// Mutation harness for following a reference to a MESSAGE.
//
//   cd frontend && node mutate_mailjump.mjs
//
// Every failure this feature exists to prevent is a SILENT one — a panel that
// opens looking ordinary while the thing you clicked for is not on screen. A
// green suite is therefore worth very little on its own: the "before" state
// also renders a perfectly normal-looking panel. Each mutant below restores
// one of those silences and requires the named check to go red.
//
// ⚠ RUN INSIDE A WORKTREE ONLY. It rewrites sources in place and restores the
// exact BYTES in a finally block; the file is CRLF and the templates here are
// LF, so both sides are normalised for the search and CRLF is written back.
//
// ⚠ MUTANTS ARE APPLIED WITH A REPLACER FUNCTION, NOT A STRING. In a string
// replacement `$` is special — `$`+backtick means "everything before the
// match" — so a mutant containing one splices the whole file into itself and
// the harness then reports a syntax error as WRONG CHECK. Measured:
//   'AAA_TARGET_ZZZ'.replace('TARGET', 'x$`y')     -> 'AAA_xAAA_y_ZZZ'
//   'AAA_TARGET_ZZZ'.replace('TARGET', () => same) -> 'AAA_x$`y_ZZZ'

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const MAIL = 'src/canvas/mail.tsx'

