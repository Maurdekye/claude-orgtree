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
import { DocReader } from './docs'
import { AgentName } from './identity'
import { MailReplyBox } from './mail'
import { ago, useEsc, usePolled } from './shared'
import { buildMentionIndex } from './workrefs'
import type { MentionIndex } from './workrefs'
import { RefProse } from './reflinks'
import type { RefWorld, ResolvedRef } from './reflinks'
import type { RefKind, TypedRef } from './workrefs'

// `review` is the AGENT check, not the user's (user ruling 2026-09-05)
const REVIEW_HELP ='Review by agents — a request for you rides the attention flag or a question'
const STATUS_LABEL: Record<string, string> = {
  backlogged: 'Backlogged',
  open: 'Open',
  in_progress: 'In progress',
  blocked: 'Blocked',
  waiting: 'Waiting',
  review: 'Agent review',
  done: 'Done',
  superseded: 'Superseded',
  dropped: 'Dropped',
}
// `waiting` is an EXTERNAL event, never the user — asking the user is the
// attention flag (user ruling 2026-09-05)
const WAITING_HELP = 'Active work waiting on an external event, not on the user — it names the event and how the agent will hear of it, and stops its own idle reminders until then'
const statusLabel = (status: string): string => STATUS_LABEL[status] ?? status
/** hover help, only where the status word can be read two ways */
const statusHelp = (status: string): string | undefined =>
  (status === 'review' ? REVIEW_HELP
    : status === 'waiting' ? WAITING_HELP : undefined)

/** Group-by-status order, exactly as specified: effective attention first,
 *  then blocked, in_progress, review, open, waiting, done, then everything
 *  else that is closed. `waiting` sits below the states somebody can act on
 *  today and above the backlog, because it is real work with nothing to do
 *  right now. A status the backend adds later lands in "Other" rather than
 *  vanishing — an unknown row must still be reachable. */
const STATUS_GROUPS: { key: string; heading: string }[] = [
  { key: 'attention', heading: 'Needs attention' },
  { key: 'blocked', heading: 'Blocked' },
  { key: 'in_progress', heading: 'In progress' },
  { key: 'review', heading: 'Agent review' },
  { key: 'open', heading: 'Open' },
  { key: 'waiting', heading: 'Waiting on an event' },
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

/** What we may honestly say about the agent an item points at. A node keeps
 *  its identity across session generations: when the node is live, the
 *  docket's name resolves to that live successor and its current model. Only
 *  a node that is actually retired gets the historical treatment. */
export type ActorFit = 'current' | 'retired' | 'gone'

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
  // The actor's generation records who wrote the item, but it does not turn
  // the node id into a different identity. A live node is therefore the
  // current destination even when the item names an archived predecessor.
  if (!n.live) return { fit: 'retired', tier: n.tier }
  return { fit: 'current', tier: n.tier }
}

const FIT_WHY: Record<ActorFit, string | null> = {
  current: null,
  // a RETIRED agent keeps its recorded model, so the chip stays and the row
  // explains itself
  retired: 'this agent has been retired',
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
  // ⚠ `why` STAYS ON THE WRAPPER, and `tier` is passed through EXACTLY as
  // actorFit returned it. A live node gets the model it wears now, even when
  // this work was written by an earlier generation; a missing node gets no
  // invented chip.
  return (
    <span className={'docket-actor fit-' + fit} title={why ?? undefined}>
      {/* the ellipsis lives on the NAME element, not on this inline-flex
          wrapper: text-overflow does nothing on a flex container, which is
          why the long name used to run under the Dismiss button instead of
          truncating (Astra review 2026-09-05) — hence `nameClass` */}
      <AgentName id={actor.node} tier={tier} nameClass="docket-actor-name"
        onFocus={onFocusAgent
          ? (id) => { close?.(); onFocusAgent(id) }
          : undefined} />
    </span>
  )
}

/** The by-agent grouping's heading: the agent itself, not a word about it.
 *
 *  ⚠ THE HEADING KEEPS ITS OWN TYPOGRAPHY — the stylesheet hands `.cc-name`
 *  the heading's font back, so this adds a chip and a click, not a restyle. */
