"""Mutation harness for test_midturn_mail_ingress.py + test_prompt_view_race.py
(D-229).

Both suites are all-green, and an all-green suite is the symptom, not the
proof — doubly so here, where the defect's own presentation WAS a clean-looking
screen ("delivering mid-task…" over a message nothing would ever move). Each
mutation below is a VALUE REPLACEMENT in the shipped code — never a deleted
call or a raised exception, because a mutant that dies with a NameError only
proves the line executes, not that anything CHECKS it. Every one must kill at
least one named check; a survivor means that behaviour is unverified.

Two controls make the rest mean something:
  NOOP    a comment word changes → must SURVIVE (else the suite is
          environment-sensitive and every kill below is noise)
  SANITY  `_delivery_stages` returns nothing → must DIE (else the suite is
          not running the code under test at all)

⚠ THIS EDITS SHIPPED SOURCE IN PLACE. Everything anyone measures against this
tree while it runs is worthless, so it refuses to start while another run
holds the lock, snapshots the original bytes before the first edit — in memory
AND on disk — restores from that snapshot on every exit path (a Ctrl-C
included), and fails LOUDLY if a `# MUTANT` marker survives the restore
(review round 1 caught the earlier version applying its edit outside the try
and restoring by reverse-replace). A run KILLED outright (a task or session
teardown) cannot restore itself; `--recover` puts the tree back from the
on-disk snapshot, and a fresh run refuses to start on a tree that already
carries a marker, because a snapshot of that tree would re-plant the
mutation on every exit (2026-09-02: the harness's own session was replaced
mid-M2 and left the original defect planted in the worktree). `--recover`
touches only files that still carry a marker or are byte-identical to the
snapshot: a file changed by hand since the kill is refused, not rolled back
(review round 2).

Run:  python backend/tests/_mutate_midturn.py            (~8 min)
      python backend/tests/_mutate_midturn.py --only M1  (one mutant)
      python backend/tests/_mutate_midturn.py --recover  (after a KILLED run)

RESULT, 2026-09-02 (Windows 11, one machine). First version: 10/10 behaved.
The round-1 review added §7 (the belt, pinned) and the coverage rule, so M3
flipped from "survives by design" to "must die" and M9/M10 were added: 12/12
behaved. The round-2 review found two claude-lane sites that flipped
`responding` off without folding (N1) and a cover marker that missed reply and
notice mail (N2); the fold became one helper (`_fold_steer`) and the marker one
function (`mail_marker_in`), and M11–M16 pin the round-2 fixes. See the bottom
of this docstring for the re-run after those changes.

    NOOP    survived                                   (control)
    SANITY  died — stage checks + race §3             (control)
    M1      died — the receipt check only: the belt saved the message and
            named itself; the lane fold is pinned by its receipt
    M2      died — THE ORIGINAL DEFECT: carrier stranded in RAM, node idle
    M3      died — §7 plants a lane that forgot to fold; only the belt
            can save that message
    M4      died — the receipt never says stranded
    M5      died — the roll-up always says zero
    M6      died — read_chat never reloads the sidecar (the TOCTOU)
    M7      died — no grace: a fresh unprojected event renders raw at once
    M8      died — the reload re-spends a consumed row
    M9      died — the belt folds to the FRONT: §7's A/B order inverts
    M10     died — the hold-back ignores whether the bubble still covers
            the message: race §2b shows the message ZERO times
    M11     died — `_carries_envelope` says yes to everything: a covered
            record with no envelope is held (race §2c′)
    M12     died — `_fold_steer` folds to the FRONT (ingress §8, §7)
    M13     died — `mail_marker_in` matches every entry: the pending
            bubble is retired for a message the transcript does not carry
            (race §2d, §3)
    M14     died — the claude phantom-drop site flips `responding` off
            without folding (ingress §8's structural guard)
    M15     died — the claude stdin-closed site, likewise
    M16     died — a queued carrier on an idle node reads `queued`
            forever (ingress §5)

Re-run after round 2, 2026-09-02: 18/18 behaved (≈9 min on a loaded machine).
"""
import hashlib
import os
import pickle
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
SUP = os.path.join(BACKEND, "orgtree", "supervisor.py")
API = os.path.join(BACKEND, "orgtree", "api.py")
INGRESS = os.path.join(HERE, "test_midturn_mail_ingress.py")
RACE = os.path.join(HERE, "test_prompt_view_race.py")
LOCK = os.path.join(tempfile.gettempdir(), "orgtree-mutate-"
                    + hashlib.sha1(BACKEND.encode("utf-8")).hexdigest()[:12]
                    + ".lock")
