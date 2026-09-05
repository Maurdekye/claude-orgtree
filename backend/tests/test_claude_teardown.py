"""The CLAUDE leg's turn teardown: what it publishes, and when.

    python backend/tests/test_claude_teardown.py    (no pytest; plain asserts)

Hermetic and IN PROCESS: drives `supervisor._run_one_turn` directly against
real org docs on disk, with the CLI resolved (via ORGTREE_CLAUDE_CLI) to
tests/fakecli.js. Every case gets its own org, and every lifecycle transition
is recorded together with `proc.poll()` TAKEN AT THE SAME INSTANT — so "was
the process alive when the record said it had died" is an observation, not a
race against the fixture.

WHAT THIS LEG IS, because it is not shaped like the codex one:
  · the lifecycle token is `wp_turn or proc` — the WarmProc when the turn was
    served warm, otherwise the Popen. Not a turn object.
  · the clear already sits in a `finally` that covers the whole stdout loop,
    so the codex defect (a raising teardown skipping the clear) never applied.
  · what did apply, measured on d73ecbd before the fix and kept as
    evidence/claude-lifecycle-before-d73ecbd.json:
      §2 an exception in the stdout loop left the CLI RUNNING and the leg
         published `live=False` anyway — poll() was None at the call and the
         process was still alive after the turn;
      §3 a raise in the cleanup skipped the FAIL-LOUD orphan sweep, which is
         the "agent waits forever on a dead subagent" incident of 2026-08-20
         reachable from two lines above it;
      §4 a raise in the FIRST cleanup call also left `st["proc"]` pointing at
         a process that had already exited — the handle ⏸ acts on.

ANTI-VACUITY: §1 is the control pair (an ordinary clear is visible; a parked
process keeps its record and its owner), §3 has its own unplanted control
proving the orphan instrument reports at all, and every planted case asserts
that its plant actually FIRED.
"""

import json
import os
import subprocess                                            # noqa: F401
import sys
import tempfile
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RIG = tempfile.mkdtemp(prefix="orgtree-clteardown-")
HOME = os.path.join(RIG, "home")
os.makedirs(HOME, exist_ok=True)
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKECLI = os.path.join(BACKEND, "tests", "fakecli.js")
CFG = os.path.join(RIG, "fakecli.json")

os.environ["ORGTREE_DATA"] = RIG            # BEFORE any orgtree import
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
os.environ["ORGTREE_CLAUDE_CLI"] = FAKECLI
os.environ["FAKECLI_CONFIG"] = CFG
os.environ["ORGTREE_TURN_IDLE"] = "60"
os.environ["ORGTREE_WARM"] = "1"
os.environ["ORGTREE_WARM_POLL"] = "3600"
# a PORT NOBODY SERVES: this rig runs no backend, and left unset the tool
# dispatcher would default to 7360 and reach the operator's live deployment
os.environ["ORGTREE_PORT"] = "9"
os.environ.pop("ORGTREE_STEER_HOOK", None)
sys.path.insert(0, BACKEND)
with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import store, supervisor as S, warmpool as W     # noqa: E402
from orgtree.ledger import USER                               # noqa: E402

S.stream = lambda slug, nid, payload: None
W._FLAG_TTL = 0.5

PASS = 0
FAIL: list[tuple[str, str]] = []
FAST = {"echoMs": 20, "firstEventMs": 40, "resultMs": 10}
SEQ = [0]


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception:                                         # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def set_cfg(node: str, cfg: dict) -> None:
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump({"default": dict(cfg), node: dict(cfg)}, f)


def mkorg(node: str) -> str:
    org = store.create_org(f"zz clteardown {node}")
    org.hire(USER, None, "haiku", 5, node, add_dirs=[],
             tools={"bash": False, "web": False, "edit": False,
                    "subagents": False, "mcp": []},
             org_visibility="team", charter="a claude teardown test agent")
    store.save_org(org)
    return org.d["slug"]


