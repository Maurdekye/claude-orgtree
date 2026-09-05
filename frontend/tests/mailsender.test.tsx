// mailsender.test.tsx — THE SENDER'S MODEL CHIP AND CLICK-TO-DESK, on the two
// surfaces the first pass missed: the mail LIST ROW and the INLINE TRANSCRIPT.
//
// User ruling 2026-09-05, reiterated the same afternoon: an agent's name wears
// its model card and takes you to its desk EVERYWHERE it appears. The landed
// work converted the mailbox's READING PANE and the docket; two places kept
// drawing a bare name — `MailList`'s row (`.mfrom`, raw `party(m)`) and the
// desk transcript's mail card (`<b>{mail.from}</b>`, with no facts wired down
// to it at all).
//
// ⚠ WHAT THIS FILE IS ACTUALLY GUARDING, because "a chip is on screen" is the
// cheap half and not the risky one:
//
//  1. THE ROW IS A CLICK TARGET INSIDE A CLICK TARGET. `.mailrow` has its own
//     onClick — it selects the mail. A sender button that does not stop the
//     bubble navigates AND selects, or deselects the mail you were reading.
//     §5 clicks the name and the row's own text and requires the two to have
//     DIFFERENT effects; a missing stopPropagation makes them the same.
//
//  2. THE NAME MATCHING IS NOT THE EVIDENCE. An outside party may spell itself
//     exactly like one of our agents. §2/§3 give the transcript a sender whose
//     name is ordinary and require no chip and no route unless the tree ON
//     SCREEN vouches for it — and prove the check is live by adding the very
//     same name to the tree and watching both appear.
//
//  3. A CONTEXT WIRED TO NOTHING IS THE FAILURE MODE HERE. `TurnMailCard` sits
//     under a memo'd row in a windowed list, so its facts arrive by context;
//     a provider that is never mounted, or mounted with a resolver that never
//     answers, looks exactly like correct code and renders exactly the bare
//     name we started with. §1 therefore mounts the REAL `DeskChat` against
//     the fake server and drives a real envelope through it — no hand-built
//     provider — so the wiring is what is on trial, not the component.
//
// Anti-vacuity, stated per section: every "renders nothing" assertion is
// paired with a mount that differs in ONE fact and does render it.
//
// Run:  cd frontend && node tests/run.mjs mailsender

import { FakeServer, flush, inAct, installFetch, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { refreshConvo, resetConvos } from '../src/convo'
import { SenderChip } from '../src/App'
import { DeskChat, LineagePanel, Msg } from '../src/canvas/desk'
import { MailList, OrgInboxModal } from '../src/canvas/mail'
import { AgentDirectoryProvider } from '../src/canvas/identity'
import type { AgentDirectory } from '../src/canvas/identity'
import type { CanvasNode, MailRow } from '../src/canvas/shared'
import type { ChatMessage, OpResult, OrgInboxEntry, TreeNode, TreePayload } from '../src/types'

// jsdom implements no layout, so `Element.scrollIntoView` does not exist, and
// `jumpTo` calls it from a ref callback where a throw kills the commit rather
// than the assertion (the mailwire.test.tsx idiom).
;(globalThis as unknown as
  { window: { Element: { prototype: Record<string, unknown> } } })
  .window.Element.prototype.scrollIntoView ??= () => {}

const noop = () => {}
const op = () => Promise.resolve({} as OpResult)
const q = (el: HTMLElement, sel: string) => [...el.querySelectorAll(sel)] as HTMLElement[]
const txt = (el: HTMLElement) => el.textContent ?? ''

/** ⚠ NEVER `assert.equal(node, null)` ON A DOM NODE IN THIS REPO. When it
 *  FAILS, node's diff walks the whole jsdom element, dies with "Array buffer
 *  allocation failed" after ~26 s, and the assertion message never prints —
 *  four mutants read as "wrong check" before this was found (docketname
 *  suite, 2026-09-05). Assert on the COUNT. */
const absent = (el: HTMLElement, sel: string, why: string) =>
  assert.equal(q(el, sel).length, 0, why)

function node(id: string, over: Partial<CanvasNode> = {}): CanvasNode {
  return {
    id, state: 'live', tier: 'sol', children: [], seat: 1, grant: 0, free: 0,
    scope: { tools: {}, add_dirs: [] }, model_id: 'sol', ...over,
  } as CanvasNode
}

/** the envelope the backend really writes (supervisor `_mail_block`) — the
 *  shape `splitTurnMail` parses into a card */
const envelope = (from: string, rel = 'your peer', body = 'a word about the build') =>
  `[MAIL — 1 message(s)]\nFROM ${from} (${rel}) · message · 2026-09-05T10:00:00Z\n`
  + `${body}\n[END MAIL]\n\n(orgtree) You have new mail above.`

function uiTest(name: string,
  body: (k: { mount: (el: React.ReactElement) => Promise<{ el: HTMLElement }> }) => Promise<void>,
): void {
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
        await flush()
        return { el: v.el }
      },
    })
  })
}

