"""Acceptance gate for the A2 lever (docket item `reduce-orgtree-mcp-token-
cost`): shrink the fattest tool descriptions in mcptool.py without cutting the
sentences agents actually rely on as operating instructions.

WHY THIS FILE EXISTS. A2 targets the five heaviest `description` fields:
orgtree_work (5,091 chars), orgtree_hire (2,115), orgtree_watchdog (4,050),
orgtree_prime_restart (3,112), orgtree_self_restart (2,840) — see redteam's
2026-09-10 report on this item. orgtree_work already has real coverage
(test_work_items.py's DOCKET_DOCTRINE pins + this session's
test_docket_doctrine_load_bearing_gaps.py) and is NOT repeated here.
orgtree_watchdog has partial coverage (test_watchdog_visibility.py §6 pins
cmd.exe/findstr/smoke/checks_run/"NOT IN YOUR SHELL" with a negative-control
pair). orgtree_hire has one existing pin (desc mentions "superior" as the
second mode, test_hire_schema_contract.py). orgtree_prime_restart and
orgtree_self_restart have ZERO description-content coverage anywhere in the
suite — verified by grep before writing this file (only schema-field/enum
checks exist for either).

CLASSIFICATION (the deliverable, not just the test). Every marker below is an
OPERATIONAL INSTRUCTION WITH A CONSEQUENCE IF MISSED, not a restatement of
something the JSON schema already carries elsewhere. The reasoning per tool:

  orgtree_hire
    · "HIRING STARTS NO ONE" / "sits IDLE until it receives its first
      message" — the single highest-consequence sentence in this card: the
      failure mode is SILENT (no error, no refusal — the hire just sits
      there), so there is no natural signal that teaches an agent this the
      hard way. Everything else in this card that is missed produces a
      refusal, which is self-correcting; this one does not. HIGHEST priority
      to keep.
    · "cannot grant anything you do not hold yourself" — a hard constraint;
      missing it costs a wasted refused call, lower severity than the above
      but still a real one (self-correcting via refusal).
    · "Any refusal anywhere in the call refuses the WHOLE call" — an
      atomicity guarantee; without it an agent may believe a hire partially
      applied and go investigate state that was never written.
    SAFE TO CUT / SHRINK: the per-tier seat-cost table (haiku 1, sonnet 2,
    opus 5, fable 10, luna 0.2 …). It is reference data, not an instruction —
    getting a number wrong produces a refusal (self-correcting) and the exact
    figures are the kind of thing that drifts and needs updating anyway. Not
    pinned here on purpose: a literal-number pin would make this test a
    change-detector on pricing, not on behaviour.

  orgtree_watchdog
    · "a dog whose pattern encodes a DEADLINE rather than an EDGE ... fires
      every single interval, forever" — the card cites a REAL past incident
      ("This has actually happened here — a dog woke its owner every 15
      minutes"). This is the textbook load-bearing warning: cutting it
      reintroduces a defect that already shipped once.
    · "fires EXACTLY ONCE and then REMOVES ITSELF" (the escape hatch for the
      above trap, `once:true`) — without this an agent has the failure mode
      described but not the fix.
    · "MAILS YOU WHEN THE THING IT WATCHES GOES QUIET, without being asked"
      (D-176) — undocumented, this reads as a surprising/unexplained mail;
      an agent that doesn't expect staleness notices may misinterpret them
      as something else entirely (e.g. a restart signal, which the card
      explicitly says it is NOT).
    SAFE TO CUT / SHRINK: "prefer a `process` dog over a `file` dog" (a
    preference, not a correctness issue) and the closing "prefer a watchdog
    over burning turns polling" line — the latter is ALREADY taught
    unconditionally in every agent's identity_prompt (near-verbatim: "Keep a
    WATCHDOG instead... a free, persistent pet that wakes you with mail"),
    so this is a second, smaller instance of the same class of duplication
    redteam flagged for THE DOCKET — safe to drop from the card without loss.

  orgtree_prime_restart
    · "survives YOUR COMPACTION, YOUR RETIREMENT and a backend bounce" — the
      entire reason to prefer this tool over self_restart; the card cites a
      real incident ("a merged fix once sat undeployed for a day"). Cutting
      it removes the tool's reason to exist from its own description.
    · "WITHOUT it a prime waits forever" (deadline_minutes) — an
      absence-behaviour warning for an OPTIONAL field; these are exactly the
      kind of thing schemas cannot express (a schema shows a field is
      optional, never what happens when you skip it) and exactly the kind a
      trim is tempted to treat as "flavor."
    · "NOBODY WILL BE PRESENT" (the deadline's unattended escalation) — a
      safety consequence: agents get stopped mid-turn with no one there to
      object. Missing this, an agent could set a deadline without weighing
      the actual cost the card asks it to weigh.
    SAFE TO CUT / SHRINK: "Top-level agents and user-audience holders only;
    kiosks sealed" — an eligibility fact that a disallowed caller learns
    immediately via refusal; lower stakes than the three above.

  orgtree_self_restart
    · "RESTARTS EVERY ORG on this machine" — blast-radius warning; the
      single fact most likely to matter to an agent deciding whether to
      call this at all.
    · "THE AGENTS DO NOT RESUME BY THEMSELVES ... YOU must message them" —
      a post-condition with a silent-failure shape close to orgtree_hire's
      kickoff trap: skip it and a forced restart leaves an org stalled with
      nothing pointing at why.
    · "never run update.ps1 / update.sh yourself" — guards a documented
      real failure ("measured on a peer install: the log stopped at
      'building the UI' and the backend never restarted").
    SAFE TO CUT / SHRINK: "one launch per 5 minutes machine-wide" — a rate
    limit an agent discovers immediately via refusal if it matters.

MUTANT DISCIPLINE. Every marker's own presence check is what fails if the
sentence is cut — that is not vacuous PROVIDED the marker is distinctive
enough that no plausible trim leaves it behind by accident. `_self_test_
markers_are_not_trivially_satisfied` proves each marker fails against a
deliberately corrupted copy of the real description (the sentence physically
removed), so a marker that would keep passing through a real cut — a typo, an
overly generic phrase — is caught before it ever ships as a false guard.

Hermetic: no CLI, no network, no listener, no org/ledger fixture needed —
mcptool.TOOLS is pure data.

    python backend/tests/test_a2_tool_description_load_bearing.py
"""
from __future__ import annotations

