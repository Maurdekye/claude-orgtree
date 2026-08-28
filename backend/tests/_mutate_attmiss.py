"""Mutation harness for D-171 — prove §5 actually fires.

⚠ WHY IT IS LINE-BASED. Every source file here is CRLF with ZERO bare LF. A
pattern containing "\\n" written against these files matches NOTHING, the
mutation silently no-ops, the suite goes green on unmutated source, and the
run reads exactly like a passing instrument. That has happened twice in this
codebase. So: split on lines, match a UNIQUE substring within ONE line, and
refuse outright unless the match count is exactly what was declared.

⚠ AND THE MUTATION IS ONLY HALF THE PROOF. The caller must print the real
`git diff` before reading any result — this file cannot prove its own edit
landed, it can only refuse to guess.

Each mutant comments out N consecutive lines starting at the matched line and
inserts a `pass` at the same indentation, which is valid in every position a
statement can occupy.

Usage:  python tests/_mutate_attmiss.py apply <name>
        python tests/_mutate_attmiss.py restore
        python tests/_mutate_attmiss.py names
"""
import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BE = os.path.dirname(HERE)
FILES = ["orgtree/api.py", "orgtree/ledger.py", "orgtree/supervisor.py"]

# name -> (file, unique line substring, how many lines the statement spans,
#          which match to take when several are expected: None = must be 1)
MUTANTS = {
    # M1 — the api layer stops recording an unresolved path. This IS the
    # original defect, restored deliberately.
    "m1-no-record": ("orgtree/api.py",
                     'missing.append(f"{rel} — no such file', 2, None),
    # M2 — the HTTP caller stops being told (half (b) removed).
    # ⚠ A REPLACEMENT, not a deletion. Commenting the line out left `warn`
    # referenced below and the suite died of NameError — which proves only
    # that the line EXECUTES. A mutant must leave working code that lacks the
    # behaviour, or the check it "kills" was never shown to detect anything.
    "m2-no-http-warning": ("orgtree/api.py",
                           'warn = list(r.get("warnings") or [])', 1, None,
                           "    warn = []"),
    # M3 — the agent stops being told (half (a) removed)
    "m3-no-mail-line": ("orgtree/supervisor.py",
                        'for miss in m.get("attachments_missing") or []:',
                        11, None),
    # M4 — the sanitiser stops collapsing whitespace (forgery re-opened)
    # M4 — same reasoning as M2: a REPLACEMENT, so the function still returns
    # a string and the forgery check must fail on its ASSERTION rather than on
    # a NameError.
    "m4-no-sanitise": ("orgtree/ledger.py",
                       's = " ".join(str(raw or "").split())', 1, None,
                       '    s = str(raw or "")'),
    # M5 — the cap goes back to trimming silently
    "m5-silent-trim": ("orgtree/ledger.py",
                       'notes.append(f"{over} further attachment(s)', 2, None),
    # M6 — OUTSIDE mail stops reporting its losses. Two lines in the file read
    # `entry["attachments_missing"] = lost`; this takes the LAST, which is
    # post_external_mail's.
    "m6-no-extern": ("orgtree/ledger.py",
                     'entry["attachments_missing"] = lost', 1, -1),
}


def apply(name):
    spec = MUTANTS[name]
    rel, needle, span, pick = spec[:4]
    repl = spec[4] if len(spec) > 4 else None
    path = os.path.join(BE, rel)
    text = io.open(path, encoding="utf-8", newline="").read()
    lines = text.split("\r\n")
    hits = [i for i, ln in enumerate(lines) if needle in ln]
    if pick is None:
        if len(hits) != 1:
            raise SystemExit(
                f"☠ REFUSING: {name} matched {len(hits)} lines, expected "
                f"exactly 1. The mutation would not have landed where it was "
                f"aimed, and the run would have read as a pass.")
        i = hits[0]
    else:
        if len(hits) < 2:
            raise SystemExit(
                f"☠ REFUSING: {name} expected several matches to choose from, "
                f"found {len(hits)}.")
        i = hits[pick]
    indent = " " * (len(lines[i]) - len(lines[i].lstrip()))
    body = lines[i:i + span]
    if not body[-1].rstrip().endswith((")", "]", ":", "lost", "split())")):
        raise SystemExit(
            f"☠ REFUSING: {name} would cut a statement mid-expression — the "
            f"last line of its span is {body[-1]!r}. Fix the span.")
    head = repl if repl is not None else indent + "pass"
    lines[i:i + span] = [head + "  # MUTANT " + name] + \
                        ["# MUTANT " + b for b in body]
    io.open(path, "w", encoding="utf-8", newline="").write("\r\n".join(lines))
    print(f"applied {name}: {rel} line {i + 1}, {span} line(s) commented")


def restore():
    subprocess.run(["git", "checkout", "--", *FILES], cwd=BE, check=True)
    left = subprocess.run(["git", "grep", "-c", "MUTANT", "--", *FILES],
                          cwd=BE, capture_output=True, text=True).stdout.strip()
    print("restored; MUTANT lines remaining in orgtree/: " + (left or "0"))


if __name__ == "__main__":
    if sys.argv[1] == "apply":
        apply(sys.argv[2])
    elif sys.argv[1] == "names":
        print("\n".join(MUTANTS))
    else:
        restore()
