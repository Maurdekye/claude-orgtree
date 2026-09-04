// canvas/pins.tsx — a desk PINNED TO SCREENSPACE (FR-3, user spec 2026-09-04):
// "a pin button for desk views, which detaches them from the canvas and pins
// them to screenspace (they don't move when dragging the canvas anymore) and
// lets me drag them around the screen and resize them; they effectively
// become manually positionable windows like in an operating system. Unpinning
// them minimizes them back to their respective agents' desk view locations."
//
// ⚠ THIS IS A COORDINATE-SPACE PROBLEM WEARING A UI COSTUME. Two spaces meet
// here and every bug in the feature comes from confusing them:
//
//   WORLD px  — where cards live. `.space` is `translate(view.x, view.y)
//               scale(view.z)`, and a card at world (p.x, p.y) is on screen at
//               (p.x * view.z + view.x, p.y * view.z + view.y). Pans and zooms
//               move everything in this space.
//   VIEWPORT px — the `.viewport` box's own coordinates, origin at ITS top-left
//               (not the window's: the top bar and org bar sit above it). The
//               zoomhud, the edge-jump cards and the tray live here, as
//               screen-space SIBLINGS of `.space`, and so does a pinned window.
//
// EVERY NUMBER IN THIS FILE IS IN VIEWPORT PX unless its name says otherwise.
// A pinned window is immune to pan and zoom BY CONSTRUCTION — it renders
// outside the `.space` transform — not by compensating math that can drift.
// The only world→viewport conversion is the one OrgCanvas hands in through
// `targetOf` (where the agent's card is on screen right now), used twice: to
// place a fresh pin over the desk it detached from, and to fly the minimise
// ghost home on unpin. Both are read AT THAT MOMENT, never stored: the card's
// position is derived from the tree by `layout()` and can move at any time.
//
// What persists (localStorage `orgtree-pins-<slug>`): the ordered array of
// {id, rect, z, snap}. Per-slug because it holds node ids, exactly like
// `orgtree-pile-<slug>`. It joins OrgCanvas's id-keyed storage sweep through
// `prunePins`. Snapping/mosaic is a LATER stage; what stage 1 fixes so stage 2
// stays cheap: geometry is a rect and only a rect; `snap: null` is in the
// persisted shape from day one (no migration later); one commit point
// (`commitRect`) that stage 2 fills with `resolveSnap`; min sizes; and a
// stable array order (mosaic tiles in array order).
//
// USER RULING 2026-09-04 — "PINNED MEANS PINNED": one live desk per agent,
// always. A pinned node is excluded from the camera's `focusId` in OrgCanvas,
// so zooming onto its card shows a placeholder, never a second desk. Two
// mounted desks for one node would share one `orgtree-draft-<slug>-<nid>`
// composer key (desk.tsx) and silently fight over it.

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent, RefObject } from 'react'
import PushPinIcon from '@mui/icons-material/PushPin'
import { CloseIcon } from '../icons'
import { DeskChat } from './desk'
import { providerOf, TIER_LETTER } from './shared'
import type { CanvasNode, MailLinkFn, OpFn } from './shared'
import type { ToastFn } from '../types'

// ------------------------------------------------------------------ types
/** a window's box, in VIEWPORT px (origin = the .viewport's top-left) */
export interface PinRect { x: number; y: number; w: number; h: number }
export interface Pin {
  id: string
  /** viewport px — see the header comment */
  rect: PinRect
  /** stacking ordinal, renormalized to 0..n-1 on every raise; the CSS z-index
   *  is derived from it inside a reserved band (see zIndexOf) */
  z: number
  /** stage 2 seam: which edge/corner this window is snapped to. Always null
   *  in stage 1, present so the persisted shape needs no migration later. */
  snap: null
}

/** a window smaller than this is not a usable desk; mosaic (stage 2) must
 *  honour the same floor or it tiles unusable slivers */
export const PIN_MIN_W = 320
export const PIN_MIN_H = 240
/** the title bar's height (CSS `.pinwin-title`); clamping keeps at least this
 *  much of the window reachable */
export const PIN_TITLE_H = 28
/** how much of the title bar must stay inside the viewport after any gesture
 *  or viewport resize, so a window can always be grabbed back */
