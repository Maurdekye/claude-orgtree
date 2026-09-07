// Freeze log — a passive in-page recorder of the moments the UI stopped
// painting, kept so that a freeze the user felt can be lined up with the
// host-side memory sampler and Chrome's own GPU-process crash dumps
// (docket: diagnose-ui-lag-freezes-and-browser-crashes, user approval
// 2026-09-07). It records; it never repairs, retries or reports anywhere.
//
// WHAT IT RECORDS, and only these:
//   gap        two consecutive animation frames further apart than the
//              threshold (default 250 ms — a 60 Hz page paints every 16 ms,
//              so this is ~15 missed frames, well past "a heavy render")
//   longtask   a PerformanceObserver 'longtask' entry, where the browser
//              supports it (Chrome): the main thread held for ≥50 ms, with
//              the attribution the browser gives
//   visibility document.visibilityState changed — the frame gap that follows
//              a hidden tab is NOT a freeze, and is not recorded as one
//   lifecycle  pagehide / pageshow / freeze / resume (Page Lifecycle API):
//              the events around a tab being discarded or restored
//   start      the recorder was installed — one per page load, so a reload
//              after a crash shows as a new start with the previous tab's
//              last entries still ahead of it
//
// WHERE IT LIVES: localStorage, not sessionStorage, deliberately. The crash
// this exists to diagnose takes the tab with it, and sessionStorage dies with
// the tab; localStorage survives the crash AND is readable from another tab,
// which is what lets `/debug/freezes` show the log of a tab that is gone.
// Each entry carries the tab it came from (a random id kept in sessionStorage,
// so it IS per tab). Ring of the last 200 entries; each is a few dozen bytes.
//
// COST: one requestAnimationFrame callback per painted frame that does a
// subtraction and a compare, and a storage write only when something is
// recorded — which, on a healthy page, is never. No imports, for the same
// reason crashReporter.ts has none: an instrument for a broken page must not
// depend on the page loading cleanly.
//
// NOT A GPU MEASUREMENT: there is no web API for GPU memory. This shows
// freezes as the page experienced them; the sampler shows the budget.

export type FreezeKind = 'gap' | 'longtask' | 'visibility' | 'lifecycle' | 'start'

export interface FreezeEntry {
  /** epoch ms — the same clock as the sampler's ts_utc column */
  at: number
  kind: FreezeKind
  /** gap / longtask: the duration */
  ms?: number
  detail: string
  /** document.visibilityState when recorded */
  vis: string
  /** the tab this came from */
  tab: string
  /** Chrome only: performance.memory.usedJSHeapSize, MB */
  heap_mb?: number
}

export const FREEZE_LOG_KEY = 'orgtree.freezes'
const TAB_KEY = 'orgtree.freezes.tab'
export const FREEZE_LOG_CAP = 200

export interface FreezeLogOptions {
  /** frame gap at or above this is recorded (ms) */
  thresholdMs?: number
  cap?: number
  /** injection points for tests; production uses the browser's own */
  raf?: (cb: (t: number) => void) => number
  caf?: (id: number) => void
  now?: () => number
  storage?: Storage
  doc?: Document
  win?: Window
}

function safeParse(raw: string | null): FreezeEntry[] {
  if (!raw) return []
  try {
    const v = JSON.parse(raw) as unknown
    return Array.isArray(v) ? (v as FreezeEntry[]) : []
  } catch {
    return []
  }
}

export function readFreezeLog(storage: Storage = localStorage): FreezeEntry[] {
  return safeParse(storage.getItem(FREEZE_LOG_KEY))
}

export function clearFreezeLog(storage: Storage = localStorage): void {
  storage.removeItem(FREEZE_LOG_KEY)
}

function tabId(win: Window): string {
  try {
    const s = win.sessionStorage
    let id = s.getItem(TAB_KEY)
    if (!id) {
      id = Math.random().toString(36).slice(2, 8)
      s.setItem(TAB_KEY, id)
    }
    return id
  } catch {
    return 'unknown'
  }
}

