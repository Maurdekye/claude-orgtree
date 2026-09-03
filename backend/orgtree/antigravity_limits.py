# pyright: strict
"""Antigravity account standing, OBSERVED from the wire — never fetched.

The Antigravity CLI publishes no usage readout orgtree can read headlessly.
Its changelog says the read-only slash commands (`/quota`, `/usage`,
`/credits`) answer in print mode "without spending quota", but measured
(1.1.24, 2026-09-03, walled Google account) each of them went to the MODEL
and came back with the wall itself as the error — so there is no cheap,
side-effect-free door this module could open, and it opens none: no process
is ever spawned here.

What it holds instead is the evidence the turns already carry:

  · A WALL. The ERROR result's message, measured 2026-09-03 02:36 local:
    "Individual quota reached. Please upgrade your subscription to increase
    your limits. Resets in 165h21m54s."  The leg folds it here as ONE window
    at 100% with the reset parsed out of "Resets in …" — the same instant the
    D-209 freeze then thaws on, instead of the blind 5-minute probe floor.
  · A SUCCESS. A completed turn after a wall means the wall is down (the
    reset passed, or the plan changed); the standing clears.

The normalized shape deliberately matches `codex_limits`/`limits` — one
renderer, one severity rule, one board formatter (`turnusage`).  The standing
survives a backend restart in a small JSON beside the provider's probe logs,
because unlike the other lanes nothing can re-fetch it.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
from typing import Any, Final

from . import providers

PROVIDER: Final = "Antigravity"
ACCOUNT: Final = "antigravity"
#: a wall with NO parseable reset stays evidence this long (the codex lane's
#: evidence age); a wall WITH a reset stays until that instant passes.
MAX_EVIDENCE_AGE: Final = 900.0
#: the CLI phrases its reset as a duration — "Resets in 165h21m54s", also
#: "Resets in 2m", "in 45s", and defensively the worded forms ("in 2 hours 5
#: minutes", "in 1 day").  Anchored on the verb so "165h" inside a model name
#: or a path can never read as a reset.
#: ⚠ `(?![a-z])`, not `\b`, after the unit: in the CLI's compact form the
#: next digit follows the unit letter directly ("165h21m54s"), and there is
#: no word boundary between "h" and "2" — a `\b` read the measured specimen
#: as no reset at all.
_UNIT_RE: Final = (r"(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)"
                   r"(?![a-z])")
_RESET_IN_RE: Final = re.compile(
    r"\bresets?\s+in\s+((?:\d+\s*" + _UNIT_RE + r"\s*,?\s*(?:and\s+)?)+)",
    re.IGNORECASE)
_PART_RE: Final = re.compile(r"(\d+)\s*" + _UNIT_RE, re.IGNORECASE)
_UNIT: Final = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}
#: the fake/legacy wording names its metric — surface it as the bar label
_METRIC_RE: Final = re.compile(r"limit\s+'([^']{1,60})'", re.IGNORECASE)

_lock = threading.Lock()
#: {"wall": {...} | None, "ok_at": epoch | None, "loaded": bool}
_state: dict[str, Any] = {"wall": None, "ok_at": None, "loaded": False}


def reset_in_seconds(text: str) -> float | None:
    """Seconds until the wall lifts, read from "Resets in …"; None when the
    text carries no such duration (or a zero one, which is not a reset)."""
    m = _RESET_IN_RE.search(text or "")
    if not m:
        return None
    total = 0.0
    for num, unit in _PART_RE.findall(m.group(1)):
        total += int(num) * _UNIT[unit[0].lower()]
    return total if total > 0 else None


def reset_at(text: str, now: float | None = None) -> float | None:
    """The reset instant behind the CLI's duration, against `now`."""
    secs = reset_in_seconds(text)
    if secs is None:
        return None
    return (time.time() if now is None else now) + secs


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


def _label(message: str) -> str:
    m = _METRIC_RE.search(message or "")
    if m:
        return m.group(1).strip().lower()
    return "individual quota"


# ── durability ──────────────────────────────────────────────────────────

def _path() -> str:
    return os.path.join(providers.antigravity_probe_dir(), "standing.json")


def _load_unlocked() -> None:
    if _state["loaded"]:
        return
    _state["loaded"] = True
    try:
        with open(_path(), encoding="utf-8") as f:
            raw: Any = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    wall = raw.get("wall")
    if isinstance(wall, dict) and isinstance(wall.get("observed_at"),
                                             (int, float)):
        _state["wall"] = {
            "message": str(wall.get("message") or "")[:300],
            "observed_at": float(wall["observed_at"]),
            "resets_at": (float(wall["resets_at"])
                          if isinstance(wall.get("resets_at"), (int, float))
                          else None),
            "tier": str(wall.get("tier") or ""),
        }
    ok_at = raw.get("ok_at")
    _state["ok_at"] = float(ok_at) if isinstance(ok_at, (int, float)) else None


def _save_unlocked() -> None:
    try:
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"wall": _state["wall"], "ok_at": _state["ok_at"]}, f)
        os.replace(tmp, path)
    except OSError:
        pass          # the in-memory standing still serves this process


