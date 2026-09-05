# pyright: strict
"""Redacted failure fixtures (docs/failure-fixtures.md).

A failed turn used to leave a 400-character sentence. This module writes, at
the supervisor's existing recording sites and nowhere else, a BOUNDED,
ALLOWLIST-CAPTURED record of the failure boundary — enough to re-run the
CLASSIFIERS offline, never enough to reconstruct a prompt, a mail body, a
file, an error sentence or a credential.

Invariants (Astra's amendments, 2026-09-05T19:52Z):
  · NO PROSE. Free text never enters a fixture. From each text input only
    RECOGNISED DIAGNOSTIC TAGS are kept (`tags_of`: the classifiers' own
    phrase vocabularies, errno spellings, HTTP status numbers, exit codes,
    JSON-RPC codes, the CLI's typed error codes, a few fixed diagnoses) plus
    the input's LENGTH. An error that echoes a confidential sentence with no
    key-shaped pattern in it is therefore dropped, not scrubbed.
  · EVERY STRING LEAF IS VALIDATED against a closed vocabulary or a strict
    pattern (`_vocab`, `_version`); anything else becomes "other" or None.
  · NO IDENTITY. The fixture carries no org, node, account, host or path;
    its place on disk (<ORGTREE_DATA>/failfix/<org>/<node>/) is the only
    correlation, and that directory is the operator's own data root.
  · OBSERVED ≠ INFERRED. `observed` is what the boundary saw (started,
    boundary, exit code, typed status…); `recorded` is what the site
    DECIDED; `phase` is an inference from `observed` with "unknown" allowed.
    `replay` recomputes the predicates from the tags and reports DRIFT
    against `recorded` — it does not claim which branch the supervisor
    would take, because that also depends on state the fixture does not
    hold (the retry counter, the lane's policy, a manual pause).
  · BOUNDED. ≤ CAP_BYTES per fixture, ≤ RING per node, never in the org
    document. FAIL-OPEN: `record` never raises and never changes a turn.
  · PURE. Imports nothing that touches storage, a provider or a process;
    `replay` takes its predicates as arguments (`failclass` supplies them).
"""
from __future__ import annotations

import itertools
import json
import os
import re
import time
from typing import Any, Callable, Mapping, cast

SCHEMA = 2
_SEQ = itertools.count(1)       # per-process; `next` is atomic under the GIL
CAP_BYTES = 4096
RING = 40
TEXT_FIELDS = ("err_blob", "stderr_tail", "result_detail")

# --------------------------------------------------------------- vocabularies
# ⚠ THESE ARE THE CLASSIFIERS' OWN PHRASES (failclass.py). The suite asserts
# every literal a predicate searches for is listed here, and that a predicate
# answers the same on a blob and on that blob's tags — so a phrase added to a
# predicate without being added here fails the suite rather than the field.
LIMIT_WORDS = ("limit", "usage", "weekly", "reached", "exceeded", "quota",
               "hit your", "resets", "session", "exceed", "account",
               "rate limit")
NET_WORDS = ("econnrefused", "econnreset", "etimedout", "econnaborted",
             "enetunreach", "ehostunreach", "enotfound", "eai_again",
             "socket hang up", "fetch failed", "network error", "networkerror",
             "connection refused", "connection reset", "connection error",
             "getaddrinfo", "dns lookup failed")
FILTER_WORDS = ("content filter", "filtering policy", "content policy",
                "blocked by content", "output blocked", "flagged by")
# the CLI's typed API-error vocabulary (supervisor `_AUTH_ERROR_CODES` and the
# list read out of the 2.1.258 binary), plus provider machine tags
CODE_WORDS = ("authentication_failed", "oauth_org_not_allowed",
              "account_on_hold", "billing_error", "rate_limit",
              "model_not_found", "invalid_request", "server_error",
              "max_output_tokens", "dlp_request_denied",
              "usagelimitexceeded", "usage_limit_reached", "invalid api key",
              "authentication failure")
# fixed diagnoses orgtree itself writes into the blob
DIAG_WORDS = ("the cli exited", "without writing anything to stderr",
              "unknown option", "no conversation found", "turn killed",
              "per-message ceiling", "enospc", "enomem", "eacces", "eperm",
              "api status")
