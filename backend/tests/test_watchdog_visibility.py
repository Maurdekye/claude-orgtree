"""Watchdog ABSTENTION VISIBILITY — the suite for the 2026-08-22 fix.

THE DEFECT THIS COVERS
----------------------
`supervisor._wd_popen` hands a command/stream dog's target to `shell=True`
with the backend SERVICE's environment. On Windows that is cmd.exe with the
service PATH — no Git usr\\bin, so grep/sed/awk/tr/`$(...)`/`$VAR`//tmp/GNU
`find` are all absent. Meanwhile `orgtree_watchdog` told agents a dog "runs
WITH YOUR HANDS (needs your bash)". Agents wrote bash, cmd.exe answered
"'grep' is not recognized", and the dog reported `state: armed, fired: 0` —
which is ALSO exactly what a healthy dog waiting on a condition reports.
Three dogs on this machine were dead that way for up to nine days.

The PATH is the accident. The defect is that **an abstention read exactly
like a pass**, which is this subtree's standing failure shape. So these
checks are about EVIDENCE, not about grep: that a dog which ran and saw
nothing says so, in words, in `orgtree_watchdog list`, and that a dog whose
target cannot run says so at CREATE time.

WHY EVERY CHECK HERE COMES IN A PAIR
------------------------------------
An all-green harness is the symptom, not the proof (team charter §3). Every
positive assertion below has a NEGATIVE twin that must come out the other
way — a healthy dog whose health line must stay "ok" next to a broken one
whose health line must not; a target that runs next to one that does not.
A check that can only ever pass has proved nothing.

Run:  python tests/test_watchdog_visibility.py
Mutation harness: MUTANTS.md next to this file lists the mutations that must
kill named checks here, with the control pair.
"""

import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ⚠ BEFORE the first orgtree import — `store` resolves ORGTREE_DATA at import
# time, so a root set afterwards leaves an env var that says "isolated" and a
# module pointed at production.
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-wdvis-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    # the discard port: nothing here may reach the operator's real mail hub
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

import _no_deploy                                                # noqa: E402
from orgtree import store, supervisor                            # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

# ☠ both halves of "no test may touch production", armed before any check.
# The engine this suite exercises RUNS every armed dog it can see and can
# WAKE their owners — against the live root that is real spawns, real mail
# and real billed turns.
_no_deploy.install()
_no_deploy.assert_isolated_data_root()

# ⚠ …and confirm WHICH orgtree we are actually testing. A suite run from a
# worktree while PYTHONPATH points at main imports MAIN and reports confident
# numbers about the wrong code — measured in this repo before. `sys.path`
# insertion above is supposed to win; this is the check that it did.
_HERE = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
_GOT = os.path.realpath(os.path.dirname(os.path.dirname(supervisor.__file__)))
if _GOT != _HERE:
    raise SystemExit(
        f"☠ REFUSING TO RUN: this suite lives under {_HERE!r} but imported "
        f"orgtree from {_GOT!r}. Every number it printed would be about a "
        f"different checkout. Clear PYTHONPATH and run it again.")
print(f"testing orgtree at: {_GOT}")

PASS = 0
FAIL: list[str] = []
WIN = os.name == "nt"


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception as e:                                       # noqa: BLE001
        FAIL.append(f"{label}: {e}")
        print(f"  FAIL  {label}\n        {e}")
        return
    PASS += 1
    print(f"  ok    {label}")


def dog(**over):
    """A bare watchdog dict, the shape the engine actually handles."""
    d = {"id": "w1", "owner": "k", "name": "d", "kind": "command",
         "target": "echo hi", "pattern": "hi", "state": "armed",
         "at": supervisor.now_iso(), "fired": 0, "events": []}
    d.update(over)
    return d


def aged(seconds):
    """An `at` stamp `seconds` in the past — `wd_health`'s age thresholds are
    the whole point of it, so they have to be reachable without sleeping."""
    import datetime as dtm
    t = dtm.datetime.now(dtm.timezone.utc) - dtm.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class _Org:
    """Just enough Org for `_wd_popen` / `wd_smoke`: they want d['slug'] and
    the sandbox probe. Deliberately NOT a real org — nothing in this section
    should be able to reach a doc at all."""
    def __init__(self, slug="zz-wdvis"):
        self.d = {"slug": slug, "key": None}


