"""Redacted failure fixtures (backend/orgtree/failfix.py, failclass.py,
tools/replay_failure.py, docs/failure-fixtures.md).

    §1  features — canaries (sentences AND identifier-shaped) vanish from
        EVERY capture field; diagnostic controls survive; the vocabulary IS
        the predicates' (AST); predicate(blob) == predicate(blob_of(features))
        over a corpus AND on the maximal mixed input; the bound is the
        vocabulary itself
    §2  validated leaves — closed vocabularies; typed evidence preserved
        strictly, never coerced; phase from established facts only
    §3  bounds — the ring, the byte cap (maximal input), unwritable root,
        malformed input
    §4  CAPTURE through the real helper (supervisor._failfix_record): the
        camelCase status spelling is typed evidence, a digit string is not,
        and neither drifts on replay
    §5  REAL failures through the fake CLI (test_limit_freeze's rig)
    §6  the replay tool as a subprocess, --assert both ways, under a purity
        import hook with a biting control
    §7  failclass' sources are byte-identical to the supervisor's

§5 and §6 spawn `node` for the stand-in CLI; declared INERT when absent.

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

# canaries: confidential-looking SENTENCES with no key/path shape …
CANARIES = ("the quarterly numbers are down twelve percent",
            "ZEBRA-OTTER-7731 said the merger closes friday",
            "patient initials JD, room 4, dosage doubled")
# … and IDENTIFIER-shaped ones, which a shape filter would let through
IDENT_CANARIES = ("privateclientname", "internalpassword", "zebraotter7731",
                  "acme_corp_prod", "--privateclientname")
SECRETS = ("sk-ant-api03-PLANTEDSECRET0123456789abcdef",
           "Bearer PLANTEDBEARER.token", r"C:\Users\planted\notes.txt",
           "planted.user@example.com", "https://planted.example.com/x?k=1")
# nonsecret diagnostic controls → (feature list, member)
CONTROLS = (("Error: ECONNRESET while reading", "net", "econnreset"),
            ("socket hang up", "net", "socket hang up"),
            ("You've hit your limit · resets 1:40pm", "limit", "hit your"),
            ("API status 401 · Invalid API key", "status", 401),
            ("the CLI exited 1 without writing anything to stderr", "exit", 1),
            ("error: unknown option '--effort'", "option", "--effort"),
            ("turn/steer: {\"code\": -32000}", "rpc", -32000),
            ("usageLimitExceeded", "code", "usagelimitexceeded"),
            ("blocked by content filtering policy", "filter", "content filter"),
            ("ENOSPC: no space left on device", "diag", "enospc"))
CORPUS = [c for c, _, _ in CONTROLS] + list(CANARIES) + list(IDENT_CANARIES) + [
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
# the MAXIMAL mixed input: every vocabulary phrase, and more numbers than fit
MAXIMAL = (" · ".join(failfix.VOCAB)
           + " " + " ".join(f"API status {s}" for s in range(400, 420))
           + " " + " ".join(f"the CLI exited {e}" for e in range(1, 12))
           + " " + " ".join(f"-32{n:03d}" for n in range(0, 12))
           + " " + " ".join(f"unknown option '{o}'"
                            for o in sorted(failfix.CLI_OPTIONS))
           + " " + CANARIES[0] + " " + IDENT_CANARIES[0])


def _every_leaf(o, out: list) -> list:
    if isinstance(o, dict):
        for k, v in o.items():
            out.append(str(k))
            _every_leaf(v, out)
    elif isinstance(o, list):
        for v in o:
            _every_leaf(v, out)
    else:
        out.append(str(o))
    return out


def _assert_no_canary(fx: dict, extra: tuple[str, ...] = ()) -> None:
    leaves = " ".join(_every_leaf(fx, [])).lower()
    for canary in CANARIES:
        for word in canary.lower().split():
            if len(word) >= 5:
                assert word not in leaves, (canary, word)
    for ident in IDENT_CANARIES + extra:
        assert ident.lower().lstrip("-") not in leaves, (ident, leaves[:400])


def sec_features() -> None:
    print("\n§1  features")

    def _canaries_vanish_everywhere() -> None:
        for canary in CANARIES + IDENT_CANARIES:
            fx = failfix.build(
                lane="claude", site="terminal",
                observed={"stream_code": canary, "terminal_reason": canary,
                          "started": True},
                text={"err_blob": f"unknown option '{canary}' ECONNRESET {canary}",
                      "stderr_tail": canary, "result_detail": canary},
                recorded={"net": True},
                codex={"status": canary, "pool": canary, "served": canary,
                       "error_code": canary, "kind_recorded": canary},
                ran_as=canary, cli={"version": canary}, at=canary)
            _assert_no_canary(fx)
            assert "econnreset" in fx["features"]["err_blob"]["net"]
            assert fx["features"]["err_blob"]["option"] in ([], ["other"]), fx["features"]
            assert fx["lens"]["stderr_tail"] == len(canary)
    check("sentence AND identifier canaries vanish from every leaf (text, "
          "stream code, terminal reason, codex status/pool/error_code/kind, "
          "option, ran_as, version, at); the errno beside one survives",
          _canaries_vanish_everywhere)

    def _secrets_vanish() -> None:
        for s in SECRETS:
            f = failfix.features_of(f"turn failed: {s} / then ECONNRESET")
            leaves = " ".join(_every_leaf(f, [])).lower()
            assert "econnreset" in leaves and "planted" not in leaves, (s, f)
    check("key / bearer / path / email / url never become a feature",
          _secrets_vanish)

    def _controls() -> None:
        for text, key, member in CONTROLS:
            assert member in failfix.features_of(text)[key], (text, failfix.features_of(text))
    check("nonsecret diagnostic controls each produce their feature",
          _controls)

    def _vocab_is_the_predicates() -> None:
        src = open(os.path.join(HERE, "..", "orgtree", "failclass.py"),
                   encoding="utf-8").read()
        found: set[str] = set()
        for n in ast.parse(src).body:
            if isinstance(n, ast.FunctionDef) and n.name.startswith("_looks_like"):
                for c in ast.walk(n):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                            and c.value and "\n" not in c.value \
                            and not c.value.startswith(" "):
                        found.add(c.value)
        missing = sorted(found - set(failfix.VOCAB))
        assert not missing, f"predicate phrases missing from VOCAB: {missing}"
        assert len(found) >= 30, len(found)
    check("the vocabulary contains every phrase the predicates search for "
          "(AST over failclass)", _vocab_is_the_predicates)

    def _equivalence() -> None:
        agree = 0
        for blob in CORPUS + [MAXIMAL]:
            tb = failfix.blob_of(failfix.features_of(blob))
            for name in ("limit", "net", "filtered"):
                assert PREDICATES[name](blob) == PREDICATES[name](tb), (name, blob[:80])
                agree += 1
        assert agree == 3 * (len(CORPUS) + 1)
        for name in ("limit", "net", "filtered"):
            assert any(PREDICATES[name](b) for b in CORPUS)
    check(f"replay equivalence over {len(CORPUS)} blobs and the maximal mixed "
          "input; each predicate fires on some", _equivalence)

    def _bound_is_the_vocabulary() -> None:
        f = failfix.features_of(MAXIMAL)
        for key, words in failfix.FEATURE_VOCAB.items():
            assert f[key] == list(words), (key, f[key])     # ALL present, in order
        assert len(f["status"]) == failfix.NUM_MAX and f["status"][0] == 400
        assert len(f["exit"]) == failfix.NUM_MAX and len(f["rpc"]) == failfix.NUM_MAX
        assert len(f["option"]) == failfix.NUM_MAX and "other" not in f["option"]
        assert failfix.features_of("unknown option '--privateclientname'")["option"] == ["other"]
        fx = failfix.build(lane="claude", site="terminal", observed={},
                           text={k: MAXIMAL for k in failfix.TEXT_FIELDS},
                           recorded={})
        assert len(json.dumps(fx, indent=1).encode()) <= failfix.CAP_BYTES, \
            len(json.dumps(fx, indent=1).encode())
        _assert_no_canary(fx)
    check("bound · on the maximal mixed input every vocabulary phrase is "
          "present (nothing later dropped), numbers cap at NUM_MAX, an "
          "unknown option is 'other', and three maximal inputs fit the cap",
          _bound_is_the_vocabulary)


def sec_leaves() -> None:
    print("\n§2  validated leaves, typed evidence, phase")

    def _leaves() -> None:
        fx = failfix.build(lane="Nonsense", site="?", observed={
            "stream_code": "authentication_failed", "terminal_reason": "api_error",
            "exit_code": 7, "api_error_status": 401},
            text={}, recorded={"typed": 401}, cli={"version": "2.1.258"},
            codex={"status": "failed", "pool": "reserve", "served": "<sent>",
                   "error_code": "usagelimitexceeded", "rpc_code": -32000,
                   "kind_recorded": "usage-limit"})
        assert fx["lane"] == "other" and fx["site"] == "other"
        o = fx["observed"]
        assert o["stream_code"] == "authentication_failed"
        assert o["terminal_reason"] == "api_error" and o["exit_code"] == 7
        assert fx["recorded"]["typed"] == 401 and fx["cli"]["version"] == "2.1.258"
        c = fx["codex"]
        assert c["status"] == "failed" and c["pool"] == "reserve"
        assert c["error_code"] == "usagelimitexceeded" and c["rpc_code"] == -32000
        assert c["kind_recorded"] == "usage-limit", c
        bad = failfix.build(lane="claude", site="terminal", observed={
            "stream_code": "Please run claude auth login", "terminal_reason": "x y",
            "exit_code": "7"},
            text={}, recorded={}, cli={"version": "v2 (built by planted)"},
            codex={"error_code": "privateclientname",
                   "kind_recorded": "internalpassword", "rpc_code": "-32000"})
        assert bad["observed"]["stream_code"] == "other"
        assert bad["observed"]["terminal_reason"] == "other"
        assert bad["observed"]["exit_code"] is None, "a digit string is not an int"
        assert bad["cli"]["version"] is None
        assert bad["codex"]["error_code"] == "other", bad["codex"]
        assert bad["codex"]["kind_recorded"] == "other", bad["codex"]
        assert bad["codex"]["rpc_code"] is None
        assert failfix.ran_as_sentinel("0f3c-some-account-uuid") == "account"
    check("closed vocabularies: known codes/kinds/reasons pass, identifier-"
          "shaped unknowns become 'other', digit strings are not ints",
          _leaves)

    def _typed_strict() -> None:
        """the fixture never turns invalid evidence into valid evidence"""
        for bad in ("401", True, 401.0, 399, 600, None):
            fx = failfix.build(lane="claude", site="terminal",
                               observed={"api_error_status": bad,
                                         "stream_status": bad},
                               text={}, recorded={"typed": bad})
            assert fx["observed"]["api_error_status"] is None, bad
            assert fx["observed"]["stream_status"] is None, bad
            assert fx["recorded"]["typed"] is None, bad
        fx = failfix.build(lane="openrouter", site="terminal",
                           observed={"api_error_status": 401, "is_error": True},
                           text={}, recorded={"limit": True, "typed": 401})
        assert fx["observed"]["api_error_status"] == 401
        assert failfix.replay(fx, PREDICATES)["drift"] == []
        # Astra's counterexample: '401' with the predicate saying None
        fx2 = failfix.build(lane="openrouter", site="terminal",
                            observed={"api_error_status": "401", "is_error": True},
                            text={}, recorded={"typed": failclass._typed_api_status(
                                {"is_error": True, "api_error_status": "401"}, {})})
        assert fx2["observed"]["api_error_status"] is None
        assert fx2["recorded"]["typed"] is None
        assert failfix.replay(fx2, PREDICATES)["drift"] == [], failfix.replay(fx2, PREDICATES)
    check("typed evidence: '401'/True/401.0/399/600 → None everywhere; int "
          "401 kept; neither drifts on replay (the coercion counterexample)",
          _typed_strict)

    def _phase() -> None:
        ph = failfix.phase_of
        assert ph({"started": False}, None) == "unknown"
        assert ph({"started": False, "api_error_status": 401}, None) == "unknown", \
            "a typed status with no boundary does not establish admission"
        assert ph({"started": False, "boundary": True, "is_error": True,
                   "api_error_status": 401}, None) == "admission"
        assert ph({"started": False, "boundary": True, "is_error": True,
                   "api_error_status": 500}, None) == "unknown"
        assert ph({"started": True, "boundary": False}, None) == "stream"
        assert ph({"started": True, "boundary": True, "is_error": True}, None) == "result-error"
        assert ph({"started": True, "boundary": True, "stream_status": 429}, None) == "result-error"
        assert ph({"started": True, "boundary": True, "exit_code": 1}, None) == "teardown"
        assert ph({"started": True, "boundary": True, "exit_code": 0}, None) == "unknown"
        assert ph({}, {"rejected_recorded": True}) == "admission"
        # Astra's counterexample: an RPC error with usage and text observed
        assert ph({}, {"rpc_code": -32000, "items_seen": 0, "had_usage": True,
                       "text_len": 10, "status": "failed"}) == "unknown"
        assert ph({}, {"rpc_code": -32000, "items_seen": 0}) == "unknown"
        assert ph({}, {"status": None, "items_seen": 3}) == "stream"
        assert ph({}, {"status": "failed", "items_seen": 3}) == "unknown"
        assert ph({}, {"status": None, "items_seen": 0}) == "unknown"
    check("phase: admission only from a refusing boundary (claude) or the "
          "recorded rejection (codex); an RPC error alone, a typed status "
          "with no boundary, a late timeout → unknown", _phase)

    def _allowlist() -> None:
        fx = failfix.build(lane="claude", site="terminal",
                           observed={"started": True, "prompt": "THE PROMPT"},
                           text={"err_blob": "x", "body": "MAIL BODY"},
                           recorded={"net": True, "extra": "SECRET"})
        js = json.dumps(fx)
        assert "THE PROMPT" not in js and "MAIL BODY" not in js and "SECRET" not in js
        assert set(fx) == {"schema", "lane", "site", "at", "observed", "features",
                           "lens", "recorded", "codex", "ran_as", "cli", "phase"}
    check("allowlist · unknown keys do not exist; no org, node, host or path",
          _allowlist)


def sec_bounds() -> None:
    print("\n§3  bounds")
    root = tempfile.mkdtemp(prefix="failfix-bounds-")

    def _ring() -> None:
        for i in range(failfix.RING + 1):
            assert failfix.record(root, "o", "n", lane="claude", site="terminal",
                                  observed={"run": i}, text={}, recorded={}), i
        names = failfix.list_fixtures(root, "o", "n")
        assert len(names) == failfix.RING, len(names)
        assert failfix.load(names[0])["observed"]["run"] == 1, "oldest not evicted"
    check(f"ring · the {failfix.RING + 1}th fixture evicts the oldest", _ring)

    def _unwritable() -> None:
        blocked = os.path.join(root, "blocked")
        os.makedirs(blocked, exist_ok=True)
        with open(os.path.join(blocked, "failfix"), "w") as f:
            f.write("a file where the directory should be")
        assert failfix.record(blocked, "o", "n", lane="claude", site="terminal",
                              observed={}, text={}, recorded={}) is None
    check("fail-open · an unwritable root records nothing, raises nothing",
          _unwritable)

    def _bad_input() -> None:
        assert failfix.record(root, "o", "n", lane="claude", site="terminal",
                              observed=None, text=None, recorded=None) is None  # type: ignore[arg-type]
    check("fail-open · malformed inputs record nothing, raise nothing",
          _bad_input)


def sec_capture() -> None:
    print("\n§4  capture through the real helper")
    slug, nid = rig.probe_org()

    def _one(res: dict, stream: dict) -> dict:
        # the site's own class choice: a typed status decides exclusively,
        # prose only when there is none (supervisor, 2026-09-05 OpenRouter rule)
        blob = "Invalid API key " + IDENT_CANARIES[0]
        typed = supervisor._typed_api_status(res, stream)
        limit = (typed in (401, 402, 429) if typed is not None
                 else supervisor._looks_like_usage_limit(blob))
        net = (typed >= 500 if typed is not None
               else supervisor._looks_like_connection_failure(blob))
        supervisor._failfix_record(
            slug, nid, site="terminal", lane="openrouter",
            err_blob=blob, err="",
            res=res, stream_err=stream, exit_code=1, parked=False,
            exit_only=False, started=False, boundary=True, run=0,
            exhausted=False, limit=limit, net=net,
            typed=typed, ran_as="openrouter")
        names = failfix.list_fixtures(store.DATA_ROOT, slug, nid)
        fixture(bool(names), "the helper wrote no fixture")
        return failfix.load(names[-1])

    def _camel() -> None:
        fx = _one({"is_error": True, "apiErrorStatus": 401, "result": "Invalid API key"}, {})
        assert fx["observed"]["api_error_status"] == 401, fx["observed"]
        assert fx["recorded"]["typed"] == 401 and fx["phase"] == "admission"
        assert failfix.replay(fx, PREDICATES)["drift"] == []
        _assert_no_canary(fx)
    check("capture · the CLI's camelCase status is typed evidence: 401, "
          "phase admission, no drift", _camel)

    def _digits() -> None:
        fx = _one({"is_error": True, "api_error_status": "401",
                   "result": "Invalid API key"}, {"status": "503"})
        assert fx["observed"]["api_error_status"] is None, fx["observed"]
        assert fx["observed"]["stream_status"] is None
        assert fx["recorded"]["typed"] is None and fx["phase"] == "unknown"
        assert failfix.replay(fx, PREDICATES)["drift"] == []
    check("capture · a digit-string status is NOT evidence: None, phase "
          "unknown, no drift (never coerced into a refusal)", _digits)


def _last_fixture(slug: str, nid: str) -> dict:
    names = failfix.list_fixtures(store.DATA_ROOT, slug, nid)
    fixture(bool(names), "no fixture was written under the data root")
    return failfix.load(names[-1])


def sec_real() -> dict:
    print("\n§5  real failures through the fake CLI")
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
        assert fx["phase"] == "stream" and fx["recorded"]["verdict"] == "net"
        assert 1 in fx["features"]["err_blob"]["exit"], fx["features"]
        _assert_no_canary(fx, ("hello",))
    check("died-in-flight · site terminal, started, no boundary, exit_only, "
          "run 1, phase stream, verdict net; the message is not in it", _died)

    def _replay_same() -> None:
        fixture(bool(got), "no fixture from the died-in-flight check")
        out = failfix.replay(got, PREDICATES)
        assert out["drift"] == [] and out["recomputed"]["verdict"] == "net", out
    check("replay · failclass recomputes verdict net, no drift", _replay_same)

    def _drift_control() -> None:
        fixture(bool(got), "no fixture from the died-in-flight check")
        bad = copy.deepcopy(got)
        bad["observed"]["boundary"] = True
        out = failfix.replay(bad, PREDICATES)
        assert "verdict" in out["drift"] and out["recomputed"]["verdict"] == "none", out
        assert "phase" in out["drift"] and out["phase"] == "teardown", out
    check("positive control · an edited fixture DRIFTS (boundary reached → "
          "verdict none, phase teardown)", _drift_control)

    slug2, nid2 = rig.probe_org()
    planted = ("Invalid API key " + SECRETS[0] + " for " + SECRETS[3] + " at "
               + SECRETS[4] + " " + SECRETS[1] + " cfg " + SECRETS[2] + " · "
               + CANARIES[1] + " unknown option '" + IDENT_CANARIES[4] + "'")
    rig.set_mode("iserror", limit_text=planted, api_error_status=401)
    rig.run_turn(slug2, nid2, "second " + CANARIES[2])
    got2: dict = {}

    def _redacted_401() -> None:
        fx = _last_fixture(slug2, nid2)
        got2.update(fx)
        leaves = " ".join(_every_leaf(fx, [])).lower()
        for s in SECRETS:
            assert s.lower() not in leaves and "planted" not in leaves, s
        _assert_no_canary(fx)
        o = fx["observed"]
        assert o["api_error_status"] == 401 and o["is_error"] and o["boundary"], o
        assert 401 in fx["features"]["result_detail"]["status"], fx["features"]
        assert "invalid api key" in fx["features"]["err_blob"]["code"], fx["features"]
        assert fx["features"]["err_blob"]["option"] == ["other"], fx["features"]
        assert fx["lens"]["err_blob"] == len(planted), fx["lens"]
        assert fx["recorded"]["verdict"] == "none" and fx["phase"] == "result-error"
        assert not rig.node(slug2, nid2).get("frozen")
    check("is_error 401 · secrets, sentence and identifier canaries absent; "
          "status 401, is_error, boundary, code feature, option 'other', "
          "length; verdict none; phase result-error; not frozen", _redacted_401)

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
        assert "enospc" in fx["features"]["err_blob"]["diag"], fx["features"]
        assert fx["recorded"]["verdict"] == "none" and fx["phase"] == "stream"
        assert failfix.replay(fx, PREDICATES)["drift"] == []
        assert not rig.node(slug3, nid3).get("frozen"), "must stay terminal"
    check("died-with-stderr · not exit_only, diag enospc, verdict none, "
          "phase stream, replay agrees, stays terminal", _with_stderr)

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
    print("\n§6  the replay tool")
    tool = os.path.join(HERE, "..", "..", "tools", "replay_failure.py")
    names = failfix.list_fixtures(store.DATA_ROOT, got["_slug"], got["_nid"])
    fixture(bool(names), "no fixture on disk for the tool")
    env = dict(os.environ)
    env.pop("ORGTREE_DATA", None)

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], capture_output=True,
                              text=True, env=env, timeout=120)

    def _ok() -> None:
        p = _run(tool, names[-1], "--assert")
        assert p.returncode == 0, (p.returncode, p.stdout[-800:], p.stderr[-800:])
        out = json.loads(p.stdout.strip().splitlines()[-1])
        assert out["drift"] == [] and out["recomputed"]["verdict"] == "net", out
    check("tool · --assert exits 0; recomputed beside recorded", _ok)

    def _drift() -> None:
        bad = failfix.load(names[-1])
        bad["recorded"]["verdict"] = "none"
        bp = os.path.join(tempfile.mkdtemp(prefix="failfix-drift-"), "bad.json")
        with open(bp, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        p = _run(tool, bp, "--assert")
        assert p.returncode == 1 and '"verdict"' in p.stdout, (p.returncode, p.stdout[-400:])
    check("tool · positive control: a wrong recorded verdict exits 1", _drift)

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
    check("purity · the tool completes under an import hook that refuses "
          "storage/provider/process modules and writes", _pure_under_hook)

    def _hook_bites() -> None:
        d = tempfile.mkdtemp(prefix="failfix-pure-")
        r1 = os.path.join(d, "ctl.py")
        with open(r1, "w", encoding="utf-8") as f:
            f.write(HOOK + textwrap.dedent(f'''
                sys.path.insert(0, {os.path.join(HERE, "..")!r})
                import os; os.environ["ORGTREE_DATA"] = {store.DATA_ROOT!r}
                try:
                    from orgtree import store
                except ImportError as e:
                    print("REFUSED", e); raise SystemExit(3)
                print("IMPORTED"); raise SystemExit(0)
                '''))
        p = _run(r1)
        assert p.returncode == 3 and "REFUSED" in p.stdout, (p.returncode, p.stdout, p.stderr[-600:])
        r2 = os.path.join(d, "ctl2.py")
        with open(r2, "w", encoding="utf-8") as f:
            f.write(HOOK + textwrap.dedent(f'''
                try:
                    open({os.path.join(d, "x.txt")!r}, "w")
                except PermissionError as e:
                    print("REFUSED", e); raise SystemExit(3)
                print("WROTE"); raise SystemExit(0)
                '''))
        p = _run(r2)
        assert p.returncode == 3 and "REFUSED" in p.stdout, (p.returncode, p.stdout)
    check("purity control · the hook refuses `orgtree.store` and a write",
          _hook_bites)


def sec_identity() -> None:
    print("\n§7  failclass is the supervisor's code")

    def _same_source() -> None:
        sup = open(os.path.join(HERE, "..", "orgtree", "supervisor.py"), encoding="utf-8").read()
        fc = open(os.path.join(HERE, "..", "orgtree", "failclass.py"), encoding="utf-8").read()
        sf = {n.name: ast.get_source_segment(sup, n) for n in ast.parse(sup).body
              if isinstance(n, ast.FunctionDef)}
        ff = {n.name: ast.get_source_segment(fc, n) for n in ast.parse(fc).body
              if isinstance(n, ast.FunctionDef)}
        for name in PURE_FNS:
            assert name in sf and name in ff, name
            assert sf[name] == ff[name], f"{name} differs from supervisor.py"
        assert set(ff) == set(PURE_FNS), set(ff) ^ set(PURE_FNS)
        assert supervisor._STATUS_KEYS == failclass._STATUS_KEYS
    check("every failclass function is byte-identical to the supervisor's",
          _same_source)

    def _same_answers() -> None:
        for blob in CORPUS + [MAXIMAL]:
            for name in ("_looks_like_usage_limit", "_looks_like_connection_failure",
                         "_looks_like_filtered"):
                assert getattr(supervisor, name)(blob) == getattr(failclass, name)(blob)
        for eo in (True, False):
            for s in (True, False):
                for b in (True, False):
                    assert supervisor._died_in_flight(exit_only=eo, started=s, boundary=b) \
                        == failclass._died_in_flight(exit_only=eo, started=s, boundary=b)
        for res, se in (({"is_error": True, "api_error_status": 401}, {}),
                        ({"is_error": True, "apiErrorStatus": 402}, {}),
                        ({"is_error": False, "api_error_status": 401}, {"status": 503}),
                        ({}, {"status": "401"}), ({}, {"status": True})):
            assert supervisor._typed_api_status(res, se) == failclass._typed_api_status(res, se)
    check("both copies answer identically over the corpus, the maximal input "
          "and the shape grid", _same_answers)


def main() -> int:
    sec_features()
    sec_leaves()
    sec_bounds()
    sec_capture()
    sec_identity()
    if shutil.which("node"):
        got = sec_real()
        if got.get("_slug"):
            sec_tool(got)
    else:
        NOTES.append("INERT: `node` is not on PATH — §5 and §6 (real failures "
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
