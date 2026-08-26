"""`resumable` — the banner counts what ▶ will actually do.

THE INCIDENT (user, 2026-08-26). The resume banner read

    ▶ resume 2     usage limit hit — 2 agents frozen · capacity available

in an org whose two frozen agents had both been RETIRED. Retiring does not
clear the freeze record, deliberately: a retired agent keeps its context and
can be rehired, so the record is still meaningful. The banner counted every
node that still carried one.

NOTHING BEHIND THE BANNER WAS BROKEN. `_resumable` already refused a node whose
state is not "live", so ▶ resumed nobody and reported "resumed 0 agent(s)".
Only the count lied — a display overstating what is wrong, which is its own bug
but a different one from what the reader would assume.

WHY THE RULE IS NOT MIRRORED IN THE FRONTEND
--------------------------------------------
The first fix re-implemented `_resumable` in TypeScript and pinned the two
copies together with a test that read `supervisor.py` AS SOURCE TEXT. That is
two expressions of one rule, and the check holding them together cannot tell a
rule that got STRONGER from one that got weaker — both present as the same
failure — while firing on a harmless rename and missing a semantic change that
keeps the same spelling. So the rule stays here, `api.py`'s `annotate` publishes
its ANSWER per node, and the client counts trues.

    §1  the rule itself, both legs
    §2  the projection trap — why `annotate` reads the DOCUMENT node
    §3  every node in a real payload carries the field
    §4  controls: what would make the above vacuous

    python backend/tests/test_resumable.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-resumable-")
os.environ["ORGTREE_DATA"] = _TMP
os.makedirs(_TMP, exist_ok=True)
with open(os.path.join(_TMP, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from orgtree import supervisor as sup  # noqa: E402

FAILED: list[str] = []
PASSED = 0
NOTES: list[str] = []


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception as e:                                     # noqa: BLE001
        FAILED.append(f"{label}\n      {type(e).__name__}: {e}")
        print(f"  ✗ {label}")
    else:
        PASSED += 1
        print(f"  ✓ {label}")


LIMIT = {"at": "2026-08-26T09:00:00Z", "until": "in 3 hours",
         "until_ts": 1900000000.0, "error": None, "limit": True}


def node(**kw):
    """A node DOCUMENT — the shape `_resumable` and `resume_frozen` read."""
    d = {"state": "live", "frozen": None, "limit_locked": False,
         "model": "opus", "parent": None}
    d.update(kw)
    return d


# ---------------------------------------------------------------- §1 the rule
print("\n§1  the rule, both legs")


def live_frozen_is_resumable() -> None:
    """THE LEG THAT MUST NOT BREAK. Every other check here asserts something
    is EXCLUDED, and all of them would pass if `resumable` returned False
    unconditionally — which is the reported bug from the other end and would
    silently empty the banner. This is the one that fails if it does."""
    assert sup.resumable(node(frozen=dict(LIMIT))) is True


def retired_frozen_is_not() -> None:
    """The user's report. `archived` is what the UI calls RETIRED."""
    assert sup.resumable(node(state="archived", frozen=dict(LIMIT))) is False


def unrecoverable_frozen_is_not() -> None:
    """The other non-live state — excluded for the same reason, and named
    separately so a fix keyed on `!= "archived"` fails here."""
    assert sup.resumable(node(state="unrecoverable", frozen=dict(LIMIT))) is False


def rehired_counts_again() -> None:
    """The SAME record, differing only in state. This is why the field is
    derived live rather than the record being scrubbed at retire time: a
    rehired agent's freeze is still real and still waiting."""
    rec = dict(LIMIT)
    assert sup.resumable(node(state="archived", frozen=rec)) is False
    assert sup.resumable(node(state="live", frozen=rec)) is True


def limit_locked_is_not() -> None:
    """▶ will not act on a fable-lock holder either, so counting it is the
    same lie as counting a retired one."""
    assert sup.resumable(node(frozen=dict(LIMIT), limit_locked=True)) is False


def unfrozen_is_not() -> None:
    assert sup.resumable(node(frozen=None)) is False


def another_kind_owns_it() -> None:
    """A spend freeze belongs to a different mechanism; ▶ leaves it alone."""
    assert sup.resumable(node(frozen={"spend": True, "spend_error": "x"})) is False


check("a live, frozen agent IS resumable (the leg that must hold)",
      live_frozen_is_resumable)
check("a RETIRED frozen agent is not — the reported bug", retired_frozen_is_not)
check("an unrecoverable frozen agent is not", unrecoverable_frozen_is_not)
check("rehiring makes it count again (live property, not a scrub)",
      rehired_counts_again)
check("a limit_locked frozen agent is not", limit_locked_is_not)
check("an agent with no freeze record is not", unfrozen_is_not)
check("a spend freeze belongs to another mechanism", another_kind_owns_it)


# ------------------------------------------------------- §2 projection trap
print("\n§2  why annotate reads the DOCUMENT node, not the payload one")


def payload_shape_would_lie() -> None:
    """⚠ THE REASON `annotate` CALLS `org.node(id)` INSTEAD OF USING THE NODE
    IT IS HANDED. `ledger.tree()` rebuilds `frozen` from a FIXED KEY LIST —
    at / until / until_ts / error / limit / connection — which omits `spend`.
    `_resumable` refuses a node carrying any other kind flag, so on a payload
    node a spend-frozen agent reads as RESUMABLE.

    This is not hypothetical shape-lawyering: the same projection renames
    `model` to `tier`, and a sibling feature shipped reading `model` off the
    payload, found nothing, and returned early on every node — a no-op that
    looked exactly like a working feature.

    So: the doc says no, the projection would say yes. If this check ever
    fails because the projection started carrying `spend`, the reason for
    reading the document weakens — but do not conclude it has gone away."""
    doc = node(frozen={"at": "x", "until": None, "until_ts": None,
                       "error": None, "spend": True, "spend_error": "over"})
    assert sup.resumable(doc) is False, "the document must refuse a spend freeze"

    projected = dict(doc)
    projected["frozen"] = {k: doc["frozen"].get(k) for k in
                           ("at", "until", "until_ts", "error", "limit",
                            "connection")}
    assert sup.resumable(projected) is True, (
        "the projection no longer drops `spend` — re-read the note on "
        "supervisor.resumable before relying on it")


