// docket.test.tsx — test suite for the native work docket and inbox navigation.
import './harness'
import { flush, inAct, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { DocketModal, DocketToolbarButton } from '../src/canvas/docket'
import { InboxPanel, SenderChip } from '../src/App'
import { NodeInboxModal, OrgInboxModal } from '../src/canvas/mail'
import type { AskInfo, CanvasNode, MailEntry, MailPayload, OrgInboxEntry, TreePayload, TreeNode, WorkItem } from '../src/types'

interface Call { method: string; url: string; body?: unknown }

function mockWorkItems(activeItems: WorkItem[], archivedItems: WorkItem[] = [],
                       extraCalls?: Call[], backlogItems: WorkItem[] = []): Call[] {
  const calls: Call[] = extraCalls ?? [];
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    ((url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const path = String(url)
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method, url: path, body })
      const headers = new Headers()
      const ok = (payload: unknown) => Promise.resolve(
        { ok: true, status: 200, headers, json: () => Promise.resolve(payload) })

      if (method === 'POST' && path.includes('/dismiss-attention')) {
        const m = path.match(/\/work-items\/([^/]+)\/dismiss-attention$/)
        const id = m ? m[1] : ''
        const found = [...activeItems, ...archivedItems, ...backlogItems]
          .find((x) => x.slug === id)
        return ok({ item: found ? { ...found, manual_attention: null, status: 'blocked' } : null })
      }
      if (method === 'POST' && path.includes('/reply')) {
        const m = path.match(/\/work-items\/([^/]+)\/reply$/)
        const id = m ? m[1] : ''
        const found = [...activeItems, ...archivedItems].find((x) => x.slug === id)
        const isDeferred = found?.last_updater?.node === 'archived-agent'
        return ok({ accepted: true, to: found?.last_updater?.node ?? 'agent', deferred: isDeferred })
      }
      if (method === 'GET' && path.includes('/work-items')) {
        // the two filters are INDEPENDENT query flags, and a group is served
        // only when its flag is set — the same contract ledger.work_list keeps
        const wantArch = path.includes('archived=1')
        const wantBack = path.includes('backlogged=1')
        return ok({
          items: activeItems,
          ...(wantArch ? { archived: archivedItems } : {}),
          ...(wantBack ? { backlogged: backlogItems } : {}),
          counts: {
            attention: activeItems.filter((x) => x.effective_attention).length,
            active: activeItems.filter((x) => x.status !== 'backlogged').length,
            archived: archivedItems.length,
            backlogged: backlogItems.length,
          },
          now: '2026-09-05T10:00:00.000Z',
        })
      }
      if (method === 'GET' && path.includes('/inbox')) {
        return ok({ pending: [], delivered: [], sent: [] })
      }
      return ok({})
    }) as typeof fetch
  return calls
}

// ⚠ ROWS ARE NAMED BY SLUG NOW (user 2026-09-05), not by title. A fixture that
// gives each item a distinct TITLE but leaves the default slug would render N
// rows all reading "test-work-item", and every ordering, grouping and
// "which row is this" assertion below would go vacuous while still passing.
// So a test that names a title and not a slug gets that title as its slug.
// Tests that care about slug SHAPE (kebab, substrings, boundaries) pass one
// explicitly, and the reference-linking suite uses realistic slugs throughout.
const mkItem = (o: Partial<WorkItem>): WorkItem => ({
  ...mkItemBase(o),
  ...(o.title !== undefined && o.slug === undefined ? { slug: o.title } : {}),
})

const mkItemBase = (o: Partial<WorkItem>): WorkItem => ({
  slug: 'test-work-item',
  rev: 1,
  kind: 'code',
  title: 'Test Work Item',
  objective: 'Test objective',
  status: 'in_progress',
  blocked_reason: null,
  archived: false,
  archived_at: null,
  owner: { node: 'agent1', generation: 1 },
  owner_current: true,
  owner_state: 'live',
  participants: [],
  created_by: { node: 'agent1', generation: 1 },
  at: '2026-09-05T08:00:00.000Z',
  updated_at: '2026-09-05T09:00:00.000Z',
  done_so_far: ['First step completed'],
  working_on_next: ['Second step in progress'],
  docket_at: '2026-09-05T09:00:00.000Z',
  last_updater: { node: 'agent1', generation: 1 },
  manual_attention: null,
  dismissals: [],
  questions: [],
  effective_attention: false,
  attention_sources: [],
  acceptance: [],
  dependencies: [],
  evidence: [],
  delivery: null,
  accepted: null,
  superseded_by: null,
  history: [],
  ...o,
})

const mkTree = (o?: Partial<TreePayload>): TreePayload => ({
  slug: 'org1',
  name: 'Org 1',
  epoch: 1,
  rev: 1,
  roots: [],
  work_items_summary: { attention: 0, active: 0 },
  user_inbox_count: 0,
  user_inbox_urgent_count: 0,
  asks: [],
  asks_open: 0,
  ...o,
})

function uiTest(name: string, body: (mount: (v: React.ReactElement)
  => Promise<{ el: HTMLElement; render: (v: React.ReactElement) => Promise<unknown> }>)
  => Promise<void>) {
  test(name, async (t: TestContext) => {
    useFakeClock()
    let open: { el: HTMLElement; unmount: () => Promise<void> } | null = null
    t.after(async () => { try { await open?.unmount() } finally { realClock() } })
    await body(async (v) => {
      const view = await mountView(v, (host) => host)
      open = view
      // `render` re-renders the SAME root with new props — mounting a second
      // view would test a fresh component, which is the opposite of asking
      // what happens to state that is already there
      return { el: view.el, render: view.render }
    })
  })
}

const noop = () => {}
const rows = (el: HTMLElement) => [...el.querySelectorAll('.mailrow.docket-row')]
const pane = (el: HTMLElement) => el.querySelector('.mailer-read')
const showArchivedBox = (el: HTMLElement) =>
  el.querySelector('.docket-showarchived input') as HTMLInputElement
/** the arrangement is a PERSISTED preference, so a test that asserts the
 *  default must clear it first — otherwise it silently inherits whatever the
 *  previous test chose, and "the default is No group" stops being tested */
const forgetGroupChoice = () => window.localStorage.removeItem('orgtree.docket.group')
const showBacklogBox = (el: HTMLElement) =>
  el.querySelector('.docket-showbacklog input') as HTMLInputElement
const groupSelect = (el: HTMLElement) =>
  el.querySelector('.docket-group-select') as HTMLSelectElement
const titles = (el: HTMLElement) =>
  rows(el).map((r) => r.querySelector('.l1 .mfrom')?.textContent ?? '')
const headings = (el: HTMLElement) =>
  [...el.querySelectorAll('.docket-group-head > span:first-child')]
    .map((h) => h.textContent ?? '')
/** drive the <select> the way a user does, through its change handler */
async function chooseGroup(el: HTMLElement, value: string) {
  const sel = groupSelect(el)
  await inAct(() => {
    sel.value = value
    sel.dispatchEvent(new window.Event('change', { bubbles: true }))
  })
  await flush()
}

const docketModal = (extra?: Partial<{
  close: () => void
  onFocusAgent: (id: string) => void
  tree: TreePayload
  toast: (lines: string[]) => void
  slug: string
}>) => (
  <DocketModal slug={extra?.slug ?? 'org1'} toast={extra?.toast ?? noop}
    close={extra?.close ?? noop}
    tree={extra?.tree ?? mkTree()} onFocusAgent={extra?.onFocusAgent} />
)

uiTest('§1 an empty org says so rather than rendering a blank panel', async (mount) => {
  mockWorkItems([])
  const { el } = await mount(docketModal())
  await flush()
  assert.equal(rows(el).length, 0)
  assert.match(el.textContent ?? '', /no work items yet/)
})

