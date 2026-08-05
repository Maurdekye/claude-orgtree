"""Hub entrypoint: the full app (UI + API) on HUB_PORT, and — when
HUB_PUBLIC is set — the FR-10 public listener (API-only, see public.py) on
the fixed internal port 7371. Compose maps that to a host port; the expose
tooling tunnels the HOST side, never 7370.

    python -m mailhub.serve
"""

from __future__ import annotations

import asyncio
import os

import uvicorn

from .app import app
from .public import PublicHub


def main() -> None:
    port = int(os.environ.get("HUB_PORT", "7370"))
    servers = [uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port))]
    if (os.environ.get("HUB_PUBLIC") or "").strip():
        servers.append(uvicorn.Server(uvicorn.Config(
            PublicHub(app), host="0.0.0.0", port=7371)))

    async def serve_all() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    if len(servers) == 1:
        uvicorn.run(app, host="0.0.0.0", port=port)
        return
    asyncio.run(serve_all())


if __name__ == "__main__":
    main()
