"""SQLite storage backend (SQLITE-SPEC Phase 1) — store.py under ORGTREE_STORE=sqlite.

    python backend/tests/test_sqlite_store.py            # everything (~seconds)
    python backend/tests/test_sqlite_store.py --quick    # same, smaller fixtures

What this suite pins, section by section (the spec section each answers to):

  1. LazyDoc — the §4.2 hazard table, method by method. `.get()`, `in`,
     `setdefault`, `pop`, whole-doc walks, `json.dumps`, `copy.deepcopy`, and
     the three the spec calls out as UNSAFE for a dict subclass (`{**d}`,
     `dict(d)`, `len(d)`) — which this LazyDoc makes complete by overriding
     `__iter__` (CPython's dict-merge fast path is taken only for subclasses
     that keep dict's own `__iter__`). Asserted, not assumed.
  2. Round trip — §6.2/§6.3 on a synthetic document that has every section
     shape the classification names, plus the shapes the live data does NOT
     currently have but JSON permits: an empty lazy section, an owner with an
     empty list, a non-dict entry, an entry without `at`, a lazy-named key of
     the wrong shape, a 1 MB entry, unicode. Canonical equality PLUS the four
     extra assertions, via the same `verify_migration` the backend runs.
  3. Save semantics — compare-on-save: a no-op save writes nothing; a node
     edit/add/remove hits exactly those rows; new nodes get ord=MAX+1 and the
     dict order survives a reload; a popped small key is deleted; a log
     section changes only when its content does; an in-place edit of ONE
     entry (no list method involved) is still persisted; `user_mail_log`'s
     sort-and-cap idiom round-trips; a plain-dict `Org.d` (Org.create) saves
     and reloads as a LazyDoc; a deep-copied LazyDoc saves independently.
  4. Migration mechanics — `.json` → `.db` + `.json.premigration`, the source
     bytes untouched; on-demand migration of a `.json` that appears later; a
     verifier failure leaves the `.json` in place and no `.db`; an
     interrupted final rename is completed on the next start.
  5. delete / restore with WAL sidecars — the file put back IS the restore.
  6. REVISION / on_save / save_hooks — unchanged semantics under sqlite.
  7. Rollback — the same data root read by a fresh interpreter under
     ORGTREE_STORE=json after the `.premigration` file is renamed back.
  8. export_json — the full reconstruction in the old format, canon-equal.

Hermetic: its own ORGTREE_DATA, no network, no CLI, no backend process.
Exit code 1 on any failure; each ✗ line names the check.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback

QUICK = "--quick" in sys.argv

# isolated data root BEFORE any orgtree import — store resolves ORGTREE_DATA
# and ORGTREE_STORE at import time
_TMP = tempfile.mkdtemp(prefix="orgtree-sqlite-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ["ORGTREE_STORE"] = "sqlite"
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB (see
# test_persistence.py) — point it at the discard port like every other rig
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from orgtree import store                                  # noqa: E402
from orgtree.ledger import USER, LedgerError, Org          # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []
_SECTION = [""]


def section(title: str) -> bool:
    _SECTION[0] = title
    print(f"\n== {title}")
    return True


def check(label: str, fn) -> None:
    try:
        fn()
    except BaseException:                                  # noqa: BLE001
        FAIL.append((f"{_SECTION[0]} / {label}", traceback.format_exc()))
        print(f"  ✗      {label}")
        return
    PASS.append(label)
    print(f"  ✓      {label}")


def eq(a, b, what: str = "") -> None:
    assert a == b, f"{what}{a!r} != {b!r}"


def orgs_dir() -> str:
    return os.path.join(store.DATA_ROOT, "orgs")


def wipe(slug: str) -> None:
    for f in os.listdir(orgs_dir()):
        if f == f"{slug}.db" or f.startswith(f"{slug}.db") or f.startswith(f"{slug}.json"):
            store._POOL.close_all(slug)
            os.remove(os.path.join(orgs_dir(), f))


def rows(slug: str, sql: str, *args) -> list:
    c = sqlite3.connect(store._db_path(slug))
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def total_changes_probe(slug: str):
    """A connection whose `total_changes` measures writes made THROUGH IT is
    useless for another connection's writes; use the WAL frame count and the
    data_version pragma instead: data_version changes iff another connection
    committed."""
    c = sqlite3.connect(store._db_path(slug))
    c.execute("PRAGMA journal_mode=WAL")
    v0 = c.execute("PRAGMA data_version").fetchone()[0]

    def changed() -> bool:
        return c.execute("PRAGMA data_version").fetchone()[0] != v0
    return changed, c


def spec(**over):
    s = dict(add_dirs=[], tools={"bash": True, "mcp": ["*"]}, org_visibility="team",
             charter="sqlite test hire")
    s.update(over)
    return s


def fresh(name: str, nodes: int = 0) -> Org:
    """A real org on disk, built through the real ledger (as test_persistence
    does), then RELOADED so the returned doc is a LazyDoc."""
    wipe(name)
    org = store.create_org(name)
    if nodes:
        org.d["max_top_grant"] = 0
        org.hire(USER, None, "opus", 2 * nodes + 200, "ceo")
        for i in range(nodes):
            org.hire("ceo", "ceo", "haiku", 0, f"n{i}", **spec())
    store.save_org(org)
    return store.load_org(name)


BIG = 1_000_000 if not QUICK else 120_000


def synthetic_doc(slug: str) -> dict:
    """Every section shape the classification names, plus the ones JSON
    permits that the live data happens not to have today."""
    nodes = {}
    for i in range(6):
        nid = f"n{i}"
        nodes[nid] = {"id": nid, "state": "archived" if i % 2 else "live",
                      "session_id": f"s-{i}", "generation": i, "lineage": [f"n{j}" for j in range(i)],
                      "bearer_state": {"x": i}, "created": f"2026-09-0{i+1}T00:00:00.000Z",
                      "ui_order": float(i), "scope": {"add_dirs": [], "tools": {"bash": True, "mcp": []}}}
    # deliberately NOT sorted, so order preservation is tested
    nodes = {k: nodes[k] for k in ["n3", "n0", "n5", "n1", "n4", "n2"]}
    return {
        "version": 1, "slug": slug, "name": slug, "created": "2026-09-01T00:00:00.000Z",
        "tiers": {"opus": 5.0, "haiku": 0.25}, "models": {}, "workspace": None,
        "dirs": [], "permission_mode": "acceptEdits",
        "default_tools": {"bash": True, "mcp": ["*"]}, "default_visibility": "full",
        "max_top_grant": 1000, "default_top_grant": 50, "credit_requests": [],
        "compact_at": 0.8, "fable_limit_policy": "halt", "fable_filter_policy": "halt",
        "fable_filter_model": "opus",
        "nodes": nodes,
        "audiences": [], "audience_requests": [],
        "events": [{"at": f"2026-09-01T00:00:{i:02d}.000Z", "kind": "e", "i": i} for i in range(40)]
                  + [{"kind": "no-at-entry"}, "a bare string entry", 42, None, ["nested", "list"]],
        "mail_log": {"n0": [{"at": "2026-09-01T00:00:00.000Z", "id": "m1", "body": "héllo ✓ 日本"},
                            {"at": "2026-09-01T00:00:01.000Z", "id": "m2", "body": "x" * BIG}],
                     "n1": [],                                   # empty owner
                     "n2": [{"at": "2026-09-01T00:00:02.000Z", "id": "m3"}]},
        "steered_log": {"n0": [{"at": "2026-09-01T00:00:00.000Z", "text": "steer"}]},
        "turn_error_log": {},                                    # empty dict log
        "org_inbox": [],                                         # empty list log
        "notice_log": [{"at": "t", "n": i} for i in range(10)],
        "user_mail_log": [{"at": f"2026-09-01T00:00:{i:02d}.000Z", "kind": "notice", "id": f"u{i}"}
                          for i in (3, 1, 2)],
        "user_outbox": [{"at": "t", "to": "n0"}],
        "mail": {"n0": [{"id": "queued"}]}, "notices": {"n0": []},
        "_actors_typed": {"n0": True}, "_migrations": ["a", "b"],
        "kiosk": None, "floats": [0.1, 1e-7, 12345678901234567890, -0.0, 3.0],
    }


# ===========================================================================
def s1_lazydoc() -> None:
    if not section("1. LazyDoc — the §4.2 hazard table"):
        return
    slug = "lazy"
    o = fresh(slug)
    o.d["events"] = [{"at": "t", "k": 1}]
    o.d["mail_log"] = {"a": [{"at": "t", "id": "x"}]}
    o.d["steered_log"] = {"b": [{"at": "t"}]}
    o.d["turn_error_log"] = {"c": [{"at": "t", "text": "boom"}]}
    store.save_org(o)
    full = json.loads(json.dumps(store.load_org(slug).d))

    def loaded_is_lazy() -> None:
        d = store.load_org(slug).d
        assert isinstance(d, store.LazyDoc)
        # Org.__init__ walks mail_log (ledger.py ~571) so that one is
        # materialised on every load; the others must NOT be
        for k in ("events", "steered_log", "turn_error_log"):
            assert not dict.__contains__(d, k), f"{k} materialised on load"
        assert dict.__contains__(d, "nodes"), "nodes must be eager (§4.3)"
    check("load_org returns a LazyDoc with the logs unmaterialised and nodes eager", loaded_is_lazy)

    def contains_before() -> None:
        d = store.load_org(slug).d
        assert "events" in d and "steered_log" in d
        assert "org_inbox" not in d          # never written for this org
        assert "nonesuch" not in d
        assert not dict.__contains__(d, "events")
    check("`k in d` is True for an unmaterialised section, False for an absent one", contains_before)

    def get_materialises() -> None:
        d = store.load_org(slug).d
        eq(d.get("events")[0]["k"], 1, "events[0].k: ")
        eq(d.get("org_inbox"), None)
        eq(d.get("org_inbox", "dflt"), "dflt")
        assert isinstance(d["steered_log"], store.SectionMap)
        assert isinstance(d["steered_log"]["b"], store.AppendLog)
        assert isinstance(d["events"], store.AppendLog)
    check(".get() materialises (never returns None for a real section)", get_materialises)

    def missing_raises() -> None:
        d = store.load_org(slug).d
        try:
            d["org_inbox"]
            raise AssertionError("KeyError expected")
        except KeyError:
            pass
    check("d[k] raises KeyError for a lazy section the db does not have", missing_raises)

    def setdefault_idiom() -> None:
        d = store.load_org(slug).d
        eq(d.setdefault("events", [])[0]["k"], 1)
        ob = d.setdefault("org_inbox", [])
        assert ob == [] and dict.__contains__(d, "org_inbox")
        mine = []
        eq(d.setdefault("user_outbox", mine) is mine, True, "identity of a caller's default: ")
        d.setdefault("mail_log", {}).setdefault("zz", []).append({"at": "t"})
        eq(d["mail_log"]["zz"], [{"at": "t"}])
    check("setdefault: existing section returned, new one stored, caller's object kept by identity", setdefault_idiom)

    def pop_and_del() -> None:
        d = store.load_org(slug).d
        v = d.pop("steered_log")
        eq(v, {"b": [{"at": "t"}]})
        assert "steered_log" not in d
        eq(d.pop("steered_log", None), None)
        eq(d.pop("nonesuch", 7), 7)
        del d["events"]
        assert "events" not in d
        try:
            d["events"]
            raise AssertionError("popped section must not resurrect")
        except KeyError:
            pass
        d["events"] = [{"fresh": 1}]
        eq(d["events"], [{"fresh": 1}])
        eq(d.pop("account_token_uuid", None), None)     # ledger.py:469 idiom
    check("pop / del on a lazy section: value returned, section gone, does not resurrect", pop_and_del)

    def whole_doc_walks() -> None:
        d = store.load_org(slug).d
        eq(set(d.keys()), set(full), "keys(): ")
        d = store.load_org(slug).d
        eq({k for k, _ in d.items()}, set(full), "items(): ")
        d = store.load_org(slug).d
        eq(len(list(d.values())), len(full), "values(): ")
        d = store.load_org(slug).d
        eq(set(iter(d)), set(full), "__iter__: ")
        d = store.load_org(slug).d
        eq(len(d), len(full), "len(): ")
    check("keys / items / values / iter / len see the whole document", whole_doc_walks)

    def dumps_and_deepcopy() -> None:
        d = store.load_org(slug).d
        eq(store.canon(json.loads(json.dumps(d))), store.canon(full), "json.dumps: ")
        d = store.load_org(slug).d
        c = copy.deepcopy(d)
        assert type(c) is store.LazyDoc and c._slug == slug
        eq(store.canon(c), store.canon(full), "deepcopy content: ")
        assert c["nodes"] is not d["nodes"], "deepcopy must not alias"
        eq(sorted(dict.keys(c)), sorted(full), "deepcopy materialised everything: ")
    check("json.dumps and copy.deepcopy route through items() (spec-verified) — asserted here", dumps_and_deepcopy)

    def the_three_unsafe_ones() -> None:
        d = store.load_org(slug).d
        eq(set({**d}), set(full), "{**d}: ")
        d = store.load_org(slug).d
        eq(set(dict(d)), set(full), "dict(d): ")
        d = store.load_org(slug).d
        eq(len(d), len(full), "len(d): ")
    check("{**d}, dict(d), len(d) are COMPLETE for this LazyDoc (overridden __iter__ defeats the fast path)",
          the_three_unsafe_ones)

    def update_routes() -> None:
        d = store.load_org(slug).d
        d.pop("events")
        d.update({"events": [1], "extra": 2})       # api.py:962 idiom
        eq(d["events"], [1])
        eq(d["extra"], 2)
        assert "events" not in d._dropped
    check("update() goes through __setitem__ (a re-added popped section is not deleted on save)", update_routes)

    def eq_repr_copy() -> None:
        d = store.load_org(slug).d
        assert d == full
        d = store.load_org(slug).d
        assert "steered_log" in repr(d)
        d = store.load_org(slug).d
        c = d.copy()
        assert type(c) is dict and set(c) == set(full)
    check("__eq__, __repr__, copy() materialise first", eq_repr_copy)

    def materialize_all() -> None:
        d = store.load_org(slug).d
        d.materialize_all()
        eq(sorted(dict.keys(d)), sorted(full))
        eq(d._unmaterialized(), set())
    check("materialize_all() exists and loads every present section", materialize_all)


# ===========================================================================
def s2_roundtrip() -> None:
    if not section("2. Round trip — §6.2 / §6.3 on a synthetic document"):
        return
    slug = "synth"
    wipe(slug)
    doc = synthetic_doc(slug)
    raw = json.dumps(doc, indent=2).encode("utf-8")
    with open(store._json_path(slug), "wb") as f:
        f.write(raw)

    def migrates() -> None:
        rep = store.migrate_org(slug)
        assert os.path.exists(store._db_path(slug))
        assert os.path.exists(store._premigration_path(slug))
        assert not os.path.exists(store._json_path(slug))
        eq(open(store._premigration_path(slug), "rb").read(), raw, "premigration bytes: ")
        eq(rep["nodes"], 6)
        eq(rep["archived"], 3)
        eq(rep["counts"]["mail_log"], 3)
        eq(rep["counts"]["mail_log.owners"], 3)
        eq(rep["counts"]["events"], 45)
        eq(rep["largest_entry_bytes"] > BIG, True)
    check("migrate_org: .json → .db, .json.premigration byte-identical, report sane", migrates)

    def canonical() -> None:
        c = sqlite3.connect(store._db_path(slug))
        try:
            rec = store.reconstruct_full(c)
        finally:
            c.close()
        eq(store.canon(rec), store.canon(doc), "canon: ")
        eq(list(rec), list(doc), "top-level key ORDER: ")
        eq(list(rec["nodes"]), list(doc["nodes"]), "nodes order: ")
        eq(list(rec["mail_log"]), list(doc["mail_log"]), "owner order: ")
        eq(rec["mail_log"]["n1"], [], "empty owner survives: ")
        eq(rec["turn_error_log"], {}, "empty dict log survives: ")
        eq(rec["org_inbox"], [], "empty list log survives: ")
        eq(rec["events"][-4:], ["a bare string entry", 42, None, ["nested", "list"]], "non-dict entries: ")
        eq(rec["mail_log"]["n0"][1]["body"], "x" * BIG, "big entry: ")
    check("reconstruct_full is canon-equal AND order-faithful (keys, nodes, owners, empties)", canonical)

    def loaded_equals() -> None:
        d = store.load_org(slug).d
        # Org.__init__ normalises nodes (scope defaults etc.) — compare
        # against the same normalisation applied to the source
        norm = Org(copy.deepcopy(doc)).d
        eq(store.canon(d), store.canon(norm), "LazyDoc == Org(source).d: ")
    check("load_org's LazyDoc equals Org(source).d canonically", loaded_equals)

    def turn_error_log_shape() -> None:
        eq(rows(slug, "SELECT COUNT(*) FROM log_d WHERE sect='turn_error_log'")[0][0], 0)
        eq(rows(slug, "SELECT COUNT(*) FROM log_l WHERE sect='turn_error_log'")[0][0], 0)
        eq(rows(slug, "SELECT val FROM meta WHERE key='owners:turn_error_log'"), [("[]",)])
        eq(store.DICT_LOGS, ("mail_log", "steered_log", "turn_error_log"))
        eq(store.LIST_LOGS, ("events", "org_inbox", "notice_log", "user_mail_log", "user_outbox"))
    check("turn_error_log is classified as a DICT log (§3.2) — and empty sections leave a marker, not rows",
          turn_error_log_shape)

    def at_column() -> None:
        eq(rows(slug, "SELECT at FROM log_l WHERE sect='events' ORDER BY seq LIMIT 1"),
           [("2026-09-01T00:00:00.000Z",)])
        eq(rows(slug, "SELECT COUNT(*) FROM log_l WHERE sect='events' AND at IS NULL")[0][0], 5)
    check("`at` column populated from entry['at'] when a string, NULL otherwise", at_column)

    def verifier_catches() -> None:
        c = sqlite3.connect(store._db_path(slug))
        try:
            bad = copy.deepcopy(doc)
            bad["mail_log"]["n2"][0]["id"] = "TAMPERED"
            try:
                store.verify_migration(c, bad)
                raise AssertionError("verifier passed a tampered source")
            except store.MigrationError:
                pass
            bad = copy.deepcopy(doc)
            bad["nodes"] = {k: bad["nodes"][k] for k in reversed(list(bad["nodes"]))}
            # canon-equal (sort_keys!) but order differs — only assertion 2 sees it
            eq(store.canon(bad), store.canon(doc))
            try:
                store.verify_migration(c, bad)
                raise AssertionError("verifier missed a nodes-order change")
            except store.MigrationError as e:
                assert "order" in str(e)
        finally:
            c.close()
    check("verify_migration trips on a changed entry and on a nodes-order change that canon() cannot see",
          verifier_catches)

    def schema_meta() -> None:
        eq(rows(slug, "SELECT val FROM meta WHERE key='schema_version'"), [("1",)])
        eq(rows(slug, "SELECT val FROM meta WHERE key='source_json_bytes'"), [(str(len(raw)),)])
        assert rows(slug, "SELECT val FROM meta WHERE key='source_json_sha256'")[0][0]
        assert rows(slug, "SELECT val FROM meta WHERE key='migrated_at'")[0][0]
        eq(rows(slug, "PRAGMA journal_mode"), [("wal",)])
        tables = {r[0] for r in rows(slug, "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"doc", "nodes", "log_d", "log_l", "meta"} <= tables, tables
    check("meta rows per §3.1; WAL persistent; the five tables exist", schema_meta)


# ===========================================================================
def s3_save() -> None:
    if not section("3. Save semantics — compare-on-save, Phase 1 full rewrite"):
        return
    slug = "saves"
    o = fresh(slug, nodes=4)
    o.d["events"] = [{"at": f"t{i}", "i": i} for i in range(20)]
    o.d["mail_log"] = {"ceo": [{"at": "t", "id": "a", "body": "one"}],
                       "n0": [{"at": "t", "id": "b"}]}
    o.d["notice_log"] = [{"at": f"t{i}"} for i in range(5)]
    store.save_org(o)

    def noop_writes_nothing() -> None:
        o = store.load_org(slug)
        changed, c = total_changes_probe(slug)
        try:
            store.save_org(o)
            eq(changed(), False, "data_version moved on a no-op save: ")
        finally:
            c.close()
    check("a load → save with no change commits no write", noop_writes_nothing)

    def node_edit_only_that_row() -> None:
        before = {r[0]: r[1] for r in rows(slug, "SELECT id, val FROM nodes")}
        o = store.load_org(slug)
        o.d["nodes"]["n1"]["charter"] = "edited"
        store.save_org(o)
        after = {r[0]: r[1] for r in rows(slug, "SELECT id, val FROM nodes")}
        eq({k for k in after if after[k] != before.get(k)}, {"n1"}, "rows changed: ")
        eq(json.loads(after["n1"])["charter"], "edited")
    check("editing one node updates exactly that row", node_edit_only_that_row)

    def node_add_remove_order() -> None:
        o = store.load_org(slug)
        order0 = list(o.d["nodes"])
        o.hire("ceo", "ceo", "haiku", 0, "late", **spec())
        del o.d["nodes"]["n2"]
        store.save_org(o)
        got = rows(slug, "SELECT id, ord FROM nodes ORDER BY ord")
        ids = [r[0] for r in got]
        eq(ids, [k for k in order0 if k != "n2"] + ["late"], "order after add+remove: ")
        assert got[-1][1] == max(r[1] for r in got), "new node must have ord=MAX+1"
        eq(list(store.load_org(slug).d["nodes"]), ids, "reload order: ")
    check("a new node gets ord=MAX+1, a removed node's row is deleted, dict order survives reload",
          node_add_remove_order)

    def small_key_pop() -> None:
        o = store.load_org(slug)
        o.d["ephemeral"] = {"x": 1}
        store.save_org(o)
        eq(rows(slug, "SELECT val FROM doc WHERE key='ephemeral'"), [('{"x":1}',)])
        o = store.load_org(slug)
        o.d.pop("ephemeral")
        store.save_org(o)
        eq(rows(slug, "SELECT val FROM doc WHERE key='ephemeral'"), [])
        assert "ephemeral" not in store.load_org(slug).d
    check("a small key added is upserted; popped, its row is deleted", small_key_pop)

    def log_untouched_unchanged() -> None:
        o = store.load_org(slug)
        _ = o.d["events"]                              # materialise, do not change
        seqs = [r[0] for r in rows(slug, "SELECT seq FROM log_l WHERE sect='events' ORDER BY seq")]
        store.save_org(o)
        eq([r[0] for r in rows(slug, "SELECT seq FROM log_l WHERE sect='events' ORDER BY seq")], seqs,
           "events rows rewritten although unchanged: ")
    check("a materialised-but-unchanged log section is not rewritten", log_untouched_unchanged)

    def log_append_full_rewrite() -> None:
        o = store.load_org(slug)
        o.d["events"].append({"at": "t99", "i": 99})
        assert o.d["events"].full_rewrite is True, "AppendLog journal flag not set"
        store.save_org(o)
        assert o.d["events"].full_rewrite is False, "flag not reset after save"
        got = [json.loads(r[0]) for r in rows(slug, "SELECT val FROM log_l WHERE sect='events' ORDER BY seq")]
        eq(len(got), 21)
        eq(got[-1], {"at": "t99", "i": 99})
        eq(got[0], {"at": "t0", "i": 0})
        eq(store.load_org(slug).d["events"], got, "reload: ")
    check("append to a list log: persisted (Phase 1 rewrites the section), order intact", log_append_full_rewrite)

    def in_place_entry_edit() -> None:
        o = store.load_org(slug)
        o.d["mail_log"]["ceo"][0]["read"] = True       # no list method involved
        assert o.d["mail_log"]["ceo"].full_rewrite is False, "journal cannot see this — and must not need to"
        store.save_org(o)
        eq(store.load_org(slug).d["mail_log"]["ceo"][0].get("read"), True)
    check("an in-place edit of ONE entry (invisible to the journal) is still persisted", in_place_entry_edit)

    def dict_log_owner_ops() -> None:
        o = store.load_org(slug)
        box = o.d["mail_log"]
        box["n0-renamed"] = box.pop("n0")                # ledger.py:6337 idiom
        box.pop("ceo", None)                             # ledger.py:3478 idiom
        box.setdefault("brand-new", []).append({"at": "t", "id": "c"})
        store.save_org(o)
        owners = {r[0] for r in rows(slug, "SELECT DISTINCT owner FROM log_d WHERE sect='mail_log'")}
        eq(owners, {"n0-renamed", "brand-new"})
        eq(json.loads(rows(slug, "SELECT val FROM meta WHERE key='owners:mail_log'")[0][0]),
           ["n0-renamed", "brand-new"])
        d = store.load_org(slug).d
        eq(d["mail_log"]["n0-renamed"], [{"at": "t", "id": "b"}])
        eq(d["mail_log"]["brand-new"], [{"at": "t", "id": "c"}])
        assert "ceo" not in d["mail_log"]
    check("dict log: pop(owner), rename via box[new]=box.pop(old), setdefault+append all persist", dict_log_owner_ops)

    def only_changed_owner_rewritten() -> None:
        o = store.load_org(slug)
        o.d["mail_log"]["brand-new"].append({"at": "t2", "id": "d"})
        keep = rows(slug, "SELECT seq FROM log_d WHERE sect='mail_log' AND owner='n0-renamed'")
        store.save_org(o)
        eq(rows(slug, "SELECT seq FROM log_d WHERE sect='mail_log' AND owner='n0-renamed'"), keep,
           "untouched owner's rows were rewritten: ")
        eq(rows(slug, "SELECT COUNT(*) FROM log_d WHERE sect='mail_log' AND owner='brand-new'")[0][0], 2)
    check("dict log: only the changed owner's rows are rewritten", only_changed_owner_rewritten)

    def user_mail_log_sort_cap() -> None:
        o = store.load_org(slug)
        log = o.d.setdefault("user_mail_log", [])
        for i in range(110):
            log.append({"at": f"2026-09-01T00:{(109 - i) // 60:02d}:{(109 - i) % 60:02d}.000Z",
                        "kind": "notice", "id": f"u{i}"})
        log.sort(key=lambda m: m.get("at") or "")     # ledger.py:1513
        del log[:-100]                                 # ledger.py:1514
        store.save_org(o)
        got = store.load_org(slug).d["user_mail_log"]
        eq(len(got), 100)
        eq([m["at"] for m in got], sorted(m["at"] for m in got), "chronological: ")
        eq(got[0]["id"], "u99")
    check("user_mail_log sort-then-cap idiom (§3.4 #2) round-trips through the full rewrite", user_mail_log_sort_cap)

    def head_truncate() -> None:
        o = store.load_org(slug)
        nl = o.d["notice_log"]
        nl.append({"at": "t5"})
        del nl[:-3]                                    # ledger.py:2433 idiom
        store.save_org(o)
        eq(store.load_org(slug).d["notice_log"], [{"at": "t3"}, {"at": "t4"}, {"at": "t5"}])
    check("del log[:-N] head truncation round-trips", head_truncate)

    def section_dropped() -> None:
        o = store.load_org(slug)
        o.d.pop("notice_log")
        store.save_org(o)
        eq(rows(slug, "SELECT COUNT(*) FROM log_l WHERE sect='notice_log'")[0][0], 0)
        assert "notice_log" not in store.load_org(slug).d
        assert "notice_log" not in json.loads(rows(slug, "SELECT val FROM meta WHERE key='key_order'")[0][0])
    check("popping a whole log section deletes its rows and drops it from key_order", section_dropped)

    def wrong_shape_blob() -> None:
        o = store.load_org(slug)
        o.d["org_inbox"] = None                         # JSON permits it; rows cannot hold it
        o.d["turn_error_log"] = "not a dict"
        store.save_org(o)
        d = store.load_org(slug).d
        eq(d["org_inbox"], None)
        eq(d["turn_error_log"], "not a dict")
        o = store.load_org(slug)
        o.d["org_inbox"] = [{"at": "t"}]                # back to rows
        store.save_org(o)
        eq(rows(slug, "SELECT val FROM doc WHERE key='org_inbox'"), [])
        eq(store.load_org(slug).d["org_inbox"], [{"at": "t"}])
    check("a lazy-named key of the wrong shape is stored faithfully as a blob and back", wrong_shape_blob)

    def plain_dict_save() -> None:
        wipe("plain")
        org = Org.create("plain", [], "acceptEdits", workspace=None)
        org.d["events"] = [{"at": "t"}]
        org.d["mail_log"] = {"x": [{"at": "t"}]}
        assert type(org.d) is dict
        store.save_org(org)                             # creates the db
        org.d["events"].append({"at": "t2"})
        org.d["nodes"]["ghost"] = {"state": "live", "scope": {}}
        store.save_org(org)                             # full reconcile, still a plain dict
        del org.d["nodes"]["ghost"]
        org.d.pop("mail_log")
        store.save_org(org)
        d = store.load_org("plain").d
        assert isinstance(d, store.LazyDoc)
        eq(d["events"], [{"at": "t"}, {"at": "t2"}])
        assert "ghost" not in d["nodes"] and "mail_log" not in d
        eq(rows("plain", "SELECT COUNT(*) FROM log_d")[0][0], 0)
    check("a plain-dict Org.d (Org.create) saves repeatedly and reloads as a LazyDoc", plain_dict_save)

    def deepcopy_saves() -> None:
        o = store.load_org(slug)
        snap = copy.deepcopy(o.d)
        o.d["nodes"]["n0"]["charter"] = "mutated after snapshot"
        o.d = snap                                       # ledger.py:4276 rollback idiom
        store.save_org(o)
        eq(store.load_org(slug).d["nodes"]["n0"].get("charter"), "sqlite test hire")
    check("a deep-copied LazyDoc rebound as Org.d saves the snapshot (batch_move rollback)", deepcopy_saves)

    def double_save_same_object() -> None:
        o = store.load_org(slug)
        o.d["events"].append({"at": "x1"})
        store.save_org(o)
        o.d["events"].append({"at": "x2"})
        o.d["nodes"]["n0"]["k"] = 1
        store.save_org(o)
        d = store.load_org(slug).d
        eq(d["events"][-2:], [{"at": "x1"}, {"at": "x2"}])
        eq(d["nodes"]["n0"]["k"], 1)
    check("two saves of the same Org object both land (snapshot adopted after commit)", double_save_same_object)

    def rollback_on_failure() -> None:
        o = store.load_org(slug)
        o.d["nodes"]["n0"]["k"] = 2
        o.d["bad"] = {1, 2}                              # not JSON-serialisable
        try:
            store.save_org(o)
            raise AssertionError("save of an unserialisable value succeeded")
        except TypeError:
            pass
        eq(store.load_org(slug).d["nodes"]["n0"]["k"], 1, "partial save leaked: ")
        assert "bad" not in store.load_org(slug).d
        o.d.pop("bad")
        store.save_org(o)                                # the connection is still usable
        eq(store.load_org(slug).d["nodes"]["n0"]["k"], 2)
    check("a save that raises mid-transaction rolls back completely; the next save works", rollback_on_failure)


# ===========================================================================
def s4_migration_mechanics() -> None:
    if not section("4. Migration mechanics"):
        return

    def on_demand() -> None:
        wipe("latecomer")
        doc = synthetic_doc("latecomer")
        with open(store._json_path("latecomer"), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        names = {r["slug"] for r in store.list_orgs()}
        assert "latecomer" in names, names
        assert os.path.exists(store._db_path("latecomer"))
        assert os.path.exists(store._premigration_path("latecomer"))
        eq(len(store.load_org("latecomer").d["nodes"]), 6)
    check("a .json that appears later (restore from a pre-migration trash copy) is migrated on demand",
          on_demand)

    def pending_at_start() -> None:
        wipe("p1")
        wipe("p2")
        for s in ("p1", "p2"):
            with open(store._json_path(s), "w", encoding="utf-8") as f:
                json.dump(synthetic_doc(s), f)
        eq(sorted(store.migrate_pending()), ["p1", "p2"])
        eq(store.migrate_pending(), [])
        for s in ("p1", "p2"):
            assert os.path.exists(store._db_path(s)) and os.path.exists(store._premigration_path(s))
    check("migrate_pending migrates every .json without a .db, once", pending_at_start)

    def verifier_failure_refuses() -> None:
        wipe("refuse")
        doc = synthetic_doc("refuse")
        raw = json.dumps(doc).encode("utf-8")
        with open(store._json_path("refuse"), "wb") as f:
            f.write(raw)
        real = store.verify_migration
        store.verify_migration = lambda conn, original: (_ for _ in ()).throw(
            store.MigrationError("injected"))
        try:
            try:
                store.migrate_org("refuse")
                raise AssertionError("migration succeeded with a failing verifier")
            except store.MigrationError as e:
                assert "injected" in str(e)
        finally:
            store.verify_migration = real
        eq(open(store._json_path("refuse"), "rb").read(), raw, "the .json must be untouched: ")
        assert not os.path.exists(store._db_path("refuse"))
        assert not os.path.exists(store._db_path("refuse") + ".migrating")
        assert not os.path.exists(store._premigration_path("refuse"))
        try:
            store.load_org("refuse")
            raise AssertionError("an unmigratable org loaded")
        except store.MigrationError:
            pass
    check("a verifier failure leaves the .json untouched, no .db, no candidate — and load_org refuses loudly",
          verifier_failure_refuses)

    def interrupted_rename() -> None:
        wipe("interrupted")
        with open(store._json_path("interrupted"), "w", encoding="utf-8") as f:
            json.dump(synthetic_doc("interrupted"), f)
        store.migrate_org("interrupted")
        # simulate the crash window: verified candidate + premigration, no .db
        store._POOL.close_all("interrupted")
        os.replace(store._db_path("interrupted"), store._db_path("interrupted") + ".migrating")
        eq(store.migrate_pending(), [])
        assert os.path.exists(store._db_path("interrupted"))
        assert not os.path.exists(store._db_path("interrupted") + ".migrating")
        eq(len(store.load_org("interrupted").d["nodes"]), 6)
    check("a crash between the two final renames is completed on the next start", interrupted_rename)

    def existing_db_refuses() -> None:
        with open(store._json_path("interrupted"), "w", encoding="utf-8") as f:
            f.write("{}")
        try:
            store.migrate_org("interrupted")
            raise AssertionError("migrated over an existing .db")
        except store.MigrationError:
            pass
        os.remove(store._json_path("interrupted"))
    check("migrate_org refuses to migrate over an existing .db", existing_db_refuses)

    def sidecar_files_not_orgs() -> None:
        names = {r["slug"] for r in store.list_orgs()}
        for n in names:
            assert not n.endswith(("-wal", "-shm", ".migrating", ".premigration")), n
    check("list_orgs never lists a -wal/-shm/.migrating/.premigration file as an org", sidecar_files_not_orgs)


# ===========================================================================
def s5_delete_restore() -> None:
    if not section("5. delete / restore with WAL sidecars"):
        return
    trash = os.path.join(store.DATA_ROOT, "deleted")

    def round_trip() -> None:
        o = fresh("roundtrip", nodes=3)
        o.d["payload"] = "keep me"
        o.d["events"] = [{"at": "t"}]
        store.save_org(o)
        assert os.path.exists(store._db_path("roundtrip") + "-wal"), "WAL sidecar expected before delete"
        n_before = len(o.nodes)
        store.delete_org("roundtrip")
        try:
            store.load_org("roundtrip")
            raise AssertionError("a deleted org still loads")
        except LedgerError:
            pass
        assert not os.path.exists(store._db_path("roundtrip"))
        cands = sorted(f for f in os.listdir(trash) if f.startswith("roundtrip-") and f.endswith(".db"))
        eq(len(cands), 1, "trash copies: ")
        stray = [f for f in os.listdir(orgs_dir()) if f.startswith("roundtrip")]
        eq(stray, [], "sidecars left in orgs/: ")
        os.replace(os.path.join(trash, cands[0]), store.org_path("roundtrip"))
        back = store.load_org("roundtrip")
        eq(back.d["payload"], "keep me")
        eq(back.d["events"], [{"at": "t"}])
        eq(len(back.nodes), n_before)
    check("delete → put the .db back → the org is intact, including the last commit that sat in the WAL",
          round_trip)

    def same_second_collision() -> None:
        for i in range(4):
            o = fresh("samesecond")
            o.d["gen"] = i
            store.save_org(o)
            store.delete_org("samesecond")
        cands = [f for f in os.listdir(trash) if f.startswith("samesecond-")]
        eq(len(cands), 4)
        gens = sorted(sqlite3.connect(os.path.join(trash, f)).execute(
            "SELECT val FROM doc WHERE key='gen'").fetchone()[0] for f in cands)
        eq(gens, ["0", "1", "2", "3"])
    check("rapid delete/recreate keeps every trash copy", same_second_collision)

    def delete_missing() -> None:
        try:
            store.delete_org("never-existed")
            raise AssertionError("deleting a missing org succeeded")
        except LedgerError as e:
            assert "no such org" in str(e).lower()
    check("deleting an org that does not exist refuses", delete_missing)

    def reader_during_delete() -> None:
        """A connection checked out by a reader (№22, outside DOC_LOCK) is
        closed on check-in rather than pooled once delete_org has run."""
        o = fresh("busy")
        store.save_org(o)
        cm = store._POOL.acquire("busy")
        conn = cm.__enter__()
        conn.execute("SELECT 1").fetchone()
        eq(store._POOL.close_all("busy"), 1, "checked-out count: ")
        cm.__exit__(None, None, None)
        try:
            conn.execute("SELECT 1")
            raise AssertionError("a connection checked in after close_all stayed open")
        except sqlite3.ProgrammingError:
            pass
        store.delete_org("busy")
        assert not os.path.exists(store._db_path("busy"))
    check("pool: a connection checked in after close_all is closed, not recycled", reader_during_delete)


# ===========================================================================
def s6_revision_hooks() -> None:
    if not section("6. REVISION / on_save / save_hooks"):
        return

    def revision_counts() -> None:
        o = fresh("rev")
        before = store.REVISION
        for _ in range(7):
            store.save_org(o)                       # no-op saves still count
        eq(store.REVISION - before, 7)
    check("REVISION increments once per save_org, changed or not", revision_counts)

    def hooks_fire() -> None:
        o = fresh("hooks")
        seen: list[str] = []
        prev = store.on_save
        store.on_save = lambda slug: (seen.append("on:" + slug), 1 / 0)[1]
        store.save_hooks.append(lambda slug: seen.append("hook:" + slug))
        try:
            o.d["v"] = 7
            store.save_org(o)
        finally:
            store.on_save = prev
            store.save_hooks.pop()
        eq(seen, ["on:hooks", "hook:hooks"])
        eq(store.load_org("hooks").d["v"], 7)
    check("on_save and save_hooks fire; a throwing hook never fails the write", hooks_fire)


# ===========================================================================
def s7_rollback() -> None:
    if not section("7. Rollback — ORGTREE_STORE=json + the .premigration file"):
        return
    child = r'''
import os, sys, json
os.environ["ORGTREE_DATA"] = sys.argv[1]
os.environ["ORGTREE_STORE"] = "json"
sys.path.insert(0, sys.argv[2])
from orgtree import store
assert store.STORE_BACKEND == "json"
names = sorted(r["slug"] for r in store.list_orgs())
org = store.load_org("synth")
print(json.dumps({"names": names, "nodes": list(org.d["nodes"]), "type": type(org.d).__name__,
                  "path": store.org_path("synth")}))
'''

    def json_mode_reads_premigration() -> None:
        # the operator's rollback: rename the premigration file back
        src = store._premigration_path("synth")
        assert os.path.exists(src)
        shutil.copy(src, store._json_path("synth"))
        try:
            out = subprocess.run([sys.executable, "-c", child, store.DATA_ROOT,
                                  os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")],
                                 capture_output=True, text=True, timeout=120)
            assert out.returncode == 0, out.stderr[-2000:]
            res = json.loads(out.stdout.strip().splitlines()[-1])
            assert "synth" in res["names"], res
            for n in res["names"]:
                assert not n.endswith(".premigration"), res
            eq(res["nodes"], ["n3", "n0", "n5", "n1", "n4", "n2"])
            eq(res["type"], "dict")
            assert res["path"].endswith("synth.json")
        finally:
            os.remove(store._json_path("synth"))
    check("a fresh interpreter under ORGTREE_STORE=json reads the restored .json, ignores the .db",
          json_mode_reads_premigration)

    def bad_backend_value() -> None:
        out = subprocess.run([sys.executable, "-c",
                              "import os,sys; os.environ['ORGTREE_STORE']='mongo'; "
                              "sys.path.insert(0, sys.argv[1]); import orgtree.store",
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode != 0 and "ORGTREE_STORE" in out.stderr
    check("an unknown ORGTREE_STORE value refuses at import (never a silent fallback)", bad_backend_value)


# ===========================================================================
def s8_export() -> None:
    if not section("8. export_json"):
        return

    def export_matches() -> None:
        doc = synthetic_doc("synth")
        p = store.export_json("synth", os.path.join(_TMP, "synth-export.json"))
        exp = json.load(open(p, encoding="utf-8"))
        eq(store.canon(exp), store.canon(doc))
        eq(list(exp), list(doc), "key order: ")
        p2 = store.export_json("synth")
        assert p2.startswith(os.path.join(store.DATA_ROOT, "exports")), p2
        assert not os.path.dirname(p2).endswith("orgs")
    check("export_json writes the canonical document; default destination is outside orgs/", export_matches)


# ===========================================================================
if __name__ == "__main__":
    for fn in (s1_lazydoc, s2_roundtrip, s3_save, s4_migration_mechanics,
               s5_delete_restore, s6_revision_hooks, s7_rollback, s8_export):
        try:
            fn()
        except BaseException:                              # noqa: BLE001
            FAIL.append((f"{_SECTION[0]} (section aborted)", traceback.format_exc()))
            print(f"  XX     section aborted: {_SECTION[0]}")
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed  (data root: {store.DATA_ROOT})")
    for label, tb in FAIL:
        print(f"\n✗ {label}\n{tb}")
    sys.exit(1 if FAIL else 0)
