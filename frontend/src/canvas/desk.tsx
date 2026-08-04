// canvas/desk.tsx — the desk: DeskChat (the zoomed-in per-agent chat window,
// styled as a miniature Claude Code session) with its transcript renderers
// (Msg, ToolChip, ThoughtLine, SysLine), the composer's effort controls and
// slash hints, the history/files tabs, the lineage panel, and the small
// ContextWheel/Activity indicators shared with the cards. Extracted verbatim
// from Canvas.tsx in the phase-3 split.

import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type {
  ChatMessage, ChatPayload, HistoryItem, ScratchPayload,
  ToolChip as ToolChipData, ToastFn,
} from '../types'
import {
  audienceAction, BASE, compactNode, fileUrl, getChat, getHistory,
  getScratch, interruptNode, retractMail, saveScope, sendMessage, uploadFile,
} from '../api'
import {
  ArrowDownIcon, ArrowUpIcon, AutorenewIcon, CloseIcon, DotIcon, DownloadIcon,
  EditIcon, EyeIcon, FileIcon, FolderIcon, FrozenIcon, HearingIcon, LayersIcon,
  LockIcon, MailIcon, PlayIcon, PsychologyIcon, SettingsIcon, SparkIcon,
  StopIcon, WarnIcon,
} from '../icons'
import { ago, EXTERN, md, TIER_LETTER, TIER_SEAT, USER, useEsc, usePolled } from './shared'
import {
  addPending, CHAT_WINDOW, dropPending, loadOlder as storeLoadOlder, markBusy,
  MAX_WINDOW, refreshConvo, useConvo,
} from '../convo'
import type {
  ActivityInfo, CanvasNode, LiveRow, MailLinkFn, OpFn,
} from './shared'
import { ConfirmModal } from './modals'
import { InboxView } from './mail'

interface ContextWheelProps {
  occ?: number | null
  cw?: number | null
  onCompact?: () => void
  compactAt?: number
}

export function ContextWheel({ occ, cw, onCompact, compactAt }: ContextWheelProps) {
  if (!occ || !cw) return null
  const frac = Math.min(1, occ / cw)
  // №19: the red ring means "about to split" — the ORG'S configured
  // threshold, not a literal 0.8 (an org set to 50% got a ring that turned
  // red 30 points after its agents had already forked)
  const hot = frac >= (compactAt || 0.8)
  const R = 5.5, C = 2 * Math.PI * R
  const svg = (
    <svg className="ctxwheel" viewBox="0 0 16 16" width="15" height="15">
      <title>{`context: ${Math.round(occ / 1000)}k / ${Math.round(cw / 1000)}k (${Math.round(frac * 100)}%)`
        + ` — auto-compacts at ${Math.round((compactAt || 0.8) * 100)}%`
        + (onCompact ? ' — click to compact now' : '')}</title>
      <circle cx="8" cy="8" r={R} className="track" />
      <circle cx="8" cy="8" r={R} className={'fill' + (hot ? ' hot' : '')}
        strokeDasharray={`${C * frac} ${C}`} transform="rotate(-90 8 8)" />
    </svg>
  )
  // clickable ONLY where a handler is wired — the zoomed desk (user ruling);
  // the zoomed-out card wheel stays a passive indicator
  if (!onCompact) return svg
  return <button className="ctxbtn" onClick={onCompact}>{svg}</button>
}

const shortTool = (t: string | null | undefined) => (t || 'tool').replace(/^mcp__([^_]+)__/, '$1: ')

const fmtBytes = (n: number | null | undefined) => (n == null ? '0 B'
  : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB`
  : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`)

export function Activity({ act, dotOnly }: { act?: ActivityInfo; dotOnly?: boolean }) {
  const phase = act?.phase ?? 'thinking'
  if (dotOnly) {
    return phase === 'tool'
      ? <span className="actgear" title={`running ${shortTool(act?.tool)}`}><SettingsIcon fontSize="inherit" /></span>
      : <span className="busydot" title={phase} />
  }
  return (
    <div className="actlabel">
      {phase === 'tool'
        ? <><span className="actgear"><SettingsIcon fontSize="inherit" /></span> {shortTool(act?.tool)}</>
        : phase === 'writing' ? <><EditIcon fontSize="inherit" /> writing</> : <><AutorenewIcon fontSize="inherit" className="cc-spin" /> thinking</>}
      <span className="actdots" />
    </div>
  )
}
// The desk is styled as a miniature Claude Code chat window (design ruling):
// compact one-line chrome, plain assistant text, boxed user turns, ⏺ tool
// lines, and a bordered composer with the model name in its footer row.
// №21: memoized — the spring engine re-renders the whole canvas every
// animation frame, and each open desk re-parsed its full transcript each
// time. The comparator checks the DATA props only; the callback props close
// over stable setters, so their per-render identities are ignorable.
export const DeskChat = memo(DeskChatInner, (p, n) =>
  p.node === n.node && p.map === n.map && p.slug === n.slug
  && p.pub === n.pub && p.bare === n.bare && p.compact === n.compact
  && p.compactAt === n.compactAt)

interface DeskChatProps {
  node: CanvasNode
  map: Map<string, CanvasNode>
  op: OpFn
  slug: string
  toast: ToastFn
  onLineage?: () => void
  onConfig?: () => void
  onRecenter?: () => void
  /** camera move to a related agent (F-01 nav chips) — the same glide as
   *  clicking its card. USER as the id targets the eye/switchboard. */
  onJump?: (id: string) => void
  pub: boolean
  bare?: boolean
  compact?: boolean
  compactAt?: number
  onMailLink?: MailLinkFn
}

/** F-01: one small clickable card pointing at a related agent — superior at
 *  the top of the desk, one per direct report at the bottom. Carries live
 *  state (busy spinner, unread mail count) because the data is already in
 *  `map`; an inert chip would be a lie of omission next to a busy agent. */
function NavChip({ n, dir, onJump }:
{ n: CanvasNode; dir: 'up' | 'down'; onJump: (id: string) => void }) {
  const eye = n.id === USER
  return (
    <button className={'desk-nav-chip' + (!eye && n.state !== 'live' ? ' dim' : '')}
      title={eye ? 'jump to the switchboard'
        : `jump to ${n.id}${n.state !== 'live' ? ` (${n.state})` : ''}`}
      onClick={() => onJump(n.id)}>
      {dir === 'up' ? <ArrowUpIcon fontSize="inherit" /> : <ArrowDownIcon fontSize="inherit" />}
      {eye
        ? <><EyeIcon fontSize="inherit" /> switchboard</>
        : <><span className={'tier t-' + n.tier}>{TIER_LETTER[n.tier!] ?? '?'}</span>
            {n.id}</>}
      {n.busy && <AutorenewIcon fontSize="inherit" className="cc-spin" />}
      {(n.mail_pending ?? 0) > 0 && <b className="eye-count">{n.mail_pending}</b>}
    </button>
  )
}

// how long the send receipt (§№11) stays up — long enough to read a routing
// word you were not expecting, short enough that it never describes a message
// that has already been answered
const SENDMODE_MS = 6000

