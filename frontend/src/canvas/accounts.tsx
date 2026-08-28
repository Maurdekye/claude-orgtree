// canvas/accounts.tsx — the accounts panel, rebuilt to the user's 2026-08-25
// spec (machine-local per-model routing). A column of rows:
//
//   · row 1, PRIMARY — whoever Claude Code is signed in as on this machine,
//     shown by EMAIL. Not draggable, not switchable from here: the CLI login
//     is the only mover. A usage button and nothing else.
//   · one row per registered fallback KEY — drag grip (the order IS the
//     routing priority), a greyed-out input whose value is deliberately
//     omitted (the server never returns key material), usage button, delete.
//   · a final row with a live input and a ✓ — paste a `claude setup-token`
//     key to register a new fallback.
//
// The H/S/O/F chips in the left gutter are the whole story: each model
// tier's chip sits beside the row its prompts currently go to — the highest
// row with remaining capacity, resolved by the SERVER from the same state
// the spawn seam reads (`assignments`), so the panel cannot disagree with
// what a turn would actually do. A dimmed chip means no row has capacity;
// it sits where capacity returns first, tooltip saying when.
//
// ⚠ THERE IS NO LONGER A GREYED "same account as the login" ROW (user ruling
// 2026-08-25, retiring an earlier ruling the same day). A `claude setup-token`
// key cannot read its own profile, so the account behind it never resolves and
// the check could only fire for rows carried over from the old registry — a
// guard that fires for one row in a hundred just makes the panel inexplicable.

import { useEffect, useRef, useState } from 'react'
import type {
  AccountsPayload, AccountUsage, ProviderInfo, TierStanding, ToastFn,
  UsageLimit,
} from '../types'
import {
  addAccountKey, deleteAccountKey, getAccounts, getAccountUsage,
  getProviders, setAccountKeyOrder,
} from '../api'
import { CheckIcon, DataUsageIcon, DeleteIcon } from '../icons'
import { TIER_LETTER, TIERS, useEsc } from './shared'

// small local copies of the usage-modal label helpers (App.tsx owns the
// originals beside UsageModal; importing them here would cycle App ↔ panel)
const USAGE_LABEL: Record<string, string> = {
  session: 'session (5hr)',
  weekly_all: 'weekly (7 day)',
}
const usageLabel = (l: UsageLimit): string =>
  l.kind === 'weekly_scoped' && l.model ? `weekly ${l.model}`
    : USAGE_LABEL[l.kind] ?? l.kind.replace(/_/g, ' ')
const usageResets = (iso: string | null): string => {
  if (!iso) return ''
  const ms = new Date(iso).getTime() - Date.now()
  if (!Number.isFinite(ms)) return ''
  if (ms <= 0) return 'resets soon'
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  if (h >= 48) return `resets in ${Math.floor(h / 24)}d ${h % 24}h`
  return h > 0 ? `resets in ${h}h ${m}m` : `resets in ${m}m`
}
/** the wall-clock time a refresh lands, for the "until when" half of the key
 *  rows' standing view. The relative form above answers "how long"; on its own
 *  it is useless for planning past an hour or two, which is exactly the range
 *  a weekly limit sits in. Dated only when it is not today. */
const atClock = (iso: string | null): string => {
  if (!iso) return ''
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return ''
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return d.toDateString() === new Date().toDateString() ? time
    : `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`
}
const sevOf = (l: UsageLimit): '' | 'warn' | 'crit' => {
  const pct = Math.max(0, Math.min(100, l.percent ?? 0))
  return l.severity === 'critical' || pct >= 90 ? 'crit'
    : (l.severity && l.severity !== 'normal') || pct >= 75 ? 'warn' : ''
}

