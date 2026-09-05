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

import { useMemo } from 'react'
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
    return { ref, token, outcome, label: `mail in ${where}`,
      why: outcome === 'ready' ? `open this mail in ${where}`
        : outcome === 'pending' ? `still finding ${where}`
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
