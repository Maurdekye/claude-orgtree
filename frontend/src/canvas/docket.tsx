// canvas/docket.tsx — the native work docket (docket-final-spec.md).
//
// SHAPED ON THE DOCUMENT GALLERY (same list-left/content-right structure,
// user spec) — wears the same `.settings.wide`/`.mailer`/`.mailer-list`/
// `.mailrow`/`.mailer-read` classes DocGalleryModal does, styled by its own
// `.docket-modal` scope the way `.gallery-modal` styles its own rows. The
// header follows the gallery's: the <h3> at the left and the filter checkboxes
// pushed to the RIGHT END, which is where the gallery's space-between layout
// puts its own — the grouping control sits on its own strip below so the header
// does not crowd.
//
// THREE GROUPINGS, ONE INVARIANT (user 2026-09-05): no group / by status / by
// agent. Whichever is chosen, the two filtered groups — backlog and archive —
// are APPENDED BELOW the current work, never mixed into it. Ticking a box adds
// a section at the end; it never re-sorts, reloads or resets what is already
// on screen, and never disturbs the selected item or a half-typed reply.
//
// Attached questions are NOT a second answering form: each is rendered as
// the real <AskCard> for the matching entry in `tree.asks` (which stays
// uncapped for every open ask), so answering here calls the exact same
// answerAsk/resolveBatch route the inbox/desk cards do. The item's own
// `questions` array (wire contract v3) is only used to know WHICH asks to
// look up and for the "who is asking" header — never to answer directly.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type {
  AskInfo, ToastFn, TreeNode, TreePayload, WorkActor, WorkItem,
} from '../types'
import {
  dismissWorkItemAttention, getWorkItems, replyWorkItem,
} from '../api'
import { CloseIcon, DocketIcon } from '../icons'
import { AskCard } from './asks'
import { TierChip } from './gallery'
import { MailReplyBox } from './mail'
import { ago, useEsc, usePolled } from './shared'
import { buildSlugIndex, WorkRefText } from './workrefs'
import type { SlugIndex } from './workrefs'

const STATUS_LABEL: Record<string, string> = {
  backlogged: 'Backlogged',
  open: 'Open',
  in_progress: 'In progress',
  blocked: 'Blocked',
  review: 'Under review',
  done: 'Done',
  superseded: 'Superseded',
  dropped: 'Dropped',
}
const statusLabel = (status: string): string => STATUS_LABEL[status] ?? status

/** Group-by-status order, exactly as specified: effective attention first,
 *  then blocked, in_progress, review, open, done, then everything else that
 *  is closed. A status the backend adds later lands in "Other" rather than
 *  vanishing — an unknown row must still be reachable. */
const STATUS_GROUPS: { key: string; heading: string }[] = [
  { key: 'attention', heading: 'Needs attention' },
  { key: 'blocked', heading: 'Blocked' },
  { key: 'in_progress', heading: 'In progress' },
  { key: 'review', heading: 'Under review' },
  { key: 'open', heading: 'Open' },
  { key: 'backlogged', heading: 'Backlogged' },
  { key: 'done', heading: 'Done' },
  { key: 'other', heading: 'Other closed' },
]

export type DocketGroupMode = 'none' | 'status' | 'agent'
const GROUP_MODES: { value: DocketGroupMode; label: string }[] = [
  { value: 'none', label: 'No group' },
  { value: 'status', label: 'Group by status' },
  { value: 'agent', label: 'Group by agent' },
]
/** app-local, per browser — a display preference, not org state */
const GROUP_KEY = 'orgtree.docket.group'

export function readGroupMode(): DocketGroupMode {
  try {
    const v = window.localStorage.getItem(GROUP_KEY)
    if (v === 'none' || v === 'status' || v === 'agent') return v
  } catch { /* storage disabled or unavailable — fall back to the default */ }
  return 'none'
}

function writeGroupMode(m: DocketGroupMode): void {
  try { window.localStorage.setItem(GROUP_KEY, m) } catch { /* ignore */ }
}

