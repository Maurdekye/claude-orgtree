// Mutation harness for the canonical-reference renderer (reflinks.tsx).
//
//   cd frontend && node mutate_reflinks.mjs
//
// A brand-new module with a green suite has proved nothing: every check in it
// was written by the same person who wrote the code, against the code as
// written. This breaks the feature one edit at a time and requires the NAMED
// check to go red.
//
// THE MUTANTS ARE THE PLAUSIBLE BUILDS, not random damage. Four of them are
// versions I nearly wrote — judging the index before the org, collapsing
// `pending` into `absent` with a `?? new Map()`, sharing the /g regex, and
// dropping a failed ref back to plain prose (the one Astra rejected outright).
//
// ⚠ RUN INSIDE A WORKTREE ONLY. It rewrites sources in place and restores the
// exact BYTES in a finally block; the file is CRLF and the templates here are
// LF, so both sides are normalised for the search and CRLF is written back.
//
// ⚠ THE SUITE FILTER IS A SUBSTRING. "reflinks" matches reflinks.test.tsx
// alone, so `kills` may name a §, but a distinctive phrase is still safer.

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const F = 'src/canvas/reflinks.tsx'
const REFS = 'src/canvas/workrefs.tsx'

const MUTANTS = [
  // ------------------------------------------- the org is checked, and FIRST
  {
    name: 'the org segment is ignored — a foreign token resolves locally',
    file: F, kills: '§4 CONTROL',
    from: `  if (ref.org !== world.org) {`,
    to: `  if (false) {`,
  },
  {
    // ORDERING, not presence. This still refuses foreign refs — but only after
    // the index has spoken, so during load a foreign ref reads as `pending`
    // and, on a surface with no index at all, as `ready`.
    name: 'the org check moves after the index lookup',
    file: F, kills: '§4b',
    from: `  if (ref.org !== world.org) {
    return { ref, token, outcome: 'foreign', label: ref.id,`,
    to: `  if (ref.org !== world.org && world.items !== 'loading') {
    return { ref, token, outcome: 'foreign', label: ref.id,`,
  },

  // -------------------------------------------- pending is not absent, twice
  {
    // the `?? new Map()` shape: an index that has not arrived is treated as an
    // index that arrived empty, so every ref reads "does not exist" while the
    // page is still loading.
    name: 'a loading index is treated as an empty one',
    file: F, kills: '§3',
    from: `  if (index === 'loading') return ['pending', null]`,
    to: `  if (index === 'loading') return ['absent', null]`,
  },
  {
    // the other direction: no index at all becomes a verdict. On the canvas,
    // which holds no docket, this marks every real item unavailable.
    name: 'a surface with no index judges anyway',
    file: F, kills: '§1',
    from: `  if (index === undefined) return ['ready', null]`,
    to: `  if (index === undefined) return ['absent', null]`,
  },

  // ------------------------------------------- a failed ref must SAY so
  {
    // exactly what Astra rejected: a guaranteed-target ref that quietly
    // becomes ordinary text nobody can tell from a typo.
    name: 'an unresolved ref is dropped back to plain prose',
    file: F, kills: '§8 the rendered chip',
    from: `    out.push({ text: m[0], ref: resolveRef(parsed, world) })`,
    to: `    const rr = resolveRef(parsed, world)
    out.push(rr.outcome === 'ready' ? { text: m[0], ref: rr } : { text: m[0] })`,
  },
  {
    name: 'a failed chip shows only the bare id, not what was written',
    file: F, kills: '§8 the rendered chip',
    from: `      {r.outcome === 'ready' ? r.label : r.token}`,
    to: `      {r.label}`,
  },
  {
    name: 'every outcome renders under one class, so absent and foreign agree',
    file: F, kills: '§10',
    from: `  const cls = \`ref-chip ref-\${r.ref.kind} ref-\${r.outcome}\``,
    to: `  const cls = \`ref-chip ref-\${r.ref.kind} ref-ready\``,
  },
  {
    name: 'a non-ready ref is still a button, so a dead link looks live',
    file: F, kills: '§8 the rendered chip',
    from: `  if (r.outcome === 'ready' && onOpen) {`,
    to: `  if (onOpen) {`,
  },

  // ------------------------------------------------------ splitting contract
  // ⚠ WITHDRAWN MUTANT: "reuse the shared /g regex". It SURVIVED, and it was
  // right to. `exec` resets `lastIndex` to 0 when a scan runs out of matches,
  // so a loop that runs to exhaustion — which this one always does — is safe
  // on a shared regex. My source comment claimed otherwise and the harness
  // corrected me. The fresh copy stays (it isolates this function from any
  // other caller that stops iterating early) but it is not a mutation-verified
  // property, and §6b is now labelled a regression pin rather than a control.
  // Do not re-add this mutant without first making the hazard real.
  {
    name: 'the text between tokens is dropped, so splitting stops being lossless',
    file: F, kills: '§6 splitting is lossless',
    from: `    if (m.index > last) out.push({ text: s.slice(last, m.index) })`,
    to: ``,
  },
  // ⚠ AND A SECOND WITHDRAWN ONE: "swallow an unparseable token". It survived
  // because the branch it edits CANNOT RUN — `parseRef` anchors the same
  // pattern the scanner uses, so every match parses. Mutating dead code and
  // calling the survival a hole would have been the wrong lesson. What matters
  // is the INVARIANT that keeps it dead, so the mutant below breaks that
  // instead, in the file where drift would actually be introduced.
  {
    // a plausible build: someone inlines the pattern into parseRef rather than
    // deriving it from the scanner's, and quietly loses the bearer generation.
    // The scanner then finds `@agent:org/bot@2`, the parser refuses it, and
    // splitTypedRefs starts DROPPING that text with no visible symptom.
    name: 'parseRef drifts from the scanner and stops accepting bearer ids',
    file: REFS, kills: '§6c CONTROL',
    from: `  const m = new RegExp(\`^\${REF_TOKEN_RE.source}$\`).exec(String(token ?? ''))`,
    to: `  const P = \`@(?:(item|doc):(\${SEG}/\${SEG})|(agent):(\${SEG}/\${SEG})\`
    + \`|(mail):(\${SEG}/(?:user|org)/\${SEG}|\${SEG}/node/\${SEG}/\${SEG}))\`
  const m = new RegExp('^' + P + '$').exec(String(token ?? ''))`,
  },

  // -------------------------------- "cannot open here" is not "does not exist"
  {
    name: 'the panel restriction is ignored, so an unopenable kind is judged',
    file: F, kills: '§11 CONTROL',
    from: `  if (world.handles && !world.handles.has(ref.kind)) {`,
    to: `  if (false) {`,
  },
  {
    // the collapse itself: a real document reported as missing because THIS
    // panel has no reader for it.
    name: 'elsewhere is folded into absent',
    file: F, kills: '§11 CONTROL',
    from: `    return { ref, token, outcome: 'elsewhere', label: ref.id,
      why: \`this \${kind} is not opened from this panel\` }`,
    to: `    return { ref, token, outcome: 'absent', label: ref.id,
      why: \`no \${kind} named \${ref.id} in this org\` }`,
  },
  {
    name: 'the handles check moves after the index, so it reports pending first',
    file: F, kills: '§11b',
    from: `  if (world.handles && !world.handles.has(ref.kind)) {`,
    to: `  if (world.handles && !world.handles.has(ref.kind) && world.docs !== 'loading') {`,
  },

  // ------------------- an explicit token outranks a bare name that collides
  {
    // the bare matcher let loose over the WHOLE string, tokens included. It
    // finds the item name INSIDE `@agent:org/checklist-evidence` and cuts the
    // token in half, so the writer's explicit choice loses to a bare rule.
    name: 'the bare mention matcher also runs over the tokens',
    file: F, kills: '§12 CONTROL',
    from: `      {runs.map((p, i) => (p.ref
        ? <RefChip key={i} r={p.ref} onOpen={onOpen} />`,
    to: `      {runs.map((p, i) => (false
        ? <RefChip key={i} r={p.ref!} onOpen={onOpen} />`,
  },
  {
    // the other half of §12, so its control cannot pass vacuously: if bare
    // mentions stopped linking entirely, "the token was not eaten" would be
    // true for the boring reason.
    name: 'bare mentions stop linking, so §12 could pass for the wrong reason',
    file: F, kills: '§12 CONTROL',
    from: `        : (index && index.size
          ? <WorkRefText key={i} text={p.text} index={index} onPick={onPick}
              onFocusAgent={onFocusAgent} />
          : <span key={i}>{p.text}</span>)))}`,
    to: `        : <span key={i}>{p.text}</span>))}`,
  },

  // ---------------------------------------------------------------- labelling
  {
    name: 'the label is invented rather than taken from the index',
    file: F, kills: '§7',
    from: `    ref, token, outcome, label: label ?? ref.id,`,
    to: `    ref, token, outcome, label: ref.id,`,
  },
  {
    // §9 of the corrections allows a friendly label — it does NOT allow the
    // mail chip to leak what the mail says. A label built from anything the
    // resolver was handed would show through here.
    name: 'the mail chip labels itself with the message id',
    file: F, kills: '§5 a mailbox',
    from: `    return { ref, token, outcome, label: \`mail in \${where}\`,`,
    to: `    return { ref, token, outcome, label: \`subject \${ref.id}\`,`,
  },
  {
    name: 'the mail box predicate is ignored and every mailbox reads as open',
    file: F, kills: '§5 a mailbox',
    from: `    const outcome = world.mail ? world.mail(ref) : 'ready'`,
    to: `    const outcome: RefOutcome = 'ready'`,
  },

  // ------------------------------------------------ a node id is not a slug
  {
    // truncating at the @ addresses the LIVE agent instead of the bearer —
    // the wrong-target failure that looks like a success.
    name: 'the token is rebuilt without the bearer generation',
    file: F, kills: '§5b',
    from: `  if (ref.kind !== 'mail') return \`@\${ref.kind}:\${ref.org}/\${ref.id}\``,
    to: `  if (ref.kind !== 'mail') {
    return \`@\${ref.kind}:\${ref.org}/\${ref.id.split('@')[0]}\`
  }`,
  },
  {
    name: 'a node mailbox token loses its node segment',
    file: F, kills: '§5b',
    from: `    return \`@mail:\${ref.org}/node/\${ref.node}/\${ref.id}\``,
    to: `    return \`@mail:\${ref.org}/node/\${String(ref.node).split('@')[0]}/\${ref.id}\``,
  },
]

const norm = (s) => s.replace(/\r\n/g, '\n')

function runSuite() {
  try {
    execFileSync(process.execPath, ['tests/run.mjs', 'reflinks'],
      { stdio: 'pipe', encoding: 'utf8' })
    return { failed: false, out: '' }
  } catch (e) {
    return { failed: true, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
}

console.log('baseline — the reflinks suite must be GREEN before anything is mutated')
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
    // ⚠ A FUNCTION REPLACEMENT, NOT A STRING ONE. In a string replacement `$`
    // is special: `$\`` means "everything before the match" and `$&` means the
    // match itself, so a mutant whose text contains one SILENTLY SPLICES THE
    // WHOLE FILE INTO ITSELF. That is not a hypothetical — it happened here,
    // and the harness reported it as "WRONG CHECK" (the suite went red, but on
    // an esbuild syntax error rather than on the named check). A replacer
    // function disables every one of those.
    mutated = mutated.replace(from, () => norm(e.to))
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
