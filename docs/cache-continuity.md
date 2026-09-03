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

"Binary" is the rule for a node that is **idle with a cache to talk about**.
Two later rulings added a fourth verdict, `none`, for the cases where there is
no claim to make at all — see [Mid-turn](#mid-turn-what-the-card-may-claim-while-a-turn-is-running).

| Readiness | Colour | Meaning |
|---|---|---|
| `ready` | green | A positive receipt for this exact prefix is inside its lane window. Never a promise that the provider will hit. |
| `not_ready` | red | Compatibility is **not established** for the next turn. Except for an elapsed entry, this is *not* a claim that a miss will occur. |
| `diagnostic` | grey | An enumerated fault stopped a verdict being formed at all. Never "unknown". |
| `none` | *no card at all* | There is nothing to make a claim about. The badge renders **nothing** — not a placeholder, not a grey. Two causes: `no_completed_fingerprint` (no completed turn, so no cache exists yet) and `turn_in_flight` (a turn is running and the prefix has not moved since it was sent). |

A flag is a claim about something assumed to exist (user, 2026-09-03), so
`none` renders no flag rather than an empty or neutral one.

Grey is not a third opinion about the cache — it is reserved for faults, and
there are exactly four: `unsupported_capability` (the provider/lane publishes
no readiness statistic: Antigravity, and Codex API-key), `receipt_timestamp_unreadable`,
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

**`no_completed_fingerprint` has been ruled on twice; D-214 is dead.** The
current answer is the third one, so the first two are recorded here only to
stop them being reinstated by someone reading an older note:

1. **D-214 — green.** With no completed turn there is nothing to conflict
   with, so the badge was green. **Overridden.**
2. **D-226 — red.** Green must require affirmative evidence of compatibility,
   and the absence of all evidence is not that; it rendered red, worded as
   "not established" rather than as a predicted miss. **Also superseded.**
3. **User ruling 2026-09-03 — `none`, no card.** Red is still a claim, and it
   was a claim about a cache that does not exist. A flag asserts that the
   thing it describes is there, so with no completed turn the honest badge is
   no badge. `READINESS["no_completed_fingerprint"] == "none"`.

Green still requires affirmative evidence — that part of D-226 stands, and is
why the absence of evidence never reads as a hit.

The countdown is green-only and additionally requires an authoritative
`expires_at` from a positive receipt; at zero the badge turns red on its own
rather than waiting for the next poll.

Enforced by `backend/tests/test_cache_readiness.py`,
`frontend/tests/cacheforecast.test.tsx` and
`frontend/tests/cachecountdown.test.tsx`.

## Mid-turn — what the card may claim while a turn is running

D-235 (user ruling, 2026-09-03). The card answers exactly one question: **will
the next full turn cause a cache miss or an auto-compact?** Idle, it always
shows, because the answer takes effect the instant a turn starts. Mid-turn,
that answer may be given in advance **only when it cannot be changed by how the
running turn ends**. Anything else would be predicting the turn's outcome,
which the UI must not do.

**The test to apply to any claim:** every field on the card compares the prefix
that would be sent next against *some* cache entry. Idle there is one candidate
— the entry the last positive receipt describes. Mid-turn there are two: that
one, and the entry the running turn is writing or refreshing right now, which
exists at the provider but is **unobserved here until its receipt lands**. So
ask: *does this claim depend on an entry nobody has observed yet?*

| Claim | Depends on the unobserved entry? | Mid-turn |
|---|---|---|
| `prefix_changed` | **No.** It compares the prefix-now against the one already sent. Whatever entry the running turn leaves belongs to the *sent* prefix. | **Shown — yellow `!`** |
| `ready` + countdown | Yes — the running turn's own calls are refreshing that receipt, so the countdown would hit zero on an entry that is not dead. | Not shown |
| `receipt_expired`, `no_positive_receipt`, the unobserved causes | Yes — they describe the *launch* of the turn in flight, and its receipt settles them. | Not shown |
| grey diagnostics | Yes — "cannot tell" is the mid-turn default, not a card. | Not shown |

**Yellow, not red.** Red and green are **guarantees** about the next message:
it will miss, it will hit. The mid-turn moved-prefix state is weaker than that
— a message that *steers* into the running turn is unaffected, and only one
that **misses the steer window** lands cold. So it earns a third colour:
`.cache-forecast.steer`, glyph `!`, in the same `var(--warn)` register as the
composer's `.cache-send-warning.midturn` banner, which says the same thing from
the other side. **Red never shows mid-turn.** Anything not shown renders no
card at all, never a placeholder in the slot.

**The baseline is the request in flight, not the last completed turn.** While a
turn runs the durable book still describes the turn *before* it: `last_turn` is
the previous request and `forecast` is the running turn's own pre-flight
verdict. Comparing against those answered the wrong question — a turn that was
itself the cold one (retool while idle, then send) stayed red for its whole
duration although nothing had moved since launch, and the turn after it would
have found the entry it was writing. The request's secret-free prefix record
now rides the `inflight` marker as `InflightInfo.cache_attempt`, attached at
admission and at the boundary feed, and refused from another generation or
session. It rides that marker deliberately: every turn exit and the startup
`reconcile` already pop `inflight` wholesale, so the attempt **cannot outlive
the turn it describes** — a stale attempt would be worse than the wrong
baseline, and a separate durable field would be a second lifecycle to keep in
step.

Mid-turn `cache_forecast_public` therefore classifies against
`{"last_turn": attempt}`. A **preview-proven** incompatibility is
`prefix_changed` with its components; the *persisted* `known_incompatible` is
ignored, because that is the running turn's own launch verdict and repeating it
is the bug this replaced. Everything else — the launch verdict, the expiry
flip, a persisted diagnostic — becomes `turn_in_flight` (readiness `none`),
minted by `cachecontinuity.in_flight_row`, stamped at launch so its generation
stays stable for the turn, and **never persisted** (hence no legacy mapping).
The projection is streamed after the admission save and after a boundary feed,
superseding the pre-flight verdict the badge used to repeat.

`precompact_action` is **`not_applicable`** mid-turn: a message sent now steers
into the running turn and starts no turn, so no pre-turn compaction applies and
the policy line is dropped rather than left to imply a cost that cannot occur.

### Yellow is a prediction, and it has to come true

The user's own statement of intent (2026-09-03), which defines the state better
than any description of when it renders:

> "a yellow card moving to turn end should *always* transition to red. that is
> the point of the yellow card: to indicate that the next turn will be red,
> ie. a cache miss."

So the transition is part of yellow's meaning: **yellow → the turn ends → red.**
Never green (the warning would have been a lie) and never nothing (the warning
would have been silently withdrawn). It reaches red by two different mechanisms
depending on how the turn ended, and both are load-bearing:

- **the turn reconciled** — `_cache_finish_turn` writes the attempt into
  `last_turn`, so the prefix that moved is still moved against it;
- **the turn did not reconcile** — the durable book is untouched and the
  projection falls back to the persisted launch verdict, which was itself cold:
  a prefix that has moved away from the request in flight while still matching
  the last completed turn can only mean the launch was the cold one.

⚠ Worth knowing which exits reconcile. `_cache_finish_turn` sits under
`if cost or occ or cw or denials or res:`, **not** under `mcp_success`, so an
ordinary **interrupt does reconcile**. The skip is for a turn that produced
nothing at all, and for **backend death healed by `reconcile`** at startup —
and this install restarts its backend routinely, so that is not an exotic path.

**A node with no completed turn never shows yellow.** There is no cache entry
for a missed steer window to fail to reuse, so a moved prefix has nothing to
warn about — the same reason the idle card renders nothing there. This is a
fixed defect, not a theoretical one: such a node used to show yellow and then,
if its first turn ended without reconciling, fall back to
`no_completed_fingerprint` and show **no card**, breaking the rule above.
`cache_forecast_public` now requires a `last_turn` in the book before a
preview-proven incompatibility may render mid-turn. The result is that yellow
is **strictly honest**: it renders only when there IS a prior cache whose entry
a missed steer window would fail to reuse.

Pinned by `backend/tests/test_cache_readiness.py` §6 and §7, and by
`frontend/tests/cacheforecast.test.tsx` (mid-turn never red/green/grey, idle
never yellow, and no stale yellow surviving the turn→idle boundary). The
transitions were found by execution rather than by reading — reading the
control flow suggested the property held, and only running it found the case
where it did not; `scratch/orgtree/cache-verify/probe_transition.py` walks the
eight paths and is kept for that reason.

The private record holds canonical digests, the exact launch/request
fingerprint, provider/account/model/session lane, history prefix evidence,
receipt time, TTL, reasons with evidence timestamps, confidence, and expected
input size. Raw prompts, tool definitions, environment values, and startup
paths are reduced to digests before persistence.

The account component names WHICH account, not merely which lane. A fallback
key row id is already a hash of its token and an API key is digested, so those
move on rotation; the MAIN LOGIN carries the digest of its own account uuid
(`primary:<digest>`), because `primary` alone names a seat — "whoever this
machine is signed into" — and would otherwise stay byte-identical across a
`claude logout` and a login as somebody else, reporting the previous account's
cache as valid for the next one's turns. The bare `primary` survives as the
value for a login this machine cannot currently read, and in rows persisted
before the account was qualified; neither is treated as a switch, because an
unobserved identity is not a changed one (`cachecontinuity._namespace_changed`,
the same rule the history relation follows). Two observed accounts that differ
always are.

Claude's startup component covers the native startup instruction manifest
(managed and user CLAUDE files/imports, unscoped rules, and the loaded memory
prefix). Codex and Antigravity currently cover Orgtree's managed startup identity
and their normalized process/tool surfaces; provider-native global/project
instruction discovery is not exposed authoritatively on those lanes. Their
positive cached-input counts remain useful receipts. Antigravity and Codex API-key
time remain unknown; Codex subscription is the one explicit estimated lane.

Positive provider usage is the only expiry-refresh evidence. For Claude,
subscription-auth receipts derive a 3,600-second TTL and API-key receipts a
300-second TTL. The OpenRouter lane (Claude Code against openrouter.ai, its
own `openrouter-key:` namespace keyed by a digest of the key) is a 300-second
lane too — measured 2026-09-02: every cache write came back in the
`ephemeral_5m` bucket and a resume inside the window read the whole prefix.
Codex app-server receipts report cached input but not TTL;
Orgtree therefore uses 1,800 seconds for a detected ChatGPT/subscription login.
That is a fixed estimate from the official OpenAI `gpt-5.6-sol` Responses API
default—`prompt_cache_options.ttl` defaults to `30m`, currently its only
supported value—not a TTL returned by the Codex receipt. At its boundary the
forecast says a miss is expected, not guaranteed. A future receipt timestamp
is clock-skew uncertainty — which D-226 renders as the named `clock_anomaly`
diagnostic, with the two stamps and the measured skew as evidence. Codex
API-key and Antigravity have no usable TTL, which is why D-226 classes them as an
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

There are **two independent composer banners**, not one with extra gates.

**Case 1 — the mid-turn steer window** (checked first; it overrides case 2).
Fires while a turn is actually running, on `prefix_changed` **and only**
`prefix_changed` — the other red causes are false alarms mid-turn, because a
message that misses the window lands *warm* (see
[Mid-turn](#mid-turn-what-the-card-may-claim-while-a-turn-is-running)). Its
claim is conditional, which is why it says "if" and why it is **always yellow**
whether or not cheap-compact is on: red is reserved for a cost that is actually
expected, and this one may cost nothing. It is threshold-gated on measured
context by the same policy as case 2, and is *not* gated on
`precompact_action`, which is `not_applicable` mid-turn by design. "Mid-turn"
here means an actually-running turn — a queued or compacting agent has no steer
window to miss.

**Case 2 — the past-threshold warning**, reached only when case 1 does not
apply. The composer is narrower than the header badge. For a
known-incompatible or boundary-expired forecast:

- with automatic compaction **off**, a red warning appears only when measured
  context is strictly greater than 25%; exactly 25% is quiet;
- with automatic compaction **on**, a yellow warning appears only when measured
  context is greater than or equal to the configured `occ` threshold, and says
  sending will cheap-compact first;
- below those boundaries there is no banner, and enabled mode never shows red.

Unknown and compatible forecasts never show either banner.

⚠ The badge does **not** always show the forecast any more. Idle it does;
mid-turn it shows the steer warning or nothing. Badge and composer are two
views of one fact and agree on colour: both yellow, both `prefix_changed`-only.
They differ in one respect on purpose — the composer is threshold-gated on
occupancy and the badge is not, because "the prefix has moved" is true at any
occupancy, while interrupting the composer over it is only worth it once a cold
turn would actually cost something.

## Safe UI and stream contract

Tree/API nodes expose `cache_forecast`; the node WebSocket sends
`{kind: "cache_forecast", forecast: ...}`. The atomic object contains:

```text
readiness, readiness_cause, readiness_detail,
generation, state, reason, source, observed_at,
lane, last_receipt_at, ttl_seconds, expires_at,
changed_inputs, precompact_action, precompact_reason
```

`generation` is opaque and suppresses stale events. **The badge renders the
readiness triple, not `state`** — `state` is the observation, `readiness` is
the verdict, and only the verdict has a colour. A consumer that reads `state`
to pick a colour is reproducing a decision the backend already made and will
drift from it.

⚠ The D-214 exception that used to live here — `no_completed_fingerprint` on a
supported `subscription`/`api_key` lane rendering **green** — is gone. It is
`none` now, and renders no card; see the three rulings above. `changed_inputs` is always
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
