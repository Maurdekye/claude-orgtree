# Architecture traps — how this actually works

The places where the obvious reading of the source is **wrong**, and the
implicit contracts a refactor could silently break. Companion to
[DECISIONS.md](../DECISIONS.md) (the normative register): if a rule would
survive a refactor it is a decision and goes there; if it would evaporate the
moment the code was restructured, it lives here. Read this before touching
ledger, supervisor, the gateways, or the canvas.

## Ledger & credits

- **`children(parent)` returns LIVE children only** (`live_only=True`
  default) — and "live" here is *budget* semantics: it excludes only
  `archived`. An `unrecoverable` node still counts and **still holds its
  seat** (deliberate — a broken session keeps its budget). Consequence:
  **live-for-budget is not live-for-delivery.** Any non-budget consumer —
  recipient lists, drive lists, notification fan-outs — must filter
  `state == "live"` itself, the way `extern_holders()` and (since the
  2026-08-01 fix) `extern_recipients()` do. This has already produced one
  live defect: external mail delivered into an unresumable node while
  suppressing the user-inbox rescue.
- **A node's own seat is not charged against its own grant** ("give my CEO
  50" = 50 to allocate). `free = grant − Σ children's holdings`, where a
  child's *holding* is `seat_cost + grant`. The obvious
  `grant − seat − committed` double-counts the seat.
- **`_chain_acquire` mutates intermediate nodes' grants** as a side effect —
  grants are not stable under user actions on descendants. `audit()` is the
  invariant checker.
- **Ledger ops mutate in memory; nothing persists until `store.save_org`**,
  and `load_org` always re-reads from disk (no instance cache).
  `_kiosk_cap_check` *relies* on this: it runs after the op has already
  mutated the object and enforces the cap purely by making `save_org`
  conditional. A ledger method that saves internally, or an op path that
  saves before the check, silently breaks the cap.
- **Idempotent no-ops return SUCCESS with a warning, never raise:**
  retire-of-archived, rehire-of-live, switch-to-same-tier,
  audience-request-to-direct-superior, duplicate requests. A test that
  `expect_error`s these is testing pre-motto behavior.
- **`retire` is NOT leaf-only** (PLAN №1 is superseded): retiring a manager
  auto-dissolves its subtree (see DECISIONS D-003 for why that automation is
  legitimate). Only self-retire with live reports still refuses.
- **`ledger.now()` returns an ISO-8601 STRING.** All timestamp comparisons
  are string comparisons — correct (ISO-Z sorts lexicographically) but
  second-granularity; the extern `after` cursor knowingly lives with
  same-second ties.
- **Actor sentinels**: `USER="@user"`, `SYSTEM="@system"`,
  `EXTERN="@extern"`. A node may literally be *named* "user" (names win in
  `_resolve_recipient`). `audiences_held` mixes sentinels with node ids —
  map sentinels before iterating as node ids. And remember `actor_kind()`
  classifies org ROLE, not authentication (DECISIONS D-001).
