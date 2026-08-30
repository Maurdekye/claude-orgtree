"""The register's index: numbers unique, citations resolvable — the acceptance suite.

`DECISIONS.md` is a normative register whose entries are addressed BY NUMBER,
and roughly twenty places in `ledger.py`, `supervisor.py`, `api.py` and the
test tree cite those numbers. Nothing checked that the numbers were unique.
They were not: `D-100`, `D-101`, `D-102` and `D-103` were each carried by TWO
different rulings at once (found 2026-08-26), so every citation of them
pointed at two entries and the reader had no way to tell which. Repaired by
giving the displaced entry a fresh number — the one the CODE cites keeps the
original, to minimise how many load-bearing citations move.

The register went four duplicates deep with nobody noticing, which is the
whole argument for this file: **an absent check and a check that cannot fail
are the same thing** (D-158). So neither section here is content to observe
that today's tree is clean. Each one seeds the real fault and watches the
checker report it.

    §1  entry numbers are unique — proved by seeding a real duplicate
    §2  every citation resolves to an entry — proved by seeding a dangling one
    §3  the 2026-08-26 repair, pinned
    §4  the citation form this suite recognises, and why it is not wider
    §5  naming a file on another drive must not crash the report

⚠ WHAT §2 CANNOT FAIL ON, stated because a green run must not be read as more
than it is. §2 proves every citation resolves to an entry that EXISTS. It says
nothing about whether that entry is the one the citation MEANT. A citation
pointing at a real-but-wrong ruling is a semantic error, it resolves perfectly,
and no cheap check will find it. This is not hypothetical: repairing the
duplicate numbers left four citations — `ledger.py`'s org-defaults comment,
`modals.tsx`'s mode field, two in `types.ts` — reading `D-100` while plainly
describing `D-101`'s ruling, and the duplication had been hiding it. They were
resolved by a human reading them (coordinator, 2026-08-26), which is the only
instrument that works on this class. Do not add a check that guesses.

⚠ §4 IS LOAD-BEARING, not decoration. `D-NNN` (three digits, zero-padded) is
`DECISIONS.md`'s namespace. `D-NN` (one or two digits) is
`docs/history/interim-docket.md`'s OWN, older series — its `## D-39` is
"`--expose-admin` — a command-line-only way off loopback", while this
register's `### D-039` is "unrecoverable holds its seat", a different ruling
entirely, and around sixty such short references are live in the tree.
Widening the pattern to `\\d{1,3}` would therefore not make this check
stricter; it would make it fail on day one against perfectly good history.

    python backend/tests/test_decisions_index.py [-v]
"""

from __future__ import annotations

import glob
import os
import re
import sys
import tempfile
import traceback
from collections import Counter

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(_HERE))
REGISTER = os.path.join(REPO, "DECISIONS.md")
VERBOSE = "-v" in sys.argv

#: an entry heading in the register
_HEADING = re.compile(r"^### (D-\d{3}) ·", re.M)
#: a citation of one. Three digits deliberately — see §4 and the docstring.
_CITATION = re.compile(r"\bD-(\d{3})\b")

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


def _true(cond, msg) -> None:
    if not cond:
        raise AssertionError(msg)


# ------------------------------------------------------------- the checker
# Two plain functions, called both against the real tree and against seeded
# faults. One implementation, so a seeded fault exercises the SAME code the
# clean run does — two copies would let the real one rot while the proof
# stayed green.

def duplicate_numbers(register_text):
    """Numbers carried by more than one entry, with their count."""
    counts = Counter(_HEADING.findall(register_text))
    return {n: c for n, c in counts.items() if c > 1}


def cited_files():
    pats = ("backend/**/*.py", "frontend/src/**/*.ts", "frontend/src/**/*.tsx",
            "tools/*.py", "docs/*.md")
    out = [REGISTER]
    for p in pats:
        out += [f for f in glob.glob(os.path.join(REPO, p), recursive=True)
                if "node_modules" not in f]
    return out


