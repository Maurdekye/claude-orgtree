"""The ORG CHARTER (org.md) reaches every agent, on every provider, through
the managed system prompt.

User ruling 2026-09-04, verbatim: "org.md needs to automatically reach every
single agent from every provider via the system prompt. if thats not currently
what happens, that needs to be changed to do so."

It did not. <workspace>/CLAUDE.md was delivered ONLY by a provider's own
project-doc loader picking the file up, so its reach was an accident of:

  * whether the agent held the workspace as a folder grant. Most seats hold no
    grants at all, and they got the charter ZERO times - the same node this
    suite calls `nogrant`, which is the ordinary case, not the exotic one.
  * which CLI it runs. Codex reads AGENTS.md and never CLAUDE.md, so on that
    lane the file was not an input by any route (measured 2026-09-04 with
    `codex debug prompt-input`).
  * whether the org is sandboxed, where the host workspace path does not exist.

The fix routes it through `identity_prompt`, which orgtree writes itself on
all three lanes. What this suite pins is the DELIVERY, its labelling, its
observable failure, and the fact that it did not quietly duplicate the text
for the one class of agent that used to receive it.

Falsifiers supplied here against the real implementation:

F1 neutralise `_org_charter_block` (pre-fix reach)   -> groups 1 and 3 FAIL
F2 let `_claudemd_block` render the workspace again  -> group 4 FAIL
F3 render the block only for the claude lane         -> group 2 FAIL
F4 return "" on an unreadable charter (silent)       -> group 5 FAIL

F1 is run inline as a check. F2-F4 are mutants applied by hand; the run that
accepted this file confirmed each one turns its group red.

Hermetic: throwaway data/home/workspace, no CLI, listener, network or
production journal.

    python backend/tests/test_org_charter_prompt.py
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

RIG = tempfile.mkdtemp(prefix="orgtree-org-charter-")
TEST_HOME = os.path.join(RIG, "home")
DATA = os.path.join(RIG, "data")
WS = os.path.join(RIG, "workspace")          # deliberately NOT under scratch
for _d in (TEST_HOME, DATA, WS):
    os.makedirs(_d, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["USERPROFILE"] = TEST_HOME
os.environ["HOME"] = TEST_HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_PORT"] = "7417"             # never bound
os.environ["ORGTREE_WARM"] = "1"

from orgtree import store, supervisor as S, warmpool as W  # noqa: E402
from orgtree.ledger import USER                            # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []

MARK = "ZZ-charter-marker-ZZ"
OPEN_TAG = "[ORG CHARTER"
CLOSE_TAG = "[END ORG CHARTER]"
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


def write_charter(text: str) -> None:
    with open(ORGMD, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def remove_charter() -> None:
    try:
        os.remove(ORGMD)
    except OSError:
        pass


org = store.create_org("org charter rig")
SLUG = org.d["slug"]
org.d["workspace"] = WS
# `holder` holds the workspace grant - the ONLY class of agent the old
# file-loader route ever reached. `nogrant` holds nothing, which is what a
# normal seat looks like. The codex/agy seats prove the block is not gated on
# a lane.
org.hire(USER, None, "haiku", 5, "holder",
         add_dirs=[{"path": WS, "mode": "rw"}],
         tools={"mcp": []}, org_visibility="full", charter="c")
org.hire(USER, None, "haiku", 5, "nogrant", add_dirs=[],
         tools={"mcp": []}, org_visibility="full", charter="c")
org.hire(USER, None, "sol", 5, "codexseat", add_dirs=[],
         tools={"mcp": []}, org_visibility="full", charter="c")
org.hire(USER, None, "flash", 5, "agyseat", add_dirs=[],
         tools={"mcp": []}, org_visibility="full", charter="c")
store.save_org(org)

ORGMD = os.path.join(WS, "CLAUDE.md")
LANES = (("holder", "haiku"), ("nogrant", "haiku"),
         ("codexseat", "sol"), ("agyseat", "flash"))


def prompt(nid: str) -> str:
    return S.identity_prompt(store.load_org(SLUG), nid)


def snapshot(nid: str):
    return W.identity_snapshot(store.load_org(SLUG), nid)


# ---------------------------------------------------------------- group 1
def t_reaches_a_node_with_no_grants() -> None:
    """THE HEADLINE. A seat with no folder grants is the ordinary seat, and it
    is exactly the one the old route reached zero times."""
    remove_charter()
    assert MARK not in prompt("nogrant"), (
        "rig is dirty before the charter exists")
    write_charter("standing org rule " + MARK + "\n")
    try:
        p = prompt("nogrant")
        assert MARK in p, (
            "an agent with no folder grants did not receive the org charter - "
            "this is the exact defect the user ruling was about")
        assert OPEN_TAG in p and CLOSE_TAG in p, "the block is not delimited"
    finally:
        remove_charter()
    assert MARK not in prompt("nogrant"), (
        "deleting the charter left its text in the prompt")


def t_absent_charter_is_silent() -> None:
    """A charter that was never written is a real answer, and must not put a
    notice in every agent's prompt forever."""
    remove_charter()
    p = prompt("nogrant")
    assert OPEN_TAG not in p, "an absent charter still rendered a block header"
    assert UNREADABLE not in p, "an absent charter rendered a failure notice"


