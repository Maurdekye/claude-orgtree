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
    §8  the codex DECISION CORE (codex_decide): production reuse, not a
        copy; an independent oracle of the documented rules; the fixture's
        evidence projection re-decides to the same answer; controls drift
    §9  the antigravity lane's boundary facts, pure: replay of walled /
        reset / schedule / ceiling from typed facts, with drift controls
    §10 REAL codex and antigravity failures through the fake CLIs (python
        stand-ins, no node): the wall freezes exactly as before, the fixture
        carries the evidence, replay agrees, canaries and prose are absent;
        a NON-wall antigravity failure past a (patched, 1s) ceiling: not
        frozen, the ceiling kill recorded and recomputed, a named reset
        NOT known without a wall; the tool and the purity hook over those
        fixtures

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

# the fake codex / antigravity CLIs for §10 (env ONLY — nothing imported
# yet); a port nobody serves, so no tool traffic can reach a live backend
_CHOME = tempfile.mkdtemp(prefix="failfix-chome-")
with open(os.path.join(_CHOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')
os.environ["ORGTREE_CODEX"] = os.path.join(HERE, "fakecodex.py")
os.environ["CODEX_HOME"] = _CHOME
os.environ["ORGTREE_ANTIGRAVITY"] = os.path.join(HERE, "fakeantigravity.py")
os.environ.setdefault("ORGTREE_PORT", "9")

# the rig binds a THROWAWAY ORGTREE_DATA + HOME at import, before orgtree
import test_limit_freeze as rig                                  # noqa: E402
from orgtree import (codex_decide, codex_route, failclass, failfix,  # noqa: E402
                     store, supervisor)
from orgtree.ledger import USER                                  # noqa: E402

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
    "codex_decide": codex_decide.decide,
    "codex_nothing_ran": codex_decide.nothing_ran,
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
                           "lens", "recorded", "codex", "agy", "ran_as", "cli",
                           "phase"}
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


NOW = 1_800_000_000.0
USAGE_ERR = {"message": "m", "codexErrorInfo": "usageLimitExceeded"}
UNNAMED_EXHAUSTED = {"codex": {"limitId": "codex", "limitName": None,
                               "primary": {"usedPercent": 100,
                                           "resetsAt": NOW + 2000}}}
RES, PLAN = codex_route.RESERVE_POOL, codex_route.PLAN_POOL


def _board(limits: list, *, complete: bool = True, available: bool = True,
           stale: bool = False) -> dict:
    return {"available": available, "stale": stale, "complete": complete,
            "limits": limits}


def _win(pool: str, percent: float, resets_at: float | None = None,
         observed_at: float = NOW - 10) -> dict:
    return {"model": codex_route.RESERVE_MODEL if pool == RES else "",
            "percent": percent, "is_active": percent >= 100,
            "resets_at": resets_at, "observed_at": observed_at}


def _kw(**over) -> dict:
    base = dict(status="failed", error=USAGE_ERR, snapshots=None,
                items_seen=0, token_usage=None, agent_text="", pool=RES,
                board=None, usage_prose=False, served="<sent>", now=NOW)
    base.update(over)
    return base


def _site_projection(kw: dict, ev: dict, fc: dict) -> dict:
    """The fixture the supervisor's codex site builds from the same
    evidence and decision (the real site is exercised in §10)."""
    return failfix.build(
        lane="codex", site="codex",
        observed={"started": kw["items_seen"] > 0,
                  "boundary": kw["status"] is not None},
        text={"err_blob": ""}, recorded={},
        codex={"status": kw["status"], "rpc_code": None,
               "error_code": ev["code"], "items_seen": kw["items_seen"],
               "had_usage": kw["token_usage"] is not None,
               "text_len": len(kw["agent_text"].strip()),
               "pool": kw["pool"], "served": kw["served"],
               "usage_prose": kw["usage_prose"], "now": int(NOW),
               **{k: ev[k] for k in ("snap_exhausted", "snap_reset",
                                     "board_fresh", "board_complete",
                                     "cap_state", "cap_reset")},
               "kind_recorded": fc["kind"], "rejected_recorded": fc["rejected"],
               "attributed_recorded": fc["attributed"],
               "redrive_recorded": fc["redrive"],
               "pool_state_recorded": fc["pool_state"],
               "reset_recorded": fc["reset_ts"]})


def _decision(fc: dict) -> dict:
    return {"kind": fc["kind"], "rejected": fc["rejected"],
            "attributed": fc["attributed"], "redrive": fc["redrive"],
            "pool_state": fc["pool_state"],
            "reset_ts": failfix._epoch(fc["reset_ts"])}


def sec_codex_core() -> None:
    print("\n§8  the codex decision core — reuse, oracle, projection, drift")

    def _reuse() -> None:
        assert codex_route.decide is codex_decide.decide
        assert codex_route._error_code is codex_decide.error_code
        assert codex_route.KIND_USAGE_LIMIT == codex_decide.KIND_USAGE_LIMIT
        src = open(os.path.join(HERE, "..", "orgtree", "codex_decide.py"),
                   encoding="utf-8").read()
        mods: set[str] = set()
        for n in ast.parse(src).body:
            if isinstance(n, ast.Import):
                mods |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add(n.module or "")
        assert mods <= {"typing", "__future__"}, mods
        rsrc = open(os.path.join(HERE, "..", "orgtree", "codex_route.py"),
                    encoding="utf-8").read()
        fn = next(n for n in ast.parse(rsrc).body
                  if isinstance(n, ast.FunctionDef) and n.name == "classify_failure")
        body = ast.get_source_segment(rsrc, fn) or ""
        assert "return decide(failure_evidence(" in body, body[-300:]
        # the decision does not depend on anything but Evidence: two calls
        # on equal evidence agree, and the wrapper equals the two steps
        kw = _kw(snapshots=UNNAMED_EXHAUSTED)
        assert codex_route.classify_failure(**kw) == codex_decide.decide(
            codex_route.failure_evidence(**kw))
    check("reuse · codex_route.decide IS codex_decide.decide, which imports only "
          "typing; classify_failure is decide(failure_evidence(...)) by source",
          _reuse)

    # an INDEPENDENT oracle of the documented rules (docstring of `decide`,
    # parent review 2026-09-05): (kwargs) → expected decision fields
    ORACLE = [
        ("snapshot exhausted, sent=served reserve: rejected, re-driven, reserve's wall",
         _kw(snapshots=UNNAMED_EXHAUSTED),
         {"kind": "usage-limit", "rejected": True, "attributed": RES,
          "redrive": True, "pool_state": "exhausted", "reset_ts": int(NOW + 2000)}),
        ("served plan (known reroute): plan's wall, no re-drive",
         _kw(snapshots=UNNAMED_EXHAUSTED, served=PLAN),
         {"rejected": True, "attributed": PLAN, "redrive": False,
          "pool_state": "exhausted", "reset_ts": int(NOW + 2000)}),
        ("unknown destination: rejected, attributed to nothing, no re-drive",
         _kw(snapshots=UNNAMED_EXHAUSTED, served=None),
         {"rejected": True, "attributed": None, "redrive": False,
          "pool_state": "unattributed", "reset_ts": None}),
        ("no snapshot, fresh board with reserve exhausted: board's reset",
         _kw(board=_board([_win(RES, 100.0, NOW + 500)])),
         {"pool_state": "exhausted", "reset_ts": int(NOW + 500), "redrive": True}),
        ("no snapshot, fresh COMPLETE board without reserve: no-grant",
         _kw(board=_board([_win(PLAN, 10.0)])),
         {"pool_state": "no-grant", "reset_ts": None, "rejected": True}),
        ("no snapshot, fresh INCOMPLETE board without reserve: unexplained",
         _kw(board=_board([_win(PLAN, 10.0)], complete=False)),
         {"pool_state": "unexplained", "reset_ts": None}),
        ("no snapshot, a stale board: unexplained",
         _kw(board=_board([_win(RES, 100.0, NOW + 500)], stale=True)),
         {"pool_state": "unexplained", "reset_ts": None}),
        ("no snapshot, an OLD reserve window (aged past EVIDENCE_MAX_AGE at now): unexplained",
         _kw(board=_board([_win(RES, 100.0, NOW + 500, observed_at=NOW - 5000)])),
         {"pool_state": "unexplained"}),
        ("an item was seen: the usage tag stands, but nothing is rejected or re-driven",
         _kw(snapshots=UNNAMED_EXHAUSTED, items_seen=1),
         {"kind": "usage-limit", "rejected": False, "redrive": False,
          "pool_state": "exhausted"}),
        ("token usage was seen: not rejected",
         _kw(snapshots=UNNAMED_EXHAUSTED, token_usage={"in": 1}),
         {"rejected": False}),
        ("non-blank text was seen: not rejected",
         _kw(snapshots=UNNAMED_EXHAUSTED, agent_text=" x "),
         {"rejected": False}),
        ("BLANK text is nothing run: rejected (the stripped length decides)",
         _kw(snapshots=UNNAMED_EXHAUSTED, agent_text="   \n"),
         {"rejected": True, "redrive": True}),
        ("an INTERRUPTED turn carrying the usage tag is not a terminal rejection",
         _kw(status="interrupted", snapshots=UNNAMED_EXHAUSTED),
         {"kind": "usage-limit", "rejected": False, "pool_state": "exhausted"}),
        ("status None (timeout): unknown, nothing attributed",
         _kw(status=None), {"kind": "unknown", "rejected": False,
                            "pool_state": "n/a"}),
        ("a rate-limit tag: rate-limit, not a rejection",
         _kw(error={"codexErrorInfo": "rateLimitExceeded"}),
         {"kind": "rate-limit", "rejected": False, "pool_state": "n/a"}),
        ("an auth tag (object form): auth",
         _kw(error={"codexErrorInfo": {"unauthorized": {}}}),
         {"kind": "auth", "rejected": False}),
        ("a connection tag: connection",
         _kw(error={"codexErrorInfo": "http_connection_failed"}),
         {"kind": "connection"}),
        ("an unknown machine tag: other",
         _kw(error={"codexErrorInfo": "sandbox_error"}),
         {"kind": "other", "rejected": False}),
        ("no tag, prose says limit: usage-limit-prose, never rejected",
         _kw(error={"message": "m"}, usage_prose=True),
         {"kind": "usage-limit-prose", "rejected": False}),
        ("no tag, failed, no prose: unknown",
         _kw(error={"message": "m"}), {"kind": "unknown"}),
        ("no tag, completed: other",
         _kw(error=None, status="completed"), {"kind": "other"}),
        ("pool PLAN is a pool (sent plan, served plan): plan's wall, re-driven",
         _kw(snapshots=UNNAMED_EXHAUSTED, pool=PLAN),
         {"attributed": PLAN, "redrive": True, "pool_state": "exhausted"}),
    ]

    def _oracle() -> None:
        for label, kw, want in ORACLE:
            fc = codex_route.classify_failure(**kw)
            got = _decision(fc)
            for k, v in want.items():
                assert got[k] == v, f"{label}: {k} got {got[k]!r} want {v!r} ({fc['why']})"
    check(f"oracle · {len(ORACLE)} documented rules hold on classify_failure "
          "(board / snapshot / reroute / nothing-ran / kinds)", _oracle)

    def _projection_replays() -> None:
        n = 0
        for label, kw, _ in ORACLE:
            ev = codex_route.failure_evidence(**kw)
            fc = codex_route.classify_failure(**kw)
            fx = _site_projection(kw, ev, fc)
            r = failfix.replay(fx, PREDICATES)
            assert r["drift"] == [], (label, r["drift"], r["recomputed"]["codex"])
            assert r["recomputed"]["codex"] == _decision(fc), (label, r)
            n += 1
        fx = _site_projection(_kw(pool=PLAN), codex_route.failure_evidence(**_kw(pool=PLAN)),
                              codex_route.classify_failure(**_kw(pool=PLAN)))
        assert fx["codex"]["pool"] == PLAN and fx["codex"]["attributed_recorded"] == PLAN, fx["codex"]
        # typed evidence is kept typed: a reset is an int of seconds, a
        # digit-string reset or a bool is nothing
        ev = codex_route.failure_evidence(**_kw(snapshots=UNNAMED_EXHAUSTED))
        assert isinstance(ev["snap_reset"], float)
        assert fx["codex"]["snap_reset"] is None
        bad = failfix.build(lane="codex", site="codex", observed={}, text={}, recorded={},
                            codex={"snap_reset": "1800000002000", "cap_reset": True,
                                   "cap_state": "privateclientname",
                                   "pool_state_recorded": "internalpassword",
                                   "attributed_recorded": "direct"})
        c = bad["codex"]
        assert c["snap_reset"] is None and c["cap_reset"] is None, c
        assert c["cap_state"] == "other" and c["pool_state_recorded"] == "other", c
        assert c["attributed_recorded"] == "other", c
    check("projection · every oracle case, projected as the site projects it, "
          "re-decides offline to the production decision with no drift; PLAN "
          "is a pool; resets are ints, unknowns are 'other'", _projection_replays)

    def _drift_controls() -> None:
        kw = _kw(snapshots=UNNAMED_EXHAUSTED)
        ev, fc = codex_route.failure_evidence(**kw), codex_route.classify_failure(**kw)
        fx = _site_projection(kw, ev, fc)
        wrong = copy.deepcopy(fx)
        wrong["codex"]["kind_recorded"] = "other"
        wrong["codex"]["redrive_recorded"] = False
        d = failfix.replay(wrong, PREDICATES)["drift"]
        assert "codex.kind" in d and "codex.redrive" in d, d
        # the recorded decision is NOT an input: change the EVIDENCE and the
        # recomputed decision moves while the recorded one stays
        moved = copy.deepcopy(fx)
        moved["codex"]["snap_exhausted"] = False
        r = failfix.replay(moved, PREDICATES)
        assert r["recomputed"]["codex"]["pool_state"] == "unexplained", r["recomputed"]
        assert "codex.pool_state" in r["drift"] and "codex.reset_ts" in r["drift"], r["drift"]
        moved["codex"]["items_seen"] = 2
        r = failfix.replay(moved, PREDICATES)
        assert r["recomputed"]["codex"]["rejected"] is False and "codex.rejected" in r["drift"]
    check("controls · a wrong recorded decision drifts; changed evidence moves "
          "the recomputed decision, not the recorded one", _drift_controls)


AGY_WALL = ("Individual quota reached. Please upgrade your subscription to "
            "increase your limits. Resets in 165h21m54s.")
AGY_WALL_S = 165 * 3600 + 21 * 60 + 54


def _agy_fx(blob: str, **agy) -> dict:
    base = {"status": "failed", "items": 0, "had_usage": True,
            "reset_in_s": None, "elapsed_s": 3, "ceiling_s": 1800,
            "walled_recorded": False, "reset_known_recorded": False,
            "schedule_recorded": "probe", "ceiling_kill_recorded": False}
    base.update(agy)
    return failfix.build(lane="antigravity", site="antigravity",
                         observed={"boundary": True, "is_error": True},
                         text={"err_blob": blob, "result_detail": blob},
                         recorded={"limit": failclass._looks_like_usage_limit(blob)},
                         agy=base)


def sec_agy_pure() -> None:
    print("\n§9  the antigravity lane, pure")

    def _wall() -> None:
        fx = _agy_fx(AGY_WALL, reset_in_s=float(AGY_WALL_S), walled_recorded=True,
                     reset_known_recorded=True, schedule_recorded="observed-deadline")
        assert fx["lane"] == "antigravity" and fx["site"] == "antigravity"
        a = fx["agy"]
        assert a["status"] == "failed" and a["reset_in_s"] == AGY_WALL_S, a
        assert {"quota", "reached", "resets", "limit"} <= set(fx["features"]["err_blob"]["limit"])
        leaves = " ".join(_every_leaf(fx, [])).lower()
        for word in ("individual", "upgrade", "subscription", "increase"):
            assert word not in leaves, (word, leaves[:300])
        r = failfix.replay(fx, PREDICATES)
        assert r["drift"] == [], r
        assert r["recomputed"]["agy"] == {"walled": True, "reset_known": True,
                                          "schedule": "observed-deadline",
                                          "ceiling_kill": False}, r["recomputed"]
        assert fx["phase"] == "unknown", "no typed status on this lane: not admission"
    check("wall · the measured sentence becomes limit features + a typed reset; "
          "the prose is gone; replay recomputes walled/reset/schedule; phase unknown", _wall)

    def _no_reset() -> None:
        fx = _agy_fx(AGY_WALL.split(" Resets")[0], walled_recorded=True)
        r = failfix.replay(fx, PREDICATES)
        assert r["drift"] == [] and r["recomputed"]["agy"]["schedule"] == "probe", r
    check("wall without a reset · probe schedule, no drift", _no_reset)

    def _ceiling() -> None:
        fx = _agy_fx("the sandbox denied a write", elapsed_s=1801,
                     ceiling_kill_recorded=True)
        r = failfix.replay(fx, PREDICATES)
        assert r["drift"] == [] and r["recomputed"]["agy"]["ceiling_kill"] is True, r
        # a WALL past the ceiling is the wall, not a timeout (main, 2deb7d7)
        fx = _agy_fx(AGY_WALL, elapsed_s=1801, reset_in_s=AGY_WALL_S,
                     walled_recorded=True, reset_known_recorded=True,
                     schedule_recorded="observed-deadline")
        r = failfix.replay(fx, PREDICATES)
        assert r["drift"] == [] and r["recomputed"]["agy"]["ceiling_kill"] is False, r
        # unknown timing → no ceiling verdict
        fx = _agy_fx("the sandbox denied a write", elapsed_s=None, ceiling_s=None)
        assert failfix.replay(fx, PREDICATES)["recomputed"]["agy"]["ceiling_kill"] is False
    check("ceiling · past the ceiling without a wall is a kill; with a wall it is "
          "the wall; unknown timing is no kill", _ceiling)

    def _controls() -> None:
        fx = _agy_fx(AGY_WALL, reset_in_s=AGY_WALL_S)     # recorded: not walled
        d = failfix.replay(fx, PREDICATES)["drift"]
        assert {"agy.walled", "agy.reset_known", "agy.schedule"} <= set(d), d
        fx = _agy_fx("the sandbox denied a write", elapsed_s=1801)
        assert failfix.replay(fx, PREDICATES)["drift"] == ["agy.ceiling_kill"]
        bad = _agy_fx(CANARIES[0], status=IDENT_CANARIES[0], reset_in_s="595314",
                      elapsed_s=3.5, schedule_recorded=IDENT_CANARIES[1])
        a = bad["agy"]
        assert a["status"] == "other" and a["reset_in_s"] is None and a["elapsed_s"] is None
        assert a["schedule_recorded"] == "other", a
        _assert_no_canary(bad)
    check("controls · wrong recorded walled/ceiling drift; leaves validated, "
          "canaries vanish", _controls)


def _mkorg(label: str, tier: str) -> tuple[str, str]:
    org = store.create_org(f"zz failfix {label}")
    r = org.hire(USER, None, tier, 2, "px", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a failure-fixture test agent")
    store.save_org(org)
    return org.d["slug"], r["node"]


def sec_real_codex_agy() -> dict:
    print("\n§10 real codex / antigravity failures through the fake CLIs")
    from orgtree import codex_limits
    out: dict = {}

    os.environ["FAKECODEX_SCENARIO"] = "usage_limit"
    slug, nid = _mkorg("codexwall", "sol")
    codex_limits.invalidate()
    rig.run_turn(slug, nid, "do the thing " + CANARIES[1])

    def _codex_wall() -> None:
        n = rig.node(slug, nid)
        fz = n.get("frozen")
        fixture(isinstance(fz, dict) and fz.get("limit") is True,
                f"the codex wall did not freeze — lane behaviour changed: {fz!r}")
        assert fz.get("schedule_kind") == "observed-deadline", fz
        fx = _last_fixture(slug, nid)
        out["codex"] = fx
        out["codex_path"] = failfix.list_fixtures(store.DATA_ROOT, slug, nid)[-1]
        assert fx["lane"] == "codex" and fx["site"] == "codex", fx
        c = fx["codex"]
        assert c["status"] == "failed" and c["error_code"] == "usagelimitexceeded", c
        # `served` is the pool that answered — the site resolves it to a
        # NAME (served_pool), never the "<sent>" default
        assert c["pool"] == PLAN and c["served"] == PLAN, c
        # the stand-in emits its preamble items BEFORE the wall (measured
        # here: items seen, text seen), so this is a wall AFTER output —
        # the usage tag stands, nothing is rejected or re-driven
        ran_nothing = codex_decide.nothing_ran(
            items_seen=c["items_seen"], had_usage=c["had_usage"],
            text_len=c["text_len"])
        assert c["snap_exhausted"] is True and isinstance(c["snap_reset"], int), c
        assert (c["kind_recorded"], c["rejected_recorded"], c["redrive_recorded"],
                c["attributed_recorded"], c["pool_state_recorded"]) == \
            ("usage-limit", ran_nothing, ran_nothing, PLAN, "exhausted"), c
        assert c["reset_recorded"] == c["snap_reset"], c
        assert fx["phase"] == ("admission" if c["rejected_recorded"] else "unknown"), fx
        assert "hit your" in fx["features"]["err_blob"]["limit"], fx["features"]
        r = failfix.replay(fx, PREDICATES)
        assert r["drift"] == [] and r["recomputed"]["codex"] == {
            "kind": "usage-limit", "rejected": ran_nothing, "attributed": PLAN,
            "redrive": ran_nothing, "pool_state": "exhausted",
            "reset_ts": c["snap_reset"]}, r
        _assert_no_canary(fx)
        leaves = " ".join(_every_leaf(fx, [])).lower()
        assert "visit" not in leaves and "://" not in leaves, leaves[:300]
    check("codex wall · froze exactly as before; the fixture carries the typed "
          "evidence (plan pool, snapshot exhausted, int reset) and the recorded "
          "decision; replay re-decides it; no prose, no canary", _codex_wall)

    os.environ["FAKECODEX_SCENARIO"] = "plain_failure"
    slug2, nid2 = _mkorg("codexplain", "sol")
    codex_limits.invalidate()
    rig.run_turn(slug2, nid2, "other thing " + CANARIES[2])

    def _codex_plain() -> None:
        n = rig.node(slug2, nid2)
        assert not n.get("frozen"), "a plain failure must not freeze"
        fx = _last_fixture(slug2, nid2)
        c = fx["codex"]
        assert c["error_code"] == "other" and c["kind_recorded"] == "other", c
        assert not c["rejected_recorded"] and c["pool_state_recorded"] == "n/a", c
        assert fx["phase"] == "unknown", fx["phase"]
        r = failfix.replay(fx, PREDICATES)
        assert r["drift"] == [] and r["recomputed"]["codex"]["kind"] == "other", r
        leaves = " ".join(_every_leaf(fx, [])).lower()
        assert "sandbox" not in leaves and "denied" not in leaves, leaves[:300]
        _assert_no_canary(fx)
    check("codex plain failure · not frozen; kind other, no rejection; the "
          "error sentence is not in the fixture; replay agrees", _codex_plain)

    def _codex_controls() -> None:
        fixture("codex" in out, "no codex wall fixture")
        wrong = copy.deepcopy(out["codex"])
        wrong["codex"]["kind_recorded"] = "other"
        assert "codex.kind" in failfix.replay(wrong, PREDICATES)["drift"]
        moved = copy.deepcopy(out["codex"])
        moved["codex"]["snap_exhausted"] = False        # both evidence sources
        moved["codex"]["board_fresh"] = False           # gone: unexplained
        r = failfix.replay(moved, PREDICATES)
        assert r["recomputed"]["codex"]["pool_state"] == "unexplained" and \
            "codex.pool_state" in r["drift"], r
        # with the board still fresh and exhausted, the board explains it
        moved["codex"]["board_fresh"] = True
        r = failfix.replay(moved, PREDICATES)
        assert r["recomputed"]["codex"]["pool_state"] == "exhausted" and \
            r["recomputed"]["codex"]["reset_ts"] == moved["codex"]["cap_reset"], r
        # the recomputed KIND follows the EVIDENCE, never the recorded verdict
        # (review 2026-09-05: `kind_recorded = "other"` above is the one
        # direction a replay peeking at its own answer would still drift on).
        # Re-key the machine tag on the real fixture, leave the recorded
        # usage-limit verdict in place: the re-decision must move with the
        # tag and the recorded kind must drift against it
        rekeyed = copy.deepcopy(out["codex"])
        assert rekeyed["codex"]["kind_recorded"] == codex_decide.KIND_USAGE_LIMIT
        rekeyed["codex"]["error_code"] = "unauthorized"
        r = failfix.replay(rekeyed, PREDICATES)
        assert r["recomputed"]["codex"]["kind"] == codex_decide.KIND_AUTH, r
        assert r["recomputed"]["codex"]["pool_state"] == "n/a" and \
            r["recomputed"]["codex"]["reset_ts"] is None, r
        assert {"codex.kind", "codex.pool_state", "codex.reset_ts"} <= set(r["drift"]), r
    check("codex controls · on the REAL fixture a wrong recorded kind drifts; "
          "removing the snapshot AND the board moves pool_state to unexplained; "
          "the board alone explains it; a re-keyed machine tag re-decides to "
          "auth against the recorded usage-limit", _codex_controls)

    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "usage_limit"
    os.environ.pop("FAKEANTIGRAVITY_RESET_IN", None)
    slug3, nid3 = _mkorg("agywall", "pro")
    rig.run_turn(slug3, nid3, "antigravity thing " + IDENT_CANARIES[2])

    def _agy_wall() -> None:
        n = rig.node(slug3, nid3)
        fz = n.get("frozen")
        fixture(isinstance(fz, dict) and fz.get("limit") is True,
                f"the antigravity wall did not freeze — lane behaviour changed: {fz!r}")
        assert (fz.get("schedule_kind"), fz.get("reset_src")) == ("observed-deadline", "provider"), fz
        fx = _last_fixture(slug3, nid3)
        out["agy"] = fx
        out["agy_path"] = failfix.list_fixtures(store.DATA_ROOT, slug3, nid3)[-1]
        assert fx["lane"] == "antigravity" and fx["site"] == "antigravity", fx
        a = fx["agy"]
        assert a["status"] == "failed" and a["items"] == 0, a
        assert a["reset_in_s"] == AGY_WALL_S, a
        assert isinstance(a["elapsed_s"], int) and isinstance(a["ceiling_s"], int), a
        assert a["elapsed_s"] < a["ceiling_s"], a
        assert (a["walled_recorded"], a["reset_known_recorded"], a["schedule_recorded"],
                a["ceiling_kill_recorded"]) == (True, True, "observed-deadline", False), a
        o = fx["observed"]
        assert not o["started"] and o["boundary"] and o["is_error"], o
        assert fx["phase"] == "unknown", fx["phase"]
        assert {"quota", "reached", "resets"} <= set(fx["features"]["err_blob"]["limit"])
        r = failfix.replay(fx, PREDICATES)
        assert r["drift"] == [] and r["recomputed"]["agy"]["walled"] is True, r
        leaves = " ".join(_every_leaf(fx, [])).lower()
        for word in ("individual", "upgrade", "subscription"):
            assert word not in leaves, (word, leaves[:300])
        _assert_no_canary(fx)
    check("antigravity wall · froze exactly as before (provider reset); the "
          "fixture carries status, typed reset, timing and the recorded "
          "decisions; replay agrees; prose and canaries absent", _agy_wall)

    os.environ["FAKEANTIGRAVITY_RESET_IN"] = ""
    slug4, nid4 = _mkorg("agyprobe", "pro")
    rig.run_turn(slug4, nid4, "antigravity again")
    os.environ.pop("FAKEANTIGRAVITY_RESET_IN", None)

    def _agy_probe() -> None:
        n = rig.node(slug4, nid4)
        fz = n.get("frozen")
        fixture(isinstance(fz, dict) and fz.get("limit") is True, f"no freeze: {fz!r}")
        assert fz.get("schedule_kind") == "probe", fz
        a = _last_fixture(slug4, nid4)["agy"]
        assert a["reset_in_s"] is None and not a["reset_known_recorded"], a
        assert a["schedule_recorded"] == "probe" and a["walled_recorded"], a
        r = failfix.replay(_last_fixture(slug4, nid4), PREDICATES)
        assert r["drift"] == [] and r["recomputed"]["agy"]["schedule"] == "probe", r
    check("antigravity wall naming no reset · probe schedule recorded and "
          "recomputed, no drift", _agy_probe)

    def _agy_control() -> None:
        fixture("agy" in out, "no antigravity fixture")
        wrong = copy.deepcopy(out["agy"])
        wrong["agy"]["walled_recorded"] = False
        assert "agy.walled" in failfix.replay(wrong, PREDICATES)["drift"]
    check("antigravity control · a wrong recorded wall drifts on the real fixture",
          _agy_control)

    # The NOT-walled branch through the real leg (review 2026-09-05: both
    # scenarios above are walls, so `ceiling_kill_recorded` could sit
    # permanently False and replay's `walled and` gate on reset_known could
    # be dropped, and nothing would notice). A failure that is not a wall,
    # past the ceiling: the stand-in stalls BEFORE init (inside start()'s
    # own INIT_TIMEOUT, so wait() does not time the result out) and then
    # reports an error whose sentence has no "limit" in it but DOES name a
    # reset duration — the one input that tells a gated reset_known from an
    # ungated one. The ceiling is patched to 1s for this turn only.
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "plain_error"
    os.environ["FAKEANTIGRAVITY_ERROR"] = ("Internal error: the model returned "
                                          "no response. Resets in 3h0m0s.")
    os.environ["FAKEANTIGRAVITY_INIT_DELAY"] = "1.5"
    _real_ceiling = supervisor.TURN_TIMEOUT
    supervisor.TURN_TIMEOUT = 1
    try:
        slug5, nid5 = _mkorg("agyceiling", "pro")
        rig.run_turn(slug5, nid5, "antigravity slowly " + CANARIES[0])
    finally:
        supervisor.TURN_TIMEOUT = _real_ceiling
        os.environ.pop("FAKEANTIGRAVITY_ERROR", None)
        os.environ.pop("FAKEANTIGRAVITY_INIT_DELAY", None)

    def _agy_ceiling() -> None:
        n = rig.node(slug5, nid5)
        assert not n.get("frozen"), f"a non-wall failure must not freeze: {n.get('frozen')!r}"
        rows = (store.load_org(slug5).d.get("turn_error_log") or {}).get(nid5) or []
        fixture(bool(rows), "no turn_error_log row — the failure left no trace")
        # the production raise that ran: the ceiling, not the CLI's sentence
        assert "per-message ceiling" in rows[-1]["text"], rows[-1]
        fx = _last_fixture(slug5, nid5)
        assert fx["lane"] == "antigravity" and fx["site"] == "antigravity", fx
        a = fx["agy"]
        assert a["status"] == "failed" and a["items"] == 0, a
        # the duration IS carried, as evidence …
        assert a["reset_in_s"] == 3 * 3600, a
        assert a["ceiling_s"] == 1 and a["elapsed_s"] >= a["ceiling_s"], a
        # … but only a WALL makes it a known reset: not walled, not known,
        # probe schedule, and the ceiling kill is what the site decided
        assert (a["walled_recorded"], a["reset_known_recorded"], a["schedule_recorded"],
                a["ceiling_kill_recorded"]) == (False, False, "probe", True), a
        assert fx["recorded"]["limit"] is False, fx["recorded"]
        r = failfix.replay(fx, PREDICATES)
        assert r["drift"] == [] and r["recomputed"]["agy"] == {
            "walled": False, "reset_known": False, "schedule": "probe",
            "ceiling_kill": True}, r
        # the controls on THIS fixture: each recorded decision, flipped, drifts
        for key, name in (("reset_known_recorded", "agy.reset_known"),
                          ("ceiling_kill_recorded", "agy.ceiling_kill")):
            wrong = copy.deepcopy(fx)
            wrong["agy"][key] = not wrong["agy"][key]
            assert name in failfix.replay(wrong, PREDICATES)["drift"], (key, name)
        # and the ceiling verdict is READ from the timing: under the ceiling
        # the same fixture recomputes no kill, and the recorded one drifts
        under = copy.deepcopy(fx)
        under["agy"]["elapsed_s"] = under["agy"]["ceiling_s"] - 1
        r = failfix.replay(under, PREDICATES)
        assert r["recomputed"]["agy"]["ceiling_kill"] is False and \
            "agy.ceiling_kill" in r["drift"], r
        leaves = " ".join(_every_leaf(fx, [])).lower()
        assert "internal error" not in leaves and "no response" not in leaves, leaves[:300]
        _assert_no_canary(fx)
    check("antigravity ceiling · a non-wall failure past the ceiling through "
          "the real leg: not frozen, the ceiling raise ran; the fixture records "
          "the named reset as evidence but not-walled / reset unknown / probe / "
          "ceiling kill; replay agrees; flipped decisions drift; the kill "
          "verdict follows the timing; prose and canary absent", _agy_ceiling)
    return out


def sec_tool_codex_agy(got: dict) -> None:
    print("\n§10b the tool over the codex / antigravity fixtures")
    tool = os.path.join(HERE, "..", "..", "tools", "replay_failure.py")
    env = dict(os.environ)
    env.pop("ORGTREE_DATA", None)

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], capture_output=True,
                              text=True, env=env, timeout=120)

    def _ok() -> None:
        p = _run(tool, got["codex_path"], got["agy_path"], "--assert")
        assert p.returncode == 0, (p.returncode, p.stdout[-800:], p.stderr[-800:])
        rows = [json.loads(ln) for ln in p.stdout.strip().splitlines()]
        assert rows[0]["recomputed"]["codex"]["kind"] == "usage-limit", rows[0]
        assert rows[1]["recomputed"]["agy"]["walled"] is True, rows[1]
    check("tool · --assert exits 0 on both; the codex decision and the "
          "antigravity wall are recomputed", _ok)

    def _pure() -> None:
        runner = os.path.join(tempfile.mkdtemp(prefix="failfix-pure-"), "run.py")
        with open(runner, "w", encoding="utf-8") as f:
            f.write(HOOK + textwrap.dedent(f'''
                import runpy
                sys.argv = [{tool!r}, {got["codex_path"]!r}, {got["agy_path"]!r}, "--assert"]
                try:
                    runpy.run_path({tool!r}, run_name="__main__")
                except SystemExit as e:
                    raise SystemExit(int(e.code or 0))
                '''))
        p = _run(runner)
        assert p.returncode == 0, (p.returncode, p.stdout[-800:], p.stderr[-1200:])
        assert "PURITY" not in p.stderr
    check("purity · re-deciding the codex fixture with the production core "
          "completes under the import hook (no codex_route/ledger/store)", _pure)


def main() -> int:
    sec_features()
    sec_leaves()
    sec_bounds()
    sec_capture()
    sec_identity()
    sec_codex_core()
    sec_agy_pure()
    if shutil.which("node"):
        got = sec_real()
        if got.get("_slug"):
            sec_tool(got)
    else:
        NOTES.append("INERT: `node` is not on PATH — §5 and §6 (real failures "
                     "through the fake CLI, the tool) DID NOT RUN")
    got2 = sec_real_codex_agy()
    if got2.get("codex_path") and got2.get("agy_path"):
        sec_tool_codex_agy(got2)
    for n in NOTES:
        print(f"\n  ! {n}")
    print(f"\n{PASSED} passed, {len(FAILED)} failed"
          + (f", {len(NOTES)} inert" if NOTES else ""))
    for f in FAILED:
        print("\n" + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
