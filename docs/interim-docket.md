# Interim docket — every problem, every fix, every commit

Branch `interim-authority`. Kept by the secondary session (4f69f83a) while holding the implementer
seat during 8385c4e9's fable outage, on the user's instruction:

> make sure you're recording every single fix we do in this secondary branch in a docket, both my
> stated problems and your discovered solutions
>
> it should be a glossary of every noticed problem and every fix submitted for each one, alongside
> the commit the fixes are each attributed to

**User reports are quoted verbatim** (recovered from the session transcript, including the
mid-turn messages the compaction summaries paraphrased). Entries marked ⟨discovered⟩ were found by
me, not reported. Entries marked ⚠ are corrections of my own errors.

`main` was force-reset to `fb427e9` on 2026-08-02 at the user's instruction; everything below lives
only on this branch and awaits the implementer's review.

---

## D-01 · Mail archive ordered by click, not by send time
> *(from `docs/todos.md`)*

`/inbox/read` did `log.extend(read)`, appending in the order the user happened to CLICK, and the
reader renders by list position — so a mail read second outranked one sent later.
**Fix:** backend sorts `user_mail_log` by `at`; `MailList` sorts too rather than trusting position,
which repairs archives already written scrambled. Node inboxes were never affected.
**Proven to discriminate:** with the sort disabled the archive returns `['m3','m1','m2']`.
→ `215c0e6`

## D-02 · PowerShell missing from the terminal switch
> also add cmd as a tool to the agents just in case they want to run it.

Allowlisted beside Bash when the terminal is on, and denied with it when off (omitting it from the
deny list would hand a "no terminal" agent a shell). Probed the pinned CLI: it reports exactly
"Bash, PowerShell" — **there is no cmd tool**, so cmd is reached as `cmd /c …` and `identity_prompt`
now says so. Inert in Linux sandboxes, so the prompt offers Bash only there.
→ `215c0e6`

## D-03 · Coordinator retired agents for finishing
Charter clause 6 inverted per the user: finished hires STAY; retire only to reclaim capacity, taking
the idle agent whose thread is least likely to reopen.
→ `215c0e6`

## D-04 · `[ORG NOTICES]` rendered as raw chrome in the bubble
> if the objects are called notices, then call them notices in the card. whatever is most appropriate.

`stripEnvelope` never handled the notices block. Split into a collapsed "n notices" card reusing the
ThoughtLine collapse shape.
→ `215c0e6`

## D-05 · The denials banner duplicated an event and sorted it wrongly
> the denials banner is the thing that my attention flagged, correct. if there's anything else, give
> it the same treatment.

Verified in a live transcript that a headless auto-deny already writes a `tool_result` with
`is_error`, which `read_chat` renders as an errored ToolChip at the point it happened. The banner
restated it AND pinned a past event below undelivered mail. **Deleted, not moved** — with its
dismissal state. ⟨discovered⟩ `last_error` had the mirror defect (hoisted above the whole
transcript); it now renders at the end of the stream.
→ `215c0e6`

## D-06 · Sticky-bottom "gets left behind"
> small issue: the scroll doesnt always stick to the bottom of the list when scrolled to the bottom,
> and gets left behind. it should be sticky, but not too sticky, in case the user wants to scroll
> back up to read earlier events without being repeatedly pulled back down.

The pin was a bare `requestAnimationFrame` scheduled from an event handler, so it could fire BEFORE
React committed the new rows and read the OLD `scrollHeight`. Now a `useLayoutEffect` (measures
post-commit). Five scattered `if (stick) toBottom()` sites collapsed into that one effect.
→ `1b65b00`

## D-07 · No way back to the bottom once scrolled up
> and display a "jump to bottom" button that appears once the "non-stickyness" threshold has been passed

`position: sticky` inside the scroller (not a wrapper — the desk flex chain is documented-fragile),
appearing past a 40px threshold.
→ `1b65b00`

## D-08 · Desk text too small on some screens
> also i need a font / dpi size option for the desk view, it can be very small on some screens

`--desk-dpi` shrinks the authored coordinate space and scales up by the same factor, so the panel
still lands on exactly 120px of card (`900/d × 0.13333d ≡ 120`). Live-verified: at 1.5× the
on-screen box is identical (790×790) with authored width 900→600. localStorage, never the org doc.
Collapsed the duplicated scale constant into `shared.ts`.
→ `1b65b00`

## D-09 · Restart replays the user's prompt back at them
> also, when orgtree is restarted while a node is working, the user prompt is restated to them. under
> the hood this is fine, but its also printed back to the user. this is redundant…
>
> though, is restating the user prompt necessary? … maybe just a "continue where you left off" is
> sufficient.

