"""The autostart tasks' SHAPE — a source contract over the installer.

The Windows autostart pair (`orgtree-deploy` at logon, `orgtree-ensure` every
five minutes) is registered by `tools/install-autostart.ps1`. Two of its
properties are load-bearing in ways nothing else in the tree can catch:

  * the actions run through `conhost.exe --headless`, so the five-minute
    watchdog does not paint a console window on the interactive desktop
    (user directive 2026-08-05, after an evening of one popping up every few
    minutes — a7567b9). A bare `-Hidden` only shortens the paint to a flash,
    so BOTH layers have to stay;
  * the principal stays `Interactive`. S4U looks like the tidier fix and
    silently lands the relaunched backend — with its docker and Claude-CLI
    children — in session 0, off the desktop and away from the profile whose
    `~/.claude` credentials every turn reads. That failure is invisible until
    a turn fails "confusingly late", which is exactly how the original
    LocalSystem version of this bug presented.

Neither can be tested by running anything: executing the installer would
register real scheduled tasks on the developer's machine, and observing the
window is a human sitting and watching for fifteen minutes. So this is a
DRIFT GUARD in the sense `msgvis.py` uses the phrase — it reads the source and
asserts the contract the shipped behaviour rests on, and it fails loudly when
the source moves and this file's model of it does not.

Hermetic and platform-neutral: it reads one text file. Nothing is registered,
nothing is executed, no PowerShell is involved.

    python backend/tests/test_autostart.py
"""

from __future__ import annotations

import os
import re
import sys
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
SCRIPT = os.path.join(_REPO, "tools", "install-autostart.ps1")

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


def source() -> str:
    with open(SCRIPT, encoding="utf-8") as f:
        return f.read()


def code() -> str:
    """The script with comment lines dropped. Every contract below is stated
    in a comment RIGHT NEXT to the line that implements it — including the
    S4U warning — so a guard that reads the raw text passes on the prose
    alone. (§11 of the net-transport suite learned this the expensive way: a
    guard tripped on the commit's own explanatory comment, and its sibling
    kept passing while asserting a defect that had already been fixed.)"""
    return "\n".join(ln for ln in source().splitlines()
                     if not ln.lstrip().startswith("#"))


def logical() -> str:
    """…and with PowerShell's backtick line-continuations folded away. Every
    statement this file reasons about is written across two or three lines, so
    a per-line regex would silently match nothing and pass. (It did, on the
    first run of this suite — which is the argument for asserting the shapes
    are FOUND, not merely that the bad ones are absent.)"""
    return re.sub(r"`\r?\n\s*", " ", code())


#: a PowerShell double-quoted string body, tolerating the `" escape inside it
_PS_STR = r'"((?:[^"`]|`.)*)"'


# ══════════════════════════════════════════════════════════════════════════ §1

