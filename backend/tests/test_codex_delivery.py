"""AUDIT D2+D3 — the Codex reader stays responsive while tools run, and a
steer's outcome is accepted / rejected / UNKNOWN, never a timeout misread as a
refusal.

    python backend/tests/test_codex_delivery.py    (no pytest; plain asserts)

THE TWO DEFECTS (orgtree-audit.md §2, reproduced on the audit snapshot by
protocol_probes.py and delivery_probes.py; ported here to CURRENT source):

  D2  `AppServerClient._pump` answered `item/tool/call` INLINE on the stdout
      reader thread. `_codex_leg._tool_call` is a loopback POST with a 60 s
      timeout, so for the length of any tool the reader consumed nothing: a
      `turn/steer` whose acknowledgement was already on the pipe timed out.
  D3  `CodexTurn.steer` returned False for EVERY CodexServerError — the
      guard's explicit "no such active turn" AND a plain request timeout —
      and `_steer_pump` requeued the carrier on False. An accepted steer whose
      ack was late was therefore delivered AGAIN next turn, with a durable
      receipt that said "steer refused".

Sections:
  §1  runner: a steer is acknowledged WHILE a tool is blocked (D2)
  §2  runner: outcomes are three-way — accepted / rejected / unknown — and a
      late reply resolves an unknown (D3)
  §3  supervisor: the same D2 case through the real `_codex_leg` pump and a
      real local /api/agent that blocks — the steered row lands mid-tool
  §4  supervisor: accepted-then-late-ack delivers ONCE, no requeue, no
      "steer refused" receipt (D3)
  §5  supervisor: unknown at turn end is an EXPLICIT, receipted redelivery,
      never a silent fold — and the carrier says so to the agent
  §6  runner: a tool result still in flight at the turn boundary is retained
      and answered, never dropped or re-run
  §7  supervisor: an INTERRUPT while a steer is unknown resolves at the
      boundary the same way (§5), no leak
  §8  restart: a `delivering` batch marked `attempt.outcome=unknown` folds
      back WITH a receipt

Anti-vacuity: every section asserts the fixture actually reached the seam
(tool entered, steer sent, STEERED[…] echoed) before judging the outcome.
Red on main 752887a for §1, §3 (the ack is seen only after the tool returns)
and §4 (run with FAKECODEX_ACK_DELAY_S > the old 30 s ceiling: duplicate).
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-codexdelivery-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_WARM"] = "0"       # cold spawns only: one process per turn
os.environ["ORGTREE_PORT"] = "9"       # replaced by the local tool server below
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

FAKECODEX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fakecodex.py")
FAKE = [sys.executable, FAKECODEX]
CODEX_HOME = tempfile.mkdtemp(prefix="codexdelivery-home-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME
with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')

from orgtree import codexrun, providers, store, supervisor          # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

assert os.path.realpath(store.DATA_ROOT) == os.path.realpath(DATA), \
    f"store bound to {store.DATA_ROOT!r}, not the throwaway {DATA!r}"
providers._status_cache = None

PASS = 0
FAIL: list[tuple[str, str]] = []
STREAMED: list[dict] = []
supervisor.stream = lambda slug, nid, payload: STREAMED.append(dict(payload))
supervisor.CODEX_STEER_POLL = 0.2

#: the client-side ceiling on a steer ack, when the source under test has one
STEER_TIMEOUT = 0.5
#: how late the impostor acks in §2/§4. Override above 30 to make main red.
ACK_DELAY = float(os.environ.get("FAKECODEX_ACK_DELAY_S", "2.0"))
os.environ["FAKECODEX_ACK_DELAY_S"] = str(ACK_DELAY)


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


def steer(turn, text, timeout=STEER_TIMEOUT, on_late=None):
    """Call the steer under test with a client-side ceiling if the source has
    one; on the old source (no `timeout`, no `on_late`) fall back to the bare
    call so the section fails on BEHAVIOUR (a 30 s wait) not on a signature."""
    try:
        return turn.steer(text, timeout=timeout, on_late=on_late)
    except TypeError:
        return turn.steer(text)


def scenario(name: str, thread: str, **env: str) -> None:
    os.environ["FAKECODEX_SCENARIO"] = name
    os.environ["FAKECODEX_THREAD_ID"] = thread
    for k, v in env.items():
        os.environ[k] = v


# ── runner-level fixtures ────────────────────────────────────────────────────
DYN = [{"type": "function", "name": "probe_tool", "description": "probe",
        "inputSchema": {"type": "object", "properties": {}}}]


def runner_turn(tool_dispatch, on_event=None) -> codexrun.CodexTurn:
    tmp = tempfile.mkdtemp(prefix="codexdelivery-cwd-")
    return codexrun.CodexTurn(
        FAKE, cwd=tmp, model="gpt-fake", effort=None, thread_id=None,
        dynamic_tools=DYN, tool_dispatch=tool_dispatch, on_event=on_event,
        env_extra={k: os.environ[k] for k in os.environ
                   if k.startswith("FAKECODEX_")})


# ── supervisor-level fixtures (the doors test_midturn_mail_ingress uses) ─────
def mkorg(label: str) -> tuple[str, str]:
    org = store.create_org(f"zz codexdelivery {label}")
    r = org.hire(USER, None, "sol", 0, "cx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a codex delivery test agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def run_turn(slug, nid, carrier):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(slug, nid, carrier)


def turn_in_background(slug, nid, text) -> dict:
    box: dict = {"follow": None, "error": None}

    def go():
        try:
            box["follow"] = run_turn(slug, nid, {"text": text, "view": text})
        except Exception:                                        # noqa: BLE001
            box["error"] = traceback.format_exc()
    t = threading.Thread(target=go, daemon=True)
    t.start()
    box["thread"] = t
    return box


def wait_responding(slug, nid, timeout=30.0) -> bool:
    st = supervisor.state(slug, nid)
    end = time.time() + timeout
    while time.time() < end:
        if st.get("responding"):
            return True
        time.sleep(0.02)
    return False


def wait_idle(slug, nid, timeout=60.0) -> bool:
    st = supervisor.state(slug, nid)
    end = time.time() + timeout
    while time.time() < end:
        with supervisor._state_lock:
            if not st.get("busy") and not st.get("waiting"):
                return True
        time.sleep(0.05)
    return False


def post_and_steer(slug, nid, body) -> dict:
    with store.DOC_LOCK:
        org = store.load_org(slug)
        org.post_mail(USER, nid, body, kind="message")
        store.save_org(org)
    return supervisor.send_message(slug, nid, body, view=body, mail_ping=True)


def drain_follow(slug, nid, follow, guard=4) -> int:
    n = 0
    while follow is not None and n < guard:
        n += 1
        follow = supervisor._run_one_turn(slug, nid, follow)
    return n


def rendered_user_rows(slug, nid, needle) -> int:
    chat = supervisor.read_chat(store.load_org(slug), nid, last=1000)
    return sum(1 for m in chat["messages"]
               if m.get("role") == "user" and needle in (m.get("text") or ""))


def delivering(slug, nid) -> list[dict]:
    org = store.load_org(slug)
    return list((org.d.get("delivering") or {}).get(nid) or [])


def mailbox(slug, nid) -> list[dict]:
    org = store.load_org(slug)
    return list((org.d.get("mail") or {}).get(nid) or [])


def log_rows(slug, nid) -> list[dict]:
    org = store.load_org(slug)
    return list((org.d.get("steered_log") or {}).get(nid, []))


def folds(slug, nid) -> list[dict]:
    return [e for e in log_rows(slug, nid) if e.get("fold")]


def durable_steers(slug, nid) -> list[dict]:
    return [e for e in log_rows(slug, nid) if not e.get("fold")]


def wait_for(pred, timeout, step=0.02) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def carriers_owned(slug, nid, box) -> list:
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        queued = list(st["queue"])
    return ([box["follow"]] if box.get("follow") else []) + queued


# ── a REAL local /api/agent whose answer can be held ─────────────────────────
class ToolGate:
    entered = threading.Event()
    release = threading.Event()
    calls: list[dict] = []


class ToolHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        ToolGate.calls.append(body)
        ToolGate.entered.set()
        ToolGate.release.wait(70)
        out = json.dumps({"result": "held tool done"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def start_tool_server() -> HTTPServer:
    srv = HTTPServer(("127.0.0.1", 0), ToolHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["ORGTREE_PORT"] = str(srv.server_port)
    return srv


def main() -> int:
    tool_srv = start_tool_server()
    print(f"   local /api/agent on 127.0.0.1:{tool_srv.server_port} "
          f"(ORGTREE_PORT set after import; store root {store.DATA_ROOT})")

    # ── §1 ───────────────────────────────────────────────────────────────────
    print("§1 runner: the steer ack is read WHILE a tool is blocked (D2)")
    scenario("slow_tool_then_steer", "fake-thread-d2-runner")
    entered, release = threading.Event(), threading.Event()

    def blocking_tool(name, args):
        entered.set()
        release.wait(70)
        return "done"
    turn1 = runner_turn(blocking_tool)
    turn1.start("call the tool")
    truthy(entered.wait(15), "fixture: the tool was never entered")
    t0 = time.time()
    out1 = steer(turn1, "mid-tool orders", timeout=3.0)
    elapsed1 = time.time() - t0
    ack_while_blocked = (not release.is_set()) and elapsed1 < 2.5 and bool(out1)
    release.set()
    res1 = turn1.wait(timeout=30)
    check("fixture reached the seam: the tool was entered before the steer, "
          "and the turn completed after release",
          lambda: eq((entered.is_set(), res1["status"]), (True, "completed"),
                     "entered, status"))
    check("THE FIX (D2): the steer was ACCEPTED while the tool was still "
          "blocked — the reader did not wait for the tool",
          lambda: truthy(ack_while_blocked,
                         f"outcome={out1!r} after {elapsed1:.2f}s, "
                         f"release_set_before_return={release.is_set()}"))
    check("…and the impostor saw the steer inside the turn (STEERED[…] echoed)",
          lambda: truthy("STEERED[mid-tool orders]" in res1["agent_text"],
                         res1["agent_text"]))
    check("…and the tool's answer still reached the model (steered=1)",
          lambda: truthy("tool said: done; steered=1" in res1["agent_text"],
                         res1["agent_text"]))
    check("no unconsumed late response leaks in the client table",
          lambda: eq(dict(turn1.client._responses), {}, "leftover responses"))

    # ── §2 ───────────────────────────────────────────────────────────────────
    print("§2 runner: three-way outcomes, and a late reply resolves unknown (D3)")
    scenario("steer_ack_late", "fake-thread-d3-runner")
    late: list = []
    turn2 = runner_turn(lambda n, a: "unused")
    turn2.start("wait for a steer")
    time.sleep(0.3)                       # the fixture is inside wait_request
    out2 = steer(turn2, "late-ack orders", timeout=STEER_TIMEOUT,
                 on_late=lambda o: late.append(str(o)))
    late_seen = wait_for(lambda: bool(late), ACK_DELAY + 5)
    res2 = turn2.wait(timeout=ACK_DELAY + 30)
    check("fixture: the provider ACCEPTED (STEERED[…] echoed) before any ack",
          lambda: truthy("STEERED[late-ack orders]" in res2["agent_text"],
                         res2["agent_text"]))
    check("THE FIX (D3): a timed-out ack is UNKNOWN — not False, not rejected",
          lambda: eq(str(out2), "unknown", f"outcome (type {type(out2).__name__})"))
    check("…unknown is falsy for a legacy boolean caller (never mistaken for "
          "accepted) but distinct from rejected",
          lambda: eq((bool(out2), str(out2) == "rejected"), (False, False),
                     "truthiness, ==rejected"))
    check("…and the LATE reply upgraded it to accepted through on_late",
          lambda: eq((late_seen, late), (True, ["accepted"]), "late resolution"))
    check("no unconsumed late response leaks in the client table",
          lambda: eq(dict(turn2.client._responses), {}, "leftover responses"))

    print("   control: the guard's explicit refusal is REJECTED, not unknown")
    scenario("steer_refuse", "fake-thread-d3-refuse")
    turn2b = runner_turn(lambda n, a: "unused")
    turn2b.start("refuse me")
    time.sleep(0.3)
    out2b = steer(turn2b, "too late", timeout=5.0)
    res2b = turn2b.wait(timeout=30)
    check("explicit JSON-RPC error → rejected (and falsy)",
          lambda: eq((str(out2b), bool(out2b)), ("rejected", False), "outcome"))
    check("…the turn itself completed (fixture ran)",
          lambda: eq(res2b["status"], "completed", "status"))

    print("   control: a prompt ack is ACCEPTED")
    scenario("steer", "fake-thread-d3-accept")
    turn2c = runner_turn(lambda n, a: "unused")
    turn2c.start("accept me")
    time.sleep(0.3)
    out2c = steer(turn2c, "fine", timeout=5.0)
    res2c = turn2c.wait(timeout=30)
    check("prompt JSON-RPC result → accepted (and truthy)",
          lambda: eq((str(out2c), bool(out2c)), ("accepted", True), "outcome"))
    check("…and it landed (STEERED[fine])",
          lambda: truthy("STEERED[fine]" in res2c["agent_text"], res2c["agent_text"]))

    print("   control: classification is STRUCTURAL — message text is never evidence")
    _cls = getattr(codexrun, "classify_steer_error", None)
    _clr = getattr(codexrun, "classify_steer_result", None)
    check("an internal error naming expectedTurnId is UNKNOWN, not rejected "
          "(an internal failure can follow the append)",
          lambda: eq((str(_cls(-32603, "Persistence failed after expectedTurnId validation")),
                      str(_cls(-32603, "Internal failure while persisting input to active turn")),
                      str(_cls(-32000, "no such active turn"))),
                     ("unknown", "unknown", "unknown"), "ambiguous codes"))
    check("the spec's never-applied codes are rejected; an unknown code is unknown",
          lambda: eq([str(_cls(c, "x")) for c in (-32700, -32600, -32601, -32602, None, 12345)],
                     ["rejected"] * 4 + ["unknown"] * 2, "codes"))
    check("a result is accepted only when it names THIS turn; other/missing → unknown",
          lambda: eq((str(_clr({"turnId": "T1"}, "T1")), str(_clr({"turnId": "T2"}, "T1")),
                      str(_clr({}, "T1")), str(_clr(None, "T1")), str(_clr({"turnId": "T1"}, None))),
                     ("accepted", "unknown", "unknown", "unknown", "unknown"), "results"))

    print("   control: a prompt ack naming the WRONG turn is unknown on the wire too")
    scenario("steer_ack_wrong_turn", "fake-thread-d3-wrongturn", FAKECODEX_ACK_TURNID="some-other-turn")
    turn2d = runner_turn(lambda n, a: "unused")
    turn2d.start("ack me wrongly")
    time.sleep(0.3)
    out2d = steer(turn2d, "whose turn", timeout=5.0)
    turn2d.wait(timeout=30)
    check("wrong-turn acknowledgement → unknown, with the shape in the reason",
          lambda: eq((str(out2d), "some-other-turn" in getattr(out2d, "reason", "")),
                     ("unknown", True), f"outcome {out2d!r}"))

    # ── §3 ───────────────────────────────────────────────────────────────────
    print("§3 supervisor: steered row lands while the REAL tool POST is held (D2)")
    scenario("slow_tool_then_steer", "fake-thread-d2-super")
    ToolGate.entered.clear()
    ToolGate.release.clear()
    ToolGate.calls.clear()
    STREAMED.clear()
    slug3, nid3 = mkorg("d2")
    box3 = turn_in_background(slug3, nid3, "call the held tool")
    resp3 = wait_responding(slug3, nid3)
    tool_in3 = ToolGate.entered.wait(20)
    routed3 = post_and_steer(slug3, nid3, "D2-MSG while the tool is held")
    landed_while_held = wait_for(
        lambda: any(p.get("kind") == "steered" and "D2-MSG" in (p.get("text") or "")
                    for p in STREAMED) and not ToolGate.release.is_set(), 4.0)
    durable_while_held = bool(durable_steers(slug3, nid3)) and not ToolGate.release.is_set()
    ToolGate.release.set()
    box3["thread"].join(timeout=90)
    check("fixture reached the seam: responding, tool POST held, steer door",
          lambda: eq((resp3, tool_in3, bool(routed3.get("steering")), box3["error"]),
                     (True, True, True, None), "responding, tool entered, steering, error"))
    check("THE FIX (D2): the steered desk frame went out while the tool was "
          "still held — the pump's ack was not behind the tool",
          lambda: truthy(landed_while_held, "no steered frame within 4 s of posting"))
    check("…and the durable steered row too",
          lambda: truthy(durable_while_held, "no durable steered row while held"))
    check("delivered ONCE: no fold, no requeue, nothing owed",
          lambda: eq((len(folds(slug3, nid3)), carriers_owned(slug3, nid3, box3),
                      delivering(slug3, nid3), len(durable_steers(slug3, nid3))),
                     (0, [], [], 1), "folds, owned, delivering, steered rows"))
    check("the tool call actually went through the local /api/agent (anti-vacuity)",
          lambda: eq([(c.get("org"), c.get("node")) for c in ToolGate.calls],
                     [(slug3, nid3)], "tool calls seen by the local server"))

    # ── §4 ───────────────────────────────────────────────────────────────────
    print(f"§4 supervisor: accepted, ack {ACK_DELAY:.0f}s late → delivered ONCE (D3)")
    scenario("steer_ack_late", "fake-thread-d3-super")
    if hasattr(codexrun, "STEER_TIMEOUT"):
        codexrun.STEER_TIMEOUT = STEER_TIMEOUT
    STREAMED.clear()
    slug4, nid4 = mkorg("d3")
    st4 = supervisor.state(slug4, nid4)
    box4 = turn_in_background(slug4, nid4, "wait for a steer")
    resp4 = wait_responding(slug4, nid4)
    time.sleep(0.3)
    routed4 = post_and_steer(slug4, nid4, "D3-MSG accepted then late ack")
    # the durable uncertainty mark must exist BEFORE any acknowledgement
    # could arrive (the ack is ACK_DELAY away): a crash in this window must
    # leave a trace for the restart fold-back (review 2026-09-05)
    marked_before_ack = wait_for(
        lambda: any((b.get("attempt") or {}).get("outcome") == "unknown"
                    for b in delivering(slug4, nid4)), min(ACK_DELAY - 0.3, 1.5))
    box4["thread"].join(timeout=ACK_DELAY + 90)
    owned4 = carriers_owned(slug4, nid4, box4)
    rows4 = log_rows(slug4, nid4)
    check("fixture reached the seam: responding, steer door, turn ended clean",
          lambda: eq((resp4, bool(routed4.get("steering")), box4["error"]),
                     (True, True, None), "responding, steering, error"))
    check("fixture: the provider ACCEPTED it inside the turn (STEERED[…] on the desk)",
          lambda: truthy(any("STEERED[" in (p.get("text") or "") and "D3-MSG" in
                             (p.get("text") or "") for p in STREAMED),
                         "no STEERED echo on the desk"))
    check("the batch carried attempt.outcome=unknown BEFORE the ack could arrive",
          lambda: truthy(marked_before_ack, "no unknown attempt mark before the ack"))
    check("THE FIX (D3): NOTHING requeued — no carrier owned after the turn",
          lambda: eq(owned4, [], "owned carriers"))
    check("…one durable steered row (delivered), batch confirmed",
          lambda: eq((len(durable_steers(slug4, nid4)), delivering(slug4, nid4)),
                     (1, []), "steered rows, delivering"))
    check("…and NO receipt says `steer refused`",
          lambda: eq([r for r in rows4 if "refused" in (r.get("text") or "")], [],
                     "refused receipts"))
    check("the unknown interval was RECEIPTED as unknown, then resolved accepted",
          lambda: truthy(any(r.get("fold") and r.get("outcome") == "unknown"
                             for r in rows4) and
                         any(r.get("fold") and r.get("outcome") == "accepted"
                             for r in rows4),
                         f"rows={rows4!r}"))
    check("the message renders exactly once as the user's row",
          lambda: eq(rendered_user_rows(slug4, nid4, "D3-MSG"), 1, "rendered copies"))
    check("node idle with nothing owed",
          lambda: eq((len(st4["queue"]), len(st4.get("steer") or []),
                      len(mailbox(slug4, nid4)), bool(st4.get("busy"))),
                     (0, 0, 0, False), "idle and clean"))

    # ── §5 ───────────────────────────────────────────────────────────────────
    print("§5 supervisor: unknown at turn end → explicit, receipted redelivery")
    scenario("steer_ack_never", "fake-thread-d3-never", FAKECODEX_STALL_S="1.5")
    STREAMED.clear()
    slug5, nid5 = mkorg("never")
    st5 = supervisor.state(slug5, nid5)
    box5 = turn_in_background(slug5, nid5, "wait for a steer that is never acked")
    resp5 = wait_responding(slug5, nid5)
    time.sleep(0.3)
    routed5 = post_and_steer(slug5, nid5, "NEVER-MSG unknown outcome")
    box5["thread"].join(timeout=90)
    owned5 = carriers_owned(slug5, nid5, box5)
    rows5 = log_rows(slug5, nid5)
    check("fixture reached the seam: responding, steer door, STEERED echoed, clean end",
          lambda: eq((resp5, bool(routed5.get("steering")), box5["error"],
                      any("STEERED[" in (p.get("text") or "") for p in STREAMED)),
                     (True, True, None, True), "responding, steering, error, echo"))
    check("the carrier is OWNED for redelivery: exactly one, tokens intact",
          lambda: eq([bool(isinstance(c, dict) and c.get("toks")) for c in owned5],
                     [True], "owned carriers"))
    check("…its TEXT tells the agent it is a redelivery of an unknown outcome",
          lambda: truthy(owned5 and "[ORGTREE REDELIVERY" in str(owned5[0].get("text")),
                         f"carrier text: {str(owned5[0].get('text') if owned5 else '')[:200]!r}"))
    check("…its VIEW is untouched (no preface on what the user sees) and the "
          "attempt metadata is on the carrier once",
          lambda: eq((("[ORGTREE REDELIVERY" in str(owned5[0].get("view")),
                       "NEVER-MSG" in str(owned5[0].get("view")),
                       (owned5[0].get("redelivery") or {}).get("attempts"))
                      if owned5 else None),
                     (False, True, 1), "view preface, view body, attempts"))
    check("the receipt says UNKNOWN + redelivered, never `steer refused`",
          lambda: eq(([r.get("outcome") for r in rows5 if r.get("fold")],
                      [r for r in rows5 if "refused" in (r.get("text") or "")]),
                     (["unknown", "unknown"], []) if len([r for r in rows5 if r.get("fold")]) == 2
                     else ([r.get("outcome") for r in rows5 if r.get("fold")], []),
                     "fold outcomes, refused rows"))
    check("…the redelivery decision is its own receipt row",
          lambda: truthy(any(r.get("fold") and "redeliver" in (r.get("text") or "")
                             for r in rows5), f"rows={rows5!r}"))
    check("nothing committed as delivered: no steered row, batch still owed",
          lambda: eq((len(durable_steers(slug5, nid5)), len(delivering(slug5, nid5))),
                     (0, 1), "steered rows, delivering"))
    print("   …and the next turn delivers it, once")
    scenario("tool", "fake-thread-d3-never")
    ran5 = drain_follow(slug5, nid5, box5["follow"])
    check("the redelivery ran as the follow-up turn (anti-vacuity)",
          lambda: eq(ran5, 1, "follow-up turns"))
    check("rendered once as the user's row; batch confirmed; node clean",
          lambda: eq((rendered_user_rows(slug5, nid5, "NEVER-MSG"),
                      delivering(slug5, nid5), len(st5["queue"]),
                      bool(st5.get("busy"))),
                     (1, [], 0, False), "rendered, delivering, queue, busy"))

    # ── §6 ───────────────────────────────────────────────────────────────────
    print("§6 runner: a tool result in flight at the boundary is retained + answered")
    probe6 = os.path.join(tempfile.mkdtemp(prefix="codexdelivery-late-"), "late.json")
    scenario("tool_inflight_at_end", "fake-thread-inflight", FAKECODEX_LATEPROBE=probe6)
    runs6: list = []

    def slow_tool(name, args):
        runs6.append(name)
        time.sleep(0.4)
        return "late answer"
    turn6 = runner_turn(slow_tool)
    turn6.start("call and leave")
    res6 = turn6.wait(timeout=30)
    late6 = res6.get("late_tool_results")
    check("fixture: the turn completed with the tool still running",
          lambda: eq((res6["status"], runs6), ("completed", ["probe_tool"]),
                     "status, dispatched tools"))
    check("the late result is RETAINED on the turn result, not dropped",
          lambda: eq([(r.get("tool"), r.get("text"), r.get("ok")) for r in (late6 or [])],
                     [("probe_tool", "late answer", True)], "late_tool_results"))
    check("…and nothing is still in flight after the drain",
          lambda: eq(res6.get("inflight_tools"), 0, "inflight_tools"))
    def _server_saw():
        if not os.path.exists(probe6):
            return None
        rows = [json.loads(ln) for ln in open(probe6, encoding="utf-8")
                if ln.strip()]
        return [((r.get("result") or {}).get("contentItems") or [{}])[0].get("text")
                for r in rows]
    check("…and the answer still went to the server (same binding, pipe open)",
          lambda: eq(_server_saw(), ["late answer"], "server-side receipt"))
    check("the tool ran exactly once", lambda: eq(len(runs6), 1, "runs"))

    # ── §7 ───────────────────────────────────────────────────────────────────
    print("§7 supervisor: interrupt during an unknown steer resolves at the boundary")
    scenario("steer_ack_never", "fake-thread-d3-interrupt", FAKECODEX_STALL_S="20")
    STREAMED.clear()
    slug7, nid7 = mkorg("interrupt")
    st7 = supervisor.state(slug7, nid7)
    box7 = turn_in_background(slug7, nid7, "wait; be interrupted")
    resp7 = wait_responding(slug7, nid7)
    time.sleep(0.3)
    routed7 = post_and_steer(slug7, nid7, "INT-MSG unknown then interrupted")
    unknown_seen7 = wait_for(lambda: any(r.get("fold") and r.get("outcome") == "unknown"
                                         for r in log_rows(slug7, nid7)),
                             STEER_TIMEOUT + 5)
    irr7 = supervisor.interrupt_turn(slug7, nid7)
    box7["thread"].join(timeout=60)
    owned7 = carriers_owned(slug7, nid7, box7)
    rows7 = log_rows(slug7, nid7)
    check("fixture reached the seam: responding, steer door, unknown receipted, "
          "interrupt accepted, turn ended",
          lambda: eq((resp7, bool(routed7.get("steering")), unknown_seen7,
                      bool(irr7.get("interrupted")),
                      box7["thread"].is_alive(), box7["error"]),
                     (True, True, True, True, False, None), "seam"))
    check("the carrier is owned for redelivery with the redelivery preface",
          lambda: eq([bool(isinstance(c, dict) and c.get("toks") and
                           "[ORGTREE REDELIVERY" in str(c.get("text"))) for c in owned7],
                     [True], "owned carriers"))
    check("no commit, no `steer refused`, redelivery receipted",
          lambda: eq((len(durable_steers(slug7, nid7)),
                      [r for r in rows7 if "refused" in (r.get("text") or "")],
                      any(r.get("fold") and "redeliver" in (r.get("text") or "")
                          for r in rows7)),
                     (0, [], True), "steered rows, refused, redelivery receipt"))
    check("nothing left in the steer store or limbo",
          lambda: eq((st7.get("steer") or [], st7.get("steer_limbo") or []),
                     ([], []), "steer, limbo"))

    # ── §8 ───────────────────────────────────────────────────────────────────
    print("§8 restart: an unknown-marked batch folds back WITH a receipt")
    scenario("tool", "fake-thread-restart-unknown")
    slug8, nid8 = mkorg("restart")
    with store.DOC_LOCK:
        org8 = store.load_org(slug8)
        org8.post_mail(USER, nid8, "REBOOT-UNKNOWN across a restart", kind="message")
        store.save_org(org8)
    etext8, tok8, _ = supervisor._envelope(slug8, nid8, "(orgtree) mail above",
                                           base_view="", view_out=[])
    marker = getattr(supervisor, "_note_steer_attempt", None)
    if marker is not None:
        marker(slug8, nid8, [tok8], "unknown")
    with supervisor._state_lock:
        supervisor._state.pop((slug8, nid8), None)
    supervisor.reconcile(slug8)
    idle8 = wait_idle(slug8, nid8, timeout=90)
    rows8 = log_rows(slug8, nid8)
    check("the attempt mark exists and was applied (anti-vacuity)",
          lambda: truthy(marker is not None, "no _note_steer_attempt in supervisor"))
    check("reconcile re-drove the node and it finished",
          lambda: truthy(idle8, "node idle after reconcile"))
    check("delivered exactly once, nothing owed",
          lambda: eq((rendered_user_rows(slug8, nid8, "REBOOT-UNKNOWN"),
                      len(mailbox(slug8, nid8)), len(delivering(slug8, nid8))),
                     (1, 0, 0), "rendered, mailbox, delivering"))
    check("…and the restart wrote a receipt naming the unknown attempt",
          lambda: truthy(any(r.get("fold") and r.get("outcome") == "unknown" and
                             "restart" in (r.get("text") or "") for r in rows8),
                         f"rows={rows8!r}"))

    # ── §9 ───────────────────────────────────────────────────────────────────
    print("§9 runner: a replayed request id runs the tool ONCE, answers twice")
    scenario("tool_replay_id", "fake-thread-dup")
    runs9: list = []

    def counted_tool(name, args):
        runs9.append(args.get("message"))
        return f"ran#{len(runs9)}"
    turn9 = runner_turn(counted_tool)
    turn9.start("replay me")
    res9 = turn9.wait(timeout=30)
    _tr = getattr(turn9.client, "tool_records", None)
    recs9 = (_tr(epoch=turn9.client.epoch - 1) or _tr()) if _tr else []
    check("the impostor received BOTH answers, identical, from one execution",
          lambda: truthy("first='ran#1' replay='ran#1'" in res9["agent_text"],
                         res9["agent_text"]))
    check("the tool ran exactly once", lambda: eq(runs9, ["dup"], "runs"))
    check("…and the record counts the replay",
          lambda: eq([r.get("replays") for r in recs9 if r.get("tool") == "probe_tool"],
                     [1], "replays"))

    def _section10():
        global PASS
        probe10 = os.path.join(tempfile.mkdtemp(prefix="codexdelivery-epoch-"), "late.json")
        scenario("tool_inflight_at_end", "fake-thread-epoch", FAKECODEX_LATEPROBE=probe10)
        sinkA: list = []
        sinkB: list = []
        gate10 = threading.Event()

        def outlasting_tool(name, args):
            gate10.wait(30)                    # released only after turn B ran
            return "A's late answer"
        tmp10 = tempfile.mkdtemp(prefix="codexdelivery-cwd-")
        envx = {k: os.environ[k] for k in os.environ if k.startswith("FAKECODEX_")}
        turnA = codexrun.CodexTurn(FAKE, cwd=tmp10, model="gpt-fake", effort=None,
                                   thread_id=None, dynamic_tools=DYN,
                                   tool_dispatch=outlasting_tool, env_extra=envx,
                                   on_late_tool_result=sinkA.append)
        turnA.start("call and leave (A)")
        resA = turnA.wait(timeout=30, close_client=False)
        shared = turnA.client
        shared.unbind()                                     # A's epoch ends
        turnB = codexrun.CodexTurn(FAKE, cwd=tmp10, model="gpt-fake", effort=None,
                                   thread_id=turnA.thread_id, dynamic_tools=DYN,
                                   tool_dispatch=lambda n, a: "B's answer",
                                   client=shared, on_late_tool_result=sinkB.append)
        turnB.start("call and leave (B)")
        resB = turnB.wait(timeout=30, close_client=False)
        gate10.set()                                        # now A's tool ends
        got_a = wait_for(lambda: bool(sinkA), 10)
        time.sleep(0.3)
        server_saw = []
        if os.path.exists(probe10):
            server_saw = [((json.loads(ln).get("result") or {}).get("contentItems") or [{}])[0].get("text")
                          for ln in open(probe10, encoding="utf-8") if ln.strip()]
        shared.close()
        check("fixture: A ended with its tool still running (drain expired), B ran "
              "on the same process and finished",
              lambda: eq((resA["status"], resA["inflight_tools"], resB["status"]),
                         ("completed", 1, "completed"), "A status, A inflight, B status"))
        check("A's late result reached A's sink, exactly once, and NOT B's",
              lambda: eq(([r.get("text") for r in sinkA], sinkB and
                          [r.get("text") for r in sinkB if "A's" in str(r.get("text"))]),
                         (["A's late answer"], []), "sinks"))
        check("…marked as never written to the wire (wire=False)",
              lambda: eq([r.get("wire") for r in sinkA], [False], "wire flag"))
        check("the server saw ONLY B's answer — A's was not written under B's binding",
              lambda: eq(server_saw, ["B's answer"], "server-side answers"))
        check("A's sink fired at all (anti-vacuity)",
              lambda: truthy(got_a, "A's sink never fired"))

    # ── §10 ──────────────────────────────────────────────────────────────────
    print("§10 runner: EPOCH SWITCH — a worker outliving the drain reports to "
          "its own turn, never to the wire of the next one")
    import inspect
    has_sink = "on_late_tool_result" in inspect.signature(
        codexrun.CodexTurn.__init__).parameters
    check("the runner has a per-turn late-result sink at all (old source: none)",
          lambda: truthy(has_sink, "CodexTurn has no on_late_tool_result"))
    if has_sink:
        _section10()

    # ── §11 ──────────────────────────────────────────────────────────────────
    print("§11 runner: OVERLOAD — the excess is answered at once, the reader "
          "still acks a steer mid-flood")
    scenario("tool_flood", "fake-thread-flood", FAKECODEX_FLOOD_N="25")
    hold11 = threading.Event()
    ran11: list = []

    def held_tool(name, args):
        ran11.append(args.get("message"))
        hold11.wait(10)
        return "ran " + str(args.get("message"))
    turn11 = runner_turn(held_tool)
    turn11.start("flood me")
    _inflight = getattr(turn11.client, "inflight_tools", lambda: 0)
    wait_for(lambda: _inflight() >= 20, 10)
    t11 = time.time()
    out11 = steer(turn11, "mid-flood", timeout=5.0)
    steer_s = time.time() - t11
    hold11.set()
    res11 = turn11.wait(timeout=60)
    limit = (getattr(codexrun, "CODEX_TOOL_WORKERS", 0)
             + getattr(codexrun, "CODEX_TOOL_QUEUE", 0))
    check("the steer was acked while 20+ tool calls were admitted and held",
          lambda: eq((str(out11), steer_s < 4.0), ("accepted", True),
                     f"outcome, fast ({steer_s:.2f}s)"))
    check(f"exactly workers+queue={limit} calls ran; the rest got an explicit "
          f"overload answer immediately; none unanswered",
          lambda: truthy(f"n=25 ran={limit} overload={25 - limit} unanswered=0 "
                         f"steer=acked" in res11["agent_text"], res11["agent_text"]))
    check("the client counted the overload answers",
          lambda: eq(getattr(turn11.client, "overloaded", None), 25 - limit,
                     "overloaded"))
    check("…and never ran more than the worker bound at once (admission held "
          "the queue, the workers held the rest)",
          lambda: eq(len(ran11), limit, "tools that ran"))

    # ── §12 ──────────────────────────────────────────────────────────────────
    print("§12 supervisor (warm): a late ack AFTER the turn-end fold is receipted, "
          "the redelivery stands, and the transcript explains the repeat")
    from orgtree import warmpool                                   # noqa: E402
    os.environ["ORGTREE_WARM"] = "1"
    os.environ["ORGTREE_WARM_POLL"] = "3600"
    scenario("steer_ack_after_end", "fake-thread-after-end", FAKECODEX_ACK_DELAY_S="1.5")
    STREAMED.clear()
    slug12, nid12 = mkorg("after-end")
    warmpool.keeper_pass_now()
    with warmpool._pool_lock:
        pre12 = warmpool._pool.get((slug12, nid12))
    warm12 = isinstance(pre12, warmpool.CodexWarmProc)
    st12 = supervisor.state(slug12, nid12)
    box12 = turn_in_background(slug12, nid12, "wait; end; ack late")
    resp12 = wait_responding(slug12, nid12)
    time.sleep(0.3)
    routed12 = post_and_steer(slug12, nid12, "AFTER-END-MSG acked after the fold")
    box12["thread"].join(timeout=60)
    folded12 = any(r.get("where") == "turn exit" and r.get("outcome") == "unknown"
                   for r in log_rows(slug12, nid12))
    late12 = wait_for(lambda: any(r.get("where") == "steer late-ack"
                                  for r in log_rows(slug12, nid12)), 8.0)
    rows12 = log_rows(slug12, nid12)
    with warmpool._pool_lock:
        parked12 = warmpool._pool.get((slug12, nid12))
    check("fixture reached the seam: warm process claimed, responding, steer "
          "door, unknown at turn exit, process parked for the late ack",
          lambda: eq((warm12, resp12, bool(routed12.get("steering")), box12["error"],
                      folded12, isinstance(parked12, warmpool.CodexWarmProc)),
                     (True, True, True, None, True, True), "seam"))
    check("the late acceptance was RECEIPTED against the redelivery decision "
          "(recovered from the queue, or explained as a possible repeat)",
          lambda: truthy(late12 and any(
              r.get("where") == "steer late-ack" and r.get("outcome") == "accepted"
              and ("recovered" in (r.get("text") or "") or "twice" in (r.get("text") or ""))
              for r in rows12), f"rows={rows12!r}"))
    late_row12 = next((r for r in rows12 if r.get("where") == "steer late-ack"), {})
    recovered12 = "recovered" in (late_row12.get("text") or "")
    scenario("tool", "fake-thread-after-end")
    ran12 = drain_follow(slug12, nid12, box12["follow"])
    copies12 = rendered_user_rows(slug12, nid12, "AFTER-END-MSG")
    check("the outcome is CONSISTENT with the receipt: recovered → delivered once "
          "as a steer and no follow-up carrier; gone → the follow-up ran and the "
          "repeat is the one the receipt announced",
          lambda: eq((copies12, ran12, len(durable_steers(slug12, nid12))),
                     (1, 0, 1) if recovered12 else (2, 1, 1),
                     f"rendered copies, follow-up turns, steered rows "
                     f"(recovered={recovered12})"))
    check("nothing owed afterwards",
          lambda: eq((delivering(slug12, nid12), len(st12["queue"]),
                      st12.get("steer_limbo") or []), ([], 0, []), "owed"))
    warmpool.kill_org(slug12, "suite-teardown")
    os.environ["ORGTREE_WARM"] = "0"

    # ── §13 ──────────────────────────────────────────────────────────────────
    print("§13 supervisor: an AMBIGUOUS late reply keeps the outcome unknown — "
          "redelivered at turn end, never receipted as refused")
    scenario("steer_ack_late_error", "fake-thread-late-error", FAKECODEX_ACK_DELAY_S="1.5")
    if hasattr(codexrun, "STEER_TIMEOUT"):
        codexrun.STEER_TIMEOUT = STEER_TIMEOUT
    STREAMED.clear()
    slug13, nid13 = mkorg("late-error")
    st13 = supervisor.state(slug13, nid13)
    box13 = turn_in_background(slug13, nid13, "wait; answer late with an internal error")
    resp13 = wait_responding(slug13, nid13)
    time.sleep(0.3)
    routed13 = post_and_steer(slug13, nid13, "LATE-ERR-MSG ambiguous late reply")
    box13["thread"].join(timeout=60)
    owned13 = carriers_owned(slug13, nid13, box13)
    rows13 = log_rows(slug13, nid13)
    check("fixture reached the seam: responding, steer door, STEERED echoed, clean end",
          lambda: eq((resp13, bool(routed13.get("steering")), box13["error"],
                      any("STEERED[" in (p.get("text") or "") for p in STREAMED)),
                     (True, True, None, True), "seam"))
    check("the late ambiguous reply was receipted as STILL UNKNOWN",
          lambda: truthy(any(r.get("where") == "steer late-unknown" and
                             r.get("outcome") == "unknown" for r in rows13),
                         f"rows={rows13!r}"))
    check("NO receipt says rejected/refused, and nothing was committed",
          lambda: eq(([r for r in rows13 if r.get("outcome") == "rejected"
                       or "refus" in (r.get("text") or "")],
                      len(durable_steers(slug13, nid13))), ([], 0),
                     "rejected receipts, steered rows"))
    check("the carrier is owned for redelivery (turn-end decision), tokens intact",
          lambda: eq([bool(isinstance(c, dict) and c.get("toks") and
                           "[ORGTREE REDELIVERY" in str(c.get("text"))) for c in owned13],
                     [True], "owned"))
    scenario("tool", "fake-thread-late-error")
    ran13 = drain_follow(slug13, nid13, box13["follow"])
    check("…and it is delivered once by the next turn, nothing owed",
          lambda: eq((ran13, rendered_user_rows(slug13, nid13, "LATE-ERR-MSG"),
                      delivering(slug13, nid13), len(st13["queue"])),
                     (1, 1, [], 0), "follow-ups, copies, delivering, queue"))

    # ── §14 ──────────────────────────────────────────────────────────────────
    print("§14 supervisor: CONTROLLED EARLY CALLBACK — the late reply beats the "
          "pump's own return; delivered once, no stale limbo, no requeue")
    scenario("steer_ack_never", "fake-thread-early-cb", FAKECODEX_STALL_S="2.0")
    real_steer = codexrun.CodexTurn.steer
    fired: list = []

    def racing_steer(self, text, timeout=None, on_late=None):
        # the real request times out (unknown); the resolver then fires
        # SYNCHRONOUSLY, before the pump thread has seen the return value —
        # the fastest possible late reply
        out = real_steer(self, text, timeout=timeout, on_late=None)
        if str(out) == "unknown" and on_late is not None:
            fired.append(1)
            on_late(codexrun.SteerOutcome("accepted", "late acknowledgement (raced)"))
        return out
    codexrun.CodexTurn.steer = racing_steer
    try:
        STREAMED.clear()
        slug14, nid14 = mkorg("early-cb")
        st14 = supervisor.state(slug14, nid14)
        box14 = turn_in_background(slug14, nid14, "wait; race the callback")
        resp14 = wait_responding(slug14, nid14)
        time.sleep(0.3)
        routed14 = post_and_steer(slug14, nid14, "RACE-MSG callback first")
        box14["thread"].join(timeout=60)
    finally:
        codexrun.CodexTurn.steer = real_steer
    owned14 = carriers_owned(slug14, nid14, box14)
    rows14 = log_rows(slug14, nid14)
    check("fixture reached the seam: responding, steer door, callback fired before return",
          lambda: eq((resp14, bool(routed14.get("steering")), box14["error"], fired),
                     (True, True, None, [1]), "seam"))
    check("delivered ONCE as a steer: committed, no carrier owned, no limbo left, "
          "no redelivery receipt",
          lambda: eq((len(durable_steers(slug14, nid14)), owned14,
                      st14.get("steer_limbo") or [],
                      [r for r in rows14 if "redeliver" in (r.get("text") or "")],
                      delivering(slug14, nid14)),
                     (1, [], [], [], []), "steered rows, owned, limbo, redelivery, owed"))
    check("…and the message renders exactly once",
          lambda: eq(rendered_user_rows(slug14, nid14, "RACE-MSG"), 1, "copies"))

    # ── §15 ──────────────────────────────────────────────────────────────────
    print("§15 supervisor: the attempt mark CANNOT be persisted → no steer is "
          "sent; delivered next turn, receipted")
    scenario("steer", "fake-thread-nomark", FAKECODEX_STALL_S="1.0")
    real_note = supervisor._note_steer_attempt
    supervisor._note_steer_attempt = lambda *a, **k: False
    try:
        STREAMED.clear()
        slug15, nid15 = mkorg("nomark")
        st15 = supervisor.state(slug15, nid15)
        box15 = turn_in_background(slug15, nid15, "wait for a steer that must not come")
        resp15 = wait_responding(slug15, nid15)
        time.sleep(0.3)
        routed15 = post_and_steer(slug15, nid15, "NOMARK-MSG do not steer me")
        box15["thread"].join(timeout=60)
    finally:
        supervisor._note_steer_attempt = real_note
    owned15 = carriers_owned(slug15, nid15, box15)
    rows15 = log_rows(slug15, nid15)
    check("fixture reached the seam: responding, steer door",
          lambda: eq((resp15, bool(routed15.get("steering")), box15["error"]),
                     (True, True, None), "seam"))
    check("NO steer reached the turn (the `steer` fixture would have echoed it)",
          lambda: eq([p for p in STREAMED if "STEERED[" in (p.get("text") or "")], [],
                     "STEERED echoes"))
    check("the skip is receipted and the carrier is owned for the next turn",
          lambda: eq((any(r.get("where") == "steer skipped" for r in rows15),
                      [bool(isinstance(c, dict) and c.get("toks")) for c in owned15]),
                     (True, [True]), "receipt, owned"))
    scenario("tool", "fake-thread-nomark")
    ran15 = drain_follow(slug15, nid15, box15["follow"])
    check("…delivered once by the next turn",
          lambda: eq((ran15, rendered_user_rows(slug15, nid15, "NOMARK-MSG"),
                      delivering(slug15, nid15)), (1, 1, []), "follow-ups, copies, owed"))

    # ── §16 ──────────────────────────────────────────────────────────────────
    print("§16 state machine: late acceptance after redelivery reclaims PER CARRIER")
    tr = getattr(supervisor, "_steer_late_transition", None)
    A, B, X = {"text": "A", "toks": ["a"]}, {"text": "B", "toks": ["b"]}, {"text": "X", "toks": ["x"]}
    st16: dict = {"queue": [A, X], "steer_limbo": []}
    e16 = {"carriers": [A, B], "state": "redelivered", "late": None, "reason": ""}
    res16 = tr(st16, e16, codexrun.SteerOutcome("accepted", "late")) if tr else None
    check("partial: A (still queued) reclaimed, B (already drained) escaped, "
          "unrelated X untouched",
          lambda: eq((res16[0], [c["text"] for c in res16[1]], [c["text"] for c in res16[2]],
                      [c["text"] for c in st16["queue"]]) if res16 else None,
                     ("reclaim", ["A"], ["B"], ["X"]), "transition"))
    st16b: dict = {"queue": [A, X], "steer_limbo": []}
    e16b = {"carriers": [A, B], "state": "redelivered", "late": None, "reason": ""}
    res16b = tr(st16b, e16b, codexrun.SteerOutcome("rejected", "late")) if tr else None
    check("a late refusal after redelivery confirms it and moves nothing",
          lambda: eq((res16b[0], [c["text"] for c in st16b["queue"]]) if res16b else None,
                     ("confirmed", ["A", "X"]), "transition"))
    e16c = {"carriers": [A], "state": "redelivered", "late": None, "reason": ""}
    res16c = tr({"queue": [A], "steer_limbo": []}, e16c,
                codexrun.SteerOutcome("unknown", "ambiguous")) if tr else None
    check("a late UNKNOWN after redelivery changes nothing (still-unknown)",
          lambda: eq(res16c[0] if res16c else None, "still-unknown", "transition"))
    e16d = {"carriers": [A], "state": "inflight", "late": None, "reason": ""}
    res16d = tr({"queue": [], "steer_limbo": [e16d]}, e16d,
                codexrun.SteerOutcome("accepted", "late")) if tr else None
    check("a reply arriving while the pump is still inflight is PARKED on the entry",
          lambda: eq((res16d[0], str(e16d["late"])) if res16d else None,
                     ("parked", "accepted"), "transition"))
    e16e = {"carriers": [A], "state": "unknown", "late": None, "reason": ""}
    st16e: dict = {"queue": [], "steer_limbo": [e16e]}
    res16e = tr(st16e, e16e, codexrun.SteerOutcome("unknown", "ambiguous")) if tr else None
    check("an ambiguous late reply in limbo leaves the entry in limbo, unknown",
          lambda: eq((res16e[0], st16e["steer_limbo"] == [e16e], e16e["state"]) if res16e else None,
                     ("still-unknown", True, "unknown"), "transition"))
    # ATOMIC OWNERSHIP (review_codex_late_refusal.py): a late REFUSAL must
    # leave limbo and enter the queue in ONE transition — the single lock
    # take the caller holds. Observed from outside that call there is no
    # instant at which the carrier is in neither (the D-229 strand).
    e16f = {"carriers": [A], "state": "unknown", "late": None, "reason": ""}
    st16f: dict = {"queue": [X], "steer_limbo": [e16f]}
    res16f = tr(st16f, e16f, codexrun.SteerOutcome("rejected", "late")) if tr else None
    check("a late REFUSAL in limbo: the transition ITSELF moves the carrier limbo→queue "
          "(owned throughout; the caller holds one lock across it)",
          lambda: eq((res16f[0], st16f["steer_limbo"], [c["text"] for c in st16f["queue"]],
                      e16f["state"]) if res16f else None,
                     ("limbo", [], ["X", "A"], "rejected"), "transition"))
    import ast as _ast
    import inspect as _inspect
    _src = _inspect.getsource(supervisor._codex_leg)
    _fn = next((n for n in _ast.walk(_ast.parse(_src)) if isinstance(n, _ast.FunctionDef)
                and n.name == "_late_steer"), None)
    _requeues = [n for n in _ast.walk(_fn) if isinstance(n, _ast.Call)
                 and isinstance(n.func, _ast.Attribute) and n.func.attr == "extend"] if _fn else None
    check("…and the late-reply caller contains NO second requeue of its own "
          "(structural pin: no `.extend` call in _late_steer)",
          lambda: eq((_fn is not None, _requeues), (True, []), "caller requeues"))

    tool_srv.shutdown()
    print(f"\n{PASS} passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\n--- FAIL: {label}\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
