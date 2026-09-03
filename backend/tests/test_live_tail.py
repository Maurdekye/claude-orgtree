"""The live/durable reconciliation — `supervisor._sweep_live`, exhaustively.

WHY THIS SUITE EXISTS
---------------------
Every "the message flashed and vanished" bug in this project's history has come
out of ONE mechanism: deciding that a row on screen may be retired because its
durable twin has arrived, when it had not. D-50 states the rule — *superseded is
not replaced; nothing is retired until its replacement is in hand* — and P2 moved
the decision server-side into `_sweep_live`, which can see both sides at once.

And then nothing tested it. Twelve suites, 1,870 checks, and the one function
that decides what leaves the screen had zero. It shipped a bug of exactly the
family it exists to prevent (user report 2026-08-04): *"thinking blocks sometimes
appear late or out of order, shifting messages around"*. A live `thought` row has
no identity — the API seals the reasoning, so the row carries no text and its
durable twin carries only `thinking_sealed` — and the rule was "is there ANY
sealed thinking in the last 12 rows?". The first think of a turn therefore
retired every later one on sight, twin or no twin: the line left the screen and
came back a poll later, ABOVE rows that were already on it.

WHAT IS ASSERTED
----------------
The invariant is stated over the whole rendered conversation rather than over one
row's retirement, because that is what the user sees:

    rendered = durable(transcript ↑ k)  ++  live survivors

  ① NO GAP        every step the agent has taken is somewhere in `rendered`
  ② IN ORDER      `rendered` is non-decreasing in step index — a row never
                  appears above one that was already shown (the reported bug)
  ③ NO ECHO       a step appears twice only where the mechanism admits it:
                  a `think` whose successor record is not yet in the transcript
                  (a ~millisecond window in real timing, and the safe direction —
                  a double beats a gap, per D-50)

and it is checked at EVERY lag: for a turn of n steps, every (live rows emitted,
transcript records written) pair the real timing can produce. That is the axis a
live race only samples.

Hermetic: no port, no Docker, no CLI, no clock assertions. Fast.

Run:  python backend/tests/test_live_tail.py [-v] [--only SUBSTR]
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import sys
import tempfile
import traceback
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-livetail-")
_HOME = os.path.join(_TMP, "home")
os.makedirs(_HOME, exist_ok=True)
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: net._default_address
# falls back to net.DEFAULT_HUB_ADDRESS — the operator's real hub — when this
# root has no defaults.json, and any rig that starts the net daemon then
# registers its fixture orgs there permanently. Measured twice (user report
# 2026-08-06; ~45 fixture orgs again on 2026-08-10). The discard port refuses
# instantly, so registration fails harmlessly into the backoff.
# Guarded over this whole directory by test_external_mail §1.
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

os.environ["USERPROFILE"] = _HOME
os.environ["HOME"] = _HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree import store, supervisor                            # noqa: E402

USER = "@user"
PASS = 0
FAIL: list[tuple[str, str]] = []
VERBOSE = "-v" in sys.argv
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""


def check(label: str, fn) -> None:
    """One check. Failures are recorded and the run continues — the count of
    what else broke is the interesting part."""
    global PASS
    if ONLY and ONLY not in label:
        return
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    if VERBOSE or PASS % 200 == 0 or not ONLY:
        print(f"  ok {PASS:4d}  {label}")


# --------------------------------------------------------------------- world

class World:
    """One org, one agent, one transcript file — and the two clocks that matter
    kept SEPARATE: `emit()` is what the websocket showed, `write()` is what the
    CLI has committed to the transcript. Every scenario here is a choice of how
    far apart those two are allowed to drift."""

    _n = 0

    def __init__(self, label: str = "w") -> None:
        World._n += 1
        org = store.create_org(f"zz livetail {World._n} {label}"[:60])
        self.slug = org.d["slug"]
        org.hire(USER, None, "haiku", 5, "agent")
        store.save_org(org)
        self.nid = "agent"
        self.sid = org.node(self.nid)["session_id"]
        tdir = os.path.join(_HOME, ".claude", "projects", "orgtree-suite")
        os.makedirs(tdir, exist_ok=True)
        self.path = os.path.join(tdir, self.sid + ".jsonl")
        self._recs = 0

    # --- the durable side: records the CLI has written
    def _rec(self, rec: dict[str, Any]) -> None:
        self._recs += 1
        rec.setdefault("timestamp",
                       f"2026-08-04T05:{self._recs // 60:02d}:"
                       f"{self._recs % 60:02d}.{self._recs % 1000:03d}Z")
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def write_think(self, body: str = "", mid: str | None = None) -> None:
        """A sealed thinking record — `{"signature": …, "thinking": ""}`, which
        is what every tier has produced since 2026-08-02. `body` fills it in for
        the tiers that still stream their reasoning. `mid` sets message.id —
        fable writes TWO thinking blocks of ONE message as two records sharing
        it (the double-thought bug, 2026-08-04)."""
        self._rec({"type": "assistant", "message": {
            "role": "assistant", "model": "m",
            **({"id": mid} if mid else {}), "content": [
                {"type": "thinking", "thinking": body, "signature": "sig"}]}})

    def write_tool(self, tid: str, name: str = "Read") -> None:
        self._rec({"type": "assistant", "message": {
            "role": "assistant", "model": "m", "content": [
                {"type": "tool_use", "id": tid, "name": name,
                 "input": {"file_path": "/x"}}]}})

    def write_text(self, text: str) -> None:
        self._rec({"type": "assistant", "message": {
            "role": "assistant", "model": "m",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 10}}})

    def write_user(self, text: str) -> None:
        self._rec({"type": "user", "message": {"role": "user", "content": text}})

    # --- the live side: rows the websocket has already shown
    def emit_thought(self, secs: int = 3, text: str = "") -> None:
        supervisor.live_row(self.slug, self.nid,
                            {"kind": "thought", "secs": secs, "text": text})

    def emit_tool(self, tid: str, text: str = "") -> None:
        supervisor.live_row(self.slug, self.nid,
                            {"kind": "tool", "id": tid,
                             "text": text or f"Read · {tid}"})

    def emit_text(self, text: str) -> None:
        supervisor.live_row(self.slug, self.nid, {"kind": "text", "text": text})

    def emit_sticky(self, text: str) -> None:
        supervisor.live_row(self.slug, self.nid,
                            {"kind": "text", "sticky": True, "text": text})

    # --- the desk's fetch: read_chat runs the sweep
    def poll(self) -> dict[str, Any]:
        return supervisor.read_chat(store.load_org(self.slug), self.nid)

    def live(self) -> list[dict[str, Any]]:
        return list(supervisor.state(self.slug, self.nid).get("live") or [])

    def end_turn(self) -> None:
        """What `_run_turn` does at the end: sticky rows survive, the rest go."""
        st = supervisor.state(self.slug, self.nid)
        with supervisor._state_lock:
            st["live"] = [r for r in (st.get("live") or []) if r.get("sticky")]

    # --- mid-turn mail: real to the org, invisible to the CLI's transcript
    def steer(self, text: str, at: str) -> None:
        """Deliver mail INTO the running turn.

        It rides PostToolUse hook context, which the CLI never writes to its
        transcript — so the org's `steered_log` is the durable copy and
        `read_chat` synthesizes the row from it, interleaved by timestamp.
        `at` is that stamp, and it is the whole point: a steer lands in the
        MIDDLE of a turn, after rows the agent has already produced."""
        org = store.load_org(self.slug)
        org.d.setdefault("steered_log", {}).setdefault(self.nid, []).append(
            {"at": at, "text": text})
        store.save_org(org)

    def destroy(self) -> None:
        try:
            store.delete_org(self.slug)
        except Exception:                                        # noqa: BLE001
            pass


# ------------------------------------------------------------- the projection

def durable_steps(payload: dict[str, Any]) -> list[str]:
    """The rendered conversation's DURABLE half, as step labels.

    A message row can carry a thought AND a body (the CLI writes them as
    separate records, but nothing in the format promises that), so a row may
    project to more than one step."""
    out: list[str] = []
    for m in payload["messages"]:
        if m.get("thinking") or m.get("thinking_sealed"):
            out.append("think")
        for t in m.get("tools") or []:
            out.append("tool:" + str(t.get("id")))
        if (m.get("text") or "").strip() and m.get("role") == "assistant":
            out.append("text:" + m["text"][:40])
    return out


def live_steps(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for r in payload.get("live") or []:
        if r.get("sticky"):
            out.append("sticky")
        elif r["kind"] == "thought":
            out.append("think")
        elif r["kind"] == "tool":
            out.append("tool:" + str(r.get("id")))
        else:
            out.append("text:" + (r.get("text") or "")[:40])
    return out


# ------------------------------------------------------------------ scenarios

STEP_KINDS = ("think", "tool", "text")


def turn_shapes(n: int) -> list[tuple[str, ...]]:
    """Every turn of n steps, minus the ones the CLI cannot produce.

    `fold_thought` banks a thought ONLY immediately before the text or tool row
    that ended it, so a turn never ends on a thought and never has two in a row
    — modelling those would be testing a shape that cannot occur."""
    ok = []
    for shape in itertools.product(STEP_KINDS, repeat=n):
        if shape[-1] == "think":
            continue
        if any(shape[i] == "think" and shape[i + 1] == "think"
               for i in range(n - 1)):
            continue
        ok.append(shape)
    return ok


def run_shape(shape: tuple[str, ...], live_upto: int, tx_upto: int,
              *, think_body: str = "", label: str = "") -> None:
    """Drive one turn to a chosen (live, transcript) lag and assert the three
    properties of the rendered conversation.

    `live_upto` steps have been streamed to the browser; `tx_upto` steps have
    been committed to the transcript. tx ≤ live always: the websocket event and
    the transcript record are written by the same process at the same moment,
    and the transcript is the one that can lag."""
    w = World((label or "shape")[:20])
    try:
        w.write_user("go")
        names = [f"{k}{i}" for i, k in enumerate(shape)]
        # ① write the durable prefix
        for i in range(tx_upto):
            if shape[i] == "think":
                w.write_think(think_body)
            elif shape[i] == "tool":
                w.write_tool(names[i])
            else:
                w.write_text(names[i])
        # ② emit the live prefix (all of it — the browser is ahead)
        for i in range(live_upto):
            if shape[i] == "think":
                w.emit_thought(text=think_body)
            elif shape[i] == "tool":
                w.emit_tool(names[i])
            else:
                w.emit_text(names[i])
        c = w.poll()
        rendered = durable_steps(c) + live_steps(c)
        want = ["think" if k == "think" else
                ("tool:" + names[i] if k == "tool" else "text:" + names[i])
                for i, k in enumerate(shape)][:live_upto]
        # the ONE admitted echo: the transcript stops right after a think, so
        # its record is durable while the live row is still waiting for the
        # successor that proves the transcript passed it. Bounded to that
        # single position — a double anywhere else is a bug, and so is a
        # double when the transcript did NOT end on a think.
        echo = (want[:tx_upto] + ["think"] + want[tx_upto:]
                if tx_upto and shape[tx_upto - 1] == "think"
                and tx_upto <= live_upto else None)

        ctx = (f"\n  shape={'·'.join(shape)}  live={live_upto} tx={tx_upto}"
               f"\n  rendered={rendered}\n  want    ={want}")
        if rendered == want or (echo is not None and rendered == echo):
            return
        # name the property that broke, rather than printing two lists
        missing = [s for s in want if s not in rendered]
        assert not missing, f"GAP: {missing} is on no surface{ctx}"
        pos = {s: i for i, s in enumerate(want) if s != "think"}
        seq = [pos[s] for s in rendered if s in pos]
        assert seq == sorted(seq), f"OUT OF ORDER: {seq}{ctx}"
        assert len(rendered) <= len(want), f"DUPLICATE row{ctx}"
        raise AssertionError(f"rendered ≠ the turn so far{ctx}")
    finally:
        w.destroy()


# --------------------------------------------------------------------- suite

def main() -> int:
    print("orgtree · live tail — the live/durable reconciliation "
          "(supervisor._sweep_live)\n")

    # ------------------------------------------------------------ ① identity
    print("identity: what proves a live row's durable twin has arrived")

    def _tool_by_id():
        w = World("toolid")
        try:
            w.emit_tool("t1")
            w.emit_tool("t2")
            w.write_tool("t1")
            assert [r.get("id") for r in w.poll()["live"]] == ["t2"], \
                "the tool row whose id landed should retire, and only it"
            w.write_tool("t2")
            assert w.poll()["live"] == []
        finally:
            w.destroy()
    check("a tool row retires on its own tool_use_id, never a sibling's",
          _tool_by_id)

    def _tool_other_id():
        w = World("toolx")
        try:
            w.emit_tool("t1")
            w.write_tool("zzz")
            assert [r.get("id") for r in w.poll()["live"]] == ["t1"], \
                "an unrelated tool record must not retire this row"
        finally:
            w.destroy()
    check("an unrelated tool record retires nothing", _tool_other_id)

    def _text_counted():
        # the agent says the same thing twice in one turn — the classic
        # no-identity trap, and the text-shaped sibling of the thought bug
        w = World("dup")
        try:
            w.emit_text("done.")
            w.emit_text("done.")
            w.write_text("done.")
            live = w.poll()["live"]
            assert len(live) == 1, (
                "ONE durable copy retires ONE live row — the second must stay "
                f"until its own twin lands (got {live})")
            w.write_text("done.")
            assert w.poll()["live"] == []
        finally:
            w.destroy()
    check("two identical texts need two durable copies (counted, not matched)",
          _text_counted)

    def _text_prefix():
        w = World("trunc")
        try:
            # live rows are capped at 2000 chars, the durable row is whole
            w.emit_text("x" * 300)
            w.write_text("x" * 300 + " and the rest of the sentence")
            assert w.poll()["live"] == [], \
                "the durable row extends the live prefix — that is a twin"
        finally:
            w.destroy()
    check("a durable row that EXTENDS the live text is its twin", _text_prefix)

    def _double_think():
        # fable returns TWO thinking blocks in one assistant message; the CLI
        # writes each block as its own record with the same message.id. One
        # thought must render as ONE row with the FIRST record's duration —
        # not "thought for Xs" followed by "thought for a moment" (user bug
        # 2026-08-04, measured: 31/38 fable messages carried two blocks).
        w = World("dblthink")
        try:
            w.write_user("go")
            w.write_think(mid="msg_1")
            w.write_think(mid="msg_1")
            rows = [m for m in w.poll()["messages"]
                    if m.get("thinking") or m.get("thinking_sealed")]
            assert len(rows) == 1, f"one message, one thought row (got {len(rows)})"
            assert rows[0].get("thinking_sealed"), "two sealed blocks stay one sealed line"
            assert rows[0].get("think_secs"), \
                "the merged row keeps the FIRST record's duration"
        finally:
            w.destroy()
    check("two thinking records of ONE message merge into one row", _double_think)

    def _double_think_distinct():
        # …but two thinks from DIFFERENT messages are two real thoughts
        w = World("dblthink2")
        try:
            w.write_think(mid="msg_1")
            w.write_think(mid="msg_2")
            rows = [m for m in w.poll()["messages"]
                    if m.get("thinking") or m.get("thinking_sealed")]
            assert len(rows) == 2, "different message.ids never merge"
        finally:
            w.destroy()
    check("thinks from different messages stay separate rows",
          _double_think_distinct)

    def _double_think_body():
        # a bodied second block joins the row and unseals it
        w = World("dblthink3")
        try:
            w.write_think(mid="msg_1")
            w.write_think(body="the visible half", mid="msg_1")
            rows = [m for m in w.poll()["messages"]
                    if m.get("thinking") or m.get("thinking_sealed")]
            assert len(rows) == 1
            assert rows[0].get("thinking") == "the visible half"
            assert not rows[0].get("thinking_sealed"), \
                "a body arrived — the row must not still claim to be sealed"
        finally:
            w.destroy()
    check("a bodied block merging into a sealed row unseals it",
          _double_think_body)

    def _sticky():
        w = World("sticky")
        try:
            w.emit_sticky("/context output")
            w.write_text("/context output")
            w.write_text("something else entirely")
            assert len(w.poll()["live"]) == 1, \
                "a sticky row is in no transcript and must never be swept"
            w.end_turn()
            assert len(w.live()) == 1, "…and it survives the turn's end"
        finally:
            w.destroy()
    check("sticky rows are never swept, and outlive the turn", _sticky)

    def _turn_end():
        w = World("endturn")
        try:
            w.emit_tool("t1")
            w.emit_sticky("kept")
            w.end_turn()
            assert [r.get("kind") for r in w.live()] == ["text"], \
                "the turn's end clears everything except sticky rows"
        finally:
            w.destroy()
    check("the turn's end clears the tail but keeps sticky rows", _turn_end)

    # ------------------------------------------------------------- ② thought
    print("\nthought rows — the kind with no identity (the 2026-08-04 bug)")

    def _stranger_think():
        # THE REGRESSION. Two thinks in one turn; the first one's durable record
        # must not retire the second one's live row.
        w = World("stranger")
        try:
            w.emit_thought(3)
            w.emit_tool("t1")
            w.write_think()
            w.write_tool("t1")
            assert w.poll()["live"] == [], "the first pair retires together"
            w.emit_thought(7)
            w.emit_tool("t2")
            live = w.poll()["live"]          # transcript has NOT caught up yet
            assert [r["kind"] for r in live] == ["thought", "tool"], (
                "the second thought must survive: its own record is not "
                f"written yet (got {live})")
            w.write_think()
            w.write_tool("t2")
            assert w.poll()["live"] == [], "…and retires when it is"
        finally:
            w.destroy()
    check("a thought is not retired by an EARLIER think's durable record",
          _stranger_think)

    def _think_with_body():
        # haiku still streams its reasoning; the rule must not depend on which
        # tier is talking
        w = World("body")
        try:
            w.emit_thought(2, text="first thought")
            w.emit_tool("t1")
            w.write_think("first thought")
            w.write_tool("t1")
            assert w.poll()["live"] == []
            w.emit_thought(2, text="second thought")
            w.emit_tool("t2")
            assert len(w.poll()["live"]) == 2, \
                "a streamed thought follows the same rule as a sealed one"
        finally:
            w.destroy()
    check("the rule is the same for streamed reasoning as for sealed",
          _think_with_body)

    def _think_repeat_body():
        # two identical thoughts — prefix matching would retire both on one twin
        w = World("samebody")
        try:
            w.emit_thought(2, text="hmm")
            w.emit_tool("t1")
            w.emit_thought(2, text="hmm")
            w.emit_tool("t2")
            w.write_think("hmm")
            w.write_tool("t1")
            live = w.poll()["live"]
            assert [r["kind"] for r in live] == ["thought", "tool"], (
                f"only the first pair has landed (got {live})")
        finally:
            w.destroy()
    check("two identical thoughts do not retire on one durable copy",
          _think_repeat_body)

    def _think_never_stranded():
        w = World("strand")
        try:
            w.emit_thought(4)
            w.emit_text("all done")
            w.write_think()
            w.write_text("all done")
            assert w.poll()["live"] == [], (
                "a thought whose successor landed must retire — else it is a "
                "permanent duplicate of the durable row")
        finally:
            w.destroy()
    check("a thought retires as soon as its successor's twin is visible",
          _think_never_stranded)

    # ------------------------------------------------------------------ ③ n
    print("\nrow identity for the client (the render key)")

    def _monotonic():
        w = World("nkey")
        try:
            w.emit_thought(1)
            w.emit_tool("t1")
            w.emit_text("hello")
            ns = [r["n"] for r in w.live()]
            assert ns == sorted(set(ns)) and len(ns) == 3, \
                f"every live row carries a distinct increasing id (got {ns})"
            before = {r["n"]: r["kind"] for r in w.live()}
            w.write_think()
            w.write_tool("t1")
            after = {r["n"]: r["kind"] for r in w.poll()["live"]}
            assert set(after) <= set(before), "ids are stable across a sweep"
            for k, v in after.items():
                assert before[k] == v, (
                    f"row {k} changed kind across a sweep: {before[k]} → {v} — "
                    "an index key would have done exactly this")
        finally:
            w.destroy()
    check("live rows carry a stable, distinct id that survives a sweep",
          _monotonic)

    def _trim():
        w = World("trim")
        try:
            for i in range(supervisor._LIVE_KEEP + 5):
                w.emit_tool(f"t{i}")
            rows = w.live()
            assert len(rows) == supervisor._LIVE_KEEP, "the tail is bounded"
            assert rows[0]["id"] == "t5", "the OLDEST rows are the ones dropped"
            assert [r["n"] for r in rows] == sorted(r["n"] for r in rows)
        finally:
            w.destroy()
    check("the tail is bounded and drops from the head", _trim)

    # -------------------------------------------------------- ④ every lag
    print("\nthe rendered conversation, at every (live, transcript) lag")

    total = 0
    for n in (2, 3, 4):
        shapes = turn_shapes(n)
        for shape in shapes:
            for live_upto in range(1, n + 1):
                for tx_upto in range(0, live_upto + 1):
                    total += 1
                    lbl = (f"{'·'.join(shape)}  live={live_upto} tx={tx_upto}")
                    check(lbl, lambda s=shape, lu=live_upto, tu=tx_upto:
                          run_shape(s, lu, tu, label="lag"))
    print(f"  ({total} lag configurations over "
          f"{sum(len(turn_shapes(n)) for n in (2, 3, 4))} turn shapes)")

    # the same, with a tier that streams its reasoning
    for shape in turn_shapes(3):
        for tx_upto in range(0, 4):
            check(f"streamed · {'·'.join(shape)} tx={tx_upto}",
                  lambda s=shape, tu=tx_upto:
                  run_shape(s, len(s), tu, think_body="hmm", label="stream"))

    # ------------------------------------------------------- ⑤ long turns
    print("\nlong turns, unwatched turns, and the limits of the match window")

    def _long_turn():
        w = World("long")
        try:
            w.write_user("go")
            for i in range(30):
                w.emit_thought(1)
                w.emit_tool(f"t{i}")
                w.write_think()
                w.write_tool(f"t{i}")
                c = w.poll()
                assert c["live"] == [], (
                    f"step {i}: everything durable must retire — a row left "
                    f"behind by the 12-row window would sit on screen for the "
                    f"rest of the turn ({c['live']})")
        finally:
            w.destroy()
    check("a 30-step turn retires cleanly at every step", _long_turn)

    def _burst_then_poll():
        # the whole turn streams before a single poll lands (a fast turn, or a
        # desk nobody had open — the sweep runs ONLY inside read_chat, so an
        # unwatched turn presents its entire backlog at the first look).
        # ⚠ N must EXCEED the match window by a wide margin, or this check
        # cannot fail the way the real defect failed: it stood at 6 steps —
        # 12 durable rows, exactly the old fixed tail — and so passed against
        # a sweep that stranded everything older than 12 (measured 2026-08-12:
        # 8 of 20 rows left on screen for the rest of the turn, every one of
        # them with its durable twin already written).
        w = World("burst")
        N = 20
        try:
            w.write_user("go")
            for i in range(N):
                w.emit_thought(1)
                w.emit_tool(f"t{i}")
                w.write_text(f"step {i}")
                w.write_think()
                w.write_tool(f"t{i}")
            c = w.poll()
            assert c["live"] == [], f"one poll retires the lot ({c['live']})"
            steps = durable_steps(c)
            assert steps.count("think") == N and \
                sum(1 for s in steps if s.startswith("tool:")) == N
        finally:
            w.destroy()
    check("a whole turn streamed before the first poll reconciles in one go",
          _burst_then_poll)

    def _sweep_ignores_the_viewers_window():
        # the other half of the same contract: `last` is the SIZE OF THE
        # CLIENT'S WINDOW, not evidence about what the transcript holds. The
        # sweep used to run on the sliced list, so a desk asking for a short
        # tail silently narrowed the reconciliation and stranded rows it had
        # already been shown.
        w = World("window")
        try:
            w.write_user("go")
            for i in range(20):
                w.emit_tool(f"t{i}")
                w.write_tool(f"t{i}")
            c = supervisor.read_chat(store.load_org(w.slug), w.nid, last=3)
            assert len(c["messages"]) == 3, len(c["messages"])
            assert c["live"] == [], (
                "the viewer's 3-row window decided the sweep: "
                + repr(c["live"]))
        finally:
            w.destroy()
    check("a narrow read window narrows the payload, never the sweep",
          _sweep_ignores_the_viewers_window)

    def _history_never_supplies_a_twin():
        # …and the widening stops where identity stops. A text row is matched
        # by its first 300 chars, so its window is THIS TURN: the same
        # sentence in an earlier turn must not retire today's live row (the
        # false-retire this bound exists to prevent — D-50).
        w = World("hist")
        try:
            w.write_user("go")
            w.write_text("done.")          # last turn said it, and it landed
            w.write_user("again")
            w.emit_text("done.")           # this turn says it, not yet durable
            c = w.poll()
            assert live_steps(c) == ["text:done."], (
                "a phrase from an earlier turn retired this turn's live row: "
                + repr(c["live"]))
            w.write_text("done.")          # now its own twin lands
            assert w.poll()["live"] == []
        finally:
            w.destroy()
    check("an identical text in an EARLIER turn is not a twin",
          _history_never_supplies_a_twin)

    # ------------------------------------------- ⑥ a steer is not a boundary
    print()
    print("mid-turn mail — the row the CLI never wrote (user report 2026-09-03)")

    # WHY. "Double messages for in-progress message events that haven't
    # finished streaming, but are present in the transcript." A text row is
    # matched inside THIS TURN, and the turn was found by scanning back for
    # `role == "user"` — but `read_chat` synthesizes a user row for every
    # mid-turn mail out of `steered_log`, because the CLI transcripts none of
    # it. A steer arriving just after a reply therefore cut the turn to the
    # rows after itself (usually none), the reply's durable twin fell outside
    # the match window, and the live row stayed on screen beside it.
    #
    # The chronology backstop could not cover for it: it retires rows older
    # than the newest durable stamp minus 2 s, and the newest stamp IS the
    # steer — so the one message the steer interrupted sits inside the guard.
    # Measured over the live org: 16 real stranding events, median 10.2 s on
    # screen, p90 54.6 s, max 123 s. Long enough to read twice.

    def _steer_does_not_strand_the_reply_it_interrupted():
        w = World("steer")
        try:
            w.write_user("go")
            w.emit_text("on it — reading the file now")   # shown live
            w.write_text("on it — reading the file now")  # …and now durable
            # mail lands mid-turn, stamped after everything the CLI wrote
            w.steer("[MAIL] FROM coordinator …", "2026-08-04T06:00:00.000Z")
            c = w.poll()
            assert durable_steps(c) == ["text:on it — reading the file now"],                 durable_steps(c)
            assert c["live"] == [], (
                "the reply the steer interrupted rendered TWICE — its twin is "
                "in the transcript and the live row survived: "
                + repr(live_steps(c)))
        finally:
            w.destroy()
    check("mid-turn mail does not strand the reply it interrupted",
          _steer_does_not_strand_the_reply_it_interrupted)

    def _every_reply_before_the_steer_retires():
        # the strand is not limited to the last row: everything the agent said
        # this turn fell outside the cut window at once
        w = World("steer-many")
        try:
            w.write_user("go")
            for i in range(4):
                w.emit_text(f"step {i}")
                w.write_text(f"step {i}")
            w.steer("[MAIL] …", "2026-08-04T06:00:00.000Z")
            c = w.poll()
            assert c["live"] == [], (
                f"4 replies, all with twins, {len(c['live'])} stranded: "
                + repr(live_steps(c)))
        finally:
            w.destroy()
    check("a steer strands none of the turn's earlier replies either",
          _every_reply_before_the_steer_retires)

    def _the_steer_itself_still_renders():
        # the fix must not cost the steer its own bubble — it is the only
        # durable copy of a message the CLI never transcripted
        w = World("steer-shown")
        try:
            w.write_user("go")
            w.write_text("hello")
            w.steer("[MAIL] FROM coordinator · a request",
                    "2026-08-04T06:00:00.000Z")
            c = w.poll()
            steers = [m for m in c["messages"] if m.get("steered")]
            assert len(steers) == 1, [m.get("role") for m in c["messages"]]
            assert steers[0]["role"] == "user"
            assert "FROM coordinator" in steers[0]["text"]
        finally:
            w.destroy()
    check("the steered row is still rendered as its own user message",
          _the_steer_itself_still_renders)

    def _a_real_user_row_is_still_a_boundary():
        # …and the widening stops at the first row the CLI ACTUALLY wrote.
        # Skipping synthesized rows must not walk the window back into
        # history, where an old phrase would false-retire a live row (D-50).
        w = World("steer-bound")
        try:
            w.write_user("go")
            w.write_text("done.")            # LAST turn said it, and it landed
            w.write_user("again")            # a real boundary — the CLI wrote it
            w.emit_text("done.")             # this turn says it; no twin yet
            w.steer("[MAIL] …", "2026-08-04T06:00:00.000Z")
            c = w.poll()
            assert live_steps(c) == ["text:done."], (
                "skipping the steer widened the window past a REAL user row, "
                "so last turn's phrase retired this turn's live row: "
                + repr(live_steps(c)))
            w.write_text("done.")            # its own twin lands
            assert w.poll()["live"] == []
        finally:
            w.destroy()
    check("a steer is skipped; a real user row still bounds the turn",
          _a_real_user_row_is_still_a_boundary)

    # ------------------------------------------------------------ ⑦ epilogue
    print()
    print("─" * 72)
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"{PASS} passed · {len(FAIL)} FAILED")
        return 1
    print(f"ALL {PASS} CHECKS PASS")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
