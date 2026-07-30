import { useCallback, useEffect, useRef, useState } from 'react'
import {
  audienceAction, clearInbox, createOrg, deleteOrg, getAudiences, getInbox,
  getOrgMd, getTree, listOrgs, openWs, putOrgMd, runOp, saveSettings,
} from './api'
import { ConfirmModal, MailList, OrgCanvas, useEsc } from './Canvas'
import { DirList } from './forms'

const TIER_LETTER = { haiku: 'H', sonnet: 'S', opus: 'O', fable: 'F' }
const USER = '@user'       // typed actor sentinels — a node may be NAMED user/system
const SYSTEM = '@system'

const slugFromPath = () => {
  const m = location.pathname.match(/^\/o\/([a-z0-9@-]+)/)
  return m ? m[1] : null
}

export default function App() {
  const [orgs, setOrgs] = useState([])
  const [slug, setSlug] = useState(slugFromPath)   // /o/<slug> survives refresh
  const [tree, setTree] = useState(null)
  const [toasts, setToasts] = useState([])
  const [error, setError] = useState(null)
  const [pulse, setPulse] = useState(null)
  const [streamEvt, setStreamEvt] = useState(null)
  const [mailEvt, setMailEvt] = useState(null)
  const [activity, setActivity] = useState({})   // node → {phase, tool}
  const [showSettings, setShowSettings] = useState(false)
  const [showInbox, setShowInbox] = useState(false)
  const [drawer, setDrawer] = useState(false)
  const [doomedOrg, setDoomedOrg] = useState(null)   // org row pending deletion
  const wsRef = useRef(null)

  const toast = useCallback((lines) => {
    if (!lines || !lines.length) return
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, lines }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 12000)
  }, [])

  const refreshOrgs = useCallback(() => listOrgs().then(setOrgs).catch((e) => setError(e.message)), [])
  const refreshTree = useCallback((s) => {
    if (!s) return
    getTree(s).then(setTree).catch((e) => setError(e.message))
  }, [])

  useEffect(() => { refreshOrgs() }, [refreshOrgs])

  useEffect(() => {                    // back/forward keep working
    const onPop = () => setSlug(slugFromPath())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  useEffect(() => {                    // Escape dismisses the org drawer
    const onKey = (e) => { if (e.key === 'Escape') setDrawer(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  useEffect(() => {                    // the active org lives in the path
    const want = slug ? `/o/${slug}` : '/'
    if (location.pathname !== want) history.pushState(null, '', want)
  }, [slug])

  useEffect(() => {
    if (!slug) return
    refreshTree(slug)
    wsRef.current?.close()
    wsRef.current = openWs(slug, (ev) => {
      let data = null
      try { data = JSON.parse(ev.data) } catch { /* ignore */ }
      if (data?.type === 'mail') {     // spark on the wire — pure animation
        setMailEvt({ from: data.from, to: data.to, t: Date.now() })
        return
      }
      if (data?.type === 'node_stream') {
        setStreamEvt({ node: data.node, kind: data.kind, text: data.text, t: Date.now() })
        setActivity((a) => ({ ...a, [data.node]:
          data.kind === 'tool' ? { phase: 'tool', tool: data.text }
            : { phase: 'writing' } }))
        return   // live feed only — no tree refetch per message
      }
      if (data?.type === 'node_event') {
        setPulse({ node: data.node, event: data.event, t: Date.now() })
        if (data.event === 'turn_started') {
          setActivity((a) => ({ ...a, [data.node]: { phase: 'thinking' } }))
        } else if (data.event === 'turn_done') {
          setActivity((a) => { const n = { ...a }; delete n[data.node]; return n })
        }
      }
      refreshTree(slug)
    })
    return () => wsRef.current?.close()
  }, [slug, refreshTree])

  const op = useCallback((body) =>
    runOp(slug, body)
      .then((r) => { toast(r.warnings); refreshTree(slug); refreshOrgs(); return r })
      .catch((e) => { toast([`⛔ ${e.message}`]); throw e }),
    [slug, toast, refreshTree, refreshOrgs])

  const pick = (s) => { setSlug(s); setShowSettings(false); setDrawer(false) }
  const goHome = () => { setSlug(null); setDrawer(false) }

  const orgPanel = (
    <>
      <h1>orgtree</h1>
      {slug && <button className="home" onClick={goHome}>⌂ all organizations</button>}
      <nav>
        {orgs.map((o) => (
          <div key={o.slug} role="button" tabIndex={0}
            className={'org' + (o.slug === slug ? ' current' : '')}
            onClick={() => pick(o.slug)}
            onKeyDown={(e) => { if (e.key === 'Enter') pick(o.slug) }}>
            <span>{o.name}</span>
            <span className="spacer" />
            <span className="dim">{o.live}/{o.nodes} live</span>
            <button className="org-del"
              onClick={(e) => { e.stopPropagation(); setDoomedOrg(o) }}>🗑</button>
          </div>
        ))}
        {!orgs.length && <div className="dim pad">no organizations yet</div>}
      </nav>
      <NewOrg onCreate={(name, dirs) =>
        createOrg(name, dirs).then((r) => { refreshOrgs(); pick(r.slug) })
          .catch((e) => toast([`⛔ ${e.message}`]))} />
    </>
  )

  return (
    <div className="app">
      {/* no active org: the org list IS the screen */}
      {!slug && (
        <div className="welcome">
          <div className="welcome-card">{orgPanel}</div>
        </div>
      )}

      {/* active org: full foreground; the list hides in a drawer */}
      {slug && (
        <main className="solo">
          {error && <div className="error">{error}</div>}
          {tree ? (
            <>
              <header className="orgbar">
                <button className="iconbtn" onClick={() => setDrawer(true)}>☰</button>
                <h2>{tree.name}</h2>
                {/* the ledger self-audit only speaks when something is wrong;
                    credit totals live on the eye's bar */}
                {!tree.audit.no_overdraft &&
                  <span className="chip bad">⚠ {tree.audit.problems.join(', ')}</span>}
                <span className="chip">{tree.audit.live_nodes} live</span>
                {tree.cost_usd_total > 0 &&
                  <span className="chip">${tree.cost_usd_total.toFixed(2)}</span>}
                {tree.fable_lock &&
                  <span className="chip bad" title={tree.fable_lock.at}>⛔ fable limit</span>}
                <span style={{ flex: 1 }} />
                <button onClick={() => setShowSettings(true)}>⚙ settings</button>
              </header>
              <OrgCanvas tree={tree} op={op} slug={slug} pulse={pulse} toast={toast}
                streamEvt={streamEvt} activity={activity} mailEvt={mailEvt}
                onInbox={() => setShowInbox(true)} />
              {showSettings && (
                <SettingsPanel tree={tree} toast={toast}
                  close={() => { setShowSettings(false); refreshTree(slug) }} />
              )}
              {showInbox && (
                <InboxPanel slug={slug} tree={tree} toast={toast}
                  close={() => { setShowInbox(false); refreshTree(slug) }} />
              )}
            </>
          ) : <div className="empty">loading {slug}…</div>}
        </main>
      )}

      {drawer && (
        <div className="drawer-backdrop" onClick={() => setDrawer(false)}>
          <aside className="drawer" onClick={(e) => e.stopPropagation()}>
            {orgPanel}
          </aside>
        </div>
      )}

      {doomedOrg && (
        <ConfirmModal title={`permanently delete ${doomedOrg.name}?`}
          body={`Erases the organization and its ${doomedOrg.nodes} node(s) — ledger, mail, lineage, audiences. Workspace and scratch folders remain on disk. This cannot be undone.`}
          confirmLabel="delete organization"
          onConfirm={() => deleteOrg(doomedOrg.slug)
            .then(() => { if (slug === doomedOrg.slug) setSlug(null); refreshOrgs() })
            .catch((e) => toast([`⛔ ${e.message}`]))}
          close={() => setDoomedOrg(null)} />
      )}

      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className="toast" onClick={() =>
            setToasts((x) => x.filter((y) => y.id !== t.id))}>
            {t.lines.map((l, i) => <div key={i}>{l}</div>)}
          </div>
        ))}
      </div>
    </div>
  )
}

function NewOrg({ onCreate }) {
  const [open, setOpen] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const [name, setName] = useState('')
  const [dirs, setDirs] = useState([])
  const reset = () => { setOpen(false); setAdvanced(false); setName(''); setDirs([]) }
  if (!open) return <button className="primary" onClick={() => setOpen(true)}>+ new organization</button>
  return (
    <form className="stack" onSubmit={(e) => {
      e.preventDefault()
      onCreate(name, dirs.map((s) => s.trim()).filter(Boolean))
      reset()
    }}>
      <input autoFocus placeholder="organization name" value={name}
        onChange={(e) => setName(e.target.value)} required />
      <button type="button" className="disclosure" aria-expanded={advanced}
        onClick={() => setAdvanced(!advanced)}>
        {advanced ? '▼' : '▶'} advanced
      </button>
      {advanced && (
        <div className="advanced">
          <div className="field-label">also grant existing folders</div>
          <DirList dirs={dirs} onChange={setDirs} />
        </div>
      )}
      <div className="row">
        <button type="submit" className="primary">create</button>
        <button type="button" onClick={reset}>cancel</button>
      </div>
    </form>
  )
}

function flatNodes(tree) {
  const map = new Map()
  const walk = (n) => { map.set(n.id, n); n.children.forEach(walk) }
  tree.roots.forEach(walk)
  return map
}

function SenderChip({ id, nodes }) {
  if (id === SYSTEM || id === 'system') return <b className="dim">system</b>
  if (id === USER) return <b>you</b>
  const n = nodes.get(id)
  if (!n) return <b>{id}</b>
  return (
    <span className={'sender ' + n.state} title={`${n.tier} · ${n.state}`}>
      <span className={'tier t-' + n.tier}>{TIER_LETTER[n.tier] ?? '?'}</span>
      <b>{id}</b>
    </span>
  )
}

function InboxPanel({ slug, tree, toast, close }) {
  useEsc(close)
  const [box, setBox] = useState(null)
  const [aud, setAud] = useState(null)
  const nodes = flatNodes(tree)
  const reload = useCallback(() => {
    getInbox(slug).then(setBox).catch((e) => toast([`⛔ ${e.message}`]))
    getAudiences(slug).then(setAud).catch(() => {})
  }, [slug, toast])
  useEffect(() => { reload() }, [reload])
  const userAud = aud?.audiences.filter((a) => a.grantor === USER) ?? []
  const userReqs = aud?.requests.filter((r) => r.target === USER && r.currently_at === USER) ?? []
  const act = (action, node, target) =>
    audienceAction(slug, action, node, target).then(reload).catch((e) => toast([`⛔ ${e.message}`]))
  return (
    <div className="overlay" onClick={close}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3>✉ your inbox</h3>
        {userReqs.length > 0 && (
          <>
            <div className="field-label">audience requests</div>
            {userReqs.map((r) => (
              <div className="hist-row" key={r.from}>
                <SenderChip id={r.from} nodes={nodes} />
                <span className="dim">{r.reason}</span>
                <button className="primary" onClick={() => act('grant', r.from)}>grant</button>
                <button onClick={() => act('deny', r.from, USER)}>deny</button>
              </div>
            ))}
          </>
        )}
        {userAud.length > 0 && (
          <>
            <div className="field-label">audience holders</div>
            <div className="row" style={{ flexWrap: 'wrap' }}>
              {userAud.map((a) => (
                <span key={a.grantee} className="badge free">
                  👂 {a.grantee}
                  <button className="chip-x" title="rescind"
                    onClick={() => act('revoke', a.grantee)}>✕</button>
                </span>
              ))}
            </div>
          </>
        )}
        <div className="inbox-list">
          {box == null
            ? <div className="dim">loading…</div>
            : <MailList pending={box.pending} delivered={box.delivered}
                waitLabel="unread"
                sender={(id) => <SenderChip id={id} nodes={nodes} />} />}
        </div>
        <div className="row">
          {box?.pending.length > 0 && <button onClick={() =>
            clearInbox(slug).then(reload).catch((e) => toast([`⛔ ${e.message}`]))}>mark all read</button>}
          <button className="primary" onClick={close}>close</button>
        </div>
      </div>
    </div>
  )
}

function SettingsPanel({ tree, toast, close }) {
  useEsc(close)
  const [dirs, setDirs] = useState(tree.dirs.filter((d) => d !== tree.workspace))
  const [maxTop, setMaxTop] = useState(tree.max_top_grant ?? 1000)
  const [orgMd, setOrgMd] = useState(null)
  const [fablePolicy, setFablePolicy] = useState(tree.fable_limit_policy ?? 'halt')
  useEffect(() => {
    getOrgMd(tree.slug).then((r) => setOrgMd(r.content)).catch(() => setOrgMd(''))
  }, [tree.slug])
  return (
    <div className="overlay" onClick={close}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3>⚙ {tree.name} — settings</h3>
        <div className="field-label">workspace</div>
        <div className="chip mono block">{tree.workspace}</div>
        <div className="field-label">external folders</div>
        <DirList dirs={dirs} onChange={setDirs} />
        <div className="field-label">top-level grant cap</div>
        <input type="number" min="1" step="1" value={maxTop} style={{ width: '8em' }}
          onChange={(e) => setMaxTop(e.target.value)} />
        <div className="field-label">fable weekly-limit policy</div>
        <select value={fablePolicy} onChange={(e) => setFablePolicy(e.target.value)}>
          <option value="halt">halt (default)</option>
          <option value="opus">switch to opus</option>
          <option value="dissolve">dissolve subtree</option>
        </select>
        <div className="field-label">org.md</div>
        <textarea rows={6} value={orgMd ?? ''} disabled={orgMd == null}
          onChange={(e) => setOrgMd(e.target.value)} />
        {tree.fable_lock && (
          <button className="danger" onClick={() =>
            saveSettings(tree.slug, dirs.map((s) => s.trim()).filter(Boolean), undefined, true)
              .then((r) => { toast(r.warnings); close() })
              .catch((e) => toast([`⛔ ${e.message}`]))}>
            ⛔ clear the fable weekly-limit lock (your decree)</button>
        )}
        <div className="row">
          <button className="primary" onClick={() =>
            Promise.all([
              saveSettings(tree.slug, dirs.map((s) => s.trim()).filter(Boolean), +maxTop || undefined,
                false, fablePolicy),
              orgMd != null ? putOrgMd(tree.slug, orgMd) : Promise.resolve({}),
            ]).then(([r]) => { toast(r.warnings); close() })
              .catch((e) => toast([`⛔ ${e.message}`]))}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>
  )
}
