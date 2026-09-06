# Turn events — ordered failure timelines and turn diagnostics

`backend/orgtree/turnread.py` (pure: schema, coercion, readers) · `backend/orgtree/turnlog.py` (the recorder) · `tools/inspect_turn.py` · `backend/tests/test_turn_events.py`

Docket item `redacted-failure-fixtures-and-extensive-logging`. Contract v3 (2026-09-06): v1 was reviewed by the coordinator at 07:54Z, v2 (commit 167aac5) by agy-journal (supervisor integration) and fable-verify (recorder/reader) at ~09:10Z; this revision incorporates every ruling and finding and describes what is implemented on branch `luna-turn-events` (from main 8d63b74). It extends the deployed failure fixtures (`docs/failure-fixtures.md`, milestones 1+2) with the two capabilities the user selected on 2026-09-06T07:24Z: **failure event timelines** (ordered adapter events, timing and state changes around a failure) and **general turn diagnostics** (the same record for turns that succeed). No UI viewer. Line references are the branch's `supervisor.py` as read on 2026-09-06.

## What exists today, and what was missing

A turn leaves, at most: a `turn_error_log` row (400 chars of prose), `last_error` in memory, a redacted failure fixture (`failfix.record` at four sites), the CLI's own transcript, and process telemetry in `journals/warm.jsonl`. A **successful** turn leaves only its transcript and cost. None of these say **in what order** the boundary events happened, **when** (first output, result, exit, kill), or which supervisor branch **took ownership** of a failure and what state it wrote (freeze, retry counter, abandonment). Reconstructing "the CLI spoke at 3 s, the wire dropped at 41 s, the retry counter went to 2, the node froze for 30 s" meant reading the backend log by eye.

## What a turn record is

One JSON file per **turn attempt** — every entry into `_run_one_turn`, which is one process attempt on one lane; a frozen-retry replay is a new attempt correlated by the run counter below. Written under `<ORGTREE_DATA>/turnlog/<org>/<node>/`, beside `failfix/`, with the same conventions: an **allowlist** (a field not named in `turnread.FIELDS`/`HEADER_FIELDS` does not exist), every string leaf a closed-vocabulary member or `"other"`/`null`, counts and timings typed and never coerced from text, fail-open, bounded, never in the org document.

```
schema        1
at            ISO second UTC, the attempt's start (the ONLY wall-clock time)
attempt       a generated token (time_ns + counter): unique per process, NOT a
              provider session id — no session, org, node or account id is in
              the record; the directory is the node correlation
lane          claude | openrouter | codex | antigravity | other
tier          the node's tier (ledger.TIERS' names, copied) or "other"
run           the node's net_fail_run counter AS THIS ATTEMPT BEGAN
              (0 = not inside a failure run)
run_since_ms  net_fail_since_ms (the failure run's origin) or null — the key
              that ties attempts 1..N of one failure run together
resumed       bool: the carrier rode a retry payload (a connection-retry replay)
cmd, ping     bool: a slash command / a bare wake
toks, text_len, images_n, view_len   counts only; never the text
warm          bool: served by a parked process (D-201)
partial       bool: an UNFINALIZED record (§Write boundaries)
events        the ORDERED list (below)
events_n      total emitted, including dropped
dropped       events NOT kept (0 when none); dropped_kinds {kind: n}
truncated     bool: the record was cut to CAP_BYTES at close (§Bounds)
outcome       completed | interrupted | frozen | killed | abandoned |
              unrecoverable | redriven | failed | crashed | unknown (§Outcome)
outcome_ms    monotonic ms from the attempt's start to its close
error_class   the CLASS name of the exception that ended the attempt, from a
              closed vocabulary (RuntimeError, _ProviderTurnFailed, …) or
              "other"; never its message
fixture       the failfix BASENAME this attempt wrote, or null (§Correlation)
paid_booked   bool or null; cost_usd float or null; cost_known bool — an
              unknown cost is null with cost_known false, never 0.0
recorder_errors  how many recorder calls failed inside their own guard
```

Every event is `{seq, t_ms, kind, ...typed fields}`:

