"""MID-TURN MAIL: DELIVERED IS DURABLE, DURABLE IS VISIBLE, AND NEITHER IS
CLAIMED BEFORE IT HAPPENED.

    python backend/tests/test_steer_delivery.py   (no pytest; plain asserts)

Two invariants, one file, both of them about the same seam — `pop_steer`, the
moment a mid-task message stops being mail and becomes something the agent has
been told.

  I. A DELIVERED STEER IS ANNOUNCED. The durable steered row is written and
     then a `steered` frame goes over the websocket, in that order. The claude
     lane always had the frame, because its steering hook comes in through an
     HTTP door that emitted one; the codex and gemini legs call `pop_steer`
     in-process from a pump thread and never pass that door, so their mid-turn
     mail went durable with NOTHING on the wire to say so. The desk then found
     out at its next 2.5 s heartbeat — and `convo.ingestStream` uses that frame
     for the two things a heartbeat cannot do in time: retire the sender's
     optimistic ghost, and nudge the refetch that draws the durable row.
     Measured on the live coordinator (a codex agent), 2026-09-02: a steered
     row committed at 09:21:57.667Z with zero `steered` frames in a 15-minute
     websocket capture of that same node.

     The announcement is also an ORDERING BARRIER. Codex notifications and
     JSON-RPC responses are consumed on independent threads, so the provider
     can notify the answer caused by a steer before the request waiter sees
     its acceptance. Both that answer's durable journal record and its desk
     frame must wait behind the accepted steer's durable row + frame.

 II. A REFUSED STEER CLAIMS NOTHING. `pop_steer` used to commit on the FETCH:
     it wrote the durable "the agent was told this" row and confirmed the
     journal batch away, and only afterwards did the lane ask the app-server to
     accept the text. When that ask was refused — the turn ended inside the 2 s
     poll interval, or the wire has no steer verb at all (gemini, every time) —
     the carriers went back on the queue and the NEXT turn delivered the same
     words again. One message, two bubbles, permanently.
     Measured on the live coordinator, 2026-09-02: steered_log 07:38:11.278Z
     and turn user row 07:38:13.986Z, the same 3512 characters.

The instrument is the same one test_codex_stream_order.py uses, for the same
reason: `supervisor.stream` IS the websocket the desk sees, so every payload
crossing it is checked against the DURABLE state on disk at that instant.
Ordering that merely happens to hold cannot pass.

Anti-vacuity: §5 plants a violation and requires the instrument to report it,
and every section asserts a non-zero count of the thing it is judging, so a
fixture that quietly does nothing fails instead of passing by silence.
"""

import json
import os
import sys
import tempfile
import threading
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-steerdel-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_WARM"] = "0"       # cold spawns only: one process per turn
# a PORT NOBODY SERVES — the codex leg's tool dispatcher POSTs /api/agent, and
# left unset it would default to 7360 and reach the operator's LIVE backend
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

