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
export const MAX_WINDOW = 1000        // the API's own cap
const BUSY_POLL_MS = 2500      // heartbeat while the payload says busy
const IDLE_POLL_MS = 7000      // heartbeat otherwise — slower, never off
const NUDGE_MS = 200           // burst coalescing for the post-event refetch

/** An optimistic ghost, plus HOW MANY copies of its text the server already
 *  showed when it was created.
 *
 *  Graduation used to be "does any of the last 20 user messages contain this
 *  text" — with no regard for WHEN. Send "continue" twice and the second ghost
 *  matched the FIRST message and vanished in 30 ms, so the preview never
 *  appeared (user report 2026-08-03, measured: 2.92 s of ghost on the first
 *  send, 0.03 s on the second). Short repeated messages are the common case,
 *  not an edge one.
 *
 *  Counting rather than timestamping is deliberate: it needs no clock
 *  comparison between browser and server, so no skew can retire a ghost early.
 *  A stale baseline can only over-count what was already there, which keeps a
 *  ghost a moment longer — erring toward showing the message, which is the
 *  direction this whole class of bug wants. */
export interface PendingGhost {
  /** client-minted identity — the render key. Ghosts retire from the MIDDLE
   *  of the list (dropOne), where an index key renames every ghost below the
   *  one that left. */
  id: number
  text: string
  seen: number
  /** the newest transcript `seq` the payload showed when this ghost was made.
   *  It is what makes the ghost's retirement REACHABLE: see `scrolledPast`.
   *  UNKNOWN_SEQ when no payload had loaded yet — see addPending. */
  seq0: number
}

/** A ghost made before the first payload loaded has no seq baseline. It used
 *  to get `-1` ("my message will be row 0") — but the composer is enabled
 *  before the first load, and on a node whose transcript is already long the
 *  first payload's window starts far past row 0, so `scrolledPast` retired the
 *  ghost on a payload that might not yet carry the mail (POST racing GET): the
 *  message was on screen NOWHERE for a poll interval. Unknown must mean
 *  "cannot scroll past me yet"; the first payload re-baselines it
 *  (see refreshConvo). */
const UNKNOWN_SEQ = Number.MAX_SAFE_INTEGER

/** ghost identity mint — see PendingGhost.id */
let GHOST_ID = 0

/** Has the fetched window moved entirely PAST where this ghost's message would
 *  sit?
 *
 *  The count baseline can otherwise become unreachable, which is the fourth
 *  costume of this bug (D-53 lead 3 → D-55 flagged it → D-57 ④ raised
 *  COPIES_WINDOW to 200 against a measured maximum burial of 138 rows). The
 *  raise did nothing, because `read_chat` only ever returns CHAT_WINDOW = 120
 *  rows: the newest-200 slice IS the whole payload, so the effective window
 *  stayed at 120 — under the measured maximum. Bury the message deeper than
 *  that and the server can never show a copy again; the ghost sits at the
 *  bottom of the desk for the rest of the session, presenting a message that
 *  was answered ten minutes ago as though it were still queued.
 *
 *  The test is evidence, not a guess: if the OLDEST row now in the window is
 *  newer than the NEWEST row that existed when the message was sent, the
 *  message cannot be in the window — it is behind it. The only other place it
 *  could be is the mailbox, and `serverCopies` counts that, so a message still
 *  waiting is still covered. (It also cannot fire early: the window has to
 *  turn over completely — 120 rows — and the CLI's echo of the message is the
 *  FIRST of those rows, so the transcript has long since taken over.) */
function scrolledPast(c: ChatPayload, g: PendingGhost): boolean {
  const oldest = c.messages[0]?.seq
  // `seq0 + 1` is the earliest row the message could occupy, so this is the
  // strict form: the window must start after the message's own place, not
  // merely after the last row that preceded it.
  return oldest != null && oldest > g.seq0 + 1
}

