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

from __future__ import annotations

import json
import os
import sys
import urllib.request


def identity() -> tuple[str | None, str | None, str | None, str | None]:
    """(org, node, base_url, secret) — org+node from argv when the backend
    passed them (it does since review C10), the cwd split as fallback.

    ⚠ The cwd is SHARED across a lineage: scratch_dir maps "name@gen" to the
    base "name" directory, so a live knowledge bearer's hook resolved as its
    SUCCESSOR and was handed (and confirmed away) the successor's steered
    mail. argv names the exact node the backend launched.

    Sandboxed kiosk containers mirror the host layout at ~/orgtree, so the
    cwd derivation is identical there; a `.bridge` file in the data root
    (written by the host into the mounted sandbox home) carries the
    off-container backend URL + the org's bridge secret."""
    cwd = os.path.realpath(os.getcwd())
    data_root = os.path.realpath(
        os.environ.get("ORGTREE_DATA", os.path.expanduser("~/orgtree")))
    scratch = os.path.join(data_root, "scratch")
    if len(sys.argv) >= 3 and sys.argv[1] and sys.argv[2]:
        org, node = sys.argv[1], sys.argv[2]
    else:
        if not cwd.startswith(scratch + os.sep):
            return None, None, None, None
        parts = cwd[len(scratch) + 1:].split(os.sep)
        if len(parts) < 2:
            return None, None, None, None
        org, node = parts[0], parts[1]
    try:
        b = json.load(open(os.path.join(data_root, ".bridge"), encoding="utf-8"))
        return org, node, b["url"].rstrip("/"), b.get("secret", "")
    except (OSError, ValueError, KeyError):
        pass
    port = os.environ.get("ORGTREE_PORT")
    if not port:
        try:
            port = open(os.path.join(data_root, ".port"),
                        encoding="utf-8").read().strip()
        except OSError:
            port = "7360"
    return org, node, f"http://127.0.0.1:{port}", ""


def main() -> None:
    try:
        sys.stdin.read()          # drain the hook payload
    except Exception:             # noqa: BLE001
        pass
    org, node, base, secret = identity()
    if not org:
        return
    try:
        req = urllib.request.Request(
            f"{base}/api/orgs/{org}/nodes/{node}/steer", method="POST")
        if secret:
            req.add_header("X-Orgtree-Bridge", secret)
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.load(r)
    except Exception:             # noqa: BLE001 — backend down = nothing to say
        return
    msgs = data.get("messages") or []
    if not msgs:
        return
    body = "\n---\n".join(msgs)
    # sender attribution (FROM @user / FROM @agent lines) is already inside
    # each message — the wrapper stays sender-neutral so agent mail is never
    # mislabeled with user authority
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext":
            f"[ORGTREE MAIL — delivered mid-task]\n"
            f"{body}\n"
            f"[END ORGTREE MAIL — authentic per your system prompt; each "
            f"message has the authority of its stated sender; handle it "
            f"before continuing your current work]",
    }}))


if __name__ == "__main__":
    main()
