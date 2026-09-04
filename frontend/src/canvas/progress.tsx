// progress.tsx — FR-2: the desk's task-progress panel. "What is this agent
// actually working on right now?", answered from data the desk ALREADY holds
// (the tree payload's node, the conversation store's live tail + transcript)
// — no new poll, no new endpoint, nothing that costs the agent a turn.
//
// ⚠ THE CONTRACT THAT MAKES THIS FEATURE WORTH HAVING. An overlay that renders
// EMPTY when it has no data is worse than no overlay: it teaches the operator
// that the agent is doing nothing, when the truth is that we do not know.
// So every section of the model carries a VERDICT (`Supply`) and a NOTE, and
// the note is never empty: "no todo list in the loaded transcript window",
// "Codex agents do not report a todo list to orgtree yet" — a sentence about
// the lane, never blank space or a plausible-looking empty checklist. The
// test suite asserts non-empty copy in every cell of the lane × state table.
//
// The user's ruling (2026-09-04, via coordinator): the TodoWrite checklist —
// the `☑ ◐ ☐` list a Claude Code agent maintains — LEADS. The agent's own
// `orgtree_status` one-liner is the secondary section.
//
// Sources, in the order a Claude/OpenRouter desk prefers them:
//   1. the live tail — a `tool` row named TodoWrite carrying `todos`
//      (supervisor._todo_live_extra; live, this turn, now)
//   2. the transcript — the newest TodoWrite chip's rendered `☑ ◐ ☐` block
//      (supervisor._todo_glyphs; ≤ one poll behind, possibly an earlier turn)
//   3. neither → a sentence saying so, and which of the two it looked in.
// Codex and Antigravity have no todo source orgtree handles today, and the
// panel SAYS that rather than showing a Claude-shaped emptiness.

import { useCallback, useState } from 'react'
import type { ReactNode } from 'react'
import type { CanvasNode, LiveRow, ProviderId } from './shared'
import { ago, PROVIDER_LABEL, providerOf } from './shared'
import type { Convo } from '../convo'
import type { ChatMessage } from '../types'

// ------------------------------------------------------------------ model

/** Where a section's data came from — or why there is none. Every value
 *  other than `live`/`durable` is an ABSENCE WITH A REASON; the reason is the
 *  `note`, and the note is mandatory. */
export type Supply =
  | 'live'            // from the live tail: this turn, now
  | 'durable'         // from the transcript: written, possibly an earlier turn
  | 'updating'        // a live TodoWrite row WITHOUT contents (an older backend)
  | 'none-in-window'  // the lane can supply it and nothing was found in what we loaded
  | 'lane-cannot'     // this provider has no such source in orgtree
  | 'loading'         // the transcript has not arrived yet — we cannot say either way

export type TodoStatus = 'completed' | 'in_progress' | 'pending'
export interface TodoItem { content: string; status: TodoStatus }

export interface TodoVerdict {
  supply: Supply
  /** null unless there is a list to show; [] is a list the agent CLEARED */
  items: TodoItem[] | null
  /** when the list was written (live row `at` / message `ts`), if known */
  at: string | null
  /** the list predates the running turn — the current turn has not written one */
  earlierTurn: boolean
  /** ALWAYS non-empty: what is shown, or exactly why nothing is */
  note: string
}

export interface SubagentVerdict {
  fg: number
  bg: number
  /** the node payload carries the counts at all (synthetic cards do not) */
  known: boolean
  note: string
}

export interface ReportedVerdict {
  status: string | null
  summary: string | null
  at: string | null
  /** "working" with nothing since, past the engine's own check-up threshold */
  stale: boolean
  note: string
}

export interface ActivityVerdict {
  phase: string | null
  tool: string | null
  /** the most recent tool rows on the live wire, oldest first */
  tail: { text: string; at: string | null }[]
  note: string
}

export interface ProgressModel {
  lane: ProviderId
  laneLabel: string
  /** the panel is a HISTORICAL record — the agent is retired */
  historical: boolean
  /** one sentence about the process/turn state everything below sits in */
  processNote: string
  todo: TodoVerdict
  subagents: SubagentVerdict
  reported: ReportedVerdict
  activity: ActivityVerdict
  /** the collapsed header chip's text — never empty */
  chip: string
  /** the chip's tooltip — the todo note, so the reason is one hover away */
  chipTitle: string
}

/** WORKING_CHECKUP_AFTER_S in supervisor.py — the engine's own rule for "a
 *  `working` report this old is stale enough to ask about". Mirrored, not
 *  invented: the panel must agree with the check-up mail the engine sends. */
export const WORKING_STALE_MS = 30 * 60 * 1000

const TODO_ROW = /^TodoWrite(\s|$)/

