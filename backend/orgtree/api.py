"""FastAPI layer — the UI's backend and (later) the supervisor's host process.

Run:  python -m orgtree.api          (serves API + built frontend on one port)
Dev:  uvicorn orgtree.api:app --reload --port 7360   (vite dev server proxies /api)

v0.1 scope: org CRUD, tree view, the ledger ops, an event tail, and a WebSocket that
pings "changed" after every successful op so the UI refreshes. Session spawning is v0.2.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import sandbox, store, subproxy, supervisor
from .ledger import LedgerError, USER, VIS_LEVELS, norm_dirs, norm_tools

app = FastAPI(title="orgtree", version="1.0.0")


# ---- kiosk v2 (user vision): preauthenticated public URLs. Each kiosk-enabled
# org carries a secret token; the PUBLIC listener serves nothing but
# /k/<token>/… — the token IS the authentication and maps to exactly one org.
# The admin app binds 127.0.0.1 only, so root access never leaves this machine.
# The gate is SERVER-SIDE — hiding UI buttons is not enforcement.
_TOKEN_RE = re.compile(r"^/k/([A-Za-z0-9_-]{8,64})(/.*)?$")
_PUBLIC_STATIC = ("/assets/", "/favicon", "/vite.svg")   # index.html's absolute refs
_token_cache: dict = {"at": 0.0, "map": {}}


def _kiosk_token_map() -> dict:
    """token → slug for every kiosk-enabled org. Rebuilt on a short TTL and
    invalidated on any kiosk-config write, so rotation revokes instantly."""
    if time.time() - _token_cache["at"] > 5:
        m = {}
        for o in store.list_orgs():
            try:
                k = store.load_org(o["slug"]).d.get("kiosk") or {}
            except LedgerError:
                continue
            if k.get("enabled") and k.get("token"):
                m[k["token"]] = o["slug"]
        _token_cache.update(at=time.time(), map=m)
    return _token_cache["map"]


def _public_denied(method: str, rest: str, slug: str) -> tuple[int, str] | None:
    """The public restriction matrix, applied to the post-token path. Config
    surfaces are admin-only; all access is scoped to the token's own org."""
    if not rest.startswith("/api"):
        return None                              # the SPA itself
    if rest == "/api/orgs" and method == "GET":
        return None                              # handler filters to this org
    frozen_config = (
        (method == "POST" and rest == "/api/orgs")           # create org
        or (method == "DELETE" and rest.startswith("/api/orgs/"))  # delete org
        or rest.endswith("/settings")                        # org settings
        or rest.endswith("/scope")                           # per-agent rights
        or rest.endswith("/kiosk")                           # kiosk caps/token
        or rest == "/api/fs"                                 # filesystem browse
        or (method == "PUT" and rest.endswith("/orgmd"))     # org.md edits
        or rest.endswith("/attach")                          # terminal handoff
        or rest == "/api/agent"                              # node MCP gateway
        or rest == "/api/mcp-servers"
    )
    if frozen_config:
        return 403, "kiosk: configuration is managed from the admin side"
    parts = rest.split("/")
    if not (len(parts) > 3 and parts[2] == "orgs" and parts[3] == slug):
        return 404, "not found"                  # other orgs, other surfaces
    return None