export const PIN_GRAB_PX = 64
/** ⚠ PLACEHOLDER, not a measurement (plan OQ-4). Every pinned window is a live
 *  chat poller (useConvo, 2.5s while busy) against a backend whose tree fetch
 *  has measured 11–38s under load and a browser capped at ~6 sockets per
 *  origin. The cap is decided in stage 1 because adding one later means
 *  telling users their saved layout is now illegal. Re-derive from a measured
 *  per-window chat-fetch cost before trusting the number. */
export const PIN_MAX = 8
/** z-index band reserved for pinned windows: above the zoomhud/edge-jump
 *  cards (7/8), below the drawer (18) and every modal (20+). Hard-clamped: a
 *  50-window org must not creep into modal territory. */
export const PIN_Z_BASE = 10
export const PIN_Z_TOP = 16
export const zIndexOf = (z: number): number =>
  Math.min(PIN_Z_TOP, PIN_Z_BASE + Math.max(0, z))
/** how long the minimise ghost flies on unpin (CSS transition, ms) */
export const PIN_GHOST_MS = 220

export const pinsKey = (slug: string) => `orgtree-pins-${slug}`

// ------------------------------------------------------------------ store
// One module-level store keyed by slug, localStorage-backed, exposed through
// useSyncExternalStore — the same contract as shared.ts's start-view store.
// Snapshots are the arrays themselves, replaced (never mutated) on write, so a
// wake that changes nothing is a render React bails out of.
const cache = new Map<string, Pin[]>()
const subs = new Set<() => void>()
const notify = () => { for (const fn of [...subs]) fn() }
const EMPTY: Pin[] = []

const isRect = (r: unknown): r is PinRect => {
  if (!r || typeof r !== 'object') return false
  const o = r as Record<string, unknown>
  return ['x', 'y', 'w', 'h'].every((k) => typeof o[k] === 'number' && Number.isFinite(o[k]))
}
const isPin = (p: unknown): p is Pin => {
  if (!p || typeof p !== 'object') return false
  const o = p as Record<string, unknown>
  return typeof o.id === 'string' && isRect(o.rect) && typeof o.z === 'number'
}

/** the pins this browser holds for `slug`, read once from storage then cached.
 *  A hand-edited or foreign value reads as no pins — never as a throw. */
export const readPins = (slug: string): Pin[] => {
  const hit = cache.get(slug)
  if (hit) return hit
  let out: Pin[] = EMPTY
  try {
    const raw = localStorage.getItem(pinsKey(slug))
    if (raw) {
      const arr = JSON.parse(raw) as unknown
      if (Array.isArray(arr)) {
        out = arr.filter(isPin).map((p) => ({ id: p.id, rect: { ...p.rect }, z: p.z, snap: null }))
      }
    }
  } catch { /* private mode, or garbage — same answer */ }
  cache.set(slug, out)
  return out
}
const writePins = (slug: string, next: Pin[]): void => {
  cache.set(slug, next)
  try {
    if (next.length) localStorage.setItem(pinsKey(slug), JSON.stringify(next))
    else localStorage.removeItem(pinsKey(slug))
  } catch { /* private mode */ }
  notify()
}
/** drop the cached copy so the next read comes from storage again — for a
 *  `storage` event from another tab, and for tests that clear localStorage */
export const forgetPins = (slug?: string): void => {
  if (slug) cache.delete(slug); else cache.clear()
  notify()
}
const subscribe = (fn: () => void): (() => void) => {
  subs.add(fn)
  const onStorage = (e: StorageEvent) => {
    if (e.key == null || e.key.startsWith('orgtree-pins-')) {
      if (e.key) cache.delete(e.key.slice('orgtree-pins-'.length)); else cache.clear()
      fn()
    }
  }
  window.addEventListener('storage', onStorage)
  return () => { subs.delete(fn); window.removeEventListener('storage', onStorage) }
}
export const usePins = (slug: string): Pin[] =>
  useSyncExternalStore(subscribe, () => readPins(slug))

// z ordinals renormalized to 0..n-1 by current order, `top` last
const renorm = (pins: Pin[], top?: string): Pin[] => {
  const order = [...pins].sort((a, b) => a.z - b.z)
  if (top) {
    const i = order.findIndex((p) => p.id === top)
    if (i >= 0) order.push(...order.splice(i, 1))
  }
  const zOf = new Map(order.map((p, i) => [p.id, i]))
  // keep ARRAY order (mosaic order, stage 2) — only the ordinals change
  return pins.map((p) => ({ ...p, z: zOf.get(p.id)! }))
}

