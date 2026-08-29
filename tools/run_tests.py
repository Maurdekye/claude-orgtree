"""One command that runs every test in the repo, and keeps them from rotting.

    python tools/run_tests.py               # fast tier  (~2 min, hermetic)
    python tools/run_tests.py --full        # everything (~13 min)
    python tools/run_tests.py --list        # show the plan, run nothing

WHY THIS EXISTS
---------------
Seven backend suites and one frontend suite, each a plain runnable script with
its own flags, its own port, its own throwaway `ORGTREE_DATA`, and no pytest.
Nobody remembers seven invocations, so in practice nobody ran them — and two of
those suites carry DRIFT GUARDS (`msgvis.py` greps four source files for nine
mirrored expressions; `derived.test.ts` pins seven constants). A drift guard
that is never run protects nothing. This runner exists so that "run the tests"
is one command, and so that a drift failure is legible as *drift* rather than
as some unrelated red line in the middle of 1,100 checks.

WHERE IT LIVES AND WHY
----------------------
`tools/`, next to `disk_drill.py` / `ui_probe.py` / `social_preview.py` — the
repo's existing home for operator scripts that are not part of the shipped
package. It has to sit above both `backend/` and `frontend/` because it drives
both, so neither of those is a candidate; the repo root is reserved for the
handful of top-level entry points (`update.*`, `expose.ps1`).

HOW SUITES ARE FOUND — nothing is hardcoded
-------------------------------------------
Backend suites are `backend/tests/test_*.py`, by glob. The frontend suite is
`frontend/tests/run.mjs` if it is there. A suite that does not exist yet is
simply absent; a suite added tomorrow is picked up with no edit here. Each
suite's own source is then read to work out how to run it:

  * FLAGS — every `"--word"` literal in the file. A suite that offers
    `--hermetic` is run that way in the fast tier; failing that, `--quick`;
    failing that, plain. The suite's own advertised reduction is respected
    rather than guessed at.
  * EXCLUSIVE — a suite that names `uvicorn`, sets an `ORGTREE_*PORT`, or
    shells out to `"docker"` without offering a `--docker` opt-in starts a real
    listener or a real container. Two of those at once collide on a fixed port
    and, worse, compete for CPU with each other's timing assertions. They are
    run one at a time, after the parallel pool has drained, so nothing is
    racing them. ⚠ Assigning each one a free port instead was considered and
    rejected: most of them fix their port by writing `ORGTREE_PORT` at import
    time, before argv is looked at, so there is nothing to assign — and the
    port was never the whole hazard anyway. The live rigs assert timing, and a
    suite measuring a ~1 s race while three others saturate the CPU is a suite
    that fails for the wrong reason.
  * WINDOWS-BOUND — a suite asserting `WinError` / `MoveFileEx` /
    `FILE_SHARE_DELETE` semantics is testing something POSIX does not do (on
    Linux `os.replace` over an open file simply succeeds). Skipped with that
    reason printed, never silently.
  * DRIFT GUARD — the suite, or a helper module it imports out of the tests
    directory, mentions a source-contract check. Its verdict is then hunted for
    in the output and reported separately from the pass/fail count.

Opt-IN modes are never driven from here: `--docker`, `--real-cli`,
`--discriminate`, `--legacy-client`. Each needs something the runner cannot
promise (a daemon, a paid model, a mutated copy of the package) or deliberately
measures pre-fix behaviour. Run those by hand from the suite's own docstring.

`SLOW` below is the one table of literals, and it is deliberately NOT a list of
suites: it is a list of *measured* wall times that disqualify a suite from the
fast tier even in its cheapest advertised mode. An entry naming a file that no
longer exists is ignored. A suite missing from it is included in the fast tier.

SAFETY
------
`:7360` and the live orgs are the operator's running deployment. The runner
refuses to start a suite whose source names that port, strips every `ORGTREE_*`
variable out of the child environment so no suite can inherit a pointer at the
real data directory, and never assigns a port itself.

DID IT FINISH? — the one question this runner used to be unable to answer
-------------------------------------------------------------------------
A run that is KILLED part-way is otherwise indistinguishable from a run that
passed: the last thing on stdout is a column of `✓` lines, stderr is empty,
and there is no marker of any kind. Measured 2026-08-26 — two tier runs died
at 49 s and 36 s having completed 19 and 25 suites with ZERO failures between
them, and the only record of a cause anyone wrote down was "a background
teardown". A deploy was gated on one of those runs.

So the last two things this runner does, and it does them together, are:

  * print `RUN COMPLETE  suites=N/M  …  rc=X` as the final stdout line, and
  * write a `COMPLETE` file into `logdir`.

Their ABSENCE is the signal. Neither can be produced by a run that died early,
because nothing writes them until every suite has been accounted for. The
summary bar above them is not enough on its own — it is prose, and a reader
skims it — while `RUN COMPLETE` is one grep and `COMPLETE` is one
`os.path.exists`. Gate on those, never on "I did not see a ✗".

⚠ The stdout line is printed BEFORE the marker file is written, and that order
is deliberate. Any interruption between them then leaves a run looking
UNFINISHED, never finished — the safe direction. Reversed, a truncated log
would carry a marker asserting a completeness it cannot back up, and a marker
that lies is worse than no marker: it turns "I cannot tell" into "I was told
wrong".

Retroactively, on a run whose stdout is long gone: the plan header prints
`plan · N to run`, and `run_one` writes a suite's log only once that suite has
FINISHED — so `ls <logdir> | wc -l` against N tells you how far a past run
actually got. That one works on every run already on disk.

`backend/tests/test_run_completion.py` is the acceptance suite, and it earns
its keep by killing a real run mid-flight rather than by reasoning about this
docstring.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: the operator's live deployment. Nothing here may go near it.
FORBIDDEN_PORTS = ("7360",)

#: Measured exceptions, not a suite list. `fast` is the argv for the fast tier;
#: None means "full tier only", and `why` is printed wherever it is skipped.
#: Re-measure and edit rather than deleting — the number is the justification.
SLOW = {
    "test_message_visibility_live.py": {
        "fast": None,
        "why": "live rig: a real uvicorn, a fake CLI and real elapsed time — "
               "--quick is 105 s and its default 3 reps is 216 s (measured), "
               "and its timing assertions want an unloaded machine",
    },
    "test_turn_lifecycle.py": {
        # it advertises --hermetic (0.4 s, 49 checks) which the generic rule
        # already picks; this entry only records why --quick is not the choice.
        "fast": ["--hermetic"],
        "why": "--quick spawns the real backend and takes 119 s (measured); "
               "--hermetic is the in-process half",
    },
}

# ---------------------------------------------------------------- detection

_FLAG = re.compile(r"""["'](--[a-z][a-z0-9-]*)["']""")
_EXCLUSIVE = re.compile(r"""\buvicorn\b|ORGTREE_(?:BRIDGE_|PUBLIC_)?PORT""")
_DOCKERISH = re.compile(r"""["']docker["']""")
_WINDOWS = re.compile(r"winerror|WinError|FILE_SHARE_DELETE|MoveFileEx", re.I)
# deliberately NOT a bare /drift/ — several suites use the word in prose
# ("the return shape had drifted") without carrying a guard, and a false
# positive here turns the rot alarm into background noise
_GUARDISH = re.compile(
    r"drift guard|client-model drift|SOURCE_CONTRACTS|_matches_source"
    r"|source contract", re.I)
_SIBLING_IMPORT = re.compile(r"^\s*(?:import|from)\s+([a-z_][a-z0-9_]*)", re.M)

_PROSE = [re.compile(r'""".*?"""', re.S), re.compile(r"'''.*?'''", re.S),
          re.compile(r"/\*.*?\*/", re.S), re.compile(r"(?m)#.*$"),
          re.compile(r"(?m)//.*$")]


def strip_prose(text):
    """Docstrings and comments out, code in. Guard detection runs on this:
    several suites *talk about* the drift guard in msgvis.py without carrying
    one, and a rot alarm that cries wolf every run is an alarm nobody reads."""
    for pat in _PROSE:
        text = pat.sub(" ", text)
    return text

#: a guard that FIRED — the source moved and a test's model of it did not
_GUARD_FIRED = re.compile(
    r"client-model drift"
    r"|no longer (?:contains|matches)"
    r"|must be updated with the source"
    r"|changed\s*[—–-]\s*re-read"
    r"|(?:FAIL|✗|✖|not ok)[^\n]*(?:drift guard|contracts intact)"
    r"|drift guard[^\n]*(?:FAIL|✗|✖)", re.I)
#: a guard that HELD — proof it actually ran, which is the whole point
_GUARD_HELD = re.compile(
    r"ok\s+\d+\s+[^\n]*(?:drift guard|contracts intact)"
    r"|✔[^\n]*drift guard", re.I)

_CHECKS = [
    re.compile(r"ALL\s+([\d,]+)\s+CHECKS PASS"),
    re.compile(r"([\d,]+)\s+checks passed"),
    re.compile(r"^ℹ\s*pass\s+(\d+)", re.M),
]
#: last-resort count — the `ok N` lines themselves, for a suite that never
#: reached its own total
_OK_LINE = re.compile(r"^\s*ok\s+(\d+)\b", re.M)
_TOTAL_LINE = re.compile(r"ALL\s+[\d,]+\s+CHECKS PASS|checks passed|^ℹ\s*pass\s",
                         re.M)


class Suite:
    def __init__(self, sid, cmd_fast, cmd_full, cwd, *, exclusive=False,
                 windows_only=False, guard=False, guard_hint="",
                 fast_why="", skip=""):
        self.id = sid
        self.cmd_fast = cmd_fast          # argv, or None = full tier only
        self.cmd_full = cmd_full
        self.cwd = cwd
        self.exclusive = exclusive
        self.windows_only = windows_only
        self.guard = guard
        self.guard_hint = guard_hint
        self.fast_why = fast_why
        self.skip = skip                  # non-empty = never run, with reason

    def cmd(self, full):
        return self.cmd_full if full else self.cmd_fast


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().replace("\r\n", "\n")
    except OSError:
        return ""


def _interpreter():
    """The venv's python if there is one, else the one running this file."""
    for rel in (os.path.join(".venv", "Scripts", "python.exe"),
                os.path.join(".venv", "bin", "python")):
        p = os.path.join(REPO, rel)
        if os.path.exists(p):
            return p
    return sys.executable


def discover(py):
    """Every suite in the tree, with a plan for each, derived from its source."""
    suites = []
    tests_dir = os.path.join(REPO, "backend", "tests")

    for path in sorted(glob.glob(os.path.join(tests_dir, "test_*.py"))):
        base = os.path.basename(path)
        src = _read(path)
        if not src:
            continue

        # a helper module imported out of the tests dir counts as part of the
        # suite for guard detection (msgvis.py holds the visibility contracts)
        blob = src
        for mod in set(_SIBLING_IMPORT.findall(src)):
            sib = os.path.join(tests_dir, mod + ".py")
            if os.path.exists(sib):
                blob += "\n" + _read(sib)
        blob = strip_prose(blob)

        flags = set(_FLAG.findall(src))
        over = SLOW.get(base, {})
        if "fast" in over:
            fast_args, why = over["fast"], over.get("why", "")
        elif "--hermetic" in flags:
            fast_args, why = ["--hermetic"], "suite advertises --hermetic"
        elif "--quick" in flags:
            fast_args, why = ["--quick"], "suite advertises --quick"
        else:
            fast_args, why = [], ""

        # ⚠ the live deployment. A suite that *uses* that port would talk to
        # the operator's running orgs, so refuse to start it — but match only
        # a USE (`=7360`, `:7360`, `port=7360`), never the prose promise not to
        # touch it, and quote the offending line so a false positive is
        # obvious rather than a silently missing suite.
        skip = ""
        for bad in FORBIDDEN_PORTS:
            hit = re.search(r"^.*[=(,:]\s*[\"']?" + bad + r"\b.*$", src, re.M)
            if hit:
                skip = (f"uses the live deployment's port :{bad} — refusing to "
                        f"start it  ⟨{hit.group(0).strip()[:60]}⟩")

        sid = base[len("test_"):-len(".py")].replace("_", "-")
        suites.append(Suite(
            sid,
            None if fast_args is None else [py, path] + fast_args,
            [py, path],
            REPO,
            # a container is as exclusive as a port — unless the suite
            # advertises `--docker`, which means Docker is opt-in and the
            # default run stubs the daemon out
            exclusive=bool(_EXCLUSIVE.search(src)
                           or (_DOCKERISH.search(src) and "--docker" not in flags)),
            windows_only=bool(_WINDOWS.search(src)),
            guard=bool(_GUARDISH.search(blob)),
            guard_hint=(_GUARDISH.search(blob).group(0)
                        if _GUARDISH.search(blob) else ""),
            fast_why=why,
            skip=skip,
        ))

    # ------------------------------------------------------------- frontend
    fe = os.path.join(REPO, "frontend")
    runner = os.path.join(fe, "tests", "run.mjs")
    if os.path.exists(runner):
        node = shutil.which("node")
        blob = strip_prose("".join(_read(p) for p in
                                   glob.glob(os.path.join(fe, "tests", "*.ts*"))))
        skip = ""
        if not node:
            skip = "node is not on PATH"
        elif not os.path.isdir(os.path.join(fe, "node_modules", "esbuild")):
            skip = "frontend/node_modules is missing — run `npm ci` in frontend/"
        cmd = [node or "node", os.path.join("tests", "run.mjs")]
        g = _GUARDISH.search(blob)
        suites.append(Suite("frontend", cmd, cmd, fe, guard=bool(g),
                            guard_hint=g.group(0) if g else "", skip=skip))

    return suites


# ------------------------------------------------------------------ running

class Result:
    __slots__ = ("suite", "rc", "out", "secs", "checks", "state",
                 "guard_state", "guard_lines", "truncated")


def child_env():
    env = dict(os.environ)
    # no suite may inherit a pointer at the operator's real deployment
    for k in [k for k in env if k.startswith("ORGTREE_")]:
        env.pop(k)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), 9)
    except Exception:                                            # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:                                            # noqa: BLE001
        pass