uiTest('§2 the row is NAMED BY ITS SLUG, and carries status, time and updater', async (mount) => {
  mockWorkItems([mkItem({
    slug: 'build-the-work-docket',
    title: 'Build the work docket',
    status: 'in_progress',
    docket_at: '2026-09-05T09:55:00.000Z',
    last_updater: { node: 'luna-reserve', generation: 1 },
  })])
  const { el } = await mount(docketModal())
  await flush()
  assert.equal(rows(el).length, 1)
  const r = rows(el)[0]!
  assert.equal(r.querySelector('.l1 .mfrom')?.textContent, 'build-the-work-docket')
  assert.ok((r.querySelector('.l1 .mtime')?.textContent ?? '').length > 0)
  assert.match(r.querySelector('.l2')?.textContent ?? '', /In progress/)
  assert.equal(r.querySelector('.l2 .docket-updater')?.textContent, 'luna-reserve')
  // THE DESCRIPTIVE TITLE IS NOT PRINTED IN THE LIST (user 2026-09-05) — it is
  // the row's hover text and nothing else. Asserting only "the slug is there"
  // would pass on a row that printed both.
  assert.equal(r.getAttribute('title'), 'Build the work docket')
  assert.ok(!(r.textContent ?? '').includes('Build the work docket'),
    'the descriptive title is still printed in the list row')
})

uiTest('§3 left row updater is last updater, not necessarily owner', async (mount) => {
  mockWorkItems([mkItem({
    title: 'Item A',
    owner: { node: 'astras-entrance-exam', generation: 1 },
    last_updater: { node: 'luna-reserve', generation: 1 },
  })])
  const { el } = await mount(docketModal())
  await flush()
  const r = rows(el)[0]!
  assert.equal(r.querySelector('.l2 .docket-updater')?.textContent, 'luna-reserve')
  assert.ok(!r.querySelector('.l2')?.textContent?.includes('astras-entrance-exam'))
})

uiTest('§4 active, attention and archived rows get correct classes and labels', async (mount) => {
  mockWorkItems([
    mkItem({ title: 'Active Item', status: 'in_progress', effective_attention: false, archived: false }),
    mkItem({ title: 'Attn Item', status: 'blocked', effective_attention: true, attention_sources: ['manual'], archived: false }),
  ], [
    mkItem({ title: 'Archived Item', status: 'done', effective_attention: false, archived: true }),
  ])
  const { el } = await mount(docketModal())
  await flush()
  // Reveal archived
  await inAct(() => showArchivedBox(el).click())
  await flush()

  const rList = rows(el)
  assert.equal(rList.length, 3)
  assert.ok(rList[0]!.classList.contains('active'))
  assert.match(rList[0]!.querySelector('.l2')?.textContent ?? '', /In progress/)

  assert.ok(rList[1]!.classList.contains('attention'))
  assert.match(rList[1]!.querySelector('.l2')?.textContent ?? '', /Needs attention/)

  assert.ok(rList[2]!.classList.contains('archived'))
  assert.match(rList[2]!.querySelector('.l2')?.textContent ?? '', /Done/)
})

uiTest('§5 show archived checkbox toggles archived items below active retaining recency order', async (mount) => {
  mockWorkItems([
    mkItem({ title: 'Active 1' }),
  ], [
    mkItem({ title: 'Archived 1', archived: true }),
    mkItem({ title: 'Archived 2', archived: true }),
  ])
  const { el } = await mount(docketModal())
  await flush()
  assert.equal(rows(el).length, 1, 'archived hidden initially')

  await inAct(() => showArchivedBox(el).click())
  await flush()
  const rList = rows(el)
  assert.equal(rList.length, 3, 'archived shown below active')
  assert.equal(rList[0]!.querySelector('.l1 .mfrom')?.textContent, 'Active 1')
  assert.equal(rList[1]!.querySelector('.l1 .mfrom')?.textContent, 'Archived 1')
  assert.equal(rList[2]!.querySelector('.l1 .mfrom')?.textContent, 'Archived 2')
})

uiTest('§6 right pane displays done so far and working on / next lists, with None when empty', async (mount) => {
  mockWorkItems([mkItem({
    title: 'Item with partial lists',
    done_so_far: ['Created schemas', 'Added endpoints'],
    working_on_next: [],
  })])
  const { el } = await mount(docketModal())
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()

  const p = pane(el)!
  assert.ok(p, 'pane rendered')
  const lists = p.querySelectorAll('.docket-list')
  assert.equal(lists.length, 2)

  const doneHeading = lists[0]!.querySelector('.docket-list-heading')?.textContent
  assert.equal(doneHeading, 'DONE SO FAR')
  const doneItems = [...lists[0]!.querySelectorAll('li')].map((li) => li.textContent)
  assert.deepEqual(doneItems, ['Created schemas', 'Added endpoints'])

  const nextHeading = lists[1]!.querySelector('.docket-list-heading')?.textContent
  assert.equal(nextHeading, 'WORKING ON / NEXT')
  const nextEmpty = lists[1]!.querySelector('.docket-list-empty')?.textContent
  assert.equal(nextEmpty, 'None')
})

uiTest('§7 right pane updater name is a clickable agent jump that closes modal', async (mount) => {
  let focused: string | null = null
  let closed = false
  mockWorkItems([mkItem({
    title: 'Work Item',
    last_updater: { node: 'luna-reserve', generation: 1 },
  })])
  const { el } = await mount(docketModal({
    close: () => { closed = true },
    onFocusAgent: (id) => { focused = id },
  }))
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()

  const subUpdater = el.querySelector('.docket-pane-sub button.cc-name-jump') as HTMLButtonElement
  assert.ok(subUpdater, 'clickable updater link in subtitle')
  assert.equal(subUpdater.textContent?.trim(), 'luna-reserve')
  await inAct(() => subUpdater.click())
  assert.ok(closed, 'modal was closed on agent click')
  assert.equal(focused, 'luna-reserve', 'focused last updater agent')
})

uiTest('§8 manual attention box renders reason and clickable author', async (mount) => {
  let focused: string | null = null
  let closed = false
  mockWorkItems([mkItem({
    title: 'Work Item',
    effective_attention: true,
    attention_sources: ['manual'],
    manual_attention: {
      reason: 'Need user confirmation on wire format',
      at: '2026-09-05T09:40:00.000Z',
      by: { node: 'codex-checklist', generation: 1 },
      set_rev: 2,
    },
  })])
  const { el } = await mount(docketModal({
    close: () => { closed = true },
    onFocusAgent: (id) => { focused = id },
  }))
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()

  const attnBox = el.querySelector('.docket-attention-box')
  assert.ok(attnBox, 'attention box rendered')
  assert.match(attnBox.textContent ?? '', /Need user confirmation on wire format/)

  const authorBtn = attnBox.querySelector('button.cc-name-jump') as HTMLButtonElement
  assert.ok(authorBtn, 'author button rendered')
  assert.equal(authorBtn.textContent?.trim(), 'codex-checklist')
  await inAct(() => authorBtn.click())
  assert.ok(closed)
  assert.equal(focused, 'codex-checklist')
})

uiTest('§9 question box renders attached AskCard and clickable asker', async (mount) => {
  let focused: string | null = null
  let closed = false
  const ask: AskInfo = {
    id: 'ask-1',
    node: 'luna-route-check',
    at: '2026-09-05T09:30:00.000Z',
    work_items: ['w1'],
    tabs: [{ index: 0, question: 'Should we keep v3 format?', work_item: 'w1' }],
  }
  const tree = mkTree({ asks: [ask], asks_open: 1 })
  mockWorkItems([mkItem({
    title: 'Work Item',
    effective_attention: true,
    attention_sources: ['question'],
    questions: [{
      ask_id: 'ask-1',
      node: 'luna-route-check',
      rev: 1,
      at: '2026-09-05T09:30:00.000Z',
      tabs: [{ index: 0, question: 'Should we keep v3 format?' }],
    }],
  })])
  const { el } = await mount(docketModal({
    tree,
    close: () => { closed = true },
    onFocusAgent: (id) => { focused = id },
  }))
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()

  const qBox = el.querySelector('.docket-question-box')
  assert.ok(qBox, 'question box rendered')
  const askerBtn = qBox.querySelector('.docket-question-head button.cc-name-jump') as HTMLButtonElement
  assert.ok(askerBtn, 'asker jump link rendered')
  assert.equal(askerBtn.textContent?.trim(), 'luna-route-check')

  await inAct(() => askerBtn.click())
  assert.ok(closed)
  assert.equal(focused, 'luna-route-check')
})

