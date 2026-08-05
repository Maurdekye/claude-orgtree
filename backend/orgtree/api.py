# pyright: strict, reportPrivateUsage=false, reportUnnecessaryIsInstance=false
# (the two relaxations restate pyrightconfig.json's project-wide rulings —
#  cross-module _helpers are deliberate, runtime isinstance guards stay —
#  which a bare file-level strict comment would otherwise override)
"""FastAPI layer — the UI's backend and (later) the supervisor's host process.

Run:  python -m orgtree.api          (serves API + built frontend on one port)
Dev:  uvicorn orgtree.api:app --reload --port 7360   (vite dev server proxies /api)

v0.1 scope: org CRUD, tree view, the ledger ops, an event tail, and a WebSocket that
pings "changed" after every successful op so the UI refreshes. Session spawning is v0.2.
"""

from __future__ import annotations

import asyncio
import importlib.util
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
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from . import net, sandbox, store, subproxy, supervisor
from .ledger import LedgerError, Org, USER, VIS_LEVELS, norm_dirs, norm_tools

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import httpx
    # aliased: `Scope` is taken by the pydantic body model of the same name
    from starlette.types import ASGIApp, Receive, Scope as ASGIScope, Send

    # aliased: `KioskCfg` is taken by the pydantic body model of the same name
    from .schema import DirGrant, KioskCfg as KioskDoc, MailEntry, UserMailEntry

app = FastAPI(title="orgtree", version="1.0.0")

#: THIS PROCESS's identity — a fresh value on every start.
#:
#: A redeploy replaces both halves of the app, but only the server half
#: restarts: every browser already open keeps running the bundle it loaded,
#: against a backend that may have changed its payloads underneath it. The
#: symptom is a UI that looks fine and is subtly wrong, and the fix has always
#: been "tell the user to hit refresh". Stamping every response with this lets
#: the client notice the change itself (see `noteInstance` in api.ts) — no new
#: endpoint, no extra request, and no poller: the heartbeats that already run
#: carry it.
INSTANCE = secrets.token_hex(8)


class InstanceStamp:
    """Adds `X-Orgtree-Instance` to every HTTP response.

    Pure ASGI rather than `@app.middleware("http")`: Starlette's
    BaseHTTPMiddleware re-wraps the response body in its own StreamingResponse,
    and this sits in front of multi-GB virtual-disk downloads. Rewriting one
    header on the `http.response.start` message touches nothing else."""

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: ASGIScope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)

        # /api/ responses also get no-store: the restart detector compares the
        # instance stamp per response, and a heuristically-cached GET replaying
        # an OLD instance id after a reload would re-trigger the reload — a
        # loop. One header closes it; static assets keep their own caching.
        api = scope.get("path", "").startswith("/api/")

        async def _send(msg: Any) -> None:
            if msg["type"] == "http.response.start":
                msg = dict(msg)
                msg["headers"] = [*(msg.get("headers") or []),
                                  (b"x-orgtree-instance", INSTANCE.encode()),
                                  *([(b"cache-control", b"no-store")] if api else [])]
            await send(msg)
        await self.inner(scope, receive, _send)


# on the APP, so all three listeners (admin, kiosk, bridge) inherit it — they
# are gateways wrapped around this same object
app.add_middleware(InstanceStamp)


@app.exception_handler(RequestValidationError)
async def _validation_error(  # type: ignore[unused-function]  # registered by the decorator
        _request: Request, exc: RequestValidationError) -> Response:
    """FastAPI's default 422 echoes the offending value back as `input`, RAW.

    JSON may carry a lone surrogate (`"\\ud800"`), Python's decoder happily
    produces it, and the echo then kills the UTF-8 encode of the RESPONSE —
    turning a 422 into an uncaught UnicodeEncodeError, i.e. a 500, on every
    body-taking endpoint at once. (Handler-authored messages are safe: they
    interpolate with !r, which escapes it.)

    Same payload shape as the default handler — only the strings are made
    encodable, so nothing that reads a 422 today sees a difference."""
    def fix(v: Any) -> Any:
        if isinstance(v, str):
            return v.encode("utf-8", "replace").decode("utf-8")
        if isinstance(v, list):
            return [fix(x) for x in cast("list[Any]", v)]
        if isinstance(v, dict):
            return {k: fix(x) for k, x in cast("dict[str, Any]", v).items()}
        return v
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422,
                        content={"detail": fix(jsonable_encoder(exc.errors()))})


def _encodable(v: Any) -> Any:
    """Replace UNPAIRED SURROGATES anywhere in a decoded request body.

    ☠ This one is a persistent denial of service, not a cosmetic bug. JSON may
    contain `"\\ud800"`; Python's decoder accepts it into a str; `json.dump`
    writes it straight back out as an escape (ensure_ascii, so the SAVE
    succeeds); and every response that later includes that string dies in
    pydantic's UTF-8 serializer. One kiosk message body was enough to make
    GET /api/orgs/<slug>, /events, /chat and /inbox answer 500 for that org
    FOREVER — the poison is on disk and nothing removes it.

    So it is scrubbed at the only door it can arrive through: the request
    body, recursively, before validation — which covers free-form dicts
    (`args`, `max_scope`, `tools`) as well as declared string fields."""
    if isinstance(v, str):
        try:
            v.encode("utf-8")
            return v
        except UnicodeEncodeError:
            return v.encode("utf-8", "replace").decode("utf-8")
    if isinstance(v, list):
        return [_encodable(x) for x in cast("list[Any]", v)]
    if isinstance(v, dict):
        return {_encodable(k): _encodable(x)
                for k, x in cast("dict[Any, Any]", v).items()}
    return v


