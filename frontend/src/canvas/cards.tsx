// canvas/cards.tsx — the canvas's card components: the overseer eye
// (UserNode) with its switchboard (EyeDesk), the hire chips (SpawnChips),
// the drag-adjustable CreditBar, the draft/hiring card (DraftNode), and the
// agent card itself (NodeSquare). Extracted verbatim from Canvas.tsx in the
// phase-3 split.

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { ToastFn, TreePayload } from '../types'
import { audienceAction, getCharters, saveKiosk } from '../api'
import {
  AutorenewIcon, CheckIcon, CloseIcon, FocusIcon, FrozenIcon, LayersIcon,
  LockIcon, MailIcon, SettingsIcon,
} from '../icons'
import {
  DESK_SCALE, deskDpi, DRAFT, NODE_H, NODE_W, TIER_LETTER, TIERS, USER,
  USER_H, USER_W,
} from './shared'
import type {
  CanvasNode, DraftScope, DraftState, MailLinkFn, OpFn, Pile,
  Pt,
} from './shared'
import { Activity, ContextWheel, DeskChat } from './desk'
import { DraftScopeModal } from './modals'

// ------------------------------------------------------------- the overseer
interface UserNodeProps {
  pos: Pt
  isDrop: boolean
  stats: { circ: number; seats: number; free: number }
  inboxCount: number
  seats: Record<string, number>
  mailGlow: boolean
  kiosk: TreePayload['kiosk']
  pub: boolean
  kioskRemaining: number | null
  kioskSegs?: { seat: number; grant: number }[]
  pxc: number
  zoom: number
  onInbox?: () => void
  onGear?: () => void
  onSpawn: (tier: string) => void
  onMailLink: MailLinkFn
  focused: boolean
  eyeW: number
  onFocus?: () => void
  posX: (id: string) => number
  onJump?: (id: string) => void
  map: Map<string, CanvasNode>
  op: OpFn
  slug: string
  toast: ToastFn
  compactAt?: number
}

export function UserNode({ pos, isDrop, stats, inboxCount, seats, mailGlow,
  kiosk, pub, kioskRemaining, kioskSegs, pxc, zoom, onInbox, onGear, onSpawn,
  onMailLink,
  focused, eyeW, onFocus, posX, onJump, map, op, slug, toast,
  compactAt }: UserNodeProps) {
  const downRef = useRef<Pt | null>(null)
  // const extraction: the kiosk-credits narrowing must survive the commit
  // closure below (a property check alone would not)
  const kioskCredits = kiosk?.credits
  return (
    <div className={'sq user' + (focused ? ' desk eyeboard' : '')
      + (isDrop ? ' drop' : '') + (mailGlow && !focused ? ' mail-glow' : '')}
      style={{
        transform: `translate(${pos.x}px, ${pos.y}px)`,
        width: focused ? eyeW : USER_W, height: USER_H,
        // symmetric expansion: the layout slot stays 124 wide, the card grows
        // both ways so the eye's center (and its edges) never move
        marginLeft: focused ? -(eyeW - USER_W) / 2 : 0,
        zIndex: focused ? 5 : undefined,
      }}
      onPointerDown={(e) => {
        if ((e.target as Element).closest('button, input, textarea, select, .desk-over')) return
        e.stopPropagation()
        downRef.current = { x: e.clientX, y: e.clientY }
      }}
      onPointerUp={(e) => {
        const d = downRef.current
        downRef.current = null
        if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) < 5 && !focused)
          onFocus?.()
      }}>
      {/* the user's pool is infinite, so their bar fades out into the top
          instead of ending; hovering it reports the org's circulation
          (the tip is a sibling — the fade mask would swallow a child).
          It stays rendered at switchboard focus (user ruling): anchored to
          the card's left edge, it glides outward as the square expands. */}
      {kioskCredits
        ? /* kiosk: the pool is FINITE — a fixed-size bar with per-child slabs,
             exactly like an agent's, and (user spec 2026-07-31) draggable BY
             THE ADMIN to adjust the org's total credit cap; the public
             gateway never gets the handle (and the /kiosk endpoint it would
             need is deny-listed anyway) */
          <CreditBar seat={0} grant={kioskCredits} committed={stats.circ}
            segments={kioskSegs ?? []} zoom={zoom} pxc={pxc} capMode
            min={stats.circ}
            onCommit={pub ? undefined : (delta) =>
              saveKiosk(slug, { credits: kioskCredits + delta })
                .then(() => toast?.([`kiosk credit cap: ${kioskCredits + delta}`]))
                .catch((e: Error) => toast?.([`error: ${e.message}`]))} />
        : <div className="cbar-inf-wrap">
            <div className="cbar-infinite" />
            <div className="cbar-tip">
              <div>circulation <b className="n-fill">{stats.circ}</b></div>
              <div>seats <b className="n-seat">{stats.seats}</b></div>
              <div>free <b className="n-free">{stats.free}</b></div>
            </div>
          </div>}
      <svg className="eye" viewBox="0 0 48 26">
        <path d="M 2 13 C 13 2, 35 2, 46 13 C 35 24, 13 24, 2 13 Z" />
        <circle className="iris" cx="24" cy="13" r="6.5" />
        <circle className="pupil" cx="24" cy="13" r="2.6" />
      </svg>
      {!focused && <div className="user-label">you</div>}
      {!focused && <button className="eye-inbox"
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => { e.stopPropagation(); onInbox?.() }}>
        <MailIcon fontSize="inherit" />{inboxCount > 0 && <span className="count">{inboxCount}</span>}
      </button>}
      {/* open to visitors too (user ruling): agent-hire defaults are
          configurable by anyone, ceiling-clamped like any grant */}
      {!focused && <button className="eye-gear" title="agent-hire defaults"
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => { e.stopPropagation(); onGear?.() }}><SettingsIcon fontSize="inherit" /></button>}
      {/* real seat costs in the hover hints — a literal 0 was technically true
          (infinite pool) but read as wrong next to every other card. The
          chips survive switchboard focus too (user spec) — hiring is never
          out of reach. */}
      <SpawnChips onSpawn={onSpawn} free={kioskRemaining ?? Infinity} seats={seats}
        maxTier={kiosk?.max_tier} />
      {focused && (
        <EyeDesk map={map} op={op} slug={slug} toast={toast}
          inboxCount={inboxCount} onInbox={onInbox}
          onGear={onGear} pub={pub} eyeW={eyeW} posX={posX} onJump={onJump}
          compactAt={compactAt} onMailLink={onMailLink} />
      )}
    </div>
  )
}

