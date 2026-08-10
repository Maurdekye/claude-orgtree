// kiosk v2: when the SPA is served from a preauthenticated public URL
// (/k/<token>/…), every API call and the WS must carry the token prefix —
// the public listener serves nothing outside it.
import type {
  AudiencesPayload, ChartersPayload, ChatPayload, DefaultsPayload,
  DiskDeleteResult, DiskDirPayload, DiskPayload, EventsPayload, FsPayload,
  HireDefaultsRequest, HistoryPayload, HostPayload,
  InboxPayload, KioskCfgRequest, KioskSaveResult, KioskSpecRequest,
  McpServersPayload, OpRequest, OpResult, OrgListEntry, OrgMdPayload,
  OrgNetReveal, ReorderRequest, ScopeRequest, ScratchPayload, SendMessageResult,
  SettingsRequest, SettingsResult, SweepPreview, SweepResult, TreePayload,
  UploadResult,
} from './types'

export const BASE = (location.pathname.match(/^\/k\/[A-Za-z0-9_-]+/) || [''])[0]
const u = (p: string) => BASE + p

/** THE RESTART DETECTOR.
 *
 *  A redeploy restarts the server and leaves every open tab running the bundle
 *  it loaded — an old client against a new API, which looks fine and is subtly
 *  wrong until someone thinks to press refresh. The backend stamps every
 *  response with the id of the process that answered it (`api.INSTANCE`, fresh
 *  per start), so the first one we see is the server we were built against and
 *  any different one means it was replaced. Reload, whole page.
 *
 *  It costs no request and no poller: the heartbeats this app already runs
 *  (the tree, the conversation, every open panel) all pass through `req`, so
 *  detection is within one poll of the restart.
 *
 *  ⚠ Deliberately unconditional — the user asked for a forced refresh. An
 *  unsent composer draft is lost, which is the same thing pressing F5 does.
 *  `reloading` only stops several in-flight responses from each calling
 *  `location.reload()` while the first one is already tearing the page down. */
let instance = ''
let reloading = false
function noteInstance(r: Response): void {
  const id = r.headers.get('X-Orgtree-Instance')
  if (!id || reloading) return
  if (!instance) { instance = id; return }
  if (id === instance) return
  reloading = true
  console.info(`orgtree restarted (${instance} → ${id}) — reloading`)
  location.reload()
}

/** A request that never answers is worse than one that fails.
 *
 *  `fetch` has NO timeout. A backend that accepts the connection and then
 *  never responds — wedged update thread, a handler blocked on something
 *  upstream, a half-open socket after the machine slept — leaves the promise
 *  unsettled for minutes, and every caller that gates on it stalls with it.
 *  On the desk that is visible as a sent message stuck unconfirmed at the
 *  bottom of the conversation: its ghost cannot graduate (no payload) and
 *  cannot retire (no error), so it sits there looking queued forever
 *  (user report 2026-08-10).
 *
 *  A ceiling turns that into an ordinary failure, which every caller already
 *  handles. It is deliberately GENEROUS — this is a liveness backstop, not a
 *  latency policy, and a slow-but-alive request must never be cut off. Calls
 *  that legitimately outlast it (uploads, org creation, sweeps) pass their
 *  own. */
const DEFAULT_TIMEOUT_MS = 45_000
/** for the handful that provision, format or transfer — still bounded, so a
 *  hung one cannot stall its caller for the life of the tab */
const SLOW_TIMEOUT_MS = 600_000
const timeoutSignal = (ms: number): AbortSignal | undefined => {
  // AbortSignal.timeout is unavailable on older Safari; without it the call
  // simply behaves as it always did rather than failing to be made
  const f = (AbortSignal as { timeout?: (n: number) => AbortSignal }).timeout
  return typeof f === 'function' ? f(ms) : undefined
}

// the one wire-boundary cast in the app: runtime JSON is untyped, and each
// endpoint's declared Promise<T> return type is the contract that types it.
// T infers from that declared return at every call site - no `any` escapes.
const req = <T,>(path: string, init?: RequestInit,
                 timeoutMs: number = DEFAULT_TIMEOUT_MS): Promise<T> =>
  fetch(u(path), init?.signal || !timeoutMs
    ? init
    : { ...init, signal: timeoutSignal(timeoutMs) }).then((r) => {
    // before the ok/not-ok split: a restart is worth noticing even when the
    // call it rode in on failed
    noteInstance(r)
    if (!r.ok) {
      return r.json().then((b: { detail?: string }) => {
        throw new Error(b.detail || r.statusText)
      })
    }
    return r.json() as Promise<T>
  })

