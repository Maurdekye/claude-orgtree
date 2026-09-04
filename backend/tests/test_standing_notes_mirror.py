"""An agent's own standing notes reach it on every lane, not just the one
whose CLI happens to read the file.

THE DEFECT. Every agent's system prompt ends with "keep a CLAUDE.md there as
standing notes", and it used to promise the file was "loaded automatically
every turn". That is a claim about a CLI, and it was only ever true of the
claude one:

  * codex reads `AGENTS.md` and NEVER `CLAUDE.md` - measured 2026-09-04 with
    `codex debug prompt-input`, which renders the model-visible prompt with no
    API call. `project_doc_fallback_filenames` defaults to `[]` in the
    binary's own embedded defaults.
  * antigravity reads its own `AGENTS.md` the same way.

So a codex or antigravity agent maintained a compaction-survival file that
NOTHING READ, and found out when a compaction destroyed the context the file
existed to protect. Two live seats were verified in that state before the fix.
It is the same shape as `org.md` reaching zero agents: a mechanism everyone
believes in that does nothing, and the belief is what stops anyone looking.

WHAT THIS PINS. Delivery on the lanes that need it, the exclusion of the lanes
that do not - IN BOTH DIRECTIONS, because a lane-conditional asserted one way
passes just as happily when the condition inverts - invalidation, and the
observable-failure behaviour.

⚠ THE OPENROUTER TRAP HAS ITS OWN CHECK. `providers.provider_of` answers
"which provider", which is NOT the same question as "does its CLI read
CLAUDE.md". OpenRouter tiers are not a separate lane at all: they run the SAME
claude CLI with an `ANTHROPIC_BASE_URL` override, so the file IS loaded
natively there. A predicate written as `provider_of(tier) != "claude"` looks
right, passes a codex/claude test pair, and hands every OpenRouter agent the
text twice.

Falsifiers, all verified to turn exactly their own group red:

M1 render on every lane (drop the exclusion)   -> group exclusion FAILS
M2 predicate as `provider_of(tier) != claude`  -> group exclusion FAILS
M3 swallow an unreadable notes file            -> group observable FAILS
M4 tail-cut instead of head-cut                -> group truncation FAILS
M5 exclude codex too (delivered nowhere)       -> group delivery FAILS

Hermetic: throwaway data/home, no CLI, listener, network or production journal.

    python backend/tests/test_standing_notes_mirror.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

RIG = tempfile.mkdtemp(prefix="orgtree-standing-notes-")
TEST_HOME = os.path.join(RIG, "home")
DATA = os.path.join(RIG, "data")
for _d in (TEST_HOME, DATA):
    os.makedirs(_d, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["USERPROFILE"] = TEST_HOME
os.environ["HOME"] = TEST_HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_PORT"] = "7419"             # never bound
os.environ["ORGTREE_WARM"] = "1"

from orgtree import openrouter, providers, store                # noqa: E402
from orgtree import supervisor as S, warmpool as W              # noqa: E402
from orgtree.ledger import USER                                 # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []

MARK = "ZZ-standing-notes-marker-ZZ"
OPEN_TAG = "[YOUR STANDING NOTES"
CLOSE_TAG = "[END YOUR STANDING NOTES]"
UNREADABLE = "PRESENT BUT UNREADABLE"


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                       # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


org = store.create_org("standing notes rig")
SLUG = org.d["slug"]
# one seat per lane. `orouter` is hired on a claude tier and then RE-TIERED to
# an or- name: the point is the prompt's predicate, not the hire validator.
for nid, tier in (("codexseat", "sol"), ("agyseat", "flash"),
                  ("claudeseat", "haiku"), ("orouter", "haiku")):
    org.hire(USER, None, tier, 5, nid, add_dirs=[], tools={"mcp": []},
             org_visibility="full", charter="c")
org.nodes["orouter"]["model"] = openrouter.TIER_PREFIX + "test-model"
store.save_org(org)

LANES = ("codexseat", "agyseat", "claudeseat", "orouter")
MIRRORED = ("codexseat", "agyseat")
NATIVE = ("claudeseat", "orouter")


def notes_path(nid: str) -> str:
    d = S.scratch_dir(SLUG, nid)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "CLAUDE.md")


def write_notes(nid: str, text: str) -> None:
    with open(notes_path(nid), "w", encoding="utf-8", newline="") as f:
        f.write(text)


def clear_notes(nid: str | None = None) -> None:
    for who in ([nid] if nid else list(LANES)):
        try:
            os.remove(notes_path(who))
        except OSError:
            pass


def prompt(nid: str) -> str:
    return S.identity_prompt(store.load_org(SLUG), nid)


#: A codex identity_snapshot normally calls _codex_process_spec, which refuses
#: unless the machine is signed in to codex - and this rig deliberately owns a
#: throwaway HOME, so it never is. The spec is stubbed so the argv/cred/envov
#: components are constant and the PROMPT component, which is what these
#: checks are about, stays entirely real.
_STUB_SPEC = {"argv_head": ["codex"], "config_overrides": [],
              "exe": "codex", "env_extra": {}}


def snapshot(nid: str):
    org = store.load_org(SLUG)
    if str(org.node(nid).get("model") or "") in providers.CODEX_TIERS:
        return W.identity_snapshot(org, nid, provider_spec=_STUB_SPEC)
    return W.identity_snapshot(org, nid)


# ------------------------------------------------------------ group delivery
def t_a_codex_seat_receives_its_own_notes() -> None:
    """THE HEADLINE, on the lane the promise was false for."""
    clear_notes()
    assert MARK not in prompt("codexseat"), (
        "rig is dirty before the notes exist")
    write_notes("codexseat", "remember this " + MARK + "\n")
    try:
        p = prompt("codexseat")
        assert MARK in p, (
            "a codex agent did not receive its own standing notes - it is "
            "keeping a compaction-survival file that nothing reads")
        assert OPEN_TAG in p and CLOSE_TAG in p, "the block is not delimited"
    finally:
        clear_notes()
    assert MARK not in prompt("codexseat"), (
        "deleting the notes left their text in the prompt")


def t_an_antigravity_seat_receives_its_own_notes() -> None:
    clear_notes()
    write_notes("agyseat", "agy notes " + MARK + "\n")
    try:
        assert MARK in prompt("agyseat"), (
            "an antigravity agent did not receive its own standing notes")
    finally:
        clear_notes()


def t_one_seats_notes_never_reach_another() -> None:
    """The notes are the agent's OWN. A scratch is per-seat; if this ever
    fails, one agent is reading another's private working file."""
    clear_notes()
    write_notes("codexseat", "private to codexseat " + MARK + "\n")
    try:
        assert MARK not in prompt("agyseat"), (
            "one agent's standing notes reached a DIFFERENT agent's prompt")
    finally:
        clear_notes()


