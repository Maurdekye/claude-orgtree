// Mutation harness for references written INSIDE a presented document.
//
//   cd frontend && node mutate_docrefs.mjs
//
// The gallery is itself a document reader, so the interesting failures here
// are about WHO ANSWERS: the panel for a document it lists, the shell for one
// it does not, and nobody when no route was supplied. All three look the same
// from outside — a chip that does something — which is why each mutant below
// has to make a NAMED check go red rather than merely change behaviour.
//
// ⚠ RUN INSIDE A WORKTREE ONLY. It rewrites sources in place and restores the
// exact BYTES in a finally block; the files are CRLF and the templates here
// are LF, so both sides are normalised for the search and CRLF is written back.
//
// ⚠ MUTANTS ARE APPLIED WITH A REPLACER FUNCTION, NOT A STRING — in a string
// replacement `$` is special and a mutant containing one splices the file into
// itself. The length invariant below catches it if that is ever undone.

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const GALLERY = 'src/canvas/gallery.tsx'
const DOCS = 'src/canvas/docs.tsx'

const MUTANTS = [
  {
    name: 'the reader stops deciding references in a document body',
    file: GALLERY, kills: '§13 a reference in a document body',
    from: `            {doc && <RefMdBody className="mailer-body md"
              html={md(doc.body, fileBase(slug, doc.node))}
              world={refs?.world} onOpen={refs?.onOpen} />}`,
    to: `            {doc && <RefMdBody className="mailer-body md"
              html={md(doc.body, fileBase(slug, doc.node))} />}`,
  },
  {
    // the panel handing its OWN documents off to the shell: the list the
    // reader is part of closes, to show the same thing somewhere else
    name: 'a document this panel lists is handed to the shell anyway',
    file: GALLERY, kills: '§14 a document referencing a document',
    from: `      if (r.ref.kind === 'doc' && (all ?? []).some((d) => d.id === r.ref.id)) {`,
    to: `      if (false) {`,
  },
  {
    // and the opposite: every document swallowed here, including ones this
    // panel does not hold, which then silently selects nothing
    name: 'every document reference is swallowed by this panel',
    file: GALLERY, kills: '§15 CONTROL',
    from: `      if (r.ref.kind === 'doc' && (all ?? []).some((d) => d.id === r.ref.id)) {`,
    to: `      if (r.ref.kind === 'doc') {`,
  },
  {
    // ⚠ THE ONE THE OBVIOUS ASSERTION MISSES. Matching against the FILTERED
    // rows means a reference to a retired agent's document falls through
    // whenever "show retired" happens to be unticked — the panel deciding
    // what it holds by what it is currently showing.
    name: 'the panel asks what it is SHOWING, not what it holds',
    file: GALLERY, kills: '§14 a document referencing a document',
    from: `      if (r.ref.kind === 'doc' && (all ?? []).some((d) => d.id === r.ref.id)) {`,
    to: `      if (r.ref.kind === 'doc' && (rows ?? []).some((d) => d.id === r.ref.id)) {`,
    // (§14's target belongs to a RETIRED agent for exactly this reason: with
    // two live documents `rows` and `all` agree and this mutant survives)
  },
  {
    name: 'the standalone document reader stops deciding references',
    file: DOCS, kills: '§28 a @doc token',
    from: `        {doc && <RefMdBody className="doc-reader-body md"
          html={md(doc.body, fileBase(slug, doc.node))}
          world={refs?.world} onOpen={refs?.onOpen} />}`,
    to: `        {doc && <div className="doc-reader-body md"
          dangerouslySetInnerHTML={md(doc.body, fileBase(slug, doc.node))} />}`,
  },
]

const norm = (s) => s.replace(/\r\n/g, '\n')

function runSuite() {
  try {
    // BOTH suites: the gallery owns §13-§15, and the docket's §28 is what
    // proves the standalone reader still renders its document at all
    execFileSync(process.execPath, ['tests/run.mjs', 'gallery'],
      { stdio: 'pipe', encoding: 'utf8' })
    execFileSync(process.execPath, ['tests/run.mjs', 'docketrefs'],
      { stdio: 'pipe', encoding: 'utf8' })
    return { failed: false, out: '' }
  } catch (e) {
    return { failed: true, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
}

console.log('baseline — the gallery and docketrefs suites must be GREEN')
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
    const want = mutated.length - from.length + norm(e.to).length
    mutated = mutated.replace(from, () => norm(e.to))
    if (mutated.length !== want) {
      throw new Error(`replacement corrupted ${m.file}: ${mutated.length} `
        + `chars, expected ${want} — a '$' in the mutant text spliced`)
    }
  }
  if (stale) { survived++; continue }
  try {
    writeFileSync(m.file, Buffer.from(mutated.replace(/\n/g, '\r\n'), 'utf8'))
    const r = runSuite()
    // ⚠ THE NAME MUST APPEAR ON A FAILING LINE. `out` holds the whole run,
    // passing checks included, so a bare `includes(kills)` is true for almost
    // every mutant and attributes the kill to whichever check was named —
    // WRONG CHECK could then never fire. node:test marks failures with '✖'.
    const named = r.out.split('\n')
      .some((l) => l.trimStart().startsWith('✖') && l.includes(m.kills))
    if (r.failed && named) {
      console.log(`killed — ${m.name}`)
    } else if (r.failed) {
      console.error(`WRONG CHECK — ${m.name}`)
      console.error(`  the suite went red but "${m.kills}" is not among the failures`)
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
      console.error(`SURVIVED    — ${m.name}`)
      survived++
    }
  } finally {
    writeFileSync(m.file, before)     // exact bytes, CRLF and all
  }
}

console.log(`\n${MUTANTS.length - survived}/${MUTANTS.length} killed`)
process.exit(survived ? 1 : 0)
