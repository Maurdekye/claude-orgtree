# The message-visibility suite — and the defects it found

> *"I want to be absolutely certain this bug is completely squashed well and
> good … reproduce the situations it occurs in dozens of times in various ways
> … do whatever you have to do in order to completely destroy this bug."*

This is the write-up for a dedicated adversarial test suite built for ONE bug
family — the one that has now produced nine docket entries (D-34, D-43, D-50,
D-51, D-52, D-55, D-57, D-59, **D-139**), each of which looked like a different
bug and was the same rule broken: **something on screen was retired before its
replacement existed, or not retired once its replacement arrived.**

> ### ⚠ Read §6 first if you are here because it came back
>
> §§1–5 describe the state of this fight on **2026-08-04**. The family produced
> another instance on **2026-08-19** (D-139) — *"messages being briefly
> duplicated for less than a second during the transition from an unconfirmed
> to a confirmed message"* — and the suite below, all 183 green checks of it,
> did not see it coming. §6 is what happened, why the guards held while the
> thing they guarded moved, and what the suite looks like now (**274 checks,
> 273 lifecycle configurations**). Where §§1–5 and §6 disagree, §6 is
> current.

---

## 1. The invariant

    A message the user sent is on screen CONTINUOUSLY from the moment it is
    sent until the conversation ends, and it NEVER appears twice.

"On screen" is the union of the three carriers an agent desk renders a user
message from — and every instance of this bug family has lived in a hand-off
between two of them:

| # | carrier | where it comes from | retired by |
|---|---|---|---|
| ① | transcript bubble | `chat.messages[]`, `role == 'user'` | never (it is the record) |
| ② | pending row (`pendrow`) | `chat.pending_mail[]`, `from == '@user'` | the mail leaving the mailbox AND the journal |
| ③ | optimistic ghost | `convo.pending[]` (client only) | `refreshConvo`'s graduation check |

② is itself two server-side sources fused in `api.node_chat`: the node's
mailbox (`org.d["mail"]`) and the delivery journal (`org.d["delivering"]`,
projected by `supervisor.delivering_mail`).

So for one message, at any instant,

    renders = ①hits + ②hits + ③hits          and the invariant is  renders == 1
    renders == 0  →  a GAP        (the message vanished)
    renders >= 2  →  a DUPLICATE  (D-55 found a 1.95–2.35 s one nobody reported)

**Both directions are asserted everywhere.** A suite that only looked for gaps
would have missed four of the six defects below.

## 2. Shape of the suite

Three files, plus a shared model.

| file | what it is | how to run |
|---|---|---|
| `backend/tests/msgvis.py` | the shared model: a faithful port of `convo.ts`'s `serverCopies`/`addPending`/graduation and `desk.tsx`'s render union, the text corpus, a transcript writer, and a drift guard | imported |
| `backend/tests/test_message_visibility.py` | **hermetic**: real server code (`api.node_chat`, `supervisor.delivering_mail`/`read_chat`/`_journal_drain`/`_confirm_delivered`/`pop_steer`, the real ledger, real org docs on disk) driven step by step | `.venv/Scripts/python.exe backend/tests/test_message_visibility.py` |
| `backend/tests/fakecli.js` | a Claude Code CLI stand-in with **programmable timing** — the substitution the supervisor already allows through `ORGTREE_CLAUDE_CLI` | used by the live suite |
| `backend/tests/test_message_visibility_live.py` | **live**: a real uvicorn backend on a throwaway port + data dir, real turn loop, real threads, and a 20 Hz poller that sees only what a browser sees | `… test_message_visibility_live.py [--quick] [--reps N] [--real-cli]` |

Style matches `test_ledger.py` — plain runnable scripts, `ok N` lines, an
`ALL N CHECKS PASS` line, no pytest (still not installed, still not added).

**Why the client rules are ported into Python.** The rule that retires ③ lives
in the browser; the evidence it retires against is served by Python. Neither
half can be judged alone — D-51, D-52 and D-55 were all failures of the *seam*.
The port is guarded: `assert_client_model_matches_source()` greps `convo.ts`,
`desk.tsx`, `api.py` and `supervisor.py` for the nine expressions it mirrors
and fails loudly if any of them changes, so the suite cannot quietly become a
test of a fiction.