// THE ROW ORDER IS THE SERVER'S, and this file deliberately does not restate
// it. `ledger.work_list` already sorts every group by newest docket update
// first with the item id breaking a tie, which makes the order total and
// therefore stable across polls. Grouping here only PARTITIONS that sequence —
// `filter` and first-appearance bucketing both preserve relative order — so a
// group can never contradict the order the server chose, and there is no
// second copy of the comparator to drift out of step with it.

/** What we may honestly say about the agent an item points at. The tier chip
 *  is a MODEL claim, and the only model this app knows is the one the node
 *  wears NOW — so it may only be shown when the actor's generation is still
 *  the current one. An earlier generation ran under whatever tier it had at
 *  the time, which is not recorded anywhere, so the chip is omitted rather
 *  than back-filled with today's answer (Astra review 2026-09-05). */
export type ActorFit = 'current' | 'moved' | 'retired' | 'gone'

export interface NodeFacts { tier: string; generation: number; live: boolean }

export function buildNodeFacts(roots?: TreeNode[]): Map<string, NodeFacts> {
  const map = new Map<string, NodeFacts>()
  const walk = (nodes?: TreeNode[]) => {
    for (const n of nodes ?? []) {
      if (n.id) {
        map.set(n.id, {
          tier: n.tier,
          generation: Number(n.generation ?? 0),
          live: n.state === 'live',
        })
      }
      walk(n.children)
    }
  }
  walk(roots)
  return map
}

export function actorFit(actor: WorkActor | null | undefined,
                         facts: Map<string, NodeFacts>):
  { fit: ActorFit; tier?: string } {
  if (!actor?.node) return { fit: 'gone' }
  const n = facts.get(actor.node)
  if (!n) return { fit: 'gone' }
  if (Number(n.generation) !== Number(actor.generation ?? 0)) return { fit: 'moved' }
  if (!n.live) return { fit: 'retired', tier: n.tier }
  return { fit: 'current', tier: n.tier }
}

const FIT_WHY: Record<ActorFit, string | null> = {
  current: null,
  // a RETIRED agent is still the same generation, so its recorded model is
  // still the one that did the work — the chip stays, the row explains itself
  retired: 'this agent has been retired',
  moved: 'this agent has been replaced since this update — the model it ran '
    + 'under then is not recorded, so no model badge is shown',
  gone: 'this agent is no longer in the org',
}

/** An agent identity as it appears everywhere in this panel: the model chip
 *  only when we can honestly attribute it, the name truncating with a real
 *  ellipsis, and a jump to its desk. */
function ActorName({ actor, facts, onFocusAgent, close }: {
  actor: WorkActor | null | undefined
  facts: Map<string, NodeFacts>
  onFocusAgent?: (agentId: string) => void
  close?: () => void
}) {
  if (!actor?.node) return null
  const { fit, tier } = actorFit(actor, facts)
  const why = FIT_WHY[fit]
  return (
    <span className={'docket-actor fit-' + fit} title={why ?? undefined}>
      {tier && <TierChip tier={tier} />}
      {/* the ellipsis lives on THIS element, not on the inline-flex wrapper
          around it: text-overflow does nothing on a flex container, which is
          why the long name used to run under the Dismiss button instead of
          truncating (Astra review 2026-09-05) */}
      <button className="cc-name cc-name-jump docket-actor-name"
        title={`focus ${actor.node}'s desk`}
        onClick={(e) => { e.stopPropagation(); close?.(); onFocusAgent?.(actor.node) }}>
        {actor.node}
      </button>
    </span>
  )
}

/** The item's readable name. Falls back to the opaque id for an item written
 *  before slugs existed — the server refuses to mint one on a read, so there
 *  is genuinely nothing else to show. */
export const itemName = (item: WorkItem): string => item.slug ?? item.id