// ---------------------------------------------------------- the switchboard
// Focusing the eye opens SIDE-BY-SIDE live chats with every agent that has a
// direct line to the user — top-level agents plus user-audience holders
// (user spec). Tabs stay visible at all times; chats minimize/maximize to
// manage crowding. A line that exists via an audience grant carries an ✕:
// closing that tab RESCINDS the grant (top-level lines are permanent).
interface EyeDeskProps {
  map: Map<string, CanvasNode>
  op: OpFn
  slug: string
  toast: ToastFn
  inboxCount: number
  onInbox?: () => void
  onGear?: () => void
  pub: boolean
  eyeW: number
  posX: (id: string) => number
  onJump?: (id: string) => void
  compactAt?: number
  onMailLink: MailLinkFn
}

function EyeDesk({ map, op, slug, toast, inboxCount,
  onInbox, onGear, pub, eyeW, posX, onJump, compactAt, onMailLink }: EyeDeskProps) {
  const agents = [...map.values()].filter((n) =>
    n.id !== USER && n.id !== DRAFT && n.state === 'live' && !n.isBearerOf
    && (n.parent === USER || n.audiences_held?.includes(USER)))
    // tab order mirrors the tree's left→right spatial order (user ruling)
    .sort((a, b) => (posX?.(a.id) ?? 0) - (posX?.(b.id) ?? 0))
  const [minned, setMinned] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem('orgtree-eyemin-' + slug)
        || '[]') as string[])
    } catch { return new Set() }
  })
  const toggle = (id: string) => setMinned((s) => {
    const n = new Set(s)
    if (n.has(id)) n.delete(id); else n.add(id)
    localStorage.setItem('orgtree-eyemin-' + slug, JSON.stringify([...n]))
    return n
  })
  // №24: a NEW direct line arrives MINIMIZED with its tab lit — the shipped
  // coordinator charter grants an audience after every hire, and each grant
  // used to shove another full-width panel into the row automatically.
  // `seen` persists beside `min` (review): a bare ref only worked while
  // EyeDesk stayed mounted, so any grant landing with the camera away
  // rendered open anyway
  const seenIds = useRef<Set<string> | null>(null)
  if (seenIds.current === null) {
    try {
      const stored = localStorage.getItem('orgtree-eyeseen-' + slug)
      seenIds.current = stored ? new Set(JSON.parse(stored) as string[]) : null
    } catch { seenIds.current = null }
  }
  const idsKey = agents.map((a) => a.id).join(',')
  useEffect(() => {
    const ids = new Set(idsKey ? idsKey.split(',') : [])
    if (seenIds.current) {
      const fresh = [...ids].filter((id) => !seenIds.current!.has(id))
      if (fresh.length) {
        setMinned((s) => {
          const n = new Set(s)
          fresh.forEach((id) => n.add(id))
          localStorage.setItem('orgtree-eyemin-' + slug, JSON.stringify([...n]))
          return n
        })
      }
    }
    seenIds.current = ids
    try {
      localStorage.setItem('orgtree-eyeseen-' + slug, JSON.stringify([...ids]))
    } catch { /* private mode */ }
  }, [idsKey, slug])
  const open = agents.filter((a) => !minned.has(a.id))
  // the inner virtual panel matches the card interior through the desk scale.
  // DESK_SCALE/deskDpi are shared so this stays in step with .desk-inner's
  // transform — the same equation used to be written out here AND in the CSS
  const innerW = Math.round((eyeW - 4) / (DESK_SCALE * deskDpi()))
  return (
    <div className="desk-over eye-desk" onWheel={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="desk-inner desk-body eye-inner" style={{ width: innerW }}>
        {/* one row (user spec 2026-07-31): the "you · N direct lines" label
            was dead space — the TABS live in the head now, beside the eye */}
        <div className="cc-head eye-head">
          <svg className="eye eye-mini" viewBox="0 0 48 26">
            <path d="M 2 13 C 13 2, 35 2, 46 13 C 35 24, 13 24, 2 13 Z" />
            <circle className="iris" cx="24" cy="13" r="6.5" />
            <circle className="pupil" cx="24" cy="13" r="2.6" />
          </svg>
          <div className="eye-tabs">
          {agents.map((a) => (
            <span key={a.id} className={'eye-tab' + (minned.has(a.id) ? '' : ' on')}>
              <button className="eye-tab-main"
                title={minned.has(a.id) ? 'open this chat' : 'minimize this chat'}
                onClick={() => toggle(a.id)}>
                <span className={'tier t-' + a.tier}>{TIER_LETTER[a.tier!] ?? '?'}</span>
                {a.id}
                {a.busy && <AutorenewIcon fontSize="inherit" className="cc-spin" />}
                {(a.mail_pending ?? 0) > 0 && <b className="eye-count">{a.mail_pending}</b>}
              </button>
              {/* jump straight to the agent's own node — same glide as
                  clicking its card (user spec) */}
              <button className="eye-tab-x eye-tab-jump"
                title="jump to this agent's node"
                onClick={() => onJump?.(a.id)}>
                <FocusIcon fontSize="inherit" /></button>
              {/* ✕ only on audience-granted lines; closing RESCINDS the grant
                  (user spec) — top-level lines have no ✕, they are intrinsic */}
              {a.parent !== USER && a.audiences_held?.includes(USER) &&
                <button className="eye-tab-x"
                  title="close this line (rescinds its audience with you)"
                  onClick={() => audienceAction(slug, 'revoke', a.id, USER)
                    .then(() => toast([`audience ${a.id} → you rescinded`]))
                    .catch((e: Error) => toast([`error: ${e.message}`]))}>
                  <CloseIcon fontSize="inherit" /></button>}
            </span>
          ))}
          {!agents.length &&
            <span className="dim">no direct lines yet — top-level hires and user-audience holders appear here</span>}
          </div>
          <span className="spacer" />
          <button className="cc-icon" title="your inbox" onClick={() => onInbox?.()}>
            <MailIcon fontSize="inherit" />{inboxCount > 0 && <b className="eye-count">{inboxCount}</b>}
          </button>
          <button className="cc-icon" title="agent-hire defaults"
            onClick={() => onGear?.()}><SettingsIcon fontSize="inherit" /></button>
        </div>
        <div className="eye-panels">
          {open.map((a) => (
            <div className="eye-panel" key={a.id}>
              <DeskChat node={a} map={map} op={op} slug={slug}
                toast={toast} pub={pub} bare compact compactAt={compactAt}
                onJump={onJump} onMailLink={onMailLink} />
            </div>
          ))}
          {!open.length && agents.length > 0 &&
            <div className="dim pad">every chat is minimized — click a tab above</div>}
        </div>
      </div>
    </div>
  )
}

