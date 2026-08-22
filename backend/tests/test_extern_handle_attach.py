"""Post-hire external response handles — the attach path and its guards.

    python backend/tests/test_extern_handle_attach.py

WHY THIS EXISTS
---------------
`external_handles` (an outward `@mcp:<peer>` address a node may post_mail to
directly, from any depth, without the org-inbox audience) was writable ONLY by
`hire()`. That gap is why the in-game Prompt Wizard's "Open chat with <node>"
panels ride user mail instead of a response handle: a panel opened onto an
ALREADY-HIRED agent had no way to give it one, so the agent was never told a
panel existed and answered by ending its turn.

`ingame-prompt` (the panel's author) recorded the gap as a constraint it was
working around, not a design decision:

    "external_handles is HIRE-TIME ONLY (no post-hire attach — why windows
     ride user mail, not handles)."

This suite covers the attach path added on 2026-08-22: `set_scope`'s
`external_handles`, its shared validator, and the three things that must NOT
become possible along with it.

THE PRIVILEGE BEING GRANTED. A handle is not a label. Holding one buys a
per-address bypass in `post_mail` (ledger.py — `held_handle`), so attaching a
handle is granting an outbound channel out of the org. Hence §3: superior-only,
never self-granted. And the peer id is a BEARER credential on the read side too
— `GET /api/extern/{peer}/messages` asks for nothing else — which is why §5
pins that kiosk visitors never see it.

    §1  the validator is shared with hire (rules cannot drift)
    §2  attach / replace / clear on a live node
    §3  authority — superior-only, and a self-retool may not carry it
    §4  atomicity — a refusal writes nothing
    §5  the read surface, and the scrub that keeps it off kiosk trees
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-handle-test-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# same hub isolation every rig in this directory takes — see test_ledger.py's
# note; a throwaway ORGTREE_DATA alone does not isolate the mail hub
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

import orgtree.ledger as _ledger  # noqa: E402

# ⚠ PROVENANCE GUARD — runs BEFORE the from-import below, so a wrong checkout
# reports itself in those words instead of as a puzzling ImportError.
# On this machine PYTHONPATH points at the MAIN checkout
# (E:\...\claude-orgtree\backend), so a worktree suite that did not win the
# path race would import main's ledger and report confident numbers about code
# it never touched. The insert above beats it only because it lands at position
# 0 — an ordering assumption, so it is asserted rather than trusted. A POSITIVE
# check (this file, next to me), not an assertion of absence.
# Verified by pre-seeding sys.modules from main: fires as designed.
_want = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "orgtree",
                                      "ledger.py"))
_got = os.path.realpath(_ledger.__file__)
assert _got == _want, (
    f"\n  imported ledger.py from {_got}\n  but this suite lives beside {_want}\n"
    f"  → you are testing a DIFFERENT checkout (PYTHONPATH="
    f"{os.environ.get('PYTHONPATH')!r})")

from orgtree.ledger import (  # noqa: E402
    LedgerError, MAX_EXTERN_HANDLES, Org, USER, norm_extern_handles)

PASS = 0
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="test hire — do test things")
    s.update(over)
    return s


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def refuses(fn, *, containing=""):
    """Assert a LedgerError, and that it SAYS something identifiable — a
    refusal that fires for an unrelated reason would otherwise read as a pass."""
    try:
        fn()
    except LedgerError as e:
        assert containing.lower() in str(e).lower(), \
            f"refused, but not for the expected reason: {e!r}"
        return str(e)
    raise AssertionError("expected a LedgerError, got none")


def fixture():
    """user → boss → worker, all live."""
    org = Org.create("handles")
    boss = org.hire(USER, None, "opus", 20, "boss", **spec())["node"]
    worker = org.hire(boss, boss, "sonnet", 5, "worker", **spec())["node"]
    return org, boss, worker


H1 = "@mcp:resonite.aaaa1111"
H2 = "@mcp:resonite.bbbb2222"

print("\n§1  the validator is shared with hire")


def _validator_accepts_the_mcp_form():
    assert norm_extern_handles([H1, H2], where="hire") == [H1, H2]


def _validator_dedupes_preserving_order():
    assert norm_extern_handles([H2, H1, H2], where="hire") == [H2, H1]


def _validator_rejects_non_mcp():
    for bad in ["@org:other", "@net:x", "resonite.plain", "@mcp:", "@mcp:has space",
                "@mcp:" + "x" * 65]:
        refuses(lambda b=bad: norm_extern_handles([b], where="hire"),
                containing="@mcp:")


def _validator_caps_the_count():
    over = [f"@mcp:peer{i}" for i in range(MAX_EXTERN_HANDLES + 1)]
    refuses(lambda: norm_extern_handles(over, where="hire"),
            containing="at most")
    # …and the boundary itself is legal, or the cap would be off by one
    assert len(norm_extern_handles(over[:MAX_EXTERN_HANDLES], where="hire")) \
        == MAX_EXTERN_HANDLES


def _hire_and_retool_share_one_rulebook():
    """The point of the shared validator: a form hire refuses, retool must
    refuse too. If these ever diverge, the attach path is a hole in the same
    privilege hire is careful about."""
    org, boss, worker = fixture()
    bad = "@org:elsewhere"
    hire_msg = refuses(
        lambda: org.hire(USER, None, "sonnet", 5, "h", **spec(external_handles=[bad])),
        containing="@mcp:")
    retool_msg = refuses(
        lambda: org.set_scope(USER, worker, external_handles=[bad]),
        containing="@mcp:")
    # same rule, same complaint — only the op name differs
    assert "hire" in hire_msg and "retool" in retool_msg, (hire_msg, retool_msg)


check("validator accepts the @mcp: form", _validator_accepts_the_mcp_form)
check("validator dedupes, preserving order", _validator_dedupes_preserving_order)
check("validator rejects every non-@mcp: form", _validator_rejects_non_mcp)
check("validator caps the count (and the boundary is legal)", _validator_caps_the_count)
check("hire and retool share one rulebook", _hire_and_retool_share_one_rulebook)

print("\n§2  attach / replace / clear on a LIVE node")


def _attach_to_a_hired_node():
    """THE HEADLINE: a node hired with no handle can be given one afterwards.
    This is the whole point of the change — before it, this raised nothing and
    simply did not happen."""
    org, boss, worker = fixture()
    assert org.nodes[worker].get("external_handles") is None
    org.set_scope(USER, worker, external_handles=[H1])
    assert org.nodes[worker]["external_handles"] == [H1]


def _attach_survives_retire_rehire():
    """schema.py: 'the grant rides the seat (survives retire/rehire)'. The
    panel's 'Rehire + open' depends on it — a rehired agent must come back
    still holding the handle its panel is polling."""
    org, boss, worker = fixture()
    org.set_scope(USER, worker, external_handles=[H1])
    org.retire(boss, worker)
    org.rehire(boss, worker)
    assert org.nodes[worker]["external_handles"] == [H1]


def _replace_semantics():
    org, boss, worker = fixture()
    org.set_scope(USER, worker, external_handles=[H1])
    org.set_scope(USER, worker, external_handles=[H2])
    assert org.nodes[worker]["external_handles"] == [H2], \
        "set_scope REPLACES the set (documented) — this is not an append"


def _empty_list_clears():
    org, boss, worker = fixture()
    org.set_scope(USER, worker, external_handles=[H1])
    org.set_scope(USER, worker, external_handles=[])
    assert "external_handles" not in org.nodes[worker]


def _none_leaves_untouched():
    """The ⚙ panel sends every field on every save. A retool that does not
    mention handles must not wipe one — the same trap permission_mode's
    'only a genuine lowering sweeps' comment guards against."""
    org, boss, worker = fixture()
    org.set_scope(USER, worker, external_handles=[H1])
    org.set_scope(USER, worker, charter="a new charter, no handles mentioned")
    assert org.nodes[worker]["external_handles"] == [H1]


def _a_superior_may_attach():
    org, boss, worker = fixture()
    org.set_scope(boss, worker, external_handles=[H1])
    assert org.nodes[worker]["external_handles"] == [H1]


check("a node hired without a handle can be given one", _attach_to_a_hired_node)
check("the grant rides the seat across retire/rehire", _attach_survives_retire_rehire)
check("set_scope REPLACES the handle set", _replace_semantics)
check("[] clears the grant", _empty_list_clears)
check("None leaves an existing grant untouched", _none_leaves_untouched)
check("a direct superior may attach", _a_superior_may_attach)

print("\n§3  authority — a handle is a privilege, not a preference")


def _self_retool_may_not_carry_handles():
    """A node granting ITSELF a handle would hand itself a channel out of the
    org — precisely what the audience system exists to gate. D-105 already
    limits a self-retool to team_charter; handles must be inside that fence."""
    org, boss, worker = fixture()
    msg = refuses(lambda: org.set_scope(worker, worker, external_handles=[H1]),
                  containing="external_handles")
    assert "team_charter" in msg, \
        "the refusal should name what a self-retool MAY carry"
    assert org.nodes[worker].get("external_handles") is None


def _self_retool_of_team_charter_still_works():
    """CONTROL for the check above — the fence must not have swallowed the one
    thing a self-retool is allowed to do. Without this, a bug that refused
    every self-retool would look exactly like the guard working."""
    org, boss, worker = fixture()
    org.set_scope(worker, worker, team_charter="my own team charter")
    assert org.nodes[worker]["team_charter"] == "my own team charter"


def _a_report_may_not_attach_to_its_superior():
    org, boss, worker = fixture()
    refuses(lambda: org.set_scope(worker, boss, external_handles=[H1]),
            containing="")
    assert org.nodes[boss].get("external_handles") is None


check("a self-retool may NOT carry external_handles",
      _self_retool_may_not_carry_handles)
check("CONTROL: a self-retool of team_charter still works",
      _self_retool_of_team_charter_still_works)
check("a report may not attach a handle to its superior",
      _a_report_may_not_attach_to_its_superior)

print("\n§4  atomicity — a refusal writes nothing")


def _bad_handle_does_not_write_the_good_charter():
    """set_scope's stated contract: 'every refusal happens in THIS block,
    before a single field is written'. A call carrying a legal charter and an
    illegal handle must write NEITHER."""
    org, boss, worker = fixture()
    before = org.nodes[worker].get("charter")
    refuses(lambda: org.set_scope(USER, worker, charter="should not be written",
                                  external_handles=["not-a-handle"]),
            containing="@mcp:")
    assert org.nodes[worker].get("charter") == before, \
        "the charter was written despite the refusal — validation moved out of " \
        "the atomicity block"
    assert org.nodes[worker].get("external_handles") is None


def _over_cap_does_not_partially_write():
    org, boss, worker = fixture()
    org.set_scope(USER, worker, external_handles=[H1])
    over = [f"@mcp:peer{i}" for i in range(MAX_EXTERN_HANDLES + 1)]
    refuses(lambda: org.set_scope(USER, worker, external_handles=over),
            containing="at most")
    assert org.nodes[worker]["external_handles"] == [H1], \
        "the prior grant was disturbed by a refused call"


check("a bad handle blocks the charter beside it",
      _bad_handle_does_not_write_the_good_charter)
check("an over-cap set leaves the prior grant intact",
      _over_cap_does_not_partially_write)

print("\n§5  the read surface, and the kiosk scrub")


def _tree_exposes_handles():
    """The panel needs to FIND the handle already bound to an agent rather than
    mint a second one, so the grant has to be readable."""
    org, boss, worker = fixture()
    org.set_scope(USER, worker, external_handles=[H1])
    found = _find(org.tree(), worker)
    assert found["external_handles"] == [H1]


def _tree_defaults_to_empty_list():
    org, boss, worker = fixture()
    assert _find(org.tree(), worker)["external_handles"] == []


def _scrub_public_drops_handles():
    """A peer id is the ONLY credential GET /api/extern/{peer}/messages wants,
    so leaking it to a kiosk visitor leaks the conversation. Imported here
    rather than reimplemented — this must test the real scrubber."""
    from orgtree.api import _scrub_public
    org, boss, worker = fixture()
    org.set_scope(USER, worker, external_handles=[H1])
    tree = org.tree()
    assert _find(tree, worker)["external_handles"] == [H1]   # present before
    _scrub_public(tree)
    node = _find(tree, worker)
    assert "external_handles" not in node, \
        "a kiosk visitor can read this agent's panel channel"
    # CONTROL: the scrub removed the handle, not the node — a scrubber that
    # flattened the tree would also 'pass' the assertion above
    assert node["id"] == worker and node["title"]


def _find(tree, nid):
    def walk(n):
        if n["id"] == nid:
            return n
        for c in n.get("children") or []:
            hit = walk(c)
            if hit:
                return hit
        return None
    for r in tree["roots"]:
        hit = walk(r)
        if hit:
            return hit
    raise AssertionError(f"{nid} not in tree")


check("tree() exposes the handle set", _tree_exposes_handles)
check("tree() defaults to []", _tree_defaults_to_empty_list)
check("_scrub_public drops handles from kiosk trees", _scrub_public_drops_handles)

print(f"\n{PASS}/{PASS} passed\n")