uiTest('§10 batch note appears when ask covers other items too', async (mount) => {
  const ask: AskInfo = {
    id: 'ask-multi',
    node: 'agent-asker',
    at: '2026-09-05T09:30:00.000Z',
    work_items: ['w1', 'w2'],
    tabs: [
      { index: 0, question: 'Question about w1', work_item: 'w1' },
      { index: 1, question: 'Question about w2', work_item: 'w2' },
    ],
  }
  const tree = mkTree({ asks: [ask], asks_open: 1 })
  mockWorkItems([mkItem({
    title: 'Work Item',
    effective_attention: true,
    attention_sources: ['question'],
    questions: [{
      ask_id: 'ask-multi',
      node: 'agent-asker',
      rev: 1,
      at: '2026-09-05T09:30:00.000Z',
      tabs: [{ index: 0, question: 'Question about w1' }],
    }],
  })])
  const { el } = await mount(docketModal({ tree }))
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()

  const note = el.querySelector('.docket-question-note')
  assert.ok(note, 'note rendered')
  assert.match(note.textContent ?? '', /this batch also covers other items — answering it resolves every tab at once/)
})

uiTest('§11 dismiss manual attention button calls endpoint with set_rev and updates refreshed row state', async (mount) => {
  let toasted: string[] = []
  let itemState = mkItem({
    title: 'Work Item',
    status: 'blocked',
    effective_attention: true,
    attention_sources: ['manual'],
    manual_attention: {
      reason: 'Need review',
      at: '2026-09-05T09:00:00.000Z',
      by: { node: 'agent1', generation: 1 },
      set_rev: 7,
    },
  })
  const calls: { url: string; method: string; body?: unknown }[] = []
  const headers = new Headers()
  const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, headers, json: () => Promise.resolve(body) })

  ;(globalThis as unknown as { fetch: typeof fetch }).fetch = ((url: string, init?: RequestInit) => {
    const path = String(url)
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ url: path, method, body })
    if (path.includes('/work-items/') && path.includes('/dismiss-attention')) {
      itemState = {
        ...itemState,
        effective_attention: false,
        attention_sources: [],
        manual_attention: null,
      }
      return ok({ ok: true, dismissed: true })
    }
    if (path.includes('/work-items')) {
      return ok({
        items: [itemState],
        counts: { attention: itemState.effective_attention ? 1 : 0, active: 1, archived: 0 },
        now: '2026-09-05T12:00:00.000Z',
      })
    }
    return ok({})
  }) as typeof fetch

  const { el } = await mount(docketModal({ toast: (t) => { toasted = t } }))
  await flush()

  // Row starts in attention state
  let row = el.querySelector('.mailrow') as HTMLElement
  assert.ok(row.classList.contains('attention'), 'row starts with attention class')
  assert.match(row.querySelector('.l2')?.textContent ?? '', /Needs attention/)

  const dismissBtn = el.querySelector('.mailrow .docket-dismiss') as HTMLButtonElement
  assert.ok(dismissBtn, 'dismiss button on row')
  await inAct(() => dismissBtn.click())
  await flush()

  const dismissCalls = calls.filter((c) => c.method === 'POST' && c.url.includes('/dismiss-attention'))
  assert.equal(dismissCalls.length, 1)
  assert.deepEqual(dismissCalls[0]!.body, { set_rev: 7 })
  assert.match(toasted[0] ?? '', /dismissed the attention flag/)

  // Refreshed row turns to its underlying status ('Blocked') and loses attention styling
  row = el.querySelector('.mailrow') as HTMLElement
  assert.ok(row.classList.contains('active'), 'row now has active class')
  assert.ok(!row.classList.contains('attention'), 'row lost attention class')
  assert.match(row.querySelector('.l2')?.textContent ?? '', /Blocked/)
  assert.doesNotMatch(row.querySelector('.l2')?.textContent ?? '', /Needs attention/)
  assert.equal(el.querySelector('.docket-dismiss'), null, 'dismiss button gone after clearing manual flag')
})

uiTest('§11b question+manual attention item stays in attention state after manual dismiss', async (mount) => {
  let toasted: string[] = []
  let itemState = mkItem({
    title: 'Multi-Attention Item',
    status: 'blocked',
    effective_attention: true,
    attention_sources: ['question', 'manual'],
    manual_attention: {
      reason: 'Urgent check',
      at: '2026-09-05T09:00:00.000Z',
      by: { node: 'agent2', generation: 1 },
      set_rev: 12,
    },
    questions: [{
      ask_id: 'q1',
      node: 'agent2',
      rev: 1,
      at: '2026-09-05T09:00:00.000Z',
      tabs: [{ index: 0, question: 'Continue?' }],
    }],
  })
  const headers = new Headers()
  const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, headers, json: () => Promise.resolve(body) })

  ;(globalThis as unknown as { fetch: typeof fetch }).fetch = ((url: string, init?: RequestInit) => {
    const path = String(url)
    if (path.includes('/dismiss-attention')) {
      itemState = {
        ...itemState,
        effective_attention: true,
        attention_sources: ['question'],
        manual_attention: null,
      }
      return ok({ ok: true, dismissed: true })
    }
    if (path.includes('/work-items')) {
      return ok({
        items: [itemState],
        counts: { attention: 1, active: 1, archived: 0 },
        now: '2026-09-05T12:00:00.000Z',
      })
    }
    return ok({})
  }) as typeof fetch

  const { el } = await mount(docketModal({ toast: (t) => { toasted = t } }))
  await flush()

  let row = el.querySelector('.mailrow') as HTMLElement
  assert.ok(row.classList.contains('attention'))
  assert.match(row.querySelector('.l2')?.textContent ?? '', /Needs attention/)

  const dismissBtn = el.querySelector('.mailrow .docket-dismiss') as HTMLButtonElement
  assert.ok(dismissBtn, 'dismiss button on row')
  await inAct(() => dismissBtn.click())
  await flush()

  assert.match(toasted[0] ?? '', /dismissed the attention flag/)

  // Stays attention after dismiss because question is still attached
  row = el.querySelector('.mailrow') as HTMLElement
  assert.ok(row.classList.contains('attention'), 'row still has attention class due to remaining question')
  assert.match(row.querySelector('.l2')?.textContent ?? '', /Needs attention/)
  assert.equal(el.querySelector('.docket-dismiss'), null, 'dismiss button gone because manual flag was cleared')
})

uiTest('§12 dismiss button is ABSENT when attention is question-only', async (mount) => {
  mockWorkItems([mkItem({
    title: 'Work Item',
    effective_attention: true,
    attention_sources: ['question'],
    manual_attention: null,
  })])
  const { el } = await mount(docketModal())
  await flush()
  assert.equal(el.querySelector('.docket-dismiss'), null, 'no dismiss button for question-only attention')
})

