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
import posixpath
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid

# typing wave: Any/Response types must be RUNTIME imports — FastAPI evaluates
# endpoint annotation strings (PEP 563) at decoration time. Helper-only types
# stay under TYPE_CHECKING so the runtime import graph is unchanged.
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import sandbox, store, subproxy, supervisor
from .ledger import LedgerError, Org, USER, VIS_LEVELS, norm_dirs, norm_tools

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx
    # aliased: `Scope` is taken by the pydantic body model of the same name
    from starlette.types import ASGIApp, Receive, Scope as ASGIScope, Send

    # aliased: `KioskCfg` is taken by the pydantic body model of the same name
    from .schema import DirGrant, KioskCfg as KioskDoc

app = FastAPI(title="orgtree", version="1.0.0")


# ---- kiosk v2 (user vision): preauthenticated public URLs. Each kiosk-enabled
# org carries a secret token; the PUBLIC listener serves nothing but
# /k/<token>/… — the token IS the authentication and maps to exactly one org.
# The admin app binds 127.0.0.1 only, so root access never leaves this machine.
# The gate is SERVER-SIDE — hiding UI buttons is not enforcement.
_TOKEN_RE = re.compile(r"^/k/([A-Za-z0-9_-]{8,64})(/.*)?$")
_PUBLIC_STATIC = ("/assets/", "/favicon", "/vite.svg")   # index.html's absolute refs
_token_cache: dict[str, Any] = {"at": 0.0, "map": {}}


def _kiosk_token_map() -> dict[str, str]:
    """token → slug for every kiosk-enabled org. Rebuilt on a short TTL and
    invalidated on any kiosk-config write, so rotation revokes instantly."""
    if time.time() - _token_cache["at"] > 5:
        m: dict[str, str] = {}
        for o in store.list_orgs():
            try:
                k = store.load_org(o["slug"]).d.get("kiosk") or {}
            except LedgerError:
                continue
            if k.get("enabled") and k.get("token"):
                m[k["token"]] = o["slug"]  # type: ignore[typeddict-item]  # guard proves the key
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
        # /scope is OPEN (ceiling spec §2): visitors retool freely WITHIN the
        # kiosk permission ceiling — the ledger clamps, never a 403 here
        or rest.endswith("/kiosk")                           # kiosk caps/token/ceiling
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

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
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
        rest = m.group(2) or "/"  # type: ignore[union-attr]  # slug non-None ⇒ m matched
        deny = _public_denied(scope.get("method", "GET"), rest, slug)
        if deny:
            return await self._reject(scope, send, deny[0], deny[1])
        scope = dict(scope)
        scope["path"] = rest
        scope["raw_path"] = rest.encode()
        scope["state"] = {**(scope.get("state") or {}), "public_slug": slug}
        await self.inner(scope, receive, send)

    async def _reject(self, scope: ASGIScope, send: Send, code: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4000 + code})
            return
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": code,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _public_slug(request: Request) -> str | None:
    return getattr(request.state, "public_slug", None)


# ---- the sandbox bridge: the ONE door out of a kiosk container. Serves only
# the agent gateway + the steering fetch, gated by the org's sandbox secret
# (which exists nowhere but inside that org's container and its org doc).
_bridge_cache: dict[str, Any] = {"at": 0.0, "map": {}}
_STEER_RE = re.compile(r"^/api/orgs/([a-z0-9@-]+)/nodes/[^/]+/steer$")


def _bridge_secret_map() -> dict[str, str]:
    if time.time() - _bridge_cache["at"] > 5:
        m: dict[str, str] = {}
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

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:                      # the admin server owns app lifespan
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
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
_origin_cache: dict[str, Any] = {"at": 0.0, "val": ""}


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


def _kiosk_cap_check(org: Org) -> None:
    """Kiosk credit cap: NO operation may push total top-level holdings past
    the cap — covers hires, §4.6 cascades, rehires, reallocations and
    credit-request approvals in one invariant (checked before save). Applies
    to admin actions too: one invariant, and the admin can raise the cap."""
    k = supervisor.kiosk_cfg(org)
    if k and int(k.get("credits") or 0) > 0:
        held = org.audit()["top_level_holds"]
        if held > int(k["credits"]):  # type: ignore[typeddict-item]  # guard above proves the key
            raise LedgerError(
                f"kiosk credit cap: the org may hold at most "
                f"{int(k['credits'])} credits (this would make it {held:g})")  # type: ignore[typeddict-item]


mail_notify: Callable[[str, str, str], None] = \
    lambda slug, frm, to: None   # wired at startup (thread-safe fanout)


@app.on_event("startup")
async def _wire_notify() -> None:
    global mail_notify, _LOOP
    loop = asyncio.get_running_loop()
    _LOOP = loop
    try:
        # hook processes get a sanitized env — the steering hook finds us here
        open(os.path.join(store.DATA_ROOT, ".port"), "w",
             encoding="utf-8").write(str(PORT))
    except OSError:
        pass

    def notify(slug: str, node: str, event: str) -> None:
        asyncio.run_coroutine_threadsafe(hub.node_event(slug, node, event), loop)

    def _mail(slug: str, frm: str, to: str) -> None:
        # pure animation signal for the UI (spark on the wire) — no state rides on it
        asyncio.run_coroutine_threadsafe(
            hub._send(slug, {"type": "mail", "org": slug, "from": frm, "to": to}),
            loop)

    mail_notify = _mail

    def stream(slug: str, node: str, payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(
            hub._send(slug, {"type": "node_stream", "org": slug, "node": node,
                             **payload}), loop)

    supervisor.notify = notify
    supervisor.stream = stream
    supervisor.start_auto_resume_loop()
    # storage watchdog (user spec): catches single long tool calls —
    # clones/builds/downloads — that balloon past the limit MID-CALL
    supervisor.start_storage_watchdog()
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

    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = {}

    async def join(self, slug: str, ws: WebSocket) -> None:
        await ws.accept()
        self.rooms.setdefault(slug, set()).add(ws)

    def leave(self, slug: str, ws: WebSocket) -> None:
        self.rooms.get(slug, set()).discard(ws)

    async def _send(self, slug: str, payload: dict[str, Any]) -> None:
        dead = []
        for ws in self.rooms.get(slug, set()):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(slug, ws)

    async def changed(self, slug: str) -> None:
        await self._send(slug, {"type": "changed", "org": slug})

    async def node_event(self, slug: str, node: str, event: str) -> None:
        await self._send(slug, {"type": "node_event", "org": slug,
                                "node": node, "event": event})


hub = Hub()
# captured at startup — threadsafe broadcasts from sync code
_LOOP: asyncio.AbstractEventLoop | None = None


def hub_changed(slug: str) -> None:
    """Schedule a 'changed' broadcast from any thread (№22: the heavyweight
    endpoints are plain `def` now — they run in the threadpool and can't
    await)."""
    if _LOOP is not None:
        asyncio.run_coroutine_threadsafe(hub.changed(slug), _LOOP)


# ---------------------------------------------------------------------- orgs
class KioskSpec(BaseModel):
    credits: int = 30                 # top-level holdings cap (user ruling)
    spend_limit: float = 50.0         # USD hard limit (user ruling 2026-07-31)
    storage_limit_mb: int = 1024      # workspace+scratch cap (user ruling)
    sandbox: bool = True              # run agent turns in a Docker container
    # ceiling spec §3: the permission ceiling is visible/editable AT CREATION —
    # the default is permissive (mcp "*", user ruling), so narrowing it must
    # be a conscious act rather than something discovered later
    max_scope: dict | None = None     # None = the default ceiling
    auto_raise: bool = False          # admin over-ceiling grants auto-raise it
    # auth is NOT configurable (user ruling): every sandbox uses the proxied
    # subscription — the host attaches the token, the sandbox never sees it


class OrgCreate(BaseModel):
    name: str
    dirs: list[str] = []
    permission_mode: str = "acceptEdits"
    kiosk: KioskSpec | None = None    # present = the org is BORN a kiosk
    sandbox: bool = False             # normal orgs may sandbox too (user ruling)


@app.get("/api/orgs")
def orgs_list(request: Request) -> list[dict[str, Any]]:
    orgs = store.list_orgs()
    pub = _public_slug(request)
    if pub:
        # public visitors see exactly their token's org — nothing to discover
        return [{**o, "kiosk": True} for o in orgs if o["slug"] == pub]
    # admin: attach the kiosk dashboard summary (incl. the secret token —
    # this listener is loopback-only)
    out: list[dict[str, Any]] = []
    for o in orgs:
        try:
            org = store.load_org(o["slug"])
        except LedgerError:
            out.append(o)
            continue
        row = {**o, "cost_usd_total": org.cost_total()}
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
                # stale-served + background-refreshed: the walk never runs on
                # the request path (arti's took ~7 s and stalled every load)
                "storage_mb": (round(u / 1048576, 2)
                               if (u := supervisor.workspace_usage_cached(org))
                               is not None else None),
                "share_url": _share_url(k.get("token")),
            }
        out.append(row)
    return out


