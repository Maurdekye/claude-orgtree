"""B8 / B13 — ROLLBACK: the previous release reads documents this release wrote.

Typed rows carry `ev` beside the frozen `body`/`text` the old code renders. This
harness runs the REAL previous release (a git worktree at ROLLBACK_BASE, the main
tip at landing) as a separate process, under a THROWAWAY data root that is verified
before the first `orgtree` import on both sides, over a document written by HEAD:

    B8  · old readers over rows with `ev`  ==  HEAD readers with the new keys stripped
          (inbox / history / chat / user inbox JSON, diff-clean); then the old code SAVES
          (it posts one mail) and every `ev` is compared KEYED BY ROW ID, BIT-EXACT,
          before vs after — with a mutation control proving the comparison can fail.
    B13 · `--doc <ORGTREE_DATA copy> --slug <org>`: the same walk over a REAL export
          (supplied read-only by the coordinator, outside the live root; only copies
          are touched). Two phases, labelled: (1) the UNTOUCHED export — pre-typed, so
          no `ev` may appear anywhere (no backfill) and the readers must agree without
          any stripping; (2) an ENRICHED copy — HEAD adds a labelled set of typed rows
          beside the real ones, the old release reads/saves it, and the retained REAL
          rows plus the added `ev`s are compared bit-exact.

ROLLBACK_BASE MUST be the main tip at landing — the landing checklist re-pins it;
the first check asserts it is an ancestor of HEAD so it cannot silently go stale.

    python backend/tests/test_events_rollback.py [--doc <data-root-copy> --slug <org>]
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

_TMP = tempfile.mkdtemp(prefix="orgtree-rollback-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = _TMP
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
REPO = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from orgtree import events, events_table, store                  # noqa: E402
from orgtree.ledger import SYSTEM, USER, Org, actor_of           # noqa: E402

LIVE = os.path.normcase(os.path.join(os.environ.get("USERPROFILE_REAL", "C:\\Users\\ncola_k8bx"), "orgtree"))
assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT
assert not os.path.normcase(os.path.abspath(store.DATA_ROOT)).startswith("c:\\users\\ncola_k8bx\\orgtree\\"), \
    "the harness must never resolve to the live root"

#: the main tip at landing — RE-PIN in the landing checklist (design §7.1)
ROLLBACK_BASE = "a253b02e128a9f794b840c1d863cfba5f5678732"
ROLLBACK_WT = os.environ.get("ORGTREE_ROLLBACK_WT") or os.path.join(
    os.path.dirname(REPO), "wt-rollback")

#: keys THIS release adds to wire rows/payloads; stripped before the diff
NEW_KEYS = {"ev", "ev_public", "ev_raw", "ev_error", "segments", "delivery", "restart_notice"}

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


#: keys this release ADDS to node_chat's `pending_mail` rows only (step 2c: the live
#: row needs kind/relationship); stripped there and nowhere else
PENDING_ROW_KEYS = {"kind", "relationship"}


def strip(x, *, pending=False):
    if isinstance(x, dict):
        drop = NEW_KEYS | (PENDING_ROW_KEYS if pending else set())
        return {k: strip(v, pending=(k == "pending_mail")) for k, v in x.items()
                if k not in drop}
    if isinstance(x, list):
        return [strip(v, pending=pending) for v in x]
    return x


# ------------------------------------------------------------------ the reader script
READER = r'''
import json, os, sys
root = sys.argv[1]; slug = sys.argv[2]; nodes = sys.argv[3].split(","); post = sys.argv[4] == "1"
os.environ["ORGTREE_DATA"] = root
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.dirname(root)
sys.path.insert(0, sys.argv[5])
from orgtree import store
assert store.DATA_ROOT.startswith(root), store.DATA_ROOT
assert not os.path.normcase(os.path.abspath(store.DATA_ROOT)).startswith("c:\\users\\ncola_k8bx\\orgtree\\")
from fastapi.testclient import TestClient
from orgtree import api
from orgtree.ledger import USER
c = TestClient(api.app)
out = {}
for nid in nodes:
    for kind in ("inbox", "history", "chat"):
        r = c.get(f"/api/orgs/{slug}/nodes/{nid}/{kind}")
        out[f"{nid}/{kind}"] = {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}
r = c.get(f"/api/orgs/{slug}/inbox")
out["user/inbox"] = {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}
if post:
    with store.DOC_LOCK:
        o = store.load_org(slug)
        # the box with the most headroom under the previous release's 100-row
        # mail_log cap: a post into a full box evicts its oldest row (the old
        # code's own cap, not a rollback defect) and would read as a lost row
        logs = o.d.get("mail_log") or {}
        target = min(nodes, key=lambda n: len(logs.get(n) or []))
        assert len(logs.get(target) or []) < 100, "every candidate box is at the mail_log cap"
        o.post_mail(USER, target, "posted by the previous release")
        store.save_org(o)
sys.stdout.write(json.dumps(out, sort_keys=True))
'''


def run_reader(backend_dir: str, root: str, slug: str, nodes: list[str], *,
               post: bool) -> dict:
    env = {**os.environ, "ORGTREE_DATA": root, "PYTHONIOENCODING": "utf-8",
           "USERPROFILE": os.path.dirname(root), "HOME": os.path.dirname(root)}
    p = subprocess.run([sys.executable, "-c", READER, root, slug, ",".join(nodes),
                        "1" if post else "0", backend_dir],
                       capture_output=True, text=True, encoding="utf-8", env=env,
                       cwd=backend_dir, timeout=600)
    if p.returncode != 0:
        raise AssertionError(f"reader at {backend_dir} failed:\n{p.stderr[-3000:]}")
    line = p.stdout.strip().splitlines()[-1]
    return json.loads(line)


def build_fixture() -> tuple[str, list[str]]:
    """An org with typed rows of every family this release writes to a box."""
    o = store.create_org("rollback fixture")
    slug = o.d["slug"]
    o.hire(USER, None, "opus", 20, "boss")
    o.hire("boss", "boss", "haiku", 5, "kid", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False, "subagents": False, "mcp": []},
           org_visibility="team", charter="rollback fixture")
    o.post_mail(USER, "boss", "plain user mail", typed=True)          # ordinary (ev on row)
    o.post_mail("boss", "kid", "legacy agent mail")                    # legacy, no ev
    wid = o.work_create("boss", "Rollback item", "old code must read it", owner="boss")["created"]
    o.work_assign("boss", wid, "kid")                                  # docket.assigned
    o.post_mail("boss", USER, "to the user", typed=True)              # user inbox typed
    o.reallocate(USER, "kid", 2)                                       # notices typed
    o.ask_user("boss", "which?", options=["a", "b"])            # boss holds the user audience
    aid = [a for a in o.d["asks"] if a["status"] == "open"][0]["id"]
    r = o.ask_answer(aid, selected=["a"])
    o.post_mail(USER, "boss", "", ev=r["ev"])                          # answer.ask
    o.append_system_mail("kid", events.mint(
        "runtime.turn_failed_terminal", actor_of(SYSTEM),
        {"kind": "session", "org": slug, "node": "kid", "session_id": "s"},
        door="idle watchdog", err="boom"))
    store.save_org(o)
    return slug, ["boss", "kid"]


def snapshot_evs(root: str, slug: str) -> dict[str, dict]:
    """{box:id → raw stored ev} for every mail / user-inbox / notice row of the org
    under `root`, read by HEAD in a FRESH process (store binds at import)."""
    p = subprocess.run([sys.executable, "-c", r"""
