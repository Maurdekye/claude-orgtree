import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  audienceAction, compactNode, dissolveAll, getCharters, getChat, getHistory,
  getMcpServers, getNodeInbox, getScratch, interruptNode, orgInboxRead,
  reorderNode, saveScope, saveSettings, sendMessage,
} from './api'
import { pickFolder } from './picker'
import {
  AddIcon, ArrowUpIcon, AutorenewIcon, CheckIcon, CloseIcon, DeleteIcon,
  DotIcon, EditIcon, FileIcon, FocusIcon, FolderIcon, FrozenIcon,
  FullscreenIcon, HearingIcon, LayersIcon, LockIcon, MailIcon, PlayIcon,
  PublicIcon, RemoveIcon, SettingsIcon, SparkIcon, StopIcon, ViewListIcon,
  WarnIcon,
} from './icons'

const TIER_LETTER = { haiku: 'H', sonnet: 'S', opus: 'O', fable: 'F' }
const TIERS = ['haiku', 'sonnet', 'opus', 'fable']

// world-space geometry (px at zoom 1). Cards are SQUARE (design ruling) and never
// change size — the desk chat fades in OVER the card; you zoom to read it.
const NODE_W = 124, NODE_H = 124
const USER_W = 124, USER_H = 124   // the eye is a peer square (user ruling)
const SX = 186, SY = 200, PAD = 90
const Z_MAX = 12       // enough for one desk to FILL the screen (124px card ≥ ~1450px)
// LOD thresholds on zoom
const Z_MINI = 0.55
const Z_DESK = 2.1
// dampened spring (underdamped → gentle elastic overshoot)
const SPRING_K = 170, SPRING_C = 15

const DRAFT = '__draft__'
const USER = '@user'   // actor sentinel — never collides with a node named "user"
const EXTERN = '@extern'      // the org-inbox audience grantor sentinel
const INBOX = '__orginbox__'  // the org-inbox panel's layout id
const INBOX_H = 64
// the eye's fixed world x (see layout()): generous enough that even a very
// wide left subtree (~32 leaf columns) never crosses into negative space
const EYE_ANCHOR_X = 6000

function withDraftTree(tree, draft) {
  const draftNode = () => ({
    id: DRAFT, title: '', tier: draft.tier, state: 'draft', children: [],
    seat: 0, grant: 0, free: 0,
  })
  const mk = (n) => ({
    ...n,
    children: [...n.children.map(mk),
      ...(draft && draft.parent === n.id ? [draftNode()] : [])],
  })
  return {
    id: USER, title: 'you', tier: null, state: 'user',
    children: [...tree.roots.map(mk),
      ...(draft && draft.parent === null ? [draftNode()] : [])],
  }
}

function flatten(root, seats) {
  const map = new Map()
  const walk = (n, parent) => {
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

function layout(root) {
  const pos = new Map()
  const width = (n) => (n.children.length ? n.children.reduce((a, c) => a + width(c), 0) : 1)
  const place = (n, x0, depth) => {
    let cx = x0
    n.children.forEach((c) => { place(c, cx, depth + 1); cx += width(c) })
    const x = n.children.length
      ? (pos.get(n.children[0].id).x + pos.get(n.children[n.children.length - 1].id).x) / 2
      : x0
    pos.set(n.id, { x, y: depth })
  }
  place(root, 0, 0)
  const out = new Map()
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

function sizeOf(id) {
  if (id === USER) return { w: USER_W, h: USER_H }
  if (id === INBOX) return { w: USER_W, h: INBOX_H }
  return { w: NODE_W, h: NODE_H }
}

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

// chat markdown: gfm + hard line breaks, sanitized (agents echo web content)
const md = (text) => ({
  __html: DOMPurify.sanitize(marked.parse(text ?? '', { gfm: true, breaks: true, async: false })),
})
const smooth = (t) => t * t * (3 - 2 * t)

// ---- connection segments (world space). kind 'c' = cubic bezier, 'l' = line.
const segD = (s) => (s.kind === 'l'
  ? `M ${s.pts[0].x} ${s.pts[0].y} L ${s.pts[1].x} ${s.pts[1].y}`
  : `M ${s.pts[0].x} ${s.pts[0].y} C ${s.pts[1].x} ${s.pts[1].y}, `
    + `${s.pts[2].x} ${s.pts[2].y}, ${s.pts[3].x} ${s.pts[3].y}`)
const segPoint = (s, t) => {
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
export function useEsc(close) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])
}

// in-page confirmation (user ruling: never a native OS dialog)
export function ConfirmModal({ title, body, confirmLabel, onConfirm, close }) {
  useEsc(close)
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings confirm-box" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {body && <div className="confirm-body">{body}</div>}
        <div className="row">
          <button className="danger solid"
            onClick={() => { close(); onConfirm() }}>{confirmLabel}</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>
  )
}

