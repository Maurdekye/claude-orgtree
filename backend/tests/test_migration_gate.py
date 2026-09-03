"""The JSON→SQLite migration GATE — what may START a migration (store.py,
2026-09-04), and that a withheld one writes nothing.

    python backend/tests/test_migration_gate.py

Why this file exists: on 2026-09-03 a test runner that strips every
`ORGTREE_*` variable from its children ran a sqlite-default build against
`~/orgtree` — the live root — and `claim_data_root()` migrated production as
a side effect of where the process happened to be pointed. The verifier and
the `.premigration` files made it a five-minute rollback. The trigger was the
bug, and this suite pins its replacement:

  A migration is an operator action (`ORGTREE_MIGRATE=1`), never an inference.
  A sqlite process that finds unmigrated JSON without the opt-in REFUSES —
  loudly, before writing anything, the `.owner` claim included. It does not
  migrate and it does not quietly read nothing.

Each case runs in a FRESH interpreter: the gate is process state
(`_gate_passed`, the owner claim, the import-time DATA_ROOT), and the whole
point is what a process that was not started for this root may do. The
fixture root is built through the real ledger under the JSON backend, then
COPIED per case; the parent never runs sqlite and never claims anything.
"Nothing was written" is asserted as a byte-level snapshot of the entire
tree before and after — not merely that an exception was raised.

Hermetic: its own ORGTREE_DATA, no network, no CLI. The one backend it
starts (`python -m orgtree.api`) must refuse before it binds. Exit code 1 on
any failure; each ✗ line names the check.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# the parent is a JSON-backend process on its own throwaway root: it builds
# the fixture and inspects the children's roots, nothing more. Set BEFORE the
# import — store resolves ORGTREE_DATA / ORGTREE_STORE at import time.
_TMP = tempfile.mkdtemp(prefix="orgtree-gate-")
FIXTURE = os.path.join(_TMP, "fixture")
os.environ["ORGTREE_DATA"] = FIXTURE
os.environ["ORGTREE_STORE"] = "json"
os.environ.pop("ORGTREE_MIGRATE", None)
os.makedirs(FIXTURE, exist_ok=True)
with open(os.path.join(FIXTURE, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, BACKEND)
from orgtree import store                                          # noqa: E402
from orgtree.ledger import TOOL_KEYS, USER                         # noqa: E402

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except Exception:                                           # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  ✗  {label}")
    else:
        PASS.append(label)
        print(f"  ✓  {label}")


def eq(a, b, what: str = "") -> None:
    assert a == b, f"{what}{a!r} != {b!r}"


# ----------------------------------------------------------------- fixture
def _spec():
    return dict(add_dirs=[], tools={**{k: True for k in TOOL_KEYS}, "mcp": ["*"]},
                org_visibility="team", charter="gate test hire")


def build_fixture() -> None:
    """Two real orgs, alpha and beta, as JSON — through the ledger, so the
    documents are exactly what a live root holds and the verifier accepts."""
    for name in ("alpha", "beta"):
        org = store.create_org(name)
        org.d["max_top_grant"] = 0
        org.hire(USER, None, "opus", 210, "ceo")
        org.hire("ceo", "ceo", "haiku", 0, "n0", **_spec())
        store.save_org(org)
    assert sorted(store.pending_migrations(FIXTURE)) == ["alpha", "beta"]


def tree(root: str) -> dict[str, str]:
    """relpath → sha256 for EVERY file under root. The whole tree, so a
    stray sidecar, a `.owner`, a temp file — anything — shows up."""
    out: dict[str, str] = {}
    for dp, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dp, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root).replace("\\", "/")] = hashlib.sha256(fh.read()).hexdigest()
    return out


_N = 0


def fresh_root(migrated: bool = False) -> str:
    """A private copy of the fixture for one case. `migrated=True` hands
    back a root a sqlite backend already converted (via the opt-in, in a
    child), i.e. the post-cutover shape."""
    global _N
    _N += 1
    root = os.path.join(_TMP, f"case{_N}")
    shutil.copytree(FIXTURE, root)
    if migrated:
        r = child("store.claim_data_root(); out['done'] = True",
                  root, migrate="1")
        assert r["done"] is True, r
        assert store.pending_migrations(root) == [], os.listdir(os.path.join(root, "orgs"))
    return root


# ------------------------------------------------------------------- child
PRELUDE = r'''
import os, sys, json, traceback
sys.path.insert(0, sys.argv[1])
out = {"env_migrate": os.environ.get("ORGTREE_MIGRATE"), "err": None, "err_type": None}
try:
    from orgtree import store
    from orgtree.ledger import LedgerError
    out["backend"] = store.STORE_BACKEND
    out["root"] = store.DATA_ROOT
'''
EPILOGUE = r'''
except Exception as e:
    out["err_type"] = type(e).__name__
    out["err"] = str(e)
    out["tb"] = traceback.format_exc()
out["owner_held"] = store._owner_fd is not None if "store" in dir() else None
out["gate_passed"] = getattr(store, "_gate_passed", None) if "store" in dir() else None
print("\n@@RESULT@@" + json.dumps(out))
'''


def child_env(root: str, backend: str = "sqlite", migrate: str | None = None,
              parent_has_migrate: bool = False) -> dict[str, str]:
    """EXACTLY the runner's rule (tools/run_tests.py): every ORGTREE_*
    variable stripped, then only what the case sets. `parent_has_migrate`
    plants the opt-in in the PARENT'S view first, to prove the strip is what
    the child sees and not the shell it was launched from."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORGTREE_")}
    if parent_has_migrate:
        # what the operator's shell would have had; the runner's strip
        # removes it before the child is born — verified INSIDE the child
        assert "ORGTREE_MIGRATE" not in env
    env["ORGTREE_DATA"] = root
    env["ORGTREE_STORE"] = backend
    if migrate is not None:
        env["ORGTREE_MIGRATE"] = migrate
    env["PYTHONIOENCODING"] = "utf-8"
    env["HOME"] = os.path.join(_TMP, "home")
    env["USERPROFILE"] = env["HOME"]
    os.makedirs(env["HOME"], exist_ok=True)
    return env


