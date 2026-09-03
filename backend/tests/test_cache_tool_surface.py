"""The MCP TOOL SURFACE is a cache prefix input.

WHY THIS SUITE EXISTS
---------------------
User question, 2026-09-03: "does mcp tool list cause the cache card to turn
red? because if a tool list is different, it should affect the cache hit
status, even if it means more tools were added". The answer was yes for the
things the classifier already hashed — the tool GRANT and the MCP launch
config — and NO for the one that matters most in practice.

`_cache_semantic_inputs` hashes `--mcp-config`, which is the canonical JSON of
`{"mcpServers": chosen}`: how to LAUNCH each server, not what that server
exposes. So a server that gained or lost tools under an identical launch spec
moved the provider-visible prefix without moving any cache input, and the card
stayed GREEN while the next turn missed. That is the green-when-it-should-be-red
lie, and it is not theoretical: orgtree's own MCP server gained
`orgtree_interrupt` on 2026-09-03 with a byte-identical launch spec, so every
Claude-lane agent's card was green across a change that invalidated all of them.

`components["mcp_surface"]` closes it. Two properties matter as much as the
detection itself, and §2 and §3 are here because getting either wrong is worse
than the hole was — a FALSE cold is one `_cache_precompact_decision` can ACT
on, compacting a node that did not need it.
"""
from __future__ import annotations

import os
import sys
import tempfile
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_REPO, "backend"))
os.environ.setdefault("ORGTREE_DATA", tempfile.mkdtemp(prefix="orgtree-toolsurface-"))

from orgtree import cachecontinuity as C, store, supervisor as S  # noqa: E402
from orgtree.ledger import USER                                   # noqa: E402

NOW = 1788253200.0
PASS = 0
FAIL = 0


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS, FAIL
    try:
        fn()
    except Exception as exc:                                    # noqa: BLE001
        FAIL += 1
        print(f"  FAIL  {label}: {exc}")
    else:
        PASS += 1
        print(f"  ok {PASS:>2}  {label}")


def eq(got: Any, want: Any) -> None:
    if got != want:
        raise AssertionError(f"got {got!r}; want {want!r}")


_SEQ = [0]


def agent() -> tuple[Any, str, str]:
    _SEQ[0] += 1
    slug = f"zz-tool-surface-{_SEQ[0]}"
    org = store.create_org(slug)
    org.hire(USER, None, "haiku", 4, "agent")
    return org, "agent", slug


def snap(org: Any, nid: str) -> dict[str, Any]:
    return S._cache_snapshot(org, nid, now=NOW, include_history=False)


def observe(slug: str, nid: str, names: set[str] | None) -> None:
    """Publish (or clear) the tool surface the provider process reported."""
    if names is None:
        S._state.pop((slug, nid), None)
        return
    S.state(slug, nid)["mcp_tool_names"] = set(names)


def book(row: dict[str, Any]) -> dict[str, Any]:
    last = {k: v for k, v in row.items() if not k.endswith("_history_relation")}
    receipt = dict(last)
    receipt.update({"observed_at": C.iso_us(NOW - 60), "ttl_seconds": 3600,
                    "expires_at": C.iso_us(NOW - 60 + 3600)})
    return {"last_turn": last, "receipt": receipt}


