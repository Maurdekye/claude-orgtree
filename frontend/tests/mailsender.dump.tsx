// mailsender.dump.tsx — STEP 1 of the mail-sender typography/hit probe.
//
// Renders the REAL surfaces under the repo's jsdom harness and writes their
// markup to a file:
//
//   · the user's own inbox — `InboxPanel` (App.tsx), which is where the
//     `.overlay > .settings` cascade applies. That cascade is the hazard: a
//     `.settings button` rule with `font-size: 14px; padding: 7px 15px;
//     border-radius: 6px` will punch a hole in an 11px mail row unless the
//     name button is reset. The same trap has been hit here before (see the
//     `.docket-ref` comment in styles.css).
//   · the desk transcript's mail card — the real `DeskChat`, so the card sits
//     inside a real `.desk-body` and the chip rule that keys on it applies.
//
// It asserts nothing about how any of it LOOKS, deliberately: jsdom has no CSS
// box model and no cascade, so "the row did not grow" is not a question it can
// answer, and an abstention reads exactly like a pass. Step 2
// (`mailsender_probe.py`) loads this markup plus the real `src/styles.css`
// into Edge and measures there. What this step guarantees is that what gets
// measured is the components' own output — classes, nesting and all.

import '../tests/harness'
import { writeFileSync } from 'node:fs'
import { createElement } from 'react'
import type { InboxPayload, TreePayload } from '../src/types'

const AT = '2026-09-05T10:00:00.000Z'

const INBOX: InboxPayload = {
  pending: [
    { id: 'p1', from: 'coordinator-astra', kind: 'message', at: AT,
      body: 'the two missed sender surfaces are yours — pick them up now' },
  ],
  delivered: [
    { id: 'd1', from: 'checklist-evidence', kind: 'message', at: '2026-09-05T09:30:00.000Z',
      body: 'landed the first pass; the list row and the transcript are still bare' },
    // ⚠ THE HEIGHT CONTROL, and the reason this row exists. `nobody-here` is
    // not in the tree, so it draws a name with NO chip — otherwise the same
    // two-line row. "The chip did not make the row taller" is then a
    // comparison between two rows that differ in exactly one thing. (The
    // system notice below is NOT that control: it is deliberately one line
    // shorter and a size smaller, and using it reported a failure that had
    // nothing to do with this work.)
    { id: 'd15', from: 'nobody-here', kind: 'message', at: '2026-09-05T09:15:00.000Z',
      body: 'a sender this tree cannot vouch for — a name, and nothing else' },
    // an @-sentinel: no chip, no button. It is in the dump so the probe can
    // measure a row WITHOUT an identity against one WITH — that pair is what
    // makes "the chip did not change the row height" a real comparison
    // instead of an assertion about one number.
    { id: 'd2', from: '@system', kind: 'notice', at: '2026-09-05T09:00:00.000Z',
      body: 'a system notice, which is never an agent' },
  ],
  sent: [],
} as unknown as InboxPayload

const TREE: TreePayload = {
  slug: 'org1', name: 'Org 1', epoch: 1, rev: 1,
  roots: [{
    id: 'coordinator-astra', tier: 'opus', generation: 1, state: 'live',
    children: [{
      id: 'checklist-evidence', tier: 'fable', generation: 2, state: 'live',
      children: [],
    }],
  }],
  work_items_summary: { attention: 0, active: 0 },
  user_inbox_count: 1, user_inbox_urgent_count: 0, asks: [], asks_open: 0,
} as unknown as TreePayload

/** the envelope the backend really writes (supervisor `_mail_block`) */
const ENVELOPE =
  '[MAIL — 1 message(s)]\n'
  + 'FROM coordinator-astra (your superior) · request · 2026-09-05T10:00:00Z\n'
  + 'pick up the two missed sender surfaces now.\n'
  + '[END MAIL]\n\n(orgtree) You have new mail above.'

;(globalThis as unknown as Record<string, unknown>).fetch = (url: string) => {
  const ok = (payload: unknown) => Promise.resolve({
    ok: true, status: 200, headers: new Headers(),
    json: () => Promise.resolve(payload),
  })
  const u = String(url)
  if (u.includes('/inbox')) return ok(INBOX)
  if (u.includes('/audiences')) return ok({ audiences: [], requests: [] })
  if (u.includes('/chat')) {
    return ok({
      messages: [{ role: 'user', text: ENVELOPE, seq: 1, ts: AT }],
      busy: false, responding: false, queued: 0, last_error: null,
      occupancy: 1000, live: [], pending_mail: [],
      draft_epoch: 'boot0:0', instance: 'inst-0',
    })
  }
  return ok({})
}

