// orgstate.test.tsx — the [ORG STATE] block never reaches the reader's eye.
//
// D-192 (user, 2026-08-29):
//
//   "i really do not think the org structure needs to be seen by the user;
//    that's extraneous information to them that they can just observe
//    directly."
//   "since it's rather short comparatively it's fine to send it every fresh
//    turn start, but it still shouldn't take up the visual chat history."
//
// D-181 prepends an [ORG STATE …] block — roster, peers, chart, credits — to
// EVERY non-command turn, so that live org state stops churning the agent's
// cached system prompt. That is a DELIVERY decision and it stands: the block
// still goes to the agent every turn. This file is about the other half — it
// must not occupy a row in the user's chat history.
//
// ⚠ AND IT OWNS A REGRESSION D-181 CAUSED, which is the more interesting half.
// The wire order is [ORG STATE] · [ORG NOTICES] · [MAIL] · body, and desk.tsx's
// NOTICE_RE is ANCHORED AT STRING START. So the moment the state block shipped,
// NOTICE_RE stopped matching and the notices card silently stopped rendering —
// the reader got raw `[ORG NOTICES …]` chrome in the bubble instead. §3 is that
// leg. Any future block prepended AHEAD of these will do the same thing again.
//
// ANTI-VACUITY (house rule, cf. sysnotice.test.tsx): every leg that asserts an
// ABSENCE is paired with a fixture where the same string is PRESENT. A selector
// that matches nothing, or an assertion that reads an empty DOM, would
// otherwise pass while proving nothing — which is the exact failure mode the
// D-181 work was commissioned to chase.
//
// Run:  cd frontend && node tests/run.mjs orgstate

import {
  FakeServer, flush, inAct, installFetch, mountView, realClock, useFakeClock,
} from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { refreshConvo, resetConvos } from '../src/convo'
import { DeskChat } from '../src/canvas/desk'
import type { CanvasNode } from '../src/canvas/shared'
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

function deskEl(nd: CanvasNode, slug: string) {
  return (
    <DeskChat node={nd} map={new Map([[nd.id, nd]])} op={op} slug={slug}
      toast={noop} pub={false} bare />
  )
}

function domTest(name: string,
  body: (k: { SL: string; ND: string; s: FakeServer;
    mount: (el: React.ReactElement) => Promise<{ el: HTMLElement }> }) => Promise<void>): void {
  test(name, async (t: TestContext) => {
    useFakeClock()
    const SL = 'org'
    const ND = `os${++_n}`
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
        return { el: v.el }
      },
    })
  })
}

const txt = (el: HTMLElement) => el.textContent ?? ''

// ------------------------------------------------------------- the fixtures
// Shaped exactly as `_run_one_turn` assembles them, in wire order.
const STATE = [
  '[ORG STATE — current as of 2026-08-29T09:52:43.618Z. This block is re-sent'
  + ' every turn and EARLIER COPIES IN THIS CONVERSATION ARE STALE: where they'
  + ' disagree with this one, this one is right.]',
  'Your reports: none yet. Your peers: multi-provider-fix, drag-zoom-bug.',
  'Credits: seat 5, grant 0, free 0 — credits bound concurrent agent capacity,'
  + ' not tokens.',
  '[END ORG STATE]',
].join('\n')

const NOTICES = [
  '[ORG NOTICES — 1 change(s) since your last turn]',
  '- 2026-08-29T09:11:24.633Z: The user hired "gemini-provider" (fable).',
  '[END NOTICES]',
].join('\n')

const BODY = 'PAYLOAD-SENTINEL the actual instruction the sender wrote'

// ======================================================================= §1
// the block does not reach the reader
// ======================================================================= §1

domTest('§1 the [ORG STATE] block is absent from the rendered chat',
  async ({ SL, ND, s, mount }) => {
    s.userMsg(STATE + '\n\n' + BODY)
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    const t = txt(el)
    // ANTI-VACUITY: the same bubble must still carry the real message, or an
    // empty desk would satisfy every absence below.
    assert.ok(t.includes('PAYLOAD-SENTINEL'),
      'the message body did not render at all — this DOM proves nothing')
    assert.ok(!t.includes('[ORG STATE'), 'the ORG STATE marker is on screen')
    assert.ok(!t.includes('[END ORG STATE]'), 'the closing marker is on screen')
    assert.ok(!t.includes('EARLIER COPIES'), 'the block header is on screen')
    assert.ok(!t.includes('Your peers:'), 'the roster is on screen')
    assert.ok(!t.includes('Credits: seat'), 'the credit balance is on screen')
    assert.ok(!t.includes('multi-provider-fix'),
      'a peer name from the chart is on screen')
  })

