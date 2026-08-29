// canvas/shared.ts — the split Canvas's leaf module: the canvas view types,
// world-space geometry constants and helpers (layout/flatten/springs math),
// the chat-markdown pipeline (md), and the small shared hooks. Helpers and
// types only — this file imports no components. Extracted verbatim from
// Canvas.tsx in the phase-3 split; all comments ride with their code.

import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { useEffect, useRef, useState } from 'react'
// side effect: the document-level "click a markdown image → full-size viewer"
// listener — loaded here because every .md surface renders through this module
import './lightbox'
import { onLiveBump } from '../livebus'
import type { DependencyList } from 'react'
import type {
  ActivityInfo, AskInfo, DirGrant, MailEntry, NodeState, NodeStatus,
  OpRequest, OpResult, ToolGrant, TreeNode, TreePayload,
} from '../types'

// One display alphabet for every provider-backed tier. Keeping the Codex rows
// out of this shared map made every generic card/header caller fall through to
// `?` even though the hire sheet had a separate Codex-only map.
export const TIER_LETTER: Record<string, string> = {
  haiku: 'H', sonnet: 'S', opus: 'O', fable: 'F',
  luna: 'L', terra: 'T', sol: 'S',
  // flash shares F with fable by the same accepted collision as sol/sonnet's
  // S — the chip class carries the family
  flash: 'F', pro: 'P',
}
export const TIERS = ['haiku', 'sonnet', 'opus', 'fable']
/** seat cost per tier — mirrors ledger.TIERS. One table, four tiers; the
 *  frontend had four copies of this before. */
export const TIER_SEAT: Record<string, number> =
  { haiku: 1, sonnet: 2, opus: 5, fable: 10 }
/** Model VERSIONS inside a tier — mirrors ledger.MODEL_VERSIONS. A version is
 *  a subcategory of the tier (user ruling 2026-08-04): it never changes the
 *  seat cost and never appears as a chip, only in the gear. A tier absent
 *  here, or present with one entry, offers no choice. */
export const MODEL_VERSIONS: Record<string, string[]> = { opus: ['5', '4.8'] }
/** The codex family (FR-15 preview) — ChatGPT/OpenAI tiers, GPT-5.6. A
 *  SEPARATE list, never merged into TIERS: every existing surface iterates
 *  TIERS, and a family that cannot be hired yet must not grow chips there by
 *  accident. Mirrors backend providers.py (CODEX_TIERS / CODEX_MODELS) the
 *  same way TIER_SEAT mirrors ledger.TIERS. Seat costs RULED 2026-08-28:
 *  API $ per M input tokens at the STANDING price (sol $5 standard, not the
 *  promo $4; luna $0.20 floors to 1) — display-only until codex hire lands. */
export const CODEX_TIERS = ['luna', 'terra', 'sol']
export const CODEX_TIER_LETTER: Record<string, string> = { luna: 'L', terra: 'T', sol: 'S' }
export const CODEX_TIER_SEAT: Record<string, number> = { luna: 1, terra: 2, sol: 5 }
/** The gemini family (D-189) — Google tiers: Gemini Flash (3.5-flash at
 *  launch, 3.7 via the version menu when it reaches the API) and Gemini Pro
 *  (3.1-pro). Same separate-list rule as the codex family. Seats by the
 *  standing rule: flash $1.50 → 1 (and still 1 at 3.7's $0.38), pro $2 → 2
 *  (the >200K long-context surcharge never sets a seat). */
export const GEMINI_TIERS = ['flash', 'pro']
export const GEMINI_TIER_LETTER: Record<string, string> = { flash: 'F', pro: 'P' }
export const GEMINI_TIER_SEAT: Record<string, number> = { flash: 1, pro: 2 }
/** Provider-neutral surfaces (for example the live-agent summary) use this;
 * provider-specific controls keep using their family list. */
export const ALL_TIERS = [...TIERS, ...CODEX_TIERS, ...GEMINI_TIERS]

// ---------------------------------------------------------------- view types
// The canvas overlays the payload's TreeNode with synthetic cards — the eye
// root, the draft card, live lineage bearers — plus flatten()'s plumbing.
// One structural type covers every card; fields absent on some card kinds
// are optional and consumers guard (or assert) exactly where the JS did.
export interface CanvasNode {
  id: string
  state: NodeState | 'draft' | 'user'
  /** null only on the eye root */
  tier: string | null
  children: CanvasNode[]
  title?: string
  /** set by flatten(): the parent card's id (null on the eye root) */
  parent?: string | null
  /** lineage pseudo-cards: the successor node this bearer floats beside */
  isBearerOf?: string
  bearerIndex?: number
  // --- the TreeNode surface the canvas reads (synthetic cards carry a subset)
  seat?: number
  grant?: number
  free?: number | null
  model_id?: string
  scope?: CanvasScope
  /** what a turn would ACTUALLY launch with: scope.effort, else the org
   *  default, else "" (no --effort flag). Derived server-side so the control
   *  cannot disagree with the runtime — ledger.Org.effective_effort */
  effort_effective?: string
  cost_usd?: number
  occupancy?: number | null
  /** …and it was estimated, not measured (post-compaction, pre-next-turn) */
  occupancy_est?: boolean
  /** the session holds only its own summary until the next turn — no compact
   *  button, because the endpoint refuses it */
  compacted_unrun?: boolean
  context_window?: number | null
  charter?: string | null
  team_charter?: string | null
  mail_pending?: number
  limit_locked?: boolean
  last_status?: NodeStatus | null
  prev_status?: NodeStatus | null
  inflight_at?: string | null
  last_denials?: TreeNode['last_denials']
  turns?: TreeNode['turns']
  frozen?: TreeNode['frozen']
  audiences_held?: string[]
  bearer_state?: TreeNode['bearer_state']
  generation?: number
  lineage?: TreeNode['lineage']
  busy?: boolean
  /** G4: server-derived, from the supervisor's live tail (absent on the
   *  synthetic cards — eye root, draft, bearers — which never run turns) */
  activity?: ActivityInfo
  /** F-04/F-05: the ask card this node shows (ledger.node_ask) */
  ask?: AskInfo | null
  /** FR-03: presented documents — metadata only; the reader fetches the
   *  body on open */
  documents?: { id: string; title: string; at: string }[] | null
  /** FR-01: parked while the user drives this session from another device */
  remote_controlled?: { at?: string } | null
  waiting?: boolean
  responding?: boolean
  phase?: string | null
  /** api_fallback: this node's in-flight turn is billing the org's own API
   *  key (absent on the synthetic cards, which never run turns) */
  on_fallback?: boolean
  /** which account actually served the last turn (resolved at spawn) */
  ran_as?: string | null
  /** "fallback 2 · <uuid>" when that account is a fallback row, else null */
  ran_as_label?: string | null
  queued?: number
  /** concurrently running subagents (Task/Agent calls in flight) — desk
   *  header shows it beside the working clock, only when > 0 */
  tasks?: number
  last_error?: string | null
}