**Why a fake CLI.** D-55's race is the gap between "orgtree drained the mail
into a turn" and "the CLI echoed it into its transcript". Against the real CLI
that gap is whatever it happens to be, and the race is won or lost by luck —
which is why five rounds of client-side probing could not pin it down. In the
shim it is a **number**, swept across the danger zone and repeated. The shim
also runs the real PostToolUse steering hook and reproduces the CLI's own
record shapes, including the `type:"attachment"` record that hook context
actually lands in.

**Coverage actually run**, on the fixed tree:

| layer | result |
|---|---|
| hermetic | **183 checks pass, 0 fail**, 10 known-fragile, over 187 lifecycle configurations (20 text variants × carrier × attachments × batch shape × transcript size × polling density × agent state × hazardous ordering) |
| hermetic, `--legacy-client` | **20 fail** — the two client-side fixes, re-measured against the pre-fix rules |
| live, fake CLI | **70 checks pass, 0 fail**, 9 known-fragile, 1 measured exception, over **99 live turns / 6 824 scored payload samples** |
| live, real CLI | **8 checks pass**, 10 real turns, 1 654 samples, 0 gaps, 0 duplicates |
| DOM (Playwright, scratchpad) | 4 runs × 360 samples at 40 Hz, 0 gaps, 0 duplicates |

Plus targeted re-runs while chasing individual defects: 12 steer repetitions,
28 window repetitions, and a pre-fix comparison run for the steer path.

### The real CLI, where the shim cannot answer

`--real-cli` runs the same probe against the pinned Claude Code binary on
haiku (never fable — explicit ruling). Ten real turns, 1 654 scored samples,
**zero gaps and zero duplicates**, and the hand-off measured directly:

| run | pending row | transcript from | overlap | gap |
|---|---|---|---|---|
| cold start ×3 | t+0.05 → 1.03–1.08 s | 1.08–1.13 s | 0.00 s | 0.00 s |
| warm, same session ×4 | t+0.05 → 0.98–1.13 s | 1.03–1.18 s | 0.00 s | 0.00 s |
| steered mid-task ×2 | t+0.05 → 0.46–1.24 s | 0.51–1.29 s | 0.00 s | 0.00 s |
| 6 kB message | t+0.05 → 1.03 s | 1.08 s | 0.00 s | 0.00 s |

So the real drain→echo window — the hole D-55 closed — is **~1.0–1.2 s wide**,
and the pending row now covers all of it, handing over inside a single 50 ms
sample every time. This also answers the fragility question the shim raises:
across 1 654 samples the confirming stdout event never beat the CLI's own
transcript write (a hole under 50 ms could still hide between samples).

⚠ Two things `--real-cli` needs that the fake path does not, both learned the
hard way and both now encoded: the **real HOME** (credentials live in
`~/.claude`; a redirected one produces turns that die in 1.5 s and look like a
pass) and an **explicit `ORGTREE_CLAUDE`** (the pinned install is found under
the DATA root, which the rig redirects, so resolution falls through to the npm
`claude.CMD` shim — launched via `cmd /c`, which truncates argv at the
multiline identity prompt). A run whose turns produce no transcript at all now
fails loudly instead of passing.

### The DOM, once

A separate Playwright probe (scratchpad, not part of the suite — Playwright is
not a dependency) samples the real desk at 40 Hz and counts DOM rows containing
the token, checking each one's rect against the `.msgs` scrollport. Four runs ×
360 samples: **0 gaps, 0 duplicates**, and the carrier trace reads
`ghost → pendrow → transcript`, exactly one at a time — which is also the
strongest available check that the Python port of the client rules is faithful.

## 3. The six defects found

Each was reproduced before it was fixed, and each fix has an
apples-to-apples after-measurement.

### ① `_in_transcript` stripped a body the transcript never strips — a permanent duplicate

`api.py`'s D-55 marker is `· {at}\n{body}[:400]`, built from `body.strip()`,
but `_mail_block` writes the body **raw**. Any message whose body begins with
whitespace therefore never matched its own transcript bubble, so the pending
row stayed up beside the durable one for the whole of the turn's first
response — measured on 94 real transcripts: median **2.4 s**, maximum
**137 s**. The composer trims; nothing else does (the API takes `body.text` as
sent, and agent mail routinely opens with a newline).

