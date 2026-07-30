import { useCallback, useEffect, useRef, useState } from 'react'
import {
  audienceAction, clearInbox, createOrg, creditDecide, deleteOrg, getAudiences,
  getInbox, getOrgMd, getTree, killAll, listOrgs, markRead, openWs, putOrgMd,
  resumeFrozen, runOp, saveSettings,
} from './api'
import { ConfirmModal, MailFolders, MailList, OrgCanvas, useEsc } from './Canvas'
import {
  BlockIcon, ChevronRightIcon, CloseIcon, DeleteIcon, ExpandMoreIcon,
  HearingIcon, HomeIcon, LockIcon, LockOpenIcon, MailIcon, MenuIcon,
  PlayIcon, SettingsIcon, SparkIcon, StopIcon, WarnIcon,
} from './icons'
import { DirList } from './forms'
import { FolderPickerHost } from './picker'

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
  const [killArmed, setKillArmed] = useState(false)  // the killswitch latch
  const wsRef = useRef(null)
  useEffect(() => {           // an unlatched killswitch re-latches on its own
    if (!killArmed) return
    const t = setTimeout(() => setKillArmed(false), 6000)
    return () => clearTimeout(t)
  }, [killArmed])

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
    // the WS must SURVIVE backend restarts (updates, redeploys): without
    // auto-reconnect every state indicator froze at its last value until a
    // manual page reload — the "states never line up" bug
    let dead = false
    let timer = null
    const connect = () => {
      if (dead) return
      refreshTree(slug)
      setActivity({})                  // drop indicators from before the gap
      wsRef.current = openWs(slug, handleWs,
        () => { if (!dead) timer = setTimeout(connect, 1500) })
    }
    const handleWs = (ev) => {
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
        if (data.event === 'frozen') {   // usage-limit popup (user ruling)
          toast([`${data.node} hit a usage limit and is FROZEN — use the resume button in the top bar when the limit resets`])
          refreshTree(slug)
        }
        if (data.event === 'resumed') refreshTree(slug)
        if (data.event === 'turn_started') {
          setActivity((a) => ({ ...a, [data.node]: { phase: 'thinking' } }))
        } else if (data.event === 'turn_done') {
          setActivity((a) => { const n = { ...a }; delete n[data.node]; return n })
        }
      }
      refreshTree(slug)
    }
    connect()
    return () => { dead = true; clearTimeout(timer); wsRef.current?.close() }
  }, [slug, refreshTree])

  const op = useCallback((body) =>
    runOp(slug, body)
      .then((r) => { toast(r.warnings); refreshTree(slug); refreshOrgs(); return r })
      .catch((e) => { toast([`error: ${e.message}`]); throw e }),
    [slug, toast, refreshTree, refreshOrgs])

  const pick = (s) => { setSlug(s); setShowSettings(false); setDrawer(false) }
  const goHome = () => { setSlug(null); setDrawer(false) }

  const orgPanel = (
    <>
      <h1><SparkIcon fontSize="inherit" /> orgtree</h1>
      {slug && <button className="home" onClick={goHome}><HomeIcon fontSize="inherit" /> all organizations</button>}
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
              onClick={(e) => { e.stopPropagation(); setDoomedOrg(o) }}><DeleteIcon fontSize="inherit" /></button>
          </div>
        ))}
        {!orgs.length && <div className="dim pad">no organizations yet</div>}
      </nav>
      <NewOrg onCreate={(name, dirs) =>
        createOrg(name, dirs).then((r) => { refreshOrgs(); pick(r.slug) })
          .catch((e) => toast([`error: ${e.message}`]))} />
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
                <button className="iconbtn" onClick={() => setDrawer(true)}><MenuIcon fontSize="inherit" /></button>
                <h2>{tree.name}</h2>
                {/* the ledger self-audit only speaks when something is wrong;
                    credit totals live on the eye's bar */}
                {!tree.audit.no_overdraft &&
                  <span className="chip bad"><WarnIcon fontSize="inherit" /> {tree.audit.problems.join(', ')}</span>}
                {(() => {   // active-agent summary: total · working · per-model
                  const ns = [...flatNodes(tree).values()].filter((n) => n.state === 'live')
                  const busy = ns.filter((n) => n.busy).length
                  const byTier = {}
                  for (const n of ns) byTier[n.tier] = (byTier[n.tier] ?? 0) + 1
                  return (
                    <span className="chip agents"
                      title="live agents · currently working · breakdown by model">
                      {ns.length} live{busy > 0 ? ` · ${busy} working` : ''}
                      {['haiku', 'sonnet', 'opus', 'fable']
                        .filter((t) => byTier[t])
                        .map((t) => <b key={t} className={'t-' + t}>{TIER_LETTER[t]}{byTier[t]}</b>)}
                    </span>
                  )
                })()}
                {tree.cost_usd_total > 0 &&
                  <span className="chip">${tree.cost_usd_total.toFixed(2)}</span>}
                {tree.fable_lock &&
                  <span className="chip bad" title={tree.fable_lock.at}><BlockIcon fontSize="inherit" /> fable limit</span>}
                {(() => {   // usage-limit freeze: ▶ restarts every frozen agent
                  const frozen = [...flatNodes(tree).values()].filter((n) => n.frozen)
                  if (!frozen.length) return null
                  const until = frozen.map((n) => n.frozen.until).find(Boolean)
                  return (
                    <>
                      <button className="resume-all" title={frozen.map((n) => n.id).join(', ')}
                        onClick={() => resumeFrozen(slug)
                          .then((r) => { toast([`resumed ${r.resumed.length} agent(s)`]); refreshTree(slug) })
                          .catch((e) => toast([`error: ${e.message}`]))}>
                        <PlayIcon fontSize="inherit" /> resume {frozen.length}
                      </button>
                      <span className="resume-note">
                        usage limit hit — {frozen.length} agent{frozen.length > 1 ? 's' : ''} frozen
                        {until ? ` · resumable ${until}` : ''}
                      </span>
                    </>
                  )
                })()}
                <span style={{ flex: 1 }} />
                {/* the killswitch: unlatch, then press — interrupts EVERY
                    active agent and clears their queues */}
                <span className="kill">
                  <button className={'kill-latch' + (killArmed ? ' open' : '')}
                    title={killArmed ? 're-latch' : 'unlatch the killswitch'}
                    onClick={() => setKillArmed((a) => !a)}>
                    {killArmed ? <LockOpenIcon fontSize="inherit" /> : <LockIcon fontSize="inherit" />}</button>
                  <button className="kill-btn" disabled={!killArmed}
                    title="interrupt every active agent at once"
                    onClick={() => {
                      setKillArmed(false)
                      killAll(slug)
                        .then((r) => { toast([`interrupted ${r.interrupted.length} agent(s); queues cleared`]); refreshTree(slug) })
                        .catch((e) => toast([`error: ${e.message}`]))
                    }}><StopIcon fontSize="inherit" /> STOP ALL</button>
                </span>
                <button onClick={() => setShowSettings(true)}><SettingsIcon fontSize="inherit" /> settings</button>
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
                  refresh={() => refreshTree(slug)}
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
            .catch((e) => toast([`error: ${e.message}`]))}
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
      {/* the in-app folder picker: LAST so it stacks above every modal */}
      <FolderPickerHost />
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
        {advanced ? <ExpandMoreIcon fontSize="inherit" /> : <ChevronRightIcon fontSize="inherit" />} advanced
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

