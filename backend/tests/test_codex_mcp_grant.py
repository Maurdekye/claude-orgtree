"""D-180: a codex node's GRANTED external MCP servers reach its app-server —
and the identity prompt promises exactly that set, never more.

    python backend/tests/test_codex_mcp_grant.py    (no pytest; plain asserts)

The defect this suite pins: `_codex_leg` built its tool set from
`mcptool.TOOLS` alone and never looked at the node's `mcp` scope, while
`identity_prompt` announced "MCP servers available to you: …" regardless of
lane. The identity ASSERTED a capability the lane dropped, and an assertion
reads to an agent exactly like the capability.

Two things are therefore proven together, because they are one bug:
  · DELIVERY  — the granted servers ride `-c mcp_servers.…` on the app-server
    launch (measured against the real codex 0.150.1: the server process is
    spawned, handshaked and its tools listed).
  · PROMISE   — `identity_prompt` is built from the SAME `codex_mcp_grant`
    call the spawn uses, so the text and the capability cannot drift.

Anti-vacuity: §2 and §5 plant a server the lane CANNOT express and require the
suite to see it disappear from BOTH the config and the prompt; §5 additionally
plants a deliverable server and requires it to APPEAR. A test that asserted
only absence would pass just as well against a function that returns nothing,
so every "not there" check is paired with a "there" check on the same run.
"""

import io
import os
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-codexmcp-")
os.environ["ORGTREE_DATA"] = DATA
# a PORT NOBODY SERVES: nothing here runs a turn, but an accidental /api/agent
# POST on the default 7360 would land on the operator's LIVE deployment
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import codexrun, store, supervisor                    # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []

#: the planted registry. `ok-name` carries a hyphen (legal TOML bare key, and
#: the shape of a REAL registered server — `ilspy-mcp`); `dot.name` cannot ride
#: `-c`'s dotted path at all; `broken` has neither command nor url.
REG = {
    "blender": {"type": "stdio", "command": "uv",
                "args": ["run", "srv.py"], "env": {"BLENDER_PORT": "9876"}},
    "ok-name": {"type": "stdio", "command": "C:\\tools\\thing.exe", "args": []},
    "weather": {"type": "http", "url": "http://127.0.0.1:9099/mcp"},
    "dot.name": {"type": "stdio", "command": "npx", "args": []},
    "broken": {"type": "stdio"},
}


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


def mkorg(label: str, tier: str, mcp: list[str]) -> tuple[str, str]:
    org = store.create_org(f"zz codexmcp {label}")
    r = org.hire(USER, None, tier, 2, "cx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": mcp},
                 org_visibility="team", charter="a codex mcp grant test agent")
    store.save_org(org)
    return org.d["slug"], r["node"]


def grant_of(slug: str, nid: str):
    real = supervisor.registered_mcp_servers
    supervisor.registered_mcp_servers = lambda: REG
    try:
        return supervisor.codex_mcp_grant(store.load_org(slug), nid)
    finally:
        supervisor.registered_mcp_servers = real


def identity_of(slug: str, nid: str) -> str:
    real = supervisor.registered_mcp_servers
    supervisor.registered_mcp_servers = lambda: REG
    try:
        return supervisor.identity_prompt(store.load_org(slug), nid)
    finally:
        supervisor.registered_mcp_servers = real


class _StubProc:
    """Enough of Popen for AppServerClient to start and stop. Empty streams
    end the reader threads immediately; we only care about the argv."""

    def __init__(self):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"")
        self.stderr = io.BytesIO(b"")
        # close() reaps the TREE by pid (`taskkill /T /F /PID`), so the stub
        # needs one. 0 is deliberate: it is the System Idle Process, which
        # Windows refuses to terminate, so the reap cannot land on anything
        # real no matter what pids this machine is recycling. close() passes
        # check=False and swallows the error, which is the whole contract.
        self.pid = 0

    def terminate(self): pass
    def kill(self): pass
    def wait(self, timeout=None): return 0
    def poll(self): return None