check("a spend freeze survives in the document and is dropped by the "
      "projection, so the payload node would answer WRONG",
      payload_shape_would_lie)


# --------------------------------------------------- §3 the field is emitted
print("\n§3  every node in a real tree payload carries `resumable`")


def annotate_emits_it_everywhere() -> None:
    """A MISSING field is the abstention shape: `undefined` is falsy in the
    client, so a node `annotate` never reached would silently not count and
    the banner would quietly under-report — the reported bug inverted, and
    invisible. So build a real org through the real endpoint and require the
    key on every node at every depth, not just on the roots."""
    try:
        from fastapi.testclient import TestClient
        from orgtree import api, store
    except Exception as e:                                     # noqa: BLE001
        raise AssertionError(
            f"the web stack must be importable for this check: "
            f"{type(e).__name__}: {e}") from e

    # hires carry NO defaults by design — every capability stated outright
    spec = {"add_dirs": [], "charter": "c", "org_visibility": "team",
            "tools": {"bash": False, "web": False, "edit": False,
                      "subagents": False, "mcp": []}}
    org = store.create_org("resumecheck")
    org.hire("@user", None, "opus", 5, "top", **spec)
    org.hire("top", "top", "haiku", 0, "kid", **spec)
    org.hire("top", "top", "haiku", 0, "spent", **spec)
    top, kid, spent = "top", "kid", "spent"
    org.nodes[kid]["frozen"] = dict(LIMIT)
    # ⚠ THE NODE THAT CATCHES THE WIRING, not just the rule. A spend freeze is
    # refused by `_resumable` but SURVIVES only in the document — `tree()`
    # rebuilds `frozen` from a key list that drops `spend`. So if `annotate`
    # is ever changed to pass the payload node it is handed instead of
    # `org.node(id)`, this node comes back `resumable: True` and this check
    # fails. Without it every assertion here passes on a limit freeze, which
    # projects identically either way, and the design decision would be
    # untested. (Verified by mutation: switching annotate to the payload node
    # fails exactly this assertion and nothing else.)
    org.nodes[spent]["frozen"] = {"at": "2026-08-26T09:00:00Z", "until": None,
                                  "until_ts": None, "error": None,
                                  "spend": True, "spend_error": "over"}
    store.save_org(org)

    c = TestClient(api.app)
    r = c.get("/api/orgs/resumecheck")
    assert r.status_code == 200, (r.status_code, r.text[:300])

    seen: list[tuple[str, object]] = []

    def walk(n) -> None:
        seen.append((n["id"], n.get("resumable", "<MISSING>")))
        for ch in n.get("children") or []:
            walk(ch)

    for root in r.json()["roots"]:
        walk(root)

    assert seen, "the payload had no nodes at all — this check proved nothing"
    missing = [nid for nid, v in seen if v == "<MISSING>"]
    assert not missing, f"nodes without `resumable`: {missing}"
    got = dict(seen)
    assert got[kid] is True, f"the frozen live child should be resumable: {got}"
    assert got[top] is False, f"an unfrozen node is not resumable: {got}"
    assert got[spent] is False, (
        f"a spend-frozen node came back resumable: {got} — `annotate` is "
        f"reading the PAYLOAD node, whose `frozen` has had `spend` projected "
        f"away, instead of org.node(id). See supervisor.resumable.")
    # …and the depth actually exercised the recursion, not just the roots
    assert len(seen) >= 3, f"only {len(seen)} node(s) walked: {seen}"


check("GET /api/orgs/{slug} emits `resumable` on every node at every depth",
      annotate_emits_it_everywhere)


# ------------------------------------------------------------- §4 controls
print("\n§4  controls — what would make the above vacuous")


def rule_is_not_constant() -> None:
    """`resumable` must be capable of BOTH answers on inputs that differ only
    in the thing under test. Every §1 check would pass against a function
    that always returned False except the first, and against one that always
    returned True except the rest; this pins that both occur."""
    answers = {sup.resumable(node(frozen=dict(LIMIT))),
               sup.resumable(node(state="archived", frozen=dict(LIMIT)))}
    assert answers == {True, False}, (
        f"resumable() gave {answers} — it is not discriminating, so §1 is "
        f"measuring nothing")


def returns_a_bool_not_the_record() -> None:
    """`_resumable` returns the RECORD or None, and a dict is truthy — so a
    caller writing `resumable(n)` and getting a dict would still 'work' while
    putting a freeze record into the payload. Pin the type."""
    got = sup.resumable(node(frozen=dict(LIMIT)))
    assert got is True and isinstance(got, bool), f"got {type(got).__name__}"


check("resumable() returns both answers on inputs differing only in state",
      rule_is_not_constant)
check("resumable() returns a real bool, never the record", returns_a_bool_not_the_record)


# ------------------------------------------------------------------- report
print()
for n in NOTES:
    print(f"  note: {n}")
if FAILED:
    print(f"\n✗ {len(FAILED)} FAILED, {PASSED} passed\n")
    for f in FAILED:
        print(f"   · {f}")
    sys.exit(1)
print(f"✓ all {PASSED} checks passed\n")