function DeskChatInner({ node, map, op, slug, toast, onLineage, onConfig,
  onRecenter, onJump, pub, bare = false, compact = false, compactAt,
  onMailLink }: DeskChatProps) {
  // THE CONVERSATION IS NOT THIS COMPONENT'S. It lives in one per-node store
  // (convo.ts) that every view of this node subscribes to, because a node can
  // be on screen twice — its card and its switchboard panel — and two private
  // copies of one conversation diverge by construction (user bug 2026-08-02:
  // "the switchboard desk going out of sync with the individual agent desks").
  // What stays local below is only what is genuinely per-VIEW: this desk's
  // scroll position, its open tab, its composer draft.
  const convo = useConvo(slug, node.id)
  const { chat, live_feed, draft, thinking, thinkSecs, pending } = {
    chat: convo.chat, live_feed: convo.live, draft: convo.draft,
    thinking: convo.thinking, thinkSecs: convo.thinkSecs, pending: convo.pending }
  // №2: the draft survives the camera — persisted per node on every keystroke
  // (clicking a sibling card unmounts this whole component)
  const draftKey = `orgtree-draft-${slug}-${node.id}`
  const [text, setTextRaw] = useState(() => {
    try { return localStorage.getItem(draftKey) || '' } catch { return '' }
  })
  const setText = useCallback((v: string | ((prev: string) => string)) => setTextRaw((prev) => {
    const next = typeof v === 'function' ? v(prev) : v
    try {
      if (next) localStorage.setItem(draftKey, next)
      else localStorage.removeItem(draftKey)
    } catch { /* private mode */ }
    return next
  }), [draftKey])
  // №11: which door the last send went through. It is a RECEIPT, not a state —
  // it answers "where did that message just go", and that answer goes stale the
  // moment the queue drains. It had no clear at all (user bug 2026-08-02: the
  // "delivering" line sat under the composer forever), so it expires. Anything
  // durable has its own surface: the per-message "delivering mid-task…" tag,
  // the frozen badge, the mail count.
  const [sendMode, setSendMode] = useState('')
  const modeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const flashMode = useCallback((m: string) => {
    setSendMode(m)
    if (modeTimer.current) clearTimeout(modeTimer.current)
    modeTimer.current = m ? setTimeout(() => setSendMode(''), SENDMODE_MS) : null
  }, [])
  useEffect(() => () => { if (modeTimer.current) clearTimeout(modeTimer.current) }, [])
  const [asking, setAsking] = useState(false)
  const [askCompact, setAskCompact] = useState(false)
  const [view, setView] = useState<'chat' | 'history' | 'files' | 'inbox'>('chat')     // chat | history | files | inbox
  // №7's denials banner and its dismissal state are gone (user bug
  // 2026-08-02): a denial already renders inline as an errored ToolChip where
  // it happened, so the banner was a duplicate that also sorted a past event
  // below undelivered mail. Nothing needs dismissing that lives in sequence.
  // (live_feed / draft / thinking / thinkSecs / pending all come from the
  // store above — they were seven local cells and a pair of refs here, which
  // is exactly how two views of one node ended up with two different answers.)
  const scroller = useRef<HTMLDivElement | null>(null)
  const loadedRef = useRef(false)     // first load always lands at the bottom
  const live = node.state === 'live'
  // sticky-bottom, in one place. `stuck` is maintained by the SCROLL EVENT
  // rather than recomputed at each update: growing content does not move
  // scrollTop, so a reader sitting at the bottom stays "stuck" and a reader who
  // scrolled up stays free until they come back down. 40px of slack keeps it
  // from unsticking on a stray pixel.
  const stickRef = useRef(true)
  const [showJump, setShowJump] = useState(false)
  const nearBottom = () => {
    const el = scroller.current
    return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }
  const setStuck = (v: boolean) => {
    stickRef.current = v
    setShowJump((s) => (s === !v ? s : !v))   // only re-render on a real flip
  }
  const pin = () => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }
  // AFTER the DOM commit, before paint: a bare requestAnimationFrame scheduled
  // during an event handler can fire BEFORE React commits the new rows, so it
  // read the OLD scrollHeight and landed short — that was the "gets left
  // behind" bug. A layout effect measures post-commit, so it cannot miss.
  // Windowed transcript: only the newest CHAT_WINDOW rows are fetched and
  // rendered; scrolling to the top loads another page. A long-lived agent's
  // transcript is unbounded, and the cost that actually bites is DOM size —
  // every row carries markdown and tool chips. The server stamps `seq` as the
  // PRE-slice ordinal, so messages[0].seq > 0 means older rows exist.
  const loadingOlder = convo.loadingOlder
  // distance-from-bottom is invariant when older rows are PREPENDED, so it is
  // the anchor that keeps the reader's place instead of jumping them down
  const growAnchor = useRef<number | null>(null)
  useLayoutEffect(() => {
    const el = scroller.current
    if (stickRef.current) { pin(); return }
    if (el && growAnchor.current != null) {
      el.scrollTop = el.scrollHeight - growAnchor.current
      growAnchor.current = null
    }
  })
  // seq is the PRE-slice ordinal, so a non-zero first seq means older rows exist
  const hasOlder = (chat?.messages[0]?.seq ?? 0) > 0
  const toBottom = () => { setStuck(true); pin() }
  const loadOlder = () => {
    const el = scroller.current
    if (!el) return
    growAnchor.current = el.scrollHeight - el.scrollTop
    if (!storeLoadOlder(slug, node.id)) growAnchor.current = null
  }

  // Ingestion of stream/pulse events, the transcript fetch, the live/durable
  // reconciliation and the busy poller ALL moved into convo.ts. They used to
  // live here, which meant every mounted view ran its own copy of each — the
  // divergence the store exists to make impossible. What is left is this
  // view's business: ask for a first load, and re-stick the scroll when the
  // store hands back a payload this view has not seen yet.
  const refresh = useCallback((force = false) => {
    if (force) setStuck(true)
    return refreshConvo(slug, node.id, { force })
  }, [slug, node.id])
  useEffect(() => {
    if (!convo.loaded) void refreshConvo(slug, node.id)
  }, [slug, node.id, convo.loaded])
  useEffect(() => {
    // the FIRST payload this view sees lands it at the bottom, whether the
    // store fetched it for us or another view had already loaded it
    if (convo.loaded && !loadedRef.current) { loadedRef.current = true; setStuck(true) }
  }, [convo.loaded])   // eslint-disable-line react-hooks/exhaustive-deps
  // (the busy-gated poller that lived here is gone. Liveness is now driven by
  // SUBSCRIPTION inside convo.ts: if a view is watching a node, that node is
  // polled. Gating it on chat.busy meant the refresh loop depended on a field
  // that arrives in the payload the loop fetches — so a view that started out
  // believing "not busy" could never learn otherwise. See convo.beat().)

  // an archived agent still RECEIVES mail (user ruling) — it queues in its
  // inbox and gets acted on at rehire; only unrecoverable nodes refuse
  const canMail = live || node.state === 'archived'
  const send = () => {
    let t = text.trim()
    if ((!t && !attached.length) || !canMail) return
    if (!t) t = '(file attached)'
    const paths = attached.map((a) => a.path)
    setText('')
    setAttached([])
    // optimistic ghost only until the server confirms — the durable copy
    // then renders from chat.pending_mail (№11); a failed send clears the
    // ghost instead of leaving a dimmed bubble forever
    addPending(slug, node.id, t)
    if (live) markBusy(slug, node.id)
    flashMode('')   // the previous send's receipt must not outlive this one
    toBottom()
    sendMessage(slug, node.id, t, paths)
      .then((r) => {
        // review C3: name every real outcome — "delivering" as the fallback
        // lied for frozen nodes (mail waits durably; nothing delivers now)
        flashMode(r.compacting ? 'compacting — the org way (§8)'
          : r.command ? 'command sent'
            : r.steering ? 'steering in mid-task'
              : r.frozen ? 'frozen — mail waits for ▶ resume'
                : r.deferred ? 'deferred — delivers at rehire'
                  : (r.queued ?? 0) > 0 ? `queued (${r.queued} ahead)` : 'delivering')
        if (r.warnings?.length) toast(r.warnings)
        // A COMMAND is not correspondence: it never enters pending_mail, and an
        // IMMEDIATE command may never reach this node's transcript either (it
        // runs in a throwaway fork and its output rides the live feed), so its
        // ghost has no evidence to graduate against and would sit there
        // forever. Retire it here — the "command sent" receipt is the feedback.
        if (r.command) dropPending(slug, node.id, t)
        return refresh(true)
      })
      .catch((e: Error) => {
        dropPending(slug, node.id, t)
        toast([`error: ${e.message}`])
      })
  }

  // file uploads (user spec 2026-07-31): the file lands in the agent's own
  // uploads/ scratch folder — same relative path sandboxed or not, and it
  // works through the public kiosk gateway from the outside internet
  const fileRef = useRef<HTMLInputElement | null>(null)
  // attachments STAGE onto the next message (user spec 2026-07-31: mail
  // carries files) — the bytes upload immediately, the mail links them
  const [attached, setAttached] = useState<{ name: string; path: string; bytes: number }[]>([])
  const attach = (file: File) => {
    uploadFile(slug, node.id, file)
      .then((r) => setAttached((a) =>
        [...a, { name: file.name, path: r.path, bytes: r.bytes }]))
      .catch((e: Error) => toast([`upload error: ${e.message}`]))
  }
  // №13: the composer grows with the draft (2 → ~8 rows); the desk interior
  // is a fixed 900px virtual panel, so .msgs absorbs the difference
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const grow = useCallback(() => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }, [])
  // the height follows the TEXT, not the keystroke. onChange was the only
  // caller, so every other way the value changes left the inline height
  // stale: SENDING cleared the draft and the box stayed tall until the desk
  // remounted (user bug 2026-08-02), a draft restored from localStorage
  // opened at two rows however long it was, and picking a slash hint did not
  // resize either. A layout effect measures POST-COMMIT, so the new value is
  // already in the DOM when this reads scrollHeight — reading it inside the
  // handler would measure the outgoing text.
  useLayoutEffect(grow, [text, grow])
  // №6: dropping a file anywhere on the desk uploads it (and prevents the
  // browser's default navigate-away, which would also eat the draft)
  const dropProps = {
    onDragOver: (e: React.DragEvent<HTMLDivElement>) => e.preventDefault(),
    onDrop: (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      if (e.dataTransfer?.files?.length) {
        [...e.dataTransfer.files].forEach(attach)
      }
    },
  }

  const liveKids = node.children.some((c) => c.state === 'live')
  const content = (
    <>
      <div className="cc-head">
        <span className={'tier t-' + node.tier}>{TIER_LETTER[node.tier!] ?? '?'}</span>
        <span className="cc-name"
          title={(node.charter || '').split('\n')[0] || node.id}>{node.id}</span>
        <ContextWheel occ={chat?.occupancy ?? node.occupancy} cw={node.context_window}
          compactAt={compactAt}
          onCompact={live && !node.bearer_state
            ? () => setAskCompact(true) : undefined} />
        {node.last_status &&
          <span className={'statuschip ' + node.last_status.status}
            title={node.last_status.summary}>{node.last_status.status}</span>}
        {/* №3: the word is the STATE, not a blanket "working" — compacting,
            queued behind the slot cap, and actually responding are different
            things and the backend already splits them */}
        {(node.busy || node.phase === 'compacting' || chat?.busy) &&
          <span className="cc-working">
            {node.phase === 'compacting' ? <>compacting…</>
              : node.waiting ? <>queued for a turn slot…</>
                : <><AutorenewIcon fontSize="inherit" className="cc-spin" /> working
                  {node.inflight_at ? <span className="dim"> · {ago(node.inflight_at)}</span> : null}</>}
          </span>}
        {node.frozen &&
          <span className="badge frozen" title={node.frozen.error ?? undefined}>
            <FrozenIcon fontSize="inherit" /> usage limit{node.frozen.until ? ` · resumes ${node.frozen.until}` : ''}</span>}
        {node.limit_locked &&
          <span className="badge dim"><LockIcon fontSize="inherit" /> limit</span>}
        {!compact && (node.generation ?? 0) > 0 &&
          <button className="badge stackbadge"
            onClick={onLineage}>gen {node.generation} <LayersIcon fontSize="inherit" /></button>}
        {!compact && node.bearer_state &&
          <span className={'badge ' + (node.bearer_state === 'preserving' ? 'dim' : '')}>
            {node.bearer_state}</span>}
        {!compact && node.audiences_held?.map((g) => (
          <span key={g} className={'badge ' + (g === USER ? 'free' : '')}>
            <HearingIcon fontSize="inherit" />
            {g === USER ? 'user' : g === EXTERN ? 'org inbox' : g}
            <button className="chip-x"
              onClick={() => audienceAction(slug, 'revoke', node.id, g)
                .then(() => toast([`audience ${node.id}→${g} rescinded`]))
                .catch((e: Error) => toast([`error: ${e.message}`]))}><CloseIcon fontSize="inherit" /></button>
          </span>
        ))}
        {!compact && (node.cost_usd ?? 0) > 0 && (
          <span className="badge dim"
            title={(node.turns ?? []).slice(-5).reverse().map((t) =>
              `${t.at?.slice(5, 16).replace('T', ' ')} · $${(t.cost ?? 0).toFixed(2)}`
              + (t.estimated ? ' est.' : '')
              + (t.ms ? ` · ${Math.round(t.ms / 1000)}s` : '')
              + (t.denials ? ` · ${t.denials} denied` : '')
              + (t.killed ? ' · killed' : '')).join('\n')
              || 'per-turn detail appears after the next turn'}>
            ${node.cost_usd!.toFixed(2)}</span>)}
        {(chat?.queued ?? 0) > 0 && <span className="badge">{chat!.queued} queued</span>}
        <span className="spacer" />
        {/* compact (switchboard panel): chat only — the agent's own desk keeps
            the full chrome (actions, tabs, gear) */}
        {!compact && <span className="cc-actions">
          {live && !liveKids &&
            <button className="danger"
              onClick={() => op({ op: 'retire', node: node.id }).then(() =>
                toast([`${node.id} retired`],
                  () => op({ op: 'rehire', node: node.id }).catch(() => {})))
                .catch(() => {})}>
              retire · {node.seat! + node.grant!}</button>}
          {live && liveKids &&
            <button className="danger" onClick={() => setAsking(true)}>
              dissolve · {node.seat! + node.grant!}</button>}
          {!live && <button onClick={() => op({ op: 'rehire', node: node.id })}>rehire</button>}
        </span>}
        {!compact && <span className="cc-tabs">
          {(['chat', 'history', 'files', 'inbox'] as const).map((v) => (
            <button key={v} className={view === v ? 'on' : ''}
              onClick={() => setView(v)}>
              {v}{v === 'inbox' && (chat?.mail_pending ?? 0) > 0 ? ` ${chat!.mail_pending}` : ''}
            </button>
          ))}
        </span>}
        {!compact && <button className="cc-icon" onClick={onConfig}><SettingsIcon fontSize="inherit" /></button>}
      </div>
      {/* F-01: superior chip at the TOP. For a top-level agent the superior is
          the user, so the chip targets the switchboard (map carries the eye
          root under USER). Bearer pseudo-cards float beside a successor and
          have no meaningful parent chip. */}
      {onJump && !node.isBearerOf && node.parent && map.has(node.parent) && (
        <div className="desk-nav">
          <NavChip n={map.get(node.parent)!} dir="up" onJump={onJump} />
        </div>
      )}
      {asking && (
        <ConfirmModal title={`dissolve ${node.id}?`}
          body="Its entire suborganization is retired with it. Context is kept; rehire brings nodes back."
          confirmLabel="dissolve"
          onConfirm={() => op({ op: 'dissolve', node: node.id })}
          close={() => setAsking(false)} />
      )}
      {askCompact && (
        <ConfirmModal title={`compact ${node.id} now?`}
          body="Same as the automatic split: the session forks and compacts — the successor carries on under this name; the pre-compaction self is archived in place as a consultable knowledge bearer."
          confirmLabel="compact"
          onConfirm={() => compactNode(slug, node.id)
            .then(() => toast([`compaction of ${node.id} started`]))
            .catch((e: Error) => toast([`error: ${e.message}`]))}
          close={() => setAskCompact(false)} />
      )}
      {/* last_error moved INTO the chat stream (it renders at the end, where
          it actually occurred). On the non-chat tabs it would otherwise be the
          only surface showing a failed turn, so it still renders here for
          those — never on the chat tab, which owns it chronologically. */}
      {chat?.last_error && view !== 'chat' && (
        <div className="desk-error"><WarnIcon fontSize="inherit" /> {chat.last_error}</div>)}
      {view === 'chat' && (
        <div className="msgs" ref={scroller}
          onScroll={(e) => {
            setStuck(nearBottom())
            // within a screen of the top: page in the previous window
            if (e.currentTarget.scrollTop < 240 && hasOlder) loadOlder()
          }}>
          {/* paging is automatic (the onScroll above pages in within a screen
              of the top) — this is a status line, not a control. It still
              earns its place: it reserves height so the list does not jump as
              rows prepend, and at the API's window cap it is the ONLY thing
              that explains why scrolling up stopped producing messages. */}
          {hasOlder && (
            <div className={'dim pad loadolder-status' + (loadingOlder ? ' on' : '')}>
              {loadingOlder ? 'loading earlier messages…'
                : convo.win >= MAX_WINDOW
                  ? `${chat?.messages[0]?.seq ?? 0} earlier messages — beyond the window`
                  : `${chat?.messages[0]?.seq ?? 0} earlier messages`}
            </div>)}
          {!hasOlder && convo.win > CHAT_WINDOW && chat?.messages.length
            ? <div className="dim pad loadolder-end">— start of the conversation —</div> : null}
          {!chat && <div className="dim pad">loading…</div>}
          {chat && !chat.messages.length && !live_feed.length &&
            <div className="dim pad">no conversation yet</div>}
          {chat?.messages.map((m, i) => {
            // №15: one dim divider per idle gap — never per-message timestamps
            const prev = chat.messages[i - 1]
            const gapMs = prev?.ts && m.ts
              ? Date.parse(m.ts) - Date.parse(prev.ts) : 0
            return (
              // seq = the server's pre-slice ordinal: index keys over the
              // sliding CHAT_WINDOW-row window remounted every row (and collapsed
              // every open ToolChip) each time one message scrolled off
              <div key={m.seq ?? i}>
                {gapMs > 5 * 60e3 && (
                  <div className="msg sys">— {gapMs > 5400e3
                    ? `${Math.round(gapMs / 3600e3)} h`
                    : `${Math.round(gapMs / 60e3)} min`} later —</div>)}
                <Msg m={m} slug={slug} nid={node.id} onMailLink={onMailLink} />
              </div>
            )
          })}
          {/* keyed on the server's row id (`n`), never the index: rows retire
              from the MIDDLE of this list as the transcript catches up, and an
              index key would rename every row below the one that left */}
          {live_feed.map((f, i) => (
            f.kind === 'thought'
              ? <div key={f.n ?? 'f' + i} className="msg assistant live">
                  <ThoughtLine text={f.text} secs={f.secs} /></div>
              : f.kind === 'tool'
                ? <div key={f.n ?? 'f' + i} className="msg live tools"><DotIcon fontSize="inherit" className="tooldot" /> {f.text}</div>
                : f.kind === 'steered'
                  // notices are split off here too: the live row would
                  // otherwise flash raw [ORG NOTICES] chrome for the second
                  // before the transcript refresh renders them as a card
                  ? <div key={f.n ?? 'f' + i} className="msg user live md"
                      dangerouslySetInnerHTML={md(stripEnvelope(splitNotices(f.text).rest))} />
                  : <div key={f.n ?? 'f' + i} className="msg assistant live md"
                      dangerouslySetInnerHTML={md(f.text)} />
          ))}
          {thinkSecs !== null && chat?.busy && (thinking
            // haiku streams its reasoning: the text IS the indicator
            ? <div className="msg live thinking">{thinking}</div>
            // opus/sonnet seal it: nothing to show but the fact and the clock,
            // which beats the blank panel this replaces
            : <div className="msg live thinking sealed">
                <PsychologyIcon fontSize="inherit" />{' '}thinking…
                {thinkSecs > 0 ? ` for ${thinkSecs}s` : ''}
              </div>)}
          {draft && <div className="msg assistant live md draft"
            dangerouslySetInnerHTML={md(draft)} />}
          {/* D-29: the turn has begun but the CLI has not produced anything
              yet — process launch, hooks, `init`, roughly six seconds during
              which the panel showed nothing but a spinner in the chrome. This
              is DERIVED, not a new event or a new state cell: busy, with
              nothing live, nothing thinking and nothing drafted, IS starting. */}
          {chat?.busy && !live_feed.length && thinkSecs === null && !draft
            && !pending.length && (
            <div className="msg live thinking sealed">
              <AutorenewIcon fontSize="inherit" className="cc-spin" /> starting…
            </div>)}
          {/* №11: pending bubbles render from the DURABLE server copy, each
              retractable until delivery (№17) */}
          {(chat?.pending_mail ?? []).filter((m) => m.from === USER).map((m) => (
            <div key={m.id ?? m.at} className="msg user pending pendrow">
              <span className="md" dangerouslySetInnerHTML={md(m.body)} />
              {(m.attachments ?? []).map((a) => (
                <span key={a.path} className="attach-chip dim">
                  <FileIcon fontSize="inherit" /> {a.name}</span>))}
              {/* journal-riding mail (drained for a mid-task delivery) shows
                  as queued but is past the point of retraction */}
              {m.delivering
                ? <span className="dim pend-tag">{m.via === 'turn'
                  ? 'delivering…' : 'delivering mid-task…'}</span>
                : m.id && (
                  <button className="chip-x" title="retract (undelivered)"
                    onClick={() => retractMail(slug, node.id, m.id!)
                      .then(() => refresh(true))
                      .catch((e: Error) => toast([`error: ${e.message}`]))}>
                    <CloseIcon fontSize="inherit" /></button>)}
            </div>
          ))}
          {pending.map((p) => (
            <div key={'q' + p.id} className="msg user pending md"
              dangerouslySetInnerHTML={md(p.text)} />
          ))}
          {/* the turn's own failure is the LAST thing that happened, so it
              reads at the end of the stream. It used to render above the whole
              transcript, which put the newest event first (user bug
              2026-08-02: events must appear in the order they occurred). */}
          {chat?.last_error && (
            <div className="desk-error"><WarnIcon fontSize="inherit" /> {chat.last_error}</div>)}
          {/* №7's denials banner is GONE (user bug 2026-08-02). A headless
              auto-deny already writes a tool_result with is_error, so the
              denial renders inline as an errored ToolChip at the point it
              happened — verified in a live transcript ("Claude requested
              permissions to write to …, but you haven't granted it yet").
              The banner restated that, pinned below even undelivered pending
              mail, so a past event sorted under a future one. */}
          {/* sticky INSIDE the scroller (not a wrapper): the desk's flex chain
              is documented as fragile, and sticky needs no new layout box. It
              is the last child, so it rides the bottom edge of the scrollport
              while the reader is up in the scrollback. */}
          {showJump && (
            <button className="jumpbottom" onClick={toBottom}
              title="jump to the newest message">
              ↓ jump to bottom
            </button>)}
        </div>
      )}
      {view === 'history' && <HistoryView slug={slug} nid={node.id} />}
      {view === 'files' && <FilesView slug={slug} nid={node.id} />}
      {view === 'inbox' && <InboxView slug={slug} nid={node.id}
        onRetract={(m) => retractMail(slug, node.id, m.id)
          .then(() => refresh(true))
          // rethrow: InboxView's optimistic hide rolls back on rejection
          .catch((e: Error) => { toast([`error: ${e.message}`]); throw e })} />}
      {/* F-01: subordinate chips at the BOTTOM — one per direct report. Drafts
          are not agents yet; bearer pseudo-cards are consultable stack layers,
          not reports. */}
      {onJump && node.children.some((c) => c.state !== 'draft' && !c.isBearerOf) && (
        <div className="desk-nav">
          {node.children.filter((c) => c.state !== 'draft' && !c.isBearerOf)
            .map((c) => <NavChip key={c.id} n={c} dir="down" onJump={onJump} />)}
        </div>
      )}
      {/* №13: the composer is present under EVERY tab — finding a wrong number
          on the files tab shouldn't cost your place to say so */}
      {sendMode && <div className="sendmode dim">{sendMode}</div>}
      {/* staged attachments ride the NEXT message as mail attachments */}
      {attached.length > 0 && (
        <div className="attach-row">
          {attached.map((a, i) => (
            <span key={a.path + i} className="attach-chip">
              <FileIcon fontSize="inherit" /> {a.name}
              <span className="dim"> {fmtBytes(a.bytes)}</span>
              <button className="chip-x" title="remove from this message"
                onClick={() => setAttached((x) => x.filter((_, j) => j !== i))}>
                <CloseIcon fontSize="inherit" /></button>
            </span>
          ))}
        </div>
      )}
      {text.trimStart().startsWith('/') && canMail && (
        <SlashHints text={text} setText={setText} />)}
      <div className={'cc-composer' + (canMail ? '' : ' off')}>
        <button className="cc-attach" disabled={!canMail}
          title="attach a file — it lands in the agent's uploads/ folder"
          onClick={() => fileRef.current?.click()}>
          <FileIcon fontSize="inherit" /></button>
        <input type="file" ref={fileRef} style={{ display: 'none' }} multiple
          onChange={(e) => {
            [...e.target.files!].forEach(attach)
            e.target.value = ''
          }} />
        <textarea rows={2} value={text} disabled={!canMail}
          ref={(el) => {
            taRef.current = el
            // autofocus single-desk only, and never let focus scroll the
            // transform-panned viewport (same hazard as the draft input)
            if (el && !bare && !compact && !el.dataset.f) {
              el.dataset.f = '1'
              el.focus({ preventScroll: true })
            }
          }}
          placeholder={live ? `message ${node.id}…`
            : node.state === 'archived'
              ? `message ${node.id} — queued until rehire…` : node.state}
          onChange={(e) => { flashMode(''); setText(e.target.value); grow() }}
          onPaste={(e) => {
            // №6: Ctrl+V of an image/file auto-bridges to a real upload
            if (e.clipboardData?.files?.length) {
              e.preventDefault()
              ;[...e.clipboardData.files].forEach(attach)
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
          }} />
        {!pub && (
          <EffortButton value={node.scope?.effort ?? ''}
            effective={node.effort_effective ?? ''}
            onSet={(lvl) => saveScope(slug, node.id, { effort: lvl })
              .then(() => toast([lvl
                ? `${node.id} thinking effort: ${lvl}`
                : `${node.id} thinking effort: back to the org default`]))
              .catch((e: Error) => toast([`error: ${e.message}`]))} />
        )}
        {/* №3: STOP renders only when an interrupt can actually land —
            pressing the one red control must never error. Gate on the CHAT
            payload's responding (refreshed every pulse + 5 s poll): the tree
            copy goes stale during a turn and the STOP never appeared while a
            long command ran (user bug 2026-07-31). Enter still queues. */}
        {(chat?.responding ?? node.responding)
          ? <button className="cc-send stop" title="interrupt the current response — Enter still queues your message"
              onClick={() => interruptNode(slug, node.id)
                .then((r) => { if (!r.interrupted) toast([`error: ${r.reason}`]) })
                .catch((e: Error) => toast([`error: ${e.message}`]))}><StopIcon fontSize="inherit" /></button>
          : <button className="cc-send" disabled={!canMail || !text.trim()}
              onClick={send}><ArrowUpIcon fontSize="inherit" /></button>}
      </div>
    </>
  )
  // bare: the switchboard hosts many chats inside ONE counter-scaled surface —
  // no overlay wrapper, no second scale (that would double-scale), no
  // recenter-on-click
  if (bare) return <div className="desk-body eye-chat" {...dropProps}>{content}</div>
  return (
    <div className="desk-over" onWheel={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()} {...dropProps}
      onClick={(e) => {
        // clicking the desk's non-interactive space recenters the camera on
        // it (user ruling) — but never steal clicks meant for controls, and
        // never fight an in-progress text selection
        if ((e.target as Element).closest('button, input, textarea, select, a, label, .mailrow, .eff-pop')) return
        if (window.getSelection()?.toString()) return
        onRecenter?.()
      }}>
      <div className="desk-inner desk-body">{content}</div>
    </div>
  )
}
function HistoryView({ slug, nid }: { slug: string; nid: string }) {
  // G5: the agent keeps acting while this tab is open — a fetch-once list is
  // a photograph of the moment the tab was clicked
  const items = usePolled(() => getHistory(slug, nid).then((r) => r.items), [slug, nid])
  return (
    <div className="msgs">
      {items == null && <div className="dim pad">loading…</div>}
      {items?.length === 0 && <div className="dim pad">nothing recorded yet</div>}
      {items?.map((it, i) => (
        <div key={i} className="hist-row">
          <span className="dim">{it.at}</span>
          <b>{it.kind}</b>
          <span className="dim">{it.actor}</span>
          <span>{it.detail.gist ?? it.detail.text ?? Object.entries(it.detail)
            .filter(([k]) => k !== 'gist').map(([k, v]) => `${k}=${v}`).join(' · ')}</span>
        </div>
      ))}
    </div>
  )
}

