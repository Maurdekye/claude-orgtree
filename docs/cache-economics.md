# Cache economics — dormant agents and the cost of waking cold

*(implementer, 2026-08-11; updated 2026-09-01. The numbered ideas remain
historical design context. Current receipt/TTL and automatic-compaction
behavior is normative in `cache-continuity.md` and D-214.)*

## The problem

An expensive-model agent with heavy context left past its cache TTL can pay
close to full input price when it wakes. The TTL is lane-specific: Orgtree now
derives 60 minutes for a positive Claude subscription receipt and 5 minutes
for a positive Claude API-key receipt. A Codex subscription receipt uses the
fixed 30-minute estimate documented in `cache-continuity.md`; Codex API-key,
Gemini and otherwise unknown lanes do not inherit another provider's number.

## The arithmetic that sorts the ideas

With context size C:

- cold wake ≈ **1.25×C** (cache-write premium on the reload)
- warm read ≈ **0.1×C**, and each read **resets the TTL**

∴ a keep-alive ping every ~4.5 min costs ≈ 1.3×C *per hour* — more than one cold wake per hour.
**Keep-warm loses for open-ended dormancy**; break-even ≈ 55 min of *known-bounded* waiting.
Everything that works does one of three things: shrink C, move the payment into a warm window
(0.1× instead of 1.25×), or pay it fewer times.

## A. Shrink C (fully passive)

1. **Externalized memory as doctrine** — prompt/charter guidance that durable state lives in
   scratch files, not conversation. Makes aggressive compaction near-lossless; the enabler for
   everything else.
2. **Lean-manager discipline** — coordinators (the longest sleepers) delegate reading to
   reports instead of pulling artifacts into their own context. Extension of the 08496a7
   prompt-audit line.
3. **Tier-aware seat design** — long-lived dormant roles belong on cheap tiers; fable seats
   should be short-lived and task-shaped. Hire-card hint, not enforcement.
4. **Envelope suppression** (D-223, shipped 2026-09-02) — the per-turn `[ORG STATE]` and
   `[PROVIDER USAGE]` blocks re-send their unchanged bulk only when it has changed or a
   staleness bound expires. This shrinks C *as it accumulates* rather than reclaiming it
   afterwards, which is what distinguishes it from compaction: every byte not appended is a
   byte no future turn re-reads and no cold resume re-pays.

   Measured by `tools/envelope_cost.py` over 779 real enveloped turns: ORG STATE was 993
   chars/turn of which the chart is 51.2%, and only **17.3% of its characters changed
   semantically** turn over turn (33.0% byte-wise — the difference is entirely countdowns and
   timestamps moving). Replaying real history through the shipped rules cuts the two blocks by
   **21.6%** overall, and by 36.9% on a turn where the chart is actually suppressed.

   ⚠ The envelope's largest line item is **MAIL** — 2,534 chars on 93% of turns — and it is
   payload, not overhead. Nothing here compresses it and nothing should; an agent that is not
   told what it was sent is the failure this whole subsystem is shaped around. Read D-223
   before tuning any threshold, and re-run the tool first: those percentages are properties of
   how this org happens to be shaped today, not constants.

## B. Pay at 0.1× — schedule work into warm windows

4. **Sunset compaction** (the strongest single measure): when a turn ends, the queue is empty,
   and context > threshold, compact **immediately while the cache is warm** — the compaction
   fork's full-transcript replay reads at 0.1× instead of the ≥1× it costs when someone compacts
   the same agent cold, hours later. Same `_compact_split_body` machinery, new trigger; the
   successor then sleeps small.
5. **Forks piggyback warm windows** — compaction/oracle forks launched > TTL after the last
   turn should be deferred to the next turn-end or explicitly warned about. The code already
   calls the fork "often the most expensive call the system makes".
6. **Targeted keep-warm, expectation-gated** — keep-warm wins only when a reply is imminent and
   likely, which orgtree can see: an open ask, an outstanding delegation, a live user
   conversation. Cap it (give up after ~45 min / N misses). Lowest priority: a CC ping is a real
   turn and grows the transcript; the break-even math caps the value.