def child(body: str, root: str, backend: str = "sqlite", migrate: str | None = None,
          **kw) -> dict:
    """Run `body` (indented into the try) in a fresh interpreter against
    `root`; returns the child's result dict plus its stderr."""
    code = PRELUDE + "".join("    " + ln + "\n" for ln in body.splitlines()) + EPILOGUE
    env = child_env(root, backend, migrate, **kw)
    p = subprocess.run([sys.executable, "-c", code, BACKEND], env=env,
                       capture_output=True, text=True, timeout=180)
    marker = "@@RESULT@@"
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith(marker)]
    assert lines, f"child produced no result (rc={p.returncode})\n{p.stdout[-2000:]}\n{p.stderr[-3000:]}"
    res = json.loads(lines[-1][len(marker):])
    res["stderr"] = p.stderr
    res["rc"] = p.returncode
    return res


def assert_refused(res: dict, *names: str) -> None:
    eq(res["err_type"], "MigrationRefused", f"expected MigrationRefused, got {res['err_type']} "
                                            f"({res.get('err')!r}); stderr tail: {res['stderr'][-1500:]}\n")
    msg = res["err"]
    assert "MIGRATION REFUSED" in msg, msg
    assert "ORGTREE_MIGRATE=1" in msg, msg
    assert "NOTHING HAS BEEN WRITTEN" in msg, msg
    assert "ORGTREE_STORE=json" in msg, msg          # the alternative is named
    for n in names:
        assert n in msg, (n, msg)
    eq(res["owner_held"], False, "the claim must not be left behind: ")
    eq(res["gate_passed"], False, "a refused process has not passed the gate: ")


def unchanged(root: str, before: dict[str, str], *, allow: tuple[str, ...] = ()) -> None:
    after = tree(root)
    extra = {k: v for k, v in after.items() if k not in before}
    gone = [k for k in before if k not in after]
    changed = [k for k in before if k in after and after[k] != before[k]]
    extra = {k: v for k, v in extra.items() if k not in allow}
    assert not gone, f"files removed: {gone}"
    assert not changed, f"files rewritten: {changed}"
    assert not extra, f"files created: {sorted(extra)}"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ======================================================================
