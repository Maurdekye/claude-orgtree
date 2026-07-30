// The in-app folder picker (user ruling): a fully custom dialog backed by the
// backend's /api/fs listing, so it works from ANY browser that can reach the
// UI — unlike a native dialog, which opens on the server's own desktop.
//
// Usage: `pickFolder()` from anywhere returns a Promise<{path: string|null}>.
// App mounts one <FolderPickerHost /> that serves every request.
import { useEffect, useRef, useState } from 'react'
import { getFs } from './api'
import { ArrowUpIcon, FolderIcon, HomeIcon, StorageIcon } from './icons'

let openFn = null
export const pickFolder = () =>
  (openFn ? openFn() : Promise.resolve({ path: null }))

export function FolderPickerHost() {
  const [req, setReq] = useState(null)      // {resolve} while the dialog is up
  const [cur, setCur] = useState(null)      // {path, parent, dirs, home?}
  const [err, setErr] = useState(null)
  const homeRef = useRef(null)
  const nav = (p) => getFs(p)
    .then((r) => { if (r.home) homeRef.current = r.home; setCur(r); setErr(null) })
    .catch((e) => setErr(e.message))
  useEffect(() => {
    openFn = () => new Promise((resolve) => { setReq({ resolve }); nav('') })
    return () => { openFn = null }
  }, [])
  useEffect(() => {
    if (!req) return
    const onKey = (e) => { if (e.key === 'Escape') finish(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req])
  const finish = (path) => { req?.resolve({ path }); setReq(null); setCur(null) }
  if (!req) return null
  // breadcrumbs from the current path, each segment navigable
  const sep = cur?.path?.includes('/') && !cur?.path?.includes('\\') ? '/' : '\\'
  const segs = (cur?.path ?? '').split(/[\\/]/).filter(Boolean)
  const crumb = (i) => {
    const head = segs.slice(0, i + 1).join(sep)
    return head.endsWith(':') ? head + sep : head
  }
  return (
    <div className="overlay picker-overlay" onClick={() => finish(null)}>
      <div className="settings picker" onClick={(e) => e.stopPropagation()}>
        <h3><FolderIcon fontSize="inherit" /> choose a folder</h3>
        <div className="picker-bar">
          <button className="iconbtn" title="drives / roots"
            onClick={() => nav('')}><StorageIcon fontSize="inherit" /></button>
          {homeRef.current &&
            <button className="iconbtn" title="home"
              onClick={() => nav(homeRef.current)}><HomeIcon fontSize="inherit" /></button>}
          {cur?.parent != null && cur?.path &&
            <button className="iconbtn" title="up"
              onClick={() => nav(cur.parent)}><ArrowUpIcon fontSize="inherit" /></button>}
          <span className="picker-crumbs mono">
            {segs.map((s, i) => (
              <span key={i}>
                <button className="crumb" onClick={() => nav(crumb(i))}>{s}</button>
                {i < segs.length - 1 ? sep : ''}
              </span>
            ))}
            {!segs.length && <span className="dim">this computer</span>}
          </span>
        </div>
        {err && <div className="picker-err">{err}</div>}
        <div className="picker-list">
          {cur?.dirs.map((d) => (
            <button key={d.path} className="picker-row"
              onDoubleClick={() => nav(d.path)}
              onClick={() => nav(d.path)}>
              <FolderIcon fontSize="inherit" /> {d.name}
            </button>
          ))}
          {cur && !cur.dirs.length && <div className="dim pad">no subfolders</div>}
        </div>
        <div className="row">
          <span className="dim mono picker-sel">{cur?.path || ''}</span>
          <span className="spacer" />
          <button className="primary" disabled={!cur?.path}
            onClick={() => finish(cur.path)}>select this folder</button>
          <button onClick={() => finish(null)}>cancel</button>
        </div>
      </div>
    </div>
  )
}
