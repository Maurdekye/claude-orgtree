# pyright: strict
"""Pure cache-continuity classification and the stable agent doctrine.

The supervisor owns observation and persistence.  This module deliberately
does no I/O, credential lookup, prompt rendering, or provider calls; that makes
the four proof classes deterministic and keeps provider uncertainty explicit.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from typing import Any, Final, Literal, cast

State = Literal[
    "known_incompatible", "expired_known_entry", "uncertain",
    "compatible_observed",
]

SUBSCRIPTION_TTL_SECONDS: Final = 60 * 60
API_KEY_TTL_SECONDS: Final = 5 * 60
CODEX_SUBSCRIPTION_TTL_SECONDS: Final = 30 * 60

#: The serialization quantum of durable receipt timestamps. `observed_at` is
#: the microsecond-rounded ISO image of the float clock the same call still
#: holds, and `datetime` rounds to the NEAREST microsecond — so the parsed
#: stamp may sit up to half a quantum ahead of the float it was made from.
#: A skew claim needs more than one whole quantum; anything inside it is the
#: same instant wearing two encodings.
SERIALIZATION_QUANTUM_S: Final = 1e-6

# Stable by construction: no formatting fields, timestamps, account names,
# settings, org state or forecast values may enter this system-prompt block.
CACHE_CONTINUITY_BLOCK: Final = """[CACHE CONTINUITY]
Provider cache continuity is separate from a local warm process. A local process restart or replacement does not by itself prove a provider cache miss.

Always treat a provider, account/auth lane, model, or session-lineage switch as a new cache namespace. A provider switch can also lose provider-specific session/context continuity. Treat a rewrite of the already-sent system/startup prompt, charter, scope, tool or MCP definitions, startup instruction files, or conversation history as a changed prefix. Avoid those changes when they are unnecessary; when they are necessary, surface the cache cost instead of hiding it.

Dynamic turn-envelope facts (org state, mail/notices, usage/status/checkup data, attachments), live process/tool counts, append-only new turns, and an effort-only control change do not invalidate an unchanged earlier prefix by themselves. TTL expiry and provider-side acceptance depend on the actual auth lane and a positive cache receipt: Claude subscription auth uses 60 minutes and Claude API-key auth uses 5 minutes; Codex subscription auth uses a fixed 30-minute estimate from OpenAI's documented gpt-5.6 prompt-cache default. Unsupported or unobserved lanes stay unknown. Even a matching, unexpired local fingerprint is evidence of compatibility, never a guaranteed provider hit.
[END CACHE CONTINUITY]"""

_COMPONENT_ORDER: Final = (
    "provider", "account", "lane", "model", "session", "system", "tools",
    "argv", "env", "startup", "lineage", "history",
)


def iso(epoch: float) -> str:
    """UTC second-resolution ISO string used by durable evidence."""
    return (dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


def iso_us(epoch: float) -> str:
    """UTC microsecond ISO string for computed expiry instants.

    Receipts store microsecond `observed_at`/`expires_at` stamps; a projected
    expiry truncated to whole seconds would disagree with them by up to a
    second and flip displays across the boundary early.
    """
    return (dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
            .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def epoch(value: Any) -> float | None:
    """Defensive timestamp parser.  Invalid and non-positive values are absent."""
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
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    try:
        number = parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def digest(value: Any, length: int = 32) -> str:
    """Canonical JSON digest; inputs are local metadata, never prompt text."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, default=str).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:length]


def ttl_seconds(provider: str, lane: str) -> int | None:
    """Fixed cache window for an observed auth lane.

    Claude receipts expose the two provider TTL lanes directly. Codex does not
    return a TTL in the app-server usage receipt, so its subscription value is
    the user's fixed estimate: the documented gpt-5.6 Responses API default
    (30m, currently the only supported prompt_cache_options.ttl value). Gemini
    and Codex API-key sessions remain unknown rather than borrowing a lane.
    """
    if provider == "claude":
        if lane == "subscription":
            return SUBSCRIPTION_TTL_SECONDS
        if lane == "api_key":
            return API_KEY_TTL_SECONDS
    if provider == "openai" and lane == "subscription":
        return CODEX_SUBSCRIPTION_TTL_SECONDS
    return None


