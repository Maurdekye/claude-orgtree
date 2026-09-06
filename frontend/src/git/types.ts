export interface GitDelta { added: number | null; removed: number | null; reason: string | null }
export interface GitFile {
  path: string; old_path: string | null; xy: string; conflicted: boolean
  staged: GitDelta | null; unstaged: GitDelta | null; untracked: GitDelta | null
}
export interface GitChanges {
  state: string; files: GitFile[]; count: number | null; complete: boolean
  operations?: string[]; reason?: string; conflicted?: number; fingerprint?: string
}
export interface GitWorktree {
  id: string; path: string; oid?: string | null; branch?: string; detached?: boolean
  bare?: boolean; locked?: string | boolean; prunable?: string | boolean
  agents: string[]; changes: GitChanges
}
export interface GitTicket {
  slug: string; title: string | null; ref: string; missing: boolean; status: string | null
  owner: { id: string; generation: number; tier: string | null; current: boolean; state: string; ref: string | null; target?: string | null } | null
}
export interface GitComparison { state: string; ahead: number | null; behind: number | null }
export interface GitBranch {
  ref: string; oid: string; local: boolean; upstream: string; remote: string; remote_ref: string
  upstream_oid: string | null; tickets: GitTicket[]; sync: GitComparison; against_trunk: GitComparison
  unique: { local: string[]; remote: string[] }; classified: boolean
}
export interface GitCommit { oid: string; parents: string[]; at: number; subject: string; message?: string; rank?: number; lane?: { offset: number; owner: string | null }; comparisons?: Record<string, 'local' | 'remote'> }
export interface GitPage { nodes: GitCommit[]; next_cursor: string | null; frontier: string[]; offset: number; shallow?: boolean }
export interface GitFreshness {
  state: string; age_seconds: number | null; watched: boolean; busy: boolean
  success_at?: number; attempt_at?: number; error?: string | null
}
export interface GitSnapshot {
  token: string; slug: string; repository_id: string; created: number; name: string; root: string; bare: boolean
  branches: GitBranch[]; worktrees: GitWorktree[]; shallow: boolean; history: GitPage
  inventory: { ref: string; oid: string; linked: boolean }[]
  config: { trunk: string | null; remote: string | null; remotes: string[]; trunk_missing: boolean; remote_missing: boolean }
  omitted_active: number; omitted_worktrees: number; freshness: GitFreshness
  ref_identity: string; unborn_branch: string | null
  total_commits: number
}
export interface GitRegistry {
  repositories: { id: string; name: string; path: string; links: { branch: string; item: string; agent: string | null }[] }[]; selected: string | null
  discovery: { candidates: { path: string; name: string }[]; truncated: boolean; scanned: number; errors: unknown[] }
}
export interface GitSettings {
  revision: number; auto_fetch: boolean; remote: string | null; trunk: string | null
  remotes: string[]; branches: string[]; saved_trunk: string | null; saved_remote: string | null
  items: { slug: string; title: string }[]
  links: { repository_id: string; branch_ref: string; org_slug: string; item_slug: string }[]
}
export interface GitOperation { state: string; message: string; before?: string; after?: string; target?: string }
export interface GitContext { slug: string; item?: string; agent?: string }