def t_empty_charter_is_silent() -> None:
    remove_charter()
    write_charter("   \n\n  \n")
    try:
        assert OPEN_TAG not in prompt("nogrant"), (
            "a whitespace-only charter rendered a block")
    finally:
        remove_charter()


# ---------------------------------------------------------------- group 2
def t_every_lane_gets_the_same_block() -> None:
    """F3: no lane conditional. The point of routing through identity_prompt is
    that all three providers are written by orgtree from this one string."""
    remove_charter()
    write_charter("cross lane " + MARK + "\n")
    try:
        for nid, tier in LANES:
            model = str(store.load_org(SLUG).node(nid).get("model") or "")
            assert model == tier, (nid, model, tier)
            assert MARK in prompt(nid), (
                f"{nid} (model {tier}) did not receive the org charter - the "
                f"block is gated on a lane")
    finally:
        remove_charter()


def t_lane_coverage_is_not_vacuous() -> None:
    """The check above proves nothing unless the three tiers really are three
    different provider legs. Pin that against the provider tables."""
    from orgtree import providers
    assert "sol" in providers.CODEX_TIERS, "sol is no longer a codex tier"
    assert "flash" in providers.ANTIGRAVITY_TIERS, \
        "flash is no longer an antigravity tier"
    claude = {t["tier"] for t in providers.claude_tiers()}
    assert "haiku" in claude, "haiku is no longer a claude tier"
    both = set(providers.CODEX_TIERS) & set(
        providers.ANTIGRAVITY_TIERS)
    assert not both, \
        "codex and antigravity tiers overlap; the lane split is not what "\
        "this suite assumes"


# ---------------------------------------------------------------- group 3
def t_charter_edit_moves_the_identity_of_a_node_with_no_grants() -> None:
    """Delivery without invalidation is the defect one layer up: a parked
    process would keep serving the old charter. The native digest cannot cover
    `nogrant` (it holds no grant), so this can only come from the prompt."""
    remove_charter()
    empty = snapshot("nogrant")
    write_charter("charter alpha\n")
    alpha = snapshot("nogrant")
    assert empty[0] != alpha[0], (
        "creating the org charter did not move a grantless agent's identity - "
        "a parked process would keep serving without it")
    fields = W.identity_change_fields(empty[0], empty[1], alpha[0], alpha[1])
    assert fields["changed_inputs"] == ["prompt"], fields

    write_charter("charter beta\n")
    beta = snapshot("nogrant")
    assert alpha[0] != beta[0], "editing the charter did not move the hash"

    write_charter("charter alpha\n")
    assert snapshot("nogrant") == alpha, (
        "restoring the charter text did not restore identity")
    remove_charter()
    assert snapshot("nogrant") == empty, (
        "deleting the charter did not restore the absent-file identity")


