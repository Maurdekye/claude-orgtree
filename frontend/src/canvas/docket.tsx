// canvas/docket.tsx — the native work docket (docket-final-spec.md).
//
// SHAPED ON THE DOCUMENT GALLERY (same list-left/content-right structure,
// user spec) — wears the same `.settings.wide`/`.mailer`/`.mailer-list`/
// `.mailrow`/`.mailer-read` classes DocGalleryModal does, styled by its own
// `.docket-modal` scope the way `.gallery-modal` styles its own rows.
//
// Attached questions are NOT a second answering form: each is rendered as
// the real <AskCard> for the matching entry in `tree.asks` (which stays
// uncapped for every open ask), so answering here calls the exact same
// answerAsk/resolveBatch route the inbox/desk cards do. The item's own
// `questions` array (wire contract v3) is only used to know WHICH asks to
// look up and for the "who is asking" header — never to answer directly.

import { useState } from 'react'
import type { AskInfo, ToastFn, TreePayload, WorkItem } from '../types'
import {
  dismissWorkItemAttention, getWorkItems, replyWorkItem,
} from '../api'
import { CloseIcon, DocketIcon } from '../icons'
import { AskCard } from './asks'
import { MailReplyBox } from './mail'
import { ago, useEsc, usePolled } from './shared'

const STATUS_LABEL: Record<string, string> = {
  open: 'Open',
  in_progress: 'In progress',
  blocked: 'Blocked',
  review: 'Under review',
  done: 'Done',
  superseded: 'Superseded',
  dropped: 'Dropped',
}
const statusLabel = (status: string): string => STATUS_LABEL[status] ?? status

export function DocketToolbarButton({ summary, onClick }: {
  summary?: { attention: number; active: number } | null
  onClick?: () => void
}) {
  const { attention, active } = summary ?? { attention: 0, active: 0 }
  return (
    <button className="iconbtn docket-bell"
      title={attention > 0
        ? `work docket — ${attention} item(s) need attention`
        : 'work docket'}
      onClick={onClick}>
      <DocketIcon fontSize="inherit" />
      <b className={'eye-count' + (attention > 0 ? ' docket-attn' : '')}>
        {attention > 0 ? attention : active}</b>
    </button>
  )
}

export function DocketModal({ slug, toast, close, tree, onFocusAgent }: {
  slug: string
  toast: ToastFn
  close: () => void
  tree: TreePayload
  /** the existing presented-document agent navigation (gallery.tsx's
   *  DocPane) — user ruling 2026-09-05: agent identities in a mail-idiom
   *  detail pane are clickable links using this exact behavior everywhere
   *  it appears, docket included. */
  onFocusAgent?: (agentId: string) => void
}) {
  useEsc(close)
  const [showArchived, setShowArchived] = useState(false)
  const [bump, setBump] = useState(0)
  const data = usePolled(() => getWorkItems(slug, showArchived), [slug, showArchived], 5000, bump)
  // active items first, archived below — each group keeps the server's own
  // recency order (spec: "retaining normal relative recency order within
  // each group"), so this is a concat, never a client-side re-sort.
  const active = data?.items ?? []
  const archived = showArchived ? (data?.archived ?? []) : []
  const rows = [...active, ...archived]
  // selection BY ID, not index — the list repolls under the user (G5)
  const [selId, setSelId] = useState<string | null>(null)
  const cur = rows.find((r) => r.id === selId)
  const asksById = new Map<string, AskInfo>((tree.asks ?? []).map((a) => [a.id, a]))

  const onDismiss = (item: WorkItem) => {
    if (!item.manual_attention) return
    dismissWorkItemAttention(slug, item.id, item.manual_attention.set_rev)
      .then(() => {
        toast([`dismissed the attention flag on “${item.title}”`])
        setBump((n) => n + 1)
      })
      // 409 (stale set_rev / already cleared) surfaces as an ordinary thrown
      // Error via req() — never a silent no-op or override
      .catch((e: Error) => toast([`error: ${e.message}`]))
  }

  return (
    <div className="overlay" onClick={(e) => { e.stopPropagation(); close() }}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide docket-modal" onClick={(e) => e.stopPropagation()}>
        <div className="gallery-head docket-head">
          <h3><DocketIcon fontSize="inherit" /> Work docket</h3>
          <label className="checkline docket-showarchived">
            <input type="checkbox" checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)} />
            Show archived
          </label>
          <span className="spacer" />
          <button className="chip-x" title="close" onClick={close}>
            <CloseIcon fontSize="inherit" />
          </button>
        </div>
        <div className="mailpane">
          {!data
            ? <div className="dim pad">loading…</div>
            : rows.length === 0
              ? <div className="dim pad">no work items yet</div>
              : (
                <div className="mailer">
                  <div className="mailer-list">
                    {rows.map((r) => (
                      <DocketRow key={r.id} item={r} selected={r.id === selId}
                        onClick={() => setSelId(r.id === selId ? null : r.id)}
                        onDismiss={onDismiss} />
                    ))}
                  </div>
                  <div className="mailer-read">
                    {cur
                      ? <DocketPane key={cur.id} slug={slug} item={cur} toast={toast}
                          asksById={asksById} onDismiss={onDismiss}
                          close={close} onFocusAgent={onFocusAgent} />
                      : <div className="dim pad mailer-none">select an item to view it</div>}
                  </div>
                </div>
              )}
        </div>
        <div className="docket-foot dim">Done items archive after 1 hour without an update.</div>
      </div>
    </div>
  )
}

