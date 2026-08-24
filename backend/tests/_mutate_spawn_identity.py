"""Mutation harness for test_spawn_identity.py.

Not a test — a check on the checks. Three properties are NOT optional here,
each one earned the hard way earlier tonight:

  1. **Every mutant NAMES the check it must kill**, and a death only counts if
     it is the NAMED death. Two mutants once anchored on a line that also
     appeared in another function and appeared there FIRST — they reported
     kills the whole time, of unrelated checks.
  2. **The pattern must be FOUND**, or the mutant is reported vacuous. Mutants
     silently stop applying when the code they anchor on is restructured.
  3. **A no-op control must SURVIVE**, and the baseline pass-count is compared,
     so mutants run against reverted code show up as drift.

⚠ The most important mutant in this file is the one that relaxes
`clean_env()`'s blanket `CLAUDE_CODE_*` strip. That is the change someone will
genuinely be tempted to make ("the variable keeps disappearing"), and it
silently hands every org on the machine whatever token the backend happens to
have been started with.

    python backend/tests/_mutate_spawn_identity.py

Restores from git after each run (the worktree must be clean).
"""

import os as _os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
SUP = ROOT / "backend" / "orgtree" / "supervisor.py"
TOK = ROOT / "backend" / "orgtree" / "tokens.py"
SUITE = ROOT / "backend" / "tests" / "test_spawn_identity.py"

