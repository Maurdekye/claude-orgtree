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
import { openLightboxIfEligibleImage } from './lightbox'
import { CloseIcon, DocIcon } from '../icons'
import { fmtFull } from '../timefmt'

export interface DocMeta { id: string; title: string; at: string }

export interface LoadedDoc { title: string; node: string; at: string; body: string }

/** fetch one document's body by id — the tree payload and the gallery list
 *  both carry metadata only, so every reading surface starts here.
 *
 *  Shared by the overlay reader (the canvas doc chips) and the gallery's
 *  reading pane so the fetch, the cancel-on-swap latch and the error state
 *  exist ONCE. The two surfaces render different chrome (overlay vs the
 *  mail-idiom right pane) — that is presentation; this is not.
 *
 *  An EMPTY `docId` fetches nothing: the gallery lists evicted cards, whose
 *  body is gone for good, and a request for one would only buy back the 404
 *  the caller already knows about. A hook cannot be called conditionally,
 *  so the condition lives here. */
export function useDoc(slug: string, docId: string): {
  doc: LoadedDoc | null
  err: string | null
} {
  const [doc, setDoc] = useState<LoadedDoc | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setDoc(null)
    setErr(null)
    if (!docId) return
    getDocument(slug, docId)
      .then((d) => { if (live) setDoc(d) })
      .catch((e: Error) => { if (live) setErr(e.message) })
    return () => { live = false }
  }, [slug, docId])
  return { doc, err }
}

/** the one dismiss path (user request 2026-09-03 put a second one in the
 *  gallery's viewer). DELETE runs through `req`, which bumps the livebus, so
 *  every polled surface — the gallery list included — drops the row without
 *  anyone wiring a refresh. `after` is for chrome that must also close. */
export function dismissDoc(slug: string, docId: string, title: string,
  toast: ToastFn, after?: () => void): void {
  dismissDocument(slug, docId)
    .then(() => { toast([`dismissed “${title}”`]); after?.() })
    .catch((e: Error) => toast([`error: ${e.message}`]))
}

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
  const { doc, err } = useDoc(slug, docId)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])
  return (
    <div className="overlay" onClick={close}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings doc-reader" onClick={(e) => {
        // an eligible image opens directly here rather than relying on the
        // click bubbling past this stopPropagation, which everything else
        // in the reader still needs (it keeps `.overlay`'s backdrop-close
        // from firing for clicks inside).
        openLightboxIfEligibleImage(e)
        e.stopPropagation()
      }}>
        <div className="doc-reader-head">
          <DocIcon fontSize="inherit" />
          <b>{doc?.title ?? '…'}</b>
          {doc && <span className="dim">· {doc.node} · {fmtFull(doc.at)}</span>}
          <span className="spacer" />
          {doc && (
            <button className="dim" title="remove the card (the document is gone)"
              onClick={() => dismissDoc(slug, docId, doc.title, toast, close)}>
              dismiss</button>
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