- **Structural caps**: `max_depth`/`max_children` 1024 (user-ruled runaway
  insurance, D-083; bearers excluded), binding hire AND move (a move
  measures the moved subtree's deepest leaf). Per-org overridable in the
  doc only.

## Mail & delivery

- **`supervisor.send_message(slug, nid, text)` — `text` is NOT the
  message.** It is the drive *nudge*; the real content sits persisted in the
  node's mailbox, drained by `_envelope` at delivery. Order is `post_mail`
  **then** `send_message`. The single most natural wrong reading in the
  codebase. Corollary: queued/steered text has already been enveloped —
  check where `_envelope` is called before believing a double-delivery bug.
- **Three different mail stores**: `mail` = the live mailbox, popped at
  delivery and usually empty; `mail_log` = the capped persistent archive
  powering inbox views; `notices` = org-change notes delivered only at turn
  boundaries, never waking anyone.
- **"Notice" is an overloaded word**: the `notices` store above is
  org-change notes (`Org._notify`, the `[ORG NOTICES]` block). An AGENT
  notice (`orgtree_send_notice`, D-137) is a plain `mail` entry with
  `kind: "notice"` — same box, same journal, same fold-back. The kind is the
  ONLY marker, and it carries a contract: nothing may start a turn because
  of it. The three suppression points are `send_message(wake=False)`,
  rehire's drive, and reconcile's revive scan — all keyed on
  `Org.waking_mail(nid)`, not box non-emptiness. A new "wake whoever has
  mail" caller that tests the box directly (the natural wrong reading)
  regresses the feature silently.
- **`delivering` (org doc key) is NOT a live-delivery indicator** — it is
  the journal of drained-but-unconfirmed batches. Confirmation happens only
  when the text reaches the process; unconfirmed batches fold back in the
  turn's `finally` and at `reconcile`. Semantics are **at-least-once**:
  expect replays, never loss.
- **`st["steer"]` / `st["queue"]` items may be dicts**
  (`{"toks": [...], "text": ...}`), not bare strings — `_run_turn`
  normalizes. Code treating entries as plain strings reads the pre-journal
  shape.
- **The `drive` contract**: ledger ops and mail posts return a `drive` list
  of nodes the caller MUST wake (outside the doc lock). Rehire depends on it
  so mail queued while archived is finally acted on. A new caller that
  ignores the key leaves agents holding unread mail forever, with no error.
- **Names predating the org inbox**: `post_external_mail` /
  `_deliver_ext` handle *all* outside peers (`@org:` inter-org, `@mcp:`
  extern-MCP, `@net:` hub — and historically `@ext:`, the local file-queue
  bridge retired 2026-08-05); `tops` inside means
  top-levels **plus** extern-audience holders. Same residue class: `attach`
  is now an overloaded word — ledger `attach` code is mail
  *file-attachments*; the org attach/release feature is retired. Bundle any
  rename with the next wave touching those files.
- **Net state must die with the configuration it described.** Per-hub
  runtime state (`net_state`: registration, seen-ring, backoff; `net_spool`)
  is keyed by client-minted hub id, but an id is reusable and an address is
  editable — so **every cell that was earned against a hub records the
  ADDRESS it was earned against**, and both the settings write (api.py
  `net_hubs` replacement) and the daemon's `_participants` reconcile drop
  cells whose stamped address no longer matches. A new `net_state`/ring
  writer that forgets to stamp the address resurrects the original defect:
  a re-keyed or re-addressed hub inherits a stranger's registration and
  seen-ring, and inbound mail is silently deduped against messages from a
  different hub. Same discipline in reverse: `status_block`'s hidden/visible
  split for the implicit local hub compares the stamped address, not mere
  presence of a state cell.

## Supervisor & turns

- **Nothing under `store.DOC_LOCK` may touch the network.** The lock is
  global to the process, so an HTTPS round trip taken while holding it stalls
  every org on the backend, not just the one that took it. The usage readout
  (`limits.py`) routinely answers in over a second, and the freeze path —
  which writes its record under that lock — sits exactly where you would
  reach for it. The shape that works, and the one the freeze path uses: read
  `limits.cached()` (never fetches) to stamp inside the lock, then hand the
  fetching to a thread that does its round trip BEFORE it takes the lock, and
  have that thread prove it still owns what it is about to overwrite. Two
  structural guards in `test_limit_freeze.py` §6 pin both halves, because the
  runtime ones cannot see a lock they are not holding.
- **A timestamp scraped out of an error string is money.** `api_fallback`
  bills the org's own API key for the length of the window a usage freeze
  opens, and that window is priced off the freeze's reset time — so a wrong
  reset is a wrong bill, silently, for as long as it says. `\|(\d{9,11})`
  matches any long number that follows a pipe; a bare "resets 1:40pm" carries
  no date and rolls to tomorrow when the hour has passed; a cached readout on
  a broken upstream is served forever. Every one of those produced a
  real over-long window in review (23 hours against a 5-hour wall; 6 days
  against the same). The rule that came out of it: band every candidate by
  the lane it claims to describe — including an explicit epoch, whenever the
  same text also names a lane, because then the two are evidence about each
  other — bound the window itself independently (`_fallback_window_until`),
  and prefer a SHORT wrong answer, which costs one re-freeze where a long one
  costs the bill. The single exemption is an epoch with NO lane word beside
  it: there the CLI is stating a machine fact and nothing contradicts it.
- **Text an AGENT could have written may not price anything.** A clean
  result's `result` field IS the agent's own final answer, and the
  limit-detection gate promotes a short one that names a limit into the same
  `err_blob` the CLI's real errors arrive in. Everything downstream then
  treats it as evidence: it named its own lane (7 days), then — once that was
  capped — its own window (6 hours, ORG-WIDE, since `spawn_env` hands the key
  to every node while one is open), and it still fired the org-wide Fable
  escalation, which under the `dissolve` policy ARCHIVES every fable node in
  the org. Each was found a round after the last. Carry provenance
  explicitly (`agent_authored`, set at the one promotion site — never inferred
  from `err_blob is synth_limit_txt`, which lumps in the CLI's own
  `<synthetic>` limit record and throws away the reset it published), and gate
  every consequence on it, not just the arithmetic. ⚠ And check the RATE, not
  only the incident: capping such a window at 15 minutes still lost, because
  the window itself makes the node resumable (ignoring the `auto_resume`
  toggle — that is D-130's "api_fallback is its own consent", not D-122,
  which says the opposite for a record carrying both kinds), the resume
  replays the same prompt, and the same
  sentence re-opens it — 95% duty, forever. Unvouched evidence now opens no
  window at all, and a run of it stops the node's self-waking.
- **The host's usage lanes describe the HOST's subscription only.** An org
  billing its own key hits the API's walls, not the subscription's; timing
  such a freeze off `/api/oauth/usage` parked nodes for four hours on a
  per-minute rate limit. `supervisor.bills_the_key(org, on_fallback_key)` is
  the gate, and the lane is captured AT SPAWN — a fallback window expiring
  mid-turn does not move the turn that is already running.

- **An agent-triggered update MUST be detached — that path is the only one it
  has.** The update stops and restarts the backend, which tears down the very
  turn that asked for it, so any update script spawned as a child of an
  agent's own shell dies mid-flight with its session. Measured on a peer
  install 2026-08-09 (neoja): an agent ran `update.ps1` from a backgrounded
  shell job, the log stopped at `== building the UI ==`, the backend never
  restarted, and the repo was left advanced with the old code still running.
  An OPERATOR's console outlives the restart; an agent has no console that
  does. ⚠ This is why the mute-log bug below mattered far more than it looked:
  the detached spawn is not one of two routes for an agent, it is the ONLY
  one, and it was the route that reported nothing.
- **`_detached_spawn` must not use `DETACHED_PROCESS` on Windows.** That flag
  detaches the child from the console and carries the redirected stdout handle
  away with it: measured 0/4 lines reaching the log vs 4/4 under
  `CREATE_NO_WINDOW`, across `Write-Host`, `Write-Output`, `[Console]::Out`
  and a native child. Every Windows self-update logged nothing but the banner
  the Python side writes, from the feature's introduction until 2026-08-09.
  It hid this long because NO local deploy exercises the path — an operator
  runs `update.ps1` through a shell that has a console. Survival does not
  depend on the flag (a Windows child already outlives its parent;
  `DETACHED_PROCESS` governs the console, not the lifetime — verified by
  killing the parent mid-spawn).

- **Hook isolation is enumerated, not categorical.** Agents get a
  `--settings` with an explicit entry for every known hook event — empty
  arrays REPLACE inherited user-global hooks (live-tested), while the
  PostToolUse steer hook rides in the same dict. `disableAllHooks` cannot be
  combined with steering (it kills same-file hooks too — live-tested), and a
  hooks-only settings MERGES with the user's globals (a global SessionStart
  hook fired inside an agent — live-tested). ⚠ A hook event name the
  defensive list misses still inherits; when the CLI grows an event, extend
  the list in `_steer_settings`. Second safety net someone could delete
  without knowing it holds anything up: the bridge hooks carry
  `ORGTREE_NODE` env/cwd guards — that mitigation is why the historical leak
  never caused visible chaos.
