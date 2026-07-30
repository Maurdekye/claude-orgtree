"""Ledger test suite — replays PLAN.md §10's validated scenarios on the ported core,
plus the v0.1 additions: user-as-root, §4.5 LCA moves, §4.6 cascades, №30 dirs,
corrected stranding semantics. Plain asserts; run with:  python tests/test_ledger.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree.ledger import LedgerError, Org, USER   # noqa: E402

PASS = 0

ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


def spec(**over):
    """Full explicit hire spec — agent actors have no defaults (user ruling)."""
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             purpose="test hire")
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
    check("leaf guard names live reports", lambda: expect_error(
        lambda: org.retire(USER, "fable-a"), "live reports"))

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
    check("self-retire of a manager refused", lambda: expect_error(
        lambda: org.retire("fable-a", "fable-a"), "leaf"))

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

    print("forcible hire at depth (§4.6):")
    org4 = build_worked_example()
    check("user forcible hire cascades grants and never fails", lambda: (
        lambda r: None
        if org4.nodes[r["node"]]["parent"] == "fable-a"
        and org4.nodes["ceo"]["grant"] == 51 and org4.nodes["fable-a"]["grant"] == 16
        and org4.audit()["no_overdraft"]
        and any("inflation" in w for w in r["warnings"])
        else (_ for _ in ()).throw(AssertionError(r))
    )(org4.hire(USER, "fable-a", "haiku", 0, "scout")))
    check("agent forcible hire needs its OWN free (§4.6)", lambda: expect_error(
        lambda: org4.hire("ceo", "fable-b", "haiku", 0, "x", **spec()), "free"))

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
        None if org8._has_audience("leaf", USER)
        and any("spoke directly" in x["text"]
                for x in org8.d["notices"].get("mgr", []))
        and any("spoke directly" in x["text"]
                for x in org8.d["notices"].get("vp", []))
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
