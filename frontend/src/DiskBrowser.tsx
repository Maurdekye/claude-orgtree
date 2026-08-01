// The recovery browser (user verdict 2026-07-31): the org's virtual disk,
// files by size DESCENDING — the sort that matters when freeing space fast.
// It exists for the state nothing else can fix: a hard-full disk, where no
// turn can run so no agent can delete anything. It therefore depends on
// NOTHING but the backend (reads/deletes go over \\wsl.localhost — the
// container can be stopped, the disk 100% full). Kiosk visitors get the
// full tool (ruled); the server enforces the deletion policy, this UI only
// mirrors it (blocked rows greyed with the reason).
import { useCallback, useEffect, useRef, useState } from 'react'
import { diskDelete, diskFileUrl, diskGrow, getDisk } from './api'
import type { DiskPayload, ToastFn } from './types'
import {
  CloseIcon, DeleteIcon, DownloadIcon, StorageIcon, WarnIcon,
} from './icons'

const fmt = (b: number | null | undefined): string => (b == null ? '?'
  : b >= 1e9 ? (b / 1e9).toFixed(2) + ' GB'
  : b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB'
  : Math.max(1, Math.round(b / 1024)) + ' KB')

export function DiskBrowser({ slug, isPublic, toast, close }: {
  slug: string
  isPublic: boolean
  toast: ToastFn
  close: () => void
}) {
  const [data, setData] = useState<DiskPayload | null>(null)
  const [sel, setSel] = useState<Set<string>>(() => new Set())
  const [armed, setArmed] = useState(false)   // two-click delete latch
  const [busy, setBusy] = useState(false)
  const [growTo, setGrowTo] = useState('')
  const selRef = useRef(sel)
  selRef.current = sel

  const load = useCallback((offset = 0) =>
    getDisk(slug, offset).then((d) => {
      setData((prev) => (offset && prev
        ? { ...d, files: [...prev.files, ...d.files] } : d))
      // selection survives refresh; entries that vanished drop out
      const alive = new Set(d.files.map((f) => f.path))
      if (!offset) {
        setSel((s) => new Set([...s].filter((p) => alive.has(p))))
      }
    }).catch((e: Error) => toast([`error: ${e.message}`])), [slug, toast])

  useEffect(() => { load() }, [load])
  useEffect(() => {              // the live readout: watch the number come down
    const t = setInterval(() => load(0), 8000)
    return () => clearInterval(t)
  }, [load])

  const doDelete = () => {
    if (!armed) { setArmed(true); return }
    setArmed(false)
    setBusy(true)
    diskDelete(slug, [...selRef.current])
      .then((r) => {
        const bad = r.results.filter((x) => !x.ok)
        const ok = r.results.length - bad.length
        toast([`deleted ${ok} file(s)` + (bad.length
          ? ` · ${bad.length} refused (${bad[0].error})` : '')])
        setSel(new Set())
        load(0)
      })
      .catch((e: Error) => toast([`error: ${e.message}`]))
      .finally(() => setBusy(false))
  }

  const frac = data?.used != null && data?.total ? data.used / data.total : 0
  const selBytes = data
    ? data.files.filter((f) => sel.has(f.path))
      .reduce((a, f) => a + f.bytes, 0) : 0

  return (
    <div className="overlay disk-overlay" onClick={close}>
      <div className="settings disk-browser" onClick={(e) => e.stopPropagation()}>
        <h3>
          <StorageIcon fontSize="inherit" /> org disk
          <span className="spacer" />
          <button className="iconbtn" title="close" onClick={close}>
            <CloseIcon fontSize="inherit" /></button>
        </h3>
        {data && (
          <div className={'disk-usage' + (frac >= 0.9 ? ' bad'
            : frac >= 0.8 ? ' warn' : '')}>
            <div className="disk-bar">
              <div className="disk-bar-fill"
                style={{ width: `${Math.min(100, frac * 100)}%` }} />
            </div>
            <span>{fmt(data.used)} / {fmt(data.total)}
              {data.full ? ' — FULL (writes are failing)'
                : data.blocked ? ' — turns paused (soft cap)' : ''}</span>
          </div>
        )}
        <div className="disk-list">
          {!data && <div className="dim pad">measuring…</div>}
          {data?.files.map((f) => (
            <label key={f.path}
              className={'disk-row' + (f.class === 'blocked' ? ' blocked' : '')}
              title={f.reason || f.path}>
              <input type="checkbox" disabled={f.class === 'blocked' || busy}
                checked={sel.has(f.path)}
                onChange={(e) => setSel((s) => {
                  const n = new Set(s)
                  if (e.target.checked) n.add(f.path); else n.delete(f.path)
                  return n
                })} />
              <span className="disk-size mono">{fmt(f.bytes)}</span>
              <span className="disk-path mono">{f.path}</span>
              {f.class === 'reclaimable' &&
                <span className="disk-cls ok">reclaimable</span>}
              {f.class === 'blocked' &&
                <span className="disk-cls">{f.reason}</span>}
              <a className="iconbtn" href={diskFileUrl(slug, f.path)}
                download title="download" onClick={(e) => e.stopPropagation()}>
                <DownloadIcon fontSize="inherit" /></a>
            </label>
          ))}
          {data && data.files.length >= data.offset + data.limit && (
            <button className="addrow" onClick={() => load(data.files.length)}>
              load more…</button>
          )}
          {data && !data.files.length &&
            <div className="dim pad">the disk holds no files</div>}
        </div>
        <div className="row">
          {sel.size > 0 && (
            <button className={'disk-del' + (armed ? ' armed' : '')}
              disabled={busy} onClick={doDelete}
              onMouseLeave={() => setArmed(false)}>
              <DeleteIcon fontSize="inherit" />
              {armed ? `really delete ${sel.size} file(s)?`
                : `delete ${sel.size} selected (${fmt(selBytes)})`}
            </button>
          )}
          <span className="spacer" />
          {!isPublic && (
            <>
              <input className="disk-grow" type="number" min="1"
                placeholder="grow to MB" value={growTo}
                onChange={(e) => setGrowTo(e.target.value)} />
              <button disabled={busy || !growTo}
                title="online grow — extends the disk without stopping anything"
                onClick={() => {
                  setBusy(true)
                  diskGrow(slug, parseInt(growTo, 10))
                    .then((r) => { toast([`disk grown to ${r.size_mb} MB`]); setGrowTo(''); load(0) })
                    .catch((e: Error) => toast([`error: ${e.message}`]))
                    .finally(() => setBusy(false))
                }}>grow</button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// The HARD-FULL alert: screen-wide and PERSISTENT while the condition holds —
// deliberately NOT a toast (toasts self-destruct in 12 s; this is a state
// that requires action). It renders from tree state, so it survives reloads,
// and dismisses itself when usage drops. It does not auto-open the browser
// (explicit user refinement) — it carries the button.
export function DiskFullAlert({ onOpen }: { onOpen: () => void }) {
  return (
    <div className="disk-alert">
      <WarnIcon fontSize="inherit" />
      <b>The org disk is FULL</b> — every write is failing with ENOSPC and no
      agent can fix it from inside. Free space to resume.
      <button onClick={onOpen}>open the recovery browser</button>
    </div>
  )
}
