// API payload types — the frontend's half of the seam (typing wave, docs/typing-plan.md).
//
// Derived from the BACKEND source, which is the ground truth:
//   - backend/orgtree/schema.py     (TypedDicts for the persisted org document)
//   - backend/orgtree/ledger.py     (Org.tree() — the projection the UI renders)
//   - backend/orgtree/api.py        (endpoint request/response shapes)
//   - backend/orgtree/supervisor.py (read_chat / state — the chat payload)
//
// Ground rules (mirror of schema.py's): describe what the code writes TODAY;
// where a field's type is not provable from the backend, use `unknown` or a
// wider union — never invent. Runtime-inert: types only, no values.

// ---------------------------------------------------------------- primitives
// schema.py: NodeState / DirMode / Visibility / PermissionMode / BearerState
export type NodeState = 'live' | 'archived' | 'unrecoverable'
export type DirMode = 'rw' | 'ro'
export type Visibility = 'self' | 'team' | 'subtree' | 'full'
export type PermissionMode = 'default' | 'acceptEdits' | 'bypassPermissions'
// "lost" = a generation whose transcript is gone (schema.py:34, ledger.py:2448)
export type BearerState = 'knowledge' | 'preserving' | 'lost' | null
export type Effort = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

// schema.py DirGrant
export interface DirGrant {
  path: string
  mode: DirMode
}

// schema.py ToolGrant — `mcp` is a sorted server-name list; ["*"] = all
export interface ToolGrant {
  bash: boolean
  web: boolean
  edit: boolean
  subagents: boolean
  mcp: string[]
}

// schema.py NodeScope — permission_mode / org_visibility are `str` there, so
// they stay `string` here (old docs may carry values outside today's literals)
export interface NodeScope {
  permission_mode: string
  add_dirs: DirGrant[]
  tools: ToolGrant
  org_visibility: string
  /** per-agent thinking effort — set/popped by ledger.py update_scope
   *  (ledger.py:2093-2096); absent = CLI default */
  effort?: string
  /** which model VERSION inside the tier (ledger.MODEL_VERSIONS) — a
   *  subcategory of the tier, never a tier of its own. Absent = the tier
   *  default; the ledger re-validates it against the node's current tier. */
  model_version?: string
}

// schema.py Denial (№7)
export interface Denial {
  tool: string
  arg?: string | null
}

// schema.py TurnStat (№15)
export interface TurnStat {
  at: string
  cost: number
  ms?: number | null
  denials: number
  /** killed-turn accounting (2026-08-04): output tokens, the kill marker,
   *  and whether the cost is derived rather than API-reported */
  toks?: number
  killed?: boolean
  estimated?: boolean
}

// schema.py AudienceGrant (§7.3)
export interface AudienceGrant {
  grantee: string
  grantor: string
  granted_at: string
  reason: string
}

// api.py node_message builds {name, path, bytes}; schema.py only proves
// list[dict[str, Any]] (extern attachments ride other paths) — all optional
export interface MailAttachment {
  name?: string
  path?: string
  bytes?: number
  [k: string]: unknown
}

// schema.py MailEntry (№11/№17)
export interface MailEntry {
  id: string
  from: string
  kind: string
  body: string
  at: string
  relationship?: string | null
  attachments?: MailAttachment[]
  delivering?: boolean
  retracted?: boolean          // api.py node_mail_retract mirrors into the log
  [k: string]: unknown
}

// schema.py OrgInboxEntry — the inter-org bridge log
export interface OrgInboxEntry {
  id: string
  dir: 'in' | 'out'
  peer: string
  body: string
  at: string
  by?: string
  // F-06: @net: outbound delivery ladder (net.py _stamp_row, monotonic)
  state?: 'queued' | 'sent' | 'delivered' | 'read'
  state_at?: string
  net_id?: string
  // §10: per-message delivery failure, copied off the spool entry when a
  // wire try fails (cleared when a later try lands)
  tries?: number
  last_err?: string
  attachments?: { name: string; bytes: number }[]
}

