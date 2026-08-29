# pyright: strict
"""The provider registry: which model PROVIDERS this install knows, and the
tier table each one brings (FR-15 / design-multi-provider.md, Phase-1 preview).

A "provider" is the vendor axis the user never had to name while there was
only one: Claude tiers (fable/opus/sonnet/haiku) come from Anthropic via the
Claude Code CLI; the codex tiers (sol/terra/luna, GPT-5.6) come from OpenAI
via the Codex CLI. Tier names stay ONE flat vocabulary — a tier implies its
provider, so nothing anywhere takes a provider argument next to a tier.

⚠ SCOPE: this module owns the provider AXIS — which tier belongs to whom,
detection, pricing views. ledger.TIERS / ledger.MODELS are the budget-bearing
tables and (since M4 hire enablement) carry the codex rows too; this module
DERIVES its views from them so seat prices exist in exactly one place. The
turn adapter is codexrun.py + the supervisor's dispatch leg; the
connected-provider hire gate is api.py's. `hire_enabled` below flips only
when the full MVP path (M1–M8) stands. Detection here is read-only: nothing
in this module spawns a codex turn or touches credentials beyond an existence
check of auth.json.

Codex CLI resolution mirrors the Claude pin (supervisor.CLAUDE): the env
override wins, then a private npm pin under the data root, then PATH:

    ORGTREE_CODEX > <data>/codex/node_modules/@openai/codex-<platform>/
                    vendor/<triple>/bin/codex[.exe]  (npm install --prefix
                    <data>/codex @openai/codex)      > PATH `codex`

The native platform binary is preferred over the `.bin/codex` npm shim for
the same reason supervisor.py avoids `cmd /c` shims: a .CMD truncates argv at
an embedded newline. Probing `--version` would survive that; a future turn
argv would not, so the resolver learns the safe habit now.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Final, TypedDict

from .ledger import MODELS as _LEDGER_MODELS
from .ledger import TIERS as _LEDGER_TIERS

_DATA: Final[str] = os.path.expanduser(os.environ.get("ORGTREE_DATA", "~/orgtree"))


class TierInfo(TypedDict):
    """One tier as the UI needs it — name, price band, default model id."""
    tier: str
    provider: str
    seat: int
    model: str
    letter: str


# chip letters for the codex family (claude's live in the frontend's
# TIER_LETTER already; these are served so the frontend never grows a second
# hand-copy for a family it can't hire yet). `sol` shares S with sonnet by
# collision of English; the chip class (t-sol) carries the family, and no
# canvas node can wear both families until codex hire is enabled.
_CODEX_LETTER: Final[dict[str, str]] = {"luna": "L", "terra": "T", "sol": "S"}

#: which tier names belong to the codex provider — the AXIS, nothing more.
#: Seats and model ids live in ledger.TIERS / ledger.MODELS (the
#: budget-bearing tables, codex rows added at M4 hire enablement); these
#: views derive from them so there is exactly one copy to drift. Seat rule
#: (user ruling 2026-08-28, ask card): STANDING API $ per M input — sol $5
#: standard (the $4 promo, through ≥2026-11-21, never sets a seat), terra
#: $2, luna $0.20 floored to 1.
_CODEX_TIER_NAMES: Final = ("luna", "terra", "sol")
CODEX_TIERS: Final[dict[str, int]] = {
    t: _LEDGER_TIERS[t] for t in _CODEX_TIER_NAMES}
CODEX_MODELS: Final[dict[str, str]] = {
    t: _LEDGER_MODELS[t] for t in _CODEX_TIER_NAMES}

#: GPT-5.6's published context window.  The Codex app-server may report a
#: smaller `modelContextWindow` for an individual call, but that operational
#: hint is not the model's ceiling and must not make Orgtree compact a Sol
#: thread hundreds of thousands of tokens early.  As with Claude's 1M tiers,
#: the pinned model capability wins over a CLI-side observation.
CODEX_CONTEXT: Final[int] = 1_050_000

#: CURRENT listed API prices per M tokens — (input, cached input, output) —
#: for COST-dollars, including sol's promotional $4/$20 cut (standard $5/$30,
#: promo through at least 2026-11-21). SEATS deliberately use the STANDING
#: input price instead (CODEX_TIERS above): dollars ≠ seats, both by user
#: ruling 2026-08-28. Cached reads are 10% of input on every tier. Sources
#: (2×-checked 2026-08-29): aipricing.guru/openai-pricing,
#: cloudzero.com/blog/gpt-5-6-pricing, layer3labs.io/guides/gpt-5-6-pricing.
CODEX_PRICES: Final[dict[str, tuple[float, float, float]]] = {
    "sol": (4.00, 0.40, 20.00),
    "terra": (2.00, 0.20, 12.00),
    "luna": (0.20, 0.02, 1.20),
}


# ── the gemini axis (D-184) ────────────────────────────────────────────────

# chip letters for the gemini family. `flash` shares F with fable by collision
# of English, the same accepted collision as sol/sonnet's S — the chip class
# (t-flash) carries the family.
_GEMINI_LETTER: Final[dict[str, str]] = {"flash": "F", "pro": "P"}

#: which tier names belong to the gemini provider — the AXIS, nothing more.
#: Seats and model ids live in ledger.TIERS / ledger.MODELS; these views
#: derive from them so there is exactly one copy to drift. Seat rule (§0 of
#: docs/adding-a-provider.md): STANDING API $ per M input floored to 1 —
#: pro $2 (the ≤200K band; the long-context surcharge never sets a seat),
#: flash $1.50 → 1, and still 1 when the tier's model moves to 3.7-flash.
_GEMINI_TIER_NAMES: Final = ("flash", "pro")
GEMINI_TIERS: Final[dict[str, int]] = {
    t: _LEDGER_TIERS[t] for t in _GEMINI_TIER_NAMES}
GEMINI_MODELS: Final[dict[str, str]] = {
    t: _LEDGER_MODELS[t] for t in _GEMINI_TIER_NAMES}

#: both launch models publish a 1M-token context window. As with the other
#: providers, the pinned model capability wins over any per-call observation.
GEMINI_CONTEXT: Final[int] = 1_000_000

#: CURRENT listed API prices per M tokens — (input, cached input, output) —
#: keyed by MODEL ID, not tier: the CLI spends tokens on SIDE MODELS in the
#: same turn (measured: a `utility_router` role on gemini-3.1-flash-lite), so
#: the cost fold must price every model the usage document names. Sources
#: (2×-checked 2026-08-29): benchlm.ai/google/api-pricing,
#: developer.puter.com/tutorials/gemini-api-pricing, metacto.com pricing
#: guide. Cached reads are 10% of input on every listed row.
GEMINI_PRICES: Final[dict[str, tuple[float, float, float]]] = {
    "gemini-3.5-flash": (1.50, 0.15, 9.00),
    "gemini-3.1-pro-preview-customtools": (2.00, 0.20, 12.00),
    "gemini-3.1-pro-preview": (2.00, 0.20, 12.00),
    "gemini-3.1-flash-lite": (0.25, 0.025, 1.50),
}
#: a model id with no row above (a future side model) is priced at the PRO
#: row: overstating a stranger's cost is recoverable, a silent $0 is not.
GEMINI_PRICE_FALLBACK: Final[tuple[float, float, float]] = (2.00, 0.20, 12.00)
#: gemini-3.1-pro doubles above 200K prompt tokens ($4/$18, both sources).
#: The cached long-context rate is unlisted; 10%-of-input is assumed — the
#: ratio every listed row of both this provider and codex publishes.
GEMINI_PRO_LONG: Final[tuple[float, float, float]] = (4.00, 0.40, 18.00)
GEMINI_LONG_THRESHOLD: Final[int] = 200_000
_GEMINI_PRO_IDS: Final = ("gemini-3.1-pro-preview-customtools",
                          "gemini-3.1-pro-preview")


def gemini_cost(usage: dict[str, Any] | None) -> float:
    """Dollars for one turn from geminirun's NORMALIZED usage document:
    {"models": {<model id>: {"input": n, "cached": n, "output": n,
    "prompt": n}}, "main": <model id>}.

    The CLI reports tokens, never dollars. Wire semantics behind the
    normalization (measured 2026-08-29, banked in the probe logs): the
    one-shot stats split cached from input (`prompt = input + cached`) and
    thoughts from candidates; the ACP lane's `_meta.quota` reports only
    input/output per model — no cached split (cached reads priced as full
    input, a slight overstatement) and output EXCLUDES reasoning (a slight
    understatement). Documented approximation, not an accident."""
    if not usage:
        return 0.0
    total = 0.0
    models: dict[str, Any] = usage.get("models") or {}
    for mid, tok in models.items():
        if not isinstance(tok, dict):
            continue
        p = GEMINI_PRICES.get(str(mid), GEMINI_PRICE_FALLBACK)
        prompt = int(tok.get("prompt") or 0)
        if str(mid) in _GEMINI_PRO_IDS and prompt > GEMINI_LONG_THRESHOLD:
            p = GEMINI_PRO_LONG
        inp = max(int(tok.get("input") or 0), 0)
        cached = max(int(tok.get("cached") or 0), 0)
        out = max(int(tok.get("output") or 0), 0)
        total += (inp * p[0] + cached * p[1] + out * p[2]) / 1e6
    return round(total, 6)


def gemini_occupancy(usage: dict[str, Any] | None) -> int:
    """Context occupancy after a turn: an ESTIMATE of the main model's last
    prompt size. The wire reports only the SUM of every request's input
    across the turn (measured — a ~30-round tool loop booked 3.6M against a
    1M window before this divisor existed), so the sum is divided by the
    turn's observed request count. A parallel tool batch makes the divisor
    overcount and the estimate run LOW — the safe direction: a low estimate
    delays compaction, the raw sum spuriously forced it. Side models'
    prompts are other conversations and must not count. 0 means "no
    measurement" to `_after_turn`, never an empty context."""
    if not usage:
        return 0
    models: dict[str, Any] = usage.get("models") or {}
    main = str(usage.get("main") or "")
    tok = models.get(main)
    if isinstance(tok, dict) and int(tok.get("prompt") or 0):
        total = int(tok["prompt"])
    else:
        total = max((int(t.get("prompt") or 0) for t in models.values()
                     if isinstance(t, dict)), default=0)
    requests = max(1, int(usage.get("requests") or 1))
    return total // requests


