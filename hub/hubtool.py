# pyright: strict
"""hubtool — an independent Claude Code chat as a first-class MAIL HUB client
(FR-06, user-ruled 2026-08-05: the strictly-org-to-org scope is reversed).

Two halves, one file, stdlib only (the externtool.py pattern):

MCP server (what a session adds):
    claude mcp add mailhub -- python <repo>\\hub\\hubtool.py
  Tools: hub_register (first run chooses the NAME — persisted; later calls
  are idempotent), hub_list (roster with kinds + presence), hub_send,
  hub_read, hub_wait (bounded long-poll).

Listener (the chatq-shape delivery half — arm it once with the Monitor tool):
    python hub/hubtool.py listen <name>       (or MAILHUB_NAME=<name>)
  Emits one line per inbound mail (long-polling the hub, acking after print),
  which is exactly how chatq delivers today — making the hub a candidate
  end-to-end chatq replacement. The name selects WHICH session identity
  listens; use the name this session registered with.

Identity (user-ruled 2026-08-05, superseding the per-profile single
identity): EACH SESSION mints its own unique identity, keyed by a NAME the
session chooses itself — semantically appropriate to its own context /
directive / purpose (e.g. "orgtree-redteam", "nebula-builder"). Identities
live one-per-file at ~/.orgtree/hub-clients/<name>.json: the 256-bit uid in
the file is the secret (the hub stores sha256(uid)), and the address is
    <name>.<username>.<sha256(uid)[:6]>        (kind: chat)
The session REMEMBERS its chosen name and registers with it again to resume
the same address — the name is the key, so a different name is a different
identity (which is exactly what makes N concurrent sessions safe: each has
its own address and its own deliver-once mailbox, no racing). Orgs address a
chat as @net:<slug>; the dial-out direction is preserved (the chat polls the
hub; nothing ever reaches in).

Multi-hub (user spec 2026-08-05): one identity may live on SEVERAL hubs at
once. The identity file carries the list (`hubs: [address, …]` — never an
env var: the env is per-process, the point is one session on many hubs), and
the address is the SAME everywhere because the fingerprint derives from the
uid, not from any hub. register joins every listed hub; listen polls them
all concurrently (one merged stream, each line tagged `via <hub>` when more
than one is listed, seen-ring PER HUB — ids are only unique within a hub);
send resolves the hub by roster (several hold the target → the local one
wins, fewest hops; none → refuse naming the hubs searched). Manage the list:
    python hub/hubtool.py addhub <name> <address>
    python hub/hubtool.py drophub <name> <address>

Env: MAILHUB_URL (the BOOTSTRAP hub, default http://127.0.0.1:7370 — used
until the identity carries its own list) · MAILHUB_NAME (pre-seeds /
selects the name; the listener requires one).
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, cast

# ☞ THE MISSED-MAIL BUG (diagnosed live 2026-08-05, user: "pervasive and
# resilient to repair"): on Windows a PIPED stdout defaults to cp1252 with
# STRICT errors, so a mail body carrying any non-cp1252 char (⚠ ✓ → ⚑ …)
# made the listener's print() RAISE — after take_fresh had already acked
# the message and marked it seen. The catch-all swallowed the error and
# slept: the mail was consumed and never shown, unrecoverably. UTF-8 with
# errors=replace makes print infallible (and un-mangles the � mojibake the
# cp1252 bytes caused downstream).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]  # TextIO stub lacks reconfigure; runtime TextIOWrapper has it (hasattr-guarded, the externtool.py pattern)

HUB = os.environ.get("MAILHUB_URL", "http://127.0.0.1:7370").rstrip("/")
_ID_DIR = os.path.expanduser("~/.orgtree/hub-clients")
_RING_LOCK = threading.Lock()    # the listener's threads share the id file


def _norm_hub(addr: str) -> str:
    """Bare host / host:port accepted (the org client's rule, user spec
    2026-08-05): no scheme assumes http, no port assumes 7370; https left
    alone (a tunneled hub listens on 443)."""
    a = str(addr or "").strip().rstrip("/")
    if not a:
        return ""
    if "://" not in a:
        a = "http://" + a
    try:
        from urllib.parse import urlsplit
        u = urlsplit(a)
        if u.scheme == "http" and u.hostname and u.port is None:
            a = f"http://{u.netloc}:7370" + (u.path or "")
    except ValueError:
        pass
    return a


def _hubs(d: dict[str, Any]) -> list[str]:
    """The identity's hub list — the file is the source of truth (never an
    env var: the env is per-process, the point is one session on several
    hubs). An identity from before the multi-hub wave carries no list and
    resolves to the bootstrap hub. Entries are stored ALREADY normalized
    (addhub applies _norm_hub); stored values and the env bootstrap pass
    through untouched — rewriting an explicit URL would change where an
    operator pointed us."""
    hs = [str(h).rstrip("/") for h in cast("list[Any]", d.get("hubs") or [])]
    hs = [h for h in hs if h]
    return hs or [HUB]


def _local_first(hubs: list[str]) -> list[str]:
    """Fewest hops first: loopback hubs sort ahead of remote ones, list
    order preserved within each class."""
    def is_local(h: str) -> bool:
        return "127.0.0.1" in h or "localhost" in h
    return sorted(hubs, key=lambda h: (not is_local(h),))
_LEGACY_ID = os.path.expanduser("~/.orgtree/hub-client.json")
_NAME_RE = re.compile(r"[^a-z0-9-]+")
_CUR: list[str] = []            # this process's active identity NAME


def _user() -> str:
    return _NAME_RE.sub("-", getpass.getuser().lower()).strip("-") or "user"


def _norm_name(raw: str) -> str:
    """Sanitize + length-budget a chosen name (the hub's slug cap is 128;
    username + fingerprint are fixed width, so the budget is exact —
    redteam ②: an over-long IMMUTABLE name bricked the client)."""
    pick = _NAME_RE.sub("-", str(raw or "").strip().lower()).strip("-")
    budget = 128 - len(_user()) - 6 - 2
    return pick[:max(1, budget)].strip("-")


def _id_path(name: str) -> str:
    return os.path.join(_ID_DIR, f"{name}.json")


def _active_name(explicit: str | None = None) -> str:
    if explicit:
        return _norm_name(explicit)
    if _CUR:
        return _CUR[0]
    return _norm_name(os.environ.get("MAILHUB_NAME") or "")


def _load_ident(name: str) -> dict[str, Any]:
    try:
        # with-block, deliberately: on a parse error the open handle would
        # otherwise live on inside the exception's traceback, and Windows
        # then refuses the quarantine rename below (PermissionError)
        with open(_id_path(name), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and cast("dict[str, Any]", d).get("uid"):
            return cast("dict[str, Any]", d)
    except ValueError:
        # the file EXISTS but does not parse — a torn write (the 2026-08-05
        # power cut left one full of zeros). The uid is unrecoverable, so a
        # re-mint is the only way forward — but it must be LOUD, not silent:
        # quarantine the wreck and let register() report the address change
        # (silent churn strands the old address with nobody told).
        try:
            os.replace(_id_path(name),
                       _id_path(name) + f".corrupt-{int(time.time())}")
        except OSError:
            pass
        return {"_corrupt": True}
    except OSError:
        pass
    # one-time adoption of the pre-ruling single-profile identity: if the
    # legacy file carries THIS name, its uid moves here so the already-
    # registered address keeps working (the hub is first-write-wins)
    try:
        legacy = json.load(open(_LEGACY_ID, encoding="utf-8"))
        if isinstance(legacy, dict) \
                and cast("dict[str, Any]", legacy).get("uid") \
                and _norm_name(str(cast("dict[str, Any]",
                                        legacy).get("name") or "")) == name:
            d2 = cast("dict[str, Any]", legacy)
            _save_ident(name, d2)
            return d2
    except (OSError, ValueError):
        pass
    return {}


def _save_ident(name: str, d: dict[str, Any]) -> None:
    """Durable write: tmp + fsync + atomic replace. A plain open/write here
    cost a real identity on 2026-08-05 — a power cut left the file full of
    zeros (NTFS makes the rename durable before the data), and the uid IS
    the only copy of the secret: a torn identity file is a PERMANENTLY
    stranded address on the first-write-wins hub."""
    os.makedirs(_ID_DIR, exist_ok=True)
    path = _id_path(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        # the uid IS the credential (redteam ④): owner-only on POSIX
        os.chmod(path, 0o600)
    except OSError:
        pass


def _mint_uid(name: str) -> dict[str, Any]:
    """Mint this identity's uid ATOMICALLY (redteam ①): two processes
    choosing the SAME name concurrently must end up with ONE uid — the
    losing writer of an unlocked read-modify-write registered an address
    whose secret died at its restart, stranding the slug on the
    first-write-wins hub forever. O_EXCL means exactly one minter wins;
    everyone else adopts the file."""
    os.makedirs(_ID_DIR, exist_ok=True)
    fresh = {"uid": uuid.uuid4().hex + uuid.uuid4().hex}     # 256-bit uid
    try:
        fd = os.open(_id_path(name), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(fresh, f, indent=1)
            f.flush()
            os.fsync(f.fileno())      # §2d: the mint is as torn-proof as the
                                      # save — this uid is the ONLY copy
        try:
            os.chmod(_id_path(name), 0o600)
        except OSError:
            pass
        return fresh
    except FileExistsError:
        return _load_ident(name)      # the other starter won — adopt theirs


def _known_names() -> list[str]:
    try:
        return sorted(f[:-5] for f in os.listdir(_ID_DIR)
                      if f.endswith(".json"))
    except OSError:
        return []


def _ident(name: str | None = None, mint: bool = True) -> dict[str, Any]:
    """The persistent PER-SESSION identity (user ruling 2026-08-05): keyed
    by the session's self-chosen name — ~/.orgtree/hub-clients/<name>.json.
    Registering with a remembered name resumes the same uid and therefore
    the same address; the name is immutable per identity (the fingerprint
    suffix rides the address, so a rename would change it — same rule as
    orgs). No name resolved → empty dict; hub_register must supply one.

    `mint=False` resolves an EXISTING identity only (redteam finding ③:
    every verb except register used to mint on miss, so a typo'd listen
    invented a fresh address and heard nothing forever while the session's
    real mail piled up elsewhere — register is the one deliberate minter)."""
    nm = _active_name(name)
    if not nm:
        return {}
    d = _load_ident(nm)
    if not d.get("uid"):
        if not mint:
            return {}
        d = _mint_uid(nm)
        if not d.get("uid"):          # corrupt file lost the race — rare
            d = {"uid": uuid.uuid4().hex + uuid.uuid4().hex}
            _save_ident(nm, d)
    fp = hashlib.sha256(str(d["uid"]).encode()).hexdigest()
    slug = f"{nm}.{_user()}.{fp[:6]}"
    if d.get("name") != nm or d.get("slug") != slug:
        # save only when something changed: steady-state _ident() must be
        # READ-ONLY — an unconditional save here raced the listener
        # threads' per-hub ring writes (load → concurrent ring save →
        # stale overwrite)
        d["name"] = nm
        d["slug"] = slug
        with _RING_LOCK:
            _save_ident(nm, d)
    _CUR[:] = [nm]
    return d


def _call(path: str, payload: dict[str, Any] | None = None,
          method: str = "POST", timeout: float = 30.0,
          hub: str | None = None) -> dict[str, Any]:
    d = _ident()
    base = hub.rstrip("/") if hub else _hubs(d)[0]
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Content-Type": "application/json",
                 # the uid IS the secret — headers only, never a URL
                 **({"X-Org-Auth": f"{d['slug']}:{d['uid']}"}
                    if d.get("slug") else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return cast("dict[str, Any]",
                    json.loads(r.read().decode() or "{}"))


def register(name: str | None = None) -> dict[str, Any]:
    nm = _active_name(name)
    if not nm:
        return {"error": "no name chosen yet — call hub_register with a "
                         "name (it becomes part of your permanent address)"}
    # redteam ①/②: the name is self-chosen prose and adoption-by-name is by
    # design, so a COLLISION (two sessions choosing the same words) silently
    # merges two mailboxes — make the resumption VISIBLE so a session that
    # expected a fresh identity sees it and can pick another name
    pre = _load_ident(nm)
    resumed = bool(pre.get("uid"))
    corrupt = bool(pre.get("_corrupt"))
    if not resumed and not corrupt:
        # a read-only verb may have already quarantined the wreck — the
        # .corrupt-* sibling is the durable breadcrumb, so the remint is
        # still LOUD even when register isn't the first to touch the damage
        try:
            corrupt = any(f.startswith(f"{nm}.json.corrupt-")
                          for f in os.listdir(_ID_DIR))
        except OSError:
            pass
    d = _ident(nm)
    # multi-hub (user spec 2026-08-05): register on EVERY listed hub — the
    # address is identical on all of them (the fingerprint derives from the
    # uid, not the hub). Per-hub failures are reported, not fatal: one dark
    # hub must not block the others.
    payload = {"slug": d["slug"], "org_name": d["name"],
               "username": getpass.getuser(), "kind": "chat",
               "blurb": "independent Claude Code chat"}
    hubs = _hubs(d)
    per_hub: dict[str, Any] = {}
    first_out: dict[str, Any] = {}
    for h in hubs:
        try:
            out = _call("/api/register", dict(payload), hub=h)
            per_hub[h] = {"hub": out.get("name")}
            if not first_out:
                first_out = out
        except Exception as e:                                   # noqa: BLE001
            per_hub[h] = {"error": str(e)[:200]}
    res: dict[str, Any] = {"slug": d["slug"],
                           "hub": first_out.get("name"),
                           "hubs": per_hub,
                           "roster": first_out.get("roster")}
    if all("error" in v for v in per_hub.values()):
        res["error"] = "no hub reachable — identity minted locally; the " \
                       "listener will register when one comes back"
    if corrupt:
        res["reminted"] = (
            f"⚠ the identity file for {nm!r} existed but was CORRUPT (torn "
            f"write — quarantined beside it) — its secret is unrecoverable, "
            f"so this is a NEW address with a new fingerprint. Your old "
            f"address is a dead letterbox: tell your correspondents the new "
            f"one, and ask the hub operator to remove the old registration")
    if resumed:
        res["resumed"] = (
            f"the name {d['name']!r} already existed on this machine — the "
            f"SAME identity and address were resumed. If YOU registered it "
            f"in an earlier session, this is correct; if you did not, "
            f"another live session owns this mailbox and you two would "
            f"split each other's mail — choose a different name")
    return res


def poll(wait: float, hub: str | None = None) -> dict[str, Any]:
    return _call(f"/api/poll?wait={wait}", {}, timeout=wait + 10.0, hub=hub)


def ack(ids: list[str], hub: str | None = None) -> None:
    if ids:
        _call("/api/ack", {"ids": ids}, hub=hub)


def _receipts(msgs: list[dict[str, Any]], state: str,
              hub: str | None = None) -> None:
    """Chat-side receipt push (user report 2026-08-06: an org's messages
    climb to `read` on the sender's ladder while a chat's stop at `fetched`
    — hubtool spoke register/poll/ack/send and never /api/receipts, so half
    the hub's clients never completed the F-06 ladder; the hub itself needs
    no change, it accepts any recipient's receipt).

    Mapping onto what an org already means by each rung: DELIVERED = the
    listener surfaced the line (sent AFTER the print, beside mark_seen/ack
    — reporting a delivery ahead of the surface would recreate the
    missed-mail class in receipt form); READ = hub_read/hub_wait returned
    the message into the caller's context. A session that only ever LISTENS
    correctly tops out at ✓✓ delivered — that is the diagnostic, not a
    shortfall. Best-effort: a lost receipt costs display state only."""
    rc = [{"id": str(m.get("id")), "state": state}
          for m in msgs if m.get("id")]
    if not rc:
        return
    try:
        _call("/api/receipts", {"receipts": rc}, hub=hub)
    except Exception:                                            # noqa: BLE001
        pass


def _ring_key(d: dict[str, Any], hub: str | None) -> str:
    return hub.rstrip("/") if hub else _hubs(d)[0]


def _rings_of(d: dict[str, Any]) -> dict[str, Any]:
    rings = cast("dict[str, Any]", d.get("seen") or {})
    if not rings and d.get("seen_ids"):
        # pre-multi-hub identities carried ONE flat ring — it belonged
        # to the bootstrap hub, so it migrates under that key
        rings = {_hubs(d)[0]: d.get("seen_ids")}
    return rings


def peek_fresh(out: dict[str, Any],
               hub: str | None = None) -> list[dict[str, Any]]:
    """READ-ONLY dedupe: which of this batch has not been seen before.
    Nothing is marked and nothing is acked — the caller surfaces the
    messages FIRST and only then commits with mark_seen + ack (redteam
    structural note on the 2026-08-05 missed-mail bug: ring-and-ack before
    the surface means any surface failure loses mail; this split closes
    the CLASS, not just the print instance)."""
    msgs = cast("list[dict[str, Any]]", out.get("messages") or [])
    if not msgs:
        return []
    nm = _active_name()
    with _RING_LOCK:
        d = _load_ident(nm)
        ring = [str(x) for x in cast(
            "list[Any]", _rings_of(d).get(_ring_key(d, hub)) or [])]
    return [m for m in msgs if str(m.get("id")) not in ring]


def mark_seen(msgs: list[dict[str, Any]],
              hub: str | None = None) -> None:
    """Persist the ring — PER HUB (`seen` keyed by address: ids are only
    unique within one hub, and one hub's ring must never suppress
    another's mail). The identity file is shared by the listener's
    threads — _RING_LOCK serializes the read-modify-write."""
    if not msgs:
        return
    nm = _active_name()
    with _RING_LOCK:
        d = _load_ident(nm)
        key = _ring_key(d, hub)
        rings = _rings_of(d)
        ring = [str(x) for x in cast("list[Any]", rings.get(key) or [])]
        new = [str(m.get("id")) for m in msgs
               if str(m.get("id")) not in ring]
        if new:
            ring.extend(new)
            rings[key] = ring[-200:]
            d["seen"] = rings
            d.pop("seen_ids", None)
            _save_ident(nm, d)


def take_fresh(out: dict[str, Any],
               hub: str | None = None) -> list[dict[str, Any]]:
    """Surface each hub message ONCE (redteam ③): the hub is at-least-once,
    so during an ACK OUTAGE the same message redelivers on every poll — not
    a crash-window duplicate but a repeat per pass. The persisted per-hub
    ring collapses repeats; the ack is still attempted for EVERY delivered
    id, seen or not, and goes back to the hub the batch came from.

    ⚠ This composition commits BEFORE the caller surfaces — safe only
    where the return itself is the surface and nothing can fail in
    between (hub_read / hub_wait building a JSON result). The listener
    uses the split primitives instead: peek_fresh → print → mark_seen →
    ack."""
    msgs = cast("list[dict[str, Any]]", out.get("messages") or [])
    if not msgs:
        return []
    fresh = peek_fresh(out, hub=hub)
    mark_seen(fresh, hub=hub)
    key = _ring_key(_load_ident(_active_name()), hub)
    try:
        ack([str(m["id"]) for m in msgs], hub=key)
    except Exception:                                            # noqa: BLE001
        pass          # unacked → redelivered → the ring collapses the repeat
    return fresh


def fmt(m: dict[str, Any], hub: str | None = None) -> str:
    # the merged multi-hub stream tags each line's SOURCE — the reader is
    # otherwise left guessing where to reply
    via = f" via {hub}" if hub else ""
    return (f"[hub mail from {m.get('from')} at {m.get('received_at')}"
            f"{via}] "
            + str(m.get("body") or "").replace("\n", "\n  "))


# ─────────────────────────────────────────────── the Monitor-armable listener

def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            import subprocess
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True, timeout=15)
            return str(pid) in (r.stdout or "")
        except Exception:                                        # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def listen(name: str | None = None) -> None:
    """python hub/hubtool.py listen <name>   (or MAILHUB_NAME) — the name
    selects WHICH session identity listens; per-session identities are the
    whole point (user ruling 2026-08-05), so an unnamed listener would be
    ambiguous and is refused. Resolves EXISTING identities only (redteam ③:
    minting on a typo'd name produced a confident listener that hears
    nothing forever — register is where names are chosen deliberately), and
    takes a LIVE-HOLDER lock (redteam ②): a second listener on the same
    name would silently split the mailbox, so it is refused while the first
    is running."""
    d = _ident(name, mint=False)
    if not d.get("slug"):
        nm = _active_name(name)
        known = _known_names()
        print((f"no identity named {nm!r} on this machine"
               if nm else "no identity name given")
              + (f" — known names: {', '.join(known)}" if known
                 else " — none registered yet")
              + ". Register first (hub_register / hubtool.py register "
              "<name>), then listen with that exact name.", flush=True)
        return
    lock = _id_path(str(d["name"])) + ".listening"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        try:
            holder = int(open(lock, encoding="utf-8").read().strip() or 0)
        except (OSError, ValueError):
            holder = 0
        if holder and _pid_alive(holder) and holder != os.getpid():
            print(f"another live listener (pid {holder}) already listens as "
                  f"{d['slug']} — two listeners on one name split the "
                  f"mailbox at random. If that session is yours and dead, "
                  f"delete {lock}", flush=True)
            return
        try:                            # stale lock — take it over
            with open(lock, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass
    hubs0 = _hubs(d)
    print(f"listening as {d['slug']} on "
          + (", ".join(hubs0) if len(hubs0) > 1 else hubs0[0]) + " …",
          flush=True)
    try:
        register()                 # all hubs; per-hub failures are non-fatal
    except Exception:                                            # noqa: BLE001
        pass                       # hub down: the loops below keep retrying
    # multi-hub (user spec 2026-08-05): ONE process, one thread per hub,
    # merged onto stdout. The hub list is re-read every cycle, so addhub /
    # drophub take effect without re-arming: a dropped hub's thread retires
    # at its next wakeup, the others' cursors are untouched.
    import threading
    stop = threading.Event()
    threads: dict[str, threading.Thread] = {}

    def pump(h: str) -> None:
        registered = False
        while not stop.is_set() and h in _hubs(_load_ident(str(d["name"]))):
            try:
                if not registered:
                    _call("/api/register", {
                        "slug": d["slug"], "org_name": d["name"],
                        "username": getpass.getuser(), "kind": "chat",
                        "blurb": "independent Claude Code chat"}, hub=h)
                    registered = True
                many = len(_hubs(_load_ident(str(d["name"])))) > 1
                # SURFACE FIRST, COMMIT AFTER (the 2026-08-05 missed-mail
                # class fix): nothing is ring-marked or acked until the
                # line is on stdout. A surface failure now leaves the
                # message unacked → the hub redelivers → retried, instead
                # of consumed-unseen. The infallible print is still the
                # belt; this ordering is the braces.
                out = poll(25.0, hub=h)
                fresh = peek_fresh(out, hub=h)
                for m in fresh:
                    line = fmt(m, hub=h if many else None)
                    try:
                        print(line, flush=True)
                    except Exception:                            # noqa: BLE001
                        print(line.encode("ascii", "replace")
                              .decode("ascii"), flush=True)
                mark_seen(fresh, hub=h)
                all_ids = [str(m["id"]) for m in cast(
                    "list[dict[str, Any]]", out.get("messages") or [])]
                try:
                    ack(all_ids, hub=h)
                except Exception:                                # noqa: BLE001
                    pass   # unacked → redelivered → the ring collapses it
                # DELIVERED receipt for what was actually surfaced — after
                # the print, with mark_seen/ack (see _receipts)
                _receipts(fresh, "delivered", hub=h)
            except Exception:                                    # noqa: BLE001
                registered = False   # re-register on reconnect
                stop.wait(5.0)

    try:
        while True:
            live = _hubs(_load_ident(str(d["name"])))
            for h in live:
                t = threads.get(h)
                if t is None or not t.is_alive():
                    threads[h] = threading.Thread(
                        target=pump, args=(h,), daemon=True)
                    threads[h].start()
            time.sleep(5.0)
    finally:
        stop.set()
        try:
            os.remove(lock)
        except OSError:
            pass


def _resolve_send_hub(d: dict[str, Any],
                      to: str) -> tuple[str | None, list[str]]:
    """Which hub reaches `to` (user spec 2026-08-05): the same shape as the
    transport ruling — whichever hub's roster holds the target; several →
    the LOCAL one wins (fewest hops); none → refuse, and the caller names
    the hubs searched rather than failing into one of them. A single-hub
    identity skips the roster gate (the hub itself refuses an unknown
    recipient, and a transient roster failure must not block mail)."""
    hubs = _hubs(d)
    if len(hubs) == 1:
        return hubs[0], hubs
    searched: list[str] = []
    for h in _local_first(hubs):
        searched.append(h)
        try:
            out = _call("/api/roster", None, method="GET", hub=h)
            if any(str(r.get("slug")) == to for r in
                   cast("list[dict[str, Any]]", out.get("roster") or [])):
                return h, searched
        except Exception:                                        # noqa: BLE001
            continue
    return None, searched


def _send(d: dict[str, Any], to: str, body: str) -> dict[str, Any]:
    hub, searched = _resolve_send_hub(d, to)
    if hub is None:
        return {"error": f"no hub on this identity's list knows {to!r} — "
                         f"searched: {', '.join(searched)}. Check hub_list "
                         f"for the right slug, or addhub the hub it lives on"}
    out = _call("/api/send", {"id": uuid.uuid4().hex, "to": to,
                              "body": body,
                              "sent_at": time.strftime(
                                  "%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                hub=hub)
    if len(_hubs(d)) > 1:
        out["via"] = hub
    return out


def _merged_roster(d: dict[str, Any]) -> list[dict[str, Any]]:
    """All hubs' rosters, one row per slug, tagged with every hub that
    holds it (the same identity registered on N hubs is ONE recipient)."""
    rows: dict[str, dict[str, Any]] = {}
    many = len(_hubs(d)) > 1
    for h in _hubs(d):
        try:
            out = _call("/api/roster", None, method="GET", hub=h)
        except Exception:                                        # noqa: BLE001
            continue
        for r in cast("list[dict[str, Any]]", out.get("roster") or []):
            s = str(r.get("slug"))
            row = rows.setdefault(s, dict(r))
            if many:
                hs = cast("list[str]", row.setdefault("hubs", []))
                if h not in hs:
                    hs.append(h)
                if r.get("online"):
                    row["online"] = True
    return list(rows.values())


def _hubs_edit(name: str, add: str = "",
               remove: str = "") -> dict[str, Any]:
    """addhub / drophub — the identity file carries the list. Adding also
    registers there right away (per-hub failure reported, not fatal);
    dropping stops its polling at the listener's next cycle without
    touching any other hub's ring."""
    d = _ident(name, mint=False)
    if not d.get("uid"):
        return {"error": f"no identity named {name!r} — register first"}
    hubs = _hubs(d)
    res: dict[str, Any] = {}
    if add:
        a = _norm_hub(add)
        if a and a not in hubs:
            hubs.append(a)
            try:
                _call("/api/register", {
                    "slug": d["slug"], "org_name": d["name"],
                    "username": getpass.getuser(), "kind": "chat",
                    "blurb": "independent Claude Code chat"}, hub=a)
                res["registered"] = a
            except Exception as e:                               # noqa: BLE001
                res["warning"] = (f"{a} added but unreachable ({e}) — the "
                                  f"listener registers there when it is up")
    if remove:
        r = _norm_hub(remove)
        if r in hubs and len(hubs) > 1:
            hubs.remove(r)
            res["dropped"] = r
        elif r in hubs:
            return {"error": "that is the identity's only hub — add "
                             "another before dropping this one"}
        else:
            return {"error": f"{r} is not on the list"}
    d["hubs"] = hubs
    _save_ident(str(d["name"]), d)
    res["hubs"] = hubs
    return res


# ──────────────────────────────────────────────────────────── the MCP server

TOOLS: list[dict[str, Any]] = [
    {"name": "hub_register",
     "description": (
         "Join the mail hub as THIS SESSION. Choose a UNIQUE, semantically "
         "appropriate `name` that reflects this session's own context / "
         "directive / purpose (e.g. 'orgtree-redteam', 'terrain-pipeline') "
         "— every session has its own identity, and the name is the key. "
         "REMEMBER the name you chose: registering with it again later "
         "resumes the SAME address (<name>.<user>.<fingerprint>); a "
         "different name is a different identity. Immutable once minted. "
         "Returns your address and the roster — ⚠ if the result carries "
         "`resumed` and YOU did not register this name earlier, another "
         "session owns it: pick a different name."),
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string",
                  "description": "this session's self-chosen identity name "
                                 "— unique, purpose-describing, reused on "
                                 "every later register"},
     }}},
    {"name": "hub_list",
     "description": "Everyone on your hubs — orgs and chats — with kind, "
                    "presence and last_seen. On a multi-hub identity the "
                    "rosters are MERGED, one row per slug, each row's "
                    "`hubs` naming where it lives.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_send",
     "description": "Send mail to any hub client (org or chat) by its slug "
                    "from hub_list. On a multi-hub identity the hub is "
                    "resolved by roster (several hold the target → the "
                    "local one wins; none → refused naming the hubs "
                    "searched — never guessed).",
     "inputSchema": {"type": "object", "properties": {
         "to": {"type": "string"}, "body": {"type": "string"}},
         "required": ["to", "body"]}},
    {"name": "hub_unregister",
     "description": "The polite exit: remove this identity's row from every "
                    "hub on its list (queued mail for you ages out on the "
                    "hub's retention). Your local identity is KEPT — "
                    "registering again later resumes the identical address.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_read",
     "description": "Fetch (and consume) any mail waiting for you right now "
                    "— across ALL your hubs; each message carries `hub` so "
                    "you know where to reply (acks go back to the hub each "
                    "message came from automatically).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_wait",
     "description": "Wait up to `timeout` seconds (max 55) for new mail — "
                    "polls every hub on your list (the window is split "
                    "across them); each message carries `hub`; empty "
                    "result on timeout.",
     "inputSchema": {"type": "object", "properties": {
         "timeout": {"type": "number"}}}},
    {"name": "hub_hubs",
     "description": "This identity's mailserver list. No args = show it. "
                    "`add` joins another hub (registers there immediately; "
                    "same address everywhere — the fingerprint derives "
                    "from your uid, not the hub). `remove` drops one (its "
                    "polling stops; the others' cursors are untouched). "
                    "Addresses accept bare host / host:port (http + :7370 "
                    "assumed).",
     "inputSchema": {"type": "object", "properties": {
         "add": {"type": "string"}, "remove": {"type": "string"}}}},
]


