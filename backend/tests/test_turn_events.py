"""Turn events — ordered failure timelines and turn diagnostics
(backend/orgtree/turnlog.py, tools/inspect_turn.py, docs/turn-events.md).

    python backend/tests/test_turn_events.py [--verbose] [--pure]

§1  the schema and coercion — closed vocabularies, typed leaves, canaries
§2  the recorder — order under threads, bounds while emitting, the stub,
    stale emission, ring at open AND close, the on-disk cap
§3  summarize / drift — hand-built timelines with controls; an unfinalized
    or truncated record asserts nothing; `end`/`dispose` are never copied;
    fixture-name validation and path containment
§4  the claude lane through the fake CLI — completed, died-in-flight,
    401 with planted secrets, watchdog kill, dead-on-arrival, interrupt
§5  the OpenRouter lane — the same CLI under the OR key, typed 429
§6  codex through fakecodex — completed, usage wall, plain failure
§7  antigravity through fakeantigravity — completed, wall, ceiling kill,
    interrupt
§8  fail-open — an obstructed root, a coercer that raises, a writer that
    raises: the node document is identical to the control
§9  the inspector under the purity import hook, with a biting control

Everything runs against a THROWAWAY data root the rig binds at import
(test_limit_freeze), fake transports only: no live data, no provider
traffic, no paid turn. `--pure` runs §1–§3 only (no node).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

_CHOME = tempfile.mkdtemp(prefix="turnlog-chome-")
with open(os.path.join(_CHOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')
os.environ["ORGTREE_CODEX"] = os.path.join(HERE, "fakecodex.py")
os.environ["CODEX_HOME"] = _CHOME
os.environ["ORGTREE_ANTIGRAVITY"] = os.path.join(HERE, "fakeantigravity.py")
os.environ.setdefault("ORGTREE_PORT", "9")

# the rig binds a THROWAWAY ORGTREE_DATA + HOME at import, before orgtree
import test_limit_freeze as rig                                  # noqa: E402
from orgtree import (codex_limits, failfix, ledger, openrouter,  # noqa: E402
                     store, supervisor, turnlog)
from orgtree.ledger import USER                                  # noqa: E402

assert store.DATA_ROOT.startswith(rig._TMP), store.DATA_ROOT    # throwaway root

VERBOSE = "--verbose" in sys.argv
PURE = "--pure" in sys.argv
PASSED = 0
FAILED: list[str] = []

CANARIES = ("the quarterly numbers are down twelve percent",
            "ZEBRA-OTTER-7731 said the merger closes friday",
            "patient initials JD, room 4, dosage doubled")
IDENT_CANARIES = ("privateclientname", "internalpassword", "zebraotter7731",
                  "acme_corp_prod", "mcp__privateserver__secrettool")
SECRETS = ("sk-ant-api03-PLANTEDSECRET0123456789abcdef",
           "Bearer PLANTEDBEARER.token", r"C:\Users\planted\notes.txt",
           "planted.user@example.com", "https://planted.example.com/x?k=1")
OR_TIER, OR_MODEL = "or-audit-fake", "audit/fake"
OR_429 = ('API Error: 429 {"error":{"message":"Rate limit exceeded: '
          'free-models-per-day. ' + CANARIES[1] + '"}}')


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


def _leaf_values(o, out: list) -> list:
    """Values only — keys are the closed schema (asserted below), and one of
    them (`percent`) is an English word a sentence canary also uses."""
    if isinstance(o, dict):
        for v in o.values():
            _leaf_values(v, out)
    elif isinstance(o, list):
        for v in o:
            _leaf_values(v, out)
    else:
        out.append(str(o))
    return out


KNOWN_KEYS = (set(turnlog.HEADER_FIELDS) | {
    "schema", "at", "attempt", "partial", "events", "events_n", "dropped",
    "dropped_kinds", "truncated", "outcome", "outcome_ms", "error_class",
    "fixture", "paid_booked", "cost_usd", "cost_known", "recorder_errors",
    "seq", "t_ms", "kind"} | set(turnlog.FIELDS)
    | {k for f in turnlog.FIELDS.values() for k in f})


def _keys(o, out: set) -> set:
    if isinstance(o, dict):
        for k, v in o.items():
            out.add(str(k))
            _keys(v, out)
    elif isinstance(o, list):
        for v in o:
            _keys(v, out)
    return out


def _assert_no_canary(rec: dict, extra: tuple[str, ...] = ()) -> None:
    strange = _keys(rec, set()) - KNOWN_KEYS
    assert not strange, f"keys outside the schema: {sorted(strange)[:8]}"
    leaves = " ".join(_leaf_values(rec, [])).lower()
    for canary in CANARIES:
        for word in canary.lower().split():
            if len(word) >= 5:
                assert word not in leaves, (canary, word)
    for ident in IDENT_CANARIES + extra:
        assert ident.lower() not in leaves, (ident, leaves[:400])
    for s in SECRETS:
        assert s.lower() not in leaves and "planted" not in leaves, s


def _records(slug: str, nid: str) -> list[str]:
    return [p for p in turnlog.list_records(store.DATA_ROOT, slug, nid)
            if not p.endswith(".partial.json")]


def _last(slug: str, nid: str) -> dict:
    names = _records(slug, nid)
    fixture(bool(names), f"no turn record for {slug}/{nid}")
    return turnlog.load(names[-1])


def _kinds(rec: dict) -> list[str]:
    return [e["kind"] for e in rec["events"]]


def _ev(rec: dict, kind: str, last: bool = False) -> dict:
    evs = [e for e in rec["events"] if e["kind"] == kind]
    fixture(bool(evs), f"no {kind} event: {_kinds(rec)}")
    return evs[-1] if last else evs[0]


def _ordered(rec: dict) -> None:
    seqs = [e["seq"] for e in rec["events"]]
    ts = [e["t_ms"] for e in rec["events"]]
    assert all(b > a for a, b in zip(seqs, seqs[1:])), seqs
    assert all(t >= 0 for t in ts), ts
    assert all(b >= a for a, b in zip(ts, ts[1:])), ts   # one lock, both stamps


def _before(rec: dict, a: str, b: str) -> None:
    """Event `a` (first) precedes event `b` (first)."""
    ks = _kinds(rec)
    assert a in ks and b in ks, (a, b, ks)
    assert ks.index(a) < ks.index(b), (a, b, ks)


# ══════════════════════════════════════════════════════════════════════════ §1

def sec_schema() -> None:
    print("\n§1  the schema and coercion")

    def _closed() -> None:
        for kind, spec in turnlog.FIELDS.items():
            assert kind not in ("seq", "t_ms")
            for k, fs in spec.items():
                assert k not in ("seq", "t_ms", "kind"), (kind, k)
                assert fs in (turnlog.B, turnlog.I, turnlog.F) or (
                    isinstance(fs, tuple) and fs[0] in ("S", "L")
                    and isinstance(fs[1], frozenset) and fs[1]), (kind, k)
        assert set(turnlog.TIERS) == set(ledger.TIERS), (
            set(turnlog.TIERS) ^ set(ledger.TIERS))
        assert turnlog.HEAD + turnlog.TAIL == turnlog.MAX_EVENTS
    check("every field is typed or a closed vocabulary; none can shadow "
          "seq/t_ms/kind; TIERS is the ledger's table", _closed)

    def _coerce() -> None:
        c = turnlog.coerce
        assert c(turnlog.I, 401) == 401 and c(turnlog.I, "401") is None
        assert c(turnlog.I, True) is None and c(turnlog.I, 401.0) is None
        assert c(turnlog.I, 10 ** 16) is None
        assert c(turnlog.B, 1) is False and c(turnlog.B, True) is True
        assert c(turnlog.F, 0.25) == 0.25 and c(turnlog.F, "0.25") is None
        assert c(turnlog.F, float("nan")) is None
        S = turnlog.S(frozenset({"a", "b"}))
        assert c(S, "a") == "a" and c(S, "zzz") == "other" and c(S, "") is None
        assert c(S, IDENT_CANARIES[0]) == "other"
        L = turnlog.L(frozenset({"Bash"}))
        assert c(L, ["Bash", "mcp__privateserver__secrettool", 3]) == \
            ["Bash", "other", "other"]
        assert len(c(L, ["Bash"] * 40)) == turnlog.LIST_MAX
        assert c(L, "Bash") == []
    check("coercion · strict ints (no bool, no str, no float, bounded), "
          "vocab or other, lists capped, unknown tool names → other",
          _coerce)

    def _canaries() -> None:
        r = turnlog.Recorder(store.DATA_ROOT, "o", "n", lane=CANARIES[0],
                             tier=SECRETS[0], toks="12", text_len=99)
        for kind, spec in turnlog.FIELDS.items():
            fields = {k: (CANARIES[1] if isinstance(fs, tuple) else SECRETS[1])
                      for k, fs in spec.items()}
            fields["unknown_field"] = SECRETS[2]
            fields[IDENT_CANARIES[0]] = SECRETS[3]
            assert r.emit(kind, **fields)
        r.emit("assistant", tools=[IDENT_CANARIES[4], "Bash"], text_n=3)
        r.dispose(CANARIES[2])
        r.fixture(SECRETS[2])
        r.error(RuntimeError(SECRETS[0] + CANARIES[0]))
        rec = r._record(partial=False, outcome=r.disposition, outcome_ms=1,
                        error_class=r._error_class, paid_booked=None,
                        cost_usd=None)
        _assert_no_canary(rec)
        assert rec["lane"] == "other" and rec["tier"] == "other"
        assert rec["toks"] is None and rec["text_len"] == 99
        assert rec["outcome"] == "unknown" and rec["fixture"] is None
        assert rec["error_class"] == "RuntimeError"
        asst = [e for e in rec["events"] if e["kind"] == "assistant"][-1]
        assert asst["tools"] == ["other", "Bash"] and asst["text_n"] == 3
        assert "unknown_field" not in json.dumps(rec)
    check("canaries · every field of every kind, the header, dispose, "
          "fixture and error fed sentences/secrets/identifiers: none "
          "survives; the builtin tool name, the count and the class do",
          _canaries)

    def _shapes() -> None:
        ev = {"type": "assistant", "message": {"model": "<synthetic>",
              "content": [{"type": "text", "text": CANARIES[0]},
                          {"type": "tool_use", "name": "Bash",
                           "input": {"command": SECRETS[0]}},
                          {"type": "tool_use", "name": IDENT_CANARIES[4],
                           "input": {}},
                          {"type": "thinking", "thinking": CANARIES[1]}]},
              "isApiErrorMessage": True}
        s = turnlog.assistant_shape(ev)
        assert s == {"text_n": 1, "tool_n": 2, "thinking": True,
                     "synthetic": True, "api_error": True,
                     "tools": ["Bash", IDENT_CANARIES[4]]}, s
        # the raw name passes through the SHAPE and dies at the vocabulary
        r = turnlog.Recorder(store.DATA_ROOT, "o", "n")
        r.emit("assistant", **s)
        assert r._head[0]["tools"] == ["Bash", "other"]
        u = {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": SECRETS[1], "is_error": True},
            {"type": "tool_result", "content": "ok"}]}}
        assert turnlog.tool_result_shape(u) == {"n": 2, "errors_n": 1}
        res = {"type": "result", "subtype": "success", "is_error": True,
               "result": CANARIES[2], "api_error_status": "401",
               "duration_ms": 12, "num_turns": 2, "errors": ["x"],
               "usage": {"input_tokens": 5, "output_tokens": 7,
                         "cache_read_input_tokens": 1},
               "total_cost_usd": 0.01}
        rs = turnlog.result_shape(res, boundary=True)
        assert rs["status"] is None and rs["result_len"] == len(CANARIES[2])
        assert (rs["in_tokens"], rs["out_tokens"], rs["cache_read"],
                rs["cache_create"], rs["cost_known"], rs["errors_n"]) == \
            (5, 7, 1, None, True, 1), rs
        assert turnlog.result_shape({**res, "api_error_status": 401},
                                    boundary=False)["status"] == 401
        ini = turnlog.init_shape({"tools": ["a", "b"], "permissionMode": "plan",
                                  "mcp_servers": [{"name": "x", "status": "failed"},
                                                  {"name": "y", "status": "connected"}]})
        assert ini == {"tools_n": 2, "mcp_n": 2, "mcp_failed_n": 1,
                       "mode": "plan"}, ini
        assert turnlog.window_of({"rateLimits": {"primary": {
            "usedPercent": 42.0, "resetsAt": 1789000000}}}) == (42, 1789000000)
        assert turnlog.window_of({"rateLimits": "junk"}) == (None, None)
        assert turnlog.window_of(None) == (None, None)
        assert turnlog.seconds_of(3.9) == 3 and turnlog.seconds_of(-1) is None
        assert turnlog.assistant_shape("junk")["tool_n"] == 0
    check("shapes · assistant/user/result/init/window helpers keep counts, "
          "flags and typed numbers, never text; a digit-string status is "
          "null; junk input never raises", _shapes)


# ══════════════════════════════════════════════════════════════════════════ §2

def _tmp_root(label: str) -> str:
    d = os.path.join(rig._TMP, "tl-" + label)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    return d


def sec_recorder() -> None:
    print("\n§2  the recorder")

    def _threads() -> None:
        root = _tmp_root("threads")
        r = turnlog.start(root, "o", "n", lane="claude")
        assert r is not None
        stubs = [n for n in os.listdir(turnlog.record_dir(root, "o", "n"))
                 if n.endswith(".partial.json")]
        assert len(stubs) == 1, stubs
        stub = turnlog.load(os.path.join(turnlog.record_dir(root, "o", "n"), stubs[0]))
        assert stub["partial"] is True and stub["events"] == [] and \
            stub["outcome"] is None and stub["lane"] == "claude"

        def worker(i: int) -> None:
            for _ in range(300):
                r.emit("assistant", text_n=i, tool_n=1)
                r.emit("tool_result", n=1)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        r.emit("result", boundary=True, is_error=False)
        p = r.close(outcome="completed", paid_booked=True, cost_usd=0.5)
        assert p and os.path.basename(p).endswith("-claude-completed.json"), p
        rec = turnlog.load(p)
        _ordered(rec)
        assert rec["events_n"] == 6 * 600 + 2, rec["events_n"]
        kept = rec["events"]
        assert len(kept) == turnlog.MAX_EVENTS
        assert kept[:turnlog.HEAD] == kept[:turnlog.HEAD]
        assert kept[-1]["kind"] == "end" and kept[-2]["kind"] == "result"
        assert kept[turnlog.HEAD - 1]["seq"] == turnlog.HEAD
        assert kept[turnlog.HEAD]["seq"] > turnlog.HEAD + 1        # a gap
        assert rec["dropped"] == rec["events_n"] - turnlog.MAX_EVENTS
        assert sum(rec["dropped_kinds"].values()) == rec["dropped"]
        assert set(rec["dropped_kinds"]) <= {"assistant", "tool_result"}
        assert rec["truncated"] is False and rec["partial"] is False
        assert rec["cost_usd"] == 0.5 and rec["cost_known"] is True
        assert not [n for n in os.listdir(os.path.dirname(p))
                    if n.endswith(".partial.json")]
        # STALE: a closed recorder drops and writes nothing more
        assert r.emit("interrupt") is False
        assert r.close() is None
        assert turnlog.load(p) == rec
        # THE STAMP IS TAKEN UNDER THE LOCK — the property the ordering rests
        # on, probed directly: every clock read an emit makes happens while
        # the recorder's lock is held. (The ordering assertions above are
        # necessary but a stamp taken just before the lock passes them most
        # of the time; this cannot pass by luck.)
        r2 = turnlog.Recorder(root, "o", "n2")
        real_mono = turnlog.time.monotonic
        seen: list[bool] = []

        def probe() -> float:
            seen.append(r2._lock.locked())
            return real_mono()
        turnlog.time.monotonic = probe
        try:
            for _ in range(50):
                r2.emit("assistant", text_n=1)
        finally:
            turnlog.time.monotonic = real_mono
        assert len(seen) == 50 and all(seen), f"clock read outside the lock: {seen.count(False)}/50"
    check("threads · six emitters, one total order (seq strictly increasing, "
          "t_ms non-decreasing); head+tail kept with a seq gap; dropped and "
          "dropped_kinds account for the rest; the stub is replaced; a closed "
          "recorder refuses emit and close; the stamp is read under the lock",
          _threads)

    def _unknown_cost() -> None:
        root = _tmp_root("cost")
        r = turnlog.start(root, "o", "n")
        r.book(paid_booked=False, cost_usd=None)
        rec = turnlog.load(r.close())
        assert rec["cost_usd"] is None and rec["cost_known"] is False
        assert rec["paid_booked"] is False and rec["outcome"] == "unknown"
        r2 = turnlog.start(root, "o", "n")
        r2.book(paid_booked=True, cost_usd=0)
        rec2 = turnlog.load(r2.close())
        assert rec2["cost_usd"] == 0.0 and rec2["cost_known"] is True
    check("cost · unknown is null with cost_known false, never 0.0; a "
          "reported zero is 0.0 with cost_known true", _unknown_cost)

    def _cap() -> None:
        root = _tmp_root("cap")
        real = turnlog.CAP_BYTES
        turnlog.CAP_BYTES = 6000
        try:
            r = turnlog.start(root, "o", "n")
            for i in range(200):
                r.emit("assistant", text_n=i, tool_n=i, tools=["Bash", "Read"])
            r.emit("result", boundary=True)
            rec = turnlog.load(r.close(outcome="completed"))
        finally:
            turnlog.CAP_BYTES = real
        assert rec["truncated"] is True and rec["dropped"] > 0
        assert rec["events"][0]["kind"] == "assistant" and \
            rec["events"][0]["seq"] == 1
        assert rec["events"][-1]["kind"] == "end"
        assert rec["events"][-2]["kind"] == "result"
        assert len(json.dumps(rec, ensure_ascii=False, indent=1).encode()) <= 6000
        _ordered(rec)
        assert turnlog.summarize(rec)["evidence"] == "insufficient"
        assert turnlog.drift(rec) == []
    check("cap · a record over CAP_BYTES is cut from the middle, keeps its "
          "first and last events, says truncated, and its summary asserts "
          "nothing", _cap)

    def _ring() -> None:
        root = _tmp_root("ring")
        d = turnlog.record_dir(root, "o", "n")
        os.makedirs(d)
        # 70 abandoned stubs from earlier attempts, plus 5 old records
        for i in range(70):
            with open(os.path.join(d, f"{1000000000000 + i:013d}-0001.partial.json"),
                      "w", encoding="utf-8") as f:
                f.write("{}")
        for i in range(5):
            with open(os.path.join(d, f"{1000000000100 + i:013d}-0001-claude-failed.json"),
                      "w", encoding="utf-8") as f:
                f.write("{}")
        with open(os.path.join(d, "unrelated.txt"), "w") as f:
            f.write("keep")
        r = turnlog.start(root, "o", "n")
        names = sorted(os.listdir(d))
        assert len([n for n in names if n.endswith(".json")]) == turnlog.RING, len(names)
        assert "unrelated.txt" in names
        assert r._stub and os.path.exists(r._stub)
        r.close(outcome="failed")
        names = [n for n in os.listdir(d) if n.endswith(".json")]
        assert len(names) == turnlog.RING and \
            not [n for n in names if n.endswith(".partial.json") and
                 n.startswith("1000000000000")], names[:3]
        assert all(n.endswith("-claude-failed.json") or n.endswith(".partial.json")
                   or n.endswith("-other-failed.json") for n in names)
    check("ring · stubs count: 75 leftovers are cut to RING at OPEN and "
          "again at close; the oldest stubs go first; unrelated files stay",
          _ring)

    def _disabled() -> None:
        os.environ["ORGTREE_TURNLOG"] = "0"
        try:
            assert turnlog.start(_tmp_root("off"), "o", "n") is None
            turnlog.emit(None, "start", slot_wait_ms=1)     # tolerated
        finally:
            os.environ.pop("ORGTREE_TURNLOG", None)
    check("off switch · ORGTREE_TURNLOG=0 opens no recorder; emit on None "
          "is a no-op", _disabled)


# ══════════════════════════════════════════════════════════════════════════ §3

def _rec(events: list[dict], **hdr) -> dict:
    evs = []
    for i, e in enumerate(events):
        evs.append({"seq": i + 1, "t_ms": i * 10, **e})
    return {"schema": 1, "partial": False, "truncated": False,
            "events": evs, "outcome": None, **hdr}


def sec_summary() -> None:
    print("\n§3  summarize / drift")

    def _frozen() -> None:
        r = _rec([{"kind": "start"}, {"kind": "first_output"},
                  {"kind": "assistant", "text_n": 1},
                  {"kind": "exit", "code": 1},
                  {"kind": "classify", "net": True},
                  {"kind": "freeze", "freeze_kind": "connection"},
                  {"kind": "owner", "branch": "net_retry", "handled": True},
                  {"kind": "owner", "branch": "terminal", "handled": True},
                  {"kind": "end", "outcome": "completed"}], outcome="frozen")
        s = turnlog.summarize(r)
        assert (s["phase"], s["implied"], s["evidence"]) == \
            ("stream", "frozen", "sufficient"), s
        assert s["first_output_ms"] == 10 and s["boundary_ms"] is None
        assert turnlog.drift(r) == []
        # a HANDLED terminal owner does not override the freeze; an
        # unhandled one is a failure
        r2 = copy.deepcopy(r)
        r2["events"][7]["handled"] = False
        assert turnlog.summarize(r2)["implied"] == "failed"
        assert turnlog.drift(r2) == ["outcome"]
    check("frozen · exit after output with no boundary is phase stream; a "
          "connection freeze under a handled terminal owner implies frozen; "
          "the recorded outcome agrees; an unhandled owner drifts", _frozen)

    def _not_copied() -> None:
        r = _rec([{"kind": "start"}, {"kind": "dispose", "outcome": "frozen"},
                  {"kind": "end", "outcome": "frozen"}], outcome="frozen")
        s = turnlog.summarize(r)
        assert s["implied"] == "unknown" and s["evidence"] == "sufficient", s
        assert turnlog.drift(r) == []
        r["events"].insert(1, {"seq": 99, "t_ms": 5, "kind": "freeze",
                               "freeze_kind": "limit"})
        for i, e in enumerate(r["events"]):
            e["seq"] = i + 1
        assert turnlog.summarize(r)["implied"] == "frozen"
    check("never copied · dispose/end alone imply nothing (unknown, no "
          "drift); a freeze event is what implies frozen", _not_copied)

    def _kill_precedence() -> None:
        r = _rec([{"kind": "start"}, {"kind": "watchdog", "why": "idle"},
                  {"kind": "abandon", "door": "killed"}], outcome="killed")
        assert turnlog.summarize(r)["implied"] == "killed"
        assert turnlog.drift(r) == []
        r2 = _rec([{"kind": "start"}, {"kind": "exit", "code": 1},
                   {"kind": "owner", "branch": "terminal", "handled": False},
                   {"kind": "abandon", "door": "pre_model"}], outcome="abandoned")
        assert turnlog.summarize(r2)["implied"] == "abandoned"
        r3 = _rec([{"kind": "start"}, {"kind": "watchdog", "why": "idle"},
                   {"kind": "freeze", "freeze_kind": "limit"}], outcome="frozen")
        assert turnlog.summarize(r3)["implied"] == "frozen", "a later freeze wins"
    check("precedence · kill then abandon stays killed; terminal then "
          "abandon is abandoned; a watchdog followed by a freeze is frozen "
          "(a watchdog event is not the outcome by itself)", _kill_precedence)

    def _phases() -> None:
        adm = _rec([{"kind": "start"},
                    {"kind": "result", "boundary": True, "is_error": True,
                     "status": 401}, {"kind": "exit", "code": 1}])
        assert turnlog.summarize(adm)["phase"] == "admission"
        adm["events"][1]["status"] = 403
        assert turnlog.summarize(adm)["phase"] == "unknown"
        rerr = _rec([{"kind": "start"}, {"kind": "assistant"},
                     {"kind": "result", "boundary": True, "is_error": True},
                     {"kind": "exit", "code": 1}])
        assert turnlog.summarize(rerr)["phase"] == "result-error"
        td = _rec([{"kind": "start"}, {"kind": "assistant"},
                   {"kind": "result", "boundary": True, "is_error": False},
                   {"kind": "exit", "code": 1}])
        assert turnlog.summarize(td)["phase"] == "teardown"
        ok = _rec([{"kind": "start"}, {"kind": "assistant"},
                   {"kind": "result", "boundary": True, "is_error": False},
                   {"kind": "exit", "code": 0}], outcome="completed")
        s = turnlog.summarize(ok)
        assert s["phase"] == "unknown" and s["implied"] == "completed"
        strag = _rec([{"kind": "start"}, {"kind": "assistant"},
                      {"kind": "result", "boundary": False, "is_error": True},
                      {"kind": "exit", "code": 1}])
        assert turnlog.summarize(strag)["phase"] == "stream", "a straggler is not a boundary"
        cdx = _rec([{"kind": "start"}, {"kind": "codex_status", "status": "failed"},
                    {"kind": "codex_decide", "decision": "usage-limit", "rejected": True}])
        assert turnlog.summarize(cdx)["phase"] == "admission"
        assert turnlog.summarize(cdx)["implied"] == "failed"
    check("phases · failfix's table from the events: typed 401 refusal with "
          "nothing output is admission, 403 is not; error after output is "
          "result-error; clean boundary and nonzero exit is teardown; a "
          "straggler is no boundary; a codex rejection is admission", _phases)

    def _insufficient() -> None:
        for hdr in ({"partial": True}, {"truncated": True}):
            r = _rec([{"kind": "start"}, {"kind": "freeze", "freeze_kind": "limit"}],
                     outcome="completed", **hdr)
            s = turnlog.summarize(r)
            assert s["evidence"] == "insufficient" and s["implied"] == "unknown" \
                and s["phase"] == "unknown", s
            assert turnlog.drift(r) == [], "an insufficient summary drifts against nothing"
        empty = _rec([], outcome="completed")
        assert turnlog.summarize(empty)["evidence"] == "insufficient"
        # ORDER is checked: a seq inversion drifts
        r = _rec([{"kind": "start"}, {"kind": "result", "boundary": True}],
                 outcome="completed")
        r["events"][0]["seq"], r["events"][1]["seq"] = 2, 1
        assert turnlog.drift(r) == ["order"]
    check("insufficient · a partial or truncated record (or none at all) "
          "yields unknown and no drift even against a wrong outcome; a "
          "seq inversion drifts as order", _insufficient)

    def _fixture_names() -> None:
        ok = "1788682251395-0044-stream-net.json"
        assert turnlog.fixture_name(ok) == ok
        assert turnlog.fixture_name("C:/x/y/" + ok) == ok      # the site's path
        assert turnlog.is_fixture_name(ok)
        for bad in ("../" + ok, "x/" + ok, "evil.json", ok + ".txt", SECRETS[2],
                    "1788682251395-0044-stream-secret.json", "", None, 3):
            assert not turnlog.is_fixture_name(bad), bad
        for bad in ("evil.json", ok + ".txt", SECRETS[2], "", None):
            assert turnlog.fixture_name(bad) is None, bad
        root = _tmp_root("fxpath")
        rd = turnlog.record_dir(root, "org", "node")
        fd = failfix.fixture_dir(root, "org", "node")
        os.makedirs(rd)
        os.makedirs(fd)
        rec_path = os.path.join(rd, "1788682251395-0001-claude-frozen.json")
        with open(rec_path, "w") as f:
            f.write("{}")
        with open(os.path.join(fd, ok), "w") as f:
            f.write("{}")
        assert turnlog.fixture_path(rec_path, ok) == os.path.abspath(os.path.join(fd, ok))
        assert turnlog.fixture_path(rec_path, "1788682251395-0045-stream-net.json") is None
        assert turnlog.fixture_path(rec_path, "../" + ok) is None
        assert turnlog.fixture_path(os.path.join(root, "elsewhere", "r.json"), ok) is None
        # a record in another node's directory cannot reach this fixture
        other = turnlog.record_dir(root, "org", "other")
        os.makedirs(other)
        assert turnlog.fixture_path(os.path.join(other, "x.json"), ok) is None
    check("fixture names · only generated names are accepted; resolution "
          "is confined to the sibling failfix/<org>/<node>/ of the record's "
          "own turnlog root, must exist, and never crosses nodes", _fixture_names)


# ══════════════════════════════════════════════════════════════════════════ §4

def _node_shape(slug: str, nid: str) -> dict:
    """The parts of the node document a turn's outcome writes — the
    comparison key for the fail-open controls (no timestamps)."""
    n = rig.node(slug, nid)
    o = store.load_org(slug)
    fz = n.get("frozen") or {}
    rows = (o.d.get("turn_error_log") or {}).get(nid) or []
    return {"frozen": {k: fz.get(k) for k in ("connection", "limit", "until")},
            "net_fail_run": n.get("net_fail_run"), "state": n.get("state"),
            "hard_fail_run": n.get("hard_fail_run"),
            "errors": [r["text"][:60] for r in rows],
            "mail_n": len(o.d.get("mail", {}).get(nid, []) if isinstance(
                o.d.get("mail"), dict) else [])}


def sec_claude() -> dict:
    print("\n§4  the claude lane through the fake CLI")
    out: dict = {}

    slug, nid = rig.probe_org()
    rig.set_mode("plain", reply="done " + CANARIES[0])
    rig.run_turn(slug, nid, "hello " + CANARIES[1])

    def _completed() -> None:
        rec = _last(slug, nid)
        out["completed"] = rec
        _ordered(rec)
        assert rec["outcome"] == "completed" and rec["lane"] == "claude" \
            and rec["tier"] == "haiku" and rec["fixture"] is None, rec
        assert rec["error_class"] is None and rec["partial"] is False
        assert rec["text_len"] == len("hello " + CANARIES[1])
        for a, b in (("start", "spawn"), ("spawn", "init"),
                     ("init", "first_output"), ("first_output", "assistant"),
                     ("assistant", "result"), ("result", "exit"),
                     ("exit", "fold_back"), ("fold_back", "end")):
            _before(rec, a, b)
        res = _ev(rec, "result")
        assert res["boundary"] is True and res["is_error"] is False and \
            res["subtype"] == "success" and res["in_tokens"] == 1000 and \
            res["result_len"] == len("done " + CANARIES[0]) and res["cost_known"]
        ex = _ev(rec, "exit")
        assert ex["code"] == 0 and ex["parked"] is False
        assert "classify" not in _kinds(rec) and "owner" not in _kinds(rec)
        assert rec["paid_booked"] is True
        assert rec["cost_usd"] == 0.0001 and rec["cost_known"] is True
        s = turnlog.summarize(rec)
        assert s["implied"] == "completed" and s["first_output_ms"] is not None
        assert turnlog.drift(rec) == []
        assert not [p for p in turnlog.list_records(store.DATA_ROOT, slug, nid)
                    if p.endswith(".partial.json")]
        _assert_no_canary(rec, ("hello", "done"))
        assert supervisor.state(slug, nid).get("turns_run", 0) >= 1
        assert not rig.node(slug, nid).get("frozen")
    check("completed · start→spawn→init→first_output→assistant→result→exit→"
          "fold_back→end in order; typed result (usage, length, cost); no "
          "classify/owner; cost booked; summary agrees; no stub left; no "
          "canary", _completed)

    slug2, nid2 = rig.probe_org()
    rig.set_mode("died-in-flight")
    rig.run_turn(slug2, nid2, "second " + CANARIES[2])

    def _died() -> None:
        n = rig.node(slug2, nid2)
        fixture(bool(n.get("frozen")), "the died-in-flight turn did not freeze")
        rec = _last(slug2, nid2)
        out["died"] = rec
        out["died_shape"] = _node_shape(slug2, nid2)
        _ordered(rec)
        assert rec["outcome"] == "frozen" and rec["error_class"] == "RuntimeError"
        assert rec["run"] == 0 and rec["run_since_ms"] is None   # BEFORE the failure
        _before(rec, "assistant", "exit")
        _before(rec, "exit", "classify")
        _before(rec, "classify", "freeze")
        _before(rec, "freeze", "owner")
        ex = _ev(rec, "exit")
        assert ex["code"] == 1 and ex["exit_only"] is True and ex["stderr_len"] == 0
        cl = _ev(rec, "classify")
        assert cl["net"] is True and cl["limit"] is False and cl["started"] \
            and not cl["boundary"] and cl["typed"] is None and not cl["or_lane"]
        fz = _ev(rec, "freeze")
        assert fz["freeze_kind"] == "connection" and fz["run"] == 1 and \
            fz["delay_s"] == 30 and fz["schedule"] == "backoff"
        owners = [(e["branch"], e["handled"]) for e in rec["events"] if e["kind"] == "owner"]
        assert owners == [("net_retry", True), ("terminal", True)], owners
        assert "abandon" not in _kinds(rec), "a scheduled retry is not abandonment"
        assert "result" not in _kinds(rec)
        # CORRELATION: the fixture named exists beside the record and replays
        assert rec["fixture"] and _ev(rec, "fixture")["written"] is True
        path = turnlog.fixture_path(_records(slug2, nid2)[-1], rec["fixture"])
        fixture(path is not None, f"fixture {rec['fixture']} not resolved")
        fx = failfix.load(path)
        assert fx["phase"] == "stream" and fx["recorded"]["verdict"] == "net"
        assert fx["observed"]["run"] == 1
        s = turnlog.summarize(rec)
        assert s["phase"] == "stream" and s["implied"] == "frozen"
        assert turnlog.drift(rec) == []
        _assert_no_canary(rec, ("second",))
    check("died-in-flight · exit(1, exit_only) → classify net → freeze "
          "connection run 1 backoff 30 → owner net_retry handled → terminal "
          "handled; outcome frozen; no abandon; the named fixture resolves "
          "beside the record and agrees (phase stream, verdict net)", _died)

    slug3, nid3 = rig.probe_org()
    planted = ("Invalid API key " + SECRETS[0] + " for " + SECRETS[3] + " at "
               + SECRETS[4] + " " + SECRETS[1] + " cfg " + SECRETS[2] + " · "
               + CANARIES[1] + " unknown option '--" + IDENT_CANARIES[0] + "'")
    rig.set_mode("iserror", limit_text=planted, api_error_status=401)
    rig.run_turn(slug3, nid3, "third " + CANARIES[0])

    def _401() -> None:
        rec = _last(slug3, nid3)
        out["e401"] = rec
        _ordered(rec)
        _assert_no_canary(rec, ("third", "invalid"))
        res = _ev(rec, "result")
        assert res["boundary"] and res["is_error"] and res["status"] == 401
        assert res["result_len"] == len(planted)
        cl = _ev(rec, "classify")
        assert cl["boundary"] is True and cl["started"] is True
        assert cl["limit"] is False and cl["net"] is False
        owners = [(e["branch"], e["handled"]) for e in rec["events"] if e["kind"] == "owner"]
        assert owners == [("terminal", False)], owners
        ab = _ev(rec, "abandon")
        assert ab["door"] == "ran_then_failed" and ab["hard_fail_run"] == 1
        assert rec["outcome"] == "abandoned" and rec["fixture"]
        s = turnlog.summarize(rec)
        assert s["phase"] == "result-error" and s["implied"] == "abandoned"
        assert turnlog.drift(rec) == []
        assert not rig.node(slug3, nid3).get("frozen")
    check("is_error 401 · secrets/sentences/identifiers absent; typed "
          "status 401, result length; owner terminal unhandled; abandon "
          "ran_then_failed on the first hard fail; outcome abandoned; "
          "phase result-error; not frozen", _401)

    slug4, nid4 = rig.probe_org()
    rig.set_mode("dead-on-arrival")
    rig.run_turn(slug4, nid4, "fourth")

    def _doa() -> None:
        rec = _last(slug4, nid4)
        _ordered(rec)
        ks = _kinds(rec)
        assert "first_output" not in ks and "assistant" not in ks, ks
        ex = _ev(rec, "exit")
        assert ex["code"] == 1 and ex["exit_only"] is True
        cl = _ev(rec, "classify")
        assert cl["started"] is False and cl["net"] is False
        assert _ev(rec, "abandon")["door"] == "pre_model"
        assert rec["outcome"] == "abandoned"
        assert "freeze" not in ks, "dead on arrival is never retried"
        assert turnlog.summarize(rec)["phase"] == "unknown"
        assert turnlog.drift(rec) == []
    check("dead-on-arrival · no output events; exit_only; not started; "
          "abandon pre_model; no freeze; outcome abandoned; phase unknown",
          _doa)

    slug5, nid5 = rig.probe_org()
    rig.set_mode("hang")
    _idle = supervisor.TURN_IDLE
    supervisor.TURN_IDLE = 1
    try:
        rig.run_turn(slug5, nid5, "fifth")
    finally:
        supervisor.TURN_IDLE = _idle

    def _hang() -> None:
        rec = _last(slug5, nid5)
        _ordered(rec)
        wd = _ev(rec, "watchdog")
        assert wd["why"] == "idle" and wd["elapsed_ms"] >= 1000, wd
        _before(rec, "watchdog", "abandon")
        assert _ev(rec, "abandon")["door"] == "killed"
        assert rec["outcome"] == "killed" and rec["error_class"] == "RuntimeError"
        assert "classify" not in _kinds(rec), "the kill path never classifies"
        assert rec["fixture"] is None
        assert turnlog.summarize(rec)["implied"] == "killed"
        assert turnlog.drift(rec) == []
    check("hang · the idle watchdog (from its own thread) precedes the "
          "abandon; door killed; outcome killed; no classify, no fixture",
          _hang)

    slug6, nid6 = rig.probe_org()
    rig.set_mode("plain")
    supervisor.state(slug6, nid6)["interrupted"] = True
    rig.run_turn(slug6, nid6, "sixth")

    def _interrupt() -> None:
        rec = _last(slug6, nid6)
        _ordered(rec)
        _before(rec, "exit", "interrupt")
        assert rec["outcome"] == "interrupted" and rec["fixture"] is None
        assert "classify" not in _kinds(rec)
        assert turnlog.summarize(rec)["implied"] == "interrupted"
        assert turnlog.drift(rec) == []
        assert supervisor.state(slug6, nid6).get("turns_run", 0) >= 1, "a pause completes"
    check("interrupt · a manual pause flag is an interrupt event after exit; "
          "outcome interrupted; the success tail still ran", _interrupt)
    out["slug"], out["nid"] = slug2, nid2
    return out


# ══════════════════════════════════════════════════════════════════════════ §5

def sec_openrouter() -> None:
    print("\n§5  the OpenRouter lane")
    openrouter.set_key("or-test-key-000000")
    try:
        slug, nid = rig.probe_org()
        o = store.load_org(slug)
        o.d.update(api_key="sk-test", api_fallback=True)
        o.node(nid)["model"] = OR_TIER
        o.d.setdefault("tiers", {})[OR_TIER] = 1
        o.d.setdefault("models", {})[OR_TIER] = OR_MODEL
        store.save_org(o)
        env = supervisor.spawn_env(store.load_org(slug), tier=OR_TIER, nid=nid)
        fixture(supervisor.identity_in_env(env) == supervisor.OPENROUTER_IDENTITY,
                "the spawn env is not the OR lane")
        rig.set_mode("iserror", limit_text=OR_429, api_error_status=429)
        rig.run_turn(slug, nid)
    finally:
        openrouter.set_key("")

    def _or() -> None:
        n = rig.node(slug, nid)
        fixture(bool((n.get("frozen") or {}).get("limit")),
                f"the OR 429 did not freeze: {n.get('frozen')!r}")
        rec = _last(slug, nid)
        _ordered(rec)
        assert rec["lane"] == "openrouter" and rec["tier"] == "other", rec["tier"]
        cl = _ev(rec, "classify")
        assert cl["or_lane"] is True and cl["typed"] == 429 and cl["limit"] is True
        assert _ev(rec, "result")["status"] == 429
        owners = [e["branch"] for e in rec["events"] if e["kind"] == "owner"]
        assert "limit_freeze" in owners, owners
        assert _ev(rec, "freeze")["freeze_kind"] == "limit"
        assert rec["outcome"] == "frozen" and rec["fixture"]
        assert turnlog.summarize(rec)["implied"] == "frozen"
        assert turnlog.drift(rec) == []
        _assert_no_canary(rec)
        fx = failfix.load(turnlog.fixture_path(_records(slug, nid)[-1], rec["fixture"]))
        assert fx["lane"] == "openrouter" and fx["recorded"]["typed"] == 429
    check("openrouter · lane openrouter (the spawn identity), classify by the "
          "typed 429 (or_lane, typed, limit), owner limit_freeze, freeze "
          "limit, outcome frozen; the fixture beside it says the same lane",
          _or)


# ══════════════════════════════════════════════════════════════════════════ §6

def _mkorg(label: str, tier: str) -> tuple[str, str]:
    org = store.create_org(f"zz turnlog {label}")
    r = org.hire(USER, None, tier, 2, "px", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a turn-events test agent")
    store.save_org(org)
    return org.d["slug"], r["node"]


def sec_codex() -> None:
    print("\n§6  codex through fakecodex")

    os.environ["FAKECODEX_SCENARIO"] = "tool"
    slug, nid = _mkorg("codexok", "sol")
    codex_limits.invalidate()
    rig.run_turn(slug, nid, "do it " + CANARIES[0])

    def _ok() -> None:
        rec = _last(slug, nid)
        _ordered(rec)
        assert rec["lane"] == "codex" and rec["tier"] == "sol"
        assert rec["outcome"] == "completed" and rec["fixture"] is None, rec["outcome"]
        rt = _ev(rec, "codex_route")
        assert rt["pool"] == "plan" and rt["route"] == "direct" and \
            rt["selection"] == "preflight", rt
        _before(rec, "codex_route", "first_output")
        _before(rec, "first_output", "codex_item")
        _before(rec, "codex_item", "teardown")
        _before(rec, "teardown", "codex_status")
        assert _ev(rec, "codex_status")["status"] == "completed"
        items = [e["type"] for e in rec["events"] if e["kind"] == "codex_item"]
        assert "agent_message" in items, items
        assert _ev(rec, "codex_item", last=True)["n"] >= len(items)
        assert "codex_decide" not in _kinds(rec)
        assert _ev(rec, "teardown")["exited"] is True
        assert turnlog.summarize(rec)["implied"] == "completed"
        assert turnlog.drift(rec) == []
        _assert_no_canary(rec)
    check("codex completed · route plan/direct/preflight → first_output → "
          "items (agent_message) → teardown → status completed; no decide; "
          "outcome completed", _ok)

    os.environ["FAKECODEX_SCENARIO"] = "usage_limit"
    slug2, nid2 = _mkorg("codexwall", "sol")
    codex_limits.invalidate()
    rig.run_turn(slug2, nid2, "wall " + CANARIES[1])

    def _wall() -> None:
        n = rig.node(slug2, nid2)
        fixture(bool((n.get("frozen") or {}).get("limit")),
                f"the codex wall did not freeze: {n.get('frozen')!r}")
        rec = _last(slug2, nid2)
        _ordered(rec)
        assert rec["outcome"] == "frozen" and rec["error_class"] == "_ProviderTurnFailed"
        _before(rec, "codex_status", "codex_decide")
        _before(rec, "codex_decide", "fixture")
        _before(rec, "fixture", "owner")
        _before(rec, "owner", "freeze")
        assert _ev(rec, "codex_status")["status"] == "failed"
        rl = [e for e in rec["events"] if e["kind"] == "codex_rate_limit"]
        assert rl and rl[0]["pool"] == "plan" and rl[0]["folded"] is True and \
            rl[0]["percent"] == 100 and isinstance(rl[0]["reset"], int), rl
        dc = _ev(rec, "codex_decide")
        assert dc["decision"] == "usage-limit" and dc["pool_state"] == "exhausted" \
            and dc["reset_known"] is True, dc
        assert _ev(rec, "owner")["branch"] == "provider_limit"
        fz = _ev(rec, "freeze")
        assert fz["freeze_kind"] == "limit" and fz["schedule"] == "observed-deadline" \
            and fz["reset_known"] is True
        assert _ev(rec, "codex_account")["ambiguous"] is False
        path = turnlog.fixture_path(_records(slug2, nid2)[-1], rec["fixture"])
        fixture(path is not None, "codex fixture not resolved")
        assert failfix.load(path)["codex"]["kind_recorded"] == "usage-limit"
        assert turnlog.summarize(rec)["implied"] == "frozen"
        assert turnlog.drift(rec) == []
        _assert_no_canary(rec)
    check("codex wall · rate_limit (plan, 100%, int reset, folded) → status "
          "failed → decide usage-limit/exhausted → fixture → owner "
          "provider_limit → freeze limit observed-deadline; outcome frozen; "
          "the fixture resolves", _wall)

    os.environ["FAKECODEX_SCENARIO"] = "plain_failure"
    slug3, nid3 = _mkorg("codexplain", "sol")
    codex_limits.invalidate()
    rig.run_turn(slug3, nid3, "plain " + CANARIES[2])

    def _plain() -> None:
        assert not rig.node(slug3, nid3).get("frozen")
        rec = _last(slug3, nid3)
        _ordered(rec)
        assert rec["outcome"] == "failed" and rec["fixture"]
        assert _ev(rec, "codex_decide")["decision"] == "other"
        assert "freeze" not in _kinds(rec) and "owner" not in _kinds(rec)
        assert turnlog.summarize(rec)["implied"] == "failed"
        assert turnlog.drift(rec) == []
        _assert_no_canary(rec)
    check("codex plain failure · decide other; no owner, no freeze; outcome "
          "failed (the except's default for a RuntimeError-class raise)",
          _plain)
    os.environ.pop("FAKECODEX_SCENARIO", None)


# ══════════════════════════════════════════════════════════════════════════ §7

def sec_antigravity() -> None:
    print("\n§7  antigravity through fakeantigravity")

    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"
    slug, nid = _mkorg("agyok", "pro")
    rig.run_turn(slug, nid, "agy " + CANARIES[0])

    def _ok() -> None:
        rec = _last(slug, nid)
        _ordered(rec)
        assert rec["lane"] == "antigravity" and rec["tier"] == "pro"
        assert rec["outcome"] == "completed" and rec["fixture"] is None
        _before(rec, "first_output", "agy_step")
        _before(rec, "agy_step", "teardown")
        _before(rec, "teardown", "agy_status")
        assert _ev(rec, "agy_step")["step"] == "text"
        assert _ev(rec, "agy_status")["status"] == "completed"
        assert "agy_wall" not in _kinds(rec)
        assert turnlog.summarize(rec)["implied"] == "completed"
        assert turnlog.drift(rec) == []
        _assert_no_canary(rec)
    check("antigravity completed · first_output → agy_step text → teardown "
          "→ status completed; outcome completed", _ok)

    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "usage_limit"
    os.environ.pop("FAKEANTIGRAVITY_RESET_IN", None)
    slug2, nid2 = _mkorg("agywall", "pro")
    rig.run_turn(slug2, nid2, "agy wall " + IDENT_CANARIES[2])

    def _wall() -> None:
        n = rig.node(slug2, nid2)
        fixture(bool((n.get("frozen") or {}).get("limit")),
                f"the antigravity wall did not freeze: {n.get('frozen')!r}")
        rec = _last(slug2, nid2)
        _ordered(rec)
        assert rec["outcome"] == "frozen"
        _before(rec, "agy_status", "agy_wall")
        _before(rec, "agy_wall", "agy_ceiling")
        _before(rec, "agy_ceiling", "fixture")
        _before(rec, "fixture", "freeze")
        w = _ev(rec, "agy_wall")
        assert w["walled"] and w["reset_known"] and w["schedule"] == "observed-deadline" \
            and isinstance(w["reset_in_s"], int), w
        assert _ev(rec, "agy_ceiling")["killed"] is False
        assert "watchdog" not in _kinds(rec)
        assert _ev(rec, "freeze")["freeze_kind"] == "limit"
        assert turnlog.summarize(rec)["implied"] == "frozen"
        assert turnlog.drift(rec) == []
        _assert_no_canary(rec)
    check("antigravity wall · status failed → wall (walled, reset known, "
          "observed-deadline, typed reset) → ceiling not killed → fixture → "
          "freeze limit; outcome frozen", _wall)

    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "plain_error"
    os.environ["FAKEANTIGRAVITY_ERROR"] = ("Internal error: the model returned "
                                          "no response. Resets in 3h0m0s. "
                                          + CANARIES[2])
    os.environ["FAKEANTIGRAVITY_INIT_DELAY"] = "1.5"
    _real = supervisor.TURN_TIMEOUT
    supervisor.TURN_TIMEOUT = 1
    try:
        slug3, nid3 = _mkorg("agyceiling", "pro")
        rig.run_turn(slug3, nid3, "agy slowly")
    finally:
        supervisor.TURN_TIMEOUT = _real
        os.environ.pop("FAKEANTIGRAVITY_ERROR", None)
        os.environ.pop("FAKEANTIGRAVITY_INIT_DELAY", None)

    def _ceiling() -> None:
        assert not rig.node(slug3, nid3).get("frozen")
        rec = _last(slug3, nid3)
        _ordered(rec)
        assert rec["outcome"] == "killed"
        c = _ev(rec, "agy_ceiling")
        assert c["killed"] is True and c["ceiling_s"] == 1 and c["elapsed_s"] >= 1
        assert _ev(rec, "agy_wall")["walled"] is False
        _before(rec, "fixture", "watchdog")
        assert _ev(rec, "watchdog")["why"] == "ceiling"
        assert "freeze" not in _kinds(rec)
        assert turnlog.summarize(rec)["implied"] == "killed"
        assert turnlog.drift(rec) == []
        _assert_no_canary(rec)
    check("antigravity ceiling · not walled, ceiling killed, watchdog "
          "ceiling after the fixture; outcome killed; no freeze", _ceiling)

    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "interrupt"
    slug4, nid4 = _mkorg("agyint", "pro")
    th = threading.Thread(target=rig.run_turn, args=(slug4, nid4, "agy interrupt"),
                          daemon=True)
    th.start()
    st4 = supervisor.state(slug4, nid4)
    deadline = time.time() + 10
    while time.time() < deadline and "antigravity_turn" not in st4:
        time.sleep(0.05)
    int_result = supervisor.interrupt_turn(slug4, nid4)
    th.join(timeout=30)

    def _interrupt() -> None:
        fixture(int_result.get("interrupted") is True, f"pause refused: {int_result}")
        fixture(not th.is_alive(), "the interrupted turn did not come back")
        rec = _last(slug4, nid4)
        _ordered(rec)
        assert _ev(rec, "agy_status")["status"] == "interrupted"
        assert rec["outcome"] == "interrupted" and rec["fixture"] is None
        assert turnlog.summarize(rec)["implied"] == "interrupted"
        assert turnlog.drift(rec) == []
    check("antigravity interrupt · status interrupted; outcome interrupted",
          _interrupt)
    os.environ.pop("FAKEANTIGRAVITY_SCENARIO", None)


# ══════════════════════════════════════════════════════════════════════════ §8

def sec_failopen(control: dict) -> None:
    print("\n§8  fail-open — recorder failure cannot change an outcome")
    fixture("died_shape" in control, "no died-in-flight control from §4")
    want = control["died_shape"]

    def _run_died(label: str) -> tuple[str, str]:
        slug, nid = rig.probe_org()
        rig.set_mode("died-in-flight")
        rig.run_turn(slug, nid, "second " + CANARIES[2])
        return slug, nid

    # (a) an obstructed root: the org's turnlog directory is a FILE
    slug_a, nid_a = rig.probe_org()
    obstacle = os.path.join(store.DATA_ROOT, "turnlog", slug_a)
    os.makedirs(os.path.dirname(obstacle), exist_ok=True)
    with open(obstacle, "w") as f:
        f.write("not a directory")
    rig.set_mode("died-in-flight")
    rig.run_turn(slug_a, nid_a, "second " + CANARIES[2])

    def _obstructed() -> None:
        assert _node_shape(slug_a, nid_a) == want, (_node_shape(slug_a, nid_a), want)
        assert turnlog.list_records(store.DATA_ROOT, slug_a, nid_a) == []
        assert failfix.list_fixtures(store.DATA_ROOT, slug_a, nid_a), "failfix still wrote"
    check("obstructed root · no record, no stub, the failure fixture still "
          "written, the node document identical to the control (frozen, "
          "counter, error rows)", _obstructed)

    # (b) the recorder's own emit — its guard included — replaced by a
    # raiser: every site-side `turnlog.emit`, `dispose`'s event and close's
    # `end` then raise inside the recorder
    _emit = turnlog.Recorder.emit

    def _boom(*a, **k):
        raise RuntimeError("planted recorder failure")
    turnlog.Recorder.emit = _boom
    try:
        slug_b, nid_b = _run_died("emit")
    finally:
        turnlog.Recorder.emit = _emit

    def _emit_raises() -> None:
        assert _node_shape(slug_b, nid_b) == want, (_node_shape(slug_b, nid_b), want)
        rec = _last(slug_b, nid_b)
        assert rec["recorder_errors"] > 0 and rec["outcome"] == "frozen", rec
        assert rec["events"] == [], _kinds(rec)
        assert rec["fixture"], "the fixture correlation survives a dead emit"
        assert turnlog.summarize(rec)["evidence"] == "insufficient"
        assert turnlog.drift(rec) == []
    check("emit raises · every event fails inside the recorder, the errors "
          "are counted, the record still closes with the disposition and "
          "the fixture name, the node document is identical to the control",
          _emit_raises)

    # (c) the writer raises at open AND close
    _write = turnlog._write
    turnlog._write = _boom
    try:
        slug_c, nid_c = _run_died("write")
    finally:
        turnlog._write = _write

    def _write_raises() -> None:
        assert _node_shape(slug_c, nid_c) == want, (_node_shape(slug_c, nid_c), want)
        assert turnlog.list_records(store.DATA_ROOT, slug_c, nid_c) == []
    check("writer raises · neither stub nor record, the node document "
          "identical to the control", _write_raises)

    def _control_bites() -> None:
        # the comparison key is not vacuous: a different scenario differs
        slug_d, nid_d = rig.probe_org()
        rig.set_mode("plain")
        rig.run_turn(slug_d, nid_d, "x")
        assert _node_shape(slug_d, nid_d) != want
        assert want["frozen"]["connection"] is True and want["net_fail_run"] == 1
    check("positive control · the node-shape key distinguishes a completed "
          "turn from the frozen control", _control_bites)


# ══════════════════════════════════════════════════════════════════════════ §9

HOOK = """
import builtins, sys
BANNED = ("orgtree.store", "orgtree.supervisor", "orgtree.ledger",
          "orgtree.codex_route", "orgtree.providers", "orgtree.warmpool",
          "subprocess", "socket", "http", "urllib", "sqlite3", "threading")
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
"""


def _run_tool(args: list[str], prelude: str = "") -> subprocess.CompletedProcess:
    tool = os.path.join(HERE, "..", "..", "tools", "inspect_turn.py")
    code = (HOOK + prelude + "\nimport runpy, sys\nsys.argv = [" + repr(tool)
            + "] + " + repr(args) + "\nrunpy.run_path(" + repr(tool)
            + ", run_name='__main__')\n")
    env = dict(os.environ)
    env.pop("ORGTREE_DATA", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, encoding="utf-8", timeout=60,
                          cwd=os.path.join(HERE, "..", ".."), env=env)


def sec_tool(control: dict) -> None:
    print("\n§9  the inspector under the purity hook")
    slug, nid = control["slug"], control["nid"]
    paths = _records(slug, nid)
    fixture(bool(paths), "no record on disk for the tool")

    def _renders() -> None:
        r = _run_tool([paths[-1], "--assert"])
        assert r.returncode == 0, r.stderr[-1500:]
        assert "outcome='frozen'" in r.stdout and "freeze" in r.stdout
        assert "summary: evidence=sufficient phase=stream implied=frozen" in r.stdout
        assert "recomputed verdict 'net' recorded 'net'" in r.stdout, r.stdout
        assert "drift: []" in r.stdout
        j = _run_tool([paths[-1], "--json"])
        o = json.loads(j.stdout.strip().splitlines()[-1])
        assert o["fixture"]["resolved"] is True and o["drift"] == []
        assert o["summary"]["implied"] == "frozen"
        for s in SECRETS + CANARIES:
            assert s.lower() not in (r.stdout + j.stdout).lower()
    check("renders · the died-in-flight record prints its timeline, summary "
          "and the chained fixture replay with no drift, under the hook",
          _renders)

    def _drifts() -> None:
        d = tempfile.mkdtemp(prefix="tl-tool-", dir=rig._TMP)
        rd = os.path.join(d, "turnlog", "o", "n")
        os.makedirs(rd)
        bad = copy.deepcopy(turnlog.load(paths[-1]))
        bad["outcome"] = "completed"
        bad["fixture"] = None
        bp = os.path.join(rd, "1788682251161-0001-claude-completed.json")
        with open(bp, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        r = _run_tool([bp, "--assert"])
        assert r.returncode == 1 and "drift: ['outcome']" in r.stdout, r.stdout[-600:]
        # a named fixture that is not beside the record is reported, not read
        bad["fixture"] = "1788682251395-0044-stream-net.json"
        with open(bp, "w", encoding="utf-8") as f:
            json.dump(bad, f)
        r = _run_tool([bp])
        assert "not found beside this record" in r.stdout, r.stdout[-400:]
        # a stub
        stub = {"schema": 1, "partial": True, "events": [], "outcome": None}
        sp = os.path.join(rd, "1788682251161-0002.partial.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(stub, f)
        r = _run_tool([sp, "--assert"])
        assert r.returncode == 0 and "PARTIAL" in r.stdout and \
            "evidence=insufficient" in r.stdout, r.stdout[-400:]
    check("drifts · an edited outcome exits 1 with drift ['outcome']; an "
          "unresolved fixture name is reported not read; a stub renders as "
          "PARTIAL/insufficient and passes --assert", _drifts)

    def _hook_bites() -> None:
        r = _run_tool([paths[-1]], prelude="\nimport orgtree.store\n")
        assert r.returncode != 0 and "PURITY: import of orgtree.store refused" in r.stderr
        r = _run_tool([paths[-1]], prelude="\nopen(%r, 'w')\n" % os.path.join(rig._TMP, "x"))
        assert r.returncode != 0 and "PURITY: write refused" in r.stderr
    check("control · the hook refuses orgtree.store and a write when asked",
          _hook_bites)


def main() -> int:
    sec_schema()
    sec_recorder()
    sec_summary()
    if not PURE:
        if not shutil.which("node"):
            print("\n  INERT    `node` is not on PATH: the CLI stand-in cannot "
                  "run, so §4–§9 prove nothing here")
            FAILED.append("§4–§9 inert: node missing")
        else:
            got = sec_claude()
            sec_openrouter()
            sec_codex()
            sec_antigravity()
            if "died_shape" in got:
                sec_failopen(got)
                sec_tool(got)
            else:
                print("\n  INERT    §8/§9 need §4's died-in-flight control, "
                      "which did not record — they prove nothing here")
                FAILED.append("§8/§9 inert: no died-in-flight control record")
    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    for f in FAILED:
        print("\n--- FAILED:", f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