def label_for(p):
    """A readable name for `p` that does NOT assume it shares a drive with the
    repo.

    ⚠ `os.path.relpath` RAISES on Windows across drives, and §2's seeded file
    lives in TEMP (C:) while a checkout of this repo may sit on E:. Measured
    2026-08-26: this suite passed from a C: worktree and died from the E: one
    with `ValueError: path is on mount 'C:', start on mount 'E:'` — a cosmetic
    call in an error path, aborting the very check that was about to prove the
    instrument works. Pinned by §5.
    """
    try:
        return os.path.relpath(p, REPO)
    except ValueError:
        return p


def dangling_citations(paths, register_text):
    """cited number -> the files citing it, for numbers with no entry."""
    have = set(_HEADING.findall(register_text))
    bad: dict[str, set[str]] = {}
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for m in _CITATION.finditer(fh.read()):
                n = "D-" + m.group(1)
                if n not in have:
                    bad.setdefault(n, set()).add(label_for(p))
    return bad


REG = open(REGISTER, encoding="utf-8", errors="replace").read()


# ═══════════════════════════════════════════════ §1  numbers are unique

def sec_unique() -> None:
    print("\n§1  entry numbers are unique")
    dups = duplicate_numbers(REG)
    if VERBOSE:
        print(f"    {len(_HEADING.findall(REG))} entries")
    check("no number is carried by two entries",
          lambda: _true(not dups,
                        f"duplicated: {dups} — every citation of those points "
                        f"at two different rulings"))

    # ⚠ PROVE IT CAN FAIL. A counter that counts nothing reports no duplicates
    # and passes forever. Seed a REAL second heading for a number that really
    # exists, and require the checker to name exactly it.
    victim = _HEADING.findall(REG)[0]
    seeded = REG + f"\n### {victim} · a seeded duplicate, not a real ruling\n"

    def _fires():
        found = duplicate_numbers(seeded)
        _true(found, "the checker found NO duplicate in a register that "
                     "provably contains one — it cannot fail, so §1 above "
                     "proves nothing")
        _true(list(found) == [victim],
              f"expected exactly {victim!r}, got {found!r}")
        _true(found[victim] == 2, f"expected 2 occurrences, got {found[victim]}")
    check("…and a seeded duplicate IS caught, naming the number", _fires)


# ═══════════════════════════════════════════════ §2  citations resolve

def sec_citations() -> None:
    print("\n§2  every citation resolves to an entry")
    paths = cited_files()
    check("the scan actually reads the tree",
          lambda: _true(len(paths) > 40,
                        f"only {len(paths)} files scanned — the globs have "
                        f"stopped matching and this section is hollow"))

    cites = set()
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            cites |= {"D-" + m.group(1) for m in _CITATION.finditer(fh.read())}
    check("…and finds citations in it",
          lambda: _true(len(cites) > 100,
                        f"only {len(cites)} distinct numbers cited"))

    bad = dangling_citations(paths, REG)
    check("no citation points at a number with no entry",
          lambda: _true(not bad,
                        f"dangling: { {k: sorted(v) for k, v in bad.items()} }"))

    # the seeded fault. The fake number is BUILT rather than written, so this
    # file does not itself contain a dangling citation for the scan above to
    # find — a test that fails itself is not a subtle bug, but it is a silly
    # one, and the next person would "fix" it by narrowing the scan.
    fake = "D-" + "997"
    d = tempfile.mkdtemp(prefix="orgtree-decidx-")
    stray = os.path.join(d, "stray.py")
    with open(stray, "w", encoding="utf-8") as fh:
        fh.write(f"# a comment citing {fake} which does not exist\n")

    def _fires():
        found = dangling_citations([stray], REG)
        _true(found, "the checker found NO dangling citation in a file that "
                     "provably contains one")
        _true(list(found) == [fake], f"expected {fake!r}, got {found!r}")
    check("…and a seeded dangling citation IS caught", _fires)


# ═══════════════════════════════════════════════ §3  the repair, pinned

