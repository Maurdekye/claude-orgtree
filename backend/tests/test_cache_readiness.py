"""D-226 — cache-readiness is binary, and every grey is an accounted fault.

Plain deterministic checks; no provider/network calls. Run with:
    python backend/tests/test_cache_readiness.py

WHAT IS ACTUALLY UNDER TEST
---------------------------
The user's invariant, not this module's convenience. Restated:

  * readiness is GREEN or RED in normal operation;
  * GREY is permitted ONLY for an explicit, enumerated error/diagnostic, and
    every grey carries a machine-readable cause AND a user-facing explanation;
  * there is NO catch-all fallthrough — an unclassified condition is itself a
    named `internal_error` state, and it is logged;
  * GREEN requires affirmative evidence; a provider hit is never fabricated;
  * a lane that publishes no readiness statistic (Antigravity, Codex API-key) is an
    accounted capability diagnostic, never a silent unknown.

⚠ AN INVARIANT OUTRANKS A DECISION (user ruling 2026-09-02). D-226 exists to
IMPLEMENT the invariant above; it may add stricter guarantees but may not
weaken, reinterpret, or except it. Where this file and D-226 ever disagree with
the invariant, the invariant wins and the disagreement is an enforcement bug —
so the exhaustiveness checks below are written against the invariant's
properties rather than against the current contents of the cause table.

The failure mode this suite exists to catch is SILENT: a new branch added to
`classify` that forgets its verdict does not crash, it just renders a neutral
grey that looks like ordinary uncertainty. §1 makes that impossible by proving
the mapping is total in both directions.
"""

from __future__ import annotations