- **`clean_env()` strips `CLAUDE_CODE_*` / `CLAUDECODE` for a reason**: the
  backend is routinely started from inside a Claude Code session, and
  inherited markers make the child CLI believe it is nested. It looks like
  dead defensive code; it is not.
- **`identity_prompt` must stay in agreement with `_build_cmd`**: a prompt
  promising a capability the config drops is a bug class already fixed once
  (MCP grants). Touch one side ⇒ touch both. Adding a verb touches FOUR
  places: `_org_op_locked` (UI/admin path), the `agent_call` dispatch (MCP
  path), the tool schema in `mcptool.py`, and the tool recital in
  `identity_prompt` — wired into fewer, the verb half-exists with no error.
- **`supervisor._bash()` exists because bare `bash` on a Windows host is
  WSL's** and cannot read `C:\` paths. Never shell out to `bash` directly.
- **Mid-task steering fires only on the pinned CLI** (`~/orgtree/cli`,
  preferred by the supervisor over the host install) or with
  `ORGTREE_STEER_HOOK=1` — CLI ≤ 2.1.31 fires no tool hooks headless, and
  steered mail then silently degrades to response-boundary delivery. Token
  streaming (`--include-partial-messages`) needs the pin too. Sandboxed
  turns always steer (the in-container CLI is current).