def t_falsifier_pre_fix_reach_reproduces_the_fault() -> None:
    """F1 - the instrument must prove it can see the fault it exists for.

    Neutralise the block, which is precisely what the code did before: the
    charter must then be invisible to a grantless seat, and its edits must move
    no identity. If the fault does not reproduce, the checks above are green
    for some reason other than the fix."""
    remove_charter()
    real = S._org_charter_block
    S._org_charter_block = lambda _org: ""
    try:
        blind_before = snapshot("nogrant")
        write_charter("an edit the pre-fix prompt cannot see " + MARK + "\n")
        assert MARK not in prompt("nogrant"), (
            "the planted fault did not reproduce: the charter reached the "
            "prompt with the block neutralised, so this suite is measuring "
            "something else")
        assert snapshot("nogrant")[0] == blind_before[0], (
            "the planted fault did not reproduce at the identity layer")
    finally:
        S._org_charter_block = real
    try:
        assert MARK in prompt("nogrant"), (
            "with the block restored the same edit was still invisible")
    finally:
        remove_charter()


# ---------------------------------------------------------------- group 4
def t_workspace_holder_gets_the_charter_exactly_once() -> None:
    """F2. The holder is the one seat that DID receive org.md before, through
    `_claudemd_block`'s granted-folder injection. Adding the charter block
    without removing that would hand it the same text twice, under two
    different headings - a duplicated instruction, which is its own defect."""
    remove_charter()
    write_charter("only once " + MARK + "\n")
    try:
        p = prompt("holder")
        assert p.count(MARK) == 1, (
            f"the workspace holder received the org charter {p.count(MARK)} "
            f"times; it must arrive exactly once, in the ORG CHARTER block")
        assert MARK not in S._claudemd_block(store.load_org(SLUG), "holder"), (
            "the granted-folder block still renders the workspace CLAUDE.md")
    finally:
        remove_charter()


