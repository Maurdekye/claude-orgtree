// canvas/mail.tsx — the mail interfaces: the shared webmail MailList +
// MailFolders, a node's InboxView tab and its modal form (NodeInboxModal),
// the org-inbox viewer (OrgInboxModal), and the org record. One mail
// interface everywhere (user ruling: the user's and the agents' inboxes
// function identically). Extracted verbatim from Canvas.tsx in the phase-3
// split.

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { InboxPayload, OrgEvent, OrgInboxEntry, ToastFn, TreePayload } from '../types'
import {
  audienceAction, fileUrl, getNodeInbox, orgInboxRead, orgInboxSend,
  orgInboxUpload,
} from '../api'
import {
  AttachIcon, CloseIcon, DownloadIcon, EditIcon, FileIcon, HearingIcon,
  MailIcon, PublicIcon,
} from '../icons'
import { EXTERN, md, USER, useEsc, usePolled } from './shared'
import type { CanvasNode, MailRow } from './shared'

// One mail interface, everywhere (user ruling: the user's and the agents'
// inboxes function identically), laid out like a webmail client: the list on
// the left (sender · time · truncated brief — mails have no subjects), the
// selected message opened in the reading pane on the right. Unread mail is
// HIGHLIGHTED but never moved.
export interface MailListProps {
  pending?: MailRow[]
  delivered?: MailRow[]
  waitLabel?: ReactNode
  /** custom head-identity renderer; receives the counterparty id + the mail */
  sender?: (id: string, m: MailRow) => ReactNode
  outgoing?: boolean
  onRead?: (m: MailRow) => void
  onReply?: (m: MailRow, text: string) => void
  onRetract?: (m: MailRow) => void
  jumpTo?: string | null
  fileHref?: (path: string) => string
  /** custom reading-pane body — return non-null to REPLACE the md body AND
   *  the reply UI (asks: the response form IS the body, user ruling
   *  2026-08-04). Null falls through to the normal rendering. */
  renderBody?: (m: MailRow) => ReactNode | null
}

const MAIL_WINDOW = 40

