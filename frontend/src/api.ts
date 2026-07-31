// kiosk v2: when the SPA is served from a preauthenticated public URL
// (/k/<token>/…), every API call and the WS must carry the token prefix —
// the public listener serves nothing outside it.
import type {
  AudiencesPayload, ChartersPayload, ChatPayload, DefaultsPayload,
  EventsPayload, FsPayload, HireDefaultsRequest, HistoryPayload, HostPayload,
  InboxPayload, KioskCfgRequest, KioskSaveResult, KioskSpecRequest,
  McpServersPayload, OpRequest, OpResult, OrgListEntry, OrgMdPayload,
  ReorderRequest, ScopeRequest, ScratchPayload, SendMessageResult,
  SettingsRequest, SettingsResult, TreePayload, UploadResult,
} from './types'

export const BASE = (location.pathname.match(/^\/k\/[A-Za-z0-9_-]+/) || [''])[0]
const u = (p: string) => BASE + p

const j = (r: Response): Promise<any> => {
  if (!r.ok) return r.json().then((b) => { throw new Error(b.detail || r.statusText) })
  return r.json()
}

export const listOrgs = (): Promise<OrgListEntry[]> => fetch(u('/api/orgs')).then(j)
export const createOrg = (
  name: string, dirs: string[],
  kiosk: KioskSpecRequest | null = null, sandbox = false,
): Promise<{ slug: string }> =>
  fetch(u('/api/orgs'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name, dirs, ...(kiosk ? { kiosk } : {}),
      ...(sandbox && !kiosk ? { sandbox: true } : {}),
    }),
  }).then(j)
export const getTree = (slug: string): Promise<TreePayload> =>
  fetch(u(`/api/orgs/${slug}`)).then(j)
export const deleteOrg = (slug: string): Promise<{ ok: boolean }> =>
  fetch(u(`/api/orgs/${slug}`), { method: 'DELETE' }).then(j)
export const runOp = (slug: string, body: OpRequest): Promise<OpResult> =>
  fetch(u(`/api/orgs/${slug}/ops`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j)

export const getChat = (slug: string, nid: string, last?: number): Promise<ChatPayload> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/chat${last ? `?last=${last}` : ''}`)).then(j)
export const getMcpServers = (): Promise<McpServersPayload> =>
  fetch(u('/api/mcp-servers')).then(j)
export const getCharters = (): Promise<ChartersPayload> =>
  fetch(u('/api/charters')).then(j)
export const getFs = (path = ''): Promise<FsPayload> =>
  fetch(u(`/api/fs?path=${encodeURIComponent(path)}`)).then(j)
export const getInbox = (slug: string): Promise<InboxPayload> =>
  fetch(u(`/api/orgs/${slug}/inbox`)).then(j)
export const getNodeInbox = (slug: string, nid: string): Promise<InboxPayload> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/inbox`)).then(j)
export const resumeFrozen = (slug: string): Promise<{ resumed: string[] }> =>
  fetch(u(`/api/orgs/${slug}/resume`), { method: 'POST' }).then(j)
export const killAll = (slug: string): Promise<{ interrupted: string[] }> =>
  fetch(u(`/api/orgs/${slug}/killswitch`), { method: 'POST' }).then(j)
export const dissolveAll = (slug: string): Promise<{ freed: number; nodes: number }> =>
  fetch(u(`/api/orgs/${slug}/dissolve-all`), { method: 'POST' }).then(j)
export const interruptNode = (
  slug: string, nid: string,
): Promise<{ interrupted: boolean; reason?: string }> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/interrupt`), { method: 'POST' }).then(j)
export const compactNode = (slug: string, nid: string): Promise<{ started: boolean }> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/compact`), { method: 'POST' }).then(j)
export const creditDecide = (slug: string, id: string, action: string): Promise<OpResult> =>
  fetch(u(`/api/orgs/${slug}/credit-requests`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, action }),
  }).then(j)
export const clearInbox = (slug: string): Promise<{ ok: boolean }> =>
  fetch(u(`/api/orgs/${slug}/inbox/clear`), { method: 'POST' }).then(j)
export const markRead = (slug: string, ids: string[]): Promise<{ read: number }> =>
  fetch(u(`/api/orgs/${slug}/inbox/read`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  }).then(j)
export const getHistory = (slug: string, nid: string): Promise<HistoryPayload> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/history`)).then(j)
export const getScratch = (slug: string, nid: string, path = ''): Promise<ScratchPayload> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/scratch?path=${encodeURIComponent(path)}`)).then(j)
export const getOrgMd = (slug: string): Promise<OrgMdPayload> =>
  fetch(u(`/api/orgs/${slug}/orgmd`)).then(j)
export const putOrgMd = (
  slug: string, content: string,
): Promise<{ path: string; bytes: number }> =>
  fetch(u(`/api/orgs/${slug}/orgmd`), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  }).then(j)
export const getAudiences = (slug: string): Promise<AudiencesPayload> =>
  fetch(u(`/api/orgs/${slug}/audiences`)).then(j)
export const audienceAction = (
  slug: string, action: string, node: string, target?: string | null,
): Promise<OpResult> =>
  fetch(u(`/api/orgs/${slug}/audiences`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, node, target }),
  }).then(j)
export const getHost = (): Promise<HostPayload> => fetch(u('/api/host')).then(j)
export const getDefaults = (): Promise<DefaultsPayload> =>
  fetch(u('/api/defaults')).then(j)
export const saveDefaults = (body: SettingsRequest): Promise<DefaultsPayload> =>
  fetch(u('/api/defaults'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j)
export const orgInboxRead = (slug: string): Promise<{ ok: boolean }> =>
  fetch(u(`/api/orgs/${slug}/org_inbox/read`), { method: 'POST' }).then(j)
export const saveScope = (slug: string, nid: string, scope: ScopeRequest): Promise<OpResult> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/scope`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(scope),
  }).then(j)
export const reorderNode = (
  slug: string, nid: string, body: ReorderRequest,
): Promise<OpResult> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/reorder`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j)
export const getEvents = (slug: string): Promise<EventsPayload> =>
  fetch(u(`/api/orgs/${slug}/events`)).then(j)
export const retractMail = (
  slug: string, nid: string, mid: string,
): Promise<{ retracted: string }> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/mail/${mid}`), { method: 'DELETE' }).then(j)
export const uploadFile = (slug: string, nid: string, file: File): Promise<UploadResult> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/upload?name=${encodeURIComponent(file.name)}`), {
    method: 'POST', body: file,
  }).then(j)
// direct <a href> download target (browser handles the transfer) — BASE-aware
// so kiosk visitors download through their token prefix
export const fileUrl = (slug: string, nid: string, path: string): string =>
  u(`/api/orgs/${slug}/nodes/${nid}/file?path=${encodeURIComponent(path)}`)
export const sendMessage = (
  slug: string, nid: string, text: string, attachments?: string[],
): Promise<SendMessageResult> =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/message`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text,
      ...(attachments?.length ? { attachments } : {}) }),
  }).then(j)
export const saveSettings = (slug: string, opts: SettingsRequest = {}): Promise<SettingsResult> =>
  fetch(u(`/api/orgs/${slug}/settings`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  }).then(j)
export const saveHireDefaults = (
  slug: string, opts: HireDefaultsRequest = {},
): Promise<OpResult> =>
  fetch(u(`/api/orgs/${slug}/defaults`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  }).then(j)
export const saveKiosk = (slug: string, opts: KioskCfgRequest = {}): Promise<KioskSaveResult> =>
  fetch(u(`/api/orgs/${slug}/kiosk`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  }).then(j)

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
