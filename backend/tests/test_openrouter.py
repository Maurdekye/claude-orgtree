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
os.environ.pop("ORGTREE_WARM", None)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
# hermetic on the OTHER providers too: §7 renders the whole providers payload,
# which probes every CLI — pin them at paths that do not exist
os.environ["ORGTREE_CODEX"] = os.path.join(os.environ["ORGTREE_DATA"], "nowhere", "codex.exe")
os.environ["CODEX_HOME"] = os.path.join(os.environ["ORGTREE_DATA"], "chome")
os.environ["ORGTREE_ANTIGRAVITY"] = os.path.join(os.environ["ORGTREE_DATA"], "nowhere", "agy.exe")

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


def ne(got, unwanted, what):
    if got == unwanted:
        raise AssertionError(f"{what}: got {got!r}, wanted anything else")


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
    {"id": "anthropic/claude-sonnet-5", "created": 1750000000, "name": "Anthropic: Claude Sonnet 5",
     "context_length": 1000000,
     "architecture": {"output_modalities": ["text"]},
     "pricing": {"prompt": "0.000002", "completion": "0.00001",
                 "input_cache_read": "0.0000002"},
     "supported_parameters": ["tools", "temperature"]},
    {"id": "anthropic/claude-sonnet-5:batch", "created": 1750000000, "name": "Anthropic: Claude Sonnet 5 (batch)",
     "context_length": 1000000, "pricing": {"prompt": "0.000001", "completion": "0.000005"}},
    {"id": "openai/gpt-5.6-sol", "created": 1780000000, "name": "OpenAI: GPT-5.6 Sol",
     "context_length": 1050000,
     "pricing": {"prompt": "0.000005", "completion": "0.00003",
                 "input_cache_read": "0.0000005"},
     "supported_parameters": ["tools"]},
    {"id": "deepseek/deepseek-v4", "created": 1700000000, "name": "DeepSeek: DeepSeek V4",
     "context_length": 163840,
     "pricing": {"prompt": "0.00000014", "completion": "0.00000028",
                 "input_cache_read": "0.00000002"},
     "supported_parameters": ["tools"]},
    {"id": "moonshotai/kimi-k3", "created": 1788000000, "name": "MoonshotAI: Kimi K3",
     "context_length": 1048576,
     "pricing": {"prompt": "0.000003", "completion": "0.000015"},
     "supported_parameters": ["tools"]},
    {"id": "someone/llama-4-maverick:free", "created": 1720000000, "name": "Someone: Llama 4 Maverick (free)",
     "context_length": 128000,
     "pricing": {"prompt": "0", "completion": "0"},
     "supported_parameters": []},
    # ⚠ THE TIED PAIR. Every other fixture model has a distinct price, so a
    # sort with NO tiebreak would still come out deterministic and the
    # totality check below would be vacuous — measured: it passed against a
    # mutant with the tiebreak deleted. Two models at $0/$0 are what make a
    # tie exist to be broken.
    {"id": "someone/llama-4-scout:free", "created": 1725000000, "name": "Someone: Llama 4 Scout (free)",
     "context_length": 128000,
     "pricing": {"prompt": "0", "completion": "0"},
     "supported_parameters": []},
    {"id": "stability/sdxl", "created": 1710000000, "name": "Stability: SDXL (image)",
     "architecture": {"output_modalities": ["image"]},
     "pricing": {"prompt": "0.00001", "completion": "0"}},
    {"id": "nobody/unpriced", "created": 1710000000, "name": "Nobody: unpriced"},
    # ⚠ THE ONE MODEL WHOSE INPUT AND OUTPUT RANKS DISAGREE. Every other
    # fixture model prices output at a fixed multiple of input, so an
    # input-price sort and an output-price sort would produce the IDENTICAL
    # order and a test could not tell them apart — it would pass with the two
    # sorts wired to the same field. Cheap in, dear out breaks that.
    {"id": "cheapo/verbose-1", "created": 1730000000, "name": "Cheapo: Verbose 1",
     "context_length": 32768,
     "pricing": {"prompt": "0.0000005", "completion": "0.00004"},
     "supported_parameters": ["tools"]},
    {"id": "anthropic/claude-fable-5.1", "created": 1770000000, "name": "Anthropic: Claude Fable 5.1",
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
                   eq(len(cards), 8, "kept")))
    sonnet = next(c for c in cards if c["id"] == "anthropic/claude-sonnet-5")
    check("prices are $ per MILLION tokens (catalog publishes per token)",
          lambda: (eq(sonnet["prompt"], 2.0, "prompt"),
                   eq(sonnet["completion"], 10.0, "completion"),
                   eq(sonnet["cache_read"], 0.2, "cache_read"),
                   eq(sonnet["context"], 1000000, "context"),
                   eq(sonnet["tools"], True, "tools"),
                   eq(sonnet["vendor"], "anthropic", "vendor")))
    check("display names: no vendor namespace, no `Vendor: ` prefix, the variant suffix stays",
          lambda: (eq(sonnet["name"], "Claude Sonnet 5", "name"),
                   eq(sonnet["label"], "claude-sonnet-5", "label"),
                   eq(next(c for c in cards if c["id"] == "someone/llama-4-maverick:free")["label"],
                      "llama-4-maverick:free", "variant kept"),
                   eq(orr.pretty_name("Someone: Llama 4 Maverick (free)", "x/y"),
                      "Llama 4 Maverick (free)", "prefix off, parenthesis kept"),
                   eq(orr.pretty_name("Claude Opus 5", "anthropic/claude-opus-5"),
                      "Claude Opus 5", "no prefix → unchanged"),
                   eq(orr.pretty_name("", "openrouter/auto"), "auto", "empty → the label"),
                   eq(orr.model_label("openrouter/auto"), "auto", "namespace off"),
                   eq(orr.model_label("bare-id"), "bare-id", "no namespace → unchanged")))
    check("labels_for disambiguates a displayed SET: an equal pair keeps its full ids",
          lambda: (eq(orr.labels_for(["a/m-1", "b/m-2"]),
                      {"a/m-1": "m-1", "b/m-2": "m-2"}, "distinct"),
                   eq(orr.labels_for(["a/m-1", "b/m-1", "c/m-2"]),
                      {"a/m-1": "a/m-1", "b/m-1": "b/m-1", "c/m-2": "m-2"}, "collision")))
    check("the catalog is banked on disk and re-read without the network",
          lambda: (eq(os.path.exists(orr._catalog_path()), True, "file"),
                   WIRE["calls"].clear(),
                   orr._catalog_mem.update({"cards": None, "at": 0.0}),
                   orr.catalog(),
                   eq([c for c in WIRE["calls"] if c[0].endswith("/models")],
                      [], "no refetch while fresh")))

    print("§3 canonical letters and colors")
    check("letter = first letter of the FIRST word of the label (user ask 2026-09-03)",
          lambda: (eq(orr.letter_for("anthropic/claude-sonnet-5"), "C", "claude-sonnet → C"),
                   eq(orr.letter_for("anthropic/claude-opus-5"), "C", "claude-opus → C too"),
                   eq(orr.letter_for("openai/gpt-5.6-sol"), "G", "gpt → G"),
                   eq(orr.letter_for("google/gemini-3.5-flash"), "G", "gemini → G"),
                   eq(orr.letter_for("x-ai/grok-4.6"), "G", "grok → G"),
                   eq(orr.letter_for("deepseek/deepseek-v4"), "D", "deepseek"),
                   eq(orr.letter_for("moonshotai/kimi-k3"), "K", "kimi"),
                   eq(orr.letter_for("meta-llama/llama-4-maverick:free"), "L", "llama"),
                   eq(orr.letter_for("z-ai/glm-5.2:free"), "G", "variant suffix ignored"),
                   eq(orr.letter_for("bare"), "B", "no namespace"),
                   eq(orr.letter_for("vendor/"), "?", "nothing to read → ?")))
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

    def xai_black():
        # user ask 2026-09-03: "give xai models a black theme" — achromatic
        # near-blacks, darker than the panel they sit on, still banded by price
        grok = orr.color_for("x-ai/grok-4.6", 2.0)
        r, g, b = (int(grok[i:i + 2], 16) for i in (1, 3, 5))
        eq(r == g == b, True, f"achromatic ({grok})")
        eq(r < 0x20, True, f"darker than the panel #252526 ({grok})")
        eq(lum(orr.color_for("x-ai/grok-cheap", 0.5))
           > lum(orr.color_for("x-ai/grok-dear", 9.0)), True, "band axis kept")
        eq(orr.color_for("x-ai/grok-dear", 9.0) != "#000000", True,
           "never pure #000 — the deepest band is still a colour, not a hole")
        eq(orr.color_for("~x-ai/grok-latest", 2.0), grok, "the ~alias vendor is xAI too")
        eq(orr.color_for("openai/gpt-5.6-sol", 2.0) != grok, True, "nobody else went black")
    check("xAI models are near-black (the black theme), still banded, never a hole", xai_black)

    import math
    from itertools import combinations

    def wcag(h):  # WCAG relative luminance — the frontend's isDarkTierColor cut (< .03)
        def lin(c):
            c /= 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = (lin(int(h[i:i + 2], 16)) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def oklab(h):  # sRGB → OKLab, the inverse of orr._oklch_hex
        def lin(c):
            c /= 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = (lin(int(h[i:i + 2], 16)) for i in (1, 3, 5))
        l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
        m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
        s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
        return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
                1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
                0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)

    def hue(h):
        _, a, b = oklab(h)
        return math.degrees(math.atan2(b, a)) % 360

    def dark_trio():
        # brand-colors-2 (2026-09-03): MiniMax is a near-black navy (#181E25),
        # Z.AI's mark a neutral #2D2D2D — both dark, so both are FILLED like
        # the xAI black (under the frontend's luminance cut in every band),
        # and each carries an ACCENT for its rim so three dark vendors are
        # three chips; the xAI black carries none — black is its identity
        for mid, price in (("minimax/minimax-m3", 0.4), ("minimax/minimax-x", 9.0),
                           ("z-ai/glm-5.2:free", 0.0), ("z-ai/glm-dear", 9.0),
                           ("x-ai/grok-4.6", 2.0), ("x-ai/grok-cheap", 0.5)):
            c = orr.color_for(mid, price)
            eq(wcag(c) < 0.03, True, f"{mid} {c} is under the dark cut (wcag {wcag(c):.3f})")
        navy = orr.color_for("minimax/minimax-m3", 0.4)
        r, g, b = (int(navy[i:i + 2], 16) for i in (1, 3, 5))
        eq(b > r + 12, True, f"MiniMax is NAVY, not the xAI neutral ({navy})")
        eq(lum(navy) > lum(orr.color_for("minimax/minimax-x", 9.0)), True, "navy banded by price")
        glm = orr.color_for("z-ai/glm-5.2:free", 0.0)
        r, g, b = (int(glm[i:i + 2], 16) for i in (1, 3, 5))
        eq(r == g == b, True, f"Z.AI is the neutral grey of its mark ({glm})")
        eq(0x2a <= r <= 0x30, True, f"…the cheap band IS the mark, #2d2d2d ± ({glm})")
        eq(glm != orr.color_for("x-ai/grok-cheap", 0.5), True, "grey is not the xAI black")
        eq(orr.accent_for("minimax/minimax-m3"), "#ff5530", "MiniMax rim: its orange-red accent")
        eq(orr.accent_for("z-ai/glm-5.2:free"), "#00d4ff", "Z.AI rim: its site cyan (deliberate)")
        eq(orr.accent_for("~z-ai/glm-latest"), "#00d4ff", "the ~alias vendor too")
        eq(orr.accent_for("x-ai/grok-4.6"), None, "xAI: no accent, black is the identity")
        eq(orr.accent_for("anthropic/claude-sonnet-5"), None, "a light colour carries none")
        eq(orr.card_of({"id": "minimax/minimax-m3", "name": "MiniMax: M3",
                        "pricing": {"prompt": "0.0000004", "completion": "0.0000022"},
                        "context_length": 200000,
                        "supported_parameters": ["tools"]})["accent"],  # type: ignore[index]
           "#ff5530", "card_of serves the accent beside the colour")
    check("the dark trio: MiniMax navy + orange rim, Z.AI grey + cyan rim, xAI black + none",
          dark_trio)

    def brand_hues():
        # brand-colors-2 (2026-09-03): the minted hue lands on the brand slot
        # (deepest band — the most chroma, the cleanest hue; ±nudge ±rounding)
        for mid, lo, hi, why in (
                ("nvidia/nemotron-x", 122, 138, "Nvidia green #76B900"),
                ("mistralai/mistral-large", 46, 62, "Mistral: orange-red ramp, NOT blue"),
                ("perplexity/sonar-pro", 202, 218, "Perplexity True Turquoise #20808D"),
                ("qwen/qwen4-plus", 280, 296, "Qwen violet #615CED, NOT Alibaba orange"),
                ("ibm-granite/granite-4", 296, 312, "IBM: Carbon Purple-60, off the Gemini blue"),
                ("reka/reka-flash", 220, 236, "Reka cyan #00BFFF")):
            c = orr.color_for(mid, 9.0)
            eq(lo <= hue(c) <= hi, True, f"{why}: {c} at {hue(c):.0f}°")
        # two orange-family vendors, ≥ 10° apart in every band: Claude's
        # terracotta (37°) and Mistral (its ramp's orange step) never merge
        for price in (0.5, 2.0, 5.0, 9.0):
            d = abs(hue(orr.color_for("mistralai/codestral", price))
                    - hue(orr.color_for("anthropic/claude-sonnet-5", price)))
            eq(d >= 10, True, f"Claude C vs Codestral C at ${price}: {d:.0f}° apart")
    check("brand hues: Nvidia green, Mistral orange, Perplexity teal, Qwen violet, IBM purple, "
          "Reka cyan — and Mistral clear of Claude's terracotta", brand_hues)

    def blue_cluster():
        # six brands 1–4° apart plus Google and Reka: spread in BRAND ORDER
        # over the family, every neighbour ≥ 8°, the family's two ends far
        # apart in every band, no two vendors the same hex in any band. What
        # this does NOT promise: adjacent vendors distinguishable by colour
        # alone — the letters (R L K N G C D Q G) carry that; see _VENDOR_HUE.
        order = [("reka", "reka/reka-flash"), ("meta-llama", "meta-llama/llama-4-maverick"),
                 ("moonshotai", "moonshotai/kimi-k3"), ("amazon", "amazon/nova-pro"),
                 ("google", "google/gemini-3.5-pro"), ("cohere", "cohere/command-a"),
                 ("deepseek", "deepseek/deepseek-v4"), ("qwen", "qwen/qwen4-plus"),
                 ("ibm-granite", "ibm-granite/granite-4")]
        for price in (0.5, 2.0, 5.0, 9.0):
            cols = [(v, orr.color_for(m, price)) for v, m in order]
            eq(len({c for _, c in cols}), len(cols), f"no two vendors share a hex at ${price}")
            hues = [hue(c) for _, c in cols]
            eq(hues == sorted(hues), True, f"brand order kept at ${price}: {[round(h) for h in hues]}")
            # table slots are ≥ 8° apart (asserted below); the ±2° model nudge
            # and 8-bit rounding at the pale band's low chroma eat up to ~5°
            gaps = [b - a for a, b in zip(hues, hues[1:])]
            eq(min(gaps) >= 2.5, True, f"neighbours apart at ${price} (min {min(gaps):.1f}°)")
            ends = math.dist(oklab(cols[0][1]), oklab(cols[-1][1]))
            eq(ends >= 0.10, True, f"cyan end vs violet end at ${price}: ΔE {ends:.3f}")
        # in the deep band every pair of the nine is at least just-noticeable
        deep = {v: oklab(orr.color_for(m, 9.0)) for v, m in order}
        worst = min((math.dist(deep[a], deep[b]), a, b) for a, b in combinations(deep, 2))
        eq(worst[0] >= 0.015, True, f"closest deep-band pair {worst[1]}/{worst[2]}: ΔE {worst[0]:.3f}")
    check("the blue cluster: spread in brand order, neighbours apart, ends far apart, "
          "no shared hex; the letters do the rest", blue_cluster)

    check("placeholders never sit on a researched hue: every table hue ≥ 8° from every other",
          lambda: eq(min(abs(a - b) if abs(a - b) <= 180 else 360 - abs(a - b)
                         for (ka, a), (kb, b) in combinations(orr._VENDOR_HUE.items(), 2)
                         if ka != kb and {ka, kb} != {"ibm", "ibm-granite"}) >= 8,
                     True, "min hue gap in _VENDOR_HUE"))

    print("§4 favorites become tiers")
    check("tier id = or- + slugified model id, clear of the static vocabulary",
          lambda: (eq(orr.tier_id("anthropic/claude-sonnet-5"),
                      "or-anthropic-claude-sonnet-5", "sonnet"),
                   eq(orr.tier_id("Some.Vendor/Model_X:free"),
                      "or-some-vendor-model-x-free", "punctuation"),
                   eq(orr.is_tier("or-x"), True, "prefix"),
                   eq(orr.is_tier("sol"), False, "static")))
    # seat_for, user ruling 2026-09-03: floor(p) at or above $1/M, and
    # max(0.10, round(p, 2)) below it. The $1.00 BOUNDARY is the whole point —
    # everything at or above it keeps the number it had before this change, so
    # no existing tier is re-priced and no saved org can become overdrawn.
    check("seat at/above $1/M is unchanged: floor($/M input)",
          lambda: (eq(orr.seat_for(2.0), 2, "$2"), eq(orr.seat_for(1.5), 1, "$1.50"),
                   eq(orr.seat_for(5.0), 5, "$5"), eq(orr.seat_for(10.0), 10, "$10"),
                   eq(orr.seat_for(1.0), 1, "$1.00 exactly — the boundary"),
                   eq(orr.seat_for(1.99), 1, "$1.99 floors, never rounds up")))
    check("seat below $1/M is FRACTIONAL — the ranking the old floor destroyed",
          lambda: (eq(orr.seat_for(0.99), 0.99, "$0.99 — just under the boundary"),
                   eq(orr.seat_for(0.75), 0.75, "$0.75 gemini-3.8-flash"),
                   eq(orr.seat_for(0.2), 0.2, "$0.20 gpt-reserve/luna"),
                   eq(orr.seat_for(0.6), 0.6, "$0.60"),
                   eq(orr.seat_for(0.123), 0.12, "quantised to the 0.01 grid"),
                   ne(orr.seat_for(0.2), orr.seat_for(0.6),
                      "two cheap models no longer collapse to one seat")))
    # ⚠ the floor is load-bearing: a $0 `:free` model priced at seat 0 would
    # bound NO concurrency (free() never decreases when you hire one) while
    # still spawning a real OS process. See openrouter.seat_for's docstring.
    check("a $0 :free model seats at the 0.10 FLOOR, never at zero",
          lambda: (eq(orr.seat_for(0.0), 0.10, "free"),
                   eq(orr.seat_for(0.02), 0.10, "$0.02 — under the floor"),
                   eq(orr.seat_for(0.05), 0.10, "$0.05 — under the floor"),
                   eq(orr.seat_for(0.10), 0.10, "$0.10 — exactly the floor"),
                   eq(orr.seat_for(0.11), 0.11, "$0.11 — just above it")))
    fav = orr.add_favorite("anthropic/claude-sonnet-5")
    check("add_favorite snapshots seat, prices, letter, color, tier",
          lambda: (eq(fav["tier"], "or-anthropic-claude-sonnet-5", "tier"),
                   eq(fav["seat"], 2, "seat"), eq(fav["letter"], "C", "letter"),
                   eq(fav["color"], c1, "color"), eq(fav["prompt"], 2.0, "prompt")))
    check("a favorite carries its label; search pages and tier_label answer the same",
          lambda: (eq(fav["label"], "claude-sonnet-5", "label"),
                   eq(orr.search("claude")["items"][0]["label"], "claude-sonnet-5", "page"),
                   eq(orr.tier_label("or-anthropic-claude-sonnet-5"), "claude-sonnet-5", "tier"),
                   eq(orr.tier_label("or-gone-model", {"or-gone-model": "gone/model:free"}),
                      "model:free", "deselected → the org doc's own table"),
                   eq(orr.tier_label("or-gone-model"), "gone-model", "unknown → bare slug"),
                   eq(orr.tier_label("sonnet"), "sonnet", "static passthrough")))
    def old_rule_record():
        # a favorite written under the LAST-word rule carries letter "S";
        # the read path recomputes from the id, so it reads "C" untouched
        doc = orr._load_state()
        doc["favorites"][0]["letter"] = "S"
        doc["favorites"][0]["name"] = "Anthropic: Claude Sonnet 5"
        orr._save_state(doc)
        eq(orr.favorites()[0]["letter"], "C", "recomputed on read")
        eq(orr.favorites()[0]["name"], "Claude Sonnet 5", "prefix stripped on read")
    check("a favorite stored under an older rule reads under the current one", old_rule_record)
    orr.add_favorite("deepseek/deepseek-v4")
    orr.add_favorite("anthropic/claude-sonnet-5")          # idempotent
    check("favorites de-duplicate; tiers()/models() are the dynamic tables",
          lambda: (eq(len(orr.favorites()), 2, "count"),
                   # deepseek-v4 is $0.14/M — under the old floor-to-1 rule it
                   # seated at 1, indistinguishable from every other cheap
                   # model. That collapse is the defect this change fixed.
                   eq(orr.tiers(), {"or-anthropic-claude-sonnet-5": 2,
                                    "or-deepseek-deepseek-v4": 0.14}, "tiers"),
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
                   # the clamp is a PAYLOAD bound now, not the retired "5–10
                   # at a time" spec (user ask 2026-09-04, page size raised)
                   eq(orr.search("", limit=9999)["limit"], orr.PAGE_MAX, "clamp hi"),
                   eq(orr.search("", limit=1)["limit"], orr.PAGE_MIN, "clamp lo"),
                   eq(orr.search("")["limit"], orr.PAGE_DEFAULT, "default"),
                   eq(len(orr.search("", offset=5)["items"]), 3, "offset")))
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

    def collide():
        # two favorites that would READ the same keep their full ids, on the
        # hire surfaces and on the picker page alike — and read short again
        # the moment one of them goes
        CATALOG["data"].append({"id": "other/claude-sonnet-5", "name": "Other: Claude Sonnet 5",
                                "context_length": 1,
                                "pricing": {"prompt": "0.000001", "completion": "0.000001"}})
        orr.refresh_catalog()
        orr.add_favorite("other/claude-sonnet-5")
        labels = {f["id"]: f["label"] for f in orr.favorites()}
        eq(labels["anthropic/claude-sonnet-5"], "anthropic/claude-sonnet-5", "full id")
        eq(labels["other/claude-sonnet-5"], "other/claude-sonnet-5", "both sides")
        eq([i["label"] for i in orr.search("sonnet")["items"]],
           ["anthropic/claude-sonnet-5", "other/claude-sonnet-5"], "the page too")
        orr.remove_favorite("other/claude-sonnet-5")
        del CATALOG["data"][-1]
        orr.refresh_catalog()
        eq(orr.favorites()[0]["label"], "claude-sonnet-5", "short again once alone")
    check("two favorites that would read the same keep their full ids", collide)

    print("§4b the sub-$1 rule reaches favorites adopted BEFORE it")
    # ⚠ THE SEAT IS SNAPSHOTTED AT ADD TIME (`add_favorite`), so the 2026-09-03
    # fractional ruling reached NO favorite already on disk — every OpenRouter
    # model under $1/M stayed frozen at the old `max(1, floor(p))` value of 1.
    # Measured on the live machine 2026-09-04: the one favorite there read
    # `prompt: 0.05, seat: 1` — the record carried the right PRICE and the
    # wrong SEAT side by side. This is the source-of-truth half of the fix;
    # the org-document half is in test_ledger_authority §4.
    check("legacy_seat_for is the OLD rule, kept executable to identify defaults",
          lambda: (eq(orr.legacy_seat_for(0.05), 1, "$0.05 floored to 1"),
                   eq(orr.legacy_seat_for(0.0), 1, "even a free model"),
                   eq(orr.legacy_seat_for(2.0), 2, "$2 — same as today"),
                   eq(orr.legacy_seat_for(1.5), 1, "$1.50 — same as today")))
    # the two rules agree EVERYWHERE at or above $1/M, which is what makes
    # "value == 1 and priced under $1" a complete and safe staleness test
    check("old and new rules differ ONLY below $1/M, and there old is always 1",
          lambda: [eq(orr.legacy_seat_for(p) == orr.seat_for(p), p >= 1.0,
                      f"agreement at ${p}")
                   for p in (0.0, 0.05, 0.2, 0.75, 0.99, 1.0, 1.5, 2.0, 9.9)]
          and eq({orr.legacy_seat_for(p) for p in (0.0, 0.05, 0.5, 0.99)}, {1.0},
                 "old rule collapses every cheap model to one value"))

    orr.add_favorite("deepseek/deepseek-v4")            # $0.14/M — a cheap one

    def stale_record():
        # write the live machine's exact shape: right price, old-rule seat
        doc = orr._load_state()
        for f in doc["favorites"]:
            f["seat"] = orr.legacy_seat_for(float(f["prompt"]))
        orr._save_state(doc)
        got = {f["id"]: f["seat"] for f in orr.favorites()}
        eq(got["deepseek/deepseek-v4"], 0.14, "$0.14 re-derived from the record")
        eq(got["anthropic/claude-sonnet-5"], 2, "$2 was never stale")
        # ⚠ POSITIVE CONTROL. The assertion above is only worth something if
        # the stale value could have survived to be seen, so prove the fixture
        # really did write a 1 and that the raw record still says so — the
        # correction is on the READ path, not a rewrite of state.json.
        raw = {f["id"]: f["seat"] for f in orr._load_state()["favorites"]}
        eq(raw["deepseek/deepseek-v4"], 1, "the stored record is still the old 1")
        ne(raw["deepseek/deepseek-v4"], got["deepseek/deepseek-v4"],
           "so the read path is what corrected it")
    check("a favorite stored under the OLD floor rule reads at its fractional "
          "seat — re-derived from the record's own snapshot price", stale_record)

    def not_a_customisation():
        doc = orr._load_state()
        for f in doc["favorites"]:
            if f["id"] == "deepseek/deepseek-v4":
                f["seat"] = 0.5              # neither the old rule nor the new
        orr._save_state(doc)
        eq({f["id"]: f["seat"] for f in orr.favorites()}["deepseek/deepseek-v4"],
           0.5, "a hand-set seat is left exactly alone")
    check("…but a seat the old rule would NOT have produced is a customisation "
          "and stays", not_a_customisation)

    def drift_proof():
        # the seat is re-derived from the RECORD's price, never the catalog's,
        # so a list-price move cannot silently re-price a committed seat — the
        # property add_favorite snapshots for, and the reason this is a rule
        # migration rather than a live lookup
        doc = orr._load_state()
        for f in doc["favorites"]:
            if f["id"] == "deepseek/deepseek-v4":
                f["seat"], f["prompt"] = 1, 0.14
        orr._save_state(doc)
        was = CATALOG["data"][3]["pricing"]["prompt"]
        CATALOG["data"][3]["pricing"]["prompt"] = "0.000009"   # $9/M overnight
        orr.refresh_catalog()
        try:
            eq({f["id"]: f["seat"] for f in orr.favorites()}["deepseek/deepseek-v4"],
               0.14, "still the snapshot price, not the new $9")
        finally:
            CATALOG["data"][3]["pricing"]["prompt"] = was
            orr.refresh_catalog()
    check("the correction reads the SNAPSHOT price, so catalog drift cannot "
          "re-price a committed seat", drift_proof)

    def migration_map():
        # `stale_seats` is what ledger.Org drives on load. It is handed a
        # document's OWN tables, so it is general: no tier is named here or
        # in the ledger, and a favorite minted tomorrow needs no new code.
        tiers = {"haiku": 1, "sonnet": 2,                  # static — never touched
                 "or-deepseek-deepseek-v4": 1,             # $0.14 — STALE
                 "or-anthropic-claude-sonnet-5": 2,        # $2 — already right
                 "or-openai-gpt-5-6-sol": 1,               # $5 at 1 — HAND-SET
                 "or-deepseek-deepseek-v4-cust": 0.5,      # operator's own
                 "or-unknown-model": 1}                    # no price anywhere
        models = {"or-deepseek-deepseek-v4": "deepseek/deepseek-v4",
                  "or-anthropic-claude-sonnet-5": "anthropic/claude-sonnet-5",
                  "or-openai-gpt-5-6-sol": "openai/gpt-5.6-sol",
                  "or-deepseek-deepseek-v4-cust": "deepseek/deepseek-v4",
                  "or-unknown-model": "nobody/nothing"}
        eq(orr.stale_seats(tiers, models), {"or-deepseek-deepseek-v4": 0.14},
           "exactly one row moves")
        # anti-vacuity: the same call over an already-migrated table is empty,
        # which is both idempotence and the zero-I/O steady state
        eq(orr.stale_seats({**tiers, "or-deepseek-deepseek-v4": 0.14}, models), {},
           "a migrated table has no candidates left")
    check("stale_seats moves the stale row and NOTHING else — not the static "
          "tiers, not a correct one, not a custom one, not an unpriced one",
          migration_map)
    # ⚠ the case above that nearly shipped wrong: `or-openai-gpt-5-6-sol` sits
    # at 1 while its model costs $5/M. The OLD rule floored $5 to 5, so that 1
    # was never a shipped default — it is a hand-set price and must survive.
    # A prefilter of "value == 1" alone would have dragged it to 5, RAISING a
    # seat, which is the one direction that can overdraw a saved org.
    check("a row at 1 whose model prices ABOVE $1/M is a hand-set price, not "
          "a stale default — it is never raised",
          lambda: eq(orr.stale_seats({"or-openai-gpt-5-6-sol": 1},
                                     {"or-openai-gpt-5-6-sol": "openai/gpt-5.6-sol"}),
                     {}, "left alone"))

    def deselected_still_priced():
        # a DESELECTED tier keeps its org row (a node hired on it still runs)
        # but has no favorite record, so its price can only come from the
        # catalog file ON DISK — never a fetch, which has no business inside a
        # document load. `gpt-5.6-sol` above proves the disk lookup runs; this
        # proves it can actually MOVE a row, not merely decline to.
        tier = "or-someone-llama-4-maverick-free"
        eq(orr.favorite_for_tier(tier), None, "not a favorite")
        eq(orr.stale_seats({tier: 1}, {tier: "someone/llama-4-maverick:free"}),
           {tier: 0.10}, "a $0 :free model lands on the FLOOR, never zero")
    check("a DESELECTED tier is repriced too, from the on-disk catalog",
          deselected_still_priced)

    print("§4c the picker's sorts and provider grouping")
    # user spec 2026-09-04: "sorting options (recency of release, input price,
    # output price), and a simple additional checkbox for 'group by provider'
    # that's perpendicular to that dropdown". Server-side by necessity: the
    # page is 8 rows of 426, so a client sort would reorder a page.
    ids = lambda **kw: [i["id"] for i in orr.search("", limit=10, **kw)["items"]]
    prices = lambda sort, **kw: [i["prompt" if sort == "input" else "completion"]
                                 for i in orr.search("", limit=10, sort=sort,
                                                     **kw)["items"]]
    check("the default is unchanged — no sort argument means the old ranking",
          lambda: (eq(orr.search("")["sort"], "relevance", "sort"),
                   eq(ids(), ids(sort="relevance"), "same rows, same order")))
    check("sort by INPUT price, cheap first, and reversed",
          lambda: (eq(ids(sort="input"),
                      ["someone/llama-4-maverick:free", "someone/llama-4-scout:free",
                       "deepseek/deepseek-v4", "cheapo/verbose-1",
                       "anthropic/claude-sonnet-5",
                       "moonshotai/kimi-k3", "openai/gpt-5.6-sol",
                       "anthropic/claude-fable-5.1"], "asc"),
                   eq(prices(sort="input", order="desc"),
                      sorted(prices(sort="input"), reverse=True), "desc")))
    # ⚠ `desc` REVERSES THE PRICE, NOT THE WHOLE LIST. The id tiebreak stays
    # ascending in both directions, so the two $0/$0 models keep their
    # alphabetical order when the price direction flips — a secondary sort
    # that inverted with the primary would make the cheap end of the list
    # reshuffle for no reason the user asked for. Both orders are still
    # total, which is the property paging actually needs.
    check("…and reversing the price does NOT reverse the tiebreak",
          lambda: (eq(ids(sort="input")[:2],
                      ["someone/llama-4-maverick:free", "someone/llama-4-scout:free"],
                      "id-ascending at the cheap end"),
                   eq(ids(sort="input", order="desc")[-2:],
                      ["someone/llama-4-maverick:free", "someone/llama-4-scout:free"],
                      "…and still id-ascending when the price flips")))
    # ⚠ THE CHECK THAT CATCHES BOTH SORTS BEING WIRED TO ONE FIELD. Every
    # other fixture model prices output at a fixed multiple of input, so these
    # two orders would be identical but for `cheapo/verbose-1` ($0.50 in,
    # $40 out) — cheap on input, near the top; dear on output, near the bottom.
    check("sort by OUTPUT price is a DIFFERENT order, not the input sort again",
          lambda: (eq(ids(sort="output"),
                      ["someone/llama-4-maverick:free", "someone/llama-4-scout:free",
                       "deepseek/deepseek-v4",
                       "anthropic/claude-sonnet-5", "moonshotai/kimi-k3",
                       "openai/gpt-5.6-sol", "cheapo/verbose-1",
                       "anthropic/claude-fable-5.1"], "asc"),
                   ne(ids(sort="output"), ids(sort="input"),
                      "the two price sorts genuinely disagree")))
    check("sort by RECENCY reads the catalog's `created` stamp, newest first "
          "by default, oldest first reversed",
          lambda: (eq(ids(sort="recency")[0], "moonshotai/kimi-k3", "newest"),
                   eq(ids(sort="recency")[-1], "deepseek/deepseek-v4", "oldest"),
                   eq(ids(sort="recency", order="asc"),
                      list(reversed(ids(sort="recency"))), "asc mirrors desc")))
    # …and recency is NOT the catalog's arrival order wearing a costume: the
    # fixture is deliberately stamped so the two disagree. On the LIVE catalog
    # they happen to coincide (measured: 0 inversions in 425 pairs), which is
    # exactly the coincidence that would hide a bug here.
    check("recency sorts on the DATE, not on catalog arrival order",
          lambda: ne(ids(sort="recency"), ids(sort="relevance"),
                     "the fixture's date order differs from its arrival order"))
    check("each sort picks its own useful direction when none is given",
          lambda: (eq(orr.search("", sort="input")["order"], "asc", "cheapest"),
                   eq(orr.search("", sort="output")["order"], "asc", "cheapest"),
                   eq(orr.search("", sort="recency")["order"], "desc", "newest"),
                   eq(orr.search("", sort="relevance")["order"], "asc",
                      "relevance has no direction to offer")))
    check("an unknown sort or order falls back instead of erroring — an "
          "ordering preference is not worth failing a catalog read over",
          lambda: (eq(orr.search("", sort="bogus")["sort"], "relevance", "sort"),
                   eq(orr.search("", sort="input", order="sideways")["order"],
                      "asc", "order")))
    # ⚠ TOTALITY. A tie under a paged sort is how a row appears on two pages
    # while another appears on none. Both free-ish models below sit at the
    # same $0 output price; the id tiebreak is what keeps the order total.
    def total_order():
        page = orr.search("", limit=10, sort="output")
        seen = [i["id"] for i in page["items"]]
        eq(len(seen), len(set(seen)), "no duplicate row")
        eq(len(seen), page["total"], "every model placed exactly once")
        # paging the same sort must partition the list, never overlap it
        a = [i["id"] for i in orr.search("", offset=0, limit=5, sort="output")["items"]]
        b = [i["id"] for i in orr.search("", offset=5, limit=5, sort="output")["items"]]
        eq(set(a) & set(b), set(), "pages do not overlap")
        eq(a + b, seen, "and together they are the whole sorted list")
    check("a sorted list is TOTALLY ordered — pages partition it, never "
          "overlap or drop a row", total_order)

    def tie_is_broken():
        # ⚠ THIS IS THE CHECK THAT COST A MUTANT TO GET RIGHT. The version
        # above passes with the id tiebreak DELETED, because `sorted` is
        # stable and one process sees one catalog order — so it proves
        # nothing about ties. What actually threatens a paged sort is the
        # catalog ARRIVING in a different order between two page requests
        # (a refresh, a failover to the disk copy): tied rows then shuffle,
        # and a row lands on two pages while another lands on none.
        before = [i["id"] for i in orr.search("", limit=10, sort="output")["items"]]
        CATALOG["data"].reverse()
        try:
            orr.refresh_catalog()
            after = [i["id"] for i in orr.search("", limit=10, sort="output")["items"]]
        finally:
            CATALOG["data"].reverse()
            orr.refresh_catalog()
        eq(after, before, "the sorted order does not depend on arrival order")
        eq(before.index("someone/llama-4-maverick:free")
           < before.index("someone/llama-4-scout:free"), True,
           "the $0/$0 pair is ordered by id, the same way every time")
    check("…and a TIE is broken by id, so the order survives the catalog "
          "arriving in a different order between two pages", tie_is_broken)
    check("an explicit sort reports that it DISPLACED relevance ranking, but "
          "only when there was a query to rank",
          lambda: (eq(orr.search("claude", sort="input")["relevance_displaced"],
                      True, "sorted with a query"),
                   eq(orr.search("claude")["relevance_displaced"], False,
                      "relevance itself displaces nothing"),
                   eq(orr.search("", sort="input")["relevance_displaced"], False,
                      "an empty box has no relevance to lose")))

    def grouping():
        # perpendicular: vendor becomes the PRIMARY key, the chosen sort stays
        # the secondary one — it re-groups the same ordering, never replaces it
        got = ids(sort="input", group_by_vendor=True)
        vendors = [i.split("/")[0] for i in got]
        eq(vendors, sorted(vendors, key=vendors.index), "each vendor is contiguous")
        # anthropic's two models keep the INPUT order inside the group
        anth = [i for i in got if i.startswith("anthropic/")]
        eq(anth, ["anthropic/claude-sonnet-5", "anthropic/claude-fable-5.1"],
           "sonnet $2 before fable $10 — the sort survives inside the group")
        # groups march in the sort's own direction: the vendor holding the
        # cheapest model leads, not the alphabetically first one
        eq(vendors[0], "someone", "cheapest model's vendor leads, not 'anthropic'")
        eq(ids(sort="input", group_by_vendor=True) != ids(sort="input"), True,
           "grouping actually changed the order")
    check("group by provider is PERPENDICULAR to the sort: vendors contiguous, "
          "chosen sort preserved within each, groups ordered by the sort",
          grouping)
    check("grouped paging reports the previous page's vendor so a split "
          "heading can say 'continued'",
          lambda: (eq(orr.search("", offset=0, limit=5,
                                 group_by_vendor=True)["prev_vendor"], None,
                      "first page has nothing before it"),
                   ne(orr.search("", offset=5, limit=5,
                                 group_by_vendor=True)["prev_vendor"], None,
                      "a later page names the row above it")))

    print("§4d paging at the raised page size, and group headings across it")
    # user ask 2026-09-04: "increase the results per page, and compress their
    # height so more can be fit onto the same page at once". The height is
    # visual and untestable here; the ARITHMETIC is not, and a page-size
    # change is exactly where an off-by-one hides.
    #
    # ⚠ THE FIXTURE IS TOO SMALL TO CATCH ONE ON ITS OWN. Eight catalog models
    # under a 25-row page means every page is the only page, so every boundary
    # check below would pass vacuously. This section GROWS the catalog past a
    # full page first — that is the whole reason it exists separately.
    def paging_boundaries():
        base = len(orr.catalog())
        # 60 models over 3 vendors, 20 each, so vendor edges and page edges
        # fall at different places and cannot mask one another
        for v in range(3):
            for i in range(20):
                CATALOG["data"].append({
                    "id": f"bulk{v}/model-{i:02d}", "name": f"Bulk{v}: model {i:02d}",
                    "created": 1700000000 + v * 1000 + i,
                    "context_length": 1000,
                    "pricing": {"prompt": f"0.0000{v+1}{i:02d}",
                                "completion": "0.00001"},
                    "supported_parameters": ["tools"]})
        try:
            orr.refresh_catalog()
            total = base + 60
            p0 = orr.search("", offset=0)
            eq(p0["total"], total, "every model counted")
            eq(p0["limit"], orr.PAGE_DEFAULT, "the raised page size is in force")
            eq(len(p0["items"]), orr.PAGE_DEFAULT, "a full page is actually full")
            # walk EVERY page and demand the union is the whole list, in order,
            # with no row seen twice and none skipped at a boundary
            walked, off = [], 0
            while off < total:
                page = orr.search("", offset=off, sort="input")
                eq(len(page["items"]) > 0, True, f"page at {off} is not empty")
                walked += [i["id"] for i in page["items"]]
                off += orr.PAGE_DEFAULT
            whole = [i["id"] for i in
                     orr.search("", offset=0, limit=orr.PAGE_MAX, sort="input")["items"]]
            eq(len(walked), total, "paging visited every row exactly once")
            eq(len(set(walked)), total, "…and none of them twice")
            eq(walked, whole, "…in the same order a single big page gives")
            # the last page is the remainder, not a padded full page
            last = (total // orr.PAGE_DEFAULT) * orr.PAGE_DEFAULT
            eq(len(orr.search("", offset=last)["items"]),
               total - last, "the last page holds the remainder")
            eq(orr.search("", offset=total)["items"], [], "past the end is empty")
        finally:
            del CATALOG["data"][-60:]
            orr.refresh_catalog()
    check("paging at the raised page size visits every row exactly once — no "
          "row dropped or repeated at a boundary", paging_boundaries)

    def continued_marker():
        # `prev_vendor` is what lets a split group heading say "continued", and
        # it is read from `hits[offset - 1]` — an index one off the page, which
        # is the classic place to be off by one.
        for i in range(30):
            CATALOG["data"].append({
                "id": f"solo{i:02d}/only", "name": f"Solo{i:02d}: only",
                "created": 1700000000 + i, "context_length": 1000,
                "pricing": {"prompt": "0.000002", "completion": "0.00001"},
                "supported_parameters": ["tools"]})
        try:
            orr.refresh_catalog()
            g = lambda off: orr.search("", offset=off, group_by_vendor=True)
            eq(g(0)["prev_vendor"], None, "the first page has nothing above it")
            second = g(orr.PAGE_DEFAULT)
            eq(second["prev_vendor"] is not None, True,
               "a later page names the row above it")
            # and it really is the row above: the last row of the page before
            first_page = g(0)["items"]
            eq(second["prev_vendor"], first_page[-1]["vendor"],
               "prev_vendor is exactly the previous page's last vendor")
            # every `solo*` vendor holds ONE model, so a boundary between two
            # of them is a group edge, NOT a continuation — the marker must be
            # able to say "no" as well as "yes"
            eq(second["prev_vendor"] != second["items"][0]["vendor"], True,
               "a boundary that falls between groups is not a continuation")
            eq(orr.search("", offset=9999, group_by_vendor=True)["prev_vendor"],
               None, "far past the end reports nothing rather than indexing")
        finally:
            del CATALOG["data"][-30:]
            orr.refresh_catalog()
    check("the 'continued' marker reads the row above the page, and says NO "
          "when the boundary falls between two groups", continued_marker)

    def continuation_really_happens():
        # ⚠ POSITIVE CONTROL for the check above. Single-model vendors can only
        # ever prove the NEGATIVE case; if no group is longer than a page, a
        # broken continuation marker would never be exercised. One vendor with
        # more models than a page guarantees a genuine split.
        for i in range(orr.PAGE_DEFAULT + 5):
            CATALOG["data"].append({
                "id": f"huge/model-{i:02d}", "name": f"Huge: model {i:02d}",
                "created": 1690000000 + i, "context_length": 1000,
                "pricing": {"prompt": "0.0000001", "completion": "0.0000001"},
                "supported_parameters": ["tools"]})
        try:
            orr.refresh_catalog()
            # cheapest input puts the whole `huge` block first, so it certainly
            # straddles the first page boundary
            p1 = orr.search("", offset=orr.PAGE_DEFAULT, sort="input",
                            group_by_vendor=True)
            eq(p1["prev_vendor"], "huge", "the page before ended inside huge")
            eq(p1["items"][0]["vendor"], "huge", "and this page starts inside it")
            eq(p1["prev_vendor"] == p1["items"][0]["vendor"], True,
               "so the heading is drawn as a CONTINUATION, not a fresh group")
        finally:
            del CATALOG["data"][-(orr.PAGE_DEFAULT + 5):]
            orr.refresh_catalog()
    check("…and a group longer than one page really does continue across the "
          "boundary — the positive case, not just the negative one",
          continuation_really_happens)

    print("§5 cost fold and tier infos")
    check("cost() prices non-cached input, cached reads and output separately",
          lambda: eq(orr.cost("anthropic/claude-sonnet-5", 1_000_000, 1_000_000, 100_000),
                     round(2.0 + 0.2 + 1.0, 6), "sonnet turn"))
    check("tier_infos carries everything a hire surface draws, secret-free",
          lambda: (eq(orr.tier_infos()[0]["tier"], "or-anthropic-claude-sonnet-5", "tier"),
                   eq(orr.tier_infos()[0]["provider"], "openrouter", "provider"),
                   eq(sorted(orr.tier_infos()[0]),
                      sorted(["tier", "provider", "seat", "model", "letter", "color",
                              "accent", "name", "label", "vendor", "prompt", "completion",
                              "context"]),
                      "keys"),
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

    print("§7 the doors: API endpoints, providers payload, hire gate, ledger, MCP, spawn env")
    from fastapi.testclient import TestClient                          # noqa: PLC0415
    from orgtree import api, cachecontinuity, mcptool, providers, supervisor  # noqa: PLC0415
    from orgtree.ledger import LedgerError, Org                        # noqa: PLC0415
    client = TestClient(api.app)

    def refused(fn, needle, what):
        """the gate speaks LedgerError (both API layers 422 it)"""
        try:
            fn()
        except LedgerError as e:
            if needle not in str(e):
                raise AssertionError(f"{what}: refusal said {e!r}, wanted {needle!r}")
            return
        raise AssertionError(f"{what}: the gate did not refuse")
    orr._catalog_mem.update({"cards": None, "at": 0.0})
    for f in orr.favorites():
        orr.remove_favorite(f["id"])
    r0 = client.get("/api/openrouter").json()
    check("GET /api/openrouter without a key: key_set False, a written reason",
          lambda: (eq(r0["key_set"], False, "key_set"),
                   eq("App settings" in str(r0["reason"]), True, "reason"),
                   eq(r0["tiers"], [], "no tiers")))
    r1 = client.put("/api/openrouter/key", json={"key": FAKE_KEY})
    check("PUT /api/openrouter/key stores it; the reply is connected and SECRET-FREE",
          lambda: (eq(r1.status_code, 200, "status"),
                   eq(r1.json()["key_set"], True, "key_set"),
                   eq(r1.json()["connected"], True, "connected"),
                   eq(r1.json()["label"], "orgtree desk", "label"),
                   no_secret(r1.json(), "key reply")))
    check("a junk key is refused with 422",
          lambda: eq(client.put("/api/openrouter/key", json={"key": "x"}).status_code,
                     422, "422"))
    page = client.get("/api/openrouter/models", params={"q": "claude", "limit": 5}).json()
    check("GET /api/openrouter/models pages the catalog for the picker",
          lambda: (eq(page["total"], 2, "two claudes"), eq(page["limit"], 5, "limit"),
                   eq(page["items"][0]["selected"], False, "unselected")))
    # ⚠ the ordering controls have to survive the WIRE, not just the function:
    # a query param that never reaches `search` leaves the picker with a
    # dropdown that does nothing, and every unit check above would still pass.
    srt = client.get("/api/openrouter/models",
                     params={"q": "", "limit": 10, "sort": "input",
                             "order": "desc", "group_by_vendor": "true"}).json()
    check("…and the sort / order / group query params reach it over HTTP",
          lambda: (eq(srt["sort"], "input", "sort"), eq(srt["order"], "desc", "order"),
                   eq(srt["group_by_vendor"], True, "grouped"),
                   eq(srt["items"][0]["id"], "anthropic/claude-fable-5.1",
                      "dearest input first, and its vendor leads the groups"),
                   eq("created" in srt["items"][0], True,
                      "the recency field is on the wire for the row to show")))
    r2 = client.put("/api/openrouter/favorites",
                    json={"id": "anthropic/claude-sonnet-5", "selected": True}).json()
    TIER = "or-anthropic-claude-sonnet-5"
    check("PUT /api/openrouter/favorites selects a model → a tier row with letter+color",
          lambda: (eq([t["tier"] for t in r2["tiers"]], [TIER], "tiers"),
                   eq(r2["tiers"][0]["seat"], 2, "seat"),
                   eq(r2["tiers"][0]["letter"], "C", "letter"),
                   eq(r2["tiers"][0]["color"].startswith("#"), True, "color")))
    check("…an unknown model is refused with 422",
          lambda: eq(client.put("/api/openrouter/favorites",
                                json={"id": "nobody/nope", "selected": True}).status_code,
                     422, "422"))
    pay = client.get("/api/providers").json()
    entry = next(p for p in pay["providers"] if p["id"] == "openrouter")
    check("/api/providers carries the openrouter entry: hireable, one tier, secret-free",
          lambda: (eq(entry["label"], "OpenRouter", "label"),
                   eq(entry["hire_enabled"], True, "hire_enabled"),
                   eq(entry["reason"], None, "reason"),
                   eq([t["tier"] for t in entry["tiers"]], [TIER], "tiers"),
                   eq(entry["status"]["installed"], True, "installed"),
                   no_secret(pay, "providers payload")))
    check("the provider axis: provider_of / label / install hint",
          lambda: (eq(providers.provider_of(TIER), "openrouter", "provider_of"),
                   eq(providers.provider_label(TIER), "OpenRouter", "label"),
                   eq("App settings" in providers.install_hint("openrouter"), True, "hint"),
                   eq(providers.provider_of("sonnet"), "claude", "claude unchanged")))
    org = Org.create("orr-org", dirs=[], permission_mode="acceptEdits")
    check("a NEW org doc carries the dynamic tier at its snapshot seat (ledger merge)",
          lambda: (eq(org.d["tiers"][TIER], 2, "seat"),
                   eq(org.d["models"][TIER], "anthropic/claude-sonnet-5", "model"),
                   eq(org.tree()["models"][TIER], "anthropic/claude-sonnet-5",
                      "the tree payload carries the table (the UI's label source)")))
    check("hire gate: the favorite passes; a stranger or-* tier and a kiosk are refused",
          lambda: (api.provider_hire_gate(org, TIER),
                   refused(lambda: api.provider_hire_gate(org, "or-nobody-nope"),
                           "not among the OpenRouter favorites", "stranger"),
                   refused(lambda: api.provider_hire_gate(org, "or-nobody-nope"),
                           "tier 'nobody-nope'", "the refusal names the display label"),
                   refused(lambda: api.provider_hire_gate(
                       Org({**org.d, "kiosk": {"max_tier": "fable"}}), TIER),
                           "kiosk", "kiosk holdout")))
    check("…a HEADLESS org may hire it (a key is a keyed login)",
          lambda: api.provider_hire_gate(Org({**org.d, "headless": True}), TIER))
    check("…plain rehire (user_choice_only) skips the connect checks",
          lambda: api.provider_hire_gate(org, "or-nobody-nope", user_choice_only=True))
    cards = {c["name"]: c for c in mcptool.available_tools()}
    check("MCP hire/switch schemas accept the runtime favorite without changing",
          lambda: (eq("enum" not in cards["orgtree_hire"]["inputSchema"]["properties"]["tier"],
                      True, "hire accepts runtime id"),
                   eq("enum" not in cards["orgtree_switch_model"]["inputSchema"]["properties"]["tier"],
                      True, "switch accepts runtime id"),
                   eq(TIER in json.dumps(mcptool.TOOLS), False,
                      "favorite is transient discovery, not schema")))
    env = supervisor.spawn_env(org, tier=TIER)
    check("spawn_env for an or-* tier: the cookbook recipe, one credential, no account lane",
          lambda: (eq(env.get("ANTHROPIC_BASE_URL"), orr.ANTHROPIC_BASE, "base"),
                   eq(env.get("ANTHROPIC_AUTH_TOKEN"), FAKE_KEY, "token"),
                   eq(env.get("ANTHROPIC_API_KEY"), "", "api key EMPTY"),
                   eq("CLAUDE_CODE_OAUTH_TOKEN" in env, False, "no account token")))
    env_c = supervisor.spawn_env(org, tier="sonnet")
    check("…and a Claude tier's env carries NONE of it (lane exclusivity)",
          lambda: (eq("ANTHROPIC_BASE_URL" in env_c, False, "no base"),
                   eq("ANTHROPIC_AUTH_TOKEN" in env_c, False, "no token")))
    check("identity_in_env reads the lane as 'openrouter', never the primary login",
          lambda: (eq(supervisor.identity_in_env(env), "openrouter", "identity"),
                   eq(supervisor.identity_in_env(env_c) == "openrouter", False, "claude")))
    ns, lane = supervisor._cache_claude_namespace(org, TIER, env, 0.0)
    check("cache namespace: openrouter-key:<digest> on the api_key lane, 5-minute TTL",
          lambda: (eq(ns.startswith("openrouter-key:"), True, "namespace"),
                   eq(FAKE_KEY in ns, False, "digest, not the key"),
                   eq(lane, "api_key", "lane"),
                   eq(cachecontinuity.ttl_seconds("openrouter", "api_key"), 300, "ttl")))
    check("tier_context reads the favorite's catalog window for an or-* tier",
          lambda: eq(supervisor.tier_context(TIER), 1000000, "context"))
    r3 = client.delete("/api/openrouter/key").json()
    check("DELETE /api/openrouter/key: gone, and the gate now names the missing key",
          lambda: (eq(r3["key_set"], False, "key_set"),
                   refused(lambda: api.provider_hire_gate(org, TIER),
                           "no OpenRouter API key", "gate"),
                   eq(supervisor.identity_in_env(env_c), "primary", "claude lane intact")))

    def spawn_without_key():
        try:
            supervisor.spawn_env(org, tier=TIER)
        except RuntimeError as e:
            assert "no OpenRouter API key" in str(e), e
            return
        raise AssertionError("spawn_env silently fell through to the subscription")
    check("…and spawn_env for the or-* tier fails LOUDLY instead of billing the subscription",
          spawn_without_key)

    print(f"\nall {PASS} checks passed")


if __name__ == "__main__":
    main()
