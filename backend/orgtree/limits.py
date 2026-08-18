# pyright: strict
"""The host subscription's usage limits, as ground truth for freeze timing.

User ruling 2026-08-18: *every* usage freeze must carry a reset timestamp,
and when the CLI's error prose does not spell one out, the time has to be
looked up rather than guessed — "via the same method the usage limit modal
presents it". This module is that lookup, and it is the single owner of the
readout the modal renders (`api.claude_usage` delegates here, so both share
one cache and one parser).

Why it matters beyond a nicer label: `api_fallback` bills the org's own API
key for the length of the window it opens at freeze time, and that window is
stamped from the freeze's timestamp. A guessed or mis-parsed reset is
therefore money — a bogus epoch scraped out of an error string would keep the
key lane open long after the subscription recovered.

Source: `GET /api/oauth/usage` on the host's subscription OAuth token
(`subproxy` owns the token and its refresh). The payload's `limits[]` carries
one entry per lane — `session` (5 h), `weekly_all` (7 d), `weekly_scoped`
(a model's own weekly pool, e.g. Fable) — each with a minute-exact ISO-8601
`resets_at` and an `is_active` flag marking the lane that is actually
throttling the account right now.

⚠ Semi-documented surface, like `subproxy`: the endpoint is what Claude Code
itself reads for `/usage`, not a published contract. Everything here fails
OPEN — an unavailable or reshaped readout returns "no idea", and the caller
keeps its own conservative floor.
"""

from __future__ import annotations

import datetime as _dt
import http.client
import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, cast

from . import subproxy

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_TTL = 30.0        # the modal polls; the upstream is a per-account readout
FETCH_TIMEOUT = 15.0
# how stale a readout may be when a freeze RE-asks (see fetch's `max_age`)
REREAD_MAX_AGE = 5.0
# …and how stale one may be before it stops being evidence AT ALL. Redteam
# 2026-08-18: `fetch` serves the last good payload forever on a broken
# upstream and never refreshes `_cache["at"]`, so an unbounded-age readout was
# still pricing key-billing windows hours after it stopped being true. The
# modal may keep showing yesterday's bars; a freeze may not spend on them.
MAX_EVIDENCE_AGE = 900.0

# A reset further out than this is not a reset — it is a number that happened
# to sit where a timestamp goes. The longest real lane is the 7-day weekly.
MAX_HORIZON = 8 * 86400.0

# Lane wall-clock lengths, used only to sanity-bound a lane's own reset.
LANE_SECONDS = {"session": 18000.0, "weekly_all": 604800.0,
                "weekly_scoped": 604800.0}

_lock = threading.Lock()
# one request at a time (redteam 2026-08-18): N nodes freezing together used
# to mean N concurrent GETs at a semi-documented endpoint, each serializing on
# subproxy's token lock behind them, and a 429 from that herd put every one of
# them on the stale path. Waiters re-check the cache and take the winner's
# answer.
_fetch_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "data": None}


