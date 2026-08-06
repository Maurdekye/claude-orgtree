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
PermissionMode = Literal["default", "acceptEdits", "bypassPermissions"]  # PM_LEVELS
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
    # the transient/connection kind (user report 2026-08-06): a network drop
    # freezes with a short exponential until_ts; ▶/auto-resume own it like
    # `limit` (resume_frozen's owned-kinds exemption names both)
    connection: bool
    spend: bool
    spend_error: str | None
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
    pid: int | None
    ui_order: float
    scope: NodeScope
    # §8 lineage axis — second axis, never an org edge
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
    cli_compactions: NotRequired[int]
    # consecutive network-classified turn failures (user report 2026-08-06);
    # reset by any completed turn, capped at NET_RETRY_MAX then manual
    net_fail_run: NotRequired[int]
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
})


# One queued message in OrgDoc["mail"][<node>] (№11/№17: durable pending copy,
# retractable by id until delivery). Functional form: "from" is a keyword.
MailEntry = TypedDict("MailEntry", {
    "id": str,
    "from": str,
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
    cred_warned_at: NotRequired[str]        # §9.2 watcher: last credential-
                                            # expiry warning (≤1/day survives
                                            # restarts — redteam finding)
    deleted_cost_usd: NotRequired[float]    # tombstone burn accumulator (cost_total)
    _actors_typed: NotRequired[bool]        # one-shot @-sentinel migration marker
    # ---- legacy keys old docs may still carry (popped/rewritten on load) ----
    default_dirs: NotRequired[list[Any]]    # superseded by `dirs` with modes
