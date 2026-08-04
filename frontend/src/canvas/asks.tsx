// canvas/asks.tsx — the unified ask system (F-04 questions + F-05 credit
// counter-offers, user-ruled 2026-08-04). An ask is a card that renders in
// TWO places at once — the asking agent's desk and the user's inbox — and is
// answered from whichever the user reaches first. Answering (or any other
// mail waking the agent) nulls it EVERYWHERE: the card stays visible wearing
// its reason — grey "answered", orange "interrupted" — instead of vanishing,
// so the user is never left wondering where a question went.
//
// The answer itself travels as ordinary user mail (it is what drives the
// agent's next turn); this file never fabricates conversation state.

import { useEffect, useRef, useState } from 'react'
import type { AskInfo, ToastFn } from '../types'
import { answerAsk, creditDecide } from '../api'
import { CreditBar } from './cards'
import { CheckIcon, PsychologyIcon, WarnIcon } from '../icons'

export function AskCard({ ask, slug, toast, seat = 0, committed = 0, maxTop }: {
  ask: AskInfo
  slug: string
  toast: ToastFn
  /** credit-mode bar geometry: the asking node's seat and committed
   *  (grant − free) — committed is also the drag FLOOR, mirroring
   *  reallocate's own invariant rather than inventing one */
  seat?: number
  committed?: number
  maxTop?: number
}) {
  const live = ask.status === 'open' || ask.status === 'pending'
  const credit = ask.kind === 'credit' || ask.old != null
  if (!live) return <NulledAsk ask={ask} credit={credit} />
  return credit
    ? <CreditAsk ask={ask} slug={slug} toast={toast}
        seat={seat} committed={committed} maxTop={maxTop} />
    : <QuestionAsk ask={ask} slug={slug} toast={toast} />
}

/** F-04: mirror of AskUserQuestion's shape — 2-4 options, optional
 *  multi-select, and free text always available alongside. */
function QuestionAsk({ ask, slug, toast }: {
  ask: AskInfo; slug: string; toast: ToastFn
}) {
  const [sel, setSel] = useState<string[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const toggle = (o: string) => setSel((s) => s.includes(o)
    ? s.filter((x) => x !== o)
    : ask.multi ? [...s, o] : [o])
  const send = () => {
    if (busy || (!sel.length && !text.trim())) return
    setBusy(true)
    answerAsk(slug, ask.id, {
      ...(sel.length ? { selected: sel } : {}),
      ...(text.trim() ? { text: text.trim() } : {}),
    })
      // stays busy on success — the next payload turns the card grey
      .then(() => toast([`answered ${ask.node}`]))
      .catch((e: Error) => { toast([`error: ${e.message}`]); setBusy(false) })
  }
  return (
    <div className="askcard">
      <div className="ask-head">
        <PsychologyIcon fontSize="inherit" />
        <b>{ask.node}</b> asks
      </div>
      <div className="ask-q">{ask.question}</div>
      {(ask.options ?? []).length > 0 && (
        <div className="ask-opts">
          {ask.options!.map((o) => (
            <button key={o} className={'ask-opt' + (sel.includes(o) ? ' on' : '')}
              disabled={busy} onClick={() => toggle(o)}>
              {sel.includes(o) && <CheckIcon fontSize="inherit" />} {o}
            </button>
          ))}
          {ask.multi && <span className="dim">several may apply</span>}
        </div>
      )}
      <div className="ask-answer-row">
        <input value={text} disabled={busy}
          placeholder={ask.options?.length ? 'or answer in your own words…' : 'your answer…'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send() }} />
        <button className="primary" disabled={busy || (!sel.length && !text.trim())}
          onClick={send}>{busy ? 'sending…' : 'answer'}</button>
      </div>
    </div>
  )
}

/** F-05: the request embeds its own drag-adjustable CreditBar — reduce the
 *  ask, exceed it, or claw back down to the committed floor. Stranding
 *  warnings surface on release, BEFORE the commit. */