### Reported-working cache lifecycle (implemented 2026-08-31)

`orgtree_status working` is the explicit expectation gate. While that durable status remains on a
live Claude agent, the backend makes a disposable `--resume --fork-session` request using the same
identity, tool, and MCP argv as a real turn. Its settings are derived from the real turn only to add
the local execution barrier described below. The fork reads the cached prefix, but its
keepalive prompt and response are deleted with the fork transcript and never enter the agent's
session. The billed request is still added to the node/org cost ledger.

The provider still sees that real system/tools/MCP prefix, but the maintenance child cannot execute
tools: an all-tool local `PreToolUse` hook denies every attempt and `--max-turns 1` supplies an
independent turn ceiling. A real turn atomically cancels and reaps an in-flight maintenance child
before it resumes the durable session, so the two processes never overlap on one session.

Cadence follows the spawn-captured billing lane: 50 minutes for OAuth/subscription (the CLI requests
the one-hour tier) and 4 minutes for `ANTHROPIC_API_KEY` (five-minute tier). The environment variables
in `configuration.md` can tune both. Codex and Gemini agents are skipped: their provider processes and
cache contracts are different, so the backend does not send them a synthetic Claude request. Frozen,
remote-controlled, preserving-bearer, never-run, busy, waiting, responding, or queued nodes are also
skipped. A real turn/status change always wins a recheck after keeper-slot contention.

The working checkup/cache-read setting determines whether this maintenance
request runs. A successful request still records `cache_keepalive_at` for
lifecycle observability, but automatic known-cold compaction does **not** use
that timestamp or generic turn idle time. Only a positive same-lane provider
receipt refreshes the predictor expiry. Explicit/manual compaction is
unchanged.

Failed requests use bounded exponential retry backoff (one minute through thirty minutes by default),
reap a fork id even when it appeared only in partial timeout output, and bank any cost the CLI
reported. A failed request never earns a freshness timestamp.

## C. Pay once instead of N times — wake shaping

7. **Debounced delivery for cold agents** — a node idle > TTL doesn't wake on first mail; hold
   for a batching window / K messages / an urgent flag. Five FYIs over an evening = one cold
   wake, not five. Mail arriving warm still delivers instantly.
8. **Exploit the warm window after any paid wake** — flush everything queued; encourage
   follow-ups now, not next hour. A "recently woke, warm" signal makes this visible.
9. **Secretary/gatekeeper screening** — a cheap-tier screener in front of a dormant expensive
   agent: answers the routine, batches the rest, wakes the principal only when warranted.
   Composes with FR-18 watchdogs and the "pets are free" precedent.
10. **Wake-cadence hygiene** — the pathological autonomous loop wakes every ~6 min: always just
    past TTL, always cold (~12.5×C/hour). Heartbeats should be < TTL or long-and-batched; the
    supervisor could warn on the bad cadence.

## D. Make the cost visible (enables A–C)

11. **Cache telemetry into the ledger** — the CLI's stream-json `result` usage carries
    `cache_read_input_tokens` / `cache_creation_input_tokens`; record hit/miss and cold-wake
    cost per turn in `TurnStat`. Thresholds for ④ and ⑦ get tuned from real numbers.
12. **Cost-visible sends + warm/cold badge** — FR-23's turn-end timestamp is the natural home:
    "last turn 47 m ago · cold" on the node square, and a wake-cost estimate in the tool result
    when an agent is about to message a cold expensive node. A deterrent that costs nothing.

## Recommended package

④ sunset compaction + ⑦ debounced cold delivery + ⑪ cache telemetry — cause, frequency,
measurability. ⑨ folds into FR-18's design. ⑫ rides FR-23. FR-24 (cheap compact, shipped as an
opt-in verb per the 2026-08-11 ruling) is the manual escape hatch the package automates around.
