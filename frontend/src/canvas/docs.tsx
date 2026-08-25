// canvas/docs.tsx — FR-03: presented documents (user request 2026-08-05).
// An agent presents a plan/report with orgtree_present; a small card pops
// out the SIDE of its node, and clicking it opens the markdown in-page for
// review. A reading surface, not a download (orgtree_send_file is the
// download path). The tree payload carries metadata only — the reader
// fetches the body on open.

import { useEffect, useState } from 'react'
import type { ToastFn } from '../types'
import { dismissDocument, fileBase, getDocument } from '../api'
import { md } from './shared'
import { CloseIcon, DocIcon } from '../icons'

export interface DocMeta { id: string; title: string; at: string }

/** the outboard chips on the node square — one per presented document.
 *  Square ICONS only (user report 2026-08-05: the titled chips were wide
 *  enough to overlap the adjacent card) — the title lives in the tooltip;
 *  the desk header carries the readable titled badges. */
export function DocChips({ docs, onOpen }: {
  docs: DocMeta[]
  onOpen: (id: string) => void
}) {
  return (
    <div className="doc-chips">
      {docs.slice(-4).map((d) => (
        <button key={d.id} className="doc-chip" title={`read “${d.title}”`}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onOpen(d.id) }}>
          <DocIcon fontSize="inherit" />
        </button>
      ))}
    </div>
  )
}

/** the in-page reader: title bar (✕ closes the reader; "dismiss" removes
 *  the card itself), markdown body under the desk's .md styling */
export function DocReader({ slug, docId, toast, close }: {
  slug: string
  docId: string
  toast: ToastFn
  close: () => void
}) {
  const [doc, setDoc] = useState<{ title: string; node: string; at: string
    body: string } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setDoc(null)
    setErr(null)
    getDocument(slug, docId)
      .then((d) => { if (live) setDoc(d) })
      .catch((e: Error) => { if (live) setErr(e.message) })
    return () => { live = false }
  }, [slug, docId])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])
  return (
    <div className="overlay" onClick={close}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings doc-reader" onClick={(e) => e.stopPropagation()}>
        <div className="doc-reader-head">
          <DocIcon fontSize="inherit" />
          <b>{doc?.title ?? '…'}</b>
          {doc && <span className="dim">· {doc.node} · {doc.at}</span>}
          <span className="spacer" />
          {doc && (
            <button className="dim" title="remove the card (the document is gone)"
              onClick={() => {
                dismissDocument(slug, docId)
                  .then(() => { toast([`dismissed “${doc.title}”`]); close() })
                  .catch((e: Error) => toast([`error: ${e.message}`]))
              }}>dismiss</button>
          )}
          <button className="chip-x" title="close the reader" onClick={close}>
            <CloseIcon fontSize="inherit" />
          </button>
        </div>
        {err && <div className="ask-warn">could not load the document: {err}</div>}
        {/* relative image srcs resolve against the PRESENTING node's files —
            `![](outbox/chart.png)` embeds a figure the agent saved */}
        {doc && <div className="doc-reader-body md"
          dangerouslySetInnerHTML={md(doc.body, fileBase(slug, doc.node))} />}
      </div>
    </div>
  )
}