class Body(BaseModel):
    """Base for every request-body model — see _encodable."""

    @model_validator(mode="before")
    @classmethod
    def _scrub(cls, data: Any) -> Any:
        return _encodable(data)


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
    # FastAPI's own routes sit OUTSIDE /api, so the "not /api ⇒ it's the SPA"
    # rule handed them to visitors: /k/<token>/openapi.json served the
    # complete 51 KB schema of every frozen admin endpoint and body model,
    # and /docs + /redoc served a working console for firing at them.
    if rest.rstrip("/") in ("/openapi.json", "/docs", "/redoc",
                            "/docs/oauth2-redirect"):
        return 404, "not found"
    if not rest.startswith("/api"):
        return None                              # the SPA itself
    if rest == "/api/orgs" and method == "GET":
        return None                              # handler filters to this org
    frozen_config = (
        (method == "POST" and rest == "/api/orgs")           # create org
        # ⚠ NOT a blanket `startswith("/api/orgs/")`: that also froze
        # DELETE …/nodes/…/mail/<id>, the mail-retraction button the visitor
        # UI renders unconditionally (desk.tsx) — a control that could only
        # ever 403. Freeze the org-delete route itself, which is the one this
        # clause was ever about.
        or (method == "DELETE"
            and re.fullmatch(r"/api/orgs/[^/]+", rest) is not None)
        or rest.endswith("/settings")                        # org settings
        # /scope is OPEN (ceiling spec §2): visitors retool freely WITHIN the
        # kiosk permission ceiling — the ledger clamps, never a 403 here
        or rest.endswith("/kiosk")                           # kiosk caps/token/ceiling
        # the PostToolUse steer fetch: an agent-process path, authorised by
        # loopback or the bridge secret, never by a browser (the frontend has
        # no call site for it). Reachable from the kiosk it POPPED the node's
        # pending mid-task mail — reading it AND destroying the delivery.
        or rest.endswith("/steer")
        or rest == "/api/fs"                                 # filesystem browse
        or (method == "PUT" and rest.endswith("/orgmd"))     # org.md edits
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


# A free-form `dict[str, Any]` off the wire (max_scope, tools, add_dirs) hits
# ledger normalizers that assume the documented SHAPE — `{"tools": 5}` came
# back out as an AttributeError, i.e. a 500 rather than a 422. Pydantic can't
# help: the field really is Any. Catch the shape errors where the untyped dict
# crosses into the ledger, and only there.
_BAD_SHAPE = (TypeError, AttributeError, ValueError, KeyError, IndexError)


def _no_nul(path: str) -> str:
    """Refuse an embedded NUL before it reaches os.path.

    Every path-taking endpoint funnels into `os.path.realpath`, and on Windows
    that raises `ValueError: embedded null character` from inside ntpath —
    below every `except OSError` in this file, so it surfaced as a bare 500.
    One `?path=%00` did it on /scratch, /file, /disk/file, /disk/delete and the
    message-attachment stager. A refusal is the contract; a 500 is not."""
    if "\x00" in path:
        raise HTTPException(422, "path contains a null byte")
    return path


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
        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
        for hk, hv in raw_headers:
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
            _LAN_IP = s.getsockname()[0]  # type: ignore[constant-redefinition]  # lazily-computed cache, not a constant
            s.close()
        except OSError:
            _LAN_IP = "127.0.0.1"  # type: ignore[constant-redefinition]  # lazily-computed cache, not a constant
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


@app.on_event("startup")  # type: ignore[deprecated]  # migrating to lifespan is a runtime change (D-079: inert wave)
async def _wire_notify() -> None:  # type: ignore[unused-function]  # registered by the decorator
    global mail_notify, _LOOP
    loop = asyncio.get_running_loop()
    _LOOP = loop  # type: ignore[constant-redefinition]  # captured-at-startup cell, not a constant
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
    supervisor.mail_spark = _mail
    # G2: every persisted change announces itself, from wherever it was made —
    # an endpoint, an agent's MCP call, the supervisor's own turn bookkeeping.
    # The explicit hub_changed() calls left at a few endpoints are now
    # redundant but harmless (they coalesce into the same window).
    store.on_save = hub_changed
    supervisor.start_auto_resume_loop()
    # storage watchdog (user spec): catches single long tool calls —
    # clones/builds/downloads — that balloon past the limit MID-CALL
    supervisor.start_storage_watchdog()
    # chatq external bridge (user vision): every org is an addressable chatq
    # peer — external Claude Code sessions message it like any other chat
    for o in store.list_orgs():
        supervisor.chatq_register_org(o["slug"])
    supervisor.start_chatq_bridge()
    # F-06: the mail-hub client — connectivity TRANSITIONS broadcast an org
    # `changed` so the UI's status dots are realtime without polling
    net.notify_changed = hub_changed
    net.start_net_client()
    # compose-stage sweep: in-memory ids died with the last process, so every
    # file already in <data>/net_stage is unreachable — remove them all
    _prune_stage(max_age_s=0.0)
    # §9.2: warn EARLY when the subscription's refresh token nears expiry —
    # an unattended box discovers an auth lapse as a pile of failed turns
    supervisor.start_cred_watcher()
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
        dead: list[WebSocket] = []
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


_BCAST_COALESCE = 0.4      # seconds; see hub_changed
_bcast_pending: set[str] = set()
_bcast_lock = threading.Lock()


def hub_changed(slug: str) -> None:
    """Schedule a 'changed' broadcast from any thread (№22: the heavyweight
    endpoints are plain `def` now — they run in the threadpool and can't
    await).

    G2: this is now driven by `store.save_org` itself rather than by ~30
    endpoints remembering to call it, which means it fires far more often —
    a single turn writes the doc many times (mail drain, budget, status,
    journal). So it COALESCES: the first save opens a 0.4 s window and every
    save inside that window rides the same broadcast. The clients refetch a
    ~4 KB tree, so the worst case is ~2.5 refetches/second/org under sustained
    writes, and the common case is one broadcast per burst.
    """
    if _LOOP is None:
        return
    with _bcast_lock:
        if slug in _bcast_pending:
            return                      # a broadcast is already coming
        _bcast_pending.add(slug)

    async def _fire() -> None:
        await asyncio.sleep(_BCAST_COALESCE)
        with _bcast_lock:
            _bcast_pending.discard(slug)
        await hub.changed(slug)

    asyncio.run_coroutine_threadsafe(_fire(), _LOOP)


# ---------------------------------------------------------------------- orgs
class KioskSpec(Body):
    credits: int = 30                 # top-level holdings cap (user ruling)
    spend_limit: float = 50.0         # USD hard limit (user ruling 2026-07-31)
    storage_limit_mb: int = 4096      # sandboxed: the org DISK size (4096 MB
                                      # floor, user ruling 2026-08-01);
                                      # unsandboxed: loose workspace+scratch cap
    sandbox: bool = True              # run agent turns in a Docker container
    # ceiling spec §3: the permission ceiling is visible/editable AT CREATION —
    # the default is permissive (mcp "*", user ruling), so narrowing it must
    # be a conscious act rather than something discovered later
    max_scope: dict[str, Any] | None = None   # None = the default ceiling
    auto_raise: bool = False          # admin over-ceiling grants auto-raise it
    # auth is NOT configurable (user ruling): every sandbox uses the proxied
    # subscription — the host attaches the token, the sandbox never sees it


class OrgCreate(Body):
    name: str
    dirs: list[str] = []
    permission_mode: str = "acceptEdits"
    kiosk: KioskSpec | None = None    # present = the org is BORN a kiosk
    sandbox: bool = False             # normal orgs may sandbox too (user ruling)
    disk_mb: int | None = None        # sandboxed non-kiosk orgs: virtual-disk
                                      # size (≥4096; None = DISK_MB fallback)
    net_autoconnect: bool = True      # F-06: join the LOCAL mail hub (creation
                                      # checkbox; not gated on hub detection)
    net_hubs: list[str] = []          # F-06: remote hub addresses, typed
                                      # explicitly (names discovered on connect)


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
        row = {**o, "cost_usd_total": org.cost_total(),
               # F-09: agents with a running turn. Deliberately absent from the
               # public/kiosk branch above — visitors don't see how busy an org is.
               "working": supervisor.working_count(o["slug"])}
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
    # sandboxed orgs ride a fixed-size virtual disk with a 4096 MB minimum
    # (the system seed and transcripts count inside the cap) — refuse smaller
    # limits at creation instead of silently flooring them at migration
    # (user ruling 2026-08-01)
    if body.kiosk is not None and body.kiosk.sandbox \
            and int(body.kiosk.storage_limit_mb) < 4096:
        raise HTTPException(422, "sandboxed orgs ride a fixed-size disk with "
                                 "a 4096 MB minimum — set storage to at "
                                 "least 4096 MB")
    if body.kiosk is None and body.sandbox and body.disk_mb is not None \
            and int(body.disk_mb) < 4096:
        raise HTTPException(422, "sandboxed orgs ride a fixed-size disk with "
                                 "a 4096 MB minimum — set disk_mb to at "
                                 "least 4096")
    try:
        org = store.create_org(body.name, body.dirs, body.permission_mode)
    except LedgerError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        # create_org mkdirs the workspace before the ledger ever sees the
        # name; a name the host filesystem refuses (too long, a reserved
        # device name, an unwritable data root) surfaced as a bare 500
        raise HTTPException(422, f"could not create the org's workspace: {e}")
    # global default org settings (user spec): every new org is born with them.
    # net_hub_address is CONFIG for the local hub entry, not an org-doc key —
    # popped here and translated below, never written raw into the doc.
    dflt = load_org_defaults()
    local_hub_addr = str(dflt.pop("net_hub_address", "") or "") \
        or net.DEFAULT_HUB_ADDRESS
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
            except (LedgerError, *_BAD_SHAPE) as e:
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
            o.d["sandbox"] = {"enabled": True, "secret": secrets.token_hex(16),
                              **({"limit_mb": int(body.disk_mb)}
                                 if body.disk_mb is not None else {})}
            store.save_org(o)
            sandbox.warm(o)
        _bridge_cache["at"] = 0.0
    if body.kiosk is None:
        # F-06: non-kiosk orgs mint their permanent network identity at birth
        # (kiosks are sealed and mint none). The hub list starts with the
        # local entry (unless opted out) plus any typed remote addresses.
        with store.DOC_LOCK:
            o = store.load_org(org.d["slug"])
            net.mint_identity(o)
            o.d["net_autoconnect"] = bool(body.net_autoconnect)
            o.d["net_hubs"] = net.hub_entries(
                body.net_autoconnect, body.net_hubs, local_hub_addr)
            store.save_org(o)
    return {"slug": org.d["slug"]}


@app.delete("/api/orgs/{slug}")
def orgs_delete(slug: str) -> dict[str, Any]:
    try:
        store.delete_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    # state-only purge: delete is a reversible rename, so scratch dirs stay
    # (a restore brings the files back) but runtime state must die with the
    # org or a restore resurrects phantom busy/queued agents
    supervisor.forget_state(slug)
    supervisor.remote_reap(slug)     # FR-01: no server outlives its org
    sandbox.remove(slug)            # container down; files stay (like scratch)
    supervisor.chatq_deregister_org(slug)
    return {"ok": True}


@app.get("/api/net/probe")
def net_probe(request: Request, address: str = "") -> dict[str, Any]:
    """F-06: is a hub reachable at this address RIGHT NOW? A creation-form
    HINT only — the auto-connect checkbox never gates on it (a hub that is
    down at config time still gets configured; the daemon retries forever)."""
    if _public_slug(request):
        raise HTTPException(404, "not found")
    addr = address.strip() or net.DEFAULT_HUB_ADDRESS
    try:
        import httpx
        r = httpx.get(f"{addr}/healthz", timeout=2.0)
        if r.status_code == 200:
            d = cast("dict[str, Any]", r.json())
            return {"ok": True, "name": d.get("name")}
    except Exception:                                            # noqa: BLE001
        pass
    return {"ok": False}


@app.get("/api/orgs/{slug}/net")
def org_net(slug: str, request: Request) -> dict[str, Any]:
    """F-06: the org's network identity — the ONE place the secret is
    returned (loopback admin listener only, like the kiosk token). The
    settings panel's reveal/export reads this; the public gateway never
    reaches it. Kiosks have no identity by design."""
    if _public_slug(request):
        raise HTTPException(404, "not found")
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    if org.d.get("kiosk"):
        return {"identity": None, "hubs": [], "autoconnect": False}
    if not org.d.get("net_identity") or "net_hubs" not in org.d:
        # existing (pre-F-06) orgs backfill lazily on first reveal — the FULL
        # default config, not just the identity: identity without a hub list
        # is an org that silently never joins while the panel says autoconnect
        # is on (researcher finding 2026-08-05). Mirrors the chatq precedent
        # (existing orgs register automatically; opt-out lives in settings).
        with store.DOC_LOCK:
            org = store.load_org(slug)
            net.mint_identity(org)
            if "net_hubs" not in org.d:
                addr = str(load_org_defaults().get("net_hub_address") or "") \
                    or net.DEFAULT_HUB_ADDRESS
                org.d.setdefault("net_autoconnect", True)
                org.d["net_hubs"] = net.hub_entries(
                    bool(org.d.get("net_autoconnect", True)), [], addr)
            store.save_org(org)
    return {"identity": org.d.get("net_identity"),
            "hubs": org.d.get("net_hubs") or [],
            "autoconnect": bool(org.d.get("net_autoconnect", True))}


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
        # G4: what the agent is doing RIGHT NOW, derived from the live tail the
        # supervisor already keeps. The client used to accumulate this itself
        # from the websocket (`activity`, keyed by node, cleared on turn_done),
        # which meant a missed `turn_done` stranded an indicator until the
        # socket reconnected — a second copy of a fact the server already had.
        # Derived here per request, stored nowhere: the newest row wins, and
        # `busy` (above) is what decides whether it renders at all.
        live = cast("list[dict[str, Any]]", st.get("live") or [])
        last = live[-1] if live else {}
        kind = last.get("kind")
        node["activity"] = (
            {"phase": "tool", "tool": last.get("text")} if kind == "tool"
            else {"phase": "writing"} if kind == "text"
            else {"phase": "thinking"})
        # (occupancy / context_window were re-read from the supervisor's
        # in-memory copy here, on the belief that it was fresher. It was not:
        # _after_turn wrote both in the same block, so the mirror could only
        # ever agree or be stale. The doc projection is the answer.)
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
            # the yellow divergence (pending shrink): requested vs actual
            "pending_mb": (org.d.get("disk") or {}).get("pending_size_mb"),
        }
    # F-06: hub config + live connectivity for the status surfaces — never
    # the secret (status_block guarantees it); None for kiosks
    tree["net"] = net.status_block(cast("dict[str, Any]", org.d))
    tree["headless"] = bool(org.d.get("headless"))
    # WHETHER a key is set, never the key (settings needs the fact)
    tree["api_key_set"] = bool(org.d.get("api_key"))
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
    # F-06: hub addresses + rosters are the operator's network topology —
    # visitors get none of it (kiosks carry no identity anyway, belt+braces)
    tree.pop("net", None)
    if tree.get("workspace"):
        tree["workspace"] = base(tree["workspace"])
    dirs: list[dict[str, Any]] = tree.get("dirs") or []
    tree["dirs"] = [{**d, "path": base(d.get("path", ""))} for d in dirs]
    if isinstance(tree.get("kiosk"), dict):
        # the ceiling's add_dirs are host paths; visitors see clamp warnings
        # naming the ceiling, never the ceiling itself
        tree["kiosk"].pop("max_scope", None)
        tree["kiosk"].pop("auto_raise", None)
        tree["kiosk"].pop("share_url", None)

    def walk(n: dict[str, Any]) -> None:
        n.pop("session_id", None)
        sc: dict[str, Any] = n.get("scope") or {}
        if sc.get("add_dirs"):
            sc["add_dirs"] = [{**d, "path": base(d.get("path", ""))}
                              for d in sc["add_dirs"]]
        if n.get("last_error"):
            n["last_error"] = _WINPATH.sub("<path>", str(n["last_error"]))
        # the other two ENGINE-generated strings on a node. `frozen.error` is
        # a raw CLI/limit error and `last_denials[].arg` is the argument of a
        # headless auto-denied tool call — i.e. routinely a host file path.
        # Both rode the tree payload unscrubbed while last_error beside them
        # was cleaned (measured: a denial arg leaked E:\… and a freeze error
        # leaked the operator's username).
        fz: dict[str, Any] = n.get("frozen") or {}
        if fz.get("error"):
            fz["error"] = _WINPATH.sub("<path>", str(fz["error"]))
        dens: list[Any] = n.get("last_denials") or []
        for dn in dens:
            if isinstance(dn, dict) and cast("dict[str, Any]", dn).get("arg"):
                d2 = cast("dict[str, Any]", dn)
                d2["arg"] = _WINPATH.sub("<path>", str(d2["arg"]))
        children: list[dict[str, Any]] = n.get("children") or []
        for c in children:
            walk(c)
        lineage: list[Any] = n.get("lineage") or []
        for ln in lineage:
            if isinstance(ln, dict):
                cast("dict[str, Any]", ln).pop("session_id", None)
    roots: list[dict[str, Any]] = tree.get("roots") or []
    for r in roots:
        walk(r)