function FilesView({ slug, nid }: { slug: string; nid: string }) {
  const [path, setPath] = useState('')
  // G5: same — the agent writes into this very directory while you browse it
  const data = usePolled(() => getScratch(slug, nid, path), [slug, nid, path])
  const up = () => setPath(path.split('/').slice(0, -1).join('/'))
  // union split (type-only narrowing): a scratch payload is a dir listing OR
  // a file body — the two reads below each see only their variant
  const entries = data && 'entries' in data ? data.entries : null
  const content = data && 'content' in data ? data.content : null
  return (
    <div className="msgs files">
      <div className="hist-row">
        <button onClick={() => setPath('')}>scratch</button>
        {path && <button onClick={up}><ArrowUpIcon fontSize="inherit" /> up</button>}
        <span className="dim mono">/{path}</span>
      </div>
      {!data && <div className="dim pad">loading…</div>}
      {entries && !entries.length && <div className="dim pad">empty</div>}
      {entries?.map((e) => (
        <div key={e.name} className="hist-row">
          {e.dir
            ? <button onClick={() => setPath(path ? `${path}/${e.name}` : e.name)}><FolderIcon fontSize="inherit" /> {e.name}</button>
            : <button onClick={() => setPath(path ? `${path}/${e.name}` : e.name)}><FileIcon fontSize="inherit" /> {e.name}</button>}
          {!e.dir && <span className="dim">{fmtBytes(e.size)}</span>}
          {!e.dir && (
            <a className="fdl" title="download"
              href={fileUrl(slug, nid, path ? `${path}/${e.name}` : e.name)}
              download={e.name}><DownloadIcon fontSize="inherit" /></a>)}
        </div>
      ))}
      {content != null && <pre className="filepre">{content}</pre>}
    </div>
  )
}
interface LineagePanelProps {
  node: CanvasNode
  op: OpFn
  slug: string
  close: () => void
}

