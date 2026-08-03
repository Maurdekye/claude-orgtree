// convo.ts — ONE conversation model per node, owned outside React.
//
// Why this exists (user bug 2026-08-02: "the switchboard desk going out of sync
// with the individual agent desks"). A node's chat is rendered by up to two
// DeskChat instances — its own card and its switchboard panel — and each one
// used to keep a PRIVATE copy: its own fetch, its own live rows, its own
// draft/thinking buffers, its own busy-gated poller. Two independent models of
// one conversation diverge by construction; per-node event routing (22cc281)
// fixed which desk got an event, not the fact that each desk was accumulating
// its own answer. Whichever view happened to miss an event stayed wrong until
// it remounted.
//
// So the model moves here and the views become renderers of it. Both instances
// read the same object, so they cannot disagree — not "usually agree", cannot.
// This is the user's "store less, derive more" direction applied at the level
// where it pays: one store, one poller, one reconciliation, N views.
//
// It also costs LESS than what it replaces: one fetch per node instead of one
// per mounted view.

import { getChat } from './api'
import type { ChatPayload } from './types'
import type { LiveRow, PulseEvent, StreamEvent } from './canvas/shared'
import { useCallback, useSyncExternalStore } from 'react'

/** Only the newest CHAT_WINDOW rows are fetched and rendered; scrolling to the
 *  top pages another window in. The cost that bites is DOM size — every row
 *  carries markdown and tool chips — so this is deliberately small. */
export const CHAT_WINDOW = 120
const MAX_WINDOW = 1000        // the API's own cap
const BUSY_POLL_MS = 2500      // heartbeat while the payload says busy
const IDLE_POLL_MS = 7000      // heartbeat otherwise — slower, never off
const NUDGE_MS = 200           // burst coalescing for the post-event refetch

export interface Convo {
  chat: ChatPayload | null
  /** the server's live tail (chat.live), mirrored here so a render need not
   *  reach through a nullable payload. NOT accumulated locally — P2: the
   *  server owns it, sweeps it against the transcript, and hands back the
   *  survivors, so every view shows the same list by construction. */
  live: LiveRow[]
  /** optimistic sent-message ghosts, until the server copy lands */
  pending: string[]
  draft: string
  thinking: string
  /** elapsed seconds while thinking is in progress; null = not thinking */
  thinkSecs: number | null
  win: number
  loadingOlder: boolean
  /** a fetch has completed at least once — the first load always sticks */
  loaded: boolean
}

const BLANK: Convo = {
  chat: null, live: [], pending: [], draft: '', thinking: '',
  thinkSecs: null, win: CHAT_WINDOW, loadingOlder: false, loaded: false,
}

interface Entry {
  s: Convo
  subs: Set<() => void>
  thinkT0: number          // 0 = not thinking (the single is-thinking truth)
  clock: ReturnType<typeof setInterval> | null
  /** The live scaffolding is SUPERSEDED but not yet replaced. Set when the
   *  durable event arrives, cleared by the fetch that carries the durable row
   *  — never before it, which is the whole point (see ingestStream). */
  staleDraft: boolean
  staleThink: boolean
  /** when the scaffolding was superseded. A fetch may only retire it if that
   *  fetch STARTED after this moment — one already in flight when the event
   *  fired returns a payload from before the durable row existed, and honouring
   *  it would reopen the very gap this closes. */
  staleAt: number
  /** debounce for the post-event refetch */
  nudge: ReturnType<typeof setTimeout> | null
  poll: ReturnType<typeof setTimeout> | null
  inflight: boolean
  /** when the last fetch COMPLETED — a live row may only expire after a fetch
   *  has had the chance to cover it (otherwise the timer races the fetch and
   *  the row vanishes into a gap) */
  fetchedAt: number
}

const M = new Map<string, Entry>()
const key = (slug: string, nid: string) => `${slug}․${nid}`

function entry(k: string): Entry {
  let e = M.get(k)
  if (!e) {
    e = { s: BLANK, subs: new Set(), thinkT0: 0, clock: null, nudge: null,
          staleDraft: false, staleThink: false, staleAt: 0,
          poll: null, inflight: false, fetchedAt: 0 }
    M.set(k, e)
  }
  return e
}

/** Patch and notify. The snapshot identity changes ONLY on a real change, which
 *  is what useSyncExternalStore requires to avoid an infinite render loop. */
function patch(k: string, p: Partial<Convo>): void {
  const e = entry(k)
  let changed = false
  for (const [f, v] of Object.entries(p)) {
    if (e.s[f as keyof Convo] !== v) { changed = true; break }
  }
  if (!changed) return
  e.s = { ...e.s, ...p }
  e.subs.forEach((cb) => cb())
}

