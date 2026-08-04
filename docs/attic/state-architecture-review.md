<!-- RETIRED 2026-08-05 (doc sweep): P1-P5 are fully implemented (D-032);
     this analysis is historical. Current truth: DECISIONS.md + the code. -->
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

**Fixed 2026-08-02 (P5, user-directed):** the supervisor now emits a `thinking_start` event on the
thinking block's `content_block_start` — not on `thinking_delta`, because a sealed think's deltas
can all arrive *after* it finishes, which would start the clock as it stopped. The desk renders a
live `thinking… for Ns` that folds into the usual `thought for Ns` line. Measured on a sonnet agent
(sealed tier): the clock appeared and counted 1s → 22s across 23 distinct values, then folded to
`thought for 27s`. That 22-second window was previously an empty panel.

Residual, unfixed: roughly 6 s still elapse between the turn starting and the thinking block opening
(CLI startup, hooks, `init`). The panel shows only the `working` spinner in that window.

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

### ②a — the deeper form of the same disease: liveness gated on stale state

Found 2026-08-02 after the store landed and the symptom persisted (docket D-34). The client's
refresh loop ran only while the payload said `busy` — and `busy` arrives *in the payload the loop
fetches*. A view that started out believing "not busy" could never learn otherwise, so it froze
until unmounted. Every fix before it had improved the push paths while leaving the pull path
conditional on the very state the pull exists to repair.

**The rule this yields, and it generalises past this codebase:** a repair mechanism must never be
gated on the data it repairs. Gate it on something known locally and independently — here, "is any
view mounted" — so no wrong belief can switch off the thing that would correct it.

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

### ①a — a measured instance: the user's message renders TWICE during a long think