/** a bearer pseudo-card carries a stub scope ({tools:{}, add_dirs:[]}),
 *  so the canvas-side scope is NodeScope with everything but the two
 *  always-present lists optional */
export interface CanvasScope {
  add_dirs: DirGrant[]
  tools: Partial<ToolGrant>
  permission_mode?: string
  org_visibility?: string
  effort?: string
  /** the model VERSION pinned inside the tier — a gear-only subcategory,
   *  never a chip (ledger.MODEL_VERSIONS). Absent = the tier's latest. */
  model_version?: string
}

/** the app-level event feeds OrgCanvas rides (produced by App's WS handler) */
export interface PulseEvent { node: string; event: string; t: number }
export interface StreamEvent {
  node: string
  /** 'delta' | 'thinking' | 'thinking_start' | 'text' | 'tool' (supervisor.py
   *  stream()) + 'steered' (api.py) — open: built from untyped WS JSON.
   *  'thinking_start' carries no text: it marks the block opening, which is
   *  the only signal that survives when the reasoning is sealed. */
  kind: string
  text: string
  sticky?: boolean
  t: number
  /** tool_use_id on a 'tool' event — see LiveRow.id */
  id?: string
}
export interface MailEvent { from: string; to: string; t: number }
// ActivityInfo moved to types.ts with G4 — it is a TREE PAYLOAD field now, not
// a client-side accumulation, and types.ts may not import from here (this file
// imports from it). Re-exported so existing importers are untouched.
export type { ActivityInfo } from '../types'
export type OpFn = (body: OpRequest) => Promise<OpResult>
/** a chat chip's mail pointer — routed to whichever box holds the mail */
export type MailLinkFn = (
  m: { id?: string | null; to?: string | null } | null | undefined,
) => void
/** the webmail row: MailEntry plus the decorations MailList's callers add */
export type MailRow = MailEntry & {
  to?: string                   // outgoing rows
  _wait?: boolean               // decorated inside MailList (pending group)
  _wait0?: boolean              // org-inbox pre-split unread flag
  _by?: string                  // org-inbox outbound attribution
  /** an ASK riding the inbox as its own mail row (user ruling 2026-08-04):
   *  the reading pane renders the response UI as the body instead of the
   *  reply UI. Never sent to markRead — it is not a real mail id. */
  _ask?: AskInfo
  /** F-06: @net: outbound delivery state (org-inbox out rows) */
  _state?: 'queued' | 'sent' | 'delivered' | 'read'
  _state_at?: string
  /** §10: wire-failure note copied off the spool entry (queued rows only) */
  _tries?: number
  _err?: string
}

// world-space geometry primitives
export interface Pt { x: number; y: number }
export interface Spring extends Pt { vx: number; vy: number }
export interface View { x: number; y: number; z: number }
export interface DraftState {
  parent: string | null
  tier: string
  /** F-03 side hire: the draft is a SIBLING placed to `side` of `anchor` —
   *  the hire lands under the same superior, and after birth a reorder pins
   *  the chosen ordering (left = before, right = after). */
  beside?: { anchor: string; side: 'left' | 'right' }
  /** FR-25 insert superior (reworked 2026-08-19): the draft WRAPS `anchor`
   *  in the preview tree — it takes the anchor's own slot with the anchor
   *  hanging beneath it (dashed edges above AND below), so the preview IS the
   *  post-splice shape and confirming causes no reflow. Nothing real changes
   *  until confirm, when the hire op carries `above` and the SERVER splices
   *  atomically (hire + ordinal pin + move in one save). `move()` is
   *  budget-neutral, so the fresh hire never needs spare grant to absorb the
   *  anchor. */
  above?: { anchor: string }
}
/** the staged pre-hire permissions (DraftScopeModal → confirmDraft); also
 *  the shape of the would-inherit prefill, whose tools may lack mcp */
export interface DraftScope {
  add_dirs: DirGrant[]
  tools: Partial<ToolGrant>
  org_visibility: string
  effort?: string
}
export interface Pile {
  key: string
  parent: string
  kind: 'a' | 'c'
  list: string[]
  front: string
}
/** a live-feed row: a StreamEvent copy or a folded thought line */
export interface LiveRow {
  kind: string
  text: string
  secs?: number
  sticky?: boolean
  _at?: number
  node?: string
  t?: number
  /** the CLI's tool_use_id on a 'tool' row — identity, so reconciliation
   *  against the transcript's chip does not have to compare rendered strings */
  id?: string
  /** the server's per-node monotonic row id — the RENDER key. An index key
   *  renames every row below one that retires from the middle (or falls off
   *  the head), remounting them; `n` names the row itself. */
  n?: number
  /** the live copy was capped at emit time; the durable twin carries it whole */
  truncated?: boolean
}

// the canvas exposes its inverse-zoom to CSS; React's CSSProperties has no
// custom-property indexer, so declare the one we use (type-only)
declare module 'react' {
  interface CSSProperties {
    '--invz'?: string
    /** the UNCLAMPED inverse zoom — the hire chips' counter-scale */
    '--invzf'?: string
  }
}
// dev/demo hook (see the launchSpark effect)
declare global {
  interface Window { __spark?: (from: string, to: string) => void }
}

