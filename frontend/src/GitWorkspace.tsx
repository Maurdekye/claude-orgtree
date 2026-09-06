import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { PinFrame } from './canvas/modalpin'
import { AgentName } from './canvas/identity'
import { resolveRef } from './canvas/reflinks'
import type { RefRoutes } from './canvas/reflinks'
import { pickFolder } from './picker'
import { fmtFull } from './timefmt'
import type { ToastFn } from './types'
import * as api from './git/api'
import { branchColor, canRecenter, layoutGraph, nodeAction, shortRef, ROW } from './git/layout'
import type { GitBranch, GitChanges, GitCommit, GitContext, GitRegistry, GitSettings, GitSnapshot } from './git/types'
import './git/workspace.css'

const message = (e: unknown) => e instanceof Error ? e.message : String(e)
const comparison = (c: GitBranch['sync']) => c.ahead === null || c.behind === null
  ? c.state.replaceAll('_', ' ') : `${c.ahead} ahead · ${c.behind} behind${c.state === 'diverged' ? ' · diverged' : ''}`

function ChangeDetails({ value }: { value: GitChanges }) {
  return <div className="git-change-details">
    <div>{value.count === null ? value.reason ?? 'Changes unavailable' : `${value.count} changed files`}
      {!value.complete && ' · incomplete scan'}</div>
    {!!value.operations?.length && <div>{value.operations.join(', ')} in progress</div>}
    {(['staged', 'unstaged', 'untracked'] as const).map(kind => {
      const deltas = value.files.flatMap(f => f[kind] ? [f[kind]!] : [])
      if (!deltas.length) return null
      const unknown = deltas.filter(d => d.added === null || d.removed === null).length
      return <div key={kind}>{kind}: {deltas.length} files · +{deltas.reduce((n, d) => n + (d.added ?? 0), 0)} / −{deltas.reduce((n, d) => n + (d.removed ?? 0), 0)}
        {unknown ? ` known lines · ${unknown} binary/unknown` : ' lines'}{!value.complete && ' · partial totals'}</div>
    })}
    {!!value.conflicted && <div>{value.conflicted} conflicted files</div>}
    {value.files.map(f => <div className="git-file" key={f.path}><code>{f.path}</code>
      {f.conflicted && <span>conflicted</span>}
      {(['staged', 'unstaged', 'untracked'] as const).map(kind => f[kind] && <span key={kind}>{kind}: {f[kind].added === null
        ? `line totals unavailable (${f[kind].reason})` : `+${f[kind].added} / −${f[kind].removed}`}</span>)}</div>)}
  </div>
}

