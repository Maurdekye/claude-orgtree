// canvas/OrgCanvas.tsx — the canvas core: the OrgCanvas component itself —
// camera (pan/zoom/springs/follow), tree layout orchestration, wires and
// mail sparks, node dragging and re-parenting, the retired/crowd piles, the
// HUD and agent tray, and the modal wiring. Extracted verbatim from
// Canvas.tsx in the phase-3 split.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { AudienceGrant, NodeStatus, ProviderInfo, ToastFn, TreeNode, TreePayload } from '../types'
import { audienceAction, getProviders, orgInboxRead, reorderNode } from '../api'
import {
  AddIcon, AutorenewIcon, ChevronLeftIcon, ChevronRightIcon, FrozenIcon,
  FullscreenIcon, PublicIcon, RemoveIcon, ViewListIcon,
} from '../icons'
import {
  ago, attentionPip, CODEX_TIER_LETTER, CODEX_TIER_SEAT, CODEX_TIERS, DOG_H, DOG_W, DRAFT, ease, edgeJumpPlacement, type EJForm, EXTERN, fallbackActive, flatten, INBOX, INBOX_H, layout, NODE_H, NODE_W, orgPxc, segD,
  segPoint, sizeOf, smooth, SPRING_C, SPRING_K, TIER_LETTER, TIERS, USER, USER_H,
  USER_W, withDraftTree, Z_DESK, Z_MAX, Z_MINI,
} from './shared'
import type {
  CanvasNode, DraftScope, DraftState, MailEvent, MailLinkFn,
  OpFn, Pile, Pt, Seg, Spring, StreamEvent, View,
} from './shared'
import { Activity, ContextWheel, DeskChat, LineagePanel } from './desk'
import { DocReader } from './docs'
import { NodeInboxModal, OrgInboxModal } from './mail'
import { NodeConfig, PilePicker, UserConfig, WatchdogPanel } from './modals'
import { DraftNode, NodeSquare, UserNode } from './cards'
import { isCompact, isMobile, MaybePortal, sheetGate } from '../mobile'

export interface OrgCanvasProps {
  tree: TreePayload
  op: OpFn
  slug: string
  toast: ToastFn
  mailEvt: MailEvent | null
  /** open the user's inbox, optionally jumped to a specific mail id */
  onInbox?: (jump?: string) => void
}

/** has this spring arrived? Both the spring loop (which snaps to the target on
 *  the frame this first holds) and the №25 follow ask it, and they have to ask
 *  the SAME question: the follow may only engage on a node that has come to
 *  rest, so a threshold that drifted between the two would let it engage on a
 *  node the loop still considers in flight — which is the freeze it exists to
 *  avoid. */
