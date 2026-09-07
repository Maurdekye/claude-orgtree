import './harness'
import { strict as assert } from 'node:assert'
import { test, mock } from 'node:test'
import { observeGit, refreshGitPreferences } from '../src/git/observers'
import { setGitPeriodicFetchEnabled } from '../src/api'

test('open views share cadence, synchronize global changes, and stop on last close', async () => {
  const timers = new Set<() => void>()
  let now = 0, enabled = false
  const calls: { path: string; method: string; body?: string }[] = []
  mock.method(globalThis, 'setInterval', (callback: () => void, delay: number) => {
    assert.equal(delay, 5000); timers.add(callback); return callback
  })
  mock.method(globalThis, 'clearInterval', (callback: () => void) => timers.delete(callback))
  mock.method(performance, 'now', () => now)
  mock.method(globalThis, 'fetch', async (path: string, options?: RequestInit) => {
    const method = options?.method ?? 'GET'
    calls.push({ path, method, body: options?.body as string })
    if (path === '/api/app-settings/runtime' && method === 'PUT') {
      enabled = JSON.parse(options!.body as string).git_periodic_fetch_enabled
      return Response.json({ git_periodic_fetch_enabled: enabled })
    }
    if (path.endsWith('/observation')) return Response.json({ busy: false, freshness: { watched: enabled } })
    if (path.endsWith('/watch') && method === 'POST') return Response.json({ started: true })
    throw Error(`INERT: unexpected request ${method} ${path}`)
  })
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
  const tick = async (at: number) => { now = at; for (const cb of timers) cb(); await flush() }
  const seen: boolean[][] = [[], [], []]
  const listener = (index: number) => ({ value: (v: { freshness?: { watched: boolean } }) => seen[index]!.push(v.freshness!.watched), error: (e: unknown) => { throw e } })
  const closeOne = observeGit('org', 'repo', listener(0))
  const closeTwo = observeGit('org', 'repo', listener(1))
  const closeOther = observeGit('org', 'other', listener(2))
  try {
    assert.equal(timers.size, 2)
    await tick(5000)
    assert.equal(calls.filter(c => c.path.endsWith('/observation')).length, 2)
    assert.equal(calls.filter(c => c.path.endsWith('/watch')).length, 0)
    await setGitPeriodicFetchEnabled(true); refreshGitPreferences(); await flush()
    assert.deepEqual(seen.map(v => v.at(-1)), [true, true, true])
    assert.equal(calls.filter(c => c.path.endsWith('/watch')).length, 2)
    await tick(10000)
    assert.equal(calls.filter(c => c.path.endsWith('/watch')).length, 2)
    closeOne(); assert.equal(timers.size, 2)
    await tick(35000)
    assert.equal(calls.filter(c => c.path.endsWith('/watch')).length, 4)
    await setGitPeriodicFetchEnabled(false); refreshGitPreferences(); await flush()
    assert.equal(seen[1]!.at(-1), false)
    await tick(70000)
    assert.equal(calls.filter(c => c.path.endsWith('/watch')).length, 4)
    closeTwo(); closeOther(); assert.equal(timers.size, 0)
    const before = calls.length
    await tick(200000); refreshGitPreferences(); await flush()
    assert.equal(calls.length, before, 'closed repositories cannot poll or request periodic fetches')
  } finally { closeOne(); closeTwo(); closeOther(); mock.restoreAll() }
})

test('a late enabled observation cannot start a fetch after the last view closes', async () => {
  let tick: (() => void) | undefined, answer: ((r: Response) => void) | undefined
  const calls: string[] = []
  mock.method(globalThis, 'setInterval', (cb: () => void) => { tick = cb; return 1 })
  mock.method(globalThis, 'clearInterval', () => {})
  mock.method(globalThis, 'fetch', (path: string) => {
    calls.push(path)
    return new Promise<Response>(resolve => { answer = resolve })
  })
  let delivered = 0
  const close = observeGit('late', 'repo', { value: () => { delivered++ }, error: e => { throw e } })
  try {
    tick!(); assert.equal(calls.length, 1, 'positive observation control')
    close(); answer!(Response.json({ freshness: { watched: true } }))
    for (let i = 0; i < 20; i++) await Promise.resolve()
    assert.equal(delivered, 0)
    assert.equal(calls.length, 1, 'no POST from a closed panel')
  } finally { close(); mock.restoreAll() }
})
