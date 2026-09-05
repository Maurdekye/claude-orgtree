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
import pickle
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback

QUICK = "--quick" in sys.argv

# the ✓/✗ below are UnicodeEncodeError on a cp1252 console. `run_tests.py`
# sets PYTHONIOENCODING=utf-8 for its children, so only the documented
# by-hand invocation ever hit it -- and it hit it in the summary printer,
# after the results, replacing every failure detail with a codec traceback.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# isolated data root BEFORE any orgtree import — store resolves ORGTREE_DATA
# and ORGTREE_STORE at import time
_TMP = tempfile.mkdtemp(prefix="orgtree-sqlite-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ["ORGTREE_STORE"] = "sqlite"
# this suite pins the migration MECHANICS, so it holds the operator's opt-in
# for its own throwaway root; the gate itself (what may START a migration,
# and that nothing is written when it is withheld) is test_migration_gate.py
os.environ["ORGTREE_MIGRATE"] = "1"
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB (see
# test_persistence.py) — point it at the discard port like every other rig
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from orgtree import store                                  # noqa: E402
from orgtree.ledger import TOOL_KEYS, USER, LedgerError, Org   # noqa: E402

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
    # `store._orgs_dir()` makes the directory; this helper reads DATA_ROOT
    # directly, so on the very first call there is nothing to list yet
    os.makedirs(orgs_dir(), exist_ok=True)
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
    # ⚠ `tools` must name EVERY key in ledger.TOOL_KEYS plus mcp — hires have
    # no defaults, and a short dict raises "agent hires have no defaults".
    # Built from the constant so this cannot drift out of date again.
    s = dict(add_dirs=[],
             tools={**{k: True for k in TOOL_KEYS}, "mcp": ["*"]},
             org_visibility="team", charter="sqlite test hire")
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
        "_actors_typed": {"n0": True},
        # ⚠ `_migrations` carries a LIST here originally, to prove the store
        # is shape-agnostic. It cannot any more: `46dc81e` gave the key
        # meaning, and `Org.__init__` now does `migs[marker] = …` on it, so a
        # non-dict makes the org UNLOADABLE (`TypeError`) — on BOTH backends,
        # which I verified against the JSON one before changing this. The
        # store property is real; the vehicle was wrong. It moves to a key
        # nothing interprets, and this one keeps a plausible marker.
        "_migrations": {"pm_plan_stamp_heal": {"at": "2026-08-01T00:00:00.000Z",
                                               "healed": []}},
        "_shape_zoo": ["a", "b", {"c": 1}, None, 42, [1, [2, [3]]], ""],
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
        # ⚠ …and `Org.__init__` also STAMPS. Since 46dc81e the `mail_log_ids`
        # migration writes `_migrations.mail_log_ids.at = now()`, so two
        # constructions disagree whenever they straddle a millisecond — and
        # they do: this check failed in 3 runs out of 5 for that reason alone
        # (sqlite-review, 2026-09-04; the flake is older than this branch —
        # it needs only 46dc81e and this check, and I had two clean runs
        # earlier tonight and believed them). The property here is that the
        # STORE round-trips the document, not that two clocks agree. Normalise
        # the stamps and nothing else: every other byte still has to match.
        for x in (d, norm):
            for m in (x.get("_migrations") or {}).values():
                if isinstance(m, dict) and "at" in m:
                    m["at"] = "<stamp>"
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
    if not section("3. Save semantics — compare-on-save and row reconciliation"):
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

    def log_append_incremental() -> None:
        o = store.load_org(slug)
        # ⚠ plant the section rather than inherit it: `node_add_remove_order`
        # above hires, and `hire` appends an event of its own
        o.d["events"] = [{"at": f"t{i}", "i": i} for i in range(20)]
        store.save_org(o)
        o = store.load_org(slug)
        before = rows(slug, "SELECT seq FROM log_l WHERE sect='events' ORDER BY seq")
        o.d["events"].append({"at": "t99", "i": 99})
        assert o.d["events"].full_rewrite is False, "append wrongly forced a rewrite"
        store.save_org(o)
        assert o.d["events"].full_rewrite is False, "flag not reset after save"
        eq(rows(slug, "SELECT seq FROM log_l WHERE sect='events' ORDER BY seq")[:-1],
           before, "append replaced existing row identities: ")
        got = [json.loads(r[0]) for r in rows(slug, "SELECT val FROM log_l WHERE sect='events' ORDER BY seq")]
        eq(len(got), 21)
        eq(got[-1], {"at": "t99", "i": 99})
        eq(got[0], {"at": "t0", "i": 0})
        eq(store.load_org(slug).d["events"], got, "reload: ")
    check("append to a list log inserts one tail row and keeps existing identities",
          log_append_incremental)

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
        # same reason as above: a hire earlier in this section wrote notices
        o.d["notice_log"] = [{"at": f"t{i}"} for i in range(5)]
        store.save_org(o)
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
        # ⚠ ASSERT THE MECHANISM RAN. `_write_doc` writes the small sections in
        # insertion order, so a key added BEFORE the unserialisable one really
        # is upserted inside the transaction that then dies. Without it the
        # check was vacuous: `bad` raised before anything had been written, so
        # the transaction was empty and COMMIT-instead-of-ROLLBACK passed
        # (measured — mutating ROLLBACK to COMMIT left this check green).
        o.d["written_before_the_failure"] = "must be rolled back"
        o.d["bad"] = {1, 2}                              # not JSON-serialisable
        try:
            store.save_org(o)
            raise AssertionError("save of an unserialisable value succeeded")
        except TypeError:
            pass
        eq(store.load_org(slug).d["nodes"]["n0"]["k"], 1, "partial save leaked: ")
        eq(rows(slug, "SELECT val FROM doc WHERE key='written_before_the_failure'"), [],
           "a row written before the failure was committed anyway: ")
        assert "bad" not in store.load_org(slug).d
        o.d.pop("bad")
        o.d.pop("written_before_the_failure")
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
            eq(open(store._json_path("refuse"), "rb").read(), raw,
               "the .json must be untouched: ")
            assert not os.path.exists(store._db_path("refuse"))
            assert not os.path.exists(store._db_path("refuse") + ".migrating")
            assert not os.path.exists(store._premigration_path("refuse"))
            # ⚠ INSIDE the injection. With the real verifier restored the org
            # is migratable again and loading it is correct — so asserting
            # this after the `finally` asserted nothing at all.
            try:
                store.load_org("refuse")
                raise AssertionError("an unmigratable org loaded")
            except store.MigrationError:
                pass
        finally:
            store.verify_migration = real
        # and once the verifier works again it migrates and loads normally
        eq(store.load_org("refuse").d["slug"], "refuse", "after the fault clears: ")
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

    # ---- a delete either moves the WHOLE org, or leaves the org WHOLE ----
    # Both defects below were proved by `phase1-audit` on b30a361 and both
    # came in with the Finding 9 fix that made the artefacts travel together:
    # the surrounding machinery still reasoned about the `.db` alone.
    # Exercised end to end in probes/p14_delete_atomic.py; these are the two
    # that belong in the suite because they are invariants, not scenarios.

    def locked_artefact_aborts() -> None:
        """A locked `<slug>.json` must abort the delete, not be swallowed.

        It used to move LAST inside `suppress(OSError)`, after the database
        was already in the trash. On Windows an ordinary open read handle
        makes `os.replace` raise; that raise was swallowed; `delete_org`
        returned SUCCESS leaving a bare `<slug>.json` — `pending_migrations`'
        definition of an unmigrated org. The next start REFUSED, naming a
        slug that no longer existed, and the wall's own `ORGTREE_MIGRATE=1`
        remedy rebuilt the org from that stale file.

        Dropping the suppression is not the fix; the destructive half has
        already happened by then. The ORDER is: the artefacts that can block
        a boot move while the database is still in `orgs/`, where a bare
        `.json` beside a `.db` is inert."""
        o = fresh("lockdel", nodes=1)
        store.save_org(o)
        with open(store._json_path("lockdel"), "w", encoding="utf-8") as f:
            f.write('{"marker": "an operator part-way through a rollback"}')
        h = open(store._json_path("lockdel"), "rb")     # noqa: SIM115
        try:
            try:
                store.delete_org("lockdel")
                raise AssertionError(
                    "delete_org reported SUCCESS with a locked .json")
            except PermissionError:
                pass
        finally:
            h.close()
        eq(os.path.exists(store._db_path("lockdel")), True,
           "the database stayed in orgs/: ")
        eq(os.path.exists(store._json_path("lockdel")), True,
           "so did the document: ")
        eq(store.pending_migrations(), [], "nothing became pending: ")
        store.delete_org("lockdel")                     # and it works unlocked
        eq(os.path.exists(store._json_path("lockdel")), False, "after: ")
    check("a locked artefact aborts the delete and leaves the org whole "
          "— never a half-delete that reports success", locked_artefact_aborts)

    def stem_free_for_every_artefact() -> None:
        """The free-stem search must clear every suffix under the stem.

        It tested `<stem>.db` alone. Restore the `.db` out of the trash (the
        documented restore), leave its premigration there, delete again in
        the same second: the `.db` slot is free, the stem is reused, and
        `os.replace` overwrote a premigration holding a whole pre-migration
        history — against the never-overwrite invariant three lines above it."""
        o = fresh("stemdel", nodes=1)
        store.save_org(o)
        with open(store._json_path("stemdel") + ".premigration", "w",
                  encoding="utf-8") as f:
            f.write('{"marker": "FIRST"}')
        store.delete_org("stemdel")
        first = [f for f in os.listdir(trash) if f.startswith("stemdel-")]
        eq(len([f for f in first if f.endswith(".json.premigration")]), 1,
           "the first premigration is in the trash: ")
        # the restore is putting the .db back; its stem-mates stay behind
        for f in first:
            if f.endswith(".db"):
                os.replace(os.path.join(trash, f), store._db_path("stemdel"))
        with open(store._json_path("stemdel") + ".premigration", "w",
                  encoding="utf-8") as f:
            f.write('{"marker": "SECOND"}')
        store.delete_org("stemdel")
        bodies = sorted(
            open(os.path.join(trash, f), encoding="utf-8").read()
            for f in os.listdir(trash)
            if f.startswith("stemdel-") and f.endswith(".json.premigration"))
        eq(len(bodies), 2, "both premigrations survive: ")
        eq(bodies[0] != bodies[1], True, "and they are not the same bytes: ")
        eq(sum('"FIRST"' in b for b in bodies), 1, "the FIRST one survived: ")
    check("a reused trash stem never overwrites an artefact already there "
          "— free means free for every suffix, not just .db",
          stem_free_for_every_artefact)


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
    if not section("7. Rollback — the supported shape, and the refused one"):
        return
    # ⚠ THE CHILD CLAIMS THE ROOT. The previous version did not, and that
    # omission is what let the old fixture "prove" a rollback shape the store
    # now refuses: `claim_data_root` is where the one-active-format-per-root
    # wall lives, so a child that skips it is not testing a rollback, it is
    # testing what happens when you walk around the safety check.
    child = r'''
import os, sys, json
os.environ["ORGTREE_DATA"] = sys.argv[1]
os.environ["ORGTREE_STORE"] = "json"
sys.path.insert(0, sys.argv[2])
from orgtree import store
assert store.STORE_BACKEND == "json"
try:
    store.claim_data_root()
except store.BackendMismatch as e:
    print(json.dumps({"refused": "BackendMismatch", "msg": str(e)}))
    raise SystemExit(0)
names = sorted(r["slug"] for r in store.list_orgs())
org = store.load_org("synth")
print(json.dumps({"refused": None, "names": names, "nodes": list(org.d["nodes"]),
                  "type": type(org.d).__name__, "path": store.org_path("synth")}))
'''

    def _run_child() -> dict:
        out = subprocess.run(
            [sys.executable, "-c", child, store.DATA_ROOT,
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")],
            capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-2000:]
        return json.loads(out.stdout.strip().splitlines()[-1])

    def premigration_beside_a_live_db_is_REFUSED() -> None:
        """The shape this suite used to call "the operator's rollback".

        Copy `.json.premigration` back beside the live `.db` and start JSON:
        that is now refused, and it must be. The `.premigration` is the
        document as it stood BEFORE the migration and holds none of the
        writes SQLite has accepted since, so choosing it silently would be a
        discard dressed as a recovery. The old fixture passed only because
        its child never claimed the root."""
        shutil.copy(store._premigration_path("synth"), store._json_path("synth"))
        try:
            res = _run_child()
            eq(res["refused"], "BackendMismatch",
               "a .json beside a live .db must REFUSE, not be read: ")
            assert "STALE" in res["msg"], \
                "the refusal has to say the .json is stale, or an operator " \
                "looking at a good-looking file will route around it"
        finally:
            os.remove(store._json_path("synth"))
    check("a .premigration restored beside a live .db is REFUSED — it is a "
          "backup, never a rollback", premigration_beside_a_live_db_is_REFUSED)

    def the_supported_rollback_works() -> None:
        """Export, validate, INSTALL, then park — and the order is the point.

        ⚠ INSTALL FIRST, PARK SECOND. The obvious order is the other way and
        it FAILS OPEN: phase1-audit interrupted a park-then-install rollback
        and got orgs parked with no `.json` installed, which SQLite then
        started on cleanly while those orgs were simply GONE — a
        `.json.premigration` alone is not "pending", so the migration wall
        never sees it.

        Installing while the databases are still authoritative is safe
        because a `.json` sitting beside a `.db` is inert, and that is only
        true because the mismatch wall refuses on ANY active database. It
        makes every interruption fail closed: after the install both
        backends disagree safely (SQLite works, JSON refuses), and DURING the
        parking neither can start at all. See probes/c6_rollback_order.py."""
        p = store.export_json("synth")
        doc = json.load(open(p, encoding="utf-8"))
        Org(doc)                                  # validate BEFORE anything
        shutil.copy(p, store._json_path("synth"))     # install FIRST
        Org(json.load(open(store._json_path("synth"), encoding="utf-8")))
        parked = os.path.join(store.DATA_ROOT, "parked")
        os.makedirs(parked, exist_ok=True)
        # the pool still holds a connection from export_json; Windows will not
        # move a file anything holds (measured — it dies PART-WAY through)
        _closed: set[str] = set()
        # ⚠ EVERY database in the root, not just this org's. Parking one and
        # leaving the rest is refused — and correctly so: the invariant is one
        # active format per ROOT, so a rollback is all-or-nothing across it.
        # The first version of this test parked `synth` alone, and the wall
        # fired with "11 SQLite org(s) under a JSON backend". That refusal was
        # right and the test was wrong, which is the same rule the runbook
        # states for steps 4-5.
        moved = []
        for f in sorted(os.listdir(os.path.join(store.DATA_ROOT, "orgs"))):
            if not (f.endswith(".db") or f.endswith(".db-wal")
                    or f.endswith(".db-shm")):
                continue
            slug = f.split(".db")[0]
            if slug not in _closed:
                with store._POOL.acquire(slug) as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                store._POOL.close_all(slug)
                _closed.add(slug)
            src = os.path.join(store.DATA_ROOT, "orgs", f)
            dst = os.path.join(parked, f)
            if os.path.exists(src):
                shutil.move(src, dst)
                moved.append((dst, src))
        try:
            res = _run_child()
            eq(res["refused"], None, f"JSON must start after a real rollback: "
                                     f"{res.get('msg', '')[:200]}")
            assert "synth" in res["names"], res
            for n in res["names"]:
                assert not n.endswith(".premigration"), res
            eq(res["nodes"], ["n3", "n0", "n5", "n1", "n4", "n2"])
            eq(res["type"], "dict")
            assert res["path"].endswith("synth.json")
        finally:
            os.remove(store._json_path("synth"))
            for dst, src in moved:                # put the database back
                shutil.move(dst, src)
    check("the SUPPORTED rollback — export, validate, INSTALL, then park — "
          "starts under JSON and reads it (install-first is the safety)",
          the_supported_rollback_works)

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
def s9_review() -> None:
    """Written by `sqlite-review`, 2026-09-03, closing the gaps MUTATION
    TESTING found in sections 1-8 (probes/p4_mutants.py: 11 of 15 planted
    defects were caught; these are the four that were not, plus a regression
    test for every defect the review fixed).

    Every check below was watched to FAIL against the defect it names before
    it was kept. A check that has never failed is not yet a check."""
    if not section("9. review: mutation gaps and regressions"):
        return

    # ---- the four mutants sections 1-8 did not catch --------------------
    def sectionmap_setdefault_identity() -> None:
        """MUTANT M12, uncaught. `SectionMap.setdefault` must NOT swap the
        caller's list for an `AppendLog` — `lst = []; box.setdefault(n, lst);
        lst.append(x)` has to land. §1 asserts this for `LazyDoc.setdefault`
        and §3 only ever appends to what setdefault RETURNS, so a version
        that stored a copy passed both."""
        slug = "sd-identity"
        o = fresh(slug)
        o.d["mail_log"] = {"a": [{"id": "0"}]}
        store.save_org(o)
        o = store.load_org(slug)
        box = o.d["mail_log"]
        assert isinstance(box, store.SectionMap), type(box)
        mine: list = []
        got = box.setdefault("mine", mine)
        eq(got is mine, True, "setdefault returned a different object: ")
        eq(box["mine"] is mine, True, "the caller's list was not the one stored: ")
        mine.append({"id": "through-my-own-reference"})     # never touches box
        # an existing owner must come back BY IDENTITY too, or the same
        # append-through-my-reference idiom breaks on the second call
        eq(box.setdefault("mine", []) is mine, True, "second setdefault: ")
        store.save_org(o)
        eq(store.load_org(slug).d["mail_log"]["mine"],
           [{"id": "through-my-own-reference"}], "reload: ")
    check("SectionMap.setdefault keeps the CALLER's list, and an append through "
          "the caller's own reference persists", sectionmap_setdefault_identity)

    def second_save_small_keys() -> None:
        """MUTANT M9, uncaught. §3's double-save check touches only `events`
        and `nodes`, so dropping `lazy._snap_doc = new_doc` (the doc-row half
        of the post-commit snapshot) survived it. A key ADDED by save 1 is
        absent from a stale `_snap_doc`, so save 2's delete sweep cannot see
        it — and a key DELETED by save 2 therefore stays on disk."""
        slug = "twosave-doc"
        o = fresh(slug)
        store.save_org(o)
        o = store.load_org(slug)
        o.d["added_in_save_1"] = {"v": 1}
        o.d["also_added"] = 2
        store.save_org(o)
        o.d.pop("added_in_save_1")
        o.d["also_added"] = 3
        o.d["added_in_save_2"] = "x"
        store.save_org(o)
        eq(rows(slug, "SELECT val FROM doc WHERE key='added_in_save_1'"), [],
           "a key added by save 1 and popped by save 2 still has a row: ")
        eq(rows(slug, "SELECT val FROM doc WHERE key='also_added'"), [("3",)])
        d = store.load_org(slug).d
        assert "added_in_save_1" not in d, sorted(dict.keys(d))
        eq(d["added_in_save_2"], "x")
        eq(d["also_added"], 3)
    check("a SECOND save adds, updates and deletes small doc keys correctly "
          "(the doc half of the post-commit snapshot)", second_save_small_keys)

    def delete_with_a_connection_still_open() -> None:
        """MUTANT M10, uncaught. §5's WAL check closes every connection
        first, and closing the LAST connection to a WAL database checkpoints
        it automatically — so `PRAGMA wal_checkpoint(TRUNCATE)` was dead code
        as far as that check could tell. It is not dead when a №22 reader is
        still holding one: without the checkpoint the last committed frames
        sit in a `-wal` the renamed file no longer owns."""
        slug = "wal-open"
        o = fresh(slug)
        o.d["committed_last"] = "must survive the delete"
        store.save_org(o)
        assert os.path.exists(store._db_path(slug) + "-wal"), "expected a WAL"
        trash = os.path.join(store.DATA_ROOT, "deleted")
        # ⚠ MEASURED: on Windows the rename CANNOT proceed while any handle
        # on the database is open, so `delete_org` exhausts its retry budget
        # and RAISES. That is the safe outcome and the one worth pinning —
        # the org must be left whole, with no half-made trash copy. (It also
        # means `PRAGMA wal_checkpoint(TRUNCATE)` is belt-and-braces here:
        # the rename only ever runs once every connection is closed, and
        # closing the last connection to a WAL database checkpoints it
        # anyway. Mutant M10 survives for that reason, not for want of a
        # test — see probes/p4_mutants.py.)
        cm = store._POOL.acquire(slug)
        conn = cm.__enter__()
        conn.execute("SELECT 1").fetchone()
        try:
            store.delete_org(slug)
            raise AssertionError("delete_org renamed a database still open")
        except PermissionError:
            pass
        finally:
            cm.__exit__(None, None, None)
        assert os.path.exists(store._db_path(slug)), "the org was left neither here nor there"
        eq([f for f in os.listdir(trash) if f.startswith(slug + "-")], [],
           "a half-made trash copy: ")
        eq(store.load_org(slug).d["committed_last"], "must survive the delete",
           "the org did not survive the refused delete: ")
        # and once the connection is gone the delete completes and restores
        store.delete_org(slug)
        cands = sorted(f for f in os.listdir(trash)
                       if f.startswith(slug + "-") and f.endswith(".db"))
        eq(len(cands), 1, "trash copies: ")
        os.replace(os.path.join(trash, cands[0]), store.org_path(slug))
        eq(store.load_org(slug).d["committed_last"], "must survive the delete",
           "the last commit did not travel with the renamed database: ")
    check("delete_org refuses rather than half-completing while a connection is "
          "open, and still carries the last commit once it can run",
          delete_with_a_connection_still_open)

    def durability_pragmas() -> None:
        """MUTANT M11, uncaught, and it cannot be caught by observing
        behaviour in-process: `synchronous=NORMAL` only loses data on a power
        cut. This is the SQLite counterpart of test_persistence's
        "save_org fsyncs the temp file before the replace", and like it, it
        pins the mechanism rather than the consequence."""
        slug = "pragmas"
        fresh(slug)
        with store._POOL.acquire(slug) as c:
            eq(c.execute("PRAGMA synchronous").fetchone()[0], 2, "synchronous (2=FULL): ")
            eq(c.execute("PRAGMA journal_mode").fetchone()[0], "wal", "journal_mode: ")
            assert c.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    check("every connection carries synchronous=FULL and journal_mode=wal",
          durability_pragmas)

    # ---- regressions for what the review fixed --------------------------
    def missing_db_is_never_created() -> None:
        """REVIEW FINDING 1 (data loss). `sqlite3.connect(path)` CREATES a
        missing database, and `delete_org` renames the file out from under
        readers by design (№22 reads outside DOC_LOCK). Measured before the
        fix: materialising a still-lazy section after the delete returned an
        EMPTY SectionMap, re-created `orgs/<slug>.db` + `-wal` + `-shm`, and
        `save_org` then wrote that mutilated document — every log section
        gone, no error anywhere — after which `create_org` refused the slug
        as "already exists"."""
        slug = "vanish"
        o = fresh(slug)
        o.d["steered_log"] = {"a": [{"at": "1"}], "b": [{"at": "2"}]}
        store.save_org(o)
        o = store.load_org(slug)
        assert "steered_log" in o.d._unmaterialized(), sorted(o.d._unmaterialized())
        store.delete_org(slug)
        try:
            got = o.d["steered_log"]
            raise AssertionError(
                f"a lazy section materialised out of a deleted database as {got!r}")
        except sqlite3.OperationalError:
            pass
        assert not os.path.exists(store._db_path(slug)), "the database was re-created"
        for side in ("-wal", "-shm"):
            assert not os.path.exists(store._db_path(slug) + side), side
        assert slug not in [r["slug"] for r in store.list_orgs()]
        store.create_org(slug)                      # must not refuse
        store.delete_org(slug)
    check("a read path never creates a missing database (no empty section, no "
          "resurrected .db after delete_org)", missing_db_is_never_created)

    def load_org_reports_a_vanished_org_as_such() -> None:
        """Same window, the other half: `Org.__init__` walks `mail_log` to
        backfill ids (ledger.py:568), which is a SECOND trip to the database
        AFTER `load_org`'s `with` block has closed. Constructing the `Org`
        outside the try let that trip escape as a raw sqlite3 error — and,
        before the fix above, as a bare `KeyError('nodes')` off an empty
        document. A vanished org is a `LedgerError`, as it is under JSON."""
        slug = "vanish2"
        o = fresh(slug)
        o.d["mail_log"] = {"a": [{"id": "1"}]}
        store.save_org(o)
        store._POOL.close_all(slug)
        os.replace(store._db_path(slug), store._db_path(slug) + ".moved")
        for side in ("-wal", "-shm"):
            if os.path.exists(store._db_path(slug) + side):
                os.remove(store._db_path(slug) + side)
        try:
            store.load_org(slug)
            raise AssertionError("a vanished org loaded")
        except LedgerError as e:
            assert "no such org" in str(e).lower(), e
        os.remove(store._db_path(slug) + ".moved")
    check("load_org reports a database that vanished mid-load as a LedgerError, "
          "not a raw sqlite3 error", load_org_reports_a_vanished_org_as_such)

    def corrupt_db_fails_loudly() -> None:
        """REVIEW FINDING 2 (silent wrong answer). JSON gets this for free —
        a truncated or zero-length doc raises `JSONDecodeError` and
        `_scan_orgs` skips it. SQLite does not: a ZERO-LENGTH file is a
        perfectly valid EMPTY database and a file truncated past page 1 often
        reads as one, so before the `schema_version` check `load_org` handed
        back a document with no nodes and no history and `list_orgs` rendered
        the org as REAL AND EMPTY."""
        for label, damage in (
                ("zero-length", lambda p: open(p, "wb").close()),
                ("truncated", lambda p: open(p, "r+b").truncate(
                    max(4096, os.path.getsize(p) // 2))),
                ("not a database", lambda p: open(p, "wb").write(b"nope" * 3000))):
            slug = "corrupt-" + label.split()[0]
            o = fresh(slug)
            o.d["nodes"]["should-not-vanish"] = {"id": "should-not-vanish",
                                                 "state": "archived"}
            store.save_org(o)
            store._POOL.close_all(slug)
            damage(store._db_path(slug))
            store._POOL.close_all(slug)
            try:
                got = store.load_org(slug)
                raise AssertionError(
                    f"a {label} database loaded, as an org with "
                    f"{len(got.d.get('nodes') or {})} nodes")
            except (LedgerError, sqlite3.DatabaseError):
                pass
            names = [r["slug"] for r in store.list_orgs()]
            assert slug not in names, f"{label}: still listed as an org: {names}"
    check("a zero-length, truncated or non-database .db fails loudly and is not "
          "listed as an empty org", corrupt_db_fails_loudly)

    def clear_drops_a_blobbed_section() -> None:
        """REVIEW FINDING 3. `LazyDoc.clear()` marked only `_present` (the
        ROW-backed lazy sections) as dropped. A section whose value had the
        wrong shape lives in `doc` as a blob and is not in `_present`, and
        `_write_doc`'s doc-row delete sweep deliberately skips LAZY_SECTIONS —
        so the blob survived the clear and came back on the next load."""
        slug = "clearblob"
        o = fresh(slug)
        o.d["org_inbox"] = None                     # wrong shape -> a doc blob
        o.d["events"] = [{"at": "t"}]               # rows
        store.save_org(o)
        eq(rows(slug, "SELECT val FROM doc WHERE key='org_inbox'"), [("null",)])
        o = store.load_org(slug)
        o.d.clear()
        # `nodes` back, or `Org.__init__` refuses the reload for its own reasons
        o.d.update({"slug": slug, "name": "cleared", "nodes": {}})
        store.save_org(o)
        eq(rows(slug, "SELECT val FROM doc WHERE key='org_inbox'"), [],
           "a blobbed lazy section survived clear(): ")
        eq(rows(slug, "SELECT COUNT(*) FROM log_l WHERE sect='events'"), [(0,)])
        d = store.load_org(slug).d
        assert "org_inbox" not in d, sorted(dict.keys(d))
        assert "events" not in d, sorted(dict.keys(d))
    check("clear() drops a BLOBBED lazy section's row, not only the row-backed ones",
          clear_drops_a_blobbed_section)

    def premigration_is_never_overwritten() -> None:
        """REVIEW FINDING 4. §6.1 step 6 says code never removes a
        `.premigration` file, and `migrate_org` removed one anyway: the final
        `os.replace(jp, premigration)` overwrites. Reachable exactly on the
        path `_ensure_migrated` exists for — the database goes away, an old
        `.json` is restored by hand, and the second migration lands on top of
        the only pre-migration copy of that org's history.

        ⚠ The precondition used to be built with `store.delete_org(slug)`,
        which left the `.premigration` sitting in `orgs/` — and that was
        REVIEW FINDING 9 (`probes/p11_delete_resurrect.py`): a delete that
        takes the database and leaves the org's document behind. Now that
        delete takes both, the state this check needs has to be built
        explicitly. **The property under test is unchanged** — only the way
        the precondition is reached, which was always incidental to it."""
        slug = "premig"
        with open(store._json_path(slug), "w", encoding="utf-8") as f:
            json.dump(synthetic_doc(slug), f)
        store.migrate_org(slug)
        first = store._premigration_path(slug)
        assert os.path.exists(first)
        marker = open(first, "rb").read()
        # the database goes, the .premigration stays: an operator removing a
        # database by hand, or restoring an old .json over a root whose
        # history was already converted once
        store._POOL.close_all(slug)
        for x in ("", "-wal", "-shm"):
            if os.path.exists(store._db_path(slug) + x):
                os.remove(store._db_path(slug) + x)
        with open(store._json_path(slug), "w", encoding="utf-8") as f:
            json.dump({"slug": slug, "name": "restored by hand"}, f)
        store._ensure_migrated(slug)
        eq(open(first, "rb").read(), marker,
           "the FIRST .premigration was overwritten by the second migration: ")
        extra = [f for f in os.listdir(orgs_dir())
                 if f.startswith(os.path.basename(first)) and f != os.path.basename(first)]
        eq(len(extra), 1, "the second migration's source was not kept: ")
    check("migrate_org never overwrites an existing .json.premigration",
          premigration_is_never_overwritten)

    def delete_takes_the_whole_org() -> None:
        """REVIEW FINDING 9 (`probes/p11_delete_resurrect.py`), found by
        diffing check-level parity in `test_turn_lifecycle`: the live backend
        refused to restart naming two orgs the suite had just DELETED.

        `delete_org` renamed `<slug>.db` and its `-wal`/`-shm` and left every
        JSON-shaped artefact of the same org sitting in `orgs/`. Two costs:

        * `<slug>.json.premigration` exists for EVERY migrated org, so after
          the cutover this was every delete — the deleted org's whole document
          stayed readable in `orgs/` while the trash held only the database.
        * a bare `<slug>.json` (an operator part-way through the documented
          rollback) became "a .json with NO .db" the instant the database
          left, which is exactly `pending_migrations`' definition of an
          unmigrated org. The next start REFUSED, naming a slug that no longer
          existed, and the remedy its own wall printed (`ORGTREE_MIGRATE=1`)
          converted that file into a live database: the deleted org came back.
        """
        trash = os.path.join(store.DATA_ROOT, "deleted")
        slug = "delwhole"
        with open(store._json_path(slug), "w", encoding="utf-8") as f:
            json.dump(synthetic_doc(slug), f)
        store.migrate_org(slug)                  # → .db + .json.premigration
        with open(store._json_path(slug), "w", encoding="utf-8") as f:
            json.dump({"slug": slug, "name": "half-done rollback"}, f)
        before = sorted(f for f in os.listdir(orgs_dir()) if f.startswith(slug))
        eq(before, [f"{slug}.db", f"{slug}.json", f"{slug}.json.premigration"],
           "fixture: ")
        store.delete_org(slug)
        left = sorted(f for f in os.listdir(orgs_dir()) if f.startswith(slug))
        eq(left, [], "delete left part of the org behind in orgs/: ")
        assert slug not in store.pending_migrations(), \
            "a DELETED org is reported as pending migration — the next start refuses"
        gone = sorted(f for f in os.listdir(trash) if f.startswith(slug + "-"))
        for suffix in (".db", ".json", ".json.premigration"):
            assert any(f.endswith(suffix) for f in gone), (suffix, gone)
        # one stem, so the trash entry is still ONE org and putting it back is
        # still the restore
        eq(len({f.split(".")[0] for f in gone}), 1, f"stems in {gone}: ")
    check("delete_org takes the WHOLE org — the .json and the .premigration "
          "travel with the database, so a deleted org is never 'pending' and "
          "never comes back", delete_takes_the_whole_org)

    def premigration_identical_is_not_duplicated() -> None:
        """The other half of REVIEW FINDING 4, found by the migration-kill
        drill (probes/p7_migfail.py) attacking the FIX rather than the
        original defect: never-overwrite, applied naively, means a REPEAT of
        the same migration mints a whole extra copy of the document. The
        drill's restore-and-re-migrate loop left 90 of them. Identical bytes
        are not new history — keep the one copy."""
        slug = "premig-same"
        doc = synthetic_doc(slug)
        for _ in range(4):
            with open(store._json_path(slug), "w", encoding="utf-8") as f:
                json.dump(doc, f)
            store._POOL.close_all(slug)
            for x in ("", "-wal", "-shm"):
                if os.path.exists(store._db_path(slug) + x):
                    os.remove(store._db_path(slug) + x)
            store.migrate_org(slug)
        prem = os.path.basename(store._premigration_path(slug))
        copies = sorted(f for f in os.listdir(orgs_dir()) if f.startswith(prem))
        eq(copies, [prem], "four identical migrations left more than one copy: ")
        eq(json.load(open(store._premigration_path(slug), encoding="utf-8")), doc,
           "the surviving copy is not the source: ")
        eq(store.load_org(slug).d["slug"], slug, "the org still loads: ")
    check("re-migrating identical bytes does not mint a second .premigration",
          premigration_identical_is_not_duplicated)

    def candidate_journal_is_reclaimed() -> None:
        """A killed migration candidate (probes/p7_migfail.py leaves them)
        must not strand files that the next migration inherits.

        ⚠ HONEST NOTE ON WHAT THIS DOES AND DOES NOT PIN. `_remove_db_files`
        was extended to take `-journal` as well as the WAL pair, because
        `journal_mode=WAL` is set AFTER the connection opens and a SIGKILL in
        those first moments leaves a `<slug>.db.migrating-journal`. Removing
        that extension does NOT make this check fail, and the reason is worth
        recording rather than papering over: SQLITE RECLAIMS A STALE JOURNAL
        ITSELF when the database is opened (measured — a hand-planted
        `-journal`, `-wal` and `-shm` are all gone after one open/close). So
        the explicit removal is belt-and-braces, not a defect fix, and the
        mutant that reverts it is EQUIVALENT, like the `wal_checkpoint` one.
        What this check does pin is the property that matters: no candidate
        file survives into the next migration, by whatever means."""
        slug = "candjournal"
        wipe(slug)
        with open(store._json_path(slug), "w", encoding="utf-8") as f:
            json.dump(synthetic_doc(slug), f)
        tmpdb = store._db_path(slug) + ".migrating"
        for side in ("-journal", "-wal", "-shm"):
            with open(tmpdb + side, "wb") as f:      # as a killed attempt left them
                f.write(b"stale")
        store.migrate_org(slug)
        left = sorted(f for f in os.listdir(orgs_dir())
                      if f.startswith(os.path.basename(tmpdb)))
        eq(left, [], "a killed candidate's files survived the next migration: ")
        eq(store.load_org(slug).d["slug"], slug, "the org still migrated: ")
    check("a killed migration candidate's -journal/-wal/-shm are all reclaimed",
          candidate_journal_is_reclaimed)

    def lazydoc_dunders() -> None:
        """REVIEW FINDINGS 5 and 6, both from the §4.2 hazard sweep.
        `__ne__` did `not self.__eq__(other)`, and `dict.__eq__` returns
        NotImplemented against a non-dict — `not NotImplemented` is False
        with a DeprecationWarning, so `doc != 5` answered False. And every
        piece of `LazyDoc` instance state was created only in `__init__`, so
        a reconstruction that fills items BEFORE state — which is exactly
        what `pickle` does, and the opposite of `copy._reconstruct` — died
        with `AttributeError: _dropped`."""
        d = store.load_org("synth").d if os.path.exists(store._db_path("synth")) \
            else store.load_org(fresh("dunders").d["slug"]).d
        eq(d != 5, True, "doc != a non-dict: ")
        eq(d == 5, False)
        eq(d != dict(dict.items(d)) | {k: d[k] for k in store.LAZY_SECTIONS if k in d},
           False, "doc != an equal plain dict: ")
        back = pickle.loads(pickle.dumps(d))
        eq(store.canon(dict(back)), store.canon(dict(d)), "pickle round-trip: ")
        assert isinstance(back, store.LazyDoc)
    check("__ne__ against a non-dict, and a pickle round-trip, both behave",
          lazydoc_dunders)


def s10_empty_is_not_absent() -> None:
    """An EMPTY collection must round-trip as EMPTY, never as ABSENT.

    Found by `phase1-audit` (cross-model, 2026-09-04): a valid org with
    `nodes: {}` — one created and not yet hired into — could not migrate.
    ONE condition was wrong: `_load_lazy`'s `_key_order` RETENTION FILTER
    kept `nodes` only `and node_rows`, so an org with zero node rows lost
    `nodes` from its key order — and `reconstruct_full` walks the key order.
    The section came back missing rather than empty.

    ⚠ The materialisation loop above that filter is NOT gated on `node_rows`
    and never was: it sets `nodes = {}` for any `"nodes"` in the recorded
    order. So `load_org` was always correct and the live path never broke.
    Said plainly because the first report of this defect described both
    conditions as gated, and a reader who believes that will look for a
    second bug in `_load_lazy` that does not exist — or, worse, "fix" the
    materialisation and invent one.

    Two consequences, and only the first is fail-safe:

      * `migrate_org` failed its own verifier — "round-trip mismatch in
        sections: ['nodes']" — leaving the org pending, so `claim_data_root`
        refused to start. Loud, and nothing lost.
      * `export_json`, which IS the documented rollback path, wrote a
        document with NO `nodes` key. `Org.__init__` does `self.d["nodes"]`,
        so that export could not be read back at all. Silent until used.

    The other eight row-backed sections were already safe — `key_order`
    carries their presence when they have no rows — and the checks below say
    so by measurement rather than by assumption. The converse matters as
    much as the fix: a document that genuinely has no `nodes` key must still
    come back without one, or the repair invents a section."""
    if not section("10. empty is not absent (row-backed sections)"):
        return

    def rt(d: dict) -> dict:
        """`d` through the store and back, with nothing cached."""
        wipe("rt")
        conn = store._open_conn(store._db_path("rt"), create=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            store._write_doc(conn, d, None)
            conn.execute("COMMIT")
            return store.reconstruct_full(conn)
        finally:
            conn.close()

    base = Org.create("Empty", [], "acceptEdits", workspace=None).d

    def empty_nodes_survives() -> None:
        d = json.loads(json.dumps(base))
        d["slug"] = "rt"
        eq(d["nodes"], {}, "fixture: a fresh org has no nodes: ")
        got = rt(d)
        eq("nodes" in got, True, "nodes present after the round-trip: ")
        eq(got["nodes"], {}, "and still empty: ")
        eq(store.canon(got), store.canon(d), "whole document: ")
    check("an org with `nodes: {}` round-trips with `nodes: {}`",
          empty_nodes_survives)

    def every_row_backed_section() -> None:
        """The class, not the instance: every section stored as ROWS, empty.
        A plain `doc` key holding `{}` is one JSON blob and cannot vanish;
        only these can."""
        for sect in ("nodes",) + store.DICT_LOGS + store.LIST_LOGS:
            d = json.loads(json.dumps(base))
            d["slug"] = "rt"
            # isolate: without a real node every case fails on `nodes` and
            # says nothing about the section it names
            if sect != "nodes":
                d["nodes"] = {"n0": {"id": "n0", "state": "idle"}}
            d[sect] = {} if sect in store.DICT_LOGS or sect == "nodes" else []
            got = rt(d)
            eq(sect in got, True, f"{sect} present: ")
            eq(store.canon(got), store.canon(d), f"{sect} document: ")
    check("...and so does every other row-backed section, empty", 
          every_row_backed_section)

    def absent_stays_absent() -> None:
        """The converse. `nodes` is kept unconditionally now, so prove the
        keeping is driven by the recorded key order and not by the name."""
        d = json.loads(json.dumps(base))
        d["slug"] = "rt"
        d.pop("nodes", None)
        got = rt(d)
        eq("nodes" in got, False, "a document with no nodes key gains none: ")
        eq(store.canon(got), store.canon(d), "whole document: ")
        conn = store._open_conn(store._db_path("rt"))
        try:
            lazy = store._load_lazy(conn, "rt")
        finally:
            conn.close()
        eq(dict.__contains__(lazy, "nodes"), False, "nor does the lazy load: ")
        eq("nodes" in lazy._key_order, False, "nor the key order: ")
    check("a document with NO `nodes` key still comes back without one",
          absent_stays_absent)

    def null_nodes_is_a_blob() -> None:
        d = json.loads(json.dumps(base))
        d["slug"] = "rt"
        d["nodes"] = None
        got = rt(d)
        eq(got.get("nodes", "<<ABSENT>>"), None, "nodes: null survives: ")
        eq(store.canon(got), store.canon(d), "whole document: ")
    check("`nodes: null` is still stored and returned as a blob",
          null_nodes_is_a_blob)

    def export_of_an_empty_org_reimports() -> None:
        """The consequence that is NOT fail-safe. `export_json` is the
        rollback path; an export that `Org.__init__` cannot read is a
        rollback that does not exist."""
        wipe("emptyorg")
        org = store.create_org("emptyorg")
        store.save_org(org)
        back = store.load_org("emptyorg")
        eq(dict.get(back.d, "nodes", "<<ABSENT>>"), {}, "the live load: ")
        p = store.export_json("emptyorg")
        exported = json.load(open(p, encoding="utf-8"))
        eq("nodes" in exported, True, "the export keeps nodes: ")
        Org(exported)                       # raises KeyError('nodes') if not
    check("a never-hired org exports a document that can be re-imported",
          export_of_an_empty_org_reimports)

    def empty_org_migrates() -> None:
        """End to end, through the real opt-in migration and its verifier."""
        wipe("memptyorg")
        d = json.loads(json.dumps(base))
        d["slug"] = "memptyorg"
        with open(store._json_path("memptyorg"), "w", encoding="utf-8") as f:
            json.dump(d, f)
        store.migrate_org("memptyorg")      # raises MigrationError if not
        eq(os.path.exists(store._db_path("memptyorg")), True, "db written: ")
        eq("memptyorg" in store.pending_migrations(), False, "still pending: ")
        conn = store._open_conn(store._db_path("memptyorg"))
        try:
            eq(store.canon(store.reconstruct_full(conn)), store.canon(d),
               "migrated document: ")
        finally:
            conn.close()
    check("a never-hired org migrates, and its verifier agrees",
          empty_org_migrates)


def s11_desync_guard() -> None:
    section("§11 data root desynchronization guard")

    def desync_predicate_passes_when_env_unset() -> None:
        """When ORGTREE_DATA is unset in environment, the default ~/orgtree root
        is legitimate (the running backend case)."""
        old_env = os.environ.pop("ORGTREE_DATA", None)
        try:
            store._assert_synced_data_root()
        finally:
            if old_env is not None:
                os.environ["ORGTREE_DATA"] = old_env
    check("desync check passes when ORGTREE_DATA is unset (backend default)",
          desync_predicate_passes_when_env_unset)

    def desync_predicate_passes_when_env_matches() -> None:
        """When ORGTREE_DATA matches store.DATA_ROOT, it passes."""
        old_env = os.environ.get("ORGTREE_DATA")
        os.environ["ORGTREE_DATA"] = store.DATA_ROOT
        try:
            store._assert_synced_data_root()
        finally:
            if old_env is not None:
                os.environ["ORGTREE_DATA"] = old_env
            else:
                os.environ.pop("ORGTREE_DATA", None)
    check("desync check passes when ORGTREE_DATA matches store.DATA_ROOT",
          desync_predicate_passes_when_env_matches)

    def desync_predicate_raises_when_env_disagrees() -> None:
        """When ORGTREE_DATA is set to a different path than store.DATA_ROOT, it raises."""
        old_env = os.environ.get("ORGTREE_DATA")
        fake_root = os.path.join(tempfile.gettempdir(), "orgtree-desync-fake")
        os.environ["ORGTREE_DATA"] = fake_root
        try:
            raised = False
            try:
                store._assert_synced_data_root()
            except store.DataRootDesync as e:
                raised = True
                assert "is desynchronized from os.environ['ORGTREE_DATA']" in str(e)
            assert raised, "expected DataRootDesync was not raised!"
        finally:
            if old_env is not None:
                os.environ["ORGTREE_DATA"] = old_env
            else:
                os.environ.pop("ORGTREE_DATA", None)
    check("desync check raises DataRootDesync when ORGTREE_DATA disagrees",
          desync_predicate_raises_when_env_disagrees)

    def import_order_inversion_positive_control() -> None:
        """Exact reproduction of the incident: an interactive probe or script
        imports an orgtree module BEFORE setting ORGTREE_DATA. The write must
        crash loudly with DataRootDesync and create NOTHING in the un-isolated root."""
        backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        child = (
            "import os, sys, tempfile\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "early_root = tempfile.mkdtemp(prefix='orgtree-desync-early-')\n"
            "os.environ['ORGTREE_DATA'] = early_root\n"
            "from orgtree import store\n"
            "late_root = tempfile.mkdtemp(prefix='orgtree-desync-late-')\n"
            "os.environ['ORGTREE_DATA'] = late_root\n"
            "try:\n"
            "    store.create_org('zz-desync-blocked')\n"
            "    sys.exit(0)\n"
            "except store.DataRootDesync:\n"
            "    if os.path.exists(os.path.join(early_root, 'orgs', 'zz-desync-blocked.db')):\n"
            "        sys.exit(43)\n"
            "    sys.exit(42)\n"
            "except Exception as e:\n"
            "    sys.exit(1)\n"
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("ORGTREE_")}
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run(
            [sys.executable, "-c", child, backend_dir],
            capture_output=True, text=True, env=env, timeout=15
        )
        eq(r.returncode, 42, f"import-order inversion should exit 42 via DataRootDesync, got rc={r.returncode}\n{r.stderr}")
    check("positive control: import-order inversion crashes loudly with DataRootDesync",
          import_order_inversion_positive_control)


# ===========================================================================
def s12_selective_incremental_logs() -> None:
    if not section("12. S1 owner-selective reads and incremental log writes"):
        return

    def owner_read_does_not_parse_another_owner() -> None:
        slug = "s1-owner-read"
        o = fresh(slug)
        o.d["mail_log"] = {
            "wanted": [{"id": "good", "at": "t"}],
            "poison": [{"id": "will-be-corrupted", "at": "t"}],
        }
        store.save_org(o)
        # A corrupt unrelated row is a behavioural materialisation detector:
        # the wanted owner must remain readable, while the positive control
        # proves that touching poison really does parse and reject that row.
        with sqlite3.connect(store._db_path(slug)) as c:
            c.execute("UPDATE log_d SET val='{not-json' "
                      "WHERE sect='mail_log' AND owner='poison'")
        traced: list[str] = []
        with store._POOL.acquire(slug) as c:
            c.set_trace_callback(traced.append)
        try:
            o = store.load_org(slug)
            log = (o.d.get("mail_log") or {}).get("wanted")
            box = dict.__getitem__(o.d, "mail_log")
            eq(log, [{"id": "good", "at": "t"}])
        finally:
            with store._POOL.acquire(slug) as c:
                c.set_trace_callback(None)
        eq(set(box._snaps), {"wanted"}, "loaded row baselines: ")
        owner_selects = [q for q in traced if
                         "SELECT seq, val FROM log_d" in q and
                         "owner='wanted'" in q]
        eq(len(owner_selects), 1, "selective owner query count: ")
        if any("GROUP BY owner" in q or
               "SELECT owner, seq, val FROM log_d" in q for q in traced):
            raise AssertionError(f"hot read scanned unrelated owners: {traced}")
        try:
            box["poison"]
            raise AssertionError("positive control: corrupt owner parsed successfully")
        except json.JSONDecodeError:
            pass
    check("reading one dict-log owner does not parse another; corrupt-owner control fires",
          owner_read_does_not_parse_another_owner)

    def append_is_one_row_write() -> None:
        slug = "s1-one-append"
        o = fresh(slug)
        o.d["mail_log"] = {
            "owner": [{"id": str(i), "at": f"t{i}"} for i in range(62)],
            "other": [{"id": f"x{i}", "at": f"x{i}"} for i in range(62)],
        }
        store.save_org(o)
        o = store.load_org(slug)
        log = o.d["mail_log"]["owner"]
        with store._POOL.acquire(slug) as c:
            before = c.total_changes
        log.append({"id": "63", "at": "t63"})
        store.save_org(o)
        with store._POOL.acquire(slug) as c:
            delta = c.total_changes - before
        eq(delta, 1, "SQL rows changed by one append: ")
        eq(len(rows(slug, "SELECT seq FROM log_d WHERE sect='mail_log' AND owner='owner'")), 63)
        eq(store.load_org(slug).d["mail_log"]["owner"][-1]["id"], "63")
    check("one append to a 62-row owner changes exactly one SQL row", append_is_one_row_write)

    def sectionmap_dict_contract() -> None:
        slug = "s1-map-contract"
        o = fresh(slug)
        o.d["mail_log"] = {
            "a": [{"id": "a0"}], "b": [{"id": "b0"}], "empty": []}
        store.save_org(o)
        expected = {"a": [{"id": "a0"}], "b": [{"id": "b0"}], "empty": []}

        box = store.load_org(slug).d["mail_log"]
        eq(box._snaps, {}, "fresh SectionMap unexpectedly loaded rows: ")
        eq(bool(box), True, "nonempty metadata-only truthiness: ")
        eq(box._snaps, {}, "truthiness unexpectedly loaded rows: ")
        eq(list(reversed(box)), ["empty", "b", "a"],
           "reverse owner order/private seed: ")
        eq(box._snaps, {}, "reverse iteration unexpectedly loaded rows: ")
        eq(json.loads(json.dumps(box)), expected,
           "zero-loaded json.dumps C encoder: ")
        eq(set(json.loads(json.dumps(box))), set(expected),
           "private backing state leaked through json.dumps: ")
        box = store.load_org(slug).d["mail_log"]
        held_a = box["a"]
        eq(set(box._snaps), {"a"}, "one-owner load materialized others: ")
        eq(json.loads(json.dumps(box)), expected,
           "one-loaded json.dumps C encoder: ")
        eq(box["a"] is held_a, True,
           "json.dumps replaced an already-returned owner list: ")
        box = store.load_org(slug).d["mail_log"]
        eq(json.loads(json.dumps({"nested": box})), {"nested": expected},
           "nested json.dumps C encoder: ")
        box = store.load_org(slug).d["mail_log"]
        eq(json.loads(json.dumps(box, indent=2)), expected,
           "zero-loaded json.dumps Python encoder: ")
        empty_slug = "s1-map-contract-empty"
        empty_org = fresh(empty_slug)
        empty_org.d["mail_log"] = {}
        store.save_org(empty_org)
        empty_box = store.load_org(empty_slug).d["mail_log"]
        eq(bool(empty_box), False, "empty metadata-only truthiness: ")
        eq(list(reversed(empty_box)), [], "empty SectionMap reverse: ")
        eq(json.loads(json.dumps(empty_box)), {}, "empty SectionMap json.dumps: ")

        standalone = store.SectionMap()
        standalone["a"] = [1]
        eq(list(reversed(standalone)), ["a"],
           "standalone SectionMap reverse leaked private backing state: ")

        box = store.load_org(slug).d["mail_log"]
        eq(dict(box), expected, "dict(box): ")
        box = store.load_org(slug).d["mail_log"]
        eq({**box}, expected, "{**box}: ")
        box = store.load_org(slug).d["mail_log"]
        eq(json.loads(json.dumps(box or {})), expected, "json.dumps(box or {}): ")
        box = store.load_org(slug).d["mail_log"]
        eq(box == expected, True, "box == dict: ")
        eq(expected == box, True, "dict == box: ")
        box = store.load_org(slug).d["mail_log"]
        original_a = box["a"]
        box.materialize_all()
        eq(box["a"] is original_a, True, "bulk load replaced a returned owner list: ")

        mutated = store.load_org(slug)
        box = mutated.d["mail_log"]
        caller: list[dict] = []
        eq(box.setdefault("new", caller) is caller, True, "setdefault identity: ")
        caller.append({"id": "new0"})
        box.update({"updated": [{"id": "u0"}]})
        box |= {"merged": [{"id": "m0"}]}
        owner, value = box.popitem()
        eq((owner, value), ("merged", [{"id": "m0"}]), "popitem order/value: ")
        try:
            box.pop("absent", None, None)
            raise AssertionError("SectionMap.pop accepted too many defaults")
        except TypeError:
            pass
        store.save_org(mutated)
        got = store.load_org(slug).d["mail_log"]
        eq(got["new"], [{"id": "new0"}])
        eq(got["updated"], [{"id": "u0"}])

        original = store.load_org(slug)
        original_ids = [r[0] for r in rows(
            slug, "SELECT seq FROM log_d WHERE sect='mail_log' AND owner='a' ORDER BY seq")]
        clone = copy.deepcopy(original.d["mail_log"])
        eq(clone["a"]._row_ids, original_ids,
           "deepcopy duplicated or discarded row identities: ")
        clone["a"].append({"id": "a1"})
        original.d["mail_log"] = clone
        store.save_org(original)
        eq([x["id"] for x in store.load_org(slug).d["mail_log"]["a"]],
           ["a0", "a1"], "deepcopy independently saveable: ")
        after_clone_ids = [r[0] for r in rows(
            slug, "SELECT seq FROM log_d WHERE sect='mail_log' AND owner='a' ORDER BY seq")]
        eq(after_clone_ids[:-1], original_ids,
           "deepcopy save rewrote proven existing rows: ")
        back = pickle.loads(pickle.dumps(store.load_org(slug).d["mail_log"]))
        eq(dict(back), dict(store.load_org(slug).d["mail_log"]), "pickle content: ")
    check("SectionMap preserves dict/spread/equality/update/ior/popitem/identity/deepcopy/pickle behavior",
          sectionmap_dict_contract)

    def unbacked_appendlog_is_an_ordinary_list() -> None:
        log = store.AppendLog([1])
        eq(log._row_ids, [None], "unbacked initial row journal: ")
        eq(log.pop(), 1, "AppendLog([1]).pop(): ")
        eq(log, [])
        log = store.AppendLog([1, 2])
        del log[0]
        eq(log, [2])
        eq(log._row_ids, [None])
    check("an unbacked AppendLog preserves ordinary pop/delete list semantics",
          unbacked_appendlog_is_an_ordinary_list)

    def edits_deletes_truncation_and_replacement() -> None:
        slug = "s1-mutations"
        o = fresh(slug)
        o.d["notice_log"] = [{"id": str(i), "at": f"t{i}"} for i in range(8)]
        store.save_org(o)

        o = store.load_org(slug)
        log = o.d["notice_log"]
        before_ids = [r[0] for r in rows(slug,
            "SELECT seq FROM log_l WHERE sect='notice_log' ORDER BY seq")]
        log[3]["edited"] = True                 # invisible to list journaling
        store.save_org(o)
        after_ids = [r[0] for r in rows(slug,
            "SELECT seq FROM log_l WHERE sect='notice_log' ORDER BY seq")]
        eq(after_ids, before_ids, "an entry edit replaced row identities: ")
        eq(store.load_org(slug).d["notice_log"][3]["edited"], True)

        o = store.load_org(slug)
        log = o.d["notice_log"]
        removed_seq = before_ids[4]
        del log[4]                                # one middle row
        store.save_org(o)
        ids = [r[0] for r in rows(slug,
            "SELECT seq FROM log_l WHERE sect='notice_log' ORDER BY seq")]
        eq(ids, [x for x in before_ids if x != removed_seq], "middle delete row ids: ")

        o = store.load_org(slug)
        log = o.d["notice_log"]
        surviving = [r[0] for r in rows(slug,
            "SELECT seq FROM log_l WHERE sect='notice_log' ORDER BY seq")][-3:]
        log.append({"id": "new", "at": "tn"})
        del log[:-4]                              # cap idiom: head delete + append
        store.save_org(o)
        ids = [r[0] for r in rows(slug,
            "SELECT seq FROM log_l WHERE sect='notice_log' ORDER BY seq")]
        eq(ids[:3], surviving, "head truncation rewrote survivors: ")
        eq(store.load_org(slug).d["notice_log"],
           [{"id": "5", "at": "t5"}, {"id": "6", "at": "t6"},
            {"id": "7", "at": "t7"}, {"id": "new", "at": "tn"}])

        o = store.load_org(slug)
        o.d["notice_log"] = [{"id": "replacement", "at": "r"}]
        store.save_org(o)
        eq(store.load_org(slug).d["notice_log"], [{"id": "replacement", "at": "r"}])
    check("mutable entries, middle delete, head truncation+append, and full replacement stay exact",
          edits_deletes_truncation_and_replacement)

    def seq_identity_duplicates_at_and_second_save() -> None:
        slug = "s1-seq-identity"
        o = fresh(slug)
        o.d["notice_log"] = [
            {"same": True, "at": "old"},
            {"same": True, "at": "old"},
            {"same": True, "at": "old"},
        ]
        store.save_org(o)
        seqs = [r[0] for r in rows(slug,
            "SELECT seq FROM log_l WHERE sect='notice_log' ORDER BY seq")]
        o = store.load_org(slug)
        log = o.d["notice_log"]
        del log[1]
        log[0]["at"] = "new"
        store.save_org(o)
        got = rows(slug,
            "SELECT seq, at, val FROM log_l WHERE sect='notice_log' ORDER BY seq")
        eq([r[0] for r in got], [seqs[0], seqs[2]],
           "duplicate-value deletion lost row identity: ")
        eq(got[0][1], "new", "indexed at column was not updated: ")
        eq(json.loads(got[0][2])["at"], "new", "JSON at disagrees: ")
        log.append({"id": "first append", "at": "a1"})
        store.save_org(o)
        first = [r[0] for r in rows(slug,
            "SELECT seq FROM log_l WHERE sect='notice_log' ORDER BY seq")]
        log.append({"id": "second append", "at": "a2"})
        store.save_org(o)
        second = [r[0] for r in rows(slug,
            "SELECT seq FROM log_l WHERE sect='notice_log' ORDER BY seq")]
        eq(second[:-1], first, "second save rewrote the first save: ")

        o = store.load_org(slug)
        rev = o.d["notice_log"]
        before = list(rev)
        rev.reverse()                            # explicitly safe fallback
        store.save_org(o)
        eq(store.load_org(slug).d["notice_log"], list(reversed(before)))
    check("seq identity survives duplicates/deletion; at updates; second save and reorder are exact",
          seq_identity_duplicates_at_and_second_save)

    def failed_commit_does_not_adopt_rows() -> None:
        slug = "s1-rollback-retry"
        o = fresh(slug)
        o.d["events"] = [{"id": "old", "at": "t0"}]
        store.save_org(o)
        o = store.load_org(slug)
        log = o.d["events"]
        old_rows = list(log._rows)
        log.append({"id": "retry", "at": "t1"})
        real = store._write_doc

        def fail_after_writes(conn, d, lazy):
            result = real(conn, d, lazy)
            raise RuntimeError("fail after row writes")

        store._write_doc = fail_after_writes  # pyright: ignore[reportAttributeAccessIssue, reportAssignmentType]
        try:
            try:
                store.save_org(o)
                raise AssertionError("injected post-write failure did not fire")
            except RuntimeError as exc:
                eq(str(exc), "fail after row writes")
        finally:
            store._write_doc = real  # pyright: ignore[reportAttributeAccessIssue]
        eq(log._rows, old_rows, "rolled-back rows were adopted as committed: ")
        eq(log._row_ids, [old_rows[0][0], None], "pending append identity was lost: ")
        eq(store.load_org(slug).d["events"], [{"id": "old", "at": "t0"}],
           "rolled-back append leaked: ")
        store.save_org(o)
        eq([x["id"] for x in store.load_org(slug).d["events"]], ["old", "retry"])
    check("a failed commit keeps old row baselines and the retry persists the pending append",
          failed_commit_does_not_adopt_rows)

    def owner_metadata_and_loaded_snapshots_stay_distinct() -> None:
        slug = "s1-owner-order"
        o = fresh(slug)
        o.d["mail_log"] = {
            "a": [{"id": "a"}], "b": [{"id": "b"}], "empty": []}
        store.save_org(o)
        o = store.load_org(slug)
        box = o.d["mail_log"]
        moved = box.pop("a")
        box["renamed"] = moved
        del box["b"]
        empty2: list[dict] = []
        box.setdefault("new-empty", empty2)
        store.save_org(o)
        got = store.load_org(slug).d["mail_log"]
        eq(list(got), ["empty", "renamed", "new-empty"], "owner order: ")
        eq(got["empty"], [])
        eq(got["renamed"], [{"id": "a"}])
        eq(got["new-empty"], [])
        eq(json.loads(rows(slug,
            "SELECT val FROM meta WHERE key='owners:mail_log'")[0][0]),
           ["empty", "renamed", "new-empty"])
    check("owner add/delete/rename/empty rows reload in exact insertion order",
          owner_metadata_and_loaded_snapshots_stay_distinct)

    def stale_docs_preserve_unloaded_other_owner() -> None:
        slug = "s1-stale-docs"
        o = fresh(slug)
        o.d["mail_log"] = {"a": [{"id": "a0"}], "b": [{"id": "b0"}]}
        store.save_org(o)
        first = store.load_org(slug)
        first.d["mail_log"]["a"].append({"id": "a1"})
        second = store.load_org(slug)
        second.d["mail_log"]["b"].append({"id": "b1"})
        second.d["mail_log"]["c"] = [{"id": "c0"}]
        store.save_org(second)
        store.save_org(first)
        got = store.load_org(slug).d["mail_log"]
        eq([x["id"] for x in got["a"]], ["a0", "a1"])
        eq([x["id"] for x in got["b"]], ["b0", "b1"],
           "saving an older doc overwrote an owner it never loaded: ")
        eq(got["c"], [{"id": "c0"}],
           "saving an older doc erased a concurrently added owner: ")

        stale = store.load_org(slug)
        stale.d["mail_log"]["a"].append({"id": "a2"})
        structural = store.load_org(slug)
        del structural.d["mail_log"]["b"]
        structural.d["mail_log"]["d"] = [{"id": "d0"}]
        store.save_org(structural)
        store.save_org(stale)
        got = store.load_org(slug).d["mail_log"]
        eq(list(got), ["a", "c", "d"], "concurrent owner order: ")
        eq(got["d"], [{"id": "d0"}],
           "saving an older doc erased a second concurrent addition: ")
        eq("b" in got, False,
           "saving an older doc resurrected a concurrently deleted owner: ")

        stale_structural = store.load_org(slug)
        stale_box = stale_structural.d["mail_log"]
        concurrent_structural = store.load_org(slug)
        concurrent_structural.d["mail_log"]["e"] = [{"id": "e0"}]
        store.save_org(concurrent_structural)
        stale_box["f"] = [{"id": "f0"}]
        store.save_org(stale_structural)
        got = store.load_org(slug).d["mail_log"]
        eq(list(got), ["a", "c", "d", "e", "f"],
           "stale structural save did not merge current owner metadata: ")
        eq(got["e"], [{"id": "e0"}])
        eq(got["f"], [{"id": "f0"}])
    check("a stale document preserves concurrently changed/added/deleted unloaded owners",
          stale_docs_preserve_unloaded_other_owner)


# ===========================================================================
if __name__ == "__main__":
    for fn in (s1_lazydoc, s2_roundtrip, s3_save, s4_migration_mechanics,
               s5_delete_restore, s6_revision_hooks, s7_rollback, s8_export,
               s9_review, s10_empty_is_not_absent, s11_desync_guard,
               s12_selective_incremental_logs):
        try:
            fn()
        except BaseException:                              # noqa: BLE001
            FAIL.append((f"{_SECTION[0]} (section aborted)", traceback.format_exc()))
            print(f"  XX     section aborted: {_SECTION[0]}")
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed  (data root: {store.DATA_ROOT})")
    for label, tb in FAIL:
        print(f"\n✗ {label}\n{tb}")
    sys.exit(1 if FAIL else 0)
