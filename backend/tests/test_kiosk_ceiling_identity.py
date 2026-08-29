"""D-182: the identity prompt names exactly the MCP servers the spawn delivers,
kiosk ceiling included.

    python backend/tests/test_kiosk_ceiling_identity.py   (no pytest; plain asserts)

The defect: `identity_prompt` computed its MCP list as `tools["mcp"]`, expanding
`"*"` against the whole registry and applying the KIOSK CEILING NOWHERE, while
`_build_cmd` clamped with `expand_mcp(grant, ceiling, registry)`. So a kiosk
agent could be told it had a server its ceiling cuts, and then not be given it.
Same promise/delivery drift as D-180, one lane over — and it survived because
three copies of "which servers may this node see" existed and only two agreed.

Why a ceiling change is the trigger: `"*"` MATERIALIZES to the ceiling's list at
grant time (test_ledger §4a), so a hire under a narrow ceiling is already clamped
in storage. The drift appears when the CEILING MOVES AFTER THE HIRE — the stored
grant keeps the wider set and only the spawn re-clamps. That is the "outpaced
sweep" case the ledger suite already models, so it is a real state, not a
contrived one.

The load-bearing check is §1's: the promise and the delivery are compared
DIRECTLY, against each other, on the same node. Asserting each against a
hardcoded list would let both drift together the next time someone edits one.

Anti-vacuity throughout: every "is not promised" is paired with an "is promised"
on the same run, so a function that simply returned nothing could not pass.
"""

import json
import os
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-d182-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"        # never the live 7360
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import store, supervisor                              # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []

REG = {"alpha": {"command": "npx", "args": ["-y", "alpha"]},
       "beta": {"command": "npx", "args": ["-y", "beta"]},
       "gamma": {"command": "npx", "args": ["-y", "gamma"]}}

ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True}


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


def mk_kiosk(label, ceiling_mcp, grant_mcp, tier="haiku"):
    """A kiosk org whose ceiling was NARROWED AFTER the hire — the state a
    ceiling change outpacing a sweep leaves behind."""
    org = store.create_org(f"zz d182 {label}")
    org.d["kiosk"] = {"enabled": True, "token": "t", "credits": 40,
                      "spend_limit": 0.0, "storage_limit_mb": 0,
                      "sandbox": False, "auto_raise": False, "max_scope": None}
    # hire under a WIDE ceiling so the grant is stored unclamped…
    org.d["kiosk"]["max_scope"] = org._norm_ceiling(
        {"tools": {**ALL_TOOLS, "mcp": ["*"]}})
    r = org.hire(USER, None, tier, 0, "k1", add_dirs=[],
                 tools={**ALL_TOOLS, "mcp": list(grant_mcp)},
                 org_visibility="team", charter="a d182 ceiling test agent")
    nid = r["node"]
    org.node(nid)["scope"]["tools"]["mcp"] = list(grant_mcp)   # the stored grant
    # …then NARROW the ceiling underneath it
    org.d["kiosk"]["max_scope"] = org._norm_ceiling(
        {"tools": {**ALL_TOOLS, "mcp": list(ceiling_mcp)}})
    store.save_org(org)
    return org.d["slug"], nid


def with_registry(fn):
    real = supervisor.registered_mcp_servers
    supervisor.registered_mcp_servers = lambda: REG
    try:
        return fn()
    finally:
        supervisor.registered_mcp_servers = real


def promised(slug, nid):
    """The servers the identity prompt NAMES."""
    p = with_registry(lambda: supervisor.identity_prompt(
        store.load_org(slug), nid))
    key = "MCP servers available to you: "
    if key not in p:
        return []
    line = p[p.index(key) + len(key):]
    line = line[:line.index(" (their tools")]
    return sorted(x.strip() for x in line.split(",") if x.strip())


def delivered(slug, nid):
    """The servers the SPAWN actually attaches (orgtree's own is not one)."""
    cmd = with_registry(lambda: supervisor._build_cmd(
        store.load_org(slug), nid))
    cfg = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]
    return sorted(k for k in cfg if k != "orgtree")


def main() -> int:
    print("§1 ☠ the promise equals the delivery under a kiosk ceiling")

    def t1():
        slug, nid = mk_kiosk("star", ceiling_mcp=["alpha"], grant_mcp=["*"])
        p, d = promised(slug, nid), delivered(slug, nid)
        # compared to EACH OTHER — the invariant, not a hardcoded pair
        assert p == d, f"promise {p} != delivery {d}"
        # and anti-vacuity: the run must be one where something was actually cut
        assert p == ["alpha"], p
    check("☠ a '*' grant under a narrowed ceiling promises only the ceiling", t1)

    def t1b():
        slug, nid = mk_kiosk("list", ceiling_mcp=["alpha"],
                             grant_mcp=["alpha", "gamma"])
        p, d = promised(slug, nid), delivered(slug, nid)
        assert p == d, f"promise {p} != delivery {d}"
        assert "alpha" in p and "gamma" not in p, p
    check("☠ an explicit over-wide grant is cut to the ceiling too", t1b)

    def t1c():
        # the ceiling permits it AND the grant asks for it → it must be there.
        # Without this, §1 would pass against a function that always cut.
        slug, nid = mk_kiosk("wide", ceiling_mcp=["alpha", "beta"],
                             grant_mcp=["*"])
        p, d = promised(slug, nid), delivered(slug, nid)
        assert p == d, f"promise {p} != delivery {d}"
        assert p == ["alpha", "beta"], p
    check("a grant the ceiling permits is still promised in full", t1c)

    print("\n§2 no kiosk: the registry itself is the only bound")

    def t2():
        org = store.create_org("zz d182 plain")
        r = org.hire(USER, None, "haiku", 0, "p1", add_dirs=[],
                     tools={**ALL_TOOLS, "mcp": ["*"]},
                     org_visibility="team", charter="plain")
        store.save_org(org)
        slug, nid = org.d["slug"], r["node"]
        p, d = promised(slug, nid), delivered(slug, nid)
        assert p == d, f"promise {p} != delivery {d}"
        assert p == ["alpha", "beta", "gamma"], p
    check("'*' with no ceiling is every registered server", t2)

    def t2b():
        # the OTHER half of the old defect: a literal grant was printed
        # verbatim, never intersected with the registry, so an unregistered
        # name was promised to the agent as if it existed
        org = store.create_org("zz d182 ghost")
        r = org.hire(USER, None, "haiku", 0, "g1", add_dirs=[],
                     tools={**ALL_TOOLS, "mcp": ["alpha", "nosuchserver"]},
                     org_visibility="team", charter="ghost")
        store.save_org(org)
        slug, nid = org.d["slug"], r["node"]
        p, d = promised(slug, nid), delivered(slug, nid)
        assert p == d, f"promise {p} != delivery {d}"
        assert p == ["alpha"], p
        assert "nosuchserver" not in p, "a server that does not exist was promised"
    check("☠ an unregistered name is no longer promised", t2b)

    print("\n§3 the codex lane (D-180) still agrees")

    def t3():
        slug, nid = mk_kiosk("codex", ceiling_mcp=["alpha"], grant_mcp=["*"],
                             tier="sol")
        p = promised(slug, nid)
        ok, _ = with_registry(
            lambda: supervisor.codex_mcp_grant(store.load_org(slug), nid))
        assert p == sorted(ok), f"promise {p} != codex grant {sorted(ok)}"
        assert p == ["alpha"], p
    check("☠ a codex node's promise is ceiling-clamped as well", t3)

    print(f"\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\n--- {label} ---\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
