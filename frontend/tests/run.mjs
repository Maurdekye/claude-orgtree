// frontend/tests/run.mjs — the frontend suite's runner.
//
//   node tests/run.mjs                 (from frontend/)  — everything
//   node tests/run.mjs convo           — only files whose name contains "convo"
//   node tests/run.mjs --reps 5        — passed through to the suites via
//                                        ORGTREE_TEST_REPS
//
// WHY A BUNDLE STEP. The sources are TypeScript with extensionless imports and
// JSX, and node's own type stripping does neither. esbuild is already in the
// tree (vite's), so each `*.test.ts(x)` is bundled — app code and all — into
// one ESM file under a temp dir and handed to node's built-in test runner. No
// new runner, no config file, no transform layer to get out of sync with vite:
// the same bundler that builds the app builds the tests.
//
// The DOM comes from jsdom (a devDependency), installed by `harness.ts` before
// any app module is reached — see the import-order note there.

import { execFileSync } from 'node:child_process'
import { mkdirSync, readdirSync, rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import * as esbuild from 'esbuild'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const argv = process.argv.slice(2)
const repsIdx = process.argv.indexOf('--reps')
// the value after --reps is not a filename filter
const filter = argv.filter((a, i) => !a.startsWith('--')
  && argv[i - 1] !== '--reps')[0] ?? ''

const entries = readdirSync(HERE)
  .filter((f) => /\.test\.tsx?$/.test(f))
  .filter((f) => !filter || f.includes(filter))
  .map((f) => path.join(HERE, f))

if (!entries.length) {
  console.error(`no test files match ${filter || '*'}`)
  process.exit(1)
}

// ⚠ the bundle lands INSIDE node_modules on purpose: it imports jsdom (kept
// external), and node resolves a bare specifier by walking up from the
// importing file — from a temp dir that walk never reaches frontend's
// node_modules. It is also already ignored by every VCS rule in the tree.
const out = path.join(HERE, '..', 'node_modules', '.orgtree-tests')
rmSync(out, { recursive: true, force: true })
mkdirSync(out, { recursive: true })
await esbuild.build({
  entryPoints: entries,
  outdir: out,
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'node22',
  jsx: 'automatic',
  sourcemap: 'inline',
  outExtension: { '.js': '.mjs' },
  logLevel: 'warning',
  // jsdom is a real node package with native-ish internals — never bundle it
  external: ['jsdom', 'node:*'],
  define: {
    'process.env.NODE_ENV': '"development"',
    // the bundle runs from node_modules/.orgtree-tests, so a suite that reads
    // the sources cannot find them from import.meta.url — hand it the path
    __SRC_DIR__: JSON.stringify(path.join(HERE, '..', 'src')),
  },
})

const files = readdirSync(out).filter((f) => f.endsWith('.mjs'))
try {
  // --test-force-exit: React's scheduler holds a ref'd MessageChannel open for
  // the process's whole life, so node would otherwise sit at 100 % pass and
  // never exit.
  execFileSync(process.execPath, ['--test', '--test-force-exit',
    ...files.map((f) => path.join(out, f))], {
    stdio: 'inherit',
    env: {
      ...process.env,
      ORGTREE_TEST_REPS: repsIdx > 0 ? process.argv[repsIdx + 1] : process.env.ORGTREE_TEST_REPS,
    },
  })
} catch {
  process.exit(1)
}
