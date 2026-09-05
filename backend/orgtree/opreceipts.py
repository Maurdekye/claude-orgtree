"""Durable operation receipts (docs/op-receipts.md, work item w71d69aac).

WHAT THIS ANSWERS. `mcptool.call_api` makes ONE POST to `/api/agent` with a
30 s timeout. When that response is lost — timeout, connection drop, backend
replaced — the caller cannot tell a refusal from a mutation that already
committed, and the retry re-executes it: a second hire, a second mail. The
incident is recorded at `supervisor.py` (the net-retry replay banner): "the
effects a dying turn commits are exactly the non-idempotent ones".

So every mutating agent call may carry an `op_key`, and a call whose document
transaction commits leaves a durable RECEIPT in the org document, appended
inside the SAME `DOC_LOCK` transaction as the effect. A later call with the
same key is answered from the receipt instead of being executed again. No
tool card exposes a key, so no agent's prompt prefix moves for this.

WHY A KEYED CALL IS A VERB OF ITS OWN (`orgtree_op_call`, `api._op_unwrap`).
The key first rode the request envelope, and that was wrong: a backend built
before receipts DROPS an unknown envelope field and executes the operation
anyway, leaving no receipt. Look the key up on a newer backend afterwards and
the absence of a receipt reads as "never applied — safe to reissue", which is
a licence to do it twice. Nothing observable after the fact distinguishes
that from a call that truly never landed, and a recent mint time certainly
does not. So the request is shaped so an old backend CANNOT execute it: it
refuses the unknown verb and applies nothing. That structural refusal — not a
timestamp, not a grace window — is what makes a missing receipt evidence.

WHAT A RECEIPT PROVES, AND WHAT IT DOES NOT.

  · It proves the DOCUMENT transaction committed. It says nothing about what
    happens after `save_org`: the recipient drive, `@org:`/`@net:` transport,
    the watchdog smoke run, `remote_reap`. `post_effects.observed` is
    "unknown" — never `false`, which would read as "failed".
  · It cannot cover work done OUTSIDE that transaction. Some verbs rename a
    folder, copy a transcript, launch a process or wait for a turn boundary,
    and none of that is rolled back when the transaction is discarded. Those
    verbs are classified (`COVERAGE` below) and their ABSENCE is reported as
    `unknown`, never as "not applied".
  · It is NOT exactly-once for an agent's INTENT. It de-duplicates transport
    retries of ONE tool call. A model that decides to issue the call again
    mints a new key and is admitted — that is a new intent, and no key can
    tell it from the first.

THE INVARIANT THAT MAKES "not applied" HONEST. Retention is bounded, so the
absence of a receipt is only evidence if nothing has been forgotten below it.
Every eviction — for any reason — advances a WATERMARK (`from_ms`) past the
largest mint time it evicted, and the watermark only ever increases. So:

    a key whose mint time is >= `from_ms`, with no receipt, was never applied.

Below the watermark the answer is `unknown`. That is the whole design: the
capped log may forget, and forgetting always costs a refusal, never a
duplicate.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, cast

# ---------------------------------------------------------------- constants

SCHEMA = 1                  # receipt row shape
COVERAGE = 1                # the COVERAGE table's revision
HORIZON_MS = 900_000        # 15 min: a key older than this is refused
CEILING = 500               # rows retained before an eviction runs
TRIM_TO = 400               # …and what an eviction leaves behind
SKEW_MS = 60_000            # a key minted "in the future" by more than this is refused

SECTION = "op_receipts"
META = "op_receipts_meta"

# The two verbs receipts add to the dispatch. Both are DISPATCH-ONLY on
# purpose — a backend without receipts refuses an unknown verb and executes
# nothing, which is how a client learns it cannot be protected here, and (for
# `OP_CALL`) the reason a missing receipt is evidence at all.
OP_CALL = "orgtree_op_call"
OP_LOOKUP = "orgtree_op_lookup"

# `<mint_ms>-<24 hex>`. The mint time is IN the key so the server can judge
# the key's own age without having recorded it first — which is what lets a
# never-seen key be refused as stale instead of being admitted blind.
KEY_RE = re.compile(r"^(\d{13,14})-([0-9a-f]{24})$")


def mint_key(now_ms: int | None = None) -> str:
    """Mint a client key. Only our own MCP client calls this; no tool card
    exposes a key, so an agent's model never invents one."""
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    return f"{ms}-{uuid.uuid4().hex[:24]}"