- **The two auxiliary fork launches** (compaction split, oracle fork) pass
  `{"disableAllHooks": true}` while the turn path sends the per-event steer
  shape — deliberate (no steering is wanted there, and isolation holds);
  do not "unify" them in either direction without re-reading D-004.
- **`_confirm_delivered`'s docstring still describes the pre-C1 "stdin
  write" semantics** — the real rule lives at the call sites: turn path
  confirms on the first non-`system` stdout event; steer path confirms at
  the hook's fetch (a ratified trade — D-045 Bounds).
- **A closed pipe raises `ValueError`, not `OSError` — and the turn loop can
  reach a boundary TWICE.** `TextIOWrapper.write`/`close` on a closed stream
  raise `ValueError: I/O operation on closed file.`, which no `except OSError`
  catches. The turn loop closes the CLI's stdin at a result boundary that finds
  the queue empty, but a result event is not once-per-turn: the CLI emits
  out-of-band results from its own stream-json writer (`error_during_execution`)
  and on `error_max_turns`, and a subagent's result carries
  `parent_tool_use_id`. Any of those re-enters the boundary branch, and a
  message queued in the interval is written down the closed pipe. Uncaught, it
  rode to the turn's catch-all: the desk showed a bare "I/O operation on closed
  file." with no site, the in-memory carrier was dropped, and the drained mail
  folded back to the mailbox undelivered — an agent visibly holding unreceived
  mail from a subordinate (user report 2026-08-19). ⚠ And the banner was the
  SMALL half. `res = ev` runs first and unconditionally, so a straggler
  carrying the CLI's real `is_error: true` clobbered the boundary result:
  `err_blob` then went non-empty and a SUCCESSFUL, paid turn raised
  "turn failed", `_after_turn` never ran, and its `total_cost_usd` was never
  booked — measured 0 turns booked, costs `[]`, plus a permanent
  turn_error_log row on a turn that worked, and the straggler's text fed to
  the freeze detectors. Catching the ValueError alone leaves all of that
  (redteam round 1, which is why the fix is a flag, not a wider `except`).
  Rules from it. ① Track `stdin_open` and treat a result arriving on a CLOSED
  pipe as a straggler, never a boundary — but ⚠ **the pipe only discriminates
  at a boundary that closed it.** At a boundary that FEEDS the next queued
  message stdin stays open, and there a straggler and that message's own
  result are the same event shape; no flag can separate them (redteam round 2
  measured the flag-only fix still losing both messages' spend, and "queue
  non-empty at the boundary" is just *mail arriving mid-turn* — the reported
  scenario). ② ∴ **the accounting is built to survive guessing wrong**:
  `turn_paid` tracks what the CLI reported and is consulted on **all three**
  ways a turn can end — folded into `res` before `_after_turn` on the success
  path, booked by `_charge_reported_spend` on the failure path, and passed to
  `_charge_killed_turn` as a measured floor under its estimate on the timeout
  path. Money is a fact about the API, not about how orgtree's bookkeeping
  ended. ⚠ The success path is not optional cover: the CLI's REAL out-of-band
  straggler carries **no `result` key and `total_cost_usd: 0`** (its text
  rides `errors: []`, and it only sets an exit code — nothing on stderr), so
  `err_blob` comes out EMPTY and the turn goes down the success path, where
  `_after_turn` booked that $0 over a message that had really billed. Round 3
  of the loop measured it still live after two rounds, because every fixture
  straggler until then carried a `result` string and so exercised the failure
  path only. Same measurement, second half: a CLI that exits non-zero with
  nothing on stderr used to read as a clean completed turn — **silence is not
  success**, so `err_blob` now names the exit code. ③ Refusing a straggler as
  a boundary must not discard what it REPORTS: a usage limit riding one is
  harvested into `synth_limit_txt` (engine-authored, so `agent_authored` stays
  False and it is trusted), or the node sails past a live limit into the next
  turn — measured. ④ Gate on `not ev.get("parent_tool_use_id")` as well, so a
  subagent finishing mid-turn cannot adopt its cost/duration/denials as the
  turn's (occupancy is already safe — `turn_occ` excludes sidechain events at
  the capture site, and `_after_turn` refuses the result event's cumulative
  usage by design). ⑤ Catch `(OSError, ValueError)` at every pipe site as
  defence in depth. Pinned by `test_turn_lifecycle.py
  --only dupresult`, whose stragglers carry poisoned numbers ($9.99, 900k
  tokens, 424242 ms, a denial) — with numbers equal to the boundary's, "not a
  boundary" is unfalsifiable and the checks passed with the guard reverted.
