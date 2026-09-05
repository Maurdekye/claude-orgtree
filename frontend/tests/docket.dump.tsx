// docket.dump.tsx — the markup half of `docket_layout_probe.py` (w31b77251).
//
// The real <DocketModal/>, rendered against a fixture built to be HOSTILE to
// the list row: the longest slugs the org actually has, long agent names
// beside them, and every row state at once. The user's objection was a LAYOUT
// one — a padded name button ate the row's metadata space and truncated the
// agent name to "c…" — and no DOM test can see that. The probe measures this
// in a real browser.
//
//   node tests/docket_dump.mjs <out.html>

import '../tests/harness'
import { writeFileSync } from 'node:fs'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { DocketModal } from '../src/canvas/docket'
import type { TreePayload, WorkItem } from '../src/types'

const mk = (o: Partial<WorkItem>): WorkItem => ({
  id: 'w0', slug: null, rev: 1, kind: 'code', title: 'Item',
  objective: '', status: 'in_progress', blocked_reason: null,
  archived: false, archived_at: null,
  owner: { node: 'agent1', generation: 1 }, owner_current: true,
  owner_state: 'live', participants: [],
  created_by: { node: 'agent1', generation: 1 },
  at: '2026-09-05T08:00:00.000Z', updated_at: '2026-09-05T09:00:00.000Z',
  done_so_far: [], working_on_next: [],
  docket_at: '2026-09-05T09:00:00.000Z',
  last_updater: { node: 'agent1', generation: 1 },
  manual_attention: null, dismissals: [], questions: [],
  effective_attention: false, attention_sources: [],
  acceptance: [], dependencies: [], evidence: [], delivery: null,
  accepted: null, superseded_by: null, history: [],
  ...o,
} as unknown as WorkItem)

// REAL NAMES AND REAL LENGTHS. These are this org's actual item slugs and
// agent ids — the row has to hold the ones it will really be given, not a
// convenient short sample.
const ITEMS: WorkItem[] = [
  mk({ id: 'w1', slug: 'working-status-nudges-every-twenty-minutes',
    title: 'Working-status nudges every twenty minutes',
    status: 'review',
    last_updater: { node: 'coordinator-astra', generation: 0 },
    objective: 'agents report "working" and go quiet; nudge them. Blocked '
      + 'behind clickable-docket-references for the link work.' }),
  mk({ id: 'w2', slug: 'clickable-docket-references-across-text-surfaces',
    title: 'Clickable docket references across text surfaces',
    status: 'in_progress',
    last_updater: { node: 'codex-checklist', generation: 5 },
    // the description carries all three cases at once: a mention that links,
    // the same name inside a URL that must not, and the same name as the head
    // of a longer word that must not either
    objective: 'docket references require manual lookup. Make the slug the '
      + 'primary name and turn a mention of explain-unavailable-actions into '
      + 'a link, while https://example.dev/explain-unavailable-actions and '
      + 'explain-unavailable-actions-notes stay as ordinary text.',
    done_so_far: ['removed the boxed copy button from list and detail',
      'shared renderer used by working-status-nudges-every-twenty-minutes too'],
    working_on_next: ['the four approved elements of nested-docket-items'] }),
  mk({ id: 'w3', slug: 'antigravity-usage-limit-estimation',
    title: 'Antigravity usage-limit estimation', status: 'blocked',
    blocked_reason: 'no authoritative window',
    last_updater: { node: 'agy-journal', generation: 2 } }),
  mk({ id: 'w4', slug: 'explain-unavailable-actions',
    title: 'Explain why an unavailable action is unavailable', status: 'done',
    effective_attention: true, attention_sources: ['manual'],
    manual_attention: { reason: 'needs a look before the next deploy',
      at: '2026-09-05T09:30:00.000Z',
      by: { node: 'coordinator-astra', generation: 0 }, set_rev: 1 },
    last_updater: { node: 'codex-checklist', generation: 5 } }),
  // the legacy row the running backend still serves: no slug at all
  mk({ id: 'w5ffabcd', slug: null, title: 'An item minted before slugs existed',
    status: 'open', last_updater: { node: 'mail-ack-contract', generation: 1 } }),
]

const BACKLOG: WorkItem[] = [
  mk({ id: 'w6', slug: 'nested-docket-items', status: 'backlogged',
    title: 'Expandable docket items with sub-items',
    last_updater: { node: 'coordinator-astra', generation: 0 } }),
]
const ARCHIVE: WorkItem[] = [
  mk({ id: 'w7', slug: 'git-review-workspace', status: 'done', archived: true,
    title: 'A finished thing', last_updater: { node: 'luna-reserve', generation: 1 } }),
]

;(globalThis as unknown as { fetch: typeof fetch }).fetch = ((url: string) => {
  const path = String(url)
  const ok = (payload: unknown) => Promise.resolve({
    ok: true, status: 200, headers: new Headers(),
    json: () => Promise.resolve(payload),
  })
  if (path.includes('/work-items')) {
    return ok({
      items: ITEMS,
      ...(path.includes('archived=1') ? { archived: ARCHIVE } : {}),
      ...(path.includes('backlogged=1') ? { backlogged: BACKLOG } : {}),
      counts: { attention: 1, active: ITEMS.length, archived: 1, backlogged: 1 },
      now: '2026-09-05T10:00:00.000Z',
    })
  }
  return ok({})
}) as unknown as typeof fetch

const tree = {
  slug: 'org1', name: 'Org 1', epoch: 1, rev: 1,
  roots: [
    { id: 'coordinator-astra', tier: 'fable', state: 'live', generation: 0, children: [] },
    { id: 'codex-checklist', tier: 'opus', state: 'live', generation: 5, children: [] },
    { id: 'agy-journal', tier: 'sonnet', state: 'live', generation: 2, children: [] },
    { id: 'mail-ack-contract', tier: 'haiku', state: 'live', generation: 1, children: [] },
    { id: 'luna-reserve', tier: 'luna', state: 'live', generation: 1, children: [] },
  ],
  work_items_summary: { attention: 1, active: ITEMS.length },
  user_inbox_count: 0, user_inbox_urgent_count: 0, asks: [], asks_open: 0,
} as unknown as TreePayload

const host = document.createElement('div')
document.body.appendChild(host)
const root = createRoot(host)
flushSync(() => {
  root.render(
    <DocketModal slug="org1" toast={() => {}} close={() => {}} tree={tree} />)
})

// let the mocked fetch resolve, then select the item whose detail pane carries
// the mentions — the pane is half of what the user is looking at
const dest = process.argv[2]
if (!dest) {
  console.error('usage: node tests/docket_dump.mjs <out.html>')
  process.exit(2)
}
setTimeout(() => {
  const rows = [...document.querySelectorAll('.mailrow.docket-row')]
  const target = rows.find((r) =>
    r.querySelector('.l1 .mfrom')?.textContent
      === 'clickable-docket-references-across-text-surfaces')
  flushSync(() => { (target as HTMLElement | undefined)?.click() })
  setTimeout(() => {
    writeFileSync(dest, host.innerHTML, 'utf8')
    console.log(`wrote ${dest} (${host.innerHTML.length} bytes)`)
    process.exit(0)
  }, 30)
}, 30)
