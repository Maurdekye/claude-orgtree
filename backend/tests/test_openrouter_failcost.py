"""What a FAILED OpenRouter turn is charged, and where that number came from.

    python backend/tests/test_openrouter_failcost.py   (no pytest)

THE DEFECT. `_after_turn` books the successful path with a source, a
completeness flag and the fields it could not price. The FAILURE path —
`_charge_reported_spend`, the sibling that runs when a turn answered, billed,
and then came apart — booked the CLI's own `total_cost_usd` with none of that.
On this lane that figure is measured WRONG by an order of magnitude for a
non-Anthropic vendor (a $0.004 gpt-5.6-luna tool turn booked as $0.134), so a
guess was landing in the node and org lifetime totals in the same shape as a
figure a provider had reported.

THE LADDER, and what each rung knows (`_failure_cost_source`):
  1. NATIVE, complete — a cost the provider itself reported. A valid ZERO is
     the amount, not a miss. ⚠ HYPOTHETICAL: the pinned CLI's usage schema has
     eleven fields and none is a cost (read out of the 2.1.258 binary,
     evidence/c4-native-cost-groundwork.md), so this rung never fires on this
     build. The checks in §2 drive it through the fixture only, and say so.
  2. CATALOGUE, incomplete — snapshot prices over the tokens the turn is known
     to have used. `usage` is ALWAYS unresolved here: a failed turn has no
     result event, so the receipt is partial (the last top-level message's
     counts plus cumulative output) and UNDER-counts a multi-message turn.
  3. THE CLI'S FIGURE, incomplete — booked, never presented as a measurement.

⚠ NO `costBasis` RUNG, and §5 says why in a check: `costBasis` describes the
most recent request for ONE model and `list` names Claude Code's own built-in
list prices, so a single matched row cannot promote a whole turn to complete
(coordinator ruling 2026-09-05). The SUCCESS path's existing use of it is out
of this unit's scope; §5 pins today's behaviour under a name that says it is a
known limit rather than an endorsement.

    §1  the ladder (pure)
    §2  a failed turn on the OpenRouter lane books with provenance
    §3  the doubt reaches the node: cost_usd_unknown
    §4  scope: another lane's failed turn is byte-for-byte unchanged
    §5  the costBasis limit, stated as a limit

Anti-vacuity: `tests/_mutate_or_failcost.py` breaks the shipped code eleven
ways and requires a NAMED check here to go red for each.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import traceback
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ⚠ the harness sets ORGTREE_DATA/HOME AT IMPORT, before `orgtree` is imported.
import test_limit_freeze as H                                    # noqa: E402

from orgtree import openrouter, store, supervisor                # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

_LIVE_ROOT = os.path.normcase(os.path.abspath(
    os.path.join(os.path.expanduser("~"), "orgtree")))
assert os.path.normcase(os.path.abspath(store.DATA_ROOT)) != _LIVE_ROOT, \
    f"store.DATA_ROOT resolved to the LIVE root: {store.DATA_ROOT}"
assert os.path.normcase(os.path.abspath(store.DATA_ROOT)).startswith(
    os.path.normcase(os.path.abspath(H._TMP))), store.DATA_ROOT

PASS = 0
FAIL: list[tuple[str, str]] = []
VERBOSE = "-v" in sys.argv
ONLY = [a for a in sys.argv[1:] if not a.startswith("-")]

OR = "or-failcost-fake"
OR_MODEL = "failcost/fake"
#: a turn's worth of tokens, priced by the snapshot below
USAGE = {"input_tokens": 1000, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0, "output_tokens": 200}


def check(label: str, fn) -> None:
    global PASS
    if ONLY and not any(o in label for o in ONLY):
        return
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        if VERBOSE:
            traceback.print_exc()
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def team(tier: str = OR) -> tuple[str, str]:
    """org + one node on `tier`, with a PRICED favourite so the catalogue rung
    has real numbers rather than an unpriced zero."""
    org = store.create_org(f"zz orfail {time.time_ns()}")
    tl = {"bash": False, "web": False, "edit": False, "subagents": False,
          "mcp": []}
    boss = org.hire(USER, None, "haiku", 60, "boss", add_dirs=[], tools=tl,
                    org_visibility="team", charter="b")["node"]
    nid = org.hire(boss, boss, "haiku", 5, "rep", add_dirs=[], tools=tl,
                   org_visibility="team", charter="r")["node"]
    n = org.node(nid)
    n["model"] = tier
    if openrouter.is_tier(tier):
        org.d.setdefault("tiers", {})[tier] = 1
        org.d.setdefault("models", {})[tier] = OR_MODEL
    store.save_org(org)
    return org.d["slug"], nid


def price_the_model() -> None:
    """One favourite at known snapshot prices: $1/M prompt, $2/M completion,
    so the catalogue rung has real numbers instead of an unpriced zero."""
    doc = openrouter._blank_state()
    doc["favorites"] = [{
        "id": OR_MODEL, "name": OR_MODEL, "tier": OR, "vendor": "probe",
        "prompt": 1.0, "completion": 2.0, "cache_read": 0.0,
        "cache_write": 0.0, "price_unknown": [],
        "price_source": "openrouter-catalog", "seat": 1.0,
        "context": 100_000, "tools": True, "created": 0, "free": False,
        "letter": "F", "color": "#888888", "accent": None,
        "added_at": "2026-09-05T00:00:00Z",
    }]
    openrouter._save_state(doc)


def unprice_the_model() -> None:
    """No favourite and no cached card: the catalogue rung answers `unpriced`,
    which is how the CLI-figure rung is reached at all."""
    openrouter._save_state(openrouter._blank_state())


def node(slug: str, nid: str) -> dict[str, Any]:
    return store.load_org(slug).nodes[nid]


def last_turn(slug: str, nid: str) -> dict[str, Any]:
    turns = node(slug, nid).get("turns") or []
    if not turns:
        raise AssertionError("nothing was booked at all")
    return dict(turns[-1])


# ══════════════════════════════════════════════════════════════════════ §1

def sec_ladder() -> None:
    print("\n§1  the ladder (pure)")
    price_the_model()
    slug, nid = team()
    org = store.load_org(slug)
    S = supervisor._failure_cost_source

    def _native_first():
        amount, source, complete, unknown = S(
            org, nid, 9.99, native=0.5, usage=USAGE, out_tokens=200)
        assert amount == 0.5, (
            f"the CLI's figure outranked a provider-reported cost: {amount}")
        assert source == "provider-native-unscoped", source
    check("ladder · a provider-reported cost outranks the CLI's figure and the catalogue",
          _native_first)

    def _native_is_not_complete():
        # ⚠ A FIELD'S EXISTENCE IS NOT A SCOPE CONTRACT. Nobody has observed a
        # native cost on this lane, so whether it would be per-request (taking
        # the largest UNDER-counts a multi-message turn) or process-cumulative
        # (taking it raw OVER-counts a warm continuation, with no baseline
        # subtracted) is unknown. Calling it complete would invent the answer.
        _, _, complete, unknown = S(
            org, nid, 9.99, native=0.5, usage=USAGE, out_tokens=200)
        assert complete is False, (
            "an unobserved provider field was believed as a TURN TOTAL")
        assert unknown == ["scope"], (
            f"the thing that is unknown about it is not named: {unknown}")
    check("ladder · …but it is INCOMPLETE with `scope` unresolved — no invented turn contract",
          _native_is_not_complete)

    def _native_zero_is_an_amount():
        amount, source, _, _ = S(
            org, nid, 9.99, native=0.0, usage=USAGE, out_tokens=200)
        assert (amount, source) == (0.0, "provider-native-unscoped"), (
            "a provider that reported ZERO was treated as having reported "
            f"nothing, and the CLI's guess was booked instead: {amount}")
    check("ladder · a native ZERO is the amount, not a miss (free models are the ordinary case)",
          _native_zero_is_an_amount)

    def _catalogue_second():
        amount, source, complete, unknown = S(
            org, nid, 9.99, native=None, usage=USAGE, out_tokens=200)
        # 1000 prompt @ $1/M + 200 completion @ $2/M
        assert abs(amount - (1000 * 1e-6 + 200 * 2e-6)) < 1e-9, amount
        assert source not in ("claude-cli", "provider-native"), source
        assert complete is False, "a catalogue estimate presented as complete"
        assert "usage" in unknown, (
            "a PARTIAL failure-path receipt was not marked partial: a failed "
            f"turn has no result event to count from — {unknown}")
    check("ladder · with no native cost the catalogue prices the turn, INCOMPLETE, usage unresolved",
          _catalogue_second)

    def _cli_last():
        unpriced_slug, unpriced_nid = team()
        unprice_the_model()
        try:
            amount, source, complete, unknown = S(
                store.load_org(unpriced_slug), unpriced_nid, 9.99,
                native=None, usage=USAGE, out_tokens=200)
            assert (amount, source, complete) == (9.99, "claude-cli", False), (
                amount, source, complete)
            assert unknown == ["prompt", "completion"], unknown
        finally:
            price_the_model()
    check("ladder · with no price either, the CLI's figure books — but never as complete",
          _cli_last)

    def _nothing_established():
        amount, source, complete, _ = S(
            org, nid, 0.0, native=None, usage={}, out_tokens=0)
        assert (amount, source, complete) == (0.0, "", False), (
            "with no native cost, no usage and no CLI figure, something was "
            f"invented: {amount} {source}")
    check("ladder · no native cost, no usage, no figure → nothing is invented", _nothing_established)

    def _no_basis_rung():
        # a costBasis of any kind is not an input to this function at all —
        # the ladder cannot be talked into completeness by one model's row
        for basis in ("list", "managed", "unknown"):
            amount, source, complete, _ = S(
                org, nid, 9.99, native=None, usage=USAGE, out_tokens=200)
            assert complete is False, f"{basis}: {complete}"
            assert source != "provider-native", basis
    check("ladder · no costBasis rung: one model's price table cannot complete a turn",
          _no_basis_rung)


# ══════════════════════════════════════════════════════════════════════ §2

def sec_booked() -> None:
    print("\n§2  a failed turn books with its provenance")
    price_the_model()
    slug, nid = team()
    supervisor._charge_reported_spend(slug, nid, 9.99, False,
                                      native=None, usage=USAGE, out_tokens=200)

    def _catalogue_entry():
        e = last_turn(slug, nid)
        assert e.get("killed") is True, e
        assert abs(float(e["cost"]) - 0.0014) < 1e-9, (
            f"the CLI's $9.99 guess was booked instead of the priced turn: {e}")
        assert e.get("cost_complete") is False and e.get("estimated") is True, e
        assert "usage" in (e.get("cost_unknown_fields") or []), e
        assert node(slug, nid)["cost_usd"] == round(0.0014, 6), node(slug, nid)["cost_usd"]
    check("booked · the failed turn's entry carries the amount, the source and what is unresolved",
          _catalogue_entry)

    slug, nid = team()
    supervisor._charge_reported_spend(slug, nid, 9.99, False,
                                      native=0.25, usage=USAGE, out_tokens=200)

    def _native_entry():
        e = last_turn(slug, nid)
        assert e["cost"] == 0.25, e
        assert e.get("cost_source") == "provider-native-unscoped", e
        assert e.get("cost_complete") is False, (
            f"an unobserved provider field booked as a complete turn: {e}")
        assert e.get("cost_unknown_fields") == ["scope"], e
        assert node(slug, nid).get("cost_usd_unknown") is True, (
            "…and the doubt did not reach the node")
    check("booked · a provider-reported figure books its amount, and its unknown SCOPE with it",
          _native_entry)

    slug, nid = team()
    supervisor._charge_reported_spend(slug, nid, 0.0, False,
                                      native=0.0, usage={}, out_tokens=0)

    def _native_zero_books():
        e = last_turn(slug, nid)
        assert e["cost"] == 0.0, e
        assert e.get("cost_source") == "provider-native-unscoped", (
            f"a provider-reported ZERO left no record at all: {e}")
        assert node(slug, nid).get("cost_usd", 0) == 0
    check("booked · a native zero still books its entry — the desk shows a free turn ran",
          _native_zero_books)

    slug, nid = team()
    supervisor._charge_reported_spend(slug, nid, 0.0, False,
                                      native=None, usage={}, out_tokens=0)

    def _nothing_books_nothing():
        assert not (node(slug, nid).get("turns") or []), (
            "a turn with nothing established wrote a confident zero")
    check("booked · a zero with no source books NOTHING (absent, not a confident $0.00)",
          _nothing_books_nothing)


# ══════════════════════════════════════════════════════════════════════ §3

def sec_doubt() -> None:
    print("\n§3  the doubt reaches the node")
    price_the_model()
    slug, nid = team()
    supervisor._charge_reported_spend(slug, nid, 9.99, False,
                                      native=None, usage=USAGE, out_tokens=200)

    def _flag_raised():
        assert node(slug, nid).get("cost_usd_unknown") is True, (
            "an ESTIMATE was booked into the lifetime total with no mark on "
            "the node — the org total then reads as measured")
    check("doubt · an incomplete failure charge raises cost_usd_unknown on the node",
          _flag_raised)

    # ⚠ THE CASE WITH NO DOLLAR ROW AT ALL. A failed turn on an UNPRICED model
    # whose CLI figure is zero establishes no amount — but it is KNOWN to have
    # consumed tokens, and a node that already carries a lifetime total would
    # otherwise go on presenting that total as complete. Nothing may be
    # invented; the uncertainty must still be recorded.
    slug, nid = team()
    with store.DOC_LOCK:
        o = store.load_org(slug)
        o.node(nid)["cost_usd"] = 1.23
        store.save_org(o)
    unprice_the_model()
    try:
        supervisor._charge_reported_spend(slug, nid, 0.0, False, native=None,
                                          usage=USAGE, out_tokens=200)
    finally:
        price_the_model()

    def _unpriced_but_consumed():
        n = node(slug, nid)
        assert n.get("cost_usd_unknown") is True, (
            "a turn that consumed tokens on an UNPRICED model left the "
            "node's lifetime total reading as complete")
        assert n["cost_usd"] == 1.23, (
            f"a cost was invented for a turn nothing could price: {n['cost_usd']}")
        assert not (n.get("turns") or []), (
            f"a confident row was written with no amount behind it: {n.get('turns')}")
    check("doubt · consumed but UNPRICED: the doubt is persisted, no dollar is invented, no row is written",
          _unpriced_but_consumed)

    slug, nid = team()
    supervisor._charge_reported_spend(slug, nid, 0.0, False, native=None,
                                      usage={}, out_tokens=0)

    def _nothing_observed_no_flag():
        n = node(slug, nid)
        assert "cost_usd_unknown" not in n, (
            "a turn with NO observed consumption marked the node uncertain — "
            "a flag nothing ever clears is a decoration, not a measure")
        assert not (n.get("turns") or []), n.get("turns")
    check("doubt · …and a turn with nothing observed marks nothing (the negative that keeps it honest)",
          _nothing_observed_no_flag)


# ══════════════════════════════════════════════════════════════════════ §4

def sec_scope() -> None:
    print("\n§4  scope — another lane's failed turn is unchanged")
    slug, nid = team("haiku")
    supervisor._charge_reported_spend(slug, nid, 1.25, False,
                                      native=None, usage=USAGE, out_tokens=200)

    def _claude_untouched():
        e = last_turn(slug, nid)
        assert e["cost"] == 1.25, (
            f"the OpenRouter ladder repriced a CLAUDE turn: {e}")
        assert "cost_source" not in e and "cost_complete" not in e, e
        assert "cost_usd_unknown" not in node(slug, nid), e
    check("scope · a haiku failure books the CLI figure with no stamps, exactly as before",
          _claude_untouched)

    slug, nid = team("haiku")
    supervisor._charge_reported_spend(slug, nid, 0.0, False,
                                      native=0.0, usage={}, out_tokens=0)

    def _claude_zero_unchanged():
        n = node(slug, nid)
        assert not (n.get("turns") or []), (
            "the native-zero rule leaked onto a lane that never asked for it")
        assert "cost_usd_unknown" not in n, (
            "the unpriced-but-consumed flag leaked onto another lane")
    check("scope · …and a zero on that lane still books nothing", _claude_zero_unchanged)


# ══════════════════════════════════════════════════════════════════════ §5

def sec_basis_limit() -> None:
    """⚠ THESE PIN TODAY'S BEHAVIOUR AS A KNOWN LIMIT, NOT AS AN ENDORSEMENT.

    The SUCCESS path keeps the CLI's own total when the matched `modelUsage`
    row says `costBasis: "list"`. That is out of this unit's scope and is left
    alone — but `list` names CLAUDE CODE'S OWN built-in list prices and
    `costBasis` describes THE MOST RECENT REQUEST for that one model, so it
    cannot establish that the turn's total equals the gateway invoice. These
    checks drive the real path and record exactly how far it goes, so a later
    change to it has to be deliberate."""
    print("\n§5  the costBasis limit, stated as a limit")
    price_the_model()

    def _book(model_usage: dict[str, Any]) -> tuple[dict[str, Any],
                                                    dict[str, Any]]:
        slug, nid = team()
        org = store.load_org(slug)
        res: dict[str, Any] = {
            "total_cost_usd": 9.99, "usage": dict(USAGE),
            "modelUsage": model_usage, "duration_ms": 10, "result": "ok"}
        supervisor._after_turn(slug, nid, org, res, {}, 0)
        return last_turn(slug, nid), node(slug, nid)

    listed, listed_node = _book({OR_MODEL: {"costUSD": 9.99,
                                            "costBasis": "list"}})

    def _list_row_is_not_complete():
        assert listed.get("cost_source") == "claude-cli-list", listed
        assert listed.get("cost_complete") is False, (
            "one model's most recent price table promoted a whole turn to "
            f"COMPLETE: {listed}")
        assert abs(float(listed["cost"]) - 9.99) < 1e-9, listed
    check("basis · a list-priced row keeps the CLI total but is NOT complete", _list_row_is_not_complete)

    def _the_limit_itself():
        # THE LIMIT ITSELF: the entry names no unresolved field, so the NODE's
        # doubt flag is not raised and this figure joins the node and org
        # lifetime totals as though it were measured. That is what "not proof
        # the total equals the invoice" costs in practice. Recorded here — see
        # evidence/c4-native-cost-groundwork.md and the code beside the branch
        # — so it cannot change by accident in either direction.
        assert listed.get("cost_unknown_fields") == [], listed
        assert "cost_usd_unknown" not in listed_node, listed_node
    check("basis · …and the known limit: it raises no unresolved field (recorded, not endorsed)",
          _the_limit_itself)

    mixed, _ = _book({OR_MODEL: {"costUSD": 1.0, "costBasis": "list"},
                   "some/other-model": {"costUSD": 8.99,
                                        "costBasis": "unknown"}})

    def _mixed_rows():
        # a turn whose rows DISAGREE is read off the matched row alone. Today
        # that means the same `list` treatment even though another model was
        # priced at a basis the CLI itself calls a guess.
        assert mixed.get("cost_source") == "claude-cli-list", mixed
        assert mixed.get("cost_complete") is False, mixed
    check("basis · rows that DISAGREE are still read off the matched row alone (the same limit)",
          _mixed_rows)

    unknown_basis, _ = _book({OR_MODEL: {"costUSD": 9.99,
                                      "costBasis": "unknown"}})

    def _unknown_basis_reprices():
        assert unknown_basis.get("cost_source") != "claude-cli-list", unknown_basis
        assert unknown_basis.get("cost_complete") is False, unknown_basis
        assert abs(float(unknown_basis["cost"]) - 0.0014) < 1e-9, (
            "a basis the CLI itself calls a guess was booked at the CLI's "
            f"number instead of being repriced: {unknown_basis}")
    check("basis · a basis the CLI calls a guess is repriced from the catalogue",
          _unknown_basis_reprices)

    missing, _ = _book({"a-key-orgtree-never-asked-for": {"costUSD": 9.99,
                                                       "costBasis": "list"}})

    def _missing_key_reprices():
        assert missing.get("cost_source") != "claude-cli-list", missing
        assert missing.get("model_usage_key", {}).get("matched") is False, missing
        assert abs(float(missing["cost"]) - 0.0014) < 1e-9, missing
    check("basis · a key orgtree never asked for is a MISS: repriced, and the miss is on the record",
          _missing_key_reprices)


def main() -> None:
    openrouter.set_key("or-test-key-000000")
    try:
        sec_ladder()
        sec_booked()
        sec_doubt()
        sec_scope()
        sec_basis_limit()
    finally:
        openrouter.set_key("")
        unprice_the_model()
    for label, tb in FAIL:
        print(f"\n--- {label}\n{tb}")
    if FAIL:
        print(f"\n{len(FAIL)} of {PASS + len(FAIL)} checks FAILED")
    else:
        print(f"\nALL {PASS} CHECKS PASS")
    try:
        shutil.rmtree(H._TMP, ignore_errors=True)
    except OSError:
        pass
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
