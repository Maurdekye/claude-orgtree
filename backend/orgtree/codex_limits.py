# pyright: strict
"""Codex subscription rate-limit usage, read through the local app-server.

Codex owns the signed-in account and its refreshed credentials.  Orgtree asks
the CLI's documented app-server protocol (`account/rateLimits/read`) and never
reads or copies auth material.  The normalized output deliberately matches the
Claude usage-bar shape so the frontend has one renderer and one severity rule.
"""

from __future__ import annotations

import datetime as _dt
import threading
import time
from typing import Any, Final

from . import codexrun, providers

CACHE_TTL: Final = 30.0
FETCH_TIMEOUT: Final = 15.0
MAX_EVIDENCE_AGE: Final = 900.0

_lock = threading.Lock()
_fetch_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "data": None}
# Full snapshots by limit id.  Rolling notifications are sparse, so they merge
# into this board rather than replacing it and accidentally erasing a window.
_snapshots: dict[str, dict[str, Any]] = {}


def _iso(epoch: Any) -> str | None:
    try:
        value = float(epoch)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        return _dt.datetime.fromtimestamp(
            value, tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _duration(minutes: Any) -> str:
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        return "usage window"
    if value > 0 and value % 1440 == 0:
        days = value // 1440
        return f"{days} day" + ("s" if days != 1 else "")
    if value > 0 and value % 60 == 0:
        hours = value // 60
        return f"{hours} hour" + ("s" if hours != 1 else "")
    return f"{value} min" if value > 0 else "usage window"


def _window_kind(minutes: Any, scoped: bool) -> str:
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        return "codex_window"
    if value <= 6 * 60:
        return "session"
    if value >= 6 * 24 * 60:
        return "weekly_scoped" if scoped else "weekly_all"
    return "codex_window"


def _severity(percent: float) -> str:
    if percent >= 90:
        return "critical"
    if percent >= 75:
        return "warning"
    return "normal"


def _snapshots_of(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Canonical bucket first, then any additional named/model buckets."""
    primary = raw.get("rateLimits")
    by_id = raw.get("rateLimitsByLimitId")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(primary, dict):
        out.append(primary)
        seen.add(str(primary.get("limitId") or "codex"))
    if isinstance(by_id, dict):
        for key, value in by_id.items():
            if not isinstance(value, dict):
                continue
            limit_id = str(value.get("limitId") or key)
            if limit_id in seen:
                continue
            out.append(value)
            seen.add(limit_id)
    return out


def _credits(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The account's spend-anyway pocket, as the CLI reports it.

    Codex keeps this beside the windows because it is what a spent window
    falls back to: `{"hasCredits": false, "unlimited": false, "balance": "0"}`
    is the shape a turn hits right before it fails `usage_limit_exceeded`,
    while credits (or an unlimited account) mean a full window is not the end
    of the story.  `_normalize` used to drop the field entirely, which made
    "out of capacity" unanswerable from the board.
    """
    raw = snapshot.get("credits")
    if not isinstance(raw, dict):
        return {"known": False, "has": False, "unlimited": False,
                "balance": None}
    balance = raw.get("balance")  # pyright: ignore[reportUnknownMemberType]
    return {
        "known": True,
        "has": bool(raw.get("hasCredits")),  # pyright: ignore[reportUnknownMemberType]
        "unlimited": bool(raw.get("unlimited")),  # pyright: ignore[reportUnknownMemberType]
        "balance": str(balance) if balance is not None else None,
    }


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    snapshots = _snapshots_of(raw)
    limits: list[dict[str, Any]] = []
    plan: str | None = None
    # `_snapshots_of` puts the ACCOUNT-WIDE bucket first (`raw["rateLimits"]`,
    # `limitId "codex"` here) and any model-scoped bucket after it.  Only the
    # first one speaks for the whole account: `codex_bengalfox` (Spark) being
    # spent says nothing about whether a Sol turn can run.
    account = snapshots[0] if snapshots else {}
    blocked = bool(account.get("rateLimitReachedType")
                   or account.get("spendControlReached"))
    credits = _credits(account)
    for snapshot in snapshots:
        limit_id = str(snapshot.get("limitId") or "codex")
        limit_name_raw = snapshot.get("limitName")
        limit_name = (str(limit_name_raw).strip()
                      if limit_name_raw is not None else "")
        raw_plan = snapshot.get("planType")
        if not plan and isinstance(raw_plan, str) and raw_plan:
            plan = raw_plan.replace("_", " ").replace("prolite", "pro lite").title()
        reached = bool(snapshot.get("rateLimitReachedType"))
        for slot in ("primary", "secondary"):
            window = snapshot.get(slot)
            if not isinstance(window, dict):
                continue
            try:
                percent = max(0.0, min(100.0,
                    float(window.get("usedPercent") or 0)))
            except (TypeError, ValueError):
                continue
            duration = window.get("windowDurationMins")
            limits.append({
                "kind": _window_kind(duration, bool(limit_name)),
                "group": limit_id,
                "percent": percent,
                "severity": _severity(percent),
                "resets_at": _iso(window.get("resetsAt")),
                "is_active": reached or percent >= 100,
                "model": limit_name or None,
                "label": ((limit_name + " · ") if limit_name else "")
                         + _duration(duration),
            })
    account_windows = [x for x in limits
                       if x["group"] == str(account.get("limitId") or "codex")]
    return {
        "available": bool(limits),
        "limits": limits,
        "plan": plan,
        "credits": credits,
        # THE question a hire gate asks: can a turn on this account run at
        # all right now?  A window at 100% (or an explicit reached/spend-stop
        # flag) with nothing to spend past it is a turn that fails on send —
        # measured 2026-09-02, `usage_limit_exceeded` on the first message.
        "exhausted": bool(
            (blocked or any(x["percent"] >= 100 for x in account_windows))
            and not (credits["has"] or credits["unlimited"])),
    }


def _account(data: dict[str, Any]) -> dict[str, Any]:
    status = providers.codex_status()
    return {
        "account": "codex",
        "label": status.get("email") or "signed-in account",
        "provider": "Codex",
        **data,
    }


def fetch(force: bool = False) -> dict[str, Any]:
    """Read Codex usage, cached for the modal's polling cadence."""
    now = time.time()
    with _lock:
        cached = _cache.get("data")
        if not force and cached is not None and now - float(_cache["at"]) <= CACHE_TTL:
            return _account(dict(cached))
    status = providers.codex_status()
    if not status.get("installed"):
        return _account({"available": False, "error": "Codex CLI is not installed"})
    if not status.get("connected"):
        return _account({"available": False, "error": "Codex CLI is not signed in"})
    if status.get("kind") == "api-key":
        return _account({
            "available": False,
            "error": "subscription usage is not available for an API-key login",
        })

    with _fetch_lock:
        now = time.time()
        with _lock:
            cached = _cache.get("data")
            if (not force and cached is not None
                    and now - float(_cache["at"]) <= CACHE_TTL):
                return _account(dict(cached))
        exe, _source = providers.codex_path()
        if not exe:
            return _account({"available": False, "error": "Codex CLI is not installed"})
        client: codexrun.AppServerClient | None = None
        try:
            client = codexrun.AppServerClient(
                providers.codex_argv(exe),
                codex_home=str(status.get("codex_home") or "") or None)
            client.initialize()
            raw = client.request("account/rateLimits/read", {}, FETCH_TIMEOUT)
            data = _normalize(raw)
            if not data["available"]:
                data["error"] = "Codex reported no usage-limit windows"
            with _lock:
                _snapshots.clear()
                for snapshot in _snapshots_of(raw):
                    limit_id = str(snapshot.get("limitId") or "codex")
                    _snapshots[limit_id] = dict(snapshot)
                _cache.update(at=time.time(), data=data)
            return _account(dict(data))
        except Exception as e:  # noqa: BLE001 — protocol failures degrade the panel
            with _lock:
                stale = _cache.get("data")
            if isinstance(stale, dict):
                return _account({**stale, "error": f"Codex usage refresh failed: {e}"})
            return _account({"available": False,
                             "error": f"Codex usage fetch failed: {e}"})
        finally:
            if client is not None:
                client.close()


def _merge_sparse(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    merged = dict(old)
    for key, value in new.items():
        if value is None:
            continue
        if key in ("primary", "secondary") and isinstance(value, dict):
            prior = merged.get(key)
            merged[key] = {**(prior if isinstance(prior, dict) else {}), **value}
        else:
            merged[key] = value
    return merged


def observe(snapshot: Any) -> None:
    """Fold a turn's sparse `account/rateLimits/updated` notification."""
    if not isinstance(snapshot, dict):
        return
    limit_id = str(snapshot.get("limitId") or "codex")
    with _lock:
        _snapshots[limit_id] = _merge_sparse(_snapshots.get(limit_id, {}), snapshot)
        ordered = list(_snapshots.values())
        if not ordered:
            return
        raw = {"rateLimits": _snapshots.get("codex", ordered[0]),
               "rateLimitsByLimitId": dict(_snapshots)}
        _cache.update(at=time.time(), data=_normalize(raw))


def exhausted() -> bool | None:
    """Is this account out of Codex capacity RIGHT NOW — cache-only.

    `None` means "no fresh evidence", and it is deliberately not `True`: this
    answer gates hiring, and a board nobody has refreshed in a quarter of an
    hour must not take a tier away.  Freshness is the same
    `MAX_EVIDENCE_AGE` the glow uses, and the board is kept warm by both
    doors already — the usage modal's `fetch` and every live Codex turn's
    `observe` notification.
    """
    with _lock:
        data = _cache.get("data")
        age = time.time() - float(_cache.get("at") or 0)
        copied = dict(data) if isinstance(data, dict) else None
    if copied is None or not copied.get("available") or age > MAX_EVIDENCE_AGE:
        return None
    value = copied.get("exhausted")
    return bool(value) if value is not None else None


def peek() -> dict[str, Any]:
    """Cache-only read for the always-on header warning glow."""
    with _lock:
        data = _cache.get("data")
        age = time.time() - float(_cache.get("at") or 0)
        copied = dict(data) if isinstance(data, dict) else None
    if copied is None or not copied.get("available"):
        return {"available": False, "provider": "Codex"}
    if age > MAX_EVIDENCE_AGE:
        return {"available": False, "provider": "Codex",
                "error": "Codex usage readout is stale"}
    return {"available": True, "provider": "Codex",
            "limits": copied.get("limits") or [], "age": round(age, 1)}


def snapshot(now: float | None = None) -> dict[str, Any]:
    """Cache-only, timestamped Codex evidence for dynamic turn envelopes.

    A stale board remains visible with an explicit stale marker; no app-server
    process is started here.  The returned records are copies so formatting a
    turn can never mutate the modal/glow cache.
    """
    now = time.time() if now is None else now
    with _lock:
        raw = _cache.get("data")
        observed = float(_cache.get("at") or 0.0)
        if not isinstance(raw, dict):
            return {"available": False, "provider": "Codex", "limits": [],
                    "observed_at": None, "age": None, "stale": False}
        data = dict(raw)
        data["limits"] = [dict(x) for x in raw.get("limits") or []
                          if isinstance(x, dict)]
    age = max(0.0, now - observed) if observed > 0 else None
    data.update(provider="Codex", observed_at=_iso(observed), age=age,
                stale=bool(age is not None and age > MAX_EVIDENCE_AGE))
    return data


def invalidate() -> None:
    with _lock:
        _cache.update(at=0.0, data=None)
        _snapshots.clear()