- `seq` — a per-attempt counter. `seq` and `t_ms` are assigned **inside the same lock**, so the reader thread, the watchdog thread, the steer pump and the codex/antigravity callback threads share ONE total order and `t_ms` is non-decreasing in `seq` order. The suite asserts both over a six-thread emitter, checks head/tail retention against the expected numbered events, and probes directly that every clock read happens while the lock is held (a stamp taken just before the lock passes the ordering assertions most of the time; the probe cannot pass by luck).
- Typed numbers: an int, or a float that is WHOLE (wire JSON may carry `3.0` for a count) — never a string, a bool, a fraction, NaN or infinity; bounded to ±10^15.
- `t_ms` — `time.monotonic()` milliseconds since the attempt's start: a wall-clock step cannot reorder it.
- No field is named `seq`, `t_ms` or `kind` (asserted at import); the emit's `kind` parameter is positional-only so a field name can never collide with it — that collision raised on the production path during development and stopped a connection freeze, which is exactly the class of defect the fail-open design exists to exclude.

### Event vocabulary (closed; fields typed; no text)

Common to every lane (source: the branch's `supervisor.py`):

| kind | fields | where |
|---|---|---|
| `start` | `slot_wait_ms` | after the turn-slot wait |
| `spawn` | `warm`, `spawn_ms` | after the process is obtained (claude lane) |
| `init` | `tools_n`, `mcp_n`, `mcp_failed_n`, `mode` | `system/init` |
| `delivered` | — the journal confirmation of the MAIL batch: emitted only when the turn carried mail tokens (C1 proof) | `_confirm_delivered` |
| `first_output` | `thinking` | the first delta, thought, assistant event, codex item or antigravity step |
| `assistant` | `text_n`, `tool_n`, `thinking`, `synthetic`, `api_error`, `tools` [≤8, §Names] — TOP-LEVEL events only; subagent (sidechain) events are not recorded | the assistant branch |
| `tool_result` | `n`, `errors_n` — top-level only | the user branch |
| `api_retry` | `code` (ten of the CLI's typed machine tags — the subset of failfix's `CODE_WORDS` a retry event can carry — or other), `n` | `system/api_retry` |
| `result` | `boundary` (false = straggler on a closed pipe), `is_error`, `subtype`, `status` (strict int), `duration_ms`, `num_turns`, `result_len`, `errors_n`, `in_tokens`, `out_tokens`, `cache_read`, `cache_create`, `cost_known` | both result branches |
| `interrupt` | — the manual ⏸ flag observed after exit | the interrupt check |
| `watchdog` | `why` (idle / budget / ceiling), `elapsed_ms` — from the watchdog thread on the claude lane; from the leg on codex/antigravity | `_dog`, the ceiling raises |
| `exit` | `code`, `parked`, `exit_only`, `stderr_len`, `stderr_lines` | once the class is decided |
| `classify` | `limit`, `net`, `filtered`, `typed`, `started`, `boundary`, `or_lane` — the site's OWN predicate results, recorded not recomputed | after `_limit_class`/`_net_class` |
| `owner` | `branch` (unrecoverable / filter / account_switch / limit_freeze / net_retry / net_exhausted / terminal / provider_limit), `handled` | each branch that claims a failure; `terminal` is ALWAYS emitted on the terminal door, with `handled` saying whether an earlier branch owns it |
| `freeze` | `freeze_kind` (limit / connection), `schedule` (observed-deadline / probe / backoff), `reset_known`, `delay_s`; connection: `run`; limit: `reset_src` (FrozenInfo's provenance: text / usage / probe / capped / inherited / provider / auth), `untrusted`, `parked` (untrusted / auth / balance) — all read back from the freeze record the branch WROTE (`turnread.freeze_shape`), never recomputed; a field the record does not carry is absent, and no record (empty or None) yields no fields at all — a nonempty record with no reset instant says `reset_known: false` explicitly | the freeze writes |
| `abandon` | `door` (pre_model / ran_then_failed / killed) and ONE counter, named for what the door observed: `hard_fail_run` (the terminal and killed doors' `_bump_hard_fail` result) or `net_fail_run` (the exhausted door's connection-retry counter; that door never bumps the hard counter) | `_turn_abandoned` / `_retry_exhausted` |
| `fixture` | `written` | after each `failfix.record` |
| `fold_back` | `undelivered_n`, `uncertain_n` | the shared finally |
| `teardown` | `parked`, `discard` (warm discard reason or null), `exited` | codex/antigravity legs' teardown |
| `dispose` | `outcome` — every disposition claim, in order | `Recorder.dispose` |
| `end` | `outcome`, `outcome_ms` | close (never read by `summarize`) |

Codex (`_codex_leg_attempt`): `codex_route` {`pool`, `route` (reserve/direct), `selection` (preflight/retry)}, `codex_item` {`type` (agent_message/reasoning/tool_call/tool_output/plan/other), `n`} per `item/completed`, `codex_rerouted` {`known`}, `codex_account` {`ambiguous`}, `codex_rate_limit` {`pool`, `percent`, `reset`, `folded`} per folded notification, `codex_status` {`status`, `rpc_code`}, `codex_decide` {`decision`, `rejected`, `redrive`, `pool_state`, `reset_known`}, `codex_redrive` {`to`}.

Antigravity (`_antigravity_leg`): `agy_step` {`step` (text/tool), `n`} per DONE/ERROR step, `agy_status` {`status`}, `agy_wall` {`walled`, `reset_known`, `reset_in_s`, `schedule`}, `agy_ceiling` {`elapsed_s`, `ceiling_s`, `killed`}.

OpenRouter is the claude CLI under the OR key: the lane is the SPAWN-STAMPED identity (`st["ran_as"] == OPENROUTER_IDENTITY`), `classify.typed` carries the number that decided the class, and the suite drives one through the real loop (§5).

**Unknown is allowed.** A boundary this code never observes produces no event: the record shows the gap and `summarize` reports `phase: unknown`. Nothing is inferred into an event.

### Names (`assistant.tools`)

Tool NAMES are kept only from a **static reviewed vocabulary** of builtin claude CLI tools (`turnread.TOOLS`). Every other name — MCP servers, custom tools, anything matching a pattern — is `"other"`; the count is retained. No pattern is an allowlist. Tool ARGUMENTS and RESULTS never enter (counts and error counts only).

### What is NOT recorded, on purpose

No prompt, mail, reply, thinking, tool argument or result, file content, env, header, credential, URL, path, host, org, node, account or provider session id. No error prose: the exception's CLASS name is kept, its message is not — the message already lives in `turn_error_log`. No regex over prose: this module has no phrase vocabulary; where a class matters the supervisor's own recorded result is carried. The suite feeds sentence, secret and identifier canaries into every field of every kind, the header, dispose, fixture and error, and through the real lanes (prompt, reply, error text, tool args), and asserts none survives beside positive controls (builtin tool name, counts, lengths, status, exit code) that must.

## Outcome — the final supervisor disposition

`outcome` is written by `Recorder.dispose`, called by the exit path that knows: **the last call before close wins, except `unrecoverable`, which is sticky**. The unrecoverable branch marks the document and then falls through to the terminal door — which still bumps the hard-fail counter, drives the superior and disposes failed/abandoned; all of that is unchanged and all of it is in the events — so the record's outcome and filename name the diagnostic that matters. This is recorder- and reader-side precedence only (`summarize` applies the same rule); the supervisor's routing, `handled`, hard-fail and wake behaviour are untouched, and the suite proves the marked node still shows `hard_fail_run` 1, the error row and the abandon. Every call is also a `dispose` event so the record shows each claim in order. The dispositions and where they are set:

| outcome | set by |
|---|---|
| `completed` | the success tail (claude lane), or a codex/antigravity status other than interrupted |
| `interrupted` | the ⏸ flag observed after exit (the success tail still runs), or a provider status `interrupted` |
| `frozen` | the limit-freeze branch, the connection-retry branch (`net_retry`, run ≤ MAX), or the provider-seam freeze in the except (`provider_limit`) |
| `killed` | the watchdog/budget raise (claude), the per-message ceiling raise (codex, antigravity) |
| `abandoned` | the terminal door when `_turn_abandoned` drove (first hard fail), or `net_exhausted` when `_retry_exhausted` drove |
| `unrecoverable` | `mark_unrecoverable` |
| `redriven` | the account-switch branch |
| `failed` | the terminal door otherwise; `net_exhausted` beyond the drive; the except's default for a `RuntimeError` no path named |
| `crashed` | the except's default for any other exception class |
| `unknown` | nothing named one (the close of a wrapper whose body never reached a disposition) |

A `watchdog` event is not the outcome by itself: the disposition is whatever the exit path last named (a wall past the ceiling on antigravity is the wall, not a kill — main 2deb7d7, preserved).

## Correlation

- **Attempt ↔ fixture**: `failfix.record` returns the path it wrote (`_failfix_record` now returns it too); the site hands it to `Recorder.fixture`, which keeps the BASENAME only when it matches the generated-name pattern `^\d{13}-\d{4}-(phase)-(verdict)\.json$`. Readers validate the stored value with `is_fixture_name` (strict: no separator, no parent reference) and `fixture_path` resolves it ONLY inside the sibling `failfix/<org>/<node>/` of the record's own `turnlog/` root, requiring the file to exist; anything else is `None`.
- **Attempt ↔ attempt** (one failure run): `run` and `run_since_ms` (the supervisor's own counter and origin); `resumed`.
- **Attempt ↔ node**: the directory. Nothing inside the file names the org or node.

## Capture and write boundaries

- **The handle is attempt-specific.** `_run_one_turn` is now a thin wrapper: it opens the recorder, calls `_run_one_turn_recorded(…, trec=…)` and closes the recorder in its OWN `finally`, outside the turn's try/finally — so the record is finalized even when the turn's own cleanup raises. The codex and antigravity legs take `trec` as a keyword parameter (signature change accepted for correctness) and their callbacks close over it: a late callback of attempt A cannot reach attempt B's record, and a closed recorder drops (returns False) rather than accepting.
- **On disk, twice**: (1) at open, a ~300-byte **stub** `<stamp>-<seq>.partial.json` (header, `partial: true`, no events); (2) at close, the full record `<stamp>-<seq>-<lane>-<outcome>.json` is written atomically (`os.replace`) and the stub removed. **A stub means an UNFINALIZED record** — a live attempt, a finalization that raised, a write that failed, or a backend that died — it does not by itself say which.
- **Where close runs and what it costs**: at the end of the wrapper's `finally`, after the turn's shared `finally` has released the queue and notified `turn_done`. It is a synchronous write on the turn's thread: it adds its milliseconds before the caller receives the next queued carrier. Nothing the turn does *waits on* it, but the return is delayed by it — said plainly.
- **Threads**: `emit` appends under the recorder's lock; no I/O, no sleep, no other lock. Safe from the watchdog, pump and callback threads.
- **Close is one critical section**: the closed flag, the disposition, the `end` event (appended through the same private locked step `emit` uses — the recorder is never reopened to the public `emit`) and the SNAPSHOT of the events are taken under one lock; the write happens after. An emit arriving during the write is refused and cannot reach the record; a second concurrent close returns `None` and writes nothing (both proved by deterministic controls in §2, negative and positive). fable-verify measured the v2 defect: a late emit landing in a finalized record and flipping a completed turn's implied outcome to killed.

## Fail-open — recording cannot change an outcome

Every recorder method is wrapped: an exception inside is swallowed and counted (`recorder_errors`). Call sites pass simple expressions; where an argument needs computation it goes through a fail-open helper in `turnread` (`assistant_shape`, `tool_result_shape`, `result_shape`, `init_shape`, `window_of`, `seconds_of`) or inside an existing fail-open block, so no site-side expression can raise on the production path. The suite runs the died-in-flight scenario three ways — (a) the org's `turnlog/` path obstructed by a file, (b) `Recorder.emit` itself (guard included) replaced by a raiser, (c) the writer replaced by a raiser at open and close — and asserts the node document (frozen state, retry counter, hard-fail counter, error rows, mail count) is **identical** to the control run, with a positive control showing the comparison key distinguishes a completed turn. No retry, route, wake, freeze delay or timeout reads anything the recorder holds; the only read is `disposition`, by the exit paths that write it.

## Bounds, retention, truncation

- **In memory, while emitting**: the first `HEAD` = 64 events and a deque of the last `TAIL` = 176; the middle is dropped as it happens and counted in `dropped` / `dropped_kinds` (a closed vocabulary, so bounded). Text deltas produce only `first_output`. Every string leaf is cut to 48 chars before vocabulary lookup, every list to 8, every int bounded to ±10^15 (else null).
- **On disk**: `CAP_BYTES` = 64 KB; over it, events are cut from the middle until it fits, `dropped` grows and `truncated: true` is set; the header is never cut.
- **Ring**: `RING` = 60 files per node — records AND stubs — enforced at OPEN (before the stub is written) and at close, so repeated unfinalized attempts cannot accumulate stubs. Size per record and per-node retention in wall time are **unmeasured**; the fake-lane records in the suite are 2–6 KB.
- **Off switch**: `ORGTREE_TURNLOG=0` opens no recorder; every site tolerates `None`.

## Schema and compatibility

- New directory `turnlog/`; an older build ignores it. No org-document field, no SQLite table, no JSON-store field: the SQLite/JSON backends, the rollback path and the `BackendMismatch`/`MigrationRefused` guards are untouched. `failfix` schema stays 4 and its four sites keep their arguments; only the returned path is now read. Old fixtures load unchanged.
- `turnread.load` refuses a record whose `schema` is not 1.

## Offline inspection — what replay is and is not

```
python tools/inspect_turn.py <record.json> [more …] [--json] [--assert]
```

- Prints the header, the timeline (`seq  t_ms  kind  fields`) and the `partial` / `dropped` / `truncated` indicators, then the **summary** `turnread.summarize` derives from the events alone: `phase` (failfix's table from `first_output`/`result`/`exit`/`codex_decide`), the disposition the events **imply** (the same precedence the sites dispose with: a freeze, an abandon, an unhandled terminal owner, an interrupt, a watchdog, a provider status — last wins, a kill stays a kill when the abandonment mail follows), first-output and boundary latencies, and whether `seq` is ordered. `drift` names disagreements between the implied and the recorded outcome and any order violation; `--assert` exits 1 on drift.
- **It never copies**: `dispose` and `end` events are excluded from the derivation (mutant M4). A `partial` or `truncated` record, one with a HEAD/TAIL gap (`dropped` > 0 — the omitted middle can hide the deciding event, an unrecoverable owner or an interrupt, even with the tail kept), or one with no events, is **insufficient**: the summary says so (`evidence`, `gapped`), implies nothing, and drifts against nothing (M5); the retained timeline is still printed and counted.
- When the record names a fixture that resolves beside it, the existing classification replay (`replay_failure.PREDICATES`) runs on it and its drift is added as `fixture.*`. A name that does not resolve is reported, not read.
- **What it is not**: nothing is re-executed — no provider, CLI or supervisor runs, and no branch the supervisor WOULD take is computed (that reads the retry counter, lane policy and pause state, which a record does not hold).
- Purity: the tool imports `turnread` (never `turnlog`, which needs a lock), `failfix`, `failclass`, `codex_decide`; the suite runs it under the import hook that refuses `store`/`supervisor`/`ledger`/`codex_route`/`providers`/`warmpool`, `subprocess`, `socket`, `http`, `urllib`, `sqlite3`, `threading` and every file write, with a control proving the hook refuses `orgtree.store` and a write.
- Malformed input: a record that is not an object, whose `schema` is not the integer 1, whose `events` is not a list, or with an event lacking integer `seq`/`t_ms` or a string `kind`, yields one `malformed record …` line on stderr and exit 2 — never a traceback (seven cases in §9, with the valid control). Filename patterns are full-string matches (`fullmatch`), so a trailing newline is not a name.

## Evidence (2026-09-06, branch luna-turn-events)

Evidence files live in the author's scratch, NOT in the repository: `C:\Users\ncola_k8bx\orgtree\scratch\orgtree\luna-reserve\evidence\` (`turnlog-red-8d63b74.log`, `mutants-turnlog.log`, `turnlog-turn-lifecycle-branch.log`, `turnlog-turn-lifecycle-main.log`).

- `test_turn_events.py`: 43/43 on the branch after the review corrections (36/36 at 167aac5), with the coordinator's two reader boundaries (empty freeze → absent fields; a gap → insufficient, beside the same events complete drifting) added as targeted controls in §1–§3. §1 schema/coercion/canaries/shapes; §2 recorder (six-thread order, head/tail drop with counts, stale emit and close refused, cost null vs 0.0, CAP truncation, ring at open and close over 75 leftovers, off switch, the under-the-lock probe); §3 summarize/drift (frozen, never-copied, precedence, five phases plus a codex admission, insufficient, fixture names and containment); §4 claude lane through the fake CLI — completed, died-in-flight (fixture resolves and agrees), 401 with planted secrets (abandoned), dead-on-arrival (pre_model), hang with `TURN_IDLE`=1 (killed), manual interrupt; §5 OpenRouter typed 429 (frozen); §6 codex tool / usage_limit / plain_failure; §7 antigravity text / usage_limit / plain_error past a 1 s ceiling / a real mid-turn ⏸; §8 fail-open ×3 against the control plus a positive control; §9 the inspector under the hook (renders, drifts on an edited outcome, reports an unresolved fixture, renders a stub as partial, seven malformed records, hook control). Added at v3: the close-race controls, the numbered head/tail retention, sticky unrecoverable in the reader, the REAL unrecoverable path (the node marked, the terminal door's bump/log/abandon unchanged, outcome and filename unrecoverable, non-circular drift), the exhausted door (net_fail_run MAX+1, no hard counter, node's hard_fail_run unset) beside the terminal door (hard_fail_run 1), and a claude limit freeze whose event fields equal the written FrozenInfo.
- **Red proof** on main 8d63b74's supervisor with the new modules copied in (`turnlog-red-8d63b74.log`, taken at 167aac5): exactly the 14 real-lane checks fail (no record is written), §8/§9 declare themselves inert, the 15 pure checks pass.
- **Mutants** for the pure checks (`mutants-turnlog.log`): M1 stamp outside the lock (rejected only after the under-the-lock probe was added — the ordering assertions alone let it survive), M2 dropped not counted, M3 unknown string kept, M4 summary copies end, M5 partial not insufficient, M6 basename validation, M7 no eviction at open, M8 watchdog overrides a later freeze, M9 over-cap unflagged, M10 unknown cost 0.0, M11 stale emit accepted, M12 handled terminal overrides the freeze, M13 admission from any typed status, M15 lists uncapped — M16 sticky unrecoverable dropped from the reader, M17 close reopening the recorder to the public emit — all rejected. **M14** (containment check removed) survives and is an equivalent mutant: the strict name regex admits no separator, so no name reaching `fixture_path` can leave the directory; the check is defence in depth and is kept, not credited (fable-verify attacked the claim independently and agreed).
- Observed and followed, not promised: the connection-retry branch DOES reach the terminal fixture site (`handled` true, `run` read from the document), so a `net_retry` record names a fixture; `dead-on-arrival` never retries; the antigravity `interrupt` scenario needs a real `interrupt_turn` mid-turn (the fake stalls until killed) — the suite drives one.
- Sibling suites on the branch at 167aac5: failure-fixtures 45/45, provider-limit-freeze 12/12, codex-dispatch 25/25, antigravity-dispatch 30/30, route-fallback-scope 25/25, limit-freeze 268/268, luna-reserve-route 51/51. Two AST-extraction suites needed the new names bound: `test_antigravity_stream_order` (the leg's bodies exec over a built namespace — `turnlog` and `trec=None` are now bound there, 112/112) and `test_harvest`'s ORDER check (it now finds the turn body — `_run_one_turn_recorded` — by the try-with-handlers it argues about, 39/39); `test_auth_cause` 26/26, `test_fable_piggyback` 32/32, `test_runner_truncation` 12/12 unaffected. `test_turn_lifecycle` on this machine: branch 234/2 and 231/5 across two runs, main 8d63b74's untouched supervisor 231/5 run alone — the same dupresult / retract-canary / freeze-cleared family on both, so those failures are pre-existing here, not from this change (`turnlog-turn-lifecycle-branch.log`, `turnlog-turn-lifecycle-main.log`). `test_limit_freeze` likewise: 265/3 on the branch and 264/4 on main, the same three time-dependent checks (`turnlog-limit-freeze-9e2f815-quiet.log`, `turnlog-limit-freeze-main-now.log`).
- Known limits: `delivered` appears only on turns carrying mail tokens; subagent (sidechain) assistant/user events are not recorded; a stub cannot say why it was not finalized; per-node retention in wall time is unmeasured; `summarize` on a completed claude turn reports `phase: unknown` (phase is a failure notion, as in failfix).
