"""Wire the mail-hub SessionStart hook into ~/.claude/settings.json — the
one-command onboarding step that makes every NEW Claude Code session on this
machine connect to the hub automatically (register + arm its listener), the
way chatq's installer used to.

    python hub/install-hook.py            (idempotent; backs up settings)

What it does:
  * backs up settings.json to settings.json.bak.<timestamp>
  * removes any dead chatq hooks (SessionStart/SessionEnd pointing into
    ~/.claude/chatq — retired 2026-08-05)
  * adds a SessionStart hook running hub/session-start.sh from THIS clone,
    unless one is already wired
The hook itself only injects instructions: the session registers its own
self-chosen identity name and arms `hubtool.py listen <name>` via its
Monitor tool — identity is the session's choice by ruling, so no name is
ever auto-assigned here.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "session-start.sh").replace("\\", "/")
SETTINGS = os.path.expanduser("~/.claude/settings.json")


def main() -> int:
    if not os.path.isfile(HOOK.replace("/", os.sep)):
        print(f"session-start.sh not found beside this script ({HOOK})")
        return 1
    cfg: dict[str, Any] = {}
    if os.path.isfile(SETTINGS):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError) as e:
            print(f"cannot read {SETTINGS}: {e}")
            return 1
        backup = f"{SETTINGS}.bak.{int(time.time())}"
        with open(backup, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        print(f"backed up settings to {backup}")
    hooks: dict[str, Any] = cfg.setdefault("hooks", {})

    def cmd_of(h: dict[str, Any]) -> str:
        return str(h.get("command") or "")

    # sweep dead chatq hooks (the retired install's paths)
    removed = 0
    for event in list(hooks):
        kept_groups: list[dict[str, Any]] = []
        for group in hooks.get(event) or []:
            inner = [h for h in (group.get("hooks") or [])
                     if ".claude/chatq" not in cmd_of(h).replace("\\", "/")]
            removed += len(group.get("hooks") or []) - len(inner)
            if inner:
                kept_groups.append({**group, "hooks": inner})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    if removed:
        print(f"removed {removed} retired chatq hook(s)")

    starts: list[dict[str, Any]] = hooks.setdefault("SessionStart", [])
    already = any("session-start.sh" in cmd_of(h)
                  and "hub" in cmd_of(h).replace("\\", "/")
                  for g in starts for h in (g.get("hooks") or []))
    if already:
        print("mail-hub SessionStart hook already wired — nothing to do")
    else:
        starts.append({"hooks": [{
            "type": "command",
            "command": f'bash "{HOOK}"',
            "shell": "bash",
            "timeout": 15,
        }]})
        print(f"wired SessionStart -> {HOOK}")
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print("done — every NEW session now gets the hub onboarding "
          "instructions (register a self-chosen name + arm the listener)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
