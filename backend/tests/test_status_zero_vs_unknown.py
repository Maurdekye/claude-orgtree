"""EXPLICIT ZERO IS NOT UNKNOWN — the reporting suite for both status chips.

    python backend/tests/test_status_zero_vs_unknown.py   (no pytest; asserts)

A status surface has three distinct things to say and only ever said two:

    · a measured value, including ZERO
    · a value we measured EARLIER and have not re-measured
    · nothing measured, ever

Collapsing the middle one into the third is what a user reads as "the thing is
broken". Reported 2026-09-01: "MCP tools = unknown, not zero" on agents hired
with `mcp: []`, and "cache status also unknown".

⚠ THE REPORT'S PREMISE WAS WRONG AND THE SUITE PINS THE TRUTH (§1). `mcp: []`
is the OPERATOR-SERVER grant list — blender, unity, and friends — not the total
MCP surface. Every agent still gets the orgtree server, so the honest answer
for such a node is 27-ish, never 0. A suite that asserted "0" would have
enshrined the misunderstanding. What was really wrong is that the count went
UNKNOWN whenever no provider process happened to be publishing, which on a
mostly-idle agent is most of the time.

    §1  a node with `mcp: []` still has a non-zero MCP surface
    §2  the API distinguishes measured-zero from unavailable
    §3  a KNOWN count carries no "no live provider process" excuse
    §4  the cache receipt keeps the LAUNCH prefix, and an unverifiable one
        is reported as its own state — never as a fabricated hit

Anti-vacuity: §2 and §3 each plant the opposite state and require the
instrument to report it, and §4 asserts the fabrication it must NOT make.
"""

