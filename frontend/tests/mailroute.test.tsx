// mailroute.test.tsx — a `@mail:` reference clicked in a panel that owns no
// mailbox, routed to the panel that does.
//
// The docket can render a mail reference and decide whether it is real
// (docketrefs §27/§32/§32b). It cannot OPEN one: the user's inbox, the org
// inbox and a node's inbox are three different panels, two of them owned by
// the canvas. So the pointer travels docket → App → OrgCanvas, and this suite
// is about the two joints in that path.
//
// ⚠ §5 IS THE REASON §32b EXISTS. The router's node branch is `map.has(...)`
// with no else — a pointer at somebody who is not on the canvas opens nothing
// and says nothing. That is fine as a router (it cannot invent a panel), and
// it is exactly why the DOCKET has to refuse the click before it gets here.
// If §5 ever goes red because the router grew an explanation of its own, read
// §32b again before deleting either.
//
// Run:  cd frontend && node tests/run.mjs mailroute

import { flush, inAct, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { mailRefTarget } from '../src/canvas/reflinks'
import { parseRef } from '../src/canvas/workrefs'
import type { TypedRef } from '../src/canvas/workrefs'
import type { TreePayload } from '../src/types'

const noop = () => {}

const mail = (token: string): TypedRef => {
  const r = parseRef(token)
  assert.ok(r && r.kind === 'mail', `fixture token did not parse: ${token}`)
  return r
}

// ------------------------------------------------- the translation, on its own

test('§1 each box is handed over in the ROUTER\'S vocabulary', () => {
  assert.deepEqual(mailRefTarget(mail('@mail:mine/user/m1')),
    { id: 'm1', to: 'user_inbox' })
  assert.deepEqual(mailRefTarget(mail('@mail:mine/org/m2')),
    { id: 'm2', to: '@org:mine' })
  assert.deepEqual(mailRefTarget(mail('@mail:mine/node/ceo/m3')),
    { id: 'm3', to: 'ceo' })
})

test('§2 CONTROL — the org box is NOT handed over as a bare org name', () => {
  // the router reads a leading `@` as "the org inbox" and ANYTHING ELSE as a
  // node id. An org box passed as `mine` would be looked up as a node called
  // `mine` — nothing today, and the wrong mailbox on the day one exists.
  const to = mailRefTarget(mail('@mail:mine/org/m2')).to
  assert.ok(to.startsWith('@'), `the org box must be marked, got ${to}`)
  assert.notEqual(to, 'mine')
  // and the user box is the literal the router tests for, not a node either
  assert.equal(mailRefTarget(mail('@mail:mine/user/m1')).to, 'user_inbox')
})

// ------------------------------------------------------ and through the canvas

function uiTest(name: string,
  body: (mount: (el: React.ReactElement) => Promise<HTMLElement>)
    => Promise<void>): void {
  test(name, async (t: TestContext) => {
    useFakeClock()
    const open: { unmount: () => Promise<void> }[] = []
    t.after(async () => {
      for (const m of open) { try { await m.unmount() } catch { /* gone */ } }
      realClock()
    })
    await body(async (el) => {
      const v = await mountView(el, (host) => host)
      open.push(v)
      return v.el
    })
  })
}

/** shaped like the payload, not type-checked into it — the same fixture idiom
 *  as agentstray.test.tsx, trimmed to what OrgCanvas dereferences */
const asTree = (v: unknown) => v as TreePayload

function tree(nodeIds: string[]): TreePayload {
  const mk = (id: string) => ({
    id, title: id, tier: 'haiku', model_id: 'haiku', state: 'live',
    seat: 1, grant: 0, free: 0, ui_order: 0, cost_usd: 0, occupancy: null,
    context_window: null, charter: null, mail_pending: 0, limit_locked: false,
    last_status: null, prev_status: null, inflight_at: null, last_denials: [],
    turns: [], frozen: null, audiences_held: [], bearer_state: null,
    generation: 0, children: [], lineage: [],
    scope: { permission_mode: 'default', add_dirs: [], tools: {}, org_visibility: 'team' },
  })
  return asTree({
    slug: 'mine', name: 'mine', workspace: null, dirs: [], max_top_grant: 1000,
    default_top_grant: 50, compact_at: 0, default_tools: null,
    default_visibility: 'team', default_effort: '', credit_requests: [],
    tiers: { haiku: 1, sonnet: 3, opus: 5, fable: 10 }, audiences: [],
    roots: nodeIds.map(mk), cost_usd_total: 0,
    audit: { live_nodes: nodeIds.length, top_level_holds: 0, no_overdraft: true, problems: [] },
    user_inbox_count: 0, user_inbox_newest: null, fable_lock: null,
    spend_frozen: false, storage_blocked: false, auto_resume: false,
    fable_limit_policy: 'freeze', fable_filter_policy: 'halt',
    cascade_hire: false, cascade_alloc: true, sandboxed: false,
    audience_requests: [], org_inbox: null, net: null,
  })
}

/** every fetch the canvas makes under test answers empty — the panels open
 *  either way, and this suite is about WHICH panel opens */
function quietServer() {
  ;(globalThis as unknown as { fetch: typeof fetch }).fetch =
    (() => Promise.resolve({
      ok: true, status: 200, headers: new Headers(),
      json: () => Promise.resolve({}),
    })) as unknown as typeof fetch
}

const heads = (el: HTMLElement) =>
  [...el.querySelectorAll('.overlay h3')].map((h) => h.textContent ?? '')

uiTest('§3 a NODE box pointer opens that node\'s inbox, once', async (mount) => {
  quietServer()
  const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
  let handled = 0
  const el = await mount(
    <OrgCanvas tree={tree(['ceo', 'cto'])} op={() => Promise.resolve({} as never)}
      slug="mine" toast={noop} mailEvt={null}
      openMailAt={mailRefTarget(mail('@mail:mine/node/cto/m3'))}
      onOpenMailHandled={() => { handled += 1 }} />)
  await flush()
  assert.ok(heads(el).some((h) => h.includes('cto')),
    `the node inbox did not open — panels: ${JSON.stringify(heads(el))}`)
  // CONTROL — the OTHER node's box is not what opened
  assert.ok(!heads(el).some((h) => h.includes('ceo')))
  assert.equal(handled, 1, 'the pointer is consumed exactly once')
})

uiTest('§4 an ORG box pointer opens the org inbox', async (mount) => {
  quietServer()
  const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
  const el = await mount(
    <OrgCanvas tree={tree(['ceo'])} op={() => Promise.resolve({} as never)}
      slug="mine" toast={noop} mailEvt={null}
      openMailAt={mailRefTarget(mail('@mail:mine/org/m2'))}
      onOpenMailHandled={noop} />)
  await flush()
  assert.ok(heads(el).some((h) => h.includes('org inbox')),
    `the org inbox did not open — panels: ${JSON.stringify(heads(el))}`)
})

uiTest('§4b a USER box pointer is handed UP — the canvas does not own that '
  + 'panel', async (mount) => {
    quietServer()
    const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
    const asked: unknown[] = []
    const el = await mount(
      <OrgCanvas tree={tree(['ceo'])} op={() => Promise.resolve({} as never)}
        slug="mine" toast={noop} mailEvt={null}
        onInbox={(j) => asked.push(j)}
        openMailAt={mailRefTarget(mail('@mail:mine/user/m1'))}
        onOpenMailHandled={noop} />)
    await flush()
    assert.deepEqual(asked, ['m1'], 'the shell was asked to open its own inbox')
    // and nothing on the canvas opened instead
    assert.equal(heads(el).length, 0,
      `no canvas panel should have opened: ${JSON.stringify(heads(el))}`)
  })

uiTest('§5 CONTROL — a node this canvas does not have opens NOTHING, silently',
  async (mount) => {
    // ⚠ THIS IS THE ROUTER'S BLIND SPOT, PINNED ON PURPOSE. It is why the
    // docket refuses such a reference before the click (docketrefs §32b): if
    // this behaviour ever changes, that guard's justification changes with it.
    quietServer()
    const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
    let handled = 0
    const el = await mount(
      <OrgCanvas tree={tree(['ceo'])} op={() => Promise.resolve({} as never)}
        slug="mine" toast={noop} mailEvt={null}
        openMailAt={mailRefTarget(mail('@mail:mine/node/never-hired/m9'))}
        onOpenMailHandled={() => { handled += 1 }} />)
    await flush()
    assert.equal(heads(el).length, 0,
      `nothing should have opened: ${JSON.stringify(heads(el))}`)
    // the pointer is still consumed — an unroutable one must not sit there
    // re-firing on every render
    assert.equal(handled, 1)
  })

uiTest('§5b a DOCUMENT pointer opens the reader on that id, once',
  async (mount) => {
    quietServer()
    const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
    let handled = 0
    const el = await mount(
      <OrgCanvas tree={tree(['ceo'])} op={() => Promise.resolve({} as never)}
        slug="mine" toast={noop} mailEvt={null}
        openDocAt="d7" onOpenDocHandled={() => { handled += 1 }} />)
    await flush()
    assert.ok(el.querySelector('.doc-reader'), 'the document reader opened')
    assert.equal(handled, 1, 'the pointer is consumed exactly once')
  })

// ------------------------------------------- and from a real mail body

/** the user's inbox holding ONE message with `body` */
function inboxServer(bodyText: string) {
  ;(globalThis as unknown as { fetch: typeof fetch }).fetch =
    ((url: string) => {
      const path = String(url)
      const payload = path.includes('/inbox')
        ? { pending: [], sent: [], delivered: [{
          id: 'm1', from: 'ceo', to: 'user_inbox', kind: 'message',
          at: '2026-09-05T09:00:00.000Z', body: bodyText,
        }] }
        : {}
      return Promise.resolve({
        ok: true, status: 200, headers: new Headers(),
        json: () => Promise.resolve(payload),
      })
    }) as unknown as typeof fetch
}

uiTest('§7 a reference written INSIDE A MAIL is a control, and it reaches the '
  + 'panel that owns the destination', async (mount) => {
    // the end of the path: the body is markdown, so this is the DOM pass in
    // refmd, mounted inside the real inbox against a real fetch.
    inboxServer('as agreed in @item:mine/the-plan, see also '
      + '@mail:mine/node/never-hired/m9')
    const { InboxPanel } = await import('../src/App')
    const items: string[] = []
    const el = await mount(
      <InboxPanel slug="mine" tree={tree(['ceo'])} toast={noop}
        jumpTo={null} close={noop} onOpenItem={(s) => items.push(s)} />)
    await flush()
    const row = el.querySelector('.mailrow') as HTMLElement
    assert.ok(row, 'the message rendered')
    await inAct(() => row.click())
    await flush()
    const chips = [...el.querySelectorAll('.mailer-body [data-ref-token]')]
    assert.equal(chips.length, 2, 'both references in the body were decided')
    const [item, mail] = chips as HTMLElement[]
    assert.equal(item!.tagName, 'BUTTON', 'the item reference is a control')
    await inAct(() => item!.click())
    await flush()
    assert.deepEqual(items, ['the-plan'],
      'and it asked the shell to open that item')
    // ⚠ THE CONTROLS IN THE SAME BODY. `onOpenMail` was NOT supplied, so a
    // mail token is "not opened from here" — and it would be inert anyway,
    // because `never-hired` has no mailbox in this tree. Neither is reported
    // as a missing message.
    assert.equal(mail!.tagName, 'SPAN')
    assert.doesNotMatch(mail!.getAttribute('title') ?? '', /does not hold/)
  })

uiTest('§7b CONTROL — with no handlers at all a mail body is plain prose',
  async (mount) => {
    // a surface with nowhere to send anybody must not draw controls. Without
    // this, §7 passes just as well for a panel that links everything always.
    inboxServer('as agreed in @item:mine/the-plan')
    const { InboxPanel } = await import('../src/App')
    const el = await mount(
      <InboxPanel slug="mine" tree={tree(['ceo'])} toast={noop}
        jumpTo={null} close={noop} />)
    await flush()
    await inAct(() => (el.querySelector('.mailrow') as HTMLElement).click())
    await flush()
    const chips = [...el.querySelectorAll('.mailer-body [data-ref-token]')]
    assert.equal(chips.length, 1, 'the token is still decided')
    assert.equal(chips[0]!.tagName, 'SPAN', 'but nothing is clickable')
    assert.ok(chips[0]!.className.includes('ref-elsewhere'),
      'and it says why: there is nothing here to open it with')
  })

uiTest('§6 CONTROL — with no pointer the canvas opens no mailbox at all',
  async (mount) => {
    // the positive control for §3-§5: these panels are not simply always up
    quietServer()
    const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
    const el = await mount(
      <OrgCanvas tree={tree(['ceo', 'cto'])} op={() => Promise.resolve({} as never)}
        slug="mine" toast={noop} mailEvt={null} />)
    await flush()
    assert.equal(heads(el).length, 0,
      `no panel should be open: ${JSON.stringify(heads(el))}`)
  })
