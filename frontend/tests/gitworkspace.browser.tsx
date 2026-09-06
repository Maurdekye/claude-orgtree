import { createRoot } from 'react-dom/client'
import { GitWorkspace } from '../src/GitWorkspace'
import type { RefRoutes } from '../src/canvas/reflinks'
import '../src/styles.css'

declare global {
  interface Window {
    gitFixture: { slug: string; agent: string }
    gitOpened: string[]
    gitToasts: string[][]
  }
}
window.gitOpened = []; window.gitToasts = []
const routes: RefRoutes = { world: { org: window.gitFixture.slug, agents: new Map([[window.gitFixture.agent, window.gitFixture.agent]]), handles: new Set(['item', 'agent']) }, onOpen: r => { window.gitOpened.push(r.token) } }
createRoot(document.getElementById('root')!).render(<GitWorkspace slug={window.gitFixture.slug} routes={routes}
  toast={lines => { if (lines) window.gitToasts.push(lines) }} close={() => { window.gitOpened.push('closed') }} />)