// world-space geometry (px at zoom 1). Cards are SQUARE (design ruling) and never
// change size — the desk chat fades in OVER the card; you zoom to read it.
export const NODE_W = 124, NODE_H = 124
export const USER_W = 124, USER_H = 124   // the eye is a peer square (user ruling)
const SX = 186, SY = 200, PAD = 90
export const Z_MAX = 12       // enough for one desk to FILL the screen (124px card ≥ ~1450px)
// LOD thresholds on zoom
export const Z_MINI = 0.55
export const Z_DESK = 2.1
// dampened spring (underdamped → gentle elastic overshoot)
export const SPRING_K = 170, SPRING_C = 15

export const DRAFT = '__draft__'
export const USER = '@user'   // actor sentinel — never collides with a node named "user"
export const EXTERN = '@extern'      // the org-inbox audience grantor sentinel
/** the ledger's own hand — mirrors ledger.SYSTEM. Mail wearing this `from`
 *  was generated by the machine rather than by any agent, which is what the
 *  de-emphasised notice row keys on (user, 2026-08-28). */
export const SYSTEM = '@system'

/** D-173's SHORTER-ROW predicate, in one place because three renderings now
 *  key on it: the row class, the suppressed preview line, and the pile below.
 *
 *  ⚠ `kind` AND `from`, never either alone. `@system` also sends the user
 *  `kind: "decision"` mail — a Fable limit exhausted, agents halted or whole
 *  subtrees dissolved — and that is the mail they most need to see. Widening
 *  this to "notice OR from @system" would de-emphasise it; widening it to
 *  "any notice" would de-emphasise an AGENT's notice, which in a node mailbox
 *  is the ordinary traffic. This is NOT the read-on-arrival predicate, which
 *  is the kind alone and lives server-side (`Org.to_user_inbox`). */
export const isSystemNotice = (m: MailRow): boolean =>
  !m._ask && m.kind === 'notice' && m.from === SYSTEM

/** Fold each RUN of consecutive system notices into one group, leaving every
 *  other row a group of one (user, 2026-08-28: "if there are multiple
 *  consecutive system notices in a row, collapse them all into a single mail
 *  entry"). Rows in, groups out, order preserved — the caller renders group[0]
 *  as the row and the whole group in the reading pane.
 *
 *  CONSECUTIVE MEANS ADJACENCY IN THE LIST SHOWN, AND NOTHING ELSE. No time
 *  bound: two system notices a day apart with nothing between them are one
 *  entry, because the user described a position ("in a row") and not a
 *  recency, and a time window would make the same two rows fold or not fold
 *  depending on when you looked. ANY row that is not itself a foldable system
 *  notice breaks the run — read or unread, ordinary mail or a @system
 *  DECISION. That is the whole safety property: nothing but a system notice
 *  can ever end up inside a row labelled "N notices".
 *
 *  ⚠ A row still AWAITING delivery is never folded in. `_wait` carries an
 *  unread highlight and, in a node mailbox, a retract button; both are things
 *  to act on, and burying an action inside a summary is the failure this rule
 *  exists to avoid. In the user's own mailbox this clause is quiet by
 *  construction — D-173 lands notices already read — but MailList is the one
 *  mail interface everywhere, and the node and org inboxes do carry pending
 *  rows.
 *
 *  This is a DISPLAY fold and deliberately nothing else: every entry survives
 *  in the record, `user_inbox` membership still means unread (D-173), and
 *  collapsing at write time would destroy what happened for the sake of how
 *  it looks. */
export const pileNotices = (rows: MailRow[]): MailRow[][] => {
  const out: MailRow[][] = []
  for (const m of rows) {
    const run = out[out.length - 1]
    const foldable = isSystemNotice(m) && !m._wait
    if (run && foldable && isSystemNotice(run[0]!) && !run[0]!._wait) run.push(m)
    else out.push([m])
  }
  return out
}

export const INBOX = '__orginbox__'  // the org-inbox panel's layout id
export const INBOX_H = 64
// the eye's fixed world x (see layout()): generous enough that even a very
// wide left subtree (~32 leaf columns) never crosses into negative space
export const EYE_ANCHOR_X = 6000

// The desk's counter-scale, and the reader's text-size dial over it. Keep this
// the ONE definition: the factor used to live both here-ish (cards.tsx) and in
// .desk-inner's transform, which is one equation with no slack —
// 900 × 0.13333 = 120 = NODE_H − 2×inset.
export const DESK_SCALE = 0.13333
export const DESK_DPI_KEY = 'orgtree-desk-dpi'
// device preference (screen-dependent), so localStorage — never the org doc
export const deskDpi = (): number => {
  try {
    const v = parseFloat(localStorage.getItem(DESK_DPI_KEY) || '1')
    return Number.isFinite(v) && v >= 0.5 && v <= 3 ? v : 1
  } catch { return 1 }
}
export const setDeskDpi = (v: number) => {
  try { localStorage.setItem(DESK_DPI_KEY, String(v)) } catch { /* private mode */ }
  document.documentElement.style.setProperty('--desk-dpi', String(v))
}