# ---------------------------------------------------------------------------
print("\n§0 · the interlock itself")
# The interlock is a guard, so charter §3 applies to IT: what would make it
# fail? Feed it production and watch it refuse.


def _interlock_refuses_the_live_root():
    assert _no_deploy.data_root_isolated(), \
        "the interlock says this suite's own temp root is production"
    real = store.DATA_ROOT
    try:
        store.DATA_ROOT = os.path.expanduser("~/orgtree")
        assert not _no_deploy.data_root_isolated(), (
            "THE INTERLOCK IS A FICTION: pointed straight at ~/orgtree it "
            "still reported the root isolated")
        store.DATA_ROOT = os.path.join(os.path.expanduser("~/orgtree"),
                                       "orgs")
        assert not _no_deploy.data_root_isolated(), \
            "a path INSIDE the live root passed the interlock"
    finally:
        store.DATA_ROOT = real
    assert _no_deploy.data_root_isolated(), "the interlock was left broken"


check("interlock: passes on a temp root, REFUSES ~/orgtree and paths inside "
      "it (control pair)", _interlock_refuses_the_live_root)

check("interlock: the deploy seam is armed", lambda: (
    _no_deploy.installed() or _raise("the deploy interlock is not installed")))


def _raise(msg):
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
print("\n§1 · wd_shell / wd_output_broken — the platform truth, named once")


