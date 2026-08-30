"""alwaysLoad on every MCP server entry (new file per the landing rules).

Run: python tests/test_mcp_alwaysload.py

WHY. The tools array sits AHEAD of the system prompt in the cached prefix.
Without alwaysLoad it is assembled from whichever servers have completed their
handshake when the turn's first request goes out, so it differs run to run for
the same agent and invalidates everything behind it. Measured association
(n=1,063): MCP-pending openings 78.7% cold (370/470) vs 43.8% (260/593).

CHECKS
  1. every server in --mcp-config carries alwaysLoad: true (host shape).
  2. CONTROL: the config is non-empty and names `orgtree` — check 1 is
     vacuously true over an empty mapping, and an empty mcpServers is a
     plausible breakage of the very line under test.
  3. the flag survives a second, independent build — i.e. it is not a
     first-call artifact of some cached structure.
  4. CONTROL: the emitted JSON still parses and still round-trips the server's
     real fields (command/args), so check 1 is not passing over a config we
     have replaced with stubs.

⚠ A CHECK THAT WAS HERE AND WAS REMOVED, because it guarded a hazard that does
not exist. It asserted that building the config does not mutate the shared MCP
registry in place — the classic aliasing bug for `{**v}`-style code. A mutant
that DID write in place passed every check, which sent me to look, and the
claim is false: `registered_mcp_servers` re-parses ~/.claude.json on every call
and `granted_mcp_servers` calls it again, so every read returns fresh objects
and there is nothing shared to corrupt. The production code still copies —
it is free and does not depend on that staying true — but a test asserting a
property that cannot currently fail is a test that reports "nothing found"
without being able to find anything, which is the thing this whole effort is
supposed to be against. Removed rather than left looking like coverage.

MUTANTS RUN (value replacements, reverted after):
  M1 drop the alwaysLoad line entirely            → checks 1 and 3 FAIL.
  M2 replace `chosen` with `{}` before serializing → checks 2 and 4 FAIL,
     proving check 1 cannot pass on an empty mapping.
  M3 mutate in place instead of copying           → NOTHING FAILS, correctly;
     see the note above. Recorded because a mutant that survives is a finding.
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


print("\nalwaysLoad on every MCP server entry")

servers = mcp_config()

check("2. CONTROL: the mcp config is non-empty and names `orgtree` "
      "(check 1 is vacuously true over an empty mapping)",
      lambda: die(f"mcpServers empty or missing orgtree: {list(servers)}")
      if not (servers and "orgtree" in servers) else None)

check("1. every server entry carries alwaysLoad: true",
      lambda: die("missing/false alwaysLoad on: "
                  + str([k for k, v in servers.items()
                         if v.get("alwaysLoad") is not True]))
      if any(v.get("alwaysLoad") is not True for v in servers.values())
      else None)

check("5. CONTROL: the entries still carry their real fields, so check 1 is "
      "not passing over stubs",
      lambda: die(f"orgtree entry lost its command/args: {servers['orgtree']}")
      if not (servers["orgtree"].get("command")
              and servers["orgtree"].get("args")) else None)


check("3. a second independent build still carries the flag",
      lambda: die("flag absent on the second build")
      if any(v.get("alwaysLoad") is not True
             for v in mcp_config().values()) else None)

print(f"\n  servers: {sorted(servers)}")
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