function DocketRow({ item, selected, onClick, onDismiss }: {
  item: WorkItem
  selected: boolean
  onClick: () => void
  onDismiss: (item: WorkItem) => void
}) {
  const attention = item.effective_attention
  // active (white) / attention (orange) / archived (grey, darker bg) — three
  // mutually exclusive states per spec; archived wins visually even if (in
  // principle) it also had attention, but the backend never actually hands
  // us that combination (an attention item comes back archived:false).
  const state = item.archived ? 'archived' : (attention ? 'attention' : 'active')
  const cls = ['mailrow', 'docket-row', state, selected ? 'on' : ''].filter(Boolean).join(' ')
  const label = attention ? 'Needs attention' : statusLabel(item.status)
  // Dismiss clears the MANUAL flag only — a question-only attention item has
  // nothing to dismiss (answering the question is the only way to clear it).
  const canDismiss = item.attention_sources.includes('manual')
  return (
    <div className={cls} title={item.title} onClick={onClick}>
      <div className="l1">
        <span className="mfrom">{item.title || '(untitled)'}</span>
        <span className="mtime">{ago(item.docket_at ?? item.at)}</span>
      </div>
      <div className="l2">
        <span>{label}</span>
        {item.last_updater?.node && <span className="docket-updater">{item.last_updater.node}</span>}
        {canDismiss && (
          <button className="badge docket-dismiss" title="clear this manually-raised flag"
            onClick={(e) => { e.stopPropagation(); onDismiss(item) }}>
            Dismiss
          </button>
        )}
      </div>
    </div>
  )
}

function DocketList({ heading, items }: { heading: string; items: string[] }) {
  return (
    <div className="docket-list">
      <div className="docket-list-heading dim">{heading}</div>
      {items.length === 0
        ? <div className="dim docket-list-empty">None</div>
        : <ul className="docket-list-items">
            {items.map((t, i) => <li key={i}>{t}</li>)}
          </ul>}
    </div>
  )
}