function GroupAgentHead({ agent, items, facts, onFocusAgent, close }: {
  agent: string
  items: WorkItem[]
  facts: Map<string, NodeFacts>
  onFocusAgent?: (agentId: string) => void
  close?: () => void
}) {
  const { fit, tier, why } = groupIdentity(items, facts)
  return (
    <span className={'docket-group-agent' + (fit ? ' fit-' + fit : '')}>
      <AgentName id={agent} tier={tier} why={why} nameClass="docket-group-name"
        onFocus={onFocusAgent
          ? (id) => { close?.(); onFocusAgent(id) }
          : undefined} />
    </span>
  )
}

/** The item's readable name. The slug IS the name — there is no other
 *  identifier and no fallback; the server never serves an item without one. */
export const itemName = (item: WorkItem): string => item.slug

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
      >
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

export interface Section {
  key: string
  heading: string | null
  items: WorkItem[]
  /** styling hook for the two appended groups, so they read as set apart
   *  from current work rather than as two more status buckets */
  tone?: 'backlog' | 'archive'
  /** the AGENT this group is named after, when the heading is an agent id and
   *  not a word. Set only by the by-agent grouping, and never for the
   *  owner-less group — `Unassigned` is a label, not a name that resolves. */
  agent?: string
}

/** The identity a GROUP HEADING may claim.
 *
 *  ⚠ THE CHIP APPEARS ONLY WHEN EVERY OWNER IN THE GROUP ATTRIBUTES THE SAME
 *  MODEL. One heading names one agent, but its group can contain current,
 *  retired, missing, or mixed references. If those references do not resolve
 *  to one fit/tier, the heading claims no single model and says why. */
export function groupIdentity(items: WorkItem[], facts: Map<string, NodeFacts>):
  { fit?: ActorFit; tier?: string; why: string | null } {
  const seen = new Map<string, { fit: ActorFit; tier?: string }>()
  for (const it of items) {
    const r = actorFit(it.owner, facts)
    seen.set(r.fit + '/' + (r.tier ?? ''), r)
  }
  // an empty group is not a disagreement (and never reaches the screen)
  if (seen.size === 0) return { why: null }
  const only = seen.size === 1 ? [...seen.values()][0] : undefined
  if (!only) {
    return {
      why: 'this group holds references with different status or model '
        + 'identity, so no one model can be attributed to the group',
    }
  }
  return { fit: only.fit, tier: only.tier, why: FIT_WHY[only.fit] }
}

/** One rendered line: the item, how deep it sits, and how many children of
 *  ITS OWN are in this same section. */
export interface DocketRowInfo {
  item: WorkItem
  depth: number
  kids: number
}

/** One section's flat, server-ordered list as the nested display order.
 *
 *  ⚠ THE SERVER'S ORDER IS STILL THE ORDER: this only re-parents, so there is
 *  no second comparator to drift out of step with `ledger.work_list`.
 *  ⚠ A parent in ANOTHER section is not a parent here — the child renders as a
 *  root rather than vanishing, because losing a row would hide work.
 *  ⚠ It cannot hang on a cycle: the walk is bounded and strays are appended,
 *  so a bad document costs nesting and never the list. */
export function nestRows(items: WorkItem[],
                         collapsed: ReadonlySet<string>): DocketRowInfo[] {
  const here = new Set(items.map((i) => i.slug))
  const kids = new Map<string, WorkItem[]>()
  const roots: WorkItem[] = []
  for (const it of items) {
    const p = it.parent && here.has(it.parent) && it.parent !== it.slug
      ? it.parent : null
    if (!p) { roots.push(it); continue }
    const list = kids.get(p)
    if (list) list.push(it)
    else kids.set(p, [it])
  }
  // ⚠ REACHABILITY IGNORES THE FOLD; the display walk applies it. "Not drawn
  // because you folded it" and "not drawn because nothing leads here" are
  // different states, and only the second is rescued below.
  const reachable = new Set<string>()
  const mark = (it: WorkItem) => {
    if (reachable.has(it.slug)) return
    reachable.add(it.slug)
    for (const k of kids.get(it.slug) ?? []) mark(k)
  }
  for (const r of roots) mark(r)

  const out: DocketRowInfo[] = []
  const seen = new Set<string>()
  const walk = (it: WorkItem, depth: number) => {
    if (seen.has(it.slug)) return
    seen.add(it.slug)
    const mine = kids.get(it.slug) ?? []
    out.push({ item: it, depth, kids: mine.length })
    if (collapsed.has(it.slug)) return
    for (const k of mine) walk(k, depth + 1)
  }
  for (const r of roots) walk(r, 0)
  // only what a CYCLE stranded — never what a fold hid
  for (const it of items) if (!reachable.has(it.slug)) walk(it, 0)
  return out
}

