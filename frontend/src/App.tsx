import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  audienceAction, BASE, clearInbox, createOrg, deleteOrg,
  getAudiences, getDefaults, getEvents, getHost, getInbox, getOrgMd,
  getSweepPreview, getTree, killAll, listOrgs, markRead, openWs, putOrgMd,
  resumeFrozen, runOp, saveDefaults, saveKiosk, saveSettings, sendMessage,
  sweepLegacy,
} from './api'
import { ConfirmModal, MailFolders, MailList, OrgCanvas, OrgRecord, useEsc } from './Canvas'
import { DiskBrowser, DiskFullAlert } from './DiskBrowser'
import {
  AutorenewIcon, BlockIcon, CheckIcon, ChevronRightIcon, CloseIcon, CopyIcon,
  DeleteIcon, ExpandMoreIcon, GitHubIcon, HearingIcon, HomeIcon, LockIcon,
  LockOpenIcon, MailIcon, MenuIcon, PlayIcon, PublicIcon, SettingsIcon,
  SparkIcon, StopIcon, StorageIcon, WarnIcon,
} from './icons'
import { DirList } from './forms'
import { FolderPickerHost } from './picker'
import { deskDpi, orgPxc, setDeskDpi, usePolled, TIERS } from './canvas/shared'
import { AskCard } from './canvas/asks'
import { addPending, dropPending, ingestPulse, ingestStream, resetConvos } from './convo'
import type {
  AskInfo, AudiencesPayload, DefaultsPayload, InboxPayload, KioskSpecRequest,
  MailEntry, OpRequest, OrgEvent, OrgListEntry, SweepPreview, ToastFn,
  ToastUndo, TreeFrozen, TreeNode, TreePayload,
} from './types'
import type { MailRow } from './canvas/shared'

const TIER_LETTER: Record<string, string> = { haiku: 'H', sonnet: 'S', opus: 'O', fable: 'F' }
const USER = '@user'       // typed actor sentinels — a node may be NAMED user/system
const SYSTEM = '@system'

// the WS broadcast shapes the handler actually reads (any other event type
// only triggers the tree refetch) — cast once at the JSON.parse boundary
type WsEvent =
  | { type: 'mail'; from: string; to: string }
  | { type: 'node_stream'; node: string; kind: string; text?: string; sticky?: boolean; id?: string }
  | { type: 'node_event'; node: string; event: string }

// live-feed state threaded into OrgCanvas (boundary shapes — Canvas declares
// its own; reconcile if they drift)
// text is required on the OUT side: the backend sends it on every stream()
// emit (supervisor stream plumbing) — the `?? ''` at the construction site
// is the wire-boundary guard, not a real case
interface MailEvt { from: string; to: string; t: number }
interface Toast { id: number; lines: string[]; undo: ToastUndo | null }

/** G1: the tree is pulled on a timer as well as pushed. Slow enough to be
 *  invisible in cost (a ~4 KB payload every 6 s), fast enough that a missed
 *  push is a blink rather than a wedge. */
const TREE_POLL_MS = 6000

const slugFromPath = () => {
  // BASE is the /k/<token> prefix when served from a public kiosk URL
  const m = location.pathname.slice(BASE.length).match(/^\/o\/([a-z0-9@-]+)/)
  return m ? m[1]! : null // nUIA: group 1 is unconditional in the regex
}

