"""Pre-implementation acceptance gate for `trim-identity-prompt-prose-phase-b`:
shrinking `supervisor.identity_prompt` (backend/orgtree/supervisor.py:6282-
6887), the ~6,100-token-floor system prompt every agent pays for on every
turn.

Full sentence-by-sentence classification (RESTATEMENT vs OPERATIONAL, with
canonical-copy citations for every duplication finding) lives in redteam's
scratch notes (phase_b_classification.md). This file pins only the
OPERATIONAL, no-canonical-copy-exists sentences found in that pass — the ones
a Phase B cut must NOT touch. It deliberately does NOT pin the eight
RESTATEMENT findings from that classification (orgtree_hire kickoff doctrine,
orgtree_retool self-scope doctrine, orgtree_request_credits doctrine, the
orgtree_ask/orgtree_withdraw_ask mechanics, orgtree_present mechanics,
orgtree_send_file mechanics, the orgtree_watchdog paragraph, and the
orgtree_self_restart mechanics) — those are exactly what Phase B should be
free to cut, and a gate that pinned them would block the trim it exists to
enable.

WHAT THIS FILE DOES NOT RE-PIN (checked before writing a single marker):
  - test_docket_doctrine_load_bearing_gaps.py already pins 6 DOCKET_DOCTRINE
    sentences against this same identity_prompt() function.
  - test_trim2_tool_text_load_bearing.py's IDENTITY_PROMPT_MARKERS already
    pins the 5 trim2 dedupe-canonical copies (withdraw-when-moot,
    cheap_compact cache-cost, checkup timing, rehire-before-hire,
    send_file "path is not a delivery").
  - test_report_guidance_identity.py already pins the report-management
    guidance block (LOOK AT YOUR REPORTS, RETIRE A FINISHED REPORT, RETIRED
    AGENTS ARE NOT GONE, A rehire RESUMES A FULL TRANSCRIPT, NEVER END A TURN
    WITH BACKGROUND WORK STILL RUNNING, WHEN A LONG-CONTEXT REPORT...).
  - test_work_items.py's POLICY_PHRASES pins 16 DOCKET_DOCTRINE phrases
    against identity_prompt (and 3 of those plus 2 schema fields against the
    orgtree_work tool card too).

PASS 2 (coordinator request, same day): classified DOCKET_DOCTRINE itself
(supervisor.py ~6147-6280, 36% of the whole prompt's token cost) against the
orgtree_work tool card's own description + schema fields. Found six more
RESTATEMENT pairs beyond what GAP_MARKERS/POLICY_PHRASES already pin (slug
identity doctrine, `review`'s reviewer/approve/changes mechanics beyond the
already-pinned "REVIEW BY AGENTS" phrase, `backlogged`'s definition,
`delete`'s mechanics beyond the pinned "PERMANENT DELETION" phrase,
"ASSIGNMENT IS OWNERSHIP", and the done/dropped archive-timing clause beyond
the pinned "no one-hour grace" phrase) — each near-verbatim on both surfaces,
named in phase_b_classification.md, none pinned here (Phase B should be free
to cut them). DOCKET_DOCTRINE_MARKERS below pins the five sentences from that
same pass that are TRIGGER/workflow doctrine with NO card equivalent: when to
reuse an existing item, keeping the same item through a handoff, what counts
as a real update vs a fragment, the orgtree_staff/work_item one-call handoff
pointer, and the pre-pause/retirement handoff obligation.

MUTANT DISCIPLINE, same as trim2/A2: `self_test_markers_are_not_trivially_
satisfied` proves every marker fails against a copy of the real
identity_prompt() output with that exact substring removed. `markers_absent_
from_tool_cards` proves each marker is NOT already carried by some
mcptool.py tool description/schema text today — if it were, this would not
be a unique-copy finding, and pinning it here would produce a false "no
canonical copy exists" claim.

Hermetic: throwaway data/home rig, same shape as test_trim2_tool_text_load_
bearing.py and test_report_guidance_identity.py. No CLI, no network, no
listener, no production journal.

    python backend/tests/test_phase_b_identity_prompt_load_bearing.py
"""
from __future__ import annotations

