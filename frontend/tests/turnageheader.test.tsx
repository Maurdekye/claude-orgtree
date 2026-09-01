import './harness'
import { mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { LastTurnAge } from '../src/canvas/desk'
import type { TurnStat } from '../src/types'

declare const __SRC_DIR__: string

const turn = (at: string, killed = false): TurnStat => ({
  at, killed, cost: 0, denials: 0,
})

test('card and focused-desk ages share one authoritative component', async () => {
  const stamp = turn(new Date(Date.now() - 120_000).toISOString(), true)
  const view = await mountView(<>
    <div data-surface="card"><LastTurnAge turn={stamp} /></div>
    <div data-surface="desk"><LastTurnAge turn={stamp} /></div>
    <div data-surface="busy"><LastTurnAge turn={stamp} busy /></div>
    <div data-surface="never"><LastTurnAge /></div>
  </>, (el) => el)
  try {
    const card = view.el.querySelector<HTMLElement>('[data-surface="card"] .turnago')!
    const desk = view.el.querySelector<HTMLElement>('[data-surface="desk"] .turnago')!
    assert.equal(card.textContent, desk.textContent)
    assert.equal(card.title, desk.title)
    assert.match(card.title, /^last turn ended .* \(killed\)$/)
    assert.equal(view.el.querySelector('[data-surface="busy"] .turnago'), null)
    assert.equal(view.el.querySelector('[data-surface="never"] .turnago'), null)
  } finally { await view.unmount() }
})

test('the shared age clock advances without a tree refetch', async () => {
  const stamp = turn(new Date(Date.now() - 10_000).toISOString())
  const view = await mountView(<LastTurnAge turn={stamp} />, (el) => el)
  try {
    const before = view.el.querySelector('.turnago')?.textContent
    const { act } = await import('react')
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1150))
    })
    const after = view.el.querySelector('.turnago')?.textContent
    assert.notEqual(after, before, 'age stayed frozen until an unrelated refetch')
  } finally { await view.unmount() }
})

test('cards and desks both consume LastTurnAge and add no backend clock', () => {
  const cards = readFileSync(path.join(__SRC_DIR__, 'canvas', 'cards.tsx'), 'utf8')
  const desk = readFileSync(path.join(__SRC_DIR__, 'canvas', 'desk.tsx'), 'utf8')
  assert.ok((cards.match(/<LastTurnAge/g) ?? []).length >= 2,
    'zoomed-out card/map stopped using the shared badge')
  assert.match(desk, /<LastTurnAge turn=\{lastTurn\}[^>]*\/>/,
    'focused desk stopped using the shared badge')
  assert.doesNotMatch(cards, /ago\(lastTurn\.at\)/,
    'card reintroduced a second formatter/update path')
})