export const listOrgs = (): Promise<OrgListEntry[]> => req('/api/orgs')
export const createOrg = (
  name: string, dirs: string[],
  kiosk: KioskSpecRequest | null = null, sandbox = false,
  diskMb: number | null = null,
  netAutoconnect = true, netHubs: string[] = [],
): Promise<{ slug: string }> =>
  // provisions a sandbox and can format a disk image — minutes, legitimately
  req<{ slug: string }>('/api/orgs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name, dirs, ...(kiosk ? { kiosk } : {}),
      ...(sandbox && !kiosk ? { sandbox: true } : {}),
      // F-06: mailserver — local-hub opt-out + typed remote addresses
      ...(netAutoconnect ? {} : { net_autoconnect: false }),
      ...(netHubs.length ? { net_hubs: netHubs } : {}),
      // sandboxed non-kiosk orgs: virtual-disk size (4096 MB minimum)
      ...(sandbox && !kiosk && diskMb != null ? { disk_mb: diskMb } : {}),
    }),
  }, SLOW_TIMEOUT_MS)
export const getTree = (slug: string): Promise<TreePayload> =>
  req(`/api/orgs/${slug}`)
export const deleteOrg = (slug: string): Promise<{ ok: boolean }> =>
  req(`/api/orgs/${slug}`, { method: 'DELETE' })
