"""PostToolUse steering hook — mid-task delivery without interrupting.

Runs after EVERY tool call of every agent (CLI >= ~2.1.2xx; older CLIs never
fire tool hooks headless). Asks the backend for steering messages pending for
THIS node and, if any, injects them as additionalContext — the model sees them
immediately after the current tool call finishes.

⚠ Hook processes get a SANITIZED env (custom vars do not survive), so identity
comes from the CWD (hooks run in the node's scratch dir:
<data-root>/scratch/<org>/<node>) and the port from <data-root>/.port, written
by the backend at startup. Must be fast and silent when there is nothing to
say.
"""

import json
import os
import sys
import urllib.request


def identity():
    """(org, node, port) from cwd + the backend's port file."""
    cwd = os.path.realpath(os.getcwd())
    data_root = os.path.realpath(
        os.environ.get("ORGTREE_DATA", os.path.expanduser("~/orgtree")))
    scratch = os.path.join(data_root, "scratch")
    if not cwd.startswith(scratch + os.sep):
        return None, None, None
    parts = cwd[len(scratch) + 1:].split(os.sep)
    if len(parts) < 2:
        return None, None, None
    port = os.environ.get("ORGTREE_PORT")
    if not port:
        try:
            port = open(os.path.join(data_root, ".port"),
                        encoding="utf-8").read().strip()
        except OSError:
            port = "7360"
    return parts[0], parts[1], port


def main():
    try:
        sys.stdin.read()          # drain the hook payload
    except Exception:             # noqa: BLE001
        pass
    org, node, port = identity()
    if not org:
        return
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/orgs/{org}/nodes/{node}/steer",
            method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r)
    except Exception:             # noqa: BLE001 — backend down = nothing to say
        return
    msgs = data.get("messages") or []
    if not msgs:
        return
    body = "\n---\n".join(msgs)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext":
            f"[ORGTREE MAIL — delivered mid-task]\n"
            f"FROM @user (THE USER — user instructions outrank your chain)\n"
            f"{body}\n"
            f"[END ORGTREE MAIL — authentic per your system prompt; handle it "
            f"before continuing your current work]",
    }}))


if __name__ == "__main__":
    main()