/** The `☑ ◐ ☐` block supervisor._todo_glyphs renders into a TodoWrite chip's
 *  `result`, parsed back. ONE function, so a glyph change breaks exactly one
 *  thing and the test that pins the three glyphs names it. An unknown prefix
 *  is `pending` — degrade to a visible item, never to a dropped one. */
export function parseTodoResult(result: string | undefined | null): TodoItem[] {
  if (!result) return []
  const out: TodoItem[] = []
  for (const raw of result.split('\n')) {
    const line = raw.replace(/\r$/, '')
    if (!line.trim()) continue
    const glyph = line.slice(0, 1)
    const status: TodoStatus = glyph === '☑' ? 'completed'
      : glyph === '◐' ? 'in_progress' : 'pending'
    const content = (glyph === '☑' || glyph === '◐' || glyph === '☐')
      ? line.slice(1).replace(/^\s/, '') : line
    out.push({ content, status })
  }
  return out
}

function liveTodos(row: LiveRow): TodoItem[] | null {
  const raw = row.todos
  if (!Array.isArray(raw)) return null
  return raw.map((t) => {
    const s = String(t?.status ?? 'pending')
    return {
      content: String(t?.content ?? ''),
      status: s === 'completed' ? 'completed' : s === 'in_progress' ? 'in_progress' : 'pending',
    }
  })
}

/** the newest TodoWrite chip in the transcript window, walking backwards */
function durableTodo(messages: ChatMessage[]): { items: TodoItem[]; ts: string | null } | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i]
    if (!m) continue
    const tools = m.tools ?? []
    for (let j = tools.length - 1; j >= 0; j -= 1) {
      const t = tools[j]
      if (t && t.name === 'TodoWrite') {
        return { items: parseTodoResult(t.result), ts: m.ts ?? null }
      }
    }
  }
  return null
}

const count = (items: TodoItem[]) => ({
  done: items.filter((t) => t.status === 'completed').length,
  doing: items.find((t) => t.status === 'in_progress') ?? null,
  total: items.length,
})

function describeList(items: TodoItem[], at: string | null, prefix: string): string {
  if (!items.length) return `${prefix}the agent cleared its todo list${at ? ` ${ago(at)} ago` : ''}.`
  const c = count(items)
  const when = at ? ` · written ${ago(at)} ago` : ''
  const head = c.doing ? ` · now: ${c.doing.content}` : c.done === c.total ? ' · all done' : ''
  return `${prefix}${c.done}/${c.total} done${head}${when}`
}

function deriveTodo(node: CanvasNode, convo: Convo, lane: ProviderId): TodoVerdict {
  const none: TodoVerdict = { supply: 'none-in-window', items: null, at: null, earlierTurn: false, note: '' }
  if (lane === 'openai') {
    return { ...none, supply: 'lane-cannot',
      note: 'Codex agents do not report a todo list to orgtree yet — the CLI emits a '
        + 'turn/plan/updated notification, and orgtree does not handle it. '
        + 'Nothing here means "no plan"; it means orgtree cannot see one.' }
  }
  if (lane === 'google') {
    return { ...none, supply: 'lane-cannot',
      note: 'Antigravity agents have no todo or plan source orgtree knows of. '
        + 'Nothing here means "no plan"; it means orgtree cannot see one.' }
  }
  if (!convo.loaded) {
    return { ...none, supply: 'loading', note: 'loading the transcript… (cannot say yet whether there is a todo list)' }
  }
  const msgs = convo.chat?.messages ?? []
  const durable = durableTodo(msgs)
  // 1. the live tail, newest TodoWrite row
  for (let i = convo.live.length - 1; i >= 0; i -= 1) {
    const r = convo.live[i]
    if (!r || r.kind !== 'tool' || !TODO_ROW.test(r.text ?? '')) continue
    const at = r.at ?? null
    const items = liveTodos(r)
    if (items) {
      return { supply: 'live', items, at, earlierTurn: false,
        note: describeList(items, at, 'live, this turn: ') }
    }
    // the row exists but carries no contents — a backend from before the
    // live-wire fix. Say so, and show the previous list as previous.
    return { supply: 'updating', items: durable?.items ?? null, at: durable?.ts ?? null,
      earlierTurn: false,
      note: 'updating its todo list now — this backend does not stream the contents; '
        + 'they land when the transcript catches up'
        + (durable ? '. Below is the PREVIOUS list, not the new one.' : '. No earlier list to show.') }
  }
  // 2. the transcript
  if (durable) {
    const earlier = Boolean(node.busy && node.inflight_at && durable.ts && durable.ts < node.inflight_at)
    return { supply: 'durable', items: durable.items, at: durable.ts, earlierTurn: earlier,
      note: describeList(durable.items, durable.ts, earlier
        ? 'from an EARLIER turn — the running turn has not written a todo list yet: '
        : 'from the transcript: ') }
  }
  // 3. neither
  if (!msgs.length) {
    return { ...none, note: 'no transcript yet — this agent has not taken a turn, so there is no todo list to show.' }
  }
  return { ...none,
    note: `no todo list in the loaded transcript window (last ${msgs.length} message${msgs.length === 1 ? '' : 's'})`
      + (node.busy ? ' or on the live wire this turn.' : '.')
      + ' The agent has not written one there; an older one may exist further back.' }
}

