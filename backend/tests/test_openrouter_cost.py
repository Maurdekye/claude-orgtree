"""OpenRouter numeric cost compatibility plus explicit provenance.

    python backend/tests/test_openrouter_cost.py
"""

import math
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-orr-cost-")
os.environ["ORGTREE_DATA"] = DATA
os.environ.pop("ORGTREE_WARM", None)

from orgtree import openrouter as orr, store, supervisor as S  # noqa: E402
from orgtree.ledger import USER                                 # noqa: E402

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def favorite(model="openai/gpt-5.6-sol", tier="or-openai-gpt-5-6-sol",
             *, prompt=5.0, completion=30.0, cache_read=0.5,
             cache_write=5.0, unknown=()):
    return {
        "id": model, "name": model, "tier": tier, "vendor": "probe",
        "prompt": prompt, "completion": completion,
        "cache_read": cache_read, "cache_write": cache_write,
        "price_unknown": list(unknown), "price_source": "openrouter-catalog",
        "seat": max(prompt, 0.1), "context": 100_000, "tools": True,
        "created": 0, "free": False, "letter": "P", "color": "#888888",
        "accent": None, "added_at": "2026-09-03T00:00:00Z",
    }


def set_favorite(row):
    doc = orr._blank_state()
    doc["favorites"] = [row]
    orr._save_state(doc)


def setup(name, row):
    set_favorite(row)
    org = store.create_org("zz-or-cost-" + name)
    org.d["tiers"][row["tier"]] = row["seat"]
    org.d["models"][row["tier"]] = row["id"]
    org.hire(USER, None, row["tier"], 0, "agent")
    store.save_org(org)
    return org


def result(model, *, usage=None, top_cost=None, cli_total=0.99,
           cost_basis="unknown"):
    out = {"status": "success", "duration_ms": 1,
           "total_cost_usd": cli_total,
           "modelUsage": {model: {"costBasis": cost_basis}}}
    if usage is not None:
        out["usage"] = usage
    if top_cost is not None:
        out["cost"] = top_cost
    return out


def run(org, res):
    S._after_turn(org.d["slug"], "agent", org, res,
                  {"interrupted": False}, occ=None)
    after = store.load_org(org.d["slug"])
    return after, after.node("agent")["turns"][-1]


