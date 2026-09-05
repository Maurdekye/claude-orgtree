// clickable-docket-references-across-text-surfaces — a canonical reference
// decided: ready, pending, absent, foreign or elsewhere.
//
// THE DEFECT THIS EXISTS TO CATCH IS NOT "the link does not work". It is a
// reference that reports the WRONG ONE OF THE FIVE — most of all the three
// that look alike from the outside:
//
//   · `foreign` silently resolved as local. Two orgs can hold the same item
//     slug, so this does not fail visibly. It opens a different, unrelated
//     object and looks like it worked. Nothing downstream can detect it.
//   · `pending` reported as `absent`. "Not loaded yet" rendered as "does not
//     exist" is a false statement that appears exactly while the page is
//     loading, which is when it will be read.
//   · `elsewhere` reported as `absent`. "This panel has no reader for it" and
//     "it does not exist" are the same picture unless you keep them apart —
//     and the second is a claim about the DATA made because of a limit of the
//     PANEL.
//
// Both are ASSERTED ON THE OUTCOME, not on whether a chip happens to be
// clickable — `foreign` and `absent` are both inert, so an is-it-a-button
// check would pass with the two swapped.
//
// Run: cd frontend && node tests/run.mjs reflinks

import { flush, inAct, mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { RefChip, RefProse, TypedRefText, refToken, resolveRef, splitTypedRefs }
  from '../src/canvas/reflinks'
import type { RefWorld } from '../src/canvas/reflinks'
import { REF_TOKEN_RE, buildMentionIndex, parseRef }
  from '../src/canvas/workrefs'
import type { WorkItem } from '../src/types'

const HERE = 'orgtree'
const world = (o: Partial<RefWorld> = {}): RefWorld => ({ org: HERE, ...o })

/** decide one token end to end, the way a surface does */
const decide = (token: string, w: RefWorld) => {
  const p = parseRef(token)
  assert.ok(p, `fixture token does not parse: ${token}`)
  return resolveRef(p!, w)
}

// ------------------------------------------------------------- the four outcomes

test('§1 with no authoritative index the ref is ready and the destination '
  + 'adjudicates', () => {
  // a surface that holds no list of items must NOT invent a verdict. The
  // canvas has no docket loaded; the docket does. Judging from absence of
  // knowledge would mark every real item "unavailable" on the canvas.
  const r = decide(`@item:${HERE}/git-review-workspace`, world())
  assert.equal(r.outcome, 'ready')
  assert.equal(r.label, 'git-review-workspace')
})

test('§2 an index that has arrived and lacks the target says ABSENT, in words',
  () => {
    const r = decide(`@item:${HERE}/no-such-item`,
      world({ items: new Map([['git-review-workspace', 'git-review-workspace']]) }))
    assert.equal(r.outcome, 'absent')
    // ⚠ the WORDS matter: this is the case Astra rejected being rendered as
    // ordinary prose. It has to state that the target is not here.
    assert.match(r.why, /no docket item named no-such-item in this org/)
  })

test('§3 an index still loading says PENDING, and pending is not absent', () => {
  const r = decide(`@item:${HERE}/git-review-workspace`,
    world({ items: 'loading' }))
  assert.equal(r.outcome, 'pending')
  assert.doesNotMatch(r.why, /no docket item/)
  // CONTROL: the SAME token against an arrived-and-empty index must differ.
  // If these two ever agree, the loading/absent distinction has collapsed —
  // and an empty Map is the shape a naive "index ?? new Map()" produces.
  const arrived = decide(`@item:${HERE}/git-review-workspace`,
    world({ items: new Map() }))
  assert.equal(arrived.outcome, 'absent')
  assert.notEqual(r.outcome, arrived.outcome)
})

test("§4 CONTROL — the same slug in two orgs: the foreign one is never "
  + 'resolved against what is on screen', () => {
    const items = new Map([['git-review-workspace', 'git-review-workspace']])
    // present locally, and named by BOTH tokens. The only difference is the
    // org segment, which is the whole point.
    const mine = decide(`@item:${HERE}/git-review-workspace`, world({ items }))
    const theirs = decide('@item:resonite/git-review-workspace', world({ items }))
    assert.equal(mine.outcome, 'ready')
    assert.equal(theirs.outcome, 'foreign')
    // it must not merely be inert — it must say WHOSE it is
    assert.match(theirs.why, /belongs to the org “resonite”/)
  })

test('§4b the org check runs BEFORE the index, so a foreign ref is foreign '
  + 'even when the local index is still loading', () => {
    // ordering control: judged index-first, this would report `pending` and
    // then, once the index arrived, quietly become `ready` — the silent
    // cross-org resolution, arriving late.
    const r = decide('@item:resonite/git-review-workspace',
      world({ items: 'loading' }))
    assert.equal(r.outcome, 'foreign')
  })

// ----------------------------------------------------------------------- mail

test('§5 a mailbox this side cannot address is unavailable, and names no '
  + 'subject or body', () => {
    // §2 of the corrections: no silent mailbox open, and an explicit
    // unavailable outcome that discloses NOTHING about the message.
    const w = world({ mail: (r) => (r.box === 'node' && r.node === 'ghost'
      ? 'absent' : 'ready') })
    const ok = decide(`@mail:${HERE}/node/codex-checklist/m1`, w)
    const no = decide(`@mail:${HERE}/node/ghost/m1`, w)
    assert.equal(ok.outcome, 'ready')
    assert.equal(no.outcome, 'absent')
    for (const r of [ok, no]) {
      assert.doesNotMatch(r.label + ' ' + r.why, /subject|body/i)
      // the id is the address, not content — but it must not be presented as
      // a readable label either
      assert.match(r.label, /^mail in /)
    }
  })

test('§5b a bearer generation survives the round trip — a node id is not a '
  + 'slug', () => {
    // truncating at the @ would address the LIVE agent instead of the bearer:
    // a wrong-target failure that looks like a success.
    const r = decide(`@agent:${HERE}/codex-checklist@4`, world())
    assert.equal(r.ref.id, 'codex-checklist@4')
    assert.equal(refToken(r.ref), `@agent:${HERE}/codex-checklist@4`)
    const m = decide(`@mail:${HERE}/node/codex-checklist@4/m1`, world())
    assert.equal(m.ref.node, 'codex-checklist@4')
    assert.equal(refToken(m.ref), `@mail:${HERE}/node/codex-checklist@4/m1`)
  })

// ------------------------------------------------------------------ splitting

test('§6 splitting is lossless and a token that does not parse stays text',
  () => {
    const text = `see @item:${HERE}/alpha and mail me at bob@example.com, `
      + 'also @item:not a token'
    const runs = splitTypedRefs(text, world())
    assert.equal(runs.map((r) => r.text).join(''), text,
      'concatenating the runs must reproduce the input exactly')
    const linked = runs.filter((r) => r.ref)
    assert.equal(linked.length, 1)
    assert.equal(linked[0]!.text, `@item:${HERE}/alpha`)
  })

test('§6b repeated splits of the same text give the same answer', () => {
  // A REGRESSION PIN, NOT A MUTATION-VERIFIED CONTROL, and labelled as one.
  // I wrote this believing a shared /g regex would drop tokens on the second
  // render; the harness survived that mutant, because `exec` resets
  // `lastIndex` when a scan runs out of matches. The check still earns its
  // place — it would catch statefulness introduced later — but nothing in the
  // code today can make it fail, and calling it a control would be the
  // vacuous pass this suite exists to avoid.
  const text = `@item:${HERE}/a and @doc:${HERE}/d1 and @agent:${HERE}/bot`
  const once = splitTypedRefs(text, world()).filter((r) => r.ref).length
  const twice = splitTypedRefs(text, world()).filter((r) => r.ref).length
  const thrice = splitTypedRefs(text, world()).filter((r) => r.ref).length
  assert.equal(once, 3)
  assert.deepEqual([twice, thrice], [3, 3])
})

test('§6c CONTROL — every token the scanner finds also PARSES, which is what '
  + 'keeps the non-parse branch dead', () => {
  // `splitTypedRefs` has an `if (!parsed) continue` that cannot fire, because
  // `parseRef` anchors the same pattern the scanner uses. That is fine — it is
  // the narrowing TypeScript wants — but only while the two stay identical.
  // Let them drift and the branch wakes up and starts SWALLOWING TEXT, which
  // is invisible: prose simply comes out shorter than it went in.
  //
  // So the invariant is checked here rather than the dead branch being
  // "tested" somewhere it can never run. The bearer generation is the segment
  // most likely to be dropped from one side and not the other.
  const probes = [
    `@item:${HERE}/a-b-c`,
    `@doc:${HERE}/d1.`,
    `@agent:${HERE}/codex-checklist@4`,
    `@mail:${HERE}/user/m1`,
    `@mail:${HERE}/org/m2`,
    `@mail:${HERE}/node/codex-checklist@4/m3`,
    `see @agent:${HERE}/bot@2@item:${HERE}/c then stop`,
  ]
  let found = 0
  for (const p of probes) {
    for (const m of p.matchAll(new RegExp(REF_TOKEN_RE.source, 'g'))) {
      found++
      assert.ok(parseRef(m[0]),
        `the scanner found ${m[0]} but parseRef refuses it — the patterns have `
        + 'drifted and splitTypedRefs will now drop that text silently')
    }
  }
  // ⚠ the loop above passes trivially if nothing matched at all
  assert.ok(found >= probes.length, `expected a match per probe, got ${found}`)
})

test('§7 a trustworthy label replaces the id, and only when we have one',
  () => {
    const docs = new Map([['d1', 'The migration contract']])
    assert.equal(decide(`@doc:${HERE}/d1`, world({ docs })).label,
      'The migration contract')
    // no index, no label to borrow — the id, never something invented
    assert.equal(decide(`@doc:${HERE}/d1`, world()).label, 'd1')
  })

// -------------------------------------------------------------------- rendered

test('§8 the rendered chip: ready is a button, the other three are inert and '
  + 'show the whole token', async () => {
    const opened: string[] = []
    const items = new Map([['alpha', 'alpha']])
    const view = await mountView(
      <TypedRefText world={world({ items })} onOpen={(r) => opened.push(r.token)}
        text={`ready @item:${HERE}/alpha absent @item:${HERE}/beta `
          + 'foreign @item:resonite/alpha'} />, (el) => el)
    await flush()
    const btns = view.el.querySelectorAll('button.ref-chip')
    assert.equal(btns.length, 1, 'only the resolvable one is clickable')
    assert.equal(btns[0]!.textContent, 'alpha')

    const absent = view.el.querySelector('.ref-chip.ref-absent')
    const foreign = view.el.querySelector('.ref-chip.ref-foreign')
    assert.ok(absent && foreign, 'both failures are rendered, not dropped')
    // ⚠ the FAILED chips show the literal token — whoever fixes the reference
    // needs to see what was written, and on a foreign ref the org segment IS
    // the explanation.
    assert.match(absent!.textContent!, new RegExp(`@item:${HERE}/beta`))
    assert.match(foreign!.textContent!, /@item:resonite\/alpha/)
    assert.ok(absent!.tagName !== 'BUTTON' && foreign!.tagName !== 'BUTTON')

    await inAct(() => (btns[0] as HTMLElement).click())
    assert.deepEqual(opened, [`@item:${HERE}/alpha`])
    await view.unmount()
  })

test('§8b CONTROL — with no onOpen nothing is clickable, and the prose is '
  + 'still all there', async () => {
    const view = await mountView(
      <TypedRefText world={world()} text={`before @item:${HERE}/alpha after`} />,
      (el) => el)
    await flush()
    assert.equal(view.el.querySelectorAll('button.ref-chip').length, 0)
    assert.match(view.el.textContent!, /before/)
    assert.match(view.el.textContent!, /after/)
    await view.unmount()
  })

test('§9 CONTROL — a chip re-rendered after its index arrives changes its '
  + 'answer', async () => {
    // tokens present BEFORE the index arrives (§8 of the corrections). The
    // first render must not be a permanent verdict.
    const text = `@item:${HERE}/alpha`
    const view = await mountView(
      <TypedRefText world={world({ items: 'loading' })} onOpen={() => {}}
        text={text} />, (el) => el)
    await flush()
    assert.ok(view.el.querySelector('.ref-chip.ref-pending'),
      'before the index: pending')
    assert.equal(view.el.querySelectorAll('button.ref-chip').length, 0)

    await view.render(
      <TypedRefText world={world({ items: new Map([['alpha', 'alpha']]) })}
        onOpen={() => {}} text={text} />)
    await flush()
    assert.ok(!view.el.querySelector('.ref-chip.ref-pending'),
      'after the index: no longer pending')
    assert.equal(view.el.querySelectorAll('button.ref-chip').length, 1,
      'the same token, now resolvable, became clickable')
    await view.unmount()
  })

test('§10 RefChip renders each outcome under its own class', async () => {
  for (const [w, cls] of [
    [world(), 'ref-ready'],
    [world({ items: 'loading' }), 'ref-pending'],
    [world({ items: new Map() }), 'ref-absent'],
  ] as [RefWorld, string][]) {
    const r = decide(`@item:${HERE}/alpha`, w)
    const view = await mountView(<RefChip r={r} onOpen={() => {}} />, (el) => el)
    await flush()
    assert.ok(view.el.querySelector('.' + cls), `expected ${cls}`)
    await view.unmount()
  }
})

test('§11 CONTROL — a kind this panel cannot open is "elsewhere", and that is '
  + 'a different claim from "absent"', () => {
  // The docket owns no document reader. If "I cannot open this" were folded
  // into "this does not exist", the panel would state — in words the user
  // reads — that a perfectly real document is missing, because of a limit of
  // the panel rather than anything about the data.
  const handles = new Set<'item'>(['item'])
  // note the docs index is present AND EMPTY: judged by the index this would
  // be `absent`, so the two answers are genuinely distinguishable here.
  const shut = decide(`@doc:${HERE}/d1`,
    world({ docs: new Map(), handles }))
  const open = decide(`@doc:${HERE}/d1`, world({ docs: new Map() }))
  assert.equal(shut.outcome, 'elsewhere')
  assert.equal(open.outcome, 'absent')
  assert.notEqual(shut.outcome, open.outcome)
  assert.doesNotMatch(shut.why, /no document named/)
  assert.match(shut.why, /not opened from this panel/)
  // and a kind it DOES handle is unaffected by the restriction
  assert.equal(decide(`@item:${HERE}/a`, world({ handles })).outcome, 'ready')
})

test('§11b the handles check runs before the index, so a kind this panel '
  + 'cannot open never reports a data verdict while loading', () => {
  const r = decide(`@doc:${HERE}/d1`,
    world({ docs: 'loading', handles: new Set<'item'>(['item']) }))
  assert.equal(r.outcome, 'elsewhere')
})

// --------------------------------- §12: an explicit token outranks a collision

test('§12 CONTROL — an explicit @agent token is NOT eaten by an item that '
  + 'happens to share the name', async () => {
  // ⚠ THE ORG REALLY HAS THIS COLLISION. An agent named `checklist-evidence`
  // works on items that could easily carry the same name, and the shared
  // mention index resolves such a bare word to the ITEM — deliberately, and
  // that rule is right for a bare word. It must not overrule a writer who
  // typed `@agent:orgtree/checklist-evidence` and thereby said which they
  // meant.
  //
  // What keeps it true is the ORDER inside RefProse: canonical tokens are cut
  // out first, so the bare matcher only ever sees the text BETWEEN them and
  // can never reach inside one. (Its boundary rules would also decline a slug
  // preceded by `/` — a second, independent rule agreeing by luck, which this
  // must not depend on.)
  //
  // ⚠ AND THE INDEX IS THE REAL ONE, built by `buildMentionIndex` with the
  // name registered as BOTH an item and an agent. A hand-rolled Map would let
  // this pass without the collision ever existing — which is the failure mode
  // that once let a "leak" control of mine pass for days.
  const NAME = 'checklist-evidence'
  const index = buildMentionIndex(
    [{ slug: NAME } as WorkItem], [[NAME, 'opus'] as const])
  assert.equal(index.get(NAME)?.kind, 'item',
    'the fixture must really collide, with the item winning the bare word')

  const picked: string[] = []
  const focused: string[] = []
  const opened: string[] = []
  const view = await mountView(
    <RefProse text={`ask @agent:${HERE}/${NAME} about ${NAME}`}
      world={world()} onOpen={(r) => opened.push(r.ref.kind)}
      index={index} onPick={(n) => picked.push(n)}
      onFocusAgent={(id) => focused.push(id)} />,
    (el) => el)
  await flush()

  const agent = view.el.querySelector('button.ref-chip.ref-agent')
  assert.ok(agent, 'the explicit token rendered as an agent reference')
  // ⚠ AND IT IS WHOLE. A bare match inside the token would have split it,
  // leaving a stray `@agent:orgtree/` beside a separate item link.
  assert.ok(!view.el.textContent!.includes(`@agent:${HERE}/`),
    'the token was consumed as one unit, not cut in half')

  await inAct(() => (agent as HTMLElement).click())
  assert.deepEqual(opened, ['agent'], 'clicking it opens the AGENT')
  assert.deepEqual(picked, [], 'and never the item that shares the name')

  // CONTROL: the BARE occurrence of the same word, later in the same string,
  // still links — as the ITEM, per the collision rule. Without this the check
  // above could be passing because the index was inert.
  const bare = view.el.querySelectorAll('button.docket-ref')
  assert.equal(bare.length, 1, 'the bare mention is still a link')
  await inAct(() => (bare[0] as HTMLElement).click())
  assert.deepEqual(picked, [NAME], 'and the bare word went to the item')
  assert.deepEqual(focused, [], 'the bare word did not go to the agent')
  await view.unmount()
})
