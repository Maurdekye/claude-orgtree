// canvas/reflinks.tsx — a canonical reference (`@item:org/slug`) rendered as
// something you can click, or as an explicit statement of why you cannot.
//
// The MATCHER lives in `workrefs.tsx` and the FORMAT in `backend/orgtree/refs.py`.
// This file is only the middle step nobody had written: deciding, for one token
// on one surface, WHICH OF FOUR THINGS IS TRUE.
//
//   ready     — this org, and something here can open it
//   pending   — this org, but the index that would confirm it has not arrived
//   absent    — this org, an authoritative index has arrived, it is NOT in it
//   foreign   — another org's token; NEVER resolved against what is on screen
//   elsewhere — real as far as we know, but THIS panel has no way to open it
//
// ⚠ THE THREE THAT ARE NOT `ready` ARE THE FEATURE. An emitter that writes a
// canonical token is CLAIMING a target exists, so quietly rendering a failed
// one as ordinary prose (what I originally proposed, and what Astra rejected)
// turns a broken pointer into text nobody can tell from a typo. Every outcome
// says which one it is, on the chip, in words.
//
// ⚠ AND `pending` IS NOT `absent`. "I have not looked yet" and "I looked and it
// is not there" are the same picture if you only track a boolean, and the wrong
// one of the two is a lie that appears while the page is still loading — which
// is exactly when a user is most likely to read it.

import { useCallback, useMemo } from 'react'
import type { ReactNode } from 'react'
import { REF_TOKEN_RE, WorkRefText, parseRef } from './workrefs'
import type { MentionIndex, RefKind, TypedRef } from './workrefs'

export type RefOutcome =
  'ready' | 'pending' | 'absent' | 'foreign' | 'elsewhere'

/** What a surface knows about one family of targets.
 *
 *  `undefined`   this surface holds NO authoritative list — do not judge.
 *                The chip stays clickable and the DESTINATION reports the
 *                outcome, because it is the one that actually looked.
 *  `'loading'`   a list is coming. → `pending`
 *  a Map         the list, complete. Membership decides, and the value is the
 *                trustworthy label (§9) — a document's real title beats its id.
 *
 *  ⚠ `undefined` VS AN EMPTY MAP IS THE WHOLE DISTINCTION. An empty Map says
 *  "there are none of these here", which makes every ref absent. Pass one only
 *  when you mean it. */
export type RefIndexOf<V = string> = ReadonlyMap<string, V> | 'loading' | undefined

export interface RefWorld {
  /** the org actually on screen. A token naming any other org is `foreign`. */
  org: string
  items?: RefIndexOf
  docs?: RefIndexOf
  agents?: RefIndexOf
  /** mail is not a list this side holds — the boxes are fetched per box, so a
   *  surface answers for the BOX (can I address it at all?) and the mail list
   *  answers for the message once it has loaded one. */
  mail?: (ref: TypedRef) => RefOutcome
  /** which kinds THIS panel can actually open. Omitted means all of them.
   *
   *  ⚠ THIS IS NOT THE SAME QUESTION AS WHETHER THE TARGET EXISTS, and
   *  collapsing the two is the mistake worth naming. The docket can jump to an
   *  item and focus an agent; it owns no mail reader and no document reader.
   *  Rendering a real document as `absent` there would state, in words, that
   *  it does not exist — a lie about the data caused by a limit of the panel.
   *  `elsewhere` says the true thing instead: it is there, just not from
   *  here. */
  handles?: ReadonlySet<RefKind>
}

export interface ResolvedRef {
  ref: TypedRef
  /** the literal token, kept so a caller can always fall back to showing it */
  token: string
  outcome: RefOutcome
  /** what to put on the chip: a real title where we have one, never a guess */
  label: string
  /** why, in words, for the tooltip and for the unavailable chip */
  why: string
}

const KIND_WORD: Record<TypedRef['kind'], string> = {
  item: 'docket item', doc: 'document', agent: 'agent', mail: 'mail',
}