# --------------------------------------------------------------- the readout
def _iso_to_epoch(value: Any) -> float | None:
    """ISO-8601 (`2026-08-18T15:30:00Z` / `…+00:00`) → epoch seconds.
    `fromisoformat` only learned the `Z` suffix in 3.11 — normalize first so
    a 3.10 host does not silently lose every reset time."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:                 # naive readings are UTC upstream
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()


def _normalize(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """`limits[]` is the modern shape; the flat `five_hour`/`seven_day` pair is
    its ancestor, kept so an older upstream still yields the two unscoped
    bars. Unknown keys are ignored rather than rejected — the upstream churns
    codename fields and this must not start failing when it does."""
    out: list[dict[str, Any]] = []
    lims_any = raw.get("limits")
    # ⚠ `cast` is a runtime no-op: a reshaped upstream answering `"limits": 3`
    # made this raise TypeError out of a function whose contract is "never
    # raises", 500-ing the usage modal (redteam 2026-08-18)
    for lim_any in (cast("list[Any]", lims_any)
                    if isinstance(lims_any, list) else []):
        if not isinstance(lim_any, dict):
            continue
        lim = cast("dict[str, Any]", lim_any)
        model: Any = None
        scope_any = lim.get("scope")
        if isinstance(scope_any, dict):
            model_any = cast("dict[str, Any]", scope_any).get("model")
            if isinstance(model_any, dict):
                model = cast("dict[str, Any]", model_any).get("display_name")
        out.append({"kind": lim.get("kind"), "group": lim.get("group"),
                    "percent": lim.get("percent"),
                    "severity": lim.get("severity"),
                    "resets_at": lim.get("resets_at"),
                    "is_active": bool(lim.get("is_active")),
                    "model": model})
    if out:
        return out
    for key, kind in (("five_hour", "session"), ("seven_day", "weekly_all")):
        w_any = raw.get(key)
        if not isinstance(w_any, dict):
            continue
        w = cast("dict[str, Any]", w_any)
        if w.get("utilization") is not None:
            out.append({"kind": kind, "group": kind,
                        "percent": w["utilization"], "severity": "normal",
                        "resets_at": w.get("resets_at"),
                        "is_active": False, "model": None})
    return out


def _plan() -> str:
    try:
        doc_any: Any = json.load(open(subproxy.CREDS, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(doc_any, dict):
        return ""
    oauth_any = cast("dict[str, Any]", doc_any).get("claudeAiOauth")
    if not isinstance(oauth_any, dict):
        return ""
    return str(cast("dict[str, Any]", oauth_any).get("subscriptionType") or "")


def fetch(force: bool = False, max_age: float | None = None) -> dict[str, Any]:
    """The normalized readout: `{available, limits[], plan}`, or
    `{available: False, error}`. Cached `CACHE_TTL`, and STALE-ON-ERROR — a
    blip must show the last good bars rather than an error box, and must not
    make a freeze forget a reset time it already knew.

    `max_age` tightens the cache for one call. The freeze-correction pass
    needs it: a limit that just fired CHANGED the standing, and an entry the
    warm loop filled a moment earlier predates the event — served from the
    ordinary 30 s cache, the correction would "re-ask" and get back the very
    number the freeze already stamped from. A few seconds still coalesces a
    storm of freezes into one request.

    Synchronous on purpose: the freeze path is a worker thread, and the modal
    route hands it to a threadpool. Never raises."""
    ttl = CACHE_TTL if max_age is None else max(0.0, max_age)

    def _fresh_enough() -> dict[str, Any] | None:
        with _lock:
            hit = cast("dict[str, Any] | None", _cache["data"])
            if (hit is not None
                    and time.time() - float(cast(float, _cache["at"])) < ttl):
                return hit
        return None

    hit = None if force else _fresh_enough()
    if hit is not None:
        return hit
    if not subproxy.available():
        return {"available": False,
                "error": "no Claude Code credentials on this host"}
    with _fetch_lock:
        # the winner of the herd has just filled the cache — take its answer
        # rather than asking the same question again
        hit = None if force else _fresh_enough()
        if hit is not None:
            return hit
        try:
            token = subproxy.get_access_token()
        except RuntimeError as e:
            return {"available": False, "error": str(e)}
        try:
            req = urllib.request.Request(USAGE_URL, headers={
                "Authorization": "Bearer " + token,
                "anthropic-beta": "oauth-2025-04-20",
                "accept": "application/json"})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                raw_any: Any = json.load(resp)
        except (urllib.error.URLError, OSError, ValueError,
                # ⚠ http.client's own family is NOT an OSError: a truncated
                # chunked body raises IncompleteRead out of `json.load`,
                # escaping a function whose contract is "never raises" and
                # 500-ing the modal instead of serving the stale bars
                # (redteam 2026-08-18)
                http.client.HTTPException) as e:
            stale = cached()
            if stale is not None:
                return stale
            return {"available": False, "error": f"usage fetch failed: {e}"}
        raw: dict[str, Any] = (cast("dict[str, Any]", raw_any)
                               if isinstance(raw_any, dict) else {})
        data: dict[str, Any] = {"available": True, "limits": _normalize(raw),
                                "plan": _plan()}
        with _lock:
            # stamped when the answer ARRIVED, not when it was asked for: a
            # 14-second response is already 14 seconds stale (redteam)
            _cache.update(at=time.time(), data=data)
        return data


def available() -> bool:
    """Is there a subscription to read lanes from at all? (An API-key-only
    host has none — the warm loop stays silent rather than logging a failure
    every few minutes.)"""
    return subproxy.available()


def cache_age() -> float:
    """Seconds since the cached readout was fetched (`inf` when there is
    none). The readout ages into worthlessness — see MAX_EVIDENCE_AGE."""
    with _lock:
        if _cache["data"] is None:
            return float("inf")
        return time.time() - float(cast(float, _cache["at"]))


def cached() -> dict[str, Any] | None:
    """The last good readout, or None — NEVER a fetch. The freeze path runs
    under the document lock and the endpoint routinely takes over a second
    (user report 2026-08-18): a freeze stamps what is already known and a
    background pass corrects it. `warm()` keeps this fresh enough to be worth
    reading."""
    with _lock:
        return cast("dict[str, Any] | None", _cache["data"])


def pressure() -> float:
    """The highest lane utilization in the cached readout, 0..100 (0 when
    nothing is cached). The warm loop paces itself on it — a lane at 99% is
    minutes from freezing something, and a stamp is only as good as the
    readout behind it."""
    data = cached()
    if not data or not data.get("available"):
        return 0.0
    top = 0.0
    for x_any in cast("list[Any]", data.get("limits") or []):
        if not isinstance(x_any, dict):
            continue
        x = cast("dict[str, Any]", x_any)
        try:
            pct = float(cast(Any, x.get("percent")) or 0)
        except (TypeError, ValueError):
            continue
        if str(x.get("severity") or "") == "critical":
            pct = max(pct, 95.0)
        top = max(top, pct)
    return top


def invalidate() -> None:
    """Drop the cache. Tests only: a caller that just learned the standing
    changed wants `fetch(max_age=REREAD_MAX_AGE)`, which re-reads without
    throwing away an answer the next caller may still need."""
    with _lock:
        _cache.update(at=0.0, data=None)


# ------------------------------------------------------------ classification
# "your Fable 5 limit" — a TIER limit names the model as the thing you ran out
# OF. Raw CLI/API error text echoes model ids for other reasons entirely
# ("model claude-opus-4-1", "switch models with /model (sonnet, haiku)"), and
# reading those as the model's WEEKLY pool widened a five-hour session limit
# into a seven-day key-billing window (redteam 2026-08-18). Anchored on the
# possessive-ish shape, and never inside a hyphenated model id.
_TIER_RE = re.compile(
    r"(?<![\w-])(?:your|the)\s+(fable|opus|sonnet|haiku)\b(?![-\w])"
    r"[^.\n]{0,32}?\blimit\b", re.IGNORECASE)


_RATE_RE = re.compile(
    # ⚠ `429` is the one marker every rate limit carries, and the separator is
    # not fixed: the first cut matched `rate_limit` and `rate limit` but not
    # `rate-limit` — while its own neighbour `per[- ]minute` accepted the
    # hyphen — and missed `RateLimitError`, the SDK class name that shows up in
    # tracebacks (`err_blob` is the last three stderr lines). One hyphen was
    # the difference between a 15-minute window and a six-day one (redteam
    # round 10). The lookbehind keeps `corporate limit` out.
    r"(?<![\w-])(?:429\b|rate[-_ ]?limits?\b|ratelimit|per[-_ ]minute"
    r"|requests? per (?:minute|second)|tpm\b|rpm\b|too many requests)",
    re.IGNORECASE)


def is_rate_limit(blob: str) -> bool:
    """A short-window RATE limit (429, per-minute tokens) rather than a
    subscription usage LANE. `supervisor._looks_like_usage_limit` matches both
    on purpose — any wall should freeze the agent — but only the lanes are
    described by this readout, and answering a per-minute 429 with the session
    lane's reset parks the node for hours (redteam 2026-08-18)."""
    return bool(_RATE_RE.search(blob))


