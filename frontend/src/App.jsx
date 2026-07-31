import { useCallback, useEffect, useRef, useState } from 'react'
import {
  audienceAction, BASE, clearInbox, createOrg, creditDecide, deleteOrg,
  getAudiences, getDefaults, getEvents, getHost, getInbox, getOrgMd, getTree,
  killAll, listOrgs, markRead, openWs, putOrgMd, resumeFrozen, runOp,
  saveDefaults, saveKiosk, saveSettings, sendMessage,
} from './api'
import { ConfirmModal, MailFolders, MailList, OrgCanvas, OrgRecord, useEsc } from './Canvas'
import {
  AutorenewIcon, BlockIcon, CheckIcon, ChevronRightIcon, CloseIcon, CopyIcon,
  DeleteIcon, ExpandMoreIcon, GitHubIcon, HearingIcon, HomeIcon, LockIcon,
  LockOpenIcon, MailIcon, MenuIcon, PlayIcon, PublicIcon, SettingsIcon,
  SparkIcon, StopIcon, StorageIcon, WarnIcon,
} from './icons'
import { DirList } from './forms'
import { FolderPickerHost } from './picker'

const TIER_LETTER = { haiku: 'H', sonnet: 'S', opus: 'O', fable: 'F' }
const USER = '@user'       // typed actor sentinels — a node may be NAMED user/system
const SYSTEM = '@system'

