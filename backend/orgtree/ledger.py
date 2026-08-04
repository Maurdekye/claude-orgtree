# pyright: strict
"""The credit ledger: nodes, the budget invariant, and the seven operations.

Semantics ratified in PLAN.md (all §-references point there):

    free(N) = grant(N) - SUM over live children C of ( seat_cost(C) + grant(C) )   >= 0

The user IS the org root (§7.4): there is no root node. Top-level nodes have parent None,
and the reserved actor id "user" has infinite free and unconditional authority.

Credits are occupancy, not spend (§3.4). A credit is not a dollar.

Stranding (§4.4, corrected during implementation): a warning fires whenever an operation
REDUCES a node's free across an archived dependent's rehire cost. Promote/demote leave every
free unchanged (the release and acquire paths cancel hop by hop), so moves cannot strand —
the ops that can are hire (the payer), forcible hire (the actor), rehire (the parent, for its
other archived children), reallocate(-Δ), and switch_model to a pricier tier (the chain).

Directory access (№30) is an inherited capability set, NOT a budget: a node may hold only
dirs its parent holds (top-level nodes are user-granted and unconstrained). Nothing conserves;
revoke is explicit; re-parenting intersects the moved subtree's dirs with the new chain.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Literal, cast

from .schema import (AudienceGrant, DirGrant, MailEntry, NodeDoc,
                     OrgDoc, OrgInboxEntry, ToolGrant, UserMailEntry)

# §3.1 — derived from published API pricing (output:input is 5:1 for every model, so the
# scale is not a judgment call). Sonnet is 3, not its introductory 2 (expires 2026-08-31).
TIERS: Final[dict[str, int]] = {"fable": 10, "opus": 5, "sonnet": 3, "haiku": 1}

# №34 runaway insurance, and NOTHING else (user ruling 2026-08-04): "no need to
# have any practical limit other than to prevent infinite recursion from a bug
# that spawns unlimited subagents". Both were low enough to be felt as design
# constraints (10 and 256); at these values a human org never meets them and a
# runaway still terminates. Both are per-org overridable.
MAX_DEPTH: Final = 1024
MAX_CHILDREN: Final = 1024

# §5 — full model ids only; aliases drift (spike: 'sonnet' resolved to sonnet-4-5).
MODELS: Final[dict[str, str]] = {
    "fable": "claude-fable-5",
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}

# A TIER is a price band — four of them, four chips. A model VERSION is a
# subcategory INSIDE a tier (user ruling 2026-08-04: "the 4 chips should
# represent the 4 tiers. individual model versions are a subcategory which
# should only be accessible within the gear menu if the user desires to change
# it"). Choosing one never touches the seat cost, the budget, or anything the
# kiosk ceiling inspects — it decides one thing: which `--model` id the CLI is
# handed. A first attempt made Opus 4.8 a fifth TIER, which put a fifth chip on
# the canvas and a fifth price band in every table; this is that, corrected.
#
# The KEY is what a node records and the gear shows; the VALUE is the CLI id.
# The tier's entry in MODELS above remains the default, so a node with no
# version recorded behaves exactly as before.
# ⚠ ids verified against the pinned CLI with a real call (2026-08-04):
# `claude-opus-4-8` answers; `claude-opus-4.8` and `opus-4-8` are refused.
MODEL_VERSIONS: Final[dict[str, dict[str, str]]] = {
    "opus": {"5": "claude-opus-5", "4.8": "claude-opus-4-8"},
}

# Actors are one of three KINDS — user, system, agent — not one string namespace.
# The non-agent kinds use @-prefixed sentinels, which slugify() can never produce,
# so agent NAMES are fully unrestricted (a node may be called "user" or "system").
USER: Final = "@user"      # the org root: infinite free, unconditional authority (§7.4)
SYSTEM: Final = "@system"  # the ledger's own hand (fable-limit policy, reconciliation)
EXTERN: Final = "@extern"  # the ORG INBOX: the org's single face to the outside world
                    # (chatq sessions, other orgs). An audience whose grantor is
                    # EXTERN lets a sub-level agent read/answer outside mail.


def actor_kind(actor: str) -> str:
    if actor == USER:
        return "user"
    if actor == SYSTEM:
        return "system"
    return "agent"

VIS_LEVELS: Final = ("self", "team", "subtree", "full")   # org-structure knowledge tiers
TOOL_KEYS: Final = ("bash", "web", "edit", "subagents")   # the built-in tool switches
# permission_mode rank order (kiosk-ceiling spec §2): later = more permissive
PM_LEVELS: Final = ("default", "acceptEdits", "bypassPermissions")


def norm_tools(t: Mapping[str, Any] | None) -> ToolGrant:
    """Normalize a tool grant: four built-in switches + an MCP server name list.
    "*" in mcp = every registered server, present AND future (collapses the list)."""
    t = t or {}
    out: dict[str, Any] = {k: bool(t.get(k, True)) for k in TOOL_KEYS}
    out["mcp"] = sorted({str(s) for s in t.get("mcp", []) if s})
    if "*" in out["mcp"]:
        out["mcp"] = ["*"]
    return cast(ToolGrant, out)


def expand_mcp(granted: Iterable[str] | None, ceiling_mcp: Iterable[str] | None,
               registry: Iterable[str] | None) -> list[str]:
    """Build-time MCP expansion (ceiling spec §6, deliberately PURE — no env,
    no engine — so the suite pins it directly). "*" = the whole registry; the
    effective set is expand(granted) ∩ expand(ceiling). ceiling_mcp None = no
    ceiling (a normal org). Miss the intersection and a kiosk with a list
    ceiling still hands over every server through the "*" default path."""
    reg = set(registry or [])
    g = reg if "*" in (granted or []) else set(granted or []) & reg
    if ceiling_mcp is not None:
        c = reg if "*" in ceiling_mcp else set(ceiling_mcp) & reg
        g = g & c
    return sorted(g)


def norm_dirs(dirs: Iterable[Any] | None) -> list[DirGrant]:
    """Normalize dir grants to [{path, mode}] — strings default to read/write."""
    out: list[DirGrant] = []
    seen: set[str] = set()
    for d in dirs or []:
        if isinstance(d, str):
            d = {"path": d, "mode": "rw"}
        path = d.get("path", "").strip()
        mode = d.get("mode", "rw")
        if not path or path in seen or mode not in ("rw", "ro"):
            continue
        seen.add(path)
        out.append({"path": path, "mode": mode})
    return out


class LedgerError(ValueError):
    """Raised when an operation violates a precondition. Message is user-facing."""


def now() -> str:
    # millisecond resolution (user ruling 2026-07-31): second-resolution stamps
    # made same-second events unorderable — the extern reply cursor had to fall
    # back to inbox position. String comparison still works: same format, more
    # digits. (Transient quirk: within one second, OLD "…:00Z" stamps sort
    # AFTER new "…:00.123Z" ones — harmless across the format transition.)
    d = datetime.now(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise LedgerError("name is mandatory and must contain letters or digits (§4.7)")
    return slug


class Org:
    """One organization: a node tree, its audiences/notices, and an event log.

    Pure bookkeeping — no processes, no I/O. Persistence lives in store.py; the
    supervisor drives sessions elsewhere. Every mutating op takes `actor` (a node id or
    USER) and enforces authority + budget preconditions before touching state.
    """

    def __init__(self, doc: OrgDoc) -> None:
        self.d: OrgDoc = doc
        # migrate older docs in place: dir grants gain modes; scopes gain tool sets
        # (pre-schema docs — the loop handles keys NodeDoc no longer declares)
        for i, n in enumerate(cast("dict[str, dict[str, Any]]",
                                   self.d.get("nodes", {})).values()):
            sc = n.setdefault("scope", {})
            sc["add_dirs"] = norm_dirs(sc.get("add_dirs"))
            if "tools" not in sc:
                sc["tools"] = norm_tools({"bash": sc.pop("bash", True), "mcp": []})
            else:
                sc["tools"] = norm_tools(sc["tools"])
            # default leans toward visibility, not opaque invisibility (user ruling)
            sc.setdefault("org_visibility", "full")
            sc.setdefault("permission_mode", self.d.get("permission_mode", "acceptEdits"))
            n.setdefault("ui_order", float(i))
            # user ruling 2026-07-31: `purpose` is dropped — charter is the one
            # role statement. Migration folds an old purpose into an empty
            # charter (dropping it silently would strip live agents' identity)
            old_purpose = n.pop("purpose", None)
            if old_purpose and not n.get("charter"):
                n["charter"] = old_purpose
            n.setdefault("charter", None)
            # pre-unification relic: queued texts now persist as mailbox mail
            n.pop("queued_msgs", None)
        if self.d.get("fable_limit_policy") in (None, "retire"):
            self.d["fable_limit_policy"] = "halt"   # 'retire' dropped by user ruling
        if self.d.get("fable_filter_policy") not in ("halt", "opus"):
            self.d["fable_filter_policy"] = "halt"  # content-filter flags (user spec)
        # org-wide agent defaults for hires that don't state them (user hires):
        # every capability enabled — all switches + all MCP servers + full org
        # visibility + the org's folders (user ruling)
        self.d["default_tools"] = norm_tools(
            self.d.get("default_tools", {"mcp": ["*"]}))
        if self.d.get("default_visibility") not in VIS_LEVELS:
            self.d["default_visibility"] = "full"
        self.d.pop("default_dirs", None)   # superseded: org dirs carry modes now
        self.d.setdefault("default_top_grant", 50)   # user ruling: 50 by default
        # §4.6 cost-bubbling toggles (user spec, both ON by default): hires /
        # allocations may pull shortfalls up the chain; off = the payer must
        # afford the action from its own free credits
        self.d.setdefault("cascade_hire", True)
        self.d.setdefault("cascade_alloc", True)
        self.d.setdefault("credit_requests", [])     # top-level asks to the user
        self.d.setdefault("compact_at", 0.80)        # compaction ratio, ≤ 0.95 hard
        # kiosk v2 (user vision): per-org public exposure via a preauthenticated
        # secret-URL token; caps live here, not in env vars. None = never a kiosk.
        self.d.setdefault("kiosk", None)             # {enabled, token, credits,
                                                     #  spend_limit, storage_limit_mb}
        # kiosk permission ceiling (consensus spec §3): pre-ceiling kiosk docs
        # get one MINTED = "what this org already does" — the union of every
        # node's scope ∪ the org's dirs ∪ default_tools. Nothing running is
        # swept; future escalation caps at the status quo; the admin is told.
        _k = self.d.get("kiosk")
        if _k is not None:
            _k.setdefault("auto_raise", False)
            # user report 2026-07-31: the inherited 50-credit default grant,
            # kiosk-clamped to "everything remaining", made the FIRST hire
            # swallow the whole pool — no second agent could ever spawn and
            # the reason was opaque. A default the cap can't even hold was
            # never a chosen default: zero it. In a capped org, grants are
            # deliberate drags; a sub-cap default the admin set survives.
            _cap = int(_k.get("credits") or 0)
            if _cap and int(self.d.get("default_top_grant") or 0) >= _cap:
                self.d["default_top_grant"] = 0
            if not _k.get("max_scope"):
                dt = self.d.get("default_tools") or {}
                mt = norm_tools(dt)
                md = {d["path"]: d["mode"] for d in norm_dirs(self.d.get("dirs"))}
                dv = self.d.get("default_visibility", "full")
                vr = VIS_LEVELS.index(dv) if dv in VIS_LEVELS else len(VIS_LEVELS) - 1
                pr = PM_LEVELS.index("acceptEdits")
                for n in self.nodes.values():
                    sc = n.get("scope") or {}
                    t = sc.get("tools") or {}
                    for key in TOOL_KEYS:
                        if t.get(key, True):
                            mt[key] = True
                    mcp = t.get("mcp") or []
                    if "*" in mcp or "*" in mt["mcp"]:
                        mt["mcp"] = ["*"]
                    else:
                        mt["mcp"] = sorted(set(mt["mcp"]) | set(mcp))
                    for d in sc.get("add_dirs") or []:
                        cur = md.get(d["path"])
                        if cur is None or (cur == "ro" and d["mode"] == "rw"):
                            md[d["path"]] = d["mode"]
                    v = sc.get("org_visibility")
                    if v in VIS_LEVELS:
                        vr = max(vr, VIS_LEVELS.index(v))
                    p = sc.get("permission_mode")
                    if p in PM_LEVELS:
                        pr = max(pr, PM_LEVELS.index(p))
                _k["max_scope"] = {
                    "tools": mt,
                    "add_dirs": [{"path": p, "mode": m} for p, m in md.items()],
                    "org_visibility": VIS_LEVELS[vr],
                    "permission_mode": PM_LEVELS[pr]}
                self.d.setdefault("user_inbox", []).append({
                    "id": uuid.uuid4().hex[:8], "from": SYSTEM,
                    "kind": "notice", "at": now(),
                    "body": ("This kiosk now carries a PERMISSION CEILING — the "
                             "maximum layer grantable to any agent in it. It was "
                             "minted from what the org already does, so nothing "
                             "changed today; review and tighten it in the kiosk "
                             "panel. Retooling within the ceiling is now open to "
                             "visitors (the /scope freeze is lifted).")})
        for m in self.d.get("user_inbox", []):       # per-mail read tracking needs ids
            m.setdefault("id", uuid.uuid4().hex[:8])
        # node mail needs ids too (retraction keys on them); pre-id entries
        # otherwise render with no ✕ and 404 the DELETE with a false excuse
        for box in ("mail", "mail_log"):
            # non-literal key → cast; both boxes hold {node: [entry, ...]}
            for ms in cast("dict[str, list[Any]]", self.d.get(box) or {}).values():
                for m in ms:
                    if isinstance(m, dict):
                        # cast: isinstance narrows Any to dict[Unknown, Unknown]
                        cast("dict[str, Any]", m).setdefault(
                            "id", uuid.uuid4().hex[:12])
        # ☞ NEW TIERS REACH EXISTING ORGS. `Org.create` COPIES the module
        # tables into the doc (`"tiers": dict(TIERS)`), so every org carries
        # its own frozen set and adding a tier to the constant does nothing for
        # any org that already exists — `switch_model` refuses with "unknown
        # tier 'X'; know [...]" while the constant plainly has it. Found live
        # 2026-08-04, the first time a tier was added since the per-org copy
        # was introduced; every test builds fresh orgs, so nothing caught it.
        # (That tier became a model VERSION instead — see MODEL_VERSIONS — but
        # the migration is the general fix and stands on its own.)
        #
        # ⚠ ADD ONLY, never overwrite. The per-org copy is what lets an org
        # price its own seats, and a plain `update` would silently reset a
        # customised table to the shipped defaults on the next load.
        # cast first: OrgDoc is a TypedDict, so a DYNAMIC key is not
        # expressible against it (`setdefault` wants a literal).
        _doc = cast("dict[str, Any]", self.d)
        for key, table in (("tiers", TIERS), ("models", MODELS)):
            cur = cast("dict[str, Any]", _doc.setdefault(key, {}))
            for k, v in table.items():
                cur.setdefault(k, v)
        # pre-№41 spend freezes wrote the usage-limit keys (error, until=None);
        # re-tag them so clear_hard_freeze("spend") actually clears them
        # instead of leaving a stale-reason freeze the API reports as cleared
        for n in self.nodes.values():
            fz = n.get("frozen")
            # ⚠ `until_ts` is checked as well as `until`: the CLI's usual
            # wording carries only an epoch, so a genuine usage-limit freeze
            # routinely has a machine time and no human one. Together with the
            # `limit` kind flag (FrozenInfo) this stops the retag eating a real
            # usage-limit freeze and making it permanently unresumable.
            if (isinstance(fz, dict) and fz.get("error") and not fz.get("until")
                    and not fz.get("until_ts") and not fz.get("resume_texts")
                    and not any(v is True for v in fz.values())):
                fz["spend"] = True
                fz["spend_error"] = fz.pop("error")
                fz.pop("until", None)
        # org holdings carry RW/RO modes (user ruling — configured on the eye's
        # gear, mirroring per-agent folder access); legacy string lists migrate
        self.d["dirs"] = norm_dirs(self.d.get("dirs"))
        # migrate pre-typed-actor docs: bare 'user'/'system' sentinels → @-forms
        # (safe exactly once, before any agent may be NAMED user/system)
        if not self.d.get("_actors_typed"):
            for a in self.d.get("audiences", []):
                if a.get("grantor") == "user":
                    a["grantor"] = USER
            for r in self.d.get("audience_requests", []):
                for f in ("target", "currently_at"):
                    if r.get(f) == "user":
                        r[f] = USER
            for m in self.d.get("user_inbox", []):
                if m.get("from") in ("system", "user"):
                    m["from"] = SYSTEM if m["from"] == "system" else USER
            self.d["_actors_typed"] = True

    # ---------------------------------------------------------------- factory
    @staticmethod
    def create(name: str, dirs: list[str] | None = None,
               permission_mode: str = "acceptEdits",
               workspace: str | None = None) -> "Org":
        # D-030 hardening: an arbitrary string here used to reach
        # --permission-mode verbatim
        if permission_mode not in PM_LEVELS:
            raise LedgerError(f"permission_mode must be one of {PM_LEVELS}")
        return Org({
            "version": 1,
            "slug": slugify(name),
            "name": name,
            "created": now(),
            "tiers": dict(TIERS),
            "models": dict(MODELS),
            # The org's own workspace dir, minted at creation (store.py makes it).
            "workspace": workspace,
            # №30: the default capability set granted to top-level hires —
            # the workspace plus any explicitly granted existing dirs, each
            # with an RW/RO mode.
            "dirs": norm_dirs(dirs),
            "permission_mode": permission_mode,   # №5: acceptEdits + --add-dir recipe
            # agent defaults (user hires that don't state them): everything on
            "default_tools": norm_tools({"mcp": ["*"]}),
            "default_visibility": "full",
            "max_top_grant": 1000,                # UI slider cap for user-level hires
            "default_top_grant": 50,              # pre-filled grant for top-level hires
            "credit_requests": [],                # §: top-level asks to the user
            "compact_at": 0.80,                   # compaction ratio (≤ 0.95 hard cap)
            "fable_limit_policy": "halt",         # halt | opus | dissolve (user ruling)
            "fable_filter_policy": "halt",        # halt | opus — filter flags (user spec)
            "nodes": {},
            "audiences": [],          # §7.3 — [{grantee, grantor, granted_at, reason}]
            # (a "chain_notices" key was seeded here and READ BY NOTHING. §7.4
            #  chain notices are ledger.user_deep_reach() writing into the
            #  normal `notices` box. The empty key shadowed the working
            #  feature well enough to convince one session it was unbuilt,
            #  so it is gone rather than reserved.)
            "audience_requests": [],  # §7.3
            "events": [],             # audit log of ops
        })

    # ---------------------------------------------------------------- queries
    @property
    def nodes(self) -> dict[str, NodeDoc]:
        return self.d["nodes"]

    def node(self, nid: str) -> NodeDoc:
        try:
            return self.nodes[nid]
        except KeyError:
            raise LedgerError(f"no such node: {nid!r}")

    def seat_cost(self, nid: str) -> int:
        return self.d["tiers"][self.node(nid)["model"]]

    def children(self, nid: str | None, live_only: bool = True) -> list[str]:
        # "live" for budget purposes includes unrecoverable — a broken session still
        # holds its seat until deliberately retired (№31)
        kids = [k for k, v in self.nodes.items()
                if v["parent"] == nid and (v["state"] != "archived" or not live_only)]
        kids.sort(key=lambda k: (self.nodes[k].get("ui_order", 0), self.nodes[k]["created"]))
        return kids

    def committed(self, nid: str) -> int:
        return sum(self.seat_cost(c) + self.nodes[c]["grant"] for c in self.children(nid))

    def free(self, nid: str) -> float:
        if nid == USER:
            return math.inf
        return self.node(nid)["grant"] - self.committed(nid)

    def parent(self, nid: str) -> str:
        """Parent id, with USER standing in for None (top level)."""
        p = self.node(nid)["parent"]
        return USER if p is None else p

    def ancestors(self, nid: str) -> list[str]:
        """Ancestor chain from immediate parent up to USER (inclusive).
        Total over the sentinel: ancestors(USER) is [] — callers holding a
        parent() result can pass it straight back without exploding."""
        if nid == USER:
            return []
        out: list[str] = []
        seen = {nid}
        cur = self.node(nid)["parent"]
        # the `seen` guard is pure defense: every op that can re-parent already
        # refuses a cycle, so on well-formed data this is identical. On a
        # corrupted doc it is the difference between a wedged process and a
        # short list — `while cur is not None` never terminates on a loop, and
        # ancestors() is under depth()/is_ancestor()/tree(), i.e. everything.
        while cur is not None and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self.nodes[cur]["parent"]
        out.append(USER)
        return out

    def is_ancestor(self, a: str, nid: str) -> bool:
        """True if `a` is a strict ancestor of node `nid` (USER is ancestor of all).
        Total over the sentinel: nothing is a strict ancestor of USER."""
        if nid == USER:
            return False
        return a == USER or a in self.ancestors(nid)

    def org_children(self, nid: str | None) -> list[str]:
        """Children on the ORG axis only — lineage predecessors (nodes with a
        `successor`) share the parent slot but are NOT organizational children (§8.5)."""
        return [k for k in self.children(nid, live_only=False)
                if not self.nodes[k].get("successor")]

    def lineage_stack(self, nid: str) -> list[str]:
        """Predecessor chain of nid, newest first."""
        out: list[str]
        out, cur = [], self.node(nid).get("predecessor")
        # same guard as ancestors(), and here it was measured: a `predecessor`
        # loop made this spin FOREVER (no RecursionError, no return), wedging
        # tree(), dissolve(), delete() and _move()'s bearer check with it.
        # Unreachable from the API (compact_split/reseed always mint a fresh
        # `<nid>@<gen>` with a rising generation) — reachable from a corrupted
        # or hand-edited doc, which is exactly when you want a process back.
        seen = {nid}
        while cur and cur in self.nodes and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self.nodes[cur].get("predecessor")
        return out

    def descendants(self, nid: str, live_only: bool = True) -> list[str]:
        out: list[str] = []
        for c in self.children(nid, live_only):
            out.append(c)
            out.extend(self.descendants(c, live_only))
        return out

    def depth(self, nid: str) -> int:
        return len(self.ancestors(nid)) - 1  # USER at depth -1's child = 0

    def effective_dirs(self, nid: str | None) -> dict[str, str] | None:
        """Capability map {path: mode} of a prospective parent. None = everything (user)."""
        if nid is None or nid == USER:
            return None
        return {d["path"]: d["mode"] for d in self.node(nid)["scope"]["add_dirs"]}

    @staticmethod
    def _clamp_tools(requested: Mapping[str, Any] | None,
                     parent_tools: Mapping[str, Any] | None,
                     strict: bool) -> tuple[ToolGrant, list[str]]:
        """Bound a tool grant by the parent's own: an agent cannot pass on a tool or
        MCP server it does not itself hold. parent_tools None = the user (everything)."""
        req = norm_tools(requested)
        if parent_tools is None:
            return req, []
        lost: list[str] = []
        for k in TOOL_KEYS:
            if req[k] and not parent_tools.get(k, True):
                if strict:
                    raise LedgerError(f"parent does not hold {k!r}; cannot grant it")
                req[k] = False
                lost.append(k)
        # "*" = the universal server set: ∩ with a concrete parent list = that list
        phold = parent_tools.get("mcp", [])
        if "*" in req["mcp"]:
            req["mcp"] = ["*"] if "*" in phold else sorted(set(phold))
        elif "*" not in phold:
            held = set(phold)
            extra = [s for s in req["mcp"] if s not in held]
            if extra:
                if strict:
                    raise LedgerError(
                        f"parent does not hold MCP server(s) {extra}; cannot grant")
                req["mcp"] = [s for s in req["mcp"] if s in held]
                lost += [f"mcp:{s}" for s in extra]
        return req, lost

    @staticmethod
    def _clamp_dirs(requested: list[DirGrant], parent_map: Mapping[str, str] | None,
                    strict: bool) -> tuple[list[DirGrant], list[str]]:
        """Intersect a dir list with a parent capability map, downgrading rw→ro where
        the parent only holds ro. strict=True raises instead of dropping (hire-time)."""
        if parent_map is None:
            return list(requested), []
        kept: list[DirGrant] = []
        lost: list[str] = []
        for d in requested:
            held = parent_map.get(d["path"])
            if held is None:
                if strict:
                    raise LedgerError(
                        f"cannot grant dirs the parent does not hold (№30): [{d['path']!r}]")
                lost.append(d["path"])
            elif held == "ro" and d["mode"] == "rw":
                if strict:
                    raise LedgerError(
                        f"parent holds {d['path']!r} read-only; cannot grant read/write (№30)")
                kept.append({"path": d["path"], "mode": "ro"})
                lost.append(f"{d['path']} (downgraded to ro)")
            else:
                kept.append(cast(DirGrant, dict(d)))  # dict() copy loses the TypedDict
        return kept, lost

    # ----------------------------------------------- kiosk permission ceiling
    # Consensus spec 2026-07-31: a kiosk carries the MAXIMUM permission layer
    # grantable to any agent in it; within it, all retooling/hiring permission
    # ops are permitted (visitors clamp-with-warning, never a 403). Normal
    # orgs have no ceiling — the top-level agent's own layer already is one.
    # `raise_ceiling` threads the one gateway-conferred CAPABILITY (not an
    # identity): "this call is authorized to, and intends to, raise the
    # ceiling to fit". Fail-closed default; agents can never pass it.

    def kiosk_ceiling(self) -> dict[str, Any] | None:
        k = self.d.get("kiosk")
        return (k or {}).get("max_scope") or None

    def default_kiosk_ceiling(self) -> dict[str, Any]:
        """Fresh-kiosk ceiling (spec §3): all built-ins ON, mcp "*" (user
        ruling — continuity with default_tools; the create dialog surfaces the
        ceiling so narrowing is a conscious act), the org's own dirs, full
        visibility, acceptEdits."""
        return {"tools": norm_tools({"mcp": ["*"]}),
                "add_dirs": norm_dirs(self.d.get("dirs")),
                "org_visibility": "full", "permission_mode": "acceptEdits"}

    def _norm_ceiling(self, ms: Mapping[str, Any] | None) -> dict[str, Any]:
        ms = ms or {}
        vis = ms.get("org_visibility", "full")
        if vis not in VIS_LEVELS:
            raise LedgerError(f"ceiling org_visibility must be one of {VIS_LEVELS}")
        pm = ms.get("permission_mode", "acceptEdits")
        if pm not in PM_LEVELS:
            raise LedgerError(f"ceiling permission_mode must be one of {PM_LEVELS}")
        mt = ms.get("max_tier") or None
        if mt is not None and mt not in TIERS:
            raise LedgerError(f"ceiling max_tier must be one of {sorted(TIERS)} "
                              f"(or unset for no cap)")
        return {"tools": norm_tools(ms.get("tools", {"mcp": ["*"]})),
                "add_dirs": norm_dirs(ms.get("add_dirs")),
                "org_visibility": vis, "permission_mode": pm,
                "max_tier": mt}

    def _check_tier_ceiling(self, tier: str) -> None:
        """Kiosk tier cap (user spec 2026-07-31: "no fable agents at all"):
        a HARD refusal for every actor — agents can't spawn above the cap and
        neither can direct API calls; the admin changes the cap itself in
        kiosk settings. No raise_ceiling bridge here: a cost cap should never
        rise as a side effect of a hire."""
        mt = (self.kiosk_ceiling() or {}).get("max_tier")
        if (mt in TIERS and tier in TIERS
                and TIERS[tier] > TIERS[mt]):
            raise LedgerError(
                f"the kiosk ceiling caps agent tier at {mt} — {tier} agents "
                f"cannot be hired, rehired or switched to in this org "
                f"(admins change this in kiosk settings)")

    def _apply_ceiling(self, tools: ToolGrant | None = None,
                       dirs: list[DirGrant] | None = None,
                       vis: str | None = None, pm: str | None = None,
                       raise_ceiling: bool = False,
                       warnings: list[str] | None = None,
                       ) -> tuple[ToolGrant | None, list[DirGrant] | None,
                                  str | None, str | None, bool]:
        """The second clamp pass, against the kiosk ceiling (parent ∩ ceiling
        at depth — the parent clamp already ran). Returns
        (tools, dirs, vis, pm, bridged): bridged=True means something was
        clamped that raise_ceiling=True would have admitted — the caller
        surfaces the one-action bridge. With raise_ceiling, the ceiling grows
        to the union instead (determinate), logged and named, never silent."""
        ceil = self.kiosk_ceiling()
        if ceil is None:
            return tools, dirs, vis, pm, False
        if raise_ceiling:
            self._raise_ceiling_for(tools, dirs, vis, pm, warnings)
            return tools, dirs, vis, pm, False
        lost_all: list[str] = []
        if tools is not None:
            had_star = "*" in (norm_tools(tools).get("mcp") or [])
            tools, tl = self._clamp_tools(tools, ceil["tools"], strict=False)
            lost_all += tl
            if had_star and "*" not in tools["mcp"]:
                # §6: "*" may survive only under a "*" ceiling; a list ceiling
                # materializes it — name the semantic change (future registry
                # additions will NOT auto-flow to this agent)
                lost_all.append("mcp:* (materialized to the ceiling's list)")
        if dirs is not None:
            cmap = {d["path"]: d["mode"] for d in ceil.get("add_dirs", [])}
            dirs, dl = self._clamp_dirs(dirs, cmap, strict=False)
            lost_all += [str(x) for x in dl]
        if vis is not None and vis in VIS_LEVELS:
            cv = ceil.get("org_visibility", "full")
            if cv in VIS_LEVELS and VIS_LEVELS.index(vis) > VIS_LEVELS.index(cv):
                lost_all.append(f"org_visibility {vis}→{cv}")
                vis = cv
        if pm is not None and pm in PM_LEVELS:
            cp = ceil.get("permission_mode", "acceptEdits")
            if cp in PM_LEVELS and PM_LEVELS.index(pm) > PM_LEVELS.index(cp):
                lost_all.append(f"permission_mode {pm}→{cp}")
                pm = cp
        if lost_all:
            if warnings is not None:
                warnings.append(
                    "clamped to the kiosk permission ceiling: "
                    + ", ".join(lost_all))
            return tools, dirs, vis, pm, True
        return tools, dirs, vis, pm, False

    def _raise_ceiling_for(self, tools: ToolGrant | None,
                           dirs: list[DirGrant] | None, vis: str | None,
                           pm: str | None, warnings: list[str] | None) -> None:
        """Grow max_scope to the union of itself and the request — the
        determinate bridge. Logged and returned as a warning NAMING what rose;
        a ceiling must never rise silently."""
        # only reached while a ceiling exists, so kiosk/max_scope are non-None
        ms: dict[str, Any] = self.d["kiosk"]["max_scope"]  # type: ignore[index]
        rose: list[str] = []
        if tools is not None:
            t = norm_tools(tools)
            ct = ms["tools"]
            for key in TOOL_KEYS:
                if t[key] and not ct.get(key, True):
                    ct[key] = True
                    rose.append(key)
            if "*" in t["mcp"] and "*" not in ct["mcp"]:
                ct["mcp"] = ["*"]
                rose.append("mcp:*")
            elif "*" not in ct["mcp"]:
                extra = [s for s in t["mcp"] if s not in ct["mcp"]]
                if extra:
                    ct["mcp"] = sorted(set(ct["mcp"]) | set(extra))
                    rose += [f"mcp:{s}" for s in extra]
        if dirs is not None:
            held = {d["path"]: d for d in ms["add_dirs"]}
            for d in dirs:
                cur = held.get(d["path"])
                if cur is None:
                    ms["add_dirs"].append({"path": d["path"], "mode": d["mode"]})
                    rose.append(d["path"])
                elif cur["mode"] == "ro" and d["mode"] == "rw":
                    cur["mode"] = "rw"
                    rose.append(f"{d['path']} (rw)")
        if vis in VIS_LEVELS:
            cv = ms.get("org_visibility", "full")
            if cv in VIS_LEVELS and VIS_LEVELS.index(vis) > VIS_LEVELS.index(cv):
                ms["org_visibility"] = vis
                rose.append(f"org_visibility {vis}")
        if pm in PM_LEVELS:
            cp = ms.get("permission_mode", "acceptEdits")
            if cp in PM_LEVELS and PM_LEVELS.index(pm) > PM_LEVELS.index(cp):
                ms["permission_mode"] = pm
                rose.append(f"permission_mode {pm}")
        if rose:
            self._log("ceiling_raise", USER, {"raised": rose}, [])
            if warnings is not None:
                warnings.append("kiosk ceiling RAISED to fit: " + ", ".join(rose))

    def set_kiosk_ceiling(self, max_scope: dict[str, Any],
                          auto_raise: bool | None = None) -> dict[str, Any]:
        """Admin sets/lowers the ceiling. Lowering SWEEPS (spec §5): the end
        state is unique — clamp every node's stored scope against the new
        ceiling — so it automates; refusal-with-directions would be the
        anti-pattern the bypass principle names. Affected live agents are told
        what they lost and why."""
        k = self.d.get("kiosk")
        if k is None:
            raise LedgerError(
                "this org is not a kiosk — normal orgs have no ceiling (the "
                "top-level agent's own layer already bounds its subtree)")
        ms = self._norm_ceiling(max_scope)
        k["max_scope"] = ms
        if auto_raise is not None:
            k["auto_raise"] = bool(auto_raise)
        swept: dict[str, list[str]] = {}
        cmap = {d["path"]: d["mode"] for d in ms["add_dirs"]}
        for nid, n in self.nodes.items():
            sc = n.get("scope") or {}
            loss: list[str] = []
            had_star = "*" in (sc.get("tools", {}).get("mcp") or [])
            t2, tl = self._clamp_tools(sc.get("tools"), ms["tools"], strict=False)
            loss += tl
            if had_star and "*" not in t2["mcp"]:
                loss.append("mcp:* (materialized)")
            d2, dl = self._clamp_dirs(sc.get("add_dirs") or [], cmap, strict=False)
            loss += [str(x) for x in dl]
            sc["tools"], sc["add_dirs"] = t2, d2
            v = sc.get("org_visibility")
            if v in VIS_LEVELS and VIS_LEVELS.index(v) > VIS_LEVELS.index(ms["org_visibility"]):
                sc["org_visibility"] = ms["org_visibility"]
                loss.append(f"org_visibility {v}→{ms['org_visibility']}")
            p = sc.get("permission_mode")
            if p in PM_LEVELS and PM_LEVELS.index(p) > PM_LEVELS.index(ms["permission_mode"]):
                sc["permission_mode"] = ms["permission_mode"]
                loss.append(f"permission_mode {p}→{ms['permission_mode']}")
            if loss:
                swept[nid] = loss
                if n["state"] == "live" and not n.get("successor"):
                    self._notify([nid],
                                 f"The kiosk permission ceiling was adjusted; "
                                 f"your grants were clamped to fit: "
                                 f"{', '.join(loss)}.")
        self._log("ceiling_set", USER, {"swept": swept}, [])
        warnings = ([f"ceiling lowered — {len(swept)} agent(s) "
                     f"clamped to fit: {sorted(swept)}"]
                    if swept else [])
        # tier cap: no model sweep — downgrading live agents moves seats and
        # credits around (side effects the admin should choose per agent), so
        # existing over-cap agents stay and the cap blocks NEW use only. Named
        # here so nothing is silent.
        mt = ms.get("max_tier")
        if mt in TIERS:
            over = sorted(i for i, n in self.nodes.items()
                          if n["state"] == "live"
                          and TIERS.get(n["model"], 0) > TIERS[mt])
            if over:
                warnings.append(
                    f"{len(over)} live agent(s) above the {mt} tier cap "
                    f"remain ({', '.join(over)}) — the cap blocks new hires, "
                    f"rehires and switches; switch or retire them as you "
                    f"see fit")
            # …and the ARCHIVED ones, which used to be reported nowhere. They
            # are the worse case: rehire hard-refuses on the cap and
            # switch_model needs a live node, so an archived over-cap agent is
            # STRANDED — recoverable only by raising the cap again — and the
            # admin was told nothing at all.
            stuck = sorted(i for i, n in self.nodes.items()
                           if n["state"] == "archived"
                           and TIERS.get(n["model"], 0) > TIERS[mt])
            if stuck:
                warnings.append(
                    f"{len(stuck)} ARCHIVED agent(s) above the {mt} tier cap "
                    f"({', '.join(stuck)}) can no longer be rehired at their "
                    f"own tier — rehire them with a cheaper tier= override, or "
                    f"raise the cap")
        return {"max_scope": ms, "swept": swept, "warnings": warnings}

    def set_hire_defaults(self, default_tools: Mapping[str, Any] | None = None,
                          default_visibility: str | None = None,
                          raise_ceiling: bool = False) -> dict[str, Any]:
        """The org's agent-hire defaults (the eye's gear). Kiosk VISITORS may
        set these too (user ruling 2026-07-31) — a default is just a pre-filled
        grant, so the ceiling clamps it with the same machinery as any grant;
        admins get the bridge/auto-raise semantics. Hire-time still re-clamps
        (defaults resolve THEN clamp), so this is honesty, not enforcement:
        the stored default must never show a capability no hire can receive."""
        warnings: list[str] = []
        bridged = False
        if default_tools is not None:
            t = norm_tools(default_tools)
            t, _d, _v, _p, b = self._apply_ceiling(
                tools=t, raise_ceiling=raise_ceiling, warnings=warnings)
            self.d["default_tools"] = cast(ToolGrant, t)  # tools in ⇒ tools out
            bridged = bridged or b
        if default_visibility is not None:
            if default_visibility not in VIS_LEVELS:
                raise LedgerError(f"default_visibility must be one of {VIS_LEVELS}")
            _t, _d, v2, _p, b = self._apply_ceiling(
                vis=default_visibility, raise_ceiling=raise_ceiling,
                warnings=warnings)
            self.d["default_visibility"] = cast(str, v2)  # vis in ⇒ vis out
            bridged = bridged or b
        self._log("set_defaults", USER,
                  {"tools": self.d.get("default_tools"),
                   "visibility": self.d.get("default_visibility")}, warnings)
        res: dict[str, Any] = {"default_tools": self.d.get("default_tools"),
                               "default_visibility": self.d.get("default_visibility"),
                               "warnings": warnings}
        if bridged:
            res["bridge"] = {"raise_ceiling": True}
        return res

    # ------------------------------------------------------------- validation
    def _require_authority(self, actor: str, nid: str,
                           allow_self: bool = False) -> None:
        """Actor must be USER/SYSTEM or an ancestor of nid (§7.1); optionally nid
        itself. Actor kinds are typed (@-sentinels), so an AGENT named "user" or
        "system" is just an agent — its name confers nothing."""
        if actor_kind(actor) in ("user", "system") or (allow_self and actor == nid):
            return
        if actor not in self.nodes:
            raise LedgerError(f"unknown actor: {actor!r}")
        if not self.is_ancestor(actor, nid):
            raise LedgerError(
                f"{actor} has no authority over {nid} — authority is downward only (§7.1)")

    def _require_live(self, nid: str) -> None:
        if self.node(nid)["state"] != "live":
            raise LedgerError(f"{nid} is {self.node(nid)['state']}, not live")

    # -------------------------------------------------------------- stranding
    def _stranding_warnings(self, payer: str, free_before: float,
                            free_after: float) -> list[str]:
        """§4.4 (corrected): name each archived dependent of `payer` whose rehire cost
        was affordable at free_before but is not at free_after."""
        if payer == USER or free_after >= free_before:
            return []
        warns: list[str] = []
        for c in self.children(payer, live_only=False):
            n = self.nodes[c]
            if n["state"] != "archived":
                continue
            cost = self.seat_cost(c) + n["grant"]  # rehire defaults to previous grant
            if free_after < cost <= free_before:
                kind = "predecessor" if n.get("bearer_state") else "report"
                warns.append(
                    f"{payer} can no longer afford to rehire archived {kind} "
                    f"{c} (needs {cost}, free now {free_after:g}) — stranded (§4.4)")
        return warns

    # ------------------------------------------------------------------ mail
    def relationship(self, sender: str, to: str) -> str:
        if sender == USER:
            return "USER"
        if to != USER and self.node(to)["parent"] == sender:
            return "your superior"
        if sender != USER and self.node(sender)["parent"] == (None if to == USER else to):
            return "your report"   # from the recipient's view: sender is a report
        if to != USER and sender != USER \
                and self.node(sender)["parent"] == self.node(to)["parent"]:
            return "your peer"
        if to != USER and self.is_ancestor(sender, to):
            return "a superior above your chain"
        return "an agent"

    def _resolve_recipient(self, to: str) -> str:
        """Agent-facing convenience: 'user' addresses the user UNLESS an agent is
        literally named user (names win — the @-sentinel stays unambiguous)."""
        if to == "user" and "user" not in self.nodes:
            return USER
        return to

    def post_mail(self, sender: str, to: str, body: str, kind: str = "message",
                  attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Agent-to-agent (or agent-to-user) mail under the §7.2 addressing rules:
        downward any depth (deep reach implicitly grants the recipient an audience),
        one hop up, siblings, held audiences. Everything else is refused with the
        proper route named."""
        to = self._resolve_recipient(to)
        if actor_kind(sender) == "agent":
            self.node(sender)
        warnings: list[str] = []
        if to.startswith(("@ext:", "@org:", "@mcp:")):
            # outbound to the OUTSIDE WORLD — a chatq session (@ext:), another
            # org's inbox (@org:), or a polling external chat on the extern MCP
            # server (@mcp: — no push transport; the peer reads the org inbox).
            # Org-inbox model (user spec): the reply speaks for the ORG as a
            # whole; top-level agents and org-inbox audience holders may send
            # it, and they are expected to have coordinated internally.
            if actor_kind(sender) != "agent":
                raise LedgerError("only agents message outside parties")
            if self.is_kiosk:
                raise LedgerError("this organization is a sealed kiosk — it has "
                                  "no contact with the outside world")
            if self.node(sender)["parent"] is not None \
                    and not self._has_audience(sender, EXTERN):
                raise LedgerError(
                    "only TOP-LEVEL agents (or holders of an ORG-INBOX audience) "
                    "speak for the org to the outside — escalate to your "
                    "superior instead (§7.5)")
            if to.startswith("@org:") and to[5:] == self.d.get("slug"):
                raise LedgerError("that address is this organization itself")
            # actual delivery rides the bridge (supervisor/api) — the ledger
            # authorizes and records the correspondence
            oid = self._org_inbox_log("out", to, body, by=sender)
            self._log("mail", sender, {"to": to, "kind": kind,
                      "gist": body.strip().splitlines()[0][:80] if body.strip()
                      else ""}, [])
            return {"delivered": to, "id": oid, "warnings": warnings}
        if to == USER:
            if sender == USER:
                raise LedgerError("the user cannot mail the user")
            if self.node(sender)["parent"] is not None and not self._has_audience(sender, USER):
                raise LedgerError(
                    "only top-level agents (or holders of a user audience) may write "
                    "to the user — escalate to your superior instead (§7.5)")
            ue: UserMailEntry = {"id": uuid.uuid4().hex[:8], "from": sender,
                                 "kind": kind, "body": body, "at": now()}
            self.d.setdefault("user_inbox", []).append(ue)
            self._log("mail", sender, {"to": USER, "kind": kind}, [])
            # the id rides the result → the sender's chat renders an inline
            # "open in mailbox" link on the send (user spec 2026-07-31)
            return {"delivered": "user_inbox", "id": ue["id"],
                    "warnings": warnings}

        target = self.node(to)
        if target["state"] == "unrecoverable":
            raise LedgerError(f"{to} is unrecoverable — it cannot receive mail")
        deferred = target["state"] != "live"
        if deferred:
            # user ruling: archived agents still RECEIVE mail — it waits in
            # their inbox and is acted on at rehire
            warnings.append(f"{to} is {target['state']} — the mail is queued in "
                            f"its inbox and will be acted on when it is rehired")
        if sender != USER:
            s = self.node(sender)
            allowed = (
                self.is_ancestor(sender, to)                      # downward, any depth
                or (None if to == USER else to) == s["parent"]    # one hop up
                or s["parent"] == target["parent"]                # sibling
                or self._has_audience(sender, to))                # sanctioned upward
            if not allowed:
                raise LedgerError(
                    f"{sender} may not address {to} — reach down, one hop up, "
                    f"sideways, or via a held audience; route anything else through "
                    f"your superior (§7.2)")
            # §7.3: messaging a non-child descendant implicitly grants the reply path
            if self.is_ancestor(sender, to) and target["parent"] != sender \
                    and not self._has_audience(to, sender):
                self.d["audiences"].append({
                    "grantee": to, "grantor": sender, "granted_at": now(),
                    "reason": f"{sender} messaged directly"})
                warnings.append(f"audience granted: {to} may now reply to {sender} directly")
        box = self.d.setdefault("mail", {})
        entry: MailEntry = {
            # parity №11/№17: node mail carries an id — pending bubbles render
            # from the durable server copy, retraction targets one entry, and
            # the per-mail read-marking gate (m._wait && m.id) finally passes
            "id": uuid.uuid4().hex[:12],
            "from": sender, "kind": kind, "body": body, "at": now(),
            "relationship": self.relationship(sender, to),
        }
        if attachments:
            # user spec 2026-07-31: mail carries FILES — [{name, path, bytes}]
            # where path is relative to the recipient's working folder (the
            # bytes already landed in its uploads/); the envelope announces
            # each one at delivery
            entry["attachments"] = list(attachments)[:10]
        box.setdefault(to, []).append(entry)
        # full-body archive for the node's inbox view (the event log keeps only
        # a gist) — capped per node
        log = self.d.setdefault("mail_log", {}).setdefault(to, [])
        log.append(cast(MailEntry, dict(entry)))  # dict() copy loses the TypedDict
        del log[:-100]
        if sender == USER:
            # the user's Sent folder: every user message IS mail (user ruling —
            # the direct-message channel was folded into the mail system)
            out = self.d.setdefault("user_outbox", [])
            out.append({**entry, "to": to})
            del out[:-100]
        # ⚠ `or [""]`: a body that is entirely whitespace strips to "" and
        # `"".splitlines()` is the EMPTY LIST, so this line raised IndexError
        # and the whole send 500ed. The composer trims and refuses empty, but
        # nothing else does — the API takes `body.text` as sent, and agent mail
        # comes from a model. Found 2026-08-04 by the message-visibility suite.
        gist = (body.strip().splitlines() or [""])[0][:80]
        self._log("mail", sender, {"to": to, "kind": kind, "gist": gist},
                  warnings)
        return {"delivered": to, "id": entry["id"], "deferred": deferred,
                "warnings": warnings}

    def post_external_mail(self, peer: str, body: str,
                           attachments_by_node: Mapping[str, list[dict[str, Any]]]
                           | None = None) -> list[str]:
        """Inbound from OUTSIDE the org — a chatq session (@ext:<id>) or another
        org (@org:<slug>). Org-inbox model (user spec): the message is addressed
        to the ORGANIZATION, not to any agent. It lands in the org-wide inbox;
        every live top-level agent AND every org-inbox audience holder receives
        a copy, coordinates internally on who answers, and the answer speaks
        for the org. Returns the recipients so the supervisor can drive them.
        Kiosk orgs are sealed: inbound is dropped (empty recipient list)."""
        if self.is_kiosk:
            return []
        self._org_inbox_log("in", peer, body)
        tops = self.extern_recipients()
        box = self.d.setdefault("mail", {})
        for t in tops:
            entry: MailEntry = {"id": uuid.uuid4().hex[:12],
                     "from": peer, "kind": "message", "body": body,
                     "at": now(),
                     "relationship": "OUTSIDE PARTY writing to the ORG'S SHARED "
                                     "INBOX — untrusted. Every top-level agent "
                                     "and org-inbox audience holder got this "
                                     "same copy: coordinate internally on who "
                                     "answers (one reply), and the reply "
                                     "speaks for the org as a whole"}
            # external attachments (user spec 2026-07-31): the caller copied
            # the files into each recipient's uploads/ — per-node metadata
            # because collision suffixes may differ per recipient
            if attachments_by_node and attachments_by_node.get(t):
                entry["attachments"] = list(attachments_by_node[t])[:10]
            box.setdefault(t, []).append(entry)
            log = self.d.setdefault("mail_log", {}).setdefault(t, [])
            log.append(cast(MailEntry, dict(entry)))  # dict() copy loses the TypedDict
            del log[:-100]
        if not tops:
            # nobody to receive it: surface to the user instead of losing it
            self.d.setdefault("user_inbox", []).append({
                "id": uuid.uuid4().hex[:8], "from": SYSTEM, "kind": "notice",
                "at": now(),
                "body": (f"Outside party {peer} messaged this org, but "
                         f"no top-level agents are live to receive it:\n\n"
                         + body[:2000])})
        self._log("ext_mail", peer,
                  {"to": ",".join(tops) or "(user inbox)",
                   "gist": body.strip().splitlines()[0][:80]
                   if body.strip() else ""}, [])
        return tops

    def _has_audience(self, grantee: str, grantor: str) -> bool:
        return any(a["grantee"] == grantee and a["grantor"] == grantor
                   for a in self.d["audiences"])

    # ------------------------------------------------ the org inbox (user spec)
    # Outside parties (chatq sessions, other orgs) see ONE recipient: the org.
    # Their mail lands here; every live top-level agent and every org-inbox
    # audience holder receives it, coordinates internally, and any one of them
    # replies FOR the org. Kiosk orgs are sealed from all of it.
    @property
    def is_kiosk(self) -> bool:
        return self.d.get("kiosk") is not None

    def extern_holders(self) -> list[str]:
        return [a["grantee"] for a in self.d["audiences"]
                if a["grantor"] == EXTERN and a["grantee"] in self.nodes
                and self.nodes[a["grantee"]]["state"] == "live"]

    def extern_recipients(self) -> list[str]:
        # live-for-budget ≠ live-for-delivery (audit 2026-08-01, item 3):
        # children() keeps unrecoverable nodes because they still hold their
        # seat, but mail queued into an unresumable node never drains — and a
        # lone unrecoverable top-level suppressed the user-inbox rescue in
        # post_external_mail. Deliver only to the truly live.
        rec = [c for c in self.children(None)
               if self.nodes[c]["state"] == "live"]
        rec += [h for h in self.extern_holders() if h not in rec]
        return rec

    def _org_inbox_log(self, direction: Literal["in", "out"], peer: str, body: str,
                       by: str | None = None) -> str:
        log = self.d.setdefault("org_inbox", [])
        e: OrgInboxEntry = {"id": uuid.uuid4().hex[:8], "dir": direction, "peer": peer,
                            "body": body[:20000], "at": now()}
        if by:
            e["by"] = by      # internal attribution only — outbound speaks as the org
        log.append(e)
        del log[:-200]
        return e["id"]

    def org_inbox_mark_read(self) -> None:
        self.d["org_inbox_read"] = len(self.d.get("org_inbox", []))

    # -------------------------------------------------- audience requests (§7.3)
    def request_audience(self, actor: str, target: str, reason: str) -> dict[str, Any]:
        """The slow upward path: a request climbs the actor's chain ONE refusable hop
        at a time. Grants flow down fast; requests climb slowly — by design."""
        self.node(actor)
        target = self._resolve_recipient(target)
        if target != USER and not self.is_ancestor(target, actor):
            raise LedgerError("audience requests climb your own chain — the target "
                              "must be one of your superiors (or 'user')")
        par = self.parent(actor)
        if target == par:
            # design motto: you can already reach them — succeed with a pointer,
            # don't refuse
            return {"already_reachable": True, "drive": [], "warnings": [
                f"{target} is your direct superior — you can already message "
                f"them with orgtree_message; no audience needed"]}
        open_req = next((r for r in self.d["audience_requests"]
                         if r["from"] == actor and r["target"] == target), None)
        if open_req:
            # design motto: a duplicate ask reports the existing request's
            # progress instead of erroring
            return {"currently_at": open_req["currently_at"], "drive": [],
                    "warnings": [
                        f"your request to reach {target} is already open — it "
                        f"currently awaits {open_req['currently_at']}"]}
        self.d["audience_requests"].append({
            "from": actor, "target": target, "currently_at": par,
            "reason": reason[:300], "opened_at": now()})
        body = (f'AUDIENCE REQUEST: your report "{actor}" asks to speak directly with '
                f'{target}. Reason: "{reason[:300]}". You may forward it one hop up '
                f'(orgtree_audience action=forward), deny it (action=deny), or simply '
                f'handle the matter yourself and deny.')
        r = self.post_mail(actor, par, body, kind="request")
        return {"currently_at": par, "drive": [] if par == USER else [par],
                "warnings": r.get("warnings", [])}

    def _find_request(self, frm: str, target: str) -> dict[str, Any]:
        req = next((r for r in self.d["audience_requests"]
                    if r["from"] == frm and r["target"] == target), None)
        if not req:
            raise LedgerError(f"no open audience request {frm} → {target}")
        return req

    def audience_forward(self, actor: str, frm: str, target: str) -> dict[str, Any]:
        req = self._find_request(frm, target)
        if actor != req["currently_at"] and actor != USER:
            raise LedgerError(f"the request currently awaits {req['currently_at']}")
        # The user is the TOP of every chain, so there is no "one hop up" from
        # there: a user forward hands the request straight to its target. It
        # used to set `nxt = USER` unconditionally, which for any target other
        # than the user fell through to `post_mail(USER, USER, …)` —
        # "the user cannot mail the user" — AFTER `currently_at` had already
        # been written, so the request was left stuck at @user and the real
        # holder could never forward or deny it again. Dormant (no route calls
        # forward as the user today) but a live landmine for the next caller.
        nxt = target if actor == USER else self.parent(actor)
        req["currently_at"] = nxt
        drive: list[str] = []
        if nxt == target:
            if target == USER:
                self.d.setdefault("user_inbox", []).append({
                    "from": frm, "kind": "request", "at": now(),
                    "body": (f'Audience request (forwarded up the chain): "{frm}" asks '
                             f'to speak with you directly. Reason: {req["reason"]}. '
                             f'Grant or deny it from the inbox panel.')})
            else:
                self.post_mail(actor, target,
                               f'AUDIENCE REQUEST reached you: "{frm}" asks to speak '
                               f'with you directly. Reason: {req["reason"]}. Grant with '
                               f'orgtree_audience action=grant, or deny.',
                               kind="request")
                drive.append(target)
        else:
            self.post_mail(actor, nxt,
                           f'AUDIENCE REQUEST (forwarded): "{frm}" seeks {target}. '
                           f'Reason: {req["reason"]}. Forward, deny, or handle it.',
                           kind="request")
            if nxt != USER:
                drive.append(nxt)
        return {"currently_at": nxt, "drive": drive, "warnings": []}

    def audience_grant(self, actor: str, frm: str,
                       target: str | None = None) -> dict[str, Any]:
        """Grant frm a direct channel to `target` — the actor itself by default.
        DELEGATED grants (user ruling): an agent may open the ear of anyone in
        its OWN messaging reach — itself, a live peer, or its direct superior
        (the user, for a top-level agent) — for any agent in its purview (its
        subtree). So a top-level agent can hand any of its descendants a
        direct line to the user. The ear's owner may rescind at will, and the
        grant survives re-parenting only while the delegator still commands
        the grantee. Also resolves any open request frm → target."""
        # names win over the bare-string aliases, the same rule
        # `_resolve_recipient` applies to "user": an agent whose slug really is
        # "extern" or "inbox" was permanently unreachable through this API,
        # every grant aimed at it being silently redirected to the org-inbox
        # sentinel. The @-sentinel itself is unambiguous and always wins.
        if target == EXTERN or (target in ("extern", "inbox")
                                and target not in self.nodes):
            return self._grant_extern(actor, frm)
        target = self._resolve_recipient(target) if target else actor
        if frm == target:
            raise LedgerError("an audience with oneself is meaningless")
        if target == actor:
            if actor != USER and not self.is_ancestor(actor, frm):
                raise LedgerError("only a superior grants an audience with itself")
        elif actor == USER:
            self.node(frm)                       # user authority: unconditional,
            if target != USER:                   # both parties must just exist
                self.node(target)
        else:
            if not self.is_ancestor(actor, frm):
                raise LedgerError("delegated audience grants cover your purview "
                                  "only — the grantee must be in your subtree")
            par = self.parent(actor)
            peers = set(self.children(None if par == USER else par))
            peers.discard(actor)
            if target != par and target not in peers:
                raise LedgerError(
                    "you may open only ears within your own reach: your own, a "
                    "live peer's, or your direct superior's"
                    + (" (the user)" if par == USER else f' ("{par}")'))
        if not self._has_audience(frm, target):
            entry: AudienceGrant = {
                     "grantee": frm, "grantor": target, "granted_at": now(),
                     "reason": ("granted on request" if target == actor
                                else f"delegated by {actor}")}
            if target != actor:
                entry["delegated_by"] = actor
            self.d["audiences"].append(entry)
        self.d["audience_requests"] = [
            r for r in self.d["audience_requests"]
            if not (r["from"] == frm and r["target"] == target)]
        drive: list[str] = []
        who = "The user" if actor == USER else f'"{actor}"'
        if target == USER:
            if actor == USER:
                self._notify([frm], "The user granted you a USER AUDIENCE — you may "
                                    "write to them directly until it is rescinded.")
            else:
                self._notify([frm],
                             f'{who} granted you a direct USER AUDIENCE — you may '
                             f'write to the user directly until it is rescinded.')
                self.d.setdefault("user_inbox", []).append({
                    "id": uuid.uuid4().hex[:8], "from": SYSTEM, "kind": "notice",
                    "at": now(),
                    "body": f'{who} granted "{frm}" a direct audience to you — it '
                            f'may now write to your inbox. Revoke it from the '
                            f'audience panel at will.'})
        elif target == actor:
            self.post_mail(actor, frm,
                           f"Audience granted: you may message {actor} directly until "
                           f"it is rescinded.", kind="decision")
            drive.append(frm)
        else:
            self._notify([frm],
                         f'{who} granted you an audience with "{target}" — you may '
                         f'message them directly until it is rescinded.')
            self._notify([target],
                         f'{who} granted "{frm}" an audience with you — it may now '
                         f'message you directly; you may revoke it at will.')
            drive.append(frm)
        self._log("audience_grant", actor, {"grantee": frm, "grantor": target}, [])
        return {"drive": drive, "warnings": []}

    def _grant_extern(self, actor: str, frm: str) -> dict[str, Any]:
        """Audience with the ORG INBOX (user spec): the grantee reads outside
        mail addressed to the org and may reply for it — the 'client contact'
        pattern. Granted by the user, or by a top-level agent for its own
        purview."""
        if self.is_kiosk:
            raise LedgerError("a sealed kiosk org has no org inbox")
        n = self.node(frm)
        if n["parent"] is None:
            return {"drive": [], "warnings": [
                f"{frm} is top-level — it already speaks for the org; "
                f"no inbox audience needed"]}
        if actor != USER:
            if self.node(actor)["parent"] is not None \
                    and not self._has_audience(actor, EXTERN):
                raise LedgerError("only the user, a top-level agent, or an "
                                  "org-inbox audience holder may extend the "
                                  "org inbox")
            if not self.is_ancestor(actor, frm):
                raise LedgerError("org-inbox audience grants cover your "
                                  "purview only — the grantee must be in "
                                  "your subtree")
        if not self._has_audience(frm, EXTERN):
            entry: AudienceGrant = {
                     "grantee": frm, "grantor": EXTERN, "granted_at": now(),
                     "reason": ("granted by the user" if actor == USER
                                else f"delegated by {actor}")}
            if actor != USER:
                entry["delegated_by"] = actor
            self.d["audiences"].append(entry)
        who = "The user" if actor == USER else f'"{actor}"'
        self._notify([frm],
                     f"{who} granted you audience with the ORG INBOX: you now "
                     f"receive outside messages addressed to this organization "
                     f"(chatq sessions, other orgs) and may reply for it with "
                     f"orgtree_message to the sender's @ext:/@org: address. "
                     f"Replies speak for the org as a whole — coordinate with "
                     f"the other recipients before answering.")
        self._log("audience_grant", actor, {"grantee": frm, "grantor": EXTERN}, [])
        return {"drive": [frm], "warnings": []}

    def audience_deny(self, actor: str, frm: str, target: str) -> dict[str, Any]:
        req = self._find_request(frm, target)
        if actor not in (req["currently_at"], target, USER):
            raise LedgerError(f"the request currently awaits {req['currently_at']}")
        self.d["audience_requests"].remove(req)
        self.post_mail(actor if actor != USER else USER, frm,
                       f"Your audience request to reach {target} was declined "
                       f"at {actor}.", kind="decision") if actor != USER else \
            self._notify([frm], f"The user declined your audience request.")
        self._log("audience_deny", actor, {"from": frm, "target": target}, [])
        return {"drive": [frm] if actor != USER else [], "warnings": []}

    def audience_revoke(self, actor: str, grantee: str,
                        grantor: str | None = None) -> dict[str, Any]:
        """Rescinding — unilateral and instant (§7.3). Actor must be the grantor
        (or the user, whose authority is unconditional — and who may name a
        specific grantor to rescind exactly that channel, e.g. the ✕ on a
        switchboard tab, leaving the grantee's other audiences intact)."""
        tgt = grantor if (actor == USER and grantor) else None
        before = len(self.d["audiences"])
        # a delegator may rescind its own delegation (covers org-inbox grants,
        # whose grantor is the EXTERN sentinel, not the granting agent)
        self.d["audiences"] = [
            a for a in self.d["audiences"]
            if not (a["grantee"] == grantee
                    and (a["grantor"] == actor or a.get("delegated_by") == actor
                         or actor == USER)
                    and (tgt is None or a["grantor"] == tgt))]
        if len(self.d["audiences"]) == before:
            raise LedgerError(f"no audience held by {grantee} that {actor} may revoke")
        label = tgt if tgt else actor
        self._notify([grantee],
                     f"Your audience with {label if label != USER else 'the user'} was "
                     f"rescinded — fall back to the parent chain.")
        self._log("audience_revoke", actor,
                  {"grantee": grantee, **({"grantor": tgt} if tgt else {})}, [])
        return {"warnings": []}

    def take_mail(self, nid: str) -> list[MailEntry]:
        return (self.d.get("mail") or {}).pop(nid, [])

    def user_deep_reach(self, nid: str, gist: str, kind: str = "message") -> None:
        """§7.4: the user reached a non-top-level node — notify every superior up
        the chain (without interruption) and grant the node a user audience.

        `kind` is "message" or "command". A SLASH COMMAND used to do NEITHER of
        these: it returned from the endpoint before the mail path ran, so the
        user could drive an agent directly — including `/compact`, which splits
        its context — and the whole superior chain never heard about it, nor did
        the agent get a user audience out of it (user report 2026-08-03). A
        command is still not mail (no envelope, no Sent copy, nothing to deliver
        at rehire), but it IS direct user contact, which is the thing these two
        effects exist for. The wording differs because the claims differ: an
        instruction outranks the chain, whereas a command changes the agent's
        session without saying anything about anyone's plan."""
        chain = [a for a in self.ancestors(nid) if a != USER]
        if not chain:
            return   # top-level: the only superior is the user themself (№12)
        # The notice used to state only that the user had spoken. A superior
        # could read that as gossip and carry on — but the RECIPIENT is
        # simultaneously told "user instructions outrank your chain" (the
        # envelope's ⚠ tag), so the two sides disagreed about what had just
        # happened. Say the authority out loud, and say what to DO about it.
        # Every direct message, no marking (user ruling 2026-08-02: "requiring
        # me to manually mark a message as authoritative is costly to my time,
        # and it doesn't take much to bring this attention to each superior").
        if kind == "command":
            self._notify(
                chain,
                f'The user ran the session command "{gist}" on "{nid}", inside '
                f'your chain. It came from the USER directly, not through you. '
                f"Re-check any plan of yours that assumes {nid}'s session is "
                f'unchanged. You are being told, not asked to act.')
        else:
            self._notify(
                chain,
                f'The user gave a direct instruction to "{nid}", inside your chain: '
                f'"{gist}" — it carries the USER\'s authority and outranks anything '
                f'you have told {nid}. Re-check any plan of yours that depends on '
                f'it. You are being told, not asked to act.')
        if not self._has_audience(nid, USER):
            self.d["audiences"].append({
                "grantee": nid, "grantor": USER, "granted_at": now(),
                "reason": ("user ran a command directly" if kind == "command"
                           else "user messaged directly")})

    # --------------------------------------------------------------- notices
    def _notify(self, nids: Iterable[str | None], text: str) -> None:
        """Queue an org-change notice for each node (user ruling: every agent
        affected by a manual action is told). Delivered by the supervisor at the
        node's NEXT turn boundary — never wakes or preempts anyone (§7.4)."""
        box = self.d.setdefault("notices", {})
        log = self.d.setdefault("notice_log", [])
        for nid in {n for n in nids if n and n in self.nodes}:
            box.setdefault(nid, []).append({"at": now(), "text": text})
            log.append({"node": nid, "at": now(), "text": text})
        del log[:-800]

    def _peers_of(self, parent: str | None, excl: str) -> list[str]:
        return [k for k in self.children(parent) if k != excl]

    # ---------------------------------------------------------------- events
    def _log(self, op: str, actor: str, detail: dict[str, Any],
             warnings: list[str]) -> None:
        self.d["events"].append({
            "op": op, "actor": actor, "at": now(), "detail": detail,
            "warnings": warnings,
        })

    # ------------------------------------------------------------------ hire
    def hire(self, actor: str, parent: str | None, tier: str, grant: int, name: str,
             add_dirs: list[Any] | None = None, tools: Mapping[str, Any] | None = None,
             org_visibility: str | None = None, charter: str | None = None,
             raise_ceiling: bool = False) -> dict[str, Any]:
        """§4.2 + §4.6. `parent` None = top level (actor must be USER). If actor is a
        strict ancestor of parent, credits cascade down the path (forcible hire).

        ⚠️ No defaults for agent actors (user ruling): the USER hires from sensible
        defaults, but an agent must state every permission — dirs, every tool switch,
        the MCP list, org visibility — and the hire's CHARTER, explicitly. (User
        ruling 2026-07-31: `purpose` is dropped — charter is the one role
        statement, editable later via retool, injected into every turn.)"""
        if tier not in self.d["tiers"]:
            raise LedgerError(f"unknown tier {tier!r}; know {sorted(self.d['tiers'])}")
        self._check_tier_ceiling(tier)
        if grant < 0 or grant != int(grant):
            raise LedgerError("grant must be a non-negative integer (№7)")
        # ATOMICITY (§4.7 moved up, 2026-08-04): the name was validated only
        # inside `_new_node`, at the very END — after `_chain_acquire` had
        # already inflated grants down the chain. A hire refused for an
        # unsluggable name therefore left the credits behind: measured
        # top_level_holds 105 → 915 on a user-pool cascade, with no node.
        slugify(name)
        need = self.d["tiers"][tier] + int(grant)

        if parent is None:
            if actor != USER:
                raise LedgerError("only the user hires at top level (§7.4)")
        else:
            self._require_live(parent)
            if actor != USER and actor != parent and not self.is_ancestor(actor, parent):
                raise LedgerError(
                    f"{actor} may hire only within its own subtree (§4.6)")

        fable_futile = tier == "fable" and bool(self.d.get("fable_lock"))
        if fable_futile and actor == USER:
            self.clear_fable_lock()   # a user fable-hire is the decree
            fable_futile = False

        if actor != USER:
            missing: list[str] = []
            if add_dirs is None:
                missing.append("add_dirs (explicit list of {path, mode}; [] is valid)")
            if tools is None or any(k not in tools for k in TOOL_KEYS) or "mcp" not in tools:
                missing.append("tools (bash, web, edit, subagents, mcp — each stated explicitly)")
            if org_visibility is None:
                missing.append("org_visibility (self|team|subtree|full)")
            if not (charter and str(charter).strip()):
                missing.append("charter (the hire's role and standing "
                               "instructions — write it in full)")
            if missing:
                raise LedgerError(
                    "agent hires have no defaults — specify exactly: " + "; ".join(missing))
        vis = (org_visibility if org_visibility is not None
               else self.d.get("default_visibility", "full"))
        if vis not in VIS_LEVELS:
            raise LedgerError(f"org_visibility must be one of {VIS_LEVELS}")

        # №34 — cheap runaway insurance
        if parent is not None:
            depth = self.depth(parent) + 1
            if depth >= self.d.get("max_depth", MAX_DEPTH):
                raise LedgerError(f"max org depth {self.d.get('max_depth', MAX_DEPTH)} reached")
            # audit finding: count ORG children only — lineage bearers share
            # the parent slot but are not reports, and counting them let
            # routine compaction silently eat the hiring cap
            # user ruling 2026-07-31: the cap is runaway INSURANCE, not a shape
            # constraint — wide flat teams are legitimate (the canvas stacks
            # leaf crowds), so the default is far above any deliberate org
            if len(self.org_children(parent)) >= self.d.get("max_children", MAX_CHILDREN):
                raise LedgerError(
                    f"{parent} already has {self.d.get('max_children', MAX_CHILDREN)} reports (cap)")

        # №30 — dirs default: top level gets the org's dirs; deeper gets what the
        # parent holds. Explicit grants must fit the parent's capability (path AND
        # mode — a read-only holding cannot beget read/write), whoever the actor is.
        if parent is None:
            parent_map = None
            default = norm_dirs(self.d["dirs"])
        else:
            parent_map = self.effective_dirs(parent)
            default = cast("list[DirGrant]",  # dict() copies lose the TypedDict
                           [dict(d) for d in self.node(parent)["scope"]["add_dirs"]])
        if add_dirs is None:
            dirs = default
        else:
            dirs, _ = self._clamp_dirs(norm_dirs(add_dirs), parent_map, strict=True)

        parent_tools = None if parent is None else self.node(parent)["scope"]["tools"]
        # unspecified tools (user hires) fall back to the org's agent defaults —
        # applied directly at top level, ∩ the superior's capability below
        requested = tools if tools is not None else self.d.get("default_tools")
        tset, tlost = self._clamp_tools(requested, parent_tools,
                                        strict=(actor != USER and tools is not None))

        warnings: list[str] = []
        if fable_futile:
            # not a gate — just the truth (user ruling): the hire is permitted, but
            # the seat cannot actually run until the limit resets or the user decrees
            warnings.append("the weekly Fable usage limit is exhausted — this agent "
                            "will not be able to run yet; hiring it now is futile")
        # ATOMICITY: every remaining check that can REFUSE runs BEFORE
        # `_chain_acquire`, which is the first thing in this method to mutate
        # state. The strict visibility clamp used to run after it, so an agent
        # hire asking for more visibility than its parent holds was refused
        # with 35 credits already moved from the actor to the payer and no node
        # created. Nothing below `_chain_acquire` may raise.
        #
        # D-021: visibility clamps like tools — strict for agent-explicit
        # grants, lenient (warned) for user hires and defaults
        if parent is not None:
            vis, vclamped = self._clamp_vis(
                vis, parent, strict=(actor != USER and org_visibility is not None))
            if vclamped:
                warnings.append(
                    f"org_visibility clamped to the parent's own ({vis})")
        # D-014: the top-level grant cap binds at the source
        if parent is None:
            self._check_top_grant(int(grant), "this hire")
        # §4.6 generalized (user ruling): the parent pays; any shortfall
        # bubbles up the chain to the actor (the user's pool is infinite) —
        # refused only when the WHOLE chain lacks it
        if parent is not None:
            self._chain_acquire(actor, parent, need, warnings,
                                cascade=bool(self.d.get("cascade_hire", True)))

        if tlost:
            warnings.append(f"tool grants clamped to the parent's own: {tlost}")
        # ceiling spec §2/§4: the ceiling clamp runs AFTER defaults resolve and
        # after the parent clamp (parent ∩ ceiling at depth) — org defaults may
        # exceed the ceiling and must lose on every bare chip-click hire
        # all three inputs are non-None here ⇒ the pass-through outputs are too
        tset, dirs, vis, _pm, bridged = cast(
            "tuple[ToolGrant, list[DirGrant], str, str | None, bool]",
            self._apply_ceiling(tools=tset, dirs=dirs, vis=vis,
                                raise_ceiling=raise_ceiling, warnings=warnings))
        nid = self._new_node(tier, parent, int(grant), name, dirs, tset, vis,
                             str(charter).strip() if charter else None)
        # D-030 hardening: the fresh node inherits the ORG-wide
        # permission_mode — clamp it against the kiosk ceiling like set_scope
        # does, or a "default"-ceiling kiosk hires above its own ceiling
        _t3, _d3, _v3, pm3, _b3 = self._apply_ceiling(
            pm=self.nodes[nid]["scope"].get("permission_mode"),
            warnings=warnings)
        if pm3 is not None:
            self.nodes[nid]["scope"]["permission_mode"] = pm3
        # every affected agent is told, WHOEVER acted (user ruling) — the actor
        # itself is skipped (it made the call and got the result)
        gist = (str(charter).strip().splitlines() or [""])[0][:120] if charter else ""
        why = f' Role: {gist}' if gist else ""
        who = "the user" if actor == USER else f'"{actor}"'
        self._notify([p for p in [parent] if p != actor],
                     f'{who.capitalize()} hired "{nid}" ({tier}, grant {int(grant)}) '
                     f'under you.{why}')
        self._notify([p for p in self._peers_of(parent, nid) if p != actor],
                     f'{who.capitalize()} hired "{nid}" ({tier}) alongside you, under '
                     f'{parent or "the top level"}.{why}')
        self._log("hire", actor, {"node": nid, "parent": parent, "tier": tier,
                                  "grant": int(grant), "charter": gist}, warnings)
        res: dict[str, Any] = {"node": nid, "warnings": warnings}
        if bridged:
            # the one-action bridge (spec §1): re-send the SAME op with
            # raise_ceiling=true. The API strips this for visitors/agents —
            # no legal raise path exists for them, so no dangling offer.
            res["bridge"] = {"raise_ceiling": True}
        return res

    def _chain_acquire(self, actor: str, payer: str, need: float,
                       warnings: list[str], cascade: bool = True) -> None:
        """§4.6 GENERALIZED (user ruling): when an action under `payer` costs
        `need` credits, the shortfall beyond the payer's own free bubbles UP
        THE CHAIN — each hop contributes what it has free, grants inflating
        down the path so every hop's invariant holds — refused only when the
        WHOLE chain up to and including the acting agent lacks it. The user
        tops an infinite pool: for user actions any remainder lands as
        top-level grant inflation (kiosk caps still bind via the API check).
        `cascade=False` (the org settings cascade_hire / cascade_alloc, user
        spec): the payer must afford it from its OWN free credits — nothing
        bubbles."""
        if need <= 0:
            return
        if not cascade:
            free = self.free(payer)
            if free < need:
                raise LedgerError(
                    f"{payer} has only {free:g} free of the {need:g} needed, and "
                    f"cost-bubbling is disabled for this action (org setting) — "
                    f"free credits on {payer} first, or re-enable bubbling in "
                    f"the org settings")
            return
        chain = [payer]
        while chain[-1] != actor:
            p = self.node(chain[-1])["parent"]
            if p is None:
                if actor != USER:
                    raise LedgerError(f"{actor} is not on {payer}'s chain")
                break
            chain.append(p)
        frees = [self.free(k) for k in chain]     # snapshot BEFORE inflating
        contrib: list[tuple[int, str, float]]     # (chain index, node, amount)
        remaining, contrib = need, []
        for i, k in enumerate(chain):
            if remaining <= 0:
                break
            c = min(frees[i], remaining)
            if c > 0:
                contrib.append((i, k, c))
                remaining -= c
        if remaining > 0 and actor != USER:
            raise LedgerError(
                f"not enough free credits on the chain: {need:g} needed, only "
                f"{need - remaining:g} free between {payer} and {actor} (§4.6)")
        # D-014 pre-check, BEFORE any mutation: total the planned inflation
        # per node and refuse if a TOP-LEVEL grant would cross the cap —
        # user-actor cascades included (that was the enforcement gap)
        adds: dict[str, float] = {}
        for i, _k, c in contrib:
            for j in range(i):
                adds[chain[j]] = adds.get(chain[j], 0) + c
        if remaining > 0:
            for k in chain:
                adds[k] = adds.get(k, 0) + remaining
        for k, extra in adds.items():
            if self.nodes[k]["parent"] is None:
                self._check_top_grant(self.nodes[k]["grant"] + extra,
                                      "carrying these credits down the chain")
        # a contribution from chain[i] inflates every grant BELOW it, so the
        # credits are actually spendable at the payer
        for i, k, c in contrib:
            for j in range(i):
                # runtime int: frees/need are int-valued here (grants and seats
                # are ints; USER is never on the chain) — float only via free()
                self.nodes[chain[j]]["grant"] += cast(int, c)
            warnings += self._stranding_warnings(k, frees[i], frees[i] - c)
            if i > 0:
                warnings.append(
                    f"§4.6: {c:g} credit(s) bubbled up to {k}; grants below it "
                    f"were inflated to carry them down — reclaim with reallocate")
        if remaining > 0:             # user actor: the infinite pool absorbs it
            for k in chain:
                self.nodes[k]["grant"] += cast(int, remaining)  # runtime int, as above
            warnings.append(
                f"§4.6: {remaining:g} credit(s) drawn from your pool — the "
                f"chain's grants inflated to carry them down; reclaim with "
                f"reallocate when done")

    def _path_down(self, top: str, bottom: str) -> list[str]:
        """Nodes from just below `top` down to `bottom`, inclusive. top may be USER."""
        chain = [bottom] + [a for a in self.ancestors(bottom) if a != USER]
        if top != USER:
            if top not in chain:
                raise LedgerError(f"{top} is not an ancestor of {bottom}")
            chain = chain[:chain.index(top)]
        return list(reversed(chain))

    def _new_node(self, tier: str, parent: str | None, grant: int, name: str,
                  dirs: list[DirGrant], tools: ToolGrant, vis: str,
                  charter: str | None) -> str:
        base = slugify(name)   # any slug is a legal name — actor kinds are typed,
                               # so even "user" or "system" is just a name here
        nid, i = base, 2
        while nid in self.nodes:
            nid, i = f"{base}-{i}", i + 1
        sibs = self.children(parent, live_only=False)
        self.nodes[nid] = {
            "session_id": str(uuid.uuid4()),
            "model": tier,
            "parent": parent,
            "grant": grant,
            "state": "live",           # live | archived | unrecoverable (№31)
            "title": name,
            "charter": charter,
            "created": now(),
            "archived_at": None,
            "pid": None,
            "ui_order": max([self.nodes[s].get("ui_order", 0) for s in sibs],
                            default=-1.0) + 1.0,
            "scope": {
                "permission_mode": self.d["permission_mode"],
                "add_dirs": dirs,
                "tools": tools,
                "org_visibility": vis,
            },
            # §8 lineage axis — second axis, never an org edge
            "lineage": base,
            "generation": 0,
            "predecessor": None,
            "successor": None,
            "bearer_state": None,      # None | knowledge | preserving
            # user ruling 2026-08-02: a new hire is IDLE, not stateless. It has
            # been created and is waiting for work — which is exactly what idle
            # means — and a blank chip read as "unknown" rather than "ready".
            "last_status": {"status": "idle", "summary": "hired — awaiting work",
                            "at": now()},
        }
        return nid

    # ---------------------------------------------------------------- retire
    def retire(self, actor: str, nid: str) -> dict[str, Any]:
        """Archive a node, freeing seat+grant. NOT leaf-only anymore (PLAN §4.2
        decision 1 is superseded by the design motto): a superior retiring a
        node with live reports auto-DISSOLVES the subtree, with a warning.
        Self-retirement stays allowed for leaves only (№26 — an agent has no
        dissolve authority over itself). Already-archived → success no-op."""
        self._require_authority(actor, nid, allow_self=True)
        if self.node(nid)["state"] == "archived":
            # design motto: asking for what's already true is a no-op, not an error
            return {"freed": 0,
                    "warnings": [f"{nid} was already archived — nothing to do"]}
        live_kids = self.children(nid)
        if live_kids:
            if actor == nid:
                # self-retire has no dissolve authority — the one case that stays
                raise LedgerError(
                    f"you have live reports {live_kids}; retire them first, or ask "
                    f"your superior to dissolve your subtree")
            # design motto: auto-bridge to what the old refusal told you to do
            r = self.dissolve(actor, nid)
            r.setdefault("warnings", []).append(
                f"{nid} had live reports {live_kids} — retire became dissolve "
                f"(the whole subtree is archived)")
            return r
        n = self.node(nid)
        freed = self.seat_cost(nid) + n["grant"]
        n["state"] = "archived"
        n["archived_at"] = now()
        # user ruling (2026-07-31): retire is PAGING (§4.3) — audiences survive
        # it, exactly like dirs and tools, and come back live on rehire. Only
        # delete destroys them. (The UI filters archived holders at render.)
        who = ("the user" if actor == USER
               else "itself (self-retirement)" if actor == nid else f'"{actor}"')
        self._notify([p for p in [n["parent"]] if p != actor],
                     f'Your report "{nid}" was retired by {who} (freed {freed} credits).')
        self._notify([p for p in self._peers_of(n["parent"], nid) if p != actor],
                     f'Your peer "{nid}" was retired by {who}.')
        self._log("retire", actor, {"node": nid, "freed": freed}, [])
        return {"freed": freed, "warnings": []}

    # ---------------------------------------------------------------- rehire
    def rehire(self, actor: str, nid: str, grant: int | None = None,
               tier: str | None = None, raise_ceiling: bool = False) -> dict[str, Any]:
        """§4.2. Parent pays seat + grant; may strand the parent's OTHER archived kids.
        `tier` override (№16, spike-verified): a knowledge bearer answers from context
        and can be consulted at a cheaper tier than it ran at.

        Motto bridges (user rulings 2026-07-31):
        - a node may rehire ITS OWN knowledge bearer, which then joins as the
          node's own SUBORDINATE (superior-rehired bearers stay coworkers);
        - rehire under an archived superior rehires the whole chain first
          (a live agent under an archived one is an invalid tree state);
        - rehire of an unrecoverable node becomes a re-seed (fresh session)."""
        own_bearer = (self.nodes.get(nid) or {}).get("successor") == actor
        if not own_bearer:
            self._require_authority(actor, nid)
        n = self.node(nid)
        if n.get("bearer_state") == "lost":
            # RESEED intent, enforced HERE (not just in the UI): a lost
            # generation's transcript is GONE — waking it would boot an empty
            # session under the dead id and present it as institutional
            # memory. The one true impossibility rehire refuses.
            raise LedgerError(
                f"{nid} is a LOST generation — its transcript is gone, so "
                f"there is nothing to consult or resume; its successor "
                f"carries the role forward")
        if n["state"] == "live":
            # design motto: asking for what's already true is a no-op, not an error
            return {"cost": 0, "drive": [],
                    "warnings": [f"{nid} is already live — nothing to do"]}
        # ATOMICITY: the tier NAME was validated far below, after the
        # archived-superior chain had already been rehired — so
        # `rehire(nid, tier="gpt-9")` woke every archived ancestor (spending
        # their parents' credits and sending notices) and only then refused.
        # Input validation belongs before the first mutation.
        if tier is not None and tier not in self.d["tiers"]:
            raise LedgerError(f"unknown tier {tier!r}")
        # kiosk tier cap: an archived over-cap agent re-entering service is
        # "using" that tier — blocked like a fresh hire (reseed too). The
        # EFFECTIVE tier is tested: a rehire that downgrades below the cap
        # is welcome (motto: permit as much as possible); reseed ignores the
        # override, so unrecoverable nodes test their own tier.
        self._check_tier_ceiling(
            n["model"] if n["state"] == "unrecoverable" or tier not in TIERS
            else tier)             # `tier not in TIERS` filtered out None
        if n["state"] == "unrecoverable":
            # motto bridge: the session is dead but the node — name, position,
            # charter, credits, reports, mailbox — is fine. Rehire = re-seed.
            r = self.reseed(actor, nid, str(uuid.uuid4()))
            ignored = [f"grant {grant:g}" if grant is not None else None,
                       f"tier {tier!r}" if tier is not None else None]
            if any(ignored):
                # declared params must never vanish silently (house pattern:
                # success WITH a warning naming what was ignored)
                r.setdefault("warnings", []).append(
                    "re-seed keeps the node's own grant and tier — the "
                    "requested " + " and ".join(x for x in ignored if x)
                    + " was ignored")
            r.setdefault("cost", 0)
            r.setdefault("drive", [nid] if (self.d.get("mail") or {}).get(nid) else [])
            return r
        warnings: list[str] = []
        drive: list[str] = []
        # user ruling: a live agent under an archived agent is an invalid tree
        # state — rehiring a deep node rehires every ARCHIVED superior between
        # it and the nearest live one first, costs bubbling like any acquire.
        # An UNRECOVERABLE ancestor stops the walk: silently re-seeding it
        # would archive a real session as a lost generation as a side effect —
        # that destruction stays an explicit decision (review C12)
        chain: list[str] = []
        p = n["parent"]
        while p is not None and self.nodes[p]["state"] != "live":
            if self.nodes[p]["state"] == "unrecoverable":
                raise LedgerError(
                    f'"{p}" above {nid} is UNRECOVERABLE — rehiring {nid} '
                    f'would silently re-seed it (its dead session would be '
                    f'archived as a lost generation). Re-seed or retire '
                    f'"{p}" first, then rehire {nid}.')
            chain.append(p)
            p = self.nodes[p]["parent"]
        for k in reversed(chain):                      # top-most first
            r = self.rehire(actor, k)
            warnings += r.get("warnings", [])
            drive += r.get("drive", [])
            warnings.append(
                f'"{k}" was archived above {nid} — rehired first, so the '
                f'chain of command is whole')
        fable_futile = (n["model"] == "fable" or tier == "fable") \
            and bool(self.d.get("fable_lock"))
        if fable_futile and actor == USER:
            self.clear_fable_lock()   # a user fable-rehire IS the decree
            fable_futile = False
        if tier is not None:
            if tier not in self.d["tiers"]:
                raise LedgerError(f"unknown tier {tier!r}")
            n["model"] = tier
        if own_bearer and n["parent"] != actor:
            # user ruling: a self-hired bearer is the node's OWN subordinate —
            # the successor commands it (and pays its seat), unlike a
            # superior-rehired bearer, which stays a coworker in the old slot
            n["parent"] = actor
            warnings.append(
                f'{nid} joins as YOUR subordinate (you woke your own '
                f'predecessor) — you command it and pay its seat')
        parent = n["parent"]
        grant = n["grant"] if grant is None else int(grant)
        if parent is None and grant > n["grant"]:
            self._check_top_grant(grant, "this rehire")   # D-014
        need = self.seat_cost(nid) + grant
        if parent is not None:
            # §4.6 generalized: the parent pays; shortfall bubbles up to the actor
            self._chain_acquire(actor, parent, need, warnings,
                                cascade=bool(self.d.get("cascade_hire", True)))
        if fable_futile:
            warnings.append("the weekly Fable usage limit is exhausted — this agent "
                            "will not be able to run yet; rehiring it now is futile")

        # №30: grants re-validate against the parent's CURRENT capability at rehire
        kept, lost = self._clamp_dirs(
            n["scope"]["add_dirs"], self.effective_dirs(parent), strict=False)
        if lost:
            n["scope"]["add_dirs"] = kept
            warnings.append(f"dir grants adjusted to the parent's capability (№30): {lost}")
        ptools = None if parent is None else self.node(parent)["scope"]["tools"]
        tkept, tlost = self._clamp_tools(n["scope"]["tools"], ptools, strict=False)
        n["scope"]["tools"] = tkept
        if tlost:
            warnings.append(f"tool grants adjusted to the parent's capability: {tlost}")
        v, vclamped = self._clamp_vis(
            n["scope"].get("org_visibility", "full"), parent, strict=False)
        if vclamped:
            n["scope"]["org_visibility"] = v
            warnings.append(
                f"org_visibility adjusted to the parent's capability ({v})")
        # kiosk ceiling: №30's revalidation extends to the ceiling — a node
        # archived before the ceiling changed re-enters within it
        # tools/dirs inputs are non-None ⇒ their pass-through outputs are too
        ct, cd, cv, cp, bridged = cast(
            "tuple[ToolGrant, list[DirGrant], str | None, str | None, bool]",
            self._apply_ceiling(
                tools=n["scope"]["tools"], dirs=n["scope"]["add_dirs"],
                vis=n["scope"].get("org_visibility"),
                pm=n["scope"].get("permission_mode"),
                raise_ceiling=raise_ceiling, warnings=warnings))
        n["scope"]["tools"], n["scope"]["add_dirs"] = ct, cd
        if cv is not None:
            n["scope"]["org_visibility"] = cv
        if cp is not None:
            n["scope"]["permission_mode"] = cp

        n["state"] = "live"
        n["grant"] = grant
        n["archived_at"] = None
        who = "the user" if actor == USER else f'"{actor}"'
        self._notify([p for p in [parent] if p != actor],
                     f'Your report "{nid}" was rehired by {who} (grant {grant}).')
        self._notify([p for p in self._peers_of(parent, nid) if p != actor],
                     f'Your peer "{nid}" was rehired by {who}.')
        self._notify([nid], f"{who.capitalize()} rehired you. You are live again; "
                            f"your prior context is intact.")
        self._log("rehire", actor, {"node": nid, "grant": grant}, warnings)
        # mail that arrived while archived waited in the inbox (user ruling) —
        # tell the caller to drive the node so it finally acts on it
        if (self.d.get("mail") or {}).get(nid):
            drive.append(nid)
        res: dict[str, Any] = {"cost": need, "warnings": warnings, "drive": drive}
        if bridged:
            res["bridge"] = {"raise_ceiling": True}
        return res

    def _taken_with(self, nid: str) -> set[str]:
        """Every node that goes when `nid` goes: org descendants AND lineage
        stacks, to a FIXPOINT.

        The fixpoint is the part that was missing. A lineage bearer can acquire
        org children of its own — rehire a bearer (a superior-rehired one keeps
        the OLD parent slot, so it is a sibling of its successor, not a
        descendant) and hire under it. Adding each node's stack without
        re-descending into it then left those children behind, two ways:
        `dissolve` archived the bearer and stranded its subtree LIVE under an
        archived parent (the "invalid tree state" rehire refuses to create, and
        the stranded seats were then committed by nobody — the parent's free
        jumped by their holding); `delete` removed the bearer outright and left
        a DANGLING parent id, so `ancestors()` raised KeyError instead of a
        LedgerError. Found 2026-08-04 by the authority suite's property test."""
        out: set[str] = set()
        frontier = [nid]
        while frontier:
            k = frontier.pop()
            if k in out or k not in self.nodes:
                continue
            out.add(k)
            frontier.extend(self.children(k, live_only=False))
            frontier.extend(self.lineage_stack(k))
        return out

    # --------------------------------------------------------------- dissolve
    def dissolve(self, actor: str, nid: str) -> dict[str, Any]:
        """Recursive retire, deepest first (§4.2). Takes the whole lineage stack (§8.5)."""
        self._require_authority(actor, nid)
        parent = self.node(nid)["parent"]
        # §8.5: dissolve takes each node's ENTIRE lineage stack with it
        order = sorted(self._taken_with(nid), key=self.depth, reverse=True)
        freed = 0
        for k in order:
            n = self.nodes[k]
            if n["state"] in ("live", "unrecoverable"):
                freed += self.seat_cost(k) + n["grant"]
                n["state"] = "archived"
                n["archived_at"] = now()
            # audiences survive dissolve too (paging, user ruling) — see retire
        who = "the user" if actor == USER else f'"{actor}"'
        self._notify([p for p in [parent] if p != actor],
                     f'{who.capitalize()} dissolved your report "{nid}" and its whole '
                     f'suborganization ({len(order)} node(s), freed {freed} credits).')
        self._notify([p for p in self._peers_of(parent, nid) if p != actor],
                     f'Your peer "{nid}" and its suborganization were dissolved '
                     f'by {who}.')
        self._log("dissolve", actor, {"node": nid, "freed": freed,
                                      "count": len(order)}, [])
        return {"freed": freed, "nodes": order, "warnings": []}

    # ----------------------------------------------------------------- delete
    def cost_total(self) -> float:
        """Org spend INCLUDING deleted agents' burn (user bug 2026-07-31:
        deleting agents shrank the total — undercounting the dashboard and,
        worse, walking the enforced kiosk SPEND LIMIT backwards). Cost is
        history, not a node property; the tombstone accumulator keeps every
        dollar ever burned."""
        return round(sum(float(v.get("cost_usd") or 0.0)
                         for v in self.nodes.values())
                     + float(self.d.get("deleted_cost_usd") or 0.0), 4)

    def delete(self, actor: str, nid: str) -> dict[str, Any]:
        """Permanent removal — USER ONLY (ruling). Agents may at most retire an
        agent and then ask the user if they truly want it deleted. Takes the whole
        subtree and every lineage stack; erases records, mail and audiences. Session
        transcripts on disk are NOT touched."""
        if actor_kind(actor) != "user":
            raise LedgerError(
                "only the user may delete agents — retire instead, and ask the user "
                "(via your chain or inbox) if permanent removal is truly warranted")
        n = self.node(nid)
        parent = n["parent"]
        peers = self._peers_of(parent, nid)
        doomed_set = self._taken_with(nid)
        # bank the burn BEFORE the nodes go — cost is history (see cost_total)
        lost = round(sum(float((self.nodes.get(k) or {}).get("cost_usd") or 0.0)
                         for k in doomed_set), 6)
        if lost:
            self.d["deleted_cost_usd"] = round(
                float(self.d.get("deleted_cost_usd") or 0.0) + lost, 6)
        for k in doomed_set:
            self.nodes.pop(k, None)
            (self.d.get("mail") or {}).pop(k, None)
            (self.d.get("mail_log") or {}).pop(k, None)
            (self.d.get("notices") or {}).pop(k, None)
            (self.d.get("steered_log") or {}).pop(k, None)
        self.d["audiences"] = [
            a for a in self.d["audiences"]
            if a["grantee"] not in doomed_set and a["grantor"] not in doomed_set
            and a.get("delegated_by") not in doomed_set]
        self.d["audience_requests"] = [
            r for r in self.d["audience_requests"]
            if r["from"] not in doomed_set and r["target"] not in doomed_set
            and r["currently_at"] not in doomed_set]
        # a pending credit request must not outlive its node: the freed slug
        # can be re-minted by a later hire, and a stale approval would re-bind
        # to the namesake (review: swept-from-three-sites-not-the-fourth)
        self.d["credit_requests"] = [
            r for r in self.d.get("credit_requests", [])
            if r.get("node") not in doomed_set]
        extra = len(doomed_set) - 1
        self._notify([parent],
                     f'The user permanently DELETED your report "{nid}"'
                     + (f" and its suborganization ({extra} more node(s))" if extra else "")
                     + ". Its records are gone from the org.")
        self._notify(peers, f'Your peer "{nid}" was permanently deleted by the user.')
        self._log("delete", actor, {"node": nid, "removed": sorted(doomed_set),
                                    **({"cost_usd": lost} if lost else {})}, [])
        return {"deleted": sorted(doomed_set), "warnings": []}

    # ------------------------------------------------------------- reallocate
    def switch_model(self, actor: str, nid: str, tier: str) -> dict[str, Any]:
        """User spec: swap an agent's model ON THE FLY, mid-life — the session
        survives (№16: --resume honors a changed --model; the next turn runs
        the new model). CHEAPER: the seat difference melts into the node's own
        grant — holding unchanged, free grows. PRICIER: paid from the node's
        own free first; the shortfall bubbles up the chain to the actor
        (§4.6-generalized). Agents may switch models anywhere in their
        SUBTREE, but never their own (user spec); the user switches anyone."""
        if tier not in self.d["tiers"]:
            raise LedgerError(f"unknown tier {tier!r}; know {sorted(self.d['tiers'])}")
        self._require_live(nid)
        n = self.node(nid)
        if actor != USER:
            if actor == nid:
                raise LedgerError("you cannot switch your OWN model (user "
                                  "ruling) — your superior or the user can")
            if not self.is_ancestor(actor, nid):
                raise LedgerError("model switches cover your own subtree only")
        old = n["model"]
        if tier == old:
            # design motto: asking for what's already true is a no-op, not an error
            return {"model": tier, "seat": self.d["tiers"][tier], "freed": 0,
                    "warnings": [f"{nid} already runs {tier} — nothing to do"]}
        # the kiosk tier cap is checked HERE, after the no-op return and after
        # the authority checks. It used to run first, so switching a
        # grandfathered over-cap agent to the tier it ALREADY runs was refused
        # ("opus agents cannot be switched to") — a hard error for a request
        # that would change nothing, against the ratified idempotent-no-op rule.
        # It also leaked the cap to actors with no authority over the node.
        self._check_tier_ceiling(tier)
        if tier == "fable" and self.d.get("fable_lock") and actor == USER:
            self.clear_fable_lock()      # a user fable-switch is the decree
        delta = self.d["tiers"][tier] - self.d["tiers"][old]
        warnings: list[str] = []
        if delta <= 0:
            # seat shrinks; the difference becomes the node's own free
            # allocation — its total holding (and the parent's commitment)
            # never moves
            if n["parent"] is None and delta < 0:
                # D-014: even the downgrade-melt may not push a top-level
                # grant past the cap — reallocate the excess down first
                self._check_top_grant(n["grant"] - delta, "this downgrade")
            n["model"] = tier
            n["grant"] += -delta
        else:
            own = min(self.free(nid), delta)   # the node's own free absorbs first
            shortfall = delta - own
            if shortfall > 0:
                if n["parent"] is None and actor != USER:
                    raise LedgerError("only the user funds a top-level upgrade")
                if n["parent"] is not None:
                    self._chain_acquire(actor, n["parent"], shortfall, warnings,
                                        cascade=bool(self.d.get("cascade_alloc", True)))
            n["model"] = tier
            # runtime int: own = min(free, delta), both int-valued for a real node
            n["grant"] -= cast(int, own)   # holding grows by exactly the shortfall
        who = "the user" if actor == USER else f'"{actor}"'
        self._notify([x for x in [nid] if x != actor],
                     f'{who.capitalize()} switched your model {old}→{tier} '
                     f'(seat {self.d["tiers"][old]}→{self.d["tiers"][tier]}). '
                     f'Your context is intact — carry on.')
        self._notify([x for x in [n["parent"]] if x not in (actor, None)],
                     f'{who.capitalize()} switched "{nid}" {old}→{tier}.')
        self._log("switch_model", actor,
                  {"node": nid, "from": old, "to": tier}, warnings)
        return {"model": tier, "seat": self.d["tiers"][tier],
                "freed": max(0, -delta), "warnings": warnings}

    def reallocate(self, actor: str, nid: str, delta: int) -> dict[str, Any]:
        """±Δ between a node and its parent (§4.2). -Δ is the classic stranding op."""
        self._require_authority(actor, nid)
        self._require_live(nid)
        n = self.node(nid)
        delta = int(delta)
        warnings: list[str] = []
        if delta > 0:
            if n["parent"] is None:
                self._check_top_grant(n["grant"] + delta, "this allocation")  # D-014
            else:
                # §4.6 generalized: shortfall bubbles up the chain to the actor
                self._chain_acquire(actor, n["parent"], delta, warnings,
                                    cascade=bool(self.d.get("cascade_alloc", True)))
        elif delta < 0:
            if self.free(nid) < -delta:
                raise LedgerError(
                    f"{nid} has only {self.free(nid):g} unused; the rest is committed")
            warnings += self._stranding_warnings(
                nid, self.free(nid), self.free(nid) + delta)
        n["grant"] += delta
        if delta != 0:
            who = "the user" if actor == USER else f'"{actor}"'
            self._notify([x for x in [nid] if x != actor],
                         f"{who.capitalize()} adjusted your grant by {delta:+d} "
                         f"(now {n['grant']}, free {self.free(nid):g}).")
            self._notify([x for x in [n["parent"]] if x != actor],
                         f'{who.capitalize()} adjusted "{nid}"\'s grant by {delta:+d}.')
        self._log("reallocate", actor, {"node": nid, "delta": delta}, warnings)
        return {"grant": n["grant"], "warnings": warnings}

    # --------------------------------------------------------- promote/demote
    def promote(self, actor: str, nid: str, new_parent: str | None) -> dict[str, Any]:
        """Re-parent upward (§4.5): new_parent must be a strict ancestor of the current
        parent (None = to top level, actor must be USER)."""
        cur = self.parent(nid)
        target = USER if new_parent is None else new_parent
        # audit finding: the docstring promised this and the code never
        # enforced it — top level is the privileged class (unbidden user
        # mail, org voice, extern recipients), so only the user seats it
        if new_parent is None and actor != USER:
            raise LedgerError("only the user promotes agents to top level (§7.4)")
        if new_parent is None:
            # D-014: promotion may not seat an over-cap grant at top level
            self._check_top_grant(self.node(nid)["grant"], "this promotion")
        if target != USER and not self.is_ancestor(target, nid):
            raise LedgerError(f"promote target {target} is not above {nid}")
        if target == cur:
            raise LedgerError(f"{nid} already reports to {cur}")
        if cur != USER and target != USER and not self.is_ancestor(target, cur):
            raise LedgerError("promote must move the node strictly upward (§4.2)")
        return self._move("promote", actor, nid, new_parent)

    def demote(self, actor: str, nid: str, new_parent: str) -> dict[str, Any]:
        """Re-parent downward/lateral under another of the actor's descendants (§4.5)."""
        if new_parent == nid or new_parent in self.descendants(nid, live_only=False):
            raise LedgerError("cannot demote a node into its own subtree — cycle (§4.5)")
        return self._move("demote", actor, nid, new_parent)

    def move(self, actor: str, nid: str, new_parent: str | None) -> dict[str, Any]:
        """§4.5 unified reorganization verb (gap audit №7): promote or demote,
        decided by direction — the capability the design derived (§4.5: a
        fully-occupied tree can still reorganize) and only the user could
        reach until now. Same-parent = success no-op (motto A3)."""
        # the RAW parent slot (None at top level) — parent()'s USER sentinel
        # made every top-level source blow up downstream (ancestors("@user"))
        # and leaked the sentinel into user-facing messages
        cur = self.node(nid)["parent"]
        tgt = None if new_parent in (None, USER) else new_parent
        if tgt == cur:
            return {"warnings": [f"{nid} already reports to "
                                 f"{tgt or 'the top level'} — nothing to do"]}
        if tgt is None or (cur is not None
                           and self.is_ancestor(tgt, cur)):
            return self.promote(actor, nid, tgt)
        return self.demote(actor, nid, tgt)

    def _move(self, op: str, actor: str, nid: str,
              new_parent: str | None) -> dict[str, Any]:
        """§4.5 LCA credit path. Release P_old→L and acquire L→P_new cancel hop by hop,
        so every node's free is unchanged — budget-neutral, cannot fail on credits."""
        self._require_authority(actor, nid)
        n = self.node(nid)
        p_old = n["parent"]
        if new_parent is not None:
            self._require_live(new_parent)
            self._require_authority(actor, new_parent, allow_self=True)
            # ⚠ The guard must cover EVERY node this move reparents, and that is
            # not just `nid`'s subtree: the loop near the end of this method
            # reparents the whole LINEAGE STACK to `new_parent` too (§8.5, the
            # stack shares the successor's slot). A bearer that was stranded
            # with org children of its own — `reseed`'s own-successor branch
            # leaves exactly that — could therefore host `new_parent` below it
            # while not being below `nid`, and the old check waved it through.
            # Result: a REAL 2-cycle in the parent graph (`a@0.parent == "b"`
            # and `b.parent == "a@0"`), reproduced 2026-08-04 by the credit
            # conservation fuzzer. The cycle guards on ancestors()/
            # lineage_stack() stop it hanging; they do not stop it existing,
            # and a cyclic org is corrupt whether or not the walk terminates.
            moved = {nid, *self.lineage_stack(nid)}
            forbidden = set(moved)
            for m in moved:
                forbidden |= set(self.descendants(m, live_only=False))
            if new_parent in forbidden:
                raise LedgerError("target is inside the moved subtree — cycle (§4.5)")
        if p_old is not None:
            self._require_authority(actor, p_old, allow_self=True)

        # №34 runaway insurance binds REORGANIZATION too (user ruling
        # 2026-08-04, closing the D-A/D-B pins). `hire` refused past the caps
        # and `move` did not, so a subtree could simply be dragged past them —
        # and since a drag is how a runaway would re-shape a tree it had
        # already been refused permission to grow, the hole defeated the
        # insurance rather than merely bending a rule. Measured against the
        # WHOLE moved subtree: the deepest leaf under `nid` is what actually
        # ends up deepest, not `nid` itself.
        if new_parent is not None:
            cap_d = self.d.get("max_depth", MAX_DEPTH)
            sub = self.descendants(nid, live_only=False)
            rel = max((self.depth(k) for k in sub), default=self.depth(nid)) \
                - self.depth(nid)
            if self.depth(new_parent) + 1 + rel >= cap_d:
                raise LedgerError(
                    f"max org depth {cap_d} reached — moving {nid} under "
                    f"{new_parent} would seat its deepest report at "
                    f"{self.depth(new_parent) + 1 + rel}")
            cap_c = self.d.get("max_children", MAX_CHILDREN)
            if new_parent != p_old \
                    and len(self.org_children(new_parent)) >= cap_c:
                raise LedgerError(
                    f"{new_parent} already has {cap_c} reports (cap)")

        # §8.5: a bearer occupies its SUCCESSOR's slot and is not an org node of
        # its own, so it may not be re-parented on its own — doing so split the
        # stack from the live agent that owns it and left the bearer showing up
        # in `descendants()` of a branch it never belonged to.
        succ = n.get("successor")
        if succ and succ in self.nodes:
            raise LedgerError(
                f'{nid} is a lineage bearer of "{succ}" — the stack shares its '
                f'successor\'s slot (§8.5). Move "{succ}" and the stack '
                f'follows it.')
        live_bearers = [k for k in self.lineage_stack(nid)
                        if self.nodes[k]["state"] != "archived"]
        if live_bearers:
            raise LedgerError(
                f"{nid} has live lineage bearer(s) {live_bearers} under consultation — "
                f"retire them first, then move (the stack moves with the node)")
        c = 0 if n["state"] != "live" else self.seat_cost(nid) + n["grant"]
        warnings: list[str] = []
        if n["state"] != "live":
            warnings.append(
                f"{nid} is archived: moving it is free, but its rehire cost "
                f"({self.seat_cost(nid) + n['grant']}) now falls on {new_parent or USER} (§4.5)")

        lca = self._lca(p_old, new_parent)
        down = (self._path_down(lca if lca is not None else USER, new_parent)
                if new_parent is not None else [])
        if c:
            # D-014, the hole the docket carried: the ACQUIRE leg inflates every
            # grant on the way down to the new parent, and when the move crosses
            # the root boundary (lca == USER) the first of those is a TOP-LEVEL
            # grant. Nothing checked it, so a drag across roots reached a number
            # `reallocate` refuses to type — the cap was enforced on one route to
            # the same end state and not the other. Pre-check BEFORE any
            # mutation, exactly as `_chain_acquire` does, so a refusal leaves the
            # tree untouched. (Release only ever shrinks; a grant on that leg is
            # >= c by the free>=0 invariant, so it cannot go negative.)
            for hop in down:
                if self.nodes[hop]["parent"] is None:
                    self._check_top_grant(
                        self.nodes[hop]["grant"] + c,
                        f"moving {nid} under {new_parent}")
            # ⚠ The docstring above claims the release leg "cannot fail on
            # credits" because a grant on it is >= c by the free>=0 invariant.
            # That holds only while the invariant does. `reseed`'s own-successor
            # branch can zero a stranded bearer's grant while its children still
            # hang off it, and then this subtraction ran unconditionally and
            # produced a NEGATIVE grant — measured -7 and -13 by the credit
            # conservation fuzzer 2026-08-04, on moves that raised nothing and
            # left an ancestor's free() lower than it started (so not
            # budget-neutral either, against this method's own contract).
            # Refuse rather than corrupt: a negative grant is not a state any
            # later operation is written to survive.
            for hop in self._chain_up(p_old, lca):
                if self.nodes[hop]["grant"] < c:
                    raise LedgerError(
                        f"cannot move {nid}: {hop} holds a grant of "
                        f"{self.nodes[hop]['grant']}, less than the {c} this "
                        f"move must release through it — the chain's accounting "
                        f"is inconsistent (§4.5)")
            for hop in self._chain_up(p_old, lca):     # release: grants shrink
                self.nodes[hop]["grant"] -= c
            for hop in down:                           # acquire: grants swell
                self.nodes[hop]["grant"] += c

        prior_peers = self._peers_of(p_old, nid)
        n["parent"] = new_parent
        for k in self.lineage_stack(nid):     # §8.5: the stack occupies the same slot
            self.nodes[k]["parent"] = new_parent
        swept = self._sweep_audiences()
        warnings += [f"audience revoked (no longer ancestral): {g}→{t}" for g, t in swept]
        dropped = self._sweep_dirs(nid)
        if dropped:
            warnings.append(f"dirs not held by the new chain were dropped (№30): {dropped}")
        who = "the user" if actor == USER else f'"{actor}"'
        subtree = len(self.descendants(nid, live_only=False))
        tail = f" Its suborganization ({subtree} node(s)) moved with it." if subtree else ""
        frm, to = p_old or "the top level", new_parent or "the top level"
        self._notify([p for p in [p_old] if p != actor],
                     f'{who.capitalize()} moved your report "{nid}" away — it now '
                     f'reports to {to}.{tail}')
        self._notify([p for p in prior_peers if p != actor],
                     f'Your peer "{nid}" was moved by {who} to under {to}.{tail}')
        self._notify([p for p in [new_parent] if p != actor],
                     f'{who.capitalize()} moved "{nid}" (from {frm}) to report to '
                     f'you.{tail}')
        self._notify([p for p in self._peers_of(new_parent, nid) if p != actor],
                     f'"{nid}" joined your team (moved by {who} from {frm}).{tail}')
        self._notify([nid],
                     f"{who.capitalize()} moved you: you now report to {to} (you were "
                     f"under {frm}). Your entire suborganization moved with you.")
        self._log(op, actor, {"node": nid, "from": p_old, "to": new_parent}, warnings)
        return {"warnings": warnings}

    def _chain_up(self, frm: str | None, until: str | None) -> list[str]:
        """Node ids from `frm` up to but excluding `until` (None = USER)."""
        out: list[str] = []
        cur = frm
        while cur is not None and cur != until:
            out.append(cur)
            cur = self.nodes[cur]["parent"]
        return out

    def _lca(self, a: str | None, b: str | None) -> str | None:
        """Lowest common ancestor of two (possibly None=USER) parent slots."""
        if a is None or b is None:
            return None
        aa = [a] + [x for x in self.ancestors(a) if x != USER]
        bset = {b} | {x for x in self.ancestors(b) if x != USER}
        for x in aa:
            if x in bset:
                return x
        return None

    # ------------------------------------------------------------------ dirs
    def revoke_dir(self, actor: str, nid: str, dir_: str) -> dict[str, Any]:
        """№30 explicit revoke — cascades into the subtree (their sets must stay ⊆)."""
        self._require_authority(actor, nid)
        removed: list[str] = []
        for k in [nid] + self.descendants(nid, live_only=False):
            dirs = self.nodes[k]["scope"]["add_dirs"]
            if any(d["path"] == dir_ for d in dirs):
                self.nodes[k]["scope"]["add_dirs"] = [d for d in dirs if d["path"] != dir_]
                removed.append(k)
        self._log("revoke_dir", actor, {"node": nid, "dir": dir_, "removed": removed}, [])
        return {"removed_from": removed, "warnings": []}

    def _clamp_vis(self, requested: str, parent: str | None,
                   strict: bool) -> tuple[str, bool]:
        """D-021 (user ruling 2026-08-01): org_visibility is a CAPABILITY —
        child ≤ parent, exactly like dirs and tools. Returns (vis, clamped);
        strict=True raises instead of clamping (agent-explicit grants)."""
        if parent is None or requested not in VIS_LEVELS:
            return requested, False
        pv = self.node(parent)["scope"].get("org_visibility", "full")
        if pv in VIS_LEVELS and VIS_LEVELS.index(requested) > VIS_LEVELS.index(pv):
            if strict:
                raise LedgerError(
                    f"org_visibility {requested!r} exceeds the parent's own "
                    f"{pv!r} — visibility is a capability and only shrinks "
                    f"downward")
            return pv, True
        return requested, False

    def _check_top_grant(self, new_grant: float, ctx: str) -> None:
        """D-014 (user ruling 2026-08-01): `max_top_grant` is a REAL ledger
        precondition — no op, user-actor cascades included, may push a
        TOP-LEVEL grant past it. 0/unset = uncapped; existing over-cap
        grants are grandfathered (only increases are refused)."""
        cap = int(self.d.get("max_top_grant") or 0)
        if cap and new_grant > cap:
            raise LedgerError(
                f"{ctx} would put a top-level grant at {new_grant:g}, past "
                f"the org's top-level grant cap of {cap} — raise the cap in "
                f"the org settings, or lower the ask")

    def _sweep_dirs(self, nid: str) -> list[str]:
        """After a move or scope shrink: clamp the subtree's dirs, tools AND
        visibility to each parent in turn (№30 + D-021 — capability sets stay
        ⊆ all the way down)."""
        dropped: list[str] = []

        def clamp(k: str, allowed: dict[str, str] | None,
                  ptools: ToolGrant | None, pvis: str | None) -> None:
            sc = self.nodes[k]["scope"]
            kept, lost = self._clamp_dirs(sc["add_dirs"], allowed, strict=False)
            sc["add_dirs"] = kept
            dropped.extend(lost)
            had_star = "*" in (sc.get("tools", {}).get("mcp") or [])
            tkept, tlost = self._clamp_tools(sc["tools"], ptools, strict=False)
            sc["tools"] = tkept
            dropped.extend(tlost)
            if had_star and "*" not in tkept["mcp"]:
                # the same semantic change `_apply_ceiling` names: "*" meant
                # "every server, present AND future" and is now a fixed list,
                # so registry additions will no longer reach this node. The
                # sweep collapsed it in silence until 2026-08-04.
                dropped.append(f"mcp:* ({k} materialized to the parent's list)")
            v = sc.get("org_visibility", "full")
            if (pvis in VIS_LEVELS and v in VIS_LEVELS
                    and VIS_LEVELS.index(v) > VIS_LEVELS.index(pvis)):
                sc["org_visibility"] = pvis
                dropped.append(f"visibility:{k}→{pvis}")
            own: dict[str, str] = {d["path"]: d["mode"] for d in kept}
            for ch in self.children(k, live_only=False):
                clamp(ch, own, tkept, sc.get("org_visibility", "full"))

        parent = self.node(nid)["parent"]
        clamp(nid, self.effective_dirs(parent),
              None if parent is None else self.node(parent)["scope"]["tools"],
              None if parent is None
              else self.node(parent)["scope"].get("org_visibility", "full"))
        return sorted(set(dropped))

    # ------------------------------------------------------------- node scope
    EFFORTS: Final = ("low", "medium", "high", "xhigh", "max")

    # What an unconfigured turn runs at. The CLI HAS a default but does not
    # document it and does not report it (checked: `--help` names no default,
    # and `system/init` carries no effort field), so the only way for orgtree
    # to state the level truthfully is to stop depending on an implicit one and
    # pass --effort on every turn. "high" is what opus resolved to unaided
    # — measured across 54 records — so this pins existing behaviour rather
    # than changing it, and makes the other tiers explicit at the same level.
    DEFAULT_EFFORT: Final = "high"

    def effective_effort(self, nid: str) -> str:
        """The effort a turn launches with: the node's own, else the org
        default, else DEFAULT_EFFORT. NEVER empty — every turn passes an
        explicit --effort, which is what lets the ⚙ control state a level
        instead of a shrug.

        The org default is read LIVE at turn time (user ruling 2026-08-01:
        visible inherit), so this is DERIVED and never stored. The supervisor
        asks this rather than recomputing it, because the UI asks it too: the
        control read configuration while the runtime read something else, and
        an unconfigured agent showed nothing at all (user bug 2026-08-02,
        reported three times — first fix read only scope.effort, second fell
        back to a transcript field the CLI stamps on some tiers and not
        others). One function, one answer, and orgtree causes it."""
        eff = (self.node(nid)["scope"].get("effort")
               or self.d.get("default_effort") or "")
        return eff if eff in self.EFFORTS else self.DEFAULT_EFFORT

    def versions_for(self, tier: str) -> dict[str, str]:
        """The model versions selectable within a tier ({} = no choice)."""
        return dict(MODEL_VERSIONS.get(tier) or {})

    def model_for(self, nid: str) -> str:
        """The `--model` id for this node: its chosen VERSION when it recorded
        a valid one for its CURRENT tier, else the tier default.

        Derived, never stored, for the same reason `effort_for` is: the tier
        can change under a node (switch_model), and a version recorded for the
        old tier must not follow it there. An unknown or stale value falls back
        silently — a bad string in a doc must never be able to stop a turn."""
        n = self.node(nid)
        tier = n["model"]
        want = n["scope"].get("model_version")
        if want:
            got = self.versions_for(tier).get(want)
            if got:
                return got
        return self.d["models"].get(tier, tier)

    def set_scope(self, actor: str, nid: str, add_dirs: list[Any] | None = None,
                  tools: Mapping[str, Any] | None = None,
                  org_visibility: str | None = None,
                  permission_mode: str | None = None,
                  charter: str | None = None, team_charter: str | None = None,
                  effort: str | None = None, model_version: str | None = None,
                  raise_ceiling: bool = False) -> dict[str, Any]:
        """Per-node configuration (the ⚙): dir grants with modes, the full tool set
        (built-ins + MCP servers), org-structure visibility. Superior-only.
        Kiosk ceiling (spec §2): permission fields clamp against parent ∩
        ceiling; charter/team_charter/effort pass unclamped (not permissions —
        effort is a cost dial by user ruling and applies under any ceiling)."""
        self._require_authority(actor, nid)
        n = self.node(nid)
        sc = n["scope"]
        warnings: list[str] = []
        changed_caps = False
        bridged = False
        # ATOMICITY (2026-08-04): every refusal happens in THIS block, before a
        # single field is written. The three capability fields used to be
        # validated-and-applied one at a time, so a call carrying a legal
        # `add_dirs` and an illegal `tools` grant wrote the dirs, refused, and
        # never ran the subtree sweep — half a retool, reported as a failure.
        # `_apply_ceiling(raise_ceiling=True)` also grows the ceiling itself, so
        # every strict parent clamp has to pass before ANY of it runs.
        want_dirs: list[DirGrant] | None = None
        want_tools: ToolGrant | None = None
        want_vis: str | None = None
        if add_dirs is not None:
            want_dirs, _ = self._clamp_dirs(
                norm_dirs(add_dirs), self.effective_dirs(n["parent"]), strict=True)
        if tools is not None:
            ptools = (None if n["parent"] is None
                      else self.node(n["parent"])["scope"]["tools"])
            want_tools, _ = self._clamp_tools(tools, ptools, strict=True)
        if org_visibility is not None:
            if org_visibility not in VIS_LEVELS:
                raise LedgerError(f"org_visibility must be one of {VIS_LEVELS}")
            # D-021: parent clamp first (strict, like dirs/tools here), then
            # the kiosk ceiling
            want_vis, _ = self._clamp_vis(org_visibility, n["parent"], strict=True)
        if permission_mode is not None and permission_mode not in PM_LEVELS:
            raise LedgerError(                     # D-030 hardening
                f"permission_mode must be one of {PM_LEVELS}")
        # user-approved (2026-07-31): thinking effort as a per-agent setting,
        # adjusted from the gear — never a hire-row control. "" clears back to
        # the CLI default. (No ultracode tier: orgtree replaces subagent
        # semantics with real hires.)
        if effort is not None and effort not in self.EFFORTS and effort != "":
            raise LedgerError(
                f"effort must be one of {self.EFFORTS} (or '' to clear)")
        # a VERSION is neither a permission nor a price, so it clamps against
        # nothing — exactly like effort. Validated against the node's CURRENT
        # tier so a stale choice can never be written in the first place.
        if model_version is not None and model_version != "":
            _ok = self.versions_for(n["model"])
            if model_version not in _ok:
                raise LedgerError(
                    f"{n['model']} has no model version {model_version!r}"
                    + (f" — know {sorted(_ok)}" if _ok
                       else " (this tier has a single model)"))

        if want_dirs is not None:
            _t, kept, _v, _p, b = self._apply_ceiling(
                dirs=want_dirs, raise_ceiling=raise_ceiling, warnings=warnings)
            bridged = bridged or b
            sc["add_dirs"] = cast("list[DirGrant]", kept)  # dirs in ⇒ dirs out
            changed_caps = True
        if want_tools is not None:
            tset, _d, _v, _p, b = self._apply_ceiling(
                tools=want_tools, raise_ceiling=raise_ceiling, warnings=warnings)
            bridged = bridged or b
            sc["tools"] = cast(ToolGrant, tset)  # tools in ⇒ tools out
            changed_caps = True
        if want_vis is not None:
            _t, _d, vis2, _p, b = self._apply_ceiling(
                vis=want_vis, raise_ceiling=raise_ceiling, warnings=warnings)
            bridged = bridged or b
            sc["org_visibility"] = cast(str, vis2)  # vis in ⇒ vis out
            changed_caps = True   # lowering sweeps the subtree like the others
        if changed_caps:
            swept = self._sweep_dirs(nid)
            if swept:
                warnings.append(f"subtree grants clamped to the new set (№30): {swept}")
        if permission_mode is not None:
            _t, _d, _v, pm2, b = self._apply_ceiling(
                pm=permission_mode, raise_ceiling=raise_ceiling, warnings=warnings)
            bridged = bridged or b
            sc["permission_mode"] = cast(str, pm2)  # pm in ⇒ pm out
        if effort is not None:
            if effort:
                sc["effort"] = effort
            else:
                sc.pop("effort", None)
        if model_version is not None:
            if model_version:
                sc["model_version"] = model_version
            else:
                sc.pop("model_version", None)   # "" clears ⇒ the tier default
        # §15 cascade: charter = this node's role card · team_charter = standing
        # instructions binding this node's whole subtree (manager-owned)
        if charter is not None:
            n["charter"] = charter.strip()[:4000] or None
        if team_charter is not None:
            n["team_charter"] = team_charter.strip()[:4000] or None
        if actor == USER:
            self._notify([nid], "The user changed your configuration (folders, tools, "
                                "charter, or org visibility). Your current scope is "
                                "stated in your system prompt each turn.")
        else:
            self._notify([nid], f'Your superior "{actor}" changed your configuration '
                                f'(folders, tools, charter, or org visibility). Your '
                                f'current scope is stated in your system prompt each turn.')
        self._log("set_scope", actor, {"node": nid, "scope": sc}, warnings)
        res: dict[str, Any] = {"scope": sc, "warnings": warnings}
        if bridged:
            res["bridge"] = {"raise_ceiling": True}
        return res

    def reorder(self, actor: str, nid: str, before: str | None = None,
                after: str | None = None) -> dict[str, Any]:
        """Cosmetic left-to-right position among siblings. No org effect — a UX
        affordance for the managing user (user-ruled); deliberately not logged as
        an authority-bearing operation beyond the ancestry check."""
        self._require_authority(actor, nid)
        n = self.node(nid)
        sibs = [k for k in self.children(n["parent"], live_only=False) if k != nid]
        if before and before in sibs:
            idx = sibs.index(before)
        elif after and after in sibs:
            idx = sibs.index(after) + 1
        else:
            raise LedgerError("reorder needs a sibling as before= or after=")
        # FULL sibling reindex (user bug report: "reordering sometimes doesn't
        # work") — the old midpoint halving converged to float ties after
        # repeated reorders, and tied ui_orders sort ambiguously. Fresh
        # integers every time keeps the order deterministic forever.
        for i, k in enumerate(sibs[:idx] + [nid] + sibs[idx:]):
            self.nodes[k]["ui_order"] = float(i)
        return {"ui_order": n["ui_order"], "warnings": []}

    # -------------------------------------------------------------- audiences
    def _sweep_audiences(self) -> list[tuple[str, str]]:
        """§7.3 auto-revoke: drop grants whose ANCHOR is no longer an ancestor
        of the grantee. For a self-grant the anchor is the grantor; for a
        delegated grant it is the delegator — a deliberately-lateral channel
        (e.g. to the delegator's peer) survives exactly as long as the
        authority that opened it still commands the grantee. User audiences
        are never swept (№11)."""
        kept: list[AudienceGrant]
        revoked: list[tuple[str, str]]
        kept, revoked = [], []
        for a in self.d["audiences"]:
            anchor = a.get("delegated_by") or a["grantor"]
            if a["grantor"] == EXTERN:
                # org-inbox grants: anchored on the delegator (user grants
                # are unanchored, like user audiences)
                if a["grantee"] in self.nodes and (
                        "delegated_by" not in a
                        or (anchor in self.nodes
                            and self.is_ancestor(anchor, a["grantee"]))):
                    kept.append(a)
                else:
                    revoked.append((a["grantee"], a["grantor"]))
            elif a["grantor"] == USER or (
                    a["grantee"] in self.nodes
                    and a["grantor"] in self.nodes
                    and (anchor == USER or (anchor in self.nodes
                         and self.is_ancestor(anchor, a["grantee"])))):
                kept.append(a)
            else:
                revoked.append((a["grantee"], a["grantor"]))
        self.d["audiences"] = kept
        return revoked

    # --------------------------------------------------- fable limit (user ruling)
    # ----------------------------------------------------- credit requests
    def request_credits(self, nid: str, new_limit: Any, reason: Any) -> dict[str, Any]:
        """A TOP-LEVEL agent asks the user directly for a larger grant. Not mail:
        a structured request (old → new + reason) the user approves or denies
        with one click. One pending request per node — but asking again AMENDS
        it (gap audit №34, user-approved): this was the only ask-verb that
        hard-errored on an idempotent ask, against the ratified pattern."""
        self._require_live(nid)
        n = self.node(nid)
        if n["parent"] is not None:
            raise LedgerError("only top-level agents may ask the user for credits "
                              "directly — ask your superior to reallocate instead")
        try:
            new_limit = int(new_limit)
        except (TypeError, ValueError):
            raise LedgerError("new_limit must be an integer (the requested TOTAL grant)")
        old = n["grant"]
        reqs = self.d.setdefault("credit_requests", [])
        pending = next((r for r in reqs
                        if r["node"] == nid and r["status"] == "pending"), None)
        if new_limit <= old:
            # motto A3: asking for what you already have is a no-op — and it
            # WITHDRAWS a pending request (the ask is "I need no more")
            if pending is not None:
                pending["status"] = "withdrawn"
                self._log("credit_request_withdrawn", nid,
                          {"id": pending["id"]}, [])
                return {"status": f"your grant is already {old} — the pending "
                                  f"request was withdrawn"}
            return {"status": f"your grant is already {old} — nothing to request"}
        if not (reason and str(reason).strip()):
            raise LedgerError("a reason is required")
        # ZERO headroom → refused OUTRIGHT, no ask made (user ruling
        # 2026-08-04): when there are genuinely no credits available to grant,
        # a pending card would be a lie — the user could only refuse it.
        room, why = self.credit_headroom(nid)
        if room is not None and room <= 0:
            self._log("credit_refused", nid, {"asked": new_limit}, [])
            return {"refused": True,
                    "status": f"refused outright — there are ZERO credits "
                              f"available to grant ({why}). No request was "
                              f"made. Free credits (retire a sibling, hand "
                              f"back unused grant) or ask the user to raise "
                              f"the cap."}
        if pending is not None:
            # amend in place: the card the user eventually clicks always
            # shows the CURRENT figure, never a stale one
            pending.update({"old": old, "new": new_limit,
                            "reason": str(reason).strip(), "at": now()})
            self._log("credit_request", nid,
                      {"old": old, "new": new_limit, "amended": pending["id"]}, [])
            return {"requested": new_limit, "increase": new_limit - old,
                    "status": "pending (amended your earlier request) — the "
                              "user will approve or deny"}
        req = {"id": f"cr{len(reqs) + 1}", "node": nid, "old": old,
               "new": new_limit, "reason": str(reason).strip(),
               "at": now(), "status": "pending"}
        reqs.append(req)
        self._log("credit_request", nid, {"old": old, "new": new_limit}, [])
        return {"requested": new_limit, "increase": new_limit - old,
                "status": "pending — the user will approve or deny"}

    def credit_headroom(self, nid: str) -> tuple[int | None, str]:
        """How many MORE credits a top-level node could be granted, and which
        cap binds. None = unbounded (no cap set). Two independent ceilings:
        max_top_grant (per-node) and the kiosk credit pool (org-wide)."""
        n = self.node(nid)
        rooms: list[tuple[int, str]] = []
        cap = int(self.d.get("max_top_grant") or 0)
        if cap:
            rooms.append((cap - int(n["grant"]),
                          f"your grant {n['grant']:g} is at the org's "
                          f"top-level cap of {cap}"))
        kc = (self.d.get("kiosk") or {}).get("credits")
        if kc is not None:
            holds = sum(self.seat_cost(k) + self.nodes[k]["grant"]
                        for k in self.children(None))
            rooms.append((int(kc) - int(holds),
                          f"the kiosk credit pool ({kc:g}) is fully held"))
        if not rooms:
            return None, ""
        room, why = min(rooms, key=lambda r: r[0])
        return room, why

    def credit_request_action(self, rid: str, action: str,
                              granted: int | None = None) -> dict[str, Any]:
        """Approve, counter-offer, or deny. `granted` (F-05, user-ruled): the
        user may set ANY legal amount — below the ask, above it, or below the
        node's current grant down to its committed floor (a clawback of unused
        credits; reallocate's own invariant is the floor). The outcome notice
        states what was asked, what was given, and that the agent may come
        back — the matter is the agent's to continue, not closed (ruling ③)."""
        req = next((r for r in self.d.get("credit_requests", [])
                    if r["id"] == rid), None)
        if req is None or req["status"] != "pending":
            raise LedgerError(f"no pending credit request {rid!r}")
        if action not in ("approve", "deny"):
            raise LedgerError("action must be approve|deny")
        nid = req["node"]
        old = req["old"]
        if action == "approve":
            if nid not in self.nodes or self.node(nid)["state"] != "live":
                # the card clears rather than raising: an approval that can't
                # apply must not leave the request pending forever (review —
                # approve was the one action that couldn't dismiss it)
                req["status"] = "moot"
                req["note"] = f"{nid} is no longer live — dropped as moot"
                self._log("credit_moot", USER, {"node": nid}, [])
                return req
            give = int(granted if granted is not None else req["new"])
            delta = give - self.node(nid)["grant"]
            warnings: list[str] = []
            if delta != 0:
                # reallocate enforces both ends: +Δ checks max_top_grant,
                # −Δ refuses past free (the committed floor) and names what
                # a reduction strands
                warnings = self.reallocate(USER, nid, delta).get("warnings", [])
            req["status"] = "answered"
            req["granted"] = give
            now_g = self.node(nid)["grant"]
            asked = f"you asked {old:g} → {req['new']:g}"
            if give == req["new"]:
                notice = (f"The user APPROVED your credit request — your "
                          f"grant is now {now_g:g}.")
            elif give > old:
                notice = (f"The user COUNTER-OFFERED: {asked}; granted "
                          f"{old:g} → {give:g} ({give - old:+g}). You may take "
                          f"this as-is, request more later, or find another "
                          f"way within it.")
            elif give == old:
                notice = (f"The user DECLINED the increase — {asked}; your "
                          f"grant stays {now_g:g}. You may re-ask with a "
                          f"stronger case, or work within it.")
            else:
                notice = (f"The user REDUCED your grant: {asked}; your grant "
                          f"is now {give:g} ({give - old:+g} — unused credits "
                          f"reclaimed). You may re-ask, or work within it.")
            req["notice"] = notice
            self._log("credit_answer", USER,
                      {"node": nid, "asked": req["new"], "granted": give},
                      warnings)
            return {**req, "warnings": warnings}
        req["status"] = "denied"
        if nid in self.nodes:
            req["notice"] = (f"The user DENIED your credit request "
                            f"({old:g} → {req['new']:g}). Your grant stays "
                            f"{old:g} — work within it, re-ask with a stronger "
                            f"case, or escalate differently.")
        self._log("credit_deny", USER, {"node": nid, "new": req["new"]}, [])
        return req

    def credit_preview(self, rid: str, granted: int) -> dict[str, Any]:
        """F-05 dry run: the warnings a `granted` amount WOULD raise, before
        the user commits — a reduction's stranding list is exactly what
        someone dragging the bar downward needs to see first."""
        req = next((r for r in self.d.get("credit_requests", [])
                    if r["id"] == rid), None)
        if req is None or req["status"] != "pending":
            raise LedgerError(f"no pending credit request {rid!r}")
        nid = req["node"]
        if nid not in self.nodes or self.node(nid)["state"] != "live":
            return {"ok": False, "warnings": [f"{nid} is no longer live"]}
        n = self.node(nid)
        give = int(granted)
        delta = give - n["grant"]
        warnings: list[str] = []
        if delta > 0 and n["parent"] is None:
            cap = int(self.d.get("max_top_grant") or 0)
            if cap and give > cap:
                return {"ok": False,
                        "warnings": [f"{give:g} is past the top-level grant "
                                     f"cap of {cap}"]}
        if delta < 0:
            if self.free(nid) < -delta:
                return {"ok": False,
                        "warnings": [f"{nid} has only {self.free(nid):g} "
                                     f"unused; the rest is committed"]}
            warnings = self._stranding_warnings(
                nid, self.free(nid), self.free(nid) + delta)
        return {"ok": True, "warnings": warnings}

    # ---------------------------------------------------- F-04: asking the user
    def ask_user(self, nid: str, question: str, options: list[Any] | None = None,
                 multi: bool = False) -> dict[str, Any]:
        """A structured question to the user (F-04, user-ruled 2026-08-04):
        ALWAYS parks — no blocking wait. The question becomes an interactive
        card on the agent's desk AND in the user's inbox; the answer arrives
        as ordinary user mail. Gate = the user-mail gate (top-level or a held
        user audience); anyone else has the question ROUTED to their superior
        as mail instead of refused (the auto-bridge motto). One open question
        per node — re-asking amends it (the ratified idempotent-ask pattern)."""
        self._require_live(nid)
        q = str(question or "").strip()
        if not q:
            raise LedgerError("a question is required")
        opts = [str(o).strip() for o in (options or []) if str(o).strip()][:4]
        n = self.node(nid)
        if n["parent"] is not None and not self._has_audience(nid, USER):
            sup = n["parent"]
            body = "[QUESTION — needs an answer]\n" + q
            if opts:
                body += "\nOptions: " + " · ".join(opts) \
                        + (" (several may apply)" if multi else "")
            r = self.post_mail(nid, sup, body, kind="question")
            return {"routed": sup, "deferred": bool(r.get("deferred")),
                    "status": f"you hold no user audience — the question was "
                              f"mailed to your superior \"{sup}\"; their "
                              f"answer arrives as mail"}
        asks = self.d.setdefault("asks", [])
        entry = next((a for a in asks
                      if a["node"] == nid and a["status"] == "open"), None)
        if entry is not None:
            entry.update({"question": q, "at": now(),
                          **({"options": opts} if opts else {}),
                          **({"multi": True} if multi else {})})
            if not opts:
                entry.pop("options", None)
            if not multi:
                entry.pop("multi", None)
            self._log("ask", nid, {"id": entry["id"], "amended": True}, [])
            return {"asked": entry["id"],
                    "status": "parked (amended your earlier question) — the "
                              "answer will arrive as mail; do NOT wait for it "
                              "in this turn"}
        aid = "q" + uuid.uuid4().hex[:8]
        asks.append({"id": aid, "node": nid, "kind": "question", "question": q,
                     **({"options": opts} if opts else {}),
                     **({"multi": True} if multi else {}),
                     "at": now(), "status": "open"})
        self._prune_asks()
        self._log("ask", nid, {"id": aid}, [])
        return {"asked": aid,
                "status": "parked — the question is on the user's screen; the "
                          "answer will arrive as mail. Do NOT wait for it in "
                          "this turn: wrap up and end the turn. ⚠ If any other "
                          "mail wakes you first, the question is VOIDED and "
                          "must be re-asked."}

    def ask_answer(self, aid: str, selected: list[Any] | None = None,
                   text: str | None = None) -> dict[str, Any]:
        """Mark a question answered and return the composed answer body — the
        caller delivers it as ordinary user mail (which is what drives the
        turn). Marking happens FIRST, under the same doc lock, so the turn the
        answer starts can never void its own question."""
        a = next((x for x in self.d.get("asks", []) if x["id"] == aid), None)
        if a is None:
            raise LedgerError(f"no ask {aid!r}")
        if a["status"] != "open":
            raise LedgerError(
                f"ask {aid} is already {a['status']}"
                + (f" ({a.get('reason')})" if a.get("reason") else ""))
        sel = [str(s).strip() for s in (selected or []) if str(s).strip()]
        txt = str(text or "").strip()
        if not sel and not txt:
            raise LedgerError("an answer needs selected options or text")
        a["status"] = "answered"
        a["reason"] = "answered"
        a["answer"] = {**({"selected": sel} if sel else {}),
                       **({"text": txt} if txt else {})}
        a["resolved_at"] = now()
        body = "[ANSWER to your question]\nQ: " + a["question"]
        if sel:
            body += "\nSelected: " + " · ".join(sel)
        if txt:
            body += ("\nAnswer: " if not sel else "\nAlso: ") + txt
        self._log("ask_answered", USER, {"id": aid, "node": a["node"]}, [])
        return {"node": a["node"], "body": body}

    def void_open_asks(self, nid: str) -> list[str]:
        """Wake-voids (user ruling 2026-08-04): a turn that starts while an
        ask is open means the agent's context moved on before the answer —
        the ask is nulled EVERYWHERE and the agent is told to re-ask. Applies
        to questions and pending credit requests alike."""
        gone: list[str] = []
        for a in self.d.get("asks", []):
            if a["node"] == nid and a["status"] == "open":
                a["status"] = "interrupted"
                a["reason"] = "the agent was woken by other input before an answer arrived"
                a["resolved_at"] = now()
                gone.append(f"your question ({a['question'][:100]!r}) was "
                            f"VOIDED — re-ask it if still needed")
        for r in self.d.get("credit_requests", []):
            if r["node"] == nid and r["status"] == "pending":
                r["status"] = "interrupted"
                r["reason"] = "the agent was woken by other input before an answer arrived"
                r["resolved_at"] = now()
                gone.append(f"your credit request ({r['old']:g} → {r['new']:g}) "
                            f"was VOIDED — re-ask it if still needed")
        if gone:
            self._log("asks_voided", nid, {"n": len(gone)}, [])
        return gone

    def _prune_asks(self) -> None:
        """Open asks are never pruned; resolved ones keep a short history."""
        asks = self.d.get("asks", [])
        resolved = [a for a in asks if a["status"] != "open"]
        for a in resolved[:-30]:
            asks.remove(a)

    def node_ask(self, nid: str) -> dict[str, Any] | None:
        """The ask the UI should show on this node's desk: the open one, or
        the most recently resolved one within its linger window (the nulled
        card carries WHY it nulled — grey answered / orange interrupted)."""
        pool = ([a for a in self.d.get("asks", []) if a["node"] == nid]
                + [{**r, "kind": "credit"} for r in self.d.get("credit_requests", [])
                   if r["node"] == nid and r["status"] not in ("withdrawn", "moot")])
        if not pool:
            return None

        def stamp(a: dict[str, Any]) -> str:
            return str(a.get("resolved_at") or a["at"])
        opens = [a for a in pool if a.get("status") in ("open", "pending")]
        best = max(opens, key=stamp) if opens else max(pool, key=stamp)
        if best.get("status") not in ("open", "pending"):
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
            if (best.get("resolved_at") or best["at"]) < cutoff:
                return None
        return best

    def fable_filter_hit(self, nid: str, detail: str) -> str:
        """A Fable content filter flagged this node's message mid-turn (user
        spec). Per-node, per-incident — nothing org-wide locks. The org's
        `fable_filter_policy` decides:
          halt (default) — the turn stays failed; the node holds its seat;
              superior + the user are told and decide.
          opus — the node converts fable→opus (seat 10→5, one-way, same
              conversion as the limit policy) and the flagged turn retries.
        Returns the policy actually applied."""
        policy = self.d.get("fable_filter_policy", "halt")
        n = self.node(nid)
        if policy == "opus" and n["model"] == "fable":
            n["model"] = "opus"
            self._notify([n["parent"]],
                         f'Your report "{nid}" switched fable→opus: a Fable content '
                         f'filter flagged its message (org policy). Seat cost dropped '
                         f'10→5; the flagged turn retries on opus.')
            self._notify(self._peers_of(n["parent"], nid),
                         f'Your peer "{nid}" switched fable→opus (content filter, '
                         f'org policy).')
        else:
            policy = "halt"
            self._notify([n["parent"]],
                         f'Your report "{nid}" had a message FLAGGED by Fable\'s '
                         f'content filters — its turn HALTED (org policy). Re-task '
                         f'it, or the user may switch the org filter policy to '
                         f'auto-convert to opus.')
        self.d.setdefault("user_inbox", []).append({
            "id": uuid.uuid4().hex[:8], "from": SYSTEM, "kind": "decision",
            "at": now(),
            "body": (f'A Fable content filter flagged a message from "{nid}" '
                     f'(org policy applied: {policy}'
                     f'{" — retried on opus" if policy == "opus" else ""}). '
                     f'Detail: {detail[:200]}')})
        self._log("fable_filter", SYSTEM, {"node": nid, "policy": policy}, [])
        return policy

    def fable_limit_hit(self, detecting_node: str | None, detail: str) -> dict[str, Any]:
        """Weekly Fable usage limit exhausted. What happens to live fable agents is
        the org's `fable_limit_policy`:
          halt (default) — nobody retires or converts; fable agents simply halt,
              visibly, and their superiors/coworkers decide what to do.
          opus — every fable agent switches to an opus seat and keeps working
              (seat 10→5; the freed credits return to each parent's pool).
          dissolve — every fable node's ENTIRE subtree is retired (recursive,
              deepest first), freeing all its credits to its parent.
        In every case the exhaustion is recorded (fable_lock) and explained to the
        user. Rehiring/hiring fable is NOT hard-blocked for agents — it is merely
        futile while the limit lasts, and their prompts say so."""
        if self.d.get("fable_lock"):
            return {"already_locked": True}
        policy = self.d.get("fable_limit_policy", "halt")
        self.d["fable_lock"] = {"at": now(), "detail": detail[:300],
                                "detected_by": detecting_node, "policy": policy}
        locked: list[str]
        converted: list[str]
        dissolved: list[str]
        locked, converted, dissolved = [], [], []
        for k in [k for k, v in self.nodes.items()
                  if v["state"] == "live" and v["model"] == "fable"]:
            n = self.nodes[k]
            if n["state"] != "live":
                continue   # already taken by an outer fable's dissolve
            if policy == "opus":
                n["model"] = "opus"
                converted.append(k)
                self._notify([n["parent"]],
                             f'Your report "{k}" switched fable→opus: weekly Fable '
                             f'usage limit exhausted (org policy). Its seat cost '
                             f'dropped 10→5; it keeps working.')
                self._notify([k], "Weekly Fable usage limit exhausted: per org policy "
                                  "you now run as OPUS. Carry on.")
            elif policy == "dissolve":
                parent, peers = n["parent"], self._peers_of(n["parent"], k)
                taken = self.dissolve(SYSTEM, k)
                dissolved.append(k)
                self._notify([parent],
                             f'Your report "{k}" and its entire suborganization '
                             f'({len(taken["nodes"])} node(s)) were dissolved: weekly '
                             f'Fable usage limit exhausted (org policy). '
                             f'{taken["freed"]} credits returned to you.')
                self._notify(peers,
                             f'Your peer "{k}" and its suborganization were dissolved '
                             f'(weekly Fable limit, org policy).')
            else:   # halt — the default
                n["limit_locked"] = True
                locked.append(k)
                self._notify([n["parent"]],
                             f'Your report "{k}" has HALTED: weekly Fable usage limit '
                             f'exhausted. It holds its seat and will not run until the '
                             f'limit resets or the user intervenes — decide how to '
                             f'cover its work.')
                self._notify(self._peers_of(n["parent"], k),
                             f'Your peer "{k}" has halted (weekly Fable limit).')
                self._notify([k], "Weekly Fable usage limit exhausted: you are halted. "
                                  "Your reports remain active.")
        self.d.setdefault("user_inbox", []).append({
            "from": SYSTEM, "kind": "decision", "at": now(),
            "body": (f"Weekly Fable usage limit exhausted (detected at "
                     f"{detecting_node or 'unknown'}; policy: {policy}). "
                     f"Halted: {locked or 'none'}. Dissolved (whole subtrees): "
                     f"{dissolved or 'none'}. Switched to opus: {converted or 'none'}"
                     + (" — they stay opus until you change them." if converted else ".")
                     + " Rehiring a fable yourself, or clearing the lock in settings, "
                       "lifts the freeze.")})
        self._log("fable_limit", SYSTEM,
                  {"policy": policy, "locked": locked, "dissolved": dissolved,
                   "converted": converted}, [])
        return {"policy": policy, "locked": locked, "dissolved": dissolved,
                "converted": converted}

    def clear_fable_lock(self) -> None:
        self.d.pop("fable_lock", None)
        for v in self.nodes.values():
            v.pop("limit_locked", None)
        self._log("fable_unlock", USER, {}, [])

    # ------------------------------------------------------- lineage (§8)
    def compact_split(self, nid: str, new_session_id: str) -> str:
        """§8: compaction splits a node. The successor keeps the name, parent and
        org position with the compacted (forked) session; the pre-compaction session
        is retired IN PLACE as an archived knowledge bearer at 0 credits, locked
        read-only. Lineage is a second axis — the predecessor is NOT a child."""
        n = self.node(nid)
        gen = n.get("generation", 0)
        pred_id = f"{nid}@{gen}"
        pred = cast(NodeDoc, dict(n))  # dict() copy loses the TypedDict
        pred.update({
            "state": "archived", "archived_at": now(), "grant": 0,
            "bearer_state": "knowledge", "successor": nid, "predecessor": n.get("predecessor"),
            "ui_order": n.get("ui_order", 0) + 0.001,
            # audit finding: dict(n) copied the ACCOUNTING and runtime fields —
            # a duplicated cost_usd inflated the org total superlinearly with
            # each compaction generation (kiosk spend caps froze on the false
            # figure). The bearer starts clean; the successor keeps the real
            # numbers.
            "cost_usd": 0.0, "last_status": None, "frozen": None,
            "inflight": None,
            "scope": {**n["scope"],
                      # deep-copy the dir grants: {**scope} still ALIASES the
                      # live successor's add_dirs list — the first in-place
                      # mutation anyone writes would silently edit every
                      # archived predecessor's grants too (review finding)
                      "add_dirs": cast("list[DirGrant]",
                                       [dict(d) for d in n["scope"].get("add_dirs", [])]),
                      "tools": {"bash": False, "web": False, "edit": False,
                                "subagents": False, "mcp": []}},
        })
        self.nodes[pred_id] = pred
        n["session_id"] = new_session_id
        n["generation"] = gen + 1
        n["predecessor"] = pred_id
        self._notify([n["parent"]],
                     f'"{nid}" compacted (now generation {gen + 1}). Its pre-compaction '
                     f'self is archived as "{pred_id}" — rehire it to consult the full '
                     f'detail the summary flattened.')
        self._log("compact_split", SYSTEM, {"node": nid, "predecessor": pred_id}, [])
        return pred_id

    def mark_unrecoverable(self, nid: str, reason: str) -> None:
        """№31: ledger said live, the session cannot actually resume."""
        n = self.node(nid)
        n["state"] = "unrecoverable"
        self._notify([n["parent"]],
                     f'⚠ Your report "{nid}" is UNRECOVERABLE — its session failed to '
                     f'resume ({reason}). Its seat is still held; rehire it to RE-SEED '
                     f'it (fresh session, same identity and credits), or retire it '
                     f'to free the credits.')
        self._log("unrecoverable", SYSTEM, {"node": nid, "reason": reason}, [])

    def reseed(self, actor: str, nid: str, new_session_id: str) -> dict[str, Any]:
        """The №31 exit (gap audit №9): an unrecoverable node's SESSION is gone,
        but the node — name, position, charter, credits, reports, mailbox — is
        fine. Re-seed mints a fresh session and archives the dead one into the
        lineage stack as a LOST generation (bearer_state="lost": kept for the
        record, never consultable — its transcript is missing). Budget-neutral:
        same node, same seat, no new charge."""
        self._require_authority(actor, nid, allow_self=True)
        n = self.node(nid)
        if n["state"] == "archived":
            raise LedgerError(f"{nid} is archived — rehire it instead")
        if n["state"] != "unrecoverable":
            return {"warnings": [f"{nid} is {n['state']} and its session works — "
                                 f"nothing to re-seed"]}
        if n.get("successor"):
            # review C14: a knowledge bearer whose transcript is gone IS the
            # lost generation — minting a fresh session would leave a node
            # badged "knowledge" over empty memory. It archives in place,
            # marked lost, and the successor (the one agent whose whole
            # reason to consult it is the context that just vanished) is
            # told directly.
            succ = n["successor"]
            was_live = n["state"] in ("live", "unrecoverable") \
                and not n.get("archived_at")
            n["state"] = "archived"
            n["archived_at"] = now()
            n["grant"] = 0
            n["bearer_state"] = "lost"
            n["frozen"] = None
            n["inflight"] = None
            self._notify([t for t in {succ, n["parent"]} if t and t != actor],
                         f'Knowledge bearer "{nid}" lost its transcript and is '
                         f'now a LOST generation — it can no longer be '
                         f'consulted; what it held survives only in what was '
                         f'already written down.')
            self._log("reseed", actor, {"node": nid, "lost_bearer": True}, [])
            return {"warnings": [
                f'{nid} was a knowledge bearer with no surviving transcript — '
                f'marked a LOST generation (archived, never consultable); no '
                f'fresh session was minted'
                + ("; its seat freed" if was_live else "")]}
        gen = n.get("generation", 0)
        pred_id = f"{nid}@{gen}"
        pred = cast(NodeDoc, dict(n))  # dict() copy loses the TypedDict
        pred.update({
            "state": "archived", "archived_at": now(), "grant": 0,
            "bearer_state": "lost", "successor": nid,
            "predecessor": n.get("predecessor"),
            "ui_order": n.get("ui_order", 0) + 0.001,
            "cost_usd": 0.0, "last_status": None, "frozen": None,
            "inflight": None,
            "scope": {**n["scope"],
                      "add_dirs": cast("list[DirGrant]",
                                       [dict(d) for d in n["scope"].get("add_dirs", [])]),
                      "tools": {"bash": False, "web": False, "edit": False,
                                "subagents": False, "mcp": []}},
        })
        self.nodes[pred_id] = pred
        n["session_id"] = new_session_id
        n["generation"] = gen + 1
        n["predecessor"] = pred_id
        n["state"] = "live"
        who = "the user" if actor == USER else f'"{actor}"'
        self._notify([p for p in [n["parent"]] if p and p != actor],
                     f'Your report "{nid}" was RE-SEEDED by {who}: its dead session '
                     f'is archived as "{pred_id}" (a lost generation) and it starts '
                     f'fresh — same role, credits and reports, empty memory.')
        self._notify([nid],
                     f"{who.capitalize()} re-seeded you after your previous session "
                     f"was lost. Your role, charter, credits and reports are intact, "
                     f"but your memory starts fresh — check your scratch CLAUDE.md "
                     f"and ask your chain to re-orient you.")
        self._log("reseed", actor, {"node": nid, "predecessor": pred_id}, [])
        return {"predecessor": pred_id,
                "warnings": [f'{nid} re-seeded — the dead session is archived as '
                             f'"{pred_id}" (lost generation, not consultable)']}

    # ------------------------------------------------------------------ audit
    def audit(self) -> dict[str, Any]:
        """Global consistency: no overdraft anywhere; per-node free is derivable."""
        live = [k for k, v in self.nodes.items() if v["state"] == "live"]
        problems = [f"{k} free={self.free(k):g}" for k in live if self.free(k) < 0]
        return {
            "live_nodes": len(live),
            "top_level_holds": sum(self.seat_cost(k) + self.nodes[k]["grant"]
                                   for k in self.children(None)),
            "no_overdraft": not problems,
            "problems": problems,
        }

    # ------------------------------------------------------------------- view
    def tree(self) -> dict[str, Any]:
        """Derived view for the API/UI: nested nodes with computed fields."""
        def build(nid: str) -> dict[str, Any]:
            n = self.nodes[nid]
            return {
                "id": nid,
                "title": n["title"],
                "tier": n["model"],
                "model_id": self.d["models"].get(n["model"], n["model"]),
                "state": n["state"],
                "seat": self.d["tiers"][n["model"]],
                "grant": n["grant"],
                "free": None if n["state"] != "live" else self.free(nid),
                "session_id": n["session_id"],
                "scope": n["scope"],
                # what a turn would ACTUALLY launch with — scope.effort is
                # only half the answer (the org default supplies the rest)
                "effort_effective": self.effective_effort(nid),
                "ui_order": n.get("ui_order", 0),
                "cost_usd": round(float(n.get("cost_usd") or 0.0), 4),
                "occupancy": n.get("occupancy"),
                "context_window": n.get("context_window"),
                "charter": n.get("charter"),
                "team_charter": n.get("team_charter"),
                "mail_pending": len((self.d.get("mail") or {}).get(nid, [])),
                "limit_locked": bool(n.get("limit_locked")),
                "last_status": n.get("last_status"),
                "prev_status": n.get("prev_status"),
                "inflight_at": (n.get("inflight") or {}).get("at"),
                "last_denials": n.get("last_denials") or [],
                "turns": (n.get("turns") or [])[-8:],
                # the `if n.get("frozen")` guard proves the key present — the
                # Any view sidesteps pyright's NotRequired-[] access flag
                "frozen": ({**{k: cast(Any, n)["frozen"].get(k)
                               for k in ("at", "until", "until_ts")},
                            # №41: freeze kinds are commutative — surface
                            # whichever reason(s) exist without overwriting
                            "error": " · ".join(
                                x for x in (cast(Any, n)["frozen"].get("error"),
                                            cast(Any, n)["frozen"].get("spend_error"))
                                if x) or None}
                           if n.get("frozen") else None),
                "audiences_held": [a["grantor"] for a in self.d["audiences"]
                                   if a["grantee"] == nid],
                # F-04/F-05: the ask card this node's desk shows — open, or
                # freshly nulled (the nulled card carries its reason)
                "ask": self.node_ask(nid),
                "bearer_state": n["bearer_state"],
                "generation": n["generation"],
                "children": [build(c) for c in self.org_children(nid)],
                "lineage": [{
                    "id": k,
                    "generation": self.nodes[k].get("generation", 0),
                    "state": self.nodes[k]["state"],
                    "bearer_state": self.nodes[k].get("bearer_state"),
                    "tier": self.nodes[k]["model"],
                } for k in self.lineage_stack(nid)],
            }
        return {
            "slug": self.d["slug"],
            "name": self.d["name"],
            "workspace": self.d.get("workspace"),
            "dirs": self.d["dirs"],
            "max_top_grant": self.d.get("max_top_grant", 1000),
            "default_top_grant": self.d.get("default_top_grant", 50),
            "compact_at": self.d.get("compact_at", 0.80),
            "default_tools": self.d.get("default_tools"),
            "default_visibility": self.d.get("default_visibility", "full"),
            # "" = CLI default (user ruling 2026-08-01: visible inherit — an
            # unset node effort falls back to this at TURN time, live)
            "default_effort": self.d.get("default_effort", ""),
            # what "" resolves to, so no UI string has to hardcode it
            "effort_default": self.DEFAULT_EFFORT,
            "credit_requests": [r for r in self.d.get("credit_requests", [])
                                if r["status"] == "pending"],
            # F-04: everything the user's inbox interleaves as ask cards —
            # open first-class, resolved for the nulled history; the header
            # ask-icon glows iff asks_open > 0
            "asks": (self.d.get("asks", [])
                     + [{**r, "kind": "credit"}
                        for r in self.d.get("credit_requests", [])
                        if r["status"] not in ("withdrawn", "moot")])[-60:],
            "asks_open": sum(1 for a in self.d.get("asks", [])
                             if a["status"] == "open")
                         + sum(1 for r in self.d.get("credit_requests", [])
                               if r["status"] == "pending"),
            "tiers": self.d["tiers"],
            "audiences": self.d["audiences"],
            "roots": [build(c) for c in self.org_children(None)],
            "audit": self.audit(),
            "cost_usd_total": self.cost_total(),
            "user_inbox_count": len(self.d.get("user_inbox", [])),
            "user_inbox_newest": (self.d.get("user_inbox") or [{}])[-1].get("at"),
            "fable_lock": self.d.get("fable_lock"),
            "spend_frozen": bool(self.d.get("spend_frozen")),
            "storage_blocked": bool(self.d.get("storage_blocked")),
            "auto_resume": bool(self.d.get("auto_resume")),
            "fable_limit_policy": self.d.get("fable_limit_policy", "halt"),
            "fable_filter_policy": self.d.get("fable_filter_policy", "halt"),
            "cascade_hire": bool(self.d.get("cascade_hire", True)),
            "cascade_alloc": bool(self.d.get("cascade_alloc", True)),
            "sandboxed": bool((self.d.get("kiosk") or {}).get("sandbox")
                             or (self.d.get("sandbox") or {}).get("enabled")),
            "audience_requests": self.d.get("audience_requests", []),
            # the org inbox panel (user spec): hidden until the org receives
            # its first outside mail OR an inbox audience is granted
            "org_inbox": {
                "entries": self.d.get("org_inbox", [])[-50:],
                "unread": max(0, len(self.d.get("org_inbox", []))
                              - int(self.d.get("org_inbox_read", 0))),
                "holders": self.extern_holders(),
                "visible": not self.is_kiosk and bool(
                    self.d.get("org_inbox")
                    or any(a["grantor"] == EXTERN
                           for a in self.d["audiences"])),
            },
        }
