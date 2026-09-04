"""Ledger AUTHORITY suite — adversarial tests for the ledger as the org's
authority: credit ceilings, conservation, the clamping hierarchy, §7.2
addressing, and the edge shapes (names, sentinels, cycles, caps).

Companion to `test_ledger.py`, which replays the ratified scenarios. This one
tries to BREAK them. Same shape: a plain runnable script, `ok N` lines, an
`ALL N CHECKS PASS` tail, no pytest.

    .venv/Scripts/python.exe backend/tests/test_ledger_authority.py
    .venv/Scripts/python.exe backend/tests/test_ledger_authority.py --discriminate

The second form is the honesty check: it puts each ledger fix BACK (in a temp
copy of the package, never in the repo) and re-runs the section that is meant
to catch it. Every one must go RED — otherwise a green suite proves nothing.

Sections
  §1  the `_move` top-level grant escape — the defect the docket carried unfixed
  §2  the grant-ceiling census: every path that mutates a grant, + a drift guard
  §2b atomicity: a refused op must leave the tree byte-identical
  §3a lineage bearers that own subtrees
  §3b conservation: randomized trees × randomized op sequences
  §4  the clamping hierarchy: child ⊆ parent, and the kiosk ceiling
  §5  addressing (§7.2): the refusals, not just the permissions
  §6  edge shapes: names, the @-sentinel namespace, cycles, structural caps

Checks marked `⚑ KNOWN DEFECT` assert the CURRENT (wrong) behaviour on purpose,
so the suite stays green while the defect stands and goes RED the moment
someone fixes it without updating the ledger of known defects. Each one names
what the right behaviour would be.
"""

import json
import os
import random
import re
import sys
import threading
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-authority-")

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: net._default_address
# falls back to net.DEFAULT_HUB_ADDRESS — the operator's real hub — when this
# root has no defaults.json, and any rig that starts the net daemon then
# registers its fixture orgs there permanently. Measured twice (user report
# 2026-08-06; ~45 fixture orgs again on 2026-08-10). The discard port refuses
# instantly, so registration fails harmlessly into the backoff.
# Guarded over this whole directory by test_external_mail §1.
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')


from orgtree import openrouter as orr                       # noqa: E402
from orgtree.ledger import (CREDIT_PLACES, EXTERN, MODELS,  # noqa: E402
                            PM_LEVELS, SYSTEM, TIERS, TOOL_KEYS, USER,
                            VIS_LEVELS, LedgerError, Org, slugify)

PASS = 0
DEFECTS = []          # (id, one-line description) — printed in the tail

ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}
LEDGER_SRC = os.path.join(os.path.dirname(__file__), "..", "orgtree", "ledger.py")
# Keep this structural property walk stable when conditional rollout metadata
# is staged in TIERS. Its random operation sequence is seed-regression data;
# changing the choice cardinality consumes a different number of random bits
# and silently turns every historical seed into a new test. Conditional tier
# admission is covered at the provider gate, and its ordinary ledger seat is
# covered deterministically in test_codex_model_inventory.py.
PROPERTY_TIERS = [tier for tier in TIERS if tier != "astra"]


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


def defect(did, label, fn, why):
    """A check that PINS a defect that is still live. Green while broken."""
    DEFECTS.append((did, why))
    check(f"⚑ {did} {label}", fn)


def eq(got, want, msg=""):
    if got != want:
        raise AssertionError(f"{msg} expected {want!r}, got {got!r}")


def true(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "expected true")


def expect_error(fn, needle=""):
    try:
        fn()
    except LedgerError as e:
        assert needle.lower() in str(e).lower(), f"wrong error: {e}"
        return
    raise AssertionError("expected LedgerError, got success")


def expect_ok(fn):
    """The spec says CLAMP, not refuse — so a LedgerError here is the failure."""
    try:
        return fn()
    except LedgerError as e:
        raise AssertionError(f"expected success (clamp), got a refusal: {e}")


def frees(org):
    return {k: org.free(k) for k in org.nodes}


def grants(org):
    return {k: org.nodes[k]["grant"] for k in org.nodes}


def holdings(org, nid):
    return org.seat_cost(nid) + org.nodes[nid]["grant"]


# ------------------------------------------------------------------ builders
def two_roots(cap=40, ga=39, gb=20, gx=30):
    """Two top-level agents; a child under the first, holding seat+grant.
    Moving the child across the root boundary is the §1 scenario."""
    org = Org.create("movecap")
    org.d["max_top_grant"] = cap
    org.hire(USER, None, "haiku", ga, "a")
    org.hire(USER, None, "haiku", gb, "b")
    org.hire("a", "a", "haiku", gx, "x", **spec())
    return org


def deep_org():
    """A 3-branch, 4-deep org used for addressing and clamping."""
    org = Org.create("deep", dirs=["E:/w", "E:/r"])
    org.hire(USER, None, "opus", 400, "top")
    org.hire(USER, None, "opus", 100, "other")       # a second root
    org.hire("top", "top", "opus", 150, "mid-a", **spec())
    org.hire("top", "top", "opus", 120, "mid-b", **spec())
    org.hire("mid-a", "mid-a", "haiku", 40, "leaf-a1", **spec())
    org.hire("mid-a", "mid-a", "haiku", 40, "leaf-a2", **spec())
    org.hire("mid-b", "mid-b", "haiku", 40, "leaf-b1", **spec())
    org.hire("leaf-a1", "leaf-a1", "haiku", 5, "deep-a", **spec())
    org.hire("other", "other", "haiku", 10, "leaf-o1", **spec())
    return org


# =====================================================================  §1
def section_move_ceiling():
    print("§1 the `_move` top-level grant escape:")

    # The docket's "Carried, not done" row, reproduced exactly.
    org = two_roots()
    before = grants(org)
    eq(before["a"], 39); eq(before["b"], 20)
    eq(holdings(org, "x"), 31, "the moved holding")
    check("a cross-root move may not inflate a top-level grant past the cap",
          lambda: expect_error(lambda: org.move(USER, "x", "b"),
                               "top-level grant cap"))
    check("the refused move is ATOMIC — no grant moved, no re-parent",
          lambda: (eq(grants(org), before, "grants after a refused move"),
                   eq(org.nodes["x"]["parent"], "a", "parent after refusal"))[-1])

    # …and the same end state, asked for directly, has always been refused.
    # That asymmetry IS the defect: one route enforced the cap, the other did
    # not, so a drag in the UI reached a number the credit bar refuses to type.
    org2 = two_roots()
    check("the identical end state via reallocate is refused too (consistency)",
          lambda: expect_error(lambda: org2.reallocate(USER, "b", 31),
                               "top-level grant cap"))

    # The fix must not over-refuse.
    ok = two_roots(cap=60)
    ok_before = frees(ok)
    check("a cross-root move that stays under the cap still succeeds",
          lambda: (ok.move(USER, "x", "b"),
                   eq(ok.nodes["x"]["parent"], "b"),
                   eq(ok.nodes["a"]["grant"], 8),
                   eq(ok.nodes["b"]["grant"], 51))[-1])
    check("that move was budget-neutral: every node's free is unchanged",
          lambda: eq(frees(ok), ok_before, "frees across a move"))
    check("…and top_level_holds is conserved across a cross-root move",
          lambda: eq(ok.audit()["top_level_holds"], (1 + 39) + (1 + 20),
                     "the two roots' holdings, redistributed but conserved"))

    exact = two_roots(cap=51)
    check("a move landing EXACTLY on the cap succeeds (cap is inclusive)",
          lambda: (exact.move(USER, "x", "b"),
                   eq(exact.nodes["b"]["grant"], 51))[-1])

    uncapped = two_roots(cap=0)
    check("cap 0 = uncapped: the same move is permitted",
          lambda: (uncapped.move(USER, "x", "b"),
                   eq(uncapped.nodes["b"]["grant"], 51))[-1])

    # An archived node moves for free (c == 0) — nothing inflates, so nothing
    # may be refused. Regression guard against a fix that checks unconditionally.
    arch = two_roots(cap=40)
    arch.retire(USER, "x")
    check("an ARCHIVED node still moves across roots (c==0, no inflation)",
          lambda: (arch.move(USER, "x", "b"),
                   eq(arch.nodes["x"]["parent"], "b"),
                   eq(arch.nodes["b"]["grant"], 20))[-1])

    # Within one root the LCA is a real node, so no top-level grant moves.
    intra = Org.create("intra")
    intra.d["max_top_grant"] = 40
    intra.hire(USER, None, "haiku", 39, "t")
    intra.hire("t", "t", "haiku", 18, "p", **spec())
    intra.hire("t", "t", "haiku", 0, "q", **spec())
    intra.hire("p", "p", "haiku", 15, "kid", **spec())
    check("an intra-root move never touches a top-level grant",
          lambda: (intra.move(USER, "kid", "q"),
                   eq(intra.nodes["t"]["grant"], 39),
                   eq(intra.nodes["kid"]["parent"], "q"))[-1])

    # Promotion to top level was already guarded (D-014); pin it so the §1 fix
    # is not mistaken for the whole rule.
    prom = Org.create("prom")
    prom.d["max_top_grant"] = 40
    prom.hire(USER, None, "haiku", 30, "root")
    prom.hire("root", "root", "haiku", 25, "big", **spec())
    prom.d["max_top_grant"] = 20          # tightened after the fact
    check("promotion to top level was already capped (D-014)",
          lambda: expect_error(lambda: prom.move(USER, "big", None),
                               "top-level grant cap"))


# =====================================================================  §2
# ⚠ EVERY SITE HERE NOW GOES THROUGH ledger._q (fractional seats, user ruling
# 2026-09-03). That changed the TEXT of nearly every write but not one of the
# guards: _q only snaps a value onto the 0.01 grid, so a write that could not
# exceed the cap before still cannot, and a write that was pre-checked is
# pre-checked on the same value. The audit question each entry answers is
# unchanged; only the fragment matching it moved.
GRANT_WRITE_SITES = {
    # line-fragment → the guard that must stand between it and the cap
    'hop_n["grant"] = _q(hop_n["grant"] + c)':
        "_chain_acquire — D-014 pre-check above it",
    '["grant"] = _q(self.nodes[k]["grant"] + remaining)':
        "_chain_acquire — D-014 pre-check",
    'n["grant"] = grant': "rehire — _check_top_grant when grant rises at top",
    'n["grant"] = _q(n["grant"] - delta)':
        "switch_model downgrade — _check_top_grant (delta < 0 here, so this "
        "is the melt: it RAISES the grant, which is why it is pre-checked)",
    'n["grant"] = _q(n["grant"] - own)':
        "switch_model upgrade — grant only shrinks",
    'n["grant"] = _q(n["grant"] + delta)':
        "reallocate — _check_top_grant on +Δ at top",
    'self.nodes[hop]["grant"] = _q(self.nodes[hop]["grant"] - c)':
        "_move release — only shrinks",
    'self.nodes[hop]["grant"] = _q(self.nodes[hop]["grant"] + c)':
        "_move acquire — §1 fix",
    'n["grant"] = 0': "reseed of a lost bearer — zeroes, never raises",
    # FR-22 rescind: SUBTRACTION only, min-clamped at free(parent) so it can
    # neither exceed max_top_grant (it never raises) nor push free negative
    'p["grant"] = _q(p["grant"] - clawed)':
        "rescind claw-back — only shrinks, clamped at free",
    # D-224 seat exchange: the two grants TRADE. Neither is a new number —
    # each already sat on a live seat — so the multiset of grants in the org
    # is unchanged, and a top seat receives a grant that was legal on the
    # other seat. The cap is a per-node ceiling, and the one node that can
    # be at top level here holds a value that already passed it… EXCEPT
    # across the top boundary, which is why swap_seats refuses outright
    # unless the actor is the user (§7.4) — the user is the only actor who
    # may reseat top level, exactly as in promote().
    'n_b["grant"] = g_b, g_a':
        "swap_seats — grants trade between two existing seats",
    # D-224 insertion: target hands back exactly the stake it committed to
    # the inserted node (a SHRINK, never a raise) …
    'n_t["grant"] = _q(g_t - stake_n)':
        "insert_parent — target's grant only shrinks",
    # … and the inserted node absorbs that stake plus its own. At top level
    # the VALUE changes (seat costs differ), so insert_parent calls
    # _check_top_grant on exactly that branch before it writes anything.
    'n_new["grant"] = _q(seat_t + n_t["grant"] + g_n)':
        "insert_parent — _check_top_grant when the target is top-level",
}


def section_ceiling_census():
    print("§2 the grant-ceiling census (every mutating path):")

    # A drift guard, in the spirit of the message-visibility suite: if a new
    # grant-mutating site appears, this check fails and whoever added it has to
    # answer the cap question rather than inherit §1 all over again.
    def tail(s):
        """Normalize a write site to `<op> <rhs>`, comment and owner stripped."""
        return s.split('["grant"]', 1)[1].split("#")[0].strip()

    src = open(LEDGER_SRC, encoding="utf-8").read()
    found = {tail(m) for m in
             re.findall(r'\["grant"\] *(?:\+=|-=|=) *[^=\n]+', src)}
    known = {tail(k) for k in GRANT_WRITE_SITES}

    def drift():
        true(not (found - known),
             "NEW grant-mutating site(s) in ledger.py — each one has to answer "
             f"'can this exceed max_top_grant?' (§1 was exactly this hole): "
             f"{sorted(found - known)}")
        true(not (known - found),
             f"audited write site(s) that no longer exist: {sorted(known - found)}")
    check(f"drift guard: all {len(found)} grant-mutating sites in ledger.py "
          f"are audited", drift)

    # Every route to a bigger top-level grant refuses at the same wall.
    def fresh(cap=40, grant=39):
        o = Org.create("cap")
        o.d["max_top_grant"] = cap
        o.hire(USER, None, "haiku", grant, "t")
        return o

    o = fresh()
    check("① hire at top level past the cap → refused", lambda: expect_error(
        lambda: o.hire(USER, None, "haiku", 41, "big"), "top-level grant cap"))
    check("② reallocate +Δ past the cap → refused", lambda: expect_error(
        lambda: o.reallocate(USER, "t", 2), "top-level grant cap"))

    o3 = fresh(grant=39)
    o3.retire(USER, "t")
    check("③ rehire with a bigger grant past the cap → refused",
          lambda: expect_error(lambda: o3.rehire(USER, "t", grant=41),
                               "top-level grant cap"))
    check("③b rehire at the SAME grant is grandfathered even if over cap",
          lambda: (o3.d.__setitem__("max_top_grant", 10),
                   o3.rehire(USER, "t"),
                   eq(o3.nodes["t"]["grant"], 39))[-1])

    o4 = fresh(cap=40, grant=38)
    check("④ switch_model downgrade melting past the cap → refused",
          lambda: (o4.switch_model(USER, "t", "opus"),   # seat 1→5, own free pays
                   o4.reallocate(USER, "t", 2),          # grant 34 → 36
                   o4.d.__setitem__("max_top_grant", 37),
                   expect_error(lambda: o4.switch_model(USER, "t", "haiku"),
                                "top-level grant cap"))[-1])

    o5 = fresh(cap=40, grant=39)
    o5.hire("t", "t", "haiku", 38, "mid", **spec())      # t free → 0
    check("⑤ a USER-pool cascade may not inflate a top-level grant",
          lambda: expect_error(
              lambda: o5.hire(USER, "mid", "haiku", 40, "deep",
                              **spec()), "top-level grant cap"))

    o6 = fresh(cap=40, grant=30)
    o6.request_credits("t", 41, "need more")
    rid = o6.d["credit_requests"][0]["id"]
    check("⑥ approving a credit request past the cap → refused",
          lambda: expect_error(lambda: o6.credit_request_action(rid, "approve"),
                               "top-level grant cap"))

    o7 = two_roots(cap=40)
    check("⑦ move (the §1 route) refuses at the same wall",
          lambda: expect_error(lambda: o7.move(USER, "x", "b"),
                               "top-level grant cap"))

    o8 = Org.create("promcap")
    o8.d["max_top_grant"] = 40
    o8.hire(USER, None, "haiku", 30, "r")
    o8.hire("r", "r", "haiku", 25, "p", **spec())
    o8.d["max_top_grant"] = 20
    check("⑧ promote to top level refuses at the same wall",
          lambda: expect_error(lambda: o8.promote(USER, "p", None),
                               "top-level grant cap"))

    # dissolve/retire only ever RELEASE — assert they cannot inflate, ever.
    o9 = two_roots(cap=40)
    g9 = grants(o9)
    check("⑨ retire/dissolve never raise a grant (they free by uncommitting)",
          lambda: (o9.dissolve(USER, "a"),
                   eq(grants(o9), g9, "grants across a dissolve"))[-1])
    check("⑨b …and the freed credits show up as the parent's free, not a grant",
          lambda: (eq(o9.nodes["a"]["state"], "archived"),
                   eq(o9.nodes["x"]["state"], "archived"))[-1])


