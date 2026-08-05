# pyright: strict
"""@net: — the mail-hub client (F-06).

Two halves:
- IDENTITY (Phase A): the org's permanent network identity + hub config.
- TRANSPORT (Phase C): the spool sender + multiplexed long-poll daemon.

Identity model (docs/mailserver-spec.md §3, all user-ruled):
  secret      = secrets.token_hex(16)        minted BY the org at creation
  fingerprint = sha256(secret)               the hub stores only this
  slug        = f"{org}.{username}.{fingerprint[:6]}"   minted ONCE, persisted,
                never recomputed — the address survives moves and renames.

Transport model (spec §4/§10, all ruled): outbound sends return instantly by
writing a SPOOL entry into the org doc (staged inside the caller's DOC_LOCK,
riding the same save as the org-inbox row — no crash window where the ledger
says "queued" with no spool entry); the SENDER thread drains spools with
backoff and retries FOREVER ("no hub yet" is a status, not an error). The
POLLER thread holds one multiplexed long poll per hub address covering every
participating org; inbound mail is dedup'd against a persisted seen-ring,
DELIVERED FIRST (deliver_org_inbox = net_wake auto), recorded seen, then
ACKED — a crash between steps duplicates, never loses. Receipts are
best-effort and never block correspondence.

⚠ Secret hygiene: the secret lives in the org doc (`net_identity`) and is
returned by exactly one loopback-admin endpoint (`GET /api/orgs/{slug}/net`).
It must never enter a tree payload, an agent's context, a log line, or a URL —
on the wire it rides headers only. Kiosk orgs mint NO identity at all: they are
sealed from the outside world, and an identity that does not exist cannot leak
(stronger than filtering rosters).

⚠ Lock discipline: DOC_LOCK is never held across an HTTP call. The daemon
threads take it only to read/mutate docs, in the same order as everyone else.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable, cast

import re
import secrets

from .ledger import now

if TYPE_CHECKING:
    from .ledger import Org

# the local hub's default address; overridable via defaults.json
# ("net_hub_address" — translated into the "local" hub entry at org creation,
# never written raw into an org doc)
DEFAULT_HUB_ADDRESS = "http://127.0.0.1:7370"
LOCAL_HUB_ID = "local"    # the implicit same-machine hub; per-hub state keys
                          # on this id, so its ADDRESS may be edited freely


def _sanitize_user(user: str) -> str:
    """The username is human-readable decoration in the slug (uniqueness comes
    from the fingerprint suffix). Dots separate the slug's three parts, so the
    username must never contain one."""
    s = re.sub(r"[^A-Za-z0-9_-]", "-", user).strip("-").lower()
    return s or "user"


def mint_identity(org: "Org") -> dict[str, Any] | None:
    """Mint the org's permanent network identity. Idempotent — an existing
    identity is returned untouched (the slug is IMMUTABLE for the org's
    lifetime, user ruling). Returns None for kiosk orgs, which have no
    identity by design. Caller holds DOC_LOCK and saves."""
    if org.d.get("kiosk"):
        return None
    ident = org.d.get("net_identity")
    if isinstance(ident, dict) and ident.get("secret"):
        return ident
    secret = secrets.token_hex(16)               # the repo's credential pattern
    fp = hashlib.sha256(secret.encode()).hexdigest()
    ident = {
        "secret": secret,
        "fingerprint": fp,
        "slug": f"{org.d['slug']}.{_sanitize_user(getpass.getuser())}.{fp[:6]}",
        "minted_at": now(),
    }
    org.d["net_identity"] = ident
    return ident


def hub_entries(autoconnect: bool, remote_addresses: list[str],
                local_address: str = DEFAULT_HUB_ADDRESS) -> list[dict[str, Any]]:
    """Build the initial `net_hubs` list for a new org. The local hub entry
    exists only under autoconnect; remote hubs are explicit addresses, each
    with a client-minted id so per-hub state survives address edits. Hub
    NAMES are discovered on connect (user ruling), never set here."""
    hubs: list[dict[str, Any]] = []
    if autoconnect:
        hubs.append({"id": LOCAL_HUB_ID,
                     "address": local_address.strip() or DEFAULT_HUB_ADDRESS,
                     "enabled": True})
    for a in remote_addresses:
        a = a.strip()
        if a:
            hubs.append({"id": uuid.uuid4().hex[:8], "address": a,
                         "enabled": True})
    return hubs


# ═══════════════════════════════════════════════ Phase C — the transport ═══

SEEN_RING = 500          # per hub: inbound message ids kept for dedupe
STALE_AFTER_S = 3600.0   # inbound older than this gets the staleness stamp
POLL_WAIT_S = 25.0       # server clamps to its own 55 s ceiling
BACKOFF_MAX_S = 30.0

# set by api._wire_notify: broadcast an org's `changed` event (thread-safe
# there). Called on every CONNECTIVITY TRANSITION so the UI's status dots are
# realtime without polling (user amendment 2026-08-05).
notify_changed: Callable[[str], None] | None = None

_started = False
_kick = threading.Event()
# in-memory per (org_slug, hub_id): {"connected", "last_ok", "error"} — status
# for the tree payload; per hub ADDRESS: roster + discovered name
_status: dict[tuple[str, str], dict[str, Any]] = {}
_status_lock = threading.Lock()
_rosters: dict[str, list[dict[str, Any]]] = {}
_hub_names: dict[str, str] = {}
# read receipts queued by _confirm_delivered (supervisor) until the sender
# thread flushes them; restart loses at most a pending "read" — the far end
# self-heals to "delivered", which is honest
_read_q: list[tuple[str, str]] = []      # (org_slug, net_id)
_read_q_lock = threading.Lock()
# delivered receipts queued at delivery time (we know the hub they came from)
_dlv_q: list[tuple[str, str, str]] = []  # (org_slug, hub_id, net_id)
_backoff: dict[str, float] = {}          # hub address → monotonic not-before


def kick() -> None:
    """Wake the sender thread now (called after a send staged a spool entry)."""
    _kick.set()


def note_read(slug: str, net_ids: list[str]) -> None:
    """A turn provably consumed inbound net mail (the _confirm_delivered
    seam) — queue READ receipts for the next flush."""
    if not net_ids:
        return
    with _read_q_lock:
        _read_q.extend((slug, i) for i in net_ids)
    _kick.set()


def spool_append(org: "Org", peer: str, body: str, oid: str,
                 kind: str = "message",
                 attachments: list[str] | None = None) -> str:
    """Stage an outbound @net: message. PURE doc mutation on the caller's
    already-loaded org — it rides the caller's save (api.py agent dispatch),
    so the org-inbox row and the spool entry land atomically. Returns the hub
    message id (the idempotency key)."""
    hubs = [h for h in (org.d.get("net_hubs") or []) if h.get("enabled")]
    hub_id = str(hubs[0]["id"]) if hubs else LOCAL_HUB_ID
    entry: dict[str, Any] = {"id": uuid.uuid4().hex, "to": peer, "body": body,
                             "kind": kind, "at": now(), "oid": oid, "tries": 0}
    metas: list[dict[str, Any]] = []
    if attachments:
        entry["attachments"] = list(attachments)[:10]
        for p in entry["attachments"]:
            try:
                metas.append({"name": os.path.basename(p),
                              "bytes": os.path.getsize(p)})
            except OSError:
                metas.append({"name": os.path.basename(p), "bytes": 0})
    spool = org.d.setdefault("net_spool", {})
    spool.setdefault(hub_id, []).append(entry)
    # stamp the org-inbox row with the delivery state machine's first state
    for row in reversed(org.d.get("org_inbox") or []):
        if row.get("id") == oid:
            row["state"] = "queued"
            row["state_at"] = now()
            row["net_id"] = entry["id"]
            if metas:
                row["attachments"] = metas
            break
    return str(entry["id"])


def status_block(org_d: dict[str, Any]) -> dict[str, Any] | None:
    """The tree payload's `net` block — config + live status, NEVER the
    secret (or the full fingerprint; the slug's baked-in 6-char suffix is the
    only identity material a payload carries)."""
    if org_d.get("kiosk") is not None:
        return None
    ident = cast("dict[str, Any]", org_d.get("net_identity") or {})
    hubs_out: list[dict[str, Any]] = []
    slug = str(org_d.get("slug") or "")
    spool = cast("dict[str, list[Any]]", org_d.get("net_spool") or {})
    for h in cast("list[dict[str, Any]]", org_d.get("net_hubs") or []):
        hid = str(h.get("id"))
        with _status_lock:
            st = dict(_status.get((slug, hid)) or {})
            roster = list(_rosters.get(str(h.get("address") or "")) or [])
            name = h.get("name") or _hub_names.get(str(h.get("address") or ""))
        hubs_out.append({
            "id": hid, "address": h.get("address"),
            "enabled": bool(h.get("enabled")), "name": name,
            "connected": bool(st.get("connected")),
            "last_ok": st.get("last_ok"), "error": st.get("error"),
            "queued": len(spool.get(hid) or []),
            "roster": [r for r in roster
                       if r.get("slug") != ident.get("slug")],
        })
    return {"slug": ident.get("slug"), "hubs": hubs_out}


def remote_peers() -> list[dict[str, Any]]:
    """Roster rows for orgtree_list_orgs — every known remote peer across all
    connected hubs, deduped by network slug."""
    with _status_lock:
        rows = [dict(r) for roster in _rosters.values() for r in roster]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        s = str(r.get("slug") or "")
        if s and s not in seen:
            seen.add(s)
            out.append({"slug": f"@net:{s}", "name": r.get("org_name") or s,
                        "online": bool(r.get("online")),
                        "last_seen": r.get("last_seen"),
                        "blurb": r.get("blurb") or ""})
    return out


def _set_status(slug: str, hub_id: str, connected: bool,
                error: str | None = None) -> None:
    """Record status; broadcast ONLY on a transition (realtime dots without
    broadcast spam)."""
    key = (slug, hub_id)
    with _status_lock:
        prev = _status.get(key)
        changed = prev is None or bool(prev.get("connected")) != connected \
            or (prev.get("error") or None) != (error or None)
        _status[key] = {"connected": connected,
                        "last_ok": now() if connected
                        else (prev or {}).get("last_ok"),
                        "error": error}
    if changed and notify_changed:
        try:
            notify_changed(slug)
        except Exception:                                        # noqa: BLE001
            pass


def _participants() -> dict[str, dict[str, Any]]:
    """Snapshot which orgs talk to which hubs. Loads docs (cheap at this
    scale, and the storage watchdog already does the same each 20 s); mints
    missing identities/backfills hub lists for pre-F-06 orgs under DOC_LOCK
    (the chatq precedent: existing orgs join automatically)."""
    from . import store
    out: dict[str, dict[str, Any]] = {}
    for o in store.list_orgs():
        slug = str(o["slug"])
        if o.get("kiosk"):
            continue
        try:
            org = store.load_org(slug)
        except Exception:                                        # noqa: BLE001
            continue
        if org.d.get("kiosk") is not None:
            continue
        if not org.d.get("net_identity") or "net_hubs" not in org.d:
            try:
                with store.DOC_LOCK:
                    org = store.load_org(slug)
                    mint_identity(org)
                    if "net_hubs" not in org.d:
                        org.d.setdefault("net_autoconnect", True)
                        org.d["net_hubs"] = hub_entries(
                            bool(org.d.get("net_autoconnect", True)), [],
                            _default_address())
                    store.save_org(org)
            except Exception:                                    # noqa: BLE001
                continue
        ident = org.d.get("net_identity") or {}
        hubs = [dict(h) for h in (org.d.get("net_hubs") or [])
                if h.get("enabled") and h.get("address")]
        if not ident.get("secret"):
            continue
        net_state = cast("dict[str, dict[str, Any]]",
                         org.d.get("net_state") or {})
        spool = cast("dict[str, list[Any]]", org.d.get("net_spool") or {})
        out[slug] = {"net_slug": str(ident.get("slug")),
                     "secret": str(ident.get("secret")),
                     "name": str(org.d.get("name") or slug),
                     "hubs": hubs,
                     "registered": {
                         str(h["id"]):
                         bool((net_state.get(str(h["id"])) or {})
                              .get("registered_at"))
                         for h in hubs},
                     "spool": {k: len(v) for k, v in spool.items()}}
    return out


def _default_address() -> str:
    # defaults.json is api-owned; read it directly to avoid an import cycle
    import json
    from . import store
    try:
        d = json.load(open(os.path.join(store.DATA_ROOT, "defaults.json"),
                           encoding="utf-8"))
        if isinstance(d, dict):
            return str(cast("dict[str, Any]", d)
                       .get("net_hub_address") or "") or DEFAULT_HUB_ADDRESS
    except (OSError, ValueError):
        pass
    return DEFAULT_HUB_ADDRESS


def _client() -> Any:
    import httpx
    return httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0))


def _poll_client() -> Any:
    import httpx
    return httpx.Client(timeout=httpx.Timeout(POLL_WAIT_S + 10.0,
                                              connect=5.0))


def _auth_header(pairs: list[tuple[str, str]]) -> dict[str, str]:
    return {"X-Org-Auth": " ".join(f"{s}:{sec}" for s, sec in pairs)}


def _record_hub_name(addr: str, name: Any, parts: dict[str, dict[str, Any]],
                     hub_ids: dict[str, str]) -> None:
    """Persist a discovered hub name onto every org's matching hub entry
    (once — names are display data, refreshed when they change)."""
    from . import store
    if not name or not isinstance(name, str):
        return
    with _status_lock:
        known = _hub_names.get(addr)
        _hub_names[addr] = name
    if known == name:
        return
    for slug in parts:
        hid = hub_ids.get(slug)
        if not hid:
            continue
        try:
            with store.DOC_LOCK:
                org = store.load_org(slug)
                for h in org.d.get("net_hubs") or []:
                    if str(h.get("id")) == hid and h.get("name") != name:
                        h["name"] = name
                        store.save_org(org)
                        break
        except Exception:                                        # noqa: BLE001
            pass


def _groups(parts: dict[str, dict[str, Any]]) \
        -> dict[str, dict[str, str]]:
    """address → {org_slug: hub_id} — one multiplexed connection per hub
    ADDRESS covering every participating org."""
    g: dict[str, dict[str, str]] = {}
    for slug, p in parts.items():
        for h in p["hubs"]:
            g.setdefault(str(h["address"]), {})[slug] = str(h["id"])
    return g


def _register_pending(parts: dict[str, dict[str, Any]]) -> None:
    from . import store
    for slug, p in parts.items():
        for h in p["hubs"]:
            hid = str(h["id"])
            if p["registered"].get(hid):
                continue
            addr = str(h["address"])
            if time.monotonic() < _backoff.get(addr, 0.0):
                continue
            try:
                with _client() as c:
                    r = c.post(f"{addr}/api/register",
                               json={"slug": p["net_slug"],
                                     "org_name": p["name"],
                                     "username": _sanitize_user(
                                         getpass.getuser())},
                               headers=_auth_header(
                                   [(p["net_slug"], p["secret"])]))
                if r.status_code != 200:
                    _set_status(slug, hid, False,
                                f"register: HTTP {r.status_code}")
                    continue
                data = r.json()
                _record_hub_name(addr, data.get("name"), {slug: p},
                                 {slug: hid})
                with store.DOC_LOCK:
                    org = store.load_org(slug)
                    st = org.d.setdefault("net_state", {})
                    st.setdefault(hid, {})["registered_at"] = now()
                    store.save_org(org)
                _set_status(slug, hid, True)
            except Exception as e:                               # noqa: BLE001
                _backoff[addr] = time.monotonic() + min(
                    BACKOFF_MAX_S, 2.0 * (1 + len(_backoff)))
                _set_status(slug, hid, False, type(e).__name__)


def _drain_spools(parts: dict[str, dict[str, Any]]) -> None:
    """Ship queued outbound. At-least-once: the hub send is idempotent on the
    entry id, so a crash after send / before the doc update just re-sends."""
    from . import store
    for slug, p in parts.items():
        for h in p["hubs"]:
            hid = str(h["id"])
            if not p["spool"].get(hid):
                continue
            addr = str(h["address"])
            if time.monotonic() < _backoff.get(addr, 0.0):
                continue
            # snapshot the entries OUTSIDE the lock hold during HTTP
            with store.DOC_LOCK:
                org = store.load_org(slug)
                entries = [dict(e) for e in
                           (org.d.get("net_spool") or {}).get(hid, [])]
            for e in entries:
                # F-06 D: upload attachments first (resumable — successful
                # ids persist on the entry so a retry never re-uploads).
                # A file DELETED since the send was staged is dropped with a
                # note rather than stalling the entry forever.
                try:
                    att_ids = _ship_attachments(slug, hid, addr, p, e)
                except Exception as ex:                          # noqa: BLE001
                    _backoff[addr] = time.monotonic() + BACKOFF_MAX_S / 2
                    _set_status(slug, hid, False, type(ex).__name__)
                    _bump_try(slug, hid, str(e["id"]), type(ex).__name__)
                    break
                try:
                    with _client() as c:
                        r = c.post(f"{addr}/api/send",
                                   json={"id": e["id"], "to": e["to"],
                                         "body": e["body"],
                                         "kind": e.get("kind"),
                                         "sent_at": e.get("at"),
                                         "from": p["net_slug"],
                                         "attachments": att_ids},
                                   headers=_auth_header(
                                       [(p["net_slug"], p["secret"])]))
                except Exception as ex:                          # noqa: BLE001
                    _backoff[addr] = time.monotonic() + BACKOFF_MAX_S / 2
                    _set_status(slug, hid, False, type(ex).__name__)
                    _bump_try(slug, hid, str(e["id"]), type(ex).__name__)
                    break
                if r.status_code == 200:
                    _spool_done(slug, hid, str(e["id"]))
                    _set_status(slug, hid, True)
                elif r.status_code == 422:
                    # unknown recipient: keep retrying forever (the peer may
                    # register later — hub-agnostic addresses, ruled), but
                    # surface the reason
                    _bump_try(slug, hid, str(e["id"]),
                              str(r.json().get("detail", "unknown recipient"))
                              if r.headers.get("content-type", "")
                              .startswith("application/json")
                              else f"HTTP {r.status_code}")
                else:
                    _bump_try(slug, hid, str(e["id"]),
                              f"HTTP {r.status_code}")


def _ship_attachments(slug: str, hub_id: str, addr: str,
                      p: dict[str, Any], e: dict[str, Any]) -> list[str]:
    """Upload a spool entry's attachment files to the hub, resumably: each
    uploaded id is persisted onto the entry (`att_ids`) so a crash or later
    failure never re-uploads. Unreadable files are dropped with a note."""
    from . import store
    paths = [str(x) for x in
             cast("list[Any]", e.get("attachments") or [])]
    att_ids = [str(x) for x in cast("list[Any]", e.get("att_ids") or [])]
    while len(att_ids) < len(paths):
        path = paths[len(att_ids)]
        try:
            data = open(path, "rb").read()
        except OSError:
            # deleted since staging — drop it rather than stall forever
            with store.DOC_LOCK:
                org = store.load_org(slug)
                for se in (org.d.get("net_spool") or {}).get(hub_id, []):
                    if se.get("id") == e["id"]:
                        se["attachments"] = [x for x in se["attachments"]
                                             if x != path]
                        se["last_err"] = (f"attachment vanished before "
                                          f"upload: {os.path.basename(path)}")
                        store.save_org(org)
                        break
            paths.remove(path)
            continue
        with _client() as c:
            r = c.post(f"{addr}/api/attachments",
                       params={"name": os.path.basename(path)},
                       content=data,
                       headers=_auth_header([(p["net_slug"], p["secret"])]))
        if r.status_code != 200:
            raise RuntimeError(f"attachment upload HTTP {r.status_code}")
        att_ids.append(str(cast("dict[str, Any]", r.json())["id"]))
        with store.DOC_LOCK:
            org = store.load_org(slug)
            for se in (org.d.get("net_spool") or {}).get(hub_id, []):
                if se.get("id") == e["id"]:
                    se["att_ids"] = list(att_ids)
                    store.save_org(org)
                    break
    return att_ids


def _spool_done(slug: str, hub_id: str, entry_id: str) -> None:
    """Hub custody confirmed — remove the spool entry, advance the org-inbox
    row to `sent` (the 200 IS the 'received' receipt)."""
    from . import store
    with store.DOC_LOCK:
        org = store.load_org(slug)
        spool = org.d.setdefault("net_spool", {})
        spool[hub_id] = [e for e in spool.get(hub_id, [])
                         if e.get("id") != entry_id]
        _stamp_row(org.d, entry_id, "sent")
        store.save_org(org)


def _bump_try(slug: str, hub_id: str, entry_id: str, err: str) -> None:
    from . import store
    with store.DOC_LOCK:
        org = store.load_org(slug)
        for e in (org.d.get("net_spool") or {}).get(hub_id, []):
            if e.get("id") == entry_id:
                e["tries"] = int(e.get("tries") or 0) + 1
                e["last_err"] = err[:200]
                store.save_org(org)
                return


def _stamp_row(org_d: Any, net_id: str, state: str) -> bool:
    """Advance an org-inbox out row's delivery state (monotonic; tolerant of
    rows trimmed by the 200-cap — a missing row is silently fine)."""
    order = ["queued", "sent", "delivered", "read"]
    rows = cast("list[dict[str, Any]]", org_d.get("org_inbox") or [])
    for row in reversed(rows):
        if row.get("net_id") == net_id:
            cur = str(row.get("state") or "queued")
            if cur in order and order.index(state) > order.index(cur):
                row["state"] = state
                row["state_at"] = now()
                return True
            return False
    return False


def _apply_receipts(slug: str, receipts: list[dict[str, Any]]) -> None:
    from . import store
    if not receipts:
        return
    with store.DOC_LOCK:
        org = store.load_org(slug)
        hit = False
        for r in receipts:
            st = str(r.get("state") or "")
            if st == "fetched":
                st = "sent"          # custody stages collapse into "sent"
            if st in ("sent", "delivered", "read"):
                hit = _stamp_row(org.d, str(r.get("id")), st) or hit
        if hit:
            store.save_org(org)


def _deliver_inbound(slug: str, hub_id: str, msgs: list[dict[str, Any]],
                     addr: str = "",
                     p: dict[str, Any] | None = None) -> list[str]:
    """Deliver polled messages: dedupe on the persisted seen-ring, deliver
    FIRST, record seen, then return the ids to ack. Duplicates never
    double-drive; a crash mid-sequence redelivers, never loses."""
    from . import store, supervisor
    ack: list[str] = []
    for m in msgs:
        mid = str(m.get("id") or "")
        if not mid:
            continue
        with store.DOC_LOCK:
            org = store.load_org(slug)
            ring = (org.d.setdefault("net_state", {})
                    .setdefault(hub_id, {}).setdefault("seen_ids", []))
            seen = mid in ring
        if not seen:
            body = str(m.get("body") or "")
            # F-06 D: fetch attachments to a temp dir; deliver_org_inbox does
            # the per-recipient uploads/ copy + sandbox chown. A fetch failure
            # is NOTED in the body, never a lost message.
            tmp_dir = ""
            att_paths: list[str] = []
            atts = cast("list[dict[str, Any]]", m.get("attachments") or [])
            if atts and addr and p:
                import tempfile
                tmp_dir = tempfile.mkdtemp(prefix=f"net-{mid[:8]}-")
                for a in atts:
                    try:
                        with _client() as c:
                            r = c.get(f"{addr}/api/attachments/{a['id']}",
                                      headers=_auth_header(
                                          [(p["net_slug"], p["secret"])]))
                        if r.status_code != 200:
                            raise RuntimeError(f"HTTP {r.status_code}")
                        name = os.path.basename(
                            str(a.get("name") or "file"))[:255] or "file"
                        dst = os.path.join(tmp_dir, name)
                        with open(dst, "wb") as f:
                            f.write(r.content)
                        att_paths.append(dst)
                    except Exception as ex:                      # noqa: BLE001
                        body += (f"\n[attachment {a.get('name')!r} could not "
                                 f"be fetched from the hub: "
                                 f"{type(ex).__name__}]")
            sent_at = str(m.get("sent_at") or "")
            try:
                import datetime
                dt = datetime.datetime.fromisoformat(
                    sent_at.replace("Z", "+00:00"))
                age = (datetime.datetime.now(datetime.timezone.utc)
                       - dt).total_seconds()
                if age > STALE_AFTER_S:
                    unit = (f"{age / 86400:.1f} days" if age >= 86400
                            else f"{age / 3600:.1f} hours")
                    body = (f"[arrived via the mail hub — sent {sent_at}, "
                            f"{unit} ago; the sender may have moved on]\n\n"
                            + body)
            except ValueError:
                pass
            supervisor.deliver_org_inbox(slug, f"@net:{m.get('from')}", body,
                                         attachments=att_paths or None,
                                         net_id=mid)
            if tmp_dir:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            with store.DOC_LOCK:
                org = store.load_org(slug)
                ring = (org.d.setdefault("net_state", {})
                        .setdefault(hub_id, {}).setdefault("seen_ids", []))
                if mid not in ring:
                    ring.append(mid)
                    del ring[:-SEEN_RING]
                store.save_org(org)
            _dlv_q.append((slug, hub_id, mid))
        ack.append(mid)
    return ack


def _flush_receipts(parts: dict[str, dict[str, Any]]) -> None:
    """Best-effort delivered/read receipts — a failure re-queues; receipts
    never block correspondence."""
    with _read_q_lock:
        reads = list(_read_q)
        _read_q.clear()
    dlv = list(_dlv_q)
    _dlv_q.clear()
    by_dest: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for slug, hid, mid in dlv:
        by_dest.setdefault((slug, hid), []).append(
            {"id": mid, "state": "delivered", "at": now()})
    for slug, mid in reads:
        # read receipts route to every enabled hub of the org — the one that
        # owns the id records it (v1 has one hub; harmless otherwise)
        for h in (parts.get(slug) or {}).get("hubs", []):
            by_dest.setdefault((slug, str(h["id"])), []).append(
                {"id": mid, "state": "read", "at": now()})
    for (slug, hid), recs in by_dest.items():
        p = parts.get(slug)
        if not p:
            continue
        addr = next((str(h["address"]) for h in p["hubs"]
                     if str(h["id"]) == hid), None)
        if not addr:
            continue
        try:
            with _client() as c:
                c.post(f"{addr}/api/receipts", json={"receipts": recs},
                       headers=_auth_header([(p["net_slug"], p["secret"])]))
            # the recipient's own out-rows don't change here; sender-side
            # states arrive via the poll's receipts
        except Exception:                                        # noqa: BLE001
            with _read_q_lock:
                _read_q.extend((slug, str(r["id"])) for r in recs
                               if r["state"] == "read")
            _dlv_q.extend((slug, hid, str(r["id"])) for r in recs
                          if r["state"] == "delivered")


def _sender_loop() -> None:
    from . import store
    rev = -1
    parts: dict[str, dict[str, Any]] = {}
    while True:
        _kick.wait(2.0)
        _kick.clear()
        try:
            if store.REVISION != rev:
                rev = store.REVISION
                parts = _participants()
            _register_pending(parts)
            # spool counts move under our feet — refresh cheaply each pass
            parts = {**parts}
            _drain_spools(_participants() if _kick.is_set() else parts)
            _flush_receipts(parts)
        except Exception:                                        # noqa: BLE001
            pass


def _poller_loop() -> None:
    from . import store
    rev = -1
    parts: dict[str, dict[str, Any]] = {}
    while True:
        try:
            if store.REVISION != rev:
                rev = store.REVISION
                parts = _participants()
            if not _poll_pass(parts):
                time.sleep(2.0)
        except Exception:                                        # noqa: BLE001
            time.sleep(2.0)


def _poll_pass(parts: dict[str, dict[str, Any]]) -> bool:
    """One sweep over every hub-address group: long-poll, deliver, ack, apply
    receipts. Split out of the loop so a test (or the smoke) can drive exactly
    one pass. Returns False when there was nothing to poll."""
    groups = _groups(parts)
    if not groups:
        return False
    for addr, members in groups.items():
        # only poll registered members (auth would 401 otherwise —
        # registration is the sender loop's job)
        pairs = [(parts[s]["net_slug"], parts[s]["secret"])
                 for s in members if parts[s]["registered"]
                 .get(members[s])]
        if not pairs:
            time.sleep(1.0)
            continue
        if time.monotonic() < _backoff.get(addr, 0.0):
            time.sleep(1.0)
            continue
        by_net = {parts[s]["net_slug"]: s for s in members}
        try:
            with _poll_client() as c:
                r = c.post(f"{addr}/api/poll",
                           params={"wait": POLL_WAIT_S},
                           headers=_auth_header(pairs))
        except Exception as e:                                   # noqa: BLE001
            _backoff[addr] = time.monotonic() + 5.0
            for s, hid in members.items():
                _set_status(s, hid, False, type(e).__name__)
            continue
        if r.status_code != 200:
            _backoff[addr] = time.monotonic() + 5.0
            for s, hid in members.items():
                _set_status(s, hid, False, f"HTTP {r.status_code}")
            continue
        data = cast("dict[str, Any]", r.json())
        _record_hub_name(addr, data.get("name"), parts, dict(members))
        with _status_lock:
            _rosters[addr] = list(
                cast("list[dict[str, Any]]", data.get("roster") or []))
        for s, hid in members.items():
            if parts[s]["registered"].get(hid):
                _set_status(s, hid, True)
        by_org: dict[str, list[dict[str, Any]]] = {}
        for m in cast("list[dict[str, Any]]", data.get("messages") or []):
            local = by_net.get(str(m.get("to") or ""))
            if local:
                by_org.setdefault(local, []).append(m)
        for s, msgs in by_org.items():
            hid = members[s]
            ack = _deliver_inbound(s, hid, msgs, addr=addr, p=parts[s])
            if ack:
                try:
                    with _client() as c:
                        c.post(f"{addr}/api/ack", json={"ids": ack},
                               headers=_auth_header(
                                   [(parts[s]["net_slug"],
                                     parts[s]["secret"])]))
                except Exception:                                # noqa: BLE001
                    pass               # unacked → redelivered → seen-ring
        by_receipt: dict[str, list[dict[str, Any]]] = {}
        for rc in cast("list[dict[str, Any]]",
                       data.get("receipts") or []):
            # receipts belong to the SENDER side: route each to the org
            # whose outbound row carries the id — we don't know which, so
            # offer to all members (missing rows are fine)
            for s in members:
                by_receipt.setdefault(s, []).append(rc)
        for s, recs in by_receipt.items():
            _apply_receipts(s, recs)
    return True


def start_net_client() -> None:
    """Start the sender + poller daemons (api._wire_notify). Idempotent."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_sender_loop, daemon=True,
                     name="net-sender").start()
    threading.Thread(target=_poller_loop, daemon=True,
                     name="net-poller").start()
