import './harness'
import { mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { EventCard } from '../src/events/card'
import { FAMILIES } from '../src/generated/events'
declare const __SRC_DIR__: string
const directory = path.resolve(__SRC_DIR__, '../tests/fixtures/events')
const fixtures = readdirSync(directory).filter(f => f.endsWith('.json')).map(f => JSON.parse(readFileSync(path.join(directory, f), 'utf8')))

test('operator and visitor render every canonical family with a type heading', async t => {
  for (const profile of ['operator', 'public'] as const) {
    const view = await mountView(<div>{fixtures.map(f => <EventCard key={f.variant} org="fixture" profile={profile}
      row={profile === 'operator' ? { ev: f.private, body: f.body } : { ev_public: f.public, body: f.body }} />)}</div>, h => h)
    t.after(() => view.unmount())
    assert.equal(view.el.querySelectorAll('[data-event-variant]').length, fixtures.length)
    assert.equal(view.el.querySelectorAll('.event-unsupported').length, 0)
    for (const family of FAMILIES) {
      const cards = view.el.querySelectorAll('.event-card.event-' + family)
      assert.ok(cards.length > 0, profile + ' ' + family)
      assert.ok(cards[0]!.querySelector('.event-head strong')!.textContent)
    }
  }
})

test('public reply renders allowed unique content and activates its qualified object link', async t => {
  const fixture = fixtures.find(f => f.variant === 'reply.document')
  const ev_public = { ...fixture.public, body: 'Allowed unique reply C', object: {
    kind: 'document', id: 'plan', title: 'Recorded plan title', node: 'agent' } }
  const opened: string[] = []
  const view = await mountView(<EventCard org="fixture" profile="public" row={{ ev_public, body: 'Full original body' }}
    world={{ org: 'fixture' }} onOpen={r => opened.push(r.ref.org + '/' + r.ref.id)} />, h => h)
  t.after(() => view.unmount())
  assert.equal(view.el.querySelector('.event-head strong')!.textContent, 'Presentation reply')
  assert.match(view.el.querySelector('.event-body')!.textContent!, /Allowed unique reply C/)
  assert.equal(view.el.querySelector('.event-card')!.classList.contains('event-linked_reply'), true)
  const link = view.el.querySelector('button.ref-chip') as HTMLButtonElement
  assert.ok(link, 'permitted object action exists')
  link.click(); assert.deepEqual(opened, ['fixture/plan'])
  assert.doesNotMatch(view.el.textContent!, /Full original body/)
})

test('public engine message shows system actor while user message shows User', async t => {
  const engine = fixtures.find(f => f.variant === 'runtime.ui_crash_report')
  const user = fixtures.find(f => f.variant === 'ordinary.message')
  const view = await mountView(<div>
    <EventCard org="fixture" profile="public" row={{ from: 'user', ev_public: engine.public }} />
    <EventCard org="fixture" profile="public" row={{ from: 'user', ev_public: { ...user.public, actor: { kind: 'user', id: 'user' } } }} />
  </div>, h => h)
  t.after(() => view.unmount())
  assert.equal(view.el.querySelector('[data-actor-kind="system"]')!.textContent, 'System')
  assert.equal(view.el.querySelector('[data-actor-kind="user"]')!.textContent, 'User')
  assert.doesNotMatch(view.el.textContent!, /report.stack|report.url/)
})
