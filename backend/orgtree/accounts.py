"""The account registry — WHO this install may bill, never HOW to bill them.

Phase 1 of the multi-account feature (two Claude Max subscriptions used
SERIALLY, primary-first). This module owns four things and deliberately
nothing else:

  · the registry     — identities known to this install, in waterfall order
  · passive adoption — notice whoever is already logged in, and change nothing
  · the pin          — an org may be nailed to one account by hand
  · the readout      — what the panel renders

⚠ THE INVARIANT THIS FILE EXISTS TO HOLD: **the registry stores IDENTITY, never
CREDENTIALS.** No access token, no refresh token, no Authorization header ever
reaches `accounts.json`. Tokens live exactly where they already live — the
CLI's own credentials store — and this file records only which *account* a
credential resolved to. `_reject_secrets` enforces it on every write and
raises rather than redacting, because a registry that silently strips a token
teaches its callers that passing one is fine.

Identity is keyed on `account.uuid`, which is the discriminator the probe
battery proved: it is IDENTICAL across a token refresh of one account and
DIFFERENT between accounts. Token bytes cannot key anything — they rotate, and
a rotation revokes its predecessor immediately.

What this module does NOT do, on purpose: it never writes the credentials
store, never refreshes a grant, never selects an account for a turn, and never
switches a lane. Selection and failover are Phase 2. See D-144 — on today's
supervisor a mid-turn auth rejection is unclassified and terminal, so nothing
here should be read as making failover work.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
import threading
import time
from typing import Any, Callable

from . import store, subproxy

REGISTRY_NAME = "accounts.json"
VERSION = 1
_lock = threading.RLock()

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
    # ⚠ any long unbroken token-ish run, WITHOUT a character-class condition.
    # This used to require ≥1 uppercase AND ≥1 digit, which made every
    # all-lowercase secret invisible to it — hex and base32 are exactly that
    # shape, and `label` is a free-form user string that reaches save()
    # (review 2026-08-24: a 72-char lowercase run reached disk). No legitimate
    # label is 40 characters with no separator, so refusing is the safe side.
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
    return {"version": VERSION, "accounts": {}, "order": [], "pins": {}}


class RegistryUnreadable(RuntimeError):
    """The registry file exists but could not be understood."""


def load(*, strict: bool = False) -> dict[str, Any]:
    """The registry, or a blank one.

    A corrupt file reads as blank for READERS: taking the panel down over it
    is a worse failure than showing nothing. But `strict=True` — which every
    read-modify-WRITE cycle uses — raises instead.

    ⚠ Why the split (review 2026-08-24): blank-on-corrupt is safe to *read*
    and catastrophic to *write back*. `load()` → mutate → `save()` over an
    unreadable file silently replaced every hand-set label, the whole
    waterfall order and every pin with an empty registry, with no backup — and
    a `VERSION` bump would have done it to every install at once, because a
    version mismatch took the same branch. The docstring used to call the cost
    "re-adopting"; re-adoption cannot restore a label, an order or a pin. The
    unreadable file is now left ON DISK, untouched, so it can be recovered by
    hand.
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
    if not isinstance(doc, dict) or doc.get("version") != VERSION:
        if strict:
            raise RegistryUnreadable(
                f"{registry_path()} is version {doc.get('version') if isinstance(doc, dict) else '?'!r}, "
                f"not {VERSION} — refusing to overwrite it; migrate it first")
        return _blank()
    for key, default in (("accounts", {}), ("order", []), ("pins", {})):
        if not isinstance(doc.get(key), type(default)):
            doc[key] = default
    return doc


def save(doc: dict[str, Any]) -> None:
    """Atomic tmp+replace, mirroring store.save_org — including the fsync,
    because a half-written registry reads as 'no accounts known' and would
    silently re-adopt."""
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


# ------------------------------------------------------------------- identity
def mask_email(addr: str | None) -> str | None:
    """`someone@example.com` → `s*****e@example.com`. The panel needs to tell
    two accounts apart; it does not need the full address to do it, and the
    registry is read by more code than the credentials store is."""
    if not addr or "@" not in addr:
        return None
    local, _, domain = addr.partition("@")
    if len(local) <= 2:
        return f"{local[:1]}*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