// F-06: net.py status_block — hub config + live connectivity (never secrets)
export interface NetPeer {
  slug: string
  org_name?: string | null
  username?: string | null
  blurb?: string | null
  online: boolean
  last_seen?: string | null
  kind?: 'org' | 'chat'        // FR-06: independent chats are clients too
  /** user spec 2026-08-05: which transports resolve this recipient —
   *  derived server-side from the same data the bare-name resolver uses */
  transports?: string[]
}
export interface NetHub {
  id: string
  address: string
  enabled: boolean
  name?: string | null         // discovered on connect, never typed
  connected: boolean
  /** the implicit local entry before the hub has EVER answered — passive
   *  surfaces (chip, dot, network section, compose roster) render nothing;
   *  the settings tab still shows it (it is config) */
  hidden?: boolean
  last_ok?: string | null
  error?: string | null
  queued: number
  // §10: how many queued entries have a recorded wire failure, + the
  // newest reason — "N stuck, last error: …" on the mailservers tab
  stuck?: number
  stuck_err?: string
  roster: NetPeer[]
}
export interface NetBlock {
  slug: string | null          // this org's network address
  hubs: NetHub[]
}

// ledger.py tree(): last_status/prev_status are dict[str, Any] | None in
// schema.py; orgtree_status (api.py) writes {status, summary, at} today
export interface NodeStatus {
  status?: string
  summary?: string
  at?: string
  [k: string]: unknown
}

// ledger.py tree(): the frozen projection — keys always present (built via
// .get), values nullable; `error` joins error + spend_error (№41)
export interface TreeFrozen {
  at: string | null
  until: string | null
  until_ts: number | null
  error: string | null
  /** the transient kind (2026-08-06): a network drop, not a usage limit —
   *  same resume machinery, different badge label */
  connection?: boolean | null
  /** D-122: present so the banner can tell a PURE connection freeze (always
   *  self-retries) from a record carrying both kinds (waits on the toggle) */
  limit?: boolean | null
}

// ledger.py tree(): one entry of the node's lineage stack (§8)
export interface LineageEntry {
  id: string
  generation: number
  state: NodeState
  bearer_state: BearerState
  tier: string
}

// ---------------------------------------------------------------- tree view
// ledger.py Org.tree() build() + api.py org_tree annotate(); _scrub_public
// pops session_id for kiosk visitors, hence optional
export interface TreeNode {
  id: string
  title: string
  tier: string
  model_id: string
  state: NodeState
  seat: number
  grant: number
  free: number | null
  session_id?: string
  scope: NodeScope
  ui_order: number
  cost_usd: number
  occupancy: number | null
  context_window: number | null
  charter: string | null
  team_charter?: string | null
  mail_pending: number
  limit_locked: boolean
  last_status: NodeStatus | null
  prev_status: NodeStatus | null
  inflight_at: string | null
  last_denials: Denial[]
  turns: TurnStat[]
  frozen: TreeFrozen | null
  audiences_held: string[]
  bearer_state: BearerState
  generation: number
  children: TreeNode[]
  lineage: LineageEntry[]
  // ---- api.py annotate() — live supervisor state layered on the projection
  busy: boolean
  waiting: boolean
  responding: boolean
  phase: string | null
  /** api_fallback: this node's IN-FLIGHT turn bills the org's own API key
   *  (captured at spawn, so it holds for the whole turn even once the window
   *  shuts). The card wears the fallback red while it is true. */
  on_fallback?: boolean
  queued: number
  /** concurrently running subagents (Task/Agent calls in flight) — desk
   *  header shows it beside the working clock, only when > 0 */
  tasks?: number
  last_error: string | null
  /** G4: what the agent is doing this instant, derived server-side from the
   *  live tail. The client used to build this itself from websocket events. */
  activity: ActivityInfo
  /** F-04/F-05: the ask card this node's desk shows — open, or freshly
   *  nulled (ledger.node_ask; null once the linger window passes) */
  ask?: AskInfo | null
  /** FR-03: presented documents (metadata; body via getDocument) */
  documents?: { id: string; title: string; at: string }[] | null
  /** FR-01: parked while the user drives this session from another device */
  remote_controlled?: { at?: string } | null
}