export const runOp = (slug: string, body: OpRequest): Promise<OpResult> =>
  req(`/api/orgs/${slug}/ops`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

export const getChat = (slug: string, nid: string, last?: number): Promise<ChatPayload> =>
  req(`/api/orgs/${slug}/nodes/${nid}/chat${last ? `?last=${last}` : ''}`)
export const getMcpServers = (): Promise<McpServersPayload> =>
  req('/api/mcp-servers')
export const getCharters = (): Promise<ChartersPayload> =>
  req('/api/charters')
export const getFs = (path = ''): Promise<FsPayload> =>
  req(`/api/fs?path=${encodeURIComponent(path)}`)
export const getInbox = (slug: string): Promise<InboxPayload> =>
  req(`/api/orgs/${slug}/inbox`)
export const getNodeInbox = (slug: string, nid: string): Promise<InboxPayload> =>
  req(`/api/orgs/${slug}/nodes/${nid}/inbox`)
export const resumeFrozen = (slug: string): Promise<{ resumed: string[] }> =>
  req(`/api/orgs/${slug}/resume`, { method: 'POST' })
export const killAll = (slug: string): Promise<{ interrupted: string[] }> =>
  req(`/api/orgs/${slug}/killswitch`, { method: 'POST' })
export const dissolveAll = (slug: string): Promise<{ freed: number; nodes: number }> =>
  req(`/api/orgs/${slug}/dissolve-all`, { method: 'POST' })
export const interruptNode = (
  slug: string, nid: string,
): Promise<{ interrupted: boolean; reason?: string }> =>
  req(`/api/orgs/${slug}/nodes/${nid}/interrupt`, { method: 'POST' })
export const compactNode = (slug: string, nid: string): Promise<{ started: boolean }> =>
  req(`/api/orgs/${slug}/nodes/${nid}/compact`, { method: 'POST' })
/** ⭐ the user's per-node override (ruling 2026-08-06): releases EVERY lock
 *  holding the agent — any freeze kind, limit_locked, the org fable_lock if
 *  last holder — and re-drives it */
export const unstickNode = (slug: string, nid: string):
  Promise<{ released: string[]; status?: string; warnings?: string[] }> =>
  req(`/api/orgs/${slug}/nodes/${nid}/unstick`, { method: 'POST' })
export const creditDecide = (
  slug: string, id: string, action: string,
  // F-05: `granted` = the counter-offer amount; `dry` = validate + stranding
  // warnings only, mutating nothing (shown BEFORE the user commits)
  granted?: number, dry?: boolean,
): Promise<OpResult & { ok?: boolean; warnings?: string[] }> =>
  req(`/api/orgs/${slug}/credit-requests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, action,
      ...(granted != null ? { granted } : {}), ...(dry ? { dry: true } : {}) }),
  })
export const answerAsk = (
  slug: string, aid: string,
  // FR-04 batch cards: `selected` is one item per tab, positionally —
  // a string, or a list for a multi tab's picks; `rev` echoes the card
  // revision so a mid-render amend refuses the stale submission
  body: { selected?: (string | string[])[]; text?: string; rev?: number
    dismiss?: boolean },
): Promise<{ answered: string; node: string }> =>
  req(`/api/orgs/${slug}/asks/${aid}/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
// FR-01: hand an agent's session to claude.ai / the mobile app (and back)
export const remoteControl = (slug: string, nid: string,
  action: 'start' | 'stop'):
  Promise<{ ok?: boolean; note?: string }> =>
  req(`/api/orgs/${slug}/nodes/${nid}/remote-control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  })
// FR-03: presented documents — the reader fetches the body on open
export const getDocument = (slug: string, did: string):
  Promise<{ id: string; node: string; title: string; body: string; at: string }> =>
  req(`/api/orgs/${slug}/documents/${did}`)
export const dismissDocument = (slug: string, did: string):
  Promise<{ ok: boolean; node: string }> =>
  req(`/api/orgs/${slug}/documents/${did}`, { method: 'DELETE' })
export const clearInbox = (slug: string): Promise<{ ok: boolean }> =>
  req(`/api/orgs/${slug}/inbox/clear`, { method: 'POST' })
export const markRead = (slug: string, ids: string[]): Promise<{ read: number }> =>
  req(`/api/orgs/${slug}/inbox/read`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
export const getHistory = (slug: string, nid: string): Promise<HistoryPayload> =>
  req(`/api/orgs/${slug}/nodes/${nid}/history`)
export const getScratch = (slug: string, nid: string, path = ''): Promise<ScratchPayload> =>
  req(`/api/orgs/${slug}/nodes/${nid}/scratch?path=${encodeURIComponent(path)}`)
export const getOrgMd = (slug: string): Promise<OrgMdPayload> =>
  req(`/api/orgs/${slug}/orgmd`)
export const putOrgMd = (
  slug: string, content: string,
): Promise<{ path: string; bytes: number }> =>
  req(`/api/orgs/${slug}/orgmd`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
export const getAudiences = (slug: string): Promise<AudiencesPayload> =>
  req(`/api/orgs/${slug}/audiences`)
export const audienceAction = (
  slug: string, action: string, node: string, target?: string | null,
): Promise<OpResult> =>
  req(`/api/orgs/${slug}/audiences`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, node, target }),
  })
export const getHost = (): Promise<HostPayload> => req('/api/host')
export const getDefaults = (): Promise<DefaultsPayload> =>
  req('/api/defaults')
export const saveDefaults = (body: SettingsRequest): Promise<DefaultsPayload> =>
  req('/api/defaults', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
export const orgInboxRead = (slug: string): Promise<{ ok: boolean }> =>
  req(`/api/orgs/${slug}/org_inbox/read`, { method: 'POST' })
// F-06: the user composes extern mail from the mailbox UI (admin only)
export const orgInboxSend = (
  slug: string, to: string, body: string, attachments: string[] = [],
): Promise<{ id: string; warnings: string[] }> =>
  req(`/api/orgs/${slug}/org_inbox/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ to, body, attachments }),
  })
export const orgInboxUpload = (
  slug: string, file: File,
): Promise<{ id: string; name: string; bytes: number }> =>
  req(`/api/orgs/${slug}/org_inbox/upload?name=${encodeURIComponent(file.name)}`, {
    method: 'POST', body: file,
  })
// F-06: the network-identity reveal — loopback admin only; the ONE call that
// returns the org secret (the settings panel's reveal/export)
export const getOrgNet = (slug: string): Promise<OrgNetReveal> =>
  req(`/api/orgs/${slug}/net`)
// F-06: is a hub reachable at this address right now? (a HINT — the
// checkbox never gates on it; a hub that is down still gets configured)
export const probeHub = (
  address = '',
): Promise<{ ok: boolean; name?: string | null }> =>
  req(`/api/net/probe${address ? `?address=${encodeURIComponent(address)}` : ''}`)
export const saveScope = (slug: string, nid: string, scope: ScopeRequest): Promise<OpResult> =>
  req(`/api/orgs/${slug}/nodes/${nid}/scope`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(scope),
  })
