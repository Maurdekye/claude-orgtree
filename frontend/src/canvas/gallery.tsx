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
import { DocIcon } from '../icons'
import { dismissDoc, useDoc } from './docs'
import { ago, md, useEsc, usePolled } from './shared'

const STATE_BADGE: Record<DocRow['node_state'], string | null> = {
  live: null, archived: 'retired', unrecoverable: 'unrecoverable',
  deleted: 'deleted agent',
}

/** the user's rule (2026-09-03): the default list is cards from agents that
 *  are CURRENTLY HIRED. Asked directly whether that should hide the rest —
 *  every card in the live org today is from a retired agent, so the strict
 *  filter opens empty — they chose "default hired + 'show retired'": strict
 *  by default, one control to reveal the archive. */
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
  // dismissed cards never arrive here — the server drops them from
  // `documents`, and the DELETE bumps the livebus so `usePolled` above
  // refetches without this panel wiring a refresh of its own
  const rows = all && (showRetired ? all : all.filter(isHired))
  const retiredCt = (all ?? []).length - (all ?? []).filter(isHired).length
  // selection is BY ID, not index — the list repolls, and the filter above
  // narrows it, so an index would silently address a different document
  const [selId, setSelId] = useState<string | null>(null)
  const cur = rows?.find((r) => r.id === selId)
  return (
    <div className="overlay" onClick={(e) => { e.stopPropagation(); close() }}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide gallery-modal" onClick={(e) => e.stopPropagation()}>
        <h3><DocIcon fontSize="inherit" /> presented documents</h3>
        {/* mail's own folder switcher, in mail's place above the pane: the
            strict list the user asked for, and the way back to the archive
            they would otherwise not know was there */}
        <div className="mail-folders">
          <button className={showRetired ? '' : 'on'}
            onClick={() => setShowRetired(false)}>currently hired</button>
          <button className={showRetired ? 'on' : ''}
            title="cards presented by agents that have since been retired or deleted"
            onClick={() => setShowRetired(true)}>
            all agents{retiredCt > 0 && <span className="dim"> {retiredCt}</span>}
          </button>
        </div>
        <div className="mailpane">
          {all == null
            ? <div className="dim pad">loading…</div>
            : rows!.length === 0
              ? <div className="dim pad">
                  {showRetired
                    ? 'no cards have been presented yet'
                    : retiredCt > 0
                      ? `no cards from currently-hired agents — ${retiredCt} from `
                        + 'retired ones, under “all agents”'
                      : 'no cards have been presented yet'}
                </div>
              : (
                <div className="mailer">
                  <div className="mailer-list">
                    {rows!.map((r) => (
                      <div key={r.id}
                        className={'mailrow doc-gallery-row'
                          + (r.id === selId ? ' on' : '')
                          + (r.evicted ? ' evicted' : '')}
                        title={r.evicted
                          ? 'content evicted — later presentations pushed this '
                            + 'card off the list'
                          : `read “${r.title}”`}
                        onClick={() => setSelId(r.id === selId ? null : r.id)}>
                        <div className="l1">
                          <span className="mfrom">{r.title || '(untitled)'}</span>
                          <span className="mtime">{ago(r.at)}</span>
                        </div>
                        <div className="l2">
                          {r.node || '?'}
                          {STATE_BADGE[r.node_state] &&
                            <span className="badge"> {STATE_BADGE[r.node_state]}</span>}
                          {r.evicted && <span className="badge evicted"> content evicted</span>}
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
 *  you are actually looking at. */
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
      <div className="mailer-head">
        <b>{row.title || '(untitled)'}</b>
        <span className="dim">{row.node || '?'}</span>
        {STATE_BADGE[row.node_state] &&
          <span className="badge">{STATE_BADGE[row.node_state]}</span>}
        <span className="dim">{row.at}</span>
        <span className="spacer" />
        {!row.evicted && (
          <button className="dim" title="remove the card (the document is gone)"
            onClick={() => dismissDoc(slug, row.id, row.title, toast, onDismissed)}>
            dismiss</button>
        )}
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