function InboxPanel({ slug, tree, toast, refresh, close }) {
  useEsc(close)
  const [box, setBox] = useState(null)
  const [aud, setAud] = useState(null)
  const [folder, setFolder] = useState('inbox')
  const nodes = flatNodes(tree)
  const reload = useCallback(() => {
    getInbox(slug).then(setBox).catch((e) => toast([`error: ${e.message}`]))
    getAudiences(slug).then(setAud).catch(() => {})
  }, [slug, toast])
  useEffect(() => { reload() }, [reload])
  const userAud = aud?.audiences.filter((a) => a.grantor === USER) ?? []
  const userReqs = aud?.requests.filter((r) => r.target === USER && r.currently_at === USER) ?? []
  const act = (action, node, target) =>
    audienceAction(slug, action, node, target).then(reload).catch((e) => toast([`error: ${e.message}`]))
  return (
    <div className="overlay" onClick={close}>
      <div className="settings wide" onClick={(e) => e.stopPropagation()}>
        <h3><MailIcon fontSize="inherit" /> your inbox</h3>
        {(tree.credit_requests ?? []).length > 0 && (
          <>
            <div className="field-label">credit requests</div>
            {tree.credit_requests.map((r) => (
              <div className="credreq" key={r.id}>
                <div className="cr-head">
                  <SenderChip id={r.node} nodes={nodes} />
                  <b>{r.old} → {r.new}</b>
                  <span className="dim">(+{r.new - r.old})</span>
                  <span className="dim">{r.at}</span>
                </div>
                <div className="cr-reason">{r.reason}</div>
                <div className="row">
                  <button className="primary" onClick={() =>
                    creditDecide(slug, r.id, 'approve')
                      .then(() => { toast([`approved — ${r.node}'s grant is now ${r.new}`]); refresh?.() })
                      .catch((e) => toast([`error: ${e.message}`]))}>approve</button>
                  <button onClick={() =>
                    creditDecide(slug, r.id, 'deny')
                      .then(() => { toast([`denied ${r.node}'s request`]); refresh?.() })
                      .catch((e) => toast([`error: ${e.message}`]))}>deny</button>
                </div>
              </div>
            ))}
          </>
        )}
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
                  <HearingIcon fontSize="inherit" /> {a.grantee}
                  <button className="chip-x" title="rescind"
                    onClick={() => act('revoke', a.grantee)}><CloseIcon fontSize="inherit" /></button>
                </span>
              ))}
            </div>
          </>
        )}
        <MailFolders folder={folder} setFolder={setFolder}
          unread={box?.pending.length ?? 0} />
        <div className="mailpane">
          {box == null
            ? <div className="dim">loading…</div>
            : folder === 'inbox'
              ? <MailList pending={box.pending} delivered={box.delivered}
                  waitLabel="unread"
                  onRead={(m) => markRead(slug, [m.id])
                    .then(() => { reload(); refresh?.() }).catch(() => {})}
                  sender={(id) => <SenderChip id={id} nodes={nodes} />} />
              : <MailList delivered={box.sent ?? []} outgoing
                  sender={(id) => <SenderChip id={id} nodes={nodes} />} />}
        </div>
        <div className="row">
          {folder === 'inbox' && box?.pending.length > 0 && <button onClick={() =>
            clearInbox(slug).then(reload).catch((e) => toast([`error: ${e.message}`]))}>mark all read</button>}
          <button className="primary" onClick={close}>close</button>
        </div>
      </div>
    </div>
  )
}