export function MailList({ pending = [], delivered = [], waitLabel, sender, outgoing,
  onRead, onReply, onRetract, jumpTo, fileHref, renderBody }: MailListProps) {
  // ONE order, by send time, always — never grouped, never re-grouped.
  //
  // Unread used to sort as its own block on top, which meant the list
  // reordered itself AS YOU READ IT: every mail you opened jumped out of the
  // top group and down into the body, moving everything around it (user bug
  // 2026-08-03: "that keeps reordering them as i read them which is
  // confusing"). Reading is now a purely visual change — the row highlights
  // and stays exactly where it was.
  //
  // Sorting by SEND time rather than trusting list position stays, and matters
  // independently: the user-mail archive was appended in READ order, so
  // position was click order, not chronology (user bug 2026-08-02). Sorting
  // here also repairs archives already written out of order, which a
  // server-side fix alone cannot. `at` is ISO-8601 Z, so a plain string
  // compare IS a time compare.
  //
  // `pending` and `delivered` still arrive as separate lists because they are
  // different server-side facts (undelivered vs delivered); `_wait` carries
  // that distinction into the row's styling, which is now all it drives.
  const newestFirst = (a: MailRow, b: MailRow) =>
    (a.at ?? '') < (b.at ?? '') ? 1 : (a.at ?? '') > (b.at ?? '') ? -1 : 0
  const all = [
    ...pending.map((m) => ({ ...m, _wait: true })),
    ...delivered,
  ].sort(newestFirst)
  // selection is BY IDENTITY, not index. The list no longer reshuffles on
  // read, but identity is still the right key: the window pages, the filter
  // narrows, and new mail arrives — an index would silently land elsewhere
  const keyOf = (m: MailRow | undefined) =>
    m?.id ?? `${m?.at}|${m?.from}|${(m?.body ?? '').slice(0, 24)}`
  // jumpTo (user spec 2026-07-31): a chat's inline mail link opens the box
  // SELECTED on that mail — identity selection means the reading pane shows
  // it; the scroll + flash happen on the row ref below. Without a jump the
  // box opens with NOTHING selected (user spec 2026-08-05 — the reading
  // pane invites a click instead of auto-opening the newest), and clicking
  // the selected row deselects it again; either way off a viewed unread
  // mail marks it read.
  const [selId, setSelId] = useState<string | null>(jumpTo ?? null)
  const jumpedRef = useRef(false)
  // №26: hunting an hour-old message decayed your unread set click by click —
  // a plain client-side filter over sender+body, no index, no server
  const [q, setQ] = useState('')
  const [draft, setDraft] = useState('')
  // windowed like the transcript: a long-lived org's folders grow without
  // bound and every row is a live DOM node. Newest MAIL_WINDOW render, the
  // rest page in. ⚠ the filter runs over the WHOLE set before the window, so
  // hunting an old message never depends on how far you have paged.
  const [vis, setVis] = useState(MAIL_WINDOW)
  // ONE page per commit. A flick emits a burst of scroll events and React
  // batches them, so every event in the burst reads the same `vis` and every
  // one of them adds a window: measured at eight events rendering a whole
  // 200-row folder in a single gesture, which is exactly the windowing this
  // exists to provide, gone. `vis` growing monotonically stops it oscillating
  // — it never stopped it over-shooting. The latch clears on the commit that
  // renders the page, so a gesture that keeps going keeps paging.
  const paging = useRef(false)
  useEffect(() => { paging.current = false }, [vis])
  const S: (id: string, m: MailRow) => ReactNode =
    sender ?? ((id) => <span>{id === USER ? '@user' : id}</span>)
  const partyOf = (m: MailRow) => (outgoing ? m.to : m.from)
  const qn = q.trim().toLowerCase()
  const shown = qn
    ? all.filter((m) => String(partyOf(m) ?? '').toLowerCase().includes(qn)
      || String(m.body ?? '').toLowerCase().includes(qn))
    : all
  const cur = selId == null ? undefined
    : shown.find((m) => keyOf(m) === selId)
  // per-mail read (user ruling): a VIEWED unread mail is marked read the
  // moment you click OFF it — select another mail, or leave the list
  const curRef = useRef<MailRow | undefined>(undefined); curRef.current = cur
  const readRef = useRef(onRead); readRef.current = onRead
  // ask rows are not real mail — their ids must never reach markRead
  const leave = (m: MailRow | undefined) => {
    if (m?._wait && m.id && !m._ask) readRef.current?.(m)
  }
  useEffect(() => () => leave(curRef.current), [])
  const party = partyOf
  // a custom sender renderer owns the whole head identity (it receives the
  // mail too — the org inbox uses this for "@agent as @org → @recipient")
  const customS = outgoing && sender != null
  const brief = (b: string | null | undefined) => (b ?? '').trim().replace(/\s+/g, ' ').slice(0, 90)
  const when = (at: string | null | undefined) => (at ?? '').slice(5, 16).replace('T', ' ')
  // reply from where you read (№11): only for incoming mail whose sender is a
  // plain agent id — @-sentinels (@user/@system/@ext:/@org:/@mcp:) route
  // elsewhere, and slugify guarantees no agent name starts with '@'
  const replyable = Boolean(onReply && cur && !outgoing && !cur._ask
    && !String(party(cur) ?? '').startsWith('@'))
  const custom = cur ? renderBody?.(cur) ?? null : null
  if (!all.length) return <div className="dim pad">no mail yet</div>
  return (
    <div className="mailer">
      {/* paging is automatic: within a screen of the bottom, the next window
          is already rendered (user ruling 2026-08-04 — reaching the end of a
          list should not then ask you to press something). `vis` only ever
          grows and is guarded against `shown.length`, so it cannot thrash and
          stops once everything is on screen; the `paging` latch keeps ONE
          gesture to ONE window (see above). */}
      <div className="mailer-list"
        onScroll={(e) => {
          const el = e.currentTarget
          if (el.scrollHeight - el.scrollTop - el.clientHeight < 240
            && vis < shown.length && !paging.current) {
            paging.current = true
            setVis((v) => v + MAIL_WINDOW)
          }
        }}>
        {all.length > 4 && (
          <input className="mail-filter" placeholder="filter…" value={q}
            onChange={(e) => setQ(e.target.value)} />
        )}
        {shown.length === 0 && <div className="dim pad">no matches</div>}
        {/* windowed like the transcript: a long-lived org's folders grow
            without bound and every row is a live DOM node. Newest MAIL_WINDOW
            render; the rest page in on demand. The filter searches the WHOLE
            set (`shown`), not just the window — hunting an old message must
            not depend on how far you have paged. */}
        {shown.slice(0, vis).map((m, i) => (
          <div key={keyOf(m)}
            ref={(el) => {
              if (el && jumpTo && keyOf(m) === jumpTo && !jumpedRef.current) {
                jumpedRef.current = true
                el.scrollIntoView({ block: 'center' })
              }
            }}
            className={'mailrow' + (m === cur ? ' on' : '') + (m._wait ? ' unread' : '')
              + (jumpTo && keyOf(m) === jumpTo ? ' jflash' : '')}
            onClick={() => {
              if (cur && keyOf(m) === keyOf(cur)) {
                // toggling the selected row off — reading it counts as read
                leave(cur)
                setSelId(null)
              } else {
                leave(cur)
                setSelId(keyOf(m))
              }
            }}>
            <div className="l1">
              <span className="mfrom">
                {outgoing ? '→ ' : ''}{party(m) === USER ? '@user' : party(m)}
              </span>
              <span className="mtime">{when(m.at)}</span>
              {m._wait && m.id && onRetract && (
                <button className="chip-x" title="retract (undelivered)"
                  onClick={(e) => { e.stopPropagation(); onRetract(m) }}>
                  <CloseIcon fontSize="inherit" /></button>)}
            </div>
            <div className="l2">{brief(m.body)}</div>
          </div>
        ))}
        {shown.length > vis && (
          <div className="dim pad loadolder-status">
            {shown.length - vis} earlier
          </div>)}
      </div>
      <div className="mailer-read">
        {cur && (
          <>
            <div className="mailer-head">
              {outgoing && !customS && <span className="dim">to</span>}
              {S(party(cur)!, cur)}
              <span className="dim">{cur.kind}</span>
              {cur.relationship && <span className="dim">{cur.relationship}</span>}
              <span className="dim">{cur.at}</span>
              {cur._wait && <span className="wait">{waitLabel}</span>}
            </div>
            {custom
              ? <div className="mailer-body">{custom}</div>
              : <div className="mailer-body md" dangerouslySetInnerHTML={md(cur.body)} />}
            {(cur.attachments ?? []).length > 0 && (
              <div className="attach-row">
                {/* extern-shaped attachments may lack `path` — a download
                    link would point at "undefined"; show a plain chip */}
                {cur.attachments!.map((a) => (fileHref && a.path
                  ? <a key={a.path} className="attach-chip" title="download"
                      href={fileHref(a.path)} download={a.name}>
                      <DownloadIcon fontSize="inherit" /> {a.name}
                      <span className="dim"> {a.bytes != null ? `${Math.round(a.bytes / 1024)} KB` : ''}</span></a>
                  : <span key={a.path ?? a.name} className="attach-chip">
                      <FileIcon fontSize="inherit" /> {a.name}</span>))}
              </div>
            )}
            {replyable && (
              <div className="mail-reply">
                <textarea rows={2} value={draft}
                  placeholder={`reply to ${party(cur)}…`}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && draft.trim()) {
                      e.preventDefault()
                      onReply!(cur, draft.trim())
                      setDraft('')
                    }
                  }} />
                <button disabled={!draft.trim()}
                  onClick={() => { onReply!(cur, draft.trim()); setDraft('') }}>
                  reply
                </button>
              </div>
            )}
          </>
        )}
        {!cur && (
          <div className="dim pad mailer-none">
            {shown.length ? 'select a mail to read it' : ''}
          </div>
        )}
      </div>
    </div>
  )
}
// The node's own mailbox (user ruling: its own tab, separate from history),
// with the same folders as the user's: inbox + sent.
interface InboxViewProps {
  slug: string
  nid: string
  /** must RETURN the retract promise (and rethrow on failure) — the optimistic
   *  hide below rolls back on rejection, and a swallowed error would leave the
   *  mail on the server but invisible here until remount */
  onRetract?: (m: MailRow) => Promise<unknown> | void
  jumpTo?: string | null
}