export const reorderNode = (
  slug: string, nid: string, body: ReorderRequest,
): Promise<OpResult> =>
  req(`/api/orgs/${slug}/nodes/${nid}/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
export const getEvents = (slug: string): Promise<EventsPayload> =>
  req(`/api/orgs/${slug}/events`)
export const retractMail = (
  slug: string, nid: string, mid: string,
): Promise<{ retracted: string }> =>
  req(`/api/orgs/${slug}/nodes/${nid}/mail/${mid}`, { method: 'DELETE' })
export const uploadFile = (slug: string, nid: string, file: File): Promise<UploadResult> =>
  // a large file over a slow link legitimately outlasts the default ceiling
  req<UploadResult>(`/api/orgs/${slug}/nodes/${nid}/upload?name=${encodeURIComponent(file.name)}`, {
    method: 'POST', body: file,
  }, SLOW_TIMEOUT_MS)
// direct <a href> download target (browser handles the transfer) — BASE-aware
// so kiosk visitors download through their token prefix
export const fileUrl = (slug: string, nid: string, path: string): string =>
  u(`/api/orgs/${slug}/nodes/${nid}/file?path=${encodeURIComponent(path)}`)
export const sendMessage = (
  slug: string, nid: string, text: string, attachments?: string[],
  replyTo?: { id?: string; from: string; at?: string; gist: string },
): Promise<SendMessageResult> =>
  req(`/api/orgs/${slug}/nodes/${nid}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text,
      ...(attachments?.length ? { attachments } : {}),
      // FR-05: an inline mailbox reply carries a snapshot of what it answers
      ...(replyTo ? { reply_to: replyTo } : {}) }),
  })
export const saveSettings = (slug: string, opts: SettingsRequest = {}): Promise<SettingsResult> =>
  req(`/api/orgs/${slug}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  })
export const saveHireDefaults = (
  slug: string, opts: HireDefaultsRequest = {},
): Promise<OpResult> =>
  req(`/api/orgs/${slug}/defaults`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  })
export const saveKiosk = (slug: string, opts: KioskCfgRequest = {}): Promise<KioskSaveResult> =>
  req(`/api/orgs/${slug}/kiosk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  })

// the org-disk recovery browser (its own surface, deliberately not /api/fs)
export const getDisk = (slug: string, offset = 0, limit = 200): Promise<DiskPayload> =>
  req(`/api/orgs/${slug}/disk?offset=${offset}&limit=${limit}`)
export const diskDelete = (slug: string, paths: string[]): Promise<DiskDeleteResult> =>
  req(`/api/orgs/${slug}/disk/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  })
export interface DiskResizeResult {
  size_mb: number
  pending_mb: number | null
  used?: number | null
  total?: number | null
}
// grow applies online immediately (and clears any pending shrink); a shrink
// stages a PENDING request applied when the org's container is next down
export const diskResize = (slug: string, size_mb: number): Promise<DiskResizeResult> =>
  req(`/api/orgs/${slug}/disk/resize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ size_mb }),
  })
export const diskResizeCancel = (slug: string): Promise<DiskResizeResult> =>
  req(`/api/orgs/${slug}/disk/resize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cancel: true }),
  })
export const diskResizeApply = (slug: string): Promise<DiskResizeResult> =>
  req(`/api/orgs/${slug}/disk/resize/apply`, { method: 'POST' })
export const getDiskDir = (slug: string, path = ''): Promise<DiskDirPayload> =>
  req(`/api/orgs/${slug}/disk/dir?path=${encodeURIComponent(path)}`)
export const getSweepPreview = (slug: string): Promise<SweepPreview> =>
  req(`/api/orgs/${slug}/sweep-legacy`)
export const sweepLegacy = (slug: string): Promise<SweepResult> =>
  req(`/api/orgs/${slug}/sweep-legacy`, { method: 'POST' })
export const diskFileUrl = (slug: string, path: string): string =>
  u(`/api/orgs/${slug}/disk/file?path=${encodeURIComponent(path)}`)

export function openWs(
  slug: string,
  onChanged: (ev: MessageEvent) => void,
  onClose?: () => void,
): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}${BASE}/api/orgs/${slug}/ws`)
  ws.onmessage = onChanged
  const ping = setInterval(() => { if (ws.readyState === 1) ws.send('ping') }, 25000)
  ws.onclose = () => { clearInterval(ping); onClose?.() }
  return ws
}