export function LineagePanel({ node, op, slug, close }: LineagePanelProps) {
  // spitshined (user request): generation cards in the app's current visual
  // language — tier token, per-generation consult-tier picker (№16: a bearer
  // answers from context, so any tier serves), live bearers marked green
  useEsc(close)
  const [tiers, setTiers] = useState<Record<string, string>>({})       // per-generation tier override
  // №12: READING an archived bearer's transcript is free — rehiring is for
  // asking it questions, not for looking at what it holds
  const [reading, setReading] = useState<string | null>(null)     // bearer id being read
  const [readChat, setReadChat] = useState<Pick<ChatPayload, 'messages'> | null>(null)
  const readingRef = useRef<string | null>(null)
  const openRead = (bid: string) => {
    if (reading === bid) { setReading(null); readingRef.current = null; return }
    setReading(bid); setReadChat(null)
    readingRef.current = bid
    // request guard (review): read_chat parses the whole transcript, so a
    // slow gen-3 landing after a fast gen-1 rendered under the wrong header
    getChat(slug, bid)
      .then((c) => { if (readingRef.current === bid) setReadChat(c) })
      .catch(() => {
        if (readingRef.current === bid) setReadChat({ messages: [] })
      })
  }
  const SEAT = TIER_SEAT      // one table (shared.ts), mirrors ledger.TIERS
  const gens = [...(node.lineage ?? [])].sort(
    (a, b) => (b.generation ?? 0) - (a.generation ?? 0))
  return (
    <div className="overlay" onClick={close} onPointerDown={(e) => e.stopPropagation()}>
      <div className="settings lineage-panel" onClick={(e) => e.stopPropagation()}>
        <h3><LayersIcon fontSize="inherit" /> {node.id} — lineage</h3>
        <div className="dim lin-blurb">
          Every generation is this agent's pre-compaction self, archived in
          place with its full context. Rehire one as a consultable knowledge
          bearer — it answers questions beside its successor; any tier works,
          cheaper tiers consult for fewer credits.
        </div>
        {gens.map((b) => (
          <div key={b.id}>
            <div className={'lin-row' + (b.state === 'archived' ? '' : ' live')}>
              <span className={'tier t-' + b.tier}>{TIER_LETTER[b.tier] ?? '?'}</span>
              <div className="lin-id">
                <b className="mono">{b.id}</b>
                <span className="dim">
                  generation {b.generation}
                  {b.bearer_state ? ` · ${b.bearer_state} bearer` : ''}
                </span>
              </div>
              {b.bearer_state !== 'lost' && (
                <button className={reading === b.id ? 'on' : ''}
                  title="read this generation's transcript — free, no seat"
                  onClick={() => openRead(b.id)}>read</button>)}
              {b.state === 'archived' && b.bearer_state !== 'lost' ? (
                <>
                  <select value={tiers[b.id] ?? ''} onChange={(e) =>
                    setTiers((t) => ({ ...t, [b.id]: e.target.value }))}>
                    <option value="">as {b.tier} · seat {SEAT[b.tier]}</option>
                    {['haiku', 'sonnet', 'opus'].filter((t) => t !== b.tier)
                      .map((t) => (
                        <option key={t} value={t}>as {t} · seat {SEAT[t]}</option>
                      ))}
                  </select>
                  <button className="primary" onClick={() =>
                    op({ op: 'rehire', node: b.id, grant: 0,
                         ...(tiers[b.id] ? { tier: tiers[b.id] } : {}) })
                      .then(close).catch(() => {})}>
                    <PlayIcon fontSize="inherit" /> rehire</button>
                </>
              ) : b.bearer_state === 'lost' ? (
                <span className="badge dim"
                  title="its session was lost — kept for the record, not consultable">
                  lost generation</span>
              ) : (
                <>
                  <span className="badge free">consultable</span>
                  <button className="danger" onClick={() =>
                    op({ op: 'retire', node: b.id }).then(close).catch(() => {})}>
                    retire · frees {SEAT[b.tier]}</button>
                </>
              )}
            </div>
            {reading === b.id && (
              <div className="lin-read">
                {readChat == null
                  ? <div className="dim pad">loading transcript…</div>
                  : readChat.messages.length
                    ? readChat.messages.slice(-80).map((m, i) => (
                        <Msg key={i} m={m} slug={slug} nid={b.id} />))
                    : <div className="dim pad">no transcript found</div>}
              </div>)}
          </div>
        ))}
        {!gens.length &&
          <div className="dim pad">no prior generations — this agent has never compacted</div>}
        <div className="row"><button onClick={close}>close</button></div>
      </div>
    </div>
  )
}
// Incoming turns are mail envelopes (messages ARE mail); for the chat view,
// The envelope also prepends an [ORG NOTICES — n change(s)…] block to the next
// turn's message (supervisor._envelope). That is machine chrome about the ORG,
// not part of what the sender wrote, so it is pulled out here and rendered as
// its own collapsed card rather than sitting inside the bubble (user bug
// 2026-08-02). Anchored at the start because _envelope builds the prelude
// notices-first; the trailing \n* eats the blank line before the mail block.
const NOTICE_RE = /^\s*\[ORG NOTICES[^\]\n]*\]\n([\s\S]*?)\n\[END NOTICES\]\n*/
const splitNotices = (t: string | null | undefined) => {
  const s = t ?? ''
  const m = NOTICE_RE.exec(s)
  if (!m) return { notices: [] as string[], rest: s }
  const notices = (m[1] ?? '').split('\n')
    .map((l) => l.replace(/^\s*-\s*/, '').trim()).filter(Boolean)
  return { notices, rest: s.slice(m[0].length) }
}