def positive_usage(usage: Any) -> tuple[int, int]:
    """Return authoritative cache read/write token counts, defensively."""
    row = usage if isinstance(usage, dict) else {}

    def count(key: str) -> int:
        try:
            value = int(row.get(key) or 0)
        except (TypeError, ValueError, OverflowError):
            return 0
        return max(value, 0)

    return count("cache_read_input_tokens"), count(
        "cache_creation_input_tokens")


def _reason(component: str, text: str, at: str,
            confidence: str = "known") -> dict[str, str]:
    return {"component": component, "reason": text,
            "evidence_at": at, "confidence": confidence}


def _different(current: dict[str, Any], prior: dict[str, Any],
               key: str) -> bool:
    return str(current.get(key) or "") != str(prior.get(key) or "")


def classify(current: dict[str, Any], continuity: dict[str, Any] | None,
             now: float) -> dict[str, Any]:
    """Classify the next turn from a current snapshot and durable evidence.

    ``current`` contains secret-safe component digests plus private provider
    lane identifiers.  History relations are prepared by the supervisor from
    prefix hashes: ``same_or_appended``, ``changed`` or ``unobserved``.
    """
    book = continuity if isinstance(continuity, dict) else {}
    last = book.get("last_turn")
    last = cast(dict[str, Any], last) if isinstance(last, dict) else {}
    receipt = book.get("receipt")
    receipt = cast(dict[str, Any], receipt) if isinstance(receipt, dict) else {}
    at = str(current.get("captured_at") or iso(now))
    expected = int(current.get("expected_input_tokens") or 0)

    if not last:
        return {
            "state": "uncertain", "source": "no_completed_fingerprint",
            "reason": "No completed turn fingerprint has been observed yet.",
            "reasons": [_reason("history", "no completed fingerprint", at,
                                "unobserved")],
            "observed_at": at, "last_receipt_at": None,
            "lane": str(current.get("lane") or "unobserved"),
            "ttl_seconds": None, "expires_at": None,
            "confidence": "unobserved",
            "expected_input_tokens": expected,
        }

    changed: list[dict[str, str]] = []
    labels = {
        "provider": "provider namespace changed",
        "account": "provider account/auth namespace changed",
        "lane": "provider authentication lane changed",
        "model": "model namespace changed",
        "session": "session lineage changed",
    }
    for key in ("provider", "account", "lane", "model", "session"):
        if _different(current, last, key):
            changed.append(_reason(key, labels[key], at))
    cur_parts = current.get("components")
    cur_parts = cast(dict[str, Any], cur_parts) if isinstance(cur_parts, dict) else {}
    old_parts = last.get("components")
    old_parts = cast(dict[str, Any], old_parts) if isinstance(old_parts, dict) else {}
    for key in ("system", "tools", "argv", "env", "startup", "lineage"):
        if str(cur_parts.get(key) or "") != str(old_parts.get(key) or ""):
            changed.append(_reason(key, f"{key} prefix component changed", at))
    relation = str(current.get("last_turn_history_relation") or "unobserved")
    if relation == "changed":
        changed.append(_reason("history", "already-sent history was rewritten or truncated",
                               at))
    # A receipt belongs to one namespace and one observed prefix.  Append-only
    # later history preserves that prefix; mutation does not.  Compare it even
    # when the last-turn fingerprint also moved: the UI contract requires
    # EVERY changed component, not merely the first baseline that proves cold.
    receipt_changes: list[dict[str, str]] = []
    receipt_relation = str(current.get("receipt_history_relation") or "unobserved")
    if receipt:
        for key in ("provider", "account", "lane", "model", "session"):
            if _different(current, receipt, key):
                receipt_changes.append(_reason(key, labels[key], at))
        receipt_parts = receipt.get("components")
        receipt_parts = (cast(dict[str, Any], receipt_parts)
                         if isinstance(receipt_parts, dict) else {})
        for key in ("system", "tools", "argv", "env", "startup", "lineage"):
            if str(cur_parts.get(key) or "") != str(receipt_parts.get(key) or ""):
                receipt_changes.append(_reason(
                    key, f"{key} cache-observed prefix component changed", at))
        if receipt_relation == "changed":
            receipt_changes.append(
                _reason("history", "cache-observed history prefix changed", at))

    # Stable, component-complete union.  A component that differs from both
    # the last completed request and the last positive receipt appears once.
    all_changes: list[dict[str, str]] = []
    changed_components: set[str] = set()
    for item in (*changed, *receipt_changes):
        component = item["component"]
        if component not in changed_components:
            changed_components.add(component)
            all_changes.append(item)
    if all_changes:
        return {
            "state": "known_incompatible",
            "source": ("fingerprint_and_receipt_mismatch"
                       if changed and receipt_changes else
                       "fingerprint_mismatch" if changed else
                       "receipt_prefix_mismatch"),
            "reason": all_changes[0]["reason"],
            "reasons": all_changes,
            "observed_at": at,
            "last_receipt_at": receipt.get("observed_at"),
            "lane": str(current.get("lane") or "unobserved"),
            "ttl_seconds": receipt.get("ttl_seconds"),
            "expires_at": receipt.get("expires_at"),
            "confidence": "known", "expected_input_tokens": expected,
        }
    if relation == "unobserved":
        return {
            "state": "uncertain", "source": "history_unobserved",
            "reason": "Local history continuity could not be observed.",
            "reasons": [_reason("history", "history continuity unobserved", at,
                                "unobserved")],
            "observed_at": at,
            "last_receipt_at": receipt.get("observed_at"),
            "lane": str(current.get("lane") or "unobserved"),
            "ttl_seconds": receipt.get("ttl_seconds"),
            "expires_at": receipt.get("expires_at"),
            "confidence": "uncertain", "expected_input_tokens": expected,
        }

    if not receipt:
        return {
            "state": "uncertain", "source": "no_positive_receipt",
            "reason": "The local prefix matches, but no positive cache receipt was observed.",
            "reasons": [_reason("receipt", "positive cache receipt unobserved", at,
                                "unobserved")],
            "observed_at": at, "last_receipt_at": None,
            "lane": str(current.get("lane") or "unobserved"),
            "ttl_seconds": None, "expires_at": None,
            "confidence": "uncertain", "expected_input_tokens": expected,
        }
    if receipt_relation == "unobserved":
        return {
            "state": "uncertain", "source": "receipt_prefix_unobserved",
            "reason": "The cache-observed history prefix could not be verified locally.",
            "reasons": [_reason("history", "cache-observed prefix unobserved", at,
                                "unobserved")],
            "observed_at": at,
            "last_receipt_at": receipt.get("observed_at"),
            "lane": str(current.get("lane") or "unobserved"),
            "ttl_seconds": receipt.get("ttl_seconds"),
            "expires_at": receipt.get("expires_at"),
            "confidence": "uncertain", "expected_input_tokens": expected,
        }

    provider = str(current.get("provider") or "")
    lane = str(current.get("lane") or "")
    ttl = ttl_seconds(provider, lane)
    codex_estimate = provider == "openai" and lane == "subscription"
    observed = epoch(receipt.get("observed_at"))
    if ttl is None or observed is None:
        return {
            "state": "uncertain", "source": "ttl_unobserved",
            "reason": "A positive cache receipt exists, but this provider/auth lane has no authoritative TTL.",
            "reasons": [_reason("ttl", "authoritative TTL unobserved", at,
                                "unobserved")],
            "observed_at": at, "last_receipt_at": receipt.get("observed_at"),
            "lane": str(current.get("lane") or "unobserved"),
            "ttl_seconds": None, "expires_at": None,
            "confidence": "uncertain", "expected_input_tokens": expected,
        }
    expires = observed + ttl
    if observed - now > SERIALIZATION_QUANTUM_S:
        return {
            "state": "uncertain", "source": "clock_skew",
            "reason": "The receipt timestamp is in the future relative to this backend clock.",
            "reasons": [_reason("clock", "receipt timestamp is in the future", at,
                                "uncertain")],
            "observed_at": at, "last_receipt_at": receipt.get("observed_at"),
            "lane": str(current.get("lane") or "unobserved"),
            "ttl_seconds": ttl, "expires_at": iso_us(expires),
            "confidence": "uncertain", "expected_input_tokens": expected,
        }
    if observed > now:
        now = observed          # inside one quantum: the same instant, clamped
    return _receipt_verdict(
        expired=now >= expires,        # equality is the expiry boundary
        codex_estimate=codex_estimate, at=at,
        last_receipt_at=receipt.get("observed_at"),
        lane=str(current.get("lane") or "unobserved"),
        ttl=ttl, expires=expires, expected=expected)


