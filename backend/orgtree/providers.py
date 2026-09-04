# pyright: strict
"""The provider registry: which model PROVIDERS this install knows, and the
tier table each one brings (FR-15 / design-multi-provider.md, Phase-1 preview).

A "provider" is the vendor axis the user never had to name while there was
only one: Claude tiers (fable/opus/sonnet/haiku) come from Anthropic via the
Claude Code CLI; the codex tiers (gpt-reserve/sol/terra/luna) come from OpenAI
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
import threading
import time
from typing import Any, Final, TypedDict

from . import appsettings, openrouter
from .ledger import MODELS as _LEDGER_MODELS
from .ledger import TIERS as _LEDGER_TIERS

_DATA: Final[str] = os.path.expanduser(os.environ.get("ORGTREE_DATA", "~/orgtree"))


class TierInfo(TypedDict):
    """One tier as the UI needs it — name, price band, default model id."""
    tier: str
    provider: str
    #: FLOAT, not int (2026-09-03): a sub-$1 tier costs a fraction of a
    #: credit — gpt-reserve and luna are 0.2 — and the OpenRouter favorites
    #: this shape is shared with have been fractional since `seat_for` grew
    #: its below-$1 branch. The frontend already types it `number`.
    seat: float
    model: str
    letter: str


# chip letters for the codex family (claude's live in the frontend's
# TIER_LETTER already; these are served so the frontend never grows a second
# hand-copy for a family it can't hire yet). `sol` shares S with sonnet by
# collision of English; the chip class (t-sol) carries the family, and no
# canvas node can wear both families until codex hire is enabled.
_CODEX_LETTER: Final[dict[str, str]] = {
    "gpt-reserve": "R", "luna": "L", "terra": "T", "sol": "S",
    "astra": "A"}

#: which tier names belong to the codex provider — the AXIS, nothing more.
#: Seats and model ids live in ledger.TIERS / ledger.MODELS (the
#: budget-bearing tables, codex rows added at M4 hire enablement); these
#: views derive from them so there is exactly one copy to drift. Seat rule
#: (user ruling 2026-08-28, ask card): STANDING API $ per M input — sol $5
#: standard (the $4 promo, through ≥2026-11-21, never sets a seat), terra
#: $2, and gpt-reserve/luna $0.20 → 0.2 each since the sub-$1 repricing
#: (user ruling 2026-09-03); they used to floor to 1, which made the four
#: bands read 1·1·2·5 and lost luna's 10× advantage over terra.
_CODEX_ALWAYS_TIER_NAMES: Final = ("gpt-reserve", "luna", "terra", "sol")
_CODEX_TIER_NAMES: Final = _CODEX_ALWAYS_TIER_NAMES + ("astra",)
#: Known Codex tiers whose metadata may exist in the ledger while their offer
#: is controlled by the signed-in account's live model inventory. New rollout
#: tiers belong here; stable tiers do not. The model id itself lives only in
#: ledger.MODELS, so an upstream rename is a one-line data correction.
CONDITIONAL_CODEX_TIERS: Final = frozenset(
    set(_CODEX_TIER_NAMES) - set(_CODEX_ALWAYS_TIER_NAMES))
#: float, not int: this is THE table the codex fractions live in.
CODEX_TIERS: Final[dict[str, float]] = {
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
    "astra": (10.00, 1.00, 50.00),
    "sol": (4.00, 0.40, 20.00),
    "terra": (2.00, 0.20, 12.00),
    "gpt-reserve": (0.20, 0.02, 1.20),
    "luna": (0.20, 0.02, 1.20),
}


# ── the antigravity axis ───────────────────────────────────────────────────

# chip letters for the antigravity family. `flash` shares F with fable by
# collision of English, the same accepted collision as sol/sonnet's S — the
# chip class (t-flash) carries the family.
_ANTIGRAVITY_LETTER: Final[dict[str, str]] = {"flash": "F", "pro": "P"}

#: which tier names belong to the antigravity provider — the AXIS, nothing
#: more. Seats and model ids live in ledger.TIERS / ledger.MODELS; these
#: views derive from them so there is exactly one copy to drift. Seat rule
#: (§0 of docs/adding-a-provider.md): STANDING API $ per M input floored to
#: 1 — pro $2 (the ≤200K band; the long-context surcharge never sets a
#: seat), flash $1.50 standing (3.8-flash's $0.75 is launch pricing through
#: 2026-12-31, and a promo never sets a seat) → 1.
_ANTIGRAVITY_TIER_NAMES: Final = ("flash", "pro")
#: float for the same reason CODEX_TIERS is, even though neither antigravity
#: seat is fractional today: the type follows ledger.TIERS, not the values
#: that happen to be in it.
ANTIGRAVITY_TIERS: Final[dict[str, float]] = {
    t: _LEDGER_TIERS[t] for t in _ANTIGRAVITY_TIER_NAMES}
ANTIGRAVITY_MODELS: Final[dict[str, str]] = {
    t: _LEDGER_MODELS[t] for t in _ANTIGRAVITY_TIER_NAMES}

#: which tier names belong to CLAUDE — everything in the ledger's tables that
#: is not another provider's, which is the rule `claude_tiers()` already
#: applied inline. Named (D-199) because the hire gate needs to distinguish "a
#: tier Claude actually owns" from "an unrecognised tier", and `provider_of`
#: cannot: it answers "claude" for both, deliberately (see its docstring). A
#: gate keyed on `provider_of` alone would refuse a typo'd tier with a message
#: about installing Claude Code.
CLAUDE_TIERS: Final[dict[str, float]] = {
    t: seat for t, seat in _LEDGER_TIERS.items()
    if t not in _CODEX_TIER_NAMES and t not in _ANTIGRAVITY_TIER_NAMES}


def provider_of(tier: str) -> str:
    """Which PROVIDER a tier runs on — `"openai"` | `"google"` | `"claude"`.

    THE one implementation of that axis (D-196). Tier names are one flat
    vocabulary and a tier implies its provider (ledger.TIERS' own comment), but
    until now every caller re-asked the question inline as
    `tier in CODEX_TIERS` / `tier in ANTIGRAVITY_TIERS`. D-182 is the standing
    warning about exactly that shape: three copies of "which MCP servers may
    this node see" existed, two agreed, and the odd one out was a live bug.

    Unknown tiers answer `"claude"` deliberately. This is used to decide
    whether a change CROSSES providers, and the safe default for an
    unrecognised tier is "same lane as the default lane" — a wrong `True` here
    would silently reset a session that did not need resetting, which destroys
    a conversation; a wrong `False` merely leaves today's behaviour.
    """
    if tier in CODEX_TIERS:
        return "openai"
    if tier in ANTIGRAVITY_TIERS:
        return "google"
    # the API-backed lane (2026-09-02): OpenRouter favorites are DYNAMIC
    # tiers, so membership is the `or-` prefix rather than a table — a prefix
    # no static tier carries, checked here without touching the registry file
    if openrouter.is_tier(tier):
        return openrouter.PROVIDER_ID
    return "claude"


#: provider id → the name the UI calls it, and the name any message shown to a
#: person must use. User ruling 2026-08-28: the CLI's OWN name is the
#: provider's UI name — "Codex", not "ChatGPT (Codex)" or "OpenAI";
#: "Antigravity", not "Google". `providers_payload` publishes these same
#: three labels, and
#: reads them from here so a refusal written in the ledger and a heading drawn
#: in the accounts panel cannot come to disagree about what a provider is
#: called.
PROVIDER_LABEL: Final[dict[str, str]] = {
    "claude": "Claude", "openai": "Codex", "google": "Antigravity",
    # the gateway's own name — there is no CLI whose product name could
    # apply, and "OpenRouter" is what the user typed a key into
    openrouter.PROVIDER_ID: openrouter.PROVIDER_LABEL}


def provider_label(tier: str) -> str:
    """The user-facing name of the provider `tier` runs on (D-197)."""
    return PROVIDER_LABEL[provider_of(tier)]


def install_hint(provider: str) -> str:
    """The command THIS machine would actually install `provider` with.

    D-202 made this one string instead of two. It was written twice — once as
    the `/api/providers` reason (the accounts panel's tooltip) and once,
    differently, in the api hire gate's refusal — and the accounts panel was
    the only place it was maintained. That stopped being survivable when the
    user ruled an uninstalled provider absent from the whole UI including the
    accounts page: the gate's refusal became the ONLY place a user is ever
    told how to install Codex or Antigravity, so it cannot be the copy that
    drifts.

    ⚠ NOT `npm i -g` for codex: it installs under the orgtree data dir with
    `--prefix` so the version orgtree spawns is the one it manages, and a
    global install is not what `codex_path` resolves first. The hint must
    name the command that produces a CLI this machine will FIND, which is
    why it interpolates `_DATA` rather than reading well. The Antigravity CLI
    is a native binary with Google's own installer (no npm package exists),
    and that installer drops it exactly where `antigravity_path` looks first.
    """
    if provider == "openai":
        return ("npm install --prefix "
                f"{os.path.join(_DATA, 'codex')} @openai/codex")
    if provider == "google":
        return ("winget install Google.AntigravityCLI" if os.name == "nt"
                else "curl -fsSL https://antigravity.google/cli/install.sh "
                     "| bash")
    if provider == openrouter.PROVIDER_ID:
        # nothing to install: the "install" of an API-backed lane is a key
        return "add an OpenRouter API key in App settings → Providers"
    return "npm install -g @anthropic-ai/claude-code"


#: both tier models publish a 1M-token context window (2×-checked 2026-09-02:
#: benchlm.ai/google/api-pricing, artificialanalysis.ai). As with the other
#: providers, the pinned model capability wins over any per-call observation.
ANTIGRAVITY_CONTEXT: Final[int] = 1_000_000

#: CURRENT listed API prices per M tokens — (input, cached input, output) —
#: keyed by the BASE model id the CLI is handed (`--model gemini-3.8-flash`;
#: the effort rides `--effort`, never the id). The Antigravity subscription
#: publishes no per-token price of its own, so cost-dollars are the
#: developer-API list price of the model that served the turn — the same
#: "tokens × list price" fold every lane uses. Sources (2×-checked
#: 2026-09-02): benchlm.ai/google/api-pricing, openrouter.ai/google/
#: gemini-3.8-flash. 3.8-flash's $0.75/$3.75 is launch pricing through
#: 2026-12-31 (standing $1.50/$7.50 from 2027-01-01 — the seat already
#: prices the standing band); cached reads are 10% of input on every listed
#: row. The wire's `output_tokens` INCLUDES thinking (measured: output 91 =
#: thinking 89 + 2 answer tokens), so the output rate prices reasoning too,
#: exactly as Google bills it.
ANTIGRAVITY_PRICES: Final[dict[str, tuple[float, float, float]]] = {
    "gemini-3.8-flash": (0.75, 0.075, 3.75),
    "gemini-3.7-flash": (0.75, 0.075, 3.75),
    "gemini-3.6-flash": (0.75, 0.075, 3.75),
    "gemini-3.1-pro": (2.00, 0.20, 12.00),
}
#: a model id with no row above (a version the registry grows later) is
#: priced at the PRO row: overstating a stranger's cost is recoverable, a
#: silent $0 is not.
ANTIGRAVITY_PRICE_FALLBACK: Final[tuple[float, float, float]] = (2.00, 0.20, 12.00)
#: gemini-3.1-pro doubles above 200K prompt tokens ($4/$18, both sources).
#: The cached long-context rate is unlisted; 10%-of-input is assumed — the
#: ratio every listed row of both this provider and codex publishes.
ANTIGRAVITY_PRO_LONG: Final[tuple[float, float, float]] = (4.00, 0.40, 18.00)
ANTIGRAVITY_LONG_THRESHOLD: Final[int] = 200_000
_ANTIGRAVITY_PRO_IDS: Final = ("gemini-3.1-pro",)

#: orgtree's effort vocabulary (ledger EFFORTS: low·medium·high·xhigh·max)
#: → the CLI's `--effort`, per tier. Measured 2026-09-02 (agy 1.1.24): the
#: flash models REQUIRE --effort and take low|medium|high; gemini-3.1-pro
#: takes low|high only (medium is refused outright); an effort-suffixed id
#: (`gemini-3.8-flash-high`) CONFLICTS with --effort, which is why the BASE
#: id is what rides `--model`. Above the CLI's ceiling everything clamps to
#: high — an effort the model cannot take is a whole failed turn, never a
#: silent downgrade the other way.
_ANTIGRAVITY_EFFORT: Final[dict[str, dict[str, str]]] = {
    "flash": {"low": "low", "medium": "medium", "high": "high",
              "xhigh": "high", "max": "high"},
    "pro": {"low": "low", "medium": "high", "high": "high",
            "xhigh": "high", "max": "high"},
}


def antigravity_effort(tier: str, effort: str) -> str:
    """The `--effort` value for `tier` at orgtree effort `effort`."""
    table = _ANTIGRAVITY_EFFORT.get(tier) or _ANTIGRAVITY_EFFORT["flash"]
    return table.get(effort, "high")


def antigravity_cost(usage: dict[str, Any] | None) -> float:
    """Dollars for one turn from antigravityrun's NORMALIZED usage document:
    {"model": <base id>, "input": n, "cached": n, "output": n, "thinking": n,
    "last_prompt": n, "requests": n}.

    The CLI reports tokens, never dollars. Wire semantics (measured
    2026-09-02, banked in the probe logs): print mode's `result.usage` SUMS
    every model request of the turn — `input_tokens` is the UNCACHED input
    (total_tokens = input + output; cache_read_tokens is reported beside it,
    not inside it) and `output_tokens` includes thinking — so the bill is
    input·p_in + cached·p_cached + output·p_out. The >200K band applies when
    the turn's last (= largest: context only grows within a turn) prompt
    crossed it — an approximation of the API's per-request banding, erring
    toward overstatement."""
    if not usage:
        return 0.0
    mid = str(usage.get("model") or "")
    p = ANTIGRAVITY_PRICES.get(mid, ANTIGRAVITY_PRICE_FALLBACK)
    if mid in _ANTIGRAVITY_PRO_IDS and int(
            usage.get("last_prompt") or 0) > ANTIGRAVITY_LONG_THRESHOLD:
        p = ANTIGRAVITY_PRO_LONG
    inp = max(int(usage.get("input") or 0), 0)
    cached = max(int(usage.get("cached") or 0), 0)
    out = max(int(usage.get("output") or 0), 0)
    return round((inp * p[0] + cached * p[1] + out * p[2]) / 1e6, 6)


def antigravity_occupancy(usage: dict[str, Any] | None) -> int:
    """Context occupancy after a turn: the LAST model request's prompt size —
    `input_tokens + cache_read_tokens` of the final `agent_response` step
    (measured: 4,563 + 12,175 for a ~16.7K context), the same last-call rule
    `codex_occupancy` follows. The turn total would overcount: it sums every
    request (80K booked for that same 16.7K context — the "123% context"
    bug in another coat). 0 means "no measurement" to `_after_turn`, never
    an empty context."""
    if not usage:
        return 0
    return max(int(usage.get("last_prompt") or 0), 0)


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
    membership in the codex axis is what says a row is not Claude's — the rule
    now lives once, in CLAUDE_TIERS, which this reads."""
    letters = {"fable": "F", "opus": "O", "sonnet": "S", "haiku": "H"}
    return [
        {"tier": t, "provider": "claude", "seat": seat,
         "model": _LEDGER_MODELS.get(t, ""), "letter": letters.get(t, t[:1].upper())}
        for t, seat in sorted(CLAUDE_TIERS.items(), key=lambda kv: kv[1])
    ]


def codex_tiers(available_models: set[str] | frozenset[str] | None = None
                ) -> list[TierInfo]:
    """Codex tier rows safe to OFFER for one account-inventory snapshot.

    Stable tiers are part of the established lane. Conditional rollout tiers
    appear only when their exact pinned model id is in a successfully fetched
    full inventory. ``None`` therefore means no evidence and fails closed for
    those rows; it is never an optimistic default.
    """
    offered = set(_CODEX_ALWAYS_TIER_NAMES)
    if available_models is not None:
        offered.update(t for t in CONDITIONAL_CODEX_TIERS
                       if CODEX_MODELS[t] in available_models)
    return [
        {"tier": t, "provider": "openai", "seat": seat,
         "model": CODEX_MODELS[t], "letter": _CODEX_LETTER[t]}
        for t, seat in sorted(CODEX_TIERS.items(), key=lambda kv: kv[1])
        if t in offered
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
                           text=True, timeout=15,
                           creationflags=(subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                                          if os.name == "nt" else 0))
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


# A provider inventory is network-backed even though it rides the local CLI.
# Keep the accounts/canvas poll cheap, but never let old evidence authorize a
# rollout tier. The hire gate passes force=True and performs its own query.
CODEX_MODEL_INVENTORY_TTL: Final = 30.0
CODEX_MODEL_INVENTORY_TIMEOUT: Final = 20.0
_codex_model_inventory_cache: tuple[float, dict[str, Any]] | None = None
_codex_model_inventory_lock = threading.Lock()


def _inventory_failure(message: str) -> dict[str, Any]:
    return {"available": False, "models": [], "error": message,
            "fetched_at": time.time()}


def codex_model_inventory(
        force: bool = False, status: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
    """Exact model ids offered to the signed-in Codex account, fail-closed.

    OpenAI's app-server contract says clients should call ``model/list``
    before rendering a picker. We request ``includeHidden=true`` because a
    hidden row is not an unusable row: this account's live full inventory
    includes both ``gpt-reserve`` (usable when its separate grant is live)
    and ``codex-auto-review``. Treating ``hidden`` as unavailable previously
    hid a working reserve tier. Only exact ``id`` membership authorizes a
    conditional tier.

    Missing/malformed/error/stale evidence never reuses the last good list.
    The short cache is a UI optimization only; admission calls with
    ``force=True`` re-query immediately before a ledger mutation.
    """
    global _codex_model_inventory_cache
    now = time.time()
    cached = _codex_model_inventory_cache
    if (not force and cached is not None
            and now - cached[0] <= CODEX_MODEL_INVENTORY_TTL):
        return dict(cached[1])

    st = status if status is not None else codex_status()
    if not st.get("installed"):
        result = _inventory_failure("Codex CLI is not installed")
    elif not st.get("connected"):
        result = _inventory_failure("Codex CLI is not signed in")
    else:
        with _codex_model_inventory_lock:
            # A second poll may have filled the cache while this caller waited.
            now = time.time()
            cached = _codex_model_inventory_cache
            if (not force and cached is not None
                    and now - cached[0] <= CODEX_MODEL_INVENTORY_TTL):
                return dict(cached[1])
            exe, _source = codex_path()
            if not exe:
                result = _inventory_failure("Codex CLI is not installed")
            else:
                from . import codexrun             # noqa: PLC0415 — cycle seam
                client: codexrun.AppServerClient | None = None
                try:
                    client = codexrun.AppServerClient(
                        codex_argv(exe),
                        codex_home=str(st.get("codex_home") or "") or None)
                    client.initialize(timeout=CODEX_MODEL_INVENTORY_TIMEOUT)
                    ids: set[str] = set()
                    cursor: str | None = None
                    seen: set[str] = set()
                    while True:
                        params: dict[str, Any] = {
                            "limit": 100, "includeHidden": True}
                        if cursor:
                            params["cursor"] = cursor
                        page = client.request(
                            "model/list", params, CODEX_MODEL_INVENTORY_TIMEOUT)
                        rows = page.get("data")
                        if not isinstance(rows, list):
                            raise ValueError("model/list returned no data list")
                        for row in rows:
                            if not isinstance(row, dict):
                                raise ValueError("model/list returned a malformed row")
                            model_id = row.get("id")
                            if not isinstance(model_id, str) or not model_id:
                                raise ValueError("model/list row has no model id")
                            ids.add(model_id)
                        nxt = page.get("nextCursor")
                        cursor = str(nxt) if nxt else None
                        if not cursor:
                            break
                        if cursor in seen:
                            raise ValueError("model/list repeated a pagination cursor")
                        seen.add(cursor)
                    result = {"available": True, "models": sorted(ids),
                              "error": None, "fetched_at": time.time()}
                except Exception as e:  # noqa: BLE001 — fail closed at boundary
                    result = _inventory_failure(
                        f"Codex model inventory refresh failed: {e}")
                finally:
                    if client is not None:
                        client.close()
    _codex_model_inventory_cache = (time.time(), result)
    return dict(result)


def conditional_codex_availability(
        tier: str, *, force: bool = False,
        status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Availability of one conditional Codex tier from exact live membership."""
    if tier not in CONDITIONAL_CODEX_TIERS:
        return {"enabled": True, "reason": None, "evidence": "not-conditional"}
    inventory = codex_model_inventory(force=force, status=status)
    if not inventory.get("available"):
        return {"enabled": False, "evidence": "inventory-unavailable",
                "reason": str(inventory.get("error") or
                              "Codex model inventory is unavailable")}
    model_id = CODEX_MODELS[tier]
    if model_id not in set(inventory.get("models") or []):
        return {"enabled": False, "evidence": "model-missing", "reason":
                f"the signed-in Codex account does not offer model '{model_id}'"}
    return {"enabled": True, "evidence": "model-present", "reason": None}


def antigravity_tiers() -> list[TierInfo]:
    return [
        {"tier": t, "provider": "google", "seat": seat,
         "model": ANTIGRAVITY_MODELS[t], "letter": _ANTIGRAVITY_LETTER[t]}
        for t, seat in sorted(ANTIGRAVITY_TIERS.items(), key=lambda kv: kv[1])
    ]


# ── antigravity CLI detection ──────────────────────────────────────────────
# The Antigravity CLI is ONE native binary (`agy`, Go) with no npm package
# and no shim: Google's installer (`winget install Google.AntigravityCLI`,
# the curl script elsewhere) drops it at a fixed per-user location, which is
# the "pin" this resolver knows — there is nothing for orgtree to
# `npm install --prefix` itself. Resolution mirrors the other lanes:
#
#     ORGTREE_ANTIGRAVITY > the installer's location > PATH `agy`

def _antigravity_install_path() -> str | None:
    """The installer's own drop location, if the binary is there:
    %LOCALAPPDATA%\\agy\\bin\\agy.exe on Windows (measured 2026-09-02),
    ~/.local/bin/agy elsewhere (the install.sh default)."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            "~/AppData/Local")
        p = os.path.join(base, "agy", "bin", "agy.exe")
    else:
        p = os.path.expanduser("~/.local/bin/agy")
    return p if os.path.exists(p) else None


def antigravity_path() -> tuple[str | None, str]:
    """(resolved executable, how it was found) —
    'env' | 'install' | 'path' | ''."""
    env = os.environ.get("ORGTREE_ANTIGRAVITY")
    if env:
        return env, "env"
    inst = _antigravity_install_path()
    if inst:
        return inst, "install"
    onpath = shutil.which("agy")
    if onpath:
        return onpath, "path"
    return None, ""


def antigravity_argv(exe: str) -> list[str]:
    """The argv HEAD for spawning this antigravity executable — the same
    contract as `codex_argv`/`_claude_argv`. A `.py` path (the test double)
    runs under this interpreter; anything else is the native binary, invoked
    directly (no shim exists to truncate argv)."""
    if exe.lower().endswith(".py"):
        return [sys.executable, exe]
    return [exe]


def antigravity_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment an antigravity child (turn or probe) is spawned with:
    ONE CREDENTIAL PER SPAWN. The CLI self-authenticates from the OS keyring,
    and its MCP children INHERIT every variable their spec does not name
    (measured: ANTHROPIC_API_KEY / OPENAI_API_KEY / CLAUDECODE set on the
    parent reached the orgtree MCP server), so the OTHER providers' material
    is stripped here, at the one place every spawn passes through. The
    CLI's own self-update is switched off for the child as well.  This is a
    per-spawn environment: the user's interactive `agy` keeps its normal
    update behaviour.  The lowercase string is the vendor's literal
    contract — `1` and `True` do not disable the updater."""
    env = dict(os.environ if base is None else base)
    for k in list(env):
        if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_")) or k in (
                "CLAUDECODE", "OPENAI_API_KEY"):
            env.pop(k, None)
    # ⚠ Exact vendor contract, not a boolean parse: agy tests
    # len(value) == 4 && memcmp(value, "true", 4).  "1", "true ", "True",
    # and every other spelling silently disable nothing.  This exact bug
    # previously shipped here as "1", leaving the updater popup alive.
    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
    return env


def _antigravity_version(exe: str) -> str:
    """`agy --version` prints the bare version and exits at once (measured);
    the hard timeout keeps a wedged binary from ever hanging the panel."""
    try:
        r = subprocess.run(antigravity_argv(exe) + ["--version"],
                           capture_output=True, text=True, timeout=15,
                           stdin=subprocess.DEVNULL, env=antigravity_env(),
                           creationflags=(subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                                          if os.name == "nt" else 0))
        m = re.search(r"\d+\.\d+\.\d+", r.stdout or "")
        if m:
            return m.group(0)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


_ANTIGRAVITY_EMAIL_RE: Final = re.compile(
    r"authenticated successfully as (\S+@\S+)")


def antigravity_probe_dir() -> str:
    """Where the connect probe (and the turn logs) live under the data root
    — orgtree's own files, never the user's."""
    return os.path.join(_DATA, "antigravity")


def _antigravity_account(exe: str) -> dict[str, Any]:
    """Connect state from the CLI ITSELF — never from credential material.

    The CLI keeps its OAuth token in the OS keyring (Windows Credential
    Manager, measured) and offers no API-key login, so there is no auth file
    whose existence could stand in for "signed in". What there is: `agy
    models` prints the account's model registry when signed in and NOTHING
    when it is not (its own log then says "Auth mode is unspecified,
    skipping fetchAvailableModels"), and the log of that same run names the
    account it authenticated as. The probe writes its log to a file under
    the data dir via the root `--log-file` flag, so nothing of the user's is
    read or touched, and `kind` is always "oauth" — the only login the CLI
    has.

    Also returns `models`: the registry is the authoritative list the
    ledger's pins are checked against (an id the CLI does not know fails
    the turn loudly — measured — but the panel can say so first)."""
    out: dict[str, Any] = {"connected": False, "email": None, "kind": None,
                           "models": []}
    log_dir = antigravity_probe_dir()
    log_path = os.path.join(log_dir, "models-probe.log")
    try:
        os.makedirs(log_dir, exist_ok=True)
        try:
            os.remove(log_path)
        except OSError:
            pass
        r = subprocess.run(
            antigravity_argv(exe) + ["--log-file", log_path, "models"],
            capture_output=True, text=True, timeout=45, cwd=log_dir,
            stdin=subprocess.DEVNULL, env=antigravity_env(),
            creationflags=(subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                           if os.name == "nt" else 0))
    except (OSError, subprocess.TimeoutExpired):
        return out
    models: list[str] = []
    for line in (r.stdout or "").splitlines():
        # registry rows are "<id>\t<label>"; the "Fetching available
        # models..." banner has spaces and no tab
        if "\t" not in line:
            continue
        mid = line.split("\t", 1)[0].strip()
        if mid and " " not in mid:
            models.append(mid)
    out["models"] = models
    if models:
        out["connected"] = True
        out["kind"] = "oauth"
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                m = _ANTIGRAVITY_EMAIL_RE.search(f.read())
            if m:
                out["email"] = m.group(1)
        except OSError:
            pass
    return out


_antigravity_status_cache: tuple[float, dict[str, Any]] | None = None


def antigravity_status(force: bool = False) -> dict[str, Any]:
    """Install + connect state for the accounts panel, cached 60s — the same
    contract (and the same reasons) as `codex_status`. The connect probe is
    a subprocess of a few seconds, so the cache is what keeps the panel's
    poll from spawning one per tick."""
    global _antigravity_status_cache
    now = time.time()
    if not force and _antigravity_status_cache \
            and now - _antigravity_status_cache[0] < 60:
        return _antigravity_status_cache[1]
    exe, source = antigravity_path()
    # an ORGTREE_ANTIGRAVITY override is taken on faith as the PATH TO USE
    # but not as proof of install (the codex resolver's rule): "installed"
    # pointing at nothing would send the user to sign in instead of to their
    # broken override
    exists = bool(exe) and os.path.exists(exe or "")
    st: dict[str, Any] = {
        "installed": exists,
        "path": exe,
        "source": source,
        "version": _antigravity_version(exe) if exe and exists else None,
    }
    st.update(_antigravity_account(exe) if exe and exists else
              {"connected": False, "email": None, "kind": None, "models": []})
    _antigravity_status_cache = (now, st)
    return st


#: the one tier whose availability is a live SERVER GRANT rather than a fact
#: about this machine.  Named so the rule below and the hire gate in `api.py`
#: cannot drift apart on a string literal.
RESERVE_TIER: Final = "gpt-reserve"


# ⚠ THERE IS NO CAPACITY GATE ON HIRING, AND THAT IS A RULING, NOT AN OVERSIGHT.
# 65273fa added one: a Codex account with its usage window spent refused every
# hire, on the reasoning that the seat is taken before the agent's first turn
# comes back `usage_limit_exceeded`. The user reversed it the same evening —
# "i should still be able to hire agents if my usage window is up; i would like
# the ability to prepare an agent with a charter, even if i cant run it
# actively".
#
# That is the better model of what a hire IS. Hiring names an agent, writes its
# charter and fixes its scope; none of that spends a token, and a window that
# resets on a schedule is a reason to prepare work, not to be locked out of
# preparing it. The capacity question belongs to the TURN, where the Codex CLI
# already answers it loudly and where refusing costs nothing but that turn.
#
# So do not re-add `codex_capacity` here or in `provider_hire_gate`.
# `test_provider_hire_availability` §7 and `test_gpt_reserve_detection` §5 pin
# the ruling from both doors.


def reserve_availability(
        status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Is `gpt-reserve` hireable right now — the tier's OWN rule.

    THE REPORT (user, 2026-09-02): "i had access to gpt-reserve in my codex
    limit earlier today and could use it via the codex cli. however i no
    longer have access. yet the reserve token still appears."

    d7b98c7 answered that with the login KIND (a ChatGPT subscription, not an
    API key).  Necessary, and nowhere near sufficient: the login did not
    change across the user's outage.  What changed was the grant.  Measured on
    that machine the same evening — a gpt-reserve session at 16:06Z billed to
    its own weekly window (2%, resetting Sep 9) while the account's plan
    window sat spent (100%, resetting Sep 7); by 19:15Z reserve turns reported
    a `premium` limit with no windows at all, zero credits, and failed
    `usage_limit_exceeded` on the first message.  Reserve is a pool OpenAI
    hands out and takes back, and DETECTION HAS TO ASK SOMETHING THAT MOVES.

    ⚠ THE MODEL REGISTRY IS NOT THAT SIGNAL, and the second bug in this saga
    was believing it was.  An earlier pass read `visibility` out of the CLI's
    `models_cache.json` and refused on `"hide"`.  The user then got the grant
    back and the token STAYED hidden.  Measured 2026-09-03T00:03:43Z, from a
    registry file written seconds earlier: `gpt-reserve` was `visibility:
    "hide"` while the very same account held a live 8%-used reserve window and
    could use the model from the CLI.  `hide` means "not offered in the model
    PICKER" — `codex-auto-review` carries it too, permanently — and gpt-reserve
    is a routed pool, never picked, so it is ALWAYS hidden.  That check could
    only ever hide the tier forever.  It was inferred from a single observation
    of the withdrawn state, never compared against a granted one.

    Two questions:

      1. is the login one that can EVER hold reserve capacity (d7b98c7), and
      2. does the account hold a gpt-reserve RATE-LIMIT WINDOW right now
         (`codex_limits.grants`) — a granted pool gets a bucket of its own on
         the app-server's board, named after the model, and a withdrawn one
         simply has none.  That is the thing that actually moved across the
         user's outage, in both directions.

    There is no third question.  A spent usage window deliberately does NOT
    refuse a hire (see the ruling above `RESERVE_TIER`), so (2) asks whether
    the window EXISTS, never how full it is; reserve is prepared like any
    other tier and the TURN answers for capacity.

    Each may answer "unknown" (no CLI evidence, a stale board), and unknown
    NEVER refuses: the tier stays offered and the CLI fails the turn loudly,
    which is strictly better than a detection bug hiding a tier the user has —
    which is precisely the failure the registry check shipped.

    Assumes the family-level facts are already settled — Codex installed,
    signed in, the provider not turned off in App settings.  Both callers
    (`providers_payload` below, `api.provider_hire_gate`) check those first
    and then ask this, so there is one implementation of the reserve rule.

    Returns ``{"enabled": bool, "reason": str | None, "evidence": str}``,
    where `reason` is written to be read by the user as a tooltip and as a
    refusal message, and `evidence` names which of the three answered.
    """
    # imported HERE, not at module scope: `codex_limits` imports this module
    # for the CLI path and the signed-in status, so a top-level import either
    # way is a cycle.
    from . import codex_limits

    st = status if status is not None else codex_status()
    if st.get("kind") != "chatgpt":
        return {"enabled": False, "evidence": "login-kind", "reason":
                "signed in with an API key — reserve capacity is a ChatGPT "
                "subscription grant (run `codex login` with a ChatGPT "
                "account to get it)"}
    if codex_limits.grants(RESERVE_TIER) is False:
        return {"enabled": False, "evidence": "no-reserve-window", "reason":
                "this account has no gpt-reserve capacity right now — OpenAI "
                "grants reserve capacity in bursts and withdraws it again, so "
                "this comes back on its own (the other Codex tiers are "
                "unaffected)"}
    return {"enabled": True, "evidence": "granted", "reason": None}


def providers_payload(claude_status: dict[str, Any]) -> dict[str, Any]:
    """The /api/providers document. `claude_status` is composed by the API
    layer from state it already owns (accounts registry, cli_version) — this
    module never reaches into those, so it stays importable from anywhere."""
    codex = codex_status()
    # asked once, before the document is built: the reserve rule reads the
    # usage board (a process spawn when its 30s cache is cold), and asking it
    # again inside a dict literal would double that work for one answer.
    reserve = (reserve_availability(codex) if codex.get("connected")
               else {"enabled": False, "reason": None, "evidence": "offline"})
    inventory = (codex_model_inventory(status=codex)
                 if codex.get("connected") else _inventory_failure("offline"))
    codex_models = (set(inventory.get("models") or [])
                    if inventory.get("available") else set())
    antigravity = antigravity_status()
    orr = openrouter.status()
    choices = appsettings.provider_choices()
    claude_on = choices["claude"]
    codex_on = choices["openai"]
    antigravity_on = choices["google"]
    orr_on = choices.get(openrouter.PROVIDER_ID, True)
    off_reason = "turned off in App settings → Providers"
    return {"providers": [
        {
            "id": "claude",
            "label": PROVIDER_LABEL["claude"],
            "cli": "Claude Code",
            "tiers": claude_tiers(),
            "status": claude_status,
            # D-199: Claude answers the SAME question as the other two now.
            # `hire_enabled: True` and a hard-coded `installed: True` used to
            # sit here, so a machine with only Codex set up still offered all
            # four Claude tiers as live hire buttons — the user's report. The
            # composed `claude_status` carries real install/connect state; this
            # only reads it, because the API layer owns the CLI path and the
            # accounts registry and this module must stay importable from
            # anywhere (see the docstring).
            "hire_enabled": bool(claude_on
                                 and claude_status.get("installed")
                                 and claude_status.get("connected")),
            # D-203: user choice is its own fact. Never falsify `installed`
            # or `connected` to make an off provider disappear — settings
            # must still show the real machine state and let the user turn it
            # back on.
            "user_enabled": claude_on,
            "reason": (
                off_reason if not claude_on
                else None if claude_status.get("installed")
                and claude_status.get("connected")
                else "not signed in — run `claude` once on this machine "
                     "and complete the login"
                if claude_status.get("installed")
                else "Claude Code is not installed — "
                     f"{install_hint('claude')}"),
        },
        {
            "id": "openai",
            # "Codex", not "ChatGPT (Codex)" or "OpenAI" — user ruling
            # 2026-08-28 (ask card): the CLI's own name is the provider's UI
            # name; tier words luna/terra/sol carry everywhere else.
            "label": PROVIDER_LABEL["openai"],
            "cli": "Codex CLI",
            # Stable rows are always described; rollout rows require exact
            # membership in the full account-scoped model list. The picker is
            # convenience only — provider_hire_gate re-queries before mutate.
            "tiers": codex_tiers(codex_models),
            "status": codex,
            # the vision, live (M1–M8 standing): a CONNECTED CLI is a
            # hireable provider — same predicate the api hire gate enforces.
            # A SPENT USAGE WINDOW IS NOT PART OF THIS, by the user's ruling
            # (see above `RESERVE_TIER`): hiring prepares an agent, and being
            # out of usage until Sunday is not a reason to be locked out of
            # writing a charter. The reason is the UI's tooltip, so it speaks
            # to the user, in order of what they'd have to do next.
            "hire_enabled": bool(codex_on and codex.get("connected")),
            "user_enabled": codex_on,
            "reason": (
                off_reason if not codex_on
                else None if codex.get("connected")
                else "not signed in — run `codex login` on this machine"
                if codex.get("installed")
                else f"Codex CLI not installed — {install_hint('openai')}"),
            # gpt-reserve rides the SAME connected-CLI gate as the rest of
            # the family, PLUS its own: reserve capacity is a pool OpenAI
            # grants and withdraws while the login never changes, which is why
            # this tier flickers where its siblings do not. `reserve` holds
            # that whole rule (see `reserve_availability` above) and
            # `provider_hire_gate` asks the SAME function at the door, so the
            # chip and the refusal can never disagree.
            "reserve_hire_enabled": bool(
                codex_on and codex.get("connected") and reserve["enabled"]),
            "reserve_reason": (
                off_reason if not codex_on
                else "not signed in — run `codex login` on this machine"
                if not codex.get("connected")
                else reserve["reason"]),
        },
        {
            "id": "google",
            # "Antigravity", by the same §0 naming rule that made "Codex" the
            # label: the CLI's own product name, not the vendor's.
            "label": PROVIDER_LABEL["google"],
            "cli": "Antigravity CLI",
            "tiers": antigravity_tiers(),
            "status": antigravity,
            "hire_enabled": bool(antigravity_on
                                 and antigravity.get("connected")),
            "user_enabled": antigravity_on,
            "reason": (
                off_reason if not antigravity_on
                else None if antigravity.get("connected")
                else "not signed in — run `agy` once on this machine and "
                     "sign in with your Google account"
                if antigravity.get("installed")
                else "Antigravity CLI not installed — "
                     f"{install_hint('google')}"),
        },
        {
            # the API-BACKED lane (user go-ahead 2026-09-02). No CLI: the
            # `installed`/`connected` vocabulary is mapped honestly by
            # openrouter.status() — installed = a key is stored, connected =
            # openrouter.ai accepted it. Its tiers are the user's FAVORITES,
            # each carrying the letter and canonical color the hire surfaces
            # draw (there is no static per-tier CSS for ~425 models), and the
            # list is empty until the user picks some — an empty family is a
            # disabled row with a reason, never a hidden one.
            "id": openrouter.PROVIDER_ID,
            "label": PROVIDER_LABEL[openrouter.PROVIDER_ID],
            "cli": "REST API (via Claude Code)",
            "tiers": openrouter.tier_infos(),
            "status": orr,
            "hire_enabled": bool(orr_on and orr.get("connected")
                                 and orr.get("favorites")),
            "user_enabled": orr_on,
            "reason": (
                off_reason if not orr_on
                else None if orr.get("connected") and orr.get("favorites")
                else "no favorite models yet — pick some in App settings "
                     "→ Providers"
                if orr.get("connected")
                else str(orr.get("reason") or install_hint(openrouter.PROVIDER_ID))),
        },
    ]}


def is_known_tier(tier: str) -> bool:
    """Is this a recognised model tier across any configured or static provider?"""
    return (tier in CLAUDE_TIERS or tier in _CODEX_TIER_NAMES
            or tier in _ANTIGRAVITY_TIER_NAMES or openrouter.is_tier(tier))


def tier_availability(tier: str) -> tuple[bool, str | None]:
    """Is this model tier configured and available to run on this machine?

    Returns (True, None) if available, or (False, reason) if unavailable.
    Fable is explicitly excluded for auto-autopsy.
    """
    if tier == "fable":
        return False, "fable cannot be used as an autopsy model"

    choices = appsettings.provider_choices()
    claude_on = choices.get("claude", True)
    codex_on = choices.get("openai", True)
    antigravity_on = choices.get("google", True)
    orr_on = choices.get(openrouter.PROVIDER_ID, True)

    if tier in CLAUDE_TIERS:
        if not claude_on:
            return False, "Claude is turned off in App settings → Providers"
        from . import accounts, supervisor
        inst = supervisor.claude_install_state()
        if not inst.get("installed"):
            return False, f"Claude Code CLI is not installed — {install_hint('claude')}"
        if not accounts.live_identity().get("uuid"):
            return False, "Claude is not signed in — complete login on this machine"
        return True, None

    if tier in _CODEX_TIER_NAMES:
        if not codex_on:
            return False, "Codex is turned off in App settings → Providers"
        st = codex_status()
        if not st.get("installed"):
            return False, f"Codex CLI is not installed — {install_hint('openai')}"
        if not st.get("connected"):
            return False, "Codex CLI is not signed in — run `codex login`"
        if tier == RESERVE_TIER:
            reserve = reserve_availability(st)
            if not reserve.get("enabled"):
                return False, f"tier '{RESERVE_TIER}' is not available: {reserve.get('reason')}"
        if tier in CONDITIONAL_CODEX_TIERS:
            availability = conditional_codex_availability(
                tier, force=True, status=st)
            if not availability.get("enabled"):
                return False, (f"tier '{tier}' is not available: "
                               f"{availability.get('reason')}")
        return True, None

    if tier in _ANTIGRAVITY_TIER_NAMES:
        if not antigravity_on:
            return False, "Antigravity is turned off in App settings → Providers"
        ast = antigravity_status()
        if not ast.get("installed"):
            return False, f"Antigravity CLI is not installed — {install_hint('google')}"
        if not ast.get("connected"):
            return False, "Antigravity CLI is not signed in — run `agy` and sign in"
        return True, None

    if openrouter.is_tier(tier):
        if not orr_on:
            return False, "OpenRouter is turned off in App settings → Providers"
        ost = openrouter.status()
        if not ost.get("key_set"):
            return False, f"no OpenRouter API key set — {install_hint(openrouter.PROVIDER_ID)}"
        if not ost.get("connected"):
            return False, f"openrouter.ai did not accept key — {ost.get('reason')}"
        if openrouter.favorite_for_tier(tier) is None:
            return False, f"tier '{tier}' is not among OpenRouter favorites"
        return True, None

    return False, f"unknown model tier '{tier}'"