function deriveSubagents(node: CanvasNode): SubagentVerdict {
  const fgRaw = node.tasks
  const bgRaw = node.bg_tasks
  const known = typeof fgRaw === 'number' || typeof bgRaw === 'number'
  const fg = fgRaw ?? 0
  const bg = bgRaw ?? 0
  if (!known) return { fg, bg, known, note: 'this payload carries no subagent counts (a synthetic card, or an older backend)' }
  if (!fg && !bg) return { fg, bg, known, note: node.busy ? 'no subagents running' : 'none (not in a turn)' }
  const parts: string[] = []
  if (fg) parts.push(`${fg} foreground`)
  if (bg) parts.push(`${bg} background`)
  return { fg, bg, known, note: parts.join(' · ') + ' subagent' + (fg + bg === 1 ? '' : 's') + ' running'
    + (bg ? ' — background ones outlive the reply, which is why a turn can look quiet for a long time' : '') }
}

function deriveReported(node: CanvasNode): ReportedVerdict {
  const s = node.last_status
  if (!s || !s.status) {
    return { status: null, summary: null, at: null, stale: false,
      note: 'this agent has never reported a status (orgtree_status)' }
  }
  const at = s.at ?? null
  const ageMs = at ? Date.now() - Date.parse(at) : NaN
  const stale = s.status === 'working' && Number.isFinite(ageMs) && ageMs > WORKING_STALE_MS
  const when = at ? `${ago(at)} ago` : 'time unknown'
  return { status: s.status, summary: s.summary ?? null, at, stale,
    note: stale
      ? `reported "working" ${when} and nothing since — older than the engine's 30-minute check-up threshold, so treat it as unconfirmed`
      : `reported ${when}` }
}

function deriveActivity(node: CanvasNode, convo: Convo): ActivityVerdict {
  const tail = convo.live.filter((r) => r.kind === 'tool').slice(-8)
    .map((r) => ({ text: r.text ?? '', at: r.at ?? null }))
  if (!node.busy) {
    return { phase: null, tool: null, tail, note: 'not in a turn — no live activity' }
  }
  const a = node.activity
  const phase = a?.phase ?? null
  const tool = a?.tool ?? null
  const note = phase === 'tool' ? `running a tool: ${tool ?? '(unnamed)'}`
    : phase === 'writing' ? 'writing its reply'
      : phase === 'thinking' ? 'thinking (no tool call or text on the wire yet)'
        : 'in a turn — phase unknown'
  return { phase, tool, tail,
    note: tail.length ? note : `${note} · no tool calls on the live wire yet this turn` }
}

export function deriveProgress(node: CanvasNode, convo: Convo): ProgressModel {
  const lane = providerOf(node.tier ?? '')
  const laneLabel = PROVIDER_LABEL[lane] ?? lane
  const historical = node.state === 'archived'
  const todo = deriveTodo(node, convo, lane)
  const subagents = deriveSubagents(node)
  const reported = deriveReported(node)
  const activity = deriveActivity(node, convo)
  const processNote = historical
    ? 'retired — everything below is the last known record, not live progress'
    : node.busy
      ? `in a turn${node.inflight_at ? ` for ${ago(node.inflight_at)}` : ''}`
      : node.proc_live
        ? 'idle — not in a turn (process warm); nothing below is live'
        : 'idle — not in a turn; nothing below is live'
  let chip: string
  if (todo.supply === 'lane-cannot') chip = 'todo n/a'
  else if (todo.supply === 'loading') chip = 'todo …'
  else if (todo.items) {
    const c = count(todo.items)
    chip = c.total ? `${c.doing ? '◐' : c.done === c.total ? '☑' : '☐'} ${c.done}/${c.total}` : 'todo cleared'
    if (todo.supply === 'updating') chip += ' ↻'
    else if (todo.earlierTurn) chip += ' (prev)'
  } else if (todo.supply === 'updating') chip = 'todo ↻'
  else chip = 'no todos'
  if (subagents.fg + subagents.bg > 0) chip += ` · ${subagents.fg + subagents.bg} sub`
  return { lane, laneLabel, historical, processNote, todo, subagents, reported, activity,
    chip, chipTitle: todo.note }
}