uiTest('§13 general reply box targets last updater and handles deferred (archived) recipient', async (mount) => {
  let toasted: string[] = []
  const calls = mockWorkItems([mkItem({
    id: 'w-arch',
    title: 'Work Item',
    last_updater: { node: 'archived-agent', generation: 1 },
  })])
  const { el } = await mount(docketModal({ toast: (t) => { toasted = t } }))
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()

  const replyLabel = el.querySelector('.docket-reply-label')
  assert.match(replyLabel?.textContent ?? '', /Reply to archived-agent · last updated this item/)

  const textarea = el.querySelector('.mail-reply textarea') as HTMLTextAreaElement
  assert.ok(textarea, 'textarea exists')
  await inAct(() => {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
    nativeSetter?.call(textarea, 'Great work!')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await flush()

  const sendBtn = el.querySelector('.mail-reply button') as HTMLButtonElement
  assert.ok(!sendBtn.disabled)
  await inAct(() => sendBtn.click())
  await flush()

  const replyCalls = calls.filter((c) => c.method === 'POST' && c.url.includes('/reply'))
  assert.equal(replyCalls.length, 1)
  assert.deepEqual(replyCalls[0]!.body, { body: 'Great work!' })
  assert.match(toasted[0] ?? '', /archived-agent is archived — the reply waits for rehire/)
})

uiTest('§13b reply box preserves draft on HTTP failure and clears on successful retry', async (mount) => {
  let toasted: string[] = []
  let shouldFail = true
  const calls: { url: string; method: string; body?: unknown }[] = []
  const headers = new Headers()
  const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, headers, json: () => Promise.resolve(body) })
  const err = (status: number, detail: string) => Promise.resolve({
    ok: false, status, statusText: detail, headers,
    json: () => Promise.resolve({ detail }),
  })

  ;(globalThis as unknown as { fetch: typeof fetch }).fetch = ((url: string, init?: RequestInit) => {
    const path = String(url)
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ url: path, method, body })
    if (path.includes('/reply')) {
      if (shouldFail) {
        return err(500, 'temporary network failure')
      }
      return ok({ ok: true, deferred: false })
    }
    if (path.includes('/work-items')) {
      return ok({
        items: [mkItem({
          id: 'w-retry',
          title: 'Retry Item',
          last_updater: { node: 'target-agent', generation: 1 },
        })],
        counts: { attention: 0, active: 1, archived: 0 },
        now: '2026-09-05T12:00:00.000Z',
      })
    }
    return ok({})
  }) as typeof fetch

  const { el } = await mount(docketModal({ toast: (t) => { toasted = t } }))
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()

  const textarea = el.querySelector('.mail-reply textarea') as HTMLTextAreaElement
  const sendBtn = el.querySelector('.mail-reply button') as HTMLButtonElement

  await inAct(() => {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
    nativeSetter?.call(textarea, 'Draft message to preserve')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await flush()
  assert.equal(textarea.value, 'Draft message to preserve')

  // First attempt: fails with 500
  await inAct(() => sendBtn.click())
  await flush()

  assert.match(toasted[0] ?? '', /error: temporary network failure/)
  // Draft MUST be preserved in textarea!
  assert.equal(textarea.value, 'Draft message to preserve', 'draft preserved on HTTP failure')
  assert.ok(!sendBtn.disabled, 'send button re-enabled after failure')

  // Second attempt: succeeds
  shouldFail = false
  toasted = []
  await inAct(() => sendBtn.click())
  await flush()

  assert.match(toasted[0] ?? '', /sent to target-agent/)
  // Draft MUST be cleared on success!
  assert.equal(textarea.value, '', 'draft cleared on successful retry')
})

uiTest('§14 InboxPanel sender chip is clickable agent jump that closes inbox', async (mount) => {
  let focused: string | null = null
  let closed = false
  const tree = mkTree({
    roots: [{ id: 'worker-1', tier: 'sonnet', state: 'live', parent: null, children: [] } as unknown as TreeNode],
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = (() => Promise.resolve({
    ok: true, status: 200, headers: new Headers(),
    json: () => Promise.resolve({
      pending: [{ id: 'm1', from: 'worker-1', to: '@user', at: '2026-09-05T09:00:00.000Z', body: 'Hello user' }],
      delivered: [],
      sent: [],
    }),
  })) as typeof fetch

  const { el } = await mount(
    <InboxPanel slug="org1" tree={tree} toast={noop} jumpTo={null}
      close={() => { closed = true }}
      onFocusAgent={(id) => { focused = id }} />
  )
  await flush()
  const mRow = el.querySelector('.mailer-list .mailrow') as HTMLElement
  assert.ok(mRow, 'mail row exists')
  await inAct(() => mRow.click())
  await flush()

  const jumpBtn = el.querySelector('.mailer-read .mailer-head button.cc-name-jump') as HTMLButtonElement
  assert.ok(jumpBtn, 'clickable agent jump button in mailer-head')
  assert.match(jumpBtn.textContent ?? '', /worker-1/)
  await inAct(() => jumpBtn.click())
  assert.ok(closed, 'inbox closed on jump')
  assert.equal(focused, 'worker-1', 'focused agent')
})

uiTest('§15 InboxPanel system and user senders are NOT clickable jumps', async (mount) => {
  const tree = mkTree();
  (globalThis as unknown as { fetch: typeof fetch }).fetch = (() => Promise.resolve({
    ok: true, status: 200, headers: new Headers(),
    json: () => Promise.resolve({
      pending: [{ id: 'm-sys', from: 'system', to: '@user', at: '2026-09-05T09:00:00.000Z', body: 'System note' }],
      delivered: [],
      sent: [],
    }),
  })) as typeof fetch

  const { el } = await mount(
    <InboxPanel slug="org1" tree={tree} toast={noop} jumpTo={null}
      close={noop} onFocusAgent={noop} />
  )
  await flush()
  const mRow = el.querySelector('.mailer-list .mailrow') as HTMLElement
  await inAct(() => mRow.click())
  await flush()

  const jumpBtn = el.querySelector('.mailer-read .mailer-head button.cc-name-jump')
  assert.equal(jumpBtn, null, 'system sender is not clickable')
})

uiTest('§16 NodeInboxModal counterparty is clickable agent jump that closes modal', async (mount) => {
  let focused: string | null = null
  let closed = false
  const node: CanvasNode = { id: 'agent-a', tier: 'sonnet', state: 'live', role: 'Worker', x: 0, y: 0 } as CanvasNode
  (globalThis as unknown as { fetch: typeof fetch }).fetch = (() => Promise.resolve({
    ok: true, status: 200, headers: new Headers(),
    json: () => Promise.resolve({
      pending: [{ id: 'm1', from: 'agent-peer', to: 'agent-a', at: '2026-09-05T09:00:00.000Z', body: 'Peer message' }],
      delivered: [],
      sent: [],
    }),
  })) as typeof fetch

  const { el } = await mount(
    <NodeInboxModal node={node} slug="org1" jumpTo={null}
      close={() => { closed = true }}
      // the tree's answer for this id. Without a resolver MailList claims no
      // local jump at all — a handler alone used to be read as "yes", which is
      // the phantom jump mailsender §12 removes.
      hasAgent={(id) => id === 'agent-peer'}
      onFocusAgent={(id) => { focused = id }} />
  )
  await flush()
  const mRow = el.querySelector('.mailer-list .mailrow') as HTMLElement
  assert.ok(mRow, 'mail row exists')
  await inAct(() => mRow.click())
  await flush()

  const jumpBtn = el.querySelector('.mailer-read .mailer-head button.cc-name-jump') as HTMLButtonElement
  assert.ok(jumpBtn, 'counterparty agent button exists')
  assert.equal(jumpBtn.textContent?.trim(), 'agent-peer')
  await inAct(() => jumpBtn.click())
  assert.ok(closed, 'modal closed')
  assert.equal(focused, 'agent-peer')
})

uiTest('§17 OrgInboxModal inbox sender is external peer and stays plain (no agent jump)', async (mount) => {
  let focused: string | null = null
  let closed = false
  const map = new Map<string, CanvasNode>();
  (globalThis as unknown as { fetch: typeof fetch }).fetch = ((url: string) => {
    const path = String(url)
    const headers = new Headers()
    const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, headers, json: () => Promise.resolve(body) })
    if (path.includes('/org_inbox')) {
      return ok({
        entries: [
          { id: 'oi-1', peer: 'external-client', by: null, dir: 'in', at: '2026-09-05T09:00:00.000Z', body: 'Incoming external' },
        ],
        total: 1,
        unread: 1,
      })
    }
    return ok({})
  }) as typeof fetch

  const orgInbox: TreePayload['org_inbox'] = {
    unread: 1,
    entries: [
      { id: 'oi-1', peer: 'external-client', by: null, dir: 'in', at: '2026-09-05T09:00:00.000Z', body: 'Incoming external' } as unknown as OrgInboxEntry,
    ],
  }
  const { el } = await mount(
    <OrgInboxModal inbox={orgInbox} map={map} slug="org1" toast={noop}
      close={() => { closed = true }}
      onFocusAgent={(id) => { focused = id }} />
  )
  await flush()
  const mRow = el.querySelector('.mailer-list .mailrow') as HTMLElement
  assert.ok(mRow, 'org inbox row exists')
  await inAct(() => mRow.click())
  await flush()

  // External peer in inbox is plain text, NEVER a clickable agent jump
  const jumpBtn = el.querySelector('.mailer-read .mailer-head button.cc-name-jump')
  assert.equal(jumpBtn, null, 'external peer has no focus jump')
  const senderB = el.querySelector('.mailer-read .mailer-head b')
  assert.equal(senderB?.textContent?.trim(), 'external-client', 'external peer renders as plain text')
  assert.equal(closed, false)
  assert.equal(focused, null)
})

