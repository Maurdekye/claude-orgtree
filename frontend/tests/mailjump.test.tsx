// mailjump.test.tsx — a reference to a MESSAGE, followed.
//
// Astra rejected the first design outright ("do not silently open a mailbox")
// and the rejection is the spec: reuse the addressed inbox fetch, select the
// exact message once it has loaded, and give an EXPLICIT unavailable outcome
// when it is not there — disclosing nothing about it.
//
// THE THREE FAILURES THIS EXISTS TO CATCH ARE ALL SILENT ONES. Not a crash,
// not a wrong mail: a panel that opens looking perfectly ordinary while the
// thing you clicked for is not on screen and nothing says why.
//
//   1. THE MESSAGE IS NOT HERE and the pane shows "select a mail to read it".
//      Indistinguishable from having misclicked.
//   2. THE SECOND LINK DOES NOTHING. The box is already mounted, so the
//      selection — initialised once at mount — never moves again. The first
//      reference of a session works and every one after it appears dead.
//   3. THE MESSAGE IS IN THE OTHER FOLDER. A node's Sent list was not even
//      given the jump, so a reference to something an agent SENT could never
//      be found, with the mail one unmarked click away.
//
// ⚠ AND ONE THING THAT MUST NOT LEAK. A message may be missing because this
// viewer is not allowed to see it. An explanation that quotes the sender or
// the subject is not a refusal — it is the disclosure, wearing an apology. §4
// asserts the absence of content, which is a check that can only be written
// by naming what must not appear.
//
// Run: cd frontend && node tests/run.mjs mailjump

import { flush, inAct, mountView } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { MailList } from '../src/canvas/mail'
import type { MailRow } from '../src/canvas/shared'

// ⚠ jsdom IMPLEMENTS NO LAYOUT, so `Element.scrollIntoView` does not exist
// there and the product's jump handler throws on the first render. Stubbed
// here rather than guarded in the source: the call is correct in a browser,
// and adding `?.` to product code to satisfy a test environment hides a real
// missing-method bug behind a shrug. The stub records nothing — these checks
// are about which message is SELECTED, not about scrolling, which no DOM test
// can observe anyway.
{
  // reached through a real element rather than a global: the harness installs
  // jsdom's document, not its constructors
  const win = document.createElement('div').ownerDocument.defaultView as
    unknown as { Element: { prototype: Record<string, unknown> } }
  const proto = win.Element.prototype
  if (typeof proto.scrollIntoView !== 'function') {
    proto.scrollIntoView = function scrollIntoView() { /* no layout in jsdom */ }
  }
}

function uiTest(name: string, body: (mount: (el: React.ReactElement)
  => Promise<{ el: HTMLElement; render: (n: React.ReactElement)
    => Promise<unknown> }>) => Promise<void>): void {
  test(name, async (t: TestContext) => {
    const open: { unmount: () => Promise<void> }[] = []
    t.after(async () => {
      for (const m of open) { try { await m.unmount() } catch { /* gone */ } }
    })
    await body(async (el) => {
      const v = await mountView(el, (host) => host)
      open.push(v)
      return { el: v.el, render: v.render }
    })
  })
}

// distinct timestamps: MailList sorts by send time, so one shared `at` would
// be asserting on the stability of Array.sort instead of on the feature
const row = (n: number, over: Partial<MailRow> = {}): MailRow => ({
  id: 'm' + n, from: 'alpha', kind: 'message',
  body: 'the body of message ' + n,
  at: `2026-09-05T1${9 - n}:00:00Z`, ...over,
} as MailRow)

const THREE = [row(1), row(2), row(3)]
const pane = (el: HTMLElement) =>
  el.querySelector('.mailer-read')?.textContent ?? ''

uiTest('§1 a jump selects the exact message, not merely the newest',
  async (mount) => {
    // the target is deliberately NOT the newest row: a panel that simply
    // opened the top of the list would pass a check aimed at row 1.
    const { el } = await mount(<MailList delivered={THREE} jumpTo="m3" />)
    await flush()
    assert.match(pane(el), /the body of message 3/)
    assert.doesNotMatch(pane(el), /the body of message 1/)
  })

