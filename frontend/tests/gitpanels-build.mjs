import * as esbuild from 'esbuild'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
const out = path.resolve('node_modules/.orgtree-gitpanels')
const mutation = process.argv[2]
const mutations = {
  'no-observer-initial-update': ['src/GitWorkspace.tsx', '    observer.observe(element); update()', '    observer.observe(element)'],
  'restored-reset-publish': ['src/GitWorkspace.tsx', '      initialPosition.current = false', '      setView({ top: vp.scrollTop, left: vp.scrollLeft, width: vp.clientWidth, height: vp.clientHeight }); initialPosition.current = false'],
  'no-initial-centering': ['src/GitWorkspace.tsx', '      vp.scrollLeft = Math.max(0, layout.trunkX - vp.clientWidth / 2)', '      vp.scrollLeft = 0'],
  'measure-before-ref': ['src/GitWorkspace.tsx', '    <GitViewportSize viewport={viewport} update={updateView} ready={snapshot !== null} />', ''],
  'close-pinned-navigation': ['src/git/panels.tsx', 'closeIfCentred(panel.kind, () => close(panel.id))', 'close(panel.id)'],
  'leaked-pin': ['src/git/panels.tsx', 'if (panel?.extra) unpinModal(panel.kind)', 'void panel'],
  'ignored-repository-seed': ['src/GitWorkspace.tsx', "useState(initialRepository ?? '')", "useState('')"],
  'secure-context-only': ['src/git/panels.tsx', '(crypto.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`)', 'crypto.randomUUID()'],
  'duplicate-observers': ['src/git/observers.ts', 'const key = JSON.stringify([slug, rid])', 'const key = JSON.stringify([slug, rid, crypto.randomUUID()])'],
  'leaked-observer': ['src/git/observers.ts', 'watch.listeners.delete(listener)', 'if (!(globalThis as any).leakClosedGitObserver) watch.listeners.delete(listener)'],
  'wrong-resize-owner': ['src/GitWorkspace.tsx', 'const owner = useSurfaceDocument()', 'const owner = document'],
  'shared-pin': ['src/git/panels.tsx', 'panelId={panel.kind}', 'panelId={`git:${panel.context.slug}`}'],
  'close-all': ['src/git/panels.tsx', 'old.filter(p => p.id !== id)', '[]'],
  'width-cap': ['src/styles.css', '.popout-mount .overlay-detached > :not(.popout-placeholder)', '.popout-mount .overlay-detached > .settings'],
  'fixed-canvas': ['src/GitWorkspace.tsx', 'width: Math.max(layout.width, view.width), height: Math.max(layout.height, view.height)', 'width: layout.width, height: layout.height'],
}
if (mutation && !mutations[mutation]) throw new Error('Unknown mutation')
const plugins = mutation ? [{ name: mutation, setup(build) {
  const [file, before, after] = mutations[mutation]
  build.onLoad({ filter: /\.(tsx?|css)$/ }, ({ path: target }) => {
    if (path.resolve(file) !== target) return
    const text = readFileSync(target, 'utf8')
    if (text.split(before).length !== 2) throw new Error(`INERT mutation ${mutation}`)
    let contents = text.replace(before, after)
    if (mutation === 'measure-before-ref') {
      const anchor = '    <header className="git-head">'
      if (contents.split(anchor).length !== 2) throw new Error('INERT ref-order mutation')
      contents = contents.replace(anchor, before + '\n' + anchor)
    }
    return { contents, loader: target.endsWith('.css') ? 'css' : 'tsx' }
  })
} }] : []
mkdirSync(out, { recursive: true })
await esbuild.build({ entryPoints: ['tests/gitpanels-fixture.tsx'], outdir: out, bundle: true, format: 'esm', jsx: 'automatic', plugins })
writeFileSync(path.join(out, 'source.json'), JSON.stringify({ mutation: mutation ?? null }))