function boxWord(ref: TypedRef): string {
  if (ref.box === 'user') return "the user's inbox"
  if (ref.box === 'org') return "the org inbox"
  return ref.node ? `${ref.node}'s inbox` : 'a mailbox'
}

function judge(index: RefIndexOf, id: string): [RefOutcome, string | null] {
  if (index === undefined) return ['ready', null]
  if (index === 'loading') return ['pending', null]
  const label = index.get(id)
  return label === undefined ? ['absent', null] : ['ready', label]
}

/** One token, decided. Pure — no fetching, no React, so the whole outcome
 *  table is testable without a DOM. */
export function resolveRef(ref: TypedRef, world: RefWorld): ResolvedRef {
  const token = refToken(ref)
  const kind = KIND_WORD[ref.kind]
  // ⚠ ORG FIRST, ALWAYS, BEFORE ANY LOOKUP. Prose gets copied between orgs and
  // two orgs can hold the same item slug, document id or agent name, so a
  // token from elsewhere that happened to match locally would open a
  // DIFFERENT, UNRELATED object and look like it had worked.
  if (ref.org !== world.org) {
    return { ref, token, outcome: 'foreign', label: ref.id,
      why: `this ${kind} belongs to the org “${ref.org}”, not to this one — `
        + 'it is not opened from here' }
  }
  // ⚠ SECOND, AND STILL BEFORE ANY LOOKUP: can this panel open this kind at
  // all? Asked after the index, a document the docket cannot open would be
  // reported `absent` — "no document named d1 in this org" — which is a
  // statement about the DATA made because of a limit of the PANEL.
  if (world.handles && !world.handles.has(ref.kind)) {
    return { ref, token, outcome: 'elsewhere', label: ref.id,
      why: `this ${kind} is not opened from this panel` }
  }
  if (ref.kind === 'mail') {
    const outcome = world.mail ? world.mail(ref) : 'ready'
    const where = boxWord(ref)
    // ⚠ `absent` AND `elsewhere` ARE DIFFERENT SENTENCES, and mail is where
    // the difference bites: "this org has no such mailbox" is a fact about the
    // ORG, while "not opened from here" is a fact about the PANEL. A surface
    // that can address every box it knows of still reports the box it has
    // never heard of as absent, and saying "cannot be opened from here" there
    // would send someone looking for a panel that would work.
    return { ref, token, outcome, label: `mail in ${where}`,
      why: outcome === 'ready' ? `open this mail in ${where}`
        : outcome === 'pending' ? `still finding ${where}`
          : outcome === 'absent' ? `${where} does not exist in this org`
            : `${where} cannot be opened from here` }
  }
  const index = ref.kind === 'item' ? world.items
    : ref.kind === 'doc' ? world.docs : world.agents
  const [outcome, label] = judge(index, ref.id)
  return {
    ref, token, outcome, label: label ?? ref.id,
    why: outcome === 'ready' ? `open the ${kind} ${ref.id}`
      : outcome === 'pending' ? `still loading this org's ${kind}s`
        : `no ${kind} named ${ref.id} in this org`,
  }
}

/** A ref back to its literal token. The renderer needs it for the `pending`
 *  and `absent` chips, where showing an id with no type would be a claim we
 *  have not earned. */
export function refToken(ref: TypedRef): string {
  if (ref.kind !== 'mail') return `@${ref.kind}:${ref.org}/${ref.id}`
  if (ref.box === 'node') {
    return `@mail:${ref.org}/node/${ref.node}/${ref.id}`
  }
  return `@mail:${ref.org}/${ref.box}/${ref.id}`
}

