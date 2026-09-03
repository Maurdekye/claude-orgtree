// canvas/gallery.tsx — FR-03 org-wide presented-document list.
//
// The per-node chips on the canvas are how a card is noticed the moment it
// arrives. This is how it is found an hour later: one toolbar-launched modal
// over the flat `documents` list (plus `present_evicted` log lines whose
// body is gone), not a second store and not a walk of the tree.
//
// Clicking a live row opens the SAME DocReader OrgCanvas already drives.
// Evicted rows are not clickable — there is no body to fetch. Dismiss stays
// inside DocReader; this list does not grow a second control for it.
// Overlay + Esc close, no extra footer button — same idiom as UsageModal.

import type { DocRow } from '../api'
import { getDocuments } from '../api'
import { DocIcon } from '../icons'
import { ago, useEsc, usePolled } from './shared'

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
  const data = usePolled(() => getDocuments(slug), [slug])
  const rows = data?.documents
  return (
    <div className="overlay" onClick={(e) => { e.stopPropagation(); close() }}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings gallery-modal" onClick={(e) => e.stopPropagation()}>
        <h3><DocIcon fontSize="inherit" /> presented documents</h3>
        {rows == null && <div className="dim">loading…</div>}
        {rows && rows.length === 0 &&
          <div className="dim">no cards have been presented yet</div>}
        {rows && rows.length > 0 && (
          <div className="gallery-list">
            {rows.map((r) => (
              <GalleryRow key={r.id} doc={r} onOpen={onOpen} close={close} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function GalleryRow({ doc, onOpen, close }: {
  doc: DocRow
  onOpen: (id: string) => void
  close: () => void
}) {
  const gone = !!doc.evicted
  const badge = STATE_BADGE[doc.node_state]
  const open = () => { if (!gone) { onOpen(doc.id); close() } }
  return (
    <div role={gone ? undefined : 'button'} tabIndex={gone ? undefined : 0}
      className={'hist-row doc-gallery-row' + (gone ? ' evicted' : '')}
      title={gone
        ? 'content evicted — later presentations pushed this card off the list'
        : `read “${doc.title}”`}
      onClick={open}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault(); open()
      } }}>
      <DocIcon fontSize="inherit" />
      <b className="doc-gallery-title">{doc.title || '(untitled)'}</b>
      <span className="dim">{doc.node || '?'}</span>
      {badge && <span className="badge">{badge}</span>}
      {gone &&
        <span className="badge evicted">content evicted</span>}
      <span className="spacer" />
      <span className="dim">{ago(doc.at)}</span>
    </div>
  )
}
