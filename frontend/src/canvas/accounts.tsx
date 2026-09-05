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
  AccountsPayload, AccountUsage, ProviderInfo, RuntimeSettingsPayload,
  TierStanding, ToastFn, UsageLimit,
} from '../types'
import {
  addAccountKey, deleteAccountKey, getAccounts, getAccountUsage,
  getProviders, getRuntimeSettings, setAccountKeyOrder,
  setIdleDocketRemindersEnabled, setProviderEnabled,
  setWaitForMcpToolsEnabled, setWarmingEnabled, setWorkingCheckupsEnabled,
} from '../api'
import { CheckIcon, DataUsageIcon, DeleteIcon } from '../icons'
import {
  SetGroup, SetRow, SettingsTabPanel, SettingsTabs, SetToggle,
} from './settingskit'
import type { SettingsTab } from './settingskit'
import { OpenRouterSection } from './openrouter'
import {
  setCrowdPilesOn, setDeskDpi, setOpenRouterTiers, setStartView, setStartZoomOn,
  fmtCredits, TIER_LETTER,
  TIERS, useCrowdPiles, useDeskDpi, useEsc, useStartView, useStartZoom,
} from './shared'
import { fmtFull, fmtWhen } from '../timefmt'
import type { StartView } from './shared'

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
const atClock = (iso: string | null): string => fmtWhen(iso)
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
        // `percent: null` is a real state (UsageLimit's own type), not an
        // absent 0 — OpenRouter reports it for an uncapped key, where the
        // dollar figure rides `label` instead: a bar reading 0% would claim
        // nothing has been spent, which is exactly the fabrication the task
        // must not make. No bar, no badge; the label carries the fact.
        const known = l.percent != null
        const pct = Math.max(0, Math.min(100, l.percent ?? 0))
        const sev = sevOf(l)
        return (
          <div className="usage-row"
            key={l.group + l.kind + (l.model ?? '') + (l.label ?? '')}>
            <div className="u-head">
              <span className="u-label">{usageLabel(l)}</span>
              <span className="u-reset">{usageResets(l.resets_at)}</span>
              {known && <span className={'u-pct' + (sev ? ' ' + sev : '')}>
                {Math.round(l.percent ?? 0)}%</span>}
            </div>
            {known && <div className="usage-track">
              <div className={'usage-fill' + (sev ? ' ' + sev : '')}
                style={{ width: pct + '%' }} />
            </div>}
          </div>
        )
      })}
      {!(u.limits ?? []).length && <div className="dim">no limits reported</div>}
    </>
  )
}

type AppSettingsTab = 'providers' | 'runtime' | 'display'
const APP_TABS: SettingsTab<AppSettingsTab>[] = [
  { id: 'providers', label: 'Providers' },
  { id: 'runtime', label: 'Runtime' },
  { id: 'display', label: 'Display', note: 'this browser' },
]

function DeskTextSize() {
  const dpi = useDeskDpi()
  const apply = (v: number) => {
    const clamped = Math.min(2.5, Math.max(0.75,
      Math.round(v * 100) / 100))
    setDeskDpi(clamped)
  }
  return (
    <SetRow label="desk text size"
      hint={'scales agent desks, cards and canvas type. '
        + 'Panels like this one keep their own size.'}>
      <button aria-label="smaller desk text" onClick={() => apply(dpi - 0.25)}
        disabled={dpi <= 0.75}>−</button>
      <span className="set-value" aria-live="polite">
        {Math.round(dpi * 100)}%</span>
      <button aria-label="larger desk text" onClick={() => apply(dpi + 0.25)}
        disabled={dpi >= 2.5}>+</button>
      <button onClick={() => apply(1)} disabled={dpi === 1}>reset</button>
    </SetRow>
  )
}

function CrowdStackToggle() {
  const on = useCrowdPiles()
  return (
    <SetToggle label="collapse crowded teams into one stack" checked={on}
      onChange={setCrowdPilesOn}
      hint={'a team with more than 8 active agents draws as a single '
        + 'stack instead of 8+ separate cards'} />
  )
}

/* D-228: where an org opens, and whether the camera glides there. Two rows
   because they are two decisions — a destination and a manner — and the
   second is moot under "where I left off", which the toggle says by going
   inert (disabled, hint rewritten) rather than by vanishing: a control that
   disappears reads as a bug, one that explains itself reads as a rule. */