FAKECODEX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fakecodex.py")
CODEX_HOME = tempfile.mkdtemp(prefix="steerdel-home-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME
with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')

from orgtree import providers, store, supervisor                   # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

providers._status_cache = None
supervisor.CODEX_STEER_POLL = 0.2

PASS = 0
FAIL: list[tuple[str, str]] = []


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


def truthy(got, what):
    if not got:
        raise AssertionError(f"{what}: got {got!r}")


# ── the instrument ──────────────────────────────────────────────────────────
#: (kind, durable steered rows for this node AT THE INSTANT this payload was
#: emitted, text). A `steered` frame carrying 0 durable rows is the announcement
#: outrunning the record it announces.
SEEN: list[tuple[str, int, str]] = []
WATCH: dict[str, str] = {"slug": "", "nid": ""}


def durable_steers(slug: str, nid: str) -> list[dict]:
    """Successful steered rows on disk right now — folds excluded, since a
    fold is the record of a MISS and would mask the very gap this counts."""
    try:
        org = store.load_org(slug)
    except Exception:                                            # noqa: BLE001
        return []
    return [e for e in (org.d.get("steered_log") or {}).get(nid, [])
            if not e.get("fold")]


def folds(slug: str, nid: str) -> list[dict]:
    try:
        org = store.load_org(slug)
    except Exception:                                            # noqa: BLE001
        return []
    return [e for e in (org.d.get("steered_log") or {}).get(nid, [])
            if e.get("fold")]


def delivering(slug: str, nid: str) -> list[dict]:
    """Journal batches still UNCONFIRMED — what `_fold_back_undelivered` reads
    to decide a message was never consumed."""
    try:
        org = store.load_org(slug)
    except Exception:                                            # noqa: BLE001
        return []
    return list((org.d.get("delivering") or {}).get(nid) or [])


def recording_stream(slug: str, nid: str, payload: dict) -> None:
    if WATCH["slug"] and slug == WATCH["slug"] and nid == WATCH["nid"]:
        SEEN.append((str(payload.get("kind") or ""),
                     len(durable_steers(slug, nid)),
                     str(payload.get("text") or "")))


supervisor.stream = recording_stream


def steered_frames() -> list[tuple[str, int, str]]:
    return [f for f in SEEN if f[0] == "steered"]


# ── fixtures ────────────────────────────────────────────────────────────────
def mkorg(label: str) -> tuple[str, str]:
    org = store.create_org(f"zz steerdel {label}")
    r = org.hire(USER, None, "sol", 0, "cx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a steer-delivery test agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def scenario(name: str, thread: str) -> None:
    """Pick the fixture's behaviour AND give this section its own thread id —
    `transcript_path` globs across ALL projects, so a shared constant would
    hand one org another's journal (see test_codex_stream_order)."""
    os.environ["FAKECODEX_SCENARIO"] = name
    os.environ["FAKECODEX_THREAD_ID"] = thread


def run_turn(slug: str, nid: str, carrier):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(slug, nid, carrier)


def turn_in_background(slug: str, nid: str, text: str) -> dict:
    """Start the turn on its own thread and hand back a handle. The steer has
    to be posted WHILE the turn runs, which is the only way this seam is
    reachable at all."""
    box: dict = {"follow": None, "error": None}

    def go() -> None:
        try:
            box["follow"] = run_turn(slug, nid,
                                     {"text": text, "view": text})
        except Exception as e:                                   # noqa: BLE001
            box["error"] = traceback.format_exc()
            box["exc"] = e

    t = threading.Thread(target=go, daemon=True)
    t.start()
    box["thread"] = t
    return box


def wait_responding(slug: str, nid: str, timeout: float = 30.0) -> bool:
    """`responding` is the flag `send_message` reads to choose the steer door.
    It is set inside the leg, after the turn is on the wire."""
    st = supervisor.state(slug, nid)
    end = time.time() + timeout
    while time.time() < end:
        if st.get("responding"):
            return True
        time.sleep(0.02)
    return False


def post_and_steer(slug: str, nid: str, body: str) -> dict:
    """The user's own door: real mail into the box, then the same
    `send_message` the API calls — which routes to the steer store because the
    node is mid-turn. Nothing here reaches into `st['steer']` by hand."""
    with store.DOC_LOCK:
        org = store.load_org(slug)
        org.post_mail(USER, nid, body, kind="message")
        store.save_org(org)
    return supervisor.send_message(slug, nid, body, view=body, mail_ping=True)


def drain_follow(slug: str, nid: str, follow, guard: int = 4) -> int:
    """Run whatever the turn handed back, and whatever that hands back, so the
    re-queued carrier of a refused steer actually gets its next turn."""
    n = 0
    while follow is not None and n < guard:
        n += 1
        follow = supervisor._run_one_turn(slug, nid, follow)
    return n


def rendered_user_rows(slug: str, nid: str, needle: str) -> int:
    """How many times the desk would show this text as a message FROM THE
    USER — the transcript's own rows and the merged steered rows together,
    which is exactly what `read_chat` hands the client."""
    org = store.load_org(slug)
    chat = supervisor.read_chat(org, nid, last=1000)
    return sum(1 for m in chat["messages"]
               if m.get("role") == "user" and needle in (m.get("text") or ""))


def chat_before(slug: str, nid: str, first: str, second: str) -> None:
    """The durable order the desk receives after all live rows reconcile."""
    chat = supervisor.read_chat(store.load_org(slug), nid, last=1000)
    rows = [str(m.get("text") or "") for m in chat["messages"]]
    a = next((i for i, text in enumerate(rows) if first in text), -1)
    b = next((i for i, text in enumerate(rows) if second in text), -1)
    if a < 0 or b < 0 or a >= b:
        raise AssertionError(
            f"wanted {first!r} before {second!r}; indices {(a, b)}, "
            f"rows={rows!r}")


def main() -> int:
    print("§1 a DELIVERED steer: durable first, then announced")
    scenario("steer", "fake-thread-steer-ok")
    slug, nid = mkorg("ok")
    SEEN.clear()
    WATCH["slug"], WATCH["nid"] = slug, nid
    box = turn_in_background(slug, nid, "the turn that will be steered")
    steered_ok = wait_responding(slug, nid)
    routed = post_and_steer(slug, nid, "MIDTURN-ONE please look at this")
    box["thread"].join(timeout=90)
    time.sleep(0.3)                     # let the pump's commit settle

    check("the fixture actually reached the steer seam (anti-vacuity: a turn "
          "that never went responding proves nothing)",
          lambda: eq((steered_ok, bool(routed.get("steering"))), (True, True),
                     "responding + routed through the steer door"))
    check("the delivered steer left exactly one durable row",
          lambda: eq(len(durable_steers(slug, nid)), 1, "durable steered rows"))
    check("…and it reached the DESK: a `steered` frame was emitted",
          lambda: eq(len(steered_frames()), 1, "steered frames"))
    check("the frame carries the message",
          lambda: truthy("MIDTURN-ONE" in steered_frames()[0][2],
                         f"frame text {steered_frames()[0][2][:80]!r}"))
    check("the DURABLE ROW EXISTED WHEN THE FRAME WAS SENT — announcement "
          "never outruns the record it announces",
          lambda: eq([n for _, n, _ in steered_frames()], [1],
                     "durable rows at emission time"))
    check("the provider deliberately notified its ANSWER before acknowledging "
          "the steer, but that answer waited behind the durable question",
          lambda: eq([n for kind, n, text in SEEN
                      if kind == "text" and "STEERED[" in text
                      and "MIDTURN-ONE" in text],
                     [1], "durable steer rows at answer emission time"))
    check("the reconciled transcript keeps the same order — the steer's "
          "pre-request timestamp sorts above the early provider item",
          lambda: chat_before(slug, nid, "MIDTURN-ONE", "STEERED["))
    check("the journal batch is confirmed — delivery was claimed, and it "
          "happened",
          lambda: eq(delivering(slug, nid), [], "unconfirmed batches"))
    check("no fold row: nothing missed the window",
          lambda: eq(folds(slug, nid), [], "fold rows"))
    check("the desk renders the message exactly once",
          lambda: eq(rendered_user_rows(slug, nid, "MIDTURN-ONE"), 1,
                     "rendered copies"))

    print("§2 a REFUSED steer: nothing durable, nothing confirmed, nothing lost")
    scenario("steer_refuse", "fake-thread-steer-refuse")
    slug2, nid2 = mkorg("refuse")
    SEEN.clear()
    WATCH["slug"], WATCH["nid"] = slug2, nid2
    box2 = turn_in_background(slug2, nid2, "the turn that refuses the steer")
    resp2 = wait_responding(slug2, nid2)
    routed2 = post_and_steer(slug2, nid2, "MIDTURN-TWO the refused one")
    box2["thread"].join(timeout=90)
    time.sleep(0.3)
    st2 = supervisor.state(slug2, nid2)
    with supervisor._state_lock:
        still_queued = list(st2["queue"])
    # …and whatever the turn's own exit already popped off that queue and
    # handed back as the next carrier to run. BOTH are "back on the queue" —
    # the turn boundary just got there first.
    requeued = still_queued + ([box2["follow"]] if box2["follow"] else [])
    after_refusal = list(SEEN)

    check("the fixture actually reached the steer seam",
          lambda: eq((resp2, bool(routed2.get("steering"))), (True, True),
                     "responding + routed through the steer door"))
    check("a REFUSED steer writes NO durable steered row — the transcript "
          "never says the agent was told something it was not",
          lambda: eq(len(durable_steers(slug2, nid2)), 0,
                     "durable steered rows"))
    check("…and emits NO frame either",
          lambda: eq([f for f in after_refusal if f[0] == "steered"], [],
                     "steered frames"))
    check("the journal batch stays UNCONFIRMED, so a crash here folds the "
          "mail back instead of losing it",
          lambda: truthy(delivering(slug2, nid2), "unconfirmed batches"))
    check("the miss is recorded where the wait happened (one dim system line, "
          "not silence)",
          lambda: eq(len(folds(slug2, nid2)), 1, "fold rows"))
    check("the WHOLE carrier is back on the queue — its journal tokens too, "
          "or the batch could never be confirmed by the turn that delivers it",
          lambda: eq([bool(isinstance(c, dict) and c.get("toks"))
                      for c in requeued],
                     [True], "re-queued carriers carrying their tokens"))

    print("§3 …and the next turn delivers it ONCE (the double this removes)")
    scenario("tool", "fake-thread-steer-refuse")
    ran = drain_follow(slug2, nid2, box2["follow"])
    check("the re-queued carrier actually got a turn (anti-vacuity)",
          lambda: eq(ran, 1, "follow-up turns run"))
    check("the message the steer refused renders EXACTLY ONCE — not twice, "
          "which is what committing on the fetch produced",
          lambda: eq(rendered_user_rows(slug2, nid2, "MIDTURN-TWO"), 1,
                     "rendered copies"))
    check("…and it is not lost either: it IS on screen",
          lambda: truthy(rendered_user_rows(slug2, nid2, "MIDTURN-TWO") >= 1,
                         "at least one rendered copy"))

    print("§4 the claude door: one frame per message, not two")
    slug3, nid3 = mkorg("hook")
    SEEN.clear()
    WATCH["slug"], WATCH["nid"] = slug3, nid3
    st3 = supervisor.state(slug3, nid3)
    with supervisor._state_lock:
        st3["steer"] = [{"toks": [], "text": "HOOKMSG one",
                         "view": "HOOKMSG one"}]
    got = supervisor.pop_steer(slug3, nid3)      # the hook's own fetch
    check("the hook fetch returns the text",
          lambda: eq(len(got), 1, "messages returned"))
    check("exactly ONE frame — the emission moved into commit_steer, and the "
          "HTTP door must no longer add its own",
          lambda: eq(len(steered_frames()), 1, "steered frames"))
    check("the hook's fetch IS the delivery, so it commits: one durable row",
          lambda: eq(len(durable_steers(slug3, nid3)), 1, "durable rows"))
    check("and that row was on disk before the frame went out",
          lambda: eq([n for _, n, _ in steered_frames()], [1],
                     "durable rows at emission time"))

    print("§5 anti-vacuity: the instrument can SEE the violation")
    SEEN.clear()
    slug4, nid4 = mkorg("blind")
    WATCH["slug"], WATCH["nid"] = slug4, nid4
    # a frame with no durable row behind it — exactly what the codex lane could
    # not produce before (it produced no frame at all) and what a future
    # regression that emits BEFORE committing would produce
    supervisor.stream(slug4, nid4, {"kind": "steered", "text": "planted"})
    check("a `steered` frame emitted with no durable row is REPORTED, so a "
          "clean §1/§4 means clean and not blind",
          lambda: eq([n for _, n, _ in steered_frames()], [0],
                     "durable rows at emission time"))

    print()
    for label, tb in FAIL:
        print(f"--- FAIL: {label}\n{tb}")
    print(f"{PASS} checks passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
