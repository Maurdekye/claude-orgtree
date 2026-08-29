# Adding a model provider to orgtree — the playbook

Standing notes kept WHILE the second provider (Codex / OpenAI, tiers
luna·terra·sol) was being implemented, at the user's instruction
(2026-08-28): every step taken, generalized so the third provider (gemini,
grok, kimi, z-ai, …) is a walk down a checklist instead of a re-derivation.
The Codex-specific design record is `design-multi-provider.md` in the
implementing agent's scratch; DECISIONS.md and §6 of that doc hold the user
rulings. THIS file is the transferable method.

Provider #3 (Gemini, tiers flash·pro, D-183…D-190) walked this checklist
2026-08-29 and it predicted well — the steps below carry `[gemini:]` marks
where the third walk added a variant or corrected a prediction. Its probe
record is the implementing agent's scratch (`gemini-provider/breadcrumbs.md`
+ banked probe logs).

Maintained live: every increment that lands for a provider updates the
matching section here. If you are adding provider #4 and a step below didn't
match reality, fix the step — this document is only worth what it predicts.

## 0. Principles (user rulings, provider-agnostic)

- **The set of CONNECTED provider CLIs is the set of hireable tiers.**
  orgtree is not Claude-centric; Claude is one provider among others (design
  §1 principle 3, user 2026-08-28).
- **Tier names stay ONE flat vocabulary** — a tier implies its provider;
  nothing takes a provider argument next to a tier (providers.py docstring).
- **Seats = API $ per M input tokens at the STANDING price**, floored to 1
  (promos don't set seats — sonnet-intro precedent). **Cost-dollars use
  CURRENT listed prices** (promos included). Dollars ≠ seats; write both in
  providers.py with sources and dates.
- **Naming: the CLI's own product name** ("Codex", not "ChatGPT (Codex)" or
  "OpenAI") — the user names the tool they installed, not the vendor.
- **Kiosks hold a new provider out until its sandbox story is settled.**
- **Headless orgs may only HIRE tiers from keyed providers.**
- **Distinctive tier-chip hues per tier; the provider gets ONE desk theme
  color** (codex: aquamarine-teal `--prov-openai`), not a chip recolor.
- **Credentials are never read, copied or moved.** Connect-state detection
  is an existence/JWT-display read of the CLI's own auth store at most; the
  child process inherits the CLI's own home and refreshes in place. Copying
  an auth file split-brains its refresh cycle.
- **One credential per spawn:** the child sees ITS provider's credentials
  and nobody else's — strip the other providers' env vars at spawn
  (`ANTHROPIC_*`/`CLAUDE_CODE_*`/`CLAUDECODE` from a codex child, and a
  stray `OPENAI_API_KEY` too, which would silently flip billing from the
  subscription login to metered API).

## 1. Recon before any code (the probe phase)

What it looked like for codex, and what to reproduce per provider:

1. **Find the machine substrate.** Prefer a long-lived structured-IO server
   surface over a bare one-shot exec if the CLI has one (codex: `codex
   app-server`, stdio NDJSON JSON-RPC — the surface its own IDE extension
   uses; bare `exec --json` was the fallback). The docs will be incomplete:
   probe the binary, don't trust prose — orgtree's codex recon was wrong
   TWICE before a live probe settled it. [gemini: `gemini --acp` (stable
   ACP, stdio JSON-RPC); the one-shot `-p … -o json` lane stayed as the
   diagnostic surface. The ACP `session/new` response is ALSO the
   authoritative model registry — richer than any docs page.]
2. **Install a private pin** (`npm install --prefix <data>/codex
   @openai/codex` pattern), resolve env-override > pin > PATH, and never
   route through a `.CMD` shim (argv truncation at embedded newlines).
3. **Probe UNAUTHED first** (isolated home dir, no login): protocol
   handshake, model list, error shapes. Then the auth-gated battery on a
   real account. Bank every event log. The codex battery that mattered:
   end-to-end turn, MID-TURN STEER, graceful interrupt, fork/compact,
   N-parallel on one account, identity-file honoring, approval callbacks,
   usage/limit telemetry shapes, account read.
4. **Verify the six seam capabilities** the adapter needs (each has a codex
   answer to compare against): session resume by durable id · mid-turn
   input · graceful interrupt · tool attachment with per-agent identity ·
   usage+limit telemetry · identity/system-prompt injection. [gemini: a
   capability may simply be ABSENT and that can be fine — no steer verb
   exists, and the supervisor's queue fallback already gives boundary
   delivery; ship the refusing `steer()` rather than inventing a lane.]