def main():
    print("§1 component price knowledge and compatibility arithmetic")

    def cache_write_controls():
        zero = favorite(prompt=2.0, cache_write=0.0)
        positive = favorite(prompt=2.0, cache_write=3.0)
        absent = favorite(prompt=2.0)
        absent.pop("cache_write")
        absent["price_unknown"] = ["cache_write"]
        values = []
        for row in (zero, positive, absent):
            set_favorite(row)
            values.append(orr.cost_detail(row["id"], 0, 0, 0, 10_000))
        assert [v["amount"] for v in values] == [0.0, 0.03, 0.02], values
        assert values[0]["unknown_fields"] == [], values[0]
        assert values[1]["unknown_fields"] == [], values[1]
        assert values[2]["unknown_fields"] == ["cache_write"], values[2]
        assert orr.cost(absent["id"], 0, 0, 0, 10_000) == 0.02
    check("explicit zero/positive cache-write prices beat absent-only prompt fallback",
          cache_write_controls)

    def used_only_unknown():
        row = favorite(cache_read=0.0, unknown=("cache_read",))
        set_favorite(row)
        cached = orr.cost_detail(row["id"], 0, 10_000, 0)
        uncached = orr.cost_detail(row["id"], 10_000, 0, 0)
        assert cached == {"amount": 0.0, "source": "catalog-snapshot",
                          "unknown_fields": ["cache_read"]}, cached
        assert uncached == {"amount": 0.05, "source": "catalog-snapshot",
                            "unknown_fields": []}, uncached
        missing = orr.cost_detail("not/in-catalog", 100, 0, 200)
        assert missing["source"] == "unpriced" and missing["unknown_fields"] == [
            "prompt", "completion"], missing
    check("only used unknown components make an estimate incomplete", used_only_unknown)

    print("§2 provider-cost precedence and validity")
    row = favorite()
    org = setup("precedence", row)
    base_usage = {"input_tokens": 1000, "output_tokens": 500}

    def native_positive():
        after, turn = run(org, result(row["id"], usage={**base_usage,
                                                         "cost": 0.004215}))
        assert turn["cost"] == 0.004215 and turn["cost_complete"] is True, turn
        assert turn["cost_source"] == "provider-usage", turn
        assert turn["cost_unknown_fields"] == [] and not turn.get("estimated"), turn
        assert after.node("agent")["cost_usd"] == 0.004215
    check("valid usage.cost is authoritative and records provider provenance",
          native_positive)

    def native_zero_wins():
        # A valid billed total is authoritative even when token counters cannot
        # be used for the lower-precedence catalog estimate.
        _after, turn = run(org, result(
            row["id"], usage={"input_tokens": math.inf,
                              "output_tokens": None, "cost": 0.0},
            top_cost=0.006))
        assert turn["cost"] == 0.0 and turn["cost_complete"] is True, turn
        assert turn["cost_source"] == "provider-usage", turn
        assert turn["cost_unknown_fields"] == [], turn
    check("provider usage zero is known zero and does not fall through",
          native_zero_wins)

    def malformed_usage_falls_to_result():
        for bad in (True, "oops", -1.0, math.nan, math.inf):
            _after, turn = run(org, result(
                row["id"], usage={**base_usage, "cost": bad}, top_cost=0.006))
            assert turn["cost"] == 0.006 and turn["cost_complete"] is True, (bad, turn)
            assert turn["cost_source"] == "provider-result", (bad, turn)
    check("invalid usage.cost cannot suppress a valid result.cost",
          malformed_usage_falls_to_result)

    def malformed_cli_is_lower_precedence():
        _after, native = run(org, result(
            row["id"], usage={"input_tokens": 0, "output_tokens": 0,
                              "cost": 0.006}, cli_total="malformed"))
        assert (native["cost"], native["cost_source"],
                native["cost_complete"]) == (
                    0.006, "provider-usage", True), native
        _after, catalog = run(org, result(
            row["id"], usage={"input_tokens": 1000, "output_tokens": 0},
            cli_total="malformed"))
        assert (catalog["cost"], catalog["cost_source"],
                catalog["cost_complete"]) == (
                    0.005, "catalog-snapshot", False), catalog
    check("malformed lower-priority CLI total cannot suppress native or catalog cost",
          malformed_cli_is_lower_precedence)

    def estimated_precedence():
        _a, cli = run(org, result(row["id"], usage=base_usage,
                                  cli_total=0.123, cost_basis="list"))
        assert (cli["cost"], cli["cost_source"], cli["cost_complete"]) == (
            0.123, "claude-cli-list", False), cli
        _a, cat = run(org, result(row["id"], usage={
            "input_tokens": 10_000, "output_tokens": 1_000}))
        assert (cat["cost"], cat["cost_source"], cat["cost_complete"]) == (
            0.08, "catalog-snapshot", False), cat
        assert cat["estimated"] is True and cat["cost_unknown_fields"] == [], cat
    check("CLI-list then catalog estimate precedence remains explicit",
          estimated_precedence)

    def counter_validity():
        controls = (("positive", 1000, 0.005, []),
                    ("zero", 0, 0.0, []))
        invalid = (("null", None), ("string", "unknown"),
                   ("infinity", math.inf), ("nan", math.nan),
                   ("negative", -1), ("fractional", 1.5), ("bool", True))
        for label, value, expected, fields in controls:
            _after, turn = run(org, result(row["id"], usage={
                "input_tokens": value, "output_tokens": 0}, cli_total=0))
            assert turn["cost"] == expected, (label, turn)
            assert turn["cost_unknown_fields"] == fields, (label, turn)
        for label, value in invalid:
            _after, turn = run(org, result(row["id"], usage={
                "input_tokens": value, "output_tokens": 0}, cli_total=0))
            assert turn["cost"] == 0.0, (label, turn)
            assert turn["cost_unknown_fields"] == ["usage"], (label, turn)
        # Cache counters keep their established absent=>zero default, but an
        # explicitly unusable value cannot silently establish known zero.
        _after, absent = run(org, result(row["id"], usage={
            "input_tokens": 0, "output_tokens": 0}, cli_total=0))
        _after, bad_cache = run(org, result(row["id"], usage={
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": "unknown"}, cli_total=0))
        assert absent["cost_unknown_fields"] == [], absent
        assert bad_cache["cost_unknown_fields"] == ["usage"], bad_cache
    check("token counters distinguish valid zero from unavailable values",
          counter_validity)

    def other_provider_compatibility():
        plain = store.create_org("zz-or-cost-non-or")
        plain.hire(USER, None, "haiku", 0, "agent")
        store.save_org(plain)
        S._after_turn(plain.d["slug"], "agent", plain, {
            "status": "success", "duration_ms": 1,
            "total_cost_usd": "0.75", "usage": {}},
            {"interrupted": False}, occ=None)
        turn = store.load_org(plain.d["slug"]).node("agent")["turns"][-1]
        assert turn["cost"] == 0.75, turn
    check("non-OpenRouter providers retain established base-cost parsing",
          other_provider_compatibility)

    print("§3 persisted unknown flags, compatibility totals and lifecycle")

    def persisted_unknown():
        unknown_row = favorite(model="probe/missing-cache", tier="or-probe-missing-cache",
                               cache_read=0.0, unknown=("cache_read",))
        unknown_org = setup("unknown", unknown_row)
        after, turn = run(unknown_org, result(unknown_row["id"], usage={
            "input_tokens": 0, "cache_read_input_tokens": 10_000,
            "output_tokens": 0}))
        assert turn["cost"] == 0.0 and turn["cost_unknown_fields"] == ["cache_read"], turn
        assert after.node("agent")["cost_usd_unknown"] is True
        tree = after.tree()
        assert tree["cost_usd_total"] == 0.0 and tree["cost_usd_unknown"] is True, tree
        assert tree["roots"][0]["cost_usd"] == 0.0
        assert tree["roots"][0]["cost_usd_unknown"] is True
        return after
    holder = []
    check("unknown zero survives reload/tree while numeric API totals remain zero",
          lambda: holder.append(persisted_unknown()))

    def missing_usage_is_named():
        miss = setup("missing-usage", favorite(
            model="probe/missing-usage", tier="or-probe-missing-usage"))
        _after, turn = run(miss, result("probe/missing-usage", usage=None,
                                       cli_total=0.0))
        assert turn["cost_unknown_fields"] == ["usage"], turn
    check("missing usage is explicit rather than silently complete zero",
          missing_usage_is_named)

    def lineage_does_not_duplicate():
        after = holder[0]
        pred = after.compact_split("agent", "replacement-session")
        assert after.node("agent")["cost_usd_unknown"] is True
        assert not after.node(pred).get("cost_usd_unknown"), after.node(pred)
        assert after.tree()["cost_usd_unknown"] is True
    check("lineage predecessor clears aggregate unknown while successor retains it",
          lineage_does_not_duplicate)

    def deletion_banks_unknown():
        doomed = setup("delete", favorite(
            model="probe/delete", tier="or-probe-delete", cache_read=0.0,
            unknown=("cache_read",)))
        doomed, _turn = run(doomed, result("probe/delete", usage={
            "input_tokens": 0, "cache_read_input_tokens": 1,
            "output_tokens": 0}))
        doomed.delete(USER, "agent")
        assert doomed.d["deleted_cost_usd_unknown"] is True
        assert doomed.tree()["cost_usd_unknown"] is True
        assert doomed.cost_total() == 0.0
    check("permanent deletion banks unresolved provenance without changing dollars",
          deletion_banks_unknown)

    def legacy_absence_stays_absent():
        old = store.create_org("zz-or-cost-legacy")
        old.d["tiers"][row["tier"]] = row["seat"]
        old.d["models"][row["tier"]] = row["id"]
        old.hire(USER, None, row["tier"], 0, "agent")
        old.node("agent")["cost_usd"] = 1.25
        store.save_org(old)
        again = store.load_org(old.d["slug"])
        assert again.cost_total() == 1.25
        assert again.tree()["cost_usd_unknown"] is False
        assert "cost_usd_unknown" not in again.node("agent")
    check("historical numeric rows remain numeric and are not retroactively unknown",
          legacy_absence_stays_absent)

    print(f"\nALL {PASS} CHECKS PASS — OpenRouter cost provenance")


if __name__ == "__main__":
    main()
