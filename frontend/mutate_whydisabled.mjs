// Mutation harness for wcc848de4 — "a disabled control says why".
//
// The checks in whydisabled.test.tsx / whydisabledstep.test.tsx are green
// against the fix. That is the easy half. This breaks the fix, one edit at a
// time, and requires the NAMED check to go red — a check that cannot fail is
// not a check, and a `title=` test is unusually easy to write in a way that
// passes on nothing (assert the helper's return value and never look at a
// button; assert "a title exists" and pass on a hard-coded one).
//
//   cd frontend && node mutate_whydisabled.mjs
//
// ⚠ RUN THIS INSIDE A WORKTREE ONLY. It rewrites source files in place. The
// originals are read as BYTES and written back as BYTES in a finally block,
// so line endings survive even if a mutant throws — but a crash between the
// two would still leave a mutated file, and these files are CRLF.
//
// ⚠ THE SOURCES ARE CRLF AND THE TEMPLATES BELOW ARE LF. Comparing them
// directly reports "target not found" for code that is sitting right there,
// which costs twenty minutes every time. Both sides are normalised to LF for
// the search and the result is written back with CRLF restored.

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const ASKS = 'src/canvas/asks.tsx'
const CARDS = 'src/canvas/cards.tsx'
const PICKER = 'src/picker.tsx'

const MUTANTS = [
  // ---------------------------------------------- the four stepper buttons
  {
    name: 'the standalone credit card\'s − loses its title',
    file: ASKS, suite: 'whydisabledstep', kills: '§7',
    from: `            <button type="button" disabled={g <= committed}
              title={stepDownWhy(g, committed)}`,
    to: `            <button type="button" disabled={g <= committed}`,
  },
  {
    name: 'the standalone credit card\'s ＋ loses its title',
    file: ASKS, suite: 'whydisabledstep', kills: '§8',
    from: `            <button type="button" disabled={maxTop != null && g >= maxTop}
              title={stepUpWhy(g, maxTop)}`,
    to: `            <button type="button" disabled={maxTop != null && g >= maxTop}`,
  },
  {
    name: 'the BATCH card\'s − loses its title (fixed one card, not both)',
    file: ASKS, suite: 'whydisabledstep', kills: '§9',
    from: `              <button type="button" disabled={d.credits.g <= committed}
                title={stepDownWhy(d.credits.g, committed)}`,
    to: `              <button type="button" disabled={d.credits.g <= committed}`,
  },
  {
    name: 'the BATCH card\'s ＋ loses its title',
    file: ASKS, suite: 'whydisabledstep', kills: '§9',
    from: `              <button type="button" disabled={maxTop != null && d.credits.g >= maxTop}
                title={stepUpWhy(d.credits.g, maxTop)}`,
    to: `              <button type="button" disabled={maxTop != null && d.credits.g >= maxTop}`,
  },

  // ------------------------------------------------- the helpers' branches
  {
    // the version anyone would write first
    name: 'stepDownWhy blames a commitment even when nothing is committed',
    file: ASKS, suite: 'whydisabled', kills: '§2',
    from: `  return committed > 0
    ? \``,
    to: `  return true
    ? \``,
  },
  {
    name: 'stepDownWhy speaks while the − button is still live',
    file: ASKS, suite: 'whydisabled', kills: '§3',
    from: `  if (g > committed) return undefined`,
    to: `  if (false) return undefined`,
  },
  {
    name: 'stepUpWhy speaks in an uncapped org ("cap of undefined")',
    file: ASKS, suite: 'whydisabledstep', kills: '§8b',
    from: `  (maxTop != null && g >= maxTop`,
    to: `  (g >= (maxTop ?? -1)`,
  },

  // --------------------------------------------------- the draft hire button
  {
    name: 'the draft hire button loses its title',
    file: CARDS, suite: 'whydisabled', kills: '§5',
    from: `            <button className="primary" disabled={!ok} onClick={hire}
              title={ok ? undefined : 'give the agent a name first'}>`,
    to: `            <button className="primary" disabled={!ok} onClick={hire}>`,
  },
  {
    // the shape a "just add a title" fix takes when nobody checks the live
    // state: the tooltip is permanent and now lies to a user who HAS named
    // the agent
    name: 'the draft hire title is unconditional',
    file: CARDS, suite: 'whydisabled', kills: '§5b',
    from: `              title={ok ? undefined : 'give the agent a name first'}>`,
    to: `              title={'give the agent a name first'}>`,
  },

  // --------------------------------------------------------- the picker
  {
    name: 'the picker button loses its title',
    file: PICKER, suite: 'whydisabled', kills: '§6',
    from: `            title={cur?.path ? undefined
              : cur ? 'this is the drive list, not a folder — open a drive to '
                + 'choose a folder inside it'
                : 'still reading the folder list'}`,
    to: ``,
  },
  {
    // ONE sentence for two different states reads fine and helps nobody: at
    // the drive list the user must act, while loading they must wait.
    name: 'the picker collapses its two states into one generic sentence',
    file: PICKER, suite: 'whydisabled', kills: '§6',
    from: `            title={cur?.path ? undefined
              : cur ? 'this is the drive list, not a folder — open a drive to '
                + 'choose a folder inside it'
                : 'still reading the folder list'}`,
    to: `            title={cur?.path ? undefined : 'no folder is selected'}`,
  },
]

