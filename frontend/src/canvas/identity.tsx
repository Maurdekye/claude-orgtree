// canvas/identity.tsx — ONE way to draw an agent's identity: its model chip
// and its name, and one decision about whether that name navigates.
//
// THE CONTRACT, in three parts:
//
// ⚠ IT RENDERS A FRAGMENT, NOT A WRAPPER. Call sites already have containers
// doing real layout work — `.sender`, `.docket-actor`, `.cc-head-left` where
// `.cc-name` carries `flex: 1 1 12ch` and an ellipsis. A wrapper here would
// collapse each into one flex item and change layout at every site.
//
// ⚠ NO TIER MEANS NO CHIP. `tier` null is a real answer — "the model this
// identity ran under is not known" — and must never be back-filled with
// today's model for a historical generation. `actorFit` (docket.tsx) decides
// that for work-item actors; this component only obeys it.
//
// ⚠ THE OWN-DESK EXEMPTION IS EXPLICIT AND KEYED ON DESTINATION. `atDestination`
// is supplied by the surface, never inferred here from the id.

import { createContext, useContext } from 'react'
import type { MouseEvent as ReactMouseEvent, ReactNode } from 'react'
import { TIER_LETTER, tierLabel } from './shared'

/** The model card: the letter chip every surface uses for a tier.
 *
 *  Nothing renders without a tier — that is the no-invented-identity rule, not
 *  an omission. Moved here from gallery.tsx (user request 2026-09-03, "for
 *  each agent entry, show its model icon card"); gallery re-exports it so
 *  existing importers are untouched. */
export function TierChip({ tier }: { tier?: string | null }) {
  if (!tier) return null
  return (
    <span className={'tier t-' + tier} title={tierLabel(tier)}>
      {TIER_LETTER[tier] ?? tier.slice(0, 1).toUpperCase()}
    </span>
  )
}

export interface AgentNameProps {
  /** the agent id as recorded. Rendered verbatim: never resolved, corrected
   *  or prettified, so a deleted or unknown name still reads as what it was. */
  id: string
  /** the model to attribute, or null/undefined for NO chip. Null is a real
   *  answer — "not recorded" — and must not be filled in from the live tree. */
  tier?: string | null
  /** why this identity is secondary or its chip withheld; becomes the title
   *  of the name element when there is no navigation to describe there. */
  why?: string | null
  /** navigate to this agent. Omit it and the name is plain text.
   *
   *  The activation event is passed through because some surfaces have to tell
   *  a POINTER activation from a KEYBOARD one: `e.detail` is 0 for a click
   *  synthesised by Enter/Space and >= 1 for a real mouse click. pins.tsx
   *  needs that — its name sits on a pointer-capturing drag handle, where the
   *  mouse path is driven by the gesture and only the keyboard may go through
   *  this handler. */
  onFocus?: (id: string, e: ReactMouseEvent<HTMLButtonElement>) => void
  /** ⚠ THIS SURFACE IS THE DESTINATION, so the click would be a no-op — the
   *  agent's own focused desk. NOT "the name matches the agent whose surface
   *  this is": a switchboard panel and a pinned window BOTH show that same
   *  agent's name and both must still navigate, because clicking takes you
   *  somewhere you are not. Keyed on destination, supplied by the surface,
   *  never inferred here from the id. */
  atDestination?: boolean
  /** rendered before the name inside the same element — the '@' of mail
   *  attribution, so the sigil is part of the click target rather than
   *  stranded beside it. */
  prefix?: string
  /** extra classes for the name element, for call sites with their own
   *  truncation rules (docket's `docket-actor-name`). */
  nameClass?: string
}

/**
 * An agent's chip and name, as a fragment: `<TierChip/>` then the name.
 *
 * The name is a `button.cc-name.cc-name-jump` when it navigates and a
 * `span.cc-name` when it does not, which is the markup every existing site
 * already produces — so this is a consolidation, not a restyle.
 */
