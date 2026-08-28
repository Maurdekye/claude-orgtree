"""☠ THE DEPLOY INTERLOCK — shared by every suite that can reach
`supervisor.launch_self_restart`.

WHY THIS EXISTS, measured 2026-08-21
------------------------------------
`launch_self_restart` spawns a REAL `update.ps1` / `update.sh`: a full rebuild
and a restart of the backend serving EVERY org on this machine. The mailhub
leg additionally runs a real `docker compose up -d --build` against the
machine's hub container.

Two suites reach it, and both did so for months without anyone intending it:

  * `test_turn_lifecycle.py` — `_org_target_refuses_while_busy` calls the
    launch and does NOT stub the spawn, on the argument that the mid-turn
    refusal fires first. Mutating that refusal away sent it straight through
    to a live spawn.
  * `test_mcptool.py` — the argument-fuzz checks call EVERY card in the
    catalogue, including this one, as a TOP-LEVEL node whose authorization
    gate therefore PASSES. `orgtree-mcptest-*/self-update-*.log` files going
    back months are the receipts: every run reached the launch and spawned
    powershell.

Nothing was ever deployed by a test run, but never once by design — only
because `update.ps1` refused on its own account: a dirty working tree, a
branch with no upstream, or (before D-142) the `-OnlyIfBehind` flag exiting
before the rebuild. D-142 removed that last accident, which is what turned a
long-standing latent hazard into a live one: on a CLEAN, up-to-date checkout
the script now prints "redeploying anyway" and proceeds to
`Stop-Process` the backend on port 7360.

⚠ It is worse than "the suite restarts the backend". The suites set
`ORGTREE_DATA` to a throwaway temp dir, which `update.ps1` inherits — so it
finds no `.port` file there, falls back to the DEFAULT port 7360, kills the
production backend, and then brings a replacement up against the empty temp
data root on the test's own port. The suite goes green while every org on the
machine goes down.

WHAT THIS DOES
--------------
Every spawn goes through here. One whose argv names a deploy is RECORDED and
DROPPED — never executed. Anything else passes through to the real
implementation untouched, because `test_turn_lifecycle`'s console-output check
must still spawn a genuine child (that check is the only reason a Windows
self-restart logs anything at all).

It does NOT raise: these calls arrive through the real `/api/agent` handler,
and an exception there would surface as a 500 and fail unrelated fuzz checks
that legitimately expect a clean response. Refusing silently and RECORDING is
what keeps the interlock invisible to every check except the ones that
deliberately assert on it.

⚠ Do not "simplify" this by stubbing `_detached_spawn` wholesale, and do not
copy it into a suite — import it, so the two callers cannot drift apart.
"""
from __future__ import annotations

import os

from orgtree import supervisor

# argv fragments that mean "this would deploy something"
_DEPLOY_ARGV = ("update.ps1", "update.sh", "update.cmd",
                "docker compose", "docker-compose")

#: every deploy this interlock refused, argv by argv, in order
ATTEMPTS: list[list[str]] = []

_REAL_DETACHED_SPAWN = supervisor._detached_spawn


class _RefusedChild:
    """What a refused deploy hands back (D-142/a).

    ⚠ NOT `None`, and this is a correctness fix rather than a convenience.
    `_detached_spawn` now returns a handle so the deploy window can watch the
    child and release held turns when it exits. If refusal kept returning
    `None`, then in the suites — where refusal is the ONLY path the deploy
    checks ever take — `_arm_deploy_window` would take its "nothing was
    spawned" early return and THE WATCHER WOULD NEVER RUN UNDER TEST. Green
    suite, unexercised production path: the precise shape this directory
    keeps getting caught by.

    An already-exited handle is not a fiction to satisfy a test, either. It
    faithfully models the COMMON production case: `update.ps1` has ten
    failure exits before its `Stop-Process`, so "the deploy child exits
    without restarting us" is the ordinary outcome, not the rare one.
    """
    returncode = 0
    pid = None

    def wait(self, timeout=None):        # noqa: ARG002 — mirrors Popen
        return 0

    def poll(self):
        return 0


def _interlock(args, cwd, logpath, env=None):
    if any(x in " ".join(args).lower() for x in _DEPLOY_ARGV):
        ATTEMPTS.append(list(args))
        return _RefusedChild()           # ☠ refused: nothing is spawned
    return _REAL_DETACHED_SPAWN(args, cwd, logpath, env)


def install() -> None:
    """Arm the interlock. Call once, immediately after importing supervisor
    and BEFORE any check runs."""
    supervisor._detached_spawn = _interlock       # type: ignore[assignment]