uiTest('§18 OrgInboxModal outbox @by links resolvable local agent, while recipient stays plain', async (mount) => {
  let focused: string | null = null
  let closed = false
  const map = new Map<string, CanvasNode>([
    ['agent-sender', { id: 'agent-sender', tier: 'sonnet', state: 'live', x: 0, y: 0, w: 100, h: 100, rx: 0, ry: 0, text: '' } as unknown as CanvasNode],
  ]);
  (globalThis as unknown as { fetch: typeof fetch }).fetch = ((url: string) => {
    const path = String(url)
    const headers = new Headers()
    const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, headers, json: () => Promise.resolve(body) })
    if (path.includes('/org_inbox')) {
      return ok({
        entries: [
          { id: 'oi-out-1', peer: 'external-client', by: 'agent-sender', dir: 'out', at: '2026-09-05T09:00:00.000Z', body: 'Outbound body' },
        ],
        total: 1,
        unread: 0,
      })
    }
    return ok({})
  }) as typeof fetch

  const orgInbox: TreePayload['org_inbox'] = {
    unread: 0,
    entries: [
      { id: 'oi-out-1', peer: 'external-client', by: 'agent-sender', dir: 'out', at: '2026-09-05T09:00:00.000Z', body: 'Outbound body' } as unknown as OrgInboxEntry,
    ],
  }
  const { el } = await mount(
    <OrgInboxModal inbox={orgInbox} map={map} slug="org1" toast={noop}
      close={() => { closed = true }}
      onFocusAgent={(id) => { focused = id }} />
  )
  await flush()
  // switch to sent folder
  const sentBtn = [...el.querySelectorAll('.mail-folders button')].find((b) => b.textContent?.includes('sent')) as HTMLElement
  assert.ok(sentBtn, 'sent folder button exists')
  await inAct(() => sentBtn.click())
  await flush()

  const mRow = el.querySelector('.mailer-list .mailrow') as HTMLElement
  assert.ok(mRow, 'outbox mail row exists')
  await inAct(() => mRow.click())
  await flush()

  // Only the local agent @agent-sender is a clickable jump; external recipient stays plain!
  const jumpBtns = [...el.querySelectorAll('.mailer-read .mailer-head button.cc-name-jump')] as HTMLButtonElement[]
  assert.equal(jumpBtns.length, 1, 'only resolvable local sender is a clickable jump')
  assert.equal(jumpBtns[0]!.textContent?.trim(), '@agent-sender')

  await inAct(() => jumpBtns[0]!.click())
  assert.ok(closed, 'clicking jump closes modal')
  assert.equal(focused, 'agent-sender', 'focuses local agent')
})

uiTest('§19 multiple questions on an item render separate question boxes with respective askers', async (mount) => {
  const tree = mkTree({
    asks: [
      { id: 'a1', node: 'asker-1', at: '2026-09-05T09:00:00.000Z', tabs: [{ index: 0, question: 'Q1' }] },
      { id: 'a2', node: 'asker-2', at: '2026-09-05T09:05:00.000Z', tabs: [{ index: 0, question: 'Q2' }] },
    ],
    asks_open: 2,
  })
  mockWorkItems([mkItem({
    title: 'Item with 2 questions',
    effective_attention: true,
    attention_sources: ['question'],
    questions: [
      { ask_id: 'a1', node: 'asker-1', rev: 1, at: '2026-09-05T09:00:00.000Z', tabs: [{ index: 0, question: 'Q1' }] },
      { ask_id: 'a2', node: 'asker-2', rev: 1, at: '2026-09-05T09:05:00.000Z', tabs: [{ index: 0, question: 'Q2' }] },
    ],
  })])
  const { el } = await mount(docketModal({ tree }))
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()

  const qBoxes = el.querySelectorAll('.docket-question-box')
  assert.equal(qBoxes.length, 2, 'two distinct question boxes rendered')
  assert.match(qBoxes[0]!.textContent ?? '', /Question from asker-1/)
  assert.match(qBoxes[1]!.textContent ?? '', /Question from asker-2/)
})

uiTest('§20 production DocketToolbarButton displays orange attention count or muted active count', async (mount) => {
  let clicked = false
  // Attention case
  const view1 = await mountView(<DocketToolbarButton summary={{ attention: 3, active: 5 }} onClick={() => { clicked = true }} />, (e) => e)
  const badge1 = view1.el.querySelector('.eye-count')!
  assert.ok(badge1.classList.contains('docket-attn'), 'orange styling applied when attention > 0')
  assert.equal(badge1.textContent?.trim(), '3')
  await inAct(() => (view1.el.querySelector('button') as HTMLButtonElement).click())
  assert.ok(clicked, 'click triggers onClick')
  await view1.unmount()

  // Quiet case
  const view2 = await mountView(<DocketToolbarButton summary={{ attention: 0, active: 7 }} />, (e) => e)
  const badge2 = view2.el.querySelector('.eye-count')!
  assert.ok(!badge2.classList.contains('docket-attn'), 'no orange class when attention is 0')
  assert.equal(badge2.textContent?.trim(), '7', 'shows active count when attention is 0')
  await view2.unmount()

  // Zero active
  const view3 = await mountView(<DocketToolbarButton summary={{ attention: 0, active: 0 }} />, (e) => e)
  const badge3 = view3.el.querySelector('.eye-count')!
  assert.ok(!badge3.classList.contains('docket-attn'))
  assert.equal(badge3.textContent?.trim(), '0', 'zero is muted and not hidden')
  await view3.unmount()
})

uiTest('§21 show archived toggle preserves detail selection and in-flight draft without full-panel reload', async (mount) => {
  mockWorkItems([
    mkItem({ title: 'Active Item', status: 'in_progress', last_updater: { node: 'agent1', generation: 1 } }),
  ], [
    mkItem({ title: 'Archived Item', status: 'done', archived: true }),
  ])
  const { el } = await mount(docketModal())
  await flush()

  // Select active item w1
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()

  const textarea = el.querySelector('.mailer-read textarea') as HTMLTextAreaElement
  assert.ok(textarea, 'textarea rendered in detail pane')
  const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
  await inAct(() => {
    nativeSetter?.call(textarea, 'in-flight draft reply')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  })
  assert.equal(textarea.value, 'in-flight draft reply')

  // Toggle Show archived ON
  await inAct(() => showArchivedBox(el).click())
  await flush()

  // Verify full-panel reload did NOT happen (no loading... screen)
  assert.ok(!el.textContent?.includes('loading…'), 'panel does not flash loading on archive toggle')
  // Verify rows appended
  assert.equal(rows(el).length, 2, 'archived row appended')
  // Verify detail pane is STILL open on w1
  assert.ok(el.querySelector('.mailer-read'), 'detail pane remains open')
  const retainedTextarea = el.querySelector('.mailer-read textarea') as HTMLTextAreaElement
  assert.ok(retainedTextarea, 'textarea still exists in detail pane')
  assert.equal(retainedTextarea.value, 'in-flight draft reply', 'draft preserved across archive toggle')

  // Toggle Show archived OFF
  await inAct(() => showArchivedBox(el).click())
  await flush()

  assert.equal(rows(el).length, 1, 'archived row removed')
  const stillTextarea = el.querySelector('.mailer-read textarea') as HTMLTextAreaElement
  assert.ok(stillTextarea, 'detail pane still open')
  assert.equal(stillTextarea.value, 'in-flight draft reply', 'draft still preserved')
})