Caught in a screenshot while verifying P5, not previously reported. From turn start until the
delivery journal is confirmed, the same message is on screen in two places: once as the transcript's
`@user` mail bubble (the envelope, already drained into the turn) and once as a pending bubble
tagged *delivering mid-task…* (the journal's copy, `delivering_mail()`).

The journal confirms on the first non-`system` stdout event, and the pending row clears on the next
5 s chat refresh — so the duplicate window is roughly turn-start → first stream event → next
refresh, up to ~10 s, and longer if the CLI is slow to start. It is a textbook ① divergence: two
copies of one fact, reconciled on a timer. **Not fixed** — it falls under the ruling.

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

---

## 8. Coverage audit — how much of the state has actually been examined (2026-08-02)

Prompted by the user:

> how thorough of an investigation into what state can be deduplicated have you done? what gaps
> remain to examine? i want to make this app as stateless internally as possible to isolate as many
> classes of stale state bugs as we can

**Honest answer: the investigation was bug-led, not systematic.** §4 was written by walking backwards
from reported symptoms, so it is deep on the *conversation* path and shallow-to-absent everywhere
else. What follows is the first attempt at a census that does not start from a bug report. Numbers
are measured at `fab4f04`.

### 8.1 What HAS been examined, and how well

| area | depth | outcome |
|------|-------|---------|
| the conversation model (§4①) | exhaustive — transcript census, CLI probe, live Playwright | rebuilt: one store, server-owned live tail, watcher-driven heartbeat |
| settings/config prop mirrors (§4②) | census of all `useState`-from-prop in `App`/`modals` | 27 mirrors collapsed to 3 `edit` buffers |
| liveness gating (§4②a) | found only after two failed fixes | rule extracted; applied to the chat path ONLY |
| backend per-node runtime state (§4③) | reference-counted | `occupancy`/`context_window`/`last_status` moved to the doc |

### 8.2 What has NOT been examined — the gaps, ranked

**G1 — the TREE payload has no heartbeat at all.** This is the same defect class as D-34 and it is
the larger half of the app. `refreshTree` is called only on WS connect, on a `node_event` frame, and
in the acting client's own mutation callbacks. There is no timer. Measured: the only intervals in
`App.tsx` are `refreshOrgs` every 3 s (org list, and only while the drawer/welcome is up) and
`nowTick` every 15 s (a clock re-render, no fetch). Every card, credit meter, occupancy bar, roster
entry, resume-red timer and inbox badge is therefore push-only. **Chat now self-heals; the tree
around it does not.**

**G2 — 14 of 30 mutating endpoints persist without broadcasting.** Measured by parsing `api.py`:
every route that calls `save_org` was checked for a `hub_changed`. The silent ones:

```
POST /api/orgs                          POST /api/orgs/{slug}/inbox/read
POST /api/orgs/{slug}/kiosk             POST /api/orgs/{slug}/org_inbox/read
POST /api/orgs/{slug}/defaults          POST /api/orgs/{slug}/inbox/clear
POST /api/orgs/{slug}/nodes/{nid}/scope POST /api/orgs/{slug}/audiences
POST /api/orgs/{slug}/nodes/{nid}/reorder   POST /api/orgs/{slug}/disk/delete
POST /api/orgs/{slug}/nodes/{nid}/message   DELETE …/nodes/{nid}/mail/{mid}
POST /api/orgs/{slug}/dissolve-all      POST /api/orgs/{slug}/credit-requests
```

The acting client refetches in its own `.then()`, which is exactly why this is invisible in
single-tab testing — and exactly why a second view (other tab, kiosk, the switchboard beside the
desk) disagrees. `/nodes/{nid}/scope` is on that list, i.e. the effort/permission surface that was
reported twice.

**G3 — the `mail` frame deliberately refreshes nothing.** `handleWs` returns early for
`type: "mail"` (it is documented as "pure animation"). So mail arriving updates no unread badge, no
tab title, no inbox panel — `InboxView` refetches on `pulse`, and a mail delivery is not a pulse.
The counts are correctly *derived* server-side (`len(user_inbox)`); nothing tells the client to ask.

**G4 — `activity` and `pulses` are client-side accumulations of server facts.** `activity` is built
from `node_stream`/`node_event` frames, keyed by node, cleared on `turn_done`. A missed `turn_done`
strands an indicator until the socket reconnects. Both are derivable now: `busy` is in the tree
payload and the last tool row is in the server-owned live tail. This is the same shape as the
`streams` chain that D-34 deleted, still threaded through the same prop path.

**G5 — 18 of 19 read-endpoint call sites are fetch-once.** Only `getChat` (via `convo.ts`) has a
heartbeat. Of the rest, these display data that mutates while the panel is open: `getInbox`,
`getNodeInbox`, `getEvents`, `getAudiences`, `getHistory`, `getScratch`, `getDisk`, `getDiskDir`.
The remainder (`getHost`, `getCharters`, `getMcpServers`, `getDefaults`, `getOrgMd`) are quasi-static
and probably fine.

**G6 — client state keyed by server ids is never garbage-collected.** `orgtree-eyemin-`,
`orgtree-eyeseen-`, `orgtree-pile-`, `orgtree-inbox-seen-` all persist node ids in `localStorage`.
Exactly one sweep exists (`orgtree-draft-`, against `map`). `minned`/`eyeseen` grow monotonically
across the org's whole hire/fire history; `pileFront` can name a node that no longer exists.

**G7 — the `delivering` journal is a second copy of mail-in-flight** and is reconciled on a timer.
Already recorded as §①a; still unfixed, still under the ruling.

**G8 — `_ws_usage_cache` is the one backend TTL cache** (`workspace_usage_bytes(max_age>0)`, UI reads
only; enforcement always measures fresh). Deliberate and correctly scoped — noted so a future audit
does not rediscover it as a defect.

### 8.3 Where the design is already right (do not "fix" these)

- `store.py` keeps **no** org-doc cache — every read loads from disk under `DOC_LOCK`.
- `Hub` payloads are deliberately dumb: *"the UI refetches the tree; the ledger stays the single
  source of truth."* The contract is correct; G1/G2/G3 are failures to honour it, not reasons to
  change it.
- Aggregates (`top_level_holds`, unread counts) are computed, not stored.
- The archived-bearer transcript snapshot (`readChat`) is a snapshot of an **immutable** object.

### 8.4 Recommended order

**G1, G2, G4, G5 and G6 are DONE** (docket D-35/D-36/D-37) — the user ruled *"proceed"* twice on
this order. **G3 was absorbed rather than fixed**: the tree heartbeat covers a missing mail refresh
within 6 s and the inbox panels poll themselves, so the mail frame stays animation-only by design.
**G7** (the `delivering` journal, §①a) and **G8** (the deliberate usage-cache) remain, both under
the existing rulings.

### 8.5 The blind spot this census had, and how it was closed

Worth recording because it is a property of the METHOD, not of the codebase. A structural census
finds state that is duplicated **by shape**. It is blind to a single copy, correctly owned, that is
simply **old** — and that turned out to be the larger class here: D-34 and G1 are both of it, and
both were found by a symptom rather than by the census.

The closing move was behavioural: **replace the page's WebSocket with one that connects, stays open
and swallows every frame, then run a real turn and see whether the UI still converges on the
ledger.** Whatever fails is a refresh path with no backstop, whatever its code shape. It now passes
5/5 (docket D-38), and the same probe against the pre-G1 build fails exactly the checks G1+G4
address, so the test discriminates rather than merely agreeing.

**The invariant to hold going forward, and the one worth testing on every change:** *the websocket
is an optimization, not a requirement — nothing on screen may depend on having caught an event.*
That is the positive form of the rule in §②a, and it is cheap to re-check.


1. **G1** — one tree heartbeat, gated on "the org view is mounted". Smallest change, largest blast
   radius, and it makes G2/G3 non-fatal rather than merely rarer.
2. **G2** — make `hub_changed` structural (broadcast in `save_org`, or one decorator) so a new
   endpoint cannot forget it. Fixing the 14 by hand recreates the "N writers" problem the review
   opened with.
3. **G3** — let the mail frame refresh, or carry the counts on the frame.
4. **G4** — delete `activity`/`pulses`, derive from tree + live tail.
5. **G5** — a shared `usePolled(fetch, deps)` for the eight mutable panels.
6. **G6** — sweep id-keyed `localStorage` against the tree, as drafts already are.

☞ The through-line: **§4 fixed the copies; §8 is about the refresh paths.** Deduplicating state
removes divergence between two copies. It does nothing for a single copy that is simply old, and
five of the eight gaps above are that second failure.
