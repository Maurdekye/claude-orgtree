# Antigravity lane: warming and mid-turn steering — design and probes

Status (2026-09-03): DESIGN, not built. Both items were graded practical in
the gap audit that followed D-231/D-233 (scratch `antigravity-scope/
breadcrumbs.md`, "gap audit"). They wait on the Google account's quota,
which walled itself on 2026-09-03 02:36 local and resets ~2026-09-09 late
evening; the probes below are written so that the moment it returns the
work is executing, not deciding. What is MEASURED is marked so; everything
else is stated as the assumption the probe exists to test.

The two non-goals are decided: cache readiness stays `unsupported_capability`
(the receipt is unreliable — an identical 13.7K prefix three times inside
20 s reported `cache_read_tokens: 0` — and Google publishes no implicit-cache
TTL), and the `/quota` structured payload is a probe for after the reset, not
a guess (D-233).

## 1. Warming: a parked stdin stream-json process

### What is measured (agy 1.1.24, probes `warm_probe.py`, `idle_probe.py`)

- The leg's own wire — `agy -p= --input-format stream-json --output-format
  stream-json --add-dir <cwd> …` with the prompt as one `user` line on stdin
  (`antigravityrun.AntigravityTurn`) — does NOT end at the first `result`
  while stdin stays open: the process was alive after the result and after a
  further 15 s idle (w1), and after 15 s idle with no input at all (idleA/B,
  ~155–160 MB RSS each).
- A second `user` line on that same stdin ran a second turn in the SAME
  conversation (it remembered turn 1; `num_turns` counted 2), with NO second
  `init` event — `init` is per process, and the result's `usage` is per
  turn, not cumulative, so per-turn billing (D-183's fold) is unchanged.
- The changelog for `--input-format stream-json` says it outright: "runs one
  turn per message in a single conversation, so a driver can keep a session
  open".
- A line written MID-turn (during a slow tool step) was QUEUED as the next
  turn — it is a queue door, not a steer door (that is §2's job).
- `--print-timeout` is not a per-turn budget on a parked process: with
  `--print-timeout 20s` the process EXITED during a 30 s idle gap after a
  result (w2; a following stdin write failed EINVAL). A parked process must
  therefore carry NO `--print-timeout`; the supervisor's own `TURN_TIMEOUT`
  wait + kill-tree is the turn ceiling, exactly as on the claude lane.
- Closing stdin ends the process cleanly (rc 0 after an idle-only life).

### What it buys, honestly

Not tokens. The claude lane's pool (D-201) was justified by ~200k tokens
re-sent per cold resume; this lane's provider cache is unobservable, so the
warm process buys (a) the CLI's startup — auth refresh, model-registry fetch,
workspace scan, the orgtree MCP plugin's stdio spawn and handshake — measured
at ~2 s to `init` on a quiet machine, (b) the conversation held hot in one
process instead of being re-loaded from the CLI's store per turn, and (c) one
less process spawn per turn on a machine that already contends on spawns.
Cost: ~160 MB RSS per parked agent, the same order as the other lanes' node
processes. Worth doing; not a cache story, and the docs must not call it one.

### Design (the codex pattern of D-201, transposed)

`warmpool.py` gains `AntigravityWarmProc`, the third generation kind:

- **Spawn** (`_spawn_for`, at boot/hire/respawn): `antigravityrun.
  write_workspace(...)` first — AGENTS.md identity, the orgtree plugin, the
  rights hook — then the leg's exact argv MINUS `--print-timeout`, WITH
  `--conversation <antigravity_conversation>` when the node has one (the
  pool's prewarm resumes the same conversation the cold path would). The
  keeper reads the `init` event at prewarm and banks it: `conversation_id`
  (a fresh hire's is minted here, harvested by the first turn exactly as the
  cold harvest does), `tools`, `model`. A wrong served model at prewarm is a
  `prewarm-failed` kill, the same gate the leg applies cold.
- **Identity hash** (`identity_snapshot`): the rendered identity, the
  workspace files the writer produces (plugin `mcp_config.json`, hooks.json,
  the rights wrapper), the spawn argv (model, effort, add-dir), the account
  namespace (`_cache_antigravity_account_namespace`) — AND the node's
  `antigravity_conversation`, because a conversation that advanced outside
  this process (a cold turn while the pool was off, a rehire re-mint) makes
  the parked conversation stale; a changed id respawns. Per the coordinator's
  rule, invalidation is the hash, never an event list.
- **Claim / park / discard** (leg side, `_antigravity_leg`): the codex leg's
  block verbatim — `warm_decision`, `eligible`, `identity_snapshot`,
  `claim_snapshot`, a `provider-lane` discard for a foreign kind,
  `journal_admit` warm/cold — then `AntigravityTurn` is constructed IN
  SESSION MODE: bound to the parked process's stdin/stdout pumps, it writes
  the `user` line and waits for the next `result` event instead of process
  exit (a new `wait_result()` beside today's `wait()`; the event fold is
  shared). After the turn: `boundary_check` (hash unchanged, status
  completed) → `park_back`, else `discard` with the same reasons codex uses
  (`limit-frozen`, `turn-timeout`, `stdin-closed`). An interrupt is still
  kill-tree (D-190): the pool sees the exit and respawns.
- **Eligibility**: `eligible()` drops its Antigravity exclusion; the D-203
  settings toggle and `warm.flag` per-agent arms apply unchanged.
- **Runner** (`antigravityrun.py`): `AntigravityTurn` learns to attach to an
  existing process (owner-token semantics unchanged for `_mcp_tool_count_*`),
  `wait_result()` returns the same normalized dict, `close()` on a parked
  claimant DETACHES (never kills). `write_workspace` stays per spawn; §R1/R2
  below decide whether the parked process also re-reads those files per turn
  (it does not matter for correctness — a changed file changes the hash and
  respawns — only for what a no-respawn turn may rely on).
- **Fake**: `fakeantigravity.py` gains a persistent mode (loop over stdin
  lines, one `init`, one `result` per line, `num_turns` counting) so
  `test_antigravity_dispatch` can prove claim → turn → park → second turn on
  the SAME pid, the hash respawn, and the cold fallback.

Estimate: ~1 day of work after the probe below, most of it the runner's
session mode and the fake.

### Probe to run: `probes/warm_reread_probe.py [--idle 1800]`

One parked process answers R1–R3, two more answer R4–R5 (~5 flash turns):

- R1 AGENTS.md rewritten between turn 1 and 2 (ALPHA→BRAVO): re-read per
  turn or process-scoped? → decides the hash's "startup files" component
  and whether an identity edit needs the respawn to apply.
- R2 `.agents/hooks.json` written between turns (a PreToolUse deny): applied
  in-process? → same question for the rights hook.
- R3 idle for 30 min with stdin open and NO `--print-timeout`, RSS sampled
  each minute, then a turn: survives? grows? → the parked lifetime the
  pool's "no idle reaping" rule assumes.
- R4 `--print-timeout 60s`, 90 s idle after a result: exits? → re-confirms
  w2 with one variable (idleB survived 15 s under an 8 s timeout BEFORE any
  prompt, so the timeout's clock is unclear; R4 settles what a parked
  process may carry).
- R5 a fresh process spawned with `--conversation <R1's id>`: init echoes
  the id and turn 1 is remembered → the prewarm resume the pool performs.

