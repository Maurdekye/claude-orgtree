"""D-199 — a provider is offered, and accepted, only where it is set up.

THE REPORT (user, 2026-08-30): "if a user has codex cli set up but *not* claude
code, will they only see codex hire tokens? or will they see both codex and
claude tokens, even if claude is not configured? i want them to only see the
hire buttons for the agent harnesses they actually have set up."

They saw both, and Claude was the exception that made it so: `providers_payload`
hard-coded `hire_enabled: True` for the claude entry, the API layer hard-coded
`installed: True` beside it, and `provider_hire_gate` deliberately ungated
Claude tiers. Codex and Gemini had honest detection from the day they were
added; Claude never did, because it predated the axis.

TWO HALVES, AND THE UI HALF IS NOT A SUBSTITUTE FOR THIS ONE. The chips not
offering a tier stops a click; it does not stop a script, a peer agent, or a
future surface. Before this, a Claude hire on a machine with no Claude was
ACCEPTED — the seat was spent and the node created — and only failed later when
the turn tried to spawn. A refusal at the door beats a failure at spawn.

    §1  the axis: which tiers Claude owns
    §2  install detection (the thing that was a literal True)
    §3  the payload each family publishes, in all three states
    §4  the hire gate, now including Claude
    §5  controls: what would make the above vacuous

    python backend/tests/test_provider_hire_availability.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-hireavail-")
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

from orgtree import api, providers, supervisor as sup      # noqa: E402
from orgtree.ledger import USER, LedgerError, Org          # noqa: E402

FAILED: list[str] = []
PASSED = 0


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception as e:                                     # noqa: BLE001
        FAILED.append(f"{label}\n      {type(e).__name__}: {e}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


def expect_error(fn, *needles: str) -> None:
    try:
        fn()
    except LedgerError as e:
        for n in needles:
            assert n in str(e).lower(), f"message missing {n!r}: {e}"
    else:
        raise AssertionError("expected a LedgerError, none raised")


class Fake:
    """Pin Claude's install state and signed-in state for one check."""

    def __init__(self, installed: bool, signed_in: bool) -> None:
        self.installed, self.signed_in = installed, signed_in

    def __enter__(self):
        self._i = sup.claude_install_state
        self._a = api.accounts.live_identity
        sup.claude_install_state = lambda force=False: {           # type: ignore[assignment]
            "installed": self.installed, "path": "/x", "source": "path"}
        api.accounts.live_identity = lambda: (                     # type: ignore[assignment]
            {"uuid": "u1", "email": "a@b"} if self.signed_in else {})
        return self

    def __exit__(self, *a) -> None:
        sup.claude_install_state = self._i                         # type: ignore[assignment]
        api.accounts.live_identity = self._a                       # type: ignore[assignment]


def org() -> Org:
    o = Org.create("d199")
    o.d["max_top_grant"] = 200
    return o


# ------------------------------------------------------------------ §1 axis
print("\n§1  which tiers Claude owns")


def claude_axis() -> None:
    assert set(providers.CLAUDE_TIERS) == {"haiku", "sonnet", "opus", "fable"}, \
        providers.CLAUDE_TIERS
    for t in providers.CLAUDE_TIERS:
        assert t not in providers.CODEX_TIERS and t not in providers.GEMINI_TIERS


def claude_tiers_reads_the_axis() -> None:
    """One rule, not two: `claude_tiers()` used to re-derive membership inline
    with the same `not in CODEX / not in GEMINI` test."""
    assert [t["tier"] for t in providers.claude_tiers()] \
        == sorted(providers.CLAUDE_TIERS, key=lambda t: providers.CLAUDE_TIERS[t])


check("CLAUDE_TIERS is exactly the non-codex, non-gemini family", claude_axis)
check("claude_tiers() reads that one table", claude_tiers_reads_the_axis)


# ------------------------------------------------------- §2 install detection
print("\n§2  install detection — the field that was a literal True")


def broken_override_is_not_installed() -> None:
    """The trust split `codex_status` documents: an env override is taken as
    the path to USE but is not proof of install. Reporting "installed" for a
    broken override would send the user to `claude login` instead of to their
    own setting."""
    old = os.environ.get("ORGTREE_CLAUDE")
    os.environ["ORGTREE_CLAUDE"] = os.path.join(_TMP, "nope", "claude.exe")
    try:
        st = sup.claude_install_state(force=True)
        assert st["installed"] is False, st
        assert st["source"] == "env", st
    finally:
        if old is None:
            del os.environ["ORGTREE_CLAUDE"]
        else:
            os.environ["ORGTREE_CLAUDE"] = old
        sup.claude_install_state(force=True)