function RepositorySettings({ slug, rid, changed, removed, close, toast }: {
  slug: string; rid: string; changed: () => void; removed: () => void; close: () => void; toast: ToastFn
}) {
  const [value, setValue] = useState<GitSettings | null>(null)
  const [branch, setBranch] = useState('')
  const [item, setItem] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => api.getGitSettings(slug, rid).then(setValue).catch(e => toast([message(e)])), [slug, rid, toast])
  useEffect(() => { void load() }, [load])
  const save = async (values: Record<string, unknown>) => {
    if (!value) return
    setBusy(true)
    try { await api.saveGitSettings(slug, rid, value.revision, values); await load(); changed() }
    catch (e) { toast([message(e)]) } finally { setBusy(false) }
  }
  const link = async (b: string, i: string, remove = false) => {
    setBusy(true)
    try { await api.linkGit(slug, rid, b, i, remove); await load(); changed() }
    catch (e) { toast([message(e)]) } finally { setBusy(false) }
  }
  return <section className="git-settings" aria-label="Repository settings">
    <header><b>Repository settings</b><button onClick={close} aria-label="Close repository settings">×</button></header>
    {!value ? <p>Loading settings…</p> : <>
      <label>Trunk <select disabled={busy} value={value.trunk ?? ''} onChange={e => void save({ trunk: e.target.value || null })}>
        <option value="">Select trunk</option>
        {value.trunk && !value.branches.includes(value.trunk) && <option value={value.trunk}>{shortRef(value.trunk)} (missing)</option>}
        {value.branches.filter(r => r.startsWith('refs/heads/')).map(r => <option key={r} value={r}>{shortRef(r)}</option>)}
      </select></label>
      <label>Remote <select disabled={busy} value={value.remote ?? ''} onChange={e => void save({ remote: e.target.value || null })}>
        <option value="">Select remote</option>
        {value.remote && !value.remotes.includes(value.remote) && <option value={value.remote}>{value.remote} (missing)</option>}
        {value.remotes.map(r => <option key={r}>{r}</option>)}
      </select></label>
      <p>Link branches to docket items. A ticket can link to several branches; a branch can link to several tickets.</p>
      <label>Branch <select value={branch} onChange={e => setBranch(e.target.value)}><option value="">Choose branch</option>
        {value.branches.map(r => <option key={r} value={r}>{shortRef(r)}</option>)}</select></label>
      <label>Ticket <select value={item} onChange={e => setItem(e.target.value)}><option value="">Choose ticket</option>
        {value.items.map(i => <option key={i.slug} value={i.slug}>{i.slug}</option>)}</select></label>
      <button disabled={busy || !branch || !item} onClick={() => void link(branch, item)}>Link ticket</button>
      {value.links.map(l => <div className="git-link-row" key={l.branch_ref + l.item_slug}>
        <span>{shortRef(l.branch_ref)} · {l.item_slug}
          {!value.branches.includes(l.branch_ref) && ' · branch missing'}
          {!value.items.some(i => i.slug === l.item_slug) && ' · ticket missing'}</span>
        <button disabled={busy} onClick={() => void link(l.branch_ref, l.item_slug, true)}>Unlink</button></div>)}
      <p>Push and fast-forward Pull honor configured Git hooks. Pull requires a clean checkout, including untracked files. It never switches branches or stashes edits.</p>
    </>}
      <button disabled={busy} onClick={async () => {
        setBusy(true)
        try { await api.forgetGit(slug, rid); removed() } catch (e) { toast([message(e)]); setBusy(false) }
      }}>Remove from this org</button><small>Removes registration and ticket links. Repository files stay on disk.</small>
  </section>
}

