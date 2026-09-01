# Cache continuity predictor and known-cold compaction

Orgtree tracks two different facts:

- A warm local provider process can make startup faster.
- A provider may accept a previously cached prompt prefix.

One does not prove the other. In particular, a local backend or CLI-process
restart does not by itself prove a provider cache miss.

## Stable agent doctrine

Every Orgtree-managed system/startup prompt contains the same concise
`CACHE CONTINUITY` block. It tells agents which changes always move the known
cache namespace or prefix, which do not do so by themselves, and which need a
provider receipt before Orgtree can decide. The block has no org state,
timestamps, settings, account names, or forecast values.

The first deployment of this block intentionally changes every existing
managed system prompt once. That is one known identity replacement. Later
turns keep the block byte-identical.

## Forecast states

The next-turn forecast is persisted on the node and owned by its lineage
generation. A completion from an older generation cannot overwrite a
successor.

| State | What Orgtree knows |
|---|---|
| `known_incompatible` | A known namespace changed (provider, account/auth lane, model, session lineage) or a known provider-visible prefix component changed (system, tools/MCP, normalized argv/env, startup inputs, lineage, or already-sent history). |
| `expired_known_entry` | A positive receipt exists for the same fingerprint/lane and its authoritative derived TTL has reached its boundary. |
| `uncertain` | Local evidence matches or is incomplete, but a positive receipt, history proof, lane, TTL, or trustworthy clock boundary is unavailable. Orgtree does not infer a miss. |
| `compatible_observed` | The local fingerprint matches an unexpired positive receipt. This is evidence of compatibility, not a guaranteed provider hit. |

The private record holds canonical digests, the exact launch/request
fingerprint, provider/account/model/session lane, history prefix evidence,
receipt time, TTL, reasons with evidence timestamps, confidence, and expected
input size. Raw prompts, tool definitions, environment values, and startup
paths are reduced to digests before persistence.

Claude's startup component covers the native startup instruction manifest
(managed and user CLAUDE files/imports, unscoped rules, and the loaded memory
prefix). Codex and Gemini currently cover Orgtree's managed startup identity
and their normalized process/tool surfaces; provider-native global/project
instruction discovery is not exposed authoritatively on those lanes. Their
positive cached-input counts therefore remain useful receipts, but neither
lane is promoted beyond `uncertain` on elapsed time.

Positive provider usage is the only expiry-refresh evidence. For Claude,
subscription-auth receipts derive a 3,600-second TTL and API-key receipts a
300-second TTL. At exactly the expiry timestamp the state is expired. A future
receipt timestamp is clock-skew uncertainty. Codex/Gemini cached-input counts
currently have no authoritative TTL exposed to Orgtree, so time alone remains
uncertain on those lanes.

Provider switching is a known namespace change. It loses the current warm
cache/process and can also lose provider-specific session/context continuity;
the forecast must not present it as an ordinary local restart.

## Automatic known-cold compaction

The setting is explicit on/off plus one number: the minimum measured context
occupancy fraction (`occ`, default 0.5 when enabled). There is no editable idle
timeout. The old `idle_s` field is ignored and removed when an org/default or
node override loads; `enabled` and `occ` are retained. A node override that
contained only `idle_s` becomes ordinary inheritance. The cleaned shape is
written on the next normal save.

Before any ordinary prompt admission—user, mail, automatic checkup,
resume/recovery, provider redrive, or warm-process boundary feed—Orgtree
cheap-compacts first only when:

1. the forecast is `known_incompatible` or `expired_known_entry`;
2. automatic cache protection is enabled; and
3. measured context is at or above the configured fraction.

It does not compact an `uncertain` forecast, commands with no prompt turn,
fresh/unrun or summary-only successors, estimated fills, knowledge bearers, or
disabled/below-threshold nodes. Frozen/blocked nodes do not reach ordinary
admission. Compaction happens before mail/notices drain, so the exact carrier
is delivered once to the successor together with its compaction notice.

## Safe UI and stream contract

Tree/API nodes expose `cache_forecast`; the node WebSocket sends
`{kind: "cache_forecast", forecast: ...}`. The atomic object contains:

```text
generation, state, reason, source, observed_at,
lane, last_receipt_at, ttl_seconds, expires_at,
changed_inputs, precompact_action, precompact_reason
```

`generation` is opaque and suppresses stale events. `changed_inputs` is always
present: every safe changed component label for `known_incompatible`, otherwise
`[]`. It contains no values, hashes, credentials, account/session IDs, or
secret-bearing paths. `precompact_action` is one of `will_compact`,
`miss_expected`, or `not_applicable`; the backend owns that policy answer so
the UI does not reproduce it.

The admission boundary persists the full transcript-aware answer. Tree polls
also perform a non-mutating, transcript-free preview of namespace, system,
tool, argv/env, and startup components so a retool/model/account/startup change
can reach the composer warning before send. History-only rewrites remain
conservative until admission rather than repeatedly hashing every agent's
transcript on the six-second UI heartbeat.