const slugFromPath = () => {
  // BASE is the /k/<token> prefix when served from a public kiosk URL
  const m = location.pathname.slice(BASE.length).match(/^\/o\/([a-z0-9@-]+)/)
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
  const [inboxJump, setInboxJump] = useState(null)   // mail id a chat link targets
  const [drawer, setDrawer] = useState(false)
  const [doomedOrg, setDoomedOrg] = useState(null)   // org row pending deletion
  const [showDefaults, setShowDefaults] = useState(false)   // global new-org defaults
  const [killArmed, setKillArmed] = useState(false)  // the killswitch latch
  const [nowTick, setNowTick] = useState(Date.now()) // drives the resume-red clock
  const wsRef = useRef(null)
  useEffect(() => {
    const t = setInterval(() => setNowTick(Date.now()), 15000)
    return () => clearInterval(t)
  }, [])
  useEffect(() => {           // an unlatched killswitch re-latches on its own
    if (!killArmed) return
    const t = setTimeout(() => setKillArmed(false), 6000)
    return () => clearTimeout(t)
  }, [killArmed])

  // №17: a toast may carry an UNDO — a 12-second reverse on the gesture just
  // made (mis-drag reorders, accidental promotes, one-click retires)
  const toast = useCallback((lines, undo = null) => {
    if (!lines || !lines.length) return
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, lines, undo }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 12000)
  }, [])

  const refreshOrgs = useCallback(() => listOrgs().then(setOrgs).catch((e) => setError(e.message)), [])
  const refreshTree = useCallback((s) => {
    if (!s) return
    getTree(s).then(setTree).catch((e) => setError(e.message))
  }, [])

  useEffect(() => { refreshOrgs() }, [refreshOrgs])
  useEffect(() => {          // the org list/dashboard is LIVE while visible —
    // kiosk spend/storage/caps move under it (agent turns, admin edits)
    if (slug && !drawer) return
    const t = setInterval(refreshOrgs, 3000)
    return () => clearInterval(t)
  }, [slug, drawer, refreshOrgs])
  useEffect(() => {          // kiosk: the single org IS the app — PUBLIC
    // builds only (BASE = /k/<token>). On the admin side orgs[0] can be a
    // kiosk org too (list_orgs carries the flag now), and a kiosk sorting
    // first hijacked the whole welcome screen into it
    if (BASE && !slug && orgs.length) setSlug(orgs[0].slug)
  }, [orgs, slug])

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
    const want = BASE + (slug ? `/o/${slug}` : '/')
    if (location.pathname !== want) history.pushState(null, '', want)
  }, [slug])
  useEffect(() => {                    // №38: the tab title carries the unread
    const n = (tree?.user_inbox_count ?? 0) + (tree?.org_inbox?.unread ?? 0)
    document.title = (n > 0 ? `(${n}) ` : '')
      + (tree?.name ? `${tree.name} — orgtree` : 'orgtree')
  }, [tree])

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
        setStreamEvt({ node: data.node, kind: data.kind, text: data.text,
          // sticky rides through: immediate-command output lives in NO
          // transcript, so the live-feed reconciliation must never sweep it
          ...(data.sticky ? { sticky: true } : {}), t: Date.now() })
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
        if (data.event === 'spend_frozen') {
          toast(['SPEND LIMIT REACHED — every agent is frozen; raise the limit in the kiosk dashboard to resume'])
          refreshTree(slug)
        }
        if (data.event === 'storage_blocked') {
          toast(['WORKSPACE STORAGE LIMIT reached — file writes are blocked until enough files are deleted (agents keep running)'])
          refreshTree(slug)
        }
        if (data.event === 'storage_cleared') {
          toast(['workspace back under its storage limit — writes unblocked'])
          refreshTree(slug)
        }
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
      .then((r) => {
        if (r?.bridge?.raise_ceiling) {
          // the one-action bridge (ceiling spec §1): the same op, re-sent
          // with the flag — auto_raise OFF never means "go navigate"
          toast(r.warnings?.length ? r.warnings
            : ['clamped to the kiosk permission ceiling'],
          { label: 'raise ceiling & apply',
            fn: () => runOp(slug, { ...body, raise_ceiling: true })
              .then((r2) => { toast(r2.warnings); refreshTree(slug); refreshOrgs() })
              .catch((e) => toast([`error: ${e.message}`])) })
        } else toast(r.warnings)
        refreshTree(slug); refreshOrgs(); return r
      })
      .catch((e) => { toast([`error: ${e.message}`]); throw e }),
    [slug, toast, refreshTree, refreshOrgs])

  const pick = (s) => { setSlug(s); setShowSettings(false); setDrawer(false) }
  const goHome = () => { setSlug(null); setDrawer(false) }

  const orgPanel = (
    <>
      <h1><SparkIcon fontSize="inherit" /> orgtree
        <a className="gh-link h1-gh" href="https://github.com/Maurdekye/claude-orgtree"
          target="_blank" rel="noreferrer" title="orgtree on GitHub">
          <GitHubIcon fontSize="inherit" /></a></h1>
      {slug && <button className="home" onClick={goHome}><HomeIcon fontSize="inherit" /> all organizations</button>}
      <nav>
        {orgs.map((o) => (
          <div key={o.slug} role="button" tabIndex={0}
            className={'org' + (o.slug === slug ? ' current' : '')
              + (o.kiosk_cfg || o.kiosk ? ' kiosk-org' : '')}
            onClick={() => pick(o.slug)}
            onKeyDown={(e) => { if (e.key === 'Enter') pick(o.slug) }}>
            <span>{o.name}</span>
            {(o.kiosk_cfg || o.kiosk) &&
              <span className="kiosk-badge" title="kiosk org"><PublicIcon fontSize="inherit" /></span>}
            <span className="spacer" />
            <span className="dim">{o.live}/{o.nodes} live</span>
            {/* kiosk orgs delete like any other (user report 2026-07-31: the
                old !o.kiosk gate left NO UI path at all — the server already
                refuses public deletes, so hiding the trash from the admin
                protected nothing) */}
            <button className="org-del"
              onClick={(e) => { e.stopPropagation(); setDoomedOrg(o) }}><DeleteIcon fontSize="inherit" /></button>
          </div>
        ))}
        {!orgs.length && <div className="dim pad">no organizations yet</div>}
      </nav>
      {!BASE && <NewOrg onCreate={(name, dirs, kiosk, sandbox) =>
        createOrg(name, dirs, kiosk, sandbox).then((r) => { refreshOrgs(); pick(r.slug) })
          .catch((e) => toast([`error: ${e.message}`]))} />}
      {/* global default org settings (user spec): every NEW org is born with
          these — admin only */}
      {!BASE && <button className="home" onClick={() => setShowDefaults(true)}>
        <SettingsIcon fontSize="inherit" /> default org settings</button>}
      {/* kiosk dashboard: admin only — a public visitor never sees this panel
          (and the server refuses the endpoints regardless) */}
      {!BASE && <KioskDash orgs={orgs} refresh={refreshOrgs} toast={toast} pick={pick} />}
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
                {!tree.public &&
                  <button className="iconbtn" onClick={() => setDrawer(true)}><MenuIcon fontSize="inherit" /></button>}
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
                {tree.kiosk?.spend_limit && (
                  tree.spend_frozen
                    ? <span className="chip bad"><BlockIcon fontSize="inherit" /> spend limit reached — agents frozen</span>
                    : <span className={'chip' + (tree.cost_usd_total >= tree.kiosk.spend_limit * 0.9 ? ' bad' : '')}>
                        spend ${tree.cost_usd_total.toFixed(2)} / ${tree.kiosk.spend_limit.toFixed(2)}
                      </span>
                )}
                {tree.kiosk?.storage_limit_mb && (
                  tree.kiosk.storage_blocked
                    ? <span className="chip bad" title="over the workspace storage limit — delete files to unblock">
                        <StorageIcon fontSize="inherit" /> {tree.kiosk.storage_mb ?? '?'} / {tree.kiosk.storage_limit_mb} MB — writes blocked
                      </span>
                    : <span className={'chip' + ((tree.kiosk.storage_mb ?? 0) >= tree.kiosk.storage_limit_mb * 0.9 ? ' bad' : '')}
                        title="workspace storage used / limit">
                        <StorageIcon fontSize="inherit" /> {tree.kiosk.storage_mb ?? 0} / {tree.kiosk.storage_limit_mb} MB
                      </span>
                )}
                {(() => {   // usage-limit freeze: ▶ restarts every frozen agent
                  if (tree.spend_frozen) return null
                  const frozen = [...flatNodes(tree).values()].filter((n) => n.frozen)
                  if (!frozen.length) return null
                  const until = frozen.map((n) => n.frozen.until).find(Boolean)
                  // RED while the reported reset time is still ahead (resuming
                  // would just re-hit the limit); normal once it has passed
                  const untilTs = Math.max(0, ...frozen.map((n) => n.frozen.until_ts || 0))
                  const notYet = untilTs > 0 && nowTick < untilTs * 1000
                  return (
                    <>
                      <button className={'resume-all' + (notYet ? ' notyet' : '')}
                        title={frozen.map((n) => n.id).join(', ')
                          + (notYet ? ' — the limit has not reset yet' : '')}
                        onClick={() => resumeFrozen(slug)
                          .then((r) => { toast([`resumed ${r.resumed.length} agent(s)`]); refreshTree(slug) })
                          .catch((e) => toast([`error: ${e.message}`]))}>
                        <PlayIcon fontSize="inherit" /> resume {frozen.length}
                      </button>
                      <span className="resume-note">
                        usage limit hit — {frozen.length} agent{frozen.length > 1 ? 's' : ''} frozen
                        {until ? ` · resumable ${until}` : ''}
                      </span>
                      {!tree.public &&
                        <button className={'auto-resume' + (tree.auto_resume ? ' on' : '')}
                          title="auto-resume all frozen agents one minute after the reported reset time"
                          onClick={() => saveSettings(slug, { auto_resume: !tree.auto_resume })
                            .then(() => refreshTree(slug))
                            .catch((e) => toast([`error: ${e.message}`]))}>
                          <AutorenewIcon fontSize="inherit" /> auto{tree.auto_resume ? ' on' : ''}
                        </button>}
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
                {!tree.public &&
                  <button onClick={() => setShowSettings(true)}><SettingsIcon fontSize="inherit" /> settings</button>}
                <a className="gh-link" href="https://github.com/Maurdekye/claude-orgtree"
                  target="_blank" rel="noreferrer" title="orgtree on GitHub">
                  <GitHubIcon fontSize="inherit" /></a>
              </header>
              <OrgCanvas tree={tree} op={op} slug={slug} pulse={pulse} toast={toast}
                streamEvt={streamEvt} activity={activity} mailEvt={mailEvt}
                onInbox={(jump) => {
                  setInboxJump(typeof jump === 'string' ? jump : null)
                  setShowInbox(true)
                }} />
              {showSettings && (
                <SettingsPanel tree={tree} toast={toast}
                  close={() => { setShowSettings(false); refreshTree(slug) }} />
              )}
              {showInbox && (
                <InboxPanel slug={slug} tree={tree} toast={toast}
                  refresh={() => refreshTree(slug)} jumpTo={inboxJump}
                  close={() => {
                    setShowInbox(false); setInboxJump(null); refreshTree(slug)
                  }} />
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

      {showDefaults && (
        <DefaultsPanel toast={toast} close={() => setShowDefaults(false)} />
      )}
      {doomedOrg && (
        <ConfirmModal title={`permanently delete ${doomedOrg.name}?`}
          body={`Erases the organization and its ${doomedOrg.nodes} node(s) — ledger, mail, lineage, audiences.${
            doomedOrg.kiosk_cfg || doomedOrg.kiosk
              ? ' The public kiosk link dies with it, and its sandbox container is removed.'
              : ''} Workspace and scratch folders remain on disk. This cannot be undone.`}
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
            {t.undo && (
              <button className="toast-undo" onClick={(e) => {
                e.stopPropagation()
                setToasts((x) => x.filter((y) => y.id !== t.id))
                ;(typeof t.undo === 'function' ? t.undo : t.undo.fn)()
              }}>{typeof t.undo === 'function' ? 'undo' : t.undo.label}</button>
            )}
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
  // kiosk is a CREATION-TIME type (user ruling): a checkbox here reveals its
  // limit fields; auth is never configurable — sandboxes use the proxied
  // subscription (the host holds the token; the sandbox never sees it)
  const [kiosk, setKiosk] = useState(false)
  // kiosk cap defaults (user ruling 2026-07-31): 30 credits · $50 · 1 GB
  const [credits, setCredits] = useState(30)
  const [spend, setSpend] = useState(50)
  const [storage, setStorage] = useState(1024)
  // the permission ceiling is visible AT CREATION (ceiling spec §3): the
  // default is permissive (mcp "*", user ruling), so narrowing must be a
  // conscious act here rather than something discovered later
  const [ceil, setCeil] = useState({ bash: true, web: true, edit: true,
                                     subagents: true, mcp: true })
  const [ceilPm, setCeilPm] = useState('acceptEdits')
  const [ceilTier, setCeilTier] = useState('')   // '' = no tier cap
  const [autoRaise, setAutoRaise] = useState(false)
  // sandbox is OFF by default (user ruling) — and impossible without Docker
  const [sandboxed, setSandboxed] = useState(false)
  const [docker, setDocker] = useState(false)
  useEffect(() => {
    getHost().then((h) => setDocker(!!h.docker)).catch(() => {})
  }, [])
  const reset = () => {
    setOpen(false); setAdvanced(false); setName(''); setDirs([]); setKiosk(false)
  }
  if (!open) return <button className="primary" onClick={() => setOpen(true)}>+ new organization</button>
  return (
    <form className="stack" onSubmit={(e) => {
      e.preventDefault()
      onCreate(name, dirs.map((s) => s.trim()).filter(Boolean),
        kiosk ? {
          credits: +credits || 0, spend_limit: +spend || 0,
          storage_limit_mb: +storage || 0, sandbox: sandboxed,
          auto_raise: autoRaise,
          max_scope: {
            tools: { bash: ceil.bash, web: ceil.web, edit: ceil.edit,
                     subagents: ceil.subagents, mcp: ceil.mcp ? ['*'] : [] },
            org_visibility: 'full', permission_mode: ceilPm,
            max_tier: ceilTier || null,
          },
        } : null,
        sandboxed)
      reset()
    }}>
      <input autoFocus placeholder="organization name" value={name}
        onChange={(e) => setName(e.target.value)} required />
      <label className="row kiosk-sbx">
        <input type="checkbox" checked={kiosk}
          onChange={(e) => {
            setKiosk(e.target.checked)
            // kiosks default the sandbox ON — but only where Docker exists
            if (e.target.checked && docker) setSandboxed(true)
          }} />
        kiosk — publicly shareable via a secret URL, with hard limits
      </label>
      {kiosk && (
        <div className="kiosk-caps">
          <label>credits <input type="number" min="0" value={credits}
            onChange={(e) => setCredits(e.target.value)} /></label>
          <label>spend $ <input type="number" min="0" step="0.5" value={spend}
            onChange={(e) => setSpend(e.target.value)} /></label>
          <label>storage MB <input type="number" min="0" value={storage}
            onChange={(e) => setStorage(e.target.value)} /></label>
        </div>
      )}
      {kiosk && (
        <div className="kiosk-ceil">
          <div className="field-label"
            title="the MAXIMUM grantable to any agent in this kiosk — visitors retool freely within it; folders bound to the org's own">
            permission ceiling</div>
          <div className="ceil-tools">
            {['bash', 'web', 'edit', 'subagents', 'mcp'].map((k) => (
              <label key={k} className="row">
                <input type="checkbox" checked={ceil[k]}
                  onChange={(e) => setCeil((c) => ({ ...c, [k]: e.target.checked }))} />
                {k === 'mcp' ? 'MCP servers' : k}
              </label>
            ))}
            <label className="row">mode <select value={ceilPm}
              onChange={(e) => setCeilPm(e.target.value)}>
              <option value="default">default (asks)</option>
              <option value="acceptEdits">acceptEdits</option>
              <option value="bypassPermissions">bypassPermissions</option>
            </select></label>
            <label className="row"
              title="the highest model tier this kiosk may run — spawn tokens above it disappear and agents cannot hire, rehire or switch above it">
              tier ≤ <select value={ceilTier}
                onChange={(e) => setCeilTier(e.target.value)}>
                <option value="">fable (no cap)</option>
                <option value="opus">opus</option>
                <option value="sonnet">sonnet</option>
                <option value="haiku">haiku</option>
              </select></label>
          </div>
          <label className="row" title="an over-ceiling grant made by YOU (admin) raises the ceiling to fit instead of clamping — off so nothing lifts it without meaning to; visitors always clamp">
            <input type="checkbox" checked={autoRaise}
              onChange={(e) => setAutoRaise(e.target.checked)} />
            auto-raise on my own over-ceiling grants
          </label>
        </div>
      )}
      {/* any org may sandbox (user ruling) — OFF by default; the checkbox is
          disabled entirely when Docker isn't installed */}
      <label className={'row kiosk-sbx' + (docker ? '' : ' dim')}
        title={docker ? undefined : 'Docker is not installed — sandboxing unavailable'}>
        <input type="checkbox" checked={sandboxed && docker} disabled={!docker}
          onChange={(e) => setSandboxed(e.target.checked)} />
        sandboxed — agents run in a Docker container, isolated from this PC
        {!docker && <span className="dim"> (requires Docker)</span>}
      </label>
      {kiosk && !sandboxed && (
        <div className="dim kiosk-warn"><WarnIcon fontSize="inherit" /> without
          a sandbox the storage limit is enforced loosely — usage is checked
          only between turns, so a single turn can overshoot it</div>
      )}
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

// kiosk dashboard (user vision): every kiosk session at a glance — spend,
// credits held, storage — with inline cap edits, the preauthenticated share
// URL (copy + rotate), and enable/disable. Loopback-only by construction.
// kiosk orgs are a DISTINCT TYPE (user ruling): born as kiosks with their
// limits defined at creation — never converted from (or back to) a normal
// org. The URL can be paused; the limits always bind.
function KioskDash({ orgs, refresh, toast, pick }) {
  const kiosks = orgs.filter((o) => o.kiosk_cfg)
  if (!kiosks.length) return null
  return (
    <div className="kiosk-dash">
      <h3><PublicIcon fontSize="inherit" /> public kiosks</h3>
      {kiosks.map((o) => (
        <KioskRow key={o.slug + ':' + o.kiosk_cfg.token + ':' + o.kiosk_cfg.credits
          + ':' + o.kiosk_cfg.spend_limit + ':' + o.kiosk_cfg.storage_limit_mb
          + ':' + o.kiosk_cfg.enabled}
          org={o} refresh={refresh} toast={toast} pick={pick} />
      ))}
    </div>
  )
}

function KioskRow({ org, refresh, toast, pick }) {
  const k = org.kiosk_cfg
  const [credits, setCredits] = useState(k.credits)
  const [spend, setSpend] = useState(k.spend_limit)
  const [storage, setStorage] = useState(k.storage_limit_mb)
  const save = (patch) => saveKiosk(org.slug, patch)
    .then((r) => {
      if (r.freezes_cleared?.length) toast([`limit raised — cleared: ${r.freezes_cleared.join(', ')}`])
      refresh()
    })
    .catch((e) => toast([`error: ${e.message}`]))
  const dirty = +credits !== k.credits || +spend !== k.spend_limit || +storage !== k.storage_limit_mb
  return (
    <div className="kiosk-row">
      <div className="row kiosk-head">
        <b className="kiosk-name" role="button" tabIndex={0} title="open this org (full admin rights)"
          onClick={() => pick(org.slug)}
          onKeyDown={(e) => { if (e.key === 'Enter') pick(org.slug) }}>{org.name}</b>
        {k.sandbox && <span className="chip" title="agent turns run in a Docker container">sandboxed</span>}
        {k.spend_frozen && <span className="chip bad">spend frozen</span>}
        {k.storage_blocked && <span className="chip bad">writes blocked</span>}
        {!k.enabled && <span className="chip">URL paused</span>}
        <span className="spacer" />
        {/* pause/unpause the PUBLIC URL — the org stays a kiosk (its limits
            always bind); only the token gateway is gated */}
        <button title={k.enabled ? 'pause the public URL' : 'reactivate the public URL'}
          onClick={() => save({ enabled: !k.enabled })}>
          {k.enabled ? <BlockIcon fontSize="inherit" /> : <PlayIcon fontSize="inherit" />}</button>
      </div>
      <div className="dim kiosk-stats">
        ${(org.cost_usd_total ?? 0).toFixed(2)}{k.spend_limit ? ` / $${k.spend_limit.toFixed(2)}` : ''} spent
        {' · '}{k.held}{k.credits ? ` / ${k.credits}` : ''} credits held
        {' · '}{k.storage_mb ?? 0}{k.storage_limit_mb ? ` / ${k.storage_limit_mb}` : ''} MB workspace
      </div>
      <div className="kiosk-caps">
        <label>credits <input type="number" min="0" value={credits}
          onChange={(e) => setCredits(e.target.value)} /></label>
        <label>spend $ <input type="number" min="0" step="0.5" value={spend}
          onChange={(e) => setSpend(e.target.value)} /></label>
        <label>storage MB <input type="number" min="0" value={storage}
          onChange={(e) => setStorage(e.target.value)} /></label>
        {dirty && <button className="primary" title="apply the new caps"
          onClick={() => save({ credits: +credits || 0, spend_limit: +spend || 0,
            storage_limit_mb: +storage || 0 })}><CheckIcon fontSize="inherit" /></button>}
      </div>
      <div className="row kiosk-url">
        <input readOnly value={k.share_url ?? '(set ORGTREE_PUBLIC_PORT to serve public URLs)'}
          onFocus={(e) => e.target.select()} />
        <button title="copy the share URL" disabled={!k.share_url}
          onClick={() => navigator.clipboard.writeText(k.share_url)
            .then(() => toast(['share URL copied']))}><CopyIcon fontSize="inherit" /></button>
        <button title="rotate the secret (the old URL stops working immediately)"
          onClick={() => save({ rotate_token: true })}><AutorenewIcon fontSize="inherit" /></button>
      </div>
    </div>
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

function InboxPanel({ slug, tree, toast, refresh, close, jumpTo }) {
  useEsc(close)
  const [box, setBox] = useState(null)
  const [aud, setAud] = useState(null)
  const [folder, setFolder] = useState('inbox')
  const [events, setEvents] = useState(null)
  const nodes = flatNodes(tree)
  const reload = useCallback(() => {
    getInbox(slug).then(setBox).catch((e) => toast([`error: ${e.message}`]))
    getAudiences(slug).then(setAud).catch(() => {})
  }, [slug, toast])
  useEffect(() => { reload() }, [reload])
  useEffect(() => {          // №10: the record loads on demand
    if (folder === 'record') {
      getEvents(slug).then((r) => setEvents(r.events)).catch(() => setEvents([]))
    }
  }, [folder, slug])
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
          folders={['inbox', 'sent', 'record']}
          unread={box?.pending.length ?? 0} />
        <div className="mailpane">
          {folder === 'record'
            ? <OrgRecord events={events} />
            : box == null
            ? <div className="dim">loading…</div>
            : folder === 'inbox'
              ? <MailList pending={box.pending} delivered={box.delivered}
                  waitLabel="unread" jumpTo={jumpTo}
                  onRead={(m) => markRead(slug, [m.id])
                    .then(() => { reload(); refresh?.() }).catch(() => {})}
                  onReply={(m, text) => sendMessage(slug, m.from, text)
                    .then(() => toast([`sent to ${m.from}`]))
                    .catch((e) => toast([`error: ${e.message}`]))}
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

// Global DEFAULT org settings (user spec, root page): every newly created
// org is born with these values — the same knobs as a single org's settings
// panel, saved once in <data>/defaults.json.
function DefaultsPanel({ toast, close }) {
  useEsc(close)
  const [d, setD] = useState(null)
  useEffect(() => { getDefaults().then(setD).catch(() => setD({})) }, [])
  if (d == null) {
    return (
      <div className="overlay" onClick={close}>
        <div className="settings"><div className="dim pad">loading…</div></div>
      </div>
    )
  }
  const set = (k, v) => setD({ ...d, [k]: v })
  return (
    <div className="overlay" onClick={close}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> default org settings
          <span className="dim"> · applied to every NEW organization</span></h3>
        <div className="field-label">top-level grant cap</div>
        <input type="number" min="1" step="1" style={{ width: '8em' }}
          value={d.max_top_grant ?? 1000}
          onChange={(e) => set('max_top_grant', +e.target.value)} />
        <div className="field-label">default top-level grant (pre-filled on new hires)</div>
        <input type="number" min="0" step="1" style={{ width: '8em' }}
          value={d.default_top_grant ?? 50}
          onChange={(e) => set('default_top_grant', +e.target.value)} />
        <div className="field-label">compaction threshold % (50–95)</div>
        <input type="number" min="50" max="95" step="1" style={{ width: '8em' }}
          value={Math.round((d.compact_at ?? 0.8) * 100)}
          onChange={(e) => set('compact_at', (+e.target.value || 80) / 100)} />
        <div className="field-label">fable weekly-limit policy</div>
        <select value={d.fable_limit_policy ?? 'halt'}
          onChange={(e) => set('fable_limit_policy', e.target.value)}>
          <option value="halt">halt (default)</option>
          <option value="opus">switch to opus</option>
          <option value="dissolve">dissolve subtree</option>
        </select>
        <div className="field-label">fable content-filter policy</div>
        <select value={d.fable_filter_policy ?? 'halt'}
          onChange={(e) => set('fable_filter_policy', e.target.value)}>
          <option value="halt">halt (default)</option>
          <option value="opus">switch to opus + retry</option>
        </select>
        <div className="field-label">credit cost bubbling</div>
        <label className="checkline">
          <input type="checkbox" checked={d.cascade_hire !== false}
            onChange={(e) => set('cascade_hire', e.target.checked)} />
          hires bubble their cost up the chain
        </label>
        <label className="checkline">
          <input type="checkbox" checked={d.cascade_alloc !== false}
            onChange={(e) => set('cascade_alloc', e.target.checked)} />
          allocations &amp; model upgrades bubble their cost up the chain
        </label>
        <label className="checkline">
          <input type="checkbox" checked={!!d.auto_resume}
            onChange={(e) => set('auto_resume', e.target.checked)} />
          auto-resume usage-limit-frozen agents after the reset time
        </label>
        <div className="hint">
          Existing organizations keep their own settings — these apply only at
          creation.
        </div>
        <div className="row">
          <button className="primary" onClick={() =>
            saveDefaults({
              max_top_grant: d.max_top_grant,
              default_top_grant: d.default_top_grant,
              compact_at: Math.round((d.compact_at ?? 0.8) * 100),
              fable_limit_policy: d.fable_limit_policy,
              fable_filter_policy: d.fable_filter_policy,
              cascade_hire: d.cascade_hire !== false,
              cascade_alloc: d.cascade_alloc !== false,
              auto_resume: !!d.auto_resume,
            }).then(() => { toast(['default org settings saved']); close() })
              .catch((e) => toast([`error: ${e.message}`]))}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>
  )
}

// mode-aware folder rows for the kiosk ceiling (DirList is string-only)
function CeilDirs({ dirs, onChange }) {
  return (
    <div className="dirlist">
      {dirs.map((d, i) => (
        <div className="dirrow" key={i}>
          <input placeholder="E:\path\to\folder" value={d.path}
            onChange={(e) => onChange(dirs.map((x, j) =>
              (j === i ? { ...x, path: e.target.value } : x)))} />
          <select value={d.mode} onChange={(e) => onChange(dirs.map((x, j) =>
            (j === i ? { ...x, mode: e.target.value } : x)))}>
            <option value="rw">rw</option><option value="ro">ro</option>
          </select>
          <button type="button" className="iconbtn" title="remove"
            onClick={() => onChange(dirs.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <div className="dirrow">
        <button type="button" className="addrow"
          onClick={() => onChange([...dirs, { path: '', mode: 'rw' }])}>+ add folder</button>
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
  const [filterPolicy, setFilterPolicy] = useState(tree.fable_filter_policy ?? 'halt')
  const [cascadeHire, setCascadeHire] = useState(tree.cascade_hire !== false)
  const [cascadeAlloc, setCascadeAlloc] = useState(tree.cascade_alloc !== false)
  // kiosk permission ceiling (consensus spec): admin payload only — the
  // public tree never carries max_scope
  const ms = tree.kiosk?.max_scope
  const [ceil, setCeil] = useState(() => (ms ? {
    bash: !!ms.tools?.bash, web: !!ms.tools?.web, edit: !!ms.tools?.edit,
    subagents: !!ms.tools?.subagents } : null))
  const [ceilMcp, setCeilMcp] = useState(() => (ms?.tools?.mcp ?? []).join(', '))
  const [ceilDirs, setCeilDirs] = useState(() => ms?.add_dirs ?? [])
  const [ceilVis, setCeilVis] = useState(ms?.org_visibility ?? 'full')
  const [ceilPm, setCeilPm] = useState(ms?.permission_mode ?? 'acceptEdits')
  const [ceilTier, setCeilTier] = useState(ms?.max_tier ?? '')
  const [autoRaise, setAutoRaise] = useState(!!tree.kiosk?.auto_raise)
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
        <div className="field-label">fable content-filter policy (a flagged message
          halts the turn, or converts the agent to opus and retries)</div>
        <select value={filterPolicy} onChange={(e) => setFilterPolicy(e.target.value)}>
          <option value="halt">halt (default)</option>
          <option value="opus">switch to opus + retry</option>
        </select>
        {/* §4.6 cost-bubbling toggles (user spec, both ON by default) */}
        <div className="field-label">credit cost bubbling</div>
        <label className="checkline">
          <input type="checkbox" checked={cascadeHire}
            onChange={(e) => setCascadeHire(e.target.checked)} />
          hires bubble their cost up the chain (off: the hiring agent's superior
          must hold the free credits itself)
        </label>
        <label className="checkline">
          <input type="checkbox" checked={cascadeAlloc}
            onChange={(e) => setCascadeAlloc(e.target.checked)} />
          allocations &amp; model upgrades bubble their cost up the chain (off:
          limited to the superior's own free credits)
        </label>
        {ms && ceil && (
          <>
            <div className="field-label"
              title="visitors and agents retool freely WITHIN it (clamped, never refused); lowering it sweeps every agent's grants to fit">
              kiosk permission ceiling — the maximum grantable to any agent</div>
            <div className="ceil-tools">
              {['bash', 'web', 'edit', 'subagents'].map((k) => (
                <label key={k} className="checkline">
                  <input type="checkbox" checked={ceil[k]}
                    onChange={(e) => setCeil((c) => ({ ...c, [k]: e.target.checked }))} />
                  {k}
                </label>
              ))}
            </div>
            <div className="field-label">MCP servers ("*" = all, empty = none,
              or a comma-separated list)</div>
            <input value={ceilMcp} placeholder="*"
              onChange={(e) => setCeilMcp(e.target.value)} />
            <div className="field-label">folder bounds (grants clamp into these)</div>
            <CeilDirs dirs={ceilDirs} onChange={setCeilDirs} />
            <div className="row">
              <label className="checkline">org visibility ≤ <select value={ceilVis}
                onChange={(e) => setCeilVis(e.target.value)}>
                {['self', 'team', 'subtree', 'full'].map((v) =>
                  <option key={v} value={v}>{v}</option>)}
              </select></label>
              <label className="checkline">mode ≤ <select value={ceilPm}
                onChange={(e) => setCeilPm(e.target.value)}>
                <option value="default">default</option>
                <option value="acceptEdits">acceptEdits</option>
                <option value="bypassPermissions">bypassPermissions</option>
              </select></label>
              <label className="checkline"
                title="the highest model tier this kiosk may run — spawn tokens above it disappear; hires, rehires and switches above it are refused (existing over-cap agents stay until you switch or retire them)">
                tier ≤ <select value={ceilTier}
                  onChange={(e) => setCeilTier(e.target.value)}>
                  <option value="">fable (no cap)</option>
                  <option value="opus">opus</option>
                  <option value="sonnet">sonnet</option>
                  <option value="haiku">haiku</option>
                </select></label>
            </div>
            <label className="checkline"
              title="an over-ceiling grant made by YOU raises the ceiling to fit (logged, named) instead of clamping; visitors always clamp">
              <input type="checkbox" checked={autoRaise}
                onChange={(e) => setAutoRaise(e.target.checked)} />
              auto-raise the ceiling on my own over-ceiling grants
            </label>
            <button onClick={() =>
              saveKiosk(tree.slug, {
                auto_raise: autoRaise,
                max_scope: {
                  tools: { ...ceil,
                           mcp: ceilMcp.split(',').map((s) => s.trim())
                             .filter(Boolean) },
                  add_dirs: ceilDirs.filter((d) => d.path.trim()),
                  org_visibility: ceilVis, permission_mode: ceilPm,
                  max_tier: ceilTier || null,
                } })
                .then((r) => toast(r.warnings?.length ? r.warnings
                  : ['ceiling saved — nothing needed sweeping']))
                .catch((e) => toast([`error: ${e.message}`]))}>
              apply ceiling{ceilMcp.trim() === '' ? ' (MCP: none)' : ''}</button>
          </>
        )}
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
                  fable_limit_policy: fablePolicy,
                  fable_filter_policy: filterPolicy,
                  cascade_hire: cascadeHire,
                  cascade_alloc: cascadeAlloc }),
              orgMd != null ? putOrgMd(tree.slug, orgMd) : Promise.resolve({}),
            ]).then(([r]) => { toast(r.warnings); close() })
              .catch((e) => toast([`error: ${e.message}`]))}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>
  )
}
