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
- **Structural caps exist only in code**: `max_depth` 10, `max_children` 256
  (bearers excluded). Overridable only by editing the org doc; no UI or doc
  predicts the refusal.

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

## Testing reality

One automated test file exists: `backend/tests/test_ledger.py` (ledger
only; deliberately excluded from pyright because it passes wrong shapes to
assert `LedgerError` — its gate is *running* it). Supervisor, api, sandbox,
disk and the frontend have no automated tests and there is no CI: a green
`pyright` + `tsc` + ledger-suite run proves nothing about the turn loop,
mail journal, kiosk gateway, or disk mounts. Those paths are verified by
scripted live drills at change time — keep drilling them.