def _shell_is_the_platform_truth():
    o = _Org()
    got = supervisor.wd_shell(o)
    want = "cmd" if WIN else "sh"
    assert got == want, f"wd_shell said {got!r} on os.name={os.name!r}"
    # …and it must agree with what _wd_popen ACTUALLY does. A constant that
    # merely SAYS "cmd" while the spawn does something else is the same class
    # of lie this whole fix is about, so ask the shell to identify itself.
    probe = ("echo WDPROBE-%COMSPEC%" if WIN
             else "echo WDPROBE-$0")
    p = subprocess.Popen(probe, shell=True, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    out = (p.communicate(timeout=30)[0] or "")
    assert "WDPROBE-" in out, f"the probe did not run at all: {out!r}"
    if WIN:
        assert "cmd.exe" in out.lower(), (
            "shell=True on Windows is not cmd.exe after all — wd_shell's "
            f"whole premise is wrong: {out!r}")


check("wd_shell names the shell `shell=True` really gives (probed, not "
      "assumed)", _shell_is_the_platform_truth)


def _shell_note_names_the_trap():
    note = supervisor.wd_shell_note("cmd")
    for word in ("cmd.exe", "grep", "findstr", "PATH"):
        assert word in note, f"the cmd idiom note never mentions {word!r}"
    assert supervisor.wd_shell_note("sh") != note, \
        "both platforms get the same note — one of them is wrong"


check("wd_shell_note spells out the cmd idiom, and differs from the sh one",
      _shell_note_names_the_trap)


def _broken_sniffer_is_a_positive_marker():
    # POSITIVE: the shell's own words
    assert supervisor.wd_output_broken(
        "'grep' is not recognized as an internal or external command,\n"
        "operable program or batch file.")
    assert supervisor.wd_output_broken("sh: 1: grep: command not found")
    # CONTROL — these must SURVIVE. A sniffer that calls everything broken is
    # exactly as useless as one that calls nothing broken.
    assert supervisor.wd_output_broken("") is None, \
        "empty output was called broken — but a findstr that matched " \
        "nothing prints nothing, and it is healthy"
    assert supervisor.wd_output_broken("LISTENING 0.0.0.0:7357") is None
    assert supervisor.wd_output_broken("ERROR: build failed") is None, \
        "a real error message from a working command was called broken"


check("wd_output_broken fires on shell 'not recognized' and NOT on quiet or "
      "ordinary output (control pair)", _broken_sniffer_is_a_positive_marker)


# ---------------------------------------------------------------------------
print("\n§2 · wd_health — the abstention detector")


def _health_separates_fresh_from_long_dead():
    # ① the case that started all this: 700 checks, nine days, zero fires
    old = supervisor.wd_health(dog(at=aged(9 * 86400), checks_run=700,
                                   fired=0, last_output="no matches"))
    assert old and "700" in old and "never matched" in old.lower(), \
        f"a nine-day silent dog reported: {old!r}"
    # ② CONTROL — the same dog, armed thirty seconds ago, must stay quiet.
    # This is the twin that makes ① mean something: if both warned, the
    # warning would be noise and agents would learn to ignore it.
    fresh = supervisor.wd_health(dog(at=aged(30), checks_run=1, fired=0,
                                     last_output=""))
    assert fresh is None, f"a 30-second-old dog was flagged: {fresh!r}"
    # ③ CONTROL — an old dog that IS firing is healthy, however old
    working = supervisor.wd_health(dog(at=aged(9 * 86400), checks_run=700,
                                       fired=12, last_output="hit"))
    assert working is None, f"a dog that fires 12 times was flagged: {working!r}"
    # ④ CONTROL — a paused/exited dog says so via `state`; a second warning
    # would just be noise
    assert supervisor.wd_health(dog(at=aged(9 * 86400), checks_run=700,
                                    state="paused")) is None


check("health: 700 quiet checks over 9 days WARNS; the same dog fresh, or "
      "firing, or paused stays silent (control pair)",
      _health_separates_fresh_from_long_dead)


def _health_reports_the_broken_target_loudest():
    h = supervisor.wd_health(dog(
        at=aged(3600), checks_run=60, fired=0,
        last_output="'grep' is not recognized as an internal or external "
                    "command"))
    assert h and "BROKEN" in h, f"a dog running a nonexistent command: {h!r}"
    assert "never fire" in h, "the note does not say the dog CAN NEVER fire"
    # CONTROL — identical counters, output that merely did not match
    ok = supervisor.wd_health(dog(at=aged(3600), checks_run=60, fired=0,
                                  last_output="port 7357: closed"))
    assert ok is None, f"a working-but-quiet dog was called broken: {ok!r}"


check("health: a 'not recognized' output is reported BROKEN, and an "
      "ordinary non-match is not (control pair)",
      _health_reports_the_broken_target_loudest)


def _health_catches_the_dog_that_never_ran():
    h = supervisor.wd_health(dog(at=aged(7200), checks_run=0, fired=0))
    assert h and "NEVER RUN A CHECK" in h, \
        f"a dog the engine never picked up: {h!r}"
    assert supervisor.wd_health(dog(at=aged(30), checks_run=0)) is None, \
        "a just-armed dog was flagged for not having run yet"


check("health: armed-but-never-checked is its own diagnosis, and a "
      "just-armed dog is not (control pair)",
      _health_catches_the_dog_that_never_ran)


def _health_covers_streams_too():
    h = supervisor.wd_health(dog(kind="stream", at=aged(9 * 3600),
                                 checks_run=0, fired=0))
    assert h and "ZERO output lines" in h, f"a mute stream reported: {h!r}"
    # CONTROL — a stream that is reading plenty and simply not matching yet
    assert supervisor.wd_health(dog(kind="stream", at=aged(9 * 3600),
                                    checks_run=5000, fired=0,
                                    last_output="…")) is None


check("health: a stream that has read ZERO lines warns; a chatty "
      "non-matching one does not (control pair)", _health_covers_streams_too)


# ---------------------------------------------------------------------------
print("\n§3 · the engine records what it SAW, not just what matched")


def _command_check_returns_raw_output_and_code():
    o = _Org()
    scratch = store.scratch_dir(o.d["slug"], "k") \
        if hasattr(store, "scratch_dir") else None
    os.makedirs(supervisor.scratch_dir(o.d["slug"], "k"), exist_ok=True)
    assert scratch is None or True
    # ① a command that RUNS and matches
    lines, raw, code = supervisor._wd_run_command(
        o, dog(target="echo WDHIT-yes", pattern="WDHIT"))
    assert lines and "WDHIT-yes" in lines[0], f"lines={lines!r}"
    assert "WDHIT-yes" in raw, f"raw output was dropped: {raw!r}"
    assert code == 0, f"exit code {code!r}"
    # ② a command that RUNS and does not match — raw must STILL carry what it
    # printed. This is the field whose absence hid the whole defect.
    lines, raw, code = supervisor._wd_run_command(
        o, dog(target="echo WDHIT-no", pattern="NOTHINGLIKETHIS"))
    assert lines == [], f"it matched something it should not: {lines!r}"
    assert "WDHIT-no" in raw, (
        "A NON-MATCHING CHECK THREW AWAY ITS OUTPUT — this is the exact "
        f"defect: the dog saw {raw!r} and reported nothing at all")
    # ③ the real thing: a bash-idiom command under this platform's shell
    lines, raw, code = supervisor._wd_run_command(
        o, dog(target="wd-no-such-binary-xyz --please", pattern="anything"))
    assert lines == [], "a failing command matched its pattern"
    assert supervisor.wd_output_broken(raw), (
        "the shell's own 'not recognized'/'not found' never reached the "
        f"dog — raw was {raw!r}, so `health` can never diagnose it")


check("_wd_run_command returns raw output + exit code, including for checks "
      "that MATCHED NOTHING (control pair)",
      _command_check_returns_raw_output_and_code)


def _the_service_path_kills_the_bash_idiom():
    """The production condition, REPRODUCED deliberately rather than
    inherited — and the documented idiom shown to work under it.

    ⚠ MEASURED WHILE WRITING THIS SUITE, and it is the trap the whole fix is
    about. The first version of this check just ran `echo hello | grep hello`
    and asserted it failed. It PASSED grep's output instead: a suite inherits
    the PATH of whoever launched it, and an agent's terminal has Git's
    usr\\bin on it while the BACKEND SERVICE does not. That check would have
    reported "the bash idiom works fine here" while three dogs lay dead of it
    — a green result proving the opposite of the truth, from the same family
    as the defect.

    So the PATH is SET to a service-like one for the probe. Control pair
    under that same stripped PATH: a cmd builtin and `findstr` (System32 —
    the idiom the tool card now tells agents to write) must both SURVIVE, or
    the stripping broke everything and the grep failure means nothing."""
    o = _Org()
    os.makedirs(supervisor.scratch_dir(o.d["slug"], "k"), exist_ok=True)

    def raw_of(target, pattern="."):
        return supervisor._wd_run_command(
            o, dog(target=target, pattern=pattern))[1]

    real_path = os.environ.get("PATH", "")
    # what the backend service actually gets: System32 and friends, no Git
    service_path = (os.pathsep.join([
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "system32"),
        os.environ.get("SystemRoot", r"C:\Windows")]) if WIN else "/nonexistent")
    try:
        os.environ["PATH"] = service_path
        broken = raw_of("echo hello | grep hello", "hello")
        # ① THE DEFECT, under the real condition
        assert supervisor.wd_output_broken(broken), (
            "under a SERVICE-LIKE PATH the bash idiom still worked — either "
            f"the strip did not take or grep is elsewhere. Got: {broken!r}")
        # ② CONTROL — the strip must not have broken everything
        alive = raw_of("echo WDPATH-alive", "WDPATH")
        assert "WDPATH-alive" in alive and not supervisor.wd_output_broken(alive), \
            f"the stripped PATH broke even a shell builtin: {alive!r}"
        # ③ CONTROL + the positive claim of item 1: the DOCUMENTED idiom
        # works under exactly the PATH that kills the bash one
        if WIN:
            good = raw_of("echo LISTENING | findstr /C:LISTENING",
                          "LISTENING")
            assert "LISTENING" in good and not supervisor.wd_output_broken(good), (
                "the idiom the tool card now recommends (findstr) does NOT "
                f"work under the service PATH — the fix would be wrong: {good!r}")
    finally:
        os.environ["PATH"] = real_path
    # ④ CONTROL — with the FULL path restored, the engine is not permanently
    # broken by anything above
    assert "WDPATH-back" in raw_of("echo WDPATH-back", "WDPATH")


