import type { GitBranch, GitCommit, GitSnapshot } from './types'

export const ROW = 28
export const PALETTE = ['#75d5ac', '#a8b8fa', '#e8be83', '#de9dbc', '#80c5de', '#c2adf0', '#dd9d80', '#accb76']
export const shortRef = (ref: string): string => ref.replace(/^refs\/(heads|remotes)\//, '')
export function branchColor(ref: string): string {
  let hash = 0
  for (const c of ref) hash = (hash * 31 + c.charCodeAt(0)) | 0
  return PALETTE[Math.abs(hash) % PALETTE.length]!
}
export interface Point { x: number; y: number; oid: string; owner: string | null }
export interface Annotation { branch: GitBranch; x: number; y: number; anchor: Point; height: number }
export interface Layout { points: Map<string, Point>; annotations: Annotation[]; width: number; height: number; trunkX: number }

/** Topology determines order; commit timestamps never manufacture ancestry.
 * Appending a page retains coordinates because existing input order is stable.
 */
export function layoutGraph(nodes: GitCommit[], snapshot: GitSnapshot): Layout {
  const branches = [...snapshot.branches].sort((a, b) => Number(b.ref === snapshot.config.trunk) - Number(a.ref === snapshot.config.trunk) || a.ref.localeCompare(b.ref))
  const sideCount = Math.max(1, branches.length - 1)
  const trunkX = Math.max(750, Math.ceil(sideCount / 2) * 330 + 100)
  const width = trunkX * 2 + 300
  const byId = new Map(nodes.map(n => [n.oid, n]))
  const reserved = new Map<string, { x: number; owner: string | null }>()
  const trunk = branches.find(b => b.ref === snapshot.config.trunk)
  let oid: string | undefined = trunk?.oid
  const seen = new Set<string>()
  while (oid && !seen.has(oid)) {
    seen.add(oid); reserved.set(oid, { x: trunkX, owner: trunk?.ref ?? null })
    oid = byId.get(oid)?.parents[0]
  }
  let side = 0
  for (const branch of branches) {
    const index = branch === trunk ? 0 : ++side
    const x = branch === trunk ? trunkX : trunkX + (index % 2 ? -1 : 1) * Math.ceil(index / 2) * 330
    if (!reserved.has(branch.oid)) reserved.set(branch.oid, { x, owner: branch.ref })
    if (branch.upstream_oid && !reserved.has(branch.upstream_oid)) {
      reserved.set(branch.upstream_oid, { x: branch.sync.state === 'diverged' ? x + 75 : x, owner: branch.ref })
    }
  }
  const points = new Map<string, Point>()
  const indexes = new Map(nodes.map((n, i) => [n.oid, i]))
  const ordered = [...nodes].sort((a, b) => (a.rank ?? indexes.get(a.oid)!) - (b.rank ?? indexes.get(b.oid)!))
  ordered.forEach((node, index) => {
    const position = node.lane ? { x: trunkX + node.lane.offset, owner: node.lane.owner }
      : reserved.get(node.oid) ?? { x: trunkX + 165, owner: null }
    const point = { ...position, oid: node.oid, y: 85 + (node.rank ?? index) * ROW }
    points.set(node.oid, point)
    node.parents.forEach((parent, parentIndex) => {
      if (!reserved.has(parent)) reserved.set(parent, {
        x: position.x + (parentIndex ? 75 * parentIndex : 0), owner: position.owner,
      })
    })
  })
  const columns = new Map<number, number>()
  const annotations: Annotation[] = []
  for (const branch of branches) {
    const anchor = points.get(branch.oid)
    if (!anchor) continue
    const owners = branch.tickets.some(t => t.owner)
    const height = 18 * (1 + Number(branch.tickets.length > 0) + Number(owners))
    // Put labels outward from side lanes; leave room for the nearby remote
    // fork on the trunk so commit controls cannot sit under ticket text.
    const x = anchor.x < trunkX ? anchor.x - 277 : anchor.x + 105
    const y = Math.max(anchor.y - height / 2, columns.get(x) ?? 15)
    columns.set(x, y + height + 12)
    annotations.push({ branch, anchor, x, y, height })
  }
  return { points, annotations, width, height: Math.max(570, 170 + Math.max(snapshot.total_commits, nodes.length) * ROW), trunkX }
}

/** A click picks a comparison/branch, never an arbitrary source commit. */
export function nodeAction(oid: string, branches: GitBranch[], focused: string | null, comparisons?: GitCommit['comparisons']): { branch: GitBranch; action: 'push' | 'pull' } | null {
  const candidates: { branch: GitBranch; action: 'push' | 'pull' }[] = []
  for (const branch of branches) {
    if (!branch.local || !branch.classified) continue
    if (comparisons ? comparisons[branch.ref] === 'local' : branch.unique.local.includes(oid)) candidates.push({ branch, action: 'push' })
    else if (comparisons ? comparisons[branch.ref] === 'remote' : branch.unique.remote.includes(oid)) candidates.push({ branch, action: 'pull' })
  }
  return candidates.find(c => c.branch.ref === focused) ?? (candidates.length === 1 ? candidates[0]! : null)
}

export function canRecenter(layout: Layout, viewportWidth: number): boolean {
  const xs = [...layout.points.values()].map(p => p.x)
  layout.annotations.forEach(a => { xs.push(a.x, a.x + 245) })
  return xs.length > 0 && Math.min(...xs) - layout.trunkX + viewportWidth / 2 >= 15
    && Math.max(...xs) - layout.trunkX + viewportWidth / 2 <= viewportWidth - 15
}
