# State architecture review — why the bugs feel like whack-a-mole

**Status: FINDINGS ONLY. Nothing in this document has been implemented.** The user asked for an
investigation and explicitly ruled *"investigate further, change nothing yet"* (2026-08-02). Every
proposal in §5 awaits a ruling from the user and the implementer.

Written by the secondary session (4f69f83a) while holding the interim implementer seat. It sits on
branch `interim-authority`, not `main`.

The prompt for it was the user's own observation:

> this whole project is beginning to become a rug-pushing exercise: fix one bug and two other old
> ones pop back up, back and forth. gives me the feeling that some architectural decision is
> standing in the way of things working as intended.

followed by a direction:

> try to prioritize storing less state and deriving it more. this ... reduces performance but
> reduces stale state issues that we keep seeing.

The investigation says the observation is correct, and names the decision. It also **corrects a
claim this session made earlier today** (§2) and **fails to reproduce** one of the reported bugs
(§3) — both recorded here because a review that only confirms itself is worthless.

---

## 1. How this was measured

Everything below is measurement, not reading. Reproduce with:

- **Transcript census** — walk every node's `.jsonl` via `supervisor.transcript_path()`, count
  content blocks by type. Covers 4 orgs / 7 nodes / ~600 blocks.
- **Headless CLI probe** — spawn the pinned CLI with the supervisor's exact argv
  (`-p --output-format stream-json --input-format stream-json --include-partial-messages
  --verbose --model … [--effort …]`) and log every event with a timestamp. This is the only way to
  see what orgtree actually receives.
- **Live UI observation** — Playwright against the running app at 150 ms resolution, with
  `JSON.parse` monkey-patched in the page to count the websocket frames the browser *received*, so
  received-vs-rendered can be compared directly.
- **Isolation** — a throwaway org (`zz-stream-probe`, 3 haiku agents) created and deleted for the
  live runs. No user org was written to. Where a UI state had to be forced, the API response was
  rewritten in flight rather than mutating org settings.

---

## 2. CORRECTION: thinking text is withheld **per tier**, not universally

Earlier today this session told the user, with confidence, that the API withholds thinking
plaintext from every model. **That was wrong**, and the error was mine: the opus samples were
real, the sonnet probe returned *no thinking blocks at all* for a trivial prompt, and I folded that
absence into the same conclusion instead of treating it as no data.

Full census, every node with any thinking block:

| org/node                | tier   | thinking WITH text | sealed (signature only) |
|-------------------------|--------|-------------------:|------------------------:|
| arti/helper             | opus   |                  0 |                      19 |
| game-club/game-master   | sonnet |                  0 |                      41 |
| game-club/hex-red       | sonnet |                  0 |                       3 |
| game-club/hex-blue      | sonnet |                  0 |                       2 |
| zz-stream-probe/probe   | haiku  |                  4 |                       0 |
| zz-stream-probe/probe2  | haiku  |                  1 |                       0 |
| zz-stream-probe/probe3  | haiku  |                  1 |                       0 |

The correlation is total and has no exceptions: **haiku returns reasoning in plaintext; opus and
sonnet return it encrypted** (`{"signature": …, "thinking": ""}`). An interactive Claude Code
session on opus behaves the same — 523 blocks, all sealed — even though the client displays the
reasoning on screen, so the withholding is of the *recorded and streamed* copy, not of the model's
output.

Consequences for the code as it now stands (`7ad36b6`):

- The sealed-thought marker (`thought for 12s`, no expander) is the **opus/sonnet** path.
- The expandable `ThoughtLine` is still live and still correct — observed rendering
  `thought for 2s ▸` for haiku agents in the switchboard.
- Both paths were needed. Neither is dead code. But the *explanation* given to the user
  ("Anthropic withholds thinking") was too broad, and this table is the accurate version.

**Not verified:** whether this is a recent change. The oldest transcript on this machine (game-club,
2026-07-29) is already sealed, so there is no before-state to compare against. Any claim about
*when* it started is unsupported.

---

## 3. NOT REPRODUCED: "responses don't appear until after the first tool call"

Reported 2026-08-02, against the switchboard. Two live runs, both against real turns:

