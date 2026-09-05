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

import type { ReactNode } from 'react'
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
  /** navigate to this agent. Omit it and the name is plain text. */
  onFocus?: (id: string) => void
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
        onClick={(e) => { e.stopPropagation(); onFocus(id) }}>
        {label}
      </button>
    </>
  )
}