class PublicGateway:
    """ASGI wrapper served ONLY on the public port: resolves /k/<token>,
    rewrites the path so the normal routes handle it, stamps the request state
    with the org slug, and 404s everything else — no org list, no discovery."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            # the admin server owns the app's lifespan — running FastAPI
            # startup twice would double-wire notify + reconcile
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] not in ("http", "websocket"):
            return await self.inner(scope, receive, send)
        path = scope.get("path", "")
        if scope["type"] == "http" and path.startswith(_PUBLIC_STATIC):
            return await self.inner(scope, receive, send)
        m = _TOKEN_RE.match(path)
        slug = _kiosk_token_map().get(m.group(1)) if m else None
        if not slug:
            return await self._reject(scope, send, 404, "not found")
        rest = m.group(2) or "/"
        deny = _public_denied(scope.get("method", "GET"), rest, slug)
        if deny:
            return await self._reject(scope, send, deny[0], deny[1])
        scope = dict(scope)
        scope["path"] = rest
        scope["raw_path"] = rest.encode()
        scope["state"] = {**(scope.get("state") or {}), "public_slug": slug}
        await self.inner(scope, receive, send)

    async def _reject(self, scope, send, code: int, detail: str):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4000 + code})
            return
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": code,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _public_slug(request) -> str | None:
    return getattr(request.state, "public_slug", None)


# ---- the sandbox bridge: the ONE door out of a kiosk container. Serves only
# the agent gateway + the steering fetch, gated by the org's sandbox secret
# (which exists nowhere but inside that org's container and its org doc).
_bridge_cache: dict = {"at": 0.0, "map": {}}
_STEER_RE = re.compile(r"^/api/orgs/([a-z0-9@-]+)/nodes/[^/]+/steer$")


def _bridge_secret_map() -> dict:
    if time.time() - _bridge_cache["at"] > 5:
        m = {}
        for o in store.list_orgs():
            try:
                d = store.load_org(o["slug"]).d
            except LedgerError:
                continue
            # kiosk sandboxes and normal-org sandboxes alike (user ruling)
            for s in ((d.get("kiosk") or {}).get("sandbox_secret"),
                      (d.get("sandbox") or {}).get("secret")):
                if s:
                    m[s] = o["slug"]
        _bridge_cache.update(at=time.time(), map=m)
    return _bridge_cache["map"]


class BridgeGateway:
    """ASGI wrapper served ONLY on the bridge port (containers reach it via
    host.docker.internal): everything except the two sanctioned paths is a
    bare 403, and the secret pins the caller to its own org."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:                      # the admin server owns app lifespan
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            body = json.dumps({"detail": "forbidden"}).encode()
            await send({"type": "websocket.close", "code": 4403})
            return
        secret = ""
        for hk, hv in scope.get("headers") or []:
            if hk == b"x-orgtree-bridge":
                secret = hv.decode("latin1")
        path, method = scope.get("path", ""), scope.get("method", "GET")
        # proxied-subscription traffic carries the secret IN THE PATH — the
        # CLI can set a base URL but not custom headers we control
        rewritten = None
        pm = re.match(r"^/anthropic/([a-f0-9]{32})(/.*)$", path)
        if pm:
            secret = pm.group(1)
            rewritten = "/anthropic" + pm.group(2)
        slug = _bridge_secret_map().get(secret) if secret else None
        m = _STEER_RE.match(path)
        allowed = slug and (
            rewritten is not None
            or (method == "POST"
                and (path == "/api/agent" or (m and m.group(1) == slug))))
        if not allowed:
            body = json.dumps({"detail": "forbidden"}).encode()
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        scope = dict(scope)
        if rewritten is not None:
            scope["path"] = rewritten
            scope["raw_path"] = rewritten.encode()
        scope["state"] = {**(scope.get("state") or {}), "bridge_slug": slug}
        await self.inner(scope, receive, send)


_LAN_IP: str | None = None
_origin_cache = {"at": 0.0, "val": ""}


def _public_origin() -> str:
    """ORGTREE_PUBLIC_ORIGIN wins; otherwise the live tunnel hostname that
    expose.ps1 drops into <data>/.public_origin (TryCloudflare quick-tunnel
    URLs change per run, so this is re-read on a short TTL)."""
    if PUBLIC_ORIGIN:
        return PUBLIC_ORIGIN
    if time.time() - _origin_cache["at"] > 5:
        _origin_cache["at"] = time.time()
        try:
            _origin_cache["val"] = open(
                os.path.join(store.DATA_ROOT, ".public_origin"),
                encoding="utf-8").read().strip()
        except OSError:
            _origin_cache["val"] = ""
    return _origin_cache["val"]


def _share_url(token: str | None) -> str | None:
    """The preauthenticated URL for a kiosk token: explicit origin, else the
    running tunnel's hostname, else best-guess this machine's LAN address."""
    global _LAN_IP
    if not token or not PUBLIC_PORT:
        return None
    origin = _public_origin()
    if origin:
        return f"{origin.rstrip('/')}/k/{token}"
    if _LAN_IP is None:
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            _LAN_IP = s.getsockname()[0]
            s.close()
        except OSError:
            _LAN_IP = "127.0.0.1"
    return f"http://{_LAN_IP}:{PUBLIC_PORT}/k/{token}"


