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

> **Review status (implementer, 2026-08-04):** reviewed in full — every hunk of the
> fb427e9→8964a0f diff accounted for, re-adopted onto `rebuild` in unit commits with green gates
> between each, and merged to `main`. The backend production diff was adopted unchanged (three of
> its fixes correct the implementer's own pre-dormancy bugs). The frontend was adopted with a fix
> pass from an adversarial re-review: an org.md-wiping failed-read path in SettingsPanel, a
> heartbeat kill in `resetConvos` on org switch, an out-of-order payload race and unmount-gated
> event fetches in `convo.ts`, a mismatched-props localStorage sweep in OrgCanvas, an effort
> control that couldn't hear a same-value answer, a retract with no rollback, and smaller edges
> (ghost/live render keys, a `LiveRow` cast, the >2000-char steered needle, API `no-store`).
> Entries below describe the branch as the secondary left it; where the rebuild differs, the code
> and DECISIONS.md are the truth.

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
→ `8f83b74`

---

## D-52 · Still happening — the preview dies on a REPEATED message
> still seems to be happening

D-51 was real and fixed, but it was not the whole bug. The remaining half is the defect D-51
**recorded and deliberately deferred**, and deferring it was the wrong call: graduation matched by
`.includes()` against the last 20 user messages **with no regard for when**, so a second send of the
same text matched the FIRST one and graduated instantly.

Short repeated messages — *"continue"*, *"yes"*, *"go on"* — are the common case in this app, not an
edge one. That is why the fix looked complete against a probe using unique text and kept failing in
real use. ⚠ **I had the evidence in hand and filed it as cosmetic.** It was the bug.

**Measured, same probe, same org, sending `"continue"` twice:**

| | before | after |
|---|---|---|
| send #1 | ghost 2.92 s | ghost 2.90 s |
| send #2 (identical text) | ghost **0.03 s** | ghost **4.85 s** |

**Fix: graduate on a COUNT, not on existence.** A ghost records how many copies of its text the
server was already showing when it was created (`PendingGhost {text, seen}`), and retires only when
a payload shows **more** than that. `serverCopies()` counts both places a message can be — the
mailbox (`pending_mail`) and the transcript — since it passes through them in that order.

Counting rather than timestamping is deliberate: it needs **no clock comparison** between browser and
server, so no skew can retire a ghost early. A stale baseline can only over-count what was already
there, which keeps a ghost a moment longer — erring toward showing the message, which is the
direction this entire class of bug wants.

⚠ **Process note.** Line endings bit twice here: `.gitattributes` sets `* text=auto` and
`core.autocrlf=true`, so a `git stash pop` returns `.ts`/`.tsx` with **CRLF**, and multi-line
patch patterns silently stop matching. Two edits reported success while changing nothing, and only
`tsc` caught it. Normalise before patching, and never trust a `.replace()` that is not asserted.
→ `72dfde2`

---

## D-53 · ⚑ HANDOFF — the queued preview, still reported, NOT reproduced
> still happening
> if you cant fix it after this, then leave it for the implementer to have a proper look at

**Two real bugs were found and fixed on the way (D-51, D-52) and the symptom persists, so at least
one more cause exists that I could not reach.** Handing over on the user's instruction. This entry
exists so the next person does not re-run the ground I covered.

#### What is fixed and verified

- **D-51** — `send()` dropped the optimistic ghost unconditionally after the round trip (measured:
  **50 ms**), leaving **2.88 s** with the message nowhere on screen. Removed; graduation is on
  evidence.
- **D-52** — graduation matched by `.includes()` with no regard for *when*, so a repeat of the same
  text ("continue", "yes") matched the earlier message and graduated in **0.03 s**. Now count-based.

#### What was tested AFTER those fixes — all passing, none reproducing the report

| axis | covered |
|---|---|
| surface | zoomed desk · **switchboard panel** (`bare compact`) |
| agent state | idle (activating) · **busy** (queued behind a turn) |
| text | unique · **byte-identical repeat** |
| measurement | element existence · **actual visibility** (rect vs the `.msgs` scroll container) |

Sampled in-page at 40 Hz. Representative: switchboard, idle → ghost first at **0.02 s**, visible
**2.75 s**, handing over to the durable bubble with no hole; busy → ghost 0.02 s then the `pendrow`
takes over for 70 samples. **No gap in any combination.**

#### The three leads I would pursue next, in order

1. ☞ **The user's browser may not be running the fixed bundle.** A tab open since before a deploy
   keeps its old JS — the filename is content-hashed, so nothing re-fetches until a reload. Three
   "still happening" reports arrived within minutes of three deploys. `index.html` is served by
   `FileResponse` with an ETag and **no `Cache-Control`**, so a reload revalidates correctly, but a
   tab that is never reloaded never learns. **Worth proving before touching code**: check the loaded
   bundle name in devtools against `curl -s localhost:7360/ | grep assets/index-`. A build stamp in
   `/api/host` compared against the bundle would make this answerable rather than assumable, and is
   probably worth building regardless.
2. **A send path with no ghost at all.** `MailList.onReply` (inbox panel) calls `sendMessage`
   directly and never calls `addPending` — replying there shows nothing until the server copy lands.
   That is a real hole; it is simply not the path I tested, and "activate the agent with a new
   message" may well mean it.
3. **Long real transcripts.** Every probe ran on a fresh org with a handful of messages;
   `serverCopies` counts within `messages.slice(-20)`. The user's `arti` doc is 177 KB. A baseline
   computed over a window that later shifts is the kind of thing that would show up only at size.

#### Honest assessment

The symptom is intermittent ("sometimes"), and every hypothesis I could construct I also
*disproved* by measurement. That combination — plus lead ①, where three fix-report cycles each
landed within minutes of a deploy — makes me suspect the remaining reports are at least partly a
stale bundle. But I could not demonstrate it, and I have twice now told the user something was fixed
when it was not, so this stays open rather than closed with an excuse.
→ `6716e13`

---

## Future feature pass — status 2026-08-05: F-01, F-03, F-04+F-05 (as the
## unified ask system), and F-07 are BUILT (see DECISIONS D-090..D-094,
## D-096 and the ui-guide); F-02 is investigated-only; F-06 is next; F-08
## stays HELD. Entries below are kept as the original specs.

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

**INVESTIGATED (implementer, 2026-08-04, against the pinned CLI 2.1.220 — `claude remote-control
--help` + binary strings; no live probe, since starting the server ENROLLS THE DEVICE on the
user's claude.ai account, an account-state change that is the user's to make).** Findings:

- It is not a per-session slash command but a **standalone subcommand**: `claude remote-control`
  runs a *persistent server* in a working directory; you connect from claude.ai/code or the Claude
  mobile app and it spawns/controls sessions there (`--spawn same-dir|worktree|session`, capacity
  32). Requires a logged-in subscription and a one-time workspace-trust acceptance in that dir.
- ☞ **The orgtree-shaped hook exists: `--session-id <id>` resumes a SPECIFIC session.** So "take
  over an agent from my phone" is plausibly: orgtree launches
  `claude remote-control --session-id <agent session_id>` in that agent's scratch dir, the user
  drives the agent's real session from claude.ai, orgtree kills the server on release.
- Constraints found: ① the supervisor must NOT run turns on a remote-controlled session (two
  writers, one session id) — needs a `remote-controlled` node state that parks mail until release;
  ② sandboxed agents are out of scope at first — their session files live in the container and
  the container deliberately never holds the subscription token; ③ unknown whether the server
  runs without a TTY (it reads keys — "press 'w'"), which decides whether orgtree can spawn it
  headless; ④ workspace trust may not have been recorded by `-p` runs.
- Next step if pursued: ONE live experiment (user present, their account): start
  `claude remote-control --session-id …` against a probe org's agent, confirm it appears on
  claude.ai/code, confirm TTY-less spawn works, then scope the UX (a desk button + the parked
  node state).

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

### F-06 · a public orgtree mailserver — org-to-org mail across machines
> a pubic orgtree mailserver. a separate subproject of the repo that acts as a central
> communication hub for multiple orgtree instances running on separate computers. you run it in a
> central hub location, and then connect individual orgtree instances to it. they communicate with
> it via the external mailbox, and can send and receive mails to it just the same as they would to
> other adjacent orgs. the only difference is that the mailserver needs to be configured in the org
> settings on creation in order for it to appear as a listed entry.
>
> it uses https and long polling to send and receive mails, and has its own independent ui that
> isolates a mimic of the mail ui of individual orgs themselves. orgs register with the mailserver
> using a slug combining the org name with the username of the logged in account on the pc they're
> interacting from.
>
> this allows multiple simultaneous users of the orgtree to have their orgs all communicate with one
> another over the air, and work autonomously as a collective unit.
>
> this system should use docker in order to be resilient and automatically start up on machine start.
>
> any further suggestions?
>
> one question that comes to mind its how to handle starting orgtree up when an org has pending mail.
> do we activate the org immediately automatically? does the org wait for the user to manually
> trigger it to check? what is the resolution here?

**NOT BUILT.** Exploration and design only, per the ruling. Full spec: **`docs/mailserver-spec.md`**.

Four things worth carrying at docket level:

**① The orgtree side is small; the hub is the project.** Inbound already funnels through ONE
function — `deliver_org_inbox` (`supervisor.py:2415`) serves both chatq and inter-org — and
outbound through ONE dispatch (`api.py:1929-1948`). A hub client is structurally
`start_chatq_bridge` (35 lines) pointed at HTTPS instead of files, plus one ledger prefix and one
settings block.

**② Identity — RULED (user, 2026-08-04): a self-issued secret minted at org creation.**
> instead of orgs receiving a secret on join, they just generate their own secret on creation and
> supply that as their registration info. still include the username and org name in the slug, but
> also part of the secret for uniqueness, and the rest can be used for authentication like a
> password kind of.
>
> and i agree with the auto start as well, i was also leaning in that direction

This supersedes the first draft's hub-issued TOFU scheme and is better than it: the identity exists
before any hub, survives a move to another PC (the draft's stated weakness), and makes a land grab
impossible rather than merely refused. ⚠ The draft's underlying finding stands — `<org>.<username>`
alone is neither unique (`Administrator`, `admin`, `user`, `pi` recur everywhere) nor authenticated
(a username is a string the client asserts) — and the ruling closes it by drawing the uniqueness
from the secret.

☞ **One amendment carried into spec §3: derive the public suffix as `sha256(secret)[:6]` rather
than slicing the secret.** Identical from the outside, but the public part then discloses nothing
about the private part, the hub stores a fingerprint instead of a credential (a hub DB leak exposes
nothing), and there is no split to get wrong. Compare against the FULL fingerprint, never the
6-char display suffix — 24 bits is brute-forceable. Mint with `secrets.token_hex(16)`, the repo's
existing credential pattern (`api.py:352,549,554,587`); `uuid4` is for ids, not credentials. And
the secret must never enter an agent's context — an org that reads untrusted remote mail must not
carry its own mail identity in the prompt.

**③ The pending-mail question — RULED (user, 2026-08-04): auto-start.** It was already answered
in-tree, and the answer agrees:
`reconcile()` drives any live unfrozen node with a waiting mailbox at startup
(`supervisor.py:2671-2678`), and a backlog is cheap because `_envelope`/`take_mail` drains the
WHOLE mailbox into one turn (`supervisor.py:861-887`) — 40 messages wake an agent once, not 40
times. So `net_wake: auto` is the default, with `notify` and `curated` positions available, and the
rule holds that **only driving is gated, never delivery** — plus a staleness stamp in the envelope
so an agent can tell a fortnight-old request from a fresh one.

**④ The new hazard is spend, not transport.** This is the first external path where an unknown
third party can make your machine run tools and burn credits — chatq peers are sessions on your own
PC and `@org:` peers are your own orgs. Needs a per-org accept policy, per-peer rate limits, and an
inbound-mail spend ceiling. The existing "untrusted outside input, never user authority" framing
(`supervisor.py:2455-2461`) is the right injection mitigation and should be reused verbatim.

**⑤ Joining is open — RULED (user, 2026-08-04).**
> any new org that has access can join and is immediately listed, the join auth is just having
> access to the server (it will be on a closed network)

Reachability is the authorization; the join code from the first draft is dropped and the default
accept policy becomes `open`. ☞ This does **not** retire ②, and the distinction is easy to collapse:
*joining is open, addresses are owned*. Without the org secret any participant could poll another
org's queue and read its mail — by accident as easily as by intent. The posture now rests entirely
on the network boundary, so the hub must bind to the private interface only, and TLS still earns its
place inside it because the org secret crosses the wire on every call.

**⑥ Unattended operation — the autostart requirement, and what it exposes.** New spec §9.
> this opens up the possibility of orgs running fully autonomously without direct oversight by a
> user, so we need a way of ensuring orgtree starts up automatically with the pc it's on

Boot-start is the easy half. Three measured findings shape the rest:

- ⚠ **Do not install it as a Windows service.** `~/.claude/.credentials.json` is a plain file in
  the **user profile** (measured on this machine, 566 bytes). A `LocalSystem` service resolves a
  different `~`, finds no credentials, and every agent turn fails — while orgtree itself boots
  fine and the UI serves, so the failure looks like anything but auth. Correct recipe: Task
  Scheduler *At log on*, running as the user, auto-login, and untick the default 3-day
  "stop the task if it runs longer than" or it dies silently on day three.
- ⚠ **Docker Desktop forces the same conclusion.** Measured: `com.docker.service` is
  `Stopped`/`Manual` — that is only the privileged helper; the engine lives behind the user-session
  app. An instance hosting **sandboxed** orgs cannot run from a logged-out box at all.
- ⚠ **Authentication expires, and that is the ceiling on "fully autonomous."** Measured from the
  credentials file: the access token lasts ~8 h (refreshed automatically, not a concern), but
  `refreshTokenExpiresAt` is **~15 days out**, and re-auth is interactive. Whether the CLI rolls
  the refresh token forward was **not** verified — it is spec §12 №1 and one experiment settles it.
  Either way the cheap fix is the same and is worth building **before** the mailserver, since it
  applies to any unattended orgtree: read those two timestamps, and mail the user days before they
  lapse. Finding out from a pile of failed 3am turns is the worst available outcome.

☞ **The biggest unattended risk is already in this docket.** D-44 — *"subordinates keep talking in
a loop as the coordinator goes back and forth with them"* — was fixed by a charter clause and
noticed **because the user was watching**. Two autonomous orgs on separate machines reproduce it
with nobody in the room and a credit meter running on both. Spec §9.4 asks for a per-peer
exchange-depth breaker, a daily inbound-drive budget, `auto_resume` effectively mandatory, and a
dead-man's switch. Also worth saying plainly: **a Linux box is the better host for an unattended
instance** — one systemd user unit with `Restart=always` plus `enable-linger` covers boot *and*
crash, against Windows' scheduler-plus-auto-login stack.

**⑦ API-key credential mode for autonomous instances** (user, 2026-08-04). Spec §9.5. Already
half-built: `sandbox.py:451-455,499-503` selects between `proxied` / subscription / **API key**
today, sourced from `kiosk.api_key` or `ORGTREE_SANDBOX_API_KEY`. Two gaps — it is **sandbox-only**
(unsandboxed orgs get the whole host env via `clean_env()`, so a key would be global or absent, not
per-org), and it is **kiosk-only** (the field lives in the kiosk spec, not org settings). The
per-node env is built at `supervisor.py:1178-1181`, which is where a per-org key belongs.

☞ For an unattended instance this should be the **default, not the alternative**: it removes ⑥'s
re-auth ceiling entirely and stops an autonomous org consuming the user's subscription limits.
Trade: metered spend against the org's own budget makes §9.4's daily drive cap and dead-man's switch
necessary rather than prudent.

**Correction to ⑥ from reading `subproxy.py`:** the refresh token **does** roll forward
(`subproxy.py:74` stores `res.get("refresh_token", <old>)`), so 15 days is a floor for an online
box, not a ceiling. ⚠ Real defect found in passing: `subproxy` never updates
`refreshTokenExpiresAt` when it writes a new refresh token, so that field goes stale — the proposed
expiry watcher must not trust it as-is.

**⑧ `headless` mode** (user, 2026-08-04). Spec §9.6. An org told no user is present, with
user-bound requests auto-denied and org mail as its only channel. Two findings:

- ☞ **Mail to the user must NOT be denied**, unlike `request_credits` and `request_audience`. The
  user inbox is the audit trail of an unattended run and where the dead-man's switch reports —
  accept the write, tell the sender no reply is coming. There are seven user-bound paths in the
  ledger (`ledger.py:240,939,1046,1122,2461,2530` plus `post_mail`→USER at 827); only two are
  questions, the rest are records and must survive.
- ⚠ **`fable_limit_policy` and `fable_filter_policy` default to `halt`** (`api.py:772`), which
  escalates to the user and waits — a halted headless org is a dead org nobody notices. Headless
  must force `auto_resume` on or refuse a `halt` policy.

⚠ Headless is **not** kiosk: a kiosk is sealed from the outside world, headless *depends* on it.
An org that is both cannot communicate at all.

**⑩ Inbox scoping + read receipts** (user, 2026-08-04). Spec §10.

- **Scoping:** every hub read is authenticated by the org secret and returns only that org's mail;
  no endpoint exposes another org's queue, including to the hub UI. ⚠ This constrains the broadcast
  idea — a group address must **fan out into per-org copies at the hub**, not create a shared
  thread. It also means the hub's "mimic of the mail UI" is per-org and behind the secret; an
  operator dashboard listing all traffic would be the shared mailbox this rules out. Unchanged
  inside an org: inbound mail still reaches every live top-level agent and inbox-audience holder
  (`ledger.py:969`).
- **Read receipts:** orgtree can report something better than "delivered". `_confirm_delivered`
  (`supervisor.py:1250`) fires only once the CLI emits a real event after mail was drained into a
  turn envelope — a **true read signal**, not a transport ack, and the same journal whose
  unconfirmed batches fold back on restart. Five states: `queued` → `sent` → `fetched` →
  `delivered` → `read`. Receipts ride the existing long poll, and `fetched` without `read` is the
  useful diagnostic (recipient frozen, out of credits, or no live top-level agent). ☞ Surface
  `read` to the sending agent — "delivered but unread for six hours" is what stops a re-send loop
  between two unattended orgs. ⚠ Receipts leak when an org is running; acceptable on the closed
  network ruled for, revisit if that boundary ever changes.

**⑪ Slug immutability + two receipts** (user, 2026-08-04). Spec §3, §10.2.

- **The full slug is fixed for the org's lifetime** — org name, username, and fingerprint suffix
  alike. Closes the rotation question: the suffix is pinned. ☞ **Store the network slug, never
  recompute it** from name + username, or moving the org or renaming the OS account silently
  changes its address. ⚠ After a rotation `sha256(secret)[:6] != suffix` — verification must
  compare the hub's stored fingerprint, never re-derive the suffix, or an org is locked out of its
  own address the first time it rotates.
- **Received and read are separate signals**, not two points on one bar: *received* = the hub
  acknowledged custody (answers "is my link to the server working" — its absence implicates the
  network, never the peer); *read* = an agent's turn consumed it. ⚠ A missing received receipt does
  **not** mean the message was not delivered — a timed-out send may already have been accepted, so
  the retry must be **idempotent on the message id** or every flaky connection duplicates mail at
  the far end. No received receipt within a threshold ⇒ mark the hub unreachable in the status pill
  rather than spooling silently.

**⑫ Scope cut — keep v1 basic (user, 2026-08-04).** Three corrections:

- ⚠ **The hub mail UI is GLOBAL**, showing all orgs' traffic with a per-org filter — ⑩ had it
  per-org and was wrong. Corrected in spec §10.1. What stays scoped is the **org's own inbox**: an
  instance polls its own queue by its own address. Consequence to keep in view — a global UI means
  hub access *is* read access to everyone's correspondence, so "who can reach the hub UI" and "who
  can read all the mail" are the same question.
- **Broadcasts / mailing lists: out of v1.** Basic org-to-org chat only, as the existing mailbox
  already offers. Kept in the spec as a later idea so it is not rediscovered as new.
- **Secret rotation: out of v1** — simplify now, harden later. This actually *removes* the trap ⑪
  recorded: with no rotation, `sha256(secret)[:6] == suffix` always holds. ⚠ That equality is
  exactly what stops holding the day rotation lands, so do not build a check that assumes it.

☞ **The one thing that must still happen at day one despite the cut:** carry an optional
`thread_id` in the message envelope. Unused it costs nothing; it cannot be retrofitted into mail
already stored without it, and at 10+ peers a flat inbox will want it.

**⑬ Headless requires an API key · one hub for v1 (user, 2026-08-04).**

- **Headless without an API key is refused**, at creation and in settings, and the key cannot be
  cleared while headless is on. Not a recommendation — the derivation is ⑥: subscription auth ends
  in an **interactive** re-login, and headless is defined as having nobody to perform it. That
  combination has exactly one possible ending, silent death at an unpredictable hour. Spend
  isolation and revocability are real but secondary.
- **One hub for v1, several not designed out.** Three schema decisions taken now cost nothing in a
  single-hub build and are painful later: store hub config as a **list of one**, key per-hub state
  (registration, last-seen, spool, receipts) **by hub id** rather than globally, and keep
  `@net:<slug>` **hub-agnostic** — one self-issued secret already works everywhere, so which hub
  reaches a peer is a lookup, not something parsed out of the address. UI and settings can stay
  singular.

**⑭ Same-machine auto-connect — YES (user question, 2026-08-04).** Spec §3. An org on the same
computer as the hub registers automatically, no configuration. The repo already has the pattern:
chatq registration is unconfigured and automatic — `chatq_available()` (`supervisor.py:2336`) gates
it, orgs register at startup (`api.py:342`) and at creation (`api.py:540`), and kiosks are excluded
with any stale registration torn down (`supervisor.py:2348-2351`). It works because ② already
removed everything that would need a prompt: the org mints its own secret, and joining needs no gate.

**The opt-out is a checkbox in the creation form's mailserver section** (user, 2026-08-04),
checked by default, alongside the hub-address fields — home is the `advanced` disclosure
(`App.tsx:583-584`) where kiosk and sandbox have lived since D-40, and the collapsed summary
(`App.tsx:577`) should name the hub state too. ⚠ **Do not gate the checkbox on the hub being
detected** — the boot race means a hub that is not up yet must still be checkable, or the setting is
missing exactly when an autostarting machine is being configured. Detection is a hint beside the
box, not a precondition.

⚠ Two constraints the chatq precedent does not cover. **Local hub only** — a remote hub is
configured explicitly, or an instance auto-joins every network it can reach, which under the
open-join ruling is exactly how you end up somewhere by accident. And **retry, do not probe once**:
with ⑥'s autostart the hub container and orgtree race at boot and the hub usually loses, so a single
startup probe leaves the instance unregistered until someone restarts it. chatq is a file that is
either there or not; a container takes time to come up.

**⑨ The six build questions — ANSWERED (user, 2026-08-04).** Full table at spec §12: built by the
**implementer**; orgs may join a hub **after creation**; **10+** participants; the hub runs on
**Linux**; `net_wake` ships **`auto` only** (`notify`/`curated` documented but not built); and the
hub carries **strictly org-to-org** mail.

⚠ **10+ participants changes two v1 calls.** Threading (`thread_id`) is no longer deferrable — a
flat org inbox holding concurrent conversations with ten peers is unreadable to an agent, and it
cannot be retrofitted into stored history. The directory blurb moves from nice-to-have to necessary
for the same reason: nobody addresses ten orgs correctly from slugs alone.

⚠ **Terminology correction (user, 2026-08-04):** an earlier line here asked about "unattended orgs
that are not headless", which is incoherent — the two words describe different nouns. **Unattended**
is the *machine* (autostarts, runs with nobody at it); **headless** is the *org* (no user will ever
answer, so user-bound requests are auto-denied). The real axis is **how long until a human answers**,
and headless is that interval being infinite. The middle case is a machine running unattended while
the user checks in daily — requests are slow, not denied, and subscription auth is fine because a
visiting user can re-login. An org is headless because it was set headless, never because its host
happens to be unattended.

**No open questions remain.** ⑦'s residual curiosity (does the OAuth endpoint return a fresh refresh
token each time) blocks nothing — it only bounds how long a *subscription* org survives on a box
nobody visits, and ⑬ removes that combination for headless orgs. The suffix question was closed by
⑪, one-hub-or-several by ⑬.

### F-07 · advanced settings move into their own modal
> at this point org configuration is getting complex, advanced settings should probably be moved to
> its own separate model
>
> but don't make that implementation now, leave it as a future feature

**NOT BUILT.** Recorded on the user's instruction.

The pressure is real and measurable. The `advanced` disclosure inside the create form
(`App.tsx:583-584`) started as two switches — kiosk and sandbox, moved there in D-40 — and the
mailserver wave (F-06) adds hub addresses plus the auto-connect checkbox, headless plus its
API-key precondition, and a credential-mode selector. That is a modal's worth of decisions living
inside a disclosure inside another modal.

☞ **The supporting evidence is `docs/configuration.md`** (written 2026-08-04), a full sweep of every
knob at every level: **six levels** — process env/flags, global defaults, org settings, kiosk/sandbox
ceilings, agent defaults, per-agent scope — and F-06 would add a seventh. Anyone designing this
modal should start from that document rather than from the current form, because the form is a
partial view of the model.

Three things the design should preserve, all of which the current form gets right by accident:

1. **Creation-time vs any-time is a real distinction**, not a layout accident. Kiosk is born-with
   (`api.py:468`); everything in `Settings` (`api.py:751`) is editable later. A single modal that
   flattens the two will offer to change things that cannot be changed after creation.
2. **The clamping hierarchy should be visible.** A kiosk ceiling narrows a request silently rather
   than refusing it, and a child's scope is clamped against its parent chain. A settings surface
   that shows a value the ledger will quietly reduce is lying to the user.
3. **The collapsed summary line** (`App.tsx:577`) is the part that works — it names `kiosk`,
   `sandboxed`, and the folder count without expanding. Whatever replaces the disclosure needs an
   equivalent, or the create form loses its at-a-glance state.

### F-08 · the mobile wave
> *(added at the review, 2026-08-04, on the user's instruction: the wave joins the prospective
> features here rather than staying a standing hold in memory)*

**NOT BUILT — held by the user** ("hold off implementing until i give the go ahead",
2026-08-01, re-affirmed after an earlier release). The full spec lives at `docs/mobile-spec.md`
(carrying its own HOLD banner); three live bugs its audit surfaced were split out and already
fixed in the pre-dormancy fix batch (`35ec4eb` + follow-ups), so the spec that remains is purely
layout/interaction work. One open ruling rides with it: the compact-desk question sits in
DECISIONS.md §Open and should be answered before (or as part of) the build.

### F-09 · a working count in the org list
> add a "working count" next to live / total count in org list, to show the number of active agents
> currently working in an org at a glance. only appears if any agents are working: color-coded to
> orange, with the little spinning working arrows next to it

*(user request 2026-08-05, recorded on their instruction by 4f69f83a. **BUILT + DEPLOYED same day
(`8312093`)** — `supervisor.working_count(slug)` non-allocating `_state` read, attached per-row in
`orgs_list`, absent from the public branch; orange `.working-ct` + `cc-spin` beside the live count.
The groundwork below was followed as written.)*

**The shape.** The org list row (`App.tsx` ~:274) currently ends in one dim figure —
`{o.live}/{o.nodes} live`. The addition sits beside it, renders only when the count is non-zero, is
orange, and carries the spinning-arrows glyph the desk already uses for "thinking":
`<AutorenewIcon fontSize="inherit" className="cc-spin" />` (see `Activity` in `canvas/desk.tsx`,
and `.cc-spin` in `styles.css`). Same icon, same animation, so "working" reads identically wherever
it appears.

**Where the number has to come from — the one design question.** `busy` is NOT in the org doc. It
lives in the supervisor's in-memory `state()` dict (`st["busy"]`, set/cleared around a turn), and
today it reaches the UI only through the per-org tree payload (`api.py:775`, `node["busy"] =
st["busy"]`). The org LIST is built by `store.list_orgs()` (`store.py:320`), which reads the org
JSON files and never consults the supervisor — which is why `live`/`nodes` are doc counts and why
`cost_usd_total` had to be attached separately in `orgs_list` (`api.py`), org by org.

So the working count is attached the same way, in `orgs_list`, not in `store.list_orgs()`:
a count over that org's node ids of `supervisor.state(slug, nid)["busy"]`. Notes for whoever
writes it:

- ⚠ **`supervisor.state()` CREATES the entry it reads** (`setdefault`), so counting naively over
  every node of every org materialises a state dict per node per poll. Read the existing `_state`
  map directly instead, or add a read-only helper (`supervisor.working_count(slug)`) that does not
  allocate — the list endpoint is polled by every open tab.
- The count must be **live-only** and should agree with what the org's own canvas shows, or the
  figure will contradict the tree the moment the user clicks in.
- `queued`/`waiting` is deliberately NOT the same thing as working; the request says *currently
  working*. A node with a queued message but no running turn should not be counted (that is what
  the desk's `starting…` and the queue badge are for).
- Payload: one integer on the existing row (`working`), absent or `0` when nothing runs. The public
  kiosk branch of `orgs_list` returns early with a trimmed row — decide deliberately whether a
  visitor sees it (it leaks how busy the org is, which for a kiosk is probably fine and arguably
  useful, but it is a decision, not a default).
- Frontend types: `OrgListEntry` in `types.ts` gains `working?: number`.

**Cadence.** The org list refreshes on its own poll; a count that updates a beat behind the canvas
is acceptable and matches how `cost_usd_total` already behaves. Nothing here needs a websocket
event.

### F-10 · present a document to the user (in-page review card)
> need the ability for the agent to present documents to the user. this is different than giving a
> download link: this should be used for presenting plans and other things to them. when doing so, a
> little card should pop out the side of the agent, which when clicked, opens the document up for
> visual review in-page.

*(user request 2026-08-05, relayed via 4f69f83a's session; groundwork theirs. NOT BUILT — queued
behind the F-06 wave.)*

⚠ Not `orgtree_send_file` — that is a DOWNLOAD card (outbox/ + `/file`). This is a READING
surface: a plan reviewed in-page without leaving the canvas.

Groundwork (researcher, 2026-08-05):
- Rendering: the desk already has the markdown renderer (`md()` in `canvas/desk.tsx`) and `.md`
  styling with the D-14 table containment — the reader is mostly plumbing.
- "Pops out the side of the agent" = a card anchored to the NODE on the canvas (the credit ask
  bar's outboard-anchored shape), not a chat-stream row.
- Storage: durable + re-openable ⇒ a per-node `documents` list on the org doc (the `asks` /
  `credit_requests` pattern) — the card derives from the doc and survives reload. The chat stream
  windows at 120 rows and is the wrong home.
- Agent tool: `orgtree_present {title, body (markdown), replaces?}` mirroring `orgtree_ask`'s
  shape — parked, never blocking.

### F-11 · batched asks — multiple questions in one card
> multiple questions should be askable at once in a batch. see the attached images for how it
> looks in claude code's ui.

*(user request 2026-08-05, with reference screenshots of Claude Code's AskUserQuestion batch
form. NOT BUILT — queued behind the F-06 wave.)*

The reference (from the screenshots): ONE card holding several questions as a **tab strip**
across the top (short headers as tab labels, e.g. `Kind · Area · Images · Handoff`), the active
tab underlined; each tab shows its own question with the usual option rows (+Other); answered
tabs keep their selection when you switch back; a single **`N Submit answers`** bar at the
bottom carrying the answered-count; ✕/Esc cancels the whole batch.

Groundwork:
- `orgtree_ask` grows a `questions: [{question, header, options, multi}]` array form (1–4,
  mirroring the single-question fields; the single form stays and normalizes to a 1-batch).
- One ask entry in the ledger holds the batch; ALL answers travel as ONE user mail (per-tab
  answers labeled by header), driving one turn. Voiding/amending applies to the whole batch.
- AskCard renders the tab strip above the existing option rows (the `ask-tab` chip row is
  already there for the single header — it becomes the strip); submit disabled until every
  non-skipped tab has a selection or free text.

## D-54 · `--expose-admin` moves from argv to an environment variable
> Move exposed to an environment variable.

**Supersedes D-39's argv-only ruling** (2026-08-03), which is quoted in full in that entry: *"the
override is COMMAND-LINE ONLY, on purpose … deliberately not an env var and not a setting: env vars
get inherited by child processes and copied between machines."* The user reversed it, and the
reversal has a clear motive — F-06 §9 puts orgtree under Task Scheduler and systemd, and a service
definition sets **environment** naturally while threading an argv flag through a deploy script to a
detached process is the awkward path. The mechanism should suit the case that needs it.

`ORGTREE_EXPOSE_ADMIN`, truthy on `1` / `true` / `yes` / `on`. `sys.argv` is gone from `_admin_host`;
the loud startup wall is unchanged apart from naming the variable.

☞ **The old ruling's first objection was real, so it is now handled rather than dismissed.** Env vars
are inherited, and `clean_env()` (`supervisor.py:406`) hands every agent CLI the whole host
environment minus `CLAUDE_CODE_*` — so the variable would have ridden into every turn. It is
stripped there: whether the host is reachable off loopback is not an agent's business. The second
objection ("copied between machines") stands and is accepted, not solved.

Both deploy scripts keep `-ExposeAdmin` / `--expose-admin` as a convenience that sets the variable
for that launch, so nothing that worked yesterday stops working; the scripts no longer pass an argv
flag to the backend.

**Verified:** truthiness table across `'' 1 true YES 0 no` → correct host each time; `bash -n` and
a PowerShell tokenizer parse on both scripts; ledger suite **186/186**; pyright **0 errors**. The
`sys` import in `api.py` is still used (`/api/host`), so nothing is orphaned.

⚠ **Commit hygiene defect — `1debf4a` is a mixed commit.** It was staged with `git add -A` while a
subagent was concurrently editing the tree for D-53, so it also carries that agent's in-flight work
(`frontend/src/App.tsx`, `canvas/desk.tsx`, `types.ts`, and part of the `api.py`/`supervisor.py`
diffs — e.g. a `m.via === 'turn'` delivering-tag change that has nothing to do with this entry).
Nothing was lost and the working tree was untouched, but the commit's stat does not match its
message. **Not rewritten deliberately:** splitting hunks out of files an agent is actively writing
risks clobbering its work, which is worse than a mixed commit on a quarantined branch. Attribution
is separated when that agent reports. ☞ Rule: **never `git add -A` while a subagent is running** —
stage explicit paths.

## D-55 · ⚑ SOLVED — the vanishing message: the server hid the mail it had just drained
> when i activate the agent with a new message, sometimes the queued preview never shows up while
> the agent is 'starting', i only see my message once it actually goes through

**The remaining cause D-53 handed off, found by a dedicated Opus subagent.** D-51 and D-52 were real
and were not the whole bug: the last one is **server-side**, which is why five rounds of client-side
probing could not see it.

`delivering_mail` (`supervisor.py:778`) **excluded `via="turn"` journal batches** from
`pending_mail`, on the premise that their text is written to the CLI as a user event and so is
"already on screen as the transcript bubble". True *eventually*, not *yet*:

1. `POST /message` writes the mail and saves ⇒ it is in `pending_mail`.
2. **~60 ms** later the turn thread drains it (`_journal_drain(…, "turn")`) ⇒ out of the mailbox and
   excluded from the journal projection.
3. The CLI launches and echoes it into the transcript — D-29's "starting…" phase, ~1 s warm, longer
   cold, longer still sandboxed.

Between ② and ③ the message is in **no place the desk renders from**. The client ghost is the only
cover — and it graduates the instant any fetch lands inside that ~60 ms window, because
`serverCopies` counts `pending_mail`. ☞ **A race: lose it and the ghost survives (what every earlier
probe measured); win it and the ghost retires against evidence that is about to vanish.**
Intermittent by construction — the user's "sometimes". The window widens exactly when a turn cannot
start at once: waiting on a slot, DOC_LOCK contention, a large org doc, a container start.

**The same rule a FIFTH time** (D-34, D-43, D-50, D-51/52), one layer down: *retired against evidence
that was itself about to disappear.*

**Measured, unique token per run.** Server-side probe at 20 Hz replaying `convo.ts`'s own graduation
rule:

| build | run | ghost graduated | `pending_mail` | transcript | gap |
|---|---|---|---|---|---|
| before | 1 | t+0.014 | t+0.014 only | t+1.108 | **1.04 s** |
| before | 2 | t+1.114 | never seen | t+1.114 | 0 |
| before | 3 | t+0.026 | t+0.026 only | t+1.047 | **0.96 s** |
| after | ×4 | t+0.03–0.04 | t+0.03 → 1.02–1.37 | t+1.09–1.43 | **0 in all** |

2 of 3 before-runs reproduce, and run 2 is the same code not tripping the race — *that shape is the
symptom*.

**Fix.** The blanket `via` exclusion becomes one evidence test applied to **both** carriers — "is
this exact mail already a transcript bubble" — built in `node_chat`, the one place both halves are
in hand, so the handover lands in **one payload**: the pending bubble leaves and the durable one
arrives together, never a frame with neither. Identity, not resemblance: the marker is
`· {at}\n{body}[:400]` from the entry's own `at`, so no clock is compared and a body-only match
cannot reintroduce D-52.

**Two extras it caught on the way.** Applying the same test to `via="steer"` killed a **pre-existing
1.95–2.35 s double-render** (pendrow *and* transcript) on every send to a busy agent — never
reported, contradicting the old docstring. And D-53's lead 2 was confirmed: `MailList.onReply` never
called `addPending`, now fixed. ⚠ No matched before-measurement exists for that path (concurrency,
below); it is closed on code evidence plus an after-measurement, and it is almost certainly *not* the
user's symptom — the inbox modal renders no transcript.

**Gates on the combined tree:** ledger **186/186**, pyright **0 errors**, `tsc` clean.

**Not done:** lead 1 (stale bundle) not investigated, no build stamp added — still worth doing.
Lead 3 unmeasured at size, but the concrete defect behind it is now named: `serverCopies`'
`messages.slice(-20)` baseline can become *unreachable* if copies scroll out of the window, which
strands a ghost forever (a stuck duplicate, not a gap). Exposure is small now that graduation
happens via `pending_mail` within ~40 ms. Flagged, not claimed.

⚠ **Concurrency note.** Part of this fix (`api.py`, `App.tsx`, `types.ts`, `desk.tsx`) was already
committed by accident in `1debf4a` — see D-54. The `supervisor.py` half lands here. The agent
committed nothing itself and declined to write this entry to avoid conflicting on a file being
edited concurrently; it also redeployed :7360 to measure, and cleaned up its probe orgs
(`zz-drain-probe`, `zz-stream-probe2` deleted; list back to `game-club` + `resonite`, verified).

## D-56 · Paging happens on scroll, not on a button
> Scrolling to the bottom of the mail list to the top of a chat shouldn't show a button to load the
> extra events or mails. It should just load automatically when you reach the scroll.

Both pagers become status lines rather than controls.

- **Chat** already auto-loaded on scroll (`scrollTop < 240 && hasOlder`) — the button was pure
  redundancy. The line replacing it does something the button could not: at the API's `MAX_WINDOW`
  cap (1000) it says *"beyond the window"*, which is the only explanation for why scrolling up stops
  producing messages. `MAX_WINDOW` is now exported for that.
- **Mail list** had **no scroll handler at all**, so auto-paging there is new: within a screen of the
  bottom the next `MAIL_WINDOW` renders. `vis` only grows and is guarded against `shown.length`, so
  it cannot thrash or re-fire once the whole set is on screen.
- The status line keeps its height reserved, so the list does not jump as rows prepend. `.loadolder`
  button styling replaced with `.loadolder-status`.

`tsc` clean. ⚠ Not verified live: the running deployment carries the D-55 agent's rebuild, and
re-verifying the scroll behaviour needs a fresh build — worth a look on the next deploy.

## D-57 · The message-visibility suite — seven more defects, all reproduced first
> okay you know what i want to be absolutely certain this bug is completely squashed well and good.
> spawn a subagent to design a massive test suite purely centered around this issue … do whatever
> you have to do in order to completely destroy this bug.

**The suite was not a formality: it found seven defects, and every one was reproduced before it was
fixed.** Write-up at `docs/message-visibility-suite.md`.

**The invariant:** a sent message is on screen continuously — `pending_mail` ∪ transcript ∪ the
client ghost — **and never appears twice**. Both directions asserted, after *every* world step
rather than by a poller.

| | defect | before → after |
|---|---|---|
| ① | `_in_transcript` matched a **stripped** body against a transcript that stores it raw ⇒ any message starting with whitespace rendered twice for the whole first response (median **2.4 s**, max **137 s**) | 6/6 configs duplicated → 0 |
| ② | `pending_mail[-20:]` ⇒ the **21st queued message pushed the 1st off the payload**, after its ghost had graduated against the row that just vanished | msg #0 gone at send #21 → all 25 visible |
| ③ | `serverCopies` searched for the ghost's **full** text inside bodies the server truncates to 2000 chars ⇒ any message >2 kB duplicated until the transcript caught up — forever if queued/frozen/archived | 14 configs → 0 |
| ④ | `serverCopies`' **20-row window is smaller than a turn** (real max gap between user messages: **138 rows**) ⇒ ghost stranded for the session — this is D-53's lead 3, which D-55 flagged and did not claim | 6 configs stranded → 0 |
| ⑤ | `pop_steer` **confirmed the journal synchronously and wrote its replacement on a daemon thread** — the message in no carrier in between | 1 of 6 live runs gapped → 12/12 clean |
| ⑥ | `load_org` had **no retry where `save_org` has one** ⇒ `GET /chat` returning **HTTP 500** at random under agent load | **3 of 123 live turns (2.4 %)** → retried |
| ⑦ | a whitespace-only body crashed `post_mail` (`"".splitlines()[0]` is an IndexError) | 500 → posts |

☞ **⑥ is the one to notice.** It is not this bug family at all — the desk's own refresh was failing
outright ~2.4 % of the time while an agent worked, and nobody had reported it. `save_org` retries
`os.replace` because a reader may hold the file open; the collision is symmetric and read-only
endpoints deliberately read **outside** `DOC_LOCK` (№22), so only one side was ever defended.

**Scale actually run:** 183 hermetic checks over **187 lifecycle configurations** · **99 live turns
/ 6 824 scored payload samples** against the fake CLI · **10 real-CLI turns / 1 654 samples**
(haiku — never fable, per the user) · 4 DOM runs × 360 samples at 40 Hz.

**The rig.** `ORGTREE_CLAUDE_CLI` (`supervisor.py:174`) is the seam: `backend/tests/fakecli.js` is a
Claude Code stand-in with **programmable timing**, which turns D-55's race into a dial. Real-CLI runs
measured the true window at **~1.0–1.2 s** (cold and warm alike; 0.46–1.24 s steered) — the pending
row now covers all of it and hands over inside one 50 ms sample.
`backend/tests/msgvis.py` ports `convo.ts`'s graduation rule into Python and carries a **drift
guard**: it greps the four source files for the nine expressions it mirrors and fails loudly if any
change. `--legacy-client` restores the pre-fix client rules and still fails exactly the 20
configurations ③ and ④ describe, so those stay re-measurable without git.

**Reported, not fixed — one measured exception.** If the CLI dies *after* writing the user record
and *before* its first stdout event, `_fold_back_undelivered` re-queues a message the transcript
already shows (22 of 32 samples, indefinitely). That is at-least-once delivery working as designed,
not this family; the principled fix is to apply the same transcript-evidence test `node_chat` uses.
The suite prints it as an exception on every run rather than hiding it.

**Known-fragile, by design.** 9–10 cases print on every run where the invariant holds only because
of a CLI behaviour measured not to occur (115/115 real echoes are plain user records preceding the
first assistant record). The `fragile()` helper **requires the unreachability claim to name a
measurement**. They become live bugs the day the CLI changes.

**Not covered:** the DOM continuously (one scenario; Playwright is not a dependency) · the websocket
(polled, not subscribed) · multi-view agreement · the inbox-reply path (audited and reasoned, not
measured) · sandboxed orgs (container start stands in as a shim dial) · compaction live · beyond 800
queued mails or a 200-row burial. ☞ The durable cure for the window class is **a per-send id
threaded through the POST** — D-51 proposed it, nobody has built it.

**Gates, re-run independently by the branch owner:** ledger **186/186** · hermetic **183/183** · live
**40/40** (`--quick`) · pyright **0 errors** · `tsc` clean. Throwaway orgs deleted, org list verified
back to `game-club` + `resonite`. ⚠ `frontend/dist` was rebuilt, so the served bundle now carries
both the `convo.ts` fixes and D-56's paging changes — a page reload picks up both.

## D-58 · The five-subsystem test campaign — 40 defects, six suites, 1,097 checks
> this test suite was seemingly a great success; keep throwing more tests at it. spawn subagents to
> design and test against the full application thoroughly as much as possible.

Five agents in parallel, one subsystem each, partitioned so no two could write the same file: each
owned **one** production file for fixes and had to *report* anything outside it. Suites below are
all plain runnable scripts in `test_ledger.py` style (**pytest is still not installed**).

| suite | checks | subsystem |
|---|---|---|
| `test_ledger.py` | 186 | pre-existing |
| `test_ledger_authority.py` | 155 | authority, budgets, structure |
| `test_turn_lifecycle.py` | 131 (120 `--quick`, 49 `--hermetic`) | the turn loop |
| `test_api_surface.py` | 395 | every endpoint + the three gateways |
| `test_persistence.py` | 62 | store, locking, durability |
| `test_message_visibility.py` | 183 | D-57's suite |
| `frontend/tests/` (`npm test`) | 51 | shared state, rendering |

### ☠ Security — all reachable from the kiosk public listener

1. **Arbitrary directory listing and file read on the host.** `…/nodes/{nid}/scratch` was the only
   `/nodes/{nid}/` route that never resolved the node, so `nid` reached `scratch_dir`, which joins
   **and mkdirs** it, and the containment check then anchored on the *escaped* base.
   `nid = ..\..\..\..\..\..\Users` → `200` with a listing of the operator's home; any file under it
   read back. ⚠ Exposure at the time was nil — neither live org is a kiosk — but it was live for
   any future one.
2. **A visitor could download the org's sandbox bridge secret.** The deny list named only
   `.credentials.json`/`.claude.json`; `sandbox.py` writes `.bridge` onto the org disk. That secret
   buys the `/api/agent` gateway the public matrix explicitly freezes **and** the `/anthropic`
   proxy, which attaches the host's subscription OAuth token.
3. **Path traversal in `org_path`** — `DELETE /api/orgs/..%5Cdefaults` returned 200 and renamed
   `<data>/defaults.json` into the trash. Starlette's `[^/]+` converter admits a backslash on
   Windows. Any relative `.json` reachable from `orgs/` was a moveable target.
4. A visitor could **read-and-destroy** any node's mid-task mail queue via `steer`; the full
   OpenAPI schema and a live Swagger console were public; `frozen.error` and `last_denials[].arg`
   leaked host paths and the username.

### ☠ Data loss and wedges

5. **Mail LOST outright.** The delivery-journal confirm fired on any non-`system` stdout event —
   including a **failing** result — so a CLI answering "API Error: 500" confirmed the journal away
   and the fold-back found nothing. In **no carrier at all**; never reached the agent.
6. **A ~900-turn recursion wedge.** `_run_turn` called itself from its own `finally`. The
   `RecursionError` is raised *inside* the `finally`, escapes the turn's own `except`, kills the
   worker thread and leaves `busy=True` with a non-empty queue — silent and terminal. Measured with
   the limit lowered: 260 queued died at depth 189 with **69 still queued**.
7. **A usage-limit freeze retagged as a kiosk SPEND freeze, unresumable forever.** The pre-№41
   migration matches on shape, and a genuine limit freeze hits that shape whenever the reset time
   is unparseable *and* no replay text was kept. ▶ resume silently did nothing.
8. **One unpaired surrogate poisoned an org permanently** — every later read 500s, nothing removes
   it.
9. **Writer starvation, not a collision.** Measured **0 of 1,659** `os.replace` calls succeeding
   under an 8-reader storm, so D-57's retry was a lottery the writer always lost. A
   writer-preferring latch: **6 → 260 writes in 6 s**, errors 2 → 0.
10. **`delete_org` overwrote its own backups** — delete/recreate/delete inside one second destroyed
    the first copy, which is exactly the loss delete-as-rename was introduced to prevent.

### The rest

`hire` was **not atomic** in three ways — a *refused* hire moved 810 credits from the user's pool
with no node to hold them; `dissolve` stranded a rehired bearer's subtree live under an archived
parent; `_move` could build a **real 2-cycle** in the parent graph and produce grants of −7/−13;
`load_org` held its file handle across the whole JSON parse; every failed save leaked its temp file
forever; `save_org` never fsynced; five frontend state defects including **stale live scaffolding
that retired only on a websocket event** (lose the frame ending a turn and the reply renders twice
forever, the clock counts into later turns, an interval leaks per node).

☞ **One earlier "fix" turned out to be inert.** D-57 ④ raised `COPIES_WINDOW` 20 → 200 against a
measured 138-row burial — but `read_chat` only ever returns `CHAT_WINDOW` = **120** rows, so the
newest-200 slice *is* the whole payload and the effective window never moved. Retirement is now
evidence-based on the window's oldest `seq`.

### Two things about method worth keeping

- **`--discriminate`** (authority suite): reverts each fix one at a time in a temp copy of the
  package and re-runs the section meant to catch it. **All 14 go RED.** That is the answer to "a
  green suite proves nothing".
- **An agent declined a fix I had recommended, correctly.** The turn-lifecycle agent evaluated
  putting the transcript-evidence test inside `_fold_back_undelivered` and rejected it: the
  fold-back is the only thing that puts a consumed-but-unanswered message back where the next
  envelope re-presents it, so that change buys one clean render at the cost of the agent never
  being asked again. It recommended the display layer instead. Taken — `node_chat` now applies
  `_in_transcript` to mailbox rows too, and the check is promoted from a pinned exception.
- ⚠ **I nearly shipped a fix that broke ▶ resume.** Adding a positive `limit` kind flag (⑦) tripped
  `resume_frozen`'s "another kind owns this record" guard, making ▶ skip *every* limit-frozen agent
  — the same bug from the other end. The suite's three freeze checks caught it on the next run.

### User rulings, 2026-08-04

> both should be restricted by max depth but at the same time it should be some ludicruously high
> value like 1024, no need to have any practical limit other than to prevent infinite recursion from
> a bug that spawns unlimited subagents

**`move` now enforces `max_depth` AND `max_children`**, closing the D-A/D-B pins, and both defaults
move **10/256 → 1024**. They are runaway insurance and nothing else. The depth check measures the
**deepest leaf of the moved subtree**, not the moved node — that leaf is what actually ends up
deepest. ⚠ Read `both` as both caps binding both operations; say so if only depth was meant.

> permission mode is independent per agent because an agent's read/write/tool use access is decided
> independently of its permission mode … so theres basically no reason to audit it whatsoever.

**D-C is WON'T-FIX.** `permission_mode` stays per-agent, deliberately unclamped and unswept. The
dirs/tools/mcp grants *are* clamped against the parent chain and the kiosk ceiling, and those bound
what an agent can reach; `permission_mode` only decides how the CLI prompts within that boundary.
Pinned in the authority suite as **intended behaviour**, so a future "fix" has to argue with the
ruling rather than silently narrow it.

### Left undecided (user: "leave the rest undecided")

- **`/chat` is not scrubbed for public visitors** — an unsandboxed kiosk's transcript hands the
  operator's absolute paths and username to the internet. Scrubbing means regexing the visitor's own
  prose. Asserted as current behaviour so a change fails loudly.
- **The bridge pins the ORG, not the NODE.** One container serves every agent and all can read the
  shared `.bridge`, so a subordinate can address `/api/agent` as its own superior. Closing it needs
  a per-node credential that does not exist.
- `audience_forward` has no `_require_live` on the actor (D-D).
- Five uncapped-growth defects in `ledger.py`, each pinned as a live reproduction in
  `test_persistence.py`: `events`, per-node `mail`, per-node `notices`, `hire`'s O(n²) peer-notify
  fanout, and **the org-inbox unread count collapsing to 0** once the 200-cap trims past a
  mark-read.
- `usePolled` keeps the previous identity's data across a deps change (folder A's listing renders
  under path B). Marked `todo` in the frontend suite so it prints every run.
- **A failed turn is never retried** — the mail sits in the mailbox with no driver until the next
  message or a restart. Visible, not lost, consistent across every failure path.

### Hygiene

Throwaway orgs deleted and the org list verified back to `game-club` + `resonite` after every
agent. One flaky check fixed in passing: the crash-durability orphan sweep asserted reclamation of
strays that a SIGKILL only leaves if it lands in a microsecond window, so a clean run failed with
nothing to reclaim — it now plants one. ⚠ `frontend/dist` was rebuilt mid-campaign, so a page
reload picks up the frontend fixes and D-56's paging together.

## D-59 · Thinking lines appearing late and out of order — the live tail retired a row it had no twin for
> thinking blocks sometimes appear late or out of order, shifting messages around

**Reproduced, then fixed.** `_sweep_live` is the one place that decides what leaves the screen, and
its rule for a `thought` row was *"is there ANY sealed thinking in the last 12 durable rows?"*. Since
2026-08-02 the API seals the reasoning, so a live thought carries no text and its durable twin
carries only `thinking_sealed` — the test matched the FIRST think of the turn and retired every later
one on sight, twin or no twin.

Measured (`scratchpad/repro_thought.py`, now a suite check): think → tool A → think → tool B, polled
between steps. Thought №2 was retired while its transcript record did not yet exist; the record
landed a poll later and the line re-appeared **above** rows already on screen. That is D-50's rule —
*superseded is not replaced* — broken in a new place.

**The fix: the identity a thought lacks, its successor has.** `fold_thought` only ever banks a
thought immediately before the text/tool row that ended it, and the CLI writes its transcript in
order, so *a covered later row is proof the transcript is already past this thought*. No strings, no
clocks; it reads the order both sides already agree on.

Two more, found while writing the tests for it:

- **`text` rows retired on a MATCH, not a COUNT.** An agent that says the same thing twice in a turn
  ("done." after two edits) had its second live row retired by the first one's durable twin — the
  same defect, in the other kind that has no id. Now one retirement per durable copy, spent
  oldest-first. (This is the server-side twin of the client's `serverCopies` counting rule, D-52.)
- **Live rows were keyed on their array index.** They retire from the *middle* of the list and trim
  from the head, so an index key renames every row below the change: React remounts them and any
  open thought line collapses. Rows now carry a per-node monotonic `n` — the same fix the durable
  rows got with `seq`.

**The gap that let it ship:** twelve suites, 1,870 checks, and `_sweep_live` — the function whose
entire job is preventing this class of bug — had **zero**. New suite `backend/tests/test_live_tail.py`
(868 checks, hermetic, 20 s) asserts the invariant over the whole rendered conversation rather than
over one row's retirement:

    rendered = durable(transcript ↑ k) ++ live survivors

① no gap ② non-decreasing in step index (the reported bug) ③ no echo — checked at **every** (live
rows emitted, transcript records written) lag for every turn shape of 2–4 steps, plus sealed and
streamed reasoning, a 30-step turn (the 12-row match window slides), and a whole turn burst before
the first poll. Discriminated: 18 failures against the pre-fix thought rule, 1 against the pre-fix
text rule.

**Admitted residual, asserted rather than hidden:** a thought may render twice for one poll if the
transcript stops *exactly* on its record — its successor is what proves the transcript passed it. In
real timing that window is the milliseconds between the CLI writing the tool record and the stream
event that banks the thought, versus the ~1.5 s gap the old rule mishandled; and a brief double is
the direction D-50 chooses over a gap.

Fast tier 12/12, 2,738 checks, drift guards 3 held · 0 FIRED. `pyright` 0 errors, `tsc` clean.

## D-60 · The page refreshes itself when orgtree restarts
> add a feature that forcibly refreshes the frontend when orgtree is restarted

A redeploy replaces both halves of the app and restarts only the server one: every tab already open
keeps running the bundle it loaded, against an API that may have changed underneath it. The failure
mode is a UI that looks fine and is quietly wrong, and the fix has always been "tell them to press
refresh".

**Mechanism — one header, no new endpoint, no new poller.** `api.INSTANCE` is a
`secrets.token_hex(8)` generated at import, so it is a fresh value per process. `InstanceStamp`, a
pure-ASGI middleware on the app, adds `X-Orgtree-Instance` to every HTTP response; the client's
`req()` keeps the first id it sees and calls `location.reload()` when a different one arrives.
Detection costs nothing: the heartbeats this app already runs (tree, conversation, every open panel)
all pass through `req`, so a restart is noticed within one poll.

Details that matter:

- **Pure ASGI, not `@app.middleware("http")`.** Starlette's BaseHTTPMiddleware re-wraps the response
  body in its own StreamingResponse, and this sits in front of multi-GB virtual-disk downloads.
  Rewriting one header on `http.response.start` touches nothing else.
- **On the app, not per listener** — admin, kiosk and bridge are gateways wrapped around the same
  object, so all three inherit it. Gateway-level rejections (a bad `/k/<token>`, a bridge call with
  no secret) are answered *above* the app and are therefore unstamped: asserted as a stated property,
  since a browser only ever talks to the admin app or a valid kiosk path.
- **The header is read before the ok/not-ok split** — a restart during an outage is exactly when one
  happens, and only reading it on success would miss it.
- **`index.html` is now served `Cache-Control: no-store`.** Asset filenames are content-hashed and may
  be cached forever, but index.html is the file that *names* them; a cached copy would reload straight
  back into the old bundle and make the refresh look like it did nothing.
- **Latched.** Several responses can be in flight when the id changes; without the latch each one
  calls `reload()` on a page already tearing down.
- Deliberately unconditional, as asked — an unsent composer draft is lost, exactly as pressing F5
  loses it.

Tests: `test_api_surface.py` §10b (5 checks — the stamp on all three listeners, on error responses,
the gateway exception, the id's shape and that it encodes nothing, and the no-store on index.html;
`call()` now captures response headers) and `frontend/tests/restart.test.ts` (2 — the full lifecycle
in one ordered test, since the baseline and the latch are page-scoped module state, plus a two-sided
drift guard on the header name). The harness fetch stub now carries headers, as the real one does.

Fast tier 12/12 · 2,745 checks · guards 3 held · 0 FIRED · pyright 0 · tsc clean.

## Carried, not done

| item | state |
|------|-------|
| D-28 message rendered twice | FIXED (journal `via`) |
| D-29 ~6s blank before thinking | FIXED (derived `starting…`) |
| D-30 shared conversation store | FIXED (`convo.ts`) |
| `chain_notices` dead reserved key | REMOVED (D-33) |
| `_move` inflates a top-level grant past `max_top_grant` | FIXED (D-58) |
| `game-master` has an empty charter although `hire` now refuses without one | predates the requirement |
| P1–P5 of the state review | ALL IMPLEMENTED (D-32) |
| mobile wave | now F-08 above — spec at `docs/mobile-spec.md`, held by the user |
