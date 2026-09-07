"""Real Git, fixture-only remotes/hooks/data. Run as a script or with unittest."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

TMP = Path(tempfile.mkdtemp(prefix="orgtree-git-tests-"))
DATA = TMP / "data"
DATA.mkdir()
CONFIG = TMP / "gitconfig"
CONFIG.write_text("[user]\n name = Git Fixture\n email = fixture@example.invalid\n[init]\n defaultBranch = main\n", encoding="utf-8")
os.environ.update(ORGTREE_DATA=str(DATA), HOME=str(TMP), USERPROFILE=str(TMP),
                  GIT_CONFIG_GLOBAL=str(CONFIG), GIT_CONFIG_NOSYSTEM="1",
                  GIT_TERMINAL_PROMPT="0")
for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_WORK_TREE", "ORGTREE_WARM"):
    os.environ.pop(key, None)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from orgtree import gitworkspace as gw, gitsettings, gitrunner, store, appsettings

assert Path(store.DATA_ROOT).resolve() == DATA.resolve(), "INERT: wrong storage root"
assert Path(gw.__file__).resolve().is_relative_to(ROOT), "INERT: wrong code imported"


def git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=path, env=os.environ.copy(),
                            capture_output=True, text=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise AssertionError(f"fixture git {args[0]} failed: {result.stderr}")
    return result.stdout.strip()


class Fixture:
    def __init__(self) -> None:
        self.base = Path(tempfile.mkdtemp(dir=TMP, prefix="fixture-"))
        self.remote = self.base / "remote.git"
        self.seed = self.base / "seed"
        self.clone = self.base / "clone"
        self.seed.mkdir()
        git(self.seed, "init", "-q")
        self.commit(self.seed, "first.txt", "first\n")
        git(self.base, "clone", "--bare", str(self.seed), str(self.remote))
        git(self.base, "clone", str(self.remote), str(self.clone))
        git(self.seed, "remote", "add", "origin", str(self.remote))
        self.org = store.create_org("git-" + self.base.name, [str(self.base)])
        self.slug = self.org.d["slug"]
        self.repo = gw.register(self.slug, str(self.clone))
        self.rid = self.repo["id"]

    def commit(self, path: Path, name: str, body: str) -> str:
        (path / name).write_text(body, encoding="utf-8")
        git(path, "add", "--", name)
        git(path, "commit", "-qm", "fixture " + name)
        return git(path, "rev-parse", "HEAD")

    def snapshot(self, **kwargs):
        return gw.snapshot(self.slug, self.rid, **kwargs)

    def history(self, count=3005, branch="long"):
        initial = git(self.clone, "rev-parse", "HEAD")
        stream = []
        for i in range(1, count + 1):
            parent = initial if i == 1 else f":{i - 1}"
            stream.append(f"commit refs/heads/{branch}\nmark :{i}\ncommitter Fixture <fixture@example.invalid> 1700000000 +0000\ndata 7\nhistory\nfrom {parent}\n\n")
        proc = subprocess.run(["git", "fast-import", "--quiet"], cwd=self.clone, input="".join(stream).encode(),
                              capture_output=True, timeout=30, env=os.environ.copy(),
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        return initial

    @staticmethod
    def branch(snap):
        return next(b for b in snap["branches"] if b["ref"] == "refs/heads/main")


class GitWorkspaceTests(unittest.TestCase):
    def setUp(self):
        appsettings.set_git_periodic_fetch_enabled(False)

    def test_app_setting_roundtrip_preserves_legacy_and_unopened_repositories(self):
        from fastapi.testclient import TestClient
        from orgtree import api as main_api
        client = TestClient(main_api.app)
        f, unopened = Fixture(), Fixture()
        gitsettings.change(lambda d: d["repositories"][unopened.rid].update(auto_fetch=True))
        before = Path(gitsettings.path()).read_bytes()
        self.assertFalse(client.get("/api/app-settings/runtime").json()["git_periodic_fetch_enabled"])
        changed = client.put("/api/app-settings/runtime", json={"git_periodic_fetch_enabled": True})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertTrue(changed.json()["git_periodic_fetch_enabled"])
        self.assertEqual(Path(gitsettings.path()).read_bytes(), before, "App preference cannot rewrite legacy Git registry flags")
        scheduler = gw.FetchScheduler()
        try:
            self.assertEqual(scheduler.jobs, {}, "Construction never starts a registry sweep")
            self.assertTrue(scheduler.request(f.slug, f.rid, 100))
            scheduler.stop()
            self.assertTrue(gw.repository(f.slug, f.rid)["observations"])
            self.assertEqual(gw.repository(unopened.slug, unopened.rid)["observations"], {})
            other = store.create_org("same-repo-other-org", [str(f.base)])
            self.assertEqual(gw.register(other.d["slug"], str(f.clone))["id"], f.rid)
            self.assertFalse(scheduler.request(other.d["slug"], f.rid, 110), "Common-dir cadence spans org views")
            off = client.put("/api/app-settings/runtime", json={"git_periodic_fetch_enabled": False})
            self.assertFalse(off.json()["git_periodic_fetch_enabled"])
            self.assertFalse(scheduler.request(f.slug, f.rid, 200))
        finally:
            scheduler.stop()

    def test_repository_list_does_not_probe_discovery_roots(self):
        from orgtree import gitapi
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        f = Fixture()
        paths = []
        for i in range(200):
            path = f.base / f"not-a-repo-{i}"
            path.mkdir()
            paths.append(str(path))
        app = FastAPI(); app.include_router(gitapi.router)
        client = TestClient(app)
        prefix = f"/api/orgs/{f.slug}/git"
        with patch.object(gw, "roots", return_value=paths + [str(f.clone)]):
            with patch.object(gw.gr, "run", wraps=gw.gr.run) as runner:
                listed = client.get(prefix + "/repositories")
                self.assertEqual(listed.status_code, 200, listed.text)
                self.assertEqual([r["id"] for r in listed.json()["repositories"]], [f.rid])
                self.assertNotIn("discovery", listed.json())
                self.assertEqual(runner.call_count, 0, "Listing must not probe disk/Git roots")
                discovered = client.post(prefix + "/discover", json={})
                self.assertEqual(discovered.status_code, 200, discovered.text)
                self.assertEqual(runner.call_count, 200, "Control: discovery still probes every bounded root")
                self.assertEqual(discovered.json()["scanned"], 200)
                self.assertTrue(discovered.json()["truncated"])
                self.assertEqual(discovered.json()["candidates"], [])
            # Explicit scanning still finds a real repository beyond the default cap.
            found = client.post(prefix + "/discover", json={"path": str(f.clone)})
            self.assertEqual(found.status_code, 200, found.text)
            self.assertEqual(found.json()["candidates"][0]["path"], gw.canonical(str(f.clone)))
            self.assertEqual(client.post(prefix + "/discover", json={"path": str(TMP)}).status_code, 403)
        with patch.object(gw, "roots", return_value=[str(f.clone)]):
            self.assertEqual(client.post(prefix + "/discover", json={}).json()["candidates"][0]["path"], gw.canonical(str(f.clone)))

    def test_registry_worktrees_and_settings(self):
        f = Fixture()
        branch = f.base / "linked"
        git(f.clone, "worktree", "add", "-b", "task", str(branch))
        self.assertEqual(gw.register(f.slug, str(branch))["id"], f.rid)
        snap = f.snapshot()
        self.assertEqual(len(snap["worktrees"]), 2)
        self.assertEqual(snap["config"]["trunk"], "refs/heads/main")
        self.assertEqual(f.branch(snap)["sync"]["state"], "in_sync")
        self.assertEqual(len({b["oid"] for b in snap["history"]["nodes"]}), 1)
        rev = gitsettings.load()["revision"]
        gw.patch_settings(f.slug, f.rid, {"trunk": "refs/heads/main"}, rev)
        with self.assertRaises(gitsettings.SettingsError):
            gw.patch_settings(f.slug, f.rid, {"trunk": "refs/heads/main"}, rev)
        with self.assertRaises(gw.GitError):
            gw.patch_settings(f.slug, f.rid, {"auto_fetch": True}, gitsettings.load()["revision"])

    def test_changes_have_separate_categories(self):
        f = Fixture()
        (f.clone / "first.txt").write_text("staged\n", encoding="utf-8")
        git(f.clone, "add", "first.txt")
        (f.clone / "first.txt").write_text("unstaged\nmore\n", encoding="utf-8")
        (f.clone / "untracked text.txt").write_text("a\nb", encoding="utf-8")
        (f.clone / "binary.dat").write_bytes(b"a\0b")
        state = gw.changes(f.repo, gw.worktrees(f.repo)[0])
        self.assertEqual(state["count"], 3)
        entries = {r["path"]: r for r in state["files"]}
        self.assertEqual(entries["first.txt"]["staged"]["added"], 1)
        self.assertEqual(entries["first.txt"]["unstaged"]["added"], 2)
        self.assertEqual(entries["untracked text.txt"]["untracked"]["added"], 2)
        self.assertIsNone(entries["binary.dat"]["untracked"]["added"])
        self.assertEqual(entries["binary.dat"]["untracked"]["reason"], "binary")

    def test_push_all_tip_and_hook_rejection_positive_control(self):
        f = Fixture()
        middle = f.commit(f.clone, "second.txt", "second\n")
        tip = f.commit(f.clone, "third.txt", "third\n")
        hook = f.clone / ".git" / "hooks" / "pre-push"
        sentinel = f.clone / ".git" / "hook-ran"
        hook.write_text('#!/bin/sh\nprintf ran > "$(git rev-parse --git-dir)/hook-ran"\necho "fixture rejects push" >&2\nexit 1\n', encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        snap = f.snapshot()
        self.assertIn(middle, f.branch(snap)["unique"]["local"])
        before = git(f.remote, "rev-parse", "refs/heads/main")
        result = gw.operate(f.slug, f.rid, "push", snap["token"], "refs/heads/main")
        self.assertEqual(result["state"], "blocked")
        self.assertIn("fixture rejects push", result["message"])
        self.assertTrue(sentinel.exists())
        self.assertEqual(git(f.remote, "rev-parse", "refs/heads/main"), before)
        hook.write_text('#!/bin/sh\nprintf accepted > "$(git rev-parse --git-dir)/hook-ran"\nexit 0\n', encoding="utf-8", newline="\n")
        result = gw.operate(f.slug, f.rid, "push", snap["token"], "refs/heads/main")
        self.assertEqual(result["state"], "success", result)
        self.assertEqual(sentinel.read_text(), "accepted")
        self.assertEqual(git(f.remote, "rev-parse", "refs/heads/main"), tip)
        self.assertNotEqual(tip, middle)

    def test_pull_full_tip_and_untracked_refusal(self):
        f = Fixture()
        f.commit(f.seed, "incoming.txt", "incoming\n")
        tip = f.commit(f.seed, "incoming2.txt", "incoming2\n")
        git(f.seed, "push", "origin", "main")
        gw.fetch(f.slug, f.rid)
        snap = f.snapshot()
        self.assertEqual(f.branch(snap)["sync"]["behind"], 2)
        harmless = f.clone / "evidence.txt"
        harmless.write_text("fixture evidence", encoding="utf-8")
        with self.assertRaisesRegex(gw.GitError, "untracked: evidence.txt"):
            gw.operate(f.slug, f.rid, "pull", snap["token"], "refs/heads/main")
        self.assertNotEqual(git(f.clone, "rev-parse", "HEAD"), tip)
        harmless.unlink()
        result = gw.operate(f.slug, f.rid, "pull", snap["token"], "refs/heads/main")
        self.assertEqual(result["state"], "success", result)
        self.assertEqual(git(f.clone, "rev-parse", "HEAD"), tip)

    def test_comparison_batch_fallback_and_missing_upstream(self):
        f = Fixture()
        git(f.clone, "checkout", "-b", "topic")
        f.commit(f.clone, "ahead.txt", "ahead\n")
        git(f.clone, "push", "-u", "origin", "topic")
        f.commit(f.clone, "ahead2.txt", "ahead2\n")
        git(f.clone, "branch", "no-upstream")
        with patch.object(gitrunner, "run", wraps=gitrunner.run) as recorded:
            modern = f.snapshot(batch=True)
            self.assertFalse(any(call.args[1][0] == "merge-base" for call in recorded.call_args_list))
        # Keep a separately executed Git-command oracle for the graph calculation.
        with patch.object(gitrunner, "run", wraps=gitrunner.run) as recorded:
            fallback = f.snapshot(batch=False)
            self.assertTrue(any(call.args[1][:3] == ["rev-list", "--left-right", "--count"] for call in recorded.call_args_list), "Git oracle did not execute")
        topic = lambda s: next(b for b in s["branches"] if b["ref"] == "refs/heads/topic")
        self.assertEqual(topic(modern)["against_trunk"]["ahead"], 2)
        self.assertEqual(topic(modern)["sync"]["ahead"], 1)
        self.assertEqual(topic(modern)["against_trunk"], topic(fallback)["against_trunk"])
        self.assertEqual(topic(modern)["sync"], topic(fallback)["sync"])
        selected = gw.snapshot(f.slug, f.rid, ["refs/heads/main", "refs/heads/no-upstream"])
        untracked = next(b for b in selected["branches"] if b["ref"].endswith("no-upstream"))
        self.assertEqual(untracked["sync"]["state"], "no_upstream")
        self.assertIsNone(untracked["sync"]["ahead"])

    def test_topology_comparisons_match_git_across_divergence_and_unrelated_roots(self):
        f = Fixture()
        f.history(180, "ahead")
        git(f.clone, "branch", "--set-upstream-to", "origin/main", "ahead")
        git(f.clone, "checkout", "-b", "diverged")
        f.commit(f.clone, "local.txt", "local\n")
        git(f.clone, "branch", "--set-upstream-to", "origin/main", "diverged")
        f.commit(f.seed, "remote.txt", "remote\n")
        git(f.seed, "push", "origin", "main")
        gw.fetch(f.slug, f.rid)
        git(f.clone, "checkout", "--orphan", "unrelated")
        git(f.clone, "commit", "-qm", "unrelated root")
        git(f.clone, "branch", "--set-upstream-to", "origin/main", "unrelated")
        git(f.clone, "branch", "gone")
        git(f.clone, "config", "branch.gone.remote", "origin")
        git(f.clone, "config", "branch.gone.merge", "refs/heads/missing")
        selected = ["refs/heads/" + name for name in ("main", "ahead", "diverged", "unrelated", "gone")]
        fast, oracle = f.snapshot(selected=selected), f.snapshot(selected=selected, batch=False)
        for a, b in zip(fast["branches"], oracle["branches"]):
            self.assertEqual(a["ref"], b["ref"])
            self.assertEqual(a["sync"], b["sync"], a["ref"])
            self.assertEqual(a["against_trunk"], b["against_trunk"], a["ref"])
            self.assertEqual(a["classified"], b["classified"])
        by_name = {b["ref"].split("/")[-1]: b for b in fast["branches"]}
        self.assertEqual(by_name["ahead"]["sync"], {"state": "diverged", "ahead": 180, "behind": 1})
        self.assertEqual(by_name["diverged"]["sync"]["state"], "diverged")
        self.assertEqual(by_name["unrelated"]["sync"]["state"], "unrelated")
        self.assertEqual(by_name["gone"]["sync"]["state"], "upstream_gone")
        self.assertGreater(fast["total_commits"], gw.PAGE_SIZE)

    def test_shallow_comparisons_remain_unknown(self):
        f = Fixture(); f.commit(f.seed, "second.txt", "second\n")
        shallow = f.base / "shallow"
        git(f.base, "clone", "--depth=1", f.seed.as_uri(), str(shallow))
        git(shallow, "checkout", "-b", "ahead")
        f.commit(shallow, "ahead.txt", "ahead\n")
        git(shallow, "branch", "--set-upstream-to", "origin/main", "ahead")
        repo = gw.register(f.slug, str(shallow))
        for batch in (True, False):
            snap = gw.snapshot(f.slug, repo["id"], batch=batch)
            branch = next(b for b in snap["branches"] if b["ref"] == "refs/heads/ahead")
            self.assertTrue(snap["shallow"])
            self.assertEqual(branch["sync"], {"state": "shallow", "ahead": None, "behind": None})
            self.assertFalse(branch["classified"])

    def test_unborn_and_history_pages(self):
        f = Fixture()
        empty = f.base / "empty"
        empty.mkdir()
        git(empty, "init", "-q")
        repo = gw.register(f.slug, str(empty))
        snap = gw.snapshot(f.slug, repo["id"])
        self.assertEqual(snap["history"]["nodes"], [])
        self.assertIsNone(snap["worktrees"][0].get("oid"))
        old = gw.PAGE_SIZE
        gw.PAGE_SIZE = 1
        try:
            f.commit(f.clone, "p2.txt", "p2\n")
            tip = f.commit(f.clone, "p3.txt", "p3\n")
            snap = f.snapshot()
            first = snap["history"]
            self.assertEqual(first["nodes"][0]["oid"], tip)
            self.assertIsNotNone(first["next_cursor"])
            f.commit(f.clone, "later.txt", "later\n")
            second = gw.history(f.slug, f.rid, first["next_cursor"])
            self.assertEqual(first["nodes"][0]["parents"], [second["nodes"][0]["oid"]])
            self.assertNotEqual(first["nodes"][0]["oid"], second["nodes"][0]["oid"])
            with self.assertRaises(gw.GitError):
                gw.history(f.slug, f.rid, first["next_cursor"] + "wrong")
        finally:
            gw.PAGE_SIZE = old

    def test_diverged_and_changed_snapshot_do_not_push(self):
        f = Fixture()
        f.commit(f.seed, "remote-change.txt", "remote\n")
        git(f.seed, "push", "origin", "main")
        f.commit(f.clone, "local-change.txt", "local\n")
        gw.fetch(f.slug, f.rid)
        snap = f.snapshot()
        self.assertEqual(f.branch(snap)["sync"], {"state": "diverged", "ahead": 1, "behind": 1})
        before = git(f.remote, "rev-parse", "main")
        with self.assertRaisesRegex(gw.GitError, "non-diverged"):
            gw.operate(f.slug, f.rid, "push", snap["token"], "refs/heads/main")
        with self.assertRaisesRegex(gw.GitError, "diverged"):
            gw.operate(f.slug, f.rid, "pull", snap["token"], "refs/heads/main")
        self.assertEqual(git(f.remote, "rev-parse", "main"), before)
        f.commit(f.clone, "next.txt", "next\n")
        with self.assertRaisesRegex(gw.GitError, "changed since"):
            gw.operate(f.slug, f.rid, "push", snap["token"], "refs/heads/main")

    def test_freshness_distinguishes_unwatched_and_failing(self):
        f = Fixture()
        gw.fetch(f.slug, f.rid)
        repo = gw.repository(f.slug, f.rid)
        observed = repo["observations"]["origin"]["success_at"]
        self.assertEqual(gw.freshness(repo, "origin", now=observed + 3600)["state"], "not_watched")
        appsettings.set_git_periodic_fetch_enabled(True)
        self.assertEqual(gw.freshness(repo, "origin", now=observed + 3600)["state"], "stale")
        repo["observations"]["origin"].update(error="fixture fetch failed", attempt_at=observed + 3590)
        failed = gw.freshness(repo, "origin", now=observed + 3600)
        self.assertEqual(failed["state"], "failing")
        self.assertEqual(failed["age_seconds"], 3600)
        self.assertIn("fixture fetch failed", failed["error"])

    def test_hook_timeout_is_bounded_and_preserves_remote(self):
        f = Fixture()
        f.commit(f.clone, "timeout.txt", "timeout\n")
        hook = f.clone / ".git" / "hooks" / "pre-push"
        hook.write_text('#!/bin/sh\nprintf ran > "$(git rev-parse --git-dir)/timeout-hook"\nsleep 10\nexit 1\n', encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        before = git(f.remote, "rev-parse", "main")
        started = time.monotonic()
        result = gitrunner.run(str(f.clone), ["push", "origin", "HEAD:refs/heads/main"], timeout=.5, read=False)
        self.assertEqual(result.failure, "timeout")
        self.assertLess(time.monotonic() - started, 7)
        self.assertTrue((f.clone / ".git" / "timeout-hook").exists())
        self.assertEqual(git(f.remote, "rev-parse", "main"), before)

    def test_api_permission_matrix_and_operator_control(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from orgtree import gitapi, api as main_api
        f = Fixture()
        app = FastAPI()
        app.include_router(gitapi.router)
        client = TestClient(app)
        prefix = f"/api/orgs/{f.slug}/git"
        snap = client.get(f"{prefix}/{f.rid}/snapshot")
        self.assertEqual(snap.status_code, 200, snap.text)
        token = snap.json()["token"]
        routes = [("GET", "/repositories", None), ("POST", "/discover", {"path": str(f.base)}),
                  ("POST", "/repositories", {"path": str(f.clone)}),
                  ("GET", f"/{f.rid}/settings", None), ("PATCH", f"/{f.rid}/settings", {"revision": 0, "values": {}}),
                  ("POST", f"/{f.rid}/selection", None), ("GET", f"/{f.rid}/observation", None),
                  ("GET", f"/{f.rid}/snapshot", None), ("GET", f"/{f.rid}/history?cursor=bad", None),
                  ("GET", f"/{f.rid}/worktrees/{snap.json()['worktrees'][0]['id']}/changes", None),
                  ("POST", f"/{f.rid}/links", {"branch": "refs/heads/main", "item": "unknown"}),
                  ("DELETE", f"/{f.rid}/links", {"branch": "refs/heads/main", "item": "unknown"}),
                  ("POST", f"/{f.rid}/fetch", None), ("POST", f"/{f.rid}/push", {"snapshot": token, "branch": "refs/heads/main"}),
                  ("POST", f"/{f.rid}/watch", None),
                  ("POST", f"/{f.rid}/pull", {"snapshot": token, "branch": "refs/heads/main"}),
                  ("DELETE", f"/{f.rid}/registration", None)]
        # The router guard is structural. Keep the behavioral controls complete
        # as routes grow: an unlisted seventeenth route must fail this test.
        values = {"slug": f.slug, "rid": f.rid, "wid": snap.json()["worktrees"][0]["id"]}
        actual_routes = {(method, route.path.format_map(values))
                         for route in gitapi.router.routes for method in route.methods}
        tested_routes = {(method, prefix + suffix.partition("?")[0]) for method, suffix, _ in routes}
        self.assertEqual(actual_routes, tested_routes, "Git route permission matrix is incomplete")
        for state_key in ("public_slug", "bridge_slug"):
            async def scoped(scope, receive, send, key=state_key):
                scope.setdefault("state", {})[key] = f.slug
                await app(scope, receive, send)
            visitor = TestClient(scoped)
            for method, suffix, body in routes:
                with self.subTest(scope=state_key, route=suffix):
                    denied = visitor.request(method, prefix + suffix, json=body)
                    self.assertEqual(denied.status_code, 403, denied.text)
                    self.assertNotIn(str(f.base), denied.text)
                    self.assertEqual(main_api._public_denied(method, (prefix + suffix).split('?')[0], f.slug)[0], 403)
        # Same routes reach their actual handlers as operator, not a dead router.
        for method, suffix, body in routes:
            response = client.request(method, prefix + suffix, json=body)
            self.assertNotIn(response.status_code, (403, 404, 405, 500), (method, suffix, response.text))

    def test_large_tied_history_has_stable_unique_pages(self):
        f = Fixture()
        initial = f.history()
        git(f.clone, "branch", "--set-upstream-to", "origin/main", "long")
        snap = gw.snapshot(f.slug, f.rid, ["refs/heads/main", "refs/heads/long"])
        long = next(b for b in snap["branches"] if b["ref"] == "refs/heads/long")
        self.assertEqual(long["against_trunk"]["ahead"], 3005)
        page, ids, pages = snap["history"], [], 0
        while True:
            ids.extend(n["oid"] for n in page["nodes"])
            for node in page["nodes"]:
                self.assertEqual(node["lane"]["offset"], 0 if node["oid"] == initial else -330)
                self.assertEqual(node["comparisons"].get("refs/heads/long"), None if node["oid"] == initial else "local")
            pages += 1
            if not page["next_cursor"]:
                break
            page = gw.history(f.slug, f.rid, page["next_cursor"])
        self.assertEqual(len(ids), 3006)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertGreater(pages, 20)
        self.assertEqual(ids[0], long["oid"])
        self.assertIn(initial, [n['oid'] for n in snap['history']['nodes']], 'old trunk tip must be included in the initial page')
        self.assertEqual(snap['total_commits'], 3006)

    def test_scheduler_real_fetch_deduplicates_and_disables(self):
        f = Fixture()
        sibling = f.base / "sibling"
        git(f.clone, "worktree", "add", "-b", "sibling", str(sibling))
        self.assertEqual(gw.register(f.slug, str(sibling))["id"], f.rid)
        # Only this fixture is watched; previous tests may leave registry rows.
        gitsettings.change(lambda d: [r.update(auto_fetch=r["id"] == f.rid) for r in d["repositories"].values()])
        self.assertFalse(appsettings.git_periodic_fetch_enabled(), "Legacy per-repo flags cannot opt in globally")
        scheduler = gw.FetchScheduler()
        entered, release = threading.Event(), threading.Event()
        called, actual = [], gw.fetch
        def observed(slug, rid):
            called.append(rid); entered.set()
            if not release.wait(10):
                raise AssertionError("fixture release timed out")
            return actual(slug, rid)
        try:
            with patch.object(gw, "fetch", side_effect=observed):
                self.assertFalse(scheduler.request(f.slug, f.rid, 99))
                appsettings.set_git_periodic_fetch_enabled(True)
                self.assertTrue(scheduler.request(f.slug, f.rid, 100))
                self.assertTrue(entered.wait(5), "positive fetch control never ran")
                scheduler.request(f.slug, f.rid, 101); scheduler.request(f.slug, f.rid, 200)
                self.assertEqual(called, [f.rid], "in-flight jobs must coalesce")
                release.set(); scheduler.stop()
                self.assertIsNotNone(gw.repository(f.slug, f.rid)["observations"]["origin"].get("success_at"))
                scheduler.request(f.slug, f.rid, 120)
                self.assertEqual(called, [f.rid])
                scheduler.request(f.slug, f.rid, 131); scheduler.stop()
                self.assertEqual(called, [f.rid, f.rid])
                appsettings.set_git_periodic_fetch_enabled(False)
                scheduler.request(f.slug, f.rid, 300)
                self.assertEqual(called, [f.rid, f.rid])
        finally:
            release.set(); scheduler.stop()

    def test_fetch_preflight_failure_and_recovery_are_observable(self):
        f = Fixture()
        git(f.clone, "config", "remote.origin.fetch", "+refs/heads/*:refs/heads/*")
        before = git(f.clone, "rev-parse", "main")
        with self.assertRaisesRegex(gw.GitError, "tracking namespace"):
            gw.fetch(f.slug, f.rid)
        failed = gw.freshness(gw.repository(f.slug, f.rid), "origin")
        self.assertEqual(failed["state"], "failing")
        self.assertIn("tracking namespace", failed["error"])
        self.assertEqual(git(f.clone, "rev-parse", "main"), before)
        git(f.clone, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        gw.fetch(f.slug, f.rid)
        self.assertEqual(gw.freshness(gw.repository(f.slug, f.rid), "origin")["state"], "not_watched")

    def test_scheduler_caps_two_distinct_running_repositories(self):
        fixtures = [Fixture() for _ in range(3)]
        self.assertEqual(len({f.repo["common"] for f in fixtures}), 3)
        scheduler = gw.FetchScheduler()
        appsettings.set_git_periodic_fetch_enabled(True)
        entered = [threading.Event(), threading.Event()]
        release = threading.Event(); calls = []; actual = gw.fetch
        def held(slug, rid):
            calls.append(rid)
            for index, f in enumerate(fixtures[:2]):
                if rid == f.rid:
                    entered[index].set()
                    if not release.wait(10): raise AssertionError("INERT: release timed out")
            return actual(slug, rid)
        try:
            with patch.object(gw, "fetch", side_effect=held):
                for index, f in enumerate(fixtures[:2]):
                    self.assertTrue(scheduler.request(f.slug, f.rid, 100))
                    self.assertTrue(entered[index].wait(5), "INERT: concurrent fetch never started")
                third = fixtures[2]
                self.assertFalse(scheduler.request(third.slug, third.rid, 100), "Two DISTINCT running keys must fill the cap")
                self.assertEqual(set(calls), {f.rid for f in fixtures[:2]})
                release.set(); scheduler.stop()
                self.assertTrue(scheduler.request(third.slug, third.rid, 101), "Freed capacity must accept the third key")
                scheduler.stop()
                self.assertEqual(set(calls), {f.rid for f in fixtures})
                self.assertTrue(all(gw.repository(f.slug, f.rid)["observations"] for f in fixtures))
        finally:
            release.set(); scheduler.stop()

    def test_scheduler_disable_after_acceptance_before_fetch_start(self):
        f = Fixture(); scheduler = gw.FetchScheduler()
        appsettings.set_git_periodic_fetch_enabled(True)
        accepted, release = threading.Event(), threading.Event()
        actual_thread = threading.Thread
        class GatedThread(actual_thread):
            def run(self):
                if self.name == "git-fetch":
                    accepted.set()
                    if not release.wait(10): raise AssertionError("INERT: release timed out")
                super().run()
        try:
            with patch.object(gw.threading, "Thread", GatedThread), patch.object(gw, "fetch", wraps=gw.fetch) as fetch:
                self.assertTrue(scheduler.request(f.slug, f.rid, 100))
                self.assertTrue(accepted.wait(5), "INERT: accepted job never reached gate")
                appsettings.set_git_periodic_fetch_enabled(False)
                release.set(); scheduler.stop()
                self.assertEqual(fetch.call_count, 0, "A job disabled after acceptance must not fetch")
                self.assertEqual(gw.repository(f.slug, f.rid)["observations"], {})
                appsettings.set_git_periodic_fetch_enabled(True)
                self.assertTrue(scheduler.request(f.slug, f.rid, 131))
                scheduler.stop()
                self.assertEqual(fetch.call_count, 1, "Positive enabled control must run a real fixture fetch")
                self.assertTrue(gw.repository(f.slug, f.rid)["observations"])
        finally:
            release.set(); scheduler.stop()

    def test_qualified_many_to_many_links_and_historical_owner(self):
        from orgtree.ledger import USER
        f = Fixture()
        f.org.hire(USER, None, "haiku", 0, "owner")
        one = f.org.work_create(USER, "One ticket", "First fixture ticket", owner="owner")
        two = f.org.work_create(USER, "Two ticket", "Second fixture ticket", owner="owner")
        store.save_org(f.org)
        git(f.clone, "branch", "second")
        for branch, item in [("main", one), ("main", two), ("second", one)]:
            gw.link_item(f.slug, f.rid, f"refs/heads/{branch}", item["slug"])
        links = gw.associations(f.slug, f.repo, gw.org_facts(f.slug))
        self.assertEqual(len(links["refs/heads/main"]), 2)
        self.assertEqual(links["refs/heads/second"][0]["slug"], one["slug"])
        archived_id, _ = f.org._archive_session_in_place("owner")
        f.org.node("owner")["model"] = "sonnet"
        store.save_org(f.org)
        historical = gw.associations(f.slug, f.repo, gw.org_facts(f.slug))["refs/heads/main"][0]["owner"]
        self.assertFalse(historical["current"])
        self.assertEqual(historical["tier"], "haiku")
        self.assertEqual(historical["target"], archived_id)
        other = Fixture()
        self.assertEqual(gw.associations(other.slug, other.repo, gw.org_facts(other.slug)), {})
        git(f.clone, "branch", "-D", "second")
        self.assertIn("refs/heads/second", gw.associations(f.slug, f.repo, gw.org_facts(f.slug)))
        gw.link_item(f.slug, f.rid, "refs/heads/second", one["slug"], remove=True)
        self.assertNotIn("refs/heads/second", gw.associations(f.slug, f.repo, gw.org_facts(f.slug)))
        # Reopen through the active storage backend and independent registry.
        self.assertEqual(store.load_org(f.slug).d["slug"], f.slug)
        self.assertEqual(len(gitsettings.load()["links"]), 2)

    def test_shallow_unrelated_remote_deletion_and_saved_selection(self):
        f = Fixture()
        git(f.clone, "checkout", "--orphan", "unrelated")
        f.commit(f.clone, "orphan.txt", "new root\n")
        snap = f.snapshot(selected=["refs/heads/main", "refs/heads/unrelated"])
        self.assertEqual(next(b for b in snap["branches"] if b["ref"].endswith("unrelated"))["against_trunk"]["state"], "unrelated")
        shallow_path = f.base / "shallow"
        git(f.base, "clone", "--depth=1", f.remote.as_uri(), str(shallow_path))
        f.commit(shallow_path, "ahead.txt", "shallow ahead\n")
        shallow = gw.register(f.slug, str(shallow_path))
        shallow_snap = gw.snapshot(f.slug, shallow["id"])
        self.assertTrue(shallow_snap["shallow"])
        self.assertEqual(f.branch(shallow_snap)["sync"], {"state": "shallow", "ahead": None, "behind": None})
        git(f.clone, "checkout", "main")
        git(f.clone, "checkout", "-b", "deleted-upstream")
        git(f.clone, "push", "-u", "origin", "deleted-upstream")
        git(f.clone, "push", "origin", ":deleted-upstream")
        gw.fetch(f.slug, f.rid)
        gone = f.snapshot()
        self.assertEqual(next(b for b in gone["branches"] if b["ref"].endswith("deleted-upstream"))["sync"]["state"], "upstream_gone")
        gw.patch_settings(f.slug, f.rid, {"remote": "origin", "trunk": "refs/heads/main"}, gitsettings.load()["revision"])
        git(f.clone, "remote", "remove", "origin")
        git(f.clone, "remote", "add", "other", str(f.remote))
        git(f.clone, "branch", "-D", "main")
        cfg = f.snapshot()["config"]
        self.assertEqual(cfg["remote"], "origin")
        self.assertTrue(cfg["remote_missing"])
        self.assertEqual(cfg["trunk"], "refs/heads/main")
        self.assertTrue(cfg["trunk_missing"])

    def test_conflicts_renames_and_detached_checkout_states(self):
        f = Fixture()
        detached = f.base / "detached"
        git(f.clone, "worktree", "add", "--detach", str(detached))
        git(detached, "mv", "first.txt", "renamed & literal$(echo).txt")
        states = f.snapshot()["worktrees"]
        renamed = next(w for w in states if w.get("detached"))
        self.assertTrue(all(w["changes"]["state"] == "not_read" and w["changes"]["count"] is None for w in states))
        details = gw.changes(f.repo, renamed)
        self.assertEqual(details["files"][0]["old_path"], "first.txt")
        self.assertEqual(details["files"][0]["path"], "renamed & literal$(echo).txt")
        self.assertEqual(gw.changes(f.repo, next(w for w in states if not w.get("detached")))["count"], 0)
        git(f.clone, "checkout", "-b", "conflict")
        f.commit(f.clone, "first.txt", "side\n")
        git(f.clone, "checkout", "main")
        f.commit(f.clone, "first.txt", "main\n")
        result = gitrunner.run(str(f.clone), ["merge", "conflict"], read=False)
        self.assertNotEqual(result.code, 0)
        state = gw.changes(f.repo, next(w for w in gw.worktrees(f.repo) if w.get("branch") == "refs/heads/main"))
        self.assertEqual(state["conflicted"], 1)
        self.assertIn("MERGE_HEAD", state["operations"])
        with self.assertRaisesRegex(gw.GitError, "current Git operation"):
            gw.operate(f.slug, f.rid, "pull", f.snapshot()["token"], "refs/heads/main")

    def test_argument_boundaries_and_corrupt_registry_refuse(self):
        f = Fixture()
        name = "--literal & $(echo).txt"
        (f.clone / name).write_text("literal\n", encoding="utf-8")
        state = gw.changes(f.repo, gw.worktrees(f.repo)[0])
        self.assertEqual(state["files"][0]["path"], name)
        snap = f.snapshot()
        actual = gitrunner.run
        seen = []
        def recording(cwd, args, **kwargs):
            self.assertIsInstance(args, list)
            seen.append(args)
            return actual(cwd, args, **kwargs)
        with patch.object(gitrunner, "run", side_effect=recording):
            gw.observation(f.slug, f.rid)
            self.assertTrue(seen, "positive command control never ran")
            before = len(seen)
            for hostile in ["--upload-pack=bad", "main;echo bad", "HEAD~1", "refs/heads/main\nother"]:
                with self.assertRaises(gw.GitError):
                    gw.operate(f.slug, f.rid, "push", snap["token"], hostile)
            self.assertEqual(len(seen), before, "hostile branch reached argv")
        redacted = gitrunner.redact("fatal https://user:secret@example.invalid/r?token=secret password=hidden")
        self.assertNotIn("secret", redacted)
        self.assertNotIn("hidden", redacted)
        file = Path(gitsettings.path())
        saved = file.read_bytes()
        try:
            file.write_bytes(b'{"version":999}')
            with self.assertRaises(gitsettings.SettingsError):
                gitsettings.change(lambda d: d.update(revision=0))
            self.assertEqual(file.read_bytes(), b'{"version":999}')
        finally:
            file.write_bytes(saved)

    def test_pull_refuses_checkout_switch_during_fetch_and_honors_post_merge_hook(self):
        f = Fixture()
        tip = f.commit(f.seed, "incoming.txt", "incoming\n")
        git(f.seed, "push", "origin", "main")
        git(f.clone, "branch", "other")
        snap = f.snapshot()
        actual = gw._fetch
        def switch_checkout(repo, remote):
            result = actual(repo, remote)
            git(f.clone, "checkout", "other")
            return result
        with patch.object(gw, "_fetch", side_effect=switch_checkout):
            with self.assertRaisesRegex(gw.GitError, "Checkout changed during fetch"):
                gw.operate(f.slug, f.rid, "pull", snap["token"], "refs/heads/main")
        self.assertNotEqual(git(f.clone, "rev-parse", "other"), tip)
        git(f.clone, "checkout", "main")
        hook = f.clone / ".git" / "hooks" / "post-merge"
        hook.write_text('#!/bin/sh\nprintf ran > "$(git rev-parse --git-dir)/post-hook-ran"\necho "fixture post-merge rejection" >&2\nexit 1\n', encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        result = gw.operate(f.slug, f.rid, "pull", f.snapshot()["token"], "refs/heads/main")
        self.assertTrue((f.clone / ".git" / "post-hook-ran").exists())
        self.assertEqual(git(f.clone, "rev-parse", "HEAD"), tip)
        self.assertEqual(result["after"], tip)
        self.assertIn(result["state"], ("success", "changed"), "a hook after HEAD moved cannot claim an unchanged blocked checkout")

    def test_octopus_merge_and_shared_ref_identity(self):
        f = Fixture()
        for branch in ("arm-a", "arm-b"):
            git(f.clone, "checkout", "-b", branch, "main")
            f.commit(f.clone, branch + ".txt", branch + "\n")
        git(f.clone, "checkout", "main")
        git(f.clone, "merge", "--no-ff", "-m", "Octopus fixture", "arm-a", "arm-b")
        git(f.clone, "branch", "alias", "arm-a")
        snap = f.snapshot(selected=["refs/heads/main", "refs/heads/arm-a", "refs/heads/arm-b", "refs/heads/alias"])
        nodes = {n["oid"]: n for n in snap["history"]["nodes"]}
        merge = nodes[git(f.clone, "rev-parse", "HEAD")]
        self.assertEqual(len(merge["parents"]), 3)
        self.assertEqual(len(nodes), 4)
        self.assertEqual(len(snap["history"]["nodes"]), 4)
        self.assertEqual(merge["lane"]["offset"], 0)
        self.assertTrue(all(parent in nodes for parent in merge["parents"]))

    def test_snapshot_retains_captured_checkout_when_checkout_moves(self):
        f = Fixture()
        tip = f.commit(f.clone, "second.txt", "second\n")
        git(f.clone, "checkout", "--detach", "HEAD~1")
        actual = gw.worktrees
        switched = False
        def move_checkout(repo):
            nonlocal switched
            result = actual(repo)
            if not switched:
                switched = True
                git(f.clone, "checkout", "--detach", tip)
            return result
        with patch.object(gw, "worktrees", side_effect=move_checkout):
            snap = f.snapshot()
        self.assertTrue(switched, "Checkout movement control must execute")
        self.assertTrue(snap["newer_available"])
        self.assertNotEqual(snap["worktrees"][0]["oid"], tip)
        self.assertEqual(f.snapshot()["worktrees"][0]["oid"], tip)

    def test_advancing_refs_preserve_capture_paging_and_revalidate_actions(self):
        f = Fixture(); f.history(3005)
        newer = git(f.clone, "rev-parse", "long")
        captured = git(f.clone, "rev-parse", "long~1")
        git(f.clone, "update-ref", "refs/heads/main", captured)
        actual = gw.refs
        moved = False
        def advancing(repo, *args, **kwargs):
            nonlocal moved
            rows = actual(repo, *args, **kwargs)
            if not moved:
                moved = True
                git(f.clone, "update-ref", "refs/heads/main", newer)
            return rows
        with patch.object(gw, "refs", side_effect=advancing), patch.object(gw, "changes", side_effect=AssertionError("Initial read scanned checkout files")):
            snap = f.snapshot(selected=["refs/heads/main"])
        self.assertTrue(moved, "Advancing ref control must execute")
        self.assertTrue(snap["newer_available"])
        self.assertEqual(f.branch(snap)["oid"], captured)
        self.assertLessEqual(snap["captured_at"], snap["created"])
        self.assertTrue(all(w["changes"]["count"] is None for w in snap["worktrees"]))
        # Each page sees another real ref write. Captured ranks/OIDs survive;
        # new descendants never appear in this token's pages.
        nodes = snap["history"]["nodes"][:]; cursor = snap["history"]["next_cursor"]
        pages = 0
        while cursor:
            git(f.clone, "update-ref", "refs/heads/main", newer if pages % 2 else captured)
            page = gw.history(f.slug, f.rid, cursor)
            nodes.extend(page["nodes"]); cursor = page["next_cursor"]; pages += 1
        self.assertGreater(pages, 20)
        self.assertEqual(len(nodes), snap["total_commits"])
        self.assertEqual(len({n["oid"] for n in nodes}), len(nodes))
        self.assertIn(captured, {n["oid"] for n in nodes})
        # A simultaneously inventoried checkout may already contain the newer
        # OID, but branch comparison membership must remain at the captured tip.
        self.assertNotIn("refs/heads/main", next((n for n in nodes if n["oid"] == newer), {}).get("comparisons", {}))
        self.assertEqual(sorted(n["rank"] for n in nodes), list(range(len(nodes))))
        git(f.clone, "update-ref", "refs/heads/main", newer)
        with self.assertRaisesRegex(gw.GitError, "Branch changed since"):
            gw.operate(f.slug, f.rid, "push", snap["token"], "refs/heads/main")
        current = f.snapshot(selected=["refs/heads/main"])
        self.assertEqual(f.branch(current)["oid"], newer)
        self.assertEqual(gw.operate(f.slug, f.rid, "push", current["token"], "refs/heads/main")["after"], newer)

    def test_on_demand_checkout_details_are_current_and_detect_switch(self):
        from orgtree import gitapi
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        f = Fixture(); app = FastAPI(); app.include_router(gitapi.router); client = TestClient(app)
        snap = f.snapshot(); wt = snap["worktrees"][0]
        self.assertEqual(wt["changes"]["state"], "not_read")
        (f.clone / "after-capture.txt").write_text("untracked\n")
        url = f"/api/orgs/{f.slug}/git/{f.rid}/worktrees/{wt['id']}/changes"
        dirty = client.get(url)
        self.assertEqual(dirty.status_code, 200, dirty.text)
        self.assertEqual(dirty.json()["state"], "dirty")
        self.assertEqual(dirty.json()["files"][0]["path"], "after-capture.txt")
        self.assertGreaterEqual(dirty.json()["read_at"], snap["captured_at"])
        self.assertEqual(dirty.json()["head_oid"], wt["oid"])
        with self.assertRaisesRegex(gw.GitError, "after-capture.txt"):
            gw.operate(f.slug, f.rid, "pull", snap["token"], "refs/heads/main")
        git(f.clone, "branch", "other")
        actual = gw.changes
        def switched(repo, checkout):
            result = actual(repo, checkout)
            git(f.clone, "checkout", "other")
            return result
        with patch.object(gw, "changes", side_effect=switched):
            changed = client.get(url)
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertIn("Checkout changed", changed.text)
        self.assertEqual(client.get(url).json()["branch"], "refs/heads/other")


if __name__ == "__main__":
    unittest.main()