@app.post("/api/orgs")
def orgs_create(body: OrgCreate) -> dict[str, Any]:
    try:
        org = store.create_org(body.name, body.dirs, body.permission_mode)
    except LedgerError as e:
        raise HTTPException(400, str(e))
    # global default org settings (user spec): every new org is born with them
    dflt = load_org_defaults()
    if dflt:
        with store.DOC_LOCK:
            org.d.update(dflt)  # type: ignore[arg-type]  # defaults.json holds org-doc-shaped keys
            store.save_org(org)
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
                "auto_raise": bool(body.kiosk.auto_raise),
                "max_scope": None,     # set via the normalizer just below
            }
            try:
                prov = body.kiosk.max_scope
                if prov is not None and "add_dirs" not in prov:
                    # the create dialog edits tools/vis/pm; dir bounds default
                    # to the org's own folders unless explicitly stated
                    prov = {**prov,
                            "add_dirs": o.default_kiosk_ceiling()["add_dirs"]}
                o.d["kiosk"]["max_scope"] = o._norm_ceiling(
                    prov if prov is not None else o.default_kiosk_ceiling())
            except LedgerError as e:
                # unwind: without this, the org survived its own failed
                # creation as a non-kiosk org (registered + saved above)
                # while the 422 told the caller nothing was made
                store.delete_org(org.d["slug"])
                raise HTTPException(422, str(e))
            # a capped org never inherits the 50-credit hire pre-fill (user
            # report: the first hire swallowed the whole pool) — grants in a
            # kiosk are deliberate drags; the admin can set a sub-cap default
            o.d["default_top_grant"] = 0
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
def orgs_delete(slug: str) -> dict[str, Any]:
    try:
        store.delete_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    sandbox.remove(slug)            # container down; files stay (like scratch)
    supervisor.chatq_deregister_org(slug)
    return {"ok": True}


@app.get("/api/orgs/{slug}")
def org_tree(slug: str, request: Request) -> dict[str, Any]:
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    tree = org.tree()

    def annotate(node: dict[str, Any]) -> None:
        st = supervisor.state(slug, node["id"])
        node["busy"] = st["busy"]
        # №12: three states wore one pulse — split them: waiting on a turn
        # slot vs actually responding vs busy-but-between (draining/queued)
        node["waiting"] = bool(st.get("waiting"))
        node["responding"] = bool(st.get("responding"))
        node["phase"] = st.get("phase")     # e.g. "compacting" (№3)
        node["queued"] = len(st["queue"])
        node["last_error"] = st["last_error"]
        if st.get("occupancy"):       # runtime is fresher than the persisted copy
            node["occupancy"] = st["occupancy"]
        if st.get("context_window"):
            node["context_window"] = st["context_window"]
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
            # the permission ceiling — the admin gear edits it; _scrub_public
            # drops it (host paths) for visitors
            "max_scope": k.get("max_scope"),
            "auto_raise": bool(k.get("auto_raise")),
            # the tier cap rides OUTSIDE max_scope too: it's public-safe (a
            # tier name) and the visitor UI needs it to hide spawn tokens
            "max_tier": (k.get("max_scope") or {}).get("max_tier"),
            # per-kiosk admin controls live in the org's own settings panel
            # (user ruling 2026-07-31 — the all-kiosks dashboard is gone);
            # share_url is admin-only, _scrub_public pops it
            "enabled": bool(k.get("enabled")),
            "sandbox": bool(k.get("sandbox")),
            "share_url": _share_url(k.get("token")),
        }
        if k.get("storage_limit_mb"):
            u = supervisor.workspace_usage_cached(org)
            if u is not None:
                tree["kiosk"]["storage_mb"] = round(u / 1048576, 2)
    if sandbox.is_sandboxed(org) and sandbox.on_disk(slug):
        # the org disk's headline numbers ride every tree payload: the
        # persistent hard-full alert is STATE (survives reload), and the
        # storage chip needs used/total without a second request
        from . import disk as dsk
        du = dsk.usage(slug, max_age=15.0)
        tree["disk"] = {
            "used_mb": round(du[0] / 1048576, 1) if du else None,
            "total_mb": round(du[1] / 1048576, 1) if du else None,
            "blocked": bool(tree.get("storage_blocked")),
            "full": bool(org.d.get("storage_full")),
        }
    if _public_slug(request):
        # tells the UI to lock itself down; the SERVER gate is the enforcement
        tree["public"] = True
        _scrub_public(tree)
    return tree


