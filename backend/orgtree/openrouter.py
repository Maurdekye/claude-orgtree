# pyright: strict
"""OpenRouter — the first API-BACKED provider lane (user go-ahead 2026-09-02).

Every provider before this one was a CONNECTED LOCAL CLI: orgtree detected a
binary, read connect-state off the CLI's own auth store, and spawned the CLI
per turn. OpenRouter is none of that — it is a REST gateway
(https://openrouter.ai/api/v1, one Bearer API key) in front of ~425 models from
~70 upstream vendors, usage-billed against a prepaid credit balance. So this
module owns what a CLI would otherwise have owned:

  · the KEY (App settings → Providers is the only door; `key_set` is the
    "installed" fact and a cached `GET /api/v1/key` the "connected" one),
  · the CATALOG (`GET /api/v1/models`, cached on disk under
    <data>/openrouter/, searched server-side for the picker modal),
  · the FAVORITES (the user's curated list — user spec 2026-09-02: "those are
    the models that have tokens that can be hired"), and
  · the DYNAMIC TIERS the favorites become: tier id `or-<slugified model id>`,
    seat = the repo's standing rule (API $ per M INPUT tokens, floored to 1 —
    ledger.py §3.1), letter + color derived CANONICALLY from the model id so
    ~425 models never need a hand-picked palette.

⚠ SECRET HYGIENE. The key is stored in <data>/openrouter/state.json and NEVER
returned by any reader here: `status()` answers `key_set`, the /api/v1/key
LABEL and the credit figures, nothing else. The key reaches a child process
only through the execution lane's spawn env (supervisor), one credential per
spawn like every other lane.

⚠ NO NETWORK FROM IMPORT. Fetches happen lazily behind `_http_get`, which
tests replace (see backend/tests/test_openrouter.py) — the catalog rig is a
fabricated document, never openrouter.ai.

Tier ids are one flat vocabulary with every other provider's (providers.py):
the `or-` prefix is what keeps a favorite's id clear of fable/sol/flash and
lets `providers.provider_of` answer "openrouter" for it without a registry
lookup.

WHAT THE USER SEES IS NOT THE ID (user ask 2026-09-03: "remove the or- and
provider name prefix from openrouter model names"). Every surface prints the
`label` — the model id after its vendor namespace (`claude-sonnet-5`), the
`:free`-style variant suffix kept — and the `name` without its `Vendor: `
prefix (`Claude Sonnet 5`). The tier id and the full model id stay exactly
what they were everywhere they are stored, keyed or sent upstream; see
`model_label`, `pretty_name`, `labels_for` and `tier_label`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, Final, TypedDict, cast

from . import store

API_BASE: Final = "https://openrouter.ai/api/v1"
#: the Anthropic-compatible base Claude Code is pointed at (its own cookbook:
#: `ANTHROPIC_BASE_URL=https://openrouter.ai/api`; the CLI appends /v1/messages)
ANTHROPIC_BASE: Final = "https://openrouter.ai/api"
TIER_PREFIX: Final = "or-"
STATE_FILE: Final = "state.json"
CATALOG_FILE: Final = "models.json"
STATE_VERSION: Final = 1
#: how long a downloaded catalog is trusted before a refetch is attempted; a
#: failed refetch falls back to the stale copy rather than an empty picker
CATALOG_TTL: Final = 3600.0
#: /api/v1/key is polled by the accounts panel; the same 60s cache the CLI
#: status probes use
KEY_TTL: Final = 60.0
HTTP_TIMEOUT: Final = 20.0
#: user ruling 2026-09-02 (ask card): NO cap on favorites — 0 means unlimited
#: (the family row wraps; the picker's selected strip grows). Kept as a knob
#: so a cap can be re-introduced without touching the add path.
FAVORITES_MAX: Final = 0
PROVIDER_ID: Final = "openrouter"
PROVIDER_LABEL: Final = "OpenRouter"

_LOCK = threading.RLock()


class OpenRouterError(RuntimeError):
    """A catalog or key request could not be satisfied (with a written why)."""


class ModelCard(TypedDict):
    """One catalog entry as the picker and the registry need it — prices in
    $ per MILLION tokens (the catalog publishes $ per token)."""
    id: str
    #: the display name without its `Vendor: ` prefix (`Claude Sonnet 5`)
    name: str
    #: the display id without its vendor namespace (`claude-sonnet-5`) — what
    #: every surface prints where it used to print the id or the tier
    label: str
    vendor: str
    prompt: float
    completion: float
    cache_read: float
    #: $/M for writing a prompt-cache entry — published for the vendors that
    #: charge it (Anthropic 1.25× input); absent ones fall back to the input
    #: price, which is what every automatic-caching vendor charges
    cache_write: float
    context: int
    tools: bool
    free: bool
    letter: str
    color: str


class Favorite(ModelCard):
    tier: str
    seat: int
    added_at: str


# ── paths ──────────────────────────────────────────────────────────────────

def _dir() -> str:
    """Resolved per call: tests replace store.DATA_ROOT after import."""
    return os.path.join(store.DATA_ROOT, "openrouter")


def _state_path() -> str:
    return os.path.join(_dir(), STATE_FILE)


def _catalog_path() -> str:
    return os.path.join(_dir(), CATALOG_FILE)


def _atomic_write(target: str, blob: bytes) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(20):
            try:
                os.replace(tmp, target)
                tmp = ""
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


# ── state: key + favorites ─────────────────────────────────────────────────

def _blank_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "key": "", "favorites": []}


#: the ledger's org-doc migration merges `tiers()`/`models()` on EVERY
#: `load_org`, which runs per API call — so the state file is read behind a
#: short mtime-keyed cache (the env_overrides precedent, ~2s), not per call.
_state_cache: dict[str, Any] = {"at": 0.0, "mtime": None, "path": "", "doc": None}
_STATE_TTL: Final = 2.0


def _load_state() -> dict[str, Any]:
    with _LOCK:
        p = _state_path()
        now = time.time()
        cached = _state_cache.get("doc")
        if (cached is not None and _state_cache["path"] == p
                and now - float(_state_cache["at"]) < _STATE_TTL):
            return json.loads(json.dumps(cached))
        try:
            mtime: float | None = os.stat(p).st_mtime
        except OSError:
            mtime = None
        if cached is not None and _state_cache["path"] == p \
                and mtime == _state_cache["mtime"]:
            _state_cache["at"] = now
            return json.loads(json.dumps(cached))
        raw: Any = None
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            raw = None
        out = _blank_state()
        doc: dict[str, Any] = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        if doc.get("version") == STATE_VERSION:
            key = doc.get("key")
            out["key"] = key if isinstance(key, str) else ""
            favs = doc.get("favorites")
            out["favorites"] = [
                cast("dict[str, Any]", f)
                for f in cast("list[Any]", favs) if isinstance(f, dict)] \
                if isinstance(favs, list) else []
        _state_cache.update({"at": now, "mtime": mtime, "path": p,
                             "doc": json.loads(json.dumps(out))})
        return out


def _save_state(doc: dict[str, Any]) -> None:
    doc["version"] = STATE_VERSION
    _atomic_write(_state_path(),
                  json.dumps(doc, indent=2).encode("utf-8"))
    with _LOCK:
        _state_cache["doc"] = None


def _key() -> str:
    """The credential, for the SPAWN SEAM ONLY (supervisor injects it into
    one child's env). Never serialize its return value."""
    return str(_load_state().get("key") or "")


def key_set() -> bool:
    return bool(_key())


def set_key(key: str) -> None:
    """Store (or, with an empty string, clear) the machine-wide key. Clearing
    also drops the cached /api/v1/key verdict so the panel cannot keep
    showing the old key's credits."""
    global _key_cache
    k = (key or "").strip()
    if k and not re.fullmatch(r"[A-Za-z0-9_\-\.]{8,512}", k):
        raise OpenRouterError("that does not look like an OpenRouter API key")
    with _LOCK:
        doc = _load_state()
        doc["key"] = k
        _save_state(doc)
        _key_cache = None


# ── the catalog ────────────────────────────────────────────────────────────

def _default_http_get(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return int(r.status), r.read()
    except urllib.error.HTTPError as e:
        return int(e.code), e.read() if e.fp else b""


#: the ONE network door — tests replace it with a scripted stand-in
_http_get = _default_http_get


def _ua_headers() -> dict[str, str]:
    return {"User-Agent": "orgtree (+https://github.com/neoja/claude-orgtree)",
            "X-OpenRouter-Title": "orgtree"}


def vendor_of(model_id: str) -> str:
    return model_id.split("/", 1)[0] if "/" in model_id else "openrouter"


# ── display names (2026-09-03) ─────────────────────────────────────────────
#
# DISPLAY ONLY. Nothing below is ever stored as a key, compared to a catalog
# id, or sent to openrouter.ai: the full `vendor/model[:variant]` id stays the
# identity everywhere, these are what the user reads.

def model_label(model_id: str) -> str:
    """The id after its vendor namespace: `anthropic/claude-sonnet-5` →
    `claude-sonnet-5`, `z-ai/glm-5.2:free` → `glm-5.2:free`. The variant
    suffix is KEPT: it is a different model at a different price, and
    dropping it would fold 74 pairs of the live catalog's 425 rows into one
    name each (measured 2026-09-03), where keeping it folds none."""
    return model_id.split("/", 1)[1] if "/" in model_id else model_id


#: the catalog's `Vendor: ` name prefix — one short run before the first
#: colon, never a parenthesised qualifier (`Claude Sonnet 5 (batch)` keeps
#: its parenthesis, `SpaceXAI: Grok 4.6` loses its vendor)
_NAME_PREFIX: Final = re.compile(r"^[^:()]{1,40}:\s+")


def pretty_name(name: str, model_id: str) -> str:
    """The catalog's display name without its vendor prefix: `Anthropic:
    Claude Sonnet 5` → `Claude Sonnet 5`. Measured on the live catalog
    2026-09-03: 398 of 425 names carry the prefix, it is constant per vendor,
    and stripping it leaves no two names equal. An empty name falls back to
    the label so the picker never shows a blank row."""
    n = (name or "").strip()
    if not n:
        return model_label(model_id)
    return _NAME_PREFIX.sub("", n, count=1) or n


def labels_for(ids: Iterable[str]) -> dict[str, str]:
    """Labels for one DISPLAYED SET of model ids. Each gets its short label
    unless another id in the SAME set would read the same — then both keep
    their full id, so the user is never shown two identical rows. The live
    catalog has no such pair today (0 of 425 with the variant suffix kept);
    a set is still disambiguated against itself, never against a catalog
    that may not be loaded (favorites are read on every org load, offline
    or not)."""
    ids = list(ids)
    short = {i: model_label(i) for i in ids}
    counts = Counter(short.values())
    return {i: (i if counts[s] > 1 else s) for i, s in short.items()}


def tier_id(model_id: str) -> str:
    """`or-` + the model id with every non-alphanumeric run folded to `-`
    (`anthropic/claude-sonnet-5` → `or-anthropic-claude-sonnet-5`). Safe as a
    CSS class fragment, a URL segment and a JSON key, and always clear of the
    static tier vocabulary by its prefix."""
    return TIER_PREFIX + re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")


def is_tier(tier: str) -> bool:
    return tier.startswith(TIER_PREFIX)


def seat_for(prompt_per_m: float) -> int:
    """The standing seat rule (ledger.py §3.1, re-affirmed 2026-08-28): API $
    per M INPUT tokens, floored to 1 — sol $5 → 5, terra $2 → 2, flash $1.50
    → 1, a $0.20 model → 1."""
    return max(1, int(math.floor(prompt_per_m + 1e-9)))


def letter_for(model_id: str) -> str:
    """The chip glyph: the first letter of the FIRST word of the model's
    display label (user ask 2026-09-03, verbatim: "base the model card icon
    letter off the first word (ignoring the or and provider name that we are
    dropping) not the last word"). claude-sonnet-5 → C · gpt-5.6-sol → G ·
    gemini-3.5-flash → G · grok-4.6 → G · deepseek-v4 → D · kimi-k3 → K ·
    llama-4-maverick → L · glm-5.2:free → G.

    ⚠ THE LETTER IS A FAMILY GLYPH NOW, NOT A DISCRIMINATOR. The first cut
    keyed off the last meaningful token, so sonnet/opus/haiku read S/O/H;
    this rule reads every claude-* as C and gpt-*, grok-*, gemini-* and glm-*
    all as G. The COLOR — the vendor hue, or the xAI black — is what tells
    such cards apart; no tie-breaker is applied, by instruction."""
    label = model_label(model_id).split(":", 1)[0]
    toks = [t for t in re.split(r"[-_./ ]+", label.lower()) if t]
    return (toks[0][:1].upper() if toks else "") or "?"


#: canonical hue per vendor (degrees, OKLCH). Chosen to sit near the desk
#: themes the app already uses for the CLI providers (--prov-claude terracotta,
#: --prov-openai teal, --prov-google blue-violet) and to spread the big open
#: vendors. Anyone missing hashes to a hue instead — the table is a courtesy,
#: not a requirement.
_VENDOR_HUE: Final[dict[str, int]] = {
    "anthropic": 40, "openai": 175, "google": 262, "meta-llama": 225,
    "mistralai": 25, "deepseek": 240, "x-ai": 305, "qwen": 285,
    "moonshotai": 200, "cohere": 330, "amazon": 55, "microsoft": 205,
    "nvidia": 135, "perplexity": 190, "minimax": 350, "z-ai": 268,
    "xiaomi": 15, "tencent": 212, "baidu": 245, "bytedance": 320,
    "ai21": 65, "inception": 95, "nousresearch": 105, "liquid": 160,
    "openrouter": 0,
}


def _hash01(s: str) -> float:
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


def _oklch_hex(light: float, chroma: float, h_deg: float) -> str:
    """OKLCH → sRGB hex, reducing chroma until the color is in gamut."""
    h = math.radians(h_deg)
    for _ in range(24):
        a, b = chroma * math.cos(h), chroma * math.sin(h)
        l_ = light + 0.3963377774 * a + 0.2158037573 * b
        m_ = light - 0.1055613458 * a - 0.0638541728 * b
        s_ = light - 0.0894841775 * a - 1.2914855480 * b
        l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
        r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
        g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
        bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
        if all(-1e-4 <= c <= 1 + 1e-4 for c in (r, g, bl)):
            def gam(c: float) -> int:
                c = min(1.0, max(0.0, c))
                v = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
                return int(round(v * 255))
            return "#%02x%02x%02x" % (gam(r), gam(g), gam(bl))
        chroma *= 0.88
    return "#808080"


def color_for(model_id: str, prompt_per_m: float) -> str:
    """The canonical chip color (recommended scheme, ask card 2026-09-02):
    hue from the VENDOR (table above, hash fallback) nudged ±12° per model so
    siblings differ; LIGHTNESS from the price band — cheap models pale,
    expensive ones deep — the same lightness axis flash/pro and luna/sol
    already use, and the one axis colour-vision deficiency preserves."""
    vendor = vendor_of(model_id)
    base = _VENDOR_HUE.get(vendor)
    if base is None:
        base = int(_hash01("vendor:" + vendor) * 360)
    hue = (base + (_hash01("model:" + model_id) * 24 - 12)) % 360
    if prompt_per_m < 1.0:
        light, chroma = 0.86, 0.10
    elif prompt_per_m < 3.0:
        light, chroma = 0.76, 0.13
    elif prompt_per_m < 8.0:
        light, chroma = 0.66, 0.16
    else:
        light, chroma = 0.56, 0.17
    return _oklch_hex(light, chroma, hue)


def _per_m(v: Any) -> float:
    try:
        return round(float(v) * 1_000_000, 6)
    except (TypeError, ValueError):
        return 0.0


def card_of(m: dict[str, Any]) -> ModelCard | None:
    """Normalize one raw catalog entry; None for entries the harnesses cannot
    run (batch variants, non-text output, unpriced)."""
    mid = str(m.get("id") or "")
    if not mid or mid.endswith(":batch"):
        return None
    arch_raw = m.get("architecture")
    arch: dict[str, Any] = (cast("dict[str, Any]", arch_raw)
                            if isinstance(arch_raw, dict) else {})
    outs_raw = arch.get("output_modalities")
    outs = cast("list[Any]", outs_raw) if isinstance(outs_raw, list) else []
    if outs and "text" not in outs:
        return None
    pricing_raw = m.get("pricing")
    pricing: dict[str, Any] = (cast("dict[str, Any]", pricing_raw)
                               if isinstance(pricing_raw, dict) else {})
    if not pricing:
        return None
    prompt = _per_m(pricing.get("prompt"))
    completion = _per_m(pricing.get("completion"))
    cache_read = _per_m(pricing.get("input_cache_read"))
    cache_write = (_per_m(pricing.get("input_cache_write"))
                   if pricing.get("input_cache_write") is not None else prompt)
    params = m.get("supported_parameters")
    tools = isinstance(params, list) and "tools" in params
    try:
        ctx = int(m.get("context_length") or 0)
    except (TypeError, ValueError):
        ctx = 0
    return {
        "id": mid,
        "name": pretty_name(str(m.get("name") or ""), mid),
        "label": model_label(mid),
        "vendor": vendor_of(mid),
        "prompt": prompt,
        "completion": completion,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "context": ctx,
        "tools": bool(tools),
        "free": mid.endswith(":free") or (prompt == 0 and completion == 0),
        "letter": letter_for(mid),
        "color": color_for(mid, prompt),
    }


_catalog_mem: dict[str, Any] = {"at": 0.0, "cards": None}


def _read_catalog_file() -> tuple[list[dict[str, Any]], float] | None:
    p = _catalog_path()
    raw: Any = None
    try:
        st = os.stat(p)
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return _rows_of(raw), st.st_mtime


def _rows_of(raw: Any) -> list[dict[str, Any]]:
    """The catalog document's `data` rows, or [] for any other shape."""
    doc: dict[str, Any] = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    data = doc.get("data")
    if not isinstance(data, list):
        return []
    return [cast("dict[str, Any]", d) for d in cast("list[Any]", data)
            if isinstance(d, dict)]


def refresh_catalog() -> list[ModelCard]:
    """Fetch the live catalog and bank it on disk. Raises OpenRouterError
    with the HTTP status or transport error spelled out."""
    try:
        status, body = _http_get(f"{API_BASE}/models", _ua_headers())
    except OSError as e:
        raise OpenRouterError(f"could not reach openrouter.ai: {e}") from None
    if status != 200:
        raise OpenRouterError(f"openrouter.ai answered {status} for the "
                              "model catalog")
    raw: Any = None
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OpenRouterError("the model catalog was not JSON") from None
    if not (isinstance(raw, dict)
            and isinstance(cast("dict[str, Any]", raw).get("data"), list)):
        raise OpenRouterError("the model catalog had no `data` list")
    data = _rows_of(raw)
    _atomic_write(_catalog_path(), json.dumps({"data": data}).encode("utf-8"))
    cards = [c for c in (card_of(d) for d in data) if c]
    with _LOCK:
        _catalog_mem["at"] = time.time()
        _catalog_mem["cards"] = cards
    return cards


def catalog(force: bool = False) -> list[ModelCard]:
    """The normalized catalog: memory → fresh disk copy → network → STALE
    disk copy (better a day-old picker than an empty one) → error."""
    now = time.time()
    with _LOCK:
        cards = _catalog_mem.get("cards")
        if not force and cards is not None and now - float(_catalog_mem["at"]) < CATALOG_TTL:
            return list(cards)
    on_disk = _read_catalog_file()
    if not force and on_disk and now - on_disk[1] < CATALOG_TTL:
        cards = [c for c in (card_of(d) for d in on_disk[0]) if c]
        with _LOCK:
            _catalog_mem["at"] = on_disk[1]
            _catalog_mem["cards"] = cards
        return list(cards)
    try:
        return refresh_catalog()
    except OpenRouterError:
        if on_disk:
            cards = [c for c in (card_of(d) for d in on_disk[0]) if c]
            with _LOCK:
                _catalog_mem["at"] = now      # do not hammer a dead network
                _catalog_mem["cards"] = cards
            return list(cards)
        raise


def search(q: str, offset: int = 0, limit: int = 8) -> dict[str, Any]:
    """The picker's page: every catalog card whose id or name contains EVERY
    whitespace-separated term of `q` (case-insensitive), id matches ranked
    before name matches, then the catalog's own order. `limit` is clamped to
    the user's 5–10 spec."""
    terms = [t for t in q.lower().split() if t]
    limit = max(5, min(10, int(limit or 8)))
    offset = max(0, int(offset or 0))
    cards = catalog()
    hits: list[tuple[int, int, ModelCard]] = []
    for i, c in enumerate(cards):
        hay_id = c["id"].lower()
        hay_name = c["name"].lower()
        if all(t in hay_id or t in hay_name for t in terms):
            rank = 0 if all(t in hay_id for t in terms) else 1
            hits.append((rank, i, c))
    hits.sort(key=lambda h: (h[0], h[1]))
    fav_ids = {f["id"] for f in favorites()}
    # labels are disambiguated across the WHOLE result set, so a model reads
    # the same on every page of one query
    labels = labels_for(c["id"] for _, _, c in hits)
    page = [dict(c, selected=c["id"] in fav_ids, label=labels[c["id"]])
            for _, _, c in hits[offset:offset + limit]]
    return {"query": q, "offset": offset, "limit": limit,
            "total": len(hits), "items": page}


def find(model_id: str) -> ModelCard | None:
    for c in catalog():
        if c["id"] == model_id:
            return c
    return None


# ── favorites → tiers ──────────────────────────────────────────────────────

def favorites() -> list[Favorite]:
    out: list[Favorite] = []
    for f in _load_state()["favorites"]:
        try:
            mid = str(f["id"])
            out.append({
                "id": mid, "tier": str(f["tier"]),
                # the display forms are DERIVED on every read, never trusted
                # from the record: a favorite added before 2026-09-03 stored
                # `Anthropic: Claude Sonnet 5` and no label at all
                "name": pretty_name(str(f.get("name") or ""), mid),
                "label": model_label(mid),
                "vendor": str(f.get("vendor") or vendor_of(mid)),
                "prompt": float(f.get("prompt") or 0.0),
                "completion": float(f.get("completion") or 0.0),
                "cache_read": float(f.get("cache_read") or 0.0),
                "cache_write": float(f.get("cache_write")
                                     or f.get("prompt") or 0.0),
                "context": int(f.get("context") or 0),
                "tools": bool(f.get("tools", True)),
                "free": bool(f.get("free", False)),
                # letter and color are CANONICAL — a pure function of the id
                # and the snapshot price — so they are recomputed on every
                # read: a rule change (the first-word letter, 2026-09-03)
                # reaches favorites added under the old rule without a
                # migration. The record's own copies are for older readers.
                "letter": letter_for(mid),
                "color": color_for(mid, float(f.get("prompt") or 0.0)),
                "seat": int(f.get("seat") or 1),
                "added_at": str(f.get("added_at") or ""),
            })
        except (KeyError, TypeError, ValueError):
            continue
    # the favorites are one displayed set (the hire surfaces): two that would
    # read the same keep their full ids
    labels = labels_for(x["id"] for x in out)
    for x in out:
        x["label"] = labels[x["id"]]
    return out


def add_favorite(model_id: str) -> Favorite:
    """Adopt a catalog model as a hireable tier. The seat and prices are
    SNAPSHOT here: a seat is a static table entry everywhere else in the
    ledger, and a favorite's price moving later must not silently re-price
    seats already committed in org docs (the sonnet-3→2 migration precedent
    says price changes are deliberate migrations, never drift)."""
    card = find(model_id)
    if card is None:
        raise OpenRouterError(f"{model_id!r} is not in the OpenRouter catalog")
    with _LOCK:
        doc = _load_state()
        favs: list[dict[str, Any]] = doc["favorites"]
        for f in favs:
            if f.get("id") == model_id:
                return favorites()[[x["id"] for x in favorites()].index(model_id)]
        if FAVORITES_MAX and len(favs) >= FAVORITES_MAX:
            raise OpenRouterError(
                f"at most {FAVORITES_MAX} favorites — deselect one first")
        rec: dict[str, Any] = dict(card)
        rec["tier"] = tier_id(model_id)
        rec["seat"] = seat_for(card["prompt"])
        rec["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        favs.append(rec)
        _save_state(doc)
    return favorites()[-1]


def remove_favorite(model_id: str) -> bool:
    """Drop a favorite from the hire surfaces. The tier row STAYS in any org
    doc that already carries it (add-only tables), so a node hired on it keeps
    its seat price and keeps running — deselecting is 'stop offering', never
    'evict'."""
    with _LOCK:
        doc = _load_state()
        before = len(doc["favorites"])
        doc["favorites"] = [f for f in doc["favorites"] if f.get("id") != model_id]
        if len(doc["favorites"]) == before:
            return False
        _save_state(doc)
    return True


def tiers() -> dict[str, int]:
    """tier id → seat, for every favorite (the dynamic half of ledger.TIERS)."""
    return {f["tier"]: f["seat"] for f in favorites()}


def models() -> dict[str, str]:
    """tier id → OpenRouter model id (the dynamic half of ledger.MODELS)."""
    return {f["tier"]: f["id"] for f in favorites()}


def contexts() -> dict[str, int]:
    return {f["tier"]: f["context"] for f in favorites() if f["context"]}


def favorite_for_tier(tier: str) -> Favorite | None:
    for f in favorites():
        if f["tier"] == tier:
            return f
    return None


def tier_label(tier: str, models: Mapping[str, Any] | None = None) -> str:
    """The display name of a TIER id — what a message prints where it used
    to print `or-anthropic-claude-sonnet-5`. A current favorite answers from
    the registry; a DESELECTED one from the org doc's own tier→model table
    (`models`, which the ledger hook merges add-only, so a node still running
    on it keeps its short name); anything else falls back to the slug without
    its `or-` prefix. A static tier passes through untouched."""
    if not is_tier(tier):
        return tier
    f = favorite_for_tier(tier)
    if f is not None:
        return f["label"]
    mid = (models or {}).get(tier)
    if isinstance(mid, str) and mid:
        return model_label(mid)
    return tier[len(TIER_PREFIX):]


def cost(model_id: str, inp: int, cached: int, out: int,
         cache_write: int = 0) -> float:
    """Dollars for one turn from token counts, at the favorite's snapshot
    prices (or the live card's, for a model that is no longer a favorite).
    `inp` is the NON-cached, non-cache-writing input (Anthropic's own
    accounting, which is what Claude Code's result `usage` reports:
    input_tokens · cache_read_input_tokens · cache_creation_input_tokens ·
    output_tokens); callers that get an inclusive input count subtract the
    cached reads first (codex_cost's measured convention)."""
    rec: dict[str, Any] | None = None
    for f in favorites():
        if f["id"] == model_id:
            rec = dict(f)
            break
    if rec is None:
        c = find(model_id) if _catalog_mem.get("cards") is not None else None
        if c is None:
            return 0.0
        rec = dict(c)
    p_in = float(rec.get("prompt") or 0.0)
    p_cached = float(rec.get("cache_read") or 0.0)
    p_write = float(rec.get("cache_write") or p_in)
    p_out = float(rec.get("completion") or 0.0)
    return round((max(inp, 0) * p_in + max(cached, 0) * p_cached
                  + max(cache_write, 0) * p_write
                  + max(out, 0) * p_out) / 1e6, 6)


# ── key status (credits) ───────────────────────────────────────────────────

_key_cache: tuple[float, dict[str, Any]] | None = None


def key_status(force: bool = False) -> dict[str, Any]:
    """`GET /api/v1/key` — the label and CREDIT standing of the stored key,
    never the key. connected: True (200) · False (401/403, the key is
    rejected) · None (no key, or the network did not answer — unknown is
    not the same as rejected, and the panel says which)."""
    global _key_cache
    now = time.time()
    if not force and _key_cache and now - _key_cache[0] < KEY_TTL:
        return dict(_key_cache[1])
    k = _key()
    out: dict[str, Any] = {
        "key_set": bool(k), "connected": None, "label": None,
        "limit": None, "limit_remaining": None, "usage": None,
        "usage_daily": None, "usage_weekly": None, "usage_monthly": None,
        "is_free_tier": None, "reason": None, "checked_at": None,
    }
    if not k:
        out["reason"] = "no API key — add one in App settings → Providers"
        return out
    try:
        status, body = _http_get(f"{API_BASE}/key", {
            **_ua_headers(), "Authorization": f"Bearer {k}"})
    except OSError as e:
        out["reason"] = f"could not reach openrouter.ai: {e}"
        _key_cache = (now, out)
        return dict(out)
    out["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    if status in (401, 403):
        out["connected"] = False
        out["reason"] = ("the stored key was rejected by openrouter.ai — "
                         "replace it in App settings → Providers")
    elif status != 200:
        out["reason"] = f"openrouter.ai answered {status} for the key check"
    else:
        raw: Any = None
        try:
            raw = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raw = None
        doc: dict[str, Any] = (cast("dict[str, Any]", raw)
                               if isinstance(raw, dict) else {})
        data_raw = doc.get("data")
        if isinstance(data_raw, dict):
            data = cast("dict[str, Any]", data_raw)
            out["connected"] = True
            for f in ("label", "limit", "limit_remaining", "usage",
                      "usage_daily", "usage_weekly", "usage_monthly",
                      "is_free_tier"):
                out[f] = data.get(f)
        else:
            out["reason"] = "the key check answered without a `data` record"
    _key_cache = (now, out)
    return dict(out)


def status(force: bool = False) -> dict[str, Any]:
    """The provider-status document for /api/providers and the panel: the
    CLI providers' `installed`/`connected` vocabulary, mapped honestly —
    installed = a key is stored; connected = openrouter.ai accepted it."""
    ks = key_status(force)
    favs = favorites()
    return {
        "installed": bool(ks["key_set"]),
        "connected": ks["connected"] is True,
        "key_set": bool(ks["key_set"]),
        "kind": "api-key" if ks["key_set"] else None,
        "email": None,
        "label": ks["label"],
        "credits": {k: ks[k] for k in (
            "limit", "limit_remaining", "usage", "usage_daily",
            "usage_weekly", "usage_monthly", "is_free_tier", "checked_at")},
        "reason": ks["reason"],
        "favorites": len(favs),
        "favorites_max": FAVORITES_MAX,
    }


def tier_infos() -> list[dict[str, Any]]:
    """The `tiers` list of the /api/providers entry — one row per favorite,
    in the user's own order, carrying everything a hire surface draws."""
    return [{
        "tier": f["tier"], "provider": PROVIDER_ID, "seat": f["seat"],
        "model": f["id"], "letter": f["letter"], "color": f["color"],
        "name": f["name"], "label": f["label"], "vendor": f["vendor"],
        "prompt": f["prompt"], "completion": f["completion"],
        "context": f["context"],
    } for f in favorites()]