Display folded to a one-line marker. **The re-delivery itself is deliberately UNTOUCHED**: if
delivery was confirmed the resumed transcript already has it, and if not the journal folds it back —
but a CLI that read stdin and died before flushing its transcript is covered by neither, so dropping
the replay would trade D-045's "worst case a duplicate, never a loss" for a rare loss.
→ `1b65b00`

## D-10 · Long chats and mail lists will lag the UI
> not an issue yet, but i suspect with long-running usage, the ui in long chat logs and mail lists
> will start to lag the ui out. keep only the most recent n chat events and mails loaded in memory,
> and load more lazily as the user scrolls to them.

Transcript windowed to the newest 120 with a pager to the API's 1000 cap; mail lists windowed to 40.
`seq` was already the PRE-slice ordinal, so `messages[0].seq > 0` is an exact "older exists" test.
Scroll anchoring uses distance-from-bottom (invariant under prepend). The mail filter deliberately
runs over the WHOLE set before the window.
→ `d95dad7`

## D-11 · `done` and `idle` are not functionally distinct
> also there is no real functional distinction between the `done` and `idle` state. the `done`
> notification can still be send to superiors, but that really should just send it to the idle state
> afterwards.

`done` now stores as `idle` carrying its summary; the DONE report still reaches the superior.
`blocked` deliberately NOT collapsed — it means "stuck, needs a human", which idle does not.
→ `d95dad7`

## D-12 · New hires are stateless
> also, new agents should be hired into the idle state as well, as opposed to being stateless

`_new_node` stamps `last_status: idle · "hired — awaiting work"`.
→ `d95dad7`

## D-13 · Switchboard chat stops updating
> issue: sometimes the switchboard chat render of an agent stops updating, even though that chat
> demonstrably is producing more events.

`pulse` and `streamEvt` were ONE shared React state slot each. Two events in the same React batch
collapse to the last, and because a desk only refetches on a pulse addressed to IT, **a clobbered
pulse meant that desk never refreshed again** — permanently, since there was no fallback poll. Fixed
with per-node slots written via functional updates.
**⚠ This fix was incomplete — see D-24.**
→ `22cc281`

## D-14 · Wide tables stretch the whole chat
> also when rendering tables, sometimes the whole chat window is stretched too wide and scrolls
> horizontally. instead just the event container itself should be horizontally scrollable when the
> content is too wide.

`display:block; width:max-content; overflow-x:auto` on `.md table`, plus `min-width: 0` on `.msg` —
the half that actually matters, since a flex column otherwise refuses to shrink below intrinsic
content width and the overflow escapes upward.
→ `22cc281`

## D-15 · Tables in mail
> also it seems tables dont properly render in mail? or maybe the agent opts just not to write tables
> into mails

⟨investigated, no defect⟩ `marked` with the app's exact options emits a `<table>` with or without a
blank line before it, identically for mail and chat. Absent tables are agent behaviour or the
overflow of D-14, not the renderer. Same containment applied to the mail pane.
→ `22cc281`

## D-16 · A superior does not know a direct order carries user authority
> if i send a direct order to a subordinate of an agent, does that agent's superior know immediately
> that the instruction came from me and carries my authority? or is that something that hasnt been
> resolved yet?
>
> go ahead and build it, and include every direct message; requiring me to manually mark a message as
> authoritative is costly to my time…

⚠ **I first told the user this was unimplemented. It was not.** `chain_notices` (the reserved
org-doc key) is dead code written nowhere, and grepping for it misled me; the live implementation is
`ledger.user_deep_reach()`, firing for every direct message all along. The REAL gap was narrower: the
notice said only *"The user spoke directly to X"* while the recipient was simultaneously told that
user instructions outrank its chain — the two sides disagreed about what had happened. The notice now
names the authority, says it outranks anything the superior told that node, and says to re-check
dependent plans. Gist cap 80→160.
→ `b9c8b86`

## D-17 · ⚠ A commit landed on a red test suite
⟨discovered — my own error⟩ `b9c8b86` was committed while `test_ledger.py` was failing: my
verification piped into `tail`, so the pipeline returned tail's exit code and hid it. A test pinned
the literal old notice wording. Re-pinned on what the notice must MEAN rather than how it is phrased.
**House note: read exit codes directly, never through a pipe.**
→ `bfc153b`

## D-18 · The `delivering` tag never goes away
> when i send a message to a chat in the switchboard view, the `delivering` tag doesnt go away