export function OrgCanvas({ tree, op, slug, pulse, toast, streamEvt, activity, mailEvt, onInbox }) {
  const [draft, setDraft] = useState(null)
  const [configId, setConfigId] = useState(null)
  const [lineageId, setLineageId] = useState(null)
  const [userCfg, setUserCfg] = useState(false)
  const [trayOpen, setTrayOpen] = useState(false)   // the flat agent tray
  const [inboxId, setInboxId] = useState(null)
  const [oiOpen, setOiOpen] = useState(false)       // the ORG-inbox viewer
  // unread-mail attention: the eye glows until the mailbox is OPENED (merely
  // opening acknowledges the glow; the count badge stays until mails are READ)
  const [inboxSeen, setInboxSeen] = useState(
    () => localStorage.getItem('orgtree-inbox-seen-' + slug) ?? '')
  const mailGlow = (tree.user_inbox_count ?? 0) > 0
    && (tree.user_inbox_newest ?? '') > inboxSeen
  const seats = tree.tiers ?? { haiku: 1, sonnet: 3, opus: 5, fable: 10 }
  const vroot = useMemo(() => withDraftTree(tree, draft), [tree, draft])
  const map = useMemo(() => flatten(vroot, seats), [vroot])   // eslint-disable-line
  const target = useMemo(() => {
    const t = layout(vroot)
    for (const n of map.values()) {           // live bearers float ABOVE the successor
      // (clear of its card — overlap made both unclickable)
      if (n.isBearerOf && t.has(n.isBearerOf)) {
        const p = t.get(n.isBearerOf)
        t.set(n.id, {
          x: p.x + 42 + 18 * n.bearerIndex,
          y: p.y - (NODE_H + 26) - 20 * n.bearerIndex,
        })
      }
    }
    // the ORG INBOX panel (user spec): up and to the RIGHT of the overseer —
    // "out of the way" of the org structure, not stacked on its axis — and it
    // only exists once the org has received outside mail or granted an inbox
    // audience; until then the canvas is unchanged
    if (tree.org_inbox?.visible) {
      const eye = t.get(USER)
      if (eye) t.set(INBOX, { x: eye.x + USER_W + 260, y: eye.y - INBOX_H - 96 })
    }
    let minY = Infinity
    for (const p of t.values()) minY = Math.min(minY, p.y)
    // headroom for the eye's infinite bar (2× the eye card, fading upward)
    if (minY < 140) for (const p of t.values()) p.y += 140 - minY
    return t
  }, [vroot, map, tree.org_inbox?.visible])
  const [view, setView] = useState(() => {
    // fit-on-load: center the initial tree in a typical viewport (re-fit against
    // the REAL viewport once mounted — see the mount effect below)
    const t = layout(withDraftTree(tree, null))
    let maxX = 0, maxY = 0
    for (const p of t.values()) { maxX = Math.max(maxX, p.x + 300); maxY = Math.max(maxY, p.y + 260) }
    const z = Math.min(1.3, Math.max(0.35, Math.min(1300 / maxX, 780 / maxY)))
    return { x: Math.max(24, (1400 - maxX * z) / 2), y: 24, z }
  })
  const [, setFrame] = useState(0)
  const [dropId, setDropId] = useState(null)

  const viewportRef = useRef(null)
  const viewRef = useRef(view); viewRef.current = view
  const animRef = useRef(null)
  const panRef = useRef(null)
  const springs = useRef(new Map())
  const targetRef = useRef(target); targetRef.current = target
  const mapRef = useRef(map); mapRef.current = map
  const nodeDrag = useRef(null)     // {id, sx, sy, ox, oy, moved}

  const posOf = (id) => springs.current.get(id) ?? targetRef.current.get(id)

  // ---------------------------------------------- wires: geometry + sparks
  const treeSeg = (parentId, childId) => {
    const a = posOf(parentId), b = posOf(childId)
    const ps = sizeOf(parentId)
    return { kind: 'c', pts: [
      { x: a.x + ps.w / 2, y: a.y + ps.h },
      { x: a.x + ps.w / 2, y: a.y + ps.h + 52 },
      { x: b.x + NODE_W / 2, y: b.y - 52 },
      { x: b.x + NODE_W / 2, y: b.y }] }
  }
  const peerSeg = (lId, rId) => {
    const a = posOf(lId), b = posOf(rId)
    return { kind: 'l', pts: [
      { x: a.x + sizeOf(lId).w, y: a.y + sizeOf(lId).h * 0.55 },
      { x: b.x, y: b.y + sizeOf(rId).h * 0.55 }] }
  }
  const audSeg = (gId, eId) => {
    const a = posOf(gId), b = posOf(eId)
    const ga = sizeOf(gId), gb = sizeOf(eId)
    // symmetrical about the grantor (user ruling, made for the overseer):
    // the line leaves the LEFT flank for grantees left of it and the RIGHT
    // flank for grantees right of it — no more always-rightward bulge
    const left = (b.x + gb.w / 2) < (a.x + ga.w / 2)
    const x1 = left ? a.x : a.x + ga.w
    const x2 = left ? b.x : b.x + gb.w
    const y1 = a.y + ga.h / 2, y2 = b.y + gb.h / 2
    const bulge = (64 + Math.abs(y2 - y1) * 0.12) * (left ? -1 : 1)
    return { kind: 'c', pts: [
      { x: x1, y: y1 }, { x: x1 + bulge, y: y1 },
      { x: x2 + bulge, y: y2 }, { x: x2, y: y2 }] }
  }

  const sparksRef = useRef([])
  const sparkId = useRef(0)
  const audSetRef = useRef(new Set())
  audSetRef.current = new Set((tree.audiences ?? []).map((a) => a.grantor + '→' + a.grantee))

  // audience-line life cycle (user ruling): a NEW grant draws itself in,
  // grantor → grantee, over the same 420ms the message spark takes — the two
  // arrive at the new agent together; a REVOKED grant retracts the same way.
  const AUD_DUR = 420
  const audAnimRef = useRef(new Map())    // 'g→e' → {phase:'in'|'out', t0, grantor, grantee}
  const audPrevRef = useRef(null)         // null until first sync: lines that
                                          // exist at page load never animate in
  const audRafRef = useRef(null)
  const kickAudAnim = useCallback(() => {
    if (audRafRef.current) return
    const loop = () => {
      if (audAnimRef.current.size) {
        setFrame((f) => f + 1)
        audRafRef.current = requestAnimationFrame(loop)
      } else {
        audRafRef.current = null
      }
    }
    audRafRef.current = requestAnimationFrame(loop)
  }, [])
  useEffect(() => {
    const cur = audSetRef.current
    if (audPrevRef.current === null) { audPrevRef.current = new Set(cur); return }
    const prev = audPrevRef.current
    const anim = audAnimRef.current
    const now = performance.now()
    for (const k of cur) {
      if (!prev.has(k) && anim.get(k)?.phase !== 'in') anim.set(k, { phase: 'in', t0: now })
    }
    for (const k of prev) {
      if (!cur.has(k)) {
        const [grantor, grantee] = k.split('→')
        anim.set(k, { phase: 'out', t0: now, grantor, grantee })
      }
    }
    audPrevRef.current = new Set(cur)
    if (anim.size) kickAudAnim()
  }, [tree.audiences, kickAudAnim])   // eslint-disable-line react-hooks/exhaustive-deps

  // a mail event rides the org's wires: down/up the tree, along the peer line
  // between coworkers, or along a direct audience line when one connects the two
  const launchSpark = useCallback((from, to) => {
    const m = mapRef.current
    const norm = (x) => (!x || x === 'user' || x === 'user_inbox' || x === USER) ? USER : x
    const a = norm(from), b = norm(to)
    if (a === b || !m.has(a) || !m.has(b)) return
    const segs = []
    const aud = audSetRef.current
    if (aud.has(a + '→' + b) || aud.has(b + '→' + a)) {
      const [g, e] = aud.has(a + '→' + b) ? [a, b] : [b, a]
      segs.push({ ...audSeg(g, e), rev: g !== a })
    } else if (a !== USER && b !== USER && m.get(a)?.parent === m.get(b)?.parent) {
      const sibs = (m.get(m.get(a).parent)?.children ?? []).map((c) => c.id)
        .filter((k) => m.has(k) && k !== DRAFT)
        .sort((p, q) => (targetRef.current.get(p)?.x ?? 0) - (targetRef.current.get(q)?.x ?? 0))
      const ia = sibs.indexOf(a), ib = sibs.indexOf(b)
      if (ia < 0 || ib < 0) return
      const step = ia < ib ? 1 : -1
      for (let i = ia; i !== ib; i += step) {
        segs.push({ ...peerSeg(sibs[Math.min(i, i + step)], sibs[Math.max(i, i + step)]),
          rev: step < 0 })
      }
    } else {
      const chain = (id) => {
        const out = [id]
        let c = id
        while (c !== USER) { c = m.get(c)?.parent ?? USER; out.push(c) }
        return out
      }
      const ca = chain(a), cb = chain(b)
      const inB = new Set(cb)
      const lca = ca.find((k) => inB.has(k))
      for (let i = 0; ca[i] !== lca; i++) segs.push({ ...treeSeg(ca[i + 1], ca[i]), rev: true })
      let prev = lca
      for (const k of cb.slice(0, cb.indexOf(lca)).reverse()) {
        segs.push({ ...treeSeg(prev, k), rev: false })
        prev = k
      }
    }
    if (!segs.length) return
    sparksRef.current.push({ id: ++sparkId.current, segs,
      start: performance.now(), segDur: 420 })
    setFrame((f) => f + 1)
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (mailEvt) launchSpark(mailEvt.from, mailEvt.to) },
    [mailEvt, launchSpark])
  useEffect(() => {
    window.__spark = launchSpark        // dev/demo hook
    return () => { if (window.__spark === launchSpark) delete window.__spark }
  }, [launchSpark])

  // org-relative credit scale: sole top-level holder = exactly one card
  // height; with many holders the TYPICAL bar ≈1.25× the card, the biggest
  // clamped at 1.6×. A bar spans the node's WHOLE holding (seat + grant,
  // seat block at the foot).
  // ⚠ Derived from TOP-LEVEL holdings ONLY (user ruling): sub-reallocations
  // re-partition circulation and must never move any other bar.
  const pxPerCredit = useMemo(() => {
    // kiosk: the cap is the scale — the overseer bar is a fixed size (user spec)
    if (tree.kiosk?.credits) return (NODE_H * 1.6) / tree.kiosk.credits
    const holds = tree.roots
      .filter((n) => n.state === 'live')
      .map((n) => n.seat + n.grant)
    if (!holds.length) return NODE_H / 10
    if (holds.length === 1) return NODE_H / holds[0]
    const avg = holds.reduce((a, b) => a + b, 0) / holds.length
    const max = Math.max(...holds)
    return Math.min((NODE_H * 1.25) / avg, (NODE_H * 1.6) / max)
  }, [tree])

  // ------------------------------------------------------- the spring engine
  useEffect(() => {
    let raf, last = performance.now()
    const tick = (t) => {
      const dt = Math.min(0.033, (t - last) / 1000); last = t
      let active = false
      for (const [id, tgt] of targetRef.current) {
        let s = springs.current.get(id)
        if (!s) {
          const par = mapRef.current.get(id)?.parent
          const ps = par && springs.current.get(par)
          s = ps ? { x: ps.x, y: ps.y, vx: 0, vy: 0 }
                 : { x: tgt.x, y: tgt.y, vx: 0, vy: 0 }
          springs.current.set(id, s)
          active = true
        }
        if (nodeDrag.current?.moved && nodeDrag.current.bases?.has(id)) continue
        const ax = SPRING_K * (tgt.x - s.x) - SPRING_C * s.vx
        const ay = SPRING_K * (tgt.y - s.y) - SPRING_C * s.vy
        s.vx += ax * dt; s.vy += ay * dt
        s.x += s.vx * dt; s.y += s.vy * dt
        if (Math.abs(tgt.x - s.x) > 0.4 || Math.abs(tgt.y - s.y) > 0.4
            || Math.abs(s.vx) > 2 || Math.abs(s.vy) > 2) active = true
        else { s.x = tgt.x; s.y = tgt.y; s.vx = 0; s.vy = 0 }
      }
      for (const id of [...springs.current.keys()]) {
        if (!targetRef.current.has(id)) springs.current.delete(id)
      }
      if (sparksRef.current.length) {
        const now = performance.now()
        sparksRef.current = sparksRef.current.filter(
          (sp) => now < sp.start + sp.segs.length * sp.segDur + 60)
        active = true
      }
      if (active || nodeDrag.current?.moved) setFrame((f) => f + 1)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  // ------------------------------------------------------------- camera math
  const animateTo = useCallback((to, ms = 460) => {
    cancelAnimationFrame(animRef.current)
    const from = { ...viewRef.current }
    const t0 = performance.now()
    const step = (t) => {
      const k = Math.min(1, (t - t0) / ms), e = ease(k)
      setView({
        x: from.x + (to.x - from.x) * e,
        y: from.y + (to.y - from.y) * e,
        z: from.z + (to.z - from.z) * e,
      })
      if (k < 1) animRef.current = requestAnimationFrame(step)
    }
    animRef.current = requestAnimationFrame(step)
  }, [])

  const centerOn = useCallback((id, z = null) => {
    const p = targetRef.current.get(id)
    const vp = viewportRef.current?.getBoundingClientRect()
    if (!p || !vp) return
    // click-to-focus fills the window with the card, small margin all round.
    // The EYE fits by HEIGHT only — it is the one cell that expands in width
    // to the screen's aspect ratio (the switchboard), so height is the fit.
    const zz = z ?? (id === USER
      ? Math.min(Z_MAX, (vp.height - 48) / USER_H)
      : Math.min(Z_MAX, (Math.min(vp.width, vp.height) - 48) / NODE_H))
    animateTo({
      x: vp.width / 2 - (p.x + NODE_W / 2) * zz,
      y: vp.height / 2 - (p.y + NODE_H / 2) * zz,
      z: zz,
    })
  }, [animateTo])

  // a REAL fit: whole org inside the actual viewport
  const fitAll = useCallback((animate = true, ms = 320) => {
    const vp = viewportRef.current?.getBoundingClientRect()
    if (!vp) return
    let minX = Infinity, minY = Infinity, maxX = 0, maxY = 0
    for (const p of targetRef.current.values()) {
      minX = Math.min(minX, p.x); minY = Math.min(minY, p.y)
      maxX = Math.max(maxX, p.x + NODE_W + 40); maxY = Math.max(maxY, p.y + NODE_H + 40)
    }
    if (!isFinite(minX)) return
    // extra top margin: the eye's infinite bar fades 110px above its card
    minX = Math.max(0, minX - 60); minY = Math.max(0, minY - 130)
    const z = Math.min(1.3, Math.max(0.24,
      Math.min((vp.width - 48) / (maxX - minX), (vp.height - 48) / (maxY - minY))))
    const to = {
      x: (vp.width - (maxX - minX) * z) / 2 - minX * z,
      y: (vp.height - (maxY - minY) * z) / 2 - minY * z,
      z,
    }
    if (animate) animateTo(to, ms)
    else setView(to)
  }, [animateTo])
  // opening an org: wake on the eye, then drift out to the whole tree.
  // Wheel and drag both cancel the shared camera animation, so the intro is
  // interruptible at any moment.
  useEffect(() => {
    const vp = viewportRef.current?.getBoundingClientRect()
    const eye = targetRef.current.get(USER)
    if (!vp || !eye) { fitAll(false); return }
    const z0 = 1.6                       // close, but under the desk threshold
    setView({
      x: vp.width / 2 - (eye.x + USER_W / 2) * z0,
      y: vp.height / 2 - (eye.y + USER_H / 2) * z0,
      z: z0,
    })
    const raf = requestAnimationFrame(() => fitAll(true, 1700))
    return () => cancelAnimationFrame(raf)
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = viewportRef.current
    if (!el) return
    const onWheel = (e) => {
      // wheel inside a modal always scrolls the modal. Inside a desk it NEVER
      // zooms (user ruling, reversing the earlier fall-through-to-zoom): the
      // wheel is scroll-only there, even when nothing can scroll — zoom by
      // moving the cursor off the desk first.
      // (native listener — it fires before React's delegated handlers, so
      // component-level stopPropagation can't guard it)
      if (e.target.closest?.('.overlay')) return
      if (e.target.closest?.('.desk-over')) return
      e.preventDefault()
      cancelAnimationFrame(animRef.current)
      const v = viewRef.current
      const factor = Math.exp(-e.deltaY * 0.0012)
      const z = Math.min(Z_MAX, Math.max(0.24, v.z * factor))
      const r = el.getBoundingClientRect()
      const mx = e.clientX - r.left, my = e.clientY - r.top
      const wx = (mx - v.x) / v.z, wy = (my - v.y) / v.z
      setView({ x: mx - wx * z, y: my - wy * z, z })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  const toWorld = (e) => {
    const r = viewportRef.current.getBoundingClientRect()
    const v = viewRef.current
    return { x: (e.clientX - r.left - v.x) / v.z, y: (e.clientY - r.top - v.y) / v.z }
  }

  // background pan
  const onPointerDown = (e) => {
    if (e.button !== 0) return
    cancelAnimationFrame(animRef.current)
    panRef.current = { sx: e.clientX, sy: e.clientY, ox: viewRef.current.x, oy: viewRef.current.y, moved: false }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e) => {
    const d = panRef.current
    if (!d) return
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
    if (d.moved) setView((v) => ({ ...v, x: d.ox + dx, y: d.oy + dy }))
  }
  const onPointerUp = () => { panRef.current = null }

  // ----------------------------------------------------------- node dragging
  const descendantsOf = (id) => {
    const out = new Set()
    const walk = (k) => mapRef.current.get(k)?.children.forEach((c) => { out.add(c.id); walk(c.id) })
    walk(id)
    return out
  }

  const dropTargetAt = (world, dragId) => {
    const banned = descendantsOf(dragId); banned.add(dragId); banned.add(DRAFT)
    for (const [id] of targetRef.current) {
      if (banned.has(id)) continue
      const n = mapRef.current.get(id)
      if (id !== USER && n?.state !== 'live') continue
      const p = posOf(id)
      const { w, h } = sizeOf(id, null)
      if (world.x >= p.x && world.x <= p.x + w && world.y >= p.y && world.y <= p.y + h) return id
    }
    return null
  }

  const startNodeDrag = (e, id) => {
    if (e.button !== 0) return
    if (e.target.closest('button, input, textarea, select, .cbar, .desk-body')) return
    if (mapRef.current.get(id)?.isBearerOf) {   // lineage cards are not org nodes
      e.stopPropagation()
      nodeDrag.current = { id, sx: e.clientX, sy: e.clientY, bases: new Map(), moved: false }
      return
    }
    e.stopPropagation()
    // the grabbed node carries its FULL subtree (user ruling) — record every
    // member's position so they all move as one rigid group
    const bases = new Map()
    for (const k of [id, ...descendantsOf(id)]) {
      const p = posOf(k)
      if (p) bases.set(k, { x: p.x, y: p.y })
    }
    nodeDrag.current = { id, sx: e.clientX, sy: e.clientY, bases, moved: false }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const moveNodeDrag = (e, id) => {
    const d = nodeDrag.current
    if (!d || d.id !== id) return
    const z = viewRef.current.z
    const dx = (e.clientX - d.sx) / z, dy = (e.clientY - d.sy) / z
    if (Math.abs(dx) + Math.abs(dy) > 5 / z) d.moved = true
    if (!d.moved) return
    for (const [k, b] of d.bases) {
      const s = springs.current.get(k)
      if (s) { s.x = b.x + dx; s.y = b.y + dy; s.vx = 0; s.vy = 0 }
    }
    setDropId(dropTargetAt(toWorld(e), id))
  }
  const endNodeDrag = (e, id, node, focused) => {
    const d = nodeDrag.current
    if (!d || d.id !== id) return
    nodeDrag.current = null
    const drop = dropId
    setDropId(null)
    if (!d.moved) {                       // a plain click → walk to the desk
      if (!focused && id !== USER && node.state !== 'draft') centerOn(id)
      return
    }
    if (node.state === 'draft' || id === USER) return
    const parent = mapRef.current.get(id)?.parent
    const ancestors = []
    let cur = parent
    while (cur != null) { ancestors.push(cur); cur = mapRef.current.get(cur)?.parent }

    const finish = () => setFrame((f) => f + 1)   // springs glide back/onward
    if (drop && drop !== parent) {
      const body = drop === USER
        ? { op: 'promote', node: id, new_parent: null }
        : ancestors.includes(drop)
          ? { op: 'promote', node: id, new_parent: drop }
          : { op: 'demote', node: id, new_parent: drop }
      op(body).catch(() => {}).finally(finish)
      return
    }
    // no (new) target → cosmetic reorder among the current cohort by dropped x
    const sibs = (mapRef.current.get(parent)?.children ?? [])
      .map((c) => c.id).filter((k) => k !== id && k !== DRAFT)
    if (!sibs.length) { finish(); return }
    const x = springs.current.get(id)?.x ?? 0
    const beforeSib = sibs.find((k) => (targetRef.current.get(k)?.x ?? 0) > x)
    const req = beforeSib ? { before: beforeSib } : { after: sibs[sibs.length - 1] }
    reorderNode(slug, id, req).catch((err) => toast([`error: ${err.message}`])).finally(finish)
  }

  // ------------------------------------------------------- focus (the desk)
  const focusId = useMemo(() => {
    if (view.z < Z_DESK) return null
    const vp = viewportRef.current?.getBoundingClientRect()
    const cw = vp ? vp.width / 2 : 500, ch = vp ? vp.height / 2 : 350
    let best = null, bestD = Infinity
    for (const [id] of target) {
      if (id === DRAFT) continue         // the EYE can be a desk too (switchboard)
      const p = posOf(id)
      const sx = (p.x + NODE_W / 2) * view.z + view.x
      const sy = (p.y + NODE_H / 2) * view.z + view.y
      const d = Math.hypot(sx - cw, sy - ch)
      if (d < bestD) { bestD = d; best = id }
    }
    // the SWITCHBOARD is a full-screen surface (user ruling): the eye's desk
    // triggers only when the zoom actually approaches screen-filling — far
    // later than an agent's desk; below that the eye stays a plain card
    if (best === USER) {
      const zFill = Math.min(Z_MAX, vp ? (vp.height - 48) / USER_H : Z_MAX)
      if (view.z < zFill * 0.85) return null
    }
    return bestD < NODE_W * 1.6 * view.z ? best : null
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, target])

  const lod = view.z < Z_MINI ? 'mini' : 'norm'

  const bounds = useMemo(() => {
    let mx = 900, my = 700
    for (const p of target.values()) { mx = Math.max(mx, p.x + 560); my = Math.max(my, p.y + 640) }
    return { w: mx, h: my }
  }, [target])

  // coworkers are wired: adjacent live siblings share a lateral line (§7.6
  // peers may message directly)
  const peerLinks = useMemo(() => {
    const links = []
    for (const n of map.values()) {
      if (!n.children || n.children.length < 2 || n.isBearerOf) continue
      const sibs = n.children.map((c) => c.id)
        .filter((k) => map.get(k)?.state === 'live')
        .sort((p, q) => (target.get(p)?.x ?? 0) - (target.get(q)?.x ?? 0))
      for (let i = 0; i + 1 < sibs.length; i++) links.push([sibs[i], sibs[i + 1]])
    }
    return links
  }, [map, target])

  const audLines = (tree.audiences ?? [])
    .filter((a) => map.has(a.grantor) && map.has(a.grantee))

  const spawn = (parentId, tier) => {
    setDraft({ parent: parentId === USER ? null : parentId, tier })
    // roughly OVERVIEW scale start to finish (user ruling): the form is
    // authored on a 200px virtual surface (scale .6 into the card), so
    // z ≈ 1.7 already renders authored px ≈ screen px — no screen-fill dive
    setTimeout(() => centerOn(DRAFT, Math.max(1.7, viewRef.current.z)), 60)
  }
  const confirmDraft = (name, grant, charter, scope) => {
    op({ op: 'hire', parent: draft.parent, tier: draft.tier, grant, name,
         charter: charter?.trim() || undefined,
         // pre-hire permissions (user spec): staged in the draft's modal,
         // applied atomically with the hire
         ...(scope ? { add_dirs: scope.add_dirs, tools: scope.tools,
                       org_visibility: scope.org_visibility } : {}) })
      .then((r) => {
        // the real card replaces the draft IN PLACE — seed its spring from the
        // draft's so it doesn't glide over from its parent a second time
        const ds = springs.current.get(DRAFT)
        if (r?.node && ds) springs.current.set(r.node, { ...ds, vx: 0, vy: 0 })
        setDraft(null)
      }).catch(() => {})
  }

  // the eye's bar on hover. Every credit in circulation is, recursively,
  // either locked in some live node's SEAT or sitting FREE in some node's
  // grant (committed grants just contain the child's seat+free again) — so
  // circulation = Σ seats + Σ free, and those are the honest labels.
  const kioskRemaining = tree.kiosk?.credits != null
    ? Math.max(0, tree.kiosk.credits - (tree.audit?.top_level_holds ?? 0))
    : null

  const orgStats = useMemo(() => {
    let free = 0
    const walk = (n) => {
      if (n.state === 'live' && n.free > 0) free += n.free
      n.children.forEach(walk)
    }
    tree.roots.forEach(walk)
    const circ = tree.audit?.top_level_holds ?? 0
    return { circ, seats: circ - free, free }
  }, [tree])

  return (
    <div className="viewport" ref={viewportRef}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove}
      onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
      <div className="space" style={{
        width: bounds.w, height: bounds.h,
        transform: `translate(${view.x}px, ${view.y}px) scale(${view.z})`,
        '--invz': Math.min(2.4, Math.max(1 / Z_MAX, 1 / view.z)).toFixed(3),
      }}>
        <svg className="edges" width={bounds.w} height={bounds.h}>
          {[...map.values()].filter((n) => n.parent && !n.isBearerOf).map((n) => {
            if (!posOf(n.parent) || !posOf(n.id)) return null
            return <path key={n.id} d={segD(treeSeg(n.parent, n.id))}
              className={'edge' + (n.state === 'archived' ? ' faded' : '')
                + (n.state === 'draft' ? ' draftedge' : '')} />
          })}
          {peerLinks.map(([l, r]) => (
            posOf(l) && posOf(r) &&
            <path key={'p' + l + r} d={segD(peerSeg(l, r))} className="edge peer" />
          ))}
          {(() => {
            const nowT = performance.now()
            const anim = audAnimRef.current
            const out = []
            for (const a of audLines) {
              if (!posOf(a.grantor) || !posOf(a.grantee)) continue
              const k = a.grantor + '→' + a.grantee
              const st = anim.get(k)
              let dash = null
              if (st?.phase === 'in') {
                const t = (nowT - st.t0) / AUD_DUR
                if (t >= 1) anim.delete(k)
                else dash = 1 - smooth(Math.max(0, t))   // draw toward the grantee
              }
              out.push(<path key={'a' + k} d={segD(audSeg(a.grantor, a.grantee))}
                pathLength={dash != null ? 1 : undefined}
                style={dash != null
                  ? { strokeDasharray: 1, strokeDashoffset: dash } : undefined}
                className={'edge aud-line' + (a.grantor === USER ? ' from-user' : '')} />)
            }
            for (const [k, st] of anim) {
              if (st.phase !== 'out') continue
              const t = (nowT - st.t0) / AUD_DUR
              if (t >= 1 || !posOf(st.grantor) || !posOf(st.grantee)) {
                anim.delete(k)
                continue
              }
              out.push(<path key={'a' + k} d={segD(audSeg(st.grantor, st.grantee))}
                pathLength={1}
                style={{ strokeDasharray: 1, strokeDashoffset: smooth(t) }}
                className={'edge aud-line'
                  + (st.grantor === USER ? ' from-user' : '')} />)
            }
            return out
          })()}
          {[...map.values()].filter((n) => n.isBearerOf).map((n) => {
            const a = posOf(n.isBearerOf), b = posOf(n.id)
            if (!a || !b) return null
            return <path key={'t' + n.id}
              d={`M ${a.x + NODE_W - 10} ${a.y + 8} L ${b.x + 10} ${b.y + NODE_H - 8}`}
              className="edge tether" />
          })}
          {tree.org_inbox?.visible && posOf(INBOX) && (() => {
            // no box↔eye tether (user revision) — the panel stands alone;
            // only audience lines to its holders. Those connect FACING sides:
            // an agent left of the box joins from its RIGHT side (user spec),
            // an agent right of it from its left.
            const out = []
            for (const h of tree.org_inbox.holders ?? []) {
              if (!map.has(h) || !posOf(h)) continue
              const a = posOf(INBOX), b = posOf(h)
              const ga = sizeOf(INBOX), gb = sizeOf(h)
              const left = (b.x + gb.w / 2) < (a.x + ga.w / 2)
              const x1 = left ? a.x : a.x + ga.w
              const x2 = left ? b.x + gb.w : b.x
              const y1 = a.y + ga.h / 2, y2 = b.y + gb.h / 2
              const bulge = 64 + Math.abs(y2 - y1) * 0.12
              out.push(<path key={'oi' + h} d={segD({ kind: 'c', pts: [
                { x: x1, y: y1 }, { x: x1 + (left ? -bulge : bulge), y: y1 },
                { x: x2 + (left ? bulge : -bulge), y: y2 }, { x: x2, y: y2 }] })}
                className="edge aud-line" />)
            }
            return out
          })()}
          {sparksRef.current.map((sp) => {
            const el = (performance.now() - sp.start) / sp.segDur
            const i = Math.max(0, Math.min(sp.segs.length - 1, Math.floor(el)))
            const t = smooth(Math.max(0, Math.min(1, el - i)))
            const seg = sp.segs[i]
            const p = segPoint(seg, seg.rev ? 1 - t : t)
            return <circle key={sp.id} className="spark" cx={p.x} cy={p.y} r="3.4" />
          })}
        </svg>
        {[...map.values()].map((n) => {
          const p = posOf(n.id)
          if (!p) return null
          if (n.id === USER) {
            const vp = viewportRef.current?.getBoundingClientRect()
            // the eye is the ONLY cell that expands in width to the screen's
            // FULL aspect ratio when focused (user spec — room for the
            // switchboard). The credit bar keeps its normal outboard spot:
            // offscreen at focus, but still rendered — pan sideways and it
            // is there (user ruling; never force it on screen).
            const eyeW = vp
              ? Math.round(USER_H * (vp.width - 48) / (vp.height - 48))
              : Math.round(USER_H * 16 / 9)
            return <UserNode key={USER} pos={p} isDrop={dropId === USER} seats={seats}
              stats={orgStats} mailGlow={mailGlow}
              kiosk={tree.kiosk} pub={!!tree.public} kioskRemaining={kioskRemaining}
              kioskSegs={tree.roots.filter((n) => n.state === 'live')
                .map((n) => ({ seat: n.seat, grant: n.grant }))}
              pxc={pxPerCredit} zoom={view.z}
              focused={focusId === USER} eyeW={Math.max(eyeW, USER_W)}
              onFocus={() => centerOn(USER)}
              posX={(id) => posOf(id)?.x ?? 0}
              onJump={(id) => centerOn(id)}
              map={map} op={op} slug={slug} pulse={pulse} toast={toast}
              streamEvt={streamEvt}
              inboxCount={(tree.user_inbox_count ?? 0) + (tree.credit_requests?.length ?? 0)}
              onInbox={() => {
                const nw = tree.user_inbox_newest ?? new Date().toISOString()
                localStorage.setItem('orgtree-inbox-seen-' + slug, nw)
                setInboxSeen(nw)
                onInbox?.()
              }}
              onGear={() => setUserCfg(true)}
              onSpawn={(t) => spawn(USER, t)} />
          }
          if (n.id === DRAFT) {
            return <DraftNode key={DRAFT} pos={p} draft={draft} map={map} seats={seats}
              maxTop={tree.max_top_grant ?? 1000} kioskRemaining={kioskRemaining}
              defaultTop={tree.default_top_grant ?? 50} tree={tree}
              zoom={view.z} pxc={pxPerCredit}
              onConfirm={confirmDraft} onCancel={() => setDraft(null)} />
          }
          return (
            <NodeSquare key={n.id} node={n} pos={p} lod={lod} focused={n.id === focusId}
              dragging={nodeDrag.current?.id === n.id && nodeDrag.current.moved}
              isDrop={dropId === n.id}
              seats={seats} map={map} op={op} slug={slug} pulse={pulse} toast={toast}
              streamEvt={streamEvt} pxc={pxPerCredit} zoom={view.z} act={activity?.[n.id]}
              onSpawn={(t) => spawn(n.id, t)} onConfig={() => setConfigId(n.id)}
              onInbox={() => setInboxId(n.id)} onLineage={() => setLineageId(n.id)}
              onRecenter={() => centerOn(n.id)}   /* recenter AND re-zoom to fill */
              pub={!!tree.public} kioskRemaining={kioskRemaining}
              onDragStart={startNodeDrag} onDragMove={moveNodeDrag} onDragEnd={endNodeDrag} />
          )
        })}
        {tree.org_inbox?.visible && posOf(INBOX) && (
          <div className="sq orginbox"
            style={{
              transform: `translate(${posOf(INBOX).x}px, ${posOf(INBOX).y}px)`,
              width: USER_W, height: INBOX_H,
            }}
            title="the org inbox — outside mail addressed to this organization"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => setOiOpen(true)}>
            <div className="oi-head">
              <PublicIcon fontSize="inherit" /> org inbox
              {tree.org_inbox.unread > 0 &&
                <b className="count">{tree.org_inbox.unread}</b>}
            </div>
            <div className="oi-last">
              {(() => {
                const e = tree.org_inbox.entries?.[tree.org_inbox.entries.length - 1]
                if (!e) return 'no mail yet'
                return `${e.dir === 'in' ? '⭠' : '⭢'} ${e.peer.replace(/^@/, '')}`
              })()}
            </div>
          </div>
        )}
      </div>
      {/* stop pointerdown: the viewport's pan pointer-capture retargets clicks
          and silently kills these buttons */}
      <div className="zoomhud" onPointerDown={(e) => e.stopPropagation()}>
        {/* jump to the SWITCHBOARD from anywhere (user spec) — the same glide
            as clicking the eye; pairs with the per-tab jump buttons for fast
            board ↔ agent hopping */}
        <button className="hud-eye" title="jump to the switchboard"
          onClick={() => centerOn(USER)}>
          <svg viewBox="0 0 48 26">
            <path d="M 2 13 C 13 2, 35 2, 46 13 C 35 24, 13 24, 2 13 Z" />
            <circle className="iris" cx="24" cy="13" r="6.5" />
            <circle className="pupil" cx="24" cy="13" r="2.6" />
          </svg>
        </button>
        <button onClick={() => animateTo({ ...viewRef.current, z: Math.min(Z_MAX, viewRef.current.z * 1.3) }, 220)}><AddIcon fontSize="inherit" /></button>
        <button onClick={() => animateTo({ ...viewRef.current, z: Math.max(0.24, viewRef.current.z / 1.3) }, 220)}><RemoveIcon fontSize="inherit" /></button>
        <button title="fit the whole org" onClick={() => fitAll()}><FullscreenIcon fontSize="inherit" /></button>
      </div>
      {/* the agent TRAY (user spec): a flat list of every agent — tier token,
          name, context wheel, working state — in the nodes' own visual
          language; a row click glides to that agent */}
      <div className="tray-wrap" onPointerDown={(e) => e.stopPropagation()}>
        {trayOpen && (
          <div className="tray">
            {[...map.values()]
              .filter((n) => n.id !== USER && n.id !== DRAFT && !n.isBearerOf)
              .sort((a, b) => {
                const pa = posOf(a.id) ?? { x: 0, y: 0 }
                const pb = posOf(b.id) ?? { x: 0, y: 0 }
                return pa.y - pb.y || pa.x - pb.x
              })
              .map((n) => (
                <div key={n.id} role="button" tabIndex={0}
                  className={'tray-row' + (n.state !== 'live' ? ' off' : '')}
                  onClick={() => centerOn(n.id)}
                  onKeyDown={(e) => { if (e.key === 'Enter') centerOn(n.id) }}>
                  <span className={'tier t-' + n.tier}>{TIER_LETTER[n.tier] ?? '?'}</span>
                  <span className="tray-name" title={n.purpose ?? n.id}>{n.id}</span>
                  <ContextWheel occ={n.occupancy} cw={n.context_window} />
                  {n.busy ? <Activity act={activity?.[n.id]} dotOnly />
                    : n.frozen
                      ? <FrozenIcon fontSize="inherit" className="tray-frozen" />
                      : n.state !== 'live'
                        ? <span className="dim">{n.state}</span>
                        : n.last_status
                          ? <span className={'statusdot ' + n.last_status.status}
                              title={n.last_status.summary} />
                          : <span className="statusdot idle" title="idle" />}
                </div>
              ))}
          </div>
        )}
        <button className="tray-toggle" title="every agent, flat"
          onClick={() => setTrayOpen((o) => !o)}>
          <ViewListIcon fontSize="inherit" /> agents
        </button>
      </div>
      {configId && map.get(configId) && (
        <NodeConfig node={map.get(configId)} map={map} tree={tree} slug={slug}
          op={op} toast={toast} close={() => setConfigId(null)} />
      )}
      {lineageId && map.get(lineageId) && (
        <LineagePanel node={map.get(lineageId)} op={op}
          close={() => setLineageId(null)} />
      )}
      {userCfg && (
        <UserConfig tree={tree} slug={slug} toast={toast}
          close={() => setUserCfg(false)} />
      )}
      {inboxId && map.get(inboxId) && (
        <NodeInboxModal node={map.get(inboxId)} slug={slug} pulse={pulse}
          close={() => setInboxId(null)} />
      )}
      {oiOpen && (
        <OrgInboxModal inbox={tree.org_inbox} map={map} slug={slug} toast={toast}
          close={() => {
            setOiOpen(false)
            // closing the panel acknowledges the whole log (same idiom as the
            // eye's glow: opening acknowledges attention)
            if (tree.org_inbox?.unread) orgInboxRead(slug).catch(() => {})
          }} />
      )}
    </div>
  )
}

// ------------------------------------------------------------- the overseer
function UserNode({ pos, isDrop, stats, inboxCount, seats, mailGlow,
  kiosk, pub, kioskRemaining, kioskSegs, pxc, zoom, onInbox, onGear, onSpawn,
  focused, eyeW, onFocus, posX, onJump, map, op, slug, pulse, toast,
  streamEvt }) {
  const downRef = useRef(null)
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
        if (e.target.closest('button, input, textarea, select, .desk-over')) return
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
      {kiosk?.credits
        ? /* kiosk: the pool is FINITE — a fixed-size bar with per-child slabs,
             exactly like an agent's (user spec); not draggable */
          <CreditBar seat={0} grant={kiosk.credits} committed={stats.circ}
            segments={kioskSegs ?? []} zoom={zoom} pxc={pxc} />
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
      {!focused && !pub && <button className="eye-gear" title="agent-hire defaults"
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => { e.stopPropagation(); onGear?.() }}><SettingsIcon fontSize="inherit" /></button>}
      {/* real seat costs in the hover hints — a literal 0 was technically true
          (infinite pool) but read as wrong next to every other card */}
      {!focused &&
        <SpawnChips onSpawn={onSpawn} free={kioskRemaining ?? Infinity} seats={seats} />}
      {focused && (
        <EyeDesk map={map} op={op} slug={slug} pulse={pulse} toast={toast}
          streamEvt={streamEvt} inboxCount={inboxCount} onInbox={onInbox}
          onGear={onGear} pub={pub} eyeW={eyeW} posX={posX} onJump={onJump} />
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
function EyeDesk({ map, op, slug, pulse, toast, streamEvt, inboxCount,
  onInbox, onGear, pub, eyeW, posX, onJump }) {
  const agents = [...map.values()].filter((n) =>
    n.id !== USER && n.id !== DRAFT && n.state === 'live' && !n.isBearerOf
    && (n.parent === USER || n.audiences_held?.includes(USER)))
    // tab order mirrors the tree's left→right spatial order (user ruling)
    .sort((a, b) => (posX?.(a.id) ?? 0) - (posX?.(b.id) ?? 0))
  const [minned, setMinned] = useState(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem('orgtree-eyemin-' + slug) || '[]'))
    } catch { return new Set() }
  })
  const toggle = (id) => setMinned((s) => {
    const n = new Set(s)
    if (n.has(id)) n.delete(id); else n.add(id)
    localStorage.setItem('orgtree-eyemin-' + slug, JSON.stringify([...n]))
    return n
  })
  const open = agents.filter((a) => !minned.has(a.id))
  // the inner virtual panel matches the card interior through the desk scale
  const innerW = Math.round((eyeW - 4) / 0.13333)
  return (
    <div className="desk-over eye-desk" onWheel={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="desk-inner desk-body eye-inner" style={{ width: innerW }}>
        <div className="cc-head">
          <svg className="eye eye-mini" viewBox="0 0 48 26">
            <path d="M 2 13 C 13 2, 35 2, 46 13 C 35 24, 13 24, 2 13 Z" />
            <circle className="iris" cx="24" cy="13" r="6.5" />
            <circle className="pupil" cx="24" cy="13" r="2.6" />
          </svg>
          <span className="cc-name">you</span>
          <span className="dim">{agents.length} direct line{agents.length === 1 ? '' : 's'}</span>
          <span className="spacer" />
          <button className="cc-icon" title="your inbox" onClick={() => onInbox?.()}>
            <MailIcon fontSize="inherit" />{inboxCount > 0 && <b className="eye-count">{inboxCount}</b>}
          </button>
          {!pub && <button className="cc-icon" title="agent-hire defaults"
            onClick={() => onGear?.()}><SettingsIcon fontSize="inherit" /></button>}
        </div>
        <div className="eye-tabs">
          {agents.map((a) => (
            <span key={a.id} className={'eye-tab' + (minned.has(a.id) ? '' : ' on')}>
              <button className="eye-tab-main"
                title={minned.has(a.id) ? 'open this chat' : 'minimize this chat'}
                onClick={() => toggle(a.id)}>
                <span className={'tier t-' + a.tier}>{TIER_LETTER[a.tier] ?? '?'}</span>
                {a.id}
                {a.busy && <AutorenewIcon fontSize="inherit" className="cc-spin" />}
                {a.mail_pending > 0 && <b className="eye-count">{a.mail_pending}</b>}
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
                    .catch((e) => toast([`error: ${e.message}`]))}>
                  <CloseIcon fontSize="inherit" /></button>}
            </span>
          ))}
          {!agents.length &&
            <span className="dim">no direct lines yet — top-level hires and user-audience holders appear here</span>}
        </div>
        <div className="eye-panels">
          {open.map((a) => (
            <div className="eye-panel" key={a.id}>
              <DeskChat node={a} map={map} op={op} slug={slug} pulse={pulse}
                toast={toast} streamEvt={streamEvt} pub={pub} bare compact />
            </div>
          ))}
          {!open.length && agents.length > 0 &&
            <div className="dim pad">every chat is minimized — click a tab above</div>}
        </div>
      </div>
    </div>
  )
}

function SpawnChips({ onSpawn, free, seats }) {
  return (
    <div className="hsof" onPointerDown={(e) => e.stopPropagation()}>
      {TIERS.map((t) => {
        const seat = seats[t] ?? 0
        const cant = Number.isFinite(free) && free < seat
        return (
          <button key={t} disabled={cant} className={'t-' + t}
            title={cant ? `${t}: needs ${seat} free (has ${free})` : `hire a ${t} (seat ${seat})`}
            onClick={(e) => { e.stopPropagation(); onSpawn(t) }}>
            {TIER_LETTER[t]}
          </button>
        )
      })}
    </div>
  )
}

// ⚙ on the overseer — the org's agent-hire defaults, symmetric with each
// agent's own config modal. Granted to hires that don't state tools: top-level
// agents get exactly this; deeper hires get the ∩ with the superior's
// capability (clamped server-side at hire time). "*" = every registered MCP
// server, present and future.
function UserConfig({ tree, slug, toast, close }) {
  useEsc(close)
  const [asking, setAsking] = useState(false)   // dissolve-all confirmation
  const [defTools, setDefTools] = useState({
    bash: true, web: true, edit: true, subagents: true,
    ...(tree.default_tools ?? {}),
    mcp: [...(tree.default_tools?.mcp ?? ['*'])],
  })
  const [servers, setServers] = useState([])
  const [vis, setVis] = useState(tree.default_visibility ?? 'full')
  // the org's folder holdings (workspace excluded — it is permanent RW).
  // These double as the folder defaults for every hire.
  const [orgDirs, setOrgDirs] = useState(
    (tree.dirs ?? []).filter((d) => d.path !== tree.workspace).map((d) => ({ ...d })))
  const [newPath, setNewPath] = useState('')
  useEffect(() => { getMcpServers().then((r) => setServers(r.servers)).catch(() => {}) }, [])
  const allMcp = defTools.mcp.includes('*')
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> you <span className="dim">· configuration</span></h3>
        <div className="row">
          <button className="danger" onClick={() => setAsking(true)}>
            dissolve all agents</button>
        </div>
        {/* folder access FIRST — same order as the per-agent config (user ruling) */}
        <div className="field-label">folder access</div>
        <div className="dirlist">
          {tree.workspace && (
            <div className="dirrow">
              <span className="chip mono grow">{tree.workspace}</span>
              <span className="modebtn rw"
                title="the org workspace — permanent, always read/write">RW</span>
            </div>
          )}
          {orgDirs.map((d, i) => (
            <div className="dirrow" key={d.path}>
              <span className="chip mono grow">{d.path}</span>
              <button type="button" className={'modebtn ' + d.mode}
                title="toggle read/write vs read-only"
                onClick={() => setOrgDirs(orgDirs.map((x, j) =>
                  j === i ? { ...x, mode: x.mode === 'rw' ? 'ro' : 'rw' } : x))}>
                {d.mode === 'rw' ? 'RW' : 'RO'}
              </button>
              <button type="button" className="iconbtn"
                title="remove from the org (revokes everywhere)"
                onClick={() => setOrgDirs(orgDirs.filter((_, j) => j !== i))}><CloseIcon fontSize="inherit" /></button>
            </div>
          ))}
          <div className="dirrow">
            <input placeholder="add an absolute path"
              value={newPath} onChange={(e) => setNewPath(e.target.value)} />
            <button type="button" className="iconbtn" title="browse for a folder"
              onClick={() => pickFolder().then((r) => {
                if (r.path) setOrgDirs([...orgDirs, { path: r.path, mode: 'rw' }])
              }).catch(() => {})}><FolderIcon fontSize="inherit" /></button>
            <button type="button" className="addrow" onClick={() => {
              if (newPath.trim()) {
                setOrgDirs([...orgDirs, { path: newPath.trim(), mode: 'rw' }])
                setNewPath('')
              }
            }}>add</button>
          </div>
        </div>
        <div className="field-label">tools</div>
        {TOOL_LABELS.map(([k, label]) => (
          <label className="checkline" key={k}>
            <input type="checkbox" checked={!!defTools[k]}
              onChange={(e) => setDefTools({ ...defTools, [k]: e.target.checked })} />
            {label}
          </label>
        ))}
        <div className="field-label">MCP servers</div>
        <label className="checkline">
          <input type="checkbox" checked={allMcp}
            onChange={(e) => setDefTools({
              ...defTools, mcp: e.target.checked ? ['*'] : [...servers] })} />
          all registered servers (current and future)
        </label>
        {!allMcp && servers.map((s) => (
          <label className="checkline" key={s}>
            <input type="checkbox" checked={defTools.mcp.includes(s)}
              onChange={(e) => setDefTools({
                ...defTools,
                mcp: e.target.checked
                  ? [...defTools.mcp, s]
                  : defTools.mcp.filter((x) => x !== s),
              })} />
            <span className="mono">{s}</span>
          </label>
        ))}
        <div className="field-label">org-structure visibility</div>
        <select value={vis} onChange={(e) => setVis(e.target.value)}>
          {VIS_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <div className="row">
          <button className="primary" onClick={() =>
            saveSettings(slug, {
              default_tools: defTools,
              default_visibility: vis,
              org_dirs: orgDirs,
            })
              .then((r) => { toast(r.warnings); close() })
              .catch((e) => toast([`error: ${e.message}`]))}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
      {asking && (
        <ConfirmModal title="dissolve ALL agents?"
          body="Every agent in the entire org is retired at once. Context is kept; rehire brings any of them back."
          confirmLabel="dissolve all"
          onConfirm={() => dissolveAll(slug)
            .then((r) => { toast([`dissolved ${r.nodes} node(s), freed ${r.freed} credits`]); close() })
            .catch((e) => toast([`error: ${e.message}`]))}
          close={() => setAsking(false)} />
      )}
    </div>
  )
}

// Every credit bar is DIRECTLY drag-adjustable (user ruling — no ± buttons):
// draft bars set the pending grant, live bars commit a reallocate on release.
// `min` floors a live bar at its committed amount; `max` caps at parent free.
// The bar spans seat+grant; the SEAT block sits at its foot (credits are
// incompressible — a node's whole holding is visible mass).
function CreditBar({ seat = 0, grant, committed, segments = [], draftMode,
  min = 0, max, maxGhost, onDragValue, onCommit, zoom, pxc }) {
  const [drag, setDrag] = useState(null)          // {y0, g0, val}
  const cur = drag && !draftMode ? drag.val : grant
  const seatLen = seat * pxc
  const len = Math.max(6, (seat + cur) * pxc)
  const start = (e) => {
    if (!draftMode && !onCommit) return
    e.stopPropagation(); e.preventDefault()
    setDrag({ y0: e.clientY, g0: grant, val: grant })
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const move = (e) => {
    if (!drag) return
    const dg = (drag.y0 - e.clientY) / (pxc * zoom)
    const v = Math.round(Math.max(min, Math.min(max ?? Infinity, drag.g0 + dg)))
    if (draftMode) onDragValue(v)
    else setDrag((d) => d && { ...d, val: v })
  }
  const end = () => {
    if (!drag) return
    const v = drag.val
    setDrag(null)
    if (!draftMode && v !== grant) onCommit(v - grant)
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
        <div className="cbar-max" style={{ height: Math.max(6, (seat + max) * pxc) }} />}
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
          const out = []
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

function DraftNode({ pos, draft, map, seats, maxTop, defaultTop, kioskRemaining,
  tree, zoom, pxc, onConfirm, onCancel }) {
  const [name, setName] = useState('')
  const [charter, setCharter] = useState('')
  // pre-hire permissions (user spec): configure the agent's dirs, tool
  // switches, MCP grants and visibility BEFORE hiring — no post-hire
  // adjustment needed. null = the org/parent defaults, untouched.
  const [scope, setScope] = useState(null)
  const [permsOpen, setPermsOpen] = useState(false)
  // named charter presets (user ruling): every .md in docs/charters/. Picked
  // presets appear as CARDS, not text (user spec) — click removes, hover
  // shows the source file's path on disk; only finalizing the hire turns
  // them into actual charter text (prepended to any manual entry).
  const [presets, setPresets] = useState([])
  const [chosen, setChosen] = useState([])
  useEffect(() => {
    getCharters().then((r) => setPresets(r.charters ?? [])).catch(() => {})
  }, [])
  const finalCharter = () =>
    [...chosen.map((c) => c.content), charter].filter((t) => t.trim())
      .join('\n\n')
  // top-level drafts pre-fill the org's default grant (50 unless configured)
  const [grant, setGrant] = useState(
    draft.parent == null ? Math.min(defaultTop ?? 50, maxTop) : 0)
  // user ruling: a user hire cascades grants up the chain (§4.6), so the
  // draft's ceiling is the org slider cap even DEEP in the tree — the parent's
  // own free credits are not a limit. (Kiosk mode will cap this instead.)
  const max = kioskRemaining != null
    ? Math.max(0, Math.min(maxTop, kioskRemaining - (seats[draft.tier] ?? 0)))
    : maxTop
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])
  const ok = name.trim().length > 0
  const hire = () => { if (ok) onConfirm(name.trim(), grant, finalCharter(), scope) }
  return (
    <div className="sq draft" style={{
      transform: `translate(${pos.x}px, ${pos.y}px)`, width: NODE_W, height: NODE_H,
    }} onPointerDown={(e) => e.stopPropagation()}>
      {/* no ghost ceiling: the cascade means the only ceiling is the slider
          cap itself (the ghost stays meaningful on LIVE bars — reallocate
          does NOT cascade) */}
      <CreditBar seat={seats[draft.tier] ?? 0} grant={grant} committed={0}
        draftMode max={max}
        onDragValue={setGrant} zoom={zoom} pxc={pxc} />
      <div className="draft-tag">uninitialized</div>
      {/* full redesign (user request: the old in-card form was cramped): the
          form is authored at NATURAL SCREEN SCALE and counter-scaled into the
          card — the same inverted-scale regime as the desk chat. spawn()
          glides the camera to screen-fill, where authored px ≈ screen px. */}
      <div className="draft-over" onWheel={(e) => e.stopPropagation()}>
        <div className="draft-inner">
          <div className="df-head">
            <span className={'tier t-' + draft.tier}>{TIER_LETTER[draft.tier]}</span>
            <b className="df-title">hire a {draft.tier}</b>
            <span className="dim">
              {draft.parent == null ? 'top level — reports to you'
                : `reports to ${draft.parent}`} · seat {seats[draft.tier] ?? 0}
            </span>
          </div>
          <input className="df-name" autoFocus placeholder="name…" value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && ok) hire() }} />
          {/* grant lives ONLY on the credit bar (user ruling) — no slider,
              no readout line; the bar's own tip reports grant + seat */}
          {presets.length > 0 && (
            <div className="df-presets">
              <div className="preset-cards">
                {chosen.map((c) => (
                  <button key={c.name} className="preset-card"
                    title={c.path ? `${c.path}\n(click to remove)` : 'click to remove'}
                    onClick={() => setChosen((cs) => cs.filter((x) => x.name !== c.name))}>
                    {c.name} <CloseIcon fontSize="inherit" />
                  </button>
                ))}
                <select className="df-preset-add" value=""
                  onChange={(e) => {
                    const p = presets.find((x) => x.name === e.target.value)
                    if (p && !chosen.some((c) => c.name === p.name))
                      setChosen((cs) => [...cs, p])
                  }}>
                  <option value="">add…</option>
                  {presets.filter((p) => !chosen.some((c) => c.name === p.name))
                    .map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
                </select>
              </div>
            </div>
          )}
          <textarea className="df-charter"
            placeholder={'charter (optional): standing role notes'
              + (chosen.length ? ' — appended after the preset cards…' : '…')}
            value={charter} onChange={(e) => setCharter(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && ok) { e.preventDefault(); hire() } }} />
          <div className="df-foot">
            <button onClick={() => setPermsOpen(true)}
              title="folders, tool switches, MCP servers, org visibility — set BEFORE hiring">
              <SettingsIcon fontSize="inherit" /> permissions{scope ? ' ·' : '…'}</button>
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

// Pre-hire permissions (user spec): the same scope surface as the per-agent
// ⚙ panel — folders with RW/RO, tool switches, MCP grants, org visibility —
// but staged locally and applied WITH the hire, so nothing needs adjusting
// after the agent exists. Prefilled from what the hire would inherit anyway.
function DraftScopeModal({ draft, map, tree, scope, onSave, close }) {
  useEsc(close)
  const parent = draft.parent ? map.get(draft.parent) : null
  const inherited = () => ({
    add_dirs: (parent ? parent.scope?.add_dirs : tree.dirs) ?? [],
    tools: parent?.scope?.tools
      ?? tree.default_tools
      ?? { bash: true, web: true, edit: true, subagents: true, mcp: ['*'] },
    org_visibility: parent?.scope?.org_visibility
      ?? tree.default_visibility ?? 'full',
  })
  const base = scope ?? inherited()
  const [dirs, setDirs] = useState(base.add_dirs.map((d) => ({ ...d })))
  const [tools, setTools] = useState({ ...base.tools, mcp: [...(base.tools.mcp ?? [])] })
  const [vis, setVis] = useState(base.org_visibility)
  const [newPath, setNewPath] = useState('')
  const [servers, setServers] = useState([])
  useEffect(() => {
    getMcpServers().then((r) => setServers(r.servers ?? [])).catch(() => {})
  }, [])
  const allMcp = tools.mcp.includes('*')
  // portal to <body>: the draft card lives inside the world transform, where
  // position:fixed would resolve against the SCALED ancestor (giant modal)
  return createPortal(
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> permissions <span className="dim">
          · applied with the hire</span></h3>
        <div className="field-label">folder access</div>
        <div className="dirlist">
          {dirs.map((d, i) => (
            <div className="dirrow" key={d.path}>
              <span className="chip mono grow">{d.path}</span>
              <button type="button" className={'modebtn ' + d.mode}
                title="toggle read/write vs read-only"
                onClick={() => setDirs(dirs.map((x, j) =>
                  j === i ? { ...x, mode: x.mode === 'rw' ? 'ro' : 'rw' } : x))}>
                {d.mode === 'rw' ? 'RW' : 'RO'}
              </button>
              <button type="button" className="iconbtn"
                onClick={() => setDirs(dirs.filter((_, j) => j !== i))}>
                <CloseIcon fontSize="inherit" /></button>
            </div>
          ))}
          <div className="dirrow">
            <input placeholder="add an absolute path"
              value={newPath} onChange={(e) => setNewPath(e.target.value)} />
            <button type="button" className="iconbtn" title="browse for a folder"
              onClick={() => pickFolder().then((r) => {
                if (r.path) setDirs([...dirs, { path: r.path, mode: 'rw' }])
              }).catch(() => {})}><FolderIcon fontSize="inherit" /></button>
            <button type="button" className="addrow" onClick={() => {
              if (newPath.trim()) {
                setDirs([...dirs, { path: newPath.trim(), mode: 'rw' }])
                setNewPath('')
              }
            }}>add</button>
          </div>
        </div>
        <div className="field-label">tools</div>
        {TOOL_LABELS.map(([k, label]) => (
          <label className="checkline" key={k}>
            <input type="checkbox" checked={!!tools[k]}
              onChange={(e) => setTools({ ...tools, [k]: e.target.checked })} />
            {label}
          </label>
        ))}
        <div className="field-label">MCP servers</div>
        <label className="checkline">
          <input type="checkbox" checked={allMcp}
            onChange={(e) => setTools({
              ...tools, mcp: e.target.checked ? ['*'] : [...servers] })} />
          all registered servers (current and future)
        </label>
        {!allMcp && servers.map((s) => (
          <label className="checkline" key={s}>
            <input type="checkbox" checked={tools.mcp.includes(s)}
              onChange={(e) => setTools({
                ...tools,
                mcp: e.target.checked
                  ? [...tools.mcp, s]
                  : tools.mcp.filter((x) => x !== s),
              })} />
            <span className="mono">{s}</span>
          </label>
        ))}
        <div className="field-label">org-structure visibility</div>
        <select value={vis} onChange={(e) => setVis(e.target.value)}>
          {VIS_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <div className="hint">
          Grants clamp to what the parent holds (№30) — anything beyond its
          capability is trimmed at hire with a warning.
        </div>
        <div className="row">
          <button className="primary" onClick={() =>
            onSave({ add_dirs: dirs, tools, org_visibility: vis })}>apply</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>,
    document.body
  )
}

function NodeSquare({ node, pos, lod, focused, dragging, isDrop, seats, map, op, slug,
  pulse, toast, streamEvt, pxc, zoom, act, onSpawn, onConfig, onInbox, onLineage,
  onRecenter, pub, kioskRemaining, onDragStart, onDragMove, onDragEnd }) {
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
  // the card never changes size or place — the desk fades in over it (design ruling)
  const style = {
    transform: `translate(${pos.x}px, ${pos.y}px)`,
    width: NODE_W, height: NODE_H,
    zIndex: focused ? 5 : dragging ? 8 : undefined,
  }
  return (
    <div className={cls.join(' ')} style={style}
      onPointerDown={(e) => { if (!focused) onDragStart(e, node.id) }}
      onPointerMove={(e) => onDragMove(e, node.id)}
      onPointerUp={(e) => onDragEnd(e, node.id, node, focused)}
      onPointerCancel={(e) => onDragEnd(e, node.id, node, focused)}>
      {live && !node.isBearerOf && (
        <CreditBar seat={node.seat} grant={node.grant} committed={node.grant - node.free}
          segments={node.children.filter((c) => c.state !== 'archived')
            .map((c) => ({ seat: c.seat, grant: c.grant }))}   /* unrecoverable still holds */
          min={node.grant - node.free}
          max={node.parent === USER
            ? Infinity
            : node.grant + (map.get(node.parent)?.free ?? 0)}
          maxGhost={node.parent !== USER}
          onCommit={(delta) => op({ op: 'reallocate', node: node.id, delta })}
          zoom={zoom} pxc={pxc} />
      )}
      {/* the whole world-scaled head disappears at focus — the desk renders its
          own compact chrome inside the counter-scaled panel (a world-scaled name
          and tier chip blow up to poster size at desk zoom) */}
      {!focused && <div className="sq-head">
        <span className={'tier t-' + node.tier}>{TIER_LETTER[node.tier] ?? '?'}</span>
        <span className="name" title={node.id}>{node.id}</span>
        <button className={'mailbtn' + (node.mail_pending > 0 ? ' has' : '')}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onInbox() }}>
          <MailIcon fontSize="inherit" />{node.mail_pending > 0 && <span className="count">{node.mail_pending}</span>}
        </button>
        {!pub && <button className="gearbtn"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onConfig() }}><SettingsIcon fontSize="inherit" /></button>}
        <ContextWheel occ={node.occupancy} cw={node.context_window} />
        {lod === 'mini' && node.last_status &&
          <span className={'statusdot ' + node.last_status.status}
            title={`${node.last_status.status} — ${node.last_status.summary ?? ''}`} />}
        {node.busy && <Activity act={act} dotOnly />}
        {node.last_error && <span className="errdot" title={node.last_error} />}
      </div>}
      {!focused && lod === 'mini' && <div className="mini-name">{node.id}</div>}
      {node.busy && !focused && lod !== 'mini' && <Activity act={act} />}
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
              title={node.frozen.error}><FrozenIcon fontSize="inherit" /> limit</span>}
          {node.limit_locked && <span className="badge dim"><LockIcon fontSize="inherit" /> limit</span>}
          {stackN > 0 &&
            <button className="badge stackbadge"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); onLineage() }}><LayersIcon fontSize="inherit" /> {stackN}</button>}
        </div>
      )}
      {focused && (
        <DeskChat node={node} map={map} op={op} slug={slug} pulse={pulse} toast={toast}
          streamEvt={streamEvt} onLineage={onLineage} onConfig={onConfig}
          onRecenter={onRecenter} pub={pub} />
      )}
      {/* user ruling: chips are NEVER disabled by the node's own free credits —
          a user hire §4.6-cascades, granting the chain whatever it lacks.
          (Kiosk mode will pass the cap remainder here instead.) */}
      {live && !node.isBearerOf && !node.bearer_state &&
        <SpawnChips onSpawn={onSpawn} free={kioskRemaining ?? Infinity} seats={seats} />}
    </div>
  )
}