/** A mail reference as the app's EXISTING mail router understands it
 *  (`MailLinkFn`: the shape a chat chip's mail pointer already uses).
 *
 *  ⚠ ONE TRANSLATION, IN ONE PLACE, BECAUSE THE ROUTER'S VOCABULARY IS NOT
 *  THE TOKEN'S. `user_inbox` is a literal the router tests for, the org inbox
 *  is recognised by a LEADING `@`, and anything else is read as a node id — so
 *  an org box handed over as the bare slug would be routed to a node with the
 *  org's name, and on the day one exists it would open the wrong mailbox.
 *  Pure, so that mapping is pinned without mounting anything. */
export function mailRefTarget(ref: TypedRef): { id: string; to: string } {
  if (ref.box === 'user') return { id: ref.id, to: 'user_inbox' }
  if (ref.box === 'org') return { id: ref.id, to: `@org:${ref.org}` }
  return { id: ref.id, to: String(ref.node ?? '') }
}

/** A surface's whole reference wiring: what it judges against, and what it
 *  does when a chip is clicked. Passed around as ONE value so a component can
 *  never be handed a world with somebody else's routes. */
export interface RefRoutes {
  world: RefWorld
  onOpen: (r: ResolvedRef) => void
}

/** THE WORLD A PANEL JUDGES REFERENCES AGAINST, and the one place that decides
 *  what a click does. Every surface that renders a reference builds its world
 *  here — the shell panels, the docket, the desk — because written per surface
 *  they would drift, and the drift is invisible: one panel quietly calling a
 *  real item missing looks exactly like a real missing item.
 *
 *  `agents` is a MAP THE CALLER ALREADY HOLDS, not a payload this hook fetches.
 *  The shell flattens the tree; the desk is handed the canvas map. Only the
 *  keys are read, which is why the value type is unconstrained — the two
 *  callers hold different node shapes and neither needs converting.
 *
 *  ⚠ `null` IS `loading`, NOT "none". An empty Map says "this org has no
 *  agents", which makes every agent reference and every node mailbox absent —
 *  a lie that appears while the first fetch is still in flight, which is
 *  exactly when someone is most likely to read it.
 *
 *  ⚠ NO ITEM OR DOCUMENT INDEX, DELIBERATELY. No caller holds either list, and
 *  `undefined` means "do not judge — the destination will". The destinations
 *  do: the docket states an id it does not have, and the reader reports a
 *  document it cannot fetch.
 *
 *  ⚠ `handles` FOLLOWS THE CALLBACKS. A kind with no route reads "not opened
 *  from here", which stays true, rather than becoming a live chip that
 *  swallows the click. Pass `undefined` for a route you do not have — never a
 *  no-op function, which would claim the panel can do something it cannot.
 *
 *  ⚠ AND EVERY ROUTE IS CALLED WITH EXACTLY ONE ARGUMENT. Adapt at the call
 *  site — `(id) => centerOn(id)`, never bare `centerOn` — because a handoff
 *  that merely type-checks will hand a second argument to whatever is behind
 *  it. That is not hypothetical: `centerOn(id, z)` reads `z ?? fit`, so a
 *  DOM event arriving there is non-null, defeats the default, and the camera
 *  is computed from an object (Astra, 2026-09-05). */
