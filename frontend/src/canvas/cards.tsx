// canvas/cards.tsx — the canvas's card components: the overseer eye
// (UserNode) with its switchboard (EyeDesk), the hire chips (SpawnChips),
// the drag-adjustable CreditBar, the draft/hiring card (DraftNode), and the
// agent card itself (NodeSquare). Extracted verbatim from Canvas.tsx in the
// phase-3 split.

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { ToastFn, TreePayload } from '../types'
import { audienceAction, getCharters, saveKiosk, unstickNode } from '../api'
import {
  CheckIcon, CloseIcon, FocusIcon, FrozenIcon, LayersIcon,
  LockIcon, MailIcon, RetireIcon, SettingsIcon,
} from '../icons'
import {
  CODEX_TIER_LETTER, CODEX_TIER_SEAT, CODEX_TIERS, DESK_SCALE, deskDpi, DRAFT, familyOffer, freezeKind, FREEZE_LABEL_SHORT, ANTIGRAVITY_TIER_LETTER, ANTIGRAVITY_TIER_SEAT, ANTIGRAVITY_TIERS, NODE_H, NODE_W, providerOf, reserveOffer, TIER_LETTER, TIER_SEAT, TIERS, USER,
  USER_H, USER_W,
} from './shared'
import type {
  AttentionPip, CanvasNode, DraftScope, DraftState, HireState, MailLinkFn, OpFn, Pile,
  Pt,
} from './shared'
import {
  Activity, ContextWheel, DeskChat, DestinationBusy, LastTurnAge,
  ProcessLifecycleMark,
} from './desk'
import { DocChips } from './docs'
import { isMobile } from '../mobile'
import { ConfirmModal, DraftScopeModal } from './modals'

// ------------------------------------------------------------- the overseer
interface UserNodeProps {
  pos: Pt
  isDrop: boolean
  stats: { circ: number; seats: number; free: number }
  /** the inbox badge, already decided (D-169). Passed in rather than
   *  re-derived from counts here: this component and EyeDesk below used to
   *  each write the two-tier rule out by hand, and had already drifted apart
   *  on the tooltip. `attentionPip` owns it now — see canvas/shared.ts. */
  pip: AttentionPip | null
  seats: Record<string, number>
  codexHire?: HireState | null
  antigravityHire?: HireState | null
  claudeHire?: HireState | null
  /** D-199: route out of the no-harness state (opens the accounts panel). */
  onNoHarness?: () => void
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
  maxTop?: number
  /** FR-03: open a presented document in the in-page reader */
  onOpenDoc?: (id: string) => void
  /** per-node lineage/config for the switchboard panel headers (they mirror
   *  the desk header identically — user spec 2026-08-19) */
  onNodeLineage?: (id: string) => void
  onNodeConfig?: (id: string) => void
}

