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
    // THE ORIGINAL SILENCE. A reference that lands on nothing falls through
    // to the same invitation an untouched panel shows, so the reader concludes
    // they misclicked.
    name: 'a missing message falls back to "select a mail to read it"',
    file: MAIL, kills: '§3 a jump to a message',
    from: `            {jumpMissing
              ? <span className="mailer-nojump">`,
    to: `            {false
              ? <span className="mailer-nojump">`,
  },
  {
    // the notice rendered for EVERY empty pane, which would make §3 and §4
    // pass while the panel cried wolf on an ordinary unselected mailbox.
    name: 'the not-here notice shows whenever nothing is selected',
    file: MAIL, kills: '§5 CONTROL',
    from: `  const jumpMissing = Boolean(jumpTo) && !all.some((m) => keyOf(m) === jumpTo)`,
    to: `  const jumpMissing = true`,
  },
  {
    // asked over the FILTERED set: typing in the search box then makes the
    // panel announce that a message it is holding does not exist.
    name: 'the not-here question is asked over the filtered rows',
    file: MAIL, kills: '§6 CONTROL',
    from: `  const jumpMissing = Boolean(jumpTo) && !all.some((m) => keyOf(m) === jumpTo)`,
    to: `  const jumpMissing = Boolean(jumpTo) && !shownForJump().some((m) => keyOf(m) === jumpTo)`,
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
    if (!jumpTo || jumpTo === jumpedRef.current) return
    jumpedRef.current = jumpTo
    setSelId(jumpTo)
  }, [jumpTo])`,
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
    from: `                  That message is not in this folder. It may have been
                  retracted, or it may not be one you can open.`,
    to: `                  Message {jumpTo} is not in this folder. It may have been
                  retracted, or it may not be one you can open.`,
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