/** 'thinking' | 'writing' | 'tool' — a string rather than a union for the same
 *  reason every other wire enum here is: it arrives as JSON. */
export interface ActivityInfo { phase: string; tool?: string }

// ledger.py credit_requests / audience_requests are
// dict[str, Any] in schema.py — only `status` is provably read on requests
export interface CreditRequest {
  status?: string
  node?: string
  id?: string
  [k: string]: unknown
}

/** ledger.node_ask / tree.asks — one ask card (F-04 question or F-05 credit
 *  request), open or nulled. `status`: open|pending = live; answered/denied/
 *  dismissed/withdrawn/superseded = grey null; interrupted = orange null
 *  (historical — the wake-void was retired 2026-08-06; `reason` says why). */
export interface AskInfo {
  id: string
  node: string
  kind?: string              // "question" | "credit" (credit rows may omit it in credit_requests)
  status: string
  at: string
  resolved_at?: string
  reason?: string
  // question kind — options mirror AskUserQuestion ({label, description?});
  // header is the short chip label (ledger normalizes plain strings away)
  question?: string
  header?: string
  options?: { label: string; description?: string }[]
  multi?: boolean
  answer?: { selected?: string[]; text?: string }
  /** FR-04: the batch — 1-4 tabs; both ask forms normalize to this. The
   *  top-level question/options/header mirror tab 0 for older surfaces. */
  questions?: AskQuestion[]
  /** FR-14 (kind === 'batch'): the COMPOSED card — question tabs + the
   *  credits tab + one tab per scope item, resolved together at one submit.
   *  `revs` carries the per-store CAS stamps the submit must echo. */
  tabs?: AskTab[]
  revs?: { ask?: number; credits?: number; scope?: number }
  /** card revision — bumped on every amend; the answer echoes it (CAS: a
   *  stale submission is refused instead of attaching positionally to
   *  questions the user never saw) */
  rev?: number
  // credit kind (ledger credit_requests shape)
  old?: number
  new?: number
  granted?: number
  notice?: string
  /** scope kind (ledger scope_requests shape): the requested items, each
   *  stamped with its decision once the batch resolves */
  items?: { kind: string; path?: string; mode?: string; tool?: string
    server?: string; decision?: string }[]
  [k: string]: unknown
}

/** FR-04: one tab of a batched ask (ledger `_norm_question_batch`) */
export interface AskQuestion {
  question: string
  header?: string
  options?: { label: string; description?: string }[]
  multi?: boolean
  /** set once answered — the tab's answer (list for a multi tab) */
  answer?: string | string[]
}

/** FR-14: one tab of the composed batch card (ledger node_ask) */
export interface AskTab {
  kind: 'question' | 'credits' | 'scope'
  // question tabs carry AskQuestion's fields
  question?: string
  header?: string
  options?: ({ label: string; description?: string } | string)[]
  multi?: boolean
  // the credits tab
  id?: string
  old?: number
  new?: number
  reason?: string
  // scope tabs: one item each, pre-labeled server-side
  item?: { kind: string; path?: string; mode?: string; tool?: string
    server?: string }
  label?: string
}

/** FR-18: a watchdog — a free persistent pet (ledger `watchdogs`) */
export interface Watchdog {
  id: string
  owner: string
  name: string
  kind: 'file' | 'command' | 'process' | 'stream'
  target: string
  pattern?: string
  interval_s: number
  state: 'armed' | 'paused' | 'exited'
  at: string
  fired: number
  last_check?: string
  last_fired?: string
  events?: { at: string; gist: string }[]
  exit?: { code?: number | null; at?: string }
}

export type AudienceRequest = Record<string, unknown>

// api.py org_tree: the kiosk block (admin fields scrubbed for visitors)
export interface TreeKiosk {
  credits: number | null
  spend_limit: number | null
  storage_limit_mb: number | null
  spend_frozen: boolean
  storage_blocked: boolean
  max_scope?: Record<string, unknown> | null
  auto_raise?: boolean
  /** (max_scope or {}).get("max_tier") (api.py:593) — writes are validated
   *  against TIERS or None (ledger.py:495-502) */
  max_tier: string | null
  enabled: boolean
  sandbox: boolean
  share_url?: string | null
  storage_mb?: number
}