def parse_key(key: str) -> int | None:
    """The key's mint time in ms, or None when it is not one of ours."""
    m = KEY_RE.match(key or "")
    return int(m.group(1)) if m else None


# ------------------------------------------------------------- coverage map
#
# ⚠ CLASSIFIED BY READING EACH DISPATCH BRANCH, NOT BY MATCHING NAMES. A verb
# lands in `UNROLLED` because something it does is not inside the transaction
# the receipt shares — a process launch, a file copy, a sidecar write — and
# `test_op_receipts` fails when a verb reachable in the dispatch is missing
# here, so this table cannot quietly go stale.
#
#   TX        the whole effect is the document transaction
#   TX_POST   plus effects that run AFTER `save_org` (drive, transport,
#             smoke run, reap) which the receipt does not prove
#   PRE       an irreversible step runs BEFORE the transaction
#   UNROLLED  an external side effect runs INSIDE the lock and is not rolled
#             back when the transaction is discarded
#   NONE      never reaches the transaction at all — no receipt
#
# Only TX and TX_POST may ever be answered "not applied": for those two, no
# commit means nothing happened anywhere. PRE and UNROLLED answer `unknown`.
TX, TX_POST, PRE, UNROLLED, NONE = ("transaction", "transaction+post",
                                    "pre_transaction", "unrolled_side_effect",
                                    "none")

_COVERAGE_STATIC: dict[str, str] = {
    # -- no document transaction ------------------------------------------
    # read-only branch (api.py, the early `if body.tool in (…)`)
    "orgtree_read_transcript": NONE,
    "orgtree_read_scratch": NONE,
    "orgtree_chart": NONE,
    "orgtree_list_tiers": NONE,
    "orgtree_send_file": NONE,        # copies bytes; no doc transaction to share
    "orgtree_rename": NONE,           # takes DOC_LOCK itself and moves folders
    "orgtree_list_orgs": NONE,        # reads the store and the hub roster
    # -- external side effects that survive a discarded transaction -------
    # `launch_self_restart` / the primed loop start a DETACHED process from
    # inside the lock; the gate's org-log entry rides the save, the process
    # does not.
    "orgtree_self_restart": UNROLLED,
    "orgtree_self_update": UNROLLED,  # the deprecated alias, same branch
    "orgtree_prime_restart": UNROLLED,
    # `restart_wake` persists to its OWN sidecar file (restart_wake._wakes_write),
    # not to the org document — so the doc transaction cannot cover it.
    "orgtree_restart_wake": UNROLLED,
    # `export_predecessor_transcript` copies the predecessor's transcript
    # FILE inside the lock.
    "orgtree_cheap_compact": UNROLLED,
    # `supervisor.interrupt_turn` signals a live process inside the lock.
    "orgtree_interrupt": UNROLLED,
    # -- irreversible work BEFORE the transaction -------------------------
    # both wait for the target's turn boundary (`interrupt_before_archive`)
    # before the lock is taken.
    "orgtree_retire": PRE,
    "orgtree_dissolve": PRE,
    # -- document transaction, plus post-commit effects -------------------
    "orgtree_message": TX_POST,       # drive / @org: / @net: / mail_to
    "orgtree_send_notice": TX_POST,   # notice_to
    "orgtree_hire": TX_POST,          # _seat_finish appends the seat to drive
    "orgtree_retool": TX_POST,        # ditto, for a retool that kicks off
    "orgtree_switch_model": TX_POST,  # stale_freeze_resumed wakes
    "orgtree_status": TX_POST,        # done/blocked reports to the parent
    "orgtree_audience": TX_POST,      # drives the grantee/forwarded superior
    "orgtree_ask": TX_POST,           # `routed` → drives the superior
    "orgtree_request_scope": TX_POST,  # ditto
    "orgtree_request_credits": TX_POST,
    "orgtree_present": TX_POST,       # routed when it lands on a superior
    # -- document transaction only ----------------------------------------
    "orgtree_withdraw_ask": TX,
    "orgtree_move": TX,
    "orgtree_swap": TX,
    "orgtree_self_subjugate": TX,
    "orgtree_reallocate": TX,
}