check("service PATH: the bash idiom dies under it, while a builtin and the "
      "DOCUMENTED findstr idiom survive (control pair)",
      _the_service_path_kills_the_bash_idiom)


def _mark_check_makes_abstention_countable():
    w = dog()
    supervisor._wd_mark_check(w, time.time(), "saw nothing", 1)
    assert w["checks_run"] == 1 and w["last_output"] == "saw nothing"
    assert w["last_exit"] == 1 and w["last_check"]
    supervisor._wd_mark_check(w, time.time(), "", None)
    assert w["checks_run"] == 2, "the counter did not advance"
    assert w["last_output"] == "", "an empty observation was not recorded"
    assert "last_exit" not in w, "a stale exit code outlived its check"


check("_wd_mark_check counts every check and records even an EMPTY "
      "observation", _mark_check_makes_abstention_countable)


def _file_and_process_checks_report_what_they_saw():
    o = _Org()
    d = tempfile.mkdtemp(prefix="wdvis-file-")
    log = os.path.join(d, "app.log")
    # ① a path that does not exist — the likeliest reason a file dog is
    # silent, and it used to be indistinguishable from patience
    _l, _hw, seen = supervisor._wd_check_poll(
        "zz", dog(kind="file", target=os.path.join(d, "nope.log"),
                  pattern="X"), o)
    assert "cannot read" in seen, f"an absent target reported: {seen!r}"
    # ② CONTROL — a real, quiet file must NOT claim to be unreadable
    open(log, "w", encoding="utf-8").close()
    w = dog(kind="file", target=log, pattern="ERROR")
    _l, hw, seen = supervisor._wd_check_poll("zz", w, o)
    assert "cannot" not in seen and "0 bytes" in seen, f"seen={seen!r}"
    w["high_water"] = hw
    # binary, so the byte count below is the same on both platforms (text
    # mode turns "\n" into 2 bytes on Windows — measured, it made this
    # assertion platform-dependent)
    with open(log, "ab") as fb:
        fb.write(b"just noise\n")
    lines, hw, seen = supervisor._wd_check_poll("zz", w, o)
    assert lines == [], "'just noise' matched /ERROR/"
    assert "+11 new byte(s)" in seen and "0 matched" in seen, (
        "a file that GREW but matched nothing reported nothing — the exact "
        f"abstention shape: {seen!r}")
    # ③ process: a target that has never been seen up can never show the
    # DOWN edge, and must say so
    _l, _hw, seen = supervisor._wd_check_poll(
        "zz", dog(kind="process", target="port:9"), o)
    assert "cannot occur" in seen, f"a never-up process dog said: {seen!r}"