uiTest('§22 entry styling colors entries by status', async (mount) => {
  mockWorkItems([
    mkItem({ title: 'In Progress Item', status: 'in_progress' }),
    mkItem({ title: 'Blocked Item', status: 'blocked' }),
    mkItem({ title: 'Review Item', status: 'review' }),
    mkItem({ title: 'Open Item', status: 'open' }),
    mkItem({ title: 'Done Item', status: 'done' }),
  ])
  const { el } = await mount(docketModal())
  await flush()

  const rList = rows(el)
  assert.equal(rList.length, 5)

  assert.ok(rList[0]!.classList.contains('status-in_progress'), 'has status-in_progress class')
  assert.ok(rList[0]!.querySelector('.docket-status.status-in_progress'), 'status element has status-in_progress class')

  assert.ok(rList[1]!.classList.contains('status-blocked'), 'has status-blocked class')
  assert.ok(rList[1]!.querySelector('.docket-status.status-blocked'), 'status element has status-blocked class')

  assert.ok(rList[2]!.classList.contains('status-review'), 'has status-review class')
  assert.ok(rList[2]!.querySelector('.docket-status.status-review'), 'status element has status-review class')

  assert.ok(rList[3]!.classList.contains('status-open'), 'has status-open class')
  assert.ok(rList[3]!.querySelector('.docket-status.status-open'), 'status element has status-open class')

  assert.ok(rList[4]!.classList.contains('status-done'), 'has status-done class')
  assert.ok(rList[4]!.querySelector('.docket-status.status-done'), 'status element has status-done class')
})

uiTest('§23 three grouping modes; archive and backlog stay last in every one', async (mount) => {
  // the server hands rows back newest-first with a total order already applied
  mockWorkItems([
    mkItem({ title: 'In Progress New', status: 'in_progress', docket_at: '2026-09-05T10:40:00.000Z', owner: { node: 'ana', generation: 1 } }),
    mkItem({ title: 'Open New', status: 'open', docket_at: '2026-09-05T10:30:00.000Z', owner: { node: 'bo', generation: 1 } }),
    mkItem({ title: 'Blocked Mid', status: 'blocked', docket_at: '2026-09-05T10:20:00.000Z', owner: null }),
    mkItem({ title: 'In Progress Old', status: 'in_progress', docket_at: '2026-09-05T10:00:00.000Z', owner: { node: 'ana', generation: 1 } }),
  ], [
    mkItem({ title: 'Archived One', status: 'done', archived: true }),
  ], undefined, [
    mkItem({ title: 'Backlog One', status: 'backlogged' }),
  ])
  forgetGroupChoice()
  const { el } = await mount(docketModal())
  await flush()
  await inAct(() => showArchivedBox(el).click())
  await flush()
  await inAct(() => showBacklogBox(el).click())
  await flush()

  const opts = [...groupSelect(el).options].map((o) => o.value)
  assert.deepEqual(opts, ['none', 'status', 'agent'], 'exactly three arrangements')
  assert.equal(groupSelect(el).value, 'none', 'no grouping by default')

  // NO GROUP: the order the server chose, untouched, then the appended groups
  assert.deepEqual(titles(el), ['In Progress New', 'Open New', 'Blocked Mid',
    'In Progress Old', 'Backlog One', 'Archived One'])
  assert.deepEqual(headings(el),
    ['Backlogged — not yet approached', 'Archived'],
    'ungrouped mode heads only the two appended groups')

  // BY STATUS: blocked, in_progress, review, open (attention first when present)
  await chooseGroup(el, 'status')
  assert.deepEqual(headings(el), ['Blocked', 'In progress', 'Open',
    'Backlogged — not yet approached', 'Archived'])
  assert.deepEqual(titles(el), ['Blocked Mid', 'In Progress New', 'In Progress Old',
    'Open New', 'Backlog One', 'Archived One'])

  // BY AGENT: owner, most recently active first, Unassigned named and last
  await chooseGroup(el, 'agent')
  assert.deepEqual(headings(el), ['ana', 'bo', 'Unassigned',
    'Backlogged — not yet approached', 'Archived'])
  assert.deepEqual(titles(el), ['In Progress New', 'In Progress Old', 'Open New',
    'Blocked Mid', 'Backlog One', 'Archived One'])

  // THE INVARIANT: in all three, the last two rows are the two appended groups
  // — ticking a filter can only ever add to the bottom of the list
  for (const mode of ['none', 'status', 'agent']) {
    await chooseGroup(el, mode)
    assert.deepEqual(titles(el).slice(-2), ['Backlog One', 'Archived One'], mode)
  }
})

uiTest('§23b attention outranks every status group, and an unknown status is still reachable', async (mount) => {
  mockWorkItems([
    mkItem({ title: 'Open Plain', status: 'open' }),
    mkItem({ title: 'Flagged Open', status: 'open', effective_attention: true, attention_sources: ['manual'] }),
    mkItem({ title: 'Blocked Plain', status: 'blocked' }),
    mkItem({ title: 'Odd', status: 'invented_later' }),
  ])
  forgetGroupChoice()
  const { el } = await mount(docketModal())
  await flush()
  await chooseGroup(el, 'status')
  assert.deepEqual(headings(el), ['Needs attention', 'Blocked', 'Open', 'Other closed'])
  assert.deepEqual(titles(el), ['Flagged Open', 'Blocked Plain', 'Open Plain', 'Odd'])
})

uiTest('§23c the chosen arrangement persists across a remount', async (mount) => {
  mockWorkItems([mkItem({ title: 'Only', status: 'open' })])
  forgetGroupChoice()
  const { el } = await mount(docketModal())
  await flush()
  assert.equal(groupSelect(el).value, 'none', 'the default with nothing stored')
  await chooseGroup(el, 'agent')
  assert.equal(window.localStorage.getItem('orgtree.docket.group'), 'agent')

  // a fresh panel reads the stored choice rather than resetting to the default
  const again = await mount(docketModal())
  await flush()
  assert.equal(groupSelect(again.el).value, 'agent')
  forgetGroupChoice()
})

uiTest('§24 the model chip is shown only when it can honestly be attributed', async (mount) => {
  const node = (id: string, tier: string, generation: number) => ({
    id, title: id, tier, model_id: 'm', state: 'live', seat: 1, grant: 1, free: 1,
    scope: { permission_mode: 'normal' }, ui_order: 1, cost_usd: 0,
    occupancy: null, context_window: null, charter: null, generation,
  })
  const tree = mkTree({
    roots: [node('worker-agent', 'sonnet', 1),
      node('rolled-agent', 'opus', 4)] as unknown as TreeNode[],
  })
  mockWorkItems([
    mkItem({ title: 'Current', last_updater: { node: 'worker-agent', generation: 1 } }),
    // the SAME node, but this update was written by an EARLIER generation: the
    // model that generation ran under is not recorded anywhere
    mkItem({ title: 'Superseded generation', last_updater: { node: 'rolled-agent', generation: 2 } }),
    mkItem({ title: 'Gone', last_updater: { node: 'never-existed', generation: 1 } }),
  ])
  const { el } = await mount(docketModal({ tree }))
  await flush()
  const [rCur, rMoved, rGone] = rows(el)

  // POSITIVE CONTROL — without it the assertions below would also pass on a
  // component that simply never renders a chip at all
  const chip = rCur!.querySelector('.docket-updater .tier')
  assert.ok(chip, 'the current generation DOES get a model chip')
  assert.ok(chip.classList.contains('t-sonnet'))
  assert.equal(chip.textContent?.trim(), 'S')

  assert.equal(rMoved!.querySelector('.docket-updater .tier'), null,
    'an old generation must not be labelled with the model the node wears today')
  assert.match(rMoved!.querySelector('.docket-actor')?.getAttribute('title') ?? '',
    /not recorded/, 'and the row says why the badge is missing')
  assert.ok(rMoved!.querySelector('.docket-actor')?.classList.contains('fit-moved'))
  assert.equal(rGone!.querySelector('.docket-updater .tier'), null)
  assert.ok(rGone!.querySelector('.docket-actor')?.classList.contains('fit-gone'))

  // the detail pane obeys the same rule
  await inAct(() => (rCur as HTMLElement).click())
  await flush()
  assert.ok(el.querySelector('.docket-pane-sub .tier')?.classList.contains('t-sonnet'))
  await inAct(() => (rMoved as HTMLElement).click())
  await flush()
  assert.equal(el.querySelector('.docket-pane-sub .tier'), null)
})