/** The name in the DETAIL pane: plain selectable text, no control.
 *
 *  ⚠ THIS WAS A BUTTON AND THE USER REMOVED IT (2026-09-05, twice — first from
 *  the list, then from here, from screenshots). A padded bordered copy chip
 *  ate the row's metadata space and truncated the agent name beside it to
 *  "c…", and it read as a control where the reader wanted a label. There is
 *  now NO copy affordance anywhere in this panel: the name is selectable text
 *  and the browser's own copy does the job. Do not reintroduce one without a
 *  new ruling. */
function SlugText({ item }: { item: WorkItem }) {
  return (
    <span className="docket-slug-text"
      title={item.slug
        ? undefined
        : `${item.id} — this item predates readable names; it gets one on its next update`}>
      {itemName(item)}
    </span>
  )
}

export function DocketToolbarButton({ summary, onClick }: {
  summary?: { attention: number; active: number } | null
  onClick?: () => void
}) {
  const { attention, active } = summary ?? { attention: 0, active: 0 }
  return (
    <button className="iconbtn docket-bell"
      title={attention > 0
        ? `work docket — ${attention} item(s) need attention`
        : 'work docket'}
      onClick={onClick}>
      <DocketIcon fontSize="inherit" />
      <b className={'eye-count' + (attention > 0 ? ' docket-attn' : '')}>
        {attention > 0 ? attention : active}</b>
    </button>
  )
}

/** the owner-less group's heading — named explicitly rather than left as a
 *  silent remainder at the bottom of the list (user 2026-09-05) */
export const UNASSIGNED = 'Unassigned'

interface Section {
  key: string
  heading: string | null
  items: WorkItem[]
  /** styling hook for the two appended groups, so they read as set apart
   *  from current work rather than as two more status buckets */
  tone?: 'backlog' | 'archive'
}

/** The whole list, in order. The contract this function exists to keep: the
 *  backlog and the archive are ALWAYS the last two sections, in that order, in
 *  every grouping mode — so ticking a box can only ever add something to the
 *  bottom of the list. */
export function buildSections(mode: DocketGroupMode, active: WorkItem[],
                              backlog: WorkItem[], archived: WorkItem[],
                              ownerName: (it: WorkItem) => string): Section[] {
  const out: Section[] = []
  const rest = active

  if (mode === 'status') {
    const bucket = (it: WorkItem): string => {
      if (it.effective_attention) return 'attention'
      if (STATUS_GROUPS.some((g) => g.key === it.status)) return it.status
      return 'other'
    }
    for (const g of STATUS_GROUPS) {
      const items = rest.filter((it) => bucket(it) === g.key)
      if (items.length) out.push({ key: 'st:' + g.key, heading: g.heading, items })
    }
  } else if (mode === 'agent') {
    const groups = new Map<string, WorkItem[]>()
    for (const it of rest) {
      const who = ownerName(it)
      const list = groups.get(who)
      if (list) list.push(it)
      else groups.set(who, [it])
    }
    // A Map keeps insertion order, and rows arrive newest-first, so the agents
    // come out in order of their most recent activity WITHOUT a second sort —
    // and therefore without a second chance to disagree with the server.
    for (const [who, items] of groups) {
      if (who !== UNASSIGNED) out.push({ key: 'ag:' + who, heading: who, items })
    }
    const un = groups.get(UNASSIGNED)
    // last, and always named rather than left as a silent remainder
    if (un) out.push({ key: 'ag:unassigned', heading: UNASSIGNED, items: un })
  } else if (rest.length) {
    out.push({ key: 'all', heading: null, items: rest })
  }

  // ALWAYS LAST, ALWAYS IN THIS ORDER, in every mode: ticking a filter may only
  // ever append a section to the bottom of the list (user 2026-09-05).
  if (backlog.length) {
    out.push({
      key: 'backlog', tone: 'backlog', items: backlog,
      heading: 'Backlogged — not yet approached',
    })
  }
  if (archived.length) {
    out.push({ key: 'archive', tone: 'archive', items: archived, heading: 'Archived' })
  }
  return out
}

