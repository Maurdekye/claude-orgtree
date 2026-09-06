"""B8 / B13 — ROLLBACK: the previous release reads documents this release wrote.

Typed rows carry `ev` beside the frozen `body`/`text` the old code renders. This
harness runs the REAL previous release (a git worktree at ROLLBACK_BASE, the main
tip at landing) as a separate process, under a THROWAWAY data root that is verified
before the first `orgtree` import on both sides, over a document written by HEAD:

    B8  · old readers over rows with `ev`  ==  HEAD readers with the new keys stripped
          (inbox / history / chat / user inbox JSON, diff-clean); the old code's own
          save (it posts one mail) preserves every `ev` bit-exact for HEAD to decode.
    B13 · the same walk over a REAL export: `--doc <path-to-an-ORGTREE_DATA-copy>`
          (the coordinator supplies a read-only export outside the live root; this
          harness never opens the live root). Without --doc, the fixture org is used.

ROLLBACK_BASE MUST be the main tip at landing — the landing checklist re-pins it;
the first check asserts it is an ancestor of HEAD so it cannot silently go stale.

    python backend/tests/test_events_rollback.py [--doc <data-root-copy> --slug <org>]
"""
from __future__ import annotations

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

from orgtree import events, store                                # noqa: E402
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
        o.post_mail(USER, nodes[0], "posted by the previous release")
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
        assert os.path.isfile(os.path.join(ROLLBACK_WT, "backend", "orgtree", "ledger.py")), \
            f"no worktree at {ROLLBACK_WT}: git worktree add --detach <path> {ROLLBACK_BASE[:7]}"
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
        print(f"\n§B13 · real export: {doc}")
        src_root, slug = doc, slug_arg
        assert slug, "--slug is required with --doc"
        nodes = [k for k, v in store.load_org(slug).nodes.items() if v["state"] == "live"][:6] \
            if False else None
    else:
        print("\n§B8 · fixture org written by HEAD")
        slug, nodes = build_fixture()
        src_root = store.DATA_ROOT
    if nodes is None:
        # --doc: read the org under the given root in a subprocess-free way by copying first
        nodes = []

    # two copies: one for the old release (it will also SAVE), one for HEAD
    old_root = os.path.join(_TMP, "old", "data")
    new_root = os.path.join(_TMP, "new", "data")
    shutil.copytree(src_root, old_root)
    shutil.copytree(src_root, new_root)
    if not nodes:
        env_slug = slug
        p = subprocess.run([sys.executable, "-c",
                            "import os,sys;os.environ['ORGTREE_DATA']=sys.argv[1];sys.path.insert(0,sys.argv[2]);"
                            "from orgtree import store;o=store.load_org(sys.argv[3]);"
                            "print(','.join([k for k,v in o.nodes.items() if v['state']=='live'][:6]))",
                            new_root, BACKEND, env_slug], capture_output=True, text=True)
        nodes = [x for x in p.stdout.strip().splitlines()[-1].split(",") if x]
    assert nodes, "no live nodes to read"

    old_out: dict = {}
    new_out: dict = {}

    def _old():
        old_out.update(run_reader(os.path.join(ROLLBACK_WT, "backend"), old_root, slug, nodes,
                                  post=True))
        assert all(v["status"] == 200 for v in old_out.values()), \
            {k: v["status"] for k, v in old_out.items()}
    check("the previous release reads inbox/history/chat/user-inbox of the typed document (all 200) "
          "and saves it", _old)

    def _new():
        new_out.update(run_reader(BACKEND, new_root, slug, nodes, post=False))
        assert all(v["status"] == 200 for v in new_out.values())
    check("HEAD reads the same document (all 200)", _new)

    def _diff():
        assert old_out and new_out
        for k in sorted(new_out):
            a, b = strip(old_out[k]["body"]), strip(new_out[k]["body"])
            if a != b:
                ja, jb = json.dumps(a, sort_keys=True, indent=1), json.dumps(b, sort_keys=True, indent=1)
                import difflib
                d = "\n".join(list(difflib.unified_diff(ja.splitlines(), jb.splitlines(), "old", "new", lineterm=""))[:60])
                raise AssertionError(f"{k}: old-release output differs from HEAD (new keys stripped):\n{d}")
        # positive control: without stripping, HEAD's output DOES differ (the ev is there)
        assert any(old_out[k]["body"] != new_out[k]["body"] for k in new_out), \
            "the strip is vacuous — HEAD added nothing to any payload"
    check("B8 · every reader output is diff-clean between the releases once this release's new "
          "keys are stripped (positive control: unstripped, they differ)", _diff)

    def _roundtrip():
        os.environ["ORGTREE_DATA"] = old_root       # HEAD re-reads the doc the OLD code saved
        # a fresh process, so store binds to the old root
        p = subprocess.run([sys.executable, "-c", r'''
import os, sys, json
os.environ["ORGTREE_DATA"] = sys.argv[1]; sys.path.insert(0, sys.argv[2])
from orgtree import store, events
assert store.DATA_ROOT.startswith(sys.argv[1])
o = store.load_org(sys.argv[3])
n_typed = n_legacy = 0; bad = []
boxes = [*(o.d.get("mail") or {}).items(), *(o.d.get("mail_log") or {}).items()]
rows = [m for _, ms in boxes for m in ms] + list(o.d.get("user_inbox") or []) + list(o.d.get("user_mail_log") or [])
rows += [r for _, rs in (o.d.get("notices") or {}).items() for r in rs] + list(o.d.get("notice_log") or [])
for r in rows:
    d = events.decode(r.get("ev"), r)
    if d["status"] == "legacy": n_legacy += 1
    elif d["status"] == "ok": n_typed += 1
    else: bad.append(d)
posted = [m for _, ms in (o.d.get("mail") or {}).items() for m in ms if m.get("body") == "posted by the previous release"]
print(json.dumps({"typed": n_typed, "legacy": n_legacy, "bad": bad, "posted": len(posted)}))
''', old_root, BACKEND, slug], capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=BACKEND)
        assert p.returncode == 0, p.stderr[-2000:]
        res = json.loads(p.stdout.strip().splitlines()[-1])
        assert res["bad"] == [], res["bad"]
        assert res["typed"] >= 5, res
        assert res["posted"] == 1, "the old release's own save landed"
        assert res["legacy"] >= 1, res
    check("B8 · after the previous release SAVED the document, every `ev` still decodes ok and "
          "its own (legacy) row sits beside them", _roundtrip)

    print()
    for label, tb in FAIL:
        print(f"── FAIL {label}\n{tb}")
    print("══════════════════════════════════════════════════════════════════════")
    print(f"{PASS} checks passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
