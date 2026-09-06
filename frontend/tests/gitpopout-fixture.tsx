import { createRoot } from 'react-dom/client'
import { GitWorkspace } from '../src/GitWorkspace'
import { CurrentOrg } from '../src/popout'
import '../src/styles.css'
const oid = 'a'.repeat(40)
const branch = { ref: 'refs/heads/main', oid, local: true, upstream: 'refs/remotes/origin/main', remote: 'origin', remote_ref: 'refs/heads/main', upstream_oid: 'b'.repeat(40), tickets: [], sync: { state: 'ahead', ahead: 1, behind: 0 }, against_trunk: { state: 'same', ahead: 0, behind: 0 }, unique: { local: [oid], remote: [] }, classified: true }
const snapshot = { token: 'fixture-token', slug: 'fixture', repository_id: 'repo', created: 0, name: 'fixture repository', root: 'C:/fixture', bare: false,
  branches: [branch], worktrees: [], shallow: false, history: { nodes: [{ oid, parents: [], at: 100, subject: 'Bottom right commit', message: Array.from({ length: 55 }, (_, i) => `Detailed commit line ${i}`).join('\n'), rank: 55, lane: { offset: 700, owner: branch.ref }, comparisons: { [branch.ref]: 'local' } }], next_cursor: null, frontier: [], offset: 0 }, inventory: [{ ref: branch.ref, oid, linked: false }], config: { trunk: branch.ref, remote: 'origin', remotes: ['origin'], trunk_missing: false, remote_missing: false }, omitted_active: 0, omitted_worktrees: 0, freshness: { state: 'fresh', age_seconds: 0, watched: false, busy: false }, ref_identity: 'fixture-ref', unborn_branch: null, total_commits: 80 }
const requests: string[] = []
Object.assign(window, { gitPopout: { requests } })
window.fetch = (async (input: RequestInfo | URL) => {
  const url = String(input); requests.push(url)
  const value = url.includes('/repositories') ? { repositories: [{ id: 'repo', name: 'fixture repository', path: 'C:/fixture', links: [] }], selected: 'repo', discovery: { candidates: [], truncated: false, scanned: 0, errors: [] } }
    : url.includes('/snapshot') ? snapshot : url.includes('/push') ? { state: 'unchanged', message: 'fixture action acknowledged' } : { busy: false, ref_identity: 'fixture-ref', freshness: snapshot.freshness }
  return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch
createRoot(document.getElementById('root')!).render(<CurrentOrg.Provider value="fixture"><GitWorkspace slug="fixture" routes={{ world: { org: 'fixture', agents: new Map(), handles: new Set(['item', 'agent']) }, onOpen: () => {} }} toast={() => {}} close={() => {}} /></CurrentOrg.Provider>)
