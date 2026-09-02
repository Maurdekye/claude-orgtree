// canvas/openrouter.tsx — the OpenRouter lane's settings surface (user spec
// 2026-09-02, verbatim intent):
//
//   · key entry in the providers list (App settings → Providers);
//   · once a key is registered, a separate ROW of model-card icons (the
//     favorites) appears below it — the row highlights as one control, and
//     clicking it opens the MODEL SELECTION modal;
//   · the modal searches the catalog, shows 5–10 results at a time, each with
//     its icon card, full name, provider (vendor) and cost per 1M tokens in
//     and out; selecting/deselecting adds/removes the model from the row;
//   · the favorites are the models whose TOKENS (chips) can be hired.
//
// Cards are MONOGRAMS (user choice): the model's canonical letter on its
// canonical colour, both derived by the backend from the model id, the same
// visual language as the tier chips on the canvas. There is no cap on
// favorites (user choice); the row wraps.
//
// The key is written here and never read back: the document this renders
// says `key_set`, the key's LABEL at openrouter.ai and the credit standing —
// nothing else, by construction of the backend (openrouter.py).

import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import {
  clearOpenRouterKey, getOpenRouter, searchOpenRouterModels, setOpenRouterFavorite,
  setOpenRouterKey,
} from '../api'
import type {
  OpenRouterDoc, OpenRouterModel, OpenRouterModelsPage, ProviderInfo, ProviderTier,
} from '../types'
import { setOpenRouterTiers } from './shared'

type ToastFn = (lines: string[]) => void

const PAGE = 8

const money = (v: number | null | undefined, digits = 2): string =>
  v == null || Number.isNaN(v) ? '—' : `$${v.toFixed(digits)}`
/** per-million price, trimmed: $0.14 · $2 · $10 */
const perM = (v: number): string =>
  `$${Number.isInteger(v) ? v.toFixed(0) : v.toFixed(v < 1 ? 2 : 1).replace(/\.0$/, '')}`
