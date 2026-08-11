# Cache economics — dormant agents and the cost of waking cold

*(implementer, 2026-08-11; from a design conversation with the user. Status: recorded for
implementation planning — the measures below are candidates, not commitments, except where a
docket/DECISIONS entry says otherwise. Premises verified in FR-24's docket entry: prompt-cache
TTL is 5 min (1 h exists on the raw API; the pinned CLI v2.1.220 exposes no flag for it), and
orgtree talks only to the CLI, so nothing here may assume an interface change.)*

## The problem

An expensive-model agent (fable) with heavy context (say 700k tokens) left dormant past the
cache TTL pays close to full input price the moment it wakes — every wake, forever, for as long
as the context stays big. In a dynamically orchestrated system agents routinely sleep ≫ 5 min
and are woken autonomously, so this is a standing tax, not an edge case.

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