// ledger.py audit()
export interface AuditReport {
  live_nodes: number
  top_level_holds: number
  no_overdraft: boolean
  problems: string[]
}

// GET /api/orgs/{slug} — ledger.py tree() + api.py org_tree additions
/** the toast surface App threads everywhere: lines + an optional 12 s undo.
 *  Callers pass `r.warnings` straight through, so nullish is accepted — the
 *  implementation's `if (!lines || !lines.length) return` is the contract */
export type ToastUndo = (() => void) | { fn: () => void; label: string }
export type ToastFn = (
  lines: string[] | null | undefined, undo?: ToastUndo | null,
) => void

export interface TreeDisk {
  used_mb: number | null
  total_mb: number | null
  blocked: boolean
  full: boolean
  /** staged shrink target — the yellow divergence: requested vs actual */
  pending_mb?: number | null
}

export interface DiskFile {
  path: string
  bytes: number
  class: 'content' | 'reclaimable' | 'blocked'
  reason?: string
}

export interface DiskPayload {
  used: number | null
  total: number | null
  blocked: boolean
  full: boolean
  /** admin only; null = Docker Desktop's VM disk cap is UNSET on the host */
  vm_cap_mib?: number | null
  /** admin only: configured size + staged shrink target (null = none) */
  size_mb?: number
  pending_mb?: number | null
  files: DiskFile[]
  offset: number
  limit: number
}

export interface DiskDeleteResult {
  /** dir deletes report their subtree tally (files, bytes) */
  results: { path: string; ok: boolean; error?: string
             files?: number; bytes?: number }[]
  used: number | null
  total: number | null
  blocked: boolean
  full: boolean
}

/** explorer mode: one directory level, intermixed by size descending */
export interface DiskDirEntry {
  name: string
  path: string
  dir: boolean
  bytes: number
  files: number
  class: 'content' | 'reclaimable' | 'blocked'
  reason?: string
}

export interface DiskDirPayload {
  path: string
  entries: DiskDirEntry[]
  used: number | null
  total: number | null
  blocked: boolean
  full: boolean
  /** admin only; null = Docker Desktop's VM disk cap is UNSET on the host */
  vm_cap_mib?: number | null
  /** admin only: configured size + staged shrink target (null = none) */
  size_mb?: number
  pending_mb?: number | null
}

/** pre-migration backup accounting (admin sweep) */
export interface SweepPreview {
  volumes: string[]
  volumes_bytes: number
  host_dirs: string[]
  host_bytes: number
  total_bytes: number
}

export interface SweepResult {
  removed_volumes: string[]
  removed_dirs: string[]
  failures: string[]
}

