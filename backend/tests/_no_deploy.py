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