**Run 1 — one agent.** Turn starts at 0.92 s. First streamed text at **7.30 s**. First tool chip at
**9.58 s**. Text preceded the tool by 2.3 s.

**Run 2 — three agents streaming concurrently** (the switchboard's actual condition, and the case
where the per-node event map could coalesce). Websocket frames received vs rendered:

| node    | delta frames received | max draft chars rendered |
|---------|----------------------:|-------------------------:|
| probe   |                    10 |                      714 |
| probe2  |                    10 |                      696 |
| probe3  |                    11 |                      712 |

No dropped deltas; all three panels showed prose *before* their tool chips. **The reported symptom
did not occur in either run.**

What the runs *did* show is a **6.4-second window where the panel says "working" and displays
nothing at all**. That is almost certainly the real phenomenon, and §2 explains why it recently got
worse: that gap used to be filled by the live thinking ribbon streaming its text. On opus and
sonnet the ribbon now has nothing to stream, so the first thing that ever appears in the panel is
whatever comes after the thinking — very often a tool call, because an agent that thinks and then
acts produces `thinking → tool_use` with no text block in between. The user's description is then
an exact account of what they saw; the cause is §2, not the streaming path.

**Cheap candidate fix (not implemented):** the sealed-thought marker exists only in the transcript
path, so it appears at the next refresh, not during the gap. Emitting a live marker when
`thinking_delta` arrives *with empty text* would put a "thinking…" indication in that window
immediately. This is a display-only change in `supervisor._run_turn` + the desk's stream effect.

**Caveats.** Both runs used haiku (cheapest, and the throwaway org was disposable). Haiku's thinking
is *not* sealed, so run 1's 6.4 s gap is the floor — an opus agent's gap is longer. A confirming run
on opus was not done. Nor was a run with an agent that opens with a tool call and no preamble.

---

## 4. THE ARCHITECTURE: one decision, three instances

The recurring shape is: **state the server already owns is copied into a second place, and every
bug is the copy diverging from the source.** Ranked by damage.

### ① The desk keeps a parallel copy of the conversation

The transcript is authoritative. The desk *simultaneously* maintains `live_feed`, `draft`,
`thinking`, `thinkBuf` and `pending` — a second rendering of the same events. Both render at once,
so they must be de-duplicated, and the de-duplication is a **heuristic**
(`frontend/src/canvas/desk.tsx`, `covered()`):

| live row kind | matched against the last 12 transcript messages by |
|---------------|----------------------------------------------------|
| `text`        | `m.text.startsWith(r.text.slice(0, 300))`           |
| `tool`        | `r.text === t.name \|\| r.text === name + ' · ' + arg` |
| `steered`     | `m.text.includes(r.text.slice(0, 200))`             |
| `thought`     | `m.thinking.includes(r.text.slice(0, 120))`         |

plus a hard rule: `now - r._at < 5000` — the row is dropped after five seconds **whether or not the
transcript has caught up**.

Every desk-transcript bug is a failure mode of that table. Too loose → a row vanishes. Too tight →
it duplicates. Timer beats fetch → a gap. This is why steered mail needed an entire second durable
store (`steered_log`) invented for it: hook-delivered text exists in no transcript, so no string
match could ever cover it.

**Cost of the current design:** a string comparison per live row per refresh, and a correctness
model nobody can hold in their head.

### ② 25 `useState` cells are seeded from server data

`useState(x)` snapshots `x` once at mount and never re-syncs. Census:

| file                  | count | examples                                              |
|-----------------------|------:|-------------------------------------------------------|
| `App.tsx`             |    20 | `maxTop`, `defTop`, `compactAt`, `defEffort`, `ceilDirs`, `kkCredits` … |
| `canvas/modals.tsx`   |     4 | `vis`, `charter`, `teamCharter`, `model`               |
| `canvas/desk.tsx`     |     1 | `view`                                                 |

The whole org settings panel and the node config panel are built this way. This is the mechanism
behind the user's transient "the charter looks empty" report — the panel had snapshotted a prop
that later changed. The latent variant is worse: a panel mounted for node A and shown for node B
without unmounting would display A's charter *while saving to B*.