uiTest('§25 the backlog is hidden until asked for, counted apart, and never merged into current work', async (mount) => {
  mockWorkItems([
    mkItem({ title: 'Current', status: 'in_progress' }),
  ], [], undefined, [
    mkItem({ title: 'Parked', status: 'backlogged' }),
  ])
  const { el } = await mount(docketModal())
  await flush()
  assert.deepEqual(titles(el), ['Current'], 'the backlog is not shown by default')
  assert.match(el.querySelector('.docket-showbacklog')?.textContent ?? '', /Show backlogged/)
  assert.match(el.querySelector('.docket-showbacklog')?.textContent ?? '', /1/,
    'the count rides the label, so a hidden backlog is still discoverable')

  await inAct(() => showBacklogBox(el).click())
  await flush()
  assert.deepEqual(titles(el), ['Current', 'Parked'], 'appended, never interleaved')
  const parked = rows(el)[1]!
  assert.ok(parked.classList.contains('backlog'), 'it reads as its own state')
  assert.ok(parked.classList.contains('status-backlogged'))
  assert.ok(!parked.classList.contains('active') && !parked.classList.contains('archived'))
  assert.match(parked.querySelector('.docket-status')?.textContent ?? '', /Backlogged/)

  // unticking puts it back out of sight without disturbing the rest
  await inAct(() => showBacklogBox(el).click())
  await flush()
  assert.deepEqual(titles(el), ['Current'])
})

uiTest('§26 an attention-holding backlog row arrives in the MAIN list, not behind the filter', async (mount) => {
  // the backend keeps such a row in `items` so the toolbar badge always opens
  // onto something visible; the UI must therefore not hide it on status alone
  mockWorkItems([
    mkItem({ title: 'Parked but flagged', status: 'backlogged',
      effective_attention: true, attention_sources: ['manual'] }),
  ])
  const { el } = await mount(docketModal())
  await flush()
  assert.deepEqual(titles(el), ['Parked but flagged'], 'visible with the filter OFF')
  const r = rows(el)[0]!
  assert.ok(r.classList.contains('attention'), 'attention wins over the backlog styling')
  assert.match(r.querySelector('.docket-status')?.textContent ?? '', /Needs attention/)
})

uiTest('§27 the name is TEXT in both places, and there is no copy control', async (mount) => {
  // ⚠ THIS TEST EXISTS BECAUSE THE COPY BUTTON WAS REMOVED, TWICE. The name
  // was a padded bordered chip in the row and again in the detail pane; from
  // screenshots the user removed it from the list (13:03) and then from the
  // detail as well (13:04), with "no replacement copy control anywhere". A
  // test that only checked the name is present would pass on its return.
  // ⚠ THERE IS NO LONGER AN UNNAMED ITEM TO TEST. The server cannot serve one:
  // a document still holding the retired opaque key is refused whole (409)
  // rather than served with some rows unnamed, so the old id-fallback half of
  // this check pinned a state the product can no longer reach.
  mockWorkItems([
    mkItem({ slug: 'git-review-workspace', title: 'Named' }),
    mkItem({ slug: 'second-named-item', title: 'Also named' }),
  ])
  // ⚠ onFocusAgent IS PASSED HERE ON PURPOSE, because App.tsx always passes it
  // (App.tsx:1027) and the assertion below is about the agent jump. It used to
  // be omitted, and the panel rendered the jump button anyway — a button whose
  // handler was `onFocusAgent?.(…)`, i.e. a control that did nothing. The
  // shared AgentName renders plain text when there is nowhere to go, so
  // omitting the prop here would now be testing a shape the product never
  // renders. The assertion itself is unchanged.
  const { el } = await mount(docketModal({ onFocusAgent: noop }))
  await flush()
  const [rNamed, rOld] = rows(el)
  assert.equal(rNamed!.querySelector('.l1 .mfrom')?.textContent, 'git-review-workspace')
  assert.equal(rOld!.querySelector('.l1 .mfrom')?.textContent, 'second-named-item')

  // NOTHING IN THE NAME LINE IS PRESSABLE. `.docket-slug` was the removed
  // chip's class; a button in the name line is the shape of the thing the user
  // rejected. (⚠ `assert.ok(x === null)`, never `assert.equal(node, null)` —
  // on failure node serializes the whole jsdom subtree into the diff and the
  // runner dies with "Array buffer allocation failed" instead of telling you
  // which assertion went wrong. That cost a 27-second mystery here.)
  assert.ok(el.querySelector('.docket-slug') === null,
    'the boxed name/copy chip is back in the list')
  assert.ok(rNamed!.querySelector('.l1 button') === null,
    'the row name became a control again')

  // clicking the NAME selects the item, because the name is just the row now
  await inAct(() => (rNamed!.querySelector('.l1 .mfrom') as HTMLElement).click())
  await flush()
  assert.ok(!el.querySelector('.mailer-none'), 'clicking the row name did not open it')

  // the detail pane: full title printed, slug as plain text beside it, and
  // still no copy control
  assert.equal(pane(el)?.querySelector('.docket-pane-head b')?.textContent, 'Named')
  assert.equal(pane(el)?.querySelector('.docket-slug-text')?.textContent,
    'git-review-workspace')
  assert.ok(pane(el)?.querySelector('.docket-slug') === null,
    'the boxed name/copy chip is back in the detail pane')
  // the name itself must be plain text. The sub-line legitimately holds ONE
  // button — the agent jump — so "no buttons here" would be a false alarm;
  // what matters is that the NAME is not one.
  assert.equal(pane(el)?.querySelector('.docket-slug-text')?.tagName, 'SPAN',
    'the detail name is a control again')
  assert.deepEqual(
    [...(pane(el)?.querySelectorAll('.docket-pane-sub button') ?? [])]
      .map((b) => b.className),
    ['cc-name cc-name-jump docket-actor-name'],
    'a control other than the agent jump appeared beside the detail name')

  // and the second row opens to its own name, not to the first one's
  await inAct(() => (rOld as HTMLElement).click())
  await flush()
  assert.equal(pane(el)?.querySelector('.docket-slug-text')?.textContent,
    'second-named-item')
})

uiTest('§28 the detail pane leads with the description, and says so when there is none', async (mount) => {
  mockWorkItems([
    mkItem({ title: 'Described',
      objective: 'agents cite opaque ids the user cannot read; give each item a name' }),
    mkItem({ title: 'Bare', objective: '' }),
  ])
  const { el } = await mount(docketModal())
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()
  const desc = pane(el)?.querySelector('.docket-desc')
  assert.match(desc?.textContent ?? '', /DESCRIPTION/)
  assert.match(desc?.textContent ?? '', /agents cite opaque ids/)
  // it LEADS: the description comes before the two progress lists
  const order = [...(pane(el)?.querySelectorAll('.docket-desc, .docket-list') ?? [])]
    .map((n) => n.className)
  assert.equal(order[0], 'docket-desc')

  await inAct(() => (rows(el)[1] as HTMLElement).click())
  await flush()
  assert.match(pane(el)?.querySelector('.docket-desc')?.textContent ?? '',
    /predates the rule/, 'an older item without one says so rather than showing blank')
})

