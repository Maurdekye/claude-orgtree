// canvas/modals.tsx — the config modals: the in-page ConfirmModal, the org
// agent-hire defaults panel (UserConfig), the pre-hire permissions modal
// (DraftScopeModal), the per-node ⚙ config (NodeConfig) with the shared MCP
// checklist, and the retired/crowd pile picker. Extracted verbatim from
// Canvas.tsx in the phase-3 split.

import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type {
  ChatInit, DirGrant, ToastFn, ToolGrant, TreePayload,
} from '../types'
import {
  dissolveAll, getChat, getMcpServers, remoteControl, saveHireDefaults,
  saveScope, saveSettings,
} from '../api'
import { pickFolder } from '../picker'
import {
  CloseIcon, DeleteIcon, FolderIcon, LayersIcon, SettingsIcon,
} from '../icons'
import { MODEL_VERSIONS, TIER_LETTER, TIER_SEAT, TIERS, USER, useEsc } from './shared'
import type { CanvasNode, DraftScope, DraftState, OpFn, Pile } from './shared'

export interface ConfirmModalProps {
  title: ReactNode
  body?: ReactNode
  confirmLabel: ReactNode
  onConfirm: () => void
  close: () => void
}

// in-page confirmation (user ruling: never a native OS dialog)
export function ConfirmModal({ title, body, confirmLabel, onConfirm, close }: ConfirmModalProps) {
  useEsc(close)
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings confirm-box" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {body && <div className="confirm-body">{body}</div>}
        <div className="row">
          <button className="danger solid"
            onClick={() => { close(); onConfirm() }}>{confirmLabel}</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>
  )
}
// ⚙ on the overseer — the org's agent-hire defaults, symmetric with each
// agent's own config modal. Granted to hires that don't state tools: top-level
// agents get exactly this; deeper hires get the ∩ with the superior's
// capability (clamped server-side at hire time). "*" = every registered MCP
// server, present and future.
interface UserConfigProps {
  tree: TreePayload
  slug: string
  toast: ToastFn
  close: () => void
}