def card(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    cur = dict(after)
    cur["last_turn_history_relation"] = "same_or_appended"
    cur["receipt_history_relation"] = "same_or_appended"
    return C.public(C.classify(cur, book(before), NOW), generation="g",
                    precompact_action="not_applicable", precompact_reason="")


# ── §1 a changed tool surface is a changed prefix ─────────────────────────

def a_tool_added_by_the_server_is_a_cache_invalidation() -> None:
    """THE REGRESSION. Same grant, same launch config, one more tool."""
    org, nid, slug = agent()
    observe(slug, nid, {f"mcp__orgtree__t{i}" for i in range(40)})
    before = snap(org, nid)
    observe(slug, nid, {f"mcp__orgtree__t{i}" for i in range(41)})
    after = snap(org, nid)
    # The launch config genuinely did NOT move — that is the whole point.
    eq(before["components"]["tools"], after["components"]["tools"])
    assert before["components"]["mcp_surface"] != after["components"]["mcp_surface"]
    row = card(before, after)
    eq((row["readiness"], row["readiness_cause"]), ("not_ready", "prefix_changed"))
    eq(row["changed_inputs"], ["mcp_surface"])


def a_tool_removed_is_equally_a_change() -> None:
    org, nid, slug = agent()
    observe(slug, nid, {f"t{i}" for i in range(41)})
    before = snap(org, nid)
    observe(slug, nid, {f"t{i}" for i in range(40)})
    row = card(before, snap(org, nid))
    eq(row["readiness_cause"], "prefix_changed")


def names_not_a_count_one_swapped_for_another() -> None:
    """⚠ WHY NAMES AND NOT `mcp_tool_count`. One tool added and one removed is
    the same number and a genuinely different prefix; a count cannot see it,
    and the node already carries a count that would have looked sufficient."""
    org, nid, slug = agent()
    observe(slug, nid, {f"t{i}" for i in range(40)})
    before = snap(org, nid)
    observe(slug, nid, {f"t{i}" for i in range(39)} | {"something-else"})
    after = snap(org, nid)
    eq(len(before["components"]["mcp_surface"]) > 0, True)
    assert before["components"]["mcp_surface"] != after["components"]["mcp_surface"], \
        "a same-size surface with different names hashed identically"
    eq(card(before, after)["readiness_cause"], "prefix_changed")


def an_unchanged_surface_stays_green() -> None:
    """THE CONTROL. Without it every check above passes on a classifier that
    simply reports everything cold."""
    org, nid, slug = agent()
    observe(slug, nid, {f"t{i}" for i in range(40)})
    before = snap(org, nid)
    row = card(before, snap(org, nid))
    eq((row["readiness"], row["readiness_cause"]), ("ready", "receipt_valid"))
    eq(row["changed_inputs"], [])


# ── §2 unobserved is NOT changed ──────────────────────────────────────────

def a_surface_that_cannot_be_observed_is_not_a_change() -> None:
    """The process is gone, so nothing reports a surface. That is an absence
    of evidence, not evidence of a change — the rule `_namespace_changed` and
    the history relation already follow. Getting this wrong would paint every
    idle agent red the moment its CLI exited."""
    org, nid, slug = agent()
    observe(slug, nid, {f"t{i}" for i in range(40)})
    before = snap(org, nid)
    observe(slug, nid, None)                       # process gone
    after = snap(org, nid)
    assert "mcp_surface" not in after["components"], \
        "an unobserved surface must be OMITTED, never a placeholder"
    row = card(before, after)
    eq((row["readiness"], row["readiness_cause"]), ("ready", "receipt_valid"))


def a_node_that_has_never_run_is_not_cold_for_it() -> None:
    org, nid, slug = agent()
    before = snap(org, nid)
    assert "mcp_surface" not in before["components"]
    observe(slug, nid, {f"t{i}" for i in range(40)})
    row = card(before, snap(org, nid))
    eq((row["readiness"], row["readiness_cause"]), ("ready", "receipt_valid"))


# ── §3 the migration: nobody goes red because this shipped ────────────────

def a_row_persisted_before_this_component_existed_stays_green() -> None:
    """⚠ THE EXPENSIVE ONE. Every `last_turn` written before `mcp_surface`
    existed lacks it. If absence compared as a difference, the first poll after
    deploy would report EVERY Claude agent cold — and a cold verdict is not
    merely displayed, `_cache_precompact_decision` can act on it and
    cheap-compact a node above threshold. A destructive false cold is exactly
    the schema-migration-wearing-a-defect's-label trap `legacy_readiness` and
    the `_namespace_changed` account carve-out exist to avoid."""
    org, nid, slug = agent()
    observe(slug, nid, {f"t{i}" for i in range(40)})
    current = snap(org, nid)
    legacy = dict(current)
    legacy["components"] = {k: v for k, v in current["components"].items()
                            if k != "mcp_surface"}
    row = card(legacy, current)
    eq((row["readiness"], row["readiness_cause"]), ("ready", "receipt_valid"))
    eq(row["changed_inputs"], [])


def the_component_is_declared_and_orderable() -> None:
    """A changed component the UI cannot name is a red card with an empty
    tooltip, so the label table has to know about it."""
    assert "mcp_surface" in C._COMPONENT_ORDER
    assert "mcp_surface" in C._PREFIX_COMPONENTS
    assert "mcp_surface" in C._OBSERVED_COMPONENTS


check("a tool ADDED by the server, same launch config, is an invalidation",
      a_tool_added_by_the_server_is_a_cache_invalidation)
check("a tool REMOVED is equally a change", a_tool_removed_is_equally_a_change)
check("NAMES, not a count: one tool swapped for another is caught",
      names_not_a_count_one_swapped_for_another)
check("CONTROL · an unchanged surface stays green",
      an_unchanged_surface_stays_green)
check("an unobservable surface is not a change (the process is gone)",
      a_surface_that_cannot_be_observed_is_not_a_change)
check("a node that has never run is not cold for lack of an observation",
      a_node_that_has_never_run_is_not_cold_for_it)
check("MIGRATION · a row persisted before this component stays green",
      a_row_persisted_before_this_component_existed_stays_green)
check("the component is declared in the order and observed tables",
      the_component_is_declared_and_orderable)

print()
if FAIL:
    print(f"{FAIL} FAILED, {PASS} PASSED")
    sys.exit(1)
print(f"ALL {PASS} CHECKS PASS")