export default function App() {
  // apply the stored desk text size before anything renders a desk
  useEffect(() => { setDeskDpi(deskDpi()) }, [])
  const [orgs, setOrgs] = useState<OrgListEntry[]>([])
  const [slug, setSlug] = useState<string | null>(slugFromPath)   // /o/<slug> survives refresh
  const [tree, setTree] = useState<TreePayload | null>(null)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [error, setError] = useState<string | null>(null)
  // G4: `pulses` used to live here — a per-node record of the last turn event,
  // threaded App → OrgCanvas → EyeDesk/NodeSquare → DeskChat. Every consumer
  // of it is gone: the conversation refetches through convo.ts, and the node
  // inbox (its last real reader) polls itself now. DeskChat still destructured
  // it and its memo still compared it, but nothing read it — the same dead
  // prop chain `streams` was, and dead update paths are what make staleness
  // hard to see. ingestPulse still runs below; only the mirror is gone.
  const [mailEvt, setMailEvt] = useState<MailEvt | null>(null)
  // G4: `activity` used to live here — a Record<node, {phase,tool}> accumulated
  // from websocket frames and cleared on turn_done, i.e. a client-side copy of
  // something the supervisor already knows. A missed turn_done stranded an
  // indicator until the socket reconnected. It is a tree-payload field now
  // (api.py annotate(), derived from the live tail), so it self-heals on the
  // same heartbeat as everything else and no event can be missed.
  const [showSettings, setShowSettings] = useState(false)
  // the recovery browser: 'largest' = forced triage mode (the alert's path);
  // 'last' = whatever mode was used last (the header chip's path)
  const [showDisk, setShowDisk] = useState<false | 'last' | 'largest'>(false)
  const [showInbox, setShowInbox] = useState(false)
  const [inboxJump, setInboxJump] = useState<string | null>(null)   // mail id a chat link targets
  const [drawer, setDrawer] = useState(false)
  const [doomedOrg, setDoomedOrg] = useState<OrgListEntry | null>(null)   // org row pending deletion
  const [showDefaults, setShowDefaults] = useState(false)   // global new-org defaults
  const [killArmed, setKillArmed] = useState(false)  // the killswitch latch
  const [nowTick, setNowTick] = useState(Date.now()) // drives the resume-red clock
  const wsRef = useRef<WebSocket | null>(null)
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
  const toast = useCallback((lines?: string[] | null, undo: ToastUndo | null = null) => {
    if (!lines || !lines.length) return
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, lines, undo }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 12000)
  }, [])

  const refreshOrgs = useCallback(() => listOrgs().then(setOrgs).catch((e: Error) => setError(e.message)), [])
  const refreshTree = useCallback((s: string | null) => {
    if (!s) return
    getTree(s).then(setTree).catch((e: Error) => setError(e.message))
  }, [])

  useEffect(() => { refreshOrgs() }, [refreshOrgs])
  // G1 — THE TREE HEARTBEAT. Everything on screen that is not the conversation
  // — every card, credit meter, occupancy bar, roster row, resume timer and
  // inbox badge — is rendered from this one payload, and until now it was
  // PUSH-ONLY: refetched on a websocket frame or in the acting client's own
  // callback, never on a timer. So any fact that reached the ledger without a
  // frame reaching THIS browser stayed invisible indefinitely — another tab's
  // edit, an endpoint that saved without broadcasting, a dropped frame, mail
  // (whose frame is animation-only and deliberately refetches nothing).
  //
  // This is the same lesson as the chat heartbeat (convo.beat, D-34) applied to
  // the other half of the app: the gate is "an org view is mounted", which is
  // known LOCALLY and cannot be stale. The payload is ~4 KB and the endpoint
  // answers in 2-12 ms, so the pull costs nothing worth counting; pushes stay
  // and simply make it feel instant instead of being the only way to learn.
  useEffect(() => {
    if (!slug) return
    const t = setInterval(() => refreshTree(slug), TREE_POLL_MS)
    return () => clearInterval(t)
  }, [slug, refreshTree])
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
    if (BASE && !slug && orgs.length) setSlug(orgs[0]!.slug)
  }, [orgs, slug])

  useEffect(() => {                    // back/forward keep working
    const onPop = () => setSlug(slugFromPath())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  useEffect(() => {                    // Escape dismisses the org drawer
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setDrawer(false) }
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

  // a conversation belongs to ONE org — dropping the store on an org switch
  // keeps a stale chat from ever being shown under a different tree
  useEffect(() => { resetConvos() }, [slug])
  useEffect(() => {
    if (!slug) return
    // the WS must SURVIVE backend restarts (updates, redeploys): without
    // auto-reconnect every state indicator froze at its last value until a
    // manual page reload — the "states never line up" bug
    let dead = false
    let timer: ReturnType<typeof setTimeout> | null = null
    const connect = () => {
      if (dead) return
      refreshTree(slug)
      wsRef.current = openWs(slug, handleWs,
        () => { if (!dead) timer = setTimeout(connect, 1500) })
    }
    const handleWs = (ev: MessageEvent<string>) => {
      let data: WsEvent | null = null
      try { data = JSON.parse(ev.data) as WsEvent } catch { /* ignore */ }
      if (data?.type === 'mail') {     // spark on the wire — pure animation
        setMailEvt({ from: data.from, to: data.to, t: Date.now() })
        return
      }
      if (data?.type === 'node_stream') {
        // the conversation model is fed ONCE here, not once per mounted view:
        // a node can be on screen twice (its card and its switchboard panel)
        // and two private copies of one conversation diverge by construction
        // (user bug 2026-08-02). See convo.ts.
        ingestStream(slug, {
          node: data.node, kind: data.kind, text: data.text ?? '',
          // sticky rides through: immediate-command output lives in NO
          // transcript, so the live-feed reconciliation must never sweep it
          ...(data.sticky ? { sticky: true } : {}),
          ...(data.id ? { id: data.id as string } : {}), t: Date.now() })
        return   // live feed only — no tree refetch per message
      }
      if (data?.type === 'node_event') {
        ingestPulse(slug, { node: data.node, event: data.event, t: Date.now() })
        // toasts only here — the tree refetch is the shared one below (each
        // branch used to call refreshTree and then fall through to it again,
        // two fetches per event)
        if (data.event === 'frozen') {   // usage-limit popup (user ruling)
          toast([`${data.node} hit a usage limit and is FROZEN — use the resume button in the top bar when the limit resets`])
        }
        if (data.event === 'spend_frozen') {
          toast(['SPEND LIMIT REACHED — every agent is frozen; raise the limit in the org’s settings (⚙) to resume'])
        }
        if (data.event === 'storage_blocked') {
          toast(['WORKSPACE STORAGE LIMIT reached — file writes are blocked until enough files are deleted (agents keep running)'])
        }
        if (data.event === 'storage_cleared') {
          toast(['workspace back under its storage limit — writes unblocked'])
        }
      }
      refreshTree(slug)
    }
    connect()
    return () => { dead = true; clearTimeout(timer!); wsRef.current?.close() }
  }, [slug, refreshTree])

  // op fires only from the active-org canvas — slug is set there (hence !)
  const op = useCallback((body: OpRequest) =>
    runOp(slug!, body)
      .then((r) => {
        // op-specific result field (OpResult is open in types.ts) — the
        // ceiling-bridge marker, stated at the wire boundary
        const bridge = (r as { bridge?: { raise_ceiling?: boolean } } | null)?.bridge
        if (bridge?.raise_ceiling) {
          // the one-action bridge (ceiling spec §1): the same op, re-sent
          // with the flag — auto_raise OFF never means "go navigate"
          toast(r.warnings?.length ? r.warnings
            : ['clamped to the kiosk permission ceiling'],
          { label: 'raise ceiling & apply',
            fn: () => runOp(slug!, { ...body, raise_ceiling: true })
              .then((r2) => { toast(r2.warnings); refreshTree(slug); refreshOrgs() })
              .catch((e: Error) => toast([`error: ${e.message}`])) })
        } else toast(r.warnings)
        refreshTree(slug); refreshOrgs(); return r
      })
      .catch((e: Error) => { toast([`error: ${e.message}`]); throw e }),
    [slug, toast, refreshTree, refreshOrgs])

  const pick = (s: string) => { setSlug(s); setShowSettings(false); setDrawer(false) }
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
            {(o.working ?? 0) > 0 &&
              <span className="working-ct"
                title={`${o.working} agent${o.working === 1 ? '' : 's'} currently working`}>
                <AutorenewIcon fontSize="inherit" className="cc-spin" /> {o.working}</span>}
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
      {!BASE && <NewOrg onCreate={(name, dirs, kiosk, sandbox, diskMb) =>
        createOrg(name, dirs, kiosk, sandbox, diskMb)
          .then((r) => { refreshOrgs(); pick(r.slug) })
          .catch((e: Error) => toast([`error: ${e.message}`]))} />}
      {/* global default org settings (user spec): every NEW org is born with
          these — admin only */}
      {!BASE && <button className="home" onClick={() => setShowDefaults(true)}>
        <SettingsIcon fontSize="inherit" /> default org settings</button>}
      {/* kiosk dashboard: admin only — a public visitor never sees this panel
          (and the server refuses the endpoints regardless) */}
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
                  const byTier: Record<string, number> = {}
                  for (const n of ns) byTier[n.tier] = (byTier[n.tier] ?? 0) + 1
                  return (
                    <span className="chip agents"
                      title="live agents · currently working · breakdown by model">
                      {ns.length} live{busy > 0 ? ` · ${busy} working` : ''}
                      {TIERS
                        .filter((t) => byTier[t])
                        .map((t) => <b key={t} className={'t-' + t}>{TIER_LETTER[t]}{byTier[t]}</b>)}
                    </span>
                  )
                })()}
                {/* the bare cost chip is redundant when the kiosk spend chip
                    already shows the same figure against its limit (user
                    spec 2026-07-31) — limitless orgs keep it */}
                {tree.cost_usd_total > 0 && !tree.kiosk?.spend_limit &&
                  <span className="chip">${tree.cost_usd_total.toFixed(2)}</span>}
                {tree.fable_lock &&
                  <span className="chip bad" title={tree.fable_lock.at as string | undefined}><BlockIcon fontSize="inherit" /> fable limit</span>}
                {tree.kiosk?.spend_limit && (
                  tree.spend_frozen
                    ? <span className="chip bad"><BlockIcon fontSize="inherit" /> spend limit reached — agents frozen</span>
                    : <span className={'chip' + (tree.cost_usd_total >= tree.kiosk.spend_limit * 0.9 ? ' bad' : '')}
                        title="spend / limit">
                        ${tree.cost_usd_total.toFixed(2)} / ${tree.kiosk.spend_limit.toFixed(2)}
                      </span>
                )}
                {tree.disk ? (
                  // the org disk chip (disk-migrated sandboxed orgs): the
                  // whole footprint against the fs cap; click opens the
                  // recovery browser (visitors get the full tool — ruled)
                  <button className={'chip disk-chip'
                    + ((tree.disk.used_mb ?? 0) >= (tree.disk.total_mb ?? Infinity) * 0.8 || tree.disk.blocked ? ' bad' : '')
                    + (tree.disk.pending_mb != null ? ' pend' : '')}
                    title={'org disk used / capacity — click to browse and free space'
                      + (tree.disk.pending_mb != null
                        ? ` · shrink to ${tree.disk.pending_mb} MB is staged` : '')}
                    onClick={() => setShowDisk('last')}>
                    <StorageIcon fontSize="inherit" /> {tree.disk.used_mb ?? '?'} / {tree.disk.total_mb ?? '?'} MB
                    {tree.disk.pending_mb != null ? ` → ${tree.disk.pending_mb} MB pending` : ''}
                    {tree.disk.full ? ' — FULL' : tree.disk.blocked ? ' — turns paused' : ''}
                  </button>
                ) : tree.kiosk?.storage_limit_mb && (
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
                  const frozen = [...flatNodes(tree).values()]
                    .filter((n): n is TreeNode & { frozen: TreeFrozen } => n.frozen != null)
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
                          .catch((e: Error) => toast([`error: ${e.message}`]))}>
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
                            .catch((e: Error) => toast([`error: ${e.message}`]))}>
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
                        .catch((e: Error) => toast([`error: ${e.message}`]))
                    }}><StopIcon fontSize="inherit" /> STOP ALL</button>
                </span>
                {/* the SECOND inbox icon (user ruling 2026-08-04): it glows —
                    alone in the whole chrome — iff an un-nulled ask (question
                    or credit request) is waiting on the user; the count badge
                    is the FULL unread total (mail + asks), same as the
                    switchboard's ✉ (user ruling 2026-08-05); click opens the
                    inbox */}
                {(() => {
                  const asks = tree.asks_open ?? 0
                  const unread = (tree.user_inbox_count ?? 0) + asks
                  return (
                    <button className={'iconbtn ask-bell' + (asks > 0 ? ' glow' : '')}
                      title={asks > 0
                        ? `${asks} ask${asks > 1 ? 's' : ''} waiting on your answer`
                          + (unread > asks ? ` · ${unread - asks} unread mail` : '')
                        : unread > 0 ? `${unread} unread` : 'your inbox'}
                      onClick={() => { setInboxJump(null); setShowInbox(true) }}>
                      <MailIcon fontSize="inherit" />
                      {unread > 0 && <b className="eye-count">{unread}</b>}
                    </button>
                  )
                })()}
                {!tree.public &&
                  <button onClick={() => setShowSettings(true)}><SettingsIcon fontSize="inherit" /> settings</button>}
                <a className="gh-link" href="https://github.com/Maurdekye/claude-orgtree"
                  target="_blank" rel="noreferrer" title="orgtree on GitHub">
                  <GitHubIcon fontSize="inherit" /></a>
              </header>
              <OrgCanvas tree={tree} op={op} slug={slug} toast={toast}
                mailEvt={mailEvt}
                onInbox={(jump: unknown) => {
                  setInboxJump(typeof jump === 'string' ? jump : null)
                  setShowInbox(true)
                }} />
              {/* hard-full is a STATE, not an event: the alert persists (and
                  survives reloads) until usage drops; it never auto-opens
                  the browser — it carries the button (user refinement) */}
              {tree.disk?.full && (
                <DiskFullAlert onOpen={() => setShowDisk('largest')} />
              )}
              {showDisk && (
                <DiskBrowser slug={slug} isPublic={!!tree.public} toast={toast}
                  initialMode={showDisk === 'largest' ? 'largest' : undefined}
                  close={() => { setShowDisk(false); refreshTree(slug) }} />
              )}
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
            .catch((e: Error) => toast([`error: ${e.message}`]))}
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
                ;(typeof t.undo === 'function' ? t.undo : t.undo!.fn)()
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

