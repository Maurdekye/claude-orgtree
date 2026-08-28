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

#: the context window the app-server itself reports per turn
#: (`thread/tokenUsage/updated → modelContextWindow: 258400`, measured on the
#: live account, Appendix C) — all three gpt-5.6 tiers share it. Long-context
#: API pricing (2× input) starts near this boundary, so treating it as "full"
#: keeps compaction ahead of the price step.
CODEX_CONTEXT: Final[int] = 258_400

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
        if t not in CODEX_TIERS
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


def providers_payload(claude_status: dict[str, Any]) -> dict[str, Any]:
    """The /api/providers document. `claude_status` is composed by the API
    layer from state it already owns (accounts registry, cli_version) — this
    module never reaches into those, so it stays importable from anywhere."""
    codex = codex_status()
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
    ]}