interface SpawnChipsProps {
  onSpawn: (tier: string) => void
  free: number
  seats: Record<string, number>
  maxTier?: string | null
  /** F-03: render as a vertical column on this edge — the chips hire a
   *  COWORKER (same superior, placed to that side), not a report */
  side?: 'left' | 'right'
}

function SpawnChips({ onSpawn, free, seats, maxTier, side }: SpawnChipsProps) {
  // kiosk tier cap (user spec): tokens above the cap DISAPPEAR entirely —
  // seat cost doubles as the tier rank, so the cap is a simple cost compare
  const shown = TIERS.filter((t) =>
    !maxTier || (seats[t] ?? 0) <= (seats[maxTier] ?? Infinity))
  return (
    <div className={'hsof' + (side ? ` side side-${side[0]}` : '')}
      onPointerDown={(e) => e.stopPropagation()}>
      {shown.map((t) => {
        const seat = seats[t] ?? 0
        const cant = Number.isFinite(free) && free < seat
        return (
          <button key={t} disabled={cant} className={'t-' + t}
            title={cant
              // user report: an exhausted kiosk cap read as an opaque dead
              // end — the tooltip now carries the REMEDY, not just the number
              ? `${t}: needs ${seat} free (has ${free}) — the kiosk credit `
                + 'cap is fully held; drag an agent’s credit bar down '
                + 'or retire one to free credits'
              : side
                ? `hire ${/^[aeiou]/.test(t) ? 'an' : 'a'} ${t} COWORKER — same `
                  + `superior, to the ${side} (seat ${seat})`
                : `hire ${/^[aeiou]/.test(t) ? 'an' : 'a'} ${t} (seat ${seat})`}
            onClick={(e) => { e.stopPropagation(); onSpawn(t) }}>
            {TIER_LETTER[t]}
          </button>
        )
      })}
    </div>
  )
}
// Every credit bar is DIRECTLY drag-adjustable (user ruling — no ± buttons):
// draft bars set the pending grant, live bars commit a reallocate on release.
// `min` floors a live bar at its committed amount; `max` caps at parent free.
// The bar spans seat+grant; the SEAT block sits at its foot (credits are
// incompressible — a node's whole holding is visible mass).
interface CreditBarProps {
  seat?: number
  grant: number
  committed: number
  segments?: { seat: number; grant: number }[]
  draftMode?: boolean
  min?: number
  max?: number
  maxGhost?: boolean
  onDragValue?: (v: number) => void   // draftMode: the pending grant
  onCommit?: (delta: number) => void  // live: reallocate on release
  zoom: number
  pxc: number
  capMode?: boolean
}

