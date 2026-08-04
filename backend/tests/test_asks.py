"""The unified ask system (F-04 questions + F-05 credit counter-offers,
user-ruled 2026-08-04) — hermetic ledger-level checks of the whole lifecycle:
park → answer-anywhere / void-on-wake → nulled-with-reason, the route-to-
superior gate, the zero-headroom outright refusal, and the counter-offer's
full legal range (partial, exceed, decline, clawback-to-committed-floor).

Plain asserts; run with:  python tests/test_asks.py
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ORGTREE_DATA", tempfile.mkdtemp(prefix="orgtree-test-"))

from orgtree.ledger import LedgerError, Org, USER   # noqa: E402

PASS = 0
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="test hire — do test things")
    s.update(over)
    return s


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def expect_error(fn, needle=""):
    try:
        fn()
    except LedgerError as e:
        assert needle.lower() in str(e).lower(), f"wrong error: {e}"
        return
    raise AssertionError("expected LedgerError, got success")


def org2():
    """top-level 'boss' (grant 20) with a report 'kid' (grant 5)."""
    org = Org.create("asks", dirs=["E:/work"])
    org.hire(USER, None, "opus", 20, "boss")
    org.hire("boss", "boss", "haiku", 5, "kid", **spec())
    return org


def open_asks(org):
    return [a for a in org.d.get("asks", []) if a["status"] == "open"]


def main():
    print("F-04 · questions to the user:")

    def _park():
        org = org2()
        r = org.ask_user("boss", "ship now or wait?",
                         options=["ship", "wait"], multi=False)
        assert r.get("asked"), r
        assert "parked" in r["status"] and "do not wait" in r["status"].lower()
        a = open_asks(org)[0]
        assert a["node"] == "boss" and a["options"] == ["ship", "wait"]
    check("a top-level ask parks as an open card and says so", _park)

    def _amend():
        org = org2()
        org.ask_user("boss", "v1?")
        r = org.ask_user("boss", "v2 — sharper question", options=["a", "b"])
        assert "amended" in r["status"]
        opens = open_asks(org)
        assert len(opens) == 1, "re-asking amends, never stacks"
        assert opens[0]["question"].startswith("v2")
    check("re-asking amends the open ask in place", _amend)

    def _route():
        org = org2()
        r = org.ask_user("kid", "may I refactor?")
        assert r.get("routed") == "boss", r
        assert not open_asks(org), "no ask card — it rode to the superior as mail"
        box = (org.d.get("mail") or {}).get("boss", [])
        assert any("may I refactor?" in m.get("body", "") for m in box), \
            "the question must be IN the superior's mailbox"
    check("no user audience → routed to the superior as mail (auto-bridge)", _route)

    def _route_audience():
        org = org2()
        org.d["audiences"].append({"grantee": "kid", "grantor": USER,
                                   "granted_at": "t", "reason": "test"})
        r = org.ask_user("kid", "direct now?")
        assert r.get("asked"), "a user-audience holder asks the user directly"
    check("a user-audience holder deep in the tree asks directly", _route_audience)

    def _answer():
        org = org2()
        aid = org.ask_user("boss", "ship now or wait?",
                           options=["ship", "wait"])["asked"]
        r = org.ask_answer(aid, selected=["wait"], text="until CI is green")
        assert r["node"] == "boss"
        assert "Q: ship now or wait?" in r["body"]
        assert "wait" in r["body"] and "until CI is green" in r["body"]
        a = org.d["asks"][0]
        assert a["status"] == "answered" and a["reason"] == "answered"
        assert a["answer"]["selected"] == ["wait"]
        expect_error(lambda: org.ask_answer(aid, text="again"), "already answered")
    check("answering marks first, composes the mail body, and is final", _answer)

    def _answer_needs_content():
        org = org2()
        aid = org.ask_user("boss", "anything?")["asked"]
        expect_error(lambda: org.ask_answer(aid), "answer needs")
    check("an empty answer is refused", _answer_needs_content)

    def _void():
        org = org2()
        org.ask_user("boss", "pending question")
        org.request_credits("boss", 30, "need more")
        notes = org.void_open_asks("boss")
        assert len(notes) == 2, notes
        assert all("VOIDED" in n for n in notes)
        assert org.d["asks"][0]["status"] == "interrupted"
        req = org.d["credit_requests"][0]
        assert req["status"] == "interrupted"
        assert "woken" in req["reason"]
    check("a wake voids the open question AND the pending credit request", _void)

    def _void_not_answered():
        org = org2()
        aid = org.ask_user("boss", "q")["asked"]
        org.ask_answer(aid, text="a")
        assert org.void_open_asks("boss") == [], \
            "an ANSWERED ask must never be voided by the turn its answer starts"
    check("the answer's own turn voids nothing", _void_not_answered)

    def _node_ask():
        org = org2()
        aid = org.ask_user("boss", "q1")["asked"]
        a = org.node_ask("boss")
        assert a and a["id"] == aid and a["status"] == "open"
        org.ask_answer(aid, text="done")
        a = org.node_ask("boss")
        assert a and a["status"] == "answered", "freshly nulled cards linger"
        # backdate past the linger window → gone from the desk
        org.d["asks"][0]["resolved_at"] = "2020-01-01T00:00:00Z"
        assert org.node_ask("boss") is None
        assert org.node_ask("kid") is None
    check("node_ask: open > nulled-lingering > nothing after the window", _node_ask)

    print("F-05 · credit counter-offers:")

    def _refuse_zero():
        org = org2()
        org.d["max_top_grant"] = 20      # boss already holds 20 → zero headroom
        r = org.request_credits("boss", 30, "more please")
        assert r.get("refused"), r
        assert "ZERO credits" in r["status"]
        assert not org.d.get("credit_requests"), "no card is made"
    check("zero headroom → refused outright, no ask (user ruling)", _refuse_zero)

    def _refuse_kiosk_pool():
        org = org2()
        org.d["kiosk"] = {"enabled": True, "token": "t",
                          "credits": org.seat_cost("boss") + 20}
        r = org.request_credits("boss", 25, "more")
        assert r.get("refused") and "kiosk credit pool" in r["status"]
    check("an exhausted kiosk pool refuses the same way", _refuse_kiosk_pool)

    def _counter_partial():
        org = org2()
        rid = "cr1"
        org.request_credits("boss", 40, "big plans")
        r = org.credit_request_action(rid, "approve", granted=25)
        assert r["status"] == "answered" and r["granted"] == 25
        assert org.node("boss")["grant"] == 25
        assert "COUNTER-OFFERED" in r["notice"] and "asked 20 → 40" in r["notice"]
        assert "as-is" in r["notice"], "ruling ③: the matter stays open"
    check("a partial grant lands with honest counter-offer wording", _counter_partial)

    def _counter_exceed():
        org = org2()
        org.request_credits("boss", 25, "a bit more")
        r = org.credit_request_action("cr1", "approve", granted=60)
        assert org.node("boss")["grant"] == 60
        assert "COUNTER-OFFERED" in r["notice"]
    check("granting MORE than the ask is legal", _counter_exceed)

    def _decline_increase():
        org = org2()
        org.request_credits("boss", 40, "why not")
        r = org.credit_request_action("cr1", "approve", granted=20)
        assert org.node("boss")["grant"] == 20
        assert "DECLINED the increase" in r["notice"]
    check("granting exactly the current grant = a polite decline", _decline_increase)

    def _clawback():
        org = org2()
        # boss: grant 20, committed = kid's seat(1)+grant(5) = 6 → floor 6
        org.request_credits("boss", 40, "more")
        r = org.credit_request_action("cr1", "approve", granted=6)
        assert org.node("boss")["grant"] == 6
        assert "REDUCED" in r["notice"] and "reclaimed" in r["notice"]
    check("clawback drags down to the committed floor and says so", _clawback)

    def _clawback_floor():
        org = org2()
        org.request_credits("boss", 40, "more")
        expect_error(
            lambda: org.credit_request_action("cr1", "approve", granted=3),
            "committed")
    check("below the committed floor the ledger refuses (its own invariant)",
          _clawback_floor)

    def _approve_asked():
        org = org2()
        org.request_credits("boss", 30, "more")
        r = org.credit_request_action("cr1", "approve")
        assert org.node("boss")["grant"] == 30
        assert "APPROVED" in r["notice"]
    check("no granted amount = the classic one-click approve", _approve_asked)

    def _deny():
        org = org2()
        org.request_credits("boss", 30, "more")
        r = org.credit_request_action("cr1", "deny")
        assert r["status"] == "denied" and org.node("boss")["grant"] == 20
        assert "DENIED" in r["notice"] and "re-ask" in r["notice"]
    check("deny keeps the grant and leaves the door open", _deny)

    def _preview():
        org = org2()
        org.request_credits("boss", 40, "more")
        ok = org.credit_preview("cr1", 25)
        assert ok["ok"] and ok["warnings"] == []
        floor = org.credit_preview("cr1", 3)
        assert not floor["ok"] and "committed" in floor["warnings"][0]
        org.d["max_top_grant"] = 30
        cap = org.credit_preview("cr1", 40)
        assert not cap["ok"] and "cap" in cap["warnings"][0]
        assert org.node("boss")["grant"] == 20, "preview mutates NOTHING"
    check("the dry run reports floor/cap/stranding without mutating", _preview)

    def _tree_payload():
        org = org2()
        org.ask_user("boss", "q?")
        org.request_credits("boss", 30, "more")
        t = org.tree()
        assert t["asks_open"] == 2
        boss = next(n for n in t["roots"] if n["id"] == "boss")
        assert boss["ask"] is not None
        kid = boss["children"][0]
        assert kid.get("ask") is None
        assert len(t["asks"]) == 2
    check("the tree payload carries per-node asks and the open count",
          _tree_payload)

    print(f"\nasks: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