function DocketPane({ slug, item, toast, asksById, onDismiss, close, onFocusAgent }: {
  slug: string
  item: WorkItem
  toast: ToastFn
  asksById: Map<string, AskInfo>
  onDismiss: (item: WorkItem) => void
  close: () => void
  onFocusAgent?: (agentId: string) => void
}) {
  const attention = item.effective_attention
  const label = attention ? 'Needs attention' : statusLabel(item.status)
  const canDismiss = item.attention_sources.includes('manual')
  // a stable local so narrowing survives into the reply closure below
  // (TS does not narrow a property access across a nested arrow function)
  const lastUpdater = item.last_updater
  const manualAttn = item.manual_attention
  return (
    <>
      <div className="mailer-head docket-pane-head">
        <b>{item.title || '(untitled)'}</b>
        <span className="spacer" />
        {canDismiss && (
          <button className="badge docket-dismiss" onClick={() => onDismiss(item)}>
            Dismiss
          </button>
        )}
      </div>
      <div className={'dim docket-pane-sub' + (attention ? ' docket-pane-sub-attn' : '')}>
        {label} · Updated {ago(item.docket_at ?? item.at)}
        {lastUpdater?.node && (
          <>
            {' by '}
            <button className="cc-name cc-name-jump" title={`focus ${lastUpdater.node}'s desk`}
              onClick={() => { close(); onFocusAgent?.(lastUpdater.node) }}>
              {lastUpdater.node}
            </button>
          </>
        )}
      </div>
      <DocketList heading="DONE SO FAR" items={item.done_so_far} />
      <DocketList heading="WORKING ON / NEXT" items={item.working_on_next} />
      {manualAttn && (
        <div className="docket-attention-box">
          <div className="docket-question-head">
            Manual attention from{' '}
            <button className="cc-name cc-name-jump" title={`focus ${manualAttn.by.node}'s desk`}
              onClick={() => { close(); onFocusAgent?.(manualAttn.by.node) }}>
              {manualAttn.by.node}
            </button>
          </div>
          <div>{manualAttn.reason}</div>
        </div>
      )}
      {item.questions.map((q) => {
        const ask = asksById.get(q.ask_id)
        // a batch card may cover tabs from OTHER items too (one agent, one
        // open batch) — the full card still answers ALL its tabs together
        // (Astra ruling: preserve original full-batch answer semantics,
        // never silently submit only the tabs shown here), so a note makes
        // that linkage explicit instead of implying this box is scoped to
        // just this item's tab.
        const otherTabs = ask?.tabs && ask.tabs.length > (q.tabs?.length ?? 0)
        return (
          <div key={q.ask_id} className="docket-question-box">
            <div className="docket-question-head">
              Question from{' '}
              <button className="cc-name cc-name-jump" title={`focus ${q.node}'s desk`}
                onClick={() => { close(); onFocusAgent?.(q.node) }}>
                {q.node}
              </button>
            </div>
            {otherTabs && (
              <div className="dim docket-question-note">
                this batch also covers other items — answering it resolves every tab at once
              </div>
            )}
            {ask
              ? <AskCard ask={ask} slug={slug} toast={toast} />
              : <div className="dim">this question is no longer open</div>}
          </div>
        )
      })}
      {lastUpdater ? (
        <>
          <div className="dim docket-reply-label">
            Reply to{' '}
            <button className="cc-name cc-name-jump" title={`focus ${lastUpdater.node}'s desk`}
              onClick={() => { close(); onFocusAgent?.(lastUpdater.node) }}>
              {lastUpdater.node}
            </button>
            {' · last updated this item'}
          </div>
          <MailReplyBox target={lastUpdater.node}
            onSend={(text) => replyWorkItem(slug, item.id, text)
              .then((r) => {
                if (r.deferred) {
                  toast([`${lastUpdater.node} is archived — the reply waits for rehire`])
                } else {
                  toast([`sent to ${lastUpdater.node}`])
                }
              })
              .catch((e: Error) => {
                toast([`error: ${e.message}`])
                throw e
              })} />
        </>
      ) : (
        <div className="dim docket-reply-label">no status update yet — nothing to reply to</div>
      )}
    </>
  )
}
