// canvas/img.tsx — inline image attachments (user spec 2026-08-25: agents
// present images as part of a response, and images the user attaches render
// viewable directly — in both cases the bytes already sit in a node's scratch
// and the /file endpoint serves them; everything here is presentation).

import { CloseIcon, DownloadIcon } from '../icons'
import { openLightbox } from './lightbox'

// what renders in an <img>: the browser decodes these regardless of the
// served content-type (image sniffing), so the extension is the whole test
const IMG_EXT = /\.(png|jpe?g|gif|webp|avif|bmp|svg|ico)$/i
export const isImg = (name: string | null | undefined): boolean =>
  IMG_EXT.test(name ?? '')

export const fmtBytes = (n: number | null | undefined) => (n == null ? '0 B'
  : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB`
  : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`)

// The envelope names delivered attachments in-band (supervisor._envelope:
// "[ATTACHED FILE: uploads/x.png (12 KB) — in your working folder]"), which
// the transcript then replays verbatim — the ONE machine-chrome line
// stripEnvelope left in the bubble as literal text. Parse those lines out;
// the caller renders them as real attachments (images viewable in place).
const ATT_LINE = /^\[ATTACHED FILE: (.+) \((\d+ K?B)\) — in your working folder\]$/
export interface AttachedFile { path: string; size: string }
export const parseAttachedFiles = (text: string): { rest: string; files: AttachedFile[] } => {
  if (!text.includes('[ATTACHED FILE:')) return { rest: text, files: [] }
  const files: AttachedFile[] = []
  const kept = text.split('\n').filter((l) => {
    const m = ATT_LINE.exec(l.trim())
    if (!m) return true
    files.push({ path: m[1]!, size: m[2]! })
    return false
  })
  return { rest: kept.join('\n').trim(), files }
}

/** one image attachment, viewable in place: a bounded thumbnail (click =
 *  lightbox), a caption with the name + size, a download link, and — on the
 *  composer's staged copies — the remove ✕. Non-images keep their existing
 *  chips at every call site; this component is only ever handed an image. */
export function AttachThumb({ href, name, meta, note, dim, onRemove }: {
  href: string
  name: string
  /** pre-formatted size/detail suffix (call sites hold bytes OR a size string) */
  meta?: string
  note?: string
  dim?: boolean
  onRemove?: () => void
}) {
  return (
    <span className={'attach-thumbwrap' + (dim ? ' dim' : '')}>
      <img className="attach-thumb" src={href} alt={name} loading="lazy"
        title={`${name} — click to view`}
        onClick={() => openLightbox(href, { name, download: href })} />
      <span className="attach-thumbcap">
        <span className="atc-name">{name}</span>
        {meta && <span className="dim">{meta}</span>}
        <a className="fdl" href={href} download={name} title="download"
          onClick={(e) => e.stopPropagation()}>
          <DownloadIcon fontSize="inherit" /></a>
        {onRemove && (
          <button className="chip-x" title="remove from this message"
            onClick={onRemove}><CloseIcon fontSize="inherit" /></button>)}
      </span>
      {note && <span className="fc-note">{note}</span>}
    </span>
  )
}