def a_real_file_is_installed() -> None:
    """The leg that must hold: detection that answers False for everything
    would 'fix' the report by hiding Claude on every machine, including the
    ones that have it."""
    exe = os.path.join(_TMP, "claude-real.exe")
    with open(exe, "w", encoding="utf-8") as f:
        f.write("#")
    old = os.environ.get("ORGTREE_CLAUDE")
    os.environ["ORGTREE_CLAUDE"] = exe
    try:
        assert sup.claude_install_state(force=True)["installed"] is True
    finally:
        if old is None:
            del os.environ["ORGTREE_CLAUDE"]
        else:
            os.environ["ORGTREE_CLAUDE"] = old
        sup.claude_install_state(force=True)


check("a broken ORGTREE_CLAUDE override reads as NOT installed",
      broken_override_is_not_installed)
check("a real file reads as installed (the leg that must hold)",
      a_real_file_is_installed)


# ------------------------------------------------------------- §3 the payload
print("\n§3  what each family publishes, in all three states")


def payload(installed: bool, connected: bool) -> dict:
    p = providers.providers_payload(
        {"installed": installed, "connected": connected})
    return next(x for x in p["providers"] if x["id"] == "claude")


def claude_available() -> None:
    e = payload(True, True)
    assert e["hire_enabled"] is True and e["reason"] is None, e


def claude_signed_out() -> None:
    e = payload(True, False)
    assert e["hire_enabled"] is False, e
    assert "not signed in" in (e["reason"] or ""), e
    # the SHOWN-disabled case: the reason must carry a remedy, since the UI
    # renders it as the tooltip on a visible-but-disabled row
    assert "claude" in (e["reason"] or "").lower(), e


def claude_absent() -> None:
    e = payload(False, False)
    assert e["hire_enabled"] is False, e
    assert "not installed" in (e["reason"] or ""), e
    assert "npm install" in (e["reason"] or ""), e


def every_family_answers_the_same_question() -> None:
    """The shape the UI's one rule depends on: each entry carries
    hire_enabled + status.installed + reason, so 'absent' and 'signed out' are
    distinguishable per provider."""
    p = providers.providers_payload({"installed": True, "connected": True})
    for e in p["providers"]:
        assert "hire_enabled" in e, e
        assert "reason" in e, e
        assert "installed" in (e.get("status") or {}), e


check("installed + signed in  -> hire_enabled, no reason", claude_available)
check("installed, signed out  -> refused, reason names the login",
      claude_signed_out)
check("not installed          -> refused, reason names the install",
      claude_absent)
check("all three providers publish the same three fields",
      every_family_answers_the_same_question)


# ---------------------------------------------------------------- §4 the gate
print("\n§4  the hire gate — Claude included since D-199")


def gate_refuses_claude_when_absent() -> None:
    with Fake(installed=False, signed_in=False):
        expect_error(lambda: api.provider_hire_gate(org(), "sonnet"),
                     "not installed")


def gate_refuses_claude_when_signed_out() -> None:
    with Fake(installed=True, signed_in=False):
        expect_error(lambda: api.provider_hire_gate(org(), "opus"),
                     "not signed in")


def gate_passes_when_claude_is_set_up() -> None:
    """THE LEG THAT MUST HOLD. Every other check in §4 asserts a refusal, and
    all of them would pass if the gate refused Claude unconditionally — which
    would brick hiring on every machine that HAS Claude."""
    with Fake(installed=True, signed_in=True):
        for t in providers.CLAUDE_TIERS:
            api.provider_hire_gate(org(), t)


def gate_ignores_a_missing_tier() -> None:
    with Fake(installed=False, signed_in=False):
        api.provider_hire_gate(org(), None)
        api.provider_hire_gate(org(), "")


check("a Claude hire is refused when the CLI is absent",
      gate_refuses_claude_when_absent)
check("a Claude hire is refused when nobody is signed in",
      gate_refuses_claude_when_signed_out)
check("...and PASSES on a machine that has Claude (the leg that must hold)",
      gate_passes_when_claude_is_set_up)
check("no tier is not this gate's business", gate_ignores_a_missing_tier)


# ------------------------------------------------------------- §5 the controls
print("\n§5  controls — what would make the above vacuous")


