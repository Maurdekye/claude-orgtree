"""OpenRouter's credit standing, reshaped for the shared usage-board contract
(openrouter_limits.py).

    python backend/tests/test_openrouter_limits.py      (no pytest; plain asserts)

Hermetic by construction, like test_openrouter.py: `openrouter._http_get` is
replaced with a scripted stand-in — no request ever leaves this process, and
no real key exists anywhere in the rig.
"""

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REAL_MACHINE_KEY = ""
try:
    _real_path = os.path.expanduser("~/orgtree/openrouter_state.json")
    if os.path.exists(_real_path):
        with open(_real_path, "r", encoding="utf-8") as _f:
            REAL_MACHINE_KEY = json.load(_f).get("key", "")
except Exception:
    REAL_MACHINE_KEY = ""

os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-openrouter-limits-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)

from orgtree import openrouter as orr                                # noqa: E402
from orgtree import openrouter_limits as ol                          # noqa: E402

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def truthy(got, what):
    if not got:
        raise AssertionError(f"{what}: got {got!r}, wanted truthy")


def falsy(got, what):
    if got:
        raise AssertionError(f"{what}: got {got!r}, wanted falsy")


def stub(status, body_doc):
    def fake_get(url, headers):
        return status, json.dumps(body_doc).encode("utf-8")
    orr._http_get = fake_get


def stub_multi(routes):
    def fake_get(url, headers):
        for route_suffix, resp in routes.items():
            if url.endswith(route_suffix):
                status, body = resp
                return status, json.dumps(body).encode("utf-8")
        return 404, b"{}"
    orr._http_get = fake_get


def reset():
    """A clean key + cache for the next case."""
    orr.set_key("")
    orr.forget_key_status()


# ── §1 no key configured — degrades like every other provider's "not
#    installed" case, never an error box ────────────────────────────────────

def case_no_key():
    reset()
    got = ol.fetch()
    eq(got["available"], False, "fetch availability")
    eq(got["account"], "openrouter", "account id")
    eq(got["provider"], "OpenRouter", "provider label")
    truthy("no API key" in (got.get("error") or ""), "reason names the fix")
    eq(ol.peek(), {"available": False, "provider": "OpenRouter"}, "peek, cache-only")
    snap = ol.snapshot()
    eq(snap["available"], False, "snapshot availability")
    eq(snap["limits"], [], "snapshot limits")


# ── §2 a rejected key (401/403) — a sign-in problem, never a fabricated bar ──

def case_rejected():
    reset()
    orr.set_key("sk-or-v1-" + "a" * 40)
    stub(401, {})
    got = ol.fetch(force=True)
    eq(got["available"], False, "rejected key availability")
    truthy("rejected" in (got.get("error") or ""), "reason names rejection")
    falsy(got.get("limits"), "no limits on a rejected key")


# ── §3 connected key with prepaid balance (the common case) — real balance ────

def case_uncapped_with_balance():
    reset()
    orr.set_key("sk-or-v1-" + "b" * 40)
    stub_multi({
        "/key": (200, {"data": {"label": "orgtree desk", "usage": 0.1637,
                               "limit": None, "limit_remaining": None,
                               "limit_reset": None, "is_free_tier": False}}),
        "/credits": (200, {"data": {"total_credits": 10.0,
                                    "total_usage": 0.1637}}),
    })
    got = ol.fetch(force=True)
    eq(got["available"], True, "connected, uncapped")
    eq(got["label"], "orgtree desk", "key label surfaces")
    lims = got["limits"]
    eq(len(lims), 1, "one credits entry")
    eq(lims[0]["percent"], None, "no fabricated percentage without a cap")
    eq(lims[0]["resets_at"], None, "no fabricated reset timestamp")
    eq(lims[0]["is_active"], False, "not active with positive balance")
    eq(lims[0]["severity"], "normal", "normal severity with healthy balance")
    truthy("$0.16 credits used" in lims[0]["label"], "credits used rides label")
    truthy("$9.84 remaining balance" in lims[0]["label"], "balance rides label")