def unregister_identity(name: str | None = None) -> dict[str, Any]:
    """The polite exit made reachable (redteam finding 2026-08-06: the hub's
    /api/unregister existed with NO client verb — the route built for
    clients could only be hand-POSTed). Removes this identity's roster row
    on EVERY hub on its list. Local identity state is deliberately KEPT —
    the uid is preserved, so a later register/listen brings back the
    IDENTICAL address; dropping the hub row and deleting local identity are
    different destructive acts and this verb performs only the first."""
    d = _ident(name, mint=False)
    if not d.get("uid"):
        return {"error": f"no identity named {_active_name(name)!r} — "
                         f"known: {', '.join(_known_names()) or 'none'}"}
    done: list[str] = []
    errors: dict[str, str] = {}
    for h in _hubs(d):
        try:
            _call("/api/unregister", {}, hub=h)
            done.append(h)
        except Exception as e:                                   # noqa: BLE001
            errors[h] = str(e)
    return {"unregistered_on": done,
            **({"errors": errors} if errors else {}),
            "note": "the local identity file is kept — registering again "
                    "resumes the identical address"}


def dispatch(tool: str, args: dict[str, Any]) -> str:
    if tool == "hub_register":
        return json.dumps(register(str(args.get("name") or "") or None))
    d = _ident(mint=False)          # only hub_register mints (redteam ③)
    if not d.get("slug"):
        return json.dumps({"error": "not registered — call hub_register "
                                    "with this session's self-chosen name "
                                    "first (pick one from your purpose; "
                                    "reuse it every session)"})
    if tool == "hub_list":
        return json.dumps(_merged_roster(d))
    if tool == "hub_send":
        return json.dumps(_send(d, str(args.get("to") or ""),
                                str(args.get("body") or "")))
    if tool == "hub_unregister":
        return json.dumps(unregister_identity(str(d["name"])))
    if tool == "hub_hubs":
        add, rem = str(args.get("add") or ""), str(args.get("remove") or "")
        if not add and not rem:
            return json.dumps({"hubs": _hubs(d)})
        return json.dumps(_hubs_edit(str(d["name"]), add=add, remove=rem))
    if tool in ("hub_read", "hub_wait"):
        wait = min(max(float(args.get("timeout") or 0), 0.0), 55.0) \
            if tool == "hub_wait" else 0.0
        hubs = _hubs(d)
        per = max(1.0, wait / len(hubs)) if wait else 0.0
        many = len(hubs) > 1
        rows: list[dict[str, Any]] = []
        for h in hubs:
            try:
                fresh = take_fresh(poll(per, hub=h), hub=h)
                # READ receipt: for these two verbs the return value IS the
                # message entering the caller's context. Sent from the raw
                # fresh dicts (which carry `id`) — the tool's return shape
                # deliberately stays id-free.
                _receipts(fresh, "read", hub=h)
                for m in fresh:
                    rows.append({"from": m.get("from"),
                                 "body": m.get("body"),
                                 "received_at": m.get("received_at"),
                                 **({"hub": h} if many else {})})
            except Exception:                                    # noqa: BLE001
                continue          # one dark hub must not block the others
            if wait and rows:
                break             # mail in hand — stop burning the window
        return json.dumps({"messages": rows})
    return json.dumps({"error": f"unknown tool {tool!r}"})


