// gallery.test.tsx — the documents gallery (user request 2026-09-03): one
// flat, org-wide, newest-first list of every presented-document card, so
// finding one back doesn't mean remembering which agent sent it.
//
// Run:  cd frontend && node tests/run.mjs gallery

import { flush, inAct, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { DocGalleryModal } from '../src/canvas/gallery'
import type { DocRow } from '../src/api'

/** a minimal fetch stub for exactly the one endpoint this modal calls — no
 *  need for the full FakeServer/installFetch chat machinery here. */
function mockDocuments(rows: DocRow[] | { status: number; detail: string }) {
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    ((url: string) => {
      assert.match(String(url), /\/documents$/, 'the modal must hit the list endpoint')
      const headers = new Headers()
      if (Array.isArray(rows)) {
        return Promise.resolve({ ok: true, status: 200, headers,
          json: () => Promise.resolve({ documents: rows }) })
      }
      return Promise.resolve({ ok: false, status: rows.status, headers,
        statusText: rows.detail,
        json: () => Promise.resolve({ detail: rows.detail }) })
    }) as typeof fetch
}

const row = (o: Partial<DocRow>): DocRow => ({
  id: 'd1', node: 'agent1', title: 'a plan', at: '2026-09-03T00:00:00.000Z',
  evicted: false, node_state: 'live', ...o,
})

function uiTest(name: string, body: (mount: (v: React.ReactElement)
  => Promise<{ el: HTMLElement }>) => Promise<void>) {
  test(name, async (t: TestContext) => {
    useFakeClock()
    let open: { el: HTMLElement; unmount: () => Promise<void> } | null = null
    t.after(async () => { try { await open?.unmount() } finally { realClock() } })
    await body(async (v) => {
      const view = await mountView(v, (host) => host)
      open = view
      return { el: view.el }
    })
  })
}

const rows = (el: HTMLElement) => [...el.querySelectorAll('.doc-gallery-row')]

uiTest('§1 an empty org shows the empty state, not a blank panel', async (mount) => {
  mockDocuments([])
  const { el } = await mount(
    <DocGalleryModal slug="org1" close={() => {}} onOpen={() => {}} />)
  await flush()
  assert.equal(rows(el).length, 0)
  assert.match(el.textContent ?? '', /no cards have been presented yet/)
})

uiTest('§2 live cards render title, node and timestamp; no evicted badge',
  async (mount) => {
    mockDocuments([row({ id: 'd1', title: 'first' })])
    const { el } = await mount(
      <DocGalleryModal slug="org1" close={() => {}} onOpen={() => {}} />)
    await flush()
    assert.equal(rows(el).length, 1)
    assert.match(rows(el)[0].textContent ?? '', /first/)
    assert.match(rows(el)[0].textContent ?? '', /agent1/)
    assert.equal(rows(el)[0].querySelector('.evicted'), null,
      'a live card carries no evicted badge')
  })

uiTest('§3 an evicted card still shows, badged, and is not clickable',
  async (mount) => {
    const opened: string[] = []
    mockDocuments([
      row({ id: 'dlive', title: 'still here' }),
      row({ id: 'd1', title: 'gone but logged', evicted: true }),
    ])
    const { el } = await mount(
      <DocGalleryModal slug="org1" close={() => {}} onOpen={(id) => opened.push(id)} />)
    await flush()
    const gone = rows(el).find((r) => r.classList.contains('evicted'))
    assert.ok(gone, 'evicted row missing')
    assert.match(gone.textContent ?? '', /gone but logged/)
    assert.match(gone.textContent ?? '', /content evicted/)
    await inAct(() => { (gone as HTMLElement).click() })
    assert.deepEqual(opened, [], 'an evicted row must not open the reader')
    const live = rows(el).find((r) => !r.classList.contains('evicted'))!
    await inAct(() => { (live as HTMLElement).click() })
    assert.deepEqual(opened, ['dlive'])
  })

uiTest('§4 a deleted presenting node badges as such, not omitted or blank',
  async (mount) => {
    mockDocuments([row({ id: 'd1', node: 'gone-agent', node_state: 'deleted' })])
    const { el } = await mount(
      <DocGalleryModal slug="org1" close={() => {}} onOpen={() => {}} />)
    await flush()
    assert.match(rows(el)[0].textContent ?? '', /deleted agent/)
  })

uiTest('§5 clicking a row hands its id off and closes the gallery', async (mount) => {
  mockDocuments([row({ id: 'the-doc-id' })])
  let opened: string | null = null
  let closed = false
  const { el } = await mount(
    <DocGalleryModal slug="org1"
      close={() => { closed = true }}
      onOpen={(id) => { opened = id }} />)
  await flush()
  await inAct(() => { (rows(el)[0] as HTMLElement).click() })
  assert.equal(opened, 'the-doc-id', 'the row click must open the reader on the right doc')
  assert.ok(closed, 'the gallery closes so the reader is not stacked under it')
})

uiTest('§6 a failed fetch does not invent rows (usePolled swallows like UsageModal)',
  async (mount) => {
    mockDocuments({ status: 500, detail: 'boom' })
    const { el } = await mount(
      <DocGalleryModal slug="org1" close={() => {}} onOpen={() => {}} />)
    await flush()
    assert.equal(rows(el).length, 0)
    assert.match(el.textContent ?? '', /loading/)
  })