VOCAB = LIMIT_WORDS + NET_WORDS + FILTER_WORDS + CODE_WORDS + DIAG_WORDS
_STATUS_RE = re.compile(r"(?<![\d.])([45]\d\d)(?![\d.])")
_EXIT_RE = re.compile(r"the cli exited (\d{1,3})")
_RPC_RE = re.compile(r"(-32\d{3})")
_OPTION_RE = re.compile(r"unknown option '?(--[a-z][a-z0-9-]{0,24})")
TAG_MAX = 24

STREAM_CODES = frozenset(CODE_WORDS[:10])
TERMINAL_REASONS = frozenset({"api_error", "max_turns", "error", "success",
                              "interrupted", "cancelled", "aborted"})
CODEX_STATUS = frozenset({"completed", "failed", "interrupted", "in_progress"})
CODEX_POOLS = frozenset({"direct", "reserve", "<sent>"})
LANES = frozenset({"claude", "openrouter", "codex"})
SITES = frozenset({"terminal", "exhausted", "codex"})
PHASES = ("admission", "stream", "result-error", "teardown", "unknown")
_VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,4}$")


def tags_of(text: Any) -> list[str]:
    """The recognised diagnostic tags in a text, in vocabulary order, then
    the numbers (status/exit/rpc/option). Nothing else survives."""
    b = str(text or "").lower()
    if not b:
        return []
    out: list[str] = [w for w in VOCAB if w in b]
    out += [f"status:{m}" for m in dict.fromkeys(_STATUS_RE.findall(b))]
    out += [f"exit:{m}" for m in dict.fromkeys(_EXIT_RE.findall(b))]
    out += [f"rpc:{m}" for m in dict.fromkeys(_RPC_RE.findall(b))]
    out += [f"option:{m}" for m in dict.fromkeys(_OPTION_RE.findall(b))]
    return out[:TAG_MAX]


def blob_of(tags: list[str]) -> str:
    """The synthetic classifier input a tag list stands for: the phrases,
    space-joined; the numeric tags rendered as the words they were read
    from. The suite proves predicate(blob) == predicate(blob_of(tags_of(blob)))
    on the corpus."""
    words: list[str] = []
    for t in tags:
        if t.startswith("status:"):
            words.append(f"api status {t[7:]}")
        elif t.startswith("exit:"):
            words.append(f"the cli exited {t[5:]}")
        elif t.startswith("rpc:"):
            words.append(t[4:])
        elif t.startswith("option:"):
            words.append(f"unknown option '{t[7:]}'")
        else:
            words.append(t)
    return " ".join(words)


def _vocab(value: Any, allowed: frozenset[str], other: str | None = "other"
           ) -> str | None:
    if value is None or value == "":
        return None
    v = str(value).strip().lower()[:32]
    return v if v in allowed else other


def _version(value: Any) -> str | None:
    v = str(value or "").strip()
    return v if _VERSION_RE.match(v) else None


_CODEWORD_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _codeword(value: Any) -> str | None:
    """A machine code word (`usage_limit_reached`, `server_error`…): a strict
    identifier or "other" — never a sentence."""
    if value is None or value == "":
        return None
    v = str(value).strip().lower()
    return v if _CODEWORD_RE.match(v) else "other"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def ran_as_sentinel(ran_as: Any) -> str:
    r = str(ran_as or "")
    if not r:
        return ""
    return r if r in ("ambient", "api-key", "openrouter") else "account"


# ------------------------------------------------------------------ inference


def phase_of(obs: Mapping[str, Any], codex: Mapping[str, Any] | None) -> str:
    """Where the turn died, ONLY where the observed facts establish it.

    admission     nothing was observed before a typed 401/402/429 (claude
                  lanes), or the provider's terminal rejection with nothing
                  run (codex, from its own recorded rejection flag)
    stream        output began and no result boundary was ever reached
    result-error  the result boundary was reached and it carried the error
                  (is_error / a typed status): the provider answered after
                  output began
    teardown      the boundary was reached clean and the process still
                  exited nonzero
    unknown       everything else — including "no output" with no typed
                  status, which does NOT prove nothing ran."""
    if codex is not None:
        if codex.get("rejected_recorded"):
            return "admission"
        items = int(codex.get("items_seen") or 0)
        if codex.get("rpc_code") is not None and items == 0:
            return "admission"
        if items and codex.get("status") in (None, "other"):
            return "stream"
        return "unknown"
    typed = obs.get("api_error_status") or obs.get("stream_status")
    if not obs.get("started"):
        return "admission" if typed in (401, 402, 429) else "unknown"
    if not obs.get("boundary"):
        return "stream"
    if obs.get("is_error") or typed is not None:
        return "result-error"
    if (obs.get("exit_code") or 0) != 0:
        return "teardown"
    return "unknown"