def _receipt_verdict(*, expired: bool, codex_estimate: bool, at: str,
                     last_receipt_at: Any, lane: str, ttl: int,
                     expires: float, expected: int) -> dict[str, Any]:
    """The two matching-receipt outcomes, shared with legacy-row healing."""
    if expired:
        return {
            "state": "expired_known_entry",
            "source": ("codex_subscription_fixed_estimate" if codex_estimate
                       else "authoritative_receipt"),
            "reason": (
                "The fixed 30-minute Codex subscription cache estimate has "
                "elapsed; a provider miss is expected, not guaranteed."
                if codex_estimate else
                f"The observed {ttl // 60}-minute cache entry has expired."),
            "reasons": [_reason(
                "ttl", ("fixed Codex subscription cache estimate elapsed"
                        if codex_estimate else
                        "authoritative cache entry expired"), at,
                "estimated" if codex_estimate else "known")],
            "observed_at": at, "last_receipt_at": last_receipt_at,
            "lane": lane,
            "ttl_seconds": ttl, "expires_at": iso_us(expires),
            "confidence": ("estimated" if codex_estimate else "known"),
            "expected_input_tokens": expected,
        }
    return {
        "state": "compatible_observed",
        "source": ("codex_subscription_fixed_estimate" if codex_estimate
                   else "authoritative_receipt"),
        "reason": (
            "The local prefix matches a positive receipt inside the fixed "
            "30-minute Codex subscription estimate; a provider hit is not "
            "guaranteed."
            if codex_estimate else
            "The local prefix matches an unexpired positive cache receipt; "
            "a provider hit is still not guaranteed."),
        "reasons": [_reason(
            "receipt", ("matching positive receipt inside fixed Codex "
                        "subscription estimate" if codex_estimate else
                        "matching unexpired positive cache receipt"), at,
            "estimated" if codex_estimate else "observed")],
        "observed_at": at, "last_receipt_at": last_receipt_at,
        "lane": lane,
        "ttl_seconds": ttl, "expires_at": iso_us(expires),
        "confidence": "observed", "expected_input_tokens": expected,
    }