function CreditBar({ seat = 0, grant, committed, segments = [], draftMode,
  min = 0, max, maxGhost, onDragValue, onCommit, zoom, pxc, capMode }: CreditBarProps) {
  const [drag, setDrag] = useState<{ y0: number; g0: number; val: number } | null>(null)          // {y0, g0, val}
  const cur = drag && !draftMode ? drag.val : grant
  const seatLen = seat * pxc
  const len = Math.max(6, (seat + cur) * pxc)
  const start = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draftMode && !onCommit) return
    e.stopPropagation(); e.preventDefault()
    setDrag({ y0: e.clientY, g0: grant, val: grant })
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const move = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag) return
    const dg = (drag.y0 - e.clientY) / (pxc * zoom)
    const v = Math.round(Math.max(min, Math.min(max ?? Infinity, drag.g0 + dg)))
    if (draftMode) onDragValue?.(v)
    else setDrag((d) => d && { ...d, val: v })
  }
  const end = () => {
    if (!drag) return
    const v = drag.val
    setDrag(null)
    if (!draftMode && v !== grant) onCommit?.(v - grant)
  }
  // ruler rungs mark REAL quantities: every 5 credits, or every 25 when the
  // scale is too fine for 5s to resolve (user ruling — never equal-spaced fluff)
  const rung = (5 * pxc >= 4 ? 5 : 25) * pxc
  const delta = drag && !draftMode ? drag.val - drag.g0 : 0
  return (
    <div className={'cbar' + (draftMode || drag ? ' dragging' : '')}
      style={{
        height: len,
        background: `repeating-linear-gradient(to top,
          rgba(255,255,255,.07) 0, rgba(255,255,255,.07) 1px,
          transparent 1px, transparent ${rung}px), var(--input)`,
      }}
      onPointerDown={start} onPointerMove={move}
      onPointerUp={end} onPointerCancel={end}
      onWheel={(e) => e.stopPropagation()}>
      {/* while adjusting a non-top-level bar, a transparent ghost shows the
          ceiling the drag can reach (seat + grant + the parent's free) */}
      {(draftMode || drag) && maxGhost && Number.isFinite(max) &&
        <div className="cbar-max" style={{ height: Math.max(6, (seat + max!) * pxc) }} />}
      {/* inner layers live in a clip so they can never punch through the
          bar's rounded outline (border-box height overhang) */}
      <div className="cbar-clip">
        {/* corner rule (user ruling): square corners ONLY at the seat↔alloc
            junction — the fill's bottom is square iff a seat sits below it,
            and the seat's top is rounded iff no alloc sits above it */}
        <div className={'cbar-fill' + (seatLen > 0 ? '' : ' alone')} style={{
          bottom: seatLen,
          height: draftMode ? cur * pxc : committed * pxc,
        }} />
        {/* the fill is a stack of the children's holdings, one slab per hire —
            each child's SEAT is the darker band at its slab's foot (no divider
            inside a slab; the wash alone splits seat from grant). 1px grey
            hairlines part the own seat from the slabs, and slab from slab. */}
        {(() => {
          let cum = 0
          const out: ReactNode[] = []
          segments.forEach((s, i) => {
            out.push(<div key={'s' + i} className="cbar-subseat"
              style={{ bottom: seatLen + cum * pxc, height: s.seat * pxc }} />)
            cum += s.seat + s.grant
            if (i < segments.length - 1) out.push(<div key={'d' + i}
              className="cbar-div" style={{ bottom: seatLen + cum * pxc }} />)
          })
          return out
        })()}
        {seat > 0 &&
          <div className={'cbar-seat'
            + ((draftMode ? cur : committed) > 0 ? '' : ' crown')}
            style={{ height: seatLen }} />}
        {seat > 0 && cur > 0 && <div className="cbar-div" style={{ bottom: seatLen }} />}
      </div>
      <div className="cbar-tip">
        {draftMode ? (
          <>
            <div>grant <b className="n-fill">{grant}</b></div>
            <div className="dim">seat <b className="n-seat">{seat}</b></div>
          </>
        ) : capMode ? (
          /* the eye's kiosk bar: the same numbers wear their org-level names */
          <>
            <div>cap <b className="n-fill">{cur}</b>{delta !== 0 && <span className="dim"> ({delta > 0 ? '+' : ''}{delta})</span>}</div>
            <div>circulation <b className="n-fill">{committed}</b></div>
            <div>free <b className="n-free">{cur - committed}</b></div>
          </>
        ) : (
          <>
            <div>grant <b className="n-fill">{cur}</b>{delta !== 0 && <span className="dim"> ({delta > 0 ? '+' : ''}{delta})</span>}</div>
            <div>alloc <b className="n-fill">{committed}</b></div>
            <div>free <b className="n-free">{cur - committed}</b></div>
            <div className="dim">seat <b className="n-seat">{seat}</b></div>
          </>
        )}
      </div>
    </div>
  )
}