# ── observation (the leg's two calls) ───────────────────────────────────

def observe_wall(message: str, *, tier: str = "",
                 now: float | None = None) -> float | None:
    """A turn ended on a usage wall.  Records it and returns the reset
    instant parsed from the message, or None when it names none — the
    caller's freeze then falls to its own prose parse / probe floor."""
    now = time.time() if now is None else now
    ts = reset_at(message, now)
    with _lock:
        _load_unlocked()
        _state["wall"] = {"message": str(message or "")[:300],
                          "observed_at": now, "resets_at": ts,
                          "tier": str(tier or "")}
        _save_unlocked()
    return ts


def observe_clear(now: float | None = None) -> None:
    """A turn completed: whatever wall stood is down."""
    now = time.time() if now is None else now
    with _lock:
        _load_unlocked()
        changed = _state["wall"] is not None
        _state["wall"] = None
        _state["ok_at"] = now
        if changed:
            _save_unlocked()


def _current(now: float) -> tuple[dict[str, Any] | None, bool]:
    """(the standing wall or None, stale?) — a wall whose reset has passed
    is presumed lifted; one that never named a reset ages out."""
    _load_unlocked()
    wall = _state["wall"]
    if not isinstance(wall, dict):
        return None, False
    resets = wall.get("resets_at")
    age = now - float(wall.get("observed_at") or 0.0)
    if isinstance(resets, (int, float)):
        return (dict(wall), False) if now < float(resets) else (None, False)
    return dict(wall), age > MAX_EVIDENCE_AGE


def _limits(wall: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        # the CLI states remaining time, never the window's length, so the
        # kind is the provider-specific one — a 165-hour reset is not proof
        # of a weekly lane, and `session` would be a guess in the other
        # direction
        "kind": "provider_window",
        "group": ACCOUNT,
        "percent": 100.0,
        "severity": "critical",
        "resets_at": _iso(wall.get("resets_at")),
        "is_active": True,
        "model": None,
        "label": _label(str(wall.get("message") or "")),
    }]


def _account(data: dict[str, Any]) -> dict[str, Any]:
    status = providers.antigravity_status()
    return {"account": ACCOUNT,
            "label": status.get("email") or "signed-in account",
            "provider": PROVIDER, **data}


# ── readers (modal, glow, turn envelope) ────────────────────────────────

def fetch(force: bool = False) -> dict[str, Any]:
    """The header modal's section.  Cache-only by construction (see the
    module docstring); `force` exists for signature parity and does nothing
    more than a plain read."""
    del force
    status = providers.antigravity_status()
    if not status.get("installed"):
        return _account({"available": False,
                         "error": "Antigravity CLI is not installed"})
    if not status.get("connected"):
        return _account({"available": False,
                         "error": "Antigravity CLI is not signed in"})
    now = time.time()
    with _lock:
        wall, stale = _current(now)
        ok_at = _state["ok_at"]
    if wall is None:
        last_ok = _iso(ok_at) if ok_at else None
        return _account({
            "available": False,
            # a settled fact, not an outage: the CLI exposes no readout
            "unsupported": True,
            "error": ("Antigravity publishes no usage readout; a quota wall "
                      "appears here when a turn hits one, with its reset"
                      + (f" — last successful turn {last_ok}" if last_ok
                         else "")),
        })
    data: dict[str, Any] = {"available": True, "limits": _limits(wall)}
    if stale:
        data["error"] = "the last observed wall named no reset and is old"
    return _account(data)


def peek() -> dict[str, Any]:
    """Cache-only read for the always-on header warning glow."""
    now = time.time()
    with _lock:
        wall, stale = _current(now)
    if wall is None or stale:
        return {"available": False, "provider": PROVIDER}
    return {"available": True, "provider": PROVIDER,
            "limits": _limits(wall),
            "age": round(now - float(wall.get("observed_at") or now), 1)}


def snapshot(now: float | None = None) -> dict[str, Any]:
    """Timestamped evidence for the dynamic turn envelope (`turnusage`).

    `unsupported: True` marks "nothing was ever observed" — the board keeps
    its explicit unsupported row for that, so a machine that never hit a wall
    renders byte-identical to before this module existed."""
    now = time.time() if now is None else now
    with _lock:
        wall, stale = _current(now)
        observed = _state["wall"] if isinstance(_state["wall"], dict) else None
    if wall is None:
        return {"available": False, "provider": PROVIDER, "limits": [],
                "observed_at": None, "age": None, "stale": False,
                "unsupported": observed is None}
    at = float(wall.get("observed_at") or 0.0)
    return {"available": True, "provider": PROVIDER, "limits": _limits(wall),
            "observed_at": _iso(at), "age": max(0.0, now - at),
            "stale": stale, "unsupported": False}


def invalidate() -> None:
    """Forget everything, in memory AND on disk (tests, and a sign-out)."""
    with _lock:
        _state.update(wall=None, ok_at=None, loaded=True)
        try:
            os.remove(_path())
        except OSError:
            pass