export function UserNode({ pos, isDrop, stats, pip, seats, codexHire, claudeHire, onNoHarness,
  antigravityHire,
  kiosk, pub, kioskRemaining, kioskSegs, pxc, zoom, onInbox, onGear, onSpawn,
  onMailLink,
  focused, eyeW, onFocus, posX, onJump, map, op, slug, toast,
  compactAt, maxTop, onOpenDoc, onNodeLineage, onNodeConfig }: UserNodeProps) {
  const downRef = useRef<Pt | null>(null)
  // const extraction: the kiosk-credits narrowing must survive the commit
  // closure below (a property check alone would not)
  const kioskCredits = kiosk?.credits
  // the eye's hire chips collapse behind the same far-zoom ⋯ toggle as every
  // other node's (NodeSquare's expandedHireEdge) — the eye has one static
  // edge (soleHire), so a plain boolean stands in for that per-edge map.
  // Cleared on zoom change so a cluster can't be left floating after the
  // camera moves, same as NodeSquare.
  const [expandedHire, setExpandedHire] = useState(false)
  useEffect(() => { setExpandedHire(false) }, [zoom])
  return (
    // the mail glow is GONE (user ruling 2026-08-04): unread mail keeps its
    // count badge; the only thing that glows anywhere is an agent that needs
    // the user's answer (see .asking), echoed by the header ask icon
    // static edge-b: the eye only has bottom chips, so the nearest-edge
    // gate always resolves to them
    <div className={'sq user edge-b' + (focused ? ' desk eyeboard' : '')
      + (isDrop ? ' drop' : '')}
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
      {/* two-tier pip (user spec 2026-08-06): open asks outrank unread mail —
          the ask count wears the vibrant pulsing form, plain unread the
          muted one */}
      {!focused && <button className="eye-inbox"
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => { e.stopPropagation(); onInbox?.() }}>
        <MailIcon fontSize="inherit" />
        {pip && <span className={'count' + (pip.urgent ? ' asks' : '')}>
          {pip.count}</span>}
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
      {/* soleHire: the eye carries no side or top sets (see the static edge-b
          above), so its subordinate badge stands alone and needs no role word
          to tell it apart from anything */}
      <SpawnChips onSpawn={onSpawn} free={kioskRemaining ?? Infinity} seats={seats}
        maxTier={kiosk?.max_tier} soleHire codexHire={codexHire}
        claudeHire={claudeHire} onNoHarness={onNoHarness}
        antigravityHire={antigravityHire}
        zoom={focused ? undefined : zoom} expanded={expandedHire}
        onToggleExpanded={() => setExpandedHire((v) => !v)} />
      {focused && (
        <EyeDesk map={map} op={op} slug={slug} toast={toast}
          /* `onFocus` IS `centerOn(USER)` — the very glide an unfocused eye
             gets from the click below. Re-centring is that same action asked
             for again, so it is the same callback, not a second one that
             could drift from it. */
          onRecenter={onFocus}
          pip={pip} onInbox={onInbox}
          onGear={onGear} pub={pub} eyeW={eyeW} posX={posX} onJump={onJump}
          compactAt={compactAt} maxTop={maxTop} pxc={pxc}
          onMailLink={onMailLink} onOpenDoc={onOpenDoc}
          onNodeLineage={onNodeLineage} onNodeConfig={onNodeConfig} />
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
  /** the ✉ badge, already decided — see UserNodeProps.pip above */
  pip: AttentionPip | null
  onInbox?: () => void
  onGear?: () => void
  pub: boolean
  eyeW: number
  posX: (id: string) => number
  onJump?: (id: string) => void
  compactAt?: number
  maxTop?: number
  pxc?: number
  onMailLink: MailLinkFn
  /** FR-03: open a presented document in the in-page reader */
  onOpenDoc?: (id: string) => void
  /** the panel headers mirror the desk header identically (user spec
   *  2026-08-19) — the gen badge and the gear need the same per-node
   *  targets the full desk gets */
  onNodeLineage?: (id: string) => void
  onNodeConfig?: (id: string) => void
  /** user bug 2026-08-26: clicking a focused AGENT's desk re-centres the
   *  camera on it (`.desk-over`'s onClick, desk.tsx). The switchboard is the
   *  eye's desk and already wore the same `.desk-over` class — but it is
   *  built here, separately, and never got the same handler. So it re-centred
   *  only while it was NOT already focused. Same gesture, same result. */
  onRecenter?: () => void
}

export function EyeDesk({ map, op, slug, toast, pip,
  onInbox, onGear, pub, eyeW, posX, onJump, compactAt, maxTop, pxc,
  onMailLink, onOpenDoc, onNodeLineage, onNodeConfig, onRecenter }: EyeDeskProps) {
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
  // Auto-open (user 2026-09-01, OPTIONAL and OFF by default): while the user
  // is actually AT the switchboard — this surface only mounts focused — a NEW
  // direct line (hired with a user audience, or an existing agent granted
  // one) may open its panel immediately instead of arriving minimized, IF one
  // more panel still fits without horizontal scrolling. The first effect run
  // after mount is the catch-up pass for lines that arrived while the camera
  // was away; those keep №24's arrive-minimized rule — "at the same time
  // they're hired" is the user's condition, and a mount is not that moment.
  const [autoOpen, setAutoOpen] = useState<boolean>(() => {
    try { return localStorage.getItem('orgtree-eyeauto-' + slug) === '1' }
    catch { return false }
  })
  const liveRun = useRef(false)
  useEffect(() => {
    const ids = new Set(idsKey ? idsKey.split(',') : [])
    if (seenIds.current) {
      const fresh = [...ids].filter((id) => !seenIds.current!.has(id))
      if (fresh.length) {
        // №24's own minimums — 420px panel min-width, 10px gaps — measured
        // against the same inner width the row lays out in. Conservative on
        // purpose: auto-open must never CAUSE the scroll it is gated on.
        const openNow = agents.filter((a) => !fresh.includes(a.id)
          && !minned.has(a.id)).length
        const fits = (already: number) =>
          (already + 1) * 420 + already * 10 <= innerW - 24
        let opened = 0
        const toMin: string[] = []
        for (const id of fresh) {
          if (liveRun.current && autoOpen && fits(openNow + opened)) opened += 1
          else toMin.push(id)
        }
        if (toMin.length) {
          setMinned((s) => {
            const n = new Set(s)
            toMin.forEach((id) => n.add(id))
            localStorage.setItem('orgtree-eyemin-' + slug, JSON.stringify([...n]))
            return n
          })
        }
      }
    }
    liveRun.current = true
    seenIds.current = ids
    try {
      localStorage.setItem('orgtree-eyeseen-' + slug, JSON.stringify([...ids]))
    } catch { /* private mode */ }
    // agents/minned/innerW/autoOpen are read at the moment a NEW ID LANDS —
    // only the id set may trigger this, or every resize/toggle would re-run
    // the arrival rule on lines that already arrived
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, slug])
  const open = agents.filter((a) => !minned.has(a.id))
  // the inner virtual panel matches the card interior through the desk scale.
  // DESK_SCALE/deskDpi are shared so this stays in step with .desk-inner's
  // transform — the same equation used to be written out here AND in the CSS
  const innerW = Math.round((eyeW - 4) / (DESK_SCALE * deskDpi()))
  return (
    <div className="desk-over eye-desk" onWheel={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
      /* recenter-on-click, character-for-character the guard `.desk-over`
         uses in desk.tsx — never steal a click meant for a control, never
         fight a text selection in progress. Deliberately ONE handler on the
         switchboard root rather than one per panel: the panels are `bare`
         (no overlay wrapper, by design — a second one would double-scale),
         so their clicks bubble to here and land on the eye, which is the
         focused thing. The per-agent jump stays reachable because it is a
         `button` (`.cc-name-jump`) and the guard excludes buttons. */
      onClick={(e) => {
        if ((e.target as Element).closest(
          'button, input, textarea, select, a, label, .mailrow, .eff-pop')) return
        if (window.getSelection()?.toString()) return
        onRecenter?.()
      }}>
      <div className="desk-inner desk-body eye-inner" style={{ width: innerW }}>
        {/* one row (user spec 2026-07-31): the "you · N direct lines" label
            was dead space — the TABS live in the head now, beside the eye */}
        <div className="eye-head">
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
                {a.busy && <DestinationBusy tier={a.tier} />}
                {/* the unread count wears the TAB AGENT's provider — the same
                    hue as its working spinner beside it, never a global tint */}
                {(a.mail_pending ?? 0) > 0 &&
                  <b className={'eye-count prov-' + providerOf(a.tier ?? '')}>
                    {a.mail_pending}</b>}
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
          {/* no spacer here (user bug 2026-08-05): .eye-tabs already has
              flex:1, and a second flex:1 sibling split the header 50/50 so
              the tab strip wrapped at half width */}
          <button className={'cc-icon eye-auto' + (autoOpen ? ' on' : '')}
            title={autoOpen
              ? 'auto-open new direct lines: on — a line hired with (or '
                + 'granted) a user audience opens its panel immediately '
                + 'while another panel still fits without scrolling; '
                + 'click to turn off'
              : 'auto-open new direct lines: off — new lines arrive as '
                + 'minimized tabs; click to turn on'}
            aria-pressed={autoOpen}
            onClick={() => setAutoOpen((v) => {
              const next = !v
              try {
                localStorage.setItem('orgtree-eyeauto-' + slug,
                  next ? '1' : '0')
              } catch { /* private mode */ }
              return next
            })}>auto</button>
          <button className="cc-icon"
            title={pip?.title ?? 'your inbox'}
            onClick={() => onInbox?.()}>
            <MailIcon fontSize="inherit" />
            {pip && <b className={'eye-count' + (pip.urgent ? ' asks' : '')}>
              {pip.count}</b>}
          </button>
          <button className="cc-icon" title="agent-hire defaults"
            onClick={() => onGear?.()}><SettingsIcon fontSize="inherit" /></button>
        </div>
        <div className="eye-panels">
          {open.map((a) => (
            <div className="eye-panel" key={a.id}>
              <DeskChat node={a} map={map} op={op} slug={slug}
                toast={toast} pub={pub} bare compact compactAt={compactAt}
                onJump={onJump} maxTop={maxTop} pxc={pxc} onMailLink={onMailLink}
                onOpenDoc={onOpenDoc}
                onLineage={onNodeLineage ? () => onNodeLineage(a.id) : undefined}
                onConfig={onNodeConfig ? () => onNodeConfig(a.id) : undefined} />
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
   *  COWORKER (same superior, placed to that side), not a report.
   *  FR-25: 'top' is the third variant — a horizontal row above the card
   *  whose hire SPLICES IN as the anchor's new superior. */
  side?: 'left' | 'right' | 'top'
  /** this is the ONLY hire badge on its card, so the tooltip drops the role
   *  word (user 2026-08-28: "under the overseer badge, where only the
   *  subordinate hire badges appear, make the text just say 'hire a haiku
   *  (-1)'"). The role exists to separate three badges sitting side by side;
   *  where one appears alone it names a choice that cannot be got wrong.
   *
   *  ⚠ Passed ONLY by UserNode, deliberately. The overseer is not the only
   *  card that shows a lone subordinate badge — the front of a CROWD pile is
   *  another (live leaf reports of a >8-report team; `pile` suppresses the
   *  side and top sets while the bottom set survives). The user described the
   *  overseer and justified it by the lone badge, and those are not the same
   *  predicate, so this implements what they asked for. If they later want it
   *  general, pass this at the crowd front too — one line, and this comment
   *  is why it was not done unasked. */
  soleHire?: boolean
  /** FR-15 M8: the codex family's hire state, from the /api/providers
   *  payload (threaded from OrgCanvas). undefined = payload not loaded —
   *  degrade to the disabled preview, never to hidden (`familyOffer`). */
  codexHire?: HireState | null
  /** D-189: the antigravity family's hire state, same contract. */
  antigravityHire?: HireState | null
  /** D-199: ...and Claude's, which nothing used to ask for. Same contract:
   *  absent means "not known yet", not "not installed". */
  claudeHire?: HireState | null
  /** D-199: open the accounts panel — the route out of the no-harness state.
   *  Optional: a surface that cannot open it simply renders the badge inert
   *  rather than lying about being clickable. */
  onNoHarness?: () => void
  /** At far map zoom the screen-constant family cluster exceeds its card.
   *  Keep its provider/tier selection intact, but stage it behind one neutral
   *  outward-pointing control until the user asks to see it. */
  /** current canvas scale: compactness is a fit comparison, not a fixed zoom */
  zoom?: number
  expanded?: boolean
  onToggleExpanded?: () => void
}

function SpawnChips({ onSpawn, free, seats, maxTier, side, soleHire,
  codexHire, antigravityHire, claudeHire, onNoHarness, zoom,
  expanded = false, onToggleExpanded }: SpawnChipsProps) {
  // kiosk tier cap (user spec): tokens above the cap DISAPPEAR entirely —
  // seat cost doubles as the tier rank, so the cap is a simple cost compare
  const shown = TIERS.filter((t) =>
    !maxTier || (seats[t] ?? 0) <= (seats[maxTier] ?? Infinity))
  const chip = (t: string, letter: string | undefined) => {
    const seat = seats[t] ?? CODEX_TIER_SEAT[t] ?? ANTIGRAVITY_TIER_SEAT[t] ?? 0
    const cant = Number.isFinite(free) && free < seat
    return (
      <button key={t} disabled={cant} className={'t-' + t}
        title={cant
          // user report: an exhausted kiosk cap read as an opaque dead
          // end — the tooltip now carries the REMEDY, not just the number
          ? `${t}: needs ${seat} free (has ${free}) — the kiosk credit `
            + 'cap is fully held; drag an agent’s credit bar down '
            + 'or retire one to free credits'
          // ONE SHAPE FOR ALL THREE (user request 2026-08-28: "make them
          // more concise; just 3-5 words at most", "for subordinate,
          // superior, and coworker"). They were written at different times
          // and read like it: `hire a haiku (seat 1)` named no role at all,
          // the coworker one appended its placement, the superior one
          // explained the whole splice in twenty words. Now they are
          // `hire <a|an> <tier> <role>` and differ in exactly the one word
          // that differs in meaning — the role. Lowercase imperative to
          // match every other control tooltip on the card (`retire — …`,
          // `dissolve — …`), four words each.
          //
          // The seat cost rides along as `(-N)` — the user's own shape and
          // their own example, after they were asked whether losing it to
          // the word ceiling was acceptable and said it was not. The MINUS
          // is the point: it reads as what this costs you, where the older
          // `(seat 1)` read as a label. It is a suffix, not a word, so the
          // four-word phrase above stays exactly as it is rather than
          // being shortened to make room.
          // ...and where this is the only hire badge on the card, the role
          // word is dropped entirely — see `soleHire`. The cost badge
          // stays: it is the one part that still says something the user
          // cannot read off the badge's position.
          : `hire ${/^[aeiou]/.test(t) ? 'an' : 'a'} ${t}`
            + (soleHire ? ''
              : ` ${side === 'top' ? 'superior' : side ? 'coworker' : 'subordinate'}`)
            + ` (-${seat})`}
        onClick={(e) => { e.stopPropagation(); onSpawn(t) }}>
        {letter}
      </button>
    )
  }
  // D-199: one disabled chip, for a family that IS installed but signed out.
  // The reason is the payload's own (`run codex login`, `run claude once`),
  // so the remedy the user reads here is the remedy the accounts panel and
  // the server's refusal name too.
  const outChip = (t: string, letter: string | undefined, label: string,
                   reason: string | null, seat: number) => (
    <button key={t} disabled className={'t-' + t + ' codex-preview'}
      title={`${t} — ${label}; `
        + (reason ?? 'hiring is not enabled yet') + ` (-${seat})`}>
      {letter}
    </button>
  )
  // PROVIDER ROWS (user spec 2026-08-28): each provider's chips on their own
  // row (own COLUMN on the coworker edges), the families sorted INWARD-TO-
  // OUTWARD by how many model tiers each has available, highest count
  // nearest the card (user refinement 2026-08-28: "sort the provider rows
  // inward-to-outward by number of available model tiers, highest to
  // lowest"). The same inward-first list rendered on opposite edges is what
  // makes top/bottom mirror about x and left/right about y — in DOM terms
  // the list is REVERSED exactly on the edges where "first" points away
  // (top's stack grows upward, left's grows outward).
  //
  // D-199: ONE RULE PER FAMILY, THE SAME ON EVERY STRIP. `familyOffer` decides
  // offer/disable/hide (shared.ts owns it; do not re-derive it here or in
  // anything wrapping this). What this replaced was three different rules:
  // codex and antigravity showed a disabled preview on the subordinate strip but
  // vanished from the side and top strips (`!side`), so one provider was
  // visible on one edge of a card and absent from another — and Claude was
  // never asked at all, which is the bug the user reported.
  //
  // The kiosk holdout is unchanged and still absolute: kiosks hold codex and
  // antigravity out entirely (user ruling — sandboxing unsettled), and the kiosk
  // cap is the one thing that sets maxTier, so it doubles as the kiosk test.
  const fams: { key: string; tiers: string[]; body: ReactNode }[] = []
  const fam = (key: string, tiers: string[], letters: Record<string, string>,
               label: string, hire: HireState | null | undefined,
               seatOf: (t: string) => number,
               kioskHeld = false): void => {
    if (kioskHeld) return
    const offer = familyOffer(hire)
    if (offer === 'hide') return
    fams.push({
      key, tiers,
      // gpt-reserve carries its OWN narrower offer (`reserveOffer`) inside an
      // otherwise-live codex family: reserve capacity is a ChatGPT-only perk,
      // so a keyed Codex session can offer sol/terra/luna while reserve alone
      // renders as the disabled preview, with its own reason.
      body: tiers.map((t) => {
        const tOffer = t === 'gpt-reserve' ? reserveOffer(hire) : offer
        return tOffer === 'offer'
          ? chip(t, letters[t])
          : outChip(t, letters[t], label,
              (t === 'gpt-reserve' ? hire?.reserveReason : null)
                ?? hire?.reason ?? null,
              seatOf(t))
      }),
    })
  }
  // Claude's own list is the kiosk-capped `shown`, not the raw family: the cap
  // removes tiers, the offer rule removes families, and they compose.
  fam('claude', shown, TIER_LETTER, 'Claude', claudeHire,
      (t) => seats[t] ?? TIER_SEAT[t] ?? 0)
  fam('codex', CODEX_TIERS, CODEX_TIER_LETTER, 'Codex', codexHire,
      (t) => seats[t] ?? CODEX_TIER_SEAT[t] ?? 0, !!maxTier)
  fam('antigravity', ANTIGRAVITY_TIERS, ANTIGRAVITY_TIER_LETTER, 'Antigravity',
      antigravityHire,
      (t) => seats[t] ?? ANTIGRAVITY_TIER_SEAT[t] ?? 0, !!maxTier)
  fams.sort((a, b) => b.tiers.length - a.tiers.length)   // inward-first
  const providersOff = [claudeHire, codexHire, antigravityHire]
    .some((h) => h?.userEnabled === false)
  // D-199, the state a brand-new user on a fresh machine hits FIRST: no
  // provider is installed, so every family hid and the strip would render
  // empty. An empty hover strip is indistinguishable from a broken one, so
  // this says what happened and points at the one place that can fix it.
  // Deliberately a `fams` ENTRY rather than a branch around the strip: the
  // far-zoom compact control wraps whatever `fams` produced, so shaping the
  // empty state as a family means that control expands it like any other row
  // instead of collapsing to a dead arrow.
  if (!fams.length && !maxTier)
    fams.push({
      key: 'none', tiers: [],
      body: (
        <button className="hs-none" disabled={!onNoHarness}
          onClick={(e) => { e.stopPropagation(); onNoHarness?.() }}
          title={(providersOff
            ? 'agent providers are off in App settings → Providers'
            : 'no agent harness found on this machine — install or sign in '
              + 'to Claude Code, Codex or Antigravity')
            + (onNoHarness ? ' (opens App settings)' : '')}>
          {providersOff ? 'providers off' : 'no harness'}
        </button>
      ),
    })
  // ...and the residual case that is NOT the no-harness state: a kiosk whose
  // cap has excluded everything. There is nothing to say and nothing to open,
  // so leave the strip out entirely — a compact arrow with no hire choice
  // behind it is worse than an absent affordance.
  // Keep the hooks below unconditional: provider availability can change
  // after its payload loads, including into or out of this empty residual.
  const hasFamilies = fams.length > 0
  const away = side === 'top' || side === 'left'   // "first" points away
  if (away) fams.reverse()
  /* D-200 / user fit rule: counter-scaled badges retain their 22px screen
     width while the card gets narrower with zoom. Collapse only when the
     WIDEST provider row no longer fits inside the actual 124px node panel.
     `fams` is the provider gate's live result, so a one-provider machine gets
     its own later crossover and a no-harness row (tiers: []) never needlessly
     collapses.

     The two figures below mirror the shipped `.hsof button` width and
     `.hs-fam` gap. They are dimensions, not a zoom threshold. A 4px deadband
     avoids a wheel resting on the exact boundary making the control flicker:
     compact enters after a 4px overflow and exits after 4px of free room. */
  const HIRE_BUTTON_PX = 22, HIRE_GAP_PX = 4, FIT_DEADBAND_PX = 4
  const widestFamilyPx = Math.max(0, ...fams.map((f) =>
    f.tiers.length * HIRE_BUTTON_PX + Math.max(0, f.tiers.length - 1) * HIRE_GAP_PX))
  const fitDelta = (zoom == null ? Infinity : NODE_W * zoom - widestFamilyPx)
  const [farCompact, setFarCompact] = useState(() => fitDelta <= 0)
  useEffect(() => {
    setFarCompact((wasCompact) => wasCompact
      ? fitDelta < FIT_DEADBAND_PX
      : fitDelta <= -FIT_DEADBAND_PX)
  }, [fitDelta])
  if (!hasFamilies) return null
  const direction = side === 'left' ? '←' : side === 'right' ? '→'
    : side === 'top' ? '↑' : '↓'
  const rows = (!farCompact || expanded)
    ? fams.map((f) => <div className="hs-fam" key={f.key}>{f.body}</div>)
    : null
  const expand = farCompact && (
    <button className="hire-expand" type="button" aria-expanded={expanded}
      title={expanded ? 'hide hire tiers' : 'show hire tiers'}
      aria-label={expanded ? 'hide hire tiers' : 'show hire tiers'}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => { e.stopPropagation(); onToggleExpanded?.() }}>
      {direction}
    </button>
  )
  return (
    <div className={'hsof' + (side ? ` side side-${side[0]}` : '')
      + (farCompact ? ' hire-compact' : '')
      + (farCompact && expanded ? ' is-expanded' : '')}
      onPointerDown={(e) => e.stopPropagation()}>
      {away && rows}
      {expand}
      {!away && rows}
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
  /** F-05 counter-offer: the agent's CURRENT grant. The tip shows the offer's
   *  ±delta against it and an I-bar brackets the difference, so the size of
   *  the concession is visible rather than arithmetic. */
  baseline?: number
  /** draftMode drag released — the ask card runs its dry-run preview here */
  onRelease?: () => void
}

export function CreditBar({ seat = 0, grant, committed, segments = [], draftMode,
  min = 0, max, maxGhost, onDragValue, onCommit, zoom, pxc, capMode,
  baseline, onRelease }: CreditBarProps) {
  const [drag, setDrag] = useState<{ y0: number; g0: number; val: number } | null>(null)          // {y0, g0, val}
  const cur = drag && !draftMode ? drag.val : grant
  const seatLen = seat * pxc
  const len = Math.max(6, (seat + cur) * pxc)
  const start = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draftMode && !onCommit) return
    // §6 of the mobile spec: drag-to-reallocate is desktop-only by design —
    // precision is ~2px per credit and a finger cannot resolve one credit.
    // Touch devices get the ask card's stepper instead; the bar stays a
    // read-only gauge under a finger.
    if (isMobile) return
    e.stopPropagation(); e.preventDefault()
    setDrag({ y0: e.clientY, g0: grant, val: grant })
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const move = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag) return
    // mobile audit §3.2: a 6px dead zone before the drag stages anything —
    // without it any press that drifted a pixel past a rounding boundary
    // committed a live reallocation on release
    if (Math.abs(e.clientY - drag.y0) < 6) return
    const dg = (drag.y0 - e.clientY) / (pxc * zoom)
    const v = Math.round(Math.max(min, Math.min(max ?? Infinity, drag.g0 + dg)))
    if (draftMode) onDragValue?.(v)
    else {
      setDrag((d) => d && { ...d, val: v })
      // the ask card mirrors the offer in realtime (user report 2026-08-05);
      // canvas bars pass no onDragValue and are untouched
      onDragValue?.(v)
    }
  }
  const end = () => {
    if (!drag) return
    const v = drag.val
    setDrag(null)
    if (!draftMode && v !== grant) onCommit?.(v - grant)
    if (draftMode) onRelease?.()
  }
  /* a UA-initiated cancel (touch scroll arbitration, capture loss) must
     ABORT the drag — routing it through `end` committed a live reallocation
     from a gesture the browser itself abandoned (mobile audit §0 class;
     endNodeDrag got this fix 2026-08-01, this bar was missed) */
  const cancel = () => {
    if (!drag) return
    onDragValue?.(drag.g0)
    setDrag(null)
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
      onPointerUp={end} onPointerCancel={cancel}
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
      {/* F-05: the I-bar spanning current grant → offered grant */}
      {baseline != null && cur !== baseline && (
        <div className={'cbar-ibar' + (cur < baseline ? ' down' : '')}
          style={{ bottom: seatLen + Math.min(baseline, cur) * pxc,
                   height: Math.max(2, Math.abs(cur - baseline) * pxc) }} />
      )}
      <div className="cbar-tip">
        {draftMode && baseline != null ? (
          /* the counter-offer tip: what is offered, vs what the agent holds */
          <>
            <div>offer <b className="n-fill">{grant}</b>
              {grant !== baseline && <span className={grant < baseline ? 'n-down' : 'dim'}>
                {' '}({grant > baseline ? '+' : ''}{grant - baseline})</span>}
            </div>
            <div className="dim">now <b>{baseline}</b></div>
          </>
        ) : draftMode ? (
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
  // A draft already knows its tier, so it also knows its provider. Do not wait
  // for the hire to become a persisted TreeNode before applying provider
  // chrome: otherwise the dashed "uninitialized" Codex card briefly wears
  // Claude terracotta and flips to teal only after creation.
  const providerClass = CODEX_TIERS.includes(draft.tier) ? ' prov-openai'
    : ANTIGRAVITY_TIERS.includes(draft.tier) ? ' prov-google' : ''
  return (
    <div className={'sq draft' + providerClass} style={{
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
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && ok && !isMobile) { e.preventDefault(); hire() } }} />
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
  codexHire?: HireState | null
  antigravityHire?: HireState | null
  claudeHire?: HireState | null
  /** D-199: route out of the no-harness state (opens the accounts panel). */
  onNoHarness?: () => void
  map: Map<string, CanvasNode>
  op: OpFn
  slug: string
  toast: ToastFn
  pxc: number
  zoom: number
  onSpawn: (tier: string) => void
  /** F-03: hire a sibling to this side (absent on piles/crowds — see render) */
  onSpawnSide?: (tier: string, side: 'left' | 'right') => void
  /** FR-25: the top-edge chips — hire a new SUPERIOR spliced above this node */
  onSpawnTop?: (tier: string) => void
  onConfig: () => void
  onInbox: () => void
  onLineage: () => void
  /** FR-03: open a presented document in the in-page reader */
  onOpenDoc?: (id: string) => void
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
  /** compact map tier (mobile wave §5.1): the card renders as a MAP marker —
   *  tier block, name, status, last-turn stamp — no desk, no chips, no drag.
   *  Taps are arbitrated by the viewport (the sheet opens there). */
  mapMode?: boolean
  /** D-125 ②: watchdogs hide from the compact map; the owner card carries
   *  their count as a dot instead */
  dogs?: number
  /** D-200: compact maps still need to expose that part of the count is a
   * finite one-shot dog, even though the individual satellites are hidden. */
  oneShotDogs?: number
}

export function NodeSquare({ node, pos, lod, focused, dragging, isDrop, seats, codexHire, antigravityHire, claudeHire, onNoHarness, map, op, slug,
  toast, pxc, zoom, onSpawn, onSpawnSide, onSpawnTop, onConfig, onInbox, onLineage, onOpenDoc,
  onRecenter, onJump, pub, kioskRemaining, cascadeAlloc, maxTop, pile, compactAt, maxTier,
  onMailLink, onDragStart, onDragMove, onDragEnd, onDragCancel,
  mapMode, dogs, oneShotDogs }: NodeSquareProps) {
  // pile fronts zoom on a plain CENTER click (user spec) — track the
  // pointer-down point so a drag's trailing click doesn't re-zoom
  const downAt = useRef<Pt | null>(null)
  // retire from the CARD (user request 2026-08-17): the seat-freeing action
  // no longer requires zooming to the desk — same confirm + undo-toast flow,
  // same retire/dissolve split as the desk's cc-actions
  const [asking, setAsking] = useState<'dissolve' | 'retire' | null>(null)
  const liveKids = node.children.some((c) => c.state === 'live')
  // NEAREST-EDGE chip gating (user ruling 2026-08-04): only the set at the
  // edge the cursor is closest to shows — bottom hires a report, left/right
  // hire a coworker, top (FR-25) inserts a superior. Tracked here from the
  // card's own pointer moves; normalized distances so the card's aspect
  // ratio doesn't bias the pick.
  const [edge, setEdge] = useState<'b' | 'l' | 'r' | 't'>('b')
  const [expandedHireEdge, setExpandedHireEdge] =
    useState<'b' | 'l' | 'r' | 't' | null>(null)
  // A cluster cannot remain floating over a card after the camera moves. This
  // also clears it when zooming back into the unchanged, direct-chip path.
  useEffect(() => { setExpandedHireEdge(null) }, [zoom])
  const trackEdge = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    if (!r.width || !r.height) return
    const x = (e.clientX - r.left) / r.width
    const y = (e.clientY - r.top) / r.height
    const d = Math.min(x, 1 - x, 1 - y, y)
    const next = d === 1 - y ? 'b' : d === y ? 't' : d === x ? 'l' : 'r'
    if (edge !== next) setExpandedHireEdge(null)
    setEdge((cur) => cur === next ? cur : next)
  }
  const live = node.state === 'live'
  const cls = ['sq', node.state, focused ? 'desk' : lod, 'tier-' + node.tier,
               'edge-' + edge]
  // provider theming (user spec 2026-08-28): codex agents wear an
  // blue accent — desk border/shadow and busy ring — where claude
  // wears terracotta. Dormant until codex hire lands; keyed on the tier
  // family so it needs no new payload field.
  if (node.tier && CODEX_TIERS.includes(node.tier)) cls.push('prov-openai')
  if (node.tier && ANTIGRAVITY_TIERS.includes(node.tier)) cls.push('prov-google')
  if (live) cls.push(node.proc_warm ? 'proc-warm' : 'proc-cold')
  if (node.busy) cls.push('busy')
  // api_fallback (user feature 2026-08-19): a turn RUNNING on the org's own
  // API key wears the same red as the canvas border. No `busy` companion
  // check on purpose — the server writes this flag at spawn and clears it in
  // the turn's finally, and it lives in memory only, so it cannot outlast the
  // turn it describes (nor survive a backend restart).
  if (node.on_fallback) cls.push('onfallback')
  if (dragging) cls.push('lifted')
  if (isDrop) cls.push('drop')
  if (node.bearer_state) cls.push('bearer')
  if (node.limit_locked) cls.push('locked')
  if (node.frozen) cls.push('frozen')
  if (node.scope?.tools?.edit === false) cls.push('ro-agent')
  // aura semantics reworked (user ruling 2026-08-04): the bright terracotta
  // glow now means ONE thing — this agent needs the user's attention (an open
  // ask). Holding a user audience is a capability, not an emergency: it wears
  // the same soft steel as any other audience.
  if (node.ask && (node.ask.status === 'open' || node.ask.status === 'pending')) {
    cls.push('asking')
  }
  if (node.audiences_held?.length) cls.push('aud')
  const stackN = (node.lineage ?? []).length
  if (!focused && stackN) cls.push('stack' + Math.min(stackN, 3))
  const toggleCompactHire = (which: 'b' | 'l' | 'r' | 't') =>
    setExpandedHireEdge((open) => open === which ? null : which)
  // FR-23: the most recent completed turn (killed included — TurnStat.at is
  // written unconditionally at completion, unlike NodeStatus.at)
  const lastTurn = node.turns?.[node.turns.length - 1]
  // the card never changes size or place — the desk fades in over it (design
  // ruling). (Every real/bearer card carries the credit trio — only the eye
  // root omits it, and it never renders through NodeSquare — hence the `!`s.)
  const seat = node.seat!, grant = node.grant!, free = node.free!
  const style: React.CSSProperties = {
    transform: `translate(${pos.x}px, ${pos.y}px)`,
    width: NODE_W, height: NODE_H,
    zIndex: focused ? 5 : dragging ? 8 : undefined,
  }
  // compact MAP tier (mobile wave, D-123/D-125): the card is a locator, not a
  // work surface — the desk lives in the full-screen sheet. Tier block, name,
  // status, last-turn stamp (FR-23 stays glanceable), watchdog count-dot
  // (D-125 ②). NO pointer handlers: the viewport arbitrates taps (a finger
  // that lands on a card must still be able to pan), and drag is hidden at
  // compact by design (§6). The `asking` aura class rides `cls` unchanged.
  if (mapMode) {
    const stat = node.last_status
    return (
      <div className={cls.join(' ') + ' maplod'} style={style}>
        <div className="map-top">
          <span className={'tier t-' + node.tier}>{TIER_LETTER[node.tier!] ?? '?'}</span>
          {node.busy
            ? <span className="statusdot waiting" />
            : node.frozen ? <FrozenIcon fontSize="inherit" className="tray-frozen" />
            : node.state !== 'live' ? <span className="map-off">{node.state}</span>
            : stat ? <span className={'statusdot ' + stat.status} />
            : <span className="statusdot idle" />}
          {live && <ProcessLifecycleMark warm={Boolean(node.proc_warm)}
            live={node.proc_live} relaunch={node.proc_relaunch}
            reason={node.proc_relaunch_reason} busy={node.busy} tier={node.tier} />}
          {(dogs ?? 0) > 0 && <span className={'map-dogs' + ((oneShotDogs ?? 0) > 0 ? ' oneshot' : '')}
            aria-label={`${dogs} watchdog${dogs === 1 ? '' : 's'}${(oneShotDogs ?? 0) > 0
              ? `, ${oneShotDogs} one-shot dog${oneShotDogs === 1 ? '' : 's'}` : ''}`}>
            ◉{dogs}{(oneShotDogs ?? 0) > 0 && <small>1×{oneShotDogs}</small>}
          </span>}
        </div>
        <span className="map-name">{node.id}</span>
        <LastTurnAge turn={lastTurn} busy={node.busy} variant="map" />
      </div>
    )
  }
  return (
    <div className={cls.join(' ')} style={style}
      onPointerDown={(e) => {
        downAt.current = { x: e.clientX, y: e.clientY }
        if (!focused) onDragStart(e, node.id)
      }}
      onPointerMove={(e) => { trackEdge(e); onDragMove(e, node.id) }}
      onPointerUp={(e) => onDragEnd(e, node.id, node, focused)}
      onPointerLeave={() => setExpandedHireEdge(null)}
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
          <MailIcon fontSize="inherit" />{(node.mail_pending ?? 0) > 0 &&
            <span className={'count prov-' + providerOf(node.tier ?? '')}>{node.mail_pending}</span>}
        </button>
        {/* retire without the zoom-in (user request 2026-08-17): hover-revealed
            like the gear/mail, confirm-gated like the desk button. Wears the
            desk's retire/dissolve split so it is never a dead control. */}
        {live && !node.isBearerOf && !node.bearer_state &&
          <button className="retirebtn"
            title={liveKids
              ? `dissolve — retire ${node.id} and its whole suborganization, freeing ${(node.seat ?? 0) + (node.grant ?? 0)} credit(s)`
              : `retire — frees ${(node.seat ?? 0) + (node.grant ?? 0)} credit(s); context kept, rehire brings it back`}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation()
              setAsking(liveKids ? 'dissolve' : 'retire')
            }}><RetireIcon fontSize="inherit" /></button>}
        {/* ceiling spec §2: visitors retool freely WITHIN the kiosk ceiling —
            the gear is theirs too; the ledger clamps, never a 403 */}
        <button className="gearbtn"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onConfig() }}><SettingsIcon fontSize="inherit" /></button>
        <ContextWheel occ={node.occupancy} cw={node.context_window}
          est={node.occupancy_est} compactAt={compactAt} />
        {live && <ProcessLifecycleMark warm={Boolean(node.proc_warm)}
          live={node.proc_live} relaunch={node.proc_relaunch}
          reason={node.proc_relaunch_reason} busy={node.busy} tier={node.tier} />}
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
          {/* LIFECYCLE AND PROVENANCE ARE TWO FACTS AND GET TWO CHIPS (user
              report 2026-08-28: "it looks retired… the ui is too similar; it
              needs to look more like a normal agent"). These used to share one
              `badge dim`, with the bearer state STANDING IN for the lifecycle
              state whenever it existed: a live bearer's only chip read
              `knowledge`, in the same grey, in the same slot where every other
              card says `archived`. So a rehired bearer mid-turn was labelled
              as though `knowledge` were its state. Now the lifecycle chip
              appears on exactly the cards that are not live — bearer or not —
              and the bearer mark is its own quieter, outlined chip beside it.
              An archived bearer therefore still shows `archived`, and shows it
              first. */}
          {node.state !== 'live' &&
            <span className="badge dim">{node.state}</span>}
          {node.bearer_state &&
            <span className="badge bearermark"
              title={`${node.bearer_state} bearer — where this agent's context `
                + 'came from, not what it is doing; a rehired bearer works '
                + 'like any other agent'}>{node.bearer_state}</span>}
          {node.last_status &&
            <span className={'statuschip ' + node.last_status.status}
              title={node.last_status.summary}>{node.last_status.status}</span>}
          {/* FR-23 (user request 2026-08-09): the end of the most recent turn,
              glanceable on the CANVAS — the desk already had it, hover-gated,
              and that surfacing evidently wasn't enough or the request would
              not exist. Source: TurnStat.at (written unconditionally at turn
              completion, killed turns included) — NOT NodeStatus.at, which
              only exists when the agent chose to report a status. Hidden
              while busy (the activity dot owns that state; "3m ago" under a
              running turn reads as a contradiction) and absent when no turn
              ever ran (a fresh hire shows nothing rather than "never"). */}
          <LastTurnAge turn={lastTurn} busy={node.busy} />
          {/* ⭐ clickable (user ruling 2026-08-06): the freeze badge IS the
              per-node unstick — the control lives where the user finds the
              agent, not only in org-level panels */}
          {node.frozen &&
            <button className="badge frozen"
              title={(node.frozen.error ? node.frozen.error + ' — ' : '')
                + 'click to UNSTICK (user override: releases every lock '
                + 'and resumes)'}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation()
                if (pub) return
                unstickNode(slug, node.id)
                  .then((r) => toast([r.released?.length
                    ? `${node.id} unstuck (${r.released.join(', ')})`
                    : (r.status ?? 'nothing to release'),
                    ...(r.warnings ?? [])]))
                  .catch((e2: Error) => toast([`error: ${e2.message}`]))
              }}><FrozenIcon fontSize="inherit" />{' '}
              {FREEZE_LABEL_SHORT[freezeKind(node.frozen, node.limit_locked) ?? 'limit']}</button>}
          {node.remote_controlled &&
            <span className="badge frozen"
              title="the user is driving this session from another device — mail queues until release (gear panel)">
              remote</span>}
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
          onRecenter={onRecenter} onJump={onJump} maxTop={maxTop} pxc={pxc}
          pub={pub} onMailLink={onMailLink} onOpenDoc={onOpenDoc} />
      )}
      {/* user ruling: chips are NEVER disabled by the node's own free credits —
          a user hire §4.6-cascades, granting the chain whatever it lacks.
          (Kiosk mode will pass the cap remainder here instead.) */}
      {live && !node.isBearerOf && !node.bearer_state &&
        <SpawnChips onSpawn={onSpawn} free={kioskRemaining ?? Infinity} seats={seats}
          maxTier={maxTier} codexHire={codexHire} antigravityHire={antigravityHire}
          claudeHire={claudeHire} onNoHarness={onNoHarness}
          zoom={focused ? undefined : zoom} expanded={expandedHireEdge === 'b'}
          onToggleExpanded={() => toggleCompactHire('b')} />}
      {/* FR-03: presented documents pop out the card's side as square icon
          chips — click opens the in-page reader. Not at desk zoom (the desk
          HEADER carries titled doc badges instead — world-scaled side chips
          blow up) and not on pile fronts (the side is the stack). */}
      {!focused && !pile && (node.documents?.length ?? 0) > 0 && onOpenDoc && (
        <DocChips docs={node.documents!} onOpen={onOpenDoc} />
      )}
      {/* F-03: side chips hire a COWORKER — same superior, landing on that
          side. Not on pile/crowd fronts: the card's edges there are the
          stack's layers, and "the side of the agent" is not a free position. */}
      {live && !node.isBearerOf && !node.bearer_state && !pile && onSpawnSide && (
        <>
          {/* transparent hover bridges (user report 2026-08-28). The columns
              now sit beyond the credit bar and the doc chips so they cannot
              cover them at any zoom — which puts a strip of empty canvas
              between card and chips, and the chips only exist while the card
              is hovered. Without these the chips would blink out as the cursor
              crossed that strip and the hire gesture would become the new
              unreachable thing. Pure hit area: no paint, under the bar and the
              doc chips, so they take nothing else's clicks. */}
          <div className="hsof-bridge bridge-l" aria-hidden="true" />
          <div className="hsof-bridge bridge-r" aria-hidden="true" />
          <SpawnChips side="left" onSpawn={(t) => onSpawnSide(t, 'left')}
            free={kioskRemaining ?? Infinity} seats={seats} maxTier={maxTier}
            codexHire={codexHire} antigravityHire={antigravityHire}
            claudeHire={claudeHire} onNoHarness={onNoHarness}
            zoom={focused ? undefined : zoom} expanded={expandedHireEdge === 'l'}
            onToggleExpanded={() => toggleCompactHire('l')} />
          <SpawnChips side="right" onSpawn={(t) => onSpawnSide(t, 'right')}
            free={kioskRemaining ?? Infinity} seats={seats} maxTier={maxTier}
            codexHire={codexHire} antigravityHire={antigravityHire}
            claudeHire={claudeHire} onNoHarness={onNoHarness}
            zoom={focused ? undefined : zoom} expanded={expandedHireEdge === 'r'}
            onToggleExpanded={() => toggleCompactHire('r')} />
        </>
      )}
      {/* FR-25: top-edge chips SPLICE a new superior above this node — the
          draft takes this card's slot immediately (anchor hangs beneath it,
          dashed both ways), and the confirmed hire splices in server-side
          atomically. Same pile/bearer exclusions as the side chips. */}
      {live && !node.isBearerOf && !node.bearer_state && !pile && onSpawnTop && (
        <SpawnChips side="top" onSpawn={(t) => onSpawnTop(t)}
          free={kioskRemaining ?? Infinity} seats={seats} maxTier={maxTier}
          codexHire={codexHire} antigravityHire={antigravityHire}
          claudeHire={claudeHire} onNoHarness={onNoHarness}
          zoom={focused ? undefined : zoom} expanded={expandedHireEdge === 't'}
          onToggleExpanded={() => toggleCompactHire('t')} />
      )}
      {/* portal to <body>: the card lives inside the world transform, where
          position:fixed would resolve against the scaled ancestor (same
          reason DraftScopeModal portals). Bodies + undo toast mirror the
          desk's confirms verbatim. */}
      {asking === 'dissolve' && createPortal(
        <ConfirmModal title={`dissolve ${node.id}?`}
          body="Its entire suborganization is retired with it. Context is kept; rehire brings nodes back."
          confirmLabel="dissolve"
          onConfirm={() => op({ op: 'dissolve', node: node.id })}
          close={() => setAsking(null)} />, document.body)}
      {asking === 'retire' && createPortal(
        <ConfirmModal title={`retire ${node.id}?`}
          body={`It stops working and frees ${(node.seat ?? 0) + (node.grant ?? 0)} credit(s) back to its superior. Its context is KEPT — rehire brings it back exactly as it was.`
            + (node.busy ? ' ⚠ It is mid-turn right now; that turn is cut off.' : '')}
          confirmLabel="retire"
          onConfirm={() => op({ op: 'retire', node: node.id }).then(() =>
            toast([`${node.id} retired`],
              () => op({ op: 'rehire', node: node.id }).catch(() => {})))
            .catch(() => {})}
          close={() => setAsking(null)} />, document.body)}
    </div>
  )
}
