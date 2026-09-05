// Mutation harness for w31b77251 — slug-named rows and clickable mentions.
//
//   cd frontend && node mutate_docketrefs.mjs
//
// Green tests are the easy half. This breaks the feature one edit at a time
// and requires the NAMED check to go red. The mutants are not random: each is
// a way this could plausibly have been built, including the two the first
// draft actually got wrong (the refetch key, and a `.docket-pane-sub button`
// assertion that caught the agent-jump button instead of a copy control).
//
// ⚠ RUN INSIDE A WORKTREE ONLY. It rewrites sources in place, restoring exact
// BYTES in a finally block — these files are CRLF and the templates here are
// LF, so both sides are normalised for the search and CRLF is written back.
//
// ⚠ `docket` MATCHES BOTH SUITES. run.mjs filters by substring, so the suite
// name "docket" runs docket.test.tsx AND docketrefs.test.tsx. That is wanted —
// several mutants should be caught by the older suite — but it means a mutant
// must name a check by a DISTINCTIVE phrase, not by "§2", which exists in both.

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const DOCKET = 'src/canvas/docket.tsx'
const REFS = 'src/canvas/workrefs.tsx'

const MUTANTS = [
  // ------------------------------------------------ the list is named by slug
  {
    name: 'the row goes back to being named by its descriptive title',
    file: DOCKET, kills: 'NAMED BY ITS SLUG',
    from: `        <span className="mfrom docket-rowname">{itemName(item)}</span>`,
    to: `        <span className="mfrom docket-rowname">{item.title}</span>`,
  },
  {
    // the exact control the user rejected, twice, from screenshots
    name: 'the detail name becomes a button again',
    file: DOCKET, kills: 'no copy control',
    from: `    <span className="docket-slug-text"`,
    to: `    <button className="docket-slug-text"`,
    also: { from: `    </span>\n  )\n}\n\nexport function DocketToolbarButton`,
      to: `    </button>\n  )\n}\n\nexport function DocketToolbarButton` },
  },
  {
    name: 'the detail pane stops printing the full descriptive title',
    file: DOCKET, kills: 'no copy control',
    from: `        <b>{item.title || '(untitled)'}</b>`,
    to: `        <b>{itemName(item)}</b>`,
  },

  // ------------------------------------------------------- what is a mention
  {
    name: 'the BEFORE boundary is dropped — a URL path becomes a link',
    file: REFS, kills: 'URLs, paths and dotted identifiers',
    from: `  if (before && BLOCKS_BEFORE.test(before)) return false`,
    to: `  if (false) return false`,
  },
  {
    name: 'the AFTER boundary is dropped — a prefix of a longer name links',
    file: REFS, kills: 'THE WRONG-ITEM CASE',
    from: `  if (after && BLOCKS_AFTER.test(after)) return false`,
    to: `  if (false) return false`,
  },
  {
    name: 'a full stop after a name is treated as prose in every case',
    file: REFS, kills: 'URLs, paths and dotted identifiers',
    from: `  if (after === '.' && /[A-Za-z0-9]/.test(text[end + 1] ?? '')) return false`,
    to: `  if (false) return false`,
  },
  {
    // JS alternation is first-match-wins, so insertion order decides which of
    // two overlapping names is tried first
    name: 'the alternation is no longer longest-first',
    file: REFS, kills: 'THE WRONG-ITEM CASE',
    from: `    .sort((a, b) => b.length - a.length || (a < b ? -1 : 1))`,
    to: `    .sort((a, b) => a.length - b.length || (a < b ? -1 : 1))`,
  },
  {
    name: 'an empty slug is allowed into the index',
    file: REFS, kills: 'contributes nothing to the index',
    from: `    if (it?.slug && it.id) out.set(it.slug, it.id)`,
    to: `    if (it?.slug != null && it.id) out.set(it.slug, it.id)`,
  },
  {
    name: 'mentions render as plain text — the renderer is never asked',
    file: REFS, kills: 'the sentence still reads',
    from: `  if (!onPick) return <>{text}</>`,
    to: `  return <>{text}</>`,
  },

  // ------------------------------------------------------------- navigation
  {
    name: 'a link selects but never reveals a filtered-out group',
    file: DOCKET, kills: 'HIDDEN BACKLOG',
    from: `    if (it.archived) setShowArchived(true)
    else if (it.status === 'backlogged') setShowBacklog(true)`,
    to: ``,
  },
  {
    // the whole reason both groups are fetched unconditionally
    name: 'the hidden groups are only fetched when their checkbox is on',
    file: DOCKET, kills: 'HIDDEN BACKLOG',
    from: `  const data = usePolled(() => getWorkItems(slug, true, true),`,
    to: `  const data = usePolled(() => getWorkItems(slug, showArchived, showBacklog),`,
  },
  {
    // THE ONE THIS AUTHOR ACTUALLY BROKE. Taking the toggles out of the
    // refresh key looks like tidy-up once the request no longer uses them.
    name: 'the toggles leave the refresh key, so a tick no longer refetches',
    file: DOCKET, kills: 'CURRENT status, not the copy we cached',
    from: '    [slug], 5000, `${bump}-${showArchived}-${showBacklog}`)',
    to: '    [slug], 5000, `${bump}`)',
  },
  // ------------------------- w2d5fab0a elements 3 and 4 (the two that need
  // ------------------------- no parent relation)
  {
    name: 'the status dot echoes the status even when the row is flagged',
    file: DOCKET, kills: 'ATTENTION outranks it',
    from: `        <span className={'docket-dot status-' + item.status
          + (attention ? ' attention' : '')} aria-hidden="true" />`,
    to: `        <span className={'docket-dot status-' + item.status}
          aria-hidden="true" />`,
  },
  {
    name: 'both progress lists are handed over as the same kind',
    file: DOCKET, kills: 'different kinds of line',
    from: `      <DocketList heading="WORKING ON / NEXT" items={item.working_on_next}
        mark="next" slugIndex={slugIndex} onGoToItem={onGoToItem} />`,
    to: `      <DocketList heading="WORKING ON / NEXT" items={item.working_on_next}
        mark="done" slugIndex={slugIndex} onGoToItem={onGoToItem} />`,
  },
  {
    name: 'the arrived-here mark never appears',
    file: DOCKET, kills: 'selects, reveals and marks',
    from: `    selected ? 'on' : '', flash ? 'docket-flash' : ''].filter(Boolean).join(' ')`,
    to: `    selected ? 'on' : ''].filter(Boolean).join(' ')`,
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

console.log('baseline — the docket suites must be GREEN before anything is mutated')
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
  const before = readFileSync(m.file)
  const text = norm(before.toString('utf8'))
  const edits = [{ from: m.from, to: m.to }, ...(m.also ? [m.also] : [])]
  let mutated = text
  let stale = false
  for (const e of edits) {
    const from = norm(e.from)
    const n = mutated.split(from).length - 1
    if (n !== 1) {
      console.error(`\nSKIPPED (target found ${n}x, expected 1): ${m.name}`)
      console.error(`  in ${m.file} — the harness is stale, NOT a pass`)
      stale = true
      break
    }
    mutated = mutated.replace(from, norm(e.to))
  }
  if (stale) { survived++; continue }
  try {
    writeFileSync(m.file, Buffer.from(mutated.replace(/\n/g, '\r\n'), 'utf8'))
    const r = runSuite()
    const named = r.out.includes(m.kills)
    if (r.failed && named) {
      console.log(`killed — ${m.name}`)
    } else if (r.failed) {
      console.error(`WRONG CHECK — ${m.name}`)
      console.error(`  the suite went red but "${m.kills}" is not among the failures`)
      survived++
    } else {
      console.error(`SURVIVED    — ${m.name}`)
      survived++
    }
  } finally {
    writeFileSync(m.file, before)     // exact bytes, CRLF and all
  }
}

console.log(`\n${MUTANTS.length - survived}/${MUTANTS.length} killed`)
process.exit(survived ? 1 : 0)
