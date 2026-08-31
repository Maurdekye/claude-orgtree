"""D-209: a usage limit on a NON-claude lane freezes the agent instead of
ending its turn in silence.

    python backend/tests/test_provider_limit_freeze.py   (no pytest)

THE DEFECT THIS PINS, in the words of the user who reported it: "codex agents
hitting usage limits don't get the normal turn refusal error; they just stop."
The observable was exactly that. The cause was not what it looked like.

codex-cli 0.150.1 has NO `turn/failed` notification — the literal string does
not occur anywhere in the binary — so a failed turn arrives as `turn/completed`
carrying `turn.status = "failed"` and a `turn.error`. `codexrun` mapped every
status except "interrupted" to COMPLETED, so a wall was booked as a SUCCESSFUL
turn: normal tokens, normal cost, no error row, no freeze, no auto-resume.
Measured on cache-structural at 2026-08-30T22:41:41Z; the agent was silent for
9h47m until a person noticed. The CLI had told us everything — a message, the
machine tag `usage_limit_exceeded`, and a `resetsAt` on the rate-limit
notification 298 ms earlier. All three were discarded.

WHAT IS MEASURED AND WHAT IS NOT — read this before trusting a green run:

  · the CODEX half is transcribed from captured bytes. `fakecodex`'s
    `usage_limit` scenario replays the real ending: the exhausted bucket, the
    second empty bucket after it, and a `turn/completed` whose status is
    "failed", with nothing on stderr.
  · the GEMINI half is BY CONSTRUCTION. No gemini usage wall has been observed
    on this machine. §5 proves the shared seam freezes a gemini-lane failure
    whose text names a limit; it does NOT prove that a real gemini limit
    arrives wearing that text. That gap is deliberate and stated, not closed
    by inventing a recording.

Anti-vacuity: `tests/_mutate_provider_limit.py` breaks the shipped code six
ways and requires a NAMED check here to go red for each. A suite that would
pass with the detection removed is the exact failure mode this org keeps
finding in its own instruments.
"""

import json
import os
import sys
import tempfile
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-provlimit-")
os.environ["ORGTREE_DATA"] = DATA
# a PORT NOBODY SERVES — this rig runs no backend, and the default 7360 would
# send a test's tool traffic to the operator's LIVE deployment
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