check("file/process checks report what they saw — absent path, quiet file, "
      "and a DOWN edge that can never come (control pair)",
      _file_and_process_checks_report_what_they_saw)


# ---------------------------------------------------------------------------
print("\n§4 · wd_smoke — failing loudly at create time")


def _smoke_runs_the_real_target_through_the_real_spawn():
    o = _Org()
    os.makedirs(supervisor.scratch_dir(o.d["slug"], "k"), exist_ok=True)
    # ① a working target
    ok = supervisor.wd_smoke(o, "k", "command", "echo WDSMOKE-ok",
                             "WDSMOKE", timeout=20)
    assert "WDSMOKE-ok" in ok["output"], f"smoke output: {ok!r}"
    assert ok["exit_code"] == 0, f"exit_code {ok.get('exit_code')!r}"
    assert ok.get("matched") is True, "the pattern matched but smoke said no"
    assert not ok.get("broken"), f"a working command was called broken: {ok!r}"
    # ② the defect, caught in five seconds instead of nine days
    bad = supervisor.wd_smoke(o, "k", "command",
                              "wd-no-such-binary-xyz | grep x", "x",
                              timeout=20)
    assert bad.get("broken") is True, (
        "THE CREATE-TIME SMOKE RUN DID NOT NOTICE A TARGET THAT CANNOT RUN — "
        f"this is the whole point of it: {bad!r}")
    assert "DID NOT RUN" in bad["ran"], f"ran={bad['ran']!r}"
    # ③ a target that runs but matches nothing is NOT broken — it is the
    # ordinary case, and calling it broken would train agents to ignore this
    quiet = supervisor.wd_smoke(o, "k", "command", "echo nothing-here",
                                "WILLNOTMATCH", timeout=20)
    assert not quiet.get("broken"), f"a quiet-but-working target: {quiet!r}"
    assert quiet.get("matched") is False
    assert "nothing-here" in quiet["output"], \
        "the agent is not shown what its target actually printed"