def _kiosk_cap_check(org):
    """Kiosk credit cap: NO operation may push total top-level holdings past
    the cap — covers hires, §4.6 cascades, rehires, reallocations and
    credit-request approvals in one invariant (checked before save). Applies
    to admin actions too: one invariant, and the admin can raise the cap."""
    k = supervisor.kiosk_cfg(org)
    if k and int(k.get("credits") or 0) > 0:
        held = org.audit()["top_level_holds"]
        if held > int(k["credits"]):
            raise LedgerError(
                f"kiosk credit cap: the org may hold at most "
                f"{int(k['credits'])} credits (this would make it {held:g})")


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
    supervisor.start_auto_resume_loop()
    # chatq external bridge (user vision): every org is an addressable chatq
    # peer — external Claude Code sessions message it like any other chat
    for o in store.list_orgs():
        supervisor.chatq_register_org(o["slug"])
    supervisor.start_chatq_bridge()
    # one-time migration of the retired v1 env-var kiosk mode into the org doc
    legacy = os.environ.get("ORGTREE_KIOSK")
    if legacy:
        try:
            with store.DOC_LOCK:
                org = store.load_org(legacy)
                if not org.d.get("kiosk"):
                    org.d["kiosk"] = {
                        "enabled": True, "token": secrets.token_hex(16),
                        "credits": int(os.environ.get("ORGTREE_KIOSK_CREDITS", "0") or 0),
                        "spend_limit": float(os.environ.get("ORGTREE_KIOSK_SPEND_LIMIT", "0") or 0),
                        "storage_limit_mb": 0,
                    }
                    store.save_org(org)
            print(f"[orgtree] ORGTREE_KIOSK is retired — {legacy!r} is now a kiosk "
                  f"org (secret URL on the admin dashboard); set "
                  f"ORGTREE_PUBLIC_PORT to expose it")
        except LedgerError:
            print(f"[orgtree] ORGTREE_KIOSK={legacy!r}: no such org — ignored")
    for o in store.list_orgs():                   # №31 eager reconciliation
        marked = supervisor.reconcile(o["slug"])
        if marked:
            print(f"[orgtree] {o['slug']}: marked unrecoverable at startup: {marked}")

PORT = int(os.environ.get("ORGTREE_PORT", "7360"))
PUBLIC_PORT = int(os.environ.get("ORGTREE_PUBLIC_PORT", "0") or 0)
PUBLIC_ORIGIN = (os.environ.get("ORGTREE_PUBLIC_ORIGIN") or "").strip()
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
class KioskSpec(BaseModel):
    credits: int = 40                 # top-level holdings cap
    spend_limit: float = 5.0          # USD hard limit
    storage_limit_mb: int = 500       # workspace-dir cap
    sandbox: bool = True              # run agent turns in a Docker container
    # auth is NOT configurable (user ruling): every sandbox uses the proxied
    # subscription — the host attaches the token, the sandbox never sees it


class OrgCreate(BaseModel):
    name: str
    dirs: list[str] = []
    permission_mode: str = "acceptEdits"
    kiosk: KioskSpec | None = None    # present = the org is BORN a kiosk
    sandbox: bool = False             # normal orgs may sandbox too (user ruling)


@app.get("/api/orgs")
def orgs_list(request: Request):
    orgs = store.list_orgs()
    pub = _public_slug(request)
    if pub:
        # public visitors see exactly their token's org — nothing to discover
        return [{**o, "kiosk": True} for o in orgs if o["slug"] == pub]
    # admin: attach the kiosk dashboard summary (incl. the secret token —
    # this listener is loopback-only)
    out = []
    for o in orgs:
        try:
            org = store.load_org(o["slug"])
        except LedgerError:
            out.append(o)
            continue
        row = {**o, "cost_usd_total": round(
            sum(float(v.get("cost_usd") or 0.0) for v in org.nodes.values()), 4)}
        k = org.d.get("kiosk")
        if k:
            row["kiosk_cfg"] = {
                "enabled": bool(k.get("enabled")),
                "token": k.get("token"),
                "credits": int(k.get("credits") or 0),
                "spend_limit": float(k.get("spend_limit") or 0),
                "storage_limit_mb": int(k.get("storage_limit_mb") or 0),
                "spend_frozen": bool(org.d.get("spend_frozen")),
                "storage_blocked": bool(org.d.get("storage_blocked")),
                "sandbox": bool(k.get("sandbox")),
                "held": org.audit()["top_level_holds"],
                "storage_mb": round(
                    supervisor.workspace_usage_bytes(org, 15) / 1048576, 2),
                "share_url": _share_url(k.get("token")),
            }
        out.append(row)
    return out


@app.post("/api/orgs")
def orgs_create(body: OrgCreate):
    try:
        org = store.create_org(body.name, body.dirs, body.permission_mode)
    except LedgerError as e:
        raise HTTPException(400, str(e))
    supervisor.chatq_register_org(org.d["slug"])
    if body.kiosk is not None:
        # kiosk orgs are a DISTINCT TYPE, born as kiosks with their limits
        # defined at creation (user ruling) — never converted from a normal
        # org. Token + sandbox secret are minted with the org.
        with store.DOC_LOCK:
            o = store.load_org(org.d["slug"])
            o.d["kiosk"] = {
                "enabled": True,
                "token": secrets.token_hex(16),
                "credits": max(0, int(body.kiosk.credits)),
                "spend_limit": max(0.0, float(body.kiosk.spend_limit)),
                "storage_limit_mb": max(0, int(body.kiosk.storage_limit_mb)),
                "sandbox": bool(body.kiosk.sandbox),
                "sandbox_secret": secrets.token_hex(16),
            }
            store.save_org(o)
            if o.d["kiosk"]["sandbox"]:
                sandbox.warm(o)        # prebuild image+container in background
        _token_cache["at"] = 0.0
        _bridge_cache["at"] = 0.0
    elif body.sandbox:
        # a sandboxed NORMAL org (user ruling): same container isolation,
        # no kiosk limits or public URL
        with store.DOC_LOCK:
            o = store.load_org(org.d["slug"])
            o.d["sandbox"] = {"enabled": True, "secret": secrets.token_hex(16)}
            store.save_org(o)
            sandbox.warm(o)
        _bridge_cache["at"] = 0.0
    return {"slug": org.d["slug"]}


