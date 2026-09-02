"""OpenRouter — the API-backed provider lane's registry (openrouter.py).

    python backend/tests/test_openrouter.py      (no pytest; plain asserts)

Hermetic by construction: `openrouter._http_get` is replaced with a scripted
stand-in serving a FABRICATED catalog and key document — no request ever
leaves this process, and no real key exists anywhere in the rig. The one
invariant that matters most is negative and is checked first: nothing this
module SERVES ever carries the key.
"""

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-openrouter-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import openrouter as orr                                # noqa: E402

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
    except orr.OpenRouterError as e:
        if needle not in str(e):
            raise AssertionError(f"{what}: error said {e!r}, wanted {needle!r}")
        return
    raise AssertionError(f"{what}: no error raised")


# ── the scripted wire ──────────────────────────────────────────────────────
FAKE_KEY = "sk-or-v1-testkey-0123456789abcdef"

CATALOG = {"data": [
    {"id": "anthropic/claude-sonnet-5", "name": "Anthropic: Claude Sonnet 5",
     "context_length": 1000000,
     "architecture": {"output_modalities": ["text"]},
     "pricing": {"prompt": "0.000002", "completion": "0.00001",
                 "input_cache_read": "0.0000002"},
     "supported_parameters": ["tools", "temperature"]},
    {"id": "anthropic/claude-sonnet-5:batch", "name": "Anthropic: Claude Sonnet 5 (batch)",
     "context_length": 1000000, "pricing": {"prompt": "0.000001", "completion": "0.000005"}},
    {"id": "openai/gpt-5.6-sol", "name": "OpenAI: GPT-5.6 Sol",
     "context_length": 1050000,
     "pricing": {"prompt": "0.000005", "completion": "0.00003",
                 "input_cache_read": "0.0000005"},
     "supported_parameters": ["tools"]},
    {"id": "deepseek/deepseek-v4", "name": "DeepSeek: DeepSeek V4",
     "context_length": 163840,
     "pricing": {"prompt": "0.00000014", "completion": "0.00000028",
                 "input_cache_read": "0.00000002"},
     "supported_parameters": ["tools"]},
    {"id": "moonshotai/kimi-k3", "name": "MoonshotAI: Kimi K3",
     "context_length": 1048576,
     "pricing": {"prompt": "0.000003", "completion": "0.000015"},
     "supported_parameters": ["tools"]},
    {"id": "someone/llama-4-maverick:free", "name": "Someone: Llama 4 Maverick (free)",
     "context_length": 128000,
     "pricing": {"prompt": "0", "completion": "0"},
     "supported_parameters": []},
    {"id": "stability/sdxl", "name": "Stability: SDXL (image)",
     "architecture": {"output_modalities": ["image"]},
     "pricing": {"prompt": "0.00001", "completion": "0"}},
    {"id": "nobody/unpriced", "name": "Nobody: unpriced"},
    {"id": "anthropic/claude-fable-5.1", "name": "Anthropic: Claude Fable 5.1",
     "context_length": 1000000,
     "pricing": {"prompt": "0.00001", "completion": "0.00005",
                 "input_cache_read": "0.00000025"},
     "supported_parameters": ["tools"]},
]}

KEY_DOC = {"data": {"label": "orgtree desk", "limit": 50.0,
                    "limit_remaining": 37.25, "usage": 12.75,
                    "usage_daily": 1.5, "usage_weekly": 4.0,
                    "usage_monthly": 12.75, "is_free_tier": False,
                    "rate_limit": {"requests": 200, "interval": "10s"}}}

WIRE = {"catalog_status": 200, "key_status": 200, "calls": [], "fail": False}


def fake_get(url, headers):
    WIRE["calls"].append((url, dict(headers)))
    if WIRE["fail"]:
        raise OSError("simulated network failure")
    if url.endswith("/models"):
        return WIRE["catalog_status"], json.dumps(CATALOG).encode("utf-8")
    if url.endswith("/key"):
        auth = headers.get("Authorization", "")
        if auth != f"Bearer {FAKE_KEY}":
            return 401, b'{"error":{"message":"bad key"}}'
        return WIRE["key_status"], json.dumps(KEY_DOC).encode("utf-8")
    return 404, b""


