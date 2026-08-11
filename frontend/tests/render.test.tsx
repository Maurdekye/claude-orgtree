// render.test.tsx — what the desk and the mail list actually put in the DOM.
//
// §6 rendering edges: markdown, fences, wide tables (D-14), rich-text names,
//    unicode/RTL, an empty transcript, a thousand rows, tab switching, and an
//    unmount in the middle of a fetch.
// §7 D-56: paging on SCROLL rather than on a button, in the chat AND in the
//    mail list — the part of D-56 that shipped unverified.
// §8 two DOM views of one node, side by side, must render the same thing.
//
// Run:  cd frontend && node tests/run.mjs render

import {
  advance, FakeServer, flush, inAct, installFetch, mountView, realClock,
  useFakeClock,
} from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { refreshConvo, resetConvos } from '../src/convo'
import { DeskChat } from '../src/canvas/desk'
import { MailList } from '../src/canvas/mail'
import type { CanvasNode } from '../src/canvas/shared'
import type { MailRow } from '../src/canvas/shared'
import type { OpResult } from '../src/types'

let _n = 0
const noop = () => {}
const op = () => Promise.resolve({} as OpResult)

function node(id: string, extra: Partial<CanvasNode> = {}): CanvasNode {
  return {
    id, state: 'live', tier: 'haiku', children: [], seat: 1, grant: 0, free: 0,
    scope: { tools: {}, add_dirs: [] }, model_id: 'haiku', ...extra,
  }
}

function deskEl(nd: CanvasNode, slug: string, extra: Record<string, unknown> = {}) {
  return (
    <DeskChat node={nd} map={new Map([[nd.id, nd]])} op={op} slug={slug}
      toast={noop} pub={false} bare {...extra} />
  )
}

/** a test with the clock mocked, a fresh store key, a FakeServer wired up, and
 *  a teardown that survives a failed assertion (see convo.test.tsx) */
function domTest(name: string,
  body: (k: { SL: string; ND: string; s: FakeServer;
    mount: (el: React.ReactElement) => Promise<{ el: HTMLElement; unmount: () => Promise<void> }> }) => Promise<void>,
  opts: { todo?: string } = {}): void {
  test(name, opts.todo ? { todo: opts.todo } : {}, async (t: TestContext) => {
    useFakeClock()
    const SL = 'org'
    const ND = `d${++_n}`
    const s = new FakeServer()
    installFetch(s)
    const open: { unmount: () => Promise<void> }[] = []
    t.after(async () => {
      for (const m of open) { try { await m.unmount() } catch { /* gone */ } }
      resetConvos()
      realClock()
    })
    await body({
      SL, ND, s,
      mount: async (el) => {
        const v = await mountView(el, (host) => host)
        open.push(v)
        return { el: v.el, unmount: v.unmount }
      },
    })
  })
}

const txt = (el: HTMLElement) => el.textContent ?? ''
const rows = (el: HTMLElement, sel: string) => [...el.querySelectorAll(sel)]

/** jsdom does no layout, so scroll geometry is stated rather than measured —
 *  which is what makes the paging predicate testable at all */
function geometry(el: Element, g: { top: number; height: number; client: number }) {
  Object.defineProperty(el, 'scrollTop', { value: g.top, configurable: true, writable: true })
  Object.defineProperty(el, 'scrollHeight', { value: g.height, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: g.client, configurable: true })
}

async function scroll(el: Element, times = 1) {
  await inAct(() => {
    for (let i = 0; i < times; i++) {
      el.dispatchEvent(new (globalThis as unknown as { Event: typeof Event }).Event(
        'scroll', { bubbles: true }))
    }
  })
}

// ===================================================================== §6
// RENDERING EDGES
// ===================================================================== §6