@app.delete("/api/orgs/{slug}")
def orgs_delete(slug: str):
    try:
        store.delete_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    sandbox.remove(slug)            # container down; files stay (like scratch)
    supervisor.chatq_deregister_org(slug)
    return {"ok": True}


@app.get("/api/orgs/{slug}")
def org_tree(slug: str, request: Request):
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
        if st.get("occupancy"):       # runtime is fresher than the persisted copy
            node["occupancy"] = st["occupancy"]
        if st.get("context_window"):
            node["context_window"] = st["context_window"]
        node["attached"] = bool(st.get("attached"))
        for c in node["children"]:
            annotate(c)

    for r in tree["roots"]:
        annotate(r)
    k = supervisor.kiosk_cfg(org)
    if k:
        tree["kiosk"] = {
            "credits": int(k.get("credits") or 0) or None,
            "spend_limit": float(k.get("spend_limit") or 0) or None,
            "storage_limit_mb": int(k.get("storage_limit_mb") or 0) or None,
            "spend_frozen": bool(tree.get("spend_frozen")),
            "storage_blocked": bool(tree.get("storage_blocked")),
        }
        if k.get("storage_limit_mb"):
            tree["kiosk"]["storage_mb"] = round(
                supervisor.workspace_usage_bytes(org, 15) / 1048576, 2)
    if _public_slug(request):
        # tells the UI to lock itself down; the SERVER gate is the enforcement
        tree["public"] = True
    return tree


class Settings(BaseModel):
    org_dirs: list | None = None            # external folders [{path, mode}] (ws excluded)
    max_top_grant: int | None = None
    default_top_grant: int | None = None    # pre-filled grant for top-level hires
    compact_at: int | None = None           # compaction threshold in percent, 50..95
    clear_fable_lock: bool = False
    fable_limit_policy: str | None = None   # halt | opus | dissolve
    fable_filter_policy: str | None = None  # halt | opus (content-filter flags)
    default_tools: dict | None = None       # {bash, web, edit, subagents, mcp: []|["*"]}
    default_visibility: str | None = None   # self|team|subtree|full
    auto_resume: bool | None = None         # restart limit-frozen agents at reset+1min


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
    if body.compact_at is not None:
        # 50–95%; the 95% ceiling is NOT configurable (user ruling)
        org.d["compact_at"] = min(95, max(50, int(body.compact_at))) / 100.0
    if body.clear_fable_lock and org.d.get("fable_lock"):
        org.clear_fable_lock()
        warnings.append("fable lock cleared — fable agents may run and be rehired again")
    if body.fable_limit_policy in ("halt", "opus", "dissolve"):
        org.d["fable_limit_policy"] = body.fable_limit_policy
    if body.fable_filter_policy in ("halt", "opus"):
        org.d["fable_filter_policy"] = body.fable_filter_policy
    if body.default_tools is not None:
        # agent defaults: applied to unspecified hires — top level directly,
        # deeper as ∩ with the superior's capability (clamped at hire time)
        org.d["default_tools"] = norm_tools(body.default_tools)
    if body.default_visibility in VIS_LEVELS:
        org.d["default_visibility"] = body.default_visibility
    if body.auto_resume is not None:
        org.d["auto_resume"] = bool(body.auto_resume)
    store.save_org(org)
    await hub.changed(slug)
    return {"dirs": org.d["dirs"], "warnings": warnings}


class KioskCfg(BaseModel):
    enabled: bool | None = None
    credits: int | None = None            # top-level holdings cap (0 = uncapped)
    spend_limit: float | None = None      # USD hard limit (0 = unlimited)
    storage_limit_mb: int | None = None   # workspace-dir size cap (0 = unlimited)
    rotate_token: bool = False            # mint a new secret URL (revokes the old)