// The restart replay (supervisor.reconcile) re-sends the message that drove an
// interrupted turn, prefixed with this marker. Re-delivery is deliberate and
// load-bearing — D-045's "worst case a duplicate, never a loss" — but the
// reader already knows what they typed, so it folds into a one-line marker
// instead of replaying their own prompt back at them (user, 2026-08-02).
const RESTART_MARK = '[ORGTREE RESTART]'
const isRestart = (t: string | null | undefined) =>
  (t ?? '').trimStart().startsWith(RESTART_MARK)

// hide the machine chrome — [MAIL]/[END MAIL] markers, drive nudges — and
// render the FROM attribution as a small header instead of body text.
const stripEnvelope = (t: string | null | undefined) => (t ?? '')
  .split('\n')
  .filter((l) => !/^\[(MAIL — .*|END MAIL)\]$/.test(l.trim())
    && !l.trim().startsWith('(orgtree) '))
  .join('\n')
  .replace(/^FROM (\S+) \([^)]*\) · \S+ · \S+$/gm, '**$1**')
  .replace(/^FROM (\S+) \([^)]*\)$/gm, '**$1**')
  .trim()

// Parity №1/№9/№10: the tool line says what it did — argument on the chip, a
// red bit + first error line on failure, and the RESULT collapsed behind a
// click (never inline: an always-expanded stream turns the desk into a log
// tail). Edits expand to their pre-computed hunk.
interface ToolChipProps {
  t: ToolChipData
  slug: string
  nid: string
  onMailLink?: MailLinkFn
}

