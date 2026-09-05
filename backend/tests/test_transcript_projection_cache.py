"""S3: bounded incremental transcript projection and exact invalidation.

    python backend/tests/test_transcript_projection_cache.py

Every file fixture is under this process's throwaway ORGTREE_DATA. The tests
count real JSON parses rather than infer caching from wall time; rewrite tests
wrap the cold builder so their positive control proves invalidation ran.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
TMP = Path(tempfile.mkdtemp(prefix="orgtree-transcript-cache-"))
DATA = TMP / "data"
DATA.mkdir()
(DATA / "defaults.json").write_text(
    '{"net_hub_address":"http://127.0.0.1:9"}', encoding="utf-8")
os.environ.update(ORGTREE_DATA=str(DATA), ORGTREE_STORE="sqlite",
                  ORGTREE_PORT="9", ORGTREE_WARM="0")

from orgtree import store, supervisor as S  # noqa: E402
from orgtree.ledger import USER  # noqa: E402

assert Path(store.DATA_ROOT).resolve() == DATA.resolve()


def stamp(i: int) -> str:
    return f"2026-01-01T00:{(i // 60) % 60:02d}:{i % 60:02d}.000Z"


def user(text: str, i: int, at: str | None = None) -> dict:
    return {"type": "user", "timestamp": at if at is not None else stamp(i),
            "message": {"role": "user", "content": text}}


def assistant(text: str, i: int, at: str | None = None) -> dict:
    return {"type": "assistant",
            "timestamp": at if at is not None else stamp(i),
            "message": {"id": f"a-{i}", "role": "assistant", "model": "m",
                        "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": i + 10,
                                  "cache_read_input_tokens": 0}}}


class ProjectionCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        org = store.create_org("zz transcript projection cache")
        org.hire(USER, None, "haiku", 0, "agent")
        store.save_org(org)
        cls.slug = org.d["slug"]
        cls.nid = "agent"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self) -> None:
        S._chat_cache_clear()
        self.org = store.load_org(self.slug)
        self.sid = "s3-" + uuid.uuid4().hex
        self.org.node(self.nid)["session_id"] = self.sid
        self.path = Path(S.journal_store()) / "projects" / self.slug \
            / f"{self.sid}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.view = Path(S._prompt_view_path(self.slug, self.sid))
        self.org.d["steered_log"] = {}
        self.org.d["turn_error_log"] = {}
        self.org.d["delivering"] = {}

    def write_rows(self, rows: list[dict], mode: str = "w") -> None:
        with self.path.open(mode, encoding="utf-8", newline="\n") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def write_views(self, rows: list[tuple[str, str, str]],
                    mode: str = "a") -> None:
        with self.view.open(mode, encoding="utf-8", newline="\n") as f:
            for raw, visible, at in rows:
                f.write(json.dumps({
                    "v": 1,
                    "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    "chars": len(raw), "visible": visible, "at": at,
                }, ensure_ascii=False) + "\n")

    def texts(self, payload: dict) -> list[str]:
        return [str(m.get("text") or "") for m in payload["messages"]]

    def counted_read(self, **kwargs):
        real = S.json.loads
        count = [0]

        def loads(value, *args, **kw):
            count[0] += 1
            return real(value, *args, **kw)

        with mock.patch.object(S.json, "loads", loads):
            result = S.read_chat(self.org, self.nid, **kwargs)
        return result, count[0]

    def test_cold_warm_append_and_reference_equality(self) -> None:
        rows = []
        views = []
        for i in range(20):
            if i % 2:
                rows.append(assistant(f"a{i}", i))
            else:
                raw = f"u{i}"
                rows.append(user(raw, i))
                views.append((raw, raw, stamp(i)))
        self.write_rows(rows)
        self.write_views(views, "w")

        cold, cold_parses = self.counted_read(last=5)
        warm, warm_parses = self.counted_read(last=5)
        self.assertEqual(cold_parses, 30)
        self.assertEqual(warm_parses, 0)
        self.assertEqual(cold, warm)

        self.write_views([("u20", "u20", stamp(20))])
        self.write_rows([user("u20", 20), assistant("a21", 21)], "a")
        appended, append_parses = self.counted_read(last=5)
        self.assertEqual(append_parses, 3,
                         "one sidecar + two transcript rows, not the prefix")
        reference = S._read_chat_uncached(self.org, self.nid, last=5)
        self.assertEqual(appended, reference)
        self.assertEqual([m["seq"] for m in appended["messages"]],
                         list(range(17, 22)))

        line = json.dumps(assistant("torn", 22)) + "\n"
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line[:-12])
        during = S.read_chat(self.org, self.nid, last=2)
        self.assertNotIn("torn", self.texts(during))
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line[-12:])
        completed, completed_parses = self.counted_read(last=2)
        self.assertEqual(completed_parses, 1)
        self.assertEqual(self.texts(completed)[-1], "torn")

    def test_returned_rows_are_independently_mutable(self) -> None:
        self.write_rows([{"type": "assistant", "timestamp": stamp(0),
                         "message": {"id": "call", "model": "m",
                                     "content": [{"type": "tool_use",
                                                  "id": "mutable-tool",
                                                  "name": "Read",
                                                  "input": {"file_path": "original"}}]}}])
        first = S.read_chat(self.org, self.nid, last=1)
        first["messages"][0]["tools"][0]["arg"] = "caller changed it"
        second = S.read_chat(self.org, self.nid, last=1)
        self.assertIn("original", second["messages"][0]["tools"][0]["arg"])

    def test_tool_result_appended_later_updates_the_retained_tool(self) -> None:
        call = {"type": "assistant", "timestamp": stamp(0), "message": {
            "id": "call", "model": "m", "content": [{
                "type": "tool_use", "id": "tool-1", "name": "Read",
                "input": {"file_path": "x"}}]}}
        self.write_rows([call])
        before = S.read_chat(self.org, self.nid, last=2)
        self.assertNotIn("result", before["messages"][0]["tools"][0])
        result = {"type": "user", "timestamp": stamp(1), "message": {
            "content": [{"type": "tool_result", "tool_use_id": "tool-1",
                         "content": "the result"}]}}
        self.write_rows([result], "a")
        after = S.read_chat(self.org, self.nid, last=2)
        self.assertEqual(after["messages"][0]["tools"][0]["result"],
                         "the result")
        self.assertEqual(after, S._read_chat_uncached(self.org, self.nid,
                                                       last=2))

    def test_reader_mode_pending_clear_freshness_and_late_view_are_live(self) -> None:
        at = S.now_iso()
        body = "human words"
        raw = ("[ORG STATE — current]\n[END ORG STATE]\n\n"
               f"FROM @user · message · {at}\n{body}")
        self.write_rows([user(raw, 0, at)])
        self.org.d["delivering"] = {self.nid: [{"mail": [{
            "at": at, "body": body}]}]}

        ui = S.read_chat(self.org, self.nid, last=5)
        mcp = S.read_chat(self.org, self.nid, last=5, hold_back=False)
        self.assertEqual(ui["prompts_withheld"], 1)
        self.assertEqual(ui["messages"], [])
        self.assertEqual(self.texts(mcp), [raw])

        self.org.d["delivering"] = {}       # live change, no transcript write
        cleared = S.read_chat(self.org, self.nid, last=5)
        self.assertEqual(self.texts(cleared), [raw])
        self.org.d["delivering"] = {self.nid: [{"mail": [{
            "at": at, "body": body}]}]}
        with mock.patch.object(S, "_prompt_is_fresh", return_value=False):
            expired = S.read_chat(self.org, self.nid, last=5)
        self.assertEqual(self.texts(expired), [raw])

        self.write_views([(raw, body, at)], "w")
        projected = S.read_chat(self.org, self.nid, last=5)
        self.assertEqual(self.texts(projected), [body])
        self.assertEqual(projected["prompts_withheld"], 0)

    def test_sidecar_append_after_reload_is_parsed_before_adoption(self) -> None:
        at = stamp(1)
        raw_a = "[ORG STATE x]\n[END ORG STATE]\n\nA"
        raw_b = "[ORG STATE x]\n[END ORG STATE]\n\nB"
        self.write_rows([user(raw_a, 1, at), user(raw_b, 2, at)])
        real_load = S._load_prompt_views
        calls = [0]

        def append_after_reload(*args):
            calls[0] += 1
            if calls[0] == 2:
                self.write_views([(raw_a, "visible A", at)])
                captured = real_load(*args)
                # This tail was absent from the loader's return, but present
                # in the file version the cache is about to adopt.
                self.write_views([(raw_b, "visible B", at)])
                return captured
            return real_load(*args)

        with mock.patch.object(S, "_load_prompt_views",
                               side_effect=append_after_reload):
            first = S.read_chat(self.org, self.nid, hold_back=False)
        second = S.read_chat(self.org, self.nid, hold_back=False)
        self.assertEqual(calls[0], 2, "positive control reached reload")
        self.assertEqual(self.texts(first), ["visible A", "visible B"])
        self.assertEqual(self.texts(second), ["visible A", "visible B"],
                         "unchanged read must not inherit a skipped tail")

    def test_complete_sidecar_without_newline_is_not_spent_twice(self) -> None:
        raw = "repeated user"
        at = stamp(1)
        self.write_rows([user(raw, 1, at)])
        row = {"v": 1, "sha256": hashlib.sha256(raw.encode()).hexdigest(),
               "chars": len(raw), "visible": "ONE projection", "at": at}
        self.view.parent.mkdir(parents=True, exist_ok=True)
        self.view.write_text(json.dumps(row), encoding="utf-8")
        self.assertEqual(self.texts(S.read_chat(self.org, self.nid)),
                         ["ONE projection"])

        with self.view.open("a", encoding="utf-8", newline="\n") as f:
            f.write("\n")
        self.write_rows([user(raw, 2, at)], "a")
        warm = S.read_chat(self.org, self.nid)
        S._chat_cache_clear()
        cold = S.read_chat(self.org, self.nid)
        self.assertEqual(self.texts(warm), ["ONE projection", raw])
        self.assertEqual(warm, cold)

    def test_synthetic_ties_missing_timestamps_and_late_seq(self) -> None:
        same = "2026-01-01T00:00:02.000Z"
        missing = {"type": "user", "message": {"content": "missing"}}
        self.write_rows([missing, assistant("base-a", 1, same),
                         assistant("base-b", 2, same)])
        self.org.d["steered_log"] = {self.nid: [
            {"at": same, "text": "steer-1"},
            {"at": same, "text": "steer-2"}]}
        self.org.d["turn_error_log"] = {self.nid: [
            {"at": same, "text": "error-1"},
            {"at": same, "text": "error-2"}]}
        got = S.read_chat(self.org, self.nid, last=10)
        self.assertEqual(self.texts(got),
                         ["missing", "base-a", "base-b", "steer-1",
                          "steer-2", "⚠ error-1", "⚠ error-2"])
        self.assertEqual([m["seq"] for m in got["messages"]], list(range(7)))

        self.org.d["steered_log"][self.nid].append(
            {"at": "2026-01-01T00:00:03.000Z", "text": "late"})
        tail = S.read_chat(self.org, self.nid, last=2)
        self.assertEqual(self.texts(tail), ["⚠ error-2", "late"])
        self.assertEqual([m["seq"] for m in tail["messages"]], [6, 7])

    def test_cache_key_includes_sidecar_identity(self) -> None:
        raw = "same raw"
        at = stamp(0)
        self.write_rows([user(raw, 0, at)])
        self.write_views([(raw, "first projection", at)], "w")
        self.assertEqual(self.texts(S.read_chat(self.org, self.nid)),
                         ["first projection"])

        other = store.create_org("zz transcript cache second org "
                                 + uuid.uuid4().hex[:8])
        other.hire(USER, None, "haiku", 0, "agent")
        other.node("agent")["session_id"] = self.sid
        other_view = Path(S._prompt_view_path(other.d["slug"], self.sid))
        other_view.parent.mkdir(parents=True, exist_ok=True)
        row = {"v": 1, "sha256": hashlib.sha256(raw.encode()).hexdigest(),
               "chars": len(raw), "visible": "second projection", "at": at}
        other_view.write_text(json.dumps(row) + "\n", encoding="utf-8")
        self.assertEqual(self.texts(S.read_chat(other, "agent")),
                         ["second projection"])

    def test_cached_live_evidence_tracks_prefixes_and_new_turns(self) -> None:
        self.write_rows([user("question", 0),
                         assistant("short plus a durable suffix", 1)])
        S.read_chat(self.org, self.nid, last=1)
        st = S.state(self.slug, self.nid)
        with S._state_lock:
            st["live"] = [{"kind": "text", "text": "short",
                           "at": stamp(1)}]
        swept = S.read_chat(self.org, self.nid, last=1)
        self.assertEqual(swept["live"], [], "prefix evidence matches exactly")

        self.write_rows([user("next turn", 2)], "a")
        with S._state_lock:
            st["live"] = [{"kind": "text", "text": "short",
                           "at": stamp(2)}]
        kept = S.read_chat(self.org, self.nid, last=1)
        self.assertEqual([r["text"] for r in kept["live"]], ["short"],
                         "the appended user row resets current-turn evidence")
        self.write_rows([assistant("short plus new", 3)], "a")
        self.assertEqual(S.read_chat(self.org, self.nid, last=1)["live"], [])

    def test_rewrite_replace_and_truncate_regrow_force_cold(self) -> None:
        old = [assistant(f"old-{i:03d}", i) for i in range(12)]
        new = [assistant(f"new-{i:03d}", i) for i in range(12)]
        self.write_rows(old)
        S.read_chat(self.org, self.nid, last=3)
        real = S._cold_projection
        calls = [0]

        def cold(*args, **kwargs):
            calls[0] += 1
            return real(*args, **kwargs)

        with mock.patch.object(S, "_cold_projection", cold):
            self.write_rows(new)  # same byte length, ordinary same-size rewrite
            self.assertEqual(self.texts(S.read_chat(self.org, self.nid, last=1)),
                             ["new-011"])
            self.assertEqual(calls[0], 1)

            replacement = self.path.with_suffix(".replacement")
            with replacement.open("w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(assistant("replacement", 20)) + "\n")
            os.replace(replacement, self.path)
            self.assertEqual(self.texts(S.read_chat(self.org, self.nid, last=1)),
                             ["replacement"])
            self.assertEqual(calls[0], 2)

            # Same inode, final size larger than the cached file, but the old
            # prefix was replaced: growth alone must not be accepted as append.
            self.write_rows([assistant(f"regrown-{i:03d}", i)
                             for i in range(30)])
            self.assertEqual(self.texts(S.read_chat(self.org, self.nid, last=1)),
                             ["regrown-029"])
            self.assertEqual(calls[0], 3)

    def test_context_change_restart_oversize_and_oracle_contract(self) -> None:
        self.write_rows([assistant("durable", 0)])
        first = S.read_chat(self.org, self.nid, last=1)
        real = S._cold_projection
        calls = [0]

        def cold(*args, **kwargs):
            calls[0] += 1
            return real(*args, **kwargs)

        with mock.patch.object(S, "_cold_projection", cold):
            self.org.node(self.nid)["model"] = "opus"
            S.read_chat(self.org, self.nid, last=1)
        self.assertEqual(calls[0], 1, "node context metadata invalidates")
        S._chat_cache_clear()
        restarted = S.read_chat(self.org, self.nid, last=1)
        self.assertEqual(first["messages"], restarted["messages"])

        S._chat_cache_clear()
        with mock.patch.object(S, "_CHAT_CACHE_MAX_ENTRY_BYTES", 1):
            S.read_chat(self.org, self.nid, last=1)
            self.assertNotIn(S._chat_cache_key(self.org, self.nid,
                                               str(self.path)), S._chat_cache)
        with mock.patch.object(S, "_CHAT_CACHE_MAX_ENTRY_ROWS", 0):
            S.read_chat(self.org, self.nid, last=1)
            self.assertNotIn(S._chat_cache_key(self.org, self.nid,
                                               str(self.path)), S._chat_cache)

        node = self.org.node(self.nid)
        node["bearer_state"] = "preserving"
        node["oracle_exchanges"] = [{"q": "oracle-q", "a": "oracle-a",
                                      "at": stamp(9)}]
        oracle = S.read_chat(self.org, self.nid, last=1)
        self.assertEqual(self.texts(oracle),
                         ["durable", "oracle-q", "oracle-a"])
        self.assertEqual(self.texts({"messages": oracle["messages"][-1:]}),
                         ["oracle-a"], "MCP's retained outer slice caps oracle")

    def test_two_readers_single_consume_and_failed_append_discards(self) -> None:
        self.write_rows([assistant("first", 0)])
        S.read_chat(self.org, self.nid, last=5)
        self.write_views([("second", "second", stamp(1))], "w")
        self.write_rows([user("second", 1)], "a")
        real_loads = S.json.loads
        parses = [0]
        guard = threading.Lock()

        def loads(value, *args, **kwargs):
            with guard:
                parses[0] += 1
            return real_loads(value, *args, **kwargs)

        results: list[dict] = []
        with mock.patch.object(S.json, "loads", loads):
            threads = [threading.Thread(
                target=lambda: results.append(S.read_chat(
                    self.org, self.nid, last=5))) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(parses[0], 2,
                         "one view occurrence and one transcript row consumed once")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

        self.write_rows([assistant("third", 2)], "a")
        real_source = S._read_chat_source

        def explode(*args, **kwargs):
            answer = real_source(*args, **kwargs)
            if kwargs.get("_resume") is not None:
                raise RuntimeError("injected after incremental mutation")
            return answer

        with mock.patch.object(S, "_read_chat_source", explode):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                S.read_chat(self.org, self.nid, last=5)
        retry = S.read_chat(self.org, self.nid, last=5)
        self.assertEqual(self.texts(retry), ["first", "second", "third"])

    def test_waiting_reader_rejects_failed_readers_mutated_entry(self) -> None:
        self.write_rows([assistant("first", 0)])
        S.read_chat(self.org, self.nid)
        self.write_rows([assistant("second", 1)], "a")
        mutated = threading.Event()
        waiter_has_entry = threading.Event()
        real_source = S._read_chat_source
        real_entry = S._entry_for
        results: dict[str, object] = {}

        def source(*args, **kwargs):
            answer = real_source(*args, **kwargs)
            if (threading.current_thread().name == "failing-reader"
                    and kwargs.get("_resume") is not None):
                mutated.set()
                self.assertTrue(waiter_has_entry.wait(3))
                raise RuntimeError("injected after shared mutation")
            return answer

        def entry(*args, **kwargs):
            answer = real_entry(*args, **kwargs)
            if threading.current_thread().name == "waiting-reader":
                waiter_has_entry.set()
            return answer

        def run() -> None:
            name = threading.current_thread().name
            try:
                results[name] = self.texts(S.read_chat(self.org, self.nid))
            except RuntimeError as exc:
                results[name] = str(exc)

        with mock.patch.object(S, "_read_chat_source", side_effect=source), \
                mock.patch.object(S, "_entry_for", side_effect=entry):
            failing = threading.Thread(target=run, name="failing-reader")
            failing.start()
            self.assertTrue(mutated.wait(3))
            waiting = threading.Thread(target=run, name="waiting-reader")
            waiting.start()
            failing.join(4)
            waiting.join(4)
        self.assertFalse(failing.is_alive())
        self.assertFalse(waiting.is_alive())
        self.assertEqual(results["failing-reader"],
                         "injected after shared mutation")
        self.assertEqual(results["waiting-reader"], ["first", "second"])
        S._chat_cache_clear()
        self.assertEqual(results["waiting-reader"],
                         self.texts(S.read_chat(self.org, self.nid)))

    def test_continuous_writer_retry_is_bounded(self) -> None:
        self.write_rows([assistant("first", 0)])
        S.read_chat(self.org, self.nid, last=5)
        real = S._refresh_projection
        calls = [0]

        def refresh(*args, **kwargs):
            answer = real(*args, **kwargs)
            calls[0] += 1
            self.write_rows([assistant(f"racing-{calls[0]}", 10 + calls[0])],
                            "a")
            return answer

        with mock.patch.object(S, "_refresh_projection", refresh):
            out = S.read_chat(self.org, self.nid, last=5)
        self.assertEqual(calls[0], 2)
        self.assertTrue(out["messages"])
        self.assertNotIn(S._chat_cache_key(self.org, self.nid,
                                           str(self.path)), S._chat_cache)


if __name__ == "__main__":
    unittest.main(verbosity=2)
