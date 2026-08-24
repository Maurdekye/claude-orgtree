"""Mutation harness for test_accounts.py.

Not a test — a check on the checks. A suite that goes green proves nothing
until you have watched each guard FAIL for its own reason. Every mutant below
names the check it must kill; a mutant that kills nothing, or kills the wrong
check, is reported as a MISS.

The CONTROL PAIR is the point:
  * NO-OP  — a real edit to the same lines that changes no behaviour. It must
             SURVIVE. If it "dies", the suite is keying on text, not conduct.
  * SANITY — an obviously broken mutant that must DIE. If it survives, the
             harness is not running the code it thinks it is.

⚠ This file also guards the failure this repo has actually shipped: a
mutation round that ran its mutants against REVERTED code and reported five
clean kills, the only tell being a drifting baseline. Two defences here — the
run refuses to start against a dirty tree, and every mutant asserts its
pattern was FOUND before drawing any conclusion from the result.

    python backend/tests/_mutate_accounts.py

Restores accounts.py from git after each run (the worktree must be clean).
"""

import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
ACC = ROOT / "backend" / "orgtree" / "accounts.py"
API = ROOT / "backend" / "orgtree" / "api.py"
SUITE = ROOT / "backend" / "tests" / "test_accounts.py"

# (name, file, find, replace, must-kill-this-check-or-None-for-survive)
MUTANTS = [
    ("NO-OP CONTROL: reword a comment in the guard",
     ACC,
     "        for pat in _SECRET_PATTERNS:",
     "        for pat in _SECRET_PATTERNS:  # noqa",
     None),

    ("SANITY CONTROL: the secret guard becomes a no-op",
     ACC,
     "def _reject_secrets(node: Any, path: str = \"\") -> None:\n    if isinstance(node, dict):",
     "def _reject_secrets(node: Any, path: str = \"\") -> None:\n    if True:\n        return\n    if isinstance(node, dict):",
     "refuses a credential-shaped VALUE"),

    ("save() stops calling the guard at all",
     ACC,
     "    _reject_secrets(doc)                       # before anything touches disk",
     "    pass  # _reject_secrets(doc)",
     "refuses a credential-shaped VALUE"),

    ("the guard checks values but no longer checks KEY NAMES",
     ACC,
     "            if str(k).replace(\"-\", \"_\").lower() in _SECRET_KEYS:",
     "            if False:",
     "refuses a credential-shaped KEY even with a harmless value"),

    ("the guard stops descending into lists",
     ACC,
     "    elif isinstance(node, list):\n        for i, v in enumerate(node):\n"
     "            _reject_secrets(v, f\"{path}[{i}]\")",
     "    elif isinstance(node, list):\n        pass",
     "refuses a secret nested inside a LIST"),

    ("the guard only knows the sk-ant prefix (opaque runs slip through)",
     ACC,
     "    re.compile(r\"\\b[A-Za-z0-9+/_-]{40,}={0,2}\\b\"),          # long opaque run",
     "    re.compile(r\"^(?!x)x$\"),                                  # long opaque run",
     "refuses a long opaque run with no sk-ant prefix"),

    ("identity_from_profile passes the whole account through",
     ACC,
     "    return {\n        \"uuid\": uuid,\n        \"org_uuid\": org.get(\"uuid\"),",
     "    return {\n        **acct,\n        \"uuid\": uuid,\n        \"org_uuid\": org.get(\"uuid\"),",
     "identity_from_profile drops token fields at the boundary"),

    ("re-adoption clobbers a hand-set label",
     ACC,
     "        if label is not None:\n            rec[\"label\"] = label\n"
     "        elif \"label\" not in rec:",
     "        if label is not None:\n            rec[\"label\"] = label\n        elif True:",
     "re-adoption preserves hand-set label AND order"),

    ("a re-adopted account is moved back to the end of the order",
     ACC,
     "        if uuid not in doc[\"order\"]:\n            doc[\"order\"].append(uuid)",
     "        if uuid in doc[\"order\"]:\n            doc[\"order\"].remove(uuid)\n"
     "        if True:\n            doc[\"order\"].append(uuid)",
     "re-adopting the PRIMARY does not demote it"),

    ("first_seen is restamped on every adoption",
     ACC,
     "               \"first_seen\": prev.get(\"first_seen\", now),",
     "               \"first_seen\": now,",
     "first_seen is stable across re-adoption, last_seen advances"),

    ("passive adoption stops noticing a write to the credentials store",
     ACC,
     "        if (after.st_mtime_ns, after.st_size) != (before.st_mtime_ns, before.st_size):",
     "        if False:",
     "RAISES if the credentials store changes mid-adoption"),

    ("adoption compares only SIZE, so an in-place rewrite slips through",
     ACC,
     "        if (after.st_mtime_ns, after.st_size) != (before.st_mtime_ns, before.st_size):",
     "        if after.st_size != before.st_size:",
     "RAISES on a same-SIZE in-place rewrite (mtime, not just size)"),

    ("a resolver failure is re-raised instead of yielding None",
     ACC,
     "    except Exception:                          # noqa: BLE001 — offline/expired/rate-limited\n        return None",
     "    except ZeroDivisionError:\n        return None",
     "resolver failure → None (and no spurious LiveStoreWritten)"),

    ("pinning an unknown account silently succeeds",
     ACC,
     "        elif uuid not in doc[\"accounts\"]:\n            raise KeyError(",
     "        elif False:\n            raise KeyError(",
     "pinning an unknown account raises AND preserves the old pin"),

    ("set_order drops accounts omitted by the caller",
     ACC,
     "        new += [u for u in known if u not in new]",
     "        pass",
     "set_order cannot delete an omitted account"),

    ("set_order stops filtering unknown uuids",
     ACC,
     "        new = list(dict.fromkeys(u for u in order if u in doc[\"accounts\"]))",
     "        new = list(dict.fromkeys(order))",
     "set_order ignores unknown uuids"),

    ("registry_path() freezes the data root at import time",
     ACC,
     "def registry_path() -> str:\n    return os.path.join(store.DATA_ROOT, REGISTRY_NAME)",
     "_FROZEN = os.path.join(store.DATA_ROOT, REGISTRY_NAME)\n\n\n"
     "def registry_path() -> str:\n    return _FROZEN",
     "registry_path() tracks a runtime change of store.DATA_ROOT"),

    ("the readout claims selection is live (D-144 violation)",
     ACC,
     "        \"selection_active\": False,",
     "        \"selection_active\": True,",
     "readout declares selection_active FALSE (D-144)"),

    # the regression this round fixed: the character-class condition that made
    # every all-lowercase secret invisible to the generic net
    ("the secret pattern goes back to requiring upper AND digit",
     ACC,
     "    re.compile(r\"\\b[A-Za-z0-9+/_-]{40,}={0,2}\\b\"),          # long opaque run",
     "    re.compile(r\"\\b(?=[A-Za-z0-9+/_-]*[A-Z])(?=[A-Za-z0-9+/_-]*[0-9])[A-Za-z0-9+/_-]{40,}={0,2}\\b\"),",
     "a long all-lowercase run in a label is refused"),

    ("a vanished credentials store stops counting as a change",
     ACC,
     "        if after is None or (after.st_mtime_ns, after.st_size) != (",
     "        if after is not None and (after.st_mtime_ns, after.st_size) != (",
     "the store DISAPPEARING mid-adoption raises LiveStoreWritten"),

    # ---- the HTTP surface ----
    ("the kiosk freeze on /api/accounts is removed",
     API,
     "        or rest.startswith(\"/api/accounts\")",
     "        or False",
     "kiosk visitors are denied every /api/accounts route"),

    ("pinning an unknown account becomes a silent 200 over HTTP",
     API,
     "    except KeyError as e:\n        raise HTTPException(422, str(e).strip(\"'\")) from None",
     "    except KeyError:\n        pass",
     "PUT pin to an unknown account → 422 and the old pin survives"),

    ("an empty label is accepted",
     API,
     "    if not label:\n        raise HTTPException(422, \"a label cannot be empty\")",
     "    if not label:\n        pass",
     "PATCH empty label → 422"),

    ("relabelling an unknown account 500s instead of 404ing",
     API,
     "    if uuid not in doc[\"accounts\"]:\n        raise HTTPException(404, f\"no such account {uuid!r}\")",
     "    if False:\n        raise HTTPException(404, f\"no such account {uuid!r}\")",
     "PATCH unknown account → 404"),

    # ---- review 2026-08-24 findings: each fix gets a mutant ----
    ("§0: adoption stops opening the configured store (isolation blind)",
     ACC,
     "    try:\n        with open(creds, encoding=\"utf-8\") as f:\n            doc = json.load(f)",
     "    try:\n        doc = {\"claudeAiOauth\": {\"accessToken\": \"x\"}}\n        _ = creds",
     "adoption opens the configured store and never the real one"),

    ("strict load stops refusing a corrupt registry (data loss returns)",
     ACC,
     "    except (OSError, json.JSONDecodeError) as e:\n        if strict:",
     "    except (OSError, json.JSONDecodeError) as e:\n        if False:",
     "a corrupt registry is NOT blanked by the next write"),

    ("strict load stops refusing a future VERSION",
     ACC,
     "    if not isinstance(doc, dict) or doc.get(\"version\") != VERSION:\n        if strict:",
     "    if not isinstance(doc, dict) or doc.get(\"version\") != VERSION:\n        if False:",
     "a future VERSION refuses the write instead of blanking it"),

    ("strict load raises for READERS too (the panel goes down)",
     ACC,
     "def load(*, strict: bool = False) -> dict[str, Any]:",
     "def load(*, strict: bool = True) -> dict[str, Any]:",
     "readers still degrade to blank (the panel stays up)"),

    ("set_order stops deduping",
     ACC,
     "        new = list(dict.fromkeys(u for u in order if u in doc[\"accounts\"]))",
     "        new = [u for u in order if u in doc[\"accounts\"]]",
     "set_order dedupes — the order stays a permutation"),

    ("relabel drops the module lock",
     ACC,
     "    with _lock:\n        doc = load(strict=True)\n        if uuid not in doc[\"accounts\"]:\n            raise KeyError(uuid)",
     "    if True:\n        doc = load(strict=True)\n        if uuid not in doc[\"accounts\"]:\n            raise KeyError(uuid)",
     "relabel serialises on the module lock"),

    ("§7: the kiosk sub-paths fall back to the catch-all 404",
     API,
     "        or rest.startswith(\"/api/accounts\")",
     "        or rest == \"/api/accounts\"",
     "kiosk visitors are denied every /api/accounts route"),

    ("a missing credentials file becomes a 500",
     API,
     "        rec = await run_in_threadpool(accounts.adopt_live)",
     "        rec = await run_in_threadpool(accounts.adopt_live)\n"
     "        if rec is None:\n            raise HTTPException(500, 'no creds')",
     "POST adopt with no credentials → 200, adopted=null"),
]