domTest('§6.1 markdown, fences and literal angle brackets survive the pipeline',
  async ({ SL, ND, s, mount }) => {
    s.assistantMsg([
      '# heading', '', 'a `Sync<float3>` in prose and a bare <Token> too', '',
      '```ts', 'const x: Map<string, number> = new Map()', '```', '',
      '| a | b |', '| --- | --- |', '| 1 | 2 |',
    ].join('\n'))
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    assert.ok(el.querySelector('h1'), 'the heading rendered')
    assert.ok(el.querySelector('pre code'), 'the fence rendered as code')
    assert.ok(el.querySelector('table'), 'the table rendered')
    assert.ok(txt(el).includes('Sync<float3>'),
      'a generic in prose kept its angle brackets (№16)')
    assert.ok(txt(el).includes('<Token>'), 'a bare tag-shaped token survived')
    assert.ok(txt(el).includes('Map<string, number>'), 'and so did the one in the fence')
  })

domTest('§6.2 agent output cannot inject markup', async ({ SL, ND, s, mount }) => {
  // the sanitizer is the only thing between an agent echoing web content and
  // the page. If DOMPurify were inert (it silently no-ops without a DOM) this
  // suite would be testing nothing, so assert it is alive.
  s.assistantMsg('<img src=x onerror="globalThis.__pwned=1"> <script>globalThis.__pwned=1</script> plain')
  await refreshConvo(SL, ND)
  const { el } = await mount(deskEl(node(ND), SL))
  await flush()
  assert.equal(el.querySelector('script'), null, 'no script element')
  const img = el.querySelector('img')
  assert.ok(!img || !img.getAttribute('onerror'), 'no inline handler survived')
  assert.equal((globalThis as Record<string, unknown>).__pwned, undefined)
  assert.ok(txt(el).includes('plain'), 'and the safe text still rendered')
})

domTest('§6.3 unicode, RTL and combining marks round-trip',
  async ({ SL, ND, s, mount }) => {
    const samples = [
      'مرحبا بالعالم — RTL',
      'é́́ combining',
      '👨‍👩‍👧‍👦 family ZWJ · 🇯🇵 flag',
      'ｆｕｌｌｗｉｄｔｈ · ﷽',
      'zero​width',
    ]
    samples.forEach((x) => s.assistantMsg(x))
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    for (const x of samples) {
      assert.ok(txt(el).includes(x), `${JSON.stringify(x.slice(0, 12))} rendered intact`)
    }
  })

domTest('§6.4 a rich-text node name is shown, never interpreted',
  async ({ SL, ND, s, mount }) => {
    // slot/agent names carry rich text in this ecosystem ("<color=cyan>Systems")
    // and one session already lost a comparison to it
    await refreshConvo(SL, ND)
    const nd = node(ND, { charter: '<color=cyan>lead' })
    const { el } = await mount(deskEl(nd, SL))
    await flush()
    void s
    assert.ok(txt(el).includes(ND), 'the name renders')
    assert.equal(el.querySelector('color'), null, 'and no tag was created from it')
  })

domTest('§6.5 an empty transcript says so, once', async ({ SL, ND, mount }) => {
  await refreshConvo(SL, ND)
  const { el } = await mount(deskEl(node(ND), SL))
  await flush()
  assert.equal(rows(el, '.pad').filter((r) => txt(r as HTMLElement).includes('no conversation yet')).length, 1)
})

domTest('§6.6 a thousand rows render only one window',
  async ({ SL, ND, s, mount }) => {
    for (let i = 0; i < 3000; i++) s.assistantMsg(`row ${i}`)
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    const msgs = rows(el, '.msgs > div').length
    assert.ok(msgs <= 130, `the DOM carries ${msgs} rows for a 3000-row transcript`)
    assert.ok(txt(el).includes('row 2999'), 'and it is the NEWEST window')
    assert.ok(txt(el).includes('earlier messages'), 'with the status line explaining the rest')
  })

domTest('§6.7 rapid tab switching keeps the conversation',
  async ({ SL, ND, s, mount }) => {
    s.assistantMsg('the answer is 42')
    await refreshConvo(SL, ND)
    // the tab strip only exists on a full desk (not `bare`/`compact`)
    const { el } = await mount(deskEl(node(ND), SL, { bare: false }))
    await flush()
    const tab = (name: string) => rows(el, '.cc-tabs button')
      .find((b) => txt(b as HTMLElement).startsWith(name)) as HTMLElement | undefined
    for (let i = 0; i < 12; i++) {
      for (const name of ['history', 'files', 'inbox', 'chat']) {
        const b = tab(name)
        assert.ok(b, `the ${name} tab exists`)
        await inAct(() => { b!.click() })
      }
    }
    await advance(200)
    assert.ok(txt(el).includes('the answer is 42'), 'back on chat, the transcript is intact')
  })

