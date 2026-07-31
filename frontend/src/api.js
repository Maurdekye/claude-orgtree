// kiosk v2: when the SPA is served from a preauthenticated public URL
// (/k/<token>/…), every API call and the WS must carry the token prefix —
// the public listener serves nothing outside it.
export const BASE = (location.pathname.match(/^\/k\/[A-Za-z0-9_-]+/) || [''])[0]
const u = (p) => BASE + p

const j = (r) => {
  if (!r.ok) return r.json().then((b) => { throw new Error(b.detail || r.statusText) })
  return r.json()
}

export const listOrgs = () => fetch(u('/api/orgs')).then(j)
export const createOrg = (name, dirs, kiosk = null, sandbox = false) =>
  fetch(u('/api/orgs'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name, dirs, ...(kiosk ? { kiosk } : {}),
      ...(sandbox && !kiosk ? { sandbox: true } : {}),
    }),
  }).then(j)
export const getTree = (slug) => fetch(u(`/api/orgs/${slug}`)).then(j)
export const deleteOrg = (slug) =>
  fetch(u(`/api/orgs/${slug}`), { method: 'DELETE' }).then(j)
export const runOp = (slug, body) =>
  fetch(u(`/api/orgs/${slug}/ops`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j)

export const getChat = (slug, nid, last) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/chat${last ? `?last=${last}` : ''}`)).then(j)
export const getMcpServers = () => fetch(u('/api/mcp-servers')).then(j)
export const getCharters = () => fetch(u('/api/charters')).then(j)
export const getFs = (path = '') =>
  fetch(u(`/api/fs?path=${encodeURIComponent(path)}`)).then(j)
export const getInbox = (slug) => fetch(u(`/api/orgs/${slug}/inbox`)).then(j)
export const getNodeInbox = (slug, nid) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/inbox`)).then(j)
export const resumeFrozen = (slug) =>
  fetch(u(`/api/orgs/${slug}/resume`), { method: 'POST' }).then(j)
export const killAll = (slug) =>
  fetch(u(`/api/orgs/${slug}/killswitch`), { method: 'POST' }).then(j)
export const dissolveAll = (slug) =>
  fetch(u(`/api/orgs/${slug}/dissolve-all`), { method: 'POST' }).then(j)
export const interruptNode = (slug, nid) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/interrupt`), { method: 'POST' }).then(j)
export const compactNode = (slug, nid) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/compact`), { method: 'POST' }).then(j)
export const creditDecide = (slug, id, action) =>
  fetch(u(`/api/orgs/${slug}/credit-requests`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, action }),
  }).then(j)
export const clearInbox = (slug) =>
  fetch(u(`/api/orgs/${slug}/inbox/clear`), { method: 'POST' }).then(j)
export const markRead = (slug, ids) =>
  fetch(u(`/api/orgs/${slug}/inbox/read`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  }).then(j)
export const getHistory = (slug, nid) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/history`)).then(j)
export const getScratch = (slug, nid, path = '') =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/scratch?path=${encodeURIComponent(path)}`)).then(j)
export const getOrgMd = (slug) => fetch(u(`/api/orgs/${slug}/orgmd`)).then(j)
export const putOrgMd = (slug, content) =>
  fetch(u(`/api/orgs/${slug}/orgmd`), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  }).then(j)
export const getAudiences = (slug) => fetch(u(`/api/orgs/${slug}/audiences`)).then(j)
export const audienceAction = (slug, action, node, target) =>
  fetch(u(`/api/orgs/${slug}/audiences`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, node, target }),
  }).then(j)
export const getHost = () => fetch(u('/api/host')).then(j)
export const getDefaults = () => fetch(u('/api/defaults')).then(j)
export const saveDefaults = (body) =>
  fetch(u('/api/defaults'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j)
export const orgInboxRead = (slug) =>
  fetch(u(`/api/orgs/${slug}/org_inbox/read`), { method: 'POST' }).then(j)
export const saveScope = (slug, nid, scope) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/scope`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(scope),
  }).then(j)
export const reorderNode = (slug, nid, body) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/reorder`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j)
export const getEvents = (slug) => fetch(u(`/api/orgs/${slug}/events`)).then(j)
export const retractMail = (slug, nid, mid) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/mail/${mid}`), { method: 'DELETE' }).then(j)
export const uploadFile = (slug, nid, file) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/upload?name=${encodeURIComponent(file.name)}`), {
    method: 'POST', body: file,
  }).then(j)
// direct <a href> download target (browser handles the transfer) — BASE-aware
// so kiosk visitors download through their token prefix
export const fileUrl = (slug, nid, path) =>
  u(`/api/orgs/${slug}/nodes/${nid}/file?path=${encodeURIComponent(path)}`)
export const sendMessage = (slug, nid, text, attachments) =>
  fetch(u(`/api/orgs/${slug}/nodes/${nid}/message`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text,
      ...(attachments?.length ? { attachments } : {}) }),
  }).then(j)
export const saveSettings = (slug, opts = {}) =>
  fetch(u(`/api/orgs/${slug}/settings`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  }).then(j)
export const saveHireDefaults = (slug, opts = {}) =>
  fetch(u(`/api/orgs/${slug}/defaults`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  }).then(j)
export const saveKiosk = (slug, opts = {}) =>
  fetch(u(`/api/orgs/${slug}/kiosk`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  }).then(j)

export function openWs(slug, onChanged, onClose) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}${BASE}/api/orgs/${slug}/ws`)
  ws.onmessage = onChanged
  const ping = setInterval(() => { if (ws.readyState === 1) ws.send('ping') }, 25000)
  ws.onclose = () => { clearInterval(ping); onClose?.() }
  return ws
}
