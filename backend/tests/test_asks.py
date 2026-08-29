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
                         options=["ship", {"label": "wait",
                                           "description": "until CI is green"}],
                         multi=False, header="Ship gate")
        assert r.get("asked"), r
        assert "parked" in r["status"] and "do not wait" in r["status"].lower()
        a = open_asks(org)[0]
        assert a["node"] == "boss" and a["header"] == "Ship gate"
        # options normalize to AskUserQuestion's {label, description?} shape,
        # plain strings included (user ruling 2026-08-04)
        assert a["options"] == [{"label": "ship"},
                                {"label": "wait", "description": "until CI is green"}]
    check("a top-level ask parks as an open card and says so", _park)

    def _dismiss():
        org = org2()
        aid = org.ask_user("boss", "may I?")["asked"]
        r = org.ask_dismiss(aid)
        assert r["node"] == "boss" and "DISMISSED" in r["body"]
        a = org.d["asks"][0]
        assert a["status"] == "dismissed" and "without an answer" in a["reason"]
        expect_error(lambda: org.ask_answer(aid, text="late"), "already dismissed")
    check("the card's ✕ dismisses: nulled grey, agent told, final", _dismiss)

    def _amend():
        # FR-14 (2026-08-12): a DIFFERENT question appends a tab; the SAME
        # question text still amends its own tab in place
        org = org2()
        org.ask_user("boss", "v1?")
        r = org.ask_user("boss", "v2 — sharper question", options=["a", "b"])
        assert "appended" in r["status"], r["status"]
        opens = open_asks(org)
        assert len(opens) == 1, "re-asking joins the one open entry"
        qs = opens[0]["questions"]
        assert [q["question"] for q in qs] == ["v1?", "v2 — sharper question"]
        org.ask_user("boss", "v1?", options=["x"])   # same text ⇒ tab amend
        qs = open_asks(org)[0]["questions"]
        assert len(qs) == 2 and qs[0].get("options"), qs
    check("re-asking appends a tab; same-text re-asks amend theirs", _amend)

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
                           options=["ship", "wait"])["asked"]  # labels normalize
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

    # ← INVERTED 2026-08-12 (user ruling, FR-14): the batch model. A new
    # request of another kind no longer evicts — it JOINS the agent's one
    # open batch, and everything resolves together at the user's single
    # submit. The old cross-supersede was exactly the data loss the append
    # ruling forbids.
    def _batch_union():
        org = org2()
        org.ask_user("boss", "pending question")
        org.request_credits("boss", 30, "need more")
        assert org.d["asks"][0]["status"] == "open", (
            "a credit request must JOIN the open batch, not evict the "
            "question (FR-14 append ruling)")
        req = org.d["credit_requests"][0]
        assert req["status"] == "pending"
        org.ask_user("boss", "also — which color?")
        assert req["status"] == "pending", (
            "a question must not evict the pending credit request either")
        a = org.d["asks"][0]
        assert a["status"] == "open" and len(a["questions"]) == 2, a
        card = org.node_ask("boss")
        kinds = [t["kind"] for t in card["tabs"]]
        assert kinds == ["question", "question", "credits"], kinds
        assert set(card["revs"]) == {"ask", "credits"}, card["revs"]
    check("one active BATCH per agent: new requests append across kinds, "
          "nothing is evicted", _batch_union)

    def _withdraw():
        org = org2()
        org.ask_user("boss", "still relevant?")
        r = org.withdraw_ask("boss")
        a = org.d["asks"][0]
        assert a["status"] == "withdrawn" and "asking agent" in a["reason"]
        assert any("question" in w for w in r["withdrawn"])
        # nothing active → benign no-op result, never an error
        assert "no active request" in org.withdraw_ask("boss")["status"]
        expect_error(lambda: org.ask_answer(a["id"], text="late"),
                     "already withdrawn")
    check("the agent withdraws its own request: nulled, final, and a second "
          "withdraw is a benign no-op", _withdraw)

    def _no_wake_void():
        org = org2()
        aid = org.ask_user("boss", "q")["asked"]
        assert not hasattr(org, "void_open_asks"), \
            "the wake-void is retired (user ruling 2026-08-06) — nothing " \
            "may void an ask because a turn started"
        assert org.d["asks"][0]["status"] == "open"
        org.ask_answer(aid, text="a")
        assert org.d["asks"][0]["status"] == "answered"
    check("the wake-void is GONE: an open ask stands until answered, "
          "dismissed, withdrawn, or replaced", _no_wake_void)

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

    def _deep_gate():
        org = org2()
        expect_error(lambda: org.request_credits("kid", 9, "more"),
                     "user audience")
        org.d["audiences"].append({"grantee": "kid", "grantor": USER,
                                   "granted_at": "t", "reason": "test"})
        r = org.request_credits("kid", 9, "more")
        assert not r.get("refused"), r
        # approval cascades down the chain (user-actor reallocate, §4.6)
        org.credit_request_action("cr1", "approve")
        assert org.node("kid")["grant"] == 9
    check("a user-audience holder deep in the tree asks; approval cascades",
          _deep_gate)

    def _deep_zero_room():
        org = org2()
        org.d["audiences"].append({"grantee": "kid", "grantor": USER,
                                   "granted_at": "t", "reason": "test"})
        org.d["cascade_alloc"] = False
        # boss free = 20 - (kid seat 1 + grant 5) = 14 → drain it to zero
        org.reallocate("boss", "kid", 14)
        r = org.request_credits("kid", 40, "more")
        assert r.get("refused") and "bubbling is off" in r["status"], r
    check("deep zero headroom (no cascade, superior dry) refuses outright",
          _deep_zero_room)

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
        # FR-14 (2026-08-12): both components stay OPEN as one batch — the
        # count reads 2 open components, and the node's card composes them
        # into tabs (kind "batch"). The 2026-08-06 single-active reading is
        # superseded by the append ruling.
        assert t["asks_open"] == 2
        boss = next(n for n in t["roots"] if n["id"] == "boss")
        assert boss["ask"] is not None
        assert boss["ask"].get("kind") == "batch", boss["ask"]
        assert [x["kind"] for x in boss["ask"]["tabs"]] \
            == ["question", "credits"], boss["ask"]["tabs"]
        kid = boss["children"][0]
        assert kid.get("ask") is None
        assert len(t["asks"]) == 2, \
            "both raw components ride the asks history pool"
    check("the tree payload carries the composed batch card and the open "
          "component count", _tree_payload)

    # ── D-103: the agent must be TOLD, every turn, that its request is still
    # standing. `orgtree_withdraw_ask` already existed — nothing ever prompted
    # anyone to reach for it, so a question the user had settled by other
    # means sat on their screen until they dealt with it a second time.
    def _open_request_accessor():
        org = org2()
        assert org.open_request("boss") is None, "nothing open yet"
        org.ask_user("boss", "ship now or wait?")
        r = org.open_request("boss")
        assert r and r["kind"] == "question" and "ship now" in r["question"], r
        assert org.open_request("kid") is None, "another node's request leaked"
        org.withdraw_ask("boss")
        assert org.open_request("boss") is None, "a withdrawn ask reads open"
    check("open_request reports the node's ACTIVE request, and only its own",
          _open_request_accessor)

    def _open_request_credit():
        org = org2()
        org.request_credits("boss", 30, "more")
        r = org.open_request("boss")
        assert r and r["kind"] == "credit" and r["new"] == 30, r
    check("…including a pending CREDIT request, tagged as one",
          _open_request_credit)

    def _open_request_excludes_resolved():
        # `node_ask` deliberately lingers recently-resolved cards for the
        # DESK. open_request must not: a nulled card is not something the
        # user waits on, and telling an agent to withdraw one is nonsense.
        org = org2()
        org.ask_user("boss", "q?")
        aid = open_asks(org)[0]["id"]
        org.ask_answer(aid, text="yes")
        assert org.node_ask("boss") is not None, \
            "fixture: the desk should still linger the resolved card"
        assert org.open_request("boss") is None, \
            "an ANSWERED ask reads as open — agents would be told to " \
            "withdraw a question the user already dealt with"
    check("…and never a resolved one, unlike the desk card",
          _open_request_excludes_resolved)

    def _prompt_names_the_open_request():
        from orgtree import supervisor
        org = org2()
        bare = supervisor.identity_prompt(org, "boss")
        # the STANDING guidance names the tool in every prompt (that is the
        # tool catalogue, and it now names the trigger too). What must be
        # conditional is the PER-TURN nudge about a specific live request —
        # unconditional, it would be noise on 95% of turns and would name a
        # question that does not exist.
        assert "orgtree_withdraw_ask" in bare, \
            "the standing guidance stopped naming the tool at all"
        assert "still OPEN" not in bare, \
            "the per-turn nudge fires with no request open — it would tell " \
            "an agent to re-read a question it never asked"
        org.ask_user("boss", "ship now or wait?")
        # ⚠ D-181 MOVED THE PER-TURN NUDGE, NOT REMOVED IT. It now rides
        # `org_state_block`, which is prepended to the turn text instead of
        # baked into the appended system prompt — an open ask is live org
        # state, and anything live in the system prompt discards the whole
        # conversation cache every time it changes (see
        # test_prompt_cache_stability.py). The D-103 property this check
        # exists for is unchanged: on a turn that begins with a request open,
        # the agent is told so, is shown the question, and is told to
        # withdraw rather than re-ask.
        p = supervisor.org_state_block(org, "boss")
        assert "still OPEN" in p, p[-600:]
        assert "ship now or wait?" in p, \
            "the prompt does not quote the question, so the agent cannot " \
            "judge whether it still stands"
        # and it must not suggest re-asking: that REPLACES, it does not end
        assert "do not re-ask" in p, p[-600:]
        assert "orgtree_withdraw_ask" in p, \
            "the nudge no longer names the tool that ends the request"
        # the STANDING catalogue entry stays in the system prompt (asserted
        # via `bare` above) — the two must not collapse into one place
        assert "still OPEN" not in supervisor.identity_prompt(org, "boss"), \
            "the live per-turn nudge leaked back into the cached system " \
            "prompt — that is the D-181 regression"
    check("☞ the turn briefing names an open request and says to withdraw "
          "it if this turn made it moot", _prompt_names_the_open_request)

    print("FR-13/FR-14 · scope requests + the one-submit batch:")

    def _scope_request_basics():
        org = org2()
        # the fixture hire holds EVERY tool — shrink first, or the requests
        # drop as already-held no-ops (motto A3)
        org.set_scope(USER, "boss", tools={"bash": False, "web": False,
                                           "edit": True, "subagents": False,
                                           "mcp": []})
        r = org.request_scope("boss", [
            {"kind": "dir", "path": "E:/data", "mode": "ro"},
            {"kind": "tool", "tool": "web"},
            {"kind": "permission_mode", "mode": "bypassPermissions"}],
            "need the dataset and the docs sites")
        assert "pending" in r["status"], r
        sr = org.d["scope_requests"][0]
        assert sr["status"] == "pending" and len(sr["items"]) == 3
        # re-requesting MERGES by identity: same path upgrades in place,
        # a new tool appends
        org.request_scope("boss", [
            {"kind": "dir", "path": "E:/data", "mode": "rw"},
            {"kind": "tool", "tool": "bash"}], "now writing results too")
        sr = org.d["scope_requests"][0]
        assert len(sr["items"]) == 4, sr["items"]
        d = next(x for x in sr["items"] if x["kind"] == "dir")
        assert d["mode"] == "rw", "same-path re-ask must upgrade in place"
        assert sr["rev"] == 2, "a merge must bump the CAS rev"
    check("scope requests park, merge by identity, and bump rev",
          _scope_request_basics)

    def _scope_already_held_is_noop():
        org = org2()   # fixture boss holds edit already
        r = org.request_scope("boss", [{"kind": "tool", "tool": "edit"}],
                              "want to edit")
        assert "already hold" in r["status"], (
            r, org.node("boss")["scope"]["tools"])
    check("asking for scope you already hold is a no-op, per item",
          _scope_already_held_is_noop)

    def _scope_routes_without_audience():
        org = org2()
        org.set_scope(USER, "kid", tools={"bash": False, "web": False,
                                          "edit": True, "subagents": False,
                                          "mcp": []})
        r = org.request_scope("kid", [{"kind": "tool", "tool": "web"}],
                              "research task")
        assert r.get("routed") == "boss", r
        assert not org.d.get("scope_requests"), "routed ⇒ no pending card"
        assert any("orgtree_retool" in m["body"]
                   for m in org.d["mail"]["boss"]), (
            "the superior must be told they can grant what they hold")
    check("a deep agent's scope request routes to its superior as mail",
          _scope_routes_without_audience)

    def _one_submit_resolves_everything():
        org = org2()
        org.set_scope(USER, "boss", tools={"bash": False, "web": False,
                                           "edit": True, "subagents": False,
                                           "mcp": []})
        org.ask_user("boss", "which db?", options=["sqlite", "pg"])
        org.request_credits("boss", 30, "more compute")
        org.request_scope("boss", [
            {"kind": "dir", "path": "E:/data", "mode": "ro"},
            {"kind": "tool", "tool": "web"}], "the dataset")
        card = org.node_ask("boss")
        assert [t["kind"] for t in card["tabs"]] \
            == ["question", "credits", "scope", "scope"], card["tabs"]
        r = org.resolve_batch("boss", card["revs"],
                              answers=["sqlite"],
                              credits={"granted": 25},
                              scope=["approve", "deny"])
        body = r["body"]
        assert "sqlite" in body and "COUNTER-OFFERED" in body, body
        assert "GRANTED" in body and "denied" in body, body
        # the grants LANDED: the dir is on the node, the tool is not
        sc = org.node("boss")["scope"]
        assert any(d["path"] == "E:/data" and d["mode"] == "ro"
                   for d in sc["add_dirs"]), sc["add_dirs"]
        assert not sc["tools"]["web"], "the denied tool must NOT be granted"
        assert org.node("boss")["grant"] == 25
        # every store resolved — the batch is finished
        assert org.node_ask("boss")["status"] != "open"
        assert org.d["asks"][0]["status"] == "answered"
        assert org.d["credit_requests"][0]["status"] == "answered"
        assert org.d["scope_requests"][0]["status"] == "answered"
    check("ONE submit answers, counter-offers, grants and denies together",
          _one_submit_resolves_everything)

    def _skips_are_explicit_never_holes():
        org = org2()
        org.ask_user("boss", questions=[{"question": "a?"},
                                        {"question": "b?"}])
        org.request_credits("boss", 30, "more")
        card = org.node_ask("boss")
        # a hole (wrong count) still refuses — FR-04's miscount guard lives
        expect_error(lambda: org.resolve_batch(
            "boss", card["revs"], answers=["yes"], credits={"skip": True}),
            "exactly one per")
        r = org.resolve_batch("boss", card["revs"],
                              answers=["yes", None],
                              credits={"skip": True})
        assert "(skipped" in r["body"] and "undecided" in r["body"], r["body"]
        assert org.d["credit_requests"][0]["status"] == "dismissed"
        qs = org.d["asks"][0]["questions"]
        assert qs[0].get("answer") == "yes" and "answer" not in qs[1], qs
    check("skips are explicit nulls; holes still refuse (FR-04 guard "
          "survives)", _skips_are_explicit_never_holes)

    def _stale_batch_submit_is_refused():
        org = org2()
        org.set_scope(USER, "boss", tools={"bash": False, "web": False,
                                           "edit": True, "subagents": False,
                                           "mcp": []})
        org.ask_user("boss", "q1?")
        card = org.node_ask("boss")
        org.request_scope("boss", [{"kind": "tool", "tool": "web"}], "x")
        expect_error(lambda: org.resolve_batch(
            "boss", card["revs"], answers=["yes"]),
            "changed after it rendered")
    check("a submit against a pre-append render is refused (batch CAS)",
          _stale_batch_submit_is_refused)

    def _withdraw_covers_the_whole_batch():
        org = org2()
        org.set_scope(USER, "boss", tools={"bash": False, "web": False,
                                           "edit": True, "subagents": False,
                                           "mcp": []})
        org.ask_user("boss", "q?")
        org.request_credits("boss", 30, "more")
        org.request_scope("boss", [{"kind": "tool", "tool": "web"}], "x")
        r = org.withdraw_ask("boss")
        assert len(r["withdrawn"]) == 3, r
        assert org.d["scope_requests"][0]["status"] == "withdrawn"
    check("withdraw nulls every component of the batch", _withdraw_covers_the_whole_batch)

    def _pm_grant_applies_and_plan_ranks_lowest():
        org = org2()
        org.request_scope("boss", [
            {"kind": "permission_mode", "mode": "bypassPermissions"}],
            "must write the global skills")
        card = org.node_ask("boss")
        assert "UNGUARDED" in card["tabs"][0]["label"], card["tabs"][0]
        org.resolve_batch("boss", card["revs"], scope=["approve"])
        assert org.node("boss")["scope"]["permission_mode"] \
            == "bypassPermissions"
        # `plan` (user request 2026-08-12) is a real, LOWEST-ranked mode:
        # holding acceptEdits already covers a plan request (no-op)
        r = org.request_scope("boss", [
            {"kind": "permission_mode", "mode": "plan"}], "x")
        assert "already hold" in r["status"], r
    check("an approved permission-mode raise applies; plan ranks lowest",
          _pm_grant_applies_and_plan_ranks_lowest)

    def _deep_grant_cascades_and_absorbs():
        # surface #1 of the 2026-08-12 redteam handover, driven here: a DEEP
        # agent's approved grant raises the whole chain (D-106) and a
        # top-level absorption records it on the org
        org = Org.create("cascade-asks", dirs=["E:/w"])
        org.hire(USER, None, "opus", 20, "top")
        org.hire(USER, "top", "haiku", 2, "mid")
        org.hire(USER, "mid", "haiku", 0, "leaf")
        for k in ("top", "mid", "leaf"):
            org.set_scope(USER, k, tools={"bash": False, "web": False,
                                          "edit": True, "subagents": False,
                                          "mcp": []})
        org.d["audiences"].append({"grantee": "leaf", "grantor": USER,
                                   "granted_at": "t", "reason": "test"})
        org.request_scope("leaf", [{"kind": "tool", "tool": "bash"},
                                   {"kind": "dir", "path": "E:/new",
                                    "mode": "ro"}], "needs both")
        card = org.node_ask("leaf")
        r = org.resolve_batch("leaf", card["revs"],
                              scope=["approve", "approve"])
        assert "cascaded permission increase" in r["body"], r["body"]
        for k in ("top", "mid", "leaf"):
            sc = org.node(k)["scope"]
            assert sc["tools"]["bash"], (k, sc["tools"])
            assert any(d["path"] == "E:/new" for d in sc["add_dirs"]), k
        assert any(d["path"] == "E:/new"
                   for d in org.d.get("dirs") or []), (
            "the top-level absorption must record the folder on the ORG")
        assert not org.audit()["problems"]
    check("a deep scope grant D-106-cascades the chain and absorbs at the "
          "org", _deep_grant_cascades_and_absorbs)

    def _kiosk_clamp_verdict_is_honest():
        # found DRIVING this surface (2026-08-12): the ceiling clamped an
        # approved item away entirely while the verdict said "GRANTED — live
        # from your next turn". The verdict is now measured against the
        # post-apply scope.
        org = Org.create("kiosk-asks", dirs=["E:/w"])
        org.d["kiosk"] = {"enabled": True, "token": "t", "credits": 500}
        org = Org(org.d)
        org.set_kiosk_ceiling({"tools": {"bash": False, "web": False,
                                         "edit": True, "subagents": False,
                                         "mcp": []},
                               "add_dirs": [{"path": "E:/w", "mode": "rw"}],
                               "org_visibility": "full",
                               "permission_mode": "acceptEdits"})
        org.hire(USER, None, "haiku", 5, "boss")
        org.set_scope(USER, "boss", tools={"bash": False, "web": False,
                                           "edit": True, "subagents": False,
                                           "mcp": []})
        org.request_scope("boss", [
            {"kind": "tool", "tool": "bash"},
            {"kind": "permission_mode", "mode": "bypassPermissions"}],
            "over the ceiling")
        card = org.node_ask("boss")
        r = org.resolve_batch("boss", card["revs"],
                              scope=["approve", "approve"])
        assert "CLAMPED" in r["body"] and "NOT in effect" in r["body"], \
            r["body"]
        assert "live from your next turn" not in r["body"], (
            "an entirely-clamped approval still promises the grant: "
            + r["body"])
        sc = org.node("boss")["scope"]
        assert not sc["tools"]["bash"] \
            and sc.get("permission_mode") == "acceptEdits", sc
    check("a ceiling-clamped approval SAYS so — never 'granted' for a "
          "capability the scope does not hold", _kiosk_clamp_verdict_is_honest)

    def _kiosk_partial_clamp_says_what_landed():
        # the INVERSE of the check above (redteam, 2026-08-12): a ceiling
        # MEETS rather than annihilates, so an approval can land REAL BUT
        # SHORT — `rw` as `ro`, `bypassPermissions` as a genuine plan →
        # acceptEdits raise. Calling either "NOT in effect" is the same
        # unkeepable-promise class inverted: the agent then declines to use
        # access it actually holds. Both cases are driven here, plus the
        # no-movement case that must STILL read "not in effect".
        org = Org.create("kiosk-partial", dirs=["E:/w", "E:/half"])
        org.d["kiosk"] = {"enabled": True, "token": "t", "credits": 500}
        org = Org(org.d)
        org.set_kiosk_ceiling({"tools": {"bash": False, "web": False,
                                         "edit": True, "subagents": False,
                                         "mcp": []},
                               "add_dirs": [{"path": "E:/w", "mode": "rw"},
                                            {"path": "E:/half", "mode": "ro"}],
                               "org_visibility": "full",
                               "permission_mode": "acceptEdits"})
        org.hire(USER, None, "haiku", 5, "boss")
        org.set_scope(USER, "boss",
                      tools={"bash": False, "web": False, "edit": True,
                             "subagents": False, "mcp": []},
                      add_dirs=[{"path": "E:/w", "mode": "rw"}],  # no E:/half
                      permission_mode="plan")
        org.request_scope("boss", [
            {"kind": "dir", "path": "E:/half", "mode": "rw"},
            {"kind": "permission_mode", "mode": "bypassPermissions"}],
            "both will be met, not annihilated")
        card = org.node_ask("boss")
        body = org.resolve_batch("boss", card["revs"],
                                 scope=["approve", "approve"])["body"]
        assert "NOT in effect" not in body, (
            "a grant that really moved is reported as nothing: " + body)
        assert body.count("PARTIALLY clamped") == 2, body
        assert "you now hold E:/half (ro)" in body, body
        assert "you now hold permission mode acceptEdits" in body, body
        sc = org.node("boss")["scope"]
        assert any(d["path"] == "E:/half" and d["mode"] == "ro"
                   for d in sc["add_dirs"]), sc["add_dirs"]
        assert sc["permission_mode"] == "acceptEdits", sc
        # …and the discrimination cuts the other way too: re-asking rw for a
        # folder now held ro moves nothing, so it is NOT partial
        org.request_scope("boss", [{"kind": "dir", "path": "E:/half",
                                    "mode": "rw"}], "again")
        card = org.node_ask("boss")
        body = org.resolve_batch("boss", card["revs"], scope=["approve"])["body"]
        assert "NOT in effect" in body and "PARTIALLY" not in body, body
        assert not org.audit()["problems"]
    check("a PARTIAL ceiling clamp reports what actually landed, and a "
          "no-movement clamp still reads 'not in effect'",
          _kiosk_partial_clamp_says_what_landed)

    print(f"\nasks: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