import atexit
import copy
import os
import shutil
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DATA = tempfile.mkdtemp(prefix="orgtree-cache-readiness-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from orgtree import cachecontinuity as C, store, supervisor as S  # noqa: E402
from orgtree.ledger import USER                                   # noqa: E402

S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None

atexit.register(lambda: shutil.rmtree(DATA, ignore_errors=True))

NOW = 1788253200.0
PASS = FAIL = 0


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as exc:
        FAIL += 1
        print(f"  FAIL    {label}: {exc}")
        import traceback
        traceback.print_exc()


def eq(got: Any, want: Any) -> None:
    assert got == want, f"got {got!r}; want {want!r}"


def snapshot(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider": "claude", "account": "primary", "lane": "subscription",
        "model": "claude-sonnet-5", "session": "session-a",
        "captured_at": C.iso(NOW),
        "components": {k: f"{k}-same" for k in
                       ("system", "tools", "argv", "env", "startup", "lineage")},
        "expected_input_tokens": 120000,
        "last_turn_history_relation": "same_or_appended",
        "receipt_history_relation": "same_or_appended",
    }
    row.update(changes)
    return row


def book(*, receipt_at: float | None = None, ttl: int = 3600,
         **ident: Any) -> dict[str, Any]:
    last = snapshot(**ident)
    last.pop("last_turn_history_relation", None)
    last.pop("receipt_history_relation", None)
    out: dict[str, Any] = {"last_turn": last}
    if receipt_at is not None:
        receipt = dict(last)
        receipt["observed_at"] = C.iso_us(receipt_at)
        receipt["ttl_seconds"] = ttl
        receipt["expires_at"] = C.iso_us(receipt_at + ttl)
        out["receipt"] = receipt
    return out


def classify(cur: dict[str, Any], prior: dict[str, Any] | None,
             now: float = NOW) -> dict[str, Any]:
    return C.classify(cur, prior, now)


# ── §1 the mapping is TOTAL, in both directions ───────────────────────────
# This is the section that makes "no catch-all" enforceable. Everything else
# in the file is a specific case; these two are the general property.

def every_cause_has_a_verdict_and_copy() -> None:
    eq(set(C.READINESS), set(C.READINESS_DETAIL))
    for cause, readiness in C.READINESS.items():
        assert readiness in ("ready", "not_ready", "diagnostic", "none"), cause
        detail = C.READINESS_DETAIL[cause]
        # Copy must be a real sentence a user can act on, not a slug echo.
        assert len(detail) > 40, f"{cause}: explanation too thin: {detail!r}"
        assert detail.strip().endswith("."), f"{cause}: not a sentence"


def unknown_causes_collapse_to_a_named_error() -> None:
    # The whole point: an unrecognised cause cannot come back neutral, and the
    # rejected value survives into the detail so the incident is traceable.
    row = C.readiness_fields("something_nobody_defined")
    eq(row["readiness"], "diagnostic")
    eq(row["readiness_cause"], "internal_error")
    assert "something_nobody_defined" in row["readiness_detail"]
    # An empty cause is equally unclassified, and equally loud.
    eq(C.readiness_fields("")["readiness_cause"], "internal_error")


def grey_is_only_ever_an_enumerated_fault() -> None:
    # The invariant's core asymmetry: greens and reds are cache verdicts,
    # greys are faults. Nothing may be grey without being in this set.
    greys = {c for c, r in C.READINESS.items() if r == "diagnostic"}
    eq(greys, {"unsupported_capability", "receipt_timestamp_unreadable",
               "clock_anomaly", "internal_error"})
    # ...and every one of them must be able to name its instance evidence.
    for cause in greys:
        assert cause in C.EVIDENCE_REQUIRED, f"{cause} may render unaccounted"


check("every cause carries a verdict and a real explanation",
      every_cause_has_a_verdict_and_copy)
check("an unclassified cause becomes the NAMED internal error, traceably",
      unknown_causes_collapse_to_a_named_error)
check("grey is exactly the enumerated fault set, all evidence-bearing",
      grey_is_only_ever_an_enumerated_fault)


# ── §2 the user's explicitly requested matrix ─────────────────────────────

def fresh_supported_lane_has_no_cache_flag() -> None:
    # A cache flag is a statement about an existing cache. When there has been
    # no completed turn there is no cache at all, so neither "ready" nor
    # "not ready" is true. Readiness is "none", rendering no cache flag.
    row = classify(snapshot(), {})
    eq(row["readiness"], "none")
    eq(row["readiness_cause"], "no_completed_fingerprint")
    # Red here must NOT claim a miss — there is no entry to miss.
    assert "not a miss" in row["readiness_detail"], row["readiness_detail"]


def compatible_receipt_is_green_with_a_boundary() -> None:
    row = classify(snapshot(), book(receipt_at=NOW - 60))
    eq(row["readiness"], "ready")
    eq(row["readiness_cause"], "receipt_valid")
    eq(row["state"], "compatible_observed")
    # A countdown is only honest with an authoritative expiry to count to.
    assert row["expires_at"], "green must carry the boundary it counts down to"
    # ...and green must never promise the provider will actually hit. Both
    # negations are acceptable; promising one is not.
    detail = row["readiness_detail"]
    assert ("never guaranteed" in detail or "not guaranteed" in detail), detail


def expiry_is_red() -> None:
    row = classify(snapshot(), book(receipt_at=NOW - 7200))
    eq(row["readiness"], "not_ready")
    eq(row["readiness_cause"], "receipt_expired")
    eq(row["state"], "expired_known_entry")


def namespace_and_prefix_changes_are_red() -> None:
    prior = book(receipt_at=NOW - 60)
    cases = {
        "provider": snapshot(provider="openai", lane="subscription"),
        "account": snapshot(account="other"),
        "model": snapshot(model="other-model"),
        "session": snapshot(session="session-b"),
        "lane": snapshot(lane="api_key"),
    }
    for label, cur in cases.items():
        row = classify(cur, prior)
        eq((label, row["readiness"]), (label, "not_ready"))
        eq((label, row["readiness_cause"]), (label, "prefix_changed"))
    # a rewritten already-sent prefix is the same verdict
    rewritten = snapshot()
    rewritten["components"] = {**rewritten["components"], "system": "rewritten"}
    eq(classify(rewritten, prior)["readiness_cause"], "prefix_changed")


def unobserved_history_is_red() -> None:
    row = classify(snapshot(last_turn_history_relation="unobserved"),
                   book(receipt_at=NOW - 60))
    eq(row["readiness"], "not_ready")
    eq(row["readiness_cause"], "history_unobserved")


def no_receipt_on_a_supported_lane_is_red() -> None:
    row = classify(snapshot(), book())
    eq(row["readiness"], "not_ready")
    eq(row["readiness_cause"], "no_positive_receipt")


def unverifiable_receipt_prefix_is_red() -> None:
    row = classify(snapshot(receipt_history_relation="unobserved"),
                   book(receipt_at=NOW - 60))
    eq(row["readiness"], "not_ready")
    eq(row["readiness_cause"], "receipt_prefix_unobserved")


check("a supported lane with no completed turn has readiness NONE (no cache flag)",
      fresh_supported_lane_has_no_cache_flag)
check("a matching unexpired receipt is GREEN and carries its boundary",
      compatible_receipt_is_green_with_a_boundary)
check("an elapsed entry is RED", expiry_is_red)
check("provider/account/model/session/lane/prefix changes are RED",
      namespace_and_prefix_changes_are_red)
check("unobserved history is RED", unobserved_history_is_red)
check("a supported lane with no positive receipt is RED",
      no_receipt_on_a_supported_lane_is_red)
check("an unverifiable receipt prefix is RED", unverifiable_receipt_prefix_is_red)


# ── §3 the capability diagnostic, and what it must NOT swallow ────────────

def unsupported_lanes_are_accounted_diagnostics() -> None:
    for provider, lane in (("google", "subscription"),
                           ("google", "provider_unsupported"),
                           ("openai", "api_key")):
        ident = {"provider": provider, "lane": lane, "account": "a",
                 "model": "m"}
        row = classify(snapshot(**ident), book(receipt_at=NOW - 60, **ident))
        label = f"{provider}/{lane}"
        eq((label, row["readiness"]), (label, "diagnostic"))
        eq((label, row["readiness_cause"]), (label, "unsupported_capability"))
        # ACCOUNTED means it names the provider, the lane, and the way out —
        # a constant "unsupported" sentence would be the banned generic grey.
        detail = row["readiness_detail"]
        assert provider in detail, detail
        assert lane in detail, detail
        assert "claude/subscription" in detail, detail


def capability_never_outranks_a_known_incompatibility() -> None:
    # ⚠ ORDERING REGRESSION GUARD. A seat that MOVED to an unsupported lane has
    # both facts true: the prefix changed, and the new lane cannot report. The
    # prefix change is a POSITIVE determination that the next turn is cold,
    # which the invariant wants shown as red — grey is only for where no
    # opinion can be formed. An earlier draft returned the capability grey here
    # and silently lost a known-cold verdict.
    prior = book(receipt_at=NOW - 60)                 # claude/subscription
    row = classify(snapshot(provider="openai", lane="api_key",
                            account="other", model="other-model"), prior)
    eq(row["state"], "known_incompatible")
    eq(row["readiness"], "not_ready")
    eq(row["readiness_cause"], "prefix_changed")


def an_unobserved_lane_is_red_not_a_capability_claim() -> None:
    # We have not looked yet, which is NOT the same as the provider being
    # unable to report. Conflating them would slander a working provider.
    ident = {"provider": "", "lane": ""}
    row = classify(snapshot(**ident), book(receipt_at=NOW - 60, **ident))
    eq(row["readiness"], "not_ready")
    eq(row["readiness_cause"], "lane_unobserved")
    assert "resolve on its own" in row["readiness_detail"]


check("Antigravity and Codex API-key lanes are accounted capability diagnostics",
      unsupported_lanes_are_accounted_diagnostics)
check("a known incompatibility outranks the capability diagnostic",
      capability_never_outranks_a_known_incompatibility)
check("an unobserved lane is RED and self-resolving, not a capability claim",
      an_unobserved_lane_is_red_not_a_capability_claim)


# ── §4 the remaining enumerated faults ────────────────────────────────────

def future_receipt_is_a_clock_diagnostic() -> None:
    row = classify(snapshot(), book(receipt_at=NOW + 300))
    eq(row["readiness"], "diagnostic")
    eq(row["readiness_cause"], "clock_anomaly")
    detail = row["readiness_detail"]
    # evidence: both stamps and the measured skew, plus a remediation
    assert "ahead by" in detail, detail
    assert "backend clock" in detail, detail
    assert "clock synchronisation" in detail, detail


def unreadable_receipt_stamp_is_a_data_diagnostic() -> None:
    prior = book(receipt_at=NOW - 60)
    prior["receipt"]["observed_at"] = "not-a-timestamp"
    row = classify(snapshot(), prior)
    eq(row["readiness"], "diagnostic")
    eq(row["readiness_cause"], "receipt_timestamp_unreadable")
    assert "not-a-timestamp" in row["readiness_detail"]


check("a receipt stamped in the future is a clock diagnostic with evidence",
      future_receipt_is_a_clock_diagnostic)
check("an unreadable receipt stamp is a named data diagnostic",
      unreadable_receipt_stamp_is_a_data_diagnostic)


# ── §5 every branch is covered, and the projection cannot fail open ───────

def every_reachable_cause_is_exercised() -> None:
    """Sweep the real classifier and prove the emitted set is the whole set.

    ⚠ THIS IS THE EXHAUSTIVENESS PROOF the ruling asks for. It is written as a
    sweep of `classify` rather than a hand-listed table so that a branch added
    tomorrow with a forgotten verdict shows up as an unknown cause here.
    """
    prior = book(receipt_at=NOW - 60)
    unreadable = book(receipt_at=NOW - 60)
    unreadable["receipt"]["observed_at"] = "not-a-timestamp"
    antigravity = {"provider": "google", "lane": "subscription", "account": "a",
              "model": "m"}
    blank = {"provider": "", "lane": ""}
    rewritten = snapshot()
    rewritten["components"] = {**rewritten["components"], "system": "changed"}
    scenarios: list[tuple[dict[str, Any], dict[str, Any] | None, float]] = [
        (snapshot(), {}, NOW),                        # no fingerprint
        (snapshot(), book(), NOW),                    # no receipt
        (snapshot(), prior, NOW),                     # green
        (snapshot(), book(receipt_at=NOW - 7200), NOW),          # expired
        (rewritten, prior, NOW),                                  # changed
        (snapshot(last_turn_history_relation="unobserved"), prior, NOW),
        (snapshot(receipt_history_relation="unobserved"), prior, NOW),
        (snapshot(**antigravity), book(receipt_at=NOW - 60, **antigravity), NOW),
        (snapshot(**blank), book(receipt_at=NOW - 60, **blank), NOW),
        (snapshot(), book(receipt_at=NOW + 300), NOW),            # clock
        (snapshot(), unreadable, NOW),                            # unreadable
    ]
    seen: set[str] = set()
    for cur, prior_book, now in scenarios:
        row = classify(cur, prior_book, now)
        cause = row.get("readiness_cause")
        assert cause in C.READINESS, f"branch emitted unknown cause {cause!r}"
        assert row.get("readiness") == C.READINESS[cause], cause
        if cause in C.EVIDENCE_REQUIRED:
            base = C.READINESS_DETAIL[cause]
            assert row["readiness_detail"] != base, (
                f"{cause} rendered with no instance evidence")
        seen.add(str(cause))
    # Codex's green wears a different cause than Claude's.
    codex = {"provider": "openai", "lane": "subscription", "account": "a",
             "model": "m"}
    seen.add(str(classify(snapshot(**codex),
                          book(receipt_at=NOW - 60, ttl=1800, **codex)
                          )["readiness_cause"]))
    # Two causes are unreachable from `classify` BY DESIGN, and each is proved
    # by its own named check instead of here:
    #   `internal_error`            — for conditions the classifier failed to
    #                                 name, so it cannot be one of its outputs
    #                                 (§1 `unknown_causes_collapse...`, and
    #                                 `public_projection_cannot_fail_open`).
    #   `legacy_forecast_unmigrated` — only reachable through `legacy_readiness`
    #                                 for rows persisted before the classifier
    #                                 existed (`legacy_rows_migrate...`).
    #   `turn_in_flight`             — minted at poll time by
    #                                 `cache_forecast_public` for a node whose
    #                                 turn is running with an unmoved prefix
    #                                 (`in_flight_row`, D-235); never persisted
    #                                 and never a `classify` output, proved by
    #                                 `mid_turn_projection_compares...` below.
    # ⚠ THIS EXEMPTION LIST IS THE ONLY ONE. Anything else added to READINESS
    # must be reachable from a real `classify` call or this check fails, which
    # is exactly the "a new branch forgot its verdict" defect it exists to
    # catch — it already caught `legacy_forecast_unmigrated` itself.
    off_classifier = {"internal_error", "legacy_forecast_unmigrated",
                      "turn_in_flight"}
    eq(seen, set(C.READINESS) - off_classifier)
    for cause in off_classifier:
        assert cause in C.READINESS_DETAIL, cause


def public_projection_cannot_fail_open() -> None:
    # An unrecognised state is an incident, not a silent coercion.
    bogus = C.public({"state": "vibes", "lane": "subscription",
                      "readiness": "ready", "readiness_cause": "receipt_valid"},
                     generation="g", precompact_action="not_applicable",
                     precompact_reason="")
    eq(bogus["readiness_cause"], "internal_error")
    assert "vibes" in bogus["readiness_detail"]

    # A well-formed row passes through unchanged, evidence and all.
    good = C.public(classify(snapshot(), book(receipt_at=NOW - 60)),
                    generation="g", precompact_action="not_applicable",
                    precompact_reason="")
    eq(good["readiness"], "ready")
    eq(good["readiness_cause"], "receipt_valid")


def green_requires_a_positive_receipt_every_time() -> None:
    """No input that lacks a positive receipt may ever come back green.

    Stated as a negative sweep because that is the direction the invariant
    actually cares about: greens are a promise, and a promise made from
    missing evidence is the failure this whole change exists to prevent.
    """
    without_receipt = [
        ({}, snapshot()),
        (book(), snapshot()),
        (book(), snapshot(last_turn_history_relation="unobserved")),
        (book(), snapshot(receipt_history_relation="unobserved")),
    ]
    for prior_book, cur in without_receipt:
        row = classify(cur, prior_book)
        assert row["readiness"] != "ready", row["readiness_cause"]


def legacy_rows_migrate_instead_of_reporting_a_defect() -> None:
    """A pre-D-226 row is a MIGRATION, and must not be labelled a defect.

    ⚠ REGRESSION GUARD FOR A REAL DEFECT (found by readiness-postreview before
    this landed). Every forecast persisted before D-226 lacks the readiness
    triple. The first implementation sent all of them through the
    unknown-cause door, so the first poll after deploy would have rendered
    EVERY idle node grey as `internal_error` and logged an UNCLASSIFIED
    incident each time, forever, until that node next completed a turn.
    """
    cases = {
        ("compatible_observed", "authoritative_receipt", "subscription"):
            ("ready", "receipt_valid"),
        ("compatible_observed", "codex_subscription_fixed_estimate",
         "subscription"): ("ready", "receipt_valid_codex_estimate"),
        ("expired_known_entry", "authoritative_receipt", "subscription"):
            ("not_ready", "receipt_expired"),
        ("known_incompatible", "fingerprint_mismatch", "subscription"):
            ("not_ready", "prefix_changed"),
        ("uncertain", "no_completed_fingerprint", "subscription"):
            ("none", "no_completed_fingerprint"),
        ("uncertain", "no_positive_receipt", "subscription"):
            ("not_ready", "no_positive_receipt"),
        ("uncertain", "clock_skew", "subscription"):
            ("diagnostic", "clock_anomaly"),
        ("uncertain", "ttl_unobserved", "provider_unsupported"):
            ("diagnostic", "unsupported_capability"),
        ("uncertain", "ttl_unobserved", "unobserved"):
            ("not_ready", "lane_unobserved"),
    }
    for (state, source, lane), (want_r, want_c) in cases.items():
        row = C.public({"state": state, "source": source, "lane": lane},
                       generation="g", precompact_action="not_applicable",
                       precompact_reason="")
        eq((source, row["readiness"]), (source, want_r))
        eq((source, row["readiness_cause"]), (source, want_c))
        # NEVER the defect label: that would be a lie about what happened.
        assert row["readiness_cause"] != "internal_error", source

    # The genuinely ambiguous residue is RED and self-describing — never a
    # guessed grey, and never green.
    residue = C.public(
        {"state": "uncertain", "source": "ttl_unobserved",
         "lane": "subscription"},
        generation="g", precompact_action="not_applicable",
        precompact_reason="")
    eq(residue["readiness"], "not_ready")
    eq(residue["readiness_cause"], "legacy_forecast_unmigrated")
    assert "ttl_unobserved" in residue["readiness_detail"]

    # ...and a legacy row can never come back green without having been
    # persisted as an observed-compatible one.
    for state in ("uncertain", "known_incompatible", "expired_known_entry"):
        row = C.public({"state": state, "source": "anything-at-all",
                        "lane": "subscription"}, generation="g",
                       precompact_action="not_applicable", precompact_reason="")
        assert row["readiness"] != "ready", state


check("the emitted cause set is exactly the declared set (exhaustiveness)",
      every_reachable_cause_is_exercised)
def an_elapsed_legacy_row_is_never_green() -> None:
    """A persisted `compatible_observed` DECAYS; healing must not revive it.

    ⚠ REGRESSION GUARD FOR D-B7, found by readiness-postreview after the first
    landing. `cache_forecast_public` returns early when a node has a PUBLIC row
    but no internal forecast, and that return happens BEFORE the expiry flip
    the main path applies. The first fix healed such a row straight from its
    persisted state, so a row saved as `compatible_observed` whose entry had
    since died came back `ready` / `receipt_valid` — a backend triple claiming
    green for an expired receipt. The badge still rendered red, but only
    because the frontend's own expiry lock overrode it; every other consumer of
    the row saw green. The invariant has to hold in the DATA, not in one
    renderer, which is why this asserts on the returned row and not on markup.

    It also covers B11: the mutant "early-return healing removed" survived all
    three backend suites, because nothing exercised that branch end to end.
    """
    org = store.create_org("zz-cache-readiness-legacy")
    org.hire(USER, None, "haiku", 4, "legacy")
    nid = "legacy"
    live = C.iso_us(NOW + 1800)
    dead = C.iso_us(NOW - 600)

    def poll(expires_at: str) -> dict[str, Any]:
        org.node(nid)["cache_continuity"] = {          # PUBLIC ONLY, no forecast
            "public": {"generation": "g", "state": "compatible_observed",
                       "source": "authoritative_receipt", "lane": "subscription",
                       "ttl_seconds": 3600, "expires_at": expires_at,
                       "last_receipt_at": C.iso_us(NOW - 1800),
                       "precompact_action": "not_applicable",
                       "precompact_reason": "", "changed_inputs": []}}
        store.save_org(org)
        row = S.cache_forecast_public(org, nid, now=NOW)
        assert isinstance(row, dict)
        return row

    # Still inside its window: green is correct and must survive healing.
    ok = poll(live)
    eq(ok["readiness"], "ready")
    eq(ok["readiness_cause"], "receipt_valid")

    # Elapsed: green would be a lie, and the row must say so itself.
    gone = poll(dead)
    eq(gone["readiness"], "not_ready")
    eq(gone["readiness_cause"], "receipt_expired")
    # ...and `state` is demoted too, so one fact does not wear two labels
    # depending on which branch happened to observe it.
    eq(gone["state"], "expired_known_entry")

    # B11: the branch is genuinely reached — a row with NO triple at all comes
    # back carrying one, so deleting the healing cannot pass silently.
    assert gone.get("readiness_cause"), "early-return healing did not run"


check("an elapsed legacy public-only row is never healed to green",
      an_elapsed_legacy_row_is_never_green)
check("pre-D-226 rows migrate honestly instead of reporting a false defect",
      legacy_rows_migrate_instead_of_reporting_a_defect)
check("the public projection fails closed, never open",
      public_projection_cannot_fail_open)
check("green is impossible without a positive receipt",
      green_requires_a_positive_receipt_every_time)


# ── §6 mid-turn: the baseline is the request in flight ────────────────────

def mid_turn_projection_compares_against_the_request_in_flight() -> None:
    """Mid-turn the projection compares against the request IN FLIGHT.

    ⚠ REGRESSION GUARD FOR A REAL DEFECT (cache-card, 2026-09-03, D-235). The
    poll preview compared the prefix-now against `book['last_turn']` — the
    turn BEFORE the running one — while the request actually running was a
    local in the run loop that nothing persisted. A turn that was itself the
    cold one (the user retooled while idle, then sent) therefore stayed red
    (`prefix_changed`) for its whole duration although nothing had moved since
    it launched, and the turn after it would find the entry it was writing.
    The card lied about the present. The request in flight now rides the
    `inflight` marker, which every turn exit already clears, and the mid-turn
    projection is taken against it.
    """
    org = store.create_org("zz-cache-readiness-inflight")
    org.hire(USER, None, "haiku", 4, "agent")
    nid = "agent"
    n = org.node(nid)
    ident = {"session": str(n.get("session_id") or ""),
             "node_generation": int(n.get("generation") or 0)}
    # The last COMPLETED turn ran with the OLD system prompt; live receipt.
    prior = book(receipt_at=NOW - 60, **ident)
    for row in (prior["last_turn"], prior["receipt"]):
        row["components"] = {**row["components"], "system": "old-system"}
    # The RUNNING turn was launched with the new prompt: cold against that
    # baseline — its admission forecast said so, and the doc still says so.
    launched = snapshot(**ident)
    admission = classify(launched, prior)
    eq(admission["readiness_cause"], "prefix_changed")
    attempt = {k: v for k, v in launched.items()
               if not k.endswith("_history_relation")}
    prior.update({"version": 1, "seq": 1, "forecast": admission,
                  "public": C.public(admission, generation="g0",
                                     precompact_action="not_applicable",
                                     precompact_reason="")})
    n["cache_continuity"] = prior
    rendered = copy.deepcopy(attempt)            # what a poll renders NOW
    old_snapshot = S._cache_snapshot
    S._cache_snapshot = lambda *a, **k: copy.deepcopy(rendered)
    try:
        def poll(now: float = NOW) -> dict[str, Any]:
            row = S.cache_forecast_public(org, nid, now=now)
            assert isinstance(row, dict)
            return row

        # IDLE — no marker: the durable answer stands. The NEXT request really
        # is cold against the last completed turn.
        eq(poll()["readiness_cause"], "prefix_changed")

        # MID-TURN, prefix unmoved since launch: nothing to claim. This is the
        # defect — it used to stay red for the whole turn.
        n["inflight"] = {"at": C.iso(NOW), "text": "go",
                         "cache_attempt": copy.deepcopy(attempt)}
        mid = poll()
        eq(mid["readiness"], "none")
        eq(mid["readiness_cause"], "turn_in_flight")
        eq(mid["changed_inputs"], [])
        # A send mid-turn steers; no pre-turn policy applies.
        eq(mid["precompact_action"], "not_applicable")
        assert "steers" in mid["precompact_reason"], mid["precompact_reason"]
        # ...and the row is stamped at launch, not per poll: no generation churn.
        eq(poll(NOW + 30)["generation"], mid["generation"])

        # The prefix MOVES mid-turn (a retool): red, naming the component —
        # settled whatever the running turn does — and a send still steers.
        rendered["components"] = {**rendered["components"], "tools": "retooled"}
        moved = poll()
        eq(moved["readiness_cause"], "prefix_changed")
        eq(moved["changed_inputs"], ["tools"])
        eq(moved["precompact_action"], "not_applicable")
        rendered["components"] = dict(attempt["components"])

        # A marker minted by another generation or session is NOT a baseline:
        # the projection falls back to the durable (idle) answer.
        n["inflight"]["cache_attempt"]["node_generation"] = ident["node_generation"] + 1
        eq(poll()["readiness_cause"], "prefix_changed")
        n["inflight"]["cache_attempt"]["node_generation"] = ident["node_generation"]
        n["inflight"]["cache_attempt"]["session"] = "somebody-else"
        eq(poll()["readiness_cause"], "prefix_changed")
        n["inflight"]["cache_attempt"]["session"] = ident["session"]

        # A persisted diagnostic is not a card mid-turn either (user, 10:34Z):
        # "cannot tell" is the mid-turn default. It returns once the turn ends.
        skewed_book = book(receipt_at=NOW + 300, **ident)
        skewed = classify(launched, skewed_book)
        eq(skewed["readiness_cause"], "clock_anomaly")
        n["cache_continuity"] = {
            **skewed_book, "version": 1, "seq": 1, "forecast": skewed,
            "public": C.public(skewed, generation="g1",
                               precompact_action="not_applicable",
                               precompact_reason="")}
        eq(poll()["readiness_cause"], "turn_in_flight")
        n.pop("inflight")
        eq(poll()["readiness_cause"], "clock_anomaly")

        # Every turn exit pops the marker; with it gone the projection is idle.
        n["cache_continuity"] = prior
        n["inflight"] = {"at": C.iso(NOW), "text": "go",
                         "cache_attempt": copy.deepcopy(attempt)}
        eq(poll()["readiness_cause"], "turn_in_flight")
        n.pop("inflight")
        eq(poll()["readiness_cause"], "prefix_changed")
    finally:
        S._cache_snapshot = old_snapshot


check("mid-turn the projection compares against the request in flight, not the last turn",
      mid_turn_projection_compares_against_the_request_in_flight)


print()
if FAIL:
    print(f"{FAIL} FAILED, {PASS} PASSED")
    sys.exit(1)
print(f"ALL {PASS} CHECKS PASS")