interface DraftNodeProps {
  pos: Pt
  draft: DraftState
  map: Map<string, CanvasNode>
  seats: Record<string, number>
  maxTop: number
  defaultTop: number
  kioskRemaining: number | null
  tree: TreePayload
  zoom: number
  pxc: number
  onConfirm: (name: string, grant: number, charter: string,
    scope: DraftScope | null) => void
  onCancel: () => void
}

type CharterPreset = { name: string; content: string; path: string }

export function DraftNode({ pos, draft, map, seats, maxTop, defaultTop, kioskRemaining,
  tree, zoom, pxc, onConfirm, onCancel }: DraftNodeProps) {
  const [name, setName] = useState('')
  const [charter, setCharter] = useState('')
  // pre-hire permissions (user spec): configure the agent's dirs, tool
  // switches, MCP grants and visibility BEFORE hiring — no post-hire
  // adjustment needed. null = the org/parent defaults, untouched.
  const [scope, setScope] = useState<DraftScope | null>(null)
  const [permsOpen, setPermsOpen] = useState(false)
  // named charter presets (user ruling): every .md in docs/charters/. Picked
  // presets appear as CARDS, not text (user spec) — click removes, hover
  // shows the source file's path on disk; only finalizing the hire turns
  // them into actual charter text (prepended to any manual entry).
  const [presets, setPresets] = useState<CharterPreset[]>([])
  const [chosen, setChosen] = useState<CharterPreset[]>([])
  useEffect(() => {
    getCharters().then((r) => setPresets(r.charters ?? [])).catch(() => {})
  }, [])
  const finalCharter = () =>
    [...chosen.map((c) => c.content), charter].filter((t) => t.trim())
      .join('\n\n')
  // top-level drafts pre-fill the org's default grant (50 unless configured),
  // clamped only by a kiosk's remaining headroom
  const [grant, setGrant] = useState(() => {
    const g = draft.parent == null ? (defaultTop ?? 50) : 0
    return kioskRemaining != null
      ? Math.max(0, Math.min(g, kioskRemaining - (seats[draft.tier] ?? 0))) : g
  })
  // user ruling: drag the allocation as high as you want — the cost bubbles
  // up the chain to you (§4.6) — bounded only by the org's GLOBAL grant cap
  // (settings: top-level grant cap), a kiosk's hard credit cap, or, when the
  // cascade_hire setting is off, the parent's own free credits.
  const max = kioskRemaining != null
    ? Math.max(0, kioskRemaining - (seats[draft.tier] ?? 0))
    : tree?.cascade_hire === false && draft.parent != null
      ? (map.get(draft.parent)?.free ?? 0)
      : maxTop
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])
  const ok = name.trim().length > 0
  const hire = () => { if (ok) onConfirm(name.trim(), grant, finalCharter(), scope) }
  return (
    <div className="sq draft" style={{
      transform: `translate(${pos.x}px, ${pos.y}px)`, width: NODE_W, height: NODE_H,
    }} onPointerDown={(e) => e.stopPropagation()}>
      {/* unbounded drag — the ghost ceiling only exists under a kiosk cap */}
      <CreditBar seat={seats[draft.tier] ?? 0} grant={grant} committed={0}
        draftMode max={max}
        onDragValue={setGrant} zoom={zoom} pxc={pxc} />
      <div className="draft-tag">uninitialized</div>
      {/* the form is authored at natural screen scale and counter-scaled into
          the card — the desk's inverted-scale regime, on a 200px surface so
          the whole hiring flow stays near overview zoom */}
      <div className="draft-over" onWheel={(e) => e.stopPropagation()}>
        <div className="draft-inner">
          {/* top row: tier token · name entry · gear — one flex row, all three
              the same height with equal gaps (user ruling); the gear is
              always visible and stages the pre-hire permissions */}
          <div className="df-head">
            <span className={'tier t-' + draft.tier}>{TIER_LETTER[draft.tier]}</span>
            <input className="df-name" placeholder="name…" value={name}
              // focus WITHOUT scroll: autoFocus on an element inside the
              // world transform made the browser scroll the overflow:hidden
              // viewport when the draft spawned off-screen (from a desk)
              ref={(el) => {
                if (el && !el.dataset.f) {
                  el.dataset.f = '1'
                  el.focus({ preventScroll: true })
                }
              }}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && ok) hire() }} />
            <button className="df-gear"
              title="permissions — folders, tools, MCP, visibility (applied with the hire)"
              onClick={() => setPermsOpen(true)}>
              <SettingsIcon fontSize="inherit" /></button>
          </div>
          {/* grant lives ONLY on the credit bar (user ruling) — no slider,
              no readout line; the bar's own tip reports grant + seat */}
          {presets.length > 0 && (
            <select className="df-preset-add" value=""
              onChange={(e) => {
                const p = presets.find((x) => x.name === e.target.value)
                if (p && !chosen.some((c) => c.name === p.name)) {
                  setChosen((cs) => [...cs, p])
                  // user spec: the FIRST chosen preset names a still-unnamed
                  // agent after itself (typing over it still works)
                  if (!name.trim()) setName(p.name)
                }
              }}>
              <option value="">add charter preset…</option>
              {presets.filter((p) => !chosen.some((c) => c.name === p.name))
                .map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
            </select>
          )}
          {/* the picked cards live INSIDE the charter box (user spec) — they
              visually ARE part of the charter, compiled to text at hire */}
          <div className="df-charter-wrap">
            {chosen.length > 0 && (
              <div className="preset-cards">
                {chosen.map((c) => (
                  <button key={c.name} className="preset-card"
                    title={c.path ? `${c.path}\n(click to remove)` : 'click to remove'}
                    onClick={() => setChosen((cs) => cs.filter((x) => x.name !== c.name))}>
                    {c.name} <CloseIcon fontSize="inherit" />
                  </button>
                ))}
              </div>
            )}
            <textarea className="df-charter"
              placeholder="charter (optional): standing role notes…"
              value={charter} onChange={(e) => setCharter(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && ok) { e.preventDefault(); hire() } }} />
          </div>
          <div className="df-foot">
            <span className="spacer" />
            <button onClick={onCancel}><CloseIcon fontSize="inherit" /> cancel</button>
            <button className="primary" disabled={!ok} onClick={hire}>
              <CheckIcon fontSize="inherit" /> hire</button>
          </div>
        </div>
      </div>
      {permsOpen && (
        <DraftScopeModal draft={draft} map={map} tree={tree} scope={scope}
          onSave={(s) => { setScope(s); setPermsOpen(false) }}
          close={() => setPermsOpen(false)} />
      )}
    </div>
  )
}
interface NodeSquareProps {
  node: CanvasNode
  pos: Pt
  lod: 'mini' | 'norm'
  focused: boolean
  dragging: boolean
  isDrop: boolean
  seats: Record<string, number>
  map: Map<string, CanvasNode>
  op: OpFn
  slug: string
  toast: ToastFn
  pxc: number
  zoom: number
  onSpawn: (tier: string) => void
  /** F-03: hire a sibling to this side (absent on piles/crowds — see render) */
  onSpawnSide?: (tier: string, side: 'left' | 'right') => void
  onConfig: () => void
  onInbox: () => void
  onLineage: () => void
  onRecenter?: () => void
  onJump?: (id: string) => void
  pub: boolean
  kioskRemaining: number | null
  cascadeAlloc: boolean
  maxTop: number
  pile?: Pile
  compactAt?: number
  maxTier?: string | null
  onMailLink: MailLinkFn
  onDragStart: (e: React.PointerEvent<HTMLDivElement>, id: string) => void
  onDragMove: (e: React.PointerEvent<HTMLDivElement>, id: string) => void
  onDragEnd: (e: React.PointerEvent<HTMLDivElement>, id: string,
    node: CanvasNode, focused: boolean) => void
  onDragCancel: (e: React.PointerEvent<HTMLDivElement>, id: string) => void
}

