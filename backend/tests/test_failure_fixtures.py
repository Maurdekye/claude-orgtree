"""Redacted failure fixtures (backend/orgtree/failfix.py, failclass.py,
tools/replay_failure.py, docs/failure-fixtures.md).

    §1  tags — canary sentences vanish from EVERY capture field; recognised
        diagnostic tags survive; the tag vocabulary IS the predicates'
        vocabulary (AST), and a predicate answers the same on a blob and on
        that blob's tags (the replay equivalence, over a corpus)
    §2  every string leaf is validated; phase is evidence, with unknown
    §3  bounds — the ring, the byte cap, an unwritable root, malformed input
    §4  REAL failures through the fake CLI (test_limit_freeze's rig):
        died-in-flight, is_error 401 with canaries and secrets in the result,
        died-with-stderr → fixtures on disk with the right observed facts,
        phase and recorded verdict; replay through failclass recomputes the
        same verdict with no drift; an edited fixture drifts (positive control)
    §5  the replay tool as a subprocess, --assert both ways
    §6  purity — failclass' sources are byte-identical to the supervisor's;
        the tool runs under an import hook that refuses storage / provider /
        process modules and file writes, with a control proving the hook bites

§4 and §5 spawn `node` for the stand-in CLI; declared INERT when it is absent.

    python backend/tests/test_failure_fixtures.py
"""
from __future__ import annotations

import ast
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

# the rig binds a THROWAWAY ORGTREE_DATA + HOME at import, before orgtree
import test_limit_freeze as rig                                  # noqa: E402
from orgtree import failclass, failfix, store, supervisor         # noqa: E402

assert store.DATA_ROOT.startswith(rig._TMP), store.DATA_ROOT    # throwaway root

PASSED = 0
FAILED: list[str] = []
NOTES: list[str] = []


