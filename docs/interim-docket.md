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
**Later reported by the user independently** (D-30).
**Fix:** the delivery journal now records HOW its text travels. A `via="turn"` batch is written to
the CLI as a user event, so the transcript carries it and the chat renders it there — those are no
longer surfaced as pending. A `via="steer"` batch rides hook context, which the CLI never
transcripts, so the journal stays its only possible display. Durability is untouched either way;
this governs display only. Old entries default to `"steer"`, because showing a duplicate is the
failure this system already prefers over hiding a message.
→ `12ccfd5`

## D-29 · ~6s of blank panel before thinking starts
⟨discovered⟩ CLI startup, hooks and `init` occupy roughly six seconds before the thinking block
opens; the panel showed only the spinner in the chrome. The D-26 clock cannot cover it because
nothing has begun yet.
**Fix:** a `starting…` line, **derived, not stored** — busy, with nothing live, nothing thinking,
nothing drafted and nothing pending, *is* starting. No new event, no new state cell.
Verified live: seen during the launch window of a real turn.
→ `ded9f1a`

## D-30 · Switchboard still out of sync; message still appears twice
> im still observing the switchboard desk going out of sync with the individual agent desks, so
> whatever fix was posted before is not working properly. i noticed the message appearing twice as
> well. i think we should work through the state duplication issues in tandem and try to fix all of
> these problems at once.

D-13 fixed which desk got an event, not the fact that **each desk keeps its own conversation model**.
A node is rendered by up to two DeskChat instances (its card and its switchboard panel), each with
its own fetch, live rows, draft/thinking buffers and busy-gated poller — two independent models of
one conversation diverge by construction.
**Fix:** `frontend/src/convo.ts` — one conversation store per node, outside React, subscribed by
every view via `useSyncExternalStore`. The desk gave up seven local state cells and two refs
(`chat`, `pending`, `live_feed`, `draft`, `thinking`, `thinkSecs`, `win`, `loadingOlder`, `thinkBuf`,
`thinkT0`) plus its own copies of the fetch, the event ingestion, the live/durable reconciliation and
the busy poller. Ingestion now happens once at the websocket, not once per mounted view. Carries a
self-heal poll while a node is busy, so the UI never depends on having caught every event — the
assumption that let one missed pulse stall a desk permanently. Tool rows reconcile by the CLI's
`tool_use_id` rather than by comparing rendered strings, and a live row may only age out after a
fetch has actually had the chance to cover it (the 5s timer used to race the transcript write).
**Verified live** on a sonnet agent: two views through a whole turn, 39 samples compared, **0
divergent**; and across the user's real motion — switchboard mid-turn, then focus the agent's card —
the new view mounted showing all 9 rows immediately, with no empty flash and nothing lost.
→ `12ccfd5`

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

## D-32 · The state-duplication programme (P2, P3, P4)
> implement P2-P4, D-29, and remove chain_notices. we'll leave D-24 for now, if i can find a more
> consistent reproduction ill let you know (or hopefully the state refactor ties that issue up
> coincidentally)

The three remaining proposals from [`docs/state-architecture-review.md`](state-architecture-review.md),
under the standing direction *"prioritize storing less state and deriving it more"*.

**P2 — the server owns the live tail.** `supervisor.live_row()` records every row a view must still
see after the moment passes (assistant text, tool calls, folded thoughts, sticky command output) in
`state()["live"]`, and `_sweep_live()` retires each one when the transcript catches up. `read_chat`
returns the survivors as `chat.live`. The client no longer builds a conversation at all: the whole
client-side reconciliation is deleted, along with the 300-character prefix matching and the
5-second expiry that raced the transcript write. A tool row now retires on the CLI's own
`tool_use_id`; nothing is dropped on a clock. Thought-folding moved server-side too — the server
sees the thinking block open AND what followed, which the client could only infer. What remains
client-side is the sub-second scaffolding that would be stale before any fetch returned: the
token-by-token draft and the thinking clock. Websocket events now schedule a 200 ms-debounced
refetch instead of assembling rows.

**P3 — 25 prop mirrors become derived.** `useState(tree.x)` snapshots once at mount and never looks
again; the org settings panel held 17 such copies, the node ⚙ panel 7, the hire-defaults panel 3.
Each is now ONE edit buffer holding only what has actually been typed, with every value derived from
the prop each render (`k in edit ? edit[k] : server`) and the buffer cleared on save. Shadowing
`const`s keep every use site unchanged. ⚠ `DraftScopeModal` was deliberately LEFT as snapshots and
documented in place: it stages permissions for an agent that does not exist yet, so its `base` is a
one-time proposal, not a live server value — re-deriving mid-edit would overwrite staged choices.

**P4 — one home for node runtime data.** `occupancy`, `context_window` and `last_status` lived in
BOTH the in-memory state dict and the org doc. `last_status` had already rotted to zero readers.
`api.annotate` re-read the in-memory copies believing they were fresher — they were not:
`_after_turn` wrote both in the same block, so the mirror could only agree or be stale. The doc is
now the only home; `state()` keeps just what is genuinely process-bound.

**Verified live** on a sonnet agent in a throwaway org: `starting…` seen during the launch window,
the server live tail peaked at 2 rows and drained to **0** after the turn (so rows are both created
and retired by the sweep), and two views compared across 27 samples showed **0 divergent**. The org
settings panel reads its real values (1000 / 50 / 80) and the node ⚙ panel renders fully populated
from the node it was opened on.
→ `ded9f1a`

## D-33 · `chain_notices` removed
The reserved org-doc key that was written nowhere and read nowhere, while shadowing the working
`ledger.user_deep_reach()`. It convinced one session (mine) that chain notices were unimplemented.
Gone from `ledger._new_doc`, `schema.OrgDoc` and the types comment.
→ `ded9f1a`

## D-34 · Desks go stale until zoomed out or reloaded — THE UNDERLYING CAUSE
> switchboard *and* individual agent desk view state still sometimes seems to go out of sync with
> the ground truth; it doesnt update until all desk views are zoomed out or the page is refreshed.
> there's got to be a more fundamental underlying cause this keeps happening.

There was, and it is worth stating plainly because it had already produced three separate reports
(D-13, D-30, this one) that each looked like a different bug:

> **Liveness was gated on state that could itself be stale.**

The refresh loop was `pollWhileBusy(slug, nid, !!chat?.busy)` — it polled only while the payload
said the node was busy, and `busy` **arrives in the payload the poll fetches**. That is a bootstrap
trap: open a desk whose last payload said `busy:false` while the node was in fact working — a turn
that began during a websocket gap, an event that never arrived, a view mounted at the wrong moment
— and the poll never starts, so nothing can ever correct the belief that stopped it from starting.
The view sits frozen until it is unmounted (zoom out) or the page is reloaded, which is exactly the
report, word for word.

Every earlier fix had improved the *push* paths — per-node event routing (D-13), then one shared
store (D-30) — while leaving the *pull* path conditional on the very thing the pull repairs.