# =====================================================================  §3
OPS = ("hire", "retire", "rehire", "dissolve", "realloc", "switch",
       "move", "reorder", "compact", "unrecoverable", "reseed", "delete",
       "request_credits", "scope",
       # D-224's topology verbs. They are here rather than in their own
       # suite for the reason §3b exists at all: each one is budget-neutral
       # BY CONSTRUCTION, and the way that claim dies is a shape the author
       # did not think of (a lineage bearer with a subtree, an archived
       # parent, a cascade that inflated a grant on the way past). The
       # randomized walk builds those; a hand-written fixture builds what
       # its author already suspected.
       "swap", "subjugate", "batch", "insert")


def build_random(rnd, cap):
    org = Org.create(f"prop-{rnd.randrange(10 ** 8)}", dirs=["E:/w", "E:/r"])
    org.d["max_top_grant"] = cap
    org.d["cascade_hire"] = rnd.random() < 0.7
    org.d["cascade_alloc"] = rnd.random() < 0.7
    hi = min(cap, 200) if cap else 200
    for i in range(rnd.randint(1, 3)):
        org.hire(USER, None, rnd.choice(PROPERTY_TIERS), rnd.randint(0, hi),
                 f"root{i}")
    return org


def pick_lineaged(rnd, org, pool):
    """Bias the taking ops toward nodes that CARRY a lineage stack.

    Uniform sampling found the `delete` half of the bearer-subtree defect at
    seed 57 and never found the `dissolve` half in 400×40 ops — the shape needs
    compact_split → rehire the bearer → hire under it → take the SUCCESSOR, and
    a uniform walk almost never lines those four up. Biasing here makes the
    property test able to find the class, not just the one instance it happened
    to hit. (Measured: with the bias, a dissolve-only regression is caught.)"""
    lin = [k for k in pool if org.lineage_stack(k)]
    return rnd.choice(lin) if lin and rnd.random() < 0.7 else rnd.choice(pool)


def random_op(rnd, org):
    """Apply one random op. Returns (op, applied?, who) — LedgerError = a
    refusal, which is a legitimate outcome; anything else is a crash and
    re-raises. `who` names the nodes a D-224 topology verb touched, so the
    caller can hold it to the exact neutrality its docstring claims."""
    live = [k for k, v in org.nodes.items() if v["state"] == "live"]
    arch = [k for k, v in org.nodes.items() if v["state"] == "archived"]
    allk = list(org.nodes)
    if not allk:
        return ("none", False, ())
    op = rnd.choice(OPS)
    who: tuple[str, ...] = ()
    try:
        if op == "hire":
            parent = rnd.choice(live + [None]) if live else None
            actor = USER if parent is None else rnd.choice([USER, parent])
            kw = {} if actor == USER else spec()
            org.hire(actor, parent, rnd.choice(PROPERTY_TIERS), rnd.randint(0, 30),
                     f"n{rnd.randrange(10 ** 6)}", **kw)
        elif op == "retire" and live:
            org.retire(USER, rnd.choice(live))
        elif op == "rehire" and arch:
            org.rehire(USER, rnd.choice(arch))
        elif op == "dissolve" and live:
            org.dissolve(USER, pick_lineaged(rnd, org, live))
        elif op == "realloc" and live:
            org.reallocate(USER, rnd.choice(live), rnd.randint(-20, 20))
        elif op == "switch" and live:
            org.switch_model(USER, rnd.choice(live), rnd.choice(PROPERTY_TIERS))
        elif op == "move" and len(live) >= 2:
            n = rnd.choice(live)
            tgt = rnd.choice(live + [None])
            org.move(USER, n, tgt)
        elif op == "reorder" and len(allk) >= 2:
            n, s = rnd.sample(allk, 2)
            org.reorder(USER, n, after=s)
        elif op == "compact" and live:
            org.compact_split(rnd.choice(live), f"s{rnd.randrange(10 ** 6)}")
        elif op == "unrecoverable" and live:
            org.mark_unrecoverable(rnd.choice(live), "test")
        elif op == "reseed" and allk:
            org.reseed(USER, rnd.choice(allk), f"s{rnd.randrange(10 ** 6)}")
        elif op == "delete" and allk:
            org.delete(USER, pick_lineaged(rnd, org, allk))
        elif op == "request_credits" and live:
            org.request_credits(rnd.choice(live), rnd.randint(0, 60), "why")
        elif op == "scope" and live:
            org.set_scope(USER, rnd.choice(live),
                          org_visibility=rnd.choice(list(VIS_LEVELS)))
        elif op == "swap" and len(live) >= 2:
            a, b = rnd.sample(live, 2)
            who = (a, b, org.nodes[a]["parent"], org.nodes[b]["parent"])
            org.swap_seats(USER, a, b)
        elif op == "subjugate" and live:
            n = rnd.choice(live)
            kids = org.descendants(n)
            if not kids:
                return (op, False, ())
            tgt = rnd.choice(kids)
            who = (n, tgt, org.nodes[n]["parent"], org.nodes[tgt]["parent"])
            org.subjugate(USER, n, tgt)
        elif op == "batch" and len(live) >= 2:
            org.move_batch(USER, [(rnd.choice(live),
                                   rnd.choice(live + [None]))
                                  for _ in range(rnd.randint(1, 3))])
        elif op == "insert" and live:
            # the shape the API composes: an ordinary hire, then the seat
            # rotation. Only the rotation runs here — it is the half that
            # claims to move no credits whatsoever.
            tgt = rnd.choice(live)
            kids = org.children(tgt)
            if not kids:
                return (op, False, ())
            org.insert_parent(USER, rnd.choice(kids), tgt)
        else:
            return (op, False, ())
    except LedgerError:
        return (op, False, ())
    return (op, True, tuple(x for x in who if x))


def assert_sound(org, cap, where):
    for k, n in org.nodes.items():
        g = n["grant"]
        # ⚠ this used to demand `isinstance(g, int)`, and that assertion is
        # STALE, not violated: credits became fractional by user ruling
        # 2026-09-03, so a grant of 7.2 is correct arithmetic. The check went
        # on passing only because the pool this suite draws from
        # (`list(TIERS)`) was all-whole; on main today, switching a node onto
        # a $0.20 OpenRouter favorite already yields a float grant, and the
        # sub-$1 repricing merely put such a tier INTO `TIERS` where this
        # property test would finally pick it. Replaced with the invariant
        # that actually holds and is stronger for the fractional world: a
        # real, non-bool number sitting EXACTLY on the 0.01 grid. That is
        # what `ledger._q` maintains and what makes `free() >= 0` exact
        # rather than approximate — a residue here is the drift the grid
        # exists to prevent, and an int can no longer catch it.
        true(isinstance(g, (int, float)) and not isinstance(g, bool),
             f"{where}: {k} grant is {type(g).__name__} {g!r}, not a number")
        true(g == round(g, CREDIT_PLACES),
             f"{where}: {k} grant {g!r} is off the 0.01 credit grid")
        true(g >= 0, f"{where}: {k} grant went negative ({g})")
        # …and the shadow derivation is quantised for the same reason the
        # ledger quantises its own (`ledger._q`, CREDIT_PLACES): with
        # fractional seats a naive left-to-right float sum carries a residue
        # the ledger deliberately does not — seed 5 produced a `derived` of
        # 1.2000000000000028 against a correct `free()` of 1.2. Rounding both
        # onto the 0.01 grid keeps the real content of the check (any actual
        # accounting error is orders of magnitude larger) without asserting
        # that float addition is associative.
        derived = round(g - sum(org.seat_cost(c) + org.nodes[c]["grant"]
                                for c in org.children(k)), CREDIT_PLACES)
        eq(org.free(k), derived, f"{where}: free({k}) not derivable")
        if n["state"] == "live":
            true(org.free(k) >= 0, f"{where}: {k} overdrafted (free {org.free(k)})")
        if n["parent"] is None and cap:
            true(g <= cap,
                 f"{where}: top-level {k} grant {g} > max_top_grant {cap}")
        p = n["parent"]
        true(p is None or p in org.nodes, f"{where}: {k} has a dangling parent {p!r}")
        if n["state"] == "live" and p is not None:
            # rehire calls this "an invalid tree state" in so many words — and
            # it is also an accounting hole: children() excludes archived
            # parents from every committed() sum, so a live node under one
            # holds a seat nobody is charged for
            true(org.nodes[p]["state"] != "archived",
                 f"{where}: live {k} hangs under ARCHIVED {p}")
        seen, cur = set(), k
        while cur is not None:
            true(cur not in seen, f"{where}: parent CYCLE through {k}")
            seen.add(cur)
            cur = org.nodes[cur]["parent"]
    true(org.audit()["no_overdraft"], f"{where}: audit {org.audit()['problems']}")


def fingerprint(org):
    """The structural core a REFUSED op must leave byte-identical."""
    return {k: (v["parent"], v["grant"], v["state"], v["model"],
                repr(v["scope"])) for k, v in org.nodes.items()}