// ------------------------------------------------------------------ the hook
export function useConvo(slug: string, nid: string): Convo {
  const k = key(slug, nid)
  const sub = useCallback((cb: () => void) => {
    const e = entry(k)
    e.subs.add(cb)
    beat(k, slug, nid)          // someone is watching -> keep it fresh
    return () => {
      e.subs.delete(cb)
      if (!e.subs.size && e.poll) { clearTimeout(e.poll); e.poll = null }
    }
  }, [k, slug, nid])
  const snap = useCallback(() => entry(k).s, [k])
  return useSyncExternalStore(sub, snap, snap)
}

// -------------------------------------------------------------------- fetch
/** Refresh a node's transcript. Concurrent calls collapse into the in-flight
 *  one — several views, several triggers, ONE request. */
export function refreshConvo(slug: string, nid: string,
                             opts: { force?: boolean } = {}): Promise<void> {
  const k = key(slug, nid)
  const e = entry(k)
  if (e.inflight && !opts.force) return Promise.resolve()
  e.inflight = true
  const startedAt = Date.now()
  return getChat(slug, nid, e.s.win).then((c) => {
    e.inflight = false
    e.fetchedAt = Date.now()
    // a pending ghost graduates once the transcript contains it — by
    // CONTAINMENT, not equality: the turn text is a mail envelope by then
    const pending = e.s.pending.filter((x) => !c.messages.slice(-20)
      .some((m) => m.role === 'user' && (m.text || '').includes(x)))
    // superseded scaffolding retires HERE, with its replacement in hand — one
    // atomic patch, so the draft never blinks out ahead of the durable row
    const retire: Partial<Convo> = {}
    const fresh = startedAt >= e.staleAt      // this fetch can SEE the new row
    if (fresh && e.staleDraft) { retire.draft = ''; e.staleDraft = false }
    if (fresh && e.staleThink) {
      retire.thinking = ''
      retire.thinkSecs = null
      e.staleThink = false
    }
    patch(k, { chat: c, loaded: true, loadingOlder: false, pending,
               live: (c.live ?? []) as LiveRow[], ...retire })
  }).catch(() => {
    e.inflight = false
    patch(k, { loadingOlder: false })
  })
}

// (the live/durable reconciliation that used to live here is gone — it moved
// into supervisor._sweep_live, which can see BOTH the live rows and the
// transcript. This file no longer decides what to retire; it renders what the
// server retired.)

export function loadOlder(slug: string, nid: string): boolean {
  const k = key(slug, nid)
  const e = entry(k)
  if (e.s.loadingOlder || e.s.win >= MAX_WINDOW) return false
  patch(k, { loadingOlder: true, win: Math.min(MAX_WINDOW, e.s.win + CHAT_WINDOW) })
  void refreshConvo(slug, nid, { force: true })
  return true
}

export function addPending(slug: string, nid: string, text: string): void {
  const k = key(slug, nid)
  patch(k, { pending: [...entry(k).s.pending, text] })
}

export function dropPending(slug: string, nid: string, text: string): void {
  const k = key(slug, nid)
  patch(k, { pending: entry(k).s.pending.filter((x) => x !== text) })
}

/** the composer's optimistic "it is working now", corrected by the next fetch */
export function markBusy(slug: string, nid: string): void {
  const k = key(slug, nid)
  const c = entry(k).s.chat
  if (c && !c.busy) patch(k, { chat: { ...c, busy: true } })
}

// ------------------------------------------------------------------ ingest
/** Called ONCE per websocket frame, at the app level — not per mounted view.
 *
 *  Since P2 this does NOT assemble a conversation. Anything a view must still
 *  see after the moment passes is a server-owned live row, so an event of that
 *  kind just schedules a (debounced) refetch. What stays here is the
 *  sub-second scaffolding that would be stale before any fetch returned: the
 *  token-by-token draft, and the thinking clock. */
export function ingestStream(slug: string, ev: StreamEvent): void {
  const k = key(slug, ev.node)
  const e = entry(k)
  if (ev.kind === 'thinking_start') {
    // the block OPENED — the only marker that survives sealing, and the only
    // one that is early (a sealed think's deltas can all arrive at the end,
    // which would start the clock as it stops)
    e.thinkT0 = Date.now()
    startClock(k, e)
    e.staleThink = false
    patch(k, { thinking: '', thinkSecs: 0 })
    return
  }
  if (ev.kind === 'thinking') {
    if (!e.thinkT0) { e.thinkT0 = Date.now(); startClock(k, e); patch(k, { thinkSecs: 0 }) }
    // a fresh thought must not continue a superseded one
    const base = e.staleThink ? '' : e.s.thinking
    e.staleThink = false
    patch(k, { thinking: (base + ev.text).slice(-2000) })
    return
  }
  if (ev.kind === 'delta') {
    const base = e.staleDraft ? '' : entry(k).s.draft
    e.staleDraft = false
    patch(k, { draft: (base + ev.text).slice(-12000) })
    return
  }
  // A durable row landed (text / tool / sticky output): the thinking phase is
  // over and the draft has been superseded by the real message.
  //
  // SUPERSEDED IS NOT REPLACED. These used to be blanked right here, but the
  // row that replaces them only arrives with the refetch `nudge()` schedules
  // below — 200 ms of debounce plus a round-trip later. In between, the grey
  // live text vanished and nothing stood in its place: the message, tool call
  // or response simply went missing for a moment and then came back (user bug
  // 2026-08-03). So mark them stale and let the FETCH clear them, in the same
  // patch that installs the payload carrying their replacement — atomic, so
  // there is neither a gap nor a frame where both are on screen.
  //
  // The clock stops immediately, because that is a fact about the world rather
  // than something being rendered: it is no longer thinking.
  e.thinkT0 = 0
  stopClock(e)
  e.staleThink = true
  e.staleAt = Date.now()
  if (ev.kind === 'text') e.staleDraft = true
  if (ev.kind === 'steered') {
    patch(k, { pending: e.s.pending.filter((x) => !ev.text.includes(x)) })
  }
  nudge(slug, ev.node)
}