const main = async () => {
  const { mountView, flush } = await import('../tests/harness')
  const { act } = await import('react')
  const { InboxPanel } = await import('../src/App')
  const { DeskChat } = await import('../src/canvas/desk')
  const { refreshConvo } = await import('../src/convo')

  // ── the user's inbox, inside its own overlay/settings cascade ────────────
  const inbox = await mountView(
    createElement(InboxPanel, {
      slug: 'org1', tree: TREE, toast: () => {}, close: () => {},
      jumpTo: null, onFocusAgent: () => {},
    }),
    (el: HTMLElement) => el.innerHTML)
  await act(async () => { await flush(8) })
  const inboxHtml = inbox.last()

  // ── the transcript's mail card, inside a real desk body ──────────────────
  const nd = {
    id: 'checklist-evidence', state: 'live', tier: 'fable', children: [],
    seat: 1, grant: 0, free: 0, scope: { tools: {}, add_dirs: [] },
    model_id: 'fable',
  }
  const astra = {
    id: 'coordinator-astra', state: 'live', tier: 'opus', children: [],
    seat: 1, grant: 0, free: 0, scope: { tools: {}, add_dirs: [] },
    model_id: 'opus',
  }
  const map = new Map<string, unknown>([[nd.id, nd], [astra.id, astra]])
  const desk = await mountView(
    createElement(DeskChat, {
      node: nd, map, op: () => Promise.resolve({}), slug: 'org1',
      toast: () => {}, pub: false, bare: true, onJump: () => {},
    } as never),
    (el: HTMLElement) => el.innerHTML)
  await refreshConvo('org1', nd.id, { force: true })
  await act(async () => { await flush(8) })
  const deskHtml = desk.last()

  // ⚠ FAIL LOUD IF THE MARKUP IS NOT THE THING UNDER TEST. Without this the
  // dump could be a "loading…" placeholder, or either surface could have
  // stopped drawing an identity at all, and the probe downstream would
  // measure nothing and report no failures — the abstention that reads as a
  // pass, one layer up.
  const want: [string, string, number][] = [
    ['inbox', 'mailrow', 4],
    // the chip and the jump, IN A ROW — not in the reading pane
    ['inbox', 'mfrom', 4],
    ['inbox', 'tier t-opus', 1],
    ['inbox', 'cc-name cc-name-jump', 1],
    ['desk', 'turn-mail-head', 1],
    ['desk', 'turn-mail-from', 1],
    ['desk', 'tier t-opus', 1],
  ]
  const src: Record<string, string> = { inbox: inboxHtml, desk: deskHtml }
  for (const [which, cls, n] of want) {
    const got = (src[which]!.match(new RegExp(cls, 'g')) ?? []).length
    if (got < n) {
      throw new Error(`the ${which} dump has ${got} × "${cls}", want at least `
        + `${n} — the fixture is wrong, or the surface stopped drawing it. `
        + `First 500 chars:\n${src[which]!.slice(0, 500)}`)
    }
  }
  // ⚠ AND THE ROW MUST BE THE ONE THAT MATTERS. The reading pane also draws a
  // SenderChip, so "a chip is in the inbox markup" is not evidence the ROW has
  // one. Cut the list out and require the chip and the button inside it.
  const listOnly = inboxHtml.slice(inboxHtml.indexOf('mailer-list'),
    inboxHtml.indexOf('mailer-read'))
  if (!listOnly.includes('tier t-opus') || !listOnly.includes('cc-name-jump')) {
    throw new Error('the LIST section carries no chip/jump — the pane\'s copy '
      + 'would have satisfied the counts above and proved nothing')
  }

  const dest = process.argv.slice(2).find((a) => !a.startsWith('--'))
  if (!dest) throw new Error('usage: mailsender.dump <out.html>')
  // both surfaces in ONE page: the probe measures the row and the card in the
  // same engine, under the same sheet, in one run
  // (`bare` DeskChat renders its own `.desk-body` wrapper, so nothing is
  // added around it here — the cascade the card sees is its own.)
  const html = `<div class="viewport">${inboxHtml}${deskHtml}</div>`
  writeFileSync(dest, html)
  console.log(`dumped ${html.length} bytes`)
}

await main()
// jsdom's window and the panels' poll timers hold the event loop open; this is
// a one-shot dump, so leave rather than wait for them
process.exit(0)
