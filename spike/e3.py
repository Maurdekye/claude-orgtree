#!/usr/bin/env python3
"""Spike E3 — E2's write matrix, but from a NEUTRAL cwd (outside ~/.claude, which is
sensitive-path protected and poisoned every E2 case)."""

import json
import os
import subprocess

from run import RESULTS, clean_env, CLAUDE, Stream, send_user, is_result, assistant_text

BASE = os.path.expanduser("~/orgtree-spike-tmp")
CWD = os.path.join(BASE, "cwd")
OUTSIDE = os.path.join(BASE, "outside")
for d in (CWD, OUTSIDE):
    os.makedirs(d, exist_ok=True)


def launch_at(args, cwd):
    return subprocess.Popen(["cmd", "/c", CLAUDE] + args, cwd=cwd, env=clean_env(),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)


def ask(path):
    return ("Use the Write tool to create the file {} containing exactly: OK\n"
            "If the tool call is refused or errors, reply with exactly: "
            "TOOL_REFUSED: <the reason in ten words or fewer>").format(path.replace("\\", "/"))


def one_shot(name, flags, target):
    if os.path.exists(target):
        os.remove(target)
    proc = launch_at(["-p", "--model", "haiku", "--output-format", "json"] + flags, CWD)
    try:
        out, err = proc.communicate(input=ask(target), timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"VERDICT": "TIMEOUT"}
    open(os.path.join(RESULTS, "E3-{}.json".format(name)), "w", encoding="utf-8").write(out)
    rep = {}
    try:
        res = json.loads(out)
        rep["text"] = res.get("result", "")[:180]
        rep["denied"] = bool(res.get("permission_denials"))
    except json.JSONDecodeError:
        rep["raw"] = out[:300]
        rep["stderr"] = err.strip()[-250:]
    rep["file_written"] = os.path.exists(target)
    if rep["file_written"]:
        os.remove(target)
    return rep


def main():
    in_cwd = os.path.join(CWD, "probe1.txt")
    outside = os.path.join(OUTSIDE, "probe2.txt")
    report = {}
    report["default/in-cwd"] = one_shot("default-in", ["--permission-mode", "default"], in_cwd)
    report["dontAsk/in-cwd"] = one_shot("dontask-in", ["--permission-mode", "dontAsk"], in_cwd)
    report["dontAsk/outside"] = one_shot("dontask-out", ["--permission-mode", "dontAsk"], outside)
    report["dontAsk/outside+add-dir"] = one_shot(
        "dontask-adddir", ["--permission-mode", "dontAsk", "--add-dir", OUTSIDE], outside)
    report["acceptEdits/in-cwd"] = one_shot(
        "acceptedits-in", ["--permission-mode", "acceptEdits"], in_cwd)

    # delegate over stream-json from neutral cwd
    if os.path.exists(in_cwd):
        os.remove(in_cwd)
    proc = launch_at(["-p", "--permission-mode", "delegate", "--model", "haiku",
                      "--input-format", "stream-json", "--output-format", "stream-json",
                      "--verbose"], CWD)
    s = Stream(proc, os.path.join(RESULTS, "E3-delegate-events.jsonl"))
    send_user(proc, ask(in_cwd))
    evs = []
    r = s.wait_event(lambda e: is_result(e) or e.get("type") == "control_request", 120, evs)
    report["delegate/in-cwd"] = {
        "terminal": {k: r.get(k) for k in ("type", "subtype")} if r else "TIMEOUT",
        "control_request": r if r and r.get("type") == "control_request" else None,
        "event_types": [e.get("type") for e in evs],
        "text": assistant_text(evs)[:180],
        "file_written": os.path.exists(in_cwd),
    }
    try:
        proc.stdin.close()
        proc.wait(timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()

    json.dump(report, open(os.path.join(RESULTS, "E3-report.json"), "w", encoding="utf-8"),
              indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