- **Readers and `os.replace` must not overlap on Windows** — the `_IOLatch`
  in store.py is writer-preferring for a measured reason (8 looping readers
  starved the replace 1,659/1,659). Nothing that can re-enter load/save may
  run under it; the held regions are one `read()` and one `os.replace()`.
- **One backend per data root is an OS-level lock now** (D-088,
  `claim_data_root` at api.main) — anything that spawns its own backend
  (tests, drills, probes) must use an isolated `ORGTREE_DATA` or it will be
  refused at startup.
- **Several suites carry DRIFT GUARDS** that mirror production expressions
  (msgvis.py greps the four source files for the nine expressions it
  replays; the runner fails LOUDLY with a ⚑ wall). A guard firing does not
  mean runtime breakage — it means fix the mirror or revert the source, and
  until then that suite's checks are fiction.
- **`ORGTREE_CLAUDE_CLI` is the test seam**: `backend/tests/fakecli.js` is a
  programmable Claude Code stand-in (timing dials) that turns delivery races
  into reproducible tests. The live tiers of the suites use it; only
  explicitly-marked runs touch a real CLI (haiku only, by user ruling).
- **A missing transcript means two different things, and №31 condemns on the
  difference.** `reconcile` marks a live node `unrecoverable` — which makes it
  REFUSE MAIL — when the transcript for its `session_id` is gone. Absence is
  only evidence of loss if the session was ever HANDED to the CLI, and two
  things break that: `cheap_compact`/`reseed` mint an id the CLI has never seen
  while the seat keeps the `cost_usd` that the sweep reads as “it ran” (hence
  `NodeDoc.session_unrun`, D-134), and `transcript_index` answers `{}` for an
  unreadable store exactly as it does for an empty one — so one unmounted org
  disk condemned every node in that org. Anything asking “did this session
  run?” must ask about the SESSION, and must distinguish “no transcripts” from
  “could not look” (`_transcript_evidence`, `transcript_index(strict=True)`).
  And “could not look” is narrower than it sounds: an entry that is GONE or
  is not a directory holds nothing and `glob` skips it, so the index is
  still correct — only an entry that EXISTS and cannot be read makes it
  short. Raising on the first kind condemned every node in every host org
  the moment a project dir vanished mid-walk or someone dropped a
  `desktop.ini` in `projects/`. And at the root, ABSENCE cannot be read
  off the errno either: a deleted directory, a junction whose target is
  gone, an unmapped drive and an unreachable share all raise the same
  `FileNotFoundError` on Windows, and only the first is a deletion
  (`_store_provably_absent` climbs to an ancestor that answers).
- **`reconcile` is called per-org from an unguarded FastAPI startup handler**
  (`api.py`, `@app.on_event("startup")`). Anything it raises stops the backend
  from starting — and it still has raisers past the transcript lookup
  (`store.load_org` on a corrupt doc, `float()` on a junk `cost_usd`, the tail
  `send_message` calls). The durable fix is a `try/except` around the per-org
  call; hardening one raiser inside an unguarded caller is a partial
  answer. ⚠ Guard the SWEEP, not the handler: everything that only ARMS
  something — `start_usage_warm_loop` at `api.py:534`, which just sets a
  flag and starts a daemon thread — must stay ABOVE the per-org loop at
  `:573` and outside any guard wrapped around it. Ordering is what makes
  it safe today. If a warm-loop start ever ended up below a call that can
  raise, the usage cache would never warm, every usage freeze would fall
  through to the blind 5-minute probe floor instead of its real reset, and
  NOTHING would fail loudly — the freezes would simply be timed wrong, and
  a freeze's length is money (see the `api_fallback` bullet above).
  (Cross-checked with the D-133 seat, 2026-08-18.)
