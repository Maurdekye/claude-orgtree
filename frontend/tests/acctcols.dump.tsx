// acctcols.dump.tsx — STEP 1 of the accounts-panel column probe.
//
// Renders the REAL <AccountsPanel/> under the repo's jsdom harness and writes
// its outerHTML to a file. It asserts nothing about layout, and deliberately
// so: jsdom implements no CSS box model, so every offsetLeft it reports is 0.
// An alignment assertion here would abstain, and an abstention reads exactly
// like a pass. Step 2 (`acctcols_probe.py`) loads this markup plus the real
// `src/styles.css` into Edge and measures getBoundingClientRect() there.
//
// What this step DOES guarantee is that the markup measured downstream is the
// component's own output — class names, nesting, ghosts and all — rather than
// a hand-copied approximation that could drift from accounts.tsx silently.

import '../tests/harness'
import { writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import type { AccountsPayload } from '../src/types'

const HERE = path.dirname(fileURLToPath(import.meta.url))

// three key rows + the primary + the new-key row: enough that a field column
// which fails to stretch is unmistakable, and one `duplicate` row so the
// greyed variant is measured too.
const PAYLOAD: AccountsPayload = {
  version: 2,
  primary: { signed_in: true, email: 'neoja.dev@gmail.com' },
  keys: [
    { id: 'k1', duplicate: false },
    { id: 'k2', duplicate: true },
    { id: 'k3', duplicate: false },
  ],
  assignments: {
    haiku: { account: 'primary', available: true, refresh_at: null },
    sonnet: { account: 'k1', available: true, refresh_at: null },
    opus: { account: 'k1', available: true, refresh_at: null },
    fable: { account: 'k3', available: false, refresh_at: null },
  },
}

// the panel fetches on mount; answer that one call and nothing else
;(globalThis as unknown as Record<string, unknown>).fetch = (url: string) => {
  if (!/\/accounts$/.test(new URL(String(url), 'http://localhost').pathname)) {
    return Promise.reject(new Error(`unexpected fetch: ${url}`))
  }
  return Promise.resolve({
    ok: true, status: 200, headers: new Headers(),
    json: () => Promise.resolve(PAYLOAD),
  })
}

const main = async () => {
  const { AccountsPanel } = await import('../src/canvas/accounts')
  const { mountView } = await import('../tests/harness')
  const view = await mountView(
    createElement(AccountsPanel, { toast: () => {}, close: () => {} }),
    (el: HTMLElement) => el.innerHTML,
  )
  // let the mount fetch settle, then take the settled markup
  const { flush } = await import('../tests/harness')
  const { act } = await import('react')
  await act(async () => { await flush(8) })
  const html = view.last()

  // ⚠ FAIL LOUD IF THE PANEL NEVER GOT ITS DATA. Without this the dump would
  // be the "reading accounts…" placeholder — no rows, nothing to align — and
  // the downstream probe would happily report "0 misaligned buttons".
  const rows = (html.match(/class="acct-row/g) ?? []).length
  const btns = (html.match(/acct-usage-btn/g) ?? []).length
  if (rows !== 5 || btns !== 4) {
    throw new Error(
      `dump is not the loaded panel: ${rows} rows (want 5), ${btns} usage `
      + `buttons (want 4). First 400 chars:\n${html.slice(0, 400)}`)
  }
  // ⚠ NOT under node_modules: this worktree's node_modules is a junction to
  // the main checkout's, so anything written there is shared with every other
  // worktree. `--out` is passed explicitly by the probe.
  const dest = process.argv[2]
  if (!dest) throw new Error('usage: acctcols.dump <out.html>')
  writeFileSync(dest, html)
  console.log(`dumped ${html.length} bytes, ${rows} rows, ${btns} usage buttons`)
}

await main()
// jsdom's window and the panel's own timers hold the event loop open; this is
// a one-shot dump, so leave rather than wait for them
process.exit(0)