function CreditAsk({ ask, slug, toast, seat, committed, maxTop }: {
  ask: AskInfo; slug: string; toast: ToastFn
  seat: number; committed: number; maxTop?: number
}) {
  const oldG = ask.old ?? 0
  const askedG = ask.new ?? oldG
  const [g, setG] = useState(askedG)          // the staged offer
  const [warns, setWarns] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  // dry-run on release (and on first render for the pre-loaded ask amount):
  // the warnings a reduction strands are exactly what a user dragging
  // DOWNWARD needs to see before committing
  const gRef = useRef(g)
  gRef.current = g
  const preview = () => {
    void creditDecide(slug, ask.id, 'approve', gRef.current, true)
      .then((r) => setWarns(r.warnings ?? []))
      .catch(() => setWarns([]))
  }
  useEffect(preview, [])          // eslint-disable-line react-hooks/exhaustive-deps
  const barMax = Math.max(maxTop ?? 0, askedG * 1.5, oldG * 1.5, 10)
  const pxc = Math.min(6, 150 / Math.max(1, seat + barMax))
  const decide = (action: string, granted?: number) => {
    if (busy) return
    setBusy(true)
    creditDecide(slug, ask.id, action, granted)
      .then((r) => {
        const w = (r.warnings ?? []) as string[]
        toast([action === 'deny' ? `denied ${ask.node}'s request`
          : `${ask.node}'s grant → ${granted ?? askedG}`, ...w])
      })
      .catch((e: Error) => { toast([`error: ${e.message}`]); setBusy(false) })
  }
  return (
    <div className="askcard credit">
      <div className="ask-head">
        <PsychologyIcon fontSize="inherit" />
        <b>{ask.node}</b> asks for credits: {oldG} → {askedG}
        <span className="dim">(+{askedG - oldG})</span>
      </div>
      {typeof ask.reason === 'string' && ask.reason &&
        <div className="ask-q dim">{ask.reason}</div>}
      <div className="ask-credit-row">
        <div className="ask-barbox" style={{ height: (seat + barMax) * pxc + 8 }}>
          <CreditBar seat={seat} grant={g} committed={committed} draftMode
            baseline={oldG} min={committed}
            max={maxTop || undefined}
            onDragValue={setG} onRelease={preview}
            zoom={1} pxc={pxc} />
        </div>
        <div className="ask-credit-side">
          <div className="dim">drag the bar — grant less, more, or claw back
            unused credits (floor: {committed} committed)</div>
          {warns.map((w) => (
            <div key={w} className="ask-warn"><WarnIcon fontSize="inherit" /> {w}</div>
          ))}
          <div className="row">
            <button className="primary" disabled={busy}
              onClick={() => decide('approve', g)}>
              {busy ? 'sending…'
                : g === askedG ? `grant ${g} (as asked)`
                : g === oldG ? 'decline the increase'
                : g < oldG ? `reduce to ${g}` : `grant ${g}`}
            </button>
            <button disabled={busy} onClick={() => decide('deny')}>deny</button>
          </div>
        </div>
      </div>
    </div>
  )
}

/** the nulled card: it KEEPS rendering, wearing why it nulled — grey for
 *  answered/denied, orange for interrupted (the agent was woken first and
 *  must re-ask). Non-interactive by design. */
function NulledAsk({ ask, credit }: { ask: AskInfo; credit: boolean }) {
  const interrupted = ask.status === 'interrupted'
  const label = interrupted ? 'interrupted'
    : ask.status === 'denied' ? 'denied' : 'answered'
  return (
    <div className={'askcard nulled ' + (interrupted ? 'orange' : 'grey')}>
      <div className="ask-head">
        <b>{ask.node}</b> {credit
          ? <>asked for credits: {ask.old} → {ask.new}</>
          : <>asked</>}
        <span className={'ask-null-tag' + (interrupted ? ' warn' : '')}>{label}</span>
      </div>
      {!credit && <div className="ask-q">{ask.question}</div>}
      {ask.answer && (
        <div className="ask-q dim">
          → {[...(ask.answer.selected ?? []),
              ...(ask.answer.text ? [ask.answer.text] : [])].join(' · ')}
        </div>
      )}
      {credit && ask.granted != null && ask.status !== 'denied' && (
        <div className="ask-q dim">→ granted {ask.granted}</div>
      )}
      {interrupted && (
        <div className="ask-null-why">
          {ask.reason || 'the agent was woken by other input before an answer arrived'}
          {' — it must re-ask'}
        </div>
      )}
    </div>
  )
}