def run_one(suite, cmd, timeout, logdir):
    r = Result()
    r.suite, r.checks, r.guard_lines = suite, None, []
    t0 = time.time()
    kw = {} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(cmd, cwd=suite.cwd, env=child_env(),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **kw)
    try:
        out = proc.communicate(timeout=timeout)[0]
        r.rc = proc.returncode
        r.state = "PASS" if r.rc == 0 else "FAIL"
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out = proc.communicate()[0] or b""
        r.rc, r.state = -1, "TIMEOUT"
    r.secs = time.time() - t0
    r.out = (out or b"").decode("utf-8", "replace")

    for pat in _CHECKS:
        m = pat.search(r.out)
        if m:
            r.checks = int(m.group(1).replace(",", ""))
            break
    oks = _OK_LINE.findall(r.out)
    if r.checks is None and oks:
        r.checks = int(oks[-1])
    # a suite that exits 0 having printed `ok N` lines but never its own total
    # did not finish — the convention in this tree is a final `ALL N CHECKS
    # PASS`, and its absence is how a silently truncated run looks
    r.truncated = bool(r.rc == 0 and oks and not _TOTAL_LINE.search(r.out))

    # ⚠ A PASSING CHECK'S OWN LINE IS NOT EVIDENCE OF A FAILURE, and reading
    # it as one turned this alarm into noise on every single run (measured
    # 2026-08-26). `test_compaction.py` carries a check LABELLED
    #     ok 326  drift · (the shape) the phantom no longer matches the live
    #             node's session — it matches the BEARER that inherited it
    # and `no longer matches` is one of the `_GUARD_FIRED` alternatives, so a
    # clean fast tier printed the full DRIFT ALARM banner — "until then every
    # check downstream of it is a fiction" — over a suite that had just
    # passed. `_GUARD_HELD` did not rescue it either: that label reads
    # "drift ·", not "drift guard".
    #
    # The `ok N` prefix is the discriminator rather than any phrase in the
    # label, deliberately: label wording drifts (that is the whole subject
    # here), while "a check that reported itself passing did not just report a
    # failure" stays true however the labels are reworded. `_OK_LINE` is
    # REUSED and not re-spelled — two definitions of "a passing check's line"
    # in one file is the same rot this alarm exists to catch.
    fired = [ln for ln in r.out.splitlines()
             if _GUARD_FIRED.search(ln) and not _OK_LINE.match(ln)]
    held = _GUARD_HELD.search(r.out)
    if fired:
        r.guard_state, r.guard_lines = "FIRED", fired[:8]
    elif held:
        r.guard_state = "held"
    elif suite.guard and r.rc == 0:
        # only meaningful on a suite that RAN to completion — a failing suite
        # may simply not have reached its guard, which is not news
        r.guard_state = "silent"
    else:
        r.guard_state = ""

    with open(os.path.join(logdir, suite.id + ".log"), "w",
              encoding="utf-8", errors="replace") as fh:
        fh.write(" ".join(cmd) + "\n\n" + r.out)
    return r


