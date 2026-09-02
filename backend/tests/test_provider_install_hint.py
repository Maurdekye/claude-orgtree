"""D-202 — where the install command lives, once the UI stops carrying it.

THE RULING (user, 2026-08-30): if codex isn't installed at all, then codex
shouldn't appear anywhere in the UI whatsoever; it should be entirely absent
— and the same for the other optional provider.

That is mostly a frontend change (frontend/tests/provabsent.test.tsx), but it
knocks a hole in the backend that is easy to miss: three refusals in
`provider_hire_gate` told the user to go and read "the accounts panel's Codex
section", and D-202 DELETES that section on exactly the machines that get
those refusals. The message pointed at a panel that had been removed on
purpose. Claude's was wrong in a second way — its section survives, but it
carries one small line and never had an install command to find.

So the gate's refusal became the ONLY place a user is told how to install a
provider, which means it can no longer be the copy that drifts. `install_hint`
is now the single source and both the payload and the gate read it.

    §1  the hint itself — the command that produces a CLI this box will FIND
    §2  the payload's reasons are built from it
    §3  the gate's refusals are built from it, and point at nothing deleted
    §4  controls: what would make the above vacuous

    python backend/tests/test_provider_install_hint.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-installhint-")
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
from orgtree.ledger import LedgerError, Org                # noqa: E402

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


def refusal(fn) -> str:
    """The message a refusal actually carries — this file is about wording,
    so it reads the string rather than only asserting that it raised."""
    try:
        fn()
    except LedgerError as e:
        return str(e)
    raise AssertionError("expected a LedgerError, none raised")


class Absent:
    """Every provider missing from this machine, for one check.

    Patches the three status readers the gate and the payload consult. Codex
    and Antigravity are dicts by contract; Claude comes through `supervisor`.
    """

    def __enter__(self):
        gone = {"installed": False, "connected": False, "path": "",
                "source": "", "kind": None}
        self._c = providers.codex_status
        self._g = providers.antigravity_status
        self._i = sup.claude_install_state
        providers.codex_status = lambda *a, **k: dict(gone)      # type: ignore[assignment]
        providers.antigravity_status = lambda *a, **k: dict(gone)  # type: ignore[assignment]
        sup.claude_install_state = lambda force=False: {         # type: ignore[assignment]
            "installed": False, "path": "", "source": ""}
        return self

    def __exit__(self, *a) -> None:
        providers.codex_status = self._c                         # type: ignore[assignment]
        providers.antigravity_status = self._g                   # type: ignore[assignment]
        sup.claude_install_state = self._i                       # type: ignore[assignment]


def org() -> Org:
    o = Org.create("d202")
    o.d["max_top_grant"] = 200
    return o


# ------------------------------------------------------------------ §1 hint
print("\n§1  the hint itself")

TIER_OF = {"openai": "sol", "google": "pro", "claude": "haiku"}


def hint_names_the_package() -> None:
    assert "@openai/codex" in providers.install_hint("openai")
    assert ("Google.AntigravityCLI" in providers.install_hint("google")
            or "antigravity.google/cli/install.sh" in providers.install_hint("google"))
    assert "@anthropic-ai/claude-code" in providers.install_hint("claude")


def hint_installs_where_orgtree_looks() -> None:
    """⚠ NOT `npm i -g`. Codex installs under the orgtree data dir with
    --prefix, because that is the copy `codex_path` resolves. A hint naming a
    global install would be a command that "works" and leaves the user
    exactly as broken — the failure this check exists to prevent, and the one
    the first draft of D-202 actually wrote before it was measured."""
    h = providers.install_hint("openai")
    assert "--prefix" in h, h
    assert " -g " not in h, h
    # and the prefix is THIS machine's data dir, not a hard-coded path
    assert _TMP in h, (h, _TMP)


def antigravity_hint_is_its_own_installer() -> None:
    """The Antigravity CLI is a native binary with Google's installer — there
    is no npm package to pin, and the installer drops the binary exactly where
    `antigravity_path` looks first. A hint naming npm would be a command that
    cannot even run."""
    h = providers.install_hint("google")
    assert "npm" not in h, h
    assert "--prefix" not in h, h


def claude_is_global_and_that_is_correct() -> None:
    """The asymmetry is real, not an oversight: Claude Code is not vendored
    under the data dir, so its hint is the global install. Pinned so a tidy-up
    does not make all three "consistent" and wrong."""
    h = providers.install_hint("claude")
    assert "--prefix" not in h, h
    assert "-g" in h, h


check("each hint names its own package", hint_names_the_package)
check("codex installs under the data dir orgtree resolves from",
      hint_installs_where_orgtree_looks)
check("antigravity's hint is its own installer, never npm",
      antigravity_hint_is_its_own_installer)
check("claude's hint is the global install, deliberately",
      claude_is_global_and_that_is_correct)


# --------------------------------------------------------------- §2 payload
print("\n§2  the payload's reasons are built from the hint")


def payload_reasons_use_the_hint() -> None:
    with Absent():
        pay = providers.providers_payload(
            {"installed": False, "connected": False})
    by = {p["id"]: p for p in pay["providers"]}
    for pid in ("openai", "google", "claude"):
        reason = by[pid]["reason"] or ""
        assert providers.install_hint(pid) in reason, (pid, reason)


def payload_says_nothing_when_healthy() -> None:
    """The leg that stops §2 passing on a mutant that jams the hint into every
    reason: a connected provider's reason is None, hint or no hint."""
    pay = providers.providers_payload({"installed": True, "connected": True})
    by = {p["id"]: p for p in pay["providers"]}
    assert by["claude"]["reason"] is None, by["claude"]