def one_turn(*, warm: bool = False, cfg: dict | None = None,
             plant: str | None = None,
             kill_noop: bool = False) -> dict:
    """Run ONE turn in its own org and report what the teardown left behind.

    `plant` is where the fault goes:
      "stream"  — one raise from inside the stdout loop, while the process is
                  still running and its stdin still open: the leg's own orphan
                  comment calls this "the stdout loop left by some other door"
      "end"     — `_mcp_tool_count_end` raises (it does real work: a poll, a
                  notify, a journal write) — between the clear and FAIL LOUD
      "surface" — the FIRST cleanup call raises, so everything after it in the
                  same block is at risk
    """
    SEQ[0] += 1
    node = f"cl{SEQ[0]}"
    os.environ["ORGTREE_WARM"] = "1" if warm else "0"
    slug = mkorg(node)
    set_cfg(node, {**FAST, **(cfg or {})})

    calls: list[dict] = []
    orphaned: list[tuple] = []
    procs: list = []
    fired = [0]
    killed: list[int] = []
    orig_life, orig_orphan = W._set_proc_lifecycle, S._bg_orphaned
    orig_end, orig_surface = S._mcp_tool_count_end, S._mcp_tool_surface_for_owner
    orig_stream = S.stream
    orig_kill = S._wd_kill_tree

    def spy_life(slug_, nid_, *, live, relaunch=False, reason=None,
                 owner=None, adopt=False):
        if slug_ == slug:
            p = getattr(owner, "proc", owner)
            try:
                rc = p.poll()
            except Exception:                                 # noqa: BLE001
                rc = "unpollable"
            if p not in procs:
                procs.append(p)
            calls.append({"live": bool(live), "owner": type(owner).__name__,
                          "rc_at_call": rc})
        return orig_life(slug_, nid_, live=live, relaunch=relaunch,
                         reason=reason, owner=owner, adopt=adopt)

    def spy_orphan(slug_, nid_, orphans, why, sid=None):
        if slug_ == slug:
            orphaned.append((len(orphans), str(why)))
        return orig_orphan(slug_, nid_, orphans, why, sid=sid)

    def raising_end(slug_, nid_, owner, reason="no live provider process"):
        if slug_ == slug:
            fired[0] += 1
            raise RuntimeError("planted: _mcp_tool_count_end raised")
        return orig_end(slug_, nid_, owner, reason)

    def raising_surface(slug_, nid_, owner):
        if slug_ == slug:
            fired[0] += 1
            raise RuntimeError("planted: _mcp_tool_surface_for_owner raised")
        return orig_surface(slug_, nid_, owner)

    def raising_stream(slug_, nid_, payload):
        if slug_ == slug and not fired[0] and str(
                payload.get("kind") or "") in ("text", "delta", "thought"):
            fired[0] += 1
            S.stream = orig_stream      # exactly one, and never in the finally
            raise RuntimeError("planted: stream() raised inside the loop")
        return None

    W._set_proc_lifecycle = spy_life
    S._bg_orphaned = spy_orphan
    if plant == "end":
        S._mcp_tool_count_end = raising_end
    if plant == "surface":
        S._mcp_tool_surface_for_owner = raising_surface
    if plant == "stream":
        S.stream = raising_stream
    if kill_noop:
        # A TREE THAT WILL NOT DIE. The teardown's kill is bounded
        # (5s) and this makes it miss, which is the only way to
        # reach the publish gate with the process still running.
        S._wd_kill_tree = lambda *a, **k: killed.append(1)
    err = ""
    try:
        if warm:
            W.keeper_pass_now()
        st = S.state(slug, node)
        with S._state_lock:
            st["busy"] = True
        S._run_one_turn(slug, node, {"text": "an ordinary turn",
                                     "view": "an ordinary turn"})
    except Exception as exc:                                  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    finally:
        W._set_proc_lifecycle = orig_life
        S._bg_orphaned = orig_orphan
        S._mcp_tool_count_end = orig_end
        S._mcp_tool_surface_for_owner = orig_surface
        S.stream = orig_stream
        S._wd_kill_tree = orig_kill

    st_ = S.state(slug, node)
    owner_after = st_.get("proc_lifecycle_owner")
    with W._pool_lock:
        pooled = W._pool.get((slug, node))
    out = {
        "slug": slug, "nid": node, "fired": fired[0], "calls": calls,
        "live_after": bool(st_.get("proc_live")),
        "owner_after": owner_after,
        "st_proc_after": st_.get("proc"),
        "pooled": pooled,
        # taken BEFORE this fixture's own cleanup — which kills what the turn
        # left running, and would otherwise answer its own question
        "pooled_alive": bool(pooled.alive()) if pooled is not None else None,
        "rc_after": [p.poll() for p in procs],
        "orphaned": orphaned,
        "kill_calls": len(killed),
        "raised": err,
    }
    for p in procs:                       # never leak a fixture's own process
        try:
            if pooled is not None and p is pooled.proc:
                continue                  # parked on purpose; kill_org has it
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)
        except Exception:                                     # noqa: BLE001
            pass
    try:
        W.kill_org(slug, "suite-teardown")
    except Exception:                                         # noqa: BLE001
        pass
    time.sleep(0.15)
    return out


def clears(r: dict) -> list[dict]:
    return [c for c in r["calls"] if not c["live"]]


