"""FR-03 · orgtree_present (6e230c7) — a card on the user's screen, from any
agent, with no reply implied.

The verb is small, which is exactly why it is worth reading beside its
neighbours: `ask_user`, three screens up in the same file, is the other verb
that puts a card on the user's screen. The two differ BY RULING now, not by
accident (D-100, 2026-08-05): a question from an ungated agent is ROUTED to
its superior; a document from an ungated agent is REFUSED outright — direct
user audience only (top-level or granted). Both refuse in a headless org
(§9.6 ②), and evictions are logged and reported since the same wave.

    §1  the caps — title, body, and the two prunes
    §2  `replaces` — in place, scoped to the node, dangling tolerated
    §3  the neighbours — what ask_user checks and present_document does not

Hermetic: in-memory orgs, no data root, no port, no CLI, no network.

    python backend/tests/test_present.py [-v]
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-present-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ["USERPROFILE"] = os.environ["HOME"] = _TMP

from orgtree.ledger import LedgerError, Org, USER                # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
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
    """SHOULD hold, currently does not — inverted so the suite stays green and
    turns RED the day it is fixed."""
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


def org3() -> Org:
    """boss → kid → grandkid, none of them holding a user audience except the
    top level (which holds it by being top level)."""
    _n[0] += 1
    o = Org.create(f"zz present {_n[0]}", dirs=["E:/work"])
    o.hire(USER, None, "opus", 20, "boss")
    o.hire("boss", "boss", "haiku", 5, "kid", add_dirs=[], tools=dict(ALL_TOOLS),
           org_visibility="team", charter="test hire")
    o.hire("kid", "kid", "haiku", 2, "grandkid", add_dirs=[],
           tools=dict(ALL_TOOLS), org_visibility="team", charter="test hire")
    return o


def docs(o: Org, nid: str | None = None) -> list[dict]:
    return [d for d in o.d.get("documents", [])
            if nid is None or d["node"] == nid]


# ══════════════════════════════════════════════════════════════════════════ §1

def sec_caps() -> None:
    print("\n§1  the caps — title, body, and the two prunes")

    def _happy():
        o = org3()
        r = o.present_document("boss", "The Plan", "# Plan\n\nstep one")
        assert r.get("presented"), r
        d = docs(o, "boss")[0]
        assert d["title"] == "The Plan" and d["body"].startswith("# Plan")
    check("present · a document lands with its title and body", _happy)

    def _empties_refused():
        o = org3()
        for title, body, why in (("", "x", "no title"),
                                 ("t", "", "no body"),
                                 ("t", "   \n ", "whitespace body")):
            try:
                o.present_document("boss", title, body)
                raise AssertionError(f"accepted a document with {why}")
            except LedgerError:
                pass
    check("present · an empty title or an empty/whitespace body is refused",
          _empties_refused)

    def _title_capped_body_bounded():
        o = org3()
        o.present_document("boss", "T" * 400, "body")
        assert len(docs(o, "boss")[0]["title"]) == 120
        try:
            o.present_document("boss", "big", "B" * (Org.DOC_BODY_MAX + 1))
            raise AssertionError("a body over the 64 KB cap was accepted")
        except LedgerError as e:
            assert "64" in str(e) or "cap" in str(e), e
        o.present_document("boss", "exact", "B" * Org.DOC_BODY_MAX)
    check("present · the title truncates at 120 and the body is REFUSED (not "
          "truncated) over 64 KB — the boundary itself is accepted",
          _title_capped_body_bounded)

    def _prunes():
        o = org3()
        for i in range(14):
            o.present_document("boss", f"doc {i}", f"body {i}")
        mine = docs(o, "boss")
        assert len(mine) == 10, len(mine)
        assert [d["title"] for d in mine] == [f"doc {i}" for i in range(4, 14)]
    check("present · the newest 10 per agent survive; older cards are dropped",
          _prunes)

    def _eviction_is_visible():
        # was a ⚑ GAP (silent eviction) — fixed 2026-08-05 with the D-100
        # wave: every pruned card is logged as `present_evicted` (naming the
        # evicting document), and the presenting agent's own result names
        # what it pushed off the screen
        o = org3()
        first = o.present_document("boss", "the one being read", "body")
        did = first["presented"]
        statuses = [o.present_document("boss", f"later {i}", "body")
                    for i in range(10)]
        assert not [d for d in docs(o, "boss") if d["id"] == did], "fixture"
        ev = [e for e in o.d["events"] if e["op"] == "present_evicted"]
        assert any(e["detail"]["id"] == did for e in ev), (
            "the evicted document never reached the org log")
        evicting = next(e for e in ev if e["detail"]["id"] == did)
        assert evicting["detail"].get("by"), "the evictor is not named"
        assert any("pushed" in s["status"] and did in s["status"]
                   for s in statuses), (
            "the presenting agent was never told its presentation evicted "
            "the card the user may have open")
    check("present · an eviction is logged and the presenting agent is told "
          "(was a gap: silent 404 for an open reader)", _eviction_is_visible)


# ══════════════════════════════════════════════════════════════════════════ §2

def sec_replaces() -> None:
    print("\n§2  `replaces` — in place, scoped, dangling-tolerant")

    def _in_place():
        o = org3()
        did = o.present_document("boss", "v1", "first")["presented"]
        r = o.present_document("boss", "v2", "second", replaces=did)
        assert r["presented"] == did, r
        assert len(docs(o, "boss")) == 1
        d = docs(o, "boss")[0]
        assert d["title"] == "v2" and d["body"] == "second"
    check("replaces · updates the same card instead of stacking a second",
          _in_place)

    def _scoped_to_the_node():
        o = org3()
        o.audience_grant(USER, "kid", USER)      # D-100: presenting needs it
        theirs = o.present_document("kid", "kid's plan", "kid body")["presented"]
        o.present_document("boss", "boss's plan", "boss body", replaces=theirs)
        kid_doc = next(d for d in docs(o, "kid") if d["id"] == theirs)
        assert kid_doc["title"] == "kid's plan", (
            "one agent overwrote another agent's card through `replaces`")
        assert len(docs(o, "boss")) == 1
    check("replaces · another agent's card cannot be overwritten (the id is "
          "scoped to the presenting node)", _scoped_to_the_node)

    def _dangling_falls_through():
        o = org3()
        r = o.present_document("boss", "fresh", "body", replaces="nope")
        assert r.get("presented") and r["presented"] != "nope"
        assert len(docs(o, "boss")) == 1
    check("replaces · a dangling id makes a fresh card rather than erroring "
          "(the user may have dismissed the original)", _dangling_falls_through)


# ══════════════════════════════════════════════════════════════════════════ §3

def sec_neighbours() -> None:
    print("\n§3  the neighbours — what ask_user checks and present does not")

    def _headless_refuses():
        # was a ⚑ GAP — fixed 2026-08-05 (D-100 wave): present now carries
        # ask_user's §9.6 ② branch, worded toward orgtree_send_file/status
        o = org3()
        o.d["headless"] = True
        try:
            o.ask_user("boss", "does the gate work?")
            raise AssertionError("fixture: a headless org must refuse a question")
        except LedgerError:
            pass
        try:
            o.present_document("boss", "nobody will read this", "body")
            raise AssertionError(
                "a headless org accepted a document card — there is no "
                "screen to put it on and the reader IS the UI")
        except LedgerError as e:
            assert "send_file" in str(e), (
                "the refusal should point the agent at orgtree_send_file")
        assert not docs(o, "boss"), "the refused document was stored anyway"
    check("present · a headless org refuses a document as it refuses a "
          "question (§9.6 ②)", _headless_refuses)

    def _chain_of_command():
        # was a ⚑ GAP (present bypassed the org chart) — USER-RULED
        # 2026-08-05 (D-100): presentation is DIRECT-audience only —
        # top-level or a held user-audience grant — and everyone else is
        # REFUSED outright, not auto-bridged like a question. The two verbs
        # now differ by decision, in the opposite direction the routing
        # bridge would have taken.
        o = org3()
        # a question from a deep node still ROUTES to the superior…
        r = o.ask_user("grandkid", "may I?")
        assert r.get("routed") == "kid", r
        # …but a document from the same node is refused, and stores nothing
        try:
            o.present_document("grandkid", "straight to the user", "body")
            raise AssertionError(
                "a grandchild with no user audience put a card on the "
                "user's screen — D-100 refuses this outright")
        except LedgerError as e:
            assert "audience" in str(e), e
        assert not docs(o, "grandkid")
        # a granted audience opens the gate at any depth…
        o.audience_grant(USER, "grandkid", USER)
        assert o.present_document("grandkid", "granted now", "body")["presented"]
        # …and the top level presents by rank alone
        assert o.present_document("boss", "top level", "body")["presented"]
    check("present · direct user audience only (granted or top-level); all "
          "others refused, never routed (user ruling D-100)",
          _chain_of_command)

    def _archived_refused():
        o = org3()
        o.d["nodes"]["kid"]["state"] = "archived"
        try:
            o.present_document("kid", "from beyond", "body")
            raise AssertionError("an archived agent presented a document")
        except LedgerError:
            pass
    check("present · an archived agent cannot present (the _require_live "
          "guard that IS there)", _archived_refused)

    def _dismiss_round_trip():
        o = org3()
        did = o.present_document("boss", "read me", "body")["presented"]
        r = o.dismiss_document(did)
        assert r["node"] == "boss" and r["title"] == "read me", r
        assert not docs(o, "boss")
        try:
            o.dismiss_document(did)
            raise AssertionError("dismissing twice silently succeeded")
        except LedgerError:
            pass
    check("dismiss · the ✕ removes the card, names the agent it belonged to, "
          "and a second dismiss is an error rather than a no-op",
          _dismiss_round_trip)


# ═════════════════════════════════════════════════════════════════════════ main

def main() -> None:
    print("═══ FR-03 present — the card, the caps, and the missing guards ═══")
    sec_caps()
    sec_replaces()
    sec_neighbours()

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
