import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  audienceAction, BASE, clearInbox, createOrg, deleteOrg,
  fileBase, fileUrl, getAudiences, getDefaults, getEvents, getHost, getInbox, getOrgMd,
  getAntigravityUsage, getAntigravityUsagePeek,
  getCodexUsage, getCodexUsagePeek, getOpenRouterUsage, getOpenRouterUsagePeek,
  getOrgNet, getProviders, getSweepPreview, getTree,
  getUsageAll, getUsagePeek, killAll, listOrgs,
  markRead, openWs,
  probeHub, putOrgMd,
  resumeFrozen, runOp, saveDefaults, saveKiosk, saveSettings, sendMessage,
  sweepLegacy,
} from './api'
import { bumpLive } from './livebus'
import { AudienceFold, ConfirmModal, MailFolders, MailList, OrgCanvas, OrgRecord, RetiredFold, useEsc } from './Canvas'
import { DiskBrowser, DiskFullAlert } from './DiskBrowser'
import {
  AutorenewIcon, BlockIcon, CheckIcon, ChevronRightIcon, CloseIcon, CopyIcon, EyeIcon, LanIcon,
  DataUsageIcon, DeleteIcon, DocIcon, ExpandMoreIcon, GitHubIcon, HearingIcon, HomeIcon, LockIcon,
  LockOpenIcon, MailIcon, MenuIcon, PlayIcon, PublicIcon, SettingsIcon,
  SparkIcon, StopIcon, StorageIcon, WarnIcon,
} from './icons'
import { DirList } from './forms'
import { FolderPickerHost } from './picker'
import { activeDocCount, ALL_TIERS, attentionPip, availableAutopsyModels, deskDpi, fallbackActive, freezeKind, isOpenRouterTier, orgPxc, presenceOfPayload, primedRestartChip, setDeskDpi, TIER_LETTER, tierLabel, usePolled } from './canvas/shared'
import { AskCard } from './canvas/asks'
import { AccountsPanel, UsageBars } from './canvas/accounts'
import { DocGalleryModal } from './canvas/gallery'
import {
  SetBlock, SetGroup, SetRow, SettingsTabPanel, SettingsTabs, SetToggle,
  useVisitedTabs,
} from './canvas/settingskit'
import type { SettingsTab } from './canvas/settingskit'
import { addPending, dropPending, ingestPulse, ingestStream, resetConvos } from './convo'
import type {
  AskInfo, AudiencesPayload, CacheForecast, DefaultsPayload, HostPayload, InboxPayload,
  KioskSpecRequest,
  MailEntry, OpRequest, OrgEvent, OrgListEntry, SweepPreview, ToastFn,
  ProvidersPayload,
  ToastUndo, TreeFrozen, TreeNode, TreePayload, UsageLimit, UsagePeek,
} from './types'
import type { MailRow, ProviderPresence } from './canvas/shared'

/** the cost chip's hover split: how much of the org total was billed to the
 *  api_fallback key vs the subscription. '' when the org has never used (and
 *  doesn't hold) a fallback key — the tooltip stays quiet rather than showing
 *  a meaningless $0.00 lane. */
const costSplitTitle = (tree: TreePayload): string => {
  const api = tree.api_cost_usd_total ?? 0
  if (!(api > 0 || tree.api_fallback)) return ''
  return `subscription $${Math.max(0, tree.cost_usd_total - api).toFixed(2)}`
    + ` · api key $${api.toFixed(2)}`
}
const USER = '@user'       // typed actor sentinels — a node may be NAMED user/system
const SYSTEM = '@system'

// the WS broadcast shapes the handler actually reads (any other event type
// only triggers the tree refetch) — cast once at the JSON.parse boundary
type WsEvent =
  | { type: 'mail'; from: string; to: string }
  | { type: 'node_stream'; node: string; kind: string; text?: string; sticky?: boolean; id?: string;
      count?: number | null; last_turn_count?: number | null; provider?: string;
      source?: string | null; reason?: string | null; emitted_at_ms?: number;
      waiting?: boolean; state?: string | null;
      forecast?: CacheForecast | null }
  | { type: 'node_event'; node: string; event: string }

export const patchMcpNode = (
  node: TreeNode, id: string,
  data: Extract<WsEvent, { type: 'node_stream' }>,
): TreeNode => {
  const children = node.children.map((c) => patchMcpNode(c, id, data))
  const childChanged = children.some((c, i) => c !== node.children[i])
  if (node.id !== id) return childChanged ? { ...node, children } : node
  return {
    ...node, children,
    mcp_tool_count: typeof data.count === 'number' ? data.count : null,
    last_turn_mcp_tool_count: typeof data.last_turn_count === 'number'
      ? data.last_turn_count : null,
    mcp_tool_count_provider: data.provider ?? node.mcp_tool_count_provider,
    mcp_tool_count_source: data.source ?? null,
    mcp_tool_count_reason: data.reason ?? null,
  }
}

export const patchCacheNode = (
  node: TreeNode, id: string, forecast: CacheForecast | null,
): TreeNode => {
  const children = node.children.map((c) => patchCacheNode(c, id, forecast))
  const childChanged = children.some((c, i) => c !== node.children[i])
  if (node.id !== id) return childChanged ? { ...node, children } : node
  return { ...node, children, cache_forecast: forecast }
}

export const patchMcpReadinessNode = (
  node: TreeNode, id: string,
  data: Extract<WsEvent, { type: 'node_stream' }>,
): TreeNode => {
  const children = node.children.map((c) => patchMcpReadinessNode(c, id, data))
  const childChanged = children.some((c, i) => c !== node.children[i])
  if (node.id !== id) return childChanged ? { ...node, children } : node
  return {
    ...node, children,
    mcp_readiness_waiting: Boolean(data.waiting),
    mcp_readiness_state: data.state ?? null,
    mcp_readiness_reason: data.reason ?? null,
  }
}

/** D-202: the usage button's tooltip named "Claude and Codex" as a literal,
 *  which is a Codex mention on a machine that has never had Codex. The bars
 *  behind it exist for exactly these two providers (Antigravity has no usage
 *  route), so the label is the shown subset of them.
 *
 *  Falls back to the bare "usage limits" rather than an empty tail if neither
 *  is present — a state that only arises with Claude itself missing, where
 *  the button is nearly moot anyway and a dangling "usage limits — " would be
 *  the more visible defect. */
export const usageTitle = (pres: ProviderPresence): string => {
  const names = [pres.claude && 'Claude', pres.openai && 'Codex']
    .filter((s): s is string => !!s)
  return names.length ? `usage limits — ${names.join(' and ')}` : 'usage limits'
}

/** The provider-neutral header summary. It deliberately walks ALL_TIERS:
 * this is an inventory of live agents, not a provider picker.
 * ⚠ D-202 DELIBERATELY LEFT THIS ALONE. It looks like a provider surface and
 * is not: `.filter((tier) => byTier[tier])` means a family appears only when
 * an agent is actually running on it, so an absent provider contributes
 * nothing without being asked. Hiding a live Codex agent's own letter because
 * the CLI went missing would make the header lie about what is running —
 * the count is an inventory, and an inventory reports what is there. */
