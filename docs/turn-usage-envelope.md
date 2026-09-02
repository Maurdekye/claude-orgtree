# Provider usage in turn envelopes

Every ordinary agent turn receives a compact `[PROVIDER USAGE …]` block in
the dynamic user message. It is built from cache-only provider snapshots and
durable local account-routing state. Formatting usage never performs a
provider request, opens a CLI, or blocks admission. A broken telemetry source
produces an explicit `unavailable(telemetry-error)` row and the turn continues.

The block is not part of the managed system prompt, tool schemas, launch
arguments or environment, session identity, or warm-process identity hash.
Changing percentages therefore adds recurring user-message suffix tokens but
does not relaunch a process or invalidate the stable system-prefix cache.

## Coverage

- Claude's signed-in primary account: every cached subscription window, with
  used percentage, authoritative reset and observation time. Stale evidence is
  shown and labelled stale.
- Claude setup-token fallbacks: percentage usage is explicitly unsupported by
  the provider permission available to these inference-only credentials.
  Orgtree does show its authoritative local capacity/cooldown state and reset
  for each model pool. Only `fallback-N` labels are emitted; account ids,
  emails and credentials are not.
- This org's Anthropic API-key lane: explicitly unsupported for quota
  percentage/absolute balance. Active/standby/frozen state and a known fallback
  horizon are shown without including the key or another org's spend.
- Codex: every cached app-server rate-limit window, with used percentage,
  authoritative reset and observation time. Stale evidence is labelled.
- Gemini: subscription-limit usage is explicitly unsupported by the current
  ACP integration. Per-turn token counts are not a provider quota and are not
  presented as one.

Neither Claude nor Codex currently reports an authoritative absolute quota
amount through Orgtree's normalized telemetry, so the stable `amount` column
is `-`. It is reserved for a future authoritative value; Orgtree does not
derive one from percentages.

## Delivery and exclusions

The block is attached to ordinary, mail-driven, automatic working-checkup,
restart-reconciled/resumed, and provider warm-reuse turns. A queued Claude
turn fed at a live result boundary receives a newly built block too; the block
is deliberately absent from durable inflight replay text so replay always gets
fresh numbers.

Two existing non-envelope paths remain excluded:

- Slash-command turns are passed verbatim because `/` must be the first byte
  the CLI sees. They already skip org state, notices and mail, so adding usage
  would break the command contract.
- Mid-response steering is `additionalContext` inside an already-running turn,
  not a new turn envelope. The turn it joins already received its usage block.

Rows are ordered Claude primary, Claude fallback ordinal, current-org API key,
Codex, then Gemini; windows are session, weekly-all, weekly-scoped, then
provider-specific. `*` marks the lane selected for that turn.

## Repetition (D-223)

The board is not re-sent in full on every turn. `turnusage.board()` returns the
rendered text *and* a **material key** — lane, window, usage band, reset rounded
to the nearest five minutes, freshness and state — and `supervisor` re-sends the
whole board only when that key changes, or when a staleness bound expires
(60,000 tokens of context progressed, 10 turns, 900 seconds, a new session, or a
context that shrank). Otherwise the turn carries a one-line stand-in naming the
snapshot number and the **selected lane's exact percentages**.

The bands are deliberately coarse and the exact numbers deliberately are not.
Banding gates only whether the *other* lanes' rows are reprinted; the lane a turn
actually runs on always reports its real usage, because that is the number an
agent throttles itself against.

Rounding the reset to the nearest five minutes rather than truncating it is
load-bearing. Providers jitter the reported reset by about a second, and this
org's own boards were measured reporting one window as `23:00:00Z` and then
`22:59:59Z` on consecutive turns. Truncation puts those in different buckets —
they straddle a minute boundary — so a whole board was re-sent because a clock
wobbled backwards by one second.

A telemetry failure returns a distinct key rather than an empty one. Two failures
in a row are not evidence that the board did not move; the board is *unknown*, so
the next successful render must read as a change and re-send in full.

Two limits with the same window, percentage, reset and active flag are one fact
reported twice, and are deduplicated **before** the `#N` disambiguation that
would otherwise render them as `weekly_scoped` and `weekly_scoped#2`. Genuinely
different buckets under one kind both survive — the Codex lane really does carry
distinct buckets, and folding those would hide a real wall.

Every lane keeps its own explicit row in the full board, including the constant
`unavailable(unsupported)` ones. An earlier draft of D-223 folded those to save
~260 characters a turn and it was withdrawn: suppression already removes them on
the turns where they cost anything, and the explicitness of each state is an
invariant this document and its suite state on purpose.