def run() -> None:
    build_fixture()
    print(f"fixture: {FIXTURE}  (alpha, beta as JSON)")

    # ---------------------------------------------------- 1. the refusal
    print("\n1. sqlite default + unmigrated JSON + no opt-in ⇒ refuses, writes nothing")

    def refuses_at_claim() -> None:
        root = fresh_root()
        before = tree(root)
        r = child("store.claim_data_root()", root)
        assert_refused(r, root, "alpha", "beta")
        eq(r["env_migrate"], None)
        unchanged(root, before)                # no .db, no .premigration, no .owner, nothing
        assert not os.path.exists(os.path.join(root, ".owner")), "the claim was written"
        eq(sorted(store.pending_migrations(root)), ["alpha", "beta"])
    check("claim_data_root refuses with MigrationRefused naming the flag, the root and every org — and the tree is byte-identical (not even .owner)",
          refuses_at_claim)

    def refuses_runner_shaped() -> None:
        # the incident's exact shape: the operator's shell HAS the opt-in;
        # the runner strips ORGTREE_* from the child; the child must see no
        # opt-in and refuse. Checked inside the child, not assumed.
        os.environ["ORGTREE_MIGRATE"] = "1"
        try:
            root = fresh_root()
            before = tree(root)
            r = child("store.claim_data_root()", root, parent_has_migrate=True)
        finally:
            os.environ.pop("ORGTREE_MIGRATE", None)
        eq(r["env_migrate"], None, "the child must NOT see the parent's opt-in: ")
        assert_refused(r, "alpha", "beta")
        unchanged(root, before)
    check("the runner's env strip: parent shell has ORGTREE_MIGRATE=1, child does not ⇒ child refuses (verified inside the child)",
          refuses_runner_shaped)

    def wrong_values_refuse() -> None:
        root = fresh_root()
        before = tree(root)
        body = '''
res = {}
for v in ("0", "true", "yes", "TRUE", " ", "2", ""):
    os.environ["ORGTREE_MIGRATE"] = v
    try:
        store.claim_data_root()
        res[v] = "STARTED"
    except store.MigrationRefused:
        res[v] = "refused"
    except store.MigrationError as e:
        res[v] = "MigrationError:" + str(e)[:80]
out["res"] = res
'''
        r = child(body, root)
        eq(r["err_type"], None, r.get("tb"))
        eq(set(r["res"].values()), {"refused"}, f"{r['res']}: ")
        eq(r["owner_held"], False)
        unchanged(root, before)
    check("only the exact value 1 opts in: 0 / true / yes / TRUE / blank / 2 all refuse (read at call time)",
          wrong_values_refuse)

    def migrate_pending_direct_refuses() -> None:
        root = fresh_root()
        before = tree(root)
        r = child("store.migrate_pending()", root)
        assert_refused(r, "alpha", "beta")
        unchanged(root, before, allow=("orgs/",))
    check("migrate_pending() called directly by a script is gated the same way",
          migrate_pending_direct_refuses)

    def load_org_direct_refuses() -> None:
        # a process that never claimed the root asks for an org that is
        # plainly there as JSON: refused loudly, NOT "no such org"
        root = fresh_root()
        before = tree(root)
        r = child('''
try:
    store.load_org("alpha")
    out["load"] = "loaded"
except store.MigrationRefused as e:
    out["load"] = "refused"
    out["load_msg"] = str(e)
except LedgerError as e:
    out["load"] = "LedgerError:" + str(e)
names = sorted(r["slug"] for r in store.list_orgs())
out["names"] = names
''', root)
        eq(r["err_type"], None, r.get("tb"))
        eq(r["load"], "refused")
        assert "alpha" in r["load_msg"] and "ORGTREE_MIGRATE=1" in r["load_msg"]
        eq(r["names"], [], "a listing must not migrate either: ")
        assert "MIGRATION REFUSED" in r["stderr"], "the listing's skip must be loud on stderr"
        unchanged(root, before)
    check("a non-owner process: load_org refuses (never 'no such org'), list_orgs skips loudly, nothing written",
          load_org_direct_refuses)

    def api_main_refuses_loudly() -> None:
        # the real thing: the backend entrypoint, sqlite default, no opt-in.
        # It must exit 1 with the wall on stderr, no traceback, before it
        # binds anything or writes anything.
        root = fresh_root()
        before = tree(root)
        env = child_env(root)
        env["ORGTREE_PORT"] = str(free_port())
        env["ORGTREE_PUBLIC_PORT"] = "0"
        env["ORGTREE_BRIDGE_PORT"] = "0"
        env["ORGTREE_WARM"] = "0"
        p = subprocess.run([sys.executable, "-m", "orgtree.api"], cwd=BACKEND, env=env,
                           capture_output=True, text=True, timeout=180)
        eq(p.returncode, 1, f"stdout: {p.stdout[-1500:]}\nstderr: {p.stderr[-3000:]}\n")
        assert "MIGRATION REFUSED" in p.stderr, p.stderr[-3000:]
        assert "ORGTREE_MIGRATE=1" in p.stderr
        assert "Traceback" not in p.stderr, p.stderr[-3000:]
        unchanged(root, before)
    check("python -m orgtree.api on that root exits 1 with the wall on stderr, no traceback, tree untouched",
          api_main_refuses_loudly)

    # ------------------------------------------------- 2. the opt-in path
    print("\n2. ORGTREE_MIGRATE=1 ⇒ migrates at claim, before anything binds")

    def optin_migrates() -> None:
        root = fresh_root()
        orig = {s: open(os.path.join(root, "orgs", f"{s}.json"), "rb").read() for s in ("alpha", "beta")}
        r = child('''
store.claim_data_root()
out["after_claim"] = sorted(os.listdir(os.path.join(store.DATA_ROOT, "orgs")))
out["nodes"] = {s: sorted(store.load_org(s).d["nodes"]) for s in ("alpha", "beta")}
out["names"] = sorted(x["slug"] for x in store.list_orgs())
''', root, migrate="1")
        eq(r["err_type"], None, r.get("tb"))
        eq(r["env_migrate"], "1")
        eq(r["owner_held"], True)
        eq(r["gate_passed"], True)
        for s in ("alpha", "beta"):
            assert f"{s}.db" in r["after_claim"], r["after_claim"]
            assert f"{s}.json.premigration" in r["after_claim"], r["after_claim"]
            assert f"{s}.json" not in r["after_claim"], r["after_claim"]
            eq(open(os.path.join(root, "orgs", f"{s}.json.premigration"), "rb").read(), orig[s],
               "premigration bytes: ")
            eq(r["nodes"][s], ["ceo", "n0"])
        eq(r["names"], ["alpha", "beta"])
        eq(store.pending_migrations(root), [])
    check("with the opt-in, claim_data_root migrates every pending org before it returns; .premigration is byte-exact",
          optin_migrates)

    def migrated_root_needs_no_optin() -> None:
        root = fresh_root(migrated=True)
        r = child('''
store.claim_data_root()
out["names"] = sorted(x["slug"] for x in store.list_orgs())
''', root)
        eq(r["err_type"], None, r.get("tb"))
        eq(r["names"], ["alpha", "beta"])
        eq(r["owner_held"], True)
        eq(r["gate_passed"], True)
    check("an already-migrated root starts without the opt-in (nothing pending ⇒ nothing to refuse)",
          migrated_root_needs_no_optin)

    def json_backend_untouched() -> None:
        root = fresh_root()
        before = tree(root)
        r = child('''
store.claim_data_root()
out["names"] = sorted(x["slug"] for x in store.list_orgs())
out["pending"] = store.pending_migrations()
''', root, backend="json")
        eq(r["err_type"], None, r.get("tb"))
        eq(r["names"], ["alpha", "beta"])
        eq(r["pending"], ["alpha", "beta"])
        unchanged(root, before, allow=(".owner",))
    check("ORGTREE_STORE=json ignores the gate entirely: claims, lists, migrates nothing",
          json_backend_untouched)

    def other_root_not_gated() -> None:
        # claim_data_root(root=...) on a root that is NOT DATA_ROOT: claimed
        # only. Never migrated, never refused — drills claim throwaway roots.
        other = fresh_root()
        empty = os.path.join(_TMP, "empty-data-root")
        os.makedirs(empty, exist_ok=True)
        before = tree(other)
        r = child(f'''
store.claim_data_root({other!r})
out["pending_other"] = store.pending_migrations({other!r})
''', empty)
        eq(r["err_type"], None, r.get("tb"))
        eq(r["owner_held"], True)
        eq(r["gate_passed"], False, "owning a foreign root grants nothing on DATA_ROOT: ")
        eq(r["pending_other"], ["alpha", "beta"])
        unchanged(other, before, allow=(".owner",))
    check("claim_data_root(root=other) claims only — no migration, no refusal, no gate pass",
          other_root_not_gated)

    # --------------------------------------- 3. on demand, for the owner
    print("\n3. after the gate: hand-restored JSON under the owning backend")

    def owner_migrates_restore() -> None:
        root = fresh_root(migrated=True)
        r = child('''
store.claim_data_root()                    # no opt-in; nothing pending
od = os.path.join(store.DATA_ROOT, "orgs")
# the operator restores alpha from its premigration copy by hand: the .db
# is gone (trashed, say) and the .json is back
store._POOL.close_all("alpha")
for f in os.listdir(od):
    if f.startswith("alpha.db"):
        os.remove(os.path.join(od, f))
import shutil
shutil.copy(os.path.join(od, "alpha.json.premigration"), os.path.join(od, "alpha.json"))
out["names"] = sorted(x["slug"] for x in store.list_orgs())
out["nodes"] = sorted(store.load_org("alpha").d["nodes"])
out["files"] = sorted(os.listdir(od))
''', root)
        eq(r["err_type"], None, r.get("tb"))
        eq(r["gate_passed"], True)
        eq(r["names"], ["alpha", "beta"])
        eq(r["nodes"], ["ceo", "n0"])
        assert "alpha.db" in r["files"] and "alpha.json" not in r["files"], r["files"]
    check("the backend that claimed the root migrates a later hand-restored .json on demand, no opt-in needed",
          owner_migrates_restore)

    def non_owner_restore_refused_then_optin() -> None:
        root = fresh_root(migrated=True)
        od = os.path.join(root, "orgs")
        for f in os.listdir(od):
            if f.startswith("alpha.db"):
                os.remove(os.path.join(od, f))
        shutil.copy(os.path.join(od, "alpha.json.premigration"), os.path.join(od, "alpha.json"))
        before = tree(root)
        r = child('''
try:
    store.load_org("alpha")
    out["first"] = "loaded"
except store.MigrationRefused:
    out["first"] = "refused"
out["names_before"] = sorted(x["slug"] for x in store.list_orgs())
out["beta_nodes"] = sorted(store.load_org("beta").d["nodes"])
out["snapshot_ok"] = True
os.environ["ORGTREE_MIGRATE"] = "1"       # the opt-in is read at call time
out["second"] = sorted(store.load_org("alpha").d["nodes"])
out["names_after"] = sorted(x["slug"] for x in store.list_orgs())
''', root)
        eq(r["err_type"], None, r.get("tb"))
        eq(r["first"], "refused")
        eq(r["names_before"], ["beta"], "beta must still be served while alpha is refused: ")
        eq(r["beta_nodes"], ["ceo", "n0"])
        eq(r["second"], ["ceo", "n0"])
        eq(r["names_after"], ["alpha", "beta"])
        # the refusal half wrote nothing; the opt-in half migrated alpha
        after = tree(root)
        assert "orgs/alpha.db" in after and "orgs/alpha.json" not in after
        eq(before["orgs/beta.db"], after["orgs/beta.db"], "beta untouched: ")
    check("a non-owner is refused the same restore, keeps serving the rest, and may migrate it once it sets the opt-in",
          non_owner_restore_refused_then_optin)

    def interrupted_finished() -> None:
        # a verified candidate whose final rename was interrupted: the org
        # would otherwise be invisible. Finished without the opt-in — that
        # migration was already authorised when it started.
        root = fresh_root(migrated=True)
        od = os.path.join(root, "orgs")
        os.replace(os.path.join(od, "beta.db"), os.path.join(od, "beta.db.migrating"))
        for side in ("beta.db-wal", "beta.db-shm"):
            p = os.path.join(od, side)
            if os.path.exists(p):
                os.remove(p)
        r = child('''
store.claim_data_root()
od = os.path.join(store.DATA_ROOT, "orgs")
out["files"] = sorted(os.listdir(od))
out["nodes"] = sorted(store.load_org("beta").d["nodes"])
''', root)
        eq(r["err_type"], None, r.get("tb"))
        assert "beta.db" in r["files"] and "beta.db.migrating" not in r["files"], r["files"]
        eq(r["nodes"], ["ceo", "n0"])
    check("an interrupted final rename is completed at claim without the opt-in (that migration was already authorised)",
          interrupted_finished)


if __name__ == "__main__":
    try:
        run()
    except BaseException:                                       # noqa: BLE001
        FAIL.append(("suite aborted", traceback.format_exc()))
        print("  XX  suite aborted")
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed  (root: {_TMP})")
    for label, tb in FAIL:
        print(f"\n✗ {label}\n{tb}")
    sys.exit(1 if FAIL else 0)
