import type { ReactNode } from 'react'
import type { Event, PublicEvent, Family } from '../generated/events'
import { decodeEventRow, record } from './decode'
import type { EventProfile } from './decode'
import { projectEvent } from './project'
import type { EventField, KnownEvent } from './project'
import { RefMdBody } from '../canvas/refmd'
import { ReceivedMailBody } from '../canvas/mailpreview'
import { RefChip, resolveRef } from '../canvas/reflinks'
import type { RefWorld, ResolvedRef } from '../canvas/reflinks'
import type { TypedRef } from '../canvas/workrefs'
import { md } from '../canvas/shared'

const FAMILY_MARK: Record<Family, string> = {
  ordinary: 'Message', linked_reply: 'Reply', assignment: 'Assignment', review: 'Review',
  status: 'Status', answer_decision: 'Answer / decision', access_resources: 'Access / resources',
  lifecycle: 'Agent lifecycle', monitor: 'Monitor', runtime_recovery: 'Runtime',
  reminder: 'Reminder', context_change: 'Context / change',
}
const FAMILY_ICON: Record<Family, string> = {
  ordinary: '·', linked_reply: '↩', assignment: '→', review: '✓', status: '●',
  answer_decision: '?', access_resources: '⚿', lifecycle: '◇', monitor: '◉',
  runtime_recovery: '!', reminder: '◷', context_change: '≡',
}
interface ContentProps { world?: RefWorld | null; onOpen?: (r: ResolvedRef) => void; imgBase?: string }
function Text({ text, world, onOpen, imgBase }: ContentProps & { text: string }) {
  return <RefMdBody className="event-prose md" html={md(text, imgBase)} world={world} onOpen={onOpen} />
}

/** Values come only from explicitly placed fields of a validated leaf. */
function Value({ value, ...props }: ContentProps & { value: unknown }): ReactNode {
  if (value === null) return <span className="dim">Not recorded</span>
  if (typeof value === 'string') return <Text text={value} {...props} />
  if (typeof value === 'number') return <span>{value}</span>
  if (typeof value === 'boolean') return <span>{value ? 'Yes' : 'No'}</span>
  if (Array.isArray(value)) return value.length
    ? <ul className="event-values">{value.map((v, i) => <li key={i}><Value value={v} {...props} /></li>)}</ul>
    : <span className="dim">None</span>
  if (record(value)) return <dl className="event-record">{Object.entries(value).map(([key, v]) =>
    <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd><Value value={v} {...props} /></dd></div>)}</dl>
  return <span className="dim">Unavailable</span>
}
function Fields({ fields, ...props }: ContentProps & { fields: EventField[] }) {
  return <>{fields.map(f => <div className="event-field" key={f.key} data-event-field={f.key}>
    {fields.length > 1 && <span className="event-field-label">{f.label}</span>}
    <Value value={f.value} {...props} />
  </div>)}</>
}
export function eventReference(event: KnownEvent, enclosingOrg: string): TypedRef | null {
  const object = event.object
  if (!object) return null
  const org = 'org' in object ? object.org : enclosingOrg
  switch (object.kind) {
    case 'work_item': return { kind: 'item', org, id: object.slug }
    case 'document': return { kind: 'doc', org, id: object.id }
    case 'node': return { kind: 'agent', org, id: object.id }
    case 'mail': return { kind: 'mail', org, id: object.id, box: object.box, ...(object.node ? { node: object.node } : {}) }
    case 'ask': case 'batch': case 'credit_request': case 'audience_request': case 'scope_request':
    case 'watchdog': case 'task': case 'build': case 'org': case 'session': return null
  }
}
function ObjectLabel({ event, org, world, onOpen }: { event: Event | PublicEvent; org: string } & ContentProps) {
  const object = event.object
  if (!object) return null
  const ref = eventReference(event, org)
  if (ref) {
    const resolved = resolveRef(ref, world ?? { org, handles: new Set() })
    // Keep the recorded title when no current authoritative index can name it.
    if ('title' in object && resolved.outcome === 'ready') resolved.label = object.title
    return <RefChip r={resolved} onOpen={onOpen} />
  }
  return <span className="event-object">{'name' in object ? object.name
    : 'description' in object ? object.description : 'short' in object ? object.short
      : 'id' in object ? object.id : 'node' in object ? object.node : org}</span>
}
export interface EventCardProps extends ContentProps {
  row: unknown; profile: EventProfile; org: string; preview?: boolean
  actor?: (id: string) => ReactNode
}
/** A single presentation path for mailbox, pending/live and settled transcript rows.
 *  Envelope time, delivery badges, attachments and existing actions stay with callers. */
export function EventCard({ row, profile, org, preview = false, actor, ...content }: EventCardProps) {
  const decoded = decodeEventRow(row, profile)
  if (decoded.kind !== 'known') return <div className="event-fallback">
    {decoded.kind === 'unsupported' && <span className="event-unsupported">Unsupported message format</span>}
    <Text text={decoded.fallback} {...content} />
  </div>
  const view = projectEvent(decoded.event)
  const event = decoded.event
  const body = view.fields.filter(f => f.placement === 'body')
  const header = view.fields.filter(f => f.placement === 'header')
  const context = view.fields.filter(f => f.placement === 'context')
  const bodyContent = <div className="event-body"><Fields fields={body} {...content} /></div>
  return <section className={'event-card event-' + view.family} data-event-variant={event.variant}>
    <header className="event-head">
      <span className="event-family" aria-label={FAMILY_MARK[view.family]} title={FAMILY_MARK[view.family]}>{FAMILY_ICON[view.family]}</span>
      <strong>{view.title}</strong>
      <span className="event-actor" data-actor-kind={event.actor.kind}>
        {event.actor.kind === 'agent' && actor ? actor(event.actor.id)
          : event.actor.kind === 'system' ? 'System' : event.actor.kind === 'user' ? 'User' : event.actor.id}
      </span>
      <ObjectLabel event={event} org={org} world={content.world} onOpen={content.onOpen} />
      {header.map(f => <div className="event-head-field" key={f.key} data-event-field={f.key}>
        <span className="dim">{f.label}: </span><Value value={f.value} {...content} />
      </div>)}
    </header>
    {body.length > 0 && (preview
      ? <ReceivedMailBody>{bodyContent}</ReceivedMailBody> : bodyContent)}
    {context.length > 0 && <details className="event-context">
      <summary>Context</summary><Fields fields={context} {...content} />
    </details>}
  </section>
}