# Verbs whose coverage depends on the ARGUMENTS, not the name (Astra's
# correction: "classification covers actions within multipurpose tools").
_ACTION_COVERAGE: dict[str, dict[str, str]] = {
    # list/get/verify never reach the shared transaction — list/get are
    # answered before the lock and `verify` sequences its own lock → git →
    # lock. Every other action is ordinary docket work inside the transaction.
    "orgtree_work": {"list": NONE, "get": NONE, "verify": NONE},
    # arming a dog runs its target ONCE, after the commit, through the same
    # `_wd_popen` the engine uses; the other actions are document-only.
    "orgtree_watchdog": {"create": TX_POST},
}
_ACTION_DEFAULT: dict[str, str] = {
    "orgtree_work": TX,
    "orgtree_watchdog": TX,
}


def coverage(tool: str, args: dict[str, Any] | None = None) -> str:
    """The coverage class of THIS call — arguments included."""
    a = args or {}
    if tool == "orgtree_rehire":
        # a rehire that also RENAMES moves folders on disk before the
        # transaction (and the dispatch says so in its own refusal); a plain
        # rehire is an ordinary transaction with a kickoff drive.
        return PRE if str(a.get("name") or "").strip() else TX_POST
    if tool in _ACTION_COVERAGE:
        act = str(a.get("action") or "")
        return _ACTION_COVERAGE[tool].get(act, _ACTION_DEFAULT[tool])
    return _COVERAGE_STATIC.get(tool, "")      # "" = unknown verb


def receipted(tool: str, args: dict[str, Any] | None = None) -> bool:
    """Does this call get a receipt at all? (NONE-class verbs never reach the
    transaction, so there is nothing to commit a receipt with.)"""
    return coverage(tool, args) in (TX, TX_POST, PRE, UNROLLED)


def provable_absence(cls: str) -> bool:
    """May the absence of a receipt be reported as "not applied"?"""
    return cls in (TX, TX_POST)


# ----------------------------------------------------------- fingerprinting

# Stored per tool, and NOTHING outside the list is kept. Astra's ruling: a
# strict field allowlist, not a generic truncation of whatever came back —
# truncating a body still stores a body.
_RESULT_FIELDS: dict[str, tuple[str, ...]] = {
    "orgtree_message": ("delivered", "deferred", "id"),
    "orgtree_send_notice": ("delivered", "deferred", "id"),
    "orgtree_hire": ("node", "name", "tier", "grant", "started"),
    "orgtree_rehire": ("node", "name", "tier", "started"),
    "orgtree_retool": ("node", "started"),
    "orgtree_retire": ("archived", "node"),
    "orgtree_dissolve": ("archived", "node"),
    "orgtree_reallocate": ("node", "delta", "grant"),
    "orgtree_switch_model": ("node", "tier", "queued"),
    "orgtree_status": ("recorded", "reported_to", "delivered"),
    "orgtree_work": ("created", "updated", "assigned", "id", "rev", "status"),
    "orgtree_ask": ("id", "routed", "deferred"),
    "orgtree_request_scope": ("id", "routed", "deferred"),
    "orgtree_request_credits": ("id", "routed", "deferred"),
    "orgtree_present": ("id", "routed", "deferred"),
    "orgtree_watchdog": ("id", "state", "created", "removed"),
    "orgtree_audience": ("granted", "revoked", "routed"),
    "orgtree_move": ("moved",),
    "orgtree_swap": ("swapped",),
    "orgtree_self_subjugate": ("parent",),
    "orgtree_withdraw_ask": ("withdrawn",),
    "orgtree_cheap_compact": ("node", "old_session"),
    "orgtree_interrupt": ("interrupted",),
    "orgtree_self_restart": ("target",),
    "orgtree_self_update": ("target",),
    "orgtree_prime_restart": ("state", "armed"),
    "orgtree_restart_wake": ("armed", "cancelled", "state"),
}
# identity-shaped arguments worth keeping on the row. Bodies, charters,
# kickoffs, questions and summaries are deliberately absent.
_TARGET_ARGS = ("node", "to", "id", "action", "stage", "tier", "target",
                "grantee", "from", "name")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def fingerprint(tool: str, node: str, generation: int,
                args: dict[str, Any]) -> str:
    """FULL sha256 (Astra's correction — a truncated fingerprint makes a
    collision between two different calls under one key thinkable) over the
    canonical form of the whole call identity, generation included."""
    return hashlib.sha256(_canonical(
        {"tool": tool, "node": node, "generation": int(generation),
         "args": args}).encode("utf-8")).hexdigest()


def _targets(args: dict[str, Any]) -> dict[str, str]:
    return {k: str(args[k])[:80] for k in _TARGET_ARGS
            if isinstance(args.get(k), (str, int, float, bool))}