# ----------------------------------------------------------- group exclusion
def t_a_claude_seat_is_not_given_a_second_copy() -> None:
    """The exclusion, direction one. The claude CLI loads `<cwd>/CLAUDE.md`
    itself and D-206 already fingerprints it, so rendering here would be a
    second source of truth for the same text."""
    clear_notes()
    write_notes("claudeseat", "claude notes " + MARK + "\n")
    try:
        p = prompt("claudeseat")
        assert MARK not in p, (
            "a claude agent was handed its scratch CLAUDE.md in the prompt as "
            "well as through its CLI - the same text twice")
        assert OPEN_TAG not in p, "the notes block rendered on the claude lane"
    finally:
        clear_notes()


def t_an_openrouter_seat_is_not_given_a_second_copy() -> None:
    """The exclusion, direction two, and the trap. OpenRouter tiers are NOT a
    separate lane: they run the same claude CLI with an ANTHROPIC_BASE_URL
    override, so the file is loaded natively there too. A predicate written as
    `provider_of(tier) != "claude"` passes every other check in this file and
    fails only here."""
    assert providers.provider_of(
        store.load_org(SLUG).node("orouter")["model"]) != "claude", (
        "the openrouter seat is not on an openrouter tier - this check would "
        "be vacuous")
    clear_notes()
    write_notes("orouter", "openrouter notes " + MARK + "\n")
    try:
        assert MARK not in prompt("orouter"), (
            "an OpenRouter agent was handed its CLAUDE.md in the prompt as "
            "well as through the claude CLI it actually runs")
    finally:
        clear_notes()


def t_the_exclusion_is_not_vacuous() -> None:
    """POSITIVE CONTROL. The two checks above prove nothing unless the very
    same file, in the very same place, IS rendered on a lane that needs it."""
    clear_notes()
    for who in LANES:
        write_notes(who, "identical text " + MARK + "\n")
    try:
        got = {who: (MARK in prompt(who)) for who in LANES}
        assert all(got[w] for w in MIRRORED), (
            f"the mirrored lanes did not receive identical notes: {got}")
        assert not any(got[w] for w in NATIVE), (
            f"a native-loading lane received a duplicate: {got}")
    finally:
        clear_notes()


