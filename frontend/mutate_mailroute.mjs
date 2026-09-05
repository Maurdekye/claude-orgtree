// Mutation harness for routing a `@mail:` reference to the panel that owns
// the mailbox.
//
//   cd frontend && node mutate_mailroute.mjs
//
// The whole path is three lines of translation and one effect, and EVERY way
// it can be wrong is quiet: the wrong panel opens, or none does, and neither
// says anything. A green suite proves little by itself — the pointer simply
// not arriving looks exactly like an app nobody clicked. Each mutant restores
// one of those silences and requires the named check to go red.
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

const REFLINKS = 'src/canvas/reflinks.tsx'
const CANVAS = 'src/canvas/OrgCanvas.tsx'
const MAIL = 'src/canvas/mail.tsx'
const APP = 'src/App.tsx'

const MUTANTS = [
  {
    // the mistake the translation exists to prevent: the router reads a
    // leading `@` as the org inbox and anything else as a node id
    name: 'the org box is handed over as the bare org name',
    file: REFLINKS, kills: '§2 CONTROL',
    from: `  if (ref.box === 'org') return { id: ref.id, to: \`@org:\${ref.org}\` }`,
    to: `  if (ref.box === 'org') return { id: ref.id, to: ref.org }`,
  },
  {
    name: 'the user box stops being the literal the router tests for',
    file: REFLINKS, kills: '§1 each box',
    from: `  if (ref.box === 'user') return { id: ref.id, to: 'user_inbox' }`,
    to: `  if (ref.box === 'user') return { id: ref.id, to: 'user' }`,
  },
  {
    // ⚠ and the one a "did a panel open?" assertion would miss: EVERY node
    // box routed to the same node. §3 opens `cto` and checks `ceo` did not.
    name: 'every node box opens the first agent instead of the one named',
    file: REFLINKS, kills: '§3 a NODE box',
    from: `  return { id: ref.id, to: String(ref.node ?? '') }`,
    to: `  return { id: ref.id, to: 'ceo' }`,
  },
  {
    name: 'the canvas ignores a pointer handed down to it',
    file: CANVAS, kills: '§3 a NODE box',
    from: `    openMailRef.current?.(openMailAt)
    onOpenMailHandled?.()`,
    to: `    onOpenMailHandled?.()`,
  },
  {
    // a pointer that is never marked handled sits in the shell's state and
    // re-opens the box every time anything re-renders
    name: 'the pointer is never reported as consumed',
    file: CANVAS, kills: '§3 a NODE box',
    from: `    openMailRef.current?.(openMailAt)
    onOpenMailHandled?.()`,
    to: `    openMailRef.current?.(openMailAt)`,
  },
  {
    name: 'the canvas ignores a document pointer',
    file: CANVAS, kills: '§5b a DOCUMENT pointer',
    from: `    setDocView(openDocAt)
    onOpenDocHandled?.()`,
    to: `    onOpenDocHandled?.()`,
  },
  {
    name: 'the reading pane stops deciding references in the body',
    file: MAIL, kills: '§7 a reference written INSIDE A MAIL',
    from: `                : <RefMdBody className="mailer-body md"
                    html={md(cur.body, mdBase?.(cur) || undefined)}
                    world={refs?.world} onOpen={refs?.onOpen} />}`,
    to: `                : <RefMdBody className="mailer-body md"
                    html={md(cur.body, mdBase?.(cur) || undefined)} />}`,
  },
  {
    // the inbox advertising a kind nobody wired up: a control that looks live
    // and drops the click
    name: 'the inbox claims it can open every kind of reference',
    file: REFLINKS, kills: '§7b CONTROL',
    from: `    const handles = new Set<RefKind>()
    if (onOpenItem) handles.add('item')`,
    to: `    const handles = new Set<RefKind>(['item', 'agent', 'doc', 'mail'])
    if (onOpenItem) handles.add('item')`,
  },
  {
    name: 'the inbox drops an item reference click',
    file: REFLINKS, kills: '§7 a reference written INSIDE A MAIL',
    from: `    if (r.ref.kind === 'item') onOpenItem?.(r.ref.id)`,
    to: `    if (r.ref.kind === 'item') { /* no-op */ }`,
  },
  {
    // the panel holds no item list; inventing an empty one reports every real
    // item mentioned in every mail as missing
    name: 'the inbox judges items against a list it does not have',
    file: REFLINKS, kills: '§7 a reference written INSIDE A MAIL',
    from: `      agents: agents
        ? new Map([...agents.keys()].map((id) => [id, id]))
        : 'loading',`,
    to: `      agents: agents
        ? new Map([...agents.keys()].map((id) => [id, id]))
        : 'loading',
      items: new Map(),`,
  },
  // ───── the user panel's own question (found by fable-verify against c7d267f)
  {
    // ⚠ THE ORIGINAL DEFECT. An effect-scoped cancel with the polled `box` in
    // the deps: the poll replaces `box`, the cleanup kills the in-flight
    // answer, and the re-run then returns early on the latch — so a question
    // outlived by one tick is dropped with nothing said and nothing re-asked.
    name: 'a poll tick throws away the user panel’s pending answer',
    file: APP, kills: '§8 the user panel',
    from: `    setJumpAsk('asking')
    Promise.resolve(userLookup(jumpTo))`,
    to: `    setJumpAsk('asking')
    cancelOnRepoll = () => { askKey.current = null }
    Promise.resolve(userLookup(jumpTo))`,
    also: {
      from: `  useEffect(() => {
    // a retry is a new attempt at the same request: it must pass the latch
    const req = jumpKey(jumpTo, jumpSeq) + '#' + askAgain`,
      to: `  let cancelOnRepoll
  useEffect(() => {
    // a retry is a new attempt at the same request: it must pass the latch
    const req = jumpKey(jumpTo, jumpSeq) + '#' + askAgain`,
    },
    then: {
      from: `  }, [jumpTo, jumpSeq, box, userLookup, askAgain])`,
      to: `    return () => cancelOnRepoll?.()
  }, [jumpTo, jumpSeq, box, userLookup, askAgain])`,
    },
  },
  {
    // the panel asks and never says so, leaving the one folder that cannot
    // ask for itself stating "not in this folder" about an open question
    name: 'the user panel does not report the question it is asking',
    file: APP, kills: '§8b the user panel',
    from: `                  askState={jumpAsk}`,
    to: ``,
  },
  {
    // present, plausible and inert: a retry wired to the LIST's own lookup,
    // which this folder does not have
    name: 'the user panel’s retry does not re-ask the panel',
    file: APP, kills: '§8c the user panel reports its FAILED question',
    from: `                  onAskRetry={() => setAskAgain((n) => n + 1)}`,
    to: ``,
  },
  {
    // the claim staked after the branches that RETURN, so a newer target the
    // box already holds never supersedes the question still in flight
    name: 'the user panel stakes its claim after the branches that return',
    file: APP, kills: '§8e an older question',
    from: `    askKey.current = req
    setJumpAsk(null)
    if (!jumpTo || !box) return`,
    to: `    setJumpAsk(null)
    if (!jumpTo || !box) return`,
    also: {
      from: `    setJumpAsk('asking')
    Promise.resolve(userLookup(jumpTo))`,
      to: `    askKey.current = req
    setJumpAsk('asking')
    Promise.resolve(userLookup(jumpTo))`,
    },
  },
]

// ⚠ NOT MUTATED, AND WORTH KNOWING WHY. §5's "a node this canvas does not
// have opens nothing" is held up by TWO guards in OrgCanvas — `map.has(m.to)`
// in the router and `inboxId && map.get(inboxId)` at the render — and
// removing either one alone leaves the other standing. Removing BOTH hands
// `node={undefined!}` to the modal, so the suite goes red on a crash rather
// than on the behaviour, which this harness cannot tell apart from a real
// kill. The check is real (it fails if the docket ever starts routing
// unknown nodes through here); the mutant would only be theatre.

const norm = (s) => s.replace(/\r\n/g, '\n')

function runSuite() {
  try {
    execFileSync(process.execPath, ['tests/run.mjs', 'mailroute'],
      { stdio: 'pipe', encoding: 'utf8' })
    return { failed: false, out: '' }
  } catch (e) {
    return { failed: true, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
}

console.log('baseline — the mailroute suite must be GREEN before anything is mutated')
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
  const edits = [{ from: m.from, to: m.to },
    ...(m.also ? [m.also] : []), ...(m.then ? [m.then] : [])]
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
