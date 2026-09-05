"""D-224 — seat exchange, atomic batch moves, and parent insertion.

    python backend/tests/test_seat_topology.py

Three topology verbs share one property that the rest of the ledger relies
on: they rearrange WHO SITS WHERE without inventing or destroying credits,
capabilities or authority. So this suite is written around invariants rather
than shapes — after every applied operation it re-derives `free()` at every
node, walks the parent graph for cycles, and checks child ⊆ parent on all
four clamped capability sets. A check that only asserted the two parent
pointers would pass on a tree whose accounting had quietly rotted.

    §1  seat exchange — the pair swaps positions, seats keep their teams
    §2  the hand-over: hire a replacement, step under it, retire
    §3  refusals (authority, top level, lineage bearers, liveness) are no-ops
    §4  atomic batch moves, and the position swap that KEEPS both teams
    §5  parent insertion — hire/rehire `target` × `hire_type`
    §6  the HTTP surface: both new tools, and the destination pair end to end

⚠ §1 and §5 are deliberately different operations and the difference is the
whole reason both exist. A seat EXCHANGE gives each agent the other's team
(the seats are what hold the reports); an INSERTION gives the new agent the
target's position while the target keeps its own team beneath it. Reversing
either one silently produces the other's shape, so several checks here assert
where the CHILDREN ended up, not just the two nodes that moved.
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-seattop-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# never let a fixture org reach the operator's real mail hub (see test_ledger)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["ORGTREE_PORT"] = "7493"
os.environ["ORGTREE_STEER_HOOK"] = "0"

from fastapi.testclient import TestClient                      # noqa: E402
from orgtree import api, sandbox, store, supervisor            # noqa: E402
from orgtree.ledger import LedgerError, Org, USER              # noqa: E402
from orgtree.mcptool import TOOLS                              # noqa: E402

supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
supervisor.storage_check = lambda slug: None
sandbox.warm = lambda org: None

PASS = 0
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True,
             "mcp": []}


def spec(**over):
    """A full explicit hire spec — agent actors have no defaults."""
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="test hire — do test things")
    s.update(over)
    return s


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def t(label):
    def deco(fn):
        check(label, fn)
        return fn
    return deco


def eq(got, want, what=""):
    assert got == want, f"{what}: {got!r} != {want!r}"


def true(cond, msg=""):
    assert cond, msg or "expected true"


def expect_error(fn, needle=""):
    try:
        fn()
    except LedgerError as e:
        assert needle.lower() in str(e).lower(), f"wrong error: {e}"
        return str(e)
    raise AssertionError(f"expected LedgerError containing {needle!r}, "
                         f"got success")


def frees(org):
    return {k: org.free(k) for k in org.nodes}


def parents(org):
    return {k: v["parent"] for k, v in org.nodes.items()}


def fingerprint(org):
    """The structural core a REFUSED op must leave byte-identical."""
    return {k: (v["parent"], v["grant"], v["state"], v["model"],
                v.get("team_charter"), repr(v["scope"]))
            for k, v in org.nodes.items()}


def dirset(org, nid):
    return {d["path"] for d in org.nodes[nid]["scope"]["add_dirs"]}


def assert_sound(org, where):
    """Every invariant the whole ledger rests on, re-derived from scratch."""
    for k, n in org.nodes.items():
        g = n["grant"]
        true(isinstance(g, int) and not isinstance(g, bool),
             f"{where}: {k} grant is {g!r}, not an int")
        true(g >= 0, f"{where}: {k} grant went negative ({g})")
        derived = g - sum(org.seat_cost(c) + org.nodes[c]["grant"]
                          for c in org.children(k))
        eq(org.free(k), derived, f"{where}: free({k}) not derivable")
        if n["state"] == "live":
            true(org.free(k) >= 0, f"{where}: {k} overdrafted")
        p = n["parent"]
        true(p is None or p in org.nodes, f"{where}: {k} dangling parent {p!r}")
        if n["state"] == "live" and p is not None:
            true(org.nodes[p]["state"] != "archived",
                 f"{where}: live {k} hangs under ARCHIVED {p}")
        seen, cur = set(), k
        while cur is not None:                    # the cycle walk
            true(cur not in seen, f"{where}: parent CYCLE through {k}")
            seen.add(cur)
            cur = org.nodes[cur]["parent"]
        if p is not None:                         # child ⊆ parent, all four
            psc, sc = org.nodes[p]["scope"], n["scope"]
            pm = {d["path"]: d["mode"] for d in psc["add_dirs"]}
            for d in sc["add_dirs"]:
                held = pm.get(d["path"])
                true(held is not None and not (held == "ro" and d["mode"] == "rw"),
                     f"{where}: {k} holds {d} its parent {p} does not")
            for key in ("bash", "web", "edit", "subagents"):
                true(not sc["tools"].get(key) or psc["tools"].get(key, True),
                     f"{where}: {k} holds tool {key} its parent {p} lacks")
    true(org.audit()["no_overdraft"], f"{where}: {org.audit()['problems']}")


# =====================================================================  §1
print("§1  seat exchange — the pair swaps positions, seats keep their teams")


def team_org():
    """boss ▸ lead ▸ {w1, w2, second}. Everything opus so tier arithmetic
    cannot mask a real credit movement."""
    org = Org.create("swap-fixture", dirs=["E:/w", "E:/r"])
    org.hire(USER, None, "opus", 80, "boss")
    org.hire(USER, "boss", "opus", 40, "lead",
             add_dirs=[{"path": "E:/w", "mode": "rw"},
                       {"path": "E:/r", "mode": "ro"}],
             tools=dict(ALL_TOOLS), org_visibility="full", charter="lead")
    org.set_scope(USER, "lead", team_charter="the lead's standing orders")
    org.hire("lead", "lead", "opus", 5, "w1", **spec())
    org.hire("lead", "lead", "opus", 3, "w2", **spec())
    org.hire("lead", "lead", "opus", 6, "second",
             **spec(add_dirs=[{"path": "E:/w", "mode": "rw"}]))
    org.set_scope(USER, "second", effort="high")
    return org


@t("a DIRECT report and its superior exchange seats: parents, teams, grants")
def _():
    org = team_org()
    f0, r = frees(org), None
    r = org.subjugate("lead", "lead", "second")
    eq(org.nodes["second"]["parent"], "boss", "the risen takes the seat")
    eq(org.nodes["lead"]["parent"], "second", "the descended goes beneath it")
    for k in ("w1", "w2"):
        eq(org.nodes[k]["parent"], "second", f"{k} follows the SEAT")
    eq(org.nodes["second"]["grant"], 40, "the seat's grant")
    eq(org.nodes["lead"]["grant"], 6, "…and the other seat's")
    eq(r["nested"], True)
    # same tier throughout ⇒ every node's free is exactly what it was
    after = frees(org)
    eq({k: after[k] for k in ("boss", "w1", "w2")},
       {k: f0[k] for k in ("boss", "w1", "w2")}, "bystanders")
    eq(after["second"], f0["lead"], "the risen inherits the seat's headroom")
    eq(after["lead"], f0["second"])
    assert_sound(org, "after a direct exchange")


@t("…the SEAT keeps scope and team charter; the AGENT keeps identity")
def _():
    org = team_org()
    sid_lead = org.nodes["lead"]["session_id"]
    sid_second = org.nodes["second"]["session_id"]
    org.subjugate("lead", "lead", "second")
    eq(dirset(org, "second"), {"E:/w", "E:/r"}, "seat scope rose with the seat")
    eq(dirset(org, "lead"), {"E:/w"}, "…and the smaller scope came down")
    eq(org.nodes["second"]["scope"]["org_visibility"], "full")
    eq(org.nodes["second"].get("team_charter"), "the lead's standing orders",
       "the standing orders bind the TEAM, so they stay with the seat")
    eq(org.nodes["lead"].get("team_charter"), None)
    eq(org.nodes["second"]["scope"].get("effort"), "high",
       "effort is the agent's own dial — it travels WITH the agent")
    eq(org.nodes["lead"]["session_id"], sid_lead, "sessions never move")
    eq(org.nodes["second"]["session_id"], sid_second)
    eq(org.nodes["lead"]["charter"], "lead", "charter is identity")
    assert_sound(org, "after a scope-carrying exchange")


@t("…and an exchange that RAISES either side's scope says so too")
def _():
    org = team_org()
    org.set_scope(USER, "second", permission_mode="plan")
    r = org.subjugate("lead", "lead", "second")
    said = " ".join(r["warnings"])
    true('"second" took the other seat\'s scope' in said, said)
    true("E:/r" in said and "permission mode plan → acceptEdits" in said, said)
    true('"lead" took' not in said,
         "the descended GAINED nothing, so nothing should be claimed for it")
    assert_sound(org, "after a scope-raising exchange")


@t("a DEEP descendant exchange keeps a standing audience both ways")
def _():
    org = Org.create("deep-swap")
    org.hire(USER, None, "opus", 60, "top")
    org.hire("top", "top", "opus", 30, "m1", **spec())
    org.hire("m1", "m1", "opus", 20, "m2", **spec())
    org.hire("m2", "m2", "opus", 8, "leaf", **spec())
    f0 = frees(org)
    r = org.subjugate("m1", "m1", "leaf")
    eq(org.nodes["leaf"]["parent"], "top")
    eq(org.nodes["m2"]["parent"], "leaf", "the seat's team followed the seat")
    eq(org.nodes["m1"]["parent"], "m2", "…into the target's old slot")
    eq(r["audience_retained"], True)
    true(org._has_audience("m1", "leaf"),
         "the descended must still be able to speak to the risen")
    eq(frees(org)["top"], f0["top"], "same tier ⇒ nothing moved at the top")
    assert_sound(org, "after a deep exchange")
    # …and the retained grant is anchored (§7.3): it survives an unrelated
    # sweep, and dies exactly when the risen stops commanding the descended
    org._sweep_audiences()
    true(org._has_audience("m1", "leaf"), "swept away while still ancestral")
    org.move(USER, "m1", "top")
    true(not org._has_audience("m1", "leaf"),
         "the anchor stopped commanding it — §7.3 must revoke")


@t("a DIRECT pair gets no retained audience (the superior link already is one)")
def _():
    org = team_org()
    r = org.subjugate("lead", "lead", "second")
    eq(r["audience_retained"], False)
    true(not org._has_audience("lead", "second"),
         "a report already reaches its own superior — no grant needed")


@t("cross-tier exchange: the seat-cost difference lands on the two payers only")
def _():
    org = Org.create("xtier")
    org.hire(USER, None, "opus", 60, "top")
    org.hire("top", "top", "opus", 40, "mid", **spec())     # seat 5
    org.hire("mid", "mid", "opus", 20, "deep", **spec())
    org.hire("deep", "deep", "haiku", 6, "cheap", **spec())  # seat 1
    f0 = frees(org)
    org.subjugate("mid", "mid", "cheap")
    f1 = frees(org)
    eq(f1["top"], f0["top"] + 4, "top now seats a haiku where an opus sat")
    eq(f1["deep"], f0["deep"] - 4, "…and the opus landed on the deep seat")
    eq(f1["cheap"], f0["mid"], "each agent inherits the seat's headroom")
    eq(f1["mid"], f0["cheap"])
    assert_sound(org, "after a cross-tier exchange")


@t("…and it is REFUSED, changing nothing, when the payer cannot afford it")
def _():
    org = Org.create("xtier-poor")
    org.hire(USER, None, "opus", 21, "top")       # top: seat 5 + 21 grant
    org.hire("top", "top", "haiku", 15, "mid", **spec())     # seat 1
    org.hire("mid", "mid", "opus", 10, "rich", **spec())     # seat 5
    eq(org.free("top"), 5, "the fixture's headroom")
    org.hire("top", "top", "haiku", 4, "filler", **spec())   # top free → 0
    eq(org.free("top"), 0)
    fp = fingerprint(org)
    expect_error(lambda: org.subjugate("mid", "mid", "rich"), "reallocate")
    eq(fingerprint(org), fp, "a refused exchange must move nothing")
    assert_sound(org, "after a refused cross-tier exchange")


@t("the pairwise swap works on DISJOINT branches too, teams staying put")
def _():
    org = Org.create("disjoint")
    org.hire(USER, None, "opus", 60, "root")
    org.hire("root", "root", "opus", 12, "a", **spec())
    org.hire("root", "root", "opus", 16, "b", **spec())
    org.hire("a", "a", "opus", 0, "ka", **spec())
    org.hire("b", "b", "opus", 0, "kb", **spec())
    f0 = frees(org)
    r = org.swap_seats("root", "a", "b")
    eq(r["nested"], False)
    eq(org.nodes["a"]["parent"], "root")
    eq(org.nodes["b"]["parent"], "root")
    eq(org.nodes["ka"]["parent"], "b", "the SEAT keeps its team — b took it")
    eq(org.nodes["kb"]["parent"], "a")
    eq(org.nodes["a"]["grant"], 16, "grants ride the seats")
    eq(org.nodes["b"]["grant"], 12)
    f1 = frees(org)
    eq(f1["root"], f0["root"], "budget-neutral at the shared parent")
    eq(f1["a"], f0["b"])
    eq(f1["b"], f0["a"])
    assert_sound(org, "after a disjoint swap")


@t("swap normalizes orientation: swap(deep, shallow) == swap(shallow, deep)")
def _():
    def build():
        o = Org.create("orient")
        o.hire(USER, None, "opus", 50, "top")
        o.hire("top", "top", "opus", 20, "up", **spec())
        o.hire("up", "up", "opus", 8, "down", **spec())
        return o
    o1, o2 = build(), build()
    o1.swap_seats("top", "up", "down")
    o2.swap_seats("top", "down", "up")
    eq(parents(o1), parents(o2), "argument order must not change the outcome")
    eq({k: v["grant"] for k, v in o1.nodes.items()},
       {k: v["grant"] for k, v in o2.nodes.items()})


@t("the tree SHAPE is preserved, so no exchange can ever build a cycle")
def _():
    org = team_org()
    org.hire("boss", "boss", "opus", 4, "other", **spec())

    def shape(o):
        """Two invariants of the UNLABELLED tree. An exchange relabels which
        agent occupies which position, so the names per slot are expected to
        move; the branching and the depths are not."""
        return (sorted(len(o.children(k, live_only=False))
                       for k in list(o.nodes) + [None]),
                sorted(o.depth(k) for k in o.nodes))
    shape0 = shape(org)
    for a, b in (("lead", "w1"), ("second", "w2"), ("lead", "second"),
                 ("w1", "w2"), ("boss", "other")):
        org.swap_seats(USER, a, b)
        assert_sound(org, f"after swapping {a}/{b}")
        eq(shape(org), shape0, f"swapping {a}/{b} changed the tree's shape")


# =====================================================================  §2
print("\n§2  the hand-over: hire a replacement, step under it, retire")


@t("the full workflow runs end to end and the credits come out even")
def _():
    org = Org.create("handover", dirs=["E:/w"])
    org.hire(USER, None, "opus", 60, "boss")
    org.hire(USER, "boss", "opus", 30, "old",
             add_dirs=[{"path": "E:/w", "mode": "rw"}], tools=dict(ALL_TOOLS),
             org_visibility="full", charter="the outgoing agent")
    org.hire("old", "old", "opus", 2, "helper", **spec())
    boss_free0 = org.free("boss")
    # ① the outgoing agent hires its own replacement out of its own grant
    org.hire("old", "old", "opus", 7, "new",
             **spec(add_dirs=[{"path": "E:/w", "mode": "rw"}],
                    charter="the incoming agent"))
    # ② …seats it in its own place and steps under it
    org.subjugate("old", "old", "new")
    eq(org.nodes["new"]["parent"], "boss")
    eq(org.nodes["old"]["parent"], "new")
    eq(org.nodes["helper"]["parent"], "new", "the team stayed with the seat")
    # ③ …and retires as a leaf under the normal rule (№26)
    free_new = org.free("new")
    freed = org.retire("old", "old")["freed"]
    eq(freed, 12, "seat 5 + the 7 it held")
    eq(org.free("new"), free_new + freed,
       "the outgoing agent's seat and grant fall to its successor")
    eq(org.free("boss"), boss_free0, "the boss's books are exactly as before")
    assert_sound(org, "after a completed hand-over")


@t("a superior with live reports still cannot self-retire (№26 survives)")
def _():
    org = Org.create("no-escape")
    org.hire(USER, None, "opus", 40, "boss")
    org.hire("boss", "boss", "opus", 20, "mid", **spec())
    org.hire("mid", "mid", "opus", 5, "kid", **spec())
    expect_error(lambda: org.retire("mid", "mid"), "live reports")
    # the exchange is what makes the retirement legal — not a way around it
    org.subjugate("mid", "mid", "kid")
    eq(org.retire("mid", "mid")["freed"], 10)
    assert_sound(org, "after the legal route")


# =====================================================================  §3
print("\n§3  refusals are no-ops (authority, top level, bearers, liveness)")


@t("subjugate refuses a target that is not a live descendant")
def _():
    org = team_org()
    org.hire("boss", "boss", "opus", 0, "cousin", **spec())
    org.retire("lead", "w2")            # an ARCHIVED descendant is no target
    fp = fingerprint(org)
    expect_error(lambda: org.subjugate("lead", "lead", "cousin"),
                 "not a live descendant")
    expect_error(lambda: org.subjugate("lead", "lead", "lead"),
                 "second party")
    expect_error(lambda: org.subjugate("lead", "lead", "ghost"), "no such node")
    expect_error(lambda: org.subjugate("lead", "lead", "w2"),
                 "not a live descendant")
    eq(fingerprint(org), fp, "no refusal may leave a trace")


@t("authority is downward only: nobody swaps seats outside its own reach")
def _():
    org = team_org()
    org.hire("boss", "boss", "opus", 0, "cousin", **spec())
    fp = fingerprint(org)
    expect_error(lambda: org.swap_seats("lead", "cousin", "w1"), "authority")
    expect_error(lambda: org.swap_seats("w1", "lead", "second"), "authority")
    expect_error(lambda: org.subjugate("w1", "lead", "second"), "authority")
    eq(fingerprint(org), fp)


@t("§7.4: ordinary top-level swaps stay user-only — from either side")
def _():
    org = Org.create("topgate")
    org.hire(USER, None, "opus", 40, "t1")
    org.hire(USER, None, "opus", 20, "t2")
    org.hire("t1", "t1", "opus", 6, "kid", **spec())
    fp = fingerprint(org)
    expect_error(lambda: org.swap_seats("t1", "t1", "kid"), "only the user")
    expect_error(lambda: org.swap_seats("t1", "kid", "t1"), "only the user")
    eq(fingerprint(org), fp, "the refused top-level swap changed nothing")
    org.subjugate(USER, "t1", "kid")             # the user may
    eq(org.nodes["kid"]["parent"], None)
    eq(org.nodes["t1"]["parent"], "kid")
    assert_sound(org, "after a user-performed top-level exchange")


@t("a top-level coordinator voluntarily hands its own seat to a live report")
def _():
    org = team_org()
    grants = {k: n["grant"] for k, n in org.nodes.items()}
    sessions = {k: n.get("session_id") for k, n in org.nodes.items()}
    r = org.subjugate("boss", "boss", "lead")
    eq(org.nodes["lead"]["parent"], None)
    eq(org.nodes["boss"]["parent"], "lead")
    eq(org.nodes["lead"]["grant"], grants["boss"])
    eq(org.nodes["boss"]["grant"], grants["lead"])
    eq({k: n.get("session_id") for k, n in org.nodes.items()}, sessions)
    true("next_step" in r)
    assert_sound(org, "after voluntary top-level handoff")


@t("a deep top-level handoff retains upward communication and rejects other chains")
def _():
    org = team_org()
    org.hire(USER, None, "opus", 10, "other")
    before = fingerprint(org)
    expect_error(lambda: org.subjugate("boss", "boss", "other"), "not a live descendant")
    expect_error(lambda: org.subjugate("lead", "boss", "w1"), "authority")
    expect_error(lambda: org.subjugate("boss", "boss", "boss"), "second party")
    eq(fingerprint(org), before)
    r = org.subjugate("boss", "boss", "w1")
    eq(org.nodes["w1"]["parent"], None)
    eq(org.nodes["boss"]["parent"], "lead")
    true(r["audience_retained"])
    true(org._has_audience("boss", "w1"))
    eq(org.nodes["other"]["parent"], None)
    assert_sound(org, "after deep voluntary handoff")


@t("a top-level grant cap is respected on the way through (D-014)")
def _():
    org = Org.create("topcap")
    org.d["max_top_grant"] = 40
    org.hire(USER, None, "haiku", 40, "t1")
    org.hire("t1", "t1", "haiku", 20, "kid", **spec())
    # the grant VALUE rides the seat, so the top seat still holds 40 after —
    # the exchange cannot inflate it, and the cap is therefore untouchable
    org.subjugate("t1", "t1", "kid")
    eq(org.nodes["kid"]["grant"], 40, "the seat's grant, unchanged in value")
    true(org.nodes["kid"]["grant"] <= org.d["max_top_grant"])
    assert_sound(org, "after a capped top-level exchange")


@t("lineage bearers refuse on BOTH sides (§8.5 — a stack has no seat)")
def _():
    org = Org.create("bearers")
    org.hire(USER, None, "haiku", 60, "root")
    org.hire("root", "root", "haiku", 30, "boss", **spec())
    org.hire("boss", "boss", "haiku", 10, "worker", **spec())
    org.compact_split("worker", "sess-2")        # mints bearer worker@0
    expect_error(lambda: org.subjugate("boss", "boss", "worker@0"),
                 "not a live descendant")        # archived: not a target
    org.rehire(USER, "worker@0", grant=2)        # …now it is live
    fp = fingerprint(org)
    expect_error(lambda: org.subjugate("boss", "boss", "worker@0"),
                 "lineage bearer")
    expect_error(lambda: org.subjugate("boss", "boss", "worker"),
                 "live lineage bearer")
    eq(fingerprint(org), fp)


@t("☠ REGRESSION: a swap may not land an agent in its OWN stack's slot")
def _():
    """Redteam 2026-09-02, reproduced end to end. §8.5's archived bearer
    shares its owner's parent slot, so it TRAVELS with the owner. If the
    other agent happens to sit under that bearer, the owner is sent into the
    very slot its stack is being moved to — and the relabeling that cannot
    build a cycle on the org axis builds one on the lineage axis. Measured
    before the guard: a@0.parent == 'a@0' (a self-cycle), free(a@0) == -25,
    from one 200-OK orgtree_swap."""
    org = Org.create("stackcycle")
    org.hire(USER, None, "opus", 60, "p")
    org.hire("p", "p", "opus", 25, "a", **spec())
    org.compact_split("a", "sess-2")                # a@0: archived bearer of a
    eq(org.nodes["a@0"]["parent"], "p", "the fixture: the stack shares p's slot")
    # a bearer that was REHIRED, hired under, and archived again — the shape
    # `reseed`'s lost-generation branch leaves behind, and the one the
    # property fuzzer walked into
    org.rehire(USER, "a@0", grant=0)
    org.hire(USER, "a@0", "opus", 0, "z", **spec())
    org.nodes["a@0"]["state"] = "archived"
    fp = fingerprint(org)
    expect_error(lambda: org.swap_seats(USER, "a", "z"), "cycle, not a swap")
    expect_error(lambda: org.swap_seats(USER, "z", "a"), "cycle, not a swap")
    eq(fingerprint(org), fp, "the refusal must leave the tree untouched")
    # …and the same slot, reached the other way, is refused for insertion too
    expect_error(lambda: org.insert_parent(USER, "z", "a@0"), "archived")
    eq(fingerprint(org), fp)
    for k, n in org.nodes.items():                  # the invariant that broke
        seen, cur = set(), k
        while cur is not None:
            true(cur not in seen, f"parent CYCLE through {k}")
            seen.add(cur)
            cur = org.nodes[cur]["parent"]


@t("☠ REGRESSION: …at ANY depth on that bearer's branch, not just its slot")
def _():
    """The first guard tested only the immediate destination, and the cycle
    does not need the destination to BE the bearer — only to sit somewhere on
    its branch, which travels with it while nothing re-parents it (redteam
    2026-09-02, second pass). Measured one level deeper: q0.parent == 'a@0'
    AND a@0.parent == 'q0', a real 2-cycle with the owner orphaned inside."""
    def bearer_branch(depth):
        org = Org.create(f"deepstack{depth}")
        org.hire(USER, None, "opus", 60, "p")
        org.hire("p", "p", "opus", 25, "a", **spec())
        org.compact_split("a", "sess-2")
        org.rehire(USER, "a@0", grant=12)
        prev = "a@0"
        for i in range(depth):
            org.hire(USER, prev, "opus", 0, f"q{i}", **spec())
            prev = f"q{i}"
        org.nodes["a@0"]["state"] = "archived"
        return org, prev
    for d in (1, 2, 3):
        org, deep = bearer_branch(d)
        fp = fingerprint(org)
        for x, y in (("a", deep), (deep, "a")):
            expect_error(lambda x=x, y=y: org.swap_seats(USER, x, y),
                         "cycle, not a swap")
        eq(fingerprint(org), fp, f"depth {d}: a refusal changed the tree")
        for k in org.nodes:
            seen, cur = set(), k
            while cur is not None:
                true(cur not in seen, f"depth {d}: parent CYCLE through {k}")
                seen.add(cur)
                cur = org.nodes[cur]["parent"]


@t("…and the widened guard still refuses nothing legitimate")
def _():
    """The closure is over the STACK's branch only. Descending into one's own
    ORG descendant is the nested case — the commonest swap there is — and
    closing over {mover} ∪ descendants(mover) the way _move does would refuse
    every one of them."""
    org = team_org()
    org.subjugate("lead", "lead", "w1")            # direct, nested
    assert_sound(org, "nested swap after the guard")
    org2 = Org.create("guardwide")
    org2.hire(USER, None, "opus", 60, "top")
    org2.hire("top", "top", "opus", 30, "m1", **spec())
    org2.hire("m1", "m1", "opus", 12, "m2", **spec())
    org2.hire("m2", "m2", "opus", 4, "m3", **spec())
    org2.compact_split("m1", "s2")                 # a stack that is NOT in the way
    org2.subjugate("top", "m1", "m3")              # deep, nested, with a bearer
    eq(org2.nodes["m3"]["parent"], "top")
    eq(org2.nodes["m1@0"]["parent"], "m2",
       "the bearer followed its owner into the new slot (§8.5)")
    assert_sound(org2, "deep swap with a bystanding bearer")


@t("☞ REGRESSION: two SIBLINGS may swap even at a fully occupied parent")
def _():
    """Their shared superior loses and gains both seats at once, so its
    committed total is unchanged and nothing is owed. Pricing one leg at a
    time refused the verb's most obvious use at free 0 — naming a cost that
    does not exist (redteam 2026-09-02)."""
    org = Org.create("siblings")
    org.hire(USER, None, "opus", 26, "p")
    org.hire("p", "p", "haiku", 10, "cheap", **spec())    # seat 1
    org.hire("p", "p", "opus", 10, "dear", **spec())      # seat 5
    eq(org.free("p"), 0, "the fixture is fully occupied")
    f0 = frees(org)
    r = org.swap_seats("p", "cheap", "dear")
    eq(org.free("p"), 0, "…and stays exactly as occupied")
    eq(org.nodes["cheap"]["grant"], 10)
    eq(frees(org), f0, "a sibling swap moves nothing at all")
    true(not any("reallocate" in w for w in r["warnings"]), r["warnings"])
    assert_sound(org, "after a sibling cross-tier swap")


@t("…while a cross-tier swap across DIFFERENT parents is still priced")
def _():
    org = Org.create("nonsibling")
    org.hire(USER, None, "opus", 40, "root")
    org.hire("root", "root", "opus", 12, "pa", **spec())
    org.hire("root", "root", "opus", 6, "pb", **spec())
    org.hire("pa", "pa", "opus", 7, "big", **spec())      # seat 5
    org.hire("pb", "pb", "haiku", 0, "small", **spec())   # seat 1
    eq(org.free("pb"), 5)
    org.reallocate(USER, "pb", -5)                        # pb free → 0
    fp = fingerprint(org)
    expect_error(lambda: org.swap_seats(USER, "big", "small"), "reallocate")
    eq(fingerprint(org), fp, "…and the refusal is still free")


@t("☞ REGRESSION: the depth cap prices the rising branch as RISING")
def _():
    """`rising` becomes the superior, so ITS branch moves up one — pricing it
    as descending refused an insertion above a leaf a whole level early
    (redteam 2026-09-02)."""
    org = Org.create("depth-rise")
    org.d["max_depth"] = 4
    org.hire(USER, None, "opus", 60, "a")             # 0
    org.hire("a", "a", "opus", 30, "b", **spec())     # 1
    org.hire("b", "b", "opus", 12, "c", **spec())     # 2
    org.hire("c", "c", "opus", 4, "d", **spec())      # 3 — the cap's edge
    # inserting `d` above `c`: d rises to 2, c drops to 3 — legal, and the
    # old arithmetic called it depth 4
    org.insert_parent("a", "d", "c")
    eq(org.nodes["d"]["parent"], "b")
    eq(org.nodes["c"]["parent"], "d")
    assert_sound(org, "after an insertion at the depth boundary")
    # …and a genuine overflow is still refused: a real branch below the target
    org2 = Org.create("depth-real")
    org2.d["max_depth"] = 4
    org2.hire(USER, None, "opus", 60, "a")
    org2.hire("a", "a", "opus", 30, "b", **spec())
    org2.hire("b", "b", "opus", 12, "c", **spec())
    org2.hire("c", "c", "opus", 4, "d", **spec())
    org2.hire("b", "b", "opus", 2, "riser", **spec())
    expect_error(lambda: org2.insert_parent("a", "riser", "b"),
                 "max org depth")


@t("…and no agent may be seated under an ARCHIVED node by either verb")
def _():
    org = Org.create("archdest")
    org.hire(USER, None, "opus", 60, "root")
    org.hire("root", "root", "opus", 20, "live1", **spec())
    org.hire("root", "root", "opus", 10, "gone", **spec())
    org.hire(USER, "gone", "opus", 0, "under", **spec())
    org.nodes["gone"]["state"] = "archived"         # the state the fuzzer built
    fp = fingerprint(org)
    expect_error(lambda: org.swap_seats(USER, "live1", "under"), "archived")
    eq(fingerprint(org), fp)


@t("an archived participant is refused, and the refusal is free")
def _():
    org = team_org()
    org.retire("lead", "w1")
    fp = fingerprint(org)
    expect_error(lambda: org.swap_seats(USER, "w1", "w2"), "not live")
    expect_error(lambda: org.swap_seats(USER, "w2", "w1"), "not live")
    eq(fingerprint(org), fp)


# =====================================================================  §4
print("\n§4  atomic batch moves")


def batch_org():
    org = Org.create("batch")
    org.hire(USER, None, "opus", 60, "root")
    org.hire("root", "root", "opus", 12, "s1", **spec())
    org.hire("root", "root", "opus", 14, "s2", **spec())
    org.hire("s1", "s1", "opus", 0, "d1", **spec())
    org.hire("s2", "s2", "opus", 0, "d2", **spec())
    return org


@t("a batch applies every move, in order")
def _():
    org = batch_org()
    r = org.move_batch("root", [("d1", "s2"), ("d2", "s1")])
    eq(r["moved"], 2)
    eq(org.nodes["d1"]["parent"], "s2")
    eq(org.nodes["d2"]["parent"], "s1")
    assert_sound(org, "after a batch")


@t("☞ a refusal at step 2 rolls step 1 back — byte-identical, and it says so")
def _():
    org = batch_org()
    fp = fingerprint(org)
    msg = expect_error(
        lambda: org.move_batch("root", [("d1", "s2"), ("d2", "nowhere")]),
        "batch refused at step 2/2")
    true("nothing was applied" in msg, msg)
    eq(fingerprint(org), fp, "step 1 must not survive its batch")
    assert_sound(org, "after a rolled-back batch")


@t("…and that holds when the LAST step is the illegal one (a cycle)")
def _():
    org = batch_org()
    fp = fingerprint(org)
    expect_error(lambda: org.move_batch(
        "root", [("d1", "s2"), ("d2", "s1"), ("s1", "d2")]), "cycle")
    eq(fingerprint(org), fp)


@t("the position swap that KEEPS both teams is two batched moves")
def _():
    org = batch_org()
    f0 = frees(org)
    org.move_batch("root", [("s1", "s2"), ("s2", "root")])
    # s1 now reports to s2 — each kept its OWN child, which is exactly what
    # the seat exchange does not do (§1)
    eq(org.nodes["d1"]["parent"], "s1", "s1 kept its own team")
    eq(org.nodes["d2"]["parent"], "s2", "…and s2 kept its own")
    eq(frees(org), f0, "§4.5: a move is budget-neutral, so a batch of them is")
    assert_sound(org, "after a team-preserving position swap")


@t("empty and oversized batches refuse, and 20 is the documented ceiling")
def _():
    org = batch_org()
    expect_error(lambda: org.move_batch("root", []), "empty batch")
    expect_error(lambda: org.move_batch("root", [("d1", "s2")] * 21),
                 "at most 20 moves")
    eq(len(org.move_batch("root", [("d1", "s2")] * 20)["warnings"]) >= 0, True)


# =====================================================================  §5
print("\n§5  parent insertion — the new seat takes the target's position")


def insert_org():
    org = Org.create("insert", dirs=["E:/w", "E:/r"])
    org.hire(USER, None, "opus", 80, "boss")
    org.hire(USER, "boss", "opus", 30, "mid",
             add_dirs=[{"path": "E:/w", "mode": "rw"},
                       {"path": "E:/r", "mode": "ro"}],
             tools=dict(ALL_TOOLS), org_visibility="full", charter="mid")
    org.hire("mid", "mid", "opus", 3, "k1", **spec())
    org.hire("mid", "mid", "opus", 2, "k2", **spec())
    return org


@t("insertion above SELF: the new seat rises, the caller keeps its whole team")
def _():
    org = insert_org()
    f0 = frees(org)
    org.hire("mid", "mid", "opus", 4, "new", **spec())
    f1 = frees(org)
    eq(f1["mid"], f0["mid"] - 9, "the ordinary hire cost (seat 5 + grant 4)")
    r = org.insert_parent("mid", "new", "mid")
    eq(org.nodes["new"]["parent"], "boss", "it took the caller's position")
    eq(org.nodes["mid"]["parent"], "new")
    for k in ("k1", "k2"):
        eq(org.nodes[k]["parent"], "mid",
           f"{k} stayed with its own superior — insertion is not an exchange")
    eq(r["under"], "boss")
    eq(frees(org), f1, "the insertion itself moves NO credits at all")
    eq(org.free("boss"), f0["boss"], "so the whole workflow costs the caller, "
                                     "never the caller's superior")
    assert_sound(org, "after inserting above self")


@t("…and the inserted seat is given the target's capability scope")
def _():
    org = insert_org()
    org.hire("mid", "mid", "opus", 4, "new",
             **spec(add_dirs=[{"path": "E:/w", "mode": "rw"}],
                    org_visibility="team"))
    eq(dirset(org, "new"), {"E:/w"}, "as hired, clamped to the caller")
    org.insert_parent("mid", "new", "mid")
    eq(dirset(org, "new"), {"E:/w", "E:/r"},
       "an inserted parent must hold what its new subtree holds")
    eq(org.nodes["new"]["scope"]["org_visibility"], "full")
    assert_sound(org, "child ⊆ parent must survive the insertion")


@t("☞ …and it SAYS what that scope handed over (redteam 2026-09-02)")
def _():
    """The copy is forced — the target's branch sits beneath the newcomer and
    child ⊆ parent must hold, permission mode included. Doing it silently is
    the problem: an agent seated at `plan` with no tools can come back from an
    insertion holding `bypassPermissions` and the seat's folders, and nobody
    chose that."""
    org = Org.create("disclose", dirs=["E:/w"])
    org.hire(USER, None, "opus", 80, "boss")
    org.hire(USER, "boss", "opus", 30, "mid",
             add_dirs=[{"path": "E:/w", "mode": "rw"}], tools=dict(ALL_TOOLS),
             org_visibility="full", charter="mid")
    org.set_scope(USER, "mid", permission_mode="bypassPermissions")
    org.hire(USER, "mid", "opus", 1, "quiet", add_dirs=[],
             tools={"bash": False, "web": False, "edit": False,
                    "subagents": False, "mcp": []},
             org_visibility="self", charter="a deliberately narrow seat")
    org.set_scope(USER, "quiet", permission_mode="plan")
    r = org.insert_parent(USER, "quiet", "mid")
    said = " ".join(r["warnings"])
    true("GRANTS IT" in said, said)
    for needle in ("E:/w", "tool bash", "permission mode plan → bypassPermissions",
                   "visibility self → full"):
        true(needle in said, f"the disclosure never mentioned {needle}: {said}")
    eq(org.nodes["quiet"]["scope"]["permission_mode"], "bypassPermissions",
       "…and the grant itself really did happen")
    assert_sound(org, "after a scope-raising insertion")


@t("insertion above a DEEP descendant leaves every other branch alone")
def _():
    org = Org.create("deep-insert")
    org.hire(USER, None, "opus", 80, "top")
    org.hire("top", "top", "opus", 40, "mid", **spec())
    org.hire("mid", "mid", "opus", 20, "deep", **spec())
    org.hire("deep", "deep", "opus", 6, "leaf", **spec())
    org.hire("top", "top", "opus", 5, "aunt", **spec())
    f0 = frees(org)
    org.hire("top", "deep", "opus", 2, "ins", **spec())
    org.insert_parent("top", "ins", "deep")
    eq(org.nodes["ins"]["parent"], "mid")
    eq(org.nodes["deep"]["parent"], "ins")
    eq(org.nodes["leaf"]["parent"], "deep", "the target's branch came along")
    f1 = frees(org)
    eq(f1["top"], f0["top"])
    eq(f1["mid"], f0["mid"])
    eq(f1["aunt"], f0["aunt"])
    eq(f1["deep"], f0["deep"] - 7, "only the hire itself was ever charged")
    assert_sound(org, "after a deep insertion")


@t("insertion refuses cleanly: self, wrong parent, outside reach, bad type")
def _():
    org = insert_org()
    org.hire("mid", "mid", "opus", 1, "new", **spec())
    fp = fingerprint(org)
    expect_error(lambda: org.insert_parent("mid", "new", "new"), "above itself")
    # the inserted node must ALREADY be the target's own report — that is what
    # makes the credit rotation a closed transfer between the two of them
    expect_error(lambda: org.insert_parent("mid", "k1", "k2"),
                 "must already report")
    expect_error(lambda: org.insert_parent("k1", "new", "mid"), "authority")
    expect_error(lambda: org.check_placement("mid", "boss", "subordinate"),
                 "outside your subtree")
    expect_error(lambda: org.check_placement("mid", "mid", "sideways"),
                 "hire_type must be")
    expect_error(lambda: org.check_placement("mid", "ghost", "subordinate"),
                 "no such node")
    eq(fingerprint(org), fp, "every refusal left the tree untouched")


@t("a TOP-LEVEL target is the user's alone (§7.4), and the user may do it")
def _():
    org = Org.create("top-insert")
    org.hire(USER, None, "opus", 40, "t1")
    org.hire("t1", "t1", "opus", 8, "kid", **spec())
    expect_error(lambda: org.check_placement("t1", "t1", "superior"),
                 "only the user")
    org.hire(USER, "t1", "opus", 2, "ins", **spec())
    org.insert_parent(USER, "ins", "t1")
    eq(org.nodes["ins"]["parent"], None, "the user may seat at top level")
    eq(org.nodes["t1"]["parent"], "ins")
    eq(org.nodes["kid"]["parent"], "t1")
    assert_sound(org, "after a user top-level insertion")


@t("the depth cap counts the target's DEEPEST report, not the target")
def _():
    org = Org.create("depthcap")
    org.d["max_depth"] = 4
    org.hire(USER, None, "opus", 60, "a")            # depth 0
    org.hire("a", "a", "opus", 30, "b", **spec())    # 1
    org.hire("b", "b", "opus", 20, "c", **spec())    # 2
    org.hire("c", "c", "opus", 8, "d", **spec())     # 3 — the cap's edge
    org.hire("a", "a", "opus", 4, "e", **spec())     # 1, a shallow branch
    # b's deepest report is `d` at 3; inserting anywhere above it would seat
    # d at 4. The cap is measured on the DEEPEST report, never on the target
    for tgt in ("b", "c", "d"):
        expect_error(lambda t=tgt: org.check_placement("a", t, "superior"),
                     "max org depth")
    # …while a target whose own branch has room is fine
    org.check_placement("a", "e", "superior")


@t("insertion is refused for a lineage bearer or a stack owner (§8.5)")
def _():
    org = Org.create("bearer-insert")
    org.hire(USER, None, "haiku", 60, "root")
    org.hire("root", "root", "haiku", 30, "boss", **spec())
    org.hire("boss", "boss", "haiku", 10, "worker", **spec())
    org.compact_split("worker", "sess-2")
    org.rehire(USER, "worker@0", grant=2)
    fp = fingerprint(org)
    expect_error(lambda: org.check_placement("boss", "worker@0", "superior"),
                 "lineage bearer")
    expect_error(lambda: org.check_placement("boss", "worker", "superior"),
                 "live lineage bearer")
    eq(fingerprint(org), fp)


# =====================================================================  §6
print("\n§6  the HTTP surface")

CARDS = {c["name"]: c for c in TOOLS}


@t("both new tools are advertised, well-formed, and reach their dispatch")
def _():
    for name in ("orgtree_swap", "orgtree_self_subjugate"):
        c = CARDS[name]
        true(len(c["description"]) > 20, name)
        eq(c["inputSchema"]["type"], "object")
        for req in c["inputSchema"].get("required", []):
            true(req in c["inputSchema"]["properties"], (name, req))
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "orgtree", "api.py"), encoding="utf-8").read()
    for name in ("orgtree_swap", "orgtree_self_subjugate"):
        true(f'body.tool == "{name}"' in src, f"{name} has no dispatch branch")


@t("the hire card documents the destination pair, and move documents batching")
def _():
    hire = CARDS["orgtree_hire"]["inputSchema"]["properties"]
    eq(hire["hire_type"]["enum"], ["subordinate", "superior"])
    true("target" in hire and "target" in CARDS["orgtree_rehire"]["inputSchema"]["properties"])
    eq(CARDS["orgtree_rehire"]["inputSchema"]["properties"]["hire_type"]["enum"],
       ["subordinate", "superior"])
    true("moves" in CARDS["orgtree_move"]["inputSchema"]["properties"])
    # the batch form must not have made the single form mandatory-or-broken
    eq(CARDS["orgtree_move"]["inputSchema"].get("required", []), [])


ORG = store.create_org("zz seat topology")
SLUG = ORG.d["slug"]
ORG.hire(USER, None, "opus", 60, "boss")
ORG.hire("boss", "boss", "opus", 30, "mid", **spec())
ORG.hire("mid", "mid", "opus", 4, "kid", **spec())
store.save_org(ORG)
_DRIVEN: list[str] = []
supervisor.send_message = (                       # never spawn a real turn
    lambda slug, nid, text, **kw: (_DRIVEN.append(nid), {})[1])

CLIENT = TestClient(api.app)


def call_ok(node, tool, args=None):
    r = CLIENT.post("/api/agent", json={"org": SLUG, "node": node,
                                        "tool": tool, "args": args or {}})
    assert r.status_code == 200, f"{tool} → {r.status_code}: {r.text[:300]}"
    return r.json()


def call_err(node, tool, args=None):
    r = CLIENT.post("/api/agent", json={"org": SLUG, "node": node,
                                        "tool": tool, "args": args or {}})
    assert r.status_code != 200, f"{tool} should have been refused: {r.text[:200]}"
    assert r.status_code != 500, f"{tool} → 500: {r.text[:300]}"
    return r.text


@t("hire with hire_type=superior inserts the new agent above the caller")
def _():
    _DRIVEN.clear()
    r = call_ok("mid", "orgtree_hire",
                {"tier": "opus", "name": "successor", "grant": 3,
                 "charter": "take over the seat", "hire_type": "superior",
                 "effort": "high", "kickoff": "you have the seat now"})
    eq(r["inserted_above"], "mid")
    eq(r["reports_to"], "boss")
    org = store.load_org(SLUG)
    eq(org.nodes["successor"]["parent"], "boss")
    eq(org.nodes["mid"]["parent"], "successor")
    eq(org.nodes["kid"]["parent"], "mid", "the caller kept its own report")
    eq(org.nodes["successor"]["scope"]["permission_mode"], "acceptEdits",
       "the requested configuration was applied")
    true("successor" in _DRIVEN,
         "the kickoff still started it — and only after the save")
    assert_sound(org, "after an API insertion")


@t("…and the ordinary destinations still behave exactly as before")
def _():
    org = store.load_org(SLUG)
    before = {k: v["parent"] for k, v in org.nodes.items()}
    r = call_ok("mid", "orgtree_hire",
                {"tier": "haiku", "name": "plain", "grant": 0,
                 "charter": "an ordinary report", "tools": dict(ALL_TOOLS),
                 "add_dirs": [], "org_visibility": "team"})
    org = store.load_org(SLUG)
    eq(org.nodes["plain"]["parent"], "mid", "omitted destination = under me")
    call_ok("mid", "orgtree_hire",
            {"tier": "haiku", "name": "deeper", "grant": 0, "parent": "kid",
             "charter": "placed by the old spelling", "tools": dict(ALL_TOOLS),
             "add_dirs": [], "org_visibility": "team"})
    org = store.load_org(SLUG)
    eq(org.nodes["deeper"]["parent"], "kid", "`parent` still works (D-224)")
    call_ok("mid", "orgtree_hire",
            {"tier": "haiku", "name": "targeted", "grant": 0, "target": "kid",
             "charter": "placed by target", "tools": dict(ALL_TOOLS),
             "add_dirs": [], "org_visibility": "team"})
    org = store.load_org(SLUG)
    eq(org.nodes["targeted"]["parent"], "kid")
    eq(before["kid"], "mid", "the fixture is what this test thought it was")
    assert_sound(org, "after ordinary hires")


@t("a destination outside the caller's subtree is refused, creating nothing")
def _():
    org = store.load_org(SLUG)
    fp = fingerprint(org)
    txt = call_err("mid", "orgtree_hire",
                   {"tier": "haiku", "name": "nope", "grant": 0,
                    "target": "boss", "charter": "c", "tools": dict(ALL_TOOLS),
                    "add_dirs": [], "org_visibility": "team"})
    true("outside your subtree" in txt, txt)
    eq(fingerprint(store.load_org(SLUG)), fp, "a refused hire left no seat")


@t("☞ a refusal AFTER the seat exists rolls the whole call back")
def _():
    org = store.load_org(SLUG)
    fp = fingerprint(org)
    # the hire itself is legal; the audience target is not — D-160's
    # all-or-nothing must discard the seat AND the insertion with it
    txt = call_err("mid", "orgtree_hire",
                   {"tier": "haiku", "name": "doomed", "grant": 0,
                    "charter": "c", "hire_type": "superior",
                    "audiences": ["no-such-agent"]})
    true("ears" in txt or "audience" in txt.lower(), txt)
    after = store.load_org(SLUG)
    true("doomed" not in after.nodes, "the seat survived a refused call")
    eq(fingerprint(after), fp, "…and nothing else moved either")
    assert_sound(after, "after a rolled-back API hire")


@t("rehire restores into a chosen destination, and above it on request")
def _():
    org = store.load_org(SLUG)
    org.hire("mid", "mid", "haiku", 0, "retiree", **spec())
    org.retire("mid", "retiree")
    store.save_org(org)
    call_ok("mid", "orgtree_rehire", {"node": "retiree", "target": "kid"})
    org = store.load_org(SLUG)
    eq(org.nodes["retiree"]["parent"], "kid", "restored at the destination")
    eq(org.nodes["retiree"]["state"], "live")
    org.retire("mid", "retiree")
    store.save_org(org)
    r = call_ok("mid", "orgtree_rehire",
                {"node": "retiree", "target": "kid", "hire_type": "superior"})
    org = store.load_org(SLUG)
    eq(r["inserted_above"], "kid")
    eq(org.nodes["retiree"]["parent"], "mid", "it took kid's position")
    eq(org.nodes["kid"]["parent"], "retiree")
    assert_sound(org, "after a rehire insertion")


@t("the seat exchange and the batch move work over the same surface")
def _():
    org = store.load_org(SLUG)
    org.hire(USER, "boss", "opus", 6, "x1", **spec())
    org.hire(USER, "boss", "opus", 4, "x2", **spec())
    store.save_org(org)
    call_ok("boss", "orgtree_swap", {"a": "x1", "b": "x2"})
    org = store.load_org(SLUG)
    eq(org.nodes["x1"]["grant"], 4, "grants rode the seats")
    eq(org.nodes["x2"]["grant"], 6)
    call_ok("boss", "orgtree_move",
            {"moves": [{"node": "x1", "new_parent": "x2"},
                       {"node": "x2", "new_parent": "boss"}]})
    org = store.load_org(SLUG)
    eq(org.nodes["x1"]["parent"], "x2")
    txt = call_err("boss", "orgtree_move",
                   {"moves": [{"node": "x1", "new_parent": "boss"},
                              {"node": "x2", "new_parent": "ghost"}]})
    true("nothing was applied" in txt, txt)
    org = store.load_org(SLUG)
    eq(org.nodes["x1"]["parent"], "x2", "the rolled-back batch stayed rolled")
    assert_sound(org, "after API topology verbs")


@t("☞ REGRESSION: a junk `moves` ELEMENT is a 422 with a reason, never a 500")
def _():
    """`isinstance(moves, list)` was checked and the elements were not, so
    ["abc"] / [5] / [True] reached `.get` on a str and 500ed the gateway an
    agent is holding a tool result open on (redteam 2026-09-02). An LLM
    writes the bare-string form readily."""
    for junk in (["abc"], [5], [["a", "b"]], [True], [None]):
        txt = call_err("boss", "orgtree_move", {"moves": junk})
        true("must be an object" in txt, f"{junk!r} → {txt[:160]}")
    # …and an element with no `new_parent` no longer silently means the top
    # level, which is a promotion only the user may make
    txt = call_err("boss", "orgtree_move", {"moves": [{"node": "mid"}]})
    true("no `new_parent`" in txt, txt)


@t("☞ REGRESSION: a superior-mode hire never CLAIMS a mode it then replaced")
def _():
    """`applied: ["permission_mode"]` beside a warning saying that mode was
    overwritten is a response contradicting itself, and `applied` is the
    machine-readable half (redteam 2026-09-02). The insertion's disclosure is
    the honest version."""
    org = store.load_org(SLUG)
    org.hire(USER, "boss", "opus", 12, "host",
             add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="full",
             charter="the seat that will be inserted above")
    org.set_scope(USER, "host", permission_mode="bypassPermissions")
    store.save_org(org)
    # asking for a scope the seat cannot honour is refused AT THE DOOR…
    txt = call_err("host", "orgtree_hire",
                   {"tier": "haiku", "name": "quietone", "grant": 0,
                    "charter": "asked to be a narrow seat",
                    "tools": {"bash": False, "web": False, "edit": False,
                              "subagents": False, "mcp": []},
                    "add_dirs": [], "org_visibility": "self",
                    "permission_mode": "plan", "hire_type": "superior"})
    for f in ("add_dirs", "tools", "org_visibility", "permission_mode"):
        true(f in txt, f"the refusal must name {f}: {txt[:200]}")
    true("quietone" not in store.load_org(SLUG).nodes, "…creating nothing")
    # …and omitting them is ENOUGH, even though an agent hire normally has no
    # defaults: the seat's own scope is what the destination already decided
    r = call_ok("host", "orgtree_hire",
                {"tier": "haiku", "name": "quietone", "grant": 0,
                 "charter": "the replacement", "hire_type": "superior"})
    true("permission_mode" not in (r.get("applied") or []),
         f"the response claims a mode it never applied: {r.get('applied')}")
    org = store.load_org(SLUG)
    eq(org.nodes["quietone"]["scope"]["permission_mode"], "bypassPermissions",
       "the seat's mode, taken from the target")
    eq(org.nodes["quietone"]["parent"], "boss")
    eq(org.nodes["host"]["parent"], "quietone")
    assert_sound(org, "after a superior-mode hire")


@t("self-subjugation over the surface reaches only the caller's own subtree")
def _():
    org = store.load_org(SLUG)
    fp = fingerprint(org)
    txt = call_err("mid", "orgtree_self_subjugate", {"target": "boss"})
    true("not a live descendant" in txt, txt)
    eq(fingerprint(store.load_org(SLUG)), fp)
    kid_was, mid_was = org.nodes["kid"]["parent"], org.nodes["mid"]["parent"]
    r = call_ok("mid", "orgtree_self_subjugate", {"target": "kid"})
    true("next_step" in r and "retire" in r["next_step"])
    org = store.load_org(SLUG)
    eq(org.nodes["mid"]["parent"], kid_was, "each took the other's slot")
    eq(org.nodes["kid"]["parent"], mid_was)
    assert_sound(org, "after an API self-subjugation")


@t("HTTP top-level self-handoff works, but plain swap and upward/cross-chain requests do not")
def _():
    org = store.create_org("top-level handoff API")
    org.hire(USER, None, "opus", 40, "leader")
    org.hire(USER, "leader", "opus", 10, "replacement")
    org.hire(USER, None, "opus", 5, "other")
    store.save_org(org)
    slug = org.d["slug"]
    before = fingerprint(org)
    for actor, tool, args in (
        ("leader", "orgtree_swap", {"a": "leader", "b": "replacement", "_self_subjugation": True}),
        ("replacement", "orgtree_self_subjugate", {"target": "leader"}),
        ("leader", "orgtree_self_subjugate", {"target": "other"}),
        ("leader", "orgtree_self_subjugate", {"target": "leader"}),
    ):
        r = CLIENT.post("/api/agent", json={"org": slug, "node": actor,
            "tool": tool, "args": args})
        eq(r.status_code, 422, (actor, tool, r.text))
        eq(fingerprint(store.load_org(slug)), before, "refusal mutated the tree")
    r = CLIENT.post("/api/agent", json={"org": slug, "node": "leader",
        "tool": "orgtree_self_subjugate", "args": {"target": "replacement"}})
    eq(r.status_code, 200, r.text)
    org = store.load_org(slug)
    eq(org.nodes["replacement"]["parent"], None)
    eq(org.nodes["leader"]["parent"], "replacement")
    eq(org.nodes["other"]["parent"], None)
    assert_sound(org, "HTTP top-level voluntary handoff")


print(f"\nALL {PASS} CHECKS PASS")
