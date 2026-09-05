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

import { useMemo } from 'react'
import type { ReactNode } from 'react'
import type { WorkItem } from '../types'

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

/** The docket's own index: every item this org served, by its name. */
export type SlugIndex = RefIndex<string>

export function buildSlugIndex(items: Iterable<WorkItem>): SlugIndex {
  const out: SlugIndex = new Map()
  for (const it of items) {
    // an empty name would compile into the alternation as an empty branch,
    // which matches at every position and spins the scanner forever
    if (it?.slug) out.set(it.slug, it.slug)
  }
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

/** The docket's rendering of a mention: an underlined name that goes there. */
export function WorkRefText({ text, index, onPick }: {
  text: string
  index: SlugIndex
  onPick?: (name: string) => void
}) {
  return (
    <RefText<string> text={text} index={index}
      render={onPick && ((name: string, shown: string, key: number) => (
        <button key={key} type="button" className="docket-ref"
          title={`go to ${shown}`}
          onClick={(e) => { e.stopPropagation(); onPick(name) }}>
          {shown}
        </button>
      ))} />
  )
}