export interface TreePayload {
  slug: string
  name: string
  workspace: string | null
  dirs: DirGrant[]
  max_top_grant: number
  default_top_grant: number
  compact_at: number
  default_tools: ToolGrant | null
  default_visibility: string
  /** the mode NEW hires are born with (D-100); existing nodes carry their own
   *  in `scope.permission_mode` and are changed one at a time in the ⚙ */
  permission_mode?: string
  /** org-wide effort fallback ("" = fall through to effort_default) — live
   *  inherit for unset nodes */
  default_effort: string
  /** what "" resolves to, so no UI string hardcodes it (ledger DEFAULT_EFFORT) */
  effort_default?: string
  credit_requests: CreditRequest[]
  tiers: Record<string, number>
  audiences: AudienceGrant[]
  roots: TreeNode[]
  audit: AuditReport
  cost_usd_total: number
  /** slice of cost_usd_total billed to the org's key while an api_fallback
   *  window was open — the cost chip's hover split */
  api_cost_usd_total?: number
  /** F-04: every ask card the inbox interleaves (open + recent resolved);
   *  the header ask-icon glows iff asks_open > 0 */
  asks?: AskInfo[]
  asks_open?: number
  user_inbox_count: number
  user_inbox_newest: string | null
  fable_lock: Record<string, unknown> | null
  spend_frozen: boolean
  storage_blocked: boolean
  /** the org's virtual disk (sandboxed, migrated orgs only) — the persistent
   *  hard-full alert and the storage chip render from this state */
  disk?: TreeDisk
  /** FR-18: the org's watchdogs (canvas satellites + detail panels) */
  watchdogs?: Watchdog[]
  /** FR-24b: org-level auto-cheap-compact config (null/absent = off) */
  auto_cheap_compact?: { enabled?: boolean; occ?: number; idle_s?: number } | null
  auto_resume: boolean
  /** cheap-compact a limit-frozen node right before its AUTO resume */
  auto_resume_compact?: boolean
  fable_limit_policy: string
  fable_filter_policy: string
  cascade_hire: boolean
  cascade_alloc: boolean
  sandboxed: boolean
  audience_requests: AudienceRequest[]
  org_inbox: {
    entries: OrgInboxEntry[]
    unread: number
    holders: string[]          // ledger.py extern_holders() -> list[str]
    visible: boolean
  }
  kiosk?: TreeKiosk            // only when the org is a kiosk
  public?: boolean             // only through the public gateway
  net?: NetBlock | null        // F-06 (null for kiosks; absent for visitors)
  headless?: boolean           // §9.6
  api_key_set?: boolean        // §9.5: whether, never the key itself
  /** 2026-08-17: the key is a usage-limit SPARE (subscription-first) */
  api_fallback?: boolean
  /** epoch seconds; the fallback window is open while now < this */
  api_fallback_until?: number | null
}

// ----------------------------------------------------------------- org list
// api.py orgs_list admin branch attaches kiosk_cfg
export interface KioskDashboard {
  enabled: boolean
  token: string | null
  credits: number
  spend_limit: number
  storage_limit_mb: number
  spend_frozen: boolean
  storage_blocked: boolean
  sandbox: boolean
  held: number
  storage_mb: number | null
  share_url: string | null
}

// GET /api/orgs — store.list_orgs() rows + api.py orgs_list decoration.
// cost_usd_total/kiosk_cfg are admin-only and skipped on a LedgerError row.
export interface OrgListEntry {
  slug: string
  name: string
  nodes: number
  live: number
  kiosk: boolean
  created: string | null
  cost_usd_total?: number
  working?: number             // F-09: agents with a running turn (admin list only)
  kiosk_cfg?: KioskDashboard
}

// --------------------------------------------------------------------- chat
// supervisor.py read_chat: tool chip (correlated by tool_use_id)
export interface ToolChip {
  name: string
  arg?: string                 // _tool_arg (supervisor.py:2611) -> str
  id?: string | null
  result?: string
  result_lines?: number
  truncated?: boolean
  error?: string
  images?: number
  file?: MailAttachment & { note?: string }   // orgtree_send_file download card
  mail?: { id: string; to: string }
  /** Edit chips: the pre-computed structuredPatch (supervisor.py:2912-2915) */
  diff?: { plus: number; minus: number; lines: string[]; truncated?: boolean }
  /** Task/subagent sidecar totals (supervisor.py:2917-2920) — tur.get() may
   *  hand back null for any of them */
  task?: { tools?: number | null; ms?: number | null; tokens?: number | null }
  [k: string]: unknown
}

// supervisor.py read_chat message rows — several producers, optional beyond
// role/text (every producer writes `text`, supervisor.py:2743-2972); `tools`
// interleaves nulls (plumbing markers for user records)
export interface ChatMessage {
  role: string
  text: string
  ts?: string | null
  /** null plumbing markers are swept SERVER-side before the payload leaves
   *  (supervisor.py read_chat: `[x for x in tools if x]`) — never null here */
  tools?: ToolChip[]
  cmd_out?: string
  summary?: string
  /** pre-slice ordinal — the UI's stable row key (supervisor.py:2963) */
  seq?: number
  /** thinking blocks, joined + capped (supervisor.py:2938) */
  thinking?: string
  /** "thought for Xs" — gap-derived seconds (supervisor.py:2941-2943) */
  think_secs?: number
  /** the block came signature-only: it thought, the plaintext was withheld */
  thinking_sealed?: boolean
  /** preserving-oracle exchange rows (supervisor.py:2969-2973) */
  oracle?: boolean
  /** interleaved from the durable steered log (supervisor.py:2951) */
  steered?: boolean
  /** the DISPLAY copy was capped (steered-log per-row cap); the delivery
   *  itself was whole — the desk renders a marker saying so */
  truncated?: boolean
  [k: string]: unknown
}

