// chiptips.test.tsx — the three hire-badge tooltips, as a FAMILY.
//
// User request 2026-08-28: "update the badge tooltip texts to all bear a
// similar resemblance to one another, and make them more concise; just 3-5
// words at most per tooltip", scoped moments later to "for subordinate,
// superior, and coworker".
//
// The deliverable is consistency, so consistency is what these check — not
// three separate spellings. Asserting the exact strings would pin today's
// wording and say nothing about whether the three still match each other; the
// next person to reword one would sail past a green suite having broken the
// only property the user actually asked for. So the checks are relational:
// same length, same shape, differing in exactly one word.
//
// Read out of the rendered DOM rather than out of cards.tsx, because a
// source-text check pins a spelling rather than behaviour — it cannot tell a
// tooltip that changed from one that moved, and this repo has already been
// bitten by a guard that matched a string instead of a state.
//
// Run:  cd frontend && node tests/run.mjs chiptips

import { flush, inAct, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import type { TreePayload } from '../src/types'

const noop = () => {}
const asTree = (v: unknown) => v as TreePayload

function tree(nodeIds: string[]): TreePayload {
  const mk = (id: string) => ({
    id, title: id, tier: 'haiku', model_id: 'haiku', state: 'live',
    seat: 1, grant: 0, free: 0, ui_order: 0, cost_usd: 0, occupancy: null,
    context_window: null, charter: null, mail_pending: 0, limit_locked: false,
    last_status: null, prev_status: null, inflight_at: null, last_denials: [],
    turns: [], frozen: null, audiences_held: [], bearer_state: null,
    generation: 0, children: [], lineage: [],
    scope: { permission_mode: 'default', add_dirs: [], tools: {}, org_visibility: 'team' },
  })
  return asTree({
    slug: 'mine', name: 'mine', workspace: null, dirs: [], max_top_grant: 1000,
    default_top_grant: 50, compact_at: 0, default_tools: null,
    default_visibility: 'team', default_effort: '', credit_requests: [],
    tiers: { haiku: 1, sonnet: 3, opus: 5, fable: 10 }, audiences: [],
    roots: nodeIds.map(mk), cost_usd_total: 0,
    audit: { live_nodes: nodeIds.length, top_level_holds: 0, no_overdraft: true, problems: [] },
    user_inbox_count: 0, user_inbox_newest: null, fable_lock: null,
    spend_frozen: false, storage_blocked: false, auto_resume: false,
    fable_limit_policy: 'freeze', fable_filter_policy: 'halt',
    cascade_hire: false, cascade_alloc: true, sandboxed: false,
    audience_requests: [], org_inbox: null, net: null,
  })
}

function uiTest(name: string,
  body: (k: { mount: (el: React.ReactElement)
    => Promise<{ el: HTMLElement }> }) => Promise<void>): void {
  test(name, async (t: TestContext) => {
    useFakeClock()
    const open: { unmount: () => Promise<void> }[] = []
    t.after(async () => {
      for (const m of open) { try { await m.unmount() } catch { /* gone */ } }
      realClock()
    })
    await body({
      mount: async (el) => {
        const v = await mountView(el, (host) => host)
        open.push(v)
        return { el: v.el }
      },
    })
  })
}

/** the enabled tooltip for one tier, off each of the three badge sets.
 *  `haiku` costs 1 seat, so on this fixture none of them is the can't-afford
 *  variant — that string is deliberately long (it carries the remedy) and is
 *  not one of the three the user scoped. */
async function tips(mount: (el: React.ReactElement) => Promise<{ el: HTMLElement }>) {
  const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
  const { el } = await mount(
    <OrgCanvas tree={tree(['ceo', 'cto'])} op={() => Promise.resolve({} as never)}
      slug="mine" toast={noop} mailEvt={null} />)
  await flush()
  const pick = (sel: string) => {
    const b = el.querySelector(`${sel} button.t-haiku`) as HTMLElement | null
    assert.ok(b, `no haiku badge rendered for ${sel}`)
    const t = b!.getAttribute('title')
    assert.ok(t, `the ${sel} badge carries no tooltip at all`)
    return t!
  }
  return {
    subordinate: pick('.hsof:not(.side)'),
    coworker: pick('.hsof.side-l'),
    superior: pick('.hsof.side-t'),
  }
}

const words = (s: string) => s.trim().split(/\s+/)

uiTest('§1 every badge tooltip is within the user’s 3-5 word ceiling',
  async ({ mount }) => {
    const t = await tips(mount)
    for (const [role, s] of Object.entries(t)) {
      const n = words(s).length
      assert.ok(n >= 3 && n <= 5,
        `the ${role} tooltip is ${n} words ("${s}") — the user set a hard `
        + 'ceiling of 3-5 words per tooltip, not a target to approach')
    }
  })

uiTest('§2 the three read as one family — same shape, one word apart',
  async ({ mount }) => {
    const t = await tips(mount)
    const [a, b, c] = [words(t.subordinate), words(t.coworker), words(t.superior)]

    assert.equal(a.length, b.length,
      `subordinate ("${t.subordinate}") and coworker ("${t.coworker}") are `
      + 'different lengths — they do the same kind of thing and should read alike')
    assert.equal(a.length, c.length,
      `subordinate ("${t.subordinate}") and superior ("${t.superior}") are `
      + 'different lengths')

    // exactly ONE position may differ across the three, and it must be the one
    // carrying the role — that is the only thing that differs in meaning
    const differing = a.map((_, i) =>
      (a[i] === b[i] && a[i] === c[i]) ? null : i).filter((i) => i !== null)
    assert.deepEqual(differing, [a.length - 1],
      `the three tooltips differ at word position(s) ${JSON.stringify(differing)} `
      + `— they must differ in exactly one word, the role.\n`
      + `  subordinate: "${t.subordinate}"\n  coworker:    "${t.coworker}"\n`
      + `  superior:    "${t.superior}"`)

    assert.deepEqual(
      [a[a.length - 1], b[b.length - 1], c[c.length - 1]],
      ['subordinate', 'coworker', 'superior'],
      'the differing word must actually name the role being hired')
  })

uiTest('§3 one voice — imperative, verb first, matching the card’s other controls',
  async ({ mount }) => {
    const t = await tips(mount)
    for (const [role, s] of Object.entries(t)) {
      assert.equal(words(s)[0], 'hire',
        `the ${role} tooltip opens with "${words(s)[0]}" ("${s}") — every badge `
        + 'here performs a hire, and mixing "insert"/"hire" is what made the '
        + 'old set read as three unrelated strings')
      assert.doesNotMatch(s, /[A-Z]/,
        `the ${role} tooltip shouts ("${s}") — the card's other control `
        + 'tooltips are lowercase ("retire — …", "dissolve — …")')
      assert.doesNotMatch(s, /\(seat/,
        `the ${role} tooltip still carries a seat cost ("${s}") — it does not `
        + 'fit the five-word ceiling, and the credit bar states it anyway')
    }
  })

uiTest('§4 the article still agrees with the tier it names',
  async ({ mount }) => {
    // `an opus`, not `a opus` — the trim must not have dropped the a/an test
    const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
    const { el } = await mount(
      <OrgCanvas tree={tree(['ceo'])} op={() => Promise.resolve({} as never)}
        slug="mine" toast={noop} mailEvt={null} />)
    await flush()
    const got: Record<string, string> = {}
    for (const b of [...el.querySelectorAll('.hsof.side-l button')] as HTMLElement[]) {
      const t = b.getAttribute('title') ?? ''
      const m = /^hire (an?) (\w+)/.exec(t)
      if (m) got[m[2]!] = m[1]!
    }
    assert.ok(Object.keys(got).length >= 2,
      `expected several tier badges, saw ${JSON.stringify(got)}`)
    for (const [tier, art] of Object.entries(got)) {
      assert.equal(art, /^[aeiou]/.test(tier) ? 'an' : 'a',
        `"${art} ${tier}" — the article does not agree with the tier name`)
    }
  })