export function InboxView({ slug, nid, onRetract, jumpTo }: InboxViewProps) {
  const [folder, setFolder] = useState('inbox')
  // G5: was a fetch keyed on the `pulse` prop, which meant it refreshed on turn
  // events and on nothing else — and a mail DELIVERY is not a turn event, so
  // the one panel whose whole job is showing mail was the one that did not
  // learn when mail arrived. Polled while mounted instead.
  const box = usePolled(() => getNodeInbox(slug, nid), [slug, nid])
  // the ONE piece of local state left here: mails this user just retracted,
  // held only until the server's copy agrees. That is an uncommitted operation,
  // not a mirror of server data — the distinction the whole refactor turns on.
  const [dropped, setDropped] = useState<string[]>([])
  const pending = (box?.pending ?? []).filter((m) => !dropped.includes(m.id ?? ''))
  useEffect(() => {         // let go as soon as the server has caught up
    if (!box) return
    setDropped((d) => d.filter((id) => box.pending.some((m) => m.id === id)))
  }, [box])
  return (
    <div className="mailwrap">
      <MailFolders folder={folder} setFolder={setFolder}
        unread={pending.length} />
      <div className="mailpane">
        {box == null
          ? <div className="dim pad">loading…</div>
          : folder === 'inbox'
            ? <MailList pending={pending} delivered={box.delivered}
                waitLabel="awaiting next turn" jumpTo={jumpTo}
                fileHref={(p) => fileUrl(slug, nid, p)}
                onRetract={onRetract
                  ? (m) => {
                      const id = m.id ?? ''
                      setDropped((d) => [...d, id])
                      // rollback on failure: the sweep above only RELEASES ids
                      // the server no longer lists, so without this a failed
                      // retract hid the mail here while it stayed on the server
                      Promise.resolve(onRetract(m))
                        .catch(() => setDropped((d) => d.filter((x) => x !== id)))
                    }
                  : undefined} />
            : <MailList delivered={box.sent ?? []} outgoing />}
      </div>
    </div>
  )
}
export interface MailFoldersProps {
  folder: string
  setFolder: (f: string) => void
  unread: number
  folders?: string[]
}