/** pin `id` at `rect` (already clamped by the caller). Returns false, with
 *  a reason, when refused — already pinned, or at the cap. */
export const addPin = (slug: string, id: string, rect: PinRect):
  { ok: true } | { ok: false; reason: string } => {
  const pins = readPins(slug)
  if (pins.some((p) => p.id === id)) return { ok: false, reason: `${id} is already pinned` }
  if (pins.length >= PIN_MAX) {
    return { ok: false, reason: `${PIN_MAX} windows are already pinned — unpin one first` }
  }
  const next = renorm([...pins, { id, rect: sizeFloor(rect), z: pins.length, snap: null }], id)
  writePins(slug, next)
  return { ok: true }
}
export const removePin = (slug: string, id: string): void => {
  const pins = readPins(slug)
  if (!pins.some((p) => p.id === id)) return
  writePins(slug, renorm(pins.filter((p) => p.id !== id)))
}
/** bring `id` to the front of the band */
export const raisePin = (slug: string, id: string): void => {
  const pins = readPins(slug)
  if (!pins.length || !pins.some((p) => p.id === id)) return
  const top = pins.reduce((m, p) => (p.z > m.z ? p : m), pins[0]!)
  if (top.id === id) return
  writePins(slug, renorm(pins, id))
}
export const isPinned = (slug: string, id: string): boolean =>
  readPins(slug).some((p) => p.id === id)

/** stage 2 fills this: rect → rect, honouring edge/corner snap zones.
 *  Stage 1: the identity. Every geometry commit passes through here. */
export const resolveSnap = (r: PinRect): PinRect => r

export const sizeFloor = (r: PinRect): PinRect =>
  ({ ...r, w: Math.max(PIN_MIN_W, r.w), h: Math.max(PIN_MIN_H, r.h) })

/** keep at least PIN_GRAB_PX of the title bar inside a `vp`-sized viewport.
 *  `vp` null = the viewport is unmeasured (jsdom, or before first layout):
 *  clamping against 0×0 would pile every window at the origin, so it is
 *  skipped and the rect passes through unchanged. */
export const clampRect = (r: PinRect, vp: { w: number; h: number } | null): PinRect => {
  const s = sizeFloor(r)
  if (!vp || vp.w <= 0 || vp.h <= 0) return s
  const x = Math.min(Math.max(s.x, PIN_GRAB_PX - s.w), vp.w - PIN_GRAB_PX)
  const y = Math.min(Math.max(s.y, 0), Math.max(0, vp.h - PIN_TITLE_H))
  return { ...s, x, y }
}

/** THE ONE COMMIT POINT for window geometry (drag end, resize end, raise-and-
 *  show, and stage 2's snap/mosaic): resolveSnap → clamp → write. One
 *  localStorage write per gesture, never per frame. */
export const commitRect = (slug: string, id: string, rect: PinRect,
  vp: { w: number; h: number } | null): void => {
  const pins = readPins(slug)
  if (!pins.some((p) => p.id === id)) return
  const next = clampRect(resolveSnap(rect), vp)
  writePins(slug, pins.map((p) => (p.id === id ? { ...p, rect: next } : p)))
}

/** the id-keyed sweep: drop pins whose node is gone. Returns the ids dropped
 *  so the caller can say so. The caller guards the mismatched-props window
 *  (`tree.slug !== slug`) and the empty-tree case — copy those, do not
 *  reimplement them here. */
export const prunePins = (slug: string, has: (id: string) => boolean): string[] => {
  const pins = readPins(slug)
  const gone = pins.filter((p) => !has(p.id)).map((p) => p.id)
  if (gone.length) writePins(slug, renorm(pins.filter((p) => has(p.id))))
  return gone
}

// an ephemeral "look here" pulse: the card placeholder's click raises the
// window and flashes its border. Not persisted, not part of the pin.
const pulses = new Map<string, number>()
const pulseSubs = new Set<() => void>()
export const pulsePin = (slug: string, id: string): void => {
  const k = `${slug}/${id}`
  pulses.set(k, (pulses.get(k) ?? 0) + 1)
  for (const fn of [...pulseSubs]) fn()
}
const subscribePulse = (fn: () => void) => {
  pulseSubs.add(fn); return () => { pulseSubs.delete(fn) }
}
const usePulse = (slug: string, id: string): number =>
  useSyncExternalStore(subscribePulse, () => pulses.get(`${slug}/${id}`) ?? 0)

