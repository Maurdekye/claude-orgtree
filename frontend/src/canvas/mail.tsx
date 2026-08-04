// canvas/mail.tsx — the mail interfaces: the shared webmail MailList +
// MailFolders, a node's InboxView tab and its modal form (NodeInboxModal),
// the org-inbox viewer (OrgInboxModal), and the org record. One mail
// interface everywhere (user ruling: the user's and the agents' inboxes
// function identically). Extracted verbatim from Canvas.tsx in the phase-3
// split.

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { InboxPayload, OrgEvent, ToastFn, TreePayload } from '../types'
import { audienceAction, fileUrl, getNodeInbox, orgInboxRead } from '../api'
import {
  CloseIcon, DownloadIcon, FileIcon, HearingIcon, MailIcon, PublicIcon,
} from '../icons'
import { DRAFT, EXTERN, md, USER, useEsc, usePolled } from './shared'
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
}

const MAIL_WINDOW = 40

export function MailList({ pending = [], delivered = [], waitLabel, sender, outgoing,
  onRead, onReply, onRetract, jumpTo, fileHref }: MailListProps) {
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
  // it; the scroll + flash happen on the row ref below. A retracted or
  // expired id falls back to the newest mail, never an error.
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
  const S: (id: string, m: MailRow) => ReactNode =
    sender ?? ((id) => <span>{id === USER ? '@user' : id}</span>)
  const partyOf = (m: MailRow) => (outgoing ? m.to : m.from)
  const qn = q.trim().toLowerCase()
  const shown = qn
    ? all.filter((m) => String(partyOf(m) ?? '').toLowerCase().includes(qn)
      || String(m.body ?? '').toLowerCase().includes(qn))
    : all
  const cur = shown.find((m) => keyOf(m) === selId) ?? shown[0]
  // per-mail read (user ruling): a VIEWED unread mail is marked read the
  // moment you click OFF it — select another mail, or leave the list
  const curRef = useRef<MailRow | undefined>(undefined); curRef.current = cur
  const readRef = useRef(onRead); readRef.current = onRead
  const leave = (m: MailRow | undefined) => { if (m?._wait && m.id) readRef.current?.(m) }
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
  const replyable = Boolean(onReply && cur && !outgoing
    && !String(party(cur) ?? '').startsWith('@'))
  if (!all.length) return <div className="dim pad">no mail yet</div>
  return (
    <div className="mailer">
      {/* paging is automatic: within a screen of the bottom, the next window
          is already rendered (user ruling 2026-08-04 — reaching the end of a
          list should not then ask you to press something). `vis` only ever
          grows, so this cannot thrash; the `shown.length` guard stops it
          climbing past the set and re-rendering on every scroll event once
          everything is on screen. */}
      <div className="mailer-list"
        onScroll={(e) => {
          const el = e.currentTarget
          if (el.scrollHeight - el.scrollTop - el.clientHeight < 240
            && vis < shown.length) setVis((v) => v + MAIL_WINDOW)
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
              if (keyOf(m) !== keyOf(cur)) leave(cur)
              setSelId(keyOf(m))
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
            <div className="mailer-body md" dangerouslySetInnerHTML={md(cur.body)} />
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
      </div>
    </div>
  )
}
// The node's own mailbox (user ruling: its own tab, separate from history),
// with the same folders as the user's: inbox + sent.
interface InboxViewProps {
  slug: string
  nid: string
  onRetract?: (m: MailRow) => void
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
                  ? (m) => { onRetract(m); setDropped((d) => [...d, m.id ?? '']) }
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
  map: Map<string, CanvasNode>
  slug: string
  toast: ToastFn
  close: () => void
  jumpTo?: string | null
}

export function OrgInboxModal({ inbox, map, slug, toast, close, jumpTo }: OrgInboxModalProps) {
  useEsc(close)
  const [grantee, setGrantee] = useState('')
  // a jump to an OUTBOUND mail (an agent's @ext:/@org: send) opens on sent
  const [folder, setFolder] = useState(() =>
    jumpTo && (inbox?.entries ?? []).some((e) => e.id === jumpTo
      && e.dir === 'out') ? 'sent' : 'inbox')
  const holders = inbox?.holders ?? []
  const candidates = [...map.values()].filter((n) =>
    n.id !== USER && n.id !== DRAFT && n.state === 'live' && !n.isBearerOf
    && n.parent !== USER && !holders.includes(n.id))
  const entries = inbox?.entries ?? []
  // the org inbox tracks read state as ONE high-water mark over the log — the
  // tail beyond it renders as unread; any read action clears the whole mark
  const readFrom = entries.length - (inbox?.unread ?? 0)
  const rows: MailRow[] = entries.map((e, i) => ({
    id: e.id, at: e.at, body: e.body, from: e.peer, to: e.peer, _by: e.by,
    kind: e.dir === 'in' ? 'message' : 'reply', _wait0: i >= readFrom,
    relationship: e.dir === 'in'
      ? 'outside party — addressed to the whole org' : undefined,
  }))
  const inn = rows.filter((r) => r.kind === 'message')
  const out = rows.filter((r) => r.kind === 'reply')
  const markRead = () => { if (inbox?.unread) orgInboxRead(slug).catch(() => {}) }
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings wide" onClick={(e) => e.stopPropagation()}>
        <h3><PublicIcon fontSize="inherit" /> The org inbox</h3>
        <div className="hint">
          Outside parties — external Claude Code sessions (chatq) and other
          organizations — see this org as a single recipient. Their mail lands
          here; every top-level agent (and every audience holder below) gets a
          copy, coordinates internally, and one of them replies for the org.
        </div>
        <div className="row" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="field-label">audience holders:</span>
          {holders.length === 0 && <span className="dim">none — top-level agents only</span>}
          {holders.map((h) => (
            <span key={h} className="badge free"><HearingIcon fontSize="inherit" />{h}
              <button className="chip-x" title="revoke this inbox audience"
                onClick={() => audienceAction(slug, 'revoke', h, EXTERN)
                  .then(() => toast([`org-inbox audience for ${h} rescinded`]))
                  .catch((e: Error) => toast([`error: ${e.message}`]))}>
                <CloseIcon fontSize="inherit" /></button>
            </span>
          ))}
          {candidates.length > 0 && <>
            <select value={grantee} onChange={(e) => setGrantee(e.target.value)}>
              <option value="">grant to…</option>
              {candidates.map((n) => <option key={n.id} value={n.id}>{n.id}</option>)}
            </select>
            <button disabled={!grantee}
              onClick={() => audienceAction(slug, 'grant', grantee, 'extern')
                .then(() => { toast([`${grantee} now reads and answers the org inbox`]); setGrantee('') })
                .catch((e: Error) => toast([`error: ${e.message}`]))}>grant</button>
          </>}
        </div>
        {/* the SAME webmail interface as every other inbox (user ruling) —
            folders + list + reading pane; only the deviations above (audience
            granting) and the outbound sender attribution differ */}
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
                      <span className="dim"> → </span><b>{id}</b></span>
                  )} />}
          </div>
        </div>
        <div className="row"><span className="spacer" />
          <button onClick={close}>close</button></div>
      </div>
    </div>
  )
}
