"""FastAPI layer — the UI's backend and (later) the supervisor's host process.

Run:  python -m orgtree.api          (serves API + built frontend on one port)
Dev:  uvicorn orgtree.api:app --reload --port 7360   (vite dev server proxies /api)

v0.1 scope: org CRUD, tree view, the ledger ops, an event tail, and a WebSocket that
pings "changed" after every successful op so the UI refreshes. Session spawning is v0.2.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store, supervisor
from .ledger import LedgerError, USER, VIS_LEVELS, norm_dirs, norm_tools

app = FastAPI(title="orgtree", version="1.0.0")


mail_notify = lambda slug, frm, to: None   # wired at startup (thread-safe fanout)


@app.on_event("startup")
async def _wire_notify():
    global mail_notify
    loop = asyncio.get_running_loop()
    try:
        # hook processes get a sanitized env — the steering hook finds us here
        open(os.path.join(store.DATA_ROOT, ".port"), "w",
             encoding="utf-8").write(str(PORT))
    except OSError:
        pass

    def notify(slug: str, node: str, event: str):
        asyncio.run_coroutine_threadsafe(hub.node_event(slug, node, event), loop)

    def _mail(slug: str, frm: str, to: str):
        # pure animation signal for the UI (spark on the wire) — no state rides on it
        asyncio.run_coroutine_threadsafe(
            hub._send(slug, {"type": "mail", "org": slug, "from": frm, "to": to}),
            loop)

    mail_notify = _mail

    def stream(slug: str, node: str, payload: dict):
        asyncio.run_coroutine_threadsafe(
            hub._send(slug, {"type": "node_stream", "org": slug, "node": node,
                             **payload}), loop)

    supervisor.notify = notify
    supervisor.stream = stream
    for o in store.list_orgs():                   # №31 eager reconciliation
        marked = supervisor.reconcile(o["slug"])
        if marked:
            print(f"[orgtree] {o['slug']}: marked unrecoverable at startup: {marked}")

PORT = int(os.environ.get("ORGTREE_PORT", "7360"))
FRONTEND_DIST = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "frontend", "dist"))


# ------------------------------------------------------------------ websocket
class Hub:
    """Per-org 'something changed' fanout. Payloads are deliberately dumb — the UI
    refetches the tree; the ledger stays the single source of truth."""

    def __init__(self):
        self.rooms: dict[str, set[WebSocket]] = {}

    async def join(self, slug: str, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(slug, set()).add(ws)

    def leave(self, slug: str, ws: WebSocket):
        self.rooms.get(slug, set()).discard(ws)

    async def _send(self, slug: str, payload: dict):
        dead = []
        for ws in self.rooms.get(slug, set()):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(slug, ws)

    async def changed(self, slug: str):
        await self._send(slug, {"type": "changed", "org": slug})

    async def node_event(self, slug: str, node: str, event: str):
        await self._send(slug, {"type": "node_event", "org": slug,
                                "node": node, "event": event})


hub = Hub()


# ---------------------------------------------------------------------- orgs
class OrgCreate(BaseModel):
    name: str
    dirs: list[str] = []
    permission_mode: str = "acceptEdits"


@app.get("/api/orgs")
def orgs_list():
    return store.list_orgs()


@app.post("/api/orgs")
def orgs_create(body: OrgCreate):
    try:
        org = store.create_org(body.name, body.dirs, body.permission_mode)
    except LedgerError as e:
        raise HTTPException(400, str(e))
    return {"slug": org.d["slug"]}


@app.delete("/api/orgs/{slug}")
def orgs_delete(slug: str):
    try:
        store.delete_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.get("/api/orgs/{slug}")
def org_tree(slug: str):
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    tree = org.tree()

    def annotate(node: dict):
        st = supervisor.state(slug, node["id"])
        node["busy"] = st["busy"]
        node["queued"] = len(st["queue"])
        node["last_error"] = st["last_error"]
        node["last_status"] = st.get("last_status")
        if st.get("occupancy"):       # runtime is fresher than the persisted copy
            node["occupancy"] = st["occupancy"]
        if st.get("context_window"):
            node["context_window"] = st["context_window"]
        node["attached"] = bool(st.get("attached"))
        for c in node["children"]:
            annotate(c)

    for r in tree["roots"]:
        annotate(r)
    return tree


class Settings(BaseModel):
    org_dirs: list | None = None            # external folders [{path, mode}] (ws excluded)
    max_top_grant: int | None = None
    default_top_grant: int | None = None    # pre-filled grant for top-level hires
    clear_fable_lock: bool = False
    fable_limit_policy: str | None = None   # halt | opus | dissolve
    default_tools: dict | None = None       # {bash, web, edit, subagents, mcp: []|["*"]}
    default_visibility: str | None = None   # self|team|subtree|full


@app.post("/api/orgs/{slug}/settings")
async def org_settings(slug: str, body: Settings):
    """Org-level knobs. Folder holdings (org_dirs) are edited from the eye's
    gear panel: the workspace is permanent; additions apply to FUTURE hires;
    removals revoke everywhere; rw→ro downgrades propagate to every grant."""
    with store.DOC_LOCK:
        return await _org_settings_locked(slug, body)


async def _org_settings_locked(slug: str, body: Settings):
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    ws = org.d.get("workspace")
    warnings = []
    if body.org_dirs is not None:
        # org folder holdings live on the eye's gear (user ruling). Removals
        # revoke everywhere; an rw→ro downgrade propagates to every node's
        # grant (upgrades don't auto-propagate — grant per node deliberately).
        new = [{**d, "path": os.path.normpath(d["path"])}
               for d in norm_dirs(body.org_dirs)
               if os.path.normpath(d["path"]) != ws]
        old = {d["path"]: d["mode"] for d in org.d["dirs"] if d["path"] != ws}
        newmap = {d["path"]: d["mode"] for d in new}
        for gone in [p for p in old if p not in newmap]:
            for root in org.children(None, live_only=False):
                r = org.revoke_dir(USER, root, gone)
                for nid in r["removed_from"]:
                    warnings.append(f"revoked {gone} from {nid}")
        for p, mode in newmap.items():
            if mode == "ro" and old.get(p) == "rw":
                for nid, n in org.nodes.items():
                    for d in n["scope"]["add_dirs"]:
                        if d["path"] == p and d["mode"] == "rw":
                            d["mode"] = "ro"
                            warnings.append(f"downgraded {p} to read-only for {nid}")
        org.d["dirs"] = ([{"path": ws, "mode": "rw"}] if ws else []) + new
    if body.max_top_grant is not None and body.max_top_grant > 0:
        org.d["max_top_grant"] = int(body.max_top_grant)
    if body.default_top_grant is not None and body.default_top_grant >= 0:
        org.d["default_top_grant"] = int(body.default_top_grant)
    if body.clear_fable_lock and org.d.get("fable_lock"):
        org.clear_fable_lock()
        warnings.append("fable lock cleared — fable agents may run and be rehired again")
    if body.fable_limit_policy in ("halt", "opus", "dissolve"):
        org.d["fable_limit_policy"] = body.fable_limit_policy
    if body.default_tools is not None:
        # agent defaults: applied to unspecified hires — top level directly,
        # deeper as ∩ with the superior's capability (clamped at hire time)
        org.d["default_tools"] = norm_tools(body.default_tools)
    if body.default_visibility in VIS_LEVELS:
        org.d["default_visibility"] = body.default_visibility
    store.save_org(org)
    await hub.changed(slug)
    return {"dirs": org.d["dirs"], "warnings": warnings}


class Scope(BaseModel):
    add_dirs: list[dict] | None = None      # [{path, mode: rw|ro}]
    tools: dict | None = None               # {bash, web, edit, subagents, mcp: []}
    org_visibility: str | None = None
    charter: str | None = None              # §15: this node's role card
    team_charter: str | None = None         # §15: binds this node's whole subtree


@app.post("/api/orgs/{slug}/nodes/{nid}/scope")
async def node_scope(slug: str, nid: str, body: Scope):
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            result = org.set_scope(USER, nid, add_dirs=body.add_dirs, tools=body.tools,
                                   org_visibility=body.org_visibility,
                                   charter=body.charter,
                                   team_charter=body.team_charter)
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    await hub.changed(slug)
    return result


@app.get("/api/mcp-servers")
def mcp_servers():
    """Names of the user's globally registered MCP servers, grantable per node."""
    return {"servers": sorted(supervisor.registered_mcp_servers().keys())}


