import * as esbuild from 'esbuild'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const here = path.dirname(fileURLToPath(import.meta.url))
// ORGTREE_GIT_BUNDLE_OUT: an alternate output directory, so a probe can hold
// two builds side by side (e.g. gitleftedge_probe.py builds the pre-fix
// layout as its positive control) without either overwriting the other
const outDir = process.env.ORGTREE_GIT_BUNDLE_OUT || path.join(here, '../node_modules/.orgtree-git-browser')
await esbuild.build({ entryPoints: [path.join(here, 'gitworkspace.browser.tsx')],
  outfile: path.join(outDir, 'probe.js'),
  bundle: true, format: 'esm', platform: 'browser', target: 'es2022', jsx: 'automatic',
  define: { 'process.env.NODE_ENV': '"production"' } })