*Before:* 6 of 6 whitespace-leading corpus configurations duplicated.
*After:* 0. Fix: use the raw body; strip only for the emptiness guard, and only
where there is no `at` to identify the entry.

### ② `pending_mail[-20:]` — the 21st queued message pushed the 1st off the screen

`node_chat` truncated the pending list to the last 20 rows. A user who queues
21 messages at a busy agent loses the first one from the payload — and its
ghost graduated long ago, against the very row that just vanished.

*Before:* burst of 25 → message #0 gone at send #21, never to return until a
turn drains it. *After:* all 25 visible. Fix: no row cap; the payload is
bounded by shrinking bodies in tiers instead (2000 / 800 / 250 chars), with an
800-row backstop because the live mailbox is uncapped.

### ③ A message over 2000 characters never graduated its ghost — duplicate until the transcript caught up, forever if it never ran

The payload truncates pending bodies to 2000 chars; `serverCopies` looked for
the ghost's **full** text inside them. A full-length needle can never occur in
a truncated haystack, so the ghost never retired against `pending_mail` and sat
on screen beside its own pending bubble.

*Before:* 14 configurations duplicated (and indefinitely for a
queued/frozen/archived recipient). *After:* 0. Fix: `serverCopies` matches a
bounded 200-character needle, which is kept safely under the server's smallest
body cap by a drift-guarded contract.

### ④ `serverCopies`' 20-row window is smaller than a turn — a ghost stranded for the session

Graduation counted within `messages.slice(-20)`. Measured over 94 real
transcripts, consecutive user messages are typically 5 rendered rows apart —
but p90 is 14 and the maximum is **138**. Once the copy is buried deeper than
the window, the count can never rise again and the ghost is stranded, showing
the message twice for the rest of the session. This is D-55's own "lead 3",
flagged and unclaimed; it is now reproduced and closed.

*Before:* 6 sparse-poll configurations stranded the ghost (a desk that misses
the intermediate fetches — a dropped websocket, or simply the 2.5 s/7 s
heartbeat). *After:* 0. Fix: window 20 → 200. Still a newest-**n** slice on
purpose, so paging older messages in cannot graduate a live ghost early.

### ⑤ `pop_steer` confirmed the journal, then wrote its replacement on another thread — a real gap

Mid-task mail rides hook context, which the CLI records as a
`type:"attachment"` record `read_chat` cannot render (verified across 94
transcripts: 9 injections, all attachments). The `steered_log` is therefore the
message's only durable home once the journal batch is confirmed — and the
confirm was **synchronous** while the log was written by a **daemon thread**.
Between the two the message was in no carrier at all, and `save_org` retries
`os.replace` for up to 2.1 s under reader contention, so the hole is not
theoretical.

*Before (live, pre-fix code restored):* **1 of 6 runs** showed a genuine gap
sample. *After:* **12 of 12 clean**. Fix: one load-modify-save under one lock —
the pending row leaves and the steered row arrives in the same payload. It is
also strictly cheaper than what it replaced (one doc write where there were
two), which answers the "the hot path must never wait on a doc save" note that
put the record off-thread in the first place.

### ⑥ `load_org` had no retry, so a routine poll could 500

`save_org` retries `os.replace` for up to 2.1 s because a reader may hold the
file open. The collision is symmetric and only one side was defended: opening
the destination while a replace is in flight raises `PermissionError` on
Windows, and read-only endpoints deliberately read *outside* `DOC_LOCK` (№22).

*Measured:* **3 of 123 live turns** had a `GET …/chat` come back **HTTP 500** —
the desk's own refresh, failing at random while an agent worked. Fix: the same
bounded backoff `save_org` already uses.

### Summary of the fixes

