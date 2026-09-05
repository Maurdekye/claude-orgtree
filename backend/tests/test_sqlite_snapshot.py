"""S2 bounded coherent SQLite read snapshots.

Hermetic: establishes a throwaway ORGTREE_DATA before importing orgtree.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any


ROOT = Path(tempfile.mkdtemp(prefix="orgtree-snapshot-"))
os.environ["ORGTREE_DATA"] = str(ROOT / "data")
os.environ["ORGTREE_STORE"] = "sqlite"
os.environ["ORGTREE_MIGRATE"] = "1"
assert Path(os.environ["ORGTREE_DATA"]).resolve() != Path(
    r"C:\Users\ncola_k8bx\orgtree").resolve()
(ROOT / "data").mkdir(parents=True)
(ROOT / "data" / "defaults.json").write_text(
    '{"net_hub_address":"http://127.0.0.1:9"}', encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.requests import Request  # noqa: E402
from orgtree import api, store  # noqa: E402
from orgtree.ledger import USER  # noqa: E402


PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except BaseException:  # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL  {label}")
    else:
        PASS.append(label)
        print(f"  PASS  {label}")


def fresh(name: str):
    org = store.create_org(name)
    nid = org.hire(USER, None, "haiku", 0, "worker")["node"]
    store.save_org(org)
    return org.d["slug"], nid


def ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row["id"]) for row in rows]


def coherent_route_snapshot_straddles_a_writer() -> None:
    """The early-COMMIT mutant returns mixed A/B and fails this test."""
    slug, nid = fresh("snapshot route race")
    with store.DOC_LOCK:
        a = store.load_org(slug)
        first = a.post_mail(USER, nid, "revision A")
        store.save_org(a)

    # Prepare the canonical B mutation before the reader starts. The writer
    # thread commits it after eager doc rows have been consumed, but before
    # the selected mail_log SQL is issued.
    writer = store.load_org(slug)
    second = writer.post_mail(USER, nid, "revision B")
    reader = store._open_conn(store._db_path(slug))
    writer_go = threading.Event()
    writer_done = threading.Event()
    writer_error: list[BaseException] = []
    trace: list[str] = []
    main_tid = threading.get_ident()

    def write_b() -> None:
        writer_go.wait(10)
        try:
            with store.DOC_LOCK:
                store.save_org(writer)
        except BaseException as exc:  # noqa: BLE001
            writer_error.append(exc)
        finally:
            writer_done.set()

    thread = threading.Thread(target=write_b, name="snapshot-writer")
    thread.start()

    fired = False

    def traced(sql: str) -> None:
        nonlocal fired
        trace.append(sql)
        # doc SELECT has completed before the nodes statement begins, so the
        # read transaction already owns revision A's SQLite snapshot.
        if not fired and sql.startswith("SELECT id, val FROM nodes"):
            fired = True
            writer_go.set()
            assert writer_done.wait(10), "writer did not commit during read"

    reader.set_trace_callback(traced)
    real_acquire = store._POOL.acquire
    main_acquires = 0

    @contextlib.contextmanager
    def controlled_acquire(got_slug: str, *, create: bool = False):
        nonlocal main_acquires
        if threading.get_ident() == main_tid:
            main_acquires += 1
            assert main_acquires == 1, (
                "snapshot opened another connection for a selected section")
            assert got_slug == slug and not create
            yield reader
        else:
            with real_acquire(got_slug, create=create) as conn:
                yield conn

    store._POOL.acquire = controlled_acquire  # type: ignore[method-assign]
    try:
        projected_a = api.node_inbox(slug, nid)
    finally:
        store._POOL.acquire = real_acquire  # type: ignore[method-assign]
        reader.set_trace_callback(None)
        reader.close()
        writer_go.set()
        thread.join(10)

    assert fired and writer_done.is_set() and not writer_error, writer_error
    assert any("FROM log_d" in sql and "owner, seq, val" in sql
               for sql in trace), "selected mail_log was not read on reader connection"
    assert ids(projected_a["pending"]) == [first["id"]]
    assert ids(projected_a["delivered"]) == []

    projected_b = api.node_inbox(slug, nid)
    assert ids(projected_b["pending"]) == [first["id"], second["id"]]
    assert ids(projected_b["delivered"]) == []


def selected_empty_and_absent_are_frozen() -> None:
    slug, _ = fresh("snapshot empty absent")
    o = store.load_org(slug)
    o.d["org_inbox"] = []                 # present-empty by key order
    o.d.pop("notice_log", None)            # genuinely absent
    store.save_org(o)

    snap = store.load_org_snapshot(slug, ("org_inbox", "notice_log"))
    empty = snap.d["org_inbox"]
    assert empty == []
    assert not dict.__contains__(snap.d, "notice_log")

    current = store.load_org(slug)
    current.d["org_inbox"].append({"id": "later-inbox"})
    current.d["notice_log"] = [{"id": "later-notice"}]
    store.save_org(current)

    assert snap.d["org_inbox"] is empty and empty == []
    assert snap.d.get("notice_log", "absent") == "absent"
    assert store.load_org(slug).d["org_inbox"] == [{"id": "later-inbox"}]


def preloaded_mutables_keep_save_baselines() -> None:
    slug, nid = fresh("snapshot mutable save")
    o = store.load_org(slug)
    o.d["events"] = [{"id": "e0"}]
    o.d["mail_log"] = {nid: [{"id": "m0"}]}
    store.save_org(o)

    snap = store.load_org_snapshot(slug, ("events", "mail_log"))
    events = snap.d["events"]
    box = snap.d["mail_log"]
    owner = box[nid]
    assert events is snap.d["events"]
    assert owner is box.setdefault(nid, [])
    events.append({"id": "e1"})
    owner.append({"id": "m1"})
    store.save_org(snap)
    got = store.load_org_snapshot(slug, ("events", "mail_log"))
    assert ids(got.d["events"]) == ["e0", "e1"]
    assert ids(got.d["mail_log"][nid]) == ["m0", "m1"]


def unmarked_constructor_read_is_bound_to_snapshot() -> None:
    slug, nid = fresh("snapshot unmarked backfill")
    o = store.load_org(slug)
    o.d["mail_log"] = {nid: [{"from": USER, "body": "legacy", "at": "t"}]}
    o.d["_migrations"].pop("mail_log_ids", None)
    store.save_org(o)

    seen: list[str] = []
    with store._POOL.acquire(slug) as conn:
        conn.set_trace_callback(seen.append)
    snap = store.load_org_snapshot(slug, ("org_inbox",))
    # The omitted mail_log is deliberately preloaded because Org.__init__
    # will backfill it. It must not become an undeclared later connection read.
    assert dict.__contains__(snap.d, "mail_log")
    assert snap.d["mail_log"][nid][0].get("id")
    # A pooled trace is only a positive observation if it saw the main load too.
    assert any("SELECT key, val FROM doc" in sql for sql in seen)
    assert any("FROM log_d" in sql and "owner, seq, val" in sql for sql in seen)


def ordinary_owner_read_remains_selective() -> None:
    slug, _ = fresh("snapshot s1 selective")
    o = store.load_org(slug)
    o.d["mail_log"] = {"good": [{"id": "ok"}], "poison": [{"id": "bad"}]}
    store.save_org(o)
    with sqlite3.connect(store._db_path(slug)) as conn:
        conn.execute("UPDATE log_d SET val='not-json' WHERE sect='mail_log' AND owner='poison'")
    ordinary = store.load_org(slug)
    assert ids((ordinary.d.get("mail_log") or {}).get("good")) == ["ok"]


def route_selectors_and_return_shapes() -> None:
    slug, nid = fresh("snapshot route selectors")
    calls: list[tuple[str, ...]] = []
    real = store.load_org_snapshot

    def recording(got_slug: str, sections):
        calls.append(tuple(sections))
        return real(got_slug, sections)

    store.load_org_snapshot = recording  # type: ignore[assignment]
    try:
        assert set(api.user_inbox(slug)) == {"pending", "delivered", "sent"}
        assert set(api.org_inbox_entries(slug)) == {"entries", "total", "unread"}
        assert set(api.node_inbox(slug, nid)) == {"pending", "delivered", "sent"}
        req = Request({"type": "http", "method": "GET", "path": "/",
                       "headers": [], "query_string": b"",
                       "client": ("127.0.0.1", 1), "server": ("test", 80),
                       "scheme": "http"})
        assert "org_inbox" in api.org_tree(slug, req)
    finally:
        store.load_org_snapshot = real  # type: ignore[assignment]
    assert calls == [
        ("user_mail_log", "user_outbox"),
        ("org_inbox",),
        ("mail_log", "user_mail_log"),
        ("org_inbox",),
    ]


def json_backend_keeps_whole_document_behavior() -> None:
    slug = "snapshot-json"
    old_backend = store.STORE_BACKEND
    store.STORE_BACKEND = "json"
    try:
        org = store.create_org(slug)
        org.d["org_inbox"] = [{"id": "j0"}]
        store.save_org(org)
        got = store.load_org_snapshot(slug, ("org_inbox",))
        assert got.d["org_inbox"] == [{"id": "j0"}]
        assert type(got.d) is dict
        got.d["org_inbox"].append({"id": "j1"})
        store.save_org(got)
        assert ids(store.load_org(slug).d["org_inbox"]) == ["j0", "j1"]
    finally:
        store.STORE_BACKEND = old_backend


def failed_preload_rolls_back_connection() -> None:
    slug, _ = fresh("snapshot rollback")
    o = store.load_org(slug)
    o.d["events"] = [{"id": "e"}]
    store.save_org(o)
    real = store._read_list_log

    def fail(conn, sect):
        assert conn.in_transaction
        raise RuntimeError("injected snapshot read failure")

    store._read_list_log = fail  # type: ignore[assignment]
    try:
        try:
            store.load_org_snapshot(slug, ("events",))
            raise AssertionError("injected failure did not fire")
        except RuntimeError as exc:
            assert str(exc) == "injected snapshot read failure"
    finally:
        store._read_list_log = real  # type: ignore[assignment]
    # Pool reuse proves _load_lazy did not check in an open transaction.
    assert ids(store.load_org(slug).d["events"]) == ["e"]


def invalid_snapshot_sections_refuse() -> None:
    slug, _ = fresh("snapshot validation")
    for sections, exc_type in [(("mail",), ValueError), ("mail_log", TypeError)]:
        try:
            store.load_org_snapshot(slug, sections)
            raise AssertionError(f"accepted invalid sections {sections!r}")
        except exc_type:
            pass


if __name__ == "__main__":
    for label, fn in [
        ("selected route stays at revision A across a concurrent B commit",
         coherent_route_snapshot_straddles_a_writer),
        ("selected present-empty and absent sections stay at their revision",
         selected_empty_and_absent_are_frozen),
        ("preloaded mutable lists retain identity and save baselines",
         preloaded_mutables_keep_save_baselines),
        ("Org.__init__ unmarked mail-id read is included in the transaction",
         unmarked_constructor_read_is_bound_to_snapshot),
        ("ordinary S1 owner read stays selective with poison control",
         ordinary_owner_read_remains_selective),
        ("the four API projections declare their exact snapshot sections",
         route_selectors_and_return_shapes),
        ("JSON snapshot entry point keeps whole-document load/save behavior",
         json_backend_keeps_whole_document_behavior),
        ("selected-section read failure rolls back before pool reuse",
         failed_preload_rolls_back_connection),
        ("invalid and string section declarations refuse",
         invalid_snapshot_sections_refuse),
    ]:
        check(label, fn)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed (data root: {store.DATA_ROOT})")
    for label, tb in FAIL:
        print(f"\nFAIL {label}\n{tb}")
    raise SystemExit(1 if FAIL else 0)