export function ActiveAgentSummary({ tree }: { tree: TreePayload }) {
  const nodes = [...flatNodes(tree).values()].filter((n) => n.state === 'live')
  const busy = nodes.filter((n) => n.busy).length
  const byTier: Record<string, number> = {}
  for (const node of nodes) byTier[node.tier] = (byTier[node.tier] ?? 0) + 1
  return (
    <span className="chip agents"
      title="live agents · currently working · breakdown by model">
      {nodes.length} live{busy > 0 ? ` · ${busy} working` : ''}
      {/* the OpenRouter tiers are runtime-minted, so the inventory takes them
          from what is actually running rather than from a static list */}
      {[...ALL_TIERS, ...Object.keys(byTier).filter(isOpenRouterTier).sort()]
        .filter((tier) => byTier[tier])
        .map((tier) => (
          <b key={tier} className={'t-' + tier}>
            {TIER_LETTER[tier]}{byTier[tier]}
          </b>
        ))}
    </span>
  )
}

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
  const [showAccounts, setShowAccounts] = useState(false)   // D-144 account registry
  const [showUsage, setShowUsage] = useState(false)         // host subscription usage bars
  // the documents gallery (user request 2026-09-03): every presented card,
  // org-wide, one place. It reads in its OWN right-hand pane (the mail
  // idiom the user asked for), so nothing about the canvas's reader is
  // lifted up here — that panel owns its selection.
  const [showGallery, setShowGallery] = useState(false)
  const [focusAgent, setFocusAgent] = useState<string | null>(null)
  const [killArmed, setKillArmed] = useState(false)  // the killswitch latch
  // the usage button GLOWS once a lane nears its wall (user feature
  // 2026-08-19), so a freeze stops being the first notice. It rides
  // /api/usage/peek — the CACHE-ONLY readout — because this poll runs whether
  // or not the modal was ever opened, and an always-on indicator must not be
  // able to add an upstream request; the server's warm loop is what keeps
  // that cache worth reading. usePolled also wakes on the livebus, so the
  // interval is only the floor.
  const usagePeek = usePolled(BASE ? noUsagePeek : getUsagePeek, [], 60000)
  const codexUsagePeek = usePolled(BASE ? noUsagePeek : getCodexUsagePeek, [], 60000)
  // the Antigravity standing is observed from turns (a wall + its reset),
  // never fetched — the same cache-only contract, so it may ride the glow
  const agyUsagePeek = usePolled(BASE ? noUsagePeek : getAntigravityUsagePeek, [], 60000)
  // OpenRouter: a prepaid credit balance, cache-only here too — see
  // openrouter_limits's module docstring for why a plain key never earns a
  // percentage without a spend cap, which is also why this lane rarely glows
  const orrUsagePeek = usePolled(BASE ? noUsagePeek : getOpenRouterUsagePeek, [], 60000)
  const usageAlert = useMemo(
    () => usagePeak(usagePeek, codexUsagePeek, agyUsagePeek, orrUsagePeek),
    [usagePeek, codexUsagePeek, agyUsagePeek, orrUsagePeek])
  // D-202: which providers this machine actually has, for the usage button's
  // label. Polled rather than fetched once so installing a CLI mid-session is
  // picked up; unresolved is ALL_PRESENT, i.e. exactly today's wording.
  const provPresence = presenceOfPayload(
    usePolled(BASE ? noProviders : getProviders, [], 60000))
  // mobile compact orgbar (D-125 ruling 2026-08-14, 'one row, banner→chip'):
  // the detail chips + resume banner collapse behind a ⋯ toggle
  const [barMore, setBarMore] = useState(false)
  const [nowTick, setNowTick] = useState(Date.now()) // drives the resume-red clock
  // the running backend's build: a short commit + start time, so a person
  // can look at the page and confirm which deploy is actually serving —
  // fetched once, since it cannot change without a process restart (see
  // supervisor.build_info)
  const [build, setBuild] = useState<HostPayload['build'] | null>(null)
  useEffect(() => { getHost().then((h) => setBuild(h.build)).catch(() => {}) }, [])
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

  // the error banner used to have no clearer at all: a transient fetch
  // failure set it and it sat there until F5, even once polling (below)
  // had long since started succeeding again. Asymmetric on purpose — slow
  // to alarm, quick to reassure — so ONE blip doesn't flicker the banner,
  // but recovery is instant: a streak ref (not state) survives across
  // polls without a re-render of its own, and any successful fetch either
  // source makes is proof the backend is reachable again.
  const errStreak = useRef(0)
  const ERROR_STREAK = 2
  const fetchOk = useCallback(() => { errStreak.current = 0; setError(null) }, [])
  const fetchErr = useCallback((e: Error) => {
    errStreak.current += 1
    if (errStreak.current >= ERROR_STREAK) setError(e.message)
  }, [])
  const refreshOrgs = useCallback(() =>
    listOrgs().then((o) => { setOrgs(o); fetchOk() }).catch(fetchErr), [fetchOk, fetchErr])
  // G1b — ONE TREE FETCH IN FLIGHT, AND NEVER A LOST ONE.
  //
  // `refreshTree` is called from two unthrottled sources: the 6 s heartbeat
  // below, and EVERY websocket `changed` frame — i.e. every `save_org` by any
  // agent, the supervisor or another tab. Neither knew whether the last fetch
  // had come back, so a slow render multiplied itself: MEASURED 2026-09-03,
  // one `GET /api/orgs/{slug}` took 11-38 s alone and 113 s with two in
  // flight, past `DEFAULT_TIMEOUT_MS` — the "signal timed out" banner. Worse,
  // HTTP/1.1 caps a browser at ~6 sockets per origin, so a stack of stalled
  // tree polls starves everything else on the page; that is why the agent
  // CHAT stopped loading while the backend was answering chat in ~2 s.
  //
  // ⚠ COALESCE, NEVER DROP. A `changed` frame means the doc really moved, so
  // skipping its refetch would leave the UI stale against a change it was
  // told about — a new bug wearing a fix's clothes. A frame that arrives
  // mid-flight therefore sets `pending`, and the settle handler runs exactly
  // one more fetch, which starts AFTER the change landed. Any number of
  // frames during one fetch collapse into that single trailing refetch.
  const treeBusy = useRef(false)
  const treePending = useRef<string | null>(null)
  // …and the slug the app actually wants right now, for the guard below.
  // A ref rather than a dep so coalescing never re-creates this callback
  // (which would restart the heartbeat interval on every org switch).
  const wantSlug = useRef(slug)
  useEffect(() => { wantSlug.current = slug }, [slug])
  const refreshTree = useCallback((s: string | null) => {
    if (!s) return
    if (treeBusy.current) { treePending.current = s; return }
    const run = (want: string) => {
      treeBusy.current = true
      getTree(want).then((t) => {
        // ⚠ an ORG SWITCH mid-flight: this payload is the PREVIOUS org's
        // tree and painting it would show the old org under the new org's
        // header until the next poll. Not new caution — before coalescing,
        // the two fetches raced and the loser was whichever the network
        // happened to settle last, so the stale one could win. Now the
        // ordering is deterministic and the stale one is simply not applied;
        // the switch has already queued its own fetch as `pending`.
        if (wantSlug.current === want) setTree(t)
        fetchOk()
      }).catch(fetchErr).finally(() => {
        treeBusy.current = false
        const next = treePending.current
        treePending.current = null
        if (next) run(next)
      })
    }
    run(s)
  }, [fetchOk, fetchErr])

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
  // known LOCALLY and cannot be stale.
  //
  // ⚠ THIS PULL IS NOT FREE, AND THE CLAIM THAT IT WAS IS HOW IT GOT
  // EXPENSIVE. Until 2026-09-03 the line here read "the payload is ~4 KB and
  // the endpoint answers in 2-12 ms, so the pull costs nothing worth
  // counting". MEASURED that day on an org with 6 live and 179 archived
  // seats: 881 KB and 11-38 s. Nothing warned, because the assertion of
  // cheapness sat in a comment where no test could reach it — and every
  // per-node field added to the tree payload since was weighed against it.
  // The COST OF ONE RENDER IS THE BUDGET THIS HEARTBEAT SPENDS SIX TIMES A
  // MINUTE, per open tab, plus once per `save_org`: measure it before adding
  // a per-node call to `annotate`, and never do filesystem work per node
  // there. `G1b` above now bounds the damage to one in-flight fetch; it does
  // not make the render cheap.
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
    // ⚠ the USER's inbox only. Org-inbox mail is addressed to the organization
    // and answered by its agents, so counting it here billed the user for
    // someone else's correspondence — and once the tile's badge went (user
    // 2026-08-10), a tab reading "(3)" would have pointed at nothing the user
    // could find or clear.
    const n = tree?.user_inbox_count ?? 0
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
        if (data.kind === 'cache_forecast') {
          setTree((old) => old ? {
            ...old,
            roots: old.roots.map((n) => patchCacheNode(
              n, data.node, data.forecast ?? null)),
          } : old)
          return
        }
        if (data.kind === 'mcp_tool_count') {
          // Inventory is a hard-realtime process fact. Apply the websocket
          // payload directly; the next ordinary tree fetch is reconciliation,
          // not the primary update path.
          setTree((old) => old ? {
            ...old,
            roots: old.roots.map((n) => patchMcpNode(n, data.node, data)),
          } : old)
          window.dispatchEvent(new CustomEvent('orgtree:mcp-tool-count-applied', {
            detail: {
              node: data.node,
              latency_ms: typeof data.emitted_at_ms === 'number'
                ? Math.max(0, Date.now() - data.emitted_at_ms) : null,
            },
          }))
          return
        }
        if (data.kind === 'mcp_readiness') {
          setTree((old) => old ? {
            ...old,
            roots: old.roots.map((n) => patchMcpReadinessNode(
              n, data.node, data)),
          } : old)
          return
        }
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
        if (data.event === 'frozen') {   // usage-limit / network popup
          toast([`${data.node} is FROZEN (usage limit or network interruption) — the resume button in the top bar releases it once the wait passes; auto-resume handles it for you if enabled`])
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
      // the client's G2 (livebus.ts): a 'changed' means SOMEONE saved the
      // org doc — agents, the supervisor, another tab — so every mounted
      // polled surface refetches too, not just the tree
      bumpLive()
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
        {build && build.commit !== 'unknown' &&
          <span className="build-badge"
            title={`running commit ${build.commit}`
              + (build.branch ? ` (branch ${build.branch})` : '')
              + ` — started ${new Date(build.started_at).toLocaleString()}`}>
            {build.branch ? `${build.branch}@${build.commit}` : build.commit}</span>}
        <a className="gh-link h1-gh" href="https://github.com/Maurdekye/claude-orgtree"
          target="_blank" rel="noreferrer" title="orgtree on GitHub">
          <GitHubIcon fontSize="inherit" /></a>
        {!BASE &&
          <button className={'h1-usage' + (usageAlert ? ' u-' + usageAlert.sev : '')}
            title={usageAlert?.title ?? usageTitle(provPresence)}
            onClick={() => setShowUsage(true)}>
            <DataUsageIcon fontSize="inherit" /></button>}
        {/* the accounts panel (machine-local routing, 2026-08-25). Beside
            the usage bars deliberately — they answer the same question
            ("which account is paying, and how close is it to a wall?") and
            are read together. */}
        {!BASE &&
          <button className="h1-usage" title="App settings"
            onClick={() => setShowAccounts(true)}>
            <SettingsIcon fontSize="inherit" />
          </button>}</h1>
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
          {/* the tree hasn't loaded at all yet — no header, no canvas, nothing
              to shift, so the plain pre-header banner is harmless here. Once
              `tree` exists the SAME `error` string moves into the orgbar
              itself (below) instead, because that's where a connectivity
              blip after load would otherwise push the canvas down. */}
          {!tree && error && <div className="error">{error}</div>}
          {tree ? (
            <>
              <header className="orgbar">
                {!tree.public &&
                  <button className="iconbtn" onClick={() => setDrawer(true)}><MenuIcon fontSize="inherit" /></button>}
                <span className="orgname-wrap">
                  <h2>{tree.name}</h2>
                  {/* connectivity/save-error banner, relocated into the header
                      (user report 2026-09-03): it used to be a block above the
                      header and pushed the whole canvas down whenever it
                      appeared or cleared. It's `position: absolute` here on
                      purpose — anchored off the org-name's own box rather
                      than sitting as a normal flex item, so its presence,
                      absence, or message length can NEVER change the height
                      the orgbar computes (that's the actual bug: a transient
                      message must not move whatever the user is doing under
                      the canvas). A long message truncates with an ellipsis;
                      the full text is always available via `title`. */}
                  <span className={'chip bad conn-chip' + (error ? ' show' : '')}
                    title={error ?? undefined}>
                    <WarnIcon fontSize="inherit" />
                    <span className="conn-chip-text">{error}</span>
                  </span>
                </span>
                {/* MOBILE-ONLY merged status chip (D-125 orgbar ruling): live
                    count · working · frozen in one glance; tapping it opens
                    the same ⋯ panel. display:none on desktop (.mob-only). */}
                {(() => {
                  const ns = [...flatNodes(tree).values()].filter((n) => n.state === 'live')
                  const busy = ns.filter((n) => n.busy).length
                  const froz = ns.filter((n) => n.frozen).length
                  return (
                    <button className={'chip mstat mob-only' + (froz ? ' bad' : '')}
                      onClick={() => setBarMore((v) => !v)}>
                      {ns.length} live{busy > 0 ? ` · ${busy}⟳` : ''}
                      {froz > 0 ? ` · ${froz} frozen` : ''}
                    </button>
                  )
                })()}
                {/* desktop: display:contents — the chips stay direct flex
                    items of the orgbar, byte-identical layout. Compact: the
                    whole run collapses behind ⋯ (D-125 orgbar ruling; the
                    resume banner + auto-resume toggle live in here). */}
                <div className={'bar-detail' + (barMore ? ' open' : '')}>
                {/* the ledger self-audit only speaks when something is wrong;
                    credit totals live on the eye's bar */}
                {!tree.audit.no_overdraft &&
                  <span className="chip bad"><WarnIcon fontSize="inherit" /> {tree.audit.problems.join(', ')}</span>}
                <ActiveAgentSummary tree={tree} />
                {/* the bare cost chip is redundant when the kiosk spend chip
                    already shows the same figure against its limit (user
                    spec 2026-07-31) — limitless orgs keep it */}
                {tree.cost_usd_total > 0 && !tree.kiosk?.spend_limit &&
                  <span className="chip" title={costSplitTitle(tree) || 'total spend'}>
                    ${tree.cost_usd_total.toFixed(2)}</span>}
                {tree.fable_lock &&
                  <span className="chip bad" title={tree.fable_lock.at as string | undefined}><BlockIcon fontSize="inherit" /> fable limit</span>}
                {tree.kiosk?.spend_limit && (
                  tree.spend_frozen
                    ? <span className="chip bad"><BlockIcon fontSize="inherit" /> spend limit reached — agents frozen</span>
                    : <span className={'chip' + (tree.cost_usd_total >= tree.kiosk.spend_limit * 0.9 ? ' bad' : '')}
                        title={'spend / limit'
                          + (costSplitTitle(tree) ? ' — ' + costSplitTitle(tree) : '')}>
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
                {tree.headless && (
                  <span className="chip"
                    title="headless: no user is present — user-bound requests auto-deny; the eye renders grey and empty">
                    <EyeIcon fontSize="inherit" /> headless
                  </span>
                )}
                {/* FR-27 (user spec 2026-08-27): "some visual indication
                    somewhere that a prime is active and will trigger the next
                    moment the system quiesces".
                    ⚠ IT SHOWS IN EVERY ORG, and that is the point rather than
                    a side effect: the record is machine-wide because the
                    restart is machine-wide, so the org that gets cut without
                    having armed anything is exactly the one that most needs
                    the warning. The words live in `primedRestartChip` —
                    see the note there for why they are not inline. */}
                {(() => {
                  const pc = primedRestartChip(tree.primed_restart)
                  if (!pc) return null
                  return (
                    <span className="chip primed" title={pc.title}>
                      <AutorenewIcon fontSize="inherit" /> {pc.label}
                    </span>
                  )
                })()}
                {(() => {   // F-06: hub connectivity chip (enabled, NON-hidden
                  // hubs — a local hub that never answered shows no UI at all,
                  // user ruling 2026-08-05)
                  const hubs = (tree.net?.hubs ?? [])
                    .filter((h) => h.enabled && !h.hidden)
                  if (!hubs.length) return null
                  const up = hubs.filter((h) => h.connected).length
                  const queued = hubs.reduce((a, h) => a + h.queued, 0)
                  const label = hubs.length === 1
                    ? (hubs[0]?.name || 'hub') : `${up}/${hubs.length} hubs`
                  return (
                    <span className={'chip' + (up === 0 ? ' bad' : '')}
                      title={hubs.map((h) =>
                        `${h.name || h.address}: ${h.connected ? 'connected'
                          : h.error || 'connecting…'}`).join(' · ')}>
                      <LanIcon fontSize="inherit" /> {label}
                      {up === 0 ? ': offline' : ''}
                      {queued > 0 ? ` · ${queued} queued` : ''}
                    </span>
                  )
                })()}
                {(() => {   // usage-limit freeze: ▶ restarts every frozen agent
                  if (tree.spend_frozen) return null
                  // ⚠ resumableFrozen, NOT every node carrying a record: a
                  // retired agent keeps its freeze and ▶ has never resumed it
                  const frozen = resumableFrozen(tree)
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
                      {/* ⚠ this line is the ONLY place that knows both the
                          freeze kind and whether anything will act on it, so
                          it is the only place allowed to say. D-122 (user
                          ruling 2026-08-14): a pure connection freeze always
                          retries itself, so the promise is unconditional now
                          — the toggle governs only limit freezes. A record
                          carrying BOTH flags falls to the limit branch: its
                          wake waits on the toggle like any limit's. */}
                      {/* ⚠ THE LABEL MUST DESCRIBE THE SET THE COUNT COUNTS.
                          `resumable` means "▶ will act on this", NOT "this is
                          waiting on capacity", and D-156 pulls those apart: an
                          AUTH freeze (rejected credential, not spent capacity)
                          stays resumable on purpose — replacing the credential
                          and pressing ▶ IS the fix. It is correctly counted;
                          it was the WORDS that over-claimed, telling the
                          operator to wait for capacity while a credential was
                          what was broken.
                          A cause is named only when the WHOLE set shares one.
                          A mixed set says the count and the action and stops,
                          because there is no honest single cause for it — an
                          under-informative line beats a confident wrong one.
                          (No spend branch here on purpose: this block already
                          returned early on `tree.spend_frozen`, which
                          `hard_freeze` always writes alongside the per-node
                          flag. The node BADGES need that branch; this does
                          not, and inventing one would be dead code pretending
                          to be a safeguard.) */}
                      <span className="resume-note">
                        {frozen.every((n) => freezeKind(n.frozen) === 'connection')
                          ? <>network interruption — {frozen.length} agent
                            {frozen.length > 1 ? 's' : ''} frozen
                            {until ? ` · ${until.replace(/^network interruption — /, '')}` : ''}
                            {' · retrying automatically'}</>
                          : frozen.every((n) => freezeKind(n.frozen) === 'auth')
                          ? <>credential rejected — {frozen.length} agent
                            {frozen.length > 1 ? 's' : ''} frozen
                            {' · replace it, then ▶ to resume'}</>
                          : frozen.some((n) => freezeKind(n.frozen) === 'auth')
                          ? <>{frozen.length} agent{frozen.length > 1 ? 's' : ''} frozen
                            {' · ▶ to resume'}</>
                          : <>usage limit hit — {frozen.length} agent
                            {frozen.length > 1 ? 's' : ''} frozen
                            {/* ⚠ NO VERB — the backend re-derives this from
                                the live roster and it already reads as a
                                sentence. "resumable <time>" outlived its
                                truth: with auto_resume off (the default)
                                nothing resumes on its own. */}
                            {until ? ` · ${until}` : ''}</>}
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
                {/* compact ⋯ panel extras — desktop hides these (.mob-only);
                    the real settings/kill controls sit right of the spacer
                    and are display:none at compact */}
                {!tree.public &&
                  <button className="mob-only bar-row"
                    onClick={() => { setBarMore(false); setShowSettings(true) }}>
                    <SettingsIcon fontSize="inherit" /> settings</button>}
                <span className="kill mob-only">
                  <button className={'kill-latch' + (killArmed ? ' open' : '')}
                    onClick={() => setKillArmed((a) => !a)}>
                    {killArmed ? <LockOpenIcon fontSize="inherit" /> : <LockIcon fontSize="inherit" />}</button>
                  <button className="kill-btn" disabled={!killArmed}
                    onClick={() => {
                      setKillArmed(false); setBarMore(false)
                      killAll(slug)
                        .then((r) => { toast([`interrupted ${r.interrupted.length} agent(s); queues cleared`]); refreshTree(slug) })
                        .catch((e: Error) => toast([`error: ${e.message}`]))
                    }}><StopIcon fontSize="inherit" /> STOP ALL</button>
                </span>
                </div>
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
                    or credit request) is waiting on the user. Two-tier badge
                    (user spec 2026-08-06, supersedes the 2026-08-05 full-total
                    ruling): with asks open the badge shows the ASK count in
                    the vibrant pulsing form; otherwise the unread-mail count,
                    muted. Click opens the inbox either way. */}
                {(() => {
                  // D-169: the rule is `attentionPip` now — one classifier,
                  // four surfaces. The 2026-08-04 ruling that this bell glows
                  // ALONE in the whole chrome is untouched; the user widened
                  // WHAT counts (urgent mail joins open asks), not the
                  // property. Nothing else in the chrome starts glowing.
                  const pip = attentionPip(tree)
                  return (
                    <button className={'iconbtn ask-bell' + (pip?.urgent ? ' glow' : '')}
                      title={pip?.title ?? 'your inbox'}
                      onClick={() => { setInboxJump(null); setShowInbox(true) }}>
                      <MailIcon fontSize="inherit" />
                      {pip && <b className={'eye-count' + (pip.urgent ? ' asks' : '')}>
                        {pip.count}</b>}
                    </button>
                  )
                })()}
                {/* the presented-document gallery sits BESIDE the inbox (user
                    ruling 2026-09-03: "place it next to the mail icon"). They
                    are the same kind of thing — a standing pile of what agents
                    sent you, read in the same list-plus-pane panel — so they
                    read as one pair of mailbox controls rather than two
                    unrelated buttons. */}
                {(() => {
                  // the corner count is the mail bell's own badge (.eye-count
                  // in a position:relative button), carrying the number of
                  // documents presented by CURRENTLY HIRED agents — the set
                  // the panel shows with "show retired agents" unticked. It
                  // never wears the `.asks` pulse: nothing here is waiting on
                  // an answer, and the 2026-08-04 ruling leaves the bell the
                  // only glowing thing in the chrome.
                  const docs = activeDocCount(tree.roots)
                  return (
                    <button className="iconbtn doc-bell"
                      title={docs > 0
                        ? `presented documents — ${docs} from currently-hired agents`
                        : 'presented documents'}
                      onClick={() => setShowGallery(true)}>
                      <DocIcon fontSize="inherit" />
                      {docs > 0 && <b className="eye-count">{docs}</b>}
                    </button>
                  )
                })()}
                <button className="iconbtn barmore mob-only" title="more"
                  onClick={() => setBarMore((v) => !v)}>⋯</button>
                {/* host subscription usage (the Claude Code /usage bars) —
                    the host account's own standing, so admin only: a kiosk
                    visitor neither sees the button nor could call the
                    endpoint (the public gateway 404s it) */}
                {!tree.public &&
                  <button className={'iconbtn' + (usageAlert ? ' u-' + usageAlert.sev : '')}
                    title={usageAlert?.title ?? usageTitle(provPresence)}
                    onClick={() => setShowUsage(true)}>
                    <DataUsageIcon fontSize="inherit" /></button>}
                {!tree.public &&
                  <button onClick={() => setShowSettings(true)}><SettingsIcon fontSize="inherit" /> settings</button>}
                <a className="gh-link" href="https://github.com/Maurdekye/claude-orgtree"
                  target="_blank" rel="noreferrer" title="orgtree on GitHub">
                  <GitHubIcon fontSize="inherit" /></a>
              </header>
              <OrgCanvas tree={tree} op={op} slug={slug} toast={toast}
                mailEvt={mailEvt}
                focusAgent={focusAgent}
                onFocusAgentHandled={() => setFocusAgent(null)}
                onAccounts={BASE ? undefined : () => setShowAccounts(true)}
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
      {showUsage && (
        <UsageModal close={() => setShowUsage(false)} />
      )}
      {showGallery && slug && (
        <DocGalleryModal slug={slug} toast={toast}
          onFocusAgent={(id) => {
            setShowGallery(false)
            setFocusAgent(id)
          }}
          close={() => setShowGallery(false)} />
      )}
      {showAccounts && (
        <AccountsPanel toast={toast} close={() => setShowAccounts(false)} />
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
function AdvancedOrgModal({ title, close, children, tabs }: {
  title: string
  close: () => void
  children?: ReactNode
  /** tabbed form (user amendment 2026-08-05): categories as a tab strip —
   *  presentation only; both callers keep their own save flow */
  tabs?: { label: string; content: ReactNode }[]
}) {
  useEsc(close)
  const [tab, setTab] = useState(0)
  return (
    <div className="overlay" onClick={(e) => { e.stopPropagation(); close() }}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> {title} — advanced</h3>
        {tabs && (
          <div className="adv-tabs">
            {tabs.map((t, i) => (
              <button key={t.label} type="button"
                className={'adv-tab' + (i === tab ? ' on' : '')}
                onClick={() => setTab(i)}>{t.label}</button>
            ))}
          </div>
        )}
        {tabs ? tabs[Math.min(tab, tabs.length - 1)]?.content : children}
        <div className="row">
          <button className="primary" type="button" onClick={close}>done</button>
        </div>
      </div>
    </div>
  )
}

/** the header usage modal: the host subscription's rate-limit bars — the
 *  same session / weekly / weekly-scoped readout Claude Code shows under
 *  /usage (user feature 2026-08-18). The backend proxies the account usage
 *  endpoint with the host OAuth token and caches ~30 s; this panel rides
 *  usePolled, so it is fresh on open and stays live while it sits there.
 *  Bars render generically from the `limits` array rather than three
 *  hardcoded rows: when the account gains or loses a scoped limit (a new
 *  model bucket), it shows up here with no code change. */
const USAGE_LABEL: Record<string, string> = {
  session: 'session (5hr)',
  weekly_all: 'weekly (7 day)',
}

const usageLabel = (l: UsageLimit): string =>
  l.label || (l.kind === 'weekly_scoped' && l.model ? `weekly ${l.model}`
    : USAGE_LABEL[l.kind] ?? l.kind.replace(/_/g, ' '))

const usageResets = (iso: string | null): string => {
  if (!iso) return ''
  const ms = new Date(iso).getTime() - Date.now()
  if (!Number.isFinite(ms)) return ''
  if (ms <= 0) return 'resets soon'
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  if (h >= 48) return `resets in ${Math.floor(h / 24)}d ${h % 24}h`
  return h > 0 ? `resets in ${h}h ${m}m` : `resets in ${m}m`
}

/** the ONE severity rule, shared by the modal's bars and the header button's
 *  glow. Severity comes straight from upstream when it speaks; the percent
 *  thresholds are the fallback so an older shape still colors. Shared because
 *  two readers of one standing that disagreed about what "near the wall"
 *  means would be a bug you could only see by opening the modal to check the
 *  button against it. */
export const usageSeverity = (l: UsageLimit): '' | 'warn' | 'crit' => {
  const pct = Math.max(0, Math.min(100, l.percent ?? 0))
  return l.severity === 'critical' || pct >= 90 ? 'crit'
    : (l.severity && l.severity !== 'normal') || pct >= 75 ? 'warn' : ''
}

/** the worst lane in a peek, or null when nothing warrants a glow (user
 *  feature 2026-08-19). The button wears the PEAK because a wall is a wall:
 *  whichever lane arrives first is the one that freezes an agent, and the
 *  breakdown is one click away. Ties break on percent so the tooltip names
 *  the lane actually closest to it. */
export const usagePeak = (...readouts: (UsagePeek | null)[]):
{ sev: 'warn' | 'crit'; title: string } | null => {
  let best: { sev: 'warn' | 'crit'; l: UsageLimit; provider: string } | null = null
  for (const u of readouts) {
    if (!u?.available) continue
    for (const l of u.limits ?? []) {
      const sev = usageSeverity(l)
      if (!sev) continue
      if (!best || (sev === 'crit' && best.sev === 'warn')
        || (sev === best.sev && (l.percent ?? 0) > (best.l.percent ?? 0))) {
        best = { sev, l, provider: u.provider ?? 'Claude' }
      }
    }
  }
  if (!best) return null
  const r = usageResets(best.l.resets_at)
  const source = best.provider === 'Claude'
    ? 'Claude subscription usage' : `${best.provider} usage`
  return {
    sev: best.sev,
    title: `${usageLabel(best.l)} at ${Math.round(best.l.percent ?? 0)}%`
      + (r ? ` · ${r}` : '') + ` — ${source}`,
  }
}

/** a kiosk visitor has no usage button and no claim on the host account's
 *  standing: the poll is not merely hidden, it is never issued. One frozen
 *  object, not a fresh literal per tick — `usePolled` stores what it is
 *  handed, and a new object every 60 s would re-render the whole app to say
 *  the same nothing. */
const NO_PEEK: UsagePeek = Object.freeze({ available: false })
const noUsagePeek = (): Promise<UsagePeek> => Promise.resolve(NO_PEEK)
// D-202: same shape for /api/providers — a kiosk gateway does not serve it,
// so don't poll a 404 every minute. An EMPTY provider list, not a rejection:
// `presenceOfPayload` reads that as all-present, which is the right answer
// for a kiosk (it hires from the host's own harnesses, and the surfaces this
// gates are admin-only and unrendered there anyway).
const noProviders = (): Promise<ProvidersPayload> =>
  Promise.resolve({ providers: [] })

export function UsageModal({ close }: { close: () => void }) {
  useEsc(close)
  // ⚠ EVERY registered account, primary first then fallbacks in priority
  // order (user ruling 2026-08-25) — one section of bars per account. The
  // bar markup itself lives in UsageBars (canvas/accounts.tsx) so this modal
  // and the panel's per-row buttons cannot drift apart.
  const all = usePolled(getUsageAll, [], 60000)
  const codex = usePolled(getCodexUsage, [], 60000)
  // Antigravity: the last wall a turn hit and its parsed reset (the CLI
  // publishes no readout — see antigravity_limits); with no wall on record
  // the section carries the settled `unsupported` note, not an error
  const agy = usePolled(getAntigravityUsage, [], 60000)
  // OpenRouter: a prepaid credit balance read off the stored key, not a
  // subscription lane — see openrouter_limits's module docstring. `fetch`
  // answers `{available:false, error:"no API key…"}` rather than nothing
  // when no key is stored, same shape as the other providers' "not
  // installed" case, so it degrades through the same `shown.openrouter &&`
  // gate below rather than a bespoke branch.
  const orr = usePolled(getOpenRouterUsage, [], 60000)
  // D-202. ⚠ `codex` IS TRUTHY ON A MACHINE WITH NO CODEX — measured, not
  // assumed: codex_limits.fetch returns {available:false, error:"Codex CLI is
  // not installed"} rather than nothing, so the bare `codex &&` gate below
  // rendered a "Codex" heading over that error. It was the app's clearest
  // remaining "you could have Codex" advertisement, and on a Codex-less
  // machine the whole block is now absent instead.
  const shown = presenceOfPayload(usePolled(getProviders, [], 60000))
  return (
    <div className="overlay" onClick={(e) => { e.stopPropagation(); close() }}>
      <div className="settings usage-modal" onClick={(e) => e.stopPropagation()}>
        <h3><DataUsageIcon fontSize="inherit" /> usage limits</h3>
        {/* the codex half only counts toward "still loading" while it is a
            half this machine has — otherwise a Codex-less box would skip the
            spinner and show a blank modal until the Claude bars land */}
        {!all && !(shown.openai && codex) && !(shown.google && agy)
          && !(shown.openrouter && orr)
          ? <div className="dim">loading…</div>
          : <>
          {(all?.accounts ?? []).map((a) => (
            <div className="usage-acct" key={a.account}>
              <div className="usage-acct-head">
                <span className="acct-label">{a.label}</span>
                {a.account === 'primary' &&
                  <span className="dim"> · this machine's login</span>}
              </div>
              <UsageBars u={a} />
            </div>
          ))}
          {shown.openai && codex && <div className="usage-acct" key={codex.account}>
            <div className="usage-acct-head">
              <span className="acct-label">{codex.provider ?? 'Codex'}</span>
              <span className="dim"> · {codex.label}</span>
            </div>
            <UsageBars u={codex} />
          </div>}
          {shown.google && agy && <div className="usage-acct" key={agy.account}>
            <div className="usage-acct-head">
              <span className="acct-label">{agy.provider ?? 'Antigravity'}</span>
              <span className="dim"> · {agy.label}</span>
            </div>
            <UsageBars u={agy} />
          </div>}
          {shown.openrouter && orr && <div className="usage-acct" key={orr.account}>
            <div className="usage-acct-head">
              <span className="acct-label">{orr.provider ?? 'OpenRouter'}</span>
              <span className="dim"> · {orr.label}</span>
            </div>
            <UsageBars u={orr} />
          </div>}
          </>}
        {all && !(all.accounts ?? []).length &&
          <div className="dim">no accounts registered</div>}
        <div className="row">
          <button className="primary" type="button" onClick={close}>done</button>
        </div>
      </div>
    </div>
  )
}

function NewOrg({ onCreate }: {
  onCreate: (name: string, dirs: string[], kiosk: KioskSpecRequest | null,
             sandbox: boolean, diskMb: number | null,
             netAuto: boolean, netHubs: string[]) => void
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
  // F-06: local-hub auto-connect defaults ON (ruled); detection is a HINT
  // beside the box, never a gate — a hub that is down still gets configured
  const [netAuto, setNetAuto] = useState(true)
  const [netHubs, setNetHubs] = useState<string[]>([])
  const [hubSeen, setHubSeen] = useState<{ ok: boolean; name?: string | null } | null>(null)
  useEffect(() => {
    getHost().then((h) => setDocker(!!h.docker)).catch(() => {})
  }, [])
  useEffect(() => {
    if (advanced && hubSeen == null) {
      probeHub().then(setHubSeen).catch(() => setHubSeen({ ok: false }))
    }
  }, [advanced, hubSeen])
  const reset = () => {
    setOpen(false); setAdvanced(false); setName(''); setDirs([])
    setKiosk(false); setSandboxed(false); setNetAuto(true); setNetHubs([])
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
        sandboxed && !kiosk ? Math.max(4096, +storage || 4096) : null,
        netAuto, netHubs.map((s) => s.trim()).filter(Boolean))
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
        {(kiosk || sandboxed || dirs.length > 0 || netAuto || netHubs.length > 0) && (
          <span className="dim adv-sum"> · {[
            kiosk ? 'kiosk' : '', sandboxed ? 'sandboxed' : '',
            dirs.length ? `${dirs.length} folder${dirs.length > 1 ? 's' : ''}` : '',
            netAuto || netHubs.length ? 'hub' : '',
          ].filter(Boolean).join(' · ')}</span>)}
      </button>
      {advanced && (
        <AdvancedOrgModal title={name.trim() || 'new organization'}
          close={() => setAdvanced(false)}
          tabs={[
            { label: 'general', content: (
              <>
                <div className="field-label">also grant existing folders</div>
                <DirList dirs={dirs} onChange={setDirs} />
              </>
            ) },
            { label: 'org type', content: (
              <OrgTypeTab />
            ) },
            { label: 'mailserver', content: (
              <>
                <label className="row kiosk-sbx"
                  title="being listed means peers can mail this org (and thereby spend its credits) — refusable here, at creation">
                  <input type="checkbox" checked={netAuto && !kiosk}
                    disabled={kiosk}
                    onChange={(e) => setNetAuto(e.target.checked)} />
                  connect to the mailserver on this computer
                  {kiosk && <span className="dim"> (kiosks are sealed — no mail identity)</span>}
                </label>
                {!kiosk && <div className="dim hub-hint">
                  {hubSeen == null ? 'checking for a local hub…'
                    : hubSeen.ok
                      ? `detected: ${hubSeen.name || 'unnamed hub'}`
                      : 'not running right now — the org will connect when it starts'}
                </div>}
                {!kiosk && (
                  <>
                    <div className="field-label adv-sep">remote mailservers</div>
                    {netHubs.map((h, i) => (
                      <div className="row" key={i}>
                        <input style={{ flex: 1 }} placeholder="http://host:7370"
                          value={h} onChange={(e) => setNetHubs(
                            (l) => l.map((x, j) => (j === i ? e.target.value : x)))} />
                        <button type="button" onClick={() => setNetHubs(
                          (l) => l.filter((_, j) => j !== i))}>
                          <CloseIcon fontSize="inherit" /></button>
                      </div>
                    ))}
                    <button type="button" onClick={() => setNetHubs((l) => [...l, ''])}>
                      + add a remote mailserver address</button>
                    <div className="dim hub-hint">names are discovered on
                      connect — only the address is typed</div>
                  </>
                )}
              </>
            ) },
          ]} />
      )}
      <div className="row">
        <button type="submit" className="primary">create</button>
        <button type="button" onClick={reset}>cancel</button>
      </div>
    </form>
  )

  // the org-type tab body — extracted so the tab array above stays readable;
  // closes over the form state (kiosk/sandbox/caps/ceiling)
  function OrgTypeTab() {
    return (
      <>
        {/* kiosk and sandbox live here (user ruling 2026-08-03): both are
            advanced choices — one publishes the org, the other changes where
            every turn executes — and neither belongs in the two-field path
            most new orgs take. */}
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
                <option value="plan">plan (read-only)</option>
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
      </>
    )
  }
}

/** F-06: the settings modal's mailserver tab. Deliberately saves IMMEDIATELY
 *  (the auto-resume header-toggle precedent) — hub membership is operational
 *  state, not a form draft; the footer says so. */
function NetTab({ tree, toast, adding, setAdding }: {
  tree: TreePayload
  toast: ToastFn
  adding: string
  setAdding: (value: string) => void
}) {
  const hubs = tree.net?.hubs ?? []
  const [reveal, setReveal] = useState<string | null>(null)
  const apply = (next: { id?: string; address: string; enabled?: boolean }[],
                 note: string) =>
    saveSettings(tree.slug, { net_hubs: next })
      .then((r) => toast(r.warnings?.length ? r.warnings : [note]))
      .catch((e: Error) => toast([`error: ${e.message}`]))
  return (
    <>
      <div className="field-label">this org's network address</div>
      <div className="row" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="badge dim mono-sm">{tree.net?.slug ?? '—'}</span>
        {reveal == null
          ? <button onClick={() => getOrgNet(tree.slug)
              .then((r) => setReveal(r.identity?.secret ?? '(none)'))
              .catch((e: Error) => toast([`error: ${e.message}`]))}>
              reveal secret…</button>
          : <>
              <span className="badge dim mono-sm">{reveal}</span>
              <button onClick={() => { navigator.clipboard?.writeText(reveal)
                .catch(() => {}); toast(['secret copied']) }}>
                <CopyIcon fontSize="inherit" /></button>
            </>}
      </div>
      <div className="dim hub-hint">the secret IS the address's ownership —
        losing it loses the address; nobody can restore it. It never reaches
        an agent.</div>
      <label className="checkline"
        title="being listed means peers can mail this org (and thereby spend its credits)">
        <input type="checkbox"
          checked={hubs.some((h) => h.id === 'local')}
          onChange={(e) => saveSettings(tree.slug,
            { net_autoconnect: e.target.checked })
            .then((r) => toast(r.warnings?.length ? r.warnings
              : [e.target.checked ? 'local hub joined' : 'local hub left']))
            .catch((err: Error) => toast([`error: ${err.message}`]))} />
        connect to the mailserver on this computer
      </label>
      <div className="field-label">mailservers</div>
      {hubs.map((h) => (
        <div className="row" key={h.id} style={{ alignItems: 'center' }}>
          <span className={'oi-dot' + (h.connected ? ' ok' : '')} />
          <b>{h.name || (h.id === 'local' ? 'local hub' : 'unnamed')}</b>
          <span className="dim mono-sm" style={{ flex: 1 }}>{h.address}</span>
          <span className="dim" style={{ fontSize: '11px' }}>
            {h.connected ? 'connected'
              : h.enabled ? (h.error ? `retrying — ${h.error}` : 'connecting…')
                : 'disabled'}
            {h.queued > 0 ? ` · ${h.queued} queued` : ''}
          </span>
          <label className="checkline" style={{ margin: 0 }}>
            <input type="checkbox" checked={h.enabled}
              onChange={(e) => apply(hubs.map((x) => ({ id: x.id,
                address: x.address, enabled: x.id === h.id
                  ? e.target.checked : x.enabled })),
                e.target.checked ? `${h.name || h.address} enabled`
                  : `${h.name || h.address} disabled`)} />
            on
          </label>
          <button title="remove this mailserver"
            onClick={() => apply(hubs.filter((x) => x.id !== h.id)
              .map((x) => ({ id: x.id, address: x.address,
                             enabled: x.enabled })),
              `${h.name || h.address} removed`)}>
            <CloseIcon fontSize="inherit" /></button>
        </div>
      ))}
      <div className="row">
        <input style={{ flex: 1 }} placeholder="http://host:7370 — add a remote mailserver"
          value={adding} onChange={(e) => setAdding(e.target.value)} />
        <button disabled={!adding.trim()}
          onClick={() => { apply([...hubs.map((x) => ({ id: x.id,
            address: x.address, enabled: x.enabled })),
            { address: adding.trim(), enabled: true }], 'mailserver added')
            setAdding('') }}>add</button>
      </div>
      <div className="dim" style={{ fontSize: '11.5px' }}>
        mailserver changes apply immediately (names are discovered on connect)
      </div>
    </>
  )
}

/** §9.5/§9.6: per-org API key + headless mode. Saves IMMEDIATELY (the
 *  couplings are server-enforced 422s — instant feedback beats a buffered
 *  save that fails later). */
function AutonomyTab({ tree, toast, keyDraft, setKeyDraft }: {
  tree: TreePayload
  toast: ToastFn
  keyDraft: string
  setKeyDraft: (value: string) => void
}) {
  const save = (opts: Parameters<typeof saveSettings>[1], note: string) =>
    saveSettings(tree.slug, opts)
      .then((r) => toast(r.warnings?.length ? r.warnings : [note]))
      .catch((e: Error) => toast([`error: ${e.message}`]))
  return (
    <>
      <div className="field-label">API key (§9.5 — the org's own metered
        billing)</div>
      <div className="row">
        {tree.api_key_set
          ? <>
              <span className="badge free">{tree.api_fallback
                ? 'key set — held as the usage-limit fallback'
                : 'key set — turns bill the key'}</span>
              <button onClick={() => save({ clear_api_key: true },
                'API key cleared')}>clear</button>
            </>
          : <>
              <input style={{ flex: 1 }} type="password"
                placeholder="sk-ant-… (stored server-side, never shown again)"
                value={keyDraft} onChange={(e) => setKeyDraft(e.target.value)} />
              <button disabled={!keyDraft.trim()}
                onClick={() => { save({ api_key: keyDraft.trim() }, 'API key set')
                  setKeyDraft('') }}>set</button>
            </>}
      </div>
      <div className="dim hub-hint">an API key removes the subscription's
        refresh-token ceiling — required for headless, useful for any
        unattended org; it never reaches an agent's context</div>
      {/* api_fallback (2026-08-17): subscription-first billing with the key
          as the spare lane while a usage limit holds; auto-reverts at the
          limit's own reset (server-enforced couplings: needs a key, mutually
          exclusive with headless) */}
      {tree.api_key_set && <label className="checkline"
        title="turns bill the subscription; when a usage limit freezes an agent, the org switches to the key and the agent resumes at once — reverting to the subscription when the limit's reset time passes">
        <input type="checkbox" checked={!!tree.api_fallback}
          onChange={(e) => save({ api_fallback: e.target.checked },
            e.target.checked
              ? 'fallback ON — the key takes over only during usage limits'
              : 'fallback off — the key bills every turn again')} />
        use the key only as a usage-limit fallback
      </label>}
      {fallbackActive(tree)
        && <div className="dim hub-hint">fallback ACTIVE — billing the key
          until {new Date((tree.api_fallback_until ?? 0) * 1000)
            .toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })},
          then back to the subscription</div>}
      {/* fable_api_fallback (2026-08-23): off by default — a fable-tier
          weekly hit normally goes to fable_limit_policy (halt/opus/dissolve)
          instead, untouched by the toggle above. This rides the SAME window;
          it opens no lane of its own, so it only makes sense once the
          fallback itself is on. */}
      {tree.api_fallback && <label className="checkline"
        title="by default a weekly Fable-tier limit is excluded from the fallback above and goes to the fable weekly-limit policy (general tab) instead; this extends the same key-billing window to cover it too">
        <input type="checkbox" checked={!!tree.fable_api_fallback}
          onChange={(e) => save({ fable_api_fallback: e.target.checked },
            e.target.checked
              ? 'fable-tier fallback ON — a weekly Fable limit now bills the key too'
              : 'fable-tier fallback off — a weekly Fable limit goes back to the fable policy')} />
        also cover the weekly Fable-tier limit with the same fallback
      </label>}
      <label className="checkline"
        title="no user is present: questions, credit requests and user audiences auto-deny; mail to you is stored with a no-reply note; requires an API key and non-halt fable policies">
        <input type="checkbox" checked={!!tree.headless}
          onChange={(e) => save({ headless: e.target.checked },
            e.target.checked ? 'headless ON — nobody is watching now'
              : 'headless off')} />
        headless — this org runs with no user present
      </label>
      {tree.headless && <div className="dim hub-hint">the overseer renders
        grey with an empty eye while headless is on</div>}
      <div className="dim" style={{ fontSize: '11.5px' }}>
        autonomy changes apply immediately
      </div>
    </>
  )
}

function flatNodes(tree: TreePayload): Map<string, TreeNode> {
  const map = new Map<string, TreeNode>()
  const walk = (n: TreeNode) => { map.set(n.id, n); n.children.forEach(walk) }
  tree.roots.forEach(walk)
  return map
}

/** The nodes ▶ resume will ACTUALLY act on — the banner's count, its title
 *  list and its wording all read from this and nothing else.
 *
 *  user report 2026-08-26: the banner read "resume 2 · 2 agents frozen" in an
 *  org whose two frozen agents had since been RETIRED. Retiring does not clear
 *  the freeze record — deliberately, since a retired agent keeps its context
 *  and can be rehired — so the old test (`n.frozen != null` alone) counted
 *  nodes ▶ has never been willing to touch. Nothing behind the banner was
 *  broken: the backend already refused them and ▶ resumed nobody.
 *
 *  ⚠ THE RULE IS NOT HERE, AND MUST NOT COME BACK HERE. `node.resumable` is
 *  composed by the backend from `supervisor.resumable`, which is the single
 *  expression of it. The first fix re-derived the rule in this file and held
 *  the two copies together with a test that read `supervisor.py` as text —
 *  and a source-text check cannot tell a rule that got STRONGER from one that
 *  got weaker, fires on a harmless rename, and misses a semantic change that
 *  keeps the same spelling. If you find yourself adding a condition below,
 *  the condition belongs in `_resumable` instead.
 *
 *  The `frozen != null` test is a TYPE NARROWING, not a second copy of the
 *  rule: `resumable` is only ever true for a node carrying a record, and the
 *  banner dereferences `.frozen.until` — this is how TypeScript is told. */
export function resumableFrozen(
  tree: TreePayload,
): (TreeNode & { frozen: TreeFrozen })[] {
  return [...flatNodes(tree).values()].filter(
    (n): n is TreeNode & { frozen: TreeFrozen } =>
      n.resumable && n.frozen != null)
}

function SenderChip({ id, nodes }: { id: string; nodes: Map<string, TreeNode> }) {
  if (id === SYSTEM || id === 'system') return <b className="dim">system</b>
  if (id === USER) return <b>you</b>
  const n = nodes.get(id)
  if (!n) return <b>{id}</b>
  return (
    <span className={'sender ' + n.state} title={`${tierLabel(n.tier)} · ${n.state}`}>
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
  //
  // ⚠ `readBump` is what makes marking-read FEEL instant (user bug 2026-08-07:
  // "takes several seconds to process"). The POST answers in ~5 ms; the delay
  // was entirely here. These rows come from getInbox, but onRead refreshed the
  // TREE — a different payload that does not carry them — so the row kept its
  // unread mark until the next 5 s poll tick: 0–5 s, ~2.5 s typical. Bumping a
  // dep restarts the effect, which ticks immediately. No optimistic local
  // state: the server answer still decides, it is just asked for now.
  const [readBump, setReadBump] = useState(0)
  // readBump rides the REFRESH key, not deps: deps changes reset the value to
  // null (identity changed — §6.10), and blanking the inbox on every
  // mark-read would regress the instant-ack this bump exists to provide
  const box = usePolled(() => getInbox(slug), [slug], 5000, readBump)
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
  const audBadge = (g: string, dim = false) => (
    <span key={g} className={'badge ' + (dim ? 'dim' : 'free')}>
      <HearingIcon fontSize="inherit" /> {g}
      <button className="chip-x" title="rescind"
        onClick={() => act('revoke', g)}><CloseIcon fontSize="inherit" /></button>
    </span>
  )
  // Asks ride the inbox as their OWN mail rows (user ruling 2026-08-04),
  // interleaved chronologically with real mail — the only difference is the
  // reading pane shows the response UI as the body instead of a reply box.
  // Open asks join the unread group; resolved ones sit in the flow wearing
  // their nulled state (grey answered/denied, orange interrupted).
  const askRow = (a: AskInfo): MailRow => ({
    id: 'ask:' + a.id, from: a.node, at: a.at,
    kind: a.kind === 'batch' ? 'request batch'
      : (a.kind === 'credit' || a.old != null) ? 'credit request'
      : a.kind === 'scope' ? 'scope request' : 'question',
    body: a.kind === 'batch'
      ? `${(a.tabs ?? []).length} request(s) awaiting one submit`
      : a.kind === 'scope'
        ? 'requests scope: ' + (a.items ?? [])
          .map((it) => it.kind === 'dir' ? it.path
            : it.kind === 'permission_mode' ? `mode ${it.mode}`
            : it.tool ?? it.server ?? it.kind).join(', ')
        : a.question ?? `asks for credits: ${a.old} → ${a.new}`,
    _ask: a,
  } as MailRow)
  const askOpen = (a: AskInfo) => a.status === 'open' || a.status === 'pending'
  const asks = tree.asks ?? []
  // FR-14: an agent's OPEN requests render as its ONE composed batch card
  // (node.ask, kind 'batch') — never as separate per-kind rows. The raw
  // per-store entries keep feeding the resolved history below.
  const askPending = [...nodes.values()]
    .filter((n) => n.ask && askOpen(n.ask)).map((n) => askRow(n.ask!))
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
              <AudienceFold
                ids={userAud.filter((a) => nodes.get(a.grantee)?.state === 'live')
                  .map((a) => a.grantee)}
                label="audience holders"
                render={(g) => audBadge(g)} />
              <RetiredFold
                ids={userAud.filter((a) =>
                  nodes.get(a.grantee)?.state !== 'live').map((a) => a.grantee)}
                render={(g) => audBadge(g, true)} />
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
                  // FR-21: this was the ONE MailList call site without
                  // fileHref, which is why the node inbox's attachments were
                  // downloadable and the user's were not. Keyed on the
                  // SENDER — the file sits in that agent's own outbox/.
                  fileHref={(p, m) => fileUrl(slug, m.from, p)}
                  mdBase={(m) => fileBase(slug, m.from)}
                  waitLabel="unread" jumpTo={jumpTo}
                  onRead={(m: MailEntry) => markRead(slug, [m.id])
                    .then(() => { setReadBump((n) => n + 1); refresh?.() })
                    .catch(() => {})}
                  onReply={(m: MailEntry, text: string) => {
                    // the desk composer's optimistic ghost, which this
                    // composer never had (D-54): a reply sent from the inbox
                    // is an ordinary message to that node, and its desk —
                    // open behind this modal, or opened a second later —
                    // showed nothing at all until the server copy landed.
                    // Same store, same graduation-on-evidence rule.
                    addPending(slug, m.from, text)
                    // FR-05: the reply is attributed to the mail it answers —
                    // the agent's [MAIL] block quotes the snapshot, so a
                    // two-word reply is unambiguous
                    return sendMessage(slug, m.from, text, undefined, {
                      id: m.id, from: m.from, at: m.at,
                      gist: (m.body ?? '').trim().replace(/\s+/g, ' ')
                        .slice(0, 200),
                    })
                      .then(() => toast([`sent to ${m.from}`]))
                      .catch((e: Error) => {
                        dropPending(slug, m.from, text)
                        toast([`error: ${e.message}`])
                      })
                  }}
                  sender={(id: string) => <SenderChip id={id} nodes={nodes} />} />
              // the user's OWN sends: attachments live in the RECIPIENT's
              // uploads/ (the upload landed there at stage time) — key on
              // m.to; a row without one ('' = unreachable) keeps plain chips
              : <MailList delivered={box.sent ?? []} outgoing
                  fileHref={(p, m) => typeof m.to === 'string' && m.to
                    ? fileUrl(slug, m.to, p) : ''}
                  mdBase={(m) => typeof m.to === 'string' && m.to
                    ? fileBase(slug, m.to) : ''}
                  sender={(id: string) => <SenderChip id={id} nodes={nodes} />} />}
        </div>
        <div className="row">
          {/* ⚠ the bump is not optional here. These rows come from getInbox,
              and the server's own `changed` broadcast only makes clients
              refetch the TREE — a different payload that does not carry them.
              So without this the button's effect waited for the 5 s poll: the
              per-mail path was fixed first and this sibling call site was
              missed, which is the same bug reported twice (2026-08-07/08). */}
          {folder === 'inbox' && (box?.pending.length ?? 0) > 0 && <button onClick={() =>
            clearInbox(slug)
              .then(() => { setReadBump((n) => n + 1); refresh?.() })
              .catch((e: Error) => toast([`error: ${e.message}`]))}>mark all read</button>}
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
  const provPayload = usePolled(getProviders, [], 60000)
  const autopsyGroups = useMemo(
    () => availableAutopsyModels(provPayload, d.fable_filter_model ?? 'opus'),
    [provPayload, d.fable_filter_model])
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
          <option value="auto-autopsy">auto-autopsy</option>
        </select>
        {(d.fable_filter_policy ?? 'halt') === 'auto-autopsy' && (
          <>
            <div className="field-label">autopsy model (fable not selectable)</div>
            <select value={d.fable_filter_model ?? 'opus'} aria-label="autopsy model"
              onChange={(e) => set('fable_filter_model', e.target.value)}>
              {autopsyGroups.map((g) => (
                <optgroup key={g.label} label={g.label}>
                  {g.models.map((m) => (
                    <option key={m.tier} value={m.tier}>
                      {m.label}{m.seat != null ? ` · seat ${m.seat}` : ''}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </>
        )}
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
        <label className="checkline">
          <input type="checkbox" checked={!!d.auto_resume_compact}
            onChange={(e) => set('auto_resume_compact', e.target.checked)} />
          cheap-compact limit-frozen agents before auto-resume wakes them
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
              fable_filter_model: d.fable_filter_policy === 'auto-autopsy'
                ? (d.fable_filter_model ?? 'opus') : undefined,
              default_effort: d.default_effort ?? '',
              cascade_hire: d.cascade_hire !== false,
              cascade_alloc: d.cascade_alloc !== false,
              auto_resume: !!d.auto_resume,
              auto_resume_compact: !!d.auto_resume_compact,
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

  // mobile audit §3.3: onMouseLeave never fires on touch, so the armed latch
  // used to stay live indefinitely — a multi-GB delete degraded to a single
  // tap. A 3s timeout disarms everywhere (mouse users keep the leave path).
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 3000)
    return () => clearTimeout(t)
  }, [armed])
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

/** D-222: the org settings modal's tab series. "basic" is always first; the
 *  rest are the categories that used to be inside the nested advanced modal,
 *  now siblings of it. `mailserver` and `autonomy` are conditional — see
 *  `orgTabs` in the panel. */
type OrgSettingsTab =
  'basic' | 'policies' | 'orgtype' | 'mailserver' | 'autonomy'

// exported for tests/orgsettings.test.tsx — the consolidation is a claim
// about THIS component's shape (one modal, one save, tabs not a nested
// modal), so the test has to be able to mount it directly
export function SettingsPanel({ tree, toast, close }: {
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
  // D-222 (user ruling 2026-09-01, "consolidate settings into ONE modal"):
  // the advanced disclosure and the nested AdvancedOrgModal it opened are
  // gone from this panel. Its categories are now tabs of THIS modal, with
  // "Basic" first — one surface, one Escape, one save button, and no
  // modal-over-a-modal. (AdvancedOrgModal itself stays for the create form,
  // which opens it from an inline form rather than from another modal.)
  const [tab, setTab, visited] = useVisitedTabs<OrgSettingsTab>('basic')
  // the strip is built from live org shape: a kiosk has no autonomy, an org
  // with no mail identity has no mailserver tab. Same conditionals the
  // advanced modal's tab array used — moved out here so the tab strip and
  // the panels below cannot disagree about which tabs exist.
  const orgTabs = useMemo<SettingsTab<OrgSettingsTab>[]>(() => [
    { id: 'basic', label: 'Basic' },
    { id: 'policies', label: 'Policies' },
    { id: 'orgtype', label: 'Org type' },
    ...(tree.net != null
      ? [{ id: 'mailserver' as const, label: 'Mailserver' }] : []),
    ...(!tree.kiosk ? [{ id: 'autonomy' as const, label: 'Autonomy' }] : []),
  ], [tree.net, tree.kiosk])
  // D-204: these are unsaved inputs. The tabs now stay mounted once visited,
  // so a tab switch can no longer destroy them — but close/reopen still
  // unmounts the whole shell, and keeping the only copies here also means a
  // future field added to those tabs inherits the protection instead of
  // having to rediscover it.
  const [netHubDraft, setNetHubDraft] = useState('')
  const [apiKeyDraft, setApiKeyDraft] = useState('')

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
  const filterModel = val('filterModel', tree.fable_filter_model ?? 'opus')
  const setFilterModel = set('filterModel', filterModel)
  const provPayload = usePolled(getProviders, [], 60000)
  const autopsyGroups = useMemo(
    () => availableAutopsyModels(provPayload, filterModel),
    [provPayload, filterModel])
  const defEffort = val('defEffort', tree.default_effort ?? '')
  const setDefEffort = set('defEffort', defEffort)
  const cascadeHire = val('cascadeHire', tree.cascade_hire !== false)
  const setCascadeHire = set('cascadeHire', cascadeHire)
  const cascadeAlloc = val('cascadeAlloc', tree.cascade_alloc !== false)
  const setCascadeAlloc = set('cascadeAlloc', cascadeAlloc)
  // Known-cold pre-turn cheap compaction — per-node overrides
  // live in each agent's own gear panel
  const acc = tree.auto_cheap_compact ?? null
  const accOn = val('accOn', !!acc?.enabled)
  const setAccOn = set('accOn', accOn)
  const accOcc = val<number | string>('accOcc',
    Math.round(((acc?.occ ?? 0.5) as number) * 100))
  const setAccOcc = set('accOcc', accOcc)
  // pre-resume cheap compact (2026-08-17): rides the AUTO limit resume only
  const arCompact = val('arCompact', !!tree.auto_resume_compact)
  const setArCompact = set('arCompact', arCompact)
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
        <SettingsTabs tabs={orgTabs} tab={tab} setTab={setTab}
          idBase="org-settings" label="Organization settings sections" />

        {/* ── Basic: the knobs an operator reaches for, in the order they
            reach for them. Everything that used to be behind "advanced…" is
            now a SIBLING TAB rather than a second modal. */}
        <SettingsTabPanel id="basic" idBase="org-settings"
          active={tab === 'basic'}>
        {/* folder access lives on the eye's ⚙ gear panel (user ruling) */}
        <SetGroup title="Credits">
          <SetRow label="top-level grant cap"
            hint="the largest grant any top-level agent may hold">
            <input type="number" min="1" step="1" value={maxTop}
              aria-label="top-level grant cap"
              onChange={(e) => setMaxTop(e.target.value)} />
          </SetRow>
          <SetRow label="default top-level grant"
            hint="pre-filled on new hires">
            <input type="number" min="0" step="1" value={defTop}
              aria-label="default top-level grant"
              onChange={(e) => setDefTop(e.target.value)} />
          </SetRow>
        </SetGroup>
        <SetGroup title="Agent defaults">
          <SetRow label="compaction threshold"
            hint="50–95%. Splits the agent when its context passes this.">
            <input type="number" min="50" max="95" step="1" value={compactAt}
              aria-label="compaction threshold percent"
              onChange={(e) => setCompactAt(e.target.value)} />
            <span className="dim">%</span>
          </SetRow>
          {/* default effort (user req 2026-08-01, visible inherit): agents
              without their own effort follow this LIVE — changing it here
              reaches every unset agent's next turn, no rehire */}
          <SetRow label="default thinking effort"
            hint={'agents without their own setting inherit this, live — '
              + 'no rehire needed. Changing it restarts every agent that '
              + 'inherits it.'}>
            <select value={defEffort} aria-label="default thinking effort"
              onChange={(e) => setDefEffort(e.target.value)}>
              <option value="">CLI default (no flag)</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="xhigh">xhigh</option>
              <option value="max">max</option>
            </select>
          </SetRow>
        </SetGroup>
        {/* per-kiosk controls (user ruling 2026-07-31): caps, share URL and
            pause live HERE, in the org's own settings — the all-kiosks
            dashboard on the welcome panel is gone */}
        {kk && (
          <SetGroup title="Kiosk"
            note={[kk.sandbox ? 'sandboxed' : '',
              kk.enabled ? '' : 'URL paused'].filter(Boolean).join(' · ')}>
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
          </SetGroup>
        )}
        <SetGroup title="Org charter" note="org.md">
          <SetBlock hint={"loaded once at session start by every CLAUDE "
            + "agent that holds the workspace — which is every Claude agent "
            + "by default — so saving restarts them. Codex agents never see "
            + "this file: their CLI reads AGENTS.md, which orgtree writes "
            + "from each agent's own charter."}>
            <textarea className="orgmd-editor" value={orgMd ?? ''}
              aria-label="org.md" disabled={orgMd == null}
              onChange={(e) => setOrgMd(e.target.value)} />
          </SetBlock>
        </SetGroup>
        </SettingsTabPanel>

        {/* ── Policies (was the advanced modal's "general" tab) ─────────── */}
        <SettingsTabPanel id="policies" idBase="org-settings"
          active={tab === 'policies'}>
          {visited('policies') && (<>
            <SetGroup title="Fable tier">
              <SetRow label="weekly-limit policy"
                hint={tree.fable_api_fallback
                  ? 'a TRUSTED weekly Fable-tier hit currently bypasses this'
                    + ' policy — see "also cover the weekly Fable-tier limit"'
                    + ' on the Autonomy tab'
                  : 'what happens when the weekly Fable-tier limit is reached'}>
                <select value={fablePolicy} aria-label="fable weekly-limit policy"
                  onChange={(e) => setFablePolicy(e.target.value)}>
                  <option value="halt">halt (default)</option>
                  <option value="opus">switch to opus</option>
                  <option value="dissolve">dissolve subtree</option>
                </select>
              </SetRow>
              <SetRow label="content-filter policy"
                hint={'a flagged message halts the turn, converts the '
                  + 'agent to opus and retries, or runs an auto-autopsy'}>
                <select value={filterPolicy} aria-label="fable content-filter policy"
                  onChange={(e) => setFilterPolicy(e.target.value)}>
                  <option value="halt">halt (default)</option>
                  <option value="opus">switch to opus + retry</option>
                  <option value="auto-autopsy">auto-autopsy</option>
                </select>
              </SetRow>
              {filterPolicy === 'auto-autopsy' && (
                <SetRow label="autopsy model"
                  hint="model used to run the autopsy and re-brief the replacement agent (fable not selectable)">
                  <select value={filterModel} aria-label="autopsy model"
                    onChange={(e) => setFilterModel(e.target.value)}>
                    {autopsyGroups.map((g) => (
                      <optgroup key={g.label} label={g.label}>
                        {g.models.map((m) => (
                          <option key={m.tier} value={m.tier}>
                            {m.label}{m.seat != null ? ` · seat ${m.seat}` : ''}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </SetRow>
              )}
            </SetGroup>
            <SetGroup title="Cache-protective cheap compaction">
              <SetToggle label="reset a session before a known-cold turn"
                checked={accOn} onChange={setAccOn}
                hint={'org default — agents can override in their own ⚙. '
                  + 'The old self stays consultable. Cache expiry is fixed by '
                  + 'lane and never editable: Claude uses 60 min after a '
                  + 'positive subscription receipt or 5 min after a positive '
                  + 'API-key receipt; OpenAI subscription uses the documented '
                  + '30 min default as a fixed estimate. A known identity '
                  + 'mismatch is cold immediately; unknown forecasts never '
                  + 'auto-compact.'} />
              {accOn && (
                <SetRow label="only above a context occupancy of">
                  <input type="number" min="5" max="95" step="5" value={accOcc}
                    aria-label="cheap compaction context occupancy percent"
                    onChange={(e) => setAccOcc(e.target.value)} />
                  <span className="dim">%</span>
                </SetRow>
              )}
              {/* 2026-08-17: a usage-limit freeze outlives the cache TTL by
                  construction, so the auto-resume wake can swap the session
                  first and skip the cold reload. The manual ▶ never compacts. */}
              <SetToggle
                label="cheap-compact limit-frozen agents before auto-resume"
                checked={arCompact} onChange={setArCompact}
                title="applies only to the automatic resume after a usage-limit freeze (auto-resume toggle); pressing ▶ yourself resumes sessions as they are"
                hint={'applies to the automatic resume only — pressing ▶ '
                  + 'yourself resumes the session as it is'} />
            </SetGroup>
            {/* §4.6 cost-bubbling toggles (user spec, both ON by default) */}
            <SetGroup title="Credit cost bubbling">
              <SetToggle label="hires bubble their cost up the chain"
                checked={cascadeHire} onChange={setCascadeHire}
                hint={"off: the hiring agent's superior must hold the free "
                  + 'credits itself'} />
              <SetToggle
                label="allocations & model upgrades bubble their cost up the chain"
                checked={cascadeAlloc} onChange={setCascadeAlloc}
                hint="off: limited to the superior's own free credits" />
            </SetGroup>
          </>)}
        </SettingsTabPanel>

        {/* ── Org type (was the advanced modal's "org type" tab) ────────── */}
        <SettingsTabPanel id="orgtype" idBase="org-settings"
          active={tab === 'orgtype'}>
          {visited('orgtype') && (<>
            {/* born-with facts render LOCKED: the modal must not offer to
                change what cannot change after creation (docket F-07 rule 1) */}
            <SetGroup title="Born with" note="set at creation, immutable">
              <SetBlock>
                <div className="row" style={{ flexWrap: 'wrap' }}>
                  <span className="badge dim">{kk ? 'kiosk' : 'not a kiosk'}</span>
                  <span className="badge dim">{tree.sandboxed ? 'sandboxed (Docker)' : 'unsandboxed'}</span>
                  {tree.disk && <span className="badge dim">fixed disk · resize via the storage browser</span>}
                </div>
              </SetBlock>
            </SetGroup>
            {ms && ceil && (
              <SetGroup title="Kiosk permission ceiling"
                note={"the maximum grantable to any agent — a change "
                  + "restarts every agent in the org"}>
                <SetBlock label="tools"
                  hint={'visitors and agents retool freely WITHIN the '
                    + "ceiling (clamped, never refused); lowering it sweeps "
                    + "every agent's grants to fit"}>
                  <div className="ceil-tools">
                    {(['bash', 'web', 'edit', 'subagents'] as const).map((k) => (
                      <label key={k} className="checkline">
                        <input type="checkbox" checked={ceil[k]}
                          onChange={(e) => setCeil((c) => ({ ...c!, [k]: e.target.checked }))} />
                        {k}
                      </label>
                    ))}
                  </div>
                </SetBlock>
                {/* "ADDITIONAL" is load-bearing (coordinator, 2026-09-01):
                    this list is the OPERATOR-supplied servers only. Every
                    agent always reaches Orgtree's own MCP server whatever is
                    set here, so an empty box reads as "zero callable MCP
                    tools" to anyone who has not been told otherwise — and it
                    never means that. */}
                <SetBlock label="additional MCP servers"
                  hint={'operator-supplied servers only — "*" = all, empty = '
                    + 'none, or a comma-separated list. Orgtree’s own MCP '
                    + 'server is always available to every agent and is not '
                    + 'affected by this field.'}>
                  <input value={ceilMcp} placeholder="*"
                    aria-label="additional MCP servers"
                    onChange={(e) => setCeilMcp(e.target.value)} />
                </SetBlock>
                <SetBlock label="folder bounds"
                  hint="grants clamp into these">
                  <CeilDirs dirs={ceilDirs} onChange={setCeilDirs} />
                </SetBlock>
                {/* styled like the credits/spend/storage caps (user spec) */}
                <SetBlock>
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
                </SetBlock>
                <SetToggle
                  label="auto-raise the ceiling on my own over-ceiling grants"
                  checked={autoRaise} onChange={setAutoRaise}
                  title="an over-ceiling grant made by YOU raises the ceiling to fit (logged, named) instead of clamping; visitors always clamp"
                  hint={'an over-ceiling grant made by YOU raises the '
                    + 'ceiling to fit (logged, named) instead of clamping; '
                    + 'visitors always clamp'} />
              </SetGroup>
            )}
            {(tree.fable_lock || tree.disk) && (
              <SetGroup title="Maintenance">
                {tree.fable_lock && (
                  <SetBlock>
                    <div className="row">
                      <button className="danger" onClick={() =>
                        saveSettings(tree.slug, { clear_fable_lock: true })
                          .then((r) => { toast(r.warnings); close() })
                          .catch((e: Error) => toast([`error: ${e.message}`]))}>
                        <BlockIcon fontSize="inherit" /> clear the fable weekly-limit lock (your decree)</button>
                    </div>
                  </SetBlock>
                )}
                {tree.disk && <SweepBlock slug={tree.slug} toast={toast} />}
              </SetGroup>
            )}
          </>)}
        </SettingsTabPanel>

        {/* ── Mailserver (F-06). Saves IMMEDIATELY on its own, which is why
            it is rendered only once visited: an unvisited tab must not fetch
            hub state the operator never asked to see. ─────────────────── */}
        {tree.net != null && (
          <SettingsTabPanel id="mailserver" idBase="org-settings"
            active={tab === 'mailserver'}>
            {visited('mailserver') && <NetTab tree={tree} toast={toast}
              adding={netHubDraft} setAdding={setNetHubDraft} />}
          </SettingsTabPanel>
        )}

        {/* ── Autonomy — kiosks have none, so the tab is absent for them ── */}
        {!kk && (
          <SettingsTabPanel id="autonomy" idBase="org-settings"
            active={tab === 'autonomy'}>
            {visited('autonomy') && <AutonomyTab tree={tree} toast={toast}
              keyDraft={apiKeyDraft} setKeyDraft={setApiKeyDraft} />}
          </SettingsTabPanel>
        )}

        {/* ONE save button for the whole modal, on every tab — the panel's
            single save surface, unchanged. It is now visible from whichever
            tab you are on, which is what retires the four "changes here save
            with the panel's own save button" notes the nested modal needed. */}
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
                  fable_filter_model: filterPolicy === 'auto-autopsy' ? filterModel : undefined,
                  default_effort: defEffort,
                  cascade_hire: cascadeHire,
                  cascade_alloc: cascadeAlloc,
                  auto_resume_compact: arCompact,
                  auto_cheap_compact: { enabled: accOn,
                    occ: (+accOcc || 50) / 100 } }),
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
