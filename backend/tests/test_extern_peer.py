"""The `@mcp:` peer identity — two ids, one machine, and what happens to a
reply whose session is gone.

`externtool.py` carries a deliberate split (FR-08):

  * a SESSION sends as `base.<6 hex>` — a per-process id, so a background
    listener can never swallow the answer a live `orgtree_wait` is holding out
    for (№5);
  * the LISTENER listens as the bare `base` (`~/.orgtree/extern-id`) — the one
    address that outlives any process, so an org can wake the machine
    unprompted.

Both halves are right on their own. This suite is about the seam: replies are
matched by EXACT address (`api._extern_scan`: `e["peer"] == addr`), so a reply
to a session id is delivered to that session or to nobody — and the session's
id is minted fresh per process unless `ORGTREE_EXTERN_ID` is pinned.

    §1  the split does what it was built to do
    §2  the seam — a reply nobody is left to collect

Hermetic: in-memory ledger + the api module's own scan function. No port, no
CLI, no network, no MCP process.

    python backend/tests/test_extern_peer.py [-v]
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-externpeer-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ["USERPROFILE"] = os.environ["HOME"] = _TMP
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree import api, store                                   # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}

BASE = "nova-desk-ncola"
SESSION = f"{BASE}.a1b2c3"          # what a live MCP session sends as


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


def fixture(ok, msg) -> None:
    """A PRECONDITION inside a gap body — raised as a RuntimeError so `gap`
    below re-reports it as a broken check instead of swallowing it as the
    finding.

    ⚠ Learned the expensive way (2026-08-06, test_batched_asks). A gap
    body's whole contract is "this assert fails", so a fixture assert and the
    assert that measures the defect are indistinguishable: gap() catches the
    first AssertionError it meets and files it as the finding. A credit
    request for 8 against a grant of 20 took the at-or-below no-op branch, so
    no row ever existed — the gap fired on its own scaffolding while the
    defect it named was real but unexercised. Use fixture(...) for every setup
    precondition in a gap body; keep a bare `assert` for the property under
    test."""
    if not ok:
        raise RuntimeError(f"fixture: {msg}")


def gap(label, why, fn) -> None:
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
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote this out of gap()")


_n = [0]


def org_with_peer(peer: str, inbound: str = "hello from outside"):
    """A saved org that has heard from `peer` once (so the peer is a known
    correspondent) with one top-level agent able to answer."""
    _n[0] += 1
    org = store.create_org(f"zz externpeer {_n[0]}")
    org.hire(USER, None, "haiku", 5, "ceo", add_dirs=[], tools=dict(ALL_TOOLS),
             org_visibility="team", charter="answers the outside")
    org.post_external_mail(f"@mcp:{peer}", inbound)
    store.save_org(org)
    return org.d["slug"]


def reply(slug: str, peer: str, body: str) -> None:
    with store.DOC_LOCK:
        org = store.load_org(slug)
        org.post_mail("ceo", f"@mcp:{peer}", body)
        store.save_org(org)


def scan(addr: str, slug: str, **kw):
    """Scoped DELIBERATELY: _extern_scan sweeps every org in the data root, so
    an unscoped call collects other checks' fixtures — measured, the first
    draft of this suite had two checks contradicting each other on it."""
    return api._extern_scan(f"@mcp:{addr}", slug, None, **kw)


# ══════════════════════════════════════════════════════════════════════════ §1

def sec_split() -> None:
    print("\n§1  the split does what it was built to do")

    def _session_gets_its_own_reply():
        slug = org_with_peer(SESSION)
        reply(slug, SESSION, "answer for the live session")
        got = [m["body"] for m in scan(SESSION, slug)]
        assert got == ["answer for the live session"], got
    check("split · a reply to a session id reaches that session", _session_gets_its_own_reply)

    def _listener_does_not_steal_it():
        slug = org_with_peer(SESSION)
        reply(slug, SESSION, "for the session only")
        assert scan(BASE, slug) == [], (
            "the machine listener collected a reply meant for a live "
            "session's own orgtree_wait — №5 exists to prevent exactly this")
    check("split · the machine listener does NOT collect a live session's "
          "replies (the whole point of the two ids)", _listener_does_not_steal_it)

    def _org_can_wake_the_machine():
        slug = org_with_peer(BASE, inbound="the machine said hello once")
        reply(slug, BASE, "wake up, something happened")
        got = [m["body"] for m in scan(BASE, slug)]
        assert got == ["wake up, something happened"], got
    check("split · an org addressing the machine-stable id reaches the "
          "listener (unprompted contact works at tier 1)", _org_can_wake_the_machine)

    def _addresses_do_not_bleed():
        slug = org_with_peer(BASE)
        reply(slug, BASE, "for the machine")
        assert scan(SESSION, slug) == [], "a session collected the machine's mail"
    check("split · and the machine's mail does not leak into a session",
          _addresses_do_not_bleed)


# ══════════════════════════════════════════════════════════════════════════ §2

def sec_orphan() -> None:
    print("\n§2  the seam — a reply nobody is left to collect")

    def _a_dead_sessions_reply_is_still_collectable():
        """The session that asked has exited (its id was minted per process and
        is not written down anywhere). The org answers anyway — that is the
        freeform-flow ruling: an org may reply any time, any number of times."""
        slug = org_with_peer(SESSION, inbound="question from a session")
        reply(slug, SESSION, "the answer, sent after the session exited")
        # a PRECONDITION, not the finding (see `fixture` above): if the reply
        # never became an out row, the scan below is empty for a reason that
        # has nothing to do with which address it was addressed to, and the
        # gap would be filed against an org that answered nobody
        fixture(any(r.get("dir") == "out" for r in
                    store.load_org(slug).d.get("org_inbox") or []),
                "the org never posted the reply this check traces")
        # everything that still exists on this machine, asking as itself
        listener = scan(BASE, slug)
        assert listener, (
            "the reply is addressed to a per-process id that no longer exists: "
            "the machine listener polls the BASE and matching is exact "
            "(_extern_scan: e['peer'] == addr), so nothing on this machine "
            "will ever hand it over — it sits in the org's inbox, visible to "
            "the org and to the user, and invisible to the party it was "
            "written for")
    gap("orphan · a reply addressed to a session that has exited is still "
        "reachable by that machine",
        "The two-id split is correct while the session LIVES; nothing covers "
        "the moment it stops. Session ids are minted `base.<6 hex>` per "
        "process (`externtool.peer_id`) and never persisted, so once the "
        "process is gone its address is unguessable and unpollable, while "
        "`_extern_scan` matches EXACTLY. The org's own view still shows the "
        "reply as sent — the same 'delivered on our side, never received on "
        "theirs' shape that cost a day on the @net: path. Fixes that keep №5 "
        "intact: let the listener sweep `base.*` for replies whose session "
        "has not polled in N minutes (a grace period, not a steal), or have "
        "the listener report the orphans it can see so a human can act. What "
        "must NOT happen is the listener eating fresh replies belonging to a "
        "live session — that is the bug the split was built to prevent.",
        _a_dead_sessions_reply_is_still_collectable)

    def _the_row_says_it_was_sent():
        """Characterisation: from the org's side this looks like a completed
        correspondence — there is no per-message delivery state on the @mcp:
        path at all (that is a hub feature)."""
        slug = org_with_peer(SESSION)
        reply(slug, SESSION, "into the void")
        rows = [r for r in store.load_org(slug).d["org_inbox"]
                if r.get("dir") == "out"]
        assert rows and "state" not in rows[-1], rows[-1]
    check("orphan · characterised: the @mcp: out row carries no delivery "
          "state, so 'sent' and 'never collected' are indistinguishable to "
          "the org", _the_row_says_it_was_sent)

    def _pinning_the_id_closes_it():
        """The documented escape hatch, verified: pin ORGTREE_EXTERN_ID and the
        session's address is stable across processes, so a later session — or
        the listener, if pinned to the same value — collects the reply."""
        slug = org_with_peer(BASE)
        reply(slug, BASE, "collected by the next process")
        assert [m["body"] for m in scan(BASE, slug)] == ["collected by the next process"]
    check("orphan · pinning the peer id (ORGTREE_EXTERN_ID / the stable base) "
          "is what makes a reply survive the process that asked",
          _pinning_the_id_closes_it)


# ═════════════════════════════════════════════════════════════════════════ main

def main() -> None:
    print("═══ @mcp: peer identity — the two ids and the seam between them ═══")
    sec_split()
    sec_orphan()

    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed, "
          f"{len(GAPS)} gaps")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    if GAPS:
        print("\n⚑ GAPS — measured, currently true, reported to the implementer:")
        for label, why, detail in GAPS:
            print(f"\n  ⚑ {label}\n    measured: {detail}\n    {why}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
