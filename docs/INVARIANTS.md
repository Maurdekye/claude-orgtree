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

- **Statement:** for every background task T a node's agent has caused to
  start, while T is classified OPEN, the system MUST NOT expose any
  observable state where BOTH (a) no process or turn remains able to
  observe T's outcome, AND (b) no durable, retry-backed recovery is
  scheduled. T's OPEN record MAY be cleared only in the same atomic durable
  write that either (i) records T's confirmed outcome, or (ii) establishes
  durable, drive-capable recovery mail when T's true outcome cannot be
  confirmed (teardown, restart, compaction, or lost owner). A superior's or
  the user's periodic checkup MUST NOT be the mechanism that discovers or
  corrects a both-inactive state; a checkup is defense-in-depth only, never
  the correctness path.
- **Scope:** every backgrounded task a node's agent starts (`Bash
  run_in_background:true` today; the mechanism is explicitly not
  shell-specific) and every turn/process a node owns.
- **Prohibited states:** any point where T is still classified OPEN, no
  process or turn can observe its outcome, and no durable recovery mail has
  been scheduled — regardless of *why* observability was lost (process
  death, an abnormal stop the CLI reports without the process dying, a
  keeper-initiated kill of a parked process for any reason, a backend
  restart, or a cheap-compaction/reconcile boundary finding a stale OPEN
  record with no fresher resolution).
- **Allowed exceptions:** purely informational status (`"completed"`, or
  any unrecognized/absent status, left alone rather than guessed at) stays
  passive by construction. Two scoping choices, stated by the implementing
  owner with reasoning rather than silently narrowing the invariant: (1)
  the exact content of a stdout event dropped while a process is parked is
  not preserved — the durable OPEN record is the source of truth, checked
  at every teardown/restart/next-turn point, so only the *fact* that
  something was outstanding needs to survive, not its payload; (2) there is
  no proactive staleness timeout for a task that finishes cleanly while
  parked and is never observed again by any teardown — the safety property
  requires only that recovery be atomic *whenever* the state is next
  observed, not a time bound on when that happens. Frozen-node recovery is
  a related, pre-existing, timer-only mechanism that `reconcile()`
  deliberately excludes; it is flagged to coordinator as a distinct area,
  not folded into this invariant.
- **Observable enforcement:** the tested subset — a task stopping without
  its process dying (D-225) and a process dying outright
  (`_bg_orphaned`/`_turn_abandoned`) — both produce durable mail
  (`kind="message"`, never `"notice"`) that wakes the owning agent exactly
  once: `backend/tests/test_bg_task_stopped_notification.py` D1 (line 158,
  fails against pre-fix code), D2 (line 206, no false alarm on normal
  completion), D3 (line 237, `kind="message"` and an actual second driven
  reply, not mere mail presence); `backend/tests/test_turn_lifecycle.py`
  lines 2963 and 3019 for the process-death path D-225 mirrors. The
  broader state machine below is landing now and does not yet have cited
  test names for its new terminal state.
- **Owning references:** `DECISIONS.md` D-225;
  `backend/tests/test_bg_task_stopped_notification.py`;
  `backend/tests/test_turn_lifecycle.py:2963,3019`;
  `backend/orgtree/warmpool.py` (`kill_node`, `_pump_out`, `attach`,
  `reconcile`); `backend/orgtree/supervisor.py` (`_bg_task_stopped`,
  `_bg_orphaned`).
- **Status:** implementation_in_flight for the full invariant as stated
  above. The D-225/`_bg_orphaned` subset (task-stops-without-death,
  process-dies) is enforced today, verified unchanged since commit
  `53bd4a1` (HEAD `a7934d8`) directly with `stopped-task-wake`, 2026-09-02.
  The generalization — a per-task state machine `UNOBSERVED` → `OPEN` →
  {`RESOLVED-CLEAN` | `RESOLVED-REPORTED` | `RESOLVED-UNKNOWN`}, all
  resolutions terminal and idempotent, with `RESOLVED-UNKNOWN` newly
  covering every loss of ownership not already handled by D-225 or
  `_bg_orphaned` (any
  `warmpool.kill_node` regardless of reason — identity-changed, retired,
  org-deleted, excluded-by-flag, cheap-compact-induced — and a stale OPEN
  record found by `reconcile()` at boot) — is **landing now, not yet
  committed or tested**. Do not mark it enforced until `stopped-task-wake`
  confirms it is committed with passing tests, and do not treat the
  previously-recorded keeper-kill gap as closed until then.
- **Provenance:** user report via coordinator, incident evidence from
  `fable-cli-migration` (D-225, 2026-09-01). Coordinator's 2026-09-02 mail
  additionally stated the invariant in its general form. The Statement
  above is `stopped-task-wake`'s canonical formalization of that same
  invariant (2026-09-02, sent as "please treat this paragraph as the
  canonical statement, separate from the implementation detail after it"),
  supplied after mapping the actual mechanism (`bg_live`/`task_notification`
  handling, drive/`send_message`, freeze-resume, restart reconcile,
  warm-pool keeper) — this is a tightened restatement of the same
  user-stated rule by its implementing owner, not a new user amendment, so
  it replaces the prior draft Statement in place rather than logging as an
  Amendment.
- **Amendments:** none — see Provenance for why the Statement was rewritten
  without one.

**Root-cause finding worth keeping regardless of the fix's fate (from
`stopped-task-wake`, 2026-09-01):** `warmpool.py`'s `_pump_out` (lines
350-391) discards all non-`init` stdout while a process is parked, and
`attach()` (lines 405-419) abandons anything unread on reuse. This is the
likely literal mechanism behind an observed post-turn
`<task-notification><status>stopped` event bypassing the reader entirely —
cited here because it explains *why* a durable-record-as-source-of-truth
design (rather than trying to preserve the dropped stdout event) is the
correct fix shape, not just a convenient one.

**Caveat, not yet resolved either way (do not read D-225 as closing this):**
`stopped-task-wake` reports D-225's mechanism is durable-mail-then-drive as
two *sequential* calls (write mail, then `send_message` to drive), and has
not yet confirmed there is no crash window between them, nor that the drive
call is retried/leased rather than fire-and-forget. Treat the D-225 subset
as **provisionally enforced, atomicity unverified** until that audit lands.
Backend-restart/crash replay, freeze/resume auto-wake, and cheap-compaction's
handling of an outstanding background task remain unconfirmed pending the
landing work above.

**Named gap, being actively closed (not yet closed as of this entry):**
`warmpool._keeper_pass` → `kill_node("identity-changed")`
(`warmpool.py` ~2092-2199) kills a **parked** process with zero check for a
live background task and no notification/mail/drive as a result. D-225's
own forensics name this exact gap (`DECISIONS.md:8048-8051`). The only
existing test touching this path, `backend/tests/test_warmpool.py:469`
("D5 · an idle identity change respawns immediately", body ~line 437),
asserts only exit-reason bookkeeping and says nothing about background-task
notification. This is exactly the case the new `RESOLVED-UNKNOWN` state
folds in; update this paragraph to `enforced` (and delete it as a separate
gap, since it becomes part of the Status line) once `stopped-task-wake`
confirms the landing work is committed and tested.

---

## B · Provider, cache, and session identity

### INV-002 · the cache card is keyed on whether a turn is running: yellow-or-nothing mid-turn, green-or-red idle

- **Statement (amended 2026-09-03; the governing rule is the user's own
  wording, ratified verbatim):**

  > "if a turn is running, it can only either show yellow or not show at all.
  > if no turn is running, it can only show either green or red. that is the
  > new invariant."

  The axis is **whether a turn is running**, not which verdict is available.
  It replaces the previous "exactly one of `ready` or `not_ready` in normal
  operation", which had no mid-turn half at all and which a running turn
  therefore violated on its face.

  **Mid-turn — yellow or nothing.** Yellow (`.cache-forecast.steer`, glyph
  `!`) is permitted for exactly one claim: `not_ready`/`prefix_changed`, the
  prefix has moved since the request in flight was sent. That claim is
  settled — whatever entry the running turn leaves belongs to the sent prefix
  — and it is a WARNING, not a guarantee, because a message that steers into
  the running turn pays nothing and only one that misses the steer window
  lands cold. Every other verdict depends on the entry the running turn is
  still writing, which is unobserved until its receipt lands, so it renders
  **no card at all** — never a placeholder in the slot.

  **Idle — green or red.** Green and red are GUARANTEES about the next
  message: it will hit, it will miss. Yellow MUST NOT appear while no turn is
  running; there is no steer window to miss, so the conditional it asserts
  cannot arise.

  Two carve-outs predate this amendment and are **retained**, not revoked —
  both are the same user's earlier rulings and neither is a colour on the
  green/red axis: grey (`diagnostic`) for an enumerated fault, and **no card**
  for readiness `none`, where there is nothing to make a claim about
  (`no_completed_fingerprint`, and mid-turn `turn_in_flight`). A flag is a
  claim about something assumed to exist (user, 2026-09-03), so the absence of
  a claim is the absence of a flag.

  Grey (`diagnostic`) MUST NOT be used as a third opinion
  about the cache; it is reserved for an enumerated, named fault that
  prevented an opinion from forming at all, and every grey MUST carry a
  machine-readable cause plus a human-actionable detail sentence. An
  absent, unrecognized, or unparseable readiness payload MUST resolve to
  the named `internal_error` diagnostic — never to green (**the badge fails
  closed**), and never to a silent/generic unknown. A live countdown MUST
  appear only while readiness is `ready` **and** an authoritative
  `expires_at` derived from a positive receipt exists (readiness alone is
  not sufficient); once elapsed, or once readiness is anything but `ready`,
  the badge MUST fall back to the readiness verdict. Two clarifying rules
  added after the first implementation pass, both normative: **a row that
  cannot establish readiness is red; grey requires an actual fault to have
  occurred** — "this row predates a schema migration" is not a fault and
  must not render grey. And **a known incompatibility outranks a capability
  gap**: if a prefix change AND an unsupported-lane capability gap are both
  true at once (e.g. a seat moved to a lane that cannot report), the known
  incompatibility (red, informative) wins over the capability diagnostic
  (grey) — grey is only for where *no* opinion can be formed at all.
- **Scope:** the per-node cache forecast surfaced to agents (`cache_forecast`
  API/WebSocket field) and rendered on the desk badge.
- **Prohibited states:** **mid-turn red or mid-turn green** — a guarantee about
  the next message while the turn that decides it is still running; **idle
  yellow** — a steer-window warning with no steer window to miss, including
  any stale-`steer` residue surviving the turn→idle boundary; a mid-turn card
  for any cause other than `prefix_changed`; a placeholder occupying the slot
  where no card should render; green with no affirmative evidence of
  compatibility (the prior D-214 `no_completed_fingerprint` → green reading is
  overridden by this invariant, not merely superseded); a live countdown
  while readiness is not `ready`, or with no authoritative `expires_at`; a
  grey badge with no cause or no detail sentence; an unclassified cause
  defaulting to anything other than the named `internal_error`; a generic
  catch-all coercion of an unrecognized state or lane; a capability-gap
  grey masking a known incompatibility that should have rendered red.
- **Allowed exceptions:** none stated — the cause table is exhaustive by
  construction, and the owning test suite asserts the invariant's
  *properties* (exhaustiveness, fail-closed, no catch-all, incompatibility-
  outranks-capability-gap) rather than the current contents of the cause
  table, specifically so table and invariant cannot silently diverge.
- **Observable enforcement (updated 2026-09-02, committed as `aec84e5`,
  confirmed directly by `turn-envelope-cost`, the implementing owner):**
  the four violations this survey originally found standing on `main`
  (`a7934d8`) — D-214's green-on-no-evidence, ungrounded grey causes, a
  silent generic-fallthrough coercion, and Antigravity/Codex-API-key lanes
  reaching a vague `uncertain` instead of an explicit capability diagnostic
  — are fixed at `aec84e5`. One further cause was added during that work,
  `legacy_forecast_unmigrated`, specifically because a naive read of this
  invariant would have gotten it wrong: every forecast persisted before
  this change lacks the readiness triple, and classifying that as
  `internal_error` would have been literally invariant-compliant
  (enumerated, explained, logged) and still wrong — it would label a schema
  migration as a classifier defect and strand every idle node on grey.
  Instead a pre-migration row re-derives its verdict from its persisted
  `state`/`source`, and genuinely ambiguous residue renders red, never
  green and never a guessed grey — the "a row that cannot establish
  readiness is red" clarification above. Countdown-expiry-renders-red
  (`desk.tsx:419-431`; `heal_quantized_skew`) remains correct and enforced,
  unchanged.
- **Owning references:** `backend/orgtree/cachecontinuity.py` (`READINESS`,
  `READINESS_DETAIL`, `EVIDENCE_REQUIRED`, `SUPPORTED_LANES`,
  `readiness_fields`, `capability_evidence`); `backend/orgtree/supervisor.py`
  (`_readiness_incident_log`, `cache_forecast_public`);
  `frontend/src/types.ts` (`Readiness`); `frontend/src/canvas/desk.tsx`
  (`readinessOf`, `readinessCause`, `cacheExpiryAt`);
  `backend/tests/test_cache_readiness.py` (19 checks);
  `frontend/tests/cacheforecast.test.tsx` +
  `frontend/tests/cachecountdown.test.tsx` (16 checks);
  `public_projection_cannot_fail_open` (backend suite, fail-closed pin).
  Full suite at `aec84e5`: 117/117 suites run, 107 passed, 10 failed — a
  strict subset of the 11 pre-existing failures at `a7934d8`, zero new
  failures introduced. `DECISIONS.md` D-226 states explicitly that it
  **implements** this invariant and overrides the conflicting D-214
  decision — it does not create the invariant, and this entry is the
  authority, not the decision. Provenance note: D-226's original text
  landed inside commit `b07a354` (this register's own first commit, which
  picked up the then-shared, then-uncommitted `DECISIONS.md` working tree
  wholesale — confirmed intact, all 108 lines, by the D-226 author); only
  the `legacy_forecast_unmigrated` amending paragraph is in `aec84e5`
  itself. `DECISIONS.md` is edited concurrently by several agents — a bulk
  `git add DECISIONS.md` sweeps up whatever anyone else has in flight; this
  register's own commits stage that file hunk-by-hunk for exactly that
  reason.
- **Status:** enforced.
- **Provenance:** user ruling, 2026-09-02: green requires affirmative
  evidence of compatibility, and the absence of all evidence is not that;
  the prior D-214 green-on-no-evidence reading is explicitly overruled.
  See also `docs/cache-continuity.md` for the underlying forecast-state
  model this readiness layer sits on top of, and
  [INV-003](#inv-003--a-local-restart-is-not-proof-of-a-cache-miss-and-provider-switching-is-a-known-break)
  for the base cache-namespace rule.
- **Amendments:** 2026-09-02 — status raised from known_gap/
  implementation_in_flight to enforced on confirmation of commit `aec84e5`
  and a full-suite run introducing zero new failures; Statement gained the
  two clarifying rules (red-not-grey-for-unestablished; incompatibility-
  outranks-capability-gap) the implementation pass surfaced.

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
- **Allowed exceptions:** Antigravity (`provider == "google"`) is explicitly
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

### INV-006 · no assistant-visible output reaches a viewer before its turn's user message is durable, and nothing is announced twice

- **Statement:** no assistant-visible output for a turn may reach a viewer
  before that turn's own user message is durable in the transcript. A
  message counts as delivered only when the provider has **accepted** it;
  delivery is made durable **before** it is announced, and it is **never
  announced twice**. This binds every provider lane, not only the lane
  implemented first — codex-stream-order's own framing, chosen specifically
  so the invariant does not have to be rewritten each time a lane is added.
  For mid-turn mail specifically: the durable steered row and its
  delivery-token confirmation commit atomically, *then* the `steered`
  WebSocket frame, *then* any provider journal/live output the delivery
  caused — provider acceptance is the linearization point, not the
  supervisor's fetch of the mail. If the provider refuses (no durable
  steer/frame/confirmation resulted), the raw carrier — tokens included —
  requeues and is delivered exactly once, on the next turn.
- **Scope:** the ordering/journal barrier for every provider lane's
  turn/mail pipeline (currently implemented for Codex; the statement is
  written provider-agnostic on purpose). `backend/orgtree/codexrun.py`
  (`CodexTurn.start(on_thread=…)`), `backend/orgtree/supervisor.py` (the
  ordering barrier, `_visible`/`_visible_stream`/`_visible_live_row`,
  `_open_journal`, `_first_time`, `commit_steer`/`pop_steer`), and its
  render into `supervisor.stream` / the desk frontend.
- **Prohibited states:** an agent's answer rendering above the question it
  is answering while that question still reads "delivering…"; a durable
  transcript with no user row for a turn whose assistant output has already
  rendered; a duplicated completion producing two live rows for one durable
  record; mid-turn mail announced with no corresponding durable steered row
  (silently un-witnessable on the codex/antigravity legs, historically); a
  refused steer whose carrier was already treated as delivered, causing the
  same words to appear twice in the transcript; the ordering barrier's own
  release running out of order relative to older held closures still
  in-flight.
- **Allowed exceptions:** if the journal never opens at all (the thread id
  never arrived because `turn.start()` raised), held output is never
  released — there is no transcript for that turn, so releasing assistant
  prose would show it under a turn the server cannot account for; the
  turn's own durable error row is what renders instead. An item with no id
  is explicitly NOT deduplicated (`_first_time()`) — a missing identity is
  not evidence of a repeat, and the ruling behind this whole family is
  stated verbatim because it generalizes: **"a duplicate is a blemish, a
  gap is a lie."** This is a deliberate, accepted risk, not a tracked gap.
- **Observable enforcement / current state (updated 2026-09-02 directly
  from `codex-stream-order`, the owner — supersedes the D-221-only reading
  below):** D-221 covers only the *start* of a turn and is, as of this
  survey, one of **four** mechanisms, three of which were live gaps
  reproduced on the org's own running `coordinator` node and are landing in
  an imminent commit at the time of writing:
  1. Mid-turn steer on the codex/antigravity legs called `pop_steer` in-process
     and never emitted the `steered` WebSocket frame `api.node_steer`
     emits for Claude — measured zero `steered` frames across a 15-minute
     capture around a committed steered row.
  2. `pop_steer` committed delivery (durable row + `_confirm_delivered`)
     *before* asking the app-server to accept the text; a refusal (turn
     ended inside the 2 s poll, or Antigravity, which refuses every steer) left
     the carrier requeued while a delivery was already claimed — measured:
     the same 3,512-character message appearing in the transcript twice.
  3. `_open_journal`'s barrier could release out of order: it copied held
     closures and dropped `jlock` before emitting, so a reader thread could
     see a newer item's `sid` and emit it while older held closures were
     still mid-release.
  4. Already on `main` as of `aec84e5`: a render-layer issue where the desk
     drew pending mail at the very bottom, below the live tail, so a
     message the running turn was started to answer sat visually under
     that turn's own answer regardless of backend timing.
  Fix for items 1-3: atomic durable-steer-then-frame-then-effect ordering
  per the Statement above; turn teardown joins in-flight carrier ownership
  before fold-back/idle; the turn-start barrier flushes held output
  atomically; the frontend only hoists output whose delivery is
  `delivering` *and* `via=turn` above current output — merely queued or
  `via=steer` output stays below. **Committed as `11f3f72`** ("Codex lane:
  linearize steered mail before its answer"), same day.
- **Owning references:** `DECISIONS.md` D-221 (start-of-turn barrier) and
  D-227 (mid-turn/steer linearization, `11f3f72`);
  `backend/tests/test_codex_stream_order.py` (24 checks — §4 covers
  **resume**: two consecutive turns, asserting the second/resumed thread
  also emits nothing before its own user row is durable; §6 covers
  **replay** as a *dedupe* guarantee for a replayed `item/completed`, not
  an ordering guarantee — orgtree does not reconnect an app-server
  mid-turn, a lost process ends the turn, so state dedupe here, not
  reconnect ordering); `backend/tests/test_steer_delivery.py` (403 lines,
  new in `11f3f72`); `frontend/tests/turnpend.test.tsx` (223 lines, new in
  `11f3f72`).
- **Status:** enforced. The D-227 extension landed the same day it was
  drafted; confirm with `codex-stream-order` if a full-suite run beyond
  the new test files' own pass is needed before treating this as final.
- **Provenance:** ruling (codex-stream-order, 2026-09-02): "no assistant
  output for a turn may become VISIBLE before that turn's user message is
  DURABLE in the transcript," generalized the same day, after further
  investigation prompted by a user follow-up ("i still observe timing
  issues"), to the provider-agnostic form quoted in the Statement. Original
  root cause: the desk drew the durable block first, the live tail under
  it, and the user's own undelivered message at the very bottom, so a fast
  Codex response could render above a question still shown as undelivered.
- **Amendments:** 2026-09-02 — Statement widened from a Codex-specific,
  start-of-turn-only reading to the general, provider-agnostic,
  mid-turn-inclusive form above, after the owner found and reproduced three
  further live gaps the narrower wording did not cover, then landed the fix
  as D-227/`11f3f72` the same day. Not a user amendment in the strict sense
  (no new user statement); logged as an Amendment anyway because the change
  is substantive, not a wording tightening — a reader who only saw the old
  Statement would under-claim what today's user report ("i still observe
  timing issues") actually requires.

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

### INV-017 · an accepted mid-turn message is owned at every instant, never parked in RAM on an idle node

- **Statement:** once a user (or agent) message is accepted while its
  recipient is mid-turn, it MUST at every observable instant be in exactly
  one of: (a) injected into the running turn (a durable `steered_log` row);
  (b) drained into a turn's own text (`delivering` via `turn`) with that turn
  running or lease-owned; (c) an in-memory carrier (`st["steer"]`,
  `st["queue"]`) on a node that is busy, waiting, responding or under process
  control; or (d) the durable mailbox / delivery journal with a driver
  scheduled (a queued carrier, an admission lease, or reconcile at startup).
  It MUST NOT sit in an in-memory carrier while the node is idle — the
  `stranded` stage — because nothing is then scheduled to move it and the
  user must resend to be heard. Tightens [INV-007](#inv-007--mail-is-at-least-once-it-may-stall-it-must-never-disappear)
  from "never lost" to "never ownerless".
- **Scope:** every site that flips `responding` off — `_codex_leg` and
  `_antigravity_leg` at their exit, the claude lane at its result boundary, its
  phantom-drop and stdin-closed recoveries and its turn exit — all through
  `_fold_steer`; the lane-agnostic `_run_one_turn` finally (the belt);
  `send_message`'s steer door; `_delivery_stages` (the receipt).
- **Prohibited states:** a non-empty `st["steer"]` with `busy=False`; a
  `delivering` batch whose token is in neither in-memory carrier while the
  node is not busy/waiting/responding/proc-controlled/lease-owned, once the
  batch is older than `STRANDED_GRACE_S` since its DRAIN — the grace shields
  only a batch drained moments ago (the two-phase steer gap), never an old
  one that loses its owner (reported as `stage: "stranded"`, counted in
  `mail_stranded`); a `responding=False` site that does not fold the steer
  store in its own lock take (with `busy` still True a later message then
  takes the queue door ahead of the earlier steer, and the exit fold inverts
  them — review round 2 found two such sites on the claude lane); a fold
  that runs after a teardown call that can raise; a fold that puts a later
  message ahead of an earlier one (leftovers go to the BACK of the queue,
  which keeps the order precisely because every site folds in its own take);
  a queued carrier on an unowned node reported as `queued` rather than
  `stranded`.
- **Allowed exceptions:** `interrupt_all` (the killswitch) clears the steer
  store and queue on purpose; the drained mail stays in the org document and
  is re-presented when the user drives the agent again — a documented user
  ruling, not an oversight. A backend restart loses the in-memory carrier
  (INV-010 accepts that) and `reconcile()` folds the journal back and
  re-drives — accepted as at-least-once, tested.
- **Observable enforcement:** `backend/tests/test_midturn_mail_ingress.py`
  (57 checks): §1 makes the miss certain (fake app-server `stall` scenario,
  `CODEX_STEER_POLL` longer than the stall) and requires an empty steer store,
  one owned carrier, a `turn exit` fold receipt, and exactly-once delivery by
  the next turn; §2 two messages in the window, in order; §3 a message at the
  finalization seam; §5 plants the pre-fix state and requires the receipt to
  say `stranded` — for a steer-store carrier and for a queued one on an idle
  node; §6 a planted strand survives a simulated restart via `reconcile()`;
  §7 a lane that forgets to fold behind a pump requeue (the belt alone, A
  before B); §8 `_fold_steer`'s order plus a structural guard, comments
  stripped first, that every `responding = False` site in `supervisor.py`
  calls it inside its ENCLOSING `with _state_lock:` block (enclosure
  verified, and the guard self-tested against a planted outside site —
  review round 3); §9 a teardown that raises after the lane's fold. `backend/tests/_mutate_midturn.py`: M2 (both folds
  off — the original defect) kills §1; M1 (lane fold off) changes only the
  receipt's `where`; M3 (belt off) and M9 (belt to the front) die on §7; M12
  (helper to the front), M14/M15 (a recovery site that no longer folds) die
  on §8; M16 (queued on an idle node) dies on §5.
- **Owning references:** `DECISIONS.md` D-229;
  `backend/orgtree/supervisor.py` (`_codex_leg` / `_antigravity_leg` finally,
  `_run_one_turn` finally, `_delivery_stages`, `delivering_mail`,
  `_steer_fold_log`); `backend/orgtree/api.py` (`node_chat` `stage` /
  `mail_stranded`); `backend/tests/test_midturn_mail_ingress.py`.
- **Status:** enforced (on the `fix/midturn-mail-ingress` branch that carries
  D-229; verified by its owning suite and mutation harness at commit time).
- **Provenance:** user report, 2026-09-02 09:55:01Z, to the coordinator: "if
  i send a message mid-turn with the wrong timing it just never gets
  delivered, and i have to send another message to actually get you to
  receive it … it seems to be a codex-exclusive issue now"; user direction
  09:57Z to fix, relayed by the coordinator. Root-caused by `midturn-mail`
  from the org document and the codex journal (22.6 s strand, D-229).
- **Amendments:** 2026-09-02 (adversarial review round 2, same branch): the
  claude lane's phantom-drop and stdin-closed recovery sites flipped
  `responding` off without folding, so the round-1 fold-to-the-back could
  put a later message ahead of an earlier steer there; all six sites now
  share `_fold_steer`, pinned structurally (§8); a queued carrier on an
  unowned node is reported `stranded`.

### INV-018 · the machine envelope is never rendered as the user's words, not even for one poll

- **Statement:** a provider user event MUST reach the reader only through
  its durable projection (the prompt-view sidecar). While a fresh event's
  projection is not yet readable, `read_chat` MUST reload the sidecar once
  and, failing that, WITHHOLD the event for that poll — but ONLY while the
  message's pending bubble still covers it (its delivery batch unconfirmed),
  so that the message is on screen exactly once throughout; an uncovered
  unprojected event MUST render (raw, loudly) rather than be hidden, and a
  reader whose payload carries no pending bubble at all
  (`orgtree_read_transcript`, `hold_back=False`) MUST NOT hold anything. The
  `[ORG STATE]`, `[PROVIDER USAGE]` and other per-turn machine blocks MUST
  NOT appear as a user bubble in any frame the desk paints while the
  durable provenance exists or is still arriving. The browser MUST NOT be
  the layer that hides them (it strips no markers, so a human who types them
  literally keeps every byte).
- **Scope:** `backend/orgtree/supervisor.py` `read_chat`'s user-row
  projection (`_take_prompt_view`, `_reload_prompt_views`,
  `_prompt_is_fresh`, `PROMPT_VIEW_GRACE_S`, `_carries_envelope` — the
  `[ORG STATE]`, `[PROVIDER USAGE]` and `[ORG NOTICES]` headers —
  `_covered_by_pending` and the `mail_marker_in` it shares with
  `node_chat._in_transcript`); every writer of a provider
  user event, which MUST write the sidecar row first (`_open_journal`, the
  two `_record_prompt_view` → `stdin.write` pairs) and every copier of a
  transcript, which MUST copy its sidecar with it (`_copy_prompt_views` at
  both compaction splits); the desk's rendering of `messages[]` and
  `pending_mail[]`.
- **Prohibited states:** a `messages[]` user row carrying the machine blocks
  for an event younger than the grace; a frame in which the user's message is
  on screen zero or two times during the pending→projected handover; a
  marker-based scrubber in the frontend.
- **Allowed exceptions:** an event whose sidecar row never arrives renders
  raw — past `PROMPT_VIEW_GRACE_S` (8 s), or at once when its delivery batch
  is already confirmed (the bubble is gone, so hiding it would show the
  message zero times) — because the sidecar write is fail-open by design and
  a message that never appears would be a gap, the worse lie (D-50); both
  cases are logged. Events that never carry the machine blocks (slash-
  command echoes, prompts typed into a remote-controlled CLI, old-CLI
  command output) have no sidecar row by construction and are never held.
  The `[MAIL …]` block stays in the projection: it is the structured
  envelope the desk parses into a mail card, not chrome.
- **Observable enforcement:** `backend/tests/test_prompt_view_race.py` (14
  cases: the TOCTOU with row+view appended from inside the sidecar loader;
  a torn sidecar row; hold-back with the pending bubble covering the message
  exactly once and a one-payload handover; an uncovered event rendered raw
  at once; the envelope gate pinned with a covered record only it can let
  through; the cover marker against every `_mail_block` shape (reply,
  notice, attachment) and the desk's handover for a reply; a no-bubble
  reader given the raw event; the grace edge both sides; no re-spend of a
  consumed row; the sidecar copied with a compacted transcript; the pre-fix
  order shown to produce the raw render);
  `backend/tests/test_codex_dispatch.py` §6 (copied history stays visible
  across a Codex fork+compact); `frontend/tests/envelopeflash.test.tsx` (a MutationObserver judges
  every DOM commit of the handover for one copy and zero chrome, and its §3
  requires the instrument to report the old server's raw row);
  `frontend/tests/orgstate.test.tsx` (no frontend scrubber).
  `backend/tests/_mutate_midturn.py` M6 (no reload), M7 (no grace), M8
  (re-spend), M10 (coverage ignored), M11 (the gate says yes to everything)
  and M13 (the marker matches everything) kill the race suite.
- **Owning references:** `DECISIONS.md` D-229 (mechanism), D-192 (the
  display ruling this refines); `backend/tests/test_prompt_view_race.py`;
  `frontend/tests/envelopeflash.test.tsx`.
- **Status:** enforced (on the `fix/midturn-mail-ingress` branch that carries
  D-229; verified by its owning suites and mutation harness at commit time).
- **Provenance:** user report, 2026-09-02 09:55:35Z, to the coordinator:
  "i saw the turn envelope associated information for a second there before
  it reverted to a normal user turn message; thats no doubt another bug";
  invariant wording supplied by the coordinator on the user's behalf
  (09:55:51Z): internal turn-envelope/control/receipt state must never be
  rendered as a transient user-visible message. Root-caused by
  `midturn-mail` to `read_chat`'s sidecar-then-transcript read order (D-229).
- **Amendments:** 2026-09-02 (adversarial review round 2, same branch): the
  cover marker missed reply and notice mail (`mail_marker_in`, now shared
  with the desk's handover); `[ORG NOTICES]` joins the envelope gate; a
  reader with no pending bubble (`orgtree_read_transcript`) never holds.

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