export function MailFolders({ folder, setFolder, unread, folders }: MailFoldersProps) {
  return (
    <div className="mail-folders">
      {(folders ?? ['inbox', 'sent']).map((f) => (
        <button key={f} className={folder === f ? 'on' : ''}
          onClick={() => setFolder(f)}>
          {f}{f === 'inbox' && unread > 0 ? ` ${unread}` : ''}
        </button>
      ))}
    </div>
  )
}
// №10: the org record — every ledger operation (the overseer was the only
// node never told what changed). Renders the events log the server has kept
// all along; the §4.6 cascade warnings ride each row.
export interface OrgRecordProps { events?: OrgEvent[] | null }

export function OrgRecord({ events }: OrgRecordProps) {
  const [q, setQ] = useState('')
  const qn = q.trim().toLowerCase()
  const rows = [...(events ?? [])].reverse().filter((ev) => !qn
    || JSON.stringify(ev).toLowerCase().includes(qn))
  const when = (at: string | null | undefined) => (at ?? '').slice(5, 16).replace('T', ' ')
  const gist = (ev: OrgEvent) => {
    const d = ev.detail || {}
    const bits = [d.node, d.from != null || d.to != null
      ? `${d.from ?? 'top'} → ${d.to ?? 'top'}` : null,
    d.freed != null ? `freed ${d.freed}` : null,
    d.grant != null ? `grant ${d.grant}` : null,
    d.reason, d.predecessor].filter(Boolean)
    return bits.join(' · ')
  }
  if (!rows.length && !qn) return <div className="dim pad">nothing yet</div>
  return (
    <div className="record">
      <input className="mail-filter" placeholder="filter…" value={q}
        onChange={(e) => setQ(e.target.value)} />
      {rows.slice(0, 200).map((ev, i) => (
        <div className="rec-row" key={i}>
          <span className="mtime">{when(ev.at)}</span>
          <b className="rec-kind">{ev.op}</b>
          <span className="rec-actor">{ev.actor === USER ? 'you' : ev.actor}</span>
          <span className="dim">{gist(ev)}</span>
          {(ev.warnings ?? []).map((w, k) => (
            <div className="rec-warn" key={k}>⚠ {w}</div>
          ))}
        </div>
      ))}
    </div>
  )
}
// ✉ on a card — the node's inbox as a modal, the same interface the eye's
// ✉ opens for the user's own inbox.
interface NodeInboxModalProps {
  node: CanvasNode
  slug: string
  close: () => void
  jumpTo?: string | null
}