export function withDraftTree(tree: TreePayload, draft: DraftState | null): CanvasNode {
  const draftNode = (): CanvasNode => ({
    id: DRAFT, title: '', tier: draft!.tier, state: 'draft', children: [],
    seat: 0, grant: 0, free: 0,
  })
  // a side-hire draft (F-03) sits ADJACENT to its anchor sibling, so the form
  // previews the ordering the hire will pin; an insert-superior draft (FR-25)
  // WRAPS its anchor — the draft takes the anchor's slot and the anchor hangs
  // beneath it, previewing the exact post-splice shape without touching the
  // real structure; a plain draft appends at the end
  const place = (kids: CanvasNode[]): CanvasNode[] => {
    const a = draft!.above
    if (a) {
      const i = kids.findIndex((k) => k.id === a.anchor)
      if (i < 0) return [...kids, draftNode()]
      const d = draftNode()
      d.children = [kids[i]!]
      return [...kids.slice(0, i), d, ...kids.slice(i + 1)]
    }
    const b = draft!.beside
    const i = b ? kids.findIndex((k) => k.id === b.anchor) : -1
    if (i < 0) return [...kids, draftNode()]
    const at = b!.side === 'right' ? i + 1 : i     // nUIA: i ≥ 0 ⇒ b matched
    return [...kids.slice(0, at), draftNode(), ...kids.slice(at)]
  }
  const mk = (n: TreeNode): CanvasNode => ({
    ...n,
    children: draft && draft.parent === n.id
      ? place(n.children.map(mk)) : n.children.map(mk),
  })
  return {
    id: USER, title: 'you', tier: null, state: 'user',
    children: draft && draft.parent === null
      ? place(tree.roots.map(mk)) : tree.roots.map(mk),
  }
}

/** api_fallback (2026-08-17): is this org billing its own API key RIGHT NOW?
 *  The server ships the option plus the window edge and leaves "active" to the
 *  client's own clock (ledger.tree) — this is the single reader, so the
 *  settings banner, the canvas border and anything later added cannot drift
 *  apart on where the edge is. Re-evaluated on every tree poll, which is what
 *  makes an expiring window drop the red without an event. */
export const fallbackActive = (tree: TreePayload): boolean =>
  !!tree.api_fallback && (tree.api_fallback_until ?? 0) * 1000 > Date.now()

/** the org's px-per-credit scale — ONE formula for the canvas bars and the
 *  credit-ask card (user ruling 2026-08-05: the ask bar must look identical
 *  to the agent's existing bar, same scale included) */
export function orgPxc(tree: TreePayload): number {
  // kiosk: the cap is the scale — the overseer bar is a fixed size (user spec)
  if (tree.kiosk?.credits) return (NODE_H * 1.6) / tree.kiosk.credits
  const holds = tree.roots
    .filter((n) => n.state === 'live')
    .map((n) => n.seat + n.grant)
  if (!holds.length) return NODE_H / 10
  if (holds.length === 1) return NODE_H / holds[0]!
  const avg = holds.reduce((a, b) => a + b, 0) / holds.length
  const max = Math.max(...holds)
  return Math.min((NODE_H * 1.25) / avg, (NODE_H * 1.6) / max)
}

export function flatten(root: CanvasNode, _seats: Record<string, number>): Map<string, CanvasNode> {
  const map = new Map<string, CanvasNode>()
  const walk = (n: CanvasNode, parent: string | null) => {
    map.set(n.id, { ...n, parent })
    // NO lineage pseudo-cards (D-120, final form). The org axis and the
    // pseudo-card path must carry DISJOINT id sets — same id from both lets
    // sibling order decide which card wins, silently — and after the ruling's
    // predicate settled on "hide iff successor AND archived", the axis
    // carries every non-archived generation (live and unrecoverable alike)
    // while archived ones were always skipped here. Nothing qualifies: the
    // synthesis that used to sit here is dead by construction, and dead
    // rendering paths get deleted, not fenced. `isBearerOf` and its gates
    // remain in the types for the tether/position plumbing until a full
    // sweep retires them — with no producer they never fire.
    n.children.forEach((c) => walk(c, n.id))
  }
  walk(root, null)
  return map
}

export function layout(root: CanvasNode, hidden: Map<string, string> = new Map()): Map<string, Pt> {
  // `hidden`: piled-away retirees (and their subtrees) — they take NO layout
  // space; their positions are assigned afterwards onto their pile's front
  const pos = new Map<string, Pt>()
  const vis = (n: CanvasNode) => !hidden.has(n.id)
  const width = (n: CanvasNode): number => {
    const kids = n.children.filter(vis)
    return kids.length ? kids.reduce((a, c) => a + width(c), 0) : 1
  }
  const place = (n: CanvasNode, x0: number, depth: number) => {
    let cx = x0
    const kids = n.children.filter(vis)
    kids.forEach((c) => { place(c, cx, depth + 1); cx += width(c) })
    const x = kids.length
      ? (pos.get(kids[0]!.id)!.x + pos.get(kids[kids.length - 1]!.id)!.x) / 2 // nUIA: kids.length checked on this branch
      : x0
    pos.set(n.id, { x, y: depth })
  }
  place(root, 0, 0)
  const out = new Map<string, Pt>()
  for (const [id, p] of pos) out.set(id, { x: p.x * SX + PAD, y: p.y * SY + PAD })
  // The EYE is the page's anchor (user ruling): its world position is a
  // CONSTANT, independent of tree shape — otherwise the coordinate space
  // hangs off the tree's left extent and the eye shifts between orgs (and on
  // every hire that widens the tree).
  const eye = out.get(USER)
  if (eye) {
    const dx = EYE_ANCHOR_X - eye.x, dy = PAD - eye.y
    for (const p of out.values()) { p.x += dx; p.y += dy }
  }
  return out
}

/** FR-18: watchdog satellite cards — the user's own sizing ("1/4 to 1/8 the
 *  area" of a 124x124 node). Shrunk from an initial 60x36 (user bug
 *  2026-08-12: read as "too magnified" against the rest of the canvas, and
 *  the name had too little room inside it) to a size closer to the 1/8 end;
 *  name + state glyph is all that fits, the click-through panel carries the
 *  detail. */
export const DOG_W = 50, DOG_H = 26

export function sizeOf(id: string): { w: number; h: number } {
  if (id === USER) return { w: USER_W, h: USER_H }
  if (id === INBOX) return { w: USER_W, h: INBOX_H }
  if (id.startsWith('dog:')) return { w: DOG_W, h: DOG_H }
  return { w: NODE_W, h: NODE_H }
}