import io
import sys

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys._utf8_wrapped = True

import os                                                          # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from orgtree import mcptool                                        # noqa: E402

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


def card(name: str) -> dict:
    return next(t for t in mcptool.TOOLS if t["name"] == name)


#: tool name -> {label: exact-substring marker}. One entry per operational
#: sentence identified in the module docstring above.
MARKERS: dict[str, dict[str, str]] = {
    "orgtree_hire": {
        "kickoff silent-trap — the highest-consequence sentence in this card":
            "HIRING STARTS NO ONE",
        "kickoff silent-trap, second clause — what idle actually means":
            "sits IDLE until it receives its first message",
        "cannot delegate a grant you do not hold":
            "cannot grant anything you do not hold yourself",
        "atomicity — a returned hire got everything it asked for":
            "Any refusal anywhere in the call refuses the WHOLE call",
    },
    "orgtree_watchdog": {
        "the deadline-vs-edge trap — cites a real repeated-wake incident":
            "a dog whose pattern encodes a DEADLINE rather than an EDGE",
        "the escape hatch for the trap above — once:true self-removes":
            "fires EXACTLY ONCE and then REMOVES ITSELF",
        "unsolicited staleness mail — D-176, not a restart signal":
            "MAILS YOU WHEN THE THING IT WATCHES GOES QUIET",
    },
    "orgtree_prime_restart": {
        "the tool's whole reason to exist — cites a real day-late deploy":
            "survives YOUR COMPACTION, YOUR RETIREMENT and a backend bounce",
        "deadline_minutes' absence-behaviour — a schema cannot say this":
            "WITHOUT it a prime waits forever",
        "the deadline's unattended-escalation safety consequence":
            "NOBODY WILL BE PRESENT",
    },
    "orgtree_self_restart": {
        "blast radius — the fact most likely to change whether to call this":
            "RESTARTS EVERY ORG on this machine",
        "post-forced-restart silent-stall risk — mirrors hire's kickoff trap":
            "THE AGENTS DO NOT RESUME BY THEMSELVES",
        "guards a documented real half-updated-install failure":
            "never run update.ps1 / update.sh yourself",
    },
}


def markers_present() -> None:
    missing = []
    for name, markers in MARKERS.items():
        desc = card(name)["description"]
        for label, marker in markers.items():
            if marker not in desc:
                missing.append(f"{name}: {label!r} (marker {marker!r})")
    if missing:
        die("operational sentence(s) missing from the tool card:\n  "
            + "\n  ".join(missing))


check("all 13 operational sentences across the 4 uncovered/under-covered "
      "cards (hire, watchdog, prime_restart, self_restart) are present "
      "today", markers_present)


def self_test_markers_are_not_trivially_satisfied() -> None:
    """MUTANT CHECK. For every marker, delete exactly that sentence's marker
    substring from a COPY of the real description and confirm the presence
    assertion then correctly reports it missing. A marker that still "found"
    itself in the corrupted copy would mean the check above is not actually
    reading the real string (e.g. a stale cached value, a typo'd tool name
    resolving to the wrong card) — this is the proof the gate is live, not
    the DOCKET_DOCTRINE-style blank-and-verify control (there is no shared
    constant here to blank; each description is its own literal string, so
    corrupting the copy directly is the equivalent control)."""
    escaped = []
    for name, markers in MARKERS.items():
        real = card(name)["description"]
        for label, marker in markers.items():
            corrupted = real.replace(marker, "")
            if marker in corrupted:
                escaped.append(f"{name}: {label!r} survived its own removal")
    if escaped:
        die("dead/tautological marker(s): " + "; ".join(escaped))


check("CONTROL: every marker actually disappears when physically cut from "
      "a copy of its real description — proves the checks above are live",
      self_test_markers_are_not_trivially_satisfied)


def markers_do_not_overlap_schema_field_descriptions() -> None:
    """The inputSchema `properties[*].description` fields are a SEPARATE
    surface from the top-level tool `description` (both are deferred
    together once ToolSearched, but a trim that only edits one must still be
    caught). If a marker also happens to live in the schema, a description-
    only cut could look safe under a careless "blob = description + schema"
    check while still losing the sentence from the place an agent reads
    first. Each marker here must be findable in `description` ALONE."""
    for name, markers in MARKERS.items():
        desc = card(name)["description"]
        for label, marker in markers.items():
            if marker not in desc:
                die(f"{name}: {label!r} not even in description (see check 1)")


check("every marker lives in the top-level `description` field itself, not "
      "only reachable via the schema", markers_do_not_overlap_schema_field_descriptions)


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
