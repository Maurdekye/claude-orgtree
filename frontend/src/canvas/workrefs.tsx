// canvas/workrefs.tsx — an exact NAME written in ordinary prose, turned into
// something clickable. One matcher, called by every surface that needs one.
//
// GENERIC OVER WHAT A NAME MEANS. It was written for docket item slugs and is
// now also the matcher for agent ids (checklist-evidence's shared agent chip),
// because the hard part is not what the name refers to — it is deciding
// whether a run of characters IS a mention. Two matchers would be two sets of
// boundary rules, and they would drift.
//
// THE CONTRACT, and the two ways it can be broken:
//
//  1. A MENTION IS THE LONGEST KNOWN SLUG AT A POSITION WHOSE BOTH BOUNDARIES
//     ARE FREE. Slugs are kebab-case and routinely contain one another, so a
//     shorter name must never win inside a longer one, inside a longer word,
//     inside a URL path, or inside a dotted identifier. Wrong link beats no
//     link only in the sense that it is worse.
//  2. SPLITTING IS LOSSLESS. Concatenating every run's `text` reproduces the
//     input exactly — whitespace included, since these surfaces render with
//     `white-space: pre-wrap`.

import { Fragment, useMemo } from 'react'
import type { ReactNode } from 'react'
import type { WorkItem } from '../types'
import { AgentName } from './identity'

/** The names that may be matched, and what each one resolves to. A Map is
 *  both halves at once — the key set is what the matcher scans for, and the
 *  value is whatever the caller needs at render time (an item, an agent's
 *  tier, anything).
 *
 *  ⚠ RESOLVING IS THE CALLER'S JOB AND IT MUST BE EXACT. A name absent from
 *  the map is never marked, which is what keeps "same org only" and "no
 *  invented identity" true by construction rather than by a check somebody
 *  can forget. Prose is full of words that look like names and are not. */
export type RefIndex<T> = Map<string, T>

/** What a name in docket prose turns out to be. The panel's writing talks
 *  about two kinds of name — the docket's own items, and this org's agents —
 *  and ONE index holds both kinds, because two indexes would mean scanning the
 *  same sentence twice with two sets of boundary rules and two chances for a
 *  short name to win inside a long one. */
export type MentionRef =
  | { kind: 'item'; slug: string }
  | { kind: 'agent'; id: string }

/** The docket's own index: every item this org served and every agent it
 *  currently has, by name. */
export type MentionIndex = RefIndex<MentionRef>

/** ⚠ ITEMS WIN A COLLISION. An item named exactly like an agent is one name
 *  with two meanings, and this panel is the docket: the reader following it is
 *  after the item. The order of these two loops IS that rule — agents first,
 *  items second, so an item overwrites the agent entry. */
export function buildMentionIndex(items: Iterable<WorkItem>,
                                  agentIds?: Iterable<string>): MentionIndex {
  const out: MentionIndex = new Map()
  // an empty name would compile into the alternation as an empty branch,
  // which matches at every position and spins the scanner forever
  for (const id of agentIds ?? []) if (id) out.set(id, { kind: 'agent', id })
  for (const it of items) if (it?.slug) out.set(it.slug, { kind: 'item', slug: it.slug })
  return out
}

export interface RefPart<T> {
  text: string
  /** present iff this run is a mention — the value the index resolved to */
  ref?: T
}

