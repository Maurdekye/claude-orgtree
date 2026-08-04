# The message-visibility suite — and the six defects it found

> *"I want to be absolutely certain this bug is completely squashed well and
> good … reproduce the situations it occurs in dozens of times in various ways
> … do whatever you have to do in order to completely destroy this bug."*

This is the write-up for a dedicated adversarial test suite built for ONE bug
family — the one that has now produced six docket entries (D-34, D-43, D-50,
D-51, D-52, D-55), each of which looked like a different bug and was the same
rule broken: **something on screen was retired before its replacement existed.**

⚠ **Not committed.** The branch belongs to another session; the files are left
in the working tree. Whoever owns the branch should fold the entry below into
`docs/interim-docket.md` as D-57 rather than take this file as the home for it.

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
