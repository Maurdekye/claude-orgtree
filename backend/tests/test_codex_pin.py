"""The Codex CLI pin: the upgrade decision, and the deploy sections that run it.

    python backend/tests/test_codex_pin.py

No npm, no network, no install. `codexpin.decide` is the whole rule and it is
pure, so it is tested directly; the two update scripts are checked as TEXT for
the specific hazards that actually bit, and each of those checks is run against
a deliberately-broken copy to prove it can fail.

WHAT THIS DEFENDS (2026-09-04). Nothing in this repo refreshed `<data>/codex`:
no codex step in update.ps1, update.sh or tools/install-autostart.ps1. The pin
sat at 0.150.1 from 28 August. OpenAI gates rollout models on the reporting
client version, so that CLI was never offered `gpt-6-astra` and the tier was
invisible — while the refusal blamed the account.
"""

import os
import re
import json
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-codexpin-")

from orgtree import codexpin  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


# ── the decision ───────────────────────────────────────────────────────────

def the_regression_pair_upgrades():
    """THE CASE THAT COST THE DAY: 0.150.1 installed, 0.153.3 pinned."""
    d = codexpin.decide("0.150.1", "0.153.3")
    assert d["action"] == "upgrade", d
    assert "0.150.1" in d["reason"] and "0.153.3" in d["reason"], d


def absent_or_unreadable_installs():
    for have in (None, "", "unknown", "not-a-version", "  "):
        d = codexpin.decide(have, "0.153.3")
        assert d["action"] == "install", (have, d)


def equal_keeps():
    d = codexpin.decide("0.153.3", "0.153.3")
    assert d["action"] == "keep", d
    assert "already current" in d["reason"], d


def newer_is_never_rolled_back():
    """THE FLOOR, from the side that matters. An operator who installed ahead
    of us did so on purpose; a deploy that silently downgrades a machine is
    worse than one that says nothing."""
    for have in ("0.153.4", "0.154.0", "1.0.0"):
        d = codexpin.decide(have, "0.153.3")
        assert d["action"] == "keep", (have, d)
        assert "NEWER" in d["reason"], (have, d)


def a_platform_suffix_is_not_older():
    """The pin's own package.json carries `0.153.3-win32-x64`. Read as a
    different version it would reinstall on EVERY deploy, forever."""
    d = codexpin.decide("0.153.3-win32-x64", "0.153.3")
    assert d["action"] == "keep", d


def an_unreadable_floor_does_nothing():
    """The only branch about OUR data being broken. It must NOT fall through
    to an install — guessing a version is how a machine ends up running one
    thing and reporting another."""
    for floor in ("", "latest", "unknown", None):
        d = codexpin.decide("0.150.1", floor)  # type: ignore[arg-type]
        assert d["action"] == "unknown", (floor, d)
        assert d["action"] != "install", (floor, d)


def version_parsing_is_digits_only():
    assert codexpin.ver_tuple("0.153.3-win32-x64") == (0, 153, 3)
    assert codexpin.ver_tuple("0.153.3") == (0, 153, 3)
    assert codexpin.ver_tuple("") == (0, 0, 0)
    assert codexpin.ver_tuple("nonsense") == (0, 0, 0)
    # …and `parses` is what stops that (0,0,0) authorising an upgrade
    assert codexpin.parses("0.1.2") and codexpin.parses("v1.2")
    assert not codexpin.parses("") and not codexpin.parses("latest")
    assert not codexpin.parses(None)


# ── the shipped constants ──────────────────────────────────────────────────

def the_shipped_pin_is_new_enough_to_be_offered_rollout_models():
    """The whole point of moving the pin. The floor we ship must be at least
    the oldest version MEASURED to be offered `gpt-6-astra`; shipping one
    below it would re-create the exact bug this section exists to fix."""
    assert codexpin.parses(codexpin.PIN), codexpin.PIN
    assert (codexpin.ver_tuple(codexpin.PIN)
            >= codexpin.ver_tuple(codexpin.ROLLOUT_OBSERVED_PRESENT)), (
        codexpin.PIN, codexpin.ROLLOUT_OBSERVED_PRESENT)
    # and the bracket is a bracket, in the right order
    assert (codexpin.ver_tuple(codexpin.ROLLOUT_OBSERVED_ABSENT)
            < codexpin.ver_tuple(codexpin.ROLLOUT_OBSERVED_PRESENT))
    assert codexpin.PACKAGE == "@openai/codex", codexpin.PACKAGE