const MUTANTS = [
  {
    // ⚠ EXECUTED BY ASTRA: a rejected promise and a null answer rendered the
    // same "retracted" sentence — a fact about the message stated from a
    // network error.
    name: 'a failed lookup is reported as a confirmed absence',
    file: MAIL, kills: 'a lookup that FAILS says so',
    from: `      .catch(() => {
        if (live) setAsk({ asking: false, row: null, failed: true })
      })`,
    to: `      .catch(() => {
        if (live) setAsk({ asking: false, row: null, failed: false })
      })`,
  },
  {
    // the reader is told the check failed and given no way to try again —
    // a dead end where the answer may simply be one click away
    name: 'the failed lookup offers no retry',
    file: MAIL, kills: 'a lookup that FAILS says so',
    from: `                    <button type="button" className="mailer-retry"
                      onClick={onAskRetry ?? (() => setRetry((n) => n + 1))}>
                      try again
                    </button>`,
    to: ``,
  },
  {
    // ⚠ ASTRA'S SECOND COUNTEREXAMPLE: the effect READS jumpSeq and did not
    // depend on it, so a repeat request never re-ran the folder decision. The
    // latch below it is irrelevant when the effect is never woken.
    name: 'the folder effect stops depending on the request it reads',
    file: MAIL, kills: '§11 a repeat request re-runs the folder decision',
    from: `  }, [jumpTo, jumpSeq, box, nodeLookup, askAgain])`,
    to: `  }, [jumpTo, box, nodeLookup, askAgain])`,
  },
  {
    // ⚠ ASTRA'S THIRD: the answer rendered where the reader happened to be,
    // so an incoming message wore an outgoing row's dress in the wrong folder.
    name: 'a found message does not open the folder that holds it',
    file: MAIL, kills: '§11c a reference to retained mail outside the window',
    from: `        if (m) setFolder('inbox')`,
    to: `        /* leave the folder where it is */`,
  },
  {
    // ⚠ ASTRA'S BLOCKER 6, restored: the pane never asks, so a retained message
    // outside the window is reported gone.
    name: 'the pane never asks for a message outside its window',
    file: MAIL, kills: 'a reference outside the window is looked up',
    from: `    if (!outsideWindow || !jumpTo || !lookup) return`,
    to: `    if (true) return`,
  },
  {
    // the found message is fetched and then not shown: the pane falls back to
    // its empty state, which reads as "you clicked nothing"
    name: 'a message found outside the window is not rendered',
    file: MAIL, kills: 'a reference outside the window is looked up',
    from: `  const cur = curPile?.[0] ?? (foundOutside ?? undefined)`,
    to: `  const cur = curPile?.[0]`,
  },
  {
    // the in-flight moment reads as a refusal — the pane says "gone" during
    // the one second somebody is actually looking at it
    name: 'the pane claims absence while it is still asking',
    file: MAIL, kills: 'while the question is in flight it says so',
    from: `  const lookingUp = outsideWindow
    && ((Boolean(lookup) && (!ask || ask.asking)) || askState === 'asking')`,
    to: `  const lookingUp = false`,
  },
  {
    // ⚠ THE WRONG-TARGET FAILURE, in the reading pane: a row left over from an
    // earlier lookup rendered as the message just asked for.
    name: 'a stale row is rendered whatever message it is',
    file: MAIL, kills: '§7f a lookup that answers with the wrong message',
    from: `  const foundOutside = ask && !ask.asking && ask.row
    && keyOf(ask.row) === jumpTo ? ask.row : null`,
    to: `  const foundOutside = ask && !ask.asking ? ask.row : null`,
  },
  {
    // THE ORIGINAL SILENCE. A reference that lands on nothing falls through
    // to the same invitation an untouched panel shows, so the reader concludes
    // they misclicked.
    name: 'a missing message falls back to "select a mail to read it"',
    file: MAIL, kills: '§3 a jump to a message',
    from: `                : jumpMissing
                  ? <span className="mailer-nojump">`,
    to: `                : false
                  ? <span className="mailer-nojump">`,
  },
  {
    // the notice rendered for EVERY empty pane, which would make §3 and §4
    // pass while the panel cried wolf on an ordinary unselected mailbox.
    name: 'the not-here notice shows whenever nothing is selected',
    file: MAIL, kills: '§7d CONTROL',
    from: `  const outsideWindow = Boolean(jumpTo)
    && !all.some((m) => keyOf(m) === jumpTo)`,
    to: `  const outsideWindow = true`,
  },
  {
    // asked over the FILTERED set: typing in the search box then makes the
    // panel announce that a message it is holding does not exist.
    name: 'the not-here question is asked over the filtered rows',
    file: MAIL, kills: '§6 CONTROL',
    from: `  const outsideWindow = Boolean(jumpTo)
    && !all.some((m) => keyOf(m) === jumpTo)`,
    to: `  const outsideWindow = Boolean(jumpTo)
    && !shownForJump().some((m) => keyOf(m) === jumpTo)`,
    // `shown` is declared below this point, so the mutant reaches it through a
    // closure rather than reordering the file — the change under test is WHICH
    // SET is consulted, not where the line sits.
    also: {
      from: `  const partyOf = (m: MailRow) => (outgoing ? m.to : m.from)`,
      to: `  const partyOf = (m: MailRow) => (outgoing ? m.to : m.from)
  function shownForJump() { return shown }`,
    },
  },
  {
    // THE SECOND-LINK SILENCE. Back to a once-ever latch: the first reference
    // of a session works and every one after it appears dead.
    name: 'the jump latch is a boolean again, so only the first link works',
    file: MAIL, kills: '§2 a SECOND jump',
    from: `  useEffect(() => {
    const key = jumpKey(jumpTo, jumpSeq)
    if (!jumpTo || key === jumpedRef.current) return
    jumpedRef.current = key
    setSelId(jumpTo)
  }, [jumpTo, jumpSeq])`,
    to: ``,
  },
  {
    // selecting the newest instead of the named one. A panel that always
    // opened the top of the list looks right nearly every time, because the
    // mail you just linked to is usually the newest.
    name: 'a jump opens the newest message rather than the named one',
    file: MAIL, kills: '§1 a jump selects',
    from: `  const [selId, setSelId] = useState<string | null>(jumpTo ?? null)`,
    to: `  const [selId, setSelId] = useState<string | null>(null)`,
  },
  {
    // the disclosure. §4 is the only check that can catch this, and it can
    // only be written by naming what must not appear.
    name: 'the not-here notice names the message it is refusing',
    file: MAIL, kills: '§4 CONTROL',
    from: `                      That message is not in this folder. It may have been
                      retracted, or it may not be one you can open.`,
    to: `                      Message {jumpTo} is not in this folder. It may have
                      been retracted, or it may not be one you can open.`,
  },
  // ───── the round Astra executed against 222088a: a folder that cannot ask
  {
    // ⚠ ASTRA'S FIRST BOUNDARY CASE. The panel asks on behalf of a list with
    // no `lookup`; swallowing the rejection leaves that list saying the only
    // thing it can know unaided, which is a claim about the message.
    name: 'the panel swallows its own failed question again',
    file: MAIL, kills: '§12 a rejected question, followed from Sent',
    from: `                askState={jumpAsk}`,
    to: ``,
  },
  {
    // the outcome reaches the list but the failure half is dropped: the
    // network error is once more rendered as "retracted".
    name: "the owner's FAILED question is not read as a failure",
    file: MAIL, kills: '§12 a rejected question, followed from Sent',
    from: `  const lookupFailed = outsideWindow && (Boolean(ask?.failed) || askState === 'failed')`,
    to: `  const lookupFailed = outsideWindow && Boolean(ask?.failed)`,
  },
  {
    // the in-flight half dropped: an unanswered question reads as an absence
    name: "the owner's OPEN question is not read as still open",
    file: MAIL, kills: '§12b an unanswered question',
    from: `    && ((Boolean(lookup) && (!ask || ask.asking)) || askState === 'asking')`,
    to: `    && (Boolean(lookup) && (!ask || ask.asking))`,
  },
  {
    // the retry re-asks the LIST's lookup, which this list does not have —
    // so the button is present, plausible and does nothing at all
    name: 'the retry does not re-ask the owner that actually asked',
    file: MAIL, kills: '§12 a rejected question, followed from Sent',
    from: `                      onClick={onAskRetry ?? (() => setRetry((n) => n + 1))}>`,
    to: `                      onClick={() => setRetry((n) => n + 1)}>`,
  },
  {
    // ⚠ ASTRA'S SECOND BOUNDARY CASE. The empty-window sentence back above
    // every jump outcome: a found message, an open question and a real
    // absence all replaced by a remark about the folder.
    name: 'an empty window answers an explicit jump with "no mail yet"',
    file: MAIL, kills: '§13 an explicit jump into an empty window',
    from: `  if (!all.length && !jumpTo) return <div className="dim pad">no mail yet</div>`,
    to: `  if (!all.length) return <div className="dim pad">no mail yet</div>`,
  },
  {
    // …and the overcorrection, which §13c exists to catch: an ordinary empty
    // folder nobody linked into loses its own sentence.
    name: 'an ordinary empty folder stops saying it is empty',
    file: MAIL, kills: '§13c CONTROL',
    from: `  if (!all.length && !jumpTo) return <div className="dim pad">no mail yet</div>`,
    to: ``,
  },
  {
    // ⚠ ASTRA'S READ-ONLY CONCERN, reproduced and now guarded. `box` is in the
    // deps and the poll replaces it every few seconds, so an effect-scoped
    // cancel throws away any answer slower than one tick — and the re-run
    // returns early on the latch, leaving the question dropped in silence.
    name: 'a repoll underneath the question throws the answer away',
    file: MAIL, kills: '§14 an answer that arrives after a repoll',
    from: `    setJumpAsk('asking')
    Promise.resolve(nodeLookup(jumpTo))`,
    to: `    setJumpAsk('asking')
    cancelOnRepoll = () => { askKey.current = null }
    Promise.resolve(nodeLookup(jumpTo))`,
    // the cancel is installed as the effect's cleanup, which is exactly what
    // the previous code did — the poll's next `box` then runs it
    also: {
      from: `  useEffect(() => {
    // a retry is a new attempt at the same request: it must pass the latch`,
      to: `  let cancelOnRepoll
  useEffect(() => {
    // a retry is a new attempt at the same request: it must pass the latch`,
    },
    then: {
      from: `  }, [jumpTo, jumpSeq, box, nodeLookup, askAgain])`,
      to: `    return () => cancelOnRepoll?.()
  }, [jumpTo, jumpSeq, box, nodeLookup, askAgain])`,
    },
  },
  {
    // ⚠ ASTRA'S SUPERSESSION CASE. The claim on the in-flight answer staked
    // AFTER the branches that handle a target already in a loaded list — so
    // such a target, which needs no question of its own, left the previous
    // question live to answer over the top of it.
    name: 'the claim is staked after the branches that return',
    file: MAIL, kills: '§15 an older question, answered late',
    from: `    askKey.current = req
    setJumpAsk(null)
    if (!jumpTo || !box) return`,
    to: `    setJumpAsk(null)
    if (!jumpTo || !box) return`,
    also: {
      from: `    setJumpAsk('asking')
    Promise.resolve(nodeLookup(jumpTo))`,
      to: `    askKey.current = req
    setJumpAsk('asking')
    Promise.resolve(nodeLookup(jumpTo))`,
    },
  },
  {
    // the overcorrection: invalidating on every effect RUN rather than on a
    // new REQUEST kills the answer to a question nothing superseded
    name: 'the claim is restaked on every run, so a repoll supersedes itself',
    file: MAIL, kills: '§11b CONTROL',
    from: `    if (foldedJump.current === req) return`,
    to: ``,
  },
  {
    // a failed or in-flight outcome outliving the request that produced it:
    // the notice is then attached to a message it was never about
    name: "a request's outcome outlives the request",
    file: MAIL, kills: '§15c an outcome belongs to the request',
    from: `    askKey.current = req
    setJumpAsk(null)`,
    to: `    askKey.current = req`,
  },
]

const norm = (s) => s.replace(/\r\n/g, '\n')

function runSuite() {
  try {
    execFileSync(process.execPath, ['tests/run.mjs', 'mailjump'],
      { stdio: 'pipe', encoding: 'utf8' })
    return { failed: false, out: '' }
  } catch (e) {
    return { failed: true, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
}

console.log('baseline — the mailjump suite must be GREEN before anything is mutated')
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
    mutated = mutated.replace(from, () => norm(e.to))
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
    writeFileSync(m.file, before)
    // ⚠ PROVE THE RESTORE. A child that dies with a FATAL heap error can
    // take this process with it, and a mutant left in the source then
    // reads as a broken feature hours later. Measured, once.
    if (!readFileSync(m.file).equals(before)) {
      console.error(`⚠ NOT RESTORED: ${m.file} — fix that before anything else`)
      process.exit(3)
    }     // exact bytes, CRLF and all
  }
}

console.log(`\n${MUTANTS.length - survived}/${MUTANTS.length} killed`)
process.exit(survived ? 1 : 0)
