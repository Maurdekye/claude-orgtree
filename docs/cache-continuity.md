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
| `expired_known_entry` | A positive receipt exists for the same fingerprint/lane and its fixed lane boundary has been reached. Claude uses provider-reported lanes; Codex subscription uses the documented 30-minute API default as an explicit estimate. |
| `uncertain` | Local evidence matches or is incomplete, but a positive receipt, history proof, lane, TTL, or trustworthy clock boundary is unavailable. Orgtree does not infer a miss. |
| `compatible_observed` | The local fingerprint matches an unexpired positive receipt. This is evidence of compatibility, not a guaranteed provider hit. |

## Readiness — what the badge renders (D-226)

⚠ **The table above is what Orgtree OBSERVED. It is not what the UI shows.**
User invariant (2026-09-02): compatibility readiness is **binary** in normal
operation. Every forecast therefore also carries a `readiness` verdict, a
machine-readable `readiness_cause`, and a user-facing `readiness_detail`, and
the badge renders *those* — never `state`.

| Readiness | Colour | Meaning |
|---|---|---|
| `ready` | green | A positive receipt for this exact prefix is inside its lane window. Never a promise that the provider will hit. |
| `not_ready` | red | Compatibility is **not established** for the next turn. Except for an elapsed entry, this is *not* a claim that a miss will occur. |
| `diagnostic` | grey | An enumerated fault stopped a verdict being formed at all. Never "unknown". |

Grey is not a third opinion about the cache — it is reserved for faults, and
there are exactly four: `unsupported_capability` (the provider/lane publishes
no readiness statistic: Gemini, and Codex API-key), `receipt_timestamp_unreadable`,
`clock_anomaly` (a receipt stamped ahead of the backend clock), and
`internal_error`. Every one of them must carry instance evidence naming the
provider, the stamp, or the incident; a constant "unsupported" sentence is the
generic unknown the ruling forbids. `internal_error` is also logged.

Three rules that are easy to break later:

* **There is no catch-all.** A cause the table does not know becomes
  `internal_error` — named, explained, logged — not a neutral grey.
* **The badge fails closed.** A payload whose readiness cannot be read — an
  unrecognised value, a verdict with no cause, a row with neither a triple nor
  a known state — renders grey `internal_error`, never green. A green badge on
  a payload nothing understood is the most expensive lie this UI can tell.
  A row with **no triple at all but a recognised `state`** is not unreadable:
  it is a pre-D-226 forecast from a backend older than the UI, and the badge
  re-derives its verdict from `state`/`source`/`lane` exactly as
  `legacy_readiness` does on the server (same table, same expiry decay, red
  residue), saying so in the tooltip. A schema migration is not a fault, and
  a deployed backend lagging a rebuilt `dist/` used to paint every node grey.
* **A known incompatibility outranks a capability gap.** A seat that moved to
  an unsupported lane is red (`prefix_changed`), not grey: a positive
  determination that the next turn is cold is strictly more informative than
  "cannot tell", and grey is only for where no opinion can be formed.

**This overrides D-214.** `no_completed_fingerprint` on a supported lane used
to render green, reasoning that with no completed turn there is nothing to
conflict with. Green now requires affirmative evidence of compatibility, and
the absence of all evidence is not that. It is red, worded as "not
established" rather than as a predicted miss.

The countdown is green-only and additionally requires an authoritative
`expires_at` from a positive receipt; at zero the badge turns red on its own
rather than waiting for the next poll.

Enforced by `backend/tests/test_cache_readiness.py`,
`frontend/tests/cacheforecast.test.tsx` and
`frontend/tests/cachecountdown.test.tsx`.

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
positive cached-input counts remain useful receipts. Gemini and Codex API-key
time remain unknown; Codex subscription is the one explicit estimated lane.

Positive provider usage is the only expiry-refresh evidence. For Claude,
subscription-auth receipts derive a 3,600-second TTL and API-key receipts a
300-second TTL. Codex app-server receipts report cached input but not TTL;
Orgtree therefore uses 1,800 seconds for a detected ChatGPT/subscription login.
That is a fixed estimate from the official OpenAI `gpt-5.6-sol` Responses API
default—`prompt_cache_options.ttl` defaults to `30m`, currently its only
supported value—not a TTL returned by the Codex receipt. At its boundary the
forecast says a miss is expected, not guaranteed. A future receipt timestamp
is clock-skew uncertainty — which D-226 renders as the named `clock_anomaly`
diagnostic, with the two stamps and the measured skew as evidence. Codex
API-key and Gemini have no usable TTL, which is why D-226 classes them as an
accounted `unsupported_capability` rather than leaving them silently unknown.
`cachecontinuity.SUPPORTED_LANES` is the single source of truth for both the
TTL table and that badge verdict, so the two cannot drift apart.

Official basis: <https://developers.openai.com/api/reference/cli/resources/responses/methods/create>

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

The composer warning is narrower than the header badge. The badge always shows
the forecast. For a known-incompatible or boundary-expired forecast:

- with automatic compaction **off**, a red warning appears only when measured
  context is strictly greater than 25%; exactly 25% is quiet;
- with automatic compaction **on**, a yellow warning appears only when measured
  context is greater than or equal to the configured `occ` threshold, and says
  sending will cheap-compact first;
- below those boundaries there is no banner, and enabled mode never shows red.

Unknown and compatible forecasts never show either banner.

## Safe UI and stream contract

Tree/API nodes expose `cache_forecast`; the node WebSocket sends
`{kind: "cache_forecast", forecast: ...}`. The atomic object contains:

```text
generation, state, reason, source, observed_at,
lane, last_receipt_at, ttl_seconds, expires_at,
changed_inputs, precompact_action, precompact_reason
```

`generation` is opaque and suppresses stale events. The UI maps the internal
`uncertain` proof state to the plain gray label **cache compatibility unknown**,
except that `no_completed_fingerprint` on a known supported `subscription` or
`api_key` lane is green because no completed turn exists to conflict with it.
That exception still says a provider hit is not guaranteed and never describes
uncertainty as an observed cache hit. `changed_inputs` is always
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