def identity_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """`GET /api/oauth/profile` → the identity fields the registry keeps.
    Everything credential-shaped in that response is dropped here, at the
    boundary, rather than being carried inward and filtered later."""
    acct = profile.get("account") or {}
    org = profile.get("organization") or {}
    uuid = acct.get("uuid")
    if not uuid:
        raise ValueError("profile carries no account.uuid — cannot key an "
                         "account on anything else (token bytes rotate)")
    return {
        "uuid": uuid,
        "org_uuid": org.get("uuid"),
        "email_masked": mask_email(acct.get("email")),
        "subscription_type": ("max" if acct.get("has_claude_max") else
                              "pro" if acct.get("has_claude_pro") else None),
        "rate_limit_tier": org.get("rate_limit_tier"),
        "account_created_at": acct.get("created_at"),
    }


def upsert(identity: dict[str, Any], *, source: str = "adopted",
           label: str | None = None) -> dict[str, Any]:
    """Record an identity, preserving anything the user has since set by hand.
    Re-adopting the same account must never clobber its label or its place in
    the waterfall order — adoption runs on a schedule, the user edits once."""
    with _lock:
        doc = load(strict=True)
        uuid = identity["uuid"]
        prev = doc["accounts"].get(uuid) or {}
        now = time.time()
        rec = {**prev, **identity,
               "source": prev.get("source", source),
               "first_seen": prev.get("first_seen", now),
               "last_seen": now}
        if label is not None:
            rec["label"] = label
        elif "label" not in rec:
            rec["label"] = rec.get("email_masked") or uuid[:8]
        doc["accounts"][uuid] = rec
        if uuid not in doc["order"]:
            doc["order"].append(uuid)          # newly seen goes last, never first
        save(doc)
        return rec


# ---------------------------------------------------------- passive adoption
class LiveStoreWritten(RuntimeError):
    """The credentials store changed while we were reading it."""


# The CLI's own config, beside `subproxy.CREDS` but a DIFFERENT file: this one
# holds identity METADATA (`oauthAccount`) and no credential at all. Module
# level so a test can point it at a fixture without going near a real login.
LIVE_CONFIG = os.path.expanduser("~/.claude.json")