// ------------------------------------------------------------- freeze kinds
// WHICH KIND OF FREEZE IS THIS? One classification, rendered three ways — the
// org banner's note (App.tsx), the desk badge (desk.tsx) and the compact card
// badge (cards.tsx). They are three registers of one question, and before this
// existed each made its own two-way test and every one of them ended in the
// same `else` reading "usage limit".
//
// ⚠ THAT DEFAULT IS THE BUG THIS FIXES, TWICE OVER. Every kind the payload
// could not describe fell through to "usage limit" and the display said it
// confidently:
//   · an AUTH freeze (`cause === "auth"`, the credential was rejected) carries
//     `limit: true` — it is a usage-limit freeze in SHAPE only. It read as
//     "usage limit hit", telling the operator to wait for capacity when the
//     fix is to replace a credential. ▶ really does resume it, so it is
//     counted; only the words were wrong.
//   · a SPEND freeze read as "usage limit" on the node badge. The org banner
//     was right about it only because it returns early on the org-level
//     `spend_frozen` flag — a badge has no org flag to consult.
// Both were invisible until `ledger.tree()`'s frozen projection was taught to
// carry `cause` and `spend`; it rebuilds the record key by key and silently
// drops whatever it does not name.
//
// ⚠ ORDER IS MEANING, not style. `limit_locked` outranks everything: a fable
// lock's clock can never fire, so naming any other kind there would promise a
// reset that nobody performs. `spend` outranks `limit` because a spend freeze
// also carries limit-ish shape but is released by raising the limit, not by
// waiting. `auth` outranks `limit` for the same reason — same shape, different
// remedy. `connection` is last of the real kinds because it is the only one
// that retries itself.
export type FreezeKind = 'halted' | 'spend' | 'auth' | 'connection' | 'limit'

export function freezeKind(
  fz: { connection?: boolean | null; limit?: boolean | null
        cause?: string | null; spend?: boolean | null } | null | undefined,
  limitLocked?: boolean,
): FreezeKind | null {
  if (limitLocked) return 'halted'
  if (!fz) return null
  if (fz.spend) return 'spend'
  if (fz.cause === 'auth') return 'auth'
  // a PURE connection freeze only — a record carrying both flags is a limit
  // whose wake waits on the auto-resume toggle (D-122)
  if (fz.connection && !fz.limit) return 'connection'
  return 'limit'
}

// The two badge registers. They live here, beside the classification and as
// data, for one reason: as inline ternaries in the JSX they could not be
// tested for the property that actually matters — that every kind gets its
// OWN words. A collapsed branch (two kinds sharing a string) is exactly the
// failure being fixed, and it is invisible unless something compares the
// labels to each other. `freezelabel.test.ts` asserts both maps are TOTAL over
// FreezeKind and that no two kinds share a label.
// FR-27 · the primed-restart chip's WORDS.
//
// Here rather than inline in App.tsx's header, for the reason the two freeze
// registers below are here: as a JSX ternary the one property that matters
// could not be tested. That property is NOT "the chip renders" — it is that a
// prime which will restart the orgs reading this says so, and a prime which
// will NOT (target 'mailhub' rebuilds a container and touches no agent) does
// not get to wear the same words.
//
// ⚠ The record is MACHINE-WIDE: api.py injects the same value into every org's
// tree, so most of this chip's audience did not arm it and is only finding out
// here. That is what the title has to serve — who armed it, why, whether THIS
// org gets cut, and how to stop it.
export interface PrimedChip { label: string; title: string; cutsUs: boolean }

export function primedRestartChip(
  pr: { target?: string | null; by_org?: string | null
        by_node?: string | null; at?: string | null
        reason?: string | null } | null | undefined,
): PrimedChip | null {
  if (!pr) return null
  const cutsUs = pr.target !== 'mailhub'
  return {
    label: cutsUs
      ? (pr.target === 'both' ? 'restart primed (+ mail hub)' : 'restart primed')
      : 'mail hub restart primed',
    title: [
      `armed by ${pr.by_org ?? '?'}/${pr.by_node ?? '?'} at ${pr.at ?? '?'}`
        + (pr.reason ? ` — ${pr.reason}` : ''),
      cutsUs
        ? 'every org on this machine restarts, including this one'
        : 'rebuilds the mail hub container only — agents here are NOT restarted',
      'nothing happens while anyone is mid-turn; it fires by itself once the machine is quiet',
      'disarm with orgtree_prime_restart action=cancel',
    ].join('\n'),
    cutsUs,
  }
}

export const FREEZE_LABEL: Record<FreezeKind, string> = {
  halted: 'HALTED — fable lock',
  spend: 'spend limit',
  auth: 'credential rejected',
  connection: 'network',
  limit: 'usage limit',
}

/** the 124px card's register — same kinds, shorter words */
export const FREEZE_LABEL_SHORT: Record<FreezeKind, string> = {
  halted: 'halted',
  spend: 'spend',
  auth: 'credential',
  connection: 'net',
  limit: 'limit',
}

// ------------------------------------------------ edge jump card placement
// user bug 2026-08-26: the jump cards sat at a fixed 6px from the window edge
// and overlapped the focused desk on a narrow window. Cards are SQUARE, and a
// focus glide fits one to min(vw, vh) − 48 centred, so on any window taller
// than it is wide the strip beside the desk is exactly 24px — while a full
// card wants 186. Worse, coworkers share a layout row, so the card's elevation
// lands mid-screen: over the chat text, not over a corner.
//
// Placement is chosen from the desk's MEASURED screen rect rather than from
// the window, so hand-zooming past the standard fit behaves the same way —
// including the case where the focus zoom is floored at Z_DESK and the desk
// overflows the viewport entirely (see centerOn), where the gutter goes
// negative and only the bare tab can sit in the desk's own padding.
//
// The shortage flips axes: a narrow window has thin gutters but a deep band
// above and below the desk, a wide window the reverse. So prefer the gutter,
// fall back to the band (which keeps the NAME — the thing worth keeping), and
// only shed content when neither has room. A near-square window is the one
// shape that is tight both ways, and that is what the tab form is for.
// ⚠ EJ_MID IS A MEASUREMENT, NOT A GUESS, and it must stay one. jsdom has no
// box model, so the unit test cannot check any of these against a rendered
// card — it can only assume them. `tests/edgejump_probe.py` renders the real
// markup against the real sheet in Edge and fails if a form outgrows its
// number here; it reads these constants directly out of this file so the two
// cannot drift apart. First measurement came in at 101.63px against a guessed
// 58 — the guess would have shipped cards that still covered the desk on any
// gutter between 66 and 110px, with the unit test green throughout.
export const EJ_EDGE = 6     // the card's inset from the window edge
export const EJ_FULL = 180   // .edge-jump max-width — the named card
export const EJ_MID = 80     // measured 78.50 worst case (busy + unread dot)
export const EJ_H = 26       // card height
export const EJ_GAP = 8      // clearance insisted on between card and desk