export function DocketModal({ slug, toast, close, tree, onFocusAgent }: {
  slug: string
  toast: ToastFn
  close: () => void
  tree: TreePayload
  /** the existing presented-document agent navigation (gallery.tsx's
   *  DocPane) — user ruling 2026-09-05: agent identities in a mail-idiom
   *  detail pane are clickable links using this exact behavior everywhere
   *  it appears, docket included. */
  onFocusAgent?: (agentId: string) => void
}) {
  useEsc(close)
  const [showArchived, setShowArchived] = useState(false)
  const [showBacklog, setShowBacklog] = useState(false)
  const [groupMode, setGroupMode] = useState<DocketGroupMode>(readGroupMode)
  const [bump, setBump] = useState(0)
  // ⚠ EVERYTHING REMEMBERED IS SCOPED TO THE ORG IT CAME FROM. This panel can
  // be handed a different `slug` while mounted; without the tag, the previous
  // org's cached rows and selected id would survive that change and the pane
  // would render one org's item while every action on it addressed another's
  // URL (Astra review 2026-09-05). Comparing the tag during RENDER rather than
  // clearing in an effect also means there is no frame in which the stale rows
  // are still on screen.
  const [cache, setCache] = useState<{ slug: string; archived: WorkItem[]; backlog: WorkItem[] }>(
    { slug, archived: [], backlog: [] })
  const [sel, setSel] = useState<{ slug: string; id: string } | null>(null)
  const archivedCache = cache.slug === slug ? cache.archived : []
  const backlogCache = cache.slug === slug ? cache.backlog : []

  // ⚠ BOTH GROUPS ARE ALWAYS FETCHED, AND THE CHECKBOXES ONLY DECIDE WHAT IS
  // SHOWN. A slug link must work when it points at a backlogged or archived
  // item — "reveal the row" is impossible if the row was never loaded, and a
  // mention that silently refuses to link because a checkbox is off would be
  // the worst of both worlds. `ledger.work_list` builds all three groups on
  // every call regardless of the flags (they gate the RESPONSE, not the work),
  // so this costs payload, not server time.
  //
  // deps is [slug] so ticking a filter does not clear data to null (which
  // would unmount the pane and wipe the user's in-flight reply draft).
  //
  // ⚠ THE TOGGLES STAY IN THE REFRESH KEY even though they no longer change the
  // REQUEST. They are what makes a tick refetch immediately instead of waiting
  // out the five-second poll, and that is load-bearing: the panel keeps a copy
  // of each group, and unticking is how a row that has just left the archive
  // gets replaced by its current self rather than by the copy we cached. Drop
  // them from the key and the stale copy survives on screen until the next
  // poll (caught by §31 of docket.test.tsx).
  const data = usePolled(() => getWorkItems(slug, true, true),
    [slug], 5000, `${bump}-${showArchived}-${showBacklog}`)

  useEffect(() => {
    if (!data?.archived && !data?.backlogged) return
    setCache((c) => ({
      slug,
      archived: data.archived ?? (c.slug === slug ? c.archived : []),
      backlog: data.backlogged ?? (c.slug === slug ? c.backlog : []),
    }))
  }, [slug, data?.archived, data?.backlogged])

  const facts = useMemo(() => buildNodeFacts(tree?.roots), [tree?.roots])

  const active = data?.items ?? []
  // while a toggle's first fetch is in flight the cached group keeps showing,
  // so the list grows once and never blinks
  const archived = showArchived ? (data?.archived ?? archivedCache) : []
  const backlog = showBacklog ? (data?.backlogged ?? backlogCache) : []
  const archivedCount = data?.counts?.archived ?? archivedCache.length
  const backlogCount = data?.counts?.backlogged ?? backlogCache.length

  const ownerName = useCallback((it: WorkItem) => it.owner?.node ?? UNASSIGNED, [])
  const sections = useMemo(
    () => buildSections(groupMode, active, backlog, archived, ownerName),
    [groupMode, active, backlog, archived, ownerName])
  const rowCount = sections.reduce((n, s) => n + s.items.length, 0)

  // selection BY ID, not index — the list repolls under the user (G5)
  const selId = sel?.slug === slug ? sel.id : null
  const setSelId = useCallback(
    (id: string | null) => setSel(id ? { slug, id } : null), [slug])
  // ⚠ ORDER IS THE POINT. The CURRENT response is written LAST, so it wins over
  // anything held from an earlier one. Written the other way round — caches
  // last — an item that had just been reopened or promoted out of the archive
  // would be overwritten by its own stale archived copy, and the detail pane
  // would show the status and description it used to have (Astra review
  // 2026-09-05).
  const allKnown = useMemo(() => {
    const map = new Map<string, WorkItem>()
    for (const item of archivedCache) map.set(item.id, item)
    for (const item of backlogCache) map.set(item.id, item)
    for (const item of (data?.archived ?? [])) map.set(item.id, item)
    for (const item of (data?.backlogged ?? [])) map.set(item.id, item)
    for (const item of active) map.set(item.id, item)
    return map
  }, [active, data?.archived, data?.backlogged, archivedCache, backlogCache])
  const cur = allKnown.get(selId ?? '')
  const asksById = new Map<string, AskInfo>((tree.asks ?? []).map((a) => [a.id, a]))

  // ---- slug mentions in prose become links to the item they name
  //
  // The index is built from `allKnown`, which is exactly the set of items this
  // org served to this viewer. That is what keeps a link same-org by
  // construction rather than by a check someone could forget: a name from
  // another org is not in the map, so it is never marked as a mention.
  const slugIndex = useMemo(
    () => buildSlugIndex(allKnown.values()), [allKnown])
  const [flash, setFlash] = useState<string | null>(null)
  const rows = useRef(new Map<string, HTMLDivElement>())

  const goToItem = useCallback((id: string) => {
    const it = allKnown.get(id)
    if (!it) return           // not ours to show — never a broken selection
    // REVEAL BEFORE SELECT. A backlogged or archived item has no row while its
    // group is filtered out, and selecting an invisible row would look like
    // the link did nothing.
    if (it.archived) setShowArchived(true)
    else if (it.status === 'backlogged') setShowBacklog(true)
    setSel({ slug, id })
    setFlash(id)
  }, [allKnown, slug])

  // the flash is a hint, not a state: it clears itself and never survives to
  // confuse the next visit
  useEffect(() => {
    if (!flash) return
    const t = window.setTimeout(() => setFlash(null), 1800)
    return () => window.clearTimeout(t)
  }, [flash])

  // scroll AFTER the render that created the row — a freshly revealed group's
  // rows do not exist at the moment the link is clicked. `scrollIntoView` is
  // absent in jsdom and in older engines, hence the guard rather than a call.
  useEffect(() => {
    if (!flash) return
    const el = rows.current.get(flash)
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [flash, sections])

  const onDismiss = (item: WorkItem) => {
    if (!item.manual_attention) return
    dismissWorkItemAttention(slug, item.id, item.manual_attention.set_rev)
      .then(() => {
        toast([`dismissed the attention flag on “${item.title}”`])
        setBump((n) => n + 1)
      })
      // 409 (stale set_rev / already cleared) surfaces as an ordinary thrown
      // Error via req() — never a silent no-op or override
      .catch((e: Error) => toast([`error: ${e.message}`]))
  }

  const pickGroup = (m: DocketGroupMode) => { setGroupMode(m); writeGroupMode(m) }

  return (
    <div className="overlay" onClick={(e) => { e.stopPropagation(); close() }}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide docket-modal" onClick={(e) => e.stopPropagation()}>
        {/* THE FILTERS SIT AT THE RIGHT END, which is where the gallery's own
            show-retired checkbox ends up: `.gallery-modal .gallery-head` uses
            justify-content: space-between over two children, so its checkbox is
            pushed to the far right rather than sitting beside its heading. The
            user asked for that POSITION (2026-09-05, confirmed by Astra after a
            measurement disproved the earlier "adjacent to the heading" reading),
            so the spacer goes BEFORE the filters here. The close button is the
            one thing further right, and the gallery has no equivalent. */}
        <div className="gallery-head docket-head">
          <h3><DocketIcon fontSize="inherit" /> Work docket</h3>
          <span className="spacer" />
          <label className="checkline docket-showarchived"
            title="include archived and closed work items">
            <input type="checkbox" checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)} />
            Show archived
            {archivedCount > 0 && <span className="dim"> · {archivedCount}</span>}
          </label>
          <label className="checkline docket-showbacklog"
            title="include work that has not been approached or approved yet">
            <input type="checkbox" checked={showBacklog}
              onChange={(e) => setShowBacklog(e.target.checked)} />
            Show backlogged
            {backlogCount > 0 && <span className="dim"> · {backlogCount}</span>}
          </label>
          <button className="chip-x" title="close" onClick={close}>
            <CloseIcon fontSize="inherit" />
          </button>
        </div>
        {/* its own strip, so the header above stays as uncrowded as the
            gallery's (Astra 2026-09-05) */}
        <div className="docket-sortbar">
          <label className="dim" htmlFor="docket-group">Arrange</label>
          <select id="docket-group" className="docket-group-select" value={groupMode}
            onChange={(e) => pickGroup(e.target.value as DocketGroupMode)}>
            {GROUP_MODES.map((g) =>
              <option key={g.value} value={g.value}>{g.label}</option>)}
          </select>
          <span className="dim docket-sort-why">
            {groupMode === 'none'
              ? 'most recently updated first'
              : 'newest first inside each group'}
          </span>
        </div>
        <div className="mailpane">
          {!data
            ? <div className="dim pad">loading…</div>
            : rowCount === 0
              ? <div className="dim pad">no work items yet</div>
              : (
                <div className="mailer">
                  <div className="mailer-list">
                    {sections.map((s) => (
                      <div key={s.key}
                        className={'docket-section' + (s.tone ? ' tone-' + s.tone : '')}>
                        {s.heading && (
                          <div className="docket-group-head">
                            <span>{s.heading}</span>
                            <span className="dim docket-group-n">{s.items.length}</span>
                          </div>
                        )}
                        {s.items.map((r) => (
                          <DocketRow key={r.id} item={r} selected={r.id === selId}
                            onClick={() => setSelId(r.id === selId ? null : r.id)}
                            onDismiss={onDismiss} facts={facts}
                            onFocusAgent={onFocusAgent} close={close}
                            flash={r.id === flash}
                            rowRef={(el) => {
                              if (el) rows.current.set(r.id, el)
                              else rows.current.delete(r.id)
                            }} />
                        ))}
                      </div>
                    ))}
                  </div>
                  <div className="mailer-read">
                    {cur
                      ? <DocketPane key={cur.id} slug={slug} item={cur} toast={toast}
                          asksById={asksById} onDismiss={onDismiss}
                          close={close} onFocusAgent={onFocusAgent} facts={facts}
                          slugIndex={slugIndex} onGoToItem={goToItem} />
                      : <div className="dim pad mailer-none">select an item to view it</div>}
                  </div>
                </div>
              )}
        </div>
        <div className="docket-foot dim">Done items archive after 1 hour without an update.</div>
      </div>
    </div>
  )
}