function ToolChip({ t, slug, nid, onMailLink }: ToolChipProps) {
  const [open, setOpen] = useState(false)
  const expandable = Boolean(t.result || t.diff || t.images)
  // orgtree_send_file → a DOWNLOAD CARD in place of the chip (user spec
  // 2026-07-31: files flow back — the card sits where the agent sent it)
  if (t.file) {
    return (
      <a className="filecard" href={fileUrl(slug, nid, t.file.path!)}
        download={t.file.name} title="download">
        <DownloadIcon fontSize="inherit" className="fc-ico" />
        <span className="fc-body">
          <span className="fc-name">{t.file.name}</span>
          <span className="dim"> · {fmtBytes(t.file.bytes)}</span>
          {t.file.note && <span className="fc-note">{t.file.note}</span>}
        </span>
      </a>
    )
  }
  return (
    <div className={'tools tchip' + (t.error ? ' terr' : '')}>
      <span className={'tline' + (expandable ? ' click' : '')}
        onClick={expandable ? () => setOpen((o) => !o) : undefined}
        title={expandable ? (open ? 'collapse' : 'expand') : undefined}>
        <DotIcon fontSize="inherit" className="tooldot" />
        {' '}{shortTool(t.name)}
        {t.arg ? <span className="targ"> {t.arg}</span> : null}
        {t.diff && <span className="tdiffn"> +{t.diff.plus} −{t.diff.minus}</span>}
        {!t.diff && !t.error && (t.result_lines ?? 0) > 0 && (
          <span className="dim"> · {t.result_lines} line{t.result_lines === 1 ? '' : 's'}</span>)}
        {t.task && (
          <span className="dim"> · {t.task.tools ?? '?'} tools
            {t.task.ms ? ` · ${Math.round(t.task.ms / 1000)}s` : ''}
            {t.task.tokens ? ` · ${Math.round(t.task.tokens / 1000)}k tok` : ''}</span>)}
        {(t.images ?? 0) > 0 && <span className="dim"> · {t.images} image{t.images === 1 ? '' : 's'}</span>}
        {t.error && <span className="terrtxt"> ⊘ {t.error}</span>}
        {/* mail sends carry the inline "open in mailbox" link (user spec):
            straight to the exact mail in whichever box holds it */}
        {t.mail && onMailLink && (
          <button className="maillink"
            title={t.mail.to === 'user_inbox'
              ? 'open this mail in your inbox'
              : `open this mail in ${String(t.mail.to).startsWith('@')
                ? 'the org inbox' : `${t.mail.to}'s inbox`}`}
            onClick={(e) => { e.stopPropagation(); onMailLink!(t.mail) }}>
            <MailIcon fontSize="inherit" /> open</button>)}
      </span>
      {open && t.diff && (
        <pre className="filepre diffpre">
          {t.diff.lines.map((l, i) => (
            <div key={i} className={l.startsWith('@@') ? 'dhunk'
              : l.startsWith('+') ? 'dplus'
              : l.startsWith('-') ? 'dminus' : ''}>{l}</div>))}
          {t.diff.truncated && <div className="dim">… truncated</div>}
        </pre>)}
      {open && !t.diff && t.result && (
        <pre className="filepre respre">
          {t.result}{t.truncated ? '\n… truncated' : ''}
        </pre>)}
      {open && (t.images ?? 0) > 0 && t.id && Array.from({ length: t.images! }).map((_, i) => (
        <img key={i} className="toolimg" alt="tool result"
          src={`${BASE}/api/orgs/${slug}/nodes/${nid}/toolimg/${t.id}?idx=${i}`} />))}
    </div>
  )
}

// №21: memoized — rows are static once fetched; only identity changes matter
const Msg = memo(function Msg({ m, slug, nid, onMailLink }: {
  m: ChatMessage; slug: string; nid: string; onMailLink?: MailLinkFn
}) {
  if (m.role === 'system') return <SysLine m={m} />
  // notices come out BEFORE the envelope strip — they are their own card
  const { notices, rest } = m.role === 'user'
    ? splitNotices(m.text) : { notices: [] as string[], rest: m.text }
  // a restart replay is machinery, not something the reader said: one line,
  // with the repeated prompt behind a click for anyone who wants to confirm it
  if (m.role === 'user' && isRestart(rest)) {
    return (
      <div className="msg user restartmsg">
        {notices.length > 0 && <NoticeLine notices={notices} />}
        <RestartLine text={stripEnvelope(rest)} />
      </div>
    )
  }
  const text = m.role === 'user' ? stripEnvelope(rest) : m.text
  return (
    <div className={'msg ' + m.role + (m.oracle ? ' oracle' : '')}>
      {notices.length > 0 && <NoticeLine notices={notices} />}
      {(m.thinking || m.thinking_sealed) &&
        <ThoughtLine text={m.thinking} secs={m.think_secs}
          sealed={m.thinking_sealed} />}
      {/* (the string branch guards legacy live rows; the payload's tools
          rows are null-swept server-side, so no null case exists) */}
      {(m.tools ?? []).map((t, i) => (typeof t === 'string'
        ? <div key={i} className="tools"><DotIcon fontSize="inherit" className="tooldot" /> {t}</div>
        : <ToolChip key={t.id ?? i} t={t} slug={slug} nid={nid}
            onMailLink={onMailLink} />))}
      {text && <div className="msgtext md" dangerouslySetInnerHTML={md(text)} />}
      {m.oracle && <div className="tools"><SparkIcon fontSize="inherit" /> oracle exchange — not retained by the node</div>}
    </div>
  )
})

// "resumed after a restart" — the replayed prompt is hidden by default because
// the reader typed it and can see it upstream; one click proves what the agent
// was actually re-sent, which matters when diagnosing a duplicated turn.
function RestartLine({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="thoughtwrap">
      <button className="thoughtline noticeline" onClick={() => setOpen((o) => !o)}
        title={open ? 'collapse' : 'show what was re-sent to the agent'}>
        <AutorenewIcon fontSize="inherit" />
        {' '}resumed after an orgtree restart {open ? '▾' : '▸'}
      </button>
      {open && <div className="thoughtbody noticebody">{text}</div>}
    </div>
  )
}

// Org-change notices (hire/retire/reallocate/move/scope) ride in on the next
// turn's message. They are about the ORG, not the conversation, so they fold
// into their own collapsed card — same shape as the thought line, deliberately
// (one collapse vocabulary in the transcript, not two).
function NoticeLine({ notices }: { notices: string[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="thoughtwrap">
      <button className="thoughtline noticeline" onClick={() => setOpen((o) => !o)}
        title={open ? 'collapse' : 'read the org changes delivered with this message'}>
        <AutorenewIcon fontSize="inherit" />
        {' '}{notices.length} notice{notices.length === 1 ? '' : 's'} {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="thoughtbody noticebody">
          {notices.map((n, i) => <div key={i}>{n}</div>)}
        </div>
      )}
    </div>
  )
}

// №18 evolved (user spec 2026-07-31): after thinking wraps up it folds into a
// small clickable "thought for Xs" line; the click expands the thought
// process. Fed live (measured) while the turn runs, and from the transcript's
// thinking blocks (gap-derived seconds) ever after.
// `sealed` = the block arrived signature-only, its plaintext withheld by the
// API (the normal case since 2026-08-02). The thought and its duration are
// still real, so the line stays — as a plain marker with no expander, because
// an expander that opens on nothing is worse than no expander.
function ThoughtLine({ text, secs, sealed }:
{ text?: string; secs?: number; sealed?: boolean }) {
  const [open, setOpen] = useState(false)
  const dur = secs ? `${secs}s` : 'a moment'
  if (sealed || !text) {
    return (
      <div className="thoughtwrap">
        <span className="thoughtline sealed"
          title="the model's reasoning was not included in the response — only its duration is known">
          <PsychologyIcon fontSize="inherit" />{' '}thought for {dur}
        </span>
      </div>
    )
  }
  return (
    <div className="thoughtwrap">
      <button className="thoughtline" onClick={() => setOpen((o) => !o)}
        title={open ? 'collapse' : 'read the thought process'}>
        <PsychologyIcon fontSize="inherit" />
        {' '}thought for {dur} {open ? '▾' : '▸'}
      </button>
      {open && <div className="thoughtbody">{text}</div>}
    </div>
  )
}

// №5: the compaction boundary carries its summary behind a click — never a
// 20 KB bubble in the user's voice
function SysLine({ m }: { m: ChatMessage }) {
  const [open, setOpen] = useState(false)
  // slash-command output (/context…): the output IS the point — an always-
  // visible markdown block, fixed from the flash-then-vanish live-only bug
  if (m.cmd_out) {
    return (
      <div className="msg sys cmdout">
        <div className="msgtext md" dangerouslySetInnerHTML={md(m.cmd_out)} />
      </div>
    )
  }
  return (
    <div className={'msg sys' + (m.summary ? ' click' : '')}
      onClick={m.summary ? () => setOpen((o) => !o) : undefined}
      title={m.summary ? (open ? 'collapse' : 'read the compaction summary') : undefined}>
      {m.text}{m.summary && !open ? ' · summary ▶' : ''}
      {open && m.summary && <pre className="filepre">{m.summary}</pre>}
    </div>
  )
}

// Thinking-effort control in the composer (user spec): a SMALL button beside
// send; the five-dot track (Claude Code's control) lives in a popover it
// opens — never inline in the entry row. Click a dot to set low…max, click
// the active dot to clear back to the CLI default. The permission-mode half
// of Claude Code's bar is deliberately absent: org permissions decide what
// agents can do.
const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max']

// `effective` is what the next turn WILL run at, resolved server-side by
// Org.effective_effort — the same call that builds the --effort flag, so the
// control and the runtime cannot disagree. It is never empty: orgtree passes
// the flag on every turn precisely so that this can always name a level.
// (Reported three times before it was right. Attempt 1 read only
// node.scope.effort, so an unconfigured agent showed nothing. Attempt 2 fell
// back to an effort field in the transcript, which the CLI writes for opus and
// not for haiku — so it worked on the agent I happened to test and nowhere
// else. The lesson is in §7 of docs/state-architecture-review.md: read the
// value that CAUSES the behaviour, not one that correlates with it.)
function EffortButton({ value, effective, onSet }:
{ value: string; effective?: string
  onSet: (lvl: string) => Promise<unknown> | void }) {
  const [open, setOpen] = useState(false)
  // OPTIMISTIC (user report 2026-08-03: "a lag of around 3-5 seconds when i
  // change the effort level before it updates visually"). The control used to
  // render purely from the tree payload, so the click showed nothing until a
  // refetch landed — which is fast when a broadcast arrives and up to a full
  // heartbeat when one does not. The click already KNOWS the new level, so
  // stop making the user wait for the server to say it back.
  //
  // `null` = nothing pending, `''` = a pending CLEAR (distinct from null, which
  // is why this is not just a string). It is uncommitted-operation state, not a
  // mirror of server data — the same exception the retract path takes — and it
  // is dropped the moment the payload speaks, whatever the payload says, so a
  // rejected or clamped write corrects itself rather than sticking.
  //
  // ⚠ "the payload speaks" is an EFFECT ON CHANGE — a 200 that changes nothing
  // (the write clamped or ignored, props come back identical) never fires it,
  // and the phantom level would stick with its .saving dim forever. So a
  // resolved write also arms a bounded settle: if the payload has not spoken
  // within a broadcast round-trip, drop the phantom and show the truth.
  const [pending, setPending] = useState<string | null>(null)
  const settle = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => { setPending(null) }, [value, effective])
  useEffect(() => () => { if (settle.current) clearTimeout(settle.current) }, [])
  // a pending CLEAR falls back to `effective`, which is still the old level for
  // one refresh — the org default is not known here. Transient and honest: it
  // is what the control showed before this change anyway.
  const shown = (pending || value || effective || '')
  const why = value ? 'set on this agent'
    : 'inherited — change it on this agent, or org-wide in ⚙ settings'
  const wrapRef = useRef<HTMLSpanElement | null>(null)
  useEffect(() => {
    if (!open) return
    // capture-phase on window: fires before the desk's stopPropagation walls
    const away = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node | null)) setOpen(false)
    }
    window.addEventListener('pointerdown', away, true)
    return () => window.removeEventListener('pointerdown', away, true)
  }, [open])
  return (
    <span className="eff-wrap" ref={wrapRef}>
      <button type="button"
        className={'cc-eff' + ((pending ?? value) ? ' set' : shown ? ' inherited' : '')
          + (pending !== null ? ' saving' : '')}
        title={`thinking effort — ${shown || 'unset'} (${why})`}
        onClick={() => setOpen((o) => !o)}>
        {shown || 'effort'}
      </button>
      {open && (
        <span className="eff-pop">
          <EffortSwitch value={pending ?? value} level={shown}
            why={(pending ?? value) ? 'set here' : 'inherited'}
            onSet={(lvl) => {
              setPending(lvl)
              setOpen(false)
              if (settle.current) clearTimeout(settle.current)
              // the payload normally lands first and clears this; the settle
              // covers a 200 that changed nothing, the catch a write that
              // never landed at all
              Promise.resolve(onSet(lvl))
                .then(() => { settle.current = setTimeout(() => setPending(null), 2500) })
                .catch(() => setPending(null))
            }} />
        </span>
      )}
    </span>
  )
}

