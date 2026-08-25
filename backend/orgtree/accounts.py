"""Machine-local account routing — WHERE each model's prompts go on this machine.

The user's 2026-08-25 redesign, replacing the D-144 registry/pin/selection
stack wholesale. Two facts and nothing else:

  · the key list       — the SECONDARY accounts this machine may bill, as
                         pasted `claude setup-token` keys, in priority order
  · usage_refreshes    — `[account][tier] = refresh-at | absent`, the TOTAL
                         routing state: an entry exists iff that account's
                         capacity for that tier is used up, and holds the
                         epoch when it refreshes

The PRIMARY account is not stored here at all: it is whoever Claude Code is
signed in as on this machine, read live from the CLI's own config. It is not
switchable from any UI — logging in/out with the CLI is the only mover.

Routing is per MODEL TIER and machine-global: a tier runs on the highest-
priority account (primary first, then keys in list order) whose capacity for
it is not used up. There is no per-org selection, no pin, no adopted-identity
registry — everything that was not this machine's own routing state is gone
(user ruling 2026-08-25: "all other state not related to this machine is to
be dropped from the internal system").

⚠ THE INVARIANT CARRIED OVER FROM THE OLD REGISTRY: **this file's document
stores IDENTITY AND STATE, never CREDENTIALS.** Key material lives in the
token store (`tokens.py`, its own file); `accounts.json` holds opaque row ids
(a short hash), account uuids, and timestamps. `_reject_secrets` still runs
on every write and raises rather than redacting.

A key row carries the `account_uuid` its token resolved to (one profile call
at registration, best-effort, lazily retried) for exactly one purpose: a key
that belongs to the SAME account the machine is signed in as is a duplicate
lane, not an alternative — failing over to it re-spends the identical limit
(measured live 2026-08-24 21:20Z: the re-driven turn hit the same session
limit 4.2 s later). Such rows are excluded from routing and greyed in the
panel (user ruling 2026-08-25).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from typing import Any

from . import limits, store, tokens

REGISTRY_NAME = "accounts.json"
VERSION = 2
_lock = threading.RLock()

#: the sentinel id of the signed-in account. Not a stored row — `readout` and
#: `resolve` synthesize it from the live CLI config on every call.
PRIMARY = "primary"

#: the four model tiers, in display order. Routing state is keyed on these;
#: an unknown tier simply never acquires a mark and so always resolves to the
#: first account in priority order, which is the right degradation.
TIERS = ("haiku", "sonnet", "opus", "fable")

PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
OAUTH_BETA = "oauth-2025-04-20"
# console/api hosts sit behind a WAF that 403s a default urllib UA (Cloudflare
# 1010). Identifying as the CLI is what this client id legitimately is.
USER_AGENT = "claude-cli (external, cli)"


# Resolved per call, never captured at import: `store.DATA_ROOT` is what the
# rest of the backend actually uses, and a module-level constant would freeze
# whatever the value was at import time — which is how a test that sets the
# data root ends up asserting against the developer's real ~/orgtree.
def registry_path() -> str:
    return os.path.join(store.DATA_ROOT, REGISTRY_NAME)


# --------------------------------------------------------------- the invariant
# Deliberately broader than "starts with sk-ant-": a future credential shape
# this pattern does not know about is exactly the one that would slip through,
# so anything long and opaque is refused too. False positives are a caller bug
# worth failing on; a token in this file is not recoverable once written.
_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}"),                 # JWT-ish
    re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}\b"),          # long opaque run
)
# keys whose NAME alone means a caller is handing us the wrong thing
_SECRET_KEYS = {"accesstoken", "access_token", "refreshtoken", "refresh_token",
                "token", "authorization", "api_key", "apikey", "secret",
                "credentials", "bearer", "id_token"}


class SecretInRegistry(ValueError):
    """Raised when a write would put credential material in the registry."""


def _reject_secrets(node: Any, path: str = "") -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).replace("-", "_").lower() in _SECRET_KEYS:
                raise SecretInRegistry(
                    f"refusing to write credential-shaped key {path}/{k!r} "
                    f"into {REGISTRY_NAME} — the registry holds identity only")
            _reject_secrets(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _reject_secrets(v, f"{path}[{i}]")
    elif isinstance(node, str):
        for pat in _SECRET_PATTERNS:
            if pat.search(node):
                raise SecretInRegistry(
                    f"refusing to write credential-shaped VALUE at {path} "
                    f"into {REGISTRY_NAME} — the registry holds identity only")


# ------------------------------------------------------------------ the record
def _blank() -> dict[str, Any]:
    return {"version": VERSION, "keys": [], "usage_refreshes": {}}


class RegistryUnreadable(RuntimeError):
    """The registry file exists but could not be understood."""


def _migrate_v1(doc: dict[str, Any]) -> dict[str, Any]:
    """The D-144 registry → this shape, WITHOUT touching disk.

    The old document's adopted identities, labels and pins are dropped on the
    floor by design. What survives is the one thing the user cannot re-create
    by hand: rows for keys already pasted into the token store ("you won't be
    able to see it again" — deleting those would force a re-mint). Old rows
    were keyed on account uuid and the token store keyed on the same, so the
    old uuid simply BECOMES the row id, and — usefully — it is also the row's
    resolved `account_uuid`, so duplicate-of-primary detection works for
    migrated rows with no network call.

    Deterministic from (v1 doc, token store), so readers can migrate in
    memory on every load; the first WRITE persists version 2. Reads never
    write.
    """
    stored = set(tokens.load()["tokens"])
    order = [str(u) for u in (doc.get("order") or []) if isinstance(u, str)]
    ids = [u for u in order if u in stored]
    ids += sorted(u for u in stored if u not in ids)
    return {"version": VERSION,
            "keys": [{"id": u, "account_uuid": u} for u in ids],
            "usage_refreshes": {}}


def load(*, strict: bool = False) -> dict[str, Any]:
    """The registry, or a blank one.

    A corrupt file reads as blank for READERS: taking the panel down over it
    is a worse failure than showing nothing. `strict=True` — which every
    read-modify-WRITE cycle uses — raises instead, so a mutation can never
    silently replace an unreadable file with an empty registry (the data-loss
    shape the 2026-08-24 review fixed; the unreadable file stays on disk for
    hand recovery). A version-1 document is not corrupt: it migrates in
    memory on every load, for readers and writers alike.
    """
    try:
        with open(registry_path(), encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return _blank()                        # genuinely absent: not corrupt
    except (OSError, json.JSONDecodeError) as e:
        if strict:
            raise RegistryUnreadable(
                f"{registry_path()} exists but could not be read ({e}) — "
                f"refusing to overwrite it with a blank registry") from None
        return _blank()
    if not isinstance(doc, dict):
        if strict:
            raise RegistryUnreadable(
                f"{registry_path()} is not an object — refusing to overwrite "
                f"it; recover or remove it first")
        return _blank()
    if doc.get("version") == 1:
        return _migrate_v1(doc)
    if doc.get("version") != VERSION:
        if strict:
            raise RegistryUnreadable(
                f"{registry_path()} is version {doc.get('version')!r}, not "
                f"{VERSION} — refusing to overwrite it; migrate it first")
        return _blank()
    if not isinstance(doc.get("keys"), list):
        doc["keys"] = []
    doc["keys"] = [k for k in doc["keys"]
                   if isinstance(k, dict) and k.get("id")]
    if not isinstance(doc.get("usage_refreshes"), dict):
        doc["usage_refreshes"] = {}
    return doc


def save(doc: dict[str, Any]) -> None:
    """Atomic tmp+replace, mirroring store.save_org — including the fsync,
    because a half-written registry reads as 'no keys registered' and the
    rows are not re-creatable from anywhere else."""
    _reject_secrets(doc)                       # before anything touches disk
    doc["version"] = VERSION
    blob = json.dumps(doc, indent=2).encode("utf-8")
    p = registry_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        for i in range(20):                    # Windows: see store._IOLatch
            try:
                os.replace(tmp, p)
                tmp = ""
                break
            except PermissionError:
                if i == 19:
                    raise
                time.sleep(0.01 * (i + 1))
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


# ------------------------------------------------------- the primary identity
# The CLI's own config, holding identity METADATA (`oauthAccount`) and no
# credential at all. Module level so a test can point it at a fixture without
# going near a real login.
LIVE_CONFIG = os.path.expanduser("~/.claude.json")


def live_identity() -> dict[str, str]:
    """WHO this machine is signed in as — `{"uuid", "email"}`, both "" when
    nobody is (missing/unreadable config, or no `oauthAccount`).

    Read from the CLI's CONFIG, never from the credentials store: this must
    stay a metadata read, cheap enough for every readout and every spawn, and
    incapable of touching a token. The uuid is the discriminator (identical
    across a token refresh, different between accounts — the old probe
    battery's one durable finding); the email is display only.
    """
    try:
        with open(LIVE_CONFIG, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {"uuid": "", "email": ""}
    if not isinstance(doc, dict):
        return {"uuid": "", "email": ""}
    acct = doc.get("oauthAccount")
    if not isinstance(acct, dict):
        return {"uuid": "", "email": ""}
    return {"uuid": str(acct.get("accountUuid") or ""),
            "email": str(acct.get("emailAddress") or "")}


# ------------------------------------------------------------------- key rows
def key_for_token(token: str) -> str:
    """The row id a raw token belongs to, or "". The reverse lookup exists so
    attribution (`supervisor.identity_in_env`) can read the RESOLVED spawn
    env — the dict the process actually holds — rather than trusting intent.
    Takes the secret, returns a label; keeps nothing."""
    if not token:
        return ""
    for kid, tok in tokens.load()["tokens"].items():
        if tok == token:
            return str(kid)
    return ""


def _resolve_via_profile(access_token: str) -> dict[str, Any]:
    """The only network call in this module. Isolated so tests inject instead
    of reaching the internet, and so the one place that handles a live token
    is a single short function that returns identity and keeps nothing."""
    import urllib.request
    req = urllib.request.Request(
        PROFILE_URL,
        headers={"Authorization": f"Bearer {access_token}",
                 "anthropic-beta": OAUTH_BETA,
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _uuid_from_profile(profile: Any) -> str:
    if not isinstance(profile, dict):
        return ""
    acct = profile.get("account")
    if not isinstance(acct, dict):
        return ""
    return str(acct.get("uuid") or "")


def _set_key_uuid(kid: str, uuid: str) -> None:
    with _lock:
        doc = load(strict=True)
        for k in doc["keys"]:
            if k["id"] == kid:
                k["account_uuid"] = uuid
                save(doc)
                return


def resolve_key_identity(kid: str) -> str:
    """Best-effort: which account does this key belong to? Fetches the
    profile with the key's own token and records the uuid on the row.
    Returns the uuid, or "" (offline, expired, unknown row — none are
    errors; the row simply stays unattributed and routes as a distinct
    account until a later call succeeds)."""
    tok = tokens.get(kid)
    if not tok:
        return ""
    try:
        uuid = _uuid_from_profile(_resolve_via_profile(tok))
    except Exception:                          # noqa: BLE001 — offline/expired/429
        return ""
    if uuid:
        _set_key_uuid(kid, uuid)
    return uuid


def register_key(token: str) -> dict[str, Any]:
    """Register a pasted `claude setup-token` key as a secondary account row.

    ⚠ STORE FIRST, RESOLVE AFTER — the ordering is the user's standing
    ruling, not a preference. The CLI shows a minted token EXACTLY ONCE
    ("you won't be able to see it again"), so the write to the token store
    happens before anything can form an opinion about the value; the identity
    lookup runs afterwards, against a token that is already durable, and its
    failure costs nothing but a greyed-out-duplicate check that will retry.

    Idempotent on the VALUE: re-pasting a key that is already stored lands on
    its existing row (same id — the id IS a hash of the token) and keeps its
    place in the order. Returns `{"id", "created", "account_uuid"}`.
    """
    token = str(token or "").strip()
    if not token:
        raise ValueError("refusing to register an empty key")
    kid = key_for_token(token) or (
        "k" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12])
    tokens.put(kid, token)                     # ← durable before anything else
    created = False
    with _lock:
        doc = load(strict=True)
        if not any(k["id"] == kid for k in doc["keys"]):
            doc["keys"].append({"id": kid, "account_uuid": None})
            created = True
            save(doc)
    # the identity lookup, OUTSIDE the lock (it is a network call) and after
    # durability. `resolve_key_identity` re-reads the token it just stored.
    uuid = resolve_key_identity(kid)
    return {"id": kid, "created": created, "account_uuid": uuid or None}


def remove_key(kid: str) -> bool:
    """Delete a secondary account row AND its stored key. ⚠ Irreversible from
    orgtree's side — the CLI cannot show a token again, so re-adding means
    re-minting. Routing state for the row goes with it: a mark against a key
    that no longer exists is not this machine's state."""
    kid = str(kid or "")
    with _lock:
        doc = load(strict=True)
        keep = [k for k in doc["keys"] if k["id"] != kid]
        existed = len(keep) != len(doc["keys"])
        if existed:
            doc["keys"] = keep
            doc["usage_refreshes"].pop(kid, None)
            save(doc)
    forgot = tokens.forget(kid)
    return existed or forgot


def set_key_order(ids: list[str]) -> list[str]:
    """The secondary priority order. Unknown ids are dropped and known ones
    missing from the request are appended — a stale panel must not be able to
    delete a row by omitting it, and the order must stay a PERMUTATION of the
    known set (`dict.fromkeys` dedupes a double-submitted POST)."""
    with _lock:
        doc = load(strict=True)
        by_id = {k["id"]: k for k in doc["keys"]}
        new = list(dict.fromkeys(str(i) for i in ids if str(i) in by_id))
        new += [i for i in by_id if i not in new]
        doc["keys"] = [by_id[i] for i in new]
        save(doc)
        return new


# ------------------------------------------------------------- routing state
def _prune_expired(doc: dict[str, Any], now: float) -> None:
    """An expired mark IS capacity — readers already treat it so; writers
    physically drop it so the file states only what is currently true. Also
    sweeps marks for rows that no longer exist (`primary` always exists)."""
    known = {PRIMARY} | {k["id"] for k in doc["keys"]}
    ref = doc["usage_refreshes"]
    for acct in list(ref):
        if acct not in known or not isinstance(ref[acct], dict):
            del ref[acct]
            continue
        for tier in list(ref[acct]):
            try:
                if float(ref[acct][tier]) <= now:
                    del ref[acct][tier]
            except (TypeError, ValueError):
                del ref[acct][tier]
        if not ref[acct]:
            del ref[acct]


def record_limit(account: str, tier: str, refresh_at: float) -> bool:
    """The ONLY writer of routing state: this account's capacity for this
    tier is used up until `refresh_at`. Unknown accounts and unknown tiers
    are refused as no-ops returning False — a stale attribution (a row
    deleted mid-turn, an `api-key` lane, `key:unattributed`) must not be able
    to resurrect or invent a row."""
    account, tier = str(account or ""), str(tier or "").lower()
    if tier not in TIERS:
        return False
    with _lock:
        doc = load(strict=True)
        if account != PRIMARY and not any(
                k["id"] == account for k in doc["keys"]):
            return False
        now = time.time()
        _prune_expired(doc, now)
        ts = float(refresh_at)
        if ts <= now:
            return False                       # already refreshed: not a mark
        doc["usage_refreshes"].setdefault(account, {})[tier] = ts
        save(doc)
        return True


def _routing_order(doc: dict[str, Any], live_uuid: str) -> list[str]:
    """Priority order: the signed-in account first (skipped entirely when
    nobody is signed in — an ambient spawn with no login authenticates as
    nothing), then key rows in list order, MINUS any key that resolved to the
    same account as the live login (a duplicate lane, not an alternative)."""
    order: list[str] = [PRIMARY] if live_uuid else []
    for k in doc["keys"]:
        au = str(k.get("account_uuid") or "")
        if live_uuid and au and au == live_uuid:
            continue
        order.append(str(k["id"]))
    return order


def _resolve_in(doc: dict[str, Any], live_uuid: str, tier: str,
                now: float) -> dict[str, Any]:
    tier = str(tier or "").lower()
    order = _routing_order(doc, live_uuid)
    ref = doc["usage_refreshes"]

    def mark(acct: str) -> float | None:
        try:
            ts = float((ref.get(acct) or {}).get(tier))
        except (TypeError, ValueError):
            return None
        return ts if ts > now else None        # expired reads as capacity

    for acct in order:
        if mark(acct) is None:
            return {"account": acct, "available": True, "refresh_at": None}
    # nowhere has capacity: name the account that refreshes SOONEST — that is
    # where capacity next appears, where the dimmed chip sits, and where a
    # probing spawn goes (if it fails it re-marks itself; self-bounding)
    if order:
        best = min(order, key=lambda a: mark(a) or float("inf"))
        return {"account": best, "available": False, "refresh_at": mark(best)}
    return {"account": None, "available": False, "refresh_at": None}


def resolve(tier: str, now: float | None = None) -> dict[str, Any]:
    """WHERE prompts for this tier go, right now:
    `{"account": id|None, "available": bool, "refresh_at": epoch|None}`.
    The single rule every consumer shares — the spawn seam injects the token
    of exactly this answer, and the panel draws the tier's chip beside exactly
    this row, so the two cannot disagree."""
    now = time.time() if now is None else now
    return _resolve_in(load(), live_identity()["uuid"], tier, now)


def _iso(epoch: Any) -> str | None:
    if not isinstance(epoch, (int, float)):
        return None
    return (_dt.datetime.fromtimestamp(epoch, _dt.timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def fallback_ordinal(doc: dict[str, Any], account: str) -> int | None:
    """This row's 1-based position among the key rows, or None if `account`
    is not a key row (the primary login, `api-key`, `key:unattributed`, an
    unknown id). ONE definition of "fallback N", shared by the panel, the
    per-row usage modal and the serving label — three surfaces that would
    otherwise each count for themselves and disagree the moment a row is
    deleted."""
    if not account or account == PRIMARY:
        return None
    for i, k in enumerate(doc["keys"]):
        if k["id"] == account:
            return i + 1
    return None


def serving_label(account: str, *, with_uuid: bool = True) -> str | None:
    """"fallback 2 · <account uuid>" for a turn served by a key row, else None
    (user ruling 2026-08-25: cite the fallback's NUMBER alongside its uuid,
    and only when an agent is actually running off a fallback — the primary
    login and the api-key lane say nothing here).

    ⚠ `with_uuid=False` for anything a KIOSK visitor can reach. D-145 keeps
    account identity off the public side by freezing `/api/accounts` whole,
    but the node payload this label rides on is reachable from a kiosk, so
    the uuid is dropped there rather than the whole label. The ordinal is
    positional and says nothing about who the account is.

    Degrades to the bare ordinal when identity has not resolved yet: "fallback
    2" is still true, and a row whose profile lookup failed must not vanish
    from the readout that explains a running turn."""
    doc = load()
    n = fallback_ordinal(doc, account)
    if n is None:
        return None
    if not with_uuid:
        return f"fallback {n}"
    row = next((k for k in doc["keys"] if k["id"] == account), None)
    uuid = str((row or {}).get("account_uuid") or "")
    return f"fallback {n} · {uuid}" if uuid else f"fallback {n}"


def readout() -> dict[str, Any]:
    """What the panel renders. One load, one live read, so the rows and the
    chip assignments are a consistent snapshot."""
    doc = load()
    live = live_identity()
    now = time.time()
    dup = {k["id"] for k in doc["keys"]
           if live["uuid"] and str(k.get("account_uuid") or "") == live["uuid"]}
    assignments = {}
    for tier in TIERS:
        r = _resolve_in(doc, live["uuid"], tier, now)
        assignments[tier] = {"account": r["account"],
                             "available": r["available"],
                             "refresh_at": _iso(r["refresh_at"])}
    return {
        "version": VERSION,
        "primary": {"signed_in": bool(live["uuid"]),
                    "email": live["email"] or None},
        # `account_uuid` is IDENTITY, never credential (user ruling
        # 2026-08-25: render each registered key's uuid in the list). It is
        # already what the duplicate check compares, and `/api/accounts` is
        # frozen whole for kiosk visitors, so this adds no public surface.
        # `ordinal` rides along so the panel's "fallback N" and the desk's
        # serving label come from ONE count rather than two.
        "keys": [{"id": k["id"], "duplicate": k["id"] in dup,
                  "ordinal": i + 1,
                  "account_uuid": str(k.get("account_uuid") or "") or None}
                 for i, k in enumerate(doc["keys"])],
        "assignments": assignments,
    }


# ------------------------------------------------------------ per-account usage
def account_usage(account: str) -> dict[str, Any]:
    """One account's usage-limit standing, for the panel's per-row button and
    the header modal's list.

    `primary` reads the host subscription through the shared `limits` cache —
    a refreshed OAuth access token with the `user:profile` scope, which is why
    that half works at all.

    ⚠ A KEY ROW MAKES NO NETWORK CALL AT ALL (D-147). `claude setup-token`
    keys are inference-only and can never read usage, so the row answers
    `unsupported` from local state. Was. two round-trips per click — the usage
    fetch, plus a lazy `resolve_key_identity` retry justified by "the user is
    already spending a network round-trip on this click". That justification
    died with the round-trip, and the retry was itself a forbidden request:
    the profile endpoint wants the same scope the usage endpoint does."""
    account = str(account or "")
    live = live_identity()
    if account == PRIMARY:
        data = limits.fetch()
        return {"account": PRIMARY, "duplicate": False,
                "label": live["email"] or "signed-in account",
                **{k: v for k, v in data.items() if k != "account"}}
    doc = load()
    row = next((k for k in doc["keys"] if k["id"] == account), None)
    if row is None:
        return {"account": account, "label": account, "duplicate": False,
                "available": False, "error": "no such key"}
    tok = tokens.get(account)
    if not tok:
        return {"account": account, "label": account, "duplicate": False,
                "available": False, "error": "no stored key for this row"}
    dup = bool(live["uuid"]
               and str(row.get("account_uuid") or "") == live["uuid"])
    ordinal = fallback_ordinal(doc, account)      # the one count, shared
    label = f"fallback {ordinal}" if ordinal else "fallback"
    # ⚠ WE DO NOT ASK. A `claude setup-token` key is INFERENCE-ONLY and the
    # usage endpoint needs the `user:profile` scope it will never carry, so
    # every such request was forbidden before it left this machine — see
    # D-147 for the evidence out of the CLI's own binary. This used to call
    # `limits.fetch_for_token`, which is how one key row earned an hour-long
    # rate-limit window by being politely asked the same forbidden question
    # on every panel open. Backoff would have been the wrong shape: the fix
    # for a request that must never be made is not to make it.
    return {"account": account, "label": label, "duplicate": dup,
            "available": False, "unsupported": True,
            "error": "usage limits can't be read for a `claude setup-token` "
                     "key — these are inference-only, and the usage endpoint "
                     "needs a permission they are never granted. Nothing is "
                     "wrong with this key; re-minting it would not help."}


def usage_all() -> dict[str, Any]:
    """Every account's standing, primary first then keys in priority order —
    the header usage modal's list (user ruling 2026-08-25: the overall usage
    button shows every registered account, fallbacks included)."""
    doc = load()
    out = [account_usage(PRIMARY)]
    out += [account_usage(k["id"]) for k in doc["keys"]]
    return {"accounts": out}
