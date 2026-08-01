// kiosk v2: when the SPA is served from a preauthenticated public URL
// (/k/<token>/…), every API call and the WS must carry the token prefix —
// the public listener serves nothing outside it.
import type {
  AudiencesPayload, ChartersPayload, ChatPayload, DefaultsPayload,
  DiskDeleteResult, DiskPayload, EventsPayload, FsPayload,
  HireDefaultsRequest, HistoryPayload, HostPayload,
  InboxPayload, KioskCfgRequest, KioskSaveResult, KioskSpecRequest,
  McpServersPayload, OpRequest, OpResult, OrgListEntry, OrgMdPayload,
  ReorderRequest, ScopeRequest, ScratchPayload, SendMessageResult,
  SettingsRequest, SettingsResult, TreePayload, UploadResult,
} from './types'

export const BASE = (location.pathname.match(/^\/k\/[A-Za-z0-9_-]+/) || [''])[0]
const u = (p: string) => BASE + p

// the one wire-boundary cast in the app: runtime JSON is untyped, and each
// endpoint's declared Promise<T> return type is the contract that types it.
// T infers from that declared return at every call site - no `any` escapes.
const req = <T,>(path: string, init?: RequestInit): Promise<T> =>
  fetch(u(path), init).then((r) => {
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
): Promise<{ slug: string }> =>
  req('/api/orgs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name, dirs, ...(kiosk ? { kiosk } : {}),
      ...(sandbox && !kiosk ? { sandbox: true } : {}),
    }),
  })
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
export const creditDecide = (slug: string, id: string, action: string): Promise<OpResult> =>
  req(`/api/orgs/${slug}/credit-requests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, action }),
  })
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
  req(`/api/orgs/${slug}/nodes/${nid}/upload?name=${encodeURIComponent(file.name)}`, {
    method: 'POST', body: file,
  })
// direct <a href> download target (browser handles the transfer) — BASE-aware
// so kiosk visitors download through their token prefix
export const fileUrl = (slug: string, nid: string, path: string): string =>
  u(`/api/orgs/${slug}/nodes/${nid}/file?path=${encodeURIComponent(path)}`)
export const sendMessage = (
  slug: string, nid: string, text: string, attachments?: string[],
): Promise<SendMessageResult> =>
  req(`/api/orgs/${slug}/nodes/${nid}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text,
      ...(attachments?.length ? { attachments } : {}) }),
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
export const diskGrow = (
  slug: string, size_mb: number,
): Promise<{ size_mb: number; used: number | null; total: number | null }> =>
  req(`/api/orgs/${slug}/disk/grow`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ size_mb }),
  })
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