const norm = (s) => s.replace(/\r\n/g, '\n')

function runSuite(suite) {
  try {
    execFileSync(process.execPath, ['tests/run.mjs', suite],
      { stdio: 'pipe', encoding: 'utf8' })
    return { failed: false, out: '' }
  } catch (e) {
    return { failed: true, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
}

// ---------------------------------------------------------------- baseline
console.log('baseline — both suites must be GREEN before anything is mutated')
for (const suite of ['whydisabled', 'whydisabledstep']) {
  const r = runSuite(suite)
  if (r.failed) {
    console.error(`  BASELINE RED (${suite}) — fix that first, not this file`)
    console.error(r.out.split('\n').slice(-30).join('\n'))
    process.exit(2)
  }
  console.log(`  ok ${suite}`)
}

// ⚠ `whydisabled` is a PREFIX of `whydisabledstep`, and run.mjs filters by
// substring — so running the "whydisabled" suite runs BOTH files. That is
// harmless (a mutant expected to kill §7 would be reported by either) but it
// would let a mutant aimed at a desktop check pass its verification on a
// mobile failure instead. Each mutant therefore names the exact check id it
// must kill, and that id is required in the output.
let survived = 0
for (const m of MUTANTS) {
  const before = readFileSync(m.file)
  const text = norm(before.toString('utf8'))
  const from = norm(m.from)
  const n = text.split(from).length - 1
  if (n !== 1) {
    console.error(`\nSKIPPED (target found ${n}x, expected 1): ${m.name}`)
    console.error(`  in ${m.file} — the harness is stale, NOT a pass`)
    survived++
    continue
  }
  try {
    // ⚠ A FUNCTION REPLACEMENT, NOT A STRING ONE. In a string replacement `$`
    // is special: `$`+backtick means "everything before the match" and `$&`
    // means the match, so a mutant whose text contains one SILENTLY SPLICES
    // THE WHOLE FILE INTO ITSELF. The suite then goes red on a syntax error
    // and this harness reports WRONG CHECK — the wrong diagnosis, pointing at
    // a line nobody touched. Measured, not assumed:
    //   'AAA_TARGET_ZZZ'.replace('TARGET', 'x$`y')     -> 'AAA_xAAA_y_ZZZ'
    //   'AAA_TARGET_ZZZ'.replace('TARGET', () => same) -> 'AAA_x$`y_ZZZ'
    // Found in the sibling harness by checklist-evidence and raised here.
    const mutated = text.replace(from, () => norm(m.to)).replace(/\n/g, '\r\n')
    writeFileSync(m.file, Buffer.from(mutated, 'utf8'))
    const r = runSuite(m.suite)
    // ⚠ THE NAME MUST APPEAR ON A FAILING LINE. `out` holds the whole run,
    // passing checks included, so a bare `includes(kills)` is true for almost
    // every mutant and attributes the kill to whichever check was named —
    // WRONG CHECK could then never fire. node:test marks failures with '✖'.
    const named = r.out.split('\n')
      .some((l) => l.trimStart().startsWith('✖') && l.includes(m.kills))
    if (r.failed && named) {
      console.log(`killed by ${m.kills.padEnd(4)} — ${m.name}`)
    } else if (r.failed) {
      console.error(`WRONG CHECK   — ${m.name}`)
      console.error(`  suite went red but ${m.kills} is not among the failures`)
      // ⚠ NAME WHAT DID FAIL. Without this the only way to correct a `kills`
      // is to re-apply the mutant by hand, which is how a misattribution
      // survives a reader's attention in the first place.
      for (const l of [...new Set(r.out.split('\n')
        .filter((x) => x.trimStart().startsWith('✖'))
        .map((x) => x.trim().replace(/\s*\([\d.]+ms\)$/, '')))]) {
        console.error(`    ${l}`)
      }
      survived++
    } else {
      console.error(`SURVIVED      — ${m.name}`)
      console.error(`  ${m.suite} stayed green with the fix removed`)
      survived++
    }
  } finally {
    writeFileSync(m.file, before)     // exact bytes, CRLF and all
  }
}

console.log(`\n${MUTANTS.length - survived}/${MUTANTS.length} killed`)
process.exit(survived ? 1 : 0)