@app.post("/api/orgs/{slug}/kiosk")
async def org_kiosk(slug: str, body: KioskCfg):
    """Admin-only (the public gateway 403s the path): enable/disable an org as
    a kiosk, adjust its caps, rotate its secret URL. Raising a breached limit
    clears the matching hard freeze — ▶ resume then replays halted turns."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
        except LedgerError as e:
            raise HTTPException(404, str(e))
        if not org.d.get("kiosk"):
            # kiosk is a creation-time TYPE (user ruling) — no conversion
            raise HTTPException(
                422, "not a kiosk org — kiosks are created as kiosks (from "
                     "the dashboard's new-kiosk form), never converted")
        k = org.d["kiosk"]
        if body.enabled is not None:
            k["enabled"] = bool(body.enabled)
        if body.credits is not None:
            k["credits"] = max(0, int(body.credits))
        if body.spend_limit is not None:
            k["spend_limit"] = max(0.0, float(body.spend_limit))
        if body.storage_limit_mb is not None:
            k["storage_limit_mb"] = max(0, int(body.storage_limit_mb))
        if (k.get("enabled") and not k.get("token")) or body.rotate_token:
            k["token"] = secrets.token_hex(16)
        # user ruling: the cap can never go BELOW what the org already holds —
        # retire/dissolve agents first, then lower it
        if k.get("enabled") and int(k.get("credits") or 0):
            held = org.audit()["top_level_holds"]
            if int(k["credits"]) < held:
                raise HTTPException(
                    422, f"cap below current holdings: the org holds {held:g} "
                         f"credits — retire or dissolve agents first, then lower it")
        org.d["kiosk"] = k
        cleared = []
        spent = sum(float(v.get("cost_usd") or 0.0)
                    for v in org.nodes.values())
        lim = float(k.get("spend_limit") or 0)
        over = k.get("enabled") and lim and spent >= lim
        if org.d.get("spend_frozen") and not over:
            supervisor.clear_hard_freeze(org, "spend")
            cleared.append("spend")
        store.save_org(org)
        need_freeze = over and not org.d.get("spend_frozen")
    # limits apply in REAL TIME (user ruling), both directions: lowering the
    # spend limit below what's already spent freezes now, not at the next
    # turn's end; the storage recheck applies/lifts the write block likewise
    if need_freeze:
        supervisor.hard_freeze(slug, "spend", "kiosk spend limit reached")
    if supervisor.storage_check(slug) == "cleared":
        cleared.append("storage")
    _token_cache["at"] = 0.0             # rotation/enable takes effect now
    await hub.changed(slug)
    safe = {kk: v for kk, v in k.items()
            if kk not in ("api_key", "sandbox_secret")}
    return {"kiosk": safe, "share_url": _share_url(k.get("token")),
            "freezes_cleared": cleared}


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


@app.get("/api/fs")
def fs_list(path: str = ""):
    """Directory listing for the IN-APP folder picker (user ruling: a native
    server-side dialog only works when the browser and server share a desktop;
    this works from anywhere the UI is reachable). Directories only; an empty
    path lists the roots (drives on Windows) plus the home shortcut."""
    if not path:
        if os.name == "nt":
            import string as _string
            roots = [f"{c}:\\" for c in _string.ascii_uppercase
                     if os.path.exists(f"{c}:\\")]
        else:
            roots = ["/"]
        return {"path": "", "parent": None,
                "dirs": [{"name": r, "path": r} for r in roots],
                "home": os.path.expanduser("~")}
    p = os.path.normpath(path)
    if not os.path.isdir(p):
        raise HTTPException(404, f"not a directory: {p}")
    try:
        names = sorted((e for e in os.listdir(p)
                        if os.path.isdir(os.path.join(p, e))), key=str.lower)
    except PermissionError:
        raise HTTPException(403, f"permission denied: {p}")
    parent = os.path.dirname(p)
    if parent == p:
        parent = ""            # drive/filesystem root → back to the roots list
    return {"path": p, "parent": parent,
            "dirs": [{"name": e, "path": os.path.join(p, e)} for e in names]}


CHARTERS_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "docs", "charters"))


@app.get("/api/charters")
def charters_list():
    """Named charter presets for the manual hire form (user ruling): every
    .md in docs/charters/ is a preset. A file may open with an explanatory
    header ending at a '---' line — only what follows is the charter body."""
    out = []
    if os.path.isdir(CHARTERS_DIR):
        for f in sorted(os.listdir(CHARTERS_DIR)):
            if not f.endswith(".md"):
                continue
            try:
                text = open(os.path.join(CHARTERS_DIR, f),
                            encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            body = text.split("\n---\n", 1)[-1].strip()
            out.append({"name": f[:-3].replace("-", " "), "content": body[:6000]})
    return {"charters": out}


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
            r = org.post_mail(USER, nid, body.text)
            org.user_deep_reach(nid, body.text.strip().splitlines()[0][:80])
            store.save_org(org)
        except LedgerError as e:
            raise HTTPException(422, str(e))
    mail_notify(slug, USER, nid)
    if r.get("deferred"):
        # archived recipient (user ruling): the mail waits in its inbox and is
        # acted on at rehire — nothing to drive now
        return {"accepted": True, "deferred": True, "queued": 0}
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


@app.post("/api/orgs/{slug}/nodes/{nid}/compact")
def node_compact(slug: str, nid: str):
    """Manual compaction (user ruling: the context wheel is a BUTTON in the
    zoomed view): the same §8 split as the automatic threshold — fork, compact
    the fork into a successor, archive this self as a knowledge bearer."""
    try:
        org = store.load_org(slug)
        n = org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    if n["state"] != "live":
        raise HTTPException(422, f"{nid} is {n['state']} — rehire it first")
    if n.get("bearer_state"):
        raise HTTPException(422, "a knowledge bearer never re-compacts (§8.3)")
    if not n.get("occupancy"):
        raise HTTPException(422, "no conversation yet — nothing to compact")
    if n.get("frozen"):
        raise HTTPException(409, "frozen by a usage limit — resume it first")
    if supervisor.state(slug, nid)["busy"]:
        raise HTTPException(409, "busy — wait for the current turn to finish")

    def run():
        supervisor._compact_split(slug, nid)
        supervisor.notify(slug, nid, "compacted")

    threading.Thread(target=run, daemon=True).start()
    return {"started": True}


@app.post("/api/orgs/{slug}/dissolve-all")
async def org_dissolve_all(slug: str):
    """Dissolve EVERY agent in the org at once (context kept — rehire revives)."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            freed = nodes = 0
            for root in list(org.children(None)):
                r = org.dissolve(USER, root)
                freed += r["freed"]
                nodes += len(r["nodes"])
            store.save_org(org)
        except LedgerError as e:
            raise HTTPException(422, str(e))
    await hub.changed(slug)
    return {"freed": freed, "nodes": nodes}