const START_VIEW_OPTIONS: [StartView, string][] = [
  ['org', 'the full org'],
  ['switchboard', 'the switchboard'],
  ['remember', 'where I left off'],
]
function StartupView() {
  const mode = useStartView()
  const zoom = useStartZoom()
  const remember = mode === 'remember'
  return (
    <>
      <SetRow label="open an org at"
        hint={remember
          ? 'the camera comes back exactly where it was in that org. A brand-new '
            + 'org, with nowhere to come back to, plays the starting zoom once.'
          : mode === 'switchboard'
            ? 'straight to the eye’s desk, every agent in one row'
            : 'the whole tree, fitted to the window'}>
        <select aria-label="open an org at" value={mode}
          onChange={(e) => setStartView(e.target.value as StartView)}>
          {START_VIEW_OPTIONS.map(([v, label]) =>
            <option key={v} value={v}>{label}</option>)}
        </select>
      </SetRow>
      <SetToggle label="play the starting zoom" checked={zoom}
        disabled={remember}
        title={remember ? 'not used by “where I left off”' : undefined}
        onChange={setStartZoomOn}
        hint={remember
          ? 'not used by “where I left off” — a restored view never glides'
          : 'wakes on the eye, then glides out to the org (or in to the switchboard)'} />
    </>
  )
}

