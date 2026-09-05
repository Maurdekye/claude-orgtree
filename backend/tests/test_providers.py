"""The provider registry (FR-15 preview): codex tiers exist as DATA, never as
hireable seats.

    python backend/tests/test_providers.py      (no pytest; plain asserts)

The axis ships before the adapter, so the ONE invariant that matters is
negative: nothing budget-bearing may learn the codex tiers. providers.py keeps
them out of ledger.TIERS on purpose — that way hire/rehire/switch_model reject
"sol" with the same "unknown tier" every other bad string gets, and there is
no new guard anywhere to rot. §2 proves the rejection AGAINST a proven-working
hire (anti-vacuity: a broken hire path would also "reject" sol, silently).

Detection (§3) is exercised hermetically: ORGTREE_CODEX pointed at files this
suite writes — a missing path, then a stub that answers `--version` — and
CODEX_HOME at a temp dir whose auth.json this suite authors. No network, no
real codex, no credential material beyond a fabricated JWT whose only claim
is an email this suite invented.
"""

import base64
import datetime as _dt
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-providers-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# an unreachable hub, or every org this rig creates registers against the
# operator's REAL roster (test_external_mail §1 guards exactly this)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
# hermetic on the antigravity axis too: providers_payload probes EVERY
# provider, so an unpinned rig would spawn the operator's real CLI (D-184)
os.environ["ORGTREE_ANTIGRAVITY"] = os.path.join(
    os.environ["ORGTREE_DATA"], "nowhere", "agy.exe")

from orgtree import codex_limits, providers                        # noqa: E402
from orgtree.ledger import (LedgerError, MODELS, Org, TIERS,       # noqa: E402
                            USER)

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def raises(fn, needle, what):
    try:
        fn()
    except LedgerError as e:
        if needle not in str(e):
            raise AssertionError(f"{what}: error said {e!r}, wanted {needle!r}")
        return
    raise AssertionError(f"{what}: no error raised")


