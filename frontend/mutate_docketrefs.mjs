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
    from: `  for (const it of items) if (it?.slug) out.set(it.slug, { kind: 'item', slug: it.slug })`,
    to: `  for (const it of items) if (it?.slug != null) out.set(it.slug, { kind: 'item', slug: it.slug })`,
  },
  {
    name: 'mentions render as plain text — the renderer is never asked',
    file: REFS, kills: 'the sentence still reads',
    from: `  if (!render) return <>{text}</>`,
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
    // ⚠ THIS CALL SITE HAS NOW BEEN REPAIRED TWICE BY TWO PEOPLE, for two
    // different reasons — a rename and two added props — and each repair
    // alone leaves the mutant stale. If it ever reports SKIPPED, that is the
    // harness telling the truth about a drifted target: repair it against
    // the real call site, do not delete it.
    from: `      <DocketList heading="WORKING ON / NEXT" items={item.working_on_next}
        mark="next" refIndex={refIndex} onGoToItem={onGoToItem}
        onGoToAgent={goToAgent}
        refWorld={refWorld} onOpenRef={onOpenRef} />`,
    to: `      <DocketList heading="WORKING ON / NEXT" items={item.working_on_next}
        mark="done" refIndex={refIndex} onGoToItem={onGoToItem}
        onGoToAgent={goToAgent}
        refWorld={refWorld} onOpenRef={onOpenRef} />`,
  },
  {
    // the recovery pass for cycles is one word away from resurrecting every
    // row a fold was hiding — which is what the first version did
    name: 'a folded subtree reappears at the bottom instead of hiding',
    file: DOCKET, kills: 'folding hides a subtree',
    from: `  for (const it of items) if (!reachable.has(it.slug)) walk(it, 0)`,
    to: `  for (const it of items) if (!seen.has(it.slug)) walk(it, 0)`,
  },
  {
    name: 'selecting a child no longer opens its ancestors',
    file: DOCKET, kills: 'OPENS ITS ANCESTORS',
    from: `    const line = ancestorsOf([...allKnown.values()], id)`,
    to: `    const line: string[] = []`,
  },
  {
    name: 'the arrived-here mark never appears',
    file: DOCKET, kills: 'selects, reveals and marks',
    from: `    selected ? 'on' : '', flash ? 'docket-flash' : '',`,
    to: `    selected ? 'on' : '',`,
  },
  // ------------------------------- canonize-the-model-chip-and-clickable-agent-name
  // ------------------------------- the OWNER GROUP HEAD and AGENTS IN PROSE
  // (killed by docketname.test.tsx, which `run.mjs docket` also runs)
  {
    name: 'the agent group head goes back to being a plain word',
    file: DOCKET, kills: 'did not render an identity',
    from: `                            {s.agent
                              ? <GroupAgentHead agent={s.agent} items={s.items}
                                  facts={facts} onFocusAgent={onFocusAgent}
                                  close={close} />
                              : <span>{s.heading}</span>}`,
    to: `                            <span>{s.heading}</span>`,
  },
  {
    // THE PLAUSIBLE WRONG BUILD: the heading names an agent, so read its model
    // straight off the live tree. It is right until one group holds two
    // generations of that name.
    name: "the group head takes today's model for every generation",
    file: DOCKET, kills: 'attributed one model to two generations',
    from: `  const { tier, why } = groupIdentity(items, facts)`,
    to: `  const tier = facts.get(agent)?.tier
  const why = null`,
  },
  {
    name: 'a group with two answers keeps the first one instead of abstaining',
    file: DOCKET, kills: 'attributed one model to two generations',
    from: `  const only = seen.size === 1 ? [...seen.values()][0] : undefined`,
    to: `  const only = [...seen.values()][0]`,
  },
  {
    name: 'the owner-LESS group is treated as an agent named "Unassigned"',
    file: DOCKET, kills: 'drawn as if it were an agent',
    from: `      if (who !== UNASSIGNED) {
        out.push({ key: 'ag:' + who, heading: who, items, agent: who })
      }`,
    to: `      if (who !== UNASSIGNED) {
        out.push({ key: 'ag:' + who, heading: who, items, agent: who })
      }
      else out.push({ key: 'ag:x', heading: who, items, agent: who })`,
  },
  {
    name: 'an agent named in prose renders as text, never as a jump',
    file: REFS, kills: 'is an agent this org has',
    from: `        return onFocusAgent
          ? (`,
    to: `        return false
          ? (`,
  },
  {
    name: 'an agent wins a name the docket also serves as an item',
    file: REFS, kills: 'won a name the docket also has as an item',
    from: `  for (const [id, tier] of agents ?? []) {
    if (id) out.set(id, { kind: 'agent', id, tier })
  }
  for (const it of items) if (it?.slug) out.set(it.slug, { kind: 'item', slug: it.slug })`,
    to: `  for (const it of items) if (it?.slug) out.set(it.slug, { kind: 'item', slug: it.slug })
  for (const [id, tier] of agents ?? []) {
    if (id) out.set(id, { kind: 'agent', id, tier })
  }`,
  },
  {
    name: 'a mention loses the chip for the desk it goes to',
    file: REFS, kills: "does not wear the destination's current model",
    from: `              <AgentName id={ref.id} tier={ref.tier}`,
    to: `              <AgentName id={ref.id}`,
  },
  {
    // THE GUESS THE RULING FORBIDS: an agent whose current model is unknown
    // must wear no chip, not a plausible one
    name: 'an unknown current model is back-filled with a plausible one',
    file: REFS, kills: 'was given one anyway',
    from: `              <AgentName id={ref.id} tier={ref.tier}`,
    to: `              <AgentName id={ref.id} tier={ref.tier ?? 'opus'}`,
  },
  {
    name: 'the tier is looked up in the renderer instead of coming from facts',
    file: DOCKET, kills: 'was given one anyway',
    from: `      [...facts].map(([id, f]) => [id, f.tier] as const)),`,
    to: `      [...facts].map(([id]) => [id, 'opus'] as const)),`,
  },
  {
    name: 'the mention stops saying WHICH model claim its chip is making',
    file: REFS, kills: 'which model claim it is making',
    from: `                why={ref.tier
                  ? \`\${ref.id} — current model, \${ref.tier}. Go to its desk.\`
                  : \`\${ref.id} — current model not known. Go to its desk.\`}`,
    to: `                why={null}`,
  },
  {
    name: 'a jump from prose leaves the docket open over the desk',
    file: DOCKET, kills: 'stayed open over the desk it focused',
    from: `    ? (id: string) => { close(); onFocusAgent(id) }`,
    to: `    ? (id: string) => { onFocusAgent(id) }`,
  },
  {
    name: 'a jump from the group head leaves the docket open over the desk',
    file: DOCKET, kills: 'stayed open over the desk it focused',
    from: `          ? (id) => { close?.(); onFocusAgent(id) }
          : undefined} />
    </span>
  )
}

/** The item's readable name.`,
    to: `          ? (id) => { onFocusAgent(id) }
          : undefined} />
    </span>
  )
}

/** The item's readable name.`,
  },

  // ------------------------- the canonical-reference wiring (§25-§27)
  {
    name: 'the panel claims it can open every kind, including ones it cannot',
    file: DOCKET, kills: '§27 CONTROL',
    from: `    handles: new Set<'item' | 'agent'>(['item', 'agent']),`,
    to: `    handles: undefined,`,
  },
  {
    // the `?? new Map()` shape, in the place it would really be written: an
    // index that has not arrived, treated as one that arrived empty.
    name: 'the item index stops being authoritative in this panel',
    file: DOCKET, kills: '§25 a canonical',
    from: `    items: data
      ? new Map([...allKnown.keys()].map((s) => [s, s]))
      : 'loading',`,
    to: `    items: undefined,`,
  },
  {
    name: 'clicking a reference no longer selects the item it names',
    file: DOCKET, kills: '§25 a canonical',
    from: `    if (r.ref.kind === 'item') goToItem(r.ref.id)`,
    to: `    if (r.ref.kind === 'item') { /* no-op */ }`,
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
    // ⚠ A REPLACER FUNCTION, NOT A STRING. In String.replace a `$` in the
    // REPLACEMENT is special — `$&` is the match and ``$` `` is everything
    // before it — so a mutant whose text contains one silently splices the
    // file into itself. The suite then goes red on a syntax error hundreds of
    // lines from anything the mutant touched, and this harness reports
    // "WRONG CHECK", which is exactly the wrong diagnosis. Cost codex-checklist
    // most of an hour on 2026-09-05. A function replacement disables every `$`.
    // and the invariant that would have CAUGHT it, kept as a check rather
    // than as a comment: one replacement changes the length by exactly the
    // difference between the two texts.
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