| # | file | before | after |
|---|---|---|---|
| ① | `api.py` `_in_transcript` | 6/6 whitespace-leading configs duplicated | 0 |
| ② | `api.py` `node_chat` pending cap | message #0 gone at send #21 | all 25 visible |
| ③ | `convo.ts` `serverCopies` needle | 14 long-message configs duplicated | 0 |
| ④ | `convo.ts` `serverCopies` window | 6 sparse-poll configs stranded a ghost | 0 |
| ⑤ | `supervisor.py` `pop_steer` | 1 of 6 live runs showed a gap | 12/12 clean |
| ⑥ | `store.py` `load_org` | 3 of 123 live turns 500ed a `/chat` poll | retried |
| ⑦ | `ledger.py` `post_mail` | whitespace-only body → `IndexError`, 500 | posts |

③ and ④ can be re-measured against the pre-fix client rules without touching
git: `test_message_visibility.py --legacy-client` restores the old window and
needle in the port and fails exactly those 20 configurations.

### ⑦ (bonus, adjacent) a whitespace-only mail body crashed `post_mail`

`body.strip().splitlines()[0]` — `"".splitlines()` is the empty list, so the
whole send raised `IndexError` and 500ed. Reachable from any client that does
not trim, and from agent mail. One-line fix.

## 4. Running it

```
# hermetic — seconds, no processes, no network, no browser
.venv/Scripts/python.exe backend/tests/test_message_visibility.py
.venv/Scripts/python.exe backend/tests/test_message_visibility.py --legacy-client
.venv/Scripts/python.exe backend/tests/test_message_visibility.py --only "steer ·"

# live, fake CLI — minutes; own port, own data dir, own HOME, orgs deleted
.venv/Scripts/python.exe backend/tests/test_message_visibility_live.py --quick
.venv/Scripts/python.exe backend/tests/test_message_visibility_live.py --reps 5
.venv/Scripts/python.exe backend/tests/test_message_visibility_live.py --keep --only window

# live, REAL CLI — real turns, real cost, haiku only (never fable)
.venv/Scripts/python.exe backend/tests/test_message_visibility_live.py --real-cli
```

A failed live run keeps its rig (`--keep` forces it) and prints the path; the
backend log inside it is where a 500 traceback lives.

## 5. What the suite does NOT cover

Stated plainly, because a suite that finds nothing is either excellent news or
a weak suite and the difference has to be visible.

- **The DOM.** The suite scores the `/chat` payload plus a *port* of the client
  rules, not React's output. The port is drift-guarded, and a separate
  Playwright probe confirms the DOM agrees for one scenario, but nothing
  asserts the DOM continuously. A CSS or render-order change could hide a row
  the payload contains and nothing here would notice.
- **The websocket.** The probe polls; it does not subscribe. `ingestStream`'s
  `steered` frame (which drops matching ghosts) is modelled, not received.
- **Multi-view agreement.** One desk per node is modelled. Two mounted views of
  the same node share `convo.ts`'s store *by construction*, so this is thin
  cover rather than no cover — but the switchboard/desk pair is not asserted.
- **The inbox modal.** `MailList.onReply` creates a ghost and never refreshes
  on success (`App.tsx`), so it retires on the target node's own poller. That
  cannot break the invariant (the ghost is the only carrier until a payload
  arrives, and the payload that shows the mail is the one that retires it), and
  a code audit confirms every error path drops the ghost — but it is reasoned,
  not measured.
- **Sandboxed orgs.** Not exercised: the rig runs host-mode only. The container
  start is exactly the "turn cannot start immediately" condition that widens
  the window, and it is covered only by the shim's `startMs` dial standing in
  for it.
- **Compaction mid-flight** is covered hermetically (a boundary between drain
  and echo) but never live.
- **Beyond 800 queued mails**, the oldest pending rows still fall off, and
  beyond a 200-row burial a ghost can still strand. Both are outside anything
  measured in the real corpus; the durable cure for the second is a per-send
  id threaded through the POST, which D-51 proposed first and which nobody has
  built.
- **Ten known-fragile cases** are reported by the hermetic suite on every run.
  They break the invariant under a precondition that a measurement says does
  not currently occur — chiefly "the CLI writes the turn's user event as a
  record `read_chat` skips" and "the confirming stdout event beats the
  transcript write". Both are facts about the CLI, not about orgtree, and both
  become live bugs the day the CLI changes. The `fragile()` helper requires the
  unreachability claim to name a measurement; anything weaker is a failure.
