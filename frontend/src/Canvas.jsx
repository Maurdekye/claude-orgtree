import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  audienceAction, BASE, compactNode, dissolveAll, getCharters, getChat,
  getHistory, getMcpServers, getNodeInbox, getScratch, interruptNode,
  orgInboxRead, reorderNode, retractMail, saveScope, saveSettings,
  sendMessage, uploadFile,
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

function layout(root, hidden = new Map()) {
  // `hidden`: piled-away retirees (and their subtrees) — they take NO layout
  // space; their positions are assigned afterwards onto their pile's front
  const pos = new Map()
  const vis = (n) => !hidden.has(n.id)
  const width = (n) => {
    const kids = n.children.filter(vis)
    return kids.length ? kids.reduce((a, c) => a + width(c), 0) : 1
  }
  const place = (n, x0, depth) => {
    let cx = x0
    const kids = n.children.filter(vis)
    kids.forEach((c) => { place(c, cx, depth + 1); cx += width(c) })
    const x = kids.length
      ? (pos.get(kids[0].id).x + pos.get(kids[kids.length - 1].id).x) / 2
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

const ago = (at) => {
  if (!at) return ''
  const s = Math.max(0, (Date.now() - Date.parse(at)) / 1000)
  return s < 90 ? `${Math.round(s)}s`
    : s < 5400 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`
}

// chat markdown: gfm + hard line breaks, sanitized (agents echo web content).
// №21: cached by text identity — every streamed token used to re-parse the
// ENTIRE visible transcript (~8 Hz × every message × every open panel)
const _mdCache = new Map()
// №16: outside code fences and inline code, a bare <Token> parses as an HTML
// tag — DOMPurify then strips it and keeps only the inner text, so
// `Sync<float3>` silently became `Sync` and changed the sentence's meaning.
// Escape `<` in plain prose; fenced/inline code is already safe.
const escapeAngles = (src) => {
  const parts = src.split(/(```[\s\S]*?```|`[^`\n]*`)/)
  for (let i = 0; i < parts.length; i += 2) {
    parts[i] = parts[i].replace(/</g, '&lt;')
  }
  return parts.join('')
}
const md = (text) => {
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
  const [trayQ, setTrayQ] = useState('')            // №26: tray name filter
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
  // RETIRED PILE (user spec): archived siblings in a cohort stack into ONE
  // pile so long-running orgs don't fill the canvas with retirees. The FRONT
  // retiree is the interactable card (zoom/desk/inbox/rehire); clicking the
  // visible stack margin opens a picker to bring another to the front.
  const [pileFront, setPileFront] = useState(() => {
    try { return JSON.parse(localStorage.getItem('orgtree-pile-' + slug) || '{}') } catch { return {} }
  })
  const [pileOpen, setPileOpen] = useState(null)  // parent id whose menu is open
  const setFront = useCallback((parent, nid) => {
    setPileFront((pf) => {
      const nf = { ...pf, [parent]: nid }
      localStorage.setItem('orgtree-pile-' + slug, JSON.stringify(nf))
      return nf
    })
  }, [slug])
  const piles = useMemo(() => {          // parent id → {list, front}
    const out = new Map()
    const walk = (n) => {
      const arch = (n.children ?? []).filter((c) => c.state === 'archived')
      if (arch.length >= 2) {
        const want = pileFront[n.id]
        out.set(n.id, {
          list: arch.map((c) => c.id),
          front: arch.some((c) => c.id === want) ? want : arch[arch.length - 1].id,
        })
      }
      ;(n.children ?? []).forEach(walk)
    }
    walk(vroot)
    return out
  }, [vroot, pileFront])
  const pileByFront = useMemo(() => {
    const out = new Map()
    for (const [parent, p] of piles) out.set(p.front, { parent, ...p })
    return out
  }, [piles])
  const hidden = useMemo(() => {         // piled-away id → its pile's front id
    const out = new Map()
    const bury = (n, front) => {
      out.set(n.id, front)
      ;(n.children ?? []).forEach((c) => bury(c, front))
    }
    const walk = (n) => {
      const p = piles.get(n.id)
      ;(n.children ?? []).forEach((c) => {
        if (p && c.state === 'archived' && c.id !== p.front) bury(c, p.front)
        else walk(c)
      })
    }
    walk(vroot)
    return out
  }, [vroot, piles])
  const target = useMemo(() => {
    const t = layout(vroot, hidden)
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
    // piled-away retirees sit exactly under their pile's front card (they
    // aren't rendered, but wires/sparks/centerOn need a sane position)
    for (const [hid, front] of hidden) {
      const fp = t.get(front)
      if (fp) t.set(hid, { x: fp.x, y: fp.y })
    }
    return t
  }, [vroot, map, tree.org_inbox?.visible, hidden])
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
  const animBusyRef = useRef(false)  // a camera animation owns the view
  const focusRef = useRef(null)      // №25: the desk the camera rides with
  const followRef = useRef(null)
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
      // №25: the camera rides the focused desk. A hire ANYWHERE re-anchors
      // the whole layout (eye at x=6000), which used to slide the desk you
      // were typing into ~1000 screen px out of the window — follow the
      // focused node's per-frame spring delta instead. Camera animations and
      // manual pans own the view; the follow yields to them.
      const fid = focusRef.current
      if (fid && !animBusyRef.current && !panRef.current) {
        const s = springs.current.get(fid)
        if (s) {
          const prev = followRef.current
          if (prev && prev.id === fid) {
            const dx = s.x - prev.x, dy = s.y - prev.y
            if (dx || dy) {
              const z = viewRef.current.z
              setView((v) => ({ ...v, x: v.x - dx * z, y: v.y - dy * z }))
            }
          }
          followRef.current = { id: fid, x: s.x, y: s.y }
        }
      } else {
        followRef.current = null
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
    animBusyRef.current = true
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
      else animBusyRef.current = false
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
    // extra top margin: the eye's infinite bar fades 110px above its card.
    // №23: NO zero-clamp — past ~64 leaf columns the leftmost nodes go
    // negative (the eye is pinned at x=6000) and a clamp silently cut them
    // out of "fit all"
    minX -= 60; minY -= 130
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
  // interruptible at any moment. Keyed on the LOADED org's slug — switching
  // orgs keeps this component mounted, so a mount-only intro left the camera
  // wherever the previous org parked it and the drift ran from way off-tree.
  useEffect(() => {
    const vp = viewportRef.current?.getBoundingClientRect()
    const eye = targetRef.current.get(USER)
    if (!vp || !eye) { fitAll(false); return }
    const z0 = 1.6                       // close, but under the desk threshold
    const v0 = {
      x: vp.width / 2 - (eye.x + USER_W / 2) * z0,
      y: vp.height / 2 - (eye.y + USER_H / 2) * z0,
      z: z0,
    }
    // write the ref FIRST: the rAF'd fitAll reads viewRef for its start frame,
    // and if it fires before React commits setView the drift would launch
    // from the stale camera instead of the eye
    viewRef.current = v0
    setView(v0)
    const raf = requestAnimationFrame(() => fitAll(true, 1700))
    return () => cancelAnimationFrame(raf)
  }, [tree.slug])   // eslint-disable-line react-hooks/exhaustive-deps

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
      animBusyRef.current = false
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
    animBusyRef.current = false
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
    // №23: edge-pan — in a wide org, drag source and target don't fit one
    // screen and the drag used to dead-stop at the window edge. Nearing an
    // edge pans the camera; the pan shifts the world under the cursor, so
    // it also counts into the node's drag delta (sx/sy compensate).
    const vp = viewportRef.current?.getBoundingClientRect()
    if (vp) {
      const M = 48, SPEED = 14
      let px = 0, py = 0
      if (e.clientX < vp.left + M) px = SPEED
      else if (e.clientX > vp.right - M) px = -SPEED
      if (e.clientY < vp.top + M) py = SPEED
      else if (e.clientY > vp.bottom - M) py = -SPEED
      if (px || py) {
        setView((v) => ({ ...v, x: v.x + px, y: v.y + py }))
        d.sx += px; d.sy += py
      }
    }
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
      // №17: a re-parent is one mis-drag away — the toast carries the reverse
      op(body).then(() => toast(
        [`${id} now reports to ${drop === USER ? 'you' : drop}`],
        () => op({ op: 'move', node: id, new_parent: parent ?? null })
          .catch(() => {})))
        .catch(() => {}).finally(finish)
      return
    }
    // no (new) target → cosmetic reorder among the current cohort by dropped x
    const cohort = (mapRef.current.get(parent)?.children ?? [])
      .map((c) => c.id).filter((k) => k !== DRAFT)
    const sibs = cohort.filter((k) => k !== id)
    if (!sibs.length) { finish(); return }
    const oldIdx = cohort.indexOf(id)
    const oldReq = oldIdx > 0 ? { after: cohort[oldIdx - 1] }
      : { before: cohort[1] }
    const x = springs.current.get(id)?.x ?? 0
    const beforeSib = sibs.find((k) => (targetRef.current.get(k)?.x ?? 0) > x)
    const req = beforeSib ? { before: beforeSib } : { after: sibs[sibs.length - 1] }
    // №17: a successful accidental reorder used to be completely silent
    reorderNode(slug, id, req)
      .then(() => toast([`${id} reordered`],
        () => reorderNode(slug, id, oldReq).catch(() => {})))
      .catch((err) => toast([`error: ${err.message}`])).finally(finish)
  }

  // ------------------------------------------------------- focus (the desk)
  const focusId = useMemo(() => {
    if (view.z < Z_DESK) return null
    const vp = viewportRef.current?.getBoundingClientRect()
    const cw = vp ? vp.width / 2 : 500, ch = vp ? vp.height / 2 : 350
    let best = null, bestD = Infinity
    for (const [id] of target) {
      if (id === DRAFT) continue         // the EYE can be a desk too (switchboard)
      if (hidden.has(id)) continue       // piled-away retirees never take focus
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
  }, [view, target, hidden])
  focusRef.current = focusId

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
        // thinking effort (user-approved): a deep-config setting, applied
        // right after the hire lands
        if (r?.node && scope?.effort) {
          saveScope(slug, r.node, { effort: scope.effort }).catch(() => {})
        }
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
          {[...map.values()].filter((n) => n.parent && !n.isBearerOf
            && !hidden.has(n.id)).map((n) => {
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
              streamEvt={streamEvt} compactAt={tree.compact_at}
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
          if (hidden.has(n.id)) return null   // piled-away: no card, no space
          const pileHere = pileByFront.get(n.id)
          const square = (
            <NodeSquare key={n.id} node={n} pos={p} lod={lod} focused={n.id === focusId}
              dragging={nodeDrag.current?.id === n.id && nodeDrag.current.moved}
              isDrop={dropId === n.id}
              seats={seats} map={map} op={op} slug={slug} pulse={pulse} toast={toast}
              streamEvt={streamEvt} pxc={pxPerCredit} zoom={view.z} act={activity?.[n.id]}
              onSpawn={(t) => spawn(n.id, t)} onConfig={() => setConfigId(n.id)}
              onInbox={() => setInboxId(n.id)} onLineage={() => setLineageId(n.id)}
              onRecenter={() => centerOn(n.id)}   /* recenter AND re-zoom to fill */
              pub={!!tree.public} kioskRemaining={kioskRemaining}
              cascadeAlloc={tree.cascade_alloc !== false}
              maxTop={tree.max_top_grant ?? 1000}
              pile={pileHere} compactAt={tree.compact_at}
              onDragStart={startNodeDrag} onDragMove={moveNodeDrag} onDragEnd={endNodeDrag} />
          )
          if (!pileHere) return square
          // the RETIRED PILE's stack layers render BEHIND the front card as
          // real elements (box-shadows can't be clicked): the exposed margin
          // is the hit target that opens the picker, and the whole stack
          // eases outward slightly on hover (user spec)
          const layers = Math.min(pileHere.list.length - 1, 3)
          return (
            <span key={n.id}>
              <div className="pile-stack"
                style={{ transform: `translate(${p.x}px, ${p.y}px)`,
                         width: NODE_W, height: NODE_H }}>
                {Array.from({ length: layers }, (_, i) => layers - i).map((i) => (
                  <button key={i} className={'pile-layer l' + i}
                    title={`${pileHere.list.length} retired here — click to choose who's in front`}
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => { e.stopPropagation(); setPileOpen(pileHere.parent) }} />
                ))}
                <button className="pile-count"
                  title={`${pileHere.list.length} retired here — click to choose who's in front`}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => { e.stopPropagation(); setPileOpen(pileHere.parent) }}>
                  {pileHere.list.length}</button>
              </div>
              {square}
            </span>
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
            <input className="mail-filter tray-filter" placeholder="filter agents…"
              value={trayQ} onChange={(e) => setTrayQ(e.target.value)} />
            {[...map.values()]
              .filter((n) => n.id !== USER && n.id !== DRAFT && !n.isBearerOf)
              .filter((n) => !trayQ.trim()
                || n.id.toLowerCase().includes(trayQ.trim().toLowerCase()))
              .sort((a, b) => {
                const pa = posOf(a.id) ?? { x: 0, y: 0 }
                const pb = posOf(b.id) ?? { x: 0, y: 0 }
                return pa.y - pb.y || pa.x - pb.x
              })
              .map((n) => {
                // a piled-away retiree comes to the FRONT of its pile when
                // picked from the tray, then the glide lands on it
                const go = () => {
                  if (hidden.has(n.id)) setFront(map.get(n.id)?.parent, n.id)
                  centerOn(n.id)
                }
                // №13: the status summary is TEXT here, not a tooltip — and a
                // finished status survives the next turn as prev_status (dim)
                const stat = n.last_status
                  ?? (n.prev_status ? { ...n.prev_status, _stale: true } : null)
                return (
                <div key={n.id} role="button" tabIndex={0}
                  className={'tray-row' + (n.state !== 'live' ? ' off' : '')}
                  onClick={go}
                  onKeyDown={(e) => { if (e.key === 'Enter') go() }}>
                  <div className="tray-main">
                    <span className={'tier t-' + n.tier}>{TIER_LETTER[n.tier] ?? '?'}</span>
                    <span className="tray-name"
                      title={(n.charter || '').split('\n')[0] || n.id}>{n.id}</span>
                    <ContextWheel occ={n.occupancy} cw={n.context_window}
                      compactAt={tree.compact_at} />
                    {n.busy ? (n.waiting
                      ? <span className="statusdot waiting"
                          title="queued — waiting for a free turn slot (№12)" />
                      : <span title={n.inflight_at
                          ? `running for ${ago(n.inflight_at)}` : 'working'}>
                          <Activity act={activity?.[n.id]} dotOnly />
                        </span>)
                      : n.frozen
                        ? <FrozenIcon fontSize="inherit" className="tray-frozen" />
                        : n.state !== 'live'
                          ? <span className="dim">{n.state}</span>
                          : n.last_status
                            ? <span className={'statusdot ' + n.last_status.status}
                                title={n.last_status.summary} />
                            : <span className="statusdot idle" title="idle" />}
                  </div>
                  {stat?.summary && (
                    <div className={'tray-sum' + (stat._stale ? ' stale' : '')}>
                      {stat.status}: {stat.summary.slice(0, 70)}
                      {stat.at ? ` · ${ago(stat.at)} ago` : ''}
                    </div>
                  )}
                </div>
                )
              })}
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
        <LineagePanel node={map.get(lineageId)} op={op} slug={slug}
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
      {pileOpen && piles.get(pileOpen) && (
        <PilePicker pile={piles.get(pileOpen)} map={map}
          onPick={(nid) => { setFront(pileOpen, nid); setPileOpen(null) }}
          close={() => setPileOpen(null)} />
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
  streamEvt, compactAt }) {
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
          (infinite pool) but read as wrong next to every other card. The
          chips survive switchboard focus too (user spec) — hiring is never
          out of reach. */}
      <SpawnChips onSpawn={onSpawn} free={kioskRemaining ?? Infinity} seats={seats} />
      {focused && (
        <EyeDesk map={map} op={op} slug={slug} pulse={pulse} toast={toast}
          streamEvt={streamEvt} inboxCount={inboxCount} onInbox={onInbox}
          onGear={onGear} pub={pub} eyeW={eyeW} posX={posX} onJump={onJump}
          compactAt={compactAt} />
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
  onInbox, onGear, pub, eyeW, posX, onJump, compactAt }) {
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
  // №24: a NEW direct line arrives MINIMIZED with its tab lit — the shipped
  // coordinator charter grants an audience after every hire, and each grant
  // used to shove another full-width panel into the row automatically
  const seenIds = useRef(null)
  const idsKey = agents.map((a) => a.id).join(',')
  useEffect(() => {
    const ids = new Set(idsKey ? idsKey.split(',') : [])
    if (seenIds.current) {
      const fresh = [...ids].filter((id) => !seenIds.current.has(id))
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
  }, [idsKey, slug])
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
                toast={toast} pub={pub} bare compact compactAt={compactAt}
                streamEvt={streamEvt?.node === a.id ? streamEvt : null} />
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
            title={cant ? `${t}: needs ${seat} free (has ${free})`
              : `hire ${/^[aeiou]/.test(t) ? 'an' : 'a'} ${t} (seat ${seat})`}
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
  const [sandboxMcp, setSandboxMcp] = useState(false)
  const [vis, setVis] = useState(tree.default_visibility ?? 'full')
  // the org's folder holdings (workspace excluded — it is permanent RW).
  // These double as the folder defaults for every hire.
  const [orgDirs, setOrgDirs] = useState(
    (tree.dirs ?? []).filter((d) => d.path !== tree.workspace).map((d) => ({ ...d })))
  const [newPath, setNewPath] = useState('')
  useEffect(() => {
    getMcpServers().then((r) => {
      setServers(r.servers ?? []); setSandboxMcp(!!r.sandbox_mcp)
    }).catch(() => {})
  }, [])
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
        {!allMcp && <McpChecklist servers={servers} sandboxMcp={sandboxMcp}
          sandboxed={!!tree.sandboxed}
          checked={(s) => defTools.mcp.includes(s)}
          onToggle={(s, on) => setDefTools({
            ...defTools,
            mcp: on ? [...defTools.mcp, s] : defTools.mcp.filter((x) => x !== s),
          })} />}
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
            <input className="df-name" autoFocus placeholder="name…" value={name}
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
  const [effort, setEffort] = useState(base.effort ?? '')
  const [newPath, setNewPath] = useState('')
  const [servers, setServers] = useState([])
  const [sandboxMcp, setSandboxMcp] = useState(false)
  useEffect(() => {
    getMcpServers().then((r) => {
      setServers(r.servers ?? []); setSandboxMcp(!!r.sandbox_mcp)
    }).catch(() => {})
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
        {!allMcp && <McpChecklist servers={servers} sandboxMcp={sandboxMcp}
          sandboxed={!!tree.sandboxed}
          checked={(s) => tools.mcp.includes(s)}
          onToggle={(s, on) => setTools({
            ...tools,
            mcp: on ? [...tools.mcp, s] : tools.mcp.filter((x) => x !== s),
          })} />}
        <div className="field-label">org-structure visibility</div>
        <select value={vis} onChange={(e) => setVis(e.target.value)}>
          {VIS_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <div className="field-label">thinking effort</div>
        <select value={effort} onChange={(e) => setEffort(e.target.value)}>
          <option value="">CLI default</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="xhigh">xhigh</option>
          <option value="max">max</option>
        </select>
        <div className="hint">
          Grants clamp to what the parent holds (№30) — anything beyond its
          capability is trimmed at hire with a warning.
        </div>
        <div className="row">
          <button className="primary" onClick={() =>
            onSave({ add_dirs: dirs, tools, org_visibility: vis,
              ...(effort ? { effort } : {}) })}>apply</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>,
    document.body
  )
}

function NodeSquare({ node, pos, lod, focused, dragging, isDrop, seats, map, op, slug,
  pulse, toast, streamEvt, pxc, zoom, act, onSpawn, onConfig, onInbox, onLineage,
  onRecenter, pub, kioskRemaining, cascadeAlloc, maxTop, pile, compactAt,
  onDragStart, onDragMove, onDragEnd }) {
  // pile fronts zoom on a plain CENTER click (user spec) — track the
  // pointer-down point so a drag's trailing click doesn't re-zoom
  const downAt = useRef(null)
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
      onPointerDown={(e) => {
        downAt.current = { x: e.clientX, y: e.clientY }
        if (!focused) onDragStart(e, node.id)
      }}
      onPointerMove={(e) => onDragMove(e, node.id)}
      onPointerUp={(e) => onDragEnd(e, node.id, node, focused)}
      onPointerCancel={(e) => onDragEnd(e, node.id, node, focused)}
      onClick={(e) => {
        // pile front: center click = zoom onto the focused retiree (user
        // spec); margin clicks (the stack) are handled by the layers behind
        if (!pile || focused) return
        if (e.target.closest('button, input, textarea, select')) return
        const d = downAt.current
        if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) > 5) return
        onRecenter?.()
      }}>
      {live && !node.isBearerOf && (
        <CreditBar seat={node.seat} grant={node.grant} committed={node.grant - node.free}
          segments={node.children.filter((c) => c.state !== 'archived')
            .map((c) => ({ seat: c.seat, grant: c.grant }))}   /* unrecoverable still holds */
          min={node.grant - node.free}
          /* reallocate cascades up the chain (§4.6), so the parent's free is
             not a ceiling — unless the cascade_alloc setting turns that off.
             Otherwise the org's global grant cap (or a kiosk's hard credit
             cap) bounds the drag. */
          max={kioskRemaining != null
            ? node.grant + kioskRemaining
            : cascadeAlloc === false && node.parent !== USER
              ? node.grant + (map.get(node.parent)?.free ?? 0)
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
        <ContextWheel occ={node.occupancy} cw={node.context_window}
          compactAt={compactAt} />
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
          streamEvt={streamEvt?.node === node.id ? streamEvt : null}
          onLineage={onLineage} onConfig={onConfig} compactAt={compactAt}
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
// MCP server checklist shared by the org / per-agent / pre-hire scope panels.
// In a SANDBOXED org, ALL servers grey out (user ruling): they are points of
// external contact the sandbox is explicitly designed to restrict. The
// experimental ORGTREE_SANDBOX_MCP env var re-enables them (url + portable
// stdio passthrough, no full support).
function McpChecklist({ servers, sandboxed, sandboxMcp, checked, onToggle }) {
  const dead = sandboxed && !sandboxMcp
  return (
    <>
      {dead && (
        <div className="hint">
          sandboxed org — MCP servers are external contact points the sandbox
          restricts, so none reach its agents (the ORGTREE_SANDBOX_MCP env var
          enables URL/portable servers experimentally)
        </div>
      )}
      {servers.map((s) => (
        <label className={'checkline' + (dead ? ' dead' : '')} key={s}
          title={dead ? 'unavailable in a sandboxed org' : undefined}>
          <input type="checkbox" disabled={dead} checked={checked(s)}
            onChange={(e) => onToggle(s, e.target.checked)} />
          <span className="mono">{s}</span>
        </label>
      ))}
    </>
  )
}

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
  const [effort, setEffort] = useState(node.scope.effort ?? '')
  const [newPath, setNewPath] = useState('')
  const [servers, setServers] = useState([])
  const [sandboxMcp, setSandboxMcp] = useState(false)
  const [initInfo, setInitInfo] = useState(null)   // №14: the CLI's own resolution
  useEffect(() => {
    getMcpServers().then((r) => {
      setServers(r.servers ?? []); setSandboxMcp(!!r.sandbox_mcp)
    }).catch(() => {})
    getChat(slug, node.id, 1).then((c) => setInitInfo(c.init ?? null))
      .catch(() => {})
  }, [slug, node.id])
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
      <div className="settings cfg" onClick={(e) => e.stopPropagation()}>
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
        {!!tree.sandboxed && !sandboxMcp && servers.length > 0 && (
          <div className="hint">
            sandboxed org — MCP servers are external contact points the sandbox
            restricts, so none reach its agents (the ORGTREE_SANDBOX_MCP env
            var enables URL/portable servers experimentally)
          </div>
        )}
        {servers.map((s) => {
          const dead = !!tree.sandboxed && !sandboxMcp
          return (
            <label className={'checkline' + (dead ? ' dead' : '')} key={s}
              title={dead ? 'unavailable in a sandboxed org' : undefined}>
              <input type="checkbox"
                checked={(holdsAllMcp || tools.mcp.includes(s)) && parentHoldsMcp(s)}
                disabled={!parentHoldsMcp(s) || dead}
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
          )
        })}

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

        <div className="field-label">thinking effort (user-approved: a deep
          setting, never a hire-row control)</div>
        <select value={effort} onChange={(e) => setEffort(e.target.value)}>
          <option value="">CLI default</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="xhigh">xhigh</option>
          <option value="max">max</option>
        </select>

        <div className="field-label">charter</div>
        <textarea rows={10} className="charterbox" value={charter}
          onChange={(e) => setCharter(e.target.value)} />
        <div className="field-label">team charter</div>
        <textarea rows={10} className="charterbox" value={teamCharter}
          onChange={(e) => setTeamCharter(e.target.value)} />
        {initInfo && (
          <>
            <div className="field-label">this turn, as the CLI resolved it (№14)</div>
            <div className="initblock dim">
              <div>model {initInfo.model ?? '?'} · {initInfo.permissionMode ?? '?'}
                {' · '}{initInfo.tools ?? '?'} tools</div>
              {(initInfo.mcp_servers ?? []).map((s) => (
                <div key={s.name}>
                  <span className={'mcpdot ' + (s.status === 'connected'
                    ? 'ok' : 'bad')} /> {s.name} · {s.status}
                </div>))}
            </div>
          </>
        )}
        <div className="row">
          <button className="primary" onClick={() =>
            (model !== node.tier
              ? op({ op: 'switch_model', node: node.id, tier: model })
              : Promise.resolve())
              .then(() => saveScope(slug, node.id,
                { add_dirs: dirs, tools, org_visibility: vis,
                  charter, team_charter: teamCharter, effort }))
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

function ContextWheel({ occ, cw, onCompact, compactAt }) {
  if (!occ || !cw) return null
  const frac = Math.min(1, occ / cw)
  // №19: the red ring means "about to split" — the ORG'S configured
  // threshold, not a literal 0.8 (an org set to 50% got a ring that turned
  // red 30 points after its agents had already forked)
  const hot = frac >= (compactAt || 0.8)
  const R = 5.5, C = 2 * Math.PI * R
  const svg = (
    <svg className="ctxwheel" viewBox="0 0 16 16" width="15" height="15">
      <title>{`context: ${Math.round(occ / 1000)}k / ${Math.round(cw / 1000)}k (${Math.round(frac * 100)}%)`
        + ` — auto-compacts at ${Math.round((compactAt || 0.8) * 100)}%`
        + (onCompact ? ' — click to compact now' : '')}</title>
      <circle cx="8" cy="8" r={R} className="track" />
      <circle cx="8" cy="8" r={R} className={'fill' + (hot ? ' hot' : '')}
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
// №21: memoized — the spring engine re-renders the whole canvas every
// animation frame, and each open desk re-parsed its full transcript each
// time. The comparator checks the DATA props only; the callback props close
// over stable setters, so their per-render identities are ignorable.
const DeskChat = memo(DeskChatInner, (p, n) =>
  p.node === n.node && p.map === n.map && p.slug === n.slug
  && p.pulse === n.pulse && p.streamEvt === n.streamEvt
  && p.pub === n.pub && p.bare === n.bare && p.compact === n.compact
  && p.compactAt === n.compactAt)

function DeskChatInner({ node, map, op, slug, pulse, toast, streamEvt, onLineage, onConfig,
  onRecenter, pub, bare = false, compact = false, compactAt }) {
  const [chat, setChat] = useState(null)
  // №2: the draft survives the camera — persisted per node on every keystroke
  // (clicking a sibling card unmounts this whole component)
  const draftKey = `orgtree-draft-${slug}-${node.id}`
  const [text, setTextRaw] = useState(() => {
    try { return localStorage.getItem(draftKey) || '' } catch { return '' }
  })
  const setText = useCallback((v) => setTextRaw((prev) => {
    const next = typeof v === 'function' ? v(prev) : v
    try {
      if (next) localStorage.setItem(draftKey, next)
      else localStorage.removeItem(draftKey)
    } catch { /* private mode */ }
    return next
  }), [draftKey])
  const [pending, setPending] = useState([])   // optimistic, until the server copy lands
  const [sendMode, setSendMode] = useState('') // №11: which door the send went through
  const [asking, setAsking] = useState(false)
  const [askCompact, setAskCompact] = useState(false)
  const [view, setView] = useState('chat')     // chat | history | files | inbox
  const [live_feed, setLiveFeed] = useState([])
  const [draft, setDraft] = useState('')       // the token-streamed growing reply
  const [thinking, setThinking] = useState('') // №18: live-only, never persisted
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
      if (pulse.event === 'turn_done') {
        setLiveFeed([]); setDraft(''); setThinking('')
      }
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
        setThinking('')
        if (stick) toBottom()
        return
      }
      if (streamEvt.kind === 'thinking') {
        // №18: live-only ribbon — persisting thinking would be a second
        // transcript the CLI does not keep
        setThinking((t) => (t + streamEvt.text).slice(-2000))
        if (stick) toBottom()
        return
      }
      if (streamEvt.kind === 'steered') {
        // a pending user message just got DELIVERED mid-task
        setPending((p) => p.filter((x) => !streamEvt.text.includes(x)))
      }
      if (streamEvt.kind === 'text') { setDraft(''); setThinking('') }
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
    // optimistic ghost only until the server confirms — the durable copy
    // then renders from chat.pending_mail (№11); a failed send clears the
    // ghost instead of leaving a dimmed bubble forever
    setPending((p) => [...p, t])
    if (live) setChat((c) => c && ({ ...c, busy: true }))
    toBottom()
    sendMessage(slug, node.id, t)
      .then((r) => {
        setSendMode(r.command ? 'command sent'
          : r.steering ? 'steering in mid-task'
            : r.deferred ? 'deferred — delivers at rehire'
              : r.queued > 0 ? `queued (${r.queued} ahead)` : 'delivering')
        return refresh(true)
      })
      .then(() => setPending((p) => p.filter((x) => x !== t)))
      .catch((e) => {
        setPending((p) => p.filter((x) => x !== t))
        toast([`error: ${e.message}`])
      })
  }

  // file uploads (user spec 2026-07-31): the file lands in the agent's own
  // uploads/ scratch folder — same relative path sandboxed or not, and it
  // works through the public kiosk gateway from the outside internet
  const fileRef = useRef(null)
  const attach = (file) => {
    uploadFile(slug, node.id, file)
      .then((r) => setText((t) => (t ? t + '\n' : '')
        + `[file attached: ${r.path} — in your working folder]`))
      .catch((e) => toast([`upload error: ${e.message}`]))
  }
  // №13: the composer grows with the draft (2 → ~8 rows); the desk interior
  // is a fixed 900px virtual panel, so .msgs absorbs the difference
  const taRef = useRef(null)
  const grow = () => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }
  // №6: dropping a file anywhere on the desk uploads it (and prevents the
  // browser's default navigate-away, which would also eat the draft)
  const dropProps = {
    onDragOver: (e) => e.preventDefault(),
    onDrop: (e) => {
      e.preventDefault()
      if (e.dataTransfer?.files?.length) {
        [...e.dataTransfer.files].forEach(attach)
      }
    },
  }

  const liveKids = node.children.some((c) => c.state === 'live')
  const content = (
    <>
      <div className="cc-head">
        <span className={'tier t-' + node.tier}>{TIER_LETTER[node.tier] ?? '?'}</span>
        <span className="cc-name"
          title={(node.charter || '').split('\n')[0] || node.id}>{node.id}</span>
        <ContextWheel occ={chat?.occupancy ?? node.occupancy} cw={node.context_window}
          compactAt={compactAt}
          onCompact={live && !node.bearer_state
            ? () => setAskCompact(true) : undefined} />
        {node.last_status &&
          <span className={'statuschip ' + node.last_status.status}
            title={node.last_status.summary}>{node.last_status.status}</span>}
        {/* №3: the word is the STATE, not a blanket "working" — compacting,
            queued behind the slot cap, and actually responding are different
            things and the backend already splits them */}
        {(node.busy || node.phase === 'compacting' || chat?.busy) &&
          <span className="cc-working">
            {node.phase === 'compacting' ? <>compacting…</>
              : node.waiting ? <>queued for a turn slot…</>
                : <><AutorenewIcon fontSize="inherit" className="cc-spin" /> working
                  {node.inflight_at ? <span className="dim"> · {ago(node.inflight_at)}</span> : null}</>}
          </span>}
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
        {!compact && node.cost_usd > 0 && (
          <span className="badge dim"
            title={(node.turns ?? []).slice(-5).reverse().map((t) =>
              `${t.at?.slice(5, 16).replace('T', ' ')} · $${(t.cost ?? 0).toFixed(2)}`
              + (t.ms ? ` · ${Math.round(t.ms / 1000)}s` : '')
              + (t.denials ? ` · ${t.denials} denied` : '')).join('\n')
              || 'per-turn detail appears after the next turn'}>
            ${node.cost_usd.toFixed(2)}</span>)}
        {chat?.queued > 0 && <span className="badge">{chat.queued} queued</span>}
        <span className="spacer" />
        {/* compact (switchboard panel): chat only — the agent's own desk keeps
            the full chrome (actions, tabs, gear) */}
        {!compact && <span className="cc-actions">
          {live && !liveKids &&
            <button className="danger"
              onClick={() => op({ op: 'retire', node: node.id }).then(() =>
                toast([`${node.id} retired`],
                  () => op({ op: 'rehire', node: node.id }).catch(() => {})))
                .catch(() => {})}>
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
        <div className="msgs" ref={scroller}>
          {!chat && <div className="dim pad">loading…</div>}
          {chat && !chat.messages.length && !live_feed.length &&
            <div className="dim pad">no conversation yet</div>}
          {chat?.messages.map((m, i) => {
            // №15: one dim divider per idle gap — never per-message timestamps
            const prev = chat.messages[i - 1]
            const gapMs = prev?.ts && m.ts
              ? Date.parse(m.ts) - Date.parse(prev.ts) : 0
            return (
              <div key={i}>
                {gapMs > 5 * 60e3 && (
                  <div className="msg sys">— {gapMs > 5400e3
                    ? `${Math.round(gapMs / 3600e3)} h`
                    : `${Math.round(gapMs / 60e3)} min`} later —</div>)}
                <Msg m={m} slug={slug} nid={node.id} />
              </div>
            )
          })}
          {live_feed.map((f, i) => (
            f.kind === 'tool'
              ? <div key={'f' + i} className="msg live tools"><DotIcon fontSize="inherit" className="tooldot" /> {f.text}</div>
              : f.kind === 'steered'
                ? <div key={'f' + i} className="msg user live md"
                    dangerouslySetInnerHTML={md(stripEnvelope(f.text))} />
                : <div key={'f' + i} className="msg assistant live md"
                    dangerouslySetInnerHTML={md(f.text)} />
          ))}
          {thinking && chat?.busy && (
            <div className="msg live thinking">{thinking}</div>)}
          {draft && <div className="msg assistant live md draft"
            dangerouslySetInnerHTML={md(draft)} />}
          {/* №11: pending bubbles render from the DURABLE server copy, each
              retractable until delivery (№17) */}
          {(chat?.pending_mail ?? []).filter((m) => m.from === USER).map((m) => (
            <div key={m.id ?? m.at} className="msg user pending pendrow">
              <span className="md" dangerouslySetInnerHTML={md(m.body)} />
              {m.id && (
                <button className="chip-x" title="retract (undelivered)"
                  onClick={() => retractMail(slug, node.id, m.id)
                    .then(() => refresh(true))
                    .catch((e) => toast([`error: ${e.message}`]))}>
                  <CloseIcon fontSize="inherit" /></button>)}
            </div>
          ))}
          {pending.map((p, i) => (
            <div key={'q' + i} className="msg user pending md"
              dangerouslySetInnerHTML={md(p)} />
          ))}
          {/* №7: the machine truth about headless auto-denies — the agent
              was corrected and nothing showed it */}
          {node.last_denials?.length > 0 && (
            <div className="denials">
              {node.last_denials.map((d, i) => (
                <div key={i}>⊘ denied · {d.tool}{d.arg ? `(${d.arg})` : ''}</div>))}
              <span className="dim">last turn — the ⚙ folder grants decide this</span>
            </div>)}
        </div>
      )}
      {view === 'history' && <HistoryView slug={slug} nid={node.id} />}
      {view === 'files' && <FilesView slug={slug} nid={node.id} />}
      {view === 'inbox' && <InboxView slug={slug} nid={node.id} pulse={pulse}
        onRetract={(m) => retractMail(slug, node.id, m.id)
          .then(() => refresh(true))
          .catch((e) => toast([`error: ${e.message}`]))} />}
      {/* №13: the composer is present under EVERY tab — finding a wrong number
          on the files tab shouldn't cost your place to say so */}
      {sendMode && <div className="sendmode dim">{sendMode}</div>}
      {text.trimStart().startsWith('/') && canMail && (
        <SlashHints text={text} setText={setText} />)}
      <div className={'cc-composer' + (canMail ? '' : ' off')}>
        <button className="cc-attach" disabled={!canMail}
          title="attach a file — it lands in the agent's uploads/ folder"
          onClick={() => fileRef.current?.click()}>
          <FileIcon fontSize="inherit" /></button>
        <input type="file" ref={fileRef} style={{ display: 'none' }} multiple
          onChange={(e) => {
            [...e.target.files].forEach(attach)
            e.target.value = ''
          }} />
        <textarea rows={2} value={text} disabled={!canMail} ref={taRef}
          autoFocus={!bare && !compact}
          placeholder={live ? `message ${node.id}…`
            : node.state === 'archived'
              ? `message ${node.id} — queued until rehire…` : node.state}
          onChange={(e) => { setSendMode(''); setText(e.target.value); grow() }}
          onPaste={(e) => {
            // №6: Ctrl+V of an image/file auto-bridges to a real upload
            if (e.clipboardData?.files?.length) {
              e.preventDefault()
              ;[...e.clipboardData.files].forEach(attach)
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
          }} />
        {!pub && (
          <EffortSwitch value={node.scope?.effort ?? ''}
            onSet={(lvl) => saveScope(slug, node.id, { effort: lvl })
              .then(() => toast([lvl
                ? `${node.id} thinking effort: ${lvl}`
                : `${node.id} thinking effort: CLI default`]))
              .catch((e) => toast([`error: ${e.message}`]))} />
        )}
        {/* №3: STOP renders only when an interrupt can actually land —
            pressing the one red control must never error */}
        {node.responding
          ? <button className="cc-send stop" title="interrupt the current response"
              onClick={() => interruptNode(slug, node.id)
                .then((r) => { if (!r.interrupted) toast([`error: ${r.reason}`]) })
                .catch((e) => toast([`error: ${e.message}`]))}><StopIcon fontSize="inherit" /></button>
          : <button className="cc-send" disabled={!canMail || !text.trim()}
              onClick={send}><ArrowUpIcon fontSize="inherit" /></button>}
      </div>
    </>
  )
  // bare: the switchboard hosts many chats inside ONE counter-scaled surface —
  // no overlay wrapper, no second scale (that would double-scale), no
  // recenter-on-click
  if (bare) return <div className="desk-body eye-chat" {...dropProps}>{content}</div>
  return (
    <div className="desk-over" onWheel={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()} {...dropProps}
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
  onRead, onReply, onRetract }) {
  // newest first throughout (user ruling) — waiting/unread stays grouped on top
  const all = [
    ...[...pending].reverse().map((m) => ({ ...m, _wait: true })),
    ...[...delivered].reverse(),
  ]
  // selection is BY IDENTITY, not index — marking a mail read reshuffles the
  // list, and an index would silently land on a different mail
  const keyOf = (m) => m?.id ?? `${m?.at}|${m?.from}|${(m?.body ?? '').slice(0, 24)}`
  const [selId, setSelId] = useState(null)
  // №26: hunting an hour-old message decayed your unread set click by click —
  // a plain client-side filter over sender+body, no index, no server
  const [q, setQ] = useState('')
  const [draft, setDraft] = useState('')
  const S = sender ?? ((id) => <span>{id === USER ? '@user' : id}</span>)
  const partyOf = (m) => (outgoing ? m.to : m.from)
  const qn = q.trim().toLowerCase()
  const shown = qn
    ? all.filter((m) => String(partyOf(m) ?? '').toLowerCase().includes(qn)
      || String(m.body ?? '').toLowerCase().includes(qn))
    : all
  const cur = shown.find((m) => keyOf(m) === selId) ?? shown[0]
  // per-mail read (user ruling): a VIEWED unread mail is marked read the
  // moment you click OFF it — select another mail, or leave the list
  const curRef = useRef(null); curRef.current = cur
  const readRef = useRef(onRead); readRef.current = onRead
  const leave = (m) => { if (m?._wait && m.id) readRef.current?.(m) }
  useEffect(() => () => leave(curRef.current), [])
  const party = partyOf
  // a custom sender renderer owns the whole head identity (it receives the
  // mail too — the org inbox uses this for "@agent as @org → @recipient")
  const customS = outgoing && sender != null
  const brief = (b) => (b ?? '').trim().replace(/\s+/g, ' ').slice(0, 90)
  const when = (at) => (at ?? '').slice(5, 16).replace('T', ' ')
  // reply from where you read (№11): only for incoming mail whose sender is a
  // plain agent id — @-sentinels (@user/@system/@ext:/@org:/@mcp:) route
  // elsewhere, and slugify guarantees no agent name starts with '@'
  const replyable = Boolean(onReply && cur && !outgoing
    && !String(party(cur) ?? '').startsWith('@'))
  if (!all.length) return <div className="dim pad">no mail yet</div>
  return (
    <div className="mailer">
      <div className="mailer-list">
        {all.length > 4 && (
          <input className="mail-filter" placeholder="filter…" value={q}
            onChange={(e) => setQ(e.target.value)} />
        )}
        {shown.length === 0 && <div className="dim pad">no matches</div>}
        {shown.map((m, i) => (
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
              {m._wait && m.id && onRetract && (
                <button className="chip-x" title="retract (undelivered)"
                  onClick={(e) => { e.stopPropagation(); onRetract(m) }}>
                  <CloseIcon fontSize="inherit" /></button>)}
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
            {replyable && (
              <div className="mail-reply">
                <textarea rows={2} value={draft}
                  placeholder={`reply to ${party(cur)}…`}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && draft.trim()) {
                      e.preventDefault()
                      onReply(cur, draft.trim())
                      setDraft('')
                    }
                  }} />
                <button disabled={!draft.trim()}
                  onClick={() => { onReply(cur, draft.trim()); setDraft('') }}>
                  reply
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// The node's own mailbox (user ruling: its own tab, separate from history),
// with the same folders as the user's: inbox + sent.
function InboxView({ slug, nid, pulse, onRetract }) {
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
                waitLabel="awaiting next turn"
                onRetract={onRetract
                  ? (m) => { onRetract(m); setBox((b) => b && ({
                      ...b, pending: b.pending.filter((x) => x.id !== m.id) })) }
                  : undefined} />
            : <MailList delivered={box.sent ?? []} outgoing />}
      </div>
    </div>
  )
}

export function MailFolders({ folder, setFolder, unread, folders }) {
  return (
    <div className="mail-folders">
      {(folders ?? ['inbox', 'sent']).map((f) => (
        <button key={f} className={folder === f ? 'on' : ''}
          onClick={() => setFolder(f)}>
          {f}{f === 'inbox' && unread > 0 ? ` ${unread}` : ''}
        </button>
      ))}
    </div>
  )
}

// №10: the org record — every ledger operation (the overseer was the only
// node never told what changed). Renders the events log the server has kept
// all along; the §4.6 cascade warnings ride each row.
export function OrgRecord({ events }) {
  const [q, setQ] = useState('')
  const qn = q.trim().toLowerCase()
  const rows = [...(events ?? [])].reverse().filter((ev) => !qn
    || JSON.stringify(ev).toLowerCase().includes(qn))
  const when = (at) => (at ?? '').slice(5, 16).replace('T', ' ')
  const gist = (ev) => {
    const d = ev.detail || {}
    const bits = [d.node, d.from != null || d.to != null
      ? `${d.from ?? 'top'} → ${d.to ?? 'top'}` : null,
    d.freed != null ? `freed ${d.freed}` : null,
    d.grant != null ? `grant ${d.grant}` : null,
    d.reason, d.predecessor].filter(Boolean)
    return bits.join(' · ')
  }
  if (!rows.length && !qn) return <div className="dim pad">nothing yet</div>
  return (
    <div className="record">
      <input className="mail-filter" placeholder="filter…" value={q}
        onChange={(e) => setQ(e.target.value)} />
      {rows.slice(0, 200).map((ev, i) => (
        <div className="rec-row" key={i}>
          <span className="mtime">{when(ev.at)}</span>
          <b className="rec-kind">{ev.op}</b>
          <span className="rec-actor">{ev.actor === USER ? 'you' : ev.actor}</span>
          <span className="dim">{gist(ev)}</span>
          {(ev.warnings ?? []).map((w, k) => (
            <div className="rec-warn" key={k}>⚠ {w}</div>
          ))}
        </div>
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

// the RETIRED-PILE menu (user spec): pick which retiree sits in front — the
// front card is the one you zoom in on, message, read and can rehire; the
// rest wait stacked beneath it. The current front is highlighted.
function PilePicker({ pile, map, onPick, close }) {
  useEsc(close)
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings pile-picker" onClick={(e) => e.stopPropagation()}>
        <h3><LayersIcon fontSize="inherit" /> retired pile
          <span className="dim"> · {pile.list.length} agents</span></h3>
        <div className="hint">
          The retiree in front is the one you zoom in on, read, message and can
          rehire; the rest wait beneath. Pick one to bring it forward.
        </div>
        {[...pile.list].reverse().map((id) => {
          const n = map.get(id)
          if (!n) return null
          return (
            <button key={id} className={'pile-row' + (id === pile.front ? ' on' : '')}
              onClick={() => onPick(id)}>
              <span className={'tier t-' + n.tier}>{TIER_LETTER[n.tier] ?? '?'}</span>
              <span className="pile-name">{id}</span>
              {n.bearer_state && <span className="badge dim">{n.bearer_state}</span>}
              {id === pile.front && <span className="badge free">in front</span>}
            </button>
          )
        })}
      </div>
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

function LineagePanel({ node, op, slug, close }) {
  // spitshined (user request): generation cards in the app's current visual
  // language — tier token, per-generation consult-tier picker (№16: a bearer
  // answers from context, so any tier serves), live bearers marked green
  useEsc(close)
  const [tiers, setTiers] = useState({})       // per-generation tier override
  // №12: READING an archived bearer's transcript is free — rehiring is for
  // asking it questions, not for looking at what it holds
  const [reading, setReading] = useState(null)     // bearer id being read
  const [readChat, setReadChat] = useState(null)
  const openRead = (bid) => {
    if (reading === bid) { setReading(null); return }
    setReading(bid); setReadChat(null)
    getChat(slug, bid).then(setReadChat)
      .catch(() => setReadChat({ messages: [] }))
  }
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
          <div key={b.id}>
            <div className={'lin-row' + (b.state === 'archived' ? '' : ' live')}>
              <span className={'tier t-' + b.tier}>{TIER_LETTER[b.tier] ?? '?'}</span>
              <div className="lin-id">
                <b className="mono">{b.id}</b>
                <span className="dim">
                  generation {b.generation}
                  {b.bearer_state ? ` · ${b.bearer_state} bearer` : ''}
                </span>
              </div>
              {b.bearer_state !== 'lost' && (
                <button className={reading === b.id ? 'on' : ''}
                  title="read this generation's transcript — free, no seat"
                  onClick={() => openRead(b.id)}>read</button>)}
              {b.state === 'archived' && b.bearer_state !== 'lost' ? (
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
              ) : b.bearer_state === 'lost' ? (
                <span className="badge dim"
                  title="its session was lost — kept for the record, not consultable">
                  lost generation</span>
              ) : (
                <>
                  <span className="badge free">consultable</span>
                  <button className="danger" onClick={() =>
                    op({ op: 'retire', node: b.id }).then(close).catch(() => {})}>
                    retire · frees {SEAT[b.tier]}</button>
                </>
              )}
            </div>
            {reading === b.id && (
              <div className="lin-read">
                {readChat == null
                  ? <div className="dim pad">loading transcript…</div>
                  : readChat.messages.length
                    ? readChat.messages.slice(-80).map((m, i) => (
                        <Msg key={i} m={m} slug={slug} nid={b.id} />))
                    : <div className="dim pad">no transcript found</div>}
              </div>)}
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

// Parity №1/№9/№10: the tool line says what it did — argument on the chip, a
// red bit + first error line on failure, and the RESULT collapsed behind a
// click (never inline: an always-expanded stream turns the desk into a log
// tail). Edits expand to their pre-computed hunk.
function ToolChip({ t, slug, nid }) {
  const [open, setOpen] = useState(false)
  const expandable = Boolean(t.result || t.diff || t.images)
  return (
    <div className={'tools tchip' + (t.error ? ' terr' : '')}>
      <span className={'tline' + (expandable ? ' click' : '')}
        onClick={expandable ? () => setOpen((o) => !o) : undefined}
        title={expandable ? (open ? 'collapse' : 'expand') : undefined}>
        <DotIcon fontSize="inherit" className="tooldot" />
        {' '}{shortTool(t.name)}
        {t.arg ? <span className="targ"> {t.arg}</span> : null}
        {t.diff && <span className="tdiffn"> +{t.diff.plus} −{t.diff.minus}</span>}
        {!t.diff && !t.error && t.result_lines > 0 && (
          <span className="dim"> · {t.result_lines} line{t.result_lines === 1 ? '' : 's'}</span>)}
        {t.task && (
          <span className="dim"> · {t.task.tools ?? '?'} tools
            {t.task.ms ? ` · ${Math.round(t.task.ms / 1000)}s` : ''}
            {t.task.tokens ? ` · ${Math.round(t.task.tokens / 1000)}k tok` : ''}</span>)}
        {t.images > 0 && <span className="dim"> · {t.images} image{t.images === 1 ? '' : 's'}</span>}
        {t.error && <span className="terrtxt"> ⊘ {t.error}</span>}
      </span>
      {open && t.diff && (
        <pre className="filepre diffpre">
          {t.diff.lines.map((l, i) => (
            <div key={i} className={l.startsWith('+') ? 'dplus'
              : l.startsWith('-') ? 'dminus' : ''}>{l}</div>))}
        </pre>)}
      {open && !t.diff && t.result && (
        <pre className="filepre respre">
          {t.result}{t.truncated ? '\n… truncated' : ''}
        </pre>)}
      {open && t.images > 0 && t.id && Array.from({ length: t.images }).map((_, i) => (
        <img key={i} className="toolimg" alt="tool result"
          src={`${BASE}/api/orgs/${slug}/nodes/${nid}/toolimg/${t.id}?idx=${i}`} />))}
    </div>
  )
}

// №21: memoized — rows are static once fetched; only identity changes matter
const Msg = memo(function Msg({ m, slug, nid }) {
  if (m.role === 'system') return <SysLine m={m} />
  const text = m.role === 'user' ? stripEnvelope(m.text) : m.text
  return (
    <div className={'msg ' + m.role + (m.oracle ? ' oracle' : '')}>
      {(m.tools ?? []).map((t, i) => (typeof t === 'string'
        ? <div key={i} className="tools"><DotIcon fontSize="inherit" className="tooldot" /> {t}</div>
        : <ToolChip key={t.id ?? i} t={t} slug={slug} nid={nid} />))}
      {text && <div className="msgtext md" dangerouslySetInnerHTML={md(text)} />}
      {m.oracle && <div className="tools"><SparkIcon fontSize="inherit" /> oracle exchange — not retained by the node</div>}
    </div>
  )
})

// №5: the compaction boundary carries its summary behind a click — never a
// 20 KB bubble in the user's voice
function SysLine({ m }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={'msg sys' + (m.summary ? ' click' : '')}
      onClick={m.summary ? () => setOpen((o) => !o) : undefined}
      title={m.summary ? (open ? 'collapse' : 'read the compaction summary') : undefined}>
      {m.text}{m.summary && !open ? ' · summary ▶' : ''}
      {open && m.summary && <pre className="filepre">{m.summary}</pre>}
    </div>
  )
}

// Thinking-effort switch in the composer (user spec, Claude Code's control):
// a five-dot track — click a dot to set low…max, click the active dot to
// clear back to the CLI default. The permission-mode half of Claude Code's
// bar is deliberately absent: org permissions decide what agents can do.
const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max']

function EffortSwitch({ value, onSet }) {
  const idx = EFFORT_LEVELS.indexOf(value)
  return (
    <span className="effort-switch"
      title={`thinking effort — ${value || 'CLI default'}; click a dot to set,`
        + ' click the active dot to clear'}>
      <span className="eff-label">Effort{value ? ` (${value})` : ''}</span>
      <span className="eff-track">
        {EFFORT_LEVELS.map((l, i) => (
          <button key={l} type="button"
            className={'eff-dot' + (i === idx ? ' on' : '')
              + (idx >= 0 && i < idx ? ' below' : '')}
            title={l}
            onClick={() => onSet(i === idx ? '' : l)} />
        ))}
      </span>
    </span>
  )
}

// Slash commands (user-approved 2026-07-31): light HINTING when the draft
// starts with "/" — a curated list of commands known to work headless, not a
// clickable palette. Sent verbatim as a session command (no mail envelope).
const SLASH_COMMANDS = [
  ['/compact', 'summarize + free context (the org also does this automatically)'],
  ['/context', 'show what is using the context window'],
  ['/cost', 'token + cost usage for this session'],
]

function SlashHints({ text, setText }) {
  const head = text.trim().split(/\s/)[0]
  const rows = SLASH_COMMANDS.filter(([c]) => c.startsWith(head))
  return (
    <div className="slash-hints">
      {rows.map(([c, d]) => (
        <button key={c} className="slash-row" onClick={() => setText(c)}>
          <b>{c}</b> <span className="dim">{d}</span>
        </button>
      ))}
      <span className="dim slash-note">
        sent as a session command, not mail — other CLI commands may work; the
        interactive-only ones will not
      </span>
    </div>
  )
}
