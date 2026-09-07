/** freezelog.test.tsx — src/freezelog.ts (the passive freeze recorder) and
 *  src/FreezeLogPage.tsx (the /debug/freezes page).
 *
 * The recorder takes its frame source and clock by injection, so every case
 * here drives frames by hand with a fake clock: nothing waits on real time,
 * and a gap is exactly the number this file says it is.
 *
 * ⚠ ANTI-VACUITY. The cheap wrong recorder is one that never records, and a
 * suite of "healthy frames leave nothing" cases is green against it. §1 pins
 * both sides in one test: sub-threshold gaps leave nothing AND a gap at the
 * threshold is recorded with its size. §3 checks the visibility carve-out the
 * same way — the hidden gap is skipped, the next real gap is not.
 *
 * §0 install writes a start entry naming the path, and the entry has the tab
 * §1 frame gaps: below threshold nothing, at/above threshold one entry, ms exact
 * §2 the ring holds the last 200 and drops the oldest first
 * §3 a hidden tab's gap is not a freeze; visibility itself is recorded
 * §4 lifecycle events (pagehide/pageshow/freeze/resume) are recorded
 * §5 stop() detaches: no frames and no events are recorded afterwards
 * §6 corrupt storage reads as empty rather than throwing
 * §6b two tabs write their own rings: neither can drop the other's entry
 * §6c retention: stale rings and rings beyond the 10 most recent are pruned at install
 * §7 the page: newest first, the empty state, Clear empties storage
 * §8 the path test: /debug/freezes with and without the kiosk prefix
 *
 * Run:  cd frontend && node tests/run.mjs freezelog
 */
import { mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  FREEZE_LOG_CAP, FREEZE_LOG_PREFIX, FREEZE_LOG_TABS_KEPT, FREEZE_LOG_MAX_AGE_MS,
  clearFreezeLog, freezeLogKey, installFreezeLog, pruneFreezeLog, readFreezeLog, readTabFreezeLog,
} from '../src/freezelog'
import FreezeLogPage, { isFreezeLogPath } from '../src/FreezeLogPage'

/** A hand-cranked frame source: `tick(ms)` advances the clock and delivers
 *  the pending frame callback, exactly once, at the new time. */
function rig(opts: { thresholdMs?: number; cap?: number } = {}) {
  let t = 1000
  let pending: ((t: number) => void) | null = null
  const stop = installFreezeLog({
    ...opts,
    now: () => t,
    raf: (cb) => { pending = cb; return 1 },
    caf: () => { pending = null },
  })
  const tick = (ms: number) => {
    t += ms
    const cb = pending
    pending = null
    cb?.(t)
  }
  return { tick, stop, hasPendingFrame: () => pending !== null }
}

test.beforeEach(() => {
  clearFreezeLog()
  window.sessionStorage.removeItem('orgtree.freezes-tab')
})

test('§0 install writes a start entry with the path and a tab id', () => {
  const r = rig()
  const log = readFreezeLog()
  assert.equal(log.length, 1)
  assert.equal(log[0]!.kind, 'start')
  assert.equal(log[0]!.detail, location.pathname)
  assert.ok(log[0]!.tab.length > 0)
  assert.equal(log[0]!.vis, document.visibilityState)
  r.stop()
})

test('§1 sub-threshold gaps leave nothing; a gap at the threshold is recorded with its size', () => {
  const r = rig({ thresholdMs: 250 })
  for (let i = 0; i < 50; i++) r.tick(16)
  r.tick(249)
  assert.deepEqual(readFreezeLog().map((e) => e.kind), ['start'], 'healthy frames must record nothing')
  r.tick(250)
  let log = readFreezeLog()
  assert.equal(log.length, 2)
  assert.equal(log[1]!.kind, 'gap')
  assert.equal(log[1]!.ms, 250)
  assert.match(log[1]!.detail, /250 ms between frames/)
  r.tick(16)
  r.tick(1234)
  log = readFreezeLog()
  assert.equal(log.length, 3)
  assert.equal(log[2]!.ms, 1234)
  assert.ok(r.hasPendingFrame(), 'the loop re-arms after every frame')
  r.stop()
})