def codex_cost(tier: str, token_usage: dict[str, Any] | None) -> float:
    """Dollars for one turn from the app-server's tokenUsage document.

    The codex CLI reports tokens, never dollars, so orgtree prices the turn
    itself (design §3.5). Measured field semantics (probe-live.jsonl):
    `total.inputTokens` INCLUDES the cached reads (totalTokens = input +
    output), and `outputTokens` includes reasoning — so the bill is
    (input − cached)·p_in + cached·p_cached + output·p_out."""
    if not token_usage:
        return 0.0
    p = CODEX_PRICES.get(tier)
    if not p:
        return 0.0
    tot: dict[str, Any] = token_usage.get("total") or {}
    inp = int(tot.get("inputTokens") or 0)
    cached = min(int(tot.get("cachedInputTokens") or 0), inp)
    out = int(tot.get("outputTokens") or 0)
    return round(((inp - cached) * p[0] + cached * p[1] + out * p[2]) / 1e6, 6)


def codex_occupancy(token_usage: dict[str, Any] | None) -> int:
    """Context occupancy after a turn: the LAST call's input+cache size — the
    same rule the claude lane's `occ` follows (`total.*` is cumulative across
    the turn's calls and would overcount, the ~123% bug in another coat).
    `last.inputTokens` already includes the cached reads (measured); a compact
    or zero-input trailing call reports last=0, and 0 is treated as "no
    measurement" by `_after_turn`, never as an empty context."""
    if not token_usage:
        return 0
    last: dict[str, Any] = token_usage.get("last") or {}
    n = int(last.get("inputTokens") or 0)
    if not n:
        n = int((token_usage.get("total") or {}).get("inputTokens") or 0)
    return n


