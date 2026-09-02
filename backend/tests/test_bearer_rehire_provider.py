"""D-197 — a rehire may not carry a node across providers.

THE REPORT (user, 2026-08-29): "cant select non-claude models for
knowledgebearer rehire. is that by design or just a system quirk?"

Both, in different halves, and that is the point of this suite.

The PANEL was a quirk: `desk.tsx` hard-coded `['haiku','sonnet','opus']`, a
list written before fable, codex and gemini existed. It under-offered its own
provider (`fable` was missing) and silently omitted the others.

The BACKEND was the actual defect, in the opposite direction: `ledger.rehire`
validated only `tier in d["tiers"]`, so it ACCEPTED precisely the rehire the
interface had merely forgotten to offer, and — unlike hire and switch_model —
never called `provider_hire_gate`, whose own docstring claimed to cover every
door. A UI-only fix would have left that open and looked complete.

WHY THE RULE IS A REFUSAL AND NOT A WARNING. A session cannot cross providers,
and the two directions fail differently:

  · to claude — LOUD. The supervisor's journal store makes `transcript_path`
    hit for a codex thread, so `_build_cmd` takes its resume branch and hands
    the Claude CLI a `--resume <threadId>` it never issued.
  · away from claude — SILENT, and worse. The provider legs resume only when
    `session_id` equals the harvested `codex_thread`/`gemini_session`; a claude
    id never does, so the leg quietly starts a FRESH thread. An empty session
    wakes wearing the bearer's name and PRESENTS AS INSTITUTIONAL MEMORY.
    Someone consults it, gets fluent answers drawn from nothing, and has no way
    to tell.

`ledger.rehire` already refuses exactly that for a `lost` generation, so this
is an existing rule applied at another door rather than a new policy.

    §1  the axis itself (providers.provider_of)
    §2  rehire refuses a crossing, both directions
    §3  ...and still permits every same-provider tier, fable included
    §4  the refusal is ATOMIC — it precedes the first mutation
    §5  re-seed is exempt (no session to preserve)
    §6  the consult door: a preserving bearer with a foreign session
    §7  controls: what would make the above vacuous

    python backend/tests/test_bearer_rehire_provider.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-bearer-rehire-")
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

from orgtree import providers, supervisor as sup            # noqa: E402
from orgtree.ledger import USER, LedgerError, Org           # noqa: E402

FAILED: list[str] = []
PASSED = 0


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


def expect_error(fn, *needles: str) -> None:
    try:
        fn()
    except LedgerError as e:
        for n in needles:
            assert n in str(e).lower(), f"message missing {n!r}: {e}"
    else:
        raise AssertionError("expected a LedgerError, none raised")


def org_with(tier: str, *, name: str = "bearer") -> Org:
    """An org holding one ARCHIVED agent that ran on `tier` — the state a
    rehire acts on."""
    o = Org.create("d197")
    o.d["max_top_grant"] = 200
    o.hire(USER, None, tier, 20, name)
    o.retire(USER, name)
    return o


# ------------------------------------------------------------------ §1 axis
print("\n§1  the tier→provider axis (the ONE implementation, D-196/D-182)")


def axis_maps_each_family() -> None:
    for t in ("haiku", "sonnet", "opus", "fable"):
        assert providers.provider_of(t) == "claude", t
    for t in ("gpt-reserve", "luna", "terra", "sol"):
        assert providers.provider_of(t) == "openai", t
    for t in ("flash", "pro"):
        assert providers.provider_of(t) == "google", t


def axis_defaults_unknown_to_claude() -> None:
    """Deliberate: this decides whether a change CROSSES, and the safe answer
    for an unrecognised tier is 'the default lane'. A wrong crossing verdict
    would reset a session that did not need resetting."""
    assert providers.provider_of("gpt-9") == "claude"
    assert providers.provider_of("") == "claude"


check("every known tier maps to its provider", axis_maps_each_family)
check("an unknown tier answers 'claude', not a crash",
      axis_defaults_unknown_to_claude)


# -------------------------------------------------------------- §2 refusals
print("\n§2  rehire refuses a provider crossing — both directions")


def claude_to_codex_refused() -> None:
    """THE SILENT DIRECTION. Before D-197 this returned success and the node
    woke on a fresh empty codex thread under the bearer's own name."""
    o = org_with("opus")
    expect_error(lambda: o.rehire(USER, "bearer", tier="sol"),
                 "cannot cross providers", "wake up empty")
    assert o.nodes["bearer"]["state"] == "archived"
    assert o.nodes["bearer"]["model"] == "opus", "the tier must not be written"


