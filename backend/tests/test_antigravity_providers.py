"""The antigravity provider axis (D-184, re-walked for the Antigravity CLI):
tiers, prices, effort, detection, payload.

    python backend/tests/test_antigravity_providers.py   (no pytest)

Everything hermetic: ORGTREE_ANTIGRAVITY points at the scripted double (or
at nothing); ORGTREE_CODEX points at nothing so the payload never probes the
machine's real codex install. No network, no real CLI, no credential
material.

The cost-fold section is the value-bearing half: the wire facts it encodes
(input EXCLUDES cached reads, output INCLUDES thinking, the pro >200K band,
an unknown model priced at the fallback rather than $0, occupancy from the
LAST request) were all measured live 2026-09-02 — see the probe logs banked
in the implementing agent's scratch.
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-agyprov-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
# hermetic: the codex side of providers_payload must not see a real install
os.environ["ORGTREE_CODEX"] = os.path.join(
    os.environ["ORGTREE_DATA"], "nowhere", "codex.exe")
os.environ["CODEX_HOME"] = os.path.join(os.environ["ORGTREE_DATA"], "chome")
FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "fakeantigravity.py")
os.environ["ORGTREE_ANTIGRAVITY"] = os.path.join(
    os.environ["ORGTREE_DATA"], "nowhere", "agy.exe")

from orgtree import providers                                      # noqa: E402
from orgtree.ledger import (LedgerError, MODELS, MODEL_VERSIONS,   # noqa: E402
                            Org, TIERS, USER)

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
    print("§1 the antigravity family in the registry")
    check("antigravity tiers are IN ledger.TIERS with the ruled seats "
          "(flash $1.50 standing→1, pro $2→2; promos and the >200K "
          "surcharge never set a seat)",
          lambda: eq({t: TIERS.get(t) for t in providers.ANTIGRAVITY_TIERS},
                     {"flash": 1, "pro": 2}, "rows"))
    check("providers' views are DERIVED from the ledger, not copied",
          lambda: eq((providers.ANTIGRAVITY_TIERS, providers.ANTIGRAVITY_MODELS),
                     ({t: TIERS[t] for t in providers.ANTIGRAVITY_TIERS},
                      {t: MODELS[t] for t in providers.ANTIGRAVITY_TIERS}),
                     "derived views"))
    check("the family is flash 1 · pro 2, the CLI-registry BASE ids "
          "(flash pinned to 3.8 by user instruction), letters F/P",
          lambda: eq([(t["tier"], t["seat"], t["model"], t["letter"])
                      for t in providers.antigravity_tiers()],
                     [("flash", 1, "gemini-3.8-flash", "F"),
                      ("pro", 2, "gemini-3.1-pro", "P")], "family"))
    check("the flash tier offers its three registry generations as model "
          "VERSIONS, default first",
          lambda: eq(list(MODEL_VERSIONS["flash"].items()),
                     [("3.8", "gemini-3.8-flash"), ("3.7", "gemini-3.7-flash"),
                      ("3.6", "gemini-3.6-flash")], "versions"))
    check("claude_tiers excludes BOTH other families",
          lambda: eq([(t["tier"], t["seat"], t["model"])
                      for t in providers.claude_tiers()],
                     [(t, TIERS[t], MODELS[t])
                      for t in sorted(TIERS, key=lambda k: TIERS[k])
                      if t not in providers.CODEX_TIERS
                      and t not in providers.ANTIGRAVITY_TIERS],
                     "claude family"))
    check("provider_of answers google for the family, and the label is the "
          "CLI's own product name",
          lambda: eq((providers.provider_of("flash"), providers.provider_of("pro"),
                      providers.provider_label("flash")),
                     ("google", "google", "Antigravity"), "axis"))

    print("§2 antigravity tiers are plain ledger hires")
    org = Org.create("agy-prov-test")
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
    check("a fresh org's flash default is the 3.8 id and a version pin "
          "resolves through model_for",
          lambda: (eq(org.model_for("x-pro"), "gemini-3.8-flash", "default"),
                   org.set_scope(USER, "x-pro", model_version="3.7"),
                   eq(org.model_for("x-pro"), "gemini-3.7-flash", "pinned"))[2])
    check("a truly unknown tier is still refused",
          lambda: raises(lambda: org.hire(USER, top, "bard", 0, "x"),
                         "unknown tier", "unknown"))

    print("§3 the cost fold (measured wire semantics)")
    doc = {"model": "gemini-3.8-flash", "input": 8000, "cached": 2000,
           "output": 500, "thinking": 300, "last_prompt": 10000,
           "requests": 1}
    check("a flash turn bills uncached input, cached reads and output "
          "(thinking included) at the 3.8-flash row "
          "(8000·.75 + 2000·.075 + 500·3.75 per M)",
          lambda: eq(providers.antigravity_cost(doc), 0.008025, "flash cost"))
    check("occupancy is the LAST request's prompt (input + cached), the "
          "field the runner measured, never the turn's summed input",
          lambda: eq(providers.antigravity_occupancy(doc), 10000, "occ"))
    long_doc = {"model": "gemini-3.1-pro", "input": 250_000, "cached": 0,
                "output": 1000, "last_prompt": 250_000}
    check("pro above 200K prompt bills the long-context band ($4/$18)",
          lambda: eq(providers.antigravity_cost(long_doc), 1.018, "long band"))
    edge = {"model": "gemini-3.1-pro", "input": 200_000, "cached": 0,
            "output": 1000, "last_prompt": 200_000}
    check("…and exactly 200K is still the standard band (strict >)",
          lambda: eq(providers.antigravity_cost(edge), 0.412, "band edge"))
    stranger = {"model": "mystery-9.9-model", "input": 1_000_000,
                "cached": 0, "output": 0, "last_prompt": 1}
    check("an unlisted model id is PRICED (fallback row), never a silent $0",
          lambda: eq(providers.antigravity_cost(stranger), 2.0, "fallback"))
    check("no usage document reads as $0 and occupancy 0 (no measurement)",
          lambda: eq((providers.antigravity_cost(None),
                      providers.antigravity_cost({}),
                      providers.antigravity_occupancy(None),
                      providers.antigravity_occupancy({"model": "x"})),
                     (0.0, 0.0, 0, 0), "empties"))
    check("the version ids of the flash tier all have their own price row",
          lambda: eq([v in providers.ANTIGRAVITY_PRICES
                      for v in MODEL_VERSIONS["flash"].values()],
                     [True, True, True], "rows"))

    print("§4 effort: orgtree's vocabulary → the CLI's --effort, per tier")
    check("flash takes low|medium|high; xhigh/max clamp to high",
          lambda: eq([providers.antigravity_effort("flash", e)
                      for e in ("low", "medium", "high", "xhigh", "max")],
                     ["low", "medium", "high", "high", "high"], "flash"))
    check("pro takes low|high only (medium is refused by the CLI): medium "
          "and above become high",
          lambda: eq([providers.antigravity_effort("pro", e)
                      for e in ("low", "medium", "high", "xhigh", "max")],
                     ["low", "high", "high", "high", "high"], "pro"))
    check("an unknown effort word never reaches the wire — it reads as high",
          lambda: eq(providers.antigravity_effort("flash", "ultra"), "high",
                     "unknown"))

    print("§5 detection — hermetic, against the scripted double")
    st = providers.antigravity_status(force=True)
    check("an env override pointing at NOTHING is not 'installed' — route "
          "the user to the override, not to a login",
          lambda: eq((st["installed"], st["source"], st["connected"]),
                     (False, "env", False), "state"))
    os.environ["ORGTREE_ANTIGRAVITY"] = FAKE
    os.environ.pop("FAKEANTIGRAVITY_SIGNED_OUT", None)
    st = providers.antigravity_status(force=True)
    check("a signed-in CLI reads as installed, versioned from --version, "
          "connected via the models registry, identity from the CLI's own "
          "log (anti-vacuity: the planted version and email must be SEEN)",
          lambda: eq((st["installed"], st["version"], st["connected"],
                      st["kind"], st["email"],
                      "gemini-3.8-flash-high" in st["models"]),
                     (True, "1.1.24", True, "oauth", "fake-agy@example.test",
                      True), "signed in"))
    os.environ["FAKEANTIGRAVITY_SIGNED_OUT"] = "1"
    st = providers.antigravity_status(force=True)
    check("a signed-out CLI (empty registry) is installed but NOT connected, "
          "no identity, no models",
          lambda: eq((st["installed"], st["connected"], st["kind"],
                      st["email"], st["models"]),
                     (True, False, None, None, []), "signed out"))
    os.environ.pop("FAKEANTIGRAVITY_SIGNED_OUT", None)
    check("argv shapes: .py → this interpreter, a binary → itself, from one "
          "contract",
          lambda: eq((providers.antigravity_argv("x.py")[0],
                      providers.antigravity_argv("C:/x/agy.exe")),
                     (sys.executable, ["C:/x/agy.exe"]), "argv"))
    check("the install hint names the installer THIS platform runs, never "
          "an npm command",
          lambda: eq(("winget install Google.AntigravityCLI"
                      in providers.install_hint("google")) if os.name == "nt"
                     else ("antigravity.google/cli/install.sh"
                           in providers.install_hint("google")),
                     True, "hint"))
    env = providers.antigravity_env({"ANTHROPIC_API_KEY": "a",
                                     "OPENAI_API_KEY": "o", "CLAUDECODE": "1",
                                     "CLAUDE_CODE_ENTRYPOINT": "x",
                                     "GOOGLE_CLOUD_PROJECT": "keep"})
    check("the spawn env strips the other providers' material, keeps this "
          "one's, and switches the CLI's self-update off",
          lambda: eq(env, {"GOOGLE_CLOUD_PROJECT": "keep",
                           "AGY_CLI_DISABLE_AUTO_UPDATE": "1"}, "env"))

    print("§6 the payload the panel renders")
    providers.antigravity_status(force=True)
    providers.codex_status(force=True)
    pay = providers.providers_payload({"installed": True, "connected": True})
    check("three providers, claude first, antigravity (id google) last",
          lambda: eq([p["id"] for p in pay["providers"]],
                     ["claude", "openai", "google"], "order"))
    agy = next(p for p in pay["providers"] if p["id"] == "google")
    check("the label is the CLI's product name, 'Antigravity', cli "
          "'Antigravity CLI'",
          lambda: eq((agy["label"], agy["cli"]),
                     ("Antigravity", "Antigravity CLI"), "naming"))
    check("hire_enabled FOLLOWS connection: connected ⇒ hireable, no reason",
          lambda: eq((agy["hire_enabled"], agy["reason"]), (True, None),
                     "connected entry"))

    def disconnected_entry():
        os.environ["FAKEANTIGRAVITY_SIGNED_OUT"] = "1"
        try:
            providers.antigravity_status(force=True)
            p2 = providers.providers_payload({"installed": True})
            g2 = next(p for p in p2["providers"] if p["id"] == "google")
            eq((g2["hire_enabled"], "sign in" in (g2["reason"] or "")),
               (False, True), "disconnected entry")
        finally:
            os.environ.pop("FAKEANTIGRAVITY_SIGNED_OUT", None)
            providers.antigravity_status(force=True)
    check("…and signed-out ⇒ not hireable, reason names the next step",
          disconnected_entry)

    def absent_entry():
        os.environ["ORGTREE_ANTIGRAVITY"] = os.path.join(
            os.environ["ORGTREE_DATA"], "nowhere", "agy.exe")
        providers.antigravity_status(force=True)
        p3 = providers.providers_payload({"installed": True})
        g3 = next(p for p in p3["providers"] if p["id"] == "google")
        eq((g3["hire_enabled"], g3["status"]["installed"],
            "not installed" in (g3["reason"] or "")),
           (False, False, True), "absent entry")
    check("…and not installed ⇒ the reason carries the install command",
          absent_entry)

    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