export function UserConfig({ tree, slug, toast, close }: UserConfigProps) {
  useEsc(close)
  // visitors configure the HIRE DEFAULTS too (user ruling 2026-07-31),
  // ceiling-clamped server-side; the org folder holdings stay admin-only
  // (host paths — the public payload only carries basenames anyway)
  const pub = !!tree.public
  const [asking, setAsking] = useState(false)   // dissolve-all confirmation
  const [servers, setServers] = useState<string[]>([])
  const [sandboxMcp, setSandboxMcp] = useState(false)
  // P3: derived from `tree`, with a buffer holding only what has been edited
  // — see NodeConfig for the reasoning. These are org DEFAULTS the server
  // owns, so a snapshot at mount could show yesterday's answer.
  const [edit, setEdit] = useState<Record<string, unknown>>({})
  const val = <T,>(k: string, server: T): T => (k in edit ? edit[k] as T : server)
  const set = <T,>(k: string, cur: T) => (v: T | ((prev: T) => T)) =>
    setEdit((e) => ({ ...e,
      [k]: typeof v === 'function' ? (v as (p: T) => T)(cur) : v }))
  const srvTools = useMemo<ToolGrant>(() => ({
    bash: true, web: true, edit: true, subagents: true,
    ...(tree.default_tools ?? {}),
    mcp: [...(tree.default_tools?.mcp ?? ['*'])],
  }), [tree.default_tools])
  const defTools = val<ToolGrant>('defTools', srvTools)
  const setDefTools = set<ToolGrant>('defTools', defTools)
  const vis = val('vis', tree.default_visibility ?? 'full')
  const setVis = set<string>('vis', vis)
  const pm = val('pm', tree.permission_mode ?? 'acceptEdits')
  const setPm = set<string>('pm', pm)
  // the org's folder holdings (workspace excluded — it is permanent RW).
  // These double as the folder defaults for every hire.
  const srvDirs = useMemo<DirGrant[]>(
    () => (tree.dirs ?? []).filter((d) => d.path !== tree.workspace)
      .map((d) => ({ ...d })), [tree.dirs, tree.workspace])
  const orgDirs = val<DirGrant[]>('orgDirs', srvDirs)
  const setOrgDirs = set<DirGrant[]>('orgDirs', orgDirs)
  const [newPath, setNewPath] = useState('')
  useEffect(() => {
    getMcpServers().then((r) => {
      setServers(r.servers ?? []); setSandboxMcp(!!r.sandbox_mcp)
    }).catch(() => {})
  }, [])
  const allMcp = defTools.mcp.includes('*')
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> you <span className="dim">· configuration</span></h3>
        <div className="row">
          <button className="danger" onClick={() => setAsking(true)}>
            dissolve all agents</button>
        </div>
        {/* folder access FIRST — same order as the per-agent config (user ruling) */}
        {!pub && <><div className="field-label">folder access</div>
        <div className="dirlist">
          {tree.workspace && (
            <div className="dirrow">
              <span className="chip mono grow">{tree.workspace}</span>
              <span className="modebtn rw"
                title="the org workspace — permanent, always read/write">RW</span>
            </div>
          )}
          {orgDirs.map((d, i) => (
            <div className="dirrow" key={d.path}>
              <span className="chip mono grow">{d.path}</span>
              <button type="button" className={'modebtn ' + d.mode}
                title="toggle read/write vs read-only"
                onClick={() => setOrgDirs(orgDirs.map((x, j) =>
                  j === i ? { ...x, mode: x.mode === 'rw' ? 'ro' : 'rw' } : x))}>
                {d.mode === 'rw' ? 'RW' : 'RO'}
              </button>
              <button type="button" className="iconbtn"
                title="remove from the org (revokes everywhere)"
                onClick={() => setOrgDirs(orgDirs.filter((_, j) => j !== i))}><CloseIcon fontSize="inherit" /></button>
            </div>
          ))}
          <div className="dirrow">
            <input placeholder="add an absolute path"
              value={newPath} onChange={(e) => setNewPath(e.target.value)} />
            <button type="button" className="iconbtn" title="browse for a folder"
              onClick={() => pickFolder().then((r) => {
                if (r.path) setOrgDirs([...orgDirs, { path: r.path, mode: 'rw' }])
              }).catch(() => {})}><FolderIcon fontSize="inherit" /></button>
            <button type="button" className="addrow" onClick={() => {
              if (newPath.trim()) {
                setOrgDirs([...orgDirs, { path: newPath.trim(), mode: 'rw' }])
                setNewPath('')
              }
            }}>add</button>
          </div>
        </div></>}
        <div className="field-label">tools</div>
        {TOOL_LABELS.map(([k, label]) => (
          <label className="checkline" key={k}>
            <input type="checkbox" checked={!!defTools[k]}
              onChange={(e) => setDefTools({ ...defTools, [k]: e.target.checked })} />
            {label}
          </label>
        ))}
        <div className="field-label">MCP servers</div>
        <label className="checkline">
          <input type="checkbox" checked={allMcp}
            onChange={(e) => setDefTools({
              ...defTools, mcp: e.target.checked ? ['*'] : [...servers] })} />
          all registered servers (current and future)
        </label>
        {!allMcp && !pub && <McpChecklist servers={servers} sandboxMcp={sandboxMcp}
          sandboxed={!!tree.sandboxed}
          checked={(s) => defTools.mcp.includes(s)}
          onToggle={(s, on) => setDefTools({
            ...defTools,
            mcp: on ? [...defTools.mcp, s] : defTools.mcp.filter((x) => x !== s),
          })} />}
        {!allMcp && pub && <div className="dim">
          individual server names are admin-side — off means none</div>}
        <div className="field-label">org-structure visibility</div>
        <select value={vis} onChange={(e) => setVis(e.target.value)}>
          {VIS_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        {/* D-100: the born-with mode, editable post-creation. Admin-only —
            it rides /settings (frozen for kiosk visitors), never the
            visitor-open defaults endpoint. It is a DEFAULT: existing agents
            keep the mode they were hired with and change one at a time in
            their own ⚙. */}
        {!pub && <>
          <div className="field-label">permission mode for NEW agents — existing
            ones keep theirs (change those in the agent&apos;s own ⚙)</div>
          <select value={pm} onChange={(e) => setPm(e.target.value)}>
            <option value="default">default — asks (headless: auto-denies)</option>
            <option value="acceptEdits">acceptEdits — the normal seat</option>
            <option value="bypassPermissions">bypassPermissions ⚠ unguarded</option>
          </select>
        </>}
        <div className="row">
          <button className="primary" onClick={() =>
            // defaults ride their own visitor-open, ceiling-clamped endpoint;
            // the org folder holdings stay on the admin-only /settings
            Promise.all([
              saveHireDefaults(slug, { default_tools: defTools,
                                       default_visibility: vis }),
              pub ? Promise.resolve<{ warnings?: string[] }>({})
                : saveSettings(slug, { org_dirs: orgDirs,
                                       permission_mode: pm }),
            ])
              .then(([r, r2]) => {
                const warns = [...(r.warnings ?? []), ...(r2.warnings ?? [])]
                if (r?.bridge?.raise_ceiling) {
                  toast(warns.length ? warns
                    : ['clamped to the kiosk permission ceiling'],
                  { label: 'raise ceiling & apply',
                    fn: () => saveHireDefaults(slug,
                      { default_tools: defTools, default_visibility: vis,
                        raise_ceiling: true })
                      .then((r3) => toast(r3.warnings?.length ? r3.warnings
                        : ['ceiling raised — defaults applied']))
                      .catch((e: Error) => toast([`error: ${e.message}`])) })
                } else toast(warns)
                close()
              })
              .catch((e: Error) => toast([`error: ${e.message}`]))}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
      {asking && (
        <ConfirmModal title="dissolve ALL agents?"
          body="Every agent in the entire org is retired at once. Context is kept; rehire brings any of them back."
          confirmLabel="dissolve all"
          onConfirm={() => dissolveAll(slug)
            .then((r) => { toast([`dissolved ${r.nodes} node(s), freed ${r.freed} credits`]); close() })
            .catch((e: Error) => toast([`error: ${e.message}`]))}
          close={() => setAsking(false)} />
      )}
    </div>
  )
}
// Pre-hire permissions (user spec): the same scope surface as the per-agent
// ⚙ panel — folders with RW/RO, tool switches, MCP grants, org visibility —
// but staged locally and applied WITH the hire, so nothing needs adjusting
// after the agent exists. Prefilled from what the hire would inherit anyway.
interface DraftScopeModalProps {
  draft: DraftState
  map: Map<string, CanvasNode>
  tree: TreePayload
  scope: DraftScope | null
  onSave: (scope: DraftScope) => void
  close: () => void
}