5. **Price table**: web-verify input/cached/output per M for every tier,
   twice, with sources and dates in the code comment. [gemini: the table may
   need to be keyed by MODEL ID, not tier — the CLI spends tokens on SIDE
   MODELS inside one turn (a `utility_router` on flash-lite, measured), so
   the cost fold prices every model the usage document names, with a
   non-zero fallback row for strangers. Watch for LONG-CONTEXT rate bands
   (3.1-pro doubles above 200K prompt tokens).]
6. **Three gemini-walk hazards worth probing on any provider:** (a) an
   unknown `--model` id may be SILENTLY replaced by the default — pin ids
   exactly as the CLI's own registry reports them AND assert the served
   model in the session/turn result, failing loudly on mismatch; (b) the
   auth secret may live in an OS KEYCHAIN (gemini api-key: Windows
   Credential Manager) — detect connect-state from the CLI's own config
   records, never open the secret, and let a missing credential fail the
   turn with the CLI's own error; (c) a resume verb may REPLAY stored
   history as live-looking events — gate stream/journal folding until the
   new turn's input is actually on the wire.

## 2. The provider registry (backend/orgtree/providers.py)

Additive module, no adapter yet — shippable as a read-only preview:

- `<PROV>_TIERS` (tier → seat), `<PROV>_MODELS` (tier → full model id, as
  the CLI itself reports them — aliases drift), chip letters, and later
  `<PROV>_CONTEXT` (measured window) and `<PROV>_PRICES` (see §0 pricing).
- Detection: `<prov>_path()` env > pin > PATH; `<prov>_version()` read from
  package metadata WITHOUT running the binary when possible, hard-timeout
  probe otherwise; `<prov>_account()` connect-state from the CLI's auth
  store (existence + display identity only); `<prov>_status()` cached ~60s
  (panels poll).
- `providers_payload()` grows one entry: id, label (§0 naming), cli, tiers,
  status, `hire_enabled` (hard-False until the adapter lands), `reason`
  (the user-facing tooltip, ordered by what they'd do next: install cmd →
  login cmd → preview note).
- Tests: `test_providers.py` — detection resolution order, tier tables,
  payload shape, connect-state against planted auth files.

## 3. The turn runner (backend/orgtree/<prov>run.py — codexrun.py)

One module owning the wire, one process per turn, hermetically testable:

- A client class (spawn from an ARGV HEAD, not a bare exe; reader thread
  pumps stdout; server→client requests answered synchronously via caller
  hooks; env hygiene per §0 applied AT SPAWN in one place). Process-level
  `cwd` = the agent's scratch (identity-file discovery and relative paths
  resolve against the PROCESS, not a protocol param — measured the hard
  way).
- A turn class normalizing the provider vocabulary to orgtree's:
  `start(input) -> durable session id`, `steer(text) -> bool` (False = the
  turn-over guard refused; caller re-queues), `interrupt() -> bool`,
  `wait(timeout) -> {status: completed|interrupted|failed, agent_text,
  token_usage, rate_limits, thread_id}`. "Interrupted" is a COMPLETED
  turn, not a failure.
- Tool attachment: if the CLI supports client-answered dynamic tools
  (codex: `dynamicTools` + `item/tool/call` server-requests — probe it, it
  gated the whole architecture), org powers attach as the SAME tool cards
  `mcptool.TOOLS` serves the first provider, answered in-process by POSTing
  `/api/agent` — the ledger enforces authority identically and no bridge
  process or user-config write exists. A tool error is an ANSWER, not a
  hang; unexpected server-requests are refused loudly; approvals fail
  CLOSED. [gemini: the EASIER door, when the wire has it — a per-session
  MCP-servers PARAM (ACP `session/new`/`session/load` `mcpServers`): the
  `python -m orgtree.mcptool` stdio server the claude lane already spawns
  plugs in UNCHANGED, per agent, no config writes and no in-process
  answering layer at all. Two wire facts to probe: env entries may be an
  ARRAY of {name,value}, and vars NOT named in the spec INHERIT from the
  CLI process — always name the full ORGTREE_* identity set. And whatever
  rides session OPEN must ride session RESUME (the §7 rule) — the param
  goes on BOTH verbs.]
