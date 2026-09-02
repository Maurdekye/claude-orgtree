# App-state invariants — the canonical register

This file records **explicit user-stated invariants**: hard constraints on
running application state that the user has stated, directly or through a
ruling accepted on their behalf, and that hold regardless of which decision
implements them today. It is deliberately narrower than
[`DECISIONS.md`](../DECISIONS.md). Not every ruling in that register is an
invariant — most are design decisions, UI specifics, or implementation
choices that could legitimately change without contradicting anything the
user said. An entry belongs here only when it is substantiated by an
explicit user statement (quoted sparingly, paraphrased into normative
language) about a state the running system must never reach, or must always
reach, and it stays here even if the decision that currently implements it
is later rewritten.

## Authority

**User authority ruling (coordinator, 2026-09-02, on the user's behalf):**
an app-state invariant recorded here outranks an ordinary `DECISIONS.md`
entry. A decision may implement an invariant, explain it, or add a
*stricter* guarantee on top of it, but **MUST NOT** weaken it, supersede it,
reinterpret it, or carve out an exception the user did not state. If code,
docs, tests, or a decision conflict with an invariant recorded here, the
invariant wins and the conflict is itself an enforcement failure — record it
as a `known_gap`, do not quietly narrow the invariant to match the code.
Only a new, explicit user amendment may change an invariant; every entry
below carries a **Provenance** line (where the statement came from) and an
**Amendments** line (empty until one occurs, then dated and reasoned).

## Status vocabulary

Every entry's `Status` is exactly one of:

- **`enforced`** — implemented, and an observable mechanism (test, log line,
  UI state, or durable record) demonstrates it holds today.
- **`implementation_in_flight`** — the user has stated the invariant, the
  implementing work exists (a design, a partial diff, an in-progress
  branch), but it does not yet hold end-to-end. Do not upgrade this to
  `enforced` on a promise; upgrade it when the owning test passes on a
  committed change.
- **`known_gap`** — the invariant is stated, and either nothing enforces it
  yet, enforcement is partial, or a specific, named case is known to violate
  it. The gap itself must be named, not generic.

There is no fourth "unknown" or "unaccounted" value. An invariant this
register cannot currently classify is a defect in the register, not a
license to leave the field blank — see
[`../backend/tests/test_invariant_register.py`](../backend/tests/test_invariant_register.py),
which fails the suite on a missing field, a duplicate ID, or a status
outside this vocabulary.

## Entry template

```
### INV-NNN · short imperative title

- **Statement:** MUST / MUST NOT, one or two sentences, normative.
- **Scope:** what part of the system, and which actors, this binds.
- **Prohibited states:** the concrete state(s) this rules out.
- **Allowed exceptions:** explicit carve-outs the user stated, if any. "None
  stated" if none.
- **Observable enforcement:** how an observer (agent, developer, or the
  user) can see this holding right now.
- **Owning references:** test file(s) and/or `DECISIONS.md` entry ID(s).
- **Status:** enforced | implementation_in_flight | known_gap
- **Provenance:** who said it, when, and where (quoted sparingly).
- **Amendments:** none, or a dated log of user-issued changes.
```

---

## A · Task and turn lifecycle

### INV-001 · a running task is never silently both-inactive

- **Statement:** while an agent owns a task the org believes is running,
  exactly one of {the task/turn is actually active, a recovery turn or
  lease to deal with its end is active} MUST hold at any observed instant.
  Both being inactive at once — the org still believes the task is live,
  nothing is driving a turn for that agent, and nothing has scheduled one —
  is an invalid state. A superior's periodic checkup MUST NOT be the
  mechanism that discovers or corrects this; checkups are a backstop for
  unrelated staleness, not the intended path back to correctness.
- **Scope:** every backgrounded task (`Bash run_in_background:true` today;
  the fix is explicitly not shell-specific) and every turn/process a node
  owns.
- **Prohibited states:** a task or turn ends (process death, or an abnormal
  stop the CLI reports without the process dying) while the owning agent's
  turn has already parked believing it is still running, and nothing
  durable wakes that agent.
- **Allowed exceptions:** purely informational status (`"completed"`, or any
  unrecognized/absent status, which is left alone rather than guessed at)
  stays passive by construction — nothing new fires for it. That is not an
  exception to the invariant; it is the case where the task genuinely is not
  both-inactive.
- **Observable enforcement:** an abnormal stop produces durable mail
  (`kind="message"`, never `"notice"`) that wakes the owning agent exactly
  once; `backend/tests/test_bg_task_stopped_notification.py` D1 (line 158)
  reproduces the incident end-to-end and fails against pre-fix code, D2
  (line 206) proves a normal completion raises no false alarm, D3 (line
  237) pins `kind="message"` and that the agent was actually driven a
  second time (exact count, not mere mail presence). The process-death path
  is the older, sibling mechanism (`_bg_orphaned`/`_turn_abandoned`),
  exercised by `backend/tests/test_turn_lifecycle.py:2963` ("bg · killing a
  CLI with live children mails their parent") and `:3019` ("...and that
  notice actually wakes it") — D-225's own docstrings say it mirrors these.
- **Owning references:** `DECISIONS.md` D-225;
  `backend/tests/test_bg_task_stopped_notification.py`;
  `backend/tests/test_turn_lifecycle.py:2963,3019`.
- **Status:** enforced for the tested surface, with the caveat and named
  gap below. Verified unchanged since commit `53bd4a1` (HEAD `a7934d8`)
  directly with `stopped-task-wake`, the owning agent, 2026-09-02.
- **Provenance:** user report via coordinator, incident evidence from
  `fable-cli-migration` (D-225, 2026-09-01): a backgrounded shell died
  mid-flight and nothing woke the owning agent for 30 minutes, until an
  unrelated heartbeat incidentally surfaced the CLI's own passive
  reconciliation message. Coordinator's mail (2026-09-02) additionally
  states the invariant in its general, atomic form — one of {task active,
  recovery turn/lease active} must always hold — which is broader than
  D-225's single incident; `stopped-task-wake` is actively formalizing that
  general state machine (drive/`send_message` contract, freeze-resume,
  restart replay, warm-pool keeper interactions) and will hand this agent
  the canonical wording to fold in here.
- **Amendments:** none yet — pending formal wording from `stopped-task-wake`
  covering the cases below.

**Caveat, not yet resolved either way (do not read D-225 as closing this):**
`stopped-task-wake` reports D-225's mechanism is durable-mail-then-drive as
two *sequential* calls (write mail, then `send_message` to drive), and has
not yet confirmed there is no crash window between them, nor that the drive
call is retried/leased rather than fire-and-forget. Treat the tested surface
as **provisionally enforced, atomicity unverified** until that audit lands.
Backend-restart/crash replay, freeze/resume auto-wake, and cheap-compaction's
handling of an outstanding background task are separately unconfirmed.

**Named gap (`known_gap` scope within this entry, confirmed real):**
`warmpool._keeper_pass` → `kill_node("identity-changed")`
(`warmpool.py` ~2092-2199) kills a **parked** process with zero check for a
live background task and no notification/mail/drive as a result. D-225's
own forensics name this exact gap (`DECISIONS.md:8048-8051`) as "real and
separately orphan-blind... a candidate for a narrower follow-up, not folded
into this fix." The only existing test touching this path,
`backend/tests/test_warmpool.py:469` ("D5 · an idle identity change respawns
immediately", body ~line 437), asserts only exit-reason bookkeeping and says
nothing about background-task notification. `stopped-task-wake` is actively
closing this gap (in scope as of coordinator's 2026-09-02 mail); update this
entry when it lands rather than leaving it stale.

---

## B · Provider, cache, and session identity

### INV-002 · cache compatibility readiness is binary, not a spectrum, and grey is always accounted for

- **Statement:** the cache-compatibility badge MUST render exactly one of
  `ready` (green) or `not_ready` (red) for any supported provider/lane in
  normal operation. Grey (`diagnostic`) MUST NOT be used as a third opinion
  about the cache; it is reserved for an enumerated, named fault that
  prevented an opinion from forming at all, and every grey MUST carry a
  machine-readable cause plus a human-actionable detail sentence. An
  absent, unrecognized, or unparseable readiness payload MUST resolve to
  the named `internal_error` diagnostic — never to green (**the badge fails
  closed**), and never to a silent/generic unknown. A live countdown MUST
  appear only while readiness is `ready` **and** an authoritative
  `expires_at` derived from a positive receipt exists (readiness alone is
  not sufficient); once elapsed, or once readiness is anything but `ready`,
  the badge MUST fall back to the readiness verdict.
- **Scope:** the per-node cache forecast surfaced to agents (`cache_forecast`
  API/WebSocket field) and rendered on the desk badge.
- **Prohibited states:** green with no affirmative evidence of compatibility
  (the prior D-214 `no_completed_fingerprint` → green reading is
  overridden by this invariant, not merely superseded); a live countdown
  while readiness is not `ready`, or with no authoritative `expires_at`; a
  grey badge with no cause or no detail sentence; an unclassified cause
  defaulting to anything other than the named `internal_error`; a generic
  catch-all coercion of an unrecognized state or lane.
- **Allowed exceptions:** none stated — the cause table is exhaustive by
  construction, and the owning test suite asserts the invariant's
  *properties* (exhaustiveness, fail-closed, no catch-all) rather than the
  current contents of the cause table, specifically so table and invariant
  cannot silently diverge.
- **Observable enforcement / current state (verified directly with
  `turn-envelope-cost`, the implementing owner, and `readiness-postreview`,
  independent reviewer, 2026-09-02):** the invariant is **violated today on
  committed `main` (`a7934d8`)** in at least four concrete, cited ways:
  1. D-214 (`DECISIONS.md`) renders `no_completed_fingerprint` on a
     supported lane green — directly contradicts this invariant. Pinned
     (enforcing the *old* decision, not the invariant) by
     `frontend/tests/cacheforecast.test.tsx:101`.
  2. Multiple `uncertain` sources render grey `?` with no enumerated,
     machine-readable cause: `backend/orgtree/cachecontinuity.py` lines 162
     (`no_completed_fingerprint`), 243 (`history_unobserved`), 257
     (`no_positive_receipt`), 268 (`receipt_prefix_unobserved`), 287
     (`ttl_unobserved`), 299 (`clock_skew`); rendered by
     `frontend/src/canvas/desk.tsx:427-431`.
  3. Silent generic fallthrough: `cachecontinuity.py` `public()` lines
     412-415 (unknown state → `"uncertain"`) and 427-429 (unknown lane →
     `"unobserved"`).
  4. Gemini and Codex API-key lanes reach `ttl_unobserved` /
     `no_positive_receipt` instead of an explicit unsupported-capability
     diagnostic (`ttl_seconds` lines 108-115 returns `None` for them without
     naming the gap).
  Correct today, already enforced: countdown expiry renders red, not grey
  (`desk.tsx:419-431`; `heal_quantized_skew` line 366).

  A fix for all four is **written, uncommitted, working-tree only** on
  `main` at `a7934d8` (`backend/orgtree/cachecontinuity.py`,
  `backend/orgtree/supervisor.py`, `backend/tests/test_cache_continuity.py`,
  `frontend/src/canvas/desk.tsx`, `frontend/src/types.ts`) — no branch, no
  commit. Its own new test file, `backend/tests/test_cache_readiness.py`
  (18 checks), and `frontend/tests/cacheforecast.test.tsx` +
  `frontend/tests/cachecountdown.test.tsx` (16 checks) are passing, but the
  owner has **not** run the full repo suite against the change and
  explicitly asked that this entry **not** be marked enforced until it is
  committed and full-suite validated.
- **Owning references:** `backend/orgtree/cachecontinuity.py` (`READINESS`,
  `READINESS_DETAIL`, `EVIDENCE_REQUIRED`, `SUPPORTED_LANES`,
  `readiness_fields`, `capability_evidence`); `backend/orgtree/supervisor.py`
  (`_readiness_incident_log`, `cache_forecast_public`);
  `frontend/src/types.ts` (`Readiness`); `frontend/src/canvas/desk.tsx`
  (`readinessOf`, `readinessCause`, `cacheExpiryAt`);
  `backend/tests/test_cache_readiness.py`;
  `frontend/tests/cacheforecast.test.tsx`;
  `frontend/tests/cachecountdown.test.tsx`;
  `public_projection_cannot_fail_open` (backend suite, fail-closed pin).
  The pending diff now includes a drafted `DECISIONS.md` D-226 entry
  (uncommitted, working tree only, as of this survey) that explicitly
  states it **implements** this invariant and overrides the conflicting
  D-214 decision — it does not create the invariant, and this entry is the
  authority, not the decision.
- **Status:** known_gap (on committed `main`, concretely per the four items
  above), with its remediation `implementation_in_flight` (uncommitted,
  passing its own new tests, not yet full-suite validated or merged). Do
  not upgrade to `enforced` until the owner confirms commit + full-suite
  pass.
- **Provenance:** user ruling, 2026-09-02: green requires affirmative
  evidence of compatibility, and the absence of all evidence is not that;
  the prior D-214 green-on-no-evidence reading is explicitly overruled.
  See also `docs/cache-continuity.md` for the underlying forecast-state
  model this readiness layer sits on top of, and
  [INV-003](#inv-003--a-local-restart-is-not-proof-of-a-cache-miss-and-provider-switching-is-a-known-break)
  for the base cache-namespace rule.
- **Amendments:** none.

### INV-003 · a local restart is not proof of a cache miss, and provider switching is a known break

- **Statement:** a local backend or CLI-process restart MUST NOT, by itself,
  be treated as proof that a provider cache miss occurred. Conversely,
  switching provider, account/auth lane, model, or session lineage MUST
  always be treated as a known cache-namespace change, and the system MUST
  NOT present that switch to an agent as an ordinary local restart — it can
  also cost provider-specific session/context continuity, not merely warmth.
- **Scope:** every agent's next-turn cache forecast and the `CACHE
  CONTINUITY` block injected into every managed system/startup prompt.
- **Prohibited states:** inferring `known_incompatible` from a bare local
  restart with no other evidence; describing a provider/account/model/
  lineage switch as compatible-by-default or as "just a restart."
- **Allowed exceptions:** none stated.
- **Observable enforcement:** the persisted, generation-owned next-turn
  forecast in `backend/orgtree/cachecontinuity.py`; the standing
  `[CACHE CONTINUITY]` doctrine block present verbatim in every managed
  system prompt (see this agent's own system prompt for a live instance).
- **Owning references:** `docs/cache-continuity.md`; `backend/orgtree/
  cachecontinuity.py`.
- **Status:** enforced.
- **Provenance:** user ruling, captured as the stable agent doctrine in
  `docs/cache-continuity.md` and repeated verbatim in every agent's system
  prompt's `[CACHE CONTINUITY]` block.
- **Amendments:** none.

### INV-004 · the Claude lane adds no MCP-handshake barrier, anywhere

- **Statement:** MUST NOT: block admission of a Claude turn on an MCP
  *handshake* barrier — i.e. wait for MCP registration/initialization
  itself to complete before the turn is allowed to proceed. This is
  narrower and sharper than "no wait of any kind": MAY: optionally, and
  only when an operator explicitly enables it, delay writing the prompt to
  stdin while a *separate* bounded gate (`_mcp_wait_for_surface`) checks
  whether the current runtime tool list covers the *last completed turn's*
  surface (see [INV-005](#inv-005--the-mcp-tool-surface-reported-for-a-turn-is-generation-correct-or-explicitly-not-ready)).
  That gate is not a handshake barrier and does not violate this invariant;
  do not "enforce" this entry by deleting it.
- **Scope:** Claude CLI process admission (`backend/orgtree/warmpool.py`)
  and the point in `backend/orgtree/supervisor.py`, immediately before
  `proc.stdin.write(_user_event(...))`, where the optional gate (if enabled)
  runs.
- **Prohibited states:** a Claude turn refusing to start, or being held,
  because MCP tool *registration* itself had not completed — as distinct
  from the optional, different, last-surface-coverage gate.
- **Allowed exceptions:** the CLI's own `alwaysLoad` setting can still hold
  a cold or too-young process's first prompt for its connection timeout —
  provider-side behavior orgtree does not control, not an orgtree-imposed
  wait; the retain/revert policy around it is explicitly still open
  (tracked as a gap, not claimed closed here). The Codex lane is
  deliberately different in kind (D-216): its app-server completes a real,
  asynchronous, keeper-side `initialize()` handshake before a seat is
  marked warm, precisely because treating it like Claude made "warm" a lie
  there — this is not an exception to the Claude-specific rule, it is a
  different lane with its own rule.
- **Observable enforcement:** `backend/tests/test_mcp_alwaysload.py` exists
  specifically to keep a real handshake barrier out; the deliberate removal
  of a permanently-zero `handshake_ms` journal field (`journal_admit`
  docstring in `backend/orgtree/warmpool.py`) because nothing at that seam
  ever observed one.
- **Owning references:** `DECISIONS.md` D-201 ("There is NO wait for the
  MCP handshake anywhere"), D-216 ("D-201's Claude rule stands untouched:
  the Claude lane adds no MCP-handshake barrier anywhere");
  `backend/orgtree/warmpool.py` (module docstring, `journal_admit`);
  `backend/tests/test_mcp_alwaysload.py`;
  `backend/tests/test_mcp_readiness.py`
  (`test_default_off_preserves_no_wait_behavior` — the separate gate,
  default off).
- **Status:** enforced.
- **Provenance:** user ruling, 2026-08-30, D-201, reaffirmed verbatim by
  D-216. Verified directly with `mcp-readiness` (2026-09-02), who corrected
  an earlier draft of this entry that mischaracterized the optional gate as
  merely "post-admission" — it runs before stdin write and does delay the
  turn when enabled, but delaying on last-surface coverage is not the same
  act as a handshake barrier, and the user's MUST NOT targets the latter.
- **Amendments:** none.

---

## C · MCP tool surface

### INV-005 · the MCP tool surface reported for a turn is generation-correct or explicitly not ready

- **Statement:** the surface reported for the current provider-process
  generation MUST NOT be, or be satisfiable by, a different generation's
  tool names — a replaced/dead process's names MUST NOT be inherited by, or
  able to satisfy, its successor's or a later generation's readiness check.
  The reported surface MUST NOT name tools without a count, and MUST NOT
  report a count that contradicts the names actually held. An **unobserved**
  measurement (`None`, "not measured this turn") MUST NOT destroy a
  previously **observed** one (including an observed *empty* surface,
  `[]`, which is a real fact, not an absence of one) — three states
  (measured, measured-earlier, never-measured) must stay distinguishable,
  carried by `mcp_tool_count` and `last_turn_mcp_tool_count` together, never
  collapsed to one field. When the optional gate ([INV-004](#inv-004--the-claude-lane-adds-no-mcp-handshake-barrier-anywhere))
  is enabled, it MUST NOT report "ready" unless observed names are an exact
  superset of the last completed turn's names (count-match alone is not
  enough), and every non-ready outcome MUST carry a distinct, named label
  and an explicit, human-readable reason — never a generic or silent
  failure.
- **Scope:** `backend/orgtree/supervisor.py`'s `_mcp_wait_for_surface` and
  its supporting `_mcp_tool_count_*` state machine; the durable
  `last_turn_mcp_tools` / `last_turn_mcp_tool_count` surface record on the
  node document.
- **Prohibited states:** "ready" on a same-count/different-name surface; a
  dead generation's names satisfying a live generation's wait or readiness
  report; an unobserved boundary erasing a known-good baseline; names
  reported with no corresponding count.
- **Allowed exceptions:** Gemini (`provider == "google"`) is explicitly
  exempted from the optional wait gate — it exposes no authoritative
  runtime MCP tool list, so the gate reports `unsupported` and proceeds, by
  design. A missing/unreadable MCP configuration fails open as
  `unavailable`, explained. A bounded timeout always fails open
  (`timed-out`).
- **⚠ Scope caveat (do not overstate):** in the shipped default
  configuration, none of the gaps below prevented a turn from actually
  running with its tools loaded. What was and is at risk is the
  **truthfulness and durability of the recorded surface**, and — only where
  an operator has opted into the wait gate — that gate's verdict. This
  entry is about the accuracy of a record and a readiness signal, not about
  tools failing to load.
- **Observable enforcement / current state (verified directly with
  `mcp-readiness` and `readiness-postreview`, 2026-09-02):** on committed
  `main` (`a7934d8`), several of the MUST NOTs above are **violated today**:
  a replaced process's stale names could satisfy a newer generation; an
  EOF-before-capture could erase a durable baseline; a count-only refresh
  could resurrect invalidated names. An unlanded branch,
  `fix/mcp-reload-on-cli-replace` (7 commits, tip `dddba3b`, based on
  `53bd4a1`, rebases cleanly onto `a7934d8`), fixes the violations that
  exist on main — independently re-verified by `readiness-postreview` (37
  tests + 13 mutants, green / 12 killed as expected) — but is **not
  merged**, and its own tip commit (`dddba3b`) currently **regresses**
  `test_status_zero_vs_unknown`. Do not record the branch's unit-3 behavior
  as enforced.
- **Owning references:** `backend/orgtree/supervisor.py`
  (`_mcp_wait_for_surface`, `_mcp_tool_count_begin`, `_mcp_tool_count_names`,
  `_mcp_infrastructure_fingerprint`); `backend/tests/test_mcp_readiness.py`;
  `backend/tests/test_mcp_tool_count.py`;
  `test_replacement_does_not_inherit_the_dead_generation_names`,
  `test_replaced_process_cannot_satisfy_the_gate_with_dead_tools`,
  `test_a_later_generation_never_inherits_the_recovered_surface`,
  `test_a_live_surface_never_names_tools_it_cannot_count`,
  `test_a_count_only_refresh_invalidates_the_recovered_names`,
  `test_status_zero_vs_unknown` (all on the fix branch except the last,
  which exists and currently regresses there); no `DECISIONS.md` entry
  number exists yet for this feature — itself a named gap (`mcp-readiness`
  confirmed no D-22x entry exists).
- **Status:** known_gap. Named, currently-standing gaps, distinct from the
  count/coherence violations the unlanded branch addresses:
  1. **Claude-lane spurious-EOF false terminal.** A stdout EOF on a still-
     live process (`warmpool.py:391` → `_on_proc_exit` → `supervisor.py`
     `_mcp_tool_count_end`) falsely labels readiness `process-ended`; the
     recovery path added by the fix branch is reachable only via a full
     re-enumeration, which on the Claude lane arrives only over the same
     stdout that just closed — structurally unreachable there. Redesign
     (`scratch/orgtree/mcp-readiness/unit2-design.md`) is unbuilt.
     Evidence: `backend/tests/test_mcp_recovery_reach.py` (characterization,
     5 tests, green).
  2. **Unpinned recovery clause.** The recovery guard
     `final.get("owner") is owner` (~`supervisor.py:1150` on the fix branch)
     is not pinned by any test — deleting it kills nothing under mutation.
  3. **State/node inconsistency on an unobserved boundary.** The in-memory
     state twin `last_turn_mcp_tool_count` is still popped on an unobserved
     boundary while the durable node copy is preserved — the two records
     can disagree.
- **Provenance:** user-ruled feature; enforcement mechanism verified
  directly with `mcp-readiness` (implementing agent) and `readiness-
  postreview` (independent reviewer), 2026-09-02, including file:line
  citations against both `main` and the fix branch. See also D-201/D-216
  for the adjacent "no handshake barrier" rule this feature must not become.
- **Amendments:** none.

---

## D · Transcript, journal, and stream ordering

### INV-006 · a turn's user message is durable before any of its assistant output is visible

- **Statement:** no assistant-visible output for a turn (a delta, a text
  row, a tool row, or a thought row) MUST become visible before that turn's
  own user message is durable in the transcript. This MUST hold for a fresh
  thread's first turn and for every reconnect/resume of an existing thread
  alike — the barrier is a property of the code path every assistant-visible
  emission passes through, not of a sequence that happens to hold on the
  common case. A replayed item completion MUST NOT produce a second durable
  record and a second live row for the same logical answer.
- **Scope:** the Codex lane's turn/journal pipeline
  (`backend/orgtree/codexrun.py` / `AppServerClient`) and its render into
  `supervisor.stream`.
- **Prohibited states:** an agent's answer rendering above the question it
  is answering while that question still reads "delivering…"; a durable
  transcript with no user row for a turn whose assistant output has already
  rendered; a duplicated completion producing two live rows for one durable
  record.
- **Allowed exceptions:** if the journal never opens at all (the thread id
  never arrived because `turn.start()` raised), held output is never
  released — there is no transcript for that turn, so releasing assistant
  prose would show it under a turn the server cannot account for; the
  turn's own durable error row is what renders instead. An item with no id
  is explicitly NOT deduplicated — a missing identity is not evidence of a
  repeat, by the ruling's own reasoning ("a duplicate is a blemish where a
  gap is a lie"); this is stated here as a **named, accepted gap**, not a
  silent one — confirm with `codex-stream-order` whether it has since been
  tightened.
- **Observable enforcement:** the journal opens at `on_thread` inside
  `CodexTurn.start()`, before `turn/start` goes on the wire; every
  assistant-visible emission passes an ordering barrier held until the
  durable record exists; item completions are deduplicated by item id.
  `backend/tests/test_codex_stream_order.py` checks `supervisor.stream`
  (what the desk actually sees) against the on-disk journal at the same
  instant, and is confirmed to fail against pre-ruling code on exactly the
  four ordering checks it targets.
- **Owning references:** `DECISIONS.md` D-221;
  `backend/tests/test_codex_stream_order.py`.
- **Status:** enforced, with the no-id-dedup case tracked as a named,
  accepted exception rather than a gap (see Allowed exceptions) pending
  confirmation from the owner.
- **Provenance:** ruling (codex-stream-order, 2026-09-02), stated as: "no
  assistant output for a turn may become VISIBLE before that turn's user
  message is DURABLE in the transcript." Root-caused to a live symptom: the
  desk drew the durable block first, the live tail under it, and the user's
  own undelivered message at the very bottom — so a fast Codex response
  could render above a question still shown as undelivered.
- **Amendments:** none.

### INV-007 · mail is at-least-once; it may stall, it must never disappear

- **Statement:** a message accepted into an agent's mailbox MUST eventually
  be delivered or remain visibly present in the mailbox. A failure
  downstream of acceptance (a closed pipe, a turn-boundary misclassification,
  an exception) MUST NOT silently drop the message; at worst it stops moving
  and stays queued for the next attempt.
- **Scope:** the org mail system end-to-end — `orgtree_message`,
  `orgtree_send_notice`, and the turn loop's mail-draining boundary.
- **Prohibited states:** a message accepted into a mailbox that is neither
  delivered nor still present anywhere.
- **Allowed exceptions:** none stated — the invariant is about loss, not
  about latency; a stalled-but-present message is compliant, a lost one is
  not.
- **Observable enforcement:** the closed-pipe incident (D-136) traced a
  "stopped moving" bug to a mis-caught exception type and confirmed, by
  reading the mailbox directly, that the message had folded back
  undelivered rather than vanished — "the at-least-once invariant held...
  but it stopped moving."
- **Owning references:** `DECISIONS.md` D-136 (turn-boundary/closed-pipe
  fix); `backend/orgtree/store.py` (mailbox persistence — the org document
  is the durable record per INV-009).
- **Status:** enforced.
- **Provenance:** established in code and treated as load-bearing throughout
  the mail system's design; directly evidenced by the D-136 forensic
  read-back of a stalled mailbox.
- **Amendments:** none.

---

## E · Ledger, credits, and permission containment

### INV-008 · scope only ever shrinks moving down, never grows past the grantor

- **Statement:** a report's granted directories, tools, MCP surface, and
  visibility MUST always be a subset of what its grantor holds (the "⊆
  invariant"). A move, promotion, or demotion MUST NOT let a node retain a
  parent-bounded capability its new position does not itself hold.
- **Scope:** every `orgtree_hire`, `orgtree_retool`, and `orgtree_move`
  across the whole tree.
- **Prohibited states:** any node holding a directory, tool, or MCP grant
  its current chain of superiors does not also hold.
- **Allowed exceptions:** a user audience is explicitly NOT parent-bounded —
  its grantor is the user, not the chain, so it survives a move intact and
  merely goes dormant (not lost) while its holder is top-level, resurfacing
  on demotion. This is a stated, deliberate exception, not an oversight —
  "do not fix it."
- **Observable enforcement:** `backend/tests/test_dir_grant_containment.py`.
- **Owning references:** `DECISIONS.md` D-095 (moves and the ⊆ invariant);
  `backend/tests/test_dir_grant_containment.py`.
- **Status:** enforced.
- **Provenance:** ruling (user, 2026-08-05): moves shrink parent-bounded
  capabilities to fit the new position; a user audience is named as the one
  stated exception because its grantor is the user, not the chain.
- **Amendments:** none.

### INV-009 · the credit cap is one invariant, checked before every save, and binds the admin too

- **Statement:** no operation — hire, cascade, rehire, reallocation,
  approval, or admin action — MAY push an org's total top-level credit
  holdings past its configured `kiosk.credits` cap. The cap MUST be checked
  before save as a single invariant, not as N per-operation checks that can
  drift out of sync with each other, and it binds the administrator
  identically to every other actor.
- **Scope:** every credit-affecting operation across the ledger.
- **Prohibited states:** total top-level holdings exceeding `kiosk.credits`
  at rest, for any reason, including an admin action.
- **Allowed exceptions:** the cap itself can never be *set* below current
  holdings — that is a constraint on changing the cap, not a way to exceed
  it. This is distinct from the permission ceiling (`kiosk.max_scope`),
  which clamps over-ask requests with a named warning rather than refusing —
  normal orgs have no ceiling at all.
- **Observable enforcement:** the pre-save check fires uniformly across
  hires, cascades, rehires, reallocations, and approvals rather than being
  reimplemented per call site.
- **Owning references:** `DECISIONS.md` (permission-ceiling/credit-cap
  ruling, user, 2026-07-31); `backend/tests/test_kiosk_ceiling_identity.py`;
  `backend/tests/test_ledger.py`; `backend/tests/test_ledger_authority.py`.
- **Status:** enforced.
- **Provenance:** ruling (user, consensus spec, 2026-07-31): "the credit cap
  is ONE invariant — no op may push total top-level holdings past
  `kiosk.credits`... it binds the ADMIN too and can never be set below
  current holdings."
- **Amendments:** none.

### INV-010 · the org document is the sole source of truth; a backend restart may lose in-flight turns, never ledger state

- **Statement:** runtime-only state (busy flags, queues, steer lists, process
  handles) MUST live in memory only and MUST NOT be treated as authoritative.
  The org document is the single source of truth for live/archived status,
  mail, and credits. A backend restart MAY lose an in-flight turn, but MUST
  NOT lose ledger state; recovery is redriving nodes with a waiting mailbox,
  never replaying from a shadow queue.
- **Scope:** the whole backend process boundary — every restart, crash, or
  redeploy.
- **Prohibited states:** ledger state (credits, live/archived status, mail)
  present only in memory and lost on restart; a shadow queue diverging from
  the org document.
- **Allowed exceptions:** an in-flight turn itself may be lost on restart —
  that is explicitly accepted, not a violation, because it is not ledger
  state.
- **Observable enforcement:** `backend/orgtree/store.py`'s durable org
  document read/write path; recovery on boot re-drives any node with
  waiting mail rather than consulting any in-memory record.
- **Owning references:** `DECISIONS.md` D-037.
- **Status:** enforced.
- **Provenance:** ruling (established in code; ratified with the durability
  wave, 2026-07-31).
- **Amendments:** none.

### INV-011 · session identity survives retire/rehire intact

- **Statement:** rehiring an archived, recoverable node MUST resume its
  exact prior conversation via its preserved `session_id` — retire/rehire is
  paging a mind to disk and back, not a fresh start with copied metadata. A
  live node reporting under an archived superior is invalid; deep rehire
  walks and rehires every archived ancestor first, but an `unrecoverable`
  ancestor stops that walk with an explicit refusal rather than silently
  re-seeding it.
- **Scope:** `orgtree_retire` / `orgtree_rehire` and the archived/live/
  unrecoverable node lifecycle.
- **Prohibited states:** a rehired recoverable node starting a new session
  instead of resuming; a live node whose superior is archived; an
  unrecoverable ancestor being silently re-seeded (which would archive a
  real session as a lost generation).
- **Allowed exceptions:** an `unrecoverable` node's own rehire IS a
  deliberate re-seed (fresh session, same identity/credits/reports/mailbox)
  that ignores any requested grant/tier change and says so explicitly — this
  is the one case where rehire does not resume the old session, because
  there is no session left to resume.
- **Observable enforcement:** `backend/tests/test_bearer_rehire_provider.py`;
  the `unrecoverable` counts-as-live-for-budget rule keeps a broken session's
  seat held until someone deliberately retires it.
- **Owning references:** `DECISIONS.md` D-038, D-039;
  `backend/tests/test_bearer_rehire_provider.py`.
- **Status:** enforced.
- **Provenance:** ruling (user, PLAN §4.3, 2026-07-28): "rehire preserves
  `session_id`... Retire/rehire is literally swapping an agent's mind to
  disk and paging it back unchanged." Extended by ruling (user, №31 + review
  C12, 2026-07-31) for the archived/unrecoverable ancestor-chain case.
- **Amendments:** none.

### INV-012 · a credit-request answer is honest about what it is

- **Statement:** the user MAY answer a credit request with any legal
  amount — below the ask, above it, or below the current grant down to the
  committed floor. A partial grant MUST be worded as a counter-offer, never
  as "approved," and MUST say the agent may re-ask or route around it. If
  there is genuinely zero headroom, the request MUST be refused outright at
  ask time with no card shown — a card the user could only ever refuse is
  not a real choice.
- **Scope:** `orgtree_request_credits` and its resolution.
- **Prohibited states:** a partial grant presented as full approval; a
  credit-request card shown when zero headroom exists.
- **Allowed exceptions:** none stated.
- **Observable enforcement:** dry-run stranding warnings before commit;
  outright refusal (no card) when `max_top_grant` is reached or the kiosk
  pool is fully held.
- **Owning references:** `DECISIONS.md` D-091.
- **Status:** enforced.
- **Provenance:** ruling (user, 2026-08-04).
- **Amendments:** none.

---

## F · Sessions, hooks, and process isolation

### INV-013 · personal hooks and MCP servers never run inside an agent session by default

- **Statement:** the operator's personal Claude Code hooks and MCP servers
  MUST NOT run inside an agent's session unless explicitly granted per-agent.
  This MUST hold simultaneously with mid-task steering (the PostToolUse
  steer hook) — one is not to be traded away to achieve the other.
- **Scope:** every agent CLI session launch.
- **Prohibited states:** an agent session inheriting the operator's personal
  hooks or MCP servers by default; a fix for hook isolation that breaks
  mid-task mail delivery, or vice versa.
- **Allowed exceptions:** explicit per-agent grant in the ⚙ panel.
- **Observable enforcement:** enumerated (not categorical) per-event hook
  suppression that keeps the steer hook while suppressing inherited ones —
  see `docs/ARCHITECTURE.md` §Supervisor for the mechanism.
- **Owning references:** `DECISIONS.md` D-004.
- **Status:** enforced.
- **Provenance:** invariant since the v0 spikes; mechanism corrected
  2026-08-01 after a live audit found the guarantee had silently held only
  on the no-steering branch.
- **Amendments:** none.

### INV-014 · every spawned CLI process dies with the backend

- **Statement:** every CLI child process spawned by the backend MUST die
  when the backend does — via a Windows job object (`KILL_ON_JOB_CLOSE`) or
  an `atexit` sweep elsewhere. A turn timeout MUST additionally reap its
  own in-container process explicitly, narrowed by that turn's session id.
- **Scope:** every spawned provider CLI process, on every platform.
- **Prohibited states:** an orphaned CLI process outliving a backend
  shutdown or restart and continuing to append to a transcript the
  restarted backend is also resuming (two writers to one transcript).
- **Allowed exceptions:** none stated.
- **Observable enforcement:** the job-object leash on Windows; the
  narrowed-by-session-id reap on turn timeout.
- **Owning references:** `DECISIONS.md` D-041.
- **Status:** enforced.
- **Provenance:** ruling (invariant, discovered live) after `update.ps1`'s
  force-kill of the backend was observed leaving orphaned CLIs writing to
  transcripts the restarted backend was concurrently resuming.
- **Amendments:** none.

### INV-015 · context occupancy is read from the latest real assistant message, never accumulated

- **Statement:** context-occupancy measurement MUST read the latest
  non-synthetic assistant message of a turn (input + cache_read +
  cache_creation tokens; zero-usage synthetic messages skipped). It MUST
  NOT read the stream-json `result` event's usage (cumulative across every
  API call of the turn) and MUST NOT sum usage across turns.
- **Scope:** every context-occupancy read that feeds compaction decisions,
  UI display, or automatic known-cold compaction thresholds.
- **Prohibited states:** an occupancy reading inflated by cumulative
  same-turn API calls or by cross-turn summation, which measured a 4.9×
  overcount and 123–1280%-full readings on genuinely 19–48%-full nodes,
  cascading into wrongful compact-splits in a live org.
- **Allowed exceptions:** the CLI's self-reported `contextWindow` is used
  only as a last-resort fallback for window *size* (not occupancy), because
  it under-reports 1M-window models as 200k; orgtree's own pinned per-tier
  table is authoritative and is overridable via `ORGTREE_CONTEXT_WINDOWS`.
- **Observable enforcement:** the incident fix and its same-day pin.
- **Owning references:** `DECISIONS.md` D-040.
- **Status:** enforced.
- **Provenance:** ruling (spike-verified 2026-07-29; incident-fixed the same
  day) after the cumulative reading was measured causing wrongful
  compact-splits.
- **Amendments:** none.

---

## G · Turn envelope integrity

### INV-016 · a suppressed envelope block never costs an agent something it could have acted on

- **Statement:** MUST NOT suppress a per-turn `[ORG STATE]` / `[PROVIDER
  USAGE]` envelope block if doing so would leave the receiving agent unable
  to act on anything it could have acted on had the block been sent in
  full. MUST, whenever a block is suppressed, emit a self-describing marker
  naming what is unchanged, the snapshot it defers to, and the tool
  (`orgtree_chart`) that fetches a fresh copy — so an agent whose context
  lost that snapshot (compaction, restart, a lost turn) can recover without
  anything server-side knowing it happened. MUST re-send in full whenever
  the answer is uncertain: first turn, new session, a changed digest, a
  context that got *smaller*, 60k tokens of progression, 10 turns, 900
  seconds elapsed, or a backwards clock. A block MUST count as delivered
  only once delivery is confirmed (`_confirm_delivered`), never at render
  time — a turn that died before launch must not have suppressed, on
  replay, a block the agent never actually saw.
- **Scope:** `backend/orgtree/envelope.py` and its caller in
  `backend/orgtree/supervisor.py` (`_run_one_turn`'s `inflight` snapshot
  timing relative to envelope attachment and delivery confirmation).
- **Prohibited states:** a suppressed block with no marker at all (silent
  omission); a suppression decision that could be wrong in the
  agent-loses-information direction under any of the enumerated uncertain
  cases; a delivery record written at render time rather than confirmed
  delivery, which could let a pre-launch-death turn suppress on replay a
  block its agent never received.
- **Allowed exceptions:** none stated. The asymmetry is deliberate and
  named: "a wrongly-suppressed block is an agent acting on a roster it
  cannot see; a wrongly-sent one costs a few hundred characters. Those are
  not comparable" — every uncertain case resolves to sending in full.
- **Observable enforcement:** `backend/tests/test_envelope_budget.py`
  (20 checks; its own header states the property under test is not "fewer
  bytes" but "fewer bytes and the agent still knows everything it could
  have acted on"); `tools/envelope_cost.py --simulate --sweep` for the
  measured savings this invariant bounds against.
- **Owning references:** `DECISIONS.md` D-223;
  `backend/orgtree/envelope.py`; `backend/tests/test_envelope_budget.py`.
- **Status:** enforced.
- **Provenance:** ruling (turn-envelope-cost, 2026-09-02), verified directly
  with the implementing owner, who supplied the exact MUST/MUST NOT wording
  above and flagged that stating only the first property (no lost
  information) without the second (self-describing marker + recovery route)
  would let a real violation through — the server's belief that an agent
  still holds a snapshot can itself be wrong.
- **Amendments:** none.

---

## Known cross-cutting gap

`INV-002` (cache readiness) and `INV-005` (MCP tool surface) are recorded
`known_gap` on committed `main` with an uncommitted or unmerged fix in
flight; `INV-001` (task ownership) is `enforced` for its tested surface but
carries an unresolved atomicity question and a confirmed, actively-being-
closed gap (the keeper-kill path); the no-id-dedup case inside `INV-006`
is a named, accepted exception pending confirmation. None of these were
guessed — every one was verified directly, with file:line citations, by
the owning agent (`codex-stream-order`, `stopped-task-wake`,
`mcp-readiness`, `turn-envelope-cost`) and cross-checked independently by
`readiness-postreview` for the two readiness invariants. See
`breadcrumbs.md` in this agent's scratch folder for the full exchange.
Update the affected entries' `Status`, `Owning references`, and
`Amendments` the moment a fix lands or a pending audit resolves — an
invariant register that goes stale the day after it is written is worse
than none, because it is trusted by default.
