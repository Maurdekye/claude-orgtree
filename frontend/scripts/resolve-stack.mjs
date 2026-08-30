#!/usr/bin/env node
// Resolves a minified production stack trace back to real source positions
// using the hidden sourcemaps written beside the build (postbuild-
// sourcemaps.mjs moves them to dist/sourcemaps/). Invoked by the Python
// backend (backend/orgtree/crashreports.py) as a subprocess: stdin/stdout are
// JSON so this stays a pure function, independently testable.
//
// Function/component NAMES are left untouched here — vite.config.js sets
// esbuild's keepNames so they're already real in the raw stack; this script's
// only job is turning "bundle.js:1:23456" into "src/App.tsx:452:10".
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { TraceMap, originalPositionFor } from '@jridgewell/trace-mapping'

// V8 stack frame shapes:
//   "    at name (https://host/assets/index-ABC123.js:1:23456)"
//   "    at https://host/assets/index-ABC123.js:1:23456"          (no name)
//   "    at new Foo (https://host/assets/index-ABC123.js:1:23456)"
const FRAME_RE = /^(\s*at\s+)(.+?\s+\()?([^()\s][^()]*?):(\d+):(\d+)\)?\s*$/

function basenameOf(url) {
  try {
    return decodeURIComponent(path.basename(new URL(url, 'http://x.invalid/').pathname))
  } catch {
    return path.basename(url)
  }
}

function loadTracer(mapsDir, cache, fileBase) {
  if (cache.has(fileBase)) return cache.get(fileBase)
  let tracer = null
  try {
    const raw = fs.readFileSync(path.join(mapsDir, `${fileBase}.map`), 'utf8')
    tracer = new TraceMap(JSON.parse(raw))
  } catch {
    tracer = null
  }
  cache.set(fileBase, tracer)
  return tracer
}

function resolveLine(line, mapsDir, cache) {
  const m = FRAME_RE.exec(line)
  if (!m) return line
  const [, prefix, namePart, url, lineStr, colStr] = m
  if (!/^https?:|^\/|\.js$/.test(url)) return line   // not a frame we can map (e.g. "<anonymous>")
  const fileBase = basenameOf(url)
  const tracer = loadTracer(mapsDir, cache, fileBase)
  if (!tracer) return line
  const genLine = Number(lineStr)
  const genCol = Math.max(0, Number(colStr) - 1)   // V8 columns are 1-based; source maps are 0-based
  let pos
  try {
    pos = originalPositionFor(tracer, { line: genLine, column: genCol })
  } catch {
    return line
  }
  if (!pos || pos.line == null || !pos.source) return line
  const src = pos.source.replace(/^.*?[\\/](src[\\/].*)$/, '$1')
  return `${prefix}${namePart || ''}${src}:${pos.line}:${(pos.column ?? 0) + 1})`
}

function resolveStack(stack, mapsDir) {
  const cache = new Map()
  return stack.split('\n').map((l) => resolveLine(l, mapsDir, cache)).join('\n')
}

function main() {
  let input = ''
  process.stdin.setEncoding('utf8')
  process.stdin.on('data', (c) => { input += c })
  process.stdin.on('end', () => {
    let payload
    try {
      payload = JSON.parse(input)
    } catch {
      process.stdout.write(JSON.stringify({ ok: false, stack: '' }))
      return
    }
    if (!payload || typeof payload.stack !== 'string' || typeof payload.mapsDir !== 'string') {
      process.stdout.write(JSON.stringify({ ok: false, stack: '' }))
      return
    }
    try {
      const resolved = resolveStack(payload.stack, payload.mapsDir)
      process.stdout.write(JSON.stringify({ ok: true, stack: resolved }))
    } catch {
      process.stdout.write(JSON.stringify({ ok: false, stack: '' }))
    }
  })
}

// Only run the stdin/stdout CLI when invoked directly — importing this
// module (e.g. from a test) must not attach stdin listeners as a side effect.
if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main()
}

export { resolveStack }