def heal_quantized_skew(forecast: Any, now: float) -> dict[str, Any] | None:
    """Reclassify one persisted false ``clock_skew`` forecast row, or None.

    The healable artifact is exact: a zero-tolerance comparison of one call's
    float clock against its own microsecond-serialized receipt stamp, provable
    because that call recorded identical ``observed_at`` and
    ``last_receipt_at`` strings. A receipt genuinely observed in the future
    carries a distinct (later) ``last_receipt_at`` and is preserved untouched,
    as is any row this cannot re-derive a verdict for. The verdict follows the
    fixed lane boundary: before the TTL the entry is compatible, after it
    expired. Codex's fixed-estimate labelling is recovered from the lane plus
    the TTL value itself — the fixed TTL table maps them one-to-one.
    """
    row = cast("dict[str, Any]", forecast) if isinstance(forecast, dict) else {}
    if (str(row.get("state") or "") != "uncertain"
            or str(row.get("source") or "") != "clock_skew"):
        return None
    at = str(row.get("observed_at") or "")
    receipt_at: Any = row.get("last_receipt_at")
    if not at or not isinstance(receipt_at, str) or at != receipt_at:
        return None
    observed = epoch(receipt_at)
    ttl_raw: Any = row.get("ttl_seconds")
    if (observed is None or isinstance(ttl_raw, bool)
            or not isinstance(ttl_raw, (int, float)) or ttl_raw <= 0):
        return None
    if observed - now > SERIALIZATION_QUANTUM_S:
        return None            # still genuinely ahead of this clock: keep it
    ttl = int(ttl_raw)
    lane = str(row.get("lane") or "unobserved")
    codex_estimate = (lane == "subscription"
                      and ttl == CODEX_SUBSCRIPTION_TTL_SECONDS)
    try:
        expected = int(row.get("expected_input_tokens") or 0)
    except (TypeError, ValueError, OverflowError):
        expected = 0
    expires = observed + ttl
    return _receipt_verdict(
        expired=max(now, observed) >= expires, codex_estimate=codex_estimate,
        at=at, last_receipt_at=receipt_at, lane=lane, ttl=ttl,
        expires=expires, expected=expected)