/** "show window" — the card placeholder's click. Raise, pull the window back
 *  inside the viewport (a window the user resized off-screen or that another
 *  window covers would otherwise appear to do nothing on raise), and flash.
 *  Never unpins. */
export const showPin = (slug: string, id: string, vp: { w: number; h: number } | null): void => {
  const pin = readPins(slug).find((p) => p.id === id)
  if (!pin) return
  raisePin(slug, id)
  commitRect(slug, id, pin.rect, vp)
  pulsePin(slug, id)
}

// ---------------------------------------------------------------- unpin plan
/** where a window goes when unpinned. `target` is the agent's card rect in
 *  viewport px right now (null: the node is gone from the tree). Pure so it
 *  can be tested with explicit numbers — jsdom measures every box as 0×0. */
export type UnpinPlan =
  | { kind: 'onscreen'; to: PinRect }
  | { kind: 'offscreen'; to: PinRect }
  | { kind: 'gone' }
export const planUnpin = (target: PinRect | null,
  vp: { w: number; h: number } | null): UnpinPlan => {
  if (!target) return { kind: 'gone' }
  if (!vp || vp.w <= 0 || vp.h <= 0) return { kind: 'onscreen', to: target }
  const visible = target.x + target.w > 0 && target.x < vp.w
    && target.y + target.h > 0 && target.y < vp.h
  return { kind: visible ? 'onscreen' : 'offscreen', to: target }
}

// --------------------------------------------------------------- component
export interface PinLayerProps {
  slug: string
  map: Map<string, CanvasNode>
  /** the .viewport element — the coordinate origin of every rect here */
  viewportRef: RefObject<HTMLDivElement | null>
  /** the agent's card rect in VIEWPORT px right now, or null if it has no
   *  position (gone from the tree). World→viewport happens in OrgCanvas,
   *  which owns the camera; this file never sees `view`. */
  targetOf: (id: string) => PinRect | null
  op: OpFn
  toast: ToastFn
  pub: boolean
  compactAt?: number
  maxTop: number
  pxc: number
  onMailLink: MailLinkFn
  onOpenDoc: (id: string) => void
  onLineage: (id: string) => void
  onConfig: (id: string) => void
  onJump: (id: string) => void
}

const vpSize = (ref: RefObject<HTMLDivElement | null>): { w: number; h: number } | null => {
  const r = ref.current?.getBoundingClientRect()
  return r && r.width > 0 && r.height > 0 ? { w: r.width, h: r.height } : null
}

interface Ghost { key: number; id: string; from: PinRect; to: PinRect | null }

/** every pinned window, rendered as screen-space siblings of `.space`. The
 *  only thing OrgCanvas mounts from this file. */
export function PinLayer(props: PinLayerProps) {
  const { slug, map, viewportRef, targetOf, toast } = props
  const pins = usePins(slug)
  const [ghosts, setGhosts] = useState<Ghost[]>([])
  const ghostKey = useRef(0)
  // a viewport resize can strand a window with no gesture to follow it, so
  // clamping happens at render time against the CURRENT size — and this tick
  // is what makes a resize a render
  const [, setVpTick] = useState(0)
  useEffect(() => {
    const bump = () => setVpTick((n) => n + 1)
    window.addEventListener('resize', bump)
    return () => window.removeEventListener('resize', bump)
  }, [])

  const unpin = useCallback((id: string, from: PinRect) => {
    const plan = planUnpin(targetOf(id), vpSize(viewportRef))
    removePin(slug, id)
    const key = ++ghostKey.current
    setGhosts((g) => [...g, { key, id, from, to: plan.kind === 'gone' ? null : plan.to }])
    if (plan.kind === 'offscreen') {
      toast([`${id} minimised — its desk is off-screen (the tray or its card jumps there)`])
    } else if (plan.kind === 'gone') {
      toast([`${id} is gone from the org — window closed`])
    }
    setTimeout(() => setGhosts((g) => g.filter((x) => x.key !== key)), PIN_GHOST_MS + 60)
  }, [slug, targetOf, viewportRef, toast])

  const vp = vpSize(viewportRef)
  return (
    <>
      {pins.map((pin) => {
        const node = map.get(pin.id)
        // a pin whose node is not in `map` is either mid-sweep (about to be
        // pruned) or inside the org-switch props window — render nothing
        // rather than a window with no agent behind it
        if (!node) return null
        return <PinWindow key={pin.id} pin={pin} node={node} vp={vp}
          onUnpin={unpin} {...props} />
      })}
      {ghosts.map((g) => <MinimiseGhost key={g.key} ghost={g} />)}
    </>
  )
}