**Fix:** liveness is driven by SUBSCRIPTION, not by payload state. `convo.beat()` polls any node
that has at least one mounted view — a fact known locally that cannot be stale. Cadence still adapts
(2.5 s while the payload says busy, 7 s otherwise) but the difference is only *how often*, never
*whether*.

Two accomplices removed in the same pass:
- **The dead `streams` prop chain.** P2 stopped calling `setStreams`, so `streamEvt` was permanently
  `null` — yet it was still threaded App → OrgCanvas → UserNode/EyeDesk/NodeSquare → DeskChat and
  still compared in DeskChat's memo. A prop that *looks* like the update path but can never fire is
  precisely what made this hard to see. Deleted end to end.
- **`resetConvos()` could orphan subscribers.** It called `M.clear()`, but a mounted view has
  already handed its re-render callback to a *specific* Entry object; discarding the map leaves that
  callback attached to an orphan and every later notify goes to a set nobody is listening to — a
  permanently deaf view with exactly these symptoms. It now resets entries **in place**. Entry
  identity is load-bearing, and the comment says so.

**Verified live.** Idle desk, the state that used to wedge: **4 chat fetches in 25 s** where the old
code fetched once and waited forever. Heartbeat follows the view — focus `hex-red` → 3 fetches for
it and 0 for `hex-blue`; switch focus → 0 and 4.
⚠ **Method note, because it nearly produced a false negative:** the first measurement showed *zero*
fetches and looked like a total failure. Python's `time.sleep` blocks Playwright's event loop, so
request events were never dispatched — the probe was broken, not the code. In-page logging showed
the heartbeat ticking normally. Use `page.wait_for_timeout`, never `time.sleep`, when a probe is
counting events.
→ `47ebd9c`

---

