# Durable operation receipts

*Implemented 2026-09-05 (work item w71d69aac). Source: `backend/orgtree/opreceipts.py`,
the admission block in `api.agent_call`, `mcptool.call_api`. Suite:
`backend/tests/test_op_receipts.py`.*

## The problem

The MCP server every agent loads makes **one** POST to `/api/agent` with a
30-second timeout. When that answer is lost — a timeout, a dropped
connection, a backend replaced mid-call — the client cannot tell a refusal
from a mutation that already committed, and the natural next move duplicates
it: a second hire, a second mail, a second retire.

This is not hypothetical. The comment on the net-retry replay banner in
`supervisor.py` records the live incident behind it: a turn that dies is
replayed inside its own session, and *"the effects a dying turn commits are
exactly the non-idempotent ones"* — mail already sent, a suite already
spawned. The timeouts are reachable too: retire and dissolve wait up to ten
seconds **per node** for a turn boundary before the archive commits, and a
watchdog create runs a real child process after the commit.

## What a receipt is

Every mutating agent call may carry an **`op_key`**. Our own MCP client mints
one per `tools/call`; no tool card exposes a key, so a model never invents one
and no agent's prompt prefix moves for this, and an older client that omits it
is unaffected in every way.

A keyed call is issued as its own verb, **`orgtree_op_call`**, carrying
`{tool, args, op_key}`. A key spelled on the request envelope is **refused**.
See *Why a keyed call is a verb* below — this is the property that makes a
missing receipt mean anything.

When such a call's **document transaction commits**, a receipt row is
appended to `op_receipts` **inside that same transaction**, immediately
before `save_org`. The receipt and the effect commit together or neither
does.

A row keeps: the operation id, the mint time, the node and its generation,
the verb, a **full SHA-256** fingerprint of the canonical call, the
identity-shaped arguments (`node`, `to`, `id`, `action`, …), the coverage
class, a per-verb allowlisted slice of the result, and the index range of the
document `events` the call produced. It keeps **no** bodies, charters,
kickoffs or question text — the fingerprint follows those, the log does not
store them.

## What it proves, and what it does not

- It proves the **document transaction** committed. Nothing after `save_org`
  is covered: waking a recipient, `@org:`/`@net:` transport, the watchdog
  smoke run, `remote_reap`. The row names those as `post_effects.expected`
  and records `observed: "unknown"` — never `false`, which would read as
  "failed".
- `ev_from`/`ev_to` bracket **document events only**, never post-commit
  effects.
- Some verbs work outside that transaction — a folder move, a transcript
  copy, a process signal, a wait for a turn boundary — and none of it is
  rolled back when the transaction is discarded. Those verbs are classified,
  and the absence of their receipt is reported as `unknown`, never as "not
  applied".
- It is **not exactly-once for an agent's intent**. It de-duplicates transport
  retries of one tool call. A model that decides to issue the call again
  mints a new key and is admitted; that is a new intent, and no key can tell
  it from the first.

## Coverage classes

Set per call — arguments included, not by matching verb names — in
`opreceipts.coverage`:

| class | meaning | may answer "not applied"? |
|---|---|---|
| `transaction` | the whole effect is the document transaction | yes |
| `transaction+post` | plus effects that run after `save_org` | yes (for the document part) |
| `pre_transaction` | irreversible work runs **before** the transaction (`retire`, `dissolve`, a `rehire` that also renames) | no |
| `unrolled_side_effect` | an external side effect runs **inside** the lock and is not rolled back (`self_restart`, `prime_restart`, `restart_wake`'s sidecar file, `cheap_compact`'s transcript copy, `interrupt`) | no |
| `none` | never reaches the transaction (read-only verbs, `send_file`, `rename`, `list_orgs`) | no |

`test_op_receipts` fails when a verb reachable in the dispatch has no class,
so the table cannot quietly go stale. Note what that test does *not* cover: a
new **action** on a multipurpose verb (`orgtree_work`, `orgtree_watchdog`)
defaults to the verb's default class, so add it to `_ACTION_COVERAGE`
yourself when you add a read-only one.

## The lookup: five answers, and `unknown` by default

When an answer is lost the client asks `orgtree_op_lookup` — the one
dispatch verb with no card, callable only by the client, listed in
`test_mcptool.DISPATCH_ONLY` and checked there for being reachable, invisible
and inert. It is a **verb rather than a flag** on the envelope for a safety
reason: a backend that predates receipts ignores unknown envelope fields, so
a flag would have made an old build *execute* the operation the client was
only asking about. An unknown verb refuses instead, and that refusal is how
the client learns the build cannot answer.