/** the unpin animation: a chrome-only box that flies from the window's rect
 *  to the agent's card (or fades in place when there is no card). The desk
 *  itself is already unmounted — a ghost carries no live view, so "one live
 *  desk per agent" holds through the animation too. An off-screen target is
 *  flown toward and clipped by the viewport's overflow:hidden on purpose: a
 *  box that stopped at the edge would imply the agent is there, and it isn't. */
function MinimiseGhost({ ghost }: { ghost: Ghost }) {
  const [flown, setFlown] = useState(false)
  useEffect(() => {
    const id = requestAnimationFrame(() => setFlown(true))
    return () => cancelAnimationFrame(id)
  }, [])
  const r = flown && ghost.to ? ghost.to : ghost.from
  const style: CSSProperties = {
    left: r.x, top: r.y, width: r.w, height: r.h,
    opacity: flown ? 0 : 0.9,
    transitionDuration: `${PIN_GHOST_MS}ms`,
  }
  return <div className={'pinwin-ghost' + (ghost.to ? '' : ' fade')} style={style}
    data-id={ghost.id} data-to={ghost.to ? 'card' : 'none'} />
}

type Gesture =
  | { kind: 'move'; sx: number; sy: number; o: PinRect }
  | { kind: 'size'; sx: number; sy: number; o: PinRect; edge: string }

const EDGES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'] as const

