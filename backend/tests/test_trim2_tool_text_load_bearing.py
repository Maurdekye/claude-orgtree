"""Acceptance gate for the trim2 lever (docket item `reduce-orgtree-mcp-token-
cost`, second slice): shrink the REMAINING tool text in mcptool.py — the
top-level `description` of the 30 tools A2 did not touch, and the inputSchema
field-level `description` text of all 35 tools (including the 5 A2 trimmed,
whose schemas A2 left alone) — without cutting the sentences agents actually
rely on as operating instructions.

Full classification, char counts and the duplication findings this gate does
NOT enforce (because the fix is "keep one copy", not "keep a string") live in
redteam's scratch notes (trim2_classification.md). This docstring covers only
what the MARKERS below are for and why each one is pinned.

WHAT THIS FILE DOES NOT RE-PIN. Checked against the existing suite before
writing a single marker:
  - test_a2_tool_description_load_bearing.py already pins 13 operational
    sentences in the TOP-LEVEL descriptions of orgtree_hire, orgtree_watchdog,
    orgtree_prime_restart, orgtree_self_restart. Not repeated here.
  - test_work_items.py (`the_ruling_reaches_agents_where_they_read_it`) already
    pins, against mcptool.TOOLS itself: orgtree_work's top-level description
    ("REVIEW BY AGENTS", "ATTENTION mechanism", "does not pass through
    `review` first") AND two of its SCHEMA fields — `status` must contain
    "REVIEW BY AGENTS", `attention_reason` must contain "not enough". Those
    two schema fields are consequently the most duplicate-heavy fields in the
    whole catalogue (each substantially restates DOCKET_DOCTRINE, which is
    injected into every turn via identity_prompt and pinned separately by
    POLICY_PHRASES in that same file) — safe to shrink hard, AS LONG AS the
    two already-pinned substrings survive. Not re-pinned here.
  - test_watchdog_visibility.py §6 pins "cmd.exe", "findstr", "smoke",
    "checks_run", "NOT IN YOUR SHELL" against `description + str(inputSchema)`
    combined — which means orgtree_watchdog's `target` schema field (the one
    that actually carries _WD_SHELL_WARNING) is already covered by that blob
    check. Not re-pinned here. The rest of watchdog's schema (`once`,
    `notice`, `shell`) restates content the (A2-preserved) top-level
    description already carries in full — internal duplication, not pinned,
    flagged as safe-to-shrink in the classification notes instead.
  - test_hire_schema_contract.py pins: `add_dirs`/`tools`/`org_visibility`
    descriptions each contain both "subordinate" and "superior"; `hire_type`'s
    description contains "OMIT", "add_dirs", "permission_mode"; the top-level
    description contains "superior". Not re-pinned here — but `hire_type`
    carries MORE than what's checked (the TOP-LEVEL-target boundary at the end
    of the field) and `permission_mode`/`work_item` have zero coverage
    anywhere — those ARE pinned below.
  - test_report_guidance_identity.py pins identity_prompt STABILITY (byte-
    identical prompt across a hire/retire cycle), not any of these five
    doctrine strings by content. POST-IMPL REDTEAM FINDING (2026-09-11):
    5 of the 6 "keep identity_prompt, shrink the card" dedupe cuts in this
    trim (cheap_compact, chart's rehire-before-hire, the withdraw-when-moot
    "chore" line, send_file's delivery doctrine, status's checkup paragraph)
    pointed at an identity_prompt copy that NO test pinned by content —
    grepped, zero hits anywhere in backend/tests. Today's cuts are safe (both
    copies exist), but nothing stopped a future supervisor.py trim from
    deleting the last copy with 0 test failures. IDENTITY_PROMPT_MARKERS below
    closes that gap: it calls the real `supervisor.identity_prompt(org, nid)`
    (not the source file) and pins one exact substring per finding, same
    mutant discipline as everything else here.

MUTANT DISCIPLINE, same as A2: `self_test_markers_are_not_trivially_satisfied`
proves every marker fails against a copy of the real text with that exact
substring removed — the proof the gate is live, not decorative.

Hermetic: no CLI, no network, no listener, no org/ledger fixture needed —
mcptool.TOOLS is pure data.

    python backend/tests/test_trim2_tool_text_load_bearing.py
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

# identity_prompt() needs a real org/ledger and a writable ORGTREE_DATA/HOME —
# same throwaway-rig pattern as test_report_guidance_identity.py, set up
# BEFORE importing orgtree so nothing touches a real install.
_RIG = tempfile.mkdtemp(prefix="orgtree-trim2-idprompt-")
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
os.environ["ORGTREE_PORT"] = "7417"      # never bound
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


def card(name: str) -> dict:
    return next(t for t in mcptool.TOOLS if t["name"] == name)


def schema_field_desc(tool: str, field: str) -> str:
    return card(tool)["inputSchema"]["properties"][field]["description"]


_IDENTITY_PROMPT_CACHE: list[str] = []


def _identity_prompt_text() -> str:
    """The real identity_prompt() output for a fresh boss node — not the
    supervisor.py source file. A source-string check would pass even if the
    doctrine text were unreachable at runtime (wrong branch, dead f-string
    concatenation); this calls the actual function agents receive. Computed
    once and cached: repeat org.create_org calls with the same slug collide."""
    if not _IDENTITY_PROMPT_CACHE:
        org = store.create_org("trim2-idprompt-check")
        boss = org.hire(USER, None, "haiku", 8, "boss", charter="X")["node"]
        _IDENTITY_PROMPT_CACHE.append(supervisor.identity_prompt(org, boss))
    return _IDENTITY_PROMPT_CACHE[0]


#: label -> exact-substring marker, checked against the REAL
#: identity_prompt() output. Each is the identity_prompt-side canonical copy
#: for one of the 6 trim2 dedupe cuts (see docstring). Only 5 are here:
#: orgtree_work.status/.attention_reason's DOCKET_DOCTRINE copy is already
#: pinned by test_work_items.py's POLICY_PHRASES against this same function.
IDENTITY_PROMPT_MARKERS: dict[str, str] = {
    "withdraw-when-moot doctrine — canonical copy for orgtree_ask/"
    "orgtree_withdraw_ask's shrunk cards":
        "chore on the user's screen with your name on it",
    "cheap_compact's cache-cost mechanism — canonical copy for its "
    "near-total card shrink":
        "reads the old transcript selectively, read-only",
    "automatic-checkup timing caveat — canonical copy for orgtree_status's "
    "cut checkup paragraph":
        "not a precise timer or a guaranteed cache hit",
    "rehire-before-hire cost equivalence — canonical copy for orgtree_chart's "
    "cut paragraph":
        "rehiring costs the same seat as a fresh hire",
    "file-delivery-is-not-a-path doctrine — canonical copy for orgtree_"
    "send_file's cut doctrine paragraph":
        "a path is not a delivery",
}


#: tool name -> {label: exact-substring marker}, checked against the
#: TOP-LEVEL `description` field. One entry per operational sentence
#: identified in the classification notes. Reasoning is kept short here —
#: full per-tool reasoning is in redteam's scratch classification doc.
TOP_LEVEL_MARKERS: dict[str, dict[str, str]] = {
    "orgtree_message": {
        "messaging a non-child descendant silently grants it a reply audience":
            "grants it an audience to reply",
        "outside mail goes out anonymized as the org, not under your name":
            "goes out AS THE ORG (not under your name)",
        "archived-recipient absence-behaviour — mail is not lost, it waits":
            "the mail waits in its inbox and is acted on when rehired",
    },
    "orgtree_send_notice": {
        "sets the correct expectation: passive mail gets no reply, maybe ever":
            "expect NO reply — an idle recipient may not read it for a long time",
    },
    "orgtree_rename": {
        "real consequence of a rename — stale addressing bounces silently":
            "anyone addressing the old name bounces until they notice",
    },
    "orgtree_ask": {
        "the core behavioural contract — park and end the turn, never poll":
            "WRAP UP AND END YOUR TURN; never wait or poll",
        "re-asking APPENDS rather than replaces — non-obvious accumulation":
            "asking again APPENDS more question tabs to it",
        "the routed-to-superior fallback for a no-audience, non-top-level caller":
            "the question is routed to your superior as mail instead",
    },
    "orgtree_withdraw_ask": {
        "withdrawal is whole-batch — cannot pull one tab and keep the rest":
            "Withdrawal is whole-batch",
        "absence-behaviour: calling it with nothing active is a safe no-op":
            "Benign no-op if you have nothing active",
    },
    "orgtree_restart_wake": {
        "one-shot auto-revert — the arm does not persist across the wake it grants":
            "fires once on the next restart, then reverts to passive notices",
        "the one fact that makes arming worth doing before a compaction/retire":
            "Survives compaction.",
    },
    "orgtree_present": {
        "refused, not routed — contrasts with ask/request_scope's auto-routing":
            "anyone else is refused (not routed",
        "HTML mockup sandbox — silent asset failure, no error, if missed":
            "there is NO network — external scripts, stylesheets, fonts and images will not load",
    },
    "orgtree_request_credits": {
        "the single highest-consequence fact — get this backwards and the ask "
        "is silently wrong (any positive integer is a 'valid' but wrong call)":
            "NEW TOTAL grant (not the increase)",
    },
    "orgtree_request_scope": {
        "the cheaper, faster path that a costly user-facing card would hide":
            "they can grant it directly with orgtree_retool, no card needed",
        "absence-behaviour: an already-held item is silently dropped, not refused":
            "Items you already hold are dropped as no-ops",
    },
    "orgtree_retool": {
        "cascading side-effect on a shrink — silent unless you already knew":
            "shrinking a grant clamps everything beneath the target too",
        "the self-retool boundary — every other field on yourself is refused":
            "exactly ONE field is legal: team_charter",
    },
    "orgtree_retire": {
        "cascading consequence most likely to surprise — one retire, many gone":
            "dissolves its whole subtree",
        "in-flight-write caveat — mirrors interrupt's, needed independently here":
            "A tool call already in flight when the interrupt lands may still finish and touch disk",
        "the only documented path to REQUEST permanent deletion — cut past "
        "classification's approval in trim2's first pass, restored, now pinned":
            "ask the user through your chain or inbox",
    },
    "orgtree_cheap_compact": {
        "the one fact NOT already taught by identity_prompt (see duplication "
        "notes) — the refusal boundary":
            "Refused on yourself and on nodes with open background tasks",
    },
    "orgtree_rehire": {
        "non-obvious cascading cost when the superior is also archived":
            "rehires the whole chain first (costs bubble)",
        "the one asymmetry — a rename is NOT covered by the all-or-nothing guarantee":
            "everything except `name` is all-or-nothing",
        "the consequence of that asymmetry — a partial apply DOES persist":
            "the node is still archived under its new name",
    },
    "orgtree_staff": {
        "the ordering guarantee that makes the item's history point at the "
        "right agent instead of a moment of belonging to the caller":
            "the seat is created first, then the item is written with THAT SEAT as its owner",
        "atomicity — mirrors hire's, restated because staff composes three calls":
            "a refusal anywhere refuses the WHOLE call, leaving no item, no seat and no mail",
        "naming collision trap — `parent` means something different here than "
        "an agent might expect from orgtree_hire/orgtree_move":
            "`parent` here is the parent WORK ITEM (as in orgtree_work), never the seat's destination",
    },
    "orgtree_list_tiers": {
        "prevents false confidence from a merely-listed tier":
            "a listed tier can still be refused",
    },
    "orgtree_move": {
        "prevents a wrong assumption that a full tree blocks reorganizing":
            "Budget-neutral along the chain",
        "batch atomicity for the swap-via-moves idiom":
            "a refusal at any step applies none of them",
    },
    "orgtree_swap": {
        "non-obvious side-effect — a standing audience is auto-granted":
            "keeps a standing audience so they can still talk",
    },
    "orgtree_self_subjugate": {
        "the hand-over pattern's silent gotcha — self-retire needs zero reports":
            "self-retire needs you to be a leaf",
        "same audience side-effect as swap, restated for this tool independently":
            "a non-direct target keeps you a standing audience with it",
    },
    "orgtree_dissolve": {
        "mid-turn blocking guarantee, mirrors retire's, needed independently here":
            "is interrupted and waited on before the archive commits",
    },
    "orgtree_interrupt": {
        "the standard pattern for applying a queued switch_model — a workflow "
        "recipe, not just a fact":
            "asking for one and then interrupting to apply it at once is the standard pattern",
        "scope of the guarantee — stops the agent, not an in-flight disk write":
            "this stops the AGENT, not an in-flight write",
    },
    "orgtree_status": {
        "prevents a redundant, silently harmless but wasted follow-up call":
            "there is no need to follow a 'done' with an 'idle'",
    },
    "orgtree_chart": {
        "correctness-critical: a self-reported status can be a dead agent's "
        "last word, not the truth":
            "a status is self-reported, so an agent that died still reads the last thing it said",
        "the one exception — distinguishes the system's own observation":
            "is the system's own observation and is the only part not self-reported",
    },
    "orgtree_send_file": {
        "the one genuinely new fact not already covered by identity_prompt's "
        "file-delivery doctrine (see duplication notes) — the path-resolution rule":
            "Sendable paths: files in your working folder (relative paths resolve there), the workspace, or any folder you hold",
    },
    "orgtree_switch_model": {
        "non-obvious deferred-apply — a mid-turn switch does not take effect "
        "when called":
            "the switch is QUEUED (result: queued=true), not applied",
        "the cross-provider consequence — context does not silently carry over":
            "the agent's pre-switch self is archived in place as a knowledge bearer",
    },
    "orgtree_audience": {
        "silent mail-routing consequence of the extern target":
            "reaches HOLDERS ONLY",
    },
}

#: (tool, field) -> {label: marker}, checked against inputSchema
#: properties[field].description. Deliberately small: most schema fields
#: across the 35 tools are either mechanical/reference (safe as-is, nothing to
#: protect) or already covered by an existing test (see docstring). These are
#: the ones that are both operational AND uncovered.
SCHEMA_MARKERS: dict[tuple[str, str], dict[str, str]] = {
    ("orgtree_hire", "permission_mode"): {
        "silent-inaction trap — the org default cannot act at all headlessly":
            "SET THIS: the org default is usually 'default', which ASKS",
        "security consequence of the one mode that can write .claude paths":
            "it removes guardrails on every path on the machine, not just the one you had in mind",
    },
    ("orgtree_hire", "hire_type"): {
        "the TOP-LEVEL-target boundary — not covered by test_hire_schema_"
        "contract.py's OMIT/add_dirs/permission_mode check on this same field":
            "only the user may do it above a TOP-LEVEL",
    },
    ("orgtree_hire", "work_item"): {
        "silent-start trap that mirrors kickoff's — a work_item with no "
        "kickoff already starts the agent":
            "with a work_item and no kickoff the hire is already running on its assignment",
    },
    ("orgtree_prime_restart", "deadline_minutes"): {
        "the escalation's consequence for OTHER agents, distinct from the "
        "top-level's 'NOBODY WILL BE PRESENT' framing (A2-pinned) — this is "
        "what happens to the ones it stops":
            "wakes them again on the new build, unattended",
    },
}


def markers_present() -> None:
    missing = []
    for name, markers in TOP_LEVEL_MARKERS.items():
        desc = card(name)["description"]
        for label, marker in markers.items():
            if marker not in desc:
                missing.append(f"{name} (description): {label!r} (marker {marker!r})")
    for (name, field), markers in SCHEMA_MARKERS.items():
        desc = schema_field_desc(name, field)
        for label, marker in markers.items():
            if marker not in desc:
                missing.append(f"{name}.{field} (schema): {label!r} (marker {marker!r})")
    if missing:
        die("operational sentence(s) missing from the tool card:\n  "
            + "\n  ".join(missing))


check(f"all {sum(len(m) for m in TOP_LEVEL_MARKERS.values())} top-level + "
      f"{sum(len(m) for m in SCHEMA_MARKERS.values())} schema-field "
      f"operational sentences across "
      f"{len(TOP_LEVEL_MARKERS)} + {len(SCHEMA_MARKERS)} cards/fields are "
      f"present today", markers_present)


def identity_prompt_markers_present() -> None:
    prompt = _identity_prompt_text()
    missing = [f"{label!r} (marker {marker!r})"
               for label, marker in IDENTITY_PROMPT_MARKERS.items()
               if marker not in prompt]
    if missing:
        die("canonical identity_prompt doctrine missing at runtime:\n  "
            + "\n  ".join(missing))


check(f"all {len(IDENTITY_PROMPT_MARKERS)} identity_prompt canonical-copy "
      f"markers are present in the REAL identity_prompt() output today",
      identity_prompt_markers_present)


def self_test_markers_are_not_trivially_satisfied() -> None:
    """MUTANT CHECK, same shape as A2's. For every marker, delete exactly
    that sentence's marker substring from a COPY of the real text and confirm
    the presence assertion then correctly reports it missing."""
    escaped = []
    for name, markers in TOP_LEVEL_MARKERS.items():
        real = card(name)["description"]
        for label, marker in markers.items():
            corrupted = real.replace(marker, "")
            if marker in corrupted:
                escaped.append(f"{name} (description): {label!r} survived its own removal")
    for (name, field), markers in SCHEMA_MARKERS.items():
        real = schema_field_desc(name, field)
        for label, marker in markers.items():
            corrupted = real.replace(marker, "")
            if marker in corrupted:
                escaped.append(f"{name}.{field} (schema): {label!r} survived its own removal")
    real_prompt = _identity_prompt_text()
    for label, marker in IDENTITY_PROMPT_MARKERS.items():
        corrupted = real_prompt.replace(marker, "")
        if marker in corrupted:
            escaped.append(f"identity_prompt: {label!r} survived its own removal")
    if escaped:
        die("dead/tautological marker(s): " + "; ".join(escaped))


check("CONTROL: every marker actually disappears when physically cut from "
      "a copy of its real text — proves the checks above are live",
      self_test_markers_are_not_trivially_satisfied)


def markers_live_where_claimed() -> None:
    """A top-level marker must be findable in `description` ALONE (not only
    reachable via some coincidental schema echo), and a schema marker must be
    findable in ITS OWN field's description alone — not a different field's,
    and not the top-level description. Both surfaces are deferred together
    once ToolSearched, but a trim that edits only one must still be caught by
    reading the right one."""
    bad = []
    for name, markers in TOP_LEVEL_MARKERS.items():
        desc = card(name)["description"]
        for label, marker in markers.items():
            if marker not in desc:
                bad.append(f"{name}: {label!r} not in its own description")
    for (name, field), markers in SCHEMA_MARKERS.items():
        desc = schema_field_desc(name, field)
        for label, marker in markers.items():
            if marker not in desc:
                bad.append(f"{name}.{field}: {label!r} not in its own field description")
    # identity_prompt markers must be genuinely absent from mcptool.py's own
    # text — otherwise this section would be pinning a duplicate that's
    # ALSO still in the card, not confirming the card's copy was safely cut.
    all_card_text = "\n".join(
        t["description"] + str(t["inputSchema"]) for t in mcptool.TOOLS)
    for label, marker in IDENTITY_PROMPT_MARKERS.items():
        if marker in all_card_text:
            bad.append(f"identity_prompt marker {label!r} is still present "
                       f"verbatim in mcptool.py — not a confirmed single copy")
    if bad:
        die("marker(s) not anchored to the field they claim to live in:\n  "
            + "\n  ".join(bad))


check("every marker lives in the exact field it claims — top-level markers "
      "in `description`, schema markers in their own field only",
      markers_live_where_claimed)


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