- **The test double first** (`backend/tests/fake<prov>.py`): a scripted
  stand-in speaking the real wire (shapes copied from the probe logs), with
  scenarios for tool round-trip, steer, interrupt, and an env probe that
  dumps named env vars to a file — credential hygiene proven without any
  real credential near the tests. Suite: `test_<prov>run.py`.

## 4. Supervisor dispatch (the seam inside _run_one_turn) — M1b

The single most delicate step. The shape that works:

- The prologue (slot gate, state gates, mail drain, inflight persist,
  `turn_started`) is provider-neutral — dispatch AFTER it, on tier
  membership (`model in providers.<PROV>_TIERS`), BEFORE any first-provider
  machinery.
- **Do not early-return** (the shared `finally` pops the next queued
  carrier into the return value AFTER a return fixes it — returning early
  strands mail) and **do not write a second function with its own
  finally** (drift). Run the provider leg, replicate the tiny success tail
  (`last_error=None`, `turns_run+=1`, `account_switches=0`,
  `paid_booked=True`, `_after_turn(...)`), then `raise _<Prov>TurnDone` — a
  control-flow exception caught by its own `except … : pass` arm ABOVE the
  generic handler, unwinding to the SHARED finally. Failures raise plain
  `RuntimeError("turn failed: …")` into the existing machinery
  (last_error + durable error row).
- The leg: connect-state guard (loud failure naming the remedy) · sandbox
  guard (kiosk holdout) · identity written pre-spawn (provider's
  identity-file door + per-thread instructions param) · session id =
  HARVESTED from the provider, stored under DOC_LOCK **with a
  `<prov>_thread` marker — only ever resume an id the leg itself
  harvested; a fresh hire's minted uuid resumes nothing** · steer pump
  polling `pop_steer` every ~2s wrapping messages in the SAME mid-task
  envelope the steer hook uses, falling back to the queue when the
  turn-over guard refuses · live text deltas through `stream()` with the
  first provider's batching (~8 Hz / 400 chars) · `interrupt_turn` taught
  the new live-session handle (`st["<prov>_turn"]`) next to `st["proc"]`.
  [gemini: markers `gemini_session`/`st["gemini_turn"]`; the identity door
  is GEMINI.md alone (no instructions param exists) — measured re-read on
  session/load, so the per-spawn rewrite self-heals resumes too. A provider
  with NO steer verb keeps the SAME pump: every offer refuses and the
  texts requeue — identical code, boundary-delivery semantics, and the day
  the wire grows the verb nothing needs rewiring. And mind the SPLIT:
  `_compact_split_body` dispatches per provider — a lane without a native
  fork/compact must REFUSE cleanly there (cheap compact is the supported
  path), or a gemini/next-provider node falls into the claude fork
  machinery.]
- Bookkeeping mapping: cost = tokens × `<PROV>_PRICES` (know whether
  `inputTokens` INCLUDES cached — codex: yes — and whether output includes
  reasoning — codex: yes); occupancy = the LAST call's input (cumulative
  totals overcount, the "123% context" bug); context window pinned in
  `TIER_CONTEXT` from the provider's own reported number, added BEFORE the
  env override so the user still wins.
- Suite: `test_<prov>_dispatch.py` driving `_run_one_turn` in-process
  against the test double: dispatch+bookkeeping, tool round-trip,
  resume-vs-fresh, env hygiene, identity file, live steer, live interrupt,
  and a PLANTED FAULT the failure path must see (anti-vacuity).

## 5. Hire enablement (ledger tables + guards) — M4

- Codex status: LANDED (74d150c). Gemini status: LANDED (D-188), same
  shape; its keyed-login set is {api-key, vertex}. What it took,
  generalized:
- Tiers/models into `ledger.TIERS`/`ledger.MODELS` (the add-only org-doc
  load hook migrates EXISTING orgs automatically — org docs carry their own
  seat-table copy); kiosk ceiling rank = seat (equal seats = equal rank is
  fine). Make the provider module DERIVE its views from the ledger tables
  once they land — two copies of a seat price will drift.
- One `provider_hire_gate(org, tier)` in api.py, called at ALL FIVE doors
  (user hire, agent hire, user switch_model, agent switch_model, and a user
  rehire that overrides the tier), raising
  LedgerError (both layers 422 it cleanly): installed → signed-in → kiosk
  holdout → headless-needs-keyed, each refusal naming the next step. The
  Agent-side `orgtree_rehire` is not another door: its schema has no `tier`
  override. The incumbent provider stays ungated — a detection bug must not brick
  existing orgs.
