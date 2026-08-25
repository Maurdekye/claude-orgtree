// acctstate.test.tsx — a key row's usage button opens THIS MACHINE'S RECORD
// for that account, not usage percentages (user ruling 2026-08-25): which
// models still have capacity on it, which are waiting, and until when.
//
// Every assertion is on the RENDERED TEXT and on elements present or absent —
// never on a prop having been passed. §1.5 in particular asserts the ABSENCE
// of the bar markup, which is only worth anything because §2 proves the same
// component still renders bars for the primary; an absence check against a
// component that renders nothing at all would pass for the wrong reason.
//
//   §1 a key row: the capacity table, the pool footnote, no bars
//   §2 the primary row: bars, unchanged — the control for §1.5
//   §3 no greyed duplicate row anywhere (that feature is retired)
//
// Run:  cd frontend && node tests/run.mjs acctstate

import { flush, inAct, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { AccountsPanel } from '../src/canvas/accounts'
import type { AccountsPayload, AccountUsage } from '../src/types'

const NOW = 1_700_000_000_000                 // what useFakeClock pins Date to
const iso = (ms: number) => new Date(NOW + ms).toISOString()
const HOUR = 3600_000

const ACCOUNTS: AccountsPayload = {
  version: 2,
  primary: { signed_in: true, email: 'host@example.test' },
  keys: [{ id: 'k1', ordinal: 1,
    account_uuid: '2d37ed7a-ff4b-4fa5-93da-54b828225866' }],
  assignments: {
    haiku: { account: 'primary', available: true, refresh_at: null },
    sonnet: { account: 'primary', available: true, refresh_at: null },
    opus: { account: 'primary', available: true, refresh_at: null },
    fable: { account: 'k1', available: true, refresh_at: null },
  },
}

// what the server answers for k1 after a sonnet limit: the pooled three are
// parked on one time, fable is untouched
const KEY_USAGE: AccountUsage = {
  account: 'k1', label: 'fallback 1', available: false, unsupported: true,
  error: 'usage limits can\'t be read for a `claude setup-token` key.',
  tiers: [
    { tier: 'haiku', available: false, refresh_at: iso(2 * HOUR + 13 * 60_000),
      pool: ['haiku', 'sonnet', 'opus'] },
    { tier: 'sonnet', available: false, refresh_at: iso(2 * HOUR + 13 * 60_000),
      pool: ['haiku', 'sonnet', 'opus'] },
    { tier: 'opus', available: false, refresh_at: iso(2 * HOUR + 13 * 60_000),
      pool: ['haiku', 'sonnet', 'opus'] },
    { tier: 'fable', available: true, refresh_at: null, pool: null },
  ],
}

const PRIMARY_USAGE: AccountUsage = {
  account: 'primary', label: 'host@example.test', available: true, plan: 'max',
  limits: [{ kind: 'session', percent: 42, resets_at: iso(HOUR) }],
}

const g = globalThis as unknown as Record<string, unknown>

/** answer exactly the three endpoints the panel reaches for, and reject
 *  anything else loudly — a silent `{ok:true}` for a mistyped path is how a
 *  test ends up asserting against an empty modal */
function stubFetch(): void {
  g.fetch = (url: string) => {
    const p = new URL(String(url), 'http://localhost').pathname
    const body = /\/accounts$/.test(p) ? ACCOUNTS
      : /\/accounts\/usage\/k1$/.test(p) ? KEY_USAGE
        : /\/accounts\/usage\/primary$/.test(p) ? PRIMARY_USAGE
          : null
    if (!body) return Promise.reject(new Error(`unexpected fetch: ${p}`))
    return Promise.resolve({
      ok: true, status: 200, headers: new Headers(),
      json: () => Promise.resolve(body),
    })
  }
}

const txt = (el: HTMLElement) => el.textContent ?? ''

/** mount the panel, click the usage button in row `row` (0 = primary), and
 *  return the settled DOM */
async function openUsage(row: number): Promise<HTMLElement> {
  const view = await mountView(
    <AccountsPanel toast={() => {}} close={() => {}} />, (el) => el)
  await inAct(async () => { await flush(8) })
  const btns = view.el.querySelectorAll<HTMLButtonElement>('.acct-usage-btn')
  assert.ok(btns[row], `no usage button in row ${row} (${btns.length} found)`)
  await inAct(async () => { btns[row].click() })
  await inAct(async () => { await flush(8) })
  return view.el
}

function panelTest(name: string,
  body: (t: TestContext) => Promise<void>): void {
  test(name, async (t: TestContext) => {
    useFakeClock()
    stubFetch()
    try {
      await body(t)
    } finally {
      realClock()
      delete g.fetch
    }
  })
}

// ---------------------------------------------------------------- §1
panelTest('§1.1 a key row lists every model tier by name', async () => {
  const el = await openUsage(1)
  const rows = el.querySelectorAll('.acct-tier-row')
  assert.equal(rows.length, 4, `four tiers, got ${rows.length}`)
  assert.deepEqual(
    [...rows].map((r) => r.querySelector('.acct-tier-name')?.textContent),
    ['haiku', 'sonnet', 'opus', 'fable'])
})

panelTest('§1.2 a tier with capacity says so', async () => {
  const el = await openUsage(1)
  const fable = [...el.querySelectorAll('.acct-tier-row')]
    .find((r) => r.querySelector('.acct-tier-name')?.textContent === 'fable')
  assert.match(txt(fable as HTMLElement), /has capacity/)
})

panelTest('§1.3 a spent tier gives BOTH how long and when', async () => {
  const el = await openUsage(1)
  const opus = [...el.querySelectorAll('.acct-tier-row')]
    .find((r) => r.querySelector('.acct-tier-name')?.textContent === 'opus')
  const line = txt(opus as HTMLElement)
  // "how long" — and phrased as a REFRESH, not a "reset": these are the
  // router's own marks, not a billing period the user can read anywhere else
  assert.match(line, /refreshes in 2h 13m/)
  assert.doesNotMatch(line, /resets/)
  // "until when" — the wall clock, which is the half the relative form cannot
  // give and the half you need to decide whether to wait
  const when = new Date(NOW + 2 * HOUR + 13 * 60_000)
    .toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  assert.ok(line.includes(when), `${JSON.stringify(line)} lacks ${when}`)
})

panelTest('§1.4 the shared bucket is explained, from the server\'s own list',
  async () => {
    const el = await openUsage(1)
    const note = txt(el.querySelector('.acct-tier-note') as HTMLElement)
    assert.match(note, /haiku, sonnet, opus share one usage pool/)
  })

panelTest('§1.5 …and NOT one usage bar (the percentages are the thing removed)',
  async () => {
    const el = await openUsage(1)
    assert.equal(el.querySelectorAll('.usage-track').length, 0)
    assert.equal(el.querySelectorAll('.usage-row').length, 0)
  })

panelTest('§1.6 the heading calls it capacity, not usage', async () => {
  const el = await openUsage(1)
  const h = txt(el.querySelector('.usage-modal h3') as HTMLElement)
  assert.match(h, /model capacity — fallback 1/)
})

// ---------------------------------------------------------------- §2
panelTest('§2.1 the primary row still renders real usage bars', async () => {
  const el = await openUsage(0)
  // THE CONTROL FOR §1.5: the same component, the same modal, bars present
  assert.equal(el.querySelectorAll('.usage-track').length, 1)
  assert.match(txt(el.querySelector('.usage-modal') as HTMLElement), /42%/)
  assert.equal(el.querySelectorAll('.acct-tier-row').length, 0)
})

panelTest('§2.2 …under a heading that still says usage', async () => {
  const el = await openUsage(0)
  const h = txt(el.querySelector('.usage-modal h3') as HTMLElement)
  assert.match(h, /usage — host@example\.test/)
})

// ---------------------------------------------------------------- §3
panelTest('§3.1 no row is greyed as a duplicate of the login', async () => {
  const view = await mountView(
    <AccountsPanel toast={() => {}} close={() => {}} />, (el) => el)
  await inAct(async () => { await flush(8) })
  assert.equal(view.el.querySelectorAll('.acct-dup').length, 0)
  // and the key row IS rendered — an empty panel would pass the line above
  assert.equal(view.el.querySelectorAll('.acct-key').length, 1)
})