// ------------------------------------------------------------ node ⚙ config
const VIS_OPTIONS = [
  ['self', 'self'],
  ['team', 'team'],
  ['subtree', 'subtree'],
  ['full', 'full (default)'],
]

const TOOL_LABELS = [
  ['bash', 'terminal (Bash)'],
  ['web', 'web browsing (search + fetch)'],
  ['edit', 'file editing (Write / Edit / notebooks)'],
  ['subagents', 'ephemeral subagents (Task / Agent tool)'],
]

function NodeConfig({ node, map, tree, slug, op, toast, close }) {
  useEsc(close)
  const [asking, setAsking] = useState(null)   // null | 'delete' | 'dissolve'
  const [dirs, setDirs] = useState(node.scope.add_dirs.map((d) => ({ ...d })))
  const [tools, setTools] = useState({
    bash: true, web: true, edit: true, subagents: true,
    ...(node.scope.tools ?? {}),
    mcp: [...(node.scope.tools?.mcp ?? [])],
  })
  const [vis, setVis] = useState(node.scope.org_visibility ?? 'full')
  const [charter, setCharter] = useState(node.charter ?? '')
  const [teamCharter, setTeamCharter] = useState(node.team_charter ?? '')
  const [model, setModel] = useState(node.tier)
  const [newPath, setNewPath] = useState('')
  const [servers, setServers] = useState([])
  useEffect(() => { getMcpServers().then((r) => setServers(r.servers)).catch(() => {}) }, [])
  const parent = map.get(node.id)?.parent
  const parentNode = parent && parent !== USER ? map.get(parent) : null
  const parentTools = parentNode?.scope?.tools ?? null   // null = the user: everything
  const parentDirs = parentNode
    ? (parentNode.scope?.add_dirs ?? [])
    : (tree.dirs ?? []).map((d) => ({ ...d }))   // org holdings carry modes now
  const addable = parentDirs.filter((pd) => !dirs.some((d) => d.path === pd.path))
  const parentHolds = (k) => parentTools == null || parentTools[k] !== false
  // "*" = every registered server, present and future
  const parentHoldsMcp = (s) => parentTools == null
    || (parentTools.mcp ?? []).includes('*') || (parentTools.mcp ?? []).includes(s)
  const holdsAllMcp = tools.mcp.includes('*')
  return (
    // pointerdown must not reach the viewport: its pan pointer-CAPTURE retargets
    // the click, so backdrop-close and every button in here silently broke
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> {node.id} <span className="dim">· {node.tier} · configuration</span></h3>

        <div className="row">
          {node.state === 'live' && !node.children.some((c) => c.state !== 'archived') &&
            <button className="danger" onClick={() =>
              op({ op: 'retire', node: node.id }).then(close).catch(() => {})}>
              retire · {node.seat + node.grant}</button>}
          {node.state === 'live' && node.children.some((c) => c.state !== 'archived') &&
            <button className="danger" onClick={() => setAsking('dissolve')}>
              dissolve subtree · {node.seat + node.grant}</button>}
          {node.state === 'archived' &&
            <button className="primary" onClick={() =>
              op({ op: 'rehire', node: node.id }).then(close).catch(() => {})}>
              rehire (context intact)</button>}
          <span style={{ flex: 1 }} />
          <button className="danger delete"
            onClick={() => setAsking('delete')}><DeleteIcon fontSize="inherit" /> delete permanently</button>
        </div>

        <div className="field-label">folder access</div>
        <div className="dirlist">
          {dirs.map((d, i) => (
            <div className="dirrow" key={d.path}>
              <span className="chip mono grow">{d.path}</span>
              <button type="button" className={'modebtn ' + d.mode}
                title="toggle read/write vs read-only"
                onClick={() => setDirs(dirs.map((x, j) =>
                  j === i ? { ...x, mode: x.mode === 'rw' ? 'ro' : 'rw' } : x))}>
                {d.mode === 'rw' ? 'RW' : 'RO'}
              </button>
              <button type="button" className="iconbtn" title="revoke"
                onClick={() => setDirs(dirs.filter((_, j) => j !== i))}><CloseIcon fontSize="inherit" /></button>
            </div>
          ))}
          {addable.length > 0 && (
            <div className="dirrow">
              <select value="" onChange={(e) => {
                const pd = addable.find((x) => x.path === e.target.value)
                if (pd) setDirs([...dirs, { ...pd }])
              }}>
                <option value="" disabled>+ grant a folder the {parent && parent !== USER ? 'parent holds' : 'org holds'}…</option>
                {addable.map((pd) => <option key={pd.path} value={pd.path}>{pd.path} ({pd.mode})</option>)}
              </select>
            </div>
          )}
          {(!parent || parent === USER) && (
            <div className="dirrow">
              <input placeholder="or any absolute path (top-level: you grant freely)"
                value={newPath} onChange={(e) => setNewPath(e.target.value)} />
              <button type="button" className="iconbtn" title="browse for a folder"
                onClick={() => pickFolder().then((r) => {
                  if (r.path) setDirs([...dirs, { path: r.path, mode: 'rw' }])
                }).catch(() => {})}><FolderIcon fontSize="inherit" /></button>
              <button type="button" className="addrow" onClick={() => {
                if (newPath.trim()) { setDirs([...dirs, { path: newPath.trim(), mode: 'rw' }]); setNewPath('') }
              }}>add</button>
            </div>
          )}
        </div>

        <div className="field-label">tools</div>
        {TOOL_LABELS.map(([k, label]) => (
          <label className="checkline" key={k}>
            <input type="checkbox" checked={tools[k] && parentHolds(k)}
              disabled={!parentHolds(k)}
              onChange={(e) => setTools({ ...tools, [k]: e.target.checked })} />
            {label}
            {!parentHolds(k) && <span className="dim"> — parent doesn't hold it</span>}
          </label>
        ))}

        <div className="field-label">MCP servers (from your global registry)</div>
        {servers.length === 0 && <div className="hint">none registered</div>}
        {servers.map((s) => (
          <label className="checkline" key={s}>
            <input type="checkbox"
              checked={(holdsAllMcp || tools.mcp.includes(s)) && parentHoldsMcp(s)}
              disabled={!parentHoldsMcp(s)}
              onChange={(e) => setTools({
                ...tools,
                // unchecking under "*" materializes the concrete server list
                mcp: e.target.checked
                  ? (holdsAllMcp ? tools.mcp : [...tools.mcp, s])
                  : (holdsAllMcp ? servers.filter((x) => x !== s)
                                 : tools.mcp.filter((x) => x !== s)),
              })} />
            <span className="mono">{s}</span>
            {!parentHoldsMcp(s) && <span className="dim"> — parent doesn't hold it</span>}
          </label>
        ))}

        <div className="field-label">model (switchable on the fly — context
          survives; cheaper frees the seat difference to the agent, pricier
          bubbles any shortfall up the chain)</div>
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          {['haiku', 'sonnet', 'opus', 'fable'].map((t) => (
            <option key={t} value={t}>
              {t} · seat {{ haiku: 1, sonnet: 3, opus: 5, fable: 10 }[t]}
            </option>
          ))}
        </select>

        <div className="field-label">org-structure visibility</div>
        <select value={vis} onChange={(e) => setVis(e.target.value)}>
          {VIS_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>

        <div className="field-label">charter</div>
        <textarea rows={3} value={charter} onChange={(e) => setCharter(e.target.value)}
          />
        <div className="field-label">team charter</div>
        <textarea rows={3} value={teamCharter} onChange={(e) => setTeamCharter(e.target.value)}
          />
        <div className="row">
          <button className="primary" onClick={() =>
            (model !== node.tier
              ? op({ op: 'switch_model', node: node.id, tier: model })
              : Promise.resolve())
              .then(() => saveScope(slug, node.id,
                { add_dirs: dirs, tools, org_visibility: vis,
                  charter, team_charter: teamCharter }))
              .then((r) => { toast(r.warnings); close() })
              .catch((e) => toast([`error: ${e.message}`]))}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
      {asking === 'dissolve' && (
        <ConfirmModal title={`dissolve ${node.id}?`}
          body="Its entire suborganization is retired with it. Context is kept; rehire brings nodes back."
          confirmLabel="dissolve"
          onConfirm={() => op({ op: 'dissolve', node: node.id }).then(close).catch(() => {})}
          close={() => setAsking(null)} />
      )}
      {asking === 'delete' && (() => {
        const count = (function c(n) {
          return n.children.reduce((a, k) => a + 1 + c(k), 0)
        })(node)
        const gens = (node.lineage ?? []).length
        return <ConfirmModal title={`permanently delete ${node.id}?`}
          body={'Erased from the organization — seats, records, mail and lineage'
            + (count ? `, plus ${count} descendant(s)` : '')
            + (gens ? ` and ${gens} prior generation(s)` : '')
            + '. Session transcripts remain on disk. This cannot be undone.'}
          confirmLabel="delete permanently"
          onConfirm={() => op({ op: 'delete', node: node.id }).then(close).catch(() => {})}
          close={() => setAsking(null)} />
      })()}
    </div>
  )
}

function ContextWheel({ occ, cw, onCompact }) {
  if (!occ || !cw) return null
  const frac = Math.min(1, occ / cw)
  const R = 5.5, C = 2 * Math.PI * R
  const svg = (
    <svg className="ctxwheel" viewBox="0 0 16 16" width="15" height="15">
      <title>{`context: ${Math.round(occ / 1000)}k / ${Math.round(cw / 1000)}k (${Math.round(frac * 100)}%)`
        + (onCompact ? ' — click to compact now' : '')}</title>
      <circle cx="8" cy="8" r={R} className="track" />
      <circle cx="8" cy="8" r={R} className={'fill' + (frac >= 0.8 ? ' hot' : '')}
        strokeDasharray={`${C * frac} ${C}`} transform="rotate(-90 8 8)" />
    </svg>
  )
  // clickable ONLY where a handler is wired — the zoomed desk (user ruling);
  // the zoomed-out card wheel stays a passive indicator
  if (!onCompact) return svg
  return <button className="ctxbtn" onClick={onCompact}>{svg}</button>
}

const shortTool = (t) => (t || 'tool').replace(/^mcp__([^_]+)__/, '$1: ')

function Activity({ act, dotOnly }) {
  const phase = act?.phase ?? 'thinking'
  if (dotOnly) {
    return phase === 'tool'
      ? <span className="actgear" title={`running ${shortTool(act?.tool)}`}><SettingsIcon fontSize="inherit" /></span>
      : <span className="busydot" title={phase} />
  }
  return (
    <div className="actlabel">
      {phase === 'tool'
        ? <><span className="actgear"><SettingsIcon fontSize="inherit" /></span> {shortTool(act?.tool)}</>
        : phase === 'writing' ? <><EditIcon fontSize="inherit" /> writing</> : <><AutorenewIcon fontSize="inherit" className="cc-spin" /> thinking</>}
      <span className="actdots" />
    </div>
  )
}

// The desk is styled as a miniature Claude Code chat window (design ruling):
// compact one-line chrome, plain assistant text, boxed user turns, ⏺ tool
// lines, and a bordered composer with the model name in its footer row.
function DeskChat({ node, map, op, slug, pulse, toast, streamEvt, onLineage, onConfig,
  onRecenter, pub, bare = false, compact = false }) {
  const [chat, setChat] = useState(null)
  const [text, setText] = useState('')
  const [pending, setPending] = useState([])   // sent, not yet in the transcript
  const [asking, setAsking] = useState(false)
  const [askCompact, setAskCompact] = useState(false)
  const [view, setView] = useState('chat')     // chat | history | files | inbox
  const [live_feed, setLiveFeed] = useState([])
  const [draft, setDraft] = useState('')       // the token-streamed growing reply
  const scroller = useRef(null)
  const loadedRef = useRef(false)     // first load always lands at the bottom
  const live = node.state === 'live'
  const nearBottom = () => {
    const el = scroller.current
    return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }
  const toBottom = () => requestAnimationFrame(() => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight
  })

  const refresh = useCallback((force = false) =>
    getChat(slug, node.id).then((c) => {
      // sticky-bottom: follow new content only if the reader is already at
      // (or near) the bottom — never yank them out of scrollback
      const stick = force || !loadedRef.current || nearBottom()
      loadedRef.current = true
      setChat(c)
      // a pending message graduates once the transcript contains it — by
      // containment, not equality: the turn text is a mail envelope now
      setPending((p) => p.filter((x) =>
        !c.messages.slice(-20).some((m) => m.role === 'user' && m.text.includes(x))))
      // the fetched transcript supersedes everything streamed so far — keeping
      // the feed around doubled the whole in-flight turn (transcript copy +
      // live copy). Stream events landing after this fetch re-append.
      setLiveFeed([])
      if (stick) toBottom()
    }).catch(() => {}), [slug, node.id])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    if (pulse && pulse.node === node.id) {
      if (pulse.event === 'turn_done') { setLiveFeed([]); setDraft('') }
      refresh()
    }
  }, [pulse, node.id, refresh])
  useEffect(() => {                       // live per-message feed while working
    if (streamEvt && streamEvt.node === node.id) {
      const stick = nearBottom()
      if (streamEvt.kind === 'delta') {
        // token streaming (user spec): the reply grows word-by-word; the
        // complete message event replaces it when the block finishes
        setDraft((d) => (d + streamEvt.text).slice(-12000))
        if (stick) toBottom()
        return
      }
      if (streamEvt.kind === 'steered') {
        // a pending user message just got DELIVERED mid-task
        setPending((p) => p.filter((x) => !streamEvt.text.includes(x)))
      }
      if (streamEvt.kind === 'text') setDraft('')
      setLiveFeed((f) => [...f.slice(-24), streamEvt])
      if (stick) toBottom()
    }
  }, [streamEvt, node.id])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!chat?.busy) return
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [chat?.busy, refresh])

  // an archived agent still RECEIVES mail (user ruling) — it queues in its
  // inbox and gets acted on at rehire; only unrecoverable nodes refuse
  const canMail = live || node.state === 'archived'
  const send = () => {
    const t = text.trim()
    if (!t || !canMail) return
    setText('')
    // NOT an optimistic chat append — while the node is busy the message is
    // only queued, so transcript refreshes would wipe it until the queued
    // turn starts. The pending list survives refreshes.
    setPending((p) => [...p, t])
    if (live) setChat((c) => c && ({ ...c, busy: true }))
    toBottom()
    sendMessage(slug, node.id, t).then(() => refresh(true))
      .catch((e) => toast([`error: ${e.message}`]))
  }

  const liveKids = node.children.some((c) => c.state === 'live')
  const content = (
    <>
      <div className="cc-head">
        <span className={'tier t-' + node.tier}>{TIER_LETTER[node.tier] ?? '?'}</span>
        <span className="cc-name" title={node.purpose ?? node.id}>{node.id}</span>
        <ContextWheel occ={chat?.occupancy ?? node.occupancy} cw={node.context_window}
          onCompact={live && !node.bearer_state
            ? () => setAskCompact(true) : undefined} />
        {node.last_status &&
          <span className={'statuschip ' + node.last_status.status}
            title={node.last_status.summary}>{node.last_status.status}</span>}
        {/* one row for everything (user ruling — the 900px panel has room);
            interruption lives on the composer's stop button, not up here */}
        {chat?.busy &&
          <span className="cc-working"><AutorenewIcon fontSize="inherit" className="cc-spin" /> working</span>}
        {node.frozen &&
          <span className="badge frozen" title={node.frozen.error}>
            <FrozenIcon fontSize="inherit" /> usage limit{node.frozen.until ? ` · resumes ${node.frozen.until}` : ''}</span>}
        {node.limit_locked &&
          <span className="badge dim"><LockIcon fontSize="inherit" /> limit</span>}
        {!compact && node.generation > 0 &&
          <button className="badge stackbadge"
            onClick={onLineage}>gen {node.generation} <LayersIcon fontSize="inherit" /></button>}
        {!compact && node.bearer_state &&
          <span className={'badge ' + (node.bearer_state === 'preserving' ? 'dim' : '')}>
            {node.bearer_state}</span>}
        {!compact && node.audiences_held?.map((g) => (
          <span key={g} className={'badge ' + (g === USER ? 'free' : '')}>
            <HearingIcon fontSize="inherit" />
            {g === USER ? 'user' : g === EXTERN ? 'org inbox' : g}
            <button className="chip-x"
              onClick={() => audienceAction(slug, 'revoke', node.id, g)
                .then(() => toast([`audience ${node.id}→${g} rescinded`]))
                .catch((e) => toast([`error: ${e.message}`]))}><CloseIcon fontSize="inherit" /></button>
          </span>
        ))}
        {!compact && node.cost_usd > 0 && <span className="badge dim">${node.cost_usd.toFixed(2)}</span>}
        {chat?.queued > 0 && <span className="badge">{chat.queued} queued</span>}
        <span className="spacer" />
        {/* compact (switchboard panel): chat only — the agent's own desk keeps
            the full chrome (actions, tabs, gear) */}
        {!compact && <span className="cc-actions">
          {live && !liveKids &&
            <button className="danger"
              onClick={() => op({ op: 'retire', node: node.id })}>
              retire · {node.seat + node.grant}</button>}
          {live && liveKids &&
            <button className="danger" onClick={() => setAsking(true)}>
              dissolve · {node.seat + node.grant}</button>}
          {!live && <button onClick={() => op({ op: 'rehire', node: node.id })}>rehire</button>}
        </span>}
        {!compact && <span className="cc-tabs">
          {['chat', 'history', 'files', 'inbox'].map((v) => (
            <button key={v} className={view === v ? 'on' : ''}
              onClick={() => setView(v)}>
              {v}{v === 'inbox' && chat?.mail_pending > 0 ? ` ${chat.mail_pending}` : ''}
            </button>
          ))}
        </span>}
        {!compact && !pub && <button className="cc-icon" onClick={onConfig}><SettingsIcon fontSize="inherit" /></button>}
      </div>
      {asking && (
        <ConfirmModal title={`dissolve ${node.id}?`}
          body="Its entire suborganization is retired with it. Context is kept; rehire brings nodes back."
          confirmLabel="dissolve"
          onConfirm={() => op({ op: 'dissolve', node: node.id })}
          close={() => setAsking(false)} />
      )}
      {askCompact && (
        <ConfirmModal title={`compact ${node.id} now?`}
          body="Same as the automatic split: the session forks and compacts — the successor carries on under this name; the pre-compaction self is archived in place as a consultable knowledge bearer."
          confirmLabel="compact"
          onConfirm={() => compactNode(slug, node.id)
            .then(() => toast([`compaction of ${node.id} started`]))
            .catch((e) => toast([`error: ${e.message}`]))}
          close={() => setAskCompact(false)} />
      )}
      {chat?.last_error && <div className="desk-error"><WarnIcon fontSize="inherit" /> {chat.last_error}</div>}
      {view === 'chat' && (
        <>
          <div className="msgs" ref={scroller}>
            {!chat && <div className="dim pad">loading…</div>}
            {chat && !chat.messages.length && !live_feed.length &&
              <div className="dim pad">no conversation yet</div>}
            {chat?.messages.map((m, i) => <Msg key={i} m={m} />)}
            {live_feed.map((f, i) => (
              f.kind === 'tool'
                ? <div key={'f' + i} className="msg live tools"><DotIcon fontSize="inherit" className="tooldot" /> {f.text}</div>
                : f.kind === 'steered'
                  ? <div key={'f' + i} className="msg user live md"
                      dangerouslySetInnerHTML={md(stripEnvelope(f.text))} />
                  : <div key={'f' + i} className="msg assistant live md"
                      dangerouslySetInnerHTML={md(f.text)} />
            ))}
            {draft && <div className="msg assistant live md draft"
              dangerouslySetInnerHTML={md(draft)} />}
            {pending.map((p, i) => (
              <div key={'q' + i} className="msg user pending md"
                dangerouslySetInnerHTML={md(p)} />
            ))}
            {chat?.busy && !draft && <div className="working"><AutorenewIcon fontSize="inherit" className="cc-spin" /> working<span className="actdots" /></div>}
          </div>
          {/* send sits BESIDE the input; no model-name footer row (the tier
              chip in the header already says it) — reclaimed vertical space */}
          <div className={'cc-composer' + (canMail ? '' : ' off')}>
            <textarea rows={2} value={text} disabled={!canMail}
              placeholder={live ? `message ${node.id}…`
                : node.state === 'archived'
                  ? `message ${node.id} — queued until rehire…` : node.state}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
              }} />
            {/* while the agent responds the send button becomes a STOP —
                same as Claude Code; Enter still queues a message */}
            {chat?.busy
              ? <button className="cc-send stop" title="interrupt the current response"
                  onClick={() => interruptNode(slug, node.id)
                    .then((r) => { if (!r.interrupted) toast([`error: ${r.reason}`]) })
                    .catch((e) => toast([`error: ${e.message}`]))}><StopIcon fontSize="inherit" /></button>
              : <button className="cc-send" disabled={!canMail || !text.trim()}
                  onClick={send}><ArrowUpIcon fontSize="inherit" /></button>}
          </div>
        </>
      )}
      {view === 'history' && <HistoryView slug={slug} nid={node.id} />}
      {view === 'files' && <FilesView slug={slug} nid={node.id} />}
      {view === 'inbox' && <InboxView slug={slug} nid={node.id} pulse={pulse} />}
    </>
  )
  // bare: the switchboard hosts many chats inside ONE counter-scaled surface —
  // no overlay wrapper, no second scale (that would double-scale), no
  // recenter-on-click
  if (bare) return <div className="desk-body eye-chat">{content}</div>
  return (
    <div className="desk-over" onWheel={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        // clicking the desk's non-interactive space recenters the camera on
        // it (user ruling) — but never steal clicks meant for controls, and
        // never fight an in-progress text selection
        if (e.target.closest('button, input, textarea, select, a, label, .mailrow')) return
        if (window.getSelection()?.toString()) return
        onRecenter?.()
      }}>
      <div className="desk-inner desk-body">{content}</div>
    </div>
  )
}

