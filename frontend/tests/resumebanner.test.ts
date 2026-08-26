// resumebanner.test.ts — the ▶ resume banner counts what ▶ will actually do.
//
// user report 2026-08-26: the banner read "resume 2 — usage limit hit · 2
// agents frozen" in an org whose two frozen agents had since been RETIRED.
// Retiring does not clear the freeze record (a retired agent keeps its context
// and can be rehired), so the old test — `n.frozen != null` and nothing else —
// counted nodes the backend has never been willing to resume.
//
// The backend was already correct: `supervisor._resumable` returns None when
// `state != "live"` or the node is `limit_locked`, so pressing ▶ on that
// banner resumed nobody. `resumableFrozen` is the frontend mirror of that
// test, and this suite is what keeps the two in step.
//
// ⚠ WHY EVERY TEST HERE HAS TWO LEGS. "The banner shows 0" passes just as
// happily if the fix hides EVERY frozen agent, or if the count is broken
// outright and renders nothing — both are the bug in the other direction, and
// both would look like a fix. So every check that something is excluded is
// paired with a live, genuinely frozen agent that must still be counted. A
// one-legged version of this suite would have signed off on `() => []`.
//
// Run:  cd frontend && node tests/run.mjs resumebanner

import './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { resumableFrozen } from '../src/App'
import type { TreePayload } from '../src/types'

/** injected by tests/run.mjs — the bundle does not sit next to the sources */
declare const __SRC_DIR__: string

// ---------------------------------------------------------------- fixtures
/** shaped like the payload, not type-checked into it — the fixture idiom of
 *  audpile/deskinit, trimmed to what resumableFrozen dereferences */
const asTree = (v: unknown) => v as TreePayload

interface FixNode {
  id: string
  state?: 'live' | 'archived' | 'unrecoverable'
  frozen?: Record<string, unknown> | null
  limit_locked?: boolean
  children?: FixNode[]
}

/** a plain usage-limit freeze — the shape the banner is about */
const LIMIT = { at: '2026-08-26T09:00:00Z', until: 'in 3 hours',
                until_ts: 1900000000, error: null, limit: true }

function mk(n: FixNode): unknown {
  return {
    id: n.id, title: n.id, tier: 'opus', model_id: 'opus',
    state: n.state ?? 'live',
    seat: 1, grant: 0, free: 0, ui_order: 0, cost_usd: 0, occupancy: null,
    context_window: null, charter: null, mail_pending: 0,
    limit_locked: n.limit_locked ?? false,
    last_status: null, prev_status: null, inflight_at: null, last_denials: [],
    turns: [], frozen: n.frozen ?? null, audiences_held: [],
    bearer_state: null, generation: 0,
    children: (n.children ?? []).map(mk), lineage: [],
    scope: { permission_mode: 'default', add_dirs: [], tools: {}, org_visibility: 'team' },
  }
}

function tree(roots: FixNode[]): TreePayload {
  return asTree({
    slug: 'mine', name: 'mine', workspace: null, dirs: [], max_top_grant: 1000,
    default_top_grant: 50, compact_at: 0, default_tools: null,
    default_visibility: 'team', default_effort: '', credit_requests: [],
    tiers: { haiku: 1, sonnet: 3, opus: 5, fable: 10 }, audiences: [],
    roots: roots.map(mk), cost_usd_total: 0,
    audit: { live_nodes: roots.length, top_level_holds: 0, no_overdraft: true, problems: [] },
    user_inbox_count: 0, user_inbox_newest: null, fable_lock: null,
    spend_frozen: false, storage_blocked: false, auto_resume: false,
    fable_limit_policy: 'freeze', fable_filter_policy: 'halt',
    cascade_hire: false, cascade_alloc: true, sandboxed: false,
    audience_requests: [], org_inbox: null, net: null,
  })
}

const ids = (t: TreePayload) => resumableFrozen(t).map((n) => n.id).sort()

// ------------------------------------------------------------------- tests

test('§1 the reported bug: retired frozen agents drop out, live ones remain', () => {
  // exactly the user's org — two frozen agents, since retired — plus one
  // live frozen agent, which is the leg that stops "return nothing" passing
  const t = tree([
    { id: 'gone-1', state: 'archived', frozen: LIMIT },
    { id: 'gone-2', state: 'archived', frozen: LIMIT },
    { id: 'still-here', frozen: LIMIT },
  ])
  assert.deepEqual(ids(t), ['still-here'])
  assert.equal(resumableFrozen(t).length, 1,
    'the banner would have said "resume 3"; ▶ would have resumed 1')
})

