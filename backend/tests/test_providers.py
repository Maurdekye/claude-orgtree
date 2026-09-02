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
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-providers-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# an unreachable hub, or every org this rig creates registers against the
# operator's REAL roster (test_external_mail §1 guards exactly this)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
# hermetic on the gemini axis too: providers_payload probes EVERY provider,
# so an unpinned rig would read the operator's real ~/.gemini (D-184)
os.environ["ORGTREE_GEMINI"] = os.path.join(
    os.environ["ORGTREE_DATA"], "nowhere", "gemini.js")
os.environ["ORGTREE_GEMINI_HOME"] = os.path.join(
    os.environ["ORGTREE_DATA"], "ghome")

from orgtree import providers                                      # noqa: E402
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
                     {"gpt-reserve": 1, "luna": 1, "terra": 2, "sol": 5},
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
                      and t not in providers.GEMINI_TIERS],
                     "claude family"))
    check("codex family is gpt-reserve 1 · luna 1 · terra 2 · sol 5",
          lambda: eq([(t["tier"], t["seat"], t["model"])
                      for t in providers.codex_tiers()],
                     [("gpt-reserve", 1, "gpt-reserve"),
                      ("luna", 1, "gpt-5.6-luna"),
                      ("terra", 2, "gpt-5.6-terra"),
                      ("sol", 5, "gpt-5.6-sol")], "codex family"))
    check("every codex tier carries a chip letter",
          lambda: eq([bool(t["letter"]) for t in providers.codex_tiers()],
                     [True, True, True, True], "letters"))
    check("gpt-reserve has Luna's price band",
          lambda: eq(providers.CODEX_PRICES["gpt-reserve"],
                     providers.CODEX_PRICES["luna"], "gpt-reserve price"))

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
          lambda: raises(lambda: org.hire(USER, top, "gemini-ultra", 0, "x"),
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

    print("§4 the payload the panel renders")
    pay = providers.providers_payload({"installed": True, "connected": True})
    # grew to three at D-184 (gemini) — the gemini entry's own behaviour is
    # test_gemini_providers.py's; here it only has to hold its place in line
    check("exactly three providers, claude first",
          lambda: eq([p["id"] for p in pay["providers"]],
                     ["claude", "openai", "google"], "order"))
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

    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