def codex_argv(exe: str) -> list[str]:
    """The argv HEAD for spawning this codex executable — the same shape as
    supervisor's `_claude_argv`. A `.py` path (the test double) runs under
    this interpreter; anything else is the native binary, invoked directly so
    no `.CMD` shim can truncate argv."""
    if exe.lower().endswith(".py"):
        return [sys.executable, exe]
    return [exe]


def claude_tiers() -> list[TierInfo]:
    """The Claude family, FROM the ledger's own tables — this module adds the
    provider axis without becoming a second copy of the seat prices. The
    ledger's tables carry EVERY provider's tiers (one flat vocabulary), so
    membership in the codex axis is what says a row is not Claude's."""
    letters = {"fable": "F", "opus": "O", "sonnet": "S", "haiku": "H"}
    return [
        {"tier": t, "provider": "claude", "seat": seat,
         "model": _LEDGER_MODELS.get(t, ""), "letter": letters.get(t, t[:1].upper())}
        for t, seat in sorted(_LEDGER_TIERS.items(), key=lambda kv: kv[1])
        if t not in CODEX_TIERS and t not in GEMINI_TIERS
    ]


def codex_tiers() -> list[TierInfo]:
    return [
        {"tier": t, "provider": "openai", "seat": seat,
         "model": CODEX_MODELS[t], "letter": _CODEX_LETTER[t]}
        for t, seat in sorted(CODEX_TIERS.items(), key=lambda kv: kv[1])
    ]


