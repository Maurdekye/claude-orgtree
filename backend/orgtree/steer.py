"""PostToolUse steering hook — the org's answer to "deliver my message NOW,
but don't interrupt".

The CLI drops stdin user events written during a response, so mid-task
delivery has exactly one legal channel: a PostToolUse hook, which runs after
every tool call and whose additionalContext reaches the model immediately.
This script asks the backend for any steering messages pending for THIS node
(identity from env, set by the supervisor) and injects them. It must be fast
and silent when there is nothing to say — it runs on every tool call of every
agent.
"""

import json
import os
import sys
import urllib.request


def main():
    try:
        sys.stdin.read()          # drain the hook payload; identity comes from env
    except Exception:             # noqa: BLE001
        pass
    org = os.environ.get("ORGTREE_ORG", "")
    node = os.environ.get("ORGTREE_NODE", "")
    port = os.environ.get("ORGTREE_PORT", "7360")
    if not org or not node:
        return
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/orgs/{org}/nodes/{node}/steer",
            method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r)
    except Exception:             # noqa: BLE001 — backend down = nothing to steer
        return
    msgs = data.get("messages") or []
    if not msgs:
        return
    body = "\n---\n".join(msgs)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext":
            f"[USER MESSAGE — arrived mid-task, handle it before continuing]\n"
            f"{body}\n[END USER MESSAGE]",
    }}))


if __name__ == "__main__":
    main()