- The MCP server's cards are DEPENDENCY-FREE (hand-written enums): grow the
  hire + switch tier enums and the seat prose by hand; its test asserts
  enum == ledger.TIERS and then follows automatically.
- Drift guards updated DELIBERATELY, not discovered: `chiptips.test.tsx`
  (regex-scrapes ledger's TIERS literal AND the frontend `tree.tiers ?? {…}`
  fallback in OrgCanvas — update both sides), `test_ledger_authority`
  ("exactly the N price bands" grows), and the provider suite's own
  preview-era checks FLIP (from "refused as unknown tier" to "plain ledger
  hire") — a test asserting the gap must flip the day the gap closes.
  `accounts.py` TIERS stays FIRST-PROVIDER-ONLY until that provider gets
  account routing.

## 6. Frontend — M8

- Codex status: LANDED (ee7b32a). Gemini status: LANDED (D-189) — the
  provider threading is per-family props (codexHire/geminiHire), so
  provider #4 adds one more; the chrome contract (`--prov-<id>` rebinding
  the accent variables at the family root) held perfectly.
- Hire surfaces render as PROVIDER
  FAMILIES from the `/api/providers` payload: the canvas fetches it
  (non-fatal, absent payload degrades to a disabled preview — never to
  hidden chips) and threads `{enabled, reason}` to every chip set and the
  compact hire sheet. `hire_enabled` in the payload flips to "provider
  connected" at this point — the same predicate the api hire gate
  enforces, so UI and gate cannot disagree.
- USER SPEC (2026-08-28), as implemented: each provider's chips are one
  family row (family COLUMN on the coworker edges); the incumbent family
  always sits NEAREST the card on every edge, which makes top/bottom
  mirror about x and left/right about y. Kiosk orgs render no held-out
  provider at all. Watch the strip ANCHORING when a second family row
  appears: a top strip anchored by its top grows DOWN over the card —
  re-anchor by the bottom (desk variants too).
- Effort vocabulary mapped per provider (codex reasoning efforts are a
  superset of orgtree's low…max — pass-through, `ultra` unused).
- Tier chips keep distinctive hues; the provider's desk theme is one color
  pair (`--prov-<id>`, `.sq.prov-<id>.desk/busy`). Sweep for stale vendor
  naming while there ("ChatGPT (Codex)" survived in the hire sheet).

## 7. Transcript durability — M3

- Codex status: LANDED (abe495f). Gemini status: LANDED (D-187) — the
  codex journal store carried the third provider UNCHANGED, exactly as
  this section predicted; write records through the same helper.
- What worked: the supervisor writes its
  own per-agent journal — `journals/projects/<org>/<session>.jsonl` under
  the org data root, records in the INCUMBENT transcript's exact shape —
  and the two transcript-lookup functions learn that store as a second
  root. Every reader (desk history, reconcile liveness, never-run pardon,
  occupancy fold) then works unchanged. Do NOT parse the provider's
  private rollout files, and do NOT build a parallel bookkeeping path —
  one layout, one index. Success paths only (failed mail folds back and
  redelivers; journaling it would duplicate).
- Two hard-won wire facts to re-verify per provider: (a) whatever rides
  session OPEN must ride session RESUME too — codex's thread/resume takes
  dynamicTools + developerInstructions (measured after the test double
  caught the runner passing them on start only, which silently stripped
  every post-first turn of its org powers); (b) rapid kill→spawn cycles on
  one provider home can contend on the CLI's own state store (codex:
  sqlite under ~/.codex — a fresh process within ~1s of a kill failed to
  boot). Mind it wherever turns cycle fast.
- Test-rig hygiene: a leg whose tool dispatcher POSTs the org API must be
  pointed at a DEAD port in hermetic rigs, or the tests reach the
  operator's live deployment on the default port (measured).

## 8. What stays deliberately out of the MVP

Account pooling/routing for the new provider (Phase 2) · sandbox/kiosk
admission (own decision, §0) · provider-side compaction verbs beyond
orgtree's own cheap-compact · rate-limit-driven freezes from provider
telemetry (P2 autonomy parity; the telemetry is already normalized and
carried in the turn result for it).

## 9. Process rules that made this survivable

Small commits, every increment, tier-green before any deploy · the full
increment map lives in the working agent's CLAUDE.md so compaction lands
between commits · breadcrumbs updated as things happen, written for a
stranger · an instrument that reports "nothing found" must first prove it
can find a planted fault · commit BEFORE running anything that mutates and
restores the tree.