check("smoke: a working target reports its output+code, a nonexistent one is "
      "flagged BROKEN, a quiet one is not (control pair)",
      _smoke_runs_the_real_target_through_the_real_spawn)


def _smoke_knows_a_stream_should_not_exit():
    o = _Org()
    os.makedirs(supervisor.scratch_dir(o.d["slug"], "k"), exist_ok=True)
    dead = supervisor.wd_smoke(o, "k", "stream", "echo done", None,
                               timeout=6)
    assert dead.get("broken") is True, \
        f"a stream command that exits instantly was accepted: {dead!r}"
    assert "EXITED IMMEDIATELY" in dead["ran"]
    # CONTROL — something that keeps running must NOT be called broken
    live = ("ping -n 30 127.0.0.1" if WIN else "sleep 30")
    alive = supervisor.wd_smoke(o, "k", "stream", live, None, timeout=6)
    assert not alive.get("broken"), \
        f"a stream that stayed alive was called broken: {alive!r}"
    assert "still running" in alive["ran"]


check("smoke: a stream whose command EXITS is broken; one that keeps "
      "listening is not (control pair)", _smoke_knows_a_stream_should_not_exit)


def _smoke_answers_file_and_process_kinds_honestly():
    o = _Org()
    d = tempfile.mkdtemp(prefix="wdvis-smoke-")
    p = os.path.join(d, "there.log")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("existing content\n")
    r = supervisor.wd_smoke(o, "k", "file", p)
    assert "exists" in r["ran"] and "APPENDED" in r["note"], (
        "a file dog's creator is not told that existing content will never "
        f"fire it: {r!r}")
    r = supervisor.wd_smoke(o, "k", "file", os.path.join(d, "absent.log"))
    assert "does not exist" in r["ran"], f"{r!r}"
    # the DOWN-edge trap, named at create time
    r = supervisor.wd_smoke(o, "k", "process", "port:9")
    assert "DOWN" in r["ran"] and "comes UP and goes down" in r["note"], (
        f"a dog armed on an already-down target was not warned: {r!r}")


check("smoke: file kinds warn that existing content never fires; process "
      "kinds warn about an already-DOWN target",
      _smoke_answers_file_and_process_kinds_honestly)


# ---------------------------------------------------------------------------
print("\n§5 · what `orgtree_watchdog list` actually returns")
# The projection is the surface an agent reads. Everything above is invisible
# if this drops it — which is precisely what it used to do.


def _list_projection_surfaces_the_evidence():
    o = store.create_org("zz wd list projection")
    try:
        o.hire(USER, None, "haiku", 5, "k")
        r = o.watchdog_create("k", "quiet", "file", "E:/nope.log", "ERROR")
        w = o._watchdog(r["id"])
        w.update(at=aged(9 * 86400), checks_run=700, fired=0,
                 last_output="'grep' is not recognized as an internal or "
                             "external command", last_check=aged(60))
        store.save_org(o)
        row = supervisor.wd_list_row(w)
        for field in ("checks_run", "last_check", "last_output", "health"):
            assert field in row, (
                f"`list` still hides {field!r} — an agent would have to read "
                f"orgs/<slug>.json by hand, which is what made three dogs "
                f"look healthy for nine days")
        assert row["checks_run"] == 700
        assert "BROKEN" in row["health"], f"health={row['health']!r}"
        # CONTROL — a healthy dog in the same projection must say "ok", not
        # nothing. A field that is present only when something is wrong
        # cannot be trusted to be absent when nothing is.
        r2 = o.watchdog_create("k", "fresh", "file", "E:/other.log", "ERROR")
        w2 = o._watchdog(r2["id"])
        row2 = supervisor.wd_list_row(w2)
        assert row2["health"] == "ok", f"a fresh dog reported {row2['health']!r}"
        assert row2["checks_run"] == 0, \
            "a never-checked dog must report 0, not omit the field"
    finally:
        try:
            store.delete_org(o.d["slug"])
        except Exception:                                    # noqa: BLE001
            pass