/** F-07 (user ruling 2026-08-04: "both, one modal"): the ONE advanced-org
 *  modal shell. The create form's advanced disclosure and the ⚙ settings
 *  panel both open this same surface; each pours in its own sections, and
 *  creation-only facts (kiosk, sandbox, disk type) render as LOCKED chips
 *  outside creation — visible, never editable, so the modal can't offer to
 *  change what cannot change after birth. No save button of its own: the
 *  create form submits, and the settings panel keeps its ONE bottom save
 *  (three save surfaces was a user-reported failure once already). */
function AdvancedOrgModal({ title, close, children }: {
  title: string
  close: () => void
  children: ReactNode
}) {
  useEsc(close)
  return (
    <div className="overlay" onClick={(e) => { e.stopPropagation(); close() }}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> {title} — advanced</h3>
        {children}
        <div className="row">
          <button className="primary" type="button" onClick={close}>done</button>
        </div>
      </div>
    </div>
  )
}

function NewOrg({ onCreate }: {
  onCreate: (name: string, dirs: string[], kiosk: KioskSpecRequest | null,
             sandbox: boolean, diskMb: number | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [advanced, setAdvanced] = useState(false)
  const [name, setName] = useState('')
  const [dirs, setDirs] = useState<string[]>([])
  // kiosk is a CREATION-TIME type (user ruling): a checkbox here reveals its
  // limit fields; auth is never configurable — sandboxes use the proxied
  // subscription (the host holds the token; the sandbox never sees it)
  const [kiosk, setKiosk] = useState(false)
  // kiosk cap defaults (user ruling 2026-07-31): 30 credits · $50; storage
  // starts at the 1 GB loose-cap default and is bumped to the 4096 MB disk
  // minimum whenever the sandbox turns on (user ruling 2026-08-01)
  const [credits, setCredits] = useState<number | string>(30)
  const [spend, setSpend] = useState<number | string>(50)
  const [storage, setStorage] = useState<number | string>(1024)
  // the permission ceiling is visible AT CREATION (ceiling spec §3): the
  // default is permissive (mcp "*", user ruling), so narrowing must be a
  // conscious act here rather than something discovered later
  const [ceil, setCeil] = useState({ bash: true, web: true, edit: true,
                                     subagents: true, mcp: true })
  const [ceilPm, setCeilPm] = useState('acceptEdits')
  const [ceilVis, setCeilVis] = useState('full')
  const [ceilTier, setCeilTier] = useState('')   // '' = no tier cap
  const [autoRaise, setAutoRaise] = useState(false)
  // sandbox is OFF by default (user ruling) — and impossible without Docker
  const [sandboxed, setSandboxed] = useState(false)
  const [docker, setDocker] = useState(false)
  useEffect(() => {
    getHost().then((h) => setDocker(!!h.docker)).catch(() => {})
  }, [])
  const reset = () => {
    setOpen(false); setAdvanced(false); setName(''); setDirs([])
    setKiosk(false); setSandboxed(false)
  }
  if (!open) return <button className="primary" onClick={() => setOpen(true)}>+ new organization</button>
  return (
    <form className="stack" onSubmit={(e) => {
      e.preventDefault()
      onCreate(name, dirs.map((s) => s.trim()).filter(Boolean),
        kiosk ? {
          credits: +credits || 0, spend_limit: +spend || 0,
          // sandboxed = the limit IS the org disk size; clamp to the floor
          storage_limit_mb: sandboxed
            ? Math.max(4096, +storage || 4096) : +storage || 0,
          sandbox: sandboxed,
          auto_raise: autoRaise,
          max_scope: {
            tools: { bash: ceil.bash, web: ceil.web, edit: ceil.edit,
                     subagents: ceil.subagents, mcp: ceil.mcp ? ['*'] : [] },
            org_visibility: ceilVis, permission_mode: ceilPm,
            max_tier: ceilTier || null,
          },
        } : null,
        sandboxed,
        sandboxed && !kiosk ? Math.max(4096, +storage || 4096) : null)
      reset()
    }}>
      <input autoFocus placeholder="organization name" value={name}
        onChange={(e) => setName(e.target.value)} required />
      {/* F-07: the disclosure now OPENS the shared advanced modal instead of
          unfolding inline — same summary line (the at-a-glance state the form
          must not lose), one modal shape shared with the ⚙ settings panel */}
      <button type="button" className="disclosure" aria-expanded={advanced}
        onClick={() => setAdvanced(true)}>
        <ChevronRightIcon fontSize="inherit" /> advanced…
        {(kiosk || sandboxed || dirs.length > 0) && (
          <span className="dim adv-sum"> · {[
            kiosk ? 'kiosk' : '', sandboxed ? 'sandboxed' : '',
            dirs.length ? `${dirs.length} folder${dirs.length > 1 ? 's' : ''}` : '',
          ].filter(Boolean).join(' · ')}</span>)}
      </button>
      {advanced && (
        <AdvancedOrgModal title={name.trim() || 'new organization'}
          close={() => setAdvanced(false)}>
          <div className="field-label">also grant existing folders</div>
          <DirList dirs={dirs} onChange={setDirs} />
          {/* kiosk and sandbox live here (user ruling 2026-08-03): both are
              advanced choices — one publishes the org, the other changes where
              every turn executes — and neither belongs in the two-field path
              most new orgs take. Below the folder grants, deliberately: the
              sandbox decides whether those folders are reachable at all. */}
          <div className="field-label adv-sep">org type</div>
        <label className="row kiosk-sbx">
          <input type="checkbox" checked={kiosk}
            onChange={(e) => {
              setKiosk(e.target.checked)
              // kiosks default the sandbox ON — but only where Docker exists
              if (e.target.checked && docker) {
                setSandboxed(true)
                setStorage((s) => Math.max(4096, +s || 0))
              }
            }} />
          kiosk — publicly shareable via a secret URL, with hard limits
        </label>
        {kiosk && (
          <div className="kiosk-caps">
            <label>credits <input type="number" min="0" value={credits}
              onChange={(e) => setCredits(e.target.value)} /></label>
            <label>spend $ <input type="number" min="0" step="0.5" value={spend}
              onChange={(e) => setSpend(e.target.value)} /></label>
            <label title={sandboxed
              ? 'the org’s fixed-size virtual disk — system dirs and transcripts count inside it; 4096 MB minimum'
              : 'loose workspace+scratch cap (checked between turns)'}>
              {sandboxed ? 'disk MB' : 'storage MB'}
              <input type="number" min={sandboxed ? 4096 : 0} value={storage}
              onChange={(e) => setStorage(e.target.value)} /></label>
          </div>
        )}
        {kiosk && (
          <div className="kiosk-ceil">
            <div className="field-label"
              title="the MAXIMUM grantable to any agent in this kiosk — visitors retool freely within it; folders bound to the org's own">
              permission ceiling</div>
            <div className="ceil-tools">
              {(['bash', 'web', 'edit', 'subagents', 'mcp'] as const).map((k) => (
                <label key={k} className="row">
                  <input type="checkbox" checked={ceil[k]}
                    onChange={(e) => setCeil((c) => ({ ...c, [k]: e.target.checked }))} />
                  {k === 'mcp' ? 'MCP servers' : k}
                </label>
              ))}
            </div>
            {/* the rank ceilings — styled like the credits/spend/storage caps
                (user spec 2026-07-31): stacked label, three columns */}
            <div className="kiosk-caps">
              <label>visibility ≤ <select value={ceilVis}
                onChange={(e) => setCeilVis(e.target.value)}>
                {['self', 'team', 'subtree', 'full'].map((v) =>
                  <option key={v} value={v}>{v}</option>)}
              </select></label>
              <label>mode ≤ <select value={ceilPm}
                onChange={(e) => setCeilPm(e.target.value)}>
                <option value="default">default (asks)</option>
                <option value="acceptEdits">acceptEdits</option>
                <option value="bypassPermissions">bypassPermissions</option>
              </select></label>
              <label
                title="the highest model tier this kiosk may run — spawn tokens above it disappear and agents cannot hire, rehire or switch above it">
                tier ≤ <select value={ceilTier}
                  onChange={(e) => setCeilTier(e.target.value)}>
                  <option value="">fable</option>
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
            onChange={(e) => {
              setSandboxed(e.target.checked)
              // the sandbox rides a fixed-size disk — bump the storage field
              // to its 4096 MB minimum (user ruling 2026-08-01)
              if (e.target.checked) setStorage((s) => Math.max(4096, +s || 0))
            }} />
          sandboxed — agents run in a Docker container, isolated from this PC
          {!docker && <span className="dim"> (requires Docker)</span>}
        </label>
        {sandboxed && !kiosk && (
          <div className="kiosk-caps">
            <label title="the org&rsquo;s fixed-size virtual disk — system dirs and transcripts count inside it; 4096 MB minimum">
              disk MB <input type="number" min="4096" value={storage}
                onChange={(e) => setStorage(e.target.value)} /></label>
          </div>
        )}
        {kiosk && !sandboxed && (
          <div className="dim kiosk-warn"><WarnIcon fontSize="inherit" /> without
            a sandbox the storage limit is enforced loosely — usage is checked
            only between turns, so a single turn can overshoot it</div>
        )}
        </AdvancedOrgModal>
      )}
      <div className="row">
        <button type="submit" className="primary">create</button>
        <button type="button" onClick={reset}>cancel</button>
      </div>
    </form>
  )
}

function flatNodes(tree: TreePayload): Map<string, TreeNode> {
  const map = new Map<string, TreeNode>()
  const walk = (n: TreeNode) => { map.set(n.id, n); n.children.forEach(walk) }
  tree.roots.forEach(walk)
  return map
}

function SenderChip({ id, nodes }: { id: string; nodes: Map<string, TreeNode> }) {
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


// audience requests parked at the user (fields the inbox reads) —
// AudienceRequest is an open dict in types.ts
interface UserAudReq {
  from: string
  reason?: string
  [k: string]: unknown
}

function InboxPanel({ slug, tree, toast, refresh, close, jumpTo }: {
  slug: string
  tree: TreePayload
  toast: ToastFn
  refresh?: () => void
  close: () => void
  jumpTo: string | null
}) {
  useEsc(close)
  const [folder, setFolder] = useState('inbox')
  const nodes = flatNodes(tree)
  // G5: mail arrives, and audience requests are raised by agents, while this
  // panel sits open. Polled while mounted rather than fetched once — the same
  // gate as everywhere else: "is anyone looking at this".
  const box = usePolled(() => getInbox(slug), [slug])
  const aud = usePolled(() => getAudiences(slug), [slug])
  // №10: the record loads on demand — and keeps loading while that tab is up
  const events = usePolled(
    () => (folder === 'record' ? getEvents(slug).then((r) => r.events)
      : Promise.resolve(null)), [folder, slug])
  const userAud = aud?.audiences.filter((a) => a.grantor === USER) ?? []
  const userReqs = (aud?.requests.filter((r) => r.target === USER && r.currently_at === USER) ?? []) as UserAudReq[]
  const act = (action: string, node: string, target?: string | null) =>
    audienceAction(slug, action, node, target)
      .catch((e: Error) => toast([`error: ${e.message}`]))
  // Asks ride the inbox as their OWN mail rows (user ruling 2026-08-04),
  // interleaved chronologically with real mail — the only difference is the
  // reading pane shows the response UI as the body instead of a reply box.
  // Open asks join the unread group; resolved ones sit in the flow wearing
  // their nulled state (grey answered/denied, orange interrupted).
  const askRow = (a: AskInfo): MailRow => ({
    id: 'ask:' + a.id, from: a.node, at: a.at,
    kind: (a.kind === 'credit' || a.old != null) ? 'credit request' : 'question',
    body: a.question ?? `asks for credits: ${a.old} → ${a.new}`,
    _ask: a,
  } as MailRow)
  const askOpen = (a: AskInfo) => a.status === 'open' || a.status === 'pending'
  const asks = tree.asks ?? []
  const askPending = asks.filter(askOpen).map(askRow)
  const askDone = asks.filter((a) => !askOpen(a)).slice(-8).map(askRow)
  const renderAskBody = (m: MailRow) => {
    if (!m._ask) return null
    const n = nodes.get(m._ask.node)
    return (
      <AskCard ask={m._ask} slug={slug} toast={toast}
        seat={n?.seat ?? 0}
        committed={(n?.grant ?? 0) - (n?.free ?? 0)}
        segments={(n?.children ?? []).filter((c) => c.state === 'live')
          .map((c) => ({ seat: c.seat, grant: c.grant }))}
        pxc={orgPxc(tree)}
        maxTop={tree.max_top_grant ?? 1000} />
    )
  }
  return (
    <div className="overlay" onClick={close}>
      <div className="settings wide" onClick={(e) => e.stopPropagation()}>
        <h3><MailIcon fontSize="inherit" /> your inbox</h3>
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
          unread={(box?.pending.length ?? 0) + askPending.length} />
        <div className="mailpane">
          {folder === 'record'
            ? <OrgRecord events={events} />
            : box == null
            ? <div className="dim">loading…</div>
            : folder === 'inbox'
              ? <MailList pending={[...box.pending, ...askPending]}
                  delivered={[...box.delivered, ...askDone]}
                  renderBody={renderAskBody}
                  waitLabel="unread" jumpTo={jumpTo}
                  onRead={(m: MailEntry) => markRead(slug, [m.id])
                    .then(() => { refresh?.() }).catch(() => {})}
                  onReply={(m: MailEntry, text: string) => {
                    // the desk composer's optimistic ghost, which this
                    // composer never had (D-54): a reply sent from the inbox
                    // is an ordinary message to that node, and its desk —
                    // open behind this modal, or opened a second later —
                    // showed nothing at all until the server copy landed.
                    // Same store, same graduation-on-evidence rule.
                    addPending(slug, m.from, text)
                    return sendMessage(slug, m.from, text)
                      .then(() => toast([`sent to ${m.from}`]))
                      .catch((e: Error) => {
                        dropPending(slug, m.from, text)
                        toast([`error: ${e.message}`])
                      })
                  }}
                  sender={(id: string) => <SenderChip id={id} nodes={nodes} />} />
              : <MailList delivered={box.sent ?? []} outgoing
                  sender={(id: string) => <SenderChip id={id} nodes={nodes} />} />}
        </div>
        <div className="row">
          {folder === 'inbox' && (box?.pending.length ?? 0) > 0 && <button onClick={() =>
            clearInbox(slug).catch((e: Error) => toast([`error: ${e.message}`]))}>mark all read</button>}
          <button className="primary" onClick={close}>close</button>
        </div>
      </div>
    </div>
  )
}

// Global DEFAULT org settings (user spec, root page): every newly created
// org is born with these values — the same knobs as a single org's settings
// panel, saved once in <data>/defaults.json.
function DefaultsPanel({ toast, close }: { toast: ToastFn; close: () => void }) {
  useEsc(close)
  // Partial: the error fallback seeds {} and every read has its own default
  const [d, setD] = useState<Partial<DefaultsPayload> | null>(null)
  useEffect(() => { getDefaults().then(setD).catch(() => setD({})) }, [])
  if (d == null) {
    return (
      <div className="overlay" onClick={close}>
        <div className="settings"><div className="dim pad">loading…</div></div>
      </div>
    )
  }
  const set = (k: string, v: unknown) => setD({ ...d, [k]: v })
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
        <div className="field-label">default thinking effort (agents without
          their own setting inherit this, live)</div>
        <select value={d.default_effort ?? ''}
          onChange={(e) => set('default_effort', e.target.value)}>
          <option value="">CLI default (no flag)</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="xhigh">xhigh</option>
          <option value="max">max</option>
        </select>
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
              default_effort: d.default_effort ?? '',
              cascade_hire: d.cascade_hire !== false,
              cascade_alloc: d.cascade_alloc !== false,
              auto_resume: !!d.auto_resume,
            }).then(() => { toast(['default org settings saved']); close() })
              .catch((e: Error) => toast([`error: ${e.message}`]))}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>
  )
}

// a ceiling folder row — mode stays `string`: the row is round-tripped from
// the open max_scope dict, and the selects constrain it to rw/ro anyway
interface CeilDir { path: string; mode: string }

// the ceiling document the settings panel edits — max_scope is an open dict
// in types.ts (TreeKiosk); this states the fields read/written here
interface MaxScope {
  tools?: { bash?: boolean; web?: boolean; edit?: boolean; subagents?: boolean; mcp?: string[] } | null
  add_dirs?: CeilDir[] | null
  org_visibility?: string | null
  permission_mode?: string | null
  max_tier?: string | null
}

// mode-aware folder rows for the kiosk ceiling (DirList is string-only)
function CeilDirs({ dirs, onChange }: {
  dirs: CeilDir[]
  onChange: (dirs: CeilDir[]) => void
}) {
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

// The pre-migration backup sweep (disk orgs): the migration kept the legacy
// volumes and host-dir copies for rollback — this shows their cost and drops
// them behind an armed click. Renders nothing once the backup is gone.
function SweepBlock({ slug, toast }: { slug: string; toast: ToastFn }) {
  const [prev, setPrev] = useState<SweepPreview | null>(null)
  const [armed, setArmed] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    getSweepPreview(slug).then(setPrev).catch(() => setPrev(null))
  }, [slug])
  if (!prev || (!prev.volumes.length && !prev.host_dirs.length)) return null
  const mb = (b: number) => `${Math.round(b / 1048576)} MB`
  return (
    <>
      <div className="field-label">pre-migration backup (rollback for the
        disk migration)</div>
      <div className="hint">
        {prev.volumes.length} legacy volume(s) ({mb(prev.volumes_bytes)}) +
        host copies ({mb(prev.host_bytes)}) = {mb(prev.total_bytes)} held
        only for rollback — the live data is on the org disk.
      </div>
      <button className={'disk-del' + (armed ? ' armed' : '')} disabled={busy}
        onMouseLeave={() => setArmed(false)}
        onClick={() => {
          if (!armed) { setArmed(true); return }
          setArmed(false)
          setBusy(true)
          sweepLegacy(slug)
            .then((r) => {
              toast(r.failures.length
                ? [`swept with ${r.failures.length} failure(s): ${r.failures[0]}`]
                : [`rollback backup deleted — freed ~${mb(prev.total_bytes)}`])
              setPrev(null)
            })
            .catch((e: Error) => toast([`error: ${e.message}`]))
            .finally(() => setBusy(false))
        }}>
        <DeleteIcon fontSize="inherit" />
        {armed ? `really delete the rollback (~${mb(prev.total_bytes)})?`
          : 'delete the pre-migration backup'}
      </button>
    </>
  )
}