def section_atomicity():
    print("§2b atomicity — a refused op must leave the tree untouched:")

    def payer_org():
        o = Org.create("atom")
        o.hire(USER, None, "opus", 100, "top")
        o.hire(USER, "top", "opus", 10, "mid")    # mid free 10; a hire cascades
        o.set_scope(USER, "mid", org_visibility="team")
        return o

    # `_chain_acquire` is the first thing `hire` mutates; two checks used to sit
    # AFTER it, so a refused hire left the cascade's inflation behind
    o = payer_org()
    fp = fingerprint(o)
    check("a hire refused by the strict visibility clamp moves no credits",
          lambda: (expect_error(
              lambda: o.hire("top", "mid", "opus", 40, "kid",
                             **spec(org_visibility="full")), "visibility"),
              eq(fingerprint(o), fp, "state after a refused hire"))[-1])

    o2 = payer_org()
    fp2 = fingerprint(o2)
    hold2 = o2.audit()["top_level_holds"]
    check("a hire refused for an unsluggable name materializes no credits "
          "from the user's pool",
          lambda: (expect_error(
              lambda: o2.hire(USER, "mid", "opus", 900, "   "), "name"),
              eq(fingerprint(o2), fp2, "state after a refused hire"),
              eq(o2.audit()["top_level_holds"], hold2,
                 "top_level_holds went 105 → 915 before the fix"))[-1])

    o3 = payer_org()
    fp3 = fingerprint(o3)
    check("…and the other refusal routes are equally clean",
          lambda: (expect_error(lambda: o3.hire(USER, "mid", "zzz", 1, "x"),
                                "unknown tier"),
                   expect_error(lambda: o3.hire(USER, "mid", "opus", -1, "x"),
                                "non-negative"),
                   expect_error(lambda: o3.hire("mid", "mid", "opus", 1, "x"),
                                "no defaults"),
                   eq(fingerprint(o3), fp3))[-1])

    # rehire validated the TIER NAME only after rehiring the archived chain
    o4 = Org.create("rat")
    o4.hire(USER, None, "opus", 200, "top")
    o4.hire(USER, "top", "opus", 60, "mid")
    o4.hire(USER, "mid", "haiku", 5, "leaf")
    o4.dissolve(USER, "mid")
    fp4 = fingerprint(o4)
    check("a rehire refused for an unknown tier does not wake the archived "
          "chain on its way out",
          lambda: (expect_error(lambda: o4.rehire(USER, "leaf", tier="gpt-9"),
                                "unknown tier"),
                   eq(o4.nodes["mid"]["state"], "archived"),
                   eq(fingerprint(o4), fp4))[-1])

    # set_scope applied its fields one at a time
    o5 = Org.create("ss", dirs=["E:/w", "E:/r"])
    o5.hire(USER, None, "opus", 100, "p")
    o5.set_scope(USER, "p", add_dirs=[{"path": "E:/w", "mode": "rw"}],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": True, "mcp": []})
    o5.hire(USER, "p", "haiku", 5, "c")
    o5.set_scope(USER, "c", add_dirs=[])
    fp5 = fingerprint(o5)
    # ⚠ D-106 moved the referent of "illegal": a grant is no longer measured
    # against the TARGET'S PARENT (an intermediate that lacks it is now raised
    # instead of the grant refused) but against the GRANTER'S OWN cap. So this
    # atomicity check — still exactly as valuable — needs an actor who is
    # actually capped. `p` holds E:/w and lacks `web`; the USER is capped by
    # nothing, so the old form is now a legal cascade, pinned two checks down.
    check("a set_scope with a legal add_dirs and an illegal tools grant "
          "applies NEITHER",
          lambda: (expect_error(
              lambda: o5.set_scope("p", "c",
                                   add_dirs=[{"path": "E:/w", "mode": "rw"}],
                                   tools=dict(ALL_TOOLS)), "does not hold"),
              eq(fingerprint(o5), fp5, "half-applied retool"))[-1])
    check("…in either field order, and for visibility/effort too",
          lambda: (expect_error(
              lambda: o5.set_scope(USER, "c", org_visibility="full",
                                   add_dirs=[{"path": "E:/w", "mode": "rw"}],
                                   effort="turbo"), "effort"),
              eq(fingerprint(o5), fp5))[-1])

    # the ceiling must not rise on a call that then refuses
    kb = kiosk_org({"tools": {"bash": True, "web": False, "edit": False,
                              "subagents": False, "mcp": []},
                    "add_dirs": [], "org_visibility": "self",
                    "permission_mode": "default"})
    kb.hire(USER, None, "haiku", 5, "n1")
    kb.hire(USER, "n1", "haiku", 1, "n2")
    ceil_before = repr(kb.kiosk_ceiling())
    check("raise_ceiling on a call that then refuses does not grow the ceiling",
          lambda: (expect_error(
              lambda: kb.set_scope(USER, "n1", org_visibility="full",
                                   tools=dict(ALL_TOOLS), effort="nope",
                                   raise_ceiling=True), "effort"),
              eq(repr(kb.kiosk_ceiling()), ceil_before,
                 "the ceiling rose on a refused call"))[-1])


def bearer_org():
    """A lineage bearer that was REHIRED and then hired under.

    A superior-rehired bearer keeps the OLD parent slot (it is the successor's
    sibling, not its descendant), so its own subtree is reachable from neither
    `descendants(successor)` nor `lineage_stack(successor)` — which is what the
    two defects below both turned on."""
    o = Org.create("bearer")
    o.hire(USER, None, "haiku", 100, "boss")
    o.hire("boss", "boss", "haiku", 40, "worker", **spec())
    o.compact_split("worker", "sess2")            # → knowledge bearer worker@0
    o.rehire(USER, "worker@0", grant=10)          # the SUPERIOR rehires it
    o.hire(USER, "worker@0", "haiku", 5, "helper")
    return o


def section_bearer_subtrees():
    print("§3a lineage bearers with subtrees (found by the §3b property test):")
    o = bearer_org()
    eq(o.nodes["helper"]["parent"], "worker@0", "the setup")
    eq(o.nodes["worker@0"]["parent"], "boss", "the bearer is the successor's sibling")

    # delete used to remove the bearer and leave `helper` pointing at a node
    # that no longer exists — ancestors() then raised KeyError, not LedgerError
    check("delete(successor) takes the bearer's whole subtree — no dangling parent",
          lambda: (o.delete(USER, "worker"),
                   eq(sorted(o.nodes), ["boss"], "survivors"),
                   true(all(v["parent"] is None or v["parent"] in o.nodes
                            for v in o.nodes.values())))[-1])

    o2 = bearer_org()
    check("…and no surviving node's ancestors() raises anything but LedgerError",
          lambda: (o2.delete(USER, "worker"),
                   [o2.ancestors(k) for k in o2.nodes])[-1] is not None)

    o3 = bearer_org()
    free_boss = o3.free("boss")
    eq(free_boss, 48, "boss free before")
    r = o3.dissolve(USER, "worker")
    check("dissolve(successor) archives the bearer's subtree too",
          lambda: eq(o3.nodes["helper"]["state"], "archived",
                     "helper was stranded LIVE under an archived bearer"))
    check("…and the freed total accounts for it (52 → 58: helper's seat+grant)",
          lambda: eq(r["freed"], 58, "freed"))
    check("…so no live node ever hangs under an archived parent",
          lambda: true(all(v["parent"] is None
                           or o3.nodes[v["parent"]]["state"] != "archived"
                           for v in o3.nodes.values() if v["state"] == "live")))

    o4 = bearer_org()
    check("retire of the bearer itself already handled it (retire→dissolve)",
          lambda: (lambda rr: (eq(o4.nodes["helper"]["state"], "archived"),
                               true("retire became dissolve" in rr["warnings"][-1]))
                   )(o4.retire(USER, "worker@0"))[-1])


def section_conservation():
    print("§3b conservation (randomized trees × op sequences):")
    seeds = range(1200)
    ops_run = applied = moves = refusals = 0
    swaps = inserts = batches = 0
    # `rehire` is the one op that legitimately half-applies: rehiring a node
    # under an archived superior rehires that superior FIRST, as its own
    # complete op, and a later refusal (the chain cannot afford the seat)
    # leaves it live. That is a valid tree either way — see D-E in the report.
    partial_ok = {"rehire"}
    for s in seeds:
        rnd = random.Random(s)
        cap = rnd.choice([0, 40, 120, 500])
        org = build_random(rnd, cap)
        assert_sound(org, cap, f"seed {s} build")
        for step in range(40):
            before_f = frees(org)
            before_hold = org.audit()["top_level_holds"]
            before_parents = {k: v["parent"] for k, v in org.nodes.items()}
            before_fp = fingerprint(org)
            op, applied_now, who = random_op(rnd, org)
            ops_run += 1
            applied += bool(applied_now)
            if not applied_now and op not in partial_ok:
                # a REFUSAL must be a no-op: nothing created, nothing moved,
                # no credits inflated by a cascade that then hit a validator
                refusals += 1
                eq(fingerprint(org), before_fp,
                   f"seed {s} step {step}: refused {op} still changed the tree")
            assert_sound(org, cap, f"seed {s} step {step} after {op}")
            if op in ("move", "batch", "insert") and applied_now:
                moves += 1
                batches += op == "batch"
                inserts += op == "insert"
                after_f = frees(org)
                # _move's docstring: release and acquire cancel hop by hop, so
                # every node's free is unchanged — budget-neutral by construction.
                # A BATCH is a sequence of those, and D-224's insertion rotates
                # credits between exactly two nodes in amounts that cancel — so
                # all three make the identical claim, and are held to it.
                for k in set(before_f) & set(after_f):
                    eq(after_f[k], before_f[k],
                       f"seed {s} step {step}: {op} changed free({k})")
                eq(org.audit()["top_level_holds"], before_hold,
                   f"seed {s} step {step}: {op} changed top_level_holds")
                _ = before_parents
            if op in ("swap", "subjugate") and applied_now:
                swaps += 1
                after_f = frees(org)
                # A seat exchange trades the two grants along with the seats.
                # Same tier on both sides ⇒ nothing moves anywhere. Different
                # tiers ⇒ the seat-cost difference appears, and may appear
                # ONLY at the two agents and their two former superiors.
                touched = {k for k in set(before_f) & set(after_f)
                           if after_f[k] != before_f[k]}
                true(touched <= set(who),
                     f"seed {s} step {step}: {op} moved credits at "
                     f"{sorted(touched - set(who))}, outside the pair "
                     f"{sorted(who)}")
                if org.seat_cost(who[0]) == org.seat_cost(who[1]):
                    eq(org.audit()["top_level_holds"], before_hold,
                       f"seed {s} step {step}: a same-tier {op} changed "
                       f"top_level_holds")
    print(f"      … {len(seeds)} random orgs · {ops_run} ops attempted · "
          f"{applied} applied · {moves} moves/batches/insertions verified "
          f"budget-neutral ({batches} batches, {inserts} insertions) · "
          f"{swaps} seat exchanges verified contained · "
          f"{refusals} refusals verified no-op")
    check("randomized sequences: no overdraft, no negative or float grant, "
          "free always derivable, cap never exceeded", lambda: None)
    check("randomized sequences: every REFUSED op left the tree byte-identical",
          lambda: true(refusals > 1000, f"only {refusals} refusals exercised"))
    check("randomized sequences: every applied move was budget-neutral "
          "(every node's free unchanged, top_level_holds conserved)",
          lambda: true(moves > 50, f"only {moves} moves exercised"))
    check("randomized sequences: D-224's batches and insertions are "
          "budget-neutral too, and its seat exchanges move credits at "
          "nothing but the pair and their two former superiors",
          lambda: true(swaps > 50 and inserts > 20 and batches > 20,
                       f"thin coverage: {swaps} exchanges, {inserts} "
                       f"insertions, {batches} batches"))


# =====================================================================  §4
def kiosk_org(ceiling=None, dirs=("E:/w", "E:/r")):
    org = Org.create("kiosk-org", dirs=list(dirs))
    org.d["kiosk"] = {"enabled": True, "token": "t", "credits": 500}
    org = Org(org.d)              # re-run __init__ so the ceiling mints
    if ceiling is not None:
        org.set_kiosk_ceiling(ceiling)
    return org


def tools_within(child, parent):
    """child ⊆ parent for a tool grant. parent None = the user (everything)."""
    if parent is None:
        return True
    for k in TOOL_KEYS:
        if child.get(k) and not parent.get(k, True):
            return False
    ph = parent.get("mcp") or []
    ch = child.get("mcp") or []
    if "*" in ph:
        return True
    if "*" in ch:
        return False
    return set(ch) <= set(ph)


def dirs_within(child, parent):
    if parent is None:
        return True
    pm = {d["path"]: d["mode"] for d in parent}
    for d in child:
        held = pm.get(d["path"])
        if held is None or (held == "ro" and d["mode"] == "rw"):
            return False
    return True


def vis_within(child, parent):
    if parent is None:
        return True
    return VIS_LEVELS.index(child) <= VIS_LEVELS.index(parent)


def assert_clamped(org, where):
    """Two containments, everywhere, always: child ⊆ parent, and (in a kiosk)
    everything ⊆ the ceiling."""
    ceil = org.kiosk_ceiling()
    for k, n in org.nodes.items():
        sc = n["scope"]
        p = n["parent"]
        psc = None if p is None else org.nodes[p]["scope"]
        true(tools_within(sc["tools"], None if psc is None else psc["tools"]),
             f"{where}: {k} tools exceed parent {p}: {sc['tools']} vs "
             f"{psc and psc['tools']}")
        true(dirs_within(sc["add_dirs"], None if psc is None else psc["add_dirs"]),
             f"{where}: {k} dirs exceed parent {p}")
        true(vis_within(sc.get("org_visibility", "full"),
                        None if psc is None else psc.get("org_visibility", "full")),
             f"{where}: {k} visibility exceeds parent {p}")
        if ceil is not None:
            true(tools_within(sc["tools"], ceil["tools"]),
                 f"{where}: {k} tools exceed the kiosk ceiling: {sc['tools']}")
            true(dirs_within(sc["add_dirs"], ceil.get("add_dirs", [])),
                 f"{where}: {k} dirs exceed the kiosk ceiling: {sc['add_dirs']}")
            true(vis_within(sc.get("org_visibility", "full"),
                            ceil.get("org_visibility", "full")),
                 f"{where}: {k} visibility exceeds the kiosk ceiling")
            true(PM_LEVELS.index(sc.get("permission_mode", "default"))
                 <= PM_LEVELS.index(ceil.get("permission_mode", "acceptEdits")),
                 f"{where}: {k} permission_mode exceeds the kiosk ceiling: "
                 f"{sc.get('permission_mode')}")


def section_clamping():
    print("§4 the clamping hierarchy (child ⊆ parent ⊆ ceiling):")

    # --- child ⊆ parent, on every route in
    o = Org.create("clamp", dirs=["E:/w", "E:/r"])
    o.hire(USER, None, "opus", 200, "boss")
    o.set_scope(USER, "boss", add_dirs=[{"path": "E:/w", "mode": "ro"}],
                tools={"bash": True, "web": False, "edit": True,
                       "subagents": True, "mcp": ["alpha"]},
                org_visibility="subtree")
    check("a USER hire asking above the parent is CLAMPED, with a warning",
          lambda: (lambda r: (
              true(any("clamped" in w for w in r["warnings"]), r["warnings"]),
              eq(o.nodes[r["node"]]["scope"]["tools"]["web"], False,
                 "web the parent lacks"),
              eq(o.nodes[r["node"]]["scope"]["org_visibility"], "subtree"))[-1]
          )(o.hire(USER, "boss", "haiku", 5, "kid",
                   add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="full")))
    check("an AGENT hire asking above the parent is REFUSED (strict), not clamped",
          lambda: expect_error(
              lambda: o.hire("boss", "boss", "haiku", 5, "kid2",
                             **spec(tools={**ALL_TOOLS, "web": True})),
              "does not hold"))
    check("a read-only holding cannot beget read/write (№30)",
          lambda: expect_error(
              lambda: o.hire("boss", "boss", "haiku", 5, "kid3",
                             **spec(add_dirs=[{"path": "E:/w", "mode": "rw"}])),
              "read-only"))
    check("a dir the parent does not hold at all is refused",
          lambda: expect_error(
              lambda: o.hire("boss", "boss", "haiku", 5, "kid4",
                             **spec(add_dirs=[{"path": "E:/r", "mode": "ro"}])),
              "does not hold"))
    check("visibility above the parent is refused for an agent-explicit grant",
          lambda: expect_error(
              lambda: o.hire("boss", "boss", "haiku", 5, "kid5",
                             **spec(tools={"bash": True, "web": False,
                                           "edit": True, "subagents": True,
                                           "mcp": []},
                                    org_visibility="full")), "visibility"))
    check("everything is contained after those hires", lambda: assert_clamped(o, "§4 hires"))

    # --- shrinking the PARENT sweeps the subtree
    o2 = Org.create("sweep", dirs=["E:/w", "E:/r"])
    o2.hire(USER, None, "opus", 300, "boss")
    o2.hire(USER, "boss", "opus", 100, "mid")
    o2.hire(USER, "mid", "haiku", 10, "leaf")
    eq(o2.nodes["leaf"]["scope"]["tools"]["bash"], True, "setup")
    check("set_scope on an ancestor sweeps the WHOLE subtree, not just the child",
          lambda: (o2.set_scope(USER, "boss",
                                tools={"bash": False, "web": True, "edit": True,
                                       "subagents": True, "mcp": []}),
                   eq(o2.nodes["mid"]["scope"]["tools"]["bash"], False),
                   eq(o2.nodes["leaf"]["scope"]["tools"]["bash"], False),
                   assert_clamped(o2, "after an ancestor shrink"))[-1])
    check("…and dropping a dir on an ancestor drops it all the way down",
          lambda: (o2.set_scope(USER, "boss", add_dirs=[{"path": "E:/w", "mode": "ro"}]),
                   eq([d["path"] for d in o2.nodes["leaf"]["scope"]["add_dirs"]],
                      ["E:/w"]),
                   eq(o2.nodes["leaf"]["scope"]["add_dirs"][0]["mode"], "ro",
                      "rw downgraded to ro down the chain"))[-1])
    check("…and visibility shrinks down the chain too (D-021)",
          lambda: (o2.set_scope(USER, "boss", org_visibility="team"),
                   eq(o2.nodes["leaf"]["scope"]["org_visibility"], "team"))[-1])

    # the subtree sweep used to collapse a child's mcp "*" to a concrete list
    # without a word — the same semantic change `_apply_ceiling` names out loud
    os_ = Org.create("star", dirs=["E:/w"])
    os_.hire(USER, None, "opus", 100, "p")
    os_.set_scope(USER, "p", tools=dict(ALL_TOOLS, mcp=["*"]))
    os_.hire(USER, "p", "haiku", 5, "c")
    os_.set_scope(USER, "c", tools=dict(ALL_TOOLS, mcp=["*"]))
    eq(os_.nodes["c"]["scope"]["tools"]["mcp"], ["*"], "setup")
    check("narrowing a parent's MCP list materializes the child's '*' AND "
          "says so", lambda: (lambda rr: (
              eq(os_.nodes["c"]["scope"]["tools"]["mcp"], ["alpha"]),
              true(any("mcp:*" in w for w in rr["warnings"]), rr["warnings"]))[-1]
          )(os_.set_scope(USER, "p", tools=dict(ALL_TOOLS, mcp=["alpha"]))))

    # ✓ D-C FIXED (user ruling 2026-08-07, D-102). This was a `defect()` pin —
    # "permission_mode is the one scope field with no parent clamp… Fix = the
    # `_clamp_vis` treatment, i.e. a capability ruling like D-021 — not this
    # session's call to make." The ruling arrived: agents adjust their
    # subordinates' mode, capped at their own. These are now positive checks.
    opm = Org.create("pm")
    opm.hire(USER, None, "opus", 100, "mgr")
    opm.hire(USER, "mgr", "haiku", 5, "rep")
    opm.set_scope(USER, "mgr", permission_mode="default")
    check("an AGENT cannot raise a report above its own permission_mode",
          lambda: expect_error(
              lambda: opm.set_scope("mgr", "rep",
                                    permission_mode="bypassPermissions"),
              "exceeds the parent"))
    # …while the USER may grant it — and since D-106 the grant BUBBLES: every
    # node between the granter and the grantee receives what it was missing,
    # rather than the grant being refused or the chain left non-monotone.
    # ⚠ This supersedes half of D-101. That entry said "raising one agent is
    # one act"; it still holds for the ORG DEFAULT (never retroactive), but a
    # per-node raise now moves the managers above it too, up to the granter.
    check("the USER may raise a node — and the chain between rises with it "
          "(D-106 supersedes D-101's one-act raise)",
          lambda: (opm.set_scope(USER, "rep",
                                 permission_mode="bypassPermissions"),
                   eq(opm.nodes["rep"]["scope"]["permission_mode"],
                      "bypassPermissions"),
                   eq(opm.nodes["mgr"]["scope"]["permission_mode"],
                      "bypassPermissions"))[-1])
    # a REAL gap to bubble across: lower the manager first (which sweeps the
    # report down with it), then grant the report back up
    opm.set_scope(USER, "mgr", org_visibility="self")
    check("☞ the bubble is REPORTED, never silent — it expands an agent "
          "nobody asked about",
          lambda: (lambda r: (
              eq(opm.nodes["mgr"]["scope"]["org_visibility"], "full"),
              true(any("bubbled up to mgr" in w for w in r["warnings"]),
                   r["warnings"]))[-1]
          )(opm.set_scope(USER, "rep", org_visibility="full")))
    # ⚠ the same-value guard: the ⚙ sends EVERY field on every save, so a
    # charter edit carries an unchanged permission_mode. Re-asserting a mode
    # must not revoke a grant made below it.
    # (the cascade above left mgr at bypassPermissions too, so re-sending THAT
    # is the same-value write — the state is asserted, not assumed, because
    # this check is worthless if it silently becomes a lowering)
    check("re-asserting an unchanged mode does NOT sweep the subtree",
          lambda: (eq(opm.nodes["mgr"]["scope"]["permission_mode"],
                      "bypassPermissions", "fixture"),
                   opm.set_scope(USER, "mgr",
                                 permission_mode="bypassPermissions"),
                   eq(opm.nodes["rep"]["scope"]["permission_mode"],
                      "bypassPermissions",
                      "a same-value write revoked a deliberate grant"))[-1])
    check("nor does an unrelated capability retool on the ancestor",
          lambda: (opm.set_scope(USER, "mgr", org_visibility="self"),
                   eq(opm.nodes["rep"]["scope"]["permission_mode"],
                      "bypassPermissions",
                      "a visibility retool revoked a deliberate grant"))[-1])
    # …but a genuine LOWERING does reach it — that is what revoking means
    check("☞ lowering the ancestor's OWN mode sweeps the subtree with it",
          lambda: (opm.set_scope(USER, "mgr", permission_mode="acceptEdits"),
                   opm.set_scope(USER, "rep",
                                 permission_mode="bypassPermissions"),
                   opm.set_scope(USER, "mgr", permission_mode="default"),
                   eq(opm.nodes["rep"]["scope"]["permission_mode"], "default"))[-1])

    # --- D-105: self-edit is exactly one field wide
    # "agents should be able to self-edit their own team charter, but not
    # their individual charter" (user, 2026-08-07). The two wear similar
    # names and are opposite objects: `charter` is what the SUPERIOR wrote
    # into this agent's own prompt; `team_charter` is what this agent writes
    # into its REPORTS' prompts.
    sc_ = Org.create("selfedit")
    sc_.hire(USER, None, "opus", 100, "mgr")
    sc_.hire(USER, "mgr", "haiku", 5, "rep")
    sc_.set_scope(USER, "mgr", charter="the role the user set")
    check("an agent may set its OWN team charter",
          lambda: (sc_.set_scope("mgr", "mgr", team_charter="ship small"),
                   eq(sc_.nodes["mgr"]["team_charter"], "ship small"))[-1])
    check("☠ …but NOT its own charter",
          lambda: expect_error(
              lambda: sc_.set_scope("mgr", "mgr", charter="I decide my role"),
              "may not rewrite your OWN charter"))
    check("☠ …and the refusal leaves the charter exactly as it was",
          lambda: eq(sc_.nodes["mgr"]["charter"], "the role the user set"))
    # the interesting attack is not `charter` — it is smuggling a capability
    # through on the same call that carries a legal team_charter
    check("☠ a self-retool carrying team_charter AND a capability is refused "
          "whole",
          lambda: expect_error(
              lambda: sc_.set_scope("mgr", "mgr", team_charter="fine",
                                    permission_mode="bypassPermissions"),
              "team_charter and nothing else"))
    check("☠ …changing neither (atomic refusal, not a partial apply)",
          lambda: (eq(sc_.nodes["mgr"]["team_charter"], "ship small"),
                   eq(sc_.nodes["mgr"]["scope"]["permission_mode"],
                      "acceptEdits"))[-1])
    check("a self-retool with nothing to set is refused, not a silent no-op",
          lambda: expect_error(lambda: sc_.set_scope("mgr", "mgr"),
                               "team_charter only"))
    # ☞ THE BACK DOOR THIS RULING DEPENDS ON BEING SHUT. If a node's own team
    # charter reached its own prompt, "you may not write your own charter"
    # would be words: it could write itself instructions through the team
    # charter instead. identity_prompt walks `ancestors`, which starts at the
    # PARENT — so it cannot. Asserted, because the ban rests on it.
    def _own_team_charter_is_not_self_direction():
        from orgtree import supervisor
        sc_.set_scope("mgr", "mgr", team_charter="ZZ-SENTINEL-TEAM")
        own = supervisor.identity_prompt(sc_, "mgr")
        rep = supervisor.identity_prompt(sc_, "rep")
        true("ZZ-SENTINEL-TEAM" in rep,
             "the team charter never reached the report it is meant to bind")
        true("Standing charter from your superior" not in own
             or "ZZ-SENTINEL-TEAM" not in own.split("Standing charter")[1],
             "a node's OWN team charter is injected as an instruction TO "
             "itself — self-direction through the back door")
    check("☞ a self-set team charter binds the REPORTS, never the author",
          _own_team_charter_is_not_self_direction)
    # and the user's clarification: subtree authority is unchanged
    check("a superior still sets a REPORT's individual charter",
          lambda: (sc_.set_scope("mgr", "rep", charter="do the thing"),
                   eq(sc_.nodes["rep"]["charter"], "do the thing"))[-1])
    check("☠ …and a peer/outsider still cannot touch either charter",
          lambda: expect_error(
              lambda: sc_.set_scope("rep", "mgr", team_charter="nope"),
              "authority is downward only"))

    # --- moving into a stricter branch clamps
    o3 = Org.create("movesweep", dirs=["E:/w", "E:/r"])
    o3.hire(USER, None, "opus", 300, "loose")
    o3.hire(USER, None, "opus", 300, "tight")
    o3.set_scope(USER, "tight", tools={"bash": False, "web": False, "edit": True,
                                       "subagents": True, "mcp": []},
                 add_dirs=[{"path": "E:/w", "mode": "ro"}], org_visibility="team")
    o3.hire(USER, "loose", "haiku", 20, "trav")
    o3.hire(USER, "trav", "haiku", 5, "trav-kid")
    check("moving a subtree into a stricter branch clamps it AND its children",
          lambda: (o3.move(USER, "trav", "tight"),
                   eq(o3.nodes["trav"]["scope"]["tools"]["bash"], False),
                   eq(o3.nodes["trav-kid"]["scope"]["tools"]["bash"], False),
                   eq(o3.nodes["trav-kid"]["scope"]["add_dirs"][0]["mode"], "ro"),
                   assert_clamped(o3, "after a move into a stricter branch"))[-1])
    check("moving back OUT does not restore what was clamped away (capabilities "
          "are lost, not borrowed)",
          lambda: (o3.move(USER, "trav", "loose"),
                   eq(o3.nodes["trav"]["scope"]["tools"]["bash"], False))[-1])

    # --- the kiosk ceiling CLAMPS, it does not refuse (ceiling spec §2)
    k = kiosk_org({"tools": {"bash": False, "web": True, "edit": True,
                             "subagents": True, "mcp": ["alpha"]},
                   "add_dirs": [{"path": "E:/w", "mode": "ro"}],
                   "org_visibility": "team",
                   "permission_mode": "default"})
    r = expect_ok(lambda: k.hire(USER, None, "haiku", 5, "vis1",
                                 add_dirs=[{"path": "E:/w", "mode": "rw"}],
                                 tools=dict(ALL_TOOLS, mcp=["alpha", "beta"]),
                                 org_visibility="full"))
    check("a kiosk over-ceiling hire SUCCEEDS (clamped), it is never a refusal",
          lambda: true(any("ceiling" in w for w in r["warnings"]), r["warnings"]))
    check("…and the clamp actually clamped: the stored scope IS the ceiling",
          lambda: (eq(k.nodes["vis1"]["scope"]["tools"]["bash"], False),
                   eq(k.nodes["vis1"]["scope"]["tools"]["mcp"], ["alpha"]),
                   eq(k.nodes["vis1"]["scope"]["add_dirs"],
                      [{"path": "E:/w", "mode": "ro"}]),
                   eq(k.nodes["vis1"]["scope"]["org_visibility"], "team"),
                   eq(k.nodes["vis1"]["scope"]["permission_mode"], "default"))[-1])
    check("…and it offers the one-action bridge rather than a dead end",
          lambda: eq(r.get("bridge"), {"raise_ceiling": True}))
    check("set_scope above the ceiling also clamps, never refuses",
          lambda: (lambda rr: (
              true(any("ceiling" in w for w in rr["warnings"]), rr["warnings"]),
              eq(k.nodes["vis1"]["scope"]["permission_mode"], "default"))[-1]
          )(expect_ok(lambda: k.set_scope(USER, "vis1",
                                          permission_mode="bypassPermissions"))))
    check("set_hire_defaults above the ceiling clamps, never refuses",
          lambda: (lambda rr: eq(rr["default_tools"]["bash"], False)
                   )(expect_ok(lambda: k.set_hire_defaults(
                       default_tools=dict(ALL_TOOLS)))))
    check("a bare hire (no scope stated) still lands inside the ceiling",
          lambda: (k.hire(USER, None, "haiku", 5, "vis2"),
                   assert_clamped(k, "bare kiosk hire"))[-1])

    # --- the TIER cap is a refusal by design (user spec: "no fable agents at all")
    kt = kiosk_org({"tools": dict(ALL_TOOLS), "add_dirs": [],
                    "org_visibility": "full", "permission_mode": "acceptEdits",
                    "max_tier": "sonnet"})
    check("the kiosk TIER cap is a hard refusal (deliberately not a clamp)",
          lambda: expect_error(lambda: kt.hire(USER, None, "opus", 5, "big"),
                               "caps agent tier"))
    check("…for rehire too", lambda: (
        kt.hire(USER, None, "sonnet", 5, "s"),
        kt.d["kiosk"]["max_scope"].__setitem__("max_tier", "haiku"),
        kt.retire(USER, "s"),
        expect_error(lambda: kt.rehire(USER, "s"), "caps agent tier"))[-1])
    check("…and a rehire that DOWNGRADES under the cap is welcomed", lambda: (
        kt.rehire(USER, "s", tier="haiku"),
        eq(kt.nodes["s"]["state"], "live"))[-1])
    check("…and for switch_model", lambda: expect_error(
        lambda: kt.switch_model(USER, "s", "opus"), "caps agent tier"))
    # a GRANDFATHERED over-cap agent asked for the tier it already runs: the
    # cap check used to run before the idempotent-no-op return, so "change
    # nothing" was a hard refusal — against the ratified no-op rule
    kg = kiosk_org()
    kg.hire(USER, None, "opus", 20, "grand")          # hired before the cap
    kg.set_kiosk_ceiling({"tools": dict(ALL_TOOLS), "add_dirs": [],
                          "org_visibility": "full",
                          "permission_mode": "acceptEdits", "max_tier": "haiku"})
    check("switching a grandfathered agent to the tier it ALREADY runs is a "
          "no-op, not a tier-cap refusal",
          lambda: (lambda rr: (eq(rr["model"], "opus"),
                               true("nothing to do" in rr["warnings"][0], rr))[-1]
                   )(expect_ok(lambda: kg.switch_model(USER, "grand", "opus"))))
    check("…and an actor with no authority is told THAT, not the tier cap",
          lambda: (kg.hire(USER, None, "haiku", 5, "bystander"),
                   expect_error(lambda: kg.switch_model("bystander", "grand",
                                                        "opus"),
                                "subtree"))[-1])
    check("lowering max_tier names the ARCHIVED agents it strands, not just "
          "the live ones", lambda: (lambda kk: (
              kk.hire(USER, None, "opus", 20, "sleeper"),
              kk.retire(USER, "sleeper"),
              (lambda rr: true(
                  any("ARCHIVED" in w and "sleeper" in w for w in rr["warnings"]),
                  rr["warnings"]))(
                  kk.set_kiosk_ceiling({"tools": dict(ALL_TOOLS), "add_dirs": [],
                                        "org_visibility": "full",
                                        "permission_mode": "acceptEdits",
                                        "max_tier": "haiku"})))[-1]
          )(kiosk_org()))

    # --- lowering the ceiling SWEEPS (spec §5) rather than refusing
    kl = kiosk_org()
    kl.hire(USER, None, "haiku", 5, "a1")
    kl.hire(USER, "a1", "haiku", 1, "a2")
    res = kl.set_kiosk_ceiling({"tools": {"bash": False, "web": False,
                                          "edit": False, "subagents": False,
                                          "mcp": []},
                                "add_dirs": [], "org_visibility": "self",
                                "permission_mode": "default"})
    check("lowering the ceiling sweeps every existing node (spec §5)",
          lambda: (true(len(res["swept"]) >= 2, res["swept"]),
                   assert_clamped(kl, "after lowering the ceiling"))[-1])
    check("…including nodes that are ARCHIVED at the time",
          lambda: (lambda kk: (
              kk.hire(USER, None, "haiku", 5, "z"),
              kk.retire(USER, "z"),
              kk.set_kiosk_ceiling({"tools": {"bash": False, "web": False,
                                              "edit": False, "subagents": False,
                                              "mcp": []},
                                    "add_dirs": [], "org_visibility": "self",
                                    "permission_mode": "default"}),
              eq(kk.nodes["z"]["scope"]["tools"]["bash"], False))[-1]
          )(kiosk_org()))
    check("…and a node archived BEFORE a ceiling change re-enters within it",
          lambda: (lambda kk: (
              kk.hire(USER, None, "haiku", 5, "z"),
              kk.retire(USER, "z"),
              kk.d["kiosk"].__setitem__(
                  "max_scope", {"tools": {"bash": False, "web": False,
                                          "edit": False, "subagents": False,
                                          "mcp": []},
                                "add_dirs": [], "org_visibility": "self",
                                "permission_mode": "default"}),
              kk.rehire(USER, "z"),
              eq(kk.nodes["z"]["scope"]["tools"]["bash"], False,
                 "rehire re-clamps against the CURRENT ceiling"),
              assert_clamped(kk, "rehire under a lowered ceiling"))[-1]
          )(kiosk_org()))

    # --- no random sequence escapes either containment
    escapes = 0
    for s in range(400):
        rnd = random.Random(10_000 + s)
        kk = kiosk_org()
        if rnd.random() < 0.5:
            kk.set_kiosk_ceiling({
                "tools": {t: rnd.random() < 0.6 for t in TOOL_KEYS}
                | {"mcp": rnd.choice([["*"], ["alpha"], []])},
                "add_dirs": rnd.choice([[], [{"path": "E:/w", "mode": "ro"}],
                                        [{"path": "E:/w", "mode": "rw"}]]),
                "org_visibility": rnd.choice(list(VIS_LEVELS)),
                "permission_mode": rnd.choice(list(PM_LEVELS))})
        kk.hire(USER, None, "haiku", 50, "r0")
        for _ in range(25):
            live = [x for x, v in kk.nodes.items() if v["state"] == "live"]
            if not live:
                break
            try:
                pick = rnd.random()
                if pick < 0.35:
                    kk.hire(USER, rnd.choice(live + [None]), "haiku",
                            rnd.randint(0, 5), f"n{rnd.randrange(10 ** 6)}",
                            add_dirs=rnd.choice(
                                [None, [], [{"path": "E:/w", "mode": "rw"}],
                                 [{"path": "E:/r", "mode": "rw"}]]),
                            tools=rnd.choice([None, dict(ALL_TOOLS),
                                              dict(ALL_TOOLS, mcp=["*"])]),
                            org_visibility=rnd.choice([None] + list(VIS_LEVELS)))
                elif pick < 0.55:
                    kk.set_scope(USER, rnd.choice(live),
                                 tools=dict(ALL_TOOLS, mcp=["*"]),
                                 org_visibility=rnd.choice(list(VIS_LEVELS)),
                                 permission_mode=rnd.choice(list(PM_LEVELS)))
                elif pick < 0.70:
                    kk.move(USER, rnd.choice(live), rnd.choice(live + [None]))
                elif pick < 0.80:
                    kk.retire(USER, rnd.choice(live))
                elif pick < 0.90:
                    arch = [x for x, v in kk.nodes.items()
                            if v["state"] == "archived"]
                    if arch:
                        kk.rehire(USER, rnd.choice(arch))
                else:
                    kk.set_hire_defaults(default_tools=dict(ALL_TOOLS, mcp=["*"]),
                                         default_visibility="full")
            except LedgerError:
                continue
            assert_clamped(kk, f"kiosk seed {s}")
            escapes += 1
    print(f"      … 400 random kiosks · {escapes} ops, every one re-checked "
          f"against parent AND ceiling")
    check("no random sequence of legal ops escapes parent ⊇ child or the "
          "kiosk ceiling", lambda: true(escapes > 800, f"only {escapes} ops"))


# =====================================================================  §5
def may_mail(org, s, t):
    """§7.2 restated from the SENTENCE, not from the code: downward any depth,
    one hop up, siblings, held audiences. Everything else refused."""
    if t == USER:
        return org.node(s)["parent"] is None or org._has_audience(s, USER)
    if org._has_audience(s, t):
        return True
    st, tt = org.node(s), org.node(t)
    if t in org.descendants(s, live_only=False):
        return True
    if st["parent"] == t:
        return True
    return st["parent"] == tt["parent"]


def section_addressing():
    print("§5 addressing (§7.2) — the refusals, not just the permissions:")

    names = list(deep_org().nodes)
    pairs = tested = permitted = refused = 0
    bad = []
    for s in names:
        for t in names + [USER]:
            if s == t:
                continue
            pairs += 1
            org = deep_org()          # rebuilt: post_mail GRANTS audiences
            want = may_mail(org, s, t)
            try:
                org.post_mail(s, t, "body")
                got = True
            except LedgerError as e:
                got = False
                if "may not address" in str(e) or "escalate" in str(e):
                    pass
                else:
                    bad.append((s, t, str(e)))
            tested += 1
            permitted += bool(got)
            refused += (not got)
            if got != want:
                bad.append((s, t, f"ledger={got} spec={want}"))
    check(f"all {pairs} ordered (sender, recipient) pairs agree with §7.2 "
          f"({permitted} permitted, {refused} refused)",
          lambda: true(not bad, f"disagreements: {bad[:6]}"))

    org = deep_org()
    check("two hops up is refused, naming the routes", lambda: expect_error(
        lambda: org.post_mail("deep-a", "mid-a", "hi"),
        "reach down, one hop up, sideways"))
    check("a cousin is refused", lambda: expect_error(
        lambda: org.post_mail("leaf-a1", "leaf-b1", "hi"), "may not address"))
    check("an uncle is refused", lambda: expect_error(
        lambda: org.post_mail("deep-a", "mid-b", "hi"), "may not address"))
    check("a node in another root's tree is refused", lambda: expect_error(
        lambda: org.post_mail("leaf-a1", "leaf-o1", "hi"), "may not address"))
    check("a top-level agent may not reach into another root either",
          lambda: expect_error(lambda: org.post_mail("top", "leaf-o1", "hi"),
                               "may not address"))
    check("writing to the user from below the top is refused, naming escalation",
          lambda: expect_error(lambda: org.post_mail("mid-a", USER, "hi"),
                               "escalate to your superior"))
    check("a top-level agent may write to the user unbidden",
          lambda: eq(org.post_mail("top", USER, "hi")["delivered"], "user_inbox"))
    check("one hop up is permitted",
          lambda: eq(org.post_mail("mid-a", "top", "hi")["delivered"], "top"))
    check("siblings are permitted",
          lambda: eq(org.post_mail("leaf-a1", "leaf-a2", "hi")["delivered"],
                     "leaf-a2"))

    # deep reach mints exactly one reply channel and no more
    o2 = deep_org()
    r = o2.post_mail("top", "deep-a", "do the thing")
    check("deep reach grants the recipient a reply audience, and says so",
          lambda: (true(any("audience granted" in w for w in r["warnings"]),
                        r["warnings"]),
                   true(o2._has_audience("deep-a", "top")))[-1])
    check("…so the reply is now permitted",
          lambda: eq(o2.post_mail("deep-a", "top", "done")["delivered"], "top"))
    check("…but it opened NO other door (still cannot reach mid-a, two hops up)",
          lambda: expect_error(lambda: o2.post_mail("deep-a", "mid-a", "hi"),
                               "may not address"))
    check("a message to a DIRECT child grants no audience (it already had one)",
          lambda: (lambda rr: true(
              not any("audience granted" in w for w in rr["warnings"]), rr)
          )(deep_org().post_mail("mid-a", "leaf-a1", "hi")))

    # audience lifecycle
    o3 = deep_org()
    o3.audience_grant(USER, "deep-a", "other")
    check("a user-granted lateral audience lets an otherwise-refused pair talk",
          lambda: eq(o3.post_mail("deep-a", "other", "hi")["delivered"], "other"))
    check("…and revoking it closes the door again",
          lambda: (o3.audience_revoke(USER, "deep-a", "other"),
                   expect_error(lambda: o3.post_mail("deep-a", "other", "hi"),
                                "may not address"))[-1])
    o4 = deep_org()
    o4.audience_grant("mid-a", "deep-a", "mid-b")   # delegated: mid-a's peer
    check("a delegated audience survives while the delegator still commands "
          "the grantee",
          lambda: eq(o4.post_mail("deep-a", "mid-b", "hi")["delivered"], "mid-b"))
    check("…and is swept when the grantee leaves the delegator's subtree",
          lambda: (o4.move(USER, "leaf-a1", "mid-b"),
                   true(not o4._has_audience("deep-a", "mid-b"),
                        "audience should have been swept by the move"),
                   expect_error(lambda: o4.post_mail("deep-a", "mid-b", "hi"),
                                "may not address"))[-1])
    o5 = deep_org()
    o5.audience_grant(USER, "deep-a", USER)
    check("a USER audience is never swept by a re-parent (№11)",
          lambda: (o5.move(USER, "leaf-a1", "mid-b"),
                   true(o5._has_audience("deep-a", USER)),
                   eq(o5.post_mail("deep-a", USER, "hi")["delivered"],
                      "user_inbox"))[-1])

    # outside-world addressing
    o6 = deep_org()
    check("only a top-level agent (or an org-inbox holder) speaks outward",
          lambda: expect_error(lambda: o6.post_mail("mid-a", "@mcp:abc", "hi"),
                               "top-level"))
    check("a top-level agent may", lambda: eq(
        o6.post_mail("top", "@mcp:abc", "hi")["delivered"], "@mcp:abc"))
    check("an org-inbox audience holder may too", lambda: (
        o6.audience_grant("top", "mid-a", "extern"),
        eq(o6.post_mail("mid-a", "@org:elsewhere", "hi")["delivered"],
           "@org:elsewhere"))[-1])
    check("the org cannot address ITSELF as an outside party", lambda: expect_error(
        lambda: o6.post_mail("top", f"@org:{o6.d['slug']}", "hi"),
        "this organization itself"))
    check("the USER cannot be an outbound sender (only agents speak outward)",
          lambda: expect_error(lambda: o6.post_mail(USER, "@mcp:abc", "hi"),
                               "only agents"))
    ko = kiosk_org()
    ko.hire(USER, None, "haiku", 5, "sealed")
    check("a sealed kiosk has no contact with the outside world",
          lambda: expect_error(lambda: ko.post_mail("sealed", "@mcp:abc", "hi"),
                               "sealed kiosk"))
    check("…and inbound to a kiosk is dropped, not delivered",
          lambda: eq(ko.post_external_mail("@ext:abc", "hello"), []))
    check("…and an org-inbox audience cannot be granted in one either",
          lambda: expect_error(lambda: ko.audience_grant(USER, "sealed", "extern"),
                               "no org inbox"))

    o7 = deep_org()
    o7.mark_unrecoverable("leaf-a2", "died")
    check("an unrecoverable node cannot receive mail", lambda: expect_error(
        lambda: o7.post_mail("mid-a", "leaf-a2", "hi"), "unrecoverable"))
    check("…and is excluded from extern delivery even though it holds its seat",
          lambda: (lambda oo: (
              oo.mark_unrecoverable("other", "died"),
              eq(sorted(oo.post_external_mail("@ext:z", "hello")), ["top"]))[-1]
          )(deep_org()))
    o8 = deep_org()
    o8.retire(USER, "leaf-a2")
    # 88804ce (2026-08-30): the warning no longer says "queued" — that read as
    # a delivery someone had scheduled. It now states the condition ("NOTHING
    # WILL READ IT until somebody rehires") and promises no rehire; this
    # check asserted the old word and failed on every run since.
    check("an ARCHIVED node still receives mail, saved with a warning that "
          "promises no rehire",
          lambda: (lambda rr: (eq(rr["deferred"], True),
                               true(any("NOTHING WILL READ IT" in w
                                        for w in rr["warnings"])))[-1]
                   )(o8.post_mail("mid-a", "leaf-a2", "hi")))

    # the request state machine
    o9 = deep_org()
    check("an audience request must climb the sender's OWN chain",
          lambda: expect_error(
              lambda: o9.request_audience("deep-a", "mid-b", "why"),
              "climb your own chain"))
    check("asking for your DIRECT superior is a no-op with a pointer, not an error",
          lambda: (lambda rr: true(rr.get("already_reachable") is True, rr)
                   )(o9.request_audience("deep-a", "leaf-a1", "why")))
    rq = o9.request_audience("deep-a", "top", "need it")
    check("a request opens at the direct superior",
          lambda: eq(rq["currently_at"], "leaf-a1"))
    check("a duplicate ask reports progress instead of erroring",
          lambda: (lambda rr: true("already open" in rr["warnings"][0], rr)
                   )(o9.request_audience("deep-a", "top", "again")))
    check("the wrong actor cannot forward it", lambda: expect_error(
        lambda: o9.audience_forward("mid-b", "deep-a", "top"), "awaits"))
    check("forwarding walks exactly one hop", lambda: eq(
        o9.audience_forward("leaf-a1", "deep-a", "top")["currently_at"], "mid-a"))
    check("reaching the target delivers it and the request is resolved on grant",
          lambda: (eq(o9.audience_forward("mid-a", "deep-a", "top")["currently_at"],
                      "top"),
                   o9.audience_grant("top", "deep-a"),
                   eq([r for r in o9.d["audience_requests"]
                       if r["from"] == "deep-a"], []))[-1])
    check("denying a request that no longer exists is refused cleanly",
          lambda: expect_error(
              lambda: o9.audience_deny("top", "deep-a", "top"),
              "no open audience request"))

    # forwarding AS THE USER used to set currently_at="@user" and then throw
    # ("the user cannot mail the user"), leaving the request stuck forever
    o10 = deep_org()
    o10.request_audience("deep-a", "top", "need it")
    check("the USER forwarding a request hands it straight to the target",
          lambda: (lambda rr: (eq(rr["currently_at"], "top"),
                               eq(rr["drive"], ["top"]))[-1]
                   )(expect_ok(lambda: o10.audience_forward(USER, "deep-a", "top"))))
    check("…and the request is never left stranded at the @user sentinel",
          lambda: true(all(r["currently_at"] != USER
                           for r in o10.d["audience_requests"]),
                       o10.d["audience_requests"]))
    o11 = deep_org()
    o11.request_audience("deep-a", USER, "need the user")
    check("…while forwarding a USER-targeted request still lands in the inbox",
          lambda: (lambda n0: (
              o11.audience_forward(USER, "deep-a", USER),
              true(len(o11.d.get("user_inbox", [])) > n0))[-1]
          )(len(o11.d.get("user_inbox", []))))

    # "extern"/"inbox" are ALIASES for the org-inbox sentinel; a real agent of
    # that name used to be shadowed permanently (names win — _resolve_recipient)
    ox = Org.create("extern-name")
    ox.hire(USER, None, "opus", 100, "vp")
    ox.hire(USER, None, "opus", 20, "Extern")     # a top-level peer of vp
    ox.hire(USER, "vp", "haiku", 5, "leaf")
    check("an agent literally named 'extern' is reachable as an audience target",
          lambda: (ox.audience_grant("vp", "leaf", "extern"),
                   eq([(a["grantee"], a["grantor"]) for a in ox.d["audiences"]],
                      [("leaf", "extern")]),
                   eq(ox.post_mail("leaf", "extern", "hi")["delivered"],
                      "extern"))[-1])
    oy = Org.create("extern-sentinel")
    oy.hire(USER, None, "opus", 100, "t")
    oy.hire(USER, "t", "haiku", 5, "sub")
    check("…and the @-sentinel still grants the ORG INBOX, unambiguously",
          lambda: (oy.audience_grant(USER, "sub", EXTERN),
                   true(oy._has_audience("sub", EXTERN)))[-1])
    check("…as does the alias when no node claims the name",
          lambda: (lambda oo: (
              oo.hire(USER, None, "opus", 100, "t"),
              oo.hire(USER, "t", "haiku", 5, "sub"),
              oo.audience_grant(USER, "sub", "inbox"),
              true(oo._has_audience("sub", EXTERN)))[-1]
          )(Org.create("alias")))

    # observations, pinned so a change in either is deliberate
    oz = deep_org()
    defect("D-D", "an ARCHIVED node can still forward an audience request",
           lambda: (oz.request_audience("deep-a", "top", "why"),
                    oz.retire(USER, "leaf-a1"),
                    eq(oz.audience_forward("leaf-a1", "deep-a",
                                           "top")["currently_at"], "mid-a"))[-1],
           "`audience_forward` has no `_require_live` on the actor. Harmless "
           "today (only a driven live session calls it), but the request "
           "machinery is the one place an archived node still acts.")
    check("an agent may mail ITSELF — the sibling clause makes parent==parent "
          "trivially true (documented quirk, not a refusal)",
          lambda: eq(deep_org().post_mail("leaf-a1", "leaf-a1", "note")
                     ["delivered"], "leaf-a1"))


# =====================================================================  §6
def section_edges():
    print("§6 edge shapes:")

    # --- the @-sentinel namespace must be unreachable from slugify
    corpus = ["@user", "@system", "@extern", "@ext:a", "@org:b", "@mcp:c",
              "＠user", "﹫user", "@@@", "  @user  ", "@" * 50, "user@", "a@b",
              "@USER", "@ user", "\u0040user", "\uff20user", "\ufe6buser",
              "<color=cyan>@user</color>", "user", "system", "extern",
              "日本語", "🙂🙂", "ＵＳＥＲ", "u\u0073er", "\u00b5", "--", "-@-"]
    rnd = random.Random(7)
    alphabet = "@＠﹫abcXY0-_ :/.\\\t\n\u00a0\u200b日🙂<>=" + "".join(
        chr(c) for c in range(0x20, 0x7f))
    for _ in range(4000):
        corpus.append("".join(rnd.choice(alphabet)
                              for _ in range(rnd.randint(1, 12))))
    ok_slugs, rejected = [], 0
    for nm in corpus:
        try:
            s = slugify(nm)
        except LedgerError:
            rejected += 1
            continue
        ok_slugs.append(s)
    check(f"slugify fuzz ({len(corpus)} names, {rejected} refused): no slug can "
          f"start with '@', or contain anything but [a-z0-9-]",
          lambda: (true(not [s for s in ok_slugs if s.startswith("@")],
                        "a slug reached the sentinel namespace"),
                   true(not [s for s in ok_slugs
                             if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", s)],
                        f"bad slug: {[s for s in ok_slugs if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', s)][:5]}"))[-1])

    # --- a node literally NAMED user/system gets none of the sentinel's powers
    on = Org.create("names")
    on.hire(USER, None, "opus", 100, "user")
    on.hire(USER, None, "opus", 100, "system")
    on.hire(USER, "user", "haiku", 5, "kid")
    check("an agent may be named 'user' and 'system' (ids are plain slugs)",
          lambda: eq(sorted(on.nodes), ["kid", "system", "user"]))
    check("actor_kind classifies them as AGENTS, not the sentinels",
          lambda: (eq(actor_kind_of("user"), "agent"),
                   eq(actor_kind_of("system"), "agent"),
                   eq(actor_kind_of(USER), "user"),
                   eq(actor_kind_of(SYSTEM), "system"))[-1])
    check("the node named 'user' has NO authority over another root",
          lambda: expect_error(lambda: on.retire("user", "system"),
                               "no authority"))
    check("…and cannot delete anything (delete is USER-only)",
          lambda: expect_error(lambda: on.delete("user", "kid"), "only the user"))
    check("…and cannot hire at top level",
          lambda: expect_error(
              lambda: on.hire("user", None, "haiku", 1, "x", **spec()),
              "only the user hires at top level"))
    check("_resolve_recipient: the NAME wins — 'user' addresses the agent",
          lambda: eq(on._resolve_recipient("user"), "user"))
    check("…and the real user is still reachable by the @-sentinel",
          lambda: eq(on._resolve_recipient(USER), USER))
    check("…so mail to 'user' lands in the agent's box, not the user inbox",
          lambda: (eq(on.post_mail("kid", "user", "hi")["delivered"], "user"),
                   eq(len(on.d.get("user_inbox", [])), 0))[-1])
    on2 = Org.create("names2")
    on2.hire(USER, None, "opus", 10, "extern")
    check("a node named 'extern' does not become the ORG INBOX sentinel",
          lambda: (on2.hire(USER, "extern", "haiku", 1, "sub"),
                   true(not on2._has_audience("sub", EXTERN)),
                   expect_error(lambda: on2.post_mail("sub", "@mcp:x", "hi"),
                                "top-level"))[-1])

    # --- names
    check("empty and whitespace-only names are refused (§4.7)", lambda: (
        expect_error(lambda: on.hire(USER, None, "haiku", 0, ""), "name"),
        expect_error(lambda: on.hire(USER, None, "haiku", 0, "   "), "name"),
        expect_error(lambda: on.hire(USER, None, "haiku", 0, "\t\n"), "name"))[-1])
    check("a name of only punctuation is refused (nothing to slugify)",
          lambda: expect_error(lambda: on.hire(USER, None, "haiku", 0, "---"),
                               "name"))
    check("a name of only non-latin letters is refused, not crashed",
          lambda: expect_error(lambda: on.hire(USER, None, "haiku", 0, "日本語"),
                               "name"))
    check("rich text in a name slugifies to its words (a real past trap)",
          lambda: eq(on.hire(USER, None, "haiku", 0,
                             "<color=cyan>Systems</color>")["node"],
                     "color-cyan-systems-color"))
    check("…and the display title keeps the rich text verbatim",
          lambda: eq(on.nodes["color-cyan-systems-color"]["title"],
                     "<color=cyan>Systems</color>"))
    check("collisions get numeric suffixes, forever", lambda: (
        eq(on.hire(USER, None, "haiku", 0, "dup")["node"], "dup"),
        eq(on.hire(USER, None, "haiku", 0, "dup")["node"], "dup-2"),
        eq(on.hire(USER, None, "haiku", 0, "DUP!")["node"], "dup-3"),
        eq(on.hire(USER, None, "haiku", 0, "  dup  ")["node"], "dup-4"))[-1])
    check("a 10k-character name is accepted and truncation never crashes",
          lambda: true(len(on.hire(USER, None, "haiku", 0,
                                   "long " * 2000)["node"]) > 0))

    # --- structure
    st = deep_org()
    check("a node cannot be moved onto itself", lambda: expect_error(
        lambda: st.move(USER, "mid-a", "mid-a"), "cycle"))
    check("a node cannot be moved into its own subtree (cycle)",
          lambda: expect_error(lambda: st.move(USER, "mid-a", "deep-a"), "cycle"))
    check("…nor into a node it does not command, via demote directly",
          lambda: expect_error(lambda: st.demote(USER, "top", "leaf-a1"), "cycle"))
    check("promote refuses a target that is not above the node",
          lambda: expect_error(lambda: st.promote(USER, "leaf-a1", "mid-b"),
                               "not above"))
    check("only the user promotes to top level (§7.4)", lambda: expect_error(
        lambda: st.promote("top", "leaf-a1", None), "only the user"))
    check("moving to the same parent is a no-op, not an error", lambda: (
        lambda rr: true("nothing to do" in rr["warnings"][0], rr)
    )(st.move(USER, "leaf-a1", "mid-a")))

    dp = Org.create("depth")
    dp.d["max_depth"] = 3
    dp.hire(USER, None, "haiku", 200, "l0")
    dp.hire(USER, "l0", "haiku", 50, "l1")
    dp.hire(USER, "l1", "haiku", 20, "l2")
    check("max_depth refuses the hire that would cross it", lambda: expect_error(
        lambda: dp.hire(USER, "l2", "haiku", 1, "l3"), "max org depth"))
    wd = Org.create("wide")
    wd.d["max_children"] = 2
    wd.hire(USER, None, "haiku", 200, "p")
    wd.hire(USER, "p", "haiku", 1, "c1")
    wd.hire(USER, "p", "haiku", 1, "c2")
    check("max_children refuses the hire that would cross it", lambda: expect_error(
        lambda: wd.hire(USER, "p", "haiku", 1, "c3"), "reports (cap)"))
    # ☞ a table added to the module must reach an org created BEFORE it
    # existed. Org.create COPIES tiers/models into the doc, so the constant
    # alone does nothing for existing orgs — switch_model refused a freshly
    # added tier on the user's real orgs while the constant plainly had it
    # (2026-08-04). Every other test builds a fresh org, which is exactly why
    # nothing caught it; this one starts from a doc that predates the entry.
    old = deep_org()
    old.d["tiers"].pop("fable"); old.d["models"].pop("fable")
    old.d["tiers"]["sonnet"] = 42                     # a custom price
    reloaded = Org(json.loads(json.dumps(old.d)))     # what load_org does
    check("a table entry added to the module reaches an org that predates it",
          lambda: eq(reloaded.d["tiers"].get("fable"), TIERS["fable"]))
    check("…and its model id comes with it",
          lambda: eq(reloaded.d["models"].get("fable"), MODELS["fable"]))
    check("…while a per-org custom seat price is NOT overwritten",
          lambda: eq(reloaded.d["tiers"]["sonnet"], 42))

    # ☞ …and a RE-PRICE is not an ADD, so the merge above cannot carry it:
    # `setdefault` finds gpt-reserve/luna already present and leaves the old
    # 1 there forever. Measured on the dev machine 2026-09-03 — all three
    # live org docs carried `gpt-reserve: 1, luna: 1` — which is what the
    # sub-$1 repricing (user ruling, same day) would have silently shipped to
    # nobody without its own migration block. This is that block's test, and
    # the first test the sonnet 3→2 precedent ever had.
    rp = deep_org()
    rp.d["tiers"]["gpt-reserve"] = 1                  # the OLD shipped default
    rp.d["tiers"]["luna"] = 1
    rp.d["tiers"]["sonnet"] = 3                       # …and the older one
    rp.d["tiers"]["terra"] = 7                        # an operator's own price
    repriced = Org(json.loads(json.dumps(rp.d)))      # what load_org does
    check("a sub-$1 RE-PRICE reaches an org that predates it (a plain "
          "add-only merge could not)",
          lambda: eq((repriced.d["tiers"]["gpt-reserve"],
                      repriced.d["tiers"]["luna"]), (0.2, 0.2)))
    check("…the sonnet 3→2 precedent still migrates alongside it",
          lambda: eq(repriced.d["tiers"]["sonnet"], 2))
    check("…and an operator's OWN price is left alone, as ever",
          lambda: eq(repriced.d["tiers"]["terra"], 7))
    # anti-vacuity: only the old shipped default moves. A doc that already
    # carries the NEW price must survive a reload unchanged (idempotence),
    # and a doc carrying some third value must not be dragged to either.
    twice = Org(json.loads(json.dumps(repriced.d)))
    check("…the migration is idempotent — a second load changes nothing",
          lambda: eq(twice.d["tiers"]["luna"], 0.2))
    cu = deep_org()
    cu.d["tiers"]["luna"] = 4                         # neither old nor new
    check("…and a customised sub-$1 seat is not repriced either",
          lambda: eq(Org(json.loads(json.dumps(cu.d))).d["tiers"]["luna"], 4))

    # the repricing is a DROP, and a drop only frees capacity: committed falls
    # by the difference per live seat, free rises by it, and `free() >= 0`
    # cannot begin to fail. Proved against a doc frozen at the old price
    # rather than asserted.
    bud = Org.create("reprice-budget")
    bud.hire(USER, None, "opus", 40, "boss")
    bud.d["tiers"]["luna"] = 1                        # pretend it is yesterday
    bud.hire(USER, "boss", "luna", 3, "cheap")
    before = (bud.committed("boss"), bud.free("boss"))
    after = Org(json.loads(json.dumps(bud.d)))        # …and reload into today
    check("re-pricing a live agent's tier DOWN frees exactly the difference",
          lambda: eq((after.committed("boss"), after.free("boss")),
                     (round(before[0] - 0.8, 2), round(before[1] + 0.8, 2)),
                     "committed/free move by 1 − 0.2"))
    check("…and the seat cost of the already-hired node follows the table, "
          "because a node stores its MODEL and never its seat",
          lambda: eq(after.seat_cost("cheap"), 0.2))
    check("…so no invariant tightens: the audit still reports no overdraft "
          "and no problems",
          lambda: eq((after.audit()["no_overdraft"], after.audit()["problems"]),
                     (True, [])))

    # ⚠ the ONE place a cheaper seat can REFUSE something it used to allow.
    # `_check_tier_ceiling` compares seats as an ORDERING, so tiers that tied
    # at 1 under the old floor no longer tie: a `max_tier="luna"` ceiling used
    # to admit haiku (five times luna's price) because 1 > 1 is false. Pinned
    # here because it is a real behaviour change on a saved ceiling, even
    # though no live org had a kiosk ceiling set on the day (measured).
    ceil = Org.create("reprice-ceiling")
    ceil.d["kiosk"] = {"enabled": True}
    ceil.set_kiosk_ceiling({"max_tier": "luna"})
    check("a luna ceiling now REFUSES haiku — the 1-vs-1 tie is gone",
          lambda: expect_error(
              lambda: ceil.hire(USER, None, "haiku", 1, "h"),
              "caps agent tier at luna"))
    check("…and still admits luna itself",
          lambda: eq((ceil.hire(USER, None, "luna", 1, "l"),
                      ceil.node("l")["model"])[1], "luna"))

    # ☞ …AND THE SAME THING AGAIN FOR THE DYNAMIC (OpenRouter) HALF, which the
    # block above MISSED because it names `gpt-reserve` and `luna` (user ask
    # 2026-09-04: put the `or-*` tiers on "the new sub-1 credit cost scheme
    # that luna (and reserve) abide by"). Measured on the live machine that
    # day, read-only: all three org documents carried
    # `or-deepseek-deepseek-v4-flash-latest: 1` against a catalog price of
    # $0.05/M, and two of them `or-z-ai-glm-5-3-flash: 1` against $0.075/M —
    # while grok-4.6 at 2 and kimi-k3 at 3 were already right. The rig below
    # reproduces that document shape exactly.
    orr.set_key("sk-or-v1-" + "0" * 24)
    orr._http_get = lambda url, headers: (
        200, json.dumps({"data": [
            {"id": "~deepseek/deepseek-v4-flash-latest",
             "name": "DeepSeek: V4 Flash Latest", "context_length": 163840,
             "pricing": {"prompt": "0.00000005", "completion": "0.00000016"},
             "supported_parameters": ["tools"]},
            {"id": "z-ai/glm-5.3-flash", "name": "Z.AI: GLM 5.3 Flash",
             "context_length": 200000,
             "pricing": {"prompt": "0.000000075", "completion": "0.00000025"},
             "supported_parameters": ["tools"]},
            {"id": "x-ai/grok-4.6", "name": "xAI: Grok 4.6",
             "context_length": 256000,
             "pricing": {"prompt": "0.000002", "completion": "0.000006"},
             "supported_parameters": ["tools"]},
        ]}).encode("utf-8"))
    orr.catalog(force=True)
    orr.add_favorite("~deepseek/deepseek-v4-flash-latest")
    # …and glm is DESELECTED, as it is live: its org row survives (a node hired
    # on it still runs) with no favorite record to price it from
    T_DS, T_GLM, T_GROK = ("or-deepseek-deepseek-v4-flash-latest",
                           "or-z-ai-glm-5-3-flash", "or-x-ai-grok-4-6")

    def live_shaped_doc():
        o = Org.create("or-reprice")
        o.d["tiers"].update({T_DS: 1, T_GLM: 1, T_GROK: 2})
        o.d["models"].update({T_DS: "~deepseek/deepseek-v4-flash-latest",
                              T_GLM: "z-ai/glm-5.3-flash",
                              T_GROK: "x-ai/grok-4.6"})
        return o

    # ⚠ POSITIVE CONTROL FIRST. A migration test that only ever asserts the
    # AFTER state cannot tell "it migrated" from "it was never stale". Prove
    # the fixture really is stale before the load path touches it — this is
    # the check that would go green on a broken fixture, so it is the one
    # that has to be seen failing-shaped first.
    check("[control] the fixture document really does carry the stale prices "
          "before any load — otherwise the checks below prove nothing",
          lambda: eq({k: live_shaped_doc().d["tiers"][k]
                      for k in (T_DS, T_GLM, T_GROK)},
                     {T_DS: 1, T_GLM: 1, T_GROK: 2}, "as measured live"))
    orb = Org(json.loads(json.dumps(live_shaped_doc().d)))   # what load_org does
    check("an OpenRouter RE-PRICE reaches an org that predates it — the "
          "document half of the fix, which a constant edit cannot do",
          lambda: eq(orb.d["tiers"][T_DS], 0.1, "$0.05/M → the 0.10 floor"))
    check("…including a DESELECTED tier, priced from the catalog because it "
          "has no favorite record left",
          lambda: eq(orb.d["tiers"][T_GLM], 0.1, "$0.075/M → the 0.10 floor"))
    check("…while a tier at or above $1/M is NOT touched: the old and new "
          "rules agree there, so nothing can be stale",
          lambda: eq(orb.d["tiers"][T_GROK], 2, "$2/M stays 2"))
    check("…and the static half still migrates alongside it, unchanged",
          lambda: eq((orb.d["tiers"]["luna"], orb.d["tiers"]["sonnet"]),
                     (0.2, 2), "luna/sonnet"))
    check("…the migration is idempotent — a second load changes nothing",
          lambda: eq(Org(json.loads(json.dumps(orb.d))).d["tiers"][T_DS], 0.1))

    def or_custom():
        o = live_shaped_doc()
        o.d["tiers"][T_DS] = 0.5                  # neither old rule nor new
        o.d["tiers"][T_GROK] = 1                  # $2/M hand-set BELOW its rule
        r = Org(json.loads(json.dumps(o.d)))
        eq(r.d["tiers"][T_DS], 0.5, "a hand-set fractional price stays")
        # ⚠ the dangerous direction. grok at 1 is not a stale default — the
        # OLD rule floored $2 to 2, not 1 — so it is an operator's own price.
        # Dragging it to 2 would RAISE a seat, the one move that can overdraw
        # a saved org. Nothing here may raise anything.
        eq(r.d["tiers"][T_GROK], 1, "a hand-set price is never RAISED to the rule")
    check("…and an operator's OWN or-* price is left alone in both directions",
          or_custom)

    # the budget half, proved against a document frozen at the old price
    # rather than asserted: a node stores its MODEL and never its seat, so
    # `committed`/`free` re-derive the moment the table moves.
    orb2 = live_shaped_doc()
    orb2.hire(USER, None, "opus", 40, "boss2")
    orb2.hire(USER, "boss2", T_DS, 3, "cheap2")
    b4 = (orb2.committed("boss2"), orb2.free("boss2"))
    af = Org(json.loads(json.dumps(orb2.d)))
    check("re-pricing a live or-* agent's tier DOWN frees exactly the "
          "difference, and its seat follows the table",
          lambda: eq((af.committed("boss2"), af.free("boss2"), af.seat_cost("cheap2")),
                     (round(b4[0] - 0.9, 2), round(b4[1] + 0.9, 2), 0.1),
                     "committed/free move by 1 − 0.1"))
    check("…so no invariant tightens: the audit reports no overdraft, no "
          "problems",
          lambda: eq((af.audit()["no_overdraft"], af.audit()["problems"]),
                     (True, [])))

    # ⚠ THE ORDERING WARNING DID NOT REACH THIS CHANGE — no tie could break,
    # because an `or-*` tier was not in the ordering AT ALL. That is what this
    # check pinned when it was written, and the note that used to sit here
    # called the invisibility "a separate pre-existing gap in the ceiling, not
    # something this change is entitled to fix". It was fixed on 2026-09-04
    # (see §CEILING below), so the reason this hire is admitted has CHANGED:
    # not "the ceiling never consults it" but "0.1 is genuinely under a luna
    # cap of 0.2". Same outcome, different sentence — worth saying, because a
    # green assertion whose stated reason has silently stopped being true is
    # how a test starts guarding nothing.
    orc = live_shaped_doc()
    orc.d["kiosk"] = {"enabled": True}
    orc.set_kiosk_ceiling({"max_tier": "luna"})
    check("an or-* tier is still not a MODULE tier, and at 0.1 it is under a "
          "luna cap on its own price rather than by being unseen",
          lambda: (eq(T_DS in TIERS, False, "not a module tier"),
                   eq(Org(json.loads(json.dumps(orc.d)))
                      .hire(USER, None, T_DS, 1, "or1")["node"], "or1",
                      "admitted at 0.1 exactly as it was at 1")))

    # ══════════════════════════════════════════════════════ §CEILING (2026-09-04)
    # THE KIOSK CAP USED TO ADMIT EVERY OpenRouter TIER IT NAMES.
    #
    # `_check_tier_ceiling` asked `tier in TIERS` against the MODULE table.
    # An `or-*` tier is never in it — those are minted at runtime and live
    # only in the per-org `d["tiers"]` — so the test was SKIPPED, not failed,
    # and the cap admitted a $3/M model under a haiku cap while refusing a
    # static opus. `api.provider_hire_gate` hid most of it by refusing
    # OpenRouter tiers in kiosk orgs outright, but that gate is explicitly
    # temporary ("until its sandboxing is settled") and the plain-rehire door
    # skips it by design, so the ledger was not carrying the guarantee its own
    # docstring claims "for every actor".
    #
    # ⚠ THE FIXTURE HAS TO CREATE THE CONDITION. No live org had a kiosk
    # ceiling set at all when this was written, so nothing here can be found
    # by looking — a cap must be set, and it must be set BELOW a tier that is
    # actually present, or every assertion below is free.
    # a third or-* row, with NO favorite record behind it — the same shape as
    # T_GLM above (a deselected favorite whose org row survives). The document
    # row IS the whole input to the ordering; nothing consults the catalog.
    T_K3 = "or-moonshotai-kimi-k3"

    def capped(slug, cap="haiku"):
        """a kiosk org capped at `cap`, carrying three or-* rows: 0.1, 2, 3."""
        o = Org.create(slug)
        o.d["tiers"].update({T_DS: 0.1, T_GROK: 2, T_K3: 3})
        o.d["models"].update({T_DS: "~deepseek/deepseek-v4-flash-latest",
                              T_GROK: "x-ai/grok-4.6",
                              T_K3: "moonshotai/kimi-k3"})
        o.d["kiosk"] = {"enabled": True}
        o.set_kiosk_ceiling({"max_tier": cap})
        return o

    # ── the control, FIRST: prove the cap discriminates before trusting a
    # refusal. A cap that refused everything, or one set above every tier in
    # the table, would make every check under it meaningless.
    check("[control] a haiku cap refuses a static opus, admits a static "
          "haiku, and admits an or-* tier genuinely BELOW it",
          lambda: (expect_error(
              lambda: capped("ceil-c1").hire(USER, None, "opus", 0, "n"),
              "caps agent tier at haiku"),
              eq(capped("ceil-c2").hire(USER, None, "haiku", 0, "n")["node"],
                 "n", "static haiku admitted"),
              eq(capped("ceil-c3").hire(USER, None, T_DS, 0, "n")["node"],
                 "n", "or-* at 0.1 admitted under a cap of 1")))

    check("HIRE: an or-* tier ABOVE the cap is refused, and the refusal names "
          "the tier the caller asked for",
          lambda: expect_error(
              lambda: capped("ceil-h").hire(USER, None, T_K3, 0, "n"),
              f"caps agent tier at haiku — {T_K3} agents"))

    def with_node(slug, tier, cap="haiku"):
        """…and the same org with `tier` hired BEFORE the cap existed — the
        reachable route, since a kiosk refuses the or-* hire at the API door."""
        o = Org.create(slug)
        o.d["tiers"].update({T_DS: 0.1, T_GROK: 2, T_K3: 3})
        o.d["models"].update({T_DS: "~deepseek/deepseek-v4-flash-latest",
                              T_GROK: "x-ai/grok-4.6",
                              T_K3: "moonshotai/kimi-k3"})
        o.hire(USER, None, tier, 0, "n")
        o.d["kiosk"] = {"enabled": True}
        o.set_kiosk_ceiling({"max_tier": cap})
        return o

    check("SWITCH_MODEL: a live node cannot be switched UP onto an or-* tier "
          "above the cap…",
          lambda: expect_error(
              lambda: with_node("ceil-s", "haiku").switch_model(USER, "n", T_K3),
              f"{T_K3} agents"))
    check("…but a switch onto an or-* tier under the cap is still welcome "
          "(the motto: permit as much as possible)",
          lambda: eq(with_node("ceil-s2", "haiku")
                     .switch_model(USER, "n", T_DS)["model"], T_DS))

    def archived(slug, tier, cap="haiku"):
        o = with_node(slug, tier, cap)
        o.retire(USER, "n")
        return o

    # the door the API gate does NOT cover: a plain rehire passes
    # `user_choice_only=True`, which returns from `provider_hire_gate` before
    # the kiosk holdout. If the ledger does not hold this, nothing does.
    check("PLAIN REHIRE: an archived or-* agent above the cap stays out",
          lambda: expect_error(
              lambda: archived("ceil-r", T_K3).rehire(USER, "n"),
              f"{T_K3} agents"))
    _under = archived("ceil-r2", T_DS)
    check("…and one below the cap comes back, so the cap is not a blanket "
          "no-or-* rule wearing a ceiling's clothes",
          lambda: eq((_under.rehire(USER, "n"),
                      _under.node("n")["state"])[1], "live"))

    # ⚠ THE SECOND BUG, and it is not the module-table one. rehire read
    # `tier not in TIERS` where it meant `tier is None`, so an or-* OVERRIDE
    # took the no-override branch and the ceiling tested the tier the node
    # ALREADY RAN. Only visible between two OpenRouter tiers: with a Claude
    # node the provider-crossing refusal fires first and hides it.
    check("REHIRE + or-* OVERRIDE: the tier ASKED FOR is tested, not the one "
          "the node used to run",
          lambda: expect_error(
              lambda: archived("ceil-o", T_DS).rehire(USER, "n", tier=T_K3),
              f"{T_K3} agents"))
    _down = archived("ceil-o2", T_K3)
    check("…and the override still works DOWNWARD — an over-cap archived "
          "agent is rescued by rehiring it cheaper, not stranded",
          lambda: eq((_down.rehire(USER, "n", tier=T_DS),
                      _down.node("n")["model"])[1], T_DS))

    # ties: `>` is strict, so a seat EQUAL to the cap is admitted — the same
    # rule that admits flash under a haiku cap. Named rather than discovered.
    check("a TIE is admitted: an or-* tier at exactly the cap's seat passes, "
          "as flash does under a haiku cap",
          lambda: (eq(capped("ceil-t", "sonnet").hire(
              USER, None, T_GROK, 0, "n")["node"], "n", "or-* 2 under cap 2"),
              eq(capped("ceil-t2").hire(USER, None, "flash", 0, "n")["node"],
                 "n", "static flash 1 under cap 1")))

    # the admin's REPORT has to count the same rule the refusal enforces, or
    # it is a number about a different question. It scored or-* agents at 0.
    rep = Org.create("ceil-rep")
    rep.d["tiers"].update({T_K3: 3})
    rep.d["models"].update({T_K3: "moonshotai/kimi-k3"})
    rep.hire(USER, None, T_K3, 0, "orlive")
    rep.hire(USER, None, "opus", 0, "opuslive")
    rep.hire(USER, None, T_K3, 0, "orarch")
    rep.retire(USER, "orarch")
    rep.d["kiosk"] = {"enabled": True}
    _w = " || ".join(rep.set_kiosk_ceiling({"max_tier": "haiku"})["warnings"])
    check("set_kiosk_ceiling's over-cap report counts or-* agents too — live "
          "and archived — instead of scoring them 0",
          lambda: (eq("orlive" in _w, True, f"live or-* named: {_w}"),
                   eq("opuslive" in _w, True, "the static one still named"),
                   eq("orarch" in _w, True, "archived or-* named (stranded)")))

    # fail-open, deliberately: a tier with no row in this document brings no
    # seat to the ordering, so the ceiling refuses nothing and the real doors
    # refuse it first for being unknown. Both halves asserted — the second is
    # what makes the first safe rather than a hole.
    check("an or-* tier absent from the document is not refused BY THE "
          "CEILING — hire refuses it earlier, for not existing",
          lambda: (eq(capped("ceil-u")._ceiling_seat("or-nobody-nope"), None,
                      "no seat, so no place in the ordering"),
                   expect_error(
                       lambda: capped("ceil-u2").hire(
                           USER, None, "or-nobody-nope", 0, "n"),
                       "unknown tier")))

    # put the rig back: a favorite is GLOBAL to this data root, so leaving it
    # selected would add an or-* row to every org built after this point — and
    # the static-vocabulary check just below counts the tier table exactly.
    orr.remove_favorite("~deepseek/deepseek-v4-flash-latest")

    # model VERSIONS are a subcategory of a tier, never a tier (user ruling
    # 2026-08-04): a fixed set of tiers, one chip each; the version lives in
    # the gear. Eleven with the provider-gated Astra metadata row: the four
    # Claude bands, five Codex bands (including dark-until-offered Astra), and
    # the antigravity family
    # (flash/pro — user greenlight 2026-08-29, re-walked for the Antigravity
    # CLI 2026-09-02).
    mv = deep_org()
    check("the tier table is exactly the eleven price bands",
          lambda: eq(sorted(mv.d["tiers"]),
                     ["astra", "fable", "flash", "gpt-reserve", "haiku", "luna",
                      "opus", "pro", "sol", "sonnet", "terra"]))
    check("model_for defaults to the tier's own model",
          lambda: eq(mv.model_for("top"), MODELS["opus"]))
    mv.set_scope(USER, "top", model_version="4.8")
    check("a pinned version changes the --model id, not the tier",
          lambda: (eq(mv.model_for("top"), "claude-opus-4-8"),
                   eq(mv.node("top")["model"], "opus"))[0])
    check("…and not the seat cost",
          lambda: eq(mv.seat_cost("top"), TIERS["opus"]))
    check("an unknown version is refused",
          lambda: expect_error(lambda: mv.set_scope(USER, "top",
                                                    model_version="9.9"),
                               "no model version"))
    check("a tier with no versions refuses any pin",
          lambda: expect_error(lambda: mv.set_scope(USER, "leaf-a1",
                                                    model_version="4.8"),
                               "single model"))
    mv.switch_model(USER, "top", "sonnet")
    check("switching TIER drops a version that belonged to the old one",
          lambda: eq(mv.model_for("top"), MODELS["sonnet"]))
    mv.switch_model(USER, "top", "opus")
    check("…and switching back restores it (the pin is remembered, not lost)",
          lambda: eq(mv.model_for("top"), "claude-opus-4-8"))
    mv.set_scope(USER, "top", model_version="")
    check("'' clears the pin back to the tier default",
          lambda: eq(mv.model_for("top"), MODELS["opus"]))

    check("lineage bearers do not count against max_children", lambda: (
        wd.compact_split("c1", "s2"),
        eq(len(wd.children("p", live_only=False)), 3),
        eq(len(wd.org_children("p")), 2))[-1])

    # ⚑ the structural caps are enforced by hire and by NOTHING ELSE
    dp.hire(USER, None, "haiku", 50, "m0")
    dp.hire(USER, "m0", "haiku", 10, "m1")
    # PROMOTED 2026-08-04 — the design ruling D-A asked for was given: the caps
    # bind reorganization as well as growth, and both defaults move to a
    # deliberately unreachable 1024 so they are runaway insurance and nothing
    # else. The check measures the DEEPEST LEAF of the moved subtree, which is
    # what actually ends up deepest.
    check("move refuses a subtree that would land past max_depth",
          lambda: expect_error(lambda: dp.move(USER, "m0", "l2"), "max org depth"))
    check("…and the tree is untouched by the refusal",
          lambda: true(dp.depth("m1") < dp.d["max_depth"],
                       f"depth {dp.depth('m1')} vs cap {dp.d['max_depth']}"))
    wd.hire(USER, None, "haiku", 5, "q")
    check("move refuses a child that would land past max_children",
          lambda: expect_error(lambda: wd.move(USER, "q", "p"), "reports (cap)"))
    check("…and the parent keeps its count",
          lambda: true(len(wd.org_children("p")) <= wd.d["max_children"],
                       f"{len(wd.org_children('p'))} vs {wd.d['max_children']}"))

    # D-102 SUPERSEDES the 2026-08-04 won't-fix that used to be pinned here.
    # That ruling answered "does permission_mode need a clamp for its own
    # sake" — no, because dirs/tools bound what an agent can REACH. The
    # 2026-08-07 ruling answers a different question: may an agent hand the
    # mode to a subordinate? Yes, and the cap at the actor's own mode is what
    # makes that safe. This block was written to force that argument rather
    # than allow a silent narrowing, and it did its job — the rewrite is the
    # argument, not a bypass of it.
    #
    # ⚠ The second check below was ALSO mislabelled: it said "when an ancestor
    # LOWERS its own" while its setup went default→acceptEdits, which is a
    # RAISE. It would have passed under the new sweep too, for the wrong
    # reason. Both directions are now asserted separately.
    # ⚠ ORDER MATTERS HERE, since D-106: the user's grant to mid-a CASCADES
    # top upward, so the agent-cap refusal has to be tested BEFORE it — after
    # the cascade `top` holds bypassPermissions and the same call is legal.
    # Sequencing was left implicit once already in this block (see above); it
    # is explicit now, and each check asserts the state it depends on.
    pm = deep_org()
    pm.set_scope(USER, "top", permission_mode="acceptEdits")
    check("☠ an AGENT may not grant above its own mode (D-102's cap)",
          lambda: expect_error(
              lambda: pm.set_scope("top", "mid-a",
                                   permission_mode="bypassPermissions"),
              "exceeds the parent"))
    check("the USER may — and D-106 cascades the chain up to the granter",
          lambda: (pm.set_scope(USER, "mid-a",
                                permission_mode="bypassPermissions"),
                   eq(pm.node("mid-a")["scope"]["permission_mode"],
                      "bypassPermissions"),
                   eq(pm.node("top")["scope"]["permission_mode"],
                      "bypassPermissions", "the chain between was left below"))[-1])
    check("an ancestor RAISING its own mode leaves the subtree alone",
          lambda: (pm.set_scope(USER, "top", permission_mode="bypassPermissions"),
                   eq(pm.node("mid-a")["scope"]["permission_mode"],
                      "bypassPermissions"))[-1])
    pm.set_scope(USER, "top", permission_mode="default")       # a LOWERING
    check("☞ …but LOWERING it sweeps the subtree down with it",
          lambda: eq(pm.node("mid-a")["scope"]["permission_mode"], "default"))

    # --- unknown ids raise LedgerError, never KeyError
    u = deep_org()
    ghost = "no-such-node"
    calls = [
        lambda: u.node(ghost), lambda: u.retire(USER, ghost),
        lambda: u.rehire(USER, ghost), lambda: u.dissolve(USER, ghost),
        lambda: u.delete(USER, ghost), lambda: u.reallocate(USER, ghost, 1),
        lambda: u.switch_model(USER, ghost, "haiku"),
        lambda: u.move(USER, ghost, "top"), lambda: u.move(USER, "top", ghost),
        lambda: u.set_scope(USER, ghost, org_visibility="team"),
        lambda: u.reorder(USER, ghost, after="top"),
        lambda: u.post_mail(ghost, "top", "hi"),
        lambda: u.post_mail("top", ghost, "hi"),
        lambda: u.request_audience(ghost, USER, "why"),
        lambda: u.audience_grant(USER, ghost, "top"),
        lambda: u.request_credits(ghost, 5, "why"),
        lambda: u.hire(USER, ghost, "haiku", 1, "x"),
        lambda: u.reseed(USER, ghost, "s"),
        lambda: u.compact_split(ghost, "s"),
        lambda: u.mark_unrecoverable(ghost, "why"),
        lambda: u.seat_cost(ghost), lambda: u.free(ghost),
        lambda: u.depth(ghost), lambda: u.effective_effort(ghost),
    ]
    crashes = []
    for i, c in enumerate(calls):
        try:
            c()
            crashes.append((i, "no error at all"))
        except LedgerError:
            pass
        except Exception as e:                    # noqa: BLE001 — that IS the test
            crashes.append((i, f"{type(e).__name__}: {e}"))
    check(f"all {len(calls)} entry points reject an unknown node id with a "
          f"LedgerError, never a raw exception",
          lambda: true(not crashes, f"raw crashes: {crashes}"))

    # --- deep lineage
    ln = Org.create("lineage")
    ln.hire(USER, None, "opus", 100, "gen")
    for i in range(10):
        ln.compact_split("gen", f"s{i}")
    check("ten compaction generations stack without corrupting the org axis",
          lambda: (eq(len(ln.lineage_stack("gen")), 10),
                   eq(ln.nodes["gen"]["generation"], 10),
                   eq(ln.org_children(None), ["gen"]),
                   eq(len(ln.children(None, live_only=False)), 11))[-1])
    check("…the whole stack is visible in tree() and costs nothing",
          lambda: (eq(len(ln.tree()["roots"][0]["lineage"]), 10),
                   eq(sum(ln.nodes[k]["grant"] for k in ln.lineage_stack("gen")),
                      0))[-1])
    check("…and dissolving the successor takes every generation with it",
          lambda: (lambda rr: eq(len(rr["nodes"]), 11)
                   )(ln.dissolve(USER, "gen")))

    lb = Org.create("bearermove")
    lb.hire(USER, None, "opus", 200, "vp")
    lb.hire(USER, None, "opus", 50, "other")
    lb.hire(USER, "vp", "opus", 40, "mgr")
    lb.compact_split("mgr", "s2")
    check("a lineage bearer may not be re-parented on its own (§8.5)",
          lambda: expect_error(lambda: lb.move(USER, "mgr@0", "other"),
                               "lineage bearer"))
    check("…but moving the SUCCESSOR carries the whole stack with it",
          lambda: (lb.move(USER, "mgr", "other"),
                   eq(lb.nodes["mgr"]["parent"], "other"),
                   eq(lb.nodes["mgr@0"]["parent"], "other"))[-1])

    # A corrupted doc must not wedge the process. Both walks used to spin
    # forever on a loop — no exception, no return. Run them on a thread so a
    # REGRESSION fails this suite instead of hanging it.
    def terminates(fn, label):
        out = []
        t = threading.Thread(target=lambda: out.append(fn()), daemon=True)
        t.start()
        t.join(5.0)
        true(out, f"{label} did not terminate within 5s (infinite walk)")
        return out[0]

    cy = Org.create("cycle")
    cy.hire(USER, None, "opus", 100, "a")
    cy.hire(USER, "a", "opus", 20, "b")
    cy.compact_split("b", "s2")
    cy.nodes["b@0"]["predecessor"] = "b"
    cy.nodes["b"]["predecessor"] = "b@0"          # a predecessor LOOP
    check("lineage_stack terminates on a predecessor cycle (corrupted doc)",
          lambda: true(len(terminates(lambda: cy.lineage_stack("b"),
                                      "lineage_stack")) < 5))
    cy.nodes["b"]["parent"] = "b@0"
    cy.nodes["b@0"]["parent"] = "b"               # a parent LOOP
    check("ancestors terminates on a parent cycle (corrupted doc)",
          lambda: true(terminates(lambda: cy.ancestors("b"), "ancestors")[-1]
                       == USER))


def actor_kind_of(a):
    from orgtree.ledger import actor_kind
    return actor_kind(a)


# ============================================================  discrimination
# `python test_ledger_authority.py --discriminate`
#
# A suite that finds nothing is either excellent news or weak, and the only way
# to tell is to put the defects back. Each entry names a fix in ledger.py, the
# exact source hunk that IS that fix, and the section that must go red without
# it. The harness copies the whole `orgtree` package to a temp dir, reverts the
# one hunk in the COPY, and runs that section against it in a subprocess.
# Nothing in the repo is touched. A "stale anchor" result means the hunk moved:
# re-anchor it rather than deleting the case.
REVERSALS = [
    ("① _move top-level grant cap pre-check", "section_move_ceiling", [(
        """            for hop in down:
                if self.nodes[hop]["parent"] is None:
                    self._check_top_grant(
                        self.nodes[hop]["grant"] + c,
                        f"moving {nid} under {new_parent}")
""", "")]),
    ("②③ _taken_with fixpoint (deterministic)", "section_bearer_subtrees", [(
        """        out: set[str] = set()
        frontier = [nid]
        while frontier:
            k = frontier.pop()
            if k in out or k not in self.nodes:
                continue
            out.add(k)
            frontier.extend(self.children(k, live_only=False))
            frontier.extend(self.lineage_stack(k))
        return out""",
        """        doomed = [nid] + self.descendants(nid, live_only=False)
        out: set[str] = set(doomed)
        for k in doomed:
            out.update(self.lineage_stack(k))
        return out""")]),
    ("②③ _taken_with fixpoint (property test)", "section_conservation", [(
        """        out: set[str] = set()
        frontier = [nid]
        while frontier:
            k = frontier.pop()
            if k in out or k not in self.nodes:
                continue
            out.add(k)
            frontier.extend(self.children(k, live_only=False))
            frontier.extend(self.lineage_stack(k))
        return out""",
        """        doomed = [nid] + self.descendants(nid, live_only=False)
        out: set[str] = set(doomed)
        for k in doomed:
            out.update(self.lineage_stack(k))
        return out""")]),
    ("④ switch_model tier-check ordering", "section_clamping", [(
        "        self._require_live(nid)\n        n = self.node(nid)\n"
        "        if actor != USER:\n            if actor == nid:\n"
        "                raise LedgerError(\"you cannot switch your OWN model",
        "        self._check_tier_ceiling(tier)\n        self._require_live(nid)\n"
        "        n = self.node(nid)\n        if actor != USER:\n"
        "            if actor == nid:\n"
        "                raise LedgerError(\"you cannot switch your OWN model")]),
    ("⑤ archived-over-tier-cap warning", "section_clamping", [(
        '            stuck = sorted(i for i, n in self.nodes.items()\n'
        '                           if n["state"] == "archived"',
        '            stuck = sorted(i for i, n in self.nodes.items()\n'
        '                           if False and n["state"] == "archived"')]),
    ("⑥ mcp:* named by the subtree sweep", "section_clamping", [(
        '            if had_star and "*" not in tkept["mcp"]:',
        '            if False and had_star and "*" not in tkept["mcp"]:')]),
    ("⑦ audience_forward as the user", "section_addressing", [(
        "        nxt = target if actor == USER else self.parent(actor)",
        "        nxt = USER if actor == USER else self.parent(actor)")]),
    ("⑧ 'extern' alias vs a real agent of that name", "section_addressing", [(
        '        if target == EXTERN or (target in ("extern", "inbox")\n'
        '                                and target not in self.nodes):',
        '        if target in ("extern", "inbox", EXTERN):')]),
    ("⑨ a bearer may not be moved alone", "section_edges", [(
        """        succ = n.get("successor")
        if succ and succ in self.nodes:
            raise LedgerError(
                f'{nid} is a lineage bearer of "{succ}" — the stack shares its '
                f'successor\\'s slot (§8.5). Move "{succ}" and the stack '
                f'follows it.')
""", "")]),
    ("⑩ cycle guards on ancestors + lineage_stack", "section_edges", [
        ("        while cur is not None and cur not in seen:",
         "        while cur is not None:"),
        ("        while cur and cur in self.nodes and cur not in seen:",
         "        while cur and cur in self.nodes:")]),
    # the last four are ORDERING fixes; the reversal re-creates the original
    # order in the smallest faithful way rather than pasting the old block
    ("⑪ hire validates the name before mutating", "section_atomicity", [(
        "        slugify(name)\n        need = self.d[\"tiers\"][tier] + int(grant)",
        "        need = self.d[\"tiers\"][tier] + int(grant)")]),
    ("⑫ hire's refusals run before _chain_acquire", "section_atomicity", [(
        "        # D-021: visibility clamps like tools — strict for agent-explicit\n"
        "        # grants, lenient (warned) for user hires and defaults\n"
        "        if parent is not None:\n"
        "            vis, vclamped = self._clamp_vis(",
        "        if parent is not None:\n"
        "            self._chain_acquire(actor, parent, need, warnings,\n"
        "                                cascade=bool(self.d.get(\"cascade_hire\", True)))\n"
        "            need = 0   # the real call below becomes the no-op\n"
        "        if parent is not None:\n"
        "            vis, vclamped = self._clamp_vis(")]),
    ("⑬ rehire validates the tier before waking the chain",
     "section_atomicity", [(
         "        if tier is not None and tier not in self.d[\"tiers\"]:\n"
         "            raise LedgerError(f\"unknown tier {tier!r}\")\n"
         "        # kiosk tier cap:",
         "        # kiosk tier cap:")]),
    ("⑭ set_scope validates every field before writing any",
     "section_atomicity", [(
         "        if tools is not None:\n"
         "            ptools = (None if n[\"parent\"] is None\n"
         "                      else self.node(n[\"parent\"])[\"scope\"][\"tools\"])\n"
         "            want_tools, _ = self._clamp_tools(tools, ptools, strict=True)",
         "        if want_dirs is not None:\n"
         "            sc[\"add_dirs\"] = cast(\"list[DirGrant]\", want_dirs)\n"
         "        if tools is not None:\n"
         "            ptools = (None if n[\"parent\"] is None\n"
         "                      else self.node(n[\"parent\"])[\"scope\"][\"tools\"])\n"
         "            want_tools, _ = self._clamp_tools(tools, ptools, strict=True)")]),
]

_RUNNER = '''
import os, sys, tempfile
sys.path.insert(0, r"{pkg}")
sys.path.insert(0, r"{tests}")
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-disc-")
import orgtree.ledger as L
assert L.__file__.startswith(r"{pkg}"), "imported the REAL ledger: " + L.__file__
import test_ledger_authority as S
try:
    S.{section}()
except AssertionError as e:
    print("RED::" + str(e)[:150]); raise SystemExit(0)
print("GREEN::the section passed with the fix reverted")
'''


def discriminate():
    import shutil
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    pkg_src = os.path.abspath(os.path.join(here, "..", "orgtree"))
    src = open(os.path.join(pkg_src, "ledger.py"), encoding="utf-8").read()
    print("putting each fix back, one at a time — every section must go RED:\n")
    all_red = True
    for label, section, patches in REVERSALS:
        tmp = tempfile.mkdtemp(prefix="orgtree-disc-")
        try:
            shutil.copytree(pkg_src, os.path.join(tmp, "orgtree"))
            text, stale = src, False
            for new, old in patches:
                if new not in text:
                    stale = True
                    break
                text = text.replace(new, old, 1)
            if stale:
                print(f"  ?? {label}\n        STALE ANCHOR — the hunk moved; "
                      f"re-anchor this case")
                all_red = False
                continue
            with open(os.path.join(tmp, "orgtree", "ledger.py"), "w",
                      encoding="utf-8") as f:
                f.write(text)
            run = os.path.join(tmp, "run.py")
            with open(run, "w", encoding="utf-8") as f:
                f.write(_RUNNER.format(pkg=tmp, tests=here, section=section))
            out = subprocess.run([sys.executable, run], capture_output=True,
                                 text=True, timeout=300, encoding="utf-8",
                                 errors="replace")
            verdicts = [x for x in (out.stdout + out.stderr).splitlines()
                        if "RED::" in x or "GREEN::" in x]
            v = verdicts[-1] if verdicts else "NO VERDICT :: " + out.stderr[-200:]
            red = v.startswith("RED::")
            all_red &= red
            print(f"  {'RED  ' if red else 'GREEN'} {label}\n"
                  f"        {v.split('::', 1)[-1]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL FIXES DISCRIMINATE" if all_red else
          "\n⚠ A FIX IS NOT COVERED BY A FAILING CHECK — see above")
    return 0 if all_red else 1


def main():
    section_move_ceiling()
    section_ceiling_census()
    section_atomicity()
    section_bearer_subtrees()
    section_conservation()
    section_clamping()
    section_addressing()
    section_edges()
    if DEFECTS:
        print("\nKNOWN DEFECTS PINNED BY THIS SUITE (green while broken):")
        for did, why in DEFECTS:
            print(f"  ⚑ {did}  {why}")
    print(f"\nALL {PASS} CHECKS PASS")


if __name__ == "__main__":
    if "--discriminate" in sys.argv:
        raise SystemExit(discriminate())
    main()