export function AgentName({
  id, tier, why, onFocus, atDestination, prefix, nameClass,
}: AgentNameProps): ReactNode {
  const label = (prefix ?? '') + id
  const extra = nameClass ? ' ' + nameClass : ''
  if (!onFocus || atDestination) {
    return (
      <>
        <TierChip tier={tier} />
        <span className={'cc-name' + extra} title={why ?? undefined}>{label}</span>
      </>
    )
  }
  return (
    <>
      <TierChip tier={tier} />
      {/* type="button": this is embedded inside forms, where the default
          submit behaviour would be wrong */}
      <button type="button" className={'cc-name cc-name-jump' + extra}
        title={why ?? `focus ${id}'s desk`}
        onClick={(e) => { e.stopPropagation(); onFocus(id, e) }}>
        {label}
      </button>
    </>
  )
}

/** What a DEEP surface knows about the agents it names.
 *
 *  The transcript's mail cards sit under a `memo`'d row inside a windowed
 *  list, several components below the only place that holds the tree — so the
 *  identity facts arrive as context rather than as props threaded through
 *  `Msg`. That is not a style preference: props on a memo'd row are recreated
 *  every render and would defeat the memo the windowing exists to protect.
 *
 *  ⚠ NO PROVIDER MEANS PLAIN TEXT, and that is the correct outcome, not a
 *  degraded one. A surface that cannot answer "is this one of OUR agents"
 *  must not draw a chip or a jump for a name it cannot vouch for. */
export interface AgentDirectory {
  /** The agent, or `undefined` when this id is NOT an agent of the tree on
   *  screen. This establishes EXISTENCE — the tree currently holds a node by
   *  that name — and nothing else. It is half of the eligibility test; the
   *  other half is the namespace (see `mailFromEligible` in desk.tsx). */
  resolve: (id: string) => { tier?: string | null } | undefined
  /** focus that agent's desk. Omit it and resolved names still wear their
   *  chip but do not click — the honest state for a read-only surface. */
  onFocus?: (id: string) => void
  /** ⚠ THE AGENT WHOSE FOCUSED DESK THIS SURFACE **IS** — the one id whose
   *  jump would land you where you already are. `null`/omitted means this
   *  surface is nobody's focused desk (a switchboard panel, a pinned window,
   *  a modal), and there every name navigates, including the surface's own.
   *
   *  Same contract as `AgentName.atDestination`, and for the same reason:
   *  keyed on DESTINATION, supplied by the surface. Comparing a message's
   *  sender against "whose transcript is this" is the mistake — a pinned
   *  window shows that agent's own self-mail and clicking it must still
   *  take you to the desk you are not on. */
  destination?: string | null
}

/** A stable summary of everything `resolve` can answer with: which ids the
 *  directory holds, and the tier of each.
 *
 *  ⚠ THIS EXISTS BECAUSE A REF IS NOT REACTIVE. The directory reads the tree
 *  through refs so its context VALUE can stay stable across the canvas's
 *  frequent re-renders — but a ref write notifies nobody, and the consumers
 *  are `memo`'d rows inside a windowed list that will not re-render on their
 *  own. Memoising the value on this signature is what makes a model change,
 *  an agent's retirement or a new hire actually reach the screen: same facts
 *  → same string → same value → no churn; changed facts → new value → the
 *  consumers re-render.
 *
 *  O(n) in the tree per render of the providing surface, which is far less
 *  than the surface itself costs. Do not "optimise" it into a ref. */
export function agentFactsSig(
  map: Map<string, { tier?: string | null }> | null | undefined,
): string {
  if (!map) return ''
  let s = ''
  // the two separators cannot occur in an agent id, so no two distinct
  // trees can collide by concatenation
  for (const [id, n] of map) s += id + '\u0000' + (n?.tier ?? '') + '\u0001'
  return s
}

const AgentDirectoryCtx = createContext<AgentDirectory | null>(null)

export const AgentDirectoryProvider = AgentDirectoryCtx.Provider

/** null when no surface above supplied one — callers render plain text. */
export function useAgentDirectory(): AgentDirectory | null {
  return useContext(AgentDirectoryCtx)
}