export type EJForm = 'full' | 'mid' | 'tab'
export type EJRect = { x0: number; y0: number; x1: number; y1: number }

export function edgeJumpPlacement(
  side: 'l' | 'r',
  desk: EJRect,
  vp: { width: number; height: number },
  elev: number,
): { form: EJForm; y: number; band: boolean } {
  const gutter = side === 'l'
    ? desk.x0 - EJ_EDGE
    : vp.width - desk.x1 - EJ_EDGE
  // the neighbour's own elevation, kept inside the viewport
  const atElev = Math.min(vp.height - EJ_H, Math.max(EJ_H, elev))
  if (gutter >= EJ_FULL + EJ_GAP) return { form: 'full', y: atElev, band: false }
  // the free band above / below the desk, whichever is deeper. No viewport
  // clamp here: the band is inside the viewport by construction, and clamping
  // would push a card in a shallow band back down onto the desk.
  const above = desk.y0, below = vp.height - desk.y1
  if (Math.max(above, below) >= EJ_H + EJ_GAP) {
    return {
      form: 'full', band: true,
      y: above >= below ? above / 2 : vp.height - below / 2,
    }
  }
  if (gutter >= EJ_MID + EJ_GAP) return { form: 'mid', y: atElev, band: false }
  return { form: 'tab', y: atElev, band: false }
}

export const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

