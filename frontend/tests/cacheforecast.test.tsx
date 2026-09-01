import './harness'
import { mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { CacheForecastMark, CacheForecastWarning } from '../src/canvas/desk'
import type { CacheForecast, CacheForecastState } from '../src/types'

const forecast = (
  state: CacheForecastState,
  action: CacheForecast['precompact_action'] = 'not_applicable',
): CacheForecast => ({
  generation: 'opaque-generation', state,
  reason: state === 'known_incompatible' ? 'three identity components changed' : 'observed',
  source: 'provider receipts', lane: 'subscription',
  last_receipt_at: '2026-09-01T10:00:00Z', ttl_seconds: 3600,
  expires_at: '2026-09-01T11:00:00Z',
  changed_inputs: state === 'known_incompatible'
    ? ['system prompt', 'callable tools', 'credential lane'] : [],
  precompact_action: action,
  precompact_reason: action === 'will_compact'
    ? 'context is above the configured minimum'
    : action === 'miss_expected' ? 'automatic policy is off' : '',
})

test('cache badge has exactly the selected green/red/grey state mapping', async () => {
  const view = await mountView(<>
    <CacheForecastMark forecast={forecast('compatible_observed')} />
    <CacheForecastMark forecast={forecast('expired_known_entry')} />
    <CacheForecastMark forecast={forecast('known_incompatible')} />
    <CacheForecastMark forecast={forecast('uncertain')} />
  </>, (el) => el)
  try {
    const marks = [...view.el.querySelectorAll<HTMLElement>('.cache-forecast')]
    assert.deepEqual(marks.map((m) => [...m.classList][1]),
      ['compatible', 'cold', 'cold', 'uncertain'])
    assert.deepEqual(marks.map((m) => m.textContent?.trim()),
      ['cache ✓', 'cache ×', 'cache ×', 'cache ?'])
    const incompatible = marks[2]?.getAttribute('aria-label') ?? ''
    for (const item of ['system prompt', 'callable tools', 'credential lane']) {
      assert.match(incompatible, new RegExp(item), `tooltip omitted ${item}`)
    }
    assert.match(incompatible, /60 minutes \(subscription authentication\)/)
    assert.match(incompatible, /last authoritative inference receipt: 2026/)
  } finally { await view.unmount() }
})

test('only known incompatibility warns at send time with policy-owned colour', async () => {
  const view = await mountView(<>
    <CacheForecastWarning forecast={forecast('compatible_observed')} />
    <CacheForecastWarning forecast={forecast('expired_known_entry')} />
    <CacheForecastWarning forecast={forecast('uncertain')} />
    <CacheForecastWarning forecast={forecast('known_incompatible', 'miss_expected')} />
    <CacheForecastWarning forecast={forecast('known_incompatible', 'will_compact')} />
  </>, (el) => el)
  try {
    const warnings = [...view.el.querySelectorAll<HTMLElement>('.cache-send-warning')]
    assert.equal(warnings.length, 2)
    assert.equal(warnings[0]?.classList.contains('miss'), true)
    assert.match(warnings[0]?.textContent ?? '', /Cache miss expected/)
    assert.equal(warnings[1]?.classList.contains('compact'), true)
    assert.match(warnings[1]?.textContent ?? '', /will cheap-compact/)
  } finally { await view.unmount() }
})
