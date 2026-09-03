// canvas/gallery.tsx — FR-03 org-wide presented-document list.
//
// The per-node chips on the canvas are how a card is noticed the moment it
// arrives. This is how it is found an hour later: one toolbar-launched panel
// over the flat `documents` list (plus `present_evicted` log lines whose
// body is gone), not a second store and not a walk of the tree.
//
// SHAPED ON THE MAIL UI, which the user named as the reference (2026-09-03:
// "the gallery ui should probably resemble the mail ui: a list of entries on
// the left with their titles, submitted agent, and submission time, and a
// the scrollable document viewer on the right"). It wears mail's own
// classes — `.mailer` / `.mailer-list` / `.mailrow` / `.mailer-read` — and
// mail's rules: selection BY IDENTITY (the list repolls under you), nothing
// selected on open, the filter box only once a list is worth filtering.
// It does NOT reuse MailList itself: that component is built on MailRow
// (sender, kind, an inline body, replies, notice piles) and a document row
// is a title + agent + time whose body is fetched on click. Sharing the
// look without pretending the data is mail is the honest half.
//
// The BODY half is shared for real: `useDoc` + `dismissDoc` from docs.tsx
// are the same fetch and the same dismiss the overlay reader uses.

import { useState } from 'react'
import type { DocRow } from '../api'
import { fileBase, getDocuments } from '../api'
import type { ToastFn } from '../types'
import { CloseIcon, DocIcon } from '../icons'
import { dismissDoc, useDoc } from './docs'
import { ago, md, TIER_LETTER, tierLabel, useEsc, usePolled } from './shared'

/** the presenting agent's model, as the letter chip every other surface uses
 *  for a tier (user request 2026-09-03: "for each agent entry, show its model
 *  icon card"). Same markup and same `t-<tier>` colour class as the mail
 *  sender chip and the node card, so a model reads the same everywhere.
 *  Nothing renders once the node is gone and the ledger has no tier to give. */
function TierChip({ tier }: { tier?: string | null }) {
  if (!tier) return null
  return (
    <span className={'tier t-' + tier} title={tierLabel(tier)}>
      {TIER_LETTER[tier] ?? tier.slice(0, 1).toUpperCase()}
    </span>
  )
}

/** why a row is secondary, for the tooltip. NOT a badge any more (user,
 *  2026-09-03: "dont put a big 'retired' card in their row; just grey them
 *  out slightly") — the state is carried by the row's own dimming, and the
 *  words stay available on hover for the case where grey is ambiguous. */
const STATE_WHY: Record<DocRow['node_state'], string | null> = {
  live: null,
  archived: 'this agent has been retired',
  unrecoverable: 'this agent is unrecoverable',
  deleted: 'this agent has been deleted',
}

/** the user's rule (2026-09-03): the default list is cards from agents that
 *  are CURRENTLY HIRED. Asked directly whether that should hide the rest —
 *  every card in the live org today is from a retired agent, so the strict
 *  filter opens empty — they chose "default hired + 'show retired'". */
const isHired = (r: DocRow) => r.node_state === 'live'

