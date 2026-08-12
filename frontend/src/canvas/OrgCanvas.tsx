// canvas/OrgCanvas.tsx — the canvas core: the OrgCanvas component itself —
// camera (pan/zoom/springs/follow), tree layout orchestration, wires and
// mail sparks, node dragging and re-parenting, the retired/crowd piles, the
// HUD and agent tray, and the modal wiring. Extracted verbatim from
// Canvas.tsx in the phase-3 split.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { NodeStatus, ToastFn, TreeNode, TreePayload } from '../types'
import { audienceAction, orgInboxRead, reorderNode } from '../api'
import {
  AddIcon, FrozenIcon, FullscreenIcon, PublicIcon, RemoveIcon, ViewListIcon,
} from '../icons'
import {
  ago, DOG_H, DOG_W, DRAFT, ease, EXTERN, flatten, INBOX, INBOX_H, layout, NODE_H, NODE_W, orgPxc, segD,
  segPoint, sizeOf, smooth, SPRING_C, SPRING_K, TIER_LETTER, USER, USER_H,
  USER_W, withDraftTree, Z_DESK, Z_MAX, Z_MINI,
} from './shared'
import type {
  CanvasNode, DraftScope, DraftState, MailEvent, MailLinkFn,
  OpFn, Pile, Pt, Seg, Spring, StreamEvent, View,
} from './shared'
import { Activity, ContextWheel, LineagePanel } from './desk'
import { DocReader } from './docs'
import { NodeInboxModal, OrgInboxModal } from './mail'
import { NodeConfig, PilePicker, UserConfig, WatchdogPanel } from './modals'
import { DraftNode, NodeSquare, UserNode } from './cards'

export interface OrgCanvasProps {
  tree: TreePayload
  op: OpFn
  slug: string
  toast: ToastFn
  mailEvt: MailEvent | null
  /** open the user's inbox, optionally jumped to a specific mail id */
  onInbox?: (jump?: string) => void
}