// One mail interface, everywhere (user ruling: the user's and the agents'
// inboxes function identically), laid out like a webmail client: the list on
// the left (sender · time · truncated brief — mails have no subjects), the
// selected message opened in the reading pane on the right. Waiting/unread
// mail sorts on top and is highlighted until read/delivered.
export function MailList({ pending = [], delivered = [], waitLabel, sender, outgoing,
  onRead }) {
  // newest first throughout (user ruling) — waiting/unread stays grouped on top
  const all = [
    ...[...pending].reverse().map((m) => ({ ...m, _wait: true })),
    ...[...delivered].reverse(),
  ]
  // selection is BY IDENTITY, not index — marking a mail read reshuffles the
  // list, and an index would silently land on a different mail
  const keyOf = (m) => m?.id ?? `${m?.at}|${m?.from}|${(m?.body ?? '').slice(0, 24)}`
  const [selId, setSelId] = useState(null)
  const S = sender ?? ((id) => <span>{id === USER ? '@user' : id}</span>)
  const cur = all.find((m) => keyOf(m) === selId) ?? all[0]
  // per-mail read (user ruling): a VIEWED unread mail is marked read the
  // moment you click OFF it — select another mail, or leave the list
  const curRef = useRef(null); curRef.current = cur
  const readRef = useRef(onRead); readRef.current = onRead
  const leave = (m) => { if (m?._wait && m.id) readRef.current?.(m) }
  useEffect(() => () => leave(curRef.current), [])
  const party = (m) => (outgoing ? m.to : m.from)
  // a custom sender renderer owns the whole head identity (it receives the
  // mail too — the org inbox uses this for "@agent as @org → @recipient")
  const customS = outgoing && sender != null
  const brief = (b) => (b ?? '').trim().replace(/\s+/g, ' ').slice(0, 90)
  const when = (at) => (at ?? '').slice(5, 16).replace('T', ' ')
  if (!all.length) return <div className="dim pad">no mail yet</div>
  return (
    <div className="mailer">
      <div className="mailer-list">
        {all.map((m, i) => (
          <div key={keyOf(m)}
            className={'mailrow' + (m === cur ? ' on' : '') + (m._wait ? ' unread' : '')}
            onClick={() => {
              if (keyOf(m) !== keyOf(cur)) leave(cur)
              setSelId(keyOf(m))
            }}>
            <div className="l1">
              <span className="mfrom">
                {outgoing ? '→ ' : ''}{party(m) === USER ? '@user' : party(m)}
              </span>
              <span className="mtime">{when(m.at)}</span>
            </div>
            <div className="l2">{brief(m.body)}</div>
          </div>
        ))}
      </div>
      <div className="mailer-read">
        {cur && (
          <>
            <div className="mailer-head">
              {outgoing && !customS && <span className="dim">to</span>}
              {S(party(cur), cur)}
              <span className="dim">{cur.kind}</span>
              {cur.relationship && <span className="dim">{cur.relationship}</span>}
              <span className="dim">{cur.at}</span>
              {cur._wait && <span className="wait">{waitLabel}</span>}
            </div>
            <div className="mailer-body md" dangerouslySetInnerHTML={md(cur.body)} />
          </>
        )}
      </div>
    </div>
  )
}