// ⚠ THE BOUNDARY RULES ARE THE WHOLE FEATURE. A mention is only a mention when
// the characters touching it cannot make it part of something bigger.
//
// BEFORE: anything that could make this the tail of a longer name, a path, a
// URL or a dotted identifier. Note `.` and `/` and `\` and `:` — `host/slug`
// and `a.slug` are not mentions.
const BLOCKS_BEFORE = /[A-Za-z0-9_\-/\\:.@#&?=+~]/
// AFTER: the same set MINUS the full stop, which is handled separately —
// blocking it outright would refuse to link a slug that simply ends a
// sentence, which is where they most often appear.
const BLOCKS_AFTER = /[A-Za-z0-9_\-/\\:@#&?=+~]/

// Backticks, quotes, brackets and parentheses are deliberately NOT blockers:
// the org's own writing convention puts slugs in backticks, so `slug` is the
// single most common way a mention actually appears.

function linkable(text: string, start: number, end: number): boolean {
  const before = start > 0 ? text[start - 1] : ''
  if (before && BLOCKS_BEFORE.test(before)) return false
  const after = end < text.length ? text[end] : ''
  if (after && BLOCKS_AFTER.test(after)) return false
  // "foo-bar." ends a sentence and links; "foo-bar.json" does not
  if (after === '.' && /[A-Za-z0-9]/.test(text[end + 1] ?? '')) return false
  return true
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** Split prose into runs, marking the ones that are mentions of a known name.
 *  Concatenating every `text` back together reproduces the input exactly. */
export function splitRefs<T>(text: string, index: RefIndex<T>): RefPart<T>[] {
  if (!text || index.size === 0) return text ? [{ text }] : []
  // LONGEST FIRST. JavaScript alternation is first-match-wins, not
  // longest-match-wins, so without this ordering `a-b` listed before
  // `a-b-c` would win inside `a-b-c` — and then the boundary check would
  // reject it and the longer name would never be tried at that position.
  const slugs = [...index.keys()]
    .sort((a, b) => b.length - a.length || (a < b ? -1 : 1))
  const re = new RegExp(slugs.map(escapeRe).join('|'), 'g')
  const out: RefPart<T>[] = []
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    const start = m.index
    const end = start + m[0].length
    if (!linkable(text, start, end)) {
      // step ONE character, not past the whole match: a rejected long name
      // may still contain a shorter one that starts further in
      re.lastIndex = start + 1
      continue
    }
    if (start > last) out.push({ text: text.slice(last, start) })
    out.push({ text: m[0], ref: index.get(m[0]) })
    last = end
  }
  if (last < text.length) out.push({ text: text.slice(last) })
  return out
}

/** Prose with its mentions rendered by the caller. With no `render` — or with
 *  nothing in the index — this is the text and nothing else, which is what
 *  every surface falls back to rather than each carrying its own special
 *  case. */
export function RefText<T>({ text, index, render }: {
  text: string
  index: RefIndex<T>
  render?: (ref: T, text: string, key: number) => ReactNode
}) {
  const parts = useMemo(() => splitRefs(text, index), [text, index])
  if (!render) return <>{text}</>
  return (
    <>
      {parts.map((p, i) => (p.ref !== undefined
        ? render(p.ref, p.text, i)
        : <span key={i}>{p.text}</span>))}
    </>
  )
}

/** The docket's rendering of a mention: an underlined name that goes there.
 *
 *  An ITEM mention selects that item; an AGENT mention goes to that agent's
 *  desk, drawn by the shared `AgentName` so a name is one component
 *  everywhere.
 *
 *  ⚠ AN AGENT MENTION WEARS NO MODEL CHIP, and that is an abstention, not an
 *  oversight. Everywhere else in this panel the chip says "this is the model
 *  that did this" — an actor line carries the generation the update was
 *  written under, so the attribution is recorded. Prose carries no generation
 *  at all: a name in a sentence written weeks ago may not be the agent that
 *  now answers to it. Rather than let one glyph mean "who did it" in a row and
 *  "who would answer now" in a paragraph, the mention claims nothing about the
 *  model and only offers the jump — which does go to whoever holds the name
 *  now, and says so in its title. */
export function WorkRefText({ text, index, onPick, onFocusAgent }: {
  text: string
  index: MentionIndex
  onPick?: (name: string) => void
  onFocusAgent?: (id: string) => void
}) {
  const render = (onPick || onFocusAgent)
    ? (ref: MentionRef, shown: string, key: number): ReactNode => {
      if (ref.kind === 'agent') {
        return onFocusAgent
          ? (
            <Fragment key={key}>
              <AgentName id={ref.id} nameClass="docket-ref docket-ref-agent"
                onFocus={(id) => onFocusAgent(id)} />
            </Fragment>
          )
          : <span key={key}>{shown}</span>
      }
      return onPick
        ? (
          <button key={key} type="button" className="docket-ref"
            title={`go to ${shown}`}
            onClick={(e) => { e.stopPropagation(); onPick(ref.slug) }}>
            {shown}
          </button>
        )
        : <span key={key}>{shown}</span>
    }
    : undefined
  return <RefText<MentionRef> text={text} index={index} render={render} />
}

// ---------------------------------------------------------------- typed refs
//
// The canonical token an agent emits. Defined once in `backend/orgtree/refs.py`
// and parsed twice — there, and here. `frontend/tests/ref-tokens.json` is
// generated from the Python and asserted by both sides, so the two cannot
// drift without a red test.
//
// ⚠ EVERY TOKEN CARRIES ITS ORG, and that is not decoration: prose gets copied
// between orgs, and two orgs can hold the same item slug, agent name or mail
// id. A token whose org is not the one on screen must NEVER be resolved
// locally — it is reported as belonging elsewhere.

export type RefKind = 'item' | 'doc' | 'agent' | 'mail'
export type MailBox = 'user' | 'org' | 'node'

export interface TypedRef {
  kind: RefKind
  org: string
  /** item slug · document id · node id · mail id */
  id: string
  /** mail only */
  box?: MailBox
  /** mail in a node's box only */
  node?: string
}

/** one segment — the intersection of every identity alphabet in the product,
 *  which is why `:` and `/` can be delimiters at all */
const SEG = '[a-z0-9-]+'
export const REF_TOKEN_RE =
  new RegExp(`@(item|doc|agent|mail):(?:${SEG})(?:/${SEG})*`, 'g')

/** A token to its parts, or null when it is not one. Never a guess: a
 *  malformed token resolves to nothing rather than to something plausible. */
export function parseRef(token: string): TypedRef | null {
  const m = new RegExp(`^@(item|doc|agent|mail):((?:${SEG})(?:/${SEG})*)$`)
    .exec(String(token ?? ''))
  if (!m) return null
  const kind = m[1] as RefKind
  const seg = (m[2] as string).split('/')
  if (kind !== 'mail') {
    return seg.length === 2
      ? { kind, org: seg[0] as string, id: seg[1] as string } : null
  }
  if (seg.length === 3 && (seg[1] === 'user' || seg[1] === 'org')) {
    return { kind, org: seg[0] as string, box: seg[1] as MailBox,
      id: seg[2] as string }
  }
  if (seg.length === 4 && seg[1] === 'node') {
    return { kind, org: seg[0] as string, box: 'node',
      node: seg[2] as string, id: seg[3] as string }
  }
  return null
}