The composer's send receipt (`sendMode`) named which door a send went through, and had exactly ONE
clear — the textarea's `onChange` — so it only vanished if you typed again. It now expires after 6s,
and a new send clears the previous receipt before issuing its own.
⟨checked first⟩ `delivering` is also a real org-doc key (the delivery journal) and a stuck batch
there would print the same word on a bubble — it is not stuck; every live org doc has it empty and
the confirm/fold-back paths close over all four exits.
→ `5c16f0f`

## D-19 · The composer stays tall after sending
> when i type a long message into the send box and hit send, the height of the box stays tall until
> the chat is defocused and refocused again

`grow()` set an inline height from `scrollHeight` and its only caller was `onChange` — so the height
was recomputed by KEYSTROKES and nothing else, while three paths change the value: `send()` (the
reported bug), mount (a restored draft opened at two rows however long it was), and the slash-hint
picker. A layout effect on `text` now owns it, covering all three by construction.
Measured 32px → 49px typed → 32px after send → 49px for a draft restored across a reload; that last
number is the discriminating one, since no keystroke happens after a remount.
→ `536bdfb`

## D-20 · Thinking is completely hidden
> also i dont see any thinking anymore at all, it appears completely hidden

Not an orgtree regression: the API sends thinking blocks as `{"signature": …, "thinking": ""}`.
What WAS ours: `read_chat` dropped those blocks, and **every one sits alone in its record** (19/19 in
one transcript, 41/41 in another), so the whole row vanished and the agent looked like it had stopped
thinking rather than like its reasoning was withheld. Now renders `thought for 12s` as a plain
marker, no expander.
**⚠ Corrected in D-23.**
→ `7ad36b6`

## D-21 · The effort level is not shown until manually configured
> also the current effort level of agents isnt shown before it's been selected manually

Attempt 1: the control read `node.scope.effort`, only half the answer — an unset node inherits the
org's `default_effort` live at turn time. Resolution moved into `Org.effective_effort`, which
`_build_cmd` also uses.
**⚠ Insufficient — see D-27 and D-31.**
→ `7ad36b6`

## D-22 · A hire does not start the agent
> in testing on another system, i found that an agent hiring subagents sometimes doesnt actually kick
> them off, assuming that the hire + charter is enough to start them going. it needs to be made
> explicit that agents need a mail sent to them in order to start them working.

Correct, and neither hire path (agent or user) drives the new node — the charter is identity, mail is
what runs a turn. Stated in three places weighted by when they are read: the hire RESULT
(`next_step`), the tool description, and `identity_prompt`; plus coordinator charter clause 3, now
"A hire is TWO calls, never one."
→ `7ad36b6`

## D-23 · ⚠ CORRECTION — thinking is sealed per TIER, not universally
⟨discovered — my own error⟩ I told the user the API withholds thinking plaintext from *every* model.
Wrong. Census, no exceptions:

| tier   | with text | sealed |
|--------|----------:|-------:|
| haiku  |         6 |      0 |
| sonnet |         0 |     46 |
| opus   |         0 |     19 |

The error: a sonnet probe returned NO thinking blocks for a trivial prompt and I folded that absence
into the conclusion instead of treating it as no data. Both render paths are needed and correct; only
the explanation was too broad. **Undated** — the oldest transcript here is already sealed, so "it
changed recently" has no evidence either way.
→ recorded in `b403eb2`

## D-24 · Responses do not appear until the first tool call
> also agent responses still dont appear until after the first tool call also appears, when looking at
> the switchboard

⟨not reproduced⟩ Two live runs: one agent (text 2.3s BEFORE the tool) and three streaming
concurrently (10–11 delta frames each, none dropped, prose before tools in all three panels). What
the runs DID show is a 6.4s window where the panel says "working" and shows nothing — and D-23
explains why that got worse: the gap used to be filled by the thinking ribbon, which on opus and
sonnet now has nothing to stream. Fixed as D-26.
→ analysis in `b403eb2`

## D-25 · The whole project feels like whack-a-mole
> this whole project is beginning to become a rug-pushing exercise: fix one bug and two other old ones
> pop back up, back and forth. gives me the feeling that some architectural decision is standing in
> the way of things working as intended.
>
> might need an investigation into that
>
> try to prioritize storing less state and deriving it more. this … reduces performance but reduces
> stale state issues that we keep seeing.

Investigated and confirmed. One decision in three places — state the server owns is copied elsewhere,
and every bug is the copy diverging: ① the desk's parallel conversation model reconciled by 300-char
string prefix plus a 5s expiry that fires whether or not the transcript caught up; ② 25 `useState`
cells seeded from server data that never re-sync; ③ `busy` with 12 in-memory references and 0 in the
ledger. Written up with counts, measurement method and five proposals in
[`docs/state-architecture-review.md`](state-architecture-review.md). The doc also records that three
of eight fixes to that point landed at the point of divergence rather than the source, and that
several ADDED state cells.
→ `b403eb2`