@app.post("/api/orgs/{slug}/killswitch")
async def org_killswitch(slug: str):
    """⏹ STOP ALL: interrupt every active agent and clear pending queues."""
    try:
        store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    result = supervisor.interrupt_all(slug)
    await hub.changed(slug)
    return result


@app.post("/api/orgs/{slug}/resume")
async def org_resume(slug: str):
    """The ▶ button: restart every usage-limit-frozen agent at once."""
    try:
        store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    try:
        resumed = supervisor.resume_frozen(slug)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
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
            _kiosk_cap_check(org)
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


class InboxRead(BaseModel):
    ids: list[str]


@app.post("/api/orgs/{slug}/inbox/read")
async def user_inbox_read(slug: str, body: InboxRead):
    """Per-mail read: a viewed mail is marked read when the user clicks off it
    (user ruling) — it moves from unread into the read archive."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
        except LedgerError as e:
            raise HTTPException(404, str(e))
        ids = set(body.ids)
        keep, read = [], []
        for m in org.d.get("user_inbox", []):
            (read if m.get("id") in ids else keep).append(m)
        if read:
            org.d["user_inbox"] = keep
            log = org.d.setdefault("user_mail_log", [])
            log.extend(read)
            del log[:-100]
            store.save_org(org)
    await hub.changed(slug)
    return {"read": len(read)}


# ------------------------------------------------ external chats (no chatq)
# The extern MCP server (externtool.py) gives any outside Claude Code session
# a peer identity (@mcp:<id>) and three verbs against org inboxes: send, read
# what's addressed to me, and wait for a response — a full Q&A loop with an
# org, no chatq required. chatq stays relevant only when the ORG must wake an
# external chat unprompted.
_PEER_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


class ExternSend(BaseModel):
    org: str
    body: str


def _extern_peer(peer: str) -> str:
    if not _PEER_RE.fullmatch(peer):
        raise HTTPException(422, "peer id must be 1-64 chars of [A-Za-z0-9._-]")
    return f"@mcp:{peer}"


@app.post("/api/extern/{peer}/send")
def extern_send(peer: str, body: ExternSend):
    addr = _extern_peer(peer)
    if not body.body.strip():
        raise HTTPException(422, "empty message")
    with store.DOC_LOCK:
        try:
            org = store.load_org(body.org)
        except LedgerError:
            raise HTTPException(404, f"no organization named {body.org!r}")
        if org.is_kiosk:
            raise HTTPException(403, f"organization {body.org!r} is a sealed "
                                     f"kiosk — unreachable from outside")
    delivered = supervisor.deliver_org_inbox(body.org, addr, body.body)
    return {"delivered": delivered or ["(user inbox — no live agents)"]}


def _extern_scan(addr: str, org_slug: str | None, after: str | None) -> list[dict]:
    out = []
    with store.DOC_LOCK:
        for o in store.list_orgs():
            if org_slug and o["slug"] != org_slug:
                continue
            try:
                org = store.load_org(o["slug"])
            except LedgerError:
                continue
            for e in org.d.get("org_inbox", []):
                if e.get("peer") == addr and e.get("dir") == "out" \
                        and (not after or e.get("at", "") > after):
                    out.append({"org": o["slug"], "id": e["id"],
                                "at": e["at"], "body": e["body"]})
    out.sort(key=lambda x: x["at"])
    return out


@app.get("/api/extern/{peer}/messages")
def extern_messages(peer: str, org: str | None = None, after: str | None = None):
    return {"messages": _extern_scan(_extern_peer(peer), org, after)}


@app.get("/api/extern/{peer}/wait")
async def extern_wait(peer: str, org: str | None = None,
                      after: str | None = None, timeout: int = 25):
    """Long-poll: block until an org replies to this peer (or timeout)."""
    addr = _extern_peer(peer)
    deadline = time.monotonic() + min(max(timeout, 1), 55)
    while True:
        msgs = _extern_scan(addr, org, after)
        if msgs or time.monotonic() >= deadline:
            return {"messages": msgs}
        await asyncio.sleep(1.0)


@app.post("/api/orgs/{slug}/org_inbox/read")
async def org_inbox_read(slug: str):
    """The user opened the org-inbox panel: clear its unread count."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
        except LedgerError as e:
            raise HTTPException(404, str(e))
        org.org_inbox_mark_read()
        store.save_org(org)
    await hub.changed(slug)
    return {"ok": True}


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
                result = org.audience_grant(USER, body.node, body.target)
            elif body.action == "deny":
                result = org.audience_deny(USER, body.node, body.target or USER)
            elif body.action == "revoke":
                result = org.audience_revoke(USER, body.node, body.target)
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