function DocketRow({ item, selected, onClick, onDismiss, facts, onFocusAgent,
  close, flash, rowRef }: {
  item: WorkItem
  selected: boolean
  onClick: () => void
  onDismiss: (item: WorkItem) => void
  facts: Map<string, NodeFacts>
  onFocusAgent?: (agentId: string) => void
  close?: () => void
  /** briefly true after a slug link brought the reader here, so the row the
   *  link meant is identifiable among rows that all look alike */
  flash?: boolean
  rowRef?: (el: HTMLDivElement | null) => void
}) {
  const attention = item.effective_attention
  // active (white) / attention (orange) / backlog (its own quiet colour) /
  // archived (grey, darker bg). Archived wins over backlog, and attention wins
  // over both — the backend never hands us an archived attention row, but the
  // precedence is written here so the row cannot be ambiguous either way.
  const state = item.archived
    ? 'archived'
    : attention
      ? 'attention'
      : item.status === 'backlogged' ? 'backlog' : 'active'
  const cls = ['mailrow', 'docket-row', state, 'status-' + item.status,
    selected ? 'on' : '', flash ? 'docket-flash' : ''].filter(Boolean).join(' ')
  const label = attention ? 'Needs attention' : statusLabel(item.status)
  // Dismiss clears the MANUAL flag only — a question-only attention item has
  // nothing to dismiss (answering the question is the only way to clear it).
  const canDismiss = item.attention_sources.includes('manual')
  return (
    // THE NAME IN THE LIST IS THE SLUG (user 2026-09-05). The full descriptive
    // title is printed only in the detail pane; here it is the row's hover
    // title, so nothing is lost and the row stays one line of name.
    <div className={cls} title={item.title} onClick={onClick} ref={rowRef}>
      <div className="l1">
        <span className="mfrom docket-rowname">{itemName(item)}</span>
        <span className="mtime">{ago(item.docket_at ?? item.at)}</span>
      </div>
      <div className="l2">
        <span className={'docket-status status-' + item.status + (attention ? ' attention' : '')}>
          {label}
        </span>
        <span className="docket-updater">
          <ActorName actor={item.last_updater} facts={facts}
            onFocusAgent={onFocusAgent} close={close} />
        </span>
        {canDismiss && (
          <button className="badge docket-dismiss" title="clear this manually-raised flag"
            onClick={(e) => { e.stopPropagation(); onDismiss(item) }}>
            Dismiss
          </button>
        )}
      </div>
    </div>
  )
}