def classify(blob: str) -> tuple[str | None, str | None]:
    """Which lane does this limit error name? → `(kind, model)`, both None
    when the prose does not say.

    `session` is tested FIRST and wins outright, mirroring
    `supervisor._looks_like_fable_tier_limit`: a session limit that happens to
    mention a model ("session limit for Fable 5") is a session limit, and
    reading it as the model's weekly pool is the FABLE-1 bug in another
    costume — here it would stamp a 7-day key-billing window for a wall that
    lifts in the hour."""
    b = blob.lower()
    if "session" in b:
        return "session", None
    tier = _TIER_RE.search(blob)
    if re.search(r"\bweekly\b|\b7[- ]day\b|\bseven[- ]day\b", b):
        return (("weekly_scoped", tier.group(1).lower()) if tier
                else ("weekly_all", None))
    if tier:
        # "You've reached your Fable 5 limit." — the real model-tier wording
        # carries the model name and no window word at all.
        return "weekly_scoped", tier.group(1).lower()
    return None, None


def lane_horizon(kind: str | None) -> float:
    """How far out a reset for this lane may plausibly sit — the lane's own
    length plus an hour of slack, or the whole horizon when the lane is
    unknown. Callers band candidate timestamps with it: a "session limit"
    that claims to lift in 23 hours is not a session limit's reset."""
    # ⚠ an unrecognized lane takes the SHORTEST length, not the longest
    # (redteam 2026-08-18). Everywhere else in this module the unknown case
    # defaults short because the answer bounds a bill; a renamed or brand-new
    # upstream lane inheriting the 8-day band was the one place it failed open.
    return min(LANE_SECONDS.get(kind or "", LANE_SECONDS["session"]) + 3600.0,
               MAX_HORIZON)


