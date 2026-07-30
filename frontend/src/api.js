const j = (r) => {
  if (!r.ok) return r.json().then((b) => { throw new Error(b.detail || r.statusText) })
  return r.json()
}

export const listOrgs = () => fetch('/api/orgs').then(j)
export const createOrg = (name, dirs) =>
  fetch('/api/orgs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, dirs }),
  }).then(j)
export const getTree = (slug) => fetch(`/api/orgs/${slug}`).then(j)
export const deleteOrg = (slug) =>
  fetch(`/api/orgs/${slug}`, { method: 'DELETE' }).then(j)
export const runOp = (slug, body) =>
  fetch(`/api/orgs/${slug}/ops`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j)

export const getChat = (slug, nid) => fetch(`/api/orgs/${slug}/nodes/${nid}/chat`).then(j)
export const getMcpServers = () => fetch('/api/mcp-servers').then(j)
export const getInbox = (slug) => fetch(`/api/orgs/${slug}/inbox`).then(j)
export const clearInbox = (slug) =>
  fetch(`/api/orgs/${slug}/inbox/clear`, { method: 'POST' }).then(j)
export const getHistory = (slug, nid) =>
  fetch(`/api/orgs/${slug}/nodes/${nid}/history`).then(j)
export const getScratch = (slug, nid, path = '') =>
  fetch(`/api/orgs/${slug}/nodes/${nid}/scratch?path=${encodeURIComponent(path)}`).then(j)
export const getOrgMd = (slug) => fetch(`/api/orgs/${slug}/orgmd`).then(j)
export const putOrgMd = (slug, content) =>
  fetch(`/api/orgs/${slug}/orgmd`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  }).then(j)
export const setAttached = (slug, nid, attached) =>
  fetch(`/api/orgs/${slug}/nodes/${nid}/attach`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attached }),
  }).then(j)
export const getAudiences = (slug) => fetch(`/api/orgs/${slug}/audiences`).then(j)
export const audienceAction = (slug, action, node, target) =>
  fetch(`/api/orgs/${slug}/audiences`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, node, target }),
  }).then(j)
export const saveScope = (slug, nid, scope) =>
  fetch(`/api/orgs/${slug}/nodes/${nid}/scope`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(scope),
  }).then(j)
export const reorderNode = (slug, nid, body) =>
  fetch(`/api/orgs/${slug}/nodes/${nid}/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(j)
export const sendMessage = (slug, nid, text) =>
  fetch(`/api/orgs/${slug}/nodes/${nid}/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  }).then(j)
export const saveSettings = (slug, extra_dirs, max_top_grant, clear_fable_lock = false,
  fable_limit_policy = undefined, default_tools = undefined) =>
  fetch(`/api/orgs/${slug}/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ extra_dirs, max_top_grant, clear_fable_lock, fable_limit_policy,
      default_tools }),
  }).then(j)

export function openWs(slug, onChanged) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/orgs/${slug}/ws`)
  ws.onmessage = onChanged
  const ping = setInterval(() => { if (ws.readyState === 1) ws.send('ping') }, 25000)
  ws.onclose = () => clearInterval(ping)
  return ws
}