- **`DOC_LOCK` is a single-process `threading.RLock`.** File writes are
  atomic (tmp + `os.replace`), which makes a concurrent SECOND backend look
  like it works while silently discarding interleaved load-modify-save
  cycles. One backend per data dir; there is no cross-process lock.

## Sandbox & storage

- **In a sandboxed org the ONLY mounted folder is the org workspace** —
  every other dir grant is stored by the ledger, shown granted in the UI,
  and silently dropped by `_build_cmd` (host paths do not exist in the
  container). A control that does nothing is displayed as if it worked.
- **The org disk mounts EXACTLY ONCE** (by the docker-desktop distro); a
  second mount of the same image is silent filesystem corruption. Verify the
  sentinel before every container start — Docker CREATES AN EMPTY DIR for a
  missing bind source, and an agent then rebuilds files into a divergent
  phantom workspace.
- **Two storage-enforcement models coexist** in supervisor.py: disk orgs
  (tiered: 80 warn / 90 turn-pause / 85 clear / 99 full; ENOSPC is the hard
  cap; NO container stop) and legacy volume-layout orgs (icacls deny +
  stop-and-freeze). Patch the right one; the module comment era matters.
- **Sandboxed transcripts live ON the org disk**, not under
  `<data>/sandboxes/` — for any migrated org the agent home (transcripts
  included) is on the ext4 image via `\\wsl.localhost`;
  `<data>/sandboxes/<slug>/` is only the frozen pre-migration rollback
  copy, and reading it yields *silently stale* transcripts — worse than
  missing.
- **`disk.py` has no platform guard** — off Windows a sandboxed org dies
  with an unhandled `FileNotFoundError: 'wsl'` (and kiosks sandbox by
  default). Host mode is genuinely cross-platform.
- **The repo path is not the data path**: the repo is the git checkout;
  `~/orgtree/` is live DATA (org docs, `.port`, the pinned CLI) and must
  not be "corrected" to match. Commit SHAs predating the clean import
  `bd45d51` belong to a retired predecessor repo and do not exist here.
- **The `/anthropic` proxy must force `Accept-Encoding: identity`**
  upstream and strip content-encoding/length/transfer-encoding, streaming
  raw — a gzip body with its header stripped reads as garbage at the CLI.
- **Store writes retry on `PermissionError` by design** — read-only
  endpoints deliberately read OUTSIDE the doc lock, and Windows fails
  `os.replace` over a momentarily-open file.
- **A bare `python -m orgtree.api` silently DROPS the public listener** —
  the 0.0.0.0 gateway starts only when `ORGTREE_PUBLIC_PORT` is set, which
  `update.ps1` does (7361) and a manual restart forgets. Manual restart =
  set the env + redirect logs + verify BOTH ports listen. Related deploy
  artifact: `update.ps1` never "hangs" — the spawned backend inherits the
  console pipe, so a piped invocation waits forever after the script is
  done; run it unpiped or redirect to a file.

## Public surface

- **The kiosk gateway is a DENYLIST, not an allowlist**: `_public_denied`
  allows any `/api` path it does not explicitly freeze, provided it is
  scoped to the token's org. **Every new endpoint is visitor-reachable by
  default** — an admin-only surface must be added to the matrix, and
  nothing (no test, no review signal) reminds you. This is how `/api/fs`,
  `/settings`, `/kiosk` and `/orgmd` each had to be retro-frozen.
- **`raise_ceiling` is computed at four independent sites** (`/ops`,
  `/defaults`, `/scope`, `/settings`) despite the "computed in exactly one
  place" comment — an invariant asserted once and implemented four times.
  Keep all four in agreement (or unify them) or a visitor-reachable ceiling
  raise is one refactor away.
