"""Replay agy's retained run-error lookup; no installed CLI or provider calls.

The numeric payload fields are from the 1.1.27 embedded protobuf descriptors.
Both the fake child process and inspection operate only in this suite's home.

PRODUCTION SHAPE OF THE WIRE (measured, redteam-opus 2026-09-07): the CLI
streams the final response as the stored body plus exactly one trailing
"\n" it does not store - 39 of 39 retained turns, never an exact match. The
first cut of this suite streamed what it stored, so the exact-equality guard
passed here and could never pass in production. `wire()` now streams
body + "\n" for the final step, and the tolerance is pinned from both sides:
exact and one-LF fire; two LFs, a leading LF, CRLF, a trailing space and any
other single-byte change decline (test_final_text_tolerance_*).

Every outcome is also asserted through the retained log line (`_emit`): the
reason codes name the predicate that fired or declined, and every line is
checked content-free (no prompt, response or error text).
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
        # 23 is the mid-turn compaction summary; its body lives in field 30.
        detail = field({23: 30, 101: 114, 132: 140}[kind], b"fixture")
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
            # the final response streams with the CLI's one trailing LF
            body["text_delta"] = "Working." if row[0] == 5 else ANSWER + "\n"
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

    def logged(self, call):
        """Run `call` collecting the retained lines; every line content-free."""
        lines = []
        with patch.object(p, "_emit", lines.append):
            value = call()
        for line in lines:
            for secret in (PROMPT, ANSWER, QUOTA, "Working.", "Earlier"):
                self.assertNotIn(secret, line, "a retained line carried conversation text")
            self.assertTrue(line.isascii(), line)
        return value, lines

    def declines(self, reason, **detail):
        value, lines = self.logged(self.reconcile)
        self.assertIsNone(value)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(f"reconcile: declined reason={reason}"), lines[0])
        for key, expected in detail.items():
            self.assertIn(f" {key}={expected} ", lines[0] + " ", lines[0])
        return lines[0]

    def fires(self, **detail):
        value, lines = self.logged(self.reconcile)
        self.assertEqual(value, {"kind": "historical_cli_quota_error", "historical_step": 1,
                                 "boundary_step": 2, "final_step": 7})
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("reconcile: fired historical=1 boundary=2 final=7"), lines[0])
        for key, expected in detail.items():
            self.assertIn(f" {key}={expected} ", lines[0] + " ", lines[0])
        return value

    def final_delta(self, text):
        self.events[-1]["step_update"]["text_delta"] = text

    def close_turn(self, turn):
        turn.close()
        if turn.proc is not None:
            for stream in (turn.proc.stdin, turn.proc.stdout, turn.proc.stderr):
                if stream is not None:
                    stream.close()

    def test_positive_read_only_and_descriptor_run_error(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        # the production shape: the wire's final text is the stored body + "\n"
        self.assertEqual(self.events[-1]["step_update"]["text_delta"], ANSWER + "\n")
        self.fires(wire_tail="lf")
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
        self.assertEqual(result["agent_text"], "Working." + ANSWER + "\n")
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

    def test_final_text_tolerance_fires_on_exact_and_on_exactly_one_lf(self):
        self.final_delta(ANSWER)
        self.fires(wire_tail="none")
        self.final_delta(ANSWER + "\n")
        self.fires(wire_tail="lf")
        # the LF may arrive as its own delta, as the real CLI sends it
        self.final_delta(ANSWER)
        done = copy.deepcopy(self.events[-1])
        self.events[-1]["step_update"]["state"] = "ACTIVE"
        done["step_update"]["text_delta"] = "\n"
        self.events.append(done)
        self.fires(wire_tail="lf")

    def test_final_text_tolerance_declines_everything_else(self):
        for label, text in (("two LFs", ANSWER + "\n\n"), ("leading LF", "\n" + ANSWER),
                            ("leading and trailing LF", "\n" + ANSWER + "\n"), ("CRLF", ANSWER + "\r\n"),
                            ("CR", ANSWER + "\r"), ("trailing space", ANSWER + " "),
                            ("LF then space", ANSWER + "\n "), ("space then LF", ANSWER + " \n"),
                            ("one byte changed", ANSWER[:-1] + "!"), ("one byte changed then LF", ANSWER[:-1] + "!\n"),
                            ("one byte dropped", ANSWER[:-1]), ("one byte added", ANSWER + "x"),
                            ("LF then a byte", ANSWER + "\nx"), ("empty", ""), ("LF only", "\n")):
            with self.subTest(label=label):
                self.final_delta(text)
                self.declines("final_text_mismatch", final=7, wire_bytes=len(text.encode()), stored_bytes=len(ANSWER))

    def test_stored_body_with_trailing_lf_needs_the_same_on_wire(self):
        # if the CLI ever stores the LF too: exact fires, exactly one extra LF
        # fires (the same tolerance), two extra or none at all decline
        save(self.path, [step(7, 15, ANSWER + "\n")])
        self.final_delta(ANSWER + "\n")
        self.fires(wire_tail="none")
        self.final_delta(ANSWER + "\n\n")
        self.fires(wire_tail="lf")
        self.final_delta(ANSWER + "\n\n\n")
        self.declines("final_text_mismatch")
        self.final_delta(ANSWER)
        self.declines("final_text_mismatch")

    def test_capture_says_why_it_has_no_evidence(self):
        cases = (("no_conversation", lambda: p.capture(None, self.env)),
                 ("no_conversation", lambda: p.capture("", self.env)),
                 ("unqualified_conversation", lambda: p.capture("../../unqualified", self.env)),
                 ("no_home", lambda: p.capture(CID, {})),
                 ("no_database", lambda: p.capture("00000000-0000-0000-0000-000000000000", self.env)))
        for reason, call in cases:
            with self.subTest(reason=reason):
                value, lines = self.logged(call)
                self.assertIsNone(value)
                self.assertEqual(lines, [f"capture: none reason={reason}"])
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM steps WHERE step_type=17")
        value, lines = self.logged(lambda: p.capture(CID, self.env))
        self.assertIsNone(value)
        self.assertEqual(lines, ["capture: none reason=no_historical_quota_error boundary=7"])
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM steps")
        value, lines = self.logged(lambda: p.capture(CID, self.env))
        self.assertIsNone(value)
        self.assertEqual(lines, ["capture: none reason=empty_store"])
        other = "00000000-0000-0000-0000-000000000000"
        p.conversation_path(other, self.env).write_bytes(b"not a database")
        value, lines = self.logged(lambda: p.capture(other, self.env))
        self.assertIsNone(value)
        self.assertEqual(lines, ["capture: none reason=exception:DatabaseError"])

    def test_capture_says_what_it_captured(self):
        value, lines = self.logged(lambda: p.capture(CID, self.env))
        self.assertIsNotNone(value)
        self.assertEqual(lines, ["capture: captured boundary=7 historical_errors=1 newest_error_step=1"])

    def test_reconcile_names_the_result_predicates(self):
        value, lines = self.logged(lambda: p.reconcile(None, PROMPT, CID, self.result, self.events))
        self.assertIsNone(value)
        self.assertEqual(lines, ["reconcile: declined reason=no_boundary"])
        self.result["status"] = "SUCCESS"
        self.declines("result_not_error")
        self.result["status"] = "ERROR"
        self.result["error"] = "Authentication failed; please sign in."
        self.declines("error_not_quota")
        self.result["error"] = QUOTA.replace("1h56m58s", "5h0m0s")
        self.declines("error_not_historical", historical_errors=1)
        self.result["error"] = QUOTA
        with patch.object(p, "MAX_EVENTS", 2):
            self.declines("event_bound", events=5)

    def test_reconcile_names_the_store_predicates(self):
        save(self.path, [step(3, 14, "A different current prompt")])
        self.declines("prompt_mismatch", step=3)
        save(self.path, [step(3, 14)])
        for row, reason, key in ((step(7, 15, state=2), "step_status", "status=2"),
                                 (step(7, 15, stop=1), "final_stop_reason", "stop_reason=1"),
                                 (step(7, 15, tools=True), "final_has_tool_call", "final=7"),
                                 (step(7, 15, ""), "final_empty", "final=7"),
                                 (step(7, 15, error=b"failure"), "step_error_details", "step=7"),
                                 (step(7, 132), "final_not_response", "step_type=132")):
            with self.subTest(reason=reason):
                save(self.path, [row])
                self.assertIn(key, self.declines(reason))
        save(self.path, [step(7, 15), step(8, 17)])
        self.declines("step_type", step=8, step_type=17)

    def test_diagnostic_details_never_carry_store_or_wire_bytes(self):
        # a malformed protobuf: stop_reason (field 12) arriving as BYTES must
        # decline AND must not put those bytes on the retained line
        marker = "SECRET-FIELD-MARKER"
        row = list(step(7, 15))
        row[-1] = field(1, 15) + field(4, 3) + field(20, field(1, ANSWER) + field(12, marker))
        save(self.path, [tuple(row)])
        line = self.declines("final_stop_reason", final=7, stop_reason="nonnumeric")
        self.assertNotIn(marker, line)
        # a text value in a numeric SQL column is a marker too, never the value
        save(self.path, [step(7, 15)])
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE steps SET step_type=? WHERE idx=6", ("SQL-TEXT-MARKER",))
        line = self.declines("step_type", step=6, step_type="nonnumeric")
        self.assertNotIn("SQL-TEXT-MARKER", line)
        # the formatter itself: only numbers, index lists and '?' pass
        self.assertEqual(p._fmt({"a": 7, "b": "4,5", "c": "?", "d": b"x", "e": "text", "f": 1.5, "g": True}),
                         " a=7 b=4,5 c=? d=nonnumeric e=nonnumeric f=nonnumeric g=?")

    def test_isolated_second_user_and_contiguity_controls(self):
        # a SECOND user step inside the appended interval, everything else
        # complete and on the wire: only the second-user rule may decline
        rows = [step(3, 14), step(4, 101), step(5, 15, "Working.", tools=True), step(6, 132),
                step(7, 14, "a second user message"), step(8, 15)]
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM steps WHERE idx>2")
        save(self.path, rows)
        self.events = wire(rows)
        self.declines("second_user", step=7)
        # contiguity: the interval must start right after the boundary...
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM steps WHERE idx>2")
        save(self.path, self.rows)
        self.events = wire(self.rows)
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM steps WHERE idx=3")
        self.declines("first_not_user", boundary=2, first=4)
        # ...and have no hole
        save(self.path, [step(3, 14)])
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM steps WHERE idx=5")
        self.declines("noncontiguous", boundary=2, rows=4, last=7)

    def test_planner_step_without_wire_done_is_named_not_relaxed(self):
        # The open unknown (Opus): a type-15 planner step with an empty body
        # and a tool call journals no text, and it is NOT observed that the
        # CLI emits a DONE for it. If it does not, this is the decline that
        # will say so on the next live turn - by index, content-free.
        save(self.path, [step(5, 15, "", tools=True)])
        self.events = [e for e in self.events if e["step_update"]["step_index"] != 5]
        self.declines("stored_steps_not_done_on_wire", steps="5")
        # and an ACTIVE without DONE for it is named too
        self.events = wire(self.rows)
        for e in self.events:
            if e["step_update"]["step_index"] == 5:
                e["step_update"]["state"] = "ACTIVE"
        self.declines("wire_steps_open", steps="5")

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

    # -- the shapes an ordinary turn actually has ------------------------
    # Added 2026-09-07 after the 13:56Z decline: the retained turn contained
    # three failed tool calls and one compaction step, and the old rules
    # rejected all four. Every control below was seen to fail before the
    # product change: the two admitted shapes declined step_status/step_type.

    def tool_failed(self, *, run_error=False, state="ERROR"):
        """Row 6 becomes the failed tool call the CLI really produces: stored
        type 132 status 7 with error_details, reported on the wire as
        step_type "tool" state "ERROR" (journal 13:55:37.939Z <-> store 5216)."""
        row = list(step(6, 132, state=7, error=b"tool error detail"))
        if run_error:
            row[6] += field(24, field(3, field(1, QUOTA) + field(7, 429)))
        self.rows[3] = tuple(row)
        save(self.path, self.rows)
        self.events = wire(self.rows)
        for event in self.events:
            if event["step_update"]["step_index"] == 6:
                event["step_update"]["state"] = state

    def compaction(self, *, on_wire=False):
        """A mid-turn compaction summary, stored only (no wire step_update has
        ever been observed for one) unless the test asks for one."""
        self.rows.insert(3, step(6, 23))
        self.rows[4] = step(7, 132)
        self.rows[5] = step(8, 15)
        save(self.path, self.rows)
        self.events = wire(self.rows)
        if on_wire:
            self.events.insert(3, {"event": "step_update", "step_update": {
                "step_index": 6, "conversation_id": CID, "state": "DONE",
                "step_type": "compaction"}})

    def test_failed_tool_call_is_not_a_failed_turn(self):
        self.tool_failed()
        value, lines = self.logged(self.reconcile)
        self.assertEqual(value["final_step"], 7, lines)
        self.assertTrue(lines[0].startswith("reconcile: fired historical=1 boundary=2 final=7"), lines[0])

    def test_a_tool_that_failed_but_carries_a_run_error_still_declines(self):
        self.tool_failed(run_error=True)
        self.declines("step_run_error", step=6)

    def test_status_seven_outside_a_tool_step_still_declines(self):
        self.rows[2] = step(5, 15, "Working.", state=7, tools=True)
        save(self.path, self.rows)
        self.declines("step_status", step=5, status=7)

    def test_error_details_outside_a_failed_tool_still_decline(self):
        self.rows[4] = step(7, 15, error=b"not a tool failure")
        save(self.path, self.rows)
        self.declines("step_error_details", step=7)

    def test_the_two_sides_of_a_tool_failure_need_not_agree(self):
        """Deliberate, and pinned here so a reviewer can attack it rather than
        wonder whether it was missed: the store's status and the wire's state
        describe the SAME tool event, and neither is the discriminator - the
        RunError is. A disagreement between them is not evidence of a current
        quota error, so it does not decline. A RunError on the row still does
        (test_a_tool_that_failed_but_carries_a_run_error_still_declines)."""
        for event in self.events:                     # wire ERROR, store DONE
            if event["step_update"]["step_index"] == 6:
                event["step_update"]["state"] = "ERROR"
        self.assertIsNotNone(self.reconcile())
        self.tool_failed(state="DONE")                # store status 7, wire DONE
        self.assertIsNotNone(self.reconcile())

    def test_wire_error_on_a_model_step_declines(self):
        self.events[-1]["step_update"]["state"] = "ERROR"
        self.declines("wire_nontool_error", step=7)

    def test_a_tool_left_active_on_the_wire_still_declines(self):
        self.tool_failed(state="ACTIVE")
        self.declines("wire_steps_open")

    def test_a_tool_missing_from_the_wire_still_declines(self):
        self.tool_failed()
        self.events = [e for e in self.events if e["step_update"]["step_index"] != 6]
        self.declines("stored_steps_not_done_on_wire", steps=6)

    def test_a_compaction_step_no_longer_blocks_the_turn(self):
        self.compaction()
        value, lines = self.logged(self.reconcile)
        self.assertEqual(value["final_step"], 8, lines)

    def test_a_compaction_step_on_the_wire_declines_by_name_not_by_keyerror(self):
        self.compaction(on_wire=True)
        line = self.declines("wire_step_type_unmapped", step=6, step_type=23)
        self.assertNotIn("exception", line)

    # -- the durable outcome record --------------------------------------

    def records(self):
        return p.read_records()

    def test_the_outcome_is_recorded_durably_on_both_paths(self):
        before = len(self.records())
        self.logged(self.reconcile)
        fired = self.records()[before:]
        self.assertEqual([r["outcome"] for r in fired], ["fired"], fired)
        self.assertEqual(fired[0]["reason"], "")
        self.assertEqual((fired[0]["historical"], fired[0]["boundary"], fired[0]["final"]), (1, 2, 7))
        self.assertEqual(fired[0]["wire_lf"], 1)
        self.result["error"] = "Authentication failed; please sign in."
        self.logged(self.reconcile)
        declined = self.records()[before + 1:]
        self.assertEqual([r["outcome"] for r in declined], ["declined"], declined)
        self.assertEqual(declined[0]["reason"], "error_not_quota")
        for row in fired + declined:
            text = json.dumps(row)
            for secret in (PROMPT, ANSWER, QUOTA, "Working.", "Earlier"):
                self.assertNotIn(secret, text, "a retained record carried conversation text")
            self.assertTrue(text.isascii(), text)

    def test_a_record_that_cannot_be_written_changes_nothing(self):
        before = len(self.records())
        with patch.object(p, "_records_path", side_effect=OSError("no disk")):
            value, lines = self.logged(self.reconcile)
        self.assertEqual(value["final_step"], 7)
        self.assertTrue(lines[0].startswith("reconcile: fired"), lines[0])
        self.assertEqual(len(self.records()), before, "a failed write must leave no record")
        # and the verdict is identical to the one reached with the record on
        self.logged(self.reconcile)
        self.assertEqual(len(self.records()), before + 1)

    def test_records_survive_a_restart_and_stay_bounded(self):
        self.logged(self.reconcile)
        path = Path(p._records_path())
        self.assertTrue(path.is_file(), "no durable record was written")
        kept = len(self.records())
        # a fresh process reads the same file; nothing is held in memory
        self.assertEqual(len(p.read_records()), kept)
        with patch.object(p, "MAX_RECORDS", 1):
            self.logged(self.reconcile)
            self.logged(self.reconcile)
        self.assertTrue(Path(str(path) + ".1").is_file(), "the record never rotated")
        with path.open(encoding="utf-8") as handle:
            self.assertLessEqual(sum(1 for _ in handle), 2)
        # a torn line is skipped, not fatal
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"outcome": "declin\n')
        self.assertTrue(all(r.get("outcome") for r in self.records()))


if __name__ == "__main__":
    unittest.main()