def restore():
    subprocess.run(["git", "-C", str(ROOT), "checkout", "--",
                    "backend/orgtree/accounts.py", "backend/orgtree/api.py"],
                   check=True, capture_output=True)


def run_suite():
    """Return (ok, set-of-check-labels-that-passed)."""
    # ⚠ force UTF-8 on the child's stdout. Without it the captured text comes
    # back mojibaked on this machine ("→" as "â†’"), so a check label containing
    # a non-ASCII character never string-matches `must_kill` and a correct kill
    # is reported as "killed the WRONG check". Measured here, 2026-08-24.
    import os as _os
    env = {**_os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(SUITE)], env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT / "backend"))
    passed = set(re.findall(r"ok\s+\d+\s+(.*)", p.stdout))
    return p.returncode == 0, {s.strip() for s in passed}


def main():
    # ⚠ covers backend/tests TOO (review 2026-08-24). It used to check only
    # backend/orgtree, so a dirty suite ran and was never restored — and with
    # two agents in one worktree, `restore()`'s `git checkout --` would have
    # silently destroyed the other's uncommitted work. The dirty-check is the
    # only thing standing between a mutation round and someone else's edits.
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                            "backend/orgtree", "backend/tests"],
                           capture_output=True, text=True).stdout
    if dirty.strip():
        sys.exit("refusing to run: backend/orgtree or backend/tests is dirty "
                 "— commit first (a mutation round would revert it)\n" + dirty)

    ok, baseline = run_suite()
    if not ok:
        sys.exit("baseline suite is RED — fix that before mutating")
    print(f"baseline: {len(baseline)} checks green\n")

    misses = []
    for name, path, find, repl, must_kill in MUTANTS:
        src = path.read_text(encoding="utf-8")
        if find not in src:
            misses.append(f"{name}: PATTERN NOT FOUND (mutant never applied)")
            print(f"  ?? {name}\n     pattern not found — mutant is vacuous")
            continue
        path.write_text(src.replace(find, repl, 1), encoding="utf-8")
        try:
            ok, passed = run_suite()
            # baseline drift is the tell that mutants ran against reverted code
            if must_kill is None and len(passed) != len(baseline):
                misses.append(f"{name}: no-op control changed the pass COUNT "
                              f"({len(baseline)} -> {len(passed)})")
            killed = baseline - passed
            if must_kill is None:
                if ok:
                    print(f"  ✓ SURVIVED  {name}")
                else:
                    misses.append(f"{name}: no-op control DIED (killed {sorted(killed)})")
                    print(f"  ✗ {name}\n     no-op control died — suite keys on text, "
                          f"not conduct: {sorted(killed)}")
            else:
                if must_kill in killed:
                    extra = killed - {must_kill}
                    note = f" (+{len(extra)} more)" if extra else ""
                    print(f"  ✓ KILLED by “{must_kill}”{note}\n     {name}")
                elif not killed:
                    misses.append(f"{name}: SURVIVED — nothing guards it")
                    print(f"  ✗ {name}\n     SURVIVED — no check covers this")
                else:
                    misses.append(f"{name}: killed the WRONG check {sorted(killed)}")
                    print(f"  ✗ {name}\n     expected “{must_kill}”, "
                          f"actually killed {sorted(killed)}")
        finally:
            restore()

    print()
    if misses:
        print(f"{len(misses)} PROBLEM(S):")
        for m in misses:
            print("  - " + m)
        sys.exit(1)
    print(f"all {len(MUTANTS)} mutants behaved as specified "
          f"(1 no-op survived, {len(MUTANTS) - 1} died to their named checks)")


if __name__ == "__main__":
    main()