HERE = os.path.dirname(os.path.abspath(__file__))
FAKECODEX = os.path.join(HERE, "fakecodex.py")
FAKEGEMINI = os.path.join(HERE, "fakegemini.py")
CODEX_HOME = tempfile.mkdtemp(prefix="provlimit-chome-")
GEMINI_HOME = tempfile.mkdtemp(prefix="provlimit-ghome-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME
os.environ["ORGTREE_GEMINI"] = FAKEGEMINI
os.environ["ORGTREE_GEMINI_HOME"] = GEMINI_HOME

with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')
with open(os.path.join(GEMINI_HOME, "settings.json"), "w",
          encoding="utf-8") as _f:
    json.dump({"security": {"auth": {"selectedType": "gemini-api-key"}}}, _f)

from orgtree import codex_limits, codexrun, store, supervisor      # noqa: E402
from orgtree import limits                                         # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402
import fakecodex                                                   # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []

STREAMED: list[dict] = []
supervisor.stream = lambda slug, nid, payload: STREAMED.append(dict(payload))
NOTIFIED: list[tuple[str, str, str]] = []
supervisor.notify = lambda slug, nid, event: NOTIFIED.append((slug, nid, event))
supervisor.CODEX_STEER_POLL = 0.2


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def mkorg(label: str, tier: str = "sol") -> tuple[str, str]:
    org = store.create_org(f"zz provlimit {label}")
    r = org.hire(USER, None, tier, 2, "px", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a provider limit test agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def run_turn(slug: str, nid: str, text):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(slug, nid, text)


def node_doc(slug: str, nid: str) -> dict:
    return store.load_org(slug).node(nid)


def err_rows(slug: str, nid: str) -> list:
    return store.load_org(slug).d.get("turn_error_log", {}).get(nid, [])


def main() -> int:
    # ───────────────────────────────────────────────────────────────────────
    print("§1 codexrun reads what the app-server actually says")

    def t_status():
        # the four members of codex's TurnStatus enum, verbatim
        eq(codexrun._status_of("completed"), codexrun.STATUS_COMPLETED,
           "completed")
        eq(codexrun._status_of("interrupted"), codexrun.STATUS_INTERRUPTED,
           "interrupted")
        # ⚠ THE ONE THAT WAS WRONG. "failed" used to normalize to COMPLETED,
        # which is the entire silent stop.
        eq(codexrun._status_of("failed"), codexrun.STATUS_FAILED, "failed")
        eq(codexrun._status_of("inProgress"), codexrun.STATUS_FAILED,
           "a turn that never finished is not a success")
        eq(codexrun._status_of("something-new-upstream"),
           codexrun.STATUS_FAILED, "an unknown status is not a success")
        # a MISSING status still completes — older/none-conforming servers
        eq(codexrun._status_of(""), codexrun.STATUS_COMPLETED, "absent status")
    check("§1 TurnStatus: failed/inProgress/unknown are NOT completed", t_status)

    def t_errtext():
        blob = codexrun.error_text({"message": fakecodex.LIMIT_MESSAGE,
                                    "codexErrorInfo": "usage_limit_exceeded",
                                    "additionalDetails": None})
        assert fakecodex.LIMIT_MESSAGE in blob, blob
        assert "usage_limit_exceeded" in blob, blob
        # the tagged-object form, should the protocol send one
        assert "sandbox_error" in codexrun.error_text(
            {"message": "nope", "codexErrorInfo": {"type": "sandbox_error"}})
        # nothing to say → empty, never None and never a crash
        eq(codexrun.error_text(None), "", "no error object")
        eq(codexrun.error_text({}), "", "empty error object")
        eq(codexrun.error_text("a string"), "", "a non-dict error")
    check("§1 error_text keeps BOTH the CLI's message and its machine tag",
          t_errtext)

    def t_reset():
        now = time.time()
        board = {
            # the exhausted bucket — the only one describing this wall
            "codex": {"limitId": "codex",
                      "primary": {"usedPercent": 100.0,
                                  "windowDurationMins": 10080,
                                  "resetsAt": now + 500_000},
                      "secondary": None},
            # …and the empty one that arrived AFTER it in the real capture
            "premium": {"limitId": "premium", "primary": None,
                        "secondary": None},
        }
        eq(codexrun.limit_reset_epoch(board), now + 500_000,
           "the exhausted window's own reset")
        # a window with room left is NOT the wall, however soon it resets
        roomy = {"codex": {"primary": {"usedPercent": 40.0,
                                       "resetsAt": now + 10}}}
        eq(codexrun.limit_reset_epoch(roomy), None,
           "a window that is not exhausted answers for nothing")
        # soonest among several exhausted windows
        two = {"a": {"primary": {"usedPercent": 100.0,
                                 "resetsAt": now + 9000}},
               "b": {"secondary": {"usedPercent": 100.0,
                                   "resetsAt": now + 400}}}
        eq(codexrun.limit_reset_epoch(two), now + 400, "soonest exhausted")
        eq(codexrun.limit_reset_epoch(None), None, "no board at all")
        eq(codexrun.limit_reset_epoch({"x": "not a dict"}), None, "junk board")
    check("§1 limit_reset_epoch reads the EXHAUSTED window across the whole "
          "board, not the last snapshot", t_reset)

    def t_band():
        now = 1_000_000.0
        # a machine reset inside the horizon wins outright
        ts, src = supervisor._provider_limit_until(
            fakecodex.LIMIT_MESSAGE, now + 500_000, now)
        eq((ts, src), (now + 500_000, "provider"), "provider value taken")
        # …but it is BANDED like any other number: a reshaped field pointing
        # years out must not park an agent for years
        ts, src = supervisor._provider_limit_until(
            fakecodex.LIMIT_MESSAGE, now + 400 * 86400, now)
        eq(src, "probe", "an absurd machine reset is refused")
        assert abs(ts - (now + supervisor.PROBE_FLOOR)) < 1.0, ts
        # a reset already past is refused too
        _ts, src = supervisor._provider_limit_until(
            fakecodex.LIMIT_MESSAGE, now - 10, now)
        eq(src, "probe", "a reset in the past is refused")
        # with no machine value, prose answers when it can
        ts, src = supervisor._provider_limit_until(
            "You've hit your usage limit. try again in 20 minutes", None, now)
        eq(src, "text", "prose parsed")
        assert ts > now, ts
        # …and the measured codex wording, which NO parser here can read,
        # falls honestly to the probe floor rather than inventing a time
        _ts, src = supervisor._provider_limit_until(
            fakecodex.LIMIT_MESSAGE, None, now)
        eq(src, "probe",
           "the real codex prose names a date no parser reads — say so")
    check("§1 the reset ladder: provider value, banded; then prose; then the "
          "5-minute probe floor", t_band)

    # ───────────────────────────────────────────────────────────────────────
    print("§2 the codex wall, end to end (replayed from captured bytes)")
    os.environ["FAKECODEX_SCENARIO"] = "usage_limit"
    slug, nid = mkorg("codexwall")
    t_start = time.time()

    def t_e2e():
        NOTIFIED.clear()
        codex_limits.invalidate()
        run_turn(slug, nid, "do the thing")
        st = supervisor.state(slug, nid)
        n = node_doc(slug, nid)

        # ── it is a FAILURE, not a success ────────────────────────────────
        eq(st["turns_run"], 0, "a wall is not a completed turn")
        eq(n.get("turns") or [], [], "…and books no turn ring entry")
        assert st["last_error"], "the desk banner must say something"
        assert "usage limit" in st["last_error"].lower(), st["last_error"]

        # ── the durable row carries the CLI's OWN words ───────────────────
        rows = err_rows(slug, nid)
        eq(len(rows), 1, "exactly one durable turn-error row")
        assert fakecodex.LIMIT_MESSAGE[:40] in rows[0]["text"], rows[0]
        assert "usage_limit_exceeded" in rows[0]["text"], rows[0]

        # ── and the node is FROZEN, the way the claude lane freezes ───────
        fz = n.get("frozen")
        assert isinstance(fz, dict), f"no freeze record: {fz!r}"
        eq(fz.get("limit"), True, "positively marked a LIMIT freeze")
        eq(fz.get("reset_src"), "provider",
           "timed from the app-server's own resetsAt")
        want = t_start + fakecodex.LIMIT_RESET_IN
        assert abs(float(fz["until_ts"]) - want) < 120, (fz["until_ts"], want)
        # ⚠ never {error, no until}: ledger's pre-№41 migration re-tags that
        # shape as a kiosk SPEND freeze and ▶ then skips the node forever
        assert fz.get("until"), "a freeze with no human label is the trap"
        assert fakecodex.LIMIT_MESSAGE[:40] in fz.get("error", ""), fz
        # the replayed text is the turn's FULL prompt — the org-state block
        # the prologue prepended, and then the message — exactly what the
        # claude lane keeps, so ▶ replays the turn rather than a summary of it
        replay = fz.get("resume_texts") or []
        eq(len(replay), 1, "one replay text")
        assert replay[0].endswith("do the thing"), replay
        assert "[ORG STATE" in replay[0], "the whole prompt, not just the mail"
        # qualifiers that do not apply are ABSENT, not inherited
        for k in ("cause", "pool", "untrusted", "on_fallback"):
            assert k not in fz, f"{k} should not be set by this path: {fz}"
        assert (slug, nid, "frozen") in NOTIFIED, NOTIFIED
    check("§2 a codex usage limit fails the turn, records the CLI's own "
          "reason, and freezes the node with the app-server's reset time",
          t_e2e)

    def t_resume():
        n = node_doc(slug, nid)
        # ▶ will act on it…
        assert supervisor.resumable(n), "▶ must be able to resume this node"
        org = store.load_org(slug)
        # …and the timer holds it until its own reset, then wakes it. Both
        # halves matter: a freeze nothing wakes is a nicer-looking silence.
        eq(supervisor.auto_resume_ready(org, time.time()), set(),
           "not ready before the reset")
        ready = supervisor.auto_resume_ready(
            org, time.time() + fakecodex.LIMIT_RESET_IN + 3600)
        assert nid in ready, f"the timer never wakes it: {ready}"
    check("§2 the freeze is the ORDINARY kind: ▶ resumes it and the "
          "auto-resume timer wakes it at its reset", t_resume)

    def t_usage_panel():
        # the exhausted snapshot must reach the shared cache — the header glow
        # learned nothing from the one turn that had something to say, because
        # the fold ran only on the success path
        peek = codex_limits.peek()
        assert peek.get("available"), peek
        hot = [x for x in peek["limits"] if x.get("percent") == 100.0]
        assert hot, peek
        assert any(x.get("is_active") for x in hot), peek
    check("§2 the exhausted rate-limit snapshot reaches the usage panel",
          t_usage_panel)

    # ───────────────────────────────────────────────────────────────────────
    print("§3 containment — what must NOT freeze")
    os.environ["FAKECODEX_SCENARIO"] = "plain_failure"
    slug3, nid3 = mkorg("plainfail")

    def t_plain():
        run_turn(slug3, nid3, "do the other thing")
        n = node_doc(slug3, nid3)
        st = supervisor.state(slug3, nid3)
        # it still FAILS loudly — that half is the D-209 fix too
        assert st["last_error"], "a plain failure must still be visible"
        assert "sandbox" in st["last_error"].lower(), st["last_error"]
        eq(len(err_rows(slug3, nid3)), 1, "one durable row")
        eq(st["turns_run"], 0, "and it is not a completed turn")
        # …but it is NOT a capacity problem, so nothing is parked
        eq(n.get("frozen"), None, "a non-limit failure must not freeze")
    check("§3 a failed turn that is NOT a limit fails loudly and freezes "
          "nothing", t_plain)

    def t_success_untouched():
        os.environ["FAKECODEX_SCENARIO"] = "tool"
        slug4, nid4 = mkorg("healthy")
        run_turn(slug4, nid4, "hello")
        n = node_doc(slug4, nid4)
        st = supervisor.state(slug4, nid4)
        eq(st["turns_run"], 1, "a healthy turn is still a turn")
        eq(st["last_error"], None, "…with no error")
        eq(n.get("frozen"), None, "…and no freeze")
        eq(len(n.get("turns") or []), 1, "…and one ring entry")
    check("§3 a healthy codex turn is completely unaffected",
          t_success_untouched)

    # ───────────────────────────────────────────────────────────────────────
    print("§4 the notification codex 0.150.1 does not send")
    os.environ["FAKECODEX_SCENARIO"] = "turn_failed_notification"
    slug5, nid5 = mkorg("turnfailed")

    def t_turn_failed():
        # `turn/failed` is retained in codexrun for a future server. It is
        # dead code against this CLI, and dead code that claims to handle a
        # lane rots — so the fixture keeps it exercised. NOTE the difference:
        # this path has no rate-limit notification behind it, so it lands on
        # the honest probe floor rather than a real reset.
        run_turn(slug5, nid5, "third thing")
        fz = node_doc(slug5, nid5).get("frozen")
        assert isinstance(fz, dict), f"no freeze: {fz!r}"
        eq(fz.get("limit"), True, "still a limit freeze")
        eq(fz.get("reset_src"), "probe", "no machine reset was offered")
        assert abs(float(fz["until_ts"]) - (time.time()
                                           + supervisor.PROBE_FLOOR)) < 120
        assert "probing" in fz.get("until", ""), fz
    check("§4 a turn/failed notification freezes too, on the honest probe "
          "floor when no reset was offered", t_turn_failed)

    # ───────────────────────────────────────────────────────────────────────
    print("§5 the gemini lane — the SEAM, not the wire (unmeasured, see the "
          "module docstring)")
    os.environ["FAKEGEMINI_SCENARIO"] = "usage_limit"
    slug6, nid6 = mkorg("geminiwall", tier="pro")

    def t_gemini():
        run_turn(slug6, nid6, "gemini thing")
        st = supervisor.state(slug6, nid6)
        n = node_doc(slug6, nid6)
        eq(st["turns_run"], 0, "not a completed turn")
        assert st["last_error"], "the failure is visible"
        fz = n.get("frozen")
        assert isinstance(fz, dict), f"no freeze record: {fz!r}"
        eq(fz.get("limit"), True, "a limit freeze")
        # gemini offers no machine reset on this lane at all
        eq(fz.get("reset_src"), "probe", "no reset time exists here")
        replay = fz.get("resume_texts") or []
        eq(len(replay), 1, "one replay text")
        assert replay[0].endswith("gemini thing"), replay
        assert supervisor.resumable(n), "▶ must resume it"
    check("§5 a gemini failure whose text names a quota freezes through the "
          "SAME seam (wire wording unverified)", t_gemini)

    # ───────────────────────────────────────────────────────────────────────
    print("§6 the horizon guard, on the shared constant")

    def t_horizon():
        # `_provider_limit_until` bands against limits.MAX_HORIZON — the same
        # 8-day ceiling every other reset in this codebase is banded by, not a
        # private number that can drift away from it.
        now = 1_000_000.0
        inside = now + limits.MAX_HORIZON - 3600
        outside = now + limits.MAX_HORIZON + 3600
        eq(supervisor._provider_limit_until("hit your usage limit",
                                            inside, now)[1], "provider",
           "just inside the shared horizon")
        eq(supervisor._provider_limit_until("hit your usage limit",
                                            outside, now)[1], "probe",
           "just outside it")
    check("§6 the machine reset is banded on limits.MAX_HORIZON, at the edge",
          t_horizon)

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