check("list: surfaces checks_run/last_check/last_output/health, and a fresh "
      "dog reports health 'ok' (control pair)",
      _list_projection_surfaces_the_evidence)


def _the_handler_uses_the_projection_this_suite_tests():
    """The checks above call `supervisor.wd_list_row`. That is only worth
    anything while the HANDLER calls it too — otherwise this suite tests a
    function nothing ships. Mutation-verified: inlining the projection back
    into api.py must turn this red."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "orgtree",
                            "api.py"), encoding="utf-8").read()
    i = src.find('elif act == "list":')
    assert i > 0, "the watchdog list branch moved — re-anchor this check"
    body = src[i:i + 1200]
    assert "wd_list_row" in body, (
        "api.py's watchdog `list` branch no longer calls "
        "supervisor.wd_list_row — the projection this suite verifies is not "
        "the one agents receive")


check("list: the api.py handler ships the very projection tested above",
      _the_handler_uses_the_projection_this_suite_tests)


# ---------------------------------------------------------------------------
print("\n§6 · the tool description agents actually read")


def _the_card_no_longer_tells_agents_to_write_bash():
    from orgtree import mcptool                             # noqa: PLC0415
    card = next(t for t in mcptool.TOOLS
                if t["name"] == "orgtree_watchdog")
    blob = card["description"] + str(card["inputSchema"])
    # POSITIVE markers, deliberately: asserting the ABSENCE of the old
    # "needs your bash" wording would pass just as happily if the whole card
    # vanished.
    for word in ("cmd.exe", "findstr", "smoke", "checks_run"):
        assert word in blob, f"the tool card never mentions {word!r}"
    assert "NOT IN YOUR SHELL" in blob, \
        "the card does not warn that the dog's shell is not the agent's"
    # …and the old claim must be gone, checked as a pair with the above so a
    # deleted card cannot pass
    assert "runs WITH YOUR HANDS" not in blob, \
        "the card still says a dog runs with the agent's own hands"


check("tool card: names cmd.exe + findstr + smoke, and no longer claims the "
      "agent's own hands (control pair)",
      _the_card_no_longer_tells_agents_to_write_bash)


# ---------------------------------------------------------------------------
print("\n§7 · END TO END — the engine, one tick, both directions")
# Everything above tests a piece. This runs the REAL `_wd_tick`: due-check
# selection, the command pool, the done-callback, the doc write, `_wd_fire`,
# and the wake. Coordinator's standard for this fix is exactly this pair —
# a dog in the DOCUMENTED idiom SEEN TO FIRE, and a broken one SEEN TO BE
# REPORTED rather than silently armed — so it is one check with two dogs in
# one tick under one PATH, which is the only way the comparison is fair.


def _one_real_tick_fires_the_good_dog_and_diagnoses_the_bad_one():
    from concurrent.futures import ThreadPoolExecutor        # noqa: PLC0415
    # ☠ the fire path calls send_message(wake=True), which starts a REAL
    # `claude -p` turn. Intercepted and RECORDED — a positive marker that the
    # wake was reached, rather than an unverifiable absence.
    _no_deploy.install_no_turn_spawn()
    _no_deploy.WAKES.clear()
    o = store.create_org("zz wd end to end")
    d = tempfile.mkdtemp(prefix="wdvis-e2e-")
    marker = os.path.join(d, "state.txt")
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("service: WDLIVE-READY\n")
    real_path = os.environ.get("PATH", "")
    real_pool = supervisor._wd_cmd_pool
    try:
        slug = o.d["slug"]
        o.hire(USER, None, "haiku", 5, "k", add_dirs=[],
               tools={"bash": True, "web": False, "edit": False,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="e2e watchdog fixture")
        # ① THE DOCUMENTED IDIOM — what the fixed tool card now tells agents
        #    to write on this platform
        good_t = (f'findstr /C:"WDLIVE-READY" "{marker}"' if WIN
                  else f'grep -F WDLIVE-READY "{marker}"')
        # ② THE IDIOM THE OLD CARD PRODUCED — the one that killed three dogs
        bad_t = f'cat "{marker}" | grep WDLIVE-READY'
        good = o.watchdog_create("k", "good", "command", good_t,
                                 "WDLIVE-READY", 15)
        bad = o.watchdog_create("k", "bad", "command", bad_t,
                                "WDLIVE-READY", 15)
        store.save_org(o)
        # the engine's pool WITHOUT its scanner thread: a background loop
        # would keep walking every org in this root for the rest of the run
        supervisor._wd_cmd_pool = ThreadPoolExecutor(max_workers=2)
        # the service's PATH, for both dogs, in the same tick — the whole
        # comparison is worthless if they run in different environments
        os.environ["PATH"] = (os.pathsep.join([
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                         "system32"),
            os.environ.get("SystemRoot", r"C:\Windows")])
            if WIN else "/nonexistent")
        supervisor._wd_tick()
        deadline = time.time() + 90
        while time.time() < deadline:
            o2 = store.load_org(slug)
            if all(int(o2._watchdog(x["id"]).get("checks_run") or 0) >= 1
                   for x in (good, bad)):
                break
            time.sleep(0.5)
        o2 = store.load_org(slug)
        g, b = o2._watchdog(good["id"]), o2._watchdog(bad["id"])

        # ①  SEEN TO FIRE
        assert int(g.get("checks_run") or 0) >= 1, \
            f"the good dog never ran a check at all: {g!r}"
        assert int(g.get("fired") or 0) >= 1, (
            "THE DOCUMENTED IDIOM DID NOT FIRE. This is the check that "
            f"decides whether the fix is real. dog={g!r}")
        assert "WDLIVE-READY" in str(g.get("last_output") or ""), \
            f"the fire carried no evidence: {g.get('last_output')!r}"
        assert supervisor.wd_list_row(g)["health"] == "ok", \
            f"a firing dog was flagged unhealthy: {supervisor.wd_list_row(g)}"
        assert any(w[1] == "k" and w[3] for w in _no_deploy.WAKES), (
            "the dog fired but nothing woke its owner — the mail path is "
            f"the whole point. WAKES={_no_deploy.WAKES!r}")

        # ②  SEEN TO BE REPORTED, not silently armed
        assert int(b.get("checks_run") or 0) >= 1, \
            f"the bad dog never ran a check: {b!r}"
        assert int(b.get("fired") or 0) == 0, \
            "the bash-idiom dog fired — the premise of this fix is wrong"
        row = supervisor.wd_list_row(b)
        if WIN:
            assert supervisor.wd_output_broken(str(b.get("last_output"))), (
                "the bash-idiom dog matched nothing AND left no evidence why "
                f"— exactly the old silent death. last_output="
                f"{b.get('last_output')!r}")
            assert "BROKEN" in row["health"], (
                "`list` would still show this dog as an ordinary armed dog "
                f"waiting patiently: {row['health']!r}")
        # ③  and the two are DISTINGUISHABLE — the entire defect in one line
        assert row["health"] != supervisor.wd_list_row(g)["health"], (
            "a working dog and a permanently-broken one still report the "
            "same health — nothing has actually been fixed")
    finally:
        os.environ["PATH"] = real_path
        try:
            supervisor._wd_cmd_pool.shutdown(wait=True)
        except Exception:                                    # noqa: BLE001
            pass
        supervisor._wd_cmd_pool = real_pool
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(o.d["slug"])
        except Exception:                                    # noqa: BLE001
            pass
    assert not _no_deploy.turn_spawn_blocked(), \
        "the turn-spawn interlock was left installed for later suites"


check("E2E: one real tick — the DOCUMENTED idiom fires and wakes its owner; "
      "the bash idiom is reported BROKEN (control pair)",
      _one_real_tick_fires_the_good_dog_and_diagnoses_the_bad_one)


# ---------------------------------------------------------------------------
print(f"\n{PASS} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
    sys.exit(1)