/** a key row's answer in place of percentages (user ruling 2026-08-25): the
 *  routing state this machine holds FOR THAT ACCOUNT — which models can still
 *  run on it, which are spent, and when the spent ones come back. It is the
 *  same `usage_refreshes` dict the router reads, so this view cannot describe
 *  a state a spawn would disagree with.
 *
 *  ⚠ WORD IT AS CAPACITY, NEVER AS ROUTING. "has capacity" is a fact about
 *  this account alone; where a tier actually RUNS is the gutter chips' job,
 *  and the two differ constantly — a fallback has capacity for opus the whole
 *  time opus is happily running on the primary above it. */
export function TierStandings({ tiers }: { tiers: TierStanding[] }) {
  return (
    <div className="acct-tiers">
      {tiers.map((t) => (
        <div className="acct-tier-row" key={t.tier}>
          <span className={'tier t-' + t.tier
            + (t.available ? '' : ' acct-chip-dim')}>
            {TIER_LETTER[t.tier] ?? t.tier.slice(0, 1).toUpperCase()}</span>
          <span className="acct-tier-name">{t.tier}</span>
          {t.available
            ? <span className="acct-tier-ok">has capacity</span>
            : <span className="acct-tier-wait">
              {usageResets(t.refresh_at).replace('resets', 'refreshes')
                || 'refreshes soon'}
              {atClock(t.refresh_at)
                && <span className="dim"> · at {atClock(t.refresh_at)}</span>}
            </span>}
        </div>
      ))}
    </div>
  )
}

/** one account's bars — the same markup family as the header usage modal */
export function UsageBars({ u }: { u: AccountUsage }) {
  // A row that has a standing table shows THE TABLE AND NOTHING ELSE (user
  // ruling 2026-08-25): no note explaining why this row reads differently
  // from the primary's, and no footnote about the shared pool. The table
  // answers the question the button was clicked to ask; prose underneath it
  // was answering a question about our own implementation.
  if (u.tiers?.length) return <TierStandings tiers={u.tiers} />
  // ⚠ …but keep this branch. "CAN'T" AND "DIDN'T" MUST NOT LOOK ALIKE: a
  // setup-token key can never report usage (D-147), and rendering that as the
  // same dim line an outage produces invites the user to keep clicking a
  // button that will never do anything. `unsupported` is a settled fact, so
  // it reads as a note; an error is a condition that might clear, so it keeps
  // the warning styling. Unreachable for a key row today — `account_usage`
  // always sends `tiers` — this catches an account that is unsupported with
  // no standing to show, which would otherwise render as a blank modal.
  if (u.unsupported) {
    return <div className="acct-unsupported">{u.error
      ?? 'usage limits are not available for this kind of key'}</div>
  }
  if (!u.available) {
    return <div className="dim">{u.error ?? 'usage unavailable'}</div>
  }
  return (
    <>
      {u.plan && <div className="dim">Claude {u.plan}</div>}
      {(u.limits ?? []).map((l) => {
        const pct = Math.max(0, Math.min(100, l.percent ?? 0))
        const sev = sevOf(l)
        return (
          <div className="usage-row" key={l.kind + (l.model ?? '')}>
            <div className="u-head">
              <span className="u-label">{usageLabel(l)}</span>
              <span className="u-reset">{usageResets(l.resets_at)}</span>
              <span className={'u-pct' + (sev ? ' ' + sev : '')}>
                {Math.round(l.percent ?? 0)}%</span>
            </div>
            <div className="usage-track">
              <div className={'usage-fill' + (sev ? ' ' + sev : '')}
                style={{ width: pct + '%' }} />
            </div>
          </div>
        )
      })}
      {!(u.limits ?? []).length && <div className="dim">no limits reported</div>}
    </>
  )
}

