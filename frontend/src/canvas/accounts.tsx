// canvas/accounts.tsx — the account registry panel (D-144).
//
// Shows which Claude subscriptions this install knows about, in waterfall
// order, and which org is pinned to which. Adoption is PASSIVE: the button
// asks the server to notice whoever is already logged in; nothing here logs
// anybody in, out, or writes the credentials store.
//
// ⚠ THE BANNER IS NOT DECORATION. A panel listing two healthy accounts with a
// primary and a pin reads exactly like a working waterfall, and in Phase 1 it
// is not one — nothing selects an account for a turn. The server says so in
// the payload (`selection_active`) rather than leaving it to the UI to
// remember, and this panel refuses to render a "primary" badge as though it
// were load-bearing while that flag is false. See D-144.
//
// Deliberately its own FILE rather than another export in modals.tsx: two
// peers hold worktrees editing that file right now, and a new file has no
// merge overlap with either.

import { useEffect, useState } from 'react'
import type {
  AccountsPayload, ServingPayload, TokensPayload, ToastFn,
} from '../types'
import {
  adoptAccount, forgetAccountToken, getAccounts, getAccountTokens,
  getServingAccount, putAccountToken, relabelAccount, setAccountOrder,
  setAccountPin,
} from '../api'
import { ago, useEsc } from './shared'

export function AccountsPanel({ slug, toast, close }: {
  /** the org whose pin this panel edits; omit for the registry-only view */
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
      .finally(() => setBusy(false))
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
          ? 'adopted the account currently logged in'
          : 'nothing to adopt — no Claude login found on this host, or the '
            + 'lookup was unreachable'])
      })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => setBusy(false))
  }

  const promote = (uuid: string) => {
    if (!data) return
    const rest = data.accounts.map((a) => a.uuid).filter((u) => u !== uuid)
    run(setAccountOrder([uuid, ...rest]), 'primary changed')
  }

  const saveLabel = (uuid: string) => {
    const label = draft.trim()
    setEditing(null)
    if (!label) return
    run(relabelAccount(uuid, label), 'renamed')
  }

  const pinned = slug && data ? (data.pins[slug] ?? null) : null

  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3>Accounts</h3>

        {/* D-144, stated where it cannot be missed. Trimmed to the bare
            warning (user: drop the explanatory blurbs, 2026-08-24) — but the
            warning itself STAYS while selection_active is false: without it a
            panel of healthy accounts reads as a working waterfall. */}
        {data && !data.selection_active && (
          <div className="ask-warn">
            Registry only — <b>no failover is running yet</b>.
          </div>
        )}

        {err && <div className="ask-warn">could not read the registry: {err}</div>}
        {!data && !err && <div className="dim">reading the registry…</div>}

        {/* ⚠ ALWAYS RENDERED, not only when something is wrong. A state you
            can see only when it breaks is one nobody checks — and this is the
            only way the user can confirm which account is actually serving
            turns without reading logs. The server RESOLVES it from the real
            spawn environment, so it cannot disagree with reality. */}
        {slug && (
          <div className="acct-serving" style={{ marginBottom: 8 }}>
            <b>serving {slug}:</b>{' '}
            {serving
              ? <span title={`resolved: ${serving.serving}`}>{serving.label}</span>
              : <span className="dim">…</span>}
          </div>
        )}

        {data && data.accounts.length === 0 && (
          <div className="dim">No accounts known yet.</div>
        )}

        {data && data.accounts.map((a, i) => (
          <div key={a.uuid} className="acct-row">
            <div className="acct-main">
              {editing === a.uuid ? (
                <input
                  className="grow" autoFocus value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') saveLabel(a.uuid)
                    if (e.key === 'Escape') setEditing(null)
                  }}
                  onBlur={() => saveLabel(a.uuid)} />
              ) : (
                <span
                  className="acct-label"
                  title="click to rename"
                  onClick={() => { setEditing(a.uuid); setDraft(a.label) }}
                >{a.label}</span>
              )}
              {i === 0 && (
                <span className="chip" title={data.selection_active
                  ? 'tried first'
                  : 'first in the intended order — not in effect yet (D-144)'}>
                  {data.selection_active ? 'primary' : 'primary (not in effect)'}
                </span>
              )}
              {slug && pinned === a.uuid &&
                <span className="chip" title={`${slug} is pinned to this account`}>
                  pinned
                </span>}
            </div>
            <div className="dim">
              {a.email_masked ?? 'account'}
              {a.subscription_type ? ` · ${a.subscription_type}` : ''}
              {a.rate_limit_tier ? ` · ${a.rate_limit_tier}` : ''}
              {` · seen ${ago(a.last_seen)} ago`}
            </div>
            <div className="row">
              {i !== 0 && (
                <button disabled={busy} onClick={() => promote(a.uuid)}>
                  make primary
                </button>
              )}
              {/* ⚠ `toks?.tokens?.[...]`, NOT `toks.tokens[...]`. The first
                  version dereferenced `.tokens` on any truthy response and
                  threw when the shape differed — which takes down the WHOLE
                  panel, D-144 banner included, rather than degrading to "no
                  token button". acctpanel.test.tsx caught it: its fetch stub
                  answers every request with the accounts payload, so `toks`
                  arrived without a `.tokens` key. A payload-shape drift or an
                  older backend would do the same thing in production. */}
              {toks?.tokens && (toks.tokens[a.uuid]
                ? <button disabled={busy} title="the CLI cannot show it again — this is a re-mint, not an undo"
                    onClick={() => {
                      setBusy(true)
                      forgetAccountToken(a.uuid)
                        .then((t) => { setToks(t); toast(['token forgotten']) })
                        .catch((e: Error) => toast([e.message]))
                        .finally(() => { setBusy(false); loadServing() })
                    }}>forget token</button>
                : <button disabled={busy}
                    onClick={() => { setPasting(a.uuid); setPaste('') }}>
                    add token
                  </button>)}
              {slug && (pinned === a.uuid
                ? <button disabled={busy}
                    onClick={() => run(setAccountPin(slug, null), 'pin cleared')}>
                    unpin {slug}
                  </button>
                : <button disabled={busy}
                    onClick={() => run(setAccountPin(slug, a.uuid), 'pinned')}>
                    pin {slug} here
                  </button>)}
            </div>
            {pasting === a.uuid && (
              <div className="row">
                {/* ⚠ NO CLIENT-SIDE FORMAT VALIDATION HERE, DELIBERATELY. The
                    CLI shows a minted token exactly once ("you won't be able
                    to see it again"), so anything that could reject the paste
                    before it is durable would destroy the only copy and cost
                    the user a re-mint plus another account-switch window. The
                    server stores first and validates after; the UI must not
                    reintroduce the gate. */}
                <input
                  className="grow" autoFocus type="password"
                  placeholder="paste the token — it is stored before anything checks it"
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
                        toast(['token stored'])
                      })
                      .catch((e: Error) => toast([e.message]))
                      .finally(() => { setBusy(false); loadServing() })
                  }}>store</button>
                <button onClick={() => setPasting(null)}>cancel</button>
              </div>
            )}
          </div>
        ))}

        <div className="row">
          <button className="primary" disabled={busy} onClick={adopt}>
            adopt current login
          </button>
          <span style={{ flex: 1 }} />
          <button onClick={close}>close</button>
        </div>
      </div>
    </div>
  )
}