def result_slice(tool: str, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    r = cast("dict[str, Any]", result)
    out: dict[str, Any] = {}
    for k in _RESULT_FIELDS.get(tool, ()):
        if k in r and isinstance(r[k], (str, int, float, bool)):
            out[k] = r[k] if not isinstance(r[k], str) else r[k][:200]
    return out


# --------------------------------------------------------- the log and meta


def _log(d: dict[str, Any], create: bool) -> list[dict[str, Any]]:
    """The receipt rows. ⚠ TOUCHING THIS MATERIALISES THE SECTION, which is
    measured at ~3 ms per call at 300 rows and ~23 ms at 2000
    (evidence/receipt-cost.json) — so nothing calls it unless the request
    actually carries a key."""
    if not create and SECTION not in d:
        return []
    return cast("list[dict[str, Any]]", d.setdefault(SECTION, []))


def _meta(d: dict[str, Any], now_ms: int, create: bool) -> dict[str, Any]:
    m = cast("dict[str, Any] | None", d.get(META))
    if m is None:
        if not create:
            return {}
        # `from_ms` starts at 0: nothing has ever been evicted, so nothing is
        # unprovable yet. There is deliberately NO "receipts began at" time
        # here. One lived here until 2026-09-05 and admitted keys minted
        # within five minutes of it as covered — which is exactly the claim
        # a mint time cannot support (see the module docstring). Coverage is
        # established by the SHAPE of the request, not by its age.
        m = {"schema": SCHEMA, "coverage": COVERAGE, "created_at": now_ms,
             "from_ms": 0, "horizon_ms": HORIZON_MS, "ceiling": CEILING,
             "trim_to": TRIM_TO, "evicted": 0}
        d[META] = m
    return m


def watermark(d: dict[str, Any]) -> int:
    m = cast("dict[str, Any]", d.get(META) or {})
    return int(m.get("from_ms") or 0)


def stamp_export(doc: dict[str, Any], now_ms: int | None = None) -> None:
    """Mark an EXPORTED COPY as a point-in-time snapshot (`store.export_json`
    calls this on the reconstructed document, never on a live one).

    A document restored from an export is a world that stopped at the export.
    Everything the live system did between the export and the restore is gone
    from it — including receipts whose effects were NOT undone, because a
    document rollback does not recall mail already delivered to another org or
    a process already started. So a key minted after this snapshot was taken
    can never be judged from the restored document, and the honest answer for
    it is `unknown`.

    The stamp is cleared by the first receipt appended after the restore,
    which also lifts the watermark to that moment (see `append`) — so the
    window in which everything is unknown is exactly the window in which
    nothing here can know anything."""
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    m = cast("dict[str, Any] | None", doc.get(META))
    if m is None:
        # ⚠ EVEN WITH NO RECEIPTS. A document that never carried one still
        # needs the stamp: without it, a key minted between the export and
        # the restore finds no row, no meta and a watermark of 0, and reads
        # as "never applied" — which is the whole failure, on the document
        # least able to argue with it.
        m = {"schema": SCHEMA, "coverage": COVERAGE, "from_ms": 0,
             "horizon_ms": HORIZON_MS, "ceiling": CEILING,
             "trim_to": TRIM_TO, "evicted": 0}
        doc[META] = m
    m["export_stamp"] = ms


def export_stamp(d: dict[str, Any]) -> int:
    m = cast("dict[str, Any]", d.get(META) or {})
    return int(m.get("export_stamp") or 0)


def restored_at(d: dict[str, Any]) -> int:
    m = cast("dict[str, Any]", d.get(META) or {})
    return int(m.get("restored_at") or 0)


def resume_after_restore(d: dict[str, Any], now_ms: int) -> bool:
    """The first receipt-layer touch of a RESTORED document converts its
    snapshot stamp into an ordinary watermark at this moment.

    ⚠ THE EXPORT'S OWN TIME IS DELIBERATELY NOT USED (Astra, 2026-09-05).
    Refusing only keys minted after the export was wrong: a key minted BEFORE
    the snapshot whose operation applied AFTER it is missing from the restored
    document in exactly the same way, so a mint time cannot establish custody
    here either. What is true is narrower and needs no arithmetic — on a
    restored document the absence of a receipt proves nothing about anything,
    until the document is recording again. Every key minted before the restore
    therefore reads `unknown`, and every key minted after it is covered
    normally, because its whole life is inside the restored document's own
    record.

    Idempotent, and safe to run in a transaction that is then discarded: the
    watermark only ever rises, and a rise only ever costs a refusal."""
    if not export_stamp(d):
        return False
    m = cast("dict[str, Any]", d[META])
    m.pop("export_stamp", None)
    m["restored_at"] = now_ms
    m["from_ms"] = max(int(m.get("from_ms") or 0), now_ms)
    return True


def schema_ahead(d: dict[str, Any]) -> str:
    """A document written by a NEWER receipts build, now being read by this
    one. Its rows were admitted and classified by rules this build does not
    have, so this build cannot say what their absence means — the same
    reasoning that removed the bootstrap grace, applied in the other
    direction. Returns a reason, or "" when this build is current."""
    m = cast("dict[str, Any]", d.get(META) or {})
    if int(m.get("schema") or 0) > SCHEMA:
        return f"row schema {m.get('schema')} > {SCHEMA}"
    if int(m.get("coverage") or 0) > COVERAGE:
        return f"coverage table {m.get('coverage')} > {COVERAGE}"
    return ""


def find(d: dict[str, Any], node: str, key: str) -> dict[str, Any] | None:
    """The row for this key on this node.

    ⚠ GENERATION IS NOT A MATCHER (Astra, 2026-09-05). It was, and that made
    a receipt INVISIBLE the moment the seat's session lineage changed: the
    call applied at generation g, the answer was lost, the seat compacted,
    and then the lookup (which reads the CURRENT generation) found nothing and
    said "not applied — safe to reissue", while a delayed original arriving on
    the same key was admitted and ran a second time. Both are the duplicate
    this file exists to prevent.

    A key belongs to the CALL that minted it, not to an incarnation. Finding
    it under any generation means it has been used; what the generation
    decides is how the row may be ANSWERED, not whether it is seen — see
    `admit` (a foreign generation can never be admitted) and the lookup (which
    compares the fingerprint at the ROW's generation, because that is the one
    it was computed with)."""
    for row in reversed(_log(d, create=False)):
        if row.get("key") == key and row.get("node") == node:
            return row
    return None


# ------------------------------------------------------------- admission
#
# Every one of these decisions is taken INSIDE the caller's single DOC_LOCK
# acquisition — the same one that mutates the document and calls `save_org`.
# It cannot move earlier: a check before the lock is a time-of-check /
# time-of-use hole, and two concurrent duplicates would both pass it.

ADMIT, REPLAY, CONFLICT, REFUSE = "admit", "replay", "conflict", "refuse"


def admit(d: dict[str, Any], node: str, generation: int, key: str, tool: str,
          args: dict[str, Any], now_ms: int | None = None
          ) -> tuple[str, dict[str, Any]]:
    """Decide what happens to a keyed call. Returns (decision, info)."""
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    mint = parse_key(key)
    if mint is None:
        return REFUSE, {"reason": "malformed_key",
                        "detail": "op_key must be `<mint_ms>-<24 hex>`"}
    if mint > ms + SKEW_MS:
        return REFUSE, {"reason": "key_from_the_future",
                        "detail": f"minted {(mint - ms) / 1000:.0f}s ahead of "
                                  f"this server's clock"}
    if ms - mint > HORIZON_MS:
        return REFUSE, {"reason": "key_stale",
                        "detail": f"minted {(ms - mint) / 1000:.0f}s ago; the "
                                  f"receipt horizon is {HORIZON_MS // 1000}s"}
    row = find(d, node, key)
    if row is not None:
        if row.get("outcome") == "fenced":
            # a lookup already fenced this key: the caller was told the
            # operation had not been recorded, so it must never apply now
            return REFUSE, {"reason": "fenced", "row": row,
                            "detail": "this key was fenced by a lookup at "
                                      f"{row.get('at')} — it can no longer be "
                                      "admitted; issue a fresh key"}
        if int(row.get("gen") or 0) != int(generation):
            # the key was used by an EARLIER INCARNATION of this seat. There
            # is no safe reading: the receipt proves the call already applied
            # once, and the seat that would run it now is not the one that
            # issued it. Refuse — never execute, never replay somebody else's
            # result as this incarnation's.
            return REFUSE, {"reason": "foreign_generation", "row": row,
                            "detail": f"this key was used at generation "
                                      f"{row.get('gen')} and this seat is now "
                                      f"at generation {generation}; the "
                                      f"operation ALREADY APPLIED and will "
                                      f"not be run again. Issue a fresh key "
                                      f"if you mean to do it now."}
        fp = fingerprint(tool, node, generation, args)
        if row.get("tool") != tool or row.get("fp") != fp:
            return CONFLICT, {"row": row, "reason": "key_reused",
                              "detail": f"this key already identifies "
                                        f"{row.get('tool')} at {row.get('at')}"}
        return REPLAY, {"row": row}
    resume_after_restore(d, ms)
    ahead = schema_ahead(d)
    if ahead:
        return REFUSE, {"reason": "schema_ahead",
                        "detail": f"this document's receipts were written by a "
                                  f"newer build ({ahead}); this one cannot "
                                  f"judge them, so the outcome is unknown"}
    # UNCONDITIONAL. It used to be skipped when the document had no meta at
    # all, which made "no receipts have ever been written here" a silently
    # different code path from "receipts exist and nothing was evicted". Both
    # mean a watermark of 0; say so once.
    if mint < watermark(d):
        if mint < restored_at(d):
            return REFUSE, {
                "reason": "restored_from_export",
                "detail": "this document was restored from an export after "
                          "the key was minted, so whatever happened to the "
                          "key was rolled out of the document and cannot be "
                          "read back — and a rollback does not recall mail "
                          "already delivered or a process already started. "
                          "The outcome is unknown"}
        return REFUSE, {"reason": "horizon_evicted",
                        "detail": "receipts for keys this old have been "
                                  "evicted; the outcome is unknown"}
    return ADMIT, {"mint_ms": mint}


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def append(d: dict[str, Any], row: dict[str, Any], now_ms: int | None = None
           ) -> dict[str, Any]:
    """File a row and evict if the log is over the ceiling. Returns the meta."""
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    m = _meta(d, ms, create=True)
    # a receipt can be the FIRST touch after a restore (the admission that
    # produced it converts the stamp, but a caller that appends without
    # admitting — the fence — must not leave a stamped document recording)
    resume_after_restore(d, ms)
    log = _log(d, create=True)
    log.append(row)
    if len(log) > CEILING:
        # ONE eviction rule, and it runs in a batch: the trim rewrites the
        # whole section (measured — the SQLite row seqs are renumbered),
        # while an ordinary append does not, so paying it once per
        # CEILING-TRIM_TO calls is the difference between ~6 ms and ~6 ms
        # amortised instead of every call.
        cut = len(log) - TRIM_TO
        evicted = log[:cut]
        # ⚠ THE WATERMARK MOVES PAST THE LARGEST MINT TIME EVICTED, not past
        # the newest row's `at`. A key minted with allowed future skew can be
        # evicted while its mint time is still ahead of "now" — comparing
        # against `at` would leave exactly that key admissible with its
        # receipt gone, which is the one thing this design must never do.
        hi = max((int(r.get("mint_ms") or 0) for r in evicted), default=0)
        m["from_ms"] = max(int(m.get("from_ms") or 0), hi + 1)  # monotonic: a
        # clock that rolls back can never lower a watermark already raised
        m["evicted"] = int(m.get("evicted") or 0) + len(evicted)
        d[SECTION] = log[cut:]      # slice assignment — the batched rewrite
    return m


def row(*, op_id: str, node: str, generation: int, key: str, mint_ms: int,
        tool: str, args: dict[str, Any], cls: str, outcome: str, at: str,
        result: Any = None, ev: tuple[int | None, int | None] = (None, None),
        post_expected: list[str] | None = None,
        summary: str = "") -> dict[str, Any]:
    """One receipt row. Note what is NOT here: no argument bodies, no charter,
    no kickoff, no question text — only a full fingerprint and identity-shaped
    arguments."""
    r: dict[str, Any] = {
        "v": SCHEMA, "id": op_id, "at": at, "mint_ms": int(mint_ms),
        "node": node, "gen": int(generation), "tool": tool, "key": key,
        "fp": fingerprint(tool, node, generation, args),
        "targets": _targets(args), "cls": cls, "outcome": outcome,
    }
    if summary:
        r["summary"] = summary[:200]
    if outcome == "applied":
        r["result"] = result_slice(tool, result)
        # DOCUMENT events only. These are indices into the unbounded `events`
        # log and say nothing about post-commit effects.
        r["ev_from"], r["ev_to"] = ev
        r["post_effects"] = {
            # what the dispatch will attempt after `save_org` …
            "expected": list(post_expected or []),
            # … and the honest state of every one of them. NOT `false`:
            # false reads as "failed", and nothing here observed anything.
            "observed": "unknown",
        }
    return r