def t_other_granted_folders_still_render() -> None:
    """The exclusion is the WORKSPACE only. A different granted folder's
    CLAUDE.md must keep arriving, or this fix has quietly deleted a feature."""
    other = os.path.join(RIG, "otherdir")
    os.makedirs(other, exist_ok=True)
    with open(os.path.join(other, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write("folder note " + MARK + "-other\n")
    o = store.load_org(SLUG)
    o.nodes["holder"]["scope"]["add_dirs"] = [
        {"path": WS, "mode": "rw"}, {"path": other, "mode": "rw"}]
    store.save_org(o)
    try:
        assert MARK + "-other" in prompt("holder"), (
            "a non-workspace granted folder's CLAUDE.md stopped "
            "being rendered")
    finally:
        o = store.load_org(SLUG)
        o.nodes["holder"]["scope"]["add_dirs"] = [{"path": WS, "mode": "rw"}]
        store.save_org(o)


# ---------------------------------------------------------------- group 5
def t_unreadable_charter_is_announced_not_swallowed() -> None:
    """F4, and the day's recurring defect class: something that looks correct
    and does nothing. A charter that exists but cannot be read must SAY so in
    the prompt - the agent is the only party able to report that the
    operator's directive did not arrive."""
    remove_charter()
    os.makedirs(ORGMD, exist_ok=True)          # a directory cannot be read
    try:
        p = prompt("nogrant")
        assert UNREADABLE in p, (
            "an unreadable org charter rendered nothing at all - silent "
            "absence is indistinguishable from an org with no charter")
        notice = p[p.index(OPEN_TAG):p.index(chr(93), p.index(OPEN_TAG)) + 1]
        assert UNREADABLE in notice, notice
        # host paths are the operator's, not the org's (api.py _public_slug):
        # scope this to the NOTICE, since the prompt legitimately names the
        # agent's own scratch elsewhere.
        assert RIG not in notice and WS not in notice, (
            "the failure notice leaked a host path into the agent's prompt")
    finally:
        try:
            os.rmdir(ORGMD)
        except OSError:
            shutil.rmtree(ORGMD, ignore_errors=True)


def t_no_workspace_configured_is_silent() -> None:
    o = store.load_org(SLUG)
    saved = o.d.get("workspace")
    o.d["workspace"] = ""
    try:
        assert S._org_charter_block(o) == "", (
            "an org with no workspace rendered a charter block")
    finally:
        o.d["workspace"] = saved


# ---------------------------------------------------------------- group 6
def t_charter_is_labelled_as_an_acting_directive() -> None:
    """It is an instruction from the operator, not reference material, and it
    is placed with the charters rather than among the tool notes."""
    remove_charter()
    write_charter("directive " + MARK + "\n")
    try:
        p = prompt("nogrant")
        head = p[p.index(OPEN_TAG):p.index("]", p.index(OPEN_TAG))]
        for word in ("EVERY agent", "every provider", "ACT ON THESE",
                     "directives, not reference material"):
            assert word in head, f"the block header does not say {word!r}"
        assert p.index(OPEN_TAG) < p.index("Folders you may work in"), (
            "the org charter is rendered below the operational text; it is a "
            "standing directive and belongs with the charters")
    finally:
        remove_charter()


def t_oversize_charter_is_cut_at_the_head_and_says_so() -> None:
    """Head-taken, unlike the breadcrumbs tail, and the cut is declared. A
    silent truncation would drop operator instructions with no trace."""
    remove_charter()
    body = "A" * (S.ORG_CHARTER_MAX + 5000)
    write_charter("FRONT-" + MARK + body + "-TAIL-" + MARK)
    try:
        p = prompt("nogrant")
        assert "TRUNCATED" in p, "an oversize charter was cut silently"
        assert "FRONT-" + MARK in p, "the head of the charter was dropped"
        assert "-TAIL-" + MARK not in p, (
            "the charter was not actually truncated")
        block = p[p.index(OPEN_TAG):p.index(CLOSE_TAG)]
        assert len(block) < S.ORG_CHARTER_MAX + 2000, (
            "the truncation did not bound the block")
    finally:
        remove_charter()


def main() -> int:
    print("group 1: it reaches an ordinary seat at all")
    check("a node with NO folder grants receives the org charter",
          t_reaches_a_node_with_no_grants)
    check("an absent charter renders nothing", t_absent_charter_is_silent)
    check("a whitespace-only charter renders nothing",
          t_empty_charter_is_silent)
    print("group 2: every provider lane")
    check("claude, codex and antigravity seats all receive it",
          t_every_lane_gets_the_same_block)
    check("those three tiers really are three different lanes",
          t_lane_coverage_is_not_vacuous)
    print("group 3: invalidation, so a parked process cannot serve "
          "the old charter")
    check("a charter edit moves and restores a grantless agent's identity",
          t_charter_edit_moves_the_identity_of_a_node_with_no_grants)
    check("pre-fix reach reproduces the fault this suite exists to catch",
          t_falsifier_pre_fix_reach_reproduces_the_fault)
    print("group 4: no duplication for the seat that already had it")
    check("a workspace holder receives the charter exactly once",
          t_workspace_holder_gets_the_charter_exactly_once)
    check("a non-workspace granted folder's CLAUDE.md still renders",
          t_other_granted_folders_still_render)
    print("group 5: failure is observable")
    check("an unreadable charter is announced in the prompt",
          t_unreadable_charter_is_announced_not_swallowed)
    check("an org with no workspace renders nothing",
          t_no_workspace_configured_is_silent)
    print("group 6: how it reads to the model")
    check("the block is labelled an acting directive and placed with charters",
          t_charter_is_labelled_as_an_acting_directive)
    check("an oversize charter is head-cut and the cut is declared",
          t_oversize_charter_is_cut_at_the_head_and_says_so)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n[X] {label}\n{tb}")
        print(f"org-charter-prompt: {PASS} passed - {len(FAIL)} FAILED")
        return 1
    print(f"org-charter-prompt: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(RIG, ignore_errors=True)
    sys.exit(rc)