- **One measured exception**, reported as such: if the CLI dies *after* writing
  the user record and *before* its first non-`system` stdout event, the
  unconfirmed batch folds back into the mailbox while the transcript already
  shows it, and the desk renders the message twice indefinitely. That is
  at-least-once delivery working as designed (`_confirm_delivered`'s C1 rule),
  not this bug family — but it is a real duplicate, so it is measured rather
  than assumed away. The principled fix, if it is ever wanted, is for
  `_fold_back_undelivered` to ask whether the transcript already carries the
  batch, the same evidence test `node_chat` applies.


---

## 6. It came back — D-139, 2026-08-19

> *"the issue with messages appearing twice is back, with messages being
> briefly duplicated for less than a second during the transition from an
> unconfirmed to a confirmed message. make sure this class of bug is
> completely eradicated from the system."*

### 6.1 The defect

`api.node_chat._in_transcript(m)` is the evidence test that retires a pending
row — carrier ② — once the transcript carries the same mail. It read:

```python
mark = (f"· {at}\n{body}" if at else body)[:400]
return any(mark in t for t in _seen_user)
```

That is a **hand-rebuilt copy of what `supervisor._mail_block` writes**, living
three files away from it, and it is correct only for as long as the formatter
puts the body immediately after the entry's timestamp. Two features shipped
after 2026-08-04 moved it:

| feature | what it inserts | consequence |
|---|---|---|
| **FR-05 `reply_to`** — a reply sent from the inbox modal (`App.tsx onReply`) | `↩ IN REPLY TO ⟨who⟩'s message of ⟨at⟩: “⟨gist⟩”` between header and body | the needle cannot occur in the transcript |
| **D-137 `kind: "notice"`** | the header itself runs on: `· ⟨at⟩ — informational, delivered passively; no reply is expected` | same |

For those shapes the entry was never "on screen", so the pending row stayed up
**beside its own durable bubble**, from the CLI's echo until
`_confirm_delivered` dropped the journal batch. That window is short — which
is exactly the *"less than a second"* the report describes, and why it read as
a flicker at the unconfirmed→confirmed transition rather than as the permanent
duplicate D-57 ① produced.

Measured before the fix, against the spanning shape list: **7 of 21 shapes
never matched their own transcript bubble** — every `reply_to` variant and
every `notice` variant. With the pre-fix rule restored in place
(`--legacy-marker`), **63 checks fail**, of which 60 are FR-05 reply
configurations failing as `DUPLICATE … after step 'transcript echo'`.

### 6.2 Why 183 green checks did not see it

Three separate guards were pointed at this seam and all three held while the
thing they guarded moved:

- **The drift grep** pinned `api.py`'s marker expression. The marker never
  changed — the **formatter** did. A one-sided grep on a two-sided contract
  proves only that one side stands still.
- **`msgvis.mail_block`** is a deliberately *independent re-implementation* of
  `_mail_block`, on the stated reasoning that importing the real one "would
  make a formatter change invisible to the very test that exists to catch a
  formatter/marker mismatch". Sound — but an independent copy is only sound
  while something pins it, and nothing did. The real formatter grew two
  branches; the copy kept writing the 2026-08-04 envelope; every echo and
  hand-over check went on testing an envelope the CLI no longer produces.
- **The corpus** varies the *text* of a message exhaustively (20 variants) and
  the *shape of the mail entry* not at all. `reply_to` and `kind` were never
  set by any scenario.

☞ The generalisable lesson, and the reason §6.3 is shaped the way it is: **a
structural guard proves a string still exists; only running both sides proves
they still agree.**

### 6.3 The fix

**The rule moved next to the formatter and now builds its needle by running
it.** `_mail_block` was split so that `_mail_entry_block(m)` renders exactly
one entry, and `supervisor.mail_in_transcript(m, seen)` renders a *probe* of
the entry through that same function. There is no copy to drift.

There are **two branches**, and the second one is easy to forget — dropping it
is a GAP, and it went untested for a whole review round:

| body | probe | test |
|---|---|---|
| longer than `MAIL_MARK_CHARS` (400) | body cut to 400, attachment lines dropped — both cuts at the **end** of the block | the needle is a contiguous **prefix** of what the envelope carried, so one plain substring test settles it |
| 400 or shorter — nearly all mail | the entry **verbatim**, attachments and all | the needle is the **whole block**, so a plain substring test is not enough: it must be found followed by `MAIL_SEP` or `MAIL_TAIL`, the two things the wrapper writes after a complete entry |

⚠ **Why the whole-block branch needs the boundary.** Without it, an entry whose
body is a *prefix* of another's — with everything else about them colliding,
sender, kind and millisecond timestamp — is reported on screen using the
longer one's bubble, and its pending row retires for a message nobody can see.
`MAIL_SEP`/`MAIL_TAIL` are the constants `_mail_block` itself joins with, never
a copy of them. Reverting this branch to a bare substring test left all 268
checks green until a prefix-body negative was added to the marker contract
(redteam, 2026-08-19); do not remove it without reading §6.7.

⚠ **The obvious repair was tried first and is weaker than what it replaced.**
Asking for the timestamp `· {at}` and the body head as two *independent*
needles makes the layout between them irrelevant — but a transcript row is a
whole drained **batch**, several entries joined by `\n---\n`, so two
independent needles can be satisfied by two *different* mails in one row. That
retires a pending row whose message is on screen nowhere: a **GAP**, which
this system ranks strictly worse than a duplicate. Caught by the redteam
before it shipped, and the reason the needle stays contiguous.

### 6.4 The guards that would have caught it

All three RUN the real functions. They live in `msgvis.py` and are checks 2–4
of the suite, immediately after the source-contract grep.

| guard | what it asserts | mutation it was verified against |
|---|---|---|
| `assert_mail_shapes_span` | `mail_shapes()` reaches **every executable line** of `_mail_entry_block` (`sys.settrace` + `dis.findlinestarts`) | a synthetic third header branch → *"does not span … 1 line(s) never ran"* |
| `assert_mail_block_matches_source` | the suite's independent formatter copy renders **byte-for-byte** what `_mail_block` renders, per shape and batched | `· message ·` → `· msg ·` in the real formatter → caught |
| `assert_mail_marker_contract` | every shape is found in its own rendering, **alone and batched**, and never in another entry's | the two-needle rule of §6.3 → *"within ONE batched row, an entry matched using one block's header and another block's body"* |

⚠ **The shape list they iterate is itself crossed with the truncation axis**
(`mail_shapes_crossed`), and that is not cosmetic. Below `MAIL_MARK_CHARS` the
needle *is* the whole block, so the probe rendering and the real rendering are
byte-identical and any change to what the formatter writes **after** the body
is invisible. The suite crossed long bodies with the plain, attachment and
reply branches and never with the notice branch — so one extra line under a
notice's body broke the invariant for every notice over 400 characters while
all 268 checks stayed green. Each of the 21 shapes therefore gets a
past-the-budget twin, **and its own timestamp**: they used to share one, and
because the header carries only `from`/`relationship`/`kind`/`at`, several
shapes rendered blocks that were prefixes of one another, so their batched
assertions were satisfied by a *sibling* entry and could not fail — 4 of 21
under the rule in force at the time (re-measured 2026-08-20). Only one of
those four is an exact duplicate rendering — 21 shapes produce 20 distinct
blocks — the other three were *prefix containments*, which the bare `in` rule
of the day accepted and the boundary rule since does not. Both found by the
redteam, 2026-08-19, in the checks written that same day to close the previous
round's holes.

`assert_mail_shapes_span` is the guard on the guards: the other two iterate a
hand-maintained list, and a formatter branch nobody wrote a shape for is
invisible to both — which is precisely how FR-05 and D-137 got through. Line
coverage is the one statement of "spanning" a future author cannot forget to
update.

Alongside them: `mail_shapes()` (one entry per formatter branch, crossed with
the fields that move the body), two carrier checks that name the two broken
shapes directly (`a NOTICE hands over…`, `a REPLY hands over…`, each asserting
the mechanism *ran* before asserting it stopped), and an 80-configuration
`lifecycle × FR-05 reply snapshots` axis.

**`--legacy-marker`** restores the pre-fix rule in place, the same
apples-to-apples switch `--legacy-client` gives for the client half.

### 6.5 Also fixed, same rule, one file over

`supervisor._sweep_live.covered()` ended in a bare `return True`: a live row of
an unrecognised `kind` was retired on its first sweep **naming no evidence at
all**, against D-50's rule that every retirement names what it retired
against. Nothing pushes such a kind today (`live_row` is only ever called with
`text`, `tool` and `thought`, and `live` is in-memory so there are no legacy
rows), so this was a latent hazard rather than a live bug — but it is the same
rule, and the safe default is to keep the row and let the chronology backstop
retire it a poll later on evidence that is real.

### 6.6 Current numbers

| run | result |
|---|---|
| hermetic | **274 checks pass, 0 fail**, 10 known-fragile, 273 lifecycle configurations |
| hermetic, `--legacy-marker` | **63 fail** — the D-139 fix, re-measured against the pre-fix server rule |
| hermetic, `--legacy-client` | **25 fail** — the D-57 ③④ fixes, re-measured against the pre-fix client rules |
| `pyright` | 0 errors, 0 warnings, 0 informations |
| `test_live_tail.py` | 873/873 — **unchanged by §6.5**, and that is the honest reading: the branch it hardens is unreachable, so nothing scores it either way (verified by reverting the flip and re-running) |

### 6.7 What §5 should now also say it does not cover

- **The formatter's *readers*, other than the evidence test.** `desk.tsx`'s
  `stripEnvelope` does not know D-137's `NOTICE FROM …` header and renders it
  as body text, and `userTurns`' `^FROM @user \(` regex would miss a
  user-authored notice. Both are cosmetic — no effect on the invariant — but
  they are the same drift, and nothing pins them.
- **The envelope's grammar is ambiguous, and no reader can fix that.** Nothing
  distinguishes an entry separator the wrapper wrote from a markdown rule an
  author typed in a body, so the whole-block boundary test can be satisfied
  from inside a longer body; and two bodies agreeing for their first 400
  characters take the prefix branch, where no boundary exists at all. Both
  were built against the helper; neither could be reached through the API.
  Closing them properly means giving the envelope an unambiguous entry
  delimiter — which changes what agents read, and is a bigger decision than
  this fix.

  ☞ **Why they are safe is ORDERING, not timestamp uniqueness.** This is a
  correction, recorded because the weaker argument was written down first and
  a future maintainer would otherwise inherit it. A body can carry another
  entry's exact `at` by **quoting its block** — a user pastes a pending
  mail's envelope into the next message — with no clock collision involved,
  so "two entries never share a timestamp" was never the barrier. What is:
  the needle embeds the victim's own timestamp, so any row carrying the
  victim's block must postdate the victim's mail, and `take_mail` pops the
  **whole** mailbox — the quoting mail and the mail it quotes drain into one
  envelope and land in **one** transcript row. There is no instant at which
  the quoter is echoed and the quoted is not, and fold-back opens none
  either (it re-inserts into the same mailbox, which drains wholesale again).
  Built end-to-end on 2026-08-20: two distinct timestamps, the attacker's
  body containing the victim's entire block, one drain taking both → the
  victim on screen, no gap. Raised by the cross-model sign-off; this argument
  survives a future in which timestamps *do* collide.
- **`pop_steer`'s 100 KB cut.** `steered_log` stores the steer text at
  `[:100000]`, so a ~100 KB envelope can be cut such that a complete entry
  block survives with its trailing boundary removed; that entry's pending row
  then does not retire until the journal is confirmed. Measured by the
  redteam; it lands in the duplicate direction and the same input already lost
  every entry past the cut under both the old rule and the new one.
- **Timestamp collisions.** `ledger.now()` is millisecond-resolution and
  back-to-back calls collide ~99.8% of the time; no current call site posts
  twice to one node inside a single load/save (measured 0/40 by one review
  round, 0/120 by another — same result, different sample), so two entries
  never share an `at` today. Nothing states or enforces that. Per the
  ordering note above this is no longer what the needle's safety rests on,
  but a future batch send should still not assume it stays true.