## D-26 · No indication while sealed thinking runs
> even when thinking blocks are witheld, it should still show "thinking... for n seconds" while
> encrypted thinking is occurring, which currently it does not.

The marker is `content_block_start` on the thinking block, **not** `thinking_delta` — a sealed
think's deltas carry no text and can all arrive after it finishes, so a client waiting for them would
start its clock as the think stopped. Measured on a sonnet agent: the clock appeared and counted
1s → 22s across 23 distinct values, then folded to `thought for 27s`, over what was previously a
blank panel.
→ `e5c5e52`

## D-27 · ⚠ The effort tag still shows nothing
> the effort appears the same; it just says "effort", rather than the actual effort level the agent
> will use when responding

Second attempt. I had treated "nothing configured" as "no effort", but orgtree passing no `--effort`
flag does not mean no effort — the CLI picks its own. It IS recorded in the transcript
(arti/helper: 54 records, all `high`), so `read_chat` surfaced `effort_used` as a fallback.
**⚠ Still insufficient — see D-31.**
→ `e36eeb8`

## D-28 · The user's message renders twice during a long think
⟨discovered — caught in a verification screenshot, never reported⟩ From turn start until the delivery
journal confirms, the same message is on screen twice: as the transcript's `@user` envelope and as a
pending bubble tagged *delivering mid-task…*. Up to ~10s. A textbook instance of finding ①.
**Later reported by the user independently** (D-30). **Status: OPEN.**

## D-29 · ~6s of blank panel before thinking starts
⟨discovered⟩ CLI startup, hooks and `init` occupy roughly six seconds before the thinking block
opens; the panel shows only the spinner. The D-26 clock cannot cover it because nothing has begun.
**Status: OPEN, recorded in the review.**

## D-30 · Switchboard still out of sync; message still appears twice
> im still observing the switchboard desk going out of sync with the individual agent desks, so
> whatever fix was posted before is not working properly. i noticed the message appearing twice as
> well. i think we should work through the state duplication issues in tandem and try to fix all of
> these problems at once.

D-13 fixed which desk got an event, not the fact that **each desk keeps its own conversation model**.
A node is rendered by up to two DeskChat instances (its card and its switchboard panel), each with
its own fetch, live rows, draft/thinking buffers and busy-gated poller — two independent models of
one conversation diverge by construction.
**In progress:** `frontend/src/convo.ts` — one store per node, outside React, subscribed by both
views via `useSyncExternalStore`; one fetch, one poller, one reconciliation, N views. Also carries a
self-heal poll so the UI never depends on having caught every websocket event.
**Status: IN PROGRESS.**

## D-31 · ⚠ The effort tag STILL shows nothing (third report)
> also the effort tag *still* doesnt show the effort level for an agent when it hasnt been manually
> configured. what are you doing? ive asked for this twice now

Both earlier attempts read something that CORRELATED with the effort rather than the thing that
CAUSED it, and I verified each on the one agent where the correlation held. D-27's transcript field
is written by the CLI for opus and **not** for haiku — so it answered on `arti/helper` and shrugged
on every sonnet and haiku agent, which is what game-club actually runs.

Root cause: with no flag passed, the level was the CLI's own default, and that default is
undocumented (`--help` names none) and unreported (`system/init` carries no effort field — both
checked). orgtree was delegating a decision it could not observe, then trying to display it.
**Fix:** stop delegating. `Org.DEFAULT_EFFORT = "high"` and `--effort` is passed on **every** turn, so
the displayed value is the same call that builds the flag — true by construction. "high" is what opus
resolved to unaided across 54 records, so behaviour is pinned, not changed; every tier accepts the
flag (haiku and sonnet, high and low, all exit 0). `effort_used` deleted rather than kept as a
fallback. Verified on the agents that were broken rather than the one that worked: game-master
(sonnet), hex-red (sonnet), coordinator (opus) all read `high`.
→ `cb29f59`

---

## Carried, not done

| item | state |
|------|-------|
| D-28 message rendered twice | OPEN — fix planned with D-30 |
| D-29 ~6s blank before thinking | OPEN — recorded in the review |
| D-30 shared conversation store | IN PROGRESS |
| `chain_notices` dead reserved key shadowing `user_deep_reach()` | should be deleted or wired; it has already misled one session |
| `_move` inflates a top-level grant past `max_top_grant` | confirmed in the shelved ledger review, unfixed |
| `game-master` has an empty charter although `hire` now refuses without one | predates the requirement |
| P1–P4 of the state review | await a ruling |
| mobile wave | spec at `docs/mobile-spec.md`, held by the user |