MUTANTS = [
    ("NO-OP CONTROL: reword a comment beside the injection",
     SUP,
     "    acct = str(org.d.get(\"account_token_uuid\") or \"\")",
     "    acct = str(org.d.get(\"account_token_uuid\") or \"\")  # noqa",
     None),

    ("SANITY CONTROL: the injection is removed entirely",
     SUP,
     "        tok = tokens.get(acct)\n        if tok:\n"
     "            env[\"CLAUDE_CODE_OAUTH_TOKEN\"] = tok",
     "        tok = tokens.get(acct)\n        if False:\n"
     "            env[\"CLAUDE_CODE_OAUTH_TOKEN\"] = tok",
     "an org WITH an account carries exactly that token"),

    # ⚠ THE ONE THAT MATTERS MOST — the "fix" someone will actually try.
    ("AMBIENT CAPTURE: clean_env stops stripping the OAuth token",
     SUP,
     "        if k.startswith(\"CLAUDE_CODE_\") or k == \"CLAUDECODE\":",
     "        if (k.startswith(\"CLAUDE_CODE_\") and k != \"CLAUDE_CODE_OAUTH_TOKEN\") "
     "or k == \"CLAUDECODE\":",
     "AMBIENT CONTROL: a host-level token reaches NO agent"),

    ("the injection ignores the store and trusts the uuid alone",
     SUP,
     "        tok = tokens.get(acct)\n        if tok:",
     "        tok = tokens.get(acct) or acct\n        if tok:",
     "selecting an account with NO stored token carries nothing"),

    # ── attribution ────────────────────────────────────────────────────────
    ("ATTRIBUTION reports the org's INTENT instead of the resolved env",
     SUP,
     "    if env.get(\"CLAUDE_CODE_OAUTH_TOKEN\"):",
     "    if org.d.get(\"account_token_uuid\"):",
     "attribution follows the ENV when intent and reality disagree"),

    ("a token-carrying turn with no uuid is filed as 'ambient'",
     SUP,
     "        return str(org.d.get(\"account_token_uuid\") or \"\") or \"token:unattributed\"",
     "        return str(org.d.get(\"account_token_uuid\") or \"\") or \"ambient\"",
     "a token with no uuid is named, not filed as ambient"),

    ("attribution stops distinguishing the api-key lane",
     SUP,
     "    if env.get(\"ANTHROPIC_API_KEY\"):\n        return \"api-key\"",
     "    if False:\n        return \"api-key\"",
     "an api-key org reports 'api-key'"),

    # ── the store ──────────────────────────────────────────────────────────
    ("tokens_path is CACHED, so it stops following the data root",
     TOK,
     "    return os.path.join(store.DATA_ROOT, \"account_tokens.json\")",
     "    global _CACHED_P\n"
     "    try:\n        return _CACHED_P\n    except NameError:\n        pass\n"
     "    _CACHED_P = os.path.join(store.DATA_ROOT, \"account_tokens.json\")\n"
     "    return _CACHED_P",
     "CONTROL: tokens_path FOLLOWS a moved data root"),

    ("redacted() leaks the token value",
     TOK,
     "    return {uuid: \"stored\" for uuid in sorted(load()[\"tokens\"])}",
     "    return dict(load()[\"tokens\"])",
     "redacted() reports presence only — no value, no length"),

    ("redacted() leaks the token LENGTH",
     TOK,
     "    return {uuid: \"stored\" for uuid in sorted(load()[\"tokens\"])}",
     "    return {u: f\"stored ({len(v)} chars)\" "
     "for u, v in sorted(load()[\"tokens\"].items())}",
     "redacted() reports presence only — no value, no length"),

    ("an empty token is accepted into the store",
     TOK,
     "    if not str(token or \"\").strip():\n"
     "        raise ValueError(\"refusing to store an empty token\")",
     "    if False:\n        raise ValueError(\"refusing to store an empty token\")",
     "an empty token is refused"),

    ("the token store is written INTO the identity registry",
     TOK,
     "    return os.path.join(store.DATA_ROOT, \"account_tokens.json\")",
     "    return os.path.join(store.DATA_ROOT, \"accounts.json\")",
     "tokens never reach the identity registry file"),

    ("forget() silently does nothing",
     TOK,
     "        del doc[\"tokens\"][str(uuid)]",
     "        pass",
     "forget removes it"),

    # ── the failover rules · these are the USER'S decisions ────────────────
    # Each of these is a plausible "simplification" someone could make while
    # believing they were tidying up. Every one must be loud.
    ("USER RULING BROKEN: a 401 fails over instead of stopping",
     SUP,
     "    if _looks_like_auth_failure(res):\n"
     "        return \"stop\", (\"the credential was rejected (401)",
     "    if _looks_like_auth_failure(res) and alternate_account(org):\n"
     "        return \"switch\", (\"the credential was rejected (401)",
     "a 401 STOPS — it never fails over"),

    ("the limit test is moved AHEAD of the 401 test (prose outranks status)",
     SUP,
     "    if _looks_like_auth_failure(res):",
     "    if _looks_like_usage_limit(err_blob) and alternate_account(org):\n"
     "        return \"switch\", \"the account is out of capacity\"\n"
     "    if _looks_like_auth_failure(res):",
     "a 401 outranks limit-sounding prose"),

    ("USER RULING BROKEN: the one-switch-per-turn bound is dropped",
     SUP,
     "    if already_switched:\n"
     "        return \"none\", \"one switch per turn; this turn has had its switch\"",
     "    if False:\n        return \"none\", \"\"",
     "ONE switch per turn — a switched turn does not switch again"),

    ("a hang stops counting as a failure to serve",
     SUP,
     "    if timed_out:",
     "    if False:",
     "a TIMEOUT counts as failure to serve and switches"),

    ("an untokened account is offered as the failover target",
     SUP,
     "        if uuid != cur and tokens.has(uuid):",
     "        if uuid != cur:",
     "an account with no token is never the alternate"),

    ("the alternate can be the account that just failed",
     SUP,
     "        if uuid != cur and tokens.has(uuid):",
     "        if tokens.has(uuid):",
     "the alternate is the OTHER account, not the current one"),

    ("an ordinary crash starts switching accounts",
     SUP,
     "    return \"none\", \"not a condition another account would fix\"",
     "    return (\"switch\" if alternate_account(org) else \"none\"), \"x\"",
     "an ordinary failure switches nothing"),

    ("deciding MUTATES the org (deciding and acting stop being separable)",
     SUP,
     "    if already_switched:",
     "    org.d[\"_decided\"] = True\n    if already_switched:",
     "the decision is pure — it changes nothing"),
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
