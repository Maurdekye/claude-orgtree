// antigravityusage.test.tsx — the shared header usage modal shows the
// Antigravity standing too: the last wall a turn hit (100%, with the reset
// the CLI named) or, with no wall on record, the settled "no readout" note —
// never a blank section and never a spurious error. And a walled Google
// account drives the header glow like any other lane.

import { flush, inAct, mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { UsageModal, usagePeak } from '../src/App'
import type { AccountUsage, UsageAllPayload, UsagePeek } from '../src/types'

const CLAUDE: UsageAllPayload = { accounts: [{
  account: 'primary', label: 'claude@example.test', available: true,
  plan: 'max', limits: [{ kind: 'session', group: 'session', percent: 17,
    severity: 'normal', resets_at: null, is_active: false, model: null }],
}] }

const CODEX: AccountUsage = {
  account: 'codex', provider: 'Codex', label: 'codex@example.test',
  available: true, limits: [
    { kind: 'weekly_all', group: 'codex', percent: 9, severity: 'normal',
      resets_at: null, is_active: false, model: null, label: '7 days' },
  ],
}

// the measured wall (2026-09-03): one 100% window, reset ~6d21h out
const WALLED: AccountUsage = {
  account: 'antigravity', provider: 'Antigravity', label: 'agy@example.test',
  available: true, limits: [
    { kind: 'provider_window', group: 'antigravity', percent: 100,
      severity: 'critical', resets_at: '2026-09-09T20:58:00Z',
      is_active: true, model: null, label: 'individual quota' },
  ],
}

// nothing observed: the settled note rides `unsupported`, not an error
const QUIET: AccountUsage = {
  account: 'antigravity', provider: 'Antigravity', label: 'agy@example.test',
  available: false, unsupported: true,
  error: 'Antigravity publishes no usage readout; a quota wall appears here '
    + 'when a turn hits one, with its reset',
}

const stubFetch = (agy: AccountUsage) => {
  const g = globalThis as unknown as Record<string, unknown>
  g.fetch = (url: string) => {
    const path = new URL(String(url), 'http://localhost').pathname
    const body = /\/accounts\/usage$/.test(path) ? CLAUDE
      : /\/codex\/usage$/.test(path) ? CODEX
      : /\/antigravity\/usage$/.test(path) ? agy : null
    if (!body) return Promise.reject(new Error(`unexpected fetch: ${path}`))
    return Promise.resolve({ ok: true, status: 200, headers: new Headers(),
      json: () => Promise.resolve(body) })
  }
  return () => { delete g.fetch }
}

test('usage modal renders the Antigravity wall beside Claude and Codex', async () => {
  const restore = stubFetch(WALLED)
  try {
    const view = await mountView(<UsageModal close={() => {}} />, (el) => el)
    await inAct(async () => { await flush(8) })
    const text = view.el.textContent ?? ''
    assert.match(text, /Antigravity · agy@example\.test/)
    assert.match(text, /individual quota/)
    assert.match(text, /100%/)
    // one Claude bar, one Codex bar, one Antigravity bar
    assert.equal(view.el.querySelectorAll('.usage-track').length, 3)
    assert.ok(!view.el.querySelector('.acct-unsupported'),
      'a walled account shows the bar, not the note')
  } finally {
    restore()
  }
})

test('with no wall on record the section carries the settled note', async () => {
  const restore = stubFetch(QUIET)
  try {
    const view = await mountView(<UsageModal close={() => {}} />, (el) => el)
    await inAct(async () => { await flush(8) })
    const text = view.el.textContent ?? ''
    assert.match(text, /Antigravity · agy@example\.test/)
    assert.match(text, /publishes no usage readout/)
    assert.ok(view.el.querySelector('.acct-unsupported'),
      'the note wears the settled styling, not the error one')
    assert.equal(view.el.querySelectorAll('.usage-track').length, 2)
  } finally {
    restore()
  }
})

test('a walled Google account drives the shared near-limit glow', () => {
  const claude: UsagePeek = { available: true, limits: [] }
  const codex: UsagePeek = { available: true, provider: 'Codex', limits: [] }
  const agy: UsagePeek = { available: true, provider: 'Antigravity', limits: [
    { kind: 'provider_window', group: 'antigravity', percent: 100,
      severity: 'critical', resets_at: null, is_active: true, model: null,
      label: 'individual quota' },
  ] }
  const alert = usagePeak(claude, codex, agy)
  assert.equal(alert?.sev, 'crit')
  assert.match(alert?.title ?? '', /individual quota at 100%/)
  assert.match(alert?.title ?? '', /Antigravity usage$/)
  // and an unavailable peek (no wall) contributes nothing
  assert.equal(usagePeak(claude, codex, { available: false, provider: 'Antigravity' }), null)
})