| answer | meaning |
|---|---|
| `applied` | the receipt, verbatim |
| `conflict` | that key already identifies a different operation |
| `running` | a call with this key is executing **in this process** right now |
| `not_applied` | provably nothing committed, and the key is now fenced |
| `unknown` | with a reason: `horizon_evicted`, `restored_from_export`, `schema_ahead`, `unsupported_operation`, `pre_transaction_step`, `unsupported_build`, `lookup_failed` |

The in-flight table behind `running` is **process-local and not durable**. It
only ever adds certainty; its absence is never evidence, because a request
that died with the process leaves nothing in it either. The durable answers
come from the log and its watermark, which survive a restart.

### The fence

A lookup that finds nothing does not simply report it. The lost request may
still be on the wire — not in flight anywhere — and would apply *after* the
caller was told it had not. So the lookup **fences** the key: a durable row
that admission refuses. The check and the fence are one transaction, so the
original either committed first (and is found) or can never take effect. Only
then is `not_applied` honest, and it says what it means: safe to reissue
**under a new key**.

The client never re-executes by itself. It hands the agent the answer.

## Retention, and the one invariant

Retention is bounded (`CEILING = 500` rows, trimmed back to `TRIM_TO = 400`;
a key older than `HORIZON_MS = 900 s` is refused as stale), so the absence of
a receipt is only evidence if nothing has been forgotten below it. Hence:

> **Every eviction advances the watermark (`op_receipts_meta.from_ms`) past
> the largest MINT time it evicted, and the watermark only ever increases.**
> A key minted at or after the watermark, with no receipt, was never applied.
> Below the watermark the answer is `unknown`.

Past the largest *mint* time, not past a wall-clock stamp: a key may
legitimately be minted up to a minute ahead of this server's clock, and a
watermark taken from the clock would leave exactly that key admissible with
its receipt gone. The forgetting always costs a refusal, never a duplicate.

On a document with no receipts yet `from_ms` is `0` — nothing has been
evicted, so nothing is unprovable — and the very first key is admitted like
any other. There is **no bootstrap grace window**: one existed until
2026-09-05 and admitted any key minted within five minutes of the section's
creation as "covered", which a mint time cannot establish. Coverage comes
from the shape of the request instead.

### A key belongs to a call, not to an incarnation

`generation` is **not** part of the match. It was, and that made a receipt
invisible the moment the seat's session lineage changed: the call applied at
generation *g*, the answer was lost, the seat compacted, and then the lookup
— which reads the seat's *current* generation — found nothing and said
"not applied". A delayed original arriving on the same key was admitted and
ran a second time, for the same reason.