export function NodeSquare({ node, pos, lod, focused, dragging, isDrop, seats, map, op, slug,
  toast, pxc, zoom, onSpawn, onSpawnSide, onConfig, onInbox, onLineage,
  onRecenter, onJump, pub, kioskRemaining, cascadeAlloc, maxTop, pile, compactAt, maxTier,
  onMailLink, onDragStart, onDragMove, onDragEnd, onDragCancel }: NodeSquareProps) {
  // pile fronts zoom on a plain CENTER click (user spec) — track the
  // pointer-down point so a drag's trailing click doesn't re-zoom
  const downAt = useRef<Pt | null>(null)
  const cls = ['sq', node.state, focused ? 'desk' : lod, 'tier-' + node.tier]
  if (node.busy) cls.push('busy')
  if (dragging) cls.push('lifted')
  if (isDrop) cls.push('drop')
  if (node.bearer_state) cls.push('bearer')
  if (node.limit_locked) cls.push('locked')
  if (node.frozen) cls.push('frozen')
  if (node.scope?.tools?.edit === false) cls.push('ro-agent')
  if (node.audiences_held?.includes(USER)) cls.push('aud-user')
  else if (node.audiences_held?.length) cls.push('aud')
  const stackN = (node.lineage ?? []).length
  if (!focused && stackN) cls.push('stack' + Math.min(stackN, 3))
  const live = node.state === 'live'
  // the card never changes size or place — the desk fades in over it (design
  // ruling). (Every real/bearer card carries the credit trio — only the eye
  // root omits it, and it never renders through NodeSquare — hence the `!`s.)
  const seat = node.seat!, grant = node.grant!, free = node.free!
  const style: React.CSSProperties = {
    transform: `translate(${pos.x}px, ${pos.y}px)`,
    width: NODE_W, height: NODE_H,
    zIndex: focused ? 5 : dragging ? 8 : undefined,
  }
  return (
    <div className={cls.join(' ')} style={style}
      onPointerDown={(e) => {
        downAt.current = { x: e.clientX, y: e.clientY }
        if (!focused) onDragStart(e, node.id)
      }}
      onPointerMove={(e) => onDragMove(e, node.id)}
      onPointerUp={(e) => onDragEnd(e, node.id, node, focused)}
      /* a UA-initiated cancel (touch arbitration, capture loss) must ABORT
         the drag — the end path's no-drop branch commits a reorder POST, so
         routing cancel through it turned a browser gesture cancellation
         into a live org restructure (mobile audit §0; fixed 2026-08-01) */
      onPointerCancel={(e) => onDragCancel(e, node.id)}
      onClick={(e) => {
        // pile front: center click = zoom onto the focused retiree (user
        // spec); margin clicks (the stack) are handled by the layers behind
        if (!pile || focused) return
        if ((e.target as Element).closest('button, input, textarea, select')) return
        const d = downAt.current
        if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) > 5) return
        onRecenter?.()
      }}>
      {live && !node.isBearerOf && (
        <CreditBar seat={seat} grant={grant} committed={grant - free}
          segments={node.children.filter((c) => c.state !== 'archived')
            .map((c) => ({ seat: c.seat!, grant: c.grant! }))}   /* unrecoverable still holds */
          min={grant - free}
          /* reallocate cascades up the chain (§4.6), so the parent's free is
             not a ceiling — unless the cascade_alloc setting turns that off.
             Otherwise the org's global grant cap (or a kiosk's hard credit
             cap) bounds the drag. */
          max={kioskRemaining != null
            ? grant + kioskRemaining
            : cascadeAlloc === false && node.parent !== USER
              ? grant + (map.get(node.parent!)?.free ?? 0)
              : maxTop}
          maxGhost={cascadeAlloc === false && node.parent !== USER}
          onCommit={(delta) => op({ op: 'reallocate', node: node.id, delta })
            .then(() => toast(
              [`${node.id} grant ${delta > 0 ? '+' : ''}${delta}`],
              () => op({ op: 'reallocate', node: node.id, delta: -delta })
                .catch(() => {})))
            .catch(() => {})}
          zoom={zoom} pxc={pxc} />
      )}
      {/* the whole world-scaled head disappears at focus — the desk renders its
          own compact chrome inside the counter-scaled panel (a world-scaled name
          and tier chip blow up to poster size at desk zoom) */}
      {!focused && <div className="sq-head">
        <span className={'tier t-' + node.tier}>{TIER_LETTER[node.tier!] ?? '?'}</span>
        <span className="name" title={node.id}>{node.id}</span>
        <button className={'mailbtn' + ((node.mail_pending ?? 0) > 0 ? ' has' : '')}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onInbox() }}>
          <MailIcon fontSize="inherit" />{(node.mail_pending ?? 0) > 0 && <span className="count">{node.mail_pending}</span>}
        </button>
        {/* ceiling spec §2: visitors retool freely WITHIN the kiosk ceiling —
            the gear is theirs too; the ledger clamps, never a 403 */}
        <button className="gearbtn"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onConfig() }}><SettingsIcon fontSize="inherit" /></button>
        <ContextWheel occ={node.occupancy} cw={node.context_window}
          compactAt={compactAt} />
        {lod === 'mini' && node.last_status &&
          <span className={'statusdot ' + node.last_status.status}
            title={`${node.last_status.status} — ${node.last_status.summary ?? ''}`} />}
        {node.busy && <Activity act={node.activity} dotOnly />}
        {node.last_error && <span className="errdot" title={node.last_error ?? undefined} />}
      </div>}
      {!focused && lod === 'mini' && <div className="mini-name">{node.id}</div>}
      {node.busy && !focused && lod !== 'mini' && <Activity act={node.activity} />}
      {!focused && lod !== 'mini' && (
        <div className="sq-badges">
          {/* no seat/free badges — the credit bar carries all of that */}
          {node.bearer_state
            ? <span className="badge dim">
                {node.state === 'live' ? '' : node.state + ' · '}{node.bearer_state}</span>
            : node.state !== 'live' &&
              <span className="badge dim">{node.state}</span>}
          {node.last_status &&
            <span className={'statuschip ' + node.last_status.status}
              title={node.last_status.summary}>{node.last_status.status}</span>}
          {node.frozen &&
            <span className="badge frozen"
              title={node.frozen.error ?? undefined}><FrozenIcon fontSize="inherit" /> limit</span>}
          {node.limit_locked && <span className="badge dim"><LockIcon fontSize="inherit" /> limit</span>}
          {stackN > 0 &&
            <button className="badge stackbadge"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); onLineage() }}><LayersIcon fontSize="inherit" /> {stackN}</button>}
        </div>
      )}
      {focused && (
        <DeskChat node={node} map={map} op={op} slug={slug}
          toast={toast}
          onLineage={onLineage} onConfig={onConfig} compactAt={compactAt}
          onRecenter={onRecenter} onJump={onJump} pub={pub} onMailLink={onMailLink} />
      )}
      {/* user ruling: chips are NEVER disabled by the node's own free credits —
          a user hire §4.6-cascades, granting the chain whatever it lacks.
          (Kiosk mode will pass the cap remainder here instead.) */}
      {live && !node.isBearerOf && !node.bearer_state &&
        <SpawnChips onSpawn={onSpawn} free={kioskRemaining ?? Infinity} seats={seats}
          maxTier={maxTier} />}
      {/* F-03: side chips hire a COWORKER — same superior, landing on that
          side. Not on pile/crowd fronts: the card's edges there are the
          stack's layers, and "the side of the agent" is not a free position. */}
      {live && !node.isBearerOf && !node.bearer_state && !pile && onSpawnSide && (
        <>
          <SpawnChips side="left" onSpawn={(t) => onSpawnSide(t, 'left')}
            free={kioskRemaining ?? Infinity} seats={seats} maxTier={maxTier} />
          <SpawnChips side="right" onSpawn={(t) => onSpawnSide(t, 'right')}
            free={kioskRemaining ?? Infinity} seats={seats} maxTier={maxTier} />
        </>
      )}
    </div>
  )
}
