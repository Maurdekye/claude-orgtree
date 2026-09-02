// The OpenRouter section of App settings → Providers (user spec 2026-09-02):
// key entry → favorites row of monogram cards → model-selection modal with
// search, paged results (card · full name · vendor · $/1M in and out) and
// select/deselect. Driven through the rendered controls against a scripted
// fetch — the key is only ever SENT, and the section renders nothing of it.

import { flush, inAct, mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { OpenRouterSection } from '../src/canvas/openrouter'
import { openrouterTierIds, TIER_LETTER } from '../src/canvas/shared'
import type { OpenRouterDoc, OpenRouterModel, ProviderInfo } from '../src/types'

type Seen = { method: string; path: string; body: unknown }
const g = globalThis as unknown as Record<string, unknown>

const CATALOG: OpenRouterModel[] = [
  { id: 'anthropic/claude-sonnet-5', name: 'Anthropic: Claude Sonnet 5',
    vendor: 'anthropic', prompt: 2, completion: 10, cache_read: 0.2,
    context: 1000000, tools: true, free: false, letter: 'S', color: '#f9907f' },
  { id: 'openai/gpt-5.6-luna', name: 'OpenAI: GPT-5.6 Luna', vendor: 'openai',
    prompt: 0.2, completion: 1.2, cache_read: 0.02, context: 1050000,
    tools: true, free: false, letter: 'L', color: '#9fe3d1' },
  { id: 'moonshotai/kimi-k3', name: 'MoonshotAI: Kimi K3', vendor: 'moonshotai',
    prompt: 3, completion: 15, cache_read: 0.3, context: 1048576,
    tools: true, free: false, letter: 'K', color: '#8fc9e8' },
]

function stubFetch(seen: Seen[], opts: { keySet?: boolean } = {}) {
  let keySet = opts.keySet ?? false
  const favorites: OpenRouterModel[] = []
  const doc = (): OpenRouterDoc => ({
    installed: keySet, connected: keySet, key_set: keySet,
    kind: keySet ? 'api-key' : null, label: keySet ? 'sk-or-v1-abc…xyz' : null,
    credits: { limit: null, limit_remaining: null, usage: 1.25, usage_daily: 0.5,
      usage_weekly: 1.25, usage_monthly: 1.25, is_free_tier: false,
      checked_at: '2026-09-02T22:00:00Z' },
    reason: keySet ? null : 'no API key — add one in App settings → Providers',
    favorites: favorites.length, favorites_max: 0,
    tiers: favorites.map((m) => ({
      tier: 'or-' + m.id.replace(/[^a-z0-9]+/g, '-'), provider: 'openrouter',
      seat: Math.max(1, Math.floor(m.prompt)), model: m.id, letter: m.letter,
      color: m.color, name: m.name, vendor: m.vendor, prompt: m.prompt,
      completion: m.completion, context: m.context })),
    user_enabled: true,
  })
  g.fetch = (url: string, init?: RequestInit) => {
    const u = new URL(String(url), 'http://localhost')
    const path = u.pathname
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : null
    seen.push({ method, path, body })
    let payload: unknown = null
    if (path === '/api/openrouter' && method === 'GET') payload = doc()
    else if (path === '/api/openrouter/key' && method === 'PUT') {
      keySet = true; payload = doc()
    } else if (path === '/api/openrouter/key' && method === 'DELETE') {
      keySet = false; payload = doc()
    } else if (path === '/api/openrouter/models') {
      const q = (u.searchParams.get('q') ?? '').toLowerCase()
      const items = CATALOG.filter((m) => !q || m.id.includes(q) || m.name.toLowerCase().includes(q))
        .map((m) => ({ ...m, selected: favorites.some((f) => f.id === m.id) }))
      payload = { query: q, offset: 0, limit: 8, total: items.length, items }
    } else if (path === '/api/openrouter/favorites' && method === 'PUT') {
      const b = body as { id: string; selected: boolean }
      const m = CATALOG.find((c) => c.id === b.id)!
      if (b.selected && !favorites.some((f) => f.id === b.id)) favorites.push(m)
      if (!b.selected) favorites.splice(favorites.findIndex((f) => f.id === b.id), 1)
      payload = doc()
    }
    if (!payload) return Promise.reject(new Error(`unexpected ${method} ${path}`))
    return Promise.resolve({
      ok: true, status: 200, headers: new Headers(),
      json: () => Promise.resolve(payload),
    })
  }
}

const PROVIDER: ProviderInfo = {
  id: 'openrouter', label: 'OpenRouter', cli: 'REST API (via Claude Code)',
  tiers: [], status: { installed: true, connected: true, key_set: true },
  hire_enabled: true, user_enabled: true, reason: null,
}

async function mountSection(pickerState: { open: boolean }) {
  const view = await mountView(
    <OpenRouterSection provider={PROVIDER} toast={() => {}}
      pickerOpen={pickerState.open}
      setPickerOpen={(o) => { pickerState.open = o }} />, (el) => el)
  await inAct(async () => { await flush(10) })
  return view
}

test('§1 no key: the section offers key entry and nothing else; setting it '
  + 'PUTs the key and reveals the favorites row', async () => {
  const seen: Seen[] = []
  stubFetch(seen)
  const picker = { open: false }
  const view = await mountSection(picker)
  try {
    const input = view.el.querySelector<HTMLInputElement>(
      'input[aria-label="OpenRouter API key"]')
    assert.ok(input, 'key input rendered')
    assert.equal(input.type, 'password')
    assert.equal(view.el.querySelector('.orr-favs'), null, 'no favorites row yet')
    await inAct(async () => {
      // React tracks the input's value through the prototype setter; jsdom's
      // HTMLInputElement is reached via the element, not a global
      const setter = Object.getOwnPropertyDescriptor(
        Object.getPrototypeOf(input), 'value')!.set!
      setter.call(input, 'sk-or-v1-testkey-000000000000')
      input.dispatchEvent(new Event('input', { bubbles: true }))
      await flush(2)
    })
    // the entry row is the Claude section's "paste a new key" row: the ✓
    // spans the two button columns
    const btn = view.el.querySelector<HTMLButtonElement>('button.acct-add[title="set the key"]')!
    assert.ok(btn, 'the ✓ button sets the key')
    await inAct(async () => { btn.click(); await flush(10) })
    const put = seen.find((r) => r.method === 'PUT' && r.path === '/api/openrouter/key')
    assert.deepEqual(put?.body, { key: 'sk-or-v1-testkey-000000000000' })
    assert.equal(view.el.textContent?.includes('sk-or-v1-testkey'), false,
      'the key never renders')
    assert.ok(view.el.querySelector('.orr-favs'), 'favorites row appears once a key is set')
    // the key row: an ACCOUNT ROW — identity + verdict on line 1, the credit
    // standing on line 2, three icon buttons in the account rows' columns
    const row = view.el.querySelector('.acct-line > .acct-row.orr-keyrow')!
    assert.ok(row, 'the key row is an account row')
    const line1 = row.querySelector('.acct-main > .orr-standing')!
    assert.ok(line1.textContent?.includes('sk-or-v1-abc…xyz'), 'the label is the identity')
    assert.ok(line1.textContent?.includes('connected'), 'connected standing shown')
    const line2 = row.querySelector('.acct-provenance')!.textContent ?? ''
    for (const s of ['$1.25 spent', 'today $0.50', 'week $1.25', 'month $1.25']) {
      assert.ok(line2.includes(s), `credit standing carries "${s}"`)
    }
    const btns = [...row.querySelectorAll<HTMLButtonElement>('.acct-main > .acct-btn')]
    assert.deepEqual(btns.map((b) => b.title.split(' — ')[0]), [
      're-check the key and its credit standing at openrouter.ai',
      'replace the key', 'forget the key'])
    // ⚠ `.acct-btn` is a 27px ICON button: a word in it spills out of the box
    // and over its neighbours (the 2026-09-02 overlap). Icons only, ever.
    for (const b of view.el.querySelectorAll('.acct-btn')) {
      assert.equal(b.textContent?.trim(), '', `no text in an icon button (${b.getAttribute('title')})`)
      assert.ok(b.querySelector('svg'), 'an icon button carries an icon')
    }
    // replace → the entry row comes back with a ✕ that keeps the current key
    await inAct(async () => { btns[1]!.click(); await flush(4) })
    assert.ok(view.el.querySelector('input[aria-label="OpenRouter API key"]'),
      'replace opens the entry row')
    const keep = view.el.querySelector<HTMLButtonElement>('button[title="keep the current key"]')!
    assert.ok(keep, 'a ✕ keeps the current key')
    await inAct(async () => { keep.click(); await flush(4) })
    assert.ok(view.el.querySelector('.orr-keyrow .orr-standing'), 'the key row is back')
    assert.equal(seen.filter((r) => r.path === '/api/openrouter/key').length, 1,
      'replace/keep touched the key endpoint no further')
  } finally { await view.unmount(); delete g.fetch }
})

test('§2 the favorites row opens the picker; search, select and deselect '
  + 'drive PUT /favorites and the monogram cards', async () => {
  const seen: Seen[] = []
  stubFetch(seen, { keySet: true })
  const picker = { open: false }
  let view = await mountSection(picker)
  try {
    const row = view.el.querySelector<HTMLButtonElement>('.orr-favs')!
    assert.ok(row, 'favorites row present')
    assert.equal(row.getAttribute('aria-haspopup'), 'dialog')
    await inAct(async () => { row.click() })
    assert.equal(picker.open, true, 'the row asks the panel to open the picker')
  } finally { await view.unmount() }
  // remount with the picker open (the panel owns that state)
  view = await mountSection({ open: true })
  try {
    const dialog = view.el.querySelector('[role="dialog"]')
    assert.ok(dialog, 'the model-selection modal renders')
    await inAct(async () => { await flush(10) })
    const rows = view.el.querySelectorAll<HTMLButtonElement>('.orr-row')
    assert.equal(rows.length, 3, 'catalog rows listed')
    const first = rows[0]!
    assert.ok(first.textContent?.includes('Anthropic: Claude Sonnet 5'), 'full name')
    assert.ok(first.textContent?.includes('anthropic'), 'vendor')
    assert.ok(first.textContent?.includes('$2 in'), 'price in per 1M')
    assert.ok(first.textContent?.includes('$10 out'), 'price out per 1M')
    assert.ok(first.querySelector('.orr-card')?.textContent === 'S', 'monogram card letter')
    // search narrows
    const search = view.el.querySelector<HTMLInputElement>(
      'input[aria-label="search OpenRouter models"]')!
    await inAct(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        Object.getPrototypeOf(search), 'value')!.set!
      setter.call(search, 'luna')
      search.dispatchEvent(new Event('input', { bubbles: true }))
      await flush(4)
      await new Promise((r) => setTimeout(r, 260))
      await flush(6)
    })
    const narrowed = view.el.querySelectorAll<HTMLButtonElement>('.orr-row')
    assert.equal(narrowed.length, 1, 'search narrows the page')
    assert.ok(narrowed[0]!.textContent?.includes('best-effort'),
      'a non-Anthropic model is marked best-effort')
    // select it
    await inAct(async () => { narrowed[0]!.click(); await flush(10) })
    const put = seen.find((r) => r.method === 'PUT' && r.path === '/api/openrouter/favorites')
    assert.deepEqual(put?.body, { id: 'openai/gpt-5.6-luna', selected: true })
    const cards = view.el.querySelectorAll('.orr-favs .orr-card')
    assert.equal(cards.length, 1, 'the favorites row grew a card')
    assert.equal(cards[0]!.textContent, 'L')
    // …and the shared registry learned the runtime tier + letter
    assert.deepEqual(openrouterTierIds(), ['or-openai-gpt-5-6-luna'])
    assert.equal(TIER_LETTER['or-openai-gpt-5-6-luna'], 'L')
    assert.ok(document.getElementById('orgtree-openrouter-tiers')?.textContent
      ?.includes('.tier.t-or-openai-gpt-5-6-luna{color:#9fe3d1'),
      'generated tier CSS injected')
    // deselect
    const selectedRow = view.el.querySelector<HTMLButtonElement>('.orr-row.on')!
    await inAct(async () => { selectedRow.click(); await flush(10) })
    const put2 = seen.filter((r) => r.path === '/api/openrouter/favorites').at(-1)
    assert.deepEqual(put2?.body, { id: 'openai/gpt-5.6-luna', selected: false })
    assert.equal(view.el.querySelectorAll('.orr-favs .orr-card').length, 0)
  } finally { await view.unmount(); delete g.fetch }
})
