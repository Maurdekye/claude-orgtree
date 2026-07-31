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
export type BearerState = 'knowledge' | 'preserving' | null
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
  queued: number
  last_error: string | null
}

// ledger.py credit_requests / audience_requests / chain_notices are
// dict[str, Any] in schema.py — only `status` is provably read on requests
export interface CreditRequest {
  status?: string
  node?: string
  id?: string
  [k: string]: unknown
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
  max_tier: unknown            // (max_scope or {}).get("max_tier") — unproven
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
export interface TreeDisk {
  used_mb: number | null
  total_mb: number | null
  blocked: boolean
  full: boolean
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
  files: DiskFile[]
  offset: number
  limit: number
}

export interface DiskDeleteResult {
  results: { path: string; ok: boolean; error?: string }[]
  used: number | null
  total: number | null
  blocked: boolean
  full: boolean
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
  credit_requests: CreditRequest[]
  tiers: Record<string, number>
  audiences: AudienceGrant[]
  roots: TreeNode[]
  audit: AuditReport
  cost_usd_total: number
  user_inbox_count: number
  user_inbox_newest: string | null
  fable_lock: Record<string, unknown> | null
  spend_frozen: boolean
  storage_blocked: boolean
  /** the org's virtual disk (sandboxed, migrated orgs only) — the persistent
   *  hard-full alert and the storage chip render from this state */
  disk?: TreeDisk
  auto_resume: boolean
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
  kiosk_cfg?: KioskDashboard
}

// --------------------------------------------------------------------- chat
// supervisor.py read_chat: tool chip (correlated by tool_use_id)
export interface ToolChip {
  name: string
  arg?: unknown                // _tool_arg's return — shape varies per tool
  id?: string | null
  result?: string
  result_lines?: number
  truncated?: boolean
  error?: string
  images?: number
  file?: MailAttachment & { note?: string }   // orgtree_send_file download card
  mail?: { id: string; to: string }
  [k: string]: unknown
}

// supervisor.py read_chat message rows — several producers, all optional
// beyond role; `tools` interleaves nulls (plumbing markers for user records)
export interface ChatMessage {
  role: string
  text?: string
  ts?: string | null
  tools?: (ToolChip | null)[]
  cmd_out?: string
  summary?: string
  [k: string]: unknown
}

// supervisor.py read_chat st["init"] (system init record excerpt)
export interface ChatInit {
  model?: string | null
  permissionMode?: string | null
  cwd?: string | null
  [k: string]: unknown
}

// api.py node_chat: the durable pending-mail projection (parity №11)
export interface PendingMail {
  id: string | null
  from: string
  body: string
  at: string
  delivering?: boolean
  attachments?: MailAttachment[]
}

// GET /api/orgs/{slug}/nodes/{nid}/chat — read_chat + node_chat additions
export interface ChatPayload {
  busy: boolean
  queued: number
  responding: boolean
  last_error: string | null
  occupancy: number | null
  messages: ChatMessage[]
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
  warnings?: unknown[]
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
  default_tools?: ToolGrant
  default_visibility?: string
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
  auto_resume?: boolean | null
  cascade_hire?: boolean | null
  cascade_alloc?: boolean | null
}

// api.py HireDefaults (POST /api/orgs/{slug}/defaults)
export interface HireDefaultsRequest {
  default_tools?: Partial<ToolGrant> | null
  default_visibility?: string | null
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
  warnings?: string[]
  [k: string]: unknown
}

export interface UploadResult {
  path: string
  bytes: number
}
