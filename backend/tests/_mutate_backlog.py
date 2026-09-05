"""Mutation harness for the backlog / mandatory-description work (w3becbb30).

A check that reports "nothing wrong" has to prove it can report something. This
rewrites ONE behaviour at a time in a COPY of the tree, runs
`test_work_items.py` against the copy, and asserts the run fails AND that the
named check is the one that went red. A mutant the suite still passes is a
check that means nothing, and this exits non-zero for it.

Nothing here touches the working tree: every mutant is applied inside a fresh
temporary copy of backend/, which is deleted afterwards. Line endings inside
the copy are irrelevant — the copy is never committed.

    python backend/tests/_mutate_backlog.py
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
MUTANTS: list[tuple[str, str, str, str, str]] = [
    (
        "active-count-includes-backlogged",
        "orgtree/ledger.py",
        "        return st not in self.WORK_CLOSED and st != self.WORK_BACKLOG",
        "        return st not in self.WORK_CLOSED",
        # NOT the "separate group" check: a backlog row with no attention is
        # already claimed by the `_work_backlogged` branch above this one, so
        # it never reaches here. The ONLY item that does is a backlogged one
        # holding attention — which is exactly the row this rule exists for,
        # and exactly the check that catches it.
        "never counts as active",
    ),
    (
        "backlog-hides-an-attention-row",
        "orgtree/ledger.py",
        '        return it.get("status") == self.WORK_BACKLOG \\\n'
        "            and not self._work_attention(it)",
        '        return it.get("status") == self.WORK_BACKLOG',
        "stays in the main list",
    ),
    (
        "no-tiebreak-on-docket-at",
        "orgtree/ledger.py",
        '            return (str(v.get("docket_at") or v.get("updated_at") or ""),\n'
        '                    str(v.get("id") or ""))',
        '            return (str(v.get("docket_at") or v.get("updated_at") or ""),\n'
        '                    "")',
        "deterministic order",
    ),
    (
        "description-not-required-on-create",
        "orgtree/ledger.py",
        "        if not obj:\n            raise LedgerError(",
        "        if False:\n            raise LedgerError(",
        "cannot be created without a description",
    ),
    (
        "description-can-be-erased-by-update",
        "orgtree/ledger.py",
        "            if not newobj:\n                raise LedgerError(",
        "            if False:\n                raise LedgerError(",
        "cannot be created without a description",
    ),
    (
        "backlog-group-served-without-the-flag",
        "orgtree/ledger.py",
        "        if include_backlogged:\n            out[\"backlogged\"] = back",
        "        out[\"backlogged\"] = back",
        "hidden by default",
    ),
    (
        "slug-follows-a-later-title-edit",
        "orgtree/ledger.py",
        '            it["title"] = str(title).strip()[:200]',
        '            it["title"] = str(title).strip()[:200]\n'
        '            it["slug"] = self._work_slugify(it["title"])',
        "fixed once assigned",
    ),
    (
        "slug-collisions-are-not-resolved",
        "orgtree/ledger.py",
        "        base = self._work_slugify(title)\n        if base not in taken:\n            return base",
        "        base = self._work_slugify(title)\n        if True:\n            return base",
        "unique",
    ),
    (
        "a-slug-can-shadow-an-opaque-id",
        "orgtree/ledger.py",
        '        ref = str(wid or "")\n'
        '        for it in self._work_active():\n'
        '            if it["id"] == ref:\n'
        "                return it, False",
        '        ref = str(wid or "")\n'
        '        for it in self._work_active():\n'
        '            if it.get("slug") == ref:\n'
        "                return it, False",
        "no unreachable name is minted",
    ),
    (
        # an opaque id must count as a TAKEN NAME. Without this an item can be
        # given a slug that some other item's id already answers to — and that
        # name is permanently unreachable, because ids resolve first
        # (Astra review 2026-09-05).
        "opaque-ids-are-not-reserved-against-slugs",
        "orgtree/ledger.py",
        '            names.add(str(it["id"]))\n',
        "",
        "no unreachable name is minted",
    ),
    (
        # and the reverse direction: a NEWLY MINTED id must not land on a name
        # some existing slug already holds, which would strand that slug
        "a-new-id-may-land-on-an-existing-slug",
        "orgtree/ledger.py",
        '        wid = "w" + uuid.uuid4().hex[:8]\n'
        '        while wid in taken:\n'
        '            wid = "w" + uuid.uuid4().hex[:8]\n'
        '        taken.add(wid)',
        '        wid = "w" + uuid.uuid4().hex[:8]',
        "a newly minted id never lands on a name",
    ),
    (
        "backfill-runs-on-the-read-path",
        "orgtree/ledger.py",
        '            "slug": it.get("slug") or None,',
        '            "slug": (it.get("slug")\n'
        '                     or it.setdefault("slug", self._work_slugify(\n'
        '                         str(it.get("title") or "")))),',
        "never by a read",
    ),
    (
        "doctrine-drops-the-new-rules",
        "orgtree/supervisor.py",
        '    "backlogged|open|in_progress|blocked|review. `backlogged` means the work "',
        '    "open|in_progress|blocked|review. The parked state means the work "',
        "reach agents through the prompt",
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


def main() -> int:
    print("baseline (unmutated copy) ...")
    base = tempfile.mkdtemp(prefix="mut-backlog-base-")
    try:
        root = os.path.join(base, "backend")
        shutil.copytree(BACKEND, root,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
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
        tmp = tempfile.mkdtemp(prefix=f"mut-{name}-")
        try:
            root = os.path.join(tmp, "backend")
            shutil.copytree(BACKEND, root,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            path = os.path.join(root, rel.replace("/", os.sep))
            src = open(path, encoding="utf-8").read()
            if src.count(old) != 1:
                print(f"  ! {name}: the target snippet appears "
                      f"{src.count(old)} times — the harness is stale, not the "
                      f"code. FIX THIS HARNESS.")
                bad.append(name)
                continue
            open(path, "w", encoding="utf-8").write(src.replace(old, new))
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