SNAP = LOCK[:-len(".lock")] + ".snap"   # the untouched bytes, ON DISK

CODEX_FOLD = ('            st.pop("codex_turn", None)\n'
              '            st["responding"] = False\n'
              '            leftover = _fold_steer(st)      # to the BACK — see the helper\n')
CODEX_FOLD_OFF = CODEX_FOLD.replace(
    '            leftover = _fold_steer(st)      # to the BACK — see the helper\n',
    '            leftover = []  # MUTANT\n')

BELT = '            residual = _fold_steer(st)\n'
BELT_OFF = '            residual = []  # MUTANT\n'
BELT_FRONT = ('            residual = st.get("steer") or []  # MUTANT\n'
              '            st["steer"] = []\n'
              '            st["queue"][0:0] = residual\n')

HELPER_BACK = ('    if leftover:\n'
               '        st["queue"].extend(leftover)\n'
               '    return leftover\n')
HELPER_FRONT = ('    if leftover:\n'
                '        st["queue"][0:0] = leftover  # MUTANT\n'
                '    return leftover\n')

# (label, [(file, find, replace)], expect_dies, what it breaks)
MUTANTS = [
    ("NOOP", [(SUP, "# THE BELT (D-229). The steer store only means anything while a\n",
               "# THE BELT (D-229): The steer store only means anything while a  # MUTANT\n")],
     False, "a comment character — the control that must survive"),
    ("SANITY", [(SUP, "    st = state(slug, nid)\n    with _state_lock:\n"
                      "        steer_toks = {str(t) for x in (st.get(\"steer\") or [])",
                 "    return {}  # MUTANT\n    st = state(slug, nid)\n    with _state_lock:\n"
                 "        steer_toks = {str(t) for x in (st.get(\"steer\") or [])")],
     True, "no stage is ever computed — the control that must die"),
    ("M1", [(SUP, CODEX_FOLD, CODEX_FOLD_OFF)],
     True, "the codex leg stops folding (the belt still saves the message, but "
           "the receipt now names the belt, not the lane) — pins the lane fold"),
    ("M2", [(SUP, CODEX_FOLD, CODEX_FOLD_OFF), (SUP, BELT, BELT_OFF)],
     True, "THE ORIGINAL DEFECT: neither the lane nor the belt folds — the "
           "carrier is stranded in RAM with the node idle"),
    ("M3", [(SUP, BELT, BELT_OFF)],
     True, "belt off, lane fold on — §7's forgetful lane has only the belt"),
    ("M4", [(SUP, '            out[tok] = "stranded"\n',
             '            out[tok] = "steer"  # MUTANT\n')],
     True, "`stranded` is never reported — the receipt lies 'delivering' the "
           "way the old tag did"),
    ("M5", [(API, '    out["mail_stranded"] = sum(1 for m in pending\n'
                  '                               if m.get("stage") == "stranded")\n',
             '    out["mail_stranded"] = 0  # MUTANT\n')],
     True, "the roll-up always says zero"),
    ("M6", [(SUP, "                if not views_reloaded:\n"
                  "                    views_reloaded = True\n",
             "                if False:  # MUTANT\n"
             "                    views_reloaded = True\n")],
     True, "read_chat never reloads the sidecar on a miss — the TOCTOU"),
    ("M7", [(SUP, "PROMPT_VIEW_GRACE_S = 8.0\n",
             "PROMPT_VIEW_GRACE_S = 0.0  # MUTANT\n")],
     True, "no grace: a fresh unprojected event renders raw at once"),
    ("M8", [(SUP, "        if consumed is not None:\n            consumed.append(row)\n",
             "        if False:  # MUTANT\n            consumed.append(row)\n")],
     True, "the reload re-spends a consumed row on an identical later prompt"),
    ("M9", [(SUP, BELT, BELT_FRONT)],
     True, "the belt folds to the FRONT — the late steer overtakes the pump's "
           "requeue and the user's order inverts (§7)"),
    ("M10", [(SUP, "    return False  # not covered\n",
              "    return True  # MUTANT\n")],
     True, "the hold-back ignores coverage — an uncovered unprojected event is "
           "hidden and the message is on screen zero times (race §2b)"),
    ("M11", [(SUP, '    return (ORG_STATE_OPEN in raw or "[PROVIDER USAGE" in raw\n'
                   '            or "[ORG NOTICES" in raw)\n',
              "    return True  # MUTANT\n")],
     True, "`_carries_envelope` says yes to everything — a covered record with "
           "no envelope is held back (race §2c′)"),
    ("M12", [(SUP, HELPER_BACK, HELPER_FRONT)],
     True, "`_fold_steer` folds to the FRONT — every site inverts the order "
           "(ingress §8 pins the helper, §7 the belt through it)"),
    ("M13", [(SUP, "    i = raw.find(stamp)\n    if i < 0:\n        return False\n",
              "    i = raw.find(stamp)\n    if i < 0:\n        return True  # MUTANT\n")],
     True, "`mail_marker_in` matches every entry — the bubble is retired for a "
           "message the transcript does not carry (race §2d, §3)"),
    ("M14", [(SUP, "                                        dropped_fold = _fold_steer(st)\n",
              "                                        dropped_fold = []  # MUTANT\n")],
     True, "the claude phantom-drop site flips `responding` off without folding "
           "(round-2 N1; ingress §8's structural guard)"),
    ("M15", [(SUP, "                                    closed_fold = _fold_steer(st)\n",
              "                                    closed_fold = []  # MUTANT\n")],
     True, "the claude stdin-closed site flips `responding` off without folding "
           "(round-2 N1; ingress §8's structural guard)"),
    ("M16", [(SUP, '            out[tok] = "queued" if (owned or young) else "stranded"\n',
              '            out[tok] = "queued"  # MUTANT\n')],
     True, "a queued carrier on an idle node reads `queued` forever and the "
           "roll-up stays 0 (round-2 N7; ingress §5)"),
]

