"""MID-TURN USER MAIL IS NEVER ABSENT FROM BOTH CARRIERS (D-229).

    python backend/tests/test_midturn_mail_ingress.py   (no pytest; plain asserts)

The invariant (user report 2026-09-02, "if i send a message mid-turn with the
wrong timing it just never gets delivered, and i have to send another message
to actually get you to receive it … it seems to be a codex-exclusive issue"):

    once a user message is ACCEPTED, it is durably either INJECTED into the
    running turn exactly once, or OWNED by a carrier that the turn machinery
    will run next — never sitting in memory with the node idle and nothing
    scheduled to move it.

THE DEFECT, measured on the live coordinator (org doc + codex journal, all to
the millisecond):

    09:51:27.868Z  turn 1 starts (user: "rehire all on claude agents")
    09:51:37.383Z  user posts "attempt to use the fallback, i just
                   reconfigured it" — node responding, so `send_message`
                   drains the mailbox into `delivering` and appends the
                   carrier to the in-memory steer store
    09:51:38.389Z  turn 1 ENDS. The codex steer pump (`stop.wait(2.0)`)
                   never polls again; the codex leg sets responding=False
                   and — unlike the claude lane's two boundary sites — folds
                   NOTHING; `_run_one_turn`'s finally finds an empty queue
                   and clears `busy`. The carrier is in RAM, the node idle,
                   the bubble reads "delivering mid-task…", nothing scheduled.
    09:51:52.269Z  the user gives up and sends "go" → a new turn starts
    09:51:59.997Z  that turn's pump pops the STALE carrier → steered_log row.
                   22.6 s after posting, 6 s after the user's second message.

The fix folds the steer store into the queue under the same lock take that
ends steering, on both provider legs, and once more as a lane-agnostic belt
in `_run_one_turn`'s finally. This file makes the window CERTAIN instead of
racy — the fake app-server's `stall` scenario ends the turn on its own clock
while `CODEX_STEER_POLL` is set longer than the stall, so the pump never polls
after the post — and then checks the carrier's fate on every surface: the
in-memory stores, the durable `delivering` journal, the fold receipt in
`steered_log`, the desk's `pending_mail` stage, and the rendered transcript
after the follow-up turn.

  §1  the strand window: one message in the last second of a codex turn
  §2  two messages in that window — both delivered, in order, once each
  §3  a message during finalization (responding=False, busy=True) → queued
  §4  the delivery RECEIPT (`stage`): turn / steer / queued / stranded, and
      the roll-up `mail_stranded`
  §5  anti-vacuity: the pre-fix state, planted by hand, IS reported as
      `stranded` — so §1's "not stranded" means something
  §6  restart: a stranded carrier's durable batch survives the loss of RAM
      and reconcile() re-drives it, delivered exactly once
  §7  the BELT and the ORDER: a lane that forgets to fold, behind a pump
      that requeued — the belt alone saves the message, A before B
  §8  `_fold_steer`, the fold stated once: order to the BACK, and a
      structural guard that every `responding = False` site in supervisor.py
      calls it in the same lock take (review round 2: two claude sites did not)
  §9  a teardown call that raises after the lane's fold: the fold already ran

Every section asserts a non-zero count of the thing it judges (a fixture that
quietly never reached the seam fails instead of passing by silence).
"""

import os
import sys
import tempfile
import threading
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-midturn-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_WARM"] = "0"       # cold spawns only: one process per turn
# a PORT NOBODY SERVES — the codex leg's tool dispatcher POSTs /api/agent, and
# left unset it would default to 7360 and reach the operator's LIVE backend
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

