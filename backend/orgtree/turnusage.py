# pyright: strict
"""Secret-free provider usage evidence for dynamic agent turn envelopes.

This module only reads caches and durable local routing state.  It never
fetches provider usage, opens a CLI, or sees a credential.  The rendered text
belongs in a user event; it must never be folded into the managed identity,
tool schema, argv, environment, session id, or warm-process hash.
"""

from __future__ import annotations

import datetime as _dt
import math
import time
from typing import Any, Final, cast

from . import accounts, codex_limits, limits
from .ledger import Org

OPEN: Final = "[PROVIDER USAGE"
CLOSE: Final = "[END PROVIDER USAGE]"

_PROVIDER_ORDER: Final = {"claude": 0, "codex": 1, "gemini": 2}
_WINDOW_ORDER: Final = {
    "session": 0,
    "weekly_all": 1,
    "weekly_scoped": 2,
    "codex_window": 3,
    "provider_window": 4,
    "usage": 5,
}
_SAFE_MODELS: Final = {
    "fable", "opus", "sonnet", "haiku",
    "sol", "terra", "luna", "flash", "pro",
}


def _iso(epoch: float) -> str:
    try:
        return (_dt.datetime.fromtimestamp(epoch, _dt.timezone.utc)
                .isoformat(timespec="seconds").replace("+00:00", "Z"))
    except (OverflowError, OSError, ValueError):
        return "-"


