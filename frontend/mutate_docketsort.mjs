// Mutation harness for the docket's SORT SELECTOR.
//
//   cd frontend && node mutate_docketsort.mjs
//
// An order is the easiest thing in this app to get wrong invisibly. Every
// mutant below leaves a docket that renders, lists every item, and looks
// entirely reasonable — it is simply in an order that answers a different
// question from the one the selector claims. A green suite proves nothing on
// its own here, which is why each mutant names the check that must go red.
//
// ⚠ RUN INSIDE A WORKTREE ONLY. It rewrites sources in place and restores the
// exact BYTES in a finally block; the files are CRLF and the templates here
// are LF, so both sides are normalised for the search and CRLF is written
// back.
//
// ⚠ ONE MUTANT HERE WAS A DECLARED SURVIVOR FOR A WHILE — the `status_at`
// fallback, which no fixture reached because every fixture carried the field.
// Declaring it was honest but it was still an unguarded line, so the suite
// grew a case with the field ABSENT (an older BACKEND, not an older item) and
// the mutant is now killed like the rest. A gap you can close beats a gap you
// have documented.
//
// ⚠ THE `kills` STRINGS DELIBERATELY AVOID SECTION NUMBERS. docket.test.tsx
// reuses "§17" and "§18" across unrelated sections, so a number alone would
// match another section's failure and report a mutant as killed by a check
// that never ran.

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const DOCKET = 'src/canvas/docket.tsx'

const MUTANTS = [
  {
    // ⚠ ASTRA'S BLOCKER 8, restored: the latch compares the TARGET, so a second
    // deliberate click on the same reference is refused for the rest of the
    // session once the reader has moved away.
    name: 'the jump latch compares the target instead of the request',
    kills: 'following the SAME reference twice works the second time',
    from: `    const key = jumpKey(jumpTo, jumpSeq)
    if (!jumpTo || !data || doneJump.current === key) return
    doneJump.current = key`,
    to: `    const key = jumpTo
    if (!jumpTo || !data || doneJump.current === key) return
    doneJump.current = key`,
  },
  {
    // the opposite error: no latch at all, so every poll re-runs the jump and
    // drags the reader back to a row they deliberately left
    name: 'the jump has no latch, so every repoll re-runs it',
    kills: 'an unrelated repoll does NOT re-run the jump',
    from: `    if (!jumpTo || !data || doneJump.current === key) return`,
    to: `    if (!jumpTo || !data) return`,
  },
  {
    // THE SELECTOR THAT DOES NOTHING. It renders, it remembers your choice,
    // the caption even changes — and every mode shows the same list.
    name: 'the selector is decorative: every mode keeps the server order',
    kills: 'three orders, and Updated is still the default',
    from: `export function sortItems(items: WorkItem[], mode: DocketSortMode): WorkItem[] {
  if (mode === 'updated') return items`,
    to: `export function sortItems(items: WorkItem[], mode: DocketSortMode): WorkItem[] {
  if (mode !== 'never') return items`,
  },
  {
    // ⚠ THE WHOLE POINT OF THE THIRD CLOCK, undone in one line. "Status
    // changed" silently becomes "updated", so a progress note outranks a real
    // transition — which is the exact complaint the mode exists to answer.
    name: 'status order reads the update clock, so a note outranks a transition',
    kills: 'a progress-only update does not advance status order',
    from: `  if (mode === 'status') return String(it.status_at ?? it.at ?? '')`,
    to: `  if (mode === 'status') return String(it.docket_at ?? it.at ?? '')`,
  },
  {
    // the honest-looking fallback that lies: an item nobody has transitioned
    // sorts as just-changed because someone edited it
    name: 'the status clock falls back to the edit clock when absent',
    kills: 'an item from an older backend sorts by CREATION',
    from: `  if (mode === 'status') return String(it.status_at ?? it.at ?? '')`,
    to: `  if (mode === 'status') return String(it.status_at ?? it.updated_at ?? '')`,
  },
  {
    // ⚠ NOT A WRONG ORDER — AN UNSTABLE ONE. Two items stamped in the same
    // tick trade places between two five-second polls, under the cursor of
    // whoever is reading them.
    name: 'the tie-break is dropped, so equal stamps keep list position',
    kills: 'ties break deterministically',
    from: `    const na = String(a.slug ?? '')
    const nb = String(b.slug ?? '')
    return na < nb ? 1 : na > nb ? -1 : 0`,
    to: `    return 0`,
  },
  {
    // sorting that forgets the tree: children promoted to roots, which reads
    // as a flat list that happens to contain some familiar names
    name: 'the nesting is dropped, so sorting flattens the tree',
    kills: 'sorting orders SIBLINGS inside their parent',
    from: `    const p = it.parent && here.has(it.parent) && it.parent !== it.slug
      ? it.parent : null`,
    to: `    const p = null`,
  },
  {
    // the caption that stops describing the list. Small, and exactly the kind
    // of thing that survives a feature change: the panel then STATES an order
    // it is not in, which is worse than saying nothing.
    name: 'the caption is hard-coded to the old single order again',
    kills: 'three orders, and Updated is still the default',
    from: `            {SORT_MODES.find((s) => s.value === sortMode)?.why ?? SORT_MODES[0]!.why}`,
    to: `            {'most recently updated first'}`,
  },
  {
    // the default quietly changes: everyone's docket opens in an order they
    // did not choose, and only a check that clears the stored preference
    // first can see it
    name: 'the default order is no longer Updated',
    kills: 'three orders, and Updated is still the default',
    from: `  } catch { /* storage disabled or unavailable — fall back to the default */ }
  return 'updated'`,
    to: `  } catch { /* storage disabled or unavailable — fall back to the default */ }
  return 'created'`,
  },
]

const norm = (s) => s.replace(/\r\n/g, '\n')

function runSuite() {
  try {
    execFileSync(process.execPath, ['tests/run.mjs', 'docket'],
      { stdio: 'pipe', encoding: 'utf8' })
    return { failed: false, out: '' }
  } catch (e) {
    return { failed: true, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
}

console.log('baseline — the docket suite must be GREEN before anything is mutated')
{
  const r = runSuite()
  if (r.failed) {
    console.error('  BASELINE RED — fix that first, not this file')
    console.error(r.out.split('\n').slice(-40).join('\n'))
    process.exit(2)
  }
  console.log('  ok')
}

let survived = 0
for (const m of MUTANTS) {
  const before = readFileSync(DOCKET)
  const text = norm(before.toString('utf8'))
  const from = norm(m.from)
  const n = text.split(from).length - 1
  if (n !== 1) {
    console.error(`\nSKIPPED (target found ${n}x, expected 1): ${m.name}`)
    console.error('  the harness is stale, NOT a pass')
    survived++
    continue
  }
  try {
    writeFileSync(DOCKET, Buffer.from(
      text.replace(from, () => norm(m.to)).replace(/\n/g, '\r\n'), 'utf8'))
    const r = runSuite()
    const named = r.out.includes(m.kills)
    if (r.failed && named) {
      console.log(`killed — ${m.name}`)
    } else if (r.failed) {
      console.error(`WRONG CHECK — ${m.name}`)
      console.error(`  the suite went red but "${m.kills}" is not among the failures`)
      survived++
    } else {
      console.error(`SURVIVED — ${m.name}`)
      survived++
    }
  } finally {
    writeFileSync(DOCKET, before)
  }
}

const real = MUTANTS.filter((m) => !m.expectSurvive).length
console.log(`\n${real - survived}/${real} killed`)
process.exit(survived ? 1 : 0)