domTest('§6.8 unmounting while a panel fetch is in flight is not an error',
  async ({ SL, ND, s, mount }) => {
    const errs: unknown[] = []
    const onErr = (e: unknown) => errs.push(e)
    process.on('unhandledRejection', onErr)
    s.latency = 4000
    const m = await mount(deskEl(node(ND), SL, { bare: false }))
    await flush()
    const hist = rows(m.el, '.cc-tabs button')
      .find((b) => txt(b as HTMLElement).startsWith('history')) as HTMLElement
    await inAct(() => { hist.click() })
    await advance(100)
    await m.unmount()
    await advance(8000)
    process.off('unhandledRejection', onErr)
    assert.deepEqual(errs, [], 'no rejection escaped the unmounted panel')
  })

domTest('§6.9 a very long single message renders whole and does not wedge',
  async ({ SL, ND, s, mount }) => {
    const big = Array.from({ length: 400 },
      (_, i) => `paragraph ${i} — ${'lorem ipsum '.repeat(20)}`).join('\n\n')
    s.assistantMsg(big)
    await refreshConvo(SL, ND)
    const t0 = Date.now()
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    void t0
    assert.ok(txt(el).includes('paragraph 0'), 'the head rendered')
    assert.ok(txt(el).includes('paragraph 399'), 'and so did the tail')
    assert.ok(rows(el, '.msgs p').length >= 400, 'every paragraph is a node')
  })

domTest('§6.10 a panel showing folder A never renders A’s rows under B',
  async ({ SL, ND, s, mount }) => {
    // usePolled keeps the last value across a deps change, so a panel whose
    // identity changed shows the OLD data until the new fetch lands. The
    // LineagePanel guards this case explicitly (`readingRef`); the polled
    // panels do not. Measured here on the files tab, which changes identity
    // on every folder click.
    void s
    const { el } = await mount(deskEl(node(ND), SL, { bare: false }))
    await flush()
    const files = rows(el, '.cc-tabs button')
      .find((b) => txt(b as HTMLElement).startsWith('files')) as HTMLElement
    await inAct(() => { files.click() })
    await advance(200)
    // slice(1): the first .hist-row is the breadcrumb, and it changes the
    // instant the path does — comparing it would make this test pass without
    // ever looking at the listing
    const shown = () => rows(el, '.msgs.files .hist-row').slice(1)
      .map((r) => txt(r as HTMLElement))
    const root = shown().join('|')
    // the next fetch is slow — the click must not leave the old listing up
    // under the new path
    s.latency = 3000
    const dir = rows(el, '.msgs.files button')
      .find((b) => txt(b as HTMLElement).includes('sub')) as HTMLElement | undefined
    assert.ok(dir, 'the scratch fixture must offer a subfolder, or this proves nothing')
    await inAct(() => { dir!.click() })
    await advance(100)
    assert.notEqual(shown().join('|'), root,
      'the previous folder’s listing is still on screen under the new path')
  })
  // ← PROMOTED 2026-08-11 (implementer): `usePolled` now resets to null when
  // `deps` change — in the hook, exactly where the redteam's report said the
  // fix belonged, never per call site. The read-ack bump (89fecd9) moved to a
  // separate `refreshKey` argument on purpose: it restarts the fetch on the
  // SAME identity, and resetting there would blank the inbox on every
  // mark-read.

// ===================================================================== §7
// D-56 — PAGING ON SCROLL
// ===================================================================== §7