# ── codex CLI detection ────────────────────────────────────────────────────

def _codex_pin() -> str | None:
    """The private npm pin's NATIVE binary, if installed. The platform package
    name and vendor triple vary per OS, so glob rather than hardcode; the
    `.bin` shim is the fallback for a layout the glob doesn't anticipate."""
    root = os.path.join(_DATA, "codex", "node_modules")
    exe = "codex.exe" if os.name == "nt" else "codex"
    hits = glob.glob(os.path.join(
        root, "@openai", "codex-*", "vendor", "*", "bin", exe))
    if hits:
        return hits[0]
    shim = os.path.join(root, ".bin", "codex.cmd" if os.name == "nt" else "codex")
    return shim if os.path.exists(shim) else None


def codex_path() -> tuple[str | None, str]:
    """(resolved executable, how it was found) — 'env' | 'pin' | 'path' | ''."""
    env = os.environ.get("ORGTREE_CODEX")
    if env:
        return env, "env"
    pin = _codex_pin()
    if pin:
        return pin, "pin"
    onpath = shutil.which("codex")
    if onpath:
        return onpath, "path"
    return None, ""


def _codex_version(exe: str) -> str:
    """Version WITHOUT running the binary when possible — read the pin's
    package.json (the same trick as supervisor.cli_version), else probe
    `--version` with a hard timeout. A CLI that hangs must never hang the
    accounts panel."""
    probe = os.path.dirname(exe)
    for _ in range(6):
        p = os.path.join(probe, "package.json")
        try:
            pkg: dict[str, Any] = json.load(open(p, encoding="utf-8"))
            if str(pkg.get("name", "")).startswith("@openai/codex"):
                return str(pkg.get("version", "unknown"))
        except OSError:
            pass
        except json.JSONDecodeError:
            pass
        probe = os.path.dirname(probe)
    try:
        argv = (["cmd", "/c", exe] if os.name == "nt"
                and exe.lower().endswith((".cmd", ".bat")) else [exe])
        r = subprocess.run(argv + ["--version"], capture_output=True,
                           text=True, timeout=15)
        m = re.search(r"\d+\.\d+\.\d+", r.stdout or "")
        if m:
            return m.group(0)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def _codex_account() -> dict[str, Any]:
    """Connect state from $CODEX_HOME/auth.json — EXISTENCE and display
    identity only, never credential material. The id_token is a JWT whose
    payload names the account; decoding the payload is a base64 read, not a
    verification, and it is used for nothing but the panel label."""
    auth = os.path.join(_codex_home(), "auth.json")
    out: dict[str, Any] = {"connected": False, "email": None, "kind": None}
    try:
        doc: dict[str, Any] = json.load(open(auth, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if doc.get("OPENAI_API_KEY"):
        out["connected"] = True
        out["kind"] = "api-key"
    tokens = doc.get("tokens")
    if isinstance(tokens, dict):
        out["connected"] = True
        out["kind"] = "chatgpt"
        idt = tokens.get("id_token")  # pyright: ignore[reportUnknownMemberType]
        if isinstance(idt, str) and idt.count(".") == 2:
            try:
                import base64
                pay = idt.split(".")[1]
                pay += "=" * (-len(pay) % 4)
                claims: dict[str, Any] = json.loads(
                    base64.urlsafe_b64decode(pay).decode("utf-8", "replace"))
                email = claims.get("email")
                if isinstance(email, str):
                    out["email"] = email
            except (ValueError, json.JSONDecodeError):
                pass
    return out


_status_cache: tuple[float, dict[str, Any]] | None = None


def codex_status(force: bool = False) -> dict[str, Any]:
    """Install + connect state for the accounts panel, cached 60s: the panel
    polls, and a `--version` subprocess per poll would be a hang risk and a
    process leak for zero freshness gain."""
    global _status_cache
    now = time.time()
    if not force and _status_cache and now - _status_cache[0] < 60:
        return _status_cache[1]
    exe, source = codex_path()
    # an ORGTREE_CODEX override is taken on faith as the PATH TO USE (same
    # trust the claude resolver gives its env override) but not as proof of
    # install: pin and PATH hits exist by construction, an env path may not,
    # and "installed" pointing at nothing would send the user to `codex
    # login` instead of to their broken override.
    exists = bool(exe) and os.path.exists(exe or "")
    st: dict[str, Any] = {
        "installed": exists,
        "path": exe,
        "source": source,
        "version": _codex_version(exe) if exe and exists else None,
        "codex_home": _codex_home(),
    }
    st.update(_codex_account())
    _status_cache = (now, st)
    return st


def gemini_tiers() -> list[TierInfo]:
    return [
        {"tier": t, "provider": "google", "seat": seat,
         "model": GEMINI_MODELS[t], "letter": _GEMINI_LETTER[t]}
        for t, seat in sorted(GEMINI_TIERS.items(), key=lambda kv: kv[1])
    ]


# ── gemini CLI detection ───────────────────────────────────────────────────
# The Gemini CLI is a Node bundle with NO native binary (bin →
# bundle/gemini.js), so unlike codex the resolver's job is to find the JS
# entry and let `gemini_argv` put `node` in front of it — never the npm
# `.CMD`/`.ps1` shims (the argv-truncation hazard both other lanes document).

def _gemini_pin() -> str | None:
    """The private npm pin's JS entry, if installed
    (`npm install --prefix <data>/gemini @google/gemini-cli`)."""
    root = os.path.join(_DATA, "gemini", "node_modules", "@google",
                        "gemini-cli")
    for rel in (("bundle", "gemini.js"), ("dist", "index.js")):
        p = os.path.join(root, *rel)
        if os.path.exists(p):
            return p
    return None


def _gemini_shim_js(exe: str) -> str | None:
    """The real JS entry next to an npm-global shim (…\\npm\\gemini.cmd →
    …\\npm\\node_modules\\@google\\gemini-cli\\bundle\\gemini.js)."""
    root = os.path.join(os.path.dirname(exe), "node_modules", "@google",
                        "gemini-cli")
    for rel in (("bundle", "gemini.js"), ("dist", "index.js")):
        p = os.path.join(root, *rel)
        if os.path.exists(p):
            return p
    return None


def gemini_path() -> tuple[str | None, str]:
    """(resolved entry, how it was found) — 'env' | 'pin' | 'path' | ''.
    A PATH hit is resolved through the shim to the JS entry when possible."""
    env = os.environ.get("ORGTREE_GEMINI")
    if env:
        return env, "env"
    pin = _gemini_pin()
    if pin:
        return pin, "pin"
    onpath = shutil.which("gemini")
    if onpath:
        return _gemini_shim_js(onpath) or onpath, "path"
    return None, ""


def gemini_argv(exe: str) -> list[str]:
    """The argv HEAD for spawning this gemini entry — same contract as
    `codex_argv`/`_claude_argv`. A `.py` path (the test double) runs under
    this interpreter; a `.js` runs under node; a shim falls back to `cmd /c`
    (safe here only because no gemini argv ever carries a newline)."""
    low = exe.lower()
    if low.endswith(".py"):
        return [sys.executable, exe]
    if low.endswith((".js", ".mjs")):
        return ["node", exe]
    if os.name == "nt" and low.endswith((".cmd", ".bat", ".ps1")):
        js = _gemini_shim_js(exe)
        if js:
            return ["node", js]
        return ["cmd", "/c", exe]
    return [exe]


def _gemini_version(exe: str) -> str:
    """Version WITHOUT running anything when possible: walk up from the JS
    entry to the @google/gemini-cli package.json, else probe `--version`
    with a hard timeout (the accounts panel must never hang on a CLI)."""
    probe = os.path.dirname(exe)
    for _ in range(6):
        p = os.path.join(probe, "package.json")
        try:
            pkg: dict[str, Any] = json.load(open(p, encoding="utf-8"))
            if str(pkg.get("name", "")) == "@google/gemini-cli":
                return str(pkg.get("version", "unknown"))
        except OSError:
            pass
        except json.JSONDecodeError:
            pass
        probe = os.path.dirname(probe)
    try:
        r = subprocess.run(gemini_argv(exe) + ["--version"],
                           capture_output=True, text=True, timeout=15)
        m = re.search(r"\d+\.\d+\.\d+", r.stdout or "")
        if m:
            return m.group(0)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _gemini_home() -> str:
    return (os.environ.get("ORGTREE_GEMINI_HOME")
            or os.path.expanduser("~/.gemini"))


def _gemini_account() -> dict[str, Any]:
    """Connect state from the CLI's own auth records — EXISTENCE and display
    identity only, never credential material. The CLI's selected auth method
    lives in settings.json; an api-key selection stores the key itself in
    the OS keychain (measured on Windows: Credential Manager target
    `gemini-cli-api-key/…`), which orgtree deliberately never opens — the
    child process self-authenticates from the CLI's own store, and a missing
    key fails the turn with the CLI's own error, loudly."""
    home = _gemini_home()
    out: dict[str, Any] = {"connected": False, "email": None, "kind": None}
    selected = ""
    try:
        doc: dict[str, Any] = json.load(
            open(os.path.join(home, "settings.json"), encoding="utf-8"))
        sec = doc.get("security")
        auth = sec.get("auth") if isinstance(sec, dict) else None
        if isinstance(auth, dict):
            selected = str(auth.get("selectedType") or "")
    except (OSError, json.JSONDecodeError):
        pass
    has_oauth = os.path.exists(os.path.join(home, "oauth_creds.json"))
    if selected == "gemini-api-key":
        out["connected"] = True
        out["kind"] = "api-key"
    elif selected == "vertex-ai":
        out["connected"] = True
        out["kind"] = "vertex"
    elif selected == "oauth-personal" or (not selected and has_oauth):
        out["connected"] = has_oauth
        out["kind"] = "oauth" if has_oauth else None
    if out["connected"] and has_oauth:
        try:
            acct: dict[str, Any] = json.load(
                open(os.path.join(home, "google_accounts.json"),
                     encoding="utf-8"))
            active = acct.get("active")
            if isinstance(active, str) and active:
                out["email"] = active
        except (OSError, json.JSONDecodeError):
            pass
    return out


_gemini_status_cache: tuple[float, dict[str, Any]] | None = None


def gemini_status(force: bool = False) -> dict[str, Any]:
    """Install + connect state for the accounts panel, cached 60s — the same
    contract (and the same reasons) as `codex_status`."""
    global _gemini_status_cache
    now = time.time()
    if not force and _gemini_status_cache and now - _gemini_status_cache[0] < 60:
        return _gemini_status_cache[1]
    exe, source = gemini_path()
    exists = bool(exe) and os.path.exists(exe or "")
    st: dict[str, Any] = {
        "installed": exists,
        "path": exe,
        "source": source,
        "version": _gemini_version(exe) if exe and exists else None,
        "gemini_home": _gemini_home(),
    }
    st.update(_gemini_account())
    _gemini_status_cache = (now, st)
    return st


def providers_payload(claude_status: dict[str, Any]) -> dict[str, Any]:
    """The /api/providers document. `claude_status` is composed by the API
    layer from state it already owns (accounts registry, cli_version) — this
    module never reaches into those, so it stays importable from anywhere."""
    codex = codex_status()
    gemini = gemini_status()
    return {"providers": [
        {
            "id": "claude",
            "label": "Claude",
            "cli": "Claude Code",
            "tiers": claude_tiers(),
            "status": claude_status,
            "hire_enabled": True,
            "reason": None,
        },
        {
            "id": "openai",
            # "Codex", not "ChatGPT (Codex)" or "OpenAI" — user ruling
            # 2026-08-28 (ask card): the CLI's own name is the provider's UI
            # name; tier words luna/terra/sol carry everywhere else.
            "label": "Codex",
            "cli": "Codex CLI",
            "tiers": codex_tiers(),
            "status": codex,
            # the vision, live (M1–M8 standing): a CONNECTED CLI is a
            # hireable provider — same predicate the api hire gate enforces.
            # The reason is the UI's tooltip, so it speaks to the user, in
            # order of what they'd have to do next.
            "hire_enabled": bool(codex.get("connected")),
            "reason": (
                None if codex.get("connected")
                else "not signed in — run `codex login` on this machine"
                if codex.get("installed")
                else "Codex CLI not installed — npm install --prefix "
                     f"{os.path.join(_DATA, 'codex')} @openai/codex"),
        },
        {
            "id": "google",
            # "Gemini", by the same §0 naming rule that made "Codex" the
            # label: the CLI's own product name, not the vendor's.
            "label": "Gemini",
            "cli": "Gemini CLI",
            "tiers": gemini_tiers(),
            "status": gemini,
            "hire_enabled": bool(gemini.get("connected")),
            "reason": (
                None if gemini.get("connected")
                else "not signed in — run `gemini` once on this machine and "
                     "pick a login method"
                if gemini.get("installed")
                else "Gemini CLI not installed — npm install --prefix "
                     f"{os.path.join(_DATA, 'gemini')} @google/gemini-cli"),
        },
    ]}
