"""@net: Phase C — the transport, attacked.

Phase C is the first part of orgtree that keeps state on BOTH sides of a
network: a spool here, a queue there, and two clocks. Every interesting failure
is therefore a state that only one side believes in — a message spooled under a
hub id nobody visits, an id evicted from a dedupe ring that the far end still
holds, a receipt flushed to a hub that never saw the message. None of those
raise; they just sit there looking like "queued".

So this suite is written from the failure end. It drives the REAL client
functions (`_poll_pass`, `_register_pending`, `_drain_spools`, `_flush_receipts`)
against the REAL hub app over an in-process sync ASGI bridge, and then asks the
question the happy path never does: after this, is there anything left that
nobody will ever pick up?

    §1  the ladder — register → send → deliver → ack → receipts, in one pass
    §2  spool routing — which hub id an outbound is filed under, and who visits
    §3  the seen-ring — duplicate collapse, and what falls off the end
    §4  receipts — both directions, and what a receipt does to the WRONG hub
    §5  failure handling — backoff, retries, and what a dead hub costs
    §6  the secret — headers only, never a URL, never a payload, never a log

Hermetic: two in-process hubs on throwaway data dirs, no socket, no thread
(the daemons are never started — every pass is driven by hand).

    python backend/tests/test_net_transport.py [-v]
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "backend"))
sys.path.insert(0, os.path.join(_REPO, "hub"))

_TMP = tempfile.mkdtemp(prefix="orgtree-nettr-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["HUB_DATA"] = os.path.join(_TMP, "hubA")     # read at import
os.environ["HUB_NAME"] = "hub-a"

import httpx                                                     # noqa: E402
from mailhub import app as hubapp, db as hubdb                   # noqa: E402
from orgtree import net, store, supervisor                       # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

hubapp.print = lambda *a, **k: None            # the hub's per-request log line

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
VERBOSE = "-v" in sys.argv
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def gap(label, why, fn) -> None:
    """Inverted expectation (see test_rename.py): asserts the SAFE property,
    is expected to FAIL today, keeps the suite green, and turns RED the day it
    is fixed."""
    global PASS
    try:
        fn()
    except AssertionError as e:
        GAPS.append((label, why, str(e).split("\n")[0][:300]))
        print(f"  ⚑ GAP    {label}")
        return
    except Exception:                                            # noqa: BLE001
        FAIL.append((label + " (gap check errored)", traceback.format_exc()))
        print(f"  FAIL     {label} — the gap check itself broke")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote out of gap()")


# ─────────────────────────────────────────────────────── the in-process hubs
HUB_A, HUB_B = "http://hub-a.test", "http://hub-b.test"
_HUB_DIRS = {"hub-a.test": os.path.join(_TMP, "hubA"),
             "hub-b.test": os.path.join(_TMP, "hubB")}
_HUB_NAMES = {"hub-a.test": "hub-a", "hub-b.test": "hub-b"}
SENT_URLS: list[str] = []          # every URL the client requested
SENT_HEADERS: list[dict] = []      # …and its headers, for the §6 sweep


class _SyncASGI(httpx.BaseTransport):
    """httpx's ASGITransport is async-only, and the sync Client asserts a
    SyncByteStream — so the response is read to completion and rebuilt. Also
    the hub FLEET switch: mailhub.db resolves its paths from module globals,
    so pointing them at this request's host is what makes two hubs possible in
    one process (requests here are sequential by construction)."""

    def __init__(self) -> None:
        self.inner = httpx.ASGITransport(app=hubapp.app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        data_dir = _HUB_DIRS[host]
        hubdb.DATA_DIR = data_dir
        hubdb.DB_PATH = os.path.join(data_dir, "hub.sqlite3")
        hubdb.BLOB_DIR = os.path.join(data_dir, "blobs")
        hubapp.HUB_NAME = _HUB_NAMES[host]
        SENT_URLS.append(str(request.url))
        SENT_HEADERS.append(dict(request.headers))
        body = request.read()

        async def go() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
            req = httpx.Request(request.method, request.url,
                                headers=request.headers, content=body)
            resp = await self.inner.handle_async_request(req)
            out = b"".join([c async for c in resp.aiter_raw()])
            await resp.aclose()
            drop = (b"content-length", b"transfer-encoding", b"content-encoding")
            hdrs = [(k, v) for k, v in resp.headers.raw if k.lower() not in drop]
            return resp.status_code, hdrs, out

        code, hdrs, out = asyncio.run(go())
        return httpx.Response(code, headers=hdrs, content=out)


def _mk_client(**_kw) -> httpx.Client:
    return httpx.Client(transport=_SyncASGI(), timeout=5.0)


net._client = _mk_client                       # type: ignore[assignment]
net._poll_client = _mk_client                  # type: ignore[assignment]
net.POLL_WAIT_S = 0.0                          # no long poll in a test
supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
supervisor.storage_check = lambda slug: None
supervisor.send_message = lambda *a, **k: {"accepted": True}   # never drive a CLI

_n = [0]


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="net transport test")
    s.update(over)
    return s


def mkorg(hubs=(HUB_A,), tops=("ceo",), autoconnect=False):
    """A saved org with an identity, the given hub addresses, and holders."""
    _n[0] += 1
    org = store.create_org(f"zz net {_n[0]}")
    slug = org.d["slug"]
    net.mint_identity(org)
    org.d["net_autoconnect"] = autoconnect
    org.d["net_hubs"] = net.hub_entries(autoconnect, list(hubs))
    for t in tops:
        org.hire(USER, None, "haiku", 5, t, **spec())
        org.audience_grant(USER, t, "extern")
    store.save_org(org)
    return slug


def parts_for(*slugs):
    p = net._participants()
    return {s: p[s] for s in slugs if s in p}


def net_slug(slug):
    return str(store.load_org(slug).d["net_identity"]["slug"])


def hub_ids(slug):
    return [str(h["id"]) for h in store.load_org(slug).d["net_hubs"]]


def spool_of(slug):
    return {k: list(v) for k, v in
            (store.load_org(slug).d.get("net_spool") or {}).items() if v}


def ladder(*slugs, passes=1):
    """Register everyone, drain outbound, poll+deliver, flush receipts."""
    for _ in range(passes):
        p = parts_for(*slugs)
        net._register_pending(p)
        p = parts_for(*slugs)          # registration flags are in the doc
        net._drain_spools(p)
        net._poll_pass(p)
        net._flush_receipts(p)


def send_net(slug, sender, to_net_slug, body="hello over the wire"):
    """The real outbound path: an org-inbox row plus a spool entry, exactly as
    api.py's agent dispatch stages it."""
    org = store.load_org(slug)
    r = org.post_mail(sender, f"@net:{to_net_slug}", body)
    oid = org.d["org_inbox"][-1]["id"]
    # ⚠ the BARE network slug — api.py passes `to[5:]`, and the spool entry's
    # `to` goes straight onto the wire, where the hub matches it against
    # orgs.slug. Passing the prefixed form here spools an address the hub can
    # never resolve (measured: an eternal 422 retry loop). See the gap in §2.
    mid = net.spool_append(org, to_net_slug, body, oid)
    store.save_org(org)
    return mid, r