def public(forecast: dict[str, Any], *, generation: str,
           precompact_action: str, precompact_reason: str) -> dict[str, Any]:
    """Credential-free, atomic UI/WebSocket forecast projection."""
    state = str(forecast.get("state") or "uncertain")
    if state not in ("known_incompatible", "expired_known_entry", "uncertain",
                     "compatible_observed"):
        state = "uncertain"
    changed_inputs: list[str] = []
    if state == "known_incompatible":
        seen: set[str] = set()
        for row in forecast.get("reasons") or []:
            component = str(row.get("component") or "") \
                if isinstance(row, dict) else ""
            if component in _COMPONENT_ORDER:
                seen.add(component)
        changed_inputs = [component for component in _COMPONENT_ORDER
                          if component in seen]
    lane = str(forecast.get("lane") or "unobserved")
    if lane not in ("subscription", "api_key", "provider_unsupported",
                    "unobserved"):
        lane = "unobserved"
    if precompact_action not in (
            "will_compact", "miss_expected", "not_applicable"):
        precompact_action = "not_applicable"
    return {
        "generation": generation,
        "state": state,
        "reason": str(forecast.get("reason") or "Cache continuity is uncertain."),
        "source": str(forecast.get("source") or "unobserved"),
        "observed_at": str(forecast.get("observed_at") or ""),
        "last_receipt_at": forecast.get("last_receipt_at"),
        "expires_at": forecast.get("expires_at"),
        "lane": lane,
        "ttl_seconds": forecast.get("ttl_seconds"),
        "precompact_action": precompact_action,
        "precompact_reason": precompact_reason,
        # Always present, always secret-free.  The UI tooltip can render this
        # without guessing whether absence means "unchanged" or "old server".
        "changed_inputs": changed_inputs,
    }


def generation(current: dict[str, Any], forecast: dict[str, Any], seq: int) -> str:
    """Opaque generation for stale WebSocket-event suppression."""
    return digest({"node_generation": current.get("node_generation"),
                   "fingerprint": current.get("fingerprint"),
                   "state": forecast.get("state"), "seq": seq}, 24)


def component_names() -> tuple[str, ...]:
    """Documentation/test surface; order is stable and provider-neutral."""
    return _COMPONENT_ORDER