def main():
    print("§1 the registry's two families")
    # FLIPPED at M4 (hire enablement): the codex tiers are now IN the
    # budget-bearing tables — the ledger prices every provider's seats from
    # one flat vocabulary, and providers.py DERIVES its views from it so a
    # seat price exists in exactly one place.
    check("codex tiers are IN ledger.TIERS with the ruled seats (M4)",
          lambda: eq({t: TIERS.get(t) for t in providers.CODEX_TIERS},
                     {"gpt-reserve": 0.2, "luna": 0.2, "terra": 2, "sol": 5,
                      "astra": 10},
                     "codex rows"))
    check("…and providers' views are DERIVED, not copied",
          lambda: eq((providers.CODEX_TIERS,
                      providers.CODEX_MODELS),
                     ({t: TIERS[t] for t in providers.CODEX_TIERS},
                      {t: MODELS[t] for t in providers.CODEX_TIERS}),
                     "derived views"))
    check("claude_tiers mirrors ledger minus the OTHER families (name, seat, "
          "model), cheap first",
          lambda: eq([(t["tier"], t["seat"], t["model"])
                      for t in providers.claude_tiers()],
                     [(t, TIERS[t], MODELS[t])
                      for t in sorted(TIERS, key=lambda k: TIERS[k])
                      if t not in providers.CODEX_TIERS
                      and t not in providers.ANTIGRAVITY_TIERS],
                     "claude family"))
    check("codex offer family omits conditional Astra without live evidence "
          "AND the legacy gpt-reserve token (item 12)",
          lambda: eq([(t["tier"], t["seat"], t["model"])
                      for t in providers.codex_tiers()],
                     [("luna", 0.2, "gpt-5.6-luna"),
                      ("terra", 2, "gpt-5.6-terra"),
                      ("sol", 5, "gpt-5.6-sol")], "codex family"))
    check("…while the AXIS still knows the legacy token (old nodes load)",
          lambda: eq(("gpt-reserve" in providers.CODEX_TIERS,
                      "gpt-reserve" in providers.CODEX_MODELS,
                      "gpt-reserve" in providers.LEGACY_CODEX_TIERS),
                     (True, True, True), "axis"))
    check("every codex tier carries a chip letter",
          lambda: eq([bool(t["letter"]) for t in providers.codex_tiers()],
                     [True, True, True], "letters"))
    check("gpt-reserve has Luna's price band",
          lambda: eq(providers.CODEX_PRICES["gpt-reserve"],
                     providers.CODEX_PRICES["luna"], "gpt-reserve price"))

    # ── the sub-$1 repricing (user ruling 2026-09-03) ──────────────────────
    # The point of the whole change: a seat is the $/M input price, so two
    # tiers that are 10× apart in dollars must be 10× apart in seats. Under
    # the old floor-to-1 rule luna and terra BOTH cost 1 and the ranking was
    # gone. This pins the rule against the price table rather than against a
    # hand-copied number, so a price revision that forgets the seat fails.
    print("§1b seats follow the STANDING $/M input, fractional below $1")

    def _expect(p: float) -> float:
        """ledger §3.1 / openrouter.seat_for, restated independently here."""
        return float(int(p)) if p >= 1 else max(0.10, round(p, 2))

    check("every codex seat equals its own standing input price by the rule",
          lambda: eq({t: TIERS[t] for t in providers.CODEX_TIERS},
                     # sol's $4 is a promo; the STANDING $5 sets the seat, so
                     # sol is priced from its documented standing band, not
                     # from the promotional CODEX_PRICES row
                     {"gpt-reserve": _expect(0.20), "luna": _expect(0.20),
                      "terra": _expect(2.00), "sol": _expect(5.00),
                      "astra": _expect(10.00)},
                     "codex seats vs prices"))
    check("…so luna is 10× cheaper than terra in SEATS, as it is in dollars",
          lambda: eq(round(TIERS["terra"] / TIERS["luna"], 6),
                     round(providers.CODEX_PRICES["terra"][0]
                           / providers.CODEX_PRICES["luna"][0], 6),
                     "seat ratio == price ratio"))
    check("no seat sits below the 0.10 floor",
          lambda: eq(sorted({t: s for t, s in TIERS.items() if s < 0.10}),
                     [], "sub-floor seats"))
    check("flash stays 1 — $0.75 is launch pricing and a promo never sets a "
          "seat (user rulings 2026-09-02 and 2026-08-28)",
          lambda: eq((TIERS["flash"],
                      providers.ANTIGRAVITY_PRICES["gemini-3.8-flash"][0]),
                     (1, 0.75), "flash seat vs promo price"))
    check("haiku stays 1 — exactly $1/M lands on the >=$1 branch",
          lambda: eq(TIERS["haiku"], _expect(1.00), "haiku"))

    print("§2 codex tiers are LEDGER-hireable since M4 (the connected-"
          "provider gate is api.py's, tested in test_codex_dispatch §6)")
    org = Org.create("prov-test")
    org.hire(USER, None, "opus", 20, "top")
    top = next(i for i, n in org.d["nodes"].items()
               if n.get("parent") is None)
    org.hire(USER, top, "haiku", 0, "canary")
    check("(canary) a claude hire works",
          lambda: eq(len(org.d["nodes"]), 2, "node count"))
    check("a sol hire is a plain ledger hire, seat 5",
          lambda: eq((org.hire(USER, top, "sol", 0, "x-sol") and
                      org.d["nodes"]["x-sol"]["model"],
                      org.seat_cost("x-sol")), ("sol", 5), "sol hire"))
    canary = next(i for i in org.d["nodes"] if i not in (top, "x-sol"))
    check("switch_model to 'terra' works and re-prices the seat",
          lambda: (org.switch_model(USER, canary, "terra"),
                   eq((org.d["nodes"][canary]["model"],
                       org.seat_cost(canary)), ("terra", 2), "switch"))[1])
    check("a truly unknown tier is still refused",
          lambda: raises(lambda: org.hire(USER, top, "bard-ultra", 0, "x"),
                         "unknown tier", "unknown"))

    print("§3 detection — hermetic, against files this suite writes")
    tmp = tempfile.mkdtemp(prefix="orgtree-codexdet-")
    os.environ["CODEX_HOME"] = os.path.join(tmp, "home")
    os.makedirs(os.environ["CODEX_HOME"], exist_ok=True)

    os.environ["ORGTREE_CODEX"] = os.path.join(tmp, "nowhere", "codex.exe")
    st = providers.codex_status(force=True)
    check("an env override pointing at NOTHING is not 'installed' — it must "
          "route the user to the override, not to `codex login`",
          lambda: eq((st["installed"], st["source"]), (False, "env"), "state"))
    pay = providers.providers_payload({"installed": True})
    codex = next(p for p in pay["providers"] if p["id"] == "openai")
    check("…and the payload's reason says install, not sign-in",
          lambda: eq("not installed" in (codex["reason"] or ""), True,
                     f"reason {codex['reason']!r}"))

    stub = os.path.join(tmp, "codex.cmd")
    with open(stub, "w", encoding="ascii") as f:
        f.write("@echo codex-cli 9.9.9\n")
    os.environ["ORGTREE_CODEX"] = stub
    st = providers.codex_status(force=True)
    check("a real (stub) CLI probes to its --version — no package.json "
          "anywhere above it, so this exercises the subprocess leg",
          lambda: eq((st["installed"], st["version"]), (True, "9.9.9"),
                     "probe"))
    check("…but with no auth.json it is not connected",
          lambda: eq(st["connected"], False, "connected"))

    email = "probe@example.test"
    payload = base64.urlsafe_b64encode(
        json.dumps({"email": email}).encode()).decode().rstrip("=")
    with open(os.path.join(os.environ["CODEX_HOME"], "auth.json"), "w",
              encoding="utf-8") as f:
        json.dump({"tokens": {"id_token": f"eyJh.{payload}.sig"}}, f)
    st = providers.codex_status(force=True)
    check("a chatgpt-login auth.json reads as connected, with the identity "
          "decoded for display (anti-vacuity: the planted email must be SEEN)",
          lambda: eq((st["connected"], st["kind"], st["email"]),
                     (True, "chatgpt", email), "chatgpt lane"))
    with open(os.path.join(os.environ["CODEX_HOME"], "auth.json"), "w",
              encoding="utf-8") as f:
        json.dump({"OPENAI_API_KEY": "sk-proj-fake"}, f)
    st = providers.codex_status(force=True)
    check("an API-key auth.json reads as connected via the key lane",
          lambda: eq((st["connected"], st["kind"]), (True, "api-key"),
                     "key lane"))

    def chatgpt_login():
        payload = base64.urlsafe_b64encode(
            json.dumps({"email": email}).encode()).decode().rstrip("=")
        with open(os.path.join(os.environ["CODEX_HOME"], "auth.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"tokens": {"id_token": f"eyJh.{payload}.sig"}}, f)
        providers.codex_status(force=True)

    def board(*, reserve_granted):
        """Publish a usage board into `codex_limits`' cache.

        Hermetic AND load-bearing: the reserve rule reads this board, so
        without one the suite would fall through to spawning the operator's
        real codex app-server (D-184's lesson, on a new axis). A granted pool
        has a rate-limit window of its OWN, named after the model; a withdrawn
        one has none, and that presence/absence IS the signal.
        """
        account = {
            "limitId": "codex", "limitName": None,
            "primary": {"usedPercent": 4, "windowDurationMins": 10080,
                        "resetsAt": 1788764643},
            "rateLimitReachedType": None,
        }
        by_id = {"codex": account}
        if reserve_granted:
            by_id["base_model_inference"] = {
                "limitId": "base_model_inference", "limitName": "gpt-reserve",
                "primary": {"usedPercent": 8, "windowDurationMins": 10080,
                            "resetsAt": 1788960413},
                "rateLimitReachedType": None,
            }
        codex_limits._cache.update(at=time.time(), data=codex_limits._normalize(
            {"rateLimits": account, "rateLimitsByLimitId": by_id}))

    def openai_entry():
        p = providers.providers_payload({"installed": True, "connected": True})
        return next(x for x in p["providers"] if x["id"] == "openai")

    def reserve_dark_on_api_key():
        # reserve capacity is a ChatGPT-subscription grant, so an api-key
        # session — CONNECTED, and hireable for the rest of the family — must
        # still show reserve as unavailable, with a reason naming the remedy.
        board(reserve_granted=True)      # the board is NOT what refuses
        cx = openai_entry()
        eq(cx["hire_enabled"], True, "codex family stays hireable")
        eq(cx["reserve_hire_enabled"], False, "reserve is dark on api-key")
        assert "chatgpt" in (cx["reserve_reason"] or "").lower(), cx
    check("gpt-reserve is dark on an api-key session even though the rest "
          "of codex is hireable", reserve_dark_on_api_key)

    def reserve_lit_on_chatgpt():
        chatgpt_login()
        board(reserve_granted=True)
        cx = openai_entry()
        eq((cx["reserve_hire_enabled"], cx["reserve_reason"]), (True, None),
           "reserve lights up on a chatgpt login")
    check("…and lights back up on a ChatGPT login", reserve_lit_on_chatgpt)

    def reserve_dark_when_the_grant_is_withdrawn():
        """THE 2026-09-02 REPORT. Same login, same machine, same connected
        CLI — and the reserve grant is gone. Login kind cannot see that; the
        account's rate-limit board can, because the pool's own window goes
        with it."""
        chatgpt_login()
        board(reserve_granted=False)
        cx = openai_entry()
        eq(cx["hire_enabled"], True, "the other three tiers are unaffected")
        eq(cx["reserve_hire_enabled"], False, "reserve goes dark with the grant")
        assert "no gpt-reserve capacity" in (cx["reserve_reason"] or ""), cx
    check("gpt-reserve goes dark when the account holds no reserve window, on "
          "an unchanged ChatGPT login", reserve_dark_when_the_grant_is_withdrawn)

    def reserve_lights_back_up_when_the_grant_returns():
        """THE 2026-09-03 REPORT, the other edge: "i suddenly have access to
        gpt reserve again, but the reserve token is not showing anymore."
        The document has to breathe in BOTH directions, in one process."""
        chatgpt_login()
        board(reserve_granted=False)
        eq(openai_entry()["reserve_hire_enabled"], False, "dark first")
        board(reserve_granted=True)
        cx = openai_entry()
        eq((cx["reserve_hire_enabled"], cx["reserve_reason"]), (True, None),
           "and lit again on the very next payload, no restart")
    check("…and lights back up on the next payload when the grant returns",
          reserve_lights_back_up_when_the_grant_returns)

    def unknown_evidence_never_refuses():
        """ANTI-VACUITY, and the rule that keeps this from being a new bug:
        a board that cannot be read is UNKNOWN, and unknown must leave the
        tier alone. A detection that failed closed would take gpt-reserve
        away from every machine it cannot ask."""
        chatgpt_login()
        codex_limits.invalidate()
        saved = codex_limits.fetch
        codex_limits.fetch = lambda force=False: {"available": False}  # type: ignore[assignment]
        try:
            cx = openai_entry()
        finally:
            codex_limits.fetch = saved                       # type: ignore[assignment]
        eq((cx["reserve_hire_enabled"], cx["reserve_reason"]), (True, None),
           "unknown evidence leaves reserve offered")
    check("no evidence either way is not a refusal", unknown_evidence_never_refuses)

    print("§4 the payload the panel renders")
    pay = providers.providers_payload({"installed": True, "connected": True})
    # grew to three at D-184 — the antigravity entry's own behaviour is
    # test_antigravity_providers.py's; here it only has to hold its place in
    # line. Four since 2026-09-02: openrouter, the API-backed lane, LAST — its
    # own behaviour is test_openrouter.py's
    check("exactly four providers, claude first, openrouter last",
          lambda: eq([p["id"] for p in pay["providers"]],
                     ["claude", "openai", "google", "openrouter"], "order"))
    codex = next(p for p in pay["providers"] if p["id"] == "openai")
    # FLIPPED at the MVP (M1–M8 standing): the vision live — a CONNECTED CLI
    # is a hireable provider, the same predicate the api hire gate enforces.
    check("codex hire_enabled FOLLOWS connection: connected ⇒ hireable, "
          "no reason to show",
          lambda: eq((codex["hire_enabled"], codex["reason"]), (True, None),
                     "connected entry"))

    def disconnected_entry():
        os.remove(os.path.join(os.environ["CODEX_HOME"], "auth.json"))
        providers.codex_status(force=True)
        p2 = providers.providers_payload({"installed": True})
        cx = next(p for p in p2["providers"] if p["id"] == "openai")
        eq((cx["hire_enabled"], "codex login" in (cx["reason"] or "")),
           (False, True), "disconnected entry")
    check("…and signed-out ⇒ not hireable, reason names the login",
          disconnected_entry)
    claude = next(p for p in pay["providers"] if p["id"] == "claude")
    check("the claude entry passes the composed status through, hireable",
          lambda: eq((claude["hire_enabled"], claude["status"]["installed"]),
                     (True, True), "claude entry"))

    print("§5 auto-autopsy model tier availability and fable exclusion")
    check("fable tier is rejected for auto-autopsy",
          lambda: eq(providers.tier_availability("fable")[0], False, "fable must not be available"))
    check("fable rejection reason is explicit",
          lambda: eq("fable cannot be used" in (providers.tier_availability("fable")[1] or ""), True, "rejection message"))
    check("unknown tier is not a known tier",
          lambda: eq(providers.is_known_tier("bogus-tier-xyz"), False, "unknown tier is unknown"))
    check("unknown tier availability returns False with unknown reason",
          lambda: eq(providers.tier_availability("bogus-tier-xyz")[0], False, "unknown tier unavailable"))
    check("claude and codex tiers are recognised",
          lambda: eq((providers.is_known_tier("opus"), providers.is_known_tier("sol")), (True, True), "known tiers recognised"))

    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
