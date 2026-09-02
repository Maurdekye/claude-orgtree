import './harness'
import { mountView } from './harness'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import assert from 'node:assert/strict'
import { CacheForecastMark, CacheForecastWarning } from '../src/canvas/desk'
import type { CacheForecast, CacheForecastState } from '../src/types'

declare const __SRC_DIR__: string

const forecast = (
  state: CacheForecastState,
  action: CacheForecast['precompact_action'] = 'not_applicable',
  overrides: Partial<CacheForecast> = {},
): CacheForecast => ({
  generation: 'opaque-generation', state,
  // D-226: readiness is what the badge renders, and the backend derives it
  // from the same branch that produced `state`. The fixture mirrors that
  // pairing so these rows stay realistic; `uncertain` maps to the enumerated
  // capability diagnostic rather than to a bare unknown, because after D-226
  // there is no such thing as an unexplained grey.
  ...(state === 'compatible_observed'
    ? { readiness: 'ready' as const, readiness_cause: 'receipt_valid' }
    : state === 'uncertain'
      ? { readiness: 'diagnostic' as const,
          readiness_cause: 'unsupported_capability' }
      : { readiness: 'not_ready' as const,
          readiness_cause: state === 'expired_known_entry'
            ? 'receipt_expired' : 'prefix_changed' }),
  reason: state === 'known_incompatible' ? 'three identity components changed' : 'observed',
  source: 'provider receipts', lane: 'subscription',
  last_receipt_at: '2026-09-01T10:00:00Z', ttl_seconds: 3600,
  // ⚠ RELATIVE, not a fixed instant. The green badge now counts down to this
  // value and stops being green once it passes (user spec 2026-09-02), so a
  // hard-coded stamp made every `compatible_observed` fixture render as an
  // EXPIRED entry the moment that date went by — the state mapping below
  // started failing on its own, with nothing changed. A fixture that decays
  // into a different test than the one that was written is worse than no
  // fixture. Countdown behaviour itself is pinned against a frozen clock in
  // `cachecountdown.test.tsx`; this one only needs the entry to be live.
  expires_at: new Date(Date.now() + 3600_000).toISOString(),
  changed_inputs: state === 'known_incompatible'
    ? ['system prompt', 'callable tools', 'credential lane'] : [],
  precompact_action: action,
  precompact_reason: action === 'will_compact'
    ? 'context is above the configured minimum'
    : action === 'miss_expected' ? 'automatic policy is off' : '',
  ...overrides,
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
    // ⚠ The green badge no longer carries a ✓ when it has a live expiry to
    // count down to — the countdown REPLACES it (user spec 2026-09-02), and
    // showing both was explicitly ruled out. The remaining three glyphs are
    // unchanged. Asserted as a shape, not a string: the exact figure depends
    // on how long this file took to get here, and pinning it would make the
    // test fail on a slow machine. Countdown behaviour proper is pinned
    // against a frozen clock in `cachecountdown.test.tsx`.
    assert.match(marks[0]?.textContent?.trim() ?? '', /^cache \d+:\d\d(:\d\d)?$/)
    assert.deepEqual(marks.slice(1).map((m) => m.textContent?.trim()),
      ['cache ×', 'cache ×', 'cache ?'])
    const incompatible = marks[2]?.getAttribute('aria-label') ?? ''
    for (const item of ['system prompt', 'callable tools', 'credential lane']) {
      assert.match(incompatible, new RegExp(item), `tooltip omitted ${item}`)
    }
    assert.match(incompatible, /60 minutes \(subscription authentication\)/)
    assert.match(incompatible, /last authoritative inference receipt: 2026/)
    // D-226: the grey slot no longer says "unknown" — it names the fault that
    // stopped a verdict being formed, and carries a machine-readable cause so
    // a screenshot of the tooltip is enough to triage it.
    const grey = marks[3]?.getAttribute('aria-label') ?? ''
    assert.doesNotMatch(grey, /compatibility: unknown/)
    assert.match(grey, /no verdict — unsupported capability/)
    assert.match(grey, /readiness: diagnostic \(unsupported_capability\)/)
  } finally { await view.unmount() }
})

test('Codex subscription tooltip names the fixed estimate without promising a hit', async () => {
  const row = {
    ...forecast('compatible_observed'),
    ttl_seconds: 1800,
    source: 'codex_subscription_fixed_estimate',
    lane: 'subscription',
  }
  const view = await mountView(<CacheForecastMark forecast={row} />, (el) => el)
  try {
    const title = view.el.querySelector<HTMLElement>('.cache-forecast')
      ?.getAttribute('aria-label') ?? ''
    assert.match(title, /30 minutes \(Codex subscription estimate\)/)
    assert.match(title, /provider hit not guaranteed/)
  } finally { await view.unmount() }
})

