// canvas/gallery.tsx — the documents gallery (user request 2026-09-03): every
// presented-document card, org-wide, in one place, so finding one back later
// doesn't mean remembering which agent sent it. Reads the flat `/documents`
// list the backend already merges live + evicted rows into — NOT the tree
// (`org_children` hides an archived node's cards once it has a successor,
// same trap docs.tsx's comment flags). Clicking a row hands the id to the
// caller (App.tsx), which lifts it into OrgCanvas's existing DocReader.

import { useEffect, useState } from 'react'
import type { DocRow } from '../api'
import { getDocuments } from '../api'
import { DocIcon } from '../icons'
import { useEsc } from './shared'

const STATE_BADGE: Record<DocRow['node_state'], string | null> = {
  live: null, archived: 'retired', unrecoverable: 'unrecoverable',
  deleted: 'deleted agent',
}

export function DocGalleryModal({ slug, close, onOpen }: {
  slug: string
  close: () => void
  onOpen: (id: string) => void
}) {
  useEsc(close)
  const [rows, setRows] = useState<DocRow[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    getDocuments(slug)
      .then((d) => { if (live) setRows(d.documents) })
      .catch((e: Error) => { if (live) setErr(e.message) })
    return () => { live = false }
  }, [slug])
  return (
    <div className="overlay" onClick={close}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide doc-gallery" onClick={(e) => e.stopPropagation()}>
        <h3><DocIcon fontSize="inherit" /> documents</h3>
        {err && <div className="ask-warn">could not load the gallery: {err}</div>}
        {!err && !rows && <div className="dim">loading…</div>}
        {rows && rows.length === 0 &&
          <div className="dim">no cards have been presented yet</div>}
        {rows && rows.map((r) => {
          const badge = STATE_BADGE[r.node_state]
          return (
            <div key={r.id} className="hist-row doc-gallery-row"
              role="button" tabIndex={0}
              onClick={() => { onOpen(r.id); close() }}
              onKeyDown={(e) => { if (e.key === 'Enter') { onOpen(r.id); close() } }}>
              <DocIcon fontSize="inherit" />
              <b className="doc-gallery-title">{r.title}</b>
              <span className="dim">{r.node}</span>
              {badge && <span className="badge">{badge}</span>}
              {r.evicted &&
                <span className="badge evicted"
                  title="dropped by the retention prune (newest 10/agent, 100/org) — the card survives, its body does not">
                  evicted</span>}
              <span className="spacer" />
              <span className="dim">{r.at}</span>
            </div>
          )
        })}
        <div className="row">
          <button className="primary" type="button" onClick={close}>done</button>
        </div>
      </div>
    </div>
  )
}
