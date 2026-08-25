// acctpanel.test.tsx — the accounts panel must not lie about failover.
//
// HISTORY, because these checks inverted once already: until 2026-08-25 §1
// pinned the PRESENCE of a "Registry only — no failover is running yet"
// banner, gated on `selection_active` being false. Then failover shipped and
// fired for real (2026-08-24 ~21:20) while the backend kept hardcoding
// `selection_active: false` — so the most prominent line in the window stated
// the exact opposite of the truth, on the user's own screen. §1/§2 now pin
// the banner's ABSENCE in BOTH payload states: nothing user-visible may key
// on that dead field. The serving line (resolved server-side from the real
// spawn environment) is the honest statement, pinned by §8.
//
// ANTI-VACUITY: an absence assertion over a blank panel abstains rather than
// tests, so §1/§2 assert a positive control from the SAME render first — the
// register-current-login button, which the panel shows unconditionally.
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

uiTest('§1 the dead banner stays gone even when the payload says selection_active:false',
  async ({ mount }) => {
    const text = await panelText(mount, payload(false))
    // positive control first — a blank panel must fail HERE, not let the
    // absence assertion below pass over nothing
    assert.match(text, /register current login/i,
      'panel rendered no content — the absence check below would abstain')
    assert.doesNotMatch(text, /no failover is running/i,
      'the "no failover is running yet" banner is back. Its gate '
      + '(`selection_active`) is hardcoded FALSE by the backend while '
      + 'failover is LIVE (first fired 2026-08-24), so this banner can only '
      + 'state the opposite of the truth. Nothing user-visible may key on '
      + 'that field — see the file banner in canvas/accounts.tsx')
  })

uiTest('§2 CONTROL: absent in the other payload state too — no gate survives',
  async ({ mount }) => {
    const text = await panelText(mount, payload(true))
    assert.match(text, /register current login/i,
      'panel rendered no content — the absence check below would abstain')
    assert.doesNotMatch(text, /no failover is running/i,
      'the banner renders when selection_active is true — someone re-added '
      + 'it with the gate inverted; the field is dead either way')
  })

// §3 — the panel must SURVIVE a response whose shape it did not expect.
//
// Added 2026-08-24 after a real crash: the token lookup dereferenced
// `.tokens` on any truthy response, so a payload without that key threw
// during render and took the WHOLE panel down. A blank panel passes no
// string check because there is no string.
//
// This is the shape a frontend talking to an older backend actually sees, and
// the serveAccounts stub reproduces it for free: it answers EVERY request with
// the accounts payload, tokens endpoint included. Re-anchored 2026-08-25 from
// the removed banner to the register button (rendered unconditionally).
uiTest('§3 an unexpected token-endpoint shape does not blank the panel',
  async ({ mount }) => {
    const text = await panelText(mount, payload(false))
    assert.match(text, /register current login/i,
      'the panel rendered nothing at all — a response without the key the '
      + 'token lookup expected threw during render and took every control '
      + 'down with it')
  })

// ════════════════════════════════════════════ the 2026-08-24 redesign (user):
// "minimal, comprehensible, show only what's necessary" — one fact (which
// account is serving) and one list (fallback keys, in the order they'll be
// tried, drag to reorder, removable). §4–§8 pin the redesign's CONTRACT, not
// its look: what's gone (pins), what the drag actually submits on the wire,
// and which endpoint a remove hits. Appearance stays the user's to judge.

import { inAct } from './harness'

const U = {
  a: '11111111-2222-3333-4444-555555555555',
  b: 'bbbbbbbb-2222-3333-4444-555555555555',
  c: 'cccccccc-2222-3333-4444-555555555555',
}

function acct(uuid: string, label: string): unknown {
  return {
    uuid, label, email_masked: label.toLowerCase() + '***@example.com',
    org_uuid: null, subscription_type: null, rate_limit_tier: null,
    account_created_at: null, source: 'adopted',
    first_seen: '2026-08-24T00:00:00Z', last_seen: '2026-08-24T00:00:00Z',
  }
}

/** three registered accounts: A serves, B and C hold keys (fallbacks) */
function world(over: {
  tokens?: Record<string, string>; pins?: Record<string, string>
  selection_active?: boolean
} = {}) {
  return {
    accounts: {
      version: 1, accounts: [acct(U.a, 'Main'), acct(U.b, 'Spare'), acct(U.c, 'Third')],
      primary: U.a, pins: over.pins ?? {},
      selection_active: over.selection_active ?? true,
    } as AccountsPayload,
    tokens: { tokens: over.tokens ?? { [U.b]: 'stored', [U.c]: 'stored' } },
    serving: { serving: U.a, label: 'Main' },
  }
}

interface Call { method: string; url: string; body: unknown }

/** route by URL and RECORD every request — §5/§6 assert on the wire, because
 *  "the drag reordered some array" proves nothing about what the server was
 *  told to persist */
