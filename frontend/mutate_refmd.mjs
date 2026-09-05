// Mutation harness for the markdown reference pass.
//
//   cd frontend && node mutate_refmd.mjs
//
// This walk edits a document somebody is already reading, and every way it
// can be wrong is quiet: a duplicated chip looks like a chip, a chip built
// from stale data looks live, and a pass that rebuilds when it did not need
// to costs the reader their selection with no visible trace at all. Each
// mutant restores one of those and requires the NAMED check to go red.
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

const REFMD = 'src/canvas/refmd.tsx'
const WORKREFS = 'src/canvas/workrefs.tsx'
const REFLINKS = 'src/canvas/reflinks.tsx'

const MUTANTS = [
  {
    // ⚠ ASTRA'S COUNTEREXAMPLE: the exit compared OUTCOMES, so a document that
    // gained its real title kept showing the id — same outcome, different
    // visible answer, no rebuild.
    name: 'the cheap exit compares only the outcome again',
    file: REFMD, kills: 'a relabelled target is re-rendered',
    from: `      return el.getAttribute(SIG)
        === chipSig(r, r.outcome === 'ready' && clickable && !r.atDestination)`,
    to: `      return r.outcome === el.getAttribute(OUT)`,
  },
  {
    // the other half of the same defect: clickability is not part of the
    // comparison, so a body that loses its handler keeps a live button
    name: 'the cheap exit ignores whether the chip can still be clicked',
    file: REFMD, kills: 'losing the handler turns a live control back into text',
    from: `        === chipSig(r, r.outcome === 'ready' && clickable && !r.atDestination)`,
    to: `        === chipSig(r, r.outcome === 'ready')`,
  },
  {
    // a world that goes away leaves its controls behind
    name: 'losing the world leaves the chips in the DOM',
    file: REFMD, kills: '§14b LOSING the world takes the controls with it',
    from: `    else unlinkifyRefs(host.current)`,
    to: `    else { /* leave them */ }`,
  },
  {
    // the crash Astra executed: resolve before the guard
    name: 'the click handler resolves before checking the world',
    file: REFMD, kills: 'a click on a chip whose world has gone is refused',
    from: `    const world = worldOf()
    if (!world) return
    const r = resolveRef(parsed, world)`,
    to: `    const r = resolveRef(parsed, worldOf() as RefWorld)`,
  },
  {
    // identity on the DOM chip
    name: 'the DOM chip drops the model icon',
    file: REFMD, kills: '§8 an agent reference wears its current model',
    from: `  if (r.ref.kind === 'agent' && r.tier) {`,
    to: `  if (false) {`,
  },
  {
    name: 'the DOM chip is a control even at its own desk',
    file: REFMD, kills: 'at its own focused desk the reference is identity',
    from: `  const live = r.outcome === 'ready' && clickable && !r.atDestination`,
    to: `  const live = r.outcome === 'ready' && clickable`,
  },
  {
    name: 'the click handler follows a reference to where you already are',
    file: REFMD, kills: 'the destination refusal is in the ROUTER too',
    from: `    if (r.outcome !== 'ready' || r.atDestination) return`,
    to: `    if (r.outcome !== 'ready') return`,
  },
  {
    // identity on the REACT chip — the other renderer of the same decision
    name: 'the React chip drops the model icon',
    file: REFLINKS, suite: 'reflinks', kills: 'an agent reference wears its current model and still navigates',
    from: `  const icon = r.ref.kind === 'agent' && r.tier`,
    to: `  const icon = false`,
  },
  {
    name: 'the React chip is a control even at its own desk',
    file: REFLINKS, suite: 'reflinks', kills: 'at its own focused desk it is identity without a control',
    from: `  if (r.outcome === 'ready' && onOpen && !r.atDestination) {`,
    to: `  if (r.outcome === 'ready' && onOpen) {`,
  },
  {
    // ⚠ AN IDENTITY INVENTED FOR A TARGET THAT DOES NOT EXIST — the tier
    // resolver will answer for any string it is handed.
    name: 'a model is claimed for an agent this org does not have',
    file: REFLINKS, suite: 'reflinks', kills: 'the model is only claimed for a reference that resolves',
    from: `  const tier = ref.kind === 'agent' && outcome === 'ready'
    ? world.tierOf?.(ref.id) ?? null : null`,
    to: `  const tier = ref.kind === 'agent'
    ? world.tierOf?.(ref.id) ?? null : null`,
  },
  {
    // the destination rule applied to a name that does not resolve
    name: 'the destination is compared before the reference resolves',
    file: REFLINKS, suite: 'reflinks', kills: 'the two renderers agree',
    from: `  const atDestination = ref.kind === 'agent' && outcome === 'ready'
    && !!world.destination && world.destination === ref.id`,
    to: `  const atDestination = ref.kind === 'agent'
    && world.destination === ref.id`,
  },
  {
    // ⚠ THE WRONG-TARGET DEFECT, restored. Without the boundary a malformed
    // token does not fail — it truncates, and the surviving prefix is a valid
    // reference to something ELSE, rendered as an ordinary working control.
    // (Astra's counterexamples, 2026-09-05.)
    name: 'a token no longer has to end at a boundary',
    file: WORKREFS, kills: '§2b a malformed token is refused by the DOM walk',
    from: "  + END, 'g')",
    to: "  + '', 'g')",
  },
  {
    // the half-fix: refuse a trailing `@` outright, which also refuses the
    // second of two adjacent tokens
    name: 'the boundary refuses an adjacent canonical token too',
    file: WORKREFS, kills: '§2c CONTROL',
    from: "const END = '(?![A-Za-z0-9_/-])(?!@(?!(?:item|doc|agent|mail):))'",
    to: "const END = '(?![A-Za-z0-9_/-@])'",
  },
  {
    // the token in a fenced block is the thing being DISCUSSED
    name: 'code and pre are walked like ordinary prose',
    file: REFMD, kills: '§3 A TOKEN INSIDE CODE',
    from: `const SKIP = new Set(['CODE', 'PRE', 'A', 'BUTTON'])`,
    to: `const SKIP = new Set(['BUTTON'])`,
  },
  {
    name: 'a token inside a link becomes a second control in one target',
    file: REFMD, kills: '§4 CONTROL',
    from: `const SKIP = new Set(['CODE', 'PRE', 'A', 'BUTTON'])`,
    to: `const SKIP = new Set(['CODE', 'PRE', 'BUTTON'])`,
  },
  {
    // THE CLASSIC: run again on a container you already decorated and the
    // walk finds the text you left behind, wrapping chips inside chips
    name: 'a re-run does not undo its own previous injections first',
    file: REFMD, kills: '§5 THE SECOND PASS',
    from: `    if (same) return -1
    unlinkifyRefs(host)`,
    to: `    if (same) return -1`,
  },
  {
    name: 'every pass rebuilds, whether or not anything changed',
    file: REFMD, kills: '§6 A PASS THAT CHANGES NOTHING',
    from: `    if (same) return -1
    unlinkifyRefs(host)`,
    to: `    unlinkifyRefs(host)`,
  },
  {
    // the other direction: a container that already has chips is left alone
    // forever, so the index arriving changes nothing on screen
    name: 'a container with chips is never revisited',
    file: REFMD, kills: '§5 THE SECOND PASS',
    from: `    const same = existing.every((el) => {`,
    to: `    const same = true || existing.every((el) => {`,
  },
  {
    name: 'the chip is built by writing html instead of text',
    file: REFMD, kills: '§8 the html is never re-parsed',
    from: `  el.appendChild(doc.createTextNode(
    r.outcome === 'ready' ? r.label : r.token))`,
    to: `  el.innerHTML = r.outcome === 'ready' ? r.label : r.token`,
  },
  {
    // a failed reference showing a friendly label instead of the token hides
    // exactly what the person fixing it needs to see
    name: 'a failed reference shows the label rather than what was written',
    file: REFMD, kills: '§9 CONTROL',
    from: `  el.appendChild(doc.createTextNode(
    r.outcome === 'ready' ? r.label : r.token))`,
    to: `  el.appendChild(doc.createTextNode(r.label))`,
  },
  {
    name: 'the click trusts the rendered chip instead of asking again',
    file: REFMD, kills: '§11 the click is decided AGAIN',
    from: `    if (r.outcome !== 'ready' || r.atDestination) return`,
    to: `    if (el.getAttribute(OUT) !== 'ready') return`,
  },
  {
    name: 'undoing an injection restores the chip\'s LABEL, not the token',
    file: REFMD, kills: '§7 unlinkify restores',
    from: `    el.replaceWith(host.ownerDocument.createTextNode(
      el.getAttribute(TOK) ?? el.textContent ?? ''))`,
    to: `    el.replaceWith(host.ownerDocument.createTextNode(
      el.textContent ?? ''))`,
  },
  {
    // without this the restored text arrives as neighbouring fragments and
    // the next pass matches nothing across the seam
    name: 'the restored text nodes are left split',
    file: REFMD, kills: '§7 unlinkify restores',
    from: `  if (chips.length) host.normalize()`,
    to: `  if (false) host.normalize()`,
  },
  {
    name: 'the effect only runs when the world object changes identity',
    file: REFMD, kills: '§13 the body changing',
    from: `    else unlinkifyRefs(host.current)
  })`,
    to: `    else unlinkifyRefs(host.current)
  }, [world, onOpen])`,
  },
  {
    // ⚠ NO WORLD IS NOT AN EMPTY WORLD. Without the guard a caller that
    // was given no org judges every token in its own app's prose against
    // `undefined` — and the org check runs first, so they all come out
    // "another org".
    name: 'a surface with no world judges anyway',
    file: REFMD, kills: '§14 a body with no world',
    from: `    if (world) linkifyRefs(host.current, world, !!onOpen)
    else unlinkifyRefs(host.current)`,
    to: `    linkifyRefs(host.current, world ?? { org: '' }, !!onOpen)`,
  },
]

const norm = (s) => s.replace(/\r\n/g, '\n')

function runSuite(name = 'refmd') {
  try {
    execFileSync(process.execPath, ['tests/run.mjs', name],
      { stdio: 'pipe', encoding: 'utf8' })
    return { failed: false, out: '' }
  } catch (e) {
    return { failed: true, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
}

console.log('baseline — the refmd suite must be GREEN before anything is mutated')
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
    const r = runSuite(m.suite)
    const named = r.out.includes(m.kills)
    if (r.failed && named) {
      console.log(`killed — ${m.name}`)
    } else if (r.failed) {
      console.error(`WRONG CHECK — ${m.name}`)
      console.error(`  the suite went red but "${m.kills}" is not among the failures`)
      console.error(r.out.slice(0, 900))
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