function DocketList({ heading, items, slugIndex, onGoToItem }: {
  heading: string
  items: string[]
  slugIndex: SlugIndex
  onGoToItem?: (id: string) => void
}) {
  return (
    <div className="docket-list">
      <div className="docket-list-heading dim">{heading}</div>
      {items.length === 0
        ? <div className="dim docket-list-empty">None</div>
        : <ul className="docket-list-items">
            {items.map((t, i) => (
              <li key={i}>
                <WorkRefText text={t} index={slugIndex} onPick={onGoToItem} />
              </li>
            ))}
          </ul>}
    </div>
  )
}

function DocketPane({ slug, item, toast, asksById, onDismiss, close, onFocusAgent,
  facts, slugIndex, onGoToItem }: {
  slug: string
  item: WorkItem
  toast: ToastFn
  asksById: Map<string, AskInfo>
  onDismiss: (item: WorkItem) => void
  close: () => void
  onFocusAgent?: (agentId: string) => void
  facts: Map<string, NodeFacts>
  slugIndex: SlugIndex
  onGoToItem?: (id: string) => void
}) {
  const attention = item.effective_attention
  const label = attention ? 'Needs attention' : statusLabel(item.status)
  const canDismiss = item.attention_sources.includes('manual')
  // a stable local so narrowing survives into the reply closure below
  // (TS does not narrow a property access across a nested arrow function)
  const lastUpdater = item.last_updater
  const manualAttn = item.manual_attention
  return (
    <>
      {/* THE ONLY PLACE THE FULL DESCRIPTIVE TITLE IS PRINTED (user
          2026-09-05) — the list is named by slug alone. */}
      <div className="mailer-head docket-pane-head">
        <b>{item.title || '(untitled)'}</b>
        <span className="spacer" />
        {canDismiss && (
          <button className="badge docket-dismiss" onClick={() => onDismiss(item)}>
            Dismiss
          </button>
        )}
      </div>
      <div className={'dim docket-pane-sub' + (attention ? ' docket-pane-sub-attn' : '')}>
        <span className={'docket-status status-' + item.status + (attention ? ' attention' : '')}>
          {label}
        </span>
        <SlugText item={item} />
        {' · Updated ' + ago(item.docket_at ?? item.at)}
        {lastUpdater?.node && (
          <>
            {' by '}
            <ActorName actor={lastUpdater} facts={facts}
              onFocusAgent={onFocusAgent} close={close} />
          </>
        )}
      </div>
      {/* THE DESCRIPTION, first thing in the pane (user 2026-09-05): the
          problem currently faced, then the proposed solution. Mandatory on
          every item created from now on; older items may genuinely have none,
          and that is said plainly rather than papered over. */}
      <div className="docket-desc">
        <div className="docket-list-heading dim">DESCRIPTION</div>
        {item.objective
          ? <div className="docket-desc-body">
              <WorkRefText text={item.objective} index={slugIndex}
                onPick={onGoToItem} />
            </div>
          : <div className="dim docket-list-empty">
              no description — this item predates the rule that every item
              states its problem and proposed solution
            </div>}
      </div>
      <DocketList heading="DONE SO FAR" items={item.done_so_far}
        slugIndex={slugIndex} onGoToItem={onGoToItem} />
      <DocketList heading="WORKING ON / NEXT" items={item.working_on_next}
        slugIndex={slugIndex} onGoToItem={onGoToItem} />
      {manualAttn && (
        <div className="docket-attention-box">
          <div className="docket-question-head">
            Manual attention from{' '}
            <ActorName actor={manualAttn.by} facts={facts}
              onFocusAgent={onFocusAgent} close={close} />
          </div>
          <div>
            <WorkRefText text={manualAttn.reason} index={slugIndex}
              onPick={onGoToItem} />
          </div>
        </div>
      )}
      {item.questions.map((q) => {
        const ask = asksById.get(q.ask_id)
        // a batch card may cover tabs from OTHER items too (one agent, one
        // open batch) — the full card still answers ALL its tabs together
        // (Astra ruling: preserve original full-batch answer semantics,
        // never silently submit only the tabs shown here), so a note makes
        // that linkage explicit instead of implying this box is scoped to
        // just this item's tab.
        const otherTabs = ask?.tabs && ask.tabs.length > (q.tabs?.length ?? 0)
        return (
          <div key={q.ask_id} className="docket-question-box">
            <div className="docket-question-head">
              Question from{' '}
              <button className="cc-name cc-name-jump" title={`focus ${q.node}'s desk`}
                onClick={() => { close(); onFocusAgent?.(q.node) }}>
                {q.node}
              </button>
            </div>
            {otherTabs && (
              <div className="dim docket-question-note">
                this batch also covers other items — answering it resolves every tab at once
              </div>
            )}
            {ask
              ? <AskCard ask={ask} slug={slug} toast={toast} />
              : <div className="dim">this question is no longer open</div>}
          </div>
        )
      })}
      {lastUpdater ? (
        <>
          <div className="dim docket-reply-label">
            Reply to{' '}
            <ActorName actor={lastUpdater} facts={facts}
              onFocusAgent={onFocusAgent} close={close} />
            {' · last updated this item'}
          </div>
          <MailReplyBox target={lastUpdater.node}
            onSend={(text) => replyWorkItem(slug, item.id, text)
              .then((r) => {
                if (r.deferred) {
                  toast([`${lastUpdater.node} is archived — the reply waits for rehire`])
                } else {
                  toast([`sent to ${lastUpdater.node}`])
                }
              })
              .catch((e: Error) => {
                toast([`error: ${e.message}`])
                throw e
              })} />
        </>
      ) : (
        <div className="dim docket-reply-label">no status update yet — nothing to reply to</div>
      )}
    </>
  )
}
