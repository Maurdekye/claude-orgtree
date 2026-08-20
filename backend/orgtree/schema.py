"""The org document's shape, in one place (typing wave, docs/typing-plan.md).

One org = one JSON file (store.py). Every dict that file contains is declared
here as a TypedDict, so pyright can catch key typos and shape drift — the class
of bug the misleading-reads history is made of. Nothing here exists at runtime
beyond the type objects: importing this module changes no behavior.

Ground rules:
- These types describe what the CODE writes today (Build sources: ledger.py,
  supervisor.py, api.py). Where old docs on disk may lack a key, the reader
  already tolerates it (`.get`) — model that as NotRequired, not as a lie that
  the key is always there.
- Extend this file rather than re-deriving a dict shape in a docstring. If a
  shape is genuinely open (freeform op payloads), say `dict[str, Any]` at the
  use site — never guess a narrower type than the code proves.
- Runtime-inert: `TypedDict` instances are plain dicts; there is no validation
  and none is wanted (store.py loads whatever JSON is on disk).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

# NotRequired lands in typing on 3.11; fastapi already depends on
# typing_extensions, so this adds no install weight on our 3.10.
from typing_extensions import NotRequired, TypedDict

NodeState = Literal["live", "archived", "unrecoverable"]   # №31
DirMode = Literal["rw", "ro"]
Visibility = Literal["self", "team", "subtree", "full"]     # VIS_LEVELS
PermissionMode = Literal["plan", "default", "acceptEdits", "bypassPermissions"]  # PM_LEVELS
# §8 lineage. "lost" = a generation whose transcript is gone — kept for the
# record, never consultable (Org.reseed writes it; rehire refuses on it).
BearerState = Optional[Literal["knowledge", "preserving", "lost"]]
Effort = Literal["low", "medium", "high", "xhigh", "max"]   # Org.EFFORTS


class DirGrant(TypedDict):
    """One entry of a capability set (№30) — see norm_dirs()."""
    path: str
    mode: DirMode


class ToolGrant(TypedDict):
    """The normalized tool switches — see norm_tools(). `mcp` is a sorted
    server-name list; `["*"]` means every registered server, present and
    future."""
    bash: bool
    web: bool
    edit: bool
    subagents: bool
    mcp: list[str]


class NodeScope(TypedDict):
    """The per-node ⚙ configuration (set_scope), clamped against the parent
    chain and the kiosk ceiling."""
    permission_mode: str
    add_dirs: list[DirGrant]
    tools: ToolGrant
    org_visibility: str
    # thinking-effort dial (one of Effort) — set_scope writes it HERE, not on
    # the node (ledger sc["effort"]; supervisor reads sc.get("effort")).
    # Absent = the CLI default ("" clears by popping the key).
    effort: NotRequired[str]
    # FR-24b: per-node auto-cheap-compact override — {enabled?, occ?, idle_s?}
    # merged key-by-key over the org's `auto_cheap_compact`; absent = inherit
    auto_cheap_compact: NotRequired[dict[str, Any]]
    # which model VERSION inside the tier (ledger.MODEL_VERSIONS) — a
    # subcategory of the tier, not a tier of its own. Absent = the tier
    # default. Neither a permission nor a price, so it clamps against nothing,
    # and `Org.model_for` re-validates it against the node's CURRENT tier on
    # every read, so a switch_model can never drag a stale choice with it.
    model_version: NotRequired[str]


class Denial(TypedDict):
    """№7: one headless auto-deny from the CLI result event (_after_turn)."""
    tool: str
    arg: NotRequired[str | None]


class TurnStat(TypedDict):
    """№15: one entry of the per-node turn ring (capped at 20)."""
    at: str
    cost: float
    ms: NotRequired[int | None]
    denials: int
    # killed-turn accounting (2026-08-04): output tokens ride every entry so a
    # later killed turn can estimate its unreported spend from the node's own
    # $/token history; `killed` marks the kill, `estimated` marks a derived
    # (not API-reported) cost
    toks: NotRequired[int]
    killed: NotRequired[bool]
    estimated: NotRequired[bool]


class FrozenInfo(TypedDict, total=False):
    """№41 freeze marker. Kinds are commutative — `error` and `spend_error`
    coexist without overwriting each other. Kind FLAGS (e.g. `spend`) are
    `True` when that freeze kind is active (Org.__init__ retags pre-№41
    spend freezes; supervisor.hard_freeze writes them)."""
    at: str
    until: str | None
    until_ts: float | None
    error: str | None
    # `limit` is the usage-limit kind flag, and it exists to be a POSITIVE
    # marker. The pre-№41 retag in Org.__init__ matches on shape — error, no
    # until, no resume_texts, no kind flag True — and a genuine usage-limit
    # freeze hits that shape exactly whenever the reset time is unparseable AND
    # no replay text was kept (a /command turn, or an unconfirmed batch). It
    # was then rewritten as a SPEND freeze, which resume_frozen skips forever:
    # ▶ resume silently did nothing and the agent could never be woken. Caught
    # live 2026-08-04 by the turn-lifecycle suite. Setting this flag takes the
    # retag's `not any(v is True …)` guard out of the picture by construction.
    limit: bool
    # where `until_ts` came from (user ruling 2026-08-18): "text" (parsed out
    # of the CLI's error prose), "usage:<lane>" (looked up in the account's
    # own usage readout — see limits.reset_for), "probe" (nothing could
    # answer, so it carries the blind 5-minute floor), "capped" (an untrusted
    # run was cut off and `until_ts` deliberately REMOVED — there is no number
    # left for a provenance to describe), or "inherited" (a re-freeze kept the
    # previous record's still-plausible horizon; the provenance of THAT number
    # belonged to the earlier freeze, so this one does not claim it).
    # Diagnostic: a freeze that opened an api_fallback window records what the
    # window was priced on. ⚠ On an `untrusted` freeze it records where the
    # NUMBER came from and nothing more — no window was priced at all.
    reset_src: str
    # the transient/connection kind (user report 2026-08-06): a network drop
    # freezes with a short exponential until_ts; ▶/auto-resume own it like
    # `limit` (resume_frozen's owned-kinds exemption names both)
    connection: bool
    spend: bool
    spend_error: str | None
    # the limit was reported by NOBODY BUT THE AGENT (2026-08-18): the
    # clean-result gate promotes a short final answer that names a limit into
    # a freeze, and that text is the agent's own. Such a freeze still carries
    # a timestamp and still wakes — but it may not open an api_fallback
    # window (the org's key would bill for a wall that need not exist), and
    # after UNTRUSTED_LIMIT_RUNS consecutive ones the node waits for a person.
    # ⚠ read it beside `reset_src`: on an untrusted freeze that field says
    # where the NUMBER came from, not that the number priced anything.
    untrusted: bool
    # api_fallback (2026-08-17): this limit freeze was recorded while the
    # org's fallback window was already OPEN — i.e. the KEY lane hit a wall,
    # not the subscription. Readiness must not insta-wake it into the same
    # wall; it waits for its own until_ts like any limit freeze.
    # (⚠ exempted in supervisor._resumable's other-kind test, like the kinds.)
    on_fallback: bool
    # prompts to replay when the freeze lifts (supervisor queues them)
    resume_texts: list[str]


class OracleExchange(TypedDict):
    """One Q&A with a preserving-oracle bearer (supervisor logs on the node)."""
    q: str
    a: str
    at: str


class InflightInfo(TypedDict):
    """The turn currently running (supervisor): prompt tail + start stamp."""
    at: str
    text: str
    cmd: NotRequired[bool]


class NodeDoc(TypedDict):
    """One agent seat. Created by Org._new_node (hire); the NotRequired tail
    is runtime bookkeeping the supervisor adds as turns happen."""
    session_id: str
    model: str                      # tier key into OrgDoc["tiers"]
    parent: str | None              # None = top level (§7.4: the user is root)
    grant: int
    state: NodeState
    title: str
    charter: str | None
    created: str
    archived_at: str | None
    # FR-22: set by rescind() — archived AND the superior's grant clawed
    # back; the marker makes a second rescind a no-op instead of a
    # double-subtraction
    rescinded_at: NotRequired[str]
    pid: int | None
    ui_order: float
    scope: NodeScope
    # external response handles (panel hires — e.g. the in-game Prompt Wizard,
    # 2026-08-20): outward @mcp:<peer> addresses THIS node may post_mail
    # directly, at any depth, without the org-inbox audience. Each send is
    # scoped to exactly these addresses and attributed by=node in the
    # org_inbox row; the grant rides the seat (survives retire/rehire).
    external_handles: NotRequired[list[str]]
    # §8 lineage axis — second axis, never an org edge. FR-24's cheap-compact
    # replacement uses the same pair: `predecessor` on the replacement points
    # at the archived original (whose scratch the supervisor grants read-only
    # each turn, transcript copy included), and f327b39 sets the `successor`
    # backlink + bearer_state so rehire recognises it as the replacement's
    # own consultable bearer — a lineage generation, not a retired sibling
    lineage: str
    generation: int
    predecessor: str | None
    successor: str | None
    bearer_state: BearerState
    # ---- runtime bookkeeping (supervisor / api) ----
    # (NB: `effort` lives in NodeScope, not here — sc["effort"].)
    team_charter: NotRequired[str | None]
    cost_usd: NotRequired[float]
    # None = explicitly unknown (compact_split resets the successor's reading)
    occupancy: NotRequired[int | None]
    context_window: NotRequired[int]
    last_status: NotRequired[dict[str, Any] | None]
    prev_status: NotRequired[dict[str, Any] | None]
    inflight: NotRequired[InflightInfo | None]
    last_denials: NotRequired[list[Denial]]
    turns: NotRequired[list[TurnStat]]
    frozen: NotRequired[FrozenInfo | None]
    remote_controlled: NotRequired[dict[str, Any] | None]  # FR-01 {at, pid} — the node is parked while the user drives its session directly
    limit_locked: NotRequired[bool]
    oracle_exchanges: NotRequired[list[OracleExchange]]
    # CLI-side compact boundaries seen in this node's session JSONL (1b,
    # 2026-08-06): absent = never observed (the first observation baselines
    # WITHOUT minting); each later increment mints a lost-generation record
    # None means "re-baseline me": the session id was just reassigned, so the
    # count belongs to a file this node no longer owns (compact_split's fork
    # already carries its own /compact boundary; cheap_compact and reseed mint
    # an empty session). _after_turn re-reads the true count WITHOUT minting.
    cli_compactions: NotRequired[int | None]
    # the line offset of the boundary a LOST row was minted against, so a
    # later recovery reads its cut point instead of re-deriving it — deriving
    # it is only sound while every boundary still has its row (2026-08-20)
    cli_boundary_offset: NotRequired[int]
    # consecutive network-classified turn failures (user report 2026-08-06);
    # reset by any completed turn, capped at NET_RETRY_MAX then manual
    net_fail_run: NotRequired[int]
    # consecutive limit freezes whose only evidence was the agent's own final
    # answer (see FrozenInfo.untrusted). Cleared by any completed turn, like
    # net_fail_run — the count is CONSECUTIVE, and it is what stops a node
    # that keeps answering "usage limit reached" from waking itself forever.
    untrusted_limit_run: NotRequired[int]
    # cheap-compact marker (user feature 2026-08-17): the CURRENT session was
    # minted by cheap_compact — it started EMPTY (no CLI summary), so the
    # supervisor splices breadcrumbs.md into the identity prompt on every
    # spawn. A normal compaction (whose successor carries its own summary)
    # clears it.
    cheap_compacted: NotRequired[bool]
    # user bug 2026-08-18: the CURRENT session id was MINTED (cheap_compact,
    # reseed) and has never been handed to the CLI — so no transcript for it
    # exists yet, and that is normal, not damage. №31's startup reconcile
    # condemns a live node whose transcript is missing, judging "has it ever
    # run" by the node-lifetime `cost_usd`; a minted session inherits that
    # cost while owning none of the history, so cheap-compacting an agent and
    # closing orgtree before messaging it marked the agent UNRECOVERABLE (it
    # then refuses mail — the seat needs a re-seed to come back). This marker
    # makes the "has it run" question SESSION-scoped. Cleared by the first
    # completed turn, and self-healed by reconcile the moment a transcript
    # for the session id does exist.
    session_unrun: NotRequired[bool]
    # ⭐ the user-override record (ruling 2026-08-06): Org.unstick moves the
    # released freeze here {by, at, was} — evidence, never erasure
    unstuck: NotRequired[dict[str, Any]]


class AudienceGrant(TypedDict):
    """§7.3 — a standing speak-directly grant. `delegated_by` marks a
    DELEGATED grant (an agent opened someone else's ear); the sweep anchors
    such a grant on the delegator, not the grantor."""
    grantee: str
    grantor: str
    granted_at: str
    reason: str
    delegated_by: NotRequired[str]


class NoticeEntry(TypedDict):
    """One queued org-change notice in OrgDoc["notices"][<node>] — delivered
    at the node's next turn boundary (Org._notify)."""
    at: str
    text: str


class NoticeLogEntry(TypedDict):
    """One row of the org-wide notice audit trail (capped at 800)."""
    node: str
    at: str
    text: str


# One entry of the user's inbox (OrgDoc["user_inbox"]). Functional form:
# "from" is a keyword. `id` is NotRequired because one writer
# (audience_forward) omits it — Org.__init__ backfills ids on next load.
UserMailEntry = TypedDict("UserMailEntry", {
    "id": NotRequired[str],
    "from": str,
    "kind": str,
    "body": str,
    "at": str,
    # FR-21: download-card metas [{name, path, bytes}], path relative to the
    # SENDER's scratch (its outbox/ — _agent_send_file's card shape); the
    # inbox renders them with fileUrl keyed on `from`
    "attachments": NotRequired[list[dict[str, Any]]],
})


# One queued message in OrgDoc["mail"][<node>] (№11/№17: durable pending copy,
# retractable by id until delivery). Functional form: "from" is a keyword.
MailEntry = TypedDict("MailEntry", {
    "id": str,
    "from": str,
    # message|question|request|decision|status — or "notice"
    # (orgtree_send_notice): minted ONLY by that tool, the single marker the
    # whole feature keys on (envelope styling, no-wake drives: Org.waking_mail)
    "kind": str,
    "body": str,
    "at": str,
    "relationship": NotRequired[Optional[str]],
    "attachments": NotRequired[list[dict[str, Any]]],
    "delivering": NotRequired[bool],
    "retracted": NotRequired[bool],   # api node_mail_retract tombstones in place
    "net_id": NotRequired[str],       # F-06: hub message id — _confirm_delivered
                                      # turns it into a READ receipt
    "reply_to": NotRequired[dict[str, Any]],  # FR-05: SNAPSHOT of the mail
                                      # this replies to ({id, from, at, gist}
                                      # captured at send — quoted by
                                      # _mail_block; no lookup needed)
})


class OrgInboxEntry(TypedDict):
    """The inter-org bridge log (capped at 200): one inbound or outbound
    message on the org's single outside face."""
    id: str
    dir: Literal["in", "out"]
    peer: str
    body: str
    at: str
    by: NotRequired[str]     # internal attribution — outbound speaks as the org
    # held-handle send (external_handles): the sender spoke to ITS OWN outside
    # channel, not for the org — _extern_scan exposes `by` to the peer for
    # exactly these rows and no others
    attributed: NotRequired[bool]
    # ---- F-06 @net: delivery states (outbound rows only) ----
    state: NotRequired[str]         # queued → sent (hub custody = "received")
                                    # → delivered (peer org inbox) → read
                                    # (a peer agent's turn consumed it)
    state_at: NotRequired[str]
    net_id: NotRequired[str]        # hub message id — the state-update lookup
                                    # key (tolerant of 200-cap-trimmed rows)
    attachments: NotRequired[list[dict[str, Any]]]   # [{name, bytes}] display


class KioskCfg(TypedDict, total=False):
    """Kiosk is a TYPE (user ruling): limits bind whether or not the public
    URL is enabled — `enabled` only gates the token gateway."""
    enabled: bool
    token: str
    credits: int
    spend_limit: float
    storage_limit_mb: int
    sandbox: bool
    sandbox_secret: str
    api_key: str                        # per-kiosk key (creation form / dashboard)
    auto_raise: bool
    max_scope: dict[str, Any] | None    # the permission ceiling (ceiling spec)


class OrgDoc(TypedDict):
    """The whole persisted document — Org.d. Org.create() writes the required
    keys; everything later code `setdefault`s is NotRequired."""
    version: int
    slug: str
    name: str
    created: str
    tiers: dict[str, int]
    models: dict[str, str]
    workspace: str | None
    dirs: list[DirGrant]
    permission_mode: str
    default_tools: ToolGrant
    default_visibility: str
    # org-wide effort fallback for nodes with no scope effort ("" = CLI
    # default, no flag) — resolved LIVE in supervisor._build_cmd
    default_effort: NotRequired[str]
    max_top_grant: int
    default_top_grant: int
    credit_requests: list[dict[str, Any]]
    compact_at: float
    fable_limit_policy: str
    fable_filter_policy: str
    nodes: dict[str, NodeDoc]
    audiences: list[AudienceGrant]
    audience_requests: list[dict[str, Any]]
    events: list[dict[str, Any]]
    # ---- setdefault'd / optional org state ----
    cascade_hire: NotRequired[bool]         # §4.6 cost-bubbling toggles
    cascade_alloc: NotRequired[bool]
    max_depth: NotRequired[int]             # №34 runaway insurance (read w/ defaults)
    max_children: NotRequired[int]
    mail: NotRequired[dict[str, list[MailEntry]]]
    mail_log: NotRequired[dict[str, list[MailEntry]]]   # full-body archive, cap 100/node
    user_inbox: NotRequired[list[UserMailEntry]]
    user_outbox: NotRequired[list[dict[str, Any]]]      # MailEntry + "to" (user's Sent)
    user_mail_log: NotRequired[list[UserMailEntry]]     # api: dismissed-inbox archive
    notices: NotRequired[dict[str, list[NoticeEntry]]]
    notice_log: NotRequired[list[NoticeLogEntry]]
    delivering: NotRequired[dict[str, list[dict[str, Any]]]]  # supervisor in-flight mail batches
    steered_log: NotRequired[dict[str, list[dict[str, Any]]]]  # per-NODE steer history, org-keyed
    turn_error_log: NotRequired[dict[str, list[dict[str, Any]]]]  # per-NODE turn failures {at, text} — the durable half of last_error
    asks: NotRequired[list[dict[str, Any]]]  # F-04 questions-to-the-user {id, node, kind, question, options?, multi?, questions?, at, status, reason?, answer?, resolved_at?}
    # FR-13: pending permission-scope requests {id, node, items: [{kind:
    # dir|tool|mcp|permission_mode, ...}], reason, at, rev, status} — one
    # pending entry per node (items merge by identity); a tab family of the
    # FR-14 batch beside asks + credit_requests
    scope_requests: NotRequired[list[dict[str, Any]]]
    # FR-24b: org-level auto-cheap-compact-on-wake config
    # {enabled: bool, occ: float 0..1, idle_s: int} — disabled by default
    auto_cheap_compact: NotRequired[dict[str, Any]]
    # FR-18: watchdogs — persistent pets {id, owner, name, kind:
    # file|command|process|stream, target, pattern?, interval_s, state:
    # armed|paused|exited, high_water?, fired, last_check?, last_fired?,
    # events: [{at, gist}], exit?}. Free by ruling; engine =
    # supervisor.start_watchdog_engine
    watchdogs: NotRequired[list[dict[str, Any]]]
    documents: NotRequired[list[dict[str, Any]]]  # FR-03 presented documents {id, node, title, body, at} — newest 10/node, 100/org
    org_inbox: NotRequired[list[OrgInboxEntry]]
    org_inbox_read: NotRequired[int]
    kiosk: NotRequired[KioskCfg | None]
    sandbox: NotRequired[dict[str, Any]]    # api: {enabled, secret, limit_mb?}
    sandbox_vols_base: NotRequired[int]     # HISTORICAL (pre-disk legacy
                                            # enforcement, retired D-063) —
                                            # system-volume image seed (bytes);
                                            # storage accounting charges growth only
    fable_lock: NotRequired[dict[str, Any] | None]
    spend_frozen: NotRequired[bool]
    storage_frozen: NotRequired[bool]   # HISTORICAL (pre-disk legacy breach) —
                                        # never set since D-063; cleared at
                                        # disk migration
    storage_blocked: NotRequired[bool]  # ACL block (legacy) / turn pause (disk ≥90%)
    storage_full: NotRequired[bool]     # disk ≥99% — the persistent UI alert state
    disk: NotRequired[dict[str, Any]]   # {size_mb, migrated_at,
                                        #  pending_size_mb?} — org rides its
                                        # virtual disk (sandbox.migrate_to_disk);
                                        # pending = staged shrink, applied when
                                        # the org's container is next down
    storage_warned: NotRequired[bool]
    auto_resume: NotRequired[bool]
    auto_resume_last: NotRequired[float]
    # user option 2026-08-17: cheap-compact a limit-frozen node right before
    # the auto-resume timer wakes it — the freeze outlived the cache TTL, so
    # the swap dodges the cold transcript reload (D-114's arithmetic).
    # Auto-path only; the manual ▶ resumes sessions as they are.
    auto_resume_compact: NotRequired[bool]
    # ---- @net: mail-hub client (F-06) — net.py owns these ----
    net_identity: NotRequired[dict[str, Any]]   # {secret, fingerprint, slug,
                                                # minted_at} — the SECRET lives
                                                # here and ONLY here; never in
                                                # tree payloads or agent context
    net_hubs: NotRequired[list[dict[str, Any]]]  # [{id, address, enabled,
                                                 #   name?}] — name discovered
                                                 # on connect, never typed
    net_autoconnect: NotRequired[bool]      # default True: local hub auto-joins
    net_state: NotRequired[dict[str, Any]]  # per HUB ID: {registered_at,
                                            #   last_ok, seen_ids ring}
    net_spool: NotRequired[dict[str, Any]]  # per HUB ID: [SpoolEntry] outbound
                                            # SpoolEntry = {id (32-hex hub msg
                                            # id, idempotency key), to (bare
                                            # net slug), body, kind, at, oid
                                            # (org_inbox row id), tries,
                                            # last_err?, attachments: [abs]}
    headless: NotRequired[bool]             # §9.6: no user present; user-bound
                                            # asks auto-deny (requires api_key)
    api_key: NotRequired[str]               # §9.5: per-org ANTHROPIC_API_KEY
    # api-key FALLBACK (user feature 2026-08-17): with this ON the stored
    # api_key is a SPARE, not the lane — routine turns bill the subscription,
    # and only while a usage-limit freeze holds the subscription lane does
    # spawn_env / the bridge proxy switch to the key. The window closes at
    # the limit's own reset time; reverting is expiry alone (no writer).
    api_fallback: NotRequired[bool]
    api_fallback_until: NotRequired[float]  # epoch; window open while now < it
    api_fallback_since: NotRequired[float]  # when the current window opened
    cred_warned_at: NotRequired[str]        # §9.2 watcher: last credential-
                                            # expiry warning (≤1/day survives
                                            # restarts — redteam finding)
    deleted_cost_usd: NotRequired[float]    # tombstone burn accumulator (cost_total)
    api_cost_usd: NotRequired[float]        # lifetime burn billed to the key while
                                            # an api_fallback window was open — the
                                            # hover split on the UI cost card.
                                            # Org-level and monotonic: node deletion
                                            # never has to re-bank it.
    _actors_typed: NotRequired[bool]        # one-shot @-sentinel migration marker
    # ---- legacy keys old docs may still carry (popped/rewritten on load) ----
    default_dirs: NotRequired[list[Any]]    # superseded by `dirs` with modes