export function OrgCanvas({ tree, op, slug, toast, mailEvt, onInbox }: OrgCanvasProps) {
  const [draft, setDraft] = useState<DraftState | null>(null)
  const [configId, setConfigId] = useState<string | null>(null)
  const [lineageId, setLineageId] = useState<string | null>(null)
  const [docView, setDocView] = useState<string | null>(null)   // FR-03 reader
  const [userCfg, setUserCfg] = useState(false)
  const [trayOpen, setTrayOpen] = useState(false)   // the flat agent tray
  const [trayQ, setTrayQ] = useState('')            // №26: tray name filter
  const [trayArch, setTrayArch] = useState(false)   // archived rows shown (user
                                                    // spec: hidden by default)
  const [inboxId, setInboxId] = useState<string | null>(null)
  const [oiOpen, setOiOpen] = useState(false)       // the ORG-inbox viewer
  // inline mail links (user spec 2026-07-31): a chat's send chip opens the
  // box that HOLDS the mail, selected on it — user inbox, a node's inbox, or
  // the org inbox for outbound. Jump ids clear when the modal closes.
  const [nodeInboxJump, setNodeInboxJump] = useState<string | null>(null)
  const [oiJump, setOiJump] = useState<string | null>(null)
  const [dogView, setDogView] = useState<string | null>(null)  // FR-18 panel
  // the eye's unread-mail GLOW is gone (user ruling 2026-08-04: only agents
  // that need the user's answer glow; the header ask icon carries the rest).
  // The seen-stamp bookkeeping stays: the inbox count badge still uses it.
  const [, setInboxSeen] = useState(
    () => localStorage.getItem('orgtree-inbox-seen-' + slug) ?? '')
  const seats = tree.tiers ?? { haiku: 1, sonnet: 2, opus: 5, fable: 10 }
  // canonical retired-stack slot (user note 2026-08-06): display-order every
  // parent's children so archived siblings sit CONTIGUOUSLY at the first
  // archived ordinal. Buried members take no layout space, so with a
  // contiguous block the pile's front renders between the same visible
  // neighbors no matter which member fronts it — switching fronts can no
  // longer make the stack jump columns. Pure display transform: the doc's
  // child order is untouched (a drag commits the block order for real).
  const canonPiles = (n: CanvasNode): CanvasNode => {
    const kids = (n.children ?? []).map(canonPiles)
    const arch = kids.filter((c) => c.state === 'archived')
    if (arch.length < 2) return { ...n, children: kids }
    const at = kids.findIndex((c) => c.state === 'archived')
    const before = kids.slice(0, at).filter((c) => c.state !== 'archived')
    const after = kids.slice(at).filter((c) => c.state !== 'archived')
    return { ...n, children: [...before, ...arch, ...after] }
  }
  const vroot = useMemo(() => canonPiles(withDraftTree(tree, draft)),
    [tree, draft])   // eslint-disable-line react-hooks/exhaustive-deps
  const map = useMemo(() => flatten(vroot, seats), [vroot])   // eslint-disable-line
  // the mail-link router — STABLE identity (Msg is memoized on its props;
  // the ref carries the fresh closure). user_inbox → the eye's mailbox
  // (marking the glow seen, same as its ✉); @ext:/@org:/@mcp: → the org
  // inbox; anything else → that node's inbox. Dead targets no-op.
  const openMailRef = useRef<MailLinkFn | null>(null)
  openMailRef.current = (m) => {
    if (!m?.id || !m?.to) return
    if (m.to === 'user_inbox') {
      const nw = tree.user_inbox_newest ?? new Date().toISOString()
      localStorage.setItem('orgtree-inbox-seen-' + slug, nw)
      setInboxSeen(nw)
      onInbox?.(m.id)
    } else if (String(m.to).startsWith('@')) {
      setOiJump(m.id); setOiOpen(true)
    } else if (map.has(m.to)) {
      setNodeInboxJump(m.id); setInboxId(m.to)
    }
  }
  const openMail = useCallback<MailLinkFn>((m) => openMailRef.current?.(m), [])
  // RETIRED PILE (user spec): archived siblings in a cohort stack into ONE
  // pile so long-running orgs don't fill the canvas with retirees. The FRONT
  // retiree is the interactable card (zoom/desk/inbox/rehire); clicking the
  // visible stack margin opens a picker to bring another to the front.
  const [pileFront, setPileFront] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(localStorage.getItem('orgtree-pile-' + slug)
        || '{}') as Record<string, string>
    } catch { return {} }
  })
  const [pileOpen, setPileOpen] = useState<string | null>(null)  // parent id whose menu is open
  const setFront = useCallback((parent: string, nid: string) => {
    setPileFront((pf) => {
      const nf = { ...pf, [parent]: nid }
      localStorage.setItem('orgtree-pile-' + slug, JSON.stringify(nf))
      return nf
    })
  }, [slug])
  const piles = useMemo(() => {   // pile key ("<parent>|a"/"<parent>|c") → pile
    const out = new Map<string, Pile>()
    const walk = (n: CanvasNode) => {
      const kids = n.children ?? []
      const arch = kids.filter((c) => c.state === 'archived')
      if (arch.length >= 2) {
        const key = n.id + '|a'
        const want = pileFront[key]
        out.set(key, {
          key, parent: n.id, kind: 'a',
          list: arch.map((c) => c.id),
          front: arch.some((c) => c.id === want) ? want! : arch[arch.length - 1]!.id, // nUIA: some() hit ⇒ want defined; length>=2 checked
        })
      }
      // CROWD pile (user spec 2026-07-31): a WIDE team — more than 8 active
      // reports under one agent — stacks its LEAF reports (no subtree of
      // their own) into one pile, same front/picker mechanics as the retired
      // pile. Non-leaf reports keep their own columns; the draft card never
      // stacks (hiring stays visible at any width).
      const active = kids.filter((c) => c.state !== 'archived' && c.id !== DRAFT)
      if (active.length > 8) {
        const leaves = active.filter((c) => !(c.children ?? []).length)
        if (leaves.length >= 2) {
          const key = n.id + '|c'
          const want = pileFront[key]
          out.set(key, {
            key, parent: n.id, kind: 'c',
            list: leaves.map((c) => c.id),
            front: leaves.some((c) => c.id === want) ? want! // nUIA: some() hit ⇒ want defined
              : leaves[leaves.length - 1]!.id,               // nUIA: leaves.length >= 2 checked
          })
        }
      }
      kids.forEach(walk)
    }
    walk(vroot)
    return out
  }, [vroot, pileFront])
  const pileByFront = useMemo(() => {
    const out = new Map<string, Pile>()
    for (const p of piles.values()) out.set(p.front, p)
    return out
  }, [piles])
  // member id → its pile, for focus-brings-to-front (ref: centerOn is a
  // stable callback and must read the CURRENT piles mid-gesture)
  const pileOfRef = useRef(new Map<string, Pile>())
  useEffect(() => {
    const out = new Map<string, Pile>()
    for (const p of piles.values()) for (const m of p.list) out.set(m, p)
    pileOfRef.current = out
  }, [piles])
  const hidden = useMemo(() => {         // piled-away id → its pile's front id
    const out = new Map<string, string>()
    const bury = (n: CanvasNode, front: string) => {
      out.set(n.id, front)
      ;(n.children ?? []).forEach((c) => bury(c, front))
    }
    const walk = (n: CanvasNode) => {
      const pa = piles.get(n.id + '|a')
      const pc = piles.get(n.id + '|c')
      const crowd = pc ? new Set(pc.list) : null
      ;(n.children ?? []).forEach((c) => {
        if (pa && c.state === 'archived' && c.id !== pa.front) bury(c, pa.front)
        else if (crowd && crowd.has(c.id) && c.id !== pc!.front) {
          out.set(c.id, pc!.front)   // leaves by definition: nothing beneath
        } else walk(c)
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
        const p = t.get(n.isBearerOf)!
        t.set(n.id, {
          x: p.x + 42 + 18 * n.bearerIndex!,
          y: p.y - (NODE_H + 26) - 20 * n.bearerIndex!,
        })
      }
    }
    // FR-18: watchdogs float to the LEFT of their owner (bearers own the
    // upper-right), stacking downward — satellite entities, never laid out
    // in the hierarchy. Having a target position is ALL a spark needs, so
    // launchSpark(dog → owner) works with zero animation code.
    {
      const perOwner: Record<string, number> = {}
      for (const w of tree.watchdogs ?? []) {
        if (!t.has(w.owner)) continue
        const p = t.get(w.owner)!
        const i = perOwner[w.owner] ?? 0
        perOwner[w.owner] = i + 1
        t.set('dog:' + w.id, {
          x: p.x - (DOG_W + 26),
          y: p.y + 4 + (DOG_H + 10) * i,
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
  const [view, setView] = useState<View>(() => {
    // fit-on-load: center the initial tree in a typical viewport (re-fit against
    // the REAL viewport once mounted — see the mount effect below)
    const t = layout(withDraftTree(tree, null))
    let maxX = 0, maxY = 0
    for (const p of t.values()) { maxX = Math.max(maxX, p.x + 300); maxY = Math.max(maxY, p.y + 260) }
    const z = Math.min(1.3, Math.max(0.35, Math.min(1300 / maxX, 780 / maxY)))
    return { x: Math.max(24, (1400 - maxX * z) / 2), y: 24, z }
  })
  const [, setFrame] = useState(0)
  const [dropId, setDropId] = useState<string | null>(null)

  const viewportRef = useRef<HTMLDivElement | null>(null)
  const viewRef = useRef(view); viewRef.current = view
  const animRef = useRef<number | null>(null)
  const animBusyRef = useRef(false)  // a camera animation owns the view
  const focusRef = useRef<string | null>(null)   // №25: the desk the camera rides with
  const followRef = useRef<{ id: string; x: number; y: number } | null>(null)
  const panRef = useRef<{ sx: number; sy: number; ox: number; oy: number; moved: boolean } | null>(null)
  const springs = useRef(new Map<string, Spring>())
  // id → {x,y,at}: a node that should MATERIALIZE at a specific spot (a hire
  // replacing its draft card) instead of gliding over from its parent. Lives
  // outside springs because the reaper below deletes any spring whose id the
  // layout doesn't know yet — and the hire response lands frames before the
  // refreshed tree does
  const seedRef = useRef(new Map<string, { x: number; y: number; at: number }>())
  const targetRef = useRef(target); targetRef.current = target
  const mapRef = useRef(map); mapRef.current = map
  const nodeDrag = useRef<{
    id: string; sx: number; sy: number
    bases: Map<string, Pt>; moved: boolean
  } | null>(null)     // {id, sx, sy, ox, oy, moved}

  const posOf = (id: string): Pt | undefined =>
    springs.current.get(id) ?? targetRef.current.get(id)

  // ---------------------------------------------- wires: geometry + sparks
  // (the seg builders assert posOf: every caller pre-checks both endpoints)
  const treeSeg = (parentId: string, childId: string): Seg => {
    const a = posOf(parentId)!, b = posOf(childId)!
    const ps = sizeOf(parentId)
    return { kind: 'c', pts: [
      { x: a.x + ps.w / 2, y: a.y + ps.h },
      { x: a.x + ps.w / 2, y: a.y + ps.h + 52 },
      { x: b.x + NODE_W / 2, y: b.y - 52 },
      { x: b.x + NODE_W / 2, y: b.y }] }
  }
  const peerSeg = (lId: string, rId: string): Seg => {
    const a = posOf(lId)!, b = posOf(rId)!
    return { kind: 'l', pts: [
      { x: a.x + sizeOf(lId).w, y: a.y + sizeOf(lId).h * 0.55 },
      { x: b.x, y: b.y + sizeOf(rId).h * 0.55 }] }
  }
  const audSeg = (gId: string, eId: string): Seg => {
    const a = posOf(gId)!, b = posOf(eId)!
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

  const sparksRef = useRef<{
    id: number; segs: (Seg & { rev: boolean })[]; start: number; segDur: number
  }[]>([])
  const sparkId = useRef(0)
  const audSetRef = useRef(new Set<string>())
  audSetRef.current = new Set((tree.audiences ?? []).map((a) => a.grantor + '→' + a.grantee))

  // audience-line life cycle (user ruling): a NEW grant draws itself in,
  // grantor → grantee, over the same 420ms the message spark takes — the two
  // arrive at the new agent together; a REVOKED grant retracts the same way.
  const AUD_DUR = 420
  const audAnimRef = useRef(new Map<string,
    | { phase: 'in'; t0: number }
    | { phase: 'out'; t0: number; grantor: string; grantee: string }
  >())    // 'g→e' → {phase:'in'|'out', t0, grantor, grantee}
  const audPrevRef = useRef<Set<string> | null>(null)   // null until first sync: lines that
                                          // exist at page load never animate in
  const audRafRef = useRef<number | null>(null)
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
        const [grantor, grantee] = k.split('→') as [string, string] // nUIA: keys are always built as "<grantor>→<grantee>"
        anim.set(k, { phase: 'out', t0: now, grantor, grantee })
      }
    }
    audPrevRef.current = new Set(cur)
    if (anim.size) kickAudAnim()
  }, [tree.audiences, kickAudAnim])   // eslint-disable-line react-hooks/exhaustive-deps

  // a mail event rides the org's wires: down/up the tree, along the peer line
  // between coworkers, or along a direct audience line when one connects the two
  const launchSpark = useCallback((from: string, to: string) => {
    const m = mapRef.current
    const norm = (x: string) => (!x || x === 'user' || x === 'user_inbox' || x === USER) ? USER : x
    // org-inbox mail (user spec 2026-08-05): a spark rides the mailbox↔node
    // curve — the same facing-sides geometry the audience lines to holders
    // draw — in whichever direction the mail travels
    const isBox = (x: string) => x === 'org_inbox' || x === INBOX
    if (isBox(from) !== isBox(to)) {
      const other = norm(isBox(from) ? to : from)
      const at = posOf(INBOX), bt = posOf(other)
      if (!at || !bt || (other !== USER && !m.has(other))) return
      const ga = sizeOf(INBOX), gb = sizeOf(other)
      const left = (bt.x + gb.w / 2) < (at.x + ga.w / 2)
      const x1 = left ? at.x : at.x + ga.w
      const x2 = left ? bt.x + gb.w : bt.x
      const y1 = at.y + ga.h / 2, y2 = bt.y + gb.h / 2
      const bulge = 64 + Math.abs(y2 - y1) * 0.12
      sparksRef.current.push({
        id: ++sparkId.current,
        segs: [{ kind: 'c', pts: [
          { x: x1, y: y1 }, { x: x1 + (left ? -bulge : bulge), y: y1 },
          { x: x2 + (left ? bulge : -bulge), y: y2 }, { x: x2, y: y2 }],
          rev: !isBox(from) }],
        start: performance.now(), segDur: 420 })
      setFrame((f) => f + 1)
      return
    }
    const a = norm(from), b = norm(to)
    if (a === b || !m.has(a) || !m.has(b)) return
    const segs: (Seg & { rev: boolean })[] = []
    const aud = audSetRef.current
    if (aud.has(a + '→' + b) || aud.has(b + '→' + a)) {
      const [g, e] = aud.has(a + '→' + b) ? [a, b] : [b, a]
      segs.push({ ...audSeg(g, e), rev: g !== a })
    } else if (a !== USER && b !== USER && m.get(a)?.parent === m.get(b)?.parent) {
      const sibs = (m.get(m.get(a)!.parent!)?.children ?? []).map((c) => c.id)
        .filter((k) => m.has(k) && k !== DRAFT)
        .sort((p, q) => (targetRef.current.get(p)?.x ?? 0) - (targetRef.current.get(q)?.x ?? 0))
      const ia = sibs.indexOf(a), ib = sibs.indexOf(b)
      if (ia < 0 || ib < 0) return
      const step = ia < ib ? 1 : -1
      for (let i = ia; i !== ib; i += step) {
        segs.push({ ...peerSeg(sibs[Math.min(i, i + step)]!, sibs[Math.max(i, i + step)]!), // nUIA: i walks ia..ib, both valid indices
          rev: step < 0 })
      }
    } else {
      const chain = (id: string) => {
        const out = [id]
        let c = id
        while (c !== USER) { c = m.get(c)?.parent ?? USER; out.push(c) }
        return out
      }
      const ca = chain(a), cb = chain(b)
      const inB = new Set(cb)
      const lca = ca.find((k) => inB.has(k))!   // both chains end at USER
      for (let i = 0; ca[i] !== lca; i++) segs.push({ ...treeSeg(ca[i + 1]!, ca[i]!), rev: true }) // nUIA: lca ∈ ca ⇒ i+1 stays in range
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
  const pxPerCredit = useMemo(() => orgPxc(tree), [tree])

  // composer drafts are keyed per node id and freed slugs are re-minted by
  // later hires (review): sweep drafts whose node no longer exists at all,
  // so a namesake never inherits a dead agent's unsent instruction.
  // Archived nodes stay in `map` — their queued-until-rehire drafts survive.
  useEffect(() => {
    // ⚠ props can be mismatched for a commit: on an org switch `slug` is the
    // new org while `tree` is still the old payload (App swaps the tree only
    // when its fetch resolves, and this component is not keyed by slug). A
    // sweep in that window prunes the NEW org's storage against the OLD org's
    // node set — nearly everything. Only sweep when the two agree.
    if (tree.slug !== slug) return
    try {
      const pre = `orgtree-draft-${slug}-`
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const k = localStorage.key(i)
        if (k && k.startsWith(pre) && !map.has(k.slice(pre.length))) {
          localStorage.removeItem(k)
        }
      }
    } catch { /* private mode */ }
  }, [map, slug, tree.slug])

  // G6: the SAME sweep for every other id-keyed client store. `orgtree-eyemin-`
  // (which direct lines are collapsed), `-eyeseen-` (which ones have been seen,
  // so a new one arrives minimized) and `-pile-` (which retiree fronts its
  // stack) all key on node ids and none of them was ever pruned. They grew
  // across the org's entire hire/fire history, and a pile front could name a
  // node that no longer exists. Client-owned state is legitimate — client-owned
  // state that outlives what it refers to is just a slower kind of stale.
  useEffect(() => {
    if (!map.size) return              // never prune against a not-yet-loaded tree
    if (tree.slug !== slug) return     // mismatched-props window — see the draft sweep
    try {
      for (const suffix of ['eyemin', 'eyeseen']) {
        const k = `orgtree-${suffix}-${slug}`
        const raw = localStorage.getItem(k)
        if (!raw) continue
        const ids = JSON.parse(raw) as string[]
        const keep = ids.filter((id) => map.has(id))
        if (keep.length !== ids.length) localStorage.setItem(k, JSON.stringify(keep))
      }
      const pk = `orgtree-pile-${slug}`
      const rawP = localStorage.getItem(pk)
      if (rawP) {
        const pf = JSON.parse(rawP) as Record<string, string>
        const keep = Object.fromEntries(Object.entries(pf)
          .filter(([parent, front]) => map.has(parent) && map.has(front)))
        if (Object.keys(keep).length !== Object.keys(pf).length) {
          localStorage.setItem(pk, JSON.stringify(keep))
        }
      }
    } catch { /* private mode, or a hand-edited value — never fatal */ }
  }, [map, slug, tree.slug])

  // ------------------------------------------------------- the spring engine
  useEffect(() => {
    let raf: number, last = performance.now()
    const tick = (t: number) => {
      const dt = Math.min(0.033, (t - last) / 1000); last = t
      let active = false
      for (const [id, tgt] of targetRef.current) {
        let s = springs.current.get(id)
        if (!s) {
          const seed = seedRef.current.get(id)
          if (seed) seedRef.current.delete(id)
          const par = mapRef.current.get(id)?.parent
          const ps = !seed && par && springs.current.get(par)
          s = seed ? { x: seed.x, y: seed.y, vx: 0, vy: 0 }
            : ps ? { x: ps.x, y: ps.y, vx: 0, vy: 0 }
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
      for (const [id, sd] of seedRef.current) {   // a hire that never landed
        if (t - sd.at > 10000) seedRef.current.delete(id)
      }
      // self-heal native scroll: the viewport pans by transform only, but
      // focusing an off-screen element (a spawned draft's name input) makes
      // the browser scroll the overflow:hidden box — which shears the HUD and
      // tray off the canvas and corrupts every centerOn until reload
      const vpEl = viewportRef.current
      if (vpEl && (vpEl.scrollLeft || vpEl.scrollTop)) {
        vpEl.scrollLeft = 0
        vpEl.scrollTop = 0
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
  const animateTo = useCallback((to: View, ms = 460) => {
    cancelAnimationFrame(animRef.current!)
    animBusyRef.current = true
    const from = { ...viewRef.current }
    const t0 = performance.now()
    const step = (t: number) => {
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

  // the HUD ± buttons zoom about the SCREEN CENTER — changing z with x/y
  // held fixed anchors the world origin instead, which (with the eye parked
  // at world x=6000) read as a violent sideways pan, not a zoom
  const zoomStep = useCallback((factor: number) => {
    const vp = viewportRef.current?.getBoundingClientRect()
    const v = viewRef.current
    const z = Math.min(Z_MAX, Math.max(0.24, v.z * factor))
    if (!vp || z === v.z) return
    const cx = vp.width / 2, cy = vp.height / 2
    const wx = (cx - v.x) / v.z, wy = (cy - v.y) / v.z
    animateTo({ x: cx - wx * z, y: cy - wy * z, z }, 220)
  }, [animateTo])

  const centerOn = useCallback((id: string, z: number | null = null) => {
    // focusing a BURIED pile member brings it to the front first (user spec
    // 2026-08-05), then finishes the glide once the re-layout gives it a
    // position (two frames: state commit, then layout)
    const pile = pileOfRef.current.get(id)
    if (pile && pile.front !== id) {
      setFront(pile.key, id)
      requestAnimationFrame(() => requestAnimationFrame(() =>
        centerRef.current?.(id, z)))
      return
    }
    const p = targetRef.current.get(id)
    const vp = viewportRef.current?.getBoundingClientRect()
    if (!p || !vp) return
    // click-to-focus fills the window with the card, small margin all round.
    // The EYE fits by HEIGHT only — it is the one cell that expands in width
    // to the screen's aspect ratio (the switchboard), so height is the fit.
    // audit 2026-08-01 (found by the mobile sweep, but a live DESKTOP bug):
    // on short/narrow windows the fit-derived zoom lands BELOW Z_DESK — the
    // camera animates and no desk (or switchboard) ever opens, silently.
    // Floor the focus zoom at the desk threshold: overflowing the viewport
    // beats a focus gesture that cannot focus.
    const zz = z ?? Math.max(Z_DESK, id === USER
      ? Math.min(Z_MAX, (vp.height - 48) / USER_H)
      : Math.min(Z_MAX, (Math.min(vp.width, vp.height) - 48) / NODE_H))
    animateTo({
      x: vp.width / 2 - (p.x + NODE_W / 2) * zz,
      y: vp.height / 2 - (p.y + NODE_H / 2) * zz,
      z: zz,
    })
  }, [animateTo, setFront])
  const centerRef = useRef<typeof centerOn | null>(null)
  centerRef.current = centerOn

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
    const onWheel = (e: WheelEvent) => {
      // wheel inside a modal always scrolls the modal. Inside a desk it NEVER
      // zooms (user ruling, reversing the earlier fall-through-to-zoom): the
      // wheel is scroll-only there, even when nothing can scroll — zoom by
      // moving the cursor off the desk first.
      // (native listener — it fires before React's delegated handlers, so
      // component-level stopPropagation can't guard it)
      if ((e.target as Element | null)?.closest?.('.overlay')) return
      if ((e.target as Element | null)?.closest?.('.desk-over')) return
      e.preventDefault()
      cancelAnimationFrame(animRef.current!)
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

  const toWorld = (e: { clientX: number; clientY: number }): Pt => {
    const r = viewportRef.current!.getBoundingClientRect()
    const v = viewRef.current
    return { x: (e.clientX - r.left - v.x) / v.z, y: (e.clientY - r.top - v.y) / v.z }
  }

  // background pan
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    cancelAnimationFrame(animRef.current!)
    animBusyRef.current = false
    panRef.current = { sx: e.clientX, sy: e.clientY, ox: viewRef.current.x, oy: viewRef.current.y, moved: false }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = panRef.current
    if (!d) return
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
    if (d.moved) setView((v) => ({ ...v, x: d.ox + dx, y: d.oy + dy }))
  }
  const onPointerUp = () => { panRef.current = null }

  // ----------------------------------------------------------- node dragging
  const descendantsOf = (id: string) => {
    const out = new Set<string>()
    const walk = (k: string) => mapRef.current.get(k)?.children.forEach((c) => { out.add(c.id); walk(c.id) })
    walk(id)
    return out
  }

  const dropTargetAt = (world: Pt, dragId: string): string | null => {
    const banned = descendantsOf(dragId); banned.add(dragId); banned.add(DRAFT)
    // C0: the org-inbox panel is a drop target too — dropping an agent on it
    // GRANTS the org-inbox audience (reorder-style interaction, user ruling).
    // posOf(INBOX) exists only while the panel is laid out (= visible), which
    // keeps this check ref-safe inside a drag.
    {
      const p = posOf(INBOX)
      if (p) {
        const { w, h } = sizeOf(INBOX)
        if (world.x >= p.x && world.x <= p.x + w
          && world.y >= p.y && world.y <= p.y + h) return INBOX
      }
    }
    for (const [id] of targetRef.current) {
      if (banned.has(id)) continue
      const n = mapRef.current.get(id)
      if (id !== USER && n?.state !== 'live') continue
      const p = posOf(id)!
      const { w, h } = sizeOf(id)
      if (world.x >= p.x && world.x <= p.x + w && world.y >= p.y && world.y <= p.y + h) return id
    }
    return null
  }

  const startNodeDrag = (e: React.PointerEvent<HTMLDivElement>, id: string) => {
    if (e.button !== 0) return
    if ((e.target as Element).closest('button, input, textarea, select, .cbar, .desk-body')) return
    if (mapRef.current.get(id)?.isBearerOf) {   // lineage cards are not org nodes
      e.stopPropagation()
      nodeDrag.current = { id, sx: e.clientX, sy: e.clientY, bases: new Map(), moved: false }
      return
    }
    e.stopPropagation()
    // the grabbed node carries its FULL subtree (user ruling) — record every
    // member's position so they all move as one rigid group
    const bases = new Map<string, Pt>()
    for (const k of [id, ...descendantsOf(id)]) {
      const p = posOf(k)
      if (p) bases.set(k, { x: p.x, y: p.y })
    }
    nodeDrag.current = { id, sx: e.clientX, sy: e.clientY, bases, moved: false }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const moveNodeDrag = (e: React.PointerEvent<HTMLDivElement>, id: string) => {
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
  const abortNodeDrag = (_e: React.PointerEvent<HTMLDivElement>, id: string) => {
    // pointercancel path: restore the recorded bases and commit NOTHING —
    // see the NodeSquare comment (a cancel routed through endNodeDrag's
    // no-drop branch issued an unconditional reorder POST)
    const d = nodeDrag.current
    if (!d || d.id !== id) return
    nodeDrag.current = null
    setDropId(null)
    for (const [k, b] of d.bases) {
      const s = springs.current.get(k)
      if (s) { s.x = b.x; s.y = b.y; s.vx = 0; s.vy = 0 }
    }
    setFrame((f) => f + 1)
  }
  const endNodeDrag = (e: React.PointerEvent<HTMLDivElement>, id: string,
    node: CanvasNode, focused: boolean) => {
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
    const ancestors: string[] = []
    let cur = parent
    while (cur != null) { ancestors.push(cur); cur = mapRef.current.get(cur)?.parent }

    const finish = () => setFrame((f) => f + 1)   // springs glide back/onward
    if (drop === INBOX) {
      // dropping on the mailbox grants the org-inbox audience, never reparents
      audienceAction(slug, 'grant', id, 'extern')
        .then(() => toast([`${id} now reads and answers the org inbox`],
          () => audienceAction(slug, 'revoke', id, EXTERN).catch(() => {})))
        .catch((err: Error) => toast([`error: ${err.message}`]))
        .finally(finish)
      return
    }
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
    const cohort = (mapRef.current.get(parent!)?.children ?? [])
      .map((c) => c.id).filter((k) => k !== DRAFT)
    // RETIRED STACK (user note 2026-08-06): dragging the front drags the
    // WHOLE stack — every member moves as one contiguous block in canonical
    // (child-list) order, committed member-by-member behind the same anchor.
    // Anchors are always NON-members: buried cards are invisible and their
    // layout positions alias the front's, so they cannot order anything.
    const pile = pileOfRef.current.get(id)
    const block = pile && pile.kind === 'a' && pile.front === id
      ? pile.list : [id]
    const members = new Set(block)
    const sibs = cohort.filter((k) => !members.has(k))
    if (!sibs.length) { finish(); return }
    const first = block[0]!
    const oldIdx = cohort.indexOf(first)
    const beforeOld = cohort.slice(0, Math.max(oldIdx, 0)).reverse()
      .find((k) => !members.has(k))
    const oldReq = beforeOld ? { after: beforeOld }
      : { before: sibs.find((k) => cohort.indexOf(k) > oldIdx) ?? sibs[0]! }
    const x = springs.current.get(id)?.x ?? 0
    const beforeSib = sibs.find((k) => (targetRef.current.get(k)?.x ?? 0) > x)
    const req = beforeSib ? { before: beforeSib } : { after: sibs[sibs.length - 1] }
    const chain = async (lead: { before?: string; after?: string }) => {
      let prev: string | null = null
      for (const m of block) {
        await reorderNode(slug, m, prev ? { after: prev } : lead)
        prev = m
      }
    }
    // №17: a successful accidental reorder used to be completely silent
    chain(req)
      .then(() => toast([block.length > 1
        ? `${id} reordered (with its ${block.length - 1} stacked sibling`
          + (block.length > 2 ? 's)' : ')')
        : `${id} reordered`],
        () => chain(oldReq).catch(() => {})))
      .catch((err: Error) => toast([`error: ${err.message}`])).finally(finish)
  }

  // ------------------------------------------------------- focus (the desk)
  const focusId = useMemo(() => {
    if (view.z < Z_DESK) return null
    const vp = viewportRef.current?.getBoundingClientRect()
    const cw = vp ? vp.width / 2 : 500, ch = vp ? vp.height / 2 : 350
    let best: string | null = null, bestD = Infinity
    for (const [id] of target) {
      if (id === DRAFT) continue         // the EYE can be a desk too (switchboard)
      if (hidden.has(id)) continue       // piled-away retirees never take focus
      const p = posOf(id)!
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
    const links: [string, string][] = []
    for (const n of map.values()) {
      if (!n.children || n.children.length < 2 || n.isBearerOf) continue
      const sibs = n.children.map((c) => c.id)
        .filter((k) => map.get(k)?.state === 'live')
        .sort((p, q) => (target.get(p)?.x ?? 0) - (target.get(q)?.x ?? 0))
      for (let i = 0; i + 1 < sibs.length; i++) links.push([sibs[i]!, sibs[i + 1]!])
    }
    return links
  }, [map, target])

  const audLines = (tree.audiences ?? [])
    .filter((a) => map.has(a.grantor) && map.has(a.grantee))

  const spawn = (parentId: string, tier: string) => {
    setDraft({ parent: parentId === USER ? null : parentId, tier })
    // roughly OVERVIEW scale start to finish (user ruling): the form is
    // authored on a 200px virtual surface (scale .6 into the card), so
    // z ≈ 1.7 already renders authored px ≈ screen px — no screen-fill dive.
    // Clamped from ABOVE too: spawning from a desk (chips live there now)
    // must glide OUT to overview, not render the form at desk fill
    setTimeout(() => centerOn(
      DRAFT, Math.min(2.05, Math.max(1.7, viewRef.current.z))), 60)
  }
  // F-03: hire a COWORKER — same superior, placed to the chosen side of the
  // anchor. Top-level agents side-hire more top-levels (parent is the user).
  const spawnBeside = (n: CanvasNode, tier: string, side: 'left' | 'right') => {
    setDraft({ parent: !n.parent || n.parent === USER ? null : n.parent, tier,
               beside: { anchor: n.id, side } })
    setTimeout(() => centerOn(
      DRAFT, Math.min(2.05, Math.max(1.7, viewRef.current.z))), 60)
  }
  // FR-25: insert a PARENT — hire under the anchor's own superior (the exact
  // parent resolution spawnBeside uses), then confirmDraft moves the anchor
  // beneath the fresh hire. move() is budget-neutral, so the chain needs no
  // credit headroom beyond the hire itself.
  const spawnAbove = (n: CanvasNode, tier: string) => {
    setDraft({ parent: !n.parent || n.parent === USER ? null : n.parent, tier,
               above: { anchor: n.id } })
    setTimeout(() => centerOn(
      DRAFT, Math.min(2.05, Math.max(1.7, viewRef.current.z))), 60)
  }
  const confirmDraft = (name: string, grant: number, charter: string,
    scope: DraftScope | null) => {
    op({ op: 'hire', parent: draft!.parent, tier: draft!.tier, grant, name,
         charter: charter?.trim() || undefined,
         // pre-hire permissions (user spec): staged in the draft's modal,
         // applied atomically WITH the hire — effort included (review: the
         // old post-hire /scope call is 403'd through the kiosk gateway,
         // and its failure was swallowed silently)
         ...(scope ? { add_dirs: scope.add_dirs, tools: scope.tools,
                       org_visibility: scope.org_visibility,
                       effort: scope.effort || undefined } : {}) })
      .then((r) => {
        // the real card replaces the draft IN PLACE — seed its birth position
        // from the draft's spring. Via seedRef, NOT a direct springs.set: the
        // reaper deletes unknown-id springs every tick, and the refreshed tree
        // (which makes the id known) lands frames after this response — a
        // direct set died instantly and the node glided in from its parent
        const ds = springs.current.get(DRAFT)
        // (typeof-narrows the op result's open dict: hire returns {node: str})
        const born = r?.node
        if (typeof born === 'string' && born && ds) {
          seedRef.current.set(born, { x: ds.x, y: ds.y, at: performance.now() })
        }
        // F-03: pin the ordering the side chip promised. Best-effort — the
        // hire itself already succeeded, and reorder is cosmetic (its own
        // ruling); a failure leaves the hire appended at the end.
        const beside = draft?.beside
        if (typeof born === 'string' && born && beside) {
          void reorderNode(slug, born, beside.side === 'left'
            ? { before: beside.anchor } : { after: beside.anchor })
            .catch(() => {})   // the broadcast refetch shows the final order
        }
        // FR-25: the SPLICE — the anchor moves under the node just hired.
        // NOT best-effort like the reorder above: the move is the entire
        // point of the top chip, so a failure is reported loudly with the
        // manual completion named (a drag finishes what the chain started).
        // Sequenced AFTER hire success by construction; move() itself is
        // budget-neutral and cycle-checked server-side.
        const above = draft?.above
        if (typeof born === 'string' && born && above) {
          op({ op: 'move', node: above.anchor, new_parent: born })
            .catch((e: Error) => toast([
              `hired ${born}, but the splice failed: ${e.message}`,
              `${above.anchor} still reports to its old superior — drag it `
              + `onto ${born} to finish the insert`]))
        }
        setDraft(null)
      }).catch((e: Error) => toast([`hire failed: ${e.message}`]))
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
    const walk = (n: TreeNode) => {
      // (const extraction: `free` is null on non-live nodes — ledger.py:2524)
      const f = n.free
      if (n.state === 'live' && f != null && f > 0) free += f
      n.children.forEach(walk)
    }
    tree.roots.forEach(walk)
    const circ = tree.audit?.top_level_holds ?? 0
    return { circ, seats: circ - free, free }
  }, [tree])

  return (
    <div className={'viewport' + (tree.sandboxed ? ' sandboxed' : '')
      + (tree.headless ? ' headless' : '')} ref={viewportRef}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove}
      onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      onScroll={(e) => {
        // the viewport pans by TRANSFORM only — any native scroll is the
        // browser force-scrolling an overflow:hidden box to reach a focused
        // element (the draft's name input, off-screen when spawning from a
        // desk). A real scrollLeft/Top here shears every screen-space anchor
        // (HUD, tray) off the canvas and corrupts centerOn math; zero it.
        e.currentTarget.scrollLeft = 0
        e.currentTarget.scrollTop = 0
      }}>
      <div className="space" style={{
        width: bounds.w, height: bounds.h,
        transform: `translate(${view.x}px, ${view.y}px) scale(${view.z})`,
        '--invz': Math.min(2.4, Math.max(1 / Z_MAX, 1 / view.z)).toFixed(3),
        // UNCLAMPED counter-scale for the hire chips (user report 2026-08-04:
        // "the chips disappear too soon when zooming out"). The 2.4 clamp let
        // chips shrink with the world below z≈0.42 while the card was still
        // usable; the hire gesture stays screen-constant for as long as the
        // card exists. Other --invz users keep the clamp — a screen-constant
        // badge on a distant card is noise, a screen-constant CONTROL is not.
        '--invzf': Math.max(1 / Z_MAX, 1 / view.z).toFixed(3),
      }}>
        <svg className="edges" width={bounds.w} height={bounds.h}>
          {[...map.values()].filter((n) => n.parent && !n.isBearerOf
            && !hidden.has(n.id)).map((n) => {
            if (!posOf(n.parent!) || !posOf(n.id)) return null
            return <path key={n.id} d={segD(treeSeg(n.parent!, n.id))}
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
            const out: ReactNode[] = []
            for (const a of audLines) {
              if (!posOf(a.grantor) || !posOf(a.grantee)) continue
              const k = a.grantor + '→' + a.grantee
              const st = anim.get(k)
              let dash: number | null = null
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
            const a = posOf(n.isBearerOf!), b = posOf(n.id)
            if (!a || !b) return null
            return <path key={'t' + n.id}
              d={`M ${a.x + NODE_W - 10} ${a.y + 8} L ${b.x + 10} ${b.y + NODE_H - 8}`}
              className="edge tether" />
          })}
          {/* FR-18: the watchdog wire — the user's spec verbatim ("connected
              to their owner with a wire"); the spark rides it on each fire */}
          {(tree.watchdogs ?? []).map((w) => {
            const a = posOf('dog:' + w.id), b = posOf(w.owner)
            if (!a || !b) return null
            return <path key={'w' + w.id}
              d={`M ${a.x + DOG_W - 4} ${a.y + DOG_H / 2} L ${b.x + 4} ${b.y + 24}`}
              className={'edge tether wd' + (w.state !== 'armed' ? ' off' : '')} />
          })}
          {tree.org_inbox?.visible && posOf(INBOX) && (() => {
            // no box↔eye tether (user revision) — the panel stands alone;
            // only audience lines to its holders. Those connect FACING sides:
            // an agent left of the box joins from its RIGHT side (user spec),
            // an agent right of it from its left.
            const out: ReactNode[] = []
            for (const h of tree.org_inbox.holders ?? []) {
              if (!map.has(h) || !posOf(h)) continue
              const a = posOf(INBOX)!, b = posOf(h)!
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
            const seg = sp.segs[i]! // nUIA: i clamped to 0..len-1 and segs is never empty (guarded at push)
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
              stats={orgStats}
              kiosk={tree.kiosk} pub={!!tree.public} kioskRemaining={kioskRemaining}
              kioskSegs={tree.roots.filter((n) => n.state === 'live')
                .map((n) => ({ seat: n.seat, grant: n.grant }))}
              pxc={pxPerCredit} zoom={view.z}
              focused={focusId === USER} eyeW={Math.max(eyeW, USER_W)}
              onFocus={() => centerOn(USER)}
              posX={(id) => posOf(id)?.x ?? 0}
              onJump={(id) => centerOn(id)}
              map={map} op={op} slug={slug} toast={toast}
              compactAt={tree.compact_at} maxTop={tree.max_top_grant ?? 1000}
              /* split counts (user spec 2026-08-06): asks OUTRANK mail on the
                 pip — asksOpen covers pending credit requests AND open
                 questions (adding credit_requests too would double-count) */
              inboxCount={tree.user_inbox_count ?? 0}
              asksOpen={tree.asks_open ?? 0}
              onInbox={() => {
                const nw = tree.user_inbox_newest ?? new Date().toISOString()
                localStorage.setItem('orgtree-inbox-seen-' + slug, nw)
                setInboxSeen(nw)
                onInbox?.()
              }}
              onGear={() => setUserCfg(true)}
              onMailLink={openMail} onOpenDoc={setDocView}
              onSpawn={(t) => spawn(USER, t)} />
          }
          if (n.id === DRAFT) {
            return <DraftNode key={DRAFT} pos={p} draft={draft!} map={map} seats={seats}
              maxTop={tree.max_top_grant ?? 1000} kioskRemaining={kioskRemaining}
              defaultTop={tree.default_top_grant ?? 50} tree={tree}
              zoom={view.z} pxc={pxPerCredit}
              onConfirm={confirmDraft} onCancel={() => setDraft(null)} />
          }
          if (hidden.has(n.id)) return null   // piled-away: no card, no space
          const pileHere = pileByFront.get(n.id)
          const square = (
            <NodeSquare key={n.id} node={n} pos={p} lod={lod} focused={n.id === focusId}
              dragging={nodeDrag.current?.id === n.id && nodeDrag.current!.moved}
              isDrop={dropId === n.id}
              seats={seats} map={map} op={op} slug={slug} toast={toast}
              pxc={pxPerCredit} zoom={view.z}
              onSpawn={(t) => spawn(n.id, t)}
              onSpawnSide={(t, side) => spawnBeside(n, t, side)}
              onSpawnTop={(t) => spawnAbove(n, t)}
              onConfig={() => setConfigId(n.id)}
              onInbox={() => setInboxId(n.id)} onLineage={() => setLineageId(n.id)}
              onOpenDoc={setDocView}
              onMailLink={openMail}
              onRecenter={() => centerOn(n.id)}   /* recenter AND re-zoom to fill */
              onJump={centerOn}                   /* F-01 nav chips */
              pub={!!tree.public} kioskRemaining={kioskRemaining}
              cascadeAlloc={tree.cascade_alloc !== false}
              maxTop={tree.max_top_grant ?? 1000} maxTier={tree.kiosk?.max_tier}
              pile={pileHere} compactAt={tree.compact_at}
              onDragStart={startNodeDrag} onDragMove={moveNodeDrag}
              onDragEnd={endNodeDrag} onDragCancel={abortNodeDrag} />
          )
          if (!pileHere) return square
          // the pile's stack layers render BEHIND the front card as real
          // elements (box-shadows can't be clicked): the exposed margin is
          // the hit target that opens the picker, and the whole stack eases
          // outward slightly on hover (user spec). Retired piles and live
          // CROWD piles share the mechanics; the crowd wears a live tint.
          const layers = Math.min(pileHere.list.length - 1, 3)
          const pTitle = pileHere.kind === 'c'
            ? `${pileHere.list.length} teammates stacked — click to choose who's in front`
            : `${pileHere.list.length} retired here — click to choose who's in front`
          return (
            <span key={n.id}>
              <div className={'pile-stack' + (pileHere.kind === 'c' ? ' crowd' : '')}
                style={{ transform: `translate(${p.x}px, ${p.y}px)`,
                         width: NODE_W, height: NODE_H }}>
                {Array.from({ length: layers }, (_, i) => layers - i).map((i) => (
                  <button key={i} className={'pile-layer l' + i}
                    title={pTitle}
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => { e.stopPropagation(); setPileOpen(pileHere.key) }} />
                ))}
                <button className="pile-count"
                  title={pTitle}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => { e.stopPropagation(); setPileOpen(pileHere.key) }}>
                  {pileHere.list.length}</button>
              </div>
              {square}
            </span>
          )
        })}
        {/* FR-18: watchdog chips — tiny satellite cards beside their owner
            (the user's spec: named; click for the detail + sent-events
            panel). Not agents: no chrome beyond name + state glyph. */}
        {(tree.watchdogs ?? []).map((w) => {
          const p = posOf('dog:' + w.id)
          if (!p || hidden.has(w.owner)) return null
          return (
            <button key={'dog' + w.id}
              className={'wd-chip ' + w.state}
              style={{ transform: `translate(${p.x}px, ${p.y}px)`,
                       width: DOG_W, height: DOG_H }}
              title={`watchdog "${w.name}" (${w.kind}) — ${w.state}; `
                + 'click for detail'}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); setDogView(w.id) }}>
              <span className="wd-glyph">{w.state === 'armed' ? '◉'
                : w.state === 'paused' ? '◫' : '✕'}</span>
              <span className="wd-name">{w.name}</span>
            </button>
          )
        })}
        {tree.org_inbox?.visible && posOf(INBOX) && (
          <div className={'sq orginbox' + (dropId === INBOX ? ' drop' : '')}
            style={{
              transform: `translate(${posOf(INBOX)!.x}px, ${posOf(INBOX)!.y}px)`,
              width: USER_W, height: INBOX_H,
            }}
            title="the org inbox — outside mail addressed to this organization"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => setOiOpen(true)}>
            <div className="oi-head">
              {/* the label is its own element so it can ELLIPSIS instead of
                  wrapping. As a bare text node it was an anonymous flex item
                  that wrapped to two lines the moment the unread badge
                  competed for width — measured 13.5px → 25.5px — which
                  flex-shrank .oi-last to 2.3px inside the fixed-height tile
                  and clipped the last recipient (user bug 2026-08-07) */}
              <PublicIcon fontSize="inherit" />
              <span className="oi-title">org inbox</span>
              {(() => {   // F-06: hub connectivity at a glance on the mailbox
                const hubs = (tree.net?.hubs ?? [])
                  .filter((h) => h.enabled && !h.hidden)
                if (!hubs.length) return null
                const up = hubs.filter((h) => h.connected).length
                const cls = up === hubs.length ? ' ok' : up > 0 ? ' mix' : ''
                return <span className={'oi-dot' + cls}
                  title={hubs.map((h) => `${h.name || h.address}: `
                    + (h.connected ? 'connected' : h.error || 'connecting…'))
                    .join(' · ')} />
              })()}
              {/* NO unread badge here (user 2026-08-10). Org-inbox mail is
                  addressed to the ORGANIZATION and answered by its agents —
                  the user is not its reader. An unread count asks them to
                  clear something that was never theirs to clear, which is a
                  standing obligation invented by the UI. The tile still shows
                  the last correspondent and opens on click, so nothing is
                  hidden; only the demand is gone. The `unread` field itself
                  stays — the server and the agents use it. */}
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
      {/* nav cluster (user spec): bottom-LEFT beside the agents tray, so
          every zoom target lives in one stack — ordered top to bottom:
          switchboard · full view · zoom in · zoom out */}
      <div className="zoomhud" onPointerDown={(e) => e.stopPropagation()}>
        <button className="hud-eye" title="jump to the switchboard"
          onClick={() => centerOn(USER)}>
          <svg viewBox="0 0 48 26">
            <path d="M 2 13 C 13 2, 35 2, 46 13 C 35 24, 13 24, 2 13 Z" />
            <circle className="iris" cx="24" cy="13" r="6.5" />
            <circle className="pupil" cx="24" cy="13" r="2.6" />
          </svg>
        </button>
        <button title="fit the whole org" onClick={() => fitAll()}><FullscreenIcon fontSize="inherit" /></button>
        <button title="zoom in" onClick={() => zoomStep(1.3)}><AddIcon fontSize="inherit" /></button>
        <button title="zoom out" onClick={() => zoomStep(1 / 1.3)}><RemoveIcon fontSize="inherit" /></button>
      </div>
      {/* the agent TRAY (user spec): every agent — tier token, name, context
          wheel, working state — in the nodes' own visual language; a row
          click glides to that agent. FR-16 (2026-08-11): listed by HIERARCHY
          — each superior immediately followed by its subtree, indented per
          depth — not by canvas position */}
      <div className="tray-wrap" onPointerDown={(e) => e.stopPropagation()}>
        {trayOpen && (
          <div className="tray">
            <input className="mail-filter tray-filter" placeholder="filter agents…"
              value={trayQ} onChange={(e) => setTrayQ(e.target.value)} />
            {/* archived rows are HIDDEN by default (user spec 2026-07-31) —
                the count row folds them in and out */}
            {(() => {
              const archN = [...map.values()].filter((n) =>
                n.id !== USER && n.id !== DRAFT && !n.isBearerOf
                && n.state !== 'live').length
              return archN > 0 && (
                <button className="tray-arch"
                  onClick={() => setTrayArch((v) => !v)}>
                  {trayArch ? '▾ hide' : '▸ show'} {archN} archived
                </button>
              )
            })()}
            {(() => {
              // FR-16 (user request 2026-08-06): the tray lists by HIERARCHY —
              // every direct report immediately after its superior, indented a
              // step — replacing the old canvas-position sort, which put a
              // child hired far from its parent nowhere near it in the list.
              // Sibling order keeps the position sort, so the tray still
              // tracks the canvas arrangement locally.
              const all = [...map.values()]
                .filter((n) => n.id !== USER && n.id !== DRAFT && !n.isBearerOf)
              const q = trayQ.trim().toLowerCase()
              const match = (n: CanvasNode) =>
                (trayArch || n.state === 'live')
                && (!q || n.id.toLowerCase().includes(q))
              const kids = new Map<string, CanvasNode[]>()
              for (const n of all) {
                const p = n.parent && map.has(n.parent) && n.parent !== USER
                  ? n.parent : USER
                kids.set(p, [...(kids.get(p) ?? []), n])
              }
              const byPos = (a: CanvasNode, b: CanvasNode) => {
                const pa = posOf(a.id) ?? { x: 0, y: 0 }
                const pb = posOf(b.id) ?? { x: 0, y: 0 }
                return pa.y - pb.y || pa.x - pb.x
              }
              // a filtered-out ANCESTOR of a matching row still renders, as a
              // dim ghost: with indentation carrying meaning, dropping it
              // would leave the descendant indented under a gap with no
              // visible parent (the docket's own open question — resolved
              // toward keeping the indent readable)
              const anyMatch = (n: CanvasNode): boolean =>
                match(n) || (kids.get(n.id) ?? []).some(anyMatch)
              const rows: { n: CanvasNode; depth: number; ghost: boolean }[] = []
              const walk = (id: string, depth: number) => {
                for (const c of (kids.get(id) ?? []).sort(byPos)) {
                  if (!anyMatch(c)) continue
                  rows.push({ n: c, depth, ghost: !match(c) })
                  walk(c.id, depth + 1)
                }
              }
              walk(USER, 0)
              return rows.map(({ n, depth, ghost }) => {
                // a piled-away agent comes to the FRONT of its pile when
                // picked from the tray, then the glide lands on it — the key
                // names the pile KIND (retired |a vs live crowd |c)
                const go = () => {
                  if (hidden.has(n.id)) {
                    const par = map.get(n.id)?.parent
                    setFront(par! + (n.state === 'archived' ? '|a' : '|c'), n.id)
                  }
                  centerOn(n.id)
                }
                // №13: the status summary is TEXT here, not a tooltip — and a
                // finished status survives the next turn as prev_status (dim)
                const stat: (NodeStatus & { _stale?: boolean }) | null = n.last_status
                  ?? (n.prev_status ? { ...n.prev_status, _stale: true } : null)
                return (
                <div key={n.id} role="button" tabIndex={0}
                  className={'tray-row' + (n.state !== 'live' ? ' off' : '')
                    + (ghost ? ' ghost' : '')}
                  style={{ paddingLeft: 8 + depth * 14 }}
                  title={ghost
                    ? 'shown for context — this row does not match the '
                      + 'current filter, but a report under it does'
                    : undefined}
                  onClick={go}
                  onKeyDown={(e) => { if (e.key === 'Enter') go() }}>
                  <div className="tray-main">
                    <span className={'tier t-' + n.tier}>{TIER_LETTER[n.tier!] ?? '?'}</span>
                    <span className="tray-name"
                      title={(n.charter || '').split('\n')[0] || n.id}>{n.id}</span>
                    <ContextWheel occ={n.occupancy} cw={n.context_window}
                      compactAt={tree.compact_at} />
                    {n.busy ? (n.waiting
                      ? <span className="statusdot waiting"
                          title="queued — waiting for a free turn slot (№12)" />
                      : <span title={n.inflight_at
                          ? `running for ${ago(n.inflight_at)}` : 'working'}>
                          <Activity act={n.activity} dotOnly />
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
              })
            })()}
          </div>
        )}
        <button className="tray-toggle" title="every agent, by hierarchy"
          onClick={() => setTrayOpen((o) => !o)}>
          <ViewListIcon fontSize="inherit" /> agents
        </button>
      </div>
      {configId && map.get(configId) && (
        <NodeConfig node={map.get(configId)!} map={map} tree={tree} slug={slug}
          op={op} toast={toast} close={() => setConfigId(null)} />
      )}
      {lineageId && map.get(lineageId) && (
        <LineagePanel node={map.get(lineageId)!} op={op} slug={slug}
          close={() => setLineageId(null)} />
      )}
      {dogView && (tree.watchdogs ?? []).some((w) => w.id === dogView) && (
        <WatchdogPanel slug={slug} toast={toast}
          dog={(tree.watchdogs ?? []).find((w) => w.id === dogView)!}
          close={() => setDogView(null)} />
      )}
      {docView && (
        <DocReader slug={slug} docId={docView} toast={toast}
          close={() => setDocView(null)} />
      )}
      {userCfg && (
        <UserConfig tree={tree} slug={slug} toast={toast}
          close={() => setUserCfg(false)} />
      )}
      {inboxId && map.get(inboxId) && (
        <NodeInboxModal node={map.get(inboxId)!} slug={slug}
          jumpTo={nodeInboxJump}
          close={() => { setInboxId(null); setNodeInboxJump(null) }} />
      )}
      {pileOpen && piles.get(pileOpen) && (
        <PilePicker pile={piles.get(pileOpen)!} map={map} op={op} toast={toast}
          onPick={(nid) => { setFront(pileOpen, nid); setPileOpen(null) }}
          close={() => setPileOpen(null)} />
      )}
      {oiOpen && (
        <OrgInboxModal inbox={tree.org_inbox} net={tree.net} map={map} slug={slug} toast={toast}
          jumpTo={oiJump}
          close={() => {
            setOiOpen(false); setOiJump(null)
            // closing the panel acknowledges the whole log (same idiom as the
            // eye's glow: opening acknowledges attention)
            if (tree.org_inbox?.unread) orgInboxRead(slug).catch(() => {})
          }} />
      )}
    </div>
  )
}