// ═══════════════════════════════════════════════════════════════════ §1
// THE INLINE TRANSCRIPT, THROUGH THE REAL DESK. Nothing here is hand-built:
// the fake server serves a real envelope, `DeskChat` mounts, and whatever the
// reader would see is what is judged. This is the section that fails if the
// provider is not actually mounted — the "wired to nothing" failure.
// ═══════════════════════════════════════════════════════════════════ §1

let _n = 0
async function desk(t: TestContext, opts: {
  from: string
  /** who is in the tree ON SCREEN besides the desk's own node */
  others?: CanvasNode[]
  /** omit to model a surface with nowhere to jump to */
  onJump?: (id: string) => void
  rel?: string
}): Promise<HTMLElement> {
  useFakeClock()
  const SL = 'org'
  const ND = `ms${++_n}`
  const s = new FakeServer()
  installFetch(s)
  const nd = node(ND, { tier: 'opus' })
  const map = new Map<string, CanvasNode>([[nd.id, nd]])
  for (const o of opts.others ?? []) map.set(o.id, o)
  s.userMsg(envelope(opts.from, opts.rel ?? 'your peer'))
  const v = await mountView(
    <DeskChat node={nd} map={map} op={op} slug={SL} toast={noop} pub={false}
      bare onJump={opts.onJump} />,
    (host) => host)
  t.after(async () => {
    try { await v.unmount() } catch { /* gone */ }
    resetConvos()
    realClock()
  })
  await refreshConvo(SL, ND, { force: true })
  await flush()
  return v.el
}

const head = (el: HTMLElement) => {
  const h = el.querySelector('.turn-mail-head') as HTMLElement | null
  assert.ok(h, 'positive control: the transcript rendered a mail CARD at all — '
    + 'without one every assertion below would pass by absence')
  return h!
}

test('§1.1 a mail from an agent of this tree wears its model chip and goes to '
  + 'its desk — through the real DeskChat, not a hand-built provider',
async (t: TestContext) => {
  const jumped: string[] = []
  const el = await desk(t, {
    from: 'peer-one',
    others: [node('peer-one', { tier: 'sonnet' })],
    onJump: (id) => { jumped.push(id) },
  })
  const h = head(el)
  const chip = q(h, '.tier')
  assert.equal(chip.length, 1, 'exactly one model chip in the mail header')
  assert.ok(chip[0]!.classList.contains('t-sonnet'),
    `and it is the SENDER's model, not the desk's (the desk is opus): ${chip[0]!.className}`)
  const jump = q(h, 'button.cc-name-jump')
  assert.equal(jump.length, 1, 'the name is one jump button')
  assert.equal(txt(jump[0]!), 'peer-one', 'labelled with the sender, verbatim')
  // ⚠ the tooltip must not claim to know the model AT THE TIME. The envelope
  // records no generation, so the chip is the CURRENT model and says so.
  const tip = jump[0]!.getAttribute('title') ?? ''
  assert.ok(tip.includes('current model'),
    `the tooltip scopes the claim to the current model — got ${JSON.stringify(tip)}`)
  await inAct(() => { jump[0]!.click() })
  assert.deepEqual(jumped, ['peer-one'], 'clicking it focuses that agent')
})