def claude_to_gemini_refused() -> None:
    o = org_with("fable")
    expect_error(lambda: o.rehire(USER, "bearer", tier="pro"),
                 "cannot cross providers")


def codex_to_claude_refused() -> None:
    """The LOUD direction — it would emit `--resume <threadId>` at a CLI that
    never issued it. That is the crash that destroyed a real session."""
    o = org_with("sol")
    expect_error(lambda: o.rehire(USER, "bearer", tier="opus"),
                 "cannot cross providers")


def gemini_to_codex_refused() -> None:
    """Neither side is claude — the rule is about the BOUNDARY, not about a
    privileged home provider."""
    o = org_with("flash")
    expect_error(lambda: o.rehire(USER, "bearer", tier="terra"),
                 "cannot cross providers")


def message_names_both_providers_and_a_way_out() -> None:
    o = org_with("sol")
    try:
        o.rehire(USER, "bearer", tier="opus")
    except LedgerError as e:
        m = str(e)
        # the LABELS a person reads, not the ids "openai"/"google"
        assert "Codex" in m and "Claude" in m, m
        assert "openai" not in m and "google" not in m, (
            f"a message shown to a person must use the provider's UI name: {m}")
        assert "'sol'" in m, "the message must name the tier it DID run"
        assert "switch its model" in m, "a refusal must name the way forward"
    else:
        raise AssertionError("expected a refusal")


check("claude → codex is refused (the SILENT direction)", claude_to_codex_refused)
check("claude → gemini is refused", claude_to_gemini_refused)
check("codex → claude is refused (the crash that killed a session)",
      codex_to_claude_refused)
check("gemini → codex is refused — it is the boundary, not a home provider",
      gemini_to_codex_refused)
check("the refusal names both providers, the old tier, and the way out",
      message_names_both_providers_and_a_way_out)


# ------------------------------------------------------------ §3 permitted
print("\n§3  ...and every SAME-provider tier still works")


def fable_is_allowed_for_a_claude_bearer() -> None:
    """THE UNDER-OFFER, from the other end. `fable` resumes a claude bearer
    perfectly and the picker simply never listed it. If the new rule were
    written as 'claude bearers keep the old three', this is what would fail."""
    o = org_with("opus")
    o.rehire(USER, "bearer", tier="fable")
    assert o.nodes["bearer"]["state"] == "live"
    assert o.nodes["bearer"]["model"] == "fable"


def cheaper_same_provider_consult_still_works() -> None:
    """№16 — the whole reason the override exists: consult a bearer at a
    cheaper tier than it ran at."""
    o = org_with("sol")
    o.rehire(USER, "bearer", tier="luna")
    assert o.nodes["bearer"]["model"] == "luna"
    assert o.nodes["bearer"]["state"] == "live"


def gemini_bearer_keeps_its_family() -> None:
    o = org_with("pro")
    o.rehire(USER, "bearer", tier="flash")
    assert o.nodes["bearer"]["model"] == "flash"


def no_override_is_untouched() -> None:
    """A plain rehire restores the node as it was and never consults the axis
    — including for a codex node, which must not need its provider present
    merely to come back."""
    o = org_with("sol")
    o.rehire(USER, "bearer")
    assert o.nodes["bearer"]["state"] == "live"
    assert o.nodes["bearer"]["model"] == "sol"


def same_tier_override_is_a_noop_not_a_refusal() -> None:
    o = org_with("sol")
    o.rehire(USER, "bearer", tier="sol")
    assert o.nodes["bearer"]["model"] == "sol"