domTest('§7.1 the chat pages in older messages on scroll, with no button',
  async ({ SL, ND, s, mount }) => {
    for (let i = 0; i < 3000; i++) s.assistantMsg(`row ${i}`)
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    assert.equal(rows(el, '.loadolder-status button').length, 0,
      'the status line is not a control')
    const msgs = el.querySelector('.msgs')!
    const before = rows(el, '.msgs > div').length
    geometry(msgs, { top: 10, height: 4000, client: 600 })
    await scroll(msgs)
    await advance(500)
    const after = rows(el, '.msgs > div').length
    assert.ok(after > before,
      `scrolling to the top loaded nothing (${before} → ${after} rows)`)
  })

domTest('§7.2 one chat gesture pages one window, and the cap is explained',
  async ({ SL, ND, s, mount }) => {
    for (let i = 0; i < 3000; i++) s.assistantMsg(`row ${i}`)
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    const msgs = el.querySelector('.msgs')!
    geometry(msgs, { top: 10, height: 4000, client: 600 })
    await scroll(msgs, 8)                 // one flick, eight scroll events
    await advance(500)
    const after = rows(el, '.msgs > div').length
    assert.ok(after <= 260,
      `one gesture paged ${after} rows in — more than one window`)
    // …and keep scrolling to the API's cap
    for (let i = 0; i < 20; i++) { await scroll(msgs); await advance(400) }
    assert.ok(txt(el).includes('beyond the window'),
      'at the cap the status line explains why scrolling up stopped working')
  })

domTest('§7.3 the mail list pages on scroll, only grows, and stops', async () => {
  useFakeClock()
  const mails: MailRow[] = Array.from({ length: 200 }, (_, i) => ({
    id: `m${i}`, from: 'agent', kind: 'message', body: `mail body ${i}`,
    at: new Date(1_700_000_000_000 - i * 60_000).toISOString(),
  }))
  const v = await mountView(<MailList delivered={mails} />, (h) => h)
  try {
    await flush()
    const list = v.el.querySelector('.mailer-list')!
    const count = () => v.el.querySelectorAll('.mailrow').length
    assert.equal(count(), 40, 'one window to start')
    assert.ok(txt(v.el).includes('160 earlier'), 'and a status line, not a button')
    assert.equal(v.el.querySelectorAll('.mailer-list button.loadolder').length, 0)
    // scroll to the bottom: the next window renders
    geometry(list, { top: 1000, height: 1700, client: 600 })
    await scroll(list)
    assert.ok(count() > 40, `paging on scroll did nothing (${count()} rows)`)
    const oneGesture = count()
    assert.ok(oneGesture <= 80, `one scroll event paged ${oneGesture} rows`)
    // a flick emits a burst of events before React can re-render
    await scroll(list, 8)
    assert.ok(count() <= oneGesture + 40,
      `a burst of 8 scroll events paged ${count() - oneGesture} extra rows in one gesture`)
    // page all the way and stop
    for (let i = 0; i < 40; i++) await scroll(list)
    assert.equal(count(), 200, 'everything is shown')
    assert.ok(!txt(v.el).includes('earlier'), 'and the status line is gone')
    await scroll(list, 5)
    assert.equal(count(), 200, 'further scrolling changes nothing')
  } finally {
    await v.unmount()
    realClock()
  }
})

// ===================================================================== §8
// TWO DOM VIEWS OF ONE NODE
// ===================================================================== §8

domTest('§8.1 a card and its switchboard panel render the same conversation',
  async ({ SL, ND, s, mount }) => {
    s.userMsg('what is the status')
    s.assistantMsg('all green')
    await refreshConvo(SL, ND)
    const card = await mount(deskEl(node(ND), SL, { bare: false }))
    const board = await mount(deskEl(node(ND), SL, { compact: true }))
    await flush()
    const body = (el: HTMLElement) =>
      rows(el, '.msgs > div').map((r) => txt(r as HTMLElement)).join('␟')
    assert.equal(body(board.el), body(card.el), 'identical at first paint')
    // …and after the world moves under them, with no event delivered at all
    s.assistantMsg('and one more thing')
    s.liveRow('tool', 'Read · notes.md', 'tid')
    await advance(9000)
    assert.equal(body(board.el), body(card.el), 'identical after a silent update')
    assert.ok(txt(card.el).includes('and one more thing'),
      'and both caught the new row from polling alone')
  })