// supervisor.py read_chat st["init"] (system init record excerpt,
// supervisor.py:1188-1194)
export interface ChatInit {
  model?: string | null
  permissionMode?: string | null
  cwd?: string | null
  /** len(ev["tools"]) — the tool COUNT, not a list (supervisor.py:1192) */
  tools?: number
  /** CLI stream-json passthrough (supervisor.py:1193) — shape is the CLI's */
  mcp_servers?: { name?: string; status?: string; [k: string]: unknown }[]
  [k: string]: unknown
}

// api.py node_chat: the durable pending-mail projection (parity №11)
export interface PendingMail {
  id: string | null
  from: string
  body: string
  at: string
  delivering?: boolean
  /** which carrier is in flight: "turn" = drained into the turn's own text,
   *  so the transcript will take over as soon as the CLI echoes it (D-54).
   *  Absent = steered mid-task, which the transcript never carries. */
  via?: 'turn'
  attachments?: MailAttachment[]
}

// GET /api/orgs/{slug}/nodes/{nid}/chat — read_chat + node_chat additions
/** one row of ChatPayload.live — the shape supervisor.live_row records */
export interface LiveRowPayload {
  kind: string
  text?: string
  id?: string
  secs?: number
  sticky?: boolean
  at?: string
  /** per-node monotonic row id — the render key (see LiveRow.n) */
  n?: number
  /** the live copy was capped at emit time; the durable twin carries it whole */
  truncated?: boolean
}

export interface ChatPayload {
  busy: boolean
  queued: number
  responding: boolean
  last_error: string | null
  occupancy: number | null
  messages: ChatMessage[]
  /** the server-owned live tail: rows this turn produced that the transcript
   *  has not caught up on yet, already swept against it server-side
   *  (supervisor._sweep_live). The client renders these; it does not build
   *  or retire them. */
  live?: LiveRowPayload[]
  init?: ChatInit | null
  mail_pending: number
  pending_mail: PendingMail[]
}

// ------------------------------------------------------------------ inboxes
export type SentMailEntry = MailEntry & { to?: string }

// GET /api/orgs/{slug}/inbox and .../nodes/{nid}/inbox — same shape by user
// ruling (api.py user_inbox docstring)
export interface InboxPayload {
  pending: MailEntry[]
  delivered: MailEntry[]
  sent: SentMailEntry[]
}

// ------------------------------------------------------------------- events
// ledger.py _log via api.py org_events / node_history — detail is open
export interface OrgEvent {
  at: string
  op: string
  actor: string
  detail?: Record<string, unknown>
  warnings?: string[]          // ledger.py _log (№1236-1241): list[str]
  [k: string]: unknown
}

// GET /api/orgs/{slug}/events
export interface EventsPayload {
  total: number
  events: OrgEvent[]
}

// GET /api/orgs/{slug}/nodes/{nid}/history — api.py node_history items
export interface HistoryItem {
  at: string
  kind: string
  actor: string
  detail: Record<string, string | number | string[]>
  warnings?: string[]          // absent on "notice" rows
}

export interface HistoryPayload {
  items: HistoryItem[]
}

// ------------------------------------------------------------ small payloads
// GET /api/orgs/{slug}/nodes/{nid}/scratch — dir listing or file content
export interface ScratchDirEntry {
  name: string
  dir: boolean
  size: number | null
}

export type ScratchPayload =
  | { dir: string; entries: ScratchDirEntry[] }
  | { file: string; content: string }