domTest('§1b CONTROL: without the strip the same fixture WOULD show — the '
  + 'assertions above are not matching an empty DOM',
  async ({ SL, ND, s, mount }) => {
    // Same desk, same pipeline, but the marker-bearing text arrives as an
    // ASSISTANT message, which is not enveloped and so is not stripped. If
    // this leg cannot see the block, the §1 selectors are broken rather than
    // the feature working.
    s.assistantMsg(STATE + '\n\n' + BODY)
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    const t = txt(el)
    assert.ok(t.includes('Your peers:'),
      'the control cannot see the roster either — §1 is vacuous')
    assert.ok(t.includes('Credits: seat'),
      'the control cannot see the credits either — §1 is vacuous')
  })

// ======================================================================= §2
// delivery is untouched — this is a display fix, not a retraction of D-181
// ======================================================================= §2

domTest('§2 the body survives verbatim beside the stripped block',
  async ({ SL, ND, s, mount }) => {
    s.userMsg(STATE + '\n\n' + BODY)
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    assert.ok(txt(el).includes(BODY),
      'stripping the block ate part of the message the sender wrote')
  })

// ======================================================================= §3
// the regression D-181 caused: notices must still card
// ======================================================================= §3

domTest('§3 with the state block AHEAD of it, the notices card still renders',
  async ({ SL, ND, s, mount }) => {
    s.userMsg(STATE + '\n\n' + NOTICES + '\n\n' + BODY)
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    assert.ok(txt(el).includes('PAYLOAD-SENTINEL'), 'the body did not render')
    // The notice is CARDED, not deleted — unlike the state block. The card is
    // collapsed by default, so its content is behind the toggle; assert the
    // card exists, then open it and read what it holds. (Asserting on the
    // collapsed textContent alone would fail for the right feature, which is
    // how this leg was first written and what caught the mistake.)
    const card = el.querySelector<HTMLElement>('.noticeline')
    assert.ok(card, 'the notices card is gone — NOTICE_RE stopped matching '
      + 'because the state block was prepended ahead of it')
    assert.ok((card.textContent ?? '').includes('1 notice'),
      `the card does not report the one notice: ${card.textContent}`)
    await inAct(() => { card.click() })
    assert.ok(txt(el).includes('gemini-provider'),
      'the expanded card does not carry the notice text')
    // …and its machine chrome is not in the bubble
    const t = txt(el)
    assert.ok(!t.includes('[ORG NOTICES'),
      'raw [ORG NOTICES] chrome leaked into the bubble')
    assert.ok(!t.includes('[END NOTICES]'), 'the notices terminator is on screen')
    assert.ok(!t.includes('[ORG STATE'), 'and the state block is still gone')
  })

domTest('§3b CONTROL: the notices fixture really does carry that chrome',
  async ({ SL, ND, s, mount }) => {
    // Prove the §3 strings exist in the input. If the fixture were malformed,
    // §3's absence assertions would pass against text that never had them.
    s.assistantMsg(NOTICES)
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    assert.ok(txt(el).includes('[ORG NOTICES'),
      'the fixture does not contain the chrome §3 claims to strip')
  })

// ======================================================================= §4
// ordering independence — the strip must not depend on notices being present
// ======================================================================= §4

domTest('§4 the block is stripped whether or not notices ride behind it',
  async ({ SL, ND, s, mount }) => {
    s.userMsg(STATE + '\n\n' + BODY)
    s.userMsg(STATE + '\n\n' + NOTICES + '\n\n' + BODY)
    await refreshConvo(SL, ND)
    const { el } = await mount(deskEl(node(ND), SL))
    await flush()
    const t = txt(el)
    assert.ok(!t.includes('[ORG STATE'),
      'one of the two shapes leaked the block')
    assert.ok(!t.includes('[END ORG STATE]'), 'a terminator leaked')
    // exactly one of the two bubbles carried notices, so exactly one card
    assert.equal(el.querySelectorAll('.noticeline').length, 1,
      'the notices card did not survive alongside a block-only bubble')
  })