function SettingsPanel({ tree, toast, close }) {
  useEsc(close)
  const [maxTop, setMaxTop] = useState(tree.max_top_grant ?? 1000)
  const [defTop, setDefTop] = useState(tree.default_top_grant ?? 50)
  const [compactAt, setCompactAt] = useState(Math.round((tree.compact_at ?? 0.8) * 100))
  const [orgMd, setOrgMd] = useState(null)
  const [fablePolicy, setFablePolicy] = useState(tree.fable_limit_policy ?? 'halt')
  useEffect(() => {
    getOrgMd(tree.slug).then((r) => setOrgMd(r.content)).catch(() => setOrgMd(''))
  }, [tree.slug])
  return (
    <div className="overlay" onClick={close}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> {tree.name} — settings</h3>
        {/* folder access lives on the eye's ⚙ gear panel (user ruling) */}
        <div className="field-label">top-level grant cap</div>
        <input type="number" min="1" step="1" value={maxTop} style={{ width: '8em' }}
          onChange={(e) => setMaxTop(e.target.value)} />
        <div className="field-label">default top-level grant (pre-filled on new hires)</div>
        <input type="number" min="0" step="1" value={defTop} style={{ width: '8em' }}
          onChange={(e) => setDefTop(e.target.value)} />
        <div className="field-label">compaction threshold % (50–95; splits the agent
          when its context passes this)</div>
        <input type="number" min="50" max="95" step="1" value={compactAt}
          style={{ width: '8em' }}
          onChange={(e) => setCompactAt(e.target.value)} />
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
            saveSettings(tree.slug, { clear_fable_lock: true })
              .then((r) => { toast(r.warnings); close() })
              .catch((e) => toast([`error: ${e.message}`]))}>
            <BlockIcon fontSize="inherit" /> clear the fable weekly-limit lock (your decree)</button>
        )}
        <div className="row">
          <button className="primary" onClick={() =>
            Promise.all([
              saveSettings(tree.slug,
                { max_top_grant: +maxTop || undefined,
                  default_top_grant: Number.isFinite(+defTop) ? +defTop : undefined,
                  compact_at: Number.isFinite(+compactAt) ? +compactAt : undefined,
                  fable_limit_policy: fablePolicy }),
              orgMd != null ? putOrgMd(tree.slug, orgMd) : Promise.resolve({}),
            ]).then(([r]) => { toast(r.warnings); close() })
              .catch((e) => toast([`error: ${e.message}`]))}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>
  )
}
