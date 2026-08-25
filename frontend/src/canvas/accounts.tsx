// canvas/accounts.tsx — the accounts panel, redesigned to the user's spec
// (2026-08-24): "minimal, comprehensible, show only what's necessary". ONE
// fact up top — which account is serving turns — and ONE list: the fallback
// keys, in the order failover will try them, drag to reorder, removable.
//
// Gone deliberately: the pin surface (the failover code never reads pins —
// zero references, measured 2026-08-24) and the registry-browsing framing.
// The registry itself is untouched and identity-only; keys live in their own
// store, and this panel only ever learns PRESENCE, never content.
//
// ⚠ THERE IS DELIBERATELY NO STATUS BANNER. The old "Registry only — no
// failover is running yet" line was gated on `selection_active`, a D-144-era
// field then hardcoded to FALSE — it stopped tracking reality the night
// failover first fired (2026-08-24), at which point the banner stated the
// exact opposite of the truth. The field is DERIVED since 2026-08-25 (any
// registered account holds a key) but is machine-wide, not a per-org fact:
// nothing user-visible may key on it. The serving line below is resolved
// from the real spawn environment and IS the status statement. Absence is
// pinned by acctpanel.test.tsx §1/§2.
//
// ⚠ THE DRAG IS A REAL CONTROL, not cosmetics: failover walks the registry
// order and takes the first account that isn't currently serving and has a
// stored key. The submitted order is [active account, …keys as displayed];
// the server appends anything omitted (proven: "set_order cannot delete an
// omitted account"), so key-less registrations survive without being listed.

import { useEffect, useRef, useState } from 'react'
import type {
  AccountsPayload, AccountEntry, ServingPayload, TokensPayload, ToastFn,
} from '../types'
import {
  adoptAccount, forgetAccountToken, getAccounts, getAccountTokens,
  getServingAccount, putAccountSelection, putAccountToken, relabelAccount,
  setAccountOrder,
} from '../api'
import { ago, useEsc } from './shared'