// The node's own mailbox (user ruling: its own tab, separate from history),
// with the same folders as the user's: inbox + sent.
function InboxView({ slug, nid, pulse }) {
  const [box, setBox] = useState(null)
  const [folder, setFolder] = useState('inbox')
  useEffect(() => {
    getNodeInbox(slug, nid).then(setBox)
      .catch(() => setBox({ pending: [], delivered: [], sent: [] }))
  }, [slug, nid, pulse])
  return (
    <div className="mailwrap">
      <MailFolders folder={folder} setFolder={setFolder}
        unread={box?.pending.length ?? 0} />
      <div className="mailpane">
        {box == null
          ? <div className="dim pad">loading…</div>
          : folder === 'inbox'
            ? <MailList pending={box.pending} delivered={box.delivered}
                waitLabel="awaiting next turn" />
            : <MailList delivered={box.sent ?? []} outgoing />}
      </div>
    </div>
  )
}

export function MailFolders({ folder, setFolder, unread }) {
  return (
    <div className="mail-folders">
      {['inbox', 'sent'].map((f) => (
        <button key={f} className={folder === f ? 'on' : ''}
          onClick={() => setFolder(f)}>
          {f}{f === 'inbox' && unread > 0 ? ` ${unread}` : ''}
        </button>
      ))}
    </div>
  )
}