_WINPATH = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:home|Users|opt|mnt|tmp)/)[^\s'\"]*")


def _scrub_public(tree: dict[str, Any]) -> None:
    """№18: a kiosk share link served the operator's ABSOLUTE host paths and
    username in every tree payload (workspace, every dir grant, session ids,
    raw error strings). Public visitors get basenames and scrubbed errors —
    they interact with the org, not the operator's filesystem."""
    def base(p: Any) -> str:
        return os.path.basename(str(p).rstrip("/\\")) or "folder"
    if tree.get("workspace"):
        tree["workspace"] = base(tree["workspace"])
    tree["dirs"] = [{**d, "path": base(d.get("path", ""))}
                    for d in tree.get("dirs") or []]
    if isinstance(tree.get("kiosk"), dict):
        # the ceiling's add_dirs are host paths; visitors see clamp warnings
        # naming the ceiling, never the ceiling itself
        tree["kiosk"].pop("max_scope", None)
        tree["kiosk"].pop("auto_raise", None)
        tree["kiosk"].pop("share_url", None)

    def walk(n: dict[str, Any]) -> None:
        n.pop("session_id", None)
        sc = n.get("scope") or {}
        if sc.get("add_dirs"):
            sc["add_dirs"] = [{**d, "path": base(d.get("path", ""))}
                              for d in sc["add_dirs"]]
        if n.get("last_error"):
            n["last_error"] = _WINPATH.sub("<path>", str(n["last_error"]))
        for c in n.get("children") or []:
            walk(c)
        for ln in n.get("lineage") or []:
            if isinstance(ln, dict):
                ln.pop("session_id", None)
    for r in tree.get("roots") or []:
        walk(r)


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
    default_effort: str | None = None       # ""=CLI default | low..max (live inherit)
    auto_resume: bool | None = None         # restart limit-frozen agents at reset+1min
    cascade_hire: bool | None = None        # hires bubble costs up the chain (§4.6)
    cascade_alloc: bool | None = None       # allocations/upgrades bubble costs up


# ------------------------------------------- global default org settings
# (user spec): configured from the root page; every NEWLY created org is
# born with these values. Stored org-doc-shaped in <data>/defaults.json.
_DEFAULTS_BASE = {
    "max_top_grant": 1000, "default_top_grant": 50, "compact_at": 0.80,
    "fable_limit_policy": "halt", "fable_filter_policy": "halt",
    "cascade_hire": True, "cascade_alloc": True, "auto_resume": False,
}


def load_org_defaults() -> dict[str, Any]:
    try:
        d = json.load(open(os.path.join(store.DATA_ROOT, "defaults.json"),
                           encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


@app.get("/api/defaults")
def defaults_get() -> dict[str, Any]:
    return {**_DEFAULTS_BASE, **load_org_defaults()}


@app.post("/api/defaults")
def defaults_set(body: Settings) -> dict[str, Any]:
    d = load_org_defaults()
    if body.max_top_grant is not None and body.max_top_grant > 0:
        d["max_top_grant"] = int(body.max_top_grant)
    if body.default_top_grant is not None and body.default_top_grant >= 0:
        d["default_top_grant"] = int(body.default_top_grant)
    if body.compact_at is not None:
        d["compact_at"] = min(95, max(50, int(body.compact_at))) / 100.0
    if body.fable_limit_policy in ("halt", "opus", "dissolve"):
        d["fable_limit_policy"] = body.fable_limit_policy
    if body.fable_filter_policy in ("halt", "opus"):
        d["fable_filter_policy"] = body.fable_filter_policy
    if body.default_tools is not None:
        d["default_tools"] = norm_tools(body.default_tools)
    if body.default_visibility in VIS_LEVELS:
        d["default_visibility"] = body.default_visibility
    if body.default_effort is not None \
            and body.default_effort in ("", *Org.EFFORTS):
        d["default_effort"] = body.default_effort
    if body.auto_resume is not None:
        d["auto_resume"] = bool(body.auto_resume)
    if body.cascade_hire is not None:
        d["cascade_hire"] = bool(body.cascade_hire)
    if body.cascade_alloc is not None:
        d["cascade_alloc"] = bool(body.cascade_alloc)
    with open(os.path.join(store.DATA_ROOT, "defaults.json"), "w",
              encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    return defaults_get()


@app.post("/api/orgs/{slug}/settings")
def org_settings(slug: str, body: Settings) -> dict[str, Any]:
    """Org-level knobs. Folder holdings (org_dirs) are edited from the eye's
    gear panel: the workspace is permanent; additions apply to FUTURE hires;
    removals revoke everywhere; rw→ro downgrades propagate to every grant."""
    with store.DOC_LOCK:
        return _org_settings_locked(slug, body)


def _org_settings_locked(slug: str, body: Settings) -> dict[str, Any]:
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    ws = org.d.get("workspace")
    warnings: list[str] = []
    if body.org_dirs is not None:
        # org folder holdings live on the eye's gear (user ruling). Removals
        # revoke everywhere; an rw→ro downgrade propagates to every node's
        # grant (upgrades don't auto-propagate — grant per node deliberately).
        new: list[DirGrant] = [
            {"path": os.path.normpath(d["path"]), "mode": d["mode"]}
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
        ws_dir: list[DirGrant] = [{"path": ws, "mode": "rw"}] if ws else []
        org.d["dirs"] = ws_dir + new
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
    if body.default_tools is not None or body.default_visibility in VIS_LEVELS:
        # agent defaults: applied to unspecified hires — top level directly,
        # deeper as ∩ with the superior's capability (clamped at hire time).
        # Routed through the ledger so the kiosk ceiling clamps stored
        # defaults too (admin surface → auto_raise applies)
        r = org.set_hire_defaults(
            default_tools=body.default_tools,
            default_visibility=(body.default_visibility
                                if body.default_visibility in VIS_LEVELS
                                else None),
            raise_ceiling=bool((org.d.get("kiosk") or {}).get("auto_raise")))
        warnings.extend(r.get("warnings") or [])
    if body.default_effort is not None \
            and body.default_effort in ("", *Org.EFFORTS):
        # deliberately outside the ceiling (user cost-dial ruling): no clamp;
        # "" = CLI default; unset-node efforts inherit this LIVE at turn time
        org.d["default_effort"] = body.default_effort
    if body.auto_resume is not None:
        org.d["auto_resume"] = bool(body.auto_resume)
    if body.cascade_hire is not None:
        org.d["cascade_hire"] = bool(body.cascade_hire)
    if body.cascade_alloc is not None:
        org.d["cascade_alloc"] = bool(body.cascade_alloc)
    store.save_org(org)
    hub_changed(slug)
    return {"dirs": org.d["dirs"], "warnings": warnings}


class KioskCfg(BaseModel):
    enabled: bool | None = None
    credits: int | None = None            # top-level holdings cap (0 = uncapped)
    spend_limit: float | None = None      # USD hard limit (0 = unlimited)
    storage_limit_mb: int | None = None   # workspace-dir size cap (0 = unlimited)
    rotate_token: bool = False            # mint a new secret URL (revokes the old)
    max_scope: dict | None = None         # the permission ceiling; setting it SWEEPS
    auto_raise: bool | None = None        # admin over-ceiling grants auto-raise it


@app.post("/api/orgs/{slug}/kiosk")
async def org_kiosk(slug: str, body: KioskCfg) -> dict[str, Any]:
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
        # the raise-guard above proves the key; declared so the None arm
        # doesn't cascade through every touch below
        k: KioskDoc = org.d["kiosk"]  # type: ignore[typeddict-item, assignment]
        if body.enabled is not None:
            k["enabled"] = bool(body.enabled)
        if body.credits is not None:
            k["credits"] = max(0, int(body.credits))
        if body.spend_limit is not None:
            k["spend_limit"] = max(0.0, float(body.spend_limit))
        if body.storage_limit_mb is not None:
            k["storage_limit_mb"] = max(0, int(body.storage_limit_mb))
        # security review 2026-08-01: subscription-auth (copied host OAuth
        # credentials ON the org disk) and a public kiosk URL are mutually
        # exclusive — structurally, not by filename filter (root-in-container
        # can copy the token anywhere the recovery browser serves)
        if k.get("enabled") and k.get("sandbox") \
                and sandbox.uses_subscription_auth(dict(k)):
            raise HTTPException(
                422, "this org's sandbox runs on COPIED host credentials "
                     "(subscription auth) — a public kiosk URL would let "
                     "visitors reach them. Switch to proxied auth first.")
        if (k.get("enabled") and not k.get("token")) or body.rotate_token:
            k["token"] = secrets.token_hex(16)
        # the permission ceiling (consensus spec): setting it SWEEPS every
        # node's stored scope to fit — determinate, so it automates; affected
        # live agents are notified with what they lost
        ceiling_warnings: list[str] = []
        if body.max_scope is not None:
            try:
                r = org.set_kiosk_ceiling(body.max_scope,
                                          auto_raise=body.auto_raise)
            except LedgerError as e:
                raise HTTPException(422, str(e))
            ceiling_warnings = r.get("warnings") or []
            k = org.d["kiosk"]  # type: ignore[typeddict-item, assignment]  # set_kiosk_ceiling keeps the key
        elif body.auto_raise is not None:
            k["auto_raise"] = bool(body.auto_raise)
        # user ruling: the cap can never go BELOW what the org already holds —
        # retire/dissolve agents first, then lower it
        if k.get("enabled") and int(k.get("credits") or 0):
            held = org.audit()["top_level_holds"]
            if int(k["credits"]) < held:  # type: ignore[typeddict-item]  # guard above proves the key
                raise HTTPException(
                    422, f"cap below current holdings: the org holds {held:g} "
                         f"credits — retire or dissolve agents first, then lower it")
        org.d["kiosk"] = k
        cleared = []
        spent = org.cost_total()            # incl. deleted agents' burn
        lim = float(k.get("spend_limit") or 0)
        over = k.get("enabled") and lim and spent >= lim
        drive_after = []
        if org.d.get("spend_frozen") and not over:
            supervisor.clear_hard_freeze(org, "spend")
            cleared.append("spend")
            # review C7: nodes whose freeze dropped entirely (no interrupted
            # turn to replay via ▶) but whose mailbox filled during the freeze
            # would sit idle until a restart's revive scan — drive them now
            drive_after = [k for k, v in org.nodes.items()
                           if v["state"] == "live" and not v.get("frozen")
                           and (org.d.get("mail") or {}).get(k)]
        store.save_org(org)
        need_freeze = over and not org.d.get("spend_frozen")
    # limits apply in REAL TIME (user ruling), both directions: lowering the
    # spend limit below what's already spent freezes now, not at the next
    # turn's end; the storage recheck applies/lifts the write block likewise
    if need_freeze:
        supervisor.hard_freeze(slug, "spend", "kiosk spend limit reached")
    for t in drive_after:
        supervisor.send_message(
            slug, t, "(orgtree) The spend freeze was lifted — you have mail "
                     "above that arrived while frozen; handle it now.")
    if supervisor.storage_check(slug) == "cleared":
        cleared.append("storage")
    _token_cache["at"] = 0.0             # rotation/enable takes effect now
    await hub.changed(slug)
    safe = {kk: v for kk, v in k.items()
            if kk not in ("api_key", "sandbox_secret")}
    return {"kiosk": safe, "share_url": _share_url(k.get("token")),
            "freezes_cleared": cleared,
            **({"warnings": ceiling_warnings} if ceiling_warnings else {})}


class HireDefaults(BaseModel):
    default_tools: dict | None = None       # {bash, web, edit, subagents, mcp}
    default_visibility: str | None = None   # self|team|subtree|full
    raise_ceiling: bool = False             # admin bridge (ignored for visitors)


@app.post("/api/orgs/{slug}/defaults")
async def org_hire_defaults(slug: str, body: HireDefaults,
                            request: Request) -> dict[str, Any]:
    """Agent-hire defaults — OPEN to kiosk visitors (user ruling 2026-07-31):
    a default is a pre-filled grant, so the ceiling clamps it like any grant.
    The rest of /settings (org folders, caps, policies) stays admin-only."""
    pub = bool(_public_slug(request))
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            rc = (not pub) and (bool((org.d.get("kiosk") or {}).get("auto_raise"))
                                or body.raise_ceiling)
            result = org.set_hire_defaults(
                default_tools=body.default_tools,
                default_visibility=body.default_visibility,
                raise_ceiling=rc)
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    if pub and isinstance(result, dict):
        result.pop("bridge", None)
    await hub.changed(slug)
    return result


class Scope(BaseModel):
    add_dirs: list[dict] | None = None      # [{path, mode: rw|ro}]
    tools: dict | None = None               # {bash, web, edit, subagents, mcp: []}
    org_visibility: str | None = None
    permission_mode: str | None = None      # rides the ceiling (spec §2)
    charter: str | None = None              # §15: this node's role card
    team_charter: str | None = None         # §15: binds this node's whole subtree
    effort: str | None = None               # thinking effort: low|medium|high|"" clears
    raise_ceiling: bool = False             # the one-action bridge (spec §1)


@app.post("/api/orgs/{slug}/nodes/{nid}/scope")
async def node_scope(slug: str, nid: str, body: Scope,
                     request: Request) -> dict[str, Any]:
    pub = bool(_public_slug(request))
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            rc = (not pub) and (bool((org.d.get("kiosk") or {}).get("auto_raise"))
                                or body.raise_ceiling)
            result = org.set_scope(USER, nid, add_dirs=body.add_dirs, tools=body.tools,
                                   org_visibility=body.org_visibility,
                                   permission_mode=body.permission_mode,
                                   charter=body.charter,
                                   team_charter=body.team_charter,
                                   effort=body.effort, raise_ceiling=rc)
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    if pub and isinstance(result, dict):
        result.pop("bridge", None)
    await hub.changed(slug)
    return result


@app.get("/api/fs")
def fs_list(path: str = "") -> dict[str, Any]:
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
def charters_list() -> dict[str, Any]:
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
            out.append({"name": f[:-3].replace("-", " "), "content": body[:6000],
                        # shown on hover of a picked preset card (user spec)
                        "path": os.path.abspath(os.path.join(CHARTERS_DIR, f))})
    return {"charters": out}


@app.get("/api/mcp-servers")
def mcp_servers() -> dict[str, Any]:
    """Names of the user's globally registered MCP servers, grantable per node.
    sandbox_mcp = the experimental ORGTREE_SANDBOX_MCP flag: without it, ALL
    servers are excluded from sandboxed orgs (external contact points the
    sandbox restricts) and the UI greys them out."""
    return {"servers": sorted(supervisor.registered_mcp_servers()),
            "sandbox_mcp": supervisor.sandbox_mcp_enabled()}


@app.get("/api/host")
def host_info() -> dict[str, Any]:
    """Host capabilities the UI adapts to (e.g. no Docker → the sandbox
    checkbox is disabled at org creation)."""
    return {"docker": sandbox.docker_available(),
            "sandbox_mcp": supervisor.sandbox_mcp_enabled(),
            "cli_version": supervisor.cli_version()}


class Reorder(BaseModel):
    before: str | None = None
    after: str | None = None


@app.post("/api/orgs/{slug}/nodes/{nid}/reorder")
async def node_reorder(slug: str, nid: str, body: Reorder) -> dict[str, Any]:
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
    # relative uploads/ paths already landed via the upload endpoint — the
    # composer stages them and sends them WITH the mail (user spec 2026-07-31)
    attachments: list[str] = []


@app.post("/api/orgs/{slug}/nodes/{nid}/message")
def node_message(slug: str, nid: str, body: Message) -> dict[str, Any]:
    """A user message IS mail (user ruling — the direct-message channel was
    folded into the mail system): it lands persisted in the node's mailbox
    (and in your Sent folder), then the node is driven; a busy node gets it
    mid-task via steering, never an interrupt. Talking to a non-top-level
    node notifies its whole superior chain (§7.4) and grants a user audience."""
    if not body.text.strip():
        raise HTTPException(422, "empty message")
    stripped = body.text.strip()
    # SLASH COMMAND (user-approved, 2026-07-31): a session command, not
    # correspondence — no mail entry, and it must reach the CLI with the
    # "/" at position 0 (the envelope would prepend [MAIL …] otherwise).
    # Command-SHAPED only (review C3b): "/compact", "/context foo" — a first
    # token with internal slashes ("/e/Libraries/report.md — pick this up")
    # is correspondence and keeps full mail semantics (durability, Sent,
    # chain notices, the user-audience grant).
    if stripped.startswith("/") \
            and re.fullmatch(r"/[A-Za-z?][\w-]*", stripped.split()[0]):
        with store.DOC_LOCK:
            try:
                org = store.load_org(slug)
                n = org.node(nid)
            except LedgerError as e:
                raise HTTPException(404, str(e))
            # review C3a: the old path returned "accepted" for nodes that run
            # nothing, while the composer printed "delivering"/"deferred —
            # delivers at rehire" — affirmatively false, since the command
            # path persists no copy anywhere. Refuse with the real reason
            # instead (house style: manual_compact's own 409).
            if n["state"] != "live":
                raise HTTPException(
                    409, f"{nid} is {n['state']} — a session command runs "
                         f"nothing there and is not mail (nothing would "
                         f"survive to deliver at rehire); rehire first, or "
                         f"send it as a plain message")
            if n.get("frozen"):
                raise HTTPException(
                    409, "frozen (usage limit) — a session command would be "
                         "dropped, not queued; ▶ resume the org first")
        if stripped.split()[0] == "/compact":
            # review C4: one word, one meaning. The hinted /compact used to
            # compact the CLI session IN PLACE — same desk, same word as the
            # compact button, opposite §8 consequence (no knowledge bearer).
            # It now routes to the same org split the button runs.
            if n.get("bearer_state"):
                raise HTTPException(422, "a knowledge bearer never re-compacts (§8.3)")
            if not n.get("occupancy"):
                raise HTTPException(422, "no conversation yet — nothing to compact")
            if supervisor.state(slug, nid)["busy"]:
                raise HTTPException(409, "busy — wait for the current turn to finish")

            def run() -> None:
                try:
                    supervisor.manual_compact(slug, nid)
                except RuntimeError:
                    pass      # raced into busy — the 409 precheck caught most

            threading.Thread(target=run, daemon=True).start()
            r: dict[str, Any] = {"accepted": True, "compacting": True}
            if stripped != "/compact":
                r["warnings"] = ["/compact arguments are ignored — org "
                                 "compaction preserves the whole session as "
                                 "a knowledge bearer"]
            return r
        # /context-class commands (user spec): answered IMMEDIATELY via a
        # throwaway session fork — works mid-turn, output rides the live feed
        if supervisor.immediate_command(slug, nid, stripped):
            return {"accepted": True, "command": True, "immediate": True}
        return supervisor.send_message(slug, nid, stripped, command=True)
    # staged attachments: already-uploaded files in the node's own scratch —
    # verify each really exists there (traversal-guarded) and ride metadata
    metas: list[dict[str, Any]] = []
    if body.attachments:
        base = os.path.realpath(supervisor.scratch_dir(slug, nid))
        for rel in body.attachments[:10]:
            full = os.path.realpath(os.path.join(base, str(rel).lstrip("/\\")))
            if full.startswith(base + os.sep) and os.path.isfile(full):
                metas.append({"name": os.path.basename(full),
                              "path": str(rel).replace("\\", "/"),
                              "bytes": os.path.getsize(full)})
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            r = org.post_mail(USER, nid, body.text, attachments=metas or None)
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
async def node_steer(slug: str, nid: str) -> dict[str, Any]:
    """Called by the PostToolUse steering hook inside a node's turn: pops ALL
    the node's pending mid-task mail — user and agent alike — for immediate
    delivery (sender attribution rides inside each message)."""
    # storage-bypass audit: every tool call gives the storage limit a chance
    # to land MID-TURN (throttled + backgrounded inside)
    supervisor.maybe_storage_check(slug)
    msgs = supervisor.pop_steer(slug, nid)
    if msgs:
        for m in msgs:
            await hub._send(slug, {"type": "node_stream", "org": slug,
                                   "node": nid, "kind": "steered", "text": m[:2000]})
    return {"messages": msgs}


@app.post("/api/orgs/{slug}/nodes/{nid}/interrupt")
def node_interrupt(slug: str, nid: str) -> dict[str, Any]:
    """Manual ⏸: stop the node's current response (the only sanctioned
    interrupt — message delivery never interrupts, user ruling)."""
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    return supervisor.interrupt_turn(slug, nid)


@app.post("/api/orgs/{slug}/nodes/{nid}/compact")
def node_compact(slug: str, nid: str) -> dict[str, Any]:
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

    def run() -> None:
        # №27: manual_compact latches busy for the whole fork — mail arriving
        # mid-split queues instead of driving the doomed old session
        try:
            supervisor.manual_compact(slug, nid)
        except RuntimeError:
            pass          # raced into busy — the 409 precheck caught most; harmless

    threading.Thread(target=run, daemon=True).start()
    return {"started": True}


@app.post("/api/orgs/{slug}/dissolve-all")
async def org_dissolve_all(slug: str) -> dict[str, Any]:
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
async def org_killswitch(slug: str) -> dict[str, Any]:
    """⏹ STOP ALL: interrupt every active agent and clear pending queues."""
    try:
        store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    result = supervisor.interrupt_all(slug)
    await hub.changed(slug)
    return result


@app.post("/api/orgs/{slug}/resume")
async def org_resume(slug: str) -> dict[str, Any]:
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
async def credit_request_decide(slug: str, body: CreditDecision) -> dict[str, Any]:
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
def user_inbox(slug: str) -> dict[str, Any]:
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
async def user_inbox_read(slug: str, body: InboxRead) -> dict[str, Any]:
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
    attachments: list[str] = []   # absolute local paths (extern peers are local)


def _extern_peer(peer: str) -> str:
    if not _PEER_RE.fullmatch(peer):
        raise HTTPException(422, "peer id must be 1-64 chars of [A-Za-z0-9._-]")
    return f"@mcp:{peer}"


@app.post("/api/extern/{peer}/send")
def extern_send(peer: str, body: ExternSend) -> dict[str, Any]:
    addr = _extern_peer(peer)
    if not body.body.strip():
        raise HTTPException(422, "empty message")
    # attachments (user spec 2026-07-31): absolute paths on this machine —
    # extern peers are local sessions. Validated here; copied into every
    # recipient's uploads/ by deliver_org_inbox.
    atts = []
    for p in (body.attachments or [])[:10]:
        p = str(p)
        if not os.path.isfile(p):
            raise HTTPException(422, f"attachment not found: {p}")
        if os.path.getsize(p) > 25 * 1048576:
            raise HTTPException(413, f"attachment over the 25 MB cap: {p}")
        atts.append(p)
    with store.DOC_LOCK:
        try:
            org = store.load_org(body.org)
        except LedgerError:
            org = None
        # sealed kiosks must be INDISTINGUISHABLE from nonexistent orgs out
        # here (review finding: a 403 vs 404 split let an outside peer
        # enumerate the kiosk roster the org listing deliberately withholds)
        if org is None or org.is_kiosk:
            raise HTTPException(404, f"no organization named {body.org!r}")
    delivered = supervisor.deliver_org_inbox(body.org, addr, body.body,
                                             attachments=atts or None)
    return {"delivered": delivered or ["(user inbox — no live agents)"]}


def _extern_scan(addr: str, org_slug: str | None, after: str | None,
                 fresh_only: bool = False) -> list[dict[str, Any]]:
    """Replies addressed to `addr`. `fresh_only` (the wait path, №5): with no
    explicit cursor, only replies newer than the peer's own LAST message to
    that org count — a wait for question ② must never be satisfied by the
    answer to question ①. The read path stays full-history (freeform flow:
    the org may reply any time, any number of times)."""
    out = []
    with store.DOC_LOCK:
        for o in store.list_orgs():
            if org_slug and o["slug"] != org_slug:
                continue
            try:
                org = store.load_org(o["slug"])
            except LedgerError:
                continue
            if org.is_kiosk:
                # unreachable today (kiosk inboxes can hold no "out" entries —
                # the ledger seals every inbound/outbound path), but the seal
                # belongs on THIS path too, locally, not as a 3-file argument
                continue
            entries = org.d.get("org_inbox", [])
            floor = after
            if not floor and fresh_only:
                # timestamps are millisecond-resolution now (user ruling), so
                # the floor is simply the peer's own latest message to the org
                mine = [e.get("at", "") for e in entries
                        if e.get("peer") == addr and e.get("dir") == "in"]
                if not mine:
                    # the peer's own inbound was trimmed (the 200-entry log
                    # cap) or never existed — nothing is provably fresh, and
                    # a collapsed floor would hand back the whole history:
                    # exactly what fresh_only exists to prevent (review P1)
                    continue
                floor = max(mine)
            for e in entries:
                if e.get("peer") == addr and e.get("dir") == "out" \
                        and (not floor or e.get("at", "") > floor):
                    out.append({"org": o["slug"], "id": e["id"],
                                "at": e["at"], "body": e["body"]})
    out.sort(key=lambda x: x["at"])
    return out


@app.get("/api/extern/{peer}/messages")
def extern_messages(peer: str, org: str | None = None,
                    after: str | None = None) -> dict[str, Any]:
    msgs = _extern_scan(_extern_peer(peer), org, after)
    # the cursor rides every reply (review P1): pass it back as `after` and a
    # repeat wait/read can never re-deliver what this call already handed over
    return {"messages": msgs, **({"cursor": msgs[-1]["at"]} if msgs else {})}


@app.get("/api/extern/{peer}/wait")
async def extern_wait(peer: str, org: str | None = None,
                      after: str | None = None, timeout: int = 25) -> dict[str, Any]:
    """Long-poll: block until an org replies to this peer (or timeout).
    Rescans (DOC_LOCK + org-doc reads) only when store.REVISION moved —
    review finding: parked waiters were paying a full scan every second
    under the same lock the turn machinery serialises on."""
    addr = _extern_peer(peer)
    deadline = time.monotonic() + min(max(timeout, 1), 55)
    rev = None
    while True:
        if rev != store.REVISION:
            rev = store.REVISION
            msgs = _extern_scan(addr, org, after, fresh_only=True)
            if msgs:
                return {"messages": msgs, "cursor": msgs[-1]["at"]}
        if time.monotonic() >= deadline:
            return {"messages": []}
        await asyncio.sleep(1.0)


@app.post("/api/orgs/{slug}/org_inbox/read")
async def org_inbox_read(slug: str) -> dict[str, Any]:
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
async def user_inbox_clear(slug: str) -> dict[str, Any]:
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
def node_history(slug: str, nid: str, last: int = 80) -> dict[str, Any]:
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
            # №10: keep warning LISTS too — the scalar filter silently dropped
            # the §4.6 cascade warnings from the only log that had them
            items.append({"at": ev["at"], "kind": ev["op"], "actor": ev["actor"],
                          "detail": {k: (v if isinstance(v, (str, int, float))
                                         else [str(x) for x in v])
                                     for k, v in det.items()
                                     if isinstance(v, (str, int, float, list))},
                          "warnings": [str(w) for w in ev.get("warnings") or []]})
    for n in org.d.get("notice_log", []):
        if n["node"] == nid:
            items.append({"at": n["at"], "kind": "notice", "actor": "system",
                          "detail": {"text": n["text"]}})
    items.sort(key=lambda x: x["at"])
    return {"items": items[-last:]}


@app.get("/api/orgs/{slug}/nodes/{nid}/scratch")
def node_scratch(slug: str, nid: str, path: str = "") -> dict[str, Any]:
    base = os.path.realpath(supervisor.scratch_dir(slug, nid))
    full = os.path.realpath(os.path.join(base, path.lstrip("/\\")))
    # separator-anchored: a bare prefix test admits sibling dirs (<base>-x)
    if full != base and not full.startswith(base + os.sep):
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
def orgmd_get(slug: str) -> dict[str, Any]:
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
async def orgmd_put(slug: str, body: OrgMd) -> dict[str, Any]:
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


class AudienceAction(BaseModel):
    action: str            # grant | deny | revoke
    node: str              # the grantee / requester
    target: str | None = None


@app.post("/api/orgs/{slug}/audiences")
async def user_audience(slug: str, body: AudienceAction) -> dict[str, Any]:
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
def audiences_list(slug: str) -> dict[str, Any]:
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
_hx: httpx.AsyncClient | None = None


def _upstream() -> httpx.AsyncClient:
    global _hx
    if _hx is None:
        import httpx
        _hx = httpx.AsyncClient(base_url="https://api.anthropic.com",
                                timeout=httpx.Timeout(600.0, connect=30.0))
    return _hx


@app.api_route("/anthropic/{path:path}",
               methods=["GET", "POST", "HEAD", "PUT", "DELETE"])
async def anthropic_proxy(path: str, request: Request) -> StreamingResponse:
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
def agent_call(body: AgentCall, request: Request) -> dict[str, Any]:
    """Backend for the orgtree MCP server every node loads. The calling NODE is the
    actor — the ledger enforces authority, budgets, capability subsets, addressing,
    and the no-defaults hire rule.

    №22: plain `def` (threadpooled) — this endpoint parses transcripts and
    walks scratch dirs, and as `async def` it did that ON the event loop
    while holding DOC_LOCK. The read-only tools also run outside the lock
    entirely: they read the filesystem, not the doc."""
    # a sandboxed container's secret pins it to its OWN org — a compromised
    # sandbox cannot act as another org's agents
    bridge_slug = getattr(request.state, "bridge_slug", None)
    if bridge_slug and body.org != bridge_slug:
        raise HTTPException(403, "bridge secret is scoped to its own org")
    a = body.args
    if body.tool in ("orgtree_read_transcript", "orgtree_read_scratch",
                     "orgtree_chart", "orgtree_send_file"):
        try:
            org = store.load_org(body.org)
            org.node(body.node)
            if body.tool == "orgtree_chart":
                return {"chart": supervisor.identity_prompt(org, body.node)}
            if body.tool == "orgtree_send_file":
                # filesystem-only (org doc untouched) — runs outside DOC_LOCK
                # with the other read-shaped tools
                return _agent_send_file(org, body.node, a)
            if body.tool == "orgtree_read_transcript":
                target = a.get("node", "")
                if target != body.node and not org.is_ancestor(body.node, target):
                    raise LedgerError("read access is strictly DOWNWARD (§7.6) — you "
                                      "may read yourself and your descendants only")
                chat = supervisor.read_chat(org, target)
                last = max(1, min(int(a.get("last") or 30), 80))
                msgs = chat["messages"][-last:]
                return {"node": target, "busy": chat["busy"],
                        "occupancy": chat["occupancy"],
                        "messages": [{"role": m["role"],
                                      "text": (m.get("text") or "")[:1200],
                                      "tools": m.get("tools", [])} for m in msgs]}
            target = a.get("node", "")
            if target != body.node and not org.is_ancestor(body.node, target):
                raise LedgerError("read access is strictly DOWNWARD (§7.6)")
            base = os.path.realpath(supervisor.scratch_dir(body.org, target))
            rel = (a.get("path") or "").strip().lstrip("/\\")
            full = os.path.realpath(os.path.join(base, rel))
            # separator-anchored: a bare prefix test admits sibling dirs
            if full != base and not full.startswith(base + os.sep):
                raise LedgerError("path escapes the scratch space")
            if os.path.isdir(full):
                return {"dir": rel or ".", "entries": sorted(os.listdir(full))[:200]}
            if os.path.isfile(full):
                return {"file": rel,
                        "content": open(full, encoding="utf-8",
                                        errors="replace").read()[:20000]}
            return {"error": f"no such path in {target}'s scratch: {rel!r}"}
        except LedgerError as e:
            raise HTTPException(422, str(e))
    result: dict[str, Any]
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
                hdirs, dwarns = supervisor.sandbox_dirs_to_host(
                    org, a.get("add_dirs"))
                result = org.hire(body.node, a.get("parent") or body.node,
                                  a.get("tier"), int(a.get("grant") or 0),  # type: ignore[arg-type]  # ledger 422s a missing tier
                                  a.get("name") or "", add_dirs=hdirs,
                                  tools=a.get("tools"),
                                  org_visibility=a.get("org_visibility"),
                                  charter=a.get("charter"))
                if dwarns:
                    result.setdefault("warnings", []).extend(dwarns)
            elif body.tool == "orgtree_retool":
                # effort joins retool (ceiling spec §6): a cost dial, so a
                # superior may set it on REPORTS — never on itself (set_scope's
                # authority check refuses self). raise_ceiling is deliberately
                # NOT plumbed: an agent can never raise a kiosk ceiling.
                rdirs, dwarns = supervisor.sandbox_dirs_to_host(
                    org, a.get("add_dirs"))
                result = org.set_scope(body.node, a.get("node", ""),
                                       add_dirs=rdirs,
                                       tools=a.get("tools"),
                                       org_visibility=a.get("org_visibility"),
                                       charter=a.get("charter"),
                                       team_charter=a.get("team_charter"),
                                       effort=a.get("effort"))
                if dwarns:
                    result.setdefault("warnings", []).extend(dwarns)
            elif body.tool == "orgtree_retire":
                result = org.retire(body.node, a.get("node"))  # type: ignore[arg-type]  # node() 422s on None
            elif body.tool == "orgtree_rehire":
                result = org.rehire(body.node, a.get("node"), a.get("grant"))  # type: ignore[arg-type]  # node() 422s on None
                drive.extend(result.pop("drive", []))
            elif body.tool == "orgtree_move":
                result = org.move(body.node, a.get("node", ""),
                                  a.get("new_parent") or None)
            elif body.tool == "orgtree_list_orgs":
                # №43 (user-approved): the @org: channel was advertised but
                # undiscoverable from inside — agents had no org listing
                result = {"orgs": [
                    {"slug": o["slug"], "name": o.get("name", o["slug"]),
                     "you": o["slug"] == body.org}
                    for o in store.list_orgs() if not o.get("kiosk")]}
            elif body.tool == "orgtree_dissolve":
                result = org.dissolve(body.node, a.get("node"))  # type: ignore[arg-type]  # node() 422s on None
            elif body.tool == "orgtree_reallocate":
                result = org.reallocate(body.node, a.get("node"), int(a.get("delta") or 0))  # type: ignore[arg-type]  # node() 422s on None
            elif body.tool == "orgtree_switch_model":
                result = org.switch_model(body.node, a.get("node", ""),
                                          a.get("tier", ""))
            elif body.tool == "orgtree_status":
                status = a.get("status", "working")
                summary = a.get("summary", "")
                # persisted on the node (survives restarts); a new turn moves
                # it to prev_status, so a stale "done" never shows over live
                # work but the history is not erased (gap audit №13)
                org.node(body.node)["last_status"] = {
                    "status": status, "summary": summary,
                    "at": supervisor.now_iso()}
                result = {"recorded": status}
                if status in ("done", "blocked"):
                    parent = org.node(body.node)["parent"]
                    if parent:
                        r = org.post_mail(body.node, parent,
                                          f"[{status.upper()}] {summary}", kind="status")
                        mail_notify(body.org, body.node, parent)
                        drive.append(parent)
                        result["reported_to"] = parent
                        # id + delivered: the chat chip's inline mailbox link
                        # (user spec — ALL agent mail sends carry it)
                        result["delivered"] = parent
                        result["id"] = r.get("id")
                        result["warnings"] = r.get("warnings", [])
                    else:
                        # top-level: the user already gets the agent's own reply
                        # mail — a second [DONE] digest was pure duplication
                        # (user ruling). The status chip is the record.
                        result["reported_to"] = ("status chip only — report your "
                                                 "actual results to the user via "
                                                 "orgtree_message")
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
    if isinstance(result, dict):
        # the bridge is an ADMIN affordance (ceiling spec §1) — an agent has
        # no path to raise the ceiling, so the offer never reaches one
        result.pop("bridge", None)
    hub_changed(body.org)
    return result


_UPLOAD_MAX = 25 * 1048576          # per file
_UPLOAD_KIOSK_TOTAL = 256 * 1048576  # per node uploads dir, kiosk orgs


@app.post("/api/orgs/{slug}/nodes/{nid}/upload")
async def node_upload(slug: str, nid: str, request: Request,
                      name: str = "") -> dict[str, Any]:
    """Attach a file to a chat (user spec 2026-07-31): the raw request body
    lands in the node's scratch under uploads/ — the one folder every agent,
    sandboxed or not, reaches at the same RELATIVE path (its cwd). Reachable
    through the public kiosk gateway too: outside-internet visitors can hand
    files to a kiosk org's agents. No multipart dependency — body is the file."""
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    if org.d.get("storage_blocked"):
        raise HTTPException(413, "the org is over its storage limit — uploads "
                                 "are paused until files are deleted (the "
                                 "block lifts automatically)")
    safe = re.sub(r"[^\w .()+\-]", "_",
                  os.path.basename(name or "upload.bin")).strip(" .") or "upload.bin"
    data = await request.body()
    if not data:
        raise HTTPException(422, "empty upload")
    if len(data) > _UPLOAD_MAX:
        raise HTTPException(413, f"file exceeds the {_UPLOAD_MAX // 1048576} MB "
                                 f"upload cap")
    updir = os.path.join(supervisor.scratch_dir(slug, nid), "uploads")
    os.makedirs(updir, exist_ok=True)
    if supervisor.kiosk_cfg(org):
        total = 0
        for f in os.listdir(updir):
            try:
                total += os.path.getsize(os.path.join(updir, f))
            except OSError:
                pass
        if total + len(data) > _UPLOAD_KIOSK_TOTAL:
            raise HTTPException(413, "this agent's upload space is full — ask "
                                     "it to clean up uploads/ first")
    stem, ext = os.path.splitext(safe)
    final, i = safe, 2
    while os.path.exists(os.path.join(updir, final)):
        final, i = f"{stem}-{i}{ext}", i + 1
    with open(os.path.join(updir, final), "wb") as f:
        f.write(data)
    return {"path": f"uploads/{final}", "bytes": len(data)}


# the return direction (user spec 2026-07-31): uploads/ is user→agent,
# outbox/ is agent→user — orgtree_send_file snapshots a file there and the
# chat renders a download card pointing at the /file endpoint below.
_SENDFILE_MAX = 256 * 1048576


def _agent_send_file(org: Org, nid: str, a: dict[str, Any]) -> dict[str, Any]:
    """Copy a file the NODE can legitimately reach into its outbox/ scratch
    folder. Copy, not reference: the card keeps working after the agent edits
    or deletes the original (re-sending an updated file yields report-2.pdf —
    both chat cards stay honest). Outbox lives in scratch, so kiosk storage
    metering already counts it and org deletion sweeps it."""
    raw = str(a.get("path") or "").strip()
    if not raw:
        raise LedgerError("path is required — the file to deliver")
    slug = org.d["slug"]
    scratch = os.path.realpath(supervisor.scratch_dir(slug, nid))
    p = raw.replace("\\", "/").rstrip("/")
    if sandbox.is_sandboxed(org):
        # sandboxed agents know only container paths — translate the three
        # bind-mounted trees (workspace, scratch, the container home);
        # anything else genuinely does not exist on the host
        cw = sandbox.cpath_workspace(slug)
        cs = f"{sandbox.cpath_data()}/scratch/{slug}"
        ch = "/home/agent"
        host_ws = org.d.get("workspace") or store.workspace_dir(slug)
        if p == cw or p.startswith(cw + "/"):
            src = os.path.normpath(host_ws + p[len(cw):])
        elif p == cs or p.startswith(cs + "/"):
            src = os.path.normpath(store.scratch_root(slug) + p[len(cs):])
        elif p == ch or p.startswith(ch + "/"):
            src = os.path.normpath(sandbox.sandbox_home(slug) + p[len(ch):])
        elif not p.startswith("/"):
            src = os.path.normpath(os.path.join(scratch, p))
        else:
            raise LedgerError(
                f"{raw} exists only inside the container — copy it into your "
                f"working folder or the workspace first, then send that path")
    else:
        src = os.path.normpath(p if os.path.isabs(p)
                               else os.path.join(scratch, p))
    src = os.path.realpath(src)
    # capability honesty (№30's sibling): only trees the node holds are
    # sendable — its own scratch, the org workspace, its granted folders.
    # realpath first, so a symlink cannot smuggle an outside file in.
    roots = [scratch]
    if org.d.get("workspace"):
        roots.append(os.path.realpath(org.d["workspace"]))  # type: ignore[arg-type]  # guard above proves non-None
    for d in org.node(nid)["scope"]["add_dirs"]:
        roots.append(os.path.realpath(d["path"]))
    if sandbox.is_sandboxed(org):
        roots.append(os.path.realpath(sandbox.sandbox_home(slug)))
    if not any(src == r or src.startswith(r + os.sep) for r in roots):
        raise LedgerError(
            f"cannot send {raw} — only files in your working folder, the "
            f"workspace, or a folder you hold are sendable")
    if not os.path.isfile(src):
        raise LedgerError(f"no such file: {raw}")
    size = os.path.getsize(src)
    if size == 0:
        raise LedgerError(f"{raw} is empty — nothing to send")
    if size > _SENDFILE_MAX:
        raise LedgerError(f"{raw} is {size // 1048576} MB — over the "
                          f"{_SENDFILE_MAX // 1048576} MB send cap")
    if org.d.get("storage_blocked"):
        raise LedgerError("the org is over its storage limit — the outbox "
                          "copy is paused; delete files to lift the block, "
                          "then re-send")
    outdir = os.path.join(scratch, "outbox")
    try:
        os.makedirs(outdir, exist_ok=True)
        if src.startswith(os.path.realpath(outdir) + os.sep):
            final = os.path.relpath(src, outdir).replace("\\", "/")
        else:
            safe = re.sub(r"[^\w .()+\-]", "_",
                          os.path.basename(src)).strip(" .") or "file.bin"
            stem, ext = os.path.splitext(safe)
            final, i = safe, 2
            while os.path.exists(os.path.join(outdir, final)):
                final, i = f"{stem}-{i}{ext}", i + 1
            shutil.copy2(src, os.path.join(outdir, final))
    except OSError as e:
        # e.g. the storage block's deny-ACE landing between check and copy
        raise LedgerError(f"outbox copy failed: {e}")
    sent = {"name": os.path.basename(final), "path": f"outbox/{final}",
            "bytes": size}
    note = " ".join(str(a.get("note") or "").split())[:300]
    if note:
        sent["note"] = note
    return {"sent": sent,
            "hint": "delivered — the user sees a download card in your chat; "
                    "announce the file in your reply or report"}


@app.get("/api/orgs/{slug}/nodes/{nid}/file")
def node_file(slug: str, nid: str, path: str = "") -> FileResponse:
    """Raw download of a file in the node's scratch — outbox/ cards, uploads/,
    anything the files tab lists. Org-scoped GET, so the kiosk public gateway
    passes it through: visitors download what agents send back."""
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    base = os.path.realpath(supervisor.scratch_dir(slug, nid))
    full = os.path.realpath(os.path.join(base, path.lstrip("/\\")))
    if not full.startswith(base + os.sep):
        raise HTTPException(422, "path escapes the scratch space")
    if not os.path.isfile(full):
        raise HTTPException(404, f"no such file: {path!r}")
    return FileResponse(full, filename=os.path.basename(full))


# ------------------------------------------------ the org disk (recovery browser)
# The user verdict's built-in file browser over the org's virtual disk — its
# OWN surface, deliberately NOT /api/fs (that is the HOST browser and stays in
# the public deny list). Org-scoped routes, so the kiosk gateway's slug check
# scopes visitors to their own org's disk for free. Reads and deletes go over
# \\wsl.localhost and work with the container STOPPED and the disk 100% FULL
# (drilled, not assumed); enumeration runs INSIDE the distro (9p is too slow).

# engine credential/state files on the disk (subscription auth copies the
# HOST's OAuth credentials into the sandbox home) — never served to visitors
_PUBLIC_DISK_DENY = (".credentials.json", ".claude.json")
_SID_FILE = re.compile(r"^home/\.claude/projects/[^/]+/([0-9a-f-]{36})\.jsonl$")


def _disk_org(slug: str) -> Org:
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    if not org.d.get("disk"):
        raise HTTPException(409, "this org has no virtual disk (not sandboxed, "
                                 "or not yet migrated)")
    return org


def _disk_rel(slug: str, path: str) -> tuple[str, str]:
    """(relative posix path, absolute windows path) — canonicalized, with
    containment ASSERTED before any read/download/unlink. A traversal here
    would reach the host filesystem from a kiosk URL: the worst outcome
    available in this feature, so both a lexical and a realpath check."""
    rel = posixpath.normpath((path or "").replace("\\", "/").strip("/"))
    if not rel or rel == "." or rel == ".." or rel.startswith("../") \
            or rel.startswith("/") or ":" in rel:
        raise HTTPException(422, "path escapes the org disk")
    from . import disk as dsk
    root = dsk.windows_path(slug)
    full = os.path.join(root, *rel.split("/"))
    if os.path.realpath(full) != full and not os.path.realpath(full).startswith(
            os.path.realpath(root) + os.sep):
        raise HTTPException(422, "path escapes the org disk")
    return rel, full


_SEED_ROOTS = ("usr", "var", "etc", "opt", "root", "srv")


def _disk_classify(org: Org, rel: str, public: bool) -> tuple[str, str | None]:
    """The verdict's deletion policy. reclaimable = freely deletable and
    POSITIVELY dead weight; blocked = shown, delete refused, with the reason;
    content = ordinary agent output. System-seed paths are blocked in BOTH
    modes (explorer follow-up): deleting /usr content bricks the container —
    but they are SHOWN, because '4 GB cap, 1.2 GB of it /usr' answers "where
    did my space go" better than any text."""
    if rel.split("/", 1)[0] in _SEED_ROOTS:
        return "blocked", "system seed — the image's own files"
    m = _SID_FILE.match(rel)
    if m:
        sid = m.group(1)
        for nid, n in org.nodes.items():
            if n.get("session_id") == sid:
                if n.get("bearer_state") == "lost":
                    return "reclaimable", (f"lost generation {nid} — never "
                                           f"consultable or rehirable again")
                if n["state"] == "live":
                    return "blocked", (f"live session of {nid} — deleting "
                                       f"breaks its resume")
                if n.get("bearer_state"):
                    return "blocked", (f"knowledge bearer {nid} — deleting "
                                       f"kills its oracle")
                return "blocked", (f"archived node {nid} — deleting breaks "
                                   f"its rehire")
        return "reclaimable", "no node owns this session"
    if rel.rsplit("/", 1)[-1] in _PUBLIC_DISK_DENY:
        if public:
            return "blocked", "engine credential/state file"
        return "content", "engine state file — admin-side only"
    return "content", None


@app.get("/api/orgs/{slug}/disk")
def disk_list(slug: str, request: Request, offset: int = 0,
              limit: int = 200) -> dict[str, Any]:
    """Files by size DESCENDING (the sort that matters when freeing space
    fast) + the live usage readout. Paginated — never the whole tree."""
    org = _disk_org(slug)
    public = bool(_public_slug(request))
    from . import disk as dsk
    try:
        du = dsk.usage(slug, max_age=5.0)
        files = dsk.enumerate_by_size(slug, limit=max(1, min(limit, 500)),
                                      offset=max(0, offset))
    except dsk.DiskError as e:
        raise HTTPException(503, str(e))
    for f in files:
        cls, why = _disk_classify(org, str(f["path"]), public)
        f["class"] = cls
        if why:
            f["reason"] = why
    return {"used": du[0] if du else None, "total": du[1] if du else None,
            "blocked": bool(org.d.get("storage_blocked")),
            "full": bool(org.d.get("storage_full")),
            "files": files, "offset": max(0, offset),
            "limit": max(1, min(limit, 500))}


def _disk_classify_dir(org: Org, rel: str, public: bool,
                       protected: list[str]) -> tuple[str, str | None]:
    """Directory classes for the explorer: seed dirs blocked; a dir whose
    subtree holds protected transcripts is blocked WHOLE (half-deleting a
    tree because a protected file sat in it is the worst outcome here)."""
    if rel.split("/", 1)[0] in _SEED_ROOTS:
        return "blocked", "system seed — the image's own files"
    hits = sum(1 for p in protected if p.startswith(rel + "/"))
    if hits:
        return "blocked", f"contains {hits} protected session transcript(s)"
    return "content", None


def _protected_transcripts(org: Org, slug: str, public: bool) -> list[str]:
    """Transcript files whose deletion is refused — from the cached walk, so
    this costs nothing beyond the walk both views already share."""
    from . import disk as dsk
    return [p for p, _sz in dsk.subtree_files(slug, "home")
            if _SID_FILE.match(p)
            and _disk_classify(org, p, public)[0] == "blocked"]


@app.get("/api/orgs/{slug}/disk/dir")
def disk_dir(slug: str, request: Request, path: str = "") -> dict[str, Any]:
    """Explorer mode: ONE directory level, entries intermixed by size
    descending (deliberate deviation from folders-first — the view exists
    for size triage). Served from the cached single walk; works with the
    container stopped, same as everything on this surface."""
    org = _disk_org(slug)
    public = bool(_public_slug(request))
    rel = ""
    if path.strip("/"):
        rel, _full = _disk_rel(slug, path)
    from . import disk as dsk
    try:
        entries = dsk.list_dir(slug, rel)
        protected = _protected_transcripts(org, slug, public)
        du = dsk.usage(slug, max_age=5.0)
    except dsk.DiskError as e:
        raise HTTPException(503, str(e))
    for e in entries:
        p = str(e["path"])
        cls, why = (_disk_classify_dir(org, p, public, protected)
                    if e["dir"] else _disk_classify(org, p, public))
        e["class"] = cls
        if why:
            e["reason"] = why
    return {"path": rel, "entries": entries,
            "used": du[0] if du else None, "total": du[1] if du else None,
            "blocked": bool(org.d.get("storage_blocked")),
            "full": bool(org.d.get("storage_full"))}


@app.get("/api/orgs/{slug}/disk/file")
def disk_file(slug: str, request: Request, path: str = "") -> FileResponse:
    """Streaming download (FileResponse streams — a multi-GB file is never
    buffered). Visitors get everything except the engine credential files."""
    org = _disk_org(slug)
    rel, full = _disk_rel(slug, path)
    cls, why = _disk_classify(org, rel, bool(_public_slug(request)))
    if cls == "blocked" and rel.rsplit("/", 1)[-1] in _PUBLIC_DISK_DENY:
        raise HTTPException(403, why or "not served publicly")
    if not os.path.isfile(full):
        raise HTTPException(404, f"no such file: {rel!r}")
    return FileResponse(full, filename=os.path.basename(full))


class DiskDelete(BaseModel):
    paths: list[str]


@app.post("/api/orgs/{slug}/disk/delete")
def disk_delete(slug: str, body: DiskDelete, request: Request) -> dict[str, Any]:
    """Multi-select delete. Classification is enforced HERE, server-side —
    the UI's greying is presentation. Works at 100% full (unlink needs no
    free space on ext4 — drilled). Ends with the recovery loop: re-measure,
    and the existing storage_check clear path lifts the block/alert."""
    org = _disk_org(slug)
    public = bool(_public_slug(request))
    from . import disk as dsk
    results: list[dict[str, Any]] = []
    for p in body.paths[:500]:
        try:
            rel, full = _disk_rel(slug, p)
        except HTTPException as e:
            results.append({"path": p, "ok": False, "error": e.detail})
            continue
        if os.path.isdir(full):
            # directory delete (explorer mode): the class rules apply to the
            # WHOLE subtree and the operation is all-or-nothing — a protected
            # file anywhere in it refuses everything, never a partial delete
            seed_cls, seed_why = _disk_classify_dir(org, rel, public, [])
            if seed_cls == "blocked":
                results.append({"path": rel, "ok": False, "error": seed_why})
                continue
            subs = dsk.subtree_files(slug, rel, max_age=0.0)
            bad = [(sp, _disk_classify(org, sp, public)[1]) for sp, _s in subs
                   if _disk_classify(org, sp, public)[0] == "blocked"]
            if bad:
                results.append({"path": rel, "ok": False,
                                "error": f"subtree holds {len(bad)} protected "
                                         f"file(s) — first: {bad[0][1]}"})
                continue
            n_files, n_bytes, err = 0, 0, None
            try:
                for base, dirs, files in os.walk(full, topdown=False):
                    for f in files:
                        fp = os.path.join(base, f)
                        n_bytes += os.path.getsize(fp)
                        os.unlink(fp)
                        n_files += 1
                    for d in dirs:
                        os.rmdir(os.path.join(base, d))
                os.rmdir(full)
            except OSError as e:
                err = str(e)
            results.append({"path": rel, "ok": err is None,
                            "files": n_files, "bytes": n_bytes,
                            **({"error": err} if err else {})})
            continue
        cls, why = _disk_classify(org, rel, public)
        if cls == "blocked":
            results.append({"path": rel, "ok": False, "error": why})
            continue
        try:
            os.unlink(full)
            results.append({"path": rel, "ok": True})
        except OSError as e:
            results.append({"path": rel, "ok": False, "error": str(e)})
    dsk.invalidate(slug)
    supervisor.storage_check(slug)          # may auto-clear blocked/full
    du = dsk.usage(slug, max_age=0.0)
    org = store.load_org(slug)
    return {"results": results,
            "used": du[0] if du else None, "total": du[1] if du else None,
            "blocked": bool(org.d.get("storage_blocked")),
            "full": bool(org.d.get("storage_full"))}


class DiskGrow(BaseModel):
    size_mb: int


@app.post("/api/orgs/{slug}/disk/grow")
def disk_grow(slug: str, body: DiskGrow, request: Request) -> dict[str, Any]:
    """Online grow (extend + resize2fs, no container stop). ADMIN-side only:
    growing spends host disk. Shrink is deliberately absent for now — it is
    offline and refuses below current usage (stage 5)."""
    if _public_slug(request):
        raise HTTPException(403, "admin side only")
    org = _disk_org(slug)
    from . import disk as dsk
    cur = int((org.d.get("disk") or {}).get("size_mb") or 0)
    if body.size_mb <= cur:
        raise HTTPException(422, f"grow only: the disk is {cur} MB (shrink "
                                 f"is an offline operation — not offered yet)")
    try:
        dsk.grow(slug, int(body.size_mb))
    except dsk.DiskError as e:
        raise HTTPException(503, str(e))
    with store.DOC_LOCK:
        o2 = store.load_org(slug)
        d = dict(o2.d.get("disk") or {})
        d["size_mb"] = int(body.size_mb)
        o2.d["disk"] = d
        store.save_org(o2)
    supervisor.storage_check(slug)          # a grow may clear blocked/full
    du = dsk.usage(slug, max_age=0.0)
    return {"size_mb": int(body.size_mb),
            "used": du[0] if du else None, "total": du[1] if du else None}


@app.get("/api/orgs/{slug}/nodes/{nid}/chat")
def node_chat(slug: str, nid: str, last: int = 300) -> dict[str, Any]:
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    out = supervisor.read_chat(org, nid, last=max(1, min(last, 1000)))
    # queued = the mail box PLUS the delivery journal's in-flight batches —
    # a message steered mid-task drains the box instantly, and during a long
    # tool call it showed NOWHERE (user bug 2026-07-31)
    pending = sorted(supervisor.delivering_mail(org, nid)
                     + list((org.d.get("mail") or {}).get(nid, [])),
                     key=lambda m: m.get("at") or "")
    out["mail_pending"] = len(pending)
    # parity №11: the pending bubble renders from the DURABLE server copy —
    # orgtree's queue is better than Claude Code's and presented as flimsier
    out["pending_mail"] = [{"id": m.get("id"), "from": m["from"],
                            "body": m["body"][:2000], "at": m["at"],
                            **({"delivering": True} if m.get("delivering")
                               else {}),
                            **({"attachments": m["attachments"]}  # type: ignore[typeddict-item]  # guard proves the key
                               if m.get("attachments") else {})}
                           for m in pending[-20:]]
    return out


@app.delete("/api/orgs/{slug}/nodes/{nid}/mail/{mid}")
async def node_mail_retract(slug: str, nid: str, mid: str) -> dict[str, Any]:
    """Parity №17: retract one UNDRAINED mail entry — the only correction
    channel for a wrong send, since delivery deliberately never interrupts."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            org.node(nid)
        except LedgerError as e:
            raise HTTPException(404, str(e))
        box = (org.d.get("mail") or {}).get(nid) or []
        kept = [m for m in box if m.get("id") != mid]
        if len(kept) == len(box):
            raise HTTPException(404, "no such pending mail — it may already "
                                     "have been delivered")
        org.d["mail"][nid] = kept  # type: ignore[typeddict-item]  # box non-empty ⇒ the key exists
        # mirror the retraction into the archive so the record stays honest
        log = (org.d.get("mail_log") or {}).get(nid) or []
        for m in log:
            if m.get("id") == mid:
                m["retracted"] = True
        store.save_org(org)
    await hub.changed(slug)
    return {"retracted": mid}


@app.get("/api/orgs/{slug}/nodes/{nid}/toolimg/{tool_use_id}")
def node_tool_image(slug: str, nid: str, tool_use_id: str, idx: int = 0) -> Response:
    """Parity №9 (image clause): serve a tool result's image by tool_use_id —
    a separate bounded fetch, never base64 inlined into the 5 s chat poll."""
    try:
        org = store.load_org(slug)
        n = org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    tpath = supervisor.transcript_path(n["session_id"],
                                       supervisor._transcript_root(org))
    if not tpath:
        raise HTTPException(404, "no transcript")
    import base64
    for line in open(tpath, encoding="utf-8", errors="replace"):
        if tool_use_id not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_result"
                    and b.get("tool_use_id") == tool_use_id):
                continue
            imgs = [x for x in (b.get("content") or [])
                    if isinstance(x, dict) and x.get("type") == "image"]
            if idx < len(imgs):
                src = imgs[idx].get("source") or {}
                if src.get("type") == "base64" and src.get("data"):
                    from fastapi.responses import Response
                    return Response(
                        content=base64.b64decode(src["data"]),
                        media_type=src.get("media_type", "image/png"))
    raise HTTPException(404, "no image on that tool result")


@app.get("/api/orgs/{slug}/nodes/{nid}/inbox")
def node_inbox(slug: str, nid: str) -> dict[str, Any]:
    """The node's OWN mailbox (user ruling: separate from the events/history
    view): mail still waiting for its next turn, plus recently delivered mail
    with full bodies (the event log keeps only a gist)."""
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    waiting = sorted(supervisor.delivering_mail(org, nid)
                     + list((org.d.get("mail") or {}).get(nid, [])),
                     key=lambda m: m.get("at") or "")
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
def org_events(slug: str, since: int = 0) -> dict[str, Any]:
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
    effort: str | None = None     # hire — thinking effort, applied WITH the hire
    delta: int | None = None      # reallocate
    new_parent: str | None = None  # promote / demote
    dir: str | None = None        # revoke_dir
    # ceiling spec §1: the one-action bridge — re-send the same op with this
    # set and an over-ceiling admin grant raises the ceiling to fit (logged,
    # named, never silent). Ignored for visitors: no legal raise path exists.
    raise_ceiling: bool = False


@app.post("/api/orgs/{slug}/ops")
def org_op(slug: str, body: Op, request: Request) -> dict[str, Any]:
    pub = bool(_public_slug(request))
    with store.DOC_LOCK:
        result = _org_op_locked(slug, body, allow_raise=not pub)
    if pub and isinstance(result, dict):
        # the bridge is the ADMIN affordance — a visitor has no legal path to
        # raise the ceiling, so the offer must not dangle
        result.pop("bridge", None)
    # rehire with a waiting mailbox: the mail queued while archived finally
    # gets acted on (user ruling) — drive outside the doc lock
    for t in result.pop("drive", []) if isinstance(result, dict) else []:
        supervisor.send_message(
            slug, t,
            "(orgtree) Mail above arrived while you were archived and waited "
            "for you — you are live again; handle it as appropriate.")
    return result


def _org_op_locked(slug: str, body: Op, allow_raise: bool = False) -> dict[str, Any]:
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    # ceiling spec §1, computed in exactly one place:
    # raise_ceiling = not public and (kiosk.auto_raise or the explicit ask)
    rc = allow_raise and (bool((org.d.get("kiosk") or {}).get("auto_raise"))
                          or body.raise_ceiling)
    try:
        if body.op == "hire":
            if body.tier is None or body.name is None:
                raise LedgerError("hire needs tier and name")
            result = org.hire(body.actor, body.parent, body.tier,
                              body.grant or 0, body.name, body.add_dirs,
                              tools=body.tools, org_visibility=body.org_visibility,
                              charter=body.charter, raise_ceiling=rc)
            if body.effort:
                # applied WITH the hire, atomically (same save): the draft
                # gear's effort used to ride a separate /scope call that the
                # kiosk gateway 403s — a control that could never succeed
                org.set_scope(body.actor, result["node"], effort=body.effort)
        # body.node is Optional on the wire (hire has none); the target ops
        # take str because Org.node(None) already raises LedgerError → 422,
        # hence the arg-type ignores below rather than a behavior-changing check
        elif body.op == "retire":
            result = org.retire(body.actor, body.node)  # type: ignore[arg-type]
        elif body.op == "rehire":
            result = org.rehire(body.actor, body.node, body.grant, tier=body.tier,  # type: ignore[arg-type]
                                raise_ceiling=rc)
        elif body.op == "dissolve":
            result = org.dissolve(body.actor, body.node)  # type: ignore[arg-type]
        elif body.op == "delete":
            result = org.delete(body.actor, body.node)  # type: ignore[arg-type]
            supervisor.forget(slug, result["deleted"])
        elif body.op == "reallocate":
            if body.delta is None:
                raise LedgerError("reallocate needs delta")
            result = org.reallocate(body.actor, body.node, body.delta)  # type: ignore[arg-type]
        elif body.op == "switch_model":
            if body.tier is None:
                raise LedgerError("switch_model needs tier")
            result = org.switch_model(body.actor, body.node, body.tier)  # type: ignore[arg-type]
        elif body.op == "promote":
            result = org.promote(body.actor, body.node, body.new_parent)  # type: ignore[arg-type]
        elif body.op == "demote":
            if body.new_parent is None:
                raise LedgerError("demote needs new_parent")
            result = org.demote(body.actor, body.node, body.new_parent)  # type: ignore[arg-type]
        elif body.op == "move":
            result = org.move(body.actor, body.node, body.new_parent)  # type: ignore[arg-type]
        elif body.op == "reseed":
            result = org.reseed(body.actor, body.node, str(uuid.uuid4()))  # type: ignore[arg-type]
        elif body.op == "revoke_dir":
            if body.dir is None:
                raise LedgerError("revoke_dir needs dir")
            result = org.revoke_dir(body.actor, body.node, body.dir)  # type: ignore[arg-type]
        else:
            raise LedgerError(f"unknown op {body.op!r}")
        _kiosk_cap_check(org)
    except LedgerError as e:
        raise HTTPException(422, str(e))
    store.save_org(org)
    hub_changed(slug)
    return result


@app.websocket("/api/orgs/{slug}/ws")
async def org_ws(ws: WebSocket, slug: str) -> None:
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
    def spa(path: str) -> FileResponse:
        full = os.path.normpath(os.path.join(FRONTEND_DIST, path))
        if path and full.startswith(FRONTEND_DIST + os.sep) \
                and os.path.isfile(full):
            return FileResponse(full)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))


def main() -> None:
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

    async def serve_all() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(serve_all())


if __name__ == "__main__":
    main()