def main() -> int:
    print("§1 the controls: what an ordinary turn leaves behind")

    def t1():
        r = one_turn()
        eq(([c["live"] for c in r["calls"]], r["live_after"],
             r["owner_after"], r["st_proc_after"]),
           ([True, False], False, None, None),
           "an ordinary cold turn raises the record and clears it")
        cl = clears(r)
        assert cl and cl[0]["rc_at_call"] is not None, (
            f"the clear was published without an observed exit: {cl}")
        assert r["rc_after"] and r["rc_after"][0] is not None, (
            "the fixture's process outlived an ordinary turn")
    check("an ordinary cold turn clears the record, on an exit it observed",
          t1)

    def t2():
        r = one_turn(warm=True)
        assert r["pooled"] is not None, (
            "the fixture did not park — every claim below would be vacuous")
        eq((r["live_after"], r["owner_after"] is r["pooled"],
            r["pooled_alive"], [c["live"] for c in r["calls"]]),
           (True, True, True, [c["live"] for c in r["calls"]]),
           "a parked process keeps the record and owns it")
        assert not clears(r), (
            f"a parked turn published a clear: {clears(r)}")
    check("a parked warm process keeps the record, owns it, and is alive", t2)

    print("§2 an exception in the stdout loop, with the process still running")
    # MEASURED on d73ecbd: the clear went out with poll() None at the call and
    # the CLI was STILL ALIVE after the turn — a death that had not happened,
    # and a process nothing would ever reap.

    def t3():
        r = one_turn(plant="stream")
        eq(r["fired"], 1, "the planted stream() raise never fired")
        cl = clears(r)
        assert cl, "nothing cleared the record at all"
        assert cl[0]["rc_at_call"] is not None, (
            "the leg published `live=False` for a process that had NOT "
            f"exited (poll() at the call: {cl[0]['rc_at_call']!r})")
        eq((r["live_after"], r["owner_after"],
            r["rc_after"][0] is not None),
           (False, None, True),
           "after a loop exception the process is ended and the record clear")
    check("a stdout-loop exception ends the process, then publishes the exit "
          "it observed", t3)

    print("§3 FAIL LOUD survives a cleanup that raises")

    def t4():
        r = one_turn(cfg={"bgTasks": 1, "bgQuit": True})
        eq([n for n, _why in r["orphaned"]], [1],
           "the control did not report its orphan — §3's instrument is blind")
    check("control · a turn whose process dies holding a live background "
          "task reports it", t4)

    def t5():
        r = one_turn(cfg={"bgTasks": 1, "bgQuit": True}, plant="end")
        eq(r["fired"], 1, "the planted _mcp_tool_count_end raise never fired")
        eq([n for n, _why in r["orphaned"]], [1],
           "the orphan sweep was skipped by a raise two lines above it — "
           "this is the 2026-08-20 hang: the agent is never told its "
           "background child died")
    check("the orphan sweep still fires when the lifecycle accounting raises",
          t5)

    # ONE turn, TWO claims, kept as separate checks so a mutant that breaks
    # only one of them cannot hide behind the other's failure.
    surfaced: dict = {}

    def t6():
        surfaced.update(one_turn(cfg={"bgTasks": 1, "bgQuit": True},
                                 plant="surface"))
        eq(surfaced["fired"], 1, "the planted surface raise never fired")
        eq((surfaced["st_proc_after"],
            [n for n, _w in surfaced["orphaned"]]), (None, [1]),
           "a raise in the FIRST cleanup call left the process handle "
           "published and the orphan sweep unrun")
    check("a raise in the first cleanup call still clears the process handle "
          "and still fires the sweep", t6)

    def t7():
        assert surfaced, "the previous check never ran its turn"
        eq((surfaced["live_after"], surfaced["owner_after"]), (False, None),
           "the lifecycle record was left standing when the capture beside "
           "it raised")
    check("…and the lifecycle record is still cleared on that same exit", t7)

    print("§4 the other half of truthful: an exit nobody observed is never "
          "published")
    # The kill above is BOUNDED, so it can miss — and then the honest answer
    # is the one `_mcp_tool_count_end` gives for the tool surface: say
    # nothing, keep the generation owned. Without this check the publish gate
    # is untestable in this rig (the fake always dies), which is exactly the
    # "present, plausible and inert" shape the charter warns about: the
    # `an-unobserved-exit-is-published-anyway` mutant SURVIVED a round before
    # this check existed.

    def t8():
        r = one_turn(plant="stream", kill_noop=True)
        eq((r["fired"], r["kill_calls"] > 0), (1, True),
           "the fixture never reached the teardown's kill")
        assert not clears(r), (
            f"an exit was published for a process that never exited: "
            f"{clears(r)}")
        eq((r["live_after"], r["owner_after"] is not None), (True, True),
           "a process that outlived the bounded kill must stay live AND "
           "owned — nothing else will ever correct a cold generation")
    check("a process that outlives the teardown's bounded kill keeps the "
          "record, owned", t8)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"FAILED: {label}\n{tb}")
        print(f"{PASS} passed, {len(FAIL)} FAILED")
        return 1
    print(f"{PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
