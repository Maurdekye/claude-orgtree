"""D1 — MID-TURN MAIL THROUGH THE REAL HOOK DOOR, WITH THE RESPONSE LOST.

    python backend/tests/test_hook_delivery_recording.py   (plain asserts)

The audit (astras-entrance-exam, orgtree-audit.md §2, delivery_probes.py)
reproduced D1 against an OLD snapshot with a hand-written HTTP handler that
called `pop_steer` and closed the socket. This file drives the REAL path on
current main: the real `api.app` under uvicorn, the real PostToolUse hook
(`orgtree/steer.py`) as a subprocess, real `send_message` draining a real
mailbox into a real `delivering` batch — and a fault-injecting TCP proxy
between hook and app that can drop the request or the response AFTER the
server has done its work. A fake CLI (this file) records the hook's printed
context into a fake transcript file the way the pinned CLI does
(`{"type":"attachment","attachment":{"type":"hook_additional_context",...}}`,
shape read from real transcripts 2026-09-05).

Two families of sections:

  RED ON MAIN (752887a): §2 asserts the contract's SAFE behaviour (contract.md
  §11: a lost response must not commit; the batch stays owned; a later hook
  or the turn-end fold delivers it). On main these FAIL — that is the
  reproduction, executed against production code rather than a stand-in.

  INVARIANTS THAT ALREADY HOLD (§3): concurrent hook fetches are exclusive
  under `_state_lock`, so no message is handed to two hooks. Recorded so the
  implementation cannot regress it.

Anti-vacuity: §1 is the positive control (the hook DOES receive mail through
the proxy in pass-through mode) and every section asserts a non-zero count of
the thing it judges. The proxy proves it can drop by being asserted to have
dropped (bytes-from-upstream > 0, bytes-to-client == 0).

No provider is ever called: no turn is started; the node is put into the
`responding` state by hand exactly as `_run_one_turn` would leave it, and the
hook is the only process spawned.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-hookrec-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_WARM"] = "0"
os.environ["ORGTREE_PORT"] = "9"          # nothing must ever reach the live backend
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import api, store, supervisor                          # noqa: E402
from orgtree.ledger import USER                                     # noqa: E402

assert os.path.realpath(store.DATA_ROOT) == os.path.realpath(DATA), store.DATA_ROOT
LIVE = os.path.realpath(os.path.expanduser("~/orgtree"))
assert os.path.realpath(store.DATA_ROOT) != LIVE, "bound to the LIVE root"

STEER_PY = os.path.join(HERE, "..", "orgtree", "steer.py")
MARK = "AUDIT IMPORTANT MAIL"

PASS = 0
FAIL: list[tuple[str, str]] = []
EVIDENCE: dict = {"main": None, "sections": {}}


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


# ── the real app ────────────────────────────────────────────────────────────
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


APP_PORT = free_port()


def serve_app() -> None:
    import uvicorn
    cfg = uvicorn.Config(api.app, host="127.0.0.1", port=APP_PORT, lifespan="off",
                         log_level="critical", access_log=False)
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(300):
        if getattr(server, "started", False):
            return
        time.sleep(0.05)
    raise RuntimeError("uvicorn did not start")


# ── the fault-injecting proxy ───────────────────────────────────────────────
class Proxy:
    """hook -> proxy -> app. mode: pass | drop_request | drop_response.
    `log` records per connection how many bytes came back from upstream and
    how many were handed to the client, so a 'drop' is PROVEN, not assumed."""

    def __init__(self) -> None:
        self.mode = "pass"
        self.log: list[dict] = []
        self.port = free_port()
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", self.port))
        self.srv.listen(16)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            c, _ = self.srv.accept()
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    @staticmethod
    def _read_http(sock: socket.socket) -> bytes:
        sock.settimeout(5)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                return buf
            buf += chunk
        head, _, body = buf.partition(b"\r\n\r\n")
        n = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                n = int(line.split(b":", 1)[1].strip() or 0)
        while len(body) < n:
            chunk = sock.recv(65536)
            if not chunk:
                break
            body += chunk
        return head + b"\r\n\r\n" + body

    def _one(self, c: socket.socket) -> None:
        rec = {"mode": self.mode, "from_upstream": 0, "to_client": 0, "path": ""}
        self.log.append(rec)
        try:
            req = self._read_http(c)
            try:
                rec["path"] = req.split(b" ", 2)[1].decode("latin1")
            except Exception:                                    # noqa: BLE001
                pass
            if rec["mode"] == "drop_request":
                c.close()
                return
            up = socket.create_connection(("127.0.0.1", APP_PORT), timeout=10)
            up.sendall(req)
            up.settimeout(10)
            resp = b""
            while True:
                try:
                    chunk = up.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                resp += chunk
                # uvicorn keeps the connection open; stop at the body end
                head, sep, body = resp.partition(b"\r\n\r\n")
                if sep:
                    n = 0
                    for line in head.split(b"\r\n"):
                        if line.lower().startswith(b"content-length:"):
                            n = int(line.split(b":", 1)[1].strip() or 0)
                    if len(body) >= n:
                        break
            up.close()
            rec["from_upstream"] = len(resp)
            if rec["mode"] == "drop_response":
                # the server has done its work; the hook gets nothing
                c.shutdown(socket.SHUT_RDWR)
                c.close()
                return
            c.sendall(resp)
            rec["to_client"] = len(resp)
            c.close()
        except Exception as e:                                   # noqa: BLE001
            rec["error"] = repr(e)
            try:
                c.close()
            except Exception:                                    # noqa: BLE001
                pass


# ── the fake CLI side: hook subprocess + transcript recording ───────────────
FAKE_CLAUDE = os.path.join(DATA, "fake-claude")
os.makedirs(os.path.join(FAKE_CLAUDE, "projects", "proj"), exist_ok=True)


def transcript_for(session_id: str) -> str:
    return os.path.join(FAKE_CLAUDE, "projects", "proj", session_id + ".jsonl")


def run_hook(proxy: Proxy, slug: str, nid: str, tool_use_id: str,
             session_id: str, record: bool = True) -> dict:
    """One PostToolUse hook process through the proxy, then the fake CLI's
    handling of its stdout: if the hook printed additionalContext, append the
    `hook_additional_context` attachment row to the transcript (unless
    record=False, the interrupted-CLI case). stdin carries the pinned CLI's
    PostToolUse fields (schema read from the 2.1.258 binary)."""
    env = dict(os.environ)
    env["ORGTREE_PORT"] = str(proxy.port)
    env["ORGTREE_DATA"] = DATA
    payload = {"session_id": session_id, "transcript_path": transcript_for(session_id),
               "cwd": os.path.join(DATA, "scratch", slug, nid),
               "hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_input": {"command": "true"}, "tool_response": {"stdout": ""},
               "tool_use_id": tool_use_id}
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, STEER_PY, slug, nid], input=json.dumps(payload),
                       text=True, capture_output=True, env=env, timeout=20)
    dt = time.perf_counter() - t0
    out = {"stdout": p.stdout, "stderr": p.stderr, "rc": p.returncode, "seconds": round(dt, 3),
           "has_mail": MARK in p.stdout, "context": None}
    if p.stdout.strip():
        try:
            ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
        except Exception:                                        # noqa: BLE001
            ctx = None
        out["context"] = ctx
        if ctx and record:
            row = {"type": "attachment", "sessionId": session_id,
                   "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                   "attachment": {"type": "hook_additional_context", "hookEvent": "PostToolUse",
                                  "hookName": "PostToolUse:Bash", "toolUseID": tool_use_id,
                                  "content": ctx}}
            with open(transcript_for(session_id), "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
    return out


# ── fixtures ────────────────────────────────────────────────────────────────
def mkorg(label: str) -> tuple[str, str]:
    org = store.create_org(f"zz hookrec {label}")
    r = org.hire(USER, None, "sol", 0, "cx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a hook-recording test agent")
    nid = r["node"]
    store.save_org(org)
    os.makedirs(os.path.join(DATA, "scratch", org.d["slug"], nid), exist_ok=True)
    return org.d["slug"], nid


def responding(slug: str, nid: str) -> None:
    """The state `_run_one_turn` leaves while the CLI is mid-response."""
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
        st["responding"] = True
        st["boundary_at"] = time.time()


def turn_end(slug: str, nid: str) -> None:
    """The lane-exit fold every leg performs (D-229): responding off and the
    RAM steer store folded into the queue in one lock take, then the journal
    batches nothing owns folded back to the mailbox."""
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["responding"] = False
        left = supervisor._fold_steer(st)
        keep = [t for x in st["queue"] if isinstance(x, dict) for t in x.get("toks") or []]
        st["busy"] = False
        st["queue"] = []
    supervisor._fold_back_undelivered(slug, nid, keep_toks=[])
    return left


def post_user_mail(slug: str, nid: str, body: str) -> dict:
    """A user message to a responding node: real ledger mail + real steer."""
    org = store.load_org(slug)
    r = org.post_mail(USER, nid, body)
    store.save_org(org)
    sent = supervisor.send_message(
        slug, nid, "(orgtree) The mail above includes a message from the user — act on it now.",
        mail_ping=True)
    return {"mail": r, "send": sent}


def doc(slug: str, nid: str) -> dict:
    org = store.load_org(slug)
    return {"mailbox": [m.get("id") for m in (org.d.get("mail") or {}).get(nid, [])],
            "delivering": list((org.d.get("delivering") or {}).get(nid) or []),
            "steered": [e for e in (org.d.get("steered_log") or {}).get(nid, []) if not e.get("fold")],
            "folds": [e for e in (org.d.get("steered_log") or {}).get(nid, []) if e.get("fold")]}


def ram(slug: str, nid: str) -> dict:
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        return {"steer": list(st.get("steer") or []), "queue": list(st["queue"]),
                "responding": bool(st.get("responding"))}


# ── mutants: prove the CONTRACT lines can fail against a plausible bug ──────
# HOOKREC_MUTANT=ack_commits        the ack commits (v2's boundary): §5 must go red
# HOOKREC_MUTANT=record_any_owner   the scan ignores toolUseID: §7 must go red
MUTANT = os.environ.get("HOOKREC_MUTANT", "")
if MUTANT == "ack_commits":
    _real_ack = supervisor.ack_steer

    def _ack_commits(slug, nid, delivery_id, tool_use_id):
        r = _real_ack(slug, nid, delivery_id, tool_use_id)
        if r.get("status") == "receipt":
            with store.DOC_LOCK:
                org = store.load_org(slug)
                att = supervisor._steer_attempts(org, nid).get(delivery_id)
                if att and not att.get("recorded_at"):
                    supervisor._apply_steer_record(org, nid, delivery_id, att, supervisor.now_iso())
                    store.save_org(org)
        return r
    supervisor.ack_steer = _ack_commits
    api.supervisor.ack_steer = _ack_commits
elif MUTANT == "ram_cleanup_by_claim_id":
    supervisor._carrier_confirmed = lambda c, did, toks: (isinstance(c, dict)
                                                           and (c.get("claim") or {}).get("delivery_id") == did)
elif MUTANT == "no_row_linkage":
    _real_apply = supervisor._apply_steer_record

    def _no_link(org, nid, did, att, stamp):
        net_ids, row = _real_apply(org, nid, did, att, stamp)
        for r in (org.d.get("steered_log") or {}).get(nid) or []:
            if r.get("delivery_id") != did and did in (r.get("recorded_ids") or []):
                r["recorded_ids"] = [x for x in r["recorded_ids"] if x != did]
                r["confirmed_duplicate"] = len(r["recorded_ids"]) >= 2
        return net_ids, row
    supervisor._apply_steer_record = _no_link
elif MUTANT == "record_any_owner":
    _real_read = supervisor._read_steer_records

    def _any_owner(att, start=None, pos_out=None):
        return [(d, att.get("tool_use_id")) for d, _t in _real_read(att, start, pos_out)]
    supervisor._read_steer_records = _any_owner

# ═══════════════════════════════════════════════════════════════════════════
serve_app()
PROXY = Proxy()
supervisor.stream = lambda *a, **k: None     # no desk to announce to

print(f"\nreal app on :{APP_PORT}, proxy on :{PROXY.port}, data root {DATA}")

# ── §1 positive control ────────────────────────────────────────────────────
print("\n§1 positive control: the real hook receives real mail through the proxy")
slug, nid = mkorg("control")
responding(slug, nid)
sent = post_user_mail(slug, nid, MARK + " control")
PROXY.mode = "pass"
r1 = run_hook(PROXY, slug, nid, "toolu_ctrl_1", "sess-control")
r2 = run_hook(PROXY, slug, nid, "toolu_ctrl_2", "sess-control")
d1 = doc(slug, nid)
EVIDENCE["sections"]["control"] = {"send": sent["send"], "first": r1, "second": r2, "doc": d1}
check("send_message chose the steer carrier (responding node)",
      lambda: truthy(sent["send"].get("steering"), f"send result {sent['send']}"))
check("first hook received the mail", lambda: truthy(r1["has_mail"], r1))
check("second hook received nothing more (no duplicate)", lambda: eq(r2["has_mail"], False, r2))
check("the fake transcript holds one hook_additional_context row with the marker",
      lambda: eq(sum(1 for line in open(transcript_for("sess-control"), encoding="utf-8")
                     if "hook_additional_context" in line and MARK in line), 1, "rows"))
check("proxy passed bytes through (instrument alive)",
      lambda: truthy(PROXY.log and PROXY.log[0]["from_upstream"] > 0 and PROXY.log[0]["to_client"] > 0,
                     PROXY.log[:1]))
turn_end(slug, nid)

# ── §2 the D1 case: response lost AFTER the real handler ran ───────────────
print("\n§2 lost response through the real door — RED ON MAIN, green under contract.md §11")
LEASE = 0.6
supervisor.STEER_CLAIM_LEASE_S = LEASE      # the lease is a constant; shortened for the test only
slug, nid = mkorg("lost")
responding(slug, nid)
sent = post_user_mail(slug, nid, MARK + " lost")
before = doc(slug, nid)
PROXY.log.clear()
PROXY.mode = "drop_response"
r_lost = run_hook(PROXY, slug, nid, "toolu_lost_1", "sess-lost")
drop = list(PROXY.log)
after_lost = doc(slug, nid)
ram_lost = ram(slug, nid)
PROXY.mode = "pass"
r_retry = run_hook(PROXY, slug, nid, "toolu_lost_2", "sess-lost")       # inside the lease
time.sleep(LEASE + 0.2)
r_late = run_hook(PROXY, slug, nid, "toolu_lost_3", "sess-lost")        # after the lease
after_late = doc(slug, nid)
r_scan = run_hook(PROXY, slug, nid, "toolu_lost_4", "sess-lost")        # its fetch scans the record
after_scan = doc(slug, nid)
EVIDENCE["sections"]["lost_response"] = {
    "before": before, "hook_lost": r_lost, "proxy": drop, "doc_after_lost": after_lost,
    "ram_after_lost": ram_lost, "hook_retry_inside_lease": r_retry, "hook_after_lease": r_late,
    "doc_after_late": after_late, "doc_after_scan": after_scan}
check("fixture: one delivering batch existed before the hook", lambda: eq(len(before["delivering"]), 1, before))
check("instrument: the server answered and the proxy dropped the whole response",
      lambda: truthy(drop and drop[0]["from_upstream"] > 0 and drop[0]["to_client"] == 0, drop))
check("the hook received nothing (the D1 symptom)", lambda: eq(r_lost["has_mail"], False, r_lost))
# --- the contract's safe properties (contract.md §11) — these are the RED lines on main
check("CONTRACT: no durable 'delivered' row for a message no hook received",
      lambda: eq(len(after_lost["steered"]), 0, f"steered rows {after_lost['steered']}"))
check("CONTRACT: the batch is still owned (delivering) after the lost response",
      lambda: eq(len(after_lost["delivering"]), 1, after_lost))
check("CONTRACT: the message still rides a carrier the node owns (RAM steer store)",
      lambda: truthy(ram_lost["steer"], ram_lost))
check("CONTRACT: the batch carries a durable claim naming the hook's tool_use_id",
      lambda: eq((after_lost["delivering"][0].get("claim") or {}).get("tool_use_id"), "toolu_lost_1",
                 after_lost["delivering"]))
check("CONTRACT: a retry inside the lease is offered nothing (single live claim)",
      lambda: eq(r_retry["has_mail"], False, r_retry))
check("CONTRACT: a retry after the lease receives the message, with a delivery marker",
      lambda: truthy(r_late["has_mail"] and "ORGTREE-DELIVERY:" in (r_late["context"] or ""), r_late))
check("CONTRACT: the hook's ack alone did not commit (no row before the record is scanned)",
      lambda: eq(len(after_late["steered"]), 0, after_late["steered"]))
check("CONTRACT: the CLI's record commits it — one row, level recorded, retried=True, not a confirmed duplicate",
      lambda: eq([(r.get("level"), r.get("retried"), r.get("confirmed_duplicate")) for r in after_scan["steered"]],
                 [("recorded", True, False)], after_scan["steered"]))
check("CONTRACT: the batch is confirmed away once recorded", lambda: eq(after_scan["delivering"], [], after_scan))
check("nothing sits in the mailbox or the steer store afterwards",
      lambda: eq((after_scan["mailbox"], ram(slug, nid)["steer"]), ([], []), doc(slug, nid)))
turn_end(slug, nid)

# ── §3 concurrent hook fetches are exclusive (holds on main) ───────────────
print("\n§3 concurrent pollers: two hooks at once, one message — exactly one gets it")
slug, nid = mkorg("concurrent")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " concurrent")
PROXY.mode = "pass"
results: dict[str, dict] = {}


def par(tag: str) -> None:
    results[tag] = run_hook(PROXY, slug, nid, f"toolu_par_{tag}", "sess-par")


ths = [threading.Thread(target=par, args=(t,)) for t in ("A", "B", "C")]
for t in ths:
    t.start()
for t in ths:
    t.join()
got = sorted(t for t, r in results.items() if r["has_mail"])
EVIDENCE["sections"]["concurrent"] = {k: {"has_mail": v["has_mail"], "seconds": v["seconds"]} for k, v in results.items()}
check("exactly one of three concurrent hooks received the message", lambda: eq(len(got), 1, results))
check("the transcript recorded it exactly once",
      lambda: eq(sum(1 for line in open(transcript_for("sess-par"), encoding="utf-8")
                     if "hook_additional_context" in line and MARK in line), 1, "rows"))
turn_end(slug, nid)

# ── §5 received by the hook, never recorded by the CLI ─────────────────────
print("\n§5 acked but never recorded: NOT delivered; folded at the boundary, visibly uncertain")


def belt(slug: str, nid: str) -> dict:
    """The turn-boundary belt as `_run_one_turn` runs it (scan, fold, receipt,
    journal fold-back), without a turn."""
    supervisor.scan_steer_records(slug, nid)
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["responding"] = False
        residual = supervisor._fold_steer(st)
        alive = [t for x in st["queue"] if isinstance(x, dict) for t in x.get("toks") or []]
        uncertain = supervisor._steer_uncertain_count(st["queue"])
    if uncertain:
        supervisor._steer_fold_log(slug, nid, uncertain, "turn boundary",
                                   why="received by the hook but not recorded by the CLI before the "
                                       "turn ended — redelivered at the next boundary; a duplicate is possible")
    supervisor._fold_back_undelivered(slug, nid, keep_toks=alive)
    return {"residual": len(residual), "alive": alive, "uncertain": uncertain}


slug, nid = mkorg("unrecorded")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " unrecorded")
r1 = run_hook(PROXY, slug, nid, "toolu_unrec_1", "sess-unrec", record=False)   # CLI interrupted
time.sleep(LEASE + 0.2)
r2 = run_hook(PROXY, slug, nid, "toolu_unrec_2", "sess-unrec")
d_before = doc(slug, nid)
b = belt(slug, nid)
d_after = doc(slug, nid)
q = ram(slug, nid)["queue"]
EVIDENCE["sections"]["acked_unrecorded"] = {"hook": r1, "retry_after_lease": r2, "belt": b,
                                            "doc_before": d_before, "doc_after": d_after, "queue": q}
check("the hook received and acked it", lambda: truthy(r1["has_mail"] and r1["rc"] == 0, r1))
check("CONTRACT: an acked delivery is never re-offered, even after the lease", lambda: eq(r2["has_mail"], False, r2))
check("CONTRACT: no delivered row without the CLI's record", lambda: eq(d_after["steered"], [], d_after))
check("CONTRACT: the belt folds it to the queue, still journaled, claim marked acked",
      lambda: truthy(len(q) == 1 and (q[0].get("claim") or {}).get("acked") and len(d_after["delivering"]) == 1,
                     {"queue": q, "doc": d_after}))
check("CONTRACT: the fold receipt says a duplicate is possible",
      lambda: truthy(any("not recorded" in (f.get("text") or "") for f in d_after["folds"]), d_after["folds"]))

# ── §6 the record lands late, after the fold ───────────────────────────────
print("\n§6 late record after the fold: the next scan reclaims it from the queue")
did = None
try:
    org = store.load_org(slug)
    atts = (org.d.get("steer_attempts") or {}).get(nid) or {}
    did = next(k for k, a in atts.items() if a.get("tool_use_id") == "toolu_unrec_1")
except StopIteration:
    pass
# the CLI writes the row it never got to write (same content, right toolUseID)
with open(transcript_for("sess-unrec"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {
        "type": "hook_additional_context", "hookEvent": "PostToolUse", "toolUseID": "toolu_unrec_1",
        "content": r1["context"]}}) + "\n")
scan1 = supervisor.scan_steer_records(slug, nid)
scan2 = supervisor.scan_steer_records(slug, nid)
d6 = doc(slug, nid)
q6 = ram(slug, nid)["queue"]
EVIDENCE["sections"]["late_record"] = {"delivery_id": did, "scan1": scan1, "scan2": scan2, "doc": d6, "queue": q6}
check("attempt record exists for the first hook", lambda: truthy(did, "no attempt"))
check("CONTRACT: the late record commits — one row, level recorded",
      lambda: eq([r.get("level") for r in d6["steered"]], ["recorded"], d6["steered"]))
check("CONTRACT: the queued copy is reclaimed (no redelivery) and the batch confirmed away",
      lambda: eq((q6, d6["delivering"], d6["mailbox"]), ([], [], []), {"queue": q6, "doc": d6}))
check("CONTRACT: a second scan is a no-op (idempotent)", lambda: eq((scan2, len(d6["steered"])), ({}, 1), scan2))

# ── §7 ownership: valid ids under the wrong tool ───────────────────────────
print("\n§7 ownership: a record or ack under another toolUseID commits nothing")
slug, nid = mkorg("owner")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " owner")
# hook fetches and prints, but the fake CLI files the row under another tool id
r7 = run_hook(PROXY, slug, nid, "toolu_own_1", "sess-own", record=False)
with open(transcript_for("sess-own"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {
        "type": "hook_additional_context", "hookEvent": "PostToolUse", "toolUseID": "toolu_SOMEONE_ELSE",
        "content": r7["context"]}}) + "\n")
scan7 = supervisor.scan_steer_records(slug, nid)
d7 = doc(slug, nid)
org = store.load_org(slug)
did7 = next(k for k, a in ((org.d.get("steer_attempts") or {}).get(nid) or {}).items())
import urllib.request


def http_ack(delivery_id: str, tool: str) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{APP_PORT}/api/orgs/{slug}/nodes/{nid}/steer/ack", method="POST",
        data=json.dumps({"delivery_id": delivery_id, "tool_use_id": tool}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


a_wrong = http_ack(did7, "toolu_SOMEONE_ELSE")
a_forged = http_ack("0000000000000000", "toolu_own_1")
a_repeat = http_ack(did7, "toolu_own_1")          # the hook already acked once
EVIDENCE["sections"]["ownership"] = {"scan": scan7, "doc": d7, "ack_wrong": a_wrong,
                                     "ack_forged": a_forged, "ack_repeat": a_repeat}
check("CONTRACT: a record under the wrong toolUseID is refused (owner-mismatch, no row)",
      lambda: eq((scan7.get("owner-mismatch"), d7["steered"]), (1, []), {"scan": scan7, "doc": d7}))
check("CONTRACT: an ack under the wrong tool is refused", lambda: eq(a_wrong.get("status"), "owner-mismatch", a_wrong))
check("CONTRACT: an ack for an unissued delivery is unknown", lambda: eq(a_forged.get("status"), "unknown", a_forged))
check("CONTRACT: a repeated ack is idempotent", lambda: eq(a_repeat.get("status"), "already-acked", a_repeat))
belt(slug, nid)

# ── §8 backend restart: the transcript is durable, RAM is not ──────────────
print("\n§8 restart reconcile: a recorded delivery commits from the doc + transcript alone")
slug, nid = mkorg("restart")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " restart")
r8 = run_hook(PROXY, slug, nid, "toolu_rs_1", "sess-rs")        # printed, recorded, acked; never scanned
# the backend dies here: forget RAM, then run the reconcile step on the doc
supervisor.state(slug, nid).clear()
supervisor.state(slug, nid).update({"queue": []})
with store.DOC_LOCK:
    org = store.load_org(slug)
    n_rec = supervisor._reconcile_steer_records(org)
    dlv = org.d.pop("delivering", None) or {}       # what reconcile() folds back afterwards
    left = list(dlv.get(nid) or [])
    store.save_org(org)
d8 = doc(slug, nid)
EVIDENCE["sections"]["restart"] = {"hook": r8, "reconciled": n_rec, "folded_after": len(left), "doc": d8}
check("the hook delivered and the CLI recorded before the 'crash'", lambda: truthy(r8["has_mail"], r8))
check("CONTRACT: reconcile commits the recorded delivery from the transcript (no fold, no duplicate)",
      lambda: eq((n_rec, len(left), [r.get("level") for r in d8["steered"]], d8["mailbox"]),
                 (1, 0, ["recorded"], []), {"n": n_rec, "left": left, "doc": d8}))

# ── §9 one delivery, two batches ───────────────────────────────────────────
print("\n§9 one hook fetch carrying two batches: the record confirms both")
slug, nid = mkorg("two")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " two-A")
post_user_mail(slug, nid, MARK + " two-B")
d9_before = doc(slug, nid)
r9 = run_hook(PROXY, slug, nid, "toolu_two_1", "sess-two")
run_hook(PROXY, slug, nid, "toolu_two_2", "sess-two")           # scans
d9 = doc(slug, nid)
EVIDENCE["sections"]["two_batches"] = {"before": d9_before, "hook": r9, "doc": d9}
check("fixture: two delivering batches", lambda: eq(len(d9_before["delivering"]), 2, d9_before))
check("one hook received both messages", lambda: eq((r9["context"] or "").count(MARK), 2, r9))
check("CONTRACT: both batches confirmed away and both rows recorded, none retried",
      lambda: eq((d9["delivering"], sorted((r["level"], r["retried"]) for r in d9["steered"])),
                 ([], [("recorded", False), ("recorded", False)]), d9))
belt(slug, nid)

# ── §10 reviewer case: two distinct recorded deliveries of the same mail ──
print("\n§10 A recorded, then B recorded for the same mail: both rows linked, both confirmed duplicates")
slug, nid = mkorg("tworec")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " tworec")
rA = run_hook(PROXY, slug, nid, "toolu_tr_A", "sess-tr", record=False)     # A: printed, acked, row late
time.sleep(LEASE + 0.2)
rB = run_hook(PROXY, slug, nid, "toolu_tr_B", "sess-tr", record=False)     # B: re-offered (A unacked? no: acked)
# A was ACKED, so B must NOT have been offered anything (P13). Make A's ack lost instead:
d10 = doc(slug, nid)
EVIDENCE["sections"]["two_records_precheck"] = {"A": rA["has_mail"], "B": rB["has_mail"]}
check("CONTRACT: B is offered nothing while A's receipt stands", lambda: eq(rB["has_mail"], False, rB))
belt(slug, nid)

slug, nid = mkorg("tworec2")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " tworec2")
PROXY.mode = "pass"
# A: printed, ack LOST (drop only the ack), record late
class _AckDrop:
    def __init__(self, proxy): self.proxy = proxy
    def __enter__(self):
        self._orig = self.proxy._one
        proxy = self.proxy
        def one(c):
            rec_mode = proxy.mode
            # peek: read the request, drop it if it is the ack
            req = Proxy._read_http(c)
            if b"/steer/ack" in req.split(b"\r\n", 1)[0]:
                proxy.log.append({"mode": "drop_ack", "from_upstream": 0, "to_client": 0, "path": "/steer/ack"})
                c.close()
                return
            # otherwise forward normally
            up = socket.create_connection(("127.0.0.1", APP_PORT), timeout=10)
            up.sendall(req); up.settimeout(10)
            resp = b""
            while True:
                try: chunk = up.recv(65536)
                except socket.timeout: break
                if not chunk: break
                resp += chunk
                head, sep, body = resp.partition(b"\r\n\r\n")
                if sep:
                    n = 0
                    for line in head.split(b"\r\n"):
                        if line.lower().startswith(b"content-length:"):
                            n = int(line.split(b":", 1)[1].strip() or 0)
                    if len(body) >= n: break
            up.close(); c.sendall(resp); c.close()
        self.proxy._one = one
    def __exit__(self, *a):
        self.proxy._one = self._orig


with _AckDrop(PROXY):
    rA = run_hook(PROXY, slug, nid, "toolu_t2_A", "sess-t2", record=False)
time.sleep(LEASE + 0.2)
rB = run_hook(PROXY, slug, nid, "toolu_t2_B", "sess-t2", record=False)      # re-offered: A never acked
org = store.load_org(slug)
atts = (org.d.get("steer_attempts") or {}).get(nid) or {}
didA = next(k for k, a in atts.items() if a.get("tool_use_id") == "toolu_t2_A")
didB = next(k for k, a in atts.items() if a.get("tool_use_id") == "toolu_t2_B")
ram_before = ram(slug, nid)
# the CLI now writes A's row (late), scan; then B's row, scan
with open(transcript_for("sess-t2"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "hook_additional_context",
            "hookEvent": "PostToolUse", "toolUseID": "toolu_t2_A", "content": rA["context"]}}) + "\n")
sA = supervisor.scan_steer_records(slug, nid)
ram_mid = ram(slug, nid)
dA = doc(slug, nid)
with open(transcript_for("sess-t2"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "hook_additional_context",
            "hookEvent": "PostToolUse", "toolUseID": "toolu_t2_B", "content": rB["context"]}}) + "\n")
sB = supervisor.scan_steer_records(slug, nid)
dB = doc(slug, nid)
EVIDENCE["sections"]["two_records"] = {"A": rA["has_mail"], "B": rB["has_mail"], "ram_before": ram_before,
                                       "scanA": sA, "ram_after_A": ram_mid, "docA": dA, "scanB": sB, "docB": dB}
check("fixture: A printed with its ack lost, B re-offered after the lease and printed",
      lambda: truthy(rA["has_mail"] and rB["has_mail"] and didA != didB, {"A": rA, "B": rB}))
check("fixture: after B's claim the RAM carrier is owned by B (the reviewer's RAM case)",
      lambda: eq((ram_before["steer"][0].get("claim") or {}).get("delivery_id"), didB, ram_before))
check("CONTRACT (RAM): A's record confirms the batch and REMOVES the carrier B holds (batch ownership, not claim id)",
      lambda: eq((sA.get("recorded"), ram_mid["steer"], ram_mid["queue"], dA["delivering"]), (1, [], [], []),
                 {"scan": sA, "ram": ram_mid, "doc": dA}))
check("CONTRACT (linkage): B's later record links both rows -- both say confirmed duplicate, recorded_ids = {A, B}",
      lambda: eq(sorted((r["delivery_id"], r["confirmed_duplicate"], sorted(r["recorded_ids"])) for r in dB["steered"]),
                 sorted([(didA, True, sorted([didA, didB])), (didB, True, sorted([didA, didB]))]), dB["steered"]))
belt(slug, nid)

# ── §11 reviewer case: one delivery, two batches, mixed states at record time
print("\n§11 one delivery covering t1 (still delivering) + t2 (folded to the mailbox): both reconciled")
slug, nid = mkorg("mixed")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " mixed-1")
post_user_mail(slug, nid, MARK + " mixed-2")
r11 = run_hook(PROXY, slug, nid, "toolu_mx_1", "sess-mx", record=False)
org = store.load_org(slug)
att11 = next(a for a in ((org.d.get("steer_attempts") or {}).get(nid) or {}).values())
t1, t2 = att11["toks"][0], att11["toks"][1]
# fold ONLY t2 back to the mailbox (a partial fold), keep t1 delivering + its carrier
supervisor._fold_back_undelivered(slug, nid, only_toks=[t2])
st11 = supervisor.state(slug, nid)
with supervisor._state_lock:
    st11["steer"] = [c for c in st11["steer"] if t2 not in (c.get("toks") or [])]
d11_before = doc(slug, nid)
with open(transcript_for("sess-mx"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "hook_additional_context",
            "hookEvent": "PostToolUse", "toolUseID": "toolu_mx_1", "content": r11["context"]}}) + "\n")
s11 = supervisor.scan_steer_records(slug, nid)
d11 = doc(slug, nid)
EVIDENCE["sections"]["mixed_fold"] = {"before": d11_before, "scan": s11, "after": d11}
check("fixture: t1 still delivering, t2's mail back in the mailbox",
      lambda: eq((len(d11_before["delivering"]), len(d11_before["mailbox"])), (1, 1), d11_before))
check("CONTRACT: the record confirms t1 AND reclaims t2's mail from the mailbox (nothing left to duplicate)",
      lambda: eq((s11.get("recorded"), d11["delivering"], d11["mailbox"], ram(slug, nid)["steer"]), (1, [], [], []),
                 {"scan": s11, "doc": d11, "ram": ram(slug, nid)}))
belt(slug, nid)

# ── §12 late record after the folded mail was RE-DRAINED into a new batch ──
print("\n§12 late record after re-drain: the new batch is flagged; its own record is a confirmed duplicate")
slug, nid = mkorg("redrain")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " redrain")
r12 = run_hook(PROXY, slug, nid, "toolu_rd_1", "sess-rd", record=False)
# turn ends, backend restarts: reconcile folds the batch to the mailbox
supervisor.state(slug, nid).clear(); supervisor.state(slug, nid).update({"queue": []})
with store.DOC_LOCK:
    org = store.load_org(slug)
    supervisor._reconcile_steer_records(org)
    dlv = org.d.pop("delivering", None) or {}
    for b in dlv.get(nid) or []:
        org.d.setdefault("mail", {}).setdefault(nid, [])[0:0] = b.get("mail") or []
    store.save_org(org)
# next turn re-drains the mailbox into a NEW batch and steers it
responding(slug, nid)
etext, tok2, _ = supervisor._envelope(slug, nid, "(orgtree) nudge")
st12 = supervisor.state(slug, nid)
with supervisor._state_lock:
    st12.setdefault("steer", []).append({"toks": [tok2], "text": etext, "view": etext})
d12_a = doc(slug, nid)
# NOW the old record lands
with open(transcript_for("sess-rd"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "hook_additional_context",
            "hookEvent": "PostToolUse", "toolUseID": "toolu_rd_1", "content": r12["context"]}}) + "\n")
s12 = supervisor.scan_steer_records(slug, nid)
d12_b = doc(slug, nid)
# the new batch delivers and records
r12b = run_hook(PROXY, slug, nid, "toolu_rd_2", "sess-rd")
run_hook(PROXY, slug, nid, "toolu_rd_3", "sess-rd")
d12_c = doc(slug, nid)
EVIDENCE["sections"]["redrain"] = {"after_redrain": d12_a, "scan_old": s12, "after_old_record": d12_b,
                                   "new_delivery": r12b["has_mail"], "final": d12_c}
check("fixture: the mail was re-drained into a new batch", lambda: eq(len(d12_a["delivering"]), 1, d12_a))
check("CONTRACT: the old record flags the new batch (previously_recorded), no row yet for the new batch",
      lambda: truthy((d12_b["delivering"] and d12_b["delivering"][0].get("previously_recorded")), d12_b))
check("CONTRACT: the new batch's own record is a confirmed duplicate, retried",
      lambda: truthy(any(r.get("confirmed_duplicate") and r.get("retried") for r in d12_c["steered"]
                         if r.get("delivery_id") != s12 and MARK in (r.get("text") or "")), d12_c["steered"]))
belt(slug, nid)

# ── §13 legacy hook (no identity) is labelled, never 'recorded' ─────────────
print("\n§13 a hook without identity gets the legacy fetch, labelled handoff")
slug, nid = mkorg("legacy")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " legacy")
import urllib.request as _ur
req = _ur.Request(f"http://127.0.0.1:{APP_PORT}/api/orgs/{slug}/nodes/{nid}/steer", method="POST")
with _ur.urlopen(req, timeout=5) as r:
    legacy = json.load(r)
d13 = doc(slug, nid)
EVIDENCE["sections"]["legacy"] = {"response": legacy, "doc": d13}
check("legacy fetch still delivers (an old hook keeps working)", lambda: truthy(legacy.get("messages") and legacy.get("legacy"), legacy))
check("CONTRACT: its row is labelled level=handoff, not recorded",
      lambda: eq([r.get("level") for r in d13["steered"]], ["handoff"], d13["steered"]))
belt(slug, nid)

# ── §14 retention boundary ─────────────────────────────────────────────────
print("\n§14 attempts: open while recoverable, resolved when the mail left by another route, capped per batch")
slug, nid = mkorg("retention")
responding(slug, nid)
post_user_mail(slug, nid, MARK + " retention")
# many lease expiries: each mints an attempt for the same batch
for i in range(supervisor.STEER_ATTEMPTS_PER_BATCH + 3):
    PROXY.mode = "drop_response"
    run_hook(PROXY, slug, nid, f"toolu_ret_{i}", "sess-ret")
    PROXY.mode = "pass"
    time.sleep(LEASE + 0.1)
org = store.load_org(slug)
atts = (org.d.get("steer_attempts") or {}).get(nid) or {}
open_n = sum(1 for a in atts.values() if supervisor._attempt_open(a))
superseded = sum(1 for a in atts.values() if a.get("resolved") == "superseded")
# the mail then leaves by another route: simulate the turn path confirming the batch
belt(slug, nid)
org = store.load_org(slug)
toks = [b.get("tok") for b in (org.d.get("delivering") or {}).get(nid) or []]
supervisor._confirm_delivered(slug, nid, toks)
supervisor.scan_steer_records(slug, nid)
with store.DOC_LOCK:
    org = store.load_org(slug); supervisor._trim_steer_attempts(org, nid); store.save_org(org)
org = store.load_org(slug)
atts2 = (org.d.get("steer_attempts") or {}).get(nid) or {}
open_after = sum(1 for a in atts2.values() if supervisor._attempt_open(a))
elsewhere = sum(1 for a in atts2.values() if a.get("resolved") == "delivered-elsewhere")
EVIDENCE["sections"]["retention"] = {"attempts": len(atts), "open_while_pending": open_n, "superseded": superseded,
                                     "open_after_turn_path": open_after, "delivered_elsewhere": elsewhere}
check("CONTRACT: open attempts per batch are capped; older ones resolve as superseded, none dropped while pending",
      lambda: eq((open_n, superseded > 0, len(atts)), (supervisor.STEER_ATTEMPTS_PER_BATCH, True,
                                                        supervisor.STEER_ATTEMPTS_PER_BATCH + 3), atts and {"open": open_n, "sup": superseded}))
check("CONTRACT: once the mail left by the turn path, no attempt stays open (nothing left to scan for)",
      lambda: eq((open_after, elsewhere > 0), (0, True), {"open": open_after, "elsewhere": elsewhere}))

# ── §4 hook latency on this machine (a measurement, not a bound) ───────────
print("\n§4 hook round trip on this machine, idle, N=10 (fetch with mail present)")
slug, nid = mkorg("latency")
lat = []
for i in range(10):
    responding(slug, nid)
    post_user_mail(slug, nid, f"{MARK} latency {i}")
    r = run_hook(PROXY, slug, nid, f"toolu_lat_{i}", "sess-lat")
    lat.append(r["seconds"])
    turn_end(slug, nid)
lat.sort()
EVIDENCE["sections"]["latency_s"] = {"sorted": lat, "median": lat[len(lat) // 2], "max": lat[-1],
                                     "note": "whole hook subprocess incl. python startup; idle test machine; not the live root"}
print(f"  hook subprocess seconds sorted: {lat}")
check("latency instrument produced 10 samples", lambda: eq(len(lat), 10, lat))

# ═══════════════════════════════════════════════════════════════════════════
if os.environ.get("HOOKREC_EVIDENCE"):
    with open(os.environ["HOOKREC_EVIDENCE"], "w", encoding="utf-8") as f:
        json.dump(EVIDENCE, f, indent=2, default=str)
print(f"\n{PASS} passed, {len(FAIL)} failed")
for label, tb in FAIL:
    print(f"\n--- {label}\n{tb}")
if not FAIL:
    print(f"ALL {PASS} CHECKS PASS")
sys.exit(1 if FAIL else 0)
