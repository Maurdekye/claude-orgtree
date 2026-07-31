"""Multi-org persistence (№36). One JSON file per org under the DATA root.

Data root is ~/orgtree (NOT ~/.claude — spike finding 4: Claude tools refuse writes into
~/.claude as sensitive, and node scratch dirs live beside the ledger data):

    ~/orgtree/
      orgs/<slug>.json          the ledger documents
      scratch/<slug>/<node>/    node working dirs (flat per §7.6; made by the supervisor)

Writes are atomic (tmp + os.replace).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time

from .ledger import LedgerError, Org, slugify

DATA_ROOT = os.environ.get("ORGTREE_DATA", os.path.expanduser("~/orgtree"))

# Coarse per-process guard around load-modify-save cycles: API ops and the
# supervisor's notice drain both rewrite org docs; without this a stale copy
# could resurrect just-delivered notices (double delivery).
DOC_LOCK = threading.RLock()


def _orgs_dir() -> str:
    d = os.path.join(DATA_ROOT, "orgs")
    os.makedirs(d, exist_ok=True)
    return d


def org_path(slug: str) -> str:
    return os.path.join(_orgs_dir(), slug + ".json")


def scratch_root(slug: str) -> str:
    return os.path.join(DATA_ROOT, "scratch", slug)


def list_orgs() -> list[dict]:
    out = []
    for f in sorted(os.listdir(_orgs_dir())):
        if not f.endswith(".json"):
            continue
        try:
            doc = json.load(open(os.path.join(_orgs_dir(), f), encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        live = sum(1 for n in doc.get("nodes", {}).values() if n.get("state") == "live")
        out.append({"slug": doc.get("slug", f[:-5]), "name": doc.get("name", f[:-5]),
                    "nodes": len(doc.get("nodes", {})), "live": live,
                    "created": doc.get("created")})
    return out


def load_org(slug: str) -> Org:
    p = org_path(slug)
    if not os.path.exists(p):
        raise LedgerError(f"no such org: {slug!r}")
    return Org(json.load(open(p, encoding="utf-8")))


REVISION = 0   # bumped on every save — cheap change detection for pollers
               # (the extern long-poll gates its full-doc rescans on this)


def save_org(org: Org) -> None:
    global REVISION
    p = org_path(org.d["slug"])
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(org.d, f, indent=2)
    os.replace(tmp, p)
    REVISION += 1


def workspace_dir(slug: str) -> str:
    return os.path.join(DATA_ROOT, "workspaces", slug)


def create_org(name: str, extra_dirs: list[str] | None = None,
               permission_mode: str = "acceptEdits") -> Org:
    """Every org gets its own fresh workspace dir, minted here. Pre-existing
    directories are an ADVANCED grant (`extra_dirs`) — appended after the workspace
    in the org's default capability set."""
    slug = slugify(name)
    if os.path.exists(org_path(slug)):
        raise LedgerError(f"org {slug!r} already exists")
    ws = os.path.normpath(workspace_dir(slug))
    os.makedirs(ws, exist_ok=True)
    dirs = [ws] + [os.path.normpath(d) for d in (extra_dirs or []) if d.strip()]
    org = Org.create(name, dirs, permission_mode, workspace=ws)
    save_org(org)
    return org


def delete_org(slug: str) -> None:
    """Gap audit №16: one confirmed hover-click used to `os.remove` the whole
    org — structure, charters, mailboxes, event history. The motto reserves
    hard stops for protecting the user's data, so delete is now a RENAME into
    <data>/deleted/; putting the file back in orgs/ IS the restore."""
    p = org_path(slug)
    if not os.path.exists(p):
        raise LedgerError(f"no such org: {slug!r}")
    trash = os.path.join(DATA_ROOT, "deleted")
    os.makedirs(trash, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    os.replace(p, os.path.join(trash, f"{slug}-{stamp}.json"))