def _candidate(lim: dict[str, Any], now: float) -> float | None:
    ts = _iso_to_epoch(lim.get("resets_at"))
    if ts is None or not now < ts <= now + lane_horizon(
            str(lim.get("kind") or "")):
        return None
    return ts


def reset_for(blob: str, now: float | None = None,
              allow_fetch: bool = False,
              trust_lane: bool = True) -> tuple[float | None, str]:
    """The authoritative reset for the limit this error is about →
    `(epoch, "usage:<lane>")`; `(None, "")` when the readout cannot answer.

    Two rules, and the second is the user's ruling of 2026-08-18 ("if the
    type of limit is not known, default to the shortest one, so that it can
    be checked sooner"):

      1. if the prose names a lane, that lane answers — scoped lanes matched
         on the model name;
      2. otherwise take the SOONEST reset on the board, `is_active` or not,
         and never one further out than the SESSION lane. Being active makes a
         lane the likely culprit but does not earn a longer window: this
         number bounds key-billing, and guessing short costs one re-freeze
         while guessing long costs money.

    A candidate must land in the future and inside its own lane's length, so
    a stale or absurd `resets_at` is declined rather than believed — which is
    also what keeps a STALE cache honest: an expired reset simply stops being
    a candidate instead of answering with a time that has already passed.

    `allow_fetch` is off by default: the freeze path must never block on the
    network (see `cached`). The background correction pass turns it on.

    ⚠ `trust_lane=False` ignores rule 1 entirely and treats the error as
    unnamed. The lane comes out of the blob's own wording, so a blob nobody
    vouches for — an agent's final answer promoted by the clean-result limit
    gate — would otherwise select its own band by typing the word "weekly",
    and be answered from a lane seven days out for a wall that does not exist
    (redteam 2026-08-18)."""
    now = time.time() if now is None else now
    data = fetch(max_age=REREAD_MAX_AGE) if allow_fetch else cached()
    if not data or not data.get("available"):
        return None, ""
    if cache_age() > MAX_EVIDENCE_AGE:
        # a readout this old is a memory, not a measurement — and a broken
        # upstream serves one indefinitely (redteam). Decline, and let the
        # caller's 5-minute probe floor have it.
        return None, ""
    lims = [cast("dict[str, Any]", x)
            for x in cast("list[Any]", data.get("limits") or [])
            if isinstance(x, dict)]
    def _within(pool: list[dict[str, Any]], at: float,
                lane: str | None) -> list[dict[str, Any]]:
        """The entries whose reset lands inside `lane`'s own length."""
        return [x for x in pool
                if (c := _candidate(x, at)) is not None
                and c <= at + lane_horizon(lane)]

    def _soonest(pool: list[dict[str, Any]]) -> tuple[float, str] | None:
        picked = sorted((t, str(x.get("kind") or "?"))
                        for x in pool if (t := _candidate(x, now)))
        return picked[0] if picked else None

    kind, model = classify(blob) if trust_lane else (None, None)
    hit = None
    if kind:
        hit = _soonest([x for x in lims if str(x.get("kind") or "") == kind
                        and (model is None
                             or model in str(x.get("model") or "").lower())])
        # ⚠ The fall-through exists because the named lane may be ABSENT from
        # the readout (an older upstream shape, an account with no scoped
        # pool) — but it is capped by the named lane's own length, and that
        # cap is the whole point. Without it, a stale cache whose `session`
        # entry has expired answers a SESSION limit with the weekly lane's
        # reset, six days out, and the org's key bills every turn for six days
        # (redteam 2026-08-18 — a critical finding against the first cut).
        hit = hit or _soonest(_within(lims, now, kind))
    else:
        # ⚠ and the SAME cap on the unnamed branch, which is the one ruling ③
        # is actually about. Capping only the named fall-through left the
        # canonical wording ("Claude AI usage limit reached", no lane word) to
        # answer from a weekly lane six days out — six days of key billing for
        # a wall that may lift in five hours (redteam 2026-08-18). A board
        # that has lost its session entry, or an upstream shape carrying only
        # the weekly one, reaches this with no stale cache involved.
        hit = _soonest(_within(lims, now, "session"))
    if hit is None:
        return None, ""
    ts, lane = hit
    return ts, f"usage:{lane}"
