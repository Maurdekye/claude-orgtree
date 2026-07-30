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
other archived children), and reallocate(-Δ).

Directory access (№30) is an inherited capability set, NOT a budget: a node may hold only
dirs its parent holds (top-level nodes are user-granted and unconstrained). Nothing conserves;
revoke is explicit; re-parenting intersects the moved subtree's dirs with the new chain.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone

# §3.1 — derived from published API pricing (output:input is 5:1 for every model, so the
# scale is not a judgment call). Sonnet is 3, not its introductory 2 (expires 2026-08-31).
TIERS = {"fable": 10, "opus": 5, "sonnet": 3, "haiku": 1}

# §5 — full model ids only; aliases drift (spike: 'sonnet' resolved to sonnet-4-5).
MODELS = {
    "fable": "claude-fable-5",
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}

# Actors are one of three KINDS — user, system, agent — not one string namespace.
# The non-agent kinds use @-prefixed sentinels, which slugify() can never produce,
# so agent NAMES are fully unrestricted (a node may be called "user" or "system").
USER = "@user"      # the org root: infinite free, unconditional authority (§7.4)
SYSTEM = "@system"  # the ledger's own hand (fable-limit policy, reconciliation)


def actor_kind(actor: str) -> str:
    if actor == USER:
        return "user"
    if actor == SYSTEM:
        return "system"
    return "agent"

VIS_LEVELS = ("self", "team", "subtree", "full")   # org-structure knowledge tiers
TOOL_KEYS = ("bash", "web", "edit", "subagents")   # the built-in tool switches


def norm_tools(t) -> dict:
    """Normalize a tool grant: four built-in switches + an MCP server name list.
    "*" in mcp = every registered server, present AND future (collapses the list)."""
    t = t or {}
    out = {k: bool(t.get(k, True)) for k in TOOL_KEYS}
    out["mcp"] = sorted({str(s) for s in t.get("mcp", []) if s})
    if "*" in out["mcp"]:
        out["mcp"] = ["*"]
    return out