So a key is matched on `(node, key)`. Finding it under **any** generation
means it has been used: admission refuses a key whose receipt sits at another
generation (it never runs and never replays someone else's result as this
incarnation's), and the lookup answers from the receipt it finds. The
fingerprint is compared at the **row's** generation, because the generation is
part of the fingerprint and recomputing it at a bumped one would never match.

A fenced row is compared the same way. Only the `applied` branch used to
check, so a lookup asking about a *different* operation under a fenced key was
told "that did not apply" — about a call the fence never covered.

### A restored document cannot speak for its own gaps

The rewind witness (`_SEEN`) is advanced by `opreceipts.witness()` AFTER
`save_org` returned for every committed receipt — an applied append and a
lookup's fence alike — still under the document lock. `custody()` only
advances it when called, and it is called before admission; a receipt saved
with no later custody read left the witness at the pre-append seq, so a
restore to exactly that state was invisible and a delayed original was
admitted again (reproduced 2026-09-05). A save that raises advances nothing.

A row found under a key is classified before any claim is made about it
(`opreceipts.classify`: tool + full fingerprint at the row's own subject and
generation): applied, fenced, or a different call. "Already applied" is never
asserted from a row's existence, under any epoch.

`store.export_json` stamps the **exported copy** (never the live document) as
a point-in-time snapshot. A document rolled back from that export lost the
receipts written after it — but not their effects: a document rollback does
not recall mail already delivered to another org, or a process already
started.

The rule this supports is narrow, and deliberately does not use the export's
time. A key minted *before* the snapshot whose operation applied *after* it is
missing from the restored document exactly like a late-minted one, so a mint
time establishes nothing here. What is true is that on a restored document the
absence of a receipt proves nothing at all until the document is recording
again. So the first receipt-layer touch converts the stamp into an ordinary
watermark **at that moment**: every key minted before the restore reads
`unknown` / `restored_from_export`, and every key minted after it is covered
normally, because its whole life is inside the restored document's own record.

⚠ Exercised: `export_json` and the admission/append behaviour. **Not**
exercised: the install path in `tools/cutover.py`, which is the operator's.

A document written by a *newer* receipts build (a higher `schema` or
`coverage` in the meta) answers `unknown` / `schema_ahead`: its rows were
admitted under rules this build does not have, so this build cannot say what
a missing one means.

## Why a keyed call is a verb

A backend built before receipts **drops an unknown envelope field and runs
the operation anyway**. So the first shape of this feature — `op_key` on the
envelope — had a hole, reproduced against the real pre-receipts build
(a0fac2f) in `luna-reserve/probe_old_build.py`:

1. the client sends a keyed call; the old backend executes it, files no
   receipt, and the answer is lost;
2. the backend is replaced by one with receipts;
3. the lookup finds no receipt, fences the key and reports **`not_applied` —
   "safe to reissue"**, for an operation that already happened.

Nothing observable afterwards distinguishes that from a call that never
landed, and a recent mint time certainly does not. The fix is structural: the
request is shaped so a backend without receipts **cannot execute it**. The
old dispatch answers `422 unknown orgtree tool 'orgtree_op_call'` and applies
nothing (measured: mail rows 0 → 0). A client that sees exactly that refusal
knows nothing happened, and reissues the call plainly — unprotected, exactly
as it behaved before receipts existed, and a lost answer to *that* call is
reported as `unknown` / `unsupported_build`.

Both halves of that refusal are required before the client falls back. The
server's own complaints about a malformed wrapper also name the verb, and
treating one of those as "this build has no receipts" would turn a client bug
into the duplicate the feature exists to prevent.

## The retry banner names what the dead turn committed

When a turn dies part-way (the CLI drops mid-response) the supervisor freezes
the seat and replays the message with a banner: "whatever that turn had
already done was not undone — check your real state". Receipts let that
banner say WHICH org operations the turn committed, as one paragraph inside
it (`supervisor.resume_frozen`, `opreceipts.applied_since`).

- **The bound is the attempt's start, never its death.** `_run_one_turn`
  stamps its own entry time on the process's wall clock. A receipt's `at` is
  minted by the same process, so nothing the attempt filed can be earlier.
  `frozen.at` is when the turn DIED — later than everything it did — and is
  never used. The stamp of the run's FIRST attempt is kept on the node
  (`net_fail_since_ms`) through the later attempts and popped by a completed
  turn, so attempt 3's banner still lists what attempt 1 committed.
- **Rendered at resume, not at freeze.** A keyed request that was on the wire
  when the CLI died is queued behind the freeze branch's own document lock
  and commits after the freeze record. Resume reads the log at least thirty
  seconds later, under the same lock, and sees it.
- **What is listed:** this node's `applied` rows filed at or after the
  bound, any generation (a cheap-compact can land mid-turn), fenced rows
  excluded, at most twelve with the remainder counted.
- **Nothing is rendered when there is nothing to list.** A paragraph whose
  only content is a disclaimer is present, plausible and inert.
- **Exactly one paragraph, text only, never parsed.** The freeze record
  keeps the banner's own parts (`frozen.retry` = head, payload, index) and
  resume recomposes head + paragraph + closing sentence + payload. The
  payload is the agent's message and is never inspected — a message that
  quotes every marker survives byte for byte. A resumed carrier brings the
  payload along (`retry_payload`), so a retry that dies again wraps the
  original message, not the previous banner. The human projection
  (`resume_views`) and the document are never touched. No extra turn is
  spent. (A first version found the paragraph by regex and deleted user text
  that happened to contain the markers — the delimiter-collision class.)

The paragraph says it lists receipts OBSERVED since the bound, that the log
is not everything that happened (files, git, shell, any unreceipted call),
and that a row proves its document transaction only (delivery or drive after
it is unknown). Suite: `backend/tests/test_retry_receipts.py`.

## Cost

`op_receipts` is a lazy row-backed section (`store.LIST_LOGS`), so a call
that carries no key never materialises it and pays nothing. Measured on the
real store (`evidence/receipt-cost.json` in the implementing agent's scratch;
15 reps, medians, whole `load_org → mutate → save_org` cycle):

| rows retained | untouched | materialise + append | append + batched trim |
|---|---|---|---|
| 0 | 2.82 ms | 3.80 ms | 3.55 ms |
| 300 | 2.45 ms | 5.93 ms | 6.93 ms |
| 400 | 2.82 ms | 8.05 ms | 6.18 ms |
| 2000 | 2.59 ms | 26.14 ms | 6.24 ms |

The append cost scales with retained rows — the S1 finding, confirmed on this
path — which is what fixes the ceiling at 500 rather than a comfortable 5000.
An ordinary append is genuinely incremental (every existing SQLite `log_l.seq`
is preserved and exactly one row is added); the trim rewrites the section,
which is why it is batched to roughly one call in a hundred.

## What is deliberately not here

- No agent-facing tool card, and no listing verb. The receipt log serves the
  client and the desk; teaching every agent a new card would move every
  agent's prompt prefix, and the agent that actually needs this is the one
  whose turn was interrupted.
- No second write to confirm post-commit effects.
- No automatic retry anywhere.