TOUCHED = sorted({path for _, edits, _, _ in MUTANTS for path, _, _ in edits})
# Both safety nets key on the marker — the startup refusal and `--recover`'s
# "still mutated?" test — so EVERY replacement must carry it. Review round 3
# found M7 and NOOP without one: a run killed during M7 would have left the
# grace disabled, unrefused and unrecoverable. Enforced where it is stated.
_UNMARKED = [(label, repl[:60]) for label, edits, _, _ in MUTANTS
             for _, _, repl in edits if "# MUTANT" not in repl]
assert not _UNMARKED, f"replacement without a `# MUTANT` marker: {_UNMARKED}"


class AnchorError(Exception):
    pass


def mutate(edits):
    """Apply every edit of one mutant, or none: all anchors are checked in
    memory before the first byte is written. Bytes in, bytes out — the
    tree's line endings are whatever they are."""
    contents = {}
    for path, find, repl in edits:
        data = contents.get(path)
        if data is None:
            with open(path, "rb") as f:
                data = f.read()
        f_b = find.encode("utf-8")
        if data.count(f_b) != 1:
            raise AnchorError(f"anchor not unique/present in "
                              f"{os.path.basename(path)}: {data.count(f_b)}× "
                              f"{find[:70]!r}")
        contents[path] = data.replace(f_b, repl.encode("utf-8"))
    for path, data in contents.items():
        with open(path, "wb") as f:
            f.write(data)


def run(suite):
    p = subprocess.run([sys.executable, suite], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=600)
    out = p.stdout + p.stderr
    fails = [ln.strip() for ln in out.splitlines()
             if ln.strip().startswith("FAIL") or ": FAIL" in ln
             or ln.startswith("  FAIL")]
    return p.returncode, fails, out


def _marked(paths):
    return [p for p in paths if b"# MUTANT" in open(p, "rb").read()]