def norm_dirs(dirs) -> list[dict]:
    """Normalize dir grants to [{path, mode}] — strings default to read/write."""
    out, seen = [], set()
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
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    def __init__(self, doc: dict):
        self.d = doc
        # migrate older docs in place: dir grants gain modes; scopes gain tool sets
        for i, n in enumerate(self.d.get("nodes", {}).values()):
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
            n.setdefault("purpose", None)
            # pre-unification relic: queued texts now persist as mailbox mail
            n.pop("queued_msgs", None)
        if self.d.get("fable_limit_policy") in (None, "retire"):
            self.d["fable_limit_policy"] = "halt"   # 'retire' dropped by user ruling
        # org-wide agent defaults for hires that don't state them (user hires):
        # every capability enabled — all switches + all MCP servers + full org
        # visibility + the org's folders (user ruling)
        self.d["default_tools"] = norm_tools(
            self.d.get("default_tools", {"mcp": ["*"]}))
        if self.d.get("default_visibility") not in VIS_LEVELS:
            self.d["default_visibility"] = "full"
        self.d.pop("default_dirs", None)   # superseded: org dirs carry modes now
        self.d.setdefault("default_top_grant", 50)   # user ruling: 50 by default
        self.d.setdefault("credit_requests", [])     # top-level asks to the user
        self.d.setdefault("compact_at", 0.80)        # compaction ratio, ≤ 0.95 hard
        for m in self.d.get("user_inbox", []):       # per-mail read tracking needs ids
            m.setdefault("id", uuid.uuid4().hex[:8])
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
            "nodes": {},
            "audiences": [],          # §7.3 — [{grantee, grantor, granted_at, reason}]
            "chain_notices": [],      # §7.4
            "audience_requests": [],  # §7.3
            "events": [],             # audit log of ops
        })

    # ---------------------------------------------------------------- queries
    @property
    def nodes(self) -> dict:
        return self.d["nodes"]

    def node(self, nid: str) -> dict:
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
        """Ancestor chain from immediate parent up to USER (inclusive)."""
        out = []
        cur = self.node(nid)["parent"]
        while cur is not None:
            out.append(cur)
            cur = self.nodes[cur]["parent"]
        out.append(USER)
        return out

    def is_ancestor(self, a: str, nid: str) -> bool:
        """True if `a` is a strict ancestor of node `nid` (USER is ancestor of all)."""
        return a == USER or a in self.ancestors(nid)

    def org_children(self, nid: str | None) -> list[str]:
        """Children on the ORG axis only — lineage predecessors (nodes with a
        `successor`) share the parent slot but are NOT organizational children (§8.5)."""
        return [k for k in self.children(nid, live_only=False)
                if not self.nodes[k].get("successor")]

    def lineage_stack(self, nid: str) -> list[str]:
        """Predecessor chain of nid, newest first."""
        out, cur = [], self.node(nid).get("predecessor")
        while cur and cur in self.nodes:
            out.append(cur)
            cur = self.nodes[cur].get("predecessor")
        return out

    def descendants(self, nid: str, live_only: bool = True) -> list[str]:
        out = []
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
    def _clamp_tools(requested, parent_tools: dict | None,
                     strict: bool) -> tuple[dict, list[str]]:
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
    def _clamp_dirs(requested: list[dict], parent_map: dict[str, str] | None,
                    strict: bool) -> tuple[list[dict], list[str]]:
        """Intersect a dir list with a parent capability map, downgrading rw→ro where
        the parent only holds ro. strict=True raises instead of dropping (hire-time)."""
        if parent_map is None:
            return list(requested), []
        kept, lost = [], []
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
                kept.append(dict(d))
        return kept, lost

    # ------------------------------------------------------------- validation
    def _require_authority(self, actor: str, nid: str, allow_self: bool = False):
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

    def _require_live(self, nid: str):
        if self.node(nid)["state"] != "live":
            raise LedgerError(f"{nid} is {self.node(nid)['state']}, not live")

    # -------------------------------------------------------------- stranding
    def _stranding_warnings(self, payer: str, free_before: float,
                            free_after: float) -> list[str]:
        """§4.4 (corrected): name each archived dependent of `payer` whose rehire cost
        was affordable at free_before but is not at free_after."""
        if payer == USER or free_after >= free_before:
            return []
        warns = []
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

    def post_mail(self, sender: str, to: str, body: str, kind: str = "message") -> dict:
        """Agent-to-agent (or agent-to-user) mail under the §7.2 addressing rules:
        downward any depth (deep reach implicitly grants the recipient an audience),
        one hop up, siblings, held audiences. Everything else is refused with the
        proper route named."""
        to = self._resolve_recipient(to)
        if actor_kind(sender) == "agent":
            self.node(sender)
        warnings: list[str] = []
        if to == USER:
            if sender == USER:
                raise LedgerError("the user cannot mail the user")
            if self.node(sender)["parent"] is not None and not self._has_audience(sender, USER):
                raise LedgerError(
                    "only top-level agents (or holders of a user audience) may write "
                    "to the user — escalate to your superior instead (§7.5)")
            self.d.setdefault("user_inbox", []).append(
                {"id": uuid.uuid4().hex[:8], "from": sender, "kind": kind,
                 "body": body, "at": now()})
            self._log("mail", sender, {"to": USER, "kind": kind}, [])
            return {"delivered": "user_inbox", "warnings": warnings}

        target = self.node(to)
        if target["state"] != "live":
            raise LedgerError(f"{to} is {target['state']} — it cannot receive mail")
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
        entry = {
            "from": sender, "kind": kind, "body": body, "at": now(),
            "relationship": self.relationship(sender, to),
        }
        box.setdefault(to, []).append(entry)
        # full-body archive for the node's inbox view (the event log keeps only
        # a gist) — capped per node
        log = self.d.setdefault("mail_log", {}).setdefault(to, [])
        log.append(dict(entry))
        del log[:-100]
        if sender == USER:
            # the user's Sent folder: every user message IS mail (user ruling —
            # the direct-message channel was folded into the mail system)
            out = self.d.setdefault("user_outbox", [])
            out.append({**entry, "to": to})
            del out[:-100]
        self._log("mail", sender, {"to": to, "kind": kind,
                                   "gist": body.strip().splitlines()[0][:80]}, warnings)
        return {"delivered": to, "warnings": warnings}

    def _has_audience(self, grantee: str, grantor: str) -> bool:
        return any(a["grantee"] == grantee and a["grantor"] == grantor
                   for a in self.d["audiences"])

    # -------------------------------------------------- audience requests (§7.3)
    def request_audience(self, actor: str, target: str, reason: str) -> dict:
        """The slow upward path: a request climbs the actor's chain ONE refusable hop
        at a time. Grants flow down fast; requests climb slowly — by design."""
        self.node(actor)
        target = self._resolve_recipient(target)
        if target != USER and not self.is_ancestor(target, actor):
            raise LedgerError("audience requests climb your own chain — the target "
                              "must be one of your superiors (or 'user')")
        par = self.parent(actor)
        if target == par:
            raise LedgerError("that is your direct superior — just message them")
        if any(r["from"] == actor and r["target"] == target
               for r in self.d["audience_requests"]):
            raise LedgerError("you already have an open request to that target")
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

    def _find_request(self, frm: str, target: str) -> dict:
        req = next((r for r in self.d["audience_requests"]
                    if r["from"] == frm and r["target"] == target), None)
        if not req:
            raise LedgerError(f"no open audience request {frm} → {target}")
        return req

    def audience_forward(self, actor: str, frm: str, target: str) -> dict:
        req = self._find_request(frm, target)
        if actor != req["currently_at"] and actor != USER:
            raise LedgerError(f"the request currently awaits {req['currently_at']}")
        nxt = USER if actor == USER else self.parent(actor)
        req["currently_at"] = nxt
        drive = []
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

    def audience_grant(self, actor: str, frm: str) -> dict:
        """Grant frm a direct channel to actor. Also resolves any open request."""
        if actor != USER and not self.is_ancestor(actor, frm):
            raise LedgerError("only a superior grants an audience with itself")
        if not self._has_audience(frm, actor):
            self.d["audiences"].append({"grantee": frm, "grantor": actor,
                                        "granted_at": now(), "reason": "granted on request"})
        self.d["audience_requests"] = [
            r for r in self.d["audience_requests"]
            if not (r["from"] == frm and r["target"] == actor)]
        drive = []
        if actor == USER:
            self._notify([frm], "The user granted you a USER AUDIENCE — you may write "
                                "to them directly until it is rescinded.")
        else:
            self.post_mail(actor, frm,
                           f"Audience granted: you may message {actor} directly until "
                           f"it is rescinded.", kind="decision")
            drive.append(frm)
        self._log("audience_grant", actor, {"grantee": frm}, [])
        return {"drive": drive, "warnings": []}

    def audience_deny(self, actor: str, frm: str, target: str) -> dict:
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

    def audience_revoke(self, actor: str, grantee: str) -> dict:
        """Rescinding — unilateral and instant (§7.3). Actor must be the grantor
        (or the user, whose authority is unconditional)."""
        before = len(self.d["audiences"])
        self.d["audiences"] = [
            a for a in self.d["audiences"]
            if not (a["grantee"] == grantee and (a["grantor"] == actor or actor == USER))]
        if len(self.d["audiences"]) == before:
            raise LedgerError(f"no audience held by {grantee} that {actor} may revoke")
        self._notify([grantee],
                     f"Your audience with {actor if actor != USER else 'the user'} was "
                     f"rescinded — fall back to the parent chain.")
        self._log("audience_revoke", actor, {"grantee": grantee}, [])
        return {"warnings": []}

    def take_mail(self, nid: str) -> list[dict]:
        return (self.d.get("mail") or {}).pop(nid, [])

    def user_deep_reach(self, nid: str, gist: str):
        """§7.4: the user spoke to a non-top-level node — notify every superior up
        the chain (without interruption) and grant the node a user audience."""
        chain = [a for a in self.ancestors(nid) if a != USER]
        if not chain:
            return   # top-level: the only superior is the user themself (№12)
        self._notify(chain, f'The user spoke directly to "{nid}": "{gist}"')
        if not self._has_audience(nid, USER):
            self.d["audiences"].append({
                "grantee": nid, "grantor": USER, "granted_at": now(),
                "reason": "user messaged directly"})

    # --------------------------------------------------------------- notices
    def _notify(self, nids, text: str):
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
    def _log(self, op: str, actor: str, detail: dict, warnings: list[str]):
        self.d["events"].append({
            "op": op, "actor": actor, "at": now(), "detail": detail,
            "warnings": warnings,
        })

    # ------------------------------------------------------------------ hire
    def hire(self, actor: str, parent: str | None, tier: str, grant: int, name: str,
             add_dirs: list[str] | None = None, tools: dict | None = None,
             org_visibility: str | None = None, purpose: str | None = None) -> dict:
        """§4.2 + §4.6. `parent` None = top level (actor must be USER). If actor is a
        strict ancestor of parent, credits cascade down the path (forcible hire).

        ⚠️ No defaults for agent actors (user ruling): the USER hires from sensible
        defaults, but an agent must state every permission — dirs, every tool switch,
        the MCP list, org visibility — and the hire's purpose, explicitly. An agent
        must know exactly what it is hiring for and what that hire requires."""
        if tier not in self.d["tiers"]:
            raise LedgerError(f"unknown tier {tier!r}; know {sorted(self.d['tiers'])}")
        if grant < 0 or grant != int(grant):
            raise LedgerError("grant must be a non-negative integer (№7)")
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
            missing = []
            if add_dirs is None:
                missing.append("add_dirs (explicit list of {path, mode}; [] is valid)")
            if tools is None or any(k not in tools for k in TOOL_KEYS) or "mcp" not in tools:
                missing.append("tools (bash, web, edit, subagents, mcp — each stated explicitly)")
            if org_visibility is None:
                missing.append("org_visibility (self|team|subtree|full)")
            if not (purpose and str(purpose).strip()):
                missing.append("purpose (what this hire is for)")
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
            if depth >= self.d.get("max_depth", 10):
                raise LedgerError(f"max org depth {self.d.get('max_depth', 10)} reached")
            if len(self.children(parent, live_only=False)) >= self.d.get("max_children", 16):
                raise LedgerError(
                    f"{parent} already has {self.d.get('max_children', 16)} reports (cap)")

        # №30 — dirs default: top level gets the org's dirs; deeper gets what the
        # parent holds. Explicit grants must fit the parent's capability (path AND
        # mode — a read-only holding cannot beget read/write), whoever the actor is.
        if parent is None:
            parent_map = None
            default = norm_dirs(self.d["dirs"])
        else:
            parent_map = self.effective_dirs(parent)
            default = [dict(d) for d in self.node(parent)["scope"]["add_dirs"]]
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
        if actor == USER:
            pass  # infinite pool — a forcible hire by the user never fails (§4.6)
        else:
            free_before = self.free(actor)
            if free_before < need:
                raise LedgerError(
                    f"{actor} has {free_before:g} free, needs {need} "
                    f"(seat {self.d['tiers'][tier]} + grant {grant})")
            warnings += self._stranding_warnings(actor, free_before, free_before - need)

        # §4.6 cascade: raise every intermediate grant so each hop's invariant holds.
        if parent is not None and actor != parent:
            path = self._path_down(actor, parent)
            for hop in path:  # actor's child-on-path … parent, inclusive
                self.nodes[hop]["grant"] += need
                warnings.append(
                    f"grant inflation (§4.6): {hop} grant raised by {need}; "
                    f"reclaim with reallocate when done")

        if tlost:
            warnings.append(f"tool grants clamped to the parent's own: {tlost}")
        nid = self._new_node(tier, parent, int(grant), name, dirs, tset, vis,
                             str(purpose).strip() if purpose else None)
        if actor == USER:
            why = f' Purpose: {purpose}.' if purpose else ""
            self._notify([parent],
                         f'The user hired "{nid}" ({tier}, grant {int(grant)}) '
                         f'under you.{why}')
            self._notify(self._peers_of(parent, nid),
                         f'The user hired "{nid}" ({tier}) alongside you, under '
                         f'{parent or "the top level"}.{why}')
        self._log("hire", actor, {"node": nid, "parent": parent, "tier": tier,
                                  "grant": int(grant), "purpose": purpose}, warnings)
        return {"node": nid, "warnings": warnings}

    def _path_down(self, top: str, bottom: str) -> list[str]:
        """Nodes from just below `top` down to `bottom`, inclusive. top may be USER."""
        chain = [bottom] + [a for a in self.ancestors(bottom) if a != USER]
        if top != USER:
            if top not in chain:
                raise LedgerError(f"{top} is not an ancestor of {bottom}")
            chain = chain[:chain.index(top)]
        return list(reversed(chain))

    def _new_node(self, tier: str, parent: str | None, grant: int, name: str,
                  dirs: list[dict], tools: dict, vis: str,
                  purpose: str | None) -> str:
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
            "purpose": purpose,
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
        }
        return nid

    # ---------------------------------------------------------------- retire
    def retire(self, actor: str, nid: str) -> dict:
        """Leaf-only (§4.2 decision 1). Self-retirement allowed for leaves (№26)."""
        self._require_authority(actor, nid, allow_self=True)
        if self.node(nid)["state"] == "archived":
            raise LedgerError(f"{nid} is already archived")
        live_kids = self.children(nid)
        if live_kids:
            raise LedgerError(
                f"{nid} has live reports {live_kids}; retire is leaf-only — use dissolve")
        n = self.node(nid)
        freed = self.seat_cost(nid) + n["grant"]
        n["state"] = "archived"
        n["archived_at"] = now()
        self._revoke_audiences_of(nid, reason="grantee retired")
        if actor == USER:
            self._notify([n["parent"]],
                         f'The user retired your report "{nid}" (freed {freed} credits).')
            self._notify(self._peers_of(n["parent"], nid),
                         f'Your peer "{nid}" was retired by the user.')
        self._log("retire", actor, {"node": nid, "freed": freed}, [])
        return {"freed": freed, "warnings": []}

    # ---------------------------------------------------------------- rehire
    def rehire(self, actor: str, nid: str, grant: int | None = None,
               tier: str | None = None) -> dict:
        """§4.2. Parent pays seat + grant; may strand the parent's OTHER archived kids.
        `tier` override (№16, spike-verified): a knowledge bearer answers from context
        and can be consulted at a cheaper tier than it ran at."""
        self._require_authority(actor, nid)
        n = self.node(nid)
        if n["state"] == "live":
            raise LedgerError(f"{nid} is already live")
        fable_futile = (n["model"] == "fable" or tier == "fable") \
            and bool(self.d.get("fable_lock"))
        if fable_futile and actor == USER:
            self.clear_fable_lock()   # a user fable-rehire IS the decree
            fable_futile = False
        if tier is not None:
            if tier not in self.d["tiers"]:
                raise LedgerError(f"unknown tier {tier!r}")
            n["model"] = tier
        parent = n["parent"]
        grant = n["grant"] if grant is None else int(grant)
        need = self.seat_cost(nid) + grant
        payer = USER if parent is None else parent
        free_before = self.free(payer)
        if free_before < need:
            raise LedgerError(
                f"{payer} has {free_before:g} free, needs {need} to rehire {nid}")
        warnings = self._stranding_warnings(payer, free_before, free_before - need)
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
        if tlost:
            n["scope"]["tools"] = tkept
            warnings.append(f"tool grants adjusted to the parent's capability: {tlost}")

        n["state"] = "live"
        n["grant"] = grant
        n["archived_at"] = None
        if actor == USER:
            self._notify([parent],
                         f'The user rehired your report "{nid}" (grant {grant}).')
            self._notify(self._peers_of(parent, nid),
                         f'Your peer "{nid}" was rehired by the user.')
            self._notify([nid], "The user rehired you. You are live again; your "
                                "prior context is intact.")
        self._log("rehire", actor, {"node": nid, "grant": grant}, warnings)
        return {"cost": need, "warnings": warnings}

    # --------------------------------------------------------------- dissolve
    def dissolve(self, actor: str, nid: str) -> dict:
        """Recursive retire, deepest first (§4.2). Takes the whole lineage stack (§8.5)."""
        self._require_authority(actor, nid)
        parent = self.node(nid)["parent"]
        # §8.5: dissolve takes each node's ENTIRE lineage stack with it
        core = self.descendants(nid) + [nid]
        with_lineage = list(core)
        for k in core:
            with_lineage.extend(self.lineage_stack(k))
        order = sorted(set(with_lineage), key=self.depth, reverse=True)
        freed = 0
        for k in order:
            n = self.nodes[k]
            if n["state"] in ("live", "unrecoverable"):
                freed += self.seat_cost(k) + n["grant"]
                n["state"] = "archived"
                n["archived_at"] = now()
            self._revoke_audiences_of(k, reason="dissolved")
        if actor == USER:
            self._notify([parent],
                         f'The user dissolved your report "{nid}" and its whole '
                         f'suborganization ({len(order)} node(s), freed {freed} credits).')
            self._notify(self._peers_of(parent, nid),
                         f'Your peer "{nid}" and its suborganization were dissolved '
                         f'by the user.')
        self._log("dissolve", actor, {"node": nid, "freed": freed,
                                      "count": len(order)}, [])
        return {"freed": freed, "nodes": order, "warnings": []}

    # ----------------------------------------------------------------- delete
    def delete(self, actor: str, nid: str) -> dict:
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
        doomed = [nid] + self.descendants(nid, live_only=False)
        with_lineage = list(doomed)
        for k in doomed:
            with_lineage.extend(self.lineage_stack(k))
        doomed_set = set(with_lineage)
        for k in doomed_set:
            self.nodes.pop(k, None)
            (self.d.get("mail") or {}).pop(k, None)
            (self.d.get("mail_log") or {}).pop(k, None)
            (self.d.get("notices") or {}).pop(k, None)
        self.d["audiences"] = [
            a for a in self.d["audiences"]
            if a["grantee"] not in doomed_set and a["grantor"] not in doomed_set]
        self.d["audience_requests"] = [
            r for r in self.d["audience_requests"]
            if r["from"] not in doomed_set and r["target"] not in doomed_set
            and r["currently_at"] not in doomed_set]
        extra = len(doomed) - 1
        self._notify([parent],
                     f'The user permanently DELETED your report "{nid}"'
                     + (f" and its suborganization ({extra} more node(s))" if extra else "")
                     + ". Its records are gone from the org.")
        self._notify(peers, f'Your peer "{nid}" was permanently deleted by the user.')
        self._log("delete", actor, {"node": nid, "removed": sorted(doomed_set)}, [])
        return {"deleted": sorted(doomed_set), "warnings": []}

    # ------------------------------------------------------------- reallocate
    def reallocate(self, actor: str, nid: str, delta: int) -> dict:
        """±Δ between a node and its parent (§4.2). -Δ is the classic stranding op."""
        self._require_authority(actor, nid)
        self._require_live(nid)
        n = self.node(nid)
        delta = int(delta)
        payer = USER if n["parent"] is None else n["parent"]
        warnings: list[str] = []
        if delta > 0:
            if self.free(payer) < delta:
                raise LedgerError(f"{payer} has only {self.free(payer):g} free")
            warnings += self._stranding_warnings(
                payer, self.free(payer), self.free(payer) - delta)
        elif delta < 0:
            if self.free(nid) < -delta:
                raise LedgerError(
                    f"{nid} has only {self.free(nid):g} unused; the rest is committed")
            warnings += self._stranding_warnings(
                nid, self.free(nid), self.free(nid) + delta)
        n["grant"] += delta
        if actor == USER and delta != 0:
            self._notify([nid],
                         f"The user adjusted your grant by {delta:+d} "
                         f"(now {n['grant']}, free {self.free(nid):g}).")
            self._notify([n["parent"]],
                         f'The user adjusted "{nid}"\'s grant by {delta:+d}.')
        self._log("reallocate", actor, {"node": nid, "delta": delta}, warnings)
        return {"grant": n["grant"], "warnings": warnings}

    # --------------------------------------------------------- promote/demote
    def promote(self, actor: str, nid: str, new_parent: str | None) -> dict:
        """Re-parent upward (§4.5): new_parent must be a strict ancestor of the current
        parent (None = to top level, actor must be USER)."""
        cur = self.parent(nid)
        target = USER if new_parent is None else new_parent
        if target != USER and not self.is_ancestor(target, nid):
            raise LedgerError(f"promote target {target} is not above {nid}")
        if target == cur:
            raise LedgerError(f"{nid} already reports to {cur}")
        if cur != USER and target != USER and not self.is_ancestor(target, cur):
            raise LedgerError("promote must move the node strictly upward (§4.2)")
        return self._move("promote", actor, nid, new_parent)

    def demote(self, actor: str, nid: str, new_parent: str) -> dict:
        """Re-parent downward/lateral under another of the actor's descendants (§4.5)."""
        if new_parent == nid or new_parent in self.descendants(nid, live_only=False):
            raise LedgerError("cannot demote a node into its own subtree — cycle (§4.5)")
        return self._move("demote", actor, nid, new_parent)

    def _move(self, op: str, actor: str, nid: str, new_parent: str | None) -> dict:
        """§4.5 LCA credit path. Release P_old→L and acquire L→P_new cancel hop by hop,
        so every node's free is unchanged — budget-neutral, cannot fail on credits."""
        self._require_authority(actor, nid)
        n = self.node(nid)
        p_old = n["parent"]
        if new_parent is not None:
            self._require_live(new_parent)
            self._require_authority(actor, new_parent, allow_self=True)
            if new_parent == nid or new_parent in self.descendants(nid, live_only=False):
                raise LedgerError("target is inside the moved subtree — cycle (§4.5)")
        if p_old is not None:
            self._require_authority(actor, p_old, allow_self=True)

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
        if c:
            for hop in self._chain_up(p_old, lca):     # release: grants shrink
                self.nodes[hop]["grant"] -= c
            for hop in self._path_down(lca if lca is not None else USER, new_parent) \
                    if new_parent is not None else []:  # acquire: grants swell
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
        if actor == USER:
            subtree = len(self.descendants(nid, live_only=False))
            tail = f" Its suborganization ({subtree} node(s)) moved with it." if subtree else ""
            frm, to = p_old or "the top level", new_parent or "the top level"
            self._notify([p_old],
                         f'The user moved your report "{nid}" away — it now reports '
                         f'to {to}.{tail}')
            self._notify(prior_peers,
                         f'Your peer "{nid}" was moved by the user to under {to}.{tail}')
            self._notify([new_parent],
                         f'The user moved "{nid}" (from {frm}) to report to you.{tail}')
            self._notify(self._peers_of(new_parent, nid),
                         f'"{nid}" joined your team (moved by the user from {frm}).{tail}')
            self._notify([nid],
                         f"The user moved you: you now report to {to} (you were under "
                         f"{frm}). Your entire suborganization moved with you.")
        self._log(op, actor, {"node": nid, "from": p_old, "to": new_parent}, warnings)
        return {"warnings": warnings}

    def _chain_up(self, frm: str | None, until: str | None) -> list[str]:
        """Node ids from `frm` up to but excluding `until` (None = USER)."""
        out = []
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
    def revoke_dir(self, actor: str, nid: str, dir_: str) -> dict:
        """№30 explicit revoke — cascades into the subtree (their sets must stay ⊆)."""
        self._require_authority(actor, nid)
        removed = []
        for k in [nid] + self.descendants(nid, live_only=False):
            dirs = self.nodes[k]["scope"]["add_dirs"]
            if any(d["path"] == dir_ for d in dirs):
                self.nodes[k]["scope"]["add_dirs"] = [d for d in dirs if d["path"] != dir_]
                removed.append(k)
        self._log("revoke_dir", actor, {"node": nid, "dir": dir_, "removed": removed}, [])
        return {"removed_from": removed, "warnings": []}

    def _sweep_dirs(self, nid: str) -> list[str]:
        """After a move or scope shrink: clamp the subtree's dirs AND tools to each
        parent in turn (№30 — capability sets stay ⊆ all the way down)."""
        dropped: list[str] = []

        def clamp(k: str, allowed: dict[str, str] | None, ptools: dict | None):
            sc = self.nodes[k]["scope"]
            kept, lost = self._clamp_dirs(sc["add_dirs"], allowed, strict=False)
            sc["add_dirs"] = kept
            dropped.extend(lost)
            tkept, tlost = self._clamp_tools(sc["tools"], ptools, strict=False)
            sc["tools"] = tkept
            dropped.extend(tlost)
            own = {d["path"]: d["mode"] for d in kept}
            for ch in self.children(k, live_only=False):
                clamp(ch, own, tkept)

        parent = self.node(nid)["parent"]
        clamp(nid, self.effective_dirs(parent),
              None if parent is None else self.node(parent)["scope"]["tools"])
        return sorted(set(dropped))

    # ------------------------------------------------------------- node scope
    def set_scope(self, actor: str, nid: str, add_dirs=None, tools=None,
                  org_visibility=None, permission_mode=None,
                  charter=None, team_charter=None) -> dict:
        """Per-node configuration (the ⚙): dir grants with modes, the full tool set
        (built-ins + MCP servers), org-structure visibility. Superior-only."""
        self._require_authority(actor, nid)
        n = self.node(nid)
        sc = n["scope"]
        warnings: list[str] = []
        changed_caps = False
        if add_dirs is not None:
            req = norm_dirs(add_dirs)
            kept, _ = self._clamp_dirs(req, self.effective_dirs(n["parent"]), strict=True)
            sc["add_dirs"] = kept
            changed_caps = True
        if tools is not None:
            ptools = (None if n["parent"] is None
                      else self.node(n["parent"])["scope"]["tools"])
            sc["tools"], _ = self._clamp_tools(tools, ptools, strict=True)
            changed_caps = True
        if changed_caps:
            swept = self._sweep_dirs(nid)
            if swept:
                warnings.append(f"subtree grants clamped to the new set (№30): {swept}")
        if org_visibility is not None:
            if org_visibility not in VIS_LEVELS:
                raise LedgerError(f"org_visibility must be one of {VIS_LEVELS}")
            sc["org_visibility"] = org_visibility
        if permission_mode is not None:
            sc["permission_mode"] = permission_mode
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
        return {"scope": sc, "warnings": warnings}

    def reorder(self, actor: str, nid: str, before: str | None = None,
                after: str | None = None) -> dict:
        """Cosmetic left-to-right position among siblings. No org effect — a UX
        affordance for the managing user (user-ruled); deliberately not logged as
        an authority-bearing operation beyond the ancestry check."""
        self._require_authority(actor, nid)
        n = self.node(nid)
        sibs = [k for k in self.children(n["parent"], live_only=False) if k != nid]
        order = lambda k: self.nodes[k].get("ui_order", 0)   # noqa: E731
        if before and before in sibs:
            i = sibs.index(before)
            lo = order(sibs[i - 1]) if i > 0 else order(before) - 2.0
            n["ui_order"] = (lo + order(before)) / 2
        elif after and after in sibs:
            i = sibs.index(after)
            hi = order(sibs[i + 1]) if i + 1 < len(sibs) else order(after) + 2.0
            n["ui_order"] = (order(after) + hi) / 2
        else:
            raise LedgerError("reorder needs a sibling as before= or after=")
        return {"ui_order": n["ui_order"], "warnings": []}

    # -------------------------------------------------------------- audiences
    def _revoke_audiences_of(self, nid: str, reason: str):
        self.d["audiences"] = [
            a for a in self.d["audiences"]
            if a["grantee"] != nid and a["grantor"] != nid]

    def _sweep_audiences(self) -> list[tuple[str, str]]:
        """§7.3 auto-revoke: drop grants whose grantor is no longer an ancestor of the
        grantee. User audiences are never swept (№11)."""
        kept, revoked = [], []
        for a in self.d["audiences"]:
            if a["grantor"] == USER or (
                    a["grantee"] in self.nodes and a["grantor"] in self.nodes
                    and self.is_ancestor(a["grantor"], a["grantee"])):
                kept.append(a)
            else:
                revoked.append((a["grantee"], a["grantor"]))
        self.d["audiences"] = kept
        return revoked

    # --------------------------------------------------- fable limit (user ruling)
    # ----------------------------------------------------- credit requests
    def request_credits(self, nid: str, new_limit, reason) -> dict:
        """A TOP-LEVEL agent asks the user directly for a larger grant. Not mail:
        a structured request (old → new + reason) the user approves or denies
        with one click. One pending request per node."""
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
        if new_limit <= old:
            raise LedgerError(f"requested limit {new_limit} must exceed the "
                              f"current grant {old}")
        if not (reason and str(reason).strip()):
            raise LedgerError("a reason is required")
        reqs = self.d.setdefault("credit_requests", [])
        if any(r["node"] == nid and r["status"] == "pending" for r in reqs):
            raise LedgerError("you already have a pending credit request — wait "
                              "for the user's decision")
        req = {"id": f"cr{len(reqs) + 1}", "node": nid, "old": old,
               "new": new_limit, "reason": str(reason).strip(),
               "at": now(), "status": "pending"}
        reqs.append(req)
        self._log("credit_request", nid, {"old": old, "new": new_limit}, [])
        return {"requested": new_limit, "increase": new_limit - old,
                "status": "pending — the user will approve or deny"}

    def credit_request_action(self, rid: str, action: str) -> dict:
        req = next((r for r in self.d.get("credit_requests", [])
                    if r["id"] == rid), None)
        if req is None or req["status"] != "pending":
            raise LedgerError(f"no pending credit request {rid!r}")
        if action not in ("approve", "deny"):
            raise LedgerError("action must be approve|deny")
        nid = req["node"]
        if action == "approve":
            if nid not in self.nodes or self.node(nid)["state"] != "live":
                raise LedgerError(f"{nid} is no longer live — request is moot")
            delta = req["new"] - self.node(nid)["grant"]
            if delta > 0:
                self.reallocate(USER, nid, delta)
            req["status"] = "approved"
            self._notify([nid], f"The user APPROVED your credit request — your "
                                f"grant is now {self.node(nid)['grant']:g}.")
        else:
            req["status"] = "denied"
            if nid in self.nodes:
                self._notify([nid], f"The user DENIED your credit request "
                                    f"({req['old']} → {req['new']}). Work within "
                                    f"your current grant or escalate differently.")
        self._log("credit_" + action, USER, {"node": nid, "new": req["new"]}, [])
        return req

    def fable_limit_hit(self, detecting_node: str | None, detail: str) -> dict:
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

    def clear_fable_lock(self):
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
        pred = dict(n)
        pred.update({
            "state": "archived", "archived_at": now(), "grant": 0,
            "bearer_state": "knowledge", "successor": nid, "predecessor": n.get("predecessor"),
            "ui_order": n.get("ui_order", 0) + 0.001,
            "scope": {**n["scope"],
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

    def mark_unrecoverable(self, nid: str, reason: str):
        """№31: ledger said live, the session cannot actually resume."""
        n = self.node(nid)
        n["state"] = "unrecoverable"
        self._notify([n["parent"]],
                     f'⚠ Your report "{nid}" is UNRECOVERABLE — its session failed to '
                     f'resume ({reason}). Its seat is still held; retire or dissolve it '
                     f'to free the credits.')
        self._log("unrecoverable", SYSTEM, {"node": nid, "reason": reason}, [])

    # ------------------------------------------------------------------ audit
    def audit(self) -> dict:
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
    def tree(self) -> dict:
        """Derived view for the API/UI: nested nodes with computed fields."""
        def build(nid: str) -> dict:
            n = self.nodes[nid]
            return {
                "id": nid,
                "title": n["title"],
                "purpose": n.get("purpose"),
                "tier": n["model"],
                "model_id": self.d["models"].get(n["model"], n["model"]),
                "state": n["state"],
                "seat": self.d["tiers"][n["model"]],
                "grant": n["grant"],
                "free": None if n["state"] != "live" else self.free(nid),
                "session_id": n["session_id"],
                "scope": n["scope"],
                "ui_order": n.get("ui_order", 0),
                "cost_usd": round(float(n.get("cost_usd") or 0.0), 4),
                "occupancy": n.get("occupancy"),
                "context_window": n.get("context_window"),
                "charter": n.get("charter"),
                "team_charter": n.get("team_charter"),
                "mail_pending": len((self.d.get("mail") or {}).get(nid, [])),
                "limit_locked": bool(n.get("limit_locked")),
                "last_status": n.get("last_status"),
                "frozen": ({k: n["frozen"].get(k) for k in ("at", "until", "error")}
                           if n.get("frozen") else None),
                "audiences_held": [a["grantor"] for a in self.d["audiences"]
                                   if a["grantee"] == nid],
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
            "credit_requests": [r for r in self.d.get("credit_requests", [])
                                if r["status"] == "pending"],
            "tiers": self.d["tiers"],
            "audiences": self.d["audiences"],
            "roots": [build(c) for c in self.org_children(None)],
            "audit": self.audit(),
            "cost_usd_total": round(sum(float(v.get("cost_usd") or 0.0)
                                        for v in self.nodes.values()), 4),
            "user_inbox_count": len(self.d.get("user_inbox", [])),
            "user_inbox_newest": (self.d.get("user_inbox") or [{}])[-1].get("at"),
            "fable_lock": self.d.get("fable_lock"),
            "fable_limit_policy": self.d.get("fable_limit_policy", "halt"),
            "audience_requests": self.d.get("audience_requests", []),
        }
