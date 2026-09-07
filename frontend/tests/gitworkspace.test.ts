import './harness'
import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { layoutGraph, nodeAction, canRecenter, EDGE, LABEL_WIDTH } from '../src/git/layout'
import type { GitBranch, GitCommit, GitSnapshot } from '../src/git/types'

const branch = (ref: string, oid: string): GitBranch => ({ ref, oid, local: true, upstream: '', remote: '', remote_ref: '', upstream_oid: null,
  tickets: [], sync: { state: 'no_upstream', ahead: null, behind: null }, against_trunk: { state: 'in_sync', ahead: 0, behind: 0 }, unique: { local: [], remote: [] }, classified: true })
const snapshot = (branches: GitBranch[]): GitSnapshot => ({ token: 'fixture', slug: 'fixture', repository_id: 'r', created: 0, name: 'fixture', root: '', bare: false,
  ref_identity: 'fixture', unborn_branch: null, total_commits: 0,
  branches, worktrees: [], shallow: false, history: { nodes: [], next_cursor: null, frontier: [], offset: 0 }, inventory: [], config: { trunk: 'refs/heads/main', remote: null, remotes: [], trunk_missing: false, remote_missing: false }, omitted_active: 0, omitted_worktrees: 0, freshness: { state: 'not_watched', age_seconds: null, watched: false, busy: false } })

test('paged ancestry retains all prior coordinates and merge parents with tied timestamps', () => {
  const nodes: GitCommit[] = Array.from({ length: 3006 }, (_, i) => ({ oid: `oid-${i}`, parents: i < 3005 ? [`oid-${i + 1}`] : [], at: 1700000000, subject: 'same time' }))
  nodes[2]!.parents.push('side')
  nodes.splice(3, 0, { oid: 'side', parents: ['oid-4'], at: 1700000000, subject: 'merge arm' })
  const snap = snapshot([branch('refs/heads/main', 'oid-0'), branch('refs/heads/task', 'side')])
  const first = layoutGraph(nodes.slice(0, 120), snap)
  const expanded = layoutGraph(nodes.slice(0, 240), snap)
  for (const [id, p] of first.points) assert.deepEqual(expanded.points.get(id), p)
  assert.notEqual(first.points.get('side')!.x, first.trunkX)
  assert.equal(first.points.get('oid-4')!.x, first.trunkX)
  assert.equal(new Set(layoutGraph(nodes, snap).points.keys()).size, 3007)
  assert.deepEqual(nodes[2]!.parents, ['oid-3', 'side'])
})

test('middle commit chooses a branch action without changing its captured tip', () => {
  const main = branch('refs/heads/main', 'tip')
  main.unique.local = ['tip', 'middle', 'old-unpushed']
  const action = nodeAction('middle', [main], null)
  assert.equal(action?.action, 'push')
  assert.equal(action?.branch.oid, 'tip')
  const alias = { ...main, ref: 'refs/heads/alias' }
  assert.equal(nodeAction('middle', [main, alias], null), null)
  assert.equal(nodeAction('middle', [main, alias], alias.ref)?.branch.ref, alias.ref)
  main.unique.remote = ['incoming-middle']
  main.upstream_oid = 'upstream-tip'
  assert.equal(nodeAction('incoming-middle', [main], null)?.branch.upstream_oid, 'upstream-tip')
  assert.equal(nodeAction('shared', [main], null), null)
})

test('recenter has both a fitting and clipping control', () => {
  const snap = snapshot([branch('refs/heads/main', 'one')])
  const layout = layoutGraph([{ oid: 'one', parents: [], at: 0, subject: 'one' }], snap)
  assert.equal(canRecenter(layout, 1300), true)
  assert.equal(canRecenter(layout, 400), false)
})