function serveWorld(w: ReturnType<typeof world>, calls: Call[]): void {
  (globalThis as unknown as { fetch: unknown }).fetch =
    (url: string, init?: { method?: string; body?: string }) => {
      const method = init?.method ?? 'GET'
      const u = String(url)
      const body = init?.body ? JSON.parse(init.body) as unknown : undefined
      calls.push({ method, url: u, body })
      const reply = (b: unknown) => Promise.resolve({
        ok: true, status: 200,
        headers: new Headers({ 'X-Orgtree-Instance': 'inst-0' }),
        json: () => Promise.resolve(b),
      })
      if (u.includes('/api/accounts/tokens')) return reply(w.tokens)
      if (u.includes('/api/accounts/serving/')) return reply(w.serving)
      if (u.includes('/token')) return reply(w.tokens)
      if (u.includes('/api/accounts/order')) return reply(w.accounts)
      return reply(w.accounts)
    }
}

async function mountWorld(
  mount: (el: React.ReactElement) => Promise<{ el: HTMLElement }>,
  w: ReturnType<typeof world>, calls: Call[] = [],
): Promise<HTMLElement> {
  serveWorld(w, calls)
  const { AccountsPanel } = await import('../src/canvas/accounts')
  const { el } = await mount(<AccountsPanel slug="acme" toast={noop} close={noop} />)
  await flush()
  return el
}

const keyRows = (el: HTMLElement) => [...el.querySelectorAll('.acct-key')]

uiTest('§4 the pin surface is GONE, even when the payload still carries a pin',
  async ({ mount }) => {
    const el = await mountWorld(mount, world({ pins: { acme: U.b } }))
    // substring, NOT \b-bounded: textContent concatenates adjacent elements
    // with no whitespace ("Sparepinned", "primaryunpin"), so a word-boundary
    // regex matched nothing against the OLD panel and this check passed
    // vacuously — measured here before the redesign, the exact abstention
    // shape this repo keeps re-finding. No other panel word contains "pin".
    assert.doesNotMatch(el.textContent ?? '', /pin/i,
      'pin controls or chips rendered — the failover code never reads the pin '
      + '(zero references, measured 2026-08-24), so it must not be a surface')
  })

uiTest('§5 dropping one key on another submits the DRAGGED order to the server',
  async ({ mount }) => {
    const calls: Call[] = []
    const el = await mountWorld(mount, world(), calls)
    const rows = keyRows(el)
    assert.equal(rows.length, 2, 'two fallback keys (B, C) should list')
    // drag C onto B: C takes B's place, so the tried-order becomes C then B
    await inAct(() => {
      rows[1]!.dispatchEvent(new Event('dragstart', { bubbles: true }))
      rows[0]!.dispatchEvent(new Event('dragover', { bubbles: true, cancelable: true }))
      rows[0]!.dispatchEvent(new Event('drop', { bubbles: true, cancelable: true }))
    })
    await flush()
    const put = calls.find((c) => c.method === 'PUT' && c.url.includes('/api/accounts/order'))
    assert.ok(put, 'no PUT /api/accounts/order was made — the drag decided nothing')
    assert.deepEqual((put!.body as { order: string[] }).order, [U.a, U.c, U.b],
      'the submitted order must be [active, dragged-first key, then the rest] '
      + '— this IS the order failover walks, so a wrong submission here '
      + 'silently changes which account limits fall to')
  })

uiTest('§6 removing a key DELETEs that key’s token endpoint, nothing else',
  async ({ mount }) => {
    const calls: Call[] = []
    const el = await mountWorld(mount, world(), calls)
    const btn = [...el.querySelectorAll('button')]
      .find((b) => /remove key/i.test(b.textContent ?? ''))
    assert.ok(btn, 'no "remove key" control on a key row')
    await inAct(() => { (btn as HTMLButtonElement).click() })
    await flush()
    const del = calls.filter((c) => c.method === 'DELETE')
    assert.equal(del.length, 1, 'exactly one DELETE should have been made')
    assert.ok(del[0]!.url.includes(`/api/accounts/${U.b}/token`),
      `the DELETE went to ${del[0]!.url} — not the first-listed key's token `
      + 'endpoint; removing one key must not touch another')
  })

uiTest('§7 a registered account with no key still offers the add-key path',
  async ({ mount }) => {
    // C has no token: it is not a fallback, but it must remain the vehicle
    // for pasting one — otherwise a registered secondary is a dead end
    const el = await mountWorld(mount, world({ tokens: { [U.b]: 'stored' } }))
    assert.match(el.textContent ?? '', /add key/i,
      'no add-key control anywhere — a registered account without a token '
      + 'has no way to become a fallback')
  })

uiTest('§8 the serving account is stated by label, resolved not inferred',
  async ({ mount }) => {
    const el = await mountWorld(mount, world())
    const servingLine = el.querySelector('.acct-serving')
    assert.ok(servingLine, 'no serving line rendered')
    assert.match(servingLine!.textContent ?? '', /Main/,
      'the serving line does not name the account the server resolved')
  })