import os, sys, json
os.environ["ORGTREE_DATA"] = sys.argv[1]; sys.path.insert(0, sys.argv[2])
from orgtree import store
assert store.DATA_ROOT.startswith(sys.argv[1])
o = store.load_org(sys.argv[3])
out = {}
def put(box, rows):
    for i, r in enumerate(rows):
        key = f"{box}:{r.get('id') or ('#' + str(i) + '@' + str(r.get('at')))}"
        out[key] = {"ev": r.get("ev"), "body": r.get("body", r.get("text"))}
for nid, ms in (o.d.get("mail") or {}).items(): put(f"mail/{nid}", ms)
for nid, ms in (o.d.get("mail_log") or {}).items(): put(f"mail_log/{nid}", ms)
put("user_inbox", o.d.get("user_inbox") or []); put("user_mail_log", o.d.get("user_mail_log") or [])
for nid, rs in (o.d.get("notices") or {}).items(): put(f"notices/{nid}", rs)
put("notice_log", o.d.get("notice_log") or [])
sys.stdout.write(json.dumps(out, sort_keys=True))
""", root, BACKEND, slug], capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ORGTREE_DATA": root, "PYTHONIOENCODING": "utf-8"}, cwd=BACKEND)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def compare_evs(before: dict, after: dict) -> list[str]:
    """Rows present before must be present after with the SAME ev (bit-exact JSON)
    and the same body. Returns the list of differences (empty = identical)."""
    diffs = []
    for k, v in before.items():
        if k not in after:
            diffs.append(f"{k}: row gone after the old release saved")
            continue
        if json.dumps(v["ev"], sort_keys=True) != json.dumps(after[k]["ev"], sort_keys=True):
            diffs.append(f"{k}: ev changed")
        if v["body"] != after[k]["body"]:
            diffs.append(f"{k}: body changed")
    return diffs


def enrich(root: str, slug: str) -> list[str]:
    """ENRICHED COPY (labelled): HEAD adds typed rows beside the real ones. Returns the
    ids/keys it added so the comparison can tell added from retained."""
    p = subprocess.run([sys.executable, "-c", r"""