test('D-226 reverses D-214: a supported lane with no completed turn is RED', async () => {
  // ⚠ THIS TEST USED TO ASSERT THE OPPOSITE. Under D-214 both rows below were
  // green, reasoning that with no completed turn there is nothing to conflict
  // with. The user ruled that green requires affirmative evidence of
  // compatibility; absence of evidence is not evidence. Both are now red, and
  // the copy must say "not established" rather than predict a miss.
  const view = await mountView(<>
    <CacheForecastMark forecast={forecast('uncertain', 'not_applicable', {
      source: 'no_completed_fingerprint', lane: 'subscription',
      readiness: 'not_ready', readiness_cause: 'no_completed_fingerprint',
      readiness_detail: 'No completed turn has been observed for this agent '
        + 'yet, so there is nothing to establish cache readiness from.',
    })} />
    <CacheForecastMark forecast={forecast('uncertain', 'not_applicable', {
      source: 'no_completed_fingerprint', lane: 'api_key', ttl_seconds: 300,
      readiness: 'not_ready', readiness_cause: 'no_completed_fingerprint',
      readiness_detail: 'No completed turn has been observed for this agent '
        + 'yet, so there is nothing to establish cache readiness from.',
    })} />
  </>, (el) => el)
  try {
    const marks = [...view.el.querySelectorAll<HTMLElement>('.cache-forecast')]
    assert.deepEqual(marks.map((m) => [...m.classList][1]), ['cold', 'cold'])
    assert.deepEqual(marks.map((m) => m.textContent?.trim()),
      ['cache ×', 'cache ×'])
    for (const mark of marks) {
      const title = mark.getAttribute('aria-label') ?? ''
      assert.match(title, /NOT compatibility-ready/)
      assert.match(title, /not established/)
      assert.match(title, /no_completed_fingerprint/)
      // Red here is NOT a claim that the provider will miss — there is no
      // entry to miss. The wording must not overclaim in either direction.
      assert.doesNotMatch(title, /observed hit/i)
      assert.doesNotMatch(title, /miss is expected/i)
    }
  } finally { await view.unmount() }
})

test('D-226: every grey is a NAMED diagnostic; ordinary uncertainty is red', async () => {
  // The old contract lumped all four of these into one "unknown" grey. The
  // user ruled that out: a provider that cannot report and a session that
  // simply has no receipt yet are different facts and must not share a colour.
  const view = await mountView(<>
    {/* a real capability gap — the only legitimate grey here */}
    <CacheForecastMark forecast={forecast('uncertain', 'not_applicable', {
      source: 'capability_unsupported', lane: 'provider_unsupported',
      readiness: 'diagnostic', readiness_cause: 'unsupported_capability',
      readiness_detail: 'Provider google on the provider_unsupported lane '
        + 'reports no cache TTL.',
    })} />
    {/* lane not observed YET: red, and it resolves itself */}
    <CacheForecastMark forecast={forecast('uncertain', 'not_applicable', {
      source: 'ttl_unobserved', lane: 'unobserved',
      readiness: 'not_ready', readiness_cause: 'lane_unobserved',
    })} />
    {/* no receipt yet on a supported lane: red, not grey */}
    <CacheForecastMark forecast={forecast('uncertain', 'not_applicable', {
      source: 'no_positive_receipt', lane: 'subscription',
      readiness: 'not_ready', readiness_cause: 'no_positive_receipt',
    })} />
    {/* a backend clock fault: grey, because no opinion can be formed */}
    <CacheForecastMark forecast={forecast('uncertain', 'not_applicable', {
      source: 'clock_skew', lane: 'subscription',
      readiness: 'diagnostic', readiness_cause: 'clock_anomaly',
      readiness_detail: 'Receipt stamped ahead of the backend clock by 5.0s.',
    })} />
  </>, (el) => el)
  try {
    const marks = [...view.el.querySelectorAll<HTMLElement>('.cache-forecast')]
    assert.deepEqual(marks.map((m) => [...m.classList][1]),
      ['uncertain', 'cold', 'cold', 'uncertain'])
    assert.deepEqual(marks.map((m) => m.textContent?.trim()),
      ['cache ?', 'cache ×', 'cache ×', 'cache ?'])
    // ⚠ NO BARE "unknown" ANYWHERE. Every grey names its cause, and the two
    // reds say they are unestablished rather than claiming a proven miss.
    for (const mark of marks) {
      const title = mark.getAttribute('aria-label') ?? ''
      assert.doesNotMatch(title, /compatibility: unknown/)
      assert.match(title, /readiness: (diagnostic|not_ready) \([a-z_]+\)/)
    }
    assert.match(marks[0]?.getAttribute('aria-label') ?? '',
      /unsupported_capability/)
    assert.match(marks[3]?.getAttribute('aria-label') ?? '', /clock_anomaly/)
  } finally { await view.unmount() }
})

