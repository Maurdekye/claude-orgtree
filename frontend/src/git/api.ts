import { req } from '../api'
import type { GitRegistry, GitDiscovery, GitSnapshot, GitSettings, GitPage, GitOperation, GitFreshness } from './types'
const base = (slug: string) => `/api/orgs/${encodeURIComponent(slug)}/git`
const repo = (slug: string, id: string) => `${base(slug)}/${encodeURIComponent(id)}`
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const listGit = (slug: string): Promise<GitRegistry> => req(`${base(slug)}/repositories`)
export const registerGit = (slug: string, path: string): Promise<{ id: string; name: string }> => req(`${base(slug)}/repositories`, json('POST', { path }))
export const discoverGit = (slug: string, path?: string): Promise<GitDiscovery> => req(`${base(slug)}/discover`, json('POST', { path }), 120_000)
export const forgetGit = (slug: string, id: string): Promise<unknown> => req(`${repo(slug, id)}/registration`, { method: 'DELETE' })
export const selectGit = (slug: string, id: string): Promise<unknown> => req(`${repo(slug, id)}/selection`, { method: 'POST' })
export const getGitObservation = (slug: string, id: string): Promise<{ busy: boolean; ref_identity?: string; freshness?: GitFreshness }> => req(`${repo(slug, id)}/observation`)
export const getGit = (slug: string, id: string, branches?: string[]): Promise<GitSnapshot> => req(`${repo(slug, id)}/snapshot${branches ? `?branches=${encodeURIComponent(JSON.stringify(branches))}` : ''}`, undefined, 120_000)
export const getGitHistory = (slug: string, id: string, cursor: string): Promise<GitPage> => req(`${repo(slug, id)}/history?cursor=${encodeURIComponent(cursor)}`)
export const getGitSettings = (slug: string, id: string): Promise<GitSettings> => req(`${repo(slug, id)}/settings`)
export const saveGitSettings = (slug: string, id: string, revision: number, values: Record<string, unknown>): Promise<unknown> => req(`${repo(slug, id)}/settings`, json('PATCH', { revision, values }))
export const linkGit = (slug: string, id: string, branch: string, item: string, remove = false): Promise<unknown> => req(`${repo(slug, id)}/links`, json(remove ? 'DELETE' : 'POST', { branch, item }))
export const fetchGit = (slug: string, id: string): Promise<GitOperation> => req(`${repo(slug, id)}/fetch`, { method: 'POST' }, 120_000)
export const gitAction = (slug: string, id: string, action: 'push' | 'pull', snapshot: string, branch: string, worktree?: string): Promise<GitOperation> => req(`${repo(slug, id)}/${action}`, json('POST', { snapshot, branch, worktree }), 150_000)
