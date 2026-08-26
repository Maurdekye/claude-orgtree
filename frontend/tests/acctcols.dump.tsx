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
// which fails to stretch is unmistakable. (There used to be a `duplicate` row
// here so the greyed variant was measured too; that feature was retired
// 2026-08-25 — see accounts.py's module docstring.)
const PAYLOAD: AccountsPayload = {
  version: 2,
  primary: { signed_in: true, email: 'neoja.dev@gmail.com' },
  // real-shaped uuids: the field now RENDERS identity (2026-08-25), so an
  // empty fixture would prove alignment for a case that no longer occurs.
  // k3 deliberately has no uuid — the unresolved-identity placeholder is a
  // different string length and must hold the column too.
  keys: [
    { id: 'k1', ordinal: 1,
      account_uuid: '2d37ed7a-ff4b-4fa5-93da-54b828225866' },
    { id: 'k2', ordinal: 2,
      account_uuid: '9f1c04b6-71ae-4d33-8c02-1b7e5590aa41' },
    { id: 'k3', ordinal: 3, account_uuid: null },
  ],
  assignments: {
    haiku: { account: 'primary', available: true, refresh_at: null },
    sonnet: { account: 'k1', available: true, refresh_at: null },
    opus: { account: 'k1', available: true, refresh_at: null },
    fable: { account: 'k3', available: false, refresh_at: null },
  },
}

// a key row's model-capacity payload, for the optional `--usage` mode below.
// Shaped like the server's: the pooled three parked on one time (user ruling
// 2026-08-25), fable untouched.
const soon = (ms: number) => new Date(Date.now() + ms).toISOString()
const KEY_USAGE = {
  account: 'k1', label: 'fallback 1', available: false, unsupported: true,
  error: 'usage limits can\'t be read for a `claude setup-token` key — these '
    + 'are inference-only, and the usage endpoint needs a permission they are '
    + 'never granted. Nothing is wrong with this key; re-minting it would not '
    + 'help.',
  tiers: [
    { tier: 'haiku', available: false, refresh_at: soon(8_000_000), pool: ['haiku', 'sonnet', 'opus'] },
    { tier: 'sonnet', available: false, refresh_at: soon(8_000_000), pool: ['haiku', 'sonnet', 'opus'] },
    { tier: 'opus', available: false, refresh_at: soon(8_000_000), pool: ['haiku', 'sonnet', 'opus'] },
    { tier: 'fable', available: true, refresh_at: null, pool: null },
  ],
}

// the panel fetches on mount; answer that, plus the per-row usage call the
// `--usage` mode makes, and reject anything else loudly
;(globalThis as unknown as Record<string, unknown>).fetch = (url: string) => {
  const p = new URL(String(url), 'http://localhost').pathname
  const body = /\/accounts$/.test(p) ? PAYLOAD
    : /\/accounts\/usage\/k1$/.test(p) ? KEY_USAGE : null
  if (!body) return Promise.reject(new Error(`unexpected fetch: ${url}`))
  return Promise.resolve({
    ok: true, status: 200, headers: new Headers(),
    json: () => Promise.resolve(body),
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

  // `--usage` opens the first KEY row's model-capacity modal before dumping,
  // so the same maintained fixture can also produce a screenshot of it. The
  // column probe measures the rows behind the overlay either way — this is a
  // second view of one component, not a second copy of its markup.
  if (process.argv.includes('--usage')) {
    const btns = view.el.querySelectorAll<HTMLButtonElement>('.acct-usage-btn')
    if (btns.length < 2) throw new Error('no key-row usage button to click')
    await act(async () => { btns[1]!.click() })
    await act(async () => { await flush(8) })
    if (!view.el.querySelector('.acct-tier-row')) {
      throw new Error('the capacity modal did not render — dump would be a lie')
    }
  }
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
  const dest = process.argv.slice(2).find((a) => !a.startsWith('--'))
  if (!dest) throw new Error('usage: acctcols.dump <out.html> [--usage]')
  writeFileSync(dest, html)
  console.log(`dumped ${html.length} bytes, ${rows} rows, ${btns} usage buttons`)
}

await main()
// jsdom's window and the panel's own timers hold the event loop open; this is
// a one-shot dump, so leave rather than wait for them
process.exit(0)