def check(label: str, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception:                                        # noqa: BLE001
        FAILED.append(f"{label}\n{traceback.format_exc()}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


def fixture(cond: bool, why: str) -> None:
    if not cond:
        raise AssertionError("FIXTURE: " + why)


PREDICATES: failfix.Predicates = {
    "limit": failclass._looks_like_usage_limit,
    "net": failclass._looks_like_connection_failure,
    "filtered": failclass._looks_like_filtered,
    "died_in_flight": failclass._died_in_flight,
    "typed": failclass._typed_api_status,
}
PURE_FNS = ("_strict_http_status", "_typed_status_field", "_typed_api_status",
            "_looks_like_usage_limit", "_looks_like_connection_failure",
            "_died_in_flight", "_looks_like_filtered")

# canaries: arbitrary confidential-looking sentences with NO key/path shape
CANARIES = ("the quarterly numbers are down twelve percent",
            "ZEBRA-OTTER-7731 said the merger closes friday",
            "patient initials JD, room 4, dosage doubled")
SECRETS = ("sk-ant-api03-PLANTEDSECRET0123456789abcdef",
           "Bearer PLANTEDBEARER.token", r"C:\Users\planted\notes.txt",
           "planted.user@example.com", "https://planted.example.com/x?k=1")
# nonsecret diagnostic controls, and the tag each must produce
CONTROLS = (("Error: ECONNRESET while reading", "econnreset"),
            ("socket hang up", "socket hang up"),
            ("You've hit your limit · resets 1:40pm", "hit your"),
            ("API status 401 · Invalid API key", "status:401"),
            ("the CLI exited 1 without writing anything to stderr", "exit:1"),
            ("error: unknown option '--effort'", "option:--effort"),
            ("turn/steer: {\"code\": -32000}", "rpc:-32000"),
            ("usageLimitExceeded", "usagelimitexceeded"),
            ("blocked by content filtering policy", "content filter"),
            ("ENOSPC: no space left on device", "enospc"))
CORPUS = [c for c, _ in CONTROLS] + list(CANARIES) + [
    "You've hit your usage limit for this account", "rate limit exceeded",
    "Weekly limit reached — resets Monday", "limit", "usage", "quota exceeded",
    "fetch failed: getaddrinfo ENOTFOUND api.anthropic.com",
    "Error: connection reset by peer / socket hang up / exit 1",
    "the model output was blocked by content policy", "content filter",
    "flagged by safety", "API status 503 · Service Unavailable",
    "the CLI exited 137", "No conversation found with session ID",
    "turn killed: exceeded the 900s per-message ceiling", "",
    "limit exceed account", "exceed rate limit", "session limit",
    "hello world", "Invalid API key · Please run /login",
]


def sec_tags() -> None:
    print("\n§1  tags")

    def _canaries_vanish_everywhere() -> None:
        for canary in CANARIES:
            fx = failfix.build(
                lane="claude", site="terminal",
                observed={"stream_code": canary, "terminal_reason": canary,
                          "started": True},
                text={"err_blob": f"turn failed: {canary} ECONNRESET",
                      "stderr_tail": canary, "result_detail": canary},
                recorded={"net": True}, codex={"status": canary, "pool": canary,
                                                "served": canary,
                                                "error_code": canary,
                                                "kind_recorded": canary},
                ran_as=canary, cli={"version": canary}, at=canary)
            js = json.dumps(fx).lower()
            for word in canary.lower().split():
                if len(word) >= 5:
                    assert word not in js, (canary, word, js)
            assert "econnreset" in fx["tags"]["err_blob"]
            assert fx["lens"]["stderr_tail"] == len(canary)
    check("canary sentences vanish from every capture field (text, stream "
          "code, terminal reason, codex status/pool/code, ran_as, version, "
          "at); the errno beside one survives; lengths are kept",
          _canaries_vanish_everywhere)

    def _secrets_vanish() -> None:
        for s in SECRETS:
            tags = failfix.tags_of(f"turn failed: {s} / then ECONNRESET")
            assert "econnreset" in tags
            assert not any("planted" in t for t in tags), (s, tags)
            assert not any(s.lower() in t for t in tags), (s, tags)
    check("key / bearer / path / email / url never become a tag",
          _secrets_vanish)

    def _controls_tagged() -> None:
        for text, tag in CONTROLS:
            assert tag in failfix.tags_of(text), (text, failfix.tags_of(text))
    check("nonsecret diagnostic controls each produce their tag",
          _controls_tagged)

    def _vocab_is_the_predicates() -> None:
        """every literal a predicate searches for is in the tag vocabulary"""
        src = open(os.path.join(HERE, "..", "orgtree", "failclass.py"),
                   encoding="utf-8").read()
        tree = ast.parse(src)
        found: set[str] = set()
        for n in tree.body:
            if isinstance(n, ast.FunctionDef) and n.name.startswith("_looks_like"):
                for c in ast.walk(n):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                            and c.value and "\n" not in c.value \
                            and not c.value.startswith(" "):
                        found.add(c.value)
        missing = sorted(found - set(failfix.VOCAB))
        assert not missing, f"predicate phrases missing from VOCAB: {missing}"
        assert len(found) >= 30, len(found)      # the AST walk found the lists
    check("the tag vocabulary contains every phrase the predicates search for "
          "(AST over failclass), and the walk found them",
          _vocab_is_the_predicates)

    def _replay_equivalence() -> None:
        """predicate(blob) == predicate(blob_of(tags_of(blob))) — the
        property replay rests on"""
        agree = 0
        for blob in CORPUS:
            tb = failfix.blob_of(failfix.tags_of(blob))
            for name in ("limit", "net", "filtered"):
                p = PREDICATES[name]
                assert p(blob) == p(tb), (name, blob, tb)
                agree += 1
        assert agree == 3 * len(CORPUS)
        assert any(PREDICATES["limit"](b) for b in CORPUS)      # not vacuous
        assert any(PREDICATES["net"](b) for b in CORPUS)
        assert any(PREDICATES["filtered"](b) for b in CORPUS)
    check(f"replay equivalence · limit/net/filtered agree on {len(CORPUS)} "
          "blobs and their tag reconstructions; each predicate fires on some",
          _replay_equivalence)


def sec_leaves() -> None:
    print("\n§2  validated leaves, and phase as evidence")

    def _leaves() -> None:
        fx = failfix.build(lane="Nonsense", site="?", observed={
            "stream_code": "authentication_failed", "terminal_reason": "api_error",
            "exit_code": "7", "api_error_status": True},
            text={}, recorded={"typed": "401"}, cli={"version": "2.1.258"},
            codex={"status": "failed", "pool": "reserve", "served": "<sent>",
                   "error_code": "usage_limit_reached", "rpc_code": "-32000",
                   "kind_recorded": "usage limit (prose)"})
        assert fx["lane"] == "other" and fx["site"] == "other"
        o = fx["observed"]
        assert o["stream_code"] == "authentication_failed"
        assert o["terminal_reason"] == "api_error" and o["exit_code"] == 7
        assert o["api_error_status"] is None, "a bool is not a status"
        assert fx["recorded"]["typed"] == 401 and fx["cli"]["version"] == "2.1.258"
        c = fx["codex"]
        assert c["status"] == "failed" and c["pool"] == "reserve"
        assert c["error_code"] == "usage_limit_reached" and c["rpc_code"] == -32000
        assert c["kind_recorded"] == "other", c
        bad = failfix.build(lane="claude", site="terminal", observed={
            "stream_code": "Please run claude auth login", "terminal_reason": "x y"},
            text={}, recorded={}, cli={"version": "v2 (built by planted)"})
        assert bad["observed"]["stream_code"] == "other"
        assert bad["observed"]["terminal_reason"] == "other"
        assert bad["cli"]["version"] is None
        assert failfix.ran_as_sentinel("0f3c-some-account-uuid") == "account"
    check("every string leaf is a closed vocabulary or a strict pattern; a "
          "sentence becomes 'other' or None; ran_as is a sentinel, not a hash",
          _leaves)

    def _phase() -> None:
        ph = failfix.phase_of
        assert ph({"started": False}, None) == "unknown"          # no output ≠ nothing ran
        assert ph({"started": False, "api_error_status": 401}, None) == "admission"
        assert ph({"started": True, "boundary": False}, None) == "stream"
        assert ph({"started": True, "boundary": True, "is_error": True}, None) == "result-error"
        assert ph({"started": True, "boundary": True, "stream_status": 429}, None) == "result-error"
        assert ph({"started": True, "boundary": True, "exit_code": 1}, None) == "teardown"
        assert ph({"started": True, "boundary": True, "exit_code": 0}, None) == "unknown"
        assert ph({}, {"rejected_recorded": True}) == "admission"
        assert ph({}, {"rpc_code": -32000, "items_seen": 0}) == "admission"
        assert ph({}, {"status": None, "items_seen": 3}) == "stream"
        assert ph({}, {"status": "failed", "items_seen": 3}) == "unknown"
        assert ph({}, {"status": None, "items_seen": 0}) == "unknown"     # a late timeout
    check("phase: admission/teardown only on evidence; unknown otherwise; "
          "result-error when the boundary carried the error", _phase)

    def _allowlist() -> None:
        fx = failfix.build(lane="claude", site="terminal",
                           observed={"started": True, "prompt": "THE PROMPT"},
                           text={"err_blob": "x", "body": "MAIL BODY"},
                           recorded={"net": True, "extra": "SECRET"})
        js = json.dumps(fx)
        assert "THE PROMPT" not in js and "MAIL BODY" not in js and "SECRET" not in js
        assert set(fx) == {"schema", "lane", "site", "at", "observed", "tags",
                           "lens", "recorded", "codex", "ran_as", "cli", "phase"}
        assert "org" not in fx and "node" not in fx
    check("allowlist · unknown keys do not exist; no org, node, host or path "
          "in the fixture", _allowlist)


def sec_bounds() -> None:
    print("\n§3  bounds")
    root = tempfile.mkdtemp(prefix="failfix-bounds-")

    def _ring() -> None:
        for i in range(failfix.RING + 1):
            p = failfix.record(root, "o", "n", lane="claude", site="terminal",
                               observed={"run": i}, text={}, recorded={},
                               at=f"2026-09-05T00:00:{i % 60:02d}Z")
            assert p, i
        names = failfix.list_fixtures(root, "o", "n")
        assert len(names) == failfix.RING, len(names)
        assert failfix.load(names[0])["observed"]["run"] == 1, "oldest not evicted"
    check(f"ring · the {failfix.RING + 1}th fixture evicts the oldest", _ring)

    def _cap() -> None:
        big = ("ECONNRESET " * 3000) + ("E" * 20_000)
        fx = failfix.build(lane="claude", site="terminal", observed={},
                           text={"err_blob": big, "stderr_tail": big,
                                 "result_detail": big}, recorded={})
        assert len(json.dumps(fx, indent=1).encode()) <= failfix.CAP_BYTES
        assert fx["lens"]["err_blob"] == len(big)
        assert len(fx["tags"]["err_blob"]) <= failfix.TAG_MAX
    check("cap · a 50 KB blob yields a bounded tag list and its length; the "
          "fixture stays under the byte cap", _cap)

    def _unwritable() -> None:
        blocked = os.path.join(root, "blocked")
        os.makedirs(blocked, exist_ok=True)
        with open(os.path.join(blocked, "failfix"), "w") as f:
            f.write("a file where the directory should be")
        assert failfix.record(blocked, "o", "n", lane="claude", site="terminal",
                              observed={}, text={}, recorded={}) is None
    check("fail-open · an unwritable root records nothing and raises nothing",
          _unwritable)

    def _bad_input() -> None:
        assert failfix.record(root, "o", "n", lane="claude", site="terminal",
                              observed=None, text=None, recorded=None) is None  # type: ignore[arg-type]
    check("fail-open · malformed inputs record nothing and raise nothing",
          _bad_input)


def _last_fixture(slug: str, nid: str) -> dict:
    names = failfix.list_fixtures(store.DATA_ROOT, slug, nid)
    fixture(bool(names), "no fixture was written under the data root")
    return failfix.load(names[-1])


def sec_real() -> dict:
    print("\n§4  real failures through the fake CLI")
    got: dict = {}

    slug, nid = rig.probe_org()
    rig.set_mode("died-in-flight")
    rig.run_turn(slug, nid, "hello " + CANARIES[0])

    def _died() -> None:
        n = rig.node(slug, nid)
        fixture(bool(n.get("frozen")), "the died-in-flight turn did not freeze")
        fx = _last_fixture(slug, nid)
        got.update(fx)
        o = fx["observed"]
        assert fx["lane"] == "claude" and fx["site"] == "terminal", fx
        assert o["started"] and not o["boundary"] and o["exit_only"], o
        assert o["exit_code"] == 1 and o["run"] == 1 and not o["exhausted"], o
        assert fx["phase"] == "stream", fx["phase"]
        assert fx["recorded"]["verdict"] == "net", fx["recorded"]
        assert "exit:1" in fx["tags"]["err_blob"], fx["tags"]
        assert "quarterly" not in json.dumps(fx) and "hello" not in json.dumps(fx)
    check("died-in-flight · fixture: site terminal, started, no boundary, "
          "exit_only, run 1, phase stream, recorded verdict net; the message "
          "is not in it", _died)

    def _replay_same() -> None:
        fixture(bool(got), "no fixture from the died-in-flight check")
        out = failfix.replay(got, PREDICATES)
        assert out["drift"] == [], out
        assert out["recomputed"]["verdict"] == "net" == out["recorded"]["verdict"]
    check("replay · failclass recomputes verdict net from the fixture, no "
          "drift, recorded and recomputed reported apart", _replay_same)

    def _drift_control() -> None:
        fixture(bool(got), "no fixture from the died-in-flight check")
        bad = copy.deepcopy(got)
        bad["observed"]["boundary"] = True         # a straggler, not a casualty
        out = failfix.replay(bad, PREDICATES)
        assert "verdict" in out["drift"] and out["recomputed"]["verdict"] == "none", out
        assert "phase" in out["drift"] and out["phase"] == "teardown", out
    check("positive control · an edited fixture DRIFTS (boundary reached → "
          "verdict none, phase teardown)", _drift_control)

    slug2, nid2 = rig.probe_org()
    planted = ("Invalid API key " + SECRETS[0] + " for " + SECRETS[3] + " at "
               + SECRETS[4] + " " + SECRETS[1] + " cfg " + SECRETS[2]
               + " · " + CANARIES[1])
    rig.set_mode("iserror", limit_text=planted, api_error_status=401)
    rig.run_turn(slug2, nid2, "second " + CANARIES[2])
    got2: dict = {}

    def _redacted_401() -> None:
        fx = _last_fixture(slug2, nid2)
        got2.update(fx)
        js = json.dumps(fx).lower()
        for s in SECRETS:
            assert s.lower() not in js and "planted" not in js, (s, js)
        for canary in CANARIES:
            for word in canary.lower().split():
                if len(word) >= 5:
                    assert word not in js, (canary, word)
        o = fx["observed"]
        assert o["api_error_status"] == 401 and o["is_error"] and o["boundary"], o
        assert "status:401" in fx["tags"]["result_detail"], fx["tags"]
        assert "invalid api key" in fx["tags"]["err_blob"], fx["tags"]
        assert fx["lens"]["err_blob"] == len(planted), fx["lens"]
        assert fx["recorded"]["verdict"] == "none", fx["recorded"]
        assert fx["phase"] == "result-error", fx["phase"]
        assert not rig.node(slug2, nid2).get("frozen")
    check("is_error 401 · secrets and canaries absent; status 401, is_error, "
          "boundary, tag invalid api key, length kept; verdict none; phase "
          "result-error; not frozen", _redacted_401)

    def _replay_401() -> None:
        fixture(bool(got2), "no fixture from the 401 check")
        assert failfix.replay(got2, PREDICATES)["drift"] == []
    check("replay · the 401 fixture recomputes verdict none, no drift",
          _replay_401)

    slug3, nid3 = rig.probe_org()
    rig.set_mode("died-with-stderr")
    rig.run_turn(slug3, nid3, "third")

    def _with_stderr() -> None:
        fx = _last_fixture(slug3, nid3)
        o = fx["observed"]
        assert o["started"] and not o["boundary"] and not o["exit_only"], o
        assert "enospc" in fx["tags"]["err_blob"], fx["tags"]
        assert fx["recorded"]["verdict"] == "none" and fx["phase"] == "stream"
        assert failfix.replay(fx, PREDICATES)["drift"] == []
        assert not rig.node(slug3, nid3).get("frozen"), "must stay terminal"
    check("died-with-stderr · same death WITH evidence: not exit_only, tag "
          "enospc, verdict none (terminal), phase stream, replay agrees",
          _with_stderr)

    def _log_row_untouched() -> None:
        rows = (store.load_org(slug2).d.get("turn_error_log") or {}).get(nid2) or []
        assert rows and rows[-1]["text"].startswith("turn failed"), rows
    check("the existing turn_error_log row is still written", _log_row_untouched)
    got["_slug"], got["_nid"] = slug, nid
    return got


HOOK = textwrap.dedent('''
    import builtins, sys
    BANNED = ("orgtree.store", "orgtree.supervisor", "orgtree.ledger",
              "orgtree.codex_route", "orgtree.providers", "subprocess",
              "socket", "http", "urllib", "sqlite3", "threading")
    class _Refuse:
        def find_spec(self, name, path=None, target=None):
            if name in BANNED or any(name.startswith(b + ".") for b in BANNED):
                raise ImportError("PURITY: import of %s refused" % name)
            return None
    sys.meta_path.insert(0, _Refuse())
    _open = builtins.open
    def _ro(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            raise PermissionError("PURITY: write refused: %r" % (file,))
        return _open(file, mode, *a, **k)
    builtins.open = _ro
''')


def sec_tool(got: dict) -> None:
    print("\n§5  the replay tool")
    tool = os.path.join(HERE, "..", "..", "tools", "replay_failure.py")
    names = failfix.list_fixtures(store.DATA_ROOT, got["_slug"], got["_nid"])
    fixture(bool(names), "no fixture on disk for the tool")
    env = dict(os.environ)
    env.pop("ORGTREE_DATA", None)        # the tool must not need one

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], capture_output=True,
                              text=True, env=env, timeout=120)

    def _ok() -> None:
        p = _run(tool, names[-1], "--assert")
        assert p.returncode == 0, (p.returncode, p.stdout[-800:], p.stderr[-800:])
        out = json.loads(p.stdout.strip().splitlines()[-1])
        assert out["drift"] == [] and out["recomputed"]["verdict"] == "net", out
    check("tool · --assert exits 0; recomputed verdict printed beside the "
          "recorded one", _ok)

    def _drift() -> None:
        bad = failfix.load(names[-1])
        bad["recorded"]["verdict"] = "none"
        bp = os.path.join(tempfile.mkdtemp(prefix="failfix-drift-"), "bad.json")
        with open(bp, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        p = _run(tool, bp, "--assert")
        assert p.returncode == 1, (p.returncode, p.stdout[-800:], p.stderr[-800:])
        assert '"verdict"' in p.stdout
    check("tool · positive control: a fixture whose recorded verdict is wrong "
          "exits 1", _drift)

    def _pure_under_hook() -> None:
        runner = os.path.join(tempfile.mkdtemp(prefix="failfix-pure-"), "run.py")
        with open(runner, "w", encoding="utf-8") as f:
            f.write(HOOK + textwrap.dedent(f'''
                import runpy
                sys.argv = [{tool!r}, {names[-1]!r}, "--assert"]
                try:
                    runpy.run_path({tool!r}, run_name="__main__")
                except SystemExit as e:
                    raise SystemExit(int(e.code or 0))
                '''))
        p = _run(runner)
        assert p.returncode == 0, (p.returncode, p.stdout[-800:], p.stderr[-1200:])
        assert "PURITY" not in p.stderr
        mods = {"orgtree.store", "orgtree.supervisor", "orgtree.ledger"}
        assert not (set(p.stdout.split()) & mods)
    check("purity · the tool completes under an import hook that refuses "
          "storage/provider/process modules and a write-refusing open()",
          _pure_under_hook)

    def _hook_bites() -> None:
        """the control: the same hook DOES stop an import of orgtree.store"""
        runner = os.path.join(tempfile.mkdtemp(prefix="failfix-pure-"), "ctl.py")
        backend = os.path.join(HERE, "..")
        with open(runner, "w", encoding="utf-8") as f:
            f.write(HOOK + textwrap.dedent(f'''
                sys.path.insert(0, {backend!r})
                import os; os.environ["ORGTREE_DATA"] = {store.DATA_ROOT!r}
                try:
                    from orgtree import store
                except ImportError as e:
                    print("REFUSED", e); raise SystemExit(3)
                print("IMPORTED"); raise SystemExit(0)
                '''))
        p = _run(runner)
        assert p.returncode == 3 and "REFUSED" in p.stdout, (p.returncode, p.stdout, p.stderr[-600:])
        runner2 = runner + "2.py"
        with open(runner2, "w", encoding="utf-8") as f:
            f.write(HOOK + textwrap.dedent(f'''
                try:
                    open({os.path.join(tempfile.gettempdir(), "failfix-ctl.txt")!r}, "w")
                except PermissionError as e:
                    print("REFUSED", e); raise SystemExit(3)
                print("WROTE"); raise SystemExit(0)
                '''))
        p = _run(runner2)
        assert p.returncode == 3 and "REFUSED" in p.stdout, (p.returncode, p.stdout)
    check("purity control · the hook refuses `orgtree.store` and a file "
          "write when asked to", _hook_bites)


def sec_identity() -> None:
    print("\n§6  failclass is the supervisor's code")

    def _same_source() -> None:
        sup = open(os.path.join(HERE, "..", "orgtree", "supervisor.py"),
                   encoding="utf-8").read()
        fc = open(os.path.join(HERE, "..", "orgtree", "failclass.py"),
                  encoding="utf-8").read()
        st, ft = ast.parse(sup), ast.parse(fc)
        sf = {n.name: ast.get_source_segment(sup, n) for n in st.body
              if isinstance(n, ast.FunctionDef)}
        ff = {n.name: ast.get_source_segment(fc, n) for n in ft.body
              if isinstance(n, ast.FunctionDef)}
        for name in PURE_FNS:
            assert name in sf and name in ff, name
            assert sf[name] == ff[name], f"{name} differs from supervisor.py"
        assert set(ff) == set(PURE_FNS), set(ff) ^ set(PURE_FNS)
        assert supervisor._STATUS_KEYS == failclass._STATUS_KEYS
    check("every failclass function is byte-identical to the supervisor's, "
          "and nothing else is in failclass", _same_source)

    def _same_answers() -> None:
        for blob in CORPUS:
            for name in ("_looks_like_usage_limit", "_looks_like_connection_failure",
                         "_looks_like_filtered"):
                assert getattr(supervisor, name)(blob) == getattr(failclass, name)(blob)
        for eo in (True, False):
            for s in (True, False):
                for b in (True, False):
                    assert supervisor._died_in_flight(exit_only=eo, started=s, boundary=b) \
                        == failclass._died_in_flight(exit_only=eo, started=s, boundary=b)
        for res, se in (({"is_error": True, "api_error_status": 401}, {}),
                        ({"is_error": False, "api_error_status": 401}, {"status": 503}),
                        ({}, {"status": "401"}), ({}, {"status": True})):
            assert supervisor._typed_api_status(res, se) == failclass._typed_api_status(res, se)
    check("both copies answer identically over the corpus and the shape grid",
          _same_answers)


def main() -> int:
    sec_tags()
    sec_leaves()
    sec_bounds()
    sec_identity()
    if shutil.which("node"):
        got = sec_real()
        if got.get("_slug"):
            sec_tool(got)
    else:
        NOTES.append("INERT: `node` is not on PATH — §4 and §5 (real failures "
                     "through the fake CLI, the tool) DID NOT RUN")
    for n in NOTES:
        print(f"\n  ! {n}")
    print(f"\n{PASSED} passed, {len(FAILED)} failed"
          + (f", {len(NOTES)} inert" if NOTES else ""))
    for f in FAILED:
        print("\n" + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