def _epoch(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    try:
        number = parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _countdown(epoch: float, now: float) -> str:
    delta = epoch - now
    sign = "+" if delta >= 0 else "-"
    seconds = int(abs(delta))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        body = f"{days}d{hours}h"
    elif hours:
        body = f"{hours}h{minutes}m"
    elif minutes:
        body = f"{minutes}m"
    else:
        body = "<1m"
    return sign + body


def _reset_cell(value: Any, now: float) -> str:
    epoch = _epoch(value)
    return "-" if epoch is None else f"{_iso(epoch)} ({_countdown(epoch, now)})"


def _percent(value: Any) -> tuple[float | None, str]:
    if isinstance(value, bool):
        return None, "unavailable"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "unavailable"
    if not math.isfinite(number) or not 0.0 <= number <= 100.0:
        return None, "unavailable"
    shown = f"{number:.1f}".rstrip("0").rstrip(".")
    return number, shown + "%"


def _window(limit: dict[str, Any]) -> str:
    raw = str(limit.get("kind") or "")
    kind = raw if raw in _WINDOW_ORDER else "provider_window"
    model = str(limit.get("model") or "").strip().lower()
    if model in _SAFE_MODELS:
        return f"{kind}:{model}"
    return kind


def _seen(snapshot: dict[str, Any], now: float) -> tuple[str, bool]:
    observed = _epoch(snapshot.get("observed_at"))
    age_raw = snapshot.get("age")
    try:
        age = float(age_raw) if age_raw is not None else None
    except (TypeError, ValueError):
        age = None
    if age is not None and (not math.isfinite(age) or age < 0):
        age = None
    if observed is None and age is not None:
        observed = max(1.0, now - age)
    stale = bool(snapshot.get("stale"))
    if observed is None:
        return "-", stale
    age_text = "?" if age is None else f"{int(age)}s"
    freshness = "stale" if stale else "fresh"
    return f"{_iso(observed)} ({age_text},{freshness})", stale


def _line(provider: str, lane: str, window: str, used: str,
          amount: str, reset: str, observed: str, state: str,
          *, selected: bool = False) -> str:
    marker = "*" if selected else ""
    return (f"{provider}/{lane}{marker} | {window} | {used} | {amount} | "
            f"{reset} | {observed} | {state}")


def _row_order(item: tuple[tuple[Any, ...], str]) -> tuple[Any, ...]:
    """Provider order plus Claude's semantic account priority.

    Lexical order would put ``fallback-1`` before ``primary``.  The account
    registry's priority is the fact agents need: primary, fallback ordinal,
    then this org's API-key lane.
    """
    key = item[0]
    provider_rank = int(cast(int, key[0]))
    lane = str(key[1])
    lane_rank = 0
    if provider_rank == _PROVIDER_ORDER["claude"]:
        if lane == "primary":
            lane_rank = 0
        elif lane.startswith("fallback-"):
            try:
                lane_rank = int(lane.split("-", 1)[1])
            except ValueError:
                lane_rank = 90
        elif lane == "fallbacks":
            lane_rank = 90
        elif lane == "org-api-key":
            lane_rank = 100
        else:
            lane_rank = 99
    return (provider_rank, lane_rank, lane, *key[2:])


def _cached_rows(snapshot: dict[str, Any], provider: str, lane: str,
                 now: float, selected: bool, frozen: bool,
                 freeze_reset: Any = None) -> list[tuple[tuple[Any, ...], str]]:
    observed, stale = _seen(snapshot, now)
    raw_limits = snapshot.get("limits")
    limits_list = ([cast("dict[str, Any]", x) for x in raw_limits
                    if isinstance(x, dict)]
                   if isinstance(raw_limits, list) else [])
    if not snapshot.get("available") or not limits_list:
        reason = ("unavailable(stale)" if stale else
                  "unavailable(no-cache)")
        state = "frozen" if selected and frozen else (
            "stale" if stale else "unavailable")
        line = _line(provider, lane, "usage", reason, "-",
                     _reset_cell(freeze_reset, now) if frozen else "-",
                     observed, state, selected=selected)
        return [((_PROVIDER_ORDER[provider], lane, 99, "", 0), line)]

    normalized: list[tuple[str, float | None, str, str, bool]] = []
    for limit in limits_list:
        window = _window(limit)
        pct, shown = _percent(limit.get("percent"))
        reset = _reset_cell(limit.get("resets_at"), now)
        normalized.append((window, pct, shown, reset,
                           bool(limit.get("is_active"))))
    normalized.sort(key=lambda row: (
        _WINDOW_ORDER.get(row[0].split(":", 1)[0], 98), row[0],
        row[3], -1.0 if row[1] is None else row[1], row[4]))
    counts: dict[str, int] = {}
    out: list[tuple[tuple[Any, ...], str]] = []
    for window, pct, shown, reset, active in normalized:
        counts[window] = counts.get(window, 0) + 1
        display = window if counts[window] == 1 else f"{window}#{counts[window]}"
        if selected and frozen:
            state = "frozen"
            reset = _reset_cell(freeze_reset, now) if freeze_reset else reset
        elif stale:
            state = "stale"
        elif active or (pct is not None and pct >= 100.0):
            state = "limit-active"
        else:
            state = "ready"
        base = display.split(":", 1)[0].split("#", 1)[0]
        key = (_PROVIDER_ORDER[provider], lane,
               _WINDOW_ORDER.get(base, 98), display,
               -1.0 if pct is None else pct)
        out.append((key, _line(provider, lane, display, shown, "-", reset,
                               observed, state, selected=selected)))
    return out


def _fallback_rows(now: float, selected_lane: str,
                   frozen: bool, freeze_reset: Any) -> list[tuple[tuple[Any, ...], str]]:
    try:
        doc = accounts.load()
    except Exception:  # noqa: BLE001 - one telemetry source cannot block a turn
        return [((0, "fallbacks", 99, "", 0),
                 _line("claude", "fallbacks", "usage",
                       "unavailable(telemetry-error)", "-", "-", "-",
                       "unavailable"))]
    rows: list[tuple[tuple[Any, ...], str]] = []
    keys = doc.get("keys")
    for ordinal, raw in enumerate(keys if isinstance(keys, list) else [], 1):
        if not isinstance(raw, dict):
            continue
        account = str(raw.get("id") or "")
        lane = f"fallback-{ordinal}"
        try:
            standings = accounts.tier_standing(doc, account, now)
        except Exception:  # noqa: BLE001
            standings = []
        grouped: dict[tuple[tuple[str, ...], bool, str], None] = {}
        for standing in standings:
            tier = str(standing.get("tier") or "")
            pool_raw = standing.get("pool")
            pool = tuple(str(x) for x in pool_raw) \
                if isinstance(pool_raw, list) else (tier,)
            pool = tuple(x for x in pool if x in _SAFE_MODELS)
            if not pool:
                pool = ("usage",)
            grouped[(pool, bool(standing.get("available")),
                     str(standing.get("refresh_at") or ""))] = None
        if not grouped:
            grouped[(('usage',), False, "")] = None
        for pool, available, refresh in sorted(grouped, key=lambda x: x[0]):
            selected = lane == selected_lane
            state = ("frozen" if selected and frozen else
                     "ready" if available else "cooldown")
            reset_value: Any = freeze_reset if selected and frozen else refresh
            window = "+".join(pool)
            line = _line(
                "claude", lane, window, "unavailable(unsupported)", "-",
                _reset_cell(reset_value, now), f"{_iso(now)} (live)", state,
                selected=selected)
            rows.append(((0, lane, 10, window, 0), line))
    return rows


def failure_block(now: float | None = None) -> str:
    """Fixed, secret-free last resort when even formatting fails."""
    now = time.time() if now is None else now
    lines = [
        _line("claude", "accounts", "usage",
              "unavailable(telemetry-error)", "-", "-", "-", "unavailable"),
        _line("codex", "account", "usage",
              "unavailable(telemetry-error)", "-", "-", "-", "unavailable"),
        _line("gemini", "account", "usage",
              "unavailable(unsupported)", "-", "-", "-", "unsupported"),
    ]
    return (f"{OPEN} — current as of {_iso(now)}; dynamic/cache-only]\n"
            "provider/lane | window | used | amount | reset (countdown) | "
            "observed (age,freshness) | state\n"
            + "\n".join(lines)
            + "\n* selected for this turn; - = not authoritatively reported.\n"
            + CLOSE)


def render(org: Org, nid: str, *, selected_provider: str = "",
           selected_lane: str = "", now: float | None = None) -> str:
    """Render one deterministic provider/account board; never raise.

    Provider order is Claude, Codex, Gemini.  Claude accounts are primary,
    fallback ordinal, then this org's API-key lane.  Window order is session,
    weekly-all, weekly-scoped, then provider-specific.  Raw provider errors,
    account ids, emails, model labels and groups never enter the text.
    """
    now = time.time() if now is None else now
    try:
        node = org.node(nid)
        freeze = node.get("frozen")
        freeze = freeze if isinstance(freeze, dict) else {}
        frozen = bool(freeze.get("limit") or node.get("limit_locked"))
        freeze_reset = freeze.get("until_ts")
        rows: list[tuple[tuple[Any, ...], str]] = []

        try:
            claude = limits.snapshot(now)
            rows += _cached_rows(
                claude, "claude", "primary", now,
                selected_provider == "claude" and selected_lane == "primary",
                frozen, freeze_reset)
        except Exception:  # noqa: BLE001
            rows.append(((0, "primary", 99, "", 0),
                         _line("claude", "primary", "usage",
                               "unavailable(telemetry-error)", "-", "-", "-",
                               "unavailable",
                               selected=(selected_provider == "claude"
                                         and selected_lane == "primary"))))

        rows += _fallback_rows(now, selected_lane if selected_provider == "claude" else "",
                               frozen, freeze_reset)

        if bool(org.d.get("api_key")) or (
                selected_provider == "claude" and selected_lane == "org-api-key"):
            selected = selected_provider == "claude" and selected_lane == "org-api-key"
            fallback_until = org.d.get("api_fallback_until")
            reset_value = freeze_reset if selected and frozen else fallback_until
            state = ("frozen" if selected and frozen else
                     "active" if selected else "standby")
            rows.append(((0, "org-api-key", 99, "", 0),
                         _line("claude", "org-api-key", "usage",
                               "unavailable(unsupported)", "-",
                               _reset_cell(reset_value, now),
                               f"{_iso(now)} (live)", state,
                               selected=selected)))

        try:
            codex = codex_limits.snapshot(now)
            rows += _cached_rows(
                codex, "codex", "account", now,
                selected_provider == "openai", frozen, freeze_reset)
        except Exception:  # noqa: BLE001
            rows.append(((1, "account", 99, "", 0),
                         _line("codex", "account", "usage",
                               "unavailable(telemetry-error)", "-", "-", "-",
                               "unavailable", selected=selected_provider == "openai")))

        rows.append(((2, "account", 99, "", 0),
                     _line("gemini", "account", "usage",
                           "unavailable(unsupported)", "-", "-", "-",
                           "unsupported", selected=selected_provider == "google")))

        rows.sort(key=_row_order)
        return (f"{OPEN} — current as of {_iso(now)}; dynamic/cache-only]\n"
                "provider/lane | window | used | amount | reset (countdown) | "
                "observed (age,freshness) | state\n"
                + "\n".join(line for _key, line in rows)
                + "\n* selected for this turn; - = not authoritatively reported.\n"
                + CLOSE)
    except Exception:  # noqa: BLE001 - telemetry is never an admission gate
        return failure_block(now)