import os, sys, json
os.environ["ORGTREE_DATA"] = sys.argv[1]; sys.path.insert(0, sys.argv[2])
from orgtree import store, events
from orgtree.ledger import USER, SYSTEM, actor_of
assert store.DATA_ROOT.startswith(sys.argv[1])
with store.DOC_LOCK:
    o = store.load_org(sys.argv[3])
    live = [k for k, v in o.nodes.items() if v["state"] == "live"]
    top = [k for k in live if o.nodes[k].get("parent") is None]
    added = []
    logs = o.d.get("mail_log") or {}
    t = min(live, key=lambda n: len(logs.get(n) or []))    # any live box with headroom under the cap
    assert len(logs.get(t) or []) < 97, "no live box has headroom for the enrichment"
    r = o.post_mail(USER, t, "[B13 ENRICHMENT] plain typed user mail", typed=True); added.append(r["id"])
    e = o.append_system_mail(t, events.mint("runtime.turn_failed_terminal", actor_of(SYSTEM),
        {"kind": "session", "org": o.d["slug"], "node": t, "session_id": "b13"},
        door="B13 enrichment", err="synthetic")); added.append(e["id"])
    o.reallocate(USER, t, 1)                      # grant notices (typed)
    store.save_org(o)
sys.stdout.write(json.dumps({"added": added, "top": t}))
""", root, BACKEND, slug], capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "ORGTREE_DATA": root, "PYTHONIOENCODING": "utf-8"}, cwd=BACKEND)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


#: the ONLY differences this release may introduce on a PRE-TYPED document, and
#: exactly where: node_chat `pending_mail` rows (in-flight delivery) now carry the
#: delivery envelope and the row's kind/relationship (step 2c). Path-scoped.
UNTOUCHED_ADDITIVE = {("pending_mail", "delivery"), ("pending_mail", "kind"),
                      ("pending_mail", "relationship"),
                      # node_inbox's in-flight `pending` rows carry the same envelope
                      ("pending", "delivery")}


def additions(a, b, seen: set, path: str = "") -> str | None:
    """Walk old (a) and new (b) payloads together. Keys present in b and absent in a
    on a `pending_mail` row are recorded in `seen` as ("pending_mail", key); ANY other
    difference (a changed value, a removed key, an addition elsewhere) is returned as
    a description. None = identical apart from recorded additions."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k not in b:
                return f"{path}.{k} removed"
        for k in b:
            if k not in a:
                lst = next((n for n in ("pending_mail", "pending") if path.endswith(f"{n}[]")), None)
                if lst is None:
                    return f"{path}.{k} added"
                seen.add((lst, k))
                continue
            r = additions(a[k], b[k], seen, f"{path}.{k}" if path else k)
            if r is not None:
                return r
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: list length {len(a)} → {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = additions(x, y, seen, f"{path}[]")
            if r is not None:
                return r
        return None
    return None if a == b else f"{path}: {a!r} → {b!r}"


FORBIDDEN_ON_PRETYPED = ("ev", "ev_public", "ev_error", "ev_raw")


def forbidden_keys(x, path: str = "") -> list[str]:
    """Every occurrence of a typed-data key ANYWHERE in a payload — inside allowed
    additions too. The allowlist above says which keys may be ADDED; this says what
    may never appear on a pre-typed document, at any depth."""
    hits: list[str] = []
    if isinstance(x, dict):
        for k, v in x.items():
            here = f"{path}.{k}" if path else str(k)
            if k in FORBIDDEN_ON_PRETYPED:
                hits.append(here)
            hits += forbidden_keys(v, here)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            hits += forbidden_keys(v, f"{path}[{i}]")
    return hits


def _mutate_valid(ev: dict) -> dict:
    """A copy of `ev` with ONE schema-valid change: the actor id (a free string on
    every leaf, public by rule) is replaced by another id. Nothing else moves, so
    the mutant validates as the same leaf."""
    out = json.loads(json.dumps(ev))
    out["actor"] = {"kind": out["actor"]["kind"], "id": str(out["actor"]["id"]) + "-x"}
    return out


def live_nodes(root: str, slug: str) -> list[str]:
    p = subprocess.run([sys.executable, "-c",
                        "import os,sys;os.environ['ORGTREE_DATA']=sys.argv[1];sys.path.insert(0,sys.argv[2]);"
                        "from orgtree import store;o=store.load_org(sys.argv[3]);"
                        "print(','.join([k for k,v in o.nodes.items() if v['state']=='live'][:6]))",
                        root, BACKEND, slug], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "ORGTREE_DATA": root, "PYTHONIOENCODING": "utf-8"}, cwd=BACKEND)
    assert p.returncode == 0, p.stderr[-2000:]
    return [x for x in p.stdout.strip().splitlines()[-1].split(",") if x]


