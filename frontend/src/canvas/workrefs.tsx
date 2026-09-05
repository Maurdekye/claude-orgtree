// canvas/workrefs.tsx — turning a docket item's SLUG, written in ordinary
// prose, into a link that goes to that item.
//
// Agents are told to refer to work by its readable name (`git-review-workspace`,
// not `w3becbb30`), so those names are already all over descriptions, progress
// entries and status text. Until now every one of them was a lookup the reader
// had to do by hand.
//
// WHY THIS IS ONE MODULE AND NOT A REGEX AT EACH CALLSITE. Two things here are
// easy to get subtly wrong, and getting either wrong is worse than not linking
// at all:
//
//  1. WHAT COUNTS AS A MENTION. A slug is kebab-case, so one slug is very
//     often a substring of another — `working-status-nudges` sits inside
//     `working-status-nudges-every-twenty-minutes`. Link the short one and the
//     reader is sent to the wrong item by a link that looks right. The same
//     shape appears inside URLs (`.../clickable-docket-references`) and inside
//     dotted or slashed identifiers.
//  2. WHAT IS NOT TEXT ANY MORE. Everything not matched must come back out
//     byte-for-byte, including the whitespace these surfaces render with
//     `white-space: pre-wrap`. A linkifier that trims, collapses or reorders
//     ordinary prose has broken the thing it was decorating.
//
// So the matcher is a pure function with its own tests, and every surface
// calls it rather than approximating it.

import { useMemo } from 'react'
import type { WorkItem } from '../types'

/** slug → item id. Items whose slug is null (written before slugs existed)
 *  are simply absent, which is what makes them degrade to plain text rather
 *  than to a link that would 404 against a name the server does not know. */
export type SlugIndex = Map<string, string>

export function buildSlugIndex(items: Iterable<WorkItem>): SlugIndex {
  const out: SlugIndex = new Map()
  for (const it of items) {
    // `?? null` is not enough: an empty string would compile into the
    // alternation as an empty branch, which matches at every position and
    // spins the scanner forever
    if (it?.slug && it.id) out.set(it.slug, it.id)
  }
  return out
}

export interface RefPart {
  text: string
  /** present iff this run is a mention that should be a link */
  id?: string
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

/** Split prose into runs, marking the ones that are mentions of a known item.
 *  Concatenating every `text` back together reproduces the input exactly. */
export function splitSlugRefs(text: string, index: SlugIndex): RefPart[] {
  if (!text || index.size === 0) return text ? [{ text }] : []
  // LONGEST FIRST. JavaScript alternation is first-match-wins, not
  // longest-match-wins, so without this ordering `a-b` listed before
  // `a-b-c` would win inside `a-b-c` — and then the boundary check would
  // reject it and the longer name would never be tried at that position.
  const slugs = [...index.keys()]
    .sort((a, b) => b.length - a.length || (a < b ? -1 : 1))
  const re = new RegExp(slugs.map(escapeRe).join('|'), 'g')
  const out: RefPart[] = []
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
    out.push({ text: m[0], id: index.get(m[0]) })
    last = end
  }
  if (last < text.length) out.push({ text: text.slice(last) })
  return out
}

/** Prose with its mentions turned into links. With no `onPick` — or with
 *  nothing in the index — this renders the text and nothing else, which is
 *  the behaviour every surface falls back to rather than a special case. */
export function WorkRefText({ text, index, onPick }: {
  text: string
  index: SlugIndex
  onPick?: (id: string) => void
}) {
  const parts = useMemo(() => splitSlugRefs(text, index), [text, index])
  if (!onPick) return <>{text}</>
  return (
    <>
      {parts.map((p, i) => (p.id
        ? <button key={i} type="button" className="docket-ref"
            title={`go to ${p.text}`}
            onClick={(e) => { e.stopPropagation(); onPick(p.id as string) }}>
            {p.text}
          </button>
        : <span key={i}>{p.text}</span>))}
    </>
  )
}