const ctxK = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M`
    : n >= 1000 ? `${Math.round(n / 1000)}K` : String(n)

/** the monogram card: letter on colour. One element, styled through `--orr-c`
 *  so the sheet owns every derived shade (border, wash) from one value. */
export function ModelCard({ letter, color, title, large }: {
  letter: string
  color: string
  title?: string
  large?: boolean
}) {
  return (
    <span className={'orr-card' + (large ? ' lg' : '')} title={title}
      style={{ '--orr-c': color } as CSSProperties} aria-hidden={!title}>
      {letter}
    </span>
  )
}

const tierTitle = (t: ProviderTier): string =>
  `${t.name ?? t.model} — ${t.vendor ?? ''} · `
  + `${perM(t.prompt ?? 0)} in / ${perM(t.completion ?? 0)} out per 1M · seat ${t.seat}`
  + (t.vendor && t.vendor !== 'anthropic'
    ? ' · runs on Claude Code best-effort (non-Anthropic model)' : '')

/** the whole section: head (label, switch), key row, favorites row, picker */
export function OpenRouterSection({ provider, headRight, toast, pickerOpen,
  setPickerOpen, onChanged }: {
  /** the /api/providers entry — for the on/off switch state and the head */
  provider: ProviderInfo | undefined
  /** the ProviderSwitch, rendered by the panel that owns `toggleProvider` */
  headRight?: ReactNode
  toast: ToastFn
  /** the picker is a modal INSIDE the App settings modal: the panel tracks
   *  whether it is open so its own Escape handler closes only the top layer */
  pickerOpen: boolean
  setPickerOpen: (open: boolean) => void
  /** something durable changed (key, favorites) — the panel refetches the
   *  providers payload so its other consumers see it without waiting a poll */
  onChanged?: () => void
}) {
  const [doc, setDoc] = useState<OpenRouterDoc | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [keyDraft, setKeyDraft] = useState('')
  const [replacing, setReplacing] = useState(false)

  const adopt = (d: OpenRouterDoc) => {
    setDoc(d); setErr(null)
    setOpenRouterTiers(d.tiers)
  }
  const load = (force = false) => getOpenRouter(force).then(adopt)
    .catch((e: Error) => setErr(e.message))
  useEffect(() => { void load() }, [])

  const run = (p: Promise<OpenRouterDoc>, ok: string) => {
    setBusy(true)
    p.then((d) => { adopt(d); if (ok) toast([ok]); onChanged?.() })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => setBusy(false))
  }
  const saveKey = () => {
    const k = keyDraft.trim()
    if (!k) return
    run(setOpenRouterKey(k), 'OpenRouter key saved')
    setKeyDraft(''); setReplacing(false)
  }

  const off = provider?.user_enabled === false
  const keySet = !!doc?.key_set
  const credits = doc?.credits
  const favorites = doc?.tiers ?? []

  return (
    <div className="set-group">
      <div className={'set-group-head acct-provider-head prov-openrouter'
        + (off ? ' provider-off' : '')}>
        OpenRouter
        <span className="dim"> · REST API, runs on Claude Code</span>
        <span className="set-head-right">
          {!off && keySet && !provider?.hire_enabled
            && <span className="acct-preview-tag">preview</span>}
          {headRight}
        </span>
      </div>

      {err && <div className="dim acct-prov-note">could not read OpenRouter state: {err}</div>}
      {!doc && !err && <div className="dim acct-prov-note">reading OpenRouter state…</div>}

      {doc && (!keySet || replacing) && (
        <div className="orr-key">
          <input type="password" autoComplete="off" spellCheck={false}
            placeholder={replacing ? 'new OpenRouter API key…' : 'OpenRouter API key (sk-or-…)'}
            aria-label="OpenRouter API key"
            value={keyDraft} disabled={busy}
            onChange={(e) => setKeyDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') saveKey() }} />
          <button className="primary" disabled={busy || !keyDraft.trim()}
            onClick={saveKey}>{replacing ? 'replace key' : 'set key'}</button>
          {replacing && <button disabled={busy}
            onClick={() => { setReplacing(false); setKeyDraft('') }}>cancel</button>}
          <span className="dim" style={{ flexBasis: '100%' }}>
            stored on this machine, never shown again — get one at openrouter.ai/keys
          </span>
        </div>
      )}

      {doc && keySet && !replacing && (
        <div className="acct-prov-note">
          key set
          {doc.connected
            ? <>{' — '}connected{doc.label && <> as <b>{doc.label}</b></>}
              {credits && (
                <span className="orr-credits">
                  {' · '}
                  {credits.limit_remaining != null
                    ? <>{money(credits.limit_remaining)} of {money(credits.limit)} left</>
                    : <>{money(credits.usage)} spent</>}
                  {credits.usage_daily != null && <> · today {money(credits.usage_daily)}</>}
                  {credits.usage_weekly != null && <> · week {money(credits.usage_weekly)}</>}
                  {credits.usage_monthly != null && <> · month {money(credits.usage_monthly)}</>}
                  {credits.is_free_tier && <> · free tier</>}
                </span>
              )}
            </>
            : <>{' — '}<span className={doc.reason ? 'ask-warn-inline' : 'dim'}>
              {doc.reason ?? 'not checked yet'}</span></>}
          {' '}
          <button className="acct-btn" title="re-check the key at openrouter.ai"
            disabled={busy} onClick={() => run(getOpenRouter(true), '')}>refresh</button>
          <button className="acct-btn" disabled={busy}
            onClick={() => setReplacing(true)}>replace</button>
          <button className="acct-btn danger" disabled={busy}
            onClick={() => run(clearOpenRouterKey(), 'OpenRouter key cleared')}>clear</button>
        </div>
      )}

      {/* the favorites ROW — one control (user spec): highlight on hover /
          focus, click opens the picker. Rendered only once a key exists. */}
      {doc && keySet && (
        <button type="button"
          className={'orr-favs' + (pickerOpen ? ' on' : '')}
          aria-haspopup="dialog" aria-expanded={pickerOpen}
          title="choose which OpenRouter models can be hired"
          onClick={() => setPickerOpen(true)}>
          {favorites.map((t) => (
            <ModelCard key={t.tier} letter={t.letter} color={t.color ?? '#9aa0a6'}
              title={tierTitle(t)} />
          ))}
          <span className="orr-hint">
            {favorites.length
              ? `${favorites.length} model${favorites.length === 1 ? '' : 's'} hireable · click to change`
              : '+ pick the models that can be hired'}
          </span>
        </button>
      )}
      {doc && keySet && !off && provider?.reason && provider.hire_enabled === false
        && favorites.length > 0
        && <div className="dim acct-prov-note">{provider.reason}</div>}

      {pickerOpen && doc && (
        <ModelPicker doc={doc} busy={busy}
          onToggle={(m, selected) =>
            run(setOpenRouterFavorite(m.id, selected),
                `${selected ? 'added' : 'removed'} ${m.name}`)}
          onClose={() => setPickerOpen(false)} />
      )}
    </div>
  )
}

/** the model-selection modal (user spec): search → 5–10 results with card,
 *  full name, vendor, $/1M in and out → select/deselect */
export function ModelPicker({ doc, busy, onToggle, onClose }: {
  doc: OpenRouterDoc
  busy: boolean
  onToggle: (m: OpenRouterModel, selected: boolean) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<OpenRouterModelsPage | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  // request guard: a slow page for an old query must not land over a fast
  // one for the current query
  const seq = useRef(0)
  const selected = new Set(doc.tiers.map((t) => t.model))

  useEffect(() => {
    const id = ++seq.current
    const timer = setTimeout(() => {
      searchOpenRouterModels(q, offset, PAGE)
        .then((p) => { if (seq.current === id) { setPage(p); setErr(null) } })
        .catch((e: Error) => { if (seq.current === id) setErr(e.message) })
    }, q ? 200 : 0)
    return () => clearTimeout(timer)
  }, [q, offset])
  useEffect(() => { inputRef.current?.focus() }, [])

  const total = page?.total ?? 0
  const from = total ? offset + 1 : 0
  const to = page ? Math.min(offset + page.items.length, total) : 0
  return (
    <div className="overlay" onClick={(e) => { e.stopPropagation(); onClose() }}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings orr-picker" role="dialog"
        aria-label="OpenRouter model selection"
        onClick={(e) => e.stopPropagation()}>
        <h3>OpenRouter models
          <span className="dim"> · {doc.tiers.length} selected</span></h3>
        <input ref={inputRef} className="orr-search" type="search"
          placeholder="search the catalog — name, vendor, id…"
          aria-label="search OpenRouter models"
          value={q} onChange={(e) => { setQ(e.target.value); setOffset(0) }} />
        {err && <div className="ask-warn">could not read the catalog: {err}</div>}
        <div className="orr-list">
          {!page && !err && <div className="dim">reading the catalog…</div>}
          {page && !page.items.length && <div className="dim">no models match</div>}
          {page?.items.map((m) => {
            const on = selected.has(m.id) || !!m.selected
            return (
              <button type="button" key={m.id}
                className={'orr-row' + (on ? ' on' : '')}
                aria-pressed={on} disabled={busy}
                onClick={() => onToggle(m, !on)}>
                <ModelCard letter={m.letter} color={m.color} large />
                <span className="orr-name">
                  <b>{m.name}</b>
                  <span className="dim">{m.vendor} · {m.id}
                    {m.context ? ` · ${ctxK(m.context)} ctx` : ''}
                    {!m.tools ? ' · no tool use' : ''}
                    {m.vendor !== 'anthropic' ? ' · best-effort on Claude Code' : ''}
                  </span>
                </span>
                <span className="orr-price">
                  {m.free ? 'free' : <>{perM(m.prompt)} in<br />{perM(m.completion)} out</>}
                  {!m.free && <><br /><span>per 1M</span></>}
                </span>
                <span className="orr-check">{on ? '✓ selected' : 'select'}</span>
              </button>
            )
          })}
        </div>
        <div className="orr-pager">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            ‹ prev</button>
          <button disabled={!page || to >= total} onClick={() => setOffset(offset + PAGE)}>
            next ›</button>
          <span className="dim">{total ? `${from}–${to} of ${total}` : ''}</span>
          <button className="primary" type="button" onClick={onClose}>done</button>
        </div>
      </div>
    </div>
  )
}