class Reorder(BaseModel):
    before: str | None = None
    after: str | None = None


@app.post("/api/orgs/{slug}/nodes/{nid}/reorder")
async def node_reorder(slug: str, nid: str, body: Reorder):
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            result = org.reorder(USER, nid, before=body.before, after=body.after)
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    await hub.changed(slug)
    return result


class Message(BaseModel):
    text: str


@app.post("/api/orgs/{slug}/nodes/{nid}/message")
def node_message(slug: str, nid: str, body: Message):
    """A user message IS mail (user ruling — the direct-message channel was
    folded into the mail system): it lands persisted in the node's mailbox
    (and in your Sent folder), then the node is driven; a busy node gets it
    mid-task via steering, never an interrupt. Talking to a non-top-level
    node notifies its whole superior chain (§7.4) and grants a user audience."""
    if not body.text.strip():
        raise HTTPException(422, "empty message")
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            if org.node(nid)["state"] != "live":
                raise LedgerError(f"{nid} is {org.node(nid)['state']} — rehire it to talk")
            org.post_mail(USER, nid, body.text)
            org.user_deep_reach(nid, body.text.strip().splitlines()[0][:80])
            store.save_org(org)
        except LedgerError as e:
            raise HTTPException(422, str(e))
    mail_notify(slug, USER, nid)
    return supervisor.send_message(
        slug, nid,
        "(orgtree) The mail above includes a message from the user, addressed "
        "to you — act on it now.")


