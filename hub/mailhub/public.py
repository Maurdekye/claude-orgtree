"""FR-10: the hub's PUBLIC face — a route split, not a blind tunnel.

The hub's own trust model (README): the UI at `/` is a read-only,
UNAUTHENTICATED view of every org's traffic — hub access IS read access to
all mail. Tunnelling port 7370 raw would publish that. What a remote client
actually needs is only `/api/*` (every route there is gated on the caller's
own org secret via X-Org-Auth) plus `/healthz` for reachability checks — so
the public listener serves exactly that and 404s everything else, mirroring
the split orgtree's admin app makes between its loopback surface and the
kiosk PublicGateway.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine


class PublicHub:
    """ASGI wrapper: /api/* and /healthz pass through; all else 404s."""

    def __init__(self, inner: Callable[..., Coroutine[Any, Any, None]]) -> None:
        self.inner = inner

    async def __call__(self, scope: dict[str, Any], receive: Any,
                       send: Any) -> None:
        st = str(scope.get("type") or "")
        if st == "lifespan":                  # startup/shutdown plumbing
            await self.inner(scope, receive, send)
            return
        if st != "http":
            # redteam FR-10 finding (2026-08-05): only http and lifespan
            # scopes may reach the inner app through the public face. A
            # websocket (none exists today) — or whatever scope type ASGI
            # grows next — must be admitted here DELIBERATELY, not
            # inherited the day someone adds a live-feed route to the
            # operator UI.
            if st == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        path = str(scope.get("path") or "")
        if not (path.startswith("/api/") or path == "/healthz"):
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"not found"})
            return
        await self.inner(scope, receive, send)