def inbox_rows(slug, direction="in"):
    return [r for r in (store.load_org(slug).d.get("org_inbox") or [])
            if r.get("dir") == direction]


# ══════════════════════════════════════════════════════════════════════ §1
def sec_ladder() -> None:
    print("\n§1  the ladder — one pass, end to end")

    def _round_trip():
        a, b = mkorg(), mkorg()
        mid, _ = send_net(a, "ceo", net_slug(b), "ping")
        ladder(a, b)
        got = inbox_rows(b, "in")
        assert got and got[-1]["body"] == "ping", got
        assert got[-1]["peer"] == f"@net:{net_slug(a)}", got[-1]
        assert not spool_of(a), f"the spool must be empty after custody: {spool_of(a)}"
        row = [r for r in inbox_rows(a, "out") if r.get("net_id") == mid][0]
        assert row["state"] in ("sent", "delivered", "read"), row
    check("a message crosses and the sender's row advances past 'queued'",
          _round_trip)

    def _delivered_receipt():
        a, b = mkorg(), mkorg()
        mid, _ = send_net(a, "ceo", net_slug(b), "receipt me")
        ladder(a, b, passes=3)      # deliver, flush delivered, poll it back
        row = [r for r in inbox_rows(a, "out") if r.get("net_id") == mid][0]
        assert row["state"] == "delivered", row
    check("the recipient's delivery reaches the sender as a receipt",
          _delivered_receipt)

    def _read_receipt():
        a, b = mkorg(), mkorg()
        mid, _ = send_net(a, "ceo", net_slug(b), "read me")
        ladder(a, b, passes=2)
        net.note_read(b, [mid])
        ladder(a, b, passes=3)
        row = [r for r in inbox_rows(a, "out") if r.get("net_id") == mid][0]
        assert row["state"] == "read", row
    check("a read receipt from the consuming turn reaches the sender",
          _read_receipt)

    def _registration_persists():
        a = mkorg()
        ladder(a)
        st = store.load_org(a).d.get("net_state") or {}
        hid = hub_ids(a)[0]
        assert (st.get(hid) or {}).get("registered_at"), st
        assert net._status.get((a, hid), {}).get("connected") is True
    check("registration is persisted and the status dot goes green",
          _registration_persists)

    def _hub_name_discovered():
        a = mkorg()
        ladder(a)
        h = store.load_org(a).d["net_hubs"][0]
        assert h.get("name") == "hub-a", h
    check("the hub's name is discovered on connect, not typed", _hub_name_discovered)