def recover():
    """`--recover`: a run that was KILLED outright (a task or session
    teardown — not a Ctrl-C, which the restore in main() survives) never
    reached its restore. Its snapshot is on disk: put the tree back from it,
    release the lock, and say whether a marker is still standing.

    Only a file that still carries a marker, or that is byte-identical to the
    snapshot already, is touched. A file that differs from the snapshot and
    carries NO marker was edited by hand after the kill — rolling it back
    would silently destroy that edit (review round 2), so it is refused and
    the snapshot is kept for a human to compare."""
    if not os.path.exists(SNAP):
        print(f"nothing to recover: no snapshot at {SNAP}"
              + (f" (lock {LOCK} present — delete it by hand once the tree "
                 f"is verified clean)" if os.path.exists(LOCK) else ""))
        return 3
    with open(SNAP, "rb") as f:
        snapshot = pickle.load(f)
    refused = []
    restored = 0
    for path, data in snapshot.items():
        with open(path, "rb") as f:
            cur = f.read()
        if cur == data:
            continue
        if b"# MUTANT" not in cur:
            refused.append(path)
            continue
        with open(path, "wb") as f:
            f.write(data)
        restored += 1
    if refused:
        print(f"REFUSED to roll back {[os.path.basename(p) for p in refused]}: "
              f"changed since the snapshot and carrying no marker — edited by "
              f"hand? Compare against the snapshot ({SNAP}) yourself; the lock "
              f"and snapshot are kept")
        return 3
    leaked = _marked(snapshot)
    for p in (SNAP, LOCK):
        try:
            os.remove(p)
        except OSError:
            pass
    if leaked:
        print(f"!!! MUTANT MARKER STILL PRESENT AFTER RECOVERY IN {leaked} — "
              f"the snapshot itself was taken from a marked tree; restore "
              f"from git")
        return 2
    print(f"recovered {restored} file(s) from the snapshot; lock released")
    return 0


def main():
    if "--recover" in sys.argv:
        return recover()
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    marked = _marked(TOUCHED)
    if marked:
        # a snapshot of a marked tree would re-plant the mutation on every
        # exit — this is the killed-run case, and only --recover (or git)
        # may touch it
        print(f"REFUSED: {[os.path.basename(p) for p in marked]} already "
              f"carry a `# MUTANT` marker (a killed run?) — `--recover` "
              f"restores its snapshot; otherwise restore from git first")
        return 3
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"REFUSED: another mutation run owns this tree ({LOCK}); "
              f"if the pid in it is dead, `--recover` puts its tree back "
              f"and releases the lock")
        return 3
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    snapshot = {}
    for path in TOUCHED:
        with open(path, "rb") as f:
            snapshot[path] = f.read()
    with open(SNAP, "wb") as f:      # before the first edit, and ON DISK
        pickle.dump(snapshot, f)
    results = []

    def restore():
        for path, data in snapshot.items():
            with open(path, "wb") as f:
                f.write(data)

    leaked = []
    try:
        for label, edits, expect_dies, what in MUTANTS:
            if only and label != only:
                continue
            t0 = time.time()
            try:
                mutate(edits)
                rc1, f1, _ = run(INGRESS)
                rc2, f2, _ = run(RACE)
            except AnchorError as e:
                restore()
                print(f"BAD {label:6s} ANCHOR: {e}")
                results.append((label, expect_dies, None, False, [], what, 0))
                continue
            finally:
                restore()
            died = (rc1 != 0) or (rc2 != 0)
            ok = died == expect_dies
            results.append((label, expect_dies, died, ok, f1 + f2, what,
                            time.time() - t0))
            print(f"{'OK ' if ok else 'BAD'} {label:6s} "
                  f"want={'die' if expect_dies else 'survive':7s} "
                  f"got={'died' if died else 'survived':8s} "
                  f"{time.time() - t0:5.1f}s  {what}")
            for f in (f1 + f2)[:6]:
                print(f"        {f[:150]}")
    finally:
        # restore, scan, release — and let any exception that got here
        # (a suite timeout, a Ctrl-C) keep propagating with its own cause,
        # instead of being swallowed by a `return` in this block (round 2)
        restore()
        leaked = _marked(TOUCHED)
        for p in (SNAP, LOCK):
            try:
                os.remove(p)
            except OSError:
                pass
        if leaked:
            print(f"!!! MUTANT MARKER SURVIVED THE RESTORE IN {leaked} — "
                  f"the tree is NOT clean; restore from git before anything "
                  f"else")
    if leaked:
        return 2
    bad = [r for r in results if not r[3]]
    print()
    print(f"{len(results) - len(bad)}/{len(results)} mutations behaved as required")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
