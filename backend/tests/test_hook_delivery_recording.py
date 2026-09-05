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
r_retry = run_hook(PROXY, slug, nid, "toolu_lost_2", "sess-lost")
after_retry = doc(slug, nid)
EVIDENCE["sections"]["lost_response"] = {
    "before": before, "hook_lost": r_lost, "proxy": drop, "doc_after_lost": after_lost,
    "ram_after_lost": ram_lost, "hook_retry": r_retry, "doc_after_retry": after_retry}
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
check("CONTRACT: the mail is delivered by a later hook or the turn-end fold, never dropped",
      lambda: truthy(r_retry["has_mail"] or (turn_end(slug, nid), doc(slug, nid)["mailbox"])[1],
                     {"retry": r_retry["has_mail"], "doc": doc(slug, nid)}))

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
sys.exit(1 if FAIL else 0)
