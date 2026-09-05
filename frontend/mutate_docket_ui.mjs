// mutate_docket_ui.mjs — do the panel checks from Astra's 2026-09-05 review
// actually catch the behaviour they were written for?
//
// Each mutant restores the PRE-FIX code for one finding, runs the docket suite,
// and requires the named check to be the one that goes red. A mutant the suite
// still passes means that check is decoration. The file is restored afterwards
// from the exact bytes read at startup.
//
//   node mutate_docket_ui.mjs        (from frontend/)
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const FILE = 'src/canvas/docket.tsx'

const MUTANTS = [
  {
    name: 'stale-cache-wins-over-the-current-row',
    from: `    for (const item of archivedCache) map.set(item.id, item)
    for (const item of backlogCache) map.set(item.id, item)
    for (const item of (data?.archived ?? [])) map.set(item.id, item)
    for (const item of (data?.backlogged ?? [])) map.set(item.id, item)
    for (const item of active) map.set(item.id, item)`,
    to: `    for (const item of active) map.set(item.id, item)
    for (const item of (data?.backlogged ?? backlogCache)) map.set(item.id, item)
    for (const item of (data?.archived ?? archivedCache)) map.set(item.id, item)`,
    mustFail: '§31',
  },
  {
    // ⚠ DECLARED UNKILLABLE, AND HERE IS THE ATTACK ON THAT EXEMPTION. Scoping
    // the cached rows to the org is defence in depth, not a fix with its own
    // observable behaviour, because TWO other things already prevent the stale
    // rows from reaching the screen: `usePolled` nulls its value whenever its
    // deps change (the slug is a dep), so a switch blanks the list before any
    // cached row could render; and the selection is scoped too, so `allKnown`
    // cannot deliver a previous-org item to the detail pane. Remove EITHER of
    // those and this becomes killable again — which is exactly why it stays.
    // Recorded rather than deleted, so nobody re-derives it as a real gap.
    name: 'the-cached-rows-are-not-scoped-to-the-org',
    expectSurvives: true,
    from: `  const archivedCache = cache.slug === slug ? cache.archived : []
  const backlogCache = cache.slug === slug ? cache.backlog : []`,
    to: `  const archivedCache = cache.archived
  const backlogCache = cache.backlog`,
    mustFail: '§32',
  },
  {
    // the other half, and only observable when the new org holds the SAME id —
    // otherwise the lookup misses and the pane is empty for the other reason.
    // That is exactly why §32 alone could not catch this, and why §32b exists:
    // the first version of this harness recorded it as SURVIVED, which is how
    // the gap was found rather than assumed away.
    name: 'the-selection-is-not-scoped-to-the-org',
    from: `  const selId = sel?.slug === slug ? sel.id : null`,
    to: `  const selId = sel ? sel.id : null`,
    mustFail: '§32b',
  },
  {
    name: 'a-missing-clipboard-reports-success',
    from: `    if (!write) { flash('failed'); return }`,
    to: `    if (!write) { flash('copied'); return }`,
    mustFail: '§33',
  },
]

// ⚠ THE SOURCES ARE CRLF. Comparing an LF template literal against the raw file
// matches nothing, and the harness then reports "target not found" for code
// sitting right there — which reads exactly like a stale harness and is not.
// Normalise to LF, mutate, write CRLF back, restore the original bytes.
const originalRaw = readFileSync(FILE, 'utf8')
const original = originalRaw.split('\r\n').join('\n')
const restore = () => writeFileSync(FILE, originalRaw)
const writeMutant = (text) => writeFileSync(FILE, text.split('\n').join('\r\n'))

function runSuite() {
  try {
    return execFileSync('node', ['tests/run.mjs', 'docket'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
  } catch (e) {
    return (e.stdout ?? '') + (e.stderr ?? '')
  }
}

console.log('baseline ...')
const base = runSuite()
const bm = base.match(/pass (\d+)[\s\S]*?fail (\d+)/)
if (!bm || bm[2] !== '0') {
  console.log('BASELINE IS NOT GREEN — every result below would be meaningless.')
  console.log(base.slice(-2000))
  process.exit(2)
}
console.log(`  baseline: ${bm[1]} passed, 0 failed`)

let bad = 0
try {
  for (const m of MUTANTS) {
    if (!original.includes(m.from)) {
      console.log(`  ! ${m.name}: target text not found — THIS HARNESS IS STALE, fix it`)
      bad++
      continue
    }
    writeMutant(original.replace(m.from, m.to))
    const out = runSuite()
    restore()
    const failed = [...out.matchAll(/^✖ (§\S+ .+?) \(/gm)].map((x) => x[1])
    const hit = failed.filter((f) => f.startsWith(m.mustFail))
    if (failed.length === 0) {
      if (m.expectSurvives) {
        console.log(`  survived (EXPECTED) ${m.name} — defence in depth, see the note above it`)
      } else {
        console.log(`  SURVIVED ${m.name}: the suite still passed — ${m.mustFail} does not test this`)
        bad++
      }
    } else if (m.expectSurvives) {
      console.log(`  ! ${m.name}: expected to survive but went red on ${failed} — the note above it is now WRONG`)
      bad++
    } else if (hit.length === 0) {
      console.log(`  MISDIRECTED ${m.name}: red, but not on ${m.mustFail} — ${failed}`)
      bad++
    } else {
      console.log(`  killed   ${m.name}  ->  x ${hit[0].slice(0, 72)}`)
    }
  }
} finally {
  restore()
}
const expected = MUTANTS.filter((m) => m.expectSurvives).length
console.log(bad
  ? `\n${bad} mutant(s) not properly killed`
  : `\n${MUTANTS.length - expected} of ${MUTANTS.length} mutants killed by the named `
    + `check; ${expected} survived BY DESIGN and is annotated as such`)
process.exit(bad ? 1 : 0)
