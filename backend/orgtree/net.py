# pyright: strict
"""@net: — the mail-hub client (F-06).

Phase A (this file's current scope): the org's permanent network IDENTITY and
its hub configuration. The poll/spool daemon lands in Phase C.

Identity model (docs/mailserver-spec.md §3, all user-ruled):
  secret      = secrets.token_hex(16)        minted BY the org at creation
  fingerprint = sha256(secret)               the hub stores only this
  slug        = f"{org}.{username}.{fingerprint[:6]}"   minted ONCE, persisted,
                never recomputed — the address survives moves and renames.

⚠ Secret hygiene: the secret lives in the org doc (`net_identity`) and is
returned by exactly one loopback-admin endpoint (`GET /api/orgs/{slug}/net`).
It must never enter a tree payload, an agent's context, a log line, or a URL —
on the wire it rides headers only. Kiosk orgs mint NO identity at all: they are
sealed from the outside world, and an identity that does not exist cannot leak
(stronger than filtering rosters).
"""

from __future__ import annotations

import getpass
import hashlib
import re
import secrets
import uuid
from typing import TYPE_CHECKING, Any

from .ledger import now

if TYPE_CHECKING:
    from .ledger import Org

# the local hub's default address; overridable via defaults.json
# ("net_hub_address" — translated into the "local" hub entry at org creation,
# never written raw into an org doc)
DEFAULT_HUB_ADDRESS = "http://127.0.0.1:7370"
LOCAL_HUB_ID = "local"    # the implicit same-machine hub; per-hub state keys
                          # on this id, so its ADDRESS may be edited freely


def _sanitize_user(user: str) -> str:
    """The username is human-readable decoration in the slug (uniqueness comes
    from the fingerprint suffix). Dots separate the slug's three parts, so the
    username must never contain one."""
    s = re.sub(r"[^A-Za-z0-9_-]", "-", user).strip("-").lower()
    return s or "user"


def mint_identity(org: "Org") -> dict[str, Any] | None:
    """Mint the org's permanent network identity. Idempotent — an existing
    identity is returned untouched (the slug is IMMUTABLE for the org's
    lifetime, user ruling). Returns None for kiosk orgs, which have no
    identity by design. Caller holds DOC_LOCK and saves."""
    if org.d.get("kiosk"):
        return None
    ident = org.d.get("net_identity")
    if isinstance(ident, dict) and ident.get("secret"):
        return ident
    secret = secrets.token_hex(16)               # the repo's credential pattern
    fp = hashlib.sha256(secret.encode()).hexdigest()
    ident = {
        "secret": secret,
        "fingerprint": fp,
        "slug": f"{org.d['slug']}.{_sanitize_user(getpass.getuser())}.{fp[:6]}",
        "minted_at": now(),
    }
    org.d["net_identity"] = ident
    return ident


def hub_entries(autoconnect: bool, remote_addresses: list[str],
                local_address: str = DEFAULT_HUB_ADDRESS) -> list[dict[str, Any]]:
    """Build the initial `net_hubs` list for a new org. The local hub entry
    exists only under autoconnect; remote hubs are explicit addresses, each
    with a client-minted id so per-hub state survives address edits. Hub
    NAMES are discovered on connect (user ruling), never set here."""
    hubs: list[dict[str, Any]] = []
    if autoconnect:
        hubs.append({"id": LOCAL_HUB_ID,
                     "address": local_address.strip() or DEFAULT_HUB_ADDRESS,
                     "enabled": True})
    for a in remote_addresses:
        a = a.strip()
        if a:
            hubs.append({"id": uuid.uuid4().hex[:8], "address": a,
                         "enabled": True})
    return hubs