def installed() -> bool:
    """True while the interlock is the live spawn seam. A check that swaps
    `_detached_spawn` out and fails to restore it re-arms the gun, so the
    suites assert this rather than assuming it."""
    return supervisor._detached_spawn is _interlock


# ---------------------------------------------------------------------------
# ☠ THE PRODUCTION DATA-ROOT INTERLOCK (added 2026-08-22, watchdog work)
# ---------------------------------------------------------------------------
# The deploy interlock above guards the one spawn that restarts the machine.
# It does NOT guard the other half of "no test may touch production": the
# real data root, `~/orgtree`, where every live org's doc, every real agent's
# scratch, and every ARMED WATCHDOG lives.
#
# Watchdog work is unusually exposed to this. `supervisor._wd_tick()` walks
# `store.list_orgs()` and RUNS every armed dog it finds; `_wd_fire` writes
# mail into a real agent's inbox and can WAKE it. A suite that exercised the
# engine against the live root would arm and fire production dogs, drive real
# turns, and bill them — and it would look exactly like a passing test.
#
# The existing convention is `os.environ["ORGTREE_DATA"] = mkdtemp()` before
# importing orgtree. A convention is what this directory keeps getting caught
# by, so this makes it a check with teeth: it reads `store.DATA_ROOT` — the
# value the code ACTUALLY resolved, not the env var someone believes they set
# — and refuses to let the suite continue if it is the live root.
_LIVE_ROOT = os.path.realpath(os.path.expanduser("~/orgtree"))


def assert_isolated_data_root() -> None:
    """Refuse to run against the machine's live data root. Call once, right
    after importing orgtree, in any suite that can reach the ledger or the
    watchdog engine.

    Reads the RESOLVED `store.DATA_ROOT` rather than `os.environ` on purpose:
    `store` reads the env var at import time, so a suite that sets
    ORGTREE_DATA *after* its first orgtree import has an env var that says
    "isolated" and a module that is pointed at production. That gap is
    precisely the abstention shape — the check would pass while the thing it
    checks is false."""
    from orgtree import store                                # noqa: PLC0415
    root = os.path.realpath(store.DATA_ROOT)
    if root == _LIVE_ROOT or root.startswith(_LIVE_ROOT + os.sep):
        raise SystemExit(
            f"☠ REFUSING TO RUN: store.DATA_ROOT resolved to {root!r}, which "
            f"is the machine's LIVE data root ({_LIVE_ROOT!r}). This suite "
            f"reaches the ledger and the watchdog engine — running it here "
            f"would arm and fire production watchdogs, write into real "
            f"agents' mailboxes and wake real (billed) turns. Set "
            f"ORGTREE_DATA to a temp dir BEFORE the first orgtree import.")


def data_root_isolated() -> bool:
    """The predicate behind `assert_isolated_data_root`, for the check that
    mutation-verifies the interlock itself."""
    try:
        assert_isolated_data_root()
    except SystemExit:
        return False
    return True


# ---------------------------------------------------------------------------
# ☠ THE TURN-SPAWN INTERLOCK (added 2026-08-22, watchdog work)
# ---------------------------------------------------------------------------
# `supervisor.send_message(..., wake=True)` DRIVES A NODE: it starts a real
# `claude -p` process and bills a real turn. The watchdog engine calls it on
# every fire (`_wd_fire`), so any check that exercises the engine end to end
# — which is the only honest way to prove a dog FIRES — reaches it.
#
# Opt-in, not automatic: suites that mean to drive real turns
# (test_turn_lifecycle) must keep doing so. Arm it in suites that do not.
#: every wake this interlock intercepted: (slug, nid, text, wake)
WAKES: list[tuple[str, str, str, bool]] = []
_REAL_SEND_MESSAGE = supervisor.send_message


def _no_wake(slug, nid, text, command=False, wake=True, **kw):  # noqa: ANN001,FBT002
    WAKES.append((slug, nid, text, bool(wake)))
    return {"intercepted": True}


def install_no_turn_spawn() -> None:
    """Arm it. Recording rather than raising, for the same reason the deploy
    interlock records: the point is to make the fire path RUNNABLE under test,
    and a check can then assert on `WAKES` — a positive marker that the wake
    really was reached — instead of asserting the absence of a process it
    cannot see."""
    supervisor.send_message = _no_wake                    # type: ignore[assignment]


def turn_spawn_blocked() -> bool:
    return supervisor.send_message is _no_wake