# ---------------------------------------------- proxied-subscription upstream
# The sandbox's CLI points ANTHROPIC_BASE_URL here (secret in the path, via
# the bridge); the HOST attaches the subscription OAuth token — the sandbox
# never holds a credential (user spec). Streaming passthrough.
_hx = None


def _upstream():
    global _hx
    if _hx is None:
        import httpx
        _hx = httpx.AsyncClient(base_url="https://api.anthropic.com",
                                timeout=httpx.Timeout(600.0, connect=30.0))
    return _hx


@app.api_route("/anthropic/{path:path}",
               methods=["GET", "POST", "HEAD", "PUT", "DELETE"])
async def anthropic_proxy(path: str, request: Request):
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask
    if not getattr(request.state, "bridge_slug", None):
        raise HTTPException(403, "bridge only")
    try:
        token = await run_in_threadpool(subproxy.get_access_token)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    headers = {}
    for k, v in request.headers.items():
        if k.lower() in ("host", "x-api-key", "authorization", "content-length",
                         "connection", "accept-encoding", "x-orgtree-bridge"):
            continue
        headers[k] = v
    betas = headers.get("anthropic-beta", "")
    if "oauth-2025-04-20" not in betas:
        headers["anthropic-beta"] = (betas + "," if betas else "") + "oauth-2025-04-20"
    headers["Authorization"] = "Bearer " + token
    # identity only: we stream the body RAW — a gzip upstream response with
    # the content-encoding header stripped reads as garbage at the CLI
    headers["Accept-Encoding"] = "identity"
    body = await request.body()
    url = "/" + path + (f"?{request.url.query}" if request.url.query else "")
    req = _upstream().build_request(request.method, url,
                                    headers=headers, content=body)
    up = await _upstream().send(req, stream=True)
    resp_headers = {k: v for k, v in up.headers.items()
                    if k.lower() not in ("content-length", "transfer-encoding",
                                         "content-encoding", "connection")}
    return StreamingResponse(up.aiter_raw(), status_code=up.status_code,
                             headers=resp_headers,
                             background=BackgroundTask(up.aclose))


# ------------------------------------------------------------- agent gateway
class AgentCall(BaseModel):
    org: str
    node: str
    tool: str
    args: dict = {}