function EffortSwitch({ value, level, why, onSet }:
{ value: string; level: string; why: string; onSet: (lvl: string) => void }) {
  // the track lights at the level that will actually be USED so it always says
  // what will happen; `pinned` is what a click can clear, which is only the
  // node's own setting — clicking an unpinned dot pins it rather than clearing
  // nothing. `why` is the caller's one description of where the level came
  // from, so the button and the popover can never word it differently.
  const pinned = EFFORT_LEVELS.indexOf(value)
  const idx = pinned >= 0 ? pinned : EFFORT_LEVELS.indexOf(level)
  return (
    <span className="effort-switch"
      title={`thinking effort — ${level || 'unset'} (${why})`
        + '; click a dot to set, click the active dot to clear back to inherit'}>
      <span className="eff-label">Effort{level
        ? ` (${level}${value ? '' : ` — ${why}`})` : ''}</span>
      <span className="eff-track">
        {EFFORT_LEVELS.map((l, i) => (
          <button key={l} type="button"
            className={'eff-dot' + (i === idx ? ' on' : '')
              + (idx >= 0 && i < idx ? ' below' : '')
              + (pinned < 0 ? ' faint' : '')}
            title={l}
            onClick={() => onSet(i === pinned ? '' : l)} />
        ))}
      </span>
    </span>
  )
}

