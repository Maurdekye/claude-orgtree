"""C2+C3: one captured Codex startup manifest, two honest projections.

Hermetic: throwaway ORGTREE_DATA/HOME/CODEX_HOME, port 9, fake CLI/account,
no provider process and no network. Run directly:

    python backend/tests/test_codex_startup_manifest.py
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import traceback
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
RIG = Path(tempfile.mkdtemp(prefix="orgtree-codex-manifest-")).resolve()
DATA, HOME, CODEX_HOME = RIG / "data", RIG / "home", RIG / "codex-home"
ALT_CODEX_HOME, ENV_CODEX_HOME = RIG / "codex-home-b", RIG / "codex-home-c"
ALT_CWD = RIG / "cwd-b"
for directory in (DATA, HOME, CODEX_HOME, ALT_CODEX_HOME, ENV_CODEX_HOME,
                  ALT_CWD):
    directory.mkdir(parents=True)
(DATA / "defaults.json").write_text(
    '{"net_hub_address":"http://127.0.0.1:9"}', encoding="utf-8")
os.environ.update({
    "ORGTREE_DATA": str(DATA), "ORGTREE_PORT": "9", "ORGTREE_WARM": "0",
    "HOME": str(HOME), "USERPROFILE": str(HOME),
    "CODEX_HOME": str(CODEX_HOME),
})
assert Path(os.environ["ORGTREE_DATA"]).resolve() == DATA
assert Path(os.environ["ORGTREE_DATA"]).resolve() != Path(
    r"C:\Users\ncola_k8bx\orgtree").resolve()
assert os.environ["ORGTREE_PORT"] == "9"
sys.path.insert(0, str(HERE.parent))

from orgtree import cachecontinuity as C, codexrun, store  # noqa: E402
from orgtree import supervisor as S, warmpool as W  # noqa: E402
from orgtree.ledger import USER  # noqa: E402

assert Path(store.DATA_ROOT).resolve() == DATA
S.chatq_register_org = lambda _slug: None
S.chatq_deregister_org = lambda _slug: None

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:  # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
    else:
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")


org = store.create_org("zz codex startup manifest")
nid = org.hire(
    USER, None, "sol", 5, "cx", add_dirs=[],
    tools={"bash": True, "web": False, "edit": True,
           "subagents": False, "mcp": []},
    org_visibility="team", charter="manifest test marker") ["node"]
store.save_org(org)
CWD = Path(S.scratch_dir(org.d["slug"], nid))
MANAGED = CWD / "AGENTS.md"
OVERRIDE = CWD / "AGENTS.override.md"
GLOBAL = CODEX_HOME / "AGENTS.md"


def status(exe: str) -> dict[str, object]:
    return {"installed": True, "connected": True, "path": exe,
            "kind": "chatgpt"}


def resolve(*, exe: str = "fake-codex-a.exe", account: str = "acct-a",
            lane: str = "subscription", write: bool = True,
            registry: dict[str, object] | None = None,
            provider_spec: dict[str, object] | None = None
            ) -> dict[str, object]:
    current = store.load_org(org.d["slug"])
    with patch.object(S.providers, "codex_status", return_value=status(exe)), \
         patch.object(S, "_cache_codex_account_namespace",
                      return_value=(account, lane)), \
         patch.object(S, "registered_mcp_servers",
                      return_value=registry or {}):
        return S._codex_startup_manifest(
            current, nid, write_ident=write,
            provider_spec=provider_spec)


def process(manifest: dict[str, object]) -> tuple[str, dict[str, str]]:
    current = store.load_org(org.d["slug"])
    return W.identity_snapshot(current, nid, codex_manifest=manifest)


def cache(manifest: dict[str, object]) -> dict[str, object]:
    current = store.load_org(org.d["slug"])
    route = {"model": "gpt-5.6-sol", "pool": "direct",
             "requested_tier": "sol"}
    # Any auth read here is a defect: the manifest already captured it.
    with patch.object(S, "_cache_codex_account_namespace",
                      side_effect=AssertionError("auth re-read")), \
         patch.object(S.providers, "codex_status", return_value=status(
             str(manifest["provider_spec"]["exe"]))), \
         patch.object(S.codex_route, "resolve", return_value=route):
        return S._cache_snapshot(
            current, nid, include_history=False,
            codex_manifest=manifest)


def t_write_order_tamper_and_override() -> None:
    MANAGED.unlink(missing_ok=True)
    OVERRIDE.unlink(missing_ok=True)
    observed = resolve(write=False)
    assert not MANAGED.exists(), "write_ident=False wrote the managed file"

    first = resolve(write=True)
    assert MANAGED.read_text(encoding="utf-8") == first["identity"]
    assert process(first) == process(resolve(write=True)), (
        "normal managed-file generation caused process churn")

    MANAGED.write_text("tampered managed carrier\n", encoding="utf-8")
    tampered = resolve(write=False)
    assert process(tampered) != process(first), "tampering was invisible"
    restored = resolve(write=True)
    assert MANAGED.read_text(encoding="utf-8") == first["identity"]
    assert process(restored) == process(first), (
        "manifest hashed pre-generation bytes instead of launch bytes")

    OVERRIDE.write_text("override wins over managed identity\n", encoding="utf-8")
    overridden = resolve(write=True)
    assert process(overridden) != process(first)
    assert cache(overridden)["components"]["startup"] != \
        cache(first)["components"]["startup"]
    assert MANAGED.read_text(encoding="utf-8") == first["identity"], (
        "normal generation stopped maintaining its own carrier")
    OVERRIDE.unlink()
    assert process(resolve(write=True)) == process(first)
    assert observed["identity"] == first["identity"]


check("read-only resolution, managed write ordering, tamper and override",
      t_write_order_tamper_and_override)


def t_native_files_follow_captured_roots() -> None:
    """Supplied launch cwd/home, not ambient values, drive discovery."""
    GLOBAL.write_text("ambient home alpha\n", encoding="utf-8")
    alt_global = ALT_CODEX_HOME / "AGENTS.md"
    alt_project = ALT_CWD / "AGENTS.md"
    alt_global.write_text("captured home alpha\n", encoding="utf-8")
    alt_project.write_text("captured cwd alpha\n", encoding="utf-8")
    spec = copy.deepcopy(resolve()["provider_spec"])
    spec["codex_home"] = str(ALT_CODEX_HOME)
    spec["cache_codex_home"] = str(ALT_CODEX_HOME)
    spec["cwd"] = str(ALT_CWD)

    base = resolve(provider_spec=spec)
    old_ambient = W.codex_startup_context_digest(org, nid)
    alt_global.write_text("captured home bravo\n", encoding="utf-8")
    home_changed = resolve(provider_spec=spec)
    assert home_changed["startup_digest"] != base["startup_digest"], (
        "captured CODEX_HOME/AGENTS.md was not discovered")
    assert W.codex_startup_context_digest(org, nid) == old_ambient, (
        "old ambient-only mutant unexpectedly saw captured home")

    GLOBAL.write_text("ambient home bravo\n", encoding="utf-8")
    ambient_changed = resolve(provider_spec=spec)
    assert ambient_changed["startup_digest"] == \
        home_changed["startup_digest"], (
            "unserved ambient CODEX_HOME changed captured discovery")
    assert W.codex_startup_context_digest(org, nid) != old_ambient, (
        "old ambient-only mutant did not reproduce the wrong-file change")

    alt_project.write_text("captured cwd bravo\n", encoding="utf-8")
    cwd_changed = resolve(provider_spec=spec)
    assert cwd_changed["startup_digest"] != \
        ambient_changed["startup_digest"], (
            "captured process cwd did not drive project-doc discovery")

    env_global = ENV_CODEX_HOME / "AGENTS.md"
    env_global.write_text("effective env home alpha\n", encoding="utf-8")
    env_spec = copy.deepcopy(spec)
    env_spec["env_extra"]["CODEX_HOME"] = str(ENV_CODEX_HOME)
    env_base = resolve(provider_spec=env_spec)
    alt_global.write_text("captured argument home charlie\n", encoding="utf-8")
    assert resolve(provider_spec=env_spec)["startup_digest"] == \
        env_base["startup_digest"], "codex_home arg beat later env_extra"
    env_global.write_text("effective env home bravo\n", encoding="utf-8")
    assert resolve(provider_spec=env_spec)["startup_digest"] != \
        env_base["startup_digest"], "effective CODEX_HOME env was ignored"


check("native files follow captured CODEX_HOME/cwd with ambient negative",
      t_native_files_follow_captured_roots)


def t_account_follows_effective_captured_home() -> None:
    def write_auth(home: Path, account: str | None) -> None:
        tokens = ({"account_id": account} if account is not None else {})
        (home / "auth.json").write_text(
            json.dumps({"tokens": tokens}), encoding="utf-8")

    def real(spec: dict[str, object]) -> dict[str, object]:
        return S._codex_startup_manifest(
            store.load_org(org.d["slug"]), nid, provider_spec=spec)

    write_auth(CODEX_HOME, "ambient-a")
    write_auth(ALT_CODEX_HOME, "captured-b")
    write_auth(ENV_CODEX_HOME, "effective-c")
    spec = copy.deepcopy(resolve()["provider_spec"])
    spec["codex_home"] = str(ALT_CODEX_HOME)
    spec["cache_codex_home"] = str(ALT_CODEX_HOME)

    captured_b = real(spec)
    ambient_account, _ = S._cache_codex_account_namespace(str(CODEX_HOME))
    b_account, _ = S._cache_codex_account_namespace(str(ALT_CODEX_HOME))
    assert captured_b["account"] == b_account != ambient_account
    assert S._codex_manifest_account_current(captured_b)[0]

    write_auth(CODEX_HOME, "ambient-a-moved")
    assert real(spec)["account"] == captured_b["account"], (
        "ambient auth changed a captured B-home account")
    assert S._codex_manifest_account_current(captured_b)[0]

    write_auth(ALT_CODEX_HOME, "captured-b-moved")
    assert not S._codex_manifest_account_current(captured_b)[0]
    captured_b2 = real(spec)
    assert captured_b2["account"] != captured_b["account"]
    assert S._codex_manifest_account_current(captured_b2)[0]

    env_spec = copy.deepcopy(spec)
    env_spec["env_extra"]["CODEX_HOME"] = str(ENV_CODEX_HOME)
    captured_c = real(env_spec)
    c_account, _ = S._cache_codex_account_namespace(str(ENV_CODEX_HOME))
    assert captured_c["account"] == c_account
    write_auth(ALT_CODEX_HOME, "captured-b-moved-again")
    assert real(env_spec)["account"] == captured_c["account"], (
        "codex_home argument beat later env home for account capture")
    write_auth(ENV_CODEX_HOME, "effective-c-moved")
    assert real(env_spec)["account"] != captured_c["account"]

    # A different explicit home with an old auth record must stay unknown;
    # providers._codex_account has no home parameter and would read ambient A.
    write_auth(ALT_CODEX_HOME, None)
    unknown = real(spec)
    assert unknown["account"] == "codex-account-unobserved"
    assert unknown["account"] != \
        S._cache_codex_account_namespace(str(CODEX_HOME))[0]


check("account capture/check follow B home, ambient negative, env precedence",
      t_account_follows_effective_captured_home)


def t_axes_and_mutants() -> None:
    GLOBAL.write_text("global alpha\n", encoding="utf-8")
    base = resolve()
    base_process, base_cache = process(base), cache(base)
    assert (base_cache["model"], base_cache["pool"]) == (
        "gpt-5.6-sol", "direct")
    assert process(resolve()) == base_process
    assert cache(resolve())["components"] == base_cache["components"]

    GLOBAL.write_text("global bravo\n", encoding="utf-8")
    file_changed = resolve()
    assert process(file_changed) != base_process
    assert cache(file_changed)["components"]["startup"] != \
        base_cache["components"]["startup"]

    # Meaningful old-code mutant: synthetic startup sees neither file.
    synthetic_a = C.digest({"managed_identity": base_cache["components"]["system"],
                            "provider": "openai"})
    synthetic_b = C.digest({"managed_identity": cache(file_changed)
                            ["components"]["system"], "provider": "openai"})
    assert synthetic_a == synthetic_b, "mutant did not reproduce C2"

    cli_changed = resolve(exe="fake-codex-b.exe")
    assert process(cli_changed) != process(file_changed)
    assert cache(cli_changed)["components"]["argv"] != \
        cache(file_changed)["components"]["argv"]

    account_changed = resolve(account="acct-b")
    assert process(account_changed) != process(file_changed)
    assert cache(account_changed)["account"] != cache(file_changed)["account"]
    # Mutant without captured account would collide exactly as current main did.
    mutant = copy.deepcopy(account_changed)
    mutant["account"] = file_changed["account"]
    assert process(mutant) == process(file_changed)

    with store.DOC_LOCK:
        changed = store.load_org(org.d["slug"])
        changed.node(nid)["scope"]["permission_mode"] = "plan"
        store.save_org(changed)
    scope_changed = resolve()
    assert process(scope_changed) != process(file_changed)
    assert cache(scope_changed)["components"] != cache(file_changed)["components"]

    with store.DOC_LOCK:
        changed = store.load_org(org.d["slug"])
        changed.node(nid)["scope"]["tools"]["mcp"] = ["probe"]
        store.save_org(changed)
    registry = {"probe": {"command": "fake-mcp", "args": ["serve"]}}
    tool_changed = resolve(registry=registry)
    assert process(tool_changed) != process(scope_changed)
    assert cache(tool_changed)["components"] != cache(scope_changed)["components"]


check("file, CLI, account, scope and actual tool inputs move both projections",
      t_axes_and_mutants)


def t_process_only_and_dynamic_controls() -> None:
    base = resolve()
    spec = copy.deepcopy(base["provider_spec"])
    spec["env_extra"]["ORGTREE_PORT"] = "12345"
    spec["pid"] = 99999
    process_only = resolve(provider_spec=spec)
    assert process(process_only) != process(base), "port did not move process"
    assert cache(process_only)["components"] == cache(base)["components"], (
        "process-only port/PID leaked into cache prefix")

    before_process, before_cache = process(base), cache(base)["components"]
    with store.DOC_LOCK:
        changed = store.load_org(org.d["slug"])
        node = changed.node(nid)
        node["effort"] = "max"
        node["inflight"] = {"at": "dynamic", "text": "envelope marker"}
        node["last_status"] = {"status": "working", "at": "dynamic"}
        changed.d.setdefault("notices", {})[nid] = [{
            "at": "dynamic", "text": "notice marker"}]
        store.save_org(changed)
    after = resolve()
    assert process(after) == before_process
    assert cache(after)["components"] == before_cache
    raw = json.dumps(cache(after), sort_keys=True)
    assert "manifest test marker" not in raw, "raw identity was persisted"
    assert "envelope marker" not in raw and "notice marker" not in raw


check("process-only, effort-only and dynamic-envelope controls stay distinct",
      t_process_only_and_dynamic_controls)


class CaptureClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def bind(self, **_kwargs: object) -> None:
        return None

    def initialize(self) -> None:
        return None

    def request(self, method: str, params: dict[str, object],
                **_kwargs: object) -> dict[str, object]:
        self.calls.append((method, copy.deepcopy(params)))
        if method in ("thread/start", "thread/resume"):
            return {"thread": {"id": str(params.get("threadId") or "new")},
                    "model": params.get("model")}
        if method == "turn/start":
            return {"turn": {"id": "turn"}}
        raise AssertionError(method)


def t_both_identity_carriers_initial_and_resume() -> None:
    manifest = resolve()
    spec = manifest["provider_spec"]
    wire_tools = manifest["cache_tools"]["dynamic_tools"]
    assert wire_tools, "tool wire-shape check is inert"
    source_tools = S._orgtree_tool_catalogue()
    assert source_tools and "type" not in source_tools[0], (
        "raw-catalogue mutant is inert; fixture already has wire type")
    assert source_tools != wire_tools
    assert all(set(tool) == {"type", "name", "description", "inputSchema"}
               and tool["type"] == "function" for tool in wire_tools), \
        wire_tools[0]
    managed = MANAGED.read_text(encoding="utf-8")
    assert managed == manifest["identity"]
    for thread_id, method in ((None, "thread/start"),
                              ("existing", "thread/resume")):
        client = CaptureClient()
        turn = codexrun.CodexTurn(
            ["fake"], cwd=str(spec["cwd"]), model="gpt-5.6-sol",
            effort=None, thread_id=thread_id,
            dynamic_tools=wire_tools,
            developer_instructions=str(manifest["identity"]), client=client)
        turn.start("hello")
        sent = next(row for called, row in client.calls if called == method)
        assert sent["developerInstructions"] == managed
        assert sent["dynamicTools"] == wire_tools
        # Deleting this request carrier must be observable on EACH path.
        deleted = dict(sent)
        deleted.pop("developerInstructions")
        assert deleted.get("developerInstructions") != managed


check("both identity carriers and exact start/resume tool wire shape survive",
      t_both_identity_carriers_initial_and_resume)


def t_partial_overrides_and_auth_race() -> None:
    current = store.load_org(org.d["slug"])
    spec = resolve()["provider_spec"]
    calls: list[int] = []

    def auth(*_args):
        calls.append(1)
        return "resolved-account", "resolved-lane"

    with patch.object(S, "_cache_codex_account_namespace", side_effect=auth):
        a = S._codex_startup_manifest(
            current, nid, provider_spec=spec, account_override="fixed-account")
        b = S._codex_startup_manifest(
            current, nid, provider_spec=spec, lane_override="fixed-lane")
        c = S._codex_startup_manifest(
            current, nid, provider_spec=spec, account_override="both-account",
            lane_override="both-lane")
    assert (a["account"], a["lane"]) == ("fixed-account", "resolved-lane")
    assert (b["account"], b["lane"]) == ("resolved-account", "fixed-lane")
    assert (c["account"], c["lane"]) == ("both-account", "both-lane")
    assert len(calls) == 2, "full overrides still touched current auth"

    captured = resolve(account="account-before")
    before_hash = process(captured)
    # Auth switches after resolution. Both consumers must keep the captured A;
    # a fresh resolution sees B and becomes incompatible.
    with patch.object(S, "_cache_codex_account_namespace",
                      side_effect=AssertionError("late auth read")):
        assert process(captured) == before_hash
        assert cache(captured)["account"] == "account-before"
    fresh = resolve(account="account-after")
    assert process(fresh) != before_hash
    assert cache(fresh)["account"] == "account-after"


check("partial overrides and account switch after resolution cannot reattribute",
      t_partial_overrides_and_auth_race)


def t_real_turn_invalidates_racing_account_receipt() -> None:
    """A fake child changes/reads auth at startup; A is never authoritative.

    This deliberately lands inside the irreducible check-vs-child-read race:
    pre-launch local evidence is account A, while the process actually reads
    account B. The post-turn local check must preserve the real output/cost
    but invalidate A's cache receipt and mark route attribution ambiguous.
    """
    fake = HERE / "fakecodex.py"
    auth_file = CODEX_HOME / "auth.json"
    auth_probe = RIG / "fake-auth-observed.json"
    wrapper = RIG / "fake-auth-race.py"

    def write_account(name: str) -> None:
        auth_file.write_text(json.dumps({"tokens": {"account_id": name}}),
                             encoding="utf-8")

    wrapper.write_text(
        "import json, os, runpy\n"
        "from pathlib import Path\n"
        "auth = Path(os.environ['CODEX_HOME']) / 'auth.json'\n"
        "doc = json.loads(auth.read_text(encoding='utf-8'))\n"
        "doc.setdefault('tokens', {})['account_id'] = "
        "'account-after-resolution'\n"
        "auth.write_text(json.dumps(doc), encoding='utf-8')\n"
        "seen = json.loads(auth.read_text(encoding='utf-8'))"
        "['tokens']['account_id']\n"
        "Path(os.environ['FAKECODEX_AUTHPROBE']).write_text("
        "json.dumps({'account_id': seen, 'codex_home': "
        "os.environ.get('CODEX_HOME')}), encoding='utf-8')\n"
        "runpy.run_path(os.environ['FAKECODEX_TARGET'], run_name='__main__')\n",
        encoding="utf-8")
    tracked_env = ("ORGTREE_CODEX", "FAKECODEX_SCENARIO",
                   "FAKECODEX_TARGET", "FAKECODEX_AUTHPROBE")
    old_env = {key: os.environ.get(key) for key in tracked_env}
    os.environ["ORGTREE_CODEX"] = str(wrapper)
    os.environ["FAKECODEX_SCENARIO"] = "plain"
    os.environ["FAKECODEX_TARGET"] = str(fake)
    os.environ["FAKECODEX_AUTHPROBE"] = str(auth_probe)
    prior_stream = S.stream
    real_after = S._after_turn
    S.stream = lambda *_args, **_kwargs: None
    try:
        rate = {"limitId": "codex", "limitName": None,
                "primary": {"usedPercent": 17,
                            "windowDurationMins": 300,
                            "resetsAt": 4102444800}}
        # Positive control for the empty board: this exact helper/snapshot
        # would seed it if the ambiguous turn reached observe().
        S.codex_limits.invalidate()
        assert not S.codex_limits.snapshot()["available"]
        assert S.codex_limits.observe(
            rate, pool_hint="plan", account="positive-control")
        assert S.codex_limits.snapshot()["available"]
        S.codex_limits.invalidate()

        for seed_account in (None, "preexisting-account-a",
                             "preexisting-account-b"):
            S.codex_limits.invalidate()
            if seed_account is not None:
                assert S.codex_limits.observe(
                    rate, pool_hint="plan", account=seed_account)
            frozen_now = 4102444700.0
            board_before = S.codex_limits.snapshot(now=frozen_now)
            write_account("account-before-launch")
            auth_probe.unlink(missing_ok=True)
            # Make this a resumable existing thread and remove any preceding
            # cache/route receipt before each empty/A/B board control.
            with store.DOC_LOCK:
                current = store.load_org(org.d["slug"])
                current.node(nid)["codex_thread"] = \
                    current.node(nid)["session_id"]
                current.node(nid)["session_unrun"] = False
                current.node(nid).pop("cache_continuity", None)
                current.node(nid).pop("codex_route_last", None)
                store.save_org(current)
            S.providers._status_cache = None
            account_before, _lane = S._cache_codex_account_namespace()
            attempts: list[dict[str, object] | None] = []
            results: list[dict[str, object]] = []
            observe_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
            real_observe = S.codex_limits.observe

            def capture_after(*args, **kwargs):
                results.append(args[3])
                attempts.append(kwargs.get("cache_attempt"))
                return real_after(*args, **kwargs)

            def capture_observe(*args, **kwargs):
                observe_calls.append((args, kwargs))
                return real_observe(*args, **kwargs)

            # Cache-only route board: the only child is the actual turn.
            with patch.object(S.codex_limits, "fetch", return_value={}), \
                 patch.object(S.codex_limits, "observe",
                              side_effect=capture_observe), \
                 patch.object(S, "_after_turn", side_effect=capture_after):
                S._run_one_turn(org.d["slug"], nid,
                                "captured launch race")
            current = store.load_org(org.d["slug"])
            node = current.node(nid)
            assert not S.state(org.d["slug"], nid).get("last_error"), \
                S.state(org.d["slug"], nid).get("last_error")
            observed = json.loads(auth_probe.read_text(encoding="utf-8"))
            assert observed == {"account_id": "account-after-resolution",
                                "codex_home": str(CODEX_HOME)}
            assert results and results[0].get("result"), results
            assert results[0].get("rate_limits"), (
                "ambiguous fold check is inert: fake emitted no snapshot")
            assert observe_calls == [], observe_calls
            assert S.codex_limits.snapshot(now=frozen_now) == board_before
            book = node.get("cache_continuity") or {}
            assert "last_turn" not in book, {
                "book": book, "attempts": attempts}
            assert attempts == [None], attempts
            assert (node.get("codex_route_last") or {}).get("account") == \
                "codex-account-ambiguous"
            account_after, _lane = S._cache_codex_account_namespace()
            assert account_after != account_before
    finally:
        S.codex_limits.invalidate()
        S.stream = prior_stream
        S.providers._status_cache = None
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


check("fake child account race invalidates A receipt and marks ambiguity",
      t_real_turn_invalidates_racing_account_receipt)


if FAIL:
    for label, tb in FAIL:
        print(f"\n[X] {label}\n{tb}")
    print(f"codex-startup-manifest: {PASS} passed - {len(FAIL)} FAILED")
    rc = 1
else:
    print(f"codex-startup-manifest: all {PASS} checks passed")
    rc = 0
shutil.rmtree(RIG, ignore_errors=True)
sys.exit(rc)