# ══════════════════════════════════════════════════════════════════════ §2
def sec_spool_routing() -> None:
    print("\n§2  spool routing — who files it, and who ever visits")

    def _picks_the_enabled_remote():
        # local DISABLED, one remote enabled: the entry must be filed under the
        # REMOTE's id, not under 'local'
        a = mkorg(hubs=(HUB_A,), autoconnect=True)
        org = store.load_org(a)
        for h in org.d["net_hubs"]:
            if h["id"] == net.LOCAL_HUB_ID:
                h["enabled"] = False
        store.save_org(org)
        remote = [h["id"] for h in store.load_org(a).d["net_hubs"]
                  if h["id"] != net.LOCAL_HUB_ID][0]
        b = mkorg()
        send_net(a, "ceo", net_slug(b))
        assert list(spool_of(a)) == [remote], spool_of(a)
    check("a disabled local hub is not chosen as the spool destination",
          _picks_the_enabled_remote)

    def _no_enabled_hub_orphans():
        # An org with NO enabled hub still accepts the send: spool_append falls
        # back to LOCAL_HUB_ID, and _drain_spools only ever visits hubs that
        # are enabled AND have an address — so the entry is filed where nobody
        # looks.
        a = mkorg(hubs=(), autoconnect=False)
        assert store.load_org(a).d["net_hubs"] == [], "precondition: no hubs"
        b = mkorg()
        mid, _ = send_net(a, "ceo", net_slug(b), "into the void")
        ladder(a, b, passes=2)
        left = spool_of(a)
        assert not left, (
            f"the message is spooled under {list(left)} — a hub id this org "
            f"does not have, so no drain will ever visit it. The org-inbox row "
            f"reads {[r.get('state') for r in inbox_rows(a, 'out')]} forever, "
            f"and nothing tells the user the send cannot leave")
    gap("a send with no reachable hub does not vanish into the spool",
        "net.spool_append falls back to LOCAL_HUB_ID when no hub is enabled "
        "(net.py:164), but _drain_spools iterates only enabled hubs WITH an "
        "address, so the entry is unreachable and the org-inbox row sits at "
        "'queued' with no error. Reachable from the UI: uncheck autoconnect at "
        "creation and add no remote, then let an agent address @net:. Either "
        "refuse the send at the door (post_mail already refuses unknown "
        "routes) or file it under a real hub and surface 'no hub configured' "
        "on the row.",
        _no_enabled_hub_orphans)

    def _hub_replaced_mid_life():
        # the settings edit the implementer asked about: net_hubs is REPLACED
        # (new ids), and anything already spooled is keyed under the old id
        a, b = mkorg(), mkorg()
        send_net(a, "ceo", net_slug(b), "before the edit")
        old_id = list(spool_of(a))[0]
        org = store.load_org(a)
        org.d["net_hubs"] = net.hub_entries(False, [HUB_A])   # fresh uuid id
        store.save_org(org)
        new_id = hub_ids(a)[0]
        assert new_id != old_id, "precondition: the edit re-minted the id"
        ladder(a, b, passes=2)
        assert not spool_of(a), (
            f"a queued message stayed under the RETIRED hub id {old_id!r} "
            f"after the hub list was replaced with {new_id!r}: "
            f"{spool_of(a)}. It is now unreachable — the drain only visits "
            f"ids that are in net_hubs")
    gap("replacing the hub list does not strand what is already spooled",
        "Editing net_hubs mints new ids (hub_entries), and net_spool is keyed "
        "by hub id, so every entry queued under the old id becomes "
        "undrainable. The address may be IDENTICAL — only the id changed. "
        "Either keep the id when the address is unchanged (match on address "
        "before minting), or re-key/flush the spool as part of the settings "
        "write.",
        _hub_replaced_mid_life)

    def _two_hubs_one_spool_each():
        a = mkorg(hubs=(HUB_A, HUB_B))
        b = mkorg(hubs=(HUB_A,))
        send_net(a, "ceo", net_slug(b), "which hub?")
        assert list(spool_of(a)) == [hub_ids(a)[0]], (
            "with two enabled hubs the entry goes to the FIRST — deliberate "
            "(hub-agnostic addressing), pinned so a change is a decision")
    check("with several hubs the first enabled one carries the message",
          _two_hubs_one_spool_each)