def verdict_of(cl: Mapping[str, Any]) -> str:
    """The predicate-level class, in the supervisor's precedence: filtered,
    then limit, then net, else none. NOT the branch taken — that also reads
    the retry counter, the lane policy and a manual pause."""
    if cl.get("filtered"):
        return "filtered"
    if cl.get("limit"):
        return "limit"
    if cl.get("net"):
        return "net"
    return "none"


# ------------------------------------------------------------------ building


def build(*, lane: str, site: str, observed: Mapping[str, Any],
          text: Mapping[str, Any], recorded: Mapping[str, Any],
          codex: Mapping[str, Any] | None = None, ran_as: Any = "",
          cli: Mapping[str, Any] | None = None,
          at: str | None = None) -> dict[str, Any]:
    """The fixture, from the boundary's facts. ALLOWLIST: only the keys read
    here exist in the output; every string leaf is validated."""
    obs: dict[str, Any] = {
        "exit_code": _int_or_none(observed.get("exit_code")),
        "parked": bool(observed.get("parked")),
        "exit_only": bool(observed.get("exit_only")),
        "started": bool(observed.get("started")),
        "boundary": bool(observed.get("boundary")),
        "errors_n": int(_int_or_none(observed.get("errors_n")) or 0),
        "is_error": bool(observed.get("is_error")),
        "api_error_status": _int_or_none(observed.get("api_error_status")),
        "stream_status": _int_or_none(observed.get("stream_status")),
        "stream_code": _vocab(observed.get("stream_code"), STREAM_CODES),
        "terminal_reason": _vocab(observed.get("terminal_reason"),
                                  TERMINAL_REASONS),
        "run": int(_int_or_none(observed.get("run")) or 0),
        "exhausted": bool(observed.get("exhausted")),
    }
    tags: dict[str, list[str]] = {k: tags_of(text.get(k)) for k in TEXT_FIELDS}
    lens: dict[str, int] = {k: len(str(text.get(k) or "")) for k in TEXT_FIELDS}
    rec: dict[str, Any] = {
        "limit": bool(recorded.get("limit")),
        "net": bool(recorded.get("net")),
        "filtered": bool(recorded.get("filtered")),
        "typed": _int_or_none(recorded.get("typed")),
    }
    rec["verdict"] = verdict_of(rec)
    cx: dict[str, Any] | None = None
    if codex is not None:
        # an INPUT projection of codex_route.classify_failure — never its
        # outputs, except the one recorded decision kept apart by name
        cx = {
            "status": _vocab(codex.get("status"), CODEX_STATUS),
            "rpc_code": _int_or_none(codex.get("rpc_code")),
            "error_code": _codeword(codex.get("error_code")),
            "items_seen": int(_int_or_none(codex.get("items_seen")) or 0),
            "had_usage": bool(codex.get("had_usage")),
            "text_len": int(_int_or_none(codex.get("text_len")) or 0),
            "pool": _vocab(codex.get("pool"), CODEX_POOLS),
            "served": _vocab(codex.get("served"), CODEX_POOLS),
            "usage_prose": bool(codex.get("usage_prose")),
            "now": _int_or_none(codex.get("now")),
            "kind_recorded": _codeword(codex.get("kind_recorded")),
            "rejected_recorded": bool(codex.get("rejected_recorded")),
        }
    fx: dict[str, Any] = {
        "schema": SCHEMA,
        "lane": _vocab(lane, LANES) or "other",
        "site": _vocab(site, SITES) or "other",
        "at": at if at and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", str(at))
        else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "observed": obs, "tags": tags, "lens": lens, "recorded": rec,
        "codex": cx, "ran_as": ran_as_sentinel(ran_as),
        "cli": {"exit": _int_or_none((cli or {}).get("exit")),
                "version": _version((cli or {}).get("version"))},
    }
    fx["phase"] = phase_of(obs, cx)
    return fx


# ------------------------------------------------------------------- writing