def live_account_uuid() -> str:
    """WHO this machine is signed in as, offline — or "" if that is unknown.

    ⚠ THIS EXISTS BECAUSE "NOT CURRENTLY SELECTED" IS NOT "A DIFFERENT
    ACCOUNT". An org with no selection runs on the AMBIENT login, and the
    ambient account can perfectly well also sit in the registry with a pasted
    token against it — passive adoption puts it there, by design, because
    `adopt_live` harvests exactly whoever is logged in. Failing over from
    ambient to that account is a switch to ITSELF: same subscription, same
    limit, and a confident row saying capacity was restored. That happened
    live on 2026-08-24 21:20Z — the re-driven turn hit the identical session
    limit 4.2s later, and the org recovered only when the limit expired.

    Read from the CLI's CONFIG (`oauthAccount.accountUuid`), never from the
    credentials store: this must stay a metadata read, cheap enough to call on
    a failure path and incapable of touching a token. Returns "" when nobody
    is logged in, when the file is missing or unparseable, or when the field
    is absent — see `supervisor.alternate_account_choice` for what "" means
    there (it degrades to the old behaviour, which is why the ambient account
    must ALSO be excluded by any caller that can afford to be stricter).
    """
    try:
        with open(LIVE_CONFIG, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(doc, dict):
        return ""
    acct = doc.get("oauthAccount")
    if not isinstance(acct, dict):
        return ""
    return str(acct.get("accountUuid") or "")


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


def adopt_live(resolver: Callable[[str], dict[str, Any]] | None = None,
               ) -> dict[str, Any] | None:
    """PASSIVE adoption: notice whoever is logged in, record who they are, and
    leave the credentials store exactly as found.

    'Passive' is the whole contract, so it is enforced rather than intended:
    the store's mtime and size are sampled before and after, and a change
    raises `LiveStoreWritten`. That guard is why this function may be called
    from a request handler without the user wondering whether their login
    survived it. It never refreshes a grant — an expired token here yields
    None and the caller re-adopts after the user logs in again.
    """
    creds = subproxy.CREDS
    try:
        before = os.stat(creds)
    except OSError:
        return None                            # nobody logged in — not an error
    try:
        with open(creds, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    token = ((doc.get("claudeAiOauth") or {}).get("accessToken"))
    if not token:
        return None
    try:
        profile = (resolver or _resolve_via_profile)(token)
    except Exception:                          # noqa: BLE001 — offline/expired/rate-limited
        return None
    finally:
        # ⚠ the store DISAPPEARING is a change, not an absence (review
        # 2026-08-24). `os.stat` used to raise FileNotFoundError straight out
        # of this `finally`, which `api.py` maps to nothing — so a logout
        # during adoption, the single most visible thing that can happen to
        # this file, produced a 500 rather than the 409 the docstring
        # promises. Any failure to re-stat is treated as "it changed".
        try:
            after: os.stat_result | None = os.stat(creds)
        except OSError:
            after = None
        if after is None or (after.st_mtime_ns, after.st_size) != (
                before.st_mtime_ns, before.st_size):
            raise LiveStoreWritten(
                f"{creds} changed during passive adoption — adoption must "
                f"never write the credentials store")
    return upsert(identity_from_profile(profile), source="adopted")


# -------------------------------------------------------------- order and pin
def set_order(order: list[str]) -> list[str]:
    """The waterfall order, primary first. Unknown uuids are dropped and known
    ones missing from the request are appended — a stale panel must not be
    able to delete an account from the registry by omitting it."""
    with _lock:
        doc = load(strict=True)
        known = list(doc["accounts"].keys())
        # dedupe as well as filter (review 2026-08-24): the endpoint takes a
        # bare list[str] with no uniqueness constraint, so a stale or
        # double-submitted panel POST could send ["A","A","B"] — which the
        # filter alone preserves, and `readout()` then renders A twice. The
        # order must stay a PERMUTATION of the known set, not merely a subset
        # of it. `dict.fromkeys` keeps first-seen position.
        new = list(dict.fromkeys(u for u in order if u in doc["accounts"]))
        new += [u for u in known if u not in new]
        doc["order"] = new
        save(doc)
        return new


def primary() -> str | None:
    doc = load()
    return doc["order"][0] if doc["order"] else None


def set_pin(org_slug: str, uuid: str | None) -> str | None:
    """Nail one org to one account, or pass None to clear it. Pinning an
    unknown account is refused loudly: a pin that silently does not apply is
    indistinguishable from a pin that does, which is the whole failure mode
    this feature is supposed to make visible."""
    with _lock:
        doc = load(strict=True)
        if uuid is None:
            doc["pins"].pop(org_slug, None)
        elif uuid not in doc["accounts"]:
            raise KeyError(f"cannot pin {org_slug!r} to unknown account "
                           f"{uuid!r} — adopt it first")
        else:
            doc["pins"][org_slug] = uuid
        save(doc)
        return doc["pins"].get(org_slug)


def relabel(uuid: str, label: str) -> str:
    """Rename an account for the panel.

    ⚠ Lives here rather than in the endpoint, and takes `_lock`, because the
    endpoint's own load→mutate→save was the ONE mutator running outside it
    (review 2026-08-24). Sync FastAPI endpoints run in a threadpool and
    `adopt_live` is dispatched with `run_in_threadpool`, so a scheduled
    adoption concurrent with a rename could be lost when the rename saved its
    stale document — the newly adopted account simply vanishing.
    """
    label = label.strip()
    if not label:
        raise ValueError("a label cannot be empty")
    with _lock:
        doc = load(strict=True)
        if uuid not in doc["accounts"]:
            raise KeyError(uuid)
        doc["accounts"][uuid]["label"] = label
        save(doc)
        return label


def get_pin(org_slug: str) -> str | None:
    return load()["pins"].get(org_slug)


def _iso(epoch: Any) -> str | None:
    """Epoch seconds → ISO-8601 Z. The record keeps epoch (cheap to compare);
    the WIRE speaks ISO, because every other timestamp this API emits does and
    the frontend's `ago()` parses strings, not numbers."""
    if not isinstance(epoch, (int, float)):
        return None
    return (_dt.datetime.fromtimestamp(epoch, _dt.timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def _wire(rec: dict[str, Any], uuid: str) -> dict[str, Any]:
    return {**rec, "uuid": uuid,
            "first_seen": _iso(rec.get("first_seen")),
            "last_seen": _iso(rec.get("last_seen"))}


def readout() -> dict[str, Any]:
    """What the panel renders. `effective` is the account a turn WOULD use if
    selection existed — it does not yet (Phase 2), so the panel labels it as
    intent, not as fact."""
    doc = load()
    return {
        "version": VERSION,
        "accounts": [_wire(doc["accounts"][u], u)
                     for u in doc["order"] if u in doc["accounts"]],
        "primary": doc["order"][0] if doc["order"] else None,
        "pins": dict(doc["pins"]),
        # Phase 1 ships the registry, not the switch. Stated in the payload so
        # a panel cannot imply failover works by rendering this. See D-144.
        "selection_active": False,
    }