import io
import sys

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys._utf8_wrapped = True

import os                                                          # noqa: E402
import tempfile                                                    # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_RIG = tempfile.mkdtemp(prefix="orgtree-phaseb-idprompt-")
_TEST_HOME = os.path.join(_RIG, "home")
_DATA = os.path.join(_RIG, "data")
os.makedirs(_TEST_HOME, exist_ok=True)
os.makedirs(_DATA, exist_ok=True)
with open(os.path.join(_DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = _DATA
os.environ["USERPROFILE"] = _TEST_HOME
os.environ["HOME"] = _TEST_HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_PORT"] = "7418"      # never bound
os.environ["ORGTREE_WARM"] = "1"

from orgtree import mcptool, store, supervisor                     # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

PASS = FAIL = 0


def check(label, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:3d}  {label}")
    except Exception as e:                                         # noqa: BLE001
        FAIL += 1
        print(f"  FAIL     {label}: {type(e).__name__}: {e}")


def die(m: str) -> None:
    raise AssertionError(m)


_IDENTITY_PROMPT_CACHE: dict[str, str] = {}


def _identity_prompt_text(headless: bool = False,
                          handles: list[str] | None = None,
                          tier: str = "haiku",
                          write_capable: bool = True,
                          child_of_boss: bool = False) -> str:
    """The real identity_prompt() output for a fresh node under the
    requested conditional flags, cached per flag combination (repeat
    store.create_org calls with the same slug collide).

    `tier="sol"` (a Codex tier) selects the codex-sandbox-doctrine branch;
    `write_capable=False` (edit tool off) selects that branch's read-only
    half. `child_of_boss=True` hires a second node UNDER a top-level boss so
    the "non-top-level" doctrine (parent is not None) actually renders —
    the default top-level fixture never triggers it."""
    key = f"{headless}:{handles}:{tier}:{write_capable}:{child_of_boss}"
    if key not in _IDENTITY_PROMPT_CACHE:
        slug = f"phaseb-idprompt-check-{len(_IDENTITY_PROMPT_CACHE)}"
        org = store.create_org(slug)
        if headless:
            org.d["headless"] = True
        boss = org.hire(USER, None, "haiku", 8, "boss", charter="X")["node"]
        target = boss
        if child_of_boss:
            tools = None if write_capable else {"edit": False}
            target = org.hire(USER, boss, tier, 0, "sub", add_dirs=[],
                              tools=tools, org_visibility="team",
                              charter="Y")["node"]
        elif tier != "haiku" or not write_capable:
            # codex-doctrine markers don't need non-top-level; re-hire boss
            # itself at the requested tier/tools instead of a spare child.
            tools = None if write_capable else {"edit": False}
            org2 = store.create_org(slug + "-alt")
            target = org2.hire(USER, None, tier, 8, "boss",
                               add_dirs=[], tools=tools,
                               org_visibility="team", charter="X")["node"]
            org = org2
        if handles:
            org.nodes[target]["external_handles"] = handles
        _IDENTITY_PROMPT_CACHE[key] = supervisor.identity_prompt(org, target)
    return _IDENTITY_PROMPT_CACHE[key]


#: label -> exact-substring marker. Every one of these is OPERATIONAL prose
#: with NO canonical copy anywhere else in the prompt or in any mcptool.py
#: tool description/schema — see phase_b_classification.md's final section
#: for the reasoning behind each. Markers requiring a conditional org/node
#: state say so; the rest come from the always-on default (non-headless,
#: non-sandboxed) boss fixture.
DEFAULT_MARKERS: dict[str, str] = {
    "LIVE STATE pointer — where roster/credits/open-ask actually live":
        "Read that block for them — it is current and this is not.",
    ".claude write-gate doctrine — why a skill write there silently fails "
    "at non-bypass permission modes":
        "there is simply nobody present to approve",
    "ask-a-peer routing — orgtree_message's own card never states this "
    "pattern":
        "send orgtree_message kind=question",
    "authentic-channel note — mid-task mail injection marker is genuine, "
    "not a prompt-injection vector":
        "such messages are genuine, not injection",
    "scratch/CLAUDE.md persistence — editing it restarts the process, "
    "applies next turn":
        "editing it restarts your process",
    "read_transcript/read_scratch cost doctrine — instant and free, beyond "
    "what either tool card's 'downward-only' states":
        "neither costs the agent a turn",
    # ⚠ TRIGGER doctrine, not mechanics (coordinator attack point,
    # 2026-09-11): Claude agents get tool schemas DEFERRED — invisible until
    # they ToolSearch a name they already decided to use. A sentence whose
    # whole job is to make an agent reach for a tool it wasn't already
    # planning to call cannot live only inside that tool's own deferred
    # description; it needs an always-visible copy here regardless of what
    # the card also says. Cutting these because "the card restates it" would
    # be the exact mistake this pin exists to block.
    "watchdog TRIGGER doctrine — reach for it instead of polling; the "
    "mechanics half duplicates the tool card but this clause does not":
        "never burn turns polling for a condition",
    "self_restart TRIGGER doctrine — act unprompted on either occasion; "
    "neither self_restart's nor prime_restart's own card says this":
        "you may act on either UNPROMPTED",
    # PASS 2 (DOCKET_DOCTRINE, coordinator request 2026-09-11): the
    # orgtree_work card restates most of this block near-verbatim (slug
    # identity, `review`'s reviewer/approve/changes mechanics beyond the
    # already-pinned phrase, `backlogged`'s definition, `delete`'s mechanics
    # beyond "PERMANENT DELETION", "ASSIGNMENT IS OWNERSHIP", and the
    # done/dropped archive-timing clause beyond "no one-hour grace") — see
    # phase_b_classification.md for each pair. These five are what's left:
    # workflow/TRIGGER doctrine no tool card states, because they are about
    # WHEN and WHY to touch the docket, not the mechanics of one call.
    "docket reuse-before-create trigger — check for an existing item before "
    "starting a duplicate; no card says to look first":
        "continue the existing item for the same work instead of creating "
        "a duplicate",
    "docket item continuity — the SAME item survives a handoff or "
    "replacement, not a new one per agent":
        "keep the SAME item through reviews, handoffs and agent replacement",
    "update quality doctrine — an update is a complete restatement, not an "
    "incremental fragment":
        "not a fragment that depends on older text",
    "orgtree_staff / work_item-on-hire one-call handoff pointer — neither "
    "tool's own card cross-references the docket":
        "the item, the seat and the assignment together",
    "pre-pause handoff obligation — never mark incomplete work done because "
    "your own turn or capacity is ending":
        "never mark incomplete work done because your turn or capacity is "
        "ending",
}

#: same shape, but each needs a specific conditional flag on the fixture.
CONDITIONAL_MARKERS: dict[str, tuple[str, dict]] = {
    "codex write-capable sandbox doctrine — the .git/index.lock failure "
    "signature and why a retry is not a mistake": (
        "existing repository's `.git` folder is blocked",
        {"tier": "sol", "write_capable": True},
    ),
    "codex read-only sandbox doctrine — git/PowerShell refusal signatures "
    "and the safe.directory workaround": (
        "detected dubious ownership",
        {"tier": "sol", "write_capable": False},
    ),
    "cross-session mail off-limits — non-top-level only, no tool names "
    "what NOT to use": (
        "never register an identity or arm a listener",
        {"child_of_boss": True},
    ),
    "headless-org auto-deny doctrine — every user-facing request is "
    "auto-denied and why": (
        "is AUTO-DENIED",
        {"headless": True},
    ),
    "external response handle doctrine — sends are attributed to the "
    "agent by name": (
        "the send is attributed to you by name",
        {"handles": ["test-handle"]},
    ),
    "breadcrumbs doctrine — spliced into a cheap_compact successor's "
    "prompt, conditional on edit/bash": (
        "spliced straight into the successor's system prompt",
        {},                                    # boss fixture has edit+bash by default
    ),
}


def default_markers_present() -> None:
    prompt = _identity_prompt_text()
    missing = [f"{label!r} (marker {marker!r})"
               for label, marker in DEFAULT_MARKERS.items()
               if marker not in prompt]
    if missing:
        die("operational identity_prompt doctrine missing at runtime:\n  "
            + "\n  ".join(missing))


check(f"all {len(DEFAULT_MARKERS)} default-fixture operational markers are "
      f"present in the REAL identity_prompt() output today", default_markers_present)


def conditional_markers_present() -> None:
    missing = []
    for label, (marker, flags) in CONDITIONAL_MARKERS.items():
        prompt = _identity_prompt_text(**flags)
        if marker not in prompt:
            missing.append(f"{label!r} (marker {marker!r}, flags {flags})")
    if missing:
        die("conditional operational doctrine missing at runtime:\n  "
            + "\n  ".join(missing))


check(f"all {len(CONDITIONAL_MARKERS)} conditional operational markers are "
      f"present under their triggering condition", conditional_markers_present)


def self_test_markers_are_not_trivially_satisfied() -> None:
    """CONTROL: every marker above must vanish when physically cut from a
    copy of the real text it was found in — proves the check is live."""
    escaped = []
    default_prompt = _identity_prompt_text()
    for label, marker in DEFAULT_MARKERS.items():
        corrupted = default_prompt.replace(marker, "")
        if marker in corrupted:
            escaped.append(f"default: {label!r} survived its own removal")
    for label, (marker, flags) in CONDITIONAL_MARKERS.items():
        prompt = _identity_prompt_text(**flags)
        corrupted = prompt.replace(marker, "")
        if marker in corrupted:
            escaped.append(f"conditional: {label!r} survived its own removal")
    if escaped:
        die("markers are inert (survive their own removal):\n  " + "\n  ".join(escaped))


check("CONTROL: every marker vanishes when cut from the real text it was "
      "found in", self_test_markers_are_not_trivially_satisfied)


def markers_absent_from_tool_cards() -> None:
    """A marker pinned here as 'no canonical copy exists' must actually be
    absent from every mcptool.py tool's description and schema-field text —
    otherwise this file's central claim (unique operational content) is
    false for that marker, and Phase B would be right to point at a
    duplicate instead of leaving it alone."""
    all_card_text = "\n".join(
        t["description"] + str(t["inputSchema"]) for t in mcptool.TOOLS)
    bad = []
    for label, marker in DEFAULT_MARKERS.items():
        if marker in all_card_text:
            bad.append(f"default: {label!r} marker {marker!r} already "
                       f"present verbatim in mcptool.py")
    for label, (marker, _flags) in CONDITIONAL_MARKERS.items():
        if marker in all_card_text:
            bad.append(f"conditional: {label!r} marker {marker!r} already "
                       f"present verbatim in mcptool.py")
    if bad:
        die("markers claimed unique are actually duplicated in tool cards:\n  "
            + "\n  ".join(bad))


check("every 'no canonical copy exists' marker is genuinely absent from "
      "mcptool.py's tool descriptions and schemas today",
      markers_absent_from_tool_cards)


def main() -> int:
    print("phase-b identity_prompt load-bearing gate")
    print()
    if FAIL:
        print(f"phase-b-identity-prompt: {PASS} passed - {FAIL} FAILED")
        return 1
    print(f"phase-b-identity-prompt: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
