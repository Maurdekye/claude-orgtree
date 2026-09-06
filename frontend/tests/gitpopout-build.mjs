// Compose exact landed Git sources with this private popout source snapshot.
// No tracked production file is changed by this integration fixture.
import { cpSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import * as esbuild from 'esbuild'
const ref = process.argv[2]
if (!ref || !/^[a-f0-9]{7,40}$/.test(ref)) throw new Error('Pass the verified landed Git commit SHA')
const mutate = process.argv[3] === 'lexical-window'
const root = path.resolve('..')
const out = path.resolve('node_modules/.orgtree-git-popout')
mkdirSync(path.join(out, 'tests'), { recursive: true })
cpSync('src', path.join(out, 'src'), { recursive: true })
for (const file of ['GitWorkspace.tsx', 'git/api.ts', 'git/layout.ts', 'git/types.ts', 'git/workspace.css']) {
  const value = execFileSync('git', ['-c', `safe.directory=${root.replaceAll('\\', '/')}`, 'show', `${ref}:frontend/src/${file}`])
  const target = path.join(out, 'src', file); mkdirSync(path.dirname(target), { recursive: true }); writeFileSync(target, value)
}
const apiPath = path.join(out, 'src/api.ts')
let api = readFileSync(apiPath, 'utf8')
if (!/export (?:const|async function|function) req/.test(api)) api += '\nexport { req }\n'
writeFileSync(apiPath, api)
const gitPath = path.join(out, 'src/GitWorkspace.tsx')
if (mutate) {
  const source = readFileSync(gitPath, 'utf8')
  const wrong = source.replace(/(?:element|target|e\.currentTarget)\.ownerDocument\.defaultView!/g, 'window')
  if (source === wrong) throw new Error('INERT lexical-window mutation: no owner-window reads found')
  writeFileSync(gitPath, wrong)
}
cpSync('tests/gitpopout-fixture.tsx', path.join(out, 'tests/gitpopout-fixture.tsx'))
await esbuild.build({ entryPoints: [path.join(out, 'tests/gitpopout-fixture.tsx')], bundle: true, outdir: out, format: 'esm', jsx: 'automatic' })
writeFileSync(path.join(out, 'index.html'), '<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/gitpopout-fixture.css"></head><body><div id="root"></div><script type="module" src="/gitpopout-fixture.js"></script></body></html>')
writeFileSync(path.join(out, 'source.json'), JSON.stringify({ git: ref, mutation: mutate, popout: path.resolve('src/popout.tsx') }))
