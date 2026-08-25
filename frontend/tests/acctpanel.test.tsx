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
  serving?: { serving: string; label: string; selection: string | null }
  selectFail?: { status: number; detail: string }
} = {}) {
  return {
    accounts: {
      version: 1, accounts: [acct(U.a, 'Main'), acct(U.b, 'Spare'), acct(U.c, 'Third')],
      primary: U.a, pins: over.pins ?? {},
      selection_active: over.selection_active ?? true,
    } as AccountsPayload,
    tokens: { tokens: over.tokens ?? { [U.b]: 'stored', [U.c]: 'stored' } },
    serving: over.serving ?? { serving: U.a, label: 'Main', selection: null },
    selectFail: over.selectFail,
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
      // PUT /api/accounts/selection/{slug} — answers with the CONTRACT shape:
      // the resolved serving fact plus the stored selection, same as the read
      if (u.includes('/api/accounts/selection/')) {
        if (w.selectFail) {
          return Promise.resolve({
            ok: false, status: w.selectFail.status,
            statusText: 'Unprocessable Entity',
            headers: new Headers({ 'X-Orgtree-Instance': 'inst-0' }),
            json: () => Promise.resolve({ detail: w.selectFail!.detail }),
          })
        }
        const uu = (body as { uuid: string | null }).uuid
        return reply(uu
          ? { serving: uu, label: uu === U.b ? 'Spare' : 'Third', selection: uu }
          : { serving: 'ambient', label: 'the signed-in login', selection: null })
      }
      if (u.includes('/api/accounts/serving/')) return reply(w.serving)
      if (u.includes('/token')) return reply(w.tokens)
      if (u.includes('/api/accounts/order')) return reply(w.accounts)
      return reply(w.accounts)
    }
}