export interface Convo {
  chat: ChatPayload | null
  /** the server's live tail (chat.live), mirrored here so a render need not
   *  reach through a nullable payload. NOT accumulated locally — P2: the
   *  server owns it, sweeps it against the transcript, and hands back the
   *  survivors, so every view shows the same list by construction. */
  live: LiveRow[]
  /** optimistic sent-message ghosts, until the server copy lands */
  pending: PendingGhost[]
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
  /** when a delta/thinking last extended the scaffolding. A payload may only
   *  declare that scaffolding stale if the request went out AFTER it — one
   *  already in flight describes a world from before the stream started. */
  streamAt: number
  /** debounce for the post-event refetch */
  nudge: ReturnType<typeof setTimeout> | null
  poll: ReturnType<typeof setTimeout> | null
  inflight: boolean
  /** when the last fetch COMPLETED — a live row may only expire after a fetch
   *  has had the chance to cover it (otherwise the timer races the fetch and
   *  the row vanishes into a gap) */
  fetchedAt: number
  /** `startedAt` of the request whose payload is currently installed. A forced
   *  fetch can overlap the heartbeat's, and whichever RESPONSE landed last used
   *  to win — a slow stale payload regressing `busy`/`live`/the transcript until
   *  the next tick. Now the payload from the latest-STARTED request wins, and a
   *  late straggler from an earlier one is discarded. */
  installed: number
  /** an event wanted a refetch while nobody was subscribed. The fetch is
   *  deferred to the next subscribe instead of run blind — a busy 20-agent org
   *  otherwise pays one full `read_chat` per node per event burst with nobody
   *  looking (the poll was mount-gated; the event path wasn't). */
  dirty: boolean
}

const M = new Map<string, Entry>()
// '/' cannot appear in a slug or node id (both are slugify()'d to [a-z0-9-]),
// so the key is unambiguous — and greppable in a debug dump, which a
// lookalike separator would not be.
const key = (slug: string, nid: string) => `${slug}/${nid}`

function entry(k: string): Entry {
  let e = M.get(k)
  if (!e) {
    e = { s: BLANK, subs: new Set(), thinkT0: 0, clock: null, nudge: null,
          staleDraft: false, staleThink: false, staleAt: 0, streamAt: 0,
          poll: null, inflight: false, fetchedAt: 0, installed: 0, dirty: false }
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
    // an event marked this node stale while nobody was looking — settle the
    // deferred refetch now, immediately, not on the next heartbeat tick
    if (e.dirty) { e.dirty = false; void refreshConvo(slug, nid, { force: true }) }
    // the thinking clock parks while unwatched (see startClock) — resume it
    if (e.thinkT0) startClock(k, e)
    return () => {
      e.subs.delete(cb)
      if (!e.subs.size) {
        if (e.poll) { clearTimeout(e.poll); e.poll = null }
        // nobody is rendering thinkSecs — stop patching it at 1 Hz. thinkT0
        // stays set, so a resubscribe resumes with the true elapsed time.
        stopClock(e)
      }
    }
  }, [k, slug, nid])
  const snap = useCallback(() => entry(k).s, [k])
  return useSyncExternalStore(sub, snap, snap)
}

/** The newest N transcript rows `serverCopies` counts within.
 *
 *  It is a NEWEST-n slice rather than the whole payload on purpose: paging
 *  older messages in (`loadOlder`) prepends rows, and a window measured from
 *  the end is immune to that, while counting the whole payload would let an
 *  ancient identical message drift into view and graduate a live ghost early —
 *  a GAP, the failure direction this system refuses.
 *
 *  ⚠ It was 20, which is smaller than a real turn. Measured over 94 real
 *  transcripts (2026-08-04): consecutive user messages are typically 5
 *  rendered rows apart, but the p90 is 14 and the maximum 138 — a tool-heavy
 *  turn buries the user's own message deeper than 20 rows in one go. Once the
 *  copy is out of the window the count can never rise again, so the ghost is
 *  stranded FOREVER and the message renders twice for the rest of the session.
 *  200 covers the measured maximum with room to spare. */
const COPIES_WINDOW = 200
/** How much of a ghost's text has to be found. Bounded because the server
 *  TRUNCATES pending bodies (`node_chat` shrinks them in tiers as the queue
 *  grows: 2000 / 800 / 250 chars) — a full-length needle can never occur in a
 *  truncated haystack, so
 *  a long message's ghost never graduated against `pending_mail` and sat on
 *  screen beside its own pending bubble until the transcript caught up, or for
 *  ever if the agent was frozen/archived/queued (measured 2026-08-04 on a
 *  ~10 kB message). Must stay well under the server's smallest body cap. */
const COPIES_NEEDLE = 200

/** how many copies of `text` the server is currently showing — the mailbox and
 *  the transcript both count, since a message passes through them in order */