# ══════════════════════════════════════════════════════════════════════ §3
def sec_seen_ring() -> None:
    print("\n§3  the seen-ring — collapse, and the far edge")

    def _duplicate_collapses():
        a, b = mkorg(), mkorg()
        send_net(a, "ceo", net_slug(b), "exactly once")
        ladder(a, b)
        before = len(inbox_rows(b, "in"))
        # re-queue the SAME hub message: the hub redelivers an unacked message,
        # and a lost ack is the normal way this happens
        p = parts_for(b)
        hid = hub_ids(b)[0]
        con = hubdb.connect()
        try:
            con.execute("UPDATE messages SET state='queued', fetched_at=NULL")
            con.commit()
        finally:
            con.close()
        net._poll_pass(p)
        assert len(inbox_rows(b, "in")) == before, (
            "a redelivered message was delivered to the org twice")
    check("a redelivered message is collapsed by the seen-ring",
          _duplicate_collapses)

    def _ring_is_bounded():
        a = mkorg()
        org = store.load_org(a)
        hid = hub_ids(a)[0]
        ring = (org.d.setdefault("net_state", {})
                .setdefault(hid, {}).setdefault("seen_ids", []))
        ring.extend(f"m{i}" for i in range(net.SEEN_RING + 50))
        del ring[:-net.SEEN_RING]
        store.save_org(org)
        got = (store.load_org(a).d["net_state"][hid]["seen_ids"])
        assert len(got) == net.SEEN_RING and got[0] == "m50", got[:2]
    check(f"the ring keeps the newest {net.SEEN_RING} ids", _ring_is_bounded)

    def _eviction_is_a_redelivery_window():
        # OPEN QUESTION, measured: an id evicted from the ring is no longer a
        # duplicate to us. The hub keeps a message for RETENTION_DAYS (30) and
        # redelivers until acked, so the window is real but narrow: it needs
        # SEEN_RING newer messages from the SAME hub between the delivery and
        # the redelivery, i.e. a lost ack plus 500 messages.
        a, b = mkorg(), mkorg()
        send_net(a, "ceo", net_slug(b), "the ancient one")
        ladder(a, b)
        org = store.load_org(b)
        hid = hub_ids(b)[0]
        ring = org.d["net_state"][hid]["seen_ids"]
        assert len(ring) == 1
        ring[:] = [f"newer{i}" for i in range(net.SEEN_RING)]   # evicted
        store.save_org(org)
        before = len(inbox_rows(b, "in"))
        con = hubdb.connect()
        try:
            con.execute("UPDATE messages SET state='queued', fetched_at=NULL")
            con.commit()
        finally:
            con.close()
        net._poll_pass(parts_for(b))
        assert len(inbox_rows(b, "in")) == before + 1, (
            "the eviction did not produce a redelivery — re-read this check")
        note = (f"an id evicted from the {net.SEEN_RING}-entry ring is "
                f"delivered AGAIN if the hub still holds it (retention is "
                f"{hubapp.RETENTION_DAYS} days). Bounded but real: it needs a "
                f"lost ack plus {net.SEEN_RING} newer messages from that hub.")
        GAPS.append(("the seen-ring's far edge is a redelivery window",
                     "DESIGN QUESTION, not a defect: at-least-once plus a "
                     "bounded ring means duplicates are possible by "
                     "construction. The alternative is a persisted high-water "
                     "mark per hub (received_at is monotonic on the hub side) "
                     "instead of a set of ids. Raising SEEN_RING only moves "
                     "the edge.", note))
        print("  ⚑ GAP    the seen-ring's far edge is a redelivery window")
    check("(measuring the ring's far edge)", _eviction_is_a_redelivery_window)


