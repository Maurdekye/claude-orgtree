// progress.test.tsx — FR-2, the desk's task-progress panel (canvas/progress.tsx).
//
// THE ONE PROPERTY THIS SUITE EXISTS TO HOLD: the panel never renders EMPTY.
// When it has no data it says WHICH kind of nothing — "no todo list in the
// loaded transcript window", "Antigravity agents have no todo or plan source
// orgtree knows of" — because an empty checklist teaches the operator that
// the agent is doing nothing, when the truth is that we do not know.
//
// FR-17 (2026-09-05): Codex's native `turn/plan/updated` checklist now runs
// through this SAME panel, as its OWN labelled source (`source: 'codex-plan'`)
// — never folded into or mistaken for Claude's TodoWrite path. Antigravity is
// the only lane still `lane-cannot`.
//
// §1 is the lane × state truth table over `deriveProgress`, asserting the
//    VERDICT and NON-EMPTY COPY in every cell. A cell that merely rendered
//    "nothing" would pass a snapshot test while the feature is broken; the
//    Codex cells additionally prove Claude-shaped fixtures (TodoWrite rows/
//    chips) are NEVER read as a Codex plan (anti-fabrication) and Antigravity
//    still asserts `lane-cannot` by name.
// §1e is Codex's OWN dedicated matrix, on its OWN wire shape (a 'plan' live
//    row / a `codexPlan` transcript record): live, durable, all three
//    statuses, the live-no-body degrade, explicit clearing vs never-observed,
//    successive snapshots (newest wins), and prior-turn labelling — real
//    turnId identity preferred, timestamp fallback only when identity is
//    unavailable, an equal timestamp never mislabelled, and idle/restarted
//    state never labelled "previous" for want of anything to be after.
// §2 pins the `☑ ◐ ☐` glyph parser to the three glyphs supervisor._todo_glyphs
//    emits — change one there and THIS is what goes red.
// §3 mounts ProgressView and reads the DOM: the note is present in every
//    state, the checklist only when there are items, the "previous list"
//    marker whenever what is shown is not current, and a Codex section's own
//    header names Codex on screen.
// §4 mounts the real DeskChat against the fake server: the fifth tab exists,
//    the header chip is there, clicking either shows the panel, and the panel
//    is fed by the SAME conversation store as the chat (a live TodoWrite row
//    pushed on the server reaches the panel after one poll). §4b repeats the
//    live-wire proof on a Codex node with a live 'plan' row, through the
//    identical wiring.
//
// ANTI-VACUITY, per behavioural claim (verified red while writing — see the
// commit message for the mutations run):
//   · §1 non-empty copy: `note: ''` in any deriveTodo branch → red
//   · §1 anti-fabrication: making the openai branch fall through to the
//     Claude TodoWrite path → red (a Claude-shaped fixture would then show
//     items/supply='live'|'durable')
//   · §1e successive snapshots: reading the FIRST matching message instead
//     of walking backward from the newest → red
//   · §1e identity-preferred labelling: dropping the `current` check and
//     falling straight to the timestamp rule → red (the "identity says
//     CURRENT despite an earlier timestamp" case flips)
//   · §2 glyphs: a glyph swap in parseTodoResult → red
//   · §4 wiring: removing the `view === 'progress'` branch → red
//   · §4 live feed: dropping `todos` from the live row → the panel's supply
//     becomes 'updating' and the §4 items assertion goes red
//   · §4b Codex live feed: dropping `plan` from a 'plan' live row, or
//     routing 'plan' rows through Claude's TodoWrite matcher → red
//
// Run:  cd frontend && node tests/run.mjs progress