function heapMb(): number | undefined {
  const mem = (performance as unknown as { memory?: { usedJSHeapSize?: number } }).memory
  const used = mem?.usedJSHeapSize
  return typeof used === 'number' ? Math.round(used / 1048576) : undefined
}

/** Start recording. Returns a function that stops it (and detaches every
 *  listener), which is what a test needs and what production never calls. */
export function installFreezeLog(opts: FreezeLogOptions = {}): () => void {
  const threshold = opts.thresholdMs ?? 250
  const cap = opts.cap ?? FREEZE_LOG_CAP
  const win = opts.win ?? window
  const doc = opts.doc ?? document
  const storage = opts.storage ?? localStorage
  const raf = opts.raf ?? ((cb) => win.requestAnimationFrame(cb))
  const caf = opts.caf ?? ((id) => win.cancelAnimationFrame(id))
  const now = opts.now ?? (() => performance.now())
  const tab = tabId(win)

  const record = (kind: FreezeKind, detail: string, ms?: number): void => {
    const entry: FreezeEntry = { at: Date.now(), kind, detail, vis: doc.visibilityState, tab }
    if (ms !== undefined) entry.ms = Math.round(ms)
    const heap = heapMb()
    if (heap !== undefined) entry.heap_mb = heap
    try {
      const ring = readFreezeLog(storage)
      ring.push(entry)
      if (ring.length > cap) ring.splice(0, ring.length - cap)
      storage.setItem(FREEZE_LOG_KEY, JSON.stringify(ring))
    } catch {
      // storage full or unavailable: an instrument must never throw into the page
    }
  }

  record('start', location.pathname)

  // ---- frame gaps
  let last = now()
  let skipNext = doc.visibilityState !== 'visible'   // no frames while hidden
  let stopped = false
  let handle = 0
  const frame = (): void => {
    if (stopped) return
    const t = now()
    if (skipNext) {
      skipNext = false
    } else {
      const gap = t - last
      if (gap >= threshold) record('gap', `${Math.round(gap)} ms between frames`, gap)
    }
    last = t
    handle = raf(frame)
  }
  handle = raf(frame)

  // ---- visibility and lifecycle
  const onVisibility = (): void => {
    record('visibility', doc.visibilityState)
    // whichever way it went, the next frame's gap is about the tab, not a freeze
    skipNext = true
    last = now()
  }
  doc.addEventListener('visibilitychange', onVisibility)
  const lifecycle = ['pagehide', 'pageshow', 'freeze', 'resume'] as const
  const onLifecycle = (e: Event): void => {
    const persisted = (e as { persisted?: boolean }).persisted
    record('lifecycle', e.type + (persisted === undefined ? '' : ` persisted=${persisted}`))
    skipNext = true
    last = now()
  }
  for (const ev of lifecycle) win.addEventListener(ev, onLifecycle)

  // ---- long tasks (Chrome)
  let observer: PerformanceObserver | null = null
  try {
    const PO = (win as unknown as { PerformanceObserver?: typeof PerformanceObserver }).PerformanceObserver
    if (PO && (PO.supportedEntryTypes ?? []).includes('longtask')) {
      observer = new PO((list) => {
        for (const e of list.getEntries()) {
          const attr = (e as unknown as { attribution?: Array<{ name?: string; containerType?: string }> }).attribution
          const who = attr && attr.length ? ` ${attr.map((a) => a.containerType ?? a.name ?? '').filter(Boolean).join(',')}` : ''
          record('longtask', `${Math.round(e.duration)} ms main thread${who}`, e.duration)
        }
      })
      observer.observe({ entryTypes: ['longtask'] })
    }
  } catch {
    observer = null
  }

  return () => {
    stopped = true
    caf(handle)
    doc.removeEventListener('visibilitychange', onVisibility)
    for (const ev of lifecycle) win.removeEventListener(ev, onLifecycle)
    observer?.disconnect()
  }
}