## 2. Mid-turn steering: the PreInvocation hook

### What is documented (builtin `docs/hooks.md`, agy 1.1.24) and what is not

- `hooks.json` (the leg already writes `<cwd>/.agents/hooks.json` with a
  NAMED `orgtree-rights` PreToolUse entry) may carry a `PreInvocation`
  handler list — flat, no matcher — that runs "before the model is
  invoked": stdin gets `{invocationNum, initialNumSteps, conversationId,
  workspacePaths, transcriptPath, artifactDirectoryPath, modelName}`, stdout
  may answer `{"injectSteps": [{"userMessage": "…"}]}` (persistent, like a
  user turn) or `{"ephemeralMessage": "…"}` (transient system message) or a
  `toolCall`. Handlers are `type: "command"` only, run synchronously via
  `cmd /c`, cwd = the hooks.json directory, default timeout 30 s.
- That is the claude lane's steering door (`steer.py`: PostToolUse →
  `additionalContext`) in another costume, firing once per model call, i.e.
  after every tool round — the same cadence, the same "a turn with no tool
  round cannot be steered" limit, the same delivery-on-acceptance shape.
- UNMEASURED: whether PreInvocation fires in `-p` print mode; whether an
  injected `userMessage` appears on the stream-json wire (the journal must
  record what the agent was told); what env the hook process inherits.
- MEASURED already: the stdin lane is NOT a steer door (a mid-turn line
  queues as the next turn), which is why today's leg pump refuses and folds
  to the queue (D-229 semantics preserved).

### Design

- `antigravityrun.write_workspace` writes a second named hook,
  `orgtree-steer`: `PreInvocation → python <backend>/orgtree/steer.py <org>
  <node> --agy`. `steer.py` keeps its identity logic (argv first, cwd second,
  `.port`/`.bridge` for the backend) and gains an output mode: for `--agy`
  it prints `{"injectSteps": [{"userMessage": "[ORGTREE MAIL — delivered
  mid-task]\n…\n[END ORGTREE MAIL …]"}]}` when the `/steer` door returned
  messages, `{}` otherwise, with the same 2 s HTTP budget. `userMessage`,
  not `ephemeralMessage`: mail must survive in the conversation the way a
  hook-injected context survives in Claude's transcript.
- The leg drops its refusing steer pump and takes the claude lane's shape:
  `responding=True` routes mail to the steer store, the hook drains it
  through the existing `/api/orgs/{org}/nodes/{node}/steer` door (which
  commits delivery — the hook's stdout IS the injection), and the turn-end
  fold under `_state_lock` (D-229) returns anything the hook never fetched
  to the queue. Nothing new in the supervisor's delivery accounting: the
  door and the fold already exist for the claude lane.
- The journal records the injected text as a user row when the wire shows
  it (S3); if the wire is silent about injections, the `/steer` door's
  commit already writes the durable "delivered mid-task" row the claude
  lane relies on, so the transcript stays honest either way.
- Rights + steer live in ONE hooks.json (named entries merge; the docs say
  handlers for one event run sequentially). The writer regenerates the file
  per spawn as it does today — no per-turn cost.
- Fake: `fakeantigravity.py` runs the PreInvocation command before each
  model step (it already runs the PreToolUse one), injects the returned
  `userMessage` as a `user_input` step and echoes it in its answer, so
  `test_antigravity_dispatch` §7 flips from "falls back to the queue" to
  "delivered mid-turn, committed once, never duplicated" — the exact
  duplicate the codex lane fixed on 2026-09-02.

Estimate: ~half a day after the probe below.

### Probe to run: `probes/preinvocation_probe.py`

Three print-mode runs against a temp workspace whose hooks.json carries a
`PreInvocation` handler that logs every call and, on `invocationNum == 2`,
injects a secret word (~3 flash turns plus two resume questions):

- S1 does it fire headless, and how many times for a one-tool-call prompt?
- S2 does the injected `userMessage` reach the model mid-turn (the answer
  must repeat KUMQUAT)?
- S3 what does the injection look like on the stream-json wire?
- S4 does the hook see the parent's `ORGTREE_*` env, or a sanitized one?
- S5 `userMessage` vs `ephemeralMessage`: resume the conversation and ask —
  the first should be remembered, the second not.

A negative S1 ends the design here (the stdin lane stays a queue door and
mail keeps its boundary semantics); a negative S2 with a positive S1 means
trying `ephemeralMessage` and `toolCall` before giving up.