import './harness'
import { advance, FakeServer, flush, installFetch, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { DeskChat } from '../src/canvas/desk'
import { deriveProgress, parseTodoResult, ProgressView, WORKING_STALE_MS } from '../src/canvas/progress'
import type { ProgressModel } from '../src/canvas/progress'
import type { CanvasNode, LiveRow } from '../src/canvas/shared'
import type { Convo } from '../src/convo'
import { resetConvos } from '../src/convo'
import type { ChatMessage, ChatPayload, OpResult } from '../src/types'

const noop = () => {}
const op = () => Promise.resolve({} as OpResult)

// ---------------------------------------------------------------- fixtures

function node(over: Partial<CanvasNode> = {}): CanvasNode {
  return {
    id: 'agent', state: 'live', tier: 'sonnet', model_id: 'sonnet', children: [],
    seat: 1, grant: 0, free: 0, scope: { tools: {}, add_dirs: [] },
    busy: false, proc_live: false, tasks: 0, bg_tasks: 0, last_status: null,
    inflight_at: null, activity: { phase: 'thinking' },
    ...over,
  }
}

function convo(over: Partial<Convo> = {}, messages: ChatMessage[] = [],
               codexTurnId?: string | null): Convo {
  const chat: ChatPayload = {
    busy: false, queued: 0, responding: false, last_error: null, occupancy: null,
    messages, live: [], mail_pending: 0, pending_mail: [],
    ...(codexTurnId !== undefined ? { codex_turn_id: codexTurnId } : {}),
  } as unknown as ChatPayload
  return {
    chat, live: [], pending: [], draft: '', thinking: '', thinkSecs: null,
    win: 120, loadingOlder: false, loaded: true, ...over,
  }
}

const GLYPHS = '☑ read the plan\n◐ write the panel\n☐ land it'

function todoMsg(result = GLYPHS, ts = '2026-09-04T20:00:00.000Z'): ChatMessage {
  return { role: 'assistant', text: '', ts, seq: 1,
    tools: [{ name: 'TodoWrite', arg: '', id: 'toolu_1', result, result_lines: 3 }] }
}

const liveTodoRow = (todos?: LiveRow['todos']): LiveRow =>
  ({ kind: 'tool', text: 'TodoWrite', id: 'toolu_live', at: '2026-09-04T20:30:00.000Z',
    ...(todos ? { todos } : {}) })

// FR-17: Codex's native fixtures, structurally parallel to the two above but
// on their OWN wire shape (a 'plan' live row / a `codexPlan` transcript
// record) — never the Claude shape, so a test built on these cannot pass by
// accident through code that actually reads TodoWrite.
// already in the NORMALIZED vocabulary — supervisor._codex_plan_steps maps
// codex's own camelCase 'inProgress' to 'in_progress' on the backend, once,
// before either the journal or the live wire ever sees it (Astra's guard:
// no redundant normalization / no second, divergent mapping on this side).
const PLAN_STEPS = [
  { step: 'read the plan', status: 'completed' },
  { step: 'write the panel', status: 'in_progress' },
  { step: 'land it', status: 'pending' },
]

function planMsg(steps = PLAN_STEPS, ts = '2026-09-04T20:00:00.000Z',
                 explanation: string | null = null, turnId = 'turn-1'): ChatMessage {
  return { role: 'assistant', text: '', ts, seq: 1,
    codexPlan: { steps, explanation, threadId: 'thread-1', turnId } }
}

const livePlanRow = (steps?: LiveRow['plan'], explanation: string | null = null,
                     turnId = 'turn-1'): LiveRow =>
  ({ kind: 'plan', text: 'checklist updated', at: '2026-09-04T20:30:00.000Z',
    threadId: 'thread-1', turnId, explanation, ...(steps ? { plan: steps } : {}) })

const items = (m: ProgressModel) => (m.todo.items ?? []).map((t) => [t.status, t.content])

// ------------------------------------------------ §1 the lane × state table

test('§1 deriveProgress: every cell of the lane × state table carries a verdict AND a sentence', () => {
  const lanes = { claude: 'sonnet', openrouter: 'or-anthropic-claude-sonnet-4', codex: 'luna', antigravity: 'flash' }
  const states: Record<string, { n: Partial<CanvasNode>; c: Partial<Convo>; msgs?: ChatMessage[] }> = {
    'live todo':      { n: { busy: true, inflight_at: '2026-09-04T20:29:00Z' },
                        c: { live: [liveTodoRow([{ content: 'a', status: 'in_progress' }])] } },
    'live, no body':  { n: { busy: true }, c: { live: [liveTodoRow()] }, msgs: [todoMsg()] },
    'durable todo':   { n: {}, c: {}, msgs: [todoMsg()] },
    'none':           { n: {}, c: {}, msgs: [{ role: 'user', text: 'hi', seq: 0 }] },
    'no transcript':  { n: {}, c: {}, msgs: [] },
    'loading':        { n: {}, c: { loaded: false, chat: null } },
    'archived':       { n: { state: 'archived' }, c: {}, msgs: [todoMsg()] },
    'restarted':      { n: { busy: false, proc_live: false }, c: {}, msgs: [todoMsg()] },
  }
  for (const [lane, tier] of Object.entries(lanes)) {
    for (const [state, s] of Object.entries(states)) {
      const m = deriveProgress(node({ tier, ...s.n }), convo(s.c, s.msgs ?? []))
      const cell = `${lane} × ${state}`
      // THE PROPERTY: never silence. Every section, every cell.
      assert.ok(m.todo.note.trim().length > 10, `${cell}: todo note is empty`)
      assert.ok(m.subagents.note.trim(), `${cell}: subagent note is empty`)
      assert.ok(m.reported.note.trim(), `${cell}: reported note is empty`)
      assert.ok(m.activity.note.trim(), `${cell}: activity note is empty`)
      assert.ok(m.processNote.trim(), `${cell}: process note is empty`)
      assert.ok(m.chip.trim(), `${cell}: chip text is empty`)
      assert.equal(m.chipTitle, m.todo.note, `${cell}: chip tooltip is not the todo note`)
      if (lane === 'antigravity') {
        // Antigravity still has no source orgtree knows of at all — unchanged.
        assert.equal(m.todo.supply, 'lane-cannot', `${cell}: supply`)
        assert.equal(m.todo.items, null, `${cell}: a lane that cannot supply must show no list`)
        assert.match(m.todo.note, /Antigravity/, `${cell}: note names the lane`)
        assert.match(m.todo.note, /cannot see/, `${cell}: note says orgtree cannot see, not that there is none`)
        assert.equal(m.chip, 'todo n/a', `${cell}: chip`)
        continue
      }
      if (lane === 'codex') {
        // FR-17 supersedes the old "Codex cannot" pin: it is never
        // 'lane-cannot' again, and it is ALWAYS its own labelled source.
        assert.notEqual(m.todo.supply, 'lane-cannot', `${cell}: supply must not be lane-cannot`)
        assert.equal(m.todo.source, 'codex-plan', `${cell}: source is always labelled`)
        // ⚠ ANTI-FABRICATION: every fixture in `states` is Claude-shaped (a
        // TodoWrite tool row / tools chip) — none of it is a Codex 'plan' row
        // or a `codexPlan` transcript record. If Codex ever picked any of it
        // up as its own checklist, this is exactly where it would show: an
        // `items` list or a 'live'/'durable'/'updating' supply born from data
        // that was never turn/plan/updated. It must not.
        assert.ok(['none-in-window', 'loading'].includes(m.todo.supply),
          `${cell}: Claude-shaped fixtures must never read as a Codex plan (got ${m.todo.supply})`)
        assert.equal(m.todo.items, null, `${cell}: no items from data that was never turn/plan/updated`)
        assert.doesNotMatch(m.todo.note, /TodoWrite/, `${cell}: never speaks in Claude's tool name`)
        continue
      }
      // Claude and OpenRouter run the same CLI: identical verdicts
      switch (state) {
        case 'live todo':
          assert.equal(m.todo.supply, 'live', cell)
          assert.deepEqual(items(m), [['in_progress', 'a']], cell)
          assert.match(m.todo.note, /^live, this turn: 0\/1 done · now: a/, cell)
          assert.equal(m.chip, '◐ 0/1', cell)
          break
        case 'live, no body':
          assert.equal(m.todo.supply, 'updating', cell)
          assert.match(m.todo.note, /updating its todo list now/, cell)
          assert.match(m.todo.note, /PREVIOUS list/, `${cell}: the shown list must be called previous`)
          assert.equal(items(m).length, 3, `${cell}: the previous list is shown, dimmed`)
          assert.match(m.chip, /↻/, cell)
          break
        case 'durable todo':
          assert.equal(m.todo.supply, 'durable', cell)
          assert.deepEqual(items(m), [['completed', 'read the plan'], ['in_progress', 'write the panel'], ['pending', 'land it']], cell)
          assert.equal(m.todo.earlierTurn, false, cell)
          assert.match(m.todo.note, /^from the transcript: 1\/3 done · now: write the panel/, cell)
          assert.equal(m.chip, '◐ 1/3', cell)
          break
        case 'none':
          assert.equal(m.todo.supply, 'none-in-window', cell)
          assert.match(m.todo.note, /no todo list in the loaded transcript window \(last 1 message\)/, cell)
          assert.match(m.todo.note, /older one may exist further back/, `${cell}: the window is named as a window`)
          assert.equal(m.chip, 'no todos', cell)
          break
        case 'no transcript':
          assert.equal(m.todo.supply, 'none-in-window', cell)
          assert.match(m.todo.note, /no transcript yet/, cell)
          break
        case 'loading':
          assert.equal(m.todo.supply, 'loading', cell)
          assert.match(m.todo.note, /cannot say yet/, `${cell}: loading must not claim there is none`)
          assert.equal(m.chip, 'todo …', cell)
          break
        case 'archived':
          assert.equal(m.historical, true, cell)
          assert.match(m.processNote, /retired/, cell)
          assert.match(m.processNote, /not live progress/, cell)
          assert.equal(m.todo.supply, 'durable', `${cell}: the last list is still shown, as a record`)
          break
        case 'restarted':
          // after a backend restart the live tail is gone and only the
          // transcript remains: the panel must say nothing is live rather
          // than present the durable list as current
          assert.equal(m.todo.supply, 'durable', cell)
          assert.match(m.processNote, /not in a turn/, cell)
          assert.match(m.processNote, /nothing below is live/, cell)
          break
      }
    }
  }
})

test('§1b deriveProgress: a durable list from BEFORE the running turn is labelled as earlier', () => {
  const m = deriveProgress(
    node({ busy: true, inflight_at: '2026-09-04T20:10:00.000Z' }),
    convo({}, [todoMsg(GLYPHS, '2026-09-04T20:00:00.000Z')]))
  assert.equal(m.todo.supply, 'durable')
  assert.equal(m.todo.earlierTurn, true)
  assert.match(m.todo.note, /EARLIER turn/)
  assert.match(m.todo.note, /running turn has not written a todo list yet/)
  assert.match(m.chip, /\(prev\)/)
  // and the same list written AFTER the turn began is current
  const cur = deriveProgress(
    node({ busy: true, inflight_at: '2026-09-04T20:10:00.000Z' }),
    convo({}, [todoMsg(GLYPHS, '2026-09-04T20:11:00.000Z')]))
  assert.equal(cur.todo.earlierTurn, false)
  assert.doesNotMatch(cur.chip, /prev/)
})

test('§1c deriveProgress: a CLEARED list is a list, not an absence', () => {
  const live = deriveProgress(node({ busy: true }), convo({ live: [liveTodoRow([])] }))
  assert.equal(live.todo.supply, 'live')
  assert.deepEqual(live.todo.items, [])
  assert.match(live.todo.note, /cleared its todo list/)
  assert.equal(live.chip, 'todo cleared')
  const durable = deriveProgress(node(), convo({}, [todoMsg('')]))
  assert.equal(durable.todo.supply, 'durable')
  assert.deepEqual(durable.todo.items, [])
  assert.match(durable.todo.note, /cleared/)
})

// -------------------------------------------- §1e FR-17: Codex's own source

test('§1e deriveProgress (Codex): live, durable, all three statuses, explanation preserved', () => {
  const live = deriveProgress(node({ tier: 'luna', busy: true }),
    convo({ live: [livePlanRow(PLAN_STEPS, 'because the plan changed')] }))
  assert.equal(live.todo.supply, 'live')
  assert.equal(live.todo.source, 'codex-plan')
  assert.deepEqual(items(live), [['completed', 'read the plan'], ['in_progress', 'write the panel'], ['pending', 'land it']])
  assert.equal(live.todo.explanation, 'because the plan changed')
  assert.match(live.todo.note, /^live, this turn: 1\/3 done · now: write the panel/)
  assert.match(live.todo.note, /because the plan changed/)
  assert.equal(live.chip, '◐ 1/3')

  const durable = deriveProgress(node({ tier: 'luna' }), convo({}, [planMsg()]))
  assert.equal(durable.todo.supply, 'durable')
  assert.equal(durable.todo.earlierTurn, false)
  assert.deepEqual(items(durable), [['completed', 'read the plan'], ['in_progress', 'write the panel'], ['pending', 'land it']])
  assert.match(durable.todo.note, /Codex's own turn checklist/)
})

test('§1e deriveProgress (Codex): a live plan row with no contents degrades like a live TodoWrite row does', () => {
  const m = deriveProgress(node({ tier: 'luna', busy: true }),
    convo({ live: [livePlanRow(undefined)] }, [planMsg()]))
  assert.equal(m.todo.supply, 'updating')
  assert.match(m.todo.note, /updating its checklist now/)
  assert.match(m.todo.note, /PREVIOUS checklist/)
  assert.equal(items(m).length, 3)
})

test('§1e deriveProgress (Codex): an explicit empty plan is CLEARED, not absent — distinct from never having observed one', () => {
  const cleared = deriveProgress(node({ tier: 'luna', busy: true }), convo({ live: [livePlanRow([])] }))
  assert.equal(cleared.todo.supply, 'live')
  assert.deepEqual(cleared.todo.items, [])
  assert.match(cleared.todo.note, /cleared its todo list/)
  assert.equal(cleared.chip, 'todo cleared')
  const neverObserved = deriveProgress(node({ tier: 'luna' }), convo({}, []))
  assert.equal(neverObserved.todo.items, null, 'no event at all must never render as an empty (cleared) list')
  assert.match(neverObserved.todo.note, /no transcript yet/)
})

test('§1e deriveProgress (Codex): successive snapshots — the newest durable one wins, not the first', () => {
  const m = deriveProgress(node({ tier: 'luna' }), convo({}, [
    planMsg([{ step: 'a', status: 'pending' }], '2026-09-04T20:00:00.000Z'),
    planMsg([{ step: 'a', status: 'completed' }], '2026-09-04T20:05:00.000Z'),
  ]))
  assert.deepEqual(items(m), [['completed', 'a']])
})

test('§1e deriveProgress (Codex): prior-turn labelling prefers REAL turn identity over a timestamp guess', () => {
  // identity says CURRENT even though the timestamp alone would look earlier
  // than inflight_at — identity wins, so it must NOT be marked previous
  const currentByIdentity = deriveProgress(
    node({ tier: 'luna', busy: true, inflight_at: '2026-09-04T20:10:00.000Z' }),
    convo({}, [planMsg(PLAN_STEPS, '2026-09-04T20:00:00.000Z', null, 'turn-9')], 'turn-9'))
  assert.equal(currentByIdentity.todo.earlierTurn, false)
  assert.doesNotMatch(currentByIdentity.chip, /prev/)
  // identity says EARLIER (a different, known turnId) even though the
  // timestamp alone would look current
  const earlierByIdentity = deriveProgress(
    node({ tier: 'luna', busy: true, inflight_at: '2026-09-04T19:00:00.000Z' }),
    convo({}, [planMsg(PLAN_STEPS, '2026-09-04T20:00:00.000Z', null, 'turn-8')], 'turn-9'))
  assert.equal(earlierByIdentity.todo.earlierTurn, true)
  assert.match(earlierByIdentity.todo.note, /EARLIER turn/)
  assert.match(earlierByIdentity.chip, /\(prev\)/)
  // no identity available anywhere (older-backend record, codex_turn_id
  // absent) — falls back to Claude's own strict-less-than timestamp rule,
  // and an EQUAL timestamp must not be mislabelled as earlier
  const equalTs = deriveProgress(
    node({ tier: 'luna', busy: true, inflight_at: '2026-09-04T20:10:00.000Z' }),
    convo({}, [planMsg(PLAN_STEPS, '2026-09-04T20:10:00.000Z', null, '')]))
  assert.equal(equalTs.todo.earlierTurn, false, 'an equal timestamp is not earlier')
  const strictlyEarlier = deriveProgress(
    node({ tier: 'luna', busy: true, inflight_at: '2026-09-04T20:10:00.000Z' }),
    convo({}, [planMsg(PLAN_STEPS, '2026-09-04T20:00:00.000Z', null, '')]))
  assert.equal(strictlyEarlier.todo.earlierTurn, true)
  // idle (not busy) — nothing is "the running turn", so the last known
  // checklist is just the last known checklist, never labelled previous,
  // even with an identity mismatch or a restart that wiped codex_turn_id
  const idle = deriveProgress(node({ tier: 'luna', busy: false }),
    convo({}, [planMsg(PLAN_STEPS, '2026-09-04T20:00:00.000Z', null, 'turn-8')], null))
  assert.equal(idle.todo.earlierTurn, false)
})

test('§1d deriveProgress: subagents, reported status and activity', () => {
  // bg_tasks had NO frontend type before FR-2 — a fixture with only `tasks`
  // would leave it undefined and render 0 forever (the plan's named trap)
  const bg = deriveProgress(node({ busy: true, tasks: 1, bg_tasks: 2 }), convo())
  assert.equal(bg.subagents.fg, 1)
  assert.equal(bg.subagents.bg, 2)
  assert.match(bg.subagents.note, /1 foreground · 2 background subagents running/)
  assert.match(bg.subagents.note, /outlive the reply/)
  assert.match(bg.chip, /· 3 sub$/)
  const noneBusy = deriveProgress(node({ busy: true }), convo())
  assert.equal(noneBusy.subagents.note, 'no subagents running')
  const synthetic = deriveProgress(node({ tasks: undefined, bg_tasks: undefined }), convo())
  assert.equal(synthetic.subagents.known, false)
  assert.match(synthetic.subagents.note, /carries no subagent counts/)

  // reported status: the summary is TEXT here (it was a tooltip on the desk)
  const never = deriveProgress(node(), convo())
  assert.equal(never.reported.status, null)
  assert.match(never.reported.note, /never reported a status/)
  const fresh = deriveProgress(node({ last_status: { status: 'working', summary: 'wiring the tab', at: new Date(Date.now() - 120_000).toISOString() } }), convo())
  assert.equal(fresh.reported.stale, false)
  assert.equal(fresh.reported.summary, 'wiring the tab')
  assert.match(fresh.reported.note, /^reported 2m ago$/)
  // staleness mirrors the engine's own 30-minute check-up rule
  const stale = deriveProgress(node({ last_status: { status: 'working', summary: 's', at: new Date(Date.now() - WORKING_STALE_MS - 60_000).toISOString() } }), convo())
  assert.equal(stale.reported.stale, true)
  assert.match(stale.reported.note, /30-minute check-up threshold/)
  const oldDone = deriveProgress(node({ last_status: { status: 'done', summary: 's', at: new Date(Date.now() - WORKING_STALE_MS * 4).toISOString() } }), convo())
  assert.equal(oldDone.reported.stale, false, 'only "working" goes stale — an old "done" is simply old')

  // activity: phase from the node, tail from the live wire, and a sentence
  // when the wire is empty
  const idle = deriveProgress(node(), convo())
  assert.equal(idle.activity.note, 'not in a turn — no live activity')
  const quiet = deriveProgress(node({ busy: true, activity: { phase: 'thinking' } }), convo())
  assert.match(quiet.activity.note, /thinking/)
  assert.match(quiet.activity.note, /no tool calls on the live wire yet/)
  const tool = deriveProgress(node({ busy: true, activity: { phase: 'tool', tool: 'Bash · ls' } }),
    convo({ live: [{ kind: 'tool', text: 'Read · a.ts' }, { kind: 'text', text: 'x' }, { kind: 'tool', text: 'Bash · ls' }] }))
  assert.equal(tool.activity.note, 'running a tool: Bash · ls')
  assert.deepEqual(tool.activity.tail.map((r) => r.text), ['Read · a.ts', 'Bash · ls'])
})

// ------------------------------------------------------- §2 the glyph parser

test('§2 parseTodoResult pins the three glyphs supervisor._todo_glyphs emits', () => {
  assert.deepEqual(parseTodoResult(GLYPHS), [
    { content: 'read the plan', status: 'completed' },
    { content: 'write the panel', status: 'in_progress' },
    { content: 'land it', status: 'pending' },
  ])
  // CRLF-tolerant, blank-line-tolerant, and an unknown prefix DEGRADES TO A
  // VISIBLE pending item rather than a dropped one
  assert.deepEqual(parseTodoResult('☑ a\r\n\r\n?? odd\n'), [
    { content: 'a', status: 'completed' },
    { content: '?? odd', status: 'pending' },
  ])
  assert.deepEqual(parseTodoResult(''), [])
  assert.deepEqual(parseTodoResult(undefined), [])
})

// --------------------------------------------------------- §3 the view's DOM

test('§3 ProgressView: a sentence in every state; a checklist only with items; "previous" when not current', async () => {
  const cases: [string, ProgressModel, { items: number; previous: boolean; noteRe: RegExp }][] = [
    ['codex none', deriveProgress(node({ tier: 'luna' }), convo()), { items: 0, previous: false, noteRe: /no transcript yet/ }],
    ['codex live', deriveProgress(node({ tier: 'luna', busy: true }), convo({ live: [livePlanRow(PLAN_STEPS)] })), { items: 3, previous: false, noteRe: /live, this turn/ }],
    ['antigravity', deriveProgress(node({ tier: 'pro' }), convo()), { items: 0, previous: false, noteRe: /Antigravity/ }],
    ['none', deriveProgress(node(), convo({}, [{ role: 'user', text: 'x', seq: 0 }])), { items: 0, previous: false, noteRe: /no todo list in the loaded transcript window/ }],
    ['loading', deriveProgress(node(), convo({ loaded: false, chat: null })), { items: 0, previous: false, noteRe: /loading the transcript/ }],
    ['live', deriveProgress(node({ busy: true }), convo({ live: [liveTodoRow([{ content: 'a', status: 'completed' }, { content: 'b', status: 'in_progress' }])] })), { items: 2, previous: false, noteRe: /live, this turn/ }],
    ['durable', deriveProgress(node(), convo({}, [todoMsg()])), { items: 3, previous: false, noteRe: /from the transcript/ }],
    ['updating', deriveProgress(node({ busy: true }), convo({ live: [liveTodoRow()] }, [todoMsg()])), { items: 3, previous: true, noteRe: /updating its todo list/ }],
    ['earlier turn', deriveProgress(node({ busy: true, inflight_at: '2026-09-04T20:10:00Z' }), convo({}, [todoMsg(GLYPHS, '2026-09-04T20:00:00Z')])), { items: 3, previous: true, noteRe: /EARLIER turn/ }],
    ['archived', deriveProgress(node({ state: 'archived' }), convo({}, [todoMsg()])), { items: 3, previous: true, noteRe: /from the transcript/ }],
  ]
  for (const [name, model, want] of cases) {
    const view = await mountView(<ProgressView model={model} />, (el) => el)
    try {
      const todo = view.el.querySelector('.progress-todo')
      assert.ok(todo, `${name}: no todo section`)
      const note = todo!.querySelector('.progress-note')?.textContent ?? ''
      assert.match(note, want.noteRe, `${name}: note`)
      assert.equal(todo!.querySelectorAll('.todo-item').length, want.items, `${name}: item count`)
      assert.equal(Boolean(todo!.querySelector('.todo-items.previous')), want.previous, `${name}: previous marker`)
      // the invariant, stated as DOM: no items ⇒ a sentence is on screen
      if (!want.items) assert.ok(note.trim().length > 10, `${name}: empty list AND empty note`)
      // FR-17: a Codex checklist's section header names its source, on screen
      if (name.startsWith('codex')) {
        assert.match(todo!.querySelector('h4')?.textContent ?? '', /Codex/, `${name}: header names Codex`)
      }
      // the whole panel always has the lane + process line
      assert.match(view.el.querySelector('.progress-state')?.textContent ?? '', /·/, `${name}: state line`)
      // every other section is a sentence too, whether open or folded
      for (const sec of ['subagents', 'reported', 'activity']) {
        assert.ok(view.el.querySelector(`.progress-${sec} h4`), `${name}: ${sec} header`)
      }
    } finally { await view.unmount() }
  }
  // glyph rendering: statuses reach the DOM as classes AND glyphs
  const v = await mountView(<ProgressView model={deriveProgress(node(), convo({}, [todoMsg()]))} />, (el) => el)
  try {
    const rows = [...v.el.querySelectorAll('.todo-item')]
    assert.deepEqual(rows.map((r) => r.className), ['todo-item completed', 'todo-item in_progress', 'todo-item pending'])
    assert.deepEqual(rows.map((r) => r.querySelector('.todo-glyph')?.textContent), ['☑', '◐', '☐'])
    assert.deepEqual(rows.map((r) => r.querySelector('.todo-text')?.textContent), ['read the plan', 'write the panel', 'land it'])
    // the reported section is OPEN by default and renders the summary as text
    // ⚠ DOM nodes are asserted as BOOLEANS: a failed assert.equal(el, null)
    // makes node inspect the whole jsdom tree for its diff, which on this
    // machine is an "Array buffer allocation failed" and a 60 s timeout
    // instead of a message (measured while writing this)
    assert.equal(Boolean(v.el.querySelector('.progress-reported-row')), false,
      'no status reported → no chip row, only the sentence')
    assert.match(v.el.querySelector('.progress-sec.progress-reported .progress-note')?.textContent ?? '', /never reported/)
  } finally { await v.unmount() }
  const withStatus = await mountView(<ProgressView model={deriveProgress(node({ last_status: { status: 'working', summary: 'the summary text', at: new Date().toISOString() } }), convo())} />, (el) => el)
  try {
    assert.equal(withStatus.el.querySelector('.progress-reported-row .statuschip.working')?.textContent, 'working')
    assert.equal(withStatus.el.querySelector('.progress-summary')?.textContent, 'the summary text')
  } finally { await withStatus.unmount() }
})

test('§3b ProgressView: fold state is a habit — one app-wide key, and it toggles', async () => {
  localStorage.removeItem('orgtree-progress-open')
  const model = deriveProgress(node({ busy: true }), convo({ live: [{ kind: 'tool', text: 'Bash · ls' }] }))
  const view = await mountView(<ProgressView model={model} />, (el) => el)
  try {
    // activity is folded by default, so its tail is not in the DOM…
    assert.equal(Boolean(view.el.querySelector('.progress-tail')), false)
    // …but its HEADER is, so the section is never silently absent
    const btn = view.el.querySelector<HTMLButtonElement>('.progress-activity .progress-fold')
    assert.ok(btn)
    assert.equal(btn!.getAttribute('aria-expanded'), 'false')
    const { act } = await import('react')
    await act(async () => { btn!.click() })
    assert.equal(view.el.querySelector<HTMLButtonElement>('.progress-activity .progress-fold')?.getAttribute('aria-expanded'), 'true')
    assert.equal(view.el.querySelectorAll('.progress-tail li').length, 1)
    assert.match(localStorage.getItem('orgtree-progress-open') ?? '', /"activity":true/)
  } finally { await view.unmount() }
  // a fresh mount honours the stored habit
  const again = await mountView(<ProgressView model={model} />, (el) => el)
  try {
    assert.equal(again.el.querySelectorAll('.progress-tail li').length, 1, 'stored fold state was not honoured on remount')
  } finally { await again.unmount(); localStorage.removeItem('orgtree-progress-open') }
})

// ------------------------------------------------------ §4 the desk wiring

test('§4 DeskChat: fifth tab + header chip open the panel, fed by the shared conversation store', async (t) => {
  resetConvos()
  useFakeClock()
  const server = new FakeServer()
  installFetch(server)
  server.busy = true
  server.userMsg('plan the work')
  const n = node({ id: 'agent', busy: true, inflight_at: new Date().toISOString(), proc_live: true })
  const view = await mountView(
    <DeskChat node={n} map={new Map([['agent', n]])} op={op} slug="prog" toast={noop} pub={false} bare onJump={noop} />,
    (el) => el)
  t.after(async () => { await view.unmount(); resetConvos(); realClock() })
  await flush()
  const tabs = [...view.el.querySelectorAll('.cc-tabs button')].map((b) => b.textContent)
  assert.deepEqual(tabs, ['chat', 'history', 'files', 'inbox', 'progress'])
  assert.equal(Boolean(view.el.querySelector('.msgs.progress')), false, 'the panel must not be open on the chat tab')
  // the header chip: present, honest about the state, and a way in
  const chip = view.el.querySelector<HTMLButtonElement>('.cc-head-meta .progress-chip')
  assert.ok(chip, 'no progress chip in the metadata row')
  assert.equal(chip!.textContent, 'no todos')
  assert.match(chip!.getAttribute('title') ?? '', /no todo list in the loaded transcript window/)
  const { act } = await import('react')
  await act(async () => { chip!.click() })
  const panel = view.el.querySelector('.msgs.progress')
  assert.ok(panel, 'clicking the chip did not open the progress tab')
  assert.match(panel!.querySelector('.progress-todo .progress-note')?.textContent ?? '',
    /no todo list in the loaded transcript window/)
  assert.match(panel!.querySelector('.progress-state')?.textContent ?? '', /Claude · in a turn/)

  // now the agent writes a todo list mid-turn: the server's live tail carries
  // it (supervisor._todo_live_extra) and the panel — fed by the same store the
  // chat reads — shows it after the next poll, with NO extra fetch path
  server.live.push({ kind: 'tool', text: 'TodoWrite', id: 'toolu_x',
    todos: [{ content: 'first', status: 'completed' }, { content: 'second', status: 'in_progress' }] })
  const before = server.requests.length
  await advance(3000)
  await flush()
  assert.ok(server.requests.length > before, 'the desk stopped polling — the panel would never update')
  const rows = [...view.el.querySelectorAll('.msgs.progress .todo-item')]
  assert.deepEqual(rows.map((r) => r.textContent), ['☑first', '◐second'],
    'the live TodoWrite row did not reach the panel through the conversation store')
  assert.match(view.el.querySelector('.msgs.progress .progress-todo .progress-note')?.textContent ?? '',
    /live, this turn: 1\/2 done · now: second/)
  assert.equal(view.el.querySelector('.cc-head-meta .progress-chip')?.textContent, '◐ 1/2')
  // and the tab strip is the other way in
  const chatTab = [...view.el.querySelectorAll<HTMLButtonElement>('.cc-tabs button')].find((b) => b.textContent === 'chat')!
  await act(async () => { chatTab.click() })
  assert.equal(Boolean(view.el.querySelector('.msgs.progress')), false)
  const progTab = [...view.el.querySelectorAll<HTMLButtonElement>('.cc-tabs button')].find((b) => b.textContent === 'progress')!
  await act(async () => { progTab.click() })
  assert.ok(view.el.querySelector('.msgs.progress'))
})

test('§4b DeskChat (Codex): a live turn/plan/updated row reaches the SAME panel through the SAME wiring', async (t) => {
  resetConvos()
  useFakeClock()
  const server = new FakeServer()
  installFetch(server)
  server.busy = true
  server.userMsg('plan the work')
  const n = node({ id: 'agent', tier: 'luna', busy: true, inflight_at: new Date().toISOString(), proc_live: true })
  const view = await mountView(
    <DeskChat node={n} map={new Map([['agent', n]])} op={op} slug="prog" toast={noop} pub={false} bare onJump={noop} />,
    (el) => el)
  t.after(async () => { await view.unmount(); resetConvos(); realClock() })
  await flush()
  const chip = view.el.querySelector<HTMLButtonElement>('.cc-head-meta .progress-chip')
  assert.ok(chip, 'no progress chip in the metadata row')
  assert.notEqual(chip!.textContent, 'todo n/a', 'a Codex node must not show the old lane-cannot chip')
  server.live.push({ kind: 'plan', text: 'checklist updated', threadId: 'thread-1', turnId: 'turn-1',
    explanation: null, plan: [{ step: 'first', status: 'completed' }, { step: 'second', status: 'in_progress' }] })
  await advance(3000)
  await flush()
  const { act } = await import('react')
  await act(async () => { chip!.click() })
  const rows = [...view.el.querySelectorAll('.msgs.progress .todo-item')]
  assert.deepEqual(rows.map((r) => r.textContent), ['☑first', '◐second'],
    'the live turn/plan/updated row did not reach the panel through the conversation store')
  assert.match(view.el.querySelector('.msgs.progress h4')?.textContent ?? '', /Codex/)
  assert.equal(view.el.querySelector('.cc-head-meta .progress-chip')?.textContent, '◐ 1/2')
})