const atRest = (s: Spring, tgt: Pt): boolean =>
  Math.abs(tgt.x - s.x) <= 0.4 && Math.abs(tgt.y - s.y) <= 0.4
  && Math.abs(s.vx) <= 2 && Math.abs(s.vy) <= 2

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
  // ---- mobile wave (D-123/D-125) ----
  // the desk SHEET: explicit state, mobile-only. This deliberately does NOT
  // touch focusId — at compact the zoom clamps below Z_DESK so the camera-
  // derived focus never fires, and the two sources of truth never coexist
  // (the spec's §2-① haunting dissolves by partition, not by a third guard).
  const [sheetId, setSheetId] = useState<string | null>(null)
  const [sheetDogs, setSheetDogs] = useState(false)   // header dog list open
  const [hireOpen, setHireOpen] = useState(false)     // compact hire form
  const [, setVpTick] = useState(0)                   // re-render on resize
  const compact = isCompact()
  const compactRef = useRef(compact); compactRef.current = compact
  // the eye's unread-mail GLOW is gone (user ruling 2026-08-04: only agents
  // that need the user's answer glow; the header ask icon carries the rest).
  // The seen-stamp bookkeeping stays: the inbox count badge still uses it.
  const [, setInboxSeen] = useState(
    () => localStorage.getItem('orgtree-inbox-seen-' + slug) ?? '')
  const seats = tree.tiers ?? { haiku: 1, sonnet: 2, opus: 5, fable: 10, luna: 1, terra: 2, sol: 5 }
  // FR-15 M8: hire surfaces render from the provider payload — whether the
  // codex family is hireable HERE and NOW (CLI installed + signed in) or
  // still a disabled preview, with the payload's own reason as the tooltip.
  // Non-fatal like the accounts panel's fetch: absent payload degrades to
  // the disabled preview, never to hidden chips.
  const [codexProvider, setCodexProvider] = useState<ProviderInfo | null>(null)
  useEffect(() => {
    getProviders().then((p) => {
      const cx = p.providers.find((v) => v.id === 'openai')
      if (cx) setCodexProvider(cx)
    }).catch(() => {})
  }, [slug])
  const codexHire = codexProvider && {
    enabled: !!codexProvider.hire_enabled,
    reason: codexProvider.reason,
  }
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
  const hiddenMemo = useMemo(() => {     // piled-away id → its pile's front id
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
  const hidden = hiddenMemo
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
    // FR-18: watchdogs fan out BELOW their owner like a miniature subtree,
    // in the corridor between the agent and its reports (user revision
    // 2026-08-14 — the old left-side column crossed into an adjacent card
    // by 14px). Rows of 3 centered on the owner: a full row is 162px
    // against the 186px sibling pitch, so it never reaches a neighboring
    // card and clears even a maxed-out sibling's own chips by 24px. Only
    // dogs 7–8 open a third row, which can brush the children's row 200px
    // down — the 8-per-agent cap keeps that the rare case. Having a target
    // position is ALL a spark needs, so launchSpark(dog → owner) works
    // with zero animation code.
    {
      const byOwner: Record<string, number> = {}
      for (const w of tree.watchdogs ?? [])
        if (t.has(w.owner)) byOwner[w.owner] = (byOwner[w.owner] ?? 0) + 1
      const seen: Record<string, number> = {}
      const PER_ROW = 3, GX = 6, GY = 4
      for (const w of tree.watchdogs ?? []) {
        if (!t.has(w.owner)) continue
        const p = t.get(w.owner)!
        const i = seen[w.owner] ?? 0
        seen[w.owner] = i + 1
        const row = Math.floor(i / PER_ROW), col = i % PER_ROW
        const inRow = Math.min(PER_ROW, byOwner[w.owner]! - row * PER_ROW)
        const rowW = inRow * DOG_W + (inRow - 1) * GX
        t.set('dog:' + w.id, {
          x: p.x + NODE_W / 2 - rowW / 2 + col * (DOG_W + GX),
          y: p.y + NODE_H + 8 + row * (DOG_H + GY),
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
  // an in-flight background pan. sx/sy is where the finger went down and ox/oy
  // the camera at that moment; the pan then writes the camera ABSOLUTELY, as
  // ox + (clientX - sx). lx/ly track where the pointer is NOW, which is what
  // lets any OTHER camera writer re-base ox/oy onto the camera it just wrote
  // (see rebasePan) instead of leaving this origin stale — a stale origin
  // re-applied at a new zoom is what put the whole world off-screen.
  const panRef = useRef<{
    sx: number; sy: number; lx: number; ly: number
    ox: number; oy: number; moved: boolean } | null>(null)
  const springs = useRef(new Map<string, Spring>())
  // id → {x,y,at}: a node that should MATERIALIZE at a specific spot (a hire
  // replacing its draft card) instead of gliding over from its parent. Lives
  // outside springs because the reaper below deletes any spring whose id the
  // layout doesn't know yet — and the hire response lands frames before the
  // refreshed tree does
  const seedRef = useRef(new Map<string, { x: number; y: number; at: number }>())
  // user feature 2026-08-26: initializing an agent opens ITS desk. The id of a
  // hire made HERE (this browser, from the draft form) that is still waiting
  // for its card to exist and settle; the spring tick below does the opening.
  // Same 10s stale bound as seedRef, and for the same reason — the hire
  // response lands frames before the tree that gives the node a position, and
  // a hire whose node never arrives must not strand a camera jump.
  const hireDeskRef = useRef<{ id: string; at: number } | null>(null)
  const targetRef = useRef(target); targetRef.current = target
  const mapRef = useRef(map); mapRef.current = map
  const nodeDrag = useRef<{
    id: string; sx: number; sy: number
    bases: Map<string, Pt>; moved: boolean
  } | null>(null)     // {id, sx, sy, ox, oy, moved}
  // mobile pointer bookkeeping (spec §2-⑥): a real per-pointerId map — the
  // desktop path keeps its single panRef untouched. Two pointers = pinch;
  // during a pinch panRef holds a moved:true sentinel so the spring-follow
  // and the tap path both yield (one guard vocabulary, not three).
  const pointersRef = useRef(new Map<number, Pt>())
  const pinchRef = useRef<{ d0: number; z0: number } | null>(null)
  const tapRef = useRef<{ x: number; y: number; t: number; target: Element | null } | null>(null)
  const hiddenRef = useRef(hidden); hiddenRef.current = hidden

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
    // compact map: sparks re-render the whole canvas at 60fps for 420ms per
    // mail — the worst paint on a mobile GPU for pure decoration (§5.1 perf)
    if (compactRef.current) return
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
    // ⚠ presence in `m` is NOT a position (neoja org, 2026-08-12 — a crash,
    // not a cosmetic gap). The seg builders assert `posOf(id)!` and the
    // contract at the top of this block says every caller pre-checks BOTH
    // endpoints; this caller pre-checked the node map instead. A node that
    // the backend returns but `layout` never places — a live lineage bearer
    // whose successor is archived is the case that bit, since bearers are
    // positioned only relative to a positioned successor — then reached
    // `b.x` on undefined and took the WHOLE canvas down, deterministically,
    // on every spark to or from it. Refreshing could not help.
    //
    // The guard is on the ids actually used, not just the endpoints: the
    // tree path walks ancestors and the sibling path walks a row, and any
    // of those may be unplaced for the same reason. An unplaceable spark is
    // simply not drawn — the animation is decoration, and no decoration is
    // worth a blank canvas.
    const placed = (id: string) => posOf(id) !== undefined
    if (!placed(a) || !placed(b)) return
    const segs: (Seg & { rev: boolean })[] = []
    const aud = audSetRef.current
    if (aud.has(a + '→' + b) || aud.has(b + '→' + a)) {
      const [g, e] = aud.has(a + '→' + b) ? [a, b] : [b, a]
      segs.push({ ...audSeg(g, e), rev: g !== a })
    } else if (a !== USER && b !== USER && m.get(a)?.parent === m.get(b)?.parent) {
      const sibs = (m.get(m.get(a)!.parent!)?.children ?? []).map((c) => c.id)
        .filter((k) => m.has(k) && k !== DRAFT && placed(k))
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
      if (!ca.every(placed) || !cb.every(placed)) return
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
        if (!atRest(s, tgt)) active = true
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
      //
      // ENGAGING ONLY AT REST. Cancelling the spring delta holds the node at a
      // FIXED SCREEN OFFSET — whatever offset it had on the frame the follow
      // engaged. That is the whole point while the layout re-anchors under a
      // settled node, and it is a trap the moment the follow engages on a node
      // still in flight: the distance the spring had left to travel is exactly
      // the distance the node is off-centre, and cancelling that motion freezes
      // it there for good. The card never arrives.
      //
      // The reachable case is a camera animation, because it suppresses the
      // follow while it runs and hands it back the instant it lands: centerOn
      // aims at the node's layout TARGET, so if the spring is still short of
      // that target when the glide ends, the follow engages on precisely the
      // gap and pins it. A fresh hire is the common way in — the card's birth
      // spring is still gliding in from the draft's seed.
      //
      // So gate ENGAGEMENT on the node having arrived. Once engaged it stays
      // engaged and rides every later disturbance exactly as before, which is
      // what keeps №25 intact; a node that is still travelling is simply left
      // alone to finish, and the camera holds still while it lands.
      const fid = focusRef.current
      if (fid && !animBusyRef.current && !panRef.current) {
        const s = springs.current.get(fid)
        const tgt = targetRef.current.get(fid)
        if (s && tgt) {
          const prev = followRef.current
          if (prev && prev.id === fid) {
            const dx = s.x - prev.x, dy = s.y - prev.y
            if (dx || dy) {
              const z = viewRef.current.z
              setView((v) => ({ ...v, x: v.x - dx * z, y: v.y - dy * z }))
            }
            followRef.current = { id: fid, x: s.x, y: s.y }
          } else if (atRest(s, tgt)) {
            followRef.current = { id: fid, x: s.x, y: s.y }
          }
        }
      } else {
        followRef.current = null
      }
      // a fresh hire opens its own desk (user feature 2026-08-26). The hire
      // response carries only an id: the card does not exist until the
      // refreshed tree lands, and its birth spring then glides in from the
      // draft's seed. BOTH have to finish before the camera moves —
      //  · no layout entry yet, and there is nothing to centre on;
      //  · a card still travelling is a card the camera would centre on where
      //    it is going to be, not where it is.
      // ⚠ The second half is now BELT-AND-BRACES, and deliberately kept. It
      // was load-bearing when written: the follow above froze whatever screen
      // offset it engaged at, so centring mid-glide left the card off-centre
      // permanently. `6ad71b3` fixed that at the source — the follow only
      // ENGAGES on a node that has arrived — so the freeze can no longer
      // happen here even without this wait (its author verified that by
      // removing this condition against the fix: green). Kept anyway, for one
      // reason worth more than the frame it costs: it makes this feature
      // correct on its own terms rather than by borrowing a guarantee from a
      // gate three blocks up that nothing here would notice losing. If you
      // remove it, `deskinit.test.tsx` §5 is the check that should fail.
      // Yields to a live gesture exactly as that follow does: a drag, a pinch
      // or another camera animation owns the view, and the hire waits a frame.
      const hd = hireDeskRef.current
      if (hd) {
        // (stamped and compared on the SAME clock — `performance.now()`, not
        // the rAF timestamp `t`. The two share an origin in a browser, so
        // either reads correctly there; only one of them does under a rig
        // whose rAF hands out a mocked `Date.now()`, and a bound that expires
        // instantly under test is a feature no test can reach.)
        if (performance.now() - hd.at > 10000) hireDeskRef.current = null
        else if (sheetGate()) {
          // the sheet IS the desk here (D-123/D-125) and needs no camera —
          // only a node the tree already knows about
          if (mapRef.current.has(hd.id)) {
            hireDeskRef.current = null
            setSheetId(hd.id)
          }
        } else if (!animBusyRef.current && !panRef.current) {
          // (the spring loop at the top of this same tick has already created
          // a spring for every laid-out id, so a target with no spring means
          // the card is not real yet — wait, don't centre on a phantom)
          // `atRest`, not a threshold of this block's own: arrival is defined
          // once, next to the loop that snaps on it, and the follow reads the
          // same predicate. The hand-rolled `|Δpos| < 1` that stood here was a
          // second spelling of the same idea AND a weaker one — position-only,
          // so it admitted a card travelling at speed through a near-target
          // frame, which is the one case where "settled" was most wrong.
          const tp = targetRef.current.get(hd.id)
          const sp = springs.current.get(hd.id)
          if (tp && sp && atRest(sp, tp)) {
            hireDeskRef.current = null
            centerRef.current?.(hd.id)
          }
        }
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

  // THE CAMERA INVARIANT (user bug 2026-08-26 — "the drag location is updated
  // to a dramatically far away location, making all panels invisible").
  //
  // A background pan does not accumulate deltas; it re-derives the camera from
  // the origin captured at pointerdown, `x = ox + (clientX - sx)`. That makes
  // the pan authoritative over x/y for as long as a finger is down — so ANY
  // other writer of the camera must leave `ox/oy` agreeing with what it wrote,
  // or the very next pointermove silently reverts it. Reverting an x/y that
  // was computed for one zoom level back onto a different zoom level is not a
  // small jitter: the correct x for a world point is `mx - wx*z`, and wx runs
  // into the thousands (the eye sits at world x=6000), so a single wheel notch
  // mid-drag threw the world tens of thousands of px off-screen.
  //
  // Two writers are subject to this: the wheel (below) and animateTo (here).
  // pointerdown already cancels a running animation, so the animation case is
  // only reachable when a glide STARTS during a drag — which centerOn's buried
  // pile-member path does, by deferring itself two frames.
  //
  // Call this after every camera write, with the view actually written. The
  // pan continues from there, still carrying the delta the user has already
  // travelled (lx/ly - sx/sy).
  const rebasePan = useCallback((nv: View) => {
    const d = panRef.current
    if (!d) return
    d.ox = nv.x - (d.lx - d.sx)
    d.oy = nv.y - (d.ly - d.sy)
  }, [])

  const animateTo = useCallback((to: View, ms = 460) => {
    cancelAnimationFrame(animRef.current!)
    animBusyRef.current = true
    const from = { ...viewRef.current }
    const t0 = performance.now()
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / ms), e = ease(k)
      const nv = {
        x: from.x + (to.x - from.x) * e,
        y: from.y + (to.y - from.y) * e,
        z: from.z + (to.z - from.z) * e,
      }
      // the glide owns the camera frame by frame, so the re-base is per frame
      // too — and the ref write keeps a pointerdown landing mid-glide from
      // snapshotting a camera one commit out of date
      rebasePan(nv)
      viewRef.current = nv
      setView(nv)
      if (k < 1) animRef.current = requestAnimationFrame(step)
      else animBusyRef.current = false
    }
    animRef.current = requestAnimationFrame(step)
  }, [rebasePan])

  // the HUD ± buttons zoom about the SCREEN CENTER — changing z with x/y
  // held fixed anchors the world origin instead, which (with the eye parked
  // at world x=6000) read as a violent sideways pan, not a zoom
  const zoomStep = useCallback((factor: number) => {
    const vp = viewportRef.current?.getBoundingClientRect()
    const v = viewRef.current
    const lim = compactRef.current ? { min: 0.3, max: 1.6 } : { min: 0.24, max: Z_MAX }
    const z = Math.min(lim.max, Math.max(lim.min, v.z * factor))
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
  // mobile zoom range (spec §5.1): compact retires Z_DESK — the map never
  // reaches desk zoom, so the camera-derived focusId never fires and the
  // sheet is the only desk. Desktop keeps [0.24, Z_MAX] untouched.
  const zLim = () => compactRef.current
    ? { min: 0.3, max: 1.6 } : { min: 0.24, max: Z_MAX }
  // mobile spec §2-⑦: nothing re-ran on resize — on rotate the camera math
  // targeted the old rect forever. Mobile-only: re-render on any resize and
  // re-fit the camera once it settles (visualViewport covers the iOS soft
  // keyboard, where window.resize never fires).
  useEffect(() => {
    if (!isMobile) return
    let t: ReturnType<typeof setTimeout> | null = null
    const onR = () => {
      setVpTick((v) => v + 1)
      if (t) clearTimeout(t)
      t = setTimeout(() => { fitAll(false) }, 250)
    }
    window.addEventListener('resize', onR)
    window.visualViewport?.addEventListener('resize', onR)
    return () => {
      if (t) clearTimeout(t)
      window.removeEventListener('resize', onR)
      window.visualViewport?.removeEventListener('resize', onR)
    }
  }, [fitAll])
  // sub-panels reset whenever the sheet's subject changes or it closes
  useEffect(() => { setSheetDogs(false); setHireOpen(false) }, [sheetId])
  // the hardware/gesture BACK closes the sheet before leaving the org (spec
  // §5.2): opening pushes a history entry; back pops it and closes; closing
  // via ✕ consumes the entry so the next back doesn't no-op visibly
  useEffect(() => {
    if (!sheetId || !isMobile) return
    window.history.pushState({ sheet: sheetId }, '')
    const onPop = () => setSheetId(null)
    window.addEventListener('popstate', onPop)
    return () => {
      window.removeEventListener('popstate', onPop)
      const st: unknown = window.history.state
      if (st && typeof st === 'object' && (st as { sheet?: string }).sheet === sheetId) {
        window.history.back()
      }
    }
  }, [sheetId])
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
      // the agents tray is a real scroll container (.tray, overflow-y:auto) —
      // same carve-out as .overlay/.desk-over, or this native listener (fires
      // ahead of React, so a component-level stopPropagation can't reach it)
      // preventDefaults every wheel over it and the canvas zooms instead of
      // the list scrolling
      if ((e.target as Element | null)?.closest?.('.tray')) return
      e.preventDefault()
      cancelAnimationFrame(animRef.current!)
      animBusyRef.current = false
      const v = viewRef.current
      const factor = Math.exp(-e.deltaY * 0.0012)
      const z = Math.min(Z_MAX, Math.max(0.24, v.z * factor))
      const r = el.getBoundingClientRect()
      const mx = e.clientX - r.left, my = e.clientY - r.top
      const wx = (mx - v.x) / v.z, wy = (my - v.y) / v.z
      const nv = { x: mx - wx * z, y: my - wy * z, z }
      // zooming MID-DRAG: keep the pan's origin agreeing with the camera this
      // zoom just wrote, or the next pointermove reverts it — see the camera
      // invariant above rebasePan. This is the doorway the user's bug came in.
      rebasePan(nv)
      // synchronous ref coherence, same reason as the pinch path below: several
      // wheel events can land inside one commit, and each must zoom off the
      // LAST write rather than the last render or the notches cancel out
      viewRef.current = nv
      setView(nv)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [rebasePan])   // stable (useCallback []) — the listener still attaches once

  const toWorld = (e: { clientX: number; clientY: number }): Pt => {
    const r = viewportRef.current!.getBoundingClientRect()
    const v = viewRef.current
    return { x: (e.clientX - r.left - v.x) / v.z, y: (e.clientY - r.top - v.y) / v.z }
  }

  // background pan (+ mobile pinch/tap — desktop keeps the exact old path)
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    cancelAnimationFrame(animRef.current!)
    animBusyRef.current = false
    if (isMobile) {
      pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
      if (pointersRef.current.size === 2) {
        // second finger: any pan/tap in flight ABORTS (spec §2-⑥c) and the
        // gesture becomes a pinch. The sentinel panRef keeps the follow off.
        const [a, b] = [...pointersRef.current.values()] as [Pt, Pt]
        pinchRef.current = { d0: Math.hypot(a.x - b.x, a.y - b.y), z0: viewRef.current.z }
        panRef.current = { sx: 0, sy: 0, lx: 0, ly: 0, ox: 0, oy: 0, moved: true }
        tapRef.current = null
        e.currentTarget.setPointerCapture(e.pointerId)
        return
      }
      // remember the touch for tap arbitration on release — cards take no
      // capture on mobile, so a finger landing on one can still pan (§2-⑤)
      tapRef.current = { x: e.clientX, y: e.clientY, t: performance.now(),
        target: e.target as Element }
    }
    panRef.current = { sx: e.clientX, sy: e.clientY, lx: e.clientX, ly: e.clientY,
      ox: viewRef.current.x, oy: viewRef.current.y, moved: false }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (isMobile && pointersRef.current.has(e.pointerId)) {
      pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    }
    if (pinchRef.current) {
      if (pointersRef.current.size < 2) return
      const [a, b] = [...pointersRef.current.values()] as [Pt, Pt]
      const d = Math.hypot(a.x - b.x, a.y - b.y)
      const pin = pinchRef.current
      if (d <= 0 || pin.d0 <= 0) return
      const lim = zLim()
      const z = Math.min(lim.max, Math.max(lim.min, pin.z0 * (d / pin.d0)))
      const r = viewportRef.current?.getBoundingClientRect()
      if (!r) return
      const v = viewRef.current
      const mx = (a.x + b.x) / 2 - r.left, my = (a.y + b.y) / 2 - r.top
      const wx = (mx - v.x) / v.z, wy = (my - v.y) / v.z
      const nv = { x: mx - wx * z, y: my - wy * z, z }
      // synchronous ref coherence (§2-⑥b): several moves land per commit at
      // 120Hz, and each must compute from the LAST write, not the last render
      viewRef.current = nv
      setView(nv)
      return
    }
    const d = panRef.current
    if (!d) return
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy
    // where the pointer is NOW — rebasePan needs it to work out how much of
    // the gesture is already spent when some other writer moves the camera
    d.lx = e.clientX; d.ly = e.clientY
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
    if (d.moved) setView((v) => ({ ...v, x: d.ox + dx, y: d.oy + dy }))
  }
  const onPointerUp = (e?: React.PointerEvent<HTMLDivElement>) => {
    if (isMobile && e) {
      pointersRef.current.delete(e.pointerId)
      if (pinchRef.current) {
        if (pointersRef.current.size < 2) { pinchRef.current = null; panRef.current = null }
        return
      }
      // tap arbitration: a still finger that landed on a card opens it —
      // as the full-screen sheet under the D-123 gate, or the classic
      // zoom-to-desk glide on larger tablets. Interactive elements (buttons,
      // the mailbox tile, piles, chips) already handle their own clicks.
      const tap = tapRef.current
      tapRef.current = null
      if (tap && !panRef.current?.moved && e.type !== 'pointercancel'
          && performance.now() - tap.t < 600
          && !tap.target?.closest(
            'button, input, textarea, select, a, .wd-chip, .sq.orginbox, '
            + '.desk-over, .overlay, .cbar, .hsof, .doc-chips, .pile-stack')) {
        const w = toWorld({ clientX: tap.x, clientY: tap.y })
        let hit: string | null = null
        for (const [id] of targetRef.current) {
          if (id === DRAFT || id === INBOX || id.startsWith('dog:')) continue
          if (hiddenRef.current.has(id)) continue
          if (!mapRef.current.has(id)) continue
          const p = posOf(id)
          if (!p) continue
          const { w: cw, h: ch } = sizeOf(id)
          if (w.x >= p.x && w.x <= p.x + cw && w.y >= p.y && w.y <= p.y + ch) { hit = id; break }
        }
        if (hit === USER) {
          // compact hides the switchboard (§5.1) — the eye tap opens the
          // user inbox instead; tablets keep the zoom-in
          if (compactRef.current) onInbox?.()
          else centerOn(USER)
        } else if (hit && mapRef.current.get(hit)?.state !== 'draft') {
          if (sheetGate()) setSheetId(hit)
          else centerOn(hit)
        }
      }
    }
    panRef.current = null
  }

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
    // mobile: drag-to-reparent/reorder is desktop-only (spec §6 — no hover
    // preview, no unoccluded drop target, no cheap escape under a finger).
    // No capture and no stopPropagation: the press bubbles to the viewport,
    // which pans on move and opens the card on a still release.
    if (isMobile) return
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

  // edge JUMP CARDS (user spec 2026-08-17): at desk zoom the focused agent's
  // coworkers (live siblings) are usually off-screen — one small card per
  // side hugs the SCREEN edge at the neighbor's own screen elevation
  // (clamped into view) and glides the camera there on click. Only the NEXT
  // sibling over in each direction, and only while that sibling is genuinely
  // not clickable on-screen — a visible card needs no proxy.
  const edgeJumps = useMemo(() => {
    if (!focusId || compact) return []
    const me = map.get(focusId)
    if (!me || me.id === USER || me.isBearerOf) return []
    const vp = viewportRef.current?.getBoundingClientRect()
    if (!vp || !vp.width) return []
    const sibs = (map.get(me.parent ?? '')?.children ?? [])
      .map((c) => c.id)
      .filter((k) => k !== DRAFT && map.get(k)?.state === 'live'
        && !map.get(k)?.isBearerOf && !hidden.has(k) && target.has(k))
      .sort((p, q) => (target.get(p)?.x ?? 0) - (target.get(q)?.x ?? 0))
    const at = sibs.indexOf(focusId)
    if (at < 0) return []
    // the FOCUSED desk's own screen rect — every placement is measured against
    // this, not against the window (user bug 2026-08-26)
    const mp = posOf(focusId)
    if (!mp) return []
    const ms = sizeOf(focusId)
    const desk = {
      x0: mp.x * view.z + view.x, y0: mp.y * view.z + view.y,
      x1: (mp.x + ms.w) * view.z + view.x, y1: (mp.y + ms.h) * view.z + view.y,
    }
    const out: {
      n: CanvasNode; side: 'l' | 'r'; y: number; form: EJForm; band: boolean
    }[] = []
    for (const [k, side] of [[sibs[at - 1], 'l'], [sibs[at + 1], 'r']] as const) {
      if (!k) continue
      const n = map.get(k), p = posOf(k)
      if (!n || !p) continue
      const { w, h } = sizeOf(k)
      const x0 = p.x * view.z + view.x, y0 = p.y * view.z + view.y
      const x1 = (p.x + w) * view.z + view.x, y1 = (p.y + h) * view.z + view.y
      if (x1 > 0 && x0 < vp.width && y1 > 0 && y0 < vp.height) continue  // visible
      const put = edgeJumpPlacement(side, desk, vp, (y0 + y1) / 2)
      out.push({ n, side, y: put.y, form: put.form, band: put.band })
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId, map, target, hidden, view, compact])

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

  // at most ONE audience line per DRAWN pair (user bug 2026-08-24): buried
  // pile members sit at exactly their front card's position, so every holder
  // in a retired pile contributed a fully coincident stroke and the stacked
  // alpha made the pile glow. Collapse by the VISUAL pair — endpoints mapped
  // through the pile — keeping the front card's own grant when it is itself a
  // holder, so the surviving raw key is the one whose card is on screen; a
  // pair whose two ends collapse to the same card would be a line to itself
  // and draws nothing.
  const audLines = useMemo(() => {
    const keep = new Map<string, AudienceGrant>()
    for (const a of tree.audiences ?? []) {
      if (!map.has(a.grantor) || !map.has(a.grantee)) continue
      const vg = hidden.get(a.grantor) ?? a.grantor
      const ve = hidden.get(a.grantee) ?? a.grantee
      if (vg === ve) continue
      const vk = vg + '→' + ve
      if (!keep.has(vk) || (a.grantor === vg && a.grantee === ve)) keep.set(vk, a)
    }
    return [...keep.values()]
  }, [tree.audiences, map, hidden])

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
  // FR-25: insert a SUPERIOR — hired under the anchor's own superior (the
  // exact parent resolution spawnBeside uses); the hire op carries the anchor
  // and the server splices atomically (hire + ordinal pin + move, one save).
  // The draft meanwhile WRAPS the anchor in the preview tree (withDraftTree),
  // so the form already sits in the final shape and confirm causes no reflow.
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
         // FR-25: the anchor rides the hire op — the SERVER splices the new
         // node in as its superior atomically (one save, one broadcast), so
         // the old client-chained hire→move two-step (and its half-done
         // failure mode) is gone
         above: draft!.above?.anchor,
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
        // user feature 2026-08-26: initializing an agent walks you to its
        // desk. A hire is only half the gesture — the agent sits idle until
        // someone messages it, and the desk is where that message is typed;
        // the draft form used to leave you at overview zoom with the new card
        // among its siblings and no way in but a second click.
        // Deliberately armed HERE and not off the tree refresh: this fires for
        // hires made in THIS browser only. An agent hiring its own subordinate
        // arrives by the same broadcast, and yanking the camera across the org
        // for something the user did not initiate is a different feature.
        if (typeof born === 'string' && born) {
          hireDeskRef.current = { id: born, at: performance.now() }
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
        // (FR-25's splice needs nothing here anymore — it happened inside
        // the hire op itself, atomically; the broadcast refetch already
        // carries the final shape, which the draft was previewing in place)
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

  // D-125 ②: at compact the watchdog chips leave the map; owners carry the
  // count as a dot and the sheet header lists them
  const dogsByOwner = useMemo(() => {
    const out: Record<string, number> = {}
    for (const w of tree.watchdogs ?? []) out[w.owner] = (out[w.owner] ?? 0) + 1
    return out
  }, [tree.watchdogs])

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
      + (tree.headless ? ' headless' : '')
      // api_fallback (user feature 2026-08-19): the office border goes red
      // while the org's own API key is the lane being billed. Whole-canvas,
      // because the fact is org-wide — the per-agent red below says which
      // turns are actually spending it.
      + (fallbackActive(tree) ? ' onfallback' : '')} ref={viewportRef}
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
                // dashed on BOTH sides of a draft: its own parent edge, and —
                // for an insert-superior draft, which wraps its anchor — the
                // edge down to the anchor it would adopt
                + (n.state === 'draft' || n.parent === DRAFT
                  ? ' draftedge' : '')} />
          })}
          {peerLinks.map(([l, r]) => (
            posOf(l) && posOf(r) &&
            <path key={'p' + l + r} d={segD(peerSeg(l, r))} className="edge peer" />
          ))}
          {(() => {
            const nowT = performance.now()
            const anim = audAnimRef.current
            const out: ReactNode[] = []
            const vpair = (g: string, e: string) =>
              (hidden.get(g) ?? g) + '→' + (hidden.get(e) ?? e)
            const drawn = new Set<string>()   // raw keys rendered this frame
            const drawnV = new Set<string>()  // visual pairs occupied by them
            for (const a of audLines) {
              if (!posOf(a.grantor) || !posOf(a.grantee)) continue
              const k = a.grantor + '→' + a.grantee
              drawn.add(k)
              drawnV.add(vpair(a.grantor, a.grantee))
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
              if (st.phase !== 'out') {
                // an 'in' whose line was collapsed into a pile's single
                // stroke (or filtered out) never renders, so the loop above
                // never reaches its delete — and one live entry keeps the
                // rAF loop repainting the whole canvas forever
                if (!drawn.has(k)) anim.delete(k)
                continue
              }
              const t = (nowT - st.t0) / AUD_DUR
              if (t >= 1 || !posOf(st.grantor) || !posOf(st.grantee)
                // a revoked grant whose visual pair a surviving pile-mate
                // still draws: retracting a stroke over the persistent line
                // is exactly the double-draw this collapse exists to prevent
                || drawnV.has(vpair(st.grantor, st.grantee))) {
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
              to their owner with a wire"); the spark rides it on each fire.
              Hidden at compact with the chips (D-125 ②). */}
          {!compact && (tree.watchdogs ?? []).map((w) => {
            const a = posOf('dog:' + w.id), b = posOf(w.owner)
            if (!a || !b) return null
            return <path key={'w' + w.id}
              d={`M ${a.x + DOG_W / 2} ${a.y + 4} L ${b.x + NODE_W / 2} ${b.y + NODE_H - 8}`}
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
            if (compact) {
              // §5.1: the switchboard is desktop-idea-shaped (N-up parallel
              // desks) — at compact the eye is a plain marker; tapping it
              // opens the user inbox (viewport tap arbitration above)
              return <div key={USER} className="sq user maplod eye-map"
                style={{ transform: `translate(${p.x}px, ${p.y}px)`,
                         width: USER_W, height: USER_H }}>
                <span className="map-name">you</span>
                {(() => {
                  const pip = attentionPip(tree)
                  return pip && <b className={'eye-count' + (pip.urgent ? ' asks' : '')}
                    title={pip.title}>{pip.count}</b>
                })()}
              </div>
            }
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
              codexHire={codexHire}
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
              /* the pip is DECIDED HERE and handed down (D-169): asks + urgent
                 mail outrank plain unread, and `asks_open` already covers
                 pending credit requests as well as open questions (adding
                 credit_requests too would double-count). One call, so the
                 eye, its switchboard and the map marker cannot disagree. */
              pip={attentionPip(tree)}
              onInbox={() => {
                const nw = tree.user_inbox_newest ?? new Date().toISOString()
                localStorage.setItem('orgtree-inbox-seen-' + slug, nw)
                setInboxSeen(nw)
                onInbox?.()
              }}
              onGear={() => setUserCfg(true)}
              onMailLink={openMail} onOpenDoc={setDocView}
              /* switchboard panel headers mirror the desk header identically
                 (user spec 2026-08-19): the gen badge and gear in each panel
                 open the same canvas-level lineage/config surfaces */
              onNodeLineage={setLineageId} onNodeConfig={setConfigId}
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
              seats={seats} codexHire={codexHire} map={map} op={op} slug={slug} toast={toast}
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
              onDragEnd={endNodeDrag} onDragCancel={abortNodeDrag}
              mapMode={compact} dogs={dogsByOwner[n.id] ?? 0} />
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
            panel). Not agents: no chrome beyond name + state glyph.
            D-125 ②: HIDDEN at compact — 7px names are illegible and 50×26
            untappable at any phone-fitting zoom; the owner card carries a
            count-dot and the sheet header carries the list. */}
        {!compact && (tree.watchdogs ?? []).map((w) => {
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
      {/* edge jump cards: the focused desk's off-screen coworkers, one per
          side at the neighbor's own elevation (pointerdown stopped — the
          pan pointer-capture would swallow the click, see above) */}
      {edgeJumps.map((e) => (
        <button key={e.n.id}
          className={'edge-jump ' + e.side + ' ej-' + e.form
            + (e.band ? ' ej-band' : '')
            + ((e.n.mail_pending ?? 0) > 0 ? ' ej-mail' : '')}
          style={{ top: e.y }} title={`jump to ${e.n.id}`}
          onPointerDown={(ev) => ev.stopPropagation()}
          onClick={() => centerOn(e.n.id)}>
          {e.side === 'l' && <ChevronLeftIcon fontSize="inherit" />}
          <span className={'tier t-' + e.n.tier}>{TIER_LETTER[e.n.tier!] ?? '?'}</span>
          <span className="ej-name">{e.n.id}</span>
          {e.n.busy && <AutorenewIcon fontSize="inherit" className="cc-spin" />}
          {(e.n.mail_pending ?? 0) > 0 && <b className="eye-count">{e.n.mail_pending}</b>}
          {e.side === 'r' && <ChevronRightIcon fontSize="inherit" />}
        </button>
      ))}
      {/* the agent TRAY (user spec): every agent — tier token, name, context
          wheel, working state — in the nodes' own visual language; a row
          click glides to that agent. FR-16 (2026-08-11): listed by HIERARCHY
          — each superior immediately followed by its subtree, indented per
          depth — not by canvas position */}
      <MaybePortal>
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
                  // mobile sheet gate: the tray is primary navigation at
                  // compact (§5.3) — a row opens the desk sheet directly
                  // (centerOn would glide past the compact zoom clamp)
                  if (sheetGate()) { setSheetId(n.id); setTrayOpen(false) }
                  else centerOn(n.id)
                }
                // №13: the status summary is TEXT here, not a tooltip — and a
                // finished status survives the next turn as prev_status (dim)
                const stat: (NodeStatus & { _stale?: boolean }) | null = n.last_status
                  ?? (n.prev_status ? { ...n.prev_status, _stale: true } : null)
                return (
                <div key={n.id} role="button" tabIndex={0}
                  className={'tray-row' + (n.state !== 'live' ? ' off' : '')
                    + (ghost ? ' ghost' : '')
                    + (n.tier && CODEX_TIERS.includes(n.tier) ? ' prov-openai' : '')}
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
                      est={n.occupancy_est} compactAt={tree.compact_at} />
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
      </MaybePortal>
      {/* every overlay rides MaybePortal (mobile wave §2-②): `.viewport`
          carries touch-action:none, and a scroller nested inside it can
          never scroll by touch — the portal moves the overlay out of that
          DOM subtree ON MOBILE ONLY; desktop renders exactly as before. */}
      {configId && map.get(configId) && (
        <MaybePortal><NodeConfig node={map.get(configId)!} map={map} tree={tree} slug={slug}
          op={op} toast={toast} codexProvider={codexProvider}
          close={() => setConfigId(null)} /></MaybePortal>
      )}
      {lineageId && map.get(lineageId) && (
        <MaybePortal><LineagePanel node={map.get(lineageId)!} op={op} slug={slug}
          close={() => setLineageId(null)} /></MaybePortal>
      )}
      {dogView && (tree.watchdogs ?? []).some((w) => w.id === dogView) && (
        <MaybePortal><WatchdogPanel slug={slug} toast={toast}
          dog={(tree.watchdogs ?? []).find((w) => w.id === dogView)!}
          close={() => setDogView(null)} /></MaybePortal>
      )}
      {docView && (
        <MaybePortal><DocReader slug={slug} docId={docView} toast={toast}
          close={() => setDocView(null)} /></MaybePortal>
      )}
      {userCfg && (
        <MaybePortal><UserConfig tree={tree} slug={slug} toast={toast}
          close={() => setUserCfg(false)} /></MaybePortal>
      )}
      {inboxId && map.get(inboxId) && (
        <MaybePortal><NodeInboxModal node={map.get(inboxId)!} slug={slug}
          jumpTo={nodeInboxJump}
          close={() => { setInboxId(null); setNodeInboxJump(null) }} /></MaybePortal>
      )}
      {pileOpen && piles.get(pileOpen) && (
        <MaybePortal><PilePicker pile={piles.get(pileOpen)!} map={map} op={op} toast={toast}
          onPick={(nid) => { setFront(pileOpen, nid); setPileOpen(null) }}
          close={() => setPileOpen(null)} /></MaybePortal>
      )}
      {oiOpen && (
        <MaybePortal><OrgInboxModal inbox={tree.org_inbox} net={tree.net} map={map} slug={slug} toast={toast}
          jumpTo={oiJump}
          close={() => {
            setOiOpen(false); setOiJump(null)
            // closing the panel acknowledges the whole log (same idiom as the
            // eye's glow: opening acknowledges attention)
            if (tree.org_inbox?.unread) orgInboxRead(slug).catch(() => {})
          }} /></MaybePortal>
      )}
      {/* ---- the desk SHEET (D-123, mobile only): full-screen, portaled,
          authored 1:1 — DeskChat's `bare` mode is exactly the unscaled desk
          body the sheet needs. Explicit state; the camera never moves. */}
      {sheetId && map.get(sheetId) && (() => {
        const n = map.get(sheetId)!
        const myDogs = (tree.watchdogs ?? []).filter((w) => w.owner === sheetId)
        return (
          <MaybePortal>
            <div className="mobsheet">
              <header className="mobsheet-head">
                <span className={'tier t-' + n.tier}>{TIER_LETTER[n.tier ?? ''] ?? '?'}</span>
                <b className="ms-name">{n.id}</b>
                {n.busy && <Activity act={n.activity} dotOnly />}
                {n.state !== 'live' && <span className="dim">{n.state}</span>}
                <span className="spacer" />
                {myDogs.length > 0 &&
                  <button className="ms-btn" onClick={() => setSheetDogs((v) => !v)}>
                    ◉ {myDogs.length}</button>}
                {n.state === 'live' && !tree.public &&
                  <button className="ms-btn" title="hire a report"
                    onClick={() => setHireOpen(true)}>＋</button>}
                <button className="ms-btn" title="inbox"
                  onClick={() => setInboxId(sheetId)}>✉</button>
                {!tree.public &&
                  <button className="ms-btn" title="permissions & settings"
                    onClick={() => setConfigId(sheetId)}>⚙</button>}
                <button className="ms-btn ms-close" onClick={() => setSheetId(null)}>✕</button>
              </header>
              {sheetDogs && myDogs.length > 0 && (
                <div className="ms-doglist">
                  {myDogs.map((w) => (
                    <button key={w.id}
                      onClick={() => { setDogView(w.id); setSheetDogs(false) }}>
                      <span className="wd-glyph">{w.state === 'armed' ? '◉'
                        : w.state === 'paused' ? '◫' : '✕'}</span>
                      {w.name} · {w.state}
                    </button>
                  ))}
                </div>
              )}
              <div className="mobsheet-body">
                <DeskChat bare node={n} map={map} op={op} slug={slug}
                  toast={toast} pub={!!tree.public} compactAt={tree.compact_at}
                  maxTop={tree.max_top_grant ?? 1000} pxc={pxPerCredit}
                  onMailLink={openMail} onOpenDoc={setDocView}
                  onLineage={() => setLineageId(sheetId)}
                  onConfig={() => setConfigId(sheetId)}
                  onJump={(id) => {
                    if (id !== USER && mapRef.current.has(id)) setSheetId(id)
                  }} />
              </div>
            </div>
          </MaybePortal>
        )
      })()}
      {hireOpen && sheetId && map.get(sheetId) && (
        <MaybePortal>
          <HireSheet anchor={map.get(sheetId)!} seats={seats} codexHire={codexHire}
            defaultGrant={!map.get(sheetId)!.parent ? (tree.default_top_grant ?? 50) : 0}
            onClose={() => setHireOpen(false)}
            onHire={(tier, name, grant, placement) => {
              const a = map.get(sheetId)!
              const parentOf = !a.parent || a.parent === USER ? null : a.parent
              const parent = placement === 'below' ? a.id : parentOf
              op({ op: 'hire', parent, tier, grant, name })
                .then((r) => {
                  const born = r?.node
                  if (typeof born === 'string' && born) {
                    // same follow-ups as the desktop chips: side = pin the
                    // promised ordering (best-effort, cosmetic); above = the
                    // FR-25 splice (loud on failure — it IS the point)
                    if (placement === 'left' || placement === 'right') {
                      void reorderNode(slug, born, placement === 'left'
                        ? { before: a.id } : { after: a.id }).catch(() => {})
                    }
                    if (placement === 'above') {
                      op({ op: 'move', node: a.id, new_parent: born })
                        .catch((e: Error) => toast([
                          `hired ${born}, but the splice failed: ${e.message}`]))
                    }
                    toast([`hired ${born}`])
                  }
                  setHireOpen(false)
                })
                .catch((e: Error) => toast([`hire failed: ${e.message}`]))
            }} />
        </MaybePortal>
      )}
    </div>
  )
}

/** compact hire form (D-125 ③): the four edge-gated chip sets depend on
 *  cursor-proximity tracking with no touch equivalent, so at compact hiring
 *  is a full-screen form — and it carries PLACEMENT, so the F-03 side-hire
 *  and FR-25 splice semantics survive: below (report), left/right (coworker
 *  ordering), above (new superior — the anchor moves under the hire). */
function HireSheet({ anchor, seats, codexHire, defaultGrant, onHire,
  onClose }: {
  anchor: CanvasNode
  seats: Record<string, number>
  codexHire?: { enabled: boolean; reason: string | null } | null
  defaultGrant: number
  onHire: (tier: string, name: string, grant: number,
    placement: 'below' | 'left' | 'right' | 'above') => void
  onClose: () => void
}) {
  const [tier, setTier] = useState('sonnet')
  const [name, setName] = useState('')
  const [grant, setGrant] = useState(defaultGrant)
  const [placement, setPlacement] =
    useState<'below' | 'left' | 'right' | 'above'>('below')
  const ok = /^[a-z][a-z0-9-]{1,29}$/.test(name.trim())
  return (
    <div className="overlay" onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings hire-sheet">
        <h3>hire{placement === 'below' ? ` under ${anchor.id}`
          : placement === 'above' ? ` above ${anchor.id}`
          : ` beside ${anchor.id}`}</h3>
        {/* each provider's tiers on their own row (user spec 2026-08-28) —
            the compact form's version of the canvas's mirrored rows */}
        <div className="field-label">model tier — Claude</div>
        <div className="hs-tiers">
          {TIERS.map((t) => (
            <button key={t} className={'hs-tier t-' + t + (tier === t ? ' on' : '')}
              onClick={() => setTier(t)}>
              <span className={'tier t-' + t}>{TIER_LETTER[t]}</span>
              {t} · seat {seats[t] ?? '?'}
            </button>
          ))}
        </div>
        <div className="field-label">
          {codexHire?.enabled ? 'Codex' : 'Codex — preview'}</div>
        <div className="hs-tiers">
          {CODEX_TIERS.map((t) => (
            <button key={t}
              className={'hs-tier t-' + t + (tier === t ? ' on' : '')}
              disabled={!codexHire?.enabled}
              title={codexHire?.enabled ? undefined
                : (codexHire?.reason ?? 'hiring is not enabled yet')}
              onClick={() => setTier(t)}>
              <span className={'tier t-' + t}>{CODEX_TIER_LETTER[t]}</span>
              {t} · seat {seats[t] ?? CODEX_TIER_SEAT[t]}
            </button>
          ))}
        </div>
        <div className="field-label">placement</div>
        <div className="hs-place">
          {([['below', 'report — under ' + anchor.id],
             ['left', 'coworker — before it'],
             ['right', 'coworker — after it'],
             ['above', 'superior — ' + anchor.id + ' moves under the hire']] as const)
            .map(([p, lbl]) => (
              <button key={p} className={'ask-row' + (placement === p ? ' on' : '')}
                onClick={() => setPlacement(p)}>
                <span className={'ask-dot' + (placement === p ? ' on' : '')} />
                <span className="ask-row-body">{lbl}</span>
              </button>
            ))}
        </div>
        <div className="field-label">name</div>
        <input value={name} placeholder="lowercase-slug"
          onChange={(e) => setName(e.target.value.toLowerCase())} />
        <div className="field-label">credit grant</div>
        <input type="number" min={0} value={grant}
          onChange={(e) => setGrant(Math.max(0, Math.round(Number(e.target.value) || 0)))} />
        <div className="row">
          <button className="primary" disabled={!ok}
            onClick={() => onHire(tier, name.trim(), grant, placement)}>hire</button>
          <button onClick={onClose}>cancel</button>
        </div>
      </div>
    </div>
  )
}