orr._http_get = fake_get


def no_secret(obj, what):
    blob = json.dumps(obj)
    if FAKE_KEY in blob or "testkey" in blob:
        raise AssertionError(f"{what}: the key leaked into {blob[:120]}…")


def main():
    print("§1 the key: stored, never served")
    check("no key at first: key_set False, status says where to add one",
          lambda: (eq(orr.key_set(), False, "key_set"),
                   eq(orr.status()["installed"], False, "installed"),
                   eq("App settings" in str(orr.status()["reason"]), True, "reason")))
    raises(lambda: orr.set_key("nope"), "does not look like", "junk key refused")
    check("junk key refused with a written reason", lambda: None)
    orr.set_key(FAKE_KEY)
    check("set_key persists; key_set True; state file holds it",
          lambda: (eq(orr.key_set(), True, "key_set"),
                   eq(json.load(open(orr._state_path(), encoding="utf-8"))["key"],
                      FAKE_KEY, "on disk")))
    st = orr.status(force=True)
    check("status(): installed+connected, label + credits from /api/v1/key",
          lambda: (eq(st["installed"], True, "installed"),
                   eq(st["connected"], True, "connected"),
                   eq(st["label"], "orgtree desk", "label"),
                   eq(st["credits"]["limit_remaining"], 37.25, "remaining"),
                   eq(st["credits"]["usage_daily"], 1.5, "daily"),
                   eq(st["kind"], "api-key", "kind")))
    check("…and NOTHING served carries the key",
          lambda: (no_secret(st, "status"),
                   no_secret(orr.key_status(), "key_status"),
                   no_secret(orr.tier_infos(), "tier_infos")))
    check("the key check sent the Bearer header exactly once (60s cache)",
          lambda: (orr.status(), orr.status(),
                   eq(len([c for c in WIRE["calls"] if c[0].endswith("/key")]),
                      1, "key calls")))

    print("§2 the catalog, normalized")
    cards = orr.catalog(force=True)
    ids = [c["id"] for c in cards]
    check("batch variants, image-only and unpriced entries are dropped",
          lambda: (eq("anthropic/claude-sonnet-5:batch" in ids, False, "batch"),
                   eq("stability/sdxl" in ids, False, "image"),
                   eq("nobody/unpriced" in ids, False, "unpriced"),
                   eq(len(cards), 6, "kept")))
    sonnet = next(c for c in cards if c["id"] == "anthropic/claude-sonnet-5")
    check("prices are $ per MILLION tokens (catalog publishes per token)",
          lambda: (eq(sonnet["prompt"], 2.0, "prompt"),
                   eq(sonnet["completion"], 10.0, "completion"),
                   eq(sonnet["cache_read"], 0.2, "cache_read"),
                   eq(sonnet["context"], 1000000, "context"),
                   eq(sonnet["tools"], True, "tools"),
                   eq(sonnet["vendor"], "anthropic", "vendor")))
    check("the catalog is banked on disk and re-read without the network",
          lambda: (eq(os.path.exists(orr._catalog_path()), True, "file"),
                   WIRE["calls"].clear(),
                   orr._catalog_mem.update({"cards": None, "at": 0.0}),
                   orr.catalog(),
                   eq([c for c in WIRE["calls"] if c[0].endswith("/models")],
                      [], "no refetch while fresh")))

    print("§3 canonical letters and colors")
    check("letter = first letter of the last meaningful token",
          lambda: (eq(orr.letter_for("anthropic/claude-sonnet-5"), "S", "sonnet"),
                   eq(orr.letter_for("openai/gpt-5.6-sol"), "S", "sol"),
                   eq(orr.letter_for("google/gemini-3.5-flash"), "F", "flash"),
                   eq(orr.letter_for("deepseek/deepseek-v4"), "D", "deepseek"),
                   eq(orr.letter_for("moonshotai/kimi-k3"), "K", "kimi"),
                   eq(orr.letter_for("meta-llama/llama-4-maverick:free"), "M", "maverick"),
                   eq(orr.letter_for("google/gemini-3.1-pro-preview-customtools"),
                      "G", "gemini pro → G (pro/preview/customtools are qualifiers)")))
    c1 = orr.color_for("anthropic/claude-sonnet-5", 2.0)
    c2 = orr.color_for("anthropic/claude-sonnet-5", 2.0)
    c3 = orr.color_for("anthropic/claude-opus-5", 5.0)
    c4 = orr.color_for("unknown-vendor/whatever", 0.5)
    check("colors are deterministic hex, differ per model, exist for unknown vendors",
          lambda: (eq(c1, c2, "deterministic"),
                   eq(c1 != c3, True, "siblings differ"),
                   eq(bool(__import__("re").fullmatch(r"#[0-9a-f]{6}", c4)), True, "hex")))

    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    check("cheaper models are LIGHTER than expensive ones of the same vendor",
          lambda: eq(lum(orr.color_for("deepseek/deepseek-v4", 0.14))
                     > lum(orr.color_for("deepseek/deepseek-r9", 9.0)),
                     True, "lightness by price band"))

    print("§4 favorites become tiers")
    check("tier id = or- + slugified model id, clear of the static vocabulary",
          lambda: (eq(orr.tier_id("anthropic/claude-sonnet-5"),
                      "or-anthropic-claude-sonnet-5", "sonnet"),
                   eq(orr.tier_id("Some.Vendor/Model_X:free"),
                      "or-some-vendor-model-x-free", "punctuation"),
                   eq(orr.is_tier("or-x"), True, "prefix"),
                   eq(orr.is_tier("sol"), False, "static")))
    check("seat = floor($/M input), floored to 1 (the standing rule)",
          lambda: (eq(orr.seat_for(2.0), 2, "$2"), eq(orr.seat_for(1.5), 1, "$1.50"),
                   eq(orr.seat_for(0.2), 1, "$0.20"), eq(orr.seat_for(5.0), 5, "$5"),
                   eq(orr.seat_for(10.0), 10, "$10"), eq(orr.seat_for(0.0), 1, "free")))
    fav = orr.add_favorite("anthropic/claude-sonnet-5")
    check("add_favorite snapshots seat, prices, letter, color, tier",
          lambda: (eq(fav["tier"], "or-anthropic-claude-sonnet-5", "tier"),
                   eq(fav["seat"], 2, "seat"), eq(fav["letter"], "S", "letter"),
                   eq(fav["color"], c1, "color"), eq(fav["prompt"], 2.0, "prompt")))
    orr.add_favorite("deepseek/deepseek-v4")
    orr.add_favorite("anthropic/claude-sonnet-5")          # idempotent
    check("favorites de-duplicate; tiers()/models() are the dynamic tables",
          lambda: (eq(len(orr.favorites()), 2, "count"),
                   eq(orr.tiers(), {"or-anthropic-claude-sonnet-5": 2,
                                    "or-deepseek-deepseek-v4": 1}, "tiers"),
                   eq(orr.models()["or-deepseek-deepseek-v4"],
                      "deepseek/deepseek-v4", "models"),
                   eq(orr.contexts()["or-anthropic-claude-sonnet-5"], 1000000, "ctx")))
    raises(lambda: orr.add_favorite("nobody/unknown-model"), "not in the OpenRouter catalog",
           "unknown model refused")
    check("a model not in the catalog cannot be favorited", lambda: None)
    check("search pages the catalog, marks selected, clamps limit to 5–10",
          lambda: (eq(orr.search("claude")["total"], 2, "two claudes"),
                   eq(orr.search("claude")["items"][0]["selected"], True, "sonnet selected"),
                   eq(orr.search("claude fable")["items"][0]["id"],
                      "anthropic/claude-fable-5.1", "all terms"),
                   eq(orr.search("", limit=99)["limit"], 10, "clamp hi"),
                   eq(orr.search("", limit=1)["limit"], 5, "clamp lo"),
                   eq(len(orr.search("", offset=5)["items"]), 1, "offset")))
    check("remove_favorite drops the hire offer (True), twice is False",
          lambda: (eq(orr.remove_favorite("deepseek/deepseek-v4"), True, "first"),
                   eq(orr.remove_favorite("deepseek/deepseek-v4"), False, "again"),
                   eq(list(orr.tiers()), ["or-anthropic-claude-sonnet-5"], "left")))

    def fill():
        # user ruling 2026-09-02: NO cap — 20 more favorites all land, in the
        # user's own order, and the tier tables grow with them
        for i in range(20):
            mid = f"vendor{i}/model-{i}"
            CATALOG["data"].append({"id": mid, "name": mid, "context_length": 1,
                                    "pricing": {"prompt": "0.000001", "completion": "0.000001"}})
        orr.refresh_catalog()
        for i in range(20):
            orr.add_favorite(f"vendor{i}/model-{i}")
        eq(len(orr.favorites()), 21, "sonnet + 20")
        eq([f["id"] for f in orr.favorites()][1], "vendor0/model-0", "order kept")
        eq(len(orr.tiers()), 21, "tiers grew")
        for i in range(20):
            orr.remove_favorite(f"vendor{i}/model-{i}")
        del CATALOG["data"][-20:]
        orr.refresh_catalog()
    check("no favorites cap (user ruling): 20 more land, in order", fill)

    print("§5 cost fold and tier infos")
    check("cost() prices non-cached input, cached reads and output separately",
          lambda: eq(orr.cost("anthropic/claude-sonnet-5", 1_000_000, 1_000_000, 100_000),
                     round(2.0 + 0.2 + 1.0, 6), "sonnet turn"))
    check("tier_infos carries everything a hire surface draws, secret-free",
          lambda: (eq(orr.tier_infos()[0]["tier"], "or-anthropic-claude-sonnet-5", "tier"),
                   eq(orr.tier_infos()[0]["provider"], "openrouter", "provider"),
                   eq(sorted(orr.tier_infos()[0]),
                      sorted(["tier", "provider", "seat", "model", "letter", "color",
                              "name", "vendor", "prompt", "completion", "context"]), "keys"),
                   no_secret(orr.tier_infos(), "tier_infos")))

    print("§6 failure honesty")
    orr.set_key("sk-or-v1-wrongkey-000000000000")
    ks = orr.key_status(force=True)
    check("a rejected key reads connected=False with a replace-it reason",
          lambda: (eq(ks["connected"], False, "connected"),
                   eq("rejected" in str(ks["reason"]), True, "reason")))
    WIRE["fail"] = True
    ks2 = orr.key_status(force=True)
    check("a dead network reads connected=None (unknown), never False",
          lambda: (eq(ks2["connected"], None, "connected"),
                   eq("could not reach" in str(ks2["reason"]), True, "reason")))
    orr._catalog_mem.update({"cards": None, "at": 0.0})
    os.utime(orr._catalog_path(), (1, 1))                     # make the disk copy stale
    check("a stale disk catalog still serves when the network is down",
          lambda: eq(len(orr.catalog()) > 0, True, "stale fallback"))
    WIRE["fail"] = False
    orr.set_key("")
    check("clearing the key: key_set False, cached credits forgotten",
          lambda: (eq(orr.key_set(), False, "key_set"),
                   eq(orr.status()["connected"], False, "connected"),
                   eq(orr.status()["credits"]["limit_remaining"], None, "credits")))

    print(f"\nall {PASS} checks passed")


if __name__ == "__main__":
    main()
