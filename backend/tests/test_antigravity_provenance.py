"""Replay agy's retained run-error lookup; no installed CLI or provider calls.

The numeric payload fields are from the 1.1.27 embedded protobuf descriptors.
Both the fake child process and inspection operate only in this suite's home.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

CID = "12345678-1234-1234-1234-123456789abc"
QUOTA = "Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 1h56m58s."
PROMPT = "Only this current prompt belongs to the run."
ANSWER = "The current work is complete."


def varint(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128)
        n >>= 7
    return bytes(out) + bytes([n])


def field(key, value):
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return varint(key * 8 + 2) + varint(len(value)) + value
    return varint(key * 8) + varint(value)


def step(index, kind, message=None, *, state=3, error=b"", stop=2, tools=False):
    if kind == 14:
        detail = field(19, field(2, message or PROMPT))
    elif kind == 15:
        detail = field(20, field(1, ANSWER if message is None else message)
                       + field(12, stop) + (field(7, b"tool") if tools else b""))
    elif kind == 17:
        # The actual historical error had EMPTY ordinary error_details.
        detail = field(24, field(3, field(1, message or QUOTA) + field(7, 429)))
    else:
        detail = field({101: 114, 132: 140}[kind], b"fixture")
    payload = field(1, kind) + field(4, state) + detail
    return (index, kind, state, 0, b"", error, payload)


def save(path, rows):
    with sqlite3.connect(path) as db:
        db.executemany("INSERT OR REPLACE INTO steps VALUES (?,?,?,?,?,?,?)", rows)


def make_db(home):
    path = home / ".gemini/antigravity-cli/conversations" / (CID + ".db")
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE trajectory_meta(cascade_id TEXT);"
                        "CREATE TABLE steps(idx INTEGER PRIMARY KEY,step_type INTEGER,status INTEGER,"
                        "step_format INTEGER,metadata BLOB,error_details BLOB,step_payload BLOB);")
        db.execute("INSERT INTO trajectory_meta VALUES (?)", (CID,))
    save(path, [step(0, 14, "Earlier prompt"), step(1, 17), step(2, 15, "Earlier answer")])
    return path


def current(prompt=PROMPT):
    return [step(3, 14, prompt), step(4, 101), step(5, 15, "Working.", tools=True),
            step(6, 132), step(7, 15)]


def wire(rows):
    events = []
    for row in rows:
        if row[1] not in (14, 15, 101, 132):
            continue
        body = {"step_index": row[0], "conversation_id": CID, "state": "DONE",
                "step_type": {14: "user_input", 15: "agent_response", 101: "system_message", 132: "tool"}[row[1]]}
        if row[1] == 15:
            body["text_delta"] = "Working." if row[0] == 5 else ANSWER
            body["usage"] = {"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 20}
        events.append({"event": "step_update", "step_update": body})
    return events


def fake():
    """Synthetic replay of the binary's all-history LastRunErrorDetails path."""
    from orgtree import antigravity_provenance as p
    prompt = json.loads(sys.stdin.readline())["message"]["content"]
    home = Path(os.environ["AGY_FIXTURE_HOME"])
    path = p.conversation_path(CID, {"HOME": str(home), "USERPROFILE": str(home)})
    scenario = os.environ["AGY_FIXTURE_SCENARIO"]
    rows = current(prompt)
    if scenario == "fresh_identical":
        rows.append(step(8, 17))
    if scenario == "fresh_distinct":
        rows.append(step(8, 17, QUOTA.replace("1h56m58s", "2h0m0s")))
    save(path, rows)
    events = [{"event": "init", "conversation_id": CID, "init": {"model": "fixture-model"}}] + wire(rows)
    # Deliberately search the WHOLE store, not the current-turn slice.
    reader = p._Read(path, CID)
    error = next(p._quota_error(r) for r in reader.rows("ORDER BY idx DESC") if r[1] == 17)
    reader.close()
    if scenario == "auth":
        error = "Authentication failed; please sign in."
    if scenario == "partial":
        events[-1]["step_update"]["state"] = "ACTIVE"
    if scenario != "missing_result":
        status = {"canceled": "CANCELED", "success": "SUCCESS"}.get(scenario, "ERROR")
        events.append({"event": "result", "result": {"conversation_id": CID, "status": status, "error": error}})
    for event in events:
        print(json.dumps(event), flush=True)
    if scenario in ("timeout", "interrupt"):
        time.sleep(30)


# Establish a throwaway data root BEFORE importing any orgtree module.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if "--fake" in sys.argv:
    assert Path(os.environ["ORGTREE_DATA"]).resolve() == Path(os.environ["AGY_FIXTURE_HOME"]).resolve() / "data"
    fake()
    raise SystemExit