function PinWindow({ pin, node, vp, onUnpin, slug, op, toast, pub,
  compactAt, maxTop, pxc, onMailLink, onOpenDoc, onLineage, onConfig, onJump, map }:
  PinLayerProps & { pin: Pin; node: CanvasNode; vp: { w: number; h: number } | null
    onUnpin: (id: string, from: PinRect) => void }) {
  // the in-flight gesture's rect lives in component state (one render per
  // pointer move); the store is written ONCE, at pointer-up, via commitRect
  const [live, setLive] = useState<PinRect | null>(null)
  const gesture = useRef<Gesture | null>(null)
  const pulse = usePulse(slug, pin.id)
  const [flash, setFlash] = useState(0)
  useEffect(() => {
    if (!pulse) return
    setFlash(pulse)
    const t = setTimeout(() => setFlash(0), 650)
    return () => clearTimeout(t)
  }, [pulse])

  // the rect on screen: the gesture's live rect while dragging, else the
  // stored one — clamped against the CURRENT viewport so a shrink can never
  // strand a window (render-time clamp; see PinLayer's resize tick)
  const rect = live ?? clampRect(pin.rect, vp)

  const begin = (e: ReactPointerEvent<HTMLElement>, g: Gesture) => {
    if (e.button !== 0) return
    e.stopPropagation()          // never let this become a canvas pan
    e.preventDefault()           // no text-selection drag from the chrome
    gesture.current = g
    e.currentTarget.setPointerCapture(e.pointerId)
    raisePin(slug, pin.id)
  }
  const move = (e: ReactPointerEvent<HTMLElement>) => {
    const g = gesture.current
    if (!g) return
    // viewport px: a window drag is 1:1 with the pointer — there is NO `/z`
    // here, unlike moveNodeDrag, because nothing about this rect is in world
    // space. That is the entire point of the feature.
    const dx = e.clientX - g.sx, dy = e.clientY - g.sy
    if (g.kind === 'move') {
      setLive({ ...g.o, x: g.o.x + dx, y: g.o.y + dy })
      return
    }
    let { x, y, w, h } = g.o
    if (g.edge.includes('e')) w = g.o.w + dx
    if (g.edge.includes('s')) h = g.o.h + dy
    if (g.edge.includes('w')) { w = g.o.w - dx; x = g.o.x + dx }
    if (g.edge.includes('n')) { h = g.o.h - dy; y = g.o.y + dy }
    // the floor pins the OPPOSITE edge: shrinking past the minimum from the
    // west/north must not walk the window across the screen
    if (w < PIN_MIN_W) { if (g.edge.includes('w')) x = g.o.x + g.o.w - PIN_MIN_W; w = PIN_MIN_W }
    if (h < PIN_MIN_H) { if (g.edge.includes('n')) y = g.o.y + g.o.h - PIN_MIN_H; h = PIN_MIN_H }
    setLive({ x, y, w, h })
  }
  const end = (e: ReactPointerEvent<HTMLElement>) => {
    const g = gesture.current
    if (!g) return
    gesture.current = null
    try { e.currentTarget.releasePointerCapture(e.pointerId) } catch { /* already released */ }
    const final = live ?? g.o
    setLive(null)
    commitRect(slug, pin.id, final, vp)
  }

  const state = node.state === 'live' ? '' : node.state
  const style: CSSProperties = {
    left: rect.x, top: rect.y, width: rect.w, height: rect.h,
    zIndex: zIndexOf(pin.z),
  }
  return (
    <div className={'pinwin prov-' + providerOf(node.tier ?? '')
        + (state ? ' pin-' + state : '') + (flash ? ' flash' : '') + (live ? ' moving' : '')}
      style={style} data-id={pin.id} data-z={pin.z}
      /* the WHOLE window is a screen-space surface: a press anywhere inside
         it is never a canvas pan (this is the stopPropagation half of the
         two-list rule in styles.css — `.pinwin` is also in the user-select
         re-enable list there; KEEP THEM IN STEP) */
      onPointerDown={(e) => { e.stopPropagation(); raisePin(slug, pin.id) }}>
      <div className="pinwin-title"
        onPointerDown={(e) => begin(e, { kind: 'move', sx: e.clientX, sy: e.clientY, o: rect })}
        onPointerMove={move} onPointerUp={end} onPointerCancel={end}>
        <PushPinIcon fontSize="inherit" className="pinwin-glyph" />
        <span className={'tier t-' + node.tier}>{TIER_LETTER[node.tier!] ?? '?'}</span>
        <b className="pinwin-name">{node.id}</b>
        {state && <span className="pinwin-state" title={`this agent is ${state}; the window stays readable`}>{state}</span>}
        <span className="spacer" />
        <button className="pinwin-unpin" title={`unpin ${node.id} — minimise back to its desk`}
          aria-label={`unpin ${node.id}`}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => onUnpin(pin.id, rect)}>
          <CloseIcon fontSize="inherit" />
        </button>
      </div>
      <div className="pinwin-body">
        <DeskChat bare node={node} map={map} op={op} slug={slug} toast={toast}
          pub={pub} compactAt={compactAt} maxTop={maxTop} pxc={pxc}
          onMailLink={onMailLink} onOpenDoc={onOpenDoc}
          onLineage={() => onLineage(pin.id)} onConfig={() => onConfig(pin.id)}
          onJump={onJump} />
      </div>
      {EDGES.map((edge) => (
        <div key={edge} className={'pinwin-rs ' + edge}
          onPointerDown={(e) => begin(e, { kind: 'size', sx: e.clientX, sy: e.clientY, o: rect, edge })}
          onPointerMove={move} onPointerUp={end} onPointerCancel={end} />
      ))}
    </div>
  )
}

/** what a pinned agent's CARD shows in the desk's place when the camera
 *  would otherwise have focused it (user ruling: never a second desk).
 *  The whole surface is the click target; clicking raises and flashes the
 *  window and never unpins — unpinning silently behind a click that reads
 *  as "show me this" would throw away a hand-arranged position with no undo. */
export function PinnedPlaceholder({ id, onShow }: { id: string; onShow: () => void }) {
  return (
    <div className="pin-placeholder" role="button" tabIndex={0}
      title={`${id}'s desk is open as a pinned window — click to show it`}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => { e.stopPropagation(); onShow() }}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onShow() } }}>
      <PushPinIcon className="pin-placeholder-glyph" />
      <b>pinned</b>
      <span>this desk is open as a window</span>
      <span className="pin-placeholder-btn">show window</span>
    </div>
  )
}