def walk(label: str, src_root: str, slug: str, *, expect_typed: bool, enriched: bool) -> None:
    """One rollback walk over a copy of `src_root`. `expect_typed` says whether the
    document is KNOWN to carry typed rows (fixture / enriched copy) — on a pre-typed
    export the honest expectations are the opposite ones, and they are asserted."""
    tag = label.replace(" ", "_")
    old_root = os.path.join(_TMP, tag, "old", "data")
    new_root = os.path.join(_TMP, tag, "new", "data")
    shutil.copytree(src_root, old_root)
    shutil.copytree(src_root, new_root)
    added: dict = {}
    if enriched:
        added = enrich(old_root, slug)
        shutil.rmtree(new_root)
        shutil.copytree(old_root, new_root)
    nodes = live_nodes(new_root, slug)
    assert nodes, "no live nodes to read"
    before = snapshot_evs(old_root, slug)
    typed_before = {k: v for k, v in before.items() if v["ev"] is not None}
    if expect_typed:
        assert len(typed_before) >= 3, f"{label}: expected typed rows, found {len(typed_before)}"
    else:
        assert not typed_before, f"{label}: a pre-typed export must carry NO ev (no backfill): " \
                                 f"{list(typed_before)[:3]}"
    old_out: dict = {}
    new_out: dict = {}

    def _old():
        old_out.update(run_reader(os.path.join(ROLLBACK_WT, "backend"), old_root, slug, nodes,
                                  post=True))
        bad = {k: v["status"] for k, v in old_out.items() if v["status"] != 200}
        assert not bad, bad
    check(f"{label} · the previous release reads inbox/history/chat/user-inbox (all 200) and saves",
          _old)

    def _new():
        new_out.update(run_reader(BACKEND, new_root, slug, nodes, post=False))
        bad = {k: v["status"] for k, v in new_out.items() if v["status"] != 200}
        assert not bad, bad
    check(f"{label} · HEAD reads the same document (all 200)", _new)

    def _diff():
        assert old_out and new_out
        for k in sorted(new_out):
            a, b = strip(old_out[k]["body"]), strip(new_out[k]["body"])
            if a != b:
                ja = json.dumps(a, sort_keys=True, indent=1); jb = json.dumps(b, sort_keys=True, indent=1)
                d = "\n".join(list(difflib.unified_diff(ja.splitlines(), jb.splitlines(), "old", "new",
                                                       lineterm=""))[:60])
                raise AssertionError(f"{k}: old-release output differs from HEAD (new keys stripped):\n{d}")
        raw_differ = any(old_out[k]["body"] != new_out[k]["body"] for k in new_out)
        if expect_typed:
            assert raw_differ, "positive control failed: HEAD added nothing to any payload"
        else:
            # a pre-typed export. The releases are compared RAW; the ONLY differences
            # allowed are the documented additive transport keys this release puts
            # on node_chat's in-flight `pending_mail` rows (UNTOUCHED_ADDITIVE, step
            # 2c — the live row needs them; they are not typed data) and on node_inbox's
            # in-flight `pending` rows (the delivery envelope). Every observed
            # addition is collected and must be inside that set; no `ev`/`ev_public`/
            # `ev_error`/`ev_raw` may appear anywhere (no backfill); and outside those
            # keys the payloads must be identical. Whether the set was exercised is
            # reported, so an export with no in-flight rows is not mistaken for proof.
            seen: set = set()
            for k in sorted(new_out):
                a, b = old_out[k]["body"], new_out[k]["body"]
                extra = additions(a, b, seen)
                if extra is not None:
                    raise AssertionError(f"{k}: RAW outputs differ beyond the documented additive "
                                         f"transport keys: {extra}")
            assert seen <= UNTOUCHED_ADDITIVE, f"undocumented additions: {seen - UNTOUCHED_ADDITIVE}"
            # no typed-data key ANYWHERE in the complete new outputs — including inside
            # the allowed additions (a `delivery` value carrying an `ev` would pass the
            # walker above; it must not pass this)
            hits = [f"{k}: {h}" for k in sorted(new_out) for h in forbidden_keys(new_out[k]["body"])]
            assert not hits, f"typed-data keys on a pre-typed document: {hits[:10]}"
            # POSITIVE CONTROL: a forbidden key planted UNDER an allowed addition is found
            planted = {"pending": [{"id": "legacy-row", "delivery": {"ev": {"injected": "typed"}}}]}
            control_seen: set = set()
            assert additions({"pending": [{"id": "legacy-row"}]}, planted, control_seen) is None \
                and control_seen == {("pending", "delivery")}, "the walker should allow the addition"
            assert forbidden_keys(planted) == ["pending[0].delivery.ev"], forbidden_keys(planted)
            print(f"         additive transport keys observed on this export: "
                  f"{sorted(seen) if seen else 'none (no in-flight rows — the exemption was not exercised)'}")
            if not seen:
                assert not raw_differ, "outputs differ yet no addition was recorded"
    check(f"{label} · reader outputs "
          f"{'diff-clean once this release' + chr(39) + 's keys are stripped (positive control: unstripped they differ)' if expect_typed else 'RAW-equal except the documented additive in-flight transport keys; no typed-data key at ANY depth (planted control caught)'}",
          _diff)

    def _roundtrip():
        after = snapshot_evs(old_root, slug)
        diffs = compare_evs(before, after)
        assert not diffs, diffs[:10]
        posted = [k for k, v in after.items() if v["body"] == "posted by the previous release"]
        assert posted, "the old release's own save did not land"
        assert all(after[k]["ev"] is None for k in posted), "the old release cannot mint ev"
        if added:
            for mid in added["added"]:
                keys = [k for k in after if k.endswith(":" + mid)]
                assert keys and all(after[k]["ev"] is not None for k in keys), f"added row {mid} lost its ev"
        # MUTATION CONTROL: the comparison must fail when a SCHEMA-VALID value of one
        # ev changes. The mutant is checked against the strict validator FIRST (so
        # the control is a real changed event, not a broken one), then the keyed
        # comparison must report it.
        import copy
        mut = copy.deepcopy(after)
        victim = next((k for k, v in mut.items() if v["ev"] is not None), None)
        if victim is not None:
            row = {"body": mut[victim]["body"]}
            full = events.decode_row_ev(mut[victim]["ev"], row)     # validates
            changed = _mutate_valid(full)
            events.validate_event(changed)                            # still a valid leaf
            assert json.dumps(changed, sort_keys=True) != json.dumps(full, sort_keys=True)
            mut[victim]["ev"] = (events.encode_row_ev(changed, row)
                                 if str(changed["variant"]) in events_table.ELIDED_FIELDS
                                 else events.encode_ev(changed))
            assert compare_evs(before, mut), "the ev comparison is vacuous — a valid mutated ev passed"
        else:
            assert not expect_typed, "no typed row to mutate on a typed document"
            mut[next(iter(mut))]["body"] = "MUTANT"
            assert compare_evs(before, mut), "the body comparison is vacuous"
    check(f"{label} · after the old release saved: every retained row's ev and body are BIT-EXACT "
          f"(keyed by id); its own row is legacy; mutation control fails as it must", _roundtrip)


