"""The gemini provider axis (D-184): tiers, prices, detection, payload.

    python backend/tests/test_gemini_providers.py   (no pytest; plain asserts)

Everything hermetic: ORGTREE_GEMINI / ORGTREE_GEMINI_HOME point at files this
suite writes; ORGTREE_CODEX points at nothing so the payload never probes the
machine's real codex install. No network, no real CLI, no credential material.

The cost-fold section is the value-bearing half: the wire facts it encodes
(input excludes cached on the one-shot lane, output excludes reasoning on the
ACP lane, side models billed at their OWN rows, the pro >200K band, unknown
models priced at the fallback rather than $0) were all measured live
2026-08-29 — see the probe logs banked in the implementing agent's scratch.
"""

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-gemprov-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
# hermetic: the codex side of providers_payload must not see a real install
os.environ["ORGTREE_CODEX"] = os.path.join(
    os.environ["ORGTREE_DATA"], "nowhere", "codex.exe")
os.environ["CODEX_HOME"] = os.path.join(os.environ["ORGTREE_DATA"], "chome")

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
    print("§1 the gemini family in the registry")
    check("gemini tiers are IN ledger.TIERS with the ruled seats "
          "(flash $1.50→1, pro $2→2; the >200K surcharge never sets a seat)",
          lambda: eq({t: TIERS.get(t) for t in providers.GEMINI_TIERS},
                     {"flash": 1, "pro": 2}, "gemini rows"))
    check("providers' views are DERIVED from the ledger, not copied",
          lambda: eq((providers.GEMINI_TIERS, providers.GEMINI_MODELS),
                     ({t: TIERS[t] for t in providers.GEMINI_TIERS},
                      {t: MODELS[t] for t in providers.GEMINI_TIERS}),
                     "derived views"))
    check("gemini family is flash 1 · pro 2, the CLI-registry ids, letters F/P",
          lambda: eq([(t["tier"], t["seat"], t["model"], t["letter"])
                      for t in providers.gemini_tiers()],
                     [("flash", 1, "gemini-3.5-flash", "F"),
                      ("pro", 2, "gemini-3.1-pro-preview-customtools", "P")],
                     "gemini family"))
    check("claude_tiers excludes BOTH other families",
          lambda: eq([(t["tier"], t["seat"], t["model"])
                      for t in providers.claude_tiers()],
                     [(t, TIERS[t], MODELS[t])
                      for t in sorted(TIERS, key=lambda k: TIERS[k])
                      if t not in providers.CODEX_TIERS
                      and t not in providers.GEMINI_TIERS],
                     "claude family"))

    print("§2 gemini tiers are plain ledger hires")
    org = Org.create("gem-prov-test")
    org.hire(USER, None, "opus", 20, "top")
    top = next(i for i, n in org.d["nodes"].items()
               if n.get("parent") is None)
    org.hire(USER, top, "haiku", 0, "canary")
    check("(canary) a claude hire works",
          lambda: eq(len(org.d["nodes"]), 2, "node count"))
    check("a pro hire is a plain ledger hire, seat 2",
          lambda: eq((org.hire(USER, top, "pro", 0, "x-pro") and
                      org.d["nodes"]["x-pro"]["model"],
                      org.seat_cost("x-pro")), ("pro", 2), "pro hire"))
    check("switch_model to 'flash' works and re-prices the seat to 1",
          lambda: (org.switch_model(USER, "x-pro", "flash"),
                   eq((org.d["nodes"]["x-pro"]["model"],
                       org.seat_cost("x-pro")), ("flash", 1), "switch"))[1])
    check("a truly unknown tier is still refused",
          lambda: raises(lambda: org.hire(USER, top, "bard", 0, "x"),
                         "unknown tier", "unknown"))

    print("§3 the cost fold (per MODEL ID, measured wire semantics)")
    multi = {"models": {
        "gemini-3.5-flash": {"input": 8000, "cached": 2000,
                             "output": 500, "prompt": 10000},
        # the measured utility_router side spend — billed at ITS OWN row
        "gemini-3.1-flash-lite": {"input": 795, "cached": 0,
                                  "output": 495, "prompt": 795}},
        "main": "gemini-3.5-flash"}
    check("a multi-model turn sums every model at its own row "
          "(8000·1.5 + 2000·.15 + 500·9 + 795·.25 + 495·1.5 per M)",
          lambda: eq(providers.gemini_cost(multi), 0.017741, "multi cost"))
    check("occupancy is the MAIN model's full prompt (input+cached), never "
          "a side model's",
          lambda: eq(providers.gemini_occupancy(multi), 10000, "occ"))
    pro_id = "gemini-3.1-pro-preview-customtools"
    long_doc = {"models": {pro_id: {"input": 250_000, "cached": 0,
                                    "output": 1000, "prompt": 250_000}},
                "main": pro_id}
    check("pro above 200K prompt bills the long-context band ($4/$18)",
          lambda: eq(providers.gemini_cost(long_doc), 1.018, "long band"))
    edge = {"models": {pro_id: {"input": 200_000, "cached": 0,
                                "output": 1000, "prompt": 200_000}},
            "main": pro_id}
    check("…and exactly 200K is still the standard band (strict >)",
          lambda: eq(providers.gemini_cost(edge), 0.412, "band edge"))
    stranger = {"models": {"gemini-9.9-mystery": {
        "input": 1_000_000, "cached": 0, "output": 0, "prompt": 1}}}
    check("an unlisted model id is PRICED (fallback row), never a silent $0",
          lambda: eq(providers.gemini_cost(stranger), 2.0, "fallback"))
    check("no usage document reads as $0 and occupancy 0 (no measurement)",
          lambda: eq((providers.gemini_cost(None), providers.gemini_cost({}),
                      providers.gemini_occupancy(None)), (0.0, 0.0, 0),
                     "empties"))
    headless = {"models": {"gemini-3.5-flash": {
        "input": 5, "cached": 0, "output": 5, "prompt": 5}}}
    check("a doc with no 'main' key still measures occupancy (max prompt)",
          lambda: eq(providers.gemini_occupancy(headless), 5, "mainless"))
    looped = {"models": {"gemini-3.5-flash": {
        "input": 3_614_165, "cached": 0, "output": 4_093,
        "prompt": 3_614_165}}, "main": "gemini-3.5-flash", "requests": 30}
    check("a tool-looping turn's SUMMED input divides by the request count — "
          "the live 3.6M-against-a-1M-window regression reads ~120K, and the "
          "same doc still BILLS the full sum (that is what Google charges)",
          lambda: eq((providers.gemini_occupancy(looped),
                      providers.gemini_cost(looped)),
                     (120_472, 5.458085), "request divisor"))

    print("§4 detection — hermetic, against files this suite writes")
    tmp = tempfile.mkdtemp(prefix="orgtree-gemdet-")
    os.environ["ORGTREE_GEMINI_HOME"] = os.path.join(tmp, "home")
    os.makedirs(os.environ["ORGTREE_GEMINI_HOME"], exist_ok=True)

    os.environ["ORGTREE_GEMINI"] = os.path.join(tmp, "nowhere", "gemini.js")
    st = providers.gemini_status(force=True)
    check("an env override pointing at NOTHING is not 'installed' — route "
          "the user to the override, not to a login",
          lambda: eq((st["installed"], st["source"]), (False, "env"), "state"))

    # a pin-shaped layout: bundle/gemini.js under a package.json that names
    # the CLI — version must come from the metadata, no subprocess
    pkg = os.path.join(tmp, "node_modules", "@google", "gemini-cli")
    os.makedirs(os.path.join(pkg, "bundle"), exist_ok=True)
    with open(os.path.join(pkg, "package.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "@google/gemini-cli", "version": "9.9.9"}, f)
    entry = os.path.join(pkg, "bundle", "gemini.js")
    with open(entry, "w", encoding="ascii") as f:
        f.write("// stub\n")
    os.environ["ORGTREE_GEMINI"] = entry
    st = providers.gemini_status(force=True)
    check("a JS entry reads its version from the package metadata "
          "(anti-vacuity: the planted 9.9.9 must be SEEN)",
          lambda: eq((st["installed"], st["version"]), (True, "9.9.9"),
                     "version"))
    check("…but with no auth records it is not connected",
          lambda: eq(st["connected"], False, "connected"))
    check("argv shapes: .py → this interpreter, .js → node, from one contract",
          lambda: eq((providers.gemini_argv("x.py")[0],
                      providers.gemini_argv(entry)),
                     (sys.executable, ["node", entry]), "argv"))

    home = os.environ["ORGTREE_GEMINI_HOME"]

    def _settings(selected):
        with open(os.path.join(home, "settings.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"security": {"auth": {"selectedType": selected}}}, f)

    _settings("gemini-api-key")
    st = providers.gemini_status(force=True)
    check("an api-key selection reads as connected via the key lane (the key "
          "itself lives in the OS keychain and is never opened)",
          lambda: eq((st["connected"], st["kind"]), (True, "api-key"),
                     "key lane"))
    _settings("oauth-personal")
    st = providers.gemini_status(force=True)
    check("an oauth selection WITHOUT oauth_creds.json is NOT connected",
          lambda: eq(st["connected"], False, "oauth absent"))
    email = "gem-probe@example.test"
    with open(os.path.join(home, "oauth_creds.json"), "w",
              encoding="utf-8") as f:
        f.write("{}")
    with open(os.path.join(home, "google_accounts.json"), "w",
              encoding="utf-8") as f:
        json.dump({"active": email, "old": []}, f)
    st = providers.gemini_status(force=True)
    check("oauth with creds present connects, identity decoded for display "
          "(anti-vacuity: the planted email must be SEEN)",
          lambda: eq((st["connected"], st["kind"], st["email"]),
                     (True, "oauth", email), "oauth lane"))
    _settings("vertex-ai")
    st = providers.gemini_status(force=True)
    check("a vertex selection reads as connected via the vertex lane",
          lambda: eq((st["connected"], st["kind"]), (True, "vertex"),
                     "vertex lane"))

    print("§5 the payload the panel renders")
    _settings("gemini-api-key")
    providers.gemini_status(force=True)
    providers.codex_status(force=True)
    pay = providers.providers_payload({"installed": True, "connected": True})
    check("three providers, claude first, gemini (id google) last",
          lambda: eq([p["id"] for p in pay["providers"]],
                     ["claude", "openai", "google"], "order"))
    gem = next(p for p in pay["providers"] if p["id"] == "google")
    check("the label is the CLI's product name, 'Gemini', cli 'Gemini CLI'",
          lambda: eq((gem["label"], gem["cli"]), ("Gemini", "Gemini CLI"),
                     "naming"))
    check("gemini hire_enabled FOLLOWS connection: connected ⇒ hireable, "
          "no reason to show",
          lambda: eq((gem["hire_enabled"], gem["reason"]), (True, None),
                     "connected entry"))

    def disconnected_entry():
        _settings("oauth-personal")
        os.remove(os.path.join(home, "oauth_creds.json"))
        providers.gemini_status(force=True)
        p2 = providers.providers_payload({"installed": True})
        g2 = next(p for p in p2["providers"] if p["id"] == "google")
        eq((g2["hire_enabled"], "login method" in (g2["reason"] or "")),
           (False, True), "disconnected entry")
    check("…and signed-out ⇒ not hireable, reason names the next step",
          disconnected_entry)

    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