test('§1.2 …and the same mail from a name the tree does NOT hold gets neither '
  + 'chip nor route — a name match is not evidence of who it is',
async (t: TestContext) => {
  // ⚠ THE ANTI-VACUITY PAIR. Identical fixture, identical envelope, identical
  // jump handler; the ONE difference is whether `outsider` is in the tree on
  // screen. §1.1 above is the positive half — the same code path DOES draw
  // both when the tree vouches for the name.
  const el = await desk(t, {
    from: 'outsider',
    others: [],                       // an ordinary agent-shaped name, unheld
    onJump: noop,
    rel: 'outside party — addressed to the whole org',
  })
  const h = head(el)
  assert.ok(txt(h).includes('outsider'), 'the name is still shown, verbatim')
  absent(h, '.tier', 'no model chip for a name this tree cannot vouch for')
  absent(h, 'button.cc-name-jump', 'and no route into our tree either')
})

test('§1.3 an @-sentinel is never an agent: @user, @system and an outside '
  + 'peer keep their plain name', async (t: TestContext) => {
  for (const from of ['@user', '@system', '@net:faraway.other-machine.abc']) {
    const el = await desk(t, {
      from,
      // ⚠ ANTI-VACUITY, THE HARD WAY: the sentinel is ALSO put in the tree
      // under its own literal name. If the '@' clause were dropped, `resolve`
      // would now answer and a chip would appear — so this fixture makes the
      // clause the only thing standing between the sentinel and a chip.
      others: [node(from, { tier: 'opus' })],
      onJump: noop,
    })
    const h = head(el)
    absent(h, '.tier', `${from} draws no model chip`)
    absent(h, 'button.cc-name-jump', `${from} draws no jump`)
  }
})

test('§1.4 a desk with nowhere to jump renders a name that does not click — '
  + 'not a button that does nothing', async (t: TestContext) => {
  const el = await desk(t, {
    from: 'peer-one',
    others: [node('peer-one', { tier: 'sonnet' })],
    onJump: undefined,                // the surface offers no navigation
  })
  const h = head(el)
  assert.equal(q(h, '.tier').length, 1,
    'positive control: the chip still renders, so this is not a dead mount')
  absent(h, 'button.cc-name-jump',
    'but there is no button: an inert control is worse than no control')
})

test('§1.5 mail an agent sent to ITSELF does not offer a trip to the desk you '
  + 'are already on', async (t: TestContext) => {
  const SL = 'org'
  const ND = `ms${++_n}`
  useFakeClock()
  const s = new FakeServer()
  installFetch(s)
  const nd = node(ND, { tier: 'opus' })
  s.userMsg(envelope(ND, 'yourself'))
  const v = await mountView(
    <DeskChat node={nd} map={new Map([[nd.id, nd]])} op={op} slug={SL}
      toast={noop} pub={false} bare onJump={noop} />, (host) => host)
  t.after(async () => {
    try { await v.unmount() } catch { /* gone */ }
    resetConvos(); realClock()
  })
  await refreshConvo(SL, ND, { force: true })
  await flush()
  const h = head(v.el)
  assert.equal(q(h, '.tier').length, 1,
    'positive control: it is still an identified agent, chip and all')
  absent(h, 'button.cc-name-jump',
    'but the destination IS this surface, so the name is plain text')
})

// ═══════════════════════════════════════════════════════════════════ §2
// THE CARD ON ITS OWN, against a directory that answers EVERYTHING. §1 proves
// the desk wires one up; this proves the card consults it rather than
// deciding from the id, and that the eligibility test is not the name.
// ═══════════════════════════════════════════════════════════════════ §2

