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

function mockWorkItems(activeItems: WorkItem[], archivedItems: WorkItem[] = [], extraCalls?: Call[]): Call[] {
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
        const found = [...activeItems, ...archivedItems].find((x) => x.id === id)
        return ok({ item: found ? { ...found, manual_attention: null, status: 'blocked' } : null })
      }
      if (method === 'POST' && path.includes('/reply')) {
        const m = path.match(/\/work-items\/([^/]+)\/reply$/)
        const id = m ? m[1] : ''
        const found = [...activeItems, ...archivedItems].find((x) => x.id === id)
        const isDeferred = found?.last_updater?.node === 'archived-agent'
        return ok({ accepted: true, to: found?.last_updater?.node ?? 'agent', deferred: isDeferred })
      }
      if (method === 'GET' && path.includes('/work-items?archived=1')) {
        return ok({
          items: activeItems,
          archived: archivedItems,
          counts: { attention: activeItems.filter((x) => x.effective_attention).length, active: activeItems.length, archived: archivedItems.length },
          now: '2026-09-05T10:00:00.000Z',
        })
      }
      if (method === 'GET' && path.match(/\/work-items$/)) {
        return ok({
          items: activeItems,
          counts: { attention: activeItems.filter((x) => x.effective_attention).length, active: activeItems.length, archived: archivedItems.length },
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

const mkItem = (o: Partial<WorkItem>): WorkItem => ({
  id: 'w10000001',
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
const rows = (el: HTMLElement) => [...el.querySelectorAll('.mailrow.docket-row')]
const pane = (el: HTMLElement) => el.querySelector('.mailer-read')
const showArchivedBox = (el: HTMLElement) =>
  el.querySelector('.docket-showarchived input') as HTMLInputElement

const docketModal = (extra?: Partial<{
  close: () => void
  onFocusAgent: (id: string) => void
  tree: TreePayload
  toast: (lines: string[]) => void
}>) => (
  <DocketModal slug="org1" toast={extra?.toast ?? noop} close={extra?.close ?? noop}
    tree={extra?.tree ?? mkTree()} onFocusAgent={extra?.onFocusAgent} />
)

uiTest('§1 an empty org says so rather than rendering a blank panel', async (mount) => {
  mockWorkItems([])
  const { el } = await mount(docketModal())
  await flush()
  assert.equal(rows(el).length, 0)
  assert.match(el.textContent ?? '', /no work items yet/)
})

uiTest('§2 a row carries title, status, time since latest docket update, and last status updater name', async (mount) => {
  mockWorkItems([mkItem({
    id: 'w1',
    title: 'Build the work docket',
    status: 'in_progress',
    docket_at: '2026-09-05T09:55:00.000Z',
    last_updater: { node: 'luna-reserve', generation: 1 },
  })])
  const { el } = await mount(docketModal())
  await flush()
  assert.equal(rows(el).length, 1)
  const r = rows(el)[0]!
  assert.equal(r.querySelector('.l1 .mfrom')?.textContent, 'Build the work docket')
  assert.ok((r.querySelector('.l1 .mtime')?.textContent ?? '').length > 0)
  assert.match(r.querySelector('.l2')?.textContent ?? '', /In progress/)
  assert.equal(r.querySelector('.l2 .docket-updater')?.textContent, 'luna-reserve')
})

uiTest('§3 left row updater is last updater, not necessarily owner', async (mount) => {
  mockWorkItems([mkItem({
    id: 'w1',
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
    mkItem({ id: 'w1', title: 'Active Item', status: 'in_progress', effective_attention: false, archived: false }),
    mkItem({ id: 'w2', title: 'Attn Item', status: 'blocked', effective_attention: true, attention_sources: ['manual'], archived: false }),
  ], [
    mkItem({ id: 'w3', title: 'Archived Item', status: 'done', effective_attention: false, archived: true }),
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
    mkItem({ id: 'w1', title: 'Active 1' }),
  ], [
    mkItem({ id: 'w2', title: 'Archived 1', archived: true }),
    mkItem({ id: 'w3', title: 'Archived 2', archived: true }),
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
    id: 'w1',
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
    id: 'w1',
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
    id: 'w1',
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
    id: 'w1',
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
    id: 'w1',
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
    id: 'w1',
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
    id: 'w2',
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
    id: 'w1',
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
    id: 'w1',
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