def main() -> int:
    args = sys.argv[1:]
    doc = args[args.index("--doc") + 1] if "--doc" in args else None
    slug_arg = args[args.index("--slug") + 1] if "--slug" in args else None
    print("\n§0 · the previous release is real and is an ancestor of HEAD")
    if not os.path.isfile(os.path.join(ROLLBACK_WT, "backend", "orgtree", "ledger.py")):
        # environment-dependent: say so LOUDLY rather than pass quietly
        print(f"  ⚠ INERT — no ROLLBACK_BASE worktree at {ROLLBACK_WT}; nothing was checked. "
              f"Create it: git worktree add --detach <path> {ROLLBACK_BASE[:7]} "
              f"(or set ORGTREE_ROLLBACK_WT).")
        print("══════════════════════════════════════════════════════════════════════")
        print("0 checks passed, 0 failed  ·  INERT (no rollback worktree)")
        return 0

    def _ancestor():
        p = subprocess.run(["git", "merge-base", "--is-ancestor", ROLLBACK_BASE, "HEAD"],
                           cwd=REPO, capture_output=True, text=True)
        assert p.returncode == 0, f"ROLLBACK_BASE {ROLLBACK_BASE[:7]} is not an ancestor of HEAD — re-pin it"
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROLLBACK_WT,
                              capture_output=True, text=True).stdout.strip()
        assert head == ROLLBACK_BASE, f"worktree is at {head[:7]}, not ROLLBACK_BASE"
        old_src = open(os.path.join(ROLLBACK_WT, "backend", "orgtree", "ledger.py"),
                       encoding="utf-8").read()
        assert "def _notify_ev" not in old_src, "the 'previous release' already has typed rows — wrong base"
    check(f"ROLLBACK_BASE {ROLLBACK_BASE[:7]} is an ancestor of HEAD and its worktree is pre-typed",
          _ancestor)
    if FAIL:
        return 1

    if doc:
        assert slug_arg, "--slug is required with --doc"
        assert not os.path.normcase(os.path.abspath(doc)).startswith("c:\\users\\ncola_k8bx\\orgtree\\"), \
            "--doc must be a copy OUTSIDE the live root"
        print(f"\n§B13 · real export (untouched): {doc}")
        walk("B13 untouched export", doc, slug_arg, expect_typed=False, enriched=False)
        print(f"\n§B13 · real export + LABELLED typed enrichment (added by HEAD on a copy)")
        walk("B13 enriched copy", doc, slug_arg, expect_typed=True, enriched=True)
    else:
        print("\n§B8 · fixture org written by HEAD")
        slug, _nodes = build_fixture()
        walk("B8 fixture", store.DATA_ROOT, slug, expect_typed=True, enriched=False)

    print()
    for label, tb in FAIL:
        print(f"── FAIL {label}\n{tb}")
    print("══════════════════════════════════════════════════════════════════════")
    print(f"{PASS} checks passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
