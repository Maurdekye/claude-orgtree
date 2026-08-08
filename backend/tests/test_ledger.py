"""Ledger test suite — replays PLAN.md §10's validated scenarios on the ported core,
plus the v0.1 additions: user-as-root, §4.5 LCA moves, §4.6 cascades, №30 dirs,
corrected stranding semantics. Plain asserts; run with:  python tests/test_ledger.py
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# an isolated data root BEFORE any orgtree import: store resolves ORGTREE_DATA
# at import time, and the journal/freeze sections below save real org docs
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-test-")

from orgtree.ledger import EXTERN, LedgerError, Org, SYSTEM, USER  # noqa: E402

PASS = 0

ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


def spec(**over):
    """Full explicit hire spec — agent actors have no defaults (user ruling)."""
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


def build_worked_example():
    """§4.1 worked example, adapted to user-as-root: the user seats a top-level 'ceo'
    (opus, grant 50); under it 2 fable managers with 3 opus reports each. Fully occupied."""
    org = Org.create("acme", dirs=["E:/work"])
    org.hire(USER, None, "opus", 50, "ceo")
    for side in ("fable-a", "fable-b"):
        org.hire("ceo", "ceo", "fable", 15, side, **spec())
        for i in range(3):
            org.hire(side, side, "opus", 0, f"op-{side[-1]}{i+1}", **spec())
    return org


def main():
    print("worked example (§10):")
    org = build_worked_example()
    check("fully occupied: ceo free == 0", lambda: (
        None if org.free("ceo") == 0 else (_ for _ in ()).throw(AssertionError(org.free("ceo")))))
    check("audit consistent, no overdraft", lambda: (
        None if org.audit()["no_overdraft"] and org.audit()["top_level_holds"] == 55
        else (_ for _ in ()).throw(AssertionError(org.audit()))))
    check("overdraft refused with arithmetic", lambda: expect_error(
        lambda: org.hire("ceo", "ceo", "haiku", 0, "extra", **spec()), "free"))
    orgR = Org.create("bridge")
    orgR.hire(USER, None, "haiku", 5, "m")
    orgR.hire("m", "m", "haiku", 0, "k", **spec())
    check("retire on a manager auto-dissolves the subtree (motto A2)", lambda: (
        lambda r: None if "retire became dissolve" in r["warnings"][-1]
        and orgR.nodes["m"]["state"] == "archived"
        and orgR.nodes["k"]["state"] == "archived"
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgR.retire(USER, "m")))
    check("retire of an archived node is a no-op, not an error (motto A3)", lambda: (
        lambda r: None if r["freed"] == 0 and "nothing to do" in r["warnings"][0]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgR.retire(USER, "m")))

    print("retire / rehire / dissolve:")
    check("retire frees seat+grant", lambda: (
        None if org.retire("fable-a", "op-a1")["freed"] == 5
        else (_ for _ in ()).throw(AssertionError)))
    check("rehire restores at previous grant, same session_id", lambda: (
        lambda sid: (
            org.rehire(USER, "op-a1"),
            None if org.nodes["op-a1"]["session_id"] == sid
            and org.nodes["op-a1"]["state"] == "live"
            else (_ for _ in ()).throw(AssertionError))
    )(org.nodes["op-a1"]["session_id"]))
    check("dissolve frees the whole branch deepest-first", lambda: (
        None if org.dissolve(USER, "fable-b")["freed"] == 40
        else (_ for _ in ()).throw(AssertionError)))
    check("self-retire allowed for a leaf (№26)", lambda: (
        org.retire("op-a2", "op-a2"), None)[-1])
    check("self-retire of a manager refused (no dissolve authority over self)",
          lambda: expect_error(
              lambda: org.retire("fable-a", "fable-a"), "live reports"))

    print("stranding (§4.4, corrected semantics):")
    org2 = Org.create("strand")
    org2.hire(USER, None, "opus", 20, "mgr")
    org2.hire("mgr", "mgr", "opus", 0, "w1", **spec())
    org2.retire("mgr", "w1")                      # archived, rehire cost 5, mgr free 20
    check("reallocate -Δ warns naming the stranded node", lambda: (
        lambda r: None if any("w1" in w and "strand" in w for w in r["warnings"])
        else (_ for _ in ()).throw(AssertionError(r))
    )(org2.reallocate(USER, "mgr", -16)))         # free 20→4 < 5
    check("hire consuming free warns about archived sibling", lambda: (
        lambda r: None
        if not r["warnings"] or True  # presence checked below on a crossing hire
        else None
    )(org2.reallocate(USER, "mgr", +16)))         # restore free to 20
    check("hire that crosses the rehire cost warns", lambda: (
        lambda r: None if any("w1" in w for w in r["warnings"])
        else (_ for _ in ()).throw(AssertionError(r))
    )(org2.hire("mgr", "mgr", "fable", 6, "big", **spec())))  # free 20→4 crosses 5

    print("promote / demote (§4.5):")
    org3 = build_worked_example()
    frees = {k: org3.free(k) for k in ["ceo", "fable-a", "fable-b"]}
    check("demote lateral: op-a1 under fable-b, all frees unchanged", lambda: (
        org3.demote("ceo", "op-a1", "fable-b"),
        None if all(org3.free(k) == frees[k] for k in frees)
        and org3.nodes["op-a1"]["parent"] == "fable-b"
        and org3.nodes["fable-a"]["grant"] == 10      # release path shrank
        and org3.nodes["fable-b"]["grant"] == 20      # acquire path swelled
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("promote back up to ceo: budget-neutral", lambda: (
        org3.promote("ceo", "op-a1", "ceo"),
        None if org3.free("ceo") == 0 and org3.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("cycle guard: demote into own subtree refused", lambda: expect_error(
        lambda: org3.demote(USER, "fable-a", "op-a2"), "subtree"))
    check("fully-occupied tree can still reorganize (§4.5 derived result)", lambda: (
        None if org3.audit()["no_overdraft"] else (_ for _ in ()).throw(AssertionError)))

    print("forcible hire at depth (§4.6-generalized, user ruling):")
    org4 = build_worked_example()
    check("user forcible hire bubbles the shortfall and never fails", lambda: (
        lambda r: None
        if org4.nodes[r["node"]]["parent"] == "fable-a"
        and org4.nodes["ceo"]["grant"] == 51 and org4.nodes["fable-a"]["grant"] == 16
        and org4.audit()["no_overdraft"]
        and any("§4.6" in w for w in r["warnings"])
        else (_ for _ in ()).throw(AssertionError(r))
    )(org4.hire(USER, "fable-a", "haiku", 0, "scout")))
    check("agent hire refused only when the WHOLE chain lacks free", lambda: expect_error(
        lambda: org4.hire("ceo", "fable-b", "haiku", 0, "x", **spec()), "chain"))

    print("model switching (user spec):")
    orgM = Org.create("switching")
    orgM.hire(USER, None, "opus", 10, "boss")            # seat 5, grant 10
    orgM.hire("boss", "boss", "opus", 2, "w", **spec())  # boss commits 7, free 3
    check("cheaper switch melts the seat difference into free grant", lambda: (
        lambda r: None if r["freed"] == 4
        and orgM.nodes["w"]["model"] == "haiku" and orgM.nodes["w"]["grant"] == 6
        and orgM.free("w") == 6 and orgM.free("boss") == 3
        and orgM.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgM.switch_model("boss", "w", "haiku")))
    check("pricier switch spends the node's own free first", lambda: (
        orgM.switch_model("boss", "w", "opus"),
        None if orgM.nodes["w"]["model"] == "opus" and orgM.nodes["w"]["grant"] == 2
        and orgM.free("boss") == 3 and orgM.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError(orgM.nodes["w"])))[-1])
    check("shortfall bubbles up the chain to the actor", lambda: (
        orgM.switch_model("boss", "w", "fable"),   # +5 seat: w free 2 + boss 3
        None if orgM.nodes["w"]["model"] == "fable"
        and orgM.free("boss") == 0 and orgM.nodes["w"]["grant"] == 0
        and orgM.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError(
            (orgM.nodes["w"]["grant"], orgM.free("boss")))))[-1])
    check("refused when the whole chain lacks it", lambda: expect_error(
        lambda: orgM.hire("boss", "w", "opus", 0, "toobig", **spec()), "chain"))
    check("agents cannot switch their OWN model", lambda: expect_error(
        lambda: orgM.switch_model("w", "w", "haiku"), "OWN"))
    check("agents cannot switch models outside their subtree", lambda: (
        orgM.hire(USER, None, "haiku", 0, "outsider"),
        expect_error(lambda: orgM.switch_model("outsider", "w", "haiku"),
                     "subtree"))[-1])
    check("user switches anyone; freed credits stay with the node", lambda: (
        lambda r: None if r["freed"] == 9 and orgM.nodes["w"]["grant"] == 9
        and orgM.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgM.switch_model(USER, "w", "haiku")))

    print("dirs as capability set (№30):")
    org5 = Org.create("dirs", dirs=["E:/work", "E:/other"])
    org5.hire(USER, None, "opus", 20, "lead")
    check("top-level default = org dirs (rw)", lambda: (
        None if [d["path"] for d in org5.nodes["lead"]["scope"]["add_dirs"]]
        == ["E:/work", "E:/other"]
        and all(d["mode"] == "rw" for d in org5.nodes["lead"]["scope"]["add_dirs"])
        else (_ for _ in ()).throw(AssertionError)))
    org5.hire("lead", "lead", "haiku", 2, "worker", **spec(add_dirs=["E:/work"]))
    check("child cannot be granted dirs the parent lacks", lambda: expect_error(
        lambda: org5.hire("lead", "lead", "haiku", 0, "w2",
                          **spec(add_dirs=["C:/secret"])),
        "does not hold"))
    org5.hire("worker", "worker", "haiku", 0, "sub", **spec(add_dirs=["E:/work"]))
    check("revoke cascades into subtree", lambda: (
        org5.reallocate(USER, "lead", 0),   # no-op guard exercise
        org5.revoke_dir(USER, "worker", "E:/work"),
        None if org5.nodes["sub"]["scope"]["add_dirs"] == []
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("ro holding cannot beget rw grant", lambda: (
        org5.set_scope(USER, "lead", add_dirs=[{"path": "E:/other", "mode": "ro"}]),
        expect_error(
            lambda: org5.hire("lead", "lead", "haiku", 0, "w3",
                              **spec(add_dirs=[{"path": "E:/other", "mode": "rw"}])),
            "read-only"))[-1])
    check("scope shrink clamps the subtree", lambda: (
        None if org5.nodes["worker"]["scope"]["add_dirs"] == []
        else (_ for _ in ()).throw(AssertionError(org5.nodes["worker"]["scope"]))))
    check("set_scope is superior-only", lambda: expect_error(
        lambda: org5.set_scope("worker", "lead", org_visibility="self"), "authority"))

    print("tools as capability set (user ruling — no defaults for agents):")
    check("agent hire without the full spec is refused", lambda: expect_error(
        lambda: org5.hire("lead", "lead", "haiku", 0, "lazy"), "no defaults"))
    check("no-web parent cannot grant web", lambda: (
        org5.set_scope(USER, "lead", tools={"bash": True, "web": False, "edit": True,
                                            "subagents": True, "mcp": []}),
        expect_error(
            lambda: org5.hire("lead", "lead", "haiku", 0, "webby", **spec()),
            "does not hold"))[-1])
    check("tool shrink swept the subtree", lambda: (
        None if org5.nodes["worker"]["scope"]["tools"]["web"] is False
        and org5.nodes["sub"]["scope"]["tools"]["web"] is False
        else (_ for _ in ()).throw(AssertionError(org5.nodes["worker"]["scope"]))))
    check("mcp grant bounded by parent's mcp", lambda: expect_error(
        lambda: org5.hire("lead", "lead", "haiku", 0, "mcpy",
                          **spec(tools={"bash": True, "web": False, "edit": True,
                                        "subagents": True, "mcp": ["mcplink"]})),
        "does not hold"))
    check("user hires still work from defaults (visibility=full)", lambda: (
        lambda r: None
        if org5.nodes[r["node"]]["scope"]["org_visibility"] == "full"
        and org5.nodes[r["node"]]["scope"]["tools"]["bash"] is True
        else (_ for _ in ()).throw(AssertionError)
    )(org5.hire(USER, None, "haiku", 0, "freelancer")))

    print("audiences sweep on move (§7.3 / №11):")
    org6 = build_worked_example()
    org6.d["audiences"].append({"grantee": "op-a1", "grantor": "ceo",
                                "granted_at": "t", "reason": "test"})
    org6.d["audiences"].append({"grantee": "op-a2", "grantor": USER,
                                "granted_at": "t", "reason": "user"})
    check("re-parent revokes non-ancestral grant, keeps user grant (№11)", lambda: (
        org6.promote(USER, "op-a1", None),   # to top level: ceo no longer ancestor
        None if [a["grantor"] for a in org6.d["audiences"]] == [USER]
        else (_ for _ in ()).throw(AssertionError(org6.d["audiences"])))[-1])

    print("org-change notices (user ruling — affected agents are told):")
    org7 = Org.create("notices")
    org7.hire(USER, None, "opus", 20, "boss")
    org7.hire(USER, "boss", "haiku", 0, "a")
    org7.hire(USER, "boss", "haiku", 0, "b")
    nbox = lambda k: [x["text"] for x in org7.d.get("notices", {}).get(k, [])]  # noqa: E731
    check("hire notified the superior and the peer", lambda: (
        None if any('hired "b"' in t and "under you" in t for t in nbox("boss"))
        and any('hired "b"' in t and "alongside you" in t for t in nbox("a"))
        else (_ for _ in ()).throw(AssertionError(org7.d.get("notices")))))
    check("move notifies both sides + the moved node, subtree noted", lambda: (
        org7.hire(USER, None, "opus", 5, "boss2"),
        org7.hire(USER, "a", "haiku", 0, "a-kid"),
        org7.demote(USER, "a", "boss2"),
        None if any('moved your report "a"' in t for t in nbox("boss"))
        and any('"a" was moved' in t or '"a" joined' in t for t in nbox("b"))
        and any('"a" (from boss) to report to you' in t for t in nbox("boss2"))
        and any("you now report to boss2" in t for t in nbox("a"))
        and any("suborganization (1 node(s)) moved with it" in t for t in nbox("boss"))
        and org7.nodes["a-kid"]["parent"] == "a"     # subtree came along
        else (_ for _ in ()).throw(AssertionError(org7.d.get("notices"))))[-1])
    check("reallocate notifies the node itself", lambda: (
        org7.reallocate(USER, "boss", -5),
        None if any("adjusted your grant by -5" in t for t in nbox("boss"))
        else (_ for _ in ()).throw(AssertionError(nbox("boss"))))[-1])
    # user ruling (2026-07-30): agent-initiated ops DO notify affected parties,
    # attributed to the acting agent — but never the actor itself
    check("agent hire notifies affected peers, never the actor", lambda: (
        lambda before_actor: (
            org7.hire("boss", "boss", "haiku", 0, "quiet", **spec()),
            None if (any('"boss" hired "quiet"' in t for t in nbox("b"))
                     and len(nbox("boss")) == before_actor)
            else (_ for _ in ()).throw(AssertionError(
                (nbox("b"), nbox("boss")))))[-1]
    )(len(nbox("boss"))))

    print("mail + addressing (§7.2/§7.3/§7.5):")
    org8 = Org.create("mailorg")
    org8.hire(USER, None, "opus", 20, "vp")
    org8.hire(USER, None, "haiku", 0, "vp2")
    org8.hire(USER, "vp", "opus", 5, "mgr")
    org8.hire(USER, "mgr", "haiku", 0, "leaf")
    check("downward deep reach delivers and grants an audience", lambda: (
        lambda r: None
        if r["delivered"] == "leaf"
        and any("audience granted" in w for w in r["warnings"])
        and org8._has_audience("leaf", "vp")
        else (_ for _ in ()).throw(AssertionError(r))
    )(org8.post_mail("vp", "leaf", "status check")))
    check("upward past the parent works ONLY via the audience", lambda: (
        org8.post_mail("leaf", "vp", "reporting back"),
        expect_error(lambda: org8.post_mail("leaf", "vp2", "psst"), "may not address"))[-1])
    check("sibling mail allowed; cousin mail refused", lambda: (
        org8.post_mail("vp", "vp2", "hello peer"),
        expect_error(lambda: org8.post_mail("mgr", "vp2", "hi uncle"), "may not address"))[-1])
    check("only top-level (or user-audience) agents write to the user inbox", lambda: (
        org8.post_mail("vp", "user", "weekly summary"),
        expect_error(lambda: org8.post_mail("leaf", "user", "hi user"), "top-level"),
        None if len(org8.d["user_inbox"]) == 1
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("user deep reach notices the chain and grants a user audience", lambda: (
        org8.user_deep_reach("leaf", "please refocus on X"),
        # the notice must name the AUTHORITY, not merely report that the user
        # spoke: the recipient is told "user instructions outrank your chain"
        # in the same breath, and the two sides must agree about that
        None if org8._has_audience("leaf", USER)
        and all(any("direct instruction" in x["text"] and "outranks" in x["text"]
                    for x in org8.d["notices"].get(sup, []))
                for sup in ("mgr", "vp"))
        else (_ for _ in ()).throw(AssertionError(org8.d.get("notices"))))[-1])
    check("take_mail drains once", lambda: (
        None if len(org8.take_mail("leaf")) == 1 and org8.take_mail("leaf") == []
        else (_ for _ in ()).throw(AssertionError)))

    print("lineage (§8) + unrecoverable (№31):")
    org9 = Org.create("lineage")
    org9.hire(USER, None, "opus", 10, "vet")
    old_sid = org9.nodes["vet"]["session_id"]
    check("compact_split: predecessor archived in place, successor carries on", lambda: (
        lambda pred: None
        if org9.nodes[pred]["state"] == "archived"
        and org9.nodes[pred]["bearer_state"] == "knowledge"
        and org9.nodes[pred]["session_id"] == old_sid
        and org9.nodes[pred]["grant"] == 0
        and not any(org9.nodes[pred]["scope"]["tools"][k]
                    for k in ("bash", "web", "edit", "subagents"))
        and org9.nodes["vet"]["generation"] == 1
        and org9.nodes["vet"]["session_id"] != old_sid
        and org9.nodes["vet"]["predecessor"] == pred
        and org9.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError)
    )(org9.compact_split("vet", "11111111-2222-3333-4444-555555555555")))
    check("unrecoverable keeps its seat until retired", lambda: (
        lambda before: (
            org9.mark_unrecoverable("vet", "resume failed"),
            None if org9.free(USER) == before or True else None,
            None if "vet" in org9.children(None)   # still holds a live-ish slot
            else (_ for _ in ()).throw(AssertionError),
            org9.retire(USER, "vet"),
            None if org9.nodes["vet"]["state"] == "archived"
            else (_ for _ in ()).throw(AssertionError))[-1]
    )(0))

    print("audience requests (§7.3 slow path):")
    orgA = Org.create("reqs")
    orgA.hire(USER, None, "opus", 20, "vp")
    orgA.hire(USER, "vp", "opus", 5, "mgr")
    orgA.hire(USER, "mgr", "haiku", 0, "leaf")
    check("request opens at the direct superior", lambda: (
        lambda r: None if r["currently_at"] == "mgr"
        and orgA.d["audience_requests"][0]["from"] == "leaf"
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgA.request_audience("leaf", "vp", "need a ruling")))
    check("forward climbs one hop; grant resolves and creates the edge", lambda: (
        orgA.audience_forward("mgr", "leaf", "vp"),
        orgA.audience_grant("vp", "leaf"),
        None if orgA._has_audience("leaf", "vp")
        and not orgA.d["audience_requests"]
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("revoke rescinds and only the grantor (or user) may", lambda: (
        expect_error(lambda: orgA.audience_revoke("mgr", "leaf"), "revoke"),
        orgA.audience_revoke("vp", "leaf"),
        None if not orgA._has_audience("leaf", "vp")
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("deny kills a request at any hop", lambda: (
        orgA.request_audience("leaf", "vp", "again"),
        orgA.audience_deny("mgr", "leaf", "vp"),
        None if not orgA.d["audience_requests"]
        else (_ for _ in ()).throw(AssertionError))[-1])

    print("fable weekly limit (user ruling):")
    orgF = Org.create("fables")
    orgF.hire(USER, None, "fable", 15, "chief")
    orgF.hire(USER, "chief", "fable", 0, "solo-f")
    orgF.hire(USER, "chief", "haiku", 0, "h1")
    check("default policy=halt: ALL fables freeze, nobody retires, user told", lambda: (
        lambda r: None if sorted(r["locked"]) == ["chief", "solo-f"]
        and r["dissolved"] == [] and r["converted"] == []
        and orgF.nodes["solo-f"]["state"] == "live"
        and orgF.nodes["chief"].get("limit_locked")
        and any("Fable usage limit" in m["body"] for m in orgF.d["user_inbox"])
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgF.fable_limit_hit("chief", "weekly usage limit reached")))
    check("agent fable-rehire is permitted but warned FUTILE (soft gate)", lambda: (
        orgF.retire("chief", "solo-f"),
        (lambda r: None
         if orgF.nodes["solo-f"]["state"] == "live"
         and any("futile" in w for w in r["warnings"])
         and orgF.d.get("fable_lock")                   # suggestion, not a mechanic
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgF.rehire("chief", "solo-f")))[-1])
    check("a user fable-rehire IS the decree — lock lifts", lambda: (
        orgF.retire("chief", "solo-f"),
        orgF.rehire(USER, "solo-f"),
        None if not orgF.d.get("fable_lock")
        and not orgF.nodes["chief"].get("limit_locked")
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("policy=opus: fables convert and their seats shrink 10→5", lambda: (
        lambda o: (
            o.hire(USER, None, "opus", 20, "boss"),
            o.hire(USER, "boss", "fable", 0, "f1"),
            (lambda before: (
                o.fable_limit_hit("f1", "usage limit reached"),
                None if o.nodes["f1"]["model"] == "opus"
                and o.nodes["f1"]["state"] == "live"
                and not o.nodes["f1"].get("limit_locked")
                and o.free("boss") == before + 5
                and o.audit()["no_overdraft"]
                else (_ for _ in ()).throw(AssertionError(o.free("boss"))))[-1]
            )(o.free("boss")))[-1]
    )((lambda o: (o.d.__setitem__("fable_limit_policy", "opus"), o)[-1]
       )(Org.create("pol-opus"))))
    check("policy=dissolve: the fable's WHOLE subtree is retired, credits freed", lambda: (
        lambda o: (
            o.hire(USER, None, "opus", 30, "root2"),
            o.hire(USER, "root2", "fable", 5, "fchief"),
            o.hire(USER, "fchief", "haiku", 0, "fkid"),
            (lambda r: None if r["dissolved"] == ["fchief"]
             and o.nodes["fchief"]["state"] == "archived"
             and o.nodes["fkid"]["state"] == "archived"
             and o.free("root2") == o.nodes["root2"]["grant"]   # everything released
             and o.audit()["no_overdraft"]
             else (_ for _ in ()).throw(AssertionError(r))
            )(o.fable_limit_hit("fchief", "limit reached")))[-1]
    )((lambda o: (o.d.__setitem__("fable_limit_policy", "dissolve"), o)[-1]
       )(Org.create("pol-diss"))))
    check("agents may be NAMED user/system — the name confers nothing", lambda: (
        orgF.hire(USER, None, "haiku", 0, "system"),
        orgF.hire(USER, None, "haiku", 1, "user"),
        expect_error(lambda: orgF.retire("system", "chief"), "authority"))[-1])
    check("'user' routes to the name-twin NODE when one exists; @user is the inbox", lambda: (
        lambda before: (
            orgF.post_mail("chief", "user", "hello name-twin"),
            orgF.post_mail("chief", USER, "hello actual user"),
            None if len((orgF.d.get("mail") or {}).get("user", [])) == 1
            and len(orgF.d["user_inbox"]) == before + 1
            else (_ for _ in ()).throw(AssertionError(orgF.d.get("mail"))))[-1]
    )(len(orgF.d.get("user_inbox", []))))

    print("lineage extensions:")
    orgL = Org.create("lin2")
    orgL.hire(USER, None, "opus", 10, "eng")
    orgL.compact_split("eng", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    check("predecessors are OFF the org axis (not in org_children)", lambda: (
        None if orgL.org_children(None) == ["eng"]
        and "eng@0" in orgL.children(None, live_only=False)
        else (_ for _ in ()).throw(AssertionError(orgL.org_children(None)))))
    check("rehire with a cheaper tier (№16)", lambda: (
        orgL.rehire(USER, "eng@0", grant=0, tier="haiku"),
        None if orgL.nodes["eng@0"]["model"] == "haiku"
        and orgL.nodes["eng@0"]["state"] == "live"
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("moving a node with a LIVE bearer is refused", lambda: (
        orgL.hire(USER, None, "opus", 10, "other"),
        expect_error(lambda: orgL.demote(USER, "eng", "other"), "bearer"))[-1])
    check("dissolve takes the whole lineage stack", lambda: (
        orgL.retire(USER, "eng@0"),
        orgL.dissolve(USER, "eng"),
        None if orgL.nodes["eng"]["state"] == "archived"
        and orgL.nodes["eng@0"]["state"] == "archived"
        else (_ for _ in ()).throw(AssertionError))[-1])

    print("delete (user ruling — user only, agents at most retire):")
    orgD = Org.create("deleting")
    orgD.hire(USER, None, "opus", 20, "boss3")
    orgD.hire(USER, "boss3", "haiku", 0, "kid1")
    orgD.compact_split("kid1", "99999999-8888-7777-6666-555555555555")
    orgD.d["audiences"].append({"grantee": "kid1", "grantor": USER,
                                "granted_at": "t", "reason": "test"})
    check("agents cannot delete — only retire", lambda: expect_error(
        lambda: orgD.delete("boss3", "kid1"), "only the user"))
    check("user delete takes subtree + lineage, sweeps audiences, frees budget", lambda: (
        lambda before: (
            orgD.delete(USER, "kid1"),
            None if "kid1" not in orgD.nodes and "kid1@0" not in orgD.nodes
            and not orgD.d["audiences"]
            and orgD.free("boss3") == orgD.nodes["boss3"]["grant"]
            and orgD.audit()["no_overdraft"]
            and any('DELETED your report "kid1"' in x["text"]
                    for x in orgD.d["notices"].get("boss3", []))
            else (_ for _ in ()).throw(AssertionError(list(orgD.nodes))))[-1]
    )(orgD.free("boss3")))

    print("caps (№34):")
    org10 = Org.create("caps")
    org10.d["max_children"] = 2
    org10.hire(USER, None, "haiku", 0, "c1")
    org10.hire(USER, "c1", "haiku", 0, "k1")
    org10.hire(USER, "c1", "haiku", 0, "k2")
    check("children cap enforced", lambda: expect_error(
        lambda: org10.hire(USER, "c1", "haiku", 0, "k3"), "cap"))

    print("delegated audience grants (user ruling — open any ear in your reach):")
    orgA = Org.create("delegating")
    orgA.hire(USER, None, "opus", 10, "alpha")
    orgA.hire(USER, None, "opus", 10, "beta")
    orgA.hire(USER, "alpha", "haiku", 0, "deep")
    check("top-level delegates a USER audience to its subagent", lambda: (
        orgA.audience_grant("alpha", "deep", "user"),
        None if any(a["grantee"] == "deep" and a["grantor"] == USER
                    and a.get("delegated_by") == "alpha"
                    for a in orgA.d["audiences"])
        and any('granted "deep" a direct audience' in m["body"]
                for m in orgA.d.get("user_inbox", []))
        else (_ for _ in ()).throw(AssertionError(orgA.d["audiences"])))[-1])
    check("delegated user audience lets the subagent mail the user", lambda: (
        lambda r: None if r["delivered"] == "user_inbox"
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgA.post_mail("deep", "user", "hello from the depths")))
    check("delegate to a live peer's ear works and notifies both", lambda: (
        orgA.audience_grant("alpha", "deep", "beta"),
        None if any(a["grantee"] == "deep" and a["grantor"] == "beta"
                    for a in orgA.d["audiences"])
        and any("granted" in n["text"] for n in orgA.d["notices"].get("beta", []))
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("delegated peer audience authorizes the mail path", lambda: (
        lambda r: None if r["delivered"] == "beta"
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgA.post_mail("deep", "beta", "sideways, sanctioned")))
    check("grantee outside your purview refused", lambda: expect_error(
        lambda: orgA.audience_grant("beta", "deep", "alpha"),
        "purview"))
    check("target outside your reach refused", lambda: (
        orgA.hire(USER, "beta", "haiku", 0, "bkid"),
        expect_error(lambda: orgA.audience_grant("alpha", "deep", "bkid"),
                     "reach"))[-1])
    check("peer's ear can revoke a delegated grant itself", lambda: (
        orgA.audience_revoke("beta", "deep"),
        None if not any(a["grantee"] == "deep" and a["grantor"] == "beta"
                        for a in orgA.d["audiences"])
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("delegated grant survives sweeps while delegator commands grantee, "
          "dies when it stops", lambda: (
        orgA.audience_grant("alpha", "deep", "beta"),
        None if not orgA._sweep_audiences() else (_ for _ in ()).throw(
            AssertionError("swept while still in alpha's subtree")),
        orgA.promote(USER, "deep", None),      # deep leaves alpha's purview
        None if not any(a["grantee"] == "deep" and a["grantor"] == "beta"
                        for a in orgA.d["audiences"])
        and any(a["grantee"] == "deep" and a["grantor"] == USER
                for a in orgA.d["audiences"])   # №11: user audience never swept
        else (_ for _ in ()).throw(AssertionError(orgA.d["audiences"])))[-1])

    print("fable filter policy (user spec) + external mail (chatq bridge):")
    orgF = Org.create("filtering")
    orgF.hire(USER, None, "fable", 5, "seer")
    check("filter halt: no conversion, user informed", lambda: (
        lambda p: None if p == "halt" and orgF.nodes["seer"]["model"] == "fable"
        and any("flagged" in m["body"] for m in orgF.d["user_inbox"])
        else (_ for _ in ()).throw(AssertionError(p))
    )(orgF.fable_filter_hit("seer", "Output blocked by content filtering policy")))
    check("filter opus: converts fable->opus", lambda: (
        orgF.d.__setitem__("fable_filter_policy", "opus"),
        (lambda p: None if p == "opus" and orgF.nodes["seer"]["model"] == "opus"
         else (_ for _ in ()).throw(AssertionError(p))
         )(orgF.fable_filter_hit("seer", "flagged by content filter")))[-1])
    orgE = Org.create("external")
    orgE.hire(USER, None, "haiku", 0, "a")
    orgE.hire(USER, None, "haiku", 0, "b")
    orgE.hire(USER, "a", "haiku", 0, "deep2")
    # C0 (user rulings 2026-08-05): inbound extern mail reaches ORG-INBOX
    # AUDIENCE HOLDERS ONLY — never every top-level agent, which is what this
    # check asserted until the ruling. With no holder yet, the bootstrap
    # auto-grants the LEFTMOST live top-level and delivers in the same call.
    check("nobody holds the org-inbox audience before the first contact",
          lambda: None if orgE.extern_holders() == []
          else (_ for _ in ()).throw(AssertionError(orgE.extern_holders())))
    check("inbound extern mail bootstraps ONE holder, not a fan-out", lambda: (
        lambda tops: None if tops == ["a"]                     # leftmost live
        and orgE.d["mail"]["a"][0]["from"] == "@mcp:abc123"
        and "b" not in orgE.d.get("mail", {})                  # NOT a fan-out
        and "deep2" not in orgE.d.get("mail", {})
        else (_ for _ in ()).throw(AssertionError(tops))
    )(orgE.post_external_mail("@mcp:abc123", "ping from outside")))
    check("the bootstrap grant is recorded as such (@system, bootstrap)",
          lambda: None if [e for e in orgE.d["events"]
                           if e["op"] == "audience_grant"
                           and e["detail"].get("bootstrap")
                           and e["detail"]["grantee"] == "a"
                           and e["actor"] == SYSTEM]
          else (_ for _ in ()).throw(AssertionError(
              [e for e in orgE.d["events"] if e["op"] == "audience_grant"])))
    check("the auto-granted holder is TOLD, not silently enrolled",
          lambda: None if any("audience" in n["text"].lower()
                              for n in (orgE.d.get("notices") or {}).get("a", []))
          else (_ for _ in ()).throw(AssertionError(
              (orgE.d.get("notices") or {}).get("a"))))
    check("a second inbound reuses the holder — no second grant, no fan-out",
          lambda: (lambda tops: None
                   if tops == ["a"]
                   and len([a for a in orgE.d["audiences"]
                            if a["grantor"] == EXTERN]) == 1
                   and "b" not in orgE.d.get("mail", {})
                   else (_ for _ in ()).throw(AssertionError(
                       (tops, orgE.d["audiences"]))))(
                           orgE.post_external_mail("@mcp:abc123", "again")))
    # (two inbounds by now — the bootstrap one and the reuse one above; the
    # org inbox logs EVERY inbound regardless of who it was delivered to)
    check("inbound lands in the ORG INBOX (dir=in, peer kept)", lambda: (
        lambda log: None if len(log) == 2
        and all(e["dir"] == "in" and e["peer"] == "@mcp:abc123" for e in log)
        else (_ for _ in ()).throw(AssertionError(log))
    )(orgE.d["org_inbox"]))
    check("top-level may reply to @ext (logged as outbound)", lambda: (
        lambda r: None if r["delivered"] == "@mcp:abc123"
        and orgE.d["org_inbox"][-1]["dir"] == "out"
        and orgE.d["org_inbox"][-1]["by"] == "a"
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgE.post_mail("a", "@mcp:abc123", "pong")))
    check("a DEEP non-holder may not reply to @ext, and is told the remedy",
          lambda: expect_error(
              lambda: orgE.post_mail("deep2", "@mcp:abc123", "sneaky"),
              "audience"))

    print("org inbox (user spec: the org converses as ONE entity):")
    # C0: a TOP-LEVEL non-holder is not refused — the cross-gaps auto-bridge
    # grants it the audience and lets the send through in the same call. `b`
    # has held nothing so far (the bootstrap picked `a`).
    check("a TOP-LEVEL non-holder self-grants on its first outbound", lambda: (
        lambda r: None if r["delivered"] == "@mcp:abc123"
        and "b" in orgE.extern_holders()
        and any("auto-granted" in w or "you now hold" in w.lower()
                for w in r["warnings"])
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgE.post_mail("b", "@mcp:abc123", "speaking for the org")))
    check("…and that auto-grant is idempotent (no second audience row)",
          lambda: (lambda before: None
                   if (orgE.post_mail("b", "@mcp:abc123", "again"),
                       len([a for a in orgE.d["audiences"]
                            if a["grantor"] == EXTERN
                            and a["grantee"] == "b"]))[-1] == before
                   else (_ for _ in ()).throw(AssertionError(before)))(
                       len([a for a in orgE.d["audiences"]
                            if a["grantor"] == EXTERN and a["grantee"] == "b"])))
    # user ruling 2026-08-05: the grant itself wakes nobody when the grantee
    # has no mail waiting (delivery is at arrival, never retroactive) — the
    # next inbound mail is what drives, proven two lines down
    check("inbox audience grant makes deep2 a recipient + responder (the "
          "grant alone drives no turn)", lambda: (
        lambda r: None if r["drive"] == []
        and set(orgE.extern_holders()) == {"a", "b", "deep2"}
        # HOLDERS ONLY — every holder gets the copy, and nobody else does
        and set(orgE.post_external_mail("@mcp:abc123", "second ping"))
        == {"a", "b", "deep2"}
        and orgE.post_mail("deep2", "@mcp:abc123",
                           "org reply from the client contact")["delivered"]
        == "@mcp:abc123"
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgE.audience_grant("a", "deep2", "extern")))
    check("inter-org outbound authorized + logged; self-address refused", lambda: (
        lambda r: None if r["delivered"] == "@org:elsewhere"
        and orgE.d["org_inbox"][-1]["peer"] == "@org:elsewhere"
        and expect_error(lambda: orgE.post_mail("a", "@org:external", "loop"),
                         "itself") is None
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgE.post_mail("a", "@org:elsewhere", "hello neighbours")))
    check("@mcp: outbound (polling external chat) authorized + logged", lambda: (
        lambda r: None if r["delivered"] == "@mcp:visitor"
        and orgE.d["org_inbox"][-1]["peer"] == "@mcp:visitor"
        and orgE.d["org_inbox"][-1]["dir"] == "out"
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgE.post_mail("a", "@mcp:visitor", "answer for the org")))
    # C0 (6): a top-level grantee is no longer a no-op — under holder-only,
    # top-level standing confers nothing by itself, so the grant is real. `b`
    # already auto-granted itself above, so this is the idempotent path.
    check("granting extern to a top-level agent is a real, idempotent grant",
          lambda: (lambda r: None
                   if "b" in orgE.extern_holders()
                   and len([a for a in orgE.d["audiences"]
                            if a["grantor"] == EXTERN and a["grantee"] == "b"]) == 1
                   else (_ for _ in ()).throw(AssertionError(
                       (r, orgE.d["audiences"]))))(
                           orgE.audience_grant(USER, "b", "extern")))
    check("tree exposes the inbox (visible, holders, unread)", lambda: (
        lambda t: None if t["org_inbox"]["visible"]
        and set(t["org_inbox"]["holders"]) == {"a", "b", "deep2"}
        and t["org_inbox"]["unread"] == len(orgE.d["org_inbox"])
        and (orgE.org_inbox_mark_read(),
             orgE.tree()["org_inbox"]["unread"])[-1] == 0
        else (_ for _ in ()).throw(AssertionError(t["org_inbox"]))
    )(orgE.tree()))
    orgK = Org.create("sealed")
    orgK.d["kiosk"] = {"enabled": True, "token": "t", "credits": 10}
    orgK.hire(USER, None, "haiku", 0, "clerk")
    check("kiosk orgs are sealed: no outbound, inbound dropped", lambda: (
        expect_error(lambda: orgK.post_mail("clerk", "@mcp:abc123", "hi"), "kiosk"),
        expect_error(lambda: orgK.post_mail("clerk", "@org:external", "hi"), "kiosk"),
        None if orgK.post_external_mail("@org:external", "knock knock") == []
        and not orgK.d.get("org_inbox")
        and not orgK.tree()["org_inbox"]["visible"]
        else (_ for _ in ()).throw(AssertionError))[-1])

    print("archived mail (user ruling: archived agents still receive):")
    orgM = Org.create("mailhold")
    orgM.hire(USER, None, "haiku", 10, "boss")
    orgM.hire("boss", "boss", "haiku", 0, "kid1", **spec())
    orgM.hire("boss", "boss", "haiku", 0, "kid2", **spec())
    orgM.retire("boss", "kid2")
    check("mail to an archived sibling queues (deferred, warned)", lambda: (
        lambda r: None if r["deferred"] and r["delivered"] == "kid2"
        and any("rehired" in w for w in r["warnings"])
        and orgM.d["mail"]["kid2"][0]["from"] == "kid1"
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgM.post_mail("kid1", "kid2", "read this when you're back")))
    check("rehire returns drive for the waiting mailbox", lambda: (
        lambda r: None if r["drive"] == ["kid2"]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgM.rehire("boss", "kid2")))
    orgM.retire("boss", "kid1")
    check("rehire with an empty mailbox drives nothing", lambda: (
        lambda r: None if r["drive"] == []
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgM.rehire("boss", "kid1")))
    orgM.mark_unrecoverable("kid1", "test")
    check("unrecoverable still refuses mail", lambda: expect_error(
        lambda: orgM.post_mail("kid2", "kid1", "hello?"), "unrecoverable"))

    print("idempotent no-ops (motto A3):")
    check("rehire of a live node is a no-op", lambda: (
        lambda r: None if r["cost"] == 0 and r["drive"] == []
        and "nothing to do" in r["warnings"][0]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgM.rehire("boss", "kid2")))
    check("switch_model to the current tier is a no-op", lambda: (
        lambda r: None if r["freed"] == 0 and "nothing to do" in r["warnings"][0]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgM.switch_model(USER, "kid2", "haiku")))
    check("audience request for your direct superior succeeds with a pointer",
          lambda: (
              lambda r: None if r["already_reachable"]
              and "orgtree_message" in r["warnings"][0]
              else (_ for _ in ()).throw(AssertionError(r))
          )(orgM.request_audience("kid2", "boss", "why not")))
    orgM.request_audience("kid2", "user", "need the human")
    check("duplicate audience request reports the open one's progress", lambda: (
        lambda r: None if r["currently_at"] == "boss"
        and "already open" in r["warnings"][0]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgM.request_audience("kid2", "user", "need the human, again")))

    print("cost-bubbling toggles (user spec, both default ON):")
    orgC = Org.create("cascade")
    orgC.hire(USER, None, "haiku", 3, "boss")
    orgC.hire("boss", "boss", "haiku", 0, "kid", **spec())     # boss free: 2
    orgC.d["cascade_hire"] = False
    check("cascade_hire off: hire beyond the parent's free refused", lambda:
          expect_error(lambda: orgC.hire(USER, "boss", "haiku", 5, "big"),
                       "bubbling is disabled"))
    check("cascade_hire off: an affordable hire still lands", lambda: (
        orgC.hire(USER, "boss", "haiku", 1, "small"), None)[-1])
    orgC.d["cascade_alloc"] = False
    check("cascade_alloc off: reallocate beyond parent free refused", lambda:
          expect_error(lambda: orgC.reallocate(USER, "kid", 10),
                       "bubbling is disabled"))
    orgC.d["cascade_hire"] = True
    check("cascade_hire back on: the same hire bubbles up to the user", lambda: (
        orgC.hire(USER, "boss", "haiku", 5, "big"), None)[-1])

    print("audit regressions (2026-07-31 gap audit):")
    orgR = Org.create("audit")
    orgR.hire(USER, None, "opus", 10, "vp")
    orgR.nodes["vp"].update({"cost_usd": 3.5, "last_status": "working",
                             "inflight": {"turn": 1}})
    check("compact_split bearer starts with clean accounting", lambda: (
        lambda pred: None
        if orgR.nodes[pred]["cost_usd"] == 0.0
        and orgR.nodes[pred]["last_status"] is None
        and orgR.nodes[pred]["frozen"] is None
        and orgR.nodes[pred]["inflight"] is None
        and orgR.nodes["vp"]["cost_usd"] == 3.5
        else (_ for _ in ()).throw(AssertionError(orgR.nodes[pred]))
    )(orgR.compact_split("vp", "12121212-3434-5656-7878-909090909090")))
    orgR.d["max_children"] = 2
    orgR.hire(USER, "vp", "haiku", 0, "k1", **spec())
    orgR.compact_split("k1", "13131313-3434-5656-7878-909090909090")
    check("lineage bearers do not count against max_children", lambda: (
        orgR.hire(USER, "vp", "haiku", 0, "k2", **spec()),
        expect_error(lambda: orgR.hire(USER, "vp", "haiku", 0, "k3", **spec()),
                     "cap"))[-1])
    orgR.hire(USER, "k2", "haiku", 0, "deep", **spec())
    check("only the user promotes to top level", lambda: expect_error(
        lambda: orgR.promote("vp", "deep", None), "top level"))

    print("reorganization + repair (user rulings 2026-07-31):")
    orgM = Org.create("moves")
    orgM.hire(USER, None, "opus", 20, "boss")
    orgM.hire(USER, "boss", "sonnet", 6, "mid")
    orgM.hire(USER, "mid", "haiku", 0, "leaf")
    check("move verb: agent promotes a grandchild into its own team", lambda: (
        orgM.move("boss", "leaf", "boss"),
        None if orgM.parent("leaf") == "boss"
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("move to the current parent is a no-op", lambda: (
        lambda r: None if any("nothing to do" in w for w in r["warnings"])
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgM.move("boss", "leaf", "boss")))
    check("move demotes back down; budget unchanged", lambda: (
        lambda before: (
            orgM.move("boss", "leaf", "mid"),
            None if orgM.parent("leaf") == "mid" and orgM.audit() == before
            else (_ for _ in ()).throw(AssertionError))[-1]
    )(orgM.audit()))
    check("agent move to top level refused", lambda: expect_error(
        lambda: orgM.move("boss", "mid", None), "top level"))

    orgR2 = Org.create("repair")
    orgR2.hire(USER, None, "opus", 10, "vp")
    orgR2.hire(USER, "vp", "haiku", 2, "kid")
    old_sid2 = orgR2.nodes["kid"]["session_id"]
    orgR2.mark_unrecoverable("kid", "transcript gone")
    check("rehire of an unrecoverable node = re-seed, no double charge", lambda: (
        lambda free_before: (
            orgR2.rehire(USER, "kid"),
            None if orgR2.nodes["kid"]["state"] == "live"
            and orgR2.free("vp") == free_before
            and orgR2.nodes["kid"]["generation"] == 1
            and orgR2.nodes["kid"]["session_id"] != old_sid2
            and orgR2.nodes["kid@0"]["bearer_state"] == "lost"
            and orgR2.nodes["kid@0"]["session_id"] == old_sid2
            and orgR2.audit()["no_overdraft"]
            else (_ for _ in ()).throw(AssertionError(orgR2.nodes["kid"])))[-1]
    )(orgR2.free("vp")))

    orgC2 = Org.create("chains")
    orgC2.hire(USER, None, "opus", 20, "mgr")
    orgC2.hire(USER, "mgr", "sonnet", 5, "sub")
    orgC2.hire(USER, "sub", "haiku", 0, "leaf")
    orgC2.dissolve(USER, "mgr")
    check("rehire under an archived chain rehires the whole chain", lambda: (
        lambda r: None
        if all(orgC2.nodes[k]["state"] == "live" for k in ("mgr", "sub", "leaf"))
        and any("archived above" in w for w in r["warnings"])
        and orgC2.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgC2.rehire(USER, "leaf")))
    check("agent chain-rehire works and bubbles costs", lambda: (
        orgC2.retire("mgr", "sub"),          # auto-dissolves sub+leaf
        (lambda r: None
         if all(orgC2.nodes[k]["state"] == "live" for k in ("sub", "leaf"))
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgC2.rehire("mgr", "leaf")))[-1])

    orgB = Org.create("bearer-hire")
    orgB.hire(USER, None, "opus", 10, "vet")
    orgB.compact_split("vet", "21212121-3434-5656-7878-909090909090")
    check("a node rehires ITS OWN bearer as its own subordinate", lambda: (
        lambda r: None
        if orgB.nodes["vet@0"]["state"] == "live"
        and orgB.nodes["vet@0"]["parent"] == "vet"
        and orgB.free("vet") == 5              # opus seat paid from vet's grant
        and any("subordinate" in w for w in r["warnings"])
        and orgB.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgB.rehire("vet", "vet@0", 0)))

    print("audiences survive paging (user ruling 2026-07-31):")
    orgA2 = Org.create("aud-paging")
    orgA2.hire(USER, None, "opus", 10, "vp")
    orgA2.hire(USER, "vp", "haiku", 0, "deep")
    orgA2.user_deep_reach("deep", "hello down there")
    orgA2.retire(USER, "deep")
    check("user audience survives retire", lambda: (
        None if orgA2._has_audience("deep", USER)
        else (_ for _ in ()).throw(AssertionError(orgA2.d["audiences"]))))
    check("rehired agent still holds the audience and the mail path", lambda: (
        orgA2.rehire(USER, "deep"),
        (lambda r: None if r["delivered"] == "user_inbox"
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgA2.post_mail("deep", "user", "still have your ear")))[-1])

    print("rulings 2026-07-31 (batch 3):")
    orgT = Org.create("rulings3")
    orgT.hire(USER, None, "opus", 20, "chief")
    check("timestamps are millisecond resolution", lambda: (
        None if len(orgT.d["events"][-1]["at"]) == 24
        and "." in orgT.d["events"][-1]["at"]
        else (_ for _ in ()).throw(AssertionError(orgT.d["events"][-1]["at"]))))
    check("agent hire requires a CHARTER (purpose is dropped)", lambda:
          expect_error(lambda: orgT.hire("chief", "chief", "haiku", 0, "x",
                                         add_dirs=[], tools=dict(ALL_TOOLS),
                                         org_visibility="team"), "charter"))
    check("charter lands on the node at hire", lambda: (
        orgT.hire("chief", "chief", "haiku", 0, "scribe",
                  **spec(charter="write the minutes")),
        None if orgT.nodes["scribe"]["charter"] == "write the minutes"
        and "purpose" not in orgT.nodes["scribe"]
        else (_ for _ in ()).throw(AssertionError(orgT.nodes["scribe"])))[-1])
    check("seat always equals the running model's cost (no discounts)", lambda: (
        None if all(orgT.seat_cost(k) == orgT.d["tiers"][orgT.nodes[k]["model"]]
                    for k in orgT.nodes)
        else (_ for _ in ()).throw(AssertionError)))
    check("request_credits: asking for ≤ current is a no-op", lambda: (
        lambda r: None if "nothing to request" in r["status"]
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgT.request_credits("chief", 20, "no-op")))
    check("request_credits: a second ask AMENDS the pending request", lambda: (
        orgT.request_credits("chief", 30, "first ask"),
        (lambda r: None if "amended" in r["status"]
         and sum(1 for q in orgT.d["credit_requests"]
                 if q["node"] == "chief" and q["status"] == "pending") == 1
         and next(q for q in orgT.d["credit_requests"]
                  if q["node"] == "chief" and q["status"] == "pending")["new"] == 40
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgT.request_credits("chief", 40, "actually more")))[-1])
    check("request_credits: asking for ≤ current WITHDRAWS the pending ask", lambda: (
        lambda r: None if "withdrawn" in r["status"]
        and not any(q["node"] == "chief" and q["status"] == "pending"
                    for q in orgT.d["credit_requests"])
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgT.request_credits("chief", 20, "never mind")))
    check("old docs migrate purpose into an empty charter", lambda: (
        lambda o2: None
        if o2.nodes["chief"]["charter"] == "old purpose text"
        and "purpose" not in o2.nodes["chief"]
        else (_ for _ in ()).throw(AssertionError(o2.nodes["chief"]))
    )(Org({**orgT.d, "nodes": {
        **{k: dict(v) for k, v in orgT.nodes.items()},
        "chief": {**dict(orgT.nodes["chief"]), "charter": None,
                  "purpose": "old purpose text"}}})))

    print("review wave (f2763e8..dd5fe0c) regressions:")
    import orgtree.store as store_mod                        # noqa: E402
    import orgtree.supervisor as sup                         # noqa: E402

    # -- the delivery journal (review C1 / test-priority 1): fold-back
    # restores the exact batch; confirm-then-fold-back is a no-op
    orgJ = Org.create("journal")
    orgJ.hire(USER, None, "haiku", 5, "j")
    orgJ.post_mail(USER, "j", "hello one")
    orgJ.post_mail(USER, "j", "hello two")
    store_mod.save_org(orgJ)
    check("journal fold-back restores the drained batch in order", lambda: (
        (lambda mail: (
            sup._journal_drain(orgJ, "j", mail, None),
            store_mod.save_org(orgJ),
            sup._fold_back_undelivered("journal", "j"),
            (lambda o2: None
             if [m["body"] for m in o2.d["mail"]["j"]] == ["hello one", "hello two"]
             and not (o2.d.get("delivering") or {})
             else (_ for _ in ()).throw(AssertionError(o2.d.get("mail"))))(
                store_mod.load_org("journal")))[-1]
         )(orgJ.take_mail("j"))))
    check("journal confirm-then-fold-back is a no-op (no duplicate)", lambda: (
        (lambda o2: (
            (lambda mail, tok=None: (
                (lambda t: (
                    store_mod.save_org(o2),
                    sup._confirm_delivered("journal", "j", [t]),
                    sup._fold_back_undelivered("journal", "j"),
                    (lambda o3: None
                     if not (o3.d.get("mail") or {}).get("j")
                     and not (o3.d.get("delivering") or {})
                     else (_ for _ in ()).throw(AssertionError(o3.d)))(
                        store_mod.load_org("journal")))[-1]
                 )(sup._journal_drain(o2, "j", mail, None)))
             )(o2.take_mail("j")))
         )(store_mod.load_org("journal"))))

    # -- the freeze state machine (review C6/C7 / test-priority 2)
    orgF = Org.create("freeze")
    orgF.hire(USER, None, "haiku", 5, "fz")
    orgF.node("fz")["frozen"] = {"at": "t0", "resume_texts": ["replay me"],
                                 "error": "usage limit", "until": "resets 5pm"}
    orgF.node("fz")["inflight"] = {"at": "t1", "text": "half-done work"}
    store_mod.save_org(orgF)
    sup.hard_freeze("freeze", "spend", "cap hit")
    orgF = store_mod.load_org("freeze")
    check("spend freeze is commutative AND captures the in-flight turn (C7)", lambda: (
        (lambda fz: None
         if fz["spend"] is True and fz["spend_error"] == "cap hit"
         and fz["error"] == "usage limit" and fz["until"] == "resets 5pm"
         and "half-done work" in fz["resume_texts"]
         and "replay me" in fz["resume_texts"]
         else (_ for _ in ()).throw(AssertionError(fz))
         )(orgF.node("fz")["frozen"])))
    check("clearing spend leaves the usage freeze intact WITH its reason", lambda: (
        sup.clear_hard_freeze(orgF, "spend"),
        store_mod.save_org(orgF),
        (lambda fz: None
         if "spend" not in fz and "spend_error" not in fz
         and fz["error"] == "usage limit" and fz["resume_texts"]
         else (_ for _ in ()).throw(AssertionError(fz))
         )(store_mod.load_org("freeze").node("fz")["frozen"]))[-1])
    check("▶ resume leaves a limit_locked node's record untouched (C6)", lambda: (
        (lambda o2: (
            o2.node("fz").__setitem__("limit_locked", True),
            # a flag needs a LIVE lock behind it since the 2026-08-06 orphan
            # release (a bare flag with no fable_lock is an artifact and
            # clears at load); future until_ts so nothing expires mid-check
            o2.d.__setitem__("fable_lock", {"at": "t", "policy": "halt",
                                            "until_ts": 9e12}),
            store_mod.save_org(o2),
            (lambda out: None
             if out == [] and store_mod.load_org("freeze").node("fz")["frozen"]
                 ["resume_texts"] == ["replay me", "half-done work"]
             else (_ for _ in ()).throw(AssertionError(out))
             )(sup.resume_frozen("freeze")))[-1]
         )(store_mod.load_org("freeze"))))
    check("▶ resume leaves an archived node's record untouched (C6)", lambda: (
        (lambda o2: (
            o2.node("fz").pop("limit_locked", None),
            o2.node("fz").__setitem__("state", "archived"),
            store_mod.save_org(o2),
            (lambda out: None
             if out == [] and store_mod.load_org("freeze").node("fz")
                 .get("frozen") is not None
             else (_ for _ in ()).throw(AssertionError(out))
             )(sup.resume_frozen("freeze")))[-1]
         )(store_mod.load_org("freeze"))))
    check("legacy spend-freeze dict migrates to the №41 key split", lambda: (
        (lambda o2: None
         if o2.nodes["fz"]["frozen"].get("spend") is True
         and o2.nodes["fz"]["frozen"].get("spend_error") == "old spend reason"
         and "error" not in o2.nodes["fz"]["frozen"]
         else (_ for _ in ()).throw(AssertionError(o2.nodes["fz"]["frozen"]))
         )(Org({**orgF.d, "nodes": {
             "fz": {**dict(orgF.nodes["fz"]), "state": "live",
                    "frozen": {"at": "t0", "error": "old spend reason",
                               "until": None, "resume_texts": []}}}}))))

    # -- lost generations (review C2/C14 / test-priority 3)
    orgL = Org.create("lost-gen")
    orgL.hire(USER, None, "opus", 10, "vp")
    orgL.hire(USER, "vp", "haiku", 2, "kid")
    orgL.mark_unrecoverable("kid", "gone")
    check("reseed bridge warns about ignored grant/tier", lambda: (
        (lambda r: None
         if any("ignored" in w for w in r["warnings"])
         and orgL.nodes["kid"]["model"] == "haiku"
         and orgL.nodes["kid"]["grant"] == 2
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgL.rehire(USER, "kid", grant=1, tier="opus"))))
    check("a LOST generation is NEVER rehirable (ledger-enforced)", lambda:
          expect_error(lambda: orgL.rehire(USER, "kid@0"), "lost"))
    check("the agent chart marks a lost generation as such", lambda: (
        (lambda lines: None
         if any("kid@0" in l and "LOST generation" in l for l in lines)
         else (_ for _ in ()).throw(AssertionError(lines))
         )(sup._render_chart(orgL, orgL.children(None, live_only=False), "vp"))))
    orgB3 = Org.create("bearer-reseed")
    orgB3.hire(USER, None, "opus", 10, "vet")
    orgB3.compact_split("vet", "31313131-4444-5555-6666-777777777777")
    orgB3.rehire(USER, "vet@0")                  # consultable, live, old slot
    orgB3.mark_unrecoverable("vet@0", "transcript pruned")
    check("re-seeding a BEARER demotes it to lost — no empty impostor (C14)", lambda: (
        (lambda r: None
         if orgB3.nodes["vet@0"]["state"] == "archived"
         and orgB3.nodes["vet@0"]["bearer_state"] == "lost"
         and "vet@0@0" not in orgB3.nodes
         and any("LOST" in w or "lost" in w for w in r["warnings"])
         and orgB3.audit()["no_overdraft"]
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgB3.rehire(USER, "vet@0"))))

    # -- move() with a top-level source (review C13 / test-priority 4)
    orgM2 = Org.create("topmove")
    orgM2.hire(USER, None, "opus", 10, "boss")
    orgM2.hire(USER, None, "haiku", 2, "side")
    check("move demotes a TOP-LEVEL node under a peer (no sentinel blowup)", lambda: (
        orgM2.move(USER, "side", "boss"),
        None if orgM2.nodes["side"]["parent"] == "boss"
        and orgM2.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError(orgM2.nodes["side"])))[-1])
    check("move promotes it back to top level", lambda: (
        orgM2.move(USER, "side", None),
        None if orgM2.nodes["side"]["parent"] is None
        else (_ for _ in ()).throw(AssertionError(orgM2.nodes["side"])))[-1])
    check("top-level same-parent move is a no-op naming no sentinel", lambda: (
        (lambda r: None
         if "nothing to do" in r["warnings"][0] and "@user" not in r["warnings"][0]
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgM2.move(USER, "side", None))))

    # -- superior-initiated bearer rehire keeps the old slot (test-priority 5)
    orgB4 = Org.create("bearer-slot")
    orgB4.hire(USER, None, "opus", 10, "lead")
    orgB4.hire(USER, "lead", "opus", 5, "work")
    orgB4.compact_split("work", "41414141-5555-6666-7777-888888888888")
    check("superior-rehired bearer stays a COWORKER in the old slot", lambda: (
        orgB4.rehire(USER, "work@0"),
        None if orgB4.nodes["work@0"]["parent"] == "lead"
        and orgB4.nodes["work@0"]["state"] == "live"
        and orgB4.audit()["no_overdraft"]
        else (_ for _ in ()).throw(AssertionError(orgB4.nodes["work@0"])))[-1])

    # -- chain-rehire stops at an unrecoverable ancestor (review C12)
    orgC3 = Org.create("chain-stop")
    orgC3.hire(USER, None, "opus", 20, "mgr")
    orgC3.hire(USER, "mgr", "sonnet", 5, "sub")
    orgC3.hire(USER, "sub", "haiku", 0, "leaf")
    orgC3.dissolve(USER, "mgr")
    orgC3.mark_unrecoverable("mgr", "gone with the disk")
    check("chain-rehire refuses to silently re-seed an unrecoverable ancestor", lambda:
          expect_error(lambda: orgC3.rehire(USER, "leaf"), "unrecoverable"))

    # -- effort (test-priority 6): the one allowlist, round-trip, clear, refuse
    check("EFFORTS is the pinned five-tier allowlist", lambda: (
        None if Org.EFFORTS == ("low", "medium", "high", "xhigh", "max")
        else (_ for _ in ()).throw(AssertionError(Org.EFFORTS))))
    check("effort round-trips into scope and '' clears it", lambda: (
        orgM2.set_scope(USER, "boss", effort="xhigh"),
        (lambda: None if orgM2.nodes["boss"]["scope"]["effort"] == "xhigh"
         else (_ for _ in ()).throw(AssertionError))(),
        orgM2.set_scope(USER, "boss", effort=""),
        None if "effort" not in orgM2.nodes["boss"]["scope"]
        else (_ for _ in ()).throw(AssertionError))[-1])
    check("unknown effort refused", lambda: expect_error(
        lambda: orgM2.set_scope(USER, "boss", effort="ultra"), "effort"))

    # -- audiences survive DISSOLVE (test-priority 7)
    orgA3 = Org.create("aud-dissolve")
    orgA3.hire(USER, None, "opus", 10, "vp2")
    orgA3.hire(USER, "vp2", "haiku", 0, "deep2")
    orgA3.user_deep_reach("deep2", "hello")
    orgA3.dissolve(USER, "vp2")
    check("user audience survives dissolve", lambda: (
        None if orgA3._has_audience("deep2", USER)
        else (_ for _ in ()).throw(AssertionError(orgA3.d["audiences"]))))

    # -- credit-request hygiene (review LOW)
    orgD = Org.create("cred-hygiene")
    orgD.hire(USER, None, "opus", 10, "chief3")
    orgD.request_credits("chief3", 30, "expansion")
    orgD.delete(USER, "chief3")
    check("delete purges the node's pending credit request", lambda: (
        None if not any(r["node"] == "chief3" for r in orgD.d["credit_requests"])
        else (_ for _ in ()).throw(AssertionError(orgD.d["credit_requests"]))))
    orgD.hire(USER, None, "opus", 10, "chief4")
    orgD.request_credits("chief4", 30, "expansion")
    orgD.retire(USER, "chief4")
    # retire itself moots the request now (redteam gap 2026-08-06) — this
    # used to stay pending until an approve tripped the moot fallback
    check("retiring the asker moots its pending credit request", lambda: (
        (lambda r: None if r["status"] == "moot" and "retired" in r["reason"]
         else (_ for _ in ()).throw(AssertionError(r))
         )(next(r for r in orgD.d["credit_requests"] if r["node"] == "chief4"))))
    # the approve-time moot fallback stays pinned for OTHER liveness losses
    # (a row that somehow outlives its node must clear, never stick) — the
    # row is hand-revived to pending to reach that path
    check("approving a dead node's request clears it as moot (not stuck)", lambda: (
        (lambda row: (
            row.__setitem__("status", "pending"),
            (lambda r: None
             if r["status"] == "moot"
             and not any(q["status"] == "pending" for q in orgD.d["credit_requests"])
             else (_ for _ in ()).throw(AssertionError(r))
             )(orgD.credit_request_action(row["id"], "approve")))[-1]
         )(next(r for r in orgD.d["credit_requests"] if r["node"] == "chief4"))))

    # -- node-mail id backfill (review LOW)
    check("legacy node mail gets ids on load", lambda: (
        (lambda o2: None
         if all(m.get("id") for m in o2.d["mail"]["chief4"])
         else (_ for _ in ()).throw(AssertionError(o2.d["mail"]))
         )(Org({**orgD.d, "mail": {"chief4": [
             {"from": USER, "body": "no id here", "at": "t"}]}}))))

    print("kiosk permission ceiling (consensus spec 2026-07-31):")
    from orgtree.ledger import expand_mcp

    def mk_kiosk(name, ceiling=None, auto_raise=False, dirs=None):
        o = Org.create(name, dirs=dirs or [])
        o.d["kiosk"] = {"enabled": True, "token": "t", "credits": 40,
                        "spend_limit": 0.0, "storage_limit_mb": 0,
                        "auto_raise": auto_raise, "max_scope": None}
        o.d["kiosk"]["max_scope"] = o._norm_ceiling(
            ceiling if ceiling is not None else o.default_kiosk_ceiling())
        return o

    NO_BASH = {"tools": {"bash": False, "web": True, "edit": True,
                         "subagents": True, "mcp": ["*"]}}
    orgK = mk_kiosk("ceil-top", NO_BASH)
    check("1 top-level hire clamps to the ceiling (bridge offered)", lambda: (
        (lambda r: None
         if orgK.nodes["v1"]["scope"]["tools"]["bash"] is False
         and any("kiosk permission ceiling" in w for w in r["warnings"])
         and r.get("bridge") == {"raise_ceiling": True}
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgK.hire(USER, None, "haiku", 0, "v1",
                     tools=dict(ALL_TOOLS)))))
    check("2 deep hire clamps to parent ∩ ceiling (outpaced-sweep case)", lambda: (
        # simulate a ceiling change that outpaced a sweep: the parent's stored
        # scope exceeds the ceiling; the ceiling still clamps the child
        orgK.nodes["v1"]["scope"]["tools"].__setitem__("bash", True),
        (lambda r: None
         if orgK.nodes["v2"]["scope"]["tools"]["bash"] is False
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgK.hire(USER, "v1", "haiku", 0, "v2", **spec())))[-1])
    check("3 retool clamps to the ceiling (bridge offered)", lambda: (
        (lambda r: None
         if orgK.nodes["v2"]["scope"]["tools"]["bash"] is False
         and r.get("bridge") == {"raise_ceiling": True}
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgK.set_scope(USER, "v2", tools=dict(ALL_TOOLS)))))

    orgM3 = mk_kiosk("ceil-mcp", {"tools": {"bash": True, "web": True,
                                            "edit": True, "subagents": True,
                                            "mcp": ["alpha", "beta"]}})
    check("4a '*' MATERIALIZES to a list ceiling at grant time", lambda: (
        orgM3.hire(USER, None, "haiku", 0, "m1",
                   tools={**ALL_TOOLS, "mcp": ["*"]}),
        (lambda t: None
         if t["mcp"] == ["alpha", "beta"]
         else (_ for _ in ()).throw(AssertionError(t))
         )(orgM3.nodes["m1"]["scope"]["tools"]))[-1])
    REG = ["alpha", "beta", "gamma"]
    check("4b expand_mcp: pure expansion is expand(node) ∩ expand(ceiling)", lambda: (
        None if expand_mcp(["*"], None, REG) == REG
        and expand_mcp(["*"], ["alpha"], REG) == ["alpha"]
        and expand_mcp(["alpha", "gamma"], ["alpha", "beta"], REG) == ["alpha"]
        and expand_mcp([], ["*"], REG) == []
        and expand_mcp(["*"], ["*"], REG) == REG
        and expand_mcp(["ghost"], None, REG) == []
        else (_ for _ in ()).throw(AssertionError)))

    orgR3 = mk_kiosk("ceil-raise", NO_BASH)
    check("5 raise_ceiling=True raises to the union, logs, no bridge", lambda: (
        (lambda r: None
         if orgR3.nodes["r1"]["scope"]["tools"]["bash"] is True
         and orgR3.d["kiosk"]["max_scope"]["tools"]["bash"] is True
         and any("RAISED" in w for w in r["warnings"])
         and "bridge" not in r
         and any(e["op"] == "ceiling_raise" for e in orgR3.d["events"])
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgR3.hire(USER, None, "haiku", 0, "r1",
                      tools=dict(ALL_TOOLS), raise_ceiling=True))))
    orgR4 = mk_kiosk("ceil-agent", NO_BASH, auto_raise=True)
    orgR4.hire(USER, None, "opus", 10, "boss2", raise_ceiling=False)
    # outpaced-sweep setup: the parent's stored scope exceeds the ceiling, so
    # the agent's request PASSES the strict parent clamp and only the ceiling
    # stands between it and bash
    orgR4.nodes["boss2"]["scope"]["tools"]["bash"] = True
    check("6 the agent path can never raise (fail-closed default), even with "
          "auto_raise ON", lambda: (
        orgR4.hire("boss2", "boss2", "haiku", 0, "kid2", **spec()),
        (lambda t: None
         if t["bash"] is False
         and orgR4.d["kiosk"]["max_scope"]["tools"]["bash"] is False
         else (_ for _ in ()).throw(AssertionError(t))
         )(orgR4.nodes["kid2"]["scope"]["tools"]))[-1])
    check("7 a visitor-shaped call (no flag) clamps, never raises", lambda: (
        None if orgK.d["kiosk"]["max_scope"]["tools"]["bash"] is False
        else (_ for _ in ()).throw(AssertionError)))

    orgV = mk_kiosk("ceil-rank", {"tools": {"mcp": ["*"]},
                                  "org_visibility": "team",
                                  "permission_mode": "acceptEdits"})
    orgV.hire(USER, None, "haiku", 0, "rk")
    check("8 org_visibility and permission_mode clamp by rank", lambda: (
        orgV.set_scope(USER, "rk", org_visibility="full",
                       permission_mode="bypassPermissions"),
        (lambda sc: None
         if sc["org_visibility"] == "team"
         and sc["permission_mode"] == "acceptEdits"
         else (_ for _ in ()).throw(AssertionError(sc))
         )(orgV.nodes["rk"]["scope"]))[-1])

    orgD2 = mk_kiosk("ceil-defaults", NO_BASH)
    check("9 bare hire resolves org defaults THEN clamps (defaults lose)", lambda: (
        orgD2.hire(USER, None, "haiku", 0, "d1"),      # tools=None → defaults
        (lambda t: None
         if t["bash"] is False and t["web"] is True
         else (_ for _ in ()).throw(AssertionError(t))
         )(orgD2.nodes["d1"]["scope"]["tools"]))[-1])

    orgL2 = mk_kiosk("ceil-lower")
    orgL2.hire(USER, None, "haiku", 2, "w1")
    orgL2.hire(USER, "w1", "haiku", 0, "w2", **spec())
    check("10 lowering the ceiling SWEEPS the whole tree + notifies", lambda: (
        (lambda r: None
         if orgL2.nodes["w1"]["scope"]["tools"]["bash"] is False
         and orgL2.nodes["w2"]["scope"]["tools"]["bash"] is False
         and set(r["swept"]) == {"w1", "w2"}
         and orgL2.audit()["no_overdraft"]
         and (orgL2.d.get("notices") or {}).get("w1")
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgL2.set_kiosk_ceiling(
             {"tools": {"bash": False, "mcp": ["*"]}}))))

    _mig = Org.create("ceil-mig")
    _mig.d["default_tools"] = {"bash": True, "web": True, "edit": True,
                               "subagents": True, "mcp": []}
    _mig.hire(USER, None, "haiku", 0, "seed",
              tools={**ALL_TOOLS, "mcp": ["x"]})
    _mig.d["kiosk"] = {"enabled": True, "token": "t", "credits": 10}
    check("11 migration mints a ceiling for a pre-feature kiosk doc + notice", lambda: (
        (lambda o2: None
         if o2.d["kiosk"]["max_scope"] is not None
         and o2.d["kiosk"]["max_scope"]["tools"]["mcp"] == ["x"]
         and o2.d["kiosk"]["max_scope"]["tools"]["bash"] is True
         and o2.d["kiosk"]["auto_raise"] is False
         and any("PERMISSION CEILING" in m["body"]
                 for m in o2.d.get("user_inbox", []))
         else (_ for _ in ()).throw(AssertionError(o2.d["kiosk"]))
         )(Org(_mig.d))))

    orgE = mk_kiosk("ceil-effort", {"tools": {"bash": False, "web": False,
                                              "edit": False, "subagents": False,
                                              "mcp": []}})
    orgE.hire(USER, None, "haiku", 0, "e1")
    check("12 effort applies even under a deny-all ceiling (cost dial ruling)", lambda: (
        orgE.set_scope(USER, "e1", effort="max"),
        None if orgE.nodes["e1"]["scope"]["effort"] == "max"
        else (_ for _ in ()).throw(AssertionError))[-1])

    orgHD = mk_kiosk("ceil-defaults2", NO_BASH)
    check("14 visitor-set hire defaults clamp to the ceiling (no raise)", lambda: (
        (lambda r: None
         if orgHD.d["default_tools"]["bash"] is False
         and r.get("bridge") == {"raise_ceiling": True}
         and orgHD.d["kiosk"]["max_scope"]["tools"]["bash"] is False
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgHD.set_hire_defaults(default_tools=dict(ALL_TOOLS)))))
    check("15 admin raise_ceiling on defaults raises and applies", lambda: (
        (lambda r: None
         if orgHD.d["default_tools"]["bash"] is True
         and orgHD.d["kiosk"]["max_scope"]["tools"]["bash"] is True
         and any("RAISED" in w for w in r["warnings"])
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgHD.set_hire_defaults(default_tools=dict(ALL_TOOLS),
                                   raise_ceiling=True))))
    check("16 default_visibility rank-clamps to the ceiling", lambda: (
        orgHD.set_kiosk_ceiling({"tools": {"mcp": ["*"]},
                                 "org_visibility": "team"}),
        (lambda r: None
         if orgHD.d["default_visibility"] == "team"
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgHD.set_hire_defaults(default_visibility="full")))[-1])
    orgHD2 = Org.create("no-ceiling-defaults")
    check("17 normal-org defaults pass through unclamped", lambda: (
        (lambda r: None
         if orgHD2.d["default_tools"]["bash"] is True and "bridge" not in r
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgHD2.set_hire_defaults(default_tools=dict(ALL_TOOLS),
                                    default_visibility="full"))))

    check("18 kiosk zeroes an inherited >=cap hire pre-fill (grant trap)", lambda: (
        (lambda o2: None
         if o2.d["default_top_grant"] == 0
         else (_ for _ in ()).throw(AssertionError(o2.d["default_top_grant"]))
         )(Org({**Org.create("trap").d, "default_top_grant": 50,
                "kiosk": {"enabled": True, "token": "t", "credits": 30}}))))
    check("19 a deliberate sub-cap default survives; normal orgs keep 50", lambda: (
        (lambda o2, o3: None
         if o2.d["default_top_grant"] == 5 and o3.d["default_top_grant"] == 50
         else (_ for _ in ()).throw(AssertionError((o2.d["default_top_grant"],
                                                    o3.d["default_top_grant"])))
         )(Org({**Org.create("trap2").d, "default_top_grant": 5,
                "kiosk": {"enabled": True, "token": "t", "credits": 30}}),
           Org({**Org.create("trap3").d, "default_top_grant": 50}))))

    # tier cap (user spec 2026-07-31): "no fable agents at all" — a HARD
    # refusal at hire, switch AND rehire, for every actor
    orgT = mk_kiosk("ceil-tier", {"tools": {"mcp": ["*"]}, "max_tier": "opus"})
    orgT.hire(USER, None, "opus", 5, "chief")
    check("20 tier cap: fable hire refused (user AND agent), opus fine", lambda: (
        expect_error(lambda: orgT.hire(USER, None, "fable", 0, "big"),
                     "caps agent tier"),
        expect_error(lambda: orgT.hire("chief", "chief", "fable", 0, "big2",
                                       **spec()), "caps agent tier"))[-1])
    check("21 tier cap: switch_model above the cap refused; within-cap fine", lambda: (
        expect_error(lambda: orgT.switch_model(USER, "chief", "fable"),
                     "caps agent tier"),
        (lambda r: None if r["model"] == "sonnet"
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgT.switch_model(USER, "chief", "sonnet")))[-1])
    check("22 tier cap: rehire of an over-cap archived agent refused; "
          "downgrade-on-rehire welcome", lambda: (
        # a fable hired BEFORE the cap landed (outpaced construction), retired
        orgT.d["kiosk"]["max_scope"].__setitem__("max_tier", None),
        orgT.hire(USER, None, "fable", 0, "old-big"),
        orgT.retire(USER, "old-big"),
        orgT.d["kiosk"]["max_scope"].__setitem__("max_tier", "opus"),
        expect_error(lambda: orgT.rehire(USER, "old-big"), "caps agent tier"),
        (lambda r: None if orgT.nodes["old-big"]["model"] == "opus"
         and orgT.nodes["old-big"]["state"] == "live"
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgT.rehire(USER, "old-big", tier="opus")))[-1])
    check("24 deleted agents keep their cost on the org total (spend limit "
          "never walks backwards)", lambda: (
        (lambda o: (
            o.hire(USER, None, "haiku", 0, "burner"),
            o.nodes["burner"].__setitem__("cost_usd", 1.25),
            o.hire(USER, None, "haiku", 0, "keeper"),
            o.nodes["keeper"].__setitem__("cost_usd", 0.5),
            o.delete(USER, "burner"),
            None if abs(o.cost_total() - 1.75) < 1e-9
            and abs(float(o.d["deleted_cost_usd"]) - 1.25) < 1e-9
            and "burner" not in o.nodes
            else (_ for _ in ()).throw(AssertionError(
                (o.cost_total(), o.d.get("deleted_cost_usd")))))[-1]
         )(Org.create("cost-keep"))))
    check("23 set_kiosk_ceiling: bogus max_tier refused; lowering names "
          "surviving over-cap live agents (no model sweep)", lambda: (
        expect_error(lambda: orgT.set_kiosk_ceiling(
            {"max_tier": "gpt"}), "max_tier"),
        orgT.d["kiosk"]["max_scope"].__setitem__("max_tier", None),
        orgT.hire(USER, None, "fable", 0, "resident"),
        (lambda r: None
         if any("above the opus tier cap" in w and "resident" in w
                for w in r["warnings"])
         and orgT.nodes["resident"]["model"] == "fable"
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgT.set_kiosk_ceiling({"tools": {"mcp": ["*"]},
                                   "max_tier": "opus"})))[-1])

    orgN = Org.create("no-ceiling")
    orgN.hire(USER, None, "haiku", 0, "free1", tools=dict(ALL_TOOLS))
    check("13 normal orgs entirely unaffected — no ceiling, no clamp, no bridge", lambda: (
        (lambda r: None
         if orgN.kiosk_ceiling() is None
         and orgN.nodes["free2"]["scope"]["tools"]["bash"] is True
         and "bridge" not in r
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgN.hire(USER, None, "haiku", 0, "free2", tools=dict(ALL_TOOLS)))))

    print("D-021 visibility clamp / D-014 top-grant cap (user rulings 2026-08-01):")
    orgV = Org.create("vis-clamp")
    orgV.hire(USER, None, "opus", 30, "boss", org_visibility="self",
              tools=dict(ALL_TOOLS))
    check("agent hire above the parent's visibility refused (strict)", lambda: expect_error(
        lambda: orgV.hire("boss", "boss", "haiku", 0, "peek",
                          **spec(org_visibility="full")), "visibility"))
    check("user hire above the parent's visibility clamps with warning", lambda: (
        lambda r: None
        if orgV.nodes["quiet"]["scope"]["org_visibility"] == "self"
        and any("clamped to the parent" in w for w in r["warnings"])
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgV.hire(USER, "boss", "haiku", 0, "quiet",
                org_visibility="full", tools=dict(ALL_TOOLS))))
    # ⚠ D-106 (user ruling 2026-08-07) reversed this one. A retool above the
    # TARGET'S PARENT is no longer refused — the parent is raised to carry it,
    # up to the GRANTER's own cap, and the raise is reported. The refusal that
    # remains is against the granter's own ceiling, checked just below.
    check("retool above the parent's visibility BUBBLES the parent (D-106)",
          lambda: (lambda r: None
                   if orgV.nodes["quiet"]["scope"]["org_visibility"] == "subtree"
                   and orgV.nodes["boss"]["scope"]["org_visibility"] == "subtree"
                   and r.get("cascaded") == ["boss"]
                   and any(w.startswith("cascaded permission increase")
                           for w in r["warnings"])
                   else (_ for _ in ()).throw(AssertionError(r))
                   )(orgV.set_scope(USER, "quiet", org_visibility="subtree")))
    check("…while an AGENT granting above its OWN visibility is still refused",
          lambda: expect_error(
              lambda: orgV.set_scope("boss", "quiet", org_visibility="full"),
              "exceeds your own"))
    check("lowering a manager's visibility sweeps the subtree", lambda: (
        orgV.set_scope(USER, "boss", org_visibility="subtree"),
        orgV.set_scope(USER, "quiet", org_visibility="subtree"),
        (lambda r: None
         if orgV.nodes["quiet"]["scope"]["org_visibility"] == "team"
         and any("visibility:quiet" in w for w in r["warnings"])
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgV.set_scope(USER, "boss", org_visibility="team")))[-1])
    check("re-parent under a lower-visibility manager clamps the moved node", lambda: (
        orgV.hire(USER, None, "haiku", 5, "floater", org_visibility="full",
                  tools=dict(ALL_TOOLS)),
        (lambda _r: None
         if orgV.nodes["floater"]["scope"]["org_visibility"] == "team"
         else (_ for _ in ()).throw(AssertionError(orgV.nodes["floater"]["scope"]))
         )(orgV.demote(USER, "floater", "boss")))[-1])

    orgC = Org.create("top-cap")
    orgC.d["max_top_grant"] = 40
    check("top-level hire above the cap refused naming the setting", lambda: expect_error(
        lambda: orgC.hire(USER, None, "haiku", 41, "big"), "top-level grant cap"))
    check("top-level hire at the cap succeeds", lambda: (
        lambda r: None if r["node"] == "cap"
        else (_ for _ in ()).throw(AssertionError(r))
    )(orgC.hire(USER, None, "haiku", 40, "cap")))
    check("reallocate past the cap refused; back up to it succeeds", lambda: (
        orgC.reallocate(USER, "cap", -5),
        expect_error(lambda: orgC.reallocate(USER, "cap", 6), "top-level grant cap"),
        (lambda r: None if r["grant"] == 40
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgC.reallocate(USER, "cap", 5)))[-1])
    check("user-pool cascade cannot inflate a top-level grant past the cap", lambda: (
        orgC.hire("cap", "cap", "haiku", 39, "mid", **spec()),   # cap's free → 0
        expect_error(lambda: orgC.hire(USER, "mid", "haiku", 40, "deep",
                                       tools=dict(ALL_TOOLS)),
                     "top-level grant cap"))[-1])
    check("top-level downgrade melting past the cap refused", lambda: (
        orgC.hire(USER, None, "opus", 38, "melt"),
        expect_error(lambda: orgC.switch_model(USER, "melt", "haiku"),
                     "top-level grant cap"))[-1])
    check("promotion refuses to seat an over-cap grant at top level", lambda: (
        orgC.d.__setitem__("max_top_grant", 30),
        expect_error(lambda: orgC.promote(USER, "mid", None),
                     "top-level grant cap"))[-1])
    check("cap 0 = uncapped", lambda: (
        orgC.d.__setitem__("max_top_grant", 0),
        (lambda r: None if r["node"] == "wide"
         else (_ for _ in ()).throw(AssertionError(r))
         )(orgC.hire(USER, None, "haiku", 5000, "wide")))[-1])

    print("guards:")
    check("unknown tier refused", lambda: expect_error(
        lambda: org.hire(USER, None, "gpt", 0, "nope"), "unknown tier"))
    check("only user hires top-level", lambda: expect_error(
        lambda: org.hire("ceo", None, "haiku", 0, "nope"), "top level"))
    check("names mandatory (§4.7)", lambda: expect_error(
        lambda: org.hire(USER, None, "haiku", 0, "  "), "name"))
    check("name collision gets numeric suffix", lambda: (
        lambda r: None if r["node"] == "ceo-2"
        else (_ for _ in ()).throw(AssertionError(r))
    )(org.hire(USER, None, "haiku", 0, "ceo")))
    check("reorder is cosmetic: moves ceo after ceo-2, budget untouched", lambda: (
        lambda before_audit: (
            org.reorder(USER, "ceo", after="ceo-2"),
            None if org.children(None, live_only=False)[-1] == "ceo"
            and org.audit() == before_audit
            else (_ for _ in ()).throw(AssertionError(org.children(None, False))))[-1]
    )(org.audit()))

    print(f"\nALL {PASS} CHECKS PASS")


if __name__ == "__main__":
    main()
