"""Mutation harness for assignment-is-ownership, one-call staffing and review.

A check that reports "nothing wrong" has to prove it can report something. This
rewrites ONE behaviour at a time in a COPY of the tree, runs
`test_work_items.py` against the copy, and asserts the run fails AND that the
named check is the one that went red. A mutant the suite still passes is a
check that means nothing, and this exits non-zero for it.

Nothing here touches the working tree: every mutant is applied inside a fresh
temporary copy of backend/, which is deleted afterwards. Line endings inside
the copy are irrelevant — the copy is never committed.

    python backend/tests/_mutate_staffing.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)

# (name, relative file, original snippet, replacement, check label that must fail)
#
# ⚠ ANCHORS ARE WRITTEN WITH "\n" AND MATCHED AGAINST A CRLF WORKING TREE, so
# every anchor is normalised below rather than pasted with carriage returns.
MUTANTS: list[tuple[str, str, str, str, str]] = [
    (
        "assignment-notifies-nobody",
        "orgtree/ledger.py",
        "        if not notify or own == actor or own == USER:",
        "        if True or not notify or own == actor or own == USER:",
        "tells the agent it now holds the item",
    ),
    (
        # the escalation: "you were allowed to update" silently becoming "you
        # may manage", which would let a participant hand the item to a third
        # party in the same call that claims it
        "update-permission-counts-as-management",
        "orgtree/ledger.py",
        "        pre_manage = self._work_can_manage(actor, it)",
        "        pre_manage = self._work_can_manage(actor, it) or actor != USER",
        "an explicit owner wins and preserves it",
    ),
    (
        "reply-falls-back-to-the-last-updater",
        "orgtree/ledger.py",
        '        own = self._work_actor_node(it.get("owner"))\n        if not own:',
        '        own = (self._work_actor_node(it.get("owner"))\n'
        '               or self._work_actor_node(it.get("last_updater")))\n'
        "        if not own:",
        "reply reaches the ASSIGNMENT exactly",
    ),
    (
        "explicit-owner-ignored-on-update",
        "orgtree/ledger.py",
        '        tgt = str(owner or "").strip()\n'
        "        if tgt and tgt != actor and not pre_manage:",
        '        tgt = ""\n        if tgt and tgt != actor and not pre_manage:',
        "an explicit owner wins and preserves it",
    ),
    (
        "seat-not-driven-by-its-assignment",
        "orgtree/api.py",
        '            if not ares.get("deferred"):\n                seat_drive = True',
        "            if False:\n                seat_drive = True",
        "the seat is hired, assigned, told, and running",
    ),
    (
        "entering-review-needs-no-reviewer",
        "orgtree/ledger.py",
        '            if entering and not self._work_actor_node(it.get("reviewer")):',
        '            if False and entering and not self._work_actor_node(it.get("reviewer")):',
        "entering review names a reviewer",
    ),
    (
        "self-review-not-rechecked-at-decision-time",
        "orgtree/ledger.py",
        "        if actor != USER and actor == own:\n"
        "            # the self-review prohibition",
        "        if False and actor != USER and actor == own:\n"
        "            # the self-review prohibition",
        "self-review is re-checked at decision time",
    ),
    (
        "a-reviewer-may-post-a-status-update",
        "orgtree/ledger.py",
        "        if actor != USER and not pre_manage \\\n"
        '                and actor not in (it.get("participants") or []):',
        "        if False and actor != USER and not pre_manage \\\n"
        '                and actor not in (it.get("participants") or []):',
        "a named reviewer gets read, evidence and the decision",
    ),
    (
        # USER 2026-09-05 22:05: the transition itself must deliver the
        # message. Naming the reviewer without mailing it is the exact failure
        # that requirement exists for.
        "the-transition-names-but-does-not-tell",
        "orgtree/ledger.py",
        "        if want == actor:\n"
        "            return None                 # naming yourself mails nobody",
        "        if True:\n"
        "            return None                 # naming yourself mails nobody",
        "delivers one message to the reviewer",
    ),
    (
        # the legacy route (Astra 2026-09-05 22:27): items already at `review`
        # when the field shipped are given a reviewer by an ordinary owner
        # update. A rule that demanded a status change would leave them
        # permanently unassignable.
        "a-reviewer-can-only-be-named-while-entering-review",
        "orgtree/ledger.py",
        '        if not entering and prev_status != "review":',
        "        if not entering:",
        "legacy review item is given its reviewer",
    ),
    # ⚠ THERE IS NO MUTANT FOR `_work_name_reviewer`'s owner-level check
    # (`if not pre_manage and owner_after != actor`). It was written and it
    # SURVIVED: the branch is unreachable, because an actor that may update
    # either manages the item already or claims it in the same call, and a
    # reviewer-only actor is refused before it. Kept out rather than left in
    # as a permanent failure — see the comment at that branch in ledger.py.
    (
        # …and the other end of the same requirement: ORDINARY updates on an
        # item already at review must not read as fresh review requests.
        "every-update-at-review-re-requests-it",
        "orgtree/ledger.py",
        '        want = str(reviewer or "").strip()\n'
        '        entering = (status == "review" and prev_status != "review")',
        '        want = str(reviewer or "").strip() or (\n'
        '            self._work_actor_node(it.get("reviewer"))\n'
        '            if status == "review" else "")\n'
        '        entering = (status == "review" and prev_status != "review")',
        "delivers one message to the reviewer",
    ),
]


def run_suite(root: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["ORGTREE_DATA"] = os.path.join(root, "throwaway-data")
    os.makedirs(env["ORGTREE_DATA"], exist_ok=True)
    env.pop("ORGTREE_WARM", None)
    r = subprocess.run(
        [sys.executable, os.path.join(root, "tests", "test_work_items.py")],
        capture_output=True, text=True, env=env, cwd=root, timeout=900,
        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def failed_labels(out: str) -> list[str]:
    return re.findall(r"^  x (.+)$", out, re.M)


def fresh_copy(prefix: str) -> tuple[str, str]:
    tmp = tempfile.mkdtemp(prefix=prefix)
    root = os.path.join(tmp, "backend")
    shutil.copytree(BACKEND, root,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                  "throwaway-data"))
    return tmp, root


def anchored(text: str, src: str) -> str:
    """The anchors are written with \\n; the tree is CRLF. Match whichever the
    file actually uses so a stale anchor is reported as stale rather than as a
    line-ending accident."""
    crlf = text.replace("\n", "\r\n")
    if src.count(crlf) and not src.count(text):
        return crlf
    return text


def main() -> int:
    print("baseline (unmutated copy) ...")
    base, root = fresh_copy("mut-staffing-base-")
    try:
        code, out = run_suite(root)
        m = re.search(r"(\d+) passed, (\d+) failed", out)
        if not m or m.group(2) != "0":
            print(out[-4000:])
            print("BASELINE IS NOT GREEN — every mutant result below would be "
                  "meaningless. Stopping.")
            return 2
        print(f"  baseline: {m.group(1)} passed, 0 failed")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    bad: list[str] = []
    for name, rel, old, new, must_fail in MUTANTS:
        tmp, root = fresh_copy(f"mut-{name}-")
        try:
            path = os.path.join(root, rel.replace("/", os.sep))
            src = open(path, encoding="utf-8", newline="").read()
            a = anchored(old, src)
            if src.count(a) != 1:
                print(f"  ! {name}: the target snippet appears "
                      f"{src.count(a)} times — the harness is stale, not the "
                      f"code. FIX THIS HARNESS.")
                bad.append(name)
                continue
            b = new.replace("\n", "\r\n") if a != old else new
            open(path, "w", encoding="utf-8", newline="").write(src.replace(a, b))
            _code, out = run_suite(root)
            labels = failed_labels(out)
            hit = [x for x in labels if must_fail in x]
            if not labels:
                print(f"  SURVIVED {name}: the suite still passed. The check "
                      f"for {must_fail!r} does not test this.")
                bad.append(name)
            elif not hit:
                print(f"  MISDIRECTED {name}: red, but not on {must_fail!r} — "
                      f"got {labels}")
                bad.append(name)
            else:
                print(f"  killed   {name}  ->  x {hit[0][:70]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if bad:
        print(f"\n{len(bad)} mutant(s) not properly killed: {bad}")
        return 1
    print(f"\nall {len(MUTANTS)} mutants killed by the named checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