// ✉ on a card — the node's inbox as a modal, the same interface the eye's
// ✉ opens for the user's own inbox.
function NodeInboxModal({ node, slug, pulse, close }) {
  useEsc(close)
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide" onClick={(e) => e.stopPropagation()}>
        <h3><MailIcon fontSize="inherit" /> {node.id} <span className="dim">· inbox</span></h3>
        <InboxView slug={slug} nid={node.id} pulse={pulse} />
        <div className="row">
          <button className="primary" onClick={close}>close</button>
        </div>
      </div>
    </div>
  )
}

function HistoryView({ slug, nid }) {
  const [items, setItems] = useState(null)
  useEffect(() => { getHistory(slug, nid).then((r) => setItems(r.items)).catch(() => setItems([])) }, [slug, nid])
  return (
    <div className="msgs">
      {items == null && <div className="dim pad">loading…</div>}
      {items?.length === 0 && <div className="dim pad">nothing recorded yet</div>}
      {items?.map((it, i) => (
        <div key={i} className="hist-row">
          <span className="dim">{it.at}</span>
          <b>{it.kind}</b>
          <span className="dim">{it.actor}</span>
          <span>{it.detail.gist ?? it.detail.text ?? Object.entries(it.detail)
            .filter(([k]) => k !== 'gist').map(([k, v]) => `${k}=${v}`).join(' · ')}</span>
        </div>
      ))}
    </div>
  )
}