function serverCopies(c: ChatPayload | null, text: string): number {
  if (!c) return 0
  const needle = text.slice(0, COPIES_NEEDLE)
  return c.messages.slice(-COPIES_WINDOW)
    .filter((m) => m.role === 'user' && (m.text || '').includes(needle)).length
    + (c.pending_mail ?? []).filter((m) => (m.body || '').includes(needle)).length
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
    // latest-STARTED request wins, not latest-landed — see Entry.installed
    if (startedAt < e.installed) return
    e.installed = startedAt
    // A pending ghost graduates once the SERVER'S OWN copy is visible — by
    // CONTAINMENT, not equality, since the turn text is a mail envelope by
    // then. Two things count as visible, and both must, because the message
    // passes through them in order: first it sits in the node's mailbox
    // (`pending_mail`, which the desk renders as a durable pending bubble),
    // then a turn drains it into the transcript. Checking only the transcript
    // left a hole for the whole of CLI startup — the mail was on the server,
    // the ghost had been dropped, and nothing was on screen (user bug
    // 2026-08-03: "the queued preview never shows up while the agent is
    // starting"). Same rule as everywhere else: retire on evidence.
    // a ghost graduates when the server shows MORE copies of its text than it
    // did when the ghost was made — never merely "a copy exists", which an
    // earlier identical message already satisfies
    const pending = e.s.pending
      .filter((g) => serverCopies(c, g.text) <= g.seen && !scrolledPast(c, g))
      // a ghost made before the first payload has no seq baseline (see
      // addPending). This survivor's message is NOT in this payload — its
      // eventual row must come after everything the payload shows — so the
      // payload's newest seq is a sound baseline, and scrolledPast becomes
      // reachable for it from here on.
      .map((g) => g.seq0 === UNKNOWN_SEQ
        ? { ...g, seq0: c.messages.length ? (c.messages[c.messages.length - 1]?.seq ?? -1) : -1 }
        : g)
    // superseded scaffolding retires HERE, with its replacement in hand — one
    // atomic patch, so the draft never blinks out ahead of the durable row
    const retire: Partial<Convo> = {}
    const fresh = startedAt >= e.staleAt      // this fetch can SEE the new row
    // THE PAYLOAD IS EVIDENCE TOO. Until now the scaffolding retired only on a
    // websocket event, so losing the one frame that ends a turn left the grey
    // streamed text and the thinking clock on screen FOREVER — beside the
    // durable row that replaced them (the reply rendered twice) and across
    // every later turn (`thinking… for 3100s`). That is a violation of the one
    // rule this app is built on: nothing on screen may depend on having caught
    // an event. `busy:false` is the server saying no turn is running, so
    // nothing can be streaming, and the payload carrying that statement also
    // carries the durable rows — the same atomic handover D-50 requires.
    //
    // ⚠ Only if this request went out AFTER the newest token, though. One
    // issued before the stream began describes a world in which nothing was
    // being typed, and honouring it would blank a draft that is still growing
    // — the same mistake this rule exists to fix, in a new place.
    //
    // Residual, stated rather than hidden: a `text` frame lost MID-turn still
    // leaves that draft up until the turn ends, because no server fact
    // distinguishes "this draft was superseded" from "this draft is still
    // being typed" without going back to the string matching P2 deleted.
    const idle = !c.busy && startedAt >= e.streamAt
    if ((fresh && e.staleDraft) || idle) { retire.draft = ''; e.staleDraft = false }
    if ((fresh && e.staleThink) || idle) {
      retire.thinking = ''
      retire.thinkSecs = null
      e.staleThink = false
    }
    if (idle && e.thinkT0) { e.thinkT0 = 0; stopClock(e) }
    // normalize, don't cast: LiveRowPayload.text is optional on the wire,
    // LiveRow.text is not — a cast would silently re-open the type hole the
    // typing wave closed
    const live: LiveRow[] = (c.live ?? []).map((r) => ({ ...r, text: r.text ?? '' }))
    patch(k, { chat: c, loaded: true, loadingOlder: false, pending, live, ...retire })
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
  const e = entry(k)
  // Baseline: everything already ACCOUNTED FOR is not this send. That is the
  // payload's copies PLUS the ghosts already standing in for earlier sends of
  // the same text — send "continue" twice before a refresh lands and both
  // ghosts used to carry `seen: 0`, so the server showing ONE copy retired
  // BOTH and the second message went off screen. (D-52 fixed the same-text
  // case across turns; this is the same-instant case its baseline missed.)
  const msgs = e.s.chat?.messages ?? []
  patch(k, {
    pending: [...e.s.pending, {
      id: ++GHOST_ID,
      text,
      seen: serverCopies(e.s.chat, text)
        + e.s.pending.filter((g) => g.text === text).length,
      // −1 on a LOADED-and-empty transcript: the message will be row 0, so
      // the window has moved past it once row 0 is no longer in it. Before
      // the first load the length is unknowable — see UNKNOWN_SEQ.
      seq0: !e.s.loaded ? UNKNOWN_SEQ
        : msgs.length ? (msgs[msgs.length - 1]?.seq ?? -1) : -1,
    }],
  })
}