export function DraftScopeModal({ draft, map, tree, scope, onSave, close }: DraftScopeModalProps) {
  useEsc(close)
  const parent = draft.parent ? map.get(draft.parent) : null
  const inherited = (): DraftScope => ({
    add_dirs: (parent ? parent.scope?.add_dirs : tree.dirs) ?? [],
    tools: parent?.scope?.tools
      ?? tree.default_tools
      ?? { bash: true, web: true, edit: true, subagents: true, mcp: ['*'] },
    org_visibility: parent?.scope?.org_visibility
      ?? tree.default_visibility ?? 'full',
  })
  const base = scope ?? inherited()
  // ⚠ These four ARE useState-from-a-prop and are deliberately left that way.
  // This modal stages permissions for an agent that DOES NOT EXIST YET: `base`
  // is a one-time proposal (what the hire would inherit), not a live server
  // value with an authoritative copy elsewhere. Re-deriving it mid-edit would
  // overwrite the user's staged choices from a default they were changing.
  // A snapshot is the correct shape here; the P3 sweep skipped it on purpose.
  const [dirs, setDirs] = useState<DirGrant[]>(base.add_dirs.map((d) => ({ ...d })))
  const [tools, setTools] = useState<Partial<ToolGrant> & { mcp: string[] }>(
    { ...base.tools, mcp: [...(base.tools.mcp ?? [])] })
  const [vis, setVis] = useState(base.org_visibility)
  const [effort, setEffort] = useState(base.effort ?? '')
  const [newPath, setNewPath] = useState('')
  const [servers, setServers] = useState<string[]>([])
  const [sandboxMcp, setSandboxMcp] = useState(false)
  useEffect(() => {
    getMcpServers().then((r) => {
      setServers(r.servers ?? []); setSandboxMcp(!!r.sandbox_mcp)
    }).catch(() => {})
  }, [])
  const allMcp = tools.mcp.includes('*')
  // portal to <body>: the draft card lives inside the world transform, where
  // position:fixed would resolve against the SCALED ancestor (giant modal)
  return createPortal(
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> permissions <span className="dim">
          · applied with the hire</span></h3>
        <div className="field-label">folder access</div>
        <div className="dirlist">
          {dirs.map((d, i) => (
            <div className="dirrow" key={d.path}>
              <span className="chip mono grow">{d.path}</span>
              <button type="button" className={'modebtn ' + d.mode}
                title="toggle read/write vs read-only"
                onClick={() => setDirs(dirs.map((x, j) =>
                  j === i ? { ...x, mode: x.mode === 'rw' ? 'ro' : 'rw' } : x))}>
                {d.mode === 'rw' ? 'RW' : 'RO'}
              </button>
              <button type="button" className="iconbtn"
                onClick={() => setDirs(dirs.filter((_, j) => j !== i))}>
                <CloseIcon fontSize="inherit" /></button>
            </div>
          ))}
          <div className="dirrow">
            <input placeholder="add an absolute path"
              value={newPath} onChange={(e) => setNewPath(e.target.value)} />
            <button type="button" className="iconbtn" title="browse for a folder"
              onClick={() => pickFolder().then((r) => {
                if (r.path) setDirs([...dirs, { path: r.path, mode: 'rw' }])
              }).catch(() => {})}><FolderIcon fontSize="inherit" /></button>
            <button type="button" className="addrow" onClick={() => {
              if (newPath.trim()) {
                setDirs([...dirs, { path: newPath.trim(), mode: 'rw' }])
                setNewPath('')
              }
            }}>add</button>
          </div>
        </div>
        <div className="field-label">tools</div>
        {TOOL_LABELS.map(([k, label]) => (
          <label className="checkline" key={k}>
            <input type="checkbox" checked={!!tools[k]}
              onChange={(e) => setTools({ ...tools, [k]: e.target.checked })} />
            {label}
          </label>
        ))}
        <div className="field-label">MCP servers</div>
        <label className="checkline">
          <input type="checkbox" checked={allMcp}
            onChange={(e) => setTools({
              ...tools, mcp: e.target.checked ? ['*'] : [...servers] })} />
          all registered servers (current and future)
        </label>
        {!allMcp && <McpChecklist servers={servers} sandboxMcp={sandboxMcp}
          sandboxed={!!tree.sandboxed}
          checked={(s) => tools.mcp.includes(s)}
          onToggle={(s, on) => setTools({
            ...tools,
            mcp: on ? [...tools.mcp, s] : tools.mcp.filter((x) => x !== s),
          })} />}
        <div className="field-label">org-structure visibility</div>
        <select value={vis} onChange={(e) => setVis(e.target.value)}>
          {VIS_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <div className="field-label">thinking effort</div>
        <select value={effort} onChange={(e) => setEffort(e.target.value)}>
          <option value="">{`inherit — org default (${tree.default_effort || tree.effort_default || 'high'})`}</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="xhigh">xhigh</option>
          <option value="max">max</option>
        </select>
        <div className="hint">
          Grants clamp to what the parent holds (№30) — anything beyond its
          capability is trimmed at hire with a warning.
        </div>
        <div className="row">
          <button className="primary" onClick={() =>
            onSave({ add_dirs: dirs, tools, org_visibility: vis,
              ...(effort ? { effort } : {}) })}>apply</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
    </div>,
    document.body
  )
}
// ------------------------------------------------------------ node ⚙ config
// MCP server checklist shared by the org / per-agent / pre-hire scope panels.
// In a SANDBOXED org, ALL servers grey out (user ruling): they are points of
// external contact the sandbox is explicitly designed to restrict. The
// experimental ORGTREE_SANDBOX_MCP env var re-enables them (url + portable
// stdio passthrough, no full support).
interface McpChecklistProps {
  servers: string[]
  sandboxed: boolean
  sandboxMcp: boolean
  checked: (s: string) => boolean
  onToggle: (s: string, on: boolean) => void
}

function McpChecklist({ servers, sandboxed, sandboxMcp, checked, onToggle }: McpChecklistProps) {
  const dead = sandboxed && !sandboxMcp
  return (
    <>
      {dead && (
        <div className="hint">
          sandboxed org — MCP servers are external contact points the sandbox
          restricts, so none reach its agents (the ORGTREE_SANDBOX_MCP env var
          enables URL/portable servers experimentally)
        </div>
      )}
      {servers.map((s) => (
        <label className={'checkline' + (dead ? ' dead' : '')} key={s}
          title={dead ? 'unavailable in a sandboxed org' : undefined}>
          <input type="checkbox" disabled={dead} checked={checked(s)}
            onChange={(e) => onToggle(s, e.target.checked)} />
          <span className="mono">{s}</span>
        </label>
      ))}
    </>
  )
}

const VIS_OPTIONS = [
  ['self', 'self'],
  ['team', 'team'],
  ['subtree', 'subtree'],
  ['full', 'full (default)'],
] as const

/* D-106 (user ruling 2026-08-07): a grant deeper than the chain can carry no
 * longer refuses — every agent BETWEEN the granter and the grantee is raised
 * to hold it. The user is the granter here, so the chain is every ancestor of
 * the target, and they asked to be WARNED BEFORE saving rather than told
 * after: which agents this grant is about to expand, and in what.
 *
 * Computed client-side from the tree the panel already has — the same union
 * the ledger performs (ledger._raise_along), so the preview and the outcome
 * are one rule expressed twice. ⚠ That duplication is the risk: if the two
 * drift, the warning lies. It is worth it because the alternative is a
 * round-trip on every keystroke, but the ledger stays the authority — its
 * answer, `cascaded`, is what the toast reports after the fact. */
const PM_RANK = ['default', 'acceptEdits', 'bypassPermissions']
const VIS_RANK = ['self', 'team', 'subtree', 'full']

export function cascadePreview(
  map: Map<string, CanvasNode>, nodeId: string, want: {
    dirs?: DirGrant[]; tools?: ToolGrant; vis?: string; pm?: string
  },
): { id: string; gains: string[] }[] {
  const out: { id: string; gains: string[] }[] = []
  let cur = map.get(nodeId)?.parent ?? null
  while (cur && cur !== USER) {
    const n = map.get(cur)
    if (!n?.scope) break
    const gains: string[] = []
    if (want.dirs) {
      const held = new Map((n.scope.add_dirs ?? []).map((d) => [d.path, d.mode]))
      for (const d of want.dirs) {
        if (!held.has(d.path)) gains.push(`${d.path} ${d.mode}`)
        else if (held.get(d.path) === 'ro' && d.mode === 'rw') gains.push(`${d.path} ro→rw`)
      }
    }
    if (want.tools) {
      for (const k of ['bash', 'web', 'edit', 'subagents'] as const) {
        if (want.tools[k] && !(n.scope.tools as Record<string, unknown>)?.[k]) gains.push(k)
      }
      const have = (n.scope.tools?.mcp ?? []) as string[]
      const wm = want.tools.mcp ?? []
      if (wm.includes('*') && !have.includes('*')) gains.push('mcp:*')
      else if (!have.includes('*')) {
        for (const s of wm) if (!have.includes(s)) gains.push(`mcp:${s}`)
      }
    }
    if (want.vis) {
      const c = n.scope.org_visibility ?? 'full'
      if (VIS_RANK.indexOf(want.vis) > VIS_RANK.indexOf(c)) gains.push(`visibility ${c}→${want.vis}`)
    }
    if (want.pm) {
      const c = n.scope.permission_mode ?? 'acceptEdits'
      if (PM_RANK.indexOf(want.pm) > PM_RANK.indexOf(c)) gains.push(`mode ${c}→${want.pm}`)
    }
    if (gains.length) out.push({ id: cur, gains })
    cur = n.parent ?? null
  }
  return out
}

const TOOL_LABELS = [
  ['bash', 'terminal (Bash)'],
  ['web', 'web browsing (search + fetch)'],
  ['edit', 'file editing (Write / Edit / notebooks)'],
  ['subagents', 'ephemeral subagents (Task / Agent tool)'],
] as const
interface NodeConfigProps {
  node: CanvasNode
  map: Map<string, CanvasNode>
  tree: TreePayload
  slug: string
  op: OpFn
  toast: ToastFn
  close: () => void
}

export function NodeConfig({ node, map, tree, slug, op, toast, close }: NodeConfigProps) {
  useEsc(close)
  const [asking, setAsking] =
    useState<'delete' | 'dissolve' | 'retire' | null>(null)
  // every card that opens a config panel carries a scope (real nodes and
  // bearer stubs both) — only the eye root and drafts lack one
  const scope = node.scope!
  // P3 — these seven were each a useState SEEDED FROM `node`/`scope`, i.e. a
  // snapshot taken once at mount that never looked at the prop again. That is
  // what produced a config panel showing an empty charter: the panel had
  // captured the node before it carried one. One buffer of ACTUAL EDITS now;
  // everything else derives from the prop each render, so the panel cannot
  // drift from the agent it is configuring. The shadowing pairs below keep
  // every use site below unchanged.
  const [edit, setEdit] = useState<Record<string, unknown>>({})
  const val = <T,>(k: string, server: T): T => (k in edit ? edit[k] as T : server)
  const set = <T,>(k: string, cur: T) => (v: T | ((prev: T) => T)) =>
    setEdit((e) => ({ ...e,
      [k]: typeof v === 'function' ? (v as (p: T) => T)(cur) : v }))
  const srvDirs = useMemo(
    () => scope.add_dirs.map((d) => ({ ...d })), [scope.add_dirs])
  const srvTools = useMemo<ToolGrant>(() => ({
    bash: true, web: true, edit: true, subagents: true,
    ...(scope.tools ?? {}),
    mcp: [...(scope.tools?.mcp ?? [])],
  }), [scope.tools])
  const dirs = val<DirGrant[]>('dirs', srvDirs)
  const setDirs = set<DirGrant[]>('dirs', dirs)
  const tools = val<ToolGrant>('tools', srvTools)
  const setTools = set<ToolGrant>('tools', tools)
  const vis = val('vis', scope.org_visibility ?? 'full')
  const setVis = set<string>('vis', vis)
  const charter = val('charter', node.charter ?? '')
  const setCharter = set<string>('charter', charter)
  const teamCharter = val('teamCharter', node.team_charter ?? '')
  const setTeamCharter = set<string>('teamCharter', teamCharter)
  const model = val('model', node.tier!)
  const setModel = set<string>('model', model)
  const effort = val('effort', scope.effort ?? '')
  const setEffort = set<string>('effort', effort)
  const pm = val('pm', scope.permission_mode ?? 'acceptEdits')
  const setPm = set<string>('pm', pm)
  // D-106: who this pending grant would raise, recomputed as the form changes
  const cascade = useMemo(
    () => cascadePreview(map, node.id,
      { dirs, tools, vis, pm }), [map, node.id, dirs, tools, vis, pm])
  // a model VERSION is a subcategory of the TIER, so it lives here in the gear
  // and never on a chip (user ruling 2026-08-04). It resets when the tier
  // changes: a version belongs to one tier, and the ledger re-validates it
  // against the node's current tier on every read anyway.
  const modelVersion = val('modelVersion', scope.model_version ?? '')
  const setModelVersion = set<string>('modelVersion', modelVersion)
  const versions = MODEL_VERSIONS[model] ?? []
  const [newPath, setNewPath] = useState('')
  const [servers, setServers] = useState<string[]>([])
  const [sandboxMcp, setSandboxMcp] = useState(false)
  const [initInfo, setInitInfo] = useState<ChatInit | null>(null)   // №14: the CLI's own resolution
  useEffect(() => {
    getMcpServers().then((r) => {
      setServers(r.servers ?? []); setSandboxMcp(!!r.sandbox_mcp)
    }).catch(() => {})
    getChat(slug, node.id, 1).then((c) => setInitInfo(c.init ?? null))
      .catch(() => {})
  }, [slug, node.id])
  const parent = map.get(node.id)?.parent
  const parentNode = parent && parent !== USER ? map.get(parent) : null
  const parentTools = parentNode?.scope?.tools ?? null   // null = the user: everything
  const parentDirs = parentNode
    ? (parentNode.scope?.add_dirs ?? [])
    : (tree.dirs ?? []).map((d) => ({ ...d }))   // org holdings carry modes now
  const addable = parentDirs.filter((pd) => !dirs.some((d) => d.path === pd.path))
  const parentHolds = (k: 'bash' | 'web' | 'edit' | 'subagents') =>
    parentTools == null || parentTools[k] !== false
  // "*" = every registered server, present and future
  const parentHoldsMcp = (s: string) => parentTools == null
    || (parentTools.mcp ?? []).includes('*') || (parentTools.mcp ?? []).includes(s)
  const holdsAllMcp = tools.mcp.includes('*')
  return (
    // pointerdown must not reach the viewport: its pan pointer-CAPTURE retargets
    // the click, so backdrop-close and every button in here silently broke
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings cfg" onClick={(e) => e.stopPropagation()}>
        <h3><SettingsIcon fontSize="inherit" /> {node.id} <span className="dim">· {node.tier} · configuration</span></h3>
        {/* FULL identity rename (user ruling 2026-08-05): id, mailbox,
            working folder and session all move; history keeps the old name
            (the warning rides the toast). Refused while mid-turn. */}
        {!node.isBearerOf && (
          <div className="row">
            <input style={{ width: '14em' }} placeholder="rename…"
              value={val('rename', node.id)}
              onChange={(e) => set('rename', node.id)(e.target.value)} />
            {val('rename', node.id) !== node.id && (
              <button onClick={() =>
                op({ op: 'rename', node: node.id,
                     name: String(val('rename', node.id)) })
                  .then((r) => {
                    toast([`renamed ${node.id} → ${String(r?.node ?? '')}`,
                           ...((r?.warnings as string[] | undefined) ?? [])])
                    close()
                  })
                  .catch((e: Error) => toast([`error: ${e.message}`]))}>
                rename</button>
            )}
          </div>
        )}

        <div className="row">
          {/* retire asks too (user bug 2026-08-09) — it sat as the one
              seat-freeing action firing straight off the click, beside a
              dissolve button that asks */}
          {node.state === 'live' && !node.children.some((c) => c.state !== 'archived') &&
            <button className="danger" onClick={() => setAsking('retire')}>
              retire · {node.seat! + node.grant!}</button>}
          {node.state === 'live' && node.children.some((c) => c.state !== 'archived') &&
            <button className="danger" onClick={() => setAsking('dissolve')}>
              dissolve subtree · {node.seat! + node.grant!}</button>}
          {node.state === 'archived' &&
            <button className="primary" onClick={() =>
              op({ op: 'rehire', node: node.id }).then(close).catch(() => {})}>
              rehire (context intact)</button>}
          <span style={{ flex: 1 }} />
          <button className="danger delete"
            onClick={() => setAsking('delete')}><DeleteIcon fontSize="inherit" /> delete permanently</button>
        </div>

        {/* FR-01: hand this agent's REAL session to claude.ai / the mobile
            app. Parked while controlled (mail queues); release resumes.
            Loopback-only server-side; hidden from kiosk visitors. */}
        {node.state === 'live' && !tree.public && !node.isBearerOf && (
          <div className="row" style={{ alignItems: 'center' }}>
            {node.remote_controlled ? (
              <>
                <span className="dim">under remote control — mail queues
                  until release</span>
                <button className="primary" onClick={() =>
                  remoteControl(slug, node.id, 'stop')
                    .then(() => toast([`${node.id} released`]))
                    .catch((e: Error) => toast([`error: ${e.message}`]))}>
                  release</button>
              </>
            ) : (
              <button title={'starts `claude remote-control` on this '
                + "agent's session — connect from claude.ai/code or the "
                + 'Claude mobile app; the agent is parked until release'}
                onClick={() =>
                  remoteControl(slug, node.id, 'start')
                    .then((r) => toast([r.note ?? 'remote control started']))
                    .catch((e: Error) => toast([`error: ${e.message}`]))}>
                remote control (claude.ai / mobile)</button>
            )}
          </div>
        )}

        <div className="field-label">folder access</div>
        <div className="dirlist">
          {dirs.map((d, i) => (
            <div className="dirrow" key={d.path}>
              <span className="chip mono grow">{d.path}</span>
              <button type="button" className={'modebtn ' + d.mode}
                title="toggle read/write vs read-only"
                onClick={() => setDirs(dirs.map((x, j) =>
                  j === i ? { ...x, mode: x.mode === 'rw' ? 'ro' : 'rw' } : x))}>
                {d.mode === 'rw' ? 'RW' : 'RO'}
              </button>
              <button type="button" className="iconbtn" title="revoke"
                onClick={() => setDirs(dirs.filter((_, j) => j !== i))}><CloseIcon fontSize="inherit" /></button>
            </div>
          ))}
          {addable.length > 0 && (
            <div className="dirrow">
              <select value="" onChange={(e) => {
                const pd = addable.find((x) => x.path === e.target.value)
                if (pd) setDirs([...dirs, { ...pd }])
              }}>
                <option value="" disabled>+ grant a folder the {parent && parent !== USER ? 'parent holds' : 'org holds'}…</option>
                {addable.map((pd) => <option key={pd.path} value={pd.path}>{pd.path} ({pd.mode})</option>)}
              </select>
            </div>
          )}
          {/* ⚠ this used to be gated on (!parent || parent === USER) — a free
              path could only be typed for a TOP-LEVEL node, because a deeper
              grant had to fit the parent. D-106 removed that constraint from
              the ledger (the chain is raised instead of the grant refused),
              and the user asked for new DIRECTORIES specifically, so the
              control has to be able to express it at any depth. Owner only:
              a kiosk visitor's grants clamp to the ceiling's folder list and
              their payload carries basenames, not host paths. */}
          {!tree.public && (
            <div className="dirrow">
              <input placeholder={parent && parent !== USER
                ? 'or any absolute path — superiors are raised to carry it'
                : 'or any absolute path (top-level: you grant freely)'}
                value={newPath} onChange={(e) => setNewPath(e.target.value)} />
              <button type="button" className="iconbtn" title="browse for a folder"
                onClick={() => pickFolder().then((r) => {
                  if (r.path) setDirs([...dirs, { path: r.path, mode: 'rw' }])
                }).catch(() => {})}><FolderIcon fontSize="inherit" /></button>
              <button type="button" className="addrow" onClick={() => {
                if (newPath.trim()) { setDirs([...dirs, { path: newPath.trim(), mode: 'rw' }]); setNewPath('') }
              }}>add</button>
            </div>
          )}
        </div>

        <div className="field-label">tools</div>
        {TOOL_LABELS.map(([k, label]) => (
          <label className="checkline" key={k}>
            <input type="checkbox" checked={tools[k] && parentHolds(k)}
              disabled={!parentHolds(k)}
              onChange={(e) => setTools({ ...tools, [k]: e.target.checked })} />
            {label}
            {!parentHolds(k) && <span className="dim"> — parent doesn't hold it</span>}
          </label>
        ))}

        <div className="field-label">MCP servers (from your global registry)</div>
        {servers.length === 0 && <div className="hint">none registered</div>}
        {!!tree.sandboxed && !sandboxMcp && servers.length > 0 && (
          <div className="hint">
            sandboxed org — MCP servers are external contact points the sandbox
            restricts, so none reach its agents (the ORGTREE_SANDBOX_MCP env
            var enables URL/portable servers experimentally)
          </div>
        )}
        {servers.map((s) => {
          const dead = !!tree.sandboxed && !sandboxMcp
          return (
            <label className={'checkline' + (dead ? ' dead' : '')} key={s}
              title={dead ? 'unavailable in a sandboxed org' : undefined}>
              <input type="checkbox"
                checked={(holdsAllMcp || tools.mcp.includes(s)) && parentHoldsMcp(s)}
                disabled={!parentHoldsMcp(s) || dead}
                onChange={(e) => setTools({
                  ...tools,
                  // unchecking under "*" materializes the concrete server list
                  mcp: e.target.checked
                    ? (holdsAllMcp ? tools.mcp : [...tools.mcp, s])
                    : (holdsAllMcp ? servers.filter((x) => x !== s)
                                   : tools.mcp.filter((x) => x !== s)),
                })} />
              <span className="mono">{s}</span>
              {!parentHoldsMcp(s) && <span className="dim"> — parent doesn't hold it</span>}
            </label>
          )
        })}

        <div className="field-label">model (switchable on the fly — context
          survives; cheaper frees the seat difference to the agent, pricier
          bubbles any shortfall up the chain)</div>
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          {/* kiosk tier cap: options above it vanish — but the node's OWN
              tier stays listed so a pre-cap agent still shows truthfully */}
          {TIERS.filter((t) => {
            const cap = tree.kiosk?.max_tier
            return t === node.tier || !cap
              || (TIER_SEAT[t] ?? 0) <= (TIER_SEAT[cap] ?? Infinity)
          }).map((t) => (
            <option key={t} value={t}>
              {t} · seat {TIER_SEAT[t]}
            </option>
          ))}
        </select>

        <div className="field-label">org-structure visibility</div>
        <select value={vis} onChange={(e) => setVis(e.target.value)}>
          {VIS_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>

        {versions.length > 1 && (
          <>
            <div className="field-label">model version — {model} runs the
              latest unless you pin one here</div>
            <select value={versions.includes(modelVersion) ? modelVersion : ''}
              onChange={(e) => setModelVersion(e.target.value)}>
              <option value="">{`latest (${versions[0]})`}</option>
              {versions.map((v) => (
                <option key={v} value={v}>{`${model} ${v}`}</option>
              ))}
            </select>
          </>
        )}

        <div className="field-label">thinking effort (user-approved: a deep
          setting, never a hire-row control)</div>
        <select value={effort} onChange={(e) => setEffort(e.target.value)}>
          <option value="">{`inherit — org default (${tree.default_effort || tree.effort_default || 'high'})`}</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
          <option value="xhigh">xhigh</option>
          <option value="max">max</option>
        </select>

        {/* user ruling 2026-08-07: writing the machine's GLOBAL skills is
            gated above every allow-rule and hook — only bypassPermissions
            clears it, so the mode had to become settable per node. It was
            reachable by API alone before this; agents still cannot set it
            (orgtree_retool does not expose it), so raising one is the
            user's act. */}
        <div className="field-label">permission mode — bypassPermissions is
          the ONLY mode that can write ~/.claude/skills, and it removes this
          agent&apos;s prompts for everything else too</div>
        <select value={pm} onChange={(e) => setPm(e.target.value)}>
          <option value="default">default — asks (headless: auto-denies)</option>
          <option value="acceptEdits">acceptEdits — the normal seat</option>
          <option value="bypassPermissions">bypassPermissions ⚠ unguarded</option>
        </select>

        <div className="field-label">charter</div>
        <textarea rows={10} className="charterbox" value={charter}
          onChange={(e) => setCharter(e.target.value)} />
        <div className="field-label">team charter</div>
        <textarea rows={10} className="charterbox" value={teamCharter}
          onChange={(e) => setTeamCharter(e.target.value)} />
        {initInfo && (
          <>
            <div className="field-label">this turn, as the CLI resolved it (№14)</div>
            <div className="initblock dim">
              <div>model {initInfo.model ?? '?'} · {initInfo.permissionMode ?? '?'}
                {' · '}{initInfo.tools ?? '?'} tools</div>
              {(initInfo.mcp_servers ?? []).map((s) => (
                <div key={s.name}>
                  <span className={'mcpdot ' + (s.status === 'connected'
                    ? 'ok' : 'bad')} /> {s.name} · {s.status}
                </div>))}
            </div>
          </>
        )}
        {/* D-106: the cascade preview, BEFORE the save (user ruling) — the
            grant is legal either way, so this warns, never blocks */}
        {cascade.length > 0 && (
          <div className="cascade-warn" title={cascade.map((c) =>
            `${c.id} gains ${c.gains.join(', ')}`).join('\n')}>
            ⚠ this also raises {cascade.length === 1 ? 'the agent' : 'the agents'}
            {' '}between you and {node.id}:{' '}
            <b>{cascade.map((c) => c.id).join(', ')}</b>
            {' — hover for exactly what each one gains'}
          </div>
        )}
        <div className="row">
          <button className="primary" onClick={() =>
            (model !== node.tier
              ? op({ op: 'switch_model', node: node.id, tier: model })
              : Promise.resolve())
              .then(() => saveScope(slug, node.id,
                { add_dirs: dirs, tools, org_visibility: vis,
                  permission_mode: pm,
                  charter, team_charter: teamCharter, effort,
                  model_version: versions.includes(modelVersion)
                    ? modelVersion : '' }))
              .then((r) => {
                if (r?.bridge?.raise_ceiling) {
                  // one-action bridge (ceiling spec §1): same save, flag set
                  toast(r.warnings?.length ? r.warnings
                    : ['clamped to the kiosk permission ceiling'],
                  { label: 'raise ceiling & apply',
                    fn: () => saveScope(slug, node.id,
                      { add_dirs: dirs, tools, org_visibility: vis,
                        permission_mode: pm,
                        charter, team_charter: teamCharter, effort,
                        model_version: versions.includes(modelVersion)
                          ? modelVersion : '',
                        raise_ceiling: true })
                      .then((r2) => toast(r2.warnings?.length ? r2.warnings
                        : ['ceiling raised — applied']))
                      .catch((e: Error) => toast([`error: ${e.message}`])) })
                } else toast(r.warnings)
                close()
              })
              .catch((e: Error) => toast([`error: ${e.message}`]))}>save</button>
          <button onClick={close}>cancel</button>
        </div>
      </div>
      {asking === 'retire' && (
        <ConfirmModal title={`retire ${node.id}?`}
          body={`It stops working and frees ${(node.seat ?? 0) + (node.grant ?? 0)} credit(s) back to its superior. Its context is KEPT — rehire brings it back exactly as it was.`}
          confirmLabel="retire"
          onConfirm={() => op({ op: 'retire', node: node.id })
            .then(close).catch(() => {})}
          close={() => setAsking(null)} />
      )}
      {asking === 'dissolve' && (
        <ConfirmModal title={`dissolve ${node.id}?`}
          body="Its entire suborganization is retired with it. Context is kept; rehire brings nodes back."
          confirmLabel="dissolve"
          onConfirm={() => op({ op: 'dissolve', node: node.id }).then(close).catch(() => {})}
          close={() => setAsking(null)} />
      )}
      {asking === 'delete' && (() => {
        const count = (function c(n: CanvasNode): number {
          return n.children.reduce((a, k) => a + 1 + c(k), 0)
        })(node)
        const gens = (node.lineage ?? []).length
        return <ConfirmModal title={`permanently delete ${node.id}?`}
          body={'Erased from the organization — seats, records, mail and lineage'
            + (count ? `, plus ${count} descendant(s)` : '')
            + (gens ? ` and ${gens} prior generation(s)` : '')
            + '. Session transcripts remain on disk. This cannot be undone.'}
          confirmLabel="delete permanently"
          onConfirm={() => op({ op: 'delete', node: node.id }).then(close).catch(() => {})}
          close={() => setAsking(null)} />
      })()}
    </div>
  )
}
// the RETIRED-PILE menu (user spec): pick which retiree sits in front — the
// front card is the one you zoom in on, message, read and can rehire; the
// rest wait stacked beneath it. The current front is highlighted.
interface PilePickerProps {
  pile: Pile
  map: Map<string, CanvasNode>
  onPick: (nid: string) => void
  close: () => void
  /** optional: the delete-all row only renders when an op channel exists */
  op?: OpFn
  toast?: ToastFn
}

export function PilePicker({ pile, map, onPick, close, op, toast }: PilePickerProps) {
  useEsc(close)
  const crowd = pile.kind === 'c'
  const [asking, setAsking] = useState(false)
  // "delete all" (user spec 2026-07-31): clear the whole retired pile at
  // once — permanent, so it sits behind the same confirm as any delete.
  // Sequential ops; each failure is already toasted by op(), the summary
  // counts what actually went through.
  const wipeAll = async () => {
    let ok = 0
    for (const id of pile.list) {
      try {
        await op!({ op: 'delete', node: id })   // reachable only via the op-gated row
        ok++
      } catch { /* op() toasted it */ }
    }
    toast?.([`${ok} of ${pile.list.length} archived agent(s) permanently deleted`])
    close()
  }
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings pile-picker" onClick={(e) => e.stopPropagation()}>
        <h3><LayersIcon fontSize="inherit" /> {crowd ? 'team stack' : 'retired pile'}
          <span className="dim"> · {pile.list.length} agents</span></h3>
        <div className="hint">
          {crowd
            ? 'A wide team stacks its leaf agents into one place. The one in '
              + 'front is the one you zoom in on and message; the rest keep '
              + 'working beneath. Pick one to bring it forward.'
            : 'The retiree in front is the one you zoom in on, read, message '
              + 'and can rehire; the rest wait beneath. Pick one to bring it '
              + 'forward.'}
        </div>
        {[...pile.list].reverse().map((id) => {
          const n = map.get(id)
          if (!n) return null
          return (
            <button key={id} className={'pile-row' + (id === pile.front ? ' on' : '')}
              onClick={() => onPick(id)}>
              <span className={'tier t-' + n.tier}>{TIER_LETTER[n.tier!] ?? '?'}</span>
              <span className="pile-name">{id}</span>
              {n.bearer_state && <span className="badge dim">{n.bearer_state}</span>}
              {n.busy && <span className="badge">working</span>}
              {n.state === 'unrecoverable' && <span className="badge dim">unrecoverable</span>}
              {(n.mail_pending ?? 0) > 0 && <span className="badge free">{n.mail_pending} mail</span>}
              {id === pile.front && <span className="badge free">in front</span>}
            </button>
          )
        })}
        {!crowd && op && (
          <div className="row">
            <span className="spacer" />
            <button className="danger" onClick={() => setAsking(true)}>
              <DeleteIcon fontSize="inherit" /> delete all {pile.list.length}
            </button>
          </div>
        )}
        {asking && (
          <ConfirmModal
            title={`permanently delete all ${pile.list.length} archived agents?`}
            body="Every agent in this pile is removed for good, along with each one's knowledge-bearer lineage — no rehire, no consulting, records erased from the org. Transcript files on disk are kept. This cannot be undone."
            confirmLabel="delete all"
            onConfirm={wipeAll}
            close={() => setAsking(false)} />
        )}
      </div>
    </div>
  )
}
