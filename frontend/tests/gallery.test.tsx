// gallery.test.tsx — the presented-documents gallery.
//
// Redesigned 2026-09-03 on the user's instruction to make it resemble the
// MAIL UI: "a list of entries on the left with their titles, submitted agent,
// and submission time, and a the scrollable document viewer on the right.
// only show non-dismissed documents from agents that are currently hired, and
// allow the dismissal of them from the viewer directly."
//
// The currently-hired filter is the DEFAULT, not the whole rule: asked
// directly (every card in the live org is from a retired agent, so the strict
// list opens empty), the user chose "default hired + 'show retired'". Both
// halves of that are pinned below — a filter that silently swallowed the
// archive and a toggle that failed to reveal it are the two ways this feature
// disappoints, and neither is visible without looking.
//
// Run:  cd frontend && node tests/run.mjs gallery

import { flush, inAct, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { DocGalleryModal } from '../src/canvas/gallery'
import { activeDocCount } from '../src/canvas/shared'
import type { DocCountNode } from '../src/canvas/shared'
import type { DocRow } from '../src/api'

interface Call { method: string; url: string }

/** stubs BOTH endpoints this panel touches: the list, and the per-document
 *  body its right-hand pane fetches on select. Records every call so a test
 *  can assert what was NOT requested (an evicted row must not fetch). */
function mockDocs(rows: DocRow[], bodies: Record<string, string> = {}): Call[] {
  const calls: Call[] = [];
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    ((url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const path = String(url)
      calls.push({ method, url: path })
      const headers = new Headers()
      const ok = (body: unknown) => Promise.resolve(
        { ok: true, status: 200, headers, json: () => Promise.resolve(body) })
      if (method === 'DELETE') return ok({ ok: true, node: 'agent1' })
      const m = path.match(/\/documents\/([^/?]+)$/)
      if (m) {
        const id = m[1]!
        const r = rows.find((x) => x.id === id)
        if (!r || bodies[id] == null) {
          return Promise.resolve({ ok: false, status: 404, headers,
            statusText: 'Not Found',
            json: () => Promise.resolve({ detail: `no document ${id}` }) })
        }
        return ok({ id, node: r.node, title: r.title, at: r.at, body: bodies[id] })
      }
      return ok({ documents: rows })
    }) as typeof fetch
  return calls
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

const noop = () => {}
const rows = (el: HTMLElement) => [...el.querySelectorAll('.mailrow')]
const pane = (el: HTMLElement) => el.querySelector('.mailer-read')
const showRetired = (el: HTMLElement) =>
  el.querySelector('.gallery-showretired input') as HTMLInputElement

const gallery = (extra?: Partial<{ close: () => void }>) => (
  <DocGalleryModal slug="org1" toast={noop} close={extra?.close ?? noop} />
)

uiTest('§1 an empty org says so rather than rendering a blank panel', async (mount) => {
  mockDocs([])
  const { el } = await mount(gallery())
  await flush()
  assert.equal(rows(el).length, 0)
  assert.match(el.textContent ?? '', /no cards have been presented yet/)
})

uiTest('§2 a row carries the three things the user asked for: title, agent, time',
  async (mount) => {
    mockDocs([row({ id: 'd1', title: 'the plan', node: 'planner' })])
    const { el } = await mount(gallery())
    await flush()
    assert.equal(rows(el).length, 1)
    // the mail row's own shape: strong line + time on .l1, detail on .l2
    assert.equal(rows(el)[0].querySelector('.l1 .mfrom')?.textContent, 'the plan',
      'the TITLE is the row headline, where a mail puts its sender')
    assert.ok((rows(el)[0].querySelector('.l1 .mtime')?.textContent ?? '').length,
      'the submission time rides the row, as mail does')
    assert.match(rows(el)[0].querySelector('.l2')?.textContent ?? '', /planner/,
      'the submitting agent is named on the row')
  })

uiTest('§3 THE DEFAULT IS CURRENTLY-HIRED ONLY — a retired agent\'s card is not '
  + 'in the default list, and the empty state says where it went', async (mount) => {
  mockDocs([
    row({ id: 'dret', title: 'from a retired agent', node: 'oldie', node_state: 'archived' }),
    row({ id: 'ddel', title: 'from a deleted agent', node: 'ghost', node_state: 'deleted' }),
  ])
  const { el } = await mount(gallery())
  await flush()
  assert.equal(rows(el).length, 0, 'archived and deleted presenters are filtered out')
  // …and the user is TOLD, rather than left thinking the gallery is broken
  assert.match(el.textContent ?? '', /no cards from currently-hired agents/)
  assert.match(el.textContent ?? '', /2 from retired ones/)
})

uiTest('§4 the checkbox adds retired cards to the SAME list, sorted below the '
  + 'active ones — one list, not a second view', async (mount) => {
  // the retired card is NEWER, so a plain newest-first sort would put it on
  // top: this fixture only passes if the grouping actually happens
  mockDocs([
    row({ id: 'dret', title: 'from a retired agent', node: 'oldie',
      node_state: 'archived', at: '2026-09-03T09:00:00.000Z' }),
    row({ id: 'dlive', title: 'from a live agent',
      at: '2026-09-03T08:00:00.000Z' }),
  ])
  const { el } = await mount(gallery())
  await flush()
  assert.equal(rows(el).length, 1, 'default shows only the live agent\'s card')
  await inAct(() => showRetired(el).click())
  await flush()
  const shown = rows(el)
  assert.equal(shown.length, 2, 'the checkbox brings the retired card into the list')
  assert.match(shown[0]!.textContent ?? '', /from a live agent/,
    'the ACTIVE agent\'s card sorts first even though it is older')
  assert.match(shown[1]!.textContent ?? '', /from a retired agent/,
    'and the retired one sits below it')
  // the way back, so the checkbox is not a one-way door
  await inAct(() => showRetired(el).click())
  await flush()
  assert.equal(rows(el).length, 1)
})

uiTest('§4b active and retired rows are told apart by CLASS, not by a badge '
  + '(the user asked for the "retired" card to go)', async (mount) => {
  // ⚠ neither title nor agent id may contain "retired", or the
  // doesNotMatch below would pass/fail on the fixture's own words
  mockDocs([
    row({ id: 'dlive', title: 'a current plan' }),
    row({ id: 'dret', title: 'an older plan', node: 'oldie',
      node_state: 'archived' }),
  ])
  const { el } = await mount(gallery())
  await flush()
  await inAct(() => showRetired(el).click())
  await flush()
  const [live, past] = rows(el)
  assert.ok(live!.classList.contains('active'),
    'an active agent\'s row carries the unread-mail accent class')
  assert.ok(past!.classList.contains('past'), 'a retired one is marked secondary')
  assert.equal(past!.querySelector('.badge'), null,
    'NO "retired" badge in the row — greying is the whole signal')
  assert.doesNotMatch(past!.textContent ?? '', /retired/i,
    'and the word does not appear in the row either')
  // …but it stays reachable on hover, so grey is never the only explanation
  assert.match(past!.getAttribute('title') ?? '', /retired/)
})

uiTest('§4c each entry shows its agent\'s model chip', async (mount) => {
  mockDocs([
    row({ id: 'dlive', title: 'live one', tier: 'opus' }),
    row({ id: 'dgone', title: 'no node left', node: 'ghost',
      node_state: 'deleted', tier: null }),
  ])
  const { el } = await mount(gallery())
  await flush()
  await inAct(() => showRetired(el).click())
  await flush()
  const chip = rows(el)[0]!.querySelector('.tier')
  assert.ok(chip, 'the row carries the model chip')
  assert.ok(chip.classList.contains('t-opus'), 'coloured by tier, like everywhere else')
  assert.equal(chip.textContent, 'O', 'and wearing the shared tier letter')
  assert.equal(rows(el)[1]!.querySelector('.tier'), null,
    'a deleted node has no tier to show — no empty chip')
})

uiTest('§5 the viewer is a PANE, not a takeover: selecting a row renders the body '
  + 'beside the list and leaves the gallery open', async (mount) => {
  mockDocs([row({ id: 'd1', title: 'the plan' })], { d1: '# heading\n\nthe body text' })
  let closed = false
  const { el } = await mount(gallery({ close: () => { closed = true } }))
  await flush()
  assert.match(pane(el)?.textContent ?? '', /select a document to read it/,
    'nothing is selected on open — the pane invites a click, as mail does')
  await inAct(() => { (rows(el)[0] as HTMLElement).click() })
  await flush()
  assert.match(pane(el)?.textContent ?? '', /the body text/,
    'the fetched markdown renders in the right-hand pane')
  assert.ok(el.querySelector('.mailer-body'), 'it is the mail reading pane markup')
  assert.equal(rows(el).length, 1, 'the list is still there beside it')
  assert.equal(closed, false, 'reading a document must not close the gallery')
})

uiTest('§6 dismiss lives in the viewer and actually deletes that document',
  async (mount) => {
    const calls = mockDocs([row({ id: 'd1', title: 'the plan' })], { d1: 'body' })
    const { el } = await mount(gallery())
    await flush()
    assert.equal(el.querySelector('.mailer-head button'), null,
      'no dismiss control before a document is open')
    await inAct(() => { (rows(el)[0] as HTMLElement).click() })
    await flush()
    const btn = [...el.querySelectorAll('.mailer-head button')]
      .find((b) => (b.textContent ?? '').includes('dismiss')) as HTMLElement
    assert.ok(btn, 'the viewer carries the dismiss control (user request)')
    await inAct(() => btn.click())
    await flush()
    const del = calls.filter((c) => c.method === 'DELETE')
    assert.equal(del.length, 1, 'exactly one delete')
    assert.match(del[0]!.url, /\/documents\/d1$/, 'and it names the open document')
  })

uiTest('§7 an evicted card is listed and explained, fetches nothing, and offers '
  + 'no dismiss', async (mount) => {
  const calls = mockDocs([
    row({ id: 'dgone', title: 'gone but logged', evicted: true }),
  ])
  const { el } = await mount(gallery())
  await flush()
  assert.equal(rows(el).length, 1, 'an evicted card is still findable — that is the point')
  await inAct(() => { (rows(el)[0] as HTMLElement).click() })
  await flush()
  assert.match(pane(el)?.textContent ?? '', /content of this card is gone/,
    'the pane explains the empty body instead of showing a raw 404')
  assert.equal(calls.filter((c) => /\/documents\/dgone$/.test(c.url)).length, 0,
    'no body request for a card whose body is known to be gone')
  assert.equal([...el.querySelectorAll('.mailer-head button')]
    .filter((b) => (b.textContent ?? '').includes('dismiss')).length, 0,
  'nothing to dismiss — the card is already gone')
})

uiTest('§8 CONTROL: the same fixture with the presenter still hired DOES list — '
  + 'so §3 is a filter firing, not an empty render', async (mount) => {
  mockDocs([row({ id: 'dret', title: 'from a retired agent', node: 'oldie' })])
  const { el } = await mount(gallery())
  await flush()
  assert.equal(rows(el).length, 1,
    'flipping only node_state to live makes the row appear — §3 saw the filter')
})

// ── the toolbar button's corner count ────────────────────────────────────
// Counted off the TREE rather than the gallery's own fetch, so the button
// costs no extra poll. That makes its agreement with the list a real claim
// worth pinning: two sources, one number.

test('§9 the badge counts documents from CURRENTLY HIRED agents only', () => {
  const node = (o: Partial<DocCountNode> & { state: string }): DocCountNode => ({
    documents: null, children: [], ...o,
  })
  assert.equal(activeDocCount(null), 0, 'no tree yet reads zero, not NaN')
  assert.equal(activeDocCount([]), 0)
  assert.equal(activeDocCount([
    node({ state: 'live', documents: [{ id: 'a' }, { id: 'b' }] }),
  ]), 2)
  // …a retired agent's cards are NOT in the badge, matching the default list
  assert.equal(activeDocCount([
    node({ state: 'archived', documents: [{ id: 'a' }, { id: 'b' }] }),
  ]), 0, 'a retired agent contributes nothing — the badge matches the list')
  assert.equal(activeDocCount([
    node({ state: 'unrecoverable', documents: [{ id: 'a' }] }),
  ]), 0)
  // …and it counts the WHOLE tree, not just the roots
  assert.equal(activeDocCount([
    node({ state: 'live', documents: [{ id: 'a' }],
      children: [
        node({ state: 'live', documents: [{ id: 'b' }, { id: 'c' }] }),
        node({ state: 'archived', documents: [{ id: 'd' }] }),
      ] }),
  ]), 3, 'a deep live report is counted; a deep retired one is not')
})
