// canvas/docs.tsx — FR-03: presented documents (user request 2026-08-05).
// An agent presents a plan/report with orgtree_present; a small card pops
// out the SIDE of its node, and clicking it opens the markdown in-page for
// review. A reading surface, not a download (orgtree_send_file is the
// download path). The tree payload carries metadata only — the reader
// fetches the body on open.

import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { ToastFn } from '../types'
import { BASE, dismissDocument, fileBase, getDocument, mockupUrl } from '../api'
import { md } from './shared'
import { RefMdBody } from './refmd'
import type { RefWorld, ResolvedRef } from './reflinks'
import { openLightboxIfEligibleImage } from './lightbox'
import { PinFrame } from './modalpin'
import { CloseIcon, DocIcon } from '../icons'
import { fmtFull } from '../timefmt'

export interface DocMeta { id: string; title: string; at: string; format?: 'markdown' | 'html'; bytes?: number }

export interface LoadedDoc { title: string; node: string; at: string; body: string; format?: 'markdown' | 'html'; bytes?: number }

/** The same activation in the canvas chips and titled desk cards. HTML
 *  opens synchronously through a native link, never after an async fetch. */
export function PresentationCard({ slug, doc, onOpen, className, children }: {
  slug: string; doc: DocMeta; onOpen: (id: string) => void
  className: string; children: ReactNode
}) {
  if (doc.format === 'html') {
    if (BASE) return <span className={className + ' mockup-unavailable'}
      aria-disabled="true" title="Mockup previews are available in the operator view">
      {children}
    </span>
    return <a className={className} href={mockupUrl(slug, doc.id)}
      target="_blank" rel="noopener noreferrer"
      title={`open ${doc.title} in a new tab`}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}>{children}</a>
  }
  return <button className={className} title={`read ${doc.title}`}
    onPointerDown={(e) => e.stopPropagation()}
    onClick={(e) => { e.stopPropagation(); onOpen(doc.id) }}>{children}</button>
}

/** Reference and gallery readers never put HTML into the app's own DOM. */
export function MockupOpen({ slug, docId }: { slug: string; docId: string }) {
  return <div className="mockup-open">
    {BASE
      ? <p className="dim">Mockup previews are available in the operator view.</p>
      : <a href={mockupUrl(slug, docId)} target="_blank" rel="noopener noreferrer">
          Open interactive mockup in a new tab
        </a>}
  </div>
}

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
export function DocChips({ slug, docs, onOpen }: {
  slug: string
  docs: DocMeta[]
  onOpen: (id: string) => void
}) {
  return (
    <div className="doc-chips">
      {docs.slice(-4).map((d) => (
        <PresentationCard key={d.id} slug={slug} doc={d}
          className="doc-chip" onOpen={onOpen}>
          <DocIcon fontSize="inherit" />
        </PresentationCard>
      ))}
    </div>
  )
}

/** the in-page reader: title bar (✕ closes the reader; "dismiss" removes
 *  the card itself), markdown body under the desk's .md styling */
export function DocReader({ slug, docId, toast, close, refs,
  pinKind = 'doc' }: {
  slug: string
  docId: string
  toast: ToastFn
  close: () => void
  /** canonical references (`@item:org/slug`) written INSIDE the document.
   *  A presented plan is exactly the kind of prose that names an item, an
   *  agent or the mail it answers, and it is rendered markdown — so this is
   *  the DOM pass, not the React renderer. Omitted, the tokens are prose:
   *  a reader with nowhere to send anybody must not draw controls. */
  refs?: { world: RefWorld; onOpen?: (r: ResolvedRef) => void }
  /** ⚠ THIS READER IS THE ONE SURFACE WITH TWO MOUNT SITES, and a pin identity
   *  is per SURFACE, not per component: the canvas opens one of these and the
   *  docket opens another, and both can be on screen at once. Sharing one kind
   *  made the two windows one window — same rect, same z, neither movable,
   *  raisable or resizable apart from the other (found by codex-delivery,
   *  2026-09-06). Each site passes its own stable identity instead. The canvas
   *  keeps the original `doc`, so pins stored before this fix still open where
   *  they were left. */
  pinKind?: string
}) {
  const { doc, err } = useDoc(slug, docId)
  return (
    // an eligible image opens from `onPanelClick`, which the frame runs
    // BEFORE the stopPropagation every panel has (that one keeps a click
    // inside the reader from reaching `.overlay`'s backdrop-close).
    <PinFrame kind={pinKind} title={doc?.title ?? 'document'}
      panel="settings doc-reader" close={close}
      onPanelClick={openLightboxIfEligibleImage}>
        <div className="doc-reader-head">
          <DocIcon fontSize="inherit" />
          {/* ⚠ THE TITLE IS NAMED BY A CLASS, not found by tag. Every other
              pinnable surface opens with an <h3> the pinned window hides so it
              does not say its own name twice; this reader's title is a <b>
              inside a header row, which no tag-based rule can reach (measured
              in a browser by codex-delivery, 2026-09-06: pinned, the document
              title appeared in both the window bar and the panel). */}
          <b className="doc-reader-title">{doc?.title ?? '…'}</b>
          {doc && <span className="dim doc-reader-meta">
            {/* the separator belongs to the TITLE it follows, so it goes with
                it when the title stands down — otherwise a pinned reader opens
                its header on a dangling "·". The node and the time are not a
                title and stay in both modes. */}
            <span className="doc-reader-sep">· </span>
            {doc.node} · {fmtFull(doc.at)}</span>}
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
        {doc?.format === 'html' && <MockupOpen slug={slug} docId={docId} />}
        {doc && doc.format !== 'html' && <RefMdBody className="doc-reader-body md"
          html={md(doc.body, fileBase(slug, doc.node))}
          world={refs?.world} onOpen={refs?.onOpen} />}
    </PinFrame>
  )
}
