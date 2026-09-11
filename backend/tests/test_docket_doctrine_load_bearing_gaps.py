"""Acceptance gate for the reduce-orgtree-mcp-token-cost item — written BEFORE
the trim, per redteam charter §7: a check that self-arms when the feature
lands, so a behaviour-changing cut goes red instead of shipping silently.

WHY THIS FILE EXISTS. test_work_items.py's `the_ruling_reaches_agents_where_
they_read_it` and test_report_guidance_identity.py already pin real load-
bearing sentences from `identity_prompt` — but their coverage of
`DOCKET_DOCTRINE` (supervisor.py ~6147) stops at the "2026-09-05 review/
attention ruling" subset (§6) plus fragments of §1/§2/§7. FOUR sections of the
same constant carry operational instructions with ZERO test coverage anywhere
in the suite (verified by grep across tests/ before writing this file):

  §3  update cadence          — "never after every tool call"
  §4  blocked enforcement     — "must SAY SO" / blocked_reason is REQUIRED
  §4  blocked idle exemption  — "NEVER nudged by the idle reminder"
  §4  dropped terminal state  — "TERMINAL NON-SUCCESS OUTCOME" / "no one-hour grace"
  §4  delete doctrine         — "PERMANENT DELETION"

A trim that deletes one of these sentences today changes agent behaviour
(an agent could start updating after every tool call, or treat `blocked`
as optional, or not know `dropped` skips the grace period) with every
existing check staying green. This file exists to make that same trim
fail loud.

CONTROL, same shape as test_work_items.py's positive control: blank
`DOCKET_DOCTRINE` and require every marker to disappear from the prompt.
A marker that survives the blank is a marker this file was never actually
testing (it would mean the phrase lives elsewhere in identity_prompt too).

Also asserts the CURRENT state of the de-dupe question (redteam finding,
2026-09-10): none of these five markers appear in mcptool.py's orgtree_work
tool description either — so today, an agent that only ever reads the tool
card (never its own identity_prompt) has NO way to learn any of them. If a
future "de-dupe by relocating into the tool description" lands, that is the
signal this second block exists to catch turning the other way (a marker
that then appears in the CARD is the sign the relocation happened, not a
failure) — see the docket item `reduce-orgtree-mcp-token-cost`.

Hermetic: throwaway ORGTREE_DATA/HOME, no CLI, no network, no listener.

    python backend/tests/test_docket_doctrine_load_bearing_gaps.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys._utf8_wrapped = True

RIG = tempfile.mkdtemp(prefix="docket-doctrine-gap-")
HOME = os.path.join(RIG, "home")
os.makedirs(HOME, exist_ok=True)
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
os.environ["ORGTREE_WARM"] = "0"
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_PORT"] = "7417"

with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import mcptool, store, supervisor as S              # noqa: E402
from orgtree.ledger import USER                                   # noqa: E402

PASS = FAIL = 0


def check(label, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:3d}  {label}")
    except Exception as e:                                        # noqa: BLE001
        FAIL += 1
        print(f"  FAIL     {label}: {type(e).__name__}: {e}")


def die(m: str) -> None:
    raise AssertionError(m)


org = store.create_org("docket doctrine gap rig")
SLUG = org.d["slug"]
org.hire(USER, None, "haiku", 5, "boss", add_dirs=[], tools={"mcp": ["*"]},
          org_visibility="full", charter="c")
org.hire(USER, "boss", "haiku", 0, "worker", add_dirs=[], tools={"mcp": ["*"]},
          org_visibility="team", charter="c")
store.save_org(org)
ORG = store.load_org(SLUG)

#: one representative, exact-substring marker per currently-unpinned doctrine
#: section. Keep these SHORT and load-bearing — not paraphrases — so a passing
#: run means the actual instruction survived, not a lookalike.
GAP_MARKERS = {
    "§3 update cadence — never spam an update after every tool call":
        "never after every tool call",
    "§4 blocked must carry a reason, transition refused without one":
        "must SAY SO",
    "§4 blocked is exempt from the idle nudge":
        "NEVER nudged by the idle reminder",
    "§4 dropped is the terminal non-success outcome, never Done":
        "TERMINAL NON-SUCCESS OUTCOME",
    "§4 dropped archives immediately, no one-hour grace":
        "no one-hour grace",
    "§4 delete doctrine exists and is distinguished from archive/drop":
        "PERMANENT DELETION",
}


def markers_present_in_identity_prompt() -> None:
    for nid in ("boss", "worker"):
        p = S.identity_prompt(ORG, nid)
        for label, marker in GAP_MARKERS.items():
            if marker not in p:
                die(f"{nid}: missing {label!r} (marker {marker!r})")


check("all six currently-unguarded DOCKET_DOCTRINE markers reach every agent "
      "(boss and worker) via identity_prompt", markers_present_in_identity_prompt)


def control_markers_vanish_with_doctrine_blanked() -> None:
    keep = S.DOCKET_DOCTRINE
    try:
        S.DOCKET_DOCTRINE = ""
        blanked = S.identity_prompt(ORG, "boss")
    finally:
        S.DOCKET_DOCTRINE = keep
    survivors = [label for label, marker in GAP_MARKERS.items()
                 if marker in blanked]
    if survivors:
        die(f"markers present without DOCKET_DOCTRINE — assert is inert for: {survivors}")


check("CONTROL: blanking DOCKET_DOCTRINE removes every gap marker — proves "
      "the asserts above test the doctrine, not a coincidental substring",
      control_markers_vanish_with_doctrine_blanked)


def gap_markers_absent_from_tool_card_today() -> None:
    card = next(t for t in mcptool.TOOLS if t["name"] == "orgtree_work")
    desc = card["description"]
    present = [label for label, marker in GAP_MARKERS.items() if marker in desc]
    if present:
        die(
            "a gap marker now appears in orgtree_work's tool description: "
            f"{present}. If this is an intentional de-dupe relocation "
            "(doctrine moved from identity_prompt into the deferred tool "
            "description), update this test to assert presence-in-the-card "
            "instead of absence, and confirm identity_prompt still teaches "
            "it too UNLESS the coordinator has explicitly accepted the "
            "trade-off that agents who never ToolSearch orgtree_work no "
            "longer receive it."
        )


check("baseline (2026-09-10): none of the six gap markers are relocated "
      "into orgtree_work's tool description either — today an agent that "
      "never reads its own identity_prompt has no path to any of them",
      gap_markers_absent_from_tool_card_today)


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