export function useRefRoutes(org: string, agents: ReadonlyMap<string, unknown> | null,
  routes: {
    onOpenItem?: (itemSlug: string) => void
    onFocusAgent?: (agentId: string) => void
    onOpenDoc?: (docId: string) => void
    onOpenMail?: (ref: TypedRef) => void
  }): RefRoutes {
  const { onOpenItem, onFocusAgent, onOpenDoc, onOpenMail } = routes
  const world = useMemo<RefWorld>(() => {
    const handles = new Set<RefKind>()
    if (onOpenItem) handles.add('item')
    if (onFocusAgent) handles.add('agent')
    if (onOpenDoc) handles.add('doc')
    if (onOpenMail) handles.add('mail')
    return {
      org,
      agents: agents
        ? new Map([...agents.keys()].map((id) => [id, id]))
        : 'loading',
      mail: (r) => (r.box !== 'node' ? 'ready'
        : !agents ? 'pending'
          : agents.has(String(r.node ?? '')) ? 'ready' : 'absent'),
      handles,
    }
  }, [org, agents, onOpenItem, onFocusAgent, onOpenDoc, onOpenMail])
  const onOpen = useCallback((r: ResolvedRef) => {
    if (r.ref.kind === 'item') onOpenItem?.(r.ref.id)
    else if (r.ref.kind === 'agent') onFocusAgent?.(r.ref.id)
    else if (r.ref.kind === 'doc') onOpenDoc?.(r.ref.id)
    else if (r.ref.kind === 'mail') onOpenMail?.(r.ref)
  }, [onOpenItem, onFocusAgent, onOpenDoc, onOpenMail])
  // ⚠ THE CALLERS PASS INLINE ARROWS, so `routes` is a fresh object every
  // render and this memo recomputes with it. That is deliberate and it is
  // cheap: what it produces is compared by OUTCOME downstream (refmd's pass
  // returns having touched nothing when every chip still says the same
  // thing), so a new world object does not cost a rebuild — and pinning the
  // identity here would mean stale routes after any state change.
  return useMemo(() => ({ world, onOpen }), [world, onOpen])
}

/** ONE LINE OF PROSE AN AGENT WROTE, with any canonical reference in it made
 *  clickable. Plain text when the surface has no world — the honest rendering
 *  for a panel with nowhere to send anybody, and the reason this takes `refs`
 *  rather than reaching for a global.
 *
 *  ⚠ THE REACT RENDERER, NOT THE MARKDOWN ONE. Its call sites render text
 *  NODES — a checklist item, a status summary — so the prose is split into
 *  runs rather than walked as DOM. Using the markdown pass on them would
 *  claim they had been through `md()`, which they have not.
 *
 *  ⚠ AND IT IS DELIBERATELY NOT USED FOR MACHINE-WRITTEN LINES. A sentence
 *  the app composed ABOUT an agent (a progress note, a derived summary) can
 *  quote an agent's text, and linkifying it would turn a quotation into a
 *  claim that somebody wrote a reference there. */
export function Written({ text, refs }: { text: string; refs?: RefRoutes }) {
  if (!refs) return <>{text}</>
  return <TypedRefText text={text} world={refs.world} onOpen={refs.onOpen} />
}

export interface RefRun {
  text: string
  ref?: ResolvedRef
}

/** Prose split into runs, every canonical token decided. Concatenating every
 *  `text` reproduces the input exactly — same losslessness contract as
 *  `splitRefs`, and for the same reason: these surfaces are `pre-wrap`.
 *
 *  Text that merely LOOKS like a token — an email address, a stray `@item:` —
 *  never matches in the first place, so it is left exactly as written. */
export function splitTypedRefs(text: string, world: RefWorld): RefRun[] {
  const s = String(text ?? '')
  if (!s) return []
  const out: RefRun[] = []
  let last = 0
  // A FRESH REGEX EACH CALL, and NOT for the reason I first wrote down.
  // `exec` resets `lastIndex` to 0 when a scan runs out of matches, so running
  // this loop to exhaustion on a shared /g regex is in fact safe — I asserted
  // otherwise, and the mutation harness caught me by surviving. What a fresh
  // copy actually buys is independence from OTHER users of the exported
  // `REF_TOKEN_RE`: any caller that stops iterating early leaves a non-zero
  // `lastIndex` behind, and this function would then start mid-string.
  const re = new RegExp(REF_TOKEN_RE.source, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(s)) !== null) {
    const parsed = parseRef(m[0])
    // ⚠ UNREACHABLE BY CONSTRUCTION, AND SAID SO ON PURPOSE. `parseRef`
    // anchors the SAME pattern this scanner uses, so anything found here
    // parses. This is the narrowing TypeScript needs, not a guard — do not
    // read it as one, and do not "test" it, because it cannot fire. The
    // invariant that keeps it dead is pinned by §6c; if the two patterns ever
    // drift apart, that check goes red rather than this branch quietly
    // starting to eat text.
    if (!parsed) continue
    if (m.index > last) out.push({ text: s.slice(last, m.index) })
    out.push({ text: m[0], ref: resolveRef(parsed, world) })
    last = m.index + m[0].length
  }
  if (last < s.length) out.push({ text: s.slice(last) })
  return out
}