check("an absent provider's reason carries its install command",
      payload_reasons_use_the_hint)
check("a healthy provider carries no reason at all",
      payload_says_nothing_when_healthy)


# ------------------------------------------------------------------ §3 gate
print("\n§3  the gate's refusals")


def gate_refusals_carry_the_command() -> None:
    o = org()
    with Absent():
        for pid, tier in TIER_OF.items():
            msg = refusal(lambda t=tier: api.provider_hire_gate(o, t))
            assert providers.install_hint(pid) in msg, (pid, msg)


def gate_points_at_no_deleted_panel() -> None:
    """⚠ THE DEFECT D-202 INTRODUCED IF THIS IS NOT DONE. All three messages
    said "the accounts panel's <X> section has the install command". For Codex
    and Antigravity that section is now removed on precisely the machines that see
    this refusal; for Claude the section survives but never carried a command.
    A refusal must name a place that exists."""
    o = org()
    with Absent():
        for tier in TIER_OF.values():
            msg = refusal(lambda t=tier: api.provider_hire_gate(o, t)).lower()
            assert "accounts panel" not in msg, msg
            assert "section" not in msg, msg


def the_gate_and_the_payload_agree() -> None:
    """The single-source property stated as an equality rather than trusted:
    the string a user reads in the refusal is the string the payload would
    have shown, character for character."""
    o = org()
    with Absent():
        pay = providers.providers_payload(
            {"installed": False, "connected": False})
        by = {p["id"]: p for p in pay["providers"]}
        for pid, tier in TIER_OF.items():
            msg = refusal(lambda t=tier: api.provider_hire_gate(o, t))
            hint = providers.install_hint(pid)
            assert hint in msg and hint in (by[pid]["reason"] or ""), pid


check("every not-installed refusal carries the install command",
      gate_refusals_carry_the_command)
check("no refusal points at an accounts-page section",
      gate_points_at_no_deleted_panel)
check("the gate and the payload quote the SAME command",
      the_gate_and_the_payload_agree)


# -------------------------------------------------------------- §4 controls
print("\n§4  controls — what would make the above vacuous")


def the_gate_still_refuses_at_all() -> None:
    """§3 reads the text of refusals. If the gate stopped refusing, `refusal`
    would raise its own AssertionError — but only if something still calls it,
    so the plain fact is pinned separately."""
    o = org()
    with Absent():
        for tier in TIER_OF.values():
            try:
                api.provider_hire_gate(o, tier)
            except LedgerError:
                continue
            raise AssertionError(f"{tier} was accepted on a bare machine")


def a_present_provider_is_not_refused() -> None:
    """The over-refusal leg. A gate that refused everything would pass every
    check above while making the app unusable — the exact shape of mistake
    D-199's first draft made in the UI."""
    o = org()
    api.provider_hire_gate(o, "haiku")          # real machine, real Claude


def the_hint_is_not_the_same_string_for_everyone() -> None:
    """§2 and §3 assert `hint in message`. A hint that collapsed to '' or to
    one shared value would satisfy every containment check trivially."""
    hints = {providers.install_hint(p) for p in TIER_OF}
    assert len(hints) == 3, hints
    assert all(len(h) > 10 for h in hints), hints


check("the gate still refuses an absent provider", the_gate_still_refuses_at_all)
check("…and does NOT refuse one that is present", a_present_provider_is_not_refused)
check("the three hints are distinct, non-empty strings",
      the_hint_is_not_the_same_string_for_everyone)


# ------------------------------------------------------------------ summary
print(f"\n{'=' * 60}")
if FAILED:
    print(f"FAILED {len(FAILED)} / {PASSED + len(FAILED)}")
    for f in FAILED:
        print(f"  x {f}")
    sys.exit(1)
print(f"PASSED {PASSED}/{PASSED}")
