"""A channel closing is not a process ending — and something must still end it.

    python backend/tests/test_mcp_lifecycle_finalize.py

`_mcp_tool_count_end` refuses to reap a generation whose exit it has not
observed, and points at the lifecycle owner as the thing that later confirms
one. That confirmation existed on exactly ONE path — a serving turn waits
(`supervisor.py:9766`/`:9770`) and ends (`:9787`). For a PARKED process it did
not exist at all: `warmpool._pump_out` called `_on_proc_exit` once, at stdout
EOF, and nothing ever observed the death that followed. So the compliant
`loading` published at EOF was terminal: a dead Popen stayed the owner and its
count and names stayed published, indefinitely.

These tests drive the REAL objects — a real `subprocess.Popen`, a real
`WarmProc` with its real pump thread, and `warmpool`'s real teardown callers.
No test here calls `_mcp_tool_count_end` by hand; a hand-driven "second end"
proves only that the accounting function works when someone calls it twice,
which was never in doubt and is precisely what hid this defect.

ANTI-VACUITY IS ENFORCED IN THE TEST, NOT ASSERTED IN PROSE. The child closes
fd 1 with `os.close(1)`, not `sys.stdout.close()`. Measured on this host:
`sys.stdout.close()` left the parent's EOF read BLOCKED for the child's whole
1.5s sleep — it unblocked at process exit, and `poll()` then returned None only
transiently, so a test written that way can pass on the exit/poll race alone
while never once observing a channel closed on a live process. `os.close(1)`
produced EOF in 0.031s with `poll()` None. Every test below re-checks
`poll() is None` at the moment it observes the parked state, and fails if the
child got there by dying.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

TMP = tempfile.mkdtemp(prefix="orgtree-mcp-finalize-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(TMP, "data")
os.makedirs(HOME, exist_ok=True)
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["USERPROFILE"] = HOME
os.environ["HOME"] = HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree import store, supervisor as S, warmpool as W  # noqa: E402
from orgtree.ledger import USER  # noqa: E402

TOOLS = ["mcp__orgtree__orgtree_message", "mcp__orgtree__orgtree_status"]
NEW_TOOLS = ["mcp__other__alpha", "mcp__other__beta", "mcp__other__gamma"]

# The child holds the process open this long AFTER closing its output handle.
# Only the EOF-observation window has to fit inside it, and that window was
# measured at 31ms; the margin is for a loaded machine, not for correctness.
LIVE_S = 4.0

# stdin is the barrier: the parent registers the WarmProc in the pool FIRST,
# then releases the child. Without it the pump could reach EOF before the
# registry knows the process exists, and the test would be measuring a
# different code path than the one it names.
CHILD = (
    "import os,sys,time\n"
    "sys.stdin.readline()\n"
    "os.close(1)\n"
    f"time.sleep({LIVE_S})\n"
    "os._exit(0)\n"          # deterministic: no interpreter-shutdown flush
)                            # onto the fd we just closed
SLEEPER = "import time\ntime.sleep(120)\n"


def _spawn(code: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace")


def _wait_for(pred, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _snap(slug: str, nid: str) -> dict:
    st = S.state(slug, nid)
    with S._state_lock:
        names = st.get("mcp_tool_names")
        return {
            "owner": st.get("mcp_tool_owner"),
            "count": st.get("mcp_tool_count"),
            "names": sorted(names) if isinstance(names, set) else None,
            "state": st.get("mcp_readiness_state"),
            "waiting": bool(st.get("mcp_readiness_waiting")),
            "proc_live": st.get("proc_live"),
            "last_turn": st.get("last_turn_mcp_tool_count"),
        }


class LifecycleFinalizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        org = store.create_org("zz mcp finalize")
        for nid in ("parked", "supersede", "killed", "parkback", "durable"):
            org.hire(USER, None, "haiku", 5, nid)
        store.save_org(org)
        cls.slug = org.d["slug"]

    def setUp(self) -> None:
        self._saved_stream = S.stream
        S.stream = lambda slug, nid, payload: None
        self._procs: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        S.stream = self._saved_stream
        with W._pool_lock:
            W._pool.clear()
            W._serving.clear()
        for p in self._procs:
            try:
                p.kill()
            except Exception:                           # noqa: BLE001
                pass

    def _own(self, code: str) -> subprocess.Popen[str]:
        p = _spawn(code)
        self._procs.append(p)
        return p

    def _adopt(self, nid: str, proc, tools=TOOLS) -> None:
        S._mcp_tool_count_begin(self.slug, nid, proc, "claude",
                                "system/init.tools", "starting", None)
        S._mcp_tool_count_names(self.slug, nid, proc, tools, "claude",
                                "system/init.tools")

    # ── 1. the whole transition, on a real parked process ──────────────────
    def test_parked_channel_eof_holds_loading_then_confirms_the_exit(self):
        nid = "parked"
        proc = self._own(CHILD)
        self._adopt(nid, proc)
        wp = W.WarmProc(self.slug, nid, proc, "sid-1", "hash-1", "env-1")
        with W._pool_lock:
            W._pool[(self.slug, nid)] = wp
        W._set_proc_lifecycle(self.slug, nid, live=True, owner=wp, adopt=True)

        before = _snap(self.slug, nid)
        self.assertEqual(before["count"], len(TOOLS), before)
        self.assertIs(before["owner"], proc, before)

        proc.stdin.write("go\n")                        # release the child
        proc.stdin.flush()

        # the channel-EOF half: the seat is freed, and that is observable
        self.assertTrue(
            _wait_for(lambda: (self.slug, nid) not in W._pool),
            "the pump never reached EOF; the test proved nothing")

        # ── ANTI-VACUITY. Everything below describes a process the OS is
        # still running. If the child died to produce that EOF, this test is
        # not about channel-closed-while-live and must fail rather than pass.
        self.assertIsNone(proc.poll(),
                          "child exited before EOF was observed — vacuous")
        eof = _snap(self.slug, nid)
        self.assertIsNone(proc.poll(),
                          "child exited while the state was read — vacuous")

        # G1: an OS-live process is LOADING, with its surface intact.
        self.assertEqual(eof["state"], "loading", eof)
        self.assertFalse(eof["waiting"], eof)
        self.assertIs(eof["owner"], proc, eof)
        self.assertEqual(eof["count"], len(TOOLS), eof)
        self.assertEqual(eof["names"], sorted(TOOLS), eof)
        # …and the LIFECYCLE agrees with it. `_on_proc_exit` used to journal
        # the exit and set proc_live=False in this same call, so the two
        # layers contradicted each other about a process both could see.
        self.assertIs(eof["proc_live"], True, eof)
        self.assertFalse(wp.exit_journaled,
                         "an exit was journaled for a process that is alive")

        # ── the confirmed-exit half. NOTHING is called by hand here: the
        # child exits on its own, and the pump thread's own wait() observes
        # it. This is the transition that did not exist.
        self.assertTrue(
            _wait_for(lambda: _snap(self.slug, nid)["state"] == "process-ended",
                      LIVE_S + 15.0),
            f"dead owner never finalized: {_snap(self.slug, nid)}")
        end = _snap(self.slug, nid)
        self.assertIsNone(end["owner"], end)
        self.assertIsNone(end["count"], end)
        self.assertIsNone(end["names"], end)
        self.assertFalse(end["waiting"], end)
        self.assertIs(end["proc_live"], False, end)
        self.assertTrue(wp.exit_journaled, "no exit row for a real death")
        # it died on its own: nothing in the readiness layer killed it
        self.assertEqual(proc.poll(), 0, "the child was not left to exit")

    # ── 2. a late predecessor may not clear its successor ──────────────────
    def test_a_replacement_supersedes_and_the_late_finalizer_cannot_clear_it(self):
        nid = "supersede"
        proc = self._own(CHILD)
        self._adopt(nid, proc)
        wp = W.WarmProc(self.slug, nid, proc, "sid-2", "hash-2", "env-2")
        with W._pool_lock:
            W._pool[(self.slug, nid)] = wp
        W._set_proc_lifecycle(self.slug, nid, live=True, owner=wp, adopt=True)
        proc.stdin.write("go\n")
        proc.stdin.flush()
        self.assertTrue(_wait_for(lambda: (self.slug, nid) not in W._pool))
        self.assertIsNone(proc.poll(), "vacuous: child died before EOF")
        self.assertEqual(_snap(self.slug, nid)["state"], "loading")

        # a replacement generation takes the seat while the predecessor is
        # still alive with a channel-closed surface
        newer = self._own(SLEEPER)
        self._adopt(nid, newer, NEW_TOOLS)
        mid = _snap(self.slug, nid)
        self.assertIs(mid["owner"], newer, mid)
        self.assertEqual(mid["names"], sorted(NEW_TOOLS), mid)

        # …and now the predecessor really dies. Its finalizer runs.
        self.assertTrue(
            _wait_for(lambda: wp.exit_journaled, LIVE_S + 15.0),
            "the predecessor's finalizer never ran")
        self.assertTrue(_wait_for(lambda: proc.poll() is not None, 10.0))
        time.sleep(0.2)                     # let any late publish land
        after = _snap(self.slug, nid)
        self.assertIs(after["owner"], newer, after)
        self.assertEqual(after["count"], len(NEW_TOOLS), after)
        self.assertEqual(after["names"], sorted(NEW_TOOLS), after)
        self.assertNotEqual(after["state"], "process-ended", after)

    # ── 3. a deliberate kill must observe the corpse it made ───────────────
    def test_kill_node_publishes_process_ended_and_not_loading(self):
        """`kill_node` drops the pool entry BEFORE it kills, so the pump's EOF
        callback finds the generation untracked and no later observer closes
        it out. Under poll-based `_end` an unreaped kill therefore published
        `loading` — for a process it had just destroyed — and left it there.

        The kill is patched to return BEFORE the OS has reaped, which is the
        documented behaviour of the real one (`_wd_kill_tree` returns as soon
        as taskkill/kill is issued) made deterministic rather than raced.
        """
        nid = "killed"
        proc = self._own(SLEEPER)
        self._adopt(nid, proc)
        wp = W.WarmProc(self.slug, nid, proc, "sid-3", "hash-3", "env-3")
        with W._pool_lock:
            W._pool[(self.slug, nid)] = wp
        W._set_proc_lifecycle(self.slug, nid, live=True, owner=wp, adopt=True)

        saved = S._wd_kill_tree
        S._wd_kill_tree = lambda p: threading.Timer(0.4, p.kill).start()
        try:
            self.assertTrue(W.kill_node(self.slug, nid, "retired"))
        finally:
            S._wd_kill_tree = saved

        end = _snap(self.slug, nid)
        self.assertEqual(end["state"], "process-ended", end)
        self.assertIsNone(end["owner"], end)
        self.assertIsNone(end["count"], end)
        self.assertIs(end["proc_live"], False, end)

    # ── 4. the survivor of a double-spawn keeps a surface ──────────────────
    def test_park_back_hands_mcp_ownership_to_the_surviving_process(self):
        """The hire-kickoff race can double-spawn a seat. The second spawn
        adopted the MCP surface last, so it OWNS it — and `park_back` kills it
        in favour of the process that just ran a turn. Killing the owner
        publishes `process-ended` and clears the surface, which would leave the
        surviving, OS-LIVE, parked process with no MCP state at all.
        """
        nid = "parkback"
        winner = self._own(SLEEPER)
        loser = self._own(SLEEPER)
        wwp = W.WarmProc(self.slug, nid, winner, "sid-w", "hash-w", "env-w")
        lwp = W.WarmProc(self.slug, nid, loser, "sid-l", "hash-l", "env-l")
        wwp.claimed = True
        self._adopt(nid, winner)                # the turn's process, first…
        self._adopt(nid, loser, NEW_TOOLS)      # …the keeper's, second: owner
        st = S.state(self.slug, nid)
        with S._state_lock:
            st["last_turn_mcp_tool_count"] = 7
        with W._pool_lock:
            W._serving[(self.slug, nid)] = wwp
            W._pool[(self.slug, nid)] = lwp
        self.assertIs(_snap(self.slug, nid)["owner"], loser)

        saved = W.warm_enabled
        W.warm_enabled = lambda: True
        try:
            self.assertTrue(W.park_back(wwp, 0.0))
        finally:
            W.warm_enabled = saved

        after = _snap(self.slug, nid)
        self.assertIs(after["owner"], winner, after)
        self.assertIsNone(after["count"], after)        # not the corpse's
        self.assertIsNone(after["names"], after)        # inventory, ever
        self.assertEqual(after["state"], "initializing", after)
        self.assertIs(winner.poll(), None, "the survivor was killed")
        # the durable measured-earlier value is not collateral damage
        self.assertEqual(after["last_turn"], 7, after)

        # and the loser's own finalizer, when it lands, cannot take the seat
        # back off the survivor
        self.assertTrue(_wait_for(lambda: lwp.exit_journaled, 15.0))
        time.sleep(0.2)
        final = _snap(self.slug, nid)
        self.assertIs(final["owner"], winner, final)
        self.assertEqual(final["state"], "initializing", final)


    # ── 5. re-adopting a survivor must not erase what it measured ──────────
    def test_reclaim_does_not_erase_the_survivors_durable_surface(self):
        """`park_back` runs BEFORE the turn boundary reads the surface
        (`supervisor.py:7718`→`:7740`, `:9734`→`:9773`), and the boundary does
        not treat `(None, None)` as "no news" — `:11509` POPS
        `last_turn_mcp_tools`. So re-adopting the survivor through
        `_mcp_tool_count_begin`, which clears count and names by design, would
        make the double-spawn race ERASE a baseline the survivor had actually
        measured. Its own stash is still keyed to it, and unobserved must not
        erase observed.
        """
        nid = "durable"
        winner = self._own(SLEEPER)
        loser = self._own(SLEEPER)
        wwp = W.WarmProc(self.slug, nid, winner, "sid-dw", "hash-dw", "env-dw")
        lwp = W.WarmProc(self.slug, nid, loser, "sid-dl", "hash-dl", "env-dl")
        wwp.claimed = True
        self._adopt(nid, winner)            # publishes, so the stash is ITS
        # the keeper's second spawn adopts the seat but never gets far enough
        # to publish an inventory — the stash therefore still belongs to the
        # process that is about to survive
        S._mcp_tool_count_begin(self.slug, nid, loser, "claude",
                                "system/init.tools", "starting", None)
        with W._pool_lock:
            W._serving[(self.slug, nid)] = wwp
            W._pool[(self.slug, nid)] = lwp
        self.assertIs(_snap(self.slug, nid)["owner"], loser)

        saved = W.warm_enabled
        W.warm_enabled = lambda: True
        try:
            self.assertTrue(W.park_back(wwp, 0.0))
        finally:
            W.warm_enabled = saved

        after = _snap(self.slug, nid)
        self.assertIs(after["owner"], winner, after)
        self.assertIsNone(after["count"], after)        # the LIVE entry is
        self.assertIsNone(after["names"], after)        # honestly unknown…
        # …and the turn boundary still reads the surface this process
        # measured, instead of popping the durable record
        count, exact = S._mcp_tool_surface_for_owner(self.slug, nid, winner)
        self.assertEqual(count, len(TOOLS))
        self.assertEqual(exact, sorted(TOOLS))
        self.assertIsNotNone(lwp)                       # (kept alive for kill)


if __name__ == "__main__":
    unittest.main(verbosity=2)
