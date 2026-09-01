import './harness'
import { mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { useState } from 'react'
import { HeaderTurnSeat, LastTurnAge } from '../src/canvas/desk'
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

test('one stable seat substitutes live activity for age and restores age', async () => {
  const stamp = turn(new Date(Date.now() - 120_000).toISOString())
  const inflight = new Date(Date.now() - 300_000).toISOString()
  function Probe() {
    const [active, setActive] = useState(false)
    return <><button onClick={() => setActive((v) => !v)}>toggle</button>
      <HeaderTurnSeat active={active} turn={stamp} inflightAt={inflight}
        tasks={2} /></>
  }
  const view = await mountView(<Probe />, (el) => el)
  try {
    const seat = () => view.el.querySelector('.cc-turn-seat')!
    assert.ok(seat().querySelector('.turnago'))
    assert.equal(seat().querySelector('.cc-working'), null)
    const { act } = await import('react')
    await act(async () => { view.el.querySelector<HTMLButtonElement>('button')!.click() })
    assert.equal(seat().querySelector('.turnago'), null)
    assert.match(seat().querySelector('.cc-working')?.textContent ?? '',
      /working.*5m.*2 tasks/)
    assert.equal(seat().querySelectorAll('.cc-working').length, 1)
    await act(async () => { view.el.querySelector<HTMLButtonElement>('button')!.click() })
    assert.ok(seat().querySelector('.turnago'))
    assert.equal(seat().querySelector('.cc-working'), null)
  } finally { await view.unmount() }
})

test('the activity seat names compacting and queued states with elapsed time', async () => {
  const inflight = new Date(Date.now() - 300_000).toISOString()
  const view = await mountView(<>
    <HeaderTurnSeat active phase="compacting" inflightAt={inflight} />
    <HeaderTurnSeat active waiting inflightAt={inflight} />
  </>, (el) => el)
  try {
    const rows = [...view.el.querySelectorAll('.cc-turn-seat')]
    assert.equal(rows.length, 2)
    assert.match(rows[0]!.textContent ?? '', /compacting….*5m/)
    assert.match(rows[1]!.textContent ?? '', /queued for a turn slot….*5m/)
    assert.equal(view.el.querySelectorAll('.turnago').length, 0)
  } finally { await view.unmount() }
})

test('cards and desks both consume LastTurnAge and add no backend clock', () => {
  const cards = readFileSync(path.join(__SRC_DIR__, 'canvas', 'cards.tsx'), 'utf8')
  const desk = readFileSync(path.join(__SRC_DIR__, 'canvas', 'desk.tsx'), 'utf8')
  assert.ok((cards.match(/<LastTurnAge/g) ?? []).length >= 2,
    'zoomed-out card/map stopped using the shared badge')
  assert.match(desk, /<HeaderTurnSeat active=\{turnActive\} turn=\{lastTurn\}/,
    'focused desk stopped using the shared age/activity seat')
  assert.match(desk, /<LastTurnAge turn=\{turn\}[^>]*\/>/,
    'the shared seat stopped using LastTurnAge when idle')
  assert.doesNotMatch(cards, /ago\(lastTurn\.at\)/,
    'card reintroduced a second formatter/update path')
})