test('every lane and label starts inside the canvas, however many side lanes there are (left-edge regression)', () => {
  // User screenshot 2026-09-07 11:00Z: with four side lanes the leftmost
  // branch's label began at x = -177, outside the canvas, and no drag could
  // reveal it. Both sides of the rule: the old formula's exact shape fails,
  // and one/two side lanes (which never clipped) keep their old geometry.
  for (const sideLanes of [1, 2, 3, 4, 5, 8, 13]) {
    const tips = Array.from({ length: sideLanes }, (_, i) => `tip-${i}`)
    const nodes: GitCommit[] = [{ oid: 'trunk', parents: [], at: 0, subject: 'trunk' },
      ...tips.map(oid => ({ oid, parents: ['trunk'], at: 0, subject: oid }))]
    const snap = snapshot([branch('refs/heads/main', 'trunk'), ...tips.map((oid, i) => branch(`refs/heads/side-${String(i).padStart(2, '0')}`, oid))])
    const layout = layoutGraph(nodes, snap)
    assert.equal(layout.annotations.length, sideLanes + 1, `all ${sideLanes + 1} labels laid out`)
    const leftmostLabel = Math.min(...layout.annotations.map(a => a.x))
    const leftmostPoint = Math.min(...[...layout.points.values()].map(p => p.x))
    assert.ok(leftmostLabel >= EDGE, `side lanes=${sideLanes}: leftmost label at ${leftmostLabel}, must be >= ${EDGE}`)
    assert.ok(leftmostPoint >= EDGE, `side lanes=${sideLanes}: leftmost point at ${leftmostPoint}`)
    const rightmost = Math.max(...layout.annotations.map(a => a.x + LABEL_WIDTH), ...[...layout.points.values()].map(p => p.x))
    assert.ok(layout.width >= rightmost + EDGE, `side lanes=${sideLanes}: width ${layout.width} must cover ${rightmost + EDGE}`)
    // the label still hangs off its own lane, on the outer side
    for (const a of layout.annotations) {
      if (a.anchor.x < layout.trunkX) assert.equal(a.x, a.anchor.x - 277)
      else assert.equal(a.x, a.anchor.x + 105)
    }
  }
  // the exact reported shape: 4 side lanes, leftmost lane 2 columns out
  const four = Array.from({ length: 4 }, (_, i) => `t${i}`)
  const snap4 = snapshot([branch('refs/heads/main', 'trunk'), ...four.map((oid, i) => branch(`refs/heads/b${i}`, oid))])
  const layout4 = layoutGraph([{ oid: 'trunk', parents: [], at: 0, subject: 't' }, ...four.map(oid => ({ oid, parents: ['trunk'], at: 0, subject: oid }))], snap4)
  const leftLane = Math.min(...[...layout4.points.values()].map(p => p.x))
  assert.equal(layout4.trunkX - leftLane, 660, 'two columns out, as the lane rule places it')
  // the trunk lands where the margin formula puts it. Honest note: WHEN THE
  // TRUNK IS AMONG THE BRANCHES the extents guard alone would land it in the
  // SAME place (measured: with the old 100px margin and the guard on, this
  // passes; with the guard off it fails). Without a trunk (next test) the
  // margin formula is short by a lane and only the guard saves the label, so
  // the guard is the load-bearing layer and the margin is the readable intent.
  assert.equal(layout4.trunkX, 2 * 330 + 277 + EDGE)
  assert.equal(layout4.trunkX, 952, 'the literal margin, so EDGE cannot drift unnoticed')
  assert.equal(Math.min(...layout4.annotations.map(a => a.x)), leftLane - 277)
  assert.ok(leftLane - 277 >= EDGE)
  // one side lane: geometry unchanged from before (trunk at 750)
  const one = layoutGraph([{ oid: 'trunk', parents: [], at: 0, subject: 't' }, { oid: 's', parents: ['trunk'], at: 0, subject: 's' }],
    snapshot([branch('refs/heads/main', 'trunk'), branch('refs/heads/s', 's')]))
  assert.equal(one.trunkX, 750)
})

test('server-assigned lanes that reach past the left edge are shifted into view, trunk and labels together', () => {
  // the backend sends lane offsets; a lane further left than the client
  // formula expects must still be reachable, and everything moves as one
  const nodes: GitCommit[] = [{ oid: 'trunk', parents: [], at: 0, subject: 't', lane: { offset: 0, owner: 'refs/heads/main' } },
    { oid: 'far', parents: ['trunk'], at: 0, subject: 'far', lane: { offset: -1320, owner: 'refs/heads/far' } }]
  const snap = snapshot([branch('refs/heads/main', 'trunk'), branch('refs/heads/far', 'far')])
  const layout = layoutGraph(nodes, snap)
  const far = layout.points.get('far')!, trunk = layout.points.get('trunk')!
  assert.equal(trunk.x - far.x, 1320, 'relative geometry preserved')
  assert.equal(trunk.x, layout.trunkX, 'trunkX moved with the points')
  const label = layout.annotations.find(a => a.branch.ref === 'refs/heads/far')!
  assert.equal(label.x, EDGE, 'the furthest-left thing sits exactly at the edge margin')
  assert.equal(label.anchor, far, 'the label anchors the shifted point itself')
})

