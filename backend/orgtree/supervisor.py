"""Session supervisor — turns ledger rows into real Claude Code sessions.

Attachment strategy is resume-on-demand (№3): no idle processes. A node is a session
UUID; each delivered message runs ONE turn via `claude -p` (first turn `--session-id`,
later `--resume`). Spike-verified flags (spike/FINDINGS.md):

  - prompt goes via STDIN (variadic flags swallow positional prompts)
  - full model ids only (aliases drift)
  - `--permission-mode acceptEdits` + `--add-dir <granted>` = autonomy within dirs (№5)
  - `--append-system-prompt` is honored on resume → identity regenerated every turn (№29)
  - `--settings {"disableAllHooks":true}` + `--strict-mcp-config` isolate the node from
    the user's global hooks and MCP servers
  - node cwd must live OUTSIDE ~/.claude → scratch under the data root

Runtime state (busy flags, queues) is in-memory only; the ledger stays the source of
truth for live/archived. A server restart loses in-flight turns, never ledger state.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import threading

from . import store
from .ledger import USER, Org, now as now_iso

COMPACT_AT = float(os.environ.get("ORGTREE_COMPACT_AT", "0.80"))   # §8.2
ORACLE_AT = float(os.environ.get("ORGTREE_ORACLE_AT", "0.92"))     # §8.3 state 2→3
BACKEND_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

# the Claude Code CLI: found on PATH, or pinned via ORGTREE_CLAUDE
CLAUDE = os.environ.get("ORGTREE_CLAUDE") or shutil.which("claude") or "claude"
# ⚠️ On Windows, never launch through the .CMD shim via `cmd /c`: cmd truncates
# argv at an embedded newline, and the identity prompt is multiline (org
# charts). Invoking node + cli.js directly passes newlines through
# CreateProcess intact. The .CMD shim is a last resort.
CLAUDE_CLI_JS = os.environ.get("ORGTREE_CLAUDE_CLI", os.path.join(
    os.path.dirname(CLAUDE), "node_modules", "@anthropic-ai", "claude-code", "cli.js"))


def _claude_argv() -> list[str]:
    if os.path.exists(CLAUDE_CLI_JS):
        return ["node", CLAUDE_CLI_JS]
    if os.name == "nt" and CLAUDE.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", CLAUDE]
    return [CLAUDE]
TURN_TIMEOUT = int(os.environ.get("ORGTREE_TURN_TIMEOUT", "1800"))   # seconds
MAX_CONCURRENT = int(os.environ.get("ORGTREE_MAX_TURNS", "3"))       # №34

_turn_slots = threading.Semaphore(MAX_CONCURRENT)
_state: dict[tuple[str, str], dict] = {}
_state_lock = threading.Lock()

# set by the API layer so worker threads can push websocket events
notify = lambda slug, node, event: None   # noqa: E731
stream = lambda slug, node, payload: None   # noqa: E731 — live per-message feed


def state(slug: str, nid: str) -> dict:
    with _state_lock:
        return _state.setdefault((slug, nid), {
            "busy": False, "queue": [], "last_error": None, "turns_run": 0,
            "last_status": None, "occupancy": None, "context_window": None})


def scratch_dir(slug: str, nid: str) -> str:
    # lineage nodes ("name@gen") share their successor's scratch — they are the same
    # self at different times, and the CLAUDE.md self-notes belong to that self
    p = os.path.join(store.scratch_root(slug), nid.split("@")[0])
    os.makedirs(p, exist_ok=True)
    return p


def transcript_path(session_id: str) -> str | None:
    hits = glob.glob(os.path.join(
        os.path.expanduser("~/.claude"), "projects", "*", session_id + ".jsonl"))
    return hits[0] if hits else None


def clean_env() -> dict:
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE":
            env.pop(k, None)
    return env


def _looks_like_usage_limit(blob: str) -> bool:
    b = blob.lower()
    return ("limit" in b and any(w in b for w in
                                 ("usage", "weekly", "reached", "exceeded", "quota")))


def registered_mcp_servers() -> dict:
    """The user's globally registered MCP servers (~/.claude.json → mcpServers)."""
    try:
        cfg = json.load(open(os.path.expanduser("~/.claude.json"), encoding="utf-8"))
        return cfg.get("mcpServers", {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}


# ------------------------------------------------------------------ identity
def _claudemd_block(org: Org, nid: str) -> str:
    """Granted-folder CLAUDE.md files, injected explicitly (spike-verified: headless
    sessions do NOT surface them natively; the scratch cwd's own CLAUDE.md DOES load
    natively, so it is deliberately not duplicated here)."""
    parts = []
    for d in org.node(nid)["scope"]["add_dirs"]:
        p = os.path.join(d["path"], "CLAUDE.md")
        if os.path.isfile(p):
            try:
                content = open(p, encoding="utf-8", errors="replace").read()[:6000]
            except OSError:
                continue
            parts.append(f"--- CLAUDE.md ({d['path']}) ---\n{content.strip()}")
    return "\n\n".join(parts)


def _claudemd_caveat(org: Org, nid: str) -> str:
    """User ruling 2026-07-29: top-level agents work directly under the user, so
    CLAUDE.md files apply literally to them. Deeper agents read the same files
    verbatim EXCEPT that user-communication instructions redirect to their direct
    superior — unless they currently hold a user audience."""
    n = org.node(nid)
    if n["parent"] is None:
        return ""
    if org._has_audience(nid, USER):
        return ("Note on CLAUDE.md guidance: you currently hold a USER AUDIENCE, so "
                "for its duration you may take instructions about communicating with "
                "the user literally. Once it is rescinded, redirect such instructions "
                f"to your direct superior ({n['parent']}) instead. ")
    return ("Note on CLAUDE.md guidance (here or in your folders/notes): it applies "
            "VERBATIM, with one reinterpretation — you do not have direct contact "
            "with the user. Read any instruction to communicate with, ask, report "
            "to, or get feedback from 'the user' as directed at your direct superior "
            f"({n['parent']}) instead. Everything else in those files is literal. ")
def _render_chart(org: Org, root_ids: list[str], mark: str, indent: int = 0) -> list[str]:
    lines = []
    for rid in root_ids:
        n = org.nodes[rid]
        state = "" if n["state"] == "live" else f" ({n['state']})"
        star = "  ← you" if rid == mark else ""
        lines.append(f"{'  ' * indent}- {rid} [{n['model']}]{state}{star}")
        lines += _render_chart(org, org.children(rid, live_only=False), mark, indent + 1)
    return lines


def identity_prompt(org: Org, nid: str) -> str:
    """№29: stable identity + org position, regenerated fresh every turn. How much
    of the org chart it reveals is the node's `org_visibility` scope (delegateable):
    self → itself + reports · team → + parent & peers by name · subtree → + its full
    subtree · full → the entire chart down from the user."""
    n = org.node(nid)
    sc = n["scope"]
    vis = sc.get("org_visibility", "team")
    kids = org.children(nid) or ["none yet"]

    if vis == "self":
        position = (f"Your reports: {', '.join(kids)}. You have a superior you can "
                    f"escalate to; its identity is not disclosed to you.")
    else:
        parent = n["parent"] or "the user"
        sibs = [s for s in org.children(n["parent"]) if s != nid] or ["none"]
        position = (f"Your superior: {parent}. Your reports: {', '.join(kids)}. "
                    f"Your peers: {', '.join(sibs)}.")
    if vis == "subtree":
        position += ("\nYour full suborganization:\n"
                     + "\n".join(_render_chart(org, [nid], nid)))
    elif vis == "full":
        position += ("\nThe full organization chart (root = the user):\n- user (overseer)\n"
                     + "\n".join(_render_chart(org, org.children(None, live_only=False),
                                               nid, 1)))

    charter_bits = []
    if n.get("charter"):
        charter_bits.append(f"Your charter: {n['charter']}")
    chain = [a for a in reversed(org.ancestors(nid)) if a != USER]
    for a in chain:                       # §15 cascade: ancestors bind their subtrees
        tc = org.nodes[a].get("team_charter")
        if tc:
            charter_bits.append(f"Standing charter from your superior {a}: {tc}")
    charter_line = ("\n".join(charter_bits) + "\n") if charter_bits else ""

    dirs = sc.get("add_dirs", [])
    ro = [d["path"] for d in dirs if d["mode"] == "ro"]
    dir_line = ("Folders you may work in: "
                + (", ".join(d["path"] for d in dirs) or "only your own scratch folder")
                + (f". Read-only: {', '.join(ro)}" if ro else "") + ". ")
    tools = sc.get("tools", {})
    off = [label for key, label in (("bash", "the terminal"), ("web", "web access"),
                                    ("edit", "file editing"), ("subagents", "subagents"))
           if not tools.get(key, True)]
    tool_line = (f"Disabled for you: {', '.join(off)}. " if off else "")
    if tools.get("mcp"):
        tool_line += f"MCP servers available to you: {', '.join(tools['mcp'])}. "
    purpose_line = f"Your purpose: {n['purpose']} " if n.get("purpose") else ""
    fable_line = ""
    if org.d.get("fable_lock"):
        fable_line = ("Note: the weekly Fable usage limit is exhausted — fable agents "
                      "cannot actually run until it resets or the user intervenes. "
                      "Hiring or rehiring fable-tier agents now would be futile (it is "
                      "permitted, but they would just fail); prefer another tier. ")

    return (
        f'You are "{nid}", an agent in the organization "{org.d["name"]}" (orgtree). '
        f"{purpose_line}{position}\n{charter_line}"
        f"Credits: seat {org.seat_cost(nid)}, grant {n['grant']}, free {org.free(nid):g} "
        f"— credits bound concurrent agent capacity, not tokens. "
        f"{dir_line}{tool_line}{fable_line}"
        f"Escalate decisions to your superior rather than the user unless the user "
        f"addresses you directly. You act when messaged. Use the orgtree MCP tools "
        f"to act on the org: orgtree_message (reach your reports at any depth, your "
        f"superior, your peers), orgtree_hire (you must state purpose, folders, every "
        f"tool switch and visibility — no defaults), orgtree_retire/rehire/dissolve/"
        f"reallocate, orgtree_chart. REQUIRED: call orgtree_status when you finish "
        f"(done) or get stuck (blocked) — that is how your superior learns of it. "
        f"Your scratch folder is your own: keep a CLAUDE.md there as standing notes — "
        f"it is loaded automatically every turn and survives compaction. "
        + _claudemd_caveat(org, nid)
        + (("\n\n[STANDING INSTRUCTIONS from your granted folders]\n" + cmd_block)
           if (cmd_block := _claudemd_block(org, nid)) else "")
    )


# --------------------------------------------------------------------- turns
def _build_cmd(org: Org, nid: str) -> list[str]:
    n = org.node(nid)
    slug = org.d["slug"]
    sid = n["session_id"]
    first = transcript_path(sid) is None
    model = org.d["models"].get(n["model"], n["model"])
    sc = n["scope"]
    settings: dict = {"disableAllHooks": True}
    ro_paths = [d["path"] for d in sc["add_dirs"] if d["mode"] == "ro"]
    if ro_paths:
        # read-only enforcement: permission deny rules on the writing tools
        deny = []
        for p in ro_paths:
            p = p.replace("\\", "/").rstrip("/")
            deny += [f"Edit({p}/**)", f"Write({p}/**)", f"NotebookEdit({p}/**)"]
        settings["permissions"] = {"deny": deny}
    cmd = _claude_argv() + ["-p",
           "--output-format", "stream-json", "--verbose",
           "--model", model,
           "--permission-mode", sc.get("permission_mode", "acceptEdits"),
           "--append-system-prompt", identity_prompt(org, nid),
           "--settings", json.dumps(settings),
           "--strict-mcp-config"]
    tools = sc.get("tools", {})
    disallowed = []
    if not tools.get("bash", True):
        disallowed += ["Bash"]
    if not tools.get("web", True):
        disallowed += ["WebSearch", "WebFetch"]
    if not tools.get("edit", True):
        disallowed += ["Edit", "Write", "NotebookEdit"]
    if not tools.get("subagents", True):
        disallowed += ["Task", "Agent"]
    if disallowed:
        cmd += ["--disallowed-tools", ",".join(disallowed)]
    # every node gets the orgtree MCP server — its hands on the org — plus any
    # user-registered servers it was granted; --strict-mcp-config pins the set
    registry = registered_mcp_servers()
    chosen = {k: registry[k] for k in (tools.get("mcp") or []) if k in registry}
    chosen["orgtree"] = {
        "command": sys.executable,
        "args": ["-m", "orgtree.mcptool"],
        "env": {"ORGTREE_ORG": slug, "ORGTREE_NODE": nid,
                "ORGTREE_PORT": os.environ.get("ORGTREE_PORT", "7360"),
                "PYTHONPATH": BACKEND_DIR},
    }
    cmd += ["--mcp-config", json.dumps({"mcpServers": chosen})]
    # MCP tools are not auto-approved headless — allowlist exactly the granted servers
    cmd += ["--allowedTools", ",".join(f"mcp__{k}" for k in sorted(chosen))]
    for d in sc["add_dirs"]:
        cmd += ["--add-dir", d["path"]]
    # §7.6 read-down: a node's file tools reach its own scratch (cwd) plus every
    # descendant's — regenerated per turn, so re-parenting never leaves stale access
    seen = set()
    for k in org.descendants(nid, live_only=False):
        p = scratch_dir(org.d["slug"], k)
        if p not in seen:
            seen.add(p)
            cmd += ["--add-dir", p]
    if n.get("bearer_state") == "preserving":
        # §8.4: preserving oracle — resume + fork, converse, discard. The canonical
        # session is never written; we simply never record the fork's session id.
        cmd += ["--resume", sid, "--fork-session"]
    else:
        cmd += ["--session-id", sid] if first else ["--resume", sid]
    return cmd


def _run_turn(slug: str, nid: str, text: str):
    st = state(slug, nid)
    try:
        with _turn_slots:
            with store.DOC_LOCK:
                org = store.load_org(slug)
                if org.node(nid)["state"] != "live":
                    raise RuntimeError(f"{nid} is not live")
                if org.node(nid).get("limit_locked"):
                    raise RuntimeError(
                        "halted: weekly Fable usage limit exhausted — waiting for the "
                        "limit to reset or the user to intervene")
                # NOT locked fable nodes under a fable_lock (e.g. rehired anyway) are
                # allowed to TRY — the real limit rejects them naturally (user ruling:
                # the gate is a suggestion, reality is the enforcement)
                # drain notices + mail atomically — the №27 envelope, delivered at
                # the turn boundary (§7.4); nothing wakes anyone, nothing arrives twice
                pending = (org.d.get("notices") or {}).pop(nid, None)
                mail = org.take_mail(nid)
                if pending or mail:
                    store.save_org(org)
            prelude = []
            if pending:
                lines = "\n".join(f"- {p['at']}: {p['text']}" for p in pending)
                prelude.append(f"[ORG NOTICES — {len(pending)} change(s) since your "
                               f"last turn]\n{lines}\n[END NOTICES]")
            if mail:
                blocks = []
                for m in mail:
                    tag = " ⚠ THE USER — user instructions outrank your chain" \
                        if m["from"] == USER else ""
                    blocks.append(f"FROM {m['from']} ({m.get('relationship', 'agent')}"
                                  f"{tag}) · {m.get('kind', 'message')} · {m['at']}\n"
                                  f"{m['body']}")
                prelude.append(f"[MAIL — {len(mail)} message(s)]\n"
                               + "\n---\n".join(blocks) + "\n[END MAIL]")
            if prelude:
                text = "\n\n".join(prelude) + "\n\n" + text
            notify(slug, nid, "turn_started")
            proc = subprocess.Popen(
                _build_cmd(org, nid), cwd=scratch_dir(slug, nid), env=clean_env(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace")
            res = {}
            timed_out = threading.Event()

            def _expire():
                timed_out.set()
                proc.kill()
            timer = threading.Timer(TURN_TIMEOUT, _expire)
            timer.start()
            try:
                proc.stdin.write(text)
                proc.stdin.close()
                for line in proc.stdout:      # live per-message feed to the UI
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "assistant":
                        for b in ev.get("message", {}).get("content", []):
                            if b.get("type") == "text" and b.get("text", "").strip():
                                stream(slug, nid, {"kind": "text",
                                                   "text": b["text"][:2000]})
                            elif b.get("type") == "tool_use":
                                stream(slug, nid, {"kind": "tool",
                                                   "text": b.get("name", "tool")})
                    elif ev.get("type") == "result":
                        res = ev
                err = proc.stderr.read()
                proc.wait()
            finally:
                timer.cancel()
            if timed_out.is_set():
                raise RuntimeError(f"turn timed out after {TURN_TIMEOUT}s and was killed")
            err_blob = " / ".join((err or "").strip().splitlines()[-3:]) \
                if proc.returncode != 0 else (
                    str(res.get("result", "")) if res.get("is_error") else "")
            if err_blob:
                if "No conversation found" in err_blob or "no conversation" in err_blob.lower():
                    with store.DOC_LOCK:
                        o2 = store.load_org(slug)
                        o2.mark_unrecoverable(nid, err_blob[:200])
                        store.save_org(o2)
                # user ruling: fable weekly-limit exhaustion → org-wide fable freeze
                if org.node(nid)["model"] == "fable" and _looks_like_usage_limit(err_blob):
                    with store.DOC_LOCK:
                        o2 = store.load_org(slug)
                        o2.fable_limit_hit(nid, err_blob)
                        store.save_org(o2)
                    notify(slug, nid, "fable_limit")
                raise RuntimeError(f"turn failed: {err_blob[:400] or 'no output'}")
            st["last_error"] = None
            st["turns_run"] += 1
            if org.node(nid).get("bearer_state") == "preserving":
                with store.DOC_LOCK:
                    o2 = store.load_org(slug)
                    log = o2.node(nid).setdefault("oracle_exchanges", [])
                    log.append({"q": text[-1500:], "a": str(res.get("result", ""))[:4000],
                                "at": now_iso()})
                    del log[:-40]
                    store.save_org(o2)
            _after_turn(slug, nid, org, res, st)
    except Exception as e:                                  # noqa: BLE001
        st["last_error"] = str(e)
    finally:
        nxt = None
        with _state_lock:
            if st["queue"]:
                nxt = st["queue"].pop(0)
            else:
                st["busy"] = False
        notify(slug, nid, "turn_done")
        if nxt is not None:
            _run_turn(slug, nid, nxt)


def _after_turn(slug: str, nid: str, org: Org, res: dict, st: dict):
    """Post-turn bookkeeping: dollar cost (№32), context occupancy (№24), and the
    §8 compaction split when occupancy crosses the threshold. Tolerates the node
    having been deleted mid-turn."""
    if nid not in org.nodes:
        return
    cost = float(res.get("total_cost_usd") or 0.0)
    u = res.get("usage") or {}
    occ = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
           + u.get("cache_creation_input_tokens", 0))
    cw = None
    for mu in (res.get("modelUsage") or {}).values():
        cw = mu.get("contextWindow") or cw
    st["occupancy"], st["context_window"] = occ or st["occupancy"], cw or st["context_window"]
    if cost or occ or cw:
        with store.DOC_LOCK:
            o2 = store.load_org(slug)
            if nid not in o2.nodes:
                return
            n = o2.node(nid)
            if cost:
                n["cost_usd"] = round(float(n.get("cost_usd") or 0.0) + cost, 6)
            # persisted so the UI context wheel survives server restarts
            if occ:
                n["occupancy"] = occ
            if cw:
                n["context_window"] = cw
            store.save_org(o2)
    n = org.node(nid)
    if n.get("bearer_state"):
        # §8.3: a predecessor NEVER re-compacts — it has already been compacted, in
        # the form of its successor. When its own headroom runs out it becomes a
        # preserving oracle: still answers, but exchanges are forked and discarded.
        if (n["bearer_state"] == "knowledge" and occ and cw
                and occ / cw >= ORACLE_AT):
            with store.DOC_LOCK:
                o2 = store.load_org(slug)
                o2.node(nid)["bearer_state"] = "preserving"
                o2._notify([o2.node(nid)["parent"]],
                           f'Knowledge bearer "{nid}" has exhausted its headroom and is '
                           f'now a PRESERVING ORACLE — it still answers, but exchanges '
                           f'are no longer retained by it.')
                store.save_org(o2)
        return
    if occ and cw and occ / cw >= COMPACT_AT:
        _compact_split(slug, nid)


def _compact_split(slug: str, nid: str):
    """§8/№18: fork the session, /compact the fork (the successor), retire the
    original in place as a knowledge bearer. The predecessor is never written again."""
    with store.DOC_LOCK:
        org = store.load_org(slug)
        n = org.node(nid)
        old_sid = n["session_id"]
        model = org.d["models"].get(n["model"], n["model"])
    argv = _claude_argv() + ["-p", "--output-format", "json",
                             "--resume", old_sid, "--fork-session",
                             "--model", model,
                             "--settings", json.dumps({"disableAllHooks": True}),
                             "--strict-mcp-config"]
    try:
        proc = subprocess.Popen(argv, cwd=scratch_dir(slug, nid), env=clean_env(),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace")
        out, _err = proc.communicate(input="/compact", timeout=600)
        res = json.loads(out) if out.strip() else {}
        new_sid = res.get("session_id")
        if proc.returncode != 0 or not new_sid or new_sid == old_sid:
            raise RuntimeError(f"fork/compact failed (rc={proc.returncode})")
    except Exception as e:                                   # noqa: BLE001
        state(slug, nid)["last_error"] = f"compaction split failed: {e}"
        return
    with store.DOC_LOCK:
        org = store.load_org(slug)
        pred = org.compact_split(nid, new_sid)
        store.save_org(org)
    st = state(slug, nid)
    st["occupancy"] = None
    notify(slug, nid, "compacted")
    notify(slug, pred, "created")


def send_message(slug: str, nid: str, text: str) -> dict:
    """Queue-or-run. Returns immediately; turn runs on a worker thread. Attached
    nodes (№17: the user has them open in a terminal) only queue — one driver at
    a time is non-negotiable."""
    st = state(slug, nid)
    with _state_lock:
        if st.get("attached"):
            st["queue"].append(text)
            return {"accepted": True, "queued": len(st["queue"]), "attached": True}
        if st["busy"]:
            st["queue"].append(text)
            return {"accepted": True, "queued": len(st["queue"])}
        st["busy"] = True
    threading.Thread(target=_run_turn, args=(slug, nid, text), daemon=True).start()
    return {"accepted": True, "queued": 0}


def set_attached(slug: str, nid: str, attached: bool) -> dict:
    """№17 managed↔attached handoff: while attached, the orchestrator releases the
    session — mail queues, turns do not run. Releasing drains the queue."""
    st = state(slug, nid)
    kick = None
    with _state_lock:
        st["attached"] = attached
        if not attached and st["queue"] and not st["busy"]:
            st["busy"] = True
            kick = st["queue"].pop(0)
    if kick is not None:
        threading.Thread(target=_run_turn, args=(slug, nid, kick), daemon=True).start()
    return {"attached": attached, "queued": len(st["queue"])}


def forget(slug: str, nids) -> None:
    """After a user delete: drop runtime state and remove org-owned scratch dirs.
    Lineage ids share their base's scratch, so only base ids delete directories;
    session transcripts under ~/.claude are deliberately left alone."""
    import shutil
    nids = set(nids)
    with _state_lock:
        for k in list(_state):
            if k[0] == slug and k[1] in nids:
                _state.pop(k, None)
    for nid in {n for n in nids if "@" not in n}:
        shutil.rmtree(os.path.join(store.scratch_root(slug), nid), ignore_errors=True)


def reconcile(slug: str) -> list[str]:
    """№31 eager pass at startup: any ledger-live node that has demonstrably run
    before (cost > 0) but whose transcript is gone cannot resume — say so now,
    not on the next message."""
    marked = []
    with store.DOC_LOCK:
        org = store.load_org(slug)
        for nid, n in org.nodes.items():
            if (n["state"] == "live" and float(n.get("cost_usd") or 0.0) > 0
                    and not n.get("bearer_state")
                    and transcript_path(n["session_id"]) is None):
                org.mark_unrecoverable(nid, "transcript missing at startup (№31)")
                marked.append(nid)
        if marked:
            store.save_org(org)
    return marked


# ---------------------------------------------------------------------- chat
def read_chat(org: Org, nid: str) -> dict:
    """Parse the node's transcript into renderable messages + context occupancy."""
    n = org.node(nid)
    st = state(org.d["slug"], nid)
    out = {"busy": st["busy"], "queued": len(st["queue"]),
           "last_error": st["last_error"], "occupancy": None, "messages": []}
    tpath = transcript_path(n["session_id"])
    if not tpath:
        return out
    msgs = []
    occupancy = None
    for line in open(tpath, encoding="utf-8", errors="replace"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("isSidechain") or rec.get("isMeta"):
            continue
        t = rec.get("type")
        if t == "system" and rec.get("subtype") == "compact_boundary":
            msgs.append({"role": "system", "text": "— context compacted —",
                         "ts": rec.get("timestamp")})
            continue
        if t not in ("user", "assistant"):
            continue
        m = rec.get("message", {})
        content = m.get("content", "")
        texts, tools = [], []
        if isinstance(content, str):
            texts.append(content)
        else:
            for block in content:
                bt = block.get("type")
                if bt == "text" and block.get("text", "").strip():
                    texts.append(block["text"])
                elif bt == "tool_use":
                    tools.append(block.get("name", "tool"))
                elif bt == "tool_result":
                    tools.append(None)   # marker: this user record is tool plumbing
        if t == "assistant":
            u = m.get("usage") or {}
            occ = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                   + u.get("cache_creation_input_tokens", 0))
            if occ and m.get("model") != "<synthetic>":
                occupancy = occ            # №24: LAST non-synthetic wins
        if t == "user" and tools and not any(texts):
            continue                        # pure tool_result plumbing — skip
        if not texts and not tools:
            continue
        msgs.append({
            "role": t,
            "text": "\n\n".join(texts),
            "tools": [x for x in tools if x],
            "ts": rec.get("timestamp"),
        })
    out["messages"] = msgs
    out["occupancy"] = occupancy
    if n.get("bearer_state") == "preserving":
        for ex in n.get("oracle_exchanges", []):
            out["messages"].append({"role": "user", "text": ex["q"], "tools": [],
                                    "ts": ex["at"], "oracle": True})
            out["messages"].append({"role": "assistant", "text": ex["a"], "tools": [],
                                    "ts": ex["at"], "oracle": True})
    return out
