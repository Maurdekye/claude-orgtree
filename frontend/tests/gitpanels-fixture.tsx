import { createRoot } from 'react-dom/client'
import { GitPanels, useGitPanels } from '../src/git/panels'
import { CurrentOrg } from '../src/popout'
import { commitModalRect } from '../src/canvas/modalpin'
import { DeskChat } from '../src/canvas/desk'
import { DeskHosts } from '../src/canvas/deskhosts'
import type { CanvasNode } from '../src/canvas/shared'
import '../src/styles.css'
declare global { interface Window { panelFixture: { slug: string }; panelProbe: unknown } }
const slug = window.panelFixture.slug
function Fixture() {
  const state = useGitPanels(slug)
  Object.assign(window, { panelProbe: { panels: () => state.panels, resize: commitModalRect } })
  return <CurrentOrg.Provider value={slug}>
    <button onClick={() => state.open({ slug })}>Open Git repositories</button>
    <GitPanels {...state} routes={{ world: { org: slug, agents: new Map(), handles: new Set(['item', 'agent']) }, onOpen: () => {} }} toast={() => {}} />
  </CurrentOrg.Provider>
}
const node = { id: 'reader', title: 'reader', tier: 'haiku', model_id: 'haiku', state: 'live', generation: 1,
  seat: 1, grant: 0, free: 0, children: [], lineage: [], turns: [], last_denials: [], audiences_held: [],
  scope: { permission_mode: 'default', add_dirs: [], tools: {}, org_visibility: 'team' } } as unknown as CanvasNode
const map = new Map([['reader', node]])
createRoot(document.getElementById('root')!).render(location.search.includes('desk')
  ? <CurrentOrg.Provider value={slug}><DeskHosts map={map} slug={slug}><div style={{ width: 900, height: 700 }}>
    <DeskChat bare node={node} map={map} slug={slug} toast={() => {}} op={async () => ({})} />
  </div></DeskHosts></CurrentOrg.Provider> : <Fixture />)