uiTest('§2 a SECOND jump moves the selection — the latch is per-jump, not '
  + 'once-ever', async (mount) => {
    // the box is already open. This is the ordinary case, not the edge one:
    // two references in the same paragraph, clicked one after the other.
    const { el, render } = await mount(
      <MailList delivered={THREE} jumpTo="m3" />)
    await flush()
    assert.match(pane(el), /message 3/)

    await render(<MailList delivered={THREE} jumpTo="m1" />)
    await flush()
    assert.match(pane(el), /message 1/, 'the second reference moved the pane')
    assert.doesNotMatch(pane(el), /message 3/)
  })

uiTest('§3 a jump to a message that is NOT in this folder says so, instead of '
  + 'falling through to "select a mail"', async (mount) => {
    const { el } = await mount(<MailList delivered={THREE} jumpTo="m9" />)
    await flush()
    const said = el.querySelector('.mailer-nojump')
    assert.ok(said, 'the panel states that the linked message is not here')
    assert.doesNotMatch(pane(el), /select a mail to read it/,
      'the generic invitation is the silent failure this replaces')
  })

uiTest('§4 CONTROL — that statement discloses nothing about the message',
  async (mount) => {
    // the reason it is missing may be that this viewer may not see it.
    const secret = row(9, { from: 'confidential-sender',
      body: 'the contents nobody may read' })
    const { el } = await mount(
      // the row exists in the WORLD but not in this folder's data
      <MailList delivered={THREE} jumpTo={secret.id!} />)
    await flush()
    const said = el.querySelector('.mailer-nojump')!.textContent ?? ''
    for (const leak of ['confidential-sender', 'nobody may read', 'm9']) {
      assert.ok(!said.includes(leak), `the notice leaked ${leak}`)
    }
    // ⚠ and it is not vacuous: it does actually say something
    assert.ok(said.trim().length > 20, 'the notice is empty, not discreet')
  })

uiTest('§5 CONTROL — a jump that IS found produces no such statement',
  async (mount) => {
    // §3 and §4 both assert on an element being present. If that element were
    // present unconditionally they would pass while the feature did nothing.
    const { el } = await mount(<MailList delivered={THREE} jumpTo="m2" />)
    await flush()
    assert.equal(el.querySelector('.mailer-nojump'), null)
    assert.match(pane(el), /message 2/)
  })

uiTest('§6 CONTROL — the search filter does not turn a found message into a '
  + 'missing one', async (mount) => {
    // the "is it here" question is asked over ALL rows, not the filtered
    // ones. Asked over the visible set, typing in the search box would make
    // the panel announce that a message it is holding does not exist.
    //
    // ⚠ SIX ROWS, NOT THREE: the filter box only renders above four, so a
    // three-row fixture would make this check pass by never filtering at all.
    const six = [1, 2, 3, 4, 5, 6].map((n) => row(n))
    const { el } = await mount(<MailList delivered={six} jumpTo="m6" />)
    await flush()
    assert.match(pane(el), /message 6/)
    const box = el.querySelector('input.mail-filter') as HTMLInputElement
    assert.ok(box, 'the search box renders above four rows')

    // React tracks the input's value on the node, so assigning `.value`
    // directly is swallowed as "no change" — the native setter is what makes
    // the synthetic onChange fire.
    const win = box.ownerDocument.defaultView as unknown as {
      HTMLInputElement: { prototype: object }; Event: typeof Event
    }
    const set = Object.getOwnPropertyDescriptor(
      win.HTMLInputElement.prototype, 'value')!.set!
    await inAct(() => {
      set.call(box, 'message 1')
      box.dispatchEvent(new win.Event('input', { bubbles: true }))
    })
    await flush()
    assert.equal(box.value, 'message 1', 'the filter really took the text')
    assert.ok(el.querySelectorAll('.mailrow').length < six.length,
      'and it really filtered — otherwise this control is inert')
    assert.equal(el.querySelector('.mailer-nojump'), null,
      'filtering the list must not be reported as a missing message')
  })

uiTest('§7 an outgoing folder honours a jump too', async (mount) => {
  // the node box passes `jumpTo` to Sent as well now; before, a reference to
  // something an agent had SENT could not be found in any folder.
  const sent = [row(1, { to: 'beta' }), row(2, { to: 'gamma' })]
  const { el } = await mount(
    <MailList delivered={sent} outgoing jumpTo="m2" />)
  await flush()
  assert.match(pane(el), /message 2/)
  assert.equal(el.querySelector('.mailer-nojump'), null)
})