def main() -> int:
    print("§1 the pure translation into codex config")

    def t1():
        out = codexrun.mcp_config_overrides({"blender": REG["blender"]})
        eq(out[0], "-c", "flag")
        assert 'mcp_servers.blender.command="uv"' in out, out
        assert 'mcp_servers.blender.args=["run", "srv.py"]' in out, out
        assert 'mcp_servers.blender.env={BLENDER_PORT = "9876"}' in out, out
    check("stdio command/args/env become -c dotted-path TOML", t1)

    def t1b():
        out = codexrun.mcp_config_overrides({"ok-name": REG["ok-name"]})
        # a naive quoter turns C:\tools into an invalid TOML escape (\t) and
        # the value is corrupted SILENTLY — the exact failure json.dumps avoids
        assert r'mcp_servers.ok-name.command="C:\\tools\\thing.exe"' in out, out
    check("a Windows command path is escaped, not corrupted", t1b)

    def t1c():
        out = codexrun.mcp_config_overrides({"weather": REG["weather"]})
        assert 'mcp_servers.weather.url="http://127.0.0.1:9099/mcp"' in out, out
        assert not any("type" in o for o in out), \
            "claude's `type` discriminator is not a codex config key"
    check("a url server maps to url; `type` is never passed through", t1c)

    print("\n§2 what this lane cannot express is DROPPED, not mangled")

    def t2():
        ok, dropped = codexrun.deliverable_mcp(REG)
        eq(sorted(ok), ["blender", "ok-name", "weather"], "deliverable")
        eq(sorted(dropped), ["broken", "dot.name"], "undeliverable")
    check("dotted name + definition-less server are reported undeliverable", t2)

    def t2b():
        # ☠ the load-bearing one: a dotted name does not merely fail to
        # attach, it aborts the whole app-server with "failed to load
        # bootstrap configuration" (measured, codex 0.150.1) — so it must
        # never reach argv
        out = codexrun.mcp_config_overrides(
            codexrun.deliverable_mcp(REG)[0])
        assert not any("dot.name" in o for o in out), out
        # …and the same run must still carry the good ones, or this proves
        # nothing but that the function returns little
        assert any("mcp_servers.blender.command" in o for o in out), out
        assert any("mcp_servers.ok-name.command" in o for o in out), out
    check("☠ a name that would abort the app-server never reaches argv", t2b)

    print("\n§3 the overrides actually ride the launch")

    def t3():
        seen: dict = {}
        real = codexrun.subprocess.Popen

        def fake_popen(argv, **kw):
            # close() now reaps the process TREE with
            # subprocess.run(["taskkill", …]), which routes through this same
            # patched Popen. Only the app-server launch is under test, so let
            # the reap through to the real Popen: intercepting it both crashes
            # (subprocess.run needs a context manager) and would overwrite the
            # argv this test is here to inspect.
            if argv and "taskkill" in str(argv[0]).lower():
                return real(argv, **kw)
            seen["argv"] = list(argv)
            return _StubProc()

        codexrun.subprocess.Popen = fake_popen
        try:
            c = codexrun.AppServerClient(
                ["codex.exe"],
                config_overrides=codexrun.mcp_config_overrides(
                    {"blender": REG["blender"]}))
            c.close()
        finally:
            codexrun.subprocess.Popen = real
        argv = seen["argv"]
        eq(argv[0], "codex.exe", "argv head preserved")
        eq(argv[-1], "app-server", "subcommand stays last")
        # `-c` is a GLOBAL option: after the subcommand codex would not see it
        assert argv.index("-c") < argv.index("app-server"), argv
        assert any("mcp_servers.blender.command" in a for a in argv), argv
    check("☠ -c overrides sit between the exe and `app-server`", t3)

    def t3b():
        seen: dict = {}
        real = codexrun.subprocess.Popen

        def fake_popen(argv, **kw):
            # close() now reaps the process TREE with
            # subprocess.run(["taskkill", …]), which routes through this same
            # patched Popen. Only the app-server launch is under test, so let
            # the reap through to the real Popen: intercepting it both crashes
            # (subprocess.run needs a context manager) and would overwrite the
            # argv this test is here to inspect.
            if argv and "taskkill" in str(argv[0]).lower():
                return real(argv, **kw)
            seen["argv"] = list(argv)
            return _StubProc()

        codexrun.subprocess.Popen = fake_popen
        try:
            codexrun.AppServerClient(["codex.exe"]).close()
        finally:
            codexrun.subprocess.Popen = real
        eq(seen["argv"], ["codex.exe", "app-server"], "ungranted argv")
    check("a node granted nothing launches an unchanged command line", t3b)

    print("\n§4 scope: the claude lane's semantics, not a weaker check")

    def t4():
        slug, nid = mkorg("star", "sol", ["*"])
        ok, dropped = grant_of(slug, nid)
        eq(sorted(ok), ["blender", "ok-name", "weather"],
           '"*" = every registered server the lane can express')
        eq(sorted(dropped), ["broken", "dot.name"], "and the rest named")
    check('"*" expands to the whole registry, present and future', t4)

    def t4b():
        slug, nid = mkorg("one", "sol", ["blender"])
        ok, _ = grant_of(slug, nid)
        eq(sorted(ok), ["blender"], "narrow grant stays narrow")
    check("☠ a narrow grant never widens to the registry", t4b)

    def t4c():
        slug, nid = mkorg("none", "sol", [])
        eq(grant_of(slug, nid)[0], {}, "no grant, no servers")
        slug2, nid2 = mkorg("ghost", "sol", ["nosuchserver"])
        eq(grant_of(slug2, nid2)[0], {}, "an unregistered name grants nothing")
    check("no grant and a ghost grant both yield nothing", t4c)

    print("\n§5 the promise equals the delivery (deliverable A)")

    def t5():
        slug, nid = mkorg("promise", "sol", ["*"])
        p = identity_of(slug, nid)
        line = p[p.index("MCP servers available to you:"):][:200]
        # present: what the lane really attaches
        assert "blender" in line, line
        assert "weather" in line, line
        # ☠ absent: what it cannot attach — the whole defect in one assertion
        assert "dot.name" not in line, line
        assert "broken" not in line, line
    check("☠ the identity lists exactly the servers the lane attaches", t5)

    def t5b():
        slug, nid = mkorg("named", "sol", ["*"])
        p = identity_of(slug, nid)
        assert "cannot be attached on the Codex provider" in p, p[-1500:]
        assert "dot.name" in p, "an undeliverable grant is named, not hidden"
    check("a granted-but-undeliverable server is named as unavailable", t5b)

    def t5c():
        # anti-vacuity for §5: the prompt is DERIVED, not a fixed string. A
        # node granted one server must not mention the other.
        slug, nid = mkorg("only", "sol", ["blender"])
        p = identity_of(slug, nid)
        line = p[p.index("MCP servers available to you:"):][:200]
        assert "blender" in line, line
        assert "weather" not in line, line
        assert "cannot be attached" not in p, \
            "nothing was dropped from THIS grant, so nothing should be named"
    check("the promise tracks the individual node's grant", t5c)

    def t5d():
        # the claude lane must be untouched: same grant, non-codex tier, still
        # promised through the original path
        slug, nid = mkorg("claude", "sonnet", ["*"])
        p = identity_of(slug, nid)
        line = p[p.index("MCP servers available to you:"):][:200]
        assert "blender" in line and "dot.name" in line, \
            "the claude lane's promise is unchanged by D-180"
    check("a claude node's identity is unaffected", t5d)

    print(f"\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\n--- {label} ---\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
