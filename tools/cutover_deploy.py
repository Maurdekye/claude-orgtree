"""Launch the SQLite cutover DETACHED, and the one probe it needs.

    python tools/cutover_deploy.py <data-root>

WHY A LAUNCHER AT ALL.  `tools/cutover_deploy.ps1` stops the backend at its
second step, and every agent on this machine runs inside that backend.  A
script started from an agent's own shell is a child of that shell: it dies at
the stop, and steps 3-5 -- the migration, the export that makes a rollback
possible, the deploy -- never happen.  So the sequence has to be handed to a
process with no parent to lose.

HOW THE DETACH IS DONE, and why it is COPIED rather than invented: this is the
shape `supervisor._detached_spawn` already uses to launch `update.ps1`, which
is the one process on this machine that is known to survive the backend it
restarts.  Specifically:

  * `CREATE_NO_WINDOW`, *not* `DETACHED_PROCESS`.  DETACHED_PROCESS detaches
    the child from the console and with it goes every write to the redirected
    handle -- measured there at 0 of 4 lines reaching the log.  Survival is
    not what DETACHED_PROCESS buys: on Windows a child already outlives its
    parent, and that flag governs the console, not the lifetime.  For this
    script the log IS the deliverable, so a silent child is a failed run.
  * `CREATE_NEW_PROCESS_GROUP`, so a Ctrl-C or a console close in the caller's
    session does not travel to the cutover.
  * the argv and the pid are written to the log BEFORE the child can say
    anything, so "never started", "started and died mute" and "its output
    never reached this file" are three different logs rather than one.

`backend/tests/test_cutover_deploy.py` pins these flags against
`supervisor.py`'s own, so the two cannot drift apart unnoticed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# supervisor.py `_detached_spawn`: CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def probe_claim(root: str, repo: str) -> int:
    """Prove the data root is not held -- the check that actually decides.

    A quiet port is NOT proof a backend stopped: a process can have dropped
    its listener while still holding the root, and the migration would then
    refuse (or, with the timing the other way, run beside something still
    writing).  What decides is `store.claim_data_root`'s OS lock, so that is
    what is tested: take it, then let go by exiting.

    The root is claimed as a NON-`DATA_ROOT` root on purpose.  `DATA_ROOT` is
    pointed at a throwaway directory and this root is passed explicitly, which
    is the documented "claimed only -- never migrated, never refused for JSON"
    path.  A probe that could migrate something would be a probe nobody could
    safely run.
    """
    tmp = tempfile.mkdtemp(prefix="orgtree-cutover-probe-")
    try:
        os.environ["ORGTREE_DATA"] = tmp
        os.environ["ORGTREE_STORE"] = "json"
        sys.path.insert(0, os.path.join(repo, "backend"))
        from orgtree import store                             # noqa: PLC0415
        target = os.path.abspath(root)
        if os.path.abspath(store.DATA_ROOT) == target:
            # belt and braces: if this were ever true the claim would run the
            # migration/mismatch arms, and a PROBE must never convert anything
            print("probe misconfigured: DATA_ROOT resolved to the target root",
                  file=sys.stderr)
            return 3
        try:
            store.claim_data_root(target)
        except store.DataRootBusy as e:
            print(f"owner lock HELD: {e}", file=sys.stderr)
            return 1
        print(f"owner lock acquired on {target} -- nothing else holds this root")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def spawn(root: str, repo: str, extra: list[str]) -> int:
    root = os.path.abspath(root)
    repo = os.path.abspath(repo)
    ps1 = os.path.join(repo, "tools", "cutover_deploy.ps1")
    if not os.path.isfile(ps1):
        print(f"no {ps1}", file=sys.stderr)
        return 2
    if not os.path.isdir(os.path.join(root, "orgs")):
        print(f"not a data root (no orgs/ under {root!r})", file=sys.stderr)
        return 2
    log = os.path.join(
        root, "cutover-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        + ".log")
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", ps1, "-Root", root, "-Repo", repo] + extra
    with open(log, "ab") as lf:
        lf.write((f"== orgtree cutover launched {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                  f"by pid {os.getpid()} ==\n").encode())
        p = subprocess.Popen(
            args, cwd=repo, stdout=lf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
        lf.write(f"-- spawned pid {p.pid}: {args}\n".encode())
    print(f"cutover launched detached, pid {p.pid}")
    print(f"log: {log}")
    # A short liveness check, and no more.  A child that dies instantly -- a
    # PowerShell parse error, a refused anchor -- would otherwise be reported
    # as a successful launch, and the caller may be killed moments from now
    # and never get to look.  Anything past this point outlives us by design.
    time.sleep(2.0)
    rc = p.poll()
    if rc is not None:
        print(f"⚠ THE CUTOVER EXITED IMMEDIATELY (rc {rc}) -- it did not run.",
              file=sys.stderr)
        try:
            with open(log, "rb") as f:
                print(f.read().decode("utf-8", "replace")[-3000:],
                      file=sys.stderr)
        except OSError:
            pass
        return 1
    print("still running after 2s; it now outlives this process.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", help="the data root to cut over")
    ap.add_argument("--repo", default=REPO,
                    help="orgtree checkout to deploy (default: this script's)")
    ap.add_argument("--probe-claim", metavar="ROOT",
                    help="internal: prove ROOT's owner lock is free")
    ap.add_argument("--drill", action="store_true",
                    help="drill: stop before the deploy (refuses the default "
                         "data root)")
    ap.add_argument("--drill-export-fail", action="store_true",
                    help="drill: treat export-verify as failed")
    ap.add_argument("--drill-migrate-fail", action="store_true",
                    help="drill: treat migrate as failed without running it")
    ap.add_argument("--drill-skip-first-relaunch", action="store_true",
                    help="drill: force the post-migration recovery to its "
                         "rollback rung")
    ap.add_argument("--allow-worktree", action="store_true",
                    help="permit deploying from a linked git worktree")
    a = ap.parse_args()

    if a.probe_claim:
        return probe_claim(a.probe_claim, a.repo)
    if not a.root:
        ap.error("a data root is required")

    # ⚠ A LINKED WORKTREE IS NOT THE DEPLOYED CHECKOUT.  Everyone on this
    # machine works in worktrees under their scratch folder, and this script
    # is edited in one.  `update.ps1` derives the tree it builds from its own
    # location, so a cutover launched out of a worktree would pull, build and
    # deploy that worktree -- a tree nobody signed off on -- and the first
    # symptom would be behaviour that exists in no commit.  In the primary
    # checkout `.git` is a directory; in a linked worktree it is a file.  That
    # is what is tested, rather than a hard-coded path, so it stays true if
    # the install moves.
    dotgit = os.path.join(os.path.abspath(a.repo), ".git")
    if os.path.isfile(dotgit) and not a.allow_worktree:
        print(f"REFUSING: {a.repo} is a linked git worktree, not the deployed\n"
              f"checkout. Deploying it would build a tree nobody signed off on.\n"
              f"Pass --repo <the real checkout>, or --allow-worktree if you\n"
              f"really mean this one (drills do).", file=sys.stderr)
        return 2

    extra = []
    if a.drill:
        extra.append("-DrillNoDeploy")
    if a.drill_export_fail:
        extra.append("-DrillForceExportFail")
    if a.drill_migrate_fail:
        extra.append("-DrillForceMigrateFail")
    if a.drill_skip_first_relaunch:
        extra.append("-DrillSkipFirstRelaunch")
    return spawn(a.root, a.repo, extra)


if __name__ == "__main__":
    raise SystemExit(main())