// Slash commands (user-approved 2026-07-31): light HINTING when the draft
// starts with "/" — a curated list of commands known to work headless, not a
// clickable palette. Sent verbatim as a session command (no mail envelope).
const SLASH_COMMANDS: [string, string][] = [
  // review C4: /compact routes to the SAME §8 org split as the compact
  // button (fork → compact → knowledge bearer) — the hint must not describe
  // a bearer-less in-place compaction as "what the org does automatically"
  ['/compact', 'compact the org way (§8): the pre-compaction self is kept as a knowledge bearer'],
  ['/context', 'show what is using the context window'],
  ['/cost', 'token + cost usage for this session'],
]

function SlashHints({ text, setText }: { text: string; setText: (v: string) => void }) {
  const head = text.trim().split(/\s/)[0]! // nUIA: split always yields at least one element
  const rows = SLASH_COMMANDS.filter(([c]) => c.startsWith(head))
  return (
    <div className="slash-hints">
      {rows.map(([c, d]) => (
        <button key={c} className="slash-row" onClick={() => setText(c)}>
          <b>{c}</b> <span className="dim">{d}</span>
        </button>
      ))}
      <span className="dim slash-note">
        sent as a session command, not mail — other CLI commands may work; the
        interactive-only ones will not
      </span>
    </div>
  )
}