const dirAll: AgentDirectory = {
  resolve: () => ({ tier: 'fable' }),          // answers for ANY id
  onFocus: noop,
}
const msg = (text: string): ChatMessage =>
  ({ role: 'user', text, seq: 1, ts: '2026-09-05T10:00:00Z' })

uiTest('§2.1 the card asks the directory — a resolver that answers puts a chip '
  + 'on an ordinary name', async ({ mount }) => {
  const { el } = await mount(
    <AgentDirectoryProvider value={dirAll}>
      <Msg m={msg(envelope('somebody'))} slug="org" nid="me" />
    </AgentDirectoryProvider>)
  const h = head(el)
  assert.equal(q(h, '.tier').length, 1,
    'the chip comes from the DIRECTORY, not from anything about the id')
  assert.ok(q(h, '.tier')[0]!.classList.contains('t-fable'),
    'and it is the model the directory named')
})

uiTest('§2.2 …and with NO provider above it the same card is plain text',
  async ({ mount }) => {
    // the honest degradation: a surface that cannot answer "is this one of
    // ours" must not draw a chip or a route for a name it cannot vouch for
    const { el } = await mount(<Msg m={msg(envelope('somebody'))} slug="org" nid="me" />)
    const h = head(el)
    assert.ok(txt(h).includes('somebody'), 'the name is still there')
    absent(h, '.tier', 'no chip without a directory')
    absent(h, 'button.cc-name-jump', 'and no jump')
  })

uiTest('§2.3 a directory that resolves but offers no focus: chip, no button',
  async ({ mount }) => {
    const { el } = await mount(
      <AgentDirectoryProvider value={{ resolve: () => ({ tier: 'fable' }) }}>
        <Msg m={msg(envelope('somebody'))} slug="org" nid="me" />
      </AgentDirectoryProvider>)
    const h = head(el)
    assert.equal(q(h, '.tier').length, 1, 'positive control: it did resolve')
    absent(h, 'button.cc-name-jump', 'but nothing claims to navigate')
  })

uiTest('§2.4 a resolved agent whose CURRENT model is unknown gets no chip, and '
  + 'the tooltip says which claim it is declining', async ({ mount }) => {
    const { el } = await mount(
      <AgentDirectoryProvider value={{ resolve: () => ({ tier: null }), onFocus: noop }}>
        <Msg m={msg(envelope('somebody'))} slug="org" nid="me" />
      </AgentDirectoryProvider>)
    const h = head(el)
    absent(h, '.tier', 'an unknown model is an answer, never back-filled')
    const jump = q(h, 'button.cc-name-jump')
    assert.equal(jump.length, 1,
      'positive control: it IS still a resolved agent, so the route stands')
    assert.ok((jump[0]!.getAttribute('title') ?? '').includes('current model not known'),
      `and the tooltip says so: ${jump[0]!.getAttribute('title')}`)
  })

// ═══════════════════════════════════════════════════════════════════ §3
// THE MAILBOX LIST ROW.
// ═══════════════════════════════════════════════════════════════════ §3

const row = (over: Partial<MailRow> = {}): MailRow => ({
  id: 'r1', from: 'peer-one', kind: 'message',
  body: 'the body of the first mail', at: '2026-09-05T10:00:00Z', ...over,
} as MailRow)

const rowsOf = (el: HTMLElement) => q(el, '.mailrow')
const mfrom = (el: HTMLElement, i = 0) => {
  const r = rowsOf(el)[i]
  assert.ok(r, `positive control: row ${i} rendered at all`)
  const f = r!.querySelector('.mfrom') as HTMLElement | null
  assert.ok(f, 'and it has an identity cell')
  return f!
}

