// Mutation harness for the sender's model chip and click-to-desk on the mail
// LIST ROW and the INLINE TRANSCRIPT.
//
//   cd frontend && node mutate_mailsender.mjs
//
// A green suite is evidence about the harness until you show the harness could
// have gone red. Each mutant below breaks the feature ONE way — every one of
// them a way this could plausibly have been written — and the named check must
// be among the failures. "Red, but not the check I named" is reported as WRONG
// CHECK and counts as a survivor, because a suite that goes red for the wrong
// reason is not measuring what its name says.
//
// ⚠ RUN INSIDE A WORKTREE ONLY. It rewrites sources in place and restores the
// exact BYTES in a finally block — these files are CRLF and the templates here
// are LF, so both sides are normalised for the search and CRLF is written back.
//
// ⚠ THE '$' TRAP, fixed once in this repo already (836fe49) and re-stated here
// because every copy of this harness has to carry it: in String.replace a `$`
// in the REPLACEMENT is special, so a mutant containing one splices the file
// into itself and the suite goes red on a syntax error far from the mutant —
// reported as WRONG CHECK, which is the wrong diagnosis. The replacement is a
// FUNCTION, and a length invariant catches it if anyone changes that back.

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const MAIL = 'src/canvas/mail.tsx'
const DESK = 'src/canvas/desk.tsx'
const IDENT = 'src/canvas/identity.tsx'
const APP = 'src/App.tsx'