def serve() -> None:
    """Minimal JSON-RPC MCP over stdio (the externtool.py shape)."""
    def reply(id_: Any, result: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(
            {"jsonrpc": "2.0", "id": id_, "result": result}) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = cast("dict[str, Any]", json.loads(line))
        except ValueError:
            continue
        method, id_ = msg.get("method"), msg.get("id")
        if method == "initialize":
            reply(id_, {"protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mailhub", "version": "1.0"}})
        elif method == "tools/list":
            reply(id_, {"tools": TOOLS})
        elif method == "tools/call":
            p = cast("dict[str, Any]", msg.get("params") or {})
            try:
                text = dispatch(str(p.get("name")),
                                cast("dict[str, Any]",
                                     dict(p.get("arguments") or {})))
            except urllib.error.URLError as e:
                text = json.dumps({"error": f"hub unreachable: {e.reason}"})
            except Exception as e:                               # noqa: BLE001
                text = json.dumps({"error": str(e)})
            reply(id_, {"content": [{"type": "text", "text": text}]})
        elif id_ is not None:
            reply(id_, {})


# ─────────────────────────────── chatq-parity CLI verbs (the FR-09 cutover)
# chatq's shell surface was send.sh/list.sh/listen.sh; sessions migrating to
# the hub need the same verbs without an MCP registration step:
#     python hubtool.py register <name>
#     python hubtool.py send <name> <to-slug> <message…>
#     python hubtool.py list <name>
#     python hubtool.py listen <name>