### ③ The backend answers "is this node working?" from memory, not the doc

`supervisor.state()` holds `busy`, `waiting`, `queue`, `last_error`, `turns_run`, `last_status`,
`occupancy`, `context_window` in a process-local dict. Reference counts:

| field            | in-memory refs | ledger refs |
|------------------|---------------:|------------:|
| `busy`           |             12 |           0 |
| `last_error`     |              4 |           0 |
| `occupancy`      |              2 |           1 |
| `context_window` |              1 |           1 |
| `last_status`    |              0 |           4 |

`busy` exists **only** in memory. A restart wipes it. Three separate mechanisms exist to rebuild
what that loses: `reconcile()`, the node's `inflight` record, and the `delivering` journal. Some of
that is irreducible — a live `Popen` handle cannot be persisted — but `occupancy`,
`context_window` and `last_status` are pure data with two homes and no reconciliation between them.

---

## 5. Why fixes have not stuck

Mapping this session's bugs to roots:

| bug                                   | root                                    | fixed at |
|---------------------------------------|-----------------------------------------|----------|
| send receipt never cleared            | one writer recomputes, others don't     | the writer |
| composer stays tall after send        | one writer recomputes, others don't     | the source (layout effect) |
| sticky bottom left behind             | one writer recomputes, others don't     | the source (layout effect) |
| effort control blank                  | read config, not runtime                | the source (one resolver) |
| switchboard chat stops updating       | single slot for per-node events         | the source (per-node map) |
| steered mail vanishes                 | ① live/durable divergence               | new durable store |
| `[ORG NOTICES]` rendered raw          | envelope parsed in one place, not all   | the parser |
| denials banner duplicated an event    | two surfaces for one fact               | deleted a surface |

Three of eight were fixed at the point of divergence rather than at the source. **With N writers and
one derived value, fixing writer 1 leaves writers 2..N** — which is precisely the "fix one, two pop
back" experience.

It is also fair to record that several fixes from this session *added* state cells — `showJump`,
`win`, `loadingOlder`, `modeTimer`, `attached`. Correct individually; the wrong direction under the
user's ruling.

---

## 6. Proposals (NONE IMPLEMENTED — awaiting a ruling)

**P1 — replace the string matching with identity.** Give every live row a server-assigned event id;
`read_chat` echoes the same id on the row it produces; `covered()` becomes `tail.some(m => m.eid ===
r.eid)`. Deletes the prefix match, the name+arg equality and the 5-second expiry. Smallest change
that removes the largest bug class. Does not restructure anything.

**P2 — the live tail becomes the server's job.** The server, which already sequences the events,
returns the live tail as part of `read_chat`; the client renders one list and keeps only the
in-flight delta buffer. Supersedes P1, removes `live_feed`/`sticky`/`steered_log`'s special case
entirely. Bigger, and it changes the WS contract.

**P3 — prop mirrors become derived.** For each of the 25: read the prop directly and keep local
state only while an edit is uncommitted (`const v = edit ?? tree.x`). Mechanical but touches every
settings surface.

**P4 — one home for node runtime data.** Move `occupancy`, `context_window`, `last_status` to the
doc only; keep in memory just what is genuinely process-bound (`proc`, `queue`, `steer`, `busy`).

**P5 — a live marker for sealed thinking** (§3). Small, display-only, and probably the actual fix
for the reported symptom.

Recommended order: **P5 → P1 → P3 → P4**, with P2 only if P1 proves insufficient.

---

## 7. Loose ends found on the way

- **`effort_used` is null for haiku.** The CLI stamps an `effort` field on opus records but not on
  haiku ones, so the effort control falls back to reading `effort` for haiku agents. The title says
  the level has not been observed yet, which is honest but not useful. Sonnet is unverified.
- **`chain_notices`** remains a reserved org-doc key written nowhere, shadowing the working
  `user_deep_reach()`. It should be deleted or wired; it has already misled one session.
- **`_move` inflates a top-level grant past `max_top_grant`** — confirmed in the shelved ledger
  review, still unfixed.
- **`game-master` has an empty charter** although `hire` now refuses without one. Predates the
  requirement.