def sec_windowless() -> None:
    print("\n§1  the actions run windowless")

    def _actions_go_through_conhost():
        c = logical()
        acts = re.findall(r"New-ScheduledTaskAction\s+-Execute\s+(\S+)", c)
        assert len(acts) == 2, f"expected two task actions, found {acts}"
        assert all(a == "$conhost" for a in acts), (
            f"an action executes {acts} directly instead of hosting it on a "
            f"windowless pseudoconsole — a task with an InteractiveToken "
            f"principal paints a real console window every time it fires, "
            f"and the watchdog fires every five minutes")
        assert re.search(r"\$conhost\s*=\s*Join-Path\s+\$env:SystemRoot\s+"
                         r"['\"]System32.conhost\.exe['\"]", c), (
            "the conhost path is no longer resolved from %SystemRoot%")
    check("windowless · both task actions launch through conhost, not "
          "powershell directly", _actions_go_through_conhost)

    def _both_actions_pass_headless():
        args = re.findall(r"New-ScheduledTaskAction[^\n]*?-Argument\s+" + _PS_STR,
                          logical())
        assert len(args) == 2, f"could not read both -Argument strings: {args}"
        for a in args:
            assert a.lstrip().startswith("--headless"), (
                f"an action hosts on conhost but never asks for a headless "
                f"pseudoconsole, so the window comes back: {a!r}")
    check("windowless · and each one actually passes --headless (hosting on "
          "conhost without it changes nothing)", _both_actions_pass_headless)

    def _settings_still_hidden():
        m = re.search(r"New-ScheduledTaskSettingsSet[^\n]*", logical())
        assert m, "the settings set is gone"
        s = m.group(0)
        assert "-Hidden" in s, (
            "the task settings dropped -Hidden. conhost --headless carries "
            "most of the fix, but Task Scheduler paints its own window "
            "without this — both layers were needed, and neither is "
            "redundant")
        assert "ExecutionTimeLimit" in s and "Zero" in s, (
            "the 3-day execution stop limit came back: an unattended backend "
            "is killed silently on day three")
        assert "IgnoreNew" in s, (
            "MultipleInstances is no longer IgnoreNew — overlapping "
            "five-minute firings would stack update.ps1 runs")
    check("windowless · the settings set keeps -Hidden beside the limits it "
          "already carried", _settings_still_hidden)

    def _the_updater_path_stays_quoted():
        """The wrapper interposes an extra argv layer, and the updater path is
        a user-chosen location. An unquoted -File breaks on any install under
        a directory with a space — on some machines only, which is the worst
        kind."""
        found = re.findall(r"-Argument\s+" + _PS_STR, logical())
        assert len(found) == 2, f"could not read both arguments: {found}"
        for a in found:
            assert re.search(r"-File\s+`\"\$updater`\"", a), (
                f"the updater path is not quoted inside the argument: {a!r}")
    check("windowless · the updater path is still quoted inside the wrapped "
          "argument (a path with a space must not split)",
          _the_updater_path_stays_quoted)


# ══════════════════════════════════════════════════════════════════════════ §2

def sec_principal() -> None:
    print("\n§2  who the tasks run as")

    def _principal_is_interactive():
        m = re.search(r"New-ScheduledTaskPrincipal[^\n]*", logical())
        assert m, "the principal is gone"
        p = m.group(0)
        assert "-LogonType Interactive" in p, (
            f"the principal is no longer Interactive: {p!r}")
        for banned in ("S4U", "ServiceAccount", "Password", "-RunLevel Highest"):
            assert banned not in p, (
                f"the principal gained {banned!r}. S4U/ServiceAccount run the "
                f"task in session 0, where the relaunched backend and every "
                f"docker/CLI child it spawns lose the user's desktop AND the "
                f"profile whose ~/.claude credentials each turn reads — the "
                f"turns then fail confusingly late, which is the original "
                f"LocalSystem bug this file exists to keep buried")
        assert "$env:USERNAME" in p, "the principal stopped naming the user"
    check("drift guard · the principal is Interactive and never S4U, so the "
          "backend keeps the user's session and profile",
          _principal_is_interactive)

    def _registration_overwrites():
        """The installer is the fix's delivery mechanism: if a re-run created
        tasks only when absent, patching this script would leave every
        existing install on the old windowed shape forever."""
        regs = re.findall(r"Register-ScheduledTask -TaskName[^\n]*", logical())
        assert len(regs) == 2, f"expected two registrations, found {len(regs)}"
        for r in regs:
            assert "-Force" in r, (
                "a registration lost -Force — re-running the installer would "
                "no longer update an existing task, so a shipped fix could "
                "never reach a machine that already has the tasks")
    check("principal · re-running the installer OVERWRITES both tasks, which "
          "is how a fix reaches an existing install", _registration_overwrites)

    def _uninstall_still_removes_both():
        m = re.search(r"foreach \(\$t in ([^)]*)\)", logical())
        assert m and "-deploy" in m.group(1) and "-ensure" in m.group(1), (
            "the uninstall path no longer names both tasks — a removed "
            "install would leave the five-minute watchdog running forever")
    check("principal · and -Uninstall still removes both tasks, not just one",
          _uninstall_still_removes_both)


# ═════════════════════════════════════════════════════════════════════════ main

def main() -> int:
    print("═══ autostart task shape — a source contract over the installer ═══")
    assert os.path.exists(SCRIPT), f"installer not found at {SCRIPT}"
    sec_windowless()
    sec_principal()
    print(f"\nautostart: {PASS} checks passed · {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