const MUTANTS = [
  // ───────────────────────────────────────────────── the list row (§3, §4)
  {
    name: 'the list row goes back to printing the raw name',
    file: MAIL, kills: 'the row names the sender with its model',
    from: `                {outgoing ? '→ ' : ''}{R(party(m)!, m)}`,
    to: `                {outgoing ? '→ ' : ''}{party(m)}`,
  },
  {
    name: 'the row renderer stops obeying the call site and re-decides from '
      + 'the id (the org-inbox over-resolution)',
    // ⚠ §5.2 (the org-inbox mount) does NOT catch this and a first draft of
    // this harness claimed it did: that modal hands its lists no `tierOf` and
    // no `onFocusAgent`, so the built-in renderer draws nothing there either
    // and the test stayed green. §5.3 supplies both on purpose and is the
    // check that can see the difference.
    file: MAIL, kills: 'the declaration wins: no chip in the row',
    from: `  const R: (id: string, m: MailRow) => ReactNode = rowSender ?? S`,
    to: `  const R: (id: string, m: MailRow) => ReactNode = rowSender ?? defaultIdentity`,
  },
  {
    name: 'the org inbox stops declaring its Sent row and the three-identity '
      + 'attribution line lands in the row',
    file: MAIL, kills: 'NOT the whole attribution line',
    from: `                    rowSender={(id) => <b>{id}</b>}\n`,
    to: ``,
  },
  {
    name: 'the sentinel test is dropped — @user/@system become agents',
    file: MAIL, kills: 'a sentinel is not an agent',
    from: `    if (!isAgentId(id)) return <span>{id}</span>`,
    to: `    if (false) return <span>{id}</span>`,
  },
  {
    name: 'AgentName stops stopping the bubble, so a jump also selects',
    file: IDENT, kills: 'the bubble was stopped',
    from: `        onClick={(e) => { e.stopPropagation(); onFocus(id, e) }}>`,
    to: `        onClick={(e) => { onFocus(id, e) }}>`,
  },
  {
    name: 'SenderChip stops stopping the bubble (the user inbox composes it '
      + 'into the row)',
    file: APP, kills: 'SenderChip stops the bubble too',
    from: `        onClick={(e) => { e.stopPropagation(); onFocusAgent(id) }}>`,
    to: `        onClick={() => { onFocusAgent(id) }}>`,
  },

  // ─────────────────────────────────────── the transcript's wiring (§1, §2)
  {
    name: 'THE PROVIDER IS NEVER MOUNTED — the card is correct and is handed '
      + 'nothing, which is what the whole of §1 exists to catch',
    file: DESK, kills: 'exactly one model chip in the mail header',
    from: `    <AgentDirectoryProvider value={agentDir}>`,
    to: `    <>`,
    also: { from: `    </AgentDirectoryProvider>\n  )`, to: `    </>\n  )` },
  },
  {
    name: 'the directory answers for every id instead of consulting the tree',
    file: DESK, kills: 'no model chip for a name this tree cannot vouch for',
    from: `    resolve: (id: string) => mapRef.current.get(id),`,
    to: `    resolve: () => ({ tier: 'sonnet' }),`,
  },
  {
    name: 'the directory hands out a focus callback even where there is '
      + 'nowhere to go — a button that runs an empty function',
    file: DESK, kills: 'an inert control is worse than no control',
    from: `    onFocus: canJump ? (id: string) => jumpRef.current?.(id) : undefined,`,
    to: `    onFocus: (id: string) => jumpRef.current?.(id),`,
  },
  {
    name: "the card drops the '@' clause and a sentinel becomes an agent",
    file: DESK, kills: 'draws no model chip',
    from: `  const agent = mail.from && !mail.from.startsWith('@')`,
    to: `  const agent = mail.from`,
  },
  {
    name: 'the card offers a jump even when this surface IS the destination',
    file: DESK, kills: 'the destination IS this surface',
    from: `              why={chipWhy} atDestination={atDest}`,
    to: `              why={chipWhy}`,
  },
  {
    name: 'the card renders a jump button whether or not the directory can '
      + 'navigate',
    file: DESK, kills: 'but nothing claims to navigate',
    from: `              onFocus={dir?.onFocus ? (id) => dir.onFocus!(id) : undefined} />`,
    to: `              onFocus={(id) => dir?.onFocus?.(id)} />`,
  },
  {
    name: 'the chip stops scoping its claim to the CURRENT model, so a '
      + 'historical mail reads as attributed',
    file: DESK, kills: 'the tooltip scopes the claim to the current model',
    from: `              why={chipWhy} atDestination={atDest}`,
    to: `              atDestination={atDest}`,
  },
  // ──────────────────────────── the archived transcript reader (§7)
  {
    name: 'the lineage panel stops providing a directory, so an archived '
      + 'generation is the one place a mail card is still a bare name',
    file: DESK, kills: "with the sender's model chip",
    from: `                      <AgentDirectoryProvider value={lineageDir}>`,
    to: `                      <>`,
    also: { from: `                      </AgentDirectoryProvider>`, to: `                      </>` },
  },
  {
    name: 'the lineage panel navigates WITHOUT closing itself, so the camera '
      + 'glides to a desk sitting behind the overlay',
    file: DESK, kills: 'and closed the panel first',
    from: `      ? (id: string) => { closeRef.current(); focusRef.current?.(id) }`,
    to: `      ? (id: string) => { focusRef.current?.(id) }`,
  },
  {
    name: "an unknown current model is back-filled from the desk's own tier",
    file: DESK, kills: 'an unknown model is an answer, never back-filled',
    from: `          ? <AgentName id={mail.from} tier={agent.tier} nameClass="turn-mail-from"`,
    to: `          ? <AgentName id={mail.from} tier={agent.tier ?? 'opus'} nameClass="turn-mail-from"`,
  },
]

const norm = (s) => s.replace(/\r\n/g, '\n')

function runSuite() {
  try {
    execFileSync(process.execPath, ['tests/run.mjs', 'mailsender'],
      { stdio: 'pipe', encoding: 'utf8' })
    return { failed: false, out: '' }
  } catch (e) {
    return { failed: true, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
}

console.log('baseline — mailsender must be GREEN before anything is mutated')
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
      console.error(`  in ${m.file} — the harness is STALE, which is not a pass`)
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