# ══════════════════════════════════════════════════════════════════════ §4
def sec_receipts() -> None:
    print("\n§4  receipts — the right hub, and the wrong one")

    def _wrong_hub_is_a_noop():
        # v1 flushes READ receipts to every enabled hub. The implementer's
        # question: on a hub that holds a DIFFERENT message with the same id,
        # is the no-op really a no-op? Client-minted ids make this adversarial
        # only — so it is constructed by hand.
        a = mkorg(hubs=(HUB_A, HUB_B))
        b = mkorg(hubs=(HUB_A,))
        c = mkorg(hubs=(HUB_B,))
        mid, _ = send_net(a, "ceo", net_slug(b), "the real one")
        ladder(a, b, passes=2)
        # plant a COLLIDING id on hub B, addressed to a's own net slug
        p = parts_for(a, c)
        net._register_pending(p)
        con_dir = _HUB_DIRS["hub-b.test"]
        hubdb.DATA_DIR = con_dir
        hubdb.DB_PATH = os.path.join(con_dir, "hub.sqlite3")
        hubdb.BLOB_DIR = os.path.join(con_dir, "blobs")
        con = hubdb.connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO messages (id, from_slug, to_slug, "
                "body, received_at, state) VALUES (?,?,?,?,?,'queued')",
                (mid, net_slug(c), net_slug(a), "the impostor", "2026-01-01"))
            con.commit()
        finally:
            con.close()
        net.note_read(a, [mid])         # a's turn consumed ITS message
        net._flush_receipts(parts_for(a))
        hubdb.DATA_DIR = con_dir
        hubdb.DB_PATH = os.path.join(con_dir, "hub.sqlite3")
        con = hubdb.connect()
        try:
            row = con.execute("SELECT read_at, to_slug FROM messages WHERE id=?",
                              (mid,)).fetchone()
        finally:
            con.close()
        assert row is not None, "the planted row vanished — re-read the setup"
        assert row["read_at"] is None, (
            "a message on hub B was stamped READ because it happens to share "
            "an id with one the org read on hub A: the hub's guard is "
            "`to_slug IN (my slugs)` and the impostor IS addressed to me, so "
            "nothing on the hub side can tell the two apart")
    gap("a read receipt cannot stamp an unrelated message on another hub",
        "_flush_receipts sends READ receipts for an id to EVERY enabled hub of "
        "the org (net.py:671-676). The hub's guard is `to_slug IN (my slugs)`, "
        "which is satisfied on any hub where that org is the recipient — so a "
        "same-id message on a second hub is stamped read although nobody read "
        "it. Client-minted uuid4 ids mean this is adversarial-only today (a "
        "peer would have to choose the id), but the fix is cheap: queue reads "
        "with the hub id they arrived on, exactly as _dlv_q already does.",
        _wrong_hub_is_a_noop)

    def _delivered_queue_has_no_lock():
        # _read_q is guarded by _read_q_lock; _dlv_q is not, and it is written
        # by the POLLER thread (_deliver_inbound) while the SENDER thread
        # list()s and clear()s it in _flush_receipts. The window drops a
        # delivered receipt: the far end then never advances past 'sent'.
        src = io.open(os.path.join(_REPO, "backend", "orgtree", "net.py"),
                      encoding="utf-8").read()
        body = src[src.index("def _flush_receipts"):
                   src.index("def _sender_loop")]
        assert "_dlv_q_lock" in src or "_read_q_lock" in body.split("dlv = ")[0], \
            "no lock is taken around the _dlv_q snapshot"
        i = body.index("dlv = list(_dlv_q)")
        assert "lock" in body[max(0, i - 200):i], (
            "_flush_receipts snapshots and clears _dlv_q with no lock while "
            "_deliver_inbound appends to it from the poller thread — an "
            "append landing between list() and clear() is dropped, and the "
            "sender's row never advances past 'sent'. Its sibling _read_q "
            "takes _read_q_lock for exactly this")
    gap("the delivered-receipt queue is locked like its read sibling",
        "net.py:665-666 — `dlv = list(_dlv_q); _dlv_q.clear()` runs on the "
        "sender thread with no lock, while _deliver_inbound appends from the "
        "poller thread (net.py:654). A receipt appended between the two lines "
        "is lost, and a lost DELIVERED receipt is not self-healing: the "
        "sender's row stays 'sent' forever unless a read receipt follows. "
        "_read_q already has _read_q_lock; _dlv_q needs the same three lines.",
        _delivered_queue_has_no_lock)

    def _receipts_requeue_on_failure():
        a, b = mkorg(), mkorg()
        mid, _ = send_net(a, "ceo", net_slug(b), "flush fails")
        ladder(a, b)
        net.note_read(b, [mid])
        real = net._client

        def boom(**_kw):
            raise RuntimeError("hub unreachable")
        net._client = boom                       # type: ignore[assignment]
        try:
            net._flush_receipts(parts_for(b))
        finally:
            net._client = real                   # type: ignore[assignment]
        with net._read_q_lock:
            assert any(m == mid for _s, m in net._read_q), (
                "a failed flush dropped the read receipt instead of re-queuing")
        net._flush_receipts(parts_for(b))
        with net._read_q_lock:
            net._read_q.clear()
    check("a failed receipt flush re-queues instead of losing the receipt",
          _receipts_requeue_on_failure)