/** Every ancestor of `slug` present in `items`, nearest first — what has to be
 *  expanded for a row to be on screen at all. Bounded against a cycle. */
export function ancestorsOf(items: WorkItem[], slug: string): string[] {
  const by = new Map(items.map((i) => [i.slug, i]))
  const out: string[] = []
  const seen = new Set<string>([slug])
  let cur = by.get(slug)?.parent ?? null
  while (cur && by.has(cur) && !seen.has(cur)) {
    seen.add(cur)
    out.push(cur)
    cur = by.get(cur)?.parent ?? null
  }
  return out
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
      // the id travels SEPARATELY from the heading text, so the renderer
      // never guesses whether a heading is a name: `Unassigned` is a word
      if (who !== UNASSIGNED) {
        out.push({ key: 'ag:' + who, heading: who, items, agent: who })
      }
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

export function DocketModal({ slug, toast, close, tree, onFocusAgent,
  jumpTo, onJumpHandled, onOpenMail }: {
  slug: string
  toast: ToastFn
  close: () => void
  tree: TreePayload
  /** the existing presented-document agent navigation (gallery.tsx's
   *  DocPane) — user ruling 2026-09-05: agent identities in a mail-idiom
   *  detail pane are clickable links using this exact behavior everywhere
   *  it appears, docket included. */
  onFocusAgent?: (agentId: string) => void
  /** open AT this item: a tool chip's docket link names the item a work write
   *  acted on (user 2026-09-05). Consumed once — see the effect below. */
  jumpTo?: string | null
  onJumpHandled?: () => void
  /** open a mail reference somewhere that actually owns a mailbox. The docket
   *  does not: the three boxes (the user's, the org's, a node's) are three
   *  different panels, and only the shell above this one can route between
   *  them.
   *
   *  ⚠ IT IS OPTIONAL, AND `handles` FOLLOWS IT. Absent, a mail token renders
   *  "not from here" — which stays TRUE, because nothing here would open it.
   *  Advertising mail unconditionally and then dropping the click on the floor
   *  is the live-looking control that does nothing. */
  onOpenMail?: (ref: TypedRef) => void
}) {
  // ⚠ ONE DOCUMENT READER, OPENED BY REFERENCE. A `@doc:` token in an item's
  // prose used to say "not opened from this panel", which was honest but was
  // never the destination — a reference the user cannot follow is half a
  // feature (Astra 2026-09-05). The reader is `DocReader`, the same one the
  // canvas chips open, so the fetch is the EXACT get by id: it distinguishes
  // "still loading" from "no such document" by itself, which is precisely the
  // judgement this panel cannot make on its own (it holds no document list).
  const [docView, setDocView] = useState<string | null>(null)
  // ⚠ ESCAPE BELONGS TO THE TOP-MOST THING ON SCREEN. Both listeners sit on
  // `window`, so an unguarded Escape with the reader open closes the reader
  // AND the docket underneath it — the user asked to back out of a document
  // and lost the panel they were reading from.
  const escClose = useCallback(() => { if (!docView) close() }, [docView, close])
  useEsc(escClose)
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
    for (const item of archivedCache) map.set(item.slug, item)
    for (const item of backlogCache) map.set(item.slug, item)
    for (const item of (data?.archived ?? [])) map.set(item.slug, item)
    for (const item of (data?.backlogged ?? [])) map.set(item.slug, item)
    for (const item of active) map.set(item.slug, item)
    return map
  }, [active, data?.archived, data?.backlogged, archivedCache, backlogCache])
  const cur = allKnown.get(selId ?? '')
  const asksById = new Map<string, AskInfo>((tree.asks ?? []).map((a) => [a.id, a]))

  // ---- names in prose become links to the item or the agent they name
  //
  // Built from `allKnown` and from the tree this panel was handed — exactly
  // what this org served this viewer — so a name from another org is in
  // neither map and is never marked. Same-org by construction, not by a check
  // somebody can forget.
  //
  // ⚠ AGENTS COME FROM `facts`, THE LIVE TREE: only a name that still resolves
  // to somebody links, and the tier it carries is that agent's CURRENT model,
  // which is what a mention navigates to. An agent dissolved out of the tree
  // leaves prose as prose.
  const refIndex = useMemo(
    () => buildMentionIndex(
      allKnown.values(),
      [...facts].map(([id, f]) => [id, f.tier] as const)),
    [allKnown, facts])
  // ---- and the CANONICAL references (`@item:org/slug`) in the same prose
  //
  // ⚠ A SEPARATE ITEM MAP, NOT `refIndex`. That one deliberately merges items
  // and agents into one namespace where a colliding name resolves to the item
  // — the right rule for a bare word. A canonical token has already said which
  // kind it means, so asking the merged map would let the bare-name collision
  // rule overrule an explicit `@agent:` token.
  //
  // ⚠ THE ITEM MAP IS AUTHORITATIVE HERE AND NOWHERE ELSE. This panel holds
  // every item the org served — active, archived and backlogged — so it can
  // say, truthfully, that a named item does not exist. Until the first
  // response lands it says `loading` instead, which is NOT the same claim: an
  // empty map would report every real reference as missing for as long as the
  // fetch takes, which is exactly when the panel is being read.
  //
  // ⚠ `handles` IS DERIVED FROM WHAT IS WIRED UP, NOT DECLARED. Items and
  // agents are always openable here; a document is openable because this
  // component now renders its own reader; mail is openable ONLY when a caller
  // handed down `onOpenMail`. Written as a literal list it would drift the
  // moment one of those callbacks was dropped from a call site, and the chip
  // would keep advertising an opener that no longer exists.
  //
  // ⚠ AND `docs` STAYS UNSET ON PURPOSE. This panel holds no document list, so
  // it must not judge one: `undefined` means "do not judge — the destination
  // will", and the destination is the reader below, which reports "could not
  // load the document: …" from the exact GET. An empty Map here would call
  // every real document missing.
  const refWorld = useMemo<RefWorld>(() => {
    const handles = new Set<RefKind>(['item', 'agent', 'doc'])
    if (onOpenMail) handles.add('mail')
    return {
      org: slug,
      items: data
        ? new Map([...allKnown.keys()].map((s) => [s, s]))
        : 'loading',
      agents: new Map([...facts.keys()].map((id) => [id, id])),
      handles,
    }
  }, [slug, data, allKnown, facts, onOpenMail])
  const [flash, setFlash] = useState<string | null>(null)
  const rows = useRef(new Map<string, HTMLDivElement>())
  // COLLAPSE IS OPT-IN. Everything starts expanded, because a docket that
  // hides work by default is worse than one that is long; the arrow is how
  // you make it shorter. Per-panel, not persisted — it is a reading posture,
  // not org state.
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(
    () => new Set<string>())
  const toggleFold = useCallback((name: string) => {
    setCollapsed((c) => {
      const next = new Set(c)
      if (!next.delete(name)) next.add(name)
      return next
    })
  }, [])

  const goToItem = useCallback((id: string) => {
    const it = allKnown.get(id)
    if (!it) return           // not ours to show — never a broken selection
    // REVEAL BEFORE SELECT. A backlogged or archived item has no row while its
    // group is filtered out, and selecting an invisible row would look like
    // the link did nothing.
    if (it.archived) setShowArchived(true)
    else if (it.status === 'backlogged') setShowBacklog(true)
    // ⚠ AND OPEN ITS ANCESTORS: a collapsed parent means the row is not on
    // screen, so the link would appear to do nothing.
    const line = ancestorsOf([...allKnown.values()], id)
    if (line.length) {
      setCollapsed((c) => {
        if (!line.some((a) => c.has(a))) return c   // no needless re-render
        const next = new Set(c)
        for (const a of line) next.delete(a)
        return next
      })
    }
    setSel({ slug, id })
    setFlash(id)
  }, [allKnown, slug])

  /** a canonical reference clicked. ONLY the kinds `refWorld.handles` admits
   *  can arrive here — anything else was rendered inert and never became a
   *  button — but the switch is exhaustive anyway, because a silent no-op is
   *  how a control ends up looking live and doing nothing.
   *
   *  ⚠ THE `mail` ARM IS GUARDED BY THE SAME CALLBACK THAT PUT `mail` IN
   *  `handles`, so the two cannot disagree: no callback, no chip, no arm. */
  const openRef = useCallback((r: ResolvedRef) => {
    if (r.ref.kind === 'item') goToItem(r.ref.id)
    else if (r.ref.kind === 'agent') onFocusAgent?.(r.ref.id)
    else if (r.ref.kind === 'doc') setDocView(r.ref.id)
    else if (r.ref.kind === 'mail') onOpenMail?.(r.ref)
  }, [goToItem, onFocusAgent, onOpenMail])

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

  // ⚠ A JUMP WAITS FOR ITS ITEM, then is consumed once. The panel mounts
  // before the first poll answers, so acting immediately would silently do
  // nothing. A name this org does not have is discarded rather than held —
  // it may be unreadable to this viewer, or gone.
  //
  // ⚠ "ONCE" IS ENFORCED HERE, not by the parent clearing the prop: the deps
  // change identity on every poll, so a check on `jumpTo` alone re-fires and
  // drags the selection back each time the user moves it.
  const doneJump = useRef<string | null>(null)
  useEffect(() => {
    if (!jumpTo || !data || doneJump.current === jumpTo) return
    doneJump.current = jumpTo
    if (allKnown.has(jumpTo)) goToItem(jumpTo)
    onJumpHandled?.()
  }, [jumpTo, data, allKnown, goToItem, onJumpHandled])

  const onDismiss = (item: WorkItem) => {
    if (!item.manual_attention) return
    dismissWorkItemAttention(slug, item.slug, item.manual_attention.set_rev)
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
    <>
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
                            {/* an agent's head IS that agent; a status, the
                                backlog, the archive and `Unassigned` are
                                words and stay plain spans */}
                            {s.agent
                              ? <GroupAgentHead agent={s.agent} items={s.items}
                                  facts={facts} onFocusAgent={onFocusAgent}
                                  close={close} />
                              : <span>{s.heading}</span>}
                            <span className="dim docket-group-n">{s.items.length}</span>
                          </div>
                        )}
                        {nestRows(s.items, collapsed).map((row) => (
                          <DocketRow key={row.item.slug} item={row.item}
                            selected={row.item.slug === selId}
                            depth={row.depth} kids={row.kids}
                            folded={collapsed.has(row.item.slug)}
                            onFold={() => toggleFold(row.item.slug)}
                            onClick={() => setSelId(
                              row.item.slug === selId ? null : row.item.slug)}
                            onDismiss={onDismiss} facts={facts}
                            onFocusAgent={onFocusAgent} close={close}
                            flash={row.item.slug === flash}
                            rowRef={(el) => {
                              if (el) rows.current.set(row.item.slug, el)
                              else rows.current.delete(row.item.slug)
                            }} />
                        ))}
                      </div>
                    ))}
                  </div>
                  <div className="mailer-read">
                    {cur
                      ? <DocketPane key={cur.slug} slug={slug} item={cur} toast={toast}
                          asksById={asksById} onDismiss={onDismiss}
                          close={close} onFocusAgent={onFocusAgent} facts={facts}
                          refIndex={refIndex} onGoToItem={goToItem}
                          refWorld={refWorld} onOpenRef={openRef} />
                      : <div className="dim pad mailer-none">select an item to view it</div>}
                  </div>
                </div>
              )}
        </div>
        <div className="docket-foot dim">Done items archive after 1 hour without an update.</div>
      </div>
    </div>
    {/* ⚠ A SIBLING, NOT A CHILD. Nested inside the docket's own `.overlay`,
        a click on the reader's backdrop would bubble into the docket's
        backdrop handler and close BOTH. As siblings the reader is simply the
        later element at the same z-index, so it paints on top and keeps its
        clicks to itself. */}
    {docView && (
      <DocReader slug={slug} docId={docView} toast={toast}
        close={() => setDocView(null)} />
    )}
    </>
  )
}