@app.post("/api/agent")
async def agent_call(body: AgentCall, request: Request):
    """Backend for the orgtree MCP server every node loads. The calling NODE is the
    actor — the ledger enforces authority, budgets, capability subsets, addressing,
    and the no-defaults hire rule."""
    # a sandboxed container's secret pins it to its OWN org — a compromised
    # sandbox cannot act as another org's agents
    bridge_slug = getattr(request.state, "bridge_slug", None)
    if bridge_slug and body.org != bridge_slug:
        raise HTTPException(403, "bridge secret is scoped to its own org")
    a = body.args
    drive: list[str] = []      # nodes whose turn should run after we release the lock
    ext_send = None            # (chat-id, body) outbound riding the chatq bridge
    org_send = None            # (dst-slug, body) outbound to another org's inbox
    with store.DOC_LOCK:
        try:
            org = store.load_org(body.org)
            org.node(body.node)
            if body.tool == "orgtree_message":
                result = org.post_mail(body.node, a.get("to", ""), a.get("body", ""),
                                       a.get("kind", "message"))
                delivered = result.get("delivered")
                if delivered and delivered.startswith("@ext:"):
                    # outbound to an external session — rides the chatq bridge
                    ext_send = (delivered[5:], a.get("body", ""))
                elif delivered and delivered.startswith("@org:"):
                    # outbound to ANOTHER ORG's inbox — direct, no chatq needed
                    org_send = (delivered[5:], a.get("body", ""))
                elif delivered and delivered.startswith("@mcp:"):
                    # a polling external chat: the org-inbox entry IS the
                    # delivery — the peer reads it via the extern MCP server
                    pass
                elif delivered is not None:
                    mail_notify(body.org, body.node,
                                USER if delivered == "user_inbox" else delivered)
                    # a deferred delivery (archived recipient) queues only —
                    # the mail is driven when the node is rehired
                    if delivered != "user_inbox" and not result.get("deferred"):
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
                drive.extend(result.pop("drive", []))
            elif body.tool == "orgtree_dissolve":
                result = org.dissolve(body.node, a.get("node"))
            elif body.tool == "orgtree_reallocate":
                result = org.reallocate(body.node, a.get("node"), int(a.get("delta") or 0))
            elif body.tool == "orgtree_switch_model":
                result = org.switch_model(body.node, a.get("node", ""),
                                          a.get("tier", ""))
            elif body.tool == "orgtree_status":
                status = a.get("status", "working")
                summary = a.get("summary", "")
                # persisted on the node (survives restarts); cleared when a
                # new turn starts, so a stale "done" never shows over live work
                org.node(body.node)["last_status"] = {
                    "status": status, "summary": summary}
                result = {"recorded": status}
                if status in ("done", "blocked"):
                    parent = org.node(body.node)["parent"]
                    if parent:
                        r = org.post_mail(body.node, parent,
                                          f"[{status.upper()}] {summary}", kind="status")
                        mail_notify(body.org, body.node, parent)
                        drive.append(parent)
                        result["reported_to"] = parent
                        result["warnings"] = r.get("warnings", [])
                    else:
                        # top-level: the user already gets the agent's own reply
                        # mail — a second [DONE] digest was pure duplication
                        # (user ruling). The status chip is the record.
                        result["reported_to"] = ("status chip only — report your "
                                                 "actual results to the user via "
                                                 "orgtree_message")
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
                    result = org.audience_grant(body.node, a.get("from", ""),
                                                a.get("target") or None)
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
            _kiosk_cap_check(org)
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    for target in drive:
        supervisor.send_message(
            body.org, target,
            "(orgtree) You have new mail above — handle it as appropriate, and use "
            "orgtree_status when your own task state changes.")
    if ext_send is not None:
        # org-voice (user spec): the message goes out under the ORG's name,
        # never the individual agent's
        ok = supervisor.chatq_send(
            body.org, ext_send[0],
            f"[message from orgtree org '{body.org}']\n" + ext_send[1])
        if not ok:
            result.setdefault("warnings", []).append(
                f"chatq delivery to {ext_send[0]} failed — is the target "
                f"chat still registered?")
    if org_send is not None:
        err = supervisor.interorg_send(body.org, org_send[0], org_send[1])
        if err:
            result.setdefault("warnings", []).append(f"not delivered: {err}")
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
    charter: str | None = None    # hire — short standing role card
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
        result = await _org_op_locked(slug, body)
    # rehire with a waiting mailbox: the mail queued while archived finally
    # gets acted on (user ruling) — drive outside the doc lock
    for t in result.pop("drive", []) if isinstance(result, dict) else []:
        supervisor.send_message(
            slug, t,
            "(orgtree) Mail above arrived while you were archived and waited "
            "for you — you are live again; handle it as appropriate.")
    return result


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
            if body.charter and body.charter.strip():
                org.node(result["node"])["charter"] = body.charter.strip()
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
        elif body.op == "switch_model":
            if body.tier is None:
                raise LedgerError("switch_model needs tier")
            result = org.switch_model(body.actor, body.node, body.tier)
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
        _kiosk_cap_check(org)
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

    # three listeners, three trust levels: the admin app stays LOOPBACK-ONLY
    # (user vision: root access never reaches the wider web); the public
    # listener serves nothing but preauthenticated /k/<token> URLs; the
    # bridge listener serves nothing but secret-gated sandbox traffic
    servers = [uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT))]
    if PUBLIC_PORT:
        servers.append(uvicorn.Server(uvicorn.Config(
            PublicGateway(app), host="0.0.0.0", port=PUBLIC_PORT)))
    if sandbox.BRIDGE_PORT:
        servers.append(uvicorn.Server(uvicorn.Config(
            BridgeGateway(app), host="0.0.0.0", port=sandbox.BRIDGE_PORT)))
    if len(servers) == 1:
        uvicorn.run(app, host="127.0.0.1", port=PORT)
        return

    async def serve_all():
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(serve_all())


if __name__ == "__main__":
    main()
