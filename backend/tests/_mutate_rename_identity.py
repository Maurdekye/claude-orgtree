"""Mutation harness for the rename identity work — §6 and §7 of test_rename.py.

Team charter §2: a check that reports "nothing wrong" has to prove it can
report something. That bites hardest here, because the defect being fixed is
SILENT — a rename that forgets a work item raises nothing, returns success,
and only shows up later as an agent that cannot see its own docket. A suite
that goes green against that is exactly what the bug looks like.

So every claim §6 and §7 make is checked by breaking the code and watching a
NAMED check go red. One behaviour is rewritten at a time in a COPY of the
tree; nothing here touches the working tree, and the copy's line endings are
irrelevant because it is never committed.

Two controls make the rest mean anything:
  NOOP    one comment word changed        must SURVIVE (else the suite is
                                          environment-sensitive and every kill
                                          below is noise)
  SANITY  work items lose their identity  must DIE (else the suite is not
                                          running the code under test)

    python backend/tests/_mutate_rename_identity.py
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

# (name, relative file, original snippet, replacement, check label that must
#  fail — "" means the mutant must SURVIVE)
MUTANTS: list[tuple[str, str, str, str, str]] = [
    (
        "NOOP-CONTROL-comment-only",
        "orgtree/ledger.py",
        "        # Work items, CURRENT-identity fields only.",
        "        # Work items, CURRENT-identity fields only (noop control).",
        "",
    ),
    (
        "SANITY-CONTROL-work-items-lose-their-name",
        "orgtree/ledger.py",
        '        return str(it.get("slug") or it.get("id") or "")',
        '        return "banana"',
        "one event records the repair",
    ),

    # ---- §6, the permanent fix: rename must carry current ownership
    (
        "rename-forgets-work-items-entirely",       # the original defect
        "orgtree/ledger.py",
        "        self._rekey_work_identity(renamed)\n",
        "",
        "owner, last_updater and participants follow the rename",
    ),
    (
        "rename-moves-authorship-too",
        "orgtree/ledger.py",
        '    WORK_IDENTITY_FIELDS: tuple[str, ...] = ("owner", "last_updater")',
        '    WORK_IDENTITY_FIELDS: tuple[str, ...] = ("owner", "last_updater",\n'
        '                                             "created_by")',
        "created_by and history keep the old name",
    ),
    (
        "rename-skips-participants",
        "orgtree/ledger.py",
        '                ps = it.get("participants")\n'
        "                if isinstance(ps, list) and any(\n"
        "                        isinstance(p, str) and p in renamed for p in ps):",
        '                ps = it.get("participants")\n'
        "                if False:",
        "owner, last_updater and participants follow the rename",
    ),
    (
        "rekey-looks-like-a-docket-update",
        "orgtree/ledger.py",
        "                for f in self.WORK_IDENTITY_FIELDS:\n"
        "                    a = it.get(f)\n"
        "                    if isinstance(a, dict) and a.get(\"node\") in renamed:\n"
        "                        a[\"node\"] = renamed[str(a[\"node\"])]\n"
        "                        moved.append((wid, f))",
        "                for f in self.WORK_IDENTITY_FIELDS:\n"
        "                    a = it.get(f)\n"
        "                    if isinstance(a, dict) and a.get(\"node\") in renamed:\n"
        "                        a[\"node\"] = renamed[str(a[\"node\"])]\n"
        "                        it[\"rev\"] = int(it.get(\"rev\") or 0) + 1\n"
        "                        moved.append((wid, f))",
        "moves no revision, timestamp or history entry",
    ),
    (
        "archived-items-are-skipped",
        "orgtree/ledger.py",
        '        for key in ("work_items", "work_items_archive"):\n'
        "            for it in self.d.get(key) or []:\n"
        "                if only is not None and not any(x is it for x in only):",
        '        for key in ("work_items",):\n'
        "            for it in self.d.get(key) or []:\n"
        "                if only is not None and not any(x is it for x in only):",
        "archived items re-key too",
    ),

    # ---- §7, the bounded repair
    (
        "repair-admits-any-agent",
        "orgtree/ledger.py",
        "        if actor != USER and actor != new:",
        "        if False:",
        "no other agent may",
    ),
    (
        "repair-skips-the-old-value-check-on-work-items",
        "orgtree/ledger.py",
        "            held = self._work_identity_holders(it, old)\n"
        "            if not held:",
        "            held = self._work_identity_holders(it, old)\n"
        "            if False:",
        "a record that does not still hold the old id is refused",
    ),
    (
        "repair-skips-the-old-value-check-on-documents",
        "orgtree/ledger.py",
        '            if not (node == old or node.startswith(old + "@")):',
        "            if False:",
        "a record that does not still hold the old id is refused",
    ),
    (
        "repair-takes-any-rename-event",
        "orgtree/ledger.py",
        '                if e.get("op") == "rename" and e.get("at") == at]',
        '                if e.get("op") == "rename"]',
        "a stamp that names no logged rename is refused",
    ),
    (
        "repair-runs-on-an-empty-allowlist",
        "orgtree/ledger.py",
        "        if not want_docs and not want_work:",
        "        if False:",
        "an empty allowlist is refused",
    ),
    (
        "repair-moves-records-off-a-live-node",
        "orgtree/ledger.py",
        '        if old in self.nodes or any(k.startswith(old + "@") for k in self.nodes):',
        "        if False:",
        "a re-used old name blocks the repair",
    ),
    (
        "repair-accepts-a-retired-destination",
        "orgtree/ledger.py",
        '        if n is None or n.get("state") != "live":',
        "        if n is None:",
        "a retired destination is refused",
    ),
    (
        "repair-logs-nothing",
        "orgtree/ledger.py",
        '        self._log("rename_repair", actor,',
        '        _unused_log = (lambda *a, **k: None)("rename_repair", actor,',
        "one event records the repair",
    ),
]


def run_suite(root: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["ORGTREE_DATA"] = os.path.join(root, "throwaway-data")
    os.makedirs(env["ORGTREE_DATA"], exist_ok=True)
    env.pop("ORGTREE_WARM", None)
    r = subprocess.run(
        [sys.executable, os.path.join(root, "tests", "test_rename.py")],
        capture_output=True, text=True, env=env, cwd=root, timeout=900,
        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def failed_labels(out: str) -> list[str]:
    """test_rename.py prints `  FAIL     <label>` per red check."""
    return [x.strip() for x in re.findall(r"^  FAIL\s+(.+)$", out, re.M)]


def main() -> int:
    print("baseline (unmutated copy) ...")
    base = tempfile.mkdtemp(prefix="mut-rnid-base-")
    try:
        root = os.path.join(base, "backend")
        shutil.copytree(BACKEND, root,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        code, out = run_suite(root)
        if code != 0 or "FAILED" in out:
            print(out[-4000:])
            print("BASELINE IS NOT GREEN — every mutant result below would be "
                  "meaningless. Stopping.")
            return 2
        print("  " + (re.findall(r"^rename: .*$", out, re.M) or ["green"])[0])
    finally:
        shutil.rmtree(base, ignore_errors=True)

    bad: list[str] = []
    for name, rel, old, new, must_fail in MUTANTS:
        tmp = tempfile.mkdtemp(prefix="mut-rnid-")
        try:
            root = os.path.join(tmp, "backend")
            shutil.copytree(BACKEND, root,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            path = os.path.join(root, rel.replace("/", os.sep))
            with open(path, encoding="utf-8") as f:
                src = f.read()
            if src.count(old) != 1:
                print(f"  ! {name}: the target snippet appears "
                      f"{src.count(old)} times — the harness is stale, not the "
                      f"code. FIX THIS HARNESS.")
                bad.append(name)
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.write(src.replace(old, new))
            _code, out = run_suite(root)
            labels = failed_labels(out)
            if not must_fail:                       # the NOOP control
                if labels:
                    print(f"  ! {name}: a comment-only change went RED "
                          f"({labels}) — the suite is not deterministic and "
                          f"every kill below is noise.")
                    bad.append(name)
                else:
                    print(f"  survived {name}  (as required)")
                continue
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
                print(f"  killed   {name}  ->  FAIL {hit[0][:66]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if bad:
        print(f"\n{len(bad)} mutant(s) not properly handled: {bad}")
        return 1
    print(f"\nall {len(MUTANTS)} mutants behaved as required "
          f"(1 noop survived, {len(MUTANTS) - 1} killed by their named check)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