uiTest('§3.1 the LIST ROW carries the model chip and the click-to-desk, not '
  + 'only the reading pane', async ({ mount }) => {
    const jumped: string[] = []
    const { el } = await mount(
      <MailList delivered={[row()]} tierOf={(id) => (id === 'peer-one' ? 'sonnet' : null)}
        onFocusAgent={(id) => { jumped.push(id) }} />)
    const f = mfrom(el)
    assert.equal(q(f, '.tier').length, 1, 'the row names the sender with its model')
    assert.ok(q(f, '.tier')[0]!.classList.contains('t-sonnet'), 'the right model')
    const jump = q(f, 'button.cc-name-jump')
    assert.equal(jump.length, 1, 'and the name is a jump')
    await inAct(() => { jump[0]!.click() })
    assert.deepEqual(jumped, ['peer-one'], 'which focuses that agent')
  })

uiTest('§3.2 …and the identical row with no facts supplied stays bare — so '
  + '§3.1 is not asserting something the markup gives away free',
  async ({ mount }) => {
    const { el } = await mount(<MailList delivered={[row()]} />)
    const f = mfrom(el)
    assert.ok(txt(f).includes('peer-one'), 'the name is still rendered')
    absent(f, '.tier', 'no tier resolver, no chip')
    absent(f, 'button.cc-name-jump', 'no focus handler, no button')
  })

uiTest('§3.3 an @-sentinel in the row keeps its plain name even with a tier '
  + 'resolver that would answer', async ({ mount }) => {
    const { el } = await mount(
      <MailList delivered={[row({ from: '@system' }), row({ id: 'r2', from: '@user' })]}
        tierOf={() => 'opus'} onFocusAgent={noop} />)
    // rows are newest-first by `at`; both share a timestamp, so judge both
    for (const i of [0, 1]) {
      const f = mfrom(el, i)
      absent(f, '.tier', `row ${i}: a sentinel is not an agent`)
      absent(f, 'button.cc-name-jump', `row ${i}: and has no desk`)
    }
  })

// ═══════════════════════════════════════════════════════════════════ §4
// A CLICK TARGET INSIDE A CLICK TARGET. The row selects the mail; the name
// navigates. One gesture must not do both.
// ═══════════════════════════════════════════════════════════════════ §4

/** the reading pane is empty exactly when nothing is selected */
const selected = (el: HTMLElement) => q(el, '.mailer-none').length === 0

uiTest('§4.1 clicking the sender NAVIGATES without selecting the mail, and '
  + 'clicking the row still selects it', async ({ mount }) => {
    const jumped: string[] = []
    const { el } = await mount(
      <MailList delivered={[row()]} tierOf={() => 'sonnet'}
        onFocusAgent={(id) => { jumped.push(id) }} />)
    assert.equal(selected(el), false,
      'the box opens with nothing selected (user spec 2026-08-05)')
    await inAct(() => { q(mfrom(el), 'button.cc-name-jump')[0]!.click() })
    assert.deepEqual(jumped, ['peer-one'], 'the name navigated')
    assert.equal(selected(el), false,
      'and the row did NOT also select — the bubble was stopped')
    // ⚠ THE POSITIVE CONTROL FOR THE INSTRUMENT ITSELF. If `selected()` could
    // not see a selection happen, the assertion above would pass however
    // broken the stopPropagation was. Clicking the row's own preview line is
    // the same gesture one element over, and it MUST select.
    await inAct(() => { (rowsOf(el)[0]!.querySelector('.l2') as HTMLElement).click() })
    assert.equal(selected(el), true,
      'positive control: a click on the row body DOES select the mail')
  })

uiTest('§4.2 …and clicking the sender of the mail you are READING does not '
  + 'close it', async ({ mount }) => {
    // the second half of the same bug: `.mailrow`\'s onClick TOGGLES, so an
    // unstopped click on the selected row would deselect and empty the pane
    // out from under the reader.
    const jumped: string[] = []
    const { el } = await mount(
      <MailList delivered={[row()]} tierOf={() => 'sonnet'}
        onFocusAgent={(id) => { jumped.push(id) }} />)
    await inAct(() => { (rowsOf(el)[0]!.querySelector('.l2') as HTMLElement).click() })
    assert.equal(selected(el), true, 'positive control: it is open')
    await inAct(() => { q(mfrom(el), 'button.cc-name-jump')[0]!.click() })
    assert.deepEqual(jumped, ['peer-one'], 'the name navigated')
    assert.equal(selected(el), true, 'and the mail stayed open')
  })