// GET /api/fs — api.py fs_list (roots listing carries `home`)
export interface FsPayload {
  path: string
  parent: string | null
  dirs: { name: string; path: string }[]
  home?: string
}

// GET /api/charters
export interface ChartersPayload {
  charters: { name: string; content: string; path: string }[]
}

// GET /api/mcp-servers
export interface McpServersPayload {
  servers: string[]
  sandbox_mcp: boolean
}

// GET /api/host
export interface HostPayload {
  docker: boolean
  sandbox_mcp: boolean
  cli_version: string
}

/** one bar of the host subscription's rate-limit standing (GET /api/usage —
 *  the same readout Claude Code shows under /usage). `model` is the display
 *  name on scoped limits ("Fable"); null on the account-wide ones. */
export interface UsageLimit {
  kind: string
  group: string
  percent: number | null
  severity: string | null
  resets_at: string | null
  is_active: boolean
  model: string | null
}

export interface UsagePayload {
  available: boolean
  error?: string
  limits?: UsageLimit[]
  plan?: string
}

/** GET /api/usage/peek — the same standing read from the server's cache
 *  ALONE, so the header button's near-the-wall glow can poll continuously
 *  without ever costing an upstream request. `available: false` means "do not
 *  glow": no readout yet, no subscription on this host, or one too old to be
 *  a claim about now (the modal still shows those bars, dated). */
export interface UsagePeek {
  available: boolean
  error?: string
  limits?: UsageLimit[]
  /** seconds since the cached readout was fetched */
  age?: number
}

// GET /api/defaults — _DEFAULTS_BASE merged with the stored overrides
export interface DefaultsPayload {
  max_top_grant: number
  default_top_grant: number
  compact_at: number
  fable_limit_policy: string
  fable_filter_policy: string
  cascade_hire: boolean
  cascade_alloc: boolean
  auto_resume: boolean
  auto_resume_compact?: boolean
  default_tools?: ToolGrant
  default_visibility?: string
  default_effort?: string
  [k: string]: unknown         // defaults.json is stored org-doc-shaped
}

// GET /api/orgs/{slug}/orgmd
export interface OrgMdPayload {
  path: string | null
  content: string
}

// GET /api/orgs/{slug}/audiences
export interface AudiencesPayload {
  audiences: AudienceGrant[]
  requests: AudienceRequest[]
}

// ---------------------------------------------------------------- requests
// api.py KioskSpec (all fields have server defaults → optional here)
export interface KioskSpecRequest {
  credits?: number
  spend_limit?: number
  storage_limit_mb?: number
  sandbox?: boolean
  max_scope?: Record<string, unknown> | null
  auto_raise?: boolean
}

// api.py Op — the ledger op envelope (POST /api/orgs/{slug}/ops)
export interface OpRequest {
  op: string
  actor?: string
  node?: string | null
  parent?: string | null
  tier?: string | null
  grant?: number | null
  name?: string | null
  charter?: string | null
  add_dirs?: unknown[] | null  // [{path, mode}] or bare paths (api.py: list)
  tools?: Partial<ToolGrant> | null
  org_visibility?: string | null
  effort?: string | null
  /** hire — FR-25 insert superior: the anchor node; the server splices the
   *  fresh hire in as its superior atomically (same save as the hire) */
  above?: string | null
  delta?: number | null
  new_parent?: string | null
  dir?: string | null
  raise_ceiling?: boolean
}

// api.py Scope (POST .../nodes/{nid}/scope)
export interface ScopeRequest {
  add_dirs?: DirGrant[] | null
  tools?: Partial<ToolGrant> | null
  org_visibility?: string | null
  permission_mode?: string | null
  charter?: string | null
  team_charter?: string | null
  effort?: string | null
  model_version?: string | null
  /** FR-24b per-node override; {} clears back to org inherit */
  auto_cheap_compact?: { enabled?: boolean; occ?: number; idle_s?: number } | null
  raise_ceiling?: boolean
}