import os
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
DATA = tempfile.mkdtemp(prefix="orgtree-statuszero-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import mcptool, store, supervisor                     # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def mkorg(label: str) -> tuple[str, str]:
    org = store.create_org(f"zz statuszero {label}")
    nid = org.hire(USER, None, "opus", 0, "worker", add_dirs=[],
                   tools={"bash": True, "web": False, "edit": True,
                          "subagents": False, "mcp": []},
                   org_visibility="team", charter="a status reporting test")["node"]
    store.save_org(org)
    return org.d["slug"], nid


class _Owner:
    """Stand-in for a provider process handle — identity is all that matters."""


def main() -> int:
    print("§1 `mcp: []` is an operator-server grant, not an empty tool surface")
    slug, nid = mkorg("surface")
    org = store.load_org(slug)
    check("the hire really did record an empty operator-server grant",
          lambda: eq(org.node(nid)["scope"]["tools"]["mcp"], [],
                     "scope.tools.mcp"))
    names = [t["name"] for t in mcptool.available_tools()]
    check("…yet the orgtree MCP server still supplies a NON-ZERO tool surface, "
          "so 'mcp: [] means 0 tools' is the wrong expectation",
          lambda: eq(len(names) > 0 and all(n.startswith("orgtree_")
                                            for n in names),
                     True, f"orgtree tool surface ({len(names)} tools)"))

    print("§2 measured ZERO and unavailable are different answers")
    owner = _Owner()
    supervisor._mcp_tool_count_begin(
        slug, nid, owner, "claude", "system/init.tools", "starting", None)
    st = supervisor.state(slug, nid)
    check("a freshly adopted generation is unknown, not zero "
          "(anti-vacuity: the instrument starts in the state §2 must move off)",
          lambda: eq(st.get("mcp_tool_count"), None, "count at begin"))
    # a provider that reports a tool list with no mcp__ entries HAS measured
    # zero — that is a fact about the surface, not an absence of evidence
    supervisor._mcp_tool_count_names(
        slug, nid, owner, ["Bash", "Read", "Edit"], "claude",
        "system/init.tools")
    check("a provider tool list carrying no mcp__ names publishes ZERO",
          lambda: eq(st.get("mcp_tool_count"), 0, "measured zero"))
    supervisor._mcp_tool_count_names(
        slug, nid, owner, ["Bash", "mcp__orgtree__orgtree_message",
                           "mcp__orgtree__orgtree_hire"], "claude",
        "system/init.tools")
    check("…and a list with two mcp__ names publishes 2, not 3 "
          "(built-ins are not MCP tools)",
          lambda: eq(st.get("mcp_tool_count"), 2, "measured two"))
    supervisor._mcp_tool_count_unknown(
        slug, nid, owner, "claude", "system/init.tools", "inventory lost")
    check("an unavailable inventory goes back to unknown — zero is never "
          "used to mean 'we could not ask'",
          lambda: eq(st.get("mcp_tool_count"), None, "count after unknown"))

    print("§3 a KNOWN count carries no 'no live provider process' excuse")
    # The API substitutes that phrase whenever the reason is falsy, and
    # `_mcp_tool_count_publish` clears the reason ON SUCCESS — so every node
    # with a resolved count reported that it had no process (measured
    # 2026-09-01 on five live nodes at once, all of them serving turns).
    def reason_for(count_state: str) -> str | None:
        s = supervisor.state(slug, nid)
        node_count = (int(s["mcp_tool_count"])
                      if isinstance(s.get("mcp_tool_count"), int)
                      and not isinstance(s.get("mcp_tool_count"), bool)
                      else None)
        return (str(s.get("mcp_tool_reason")) if s.get("mcp_tool_reason")
                else None if node_count is not None
                else "no live provider process")

    check("unknown WITH a stated cause keeps that cause "
          "(anti-vacuity: the reason field is live, not always-None)",
          lambda: eq(reason_for("unknown"), "inventory lost", "reason"))
    supervisor._mcp_tool_count_names(
        slug, nid, owner, ["mcp__orgtree__orgtree_message"], "claude",
        "system/init.tools")
    check("a published count reports NO reason — a known number needs no "
          "excuse, least of all a false one",
          lambda: eq(reason_for("known"), None, "reason with known count"))
    supervisor._mcp_tool_count_end(slug, nid, owner)
    check("…and a retired process is unknown again, with the honest cause",
          lambda: eq(reason_for("ended"), "no live provider process", "reason"))

    print("§4 the cache receipt keeps the LAUNCH prefix and never invents one")
    slug2, nid2 = mkorg("cache")
    org2 = store.load_org(slug2)
    n2 = org2.node(nid2)
    # ⚠ An ordinary `hire` does NOT arm the never-run pardon — only
    # cheap_compact, re-seed and rehire do. That is the whole defect: the
    # pardon was the ONLY thing standing between a fresh agent's first turn
    # and a receipt that recorded MISSING history where the truth was "nothing
    # written yet". Pinned, so nobody reads the fix below as redundant.
    check("a fresh hire does NOT carry the never-run pardon "
          "(anti-vacuity: the fix below cannot be riding on it)",
          lambda: eq(bool(n2.get("session_unrun")), False, "session_unrun"))
    check("the turn ring is empty — this node has demonstrably never run",
          lambda: eq(list(n2.get("turns") or []), [], "turn ring"))
    hist, _p = supervisor._cache_history(org2, nid2)
    check("…and a never-run session with no transcript measures as "
          "EMPTY-OBSERVED, not as missing evidence",
          lambda: eq(hist, {"bytes": 0, "sha256":
                            "e3b0c44298fc1c149afbf4c8996fb924"
                            "27ae41e4649b934ca495991b7852b855"}, "history"))
    # Now make the node one that HAS run, with its transcript gone. That is
    # amnesia (№31), and it must still measure as missing.
    n2.setdefault("turns", []).append({"at": "2026-09-01T00:00:00Z",
                                       "cost": 0.0, "ms": 1, "denials": 0})
    store.save_org(org2)
    org2 = store.load_org(slug2)
    hist2, _p2 = supervisor._cache_history(org2, nid2)
    check("a node that HAS booked a turn but has no transcript still "
          "measures None — real amnesia is not laundered into empty history",
          lambda: eq(hist2, None, "history for a run node with no transcript"))
    check("a receipt built on missing history reports its prefix as "
          "unobserved — never as a hit",
          lambda: eq(supervisor._cache_history_relation(_p2, None),
                     "unobserved", "relation for missing history"))
    check("…while an observed-empty prefix VERIFIES against the same absent "
          "file (anti-vacuity: the relation check can return something other "
          "than 'unobserved')",
          lambda: eq(supervisor._cache_history_relation(
              _p2, {"bytes": 0, "sha256":
                    "e3b0c44298fc1c149afbf4c8996fb924"
                    "27ae41e4649b934ca495991b7852b855"}),
              "unobserved", "relation for observed-empty against no file"))

    print()
    for label, tb in FAIL:
        print(f"--- FAILED: {label}\n{tb}")
    print(f"{PASS} checks passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