def _env_without_data_root():
    """The real environment minus ORGTREE_DATA — so the child proves it needs
    no data root, while keeping what the interpreter itself needs to boot
    (a PATH-only env fails `_Py_HashRandomization_Init` on Windows, which
    would have made this check pass for the wrong reason if it were inverted).
    """
    env = dict(os.environ)
    env.pop("ORGTREE_DATA", None)
    return env


def codexpin_imports_nothing():
    """Both update scripts import this at a point where NOTHING ELSE about the
    install is known to be healthy — before pip has necessarily succeeded. It
    must not drag in the package. Same contract as `clipin`."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]);"
         "from orgtree import codexpin;"
         "bad=[m for m in ('orgtree.store','orgtree.ledger','orgtree.providers',"
         "'orgtree.supervisor','orgtree.appsettings') if m in sys.modules];"
         "print('LEAKED:'+','.join(bad) if bad else 'clean');"
         "print(codexpin.PIN)",
         os.path.join(ROOT, "backend")],
        capture_output=True, text=True, env=_env_without_data_root())
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.splitlines()[0] == "clean", out.stdout
    assert out.stdout.splitlines()[1].strip() == codexpin.PIN, out.stdout


# ── the deploy sections ────────────────────────────────────────────────────

def _section(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i) + len(end)]


def ps1_section():
    t = open(os.path.join(ROOT, "update.ps1"), encoding="utf-8").read()
    return _section(t, "# -- 4c - the Codex CLI pin",
                    "}   # end -not $EnsureUp (section 4c)")


def sh_section():
    t = open(os.path.join(ROOT, "update.sh"), encoding="utf-8").read()
    return _section(t, "# -- 4c - the Codex CLI pin",
                    'mkdir -p "$DATA_ROOT"')


def _npm_lines(section):
    """The LOGICAL lines that actually RUN npm — not comments quoting a command.

    ⚠ Continuations are joined first. Both scripts wrap the npm invocation
    (PowerShell with a trailing backtick, sh with a trailing backslash), so a
    naive line scan sees only `npm install --prefix ...` and misses the flags
    on the next line — which would have made every flag assertion below pass
    vacuously on a command that carried none of them.
    """
    joined, buf = [], ""
    for ln in section.splitlines():
        stripped = ln.rstrip()
        if stripped.endswith("`") or stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        joined.append(buf + ln)
        buf = ""
    if buf:
        joined.append(buf)
    # must START the statement: both sections also PRINT an npm command in the
    # by-hand retry hint, and one of those deliberately shows the BROKEN bare
    # form. Matching anywhere would test the hint text instead of the command.
    return [ln for ln in joined if re.match(r"\s*npm install\s", ln)]


#: each: (name, predicate over a section, a mutation that must break it)
SECTION_RULES = [
    ("installs an EXPLICIT @version (the caret trap)",
     lambda s: all("@$" in ln or "@$CDX_WANT" in ln or "@$cdxWant" in ln
                   for ln in _npm_lines(s)) and _npm_lines(s),
     lambda s: s.replace("@$cdxWant", "").replace("@$CDX_WANT", "")),
    ("passes --save-exact so the caret is rewritten",
     lambda s: all("--save-exact" in ln for ln in _npm_lines(s)) and _npm_lines(s),
     lambda s: s.replace("--save-exact", "")),
    ("never aborts the deploy",
     lambda s: not re.search(r"^\s*(exit 1|die )", s, re.M),
     lambda s: s + "\nexit 1\n"),
    ("retypes no version literal — the floor comes from codexpin",
     lambda s: not re.search(r"['\"]?\b0\.\d+\.\d+\b", _no_comments(s)),
     lambda s: s.replace("$cdxWant", "0.153.3").replace("$CDX_WANT", "0.153.3")),
    ("reads the decision from codexpin, not from shell logic",
     lambda s: "codexpin" in s and "decide" in s,
     lambda s: s.replace("codexpin", "xxx")),
]


def _no_comments(s):
    out = []
    for ln in s.splitlines():
        st = ln.lstrip()
        if st.startswith("#"):
            continue
        out.append(ln)
    return "\n".join(out)


def both_scripts_obey_every_rule():
    for who, sect in (("update.ps1", ps1_section()), ("update.sh", sh_section())):
        for name, ok, _break in SECTION_RULES:
            assert ok(sect), f"{who}: FAILS — {name}"


def every_section_rule_can_fail():
    """POSITIVE CONTROL. A rule that cannot fail is not a rule: break the
    section on purpose and require each check to notice."""
    for who, sect in (("update.ps1", ps1_section()), ("update.sh", sh_section())):
        for name, ok, brk in SECTION_RULES:
            mutated = brk(sect)
            assert mutated != sect, f"{who}: mutation for {name!r} changed nothing"
            assert not ok(mutated), (
                f"{who}: rule {name!r} STILL PASSED on a broken section — it is inert")


def the_ps1_section_is_scoped_off_the_ensure_up_leg():
    """⚠ -EnsureUp is the 5-minute crash-restart net. An unscoped upgrade would
    try to reinstall the CLI every five minutes forever. update.sh has no
    -EnsureUp leg, so only the ps1 needs this."""
    s = ps1_section()
    assert "if (-not $EnsureUp) {" in s, s[:300]
    assert s.rstrip().endswith("}   # end -not $EnsureUp (section 4c)")
    sh = open(os.path.join(ROOT, "update.sh"), encoding="utf-8").read()
    assert "EnsureUp" not in _no_comments(sh), "update.sh grew an EnsureUp leg"


def the_section_runs_after_the_stop_and_before_the_start():
    """The only safe window: a live app-server holds codex.exe open on Windows,
    so an in-place npm install fails on the one file the upgrade is about."""
    t = open(os.path.join(ROOT, "update.ps1"), encoding="utf-8").read()
    stop = t.index("stopping old backend")
    sect = t.index("# -- 4c - the Codex CLI pin")
    start = t.index("Start-Process -FilePath $py")
    assert stop < sect < start, (stop, sect, start)
    b = open(os.path.join(ROOT, "update.sh"), encoding="utf-8").read()
    assert b.index("# -- 4b") < b.index("# -- 4c - the Codex CLI pin") \
        < b.index('OUT="$DATA_ROOT/backend.log"')


# ── the sections RUN, not just read ────────────────────────────────────────
# Text checks cannot tell whether the branches route correctly. These extract
# the SHIPPED section out of the SHIPPED script, supply the variables the
# surrounding deploy would have set, stub npm, and run it.
#
# ⚠ THEY DECLARE THEMSELVES INERT rather than passing when the shell is not
# available. A check that silently turns into a no-op on someone else's
# machine is the thing this whole suite exists to prevent.

INERT = []


def inert(label, why):
    INERT.append(label)
    print(f"  --   {label}  [INERT: {why}]")


def _long(path):
    """Expand a Windows 8.3 short path. `tempfile` hands back
    `C:\\Users\\NCOLA_~1\\...` on this host and Git Bash cannot open a script
    through it — it reports "No such file or directory" for a file that plainly
    exists, which reads like a broken script rather than a broken path."""
    if os.name != "nt" or "~" not in path:
        return path
    import ctypes
    buf = ctypes.create_unicode_buffer(4096)
    if ctypes.windll.kernel32.GetLongPathNameW(path, buf, 4096):  # type: ignore[attr-defined]
        return buf.value
    return path


def _fixture(installed, npm_ok, windows, exe=None):
    """`windows` selects the STUB FORM (npm.cmd for PowerShell, an `npm` shell
    script for bash) — NOT the binary name. Under Git Bash on Windows the sh
    leg needs a POSIX stub but the pin still ships `codex.exe`; conflating the
    two made the sh scenarios find no npm at all and report an empty argv."""
    """A throwaway data root with a pin at `installed`, and an npm stub that
    logs its argv and either simulates a real successful install — package.json
    AND the platform-specific native binary, which is what the verify step
    actually looks for — or fails."""
    d = _long(tempfile.mkdtemp(prefix="cdxsec-"))
    data = os.path.join(d, "data")
    pkg = os.path.join(data, "codex", "node_modules", "@openai", "codex")
    native = os.path.join(data, "codex", "node_modules", "@openai",
                          "codex-win32-x64", "vendor", "x", "bin")
    os.makedirs(pkg)
    if installed:
        with open(os.path.join(pkg, "package.json"), "w") as fh:
            json.dump({"name": "@openai/codex", "version": installed}, fh)
    log = os.path.join(d, "argv.log")
    binp = os.path.join(d, "bin")
    os.makedirs(binp)
    exe = exe or ("codex.exe" if os.name == "nt" else "codex")
    py = sys.executable
    ok_body = (
        f'"{py}" -c "import json,os;'
        f"os.makedirs(r'{pkg}',exist_ok=True);"
        f"json.dump({{'name':'@openai/codex','version':'0.153.3'}},"
        f"open(os.path.join(r'{pkg}','package.json'),'w'));"
        f"os.makedirs(r'{native}',exist_ok=True);"
        f"open(os.path.join(r'{native}','{exe}'),'wb').close()\"")
    if windows:
        body = (ok_body + "\nexit /b 0") if npm_ok else \
            "echo npm: simulated failure 1>&2\nexit /b 1"
        with open(os.path.join(binp, "npm.cmd"), "w") as fh:
            fh.write(f'@echo off\necho %* >> "{log}"\n{body}\n')
    else:
        body = (ok_body + "\nexit 0") if npm_ok else \
            'echo "npm: simulated failure" >&2\nexit 1'
        p = os.path.join(binp, "npm")
        with open(p, "w", newline="\n") as fh:
            fh.write(f'#!/bin/sh\necho "$@" >> "{log}"\n{body}\n')
        os.chmod(p, 0o755)
    return d, data, binp, log


def _env(binp):
    env = dict(os.environ)
    env["PATH"] = binp + os.pathsep + env["PATH"]
    env.pop("ORGTREE_CODEX", None)
    return env


def run_ps1(installed, npm_ok):
    d, data, binp, log = _fixture(installed, npm_ok, windows=True)
    h = os.path.join(d, "run.ps1")
    with open(h, "w", encoding="utf-8") as fh:
        fh.write(f"$ErrorActionPreference='Continue'\n$EnsureUp=$false\n"
                 f"$root='{ROOT}'\n$dataRoot='{data}'\n$py='{sys.executable}'\n"
                 f"$env:ORGTREE_CODEX=$null\n{ps1_section()}\n"
                 f'Write-Host "SECTION-COMPLETED"\n')
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", h], capture_output=True, text=True,
                       env=_env(binp), timeout=300)
    return r, (open(log).read().strip() if os.path.exists(log) else "")


def run_sh(installed, npm_ok):
    d, data, binp, log = _fixture(installed, npm_ok, windows=False)
    h = os.path.join(d, "run.sh")
    fwd = data.replace("\\", "/")
    with open(h, "w", newline="\n", encoding="utf-8") as fh:
        fh.write(f"#!/bin/bash\nset -u\nPY='{sys.executable.replace(chr(92), '/')}'\n"
                 f"ROOT='{ROOT.replace(chr(92), '/')}'\nDATA_ROOT='{fwd}'\n"
                 f"RED=''; OFF=''\nnote() {{ echo \"NOTE: $*\"; }}\n"
                 f"good() {{ echo \"GOOD: $*\"; }}\ndie() {{ echo \"DIE: $*\"; exit 9; }}\n"
                 f"unset ORGTREE_CODEX\n{sh_section_body()}\n"
                 f'echo "SECTION-COMPLETED"\n')
    arg = h
    if os.name == "nt":
        arg = h.replace("\\", "/")
        if len(arg) > 2 and arg[1] == ":":
            arg = "/" + arg[0].lower() + arg[2:]
    r = subprocess.run([_bash(), arg], capture_output=True, text=True,
                       env=_env(binp), timeout=300)
    return r, (open(log).read().strip() if os.path.exists(log) else "")


def _bash():
    """⚠ `bash` on PATH resolves to Git\\usr\\bin\\bash.EXE from Python on
    Windows — the MSYS binary WITHOUT the wrapper that mounts the MSYS root, so
    it cannot resolve `/c/...` and reports "No such file or directory" for a
    file that exists. Git\\bin\\bash.exe is the wrapper."""
    wrapper = r"C:\Program Files\Git\bin\bash.exe"
    if os.name == "nt" and os.path.exists(wrapper):
        return wrapper
    return "bash"


def sh_section_body():
    """The sh section WITHOUT the trailing `mkdir` line the extractor uses as
    its end marker (that line belongs to the next step, not to 4c)."""
    t = open(os.path.join(ROOT, "update.sh"), encoding="utf-8").read()
    i = t.index("# -- 4c - the Codex CLI pin")
    return t[i:t.index('mkdir -p "$DATA_ROOT"', i)]


SCENARIOS = [
    # installed, npm_ok, must_call_npm, needles
    ("an old pin upgrades with an explicit @version and --save-exact",
     "0.150.1", True, True, ["now 0.153.3"]),
    ("an already-current pin never calls npm", "0.153.3", True, False,
     ["already current"]),
    ("a NEWER pin is left alone and never calls npm", "0.154.0", True, False,
     ["NEWER"]),
    ("an npm failure warns loudly and does NOT abort the deploy",
     "0.150.1", False, True, ["could NOT be updated", "retry by hand"]),
]


def _drive(runner, who):
    for label, installed, npm_ok, calls, needles in SCENARIOS:
        r, argv = runner(installed, npm_ok)
        assert "SECTION-COMPLETED" in r.stdout, (
            f"{who}/{label}: section did not finish — {r.stdout[-300:]}"
            f" ERR {r.stderr[-300:]}")
        assert r.returncode == 0, f"{who}/{label}: aborted rc={r.returncode}"
        if calls:
            assert "@openai/codex@0.153.3" in argv, f"{who}/{label}: argv={argv!r}"
            assert "--save-exact" in argv, f"{who}/{label}: argv={argv!r}"
            assert "--prefix" in argv, f"{who}/{label}: argv={argv!r}"
        else:
            assert argv == "", f"{who}/{label}: npm WAS called — {argv!r}"
        for n in needles:
            assert n in r.stdout, f"{who}/{label}: missing {n!r} in {r.stdout[-300:]}"


def ps1_section_behaves():
    _drive(run_ps1, "update.ps1")


def sh_section_behaves():
    _drive(run_sh, "update.sh")


for label, fn in [
    ("0.150.1 installed against a 0.153.3 pin → upgrade", the_regression_pair_upgrades),
    ("absent or unreadable → install", absent_or_unreadable_installs),
    ("equal → keep", equal_keeps),
    ("NEWER is never rolled back", newer_is_never_rolled_back),
    ("a -win32-x64 suffix does not reinstall every deploy",
     a_platform_suffix_is_not_older),
    ("an unreadable FLOOR does nothing, never installs",
     an_unreadable_floor_does_nothing),
    ("version parsing is digits-only and `parses` gates it",
     version_parsing_is_digits_only),
    ("the shipped PIN is >= the version measured to carry astra",
     the_shipped_pin_is_new_enough_to_be_offered_rollout_models),
    ("codexpin imports nothing from the package", codexpin_imports_nothing),
    ("both deploy sections obey every rule", both_scripts_obey_every_rule),
    ("…and every rule can actually fail (positive control)",
     every_section_rule_can_fail),
    ("the ps1 section is off the -EnsureUp leg",
     the_ps1_section_is_scoped_off_the_ensure_up_leg),
    ("the section sits between the stop and the start",
     the_section_runs_after_the_stop_and_before_the_start),
]:
    check(label, fn)

# the two that need a real shell, each declaring itself rather than vanishing
if os.name != "nt":
    inert("update.ps1 section: all four scenarios",
          "PowerShell is a Windows leg; not exercised here")
elif not shutil.which("powershell"):
    inert("update.ps1 section: all four scenarios", "powershell not on PATH")
else:
    check("update.ps1 section: all four scenarios RUN correctly", ps1_section_behaves)

if not (os.path.exists(r"C:\Program Files\Git\bin\bash.exe")
        or shutil.which("bash")):
    inert("update.sh section: all four scenarios", "no bash available")
else:
    check("update.sh section: all four scenarios RUN correctly", sh_section_behaves)

print(f"\nall {PASS} checks passed"
      + (f", {len(INERT)} DECLARED INERT: {'; '.join(INERT)}" if INERT else ""))