def cli(argv: list[str]) -> int:
    verb = argv[0] if argv else ""
    if verb == "listen":
        listen(argv[1] if len(argv) > 1 else None)
        return 0
    if verb == "register":
        if len(argv) < 2:
            print("usage: hubtool.py register <name>", flush=True)
            return 2
        try:
            out = register(argv[1])
        except Exception as e:                                   # noqa: BLE001
            # hub down is NOT a failed onboarding: the identity was minted
            # locally, and the listener self-registers on every reconnect —
            # arm it anyway and the session comes online with the hub
            d = _ident(argv[1])
            out = {"slug": d.get("slug"),
                   "warning": f"hub unreachable ({e}) — identity minted "
                              f"locally; arm the listener anyway, it will "
                              f"register automatically when the hub is back"}
        print(json.dumps(out), flush=True)
        return 1 if out.get("error") else 0
    if verb == "send":
        if len(argv) < 4:
            print("usage: hubtool.py send <name> <to-slug> <message…>",
                  flush=True)
            return 2
        d = _ident(argv[1], mint=False)      # a typo must not mint (redteam ③)
        if not d.get("slug"):
            print(json.dumps({"error": f"no identity named {argv[1]!r} — "
                              f"known: {', '.join(_known_names()) or 'none'}"
                              f"; register first"}), flush=True)
            return 1
        try:
            register()               # idempotent; also refreshes presence
        except Exception:                                        # noqa: BLE001
            pass
        out = _send(d, argv[2], " ".join(argv[3:]))
        print(json.dumps(out), flush=True)
        return 1 if out.get("error") else 0
    if verb == "list":
        if len(argv) < 2:
            print("usage: hubtool.py list <name>", flush=True)
            return 2
        d = _ident(argv[1], mint=False)      # a typo must not mint (redteam ③)
        if not d.get("slug"):
            print(json.dumps({"error": f"no identity named {argv[1]!r} — "
                              f"known: {', '.join(_known_names()) or 'none'}"
                              f"; register first"}), flush=True)
            return 1
        for r in _merged_roster(d):
            print(f"{r.get('slug')}  [{r.get('kind') or 'org'}]"
                  + ("  online" if r.get("online") else
                     f"  last seen {r.get('last_seen')}")
                  + (f"  on {', '.join(cast('list[str]', r['hubs']))}"
                     if r.get("hubs") else ""), flush=True)
        return 0
    if verb == "unregister":
        if len(argv) < 2:
            print("usage: hubtool.py unregister <name>", flush=True)
            return 2
        out = unregister_identity(argv[1])
        print(json.dumps(out), flush=True)
        return 1 if out.get("error") else 0
    if verb in ("addhub", "drophub"):
        if len(argv) < 3:
            print(f"usage: hubtool.py {verb} <name> <address>", flush=True)
            return 2
        out = _hubs_edit(_norm_name(argv[1]),
                         add=argv[2] if verb == "addhub" else "",
                         remove=argv[2] if verb == "drophub" else "")
        print(json.dumps(out), flush=True)
        return 1 if out.get("error") else 0
    if verb == "hubs":
        d = _ident(argv[1] if len(argv) > 1 else None, mint=False)
        if not d.get("uid"):
            print(json.dumps({"error": "no such identity"}), flush=True)
            return 1
        print(json.dumps({"hubs": _hubs(d)}), flush=True)
        return 0
    print("usage: hubtool.py [listen|register|send|list|hubs|addhub|drophub]"
          " …  (no verb = MCP server on stdio)", flush=True)
    return 2


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    serve()
