#!/usr/bin/env python3
"""v0 spike harness — verifies the load-bearing unknowns from PLAN.md §13 v0.

Each spike is a subcommand: python run.py A|B|C|D|E|F
Raw event logs land in results/<spike>-*.jsonl; a state file carries session ids between spikes.

Findings feed §12 decisions №18, №16, №5, №29, №24 and the stream-json injection mechanism (§6.4).
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
STATE_PATH = os.path.join(RESULTS, "state.json")
CLAUDE = r"C:\Users\ncola_k8bx\AppData\Roaming\npm\claude.CMD"

os.makedirs(RESULTS, exist_ok=True)


def load_state():
    if os.path.exists(STATE_PATH):
        return json.load(open(STATE_PATH, encoding="utf-8"))
    return {}


def save_state(st):
    json.dump(st, open(STATE_PATH, "w", encoding="utf-8"), indent=2)


def clean_env():
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE":
            env.pop(k, None)
    return env


def launch(args):
    cmd = ["cmd", "/c", CLAUDE] + args
    return subprocess.Popen(
        cmd, cwd=HERE, env=clean_env(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1)


class Stream:
    """Line reader with timeout over a subprocess stdout."""

    def __init__(self, proc, log_path):
        self.proc = proc
        self.lines = []
        self.log = open(log_path, "w", encoding="utf-8")
        self._buf = []
        self._lock = threading.Condition()
        self._eof = False
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()
        self.err_lines = []

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.log.write(line + "\n")
            self.log.flush()
            with self._lock:
                self._buf.append(line)
                self._lock.notify_all()
        with self._lock:
            self._eof = True
            self._lock.notify_all()

    def _pump_err(self):
        for line in self.proc.stderr:
            self.err_lines.append(line.rstrip("\n"))
            self.log.write("STDERR: " + line)
            self.log.flush()

    def next_line(self, timeout):
        deadline = time.time() + timeout
        with self._lock:
            while not self._buf:
                if self._eof:
                    return None
                rem = deadline - time.time()
                if rem <= 0:
                    return None
                self._lock.wait(rem)
            return self._buf.pop(0)

    def wait_event(self, pred, timeout, collect=None):
        """Read events until pred(event) is true. Returns the event or None on timeout/EOF."""
        deadline = time.time() + timeout
        while True:
            rem = deadline - time.time()
            if rem <= 0:
                return None
            line = self.next_line(rem)
            if line is None:
                return None
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if collect is not None:
                collect.append(ev)
            if pred(ev):
                return ev


def send_user(proc, text):
    msg = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def is_result(ev):
    return ev.get("type") == "result"


def assistant_text(events):
    out = []
    for ev in events:
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    out.append(block["text"])
    return "\n".join(out)


def find_transcript(session_id):
    projects = os.path.expanduser("~/.claude/projects")
    for root, _dirs, files in os.walk(projects):
        for f in files:
            if f == session_id + ".jsonl":
                return os.path.join(root, f)
    return None


# -- spikes -----------------------------------------------------------------

def spike_a():
    """Turn injection: two user turns into ONE -p stream-json process."""
    sid = str(uuid.uuid4())
    proc = launch(["-p", "--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose", "--model", "haiku", "--session-id", sid])
    s = Stream(proc, os.path.join(RESULTS, "A-events.jsonl"))
    report = {"session_id": sid, "turns": []}

    send_user(proc, "Reply with exactly the single word: PONG1")
    evs = []
    r1 = s.wait_event(is_result, 120, evs)
    report["turns"].append({"n": 1, "ok": r1 is not None,
                            "text": assistant_text(evs), "result": r1})

    evs = []
    send_user(proc, "Reply with exactly the single word: PONG2")
    r2 = s.wait_event(is_result, 120, evs)
    report["turns"].append({"n": 2, "ok": r2 is not None,
                            "text": assistant_text(evs), "result": r2})

    # a little ballast so /compact in spike B has something to chew on
    evs = []
    send_user(proc, "List the numbers 1 to 40, one per line, no other text.")
    r3 = s.wait_event(is_result, 120, evs)
    report["turns"].append({"n": 3, "ok": r3 is not None, "chars": len(assistant_text(evs))})

    proc.stdin.close()
    proc.wait(timeout=30)
    report["stderr_tail"] = s.err_lines[-5:]
    verdict = all(t["ok"] for t in report["turns"])
    report["VERDICT"] = ("PASS: multi-turn injection into one process works" if verdict
                         else "FAIL: see events log")
    st = load_state()
    st["sid_a"] = sid
    save_state(st)
    return report


def spike_b():
    """/compact as a stream-json user turn, resuming spike A's session."""
    st = load_state()
    sid = st["sid_a"]
    proc = launch(["-p", "--resume", sid, "--input-format", "stream-json",
                   "--output-format", "stream-json", "--verbose", "--model", "haiku"])
    s = Stream(proc, os.path.join(RESULTS, "B-events.jsonl"))
    report = {"resumed": sid}

    evs = []
    send_user(proc, "/compact")
    r = s.wait_event(is_result, 180, evs)
    report["events_seen"] = [{"type": e.get("type"), "subtype": e.get("subtype")} for e in evs]
    report["result"] = r
    report["assistant_text"] = assistant_text(evs)[:2000]

    proc.stdin.close()
    proc.wait(timeout=30)
    report["stderr_tail"] = s.err_lines[-5:]

    # ground truth: inspect the transcript for compaction artifacts
    time.sleep(1)
    tpath = find_transcript(sid)
    compact_markers = []
    if tpath:
        for line in open(tpath, encoding="utf-8", errors="replace"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = rec.get("type", "")
            if ("compact" in json.dumps(rec).lower()[:400] or t == "summary"):
                compact_markers.append({"type": t, "subtype": rec.get("subtype"),
                                        "keys": sorted(rec.keys())[:10]})
    report["transcript"] = tpath
    report["compact_markers"] = compact_markers[:10]
    return report


def spike_c():
    """--resume with a changed --model: does the resumed turn run on the new model?"""
    st = load_state()
    sid = st["sid_a"]
    proc = launch(["-p", "--resume", sid, "--model", "sonnet", "--output-format", "json",
                   "Reply with exactly the single word: PONG3"])
    out, err = proc.communicate(timeout=180)
    open(os.path.join(RESULTS, "C-out.json"), "w", encoding="utf-8").write(out)
    report = {"exit": proc.returncode, "stderr_tail": err.strip().splitlines()[-3:]}
    try:
        res = json.loads(out)
        report["modelUsage_keys"] = list(res.get("modelUsage", {}).keys())
        report["result_text"] = res.get("result", "")[:200]
        # session id of the resumed turn (resume KEEPS the id unless --fork-session)
        report["result_session_id"] = res.get("session_id")
    except json.JSONDecodeError:
        report["raw"] = out[:1500]
        return report

    time.sleep(1)
    tpath = find_transcript(report.get("result_session_id") or sid)
    models = []
    if tpath:
        for line in open(tpath, encoding="utf-8", errors="replace"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = rec.get("message", {})
            if rec.get("type") == "assistant" and m.get("model"):
                models.append(m["model"])
    report["assistant_models_in_transcript"] = models
    report["VERDICT"] = ("PASS: resumed turn ran on " + models[-1]
                         if models and "sonnet" in models[-1] else "CHECK events")
    return report


def spike_d():
    """--append-system-prompt on --resume: is a NEW appended prompt honored?"""
    st = load_state()
    sid = st["sid_a"]
    proc = launch(["-p", "--resume", sid, "--model", "haiku",
                   "--append-system-prompt",
                   "IMPORTANT: end every reply with the single word ZANZIBAR.",
                   "--output-format", "json", "Say hi in three words or fewer."])
    out, err = proc.communicate(timeout=180)
    open(os.path.join(RESULTS, "D-out.json"), "w", encoding="utf-8").write(out)
    report = {"exit": proc.returncode}
    try:
        res = json.loads(out)
        report["result_text"] = res.get("result", "")[:300]
        report["VERDICT"] = ("PASS: appended prompt honored on resume"
                             if "ZANZIBAR" in res.get("result", "").upper()
                             else "FAIL: marker absent — appended prompt NOT honored on resume")
    except json.JSONDecodeError:
        report["raw"] = out[:1500]
    return report


def spike_e():
    """Permission modes headless: default vs dontAsk vs delegate; dontAsk+allowlist."""
    cases = [
        ("default-deny", ["--permission-mode", "default"], []),
        ("dontAsk-deny", ["--permission-mode", "dontAsk"], []),
        ("dontAsk-allowlist", ["--permission-mode", "dontAsk",
                               "--allowedTools", "Bash(echo:*)"], []),
    ]
    ask = ("Run this exact bash command using the Bash tool: echo SPIKE_OK\n"
           "Then report the command output verbatim. If the tool call is refused, "
           "reply with exactly: TOOL_REFUSED <reason in 10 words>")
    report = {}
    for name, flags, _ in cases:
        proc = launch(["-p", "--model", "haiku", "--output-format", "json"] + flags + [ask])
        try:
            out, err = proc.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill()
            report[name] = {"VERDICT": "TIMEOUT (likely waiting on a prompt it cannot show)"}
            continue
        open(os.path.join(RESULTS, f"E-{name}.json"), "w", encoding="utf-8").write(out)
        try:
            res = json.loads(out)
            report[name] = {
                "result_text": res.get("result", "")[:300],
                "denials": res.get("permission_denials", []),
                "ran": "SPIKE_OK" in res.get("result", ""),
            }
        except json.JSONDecodeError:
            report[name] = {"raw": out[:800], "stderr": err.strip()[-400:]}

    # delegate over stream-json: watch for control_request permission traffic
    proc = launch(["-p", "--permission-mode", "delegate", "--model", "haiku",
                   "--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose"])
    s = Stream(proc, os.path.join(RESULTS, "E-delegate-events.jsonl"))
    send_user(proc, ask)
    evs = []
    r = s.wait_event(lambda e: is_result(e) or e.get("type") == "control_request", 120, evs)
    report["delegate-stream"] = {
        "terminal_event": {k: r.get(k) for k in ("type", "subtype", "request_id")} if r else None,
        "event_types": [e.get("type") for e in evs],
        "control_request_full": r if r and r.get("type") == "control_request" else None,
        "assistant_text": assistant_text(evs)[:300],
    }
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        report["delegate-stream"]["note"] = "process killed after wait timeout"
    report["delegate-stderr"] = s.err_lines[-5:]
    return report


def spike_f():
    """№24: last assistant message's usage = context occupancy; summing overcounts."""
    st = load_state()
    sid = st["sid_a"]
    tpath = find_transcript(sid)
    if not tpath:
        return {"VERDICT": "FAIL: transcript not found"}
    per_msg = []
    for line in open(tpath, encoding="utf-8", errors="replace"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") == "assistant":
            u = rec.get("message", {}).get("usage")
            if u:
                per_msg.append({
                    "input": u.get("input_tokens", 0),
                    "cache_read": u.get("cache_read_input_tokens", 0),
                    "cache_creation": u.get("cache_creation_input_tokens", 0),
                    "output": u.get("output_tokens", 0),
                })
    occ = [(m["input"] + m["cache_read"] + m["cache_creation"]) for m in per_msg]
    return {
        "transcript": tpath,
        "assistant_messages_with_usage": len(per_msg),
        "per_message_occupancy": occ,
        "last_message_occupancy": occ[-1] if occ else None,
        "sum_across_turns_would_be": sum(occ),
        "VERDICT": ("PASS: fields present; last≈context, sum={} vs last={} (overcount x{:.1f})"
                    .format(sum(occ), occ[-1], sum(occ) / occ[-1]) if occ else "no usage found"),
    }


SPIKES = {"A": spike_a, "B": spike_b, "C": spike_c, "D": spike_d, "E": spike_e, "F": spike_f}

if __name__ == "__main__":
    which = sys.argv[1].upper()
    rep = SPIKES[which]()
    path = os.path.join(RESULTS, f"{which}-report.json")
    json.dump(rep, open(path, "w", encoding="utf-8"), indent=2)
    print(json.dumps(rep, indent=2)[:6000])