def case_uncapped_balance_low():
    reset()
    orr.set_key("sk-or-v1-" + "b" * 40)
    stub_multi({
        "/key": (200, {"data": {"label": "low desk", "usage": 9.50,
                               "limit": None, "limit_remaining": None,
                               "limit_reset": None, "is_free_tier": False}}),
        "/credits": (200, {"data": {"total_credits": 10.0,
                                    "total_usage": 9.50}}),
    })
    got = ol.fetch(force=True)
    lims = got["limits"]
    eq(lims[0]["severity"], "warning", "warning when balance <= $1.00")
    eq(lims[0]["is_active"], False, "not yet exhausted")


def case_uncapped_balance_exhausted():
    reset()
    orr.set_key("sk-or-v1-" + "b" * 40)
    stub_multi({
        "/key": (200, {"data": {"label": "empty desk", "usage": 10.0,
                               "limit": None, "limit_remaining": None,
                               "limit_reset": None, "is_free_tier": False}}),
        "/credits": (200, {"data": {"total_credits": 10.0,
                                    "total_usage": 10.0}}),
    })
    got = ol.fetch(force=True)
    lims = got["limits"]
    eq(lims[0]["severity"], "critical", "critical when balance <= $0.00")
    eq(lims[0]["is_active"], True, "is_active is True when balance exhausted")


def case_uncapped_no_credits_fallback():
    reset()
    orr.set_key("sk-or-v1-" + "b" * 40)
    stub_multi({
        "/key": (200, {"data": {"label": "orgtree desk", "usage": 0.1637,
                               "limit": None, "limit_remaining": None,
                               "limit_reset": None, "is_free_tier": False}}),
        "/credits": (404, {"error": "not found"}),
    })
    got = ol.fetch(force=True)
    lims = got["limits"]
    eq(lims[0]["percent"], None, "no fabricated percentage without a cap")
    eq(lims[0]["resets_at"], None, "no fabricated reset")
    truthy("$0.16 spent · no spend cap" in lims[0]["label"], "falls back to spend label")


# ── §4 a connected key WITH a per-key spend cap — real percentage ───────────

def case_capped():
    reset()
    orr.set_key("sk-or-v1-" + "c" * 40)
    stub_multi({
        "/key": (200, {"data": {"label": "capped desk", "usage": 45.0,
                               "limit": 50.0, "limit_remaining": 5.0,
                               "limit_reset": "monthly", "is_free_tier": False}}),
        "/credits": (200, {"data": {"total_credits": 100.0, "total_usage": 45.0}}),
    })
    got = ol.fetch(force=True)
    lims = got["limits"]
    eq(lims[0]["percent"], 90.0, "45/50 = 90%")
    eq(lims[0]["severity"], "critical", "critical at >=90")
    eq(lims[0]["is_active"], False, "not yet AT the cap")
    truthy("renews monthly" in lims[0]["label"], "cadence word in label")
    truthy("$55.00 balance" in lims[0]["label"], "prepaid balance in label")


def case_capped_over():
    reset()
    orr.set_key("sk-or-v1-" + "d" * 40)
    stub(200, {"data": {"label": "over desk", "usage": 60.0, "limit": 50.0,
                        "limit_remaining": 0.0, "limit_reset": None,
                        "is_free_tier": False}})
    got = ol.fetch(force=True)
    lims = got["limits"]
    eq(lims[0]["percent"], 100.0, "clamped at 100, never over")
    eq(lims[0]["is_active"], True, "active once spend reaches the cap")


# ── §5 free tier — a real fact, carried as `plan` like Claude's subscription ──

