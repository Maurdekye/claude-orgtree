// Mutation harness for canonical references in the DESK CHAT.
//
//   cd frontend && node mutate_deskrefs.mjs
//
// Every failure this wiring can have is a QUIET one. Delete any of it and the
// desk still renders a perfectly ordinary transcript — the reference is simply
// text again, which is indistinguishable from prose that never carried one. A
// green suite proves nothing on its own, because the BEFORE state is green
// too. Each mutant below restores one of those silences and requires the named
// check to go red.
//
// ⚠ RUN INSIDE A WORKTREE ONLY. It rewrites sources in place and restores the
// exact BYTES in a finally block; the files are CRLF and the templates here
// are LF, so both sides are normalised for the search and CRLF is written
// back.
//
// ⚠ MUTANTS ARE APPLIED WITH A REPLACER FUNCTION, NOT A STRING — in a string
// replacement `$` is special, so a mutant containing one splices the file into
// itself and the harness reports a syntax error as WRONG CHECK.

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const DESK = 'src/canvas/desk.tsx'
const LINKS = 'src/canvas/reflinks.tsx'

const MUTANTS = [
  {
    // THE WHOLE FEATURE, OFF. The plainest silence there is: the transcript
    // renders, the prose is all there, and the reference is dead text.
    name: 'the settled reply renders as plain markdown again',
    file: DESK, kills: '§1 a reference the agent wrote',
    from: `      {text && <RefMdBody className="msgtext md" world={refs?.world}
        onOpen={refs?.onOpen} html={md(text, fb)} />}`,
    to: `      {text && <div className="msgtext md" dangerouslySetInnerHTML={md(text, fb)} />}`,
  },
  {
    // THE WIRED-TO-NOTHING FAILURE, and the one a hand-built test cannot see.
    // The component still renders RefMdBody; it is simply never handed a
    // world, so every chip silently becomes plain text.
    name: 'the desk mounts Msg without its refs — present, plausible, inert',
    file: DESK, kills: '§1 a reference the agent wrote',
    from: `                  onWorkLink={onWorkLink} refs={deskRefs} />`,
    to: `                  onWorkLink={onWorkLink} />`,
  },
  {
    // A MAIL CARD IS A DIFFERENT CALL SITE from the reply, and the two have
    // been wired separately since the beginning. Losing one is invisible while
    // the other works.
    name: 'a mail card in the transcript loses its refs',
    file: DESK, kills: '§3 a reference inside a mail card',
    from: `          <TurnMailCard key={\`\${mail.at}-\${i}\`} mail={mail} slug={slug} nid={nid}
            refs={refs} />)}`,
    to: `          <TurnMailCard key={\`\${mail.at}-\${i}\`} mail={mail} slug={slug} nid={nid} />)}`,
  },
  {
    // THE LIVE ROW is drawn by different code from the settled row it becomes,
    // so deleting this one leaves references dead for the whole time a turn is
    // actually being watched and green the moment it finishes.
    name: 'streaming text renders as plain markdown again',
    file: DESK, kills: '§1b the text a turn is still streaming',
    from: `                      <RefMdBody className="md" world={deskRefs.world}
                        onOpen={deskRefs.onOpen}
                        html={md(f.text, fileBase(slug, node.id))} />`,
    to: `                      <div className="md" dangerouslySetInnerHTML={md(f.text, fileBase(slug, node.id))} />`,
  },
  {
    // the DURABLE pending row — the ghost's twin, and the reason both have a
    // mutant: one check covering one of them looked like coverage of both.
    name: 'the durable pending bubble renders as plain markdown again',
    file: DESK, kills: '§8 the reader\'s own undelivered message',
    from: `        {m.body && <RefMdBody className="msgtext md"
          world={deskRefs.world} onOpen={deskRefs.onOpen}
          html={md(m.body, fileBase(slug, node.id))} />}`,
    to: `        {m.body && <div className="msgtext md"
          dangerouslySetInnerHTML={md(m.body, fileBase(slug, node.id))} />}`,
  },
  {
    // THE GHOST BUBBLE — a SECOND call site, and this mutant is why §8b
    // exists. It survived a full run against §8 alone: the durable pending row
    // and the optimistic ghost are drawn by different code, so a check on one
    // leaves the other free to rot while the suite stays green.
    name: 'the undelivered GHOST renders as plain markdown again',
    file: DESK, kills: '§8b …and so does the optimistic ghost',
    from: `              <RefMdBody className="pendbody"
                world={deskRefs.world} onOpen={deskRefs.onOpen}
                html={md(p.text, fileBase(slug, node.id))} />`,
    to: `              <div className="pendbody"
                dangerouslySetInnerHTML={md(p.text, fileBase(slug, node.id))} />`,
  },
  {
    // ⚠ THE REGRESSION ASTRA FOUND, reintroduced here. This type-checks, it
    // renders, it navigates — and it hands the click event to `centerOn` as
    // the zoom, because `focusView` reads `z ?? fit` and an event is not null.
    // §4 is the only check in this repo that can see it from the desk side.
    name: 'the agent route is handed over bare, so a second argument rides along',
    file: DESK, kills: '§4 every route is called with EXACTLY ONE argument',
    from: `    onFocusAgent: jumpRef.current
      ? (id: string) => jumpRef.current!(id) : undefined,`,
    to: `    onFocusAgent: jumpRef.current
      ? (...a: unknown[]) => (jumpRef.current as (...x: unknown[]) => void)(...a, {})
      : undefined,`,
  },
  {
    // the same class, on the route whose argument is an OBJECT — a second
    // argument here is even easier to miss, because the first one is already
    // a shape rather than a string.
    name: 'the mail route forwards everything it was given',
    file: DESK, kills: '§6 a mail reference reaches the router',
    from: `    onOpenMail: mailLinkRef.current
      ? (r: TypedRef) => mailLinkRef.current!(mailRefTarget(r)) : undefined,`,
    to: `    onOpenMail: mailLinkRef.current
      ? (r: TypedRef) => mailLinkRef.current!(r as unknown as { id: string }) : undefined,`,
  },
  {
    // A ROUTE STUBBED RATHER THAN OMITTED. `handles` then claims the desk can
    // open a docket item it cannot, and the chip becomes a live button that
    // eats the click — the dead control the outcome table exists to prevent.
    name: 'an absent route is stubbed, so the chip lies about what it can do',
    file: DESK, kills: '§2 CONTROL',
    from: `    onOpenItem: workRef.current
      ? (s: string) => workRef.current!({ slug: s }) : undefined,`,
    to: `    onOpenItem: (s: string) => workRef.current?.({ slug: s }),`,
  },
  {
    // THE VACUOUS-PASS GUARD. If the desk judged agents against `undefined`
    // instead of the map it holds, every name would resolve `ready` and a
    // reference to somebody who does not exist would render as a live button.
    name: 'the desk judges no agent index, so any name resolves',
    file: DESK, kills: '§5 CONTROL',
    from: `  const deskRefs = useRefRoutes(slug, agentIndex, deskRoutes)`,
    to: `  const deskRefs = useRefRoutes(slug, null, deskRoutes)`,
  },
  {
    // THE LATCH. Removing it is invisible in every functional check — the
    // feature works exactly as well — and it re-renders and re-`md()`s the
    // whole transcript on every keystroke in the composer.
    name: 'the refs value is rebuilt every render, so the transcript re-renders',
    file: LINKS, kills: '§9 the refs value keeps its identity',
    from: `  return useMemo(() => ({ world, onOpen }), [world, onOpen])`,
    to: `  return { world, onOpen }`,
  },
  {
    // the same claim from the other side: a world rebuilt on every render
    // defeats the latch even with the outer memo in place.
    name: 'the world is rebuilt every render',
    file: LINKS, kills: '§9 the refs value keeps its identity',
    from: `  }, [org, agents, onOpenItem, onFocusAgent, onOpenDoc, onOpenMail])`,
    to: `  }, [org, agents, onOpenItem, onFocusAgent, onOpenDoc, onOpenMail, Math.random()])`,
  },
  {
    // AND THE CONTROL'S OWN CONTROL. If the world never changed at all, §9's
    // first half would pass for free — a latch that is simply frozen is not a
    // latch, it is a stale answer.
    name: 'the world ignores who exists, so it never changes',
    file: LINKS, kills: '§9 the refs value keeps its identity',
    from: `  }, [org, agents, onOpenItem, onFocusAgent, onOpenDoc, onOpenMail])`,
    to: `  }, [org, onOpenItem, onFocusAgent, onOpenDoc, onOpenMail])`,
  },
]

const norm = (s) => s.replace(/\r\n/g, '\n')

function runSuite() {
  try {
    execFileSync(process.execPath, ['tests/run.mjs', 'deskrefs'],
      { stdio: 'pipe', encoding: 'utf8' })
    return { failed: false, out: '' }
  } catch (e) {
    return { failed: true, out: String(e.stdout ?? '') + String(e.stderr ?? '') }
  }
}

console.log('baseline — the deskrefs suite must be GREEN before anything is mutated')
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