// ------------------------------------------------------- collapse prefs

/** one app-wide key, following `orgtree-start-view` (shared.ts): which
 *  sections you like open is a habit of yours, not a property of an agent */
const OPEN_KEY = 'orgtree-progress-open'
type SectionId = 'subagents' | 'reported' | 'activity'
const OPEN_DEFAULT: Record<SectionId, boolean> = { subagents: true, reported: true, activity: false }

function readOpen(): Record<SectionId, boolean> {
  try {
    const raw = localStorage.getItem(OPEN_KEY)
    if (!raw) return { ...OPEN_DEFAULT }
    const v = JSON.parse(raw) as Partial<Record<SectionId, boolean>>
    return { ...OPEN_DEFAULT, ...v }
  } catch { return { ...OPEN_DEFAULT } }
}

function useOpen(): [Record<SectionId, boolean>, (id: SectionId) => void] {
  const [open, setOpen] = useState(readOpen)
  const toggle = useCallback((id: SectionId) => setOpen((o) => {
    const next = { ...o, [id]: !o[id] }
    try { localStorage.setItem(OPEN_KEY, JSON.stringify(next)) } catch { /* private mode */ }
    return next
  }), [])
  return [open, toggle]
}

// ------------------------------------------------------------------ views

const GLYPH: Record<TodoStatus, string> = { completed: '☑', in_progress: '◐', pending: '☐' }

function TodoSection({ v, historical }: { v: TodoVerdict; historical: boolean }) {
  const dimmed = v.supply === 'updating' || v.earlierTurn || historical
  return (
    <section className={`progress-sec progress-todo supply-${v.supply}`}>
      <h4>todo list <span className="dim">· {v.supply === 'live' ? 'live' : v.supply === 'durable' ? 'transcript' : v.supply}</span></h4>
      <div className="progress-note">{v.note}</div>
      {v.items && v.items.length > 0 && (
        <ul className={'todo-items' + (dimmed ? ' previous' : '') + (historical ? ' historical' : '')}>
          {v.items.map((t, i) => (
            <li key={i} className={'todo-item ' + t.status}>
              <span className="todo-glyph" aria-hidden="true">{GLYPH[t.status]}</span>
              <span className="todo-text">{t.content}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Fold({ id, title, open, toggle, children }: {
  id: SectionId; title: string; open: boolean; toggle: (id: SectionId) => void; children: ReactNode
}) {
  return (
    <section className={`progress-sec progress-${id}` + (open ? ' open' : ' closed')}>
      <h4><button className="progress-fold" aria-expanded={open}
        onClick={() => toggle(id)}>{open ? '▾' : '▸'} {title}</button></h4>
      {open && children}
    </section>
  )
}

export function ProgressView({ model }: { model: ProgressModel }) {
  const [open, toggle] = useOpen()
  const m = model
  return (
    <div className="msgs progress" data-lane={m.lane}>
      <div className="progress-state">
        <b>{m.laneLabel}</b> · {m.processNote}
      </div>
      <TodoSection v={m.todo} historical={m.historical} />
      <Fold id="subagents" title="subagents" open={open.subagents} toggle={toggle}>
        <div className="progress-note">{m.subagents.note}</div>
      </Fold>
      <Fold id="reported" title="reported status" open={open.reported} toggle={toggle}>
        {m.reported.status && (
          <div className="progress-reported-row">
            <span className={'statuschip ' + m.reported.status}>{m.reported.status}</span>
            {m.reported.summary && <span className="progress-summary">{m.reported.summary}</span>}
          </div>
        )}
        <div className={'progress-note' + (m.reported.stale ? ' stale' : '')}>{m.reported.note}</div>
      </Fold>
      <Fold id="activity" title="activity" open={open.activity} toggle={toggle}>
        <div className="progress-note">{m.activity.note}</div>
        {m.activity.tail.length > 0 && (
          <ul className="progress-tail">
            {m.activity.tail.map((r, i) => (
              <li key={i}><span className="dim">{r.at ? ago(r.at) : ''}</span> {r.text}</li>
            ))}
          </ul>
        )}
      </Fold>
    </div>
  )
}

/** the collapsed summary in `.cc-head-meta`: click → the progress tab. This
 *  is what makes the tab discoverable — a tab nobody clicks is a feature
 *  nobody has. */
export function ProgressChip({ model, onClick }: { model: ProgressModel; onClick: () => void }) {
  return (
    <button className={`progress-chip supply-${model.todo.supply}`} title={model.chipTitle}
      onClick={onClick}>{model.chip}</button>
  )
}