@app.post("/api/orgs/{slug}/nodes/{nid}/steer")
async def node_steer(slug: str, nid: str):
    """Called by the PostToolUse steering hook inside a node's turn: pops ALL
    the node's pending mid-task mail — user and agent alike — for immediate
    delivery (sender attribution rides inside each message)."""
    msgs = supervisor.pop_steer(slug, nid)
    if msgs:
        for m in msgs:
            await hub._send(slug, {"type": "node_stream", "org": slug,
                                   "node": nid, "kind": "steered", "text": m[:2000]})
    return {"messages": msgs}


@app.post("/api/orgs/{slug}/nodes/{nid}/interrupt")
def node_interrupt(slug: str, nid: str):
    """Manual ⏸: stop the node's current response (the only sanctioned
    interrupt — message delivery never interrupts, user ruling)."""
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    return supervisor.interrupt_turn(slug, nid)


@app.post("/api/orgs/{slug}/resume")
async def org_resume(slug: str):
    """The ▶ button: restart every usage-limit-frozen agent at once."""
    try:
        store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    resumed = supervisor.resume_frozen(slug)
    await hub.changed(slug)
    return {"resumed": resumed}


class CreditDecision(BaseModel):
    id: str
    action: str        # approve | deny


@app.post("/api/orgs/{slug}/credit-requests")
async def credit_request_decide(slug: str, body: CreditDecision):
    """One-click approve/deny of a top-level agent's credit request."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            req = org.credit_request_action(body.id, body.action)
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    # drive the agent so it learns the verdict promptly (rides the notice)
    supervisor.send_message(
        slug, req["node"],
        "(orgtree) The user has decided on your credit request — see the "
        "notice above and proceed accordingly.")
    await hub.changed(slug)
    return req


@app.get("/api/orgs/{slug}/inbox")
def user_inbox(slug: str):
    """Same shape as a node's inbox (user ruling — the two interfaces function
    identically): unread mail + the read archive + the Sent folder (every user
    message is mail and gets recorded)."""
    try:
        d = store.load_org(slug).d
    except LedgerError as e:
        raise HTTPException(404, str(e))
    return {"pending": d.get("user_inbox", []),
            "delivered": d.get("user_mail_log", [])[-50:],
            "sent": d.get("user_outbox", [])[-50:]}


@app.post("/api/orgs/{slug}/inbox/clear")
async def user_inbox_clear(slug: str):
    """Mark-all-read: archives into the read log (mirror of a node's mail_log)
    rather than deleting."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
        except LedgerError as e:
            raise HTTPException(404, str(e))
        log = org.d.setdefault("user_mail_log", [])
        log.extend(org.d.get("user_inbox", []))
        del log[:-100]
        org.d["user_inbox"] = []
        store.save_org(org)
    await hub.changed(slug)
    return {"ok": True}


# --------------------------------------------------------- inspector + admin
@app.get("/api/orgs/{slug}/nodes/{nid}/history")
def node_history(slug: str, nid: str, last: int = 80):
    """Message history with attribution + delivered notices + ops touching the node."""
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    items = []
    for ev in org.d.get("events", []):
        det = ev.get("detail", {})
        touches = (det.get("node") == nid or det.get("to") == nid
                   or ev.get("actor") == nid or det.get("grantee") == nid
                   or det.get("from") == nid)
        if touches:
            items.append({"at": ev["at"], "kind": ev["op"], "actor": ev["actor"],
                          "detail": {k: v for k, v in det.items()
                                     if isinstance(v, (str, int, float))} })
    for n in org.d.get("notice_log", []):
        if n["node"] == nid:
            items.append({"at": n["at"], "kind": "notice", "actor": "system",
                          "detail": {"text": n["text"]}})
    items.sort(key=lambda x: x["at"])
    return {"items": items[-last:]}


@app.get("/api/orgs/{slug}/nodes/{nid}/scratch")
def node_scratch(slug: str, nid: str, path: str = ""):
    base = os.path.realpath(supervisor.scratch_dir(slug, nid))
    full = os.path.realpath(os.path.join(base, path.lstrip("/\\")))
    if not full.startswith(base):
        raise HTTPException(422, "path escapes the scratch space")
    if os.path.isdir(full):
        out = []
        for e in sorted(os.listdir(full))[:300]:
            p = os.path.join(full, e)
            out.append({"name": e, "dir": os.path.isdir(p),
                        "size": None if os.path.isdir(p) else os.path.getsize(p)})
        return {"dir": path or ".", "entries": out}
    if os.path.isfile(full):
        return {"file": path,
                "content": open(full, encoding="utf-8", errors="replace").read()[:60000]}
    raise HTTPException(404, f"no such path: {path!r}")


@app.get("/api/orgs/{slug}/orgmd")
def orgmd_get(slug: str):
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    ws = org.d.get("workspace")
    p = os.path.join(ws, "CLAUDE.md") if ws else None
    content = ""
    if p and os.path.isfile(p):
        content = open(p, encoding="utf-8", errors="replace").read()[:60000]
    return {"path": p, "content": content}


class OrgMd(BaseModel):
    content: str


@app.put("/api/orgs/{slug}/orgmd")
async def orgmd_put(slug: str, body: OrgMd):
    """org.md v1: the workspace CLAUDE.md — injected into every node that holds the
    workspace, which is every node by default."""
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    ws = org.d.get("workspace")
    if not ws:
        raise HTTPException(422, "org has no workspace")
    os.makedirs(ws, exist_ok=True)
    p = os.path.join(ws, "CLAUDE.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body.content)
    await hub.changed(slug)
    return {"path": p, "bytes": len(body.content)}


class Attach(BaseModel):
    attached: bool


@app.post("/api/orgs/{slug}/nodes/{nid}/attach")
def node_attach(slug: str, nid: str, body: Attach):
    """№17 handoff: attach releases the node to your terminal (mail queues, turns
    pause); release resumes management and drains the queue."""
    try:
        org = store.load_org(slug)
        n = org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    r = supervisor.set_attached(slug, nid, body.attached)
    r["command"] = (f"cd {supervisor.scratch_dir(slug, nid)} && "
                    f"claude --resume {n['session_id']}")
    return r


class AudienceAction(BaseModel):
    action: str            # grant | deny | revoke
    node: str              # the grantee / requester
    target: str | None = None


@app.post("/api/orgs/{slug}/audiences")
async def user_audience(slug: str, body: AudienceAction):
    """User-side audience management: grant/deny requests that reached you, and
    one-click rescind of any audience (your authority is unconditional)."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            if body.action == "grant":
                result = org.audience_grant(USER, body.node)
            elif body.action == "deny":
                result = org.audience_deny(USER, body.node, body.target or USER)
            elif body.action == "revoke":
                result = org.audience_revoke(USER, body.node)
            else:
                raise LedgerError("action must be grant|deny|revoke")
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    for t in result.pop("drive", []):
        supervisor.send_message(slug, t, "(orgtree) You have new mail above.")
    await hub.changed(slug)
    return result


@app.get("/api/orgs/{slug}/audiences")
def audiences_list(slug: str):
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    return {"audiences": org.d.get("audiences", []),
            "requests": org.d.get("audience_requests", [])}


# ------------------------------------------------------------- agent gateway
class AgentCall(BaseModel):
    org: str
    node: str
    tool: str
    args: dict = {}


@app.post("/api/agent")
async def agent_call(body: AgentCall):
    """Backend for the orgtree MCP server every node loads. The calling NODE is the
    actor — the ledger enforces authority, budgets, capability subsets, addressing,
    and the no-defaults hire rule."""
    a = body.args
    drive: list[str] = []      # nodes whose turn should run after we release the lock
    with store.DOC_LOCK:
        try:
            org = store.load_org(body.org)
            org.node(body.node)
            if body.tool == "orgtree_message":
                result = org.post_mail(body.node, a.get("to", ""), a.get("body", ""),
                                       a.get("kind", "message"))
                delivered = result.get("delivered")
                if delivered is not None:
                    mail_notify(body.org, body.node,
                                USER if delivered == "user_inbox" else delivered)
                if delivered not in (None, "user_inbox"):
                    drive.append(delivered)
            elif body.tool == "orgtree_request_credits":
                result = org.request_credits(body.node, a.get("new_limit"),
                                             a.get("reason"))
            elif body.tool == "orgtree_hire":
                result = org.hire(body.node, a.get("parent") or body.node,
                                  a.get("tier"), int(a.get("grant") or 0),
                                  a.get("name") or "", add_dirs=a.get("add_dirs"),
                                  tools=a.get("tools"),
                                  org_visibility=a.get("org_visibility"),
                                  purpose=a.get("purpose"))
            elif body.tool == "orgtree_retool":
                result = org.set_scope(body.node, a.get("node", ""),
                                       add_dirs=a.get("add_dirs"),
                                       tools=a.get("tools"),
                                       org_visibility=a.get("org_visibility"),
                                       charter=a.get("charter"),
                                       team_charter=a.get("team_charter"))
            elif body.tool == "orgtree_retire":
                result = org.retire(body.node, a.get("node"))
            elif body.tool == "orgtree_rehire":
                result = org.rehire(body.node, a.get("node"), a.get("grant"))
            elif body.tool == "orgtree_dissolve":
                result = org.dissolve(body.node, a.get("node"))
            elif body.tool == "orgtree_reallocate":
                result = org.reallocate(body.node, a.get("node"), int(a.get("delta") or 0))
            elif body.tool == "orgtree_status":
                status = a.get("status", "working")
                summary = a.get("summary", "")
                supervisor.state(body.org, body.node)["last_status"] = {
                    "status": status, "summary": summary}
                result = {"recorded": status}
                if status in ("done", "blocked"):
                    parent = org.node(body.node)["parent"]
                    r = org.post_mail(body.node, parent if parent else USER,
                                      f"[{status.upper()}] {summary}", kind="status")
                    mail_notify(body.org, body.node, parent if parent else USER)
                    if parent:
                        drive.append(parent)
                    result["reported_to"] = parent or "user inbox"
                    result["warnings"] = r.get("warnings", [])
            elif body.tool == "orgtree_chart":
                result = {"chart": supervisor.identity_prompt(org, body.node)}
            elif body.tool == "orgtree_read_transcript":
                target = a.get("node", "")
                if target != body.node and not org.is_ancestor(body.node, target):
                    raise LedgerError("read access is strictly DOWNWARD (§7.6) — you "
                                      "may read yourself and your descendants only")
                chat = supervisor.read_chat(org, target)
                last = max(1, min(int(a.get("last") or 30), 80))
                msgs = chat["messages"][-last:]
                result = {"node": target, "busy": chat["busy"],
                          "occupancy": chat["occupancy"],
                          "messages": [{"role": m["role"],
                                        "text": (m.get("text") or "")[:1200],
                                        "tools": m.get("tools", [])} for m in msgs]}
            elif body.tool == "orgtree_read_scratch":
                target = a.get("node", "")
                if target != body.node and not org.is_ancestor(body.node, target):
                    raise LedgerError("read access is strictly DOWNWARD (§7.6)")
                base = os.path.realpath(supervisor.scratch_dir(body.org, target))
                rel = (a.get("path") or "").strip().lstrip("/\\")
                full = os.path.realpath(os.path.join(base, rel))
                if not full.startswith(base):
                    raise LedgerError("path escapes the scratch space")
                if os.path.isdir(full):
                    entries = sorted(os.listdir(full))[:200]
                    result = {"dir": rel or ".", "entries": entries}
                elif os.path.isfile(full):
                    result = {"file": rel,
                              "content": open(full, encoding="utf-8",
                                              errors="replace").read()[:20000]}
                else:
                    result = {"error": f"no such path in {target}'s scratch: {rel!r}"}
            elif body.tool == "orgtree_audience":
                action = a.get("action", "")
                if action == "request":
                    result = org.request_audience(body.node, a.get("target", ""),
                                                  a.get("reason", ""))
                elif action == "forward":
                    result = org.audience_forward(body.node, a.get("from", ""),
                                                  a.get("target", ""))
                elif action == "grant":
                    result = org.audience_grant(body.node, a.get("from", ""))
                elif action == "deny":
                    result = org.audience_deny(body.node, a.get("from", ""),
                                               a.get("target", "") or body.node)
                elif action == "revoke":
                    result = org.audience_revoke(body.node, a.get("grantee", ""))
                else:
                    raise LedgerError("action must be request|forward|grant|deny|revoke")
                drive.extend(result.pop("drive", []))
            else:
                raise LedgerError(f"unknown orgtree tool {body.tool!r}")
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    for target in drive:
        supervisor.send_message(
            body.org, target,
            "(orgtree) You have new mail above — handle it as appropriate, and use "
            "orgtree_status when your own task state changes.")
    await hub.changed(body.org)
    return result


@app.get("/api/orgs/{slug}/nodes/{nid}/chat")
def node_chat(slug: str, nid: str):
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    out = supervisor.read_chat(org, nid)
    out["mail_pending"] = len((org.d.get("mail") or {}).get(nid, []))
    return out


@app.get("/api/orgs/{slug}/nodes/{nid}/inbox")
def node_inbox(slug: str, nid: str):
    """The node's OWN mailbox (user ruling: separate from the events/history
    view): mail still waiting for its next turn, plus recently delivered mail
    with full bodies (the event log keeps only a gist)."""
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    waiting = (org.d.get("mail") or {}).get(nid, [])
    keys = {(m["at"], m["from"], m["body"]) for m in waiting}
    delivered = [m for m in (org.d.get("mail_log") or {}).get(nid, [])
                 if (m["at"], m["from"], m["body"]) not in keys]
    # the node's Sent folder, mirrored from the recipients' archives
    sent = []
    for to, lst in (org.d.get("mail_log") or {}).items():
        sent += [{**m, "to": to} for m in lst if m["from"] == nid]
    for m in org.d.get("user_inbox", []) + org.d.get("user_mail_log", []):
        if m["from"] == nid:
            sent.append({**m, "to": USER})
    sent.sort(key=lambda m: m["at"])
    return {"pending": waiting, "delivered": delivered[-50:], "sent": sent[-50:]}


@app.get("/api/orgs/{slug}/events")
def org_events(slug: str, since: int = 0):
    try:
        events = store.load_org(slug).d["events"]
    except LedgerError as e:
        raise HTTPException(404, str(e))
    return {"total": len(events), "events": events[since:]}


# ----------------------------------------------------------------------- ops
class Op(BaseModel):
    op: str                       # hire|retire|rehire|dissolve|reallocate|promote|demote|revoke_dir
    actor: str = USER
    node: str | None = None       # target node (all but hire)
    parent: str | None = None     # hire target parent (None = top level)
    tier: str | None = None       # hire
    grant: int | None = None      # hire / rehire / reallocate delta via `delta`
    name: str | None = None       # hire
    add_dirs: list | None = None  # hire — [{path, mode}] or bare paths
    tools: dict | None = None     # hire — {bash, web, edit, subagents, mcp: []}
    org_visibility: str | None = None
    purpose: str | None = None
    delta: int | None = None      # reallocate
    new_parent: str | None = None  # promote / demote
    dir: str | None = None        # revoke_dir


@app.post("/api/orgs/{slug}/ops")
async def org_op(slug: str, body: Op):
    with store.DOC_LOCK:
        return await _org_op_locked(slug, body)


async def _org_op_locked(slug: str, body: Op):
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    try:
        if body.op == "hire":
            if body.tier is None or body.name is None:
                raise LedgerError("hire needs tier and name")
            result = org.hire(body.actor, body.parent, body.tier,
                              body.grant or 0, body.name, body.add_dirs,
                              tools=body.tools, org_visibility=body.org_visibility,
                              purpose=body.purpose)
        elif body.op == "retire":
            result = org.retire(body.actor, body.node)
        elif body.op == "rehire":
            result = org.rehire(body.actor, body.node, body.grant, tier=body.tier)
        elif body.op == "dissolve":
            result = org.dissolve(body.actor, body.node)
        elif body.op == "delete":
            result = org.delete(body.actor, body.node)
            supervisor.forget(slug, result["deleted"])
        elif body.op == "reallocate":
            if body.delta is None:
                raise LedgerError("reallocate needs delta")
            result = org.reallocate(body.actor, body.node, body.delta)
        elif body.op == "promote":
            result = org.promote(body.actor, body.node, body.new_parent)
        elif body.op == "demote":
            if body.new_parent is None:
                raise LedgerError("demote needs new_parent")
            result = org.demote(body.actor, body.node, body.new_parent)
        elif body.op == "revoke_dir":
            if body.dir is None:
                raise LedgerError("revoke_dir needs dir")
            result = org.revoke_dir(body.actor, body.node, body.dir)
        else:
            raise LedgerError(f"unknown op {body.op!r}")
    except LedgerError as e:
        raise HTTPException(422, str(e))
    store.save_org(org)
    await hub.changed(slug)
    return result


@app.websocket("/api/orgs/{slug}/ws")
async def org_ws(ws: WebSocket, slug: str):
    await hub.join(slug, ws)
    try:
        while True:
            await ws.receive_text()   # client pings keep it alive; content ignored
    except WebSocketDisconnect:
        hub.leave(slug, ws)


# ------------------------------------------------------------------- static
if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")),
              name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        full = os.path.normpath(os.path.join(FRONTEND_DIST, path))
        if path and full.startswith(FRONTEND_DIST) and os.path.isfile(full):
            return FileResponse(full)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