# ------------------------------------------------------------------- report

BAR = "─" * 72

#: written into `logdir` by `emit_completion`, and by nothing else
COMPLETE_MARKER = "COMPLETE"


def emit_completion(logdir, planned, results, bad, skipped, wall, rc):
    """The last two acts of a run, kept in ONE function on purpose.

    Split across two call sites, the day would come when a `return` was added
    between them and a finished run stopped writing its own marker — which is
    the exact failure this whole mechanism exists to detect, reintroduced by
    the detector. One function, one caller: both artefacts or neither.

    Line first, file second — see the module docstring. An interruption
    between them must read as UNFINISHED.
    """
    line = (f"RUN COMPLETE  suites={len(results)}/{planned}  "
            f"passed={len(results) - len(bad)}  failed={len(bad)}  "
            f"skipped={len(skipped)}  rc={rc}  wall={wall:.1f}s")
    print()
    print(line, flush=True)
    try:
        with open(os.path.join(logdir, COMPLETE_MARKER), "w",
                  encoding="utf-8") as fh:
            fh.write(line + "\n" + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
    except OSError as e:
        # say it rather than swallow it — a missing marker is read as "this run
        # did not finish", so an unwritable logdir would make a complete run
        # lie about itself, in the safe direction but for a reason nobody
        # reading the log could ever find
        print(f"⚠ could not write the {COMPLETE_MARKER} marker in {logdir}: "
              f"{e}  — the run DID finish; the line above is the record.")


def plan_for(suites, args):
    """(runnable, skipped) — skipped carries a reason, never an error."""
    run, skipped = [], []
    for s in suites:
        if args.only and not any(f in s.id for f in args.only):
            continue
        # Explicitly declining the frontend suite is a policy choice, not a
        # missing prerequisite. It must win over discovery's dependency check
        # so `--no-frontend` remains the intentional escape hatch.
        if s.id == "frontend" and args.no_frontend:
            skipped.append((s, "--no-frontend"))
        elif s.skip:
            skipped.append((s, s.skip))
        elif s.windows_only and os.name != "nt":
            skipped.append((s, "asserts Windows filesystem semantics "
                               "(WinError/MoveFileEx) — not meaningful here"))
        elif s.cmd(args.full) is None:
            skipped.append((s, SLOW.get("test_" + s.id.replace("-", "_") + ".py",
                                        {}).get("why", "full tier only")
                               + "  →  --full"))
        else:
            run.append(s)
    return run, skipped


def required_skip_failures(skipped):
    """Mandatory suites blocked by setup, rather than intentionally skipped.

    The frontend is part of both ordinary tiers. Missing Node or dependencies
    means the tier did not establish its claim, even when every backend suite
    passed. `--no-frontend` is the one explicit opt-out and stays green.
    """
    return [(s, why) for s, why in skipped
            if s.id == "frontend" and why != "--no-frontend"]


def main():
    ap = argparse.ArgumentParser(
        prog="run_tests.py", description="run every orgtree test suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="tiers:\n"
               "  fast (default)  every suite in its cheapest advertised mode;\n"
               "                  no live rig, no real elapsed time. ~2 min as\n"
               "                  of 12 suites / 1,853 checks. CI runs this.\n"
               "  --full          every suite at full depth, live rigs\n"
               "                  included. ~13 min measured.")
    ap.add_argument("--full", action="store_true", help="the full tier")
    # repeatable AND comma-separated (2026-08-18): both `--only a --only b`
    # and `--only a,b` used to keep only the last token — and a filter that
    # matched nothing printed "plan · 0 to run" and EXITED 0, so a CI line
    # could pass having tested nothing at all
    ap.add_argument("--only", action="append", default=[], metavar="SUBSTR",
                    help="only suites whose id contains this (repeatable, "
                         "comma-separated)")
    ap.add_argument("--jobs", type=int, default=0,
                    help="parallel workers (default: min(4, cpus))")
    ap.add_argument("--serial", action="store_true", help="one suite at a time")
    ap.add_argument("--no-frontend", action="store_true")
    ap.add_argument("--timeout", type=int, default=0, help="per-suite seconds")
    ap.add_argument("--logdir", default="")
    ap.add_argument("--list", action="store_true", help="print the plan only")
    args = ap.parse_args()
    args.only = [t.strip() for chunk in args.only for t in chunk.split(",")
                 if t.strip()]

    py = _interpreter()
    jobs = 1 if args.serial else (args.jobs or min(4, os.cpu_count() or 1))
    timeout = args.timeout or (600 if not args.full else 5400)
    logdir = args.logdir or tempfile.mkdtemp(prefix="orgtree-tests-")
    os.makedirs(logdir, exist_ok=True)

    suites = discover(py)
    run, skipped = plan_for(suites, args)
    par = [s for s in run if not s.exclusive]
    exc = [s for s in run if s.exclusive]

    print("orgtree · test runner")
    print(f"  repo    {REPO}")
    print(f"  python  {py}")
    print(f"  tier    {'FULL' if args.full else 'fast'}"
          f"   ·  jobs {jobs}   ·  per-suite timeout {timeout}s")
    print(f"  logs    {logdir}")
    print()
    print(f"plan · {len(run)} to run, {len(skipped)} skipped")
    for s in par + exc:
        lane = "exclusive" if s.exclusive else "parallel "
        extra = " ".join(s.cmd(args.full)[2:]) or "—"
        print(f"  {lane}  {s.id:<24} {extra:<12}"
              f"{'  ⚑ drift guard' if s.guard else ''}")
    for s, why in skipped:
        print(f"  skipped    {s.id:<24} {why}")
    if args.list:
        return 0     # no marker: --list ran nothing, so there is nothing to
                     # claim finished. Same for the refusal below.
    if not run:
        # silence is not success — a filter that leaves nothing to run is
        # an error, not a green run of zero suites
        if skipped:
            print(f"\nNOTHING TO RUN — every match was skipped "
                  f"({', '.join(x.id for x, _ in skipped)}); "
                  f"reasons above.")
        elif args.only:
            print(f"\nNOTHING TO RUN — --only {args.only!r} matched "
                  f"no suite. Ids: "
                  f"{', '.join(sorted(x.id for x in suites))}")
        else:
            print("\nNOTHING TO RUN — no suites were found.")
        return 2
    print()

    results = []
    t0 = time.time()

    def done(r):
        mark = {"PASS": "✓", "FAIL": "✗", "TIMEOUT": "⏱"}[r.state]
        n = f"{r.checks} checks" if r.checks is not None else ""
        flag = {"FIRED": "  ⚑ DRIFT", "silent": "  ⚐ guard silent"}.get(
            r.guard_state, "")
        if r.truncated:
            flag += "  ⚐ no final total"
        print(f"  {mark} {r.suite.id:<26}{n:>12}{r.secs:9.1f}s{flag}", flush=True)

    # the parallel lane first; the exclusive lane runs alone afterwards so no
    # timing-sensitive suite is competing with anything for the CPU
    if par:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futs = {pool.submit(run_one, s, s.cmd(args.full), timeout, logdir): s
                    for s in par}
            for f in concurrent.futures.as_completed(futs):
                r = f.result()
                results.append(r)
                done(r)
    for s in exc:
        r = run_one(s, s.cmd(args.full), timeout, logdir)
        results.append(r)
        done(r)

    wall = time.time() - t0
    results.sort(key=lambda r: r.suite.id)

    print()
    print(BAR)
    print(f"{'suite':<26}{'status':<10}{'checks':>8}{'time':>10}   drift")
    print(BAR)
    for r in results:
        n = "" if r.checks is None else f"{r.checks:,}"
        g = {"FIRED": "⚑ FIRED", "held": "held", "silent": "⚐ silent"}.get(
            r.guard_state, "—")
        print(f"{r.suite.id:<26}{r.state:<10}{n:>8}{r.secs:9.1f}s   {g}")
    blocked = required_skip_failures(skipped)
    blocked_ids = {s.id for s, _why in blocked}
    for s, why in skipped:
        state = "BLOCKED" if s.id in blocked_ids else "SKIP"
        print(f"{s.id:<26}{state:<10}{'':>8}{'':>10}   {why[:40]}")
    print(BAR)

    bad = [r for r in results if r.state != "PASS"]
    total = sum(r.checks or 0 for r in results)
    print(f"{len(results) - len(bad)} passed · {len(bad)} failed · "
          f"{len(skipped)} skipped      {total:,} checks      {wall:.1f}s wall")

    guards = [r for r in results if r.guard_state in ("held", "FIRED", "silent")]
    held = sum(1 for r in guards if r.guard_state == "held")
    fired = [r for r in guards if r.guard_state == "FIRED"]
    silent = [r for r in guards if r.guard_state == "silent"]
    print(f"drift guards: {held} held · {len(fired)} FIRED · {len(silent)} silent")

    if blocked:
        print()
        print("REQUIRED SUITE BLOCKED — this run is not green:")
        for s, why in blocked:
            print(f"  {s.id}: {why}")

    if fired:
        print()
        print("⚑" * 36)
        print("⚑ DRIFT ALARM — a test's model of the source no longer matches")
        print("⚑ the source. This is NOT an ordinary failure: nothing is")
        print("⚑ necessarily broken at runtime. A guarded expression in a")
        print("⚑ production file moved, and the test that mirrors it did not.")
        print("⚑ Fix the MIRROR (or revert the source), then re-run — until")
        print("⚑ then every check downstream of it is a fiction.")
        print("⚑" * 36)
        for r in fired:
            print(f"\n  {r.suite.id}:")
            for ln in r.guard_lines:
                print(f"    {ln.strip()[:160]}")
    cut = [r for r in results if r.truncated]
    if cut:
        print()
        for r in cut:
            print(f"⚐ {r.suite.id} exited 0 after {r.checks} `ok` lines but "
                  f"never printed its own total — the convention here is a "
                  f"final `ALL N CHECKS PASS`, so this run did not finish.")
    if silent:
        print()
        for r in silent:
            print(f"⚐ {r.suite.id} names a drift guard ⟨{r.suite.guard_hint}⟩ "
                  f"but printed no verdict. Either the guard did not run (a "
                  f"--only filter, an early return), its output changed shape, "
                  f"or the phrase is only prose — check {logdir}.")

    if bad:
        print()
        for r in bad:
            print(f"✗ {r.suite.id}  ({r.state})   log: "
                  f"{os.path.join(logdir, r.suite.id + '.log')}")
            # the tail of one of these suites is usually a pinned-exception
            # epilogue, not the failure — surface the failure lines first
            live = [ln for ln in r.out.splitlines() if ln.strip()]
            # ⚠ A PASSING CHECK'S OWN LINE IS NOT THE FAILURE — the same rule
            # the drift alarm needed, in the other reporting path. `Error:`
            # matches inside labels like `ok 9  limit-detect · no 'API Error:
            # 500 …'`, so a failing turn-lifecycle showed the reader four
            # GREEN lines and "… 119 lines in the log", with the actual cause
            # nowhere on screen (measured 2026-08-28). That excerpt is the
            # only thing most readers see, and it was pointing at passing
            # checks while three agents hunted defects that did not exist.
            # `_OK_LINE` is reused, not re-spelled — one definition of "a
            # passing check's line" in this file. See D-170.
            hits = [ln for ln in live
                    if re.search(r"\bFAIL(?:ED)?\b|Traceback|Error:|not ok|✗|✖",
                                 ln) and not _OK_LINE.match(ln)][:10]
            for ln in hits or live[-12:]:
                print(f"    {ln[:160]}")
            if hits:
                print(f"    … {len(live)} lines in the log")

    # ⚠ A RUN THAT FAILED STILL FINISHED. The marker answers "did this run
    # reach the end", which is a different question from "did it pass" — rc
    # carries that, in the line and in the exit status. Gating the marker on
    # `not bad` would make a red run and a killed run identical again, and
    # telling those two apart is the entire point.
    rc = 1 if bad or blocked else 0
    emit_completion(logdir, len(run), results, bad, skipped, wall, rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
