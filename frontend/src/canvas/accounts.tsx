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
  getProviders, setAccountKeyOrder, setProviderEnabled,
} from '../api'
import { CheckIcon, DataUsageIcon, DeleteIcon } from '../icons'
import {
  setCrowdPilesOn, setDeskDpi, TIER_LETTER, TIERS, useCrowdPiles,
  useDeskDpi, useEsc,
} from './shared'

// small local copies of the usage-modal label helpers (App.tsx owns the
// originals beside UsageModal; importing them here would cycle App ↔ panel)
const USAGE_LABEL: Record<string, string> = {
  session: 'session (5hr)',
  weekly_all: 'weekly (7 day)',
}
const usageLabel = (l: UsageLimit): string =>
  l.label || (l.kind === 'weekly_scoped' && l.model ? `weekly ${l.model}`
    : USAGE_LABEL[l.kind] ?? l.kind.replace(/_/g, ' ')
  )
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
      {u.plan && <div className="dim">{u.provider ?? 'Claude'} {u.plan}</div>}
      {(u.limits ?? []).map((l) => {
        const pct = Math.max(0, Math.min(100, l.percent ?? 0))
        const sev = sevOf(l)
        return (
          <div className="usage-row"
            key={l.group + l.kind + (l.model ?? '') + (l.label ?? '')}>
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

type AppSettingsTab = 'providers' | 'display'
const APP_TABS: { id: AppSettingsTab; label: string }[] = [
  { id: 'providers', label: 'Providers' },
  { id: 'display', label: 'Display' },
]

function DeskTextSize() {
  const dpi = useDeskDpi()
  const apply = (v: number) => {
    const clamped = Math.min(2.5, Math.max(0.75,
      Math.round(v * 100) / 100))
    setDeskDpi(clamped)
  }
  return (
    <div className="app-pref-row">
      <span className="app-pref-label">desk text size</span>
      <div className="row app-pref-control">
        <button onClick={() => apply(dpi - 0.25)}
          disabled={dpi <= 0.75}>−</button>
        <span className="app-pref-value">{Math.round(dpi * 100)}%</span>
        <button onClick={() => apply(dpi + 0.25)}
          disabled={dpi >= 2.5}>+</button>
        <button onClick={() => apply(1)} disabled={dpi === 1}>reset</button>
      </div>
    </div>
  )
}

function CrowdStackToggle() {
  const on = useCrowdPiles()
  return (
    <label className="checkline app-pref-row app-pref-check">
      <input type="checkbox" checked={on}
        onChange={(e) => setCrowdPilesOn(e.target.checked)} />
      collapse teams with more than 8 active agents into one stack
    </label>
  )
}

function ProviderSwitch({ provider, busy, onChange }: {
  provider: ProviderInfo | undefined
  busy: boolean
  onChange: (provider: ProviderInfo, enabled: boolean) => void
}) {
  if (!provider?.status.installed) return null
  const enabled = provider.user_enabled !== false
  return (
    <label className="provider-switch">
      <input type="checkbox" role="switch" checked={enabled} disabled={busy}
        aria-label={`${provider.label} enabled for new agents`}
        onChange={(e) => onChange(provider, e.target.checked)} />
      <span>{enabled ? 'on' : 'off'}</span>
    </label>
  )
}

export function AccountsPanel({ toast, close }: {
  toast: ToastFn
  close: () => void
}) {
  const [tab, setTab] = useState<AppSettingsTab>('providers')
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])
  const [data, setData] = useState<AccountsPayload | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState('')
  const [mintConfigDir, setMintConfigDir] = useState('')
  // which row's usage MODAL is open (user ruling 2026-08-25: a modal, never
  // an inline expansion), and each row's last-fetched bars
  const [usageFor, setUsageFor] = useState<string | null>(null)
  const [usage, setUsage] = useState<Record<string, AccountUsage | 'loading'>>({})
  // One Escape closes only the topmost layer. Previously the parent panel's
  // listener saw Escape while the nested usage modal was open and removed the
  // entire App settings surface behind it.
  useEsc(() => { if (usageFor) setUsageFor(null); else close() })
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
  const [providerBusy, setProviderBusy] = useState<string | null>(null)
  useEffect(() => {
    getProviders().then((p) => setProviders(p.providers)).catch(() => {})
  }, [])
  const claudeProv = providers?.find((p) => p.id === 'claude')
  const codex = providers?.find((p) => p.id === 'openai')
  const gemini = providers?.find((p) => p.id === 'google')
  // D-202: the SAME verdict the hire chips use, so the accounts page and the
  // canvas cannot disagree about whether a provider exists. Undefined (the
  // payload has not arrived, or an old backend omits the entry) shows the
  // section — see `providerShown` for why unknown is optimistic.
  // Settings is the deliberate exception to provider hiding: an installed
  // provider the user switched off must stay visible here or it can never be
  // switched back on. Truly absent Codex/Gemini remain absent (D-202).
  const codexShown = providers == null || !!codex?.status.installed
  const geminiShown = providers == null || !!gemini?.status.installed
  const srcLabel: Record<string, string> = {
    pin: 'private pin', env: 'ORGTREE_CODEX', path: 'on PATH',
  }

  const run = (p: Promise<AccountsPayload>, ok: string) => {
    setBusy(true)
    p.then((d) => { setData(d); if (ok) toast([ok]) })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => setBusy(false))
  }

  const toggleProvider = (provider: ProviderInfo, enabled: boolean) => {
    setProviderBusy(provider.id)
    setProviderEnabled(provider.id, enabled)
      .then((p) => {
        setProviders(p.providers)
        toast([`${provider.label} turned ${enabled ? 'on' : 'off'}`])
      })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => setProviderBusy(null))
  }

  const moveTab = (from: number, delta: number) => {
    const next = (from + delta + APP_TABS.length) % APP_TABS.length
    setTab(APP_TABS[next]!.id)
    tabRefs.current[next]?.focus()
  }

  const register = () => {
    // ⚠ NO CLIENT-SIDE FORMAT VALIDATION, DELIBERATELY. The CLI shows a
    // minted token exactly once ("you won't be able to see it again"), so
    // anything that could reject the paste before it is durable would
    // destroy the only copy. The server stores first and resolves after.
    const t = draft
    if (!t.trim()) return
    setBusy(true)
    addAccountKey(t, mintConfigDir.trim() || undefined)
      .then((d) => {
        setData(d); setDraft(''); setMintConfigDir(''); toast(['key registered'])
      })
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

  // The isolated probe proves authentication only. A limit response is proof
  // of life but specifically NOT proof that this account can serve now; the
  // separate capacity table remains the routing authority.
  const livenessLabel = (state: AccountsPayload['keys'][number]['liveness']): string => {
    if (state === 'alive') return 'authentication: alive (probe had capacity then; not current capacity)'
    if (state === 'limited') return 'authentication: alive, rate-limited (not serving capacity)'
    if (state === 'dead') return 'authentication: dead (not routed)'
    return 'authentication: no decisive probe result'
  }

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
        <h3>App settings</h3>
        <div className="app-settings-tabs" role="tablist"
          aria-label="App settings sections">
          {APP_TABS.map((item, index) => (
            <button type="button" role="tab" key={item.id}
              ref={(el) => { tabRefs.current[index] = el }}
              id={`app-settings-tab-${item.id}`}
              aria-selected={tab === item.id}
              aria-controls={`app-settings-panel-${item.id}`}
              tabIndex={tab === item.id ? 0 : -1}
              className={'app-settings-tab' + (tab === item.id ? ' on' : '')}
              onClick={() => setTab(item.id)}
              onKeyDown={(e) => {
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                  e.preventDefault(); moveTab(index, 1)
                } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                  e.preventDefault(); moveTab(index, -1)
                } else if (e.key === 'Home' || e.key === 'End') {
                  e.preventDefault()
                  const next = e.key === 'Home' ? 0 : APP_TABS.length - 1
                  setTab(APP_TABS[next]!.id); tabRefs.current[next]?.focus()
                }
              }}>
              {item.label}
              {item.id === 'display'
                && <span className="app-settings-scope">this browser</span>}
            </button>
          ))}
        </div>

        <div id="app-settings-panel-providers" role="tabpanel"
          aria-labelledby="app-settings-tab-providers"
          hidden={tab !== 'providers'} className="app-settings-panel">
          {err && <div className="ask-warn">could not read accounts: {err}</div>}
          {!data && !err && <div className="dim">reading accounts…</div>}

          {data && (
          <>
            {/* ── provider section: Claude (FR-15 preview) — a head over
                the rows that were the whole panel while Claude was the only
                provider; everything under it is untouched. */}
            <div className={'acct-provider-head'
              + (claudeProv?.user_enabled === false ? ' provider-off' : '')}>
              Claude
              <span className="dim"> · Claude Code
                {claudeProv?.status.version ? ` ${claudeProv.status.version}` : ''}</span>
              <ProviderSwitch provider={claudeProv}
                busy={providerBusy === 'claude'} onChange={toggleProvider} />
            </div>
            {/* D-202: Claude is the ONE provider whose absence is reported
                rather than hidden — "since orgtree is built around it, do show
                that its not installed on the accounts page, but make it a very
                small piece of ui" (user, 2026-08-30). So: one dim line under
                the head, here and nowhere else in the app. Deliberately NOT a
                warning banner, an install command or a button — the ask was
                explicitly for something small, and a call to action here would
                compete with every row below it. The rows still render: the
                account list is machine state worth seeing whether or not the
                CLI is on this box.
                Guarded on `claudeProv &&` so an unresolved payload says
                nothing at all — `providerShown`'s optimism in its local form,
                since claiming "not installed" on no evidence is the one
                failure this line could actually cause. */}
            {claudeProv && !claudeProv.status.installed && (
              <div className="dim acct-prov-note">
                {claudeProv.reason
                  ?? 'Claude Code is not installed on this machine'}
              </div>
            )}

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
                        + 'identity not resolved yet'}
                      title={[
                        k.registered_at
                          ? `registered: ${k.registered_at} (lower bound on survival)`
                          : 'registered before provenance recording existed',
                        k.mint_config_dir
                          ? `operator supplied mint config: ${k.mint_config_dir}`
                          : 'mint config dir was not supplied',
                        k.registered_from_config_dir
                          ? `backend registration config: ${k.registered_from_config_dir}`
                          : '',
                        livenessLabel(k.liveness),
                      ].filter(Boolean).join('\n')} />
                    {usageBtn(k.id, KEY_USAGE_TITLE)}
                    <button className="acct-btn acct-del"
                      title="delete this account row and forget its key — the CLI cannot show a key again, so re-adding means re-minting"
                      disabled={busy}
                      onClick={() => run(deleteAccountKey(k.id), 'key removed')}>
                      <DeleteIcon fontSize="inherit" /></button>
                  </div>
                  <div className="acct-provenance">
                    <span>{k.registered_at
                      ? `registered ${k.registered_at}`
                      : 'registered before provenance recording'}</span>
                    {k.mint_config_dir && <span>mint config: {k.mint_config_dir}</span>}
                    {k.registered_from_config_dir &&
                      <span>registration config: {k.registered_from_config_dir}</span>}
                    <span className={k.liveness === 'dead' ? 'acct-dead' : ''}>
                      {livenessLabel(k.liveness)}
                    </span>
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
                <div className="acct-provenance acct-mint-entry">
                  <input className="acct-mint-input" autoComplete="off"
                    placeholder="mint config directory (optional)"
                    value={mintConfigDir}
                    onChange={(e) => setMintConfigDir(e.target.value)} />
                  <span>Only enter the directory actually used to mint this key; leave blank if unknown.</span>
                </div>
              </div>
            </div>

            {/* ── provider section: ChatGPT (Codex) — FR-15 preview.
                Machine-level install/connect state for the Codex CLI, and
                the tier family it will bring. Hiring stays off until the
                provider adapter lands (design §5 Phase 1); the `reason`
                line below is the server's word on what would come next. */}
            {/* "Codex" (user ruling 2026-08-28, ask card) — the CLI's own
                name is the provider's UI name.
                ⚠ D-202: THE WHOLE SECTION IS CONDITIONAL. A Codex that is not
                installed produces no head, no note, no tier list and no
                preview tag — the accounts page is not an exception to "absent
                means absent", and this was the surface most likely to become
                one, because until now it was the designated home for the
                "here is how to install it" story. The user overruled that:
                an uninstalled provider is not part of the product until it is
                installed. Installed-but-signed-out is untouched and still
                renders in full, reason and all. */}
            {codexShown && <>
            <div className={'acct-provider-head prov-openai'
              + (codex?.user_enabled === false ? ' provider-off' : '')}>
              Codex
              <span className="dim"> · Codex CLI
                {codex?.status.version ? ` ${codex.status.version}` : ''}</span>
              {codex?.user_enabled !== false && !codex?.hire_enabled
                && <span className="acct-preview-tag">preview</span>}
              <ProviderSwitch provider={codex}
                busy={providerBusy === 'openai'} onChange={toggleProvider} />
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
                {codex.reason && codex.user_enabled !== false
                  && <div className="dim acct-prov-note">{codex.reason}</div>}
              </>
            )}
            </>}

            {/* ── provider section: Gemini (D-189) — the same machine-level
                install/connect surface, the CLI's own name as the label.
                The preview tag only while hiring is actually off.
                D-202 hides it whole when absent, exactly as Codex above. */}
            {geminiShown && <>
            <div className={'acct-provider-head prov-google'
              + (gemini?.user_enabled === false ? ' provider-off' : '')}>
              Gemini
              <span className="dim"> · Gemini CLI
                {gemini?.status.version ? ` ${gemini.status.version}` : ''}</span>
              {gemini?.user_enabled !== false && !gemini?.hire_enabled
                && <span className="acct-preview-tag">preview</span>}
              <ProviderSwitch provider={gemini}
                busy={providerBusy === 'google'} onChange={toggleProvider} />
            </div>
            {!gemini && (
              <div className="dim acct-prov-note">
                {providers ? 'provider state unavailable' : 'reading provider state…'}
              </div>
            )}
            {gemini && (
              <>
                <div className="acct-prov-note">
                  {gemini.status.installed
                    ? <>
                      installed
                      {gemini.status.source
                        && <span className="dim"> ({srcLabel[gemini.status.source]
                          ?? gemini.status.source})</span>}
                      {' — '}
                      {gemini.status.connected
                        ? <>signed in
                          {gemini.status.email && <> as <b>{gemini.status.email}</b></>}
                          {gemini.status.kind === 'api-key' && <> (API key)</>}
                          {gemini.status.kind === 'vertex' && <> (Vertex AI)</>}
                        </>
                        : 'not signed in'}
                    </>
                    : 'not installed on this machine'}
                </div>
                <div className="acct-prov-tiers">
                  {gemini.tiers.map((t) => (
                    <span key={t.tier} className="acct-prov-tier">
                      <span className={'tier t-' + t.tier}>{t.letter}</span>
                      {t.tier} · seat {t.seat}
                      <span className="dim"> · {t.model}</span>
                    </span>
                  ))}
                </div>
                {gemini.reason && gemini.user_enabled !== false
                  && <div className="dim acct-prov-note">{gemini.reason}</div>}
              </>
            )}
            </>}
          </>
        )}
        </div>

        <div id="app-settings-panel-display" role="tabpanel"
          aria-labelledby="app-settings-tab-display"
          hidden={tab !== 'display'} className="app-settings-panel">
          <DeskTextSize />
          <CrowdStackToggle />
        </div>

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