test('D-226: the badge FAILS CLOSED — no readiness is grey, never green', async () => {
  // ⚠ THE MOST EXPENSIVE LIE THIS COMPONENT COULD TELL is a green badge on a
  // payload it did not understand: the user would withhold a compaction, or
  // send a large turn, on a promise nothing made. A row from an older backend,
  // a hand-built object, or a field that arrives misspelled must all land on
  // the named internal_error diagnostic — grey, explained, and greppable.
  const noReadiness = { ...forecast('compatible_observed') }
  delete (noReadiness as Partial<CacheForecast>).readiness
  delete (noReadiness as Partial<CacheForecast>).readiness_cause
  const cases: Array<[string, CacheForecast]> = [
    ['readiness absent entirely', noReadiness as CacheForecast],
    ['readiness is an unrecognised value', {
      ...forecast('compatible_observed'),
      readiness: 'probably-fine' as unknown as CacheForecast['readiness'],
    }],
    // Even a state that WOULD have been green is overruled by its readiness:
    // readiness is the single authority for what the badge renders.
    ['state says green but readiness says otherwise', {
      ...forecast('compatible_observed'),
      readiness: 'not_ready', readiness_cause: 'prefix_changed',
    }],
  ]
  for (const [label, row] of cases) {
    const view = await mountView(<CacheForecastMark forecast={row} />, (el) => el)
    try {
      const mark = view.el.querySelector<HTMLElement>('.cache-forecast')
      assert.notEqual([...(mark?.classList ?? [])][1], 'compatible', label)
      // and never a live countdown, which would imply a trusted expiry
      assert.doesNotMatch(mark?.textContent ?? '', /\d+:\d\d/, label)
    } finally { await view.unmount() }
  }
})

test('only known-cold states warn at send time with policy-owned colour', async () => {
  const view = await mountView(<>
    <CacheForecastWarning forecast={forecast('compatible_observed')} />
    <CacheForecastWarning forecast={forecast('expired_known_entry')} />
    <CacheForecastWarning forecast={forecast('uncertain')} />
    <CacheForecastWarning forecast={forecast('known_incompatible', 'miss_expected')} />
    <CacheForecastWarning forecast={forecast('expired_known_entry', 'miss_expected')} />
    <CacheForecastWarning forecast={forecast('known_incompatible', 'will_compact')} />
    <CacheForecastWarning forecast={forecast('expired_known_entry', 'will_compact')} />
  </>, (el) => el)
  try {
    const warnings = [...view.el.querySelectorAll<HTMLElement>('.cache-send-warning')]
    assert.equal(warnings.length, 4)
    assert.equal(warnings[0]?.classList.contains('miss'), true)
    assert.match(warnings[0]?.textContent ?? '', /Cache miss expected/)
    assert.equal(warnings[1]?.classList.contains('miss'), true)
    assert.match(warnings[1]?.textContent ?? '', /Cache miss expected/)
    assert.equal(warnings[2]?.classList.contains('compact'), true)
    assert.match(warnings[2]?.textContent ?? '', /will cheap-compact/)
    assert.equal(warnings[3]?.classList.contains('compact'), true)
    assert.match(warnings[3]?.textContent ?? '', /will cheap-compact/)
  } finally { await view.unmount() }
})

test('manual compaction warning uses forecast evidence, never generic idle age', () => {
  const source = readFileSync(path.join(__SRC_DIR__, 'canvas', 'desk.tsx'), 'utf8')
  const start = source.indexOf('{askCompact && (() => {')
  const end = source.indexOf('{/* last_error moved', start)
  assert.ok(start >= 0 && end > start, 'manual compact modal source seam moved')
  const modal = source.slice(start, end)
  assert.match(modal, /node\.cache_forecast/)
  assert.match(modal, /expired_known_entry/)
  assert.match(modal, /known_incompatible/)
  assert.doesNotMatch(modal, /Date\.parse|60\s*\*\s*60e3|lastAt/)
})