export function NodeInboxModal({ node, slug, close, jumpTo }: NodeInboxModalProps) {
  useEsc(close)
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide" onClick={(e) => e.stopPropagation()}>
        <h3><MailIcon fontSize="inherit" /> {node.id} <span className="dim">· inbox</span></h3>
        <InboxView slug={slug} nid={node.id} jumpTo={jumpTo} />
        <div className="row">
          <button className="primary" onClick={close}>close</button>
        </div>
      </div>
    </div>
  )
}
// the ORG INBOX viewer (user spec): the org's correspondence with the outside
// world — chatq sessions and other orgs — as one chronological thread. Also
// where the user staffs the "client contact" role: grant/revoke org-inbox
// audiences so chosen sub-agents read and answer outside mail.
interface OrgInboxModalProps {
  inbox: TreePayload['org_inbox'] | undefined
  net?: TreePayload['net']            // F-06: hubs + rosters + status
  map: Map<string, CanvasNode>
  slug: string
  toast: ToastFn
  close: () => void
  jumpTo?: string | null
}

export function OrgInboxModal({ inbox, net, map, slug, toast, close, jumpTo }: OrgInboxModalProps) {
  useEsc(close)
  // reworked (user spec 2026-08-05): no blurb, mailservers on their own TAB,
  // compose in its own modal, holders as bare chips with a drag-to-grant tip
  const [tab, setTab] = useState<'mail' | 'servers'>('mail')
  const [composing, setComposing] = useState(false)
  // a jump to an OUTBOUND mail (an agent's @ext:/@org: send) opens on sent
  const [folder, setFolder] = useState(() =>
    jumpTo && (inbox?.entries ?? []).some((e) => e.id === jumpTo
      && e.dir === 'out') ? 'sent' : 'inbox')
  const holders = inbox?.holders ?? []
  const entries = inbox?.entries ?? []
  const hubsVisible = (net?.hubs ?? []).some((h) => !h.hidden)
  // the org inbox tracks read state as ONE high-water mark over the log — the
  // tail beyond it renders as unread; any read action clears the whole mark
  const readFrom = entries.length - (inbox?.unread ?? 0)
  const rows: MailRow[] = entries.map((e, i) => ({
    id: e.id, at: e.at, body: e.body, from: e.peer, to: e.peer, _by: e.by,
    kind: e.dir === 'in' ? 'message' : 'reply', _wait0: i >= readFrom,
    _state: e.state, _state_at: e.state_at, _tries: e.tries, _err: e.last_err,
    attachments: e.attachments?.map((a) => ({ ...a, path: a.name })),
    relationship: e.dir === 'in'
      ? 'outside party — addressed to the whole org' : undefined,
  }))
  const inn = rows.filter((r) => r.kind === 'message')
  const out = rows.filter((r) => r.kind === 'reply')
  const markRead = () => { if (inbox?.unread) orgInboxRead(slug).catch(() => {}) }
  // F-06: the @net: delivery ladder glyph — ▫ queued · ✓ sent (hub custody)
  // · ✓✓ delivered · ✓✓ read (green). "Delivered, not yet read" is the
  // diagnostic that matters (peer down/busy) — the tooltip says it plainly.
  const glyph = (m: MailRow) => {
    if (!m._state) return null
    // §10: a queued row whose wire tries keep failing says so — the ⚠ and
    // its reason were previously trapped in net_spool where nothing reads
    if (m._state === 'queued' && m._err) {
      return <span className="net-state stuck"
        title={`delivery failing (${m._tries ?? '?'} tries) — ${m._err}. `
          + 'Retries continue; a stale address fails forever — check the '
          + 'recipient on the mailservers tab'}> ⚠</span>
    }
    const g = m._state === 'queued' ? '▫'
      : m._state === 'sent' ? '✓' : '✓✓'
    const tip = m._state === 'queued' ? 'queued — not yet at the hub'
      : m._state === 'sent' ? 'at the hub — the peer has not fetched it yet'
      : m._state === 'delivered'
        ? `delivered ${m._state_at?.slice(5, 16).replace('T', ' ') ?? ''} — no agent has read it yet`
        : 'read — a peer agent\'s turn consumed it'
    return <span className={'net-state' + (m._state === 'read' ? ' read' : '')}
      title={tip}> {g}</span>
  }
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide" onClick={(e) => e.stopPropagation()}>
        <h3><PublicIcon fontSize="inherit" /> The org inbox</h3>
        {hubsVisible && (
          <div className="adv-tabs">
            <button className={'adv-tab' + (tab === 'mail' ? ' on' : '')}
              onClick={() => setTab('mail')}>mail</button>
            <button className={'adv-tab' + (tab === 'servers' ? ' on' : '')}
              onClick={() => setTab('servers')}>mailservers</button>
          </div>
        )}
        {tab === 'mail' ? (<>
          <div className="row" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
            {holders.map((h) => (
              <span key={h} className="badge free"><HearingIcon fontSize="inherit" />{h}
                <button className="chip-x" title="revoke this inbox audience"
                  onClick={() => audienceAction(slug, 'revoke', h, EXTERN)
                    .then(() => toast([`org-inbox audience for ${h} rescinded`]))
                    .catch((e: Error) => toast([`error: ${e.message}`]))}>
                  <CloseIcon fontSize="inherit" /></button>
              </span>
            ))}
            <span className="dim" style={{ fontSize: 11.5 }}>
              {holders.length === 0
                ? 'inbound mail auto-grants the senior top-level agent — or drag an agent onto the mailbox to choose who reads it'
                : 'these agents read and answer outside mail — drag an agent onto the mailbox to add one'}
            </span>
          </div>
          {/* the SAME webmail interface as every other inbox (user ruling) —
              folders + list + reading pane; only the outbound sender
              attribution differs */}
          <div className="mailwrap">
            <MailFolders folder={folder} setFolder={setFolder}
              unread={inn.filter((r) => r._wait0).length} />
            <div className="mailpane">
              {folder === 'inbox'
                ? <MailList pending={inn.filter((r) => r._wait0)}
                    delivered={inn.filter((r) => !r._wait0)}
                    waitLabel="unread" onRead={markRead} jumpTo={jumpTo} />
                : <MailList delivered={out} outgoing jumpTo={jumpTo}
                    sender={(id, m) => (
                      /* outbound attribution (user spec): @agent as @org → @recipient */
                      <span><b>{m?._by ? `@${m._by}` : '@?'}</b>
                        <span className="dim"> as </span><b>@{slug}</b>
                        <span className="dim"> → </span><b>{id}</b>
                        {m && glyph(m)}</span>
                    )} />}
            </div>
          </div>
        </>) : (
          <NetSection net={net} />
        )}
        <div className="row">
          {tab === 'mail' && (
            <button className="primary" onClick={() => setComposing(true)}>
              <EditIcon fontSize="inherit" /> compose mail</button>
          )}
          <span className="spacer" />
          <button onClick={close}>close</button>
        </div>
        {composing && (
          <ComposeModal slug={slug} net={net} entries={entries} toast={toast}
            close={() => setComposing(false)} />
        )}
      </div>
    </div>
  )
}

