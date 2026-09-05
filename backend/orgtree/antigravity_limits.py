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
import hashlib
import json
import os
import re
import threading
import uuid
import time
from typing import Any, Final, TypedDict

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


class _Wall(TypedDict):
    """One observed wall: the CLI's words, when, and the reset they named."""
    message: str
    observed_at: float
    resets_at: float | None
    tier: str
    id: str


_lock = threading.Lock()
_wall: _Wall | None = None
_ok_at: float | None = None
_loaded = False


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


def _number(value: object) -> float | None:
    """A JSON number (never a bool) as a float, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# ── durability ──────────────────────────────────────────────────────────

def _path() -> str:
    return os.path.join(providers.antigravity_probe_dir(), "standing.json")


def _load_unlocked() -> None:
    global _wall, _ok_at, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_path(), encoding="utf-8") as f:
            raw: object = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    doc: dict[str, object] = {str(k): v for k, v in raw.items()}  # type: ignore[misc]
    wall_raw = doc.get("wall")
    if isinstance(wall_raw, dict):
        fields: dict[str, object] = {
            str(k): v for k, v in wall_raw.items()}  # type: ignore[misc]
        observed = _number(fields.get("observed_at"))
        if observed is not None:
            _wall = {"message": str(fields.get("message") or "")[:300],
                     "observed_at": observed,
                     "resets_at": _number(fields.get("resets_at")),
                     "tier": str(fields.get("tier") or ""),
                     "id": str(fields.get("id") or "")}
    _ok_at = _number(doc.get("ok_at"))


def _save_unlocked() -> None:
    try:
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"wall": _wall, "ok_at": _ok_at}, f)
        os.replace(tmp, path)
    except OSError:
        pass          # the in-memory standing still serves this process


def forget_memory() -> None:
    """Drop the in-process copy only — what a backend restart does. The file
    stays, and the next reader loads from it. A test hook, public so the
    restart proof does not have to reach into module state."""
    global _wall, _ok_at, _loaded
    with _lock:
        _wall, _ok_at, _loaded = None, None, False


# ── observed windows: the append-only record the estimator needs ────────
#
# WHY THIS FILE EXISTS AT ALL. `standing.json` holds the CURRENT wall and
# nothing else: `observe_wall` REPLACES it and `observe_clear` sets it to
# None, so the moment a turn succeeds the wall that stood is gone. That is
# right for the standing (a lifted wall must not keep glowing) and useless
# for measurement — you cannot calibrate a window you no longer have. So
# every observation is ALSO appended here, where nothing overwrites it.
#
# ⚠ BOUNDED, NOT UNLIMITED. An append-only file that only grows is a disk
# leak with a nice name. Two generations, capped by lines and bytes; the
# older generation is dropped, and the estimator says how many samples it
# actually had rather than pretending the record is complete.
#
# ⚠ NO SECRETS. The account is identified by a NAMESPACE HASH of the signed-in
# address, never the address: enough to notice "these observations are all the
# same account" or "the account changed under us", not enough to leak who it
# is into a file that gets pasted into bug reports.
#
# ⚠ NO ASSUMPTION OF ONE CEILING. Two different windows have already been
# measured on one account (165h21m54s on 2026-09-03, 3h20m48s on 2026-09-04),
# so a record that folded them together would be measuring nothing. Each event
# stores its own observed reset DURATION and label; deciding which windows are
# comparable is a read-time judgement (`_comparable`), revisable without a
# migration, rather than a bucket guessed at write time.
MAX_EVENTS: Final = 400
MAX_EVENT_BYTES: Final = 262144
#: two observations belong to the same limit when their named metric matches
#: and their reset durations are within this factor of each other.
COMPARABLE_RATIO: Final = 0.25


def _events_path() -> str:
    return os.path.join(providers.antigravity_probe_dir(), "windows.ndjson")


def _account_ns() -> str:
    """A stable, non-identifying handle for the signed-in account."""
    try:
        email = str(providers.antigravity_status().get("email") or "")
    except Exception:                                        # noqa: BLE001
        email = ""
    if not email:
        return ""
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]


def _rotate_unlocked(path: str) -> None:
    """Keep the record bounded: one older generation, then drop."""
    try:
        if not os.path.exists(path):
            return
        if os.path.getsize(path) < MAX_EVENT_BYTES:
            with open(path, encoding="utf-8") as f:
                if sum(1 for _ in f) < MAX_EVENTS:
                    return
        os.replace(path, path + ".1")
    except OSError:
        pass


def _append_event(event: dict[str, Any]) -> None:
    """One observation, durably, best effort — never a reason a turn fails."""
    try:
        path = _events_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _rotate_unlocked(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as e:
        print(f"[orgtree] antigravity: window journal write failed: {e!r}")


def read_events() -> list[dict[str, Any]]:
    """Every retained observation, oldest first, across both generations."""
    out: list[dict[str, Any]] = []
    base = _events_path()
    for path in (base + ".1", base):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row: object = json.loads(line)
                    except ValueError:
                        continue          # a torn line, not a reason to stop
                    if isinstance(row, dict) and _number(row.get("at")):
                        out.append({str(k): v for k, v in row.items()})  # type: ignore[misc]
        except OSError:
            continue
    out.sort(key=lambda r: _number(r.get("at")) or 0.0)
    return out


def note_boot(now: float | None = None) -> None:
    """Mark that this process began observing.

    A gap between the previous event and a boot is a period in which orgtree
    was not watching. That is NOT the same as missing receipts — orgtree spent
    no tokens while it was down, so its own accounting is intact across a
    restart — but it IS a period in which a wall could have come and gone
    unseen, and a window whose start we inferred across one is worth less.
    Recorded so the estimator can say which, instead of averaging over it.
    """
    _append_event({"v": 1, "kind": "boot",
                   "at": time.time() if now is None else now,
                   "account_ns": _account_ns()})


def _window_event(kind: str, now: float, *, tier: str = "", message: str = "",
                  resets_at: float | None = None,
                  wall_id: str = "", after_wall: str = "") -> dict[str, Any]:
    event: dict[str, Any] = {
        "v": 1, "kind": kind, "at": now, "account_ns": _account_ns(),
        "tier": str(tier or ""),
        # the tier IS the model selector on this lane; recorded under its own
        # key so a future model split does not have to reinterpret `tier`
        "model": str(tier or "") or None,
    }
    if message:
        event["message"] = str(message)[:300]
        event["label"] = _label(message)
    if resets_at is not None:
        event["resets_at"] = resets_at
        # the DURATION the CLI actually named, which is what distinguishes one
        # limit from another; `resets_at` alone cannot, since it moves with now
        event["reset_seconds"] = round(resets_at - now, 3)
    if wall_id:
        event["wall_id"] = wall_id
    if after_wall:
        event["after_wall"] = after_wall
    return event


# ── windows and the estimate ────────────────────────────────────────────
#
# WHAT AN ESTIMATE HERE CAN AND CANNOT BE. The account's ceiling is not
# published anywhere orgtree can read, so nothing below is a provider-reported
# limit and none of it is presented as one. What CAN be measured is: between
# the moment a window began and the moment a wall was hit, orgtree spent this
# many tokens. That is a LOWER BOUND on what the account spent, because the
# same Google account is spendable in the Antigravity IDE where orgtree
# observes nothing at all. A remaining-budget figure derived from it therefore
# reads OPTIMISTIC — it can only ever say "at least this much was spent", so
# "at most this much is left" is the honest direction of its error, and the
# estimate carries that warning rather than burying it.


class Window(TypedDict):
    """One observed limit window, closed by a wall."""
    limit: str                 # the metric the CLI named
    tier: str
    started_at: float | None   # when the window opened, if we can tell
    start_kind: str            # how we know: "reset" | "boot" | "unknown"
    walled_at: float
    resets_at: float | None
    reset_seconds: float | None
    wall_id: str
    account_ns: str
    gap_before_s: float        # orgtree not observing, inside this window
    complete: bool             # a start we can defend AND a wall


def _comparable(a: Window, b: Window) -> bool:
    """Do these two windows describe the SAME limit?

    Same named metric, and reset durations within COMPARABLE_RATIO. Two
    different limits have already been measured on one account (165h and
    3h20m), so folding them together would average two unrelated things into
    a number that describes neither.
    """
    if a["limit"] != b["limit"]:
        return False
    x, y = a["reset_seconds"], b["reset_seconds"]
    if x is None or y is None:
        return x is None and y is None
    if x <= 0 or y <= 0:
        return False
    return abs(x - y) / max(x, y) <= COMPARABLE_RATIO


def windows(events: list[dict[str, Any]] | None = None) -> list[Window]:
    """Reconstruct the closed windows the record supports.

    A window ENDS at a wall — that is the only unambiguous event this lane
    produces. Its START is the previous wall's reset instant when we have one
    (the moment the account was refilled), else the boot after which we began
    watching, else unknown. A start we had to infer across a period when
    orgtree was not running is still reported, with the gap attached, so a
    caller can weigh it instead of being handed an average that hid it.
    """
    rows = read_events() if events is None else sorted(
        events, key=lambda r: _number(r.get("at")) or 0.0)
    out: list[Window] = []
    open_from: float | None = None          # when the current window began
    open_kind = "unknown"
    last_seen: float | None = None          # last moment we were observing
    gap = 0.0
    for row in rows:
        at = _number(row.get("at"))
        if at is None:
            continue
        kind = str(row.get("kind") or "")
        if kind == "boot":
            # time between the last thing we saw and this boot is time we were
            # not watching. It does NOT mean receipts are missing — orgtree
            # spends nothing while it is down — but a wall could have passed
            # unseen, so it is carried on the window rather than discarded.
            if last_seen is not None:
                gap += max(0.0, at - last_seen)
            if open_from is None:
                open_from, open_kind = at, "boot"
            last_seen = at
            continue
        last_seen = at
        if kind != "wall":
            continue
        resets = _number(row.get("resets_at"))
        out.append({
            "limit": str(row.get("label") or "individual quota"),
            "tier": str(row.get("tier") or ""),
            "started_at": open_from,
            "start_kind": open_kind if open_from is not None else "unknown",
            "walled_at": at,
            "resets_at": resets,
            "reset_seconds": _number(row.get("reset_seconds")),
            "wall_id": str(row.get("wall_id") or ""),
            "account_ns": str(row.get("account_ns") or ""),
            "gap_before_s": round(gap, 3),
            "complete": open_from is not None and open_from < at,
        })
        # the NEXT window opens when this wall lifts, which is the one moment
        # this lane states outright
        open_from, open_kind, gap = resets, "reset", 0.0
    return out


def estimate(events: list[dict[str, Any]] | None = None,
             tokens_between: Any = None,
             now: float | None = None) -> dict[str, Any]:
    """What the observed windows support, and nothing beyond it.

    `tokens_between(start, end)` returns the tokens ORGTREE spent in an
    interval — injected rather than imported so this stays a pure function of
    its evidence, and so a test can drive it with known numbers.

    The return always says how many samples it had. With no complete sample it
    returns a refusal with a reason, NOT a number: one wall on its own tells
    you a wall exists, not what it costs, and printing the first figure that
    can be computed is how an inference becomes a ceiling nobody checks.
    """
    now = time.time() if now is None else now
    closed = [w for w in windows(events) if w["complete"]]
    if not closed:
        return {"available": False, "samples": 0,
                "reason": ("no complete observed window yet — an estimate "
                           "needs a window with a start we can defend and a "
                           "wall that closed it"),
                "estimate": None}
    if tokens_between is None:
        return {"available": False, "samples": len(closed),
                "reason": "no token receipts were supplied to measure with",
                "estimate": None}
    # group by limit: never average two different ceilings together
    groups: list[list[Window]] = []
    for w in closed:
        for g in groups:
            if _comparable(g[0], w):
                g.append(w)
                break
        else:
            groups.append([w])
    groups.sort(key=lambda g: (-len(g), -g[-1]["walled_at"]))
    best = groups[0]
    measured: list[dict[str, Any]] = []
    for w in best:
        start, end = w["started_at"], w["walled_at"]
        if start is None:
            continue
        try:
            spent = int(tokens_between(start, end))
        except Exception:                                    # noqa: BLE001
            continue
        measured.append({"tokens": spent, "walled_at": w["walled_at"],
                         "gap_before_s": w["gap_before_s"],
                         "covered": w["gap_before_s"] <= 0.0})
    if not measured:
        return {"available": False, "samples": len(best),
                "reason": "no receipts could be read for the observed windows",
                "estimate": None}
    values = [m["tokens"] for m in measured]
    n = len(values)
    lo, hi = min(values), max(values)
    spread = (hi - lo) / hi if hi > 0 else 0.0
    # one sample is worth reporting AS ONE SAMPLE. It is not worthless — it is
    # the only number anyone has — but it is an observation, not a limit, and
    # the label says which.
    confidence = ("experimental" if n == 1
                  else "low" if spread > 0.4 else "indicative")
    uncovered = [m for m in measured if not m["covered"]]
    return {
        "available": True,
        "kind": "estimate",
        "limit": best[0]["limit"],
        "tier": best[0]["tier"],
        "samples": n,
        "confidence": confidence,
        "estimate": {"tokens_lowest": lo, "tokens_highest": hi,
                     "tokens_latest": values[-1]},
        "reset_seconds": best[0]["reset_seconds"],
        "observations": measured,
        # said on the face of it, every time, in the caller's words if it likes
        "basis": ("tokens ORGTREE spent between the window opening and the "
                  "wall; the provider publishes no usage readout, so this is "
                  "an inference from observed walls, not a reported limit"),
        "warning": ("a LOWER BOUND: the same account can be spent in the "
                    "Antigravity IDE, which orgtree cannot observe, so any "
                    "remaining-budget reading from this is optimistic"),
        "coverage": {
            "windows_with_unobserved_gaps": len(uncovered),
            "note": ("a gap means orgtree was not running for part of the "
                     "window, so a wall could have passed unseen; orgtree's "
                     "own receipts are still complete for the time it ran"),
        },
    }


# ── observation (the leg's two calls) ───────────────────────────────────

def observe_wall(message: str, *, tier: str = "",
                 now: float | None = None) -> float | None:
    """A turn ended on a usage wall.  Records it and returns the reset
    instant parsed from the message, or None when it names none — the
    caller's freeze then falls to its own prose parse / probe floor."""
    global _wall
    now = time.time() if now is None else now
    ts = reset_at(message, now)
    wall_id = uuid.uuid4().hex[:12]
    with _lock:
        _load_unlocked()
        _wall = {"message": str(message or "")[:300], "observed_at": now,
                 "resets_at": ts, "tier": str(tier or ""), "id": wall_id}
        _save_unlocked()
    # the standing above is overwritten by the next observation; this is not
    _append_event(_window_event("wall", now, tier=tier, message=message,
                                resets_at=ts, wall_id=wall_id))
    return ts