def _scrub_events(evts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Public callers (audit 2026-08-01): event details and warnings can embed
    host paths — dir revokes, scope dumps, clamp warnings — so every string
    leaf is regex-scrubbed. Returns scrubbed COPIES; the doc is untouched."""
    def scrub(v: Any) -> Any:
        if isinstance(v, str):
            return _WINPATH.sub("<path>", v)
        if isinstance(v, list):
            return [scrub(x) for x in cast("list[Any]", v)]
        if isinstance(v, dict):
            return {k: scrub(x) for k, x in cast("dict[str, Any]", v).items()}
        return v
    return [cast("dict[str, Any]", scrub(e)) for e in evts]


class Settings(Body):
    org_dirs: list[Any] | None = None       # external folders [{path, mode}] (ws excluded)
    max_top_grant: int | None = None
    default_top_grant: int | None = None    # pre-filled grant for top-level hires
    compact_at: int | None = None           # compaction threshold in percent, 50..95
    clear_fable_lock: bool = False
    fable_limit_policy: str | None = None   # halt | opus | dissolve
    fable_filter_policy: str | None = None  # halt | opus (content-filter flags)
    default_tools: dict[str, Any] | None = None  # {bash, web, edit, subagents, mcp: []|["*"]}
    default_visibility: str | None = None   # self|team|subtree|full
    default_effort: str | None = None       # ""=CLI default | low..max (live inherit)
    auto_resume: bool | None = None         # restart limit-frozen agents at reset+1min
    cascade_hire: bool | None = None        # hires bubble costs up the chain (§4.6)
    cascade_alloc: bool | None = None       # allocations/upgrades bubble costs up
    net_hub_address: str | None = None      # F-06 (global defaults only): the
                                            # local hub's address for NEW orgs
    net_autoconnect: bool | None = None     # F-06 (per-org): keep/join the
                                            # local hub entry
    net_hubs: list[Any] | None = None       # F-06 (per-org): authoritative hub
                                            # list [{id?, address, enabled?}]
    headless: bool | None = None            # §9.6: no user present; requires
                                            # an api_key (both directions)
    api_key: str | None = None              # §9.5: per-org ANTHROPIC_API_KEY
    clear_api_key: bool = False             # refused while headless is on


# ------------------------------------------- global default org settings
# (user spec): configured from the root page; every NEWLY created org is
# born with these values. Stored org-doc-shaped in <data>/defaults.json.
_DEFAULTS_BASE = {
    "max_top_grant": 1000, "default_top_grant": 50, "compact_at": 0.80,
    "fable_limit_policy": "halt", "fable_filter_policy": "halt",
    "cascade_hire": True, "cascade_alloc": True, "auto_resume": False,
    # F-06: NOT an org-doc key — popped + translated into the "local" hub
    # entry at creation (orgs_create), shown on the root defaults page
    "net_hub_address": net.DEFAULT_HUB_ADDRESS,
}


def load_org_defaults() -> dict[str, Any]:
    try:
        d = json.load(open(os.path.join(store.DATA_ROOT, "defaults.json"),
                           encoding="utf-8"))
        return cast("dict[str, Any]", d) if isinstance(d, dict) else {}
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
    if body.net_hub_address is not None:
        d["net_hub_address"] = body.net_hub_address.strip() \
            or net.DEFAULT_HUB_ADDRESS
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
        # `org_dirs` is typed `list[Any]` on the wire, and norm_dirs assumes
        # every entry is a str or a {path: str} mapping — `[null]` and
        # `[{"path": 123}]` both reached it and came back out as an
        # AttributeError, i.e. a 500. Say what is wrong instead.
        for d in body.org_dirs:
            ok = isinstance(d, str) or (
                isinstance(d, dict)
                and isinstance(cast("dict[str, Any]", d).get("path"), str))
            if not ok:
                raise HTTPException(
                    422, "org_dirs entries must be a path string or "
                         f"{{path, mode}} — got {d!r}")
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
        # D-014: the cap is a real ledger precondition now. Existing over-cap
        # grants are grandfathered (no sweep — which agents shrink is the
        # user's choice, D-003), but lowering past them deserves the truth
        over = [f'{nid} (grant {org.nodes[nid]["grant"]})'
                for nid in org.children(None)
                if org.nodes[nid]["grant"] > int(body.max_top_grant)]
        if over:
            warnings.append(
                "top-level grants already above the new cap (kept as-is; "
                "the cap binds increases): " + ", ".join(over))
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
    # ---- §9.5/§9.6: per-org API key + headless (couplings are HARD rules) --
    if body.api_key is not None and body.api_key.strip():
        org.d["api_key"] = body.api_key.strip()
        warnings.append("API key set — this org's turns now bill the key, "
                        "not the subscription")
    if body.clear_api_key:
        if org.d.get("headless"):
            raise HTTPException(
                422, "this org runs headless, which REQUIRES an API key "
                     "(subscription auth ends in an interactive re-login "
                     "nobody is present to perform) — turn headless off "
                     "first")
        org.d.pop("api_key", None)
        warnings.append("API key cleared — turns use the subscription again")
    if body.headless is not None:
        if body.headless and not org.d.get("headless"):
            if org.d.get("kiosk") is not None:
                raise HTTPException(422, "a kiosk cannot run headless — it "
                                         "is sealed from the org mail that "
                                         "headless depends on")
            if not org.d.get("api_key"):
                raise HTTPException(
                    422, "headless REQUIRES an API key (set it in the same "
                         "panel): subscription auth ends in an interactive "
                         "re-login that a headless org, by definition, has "
                         "nobody to perform")
            halted = [k for k in ("fable_limit_policy", "fable_filter_policy")
                      if org.d.get(k, "halt") == "halt"]
            if halted:
                raise HTTPException(
                    422, f"headless refuses while {' and '.join(halted)} "
                         f"is 'halt' — a halted headless org is a dead org "
                         f"nobody will notice; switch the policy first")
            org.d["headless"] = True
            if not org.d.get("auto_resume"):
                org.d["auto_resume"] = True
                warnings.append("auto-resume forced ON — a limit freeze must "
                                "not park an org nobody will un-park")
        elif not body.headless and org.d.get("headless"):
            org.d["headless"] = False
            warnings.append("headless off — review the user inbox for what "
                            "accumulated while nobody was watching")
    # ---- F-06: mail-hub config (non-kiosk orgs only; kiosks are sealed) ----
    if body.net_autoconnect is not None and org.d.get("kiosk") is None:
        org.d["net_autoconnect"] = bool(body.net_autoconnect)
        hubs = list(org.d.get("net_hubs") or [])
        has_local = any(h.get("id") == net.LOCAL_HUB_ID for h in hubs)
        if body.net_autoconnect and not has_local:
            addr = str(load_org_defaults().get("net_hub_address") or "") \
                or net.DEFAULT_HUB_ADDRESS
            hubs.insert(0, {"id": net.LOCAL_HUB_ID, "address": addr,
                            "enabled": True})
        elif not body.net_autoconnect:
            hubs = [h for h in hubs if h.get("id") != net.LOCAL_HUB_ID]
            warnings.append("local hub entry removed — the org no longer "
                            "auto-connects")
        org.d["net_hubs"] = hubs
    if body.net_hubs is not None and org.d.get("kiosk") is None:
        # authoritative replacement of the hub LIST. Ids (and discovered
        # names) survive by id OR BY ADDRESS (redteam ②: minting a fresh id
        # for an identical address orphaned every spooled entry — net_spool
        # keys on the hub id); entries under truly-removed hubs re-key to the
        # first enabled hub (addresses are hub-agnostic, ruled)
        old_hubs = list(org.d.get("net_hubs") or [])
        old_by_id = {str(h.get("id")): h for h in old_hubs}
        old_by_addr = {str(h.get("address")): h for h in old_hubs}
        new_hubs: list[dict[str, Any]] = []
        for h in body.net_hubs:
            if not isinstance(h, dict):
                raise HTTPException(422, "net_hubs entries must be "
                                         "{id?, address, enabled?}")
            hd = cast("dict[str, Any]", h)
            # bare host / host:port entries are valid (user spec 2026-08-05):
            # no scheme assumes http, no port assumes the hub default 7370
            addr = net.normalize_hub_address(str(hd.get("address") or ""))
            if not addr:
                continue
            kept = old_by_id.get(str(hd.get("id") or "")) \
                or old_by_addr.get(addr) or {}
            hid = str(hd.get("id") or "") or str(kept.get("id") or "") \
                or uuid.uuid4().hex[:8]
            new_hubs.append({"id": hid, "address": addr,
                             "enabled": bool(hd.get("enabled", True)),
                             **({"name": kept["name"]}
                                if kept.get("name") else {})})
        org.d["net_hubs"] = new_hubs
        org.d["net_autoconnect"] = any(
            h["id"] == net.LOCAL_HUB_ID for h in new_hubs)
        # re-key orphaned spool entries so nothing queued becomes invisible
        spool: dict[str, list[Any]] = org.d.get("net_spool") or {}
        live_ids = {h["id"] for h in new_hubs}
        target = next((str(h["id"]) for h in new_hubs if h["enabled"]), None)
        for gone in [k for k in list(spool) if k not in live_ids]:
            entries: list[Any] = spool.pop(gone) or []
            if entries and target:
                spool.setdefault(target, []).extend(entries)
                warnings.append(f"{len(entries)} queued message(s) moved to "
                                f"the remaining mailserver")
            elif entries:
                spool[gone] = entries    # keep; ① blocks new ones doorside
                warnings.append(f"{len(entries)} queued message(s) have no "
                                f"mailserver to leave through — enable one")
        org.d["net_spool"] = spool
    if (body.net_hubs is not None or body.net_autoconnect is not None) \
            and org.d.get("kiosk") is None:
        # per-hub STATE dies with the configuration it described (redteam
        # second wave): a removed id keeps no registration (a re-added local
        # entry must start hidden until it answers again), and a changed
        # ADDRESS keeps no dedupe ring (a ring carried to a different machine
        # silently swallows a re-homed peer's re-sent ids — dropping it risks
        # a bounded duplicate, never a loss). The net daemon reconciles the
        # same way for direct doc edits.
        cells = cast("dict[str, dict[str, Any]]",
                     org.d.get("net_state") or {})
        addr_now = {str(h.get("id")): str(h.get("address"))
                    for h in org.d.get("net_hubs") or []}
        for k in list(cells):
            if k not in addr_now \
                    or (cells.get(k) or {}).get("address") != addr_now[k]:
                cells.pop(k, None)
    store.save_org(org)
    hub_changed(slug)
    net.kick()
    return {"dirs": org.d["dirs"], "warnings": warnings}


class KioskCfg(Body):
    enabled: bool | None = None
    credits: int | None = None            # top-level holdings cap (0 = uncapped)
    spend_limit: float | None = None      # USD hard limit (0 = unlimited)
    storage_limit_mb: int | None = None   # workspace-dir size cap (0 = unlimited)
    rotate_token: bool = False            # mint a new secret URL (revokes the old)
    max_scope: dict[str, Any] | None = None   # the permission ceiling; setting it SWEEPS
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
            # sandboxed kiosks: the limit IS the disk size — same 4096 MB
            # floor as creation, or the migration would silently re-floor it
            if k.get("sandbox") and int(body.storage_limit_mb) < 4096:
                raise HTTPException(
                    422, "sandboxed orgs ride a fixed-size disk with a "
                         "4096 MB minimum — set storage to at least 4096 MB")
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
            except _BAD_SHAPE as e:
                raise HTTPException(422, f"malformed max_scope: {e}")
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
        cleared: list[str] = []
        spent = org.cost_total()            # incl. deleted agents' burn
        lim = float(k.get("spend_limit") or 0)
        over = k.get("enabled") and lim and spent >= lim
        drive_after: list[str] = []
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


class HireDefaults(Body):
    default_tools: dict[str, Any] | None = None  # {bash, web, edit, subagents, mcp}
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


class Scope(Body):
    add_dirs: list[dict[str, Any]] | None = None  # [{path, mode: rw|ro}]
    tools: dict[str, Any] | None = None     # {bash, web, edit, subagents, mcp: []}
    org_visibility: str | None = None
    permission_mode: str | None = None      # rides the ceiling (spec §2)
    charter: str | None = None              # §15: this node's role card
    team_charter: str | None = None         # §15: binds this node's whole subtree
    effort: str | None = None               # thinking effort: low|medium|high|"" clears
    model_version: str | None = None        # a VERSION inside the tier ("" clears)
    raise_ceiling: bool = False             # the one-action bridge (spec §1)


@app.post("/api/orgs/{slug}/nodes/{nid}/scope")
# plain `def`, not `async` (No.22): the body does load_org + save_org under a
# THREADING lock, and an `async def` runs that ON THE EVENT LOOP -- so while it
# waits for the lock or the disk, every other request and every websocket frame
# waits with it. As a sync def FastAPI runs it in the threadpool and only this
# request pays. Measured 3-22 ms either way, so this is NOT the cause of the
# reported effort lag; it is a hazard that sat on the path and cost nothing to
# remove. 15 other async routes still do doc IO on the loop -- listed in the
# docket, deliberately not swept here.
def node_scope(slug: str, nid: str, body: Scope,
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
                                   effort=body.effort,
                                   model_version=body.model_version,
                                   raise_ceiling=rc)
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    if pub and isinstance(result, dict):
        result.pop("bridge", None)
    # (the explicit broadcast is gone: store.save_org announces every write
    # now -- G2 -- so this was a second, uncoalesced copy of one signal)
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
    out: list[dict[str, Any]] = []
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
            "cli_version": supervisor.cli_version(),
            # None = uvicorn has no WebSocket implementation, so pushed updates
            # never reach the browser and the UI is running on its polling
            # heartbeats alone. Reported here so the deployment can SAY it
            # rather than just feeling slow (see _ws_impl).
            "websockets": _ws_impl(),
            # which interpreter is serving. `venv` false means the deps live in
            # a system-wide Python shared with every other project, which is
            # how the missing-websockets bug stayed invisible for so long
            # (D-46). Reported so the answer needs no process forensics: on
            # Windows a venv-launched process reports the BASE exe in the task
            # list, so "which python is this" is genuinely hard to see from
            # outside.
            "python": {"prefix": sys.prefix,
                       "venv": sys.prefix != sys.base_prefix,
                       "version": sys.version.split()[0]}}


class Reorder(Body):
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


class Message(Body):
    text: str
    # relative uploads/ paths already landed via the upload endpoint — the
    # composer stages them and sends them WITH the mail (user spec 2026-07-31)
    attachments: list[str] = []
    # FR-05: when this is an inline mailbox REPLY, a snapshot of the mail it
    # answers ({id, from, at, gist}) — quoted in the agent's [MAIL] block
    reply_to: dict[str, Any] | None = None


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
    # is correspondence and keeps full MAIL semantics (durability, a Sent copy,
    # delivery at rehire). What a command DOES share with a message, since
    # 2026-08-03, is the two consequences of direct user contact: the superior
    # chain is notified and the node gains a user audience. Those are about who
    # the user reached, not about whether a copy was filed.
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
            if n.get("remote_controlled"):
                # FR-01 (redteam): the remote park queues MAIL, but a command
                # has no mailbox behind it — success here would be a lie
                raise HTTPException(
                    409, "under remote control — a session command would be "
                         "dropped, not queued; release remote control first")
            # A command is direct user contact, so it carries the same two
            # consequences a message does: the superior chain is told, and the
            # node gains a user audience (user report 2026-08-03 — running a
            # command did neither, so the user could `/compact` an agent deep
            # in a tree and nobody above it would ever know). Done HERE, after
            # the validity checks and before any of the three command paths
            # below, so all of them get it from one place — the branch has
            # several returns and per-return calls would rot apart.
            org.user_deep_reach(nid, stripped[:160], kind="command")
            store.save_org(org)
        if stripped.split()[0] == "/compact":
            # review C4: one word, one meaning. The hinted /compact used to
            # compact the CLI session IN PLACE — same desk, same word as the
            # compact button, opposite §8 consequence (no knowledge bearer).
            # It now routes to the same org split the button runs.
            if n.get("bearer_state"):
                raise HTTPException(422, "a knowledge bearer never re-compacts (§8.3)")
            if n.get("remote_controlled"):
                # FR-01: unreachable today (the endpoint's own remote gate
                # refuses first), kept HERE like busy/bearer so the branch
                # stays safe if it ever moves — the fork rebinds the session
                # id out from under the phone
                raise HTTPException(409, "under remote control — release it "
                                         "before compacting")
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
        # same rule as /scratch: `nid` reaches the filesystem here (via
        # scratch_dir's makedirs), so it must name a real node first — an
        # unresolved `..\..\..\x` created a directory outside the data root
        # and only THEN got its 422 from post_mail
        try:
            store.load_org(slug).node(nid)
        except LedgerError as e:
            raise HTTPException(404, str(e))
        base = os.path.realpath(supervisor.scratch_dir(slug, nid))
        for rel in body.attachments[:10]:
            full = os.path.realpath(
                os.path.join(base, _no_nul(str(rel)).lstrip("/\\")))
            if full.startswith(base + os.sep) and os.path.isfile(full):
                metas.append({"name": os.path.basename(full),
                              "path": str(rel).replace("\\", "/"),
                              "bytes": os.path.getsize(full)})
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            r = org.post_mail(USER, nid, body.text, attachments=metas or None,
                              reply_to=body.reply_to)
            # 80 chars truncated most instructions mid-clause; the notice is a
            # gist, but it has to survive being read on its own
            org.user_deep_reach(nid, body.text.strip().splitlines()[0][:160])
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
    if n.get("remote_controlled"):
        # FR-01 (redteam): compaction forks this session id and rebinds the
        # node to a new one — the phone would keep driving an orphaned id
        raise HTTPException(409, "under remote control — release it before "
                                 "compacting (the fork would strand the "
                                 "controlled session)")
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


class CreditDecision(Body):
    id: str
    action: str = "approve"    # approve | deny
    # F-05 counter-offer: the amount the user actually grants — any legal
    # value (below the ask, above it, or a clawback down to the committed
    # floor). Absent = the asked amount, the old one-click approve.
    granted: int | None = None
    # dry run: validate + return the stranding warnings for `granted`,
    # mutating nothing — the card shows them BEFORE the user commits
    dry: bool = False


@app.post("/api/orgs/{slug}/credit-requests")
async def credit_request_decide(slug: str, body: CreditDecision) -> dict[str, Any]:
    """Decide a top-level agent's credit request: approve as asked, counter-
    offer any legal amount (F-05), or deny. The outcome reaches the agent as
    ordinary user MAIL (the unified ask system: the answer is a mail that
    drives a turn), wearing honest wording — a partial grant is not an
    approval, and the matter stays the agent's to continue."""
    if body.dry:
        if body.granted is None:
            raise HTTPException(422, "dry run needs `granted`")
        with store.DOC_LOCK:
            try:
                return store.load_org(slug).credit_preview(body.id, body.granted)
            except LedgerError as e:
                raise HTTPException(422, str(e))
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            req = org.credit_request_action(body.id, body.action,
                                            granted=body.granted)
            _kiosk_cap_check(org)
            notice = req.get("notice")
            drive = False
            if notice and req["node"] in org.nodes:
                drive = not org.post_mail(
                    USER, req["node"], notice).get("deferred")
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    if drive:
        mail_notify(slug, USER, req["node"])
        supervisor.send_message(
            slug, req["node"],
            "(orgtree) The mail above contains the user's decision on your "
            "credit request — proceed accordingly.")
    await hub.changed(slug)
    return req


class RemoteControl(Body):
    action: str                       # "start" | "stop"


@app.post("/api/orgs/{slug}/nodes/{nid}/remote-control")
def remote_control(slug: str, nid: str, body: RemoteControl,
                   request: Request) -> dict[str, Any]:
    """FR-01: hand the agent's real session to the user's claude.ai / mobile
    app (`claude remote-control --session-id`). Strictly user-triggered —
    starting the server enrolls THIS device on the user's account — and
    loopback-only (never the kiosk gateway)."""
    if _public_slug(request):
        raise HTTPException(404, "not found")
    if body.action == "start":
        r = supervisor.remote_control_start(slug, nid)
    elif body.action == "stop":
        r = supervisor.remote_control_stop(slug, nid)
    else:
        raise HTTPException(422, "action must be start or stop")
    if r.get("error"):
        raise HTTPException(422, str(r["error"]))
    hub_changed(slug)
    return r


@app.get("/api/orgs/{slug}/documents/{did}")
def document_get(slug: str, did: str) -> dict[str, Any]:
    """FR-03: the reader fetches the BODY on open (the tree payload carries
    metadata only). Kiosk visitors are the user of their org — readable."""
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    doc = next((x for x in org.d.get("documents", []) if x["id"] == did), None)
    if doc is None:
        raise HTTPException(
            404, f"no document {did!r} — it was dismissed, or evicted by "
                 f"later presentations (newest 10 per agent are kept; "
                 f"evictions are in the org log)")
    return {"id": doc["id"], "node": doc["node"], "title": doc["title"],
            "body": doc["body"], "at": doc["at"]}


@app.delete("/api/orgs/{slug}/documents/{did}")
async def document_dismiss(slug: str, did: str) -> dict[str, Any]:
    """FR-03: the card's ✕ — remove a presented document."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            r = org.dismiss_document(did)
        except LedgerError as e:
            raise HTTPException(404, str(e))
        store.save_org(org)
    await hub.changed(slug)
    return {"ok": True, "node": r["node"]}


class AskAnswer(Body):
    # single card: the picked labels. FR-04 batch card: ONE item per tab,
    # positionally — a string, or a list for a multi tab's picks
    selected: list[str | list[str]] | None = None
    text: str | None = None
    # the card revision the answer was composed against (redteam CAS —
    # answers are positional, so an amend mid-render must refuse the stale
    # submission rather than attach it to questions the user never saw)
    rev: int | None = None
    # the card's ✕ — close without answering (mirrors AskUserQuestion's Esc)
    dismiss: bool = False


@app.post("/api/orgs/{slug}/asks/{aid}/answer")
async def ask_answer(slug: str, aid: str, body: AskAnswer) -> dict[str, Any]:
    """Answer an agent's question (F-04) — from the desk card or the inbox
    card, whichever the user reached first. Marking happens before the mail
    is posted, under one doc lock, so the turn the answer starts can never
    void its own question; every other rendering of the card nulls to grey
    "answered" on the next payload."""
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
            r = (org.ask_dismiss(aid) if body.dismiss
                 else org.ask_answer(aid, selected=body.selected,
                                     text=body.text, rev=body.rev))
            drive = not org.post_mail(USER, r["node"], r["body"]).get("deferred")
        except LedgerError as e:
            raise HTTPException(422, str(e))
        store.save_org(org)
    if drive:
        mail_notify(slug, USER, r["node"])
        supervisor.send_message(
            slug, r["node"],
            "(orgtree) The mail above answers the question you asked the "
            "user — act on it now." if not body.dismiss else
            "(orgtree) The mail above reports that the user dismissed your "
            "question — proceed accordingly.")
    await hub.changed(slug)
    return {"answered": aid, "node": r["node"]}


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


class InboxRead(Body):
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
        keep: list[UserMailEntry] = []
        read: list[UserMailEntry] = []
        for m in org.d.get("user_inbox", []):
            (read if m.get("id") in ids else keep).append(m)
        if read:
            org.d["user_inbox"] = keep
            log = org.d.setdefault("user_mail_log", [])
            log.extend(read)
            # the archive is CHRONOLOGICAL, never read-order. extend() appends
            # in whatever order the user happened to CLICK, and the reader
            # renders by list position — so without this sort a mail read
            # second outranks one sent later (user bug 2026-08-02). `at` is
            # ISO-8601 Z, so a string sort is a time sort.
            log.sort(key=lambda m: m.get("at") or "")
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


class ExternSend(Body):
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
    atts: list[str] = []
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
    out: list[dict[str, Any]] = []
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


# ---- F-06 E: the user composes extern mail from the mailbox UI ----
# The user bypasses the audience gate (they outrank it) and this grants
# nobody anything. Attachments stage first (browser body upload, same caps),
# then ride the same transport as agent sends. Stage hygiene (redteam):
# per-ORG ids, startup sweep (in-memory ids die with the process, so files
# on disk at boot are unreachable), and a 24 h age-out for abandoned drafts;
# successfully drained files are deleted by net._spool_done.
_COMPOSE_STAGE: dict[str, tuple[str, str]] = {}   # stage-id → (slug, path)
_COMPOSE_DIR = "net_stage"


def _prune_stage(max_age_s: float = 86400.0) -> None:
    stage = os.path.join(store.DATA_ROOT, _COMPOSE_DIR)
    try:
        cutoff = time.time() - max_age_s
        for f in os.listdir(stage):
            p = os.path.join(stage, f)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    _COMPOSE_STAGE.pop(f.split("-", 1)[0], None)
            except OSError:
                pass
    except OSError:
        pass


class OrgInboxSend(Body):
    to: str                                  # @ext:/@org:/@mcp:/@net: address
    body: str
    attachments: list[str] = []              # stage ids from /org_inbox/upload


@app.post("/api/orgs/{slug}/org_inbox/upload")
async def org_inbox_upload(slug: str, request: Request,
                           name: str = "file") -> dict[str, Any]:
    if _public_slug(request):
        raise HTTPException(404, "not found")
    # refuse oversize BEFORE buffering when the client says how big it is
    try:
        clen = int(request.headers.get("content-length") or 0)
    except ValueError:
        clen = 0
    if clen > _NET_ATT_MAX:
        raise HTTPException(413, "attachment exceeds 25 MB")
    data = await request.body()
    if len(data) > _NET_ATT_MAX:
        raise HTTPException(413, "attachment exceeds 25 MB")
    _prune_stage()
    stage = os.path.join(store.DATA_ROOT, _COMPOSE_DIR)
    os.makedirs(stage, exist_ok=True)
    sid = uuid.uuid4().hex
    safe = re.sub(r"[^\w .()+\-]", "_",
                  os.path.basename(name)).strip(" .")[:120] or "file.bin"
    path = os.path.join(stage, f"{sid}-{safe}")
    with open(path, "wb") as f:
        f.write(data)
    _COMPOSE_STAGE[sid] = (slug, path)
    return {"id": sid, "name": safe, "bytes": len(data)}


@app.post("/api/orgs/{slug}/org_inbox/send")
def org_inbox_send(slug: str, body: OrgInboxSend,
                   request: Request) -> dict[str, Any]:
    if _public_slug(request):
        raise HTTPException(404, "not found")
    to = body.to.strip()
    if not to.startswith(("@ext:", "@org:", "@mcp:", "@net:")):
        raise HTTPException(422, "recipient must be an outside address "
                                 "(@ext:/@org:/@mcp:/@net:)")
    paths: list[str] = []
    for sid in body.attachments[:10]:
        staged = _COMPOSE_STAGE.get(sid)
        if not staged or staged[0] != slug or not os.path.isfile(staged[1]):
            raise HTTPException(422, f"staged attachment {sid!r} not found — "
                                     f"re-upload and retry")
        paths.append(staged[1])
    if paths and to.startswith(("@ext:", "@mcp:")):
        # ruled 2026-08-05: those transports are text-only
        raise HTTPException(422, "attachments ride @net: and @org: mail "
                                 "only — @ext:/@mcp: are text-only transports")
    warnings: list[str] = []
    with store.DOC_LOCK:
        try:
            org = store.load_org(slug)
        except LedgerError as e:
            raise HTTPException(404, str(e))
        if org.d.get("kiosk") is not None:
            raise HTTPException(422, "a sealed kiosk org has no outside face")
        if to.startswith("@net:") and to[5:] == (
                (org.d.get("net_identity") or {}).get("slug")):
            raise HTTPException(422, "that address is this organization")
        if to.startswith("@net:") and not any(
                h.get("enabled") for h in org.d.get("net_hubs") or []):
            raise HTTPException(422, "no mailserver is configured — enable a "
                                     "hub in settings → mailserver first")
        oid = org._org_inbox_log("out", to, body.body, by="user")
        if to.startswith("@net:"):
            net.spool_append(org, to[5:], body.body, oid=oid,
                             attachments=paths)
        store.save_org(org)
    # spark on the wire (user spec 2026-08-05): a user compose leaves the
    # eye for the mailbox like an agent's outbound leaves its node
    mail_notify(slug, USER, "org_inbox")
    if to.startswith("@net:"):
        net.kick()
    elif to.startswith("@org:"):
        dst = to[5:]
        try:
            dst_org = store.load_org(dst)
            sealed = dst_org.d.get("kiosk") is not None
        except LedgerError:
            sealed = True
        if sealed:
            # same anti-enumeration answer as interorg_send
            warnings.append(f"not delivered: no organization named {dst!r} "
                            f"is reachable")
        else:
            supervisor.deliver_org_inbox(dst, f"@org:{slug}", body.body,
                                         attachments=paths or None)
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass
    elif to.startswith("@ext:"):
        ok = supervisor.chatq_send(
            slug, to[5:],
            f"[message from orgtree org '{slug}' (sent by its user)]\n"
            + body.body)
        if not ok:
            warnings.append(f"chatq delivery to {to[5:]} failed — is the "
                            f"target chat still registered?")
    # @mcp: — the org-inbox entry IS the delivery; the peer polls
    hub_changed(slug)
    return {"id": oid, "warnings": warnings}


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
def node_history(slug: str, nid: str, request: Request,
                 last: int = 80) -> dict[str, Any]:
    """Message history with attribution + delivered notices + ops touching the node."""
    try:
        org = store.load_org(slug)
        org.node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    items: list[dict[str, Any]] = []
    for ev in org.d.get("events", []):
        det: dict[str, Any] = ev.get("detail", {})
        touches = (det.get("node") == nid or det.get("to") == nid
                   or ev.get("actor") == nid or det.get("grantee") == nid
                   or det.get("from") == nid)
        if touches:
            # №10: keep warning LISTS too — the scalar filter silently dropped
            # the §4.6 cascade warnings from the only log that had them
            items.append({"at": ev["at"], "kind": ev["op"], "actor": ev["actor"],
                          "detail": {k: (v if isinstance(v, (str, int, float))
                                         else [str(x) for x in cast("list[Any]", v)])
                                     for k, v in det.items()
                                     if isinstance(v, (str, int, float, list))},
                          "warnings": [str(w) for w
                                       in cast("list[Any]", ev.get("warnings") or [])]})
    for n in org.d.get("notice_log", []):
        if n["node"] == nid:
            items.append({"at": n["at"], "kind": "notice", "actor": "system",
                          "detail": {"text": n["text"]}})
    items.sort(key=lambda x: x["at"])
    # clamped like /chat's `last`: `?last=0` is `items[-0:]`, i.e. the WHOLE
    # log — the one value of `last` that means "no limit"
    out = items[-max(1, min(last, 1000)):]
    if _public_slug(request):
        out = _scrub_events(out)     # e.g. revoke_dir carries the host path
    return {"items": out}


@app.get("/api/orgs/{slug}/nodes/{nid}/scratch")
def node_scratch(slug: str, nid: str, path: str = "") -> dict[str, Any]:
    # ☠ The node MUST be resolved before `nid` reaches the filesystem. This
    # was the only /nodes/{nid}/… endpoint that skipped it, and `nid` is
    # joined straight into a path by supervisor.scratch_dir: `nid` =
    # `..\..\..\..\Users` walked out of the data root, mkdir'd the target,
    # and then anchored the containment check TO THE ESCAPED BASE — so the
    # listing and the 60 KB file read both succeeded. Reachable through the
    # kiosk gateway (the path is org-scoped, so the public matrix allows it),
    # which made it an internet-facing read of the operator's filesystem.
    try:
        store.load_org(slug).node(nid)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    base = os.path.realpath(supervisor.scratch_dir(slug, nid))
    full = os.path.realpath(os.path.join(base, _no_nul(path).lstrip("/\\")))
    # separator-anchored: a bare prefix test admits sibling dirs (<base>-x)
    if full != base and not full.startswith(base + os.sep):
        raise HTTPException(422, "path escapes the scratch space")
    if os.path.isdir(full):
        out: list[dict[str, Any]] = []
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
def orgmd_get(slug: str, request: Request) -> dict[str, Any]:
    try:
        org = store.load_org(slug)
    except LedgerError as e:
        raise HTTPException(404, str(e))
    ws = org.d.get("workspace")
    p = os.path.join(ws, "CLAUDE.md") if ws else None
    content = ""
    if p and os.path.isfile(p):
        content = open(p, encoding="utf-8", errors="replace").read()[:60000]
    if _public_slug(request) and p:
        p = os.path.basename(p)      # the host path is the operator's, not the org's
    return {"path": p, "content": content}


class OrgMd(Body):
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


class AudienceAction(Body):
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
    headers: dict[str, str] = {}
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
class AgentCall(Body):
    org: str
    node: str
    tool: str
    args: dict[str, Any] = {}


# every `args` key the tool schema documents as text: an identifier, a tier
# name, a body, a status. Containers were never legal in any of them.
_ARG_STRS = ("node", "to", "from", "target", "grantee", "parent", "new_parent",
             "name", "tier", "kind", "body", "action", "status", "summary",
             "reason", "charter", "team_charter", "org_visibility", "effort",
             "path")


def _norm_args(a: dict[str, Any]) -> dict[str, Any]:
    """Normalise the free-form `args` dict an LLM fills in.

    Two 500 families came out of trusting it verbatim. A container in a text
    argument reached `self.nodes[nid]` as an unhashable dict key (every
    node-taking tool) and `delivered.startswith(...)` as a list (message). An
    explicit `null` was worse than a missing key: `a.get("to", "")` returns
    None when the key is PRESENT and null, so the "" default never applied.

    So: drop nulls (restoring the defaults), coerce scalars to text, refuse
    containers with the same 422 shape as any other bad argument."""
    out = dict(a)
    for k in _ARG_STRS:
        if k not in out:
            continue
        v = out[k]
        if v is None:
            del out[k]                       # let the `.get(k, default)` win
        elif isinstance(v, (dict, list, tuple, set)):
            raise LedgerError(
                f"{k} must be text, not {type(cast('object', v)).__name__}")
        elif not isinstance(v, str):
            out[k] = str(v)                  # a bare number reads as its text
    return out


def _arg_int(a: dict[str, Any], key: str, default: int) -> int:
    """`args` is a free-form dict off the wire — an LLM fills it, so a string
    or a float lands there routinely. A bare `int(a.get(k) or d)` turned
    `{"delta": "x"}` and `{"last": "abc"}` into an uncaught ValueError, i.e. a
    500 from the gateway an agent is holding a tool result open on. Coerce
    what is coercible; refuse the rest the way every other bad argument is."""
    v = a.get(key)
    if v is None or v == "":
        return default
    # ⚠ OverflowError as well as TypeError/ValueError: `int(float("Infinity"))`
    # and `float("1e400")` raise it, not ValueError, so "Infinity", "-Infinity"
    # and "1e400" walked past the guard and 500ed the gateway an agent is
    # holding a tool result open on. Found 2026-08-04 by the mcptool suite,
    # which builds these args itself — and an LLM writes "Infinity" far more
    # readily than a human does.
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(float(v))
        except (TypeError, ValueError, OverflowError):
            raise LedgerError(f"{key} must be a number (got {v!r})")


# the @net: attachment cap — same value as the user-upload per-file cap
# (deliberately its own name; see the anchor note at the use site)
_NET_ATT_MAX = 25 * 1048576


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
    try:
        a = _norm_args(body.args)
    except LedgerError as e:
        raise HTTPException(422, str(e))
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
                last = max(1, min(_arg_int(a, "last", 30), 80))
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
            rel = _no_nul(str(a.get("path") or "")).strip().lstrip("/\\")
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
    # rename orchestrates its own DOC_LOCK + filesystem moves — it must run
    # OUTSIDE the block below (the lock is not reentrant)
    if body.tool == "orgtree_rename":
        try:
            return supervisor.rename_node(
                body.org, a.get("node") or "", a.get("name") or "",
                actor=body.node)
        except LedgerError as e:
            raise HTTPException(422, str(e))
    drive: list[str] = []      # nodes whose turn should run after we release the lock
    ext_send: tuple[str, str] | None = None   # (chat-id, body) outbound riding the chatq bridge
    org_send: tuple[str, str] | None = None   # (dst-slug, body) outbound to another org's inbox
    net_send = False                          # @net: — staged to the spool; kick after the lock
    with store.DOC_LOCK:
        try:
            org = store.load_org(body.org)
            org.node(body.node)
            if body.tool == "orgtree_message":
                # F-06 D: outbound attachments — @net: recipients only in v1
                # (ruled; @ext:/@mcp: are text-only transports, @org: local
                # mail has its own path). Validated BEFORE post_mail so a
                # refused send records nothing.
                if str(a.get("to", "")).startswith("@net:") \
                        and not any(h.get("enabled")
                                    for h in org.d.get("net_hubs") or []):
                    # redteam ①: refuse at the door — the old fallback spooled
                    # under an id no drain visits ("queued" forever)
                    raise LedgerError(
                        "no mailserver is configured for this org — ask the "
                        "user to enable a hub (settings → mailserver) before "
                        "addressing @net: mail")
                net_atts: list[str] = []
                raw_atts = [str(x) for x in
                            cast("list[Any]", a.get("attachments") or [])]
                if raw_atts:
                    if not str(a.get("to", "")).startswith("@net:"):
                        raise LedgerError(
                            "attachments ride @net: mail only (v1) — for "
                            "local recipients use orgtree_send_file or paths")
                    if len(raw_atts) > 10:
                        raise LedgerError("at most 10 attachments")
                    ab = os.path.realpath(
                        supervisor.scratch_dir(body.org, body.node))
                    for rel in raw_atts:
                        rel = _no_nul(rel).strip().lstrip("/\\")
                        full = os.path.realpath(os.path.join(ab, rel))
                        # separator-anchored containment (send_file pattern)
                        if full != ab and not full.startswith(ab + os.sep):
                            raise LedgerError(f"attachment escapes your "
                                              f"scratch space: {rel}")
                        if not os.path.isfile(full):
                            raise LedgerError(f"attachment not found: {rel}")
                        # cap under its own name: the user-upload cap's
                        # identifier is test_mcptool's source-slice END
                        # ANCHOR for the dispatch-verb extraction — writing
                        # that token here (even in a comment) truncates the
                        # slice and the drift guard fires
                        if os.path.getsize(full) > _NET_ATT_MAX:
                            raise LedgerError(
                                f"attachment over 25 MB: {rel}")
                        net_atts.append(full)
                result = org.post_mail(body.node, a.get("to", ""), a.get("body", ""),
                                       a.get("kind", "message"))
                delivered = result.get("delivered")
                if delivered and delivered.startswith("@"):
                    # spark on the wire (user spec 2026-08-05): outbound
                    # org mail rides the sender→mailbox line, whatever the
                    # transport branch below does with it
                    mail_notify(body.org, body.node, "org_inbox")
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
                elif delivered and delivered.startswith("@net:"):
                    # F-06: stage the spool entry on the SAME loaded org — it
                    # rides this block's save, so the org-inbox row and the
                    # spool entry land atomically (no crash window). The
                    # daemon ships it; the agent's call returns instantly.
                    net.spool_append(org, delivered[5:], a.get("body", ""),
                                     oid=str(result.get("id") or ""),
                                     kind=a.get("kind", "message"),
                                     attachments=net_atts)
                    net_send = True
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
            elif body.tool == "orgtree_ask":
                result = org.ask_user(body.node, a.get("question") or "",
                                      options=a.get("options"),
                                      multi=bool(a.get("multi")),
                                      header=a.get("header"),
                                      questions=a.get("questions"))
            elif body.tool == "orgtree_present":
                # FR-03: a reading card beside the node — non-blocking
                result = org.present_document(body.node,
                                              a.get("title") or "",
                                              a.get("body") or "",
                                              a.get("replaces"))
                # no user audience → the question rode to the superior as
                # mail; drive them like any other delivery
                routed = result.get("routed")
                if routed and not result.get("deferred"):
                    drive.append(routed)
            elif body.tool == "orgtree_hire":
                hdirs, dwarns = supervisor.sandbox_dirs_to_host(
                    org, a.get("add_dirs"))
                result = org.hire(body.node, a.get("parent") or body.node,
                                  a.get("tier"), _arg_int(a, "grant", 0),  # type: ignore[arg-type]  # ledger 422s a missing tier
                                  a.get("name") or "", add_dirs=hdirs,
                                  tools=a.get("tools"),
                                  org_visibility=a.get("org_visibility"),
                                  charter=a.get("charter"))
                if dwarns:
                    result.setdefault("warnings", []).extend(dwarns)
                # observed on another install (user report 2026-08-02): an
                # agent hires, writes a thorough charter, and considers the
                # delegation DONE — the hire then sits idle forever, because
                # nothing in the tree self-starts. The charter is identity;
                # mail is what runs a turn. Said in the RESULT because that is
                # what the hiring agent reads next, not the tool description
                # it read once.
                if result.get("node"):
                    result["next_step"] = (
                        f'"{result["node"]}" is hired and IDLE. Hiring does not '
                        f'start it — send it an orgtree_message now saying what '
                        f'to do, or it will never run.')
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
                # `grant` now goes through _arg_int like every other int
                # argument. It was the ONE that did not, so {"grant": "abc"}
                # reached `int(grant)` in the ledger and 500ed (mcptool suite,
                # 2026-08-04). None/"" stays None — rehire's "no explicit grant".
                _g = a.get("grant")
                result = org.rehire(body.node, a.get("node"),  # type: ignore[arg-type]  # node() 422s on None
                                    None if _g is None or _g == ""
                                    else _arg_int(a, "grant", 0))
                drive.extend(result.pop("drive", []))
            elif body.tool == "orgtree_move":
                result = org.move(body.node, a.get("node", ""),
                                  a.get("new_parent") or None)
            elif body.tool == "orgtree_list_orgs":
                # №43 (user-approved): the @org: channel was advertised but
                # undiscoverable from inside — agents had no org listing.
                # F-06 (§6 presence): remote peers from the hub roster ride
                # the same listing, addressed @net:<slug>, with online /
                # last_seen so an agent can route around a dark peer.
                result = {"orgs": [
                    {"slug": o["slug"], "name": o.get("name", o["slug"]),
                     "you": o["slug"] == body.org}
                    for o in store.list_orgs() if not o.get("kiosk")]
                    + net.remote_peers()}
            elif body.tool == "orgtree_dissolve":
                result = org.dissolve(body.node, a.get("node"))  # type: ignore[arg-type]  # node() 422s on None
            elif body.tool == "orgtree_reallocate":
                result = org.reallocate(body.node, a.get("node"), _arg_int(a, "delta", 0))  # type: ignore[arg-type]  # node() 422s on None
            elif body.tool == "orgtree_switch_model":
                result = org.switch_model(body.node, a.get("node", ""),
                                          a.get("tier", ""))
            elif body.tool == "orgtree_status":
                status = a.get("status", "working")
                summary = a.get("summary", "")
                # persisted on the node (survives restarts); a new turn moves
                # it to prev_status, so a stale "done" never shows over live
                # work but the history is not erased (gap audit №13)
                # user ruling 2026-08-02: `done` and `idle` are not functionally
                # distinct — an agent that finished IS idle. The DONE report
                # still goes to the superior below; the node then simply sits
                # idle, carrying the summary so the chip still says what it did.
                # `blocked` is NOT collapsed: it means "stuck, needs a human or
                # a superior", which idle does not.
                stored = "idle" if status == "done" else status
                org.node(body.node)["last_status"] = {
                    "status": stored, "summary": summary,
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
    if body.tool in ("orgtree_retire", "orgtree_dissolve", "orgtree_rename"):
        # FR-01 (redteam): agents removing/re-keying seats must not orphan a
        # running remote-control server either
        supervisor.remote_reap(body.org)
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
    if net_send:
        # F-06: the spool entry is persisted — wake the sender daemon; the
        # agent's result already reflects "queued", never a network wait
        net.kick()
        if isinstance(result, dict):
            result.setdefault("warnings", []).append(
                "queued for the mail hub — delivery states (sent/delivered/"
                "read) appear on the org inbox entry")
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
    # a filename the host refuses is an OSError from the write below, i.e. a
    # 500: Windows caps one path COMPONENT at 255 chars, and `?name=` is
    # attacker-supplied. Bound it here, leaving room for the `-2` de-dupe.
    stem, ext = (stem[:120] or "upload"), ext[:20]
    safe = stem + ext
    final, i = safe, 2
    while os.path.exists(os.path.join(updir, final)):
        final, i = f"{stem}-{i}{ext}", i + 1
    try:
        with open(os.path.join(updir, final), "wb") as f:
            f.write(data)
    except OSError as e:
        # ENOSPC on a full org disk, or a name the filesystem still refuses
        raise HTTPException(422, f"could not store the upload: {e}")
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
    raw = _no_nul(str(a.get("path") or "")).strip()
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
    new_outdir = not os.path.isdir(outdir)
    try:
        os.makedirs(outdir, exist_ok=True)
        if new_outdir:
            # backend-minted = root-owned inside a sandbox — the agent is then
            # TOLD its file is in outbox/ and finds a dir it cannot write
            # (live bug 2026-08-04, kiosk `vnuser`)
            sandbox.chown_agent(org, nid)
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
    full = os.path.realpath(os.path.join(base, _no_nul(path).lstrip("/\\")))
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
# ⚠ `.bridge` is not an engine file — orgtree writes it itself
# (sandbox.py: `{home}/orgtree/.bridge` = {"url", "secret"}), and it holds the
# org's SANDBOX BRIDGE SECRET. The bridge listener binds 0.0.0.0, so a visitor
# who downloads this file gets: the /api/agent gateway this very matrix
# freezes for the public (acting as ANY node of the org), the node steer
# fetch, and the /anthropic proxy — which attaches the HOST's subscription
# token. Verified reachable at GET …/disk/file?path=home/orgtree/.bridge.
_PUBLIC_DISK_DENY = (".credentials.json", ".claude.json", ".bridge")
#: how much of a file a visitor download scans for this org's bridge secret.
#: 256 KiB covers any plausible copy of a credential file while costing one
#: read; see disk_file for why the name check alone is not a boundary.
_SECRET_SCAN_BYTES = 262144
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
    rel = posixpath.normpath(_no_nul(path or "").replace("\\", "/").strip("/"))
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
            return "blocked", "credential/secret file"
        return "content", "credential/secret file — admin-side only"
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
            # admin-only nudge: org disks are SPARSE, the VM cap is the
            # aggregate wall — None = unset on the host
            **({} if public else {
                "vm_cap_mib": sandbox.vm_disk_cap_mib(),
                "size_mb": int((org.d.get("disk") or {}).get("size_mb") or 0),
                "pending_mb": (org.d.get("disk") or {}).get("pending_size_mb"),
            }),
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
            "full": bool(org.d.get("storage_full")),
            **({} if public else {
                "vm_cap_mib": sandbox.vm_disk_cap_mib(),
                "size_mb": int((org.d.get("disk") or {}).get("size_mb") or 0),
                "pending_mb": (org.d.get("disk") or {}).get("pending_size_mb"),
            })}


@app.get("/api/orgs/{slug}/disk/file")
def disk_file(slug: str, request: Request, path: str = "") -> FileResponse:
    """Streaming download (FileResponse streams — a multi-GB file is never
    buffered). Visitors get everything except the engine credential files."""
    org = _disk_org(slug)
    rel, full = _disk_rel(slug, path)
    public = bool(_public_slug(request))
    cls, why = _disk_classify(org, rel, public)
    if cls == "blocked" and rel.rsplit("/", 1)[-1] in _PUBLIC_DISK_DENY:
        raise HTTPException(403, why or "not served publicly")
    if not os.path.isfile(full):
        raise HTTPException(404, f"no such file: {rel!r}")
    # ☠ A FILENAME denylist is not a boundary here, and the sandbox suite
    # proved it end to end: every sandboxed agent has passwordless root on the
    # org disk, so `cp ~/orgtree/.bridge workspace/notes.txt` renames the
    # secret out of the deny tuple and a kiosk visitor downloads it with a 200.
    # That secret opens /api/agent as ANY node of the org and the /anthropic
    # proxy, which attaches the HOST's subscription OAuth token — so this is
    # the whole sandbox boundary, defeated by a copy.
    #
    # Content is therefore checked as well as name, for visitors only: any file
    # carrying this org's bridge secret is refused whatever it is called. The
    # scan is bounded and cheap (the secret is 32 hex chars; a copy of a small
    # JSON file is what this defends against, and a multi-GB file streaming to
    # a visitor is not a credential-shaped object).
    if public:
        secret = sandbox.sandbox_secret(org)
        if secret:
            try:
                with open(full, "rb") as f:
                    head = f.read(_SECRET_SCAN_BYTES)
                if secret.encode() in head:
                    raise HTTPException(403, "credential/secret file")
            except OSError:
                pass          # unreadable: the FileResponse below reports it
    return FileResponse(full, filename=os.path.basename(full))


class DiskDelete(Body):
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


class DiskResize(Body):
    size_mb: int | None = None
    cancel: bool = False       # one-click cancel of a pending shrink (ruled)


def _disk_doc_update(slug: str, **kv: Any) -> None:
    with store.DOC_LOCK:
        o2 = store.load_org(slug)
        d = dict(o2.d.get("disk") or {})
        for k, v in kv.items():
            if v is None:
                d.pop(k, None)
            else:
                d[k] = v
        o2.d["disk"] = d
        store.save_org(o2)


@app.post("/api/orgs/{slug}/disk/resize")
def disk_resize(slug: str, body: DiskResize, request: Request) -> dict[str, Any]:
    """Resize, ADMIN only (it spends/reshapes host disk). GROW applies
    online, immediately, and CLEARS any pending shrink outright (ruled — a
    grow can always apply now). SHRINK becomes a PENDING request persisted
    in the org doc: it applies at the next moment this org's container is
    down (or via /disk/resize/apply), and the UI shows requested vs actual
    until then. A shrink below current usage is refused HERE with the MB to
    free — the same refuse-not-guess rule the apply path enforces."""
    if _public_slug(request):
        raise HTTPException(403, "admin side only")
    org = _disk_org(slug)
    from . import disk as dsk
    d = dict(org.d.get("disk") or {})
    cur = int(d.get("size_mb") or 0)
    if body.cancel:
        _disk_doc_update(slug, pending_size_mb=None)
        return {"size_mb": cur, "pending_mb": None}
    if body.size_mb is None:
        raise HTTPException(422, "size_mb required (or cancel: true)")
    want = int(body.size_mb)
    if want == cur:
        _disk_doc_update(slug, pending_size_mb=None)   # replace/no-op clears
        return {"size_mb": cur, "pending_mb": None}
    if want > cur:
        try:
            dsk.grow(slug, want)
        except dsk.DiskError as e:
            raise HTTPException(503, str(e))
        _disk_doc_update(slug, size_mb=want, pending_size_mb=None)
        supervisor.storage_check(slug)      # a grow may clear blocked/full
        du = dsk.usage(slug, max_age=0.0)
        return {"size_mb": want, "pending_mb": None,
                "used": du[0] if du else None, "total": du[1] if du else None}
    # shrink request: floor + live usage refusal, then stage it
    if want < 4096:
        raise HTTPException(422, "org disks have a 4096 MB minimum (the "
                                 "system seed and transcripts live inside "
                                 "the cap)")
    du = dsk.usage(slug, max_age=0.0)
    if du and du[0] > want * 1048576 * 0.9:
        need = int((du[0] - want * 1048576 * 0.9) / 1048576) + 1
        raise HTTPException(422, f"usage is {du[0] // 1048576} MB — free "
                                 f"about {need} MB before shrinking to "
                                 f"{want} MB")
    # a new request supersedes any earlier one (ruled: replaceable)
    _disk_doc_update(slug, pending_size_mb=want)
    return {"size_mb": cur, "pending_mb": want}


@app.post("/api/orgs/{slug}/disk/resize/apply")
def disk_resize_apply(slug: str, request: Request) -> dict[str, Any]:
    """The BRIDGE (ruled — a pending shrink the operator cannot trigger is a
    wall with a legal sequence behind it): briefly stops THIS org's agents,
    applies the pending shrink, and lets the container restart on the next
    turn. Never touches the backend or other orgs."""
    if _public_slug(request):
        raise HTTPException(403, "admin side only")
    org = _disk_org(slug)
    if not int((org.d.get("disk") or {}).get("pending_size_mb") or 0):
        raise HTTPException(422, "no pending resize")
    from . import disk as dsk
    sandbox.stop_container(slug)
    try:
        note = sandbox.try_apply_pending_resize(org)
    except (dsk.DiskError, RuntimeError) as e:
        raise HTTPException(503, str(e))
    if note:
        raise HTTPException(422, note)     # kept pending — says what to free
    org = store.load_org(slug)
    d = dict(org.d.get("disk") or {})
    du = dsk.usage(slug, max_age=0.0)
    return {"size_mb": int(d.get("size_mb") or 0), "pending_mb": None,
            "used": du[0] if du else None, "total": du[1] if du else None}


# ------------------------------------------- pre-migration backup sweep
def _du_native(path: str) -> int:
    """Host-dir size (native paths only — never point this at UNC)."""
    total = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _legacy_targets(slug: str) -> tuple[list[str], list[str]]:
    """(existing legacy volume names, existing host-dir copies) — the state
    the disk migration copied FROM and kept for rollback."""
    vols = [sandbox.sys_volume(slug, d)
            for d in ("usr", "var", "etc", "opt", "root", "srv")
            if subprocess.run(["docker", "volume", "inspect",
                               sandbox.sys_volume(slug, d)],
                              capture_output=True).returncode == 0]
    dirs = [p for p in (sandbox.sandbox_root(slug),
                        store.workspace_dir(slug), store.scratch_root(slug))
            if os.path.isdir(p)]
    return vols, dirs


@app.get("/api/orgs/{slug}/sweep-legacy")
def sweep_legacy_preview(slug: str, request: Request) -> dict[str, Any]:
    """What the pre-migration backup still costs — admin decides whether to
    drop the rollback. Refuses unless the org's disk is mounted and healthy
    (never delete the backup of a disk that can't prove it's alive)."""
    if _public_slug(request):
        raise HTTPException(403, "admin side only")
    org = _disk_org(slug)
    from . import disk as dsk
    if not dsk.is_mounted(org.d["slug"]):
        raise HTTPException(503, "the org disk is not mounted — not touching "
                                 "its rollback backup")
    vols, dirs = _legacy_targets(slug)
    vol_bytes = sandbox.sandbox_volumes_bytes(slug, max_age=0.0) or 0
    host_bytes = sum(_du_native(p) for p in dirs)
    return {"volumes": vols, "volumes_bytes": vol_bytes,
            "host_dirs": dirs, "host_bytes": host_bytes,
            "total_bytes": vol_bytes + host_bytes}


@app.post("/api/orgs/{slug}/sweep-legacy")
def sweep_legacy(slug: str, request: Request) -> dict[str, Any]:
    """Drop the rollback: legacy volumes + host-dir copies. Explicit admin
    action behind a preview + armed click in the UI — the data lives ON the
    org disk now; this deletes only the pre-migration copies."""
    if _public_slug(request):
        raise HTTPException(403, "admin side only")
    org = _disk_org(slug)
    from . import disk as dsk
    if not dsk.is_mounted(org.d["slug"]):
        raise HTTPException(503, "the org disk is not mounted — not touching "
                                 "its rollback backup")
    vols, dirs = _legacy_targets(slug)
    failures: list[str] = []
    if vols:
        r = subprocess.run(["docker", "volume", "rm", "-f", *vols],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            failures.append((r.stderr or r.stdout)[-200:])
    for p in dirs:
        try:
            shutil.rmtree(p)
        except OSError as e:
            failures.append(f"{p}: {e}")
    return {"removed_volumes": vols, "removed_dirs": dirs,
            "failures": failures}


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
    #
    # A batch drained INTO a turn is the same story one step later: the
    # mailbox no longer has it and the CLI has not echoed it into the
    # transcript yet, so it is surfaced until the transcript does. This is the
    # one place both halves are in hand, so the handover is decided here and
    # lands in ONE payload — the pending bubble goes and the durable @user
    # bubble arrives together, never a frame with neither (D-54).
    _seen_user = [(m.get("text") or "") for m in out["messages"]
                  if m.get("role") == "user"]

    def _in_transcript(m: Mapping[str, Any]) -> bool:
        """Is THIS mail entry already on screen as a transcript bubble?

        Identity, not resemblance. The transcript text is the `_mail_block`
        envelope wrapped around the body, and that envelope carries the
        entry's own `at` immediately before it —

            FROM @user (…) · message · 2026-08-04T04:27:08.545Z
            <body>

        — so the timestamp+body junction names one specific mail. Matching on
        the body alone would repeat D-52's mistake one layer down: re-send
        "continue" and the new entry would match the OLD bubble and be hidden
        while still in flight. No clock is compared, only a string this
        process itself wrote.

        ⚠ The body is used RAW. `_mail_block` writes `f"…· {at}\\n{m['body']}"`
        with no normalisation, so a stripped copy of a body that begins with
        whitespace does not occur in the transcript at all — the test then said
        "not on screen" forever and the pending bubble stayed up ALONGSIDE the
        durable one for the whole of the turn's first response (measured
        2026-08-04 on a real transcript sample: median 2.4 s, max 137 s). The
        composer trims, but nothing else does: the API takes `body.text` as
        sent, and agent mail routinely opens with a newline. Only the emptiness
        guard strips — and only where it must. With an `at` the marker is
        unique whatever the body is, so a whitespace-only message (nothing
        forbids one; only the composer trims) is identified like any other. It
        is only the legacy `at`-less entry that falls back to a bare body, and
        THAT needle must not be empty or it would match every bubble."""
        body = m.get("body") or ""
        at = m.get("at")
        if not at and not body.strip():
            return False
        # the head is enough to identify it and survives truncation either side
        mark = (f"· {at}\n{body}" if at else body)[:400]
        return any(mark in t for t in _seen_user)

    # ⚠ The same evidence test applies to the MAILBOX rows, not only the
    # journal's. `_fold_back_undelivered` re-queues a batch whose delivery was
    # never confirmed — correctly: it is the only thing that puts a
    # consumed-but-unanswered message back where the next envelope re-presents
    # it, so weakening the fold-back itself would buy one clean render at the
    # cost of the agent never being asked again. But when the CLI died AFTER
    # echoing the message into its transcript and BEFORE its first stdout
    # event, the returned row is one the transcript already shows, and the desk
    # rendered both — measured 2026-08-04, 22 of 32 samples, indefinitely.
    # Hiding it HERE removes the duplicate at the display layer and leaves
    # delivery untouched: the marker names one specific entry, so only a row
    # the transcript genuinely carries is dropped from the payload.
    pending = sorted(supervisor.delivering_mail(org, nid, _in_transcript)
                     + [m for m in (org.d.get("mail") or {}).get(nid, [])
                        if not _in_transcript(m)],
                     key=lambda m: m.get("at") or "")
    out["mail_pending"] = len(pending)
    # ⚠ NOT `pending[-20:]`. A fixed row cap on a list that only GROWS while
    # the agent cannot run is the same bug as everything else in this family:
    # the 21st queued message pushed the 1st off the payload, its ghost had
    # long since graduated against the very row that just vanished, and the
    # message was on screen nowhere (measured 2026-08-04: message #0 gone at
    # send #21, and it never comes back until a turn drains it). Every queued
    # message keeps a row; the payload is bounded by SHRINKING BODIES instead,
    # which costs a truncated preview rather than a missing message.
    #
    # Bodies shrink in tiers as the queue grows, so the payload stays bounded
    # (worst case ~200 KB at the 800-row backstop) without any message losing
    # its row. The floor must stay well above the client's graduation needle
    # (`serverCopies` compares the first 200 characters) or a shrunk body would
    # stop matching its own ghost and strand it — a duplicate.
    #
    # ⚠ Residual: past 800 undelivered mails the oldest do fall off. The live
    # mailbox is uncapped (`ledger.post_mail` caps `mail_log`, not `mail`), so
    # SOME backstop has to exist; 800 is far outside anything a user can type
    # and the tier floor keeps the payload survivable if an agent ever spams a
    # frozen node.
    n_pending = len(pending)
    body_cap = 2000 if n_pending <= 20 else 800 if n_pending <= 100 else 250
    pending = pending[-800:]
    out["pending_mail"] = [{"id": m.get("id"), "from": m["from"],
                            "body": m["body"][:body_cap], "at": m["at"],
                            **({"delivering": True} if m.get("delivering")
                               else {}),
                            # the two in-flight carriers read differently to a
                            # human: "mid-task" is only true of a steer
                            **({"via": "turn"} if m.get("via") == "turn"
                               else {}),
                            **({"attachments": m["attachments"]}  # type: ignore[typeddict-item]  # guard proves the key
                               if m.get("attachments") else {})}
                           for m in pending]
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
        msg: dict[str, Any] = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for b in cast("list[Any]", content):
            blk = cast("dict[str, Any]", b)   # identity — typing only
            if not (isinstance(blk, dict) and blk.get("type") == "tool_result"
                    and blk.get("tool_use_id") == tool_use_id):
                continue
            imgs: list[dict[str, Any]] = [
                x for x in cast("list[Any]", blk.get("content") or [])
                if isinstance(x, dict)
                and cast("dict[str, Any]", x).get("type") == "image"]
            if idx < len(imgs):
                src: dict[str, Any] = imgs[idx].get("source") or {}
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
    sent: list[dict[str, Any]] = []
    logs: dict[str, list[MailEntry]] = org.d.get("mail_log") or {}
    for to, lst in logs.items():
        sent += [{**m, "to": to} for m in lst if m["from"] == nid]
    for m in org.d.get("user_inbox", []) + org.d.get("user_mail_log", []):
        if m["from"] == nid:
            sent.append({**m, "to": USER})
    sent.sort(key=lambda m: m["at"])
    return {"pending": waiting, "delivered": delivered[-50:], "sent": sent[-50:]}


@app.get("/api/orgs/{slug}/events")
def org_events(slug: str, request: Request, since: int = 0) -> dict[str, Any]:
    try:
        events = store.load_org(slug).d["events"]
    except LedgerError as e:
        raise HTTPException(404, str(e))
    out = list(events[since:])
    if _public_slug(request):
        out = _scrub_events(out)     # host paths ride event details/warnings
    return {"total": len(events), "events": out}


# ----------------------------------------------------------------------- ops
class Op(Body):
    op: str                       # hire|retire|rehire|dissolve|reallocate|promote|demote|revoke_dir
    actor: str = USER
    node: str | None = None       # target node (all but hire)
    parent: str | None = None     # hire target parent (None = top level)
    tier: str | None = None       # hire
    grant: int | None = None      # hire / rehire / reallocate delta via `delta`
    name: str | None = None       # hire
    charter: str | None = None    # hire — short standing role card
    add_dirs: list[Any] | None = None  # hire — [{path, mode}] or bare paths
    tools: dict[str, Any] | None = None  # hire — {bash, web, edit, subagents, mcp: []}
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
    # Visitor delete is deliberately OPEN (user ruling 2026-08-01, twice
    # confirmed): visitors act as @user for everything inside the ceiling,
    # permanent deletion included — the ceiling is the only wall, and a
    # kiosk org is disposable by design. See DECISIONS.md D-001 (incl. why
    # the cost-is-history tombstone makes this budget-safe). An interim 403
    # lived here for ~25 min while the ruling was pending (2c5af3e).
    if body.op == "rename":
        if not body.node or not body.name:
            raise HTTPException(422, "rename needs node and name")
        try:
            result = supervisor.rename_node(slug, body.node, body.name,
                                            actor=body.actor)
        except LedgerError as e:
            raise HTTPException(422, str(e))
        supervisor.remote_reap(slug)     # FR-01: a rename re-keys the seat
        hub_changed(slug)
        return result
    with store.DOC_LOCK:
        result = _org_op_locked(slug, body, allow_raise=not pub)
    # FR-01 (redteam): retire/dissolve/delete must not orphan a running
    # remote-control server — reap any whose seat is gone or no longer live
    if body.op in ("retire", "dissolve", "delete"):
        supervisor.remote_reap(slug)
    if pub and isinstance(result, dict):
        # the bridge is the ADMIN affordance — a visitor has no legal path to
        # raise the ceiling, so the offer must not dangle
        result.pop("bridge", None)
    # rehire with a waiting mailbox: the mail queued while archived finally
    # gets acted on (user ruling) — drive outside the doc lock
    drive: list[str] = result.pop("drive", []) if isinstance(result, dict) else []
    for t in drive:
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
        # ⚠ never cached. Asset filenames are content-hashed, so /assets/* may
        # be held forever — but index.html is the file that NAMES them, and a
        # browser that reuses a stale copy pulls the previous bundle straight
        # back after the reload the instance change just triggered. That would
        # make the refresh look like it did nothing.
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"),
                            headers={"Cache-Control": "no-store"})


EXPOSE_ENV = "ORGTREE_EXPOSE_ADMIN"
_TRUTHY = {"1", "true", "yes", "on"}


def _admin_host() -> str:
    """Where the ADMIN listener binds.

    Loopback by design — the admin app has no authentication of any kind,
    because "you can reach 127.0.0.1" has always been the whole credential.
    Exposing it hands anyone who finds the port the same powers the owner has:
    read and write every org, grant any folder on this machine to an agent,
    and run turns that execute commands on it.

    The override is an ENV VAR (user ruling 2026-08-04, superseding the
    argv-only ruling of 2026-08-03 recorded in D-39). The reason it moved: a
    service definition — Task Scheduler, a systemd unit — sets environment
    naturally and threading an argv flag through the deploy scripts to a
    detached process is the awkward path. Unattended hosts are the case that
    needs this, so the mechanism should suit them.

    ⚠ What the old ruling was protecting against is still true and is now
    handled elsewhere: env vars are INHERITED by child processes, so every
    agent CLI would see this one through `supervisor.clean_env()`. It is
    stripped there (it is not the agent's business whether the host is
    exposed) — see that function.

    Still deliberately NOT a setting in the org doc: a setting can be flipped
    by anything that can write the doc, including an agent.
    """
    return ("0.0.0.0" if os.environ.get(EXPOSE_ENV, "").strip().lower() in _TRUTHY
            else "127.0.0.1")


def _ws_impl() -> str | None:
    """Which WebSocket implementation uvicorn will find, if any.

    Plain `uvicorn` has none, and the resulting failure is SILENT: an upgrade
    request falls through to the SPA catch-all and answers 200 OK with HTML, so
    the browser's socket simply never opens and reconnect-loops forever. Every
    HTTP route keeps working, the UI falls back to its polling heartbeats, and
    the only symptom is that everything feels slightly slow — which is exactly
    how a user lost time to it on a second machine (2026-08-03). A dependency
    nothing imports, whose absence produces no error, has to be checked for
    explicitly or it is undiscoverable.
    """
    for mod in ("websockets", "wsproto"):
        if importlib.util.find_spec(mod) is not None:
            return mod
    return None


def main() -> None:
    import uvicorn

    # ⚠ ONE BACKEND PER DATA ROOT — enforced here because this is the only
    # moment it is cheap and safe. MEASURED (test_compaction.py "xproc"): two
    # processes running the canonical load → mutate → save cycle against one
    # org doc lose 44–50 % of their COMPLETED writes, four processes 62–82 %,
    # with zero exceptions, zero torn reads and zero orphaned temp files. Both
    # existing guards are per-process and `os.replace` is atomic, which is
    # exactly why the loss is silent: every writer is told it succeeded.
    #
    # A lock around save_org would not help — the race is the read-modify-write
    # CYCLE, so a correct lock would have to span load → save, i.e. regions
    # that spawn CLI children and stay held for a 600 s compaction fork. That
    # is a deadlock surface. Claiming the root at startup is the whole fix.
    try:
        store.claim_data_root()
    except store.DataRootBusy as e:
        bar = "!" * 74
        print(f"\n{bar}\n"
              f"  ANOTHER ORGTREE BACKEND ALREADY OWNS THIS DATA ROOT\n"
              f"\n"
              f"  {e}\n"
              f"\n"
              f"  Two backends on one data root silently DISCARD each other's\n"
              f"  writes — measured at 44-82% of completed saves lost, with no\n"
              f"  error on either side. Stop the other one, or point this one\n"
              f"  at a different ORGTREE_DATA.\n"
              f"{bar}\n", flush=True)
        raise SystemExit(1)

    if _ws_impl() is None:
        bar = "!" * 74
        print(f"\n{bar}\n"
              "  NO WEBSOCKET LIBRARY — the live UI will be DEGRADED, not broken.\n"
              "\n"
              "  uvicorn has no WebSocket implementation installed, so pushed\n"
              "  updates cannot reach the browser. Everything still works; it\n"
              "  falls back to polling, so every action lags by up to one poll.\n"
              "\n"
              "  Fix:  pip install -r requirements.txt      (or: pip install websockets)\n"
              f"{bar}\n", flush=True)

    host = _admin_host()
    if host != "127.0.0.1":
        # not a log line — a wall. Whoever typed the flag should see exactly
        # what they turned off, and anyone reading the console later should be
        # able to tell at a glance that this process is wide open.
        bar = "!" * 74
        print(f"\n{bar}\n"
              f"  {EXPOSE_ENV}=1: THE ADMIN API IS BOUND TO {host}:{PORT}\n"
              f"\n"
              f"  It has NO password, NO token and NO login. Anyone who can\n"
              f"  reach this port has full control of every org and can make\n"
              f"  agents run commands on this machine.\n"
              f"\n"
              f"  Only do this behind a VPN, an SSH tunnel or an authenticating\n"
              f"  reverse proxy. To share an org with someone instead, make it\n"
              f"  a kiosk: that serves one org over a secret URL with limits.\n"
              f"{bar}\n", flush=True)

    # three listeners, three trust levels: the admin app is LOOPBACK-ONLY
    # unless the operator typed the flag above (user vision: root access never
    # reaches the wider web); the public listener serves nothing but
    # preauthenticated /k/<token> URLs; the bridge listener serves nothing but
    # secret-gated sandbox traffic
    servers = [uvicorn.Server(uvicorn.Config(app, host=host, port=PORT))]
    if PUBLIC_PORT:
        servers.append(uvicorn.Server(uvicorn.Config(
            PublicGateway(app), host="0.0.0.0", port=PUBLIC_PORT)))
    if sandbox.BRIDGE_PORT:
        servers.append(uvicorn.Server(uvicorn.Config(
            BridgeGateway(app), host="0.0.0.0", port=sandbox.BRIDGE_PORT)))
    if len(servers) == 1:
        uvicorn.run(app, host=host, port=PORT)
        return

    async def serve_all() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(serve_all())


if __name__ == "__main__":
    main()