// ═══════════════════════════════════════════════════════════════════ §5
// THE ORG INBOX — the one mailbox whose counterparty is NOT this org's agent.
// ═══════════════════════════════════════════════════════════════════ §5

const oiIn: OrgInboxEntry = {
  id: 'e-in', dir: 'in', peer: '@net:faraway.other-machine.abcdef',
  body: 'an inbound one', at: '2026-09-05T09:00:00Z',
}
const oiOut: OrgInboxEntry = {
  id: 'e-out', dir: 'out', peer: '@net:faraway.other-machine.abcdef',
  body: 'our answer', at: '2026-09-05T10:00:00Z', by: 'ceo',
  net_id: 'n-out', state: 'sent', state_at: '2026-09-05T10:00:00Z',
}
const inboxBox = (entries: OrgInboxEntry[]): TreePayload['org_inbox'] =>
  ({ entries, unread: 0, holders: ['ceo'], visible: true })

uiTest('§5.1 an org-inbox row names the outside party as plain text — no chip, '
  + 'no route — while the SENT pane still shows who sent it, in full',
  async ({ mount }) => {
    const map = new Map<string, CanvasNode>([['ceo', node('ceo', { tier: 'opus' })]])
    const { el } = await mount(
      <OrgInboxModal inbox={inboxBox([oiIn, oiOut])}
        net={{ slug: 'mine.ncola.abc123', hubs: [] }} map={map} slug="mine"
        toast={noop} close={noop} jumpTo={null} onFocusAgent={noop} />)
    // the inbox folder: the peer is the counterparty
    const f = mfrom(el)
    absent(f, '.tier', 'an outside peer gets no model chip in the row')
    absent(f, 'button.cc-name-jump', 'and no route into our tree')
    // the SENT folder: the row names the recipient ONLY. The reading pane's
    // "@agent as @org → @recipient" is three identities and does not belong
    // in a row — but it must still be there when you OPEN the mail.
    const sent = q(el, '.mail-folders button')
      .find((b) => txt(b).trim().startsWith('sent'))
    assert.ok(sent, 'positive control: there is a sent folder to click')
    await inAct(() => { sent!.click() })
    const sf = mfrom(el)
    assert.ok(txt(sf).includes('faraway'), 'the sent row names the recipient')
    assert.ok(!txt(sf).includes(' as '),
      `and NOT the whole attribution line: ${JSON.stringify(txt(sf))}`)
    await inAct(() => { (rowsOf(el)[0]!.querySelector('.l2') as HTMLElement).click() })
    const pane = el.querySelector('.mailer-head') as HTMLElement | null
    assert.ok(pane, 'positive control: opening the mail rendered a reading pane')
    assert.ok(txt(pane!).includes(' as '),
      `the PANE still carries the full attribution: ${JSON.stringify(txt(pane!))}`)
    // …and the local agent that sent it keeps its chip THERE, which is the
    // control proving this fixture could have produced one in the row
    assert.equal(q(pane!, '.tier').length, 1,
      'the sending agent — ours — is identified in the pane')
  })