export function AccountsPanel({ toast, close }: {
  toast: ToastFn
  close: () => void
}) {
  useEsc(close)
  const [data, setData] = useState<AccountsPayload | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState('')
  // which row's usage MODAL is open (user ruling 2026-08-25: a modal, never
  // an inline expansion), and each row's last-fetched bars
  const [usageFor, setUsageFor] = useState<string | null>(null)
  const [usage, setUsage] = useState<Record<string, AccountUsage | 'loading'>>({})
  // ⚠ a REF, not state: dragstart and drop can land in one React batch, and a
  // drop reading the dragged id from its render closure would see the
  // pre-drag null and silently do nothing. The state twin is styling only.
  const dragRef = useRef<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)

  const load = () => getAccounts().then((d) => { setData(d); setErr(null) })
    .catch((e: Error) => setErr(e.message))
  useEffect(() => { void load() }, [])

  // the provider axis (FR-15 preview) — the section heads' install/connect
  // state. NON-FATAL by design: the Claude rows above predate providers and
  // must keep working if this endpoint is missing (an old backend) or slow.
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null)
  useEffect(() => {
    getProviders().then((p) => setProviders(p.providers)).catch(() => {})
  }, [])
  const claudeProv = providers?.find((p) => p.id === 'claude')
  const codex = providers?.find((p) => p.id === 'openai')
  const srcLabel: Record<string, string> = {
    pin: 'private pin', env: 'ORGTREE_CODEX', path: 'on PATH',
  }

  const run = (p: Promise<AccountsPayload>, ok: string) => {
    setBusy(true)
    p.then((d) => { setData(d); if (ok) toast([ok]) })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => setBusy(false))
  }

  const register = () => {
    // ⚠ NO CLIENT-SIDE FORMAT VALIDATION, DELIBERATELY. The CLI shows a
    // minted token exactly once ("you won't be able to see it again"), so
    // anything that could reject the paste before it is durable would
    // destroy the only copy. The server stores first and resolves after.
    const t = draft
    if (!t.trim()) return
    setBusy(true)
    addAccountKey(t)
      .then((d) => { setData(d); setDraft(''); toast(['key registered']) })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => setBusy(false))
  }

  const openUsage = (id: string) => {
    setUsageFor(id)
    setUsage((u) => ({ ...u, [id]: u[id] && u[id] !== 'loading' ? u[id] : 'loading' }))
    getAccountUsage(id)
      .then((r) => setUsage((u) => ({ ...u, [id]: r })))
      .catch((e: Error) => setUsage((u) => ({
        ...u, [id]: { account: id, label: id, available: false, error: e.message },
      })))
  }

  // drop B on A ⇒ B takes A's place in the priority order
  const dropOn = (target: string) => {
    setOverId(null)
    const src = dragRef.current
    if (!src || src === target || !data) return
    const ids = data.keys.map((k) => k.id).filter((i) => i !== src)
    ids.splice(ids.indexOf(target), 0, src)
    run(setAccountKeyOrder(ids), 'order saved')
  }

  /** the left gutter: each tier's chip sits beside the row it routes to */
  const chips = (id: string) => (
    <span className="acct-gutter">
      {TIERS.map((t) => {
        const a = data?.assignments?.[t]
        if (!a || a.account !== id) return null
        const when = usageResets(a.refresh_at)
        return (
          <span key={t}
            className={'tier t-' + t + (a.available ? '' : ' acct-chip-dim')}
            title={a.available
              ? `${t} prompts run on this account`
              : `${t}: no account has capacity right now — it returns here`
                + (when ? ` (${when.replace('resets', 'refreshes')})` : '')}>
            {TIER_LETTER[t]}</span>
        )
      })}
    </span>
  )

  const usageBtn = (id: string, title = 'usage limits') => (
    <button className="acct-btn acct-usage-btn" title={title}
      onClick={() => openUsage(id)}>
      <DataUsageIcon fontSize="inherit" /></button>
  )
  // a key row's button no longer apologises for having nothing — it now opens
  // this machine's own record for that account (user ruling 2026-08-25)
  const KEY_USAGE_TITLE =
    'which models still have capacity on this account, and when the spent '
    + 'ones refresh'

  // the heading follows the CONTENT: a key row shows this machine's capacity
  // record, not usage, and heading that "usage — fallback 1" is what would
  // send a reader hunting for percentages that are never coming
  const isCapacityView = (id: string): boolean => {
    const u = usage[id]
    return !!(u && u !== 'loading' && u.tiers?.length)
  }

  // the row's human name, for the modal header — the payload's own label
  // once it arrives, else derived from position
  const rowLabel = (id: string): string => {
    const u = usage[id]
    if (u && u !== 'loading' && u.label) return u.label
    if (id === 'primary') return data?.primary.email ?? 'primary'
    const i = data?.keys.findIndex((k) => k.id === id) ?? -1
    return i >= 0 ? `fallback ${i + 1}` : id
  }

  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings acct-panel" onClick={(e) => e.stopPropagation()}>
        <h3>Accounts</h3>
        <div className="dim acct-blurb">
          Each model runs on the highest row with remaining capacity — the
          H/S/O/F markers show where each one currently routes. To add a
          fallback account, run <code>claude setup-token</code> in a terminal
          logged into that account and paste the key it prints below.
        </div>

        {err && <div className="ask-warn">could not read accounts: {err}</div>}
        {!data && !err && <div className="dim">reading accounts…</div>}

        {data && (
          <>
            {/* ── provider section: Claude (FR-15 preview) — a head over
                the rows that were the whole panel while Claude was the only
                provider; everything under it is untouched. */}
            <div className="acct-provider-head">
              Claude
              <span className="dim"> · Claude Code
                {claudeProv?.status.version ? ` ${claudeProv.status.version}` : ''}</span>
            </div>

            {/* ── the primary row: the machine's own login ─────────────── */}
            <div className="acct-line">
              {chips('primary')}
              <div className="acct-row acct-primary">
                {/* ⚠ GHOSTS ARE THE ALIGNMENT MECHANISM (user ruling
                    2026-08-25): every row lays out the same four columns —
                    grip · field · usage · delete — and a row missing an
                    element renders an invisible same-width placeholder, so
                    fields and buttons line up exactly across rows. */}
                <div className="acct-main">
                  <span className="acct-grip acct-ghost">⠿</span>
                  <span className="acct-email"
                    title="whoever Claude Code is signed in as on this machine — log in or out with the CLI to change it; it cannot be switched here">
                    {data.primary.signed_in
                      ? (data.primary.email ?? 'signed in')
                      : <span className="dim">not signed in — log in with the Claude CLI</span>}
                  </span>
                  {usageBtn('primary')}
                  <span className="acct-btn acct-ghost" />
                </div>
              </div>
            </div>

            {/* ── one row per registered fallback key ──────────────────── */}
            {data.keys.map((k) => (
              <div className="acct-line" key={k.id}>
                {chips(k.id)}
                <div
                  className={'acct-row acct-key'
                    + (overId === k.id ? ' acct-over' : '')}
                  draggable
                  onDragStart={(e) => {
                    dragRef.current = k.id
                    e.dataTransfer?.setData('text/plain', k.id)
                  }}
                  onDragOver={(e) => { e.preventDefault(); if (overId !== k.id) setOverId(k.id) }}
                  onDragLeave={() => { if (overId === k.id) setOverId(null) }}
                  onDrop={(e) => { e.preventDefault(); dropOn(k.id) }}
                  onDragEnd={() => { dragRef.current = null; setOverId(null) }}
                >
                  <div className="acct-main">
                    <span className="acct-grip" title="drag to reorder — the order is the routing priority">⠿</span>
                    {/* The row's IDENTITY — its account uuid, prefixed with
                        the "fallback N" the desk badge and the usage modal
                        cite (user ruling 2026-08-25). The KEY itself is still
                        never shown: the server does not return key material,
                        not even masked, and a uuid is identity rather than
                        credential. Blank uuid ⇒ the profile lookup has not
                        succeeded yet; it retries lazily, so say so instead of
                        implying the row is broken.
                        ⚠ no `grow` class: `.grow` is not a rule in this sheet
                        (only `.chip.grow` is), and believing it was one is
                        what left the buttons out of column. The field column
                        is stretched by `.acct-panel .acct-main input`. */}
                    <input className="acct-keyfield" disabled
                      value={k.account_uuid
                        ? `fallback ${k.ordinal} · ${k.account_uuid}`
                        : ''}
                      placeholder={`fallback ${k.ordinal} — key registered, `
                        + 'identity not resolved yet'} />
                    {usageBtn(k.id, KEY_USAGE_TITLE)}
                    <button className="acct-btn acct-del"
                      title="delete this account row and forget its key — the CLI cannot show a key again, so re-adding means re-minting"
                      disabled={busy}
                      onClick={() => run(deleteAccountKey(k.id), 'key removed')}>
                      <DeleteIcon fontSize="inherit" /></button>
                  </div>
                </div>
              </div>
            ))}

            {/* ── the new-key row ──────────────────────────────────────── */}
            <div className="acct-line">
              <span className="acct-gutter" />
              <div className="acct-row acct-new">
                <div className="acct-main">
                  <span className="acct-grip acct-ghost">⠿</span>
                  <input type="password" autoComplete="off"
                    placeholder="paste a new key (claude setup-token)"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') register() }} />
                  {/* spans the usage+delete columns of the rows above */}
                  <button className="acct-btn acct-add" title="register this key as a fallback account"
                    disabled={busy || !draft.trim()}
                    onClick={register}>
                    <CheckIcon fontSize="inherit" /></button>
                </div>
              </div>
            </div>

            {/* ── provider section: ChatGPT (Codex) — FR-15 preview.
                Machine-level install/connect state for the Codex CLI, and
                the tier family it will bring. Hiring stays off until the
                provider adapter lands (design §5 Phase 1); the `reason`
                line below is the server's word on what would come next. */}
            <div className="acct-provider-head">
              ChatGPT (Codex)
              <span className="dim"> · Codex CLI
                {codex?.status.version ? ` ${codex.status.version}` : ''}</span>
              <span className="acct-preview-tag">preview</span>
            </div>
            {!codex && (
              <div className="dim acct-prov-note">
                {providers ? 'provider state unavailable' : 'reading provider state…'}
              </div>
            )}
            {codex && (
              <>
                <div className="acct-prov-note">
                  {codex.status.installed
                    ? <>
                      installed
                      {codex.status.source
                        && <span className="dim"> ({srcLabel[codex.status.source]
                          ?? codex.status.source})</span>}
                      {' — '}
                      {codex.status.connected
                        ? <>signed in
                          {codex.status.email && <> as <b>{codex.status.email}</b></>}
                          {codex.status.kind === 'api-key' && <> (API key)</>}
                        </>
                        : 'not signed in'}
                    </>
                    : 'not installed on this machine'}
                </div>
                <div className="acct-prov-tiers">
                  {codex.tiers.map((t) => (
                    <span key={t.tier} className="acct-prov-tier">
                      <span className={'tier t-' + t.tier}>{t.letter}</span>
                      {t.tier} · seat {t.seat}
                      <span className="dim"> · {t.model}</span>
                    </span>
                  ))}
                </div>
                {codex.reason
                  && <div className="dim acct-prov-note">{codex.reason}</div>}
              </>
            )}
          </>
        )}

        <div className="row">
          <span style={{ flex: 1 }} />
          <button onClick={close}>close</button>
        </div>

        {/* one row's usage limits — a MODAL over the panel (user ruling
            2026-08-25), same bar family as the header usage modal */}
        {usageFor && (
          <div className="overlay"
            onClick={(e) => { e.stopPropagation(); setUsageFor(null) }}>
            <div className="settings usage-modal" onClick={(e) => e.stopPropagation()}>
              <h3><DataUsageIcon fontSize="inherit" />{' '}
                {isCapacityView(usageFor) ? 'model capacity' : 'usage'}
                {' — '}{rowLabel(usageFor)}</h3>
              {usage[usageFor] === 'loading' || !usage[usageFor]
                ? <div className="dim">reading usage…</div>
                : <UsageBars u={usage[usageFor] as AccountUsage} />}
              <div className="row">
                <button className="primary" type="button"
                  onClick={() => setUsageFor(null)}>done</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
