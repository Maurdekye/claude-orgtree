// crashreporter.test.ts — frontend/src/crashReporter.ts's four capture paths
// plus the durability/delivery contract: persist to localStorage BEFORE any
// attempt at delivery, then beacon out. See CrashBoundary's own render-path
// coverage is proven live (not here) via ?crashtest= query params — this
// suite covers what a jsdom environment can exercise directly: the module's
// exported entry point, its window-level listeners, and its breadcrumb trail.
//
// Run:  cd frontend && node tests/run.mjs crashreporter

import './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { mock } from 'node:test'

// navigator.sendBeacon is not implemented by jsdom — stub it before any
// call into crashReporter's deliver() path reaches for it. Assigned AFTER
// the harness DOM exists but that's fine: crashReporter only reads
// navigator.sendBeacon at CALL time (inside reportCrash), never at import
// time, so ordering relative to the import below doesn't matter.
const beacon = mock.fn(() => true)
;(navigator as unknown as { sendBeacon: typeof beacon }).sendBeacon = beacon

import { reportCrash } from '../src/crashReporter'

const REPORTS_KEY = 'orgtree.crashReports'
const readStored = (): Array<{ id: string; message: string }> =>
  JSON.parse(localStorage.getItem(REPORTS_KEY) || '[]')

test.beforeEach(() => {
  localStorage.removeItem(REPORTS_KEY)
  beacon.mock.resetCalls()
})

test('reportCrash() persists to localStorage before attempting delivery', () => {
  const report = reportCrash({ kind: 'window-error', message: 'boom-1', stack: 'Error: boom-1' })
  const stored = readStored()
  assert.equal(stored.length, 1)
  assert.equal(stored[0]!.id, report.id)
  assert.equal(stored[0]!.message, 'boom-1')
})

test('reportCrash() beacons the report to /api/crash-report', () => {
  reportCrash({ kind: 'unhandledrejection', message: 'boom-2', stack: 'Error: boom-2' })
  assert.equal(beacon.mock.callCount(), 1)
  const [url, blob] = beacon.mock.calls[0]!.arguments as unknown as [string, Blob]
  assert.equal(url, '/api/crash-report')
  assert.ok(blob instanceof Blob)
})

test('local report history is capped — a crash loop cannot grow it unbounded', () => {
  for (let i = 0; i < 25; i += 1) {
    reportCrash({ kind: 'window-error', message: `boom-${i}`, stack: 'x' })
  }
  const stored = readStored()
  assert.equal(stored.length, 20, 'MAX_REPORTS should cap the local list')
  // oldest were dropped, newest survive
  assert.equal(stored[stored.length - 1]!.message, 'boom-24')
  assert.ok(!stored.some((r) => r.message === 'boom-0'), 'the oldest entry should have been evicted')
})

test("window 'error' events produce a window-error report with the real error's stack", () => {
  const err = new Error('uncaught in a handler')
  const event = new (window as unknown as { ErrorEvent: typeof ErrorEvent }).ErrorEvent('error', {
    message: err.message, error: err, filename: 'app.js', lineno: 1, colno: 2,
  })
  window.dispatchEvent(event)
  const stored = readStored()
  const last = stored[stored.length - 1]!
  assert.equal(last.message, 'uncaught in a handler')
  assert.match((last as unknown as { stack: string }).stack, /uncaught in a handler/)
  assert.equal((last as unknown as { kind: string }).kind, 'window-error')
})

test("window 'unhandledrejection' events produce an unhandledrejection report", () => {
  const reason = new Error('promise blew up')
  const PRE = (window as unknown as { PromiseRejectionEvent?: typeof Event }).PromiseRejectionEvent
  // jsdom does not implement PromiseRejectionEvent — build an Event and graft
  // the fields crashReporter.ts actually reads (`.reason`) onto it, the same
  // shape the browser hands the listener.
  const event = new Event('unhandledrejection') as Event & { reason: unknown }
  event.reason = reason
  window.dispatchEvent(event)
  const stored = readStored()
  const last = stored[stored.length - 1]!
  assert.equal(last.message, 'promise blew up')
  assert.equal((last as unknown as { kind: string }).kind, 'unhandledrejection')
  void PRE
})

test('a click breadcrumb rides the next report — "what had the user just done"', () => {
  const button = document.createElement('button')
  button.setAttribute('aria-label', 'Save changes')
  document.body.appendChild(button)
  button.dispatchEvent(new MouseEvent('click', { bubbles: true }))

  const report = reportCrash({ kind: 'window-error', message: 'after-click', stack: 'x' })
  const crumb = report.breadcrumbs[report.breadcrumbs.length - 1]!
  assert.equal(crumb.kind, 'click')
  assert.match(crumb.detail, /Save changes/)
  document.body.removeChild(button)
})