uiTest('§29 the panel never re-sorts what the server ordered', async (mount) => {
  // deliberately NOT in recency order, and tied on docket_at: a component that
  // sorted for itself would disagree with the server, and two orderings of the
  // same rows is exactly the shuffle this pins down
  mockWorkItems([
    mkItem({ title: 'First from server', docket_at: '2026-09-05T10:00:00.000Z' }),
    mkItem({ id: 'wzzz', title: 'Second from server', docket_at: '2026-09-05T10:00:00.000Z' }),
    mkItem({ id: 'wmmm', title: 'Third from server', docket_at: '2026-09-05T11:00:00.000Z' }),
  ])
  const { el } = await mount(docketModal())
  await flush()
  assert.deepEqual(titles(el),
    ['First from server', 'Second from server', 'Third from server'])
})

uiTest('§30 a long agent name truncates instead of running under the Dismiss button', async (mount) => {
  mockWorkItems([mkItem({
    title: 'Long updater', effective_attention: true,
    attention_sources: ['manual'],
    manual_attention: { reason: 'look', at: '2026-09-05T09:00:00.000Z', by: { node: 'a', generation: 1 }, set_rev: 1 },
    last_updater: { node: 'an-extremely-long-agent-identifier-that-will-not-fit', generation: 1 },
  })])
  const { el } = await mount(docketModal())
  await flush()
  const r = rows(el)[0]!
  // THE ACTUAL DEFECT: text-overflow does nothing on a flex container, so the
  // ellipsis has to sit on a NON-flex element inside the wrapper. The structure
  // is what a jsdom test can honestly check; the rendered pixels are measured
  // in the browser capture instead.
  const wrap = r.querySelector('.docket-actor') as HTMLElement
  const name = r.querySelector('.docket-actor-name') as HTMLElement
  assert.ok(wrap && name, 'the name has its own element inside the flex wrapper')
  assert.ok(!name.classList.contains('docket-actor'),
    'the truncating element must not itself be the flex container')
  assert.equal(name.textContent, 'an-extremely-long-agent-identifier-that-will-not-fit')
  assert.ok(r.querySelector('.docket-dismiss'), 'and the Dismiss button is still rendered')
})

uiTest('§31 a row that leaves the archive shows its CURRENT status, not the copy we cached',
  async (mount) => {
    // ⚠ THE FIXTURE MUST HAND OVER A DIFFERENT ARRAY, not mutate the one it
    // already gave out. The panel caches the very array the mock returns, so
    // emptying that array in place empties the cache too — and the stale state
    // this test exists to reproduce never comes into being. (It did not, at
    // first: the check passed against the defective code.)
    mockWorkItems(
      [mkItem({ title: 'Live one' })],
      [mkItem({
        title: 'Was archived', status: 'done', archived: true,
        objective: 'the description it had while it was finished',
      })])
    forgetGroupChoice()
    const { el } = await mount(docketModal())
    await flush()
    await inAct(() => showArchivedBox(el).click())
    await flush()
    await inAct(() => (rows(el)[1] as HTMLElement).click())
    await flush()
    assert.match(pane(el)?.textContent ?? '', /Done/)
    assert.match(pane(el)?.textContent ?? '', /while it was finished/)

    // the server now answers differently: the item has been reopened, so it is
    // live work again with a new status and a rewritten description, and the
    // archive no longer holds it. The panel still has the old copy cached.
    mockWorkItems([
      mkItem({ title: 'Live one' }),
      mkItem({
        title: 'Was archived', status: 'in_progress', archived: false,
        objective: 'the description it has now that it is moving again',
      }),
    ], [])
    // and the user unticks the filter, so the archived group is not even served
    await inAct(() => showArchivedBox(el).click())
    await flush()

    assert.match(pane(el)?.textContent ?? '', /In progress/,
      'the CURRENT row must win over the cached archived copy')
    assert.match(pane(el)?.textContent ?? '', /moving again/)
    assert.doesNotMatch(pane(el)?.textContent ?? '', /while it was finished/)
  })

uiTest('§32 switching org drops the previous org rows and selection', async (mount) => {
  // each org answers with its own item, so "which org is on screen" is visible
  // rather than inferred
  const a = mkItem({ id: 'wA', title: 'Org one item', status: 'done', archived: true })
  const b = mkItem({ id: 'wB', title: 'Org two item', status: 'open' })
  ;(globalThis as unknown as { fetch: typeof fetch }).fetch =
    ((url: string) => {
      const path = String(url)
      const two = path.includes('/orgs/org2/')
      const headers = new Headers()
      return Promise.resolve({
        ok: true, status: 200, headers,
        json: () => Promise.resolve({
          items: two ? [b] : [],
          ...(path.includes('archived=1') ? { archived: two ? [] : [a] } : {}),
          counts: { attention: 0, active: two ? 1 : 0, archived: two ? 0 : 1, backlogged: 0 },
          now: '2026-09-05T10:00:00.000Z',
        }),
      })
    }) as typeof fetch

  forgetGroupChoice()
  const { el, render } = await mount(docketModal({ slug: 'org1' }))
  await flush()
  await inAct(() => showArchivedBox(el).click())
  await flush()
  assert.deepEqual(titles(el), ['Org one item'])
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()
  assert.match(pane(el)?.textContent ?? '', /Org one item/)

  // the SAME mounted panel is handed a different org
  await render(docketModal({ slug: 'org2' }))
  await flush()
  assert.deepEqual(titles(el), ['Org two item'],
    "the previous org's cached archived row must not survive the switch")
  assert.doesNotMatch(el.textContent ?? '', /Org one item/)
  assert.ok(pane(el)?.querySelector('.mailer-none'),
    'and no detail from the previous org may stay open under the new org URL')
})

uiTest('§32b switching org never auto-opens an item the user did not click', async (mount) => {
  // the sharp case for scoping the SELECTION rather than only the cached rows:
  // when the new org happens to hold the same id, an unscoped selection silently
  // opens a different org's item under the same id — a detail pane the user
  // never asked for, wired to a reply URL they never chose.
  // the same NAME in both orgs — that is the collision this guards against,
  // and the name is the key now
  const one = mkItem({ slug: 'same-name', title: 'Org one item',
    status: 'done', archived: true })
  const two = mkItem({ slug: 'same-name', title: 'Org two item', status: 'open' })
  ;(globalThis as unknown as { fetch: typeof fetch }).fetch =
    ((url: string) => {
      const path = String(url)
      const isTwo = path.includes('/orgs/org2/')
      return Promise.resolve({
        ok: true, status: 200, headers: new Headers(),
        json: () => Promise.resolve({
          items: isTwo ? [two] : [],
          ...(path.includes('archived=1') ? { archived: isTwo ? [] : [one] } : {}),
          counts: { attention: 0, active: isTwo ? 1 : 0, archived: isTwo ? 0 : 1, backlogged: 0 },
          now: '2026-09-05T10:00:00.000Z',
        }),
      })
    }) as typeof fetch

  forgetGroupChoice()
  const { el, render } = await mount(docketModal({ slug: 'org1' }))
  await flush()
  await inAct(() => showArchivedBox(el).click())
  await flush()
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()
  assert.match(pane(el)?.textContent ?? '', /Org one item/, 'selected in org1')

  await render(docketModal({ slug: 'org2' }))
  await flush()
  // the row is named by its slug, and BOTH orgs use the same one — which is
  // exactly the collision this test exists for
  assert.deepEqual(titles(el), ['same-name'])
  assert.ok(pane(el)?.querySelector('.mailer-none'),
    'the identical NAME must NOT carry the selection across into the new org')
  assert.doesNotMatch(pane(el)?.textContent ?? '', /Org two item/)

  // POSITIVE CONTROL: clicking in the new org still opens the new org's item,
  // so the check above is not passing because selection stopped working
  await inAct(() => (rows(el)[0] as HTMLElement).click())
  await flush()
  assert.match(pane(el)?.textContent ?? '', /Org two item/)
})