/** Coalesce a burst of events into ONE refetch. A turn emits several rows in
 *  quick succession and every one of them wants the same payload. */
function nudge(slug: string, nid: string): void {
  const e = entry(key(slug, nid))
  if (e.nudge) return
  e.nudge = setTimeout(() => {
    e.nudge = null
    void refreshConvo(slug, nid, { force: true })
  }, NUDGE_MS)
}

export function ingestPulse(slug: string, ev: PulseEvent): void {
  const k = key(slug, ev.node)
  const e = entry(k)
  if (ev.event === 'turn_done') {
    // the live tail is cleared server-side (sticky rows survive there); here
    // only the sub-second scaffolding needs resetting — and by the same rule as
    // ingestStream, it retires on the FETCH below rather than right now, or the
    // final message blinks out between the turn ending and the payload landing
    e.thinkT0 = 0
    stopClock(e)
    e.staleDraft = true
    e.staleThink = true
    e.staleAt = Date.now()
  }
  void refreshConvo(slug, ev.node, { force: true })
}

// ------------------------------------------------------------------- timers
function startClock(k: string, e: Entry): void {
  if (e.clock) return
  e.clock = setInterval(() => {
    if (!e.thinkT0) return stopClock(e)
    patch(k, { thinkSecs: Math.round((Date.now() - e.thinkT0) / 1000) })
  }, 1000)
}

function stopClock(e: Entry): void {
  if (e.clock) { clearInterval(e.clock); e.clock = null }
}

/** THE HEARTBEAT. If anything is watching this node, refetch it — full stop.
 *
 *  This used to be `pollWhileBusy(..., !!chat?.busy)`, and that was a
 *  bootstrap trap: the refresh loop was gated on a field that ARRIVES IN THE
 *  PAYLOAD THE LOOP FETCHES. Open a desk whose last payload said `busy:false`
 *  while the node was in fact working — a turn that began during a websocket
 *  gap, an event that never arrived, a view mounted at the wrong moment — and
 *  the poll never starts, so nothing can ever correct the belief that made it
 *  not start. The view then sits frozen until it is unmounted (zoom out) or
 *  the page is reloaded, which is exactly what the user reported, twice.
 *
 *  Liveness must never depend on state that could itself be stale. "Is anyone
 *  looking at this node" is known LOCALLY and cannot be wrong, so that is the
 *  gate. Cadence still adapts — fast while the payload says busy, slow
 *  otherwise — but the difference is only how often, never whether. */
function beat(k: string, slug: string, nid: string): void {
  const e = entry(k)
  if (e.poll || !e.subs.size) return
  const tick = () => {
    const cur = entry(k)
    if (!cur.subs.size) { cur.poll = null; return }
    void refreshConvo(slug, nid)
    cur.poll = setTimeout(tick, cur.s.chat?.busy ? BUSY_POLL_MS : IDLE_POLL_MS)
  }
  e.poll = setTimeout(tick, BUSY_POLL_MS)
}

/** Drop the CONTENT for every node — used when the viewer switches orgs, so a
 *  stale conversation can never be shown under a different tree.
 *
 *  ⚠ It resets entries IN PLACE and deliberately does not `M.clear()`. A
 *  mounted view has already handed its re-render callback to a specific Entry
 *  object; discarding the map would leave that callback attached to an
 *  orphan, and every later patch would notify a set nobody is listening to —
 *  a permanently deaf view that looks exactly like the staleness this whole
 *  file exists to prevent. Identity of the Entry is load-bearing. */
export function resetConvos(): void {
  M.forEach((e) => {
    stopClock(e)
    if (e.poll) { clearTimeout(e.poll); e.poll = null }
    if (e.nudge) { clearTimeout(e.nudge); e.nudge = null }
    e.thinkT0 = 0
    e.staleDraft = false
    e.staleThink = false
    e.staleAt = 0
    e.inflight = false
    e.fetchedAt = 0
    e.s = BLANK
    e.subs.forEach((cb) => cb())
  })
}
