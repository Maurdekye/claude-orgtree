"""The phantom external channel — dead sends, and the detach that ends them.

    python backend/tests/test_phantom_handle.py

WHY THIS EXISTS
---------------
An `external_handles` entry is injected into its holder's SYSTEM PROMPT every
turn, with the prompt telling the agent to send its answers there. Nothing
ever removed one. A panel that closed left a handle that was live forever:
the agent kept reporting into it, and every send returned a cheerful
`200 "delivered"` it could not act on.

Why that is worse than an ordinary stale-state bug, and why the fix is REMOVAL
rather than an announcement: the handle lives in the system prompt, not the
conversation, so a compacted agent knows the channel ONLY through that line.
It cannot discover the channel died — there is no message it failed to read.
It can miss a notice; it cannot read a line that is gone. D-166.

    §1  the signal itself — @mcp: is a pull transport, so a poll IS the
        heartbeat, and there was no record of one before this
    §2  half (b) — the detach, including the two ways it could do harm
    §3  half (a) — a send stops claiming delivery it cannot know about
    §4  D-158 — both halves, each with its real fault seeded, watching the
        checks fire

⚠ §2's most important check is not that a dead handle is dropped. It is that a
POLLING peer keeps its handle. A false detach breaks a working integration and
is diagnosed from the far side by someone who cannot see this machine; a late
detach only delays cleanup of something already dead. The suite is weighted
that way on purpose.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-phantom-test-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# the hub isolation every rig in this directory takes (test_external_mail §1)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import api, store, supervisor                       # noqa: E402
from orgtree.ledger import Org, USER                             # noqa: E402

PASS = 0
FAILED: list[str] = []

ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True,
             "mcp": []}


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="test hire — do test things")
    s.update(over)
    return s


def check(label, fn):
    global PASS
    try:
        fn()
    except AssertionError as e:
        FAILED.append(label)
        print(f"  FAIL  {label}\n          {e}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def refutes(label, fn):
    """The D-158 shape: the fault is already seeded, so `fn` — the very
    assertion that guards this behaviour — MUST now raise. A silent pass here
    means the check cannot fail and therefore protects nothing."""
    global PASS
    try:
        fn()
    except AssertionError:
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
        return
    FAILED.append(label)
    print(f"  FAIL  {label}\n          the seeded fault did NOT trip the "
          f"check — that check is decorative")


def mkorg(name, handle="@mcp:panel.one"):
    org = Org.create(name)
    org.hire(USER, None, "opus", 20, "top")
    org.hire("top", "top", "haiku", 0, "kid", **spec())
    if handle:
        org.set_scope(USER, "kid", external_handles=[handle])
    store.save_org(org)
    return org.d["slug"], org


def api_call(method, path, body=None):
    """One request against the admin app with a hand-built scope (the shape
    test_net_transport uses) — no live server, no port to collide on."""
    payload = b"" if body is None else json.dumps(body).encode()
    hdrs = [(b"host", b"127.0.0.1:7411")]
    if payload:
        hdrs += [(b"content-type", b"application/json"),
                 (b"content-length", str(len(payload)).encode())]
    st, chunks = [0], []

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            st[0] = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": hdrs,
             "client": ("127.0.0.1", 5), "server": ("127.0.0.1", 7411)}
    try:
        asyncio.run(api.app(scope, receive, send))
    except Exception as e:                                       # noqa: BLE001
        st[0] = st[0] or 500
        chunks.append(f"{type(e).__name__}: {e}".encode())
    raw = b"".join(chunks)
    try:
        return st[0], json.loads(raw)
    except Exception:                                            # noqa: BLE001
        return st[0], raw.decode("utf-8", "replace")


def _backdate(seconds):
    import datetime as _dt                                # noqa: PLC0415
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(seconds=seconds)).strftime(
                "%Y-%m-%dT%H:%M:%S.") + "000Z"


def age(addr, seconds, slug=None, nid="kid"):
    """Backdate a peer's sighting — the only way to test a 24h threshold in a
    suite that must run in under a second.

    ⚠ Pass `slug` to backdate the node's ATTACH stamp too. Silence runs from
    the LATER of the two, so ageing only the sighting leaves a handle that was
    attached moments ago and is correctly NOT detached. That is the design
    working, and this helper hid it once already."""
    from orgtree.store import _peers_read, _peers_write   # noqa: PLC0415
    stamp = _backdate(seconds)
    d = _peers_read()
    d.setdefault(addr, {})["last_seen"] = stamp
    _peers_write(d)
    if slug:
        o = store.load_org(slug)
        at = o.nodes[nid].get("external_handles_at") or {}
        if addr in at:
            at[addr] = stamp
            store.save_org(o)
    return stamp


def main():
    H = "@mcp:panel.one"

    print("§1 · the signal — a poll IS the heartbeat")
    store.extern_seen("@mcp:live.peer")

    def _records():
        seen = store.extern_last_seen("@mcp:live.peer")
        assert seen, "no sighting recorded"
        silent = time.time() - store._epoch(seen)
        assert silent < 5, f"a just-seen peer reads as {silent}s silent"
    check("a sighting is recorded and reads back as ~0s of silence", _records)

    def _no_tz_drift():
        # the bug this nearly shipped with: strptime defaults to NAIVE LOCAL,
        # so on any machine east of UTC a fresh sighting read hours stale.
        # This box is UTC+2; unpinned, that is a 2h error on a 24h threshold.
        from orgtree.ledger import now                    # noqa: PLC0415
        drift = abs(time.time() - store._epoch(now()))
        assert drift < 2, f"timestamp parsing is off by {drift:.0f}s — a " \
                          f"timezone bug, not a rounding one"
    check("timestamps parse as UTC — no timezone drift in the clock",
          _no_tz_drift)

    def _attach_is_stamped():
        # THE anti-instant-detach property. A peer never heard from has no
        # sighting at all, so without an attach stamp a handle bound one
        # second ago reads as silent since 1970 and is swept on the first
        # tick. The stamp lives on the NODE — it is a fact about this
        # binding, and the peer store cannot tell a week-old handle from a
        # just-re-attached one.
        o = Org.create("stamp check")
        o.hire(USER, None, "opus", 20, "t")
        o.set_scope(USER, "t", external_handles=["@mcp:never.polled"])
        at = (o.nodes["t"].get("external_handles_at") or {}).get("@mcp:never.polled")
        assert at, "a freshly attached handle carries no attach time"
        assert time.time() - store._epoch(at) < 5, at
    check("attaching a handle stamps WHEN, on the node that holds it",
          _attach_is_stamped)

    def _stamp_pruned_with_the_handle():
        # if a stale stamp outlived its handle, a re-attach would inherit the
        # dead clock — which is exactly the bug this suite caught in review
        o = Org.create("prune check")
        o.hire(USER, None, "opus", 20, "t")
        o.set_scope(USER, "t", external_handles=["@mcp:a"])
        o.set_scope(USER, "t", external_handles=["@mcp:b"])
        stamps = o.nodes["t"].get("external_handles_at") or {}
        assert set(stamps) == {"@mcp:b"}, stamps
        o.set_scope(USER, "t", external_handles=[])
        assert "external_handles_at" not in o.nodes["t"]
    check("…and the stamp set is pruned to exactly the handles held",
          _stamp_pruned_with_the_handle)

    def _corrupt_is_safe():
        p = store._peers_path()
        keep = open(p, encoding="utf-8").read() if os.path.exists(p) else None
        open(p, "w", encoding="utf-8").write("{ this is not json")
        try:
            assert store._peers_read() == {}, "a corrupt file did not read empty"
            assert store.extern_last_seen("@mcp:live.peer") is None
        finally:
            if keep is not None:
                open(p, "w", encoding="utf-8").write(keep)
    check("a corrupt sightings file fails SAFE (reads empty → detach delayed, "
          "never early)", _corrupt_is_safe)

    print("\n§2 · half (b) — the detach")
    slug, org = mkorg("phantom detach")

    def _fresh_survives():
        assert supervisor.sweep_extern_handles() == [], \
            "a handle attached seconds ago was detached at the real 24h TTL"
        assert store.load_org(slug).nodes["kid"]["external_handles"] == [H]
    check("a FRESH handle survives the real 24h sweep", _fresh_survives)

    def _polling_peer_survives():
        # ⚠ THE EXPENSIVE FAILURE. A live peer losing its handle is an outage
        # diagnosed from the far side. Silent 12h — half the threshold.
        age(H, 12 * 3600, slug)
        assert supervisor.sweep_extern_handles() == [], \
            "a peer seen 12h ago was detached under a 24h threshold"
        assert store.load_org(slug).nodes["kid"]["external_handles"] == [H]
    check("a peer polling INSIDE the threshold keeps its handle", _polling_peer_survives)

    seen_stamp = age(H, 40 * 3600, slug)

    def _silent_detached():
        dropped = supervisor.sweep_extern_handles()
        assert len(dropped) == 1, f"silent 40h and not detached: {dropped}"
        assert dropped[0]["handle"] == H and dropped[0]["node"] == "kid"
        assert "external_handles" not in store.load_org(slug).nodes["kid"], \
            "the sweep reported a detach it did not perform"
    check("a peer silent PAST the threshold loses its handle", _silent_detached)

    def _operator_trace():
        ev = [e for e in store.load_org(slug).d["events"]
              if e["op"] == "extern_handle_detached"]
        assert len(ev) == 1, ev
        d = ev[0]["detail"]
        # "why did my channel drop" has to be answerable afterwards
        assert d["handle"] == H and d["node"] == "kid"
        assert d["last_seen"] == seen_stamp, d
        assert d["threshold_s"] == 24 * 3600 and d["silent_s"] > 24 * 3600, d
    check("the detach leaves an operator trace: handle, last-seen, threshold",
          _operator_trace)

    def _prompt_loses_the_line():
        # the whole point: the prompt is a pure function of the doc, so the
        # agent's NEXT prompt simply does not mention the dead channel
        p = supervisor.identity_prompt(store.load_org(slug), "kid")
        assert H not in p and "EXTERNAL RESPONSE HANDLE" not in p, \
            "the detached handle is still in the system prompt"
    check("…and the next system prompt no longer names it", _prompt_loses_the_line)

    def _reattach_starts_fresh():
        # a re-attach must not inherit the old clock and die on the next tick
        o = store.load_org(slug)
        o.set_scope(USER, "kid", external_handles=[H])
        store.save_org(o)
        assert supervisor.sweep_extern_handles() == [], \
            "a RE-attached handle was swept immediately — the detach did not " \
            "clear the old observation clock"
        assert store.load_org(slug).nodes["kid"]["external_handles"] == [H]
    check("a re-attached handle starts a fresh clock", _reattach_starts_fresh)

    print("\n§3 · half (a) — a send stops claiming delivery")
    slug2, org2 = mkorg("phantom send", handle="@mcp:quiet.panel")

    def _send(addr="@mcp:quiet.panel"):
        return api_call("POST", "/api/agent", {
            "org": slug2, "node": "kid", "tool": "orgtree_message",
            "args": {"to": addr, "body": "are you there?"}})

    st, r = _send()

    def _not_delivered():
        assert st == 200, (st, r)
        assert r.get("delivered") is False, \
            f"still claims delivery into a pull transport: {r}"
        assert r.get("filed") == "@mcp:quiet.panel", r
    check("an @mcp: send no longer reports `delivered`", _not_delivered)

    def _says_never_polled():
        assert "NEVER polled" in r.get("status", ""), r.get("status")
    check("…and says plainly that the peer has never polled", _says_never_polled)

    def _still_filed():
        # honesty must not cost the message: a late poller still collects it
        rows = [e for e in store.load_org(slug2).d["org_inbox"]
                if e["peer"] == "@mcp:quiet.panel" and e["dir"] == "out"]
        assert rows and rows[-1]["body"] == "are you there?", rows
    check("the row is still FILED — nothing is lost, only the claim changes",
          _still_filed)

    def _dispatch_not_corrupted():
        # ⚠ the trap: `delivered` False must not fall through the dispatch's
        # `elif delivered is not None` into mail_notify()/drive.append(False).
        # A 500 here, or a node named "False", is that bug.
        assert st == 200 and "error" not in r, r
        assert "False" not in str(store.load_org(slug2).d.get("mail") or {}), \
            "a False recipient reached the mail box"
    check("the False does NOT leak into the dispatch's drive path",
          _dispatch_not_corrupted)

    def _recent_peer_reads_differently():
        store.extern_seen("@mcp:quiet.panel")
        st2, r2 = _send()
        assert r2.get("delivered") is False, r2
        assert "polling recently" in r2.get("status", ""), r2.get("status")
    check("a recently-seen peer gets a different, truthful status",
          _recent_peer_reads_differently)

    print("\n§4 · D-158 — seed each real fault, watch the check fire")

    # ---- half (b): the detach itself is disabled
    slug3, _ = mkorg("phantom fault b")
    age("@mcp:panel.one", 40 * 3600, slug3)
    real_detach = Org.detach_extern_handle
    Org.detach_extern_handle = lambda *a, **k: False        # type: ignore[assignment]
    try:
        def _b_check():
            supervisor.sweep_extern_handles()
            assert "external_handles" not in store.load_org(slug3).nodes["kid"], \
                "handle survived"
        refutes("(b) with the detach disabled, the 'handle is dropped' check "
                "goes red", _b_check)
    finally:
        Org.detach_extern_handle = real_detach              # type: ignore[assignment]

    def _b_restored():
        supervisor.sweep_extern_handles()
        assert "external_handles" not in store.load_org(slug3).nodes["kid"]
    check("…and passes again once the detach is restored", _b_restored)

    # ---- half (a): the old cheerful "delivered" is put back
    slug4, _ = mkorg("phantom fault a", handle="@mcp:quiet4.panel")

    def _a_check():
        st5, r5 = api_call("POST", "/api/agent", {
            "org": slug4, "node": "kid", "tool": "orgtree_message",
            "args": {"to": "@mcp:quiet4.panel", "body": "x"}})
        assert r5.get("delivered") is False, \
            f"reported delivery into a pull transport: {r5}"

    # seed it by restoring the pre-D-166 behaviour at the one place that
    # shapes the agent's answer
    # Seeded by calling the REAL handler and undoing D-166's rewrite on the
    # way out — the pre-D-166 answer, byte for byte. The router is left alone
    # on purpose: patching `api.app.routes` in place broke the live route for
    # every later check in this file, which is a rig that damages what it is
    # measuring.
    def _old_shape():
        class _B:
            org, node, tool = slug4, "kid", "orgtree_message"
            args = {"to": "@mcp:quiet4.panel", "body": "x"}

        class _R:
            state = type("S", (), {"bridge_slug": None})()
        r = api.agent_call(_B(), _R())
        if isinstance(r, dict) and "filed" in r:            # undo D-166
            r["delivered"] = r.pop("filed")
            r.pop("status", None)
        return r

    refutes("(a) with the old 'delivered' shape restored, the honesty check "
            "goes red",
            lambda: (lambda r: (_ for _ in ()).throw(AssertionError(
                f"reported delivery into a pull transport: {r}"))
                if r.get("delivered") is not False else None)(_old_shape()))

    check("…and the real handler still answers honestly", _a_check)

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"ALL {PASS} CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
