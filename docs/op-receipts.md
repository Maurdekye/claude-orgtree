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

Every mutating agent call may carry an **`op_key`** on the request envelope —
never in a tool's arguments, so no tool card changes and no agent's prompt
prefix moves for this. Our own MCP client mints one per `tools/call`; no card
exposes a key, so a model never invents one, and an older client that omits
it is unaffected in every way.

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
| `unknown` | with a reason: `horizon_evicted`, `before_bootstrap`, `unsupported_operation`, `pre_transaction_step`, `unsupported_build`, `lookup_failed` |

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

At bootstrap `from_ms` is `0` and `bootstrap_ms` is the moment the section
was created, because nothing has been evicted yet and starting the watermark
at "now" would refuse the very first key — minted moments *before* the
section existed. A key older than `bootstrap_ms - 300 s` reads `unknown`
rather than claiming coverage of a window this build never had.

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