def fixture_dir(root: str, org: str, node: str) -> str:
    return os.path.join(root, "failfix", str(org), str(node))


def write(root: str, org: str, node: str, fx: Mapping[str, Any]) -> str | None:
    """One fixture into the node's ring; the oldest beyond RING is evicted.
    Returns the path, or None when nothing could be written — never raises
    (the caller is a failure path already)."""
    try:
        blob = json.dumps(fx, ensure_ascii=False, indent=1)
        if len(blob.encode("utf-8")) > CAP_BYTES:
            return None
        d = fixture_dir(root, org, node)
        os.makedirs(d, exist_ok=True)
        # ms stamp + a per-process counter: two failures in one millisecond
        # (the suite's ring check) must not share a name
        stamp = int(time.time() * 1000)
        seq = next(_SEQ) % 10000
        name = (f"{stamp:013d}-{seq:04d}-{fx.get('phase')}-"
                f"{fx.get('recorded', {}).get('verdict')}.json")
        path = os.path.join(d, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(blob)
        os.replace(tmp, path)
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
        for old in (names[:-RING] if len(names) > RING else []):
            try:
                os.remove(os.path.join(d, old))
            except OSError:
                pass
        return path
    except Exception:                                        # noqa: BLE001
        return None


def record(root: str, org: str, node: str, **kw: Any) -> str | None:
    """build + write, fail-open. The ONE entry point production calls; `org`
    and `node` place the file and never enter it."""
    try:
        return write(root, org, node, build(**kw))
    except Exception:                                        # noqa: BLE001
        return None


def load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        fx = cast("dict[str, Any]", json.load(f))
    if int(fx.get("schema") or 0) != SCHEMA:
        raise ValueError(f"fixture schema {fx.get('schema')!r}, expected {SCHEMA}")
    return fx


def list_fixtures(root: str, org: str, node: str) -> list[str]:
    d = fixture_dir(root, org, node)
    try:
        return sorted(os.path.join(d, n) for n in os.listdir(d)
                      if n.endswith(".json"))
    except OSError:
        return []


# -------------------------------------------------------------------- replay

Predicates = Mapping[str, Callable[..., Any]]


def replay(fx: Mapping[str, Any], predicates: Predicates) -> dict[str, Any]:
    """Re-run the classifiers on the fixture's tags and observed facts.

    `predicates`: limit(blob), net(blob), filtered(blob),
    died_in_flight(exit_only=, started=, boundary=), typed(res, stream_err)
    — the pure functions in `failclass`. Returns
      {"recomputed": {limit, net, filtered, typed, verdict},
       "recorded": the fixture's own, "phase": re-inferred,
       "drift": [every name whose recomputed value differs from the recorded]}
    Codex outputs are NOT recomputed here (their classifier is not yet behind
    a pure boundary); the projection is carried for a later milestone."""
    obs = cast("Mapping[str, Any]", fx.get("observed") or {})
    tags = cast("Mapping[str, list[str]]", fx.get("tags") or {})
    rec = cast("Mapping[str, Any]", fx.get("recorded") or {})
    blob = blob_of(list(tags.get("err_blob") or []))
    typed: int | None = None
    if fx.get("lane") == "openrouter":
        res = {"is_error": bool(obs.get("is_error")),
               "api_error_status": obs.get("api_error_status")}
        typed = predicates["typed"](res, {"status": obs.get("stream_status")})
    if typed is None:
        limit = bool(predicates["limit"](blob))
        net = bool(predicates["net"](blob)) or bool(
            predicates["died_in_flight"](exit_only=bool(obs.get("exit_only")),
                                         started=bool(obs.get("started")),
                                         boundary=bool(obs.get("boundary"))))
    else:
        limit = typed in (401, 402, 429)
        net = typed >= 500
    cl: dict[str, Any] = {"limit": limit, "net": net,
                          "filtered": bool(predicates["filtered"](blob)),
                          "typed": typed}
    cl["verdict"] = verdict_of(cl)
    phase = phase_of(obs, cast("Mapping[str, Any] | None", fx.get("codex")))
    drift = [k for k in ("limit", "net", "filtered", "typed", "verdict")
             if rec.get(k) != cl.get(k)]
    if phase != fx.get("phase"):
        drift.append("phase")
    return {"recomputed": cl, "recorded": dict(rec), "phase": phase,
            "phase_recorded": fx.get("phase"), "drift": drift}