def t_an_unknown_tier_is_treated_as_native_because_it_runs_that_cli() -> None:
    """An unrecognised tier resolves to the claude lane, and that is CORRECT
    rather than a fallback: the turn dispatcher takes its codex and
    antigravity legs on TIER MEMBERSHIP and lets everything else through to
    the claude machinery. So an unknown tier really does run the claude CLI
    and really does load the file natively - mirroring it would duplicate.

    Pinned against the dispatcher's own tables, so that if a future lane is
    selected some other way this check stops being true rather than stops
    being noticed."""
    unknown = "zz-not-a-real-tier"
    assert unknown not in providers.CODEX_TIERS, "the premise moved"
    assert unknown not in providers.ANTIGRAVITY_TIERS, "the premise moved"
    assert not openrouter.is_tier(unknown), "the premise moved"
    assert providers.provider_of(unknown) == "claude", (
        "provider_of no longer sends an unknown tier to the claude lane; the "
        "mirror's native/mirrored split needs re-deciding")

    o = store.load_org(SLUG)
    saved = o.nodes["codexseat"]["model"]
    o.nodes["codexseat"]["model"] = unknown
    store.save_org(o)
    clear_notes()
    write_notes("codexseat", "unknown lane " + MARK + "\n")
    try:
        assert MARK not in prompt("codexseat"), (
            "an unknown tier was mirrored, but it runs the claude CLI, which "
            "loads the file itself - that is the same text twice")
    finally:
        clear_notes()
        o = store.load_org(SLUG)
        o.nodes["codexseat"]["model"] = saved
        store.save_org(o)


# ------------------------------------------------------- group invalidation
def t_editing_notes_moves_a_codex_seats_identity() -> None:
    """Delivery without invalidation is the defect one layer up: the parked
    design appended the notes to AGENTS.md at spawn, so a PARKED process would
    keep serving yesterday's notes while the file said otherwise. Riding the
    prompt makes the invalidation the same fix, not a second one."""
    clear_notes()
    empty = snapshot("codexseat")
    write_notes("codexseat", "notes alpha\n")
    alpha = snapshot("codexseat")
    assert empty[0] != alpha[0], (
        "creating a codex seat's notes did not move its identity - a parked "
        "process would keep serving without them")
    fields = W.identity_change_fields(empty[0], empty[1], alpha[0], alpha[1])
    assert fields["changed_inputs"] == ["prompt"], fields

    write_notes("codexseat", "notes beta\n")
    assert snapshot("codexseat")[0] != alpha[0], (
        "editing the notes did not move the hash")
    write_notes("codexseat", "notes alpha\n")
    assert snapshot("codexseat") == alpha, (
        "restoring the notes text did not restore identity")
    clear_notes()
    assert snapshot("codexseat") == empty, (
        "deleting the notes did not restore the absent-file identity")


def t_the_claude_lane_still_invalidates_natively() -> None:
    """D-206 must be untouched. The claude seat gets no prompt block, so if
    its notes stopped moving its identity the exclusion would have quietly
    removed an invalidation instead of a duplication."""
    clear_notes()
    before = snapshot("claudeseat")
    write_notes("claudeseat", "claude notes\n")
    try:
        assert snapshot("claudeseat")[0] != before[0], (
            "a claude seat's scratch CLAUDE.md stopped moving its identity - "
            "the D-206 guarantee has regressed")
    finally:
        clear_notes()
    assert snapshot("claudeseat") == before, "removal did not restore identity"


# --------------------------------------------------------- group observable
def t_unreadable_notes_are_announced() -> None:
    """A file that exists and cannot be read must SAY so. Silence is
    indistinguishable from having written no notes, and that is the state in
    which an agent assumes it simply never wrote any."""
    clear_notes()
    p = notes_path("codexseat")
    os.makedirs(p, exist_ok=True)                 # a directory cannot be read
    try:
        out = prompt("codexseat")
        assert UNREADABLE in out, (
            "unreadable standing notes rendered nothing at all")
        notice = out[out.index(OPEN_TAG):out.index("]", out.index(OPEN_TAG))]
        assert RIG not in notice, (
            "the failure notice leaked a host path into the prompt")
    finally:
        shutil.rmtree(p, ignore_errors=True)


def t_absent_and_empty_notes_are_silent() -> None:
    """Having written no notes is a real answer; a permanent nag is not."""
    clear_notes()
    assert OPEN_TAG not in prompt("codexseat"), "absent notes rendered a block"
    assert UNREADABLE not in prompt("codexseat"), (
        "absent notes rendered a notice")
    write_notes("codexseat", "   \n\n \n")
    try:
        assert OPEN_TAG not in prompt("codexseat"), (
            "whitespace-only notes rendered a block")
    finally:
        clear_notes()


