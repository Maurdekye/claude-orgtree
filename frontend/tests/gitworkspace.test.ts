import './harness'
import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { layoutGraph, nodeAction, canRecenter } from '../src/git/layout'
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