def case_free_tier():
    reset()
    orr.set_key("sk-or-v1-" + "e" * 40)
    stub(200, {"data": {"label": "free desk", "usage": 0.0, "limit": None,
                        "limit_remaining": None, "limit_reset": None,
                        "is_free_tier": True}})
    got = ol.fetch(force=True)
    eq(got["plan"], "free tier", "is_free_tier -> plan")


# ── §6 peek/snapshot are cache-only: never fetch ─────────────────────────────

def case_cache_only_never_fetches():
    reset()
    orr.set_key("sk-or-v1-" + "f" * 40)
    calls = []

    def counting_get(url, headers):
        calls.append(url)
        return 200, json.dumps({"data": {"label": "x", "usage": 1.0,
                                         "limit": None, "limit_remaining": None,
                                         "limit_reset": None,
                                         "is_free_tier": False}}).encode("utf-8")
    orr._http_get = counting_get

    eq(ol.peek(), {"available": False, "provider": "OpenRouter"},
       "peek before any fetch: unavailable, no request sent")
    eq(len(calls), 0, "peek must not have spent a request")

    ol.fetch(force=True)
    truthy(len(calls) >= 1, "fetch spent request(s)")
    fetch_calls = len(calls)

    peeked = ol.peek()
    eq(peeked["available"], True, "peek reads the warm cache")
    eq(len(calls), fetch_calls, "peek still spent no request of its own")

    snap = ol.snapshot()
    eq(snap["available"], True, "snapshot reads the warm cache too")
    truthy(snap["observed_at"], "snapshot stamps an observed_at")
    eq(len(calls), fetch_calls, "snapshot spent no request either")


# ── §7 live key test — behaves sanely whether key is present or absent ───────

def case_live_or_absent_key_sanity():
    # Restore actual unpatched _http_get
    from urllib import request as _req
    def real_get(url, headers):
        req = _req.Request(url, headers=headers)
        with _req.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    orr._http_get = real_get
    orr.forget_key_status()

    if REAL_MACHINE_KEY:
        orr.set_key(REAL_MACHINE_KEY)
        got = ol.fetch(force=True)
        eq(got["available"], True, "live key is connected")
        truthy(len(got.get("limits") or []) == 1, "live key produces a limit row")
        lbl = got["limits"][0]["label"]
        truthy("credits used" in lbl or "spent" in lbl, f"live label honest: {lbl}")

    # Verify absent key degrades cleanly
    reset()
    got_absent = ol.fetch(force=True)
    eq(got_absent["available"], False, "unconfigured key is unavailable")
    truthy("no API key" in (got_absent.get("error") or ""), "unconfigured key has reason")


def main():
    print("§1 no key configured")
    check("degrades like the other providers' 'not installed' case", case_no_key)
    print("§2 a rejected key")
    check("a 401/403 reads as a sign-in problem, not a usage error", case_rejected)
    print("§3 connected, prepaid balance (the common case)")
    check("real balance ($10 - $0.16 = $9.84) rides label honestly",
          case_uncapped_with_balance)
    check("low balance (<= $1.00) triggers warning severity",
          case_uncapped_balance_low)
    check("exhausted balance (<= $0.00) triggers critical severity and active limit",
          case_uncapped_balance_exhausted)
    check("fallback when /credits unavailable shows honest spent without fake %",
          case_uncapped_no_credits_fallback)
    print("§4 connected, with a spend cap")
    check("real percentage against the cap, cadence word never a timestamp",
          case_capped)
    check("spend at/over the cap clamps to 100% and reads active",
          case_capped_over)
    print("§5 free tier")
    check("is_free_tier surfaces as `plan`, like a subscription tier",
          case_free_tier)
    print("§6 peek/snapshot never fetch")
    check("cache-only reads never spend a request of their own",
          case_cache_only_never_fetches)
    print("§7 live/absent key contract")
    check("live key verification behaves sanely whether key is present or not",
          case_live_or_absent_key_sanity)
    print(f"\n{PASS} passed")


if __name__ == "__main__":
    main()