export function AccountsPanel({ slug, toast, close }: {
  /** the org whose serving account is resolved; omit for the bare list */
  slug?: string
  toast: ToastFn
  close: () => void
}) {
  useEsc(close)
  const [data, setData] = useState<AccountsPayload | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [toks, setToks] = useState<TokensPayload | null>(null)
  const [serving, setServing] = useState<ServingPayload | null>(null)
  const [pasting, setPasting] = useState<string | null>(null)
  const [paste, setPaste] = useState('')
  // ⚠ a REF, not state: dragstart and drop can land in one React batch (they
  // do under act() in tests, and nothing forbids it in a browser), and a drop
  // reading the dragged id from its render closure would see the pre-drag
  // null and silently do nothing. The state twin exists only for styling.
  const dragRef = useRef<string | null>(null)
  const [overU, setOverU] = useState<string | null>(null)

  const load = () => getAccounts().then(setData)
    .catch((e: Error) => setErr(e.message))
  const loadToks = () => getAccountTokens().then(setToks).catch(() => {})
  // ⚠ re-read after EVERY mutation. The serving account is the one fact on
  // this panel a user will act on, and a stale one is worse than none.
  const loadServing = () => {
    if (!slug) return
    getServingAccount(slug).then(setServing).catch(() => {})
  }
  useEffect(() => { void load(); void loadToks(); void loadServing() }, [])

  const run = (p: Promise<AccountsPayload>, ok: string) => {
    setBusy(true)
    p.then((d) => { setData(d); toast([ok]) })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => { setBusy(false); loadServing() })
  }

  const adopt = () => {
    setBusy(true)
    adoptAccount()
      .then((d) => {
        setData(d)
        // `adopted: null` is a NORMAL outcome, not a failure — nobody is
        // logged in, or the identity lookup was unreachable. Saying so beats
        // a silent no-op that looks like a broken button.
        toast([d.adopted
          ? 'registered the account currently logged in'
          : 'nothing to register — no Claude login found on this host, or '
            + 'the lookup was unreachable'])
      })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => { setBusy(false); loadServing() })
  }

  const saveLabel = (uuid: string) => {
    const label = draft.trim()
    setEditing(null)
    if (!label) return
    run(relabelAccount(uuid, label), 'renamed')
  }

  // ⚠ `toks?.tokens?.[…]`, never a bare deref: a response of an unexpected
  // shape must degrade to "no key", not throw during render and blank the
  // whole panel, banner included (acctpanel §3 caught exactly that crash).
  const hasKey = (uuid: string) => !!toks?.tokens?.[uuid]

  // the serving value is a registry uuid, or "ambient"/"api-key"/
  // "token:unattributed" — only a uuid maps onto a listed account
  const activeUuid =
    serving && data?.accounts.some((a) => a.uuid === serving.serving)
      ? serving.serving : null
  // the STORED INTENT beside the resolved fact; an older backend omits it
  const selection = serving?.selection ?? null

  // ⚠ the PUT's response IS the new resolved truth (same shape as the read) —
  // set it directly rather than racing a re-fetch that could answer stale
  const select = (uuid: string | null) => {
    if (!slug) return
    setBusy(true)
    putAccountSelection(slug, uuid)
      .then((s) => { setServing(s); toast([`serving ${s.label}`]) })
      // a refused selection that says nothing reads exactly like one that
      // worked — the 422 detail names the reason and must reach the user
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => setBusy(false))
  }

  const keys: AccountEntry[] =
    data?.accounts.filter((a) => a.uuid !== activeUuid && hasKey(a.uuid)) ?? []
  const keyless: AccountEntry[] =
    data?.accounts.filter((a) => a.uuid !== activeUuid && !hasKey(a.uuid)) ?? []

  const commitOrder = (ks: string[]) => {
    const head = activeUuid ? [activeUuid] : []
    run(setAccountOrder([...head, ...ks]), 'order saved')
  }

  // drop B on A ⇒ B takes A's place in the tried-order
  const dropOn = (target: string) => {
    setOverU(null)
    const src = dragRef.current
    if (!src || src === target) return
    const ks = keys.map((k) => k.uuid).filter((u) => u !== src)
    ks.splice(ks.indexOf(target), 0, src)
    commitOrder(ks)
  }

  const label = (a: AccountEntry) => editing === a.uuid
    ? <input
        className="grow" autoFocus value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') saveLabel(a.uuid)
          if (e.key === 'Escape') setEditing(null)
        }}
        onBlur={() => saveLabel(a.uuid)} />
    : <span
        className="acct-label" title="click to rename"
        onClick={() => { setEditing(a.uuid); setDraft(a.label) }}
      >{a.label}</span>

  const identity = (a: AccountEntry) => (
    <span className="dim">
      {' '}{a.email_masked ?? ''}
      {a.subscription_type ? ` · ${a.subscription_type}` : ''}
      {a.last_seen ? ` · seen ${ago(a.last_seen)} ago` : ''}
    </span>
  )

  const pasteRow = (a: AccountEntry) => pasting === a.uuid && (
    <div className="row">
      {/* ⚠ NO CLIENT-SIDE FORMAT VALIDATION HERE, DELIBERATELY. The CLI
          shows a minted token exactly once ("you won't be able to see it
          again"), so anything that could reject the paste before it is
          durable would destroy the only copy and cost the user a re-mint
          plus another account-switch window. The server stores first and
          validates after; the UI must not reintroduce the gate. */}
      <input
        className="grow" autoFocus type="password"
        placeholder="paste the key — it is stored before anything checks it"
        value={paste}
        onChange={(e) => setPaste(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') setPasting(null) }} />
      <button
        className="primary" disabled={busy || !paste}
        onClick={() => {
          setBusy(true)
          putAccountToken(a.uuid, paste)
            .then((t) => {
              setToks(t); setPasting(null); setPaste('')
              toast(['key stored'])
            })
            .catch((e: Error) => toast([e.message]))
            .finally(() => { setBusy(false); loadServing() })
        }}>store</button>
      <button onClick={() => setPasting(null)}>cancel</button>
    </div>
  )

  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3>Accounts</h3>

        {err && <div className="ask-warn">could not read the registry: {err}</div>}
        {!data && !err && <div className="dim">reading the registry…</div>}

        {/* ⚠ ALWAYS RENDERED when an org is open, not only when something is
            wrong. A state you can see only when it breaks is one nobody
            checks — and this is the only way the user can confirm which
            account is actually serving turns without reading logs. The
            server RESOLVES it from the real spawn environment, so it cannot
            disagree with reality. */}
        {slug && (
          <div className="acct-serving">
            <b>serving {slug}:</b>{' '}
            {serving
              ? <span title={`resolved: ${serving.serving}`}>{serving.label}</span>
              : <span className="dim">…</span>}
          </div>
        )}

        {/* the way BACK — rendered only while an intent is stored, because a
            "return to my login" control with nothing to clear presents the
            default as if it were a choice. When the stored intent is not what
            resolution answered (its key was removed later), SAY so: stating
            the intention as the state is the removed banner's bug again. */}
        {slug && selection !== null && (
          <div className="row">
            {serving && serving.serving !== selection && (
              <span className="dim">
                stored selection is not in effect — that account can no longer
                serve (no stored key)
              </span>
            )}
            <button
              disabled={busy}
              title="clears the stored selection — turns then authenticate as whatever this machine is signed in to"
              onClick={() => select(null)}>
              serve from the account I'm signed in as
            </button>
          </div>
        )}

        {data && data.accounts.length === 0 && (
          <div className="dim">No accounts known yet.</div>
        )}

        {keys.length > 0 && (
          <>
            <div className="dim acct-head">fallback keys — tried in this order</div>
            {keys.map((a) => (
              <div
                key={a.uuid}
                className={'acct-row acct-key' + (overU === a.uuid ? ' acct-over' : '')}
                draggable
                onDragStart={(e) => {
                  dragRef.current = a.uuid
                  e.dataTransfer?.setData('text/plain', a.uuid)
                }}
                onDragOver={(e) => { e.preventDefault(); if (overU !== a.uuid) setOverU(a.uuid) }}
                onDragLeave={() => { if (overU === a.uuid) setOverU(null) }}
                onDrop={(e) => { e.preventDefault(); dropOn(a.uuid) }}
                onDragEnd={() => { dragRef.current = null; setOverU(null) }}
              >
                <div className="acct-main">
                  <span className="acct-grip" title="drag to reorder">⠿</span>
                  {label(a)}
                  {identity(a)}
                  <span style={{ flex: 1 }} />
                  <button
                    disabled={busy}
                    title="serve this org from this account — stored as a selection; the serving line above stays the resolved fact"
                    onClick={() => select(a.uuid)}>serve from this account</button>
                  <button
                    disabled={busy}
                    title="forgets the stored key — the CLI cannot show it again, so re-adding means re-minting"
                    onClick={() => {
                      setBusy(true)
                      forgetAccountToken(a.uuid)
                        .then((t) => { setToks(t); toast(['key removed']) })
                        .catch((e: Error) => toast([e.message]))
                        .finally(() => { setBusy(false); loadServing() })
                    }}>remove key</button>
                </div>
                {/* per-key usage limits render here once the cached
                    GET /api/accounts/usage exists (backend piece, in flight
                    separately); absence must stay silent, never an error */}
              </div>
            ))}
          </>
        )}

        {keyless.length > 0 && (
          <>
            <div className="dim acct-head">registered, no key</div>
            {keyless.map((a) => (
              <div key={a.uuid} className="acct-row acct-nokey">
                <div className="acct-main">
                  {label(a)}
                  {identity(a)}
                  <span style={{ flex: 1 }} />
                  <button disabled={busy}
                    onClick={() => { setPasting(a.uuid); setPaste('') }}>
                    add key
                  </button>
                </div>
                {pasteRow(a)}
              </div>
            ))}
          </>
        )}

        <div className="row">
          <button className="primary" disabled={busy} onClick={adopt}>
            register current login
          </button>
          <span style={{ flex: 1 }} />
          <button onClick={close}>close</button>
        </div>
      </div>
    </div>
  )
}