def unknown_tier_is_not_a_claude_message() -> None:
    """`provider_of` answers 'claude' for an UNKNOWN tier deliberately, so a
    gate keyed on it alone would tell someone who typo'd a tier name to go
    install Claude Code. The gate keys on CLAUDE_TIERS membership instead.
    This is why that constant had to exist."""
    with Fake(installed=False, signed_in=False):
        api.provider_hire_gate(org(), "gpt-9")      # must not raise
        assert providers.provider_of("gpt-9") == "claude"


def codex_still_gated_independently() -> None:
    """Claude's new branch must not have swallowed the codex one: it sits
    after the `tier not in CODEX_TIERS` early return, so a regression there
    would silently ungate codex."""
    saved = providers.codex_status
    providers.codex_status = lambda force=False: {                 # type: ignore[assignment]
        "installed": False, "connected": False}
    try:
        expect_error(lambda: api.provider_hire_gate(org(), "sol"),
                     "not installed")
    finally:
        providers.codex_status = saved                             # type: ignore[assignment]


def the_docstring_still_claims_every_door() -> None:
    """D-203 replaces the repeatedly-wrong count with a named checklist."""
    import inspect
    doc = inspect.getdoc(api.provider_hire_gate) or ""
    for door in ("user hire", "agent hire", "user model switch",
                 "agent model switch", "user rehire WITH a tier override",
                 "user plain rehire", "agent plain rehire"):
        assert door in doc, (door, doc)
    assert "FIVE doors" not in doc and "all FIVE" not in doc, doc
    assert "CLAUDE IS GATED TOO" in doc, (
        "the docstring must not go back to saying Claude is ungated")


check("an UNKNOWN tier is not refused with a Claude message",
      unknown_tier_is_not_a_claude_message)
check("codex is still gated on its own terms", codex_still_gated_independently)
check("the gate's docstring still describes what it does",
      the_docstring_still_claims_every_door)


# --------------------------------------------------------- §6 gpt-reserve
print("\n§6  gpt-reserve — its own gate, beside the family's")


def _with_codex_kind(kind):
    """`codex_status` pinned CONNECTED with a chosen login `kind` — the
    family-level checks above already pass, so any refusal here is
    gpt-reserve's OWN rule, not a re-run of §4's."""
    saved = providers.codex_status
    providers.codex_status = lambda force=False: {                 # type: ignore[assignment]
        "installed": True, "connected": True, "kind": kind}
    return saved


def reserve_refused_on_api_key() -> None:
    """Reserve capacity is a ChatGPT-subscription perk, never billed
    per-token — an api-key session is genuinely connected (sol/terra/luna
    hire fine there) but was never granted reserve capacity at all."""
    saved = _with_codex_kind("api-key")
    try:
        expect_error(lambda: api.provider_hire_gate(org(), "gpt-reserve"),
                     "chatgpt")
        api.provider_hire_gate(org(), "sol")      # the leg that must hold
    finally:
        providers.codex_status = saved                             # type: ignore[assignment]


def reserve_passes_on_chatgpt() -> None:
    saved = _with_codex_kind("chatgpt")
    try:
        api.provider_hire_gate(org(), "gpt-reserve")
    finally:
        providers.codex_status = saved                             # type: ignore[assignment]


def reserve_gate_never_touches_the_other_three() -> None:
    """Anti-vacuity: a gate that refused every codex tier on api-key would
    also make `reserve_refused_on_api_key` look right for the wrong reason."""
    saved = _with_codex_kind("api-key")
    try:
        for t in ("luna", "terra", "sol"):
            api.provider_hire_gate(org(), t)
    finally:
        providers.codex_status = saved                             # type: ignore[assignment]


check("gpt-reserve is refused on an api-key session, naming ChatGPT — "
      "sol still hires fine there", reserve_refused_on_api_key)
check("gpt-reserve passes on a ChatGPT-subscription session",
      reserve_passes_on_chatgpt)
check("…and its own rule never leaks onto luna/terra/sol",
      reserve_gate_never_touches_the_other_three)


# ------------------------------------------------------------------ summary
print(f"\n{'=' * 60}")
if FAILED:
    print(f"FAILED {len(FAILED)} / {PASSED + len(FAILED)}")
    for f in FAILED:
        print(f"  x {f}")
    sys.exit(1)
print(f"PASSED {PASSED}/{PASSED}")