def sec_repair() -> None:
    print("\n§3  the 2026-08-26 repair")
    heads = _HEADING.findall(REG)

    def _once():
        for n in ("D-100", "D-101", "D-102", "D-103"):
            c = heads.count(n)
            _true(c == 1, f"{n} appears {c} times, expected exactly 1")
    check("D-100 through D-103 each name exactly one ruling", _once)

    def _displaced():
        for n in ("D-161", "D-162", "D-163", "D-164"):
            _true(n in heads, f"{n} is missing — a displaced ruling was lost")
    check("the four displaced rulings exist under their new numbers",
          _displaced)

    def _traceable():
        # an old citation must still be followable to where its ruling went
        notes = re.findall(r"^Renumbered 2026-08-26 \(was (D-\d{3})\)", REG,
                           re.M)
        _true(sorted(notes) == ["D-100", "D-101", "D-102", "D-103"],
              f"provenance notes name {sorted(notes)}; without them a stale "
              f"citation cannot be followed to the moved ruling")
    check("each displaced ruling records the number it used to carry",
          _traceable)


# ═══════════════════════════════════════════════ §4  the recognised form

def sec_namespace() -> None:
    print("\n§4  the citation form, and why it is not wider")

    def _short_form_is_someone_elses():
        docket = os.path.join(REPO, "docs", "history", "interim-docket.md")
        _true(os.path.exists(docket), "historical interim-docket.md is gone — re-derive "
                                      "the namespace split before trusting §2")
        text = open(docket, encoding="utf-8", errors="replace").read()
        own = re.findall(r"^## (D-\d{1,2}) ·", text, re.M)
        _true(len(own) > 10,
              f"historical interim-docket.md carries {len(own)} short-form headings; the "
              f"claim that D-NN is its namespace no longer holds, so §2's "
              f"three-digit restriction needs re-deriving")
    check("the short D-NN form is interim-docket's own series, not this one",
          _short_form_is_someone_elses)

    def _register_is_padded():
        _true(not re.search(r"^### D-\d{1,2} ", REG, re.M),
              "the register has grown an unpadded heading — §2 would silently "
              "stop covering it")
    check("every register heading uses the padded three-digit form",
          _register_is_padded)


# ═══════════════════════════════════════════ §5  the report survives any drive

def sec_cross_drive() -> None:
    print("\n§5  naming a file on another drive must not crash the report")
    drive = os.path.splitdrive(os.path.abspath(REPO))[0]
    alien = ("Z:\\t\\stray.py" if drive.upper() != "Z:" else "Y:\\t\\stray.py") \
        if os.name == "nt" else "/somewhere/else/stray.py"

    if os.name == "nt":
        # ⚠ PROVE THE SCENARIO IS REAL ON THIS MACHINE before testing the
        # guard against it. If relpath stopped raising here, the check below
        # would pass while guarding nothing.
        def _really_raises():
            try:
                os.path.relpath(alien, REPO)
            except ValueError:
                return
            raise AssertionError(
                f"os.path.relpath({alien!r}, {REPO!r}) no longer raises, so "
                f"this section proves nothing — re-derive it")
        check("os.path.relpath really does raise across drives here",
              _really_raises)
    else:
        print("      (POSIX: no drives, so only the non-raising branch below "
              "is meaningful)")

    check("label_for returns instead of raising",
          lambda: _true(label_for(alien) == alien if os.name == "nt"
                        else isinstance(label_for(alien), str),
                        f"label_for({alien!r}) did not survive"))

    def _same_drive_unchanged():
        inside = os.path.join(REPO, "DECISIONS.md")
        _true(label_for(inside) == "DECISIONS.md",
              f"label_for stopped shortening in-repo paths: "
              f"{label_for(inside)!r}")
    check("…and still shortens paths that ARE inside the repo",
          _same_drive_unchanged)


# ═════════════════════════════════════════════════════════════════════════ main

def main() -> None:
    print("═══ the decisions index: unique numbers, resolvable citations ═══")
    sec_unique()
    sec_citations()
    sec_repair()
    sec_namespace()
    sec_cross_drive()

    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