/** Retire ONE ghost — the oldest with this text.
 *
 *  It used to filter by text, which retires every send of the same words: a
 *  failed second "yes" took the first one's preview down with it. One call,
 *  one send, one ghost. */
export function dropPending(slug: string, nid: string, text: string): void {
  const k = key(slug, nid)
  patch(k, { pending: dropOne(entry(k).s.pending, (g) => g.text === text) })
}

/** the same list minus the first match — or the same array if nothing matched,
 *  so `patch` can tell that nothing changed */
function dropOne(list: PendingGhost[], hit: (g: PendingGhost) => boolean): PendingGhost[] {
  const i = list.findIndex(hit)
  return i < 0 ? list : [...list.slice(0, i), ...list.slice(i + 1)]
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
    e.streamAt = e.thinkT0
    startClock(k, e)
    e.staleThink = false
    patch(k, { thinking: '', thinkSecs: 0 })
    return
  }
  if (ev.kind === 'thinking') {
    e.streamAt = Date.now()
    if (!e.thinkT0) { e.thinkT0 = Date.now(); startClock(k, e); patch(k, { thinkSecs: 0 }) }
    // a fresh thought must not continue a superseded one
    const base = e.staleThink ? '' : e.s.thinking
    e.staleThink = false
    patch(k, { thinking: (base + ev.text).slice(-2000) })
    return
  }
  if (ev.kind === 'delta') {
    e.streamAt = Date.now()
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
    // one steered delivery retires one ghost — see dropPending. Matched on a
    // bounded needle for the same reason as serverCopies: the server caps the
    // event text (api `m[:2000]`), so a full-length needle from a longer steer
    // can never occur in it.
    patch(k, { pending: dropOne(e.s.pending,
      (g) => ev.text.includes(g.text.slice(0, COPIES_NEEDLE))) })
  }
  nudge(slug, ev.node)
}

/** Coalesce a burst of events into ONE refetch. A turn emits several rows in
 *  quick succession and every one of them wants the same payload. */
function nudge(slug: string, nid: string): void {
  const e = entry(key(slug, nid))
  // The poll is mount-gated; the event path must be too, or a busy org pays
  // one read_chat per node per event burst with nobody looking. Deferred, not
  // dropped: the next subscribe settles it immediately (see useConvo).
  if (!e.subs.size) { e.dirty = true; return }
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
  // same mount gate as nudge(): defer, don't fetch blind
  if (!e.subs.size) { e.dirty = true; return }
  void refreshConvo(slug, ev.node, { force: true })
}

// ------------------------------------------------------------------- timers
function startClock(k: string, e: Entry): void {
  if (e.clock) return
  // no subscribers, no ticking — an unwatched node whose end-of-turn frame is
  // lost would otherwise patch thinkSecs at 1 Hz forever. thinkT0 keeps the
  // truth; the next subscribe resumes the clock (see useConvo).
  if (!e.subs.size) return
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
    // ⚠ Only unwatched entries lose their poll. React runs child effects
    // before parent effects, so on an org switch the new desks have ALREADY
    // subscribed (and beat() armed their polls) by the time App's reset effect
    // runs — clearing those polls left every surviving view heartbeat-less,
    // which is verbatim the frozen-desk failure beat() exists to abolish.
    // Entries with subscribers ARE the new org's; their poll stands.
    if (e.poll && !e.subs.size) { clearTimeout(e.poll); e.poll = null }
    if (e.nudge) { clearTimeout(e.nudge); e.nudge = null }
    e.thinkT0 = 0
    e.staleDraft = false
    e.staleThink = false
    e.staleAt = 0
    e.streamAt = 0
    e.inflight = false
    e.fetchedAt = 0
    e.installed = 0
    e.dirty = false
    e.s = BLANK
    e.subs.forEach((cb) => cb())
  })
}
