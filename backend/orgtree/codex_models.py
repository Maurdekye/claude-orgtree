# pyright: strict
"""Which Codex models the signed-in account is CURRENTLY offered.

`providers.codex_status` answers "is Codex installed and who is signed in" — a
property of the MACHINE that changes when the user runs `codex login`.  This
module answers a different, faster-moving question: of the models orgtree's
tiers map to, which ones is OpenAI serving to THIS account right now.

It exists for `gpt-reserve`.  Reserve capacity is not a property of the login:
it is a pool OpenAI grants and withdraws per account, on its own schedule, and
while it is granted the CLI bills reserve turns to a separate weekly window
(measured 2026-09-02: a gpt-reserve session reported `limit_id "codex"` at 2%
resetting Sep 9, while the account's own plan window sat spent at 100%
resetting Sep 7; three hours later the same tier reported `limit_id "premium"`
with no windows at all and every turn failed `usage_limit_exceeded`).  Nothing
about the login changed across that transition — so a login-kind check cannot
see it, which is the user's report.

What DOES see it is the CLI's own model registry, which is fetched per account
and carries a `visibility` per model.  When the grant went away, `gpt-reserve`
flipped to `visibility: "hide"` and vanished from the app-server's `model/list`
while sol/terra/luna stayed listed.  A model the Codex CLI will not itself
offer is not a tier orgtree should offer.

TWO READS, cheapest first, because this sits behind a UI poll:

  · `$CODEX_HOME/models_cache.json` — the CLI's own registry cache, rewritten
    (etag-conditional, so nearly free for the CLI) on essentially every run.
    A plain file read: no subprocess, no network, no auth material touched.
  · the app-server's `model/list` — authoritative and live, but a process
    spawn, so it is only reached when the file is missing or has gone stale.

UNKNOWN IS NOT "NO".  If neither read produces fresh evidence, `offers()`
answers `None` and the reserve gate leaves the tier alone.  Detection that
failed closed would brick a working machine every time Codex changed a file
format, and the CLI still refuses an ungranted turn loudly on its own.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
from typing import Any, Final

from . import codexrun, providers

#: our own in-process cache.  Long enough that a polling UI never spawns an
#: app-server per tick, short enough that a grant appearing or disappearing is
#: visible within minutes rather than at the next restart.
CACHE_TTL: Final = 300.0
#: how old the CLI's registry file may be before it stops counting as
#: evidence — the same 15 minutes `codex_limits` allows a usage board.
FILE_MAX_AGE: Final = 900.0
FETCH_TIMEOUT: Final = 15.0

_lock = threading.Lock()
_fetch_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _iso(epoch: float) -> str | None:
    if epoch <= 0:
        return None
    try:
        return _dt.datetime.fromtimestamp(
            epoch, tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _parse_fetched_at(value: Any) -> float | None:
    """The registry's own `fetched_at` (RFC3339, `Z`, sub-second precision).

    Its timestamp beats the file's mtime because the CLI rewrites the file on
    a conditional GET: mtime says "we asked", `fetched_at` says "this is when
    the answer was current", and the two can differ.  `fromisoformat` takes at
    most six fractional digits and the CLI writes seven, so the fraction is
    trimmed rather than the whole read being thrown away.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        while len(digits) < len(tail) and tail[len(digits)].isdigit():
            digits += tail[len(digits)]
        text = head + "." + digits[:6] + tail[len(digits):]
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


def registry_path(codex_home: str | None = None) -> str:
    """Where the CLI keeps its model registry cache."""
    home = (codex_home or os.environ.get("CODEX_HOME")
            or os.path.expanduser("~/.codex"))
    return os.path.join(home, "models_cache.json")


def _from_registry_file(codex_home: str | None) -> dict[str, Any] | None:
    """The CLI's cached registry — which slugs it offers, and as of when."""
    path = registry_path(codex_home)
    try:
        with open(path, encoding="utf-8") as f:
            doc: dict[str, Any] = json.load(f)
    except (OSError, ValueError):
        return None
    models = doc.get("models")
    if not isinstance(models, list):
        return None
    offered: list[str] = []
    hidden: list[str] = []
    for entry in models:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")  # pyright: ignore[reportUnknownMemberType]
        if not isinstance(slug, str) or not slug:
            continue
        visibility = entry.get("visibility")  # pyright: ignore[reportUnknownMemberType]
        # only an explicit "hide" withholds a model.  A missing or
        # unrecognised visibility reads as offered: a new value from a newer
        # CLI must not silently retract a tier the user can still hire.
        (hidden if visibility == "hide" else offered).append(slug)
    if not offered and not hidden:
        return None
    observed = _parse_fetched_at(doc.get("fetched_at"))
    if observed is None:
        try:
            observed = os.path.getmtime(path)
        except OSError:
            return None
    return {"offered": offered, "hidden": hidden, "observed_at": observed,
            "source": "registry file"}