function FilesView({ slug, nid }) {
  const [path, setPath] = useState('')
  const [data, setData] = useState(null)
  useEffect(() => { getScratch(slug, nid, path).then(setData).catch(() => setData(null)) }, [slug, nid, path])
  const up = () => setPath(path.split('/').slice(0, -1).join('/'))
  return (
    <div className="msgs files">
      <div className="hist-row">
        <button onClick={() => setPath('')}>scratch</button>
        {path && <button onClick={up}><ArrowUpIcon fontSize="inherit" /> up</button>}
        <span className="dim mono">/{path}</span>
      </div>
      {!data && <div className="dim pad">empty or unreadable</div>}
      {data?.entries?.map((e) => (
        <div key={e.name} className="hist-row">
          {e.dir
            ? <button onClick={() => setPath(path ? `${path}/${e.name}` : e.name)}><FolderIcon fontSize="inherit" /> {e.name}</button>
            : <button onClick={() => setPath(path ? `${path}/${e.name}` : e.name)}><FileIcon fontSize="inherit" /> {e.name}</button>}
          {!e.dir && <span className="dim">{e.size} B</span>}
        </div>
      ))}
      {data?.content != null && <pre className="filepre">{data.content}</pre>}
    </div>
  )
}

// the ORG INBOX viewer (user spec): the org's correspondence with the outside
// world — chatq sessions and other orgs — as one chronological thread. Also
// where the user staffs the "client contact" role: grant/revoke org-inbox
// audiences so chosen sub-agents read and answer outside mail.
function OrgInboxModal({ inbox, map, slug, toast, close }) {
  useEsc(close)
  const [grantee, setGrantee] = useState('')
  const [folder, setFolder] = useState('inbox')
  const holders = inbox?.holders ?? []
  const candidates = [...map.values()].filter((n) =>
    n.id !== USER && n.id !== DRAFT && n.state === 'live' && !n.isBearerOf
    && n.parent !== USER && !holders.includes(n.id))
  const entries = inbox?.entries ?? []
  // the org inbox tracks read state as ONE high-water mark over the log — the
  // tail beyond it renders as unread; any read action clears the whole mark
  const readFrom = entries.length - (inbox?.unread ?? 0)
  const rows = entries.map((e, i) => ({
    id: e.id, at: e.at, body: e.body, from: e.peer, to: e.peer, _by: e.by,
    kind: e.dir === 'in' ? 'message' : 'reply', _wait0: i >= readFrom,
    relationship: e.dir === 'in'
      ? 'outside party — addressed to the whole org' : undefined,
  }))
  const inn = rows.filter((r) => r.kind === 'message')
  const out = rows.filter((r) => r.kind === 'reply')
  const markRead = () => { if (inbox?.unread) orgInboxRead(slug).catch(() => {}) }
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide" onClick={(e) => e.stopPropagation()}>
        <h3><PublicIcon fontSize="inherit" /> The org inbox</h3>
        <div className="hint">
          Outside parties — external Claude Code sessions (chatq) and other
          organizations — see this org as a single recipient. Their mail lands
          here; every top-level agent (and every audience holder below) gets a
          copy, coordinates internally, and one of them replies for the org.
        </div>
        <div className="row" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="field-label">audience holders:</span>
          {holders.length === 0 && <span className="dim">none — top-level agents only</span>}
          {holders.map((h) => (
            <span key={h} className="badge free"><HearingIcon fontSize="inherit" />{h}
              <button className="chip-x" title="revoke this inbox audience"
                onClick={() => audienceAction(slug, 'revoke', h, EXTERN)
                  .then(() => toast([`org-inbox audience for ${h} rescinded`]))
                  .catch((e) => toast([`error: ${e.message}`]))}>
                <CloseIcon fontSize="inherit" /></button>
            </span>
          ))}
          {candidates.length > 0 && <>
            <select value={grantee} onChange={(e) => setGrantee(e.target.value)}>
              <option value="">grant to…</option>
              {candidates.map((n) => <option key={n.id} value={n.id}>{n.id}</option>)}
            </select>
            <button disabled={!grantee}
              onClick={() => audienceAction(slug, 'grant', grantee, 'extern')
                .then(() => { toast([`${grantee} now reads and answers the org inbox`]); setGrantee('') })
                .catch((e) => toast([`error: ${e.message}`]))}>grant</button>
          </>}
        </div>
        {/* the SAME webmail interface as every other inbox (user ruling) —
            folders + list + reading pane; only the deviations above (audience
            granting) and the outbound sender attribution differ */}
        <div className="mailwrap">
          <MailFolders folder={folder} setFolder={setFolder}
            unread={inn.filter((r) => r._wait0).length} />
          <div className="mailpane">
            {folder === 'inbox'
              ? <MailList pending={inn.filter((r) => r._wait0)}
                  delivered={inn.filter((r) => !r._wait0)}
                  waitLabel="unread" onRead={markRead} />
              : <MailList delivered={out} outgoing
                  sender={(id, m) => (
                    /* outbound attribution (user spec): @agent as @org → @recipient */
                    <span><b>{m?._by ? `@${m._by}` : '@?'}</b>
                      <span className="dim"> as </span><b>@{slug}</b>
                      <span className="dim"> → </span><b>{id}</b></span>
                  )} />}
          </div>
        </div>
        <div className="row"><span className="spacer" />
          <button onClick={close}>close</button></div>
      </div>
    </div>
  )
}

