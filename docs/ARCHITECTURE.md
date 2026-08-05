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
  `_deliver_ext` handle *all* outside peers (`@ext:` chatq, `@org:`
  inter-org, `@mcp:` extern-MCP), not just chatq; `tops` inside means
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

- **Hook isolation is enumerated, not categorical.** Agents get a
  `--settings` with an explicit entry for every known hook event — empty
  arrays REPLACE inherited user-global hooks (live-tested), while the
  PostToolUse steer hook rides in the same dict. `disableAllHooks` cannot be
  combined with steering (it kills same-file hooks too — live-tested), and a
  hooks-only settings MERGES with the user's globals (a global SessionStart
  hook fired inside an agent — live-tested). ⚠ A hook event name the
  defensive list misses still inherits; when the CLI grows an event, extend
  the list in `_steer_settings`. Second safety net someone could delete
  without knowing it holds anything up: the chatq hooks carry
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
  status and *deregisters* sealed orgs.
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
