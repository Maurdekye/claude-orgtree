#!/usr/bin/env node
// Runs automatically after `vite build` (npm's postbuild convention — see
// package.json). vite.config.js sets build.sourcemap = 'hidden', which still
// writes *.js.map files into dist/assets/ alongside the bundles.
//
// ⚠ THEY MUST LAND OUTSIDE dist/ ENTIRELY, NOT JUST OUTSIDE dist/assets/.
// The first version of this script moved them to dist/sourcemaps/ — still
// publicly reachable, because api.py's SPA catch-all (`@app.get("/{path:path}")`)
// serves ANY file under FRONTEND_DIST whose resolved path starts with
// FRONTEND_DIST, not just the /assets StaticFiles mount. Measured live: a
// build with maps under dist/sourcemaps/ answered GET /sourcemaps/<file>.map
// with 200 and the full map. Landing them as a SIBLING of dist/ (frontend/
// sourcemaps/, not frontend/dist/sourcemaps/) puts them outside that prefix
// check, so no route serves them — the backend resolver
// (backend/orgtree/crashreports.py) reads them straight off disk instead.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const distAssets = path.join(here, '..', 'dist', 'assets')
const sourcemapsDir = path.join(here, '..', 'sourcemaps')

if (!fs.existsSync(distAssets)) {
  console.warn('[postbuild-sourcemaps] no dist/assets — skipping (did the build fail?)')
  process.exit(0)
}

fs.mkdirSync(sourcemapsDir, { recursive: true })

let moved = 0
for (const name of fs.readdirSync(distAssets)) {
  if (!name.endsWith('.map')) continue
  fs.renameSync(path.join(distAssets, name), path.join(sourcemapsDir, name))
  moved += 1
}

console.log(`[postbuild-sourcemaps] moved ${moved} source map(s) out of the public assets dir`)