# ══════════════════════════════════════════════════════════════════════ §5
def sec_failure() -> None:
    print("\n§5  failure handling — what a dead hub actually costs")

    def _backoff_grows():
        net._backoff.clear()
        a = mkorg(hubs=("http://dead.test",))
        _HUB_DIRS["dead.test"] = os.path.join(_TMP, "dead")   # never registered
        real = net._client

        def boom(**_kw):
            raise RuntimeError("connection refused")
        net._client = boom                       # type: ignore[assignment]
        waits = []
        try:
            import time as _t
            for _ in range(4):
                net._backoff.clear() if False else None
                before = _t.monotonic()
                net._register_pending(parts_for(a))
                waits.append(round(net._backoff.get("http://dead.test", 0.0)
                                   - before, 2))
        finally:
            net._client = real                   # type: ignore[assignment]
        assert waits == sorted(waits) and waits[-1] > waits[0], (
            f"the backoff never grows for a hub that keeps failing: {waits}. "
            f"_backoff[addr] = min(30, 2 * (1 + len(_backoff))) is driven by "
            f"HOW MANY ADDRESSES have ever failed, not by how many times THIS "
            f"one has — so a permanently dead hub is retried at a fixed ~4 s "
            f"forever, by every org that lists it")
    gap("a repeatedly failing hub is retried less and less often",
        "net.py:409 — `_backoff[addr] = time.monotonic() + min(BACKOFF_MAX_S, "
        "2.0 * (1 + len(_backoff)))`. The term that varies is len(_backoff), "
        "the number of distinct addresses with a backoff recorded, so for a "
        "single dead hub the interval is constant (2 s, then 4 s forever) and "
        "for an instance with 15 hub addresses a brand-new failure starts at "
        "the 30 s ceiling. Consecutive failures per address is the quantity "
        "meant here: keep a count beside the deadline and use it.",
        _backoff_grows)

    def _unknown_recipient_keeps_retrying():
        a = mkorg()
        org = store.load_org(a)
        r = org.post_mail("ceo", "@net:nobody.here.abcdef", "into the dark")
        oid = org.d["org_inbox"][-1]["id"]
        net.spool_append(org, "@net:nobody.here.abcdef", "into the dark", oid)
        store.save_org(org)
        ladder(a, passes=2)
        left = spool_of(a)
        assert left, "an unknown recipient must be RETAINED, not dropped"
        e = list(left.values())[0][0]
        assert int(e.get("tries") or 0) >= 1 and e.get("last_err"), e
        assert "no org registered" in str(e["last_err"]).lower(), e["last_err"]
        _ = r
    check("mail to an unregistered peer is retained with the reason recorded",
          _unknown_recipient_keeps_retrying)

    def _hub_5xx_marks_disconnected():
        a = mkorg()
        ladder(a)
        assert net._status[(a, hub_ids(a)[0])]["connected"] is True
        real = net._poll_client

        def boom(**_kw):
            raise RuntimeError("hub fell over")
        net._poll_client = boom                  # type: ignore[assignment]
        try:
            net._poll_pass(parts_for(a))
        finally:
            net._poll_client = real              # type: ignore[assignment]
        st = net._status[(a, hub_ids(a)[0])]
        assert st["connected"] is False and st["error"], st
        assert st["last_ok"], "the last good time must survive the failure"
    check("a hub that stops answering flips the status dot and keeps last_ok",
          _hub_5xx_marks_disconnected)