test('a repository without a trunk (local-only, no remote) keeps its leftmost label inside the canvas', () => {
  // Found by redteam-opus on a real local-only repository (2026-09-07 13:12Z):
  // configuration() leaves config.trunk null when there is no remote, so the
  // server lays out ALL N branches as side lanes (gitworkspace.py, offsets
  // -330, +330, -660, +660, -990 ...), while the client margin is sized from
  // branches.length - 1. With an odd N the leftmost server lane is a whole
  // 330px column further left than the margin reserves; only the extents
  // guard brings it back. Real snapshot numbers: N=3 leftmost lane -660,
  // N=5 leftmost lane -990; pre-fix labels at -187 / -507, post-fix at 15.
  const serverOffset = (side: number) => (side % 2 ? -1 : 1) * Math.ceil(side / 2) * 330
  for (const n of [3, 4, 5]) {
    const refs = Array.from({ length: n }, (_, i) => `refs/heads/b${i}`)
    const nodes: GitCommit[] = refs.map((ref, i) => ({ oid: `tip${i}`, parents: [], at: 0, subject: ref, lane: { offset: serverOffset(i + 1), owner: ref } }))
    const snap = snapshot(refs.map((ref, i) => branch(ref, `tip${i}`)))
    snap.config = { ...snap.config, trunk: null }
    const layout = layoutGraph(nodes, snap)
    assert.equal(layout.annotations.length, n)
    const leftmostLane = Math.min(...nodes.map(nd => nd.lane!.offset))
    assert.equal(leftmostLane, -330 * Math.ceil(n / 2), 'the server shape under test is the real one')
    // the margin formula alone: where would the leftmost label have started?
    const marginOnly = Math.max(750, Math.ceil((n - 1) / 2) * 330 + 277 + EDGE)
    const unguarded = marginOnly + leftmostLane - 277
    if (n % 2) assert.ok(unguarded < EDGE, `n=${n}: margin alone leaves the label at ${unguarded}, the guard must act`)
    else assert.equal(unguarded, EDGE, `n=${n}: even N is covered by the margin alone`)
    const leftmostLabel = Math.min(...layout.annotations.map(a => a.x))
    assert.equal(leftmostLabel, EDGE, `n=${n}: leftmost label sits exactly at the edge`)
    assert.equal(layout.trunkX, marginOnly + Math.max(0, EDGE - unguarded), `n=${n}: trunk shifted by exactly the shortfall`)
    for (const p of layout.points.values()) assert.equal(p.x - layout.trunkX, nodes.find(nd => nd.oid === p.oid)!.lane!.offset, 'relative lanes preserved')
    const rightmost = Math.max(...layout.annotations.map(a => a.x + LABEL_WIDTH), ...[...layout.points.values()].map(p => p.x))
    assert.ok(layout.width >= rightmost + EDGE, `n=${n}: width ${layout.width} covers ${rightmost + EDGE}`)
  }
  // the two odd shapes Opus measured on the real snapshot, as literals
  const at = (n: number) => {
    const refs = Array.from({ length: n }, (_, i) => `refs/heads/b${i}`)
    const snap = snapshot(refs.map((ref, i) => branch(ref, `tip${i}`)))
    snap.config = { ...snap.config, trunk: null }
    return layoutGraph(refs.map((ref, i) => ({ oid: `tip${i}`, parents: [], at: 0, subject: ref, lane: { offset: serverOffset(i + 1), owner: ref } })), snap)
  }
  assert.equal(at(3).trunkX, 952)
  assert.equal(at(5).trunkX, 1282)
})

test('the canvas width follows the right extent, after the shift, and a bare point is measured by its half width', () => {
  // right extent alone: a far-right server lane's label ends past trunkX*2+300
  const right = layoutGraph([{ oid: 'trunk', parents: [], at: 0, subject: 't', lane: { offset: 0, owner: 'refs/heads/main' } },
    { oid: 'r', parents: ['trunk'], at: 0, subject: 'r', lane: { offset: 1320, owner: 'refs/heads/r' } }],
    snapshot([branch('refs/heads/main', 'trunk'), branch('refs/heads/r', 'r')]))
  const rLabel = right.annotations.find(a => a.branch.ref === 'refs/heads/r')!
  assert.ok(rLabel.x + LABEL_WIDTH > 750 * 2 + 300, 'the fixture really exceeds the base width')
  assert.equal(right.width, rLabel.x + LABEL_WIDTH + EDGE)
  // both at once: the shift moves the right extent too, and width must include it
  const both = layoutGraph([{ oid: 'trunk', parents: [], at: 0, subject: 't', lane: { offset: 0, owner: 'refs/heads/main' } },
    { oid: 'l', parents: ['trunk'], at: 0, subject: 'l', lane: { offset: -1320, owner: 'refs/heads/l' } },
    { oid: 'r', parents: ['trunk'], at: 0, subject: 'r', lane: { offset: 1320, owner: 'refs/heads/r' } }],
    snapshot([branch('refs/heads/main', 'trunk'), branch('refs/heads/l', 'l'), branch('refs/heads/r', 'r')]))
  assert.equal(both.annotations.find(a => a.branch.ref === 'refs/heads/l')!.x, EDGE)
  const bothRight = both.annotations.find(a => a.branch.ref === 'refs/heads/r')!
  assert.equal(both.width, bothRight.x + LABEL_WIDTH + EDGE, 'width measured from the SHIFTED right label')
  // a point with no label of its own is the leftmost thing: its half width binds
  const bare = layoutGraph([{ oid: 'trunk', parents: [], at: 0, subject: 't', lane: { offset: 0, owner: 'refs/heads/main' } },
    { oid: 'orphan', parents: ['trunk'], at: 0, subject: 'o', lane: { offset: -1320, owner: null } }],
    snapshot([branch('refs/heads/main', 'trunk')]))
  assert.equal(bare.annotations.length, 1, 'only the trunk has a label')
  assert.equal(bare.points.get('orphan')!.x, EDGE + 12, 'the node box (24px) begins exactly at the edge')
})