export function DocGalleryModal({ slug, toast, close }: {
  slug: string
  toast: ToastFn
  close: () => void
}) {
  useEsc(close)
  const data = usePolled(() => getDocuments(slug), [slug])
  const all = data?.documents
  const [showRetired, setShowRetired] = useState(false)
  // ONE list, grouped — not two views (user, 2026-09-03: "one tab with a
  // checkbox to show retired agents, which appear in the same list, sorted
  // below the active agents"). The server already returns newest-first, and
  // a stable partition keeps that order WITHIN each group while lifting the
  // hired ones above the retired: two filters, not a comparator, because a
  // sort would have to re-establish the recency order the server just set.
  //
  // Dismissed cards never arrive here at all — the server drops them from
  // `documents`, and the DELETE bumps the livebus so `usePolled` above
  // refetches without this panel wiring a refresh of its own.
  const hired = (all ?? []).filter(isHired)
  const retired = (all ?? []).filter((r) => !isHired(r))
  const rows = all && (showRetired ? [...hired, ...retired] : hired)
  const retiredCt = retired.length
  // selection is BY ID, not index — the list repolls, and the filter above
  // narrows it, so an index would silently address a different document
  const [selId, setSelId] = useState<string | null>(null)
  const cur = rows?.find((r) => r.id === selId)
  return (
    <div className="overlay" onClick={(e) => { e.stopPropagation(); close() }}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide gallery-modal" onClick={(e) => e.stopPropagation()}>
        <h3><DocIcon fontSize="inherit" /> presented documents</h3>
        {/* one control, not two views: the retired cards JOIN the list below
            the active ones rather than replacing them. The count rides the
            label so the archive is discoverable even while it is hidden —
            which is doing real work here, because the default list is empty
            whenever no currently-hired agent has presented anything. */}
        <label className="checkline gallery-showretired">
          <input type="checkbox" checked={showRetired}
            onChange={(e) => setShowRetired(e.target.checked)} />
          show retired agents
          {retiredCt > 0 && <span className="dim"> · {retiredCt}</span>}
        </label>
        <div className="mailpane">
          {all == null
            ? <div className="dim pad">loading…</div>
            : rows!.length === 0
              ? <div className="dim pad">
                  {!showRetired && retiredCt > 0
                    ? `no cards from currently-hired agents — ${retiredCt} from `
                      + 'retired ones, behind the checkbox above'
                    : 'no cards have been presented yet'}
                </div>
              : (
                <div className="mailer">
                  <div className="mailer-list">
                    {rows!.map((r) => (
                      <div key={r.id}
                        className={'mailrow doc-gallery-row'
                          // the accent treatment unread mail wears (user:
                          // "color-code the active agent cards with an orange
                          // flare similar to how unread mails are color
                          // coded") vs the slight grey for a retired one
                          + (isHired(r) ? ' active' : ' past')
                          + (r.id === selId ? ' on' : '')
                          + (r.evicted ? ' evicted' : '')}
                        title={[
                          r.evicted
                            ? 'content evicted — later presentations pushed '
                              + 'this card off the list'
                            : `read “${r.title}”`,
                          STATE_WHY[r.node_state],
                        ].filter(Boolean).join(' · ')}
                        onClick={() => setSelId(r.id === selId ? null : r.id)}>
                        <div className="l1">
                          <span className="mfrom">{r.title || '(untitled)'}</span>
                          <span className="mtime">{ago(r.at)}</span>
                        </div>
                        <div className="l2">
                          <TierChip tier={r.tier} />
                          {r.node || '?'}
                          {r.evicted && <span className="badge evicted">content evicted</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="mailer-read">
                    {cur
                      ? <DocPane key={cur.id} slug={slug} row={cur} toast={toast}
                          onDismissed={() => setSelId(null)} />
                      : <div className="dim pad mailer-none">
                          select a document to read it</div>}
                  </div>
                </div>
              )}
        </div>
      </div>
    </div>
  )
}

/** the right-hand viewer: the same fetch and the same dismiss the overlay
 *  reader runs (docs.tsx), in the mail reading pane's chrome. Dismiss lives
 *  HERE rather than on each row (user request 2026-09-03: "allow the
 *  dismissal of them from the viewer directly") — one control, on the thing
 *  you are actually looking at. Title sits on its own separate line; the
 *  dismiss button mirrors the desk view document card (right-aligned chip-x
 *  with CloseIcon). */
function DocPane({ slug, row, toast, onDismissed }: {
  slug: string
  row: DocRow
  toast: ToastFn
  onDismissed: () => void
}) {
  // an evicted row has no body to fetch — say so instead of spending a
  // request to render the 404 the endpoint would answer with
  const { doc, err } = useDoc(slug, row.evicted ? '' : row.id)
  return (
    <>
      <div className="mailer-head doc-pane-head">
        <div className="doc-pane-title-row">
          <b>{row.title || '(untitled)'}</b>
          <span className="spacer" />
          {!row.evicted && (
            <button className="chip-x" title="dismiss"
              onClick={() => dismissDoc(slug, row.id, row.title, toast, onDismissed)}>
              <CloseIcon fontSize="inherit" />
            </button>
          )}
        </div>
        <div className="doc-pane-meta-row">
          <TierChip tier={row.tier} />
          <span className="dim">{row.node || '?'}</span>
          {/* the OPEN document names the agent's state in words. The row only
              greys (user ruling — no badge in the row), but here there is room,
              and "why can I not dismiss this / who wrote it" is exactly the
              question the reading pane exists to answer. */}
          {STATE_WHY[row.node_state] &&
            <span className="dim">{STATE_WHY[row.node_state]}</span>}
          <span className="dim">{row.at}</span>
        </div>
      </div>
      {row.evicted
        ? <div className="dim pad">
            the content of this card is gone — later presentations pushed it off
            the list (newest 10 per agent, 100 org-wide are kept). Its title and
            sender survive in the org log, which is why it is still listed here.
          </div>
        : (
          <>
            {err && <div className="ask-warn">could not load the document: {err}</div>}
            {/* relative image srcs resolve against the PRESENTING node's
                files — `![](outbox/chart.png)` embeds a figure that agent saved */}
            {doc && <div className="mailer-body md"
              dangerouslySetInnerHTML={md(doc.body, fileBase(slug, doc.node))} />}
            {!doc && !err && <div className="dim pad">loading…</div>}
          </>
        )}
    </>
  )
}
