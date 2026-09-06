import { BASE } from '../api'
import type { GitContext } from './types'
export function GitContextButton({ slug, item, agent }: GitContext) {
  if (BASE) return null
  return <button type="button" className="badge git-context" title="Open related repository graph"
    onClick={(e) => { e.stopPropagation(); window.dispatchEvent(new CustomEvent<GitContext>('orgtree:git-open', { detail: { slug, item, agent } })) }}>⑂ Git</button>
}