function ProviderSwitch({ provider, busy, onChange }: {
  provider: ProviderInfo | undefined
  busy: boolean
  onChange: (provider: ProviderInfo, enabled: boolean) => void
}) {
  if (!provider || (!provider.status.installed && provider.user_enabled !== false))
    return null
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
  // the OpenRouter model picker is a third layer (2026-09-02): same rule,
  // one Escape closes only the topmost
  const [orrPicker, setOrrPicker] = useState(false)
  useEsc(() => {
    if (usageFor) setUsageFor(null)
    else if (orrPicker) setOrrPicker(false)
    else close()
  })
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
  const [warming, setWarming] = useState<boolean | null>(null)
  const [warmingBusy, setWarmingBusy] = useState(false)
  const [workingCheckups, setWorkingCheckups] = useState<boolean | null>(null)
  const [workingCheckupsBusy, setWorkingCheckupsBusy] = useState(false)
  const [waitForMcpTools, setWaitForMcpTools] = useState<boolean | null>(null)
  const [waitForMcpToolsBusy, setWaitForMcpToolsBusy] = useState(false)
  const [docketReminders, setDocketReminders] = useState<boolean | null>(null)
  const [docketRemindersBusy, setDocketRemindersBusy] = useState(false)
  const [warmingErr, setWarmingErr] = useState<string | null>(null)
  // the OpenRouter entry carries the runtime tiers (favorites); adopting them
  // here colours this panel's own chips even before the canvas has polled
  const adoptProviders = (list: ProviderInfo[]) => {
    setProviders(list)
    setOpenRouterTiers(list.find((p) => p.id === 'openrouter')?.tiers)
  }
  const refetchProviders = () =>
    getProviders().then((p) => adoptProviders(p.providers)).catch(() => {})
  useEffect(() => { void refetchProviders() }, [])
  useEffect(() => {
    getRuntimeSettings()
      .then((p) => {
        setWarming(p.warming_enabled)
        setWorkingCheckups(p.working_checkups_enabled !== false)
        setWaitForMcpTools(p.wait_for_mcp_tools_enabled === true)
        setDocketReminders(p.idle_docket_reminders_enabled === true)
        setWarmingErr(null)
      })
      .catch((e: Error) => setWarmingErr(e.message))
  }, [])
  /** D-222 — every Runtime switch does the same five things: raise its own
   *  busy flag, PUT, adopt EVERY value from the reply (the endpoint answers
   *  with the whole record, so a sibling cannot linger stale), clear the
   *  error, toast. Written once because it was written three times, and the
   *  three copies had already drifted: two of them adopted the reply's
   *  booleans raw while the load effect and the third normalised them, so a
   *  backend that omitted a field could leave one row showing a different
   *  default from the one the checkbox was reading. The normalisation here is
   *  the load effect's, exactly — default-on for warming and checkups,
   *  default-off for the MCP wait and the docket reminder. */
  const runtimeSwitch = (
    put: (v: boolean) => Promise<RuntimeSettingsPayload>,
    setBusy: (b: boolean) => void,
    say: (p: RuntimeSettingsPayload) => string,
  ) => (next: boolean) => {
    setBusy(true)
    put(next)
      .then((p) => {
        setWarming(p.warming_enabled)
        setWorkingCheckups(p.working_checkups_enabled !== false)
        setWaitForMcpTools(p.wait_for_mcp_tools_enabled === true)
        setDocketReminders(p.idle_docket_reminders_enabled === true)
        setWarmingErr(null)
        toast([say(p)])
      })
      .catch((runtimeErr: Error) => {
        setWarmingErr(runtimeErr.message)
        toast([`error: ${runtimeErr.message}`])
      })
      .finally(() => setBusy(false))
  }
  const claudeProv = providers?.find((p) => p.id === 'claude')
  const codex = providers?.find((p) => p.id === 'openai')
  const antigravity = providers?.find((p) => p.id === 'google')
  const openrouter = providers?.find((p) => p.id === 'openrouter')
  // D-202: the SAME verdict the hire chips use, so the accounts page and the
  // canvas cannot disagree about whether a provider exists. Undefined (the
  // payload has not arrived, or an old backend omits the entry) shows the
  // section — see `providerShown` for why unknown is optimistic.
  // Settings is the deliberate exception to provider hiding: an installed
  // provider the user switched off must stay visible here or it can never be
  // switched back on. Truly absent Codex/Antigravity remain absent (D-202).
  const codexShown = codex == null || !!codex.status.installed
    || codex.user_enabled === false
  const antigravityShown = antigravity == null || !!antigravity.status.installed
    || antigravity.user_enabled === false
  // how a CLI was found, per provider: the env override is named after the
  // provider's own variable, and the Antigravity CLI has no npm pin — its
  // installer's own location is the resolver's first stop
  const srcLabelOf = (envVar: string): Record<string, string> => ({
    pin: 'private pin', install: 'installer location', env: envVar,
    path: 'on PATH',
  })

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
        adoptProviders(p.providers)
        toast([`${provider.label} turned ${enabled ? 'on' : 'off'}`])
      })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => setProviderBusy(null))
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
        <SettingsTabs tabs={APP_TABS} tab={tab} setTab={setTab}
          idBase="app-settings" label="App settings sections" />

        <SettingsTabPanel id="providers" idBase="app-settings"
          active={tab === 'providers'}>
          {err && <div className="ask-warn">could not read accounts: {err}</div>}
          {!data && !err && <div className="dim">reading accounts…</div>}

          {data && (
          <>
            {/* ── provider section: Claude (FR-15 preview) — a head over
                the rows that were the whole panel while Claude was the only
                provider; everything under it is untouched.
                D-222: each vendor's head and rows are wrapped in a
                `.set-group`, so the panel's gap is the space BETWEEN vendors
                and the rows inside one vendor stay a tight list. Before, the
                panel gap applied between every child alike, which spaced a
                key row from its neighbour exactly as far as it spaced Claude
                from Codex — nothing in the spacing said where a section
                ended. */}
            <div className="set-group">
            <div className={'set-group-head acct-provider-head'
              + (claudeProv?.user_enabled === false ? ' provider-off' : '')}>
              Claude
              <span className="dim"> · Claude Code
                {claudeProv?.status.version ? ` ${claudeProv.status.version}` : ''}</span>
              <span className="set-head-right">
                <ProviderSwitch provider={claudeProv}
                  busy={providerBusy !== null} onChange={toggleProvider} />
              </span>
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
                          ? `registered: ${fmtFull(k.registered_at)} (lower bound on survival)`
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
                      ? `registered ${fmtFull(k.registered_at)}`
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
            </div>{/* /.set-group — Claude */}

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
            {codexShown && <div className="set-group">
            <div className={'set-group-head acct-provider-head prov-openai'
              + (codex?.user_enabled === false ? ' provider-off' : '')}>
              Codex
              <span className="dim"> · Codex CLI
                {codex?.status.version ? ` ${codex.status.version}` : ''}</span>
              <span className="set-head-right">
                {codex?.user_enabled !== false && !codex?.hire_enabled
                  && <span className="acct-preview-tag">preview</span>}
                <ProviderSwitch provider={codex}
                  busy={providerBusy !== null} onChange={toggleProvider} />
              </span>
            </div>
            {!codex && (
              <div className="dim acct-prov-note">
                {providers ? 'provider state unavailable' : 'reading provider state…'}
              </div>
            )}
            {codex?.status.installed && (
              <>
                <div className="acct-prov-note">
                  {codex.status.installed
                    ? <>
                      installed
                      {codex.status.source
                        && <span className="dim"> ({srcLabelOf('ORGTREE_CODEX')[codex.status.source]
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
                      {t.tier} · seat {fmtCredits(t.seat)}
                      <span className="dim"> · {t.model}</span>
                    </span>
                  ))}
                </div>
                {/* ⚠ DRIFT SITS BESIDE THE TIERS IT SUPPRESSES. Nothing in
                    this repo refreshes the pin — `update.ps1`, `update.sh`
                    and `install-autostart.ps1` have no codex step — and
                    OpenAI gates rollout models on the reporting CLI version,
                    so a stale CLI SHORTENS the list above with no other
                    symptom. Measured 2026-09-04: pinned 0.150.1 listed 9
                    models, 0.153.0 listed the same 9 plus `gpt-6-astra`, same
                    account. Rendered ONLY on a `true` verdict:
                    `update_available` is a tristate and `null` means we
                    cannot tell, which must never be dressed as either answer.
                    The path is shown because the pin lives under the DATA
                    ROOT, so this names the build actually measured rather
                    than "the" CLI. */}
                {codex.cli_version?.update_available === true && (
                  <div className="acct-prov-note acct-cli-drift">
                    Codex CLI <b>{codex.cli_version.version}</b> is installed;{' '}
                    <b>{codex.cli_version.latest}</b> is available. OpenAI only
                    offers rollout models to a recent enough CLI, so newer
                    tiers can be missing from the list above until this is
                    upgraded: <code>npm install --prefix ~/orgtree/codex
                    @openai/codex</code>
                    {codex.cli_version.path && <div className="dim">
                      measured build: {codex.cli_version.path}</div>}
                  </div>
                )}
                {codex.reason && codex.user_enabled !== false
                  && <div className="dim acct-prov-note">{codex.reason}</div>}
              </>
            )}
            </div>}

            {/* ── provider section: Antigravity (D-189, re-walked for the
                Antigravity CLI) — the same machine-level install/connect
                surface, the CLI's own name as the label. The preview tag
                only while hiring is actually off. D-202 hides it whole when
                absent, exactly as Codex above. */}
            {antigravityShown && <div className="set-group">
            <div className={'set-group-head acct-provider-head prov-google'
              + (antigravity?.user_enabled === false ? ' provider-off' : '')}>
              Antigravity
              <span className="dim"> · Antigravity CLI
                {antigravity?.status.version ? ` ${antigravity.status.version}` : ''}</span>
              <span className="set-head-right">
                {antigravity?.user_enabled !== false && !antigravity?.hire_enabled
                  && <span className="acct-preview-tag">preview</span>}
                <ProviderSwitch provider={antigravity}
                  busy={providerBusy !== null} onChange={toggleProvider} />
              </span>
            </div>
            {!antigravity && (
              <div className="dim acct-prov-note">
                {providers ? 'provider state unavailable' : 'reading provider state…'}
              </div>
            )}
            {antigravity?.status.installed && (
              <>
                <div className="acct-prov-note">
                  {antigravity.status.installed
                    ? <>
                      installed
                      {antigravity.status.source
                        && <span className="dim"> ({srcLabelOf('ORGTREE_ANTIGRAVITY')[antigravity.status.source]
                          ?? antigravity.status.source})</span>}
                      {' — '}
                      {antigravity.status.connected
                        ? <>signed in
                          {antigravity.status.email && <> as <b>{antigravity.status.email}</b></>}
                          {antigravity.status.kind === 'oauth' && <> (Google account)</>}
                        </>
                        : 'not signed in'}
                    </>
                    : 'not installed on this machine'}
                </div>
                <div className="acct-prov-tiers">
                  {antigravity.tiers.map((t) => (
                    <span key={t.tier} className="acct-prov-tier">
                      <span className={'tier t-' + t.tier}>{t.letter}</span>
                      {t.tier} · seat {fmtCredits(t.seat)}
                      <span className="dim"> · {t.model}</span>
                    </span>
                  ))}
                </div>
                {antigravity.reason && antigravity.user_enabled !== false
                  && <div className="dim acct-prov-note">{antigravity.reason}</div>}
              </>
            )}
            </div>}

            {/* ── provider section: OpenRouter (2026-09-02) — the API-backed
                lane. ALWAYS rendered, unlike Codex/Antigravity: D-202 hides an
                uninstalled CLI because nothing here could install it, but a
                key IS the install, and this section is the only door for it
                (user spec: "key entry in providers list"). The section owns
                its key row, favorites row and picker; the switch is the
                panel's, like every other provider's. */}
            <OpenRouterSection provider={openrouter} toast={toast}
              pickerOpen={orrPicker} setPickerOpen={setOrrPicker}
              onChanged={() => { void refetchProviders() }}
              headRight={<ProviderSwitch provider={openrouter}
                busy={providerBusy !== null} onChange={toggleProvider} />} />
          </>
        )}
        </SettingsTabPanel>

        <SettingsTabPanel id="runtime" idBase="app-settings"
          active={tab === 'runtime'}>
          {warmingErr && <div className="ask-warn">
            could not read runtime settings: {warmingErr}</div>}
          {/* grouped by WHAT THEY ACT ON — one is about the processes that
              sit between turns, two are about what a turn does at its edges.
              The old flat list of three gave no reason why "keep processes
              warm" and "wait for MCP tools" sat next to each other. */}
          <SetGroup title="Agent processes">
            <SetToggle label="keep agent processes warm"
              checked={warming !== false}
              disabled={warming == null || warmingBusy}
              onChange={runtimeSwitch(setWarmingEnabled, setWarmingBusy,
                (p) => `process warming turned ${p.warming_enabled ? 'on' : 'off'}`)}
              hint={'a warm process answers its next turn immediately '
                + 'instead of starting cold'} />
          </SetGroup>
          <SetGroup title="Turns">
            <SetToggle label="check on working agents after 20 minutes"
              checked={workingCheckups !== false}
              disabled={workingCheckups == null || workingCheckupsBusy}
              onChange={runtimeSwitch(setWorkingCheckupsEnabled,
                setWorkingCheckupsBusy,
                (p) => 'working-agent checkups turned '
                  + (p.working_checkups_enabled ? 'on' : 'off'))}
              hint="off uses isolated Claude cache reads instead" />
            <SetToggle label="wait until the MCP tool surface is ready"
              checked={waitForMcpTools === true}
              disabled={waitForMcpTools == null || waitForMcpToolsBusy}
              onChange={runtimeSwitch(setWaitForMcpToolsEnabled,
                setWaitForMcpToolsBusy,
                (p) => 'MCP tool readiness wait turned '
                  + (p.wait_for_mcp_tools_enabled ? 'on' : 'off'))}
              hint={'when on, a turn waits briefly for every MCP tool its '
                + 'last successful turn could call'} />
            <SetToggle label="remind idle agents about unfinished docket items"
              checked={docketReminders === true}
              disabled={docketReminders == null || docketRemindersBusy}
              onChange={runtimeSwitch(setIdleDocketRemindersEnabled,
                setDocketRemindersBusy,
                (p) => 'idle docket reminders turned '
                  + (p.idle_docket_reminders_enabled ? 'on' : 'off'))}
              hint={'wakes an agent idle for 20 minutes that still owns '
                + 'active items; never for backlogged items or ones waiting '
                + 'on you'} />
          </SetGroup>
        </SettingsTabPanel>

        <SettingsTabPanel id="display" idBase="app-settings"
          active={tab === 'display'}>
          {/* the scope note moves off the tab button's badge and onto the
              group, where it can say WHAT is browser-local rather than
              decorating the tab strip. The badge stays too — it is what
              tells you before you open the tab. */}
          <SetGroup title="Desk" note="saved in this browser">
            <DeskTextSize />
            <CrowdStackToggle />
          </SetGroup>
          <SetGroup title="Startup" note="saved in this browser">
            <StartupView />
          </SetGroup>
        </SettingsTabPanel>

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
