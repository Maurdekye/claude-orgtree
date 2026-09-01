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