test('§2 the ring keeps the last 200 and drops the oldest first', () => {
  const r = rig({ thresholdMs: 100 })
  for (let i = 0; i < FREEZE_LOG_CAP + 25; i++) r.tick(100 + i)   // distinct sizes
  const log = readFreezeLog()
  assert.equal(log.length, FREEZE_LOG_CAP)
  assert.ok(log.every((e) => e.kind === 'gap'), 'the start entry was the oldest and is gone')
  assert.equal(log[0]!.ms, 100 + 25, 'the 25 oldest gaps were dropped')
  assert.equal(log[log.length - 1]!.ms, 100 + FREEZE_LOG_CAP + 24)
  r.stop()
})

test('§3 a hidden tab\'s gap is not a freeze, the visibility change itself is, and the next real gap still counts', () => {
  const r = rig({ thresholdMs: 250 })
  const doc = document as unknown as { visibilityState: string }
  Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
  r.tick(5000)   // the tab was away for five seconds: no frames were painted
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
  r.tick(3000)   // first frame after coming back: also not a freeze
  let kinds = readFreezeLog().map((e) => `${e.kind}:${e.detail}`)
  assert.deepEqual(kinds.slice(1), ['visibility:hidden', 'visibility:visible'],
    `no gap may be recorded across a hidden tab, got ${kinds.join(' | ')}`)
  r.tick(16)
  r.tick(400)
  kinds = readFreezeLog().map((e) => e.kind)
  assert.equal(kinds[kinds.length - 1], 'gap', 'a real gap after the tab is back is still recorded')
  assert.equal(doc.visibilityState, 'visible')
  r.stop()
})

test('§4 lifecycle events are recorded with their type and persisted flag', () => {
  const r = rig()
  window.dispatchEvent(new Event('pagehide'))
  const show = new Event('pageshow')
  Object.defineProperty(show, 'persisted', { value: true })
  window.dispatchEvent(show)
  window.dispatchEvent(new Event('freeze'))
  window.dispatchEvent(new Event('resume'))
  const details = readFreezeLog().filter((e) => e.kind === 'lifecycle').map((e) => e.detail)
  assert.deepEqual(details, ['pagehide', 'pageshow persisted=true', 'freeze', 'resume'])
  r.stop()
})

test('§5 stop() detaches everything: frames and events after it record nothing', () => {
  const r = rig({ thresholdMs: 100 })
  r.tick(500)
  assert.equal(readFreezeLog().length, 2)
  r.stop()
  assert.ok(!r.hasPendingFrame(), 'the frame loop is cancelled')
  r.tick(900)
  document.dispatchEvent(new Event('visibilitychange'))
  window.dispatchEvent(new Event('pagehide'))
  assert.equal(readFreezeLog().length, 2, 'nothing may be recorded after stop()')
})

test('§6 corrupt or foreign storage reads as empty and is overwritten, never thrown on', () => {
  localStorage.setItem(freezeLogKey('bad1'), '{not json')
  assert.deepEqual(readFreezeLog(), [])
  localStorage.setItem(freezeLogKey('bad2'), '{"a":1}')
  assert.deepEqual(readFreezeLog(), [])
  const r = rig()
  assert.equal(readFreezeLog().length, 1)
  r.stop()
})

