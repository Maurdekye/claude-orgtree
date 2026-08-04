// canvas/shared.ts — the split Canvas's leaf module: the canvas view types,
// world-space geometry constants and helpers (layout/flatten/springs math),
// the chat-markdown pipeline (md), and the small shared hooks. Helpers and
// types only — this file imports no components. Extracted verbatim from
// Canvas.tsx in the phase-3 split; all comments ride with their code.

import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { useEffect, useRef, useState } from 'react'
import type { DependencyList } from 'react'
import type {
  ActivityInfo, AskInfo, DirGrant, MailEntry, NodeState, NodeStatus,
  OpRequest, OpResult, ToolGrant, TreeNode, TreePayload,
} from '../types'

export const TIER_LETTER: Record<string, string> = { haiku: 'H', sonnet: 'S', opus: 'O', fable: 'F' }
export const TIERS = ['haiku', 'sonnet', 'opus', 'fable']
/** seat cost per tier — mirrors ledger.TIERS. One table, four tiers; the
 *  frontend had four copies of this before. */
export const TIER_SEAT: Record<string, number> =
  { haiku: 1, sonnet: 3, opus: 5, fable: 10 }
/** Model VERSIONS inside a tier — mirrors ledger.MODEL_VERSIONS. A version is
 *  a subcategory of the tier (user ruling 2026-08-04): it never changes the
 *  seat cost and never appears as a chip, only in the gear. A tier absent
 *  here, or present with one entry, offers no choice. */
export const MODEL_VERSIONS: Record<string, string[]> = { opus: ['5', '4.8'] }

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
  waiting?: boolean
  responding?: boolean
  phase?: string | null
  queued?: number
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
}

// the canvas exposes its inverse-zoom to CSS; React's CSSProperties has no
// custom-property indexer, so declare the one we use (type-only)
declare module 'react' {
  interface CSSProperties {
    '--invz'?: string
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
  // previews the ordering the hire will pin; a plain draft appends at the end
  const place = (kids: CanvasNode[]): CanvasNode[] => {
    const b = draft!.beside
    const i = b ? kids.findIndex((k) => k.id === b.anchor) : -1
    if (!b || i < 0) return [...kids, draftNode()]
    const at = b.side === 'left' ? i : i + 1
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

export function flatten(root: CanvasNode, seats: Record<string, number>): Map<string, CanvasNode> {
  const map = new Map<string, CanvasNode>()
  const walk = (n: CanvasNode, parent: string | null) => {
    map.set(n.id, { ...n, parent })
    // live (rehired) lineage bearers surface as consultable cards beside their
    // successor — never as org children (§8.5)
    ;(n.lineage ?? []).forEach((b, i) => {
      if (b.state !== 'archived') {
        map.set(b.id, {
          id: b.id, title: b.id, tier: b.tier, state: b.state,
          bearer_state: b.bearer_state, generation: b.generation,
          seat: seats?.[b.tier] ?? 0, grant: 0, free: 0, children: [],
          lineage: [], parent, model_id: b.tier, scope: { tools: {}, add_dirs: [] },
          isBearerOf: n.id, bearerIndex: i,
        })
      }
    })
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

export function sizeOf(id: string): { w: number; h: number } {
  if (id === USER) return { w: USER_W, h: USER_H }
  if (id === INBOX) return { w: USER_W, h: INBOX_H }
  return { w: NODE_W, h: NODE_H }
}

export const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

export const ago = (at: string | null | undefined) => {
  if (!at) return ''
  const s = Math.max(0, (Date.now() - Date.parse(at)) / 1000)
  return s < 90 ? `${Math.round(s)}s`
    : s < 5400 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`
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
export const md = (text: string | null | undefined): { __html: string } => {
  const key = text ?? ''
  let hit = _mdCache.get(key)
  if (hit === undefined) {
    hit = { __html: DOMPurify.sanitize(
      marked.parse(escapeAngles(key), { gfm: true, breaks: true, async: false })) }
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
): T | null {
  const [v, setV] = useState<T | null>(null)
  const ref = useRef(fetcher)
  ref.current = fetcher
  useEffect(() => {
    let dead = false
    const tick = () => {
      void ref.current().then((r) => { if (!dead) setV(r) }).catch(() => {})
    }
    tick()
    const t = setInterval(tick, ms)
    return () => { dead = true; clearInterval(t) }
    // the fetcher rides a ref on purpose; `deps` is the identity of the thing
    // being fetched (slug, node, folder), which is what should restart it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, ms])
  return v
}
