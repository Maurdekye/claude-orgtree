import * as esbuild from 'esbuild'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
const out = path.resolve('node_modules/.orgtree-gitpanels')
const mutation = process.argv[2]
const mutations = {
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
    return { contents: text.replace(before, after), loader: target.endsWith('.css') ? 'css' : 'tsx' }
  })
} }] : []
mkdirSync(out, { recursive: true })
await esbuild.build({ entryPoints: ['tests/gitpanels-fixture.tsx'], outdir: out, bundle: true, format: 'esm', jsx: 'automatic', plugins })
writeFileSync(path.join(out, 'source.json'), JSON.stringify({ mutation: mutation ?? null }))