def _from_app_server(status: dict[str, Any]) -> dict[str, Any] | None:
    """`model/list` over the CLI's documented app-server protocol.

    The server returns only what it would offer — hidden models are filtered
    on its side (measured: `gpt-reserve` and `codex-auto-review`, both
    `visibility: "hide"` in the registry file, are simply absent) — so ABSENCE
    is the signal here and there is no hidden list to report.
    """
    exe, _source = providers.codex_path()
    if not exe:
        return None
    client: codexrun.AppServerClient | None = None
    try:
        client = codexrun.AppServerClient(
            providers.codex_argv(exe),
            codex_home=str(status.get("codex_home") or "") or None)
        client.initialize()
        raw = client.request("model/list", {}, FETCH_TIMEOUT)
        rows = raw.get("data")
        if not isinstance(rows, list):
            return None
        offered: list[str] = []
        for row in rows:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(row, dict) or row.get("hidden"):  # pyright: ignore[reportUnknownMemberType]
                continue
            slug = row.get("id") or row.get("model")  # pyright: ignore[reportUnknownMemberType]
            if isinstance(slug, str) and slug:
                offered.append(slug)
        if not offered:
            return None
        return {"offered": offered, "hidden": [], "observed_at": time.time(),
                "source": "app-server"}
    except Exception:  # noqa: BLE001 — a protocol failure is unknown, not "no"
        return None
    finally:
        if client is not None:
            client.close()


def _unknown(error: str) -> dict[str, Any]:
    return {"available": False, "offered": [], "hidden": [], "error": error,
            "source": None, "observed_at": None, "age": None, "stale": False}


def _decorate(data: dict[str, Any], now: float) -> dict[str, Any]:
    out = dict(data)
    out["offered"] = list(data.get("offered") or [])
    out["hidden"] = list(data.get("hidden") or [])
    observed = float(data.get("observed_at") or 0.0)
    age = max(0.0, now - observed) if observed > 0 else None
    out["observed_at"] = _iso(observed)
    out["age"] = age
    out["stale"] = bool(age is not None and age > FILE_MAX_AGE)
    return out


def snapshot(force: bool = False) -> dict[str, Any]:
    """The offered-model board, cached for `CACHE_TTL`.

    `available` is about the EVIDENCE, not about any one model: False means
    this machine could not be asked, and callers must then treat every model
    as unknown rather than as withheld.
    """
    now = time.time()
    with _lock:
        cached = _cache.get("data")
        if (not force and isinstance(cached, dict)
                and now - float(_cache["at"]) <= CACHE_TTL):
            return _decorate(cached, now)
    status = providers.codex_status()
    if not status.get("installed"):
        return _unknown("Codex CLI is not installed")
    if not status.get("connected"):
        return _unknown("Codex CLI is not signed in")
    home = str(status.get("codex_home") or "") or None

    with _fetch_lock:
        now = time.time()
        with _lock:
            cached = _cache.get("data")
            if (not force and isinstance(cached, dict)
                    and now - float(_cache["at"]) <= CACHE_TTL):
                return _decorate(cached, now)
        found = _from_registry_file(home)
        fresh = (found is not None
                 and now - float(found["observed_at"]) <= FILE_MAX_AGE)
        if not fresh:
            # the file is absent or has aged out — pay for the live read
            # rather than answer from evidence nobody should act on.
            found = _from_app_server(status) or found
        if found is None:
            return _unknown(
                "Codex has not published a model list on this machine")
        data: dict[str, Any] = {"available": True, **found}
        with _lock:
            _cache.update(at=time.time(), data=data)
        return _decorate(data, time.time())


def offers(slug: str, force: bool = False) -> bool | None:
    """Is `slug` offered to the signed-in account right now?

    `None` is "no fresh evidence either way", and it is NOT a refusal — see
    the module docstring: the reserve gate leaves the tier alone on `None`.
    """
    board = snapshot(force)
    if not board.get("available") or board.get("stale"):
        return None
    return slug in (board.get("offered") or [])


def invalidate() -> None:
    with _lock:
        _cache.update(at=0.0, data=None)