# --------------------------------------------------------- group truncation
def t_oversize_notes_are_head_cut_and_say_so() -> None:
    """Head-taken, unlike breadcrumbs.md's tail: notes are a curated document,
    not an append log, so the top is the part meant to be read first. The cut
    is declared, because a silent one drops what the agent left itself with
    no trace that anything is missing."""
    clear_notes()
    body = "A" * (S.STANDING_NOTES_MAX + 5000)
    text = "FRONT-" + MARK + body + "-TAIL-" + MARK
    write_notes("codexseat", text)
    try:
        p = prompt("codexseat")
        assert "TRUNCATED" in p, "oversize notes were cut silently"
        assert "FRONT-" + MARK in p, "the head of the notes was dropped"
        assert "-TAIL-" + MARK not in p, "the notes were not actually cut"
        # ...and the SCALE of the cut, not merely that one happened. The bound
        # alone says a cut occurred; how much is gone is what tells the agent
        # whether to go open the file or carry on. Two live scratch files were
        # measured over this bound on 2026-09-04, so this fires in practice.
        assert str(len(text)) in p, \
            f"the notice does not state the file's TRUE length ({len(text)})"
        assert str(len(text) - S.STANDING_NOTES_MAX) in p, \
            ("the notice does not say how much is missing "
             f"({len(text) - S.STANDING_NOTES_MAX} chars)")
    finally:
        clear_notes()


def t_notes_that_fit_carry_no_truncation_notice() -> None:
    """POSITIVE CONTROL for the check above: without this, a notice that fired
    unconditionally would satisfy every assertion there and mean nothing."""
    clear_notes()
    write_notes("codexseat", "FRONT-" + MARK + ("B" * 200) + "-TAIL-" + MARK)
    try:
        p = prompt("codexseat")
        assert "TRUNCATED" not in p, "short notes claimed to be truncated"
        assert "-TAIL-" + MARK in p, "short notes lost their tail anyway"
    finally:
        clear_notes()


# ------------------------------------------------------------- group promise
def t_the_prompt_no_longer_promises_what_it_cannot_keep() -> None:
    """The sentence that started this. It told EVERY agent the file was
    "loaded automatically every turn" - a claim about a CLI that was true on
    exactly one lane. It must not come back, on any lane."""
    clear_notes()
    for who in LANES:
        p = prompt(who)
        assert "loaded automatically every turn" not in p, (
            f"{who}'s prompt still makes the false every-turn promise")
        assert "keep a CLAUDE.md there as standing notes" in p, (
            f"{who} is no longer told to keep standing notes at all")


def main() -> int:
    print("group delivery: the lanes whose CLI cannot read the file")
    check("a codex seat receives its own standing notes",
          t_a_codex_seat_receives_its_own_notes)
    check("an antigravity seat receives its own standing notes",
          t_an_antigravity_seat_receives_its_own_notes)
    check("one seat's notes never reach another",
          t_one_seats_notes_never_reach_another)
    print("group exclusion: the lanes that already load it, both directions")
    check("a claude seat is not given a second copy",
          t_a_claude_seat_is_not_given_a_second_copy)
    check("an OpenRouter seat is not given a second copy (the trap)",
          t_an_openrouter_seat_is_not_given_a_second_copy)
    check("POSITIVE CONTROL: the same file IS rendered where it is needed",
          t_the_exclusion_is_not_vacuous)
    check("an unknown tier counts as native because it runs that CLI",
          t_an_unknown_tier_is_treated_as_native_because_it_runs_that_cli)
    print("group invalidation: a parked process cannot serve stale notes")
    check("editing a codex seat's notes moves and restores its identity",
          t_editing_notes_moves_a_codex_seats_identity)
    check("the claude lane still invalidates natively (D-206 intact)",
          t_the_claude_lane_still_invalidates_natively)
    print("group observable: a failed delivery says so")
    check("unreadable notes are announced in the prompt",
          t_unreadable_notes_are_announced)
    check("absent and empty notes render nothing",
          t_absent_and_empty_notes_are_silent)
    print("group truncation")
    check("oversize notes are head-cut, and the cut declares its SCALE",
          t_oversize_notes_are_head_cut_and_say_so)
    check("POSITIVE CONTROL: notes that fit carry no truncation notice",
          t_notes_that_fit_carry_no_truncation_notice)
    print("group promise: the sentence that started this")
    check("the false every-turn promise is gone on every lane",
          t_the_prompt_no_longer_promises_what_it_cannot_keep)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n[X] {label}\n{tb}")
        print(f"standing-notes-mirror: {PASS} passed - {len(FAIL)} FAILED")
        return 1
    print(f"standing-notes-mirror: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(RIG, ignore_errors=True)
    sys.exit(rc)