check("a claude bearer may be rehired as FABLE — the missing option",
      fable_is_allowed_for_a_claude_bearer)
check("a codex bearer consults cheaper at luna (№16)",
      cheaper_same_provider_consult_still_works)
check("a gemini bearer may move flash↔pro", gemini_bearer_keeps_its_family)
check("a rehire with NO tier override is untouched", no_override_is_untouched)
check("re-stating the node's own tier is a no-op, not a refusal",
      same_tier_override_is_a_noop_not_a_refusal)


# ------------------------------------------------------------- §4 atomicity
print("\n§4  the refusal precedes the first mutation")


def refusal_does_not_wake_an_archived_superior() -> None:
    """The property the ATOMICITY comment above the tier check exists to
    protect. Rehiring a deep node rehires its archived ancestors FIRST, so a
    check placed after that walk refuses only once credits have been spent and
    notices sent. The crossing check sits with the tier-name check, before any
    of it."""
    o = Org.create("d197atomic")
    o.d["max_top_grant"] = 200
    o.hire(USER, None, "opus", 40, "boss")
    o.hire(USER, "boss", "opus", 5, "kid")
    o.retire(USER, "kid")
    o.retire(USER, "boss")
    free_before = o.free("boss") if o.nodes["boss"]["state"] == "live" else None

    expect_error(lambda: o.rehire(USER, "kid", tier="sol"),
                 "cannot cross providers")

    assert o.nodes["boss"]["state"] == "archived", (
        "the archived superior was woken by a rehire that then refused")
    assert o.nodes["kid"]["state"] == "archived"
    assert free_before is None


check("a refused crossing leaves an archived superior asleep",
      refusal_does_not_wake_an_archived_superior)


# -------------------------------------------------------------- §5 re-seed
print("\n§5  an unrecoverable node re-seeds — no session to protect")


def unrecoverable_ignores_the_crossing_rule() -> None:
    """A re-seed starts a FRESH session by definition, so there is no
    conversation to strand and the tier override is already documented as
    ignored-with-a-warning. Refusing here would block a legitimate recovery on
    a rule about preserving something that no longer exists."""
    o = org_with("sol")
    o.nodes["bearer"]["state"] = "unrecoverable"
    r = o.rehire(USER, "bearer", tier="opus")
    assert o.nodes["bearer"]["model"] == "sol", "re-seed keeps its own tier"
    assert any("ignored" in w for w in r.get("warnings", [])), r


check("an unrecoverable node re-seeds instead of refusing",
      unrecoverable_ignores_the_crossing_rule)


# -------------------------------------------------------- §6 the consult door
print("\n§6  the consult door — a preserving bearer with a foreign session")


def foreign_reads_the_markers_not_the_tier() -> None:
    # provider IDs — the same vocabulary as providers.provider_of, so one
    # PROVIDER_LABEL lookup renders either of them for a person
    assert sup._foreign_session_provider(
        {"session_id": "t1", "codex_thread": "t1"}) == "openai"
    assert sup._foreign_session_provider(
        {"session_id": "g1", "gemini_session": "g1"}) == "google"


def a_stale_marker_is_not_foreign() -> None:
    """The equality is the SAME one the provider legs resume on, so a re-mint
    (fresh hire, compaction, re-seed) breaks it here and there together. A
    broken equality means the marker is stale and the node is claude-native."""
    assert sup._foreign_session_provider(
        {"session_id": "new", "codex_thread": "old"}) is None
    assert sup._foreign_session_provider({"session_id": "plain"}) is None
    assert sup._foreign_session_provider({}) is None