// api.py Settings — shared by /api/defaults and /api/orgs/{slug}/settings
export interface SettingsRequest {
  org_dirs?: DirGrant[] | null
  max_top_grant?: number | null
  default_top_grant?: number | null
  compact_at?: number | null   // percent, 50..95
  clear_fable_lock?: boolean
  fable_limit_policy?: string | null
  fable_filter_policy?: string | null
  default_tools?: Partial<ToolGrant> | null
  default_visibility?: string | null
  /** D-100 — the mode NEW hires are born with; admin-only (this endpoint is
   *  frozen for kiosk visitors, unlike /defaults) */
  permission_mode?: string | null
  default_effort?: string | null
  auto_resume?: boolean | null
  /** cheap-compact a limit-frozen node right before its AUTO resume wakes it */
  auto_resume_compact?: boolean | null
  /** FR-24b: auto cheap-compact on wake (org level; disabled by default) */
  auto_cheap_compact?: { enabled?: boolean; occ?: number; idle_s?: number } | null
  cascade_hire?: boolean | null
  cascade_alloc?: boolean | null
  // F-06
  net_hub_address?: string | null      // global defaults only
  net_autoconnect?: boolean | null     // per-org: keep/join the local hub
  net_hubs?: { id?: string; address: string; enabled?: boolean }[] | null
  headless?: boolean | null            // §9.6 (server enforces the couplings)
  api_key?: string | null              // §9.5 (write-only)
  clear_api_key?: boolean
  /** the key as a usage-limit SPARE — server enforces the couplings */
  api_fallback?: boolean | null
}

// F-06: GET /api/orgs/{slug}/net — loopback-admin reveal (the ONE place the
// secret is returned)
export interface OrgNetReveal {
  identity: { secret: string; fingerprint: string; slug: string;
              minted_at: string } | null
  hubs: { id: string; address: string; enabled: boolean;
          name?: string | null }[]
  autoconnect: boolean
}

// api.py HireDefaults (POST /api/orgs/{slug}/defaults)
export interface HireDefaultsRequest {
  default_tools?: Partial<ToolGrant> | null
  default_visibility?: string | null
  default_effort?: string | null
  raise_ceiling?: boolean
}

// api.py KioskCfg (POST /api/orgs/{slug}/kiosk)
export interface KioskCfgRequest {
  enabled?: boolean | null
  credits?: number | null
  spend_limit?: number | null
  storage_limit_mb?: number | null
  rotate_token?: boolean
  max_scope?: Record<string, unknown> | null
  auto_raise?: boolean | null
}

// api.py Reorder (POST .../nodes/{nid}/reorder)
export interface ReorderRequest {
  before?: string | null
  after?: string | null
}

// ---------------------------------------------------------------- responses
// Ledger op results are op-specific dicts (hire → {node, ...}, dissolve →
// {freed, nodes}, …) — only `warnings` is a cross-op convention
export interface OpResult {
  warnings?: string[]
  /** the one-action kiosk-ceiling bridge (ledger.py:714-715): present when
   *  something was clamped and re-sending with raise_ceiling would fit it */
  bridge?: { raise_ceiling?: boolean }
  /** D-106: agents whose permissions this grant raised on its way down the
   *  chain (ledger.set_scope). The ledger is the authority — the panel's
   *  pre-save preview is a courtesy, this is what actually happened. */
  cascaded?: string[]
  [k: string]: unknown
}

// POST .../settings
export interface SettingsResult {
  dirs: DirGrant[]
  warnings: string[]
}

// POST .../kiosk
export interface KioskSaveResult {
  kiosk: Record<string, unknown>
  share_url: string | null
  freezes_cleared: string[]
  warnings?: string[]
}

// POST .../nodes/{nid}/message — several branches (api.py node_message):
// mail accept, /compact start, immediate command, or send_message's result
export interface SendMessageResult {
  accepted?: boolean
  deferred?: boolean | string
  queued?: number
  frozen?: boolean
  compacting?: boolean
  command?: boolean
  immediate?: boolean
  /** delivered mid-task via the steering hook (supervisor.py:1643) */
  steering?: boolean
  warnings?: string[]
  [k: string]: unknown
}

export interface UploadResult {
  path: string
  bytes: number
}
