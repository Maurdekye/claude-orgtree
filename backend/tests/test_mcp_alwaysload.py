"""OrgTree must not force alwaysLoad on every MCP server entry.

Run: python tests/test_mcp_alwaysload.py

WHY. CLI 2.1.220 waits for every alwaysLoad server before building turn 1.
Production measured 5-7 second waits on cold and young-prewarm turns, which
violates the user's no-first-request-handshake-wait rule. Registry entries may
still opt in explicitly; the product must not add the field fleet-wide.

CHECKS
  1. CONTROL: emitted config is non-empty and names orgtree.
  2. no generated entry is forced to alwaysLoad.
  3. a second independent build stays unforced.
  4. the emitted JSON retains the server's real command/args fields.
  5. an explicit per-server opt-in remains true.

MUTANT: restoring the fleet-wide alwaysLoad rewrite turns checks 2 and 3 red.
"""
import io
import json
import os
import sys
import tempfile

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys._utf8_wrapped = True

RIG = tempfile.mkdtemp(prefix="alwaysload-")
HOME = os.path.join(RIG, "home")
os.makedirs(HOME, exist_ok=True)
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
os.environ["ORGTREE_WARM"] = "0"
sys.path.insert(0, BACKEND)

with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import store, supervisor as S                  # noqa: E402
from orgtree.ledger import USER                             # noqa: E402

PASS = FAIL = 0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:3d}  {label}")
    except Exception as e:                                   # noqa: BLE001
        FAIL += 1
        print(f"  FAIL     {label}: {type(e).__name__}: {e}")


def die(m):
    raise AssertionError(m)


org = store.create_org("alwaysload rig")
SLUG = org.d["slug"]
org.hire(USER, None, "haiku", 5, "boss", add_dirs=[], tools={"mcp": ["*"]},
         org_visibility="full", charter="c")
store.save_org(org)


def mcp_config(nid="boss"):
    cmd = S._build_cmd(store.load_org(SLUG), nid, write_ident=False)
    i = cmd.index("--mcp-config")
    return json.loads(cmd[i + 1])["mcpServers"]


print("\nno fleet-wide forced alwaysLoad")

servers = mcp_config()

check("1. CONTROL: the mcp config is non-empty and names `orgtree` "
      "(check 1 is vacuously true over an empty mapping)",
      lambda: die(f"mcpServers empty or missing orgtree: {list(servers)}")
      if not (servers and "orgtree" in servers) else None)

check("2. no generated server entry is forced to alwaysLoad",
      lambda: die("forced alwaysLoad on: "
                  + str([k for k, v in servers.items()
                         if v.get("alwaysLoad") is True]))
      if any(v.get("alwaysLoad") is True for v in servers.values()) else None)

check("3. CONTROL: the entries still carry their real fields, so check 2 is "
      "not passing over stubs",
      lambda: die(f"orgtree entry lost its command/args: {servers['orgtree']}")
      if not (servers["orgtree"].get("command")
              and servers["orgtree"].get("args")) else None)


check("4. a second independent build remains unforced",
      lambda: die("forced alwaysLoad appeared on the second build")
      if any(v.get("alwaysLoad") is True
             for v in mcp_config().values()) else None)

original_grants = S.granted_mcp_servers
S.granted_mcp_servers = lambda _org, _nid: {
    "explicit-opt-in": {
        "command": sys.executable,
        "args": ["-c", "pass"],
        "alwaysLoad": True,
    }
}
try:
    opted = mcp_config()
finally:
    S.granted_mcp_servers = original_grants

check("5. an explicit per-server alwaysLoad opt-in is preserved",
      lambda: die(f"explicit opt-in changed: {opted}")
      if opted.get("explicit-opt-in", {}).get("alwaysLoad") is not True
      else None)

print(f"\n  servers: {sorted(servers)}")
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