/** One reference on screen. `ready` is a button; the other three are inert
 *  and SAY SO — they are not styled to look clickable, because a control that
 *  looks live and does nothing is worse than one that explains itself. */
export function RefChip({ r, onOpen }: {
  r: ResolvedRef
  onOpen?: (r: ResolvedRef) => void
}) {
  const cls = `ref-chip ref-${r.ref.kind} ref-${r.outcome}`
  if (r.outcome === 'ready' && onOpen) {
    return (
      <button type="button" className={cls} title={r.why}
        onClick={(e) => { e.stopPropagation(); onOpen(r) }}>
        {r.label}
      </button>
    )
  }
  // ⚠ THE TOKEN IS SHOWN ON A FAILED REF, not the bare id. Whoever has to fix
  // the reference needs to see what was actually written, and on `foreign`
  // the org segment is the entire explanation.
  return (
    <span className={cls} title={r.why}>
      {r.outcome === 'ready' ? r.label : r.token}
      <span className="ref-why">{r.outcome === 'pending' ? '…'
        : r.outcome === 'foreign' ? 'other org'
          : r.outcome === 'elsewhere' ? 'not from here' : 'unavailable'}</span>
    </span>
  )
}

/** Prose with its canonical references rendered. With no `onOpen` every chip
 *  is inert, which is the correct read-only rendering rather than a special
 *  case each surface has to remember. */
/** THE ONE A SURFACE ACTUALLY RENDERS. Two matchers run over the same prose:
 *  canonical tokens (`@item:org/slug`), then — on the text BETWEEN them — the
 *  bare-slug mentions the docket already linked.
 *
 *  ⚠ TYPED FIRST, AND THAT ORDER IS LOAD-BEARING. A token contains a slug:
 *  `@item:orgtree/alpha` ends in `alpha`. Running the bare matcher over the
 *  whole string would find that inner `alpha` and cut the token in half. Its
 *  boundary rules do reject a slug preceded by `/`, so today it would decline
 *  anyway — but that is a second, independent rule agreeing by luck, and this
 *  ordering does not depend on it. */
export function RefProse({ text, world, onOpen, index, onPick,
  onFocusAgent }: {
  text: string
  world: RefWorld
  onOpen?: (r: ResolvedRef) => void
  /** the bare-name index — items and agents in one namespace. Omit it and
   *  only canonical tokens are linked. */
  index?: MentionIndex
  onPick?: (name: string) => void
  onFocusAgent?: (id: string) => void
}) {
  const runs = useMemo(() => splitTypedRefs(text, world), [text, world])
  return (
    <>
      {runs.map((p, i) => (p.ref
        ? <RefChip key={i} r={p.ref} onOpen={onOpen} />
        : (index && index.size
          ? <WorkRefText key={i} text={p.text} index={index} onPick={onPick}
              onFocusAgent={onFocusAgent} />
          : <span key={i}>{p.text}</span>)))}
    </>
  )
}

export function TypedRefText({ text, world, onOpen, render }: {
  text: string
  world: RefWorld
  onOpen?: (r: ResolvedRef) => void
  /** override the chip entirely (the markdown walk needs plain DOM) */
  render?: (r: ResolvedRef, key: number) => ReactNode
}) {
  const runs = useMemo(() => splitTypedRefs(text, world), [text, world])
  return (
    <>
      {runs.map((p, i) => (p.ref
        ? (render ? render(p.ref, i)
          : <RefChip key={i} r={p.ref} onOpen={onOpen} />)
        : <span key={i}>{p.text}</span>))}
    </>
  )
}
