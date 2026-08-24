// acctpanel.test.tsx — the ONE load-bearing sentence in the accounts panel.
//
// The 2026-08-24 copy trim removed every blurb explaining what the panel is
// for, deliberately unpinned: that text is expected to churn. This banner is
// different in kind and gets the panel's only string check. `selection_active`
// is the machine-readable form of D-144 — in Phase 1 nothing selects an
// account for a turn, and a panel of healthy accounts with a primary reads
// exactly like a working waterfall unless it SAYS otherwise. The banner core
// currently survives only because nobody has deleted it; after this file,
// deleting it fails a named check instead.
//
// ANTI-VACUITY: §2 flips only `selection_active` and asserts the warning is
// ABSENT — so §1 cannot be passing on decorative always-on text, and a build
// that warns even when selection IS live (the opposite lie) also fails.
//
// Run:  cd frontend && node tests/run.mjs acctpanel

import { flush, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import type { AccountsPayload } from '../src/types'

const noop = () => {}

function uiTest(name: string,
  body: (k: { mount: (el: React.ReactElement)
    => Promise<{ el: HTMLElement }> }) => Promise<void>): void {
  test(name, async (t: TestContext) => {
    useFakeClock()
    const open: { unmount: () => Promise<void> }[] = []
    t.after(async () => {
      for (const m of open) { try { await m.unmount() } catch { /* gone */ } }
      realClock()
    })
    await body({
      mount: async (el) => {
        const v = await mountView(el, (host) => host)
        open.push(v)
        return { el: v.el }
      },
    })
  })
}

function payload(selection_active: boolean): AccountsPayload {
  return {
    version: 1,
    accounts: [{
      uuid: '11111111-2222-3333-4444-555555555555', label: 'Main',
      email_masked: 'm***@example.com', org_uuid: null,
      subscription_type: 'max', rate_limit_tier: null,
      account_created_at: null, source: 'adopted',
      first_seen: '2026-08-24T00:00:00Z', last_seen: '2026-08-24T00:00:00Z',
    }],
    primary: '11111111-2222-3333-4444-555555555555',
    pins: {}, selection_active,
  }
}

/** serve `/api/accounts` from a literal; same fake-Response shape as the
 *  harness's installFetch (instance header included, so `req`'s restart
 *  detector sees what it always sees) */
function serveAccounts(p: AccountsPayload): void {
  (globalThis as unknown as { fetch: unknown }).fetch = () =>
    Promise.resolve({
      ok: true, status: 200,
      headers: new Headers({ 'X-Orgtree-Instance': 'inst-0' }),
      json: () => Promise.resolve(p),
    })
}

async function panelText(
  mount: (el: React.ReactElement) => Promise<{ el: HTMLElement }>,
  p: AccountsPayload,
): Promise<string> {
  serveAccounts(p)
  const { AccountsPanel } = await import('../src/canvas/accounts')
  const { el } = await mount(<AccountsPanel toast={noop} close={noop} />)
  await flush()
  return el.textContent ?? ''
}

uiTest('§1 while selection is not active, the panel SAYS no failover is running',
  async ({ mount }) => {
    const text = await panelText(mount, payload(false))
    assert.match(text, /no failover is running/i,
      'the D-144 banner core is gone — a registry of healthy accounts with a '
      + '"primary" now reads exactly like a working waterfall (see D-144: '
      + 'this sentence is load-bearing, not copy)')
  })

uiTest('§2 CONTROL: when selection IS active, that warning is absent',
  async ({ mount }) => {
    const text = await panelText(mount, payload(true))
    assert.doesNotMatch(text, /no failover is running/i,
      'the warning renders even when selection is live — either the check '
      + 'above is keying on decorative always-on text, or the panel now '
      + 'tells the opposite lie')
  })

// §3 — the panel must SURVIVE a response whose shape it did not expect.
//
// Added 2026-08-24 after a real crash: the token lookup dereferenced
// `.tokens` on any truthy response, so a payload without that key threw
// during render and took the WHOLE panel down — including the §1 banner
// above, which is the one sentence in this file that must never silently
// vanish. A blank panel passes no string check because there is no string.
//
// This is the shape a frontend talking to an older backend actually sees, and
// the serveAccounts stub reproduces it for free: it answers EVERY request with
// the accounts payload, tokens endpoint included.
uiTest('§3 an unexpected token-endpoint shape does not blank the panel',
  async ({ mount }) => {
    const text = await panelText(mount, payload(false))
    assert.match(text, /no failover is running/i,
      'the panel rendered nothing at all — a response without the key the '
      + 'token lookup expected threw during render, so the D-144 banner is '
      + 'gone for a reason no string check could otherwise report')
  })