uiTest('§5.2 …and a peer that spells itself EXACTLY like one of our live '
  + 'agents still gets nothing — the row obeys the call site, not the name',
  async ({ mount }) => {
    // ⚠ WHAT THIS IS AND IS NOT, corrected after mutating it. Today every
    // org-inbox peer the backend mints is '@'-prefixed (`_extern_peer` →
    // @mcp:, api.py → @org:, net.py → @net:), so the bare-name collision
    // below cannot arrive from the server as things stand — this is DEFENCE
    // IN DEPTH against a frontend that must not rest on an invariant it does
    // not itself enforce.
    //
    // It is NOT the check that holds the row renderer's default in place. A
    // first draft of this comment said it was; mutating `rowSender ?? sender`
    // to `rowSender ?? <the built-in>` left this test GREEN, because the org
    // inbox hands its lists no `tierOf` and no `onFocusAgent`, so the
    // built-in renderer draws nothing here either. §5.3 is that check, at the
    // one place the difference can be made visible.
    const map = new Map<string, CanvasNode>([
      ['luna-reserve', node('luna-reserve', { tier: 'opus' })],
      ['ceo', node('ceo', { tier: 'opus' })],
    ])
    const collide: OrgInboxEntry = { ...oiIn, id: 'e-collide', peer: 'luna-reserve' }
    const { el } = await mount(
      <OrgInboxModal inbox={inboxBox([collide])}
        net={{ slug: 'mine.ncola.abc123', hubs: [] }} map={map} slug="mine"
        toast={noop} close={noop} jumpTo={null} onFocusAgent={noop} />)
    const f = mfrom(el)
    assert.ok(txt(f).includes('luna-reserve'),
      'positive control: the collision is REAL — this row does name it, and '
      + 'the tree above holds a live agent by that exact name')
    absent(f, '.tier', "an outside party does not borrow our agent's model")
    absent(f, 'button.cc-name-jump', 'nor a route into our tree')
  })

uiTest('§5.3 a list that DECLARES its counterparty plain text keeps a plain '
  + 'row even when the same list is also given a tier resolver and a focus '
  + 'handler', async ({ mount }) => {
    // ⚠ THIS IS THE ONE PLACE THE DEFAULT IS VISIBLE. `rowSender` falls back
    // to `sender`, not to the built-in identity renderer, so a call site that
    // said "plain text" once cannot be undone by a row it never thought
    // about. The fixture supplies `tierOf` and `onFocusAgent` DELIBERATELY:
    // they are exactly what a well-meaning future edit would add to the org
    // inbox, and they are what makes the wrong default draw a chip here.
    const jumped: string[] = []
    const { el } = await mount(
      <MailList delivered={[row()]}
        sender={(id: string) => <b>{id}</b>}
        tierOf={() => 'opus'} onFocusAgent={(id) => { jumped.push(id) }} />)
    const f = mfrom(el)
    assert.ok(txt(f).includes('peer-one'),
      'positive control: the row does name the party')
    absent(f, '.tier', 'the declaration wins: no chip in the row')
    absent(f, 'button.cc-name-jump', 'and no jump')
    // …and the CONTROL that the fixture could have produced one: the same
    // props with the declaration removed do draw both.
    const c = await mount(
      <MailList delivered={[row()]}
        tierOf={() => 'opus'} onFocusAgent={(id) => { jumped.push(id) }} />)
    assert.equal(q(mfrom(c.el), '.tier').length, 1,
      'control: without the plain-text declaration the very same props DO '
      + 'draw a chip — so the assertions above are not free')
    assert.equal(q(mfrom(c.el), 'button.cc-name-jump').length, 1,
      'control: …and a jump')
  })

// ═══════════════════════════════════════════════════════════════════ §6
// THE USER'S OWN INBOX composes `MailList` with `SenderChip` as its identity
// renderer (App.tsx, both folders — read there, not inferred). That chip
// predates `AgentName` and keeps its own markup by ruling, so it needs its
// own proof that it survives being put inside a row that is itself clickable.
// ═══════════════════════════════════════════════════════════════════ §6

uiTest('§6.1 SenderChip in a LIST ROW: chip, jump, and the row does not select '
  + 'underneath it', async ({ mount }) => {
    const nodes = new Map<string, TreeNode>([
      ['peer-one', { id: 'peer-one', tier: 'sonnet', state: 'live' } as TreeNode],
    ])
    const jumped: string[] = []
    const { el } = await mount(
      <MailList delivered={[row()]}
        sender={(id: string) => <SenderChip id={id} nodes={nodes}
          onFocusAgent={(a) => { jumped.push(a) }} />} />)
    const f = mfrom(el)
    assert.equal(q(f, '.tier').length, 1, 'the row carries the model chip')
    const jump = q(f, 'button.cc-name-jump')
    assert.equal(jump.length, 1, 'and the name is a jump')
    assert.equal(selected(el), false, 'nothing selected yet')
    await inAct(() => { jump[0]!.click() })
    assert.deepEqual(jumped, ['peer-one'], 'the name navigated')
    assert.equal(selected(el), false,
      'and did NOT also select the mail — SenderChip stops the bubble too')
    await inAct(() => { (rowsOf(el)[0]!.querySelector('.l2') as HTMLElement).click() })
    assert.equal(selected(el), true,
      'positive control: the row body still selects, so the check above could fail')
  })