_root = tempfile.TemporaryDirectory(prefix="orgtree-agy-provenance-")
os.environ["ORGTREE_DATA"] = str(Path(_root.name) / "data")
Path(os.environ["ORGTREE_DATA"]).mkdir()
Path(os.environ["ORGTREE_DATA"], "defaults.json").write_text('{"net_hub_address":"http://127.0.0.1:9"}')
from orgtree import antigravity_provenance as p, antigravityrun  # noqa: E402


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="orgtree-agy-home-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = make_db(self.home)
        self.env = {"HOME": str(self.home), "USERPROFILE": str(self.home)}
        self.boundary = p.capture(CID, self.env)
        self.assertIsNotNone(self.boundary, "INERT: fixture must provide prelaunch quota evidence")
        self.rows = current()
        save(self.path, self.rows)
        self.events = wire(self.rows)
        self.result = {"status": "ERROR", "conversation_id": CID, "error": QUOTA}

    def reconcile(self):
        return p.reconcile(self.boundary, PROMPT, CID, self.result, self.events)

    def close_turn(self, turn):
        turn.close()
        if turn.proc is not None:
            for stream in (turn.proc.stdin, turn.proc.stdout, turn.proc.stderr):
                if stream is not None:
                    stream.close()

    def test_positive_read_only_and_descriptor_run_error(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.assertEqual(self.reconcile(), {"kind": "historical_cli_quota_error", "historical_step": 1,
                                          "boundary_step": 2, "final_step": 7})
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT error_details FROM steps WHERE idx=1").fetchone()[0], b"")

    def test_real_child_replays_old_error_and_preserves_usage(self):
        # Start captures at index2; the fake appends all current data only AFTER Popen.
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM steps WHERE idx>2")
        (self.home / "data").mkdir()
        env = {**self.env, "ORGTREE_DATA": str(self.home / "data"), "AGY_FIXTURE_HOME": str(self.home),
               "AGY_FIXTURE_SCENARIO": "historical"}
        turn = antigravityrun.AntigravityTurn([sys.executable, __file__, "--fake"], cwd=str(self.home),
                   model="fixture-model", effort=None, conversation_id=CID, env_extra=env)
        self.addCleanup(self.close_turn, turn)
        turn.start(PROMPT)
        result = turn.wait(timeout=10)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["agent_text"], "Working." + ANSWER)
        self.assertEqual(result["token_usage"]["input"], 200)
        self.assertEqual(result["token_usage"]["requests"], 2)
        self.assertEqual(result["result_provenance"]["boundary_step"], 2)
        self.assertEqual(turn._result["error"], QUOTA, "Original evidence remains unchanged")

    def test_child_fresh_quota_after_output_and_other_failures(self):
        for scenario in ("fresh_identical", "fresh_distinct", "auth", "partial", "canceled", "missing_result", "timeout", "interrupt", "success"):
            with self.subTest(scenario=scenario):
                with sqlite3.connect(self.path) as db:
                    db.execute("DELETE FROM steps WHERE idx>2")
                (self.home / "data").mkdir(exist_ok=True)
                env = {**self.env, "ORGTREE_DATA": str(self.home / "data"), "AGY_FIXTURE_HOME": str(self.home),
                       "AGY_FIXTURE_SCENARIO": scenario}
                turn = antigravityrun.AntigravityTurn([sys.executable, __file__, "--fake"], cwd=str(self.home),
                       model="fixture-model", effort=None, conversation_id=CID, env_extra=env)
                self.addCleanup(self.close_turn, turn)
                turn.start(PROMPT)
                if scenario == "interrupt":
                    time.sleep(.1)
                    self.assertTrue(turn.interrupt())
                result = turn.wait(timeout=.1 if scenario == "timeout" else 10)
                self.assertEqual(result["status"], {"success": "completed", "interrupt": "interrupted"}.get(scenario, "failed"))
                self.assertNotIn("result_provenance", result)
                self.assertTrue(result["agent_text"], "INERT: failure must follow real partial output")
                if scenario in ("fresh_identical", "fresh_distinct", "auth", "partial", "canceled", "timeout"):
                    self.assertEqual(result["stop_reason"], turn._result["error"])

    def test_fresh_identical_error_in_payload_cannot_be_ignored(self):
        save(self.path, [step(8, 17)])
        self.assertIsNone(self.reconcile())

    def test_error_variant_even_with_known_sql_type(self):
        row = list(step(7, 15))
        row[-1] += field(24, field(3, field(1, QUOTA)))
        save(self.path, [row])
        self.assertIsNone(self.reconcile())

    def test_completion_predicates(self):
        for row in (step(7, 15, state=2), step(7, 15, state=7), step(7, 15, stop=1),
                    step(7, 15, tools=True), step(7, 15, ""), step(7, 15, error=b"failure"), step(7, 132)):
            with self.subTest(row=row[:3]):
                save(self.path, [row])
                self.assertIsNone(self.reconcile())

    def test_prompt_and_response_exact_association(self):
        save(self.path, [step(3, 14, "A different current prompt")])
        self.assertIsNone(self.reconcile())
        save(self.path, [step(3, 14)])
        save(self.path, [step(7, 15, "A different completed response")])
        self.assertIsNone(self.reconcile())

    def test_prelaunch_boundary_and_historical_message_are_required(self):
        self.assertIsNone(p.reconcile(None, PROMPT, CID, self.result, self.events))
        self.result["error"] = QUOTA.replace("1h56m58s", "5h0m0s")
        self.assertIsNone(self.reconcile())
        self.result["error"] = QUOTA
        save(self.path, [step(1, 17, QUOTA + " Changed")])
        self.assertIsNone(self.reconcile())
        save(self.path, [step(1, 17), step(2, 15, "Changed boundary")])
        self.assertIsNone(self.reconcile())

    def test_wire_identity_completion_and_text_controls(self):
        baseline = copy.deepcopy(self.events)
        for key, value in (("state", "ACTIVE"), ("state", "ERROR"), ("conversation_id", "different"),
                           ("step_index", 2), ("step_index", 200), ("text_delta", "wrong"), ("step_type", "tool")):
            with self.subTest(key=key, value=value):
                self.events = copy.deepcopy(baseline)
                self.events[-1]["step_update"][key] = value
                self.assertIsNone(self.reconcile())
        self.events = baseline[:-1]
        self.assertIsNone(self.reconcile())
        self.events = baseline + [{"event": "error", "error": "fresh failure"}]
        self.assertIsNone(self.reconcile())

    def test_missing_tool_completion_and_second_user(self):
        self.events = [e for e in self.events if e["step_update"]["step_type"] != "tool"]
        self.assertIsNone(self.reconcile())
        self.events = wire(self.rows)
        save(self.path, [step(6, 14, "another turn")])
        self.assertIsNone(self.reconcile())

    def test_missing_indices(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM steps WHERE idx=4")
        self.assertIsNone(self.reconcile())

    def test_missing_wrong_unsupported_and_wal_evidence(self):
        self.assertIsNone(p.capture("../../unqualified", self.env))
        self.assertIsNone(p.capture(CID, {}))
        self.assertIsNone(p.capture("00000000-0000-0000-0000-000000000000", self.env))
        wal = Path(str(self.path) + "-wal")
        wal.write_bytes(b"fixture")
        self.assertIsNone(self.reconcile())
        self.assertIsNone(p.capture(CID, self.env))
        wal.unlink()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE steps SET step_format=99 WHERE idx=7")
        self.assertIsNone(self.reconcile())
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE trajectory_meta SET cascade_id='another-conversation'")
        self.assertIsNone(self.reconcile())

    def test_payload_and_work_bounds(self):
        with patch.object(p, "MAX_ROWS", 4):
            self.assertIsNone(self.reconcile())
        with patch.object(p, "MAX_BYTES", 20):
            self.assertIsNone(self.reconcile())
        with patch.object(p, "MAX_EVENTS", 2):
            self.assertIsNone(self.reconcile())
        for payload in (b"\x80", b"\x0a\xff\xff\xff", b"x" * (p.MAX_BLOB + 1)):
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE steps SET step_payload=? WHERE idx=7", (payload,))
            self.assertIsNone(self.reconcile())

    def test_sql_work_budget_and_exact_result_identity(self):
        reader = p._Read(self.path, CID)
        self.addCleanup(reader.db.close)
        self.assertEqual(reader.db.execute("SELECT count(*) FROM steps").fetchone()[0], 8)
        with self.assertRaises(sqlite3.OperationalError):
            reader.db.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000) SELECT sum(x) FROM n").fetchone()
        for cid in ("different", None):
            self.assertIsNone(p.reconcile(self.boundary, PROMPT, cid, self.result, self.events))
        self.result["conversation_id"] = "different"
        self.assertIsNone(self.reconcile())

    def test_file_replacement_and_mid_read_change(self):
        original = self.path.read_bytes()
        replacement = self.path.with_suffix(".replacement")
        replacement.write_bytes(original)
        os.replace(replacement, self.path)
        self.assertIsNone(self.reconcile())
        self.boundary = p.capture(CID, self.env)
        # A valid positive control for a new boundary and a later completed turn.
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM steps WHERE idx>2")
        self.boundary = p.capture(CID, self.env)
        save(self.path, current())
        self.assertIsNotNone(self.reconcile())
        close = p._Read.close
        def changed(reader):
            stat = reader.path.stat()
            os.utime(reader.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            close(reader)
        with patch.object(p._Read, "close", changed):
            self.assertIsNone(self.reconcile())


if __name__ == "__main__":
    unittest.main()