test('§6b two tabs keep separate rings, and reading merges them by time', () => {
  // tab A installs and records; then tab B (a different sessionStorage id)
  // installs and records; A's entry must still be there afterwards
  const a = rig({ thresholdMs: 100 })
  a.tick(300)
  const tabA = readFreezeLog()[0]!.tab
  a.stop()
  window.sessionStorage.removeItem('orgtree.freezes-tab')   // a new tab mints a new id
  const b = rig({ thresholdMs: 100 })
  b.tick(500)
  b.stop()
  const tabB = readTabFreezeLog(tabA).length ? readFreezeLog().find((e) => e.tab !== tabA)!.tab : ''
  assert.notEqual(tabA, tabB)
  assert.deepEqual(readTabFreezeLog(tabA).map((e) => e.kind), ['start', 'gap'], "tab A's ring is intact after tab B wrote")
  assert.deepEqual(readTabFreezeLog(tabB).map((e) => e.kind), ['start', 'gap'])
  assert.equal(readFreezeLog().length, 4, 'the merged view has both')
  assert.equal(readTabFreezeLog(tabB).find((e) => e.kind === 'gap')!.ms, 500)
  clearFreezeLog()
  assert.equal(localStorage.getItem(freezeLogKey(tabA)), null, 'Clear removes every tab ring')
  assert.equal(localStorage.getItem(freezeLogKey(tabB)), null)
})

test('§6c retention at install: rings older than 7 days go, only the 10 most recent tabs stay, the installing tab is never pruned', () => {
  const now = Date.now()
  const seed = (tab: string, at: number) =>
    localStorage.setItem(freezeLogKey(tab), JSON.stringify([{ at, kind: 'start', detail: '/', vis: 'visible', tab }]))
  seed('stale', now - FREEZE_LOG_MAX_AGE_MS - 1)
  for (let i = 0; i < 12; i++) seed(`t${i}`, now - (i + 1) * 1000)   // t0 newest ... t11 oldest
  pruneFreezeLog(localStorage, now, 'me')
  const left = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)!)
    .filter((k) => k.startsWith(FREEZE_LOG_PREFIX)).sort()
  assert.ok(!left.includes(freezeLogKey('stale')), 'the stale ring is gone')
  assert.equal(left.length, FREEZE_LOG_TABS_KEPT - 1, 'nine others stay beside the installing tab')
  assert.ok(!left.includes(freezeLogKey('t9')) && !left.includes(freezeLogKey('t11')), 'the oldest were dropped')
  assert.ok(left.includes(freezeLogKey('t0')) && left.includes(freezeLogKey('t8')))
  // and install itself prunes: a fresh install with 12 rings present leaves 9 + its own
  const r = rig()
  const after = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)!)
    .filter((k) => k.startsWith(FREEZE_LOG_PREFIX))
  assert.equal(after.length, FREEZE_LOG_TABS_KEPT)
  r.stop()
})

test('§7 the page lists entries newest first, shows an empty state, and Clear empties storage', async () => {
  const empty = await mountView(<FreezeLogPage />, (el) => el.querySelector('[data-testid=freeze-empty]'))
  assert.ok(empty.frames[0], 'empty state shown when nothing is recorded')

  const r = rig({ thresholdMs: 100 })
  r.tick(300)
  r.tick(16)
  r.tick(700)
  r.stop()
  const view = await mountView(<FreezeLogPage />, (el) =>
    Array.from(el.querySelectorAll('tbody tr')).map((tr) => tr.getAttribute('data-kind')))
  assert.deepEqual(view.frames[0], ['gap', 'gap', 'start'], 'newest first')
  const cells = Array.from(view.el.querySelectorAll('tbody tr td:nth-child(3)')).map((td) => td.textContent)
  assert.deepEqual(cells, ['700', '300', ''])
  const { act } = await import('react')
  const clear = Array.from(view.el.querySelectorAll('button')).find((b) => b.textContent === 'Clear')!
  await act(async () => { clear.click() })
  assert.equal(readFreezeLog().length, 0, 'Clear removes the stored rings')
  assert.ok(view.el.querySelector('[data-testid=freeze-empty]'), 'and the page shows the empty state')
})

test('§8 the debug path is recognised with and without the kiosk prefix, and nowhere else', () => {
  assert.equal(isFreezeLogPath('/debug/freezes'), true)
  assert.equal(isFreezeLogPath('/debug/freezes/'), true)
  assert.equal(isFreezeLogPath('/k/abc123/debug/freezes'), true)
  assert.equal(isFreezeLogPath('/o/orgtree'), false)
  assert.equal(isFreezeLogPath('/debug/freezes-not'), false)
  assert.equal(isFreezeLogPath('/'), false)
})