test('§2 the user\'s exact screenshot: 2 retired and NOTHING live → no banner', () => {
  // the banner renders only when the list is non-empty, so this is the case
  // where it must disappear entirely rather than read "resume 0"
  const t = tree([
    { id: 'gone-1', state: 'archived', frozen: LIMIT },
    { id: 'gone-2', state: 'archived', frozen: LIMIT },
  ])
  assert.deepEqual(resumableFrozen(t), [])
})

test('§3 a live frozen agent is ALWAYS counted — the leg that must not break', () => {
  // no retired nodes anywhere: nothing here is excludable, so if this ever
  // returns empty the fix has started eating the case it exists to serve
  const t = tree([
    { id: 'a', frozen: LIMIT },
    { id: 'b', frozen: LIMIT, children: [{ id: 'c', frozen: LIMIT }] },
  ])
  assert.deepEqual(ids(t), ['a', 'b', 'c'])
})

test('§4 every non-live state is excluded, and they are distinct states', () => {
  // `archived` is what the UI calls RETIRED; `unrecoverable` is the other
  // non-live state. Both must go, and the live one must stay — keyed on
  // `state === 'live'` rather than on `!== 'archived'`, so a third state
  // added later fails closed instead of silently counting.
  const t = tree([
    { id: 'retired', state: 'archived', frozen: LIMIT },
    { id: 'unrecoverable', state: 'unrecoverable', frozen: LIMIT },
    { id: 'live', state: 'live', frozen: LIMIT },
  ])
  assert.deepEqual(ids(t), ['live'])
})

test('§5 rehiring counts again — a live property, not a scrubbed record', () => {
  // the SAME node and the SAME freeze record, differing only in state. This
  // is the reason the fix keys on state instead of clearing `frozen` at
  // retire time: a rehired agent's freeze is still real and still waiting.
  const retired = tree([{ id: 'n', state: 'archived', frozen: LIMIT }])
  const rehired = tree([{ id: 'n', state: 'live', frozen: LIMIT }])
  assert.deepEqual(resumableFrozen(retired), [])
  assert.deepEqual(ids(rehired), ['n'])
})

test('§6 limit_locked is excluded too — ▶ will not touch it either', () => {
  // `_resumable` refuses a limit_locked node (only clear_fable_lock releases
  // it), so counting it is the same lie as counting a retired one
  const t = tree([
    { id: 'locked', frozen: LIMIT, limit_locked: true },
    { id: 'open', frozen: LIMIT, limit_locked: false },
  ])
  assert.deepEqual(ids(t), ['open'])
})

test('§7 an unfrozen agent never counts, live or not', () => {
  const t = tree([
    { id: 'idle', frozen: null },
    { id: 'retired-clean', state: 'archived', frozen: null },
    { id: 'frozen', frozen: LIMIT },
  ])
  assert.deepEqual(ids(t), ['frozen'])
})

// ---------------------------------------------------------------------------
// DRIFT GUARD. `resumableFrozen` is a hand-written mirror of a Python function
// in another language that no compiler checks against it. If `_resumable`
// grows a condition, this mirror silently stops matching and the banner starts
// over-counting again — the exact bug, returning by a route the tests above
// cannot see, because they only ever ask this file's own opinion.
//
// So read the backend and assert its shape. This cannot prove the mirror is
// correct, but it fails loudly when the thing being mirrored moves, which is
// the failure that actually happened here.
test('§8 DRIFT — supervisor._resumable still tests exactly what we mirror', () => {
  // __SRC_DIR__ is frontend/src; the backend sits two levels up
  const sup = path.join(__SRC_DIR__, '..', '..', 'backend', 'orgtree', 'supervisor.py')
  const src = readFileSync(sup, 'utf8')
  const at = src.indexOf('def _resumable(')
  assert.ok(at > 0, 'could not find _resumable — it moved or was renamed')
  const body = src.slice(at, src.indexOf('\ndef ', at + 10))

  assert.match(body, /n\["state"\]\s*!=\s*"live"/,
    '_resumable no longer keys on state != "live" — resumableFrozen mirrors it')
  assert.match(body, /n\.get\("limit_locked"\)/,
    '_resumable no longer tests limit_locked — resumableFrozen mirrors it')
  // the kinds exempted from its other-kind test. If this list changes, the
  // "not mirrored" note on resumableFrozen needs re-reading.
  for (const kind of ['limit', 'connection', 'on_fallback', 'untrusted']) {
    assert.ok(body.includes(`"${kind}"`),
      `_resumable's exempt-kind list no longer names ${kind}`)
  }
})