- **`kioskRemaining` is the generic hard-cap channel and `null` means
  non-kiosk, not 0** — the null/number distinction carries meaning. Same
  family: `max_top_grant` has grown into the global drag ceiling for every
  credit bar whenever the cascade toggles are on.
- **`chatq_register_org` is not a pure register** — it self-checks kiosk
  status and *deregisters* sealed orgs. ⚠︎ The bridge it registers with is
  RETIRED (2026-08-05, user ruling: drop `@ext:` entirely); the function and
  its callers are pending removal, so treat this entry as a note about code
  on its way out, not a pattern to copy.
- **Known matrix/scrub drift (fixes pending):** `GET …/events` and
  `GET …/orgmd` are visitor-reachable and can leak host paths/usernames the
  scrub exists to hide (`…/history` passes `revoke_dir` path strings);
  `_public_denied` still 403s the dead `/attach` route — the denylist
  drifts in BOTH directions, not just toward openness.
- **`permission_mode` is never validated against `PM_LEVELS`** on
  `set_scope` or org creation (an arbitrary string reaches
  `--permission-mode` verbatim), and `hire()` skips `_apply_ceiling` for
  permission_mode in kiosks — both ride D-030 as pending hardening.

## Frontend

- **Nothing inside the world-transformed `.space` may use
  `position: fixed`** (the transform becomes the containing block — bit
  twice). Modals launched from in-world components must `createPortal` to
  `document.body`.
- **Desk and draft interiors use the inverted-scale regime** — authored at a
  virtual size and counter-scaled into the 124 px card, so authored px ≈
  screen px only at the intended zoom.
- **The API `Settings` body takes `compact_at` as a PERCENT (50–95) but the
  org doc stores a FRACTION** (0.50–0.95); `defaults.json` holds
  org-doc-shaped values, not request-shaped ones.
- **`CreditBar`'s `max ?? Infinity`** means `max=undefined` is legal and
  means unbounded; `maxGhost` renders only when finite.
- Org docs are re-slugified from **name** — smoke-org names collide only
  when data dirs are shared; always use an isolated `ORGTREE_DATA` in tests.
- **`vite build` does NOT typecheck** — the frontend gate is
  `npm run typecheck` (tsc --noEmit); a green build proves nothing about
  types.
- **`fitAll()` must not clamp computed bounds at zero** — with the eye
  pinned at a constant x, leftmost nodes go NEGATIVE in wide orgs, and a
  zero-clamp silently crops them out of "fit the whole org".
- **Background pan calls `setPointerCapture`, retargeting subsequent
  clicks to the viewport** — every screen-space control layered over the
  canvas must stopPropagation on pointerdown or its clicks silently do
  nothing.
- **Never author text below ~10 px inside the counter-scaled virtual
  panels** — browsers clamp small font sizes UP, exploding the authored
  layout; shrinking is the scale transform's job.
- **Counter-scaled hover chrome (`--invz` chips, bar tips) must anchor
  1 px INSIDE the card border** — any border↔chip gap is a dead zone that
  hides them before they can be clicked (hit-testable only while hovered).
- **The viewport must never natively scroll** — zero scrollLeft/scrollTop
  in the onScroll handler and the spring tick, and always
  `focus({preventScroll: true})`; one focus of an off-screen input shears
  the HUD off the canvas until reload.
- **No CSS transitions on spring-animated geometry** (wire path `d`, node
  transform, bar height) — an ease on top of per-frame springs makes wires
  trail and layers poke out of the animating container.
- **The chat markdown pipeline is constraint-loaded**: gfm + hard breaks,
  DOMPurify, `<` escaped in PROSE ONLY via a fence-aware line walk
  (unterminated fences stay open to EOF), parse cache bounded — else
  `Sync<float3>` silently becomes `Sync` and streaming re-parses the
  transcript at ~8 Hz.
- **`.sq.aud` (audience glow) and `.sq.stack1-3` (lineage slabs) both set
  `box-shadow` at equal specificity** — the later stack rules win
  wholesale, so a card with both shows only the stack (ruling direction
  pending: DECISIONS §Open).

## Testing reality