// ---- F-06: the user composes extern mail from its own MODAL (user spec
// 2026-08-05: the inline bar was cramped; recipients are selectable CHIPS) ----
// recipients come from "the extern list": hub roster peers (@net:) plus every
// past correspondent in the log, deduped — plus a free-typed address (FR-07:
// addressing must never require a live roster; the spool holds @net: mail
// until the hub is reachable). The user bypasses the audience gate
// (they outrank it); attachments stage first and are refused for the
// text-only transports (@ext:/@mcp:) by the server with a clear message.
function ComposeModal({ slug, net, entries, toast, close }: {
  slug: string
  net?: TreePayload['net']
  entries: OrgInboxEntry[]
  toast: ToastFn
  close: () => void
}) {
  // multiple recipients (user spec 2026-08-05): chips TOGGLE into a set and
  // the mail goes to every selected address — one send per recipient, the
  // failures reported per-address
  const [sel, setSel] = useState<string[]>([])
  const [other, setOther] = useState(false) // the free-entry "other" chip
  // FR-07: a free-typed address — offline addressing must never be gated on
  // a live roster; @net:<slug> needs only the slug string, and the spool
  // holds the mail until the hub is back
  const [freeTo, setFreeTo] = useState('')
  const [text, setText] = useState('')
  const [staged, setStaged] = useState<{ id: string; name: string }[]>([])
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement | null>(null)
  // recipients grouped by ORIGIN (user spec 2026-08-05, FR-11 parity): hub
  // peers under their slug's username segment — the same "domain (account)"
  // headers the mailservers tab and the hub's own web UI use — and the
  // log-only correspondents under their transport's namespace
  const groups = (() => {
    const seen = new Set<string>()
    const gs = new Map<string, { addr: string; name: string; kind: string
      online?: boolean; via?: string[] }[]>()
    const put = (g: string, o: { addr: string; name: string; kind: string
      online?: boolean; via?: string[] }) => {
      if (seen.has(o.addr)) return
      seen.add(o.addr)
      gs.set(g, [...(gs.get(g) ?? []), o])
    }
    for (const h of net?.hubs ?? []) {
      if (h.hidden) continue
      for (const r of h.roster) {
        put(r.slug.split('.')[1] ?? h.name ?? '?',
          { addr: `@net:${r.slug}`, name: r.org_name || r.slug.split('.')[0]!,
            kind: r.kind === 'chat' ? 'chat' : 'org', online: !!r.online,
            via: r.transports ?? ['net'] })
      }
    }
    for (const e of entries) {
      // @ext: correspondents are HISTORY only — the bridge is retired
      // (user ruling 2026-08-05); their rows stay readable but they are
      // not addressable, so no chip
      if (!e.peer.startsWith('@') || e.peer.startsWith('@ext:')) continue
      const ns = e.peer.slice(1, e.peer.indexOf(':'))
      const g = e.peer.startsWith('@net:')
        ? e.peer.slice(5).split('.')[1] ?? '?'
        : ns === 'org' ? 'this instance' : `${ns} peers`
      put(g, { addr: e.peer, name: e.peer.replace(/^@\w+:/, ''), kind: ns,
        via: [ns] })
    }
    return [...gs.entries()].sort(([a], [b]) => (a < b ? -1 : 1))
  })()
  const toggle = (addr: string) => setSel((s) =>
    s.includes(addr) ? s.filter((a) => a !== addr) : [...s, addr])
  const dests = [
    ...sel,
    ...(other && freeTo.trim() ? [freeTo.trim()] : []),
  ].filter((d, i, a) => a.indexOf(d) === i)
  const attachable = dests.length > 0
    && dests.every((d) => d.startsWith('@net:') || d.startsWith('@org:'))
  const send = () => {
    if (!dests.length || !text.trim() || busy) return
    setBusy(true)
    Promise.all(dests.map((d) =>
      orgInboxSend(slug, d, text, staged.map((s) => s.id))
        .then((r) => ({ d, error: null as string | null, warnings: r.warnings }))
        .catch((e: Error) => ({ d, error: e.message, warnings: [] as string[] }))))
      .then((rs) => {
        const fails = rs.filter((r) => r.error != null)
        const warns = rs.flatMap((r) => r.warnings)
        if (!fails.length) {
          toast(warns.length ? warns
            : [dests.length > 1
                ? `sent to ${dests.length} recipients — as the org, by you`
                : 'sent — as the org, by you'])
          close()
        } else {
          // partial failure: stay open so the text is not lost; the sent
          // ones are already out, the failed addresses are named
          toast([
            ...fails.map((f) => `→ ${f.d} failed: ${f.error}`),
            ...(rs.length > fails.length
              ? [`${rs.length - fails.length} of ${rs.length} sent`] : []),
          ])
          setBusy(false)
        }
      })
  }
  return (
    <div className="overlay" onClick={close}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings cmp-modal" onClick={(e) => e.stopPropagation()}>
        <h3><EditIcon fontSize="inherit" /> Compose mail
          <span className="dim"> — goes out as the org, sent by you</span></h3>
        <div className="field-label">to
          <span className="dim"> — click to add, click again to remove; the
            mail goes to every selected recipient</span></div>
        {groups.map(([g, os]) => (
          <div key={g}>
            <div className="oi-origin">{g} · {os.length}</div>
            <div className="cmp-chips">
              {os.map((o) => (
                <button key={o.addr} type="button" disabled={busy}
                  className={'cmp-chip' + (sel.includes(o.addr) ? ' on' : '')}
                  title={o.addr + ' — reachable via: '
                    + (o.via ?? [o.kind]).join(', ')}
                  onClick={() => toggle(o.addr)}>
                  <span className={'oi-dot' + (o.online ? ' ok' : '')} />
                  {o.name}
                  {o.kind === 'chat' && <span className="dim">chat</span>}
                  <span className="dim">{(o.via ?? [o.kind]).join('·')}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
        <div className="cmp-chips">
          <button type="button" disabled={busy}
            className={'cmp-chip' + (other ? ' on' : '')}
            onClick={() => setOther((v) => !v)}>other address…</button>
        </div>
        {other && (
          <input autoFocus placeholder="@net:slug / @org:slug / @mcp:id"
            value={freeTo} onChange={(e) => setFreeTo(e.target.value)} />
        )}
        <textarea rows={5} placeholder="the message…" value={text}
          disabled={busy} onChange={(e) => setText(e.target.value)} />
        <div className="row" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <input ref={fileRef} type="file" multiple hidden
            onChange={(e) => {
              for (const f of [...(e.target.files ?? [])].slice(0, 10)) {
                orgInboxUpload(slug, f)
                  .then((r) => setStaged((s) => [...s, { id: r.id, name: r.name }]))
                  .catch((err: Error) => toast([`upload failed: ${err.message}`]))
              }
              e.target.value = ''
            }} />
          {staged.map((s) => (
            <span key={s.id} className="badge free"><FileIcon fontSize="inherit" />{s.name}
              <button className="chip-x" onClick={() =>
                setStaged((st) => st.filter((x) => x.id !== s.id))}>
                <CloseIcon fontSize="inherit" /></button></span>
          ))}
          <button title={attachable ? 'attach files'
            : 'attachments ride @net:/@org: mail only'}
            disabled={!attachable}
            onClick={() => fileRef.current?.click()}>
            <AttachIcon fontSize="inherit" /></button>
          <span className="spacer" />
          <button onClick={close} disabled={busy}>cancel</button>
          <button className="primary"
            disabled={busy || !dests.length || !text.trim()}
            onClick={send}>
            {busy ? 'sending…'
              : dests.length > 1 ? `send to ${dests.length}` : 'send'}</button>
        </div>
      </div>
    </div>
  )
}

// ---- F-06: every mailserver, its status, and all clients on each ----
function NetSection({ net }: { net?: TreePayload['net'] }) {
  return (
    <div className="oi-net">
      {net?.slug && <div className="dim" style={{ paddingBottom: 4 }}>
        this org is <b>{net.slug}</b></div>}
      {(net?.hubs ?? []).filter((h) => !h.hidden).map((h) => (
        <div key={h.id} className="oi-hub">
          <div className="oi-hub-head">
            <span className={'oi-dot' + (h.connected ? ' ok' : '')} />
            <b>{h.name || 'unnamed hub'}</b>
            <span className="dim mono-sm">{h.address}</span>
            <span className="dim">
              {h.connected ? 'connected' : h.enabled
                ? (h.error ? `retrying — ${h.error}` : 'connecting…')
                : 'disabled'}
              {h.queued > 0 ? ` · ${h.queued} queued outbound` : ''}
            </span>
            {(h.stuck ?? 0) > 0 && (
              <span className="oi-stuck" title={h.stuck_err}>
                ⚠ {h.stuck} failing — {h.stuck_err}
              </span>
            )}
          </div>
          {h.roster.length > 0 && (() => {
            // FR-11 parity in-app: group by ORIGIN — the slug's username
            // segment, shared by every org and session registered from one
            // machine profile — so "who is this cluster of clients" reads
            // at a glance, same as the hub's own web UI
            const groups = new Map<string, typeof h.roster>()
            for (const r of h.roster) {
              const origin = r.slug.split('.')[1] ?? '?'
              groups.set(origin, [...(groups.get(origin) ?? []), r])
            }
            return [...groups.entries()].sort().map(([origin, rs]) => (
              <div key={origin}>
                <div className="oi-origin">{origin} · {rs.length}</div>
                <div className="oi-roster">
                  {rs.map((r) => (
                    <span key={r.slug} className="oi-peer"
                      title={(r.blurb || '') + (r.last_seen
                        ? ` · last seen ${r.last_seen.slice(5, 16).replace('T', ' ')}` : '')
                        + ' · reachable via: '
                        + (r.transports ?? ['net']).join(', ')}>
                      <span className={'oi-dot' + (r.online ? ' ok' : '')} />
                      {r.org_name || r.slug.split('.')[0]}
                      {r.kind === 'chat' &&
                        <span className="dim"> (chat)</span>}
                      <span className="dim mono-sm">·{r.slug.split('.').pop()}</span>
                      <span className="dim"> {(r.transports ?? ['net']).join('·')}</span>
                    </span>
                  ))}
                </div>
              </div>
            ))
          })()}
          {h.connected && !h.roster.length &&
            <div className="dim pad-s">no other orgs on this hub yet</div>}
        </div>
      ))}
    </div>
  )
}