FAKECODEX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fakecodex.py")
CODEX_HOME = tempfile.mkdtemp(prefix="midturn-home-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME
with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')

from orgtree import api, providers, store, supervisor              # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

providers._status_cache = None

#: the pump must NOT poll during the fixture's stall — that is the window
STALL_S = 2.0
NEVER_POLL = 60.0

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


# ── fixtures (the same doors test_steer_delivery.py uses) ───────────────────
def mkorg(label: str) -> tuple[str, str]:
    org = store.create_org(f"zz midturn {label}")
    r = org.hire(USER, None, "sol", 0, "cx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a mid-turn mail test agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def scenario(name: str, thread: str, stall: float | None = None) -> None:
    """Pick the fixture's behaviour AND give this section its own thread id —
    `transcript_path` globs across ALL projects, so a shared constant would
    hand one org another's journal (see test_codex_stream_order)."""
    os.environ["FAKECODEX_SCENARIO"] = name
    os.environ["FAKECODEX_THREAD_ID"] = thread
    if stall is not None:
        os.environ["FAKECODEX_STALL_S"] = str(stall)


def run_turn(slug: str, nid: str, carrier):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(slug, nid, carrier)


def turn_in_background(slug: str, nid: str, text: str) -> dict:
    box: dict = {"follow": None, "error": None}

    def go() -> None:
        try:
            box["follow"] = run_turn(slug, nid, {"text": text, "view": text})
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


def wait_idle(slug: str, nid: str, timeout: float = 60.0) -> bool:
    st = supervisor.state(slug, nid)
    end = time.time() + timeout
    while time.time() < end:
        with supervisor._state_lock:
            if not st.get("busy") and not st.get("waiting"):
                return True
        time.sleep(0.05)
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
    """Run whatever the turn handed back, and whatever that hands back — the
    turn loop's own iteration, in the foreground."""
    n = 0
    while follow is not None and n < guard:
        n += 1
        follow = supervisor._run_one_turn(slug, nid, follow)
    return n


def rendered_user_rows(slug: str, nid: str, needle: str) -> int:
    """How many times the desk would show this text as a message FROM THE
    USER — the transcript's own rows and the merged steered rows together."""
    org = store.load_org(slug)
    chat = supervisor.read_chat(org, nid, last=1000)
    return sum(1 for m in chat["messages"]
               if m.get("role") == "user" and needle in (m.get("text") or ""))


def chat_before(slug: str, nid: str, first: str, second: str) -> None:
    chat = supervisor.read_chat(store.load_org(slug), nid, last=1000)
    rows = [str(m.get("text") or "") for m in chat["messages"]]
    a = next((i for i, text in enumerate(rows) if first in text), -1)
    b = next((i for i, text in enumerate(rows) if second in text), -1)
    if a < 0 or b < 0 or a >= b:
        raise AssertionError(
            f"wanted {first!r} before {second!r}; indices {(a, b)}, "
            f"rows={rows!r}")


def delivering(slug: str, nid: str) -> list[dict]:
    """Journal batches still UNCONFIRMED — what `_fold_back_undelivered` reads
    to decide a message was never consumed."""
    org = store.load_org(slug)
    return list((org.d.get("delivering") or {}).get(nid) or [])


def mailbox(slug: str, nid: str) -> list[dict]:
    org = store.load_org(slug)
    return list((org.d.get("mail") or {}).get(nid) or [])


def folds(slug: str, nid: str) -> list[dict]:
    org = store.load_org(slug)
    return [e for e in (org.d.get("steered_log") or {}).get(nid, [])
            if e.get("fold")]


def durable_steers(slug: str, nid: str) -> list[dict]:
    org = store.load_org(slug)
    return [e for e in (org.d.get("steered_log") or {}).get(nid, [])
            if not e.get("fold")]


def pending_stages(slug: str, nid: str) -> dict[str, str | None]:
    """body → stage, from the SAME projection the desk fetches."""
    chat = api.node_chat(slug, nid)
    return {m["body"]: m.get("stage") for m in chat["pending_mail"]}


def carriers_owned(slug: str, nid: str, box: dict) -> list:
    """Every in-memory carrier that still exists after a turn: what the queue
    holds, plus what the turn's own exit already popped and handed back."""
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        queued = list(st["queue"])
    return ([box["follow"]] if box.get("follow") else []) + queued


def age_batches(slug: str, nid: str, seconds: float = 60.0) -> None:
    """Push every `delivering` batch's stamp into the past, past the strand
    hysteresis (`STRANDED_GRACE_S`): a batch nobody owns is only CALLED
    stranded once it has been unowned for longer than the benign windows the
    killswitch and the two-phase steer decision open (review round 1)."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                          time.gmtime(time.time() - seconds))
    with store.DOC_LOCK:
        org = store.load_org(slug)
        for b in (org.d.get("delivering") or {}).get(nid, []):
            b["at"] = stamp
        store.save_org(org)


def drain_two_in_order(slug: str, nid: str, first: str, second: str
                       ) -> tuple[dict, dict]:
    """Two user messages through the real doors, in this order, each drained
    into its own `delivering` batch the way `send_message` drains a steer —
    returns the two carriers a lane would be holding."""
    out = []
    for body in (first, second):
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.post_mail(USER, nid, body, kind="message")
            store.save_org(org)
        etext, tok, _ = supervisor._envelope(slug, nid, "(orgtree) mail above",
                                             base_view="", view_out=[])
        out.append({"toks": [tok], "text": etext, "view": body, "ping": True})
    return out[0], out[1]


def main() -> int:
    supervisor.CODEX_STEER_POLL = NEVER_POLL

    print("§1 the strand window: a message in the last second of a codex turn")
    scenario("stall", "fake-thread-strand-one", stall=STALL_S)
    slug, nid = mkorg("strand")
    st = supervisor.state(slug, nid)
    box = turn_in_background(slug, nid, "the turn that ends before the pump polls")
    resp = wait_responding(slug, nid)
    routed = post_and_steer(slug, nid, "LATE-ONE the message in the last second")
    stage_mid = pending_stages(slug, nid).get("LATE-ONE the message in the last second")
    box["thread"].join(timeout=90)
    with supervisor._state_lock:
        steer_after = list(st.get("steer") or [])
    owned = carriers_owned(slug, nid, box)
    stage_after = pending_stages(slug, nid).get("LATE-ONE the message in the last second")
    fold_rows = folds(slug, nid)

    check("the fixture reached the seam (anti-vacuity): the turn was responding "
          "and the message went through the STEER door",
          lambda: eq((resp, bool(routed.get("steering")), box.get("error")),
                     (True, True, None), "responding + steering + no turn error"))
    check("while the turn ran, the receipt said `steer` — owned, mid-task",
          lambda: eq(stage_mid, "steer", "stage during the turn"))
    check("THE FIX: the steer store is EMPTY once the turn has ended — nothing "
          "waits in RAM for a poll that will never come",
          lambda: eq(steer_after, [], "steer store after the turn"))
    check("…and the carrier is OWNED: exactly one in-memory carrier, tokens "
          "included, handed to the turn machinery as the next thing to run",
          lambda: eq([bool(isinstance(c, dict) and c.get("toks")) for c in owned],
                     [True], "owned carriers carrying their journal tokens"))
    check("the miss is RECEIPTED where it happened: one fold row at `turn exit` "
          "naming the pump's reason, not the claude hook's",
          lambda: eq([(f.get("where"), "steer pump" in (f.get("text") or ""))
                      for f in fold_rows],
                     [("turn exit", True)], "fold rows"))
    check("the durable batch stays journaled (the carrier still owes it), so a "
          "crash here folds the mail back instead of losing it",
          lambda: eq(len(delivering(slug, nid)), 1, "unconfirmed batches"))
    check("the receipt never says `stranded` — the message is owned throughout",
          lambda: truthy(stage_after in ("steer", "queued", "turn"),
                         f"stage after the turn: {stage_after!r}"))

    print("   …and the very next turn delivers it, once")
    scenario("tool", "fake-thread-strand-one")
    ran = drain_follow(slug, nid, box["follow"])
    check("the folded carrier actually ran as the follow-up turn (anti-vacuity)",
          lambda: eq(ran, 1, "follow-up turns run"))
    check("the message renders EXACTLY ONCE, as the user's own row",
          lambda: eq(rendered_user_rows(slug, nid, "LATE-ONE"), 1, "rendered copies"))
    check("its batch is confirmed: delivery was claimed, and it happened",
          lambda: eq(delivering(slug, nid), [], "unconfirmed batches"))
    check("no phantom steered row — it was delivered as a turn, and the record "
          "says so",
          lambda: eq(len(durable_steers(slug, nid)), 0, "durable steered rows"))
    check("the node is idle with nothing owed: no queue, no steer, no mail",
          lambda: eq((len(st["queue"]), len(st.get("steer") or []),
                      len(mailbox(slug, nid)), bool(st.get("busy"))),
                     (0, 0, 0, False), "idle and clean"))

    print("§2 two messages in the window: both delivered, in order, once each")
    scenario("stall", "fake-thread-strand-two", stall=STALL_S)
    slug2, nid2 = mkorg("two")
    st2 = supervisor.state(slug2, nid2)
    box2 = turn_in_background(slug2, nid2, "the turn two messages will miss")
    resp2 = wait_responding(slug2, nid2)
    r2a = post_and_steer(slug2, nid2, "LATE-A first of two")
    r2b = post_and_steer(slug2, nid2, "LATE-B second of two")
    box2["thread"].join(timeout=90)
    owned2 = carriers_owned(slug2, nid2, box2)

    check("both went through the steer door while the turn was responding",
          lambda: eq((resp2, bool(r2a.get("steering")), bool(r2b.get("steering"))),
                     (True, True, True), "responding + both steering"))
    check("both are owned after the turn, in the order they were sent",
          lambda: eq(["LATE-A" in str(c.get("text")) for c in owned2]
                     + ["LATE-B" in str(c.get("text")) for c in owned2],
                     [True, False, False, True], "owned carriers by text, in order"))
    check("the steer store is empty",
          lambda: eq(list(st2.get("steer") or []), [], "steer store"))
    scenario("tool", "fake-thread-strand-two")
    ran2 = drain_follow(slug2, nid2, box2["follow"])
    check("two follow-up turns ran — one per carrier (anti-vacuity)",
          lambda: eq(ran2, 2, "follow-up turns run"))
    check("A renders once", lambda: eq(rendered_user_rows(slug2, nid2, "LATE-A"), 1, "copies of A"))
    check("B renders once", lambda: eq(rendered_user_rows(slug2, nid2, "LATE-B"), 1, "copies of B"))
    check("A before B — the order the user sent them",
          lambda: chat_before(slug2, nid2, "LATE-A", "LATE-B"))
    check("nothing left unconfirmed", lambda: eq(delivering(slug2, nid2), [], "batches"))

    print("§3 a message during FINALIZATION (responding=False, busy=True) → queued")
    scenario("stall", "fake-thread-final", stall=0.3)
    slug3, nid3 = mkorg("final")
    st3 = supervisor.state(slug3, nid3)
    hook_box: dict = {}
    orig_after = supervisor._after_turn

    def hooked_after(s, n, *a, **k):
        # the codex leg has set responding=False; `_run_one_turn` is still
        # inside its try, so busy is True — the finalization window, exactly
        if s == slug3 and n == nid3 and "routed" not in hook_box:
            with supervisor._state_lock:
                hook_box["responding"] = bool(st3.get("responding"))
                hook_box["busy"] = bool(st3.get("busy"))
            hook_box["routed"] = post_and_steer(slug3, nid3, "FINAL-ONE during finalization")
        return orig_after(s, n, *a, **k)

    supervisor._after_turn = hooked_after
    try:
        box3 = turn_in_background(slug3, nid3, "the turn a message will trail")
        box3["thread"].join(timeout=90)
    finally:
        supervisor._after_turn = orig_after
    owned3 = carriers_owned(slug3, nid3, box3)
    check("the hook fired inside the finalization window (anti-vacuity)",
          lambda: eq((hook_box.get("responding"), hook_box.get("busy")),
                     (False, True), "responding/busy at post time"))
    check("the message took the QUEUE door — not steering, not a fresh turn",
          lambda: eq((bool(hook_box["routed"].get("steering")),
                      hook_box["routed"].get("queued")), (False, 1), "door"))
    check("…and is owned at turn exit",
          lambda: eq(len(owned3), 1, "owned carriers"))
    scenario("tool", "fake-thread-final")
    ran3 = drain_follow(slug3, nid3, box3["follow"])
    check("delivered by the next turn, once",
          lambda: eq((ran3, rendered_user_rows(slug3, nid3, "FINAL-ONE")), (1, 1),
                     "turns run, rendered copies"))
    check("no fold row: it never entered the steer store",
          lambda: eq(folds(slug3, nid3), [], "fold rows"))

    print("§4 the delivery RECEIPT: turn / steer / queued, and the roll-up")
    slug4, nid4 = mkorg("stages")
    st4 = supervisor.state(slug4, nid4)

    def drain_batch(body: str, via: str) -> str:
        with store.DOC_LOCK:
            org = store.load_org(slug4)
            org.post_mail(USER, nid4, body, kind="message")
            mail = org.take_mail(nid4)
            tok = supervisor._journal_drain(org, nid4, mail, None, via)
            store.save_org(org)
        return tok

    tok_turn = drain_batch("STAGE-TURN drained into the turn", "turn")
    tok_steer = drain_batch("STAGE-STEER in the steer store", "steer")
    tok_queue = drain_batch("STAGE-QUEUE behind the busy turn", "steer")
    with supervisor._state_lock:
        st4["busy"] = True
        st4["responding"] = True
        st4["steer"] = [{"toks": [tok_steer], "text": "s", "view": "s"}]
        st4["queue"] = [{"toks": [tok_queue], "text": "q", "view": "q"}]
    stages4 = pending_stages(slug4, nid4)
    payload4 = api.node_chat(slug4, nid4)
    check("a batch riding the running turn's text reads `turn`",
          lambda: eq(stages4.get("STAGE-TURN drained into the turn"), "turn", "stage"))
    check("a batch in a responding turn's steer store reads `steer`",
          lambda: eq(stages4.get("STAGE-STEER in the steer store"), "steer", "stage"))
    check("a batch in the queue behind a busy turn reads `queued`",
          lambda: eq(stages4.get("STAGE-QUEUE behind the busy turn"), "queued", "stage"))
    check("nothing is stranded while a turn owns the node",
          lambda: eq(payload4.get("mail_stranded"), 0, "mail_stranded"))

    print("§5 anti-vacuity: the pre-fix state, planted, IS reported as stranded")
    with supervisor._state_lock:
        st4["busy"] = False
        st4["responding"] = False
        st4["waiting"] = False
        st4["queue"] = []
        # exactly what the live coordinator held at 09:51:38.389Z: a carrier
        # in the steer store, its batch in `delivering`, the node idle
        st4["steer"] = [{"toks": [tok_steer], "text": "s", "view": "s"}]
    young5 = pending_stages(slug4, nid4)
    check("HYSTERESIS: a batch unowned for less than the grace is NOT yet called "
          "stranded — the killswitch and the two-phase steer decision open "
          "windows exactly this shape for a moment (review round 1)",
          lambda: eq((young5.get("STAGE-STEER in the steer store"),
                      young5.get("STAGE-TURN drained into the turn")),
                     ("steer", "turn"), "stages while young"))
    age_batches(slug4, nid4)
    stages5 = pending_stages(slug4, nid4)
    payload5 = api.node_chat(slug4, nid4)
    check("the carrier in RAM with the node idle reads `stranded` once it has "
          "been unowned past the grace — the state §1 proves unreachable is one "
          "the instrument can SEE",
          lambda: eq(stages5.get("STAGE-STEER in the steer store"), "stranded", "stage"))
    check("a turn-drained batch with no turn running is stranded too",
          lambda: eq(stages5.get("STAGE-TURN drained into the turn"), "stranded", "stage"))
    check("the roll-up counts them",
          lambda: eq(payload5.get("mail_stranded"), 3, "mail_stranded"))
    with supervisor._state_lock:
        st4["steer"] = []
        # the same shape one carrier over (review round 2): a carrier in the
        # QUEUE of a node nothing owns is stranded too — `queued` is only
        # honest while something will pop it
        st4["queue"] = [{"toks": [tok_queue], "text": "q", "view": "q"}]
    idle_queue = pending_stages(slug4, nid4)
    check("a queued carrier on an idle node, past the grace, reads `stranded` "
          "— not the benign `queued` (review round 2)",
          lambda: eq(idle_queue.get("STAGE-QUEUE behind the busy turn"),
                     "stranded", "stage"))
    age_batches(slug4, nid4, seconds=0.0)
    young_queue = pending_stages(slug4, nid4)
    check("…and inside the grace it still reads `queued` — the hysteresis "
          "applies to the queue too",
          lambda: eq(young_queue.get("STAGE-QUEUE behind the busy turn"),
                     "queued", "stage"))
    with supervisor._state_lock:
        st4["queue"] = []
    tok_turn  # noqa: B018 — named for the reader; the stages above used it

    print("§6 restart: a stranded carrier's durable batch survives losing RAM")
    scenario("tool", "fake-thread-restart")
    slug6, nid6 = mkorg("restart")
    st6 = supervisor.state(slug6, nid6)
    with store.DOC_LOCK:
        org6 = store.load_org(slug6)
        org6.post_mail(USER, nid6, "REBOOT-ONE the message across a restart", kind="message")
        store.save_org(org6)
    # the pre-fix strand, by hand: drained for a steer, carrier in RAM, idle
    etext, tok6, _ = supervisor._envelope(slug6, nid6, "(orgtree) mail above",
                                          base_view="", view_out=[])
    with supervisor._state_lock:
        st6["steer"] = [{"toks": [tok6], "text": etext, "view": "REBOOT-ONE"}]
    age_batches(slug6, nid6)
    before6 = pending_stages(slug6, nid6)
    check("before the restart the receipt says stranded (the planted defect)",
          lambda: eq(before6.get("REBOOT-ONE the message across a restart"),
                     "stranded", "stage"))
    check("…and the mailbox is EMPTY — the only copy is the journal",
          lambda: eq((len(mailbox(slug6, nid6)), len(delivering(slug6, nid6))),
                     (0, 1), "mailbox, delivering"))
    # the restart: RAM is gone, the org doc is not
    with supervisor._state_lock:
        supervisor._state.pop((slug6, nid6), None)
    supervisor.reconcile(slug6)
    idle6 = wait_idle(slug6, nid6, timeout=90)
    check("reconcile folded the batch back and re-drove the node — a turn ran "
          "and finished (anti-vacuity: idle again after being driven)",
          lambda: truthy(idle6, "node idle after reconcile"))
    check("the message is delivered exactly once",
          lambda: eq(rendered_user_rows(slug6, nid6, "REBOOT-ONE"), 1, "rendered copies"))
    check("nothing owed: mailbox and journal both empty",
          lambda: eq((len(mailbox(slug6, nid6)), len(delivering(slug6, nid6))),
                     (0, 0), "mailbox, delivering"))

    print("§7 the BELT and the ORDER: a lane that forgets to fold, behind a pump "
          "that requeued")
    # A provider leg that ends its turn WITHOUT folding its steer store —
    # what every lane did before D-229, and what a future lane could do
    # again. It also models the one ordering trap: message A was popped by
    # the pump, refused by the provider and requeued to the BACK of the
    # queue; message B arrived later and sits in the steer store. The user
    # sent A then B. The belt in `_run_one_turn`'s finally must (1) move B
    # out of RAM, (2) behind A, and (3) receipt the miss as its own.
    scenario("tool", "fake-thread-belt")
    slug7, nid7 = mkorg("belt")
    st7 = supervisor.state(slug7, nid7)
    real_leg = supervisor._codex_leg
    seen7: dict = {}

    def forgetful_leg(s, n, org, st, text, toks, images=None, turn_view=""):
        if toks:
            supervisor._confirm_delivered(s, n, toks)   # like a real leg
        with supervisor._state_lock:
            st["responding"] = True
        a, b = drain_two_in_order(s, n, "ORDER-A first, refused by the pump",
                                  "ORDER-B second, in the last second")
        with supervisor._state_lock:
            st["queue"].append(a)          # the pump's requeue after a refusal
            st["steer"] = [b]              # the late steer nobody polled
            st["responding"] = False       # …and the lane forgets the fold
        seen7["planted"] = True
        return ({"status": "completed", "total_cost_usd": 0.0,
                 "usage": {"output_tokens": 0}, "duration_ms": 1,
                 "permission_denials": [], "rate_limits": None,
                 "result": "the lane that forgot",
                 "_mcp_tool_count": None, "_mcp_tool_names": None,
                 "_mcp_tool_fingerprint": None,
                 "_cache_usage": {"input_tokens": 0,
                                  "cache_read_input_tokens": 0,
                                  "cache_creation_input_tokens": 0}}, 0)

    supervisor._codex_leg = forgetful_leg
    try:
        follow7 = run_turn(slug7, nid7, {"text": "the turn whose lane forgets",
                                         "view": "the turn whose lane forgets"})
    finally:
        supervisor._codex_leg = real_leg
    with supervisor._state_lock:
        steer7 = list(st7.get("steer") or [])
        queued7 = list(st7["queue"])
    owned7 = ([follow7] if follow7 else []) + queued7
    folds7 = folds(slug7, nid7)
    check("the forgetful lane really planted both carriers (anti-vacuity)",
          lambda: truthy(seen7.get("planted"), "planted"))
    check("THE BELT: the steer store is empty although the lane never folded",
          lambda: eq(steer7, [], "steer store"))
    check("…and both carriers are owned, A BEFORE B — the belt appended the "
          "late steer BEHIND the pump's requeue, not in front of it",
          lambda: eq([str(c.get("view")) for c in owned7],
                     ["ORDER-A first, refused by the pump",
                      "ORDER-B second, in the last second"], "owned order"))
    check("the miss is receipted as the belt's own: `turn boundary`, naming "
          "the missed lane fold",
          lambda: eq([(f.get("where"), "belt" in (f.get("text") or ""))
                      for f in folds7],
                     [("turn boundary", True)], "fold rows"))
    ran7 = drain_follow(slug7, nid7, follow7)
    check("two follow-up turns ran (anti-vacuity)", lambda: eq(ran7, 2, "turns"))
    check("A renders once", lambda: eq(rendered_user_rows(slug7, nid7, "ORDER-A"), 1, "A"))
    check("B renders once", lambda: eq(rendered_user_rows(slug7, nid7, "ORDER-B"), 1, "B"))
    check("A before B — the order the user sent them survives a refusal plus "
          "a late steer",
          lambda: chat_before(slug7, nid7, "ORDER-A", "ORDER-B"))
    check("nothing left unconfirmed", lambda: eq(delivering(slug7, nid7), [], "batches"))

    print("§8 the FOLD, stated once: `_fold_steer` keeps the order, and every "
          "site that ends steering calls it in the same lock take")
    # Review round 2, finding N1: the claude lane had two sites that flipped
    # `responding` off and folded nothing — the phantom drop and the
    # stdin-closed recovery. With `busy` still True a later message took the
    # queue door BEHIND nothing and the exit fold then appended the earlier
    # steer after it: the round-1 fix (fold to the back) inverted the order
    # one lane over. The rule is now one helper, called at every such site.
    st8: dict = {"queue": [{"text": "A", "view": "A"}],
                 "steer": [{"text": "S1", "view": "S1"},
                           {"text": "S2", "view": "S2"}]}
    moved8 = supervisor._fold_steer(st8)
    check("the store empties into the BACK of the queue, oldest first: "
          "[A] + [S1, S2] → [A, S1, S2], and the move is returned for the receipt",
          lambda: eq(([c["view"] for c in st8["queue"]], st8["steer"],
                      [c["view"] for c in moved8]),
                     (["A", "S1", "S2"], [], ["S1", "S2"]), "queue, store, moved"))
    st8e: dict = {"queue": [{"text": "A", "view": "A"}], "steer": []}
    check("an empty store moves nothing and leaves the queue alone",
          lambda: eq((supervisor._fold_steer(st8e),
                      [c["view"] for c in st8e["queue"]]),
                     ([], ["A"]), "moved, queue"))

    import re
    src_lines: list[str] = []
    with open(supervisor.__file__, encoding="utf-8") as f:
        for ln in f:
            # comments stripped BEFORE any window is taken, so commentary
            # above a site cannot move the guard off the code it protects.
            # (A `#` inside a string literal is cut too — harmless, since no
            # site line or `with _state_lock:` line depends on text after
            # one; noted by review round 3.)
            src_lines.append(ln.split("#", 1)[0].rstrip())
    flag_re = re.compile(r'st\[\s*["\']responding["\']\s*\]\s*=\s*False\b')
    sites = [i for i, ln in enumerate(src_lines) if flag_re.search(ln)]

    def indent_of(s: str) -> int:
        return len(s) - len(s.lstrip())

    def folded_in_same_take(lines: list[str], i: int) -> bool:
        """Is `_fold_steer(st)` called inside the `with _state_lock:` block
        that ENCLOSES this `responding = False`? Enclosure is verified, not
        assumed (review round 3): the nearest `with` above at a lower indent
        is the right block only if the site lies before the block's end —
        every line between them blank or indented past the `with`."""
        top = i
        while top >= 0:
            s = lines[top]
            if (s.strip().startswith("with _state_lock")
                    and indent_of(s) < indent_of(lines[i])):
                break
            top -= 1
        if top < 0:
            return False
        end = top + 1
        while end < len(lines) and (
                not lines[end].strip()
                or indent_of(lines[end]) > indent_of(lines[top])):
            end += 1
        if not (top < i < end):
            return False            # the site is OUTSIDE that block
        return any("_fold_steer(st)" in lines[k] for k in range(top, end))

    # the guard guarded: a planted future site OUTSIDE any lock take passed
    # the first version because it only looked upward (review round 3)
    enclosed = ["def f():", "    with _state_lock:",
                '        st["responding"] = False',
                "        leftover = _fold_steer(st)", "    other()"]
    outside = ["def f():", "    with _state_lock:",
               "        leftover = _fold_steer(st)",
               "    if wp_turn is None:", '        st["responding"] = False']
    unfolded_block = ["def f():", "    with _state_lock:",
                      '        st["responding"] = False', "    other()"]
    check("the guard itself is not vacuous: an enclosed site with a fold "
          "passes; a site OUTSIDE the block (after it, at a lower indent) "
          "fails although the block folds; an enclosed site without a fold "
          "fails",
          lambda: eq((folded_in_same_take(enclosed, 2),
                      folded_in_same_take(outside, 4),
                      folded_in_same_take(unfolded_block, 2)),
                     (True, False, False), "guard verdicts"))

    unfolded = [i + 1 for i in sites if not folded_in_same_take(src_lines, i)]
    check("every `responding = False` site in supervisor.py folds the steer "
          "store inside the SAME `with _state_lock:` block — six sites, the "
          "two round-2 recovery sites among them (anti-vacuity: the sites "
          "are counted, so a regex that matches nothing cannot pass)",
          lambda: eq((len(sites) >= 6, unfolded), (True, []),
                     f"(enough sites found: {len(sites)}, unfolded lines)"))

    print("§9 a teardown call that RAISES after the lane's fold (review round 1, "
          "finding 6): the fold has already run and `responding` is off")
    scenario("stall", "fake-thread-teardown", stall=STALL_S)
    slug9, nid9 = mkorg("teardown")
    st9 = supervisor.state(slug9, nid9)
    real_surface = supervisor._mcp_tool_surface_for_owner
    raised9: dict = {}

    def raising_surface(s, n, proc):
        # the first teardown call after the codex leg's fold; the leg's own
        # `finally` is aborted here, so everything after this line is skipped
        if s == slug9 and n == nid9 and "n" not in raised9:
            raised9["n"] = 1
            raise RuntimeError("teardown failed on purpose (§9)")
        return real_surface(s, n, proc)

    supervisor._mcp_tool_surface_for_owner = raising_surface
    try:
        box9 = turn_in_background(slug9, nid9, "the turn whose teardown raises")
        resp9 = wait_responding(slug9, nid9)
        routed9 = post_and_steer(slug9, nid9,
                                 "TEAR-ONE posted before a raising teardown")
        box9["thread"].join(timeout=90)
    finally:
        supervisor._mcp_tool_surface_for_owner = real_surface
    with supervisor._state_lock:
        state9 = (bool(st9.get("responding")), list(st9.get("steer") or []))
    owned9 = carriers_owned(slug9, nid9, box9)
    folds9 = folds(slug9, nid9)
    check("the teardown really raised, after the message had steered in "
          "(anti-vacuity)",
          lambda: eq((resp9, bool(routed9.get("steering")), raised9.get("n"),
                      box9.get("error")),
                     (True, True, 1, None),
                     "responding, steering, raised once, no turn-runner error"))
    check("`responding` is off and the store is empty although the leg's "
          "teardown blew up — the fold ran FIRST",
          lambda: eq(state9, (False, []), "(responding, steer store)"))
    check("the carrier is owned, and the receipt is the LANE's (`turn exit`), "
          "not the belt's — the lane folded before it raised",
          lambda: eq((len(owned9), [f.get("where") for f in folds9]),
                     (1, ["turn exit"]), "owned, fold receipts"))
    scenario("tool", "fake-thread-teardown")
    ran9 = drain_follow(slug9, nid9, box9["follow"])
    check("…and the next turn delivers it once",
          lambda: eq((ran9, rendered_user_rows(slug9, nid9, "TEAR-ONE")),
                     (1, 1), "turns run, rendered copies"))

    print()
    for label, tb in FAIL:
        print(f"--- FAIL: {label}\n{tb}")
    print(f"{PASS} checks passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