export const ago = (at: string | null | undefined) => {
  if (!at) return ''
  const s = Math.max(0, (Date.now() - Date.parse(at)) / 1000)
  return s < 90 ? `${Math.round(s)}s`
    : s < 5400 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`
}

/** WHEN THIS AGENT WAS LAST TOUCHED, as epoch ms — the END OF ITS LAST TURN.
 *
 *  This is the same clock FR-23's card badge already shows (`TurnStat.at`,
 *  written unconditionally at turn completion, killed turns included), chosen
 *  again here so a pile row and that agent's card can never disagree about how
 *  long ago it last did anything. The two alternatives were both worse:
 *    · `last_status.at` exists only when the agent CHOSE to report a status,
 *      so a busy agent that never called orgtree_status would read as older
 *      than one that reported once and then idled (FR-23 rejected it for the
 *      same reason).
 *    · `archived_at` is the retire time, and a dissolve stamps an entire
 *      subtree with ONE instant — the order it produces for the common case
 *      (a team retired together) is a single tie, i.e. no order at all.
 *
 *  0 = never ran. A positive timestamp always beats it, so a fresh hire that
 *  was retired without ever taking a turn sorts to the BOTTOM. It is 0 and not
 *  -Infinity deliberately: `-Infinity - -Infinity` is NaN, and a comparator
 *  that returns NaN silently leaves the array in an arbitrary order. */
export const lastTouched = (n: CanvasNode | undefined | null): number => {
  const at = n?.turns?.[n.turns.length - 1]?.at
  const t = at ? Date.parse(at) : NaN
  return Number.isFinite(t) ? t : 0
}

/** the order a pile's members are LISTED in, top row first (user request
 *  2026-08-27): most recently touched at the top, so the retirees you last
 *  worked with are the ones you do not have to scroll for.
 *
 *  Ties (and the never-ran block at the bottom) keep the order the picker used
 *  before this existed — newest-archived first, which is `list` reversed —
 *  because `Array.prototype.sort` is stable. So this only ever REFINES the old
 *  order; it never scrambles the members it has no clock for.
 *
 *  ⚠ It does NOT reorder `Pile.list` itself. `list` is the stack's own order
 *  and its last entry is the default front card; re-sorting it would silently
 *  move which retiree the canvas shows on top of the pile, which is a separate
 *  decision the user already controls by hand (and one persisted per-org in
 *  localStorage). This is a display order for the picker's rows, nothing more. */
export const pileOrder = (list: string[],
  map: Map<string, CanvasNode>): string[] =>
  [...list].reverse()
    .sort((a, b) => lastTouched(map.get(b)) - lastTouched(map.get(a)))

// ------------------------------------------------- the attention pip (D-169)
/** What the user's inbox pip shows, and whether it is the ATTENTION tier.
 *  `null` = show no pip at all. */
export interface AttentionPip {
  /** the number on the badge */
  count: number
  /** the ATTENTION tier: the pulsing accent badge (`.asks`) and the header
   *  bell's `.glow`. False = the quiet ordinary-unread badge. */
  urgent: boolean
  /** the control's tooltip. Here rather than at the call sites BECAUSE they
   *  had already drifted — see the ⚠ below. */
  title: string
}

/** THE user-inbox pip rule, in one place.
 *
 *  ⚠ WHY THIS IS A FUNCTION AND NOT FOUR TERNARIES. It used to be four:
 *  the header ask-bell (App.tsx), UserNode's pip and EyeDesk's pip
 *  (cards.tsx), and the compact map eye (OrgCanvas.tsx) each wrote
 *  `asks > 0 ? asks : unread` out by hand with its own title string — and
 *  they had ALREADY diverged before this feature (the bell's tooltip named
 *  the unread count alongside the asks; EyeDesk's said only "your inbox").
 *  Threading a THIRD input through four hand-written copies is exactly how
 *  one of them ends up wrong, and this codebase has paid for that shape more
 *  than once (freeze labels, three surfaces, one wrong default in each).
 *
 *  THE RULE. Attention = open asks + unread URGENT mail (D-169: an agent may
 *  tag user-bound mail urgent, and the inbox then pulses the way it does for
 *  an unanswered question). When that total is non-zero it OVERRIDES the
 *  ordinary unread count and pulses.
 *
 *  THE ZERO EDGE, stated because the spec did not settle it: with nothing
 *  urgent and no ask open, an inbox holding ordinary unread mail falls back
 *  to the plain unread count, NOT pulsing — 12 unread reads "12", quietly.
 *  The two branches are named (`attention` vs `unread`) rather than nested
 *  so which is which can be read rather than inferred.
 *
 *  ⚠ `urgent_unread` is a SUBSET of `user_inbox_count` (both count entries
 *  still sitting in `user_inbox`, which is what unread means server-side),
 *  so the title lists them side by side and never adds them together. */
export const attentionPip = (t: {
  asks_open?: number | null
  urgent_unread?: number | null
  user_inbox_count?: number | null
}): AttentionPip | null => {
  const asks = t.asks_open ?? 0
  const urgent = t.urgent_unread ?? 0
  const unread = t.user_inbox_count ?? 0
  const attention = asks + urgent
  if (attention > 0) {
    const parts: string[] = []
    if (asks > 0) parts.push(`${asks} ask${asks > 1 ? 's' : ''} waiting on your answer`)
    if (urgent > 0) parts.push(`${urgent} urgent mail`)
    if (unread > 0) parts.push(`${unread} unread`)
    return { count: attention, urgent: true, title: parts.join(' · ') }
  }
  return unread > 0
    ? { count: unread, urgent: false, title: `${unread} unread` }
    : null
}

// chat markdown: gfm + hard line breaks, sanitized (agents echo web content).
// №21: cached by text identity — every streamed token used to re-parse the
// ENTIRE visible transcript (~8 Hz × every message × every open panel)
const _mdCache = new Map<string, { __html: string }>()
// №16: outside code fences and inline code, a bare <Token> parses as an HTML
// tag — DOMPurify then strips it and keeps only the inner text, so
// `Sync<float3>` silently became `Sync` and changed the sentence's meaning.
// Escape `<` in plain prose; fenced/inline code is already safe.
// Review C8: the old closed-fence regex OVER-escaped inside every block it
// didn't recognise — an UNTERMINATED fence (every streaming code block, on
// every delta until the closing ``` arrives), ~~~ fences, and 4-space
// indented blocks all rendered a literal "&lt;". Line-walk instead: one
// inCode flag, an unterminated opener stays open to EOF, and only prose
// lines are escaped (inline `spans` protected within them).
const escapeAngles = (src: string) => {
  const lines = src.split('\n')
  let fence: { ch: string; len: number } | null = null   // {ch, len} of the open fence
  let indented = false                   // inside a 4-space indented block
  let prevBlank = true                   // indented blocks open after a blank
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i]!
    const m = /^ {0,3}(`{3,}|~{3,})/.exec(l)
    if (fence) {
      // closed only by a run of the SAME char, at least as long as the opener
      if (m && m[1]![0] === fence.ch && m[1]!.length >= fence.len) fence = null // nUIA: group 1 is unconditional in the regex
      prevBlank = false
      continue
    }
    if (m) {
      fence = { ch: m[1]![0]!, len: m[1]!.length } // nUIA: group 1 is unconditional and non-empty ({3,})
      prevBlank = false
      continue
    }
    const blank = /^\s*$/.test(l)
    if (indented) {
      if (!blank && !/^(?: {4}|\t)/.test(l)) indented = false
      else { prevBlank = blank; continue }
    } else if (prevBlank && /^(?: {4}|\t)/.test(l) && !blank) {
      indented = true
      prevBlank = false
      continue
    }
    prevBlank = blank
    // prose line: escape < outside inline `spans`
    const parts = l.split(/(`[^`\n]*`)/)
    for (let j = 0; j < parts.length; j += 2) {
      parts[j] = parts[j]!.replace(/</g, '&lt;')
    }
    lines[i] = parts.join('')
  }
  return lines.join('\n')
}
// click-to-copy on code blocks: every <pre> gets wrapped in a .codewrap with a
// copy button as a SIBLING (not a child of the scrolling <pre> — an absolute
// child would ride along on horizontal scroll, and its label would pollute
// pre.textContent). Injected AFTER DOMPurify, so the markup is ours; a spoofed
// button in agent output is harmless — the handler only ever reads code text.
export const CopyIcon =
  '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">'
  + '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 3.5v-1a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h1"/></svg>'
const CheckIcon =
  '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
  + '<path d="M3 8.5l3.5 3.5L13 4.5"/></svg>'