# ══════════════════════════════════════════════════════════════════════ §6
def sec_secret() -> None:
    print("\n§6  the secret — one carrier, and no copies")

    def _headers_only():
        a, b = mkorg(), mkorg()
        send_net(a, "ceo", net_slug(b), "watch the wire")
        SENT_URLS.clear()
        SENT_HEADERS.clear()
        ladder(a, b, passes=2)
        secret = store.load_org(a).d["net_identity"]["secret"]
        assert SENT_URLS, "nothing was sent — the check would pass vacuously"
        for u in SENT_URLS:
            assert secret not in u, f"THE SECRET RODE IN A URL: {u}"
        carried = [h for h in SENT_HEADERS if secret in json.dumps(h)]
        assert carried, "the secret never rode at all — re-read the harness"
        for h in carried:
            assert secret in h.get("x-org-auth", ""), (
                f"the secret appeared in a header other than x-org-auth: "
                f"{[k for k, v in h.items() if secret in str(v)]}")
    check("the secret rides X-Org-Auth only — never a URL, never another header",
          _headers_only)

    def _not_in_the_tree_payload():
        a = mkorg()
        ladder(a)
        blob = json.dumps(store.load_org(a).tree())
        d = store.load_org(a).d["net_identity"]
        assert d["secret"] not in blob and d["fingerprint"] not in blob
        block = net.status_block(store.load_org(a).d)
        assert block is not None and json.dumps(block).find(d["secret"]) < 0
        assert d["fingerprint"] not in json.dumps(block), (
            "the status block carries the full fingerprint — the 6-char "
            "suffix baked into the slug is the only identity material a "
            "payload should hold")
    check("neither the tree payload nor the status block carries it",
          _not_in_the_tree_payload)

    def _not_in_an_agents_context():
        a, b = mkorg(), mkorg()
        send_net(a, "ceo", net_slug(b), "inbound with a secret nearby")
        ladder(a, b)
        secret_b = store.load_org(b).d["net_identity"]["secret"]
        rows = inbox_rows(b, "in")
        mail = (store.load_org(b).d.get("mail") or {}).get("ceo") or []
        blob = json.dumps(rows) + json.dumps(mail)
        assert secret_b not in blob, "the org's own secret reached an agent's mail"
        assert "@net:" in json.dumps(rows), "…and the peer address did arrive"
    check("an agent's mail never carries its org's network secret",
          _not_in_an_agents_context)


def main() -> int:
    print("orgtree · @net Phase C — the transport, attacked")
    sec_ladder()
    sec_spool_routing()
    sec_seen_ring()
    sec_receipts()
    sec_failure()
    sec_secret()

    print()
    if GAPS:
        print("findings (asserted inverted — they turn RED when fixed):")
        for label, why, saw in GAPS:
            print(f"  ⚑ {label}\n      why: {why}\n      saw: {saw}")
        print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"net-transport: {PASS} passed · {len(FAIL)} FAILED · {len(GAPS)} findings")
        return 1
    print(f"net-transport: all {PASS} checks passed"
          + (f" · {len(GAPS)} findings" if GAPS else ""))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