function DocketRow({ item, selected, onClick, onDismiss, facts, onFocusAgent,
  close, flash, rowRef, depth = 0, kids = 0, folded = false, onFold }: {
  item: WorkItem
  selected: boolean
  /** w2d5fab0a elements 1 and 2: how deep this row sits, and whether it has
   *  children of its own to fold away. The connecting lines are drawn from
   *  `depth` in CSS rather than with spacer elements. */
  depth?: number
  kids?: number
  folded?: boolean
  onFold?: () => void
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
    selected ? 'on' : '', flash ? 'docket-flash' : '',
    depth > 0 ? 'docket-child' : '',
    kids > 0 ? 'docket-parent' : ''].filter(Boolean).join(' ')
  const label = attention ? 'Needs attention' : statusLabel(item.status)
  // Dismiss clears the MANUAL flag only — a question-only attention item has
  // nothing to dismiss (answering the question is the only way to clear it).
  const canDismiss = item.attention_sources.includes('manual')
  return (
    // THE NAME IN THE LIST IS THE SLUG (user 2026-09-05). The full descriptive
    // title is printed only in the detail pane; here it is the row's hover
    // title, so nothing is lost and the row stays one line of name.
    <div className={cls} title={item.title} onClick={onClick} ref={rowRef}
      style={depth ? { '--docket-depth': depth } as React.CSSProperties : undefined}>
      <div className="l1">
        {/* TWO SEPARATE CLICK TARGETS (the approved design's own note): the
            arrow folds, the row selects. A parent's own details stay reachable
            even when it has children, so folding is never the only thing a
            click on a parent can do. */}
        {kids > 0 && (
          <button className={'docket-fold' + (folded ? ' folded' : '')}
            title={folded ? `show ${kids} sub-item${kids === 1 ? '' : 's'}`
              : `hide ${kids} sub-item${kids === 1 ? '' : 's'}`}
            aria-expanded={!folded}
            onClick={(e) => { e.stopPropagation(); onFold?.() }}>▾</button>
        )}
        {/* w2d5fab0a element 3: the small status dot beside the coloured left
            edge. Two readings of the same fact on purpose — the edge is easy
            to lose against a selected row's tint, and the dot sits with the
            name where the eye already is. It carries NO text, so the row's
            accessible name is still the item's name and nothing else. */}
        <span className={'docket-dot status-' + item.status
          + (attention ? ' attention' : '')} aria-hidden="true" />
        <span className="mfrom docket-rowname">{itemName(item)}</span>
        <span className="mtime">{ago(item.docket_at ?? item.at)}</span>
      </div>
      <div className="l2">
        <span className={'docket-status status-' + item.status + (attention ? ' attention' : '')}
          title={attention ? undefined : statusHelp(item.status)}>
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

function DocketList({ heading, items, refIndex, onGoToItem, onGoToAgent, mark,
  refWorld, onOpenRef }: {
  heading: string
  items: string[]
  refIndex: MentionIndex
  onGoToItem?: (id: string) => void
  onGoToAgent?: (id: string) => void
  refWorld: RefWorld
  onOpenRef?: (r: ResolvedRef) => void
  /** w2d5fab0a element 4: the two progress lists get DIFFERENT bullets —
   *  a tick for what is finished, an arrow for what is still ahead. They sit
   *  one under the other and read as one wall of dots otherwise, and which
   *  list an entry is in is the single most important thing about it. */
  mark: 'done' | 'next'
}) {
  return (
    <div className="docket-list">
      <div className="docket-list-heading dim">{heading}</div>
      {items.length === 0
        ? <div className="dim docket-list-empty">None</div>
        : <ul className={'docket-list-items mark-' + mark}>
            {items.map((t, i) => (
              <li key={i}>
                <RefProse text={t} world={refWorld} onOpen={onOpenRef}
                  index={refIndex} onPick={onGoToItem}
                  onFocusAgent={onGoToAgent} />
              </li>
            ))}
          </ul>}
    </div>
  )
}

function DocketPane({ slug, item, toast, asksById, onDismiss, close, onFocusAgent,
  facts, refIndex, onGoToItem, refWorld, onOpenRef }: {
  slug: string
  item: WorkItem
  toast: ToastFn
  asksById: Map<string, AskInfo>
  onDismiss: (item: WorkItem) => void
  close: () => void
  onFocusAgent?: (agentId: string) => void
  facts: Map<string, NodeFacts>
  refIndex: MentionIndex
  onGoToItem?: (id: string) => void
  refWorld: RefWorld
  onOpenRef?: (r: ResolvedRef) => void
}) {
  const attention = item.effective_attention
  const label = attention ? 'Needs attention' : statusLabel(item.status)
  const canDismiss = item.attention_sources.includes('manual')
  // a stable local so narrowing survives into the reply closure below
  // (TS does not narrow a property access across a nested arrow function)
  const lastUpdater = item.last_updater
  const manualAttn = item.manual_attention
  // the state's own information, chosen BY THE CURRENT STATUS rather than by
  // whichever field happens to be populated: a stale value must never be
  // rendered as if it described where the item stands now
  const stateInfo =
    item.status === 'blocked'
      ? { heading: 'BLOCKED BECAUSE', text: item.blocked_reason ?? '' }
      : item.status === 'waiting'
        ? { heading: 'WAITING FOR', text: item.waiting_reason ?? '' }
        : null
  // as an actor line does: close first, or the desk opens behind this modal
  const goToAgent = onFocusAgent
    ? (id: string) => { close(); onFocusAgent(id) }
    : undefined
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
        <span className={'docket-status status-' + item.status + (attention ? ' attention' : '')}
          title={attention ? undefined : statusHelp(item.status)}>
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
              <RefProse text={item.objective} world={refWorld}
                onOpen={onOpenRef} index={refIndex} onPick={onGoToItem}
                onFocusAgent={goToAgent} />
            </div>
          : <div className="dim docket-list-empty">
              no description — this item predates the rule that every item
              states its problem and proposed solution
            </div>}
      </div>
      {/* STATE INFORMATION (user 2026-09-05). Blocked and waiting each owe an
          explanation, so the pane shows the one that belongs to the state the
          item is actually in. Older blocked items may carry none: say that
          rather than render an empty box. Reasons for states the item has
          left are not shown — the backend clears them. */}
      {stateInfo && (
        <div className="docket-desc">
          <div className="docket-list-heading dim">{stateInfo.heading}</div>
          {stateInfo.text
            ? <div className="docket-desc-body">{stateInfo.text}</div>
            : <div className="dim docket-list-empty">
                not recorded — this item entered {item.status} before the rule
                that the state says why
              </div>}
        </div>
      )}
      <DocketList heading="DONE SO FAR" items={item.done_so_far} mark="done"
        refIndex={refIndex} onGoToItem={onGoToItem} onGoToAgent={goToAgent}
        refWorld={refWorld} onOpenRef={onOpenRef} />
      <DocketList heading="WORKING ON / NEXT" items={item.working_on_next}
        mark="next" refIndex={refIndex} onGoToItem={onGoToItem}
        onGoToAgent={goToAgent}
        refWorld={refWorld} onOpenRef={onOpenRef} />
      {manualAttn && (
        <div className="docket-attention-box">
          <div className="docket-question-head">
            Manual attention from{' '}
            <ActorName actor={manualAttn.by} facts={facts}
              onFocusAgent={onFocusAgent} close={close} />
          </div>
          {/* the reason is written as several lines; a plain <div> ran them together */}
          <div className="docket-attention-body">
            <RefProse text={manualAttn.reason} world={refWorld}
              onOpen={onOpenRef} index={refIndex} onPick={onGoToItem}
              onFocusAgent={goToAgent} />
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
            onSend={(text) => replyWorkItem(slug, item.slug, text)
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