const wrapCodeBlocks = (html: string, imgBase?: string) => {
  if (!html.includes('<pre') && !html.includes('<img')) return html
  const tpl = document.createElement('template')
  tpl.innerHTML = html
  tpl.content.querySelectorAll('pre').forEach(pre => {
    const wrap = document.createElement('div')
    wrap.className = 'codewrap'
    pre.replaceWith(wrap)
    wrap.appendChild(pre)
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.className = 'code-copy'
    btn.title = 'Copy code'
    btn.setAttribute('aria-label', 'Copy code')
    btn.innerHTML = CopyIcon
    wrap.appendChild(btn)
  })
  // images (user spec 2026-08-25): a RELATIVE src — `![](outbox/plot.png)` in
  // an agent's reply, a mail body, a presented doc — names a file in the
  // author's working folder, which the /file endpoint already serves. Resolve
  // it against the caller's per-node base; absolute/data/anchor srcs pass
  // untouched. Runs on SANITIZED html, and builds only same-origin /file URLs
  // from the path, so no new scheme can enter here. decode-then-encode:
  // marked percent-encodes what it parses, the query param needs exactly one
  // layer. (No base → leave the src alone; it 404s visibly rather than
  // silently pointing at the SPA route.)
  tpl.content.querySelectorAll('img').forEach(img => {
    const src = img.getAttribute('src') ?? ''
    if (imgBase && src && !/^([a-z][a-z0-9+.-]*:|\/|#)/i.test(src)) {
      let rel = src
      try { rel = decodeURIComponent(src) } catch { /* malformed % — keep raw */ }
      img.setAttribute('src', imgBase + encodeURIComponent(rel))
    }
    img.setAttribute('loading', 'lazy')
  })
  return tpl.innerHTML
}
// one delegated listener for every panel (content is innerHTML, so per-element
// React handlers don't exist). Streaming re-renders replace the button node,
// which simply drops the transient ✓ state — harmless.
if (typeof document !== 'undefined') document.addEventListener('click', e => {
  // no `instanceof HTMLButtonElement` here — that global doesn't exist in the
  // node+jsdom test scope and the check threw on every unrelated click
  const btn = (e.target as Element | null)?.closest?.('button.code-copy')
  if (!btn) return
  const pre = btn.closest('.codewrap')?.querySelector('pre')
  // innerText, not textContent — the diff pre renders each line as a <div>,
  // whose textContent concatenates with NO newlines
  const text = (pre?.innerText ?? '').replace(/\n$/, '')
  navigator.clipboard?.writeText(text).then(() => {
    btn.innerHTML = CheckIcon
    btn.classList.add('copied')
    setTimeout(() => {
      if (!btn.isConnected) return
      btn.innerHTML = CopyIcon
      btn.classList.remove('copied')
    }, 1200)
  }).catch(() => {})
})
/** `imgBase` (optional): the node-scoped /file URL prefix relative image
 *  srcs resolve against — pass `fileBase(slug, nid)` where the author's
 *  files are known; the cache keys on it (NUL joins the halves — it never
 *  occurs in a URL prefix, so two pairs cannot alias), and the same text
 *  rendered for two nodes never crosses. */
export const md = (text: string | null | undefined,
                   imgBase?: string): { __html: string } => {
  const key = (imgBase ?? '') + '\u0000' + (text ?? '')
  let hit = _mdCache.get(key)
  if (hit === undefined) {
    hit = { __html: wrapCodeBlocks(DOMPurify.sanitize(
      marked.parse(escapeAngles(text ?? ''), { gfm: true, breaks: true, async: false })), imgBase) }
    if (_mdCache.size > 800) _mdCache.clear()   // bounded; refills on demand
    _mdCache.set(key, hit)
  }
  return hit
}
export const smooth = (t: number) => t * t * (3 - 2 * t)

// ---- connection segments (world space). kind 'c' = cubic bezier, 'l' = line.
export type Seg =
  | { kind: 'l'; pts: [Pt, Pt] }
  | { kind: 'c'; pts: [Pt, Pt, Pt, Pt] }
export const segD = (s: Seg) => (s.kind === 'l'
  ? `M ${s.pts[0].x} ${s.pts[0].y} L ${s.pts[1].x} ${s.pts[1].y}`
  : `M ${s.pts[0].x} ${s.pts[0].y} C ${s.pts[1].x} ${s.pts[1].y}, `
    + `${s.pts[2].x} ${s.pts[2].y}, ${s.pts[3].x} ${s.pts[3].y}`)
export const segPoint = (s: Seg, t: number): Pt => {
  if (s.kind === 'l') {
    const [p, q] = s.pts
    return { x: p.x + (q.x - p.x) * t, y: p.y + (q.y - p.y) * t }
  }
  const [p0, p1, p2, p3] = s.pts, u = 1 - t
  return {
    x: u * u * u * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t * t * t * p3.x,
    y: u * u * u * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t * t * t * p3.y,
  }
}

// Escape closes any overlay panel (they had no keyboard exit at all)
export function useEsc(close: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])
}

/** G5 — the panel heartbeat.
 *
 *  An audit of the read endpoints found 18 of 19 call sites fetching ONCE on
 *  mount: open a panel, and whatever it showed at that instant is what it
 *  shows until you close it. Some of those panels display data that changes
 *  while you are looking at it — mail arriving, an agent writing files, disk
 *  filling — and the node inbox had a workaround for exactly this, refetching
 *  when a `pulse` prop changed, which covered turn events and nothing else
 *  (a mail delivery is not a pulse).
 *
 *  Same rule as the tree and the chat: liveness is gated on "this panel is
 *  mounted", which is known locally and cannot be stale — never on a piece of
 *  the data being refreshed. The fetcher is held in a ref so an inline arrow
 *  does not restart the timer on every render; `deps` decides identity.
 */
export function usePolled<T>(
  fetcher: () => Promise<T>, deps: DependencyList, ms = 5000,
  refreshKey: unknown = 0,
): T | null {
  const [v, setV] = useState<T | null>(null)
  const ref = useRef(fetcher)
  ref.current = fetcher
  // ⚠ a DEPS change is an IDENTITY change (new folder, new node, new org) —
  // the previous identity's data must not stay on screen until the new fetch
  // lands (redteam finding, render.test §6.10: a slow fetch left folder A's
  // listing rendered under folder B's path). Reset here, once, rather than
  // per call site — patching one panel recreates the N-writers problem the
  // state review opens with. `refreshKey` is the OTHER kind of restart: same
  // identity, fetch again now (the read-ack bump, 89fecd9) — resetting there
  // would blank the inbox on every mark-read, so it deliberately does not.
  useEffect(() => { setV(null) },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [...deps])
  useEffect(() => {
    let dead = false
    const tick = () => {
      void ref.current().then((r) => { if (!dead) setV(r) }).catch(() => {})
    }
    tick()
    const t = setInterval(tick, ms)
    // the client's G2 (livebus.ts): every mutation and every ws 'changed'
    // wakes this surface immediately — the interval above is only the
    // fallback for a dropped ws. This is what makes a grant rescinded in
    // one panel disappear from another within a beat instead of a poll
    // interval (user bug 2026-08-14).
    const off = onLiveBump(tick)
    return () => { dead = true; clearInterval(t); off() }
    // the fetcher rides a ref on purpose; `deps` is the identity of the thing
    // being fetched (slug, node, folder), which is what should restart it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, ms, refreshKey])
  return v
}