def consult_refuses_in_writing() -> None:
    """`bearer_state == "preserving"` resumes+forks UNCONDITIONALLY — it has no
    `--session-id` fallback, because a consult that cannot reach the transcript
    has nothing to consult. So a foreign session has no safe path at all here,
    not even the silent-fresh one. It must say so rather than emit a doomed
    `--resume`."""
    o = Org.create("d197consult")
    o.d["max_top_grant"] = 200
    o.hire(USER, None, "opus", 10, "oracle")
    n = o.nodes["oracle"]
    n["bearer_state"] = "preserving"
    n["session_id"] = "thread-abc"
    n["codex_thread"] = "thread-abc"     # a codex session on a claude tier
    try:
        sup._build_cmd(o, "oracle")
    except RuntimeError as e:
        m = str(e)
        assert "Codex" in m, m
        assert "cannot cross providers" in m, m
        assert "reading is free" in m, "a refusal must name the way forward"
    else:
        raise AssertionError("expected a RuntimeError, none raised")


def a_native_preserving_bearer_still_forks() -> None:
    """THE LEG THAT MUST NOT BREAK. Every other check in §6 asserts something
    is EXCLUDED, and all of them would pass if the guard refused every consult
    — which would silently disable the oracle feature outright."""
    o = Org.create("d197native")
    o.d["max_top_grant"] = 200
    o.hire(USER, None, "opus", 10, "oracle")
    n = o.nodes["oracle"]
    n["bearer_state"] = "preserving"
    cmd = sup._build_cmd(o, "oracle")
    assert "--fork-session" in cmd, cmd
    assert "--resume" in cmd, cmd


check("foreign-session detection reads the harvested markers",
      foreign_reads_the_markers_not_the_tier)
check("a stale marker reads as claude-native, not foreign",
      a_stale_marker_is_not_foreign)
check("a cross-provider consult refuses with a written reason",
      consult_refuses_in_writing)
check("a NATIVE preserving bearer still resumes and forks (the leg that "
      "must hold)", a_native_preserving_bearer_still_forks)


# -------------------------------------------------------------- §7 controls
print("\n§7  controls — what would make the above vacuous")


def the_gate_covers_the_rehire_door() -> None:
    """The docstring asserted completeness ('all four doors') while
    rehire-with-a-tier went ungated — the gap was invisible BECAUSE the
    sentence claimed there was none. Pin the count to the call sites."""
    import inspect
    from orgtree import api

    doc = inspect.getdoc(api.provider_hire_gate) or ""
    assert "FIVE doors" in doc, (
        "provider_hire_gate's door count changed — update it and this check "
        "together, or the next missing door hides the same way")
    src = inspect.getsource(api._org_op_locked)
    assert src.count("provider_hire_gate(org,") >= 3, (
        "the user ops path must gate hire, switch_model AND a tier-overriding "
        f"rehire; found {src.count('provider_hire_gate(org,')}")


def agent_rehire_still_has_no_tier() -> None:
    """The reason the agent-side `orgtree_rehire` is not a sixth door. If a
    `tier` is ever added to its schema it becomes one, and this fails."""
    from orgtree import mcptool

    tool = next(t for t in mcptool.TOOLS if t["name"] == "orgtree_rehire")
    assert "tier" not in tool["inputSchema"].get("properties", {}), (
        "orgtree_rehire grew a `tier` — gate it in api.py like the others")


def unknown_tier_still_refused_first() -> None:
    """The crossing check must not shadow the tier-name check: 'gpt-9' maps to
    claude by the safe default, so a claude bearer would otherwise sail past
    it and be refused later, after the ancestor walk."""
    o = org_with("opus")
    expect_error(lambda: o.rehire(USER, "bearer", tier="gpt-9"), "unknown tier")


check("provider_hire_gate now covers the rehire door, and says so",
      the_gate_covers_the_rehire_door)
check("the agent rehire tool still takes no tier", agent_rehire_still_has_no_tier)
check("an unknown tier is still refused by name, before the crossing rule",
      unknown_tier_still_refused_first)


# ------------------------------------------------------------------ summary
print(f"\n{'=' * 60}")
if FAILED:
    print(f"FAILED {len(FAILED)} / {PASSED + len(FAILED)}")
    for f in FAILED:
        print(f"  ✗ {f}")
    sys.exit(1)
print(f"PASSED {PASSED}/{PASSED}")