## D-35 · The tree payload had no heartbeat (G1) — the other half of D-34
> proceed  [on the §8 audit's recommended order: G1 then G2]

D-34 gave the CONVERSATION a heartbeat. The audit in `state-architecture-review.md` §8 found the
same defect untouched on everything else: `refreshTree` fired only on a websocket frame, on socket
connect, or in the acting client's own mutation callback. Measured — the only intervals in
`App.tsx` were `refreshOrgs` every 3 s (org list, and only while the welcome/drawer is up) and a
15 s clock re-render that fetches nothing. Every card, credit meter, occupancy bar, roster row,
resume timer and inbox badge is drawn from that one push-only payload.

**Fix:** a 6 s `setInterval` gated on `slug` — "an org view is mounted", known locally, cannot be
stale. Pushes stay; they now make it feel instant rather than being the only way to learn.
Measured cost first: the tree is **~4 KB and answers in 2–12 ms**, so the pull is not worth counting.

**Verified live** (`zz-g2-probe`, created and deleted): org view open, nothing happening —
**3 tree fetches in 20 s at 4.5 / 10.5 / 16.5 s**. Before: zero.
→ `524f353`

---

## D-36 · 14 of 30 mutating endpoints never told anyone (G2)
> [same directive]

Measured by parsing `api.py`: every route calling `save_org` checked for a `hub_changed`. Fourteen
had none — including `/nodes/{nid}/scope` (the effort/permission surface reported twice),
`/audiences`, `/inbox/read`, `/nodes/{nid}/message` and mail retraction. The acting client refetches
in its own `.then()`, which is exactly why this never showed up in single-tab testing: only a
SECOND view (another tab, the kiosk, the switchboard beside a desk) ever saw the stale copy.

**Fix — structural, not fourteen edits.** Fixing them by hand would have rebuilt the very shape this
programme exists to remove: N writers each responsible for remembering the same side effect. The
save *is* the change, so `store.save_org` announces it (`store.on_save`, wired to the hub at
startup). A new endpoint cannot forget. The fanout is wrapped in a `try` — a broadcast failure must
never fail a write that already reached disk.

Because it now fires per SAVE rather than per endpoint, it **coalesces**: the first save opens a
0.4 s window and every save inside it rides the same broadcast.

**Verified live:**
- a scope edit made by a *different HTTP client* → the browser receives `changed` frames
  (0.01 s and 0.41 s). Before the change that endpoint produced **none**.
- `/inbox/read`, also formerly silent → 1 frame.
- **Load, the only real risk:** one real haiku turn, 13 s, 26 stream events → **8 tree refetches
  = 0.6/s** (~2 KB/s at 4 KB a payload). A turn saves the doc far more often than 8 times, so the
  coalescing window is doing its job under the load that matters. Serialized saves further apart
  than 0.4 s each get their own broadcast, which is correct: the window is a ceiling, not a promise.

⚠ **Method note.** The first probe timed *the browser's next tree GET* after a mutation and reported
0.01 s — faster than the 0.4 s coalesce window, i.e. a coincidental heartbeat, not the broadcast.
Re-measured against the `changed` FRAME itself, which the timer cannot fake. Timing a proxy for the
thing instead of the thing was also what nearly sank D-34.
→ `524f353`

---

## D-37 · The remaining state gaps (G4/G5/G6) — deletions, not additions
> proceed

Three of the §8 gaps, done together because they are one idea: **client state that mirrors, or
outlives, something the server already owns.**

**G4 — `activity` and `pulses` deleted.** `activity` was a `Record<node, {phase,tool}>` accumulated
from websocket frames and cleared on `turn_done`; a missed `turn_done` stranded an indicator until
the socket reconnected. It is a **tree-payload field** now, derived server-side in `annotate()` from
the live tail the supervisor already keeps — stored nowhere, recomputed per request. `pulses` was a
per-node record of the last turn event threaded App → OrgCanvas → EyeDesk/NodeSquare → DeskChat;
after the node inbox stopped keying off it, **nothing read it** — `DeskChat` still destructured it
and its memo still compared it, exactly like the dead `streams` chain D-34 removed. A prop that
looks like an update path but can never fire is the thing that makes staleness hard to see. Deleted
end to end.

**G5 — `usePolled`, one hook for the panels.** 18 of 19 read-endpoint call sites fetched once on
mount. The node inbox had a workaround — refetch when a `pulse` prop changed — which covered turn
events and nothing else, so **the one panel whose whole job is showing mail was the one that never
learned when mail arrived.** Converted: node inbox, user inbox, audiences, the org record, agent
history, agent scratch files. Same gate as everywhere else — "is this panel mounted".
`DiskBrowser` deliberately NOT converted: it is a triage tool with a selection model, and surprise
re-renders there could re-target a delete.
The retract path kept one local cell — mails just retracted, held until the server agrees. That is
an **uncommitted operation**, not a mirror, and the distinction is the whole point.

**G6 — id-keyed `localStorage` is swept.** `orgtree-eyemin-`, `-eyeseen-` and `-pile-` all key on
node ids and none was ever pruned (only `-draft-` was). They grew across the org's entire hire/fire
history, and a pile front could name a node that no longer exists. Client-owned state is legitimate;
client-owned state that outlives what it refers to is just a slower kind of stale.

**Verified live** (`zz-drop-probe`, created and deleted):
- G6: planted `['probe','long-gone-agent','another-ghost']` + two bogus pile entries before boot →
  after the sweep, `['probe']` and `{}`. Dead ids pruned, the live one kept.
- G5: node inbox open and idle → **3 refetches in 16 s**; mail sent while it was open appeared in
  **3.5 s**. Both were previously never.
→ `2defb56`

---

## D-38 · THE FRAME-DROP TEST — the UI no longer needs the websocket at all
> [the investigation's own blind spot, closed]

Every bug here so far was found by a symptom and then explained. The §8 census finds state that is
duplicated **by shape**; it is blind to a single copy, correctly owned, that is simply **old** —
which is what D-34 and G1 both turned out to be. So this test looks for that class directly, by
taking the push channel away entirely and asking whether the UI still converges on the ledger.

**Method.** The page's `WebSocket` is replaced before any app code runs with a subclass that
connects, stays open — so no reconnect fires and no reconnect-time refetch papers over the result —
and **swallows every frame**. `api.ts` does `ws.onmessage = handler`, and the subclass drops that
setter (the `addEventListener` path was instrumented too and stayed at 0, proving which one is
used). Then a real turn runs, driven over HTTP by a separate client.

**Result — 5/5 converged with a deaf websocket:**

| check | result |
|---|---|
| a hire by another client reaches the canvas | PASS |
| every ledger node is on the canvas | PASS |
| the UI notices the turn STARTED | PASS — busy dot in 0.5 s |
| the server-derived activity label renders | PASS — 0.5 s |
| the busy indicator CLEARS | PASS — 10.6 s |
| the agent's reply is on screen | PASS — 12.9 s |

**Proven to discriminate.** The same probe against the pre-G1 build (`117f7ca`'s frontend, rebuilt
and re-run) **fails exactly the two checks G1+G4 address** — the busy dot and the activity label
never appear at all. The other three passed there for reasons that do not generalise: the reply
arrives via the D-34 conversation heartbeat, `busy` "clears" trivially because it never showed, and
the node list happened to be present at page load.

⚠ **Honest limits.** This tests convergence with the socket *fully* dead, which is the strong case;
it does not test a socket that is alive but dropping a fraction of frames, nor a backend restart
mid-turn. And "every ledger node is on the canvas" is the weakest assertion in the set — the node it
checked for pre-existed the run.

**What it means:** the websocket is now an **optimization**, not a requirement. Nothing on screen
depends on having caught an event, which is the property every one of D-13, D-24, D-30 and D-34
turned out to be missing.
→ `2defb56`

---

## D-39 · `--expose-admin` — a command-line-only way off loopback
> need a commandline option to expose the main port to the outside internet. dangerous obviously,
> which is why its commandline-only

The admin listener has always been loopback-only by design, and the reason it has **no
authentication of any kind** is that "you can reach 127.0.0.1" *was* the credential. Exposing it
therefore hands whoever finds the port the owner's powers: every org, any folder on the machine
grantable to an agent, and turns that execute commands.

**Command-line only, and the shape matters.** Not an env var (inherited by child processes, copied
between machines) and not a setting (anything that can write the doc could flip it — **including an
agent**). An argv flag has to be typed by whoever starts the process, every time. Startup prints a
74-column wall, not a log line, naming exactly what was turned off.

`update.ps1` gains a matching `-ExposeAdmin` switch — the deploy script is how the backend actually
starts, so without it the flag would be unusable in practice — and prints the same warning in red
before launching.

**Verified live** on an isolated data root + port (the running instance untouched): default →
`127.0.0.1:7399`, with the flag → `0.0.0.0:7399`, banner printed. `update.ps1` re-parsed clean
under PowerShell 5.1.

☞ Not built, deliberately: any form of auth on that port. The user asked for the switch and
accepted the risk; a token gate would be a separate feature and a bigger decision.
→ `dc1f0cb`

## D-40 · kiosk and sandbox move into `advanced`
> the kiosk and sandbox features can be considered advanced, move them into the dropdown below the
> folder selection

Both are advanced choices — one publishes the org, the other changes where every turn executes —
and neither belonged in the two-field path most new orgs take. Moved inside the disclosure, **below
the folder grants** as asked, under an `org type` label with a rule separating them from the grants.
The collapsed form is now name + advanced + create.

⟨discovered⟩ One thing the move introduces: settings that are *on* can be folded out of sight, so a
kiosk could be created with the evidence hidden. The collapsed disclosure therefore summarises what
is set — `advanced · kiosk · sandboxed · 2 folders`.

**Verified live:** collapsed form carries 4 controls and no kiosk row; expanded shows kiosk +
sandbox under the folder list; **the form was actually submitted** — `zz-form-check` came back with
`kiosk=True, credits cap=7, sandboxed=True`, then was deleted. Screenshots reviewed.
→ `dc1f0cb`

## D-41 · ⚠ The npm 11 / esbuild report was a MISDIAGNOSIS
> another setup on my coworker's computer resulted in the agent responding with this after
> installing the latest version of node via nvm: I modified a tracked file. npm 11 blocks postinstall
> scripts by default, which left esbuild without its binary and would have broken the Vite build.
> npm approve-scripts esbuild fixed it but wrote an allowScripts block into frontend/package.json
> and dropped 10 lines from the lockfile. Both are uncommitted — keep them (helps anyone else on
> npm 11+) or revert with git checkout … Your call.

**Measured, not reasoned.** On npm 11.6.2 here:

| claim | measurement |
|---|---|
| npm 11 blocks postinstall by default | `ignore-scripts` = **false**; `npm approve-scripts` is **not a command** in 11.6.2 |
| blocked scripts leave esbuild without its binary | **esbuild works with `--ignore-scripts`** — fresh installs of **0.25.12** (our pin) and **0.28.1** both `transformSync` fine |

The binary arrives as an **optional per-platform package** (`@esbuild/win32-x64`), not a postinstall
download; modern esbuild dropped that years ago. `esbuild` still declares `hasInstallScript: true`
for a fallback path, which is almost certainly what the diagnosis keyed off — but the script is not
what installs the binary.

**Verdict: revert both files, do not keep them.** `allowScripts` fixes nothing, and the lockfile
edit is *actively dangerous* — our lock carries all **26** `@esbuild/*` platform entries, and a
rewrite that drops entries breaks the machines it was meant to help.

**The real cause is almost certainly npm's optional-dependency bug** (npm/cli#4828), which a fresh
nvm install invites; the `approve-scripts` run "fixed" it by triggering a reinstall.

**Durable fix, in `update.ps1`:** after `npm install`, actually *run* esbuild
(`node -e "require('esbuild').transformSync('let x=1')"`). If it fails, wipe `node_modules`,
reinstall, re-test, and only then give up with a pointed message about platform mismatch. Turns an
opaque build failure into a self-heal.
**Proven to discriminate:** healthy tree → exit 0; delete `node_modules/@esbuild` to simulate the
bug → exit **1**; clean reinstall → exit 0 again.
→ `dc1f0cb`

---

## D-42 · `update.sh` — a bash deploy script
> also i need a bash version of the update script

A step-for-step port of `update.ps1`: pull → npm install + esbuild self-heal → build → pip install →
report the CLI version → stop the old backend → start detached → health-check. Same
`--expose-admin` switch with the same red wall. Written for Linux/macOS; it also runs under Git Bash
on Windows, where the two things that cannot be POSIX — finding and killing whoever holds a TCP port
— fall back to `netstat` + `taskkill`. Port discovery tries `lsof` → `ss` → `fuser` and matches
**listeners only**, never a client connection, which would kill an innocent process.

**Three real defects found by running it rather than reading it:**

⚠ **`python3` exists here and does not work.** Windows ships an App Execution Alias at
`~/AppData/Local/Microsoft/WindowsApps/python3` that `command -v` finds happily, then prints
*"Python was not found"* and fails — while the real 3.10 sits right behind it as `python`. The first
draft picked the stub and died at `pip install`. Fixed by **running each candidate** rather than
checking that it exists (`python3` → `python` → `py`, first one that executes wins; `$PYTHON`
overrides and is validated too). Same lesson as everything else this week: test the thing, not a
proxy for it.

⚠ **A backgrounded child kept the pipe open under MSYS.** `./update.sh | tee log` hung forever
*after the script had finished all its work* — the backend restarted correctly and the pipeline
never closed. Reproduced in isolation: under Git Bash a backgrounded grandchild keeps the parent's
pipe alive regardless of its own redirections. `disown` does not fix it; `setsid` does not exist
there (and appeared to "pass" only because that branch started no child at all — a false positive
worth recording). The fix is to redirect **the subshell's** descriptors as well as the child's, and
it costs nothing on Linux/macOS.

⚠ **CRLF would have made the script unrunnable on Linux.** This repo is developed with
`core.autocrlf=true`, so every text file checks out CRLF — harmless for `.py`/`.ts`/`.md`, fatal for
a shell script (`
: command not found`). Added `.gitattributes` pinning `*.sh` to `eol=lf`, with
`*.ps1`/`*.cmd` pinned CRLF and `* text=auto` for the rest. Verified by deleting and re-checking-out
the file: **0 CR bytes**, executable bit intact, still parses. `git add --renormalize .` touches
nothing else, so the rule is not a disguised mass rewrite.

**Verified live, end to end and piped:** a full `bash update.sh` run pulled, rebuilt the UI,
installed deps with the correct interpreter, reported the CLI version, stopped the old backend by
pid, started a new one and passed its health check — exit 0. The flag path was checked without ever
exposing the live instance, by standing a recorder in for python: without the switch the recorded
argv is `-m orgtree.api`, with it `-m orgtree.api --expose-admin`, banner printed. The health check
also proved it discriminates — with the fake interpreter serving nothing, the script correctly
failed with a pointer to the error log instead of claiming success.

README now documents both scripts and the switch.
→ `59090a2`

---

## D-43 · Effort control lags 3-5 s before it updates visually
> there's a lag in the ui of around 3-5 seconds when i change the effort level on an agent before
> it updates visually

**⚠ Not reproduced, and the fix is deliberately cause-independent.** What was measured here:

| suspected cause | measurement | verdict |
|---|---|---|
| the desk chip round-trip | click → button text: **0.10 s** | not it |
| the scope endpoint | POST `/scope`: **3-5 ms** idle, **2-18 ms** during a live turn | not it |
| big org doc (`arti` is 177 KB vs 13-30 KB) | bloated a probe org to 182 KB: **11-22 ms** | not it |
| read/write contention (`save_org` retries `os.replace` up to 2.1 s on Windows when a reader holds the file, and G1/G2 added readers) | 0 / 3 / 8 concurrent readers: **max 22 ms** | not it |

So the lag lives somewhere this session could not construct — most likely a browser whose websocket
was not delivering, leaving the 6 s heartbeat as the only refresh, whose *average* wait is 3 s.
That fits the reported number almost exactly, but it is a hypothesis, not a measurement.

**Fix: stop depending on the round trip.** The control rendered purely from the tree payload, so a
click showed nothing until a refetch landed — fast when a broadcast arrives, up to a full heartbeat
when one does not. **The click already knows the new level.** `EffortButton` now applies it
optimistically and drops the optimistic value the moment the payload speaks, whatever it says, so a
rejected or ceiling-clamped write corrects itself instead of sticking. `null` vs `''` are distinct
(nothing pending vs a pending *clear*), and the chip dims while unacknowledged.
This is uncommitted-operation state, not a mirror — the same exception the mail-retract path takes.

**Known gap, stated rather than hidden:** a pending *clear* still shows the old level for one
refresh, because the org default is not known client-side and threading it down is more plumbing
than the ~0.4 s it saves. A clear behaves exactly as it did before this change; only *sets* became
instant.

⟨discovered⟩ **16 async routes do blocking doc IO on the event loop.** `node_scope` was one:
`async def` holding `store.DOC_LOCK` (a *threading* lock) across `load_org` + `save_org`, so while
it waits for the lock or the disk **every other request and every websocket frame waits with it** —
precisely the №22 hazard the heavyweight endpoints were converted away from. Converted this one to
a plain `def` (FastAPI then runs it in the threadpool). Its explicit `await hub.changed(slug)` also
went: since G2 `save_org` announces every write, so that was a second, uncoalesced copy of one
signal. **The other 15 are listed below and deliberately NOT swept** — measured at 3-22 ms, so
nothing is urgent, and 15 route conversions is not a change to make while chasing a symptom:
`org_kiosk` · `org_hire_defaults` · `node_reorder` · `org_dissolve_all` · `org_killswitch` ·
`org_resume` · `credit_request_decide` · `user_inbox_read` · `extern_wait` · `org_inbox_read` ·
`user_inbox_clear` · `orgmd_put` · `user_audience` · `node_upload` · `node_mail_retract`.
`extern_wait` deserves the first look: it is a long-poll that rescans **every org doc** under
`DOC_LOCK` whenever `store.REVISION` moves — and since G2 the revision moves on every save.

⟨discovered⟩ **Five orphaned `tmp*.tmp` files** sit in `~/orgtree/orgs/` (20-27 KB, dated Jul 29 and
Jul 31 — *before* any of this branch's work). They are abandoned `save_org` temp files, so an atomic
replace has failed at least twice historically. Harmless (`list_orgs` globs `*.json`) but a symptom
worth a look; not touched, since they are the user's data.

**Verified live:** a genuine set is visible in **0.09 s** (the probe's own polling granularity)
across three consecutive changes, each confirmed by the server's own value afterwards.
→ `0ee5c28`

---

## D-44 · Coordinator answers status updates and starts a ping-pong loop
> also, add a note to the coordinator not to respond to status updates from its subordinates unless
> its work it directly requested from it. subordinates keep talking in a loop as the coordinator
> goes back and forth with them.

A status report is *information*. The coordinator was treating it as a message to answer, the report
answered the answer, and two agents burned turns being polite at each other. Added to charter clause
5 (already the message-discipline clause, so nothing renumbers): reply ONLY if the status concerns
work you directly asked that agent for **and** the reply changes what someone does next — a
decision, a correction, or the next piece. Never acknowledge. Named the cost explicitly, because an
agent that does not know an acknowledgement costs a whole turn will keep sending them. A genuinely
stuck report is not a status update and still gets answered.

Note `docs/charters/*.md` are live presets served by `/api/charters`, so this reaches the manual
hire form immediately — no deploy needed beyond the file. Existing coordinators keep their old
charter text until it is re-pasted.
→ `513b742`

## D-45 · Unread mail re-sorted itself as it was read
> also dont order unread mails at the top by default, that keeps reordering them as i read them
> which is confusing. keep their order static and based only on send time, dont ever reorder them

Unread mail sorted as its own block on top, so **reading reordered the list under the reader**: each
mail you opened left the top group and dropped into the body, moving everything around it. Now there
is ONE order — send time, newest first — and reading is a purely visual change: the row highlights
and stays put. `pending`/`delivered` still arrive as separate lists (they are different server-side
facts) and `_wait` still drives the styling; it just no longer drives position.

Sorting by send time rather than list position **stays** and is independent — that was D-01, where
the archive was appended in READ order so position was click order. This change is only about the
grouping.

**Verified live** with a synthetic inbox injected in flight (`page.route`, no org touched), built so
the two orders differ: four mails at 10:00 read / 09:00 unread / 08:00 read / 07:00 unread. Rendered
`TEN NINE EIGHT SEVEN`, and **a read mail rendering first is itself the discriminating evidence** —
under the old grouping an unread could never sort below it. Reading the 09:00 mail left the order
byte-identical.
→ `513b742`

---

## D-46 · The UI lag was a MISSING DEPENDENCY — and the manifest never declared it
> i discovered what was causing the lag: websocket events were nonfunctional due to there being no
> websocket library installed on the other machine. does the python backend package a list of its
> dependencies? if not, can you set up a simple poetry file or even just a pip freeze into
> requirements.txt?

This closes D-43, where the 3-5 s lag could not be reproduced here. The hypothesis recorded then —
*"a browser whose websocket was not delivering, leaving the 6 s heartbeat as the only refresh, whose
average wait is 3 s"* — was right, and the cause was one line of `requirements.txt`.

**Answer to the question: yes, a manifest exists — and it was wrong.** It declared `uvicorn>=0.27`.
**Plain `uvicorn` ships no WebSocket implementation.**

**The failure mode is the interesting part, and it is a textbook silent degradation.** Reproduced in
a clean venv installing exactly what the old manifest declared:

| | WebSocket upgrade | `/api/host` | startup |
|---|---|---|---|
| old manifest (no ws lib) | **`HTTP/1.1 200 OK`** | — | silent |
| with `websockets` | **`HTTP/1.1 101 Switching Protocols`** | — | silent |

Not a 500, not a 400 — a **200**. With no WS implementation the upgrade request falls through to the
SPA catch-all route and the browser is handed `index.html` where it expected a protocol switch. The
socket never opens, the client reconnect-loops every 1.5 s forever, every HTTP endpoint keeps working
perfectly, and the only symptom is that the app *feels slow*. ☞ **It was invisible from this machine
because the dev box has `websockets` installed for unrelated reasons** — the classic shape of a
dependency bug: it cannot be seen from where the code was written.

⚠ **A static import scan would never have caught it.** Nothing in the backend imports `websockets`;
uvicorn loads it by name at runtime. An audit of every third-party import (`ast`-walked across
`backend/` and `tools/`) found `fastapi · httpx · pydantic · starlette · typing_extensions ·
uvicorn · playwright` — and *not* the one package whose absence broke the app. **A dependency
nothing imports, whose absence produces no error, is undiscoverable without an explicit check.**

**Fixed:**
- `websockets>=12` declared, with the whole story in a comment so nobody "tidies" it away as unused.
- `pydantic`, `starlette`, `typing_extensions` declared too — all three are imported **directly**
  and were riding in as fastapi's transitive dependencies. A direct import deserves a direct
  declaration.
- **A startup wall** when no WS implementation is found, and `_ws_impl()` reported on `/api/host` so
  a deployment can *say* it is degraded instead of merely feeling slow. (UI banner deliberately not
  built — that is a design call, and the flag is now one line away for whoever wants it.)

**Not done, deliberately — and the user offered both:** no `pip freeze`, and no poetry file.
A freeze here would capture the **193** packages of a system-wide Python shared with other projects
and turn a fresh install into a resolution fight; poetry would add a tool dependency to a project
whose entire install path is `pip install -r requirements.txt` in two deploy scripts. Floors in
`requirements.txt` are the right weight for this. Say the word if you want a `pyproject.toml`.

**Verified live, and it discriminates** (clean venv, isolated port and data root):
fixed manifest → `101 Switching Protocols`, `/api/host` reports `"websockets"`, no warning.
Then `pip uninstall websockets` → `200 OK`, `/api/host` reports `null`, **and the startup wall
fires**. Four assertions, both directions.
→ `6cb3fd2`

---

## D-47 · orgtree now runs from a virtualenv
> is orgtree built to operate from a python venv? if not, it should be

**It was not.** `.venv/` was already in `.gitignore` — someone anticipated this — but nothing
created or used one: both deploy scripts installed into whatever `python` was on PATH, which on a
normal desktop is a system-wide interpreter shared with every other project. That is precisely the
condition behind D-46: the app worked here and not on another machine because this box happened to
hold `websockets` for unrelated reasons. **A venv makes "what is installed" equal to "what
`requirements.txt` says"**, which is the only version of that question worth answering.

Both scripts now resolve the interpreter as: `PYTHON`/`ORGTREE_PYTHON` override → repo-local
`.venv` → create it → **fall back to the system interpreter with a warning** if creation fails,
rather than breaking a deployment that was working a minute ago. `ORGTREE_NO_VENV=1` keeps the old
behaviour. Each run prints which interpreter it chose and labels it `[.venv]` or
`[system — deps are shared with every other project]`. README documents it, including why it is not
decoration. `steer.py` is stdlib-only, so the per-turn hook is unaffected by which interpreter runs
the backend — checked, not assumed.

⚠ ⟨discovered⟩ **"Verified end to end" was weaker than I claimed for `update.sh` on Windows.**
After the venv landed, the process holding the port was still the *system* `python.exe` while a venv
process ran beside it — which read exactly like a failed restart. It was not: on Windows a
venv-created `python.exe` is a launcher that spawns the base interpreter as a child, so the task
list reports the BASE exe. `sys.prefix` and `site-packages` both resolve inside `.venv`, so the
backend genuinely runs from it. **Checked before "fixing" it, which is the only reason nothing was
broken chasing it.**

But the false alarm exposed a real hole: **the health check proved liveness, not replacement.** It
asked "does something answer on the port", and if the old process were ever left alive that question
passes against the very code the run was trying to replace — reporting success while serving stale
code. Both scripts now capture the listening pids *before* the kill and fail loudly if the set
afterwards is a subset of them. Unit-tested across six cases (old survives · replaced · one of two
survives · old plus a new one · nothing was running · nothing listening).

Also on the way, `/api/host` now reports `python: {prefix, venv, version}` beside `websockets`, so
"which environment is this deployment actually running?" needs no process forensics — a question
Windows makes genuinely hard to answer from outside, as above.

**Verified live:** first run created `.venv`, installed into it, restarted the backend from it and
passed the health check; `/api/host` reports `venv: true`, prefix `…\claude-orgtree\.venv`, and
`websockets`. `update.ps1` re-parses clean under PowerShell 5.1.
→ `c04168f`

---

## D-48 · A slash command was not treated as user contact
> running a command in a chat doesn't count as a user message it seems: it doesnt grant a user
> audience, and doesnt cascade the command's execution up the chain

Correct, and it was structural rather than accidental: the `/message` endpoint branches on
command-shaped input near the top and **returns from one of three places before ever reaching the
mail block**, where `post_mail` and `user_deep_reach` live. So the user could run `/compact` on an
agent deep in a tree — splitting its context — and no superior would ever hear, and the agent gained
no user audience from the interaction. The endpoint's own docstring already claimed both effects, so
the code and its description had quietly disagreed.

**Fix.** `user_deep_reach` now takes `kind` ("message" | "command"), and the command branch calls it
**once, after the validity checks and before all three command paths**, rather than at each return —
the branch has several exits and per-exit calls are exactly the N-writers shape this month has been
spent removing.

A command stays **not mail**: no envelope, no Sent copy, nothing to deliver at rehire. What it now
shares is the two consequences of *direct user contact*, which are about who the user reached, not
about whether a copy was filed.

The notice wording differs because the claims differ. An instruction outranks the chain; a command
changes the agent's session without saying anything about anyone's plan:
> The user ran the session command "/context" on "worker", inside your chain. It came from the USER
> directly, not through you. Re-check any plan of yours that assumes worker's session is unchanged.

**Verified live** on a two-level probe org (`user → boss → worker`), created and deleted:

| case | result |
|---|---|
| before | `audiences: []`, only the hire notice |
| `/context` on the DEEP node | audience granted (`reason: "user ran a command directly"`) **and** boss notified with the command wording |
| `/context` on a TOP-LEVEL node | accepted, **no** notice and no audience — correct: `user_deep_reach` returns early when the only superior is the user |
| plain message to the deep node | still the "direct instruction" wording, and the audience is **not** duplicated |

The before/after within one run is the discriminating evidence: the audience list went from empty to
exactly one entry, and the notice appeared, off a single command.
→ `b759436`

---

## D-49 · Concurrent turn cap raised 3 → 16
> also increase max queued turn slots to 16, unless there's a reason not to

**No reason not to, and one number worth knowing.** The semaphore bounds *resources*, not
correctness — nothing serialises on it, no invariant depends on the width, and the cap is one
constant with one reader (`_turn_slots`), one env override and one README row. Grepped: nothing else
assumes 3.

**Measured before changing it:** a single headless CLI turn holds **~306 MB** resident, so 16
concurrent is roughly **5 GB** of working set at full tilt. Comfortable on the 32 GB dev box, tight
on a small VM — which is why it stays an env override (`ORGTREE_MAX_TURNS`) rather than a hardcoded
number, and why the README now carries the per-turn cost so the next person can size it.

⚠ Two things worth stating rather than discovering later:
- **The cap is GLOBAL, not per-org.** 16 is shared across every org on the instance, so a busy org
  can starve a quiet one. Nothing enforces fairness, and that was equally true at 3 — it just
  matters more now that the number looks generous.
- **It changes F-04's central tradeoff.** The ask-the-user design was shaped by a waiting agent
  costing a third of the org's capacity; at 1/16 that pressure is largely gone, and a longer wait
  window becomes reasonable. F-04 updated in place rather than left to mislead.
→ `7ad33f6`

---

## D-50 · A gap between the grey live row leaving and the real one arriving
> there appears to be a delay between a temporary grey event disappearing and the final full event
> taking its place, leaving a gap where messages / tool uses / responses are missing before they
> reappear

**The same mistake as D-34 and §②a, in its third costume: something on screen was dropped before its
replacement was in hand.**

The server side was already right — `_sweep_live` retires a live row only when its durable twin is
in the *same payload*, by `tool_use_id` rather than a timer. The client was not. `ingestStream`
blanked `draft` and `thinking` the instant the durable `text` event arrived:

```
patch(k, { thinking: '', thinkSecs: null, ...(ev.kind === 'text' ? { draft: '' } : {}) })
nudge(slug, ev.node)      // the replacement arrives 200 ms + a round-trip LATER
```

So the grey streaming text vanished on the event and the durable row appeared on the fetch — with a
hole in between where the message, tool call or response was simply missing. `turn_done` had the
identical shape.

**Fix: superseded ≠ replaced.** The durable event now *marks* the scaffolding stale instead of
blanking it, and the FETCH retires it — in the **same patch** that installs the payload carrying its
replacement, so there is neither a gap nor a frame showing both. The thinking clock still stops
immediately, because that is a fact about the world rather than something being rendered.

Three details that are easy to get wrong and are handled:
- **A new stream must not continue superseded scaffolding.** A fresh `delta`/`thinking` resets
  rather than appending, so the next message never inherits the last one's tail.
- **An in-flight fetch may not retire it.** A request issued *before* the event returns a payload
  from before the durable row existed; honouring it reopens the gap. `staleAt` records when the
  scaffolding was superseded and only a fetch that STARTED at or after that moment may clear it.
- `resetConvos` drops the flags with everything else.

**Measured, and the probe discriminates.** An in-page sampler at 20 Hz (a Playwright round-trip per
sample is far too jittery to see a ~300 ms hole) records per frame whether the grey draft is on
screen and how many durable rows the transcript shows. A **gap** is `draft` going true→false while
the row count does *not* increase — the thing on screen left and nothing replaced it.

| build | samples | gaps |
|---|---|---|
| pre-fix (`convo.ts` at HEAD, rebuilt) | 900 over 45 s | **1 gap at t+5.85 s, lasting 0.25 s** |
| fixed | 900 over 45 s | **0** |
| fixed, second run | 900 over 45 s | **0** |

0.25 s matches the report: brief, real, and exactly long enough to see.
→ `0f2746b`

---

## D-51 · The queued preview never appears while the agent is "starting"
> when i activate the agent with a new message, sometimes the queued preview never shows up while
> the agent is 'starting', i only see my message once it actually goes through

**The same rule a FOURTH time** (after D-34, D-43, D-50): something was retired before its
replacement existed. `send()` ended with

```
.then((r) => { …; return refresh(true) })
.then(() => dropPending(slug, node.id, t))   // unconditional
```

so the optimistic ghost died **as soon as the round trip completed** — measured at **50 ms**, which
is simply POST (~30 ms) + the follow-up GET (~15 ms). The mail is on the server by then, but it is
in neither place the desk renders: `send_message` drains the mailbox straight into the turn, so
`pending_mail` is empty, and the transcript will not carry it until the CLI has started (~3 s).
Between those, nothing on screen.

**Fix, in two parts:**
- **The unconditional drop is gone.** The ghost now retires only through `refreshConvo`'s
  graduation check — i.e. on evidence. This is what closes the reported hole.
- **Graduation checks `pending_mail` as well as the transcript.** A message passes through the
  mailbox first and the transcript second; checking only the second left the queued/frozen/deferred
  cases relying on the unconditional drop that just went away.
- A **command keeps** an explicit drop: it is not correspondence, never enters `pending_mail`, and an
  *immediate* command may never reach this node's transcript at all (it runs in a throwaway fork,
  output riding the live feed), so its ghost would have no evidence to graduate against and would
  sit there forever. The "command sent" receipt is its feedback.

**Measured, apples to apples, unique token per run:**

| build | ghost lifetime | message off screen |
|---|---|---|
| pre-fix | t+0.02 → **t+0.07** (50 ms) | **2.88 s** (t+0.07 → t+2.95) |
| fixed | t+0.03 → **t+2.95** | **never** — hands over the instant the transcript has it |

⚠ **Method note, because the first attempt produced contaminated evidence.** Both runs initially used
the SAME token, so the previous run's copy was already in the transcript and the containment check
graduated the new ghost within 20 ms — making the fixed build look broken. A repeated probe token is
indistinguishable from the thing being measured. Unique token per run, always.

⟨discovered, not fixed⟩ That contamination is also a real if minor defect: graduation matches by
`.includes()` against the last 20 user messages, so **re-sending byte-identical text graduates the
new ghost against the old message**. Harmless today (the ghost is cosmetic and the mail still sends)
and fixing it properly needs a per-send id threaded through, so it is recorded rather than patched.
→ `PENDING-COMMIT`

---

## Future feature pass — SPECIFIED, NOT BUILT

User, 2026-08-02: *"add two new features to the docket, but dont implement them: create a new
section for a future feature pass with them."* Nothing below has been started.

### F-01 · Navigation chips on the desk — jump to superior or subordinate
> add little clickable cards in the desk view of individual agents to jump directly to any of the
> agent's subordinates, or to its direct superior (the switchboard if its directly below the user).
> place the subordinate chips somewhere at the bottom of the ui, and the superior chip at the top.

Small clickable cards inside an agent's desk that move the camera to a related agent:
- **superior chip at the TOP** of the desk. For a top-level agent the superior is the user, so the
  chip targets the switchboard (the eye) rather than a node.
- **subordinate chips at the BOTTOM**, one per direct report.

Notes for whoever builds it: `DeskChat` already receives `map` (id → CanvasNode) and `node.children`,
so both sets are in hand without a new payload. The jump itself is the existing camera move —
`onJump`/`centerOn` in `OrgCanvas`, already threaded into the switchboard tabs for exactly this
purpose. Worth deciding whether a chip carries live state (busy dot, mail count) or stays inert.

### F-02 · `/remote-control`, if feasible
> potentially enabling /remote-control? if its feasible

Feasibility unknown — **investigate before scoping**. Open questions: what the slash command
actually does in the pinned CLI; whether it works at all in a headless `-p` session (orgtree already
strips the interactive-only tools, and a command needing a live client would be inert); and what it
would mean for an agent inside a sandbox container. orgtree already has a verbatim slash-command
path (`send_message(command=True)`) that delivers a `/…` as its own user event, so the delivery
mechanism exists if the command itself turns out to be viable.

---

### F-03 · side hire buttons — hire a COWORKER, not a report
> add to the feature docket: separate hire buttons that appear on the left / right sides of an agent
> that hire a coworker to it underneath the same superior
>
> but dont implement that now

Hire chips today spawn a SUBORDINATE (a child of the hovered agent). This adds chips on the LEFT and
RIGHT edges that hire a **sibling** — same parent, placed to that side. Left/right chooses which
side of the agent the new hire lands on, which also fixes ordering intent at creation time rather
than by a later reorder.

Open questions for whoever builds it: what happens on a TOP-LEVEL agent (parent is the user — the
same grant rules as a top-level hire, presumably, and `max_top_grant` applies); whether the credit
grant comes from the same place as a subordinate hire; and how the chip behaves on a card inside a
retired pile or a crowd stack, where "the side of the agent" is not a free position.

**NOT BUILT — the user ruled explicitly.**

### F-04 · agents asking the USER a question
> i want to discuss a new feature to add to the docket: support for asking the user a question. i
> know this isnt necessarily possible in headless mode directly with claude code, but perhaps we can
> mimic it with a bespoke custom implementation instead.
>
> dont implement the question asking yet, put it on the feature docket. but do the research on
> feasibility for now.

**NOT BUILT.** Research only, per the ruling. Everything below marked *measured* was run against the
pinned CLI on 2026-08-03; everything else is design, and the open questions are genuinely open.

#### What was measured

| question | result |
|---|---|
| Is `AskUserQuestion` even present headless? | **Yes** — it is in the headless tool list. |
| What happens when a headless agent calls it? | **It fails.** The tool returns the error text `Answer questions?` — the permission-prompt title, auto-denied because there is no interactive client to answer it. So the user's instinct was right: the native tool cannot work as-is. |
| Can an MCP tool call BLOCK long enough to wait for a human? | **Yes, and by a lot.** Purpose-built stdio MCP server with one tool that sleeps: **20 s → works**, **606 s → works** (`exit=0`, the agent received the tool's return value and answered from it). No timeout, no error, no special flags. |
| Is there hook machinery to intercept the native tool? | **Already wired.** `supervisor._steer_settings` declares an explicit array per hook event, with a live `PostToolUse` (steer.py, `timeout: 8`) and an **empty `PreToolUse`** — the slot exists and is currently unused. |
| What does a blocking wait cost? | A turn holds one slot for its whole life, so a waiting agent occupies one. **Was** `MAX_CONCURRENT = 3` — a third of the org — which made a blocking wait expensive and drove the "short window, auto-degrade to parking" design below. **Raised to 16 on 2026-08-03 (D-49)**, so a wait now costs 1/16 and the pressure behind that design is largely gone: a longer default window (or not parking at all) is worth reconsidering when this is built. |
| Is there precedent for long-poll Q&A here? | Yes — `externtool.orgtree_wait` (25 s default, deadline-bounded, returns empty so the caller re-waits). Same shape, opposite direction: outsiders waiting on an org rather than an org waiting on the user. |

#### Two viable mechanisms

**① A bespoke MCP tool (`orgtree_ask`) — the recommended primary.** Explicit, returns a real
*successful* tool result, and every piece it needs already exists: a durable org-doc key for the
pending question (like `delivering`/`org_inbox`), the user inbox for surfacing, audiences for
gating, and `orgtree_status` for saying why an agent is idle.

**② PreToolUse interception of the native `AskUserQuestion` — the elegant bonus.** Agents would use
the tool they already know, and prompts written for interactive Claude Code would just work headless.
⚠ Unproven: whether a hook can inject a **successful** result or only deny-with-reason (where the
reason text reaches the model but is framed as a refusal). That is the one experiment left, and it
should be run before anyone commits to this path.

#### The shape I would build

One tool, **bounded wait that auto-degrades to parking** — the agent should not have to choose:

1. `orgtree_ask(question, options?, multi?, timeout_s?)` writes a pending question to the org doc
   with an id, sets the node's status to *waiting on the user*, and long-polls.
2. **Answered inside the window** → the answer comes back as the tool result and the turn continues
   with full context. This is the case worth building for: quick disambiguation, where ending the
   turn would throw away momentum and cost a fresh context load.
3. **Not answered** → the tool returns "no answer yet; it will reach you as mail", the agent wraps up
   and ends its turn. The question stays pending.
4. **Answered later** → delivered as mail, which starts a turn as any mail does. The answer is never
   lost, which is D-045's at-least-once instinct applied to questions.

Because a waiting agent held one of **three** slots when this was researched, the default window was
to be **short — 30-60 s**. With the cap now at **16** (D-49) that pressure is largely gone; revisit
the window, and open question ② below, before building.

#### Open questions — for the user, not for me to assume

1. **Default wait before parking.** My suggestion: 60 s. Long enough to catch a user who is looking
   at the screen, short enough that an absent user does not cost a slot.
2. **Should a waiting agent hold a turn slot at all?** Cleanest is to exclude waiting agents from the
   concurrency cap, but that is a supervisor change with real deadlock risk (re-acquiring a slot on
   answer). Ruling needed before anyone builds long waits.
3. **Who may ask?** Mail to the user is audience-gated. Same gate for questions is the consistent
   answer — and per the design motto ("auto-bridge instead of refuse"), an agent without a user
   audience should probably have its question **routed to its superior** rather than refused.
4. **UI home.** A question is stickier than mail: it stalls an agent. Options are the user inbox with
   a distinct "needs an answer" state, or its own surface. There is also a tension with D-45 — a
   question that must not be missed argues for pinning, and pinning is exactly what was just removed
   from the mail list.
5. **Answer shape.** Mirror `AskUserQuestion` (a header, 2-4 options, optional multi-select, free-text
   "Other")? Agents already understand that schema, which is an argument for copying it exactly.
6. **Kiosk.** May an agent question a public visitor? Probably yes but worth a per-org switch.

#### What already works today, unglamorously

`orgtree_status(blocked, ...)` notifies the superior, and an agent with a user audience can mail the
user and stop. The user replies and the turn resumes. That is questions-without-options and
questions-without-blocking — so the feature is really about **structured options** and **not losing
the turn**, not about making the impossible possible.

### F-05 · counter-offer a credit request by dragging the bar
> new feature for the docket: allow the user to adjust the amount of credits granted during a credit
> request by an agent by dragging their bar up or down, which is copied into the dialogue temporarily
> and shows how much additional credits are being granted with a "+x" value an a I-bar to the side
> stretching the difference between current and new grant quantity
>
> the dialogue should embed its own credit bar inline. the bar can be dragged as high ir low as the
> user wants: reducing the ask, *increasing* beyond the ask, or even lowering the total credits the
> agent has, down to its current allocation (why a user would want to do this, who knows). the agent
> can opt to do whatever it wants immediately after: it can take the user's grant as-is, request
> more, or find another way around the limitation.

**NOT BUILT.** Today a credit request is take-it-or-leave-it: the card shows `old → new (+delta)`
with **approve** and **deny**, and `credit_request_action(rid, action)` grants exactly `req["new"]`.
There is no way to say "you asked for 12, have 5".

**Most of the machinery already exists.** `CreditBar` (`canvas/cards.tsx`) does the whole drag —
pointer capture, `min`/`max` clamps, a `maxGhost` overlay, ruler rungs at real quantities,
`onDragValue` for live values and `onCommit` otherwise — and it **already computes
`delta = drag.val - drag.g0`**, which is the "+x" this asks for.

#### Rulings (user, 2026-08-03) — the open questions are closed

**① The dialogue embeds its own `CreditBar` inline.** No canvas mode, no docking; the panel is a
full-screen overlay and stays one. Cheapest of the two options by roughly an order of magnitude.

**② The bar's range is the full legal range** — reduce the ask, exceed the ask, or reduce the
agent's grant *below what it already holds*, "down to its current allocation".

☞ **That floor already exists as a ledger invariant, so the bar should mirror it rather than invent
one.** `reallocate` refuses any `-Δ` larger than `free(nid)`:
> `{nid} has only {free} unused; the rest is committed`

So the true minimum is **`grant − free(nid)`** — everything unused can be stripped, nothing already
handed down to subordinates can be. That is almost certainly what "down to its current allocation"
means; ⚠ if it instead meant "no lower than its existing grant", say so, because it is one number
and it is the difference between a clawback tool and a decline-politely tool.
Both ends map onto invariants that already exist, which is the good news:
`min` ← `grant − free(nid)` · `max` ← `_check_top_grant`/`max_top_grant` and `_kiosk_cap_check`.
Also: `_stranding_warnings` already fires on a reduction, so the dialogue should **surface those
warnings before committing** — "this strands 4 credits of work under X" is exactly the thing a user
dragging downward needs to see.

**③ The agent's move is its own.** After the verdict it may take the grant as-is, request more, or
route around the limit. So the notice must state the outcome and **not** imply the matter is closed
— which today's wording does.

#### What it still needs

1. `credit_request_action` takes an optional granted amount instead of implying `req["new"]`, and
   `CreditDecision` carries it. The delta maths (`req["new"] - grant` → `granted - grant`) is one
   line; the *validation* is the real work, and per ② most of it already exists.
2. **An honest notice.** Today: *"APPROVED — your grant is now N"* — true but misleading for a
   partial grant, and actively wrong for a reduction. A counter-offer wants its own status so the
   record does not read as a plain approval, and wording that names what was asked, what was given,
   and that the agent may come back.
3. The UI: requested value pre-loaded, live "+x", and the **I-bar** spanning current→new so the size
   of the concession is visible rather than arithmetic.

Note these requests come from **top-level** agents (the credit is the user's own pool), so
`max_top_grant` is the binding upper clamp.

## Carried, not done

| item | state |
|------|-------|
| D-28 message rendered twice | FIXED (journal `via`) |
| D-29 ~6s blank before thinking | FIXED (derived `starting…`) |
| D-30 shared conversation store | FIXED (`convo.ts`) |
| `chain_notices` dead reserved key | REMOVED (D-33) |
| `_move` inflates a top-level grant past `max_top_grant` | confirmed in the shelved ledger review, unfixed |
| `game-master` has an empty charter although `hire` now refuses without one | predates the requirement |
| P1–P5 of the state review | ALL IMPLEMENTED (D-32) |
| mobile wave | spec at `docs/mobile-spec.md`, held by the user |