async function mountWorld(
  mount: (el: React.ReactElement) => Promise<{ el: HTMLElement }>,
  w: ReturnType<typeof world>, calls: Call[] = [],
  toastFn: (lines: string[]) => void = noop,
): Promise<HTMLElement> {
  serveWorld(w, calls)
  const { AccountsPanel } = await import('../src/canvas/accounts')
  const { el } = await mount(<AccountsPanel slug="acme" toast={toastFn} close={noop} />)
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

// ═══════════════════════════════════ the 2026-08-25 selection control (D-144's
// successor): PUT /api/accounts/selection/{slug} with {uuid} selects, {uuid:
// null} clears back to the signed-in login. The contract's load-bearing
// distinction: the response's `serving` is the RESOLVED FACT (what the next
// turn authenticates as), `selection` the STORED INTENT beside it, and they
// legitimately disagree (a selection whose key was later removed resolves to
// ambient). §9–§12 pin the wire and that distinction, not the look.

uiTest('§9 "serve from this account" PUTs the selection; the RESPONSE drives the line',
  async ({ mount }) => {
    const calls: Call[] = []
    const el = await mountWorld(mount, world(), calls)
    const btn = [...el.querySelectorAll('.acct-key button')]
      .find((b) => /serve from this/i.test(b.textContent ?? ''))
    assert.ok(btn, 'no "serve from this account" control on a key row — the '
      + 'lever the dead pin UI only looked like it was is still missing')
    const servingReads = () => calls.filter((c) =>
      c.method === 'GET' && c.url.includes('/api/accounts/serving/')).length
    const readsBefore = servingReads()
    await inAct(() => { (btn as HTMLButtonElement).click() })
    await flush()
    const put = calls.find((c) =>
      c.method === 'PUT' && c.url.includes('/api/accounts/selection/acme'))
    assert.ok(put, 'no PUT /api/accounts/selection/acme was made — the click '
      + 'decided nothing on the wire')
    assert.deepEqual(put!.body, { uuid: U.b },
      'the PUT must carry the clicked row\'s uuid — a wrong body here '
      + 'silently serves the org from a different account')
    assert.match(el.querySelector('.acct-serving')?.textContent ?? '', /Spare/,
      'the serving line does not show what the PUT response resolved')
    assert.equal(servingReads(), readsBefore,
      'a serving re-fetch happened after the PUT — the contract says the '
      + 'response IS the new truth; a racing re-read can only disagree')
  })

uiTest('§10 the clear control exists only while a selection is stored, and PUTs null',
  async ({ mount }) => {
    // leg 1: nothing stored → no clear control (a "return to my login" button
    // with nothing to clear presents the default as if it were a choice)
    const el0 = await mountWorld(mount, world())
    assert.match(el0.textContent ?? '', /register current login/i,
      'panel rendered no content — the absence check below would abstain')
    assert.ok(![...el0.querySelectorAll('button')]
      .some((b) => /signed in as/i.test(b.textContent ?? '')),
      'the clear control rendered with no stored selection')
    // leg 2: selection stored → control present; clicking clears with uuid:null
    const calls: Call[] = []
    const el = await mountWorld(mount,
      world({ serving: { serving: U.b, label: 'Spare', selection: U.b } }), calls)
    const btn = [...el.querySelectorAll('button')]
      .find((b) => /signed in as/i.test(b.textContent ?? ''))
    assert.ok(btn, 'no way back — a stored selection offers no control to '
      + 'return the org to the signed-in login (the exact one-way ratchet '
      + 'this endpoint exists to end)')
    await inAct(() => { (btn as HTMLButtonElement).click() })
    await flush()
    const put = calls.find((c) =>
      c.method === 'PUT' && c.url.includes('/api/accounts/selection/acme'))
    assert.ok(put, 'no PUT was made — the clear decided nothing on the wire')
    assert.deepEqual(put!.body, { uuid: null },
      'clearing must send {uuid: null} explicitly — not omit the key, not DELETE')
    assert.match(el.querySelector('.acct-serving')?.textContent ?? '',
      /signed-in login/,
      'the serving line does not show what the cleared response resolved')
  })

uiTest('§11 a 422 from the selection write surfaces its detail, not a swallow',
  async ({ mount }) => {
    const toasts: string[][] = []
    const el = await mountWorld(mount,
      world({ selectFail: { status: 422, detail: 'account holds no stored key' } }),
      [], (lines) => { toasts.push(lines) })
    const btn = [...el.querySelectorAll('.acct-key button')]
      .find((b) => /serve from this/i.test(b.textContent ?? ''))
    assert.ok(btn, 'no select control to fail')
    await inAct(() => { (btn as HTMLButtonElement).click() })
    await flush()
    assert.ok(toasts.flat().some((t) => /holds no stored key/.test(t)),
      'the 422 detail never reached the user — a refused selection that says '
      + 'nothing reads exactly like a selection that worked')
    assert.match(el.textContent ?? '', /register current login/i,
      'the failed write blanked the panel')
  })

uiTest('§12 a selection that cannot serve is shown as setting, not stated as fact',
  async ({ mount }) => {
    // stored intent = B, but B's key was removed later: resolution fell back
    // to the login. Stating B as serving would be the banner bug again.
    const el = await mountWorld(mount, world({
      serving: { serving: 'ambient', label: 'the signed-in login', selection: U.b },
      tokens: {},
    }))
    assert.match(el.textContent ?? '', /not in effect/i,
      'nothing tells the user their stored selection is not what is serving — '
      + 'the panel states an intention as though it were a state')
    // control: when selection and resolution agree, no such note
    const el2 = await mountWorld(mount,
      world({ serving: { serving: U.b, label: 'Spare', selection: U.b } }))
    assert.match(el2.textContent ?? '', /register current login/i,
      'panel rendered no content — the absence check below would abstain')
    assert.doesNotMatch(el2.textContent ?? '', /not in effect/i,
      'the disagreement note renders even when the selection IS serving')
  })