// ═══════════════════════════════════════════════════════════════════ §7
// THE ARCHIVED TRANSCRIPT. `LineagePanel` reads a prior generation's real
// conversation through the same `Msg`, mail cards and all — so it is the one
// remaining place a mail card could have fallen back to a bare name.
// ═══════════════════════════════════════════════════════════════════ §7

function bearerNode(): CanvasNode {
  return {
    ...node('agent-a', { tier: 'opus' }),
    generation: 2,
    lineage: [{
      id: 'agent-a@1', tier: 'opus', generation: 1, state: 'archived',
      bearer_state: 'preserving', at: '2026-09-01T10:00:00.000Z',
    }],
  } as unknown as CanvasNode
}

/** the panel fetches the archived transcript; serve it one mail envelope */
function serveTranscript(from: string) {
  const s = new FakeServer()
  s.userMsg(envelope(from, 'your superior'))
  installFetch(s)
}

uiTest('§7.1 a mail card inside an archived generation carries the same chip '
  + 'and the same route as one in the live desk', async ({ mount }) => {
    serveTranscript('peer-one')
    const jumped: string[] = []
    const closed: number[] = []
    const { el } = await mount(
      <LineagePanel node={bearerNode()} slug="org"
        op={() => Promise.resolve({} as OpResult)}
        map={new Map<string, CanvasNode>([['peer-one', node('peer-one', { tier: 'sonnet' })]])}
        onFocusAgent={(id) => { jumped.push(id) }}
        close={() => { closed.push(1) }} />)
    const read = q(el, 'button').find((b) => txt(b).trim() === 'read')
    assert.ok(read, 'positive control: the panel offers a transcript to read')
    await inAct(() => { read!.click() })
    await flush()
    const h = el.querySelector('.lin-read .turn-mail-head') as HTMLElement | null
    assert.ok(h, 'positive control: the archived transcript rendered a mail CARD')
    assert.equal(q(h!, '.tier').length, 1, 'with the sender\'s model chip')
    const jump = q(h!, 'button.cc-name-jump')
    assert.equal(jump.length, 1, 'and a route to that agent')
    await inAct(() => { jump[0]!.click() })
    // ⚠ A MODAL MUST CLOSE BEFORE IT NAVIGATES, or the camera glides to a desk
    // sitting behind this overlay. Every other modal here does the same.
    assert.deepEqual(jumped, ['peer-one'], 'the click focused that agent')
    assert.equal(closed.length, 1, 'and closed the panel first')
  })

uiTest('§7.2 …and the same panel with no tree behind it draws a plain name',
  async ({ mount }) => {
    // the anti-vacuity pair: identical mount but for the two new props, which
    // is exactly what this panel looked like before they existed
    serveTranscript('peer-one')
    const { el } = await mount(
      <LineagePanel node={bearerNode()} slug="org"
        op={() => Promise.resolve({} as OpResult)} close={noop} />)
    const read = q(el, 'button').find((b) => txt(b).trim() === 'read')
    await inAct(() => { read!.click() })
    await flush()
    const h = el.querySelector('.lin-read .turn-mail-head') as HTMLElement | null
    assert.ok(h, 'positive control: the card is still rendered')
    assert.ok(txt(h!).includes('peer-one'), 'and still names the sender')
    absent(h!, '.tier', 'but with no tree to ask, there is no chip')
    absent(h!, 'button.cc-name-jump', 'and nowhere to go')
  })