export function GitWorkspace({ slug, context, routes, toast, close }: {
  slug: string; context?: GitContext; routes: RefRoutes; toast: ToastFn; close: () => void
}) {
  const [registry, setRegistry] = useState<GitRegistry | null>(null)
  const [rid, setRid] = useState('')
  const [snapshot, setSnapshot] = useState<GitSnapshot | null>(null)
  const [nodes, setNodes] = useState<GitCommit[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [loadedThrough, setLoadedThrough] = useState(120)
  const [error, setError] = useState('')
  const [newHistory, setNewHistory] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [branchesOpen, setBranchesOpen] = useState(false)
  const [discoveryOpen, setDiscoveryOpen] = useState(false)
  const [pullWorktrees, setPullWorktrees] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<string[] | undefined>()
  const [focused, setFocused] = useState<string | null>(null)
  const [hover, setHover] = useState<{ x: number; y: number; body: ReactNode } | null>(null)
  const [action, setAction] = useState<{ x: number; y: number; kind: 'push' | 'pull'; branch: string } | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const sequence = useRef(0)
  const pageBusy = useRef(false)
  const [view, setView] = useState({ top: 0, left: 0, width: 1000, height: 570 })
  const gesture = useRef<{ id: number; x: number; y: number; left: number; top: number } | null>(null)
  const initialPosition = useRef(true)
  const appliedContext = useRef('')
  const layout = useMemo(() => snapshot ? layoutGraph(nodes, snapshot) : null, [nodes, snapshot])
  const refreshRegistry = useCallback(async () => {
    try { const value = await api.listGit(slug); setRegistry(value); setRid(old => old || value.selected || value.repositories[0]?.id || '') }
    catch (e) { setError(message(e)) }
  }, [slug])
  useEffect(() => { void refreshRegistry() }, [refreshRegistry])
  useEffect(() => {
    if (!registry || (!context?.item && !context?.agent)) return
    const key = `${context.slug}:${context.item ?? ''}:${context.agent ?? ''}`
    if (appliedContext.current === key) return
    appliedContext.current = key
    const matches = (link: GitRegistry['repositories'][number]['links'][number]) => context.item ? link.item === context.item : link.agent === context.agent
    const target = registry.repositories.find(r => r.links.some(matches))
    if (target) { setRid(target.id); setSelected([...new Set(target.links.filter(matches).map(l => l.branch))]) }
  }, [context, registry])
  const refresh = useCallback(async () => {
    if (!rid) return
    const current = ++sequence.current
    setBusy(true); setError(''); setAction(null); setHover(null); setNewHistory(false)
    try {
      const value = await api.getGit(slug, rid, selected)
      if (current !== sequence.current) return
      setSnapshot(value); setNodes(value.history.nodes); setCursor(value.history.next_cursor); setLoadedThrough(120)
      setFocused(old => old && value.branches.some(b => b.ref === old) ? old : value.config.trunk)
    } catch (e) { if (current === sequence.current) setError(message(e)) }
    finally { if (current === sequence.current) setBusy(false) }
  }, [slug, rid, selected])
  useEffect(() => { initialPosition.current = true; setSnapshot(null); setNodes([]); void refresh(); return () => { sequence.current++ } }, [refresh])
  useEffect(() => {
    if (!snapshot || !rid) return
    let cancelled = false, running = false
    const poll = async () => {
      if (running || busy) return
      running = true
      try {
        const value = await api.getGitObservation(slug, rid)
        if (cancelled) return
        if (value.freshness) setSnapshot(old => old ? { ...old, freshness: value.freshness! } : old)
        else if (value.busy) setSnapshot(old => old ? { ...old, freshness: { ...old.freshness, busy: true } } : old)
        if (value.ref_identity && value.ref_identity !== snapshot.ref_identity) {
          if (!gesture.current && (viewport.current?.scrollTop ?? 0) < 50) void refresh()
          else setNewHistory(true)
        }
      } catch (e) { if (!cancelled) setError(message(e)) } finally { running = false }
    }
    const timer = setInterval(() => void poll(), 5000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [slug, rid, snapshot?.token, busy, refresh])
  useLayoutEffect(() => {
    const vp = viewport.current
    if (vp && layout && initialPosition.current) {
      vp.scrollLeft = Math.max(0, layout.trunkX - vp.clientWidth / 2)
      vp.scrollTop = 0
      initialPosition.current = false
    }
  }, [layout])
  const updateView = useCallback(() => {
    const vp = viewport.current
    if (vp) setView({ top: vp.scrollTop, left: vp.scrollLeft, width: vp.clientWidth, height: vp.clientHeight })
  }, [])
  useEffect(() => {
    const vp = viewport.current
    if (!vp) return
    const observer = new ResizeObserver(updateView); observer.observe(vp); updateView()
    return () => observer.disconnect()
  }, [snapshot !== null, updateView])
  const loadOlder = useCallback(async () => {
    if (!cursor || !rid || pageBusy.current || busy) return
    pageBusy.current = true; setLoadingHistory(true)
    const current = sequence.current
    try {
      const page = await api.getGitHistory(slug, rid, cursor)
      if (current !== sequence.current) return
      setNodes(old => { const known = new Set(old.map(n => n.oid)); return [...old, ...page.nodes.filter(n => !known.has(n.oid))] })
      setCursor(page.next_cursor)
      setLoadedThrough(page.offset + 120)
    } catch (e) { setError(message(e)) }
    finally { pageBusy.current = false; setLoadingHistory(false) }
  }, [cursor, rid, slug, busy])
  useEffect(() => {
    if (cursor && view.top + view.height > 85 + loadedThrough * ROW - 160) void loadOlder()
  }, [cursor, loadedThrough, view.top, view.height, loadOlder])
  const hideHover = () => { hoverTimer.current = setTimeout(() => setHover(null), 120) }
  const showHover = (target: HTMLElement, body: ReactNode) => {
    clearTimeout(hoverTimer.current)
    const rect = target.getBoundingClientRect()
    setHover({ x: Math.max(8, Math.min(window.innerWidth - 340, rect.right + 12)), y: Math.max(8, Math.min(window.innerHeight - 240, rect.top)), body })
  }
  const openRef = (kind: 'item' | 'agent', id: string) => {
    const ref = resolveRef({ kind, org: slug, id }, routes.world)
    if (ref.outcome === 'ready') routes.onOpen(ref)
    else toast([ref.why])
  }
  const add = async (path?: string) => {
    const selectedPath = path ?? (await pickFolder()).path
    if (!selectedPath) return
    setBusy(true)
    try { const value = await api.registerGit(slug, selectedPath); await refreshRegistry(); setSelected(undefined); setRid(value.id) }
    catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  const scan = async () => {
    const path = (await pickFolder()).path
    if (!path) return
    setBusy(true)
    try { const discovery = await api.discoverGit(slug, path); setRegistry(old => old ? { ...old, discovery } : old); setDiscoveryOpen(true) }
    catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  const chooseRepository = (id: string) => {
    setSelected(undefined); setRid(id)
    void api.selectGit(slug, id).catch(e => toast([message(e)]))
  }
  const fetch = async () => {
    setBusy(true)
    try { await api.fetchGit(slug, rid); await refresh() }
    catch (e) { toast([message(e)]); await refresh() } finally { setBusy(false) }
  }
  const autoFetch = async (enabled: boolean) => {
    try { const value = await api.getGitSettings(slug, rid); await api.saveGitSettings(slug, rid, value.revision, { auto_fetch: enabled }); await refresh() }
    catch (e) { toast([message(e)]) }
  }
  const perform = async () => {
    if (!snapshot || !action) return
    setBusy(true)
    try {
      const result = await api.gitAction(slug, rid, action.kind, snapshot.token, action.branch, pullWorktrees[action.branch] || undefined)
      toast([result.message, ...(result.state === 'changed' || result.state === 'unknown' ? [`Repository outcome: ${result.state}; refresh before another action`] : [])])
      await refresh()
    } catch (e) { toast([message(e)]) } finally { setBusy(false); setAction(null) }
  }
  const branchDetails = (branch: GitBranch) => <>
    <b>{shortRef(branch.ref)}</b>
    {branch.tickets.map(t => <div key={t.slug}>{t.title ?? `${t.slug} · unavailable`}</div>)}
    <p>Local trunk: {comparison(branch.against_trunk)}<br />Upstream: {comparison(branch.sync)}</p>
    {snapshot?.worktrees.filter(w => w.branch === branch.ref).map(w => <div key={w.id} className="git-checkout">
      <code>{w.path}</code><div>Checkout {w.oid?.slice(0, 10) ?? 'without commits'}{w.agents.length ? ` · ${w.agents.join(', ')}` : ''}</div>
      <ChangeDetails value={w.changes} /></div>)}
  </>
  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const vp = viewport.current
    if (!gesture.current || !vp || gesture.current.id !== event.pointerId) return
    gesture.current = null
    vp.classList.remove('dragging')
    if (vp.hasPointerCapture(event.pointerId)) vp.releasePointerCapture(event.pointerId)
    if (layout && canRecenter(layout, vp.clientWidth)) vp.scrollTo({ left: layout.trunkX - vp.clientWidth / 2,
      top: vp.scrollTop, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
  }
  return <PinFrame kind={`git:${slug}`} title="Git repositories" panel="git-workspace" close={close}
    onEsc={() => { if (action || hover) { setAction(null); setHover(null) } else close() }}>
    <header className="git-head"><span className="git-mark">⑂</span>
      <select aria-label="Repository" value={rid} disabled={busy} onChange={e => chooseRepository(e.target.value)}>
        {!rid && <option value="">Select repository</option>}
        {registry?.repositories.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
      </select><button disabled={busy} onClick={() => void add()}>Add repository</button>
      <button disabled={busy} onClick={() => void scan()}>Scan subfolders</button>
      {rid && <button onClick={() => setSettingsOpen(v => !v)}>Repository settings</button>}
      <button className="git-close" onClick={close} aria-label="Close Git workspace">×</button></header>
    {(context?.item || context?.agent) && <div className="git-context-note">Opened from {context.item ? `ticket ${context.item}` : `agent ${context.agent}`}
      {registry && !registry.repositories.some(r => r.links.some(l => context.item ? l.item === context.item : l.agent === context.agent)) && ' · no linked branch; add a link in repository settings'}</div>}
    {error && <div className="git-error" role="alert">{error} <button onClick={() => void refresh()}>Refresh</button></div>}
    {(!rid || discoveryOpen) && <div className="git-empty"><p>Choose a repository to see its branches and checkouts.</p>
      {discoveryOpen && <button onClick={() => setDiscoveryOpen(false)}>Close discovery results</button>}
      {registry?.discovery.candidates.map(c => <button key={c.path} onClick={() => void add(c.path)}>{c.name} · {c.path}</button>)}
      {registry?.discovery.candidates.length === 0 && <p>No repositories found in the selected roots.</p>}
      {registry?.discovery.truncated && <p>Discovery reached its directory limit.</p>}</div>}
    {rid && <nav className="git-toolbar">
      <button disabled={busy} onClick={() => void refresh()}>Refresh</button>
      <button disabled={busy || !snapshot?.config.remote} onClick={() => void fetch()}>Fetch now</button>
      <label><input type="checkbox" checked={snapshot?.freshness.watched ?? false} disabled={busy || !snapshot?.config.remote}
        onChange={e => void autoFetch(e.target.checked)} />Fetch every 30s</label>
      <button onClick={() => setBranchesOpen(v => !v)}>Branches and history</button>
      <span className="git-key"><i />Shared <i className="unpushed" />Unpushed <i className="ghost" />Remote only</span>
      {snapshot && <span className={'git-freshness ' + snapshot.freshness.state} title={snapshot.freshness.error ?? ''}>
        {snapshot.freshness.busy ? 'Fetching · ' : ''}{snapshot.freshness.state.replaceAll('_', ' ')}
        {snapshot.freshness.age_seconds !== null && ` · observed ${Math.floor(snapshot.freshness.age_seconds / 60)}m ago`}</span>}
    </nav>}
    {newHistory && <div className="git-context-note">Repository history changed. <button onClick={() => void refresh()}>Refresh graph</button></div>}
    {busy && <div className="git-loading" role="status">Reading repository…</div>}
    {snapshot && nodes.length > 0 && (!snapshot.config.trunk || snapshot.config.trunk_missing) && <div className="git-context-note">
      {snapshot.config.trunk_missing ? 'Saved trunk is missing.' : 'Select the repository trunk.'} <button onClick={() => setSettingsOpen(true)}>Choose trunk</button></div>}
    {settingsOpen && rid && <RepositorySettings key={rid} slug={slug} rid={rid} toast={toast} close={() => setSettingsOpen(false)} changed={() => void refresh()}
      removed={() => { setSettingsOpen(false); setRid(''); setSnapshot(null); setNodes([]); void refreshRegistry() }} />}
    {branchesOpen && snapshot && <section className="git-branch-picker" aria-label="Branches and history">
      {snapshot.branches.filter(b => snapshot.worktrees.filter(w => w.branch === b.ref && !w.bare).length > 1).map(b => <label key={b.ref}>Checkout for Pull · {shortRef(b.ref)}
        <select aria-label={`Checkout for Pull · ${shortRef(b.ref)}`} value={pullWorktrees[b.ref] ?? ''} onChange={e => setPullWorktrees(old => ({ ...old, [b.ref]: e.target.value }))}>
          <option value="">Choose checkout</option>{snapshot.worktrees.filter(w => w.branch === b.ref && !w.bare).map(w => <option key={w.id} value={w.id}>{w.path}</option>)}
        </select></label>)}
      <p>Choose up to 40 branches. Inactive and remote-only branches are included here.</p>
      <button onClick={() => { setSelected(undefined); setBranchesOpen(false) }}>Relevant branches</button>
      {snapshot.inventory.map(b => <label key={b.ref}><input type="checkbox"
        checked={(selected ?? snapshot.branches.map(v => v.ref)).includes(b.ref)}
        onChange={e => setSelected(old => { const list = old ?? snapshot.branches.map(v => v.ref); return e.target.checked ? [...list, b.ref].slice(0, 40) : list.filter(r => r !== b.ref) })} />{shortRef(b.ref)}</label>)}
    </section>}
    {layout && snapshot && <div className="git-viewport" ref={viewport} tabIndex={0} aria-label="Repository commit graph"
      style={{ backgroundPosition: `${-view.left * .22}px ${-view.top * .32}px` }}
      onScroll={() => { updateView(); setHover(null); setAction(null) }}
      onPointerDown={e => {
        if (e.button !== 0 || (e.target as HTMLElement).closest('button,a,input,select')) return
        const vp = e.currentTarget
        gesture.current = { id: e.pointerId, x: e.clientX, y: e.clientY, left: vp.scrollLeft, top: vp.scrollTop }
        vp.setPointerCapture(e.pointerId); vp.classList.add('dragging'); setHover(null); setAction(null)
      }}
      onPointerMove={e => { const g = gesture.current; if (!g || g.id !== e.pointerId) return
        e.currentTarget.scrollLeft = g.left - (e.clientX - g.x); e.currentTarget.scrollTop = g.top - (e.clientY - g.y) }}
      onPointerUp={endDrag} onPointerCancel={endDrag}
      onLostPointerCapture={() => { gesture.current = null; viewport.current?.classList.remove('dragging') }}>
      <div className="git-canvas" style={{ width: layout.width, height: layout.height }}>
        <svg aria-hidden="true" width={layout.width} height={layout.height}>
          {nodes.flatMap(n => { const a = layout.points.get(n.oid)!; return n.parents.map(parent => {
            const b = layout.points.get(parent)
            if (b && (Math.max(a.y, b.y) < view.top - 100 || Math.min(a.y, b.y) > view.top + view.height + 100)) return null
            if (!b && (a.y < view.top - 100 || a.y > view.top + view.height + 100)) return null
            return <line key={n.oid + parent} x1={a.x} y1={a.y} x2={b?.x ?? a.x} y2={b?.y ?? a.y + 17}
              stroke={branchColor(a.owner ?? snapshot.config.trunk ?? 'main')} strokeWidth={1.5} strokeDasharray={b ? undefined : '3 3'} opacity={.65} />
          }) })}
          {layout.annotations.filter(a => a.y > view.top - 150 && a.y < view.top + view.height + 150).map(a => <line key={a.branch.ref}
            x1={a.anchor.x + (a.x < a.anchor.x ? -7 : 7)} y1={a.anchor.y} x2={a.x < a.anchor.x ? a.x + 252 : a.x - 7} y2={a.y + a.height / 2} stroke={branchColor(a.branch.ref)} strokeDasharray="2 3" opacity={.6} />)}
        </svg>
        {nodes.filter(n => { const p = layout.points.get(n.oid)!; return p.y > view.top - 100 && p.y < view.top + view.height + 100 && p.x > view.left - 100 && p.x < view.left + view.width + 100 }).map(n => {
          const p = layout.points.get(n.oid)!; const selectedAction = nodeAction(n.oid, snapshot.branches, focused, n.comparisons)
          const owner = selectedAction?.branch ?? snapshot.branches.find(b => b.ref === p.owner)
          const kind = selectedAction?.action === 'pull' || owner && !owner.local ? 'ghost' : selectedAction?.action === 'push' ? 'unpushed' : 'shared'
          const body = <><b>{n.subject}</b>{n.message && n.message.trim() !== n.subject && <p style={{ whiteSpace: 'pre-wrap' }}>{n.message}</p>}<code>{n.oid}</code><div>{fmtFull(new Date(n.at * 1000).toISOString())}</div>
            {owner && <div>{shortRef(owner.ref)} · {kind === 'ghost' ? 'observed remote only' : kind === 'unpushed' ? 'unpushed in this branch comparison' : owner.sync.ahead === null ? comparison(owner.sync) : 'shared history'}</div>}
            {snapshot.worktrees.filter(w => w.oid === n.oid && !w.branch).map(w => <div key={w.id}><code>{w.path}</code><div>Detached checkout</div><ChangeDetails value={w.changes} /></div>)}</>
          return <button key={n.oid} data-oid={n.oid} className={`git-node ${kind}`} aria-label={`${n.subject} · ${n.oid.slice(0, 8)}`}
            style={{ left: p.x, top: p.y, '--branch-color': branchColor(owner?.ref ?? p.owner ?? '') } as CSSProperties}
            onPointerEnter={e => showHover(e.currentTarget, body)} onPointerLeave={hideHover}
            onFocus={e => showHover(e.currentTarget, body)} onBlur={hideHover}
            onClick={e => { setHover(null); if (!selectedAction) { setAction(null); return }
              const rect = e.currentTarget.getBoundingClientRect(); setAction({ kind: selectedAction.action, branch: selectedAction.branch.ref,
                x: Math.max(8, Math.min(window.innerWidth - 175, e.clientX || rect.right)), y: Math.max(8, Math.min(window.innerHeight - 45, e.clientY || rect.bottom)) }) }}><i /></button>
        })}
        {layout.annotations.filter(a => a.y > view.top - 150 && a.y < view.top + view.height + 150 && a.x > view.left - 280 && a.x < view.left + view.width + 100).map(a => {
          const owners = [...new Map(a.branch.tickets.filter(t => t.owner).map(t => [`${t.owner!.id}@${t.owner!.generation}`, t.owner!])).values()]
          return <div key={a.branch.ref} className={'git-annotation' + (a.branch.tickets.length ? ' associated' : '')}
            data-branch={a.branch.ref} style={{ left: a.x, top: a.y, '--branch-color': branchColor(a.branch.ref) } as CSSProperties}
            onPointerEnter={e => showHover(e.currentTarget, branchDetails(a.branch))} onPointerLeave={hideHover}>
            <button className="git-branch-name" onClick={() => { setFocused(a.branch.ref); setAction(null) }}
              onFocus={e => showHover(e.currentTarget, branchDetails(a.branch))} onBlur={hideHover}>{shortRef(a.branch.ref)}</button>
            {!!a.branch.tickets.length && <div className="git-ticket-row">{a.branch.tickets.map(t => <button key={t.slug} disabled={t.missing} onClick={() => openRef('item', t.slug)}>{t.slug}</button>)}</div>}
            {!!owners.length && <div className="git-agent-row">{owners.map(o => <span key={`${o.id}@${o.generation}`}><AgentName id={o.id} tier={o.tier} why={o.current ? undefined : 'Historical assignee'} onFocus={o.target ? () => openRef('agent', o.target!) : undefined} /></span>)}</div>}
          </div>
        })}
        {!nodes.length && <div className="git-unborn" style={{ left: layout.trunkX - 100 }}>{snapshot.unborn_branch ? `${shortRef(snapshot.unborn_branch)} · ` : ''}Repository has no commits yet.</div>}
        <div className="git-history-end" style={{ left: layout.trunkX - 80, top: cursor ? 85 + loadedThrough * ROW : layout.height - 65 }}>
          {loadingHistory ? 'Loading older history…' : cursor ? <button onClick={() => void loadOlder()}>Load older history · {Math.max(0, snapshot.total_commits - nodes.length)} commits not loaded</button> : snapshot.shallow ? 'Shallow history boundary' : 'Beginning of history'}</div>
      </div>
    </div>}
    {hover && <div className="git-hover" role="tooltip" style={{ left: hover.x, top: hover.y }} onPointerEnter={() => clearTimeout(hoverTimer.current)} onPointerLeave={hideHover}>{hover.body}</div>}
    {action && <div className="git-node-action" style={{ left: action.x + 10, top: action.y + 10 }}><button disabled={busy} onClick={() => void perform()}>{action.kind === 'push' ? 'Push local changes' : 'Pull unsynced commits'}</button></div>}
    {snapshot && <footer className="git-footer">Against trunk: local {snapshot.config.trunk ? shortRef(snapshot.config.trunk) : 'not selected'}. Upstream comparisons use configured tracking refs. Newer commits are higher.
      {(snapshot.omitted_active > 0 || snapshot.omitted_worktrees > 0) && <span> {snapshot.omitted_active} active branches and {snapshot.omitted_worktrees} checkouts outside this view.</span>}</footer>}
  </PinFrame>
}
