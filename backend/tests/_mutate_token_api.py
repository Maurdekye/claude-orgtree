"""Mutation harness for test_token_api.py.

⚠ The mutants that matter here are the LEAKS — precisely the ones
`/api/accounts`'s own "leaks no token text" check cannot see, because it
guards the registry object and passes however these endpoints behave. If those
mutants survive, the new leak checks are decoration.

Same three non-optional properties as the other harnesses: every mutant names
the check it must kill, a missing pattern is reported vacuous rather than
passing, and a no-op control must survive.

    python backend/tests/_mutate_token_api.py
"""

import os as _os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "backend" / "orgtree" / "api.py"
TOK = ROOT / "backend" / "orgtree" / "tokens.py"
SUITE = ROOT / "backend" / "tests" / "test_token_api.py"

_STORE_FIRST = ("        tokens.put(uuid, body.token)"
                "          # ← durable before anything else")

MUTANTS = [
    ("NO-OP CONTROL: reword a comment in the token endpoint",
     API,
     '    return {"tokens": tokens.redacted(), "stored": uuid}',
     '    return {"tokens": tokens.redacted(), "stored": uuid}  # noqa',
     None),

    # ── the leaks the registry's own check cannot see ──────────────────────
    ("LEAK: the PUT response echoes the token back",
     API,
     '    return {"tokens": tokens.redacted(), "stored": uuid}',
     '    return {"tokens": tokens.redacted(), "stored": uuid,\n'
     '            "token": body.token}',
     "PUT response never echoes the token"),

    ("LEAK: the tokens listing returns raw values",
     API,
     '    return {"tokens": tokens.redacted()}',
     '    return {"tokens": dict(tokens.load()["tokens"])}',
     "GET tokens leaks neither value nor length"),

    ("LEAK: the listing reports the token LENGTH",
     TOK,
     '    return {uuid: "stored" for uuid in sorted(load()["tokens"])}',
     '    return {u: f"stored ({len(v)} chars)" '
     'for u, v in sorted(load()["tokens"].items())}',
     "GET tokens leaks neither value nor length"),

    # ── the user's store-first ruling ──────────────────────────────────────
    ("VALIDATE-BEFORE-STORE: a paste is judged before it is durable",
     API,
     _STORE_FIRST,
     '        if not body.token.startswith("sk-ant-oat"):\n'
     '            raise HTTPException(422, "that does not look like a token")\n'
     '        tokens.put(uuid, body.token)',
     "PUT a token → 200 and it is reported as present"),

    ("a refused paste destroys the token already stored",
     API,
     _STORE_FIRST,
     '        tokens.forget(uuid)\n        tokens.put(uuid, body.token)',
     "an empty token → 422, and the stored one survives"),

    ("an unknown account is accepted instead of 404",
     API,
     '    if uuid not in (accounts.load().get("accounts") or {}):',
     '    if False:',
     "PUT for an unknown account → 404"),

    # ── the access boundary ────────────────────────────────────────────────
    ("the kiosk freeze stops covering the /api/accounts prefix",
     API,
     '        or rest.startswith("/api/accounts")',
     '        or rest.startswith("/api/accounts/adopt")',
     "kiosk visitors are denied every token route, by the FREEZE"),

    ("DELETE stops forgetting",
     API,
     '    return {"tokens": tokens.redacted(), "forgotten": tokens.forget(uuid)}',
     '    return {"tokens": tokens.redacted(), "forgotten": False}',
     "DELETE forgets it"),
]


def restore():
    subprocess.run(["git", "-C", str(ROOT), "checkout", "--",
                    "backend/orgtree", "backend/tests"], check=False)


def run_suite():
    env = {**_os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(SUITE)], env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT / "backend"))
    passed = set(re.findall(r"ok\s+\d+\s+(.*)", p.stdout))
    return p.returncode == 0, {s.strip() for s in passed}


def main():
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
            if must_kill is None and len(passed) != len(baseline):
                misses.append(f"{name}: no-op control changed the pass COUNT "
                              f"({len(baseline)} -> {len(passed)})")
            killed = baseline - passed
            if must_kill is None:
                if ok:
                    print(f"  ✓ SURVIVED  {name}")
                else:
                    misses.append(f"{name}: no-op control DIED "
                                  f"(killed {sorted(killed)})")
                    print(f"  ✗ {name}\n     no-op control died: "
                          f"{sorted(killed)}")
            else:
                if must_kill in killed:
                    extra = killed - {must_kill}
                    note = f" (+{len(extra)} more)" if extra else ""
                    print(f"  ✓ KILLED by “{must_kill}”{note}\n     {name}")
                elif not killed:
                    misses.append(f"{name}: SURVIVED — nothing guards it")
                    print(f"  ✗ {name}\n     SURVIVED — no check covers this")
                else:
                    misses.append(f"{name}: killed the WRONG check "
                                  f"{sorted(killed)}")
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