// Desk text size — a DEVICE preference, never org state: the same org read on a
// laptop and a 4K monitor wants different values. Applied immediately to the
// --desk-dpi custom property, so it is not part of the settings save.
function DeskTextSize() {
  const [dpi, setDpi] = useState(deskDpi)
  const apply = (v: number) => {
    const c = Math.min(2.5, Math.max(0.75, Math.round(v * 100) / 100))
    setDpi(c); setDeskDpi(c)
  }
  return (
    <>
      <div className="field-label">desk text size — this browser only (the desk
        is counter-scaled into the card, so it reads smaller on smaller screens)</div>
      <div className="row">
        <button onClick={() => apply(dpi - 0.25)} disabled={dpi <= 0.75}>−</button>
        <span style={{ minWidth: '4.5em', textAlign: 'center' }}>{Math.round(dpi * 100)}%</span>
        <button onClick={() => apply(dpi + 0.25)} disabled={dpi >= 2.5}>+</button>
        <button onClick={() => apply(1)} disabled={dpi === 1}>reset</button>
      </div>
    </>
  )
}

function SettingsPanel({ tree, toast, close }: {
  tree: TreePayload
  toast: ToastFn
  close: () => void
}) {
  useEsc(close)
  // P3 — every field below used to be its own useState SEEDED FROM `tree`.
  // useState(x) snapshots x once at mount and never looks again, so this panel
  // held seventeen private copies of server values that could each go stale
  // silently (the mechanism behind the user's "the charter looks empty"). Now
  // there is ONE cell: the edits you have actually made. Everything else is
  // derived from the prop on every render, so a value that changes anywhere
  // else shows up here, and saving clears the buffer back to server truth.
  const [edit, setEdit] = useState<Record<string, unknown>>({})
  // takes the value THIS render derived, so an updater form still works
  const set = <T,>(k: string, cur: T) => (v: T | ((prev: T) => T)) =>
    setEdit((e) => ({ ...e,
      [k]: typeof v === 'function' ? (v as (p: T) => T)(cur) : v }))
  const val = <T,>(k: string, server: T): T =>
    (k in edit ? edit[k] as T : server)
  const clearEdits = () => setEdit({})
  const [orgMd, setOrgMd] = useState<string | null>(null)
  const [showAdv, setShowAdv] = useState(false)   // F-07: the shared modal

  // kiosk permission ceiling (consensus spec): admin payload only — the
  // public tree never carries max_scope
  const ms = tree.kiosk?.max_scope as MaxScope | null | undefined
  // const extraction so the kiosk narrowing survives the click closures
  const kk = tree.kiosk
  // the shadowing pair below keeps every USE SITE unchanged: same name, same
  // setter signature — only where the value comes from has changed
  const maxTop = val<number | string>('maxTop', tree.max_top_grant ?? 1000)
  const setMaxTop = set('maxTop', maxTop)
  const defTop = val<number | string>('defTop', tree.default_top_grant ?? 50)
  const setDefTop = set('defTop', defTop)
  const compactAt = val<number | string>('compactAt',
    Math.round((tree.compact_at ?? 0.8) * 100))
  const setCompactAt = set('compactAt', compactAt)
  const fablePolicy = val('fablePolicy', tree.fable_limit_policy ?? 'halt')
  const setFablePolicy = set('fablePolicy', fablePolicy)
  const filterPolicy = val('filterPolicy', tree.fable_filter_policy ?? 'halt')
  const setFilterPolicy = set('filterPolicy', filterPolicy)
  const defEffort = val('defEffort', tree.default_effort ?? '')
  const setDefEffort = set('defEffort', defEffort)
  const cascadeHire = val('cascadeHire', tree.cascade_hire !== false)
  const setCascadeHire = set('cascadeHire', cascadeHire)
  const cascadeAlloc = val('cascadeAlloc', tree.cascade_alloc !== false)
  const setCascadeAlloc = set('cascadeAlloc', cascadeAlloc)
  const srvCeil = useMemo(() => (ms ? {
    bash: !!ms.tools?.bash, web: !!ms.tools?.web, edit: !!ms.tools?.edit,
    subagents: !!ms.tools?.subagents } : null), [ms])
  const ceil = val('ceil', srvCeil)
  const setCeil = set('ceil', ceil)
  const ceilMcp = val('ceilMcp', (ms?.tools?.mcp ?? []).join(', '))
  const setCeilMcp = set('ceilMcp', ceilMcp)
  const srvDirs = useMemo(() => ms?.add_dirs ?? [], [ms])
  const ceilDirs = val<CeilDir[]>('ceilDirs', srvDirs)
  const setCeilDirs = set('ceilDirs', ceilDirs)
  const ceilVis = val('ceilVis', ms?.org_visibility ?? 'full')
  const setCeilVis = set('ceilVis', ceilVis)
  const ceilPm = val('ceilPm', ms?.permission_mode ?? 'acceptEdits')
  const setCeilPm = set('ceilPm', ceilPm)
  const ceilTier = val('ceilTier', ms?.max_tier ?? '')
  const setCeilTier = set('ceilTier', ceilTier)
  const autoRaise = val('autoRaise', !!tree.kiosk?.auto_raise)
  const setAutoRaise = set('autoRaise', autoRaise)
  // per-kiosk caps (moved here from the retired all-kiosks dashboard)
  const kkCredits = val<number | string>('kkCredits', tree.kiosk?.credits ?? 0)
  const setKkCredits = set('kkCredits', kkCredits)
  const kkSpend = val<number | string>('kkSpend', tree.kiosk?.spend_limit ?? 0)
  const setKkSpend = set('kkSpend', kkSpend)
  const kkStorage = val<number | string>('kkStorage', tree.kiosk?.storage_limit_mb ?? 0)
  const setKkStorage = set('kkStorage', kkStorage)
  useEffect(() => {
    // null = not loaded: the textarea is disabled and save skips the write.
    // ☠ The catch used to set '' — an empty EDITABLE buffer — so a transient
    // fetch failure plus one ordinary save wiped the org's charter with
    // putOrgMd(slug, ''). A failed READ must never arm a destructive write;
    // null also resets on org switch so the previous org's text cannot be
    // saved into the new one during the load window.
    setOrgMd(null)
    getOrgMd(tree.slug).then((r) => setOrgMd(r.content)).catch(() => setOrgMd(null))
  }, [tree.slug])
  return (
    <div className="overlay" onClick={close}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> {tree.name} — settings</h3>
        <DeskTextSize />
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
        {/* default effort (user req 2026-08-01, visible inherit): agents
            without their own effort follow this LIVE — changing it here
            reaches every unset agent's next turn, no rehire */}
        <div className="field-label">default thinking effort (agents without
          their own setting inherit this, live)</div>
        <select value={defEffort} onChange={(e) => setDefEffort(e.target.value)}>
          <option value="">CLI default (no flag)</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="xhigh">xhigh</option>
          <option value="max">max</option>
        </select>
        {/* per-kiosk controls (user ruling 2026-07-31): caps, share URL and
            pause live HERE, in the org's own settings — the all-kiosks
            dashboard on the welcome panel is gone */}
        {kk && (
          <>
            <div className="field-label">kiosk caps
              {kk.sandbox && <span className="dim"> · sandboxed</span>}
              {!kk.enabled && <span className="dim"> · URL paused</span>}
            </div>
            <div className="kiosk-caps">
              <label>credits <input type="number" min="0" value={kkCredits}
                onChange={(e) => setKkCredits(e.target.value)} /></label>
              <label>spend $ <input type="number" min="0" step="0.5" value={kkSpend}
                onChange={(e) => setKkSpend(e.target.value)} /></label>
              <label title={kk.sandbox
                ? 'the org’s fixed-size virtual disk — 4096 MB minimum (already-migrated orgs resize via the storage browser)'
                : 'loose workspace+scratch cap (checked between turns)'}>
                {kk.sandbox ? 'disk MB' : 'storage MB'}
                <input type="number" min={kk.sandbox ? 4096 : 0} value={kkStorage}
                onChange={(e) => setKkStorage(e.target.value)} /></label>
              {/* saved by the panel's bottom "save" — the old inline ✓ (and
                  the ceiling's own apply button) made three save surfaces
                  nobody could find (user report 2026-08-01) */}
            </div>
            <div className="row kiosk-url">
              <input readOnly value={kk.share_url
                ?? '(set ORGTREE_PUBLIC_PORT to serve public URLs)'}
                onFocus={(e) => e.target.select()} />
              <button title="copy the share URL" disabled={!kk.share_url}
                onClick={() => navigator.clipboard.writeText(kk.share_url!)
                  .then(() => toast(['share URL copied']))}>
                <CopyIcon fontSize="inherit" /></button>
              <button title="rotate the secret (the old URL stops working immediately)"
                onClick={() => saveKiosk(tree.slug, { rotate_token: true })
                  .then(() => toast(['secret rotated — the old URL is dead']))
                  .catch((e: Error) => toast([`error: ${e.message}`]))}>
                <AutorenewIcon fontSize="inherit" /></button>
              <button title={kk.enabled
                ? 'pause the public URL (the org stays a kiosk; limits always bind)'
                : 'reactivate the public URL'}
                onClick={() => saveKiosk(tree.slug, { enabled: !kk.enabled })
                  .then(() => toast([kk.enabled
                    ? 'public URL paused' : 'public URL live']))
                  .catch((e: Error) => toast([`error: ${e.message}`]))}>
                {kk.enabled ? <BlockIcon fontSize="inherit" />
                  : <PlayIcon fontSize="inherit" />}</button>
            </div>
          </>
        )}
        <div className="field-label">org.md</div>
        <textarea rows={6} value={orgMd ?? ''} disabled={orgMd == null}
          onChange={(e) => setOrgMd(e.target.value)} />
        {/* F-07: everything below the everyday knobs lives in the shared
            advanced modal — same shape the create form opens. The summary
            names what is set, so nothing hides silently. */}
        <button type="button" className="disclosure" onClick={() => setShowAdv(true)}>
          <ChevronRightIcon fontSize="inherit" /> advanced…
          <span className="dim adv-sum"> · {[
            kk ? 'kiosk' : '', tree.sandboxed ? 'sandboxed' : '',
            fablePolicy !== 'halt' ? `fable-limit:${fablePolicy}` : '',
            filterPolicy !== 'halt' ? `fable-filter:${filterPolicy}` : '',
            !cascadeHire || !cascadeAlloc ? 'cascade off' : '',
          ].filter(Boolean).join(' · ') || 'policies & ceiling'}</span>
        </button>
        {showAdv && (
          <AdvancedOrgModal title={tree.name} close={() => setShowAdv(false)}>
            {/* born-with facts render LOCKED: the modal must not offer to
                change what cannot change after creation (docket F-07 rule 1) */}
            <div className="field-label">born-with — set at creation, immutable</div>
            <div className="row" style={{ flexWrap: 'wrap' }}>
              <span className="badge dim">{kk ? 'kiosk' : 'not a kiosk'}</span>
              <span className="badge dim">{tree.sandboxed ? 'sandboxed (Docker)' : 'unsandboxed'}</span>
              {tree.disk && <span className="badge dim">fixed disk · resize via the storage browser</span>}
            </div>
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
                  {(['bash', 'web', 'edit', 'subagents'] as const).map((k) => (
                    <label key={k} className="checkline">
                      <input type="checkbox" checked={ceil[k]}
                        onChange={(e) => setCeil((c) => ({ ...c!, [k]: e.target.checked }))} />
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
                {/* styled like the credits/spend/storage caps (user spec) */}
                <div className="kiosk-caps">
                  <label>visibility ≤ <select value={ceilVis}
                    onChange={(e) => setCeilVis(e.target.value)}>
                    {['self', 'team', 'subtree', 'full'].map((v) =>
                      <option key={v} value={v}>{v}</option>)}
                  </select></label>
                  <label>mode ≤ <select value={ceilPm}
                    onChange={(e) => setCeilPm(e.target.value)}>
                    <option value="default">default</option>
                    <option value="acceptEdits">acceptEdits</option>
                    <option value="bypassPermissions">bypassPermissions</option>
                  </select></label>
                  <label
                    title="the highest model tier this kiosk may run — spawn tokens above it disappear; hires, rehires and switches above it are refused (existing over-cap agents stay until you switch or retire them)">
                    tier ≤ <select value={ceilTier}
                      onChange={(e) => setCeilTier(e.target.value)}>
                      <option value="">fable</option>
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
              </>
            )}
            {tree.fable_lock && (
              <button className="danger" onClick={() =>
                saveSettings(tree.slug, { clear_fable_lock: true })
                  .then((r) => { toast(r.warnings); close() })
                  .catch((e: Error) => toast([`error: ${e.message}`]))}>
                <BlockIcon fontSize="inherit" /> clear the fable weekly-limit lock (your decree)</button>
            )}
            {tree.disk && <SweepBlock slug={tree.slug} toast={toast} />}
            <div className="dim" style={{ fontSize: '11.5px' }}>
              changes here save with the panel's own save button
            </div>
          </AdvancedOrgModal>
        )}
        <div className="row">
          <button className="primary" onClick={() => {
            // the bottom save applies the WHOLE panel: the kiosk caps and
            // the permission ceiling have their own inline buttons, but a
            // ceiling change followed by "save" used to silently revert
            // (user report 2026-08-01) — so any dirty group rides along here
            const jobs: Promise<{ warnings?: string[]
                                  freezes_cleared?: string[] }>[] = [
              saveSettings(tree.slug,
                { max_top_grant: +maxTop || undefined,
                  default_top_grant: Number.isFinite(+defTop) ? +defTop : undefined,
                  compact_at: Number.isFinite(+compactAt) ? +compactAt : undefined,
                  fable_limit_policy: fablePolicy,
                  fable_filter_policy: filterPolicy,
                  default_effort: defEffort,
                  cascade_hire: cascadeHire,
                  cascade_alloc: cascadeAlloc }),
              orgMd != null ? putOrgMd(tree.slug, orgMd).then(() => ({}))
                : Promise.resolve({}),
            ]
            if (kk && (+kkCredits !== (kk.credits ?? 0)
                || +kkSpend !== (kk.spend_limit ?? 0)
                || +kkStorage !== (kk.storage_limit_mb ?? 0)))
              jobs.push(saveKiosk(tree.slug, {
                credits: +kkCredits || 0, spend_limit: +kkSpend || 0,
                storage_limit_mb: kk.sandbox
                  ? Math.max(4096, +kkStorage || 4096) : +kkStorage || 0 }))
            if (ms && ceil) {
              const scope = {
                tools: { ...ceil,
                         mcp: ceilMcp.split(',').map((s) => s.trim())
                           .filter(Boolean) },
                add_dirs: ceilDirs.filter((d) => d.path.trim()),
                org_visibility: ceilVis, permission_mode: ceilPm,
                max_tier: ceilTier || null,
              }
              // dirty check against the stored (normalized) ceiling — same
              // key order on both sides makes stringify a faithful compare
              const cur = {
                tools: { bash: !!ms.tools?.bash, web: !!ms.tools?.web,
                         edit: !!ms.tools?.edit, subagents: !!ms.tools?.subagents,
                         mcp: ms.tools?.mcp ?? [] },
                add_dirs: ms.add_dirs ?? [],
                org_visibility: ms.org_visibility ?? 'full',
                permission_mode: ms.permission_mode ?? 'acceptEdits',
                max_tier: ms.max_tier ?? null,
              }
              if (JSON.stringify(scope) !== JSON.stringify(cur)
                  || autoRaise !== !!kk?.auto_raise)
                jobs.push(saveKiosk(tree.slug,
                  { auto_raise: autoRaise, max_scope: scope }))
            }
            Promise.all(jobs).then((rs) => {
              const cleared = rs.flatMap((r) => r.freezes_cleared ?? [])
              const lines = [
                ...(cleared.length
                  ? [`limit raised — cleared: ${cleared.join(', ')}`] : []),
                ...rs.flatMap((r) => r.warnings ?? []),
              ]
              toast(lines.length ? lines : ['settings saved'])
              // the edits are the server's now — drop the buffer so the panel
              // reads from the tree again rather than from what was typed
              clearEdits()
              close()
            }).catch((e: Error) => toast([`error: ${e.message}`]))
          }}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>
  )
}