function LineagePanel({ node, op, close }) {
  // spitshined (user request): generation cards in the app's current visual
  // language — tier token, per-generation consult-tier picker (№16: a bearer
  // answers from context, so any tier serves), live bearers marked green
  useEsc(close)
  const [tiers, setTiers] = useState({})       // per-generation tier override
  const SEAT = { haiku: 1, sonnet: 3, opus: 5, fable: 10 }
  const gens = [...(node.lineage ?? [])].sort(
    (a, b) => (b.generation ?? 0) - (a.generation ?? 0))
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings lineage-panel" onClick={(e) => e.stopPropagation()}>
        <h3><LayersIcon fontSize="inherit" /> {node.id} — lineage</h3>
        <div className="dim lin-blurb">
          Every generation is this agent's pre-compaction self, archived in
          place with its full context. Rehire one as a consultable knowledge
          bearer — it answers questions beside its successor; any tier works,
          cheaper tiers consult for fewer credits.
        </div>
        {gens.map((b) => (
          <div key={b.id}
            className={'lin-row' + (b.state === 'archived' ? '' : ' live')}>
            <span className={'tier t-' + b.tier}>{TIER_LETTER[b.tier] ?? '?'}</span>
            <div className="lin-id">
              <b className="mono">{b.id}</b>
              <span className="dim">
                generation {b.generation}
                {b.bearer_state ? ` · ${b.bearer_state} bearer` : ''}
              </span>
            </div>
            {b.state === 'archived' ? (
              <>
                <select value={tiers[b.id] ?? ''} onChange={(e) =>
                  setTiers((t) => ({ ...t, [b.id]: e.target.value }))}>
                  <option value="">as {b.tier} · seat {SEAT[b.tier]}</option>
                  {['haiku', 'sonnet', 'opus'].filter((t) => t !== b.tier)
                    .map((t) => (
                      <option key={t} value={t}>as {t} · seat {SEAT[t]}</option>
                    ))}
                </select>
                <button className="primary" onClick={() =>
                  op({ op: 'rehire', node: b.id, grant: 0,
                       ...(tiers[b.id] ? { tier: tiers[b.id] } : {}) })
                    .then(close).catch(() => {})}>
                  <PlayIcon fontSize="inherit" /> rehire</button>
              </>
            ) : (
              <>
                <span className="badge free">consultable</span>
                <button className="danger" onClick={() =>
                  op({ op: 'retire', node: b.id }).then(close).catch(() => {})}>
                  retire · frees {SEAT[b.tier]}</button>
              </>
            )}
          </div>
        ))}
        {!gens.length &&
          <div className="dim pad">no prior generations — this agent has never compacted</div>}
        <div className="row"><button onClick={close}>close</button></div>
      </div>
    </div>
  )
}

// Incoming turns are mail envelopes (messages ARE mail); for the chat view,
// hide the machine chrome — [MAIL]/[END MAIL] markers, drive nudges — and
// render the FROM attribution as a small header instead of body text.
const stripEnvelope = (t) => (t ?? '')
  .split('\n')
  .filter((l) => !/^\[(MAIL — .*|END MAIL)\]$/.test(l.trim())
    && !l.trim().startsWith('(orgtree) '))
  .join('\n')
  .replace(/^FROM (\S+) \([^)]*\) · \S+ · \S+$/gm, '**$1**')
  .replace(/^FROM (\S+) \([^)]*\)$/gm, '**$1**')
  .trim()

function Msg({ m }) {
  if (m.role === 'system') return <div className="msg sys">{m.text}</div>
  const text = m.role === 'user' ? stripEnvelope(m.text) : m.text
  return (
    <div className={'msg ' + m.role + (m.oracle ? ' oracle' : '')}>
      {m.tools?.length > 0 && <div className="tools"><DotIcon fontSize="inherit" className="tooldot" /> {m.tools.join(' · ')}</div>}
      {text && <div className="msgtext md" dangerouslySetInnerHTML={md(text)} />}
      {m.oracle && <div className="tools"><SparkIcon fontSize="inherit" /> oracle exchange — not retained by the node</div>}
    </div>
  )
}