def observe_clear(now: float | None = None) -> None:
    """A turn completed: whatever wall stood is down."""
    global _wall, _ok_at
    now = time.time() if now is None else now
    with _lock:
        _load_unlocked()
        changed = _wall is not None
        after = str((_wall or {}).get("id") or "") if changed else ""
        tier = str((_wall or {}).get("tier") or "") if changed else ""
        _wall = None
        _ok_at = now
        if changed:
            _save_unlocked()
    # only a clear that ENDED a wall closes a window; a successful turn with
    # nothing standing is not an observation about any limit
    if changed:
        _append_event(_window_event("clear", now, tier=tier, after_wall=after))


def _current(now: float) -> tuple[_Wall | None, bool]:
    """(the standing wall or None, stale?) — a wall whose reset has passed
    is presumed lifted; one that never named a reset ages out."""
    _load_unlocked()
    wall = _wall
    if wall is None:
        return None, False
    # the record itself, never a copy: observations REPLACE `_wall`, nothing
    # mutates one in place, so readers can hold it without a defensive copy
    resets = wall["resets_at"]
    if resets is not None:
        return (wall, False) if now < resets else (None, False)
    return wall, now - wall["observed_at"] > MAX_EVIDENCE_AGE


def _limits(wall: _Wall) -> list[dict[str, Any]]:
    return [{
        # the CLI states remaining time, never the window's length, so the
        # kind is the provider-specific one — a 165-hour reset is not proof
        # of a weekly lane, and `session` would be a guess in the other
        # direction
        "kind": "provider_window",
        "group": ACCOUNT,
        "percent": 100.0,
        "severity": "critical",
        "resets_at": _iso(wall["resets_at"]),
        "is_active": True,
        "model": None,
        "label": _label(wall["message"]),
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
        ok_at = _ok_at
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
            "age": round(now - wall["observed_at"], 1)}


def snapshot(now: float | None = None) -> dict[str, Any]:
    """Timestamped evidence for the dynamic turn envelope (`turnusage`).

    `unsupported: True` marks "nothing was ever observed" — the board keeps
    its explicit unsupported row for that, so a machine that never hit a wall
    renders byte-identical to before this module existed."""
    now = time.time() if now is None else now
    with _lock:
        wall, stale = _current(now)
        observed = _wall is not None
    if wall is None:
        return {"available": False, "provider": PROVIDER, "limits": [],
                "observed_at": None, "age": None, "stale": False,
                "unsupported": not observed}
    at = wall["observed_at"]
    return {"available": True, "provider": PROVIDER, "limits": _limits(wall),
            "observed_at": _iso(at), "age": max(0.0, now - at),
            "stale": stale, "unsupported": False}


def invalidate() -> None:
    """Forget everything, in memory AND on disk (tests, and a sign-out)."""
    global _wall, _ok_at, _loaded
    with _lock:
        _wall, _ok_at, _loaded = None, None, True
        try:
            os.remove(_path())
        except OSError:
            pass
