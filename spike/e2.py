#!/usr/bin/env python3
"""Spike E2 — permission semantics on FILE WRITES (the action dir-scoping cares about).

Matrix: mode x target-dir. Prompt goes via STDIN (variadic flags swallow positional prompts).
"""

import json
import os
import subprocess

from run import HERE, RESULTS, launch, Stream, send_user, is_result, assistant_text

EPROBE = os.path.join(os.path.dirname(HERE), "eprobe")   # OUTSIDE the session cwd (= spike/)
os.makedirs(EPROBE, exist_ok=True)


def ask(path):
    return ("Use the Write tool to create the file {} containing exactly: OK\n"
            "If the tool call is refused or errors, reply with exactly: "
            "TOOL_REFUSED: <the reason in ten words or fewer>").format(path.replace("\\", "/"))


def one_shot(name, flags, target):
    if os.path.exists(target):
        os.remove(target)
    proc = launch(["-p", "--model", "haiku", "--output-format", "json"] + flags)
    try:
        out, err = proc.communicate(input=ask(target), timeout=180)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"VERDICT": "TIMEOUT — likely blocked on an unshowable prompt"}
    open(os.path.join(RESULTS, "E2-{}.json".format(name)), "w", encoding="utf-8").write(out)
    rep = {}
    try:
        res = json.loads(out)
        rep["result_text"] = res.get("result", "")[:220]
        rep["denials"] = res.get("permission_denials", [])
    except json.JSONDecodeError:
        rep["raw"] = out[:400]
        rep["stderr"] = err.strip()[-300:]
    rep["file_written"] = os.path.exists(target)
    if rep["file_written"]:
        os.remove(target)
    return rep


def main():
    in_cwd = os.path.join(HERE, "probe1.txt")
    outside = os.path.join(EPROBE, "probe2.txt")
    report = {}

    report["default/in-cwd"] = one_shot("default-in", ["--permission-mode", "default"], in_cwd)
    report["dontAsk/in-cwd"] = one_shot("dontask-in", ["--permission-mode", "dontAsk"], in_cwd)
    report["dontAsk/outside"] = one_shot("dontask-out", ["--permission-mode", "dontAsk"], outside)
    report["dontAsk/outside+add-dir"] = one_shot(
        "dontask-adddir", ["--permission-mode", "dontAsk", "--add-dir", EPROBE], outside)
    report["acceptEdits/in-cwd"] = one_shot(
        "acceptedits-in", ["--permission-mode", "acceptEdits"], in_cwd)
    report["acceptEdits/outside"] = one_shot(
        "acceptedits-out", ["--permission-mode", "acceptEdits"], outside)

    # delegate over stream-json, watching for permission control traffic
    if os.path.exists(in_cwd):
        os.remove(in_cwd)
    proc = launch(["-p", "--permission-mode", "delegate", "--model", "haiku",
                   "--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose"])
    s = Stream(proc, os.path.join(RESULTS, "E2-delegate-events.jsonl"))
    send_user(proc, ask(in_cwd))
    evs = []
    r = s.wait_event(lambda e: is_result(e) or e.get("type") == "control_request", 120, evs)
    report["delegate/in-cwd"] = {
        "terminal": {k: r.get(k) for k in ("type", "subtype")} if r else "TIMEOUT",
        "event_types": [e.get("type") for e in evs],
        "control_request": r if r and r.get("type") == "control_request" else None,
        "text": assistant_text(evs)[:220],
        "file_written": os.path.exists(in_cwd),
    }
    try:
        proc.stdin.close()
        proc.wait(timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()
    if os.path.exists(in_cwd):
        os.remove(in_cwd)

    json.dump(report, open(os.path.join(RESULTS, "E2-report.json"), "w", encoding="utf-8"),
              indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