One automated test file exists: `backend/tests/test_ledger.py` (ledger
only; deliberately excluded from pyright because it passes wrong shapes to
assert `LedgerError` — its gate is *running* it). Supervisor, api, sandbox,
disk and the frontend have no automated tests and there is no CI: a green
`pyright` + `tsc` + ledger-suite run proves nothing about the turn loop,
mail journal, kiosk gateway, or disk mounts. Those paths are verified by
scripted live drills at change time — keep drilling them.

(That paragraph is stale on the count — `backend/tests/test_*.py` is 40
suites as of 2026-08-26, discovered by glob. Its point about *coverage* still
stands; the number does not.)

## Running a long job without losing it

The full tier takes tens of minutes, which is longer than an agent's turn.
Everything below was measured on this machine on 2026-08-26 (`task-timeouts`),
after two tier runs were silently cut and one deploy was gated on a partial
one. None of it involves a timeout: nothing on this machine has one short
enough to have fired.

- **A job started as a harness BACKGROUND TASK dies when the turn ends.** It
  is a child of that turn's `claude.exe`, and `supervisor.py` closes the CLI's
  stdin at every turn boundary (`stdin.close()` in `_run_one_turn`) — one CLI
  process per turn, and its children go with it. Measured with a heartbeat
  process: last beat at 23:09:32Z, the same second the CLI exited. Two tier
  runs died this way at 49 s and 36 s, having completed 19 and 25 suites.
- **The same applies to the WAITER, which makes the obvious pattern
  circular.** Starting the run in one background task and then waiting on it
  from a second one kills both at the same boundary — the agent learns
  nothing, and the "task stopped" notice it eventually gets says only that no
  completion record was found. That reads like the job failed, and it also
  reads like the job succeeded.
- **This is also how a liveness check comes back FALSE for a live process.**
  A reaped harness task is not a dead runner. One agent read it that way,
  relaunched a duplicate, and only avoided a port collision by noticing the
  original was still running.
- **A DETACHED process survives the turn — but not a backend restart.**
  `Start-Process` (Windows) or `start_new_session` outlives the turn fine; a
  detached tier run was measured completing all 40 suites across several turn
  boundaries. But `_leash()` puts every CLI child in a Windows job object with
  `KILL_ON_JOB_CLOSE` tied to the backend, and job membership is INHERITED by
  every descendant — so `orgtree_self_restart` / `update.ps1` reaps detached
  runners too. Confirmed with `IsProcessInJob`: processes started outside
  orgtree read *not-in-job*; a deliberately-detached grandchild reads
  *in-job*. **Sequence a deploy against runs in flight; the tool's own "no
  agent is mid-turn" refusal does not know a detached test run exists.**
- **So: launch detached, record your own exit status, and wait with a
  watchdog.**

  ```
  python tools/run_tests.py --full > run.log 2>&1; echo "RUN-EXIT=$?" >> run.log
  ```

  Then `orgtree_watchdog` `kind=file` on `run.log` for `RUN-EXIT=`. The
  watchdog is persistent, survives turn ends and backend restarts, and costs
  nothing — it caught a process death in 12 s in the drill that established
  all of this.
- **A cut run and a passing run are otherwise identical.** Both end in a
  column of `✓` with empty stderr. `run_tests.py` therefore ends a real run
  with `RUN COMPLETE …` on stdout and a `COMPLETE` file in `logdir` (D-157);
  gate on those, never on "I did not see a ✗". For a run whose stdout is gone,
  `ls <logdir> | wc -l` against the `plan · N to run` header still tells you
  how far it got — suite logs are written on completion, so the count measures
  progress. That works on the ~800 runs already in `%TEMP%`.

## Test-tree authorship

`backend/tests/` and `frontend/tests/` have a **single** adversarial
author — this repo's redteam seat; the implementer lands and deploys. Set
2026-08-06, on a deconfliction request from another org running this same
codebase (neoja) after they hired their own redteam. External or cross-org
findings arrive as **reports**, not commits — inline test bodies in the
report are welcome, but the redteam seat adapts them into suite idiom before
they land, and origin credit goes in the commit message rather than the
authorship. Rationale (worth keeping verbatim): two adversarial seats must
never author one tree, because a merge conflict in a test tree gets resolved
by picking a side rather than understanding both.

Naming follows the same split: bare "redteam" means this repo's own seat; a
remote org's seat is named explicitly — the `@net:` prefix, or possessive
("neoja's redteam") — never left bare.
