# Adding a model provider to orgtree — the playbook

Standing notes kept WHILE the second provider (Codex / OpenAI, tiers
gpt-reserve·luna·terra·sol) was being implemented, at the user's instruction
(2026-08-28): every step taken, generalized so the third provider (antigravity,
grok, kimi, z-ai, …) is a walk down a checklist instead of a re-derivation.
The Codex-specific design record is `design-multi-provider.md` in the
implementing agent's scratch; DECISIONS.md and §6 of that doc hold the user
rulings. THIS file is the transferable method.

Provider #3 (Antigravity, tiers flash·pro, D-183…D-190 and D-231) walked
this checklist twice — 2026-08-29 as Google's earlier CLI and again
2026-09-02 when the Antigravity CLI (`agy`) replaced it outright — and it
predicted well both times. The steps below carry `[antigravity:]` marks
where those walks added a variant or corrected a prediction. The probe
record is the implementing agent's scratch (`antigravity-scope/
breadcrumbs.md` + banked probe logs under `probes/`).

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
   TWICE before a live probe settled it. [antigravity: NO server surface
   exists — an ACP mode is an open feature request — so the substrate is
   print mode itself: `-p= --input-format stream-json --output-format
   stream-json`, the prompt as one NDJSON `user` event on STDIN (argv caps
   at 32K characters on Windows; stdin carried 120K of prose intact) and
   an NDJSON event stream back. `agy models` is the authoritative registry
   — richer than any docs page, and it names effort-suffixed ids the CLI
   REFUSES on `--model`; the base id rides `--model`, the effort rides
   `--effort`.]
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
   usage+limit telemetry · identity/system-prompt injection. [antigravity:
   a capability may simply be ABSENT and that can be fine — no steer verb
   exists, and the supervisor's queue fallback already gives boundary
   delivery; ship the refusing `steer()` rather than inventing a lane. And
   "graceful interrupt" may legitimately be a KILL: the conversation store
   is written as the turn runs, so the next resume continues from the kill
   and the per-request usage the steps already reported is billed.]
   [antigravity, D-233: usage+limit telemetry may be ABSENT from the wire
   too — then the WALL is the telemetry: parse the reset out of the CLI's
   own error ("Resets in 165h21m54s"), hand it to the freeze as a
   provider reset, and record the account standing from the turn. Do not
   open a "read-only" slash-command door on a panel's poll until you have
   measured that it spends no turn — agy's print-mode `/quota` did.]
5. **Price table**: web-verify input/cached/output per M for every tier,
   twice, with sources and dates in the code comment. [antigravity: key the
   table by the BASE model id the CLI is handed, with a non-zero fallback
   row for strangers, and know the wire's semantics before folding:
   `input_tokens` is UNCACHED input (cache reads sit beside it), output
   INCLUDES thinking, and the result's usage SUMS every request of the turn
   — occupancy is the LAST priced request's input + cache read, never that
   sum. Watch for LONG-CONTEXT rate bands (3.1-pro doubles above 200K
   prompt tokens).]
6. **Four hazards from the Google-lane walks worth probing on any
   provider:** (a) an unknown `--model` id may be SILENTLY replaced by the
   default (the earlier CLI did; the Antigravity CLI fails loudly, rc=1
   with the registry listed) — pin ids exactly as the CLI's own registry
   reports them AND assert the served model from the init/session result,
   failing loudly on mismatch; (b) the auth secret may live in an OS
   KEYCHAIN with NO auth file at all (the Antigravity CLI: a Google OAuth
   token in Windows Credential Manager) — detect connect-state from the
   CLI's own registry output and its own log (`--log-file` is a ROOT flag:
   `agy --log-file X models`, never after the subcommand), never open the
   secret, and let a missing login fail the turn with the CLI's own error;
   (c) a resume verb may REPLAY stored history as live-looking events, or
   — the Antigravity shape — answer a MISSING conversation id with a fresh
   conversation and only a stderr warning: compare the harvested id with
   the requested one; (d) the process cwd may NOT be the workspace: the
   Antigravity CLI ran its tools in its own scratch until `--add-dir <cwd>`
   said otherwise.

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
  CLOSED. [antigravity: the EASIER door, when the CLI has it — a
  WORKSPACE PLUGIN it discovers walking up from the cwd:
  `<cwd>/.agents/plugins/orgtree/{plugin.json, mcp_config.json}` carries
  the `python -m orgtree.mcptool` stdio server the claude lane already
  spawns, UNCHANGED, per agent, with no write to the user's own
  `~/.gemini/config` and no in-process answering layer at all
  (`antigravityrun.write_workspace` regenerates it per spawn). Two wire
  facts to probe: the config shapes are `command/args/env` and
  `serverUrl/headers` (what `agy mcp add` itself writes), and vars NOT
  named in the spec INHERIT from the CLI process — always name the full
  ORGTREE_* identity set and scrub the other providers' material at spawn.
  MCP calls arrive on the wire as `call_mcp_tool{ServerName, ToolName,
  Arguments}`: journal them under the TOOL'S bare name, which is what the
  download-card and mail-link readers match.]
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
  [antigravity: markers `antigravity_conversation`/`st["antigravity_turn"]`;
  the identity door is AGENTS.md (a directory rule the CLI injects on every
  turn, resumed or not — measured), so the per-spawn rewrite self-heals
  resumes too. THE ⚙-RIGHTS SEAM IS A HOOK, NOT A MODE: headless print
  mode cannot prompt, so its review mode auto-DENIES every command, write
  AND org-power call and ends the run — every turn therefore runs with the
  CLI's prompts switched off, and a narrowed scope is enforced by a
  PreToolUse hook in `<cwd>/.agents/hooks.json` whose command is a wrapper
  script with no quoted-executable-plus-argument shape (`cmd /c` mangles
  that; a hook that fails to run blocks the call, so the failure mode is
  closed). A provider with NO steer verb keeps the SAME pump: every offer
  refuses and the texts requeue — identical code, boundary-delivery
  semantics, and the day the wire grows the verb nothing needs rewiring.
  And mind the SPLIT: `_compact_split_body` dispatches per provider — a
  lane without a native fork/compact must REFUSE cleanly there (cheap
  compact is the supported path), or a next-provider node falls into the
  claude fork machinery.]
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

- Codex status: LANDED (74d150c). Antigravity status: LANDED (D-188,
  re-walked in D-231), same shape; its keyed-login set is EMPTY — the CLI
  signs in with a Google account only — so a headless org can never hire
  it, and the gate says so. What it took, generalized:
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

- Codex status: LANDED (ee7b32a). Antigravity status: LANDED (D-189) — the
  provider threading is per-family props (codexHire/antigravityHire), so
  provider #4 adds one more; the chrome contract (`--prov-<id>` rebinding
  the accent variables at the family root) held perfectly, and survived
  the CLI swap untouched because the provider ID (`google`) and the tier
  words did not move — only the labels did.
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

- Codex status: LANDED (abe495f). Antigravity status: LANDED (D-187) — the
  codex journal store carried the third provider UNCHANGED, exactly as
  this section predicted, through both of its CLIs; write records through
  the same helper.
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

## 10. An API-BACKED lane — the OpenRouter walk (2026-09-02)

Provider #4 was not a CLI. OpenRouter is a REST gateway with one API key in
front of ~425 models, so §1–§7 did not apply as written: nothing to detect,
no auth store, no session substrate. What was reused instead, and what was
new — recorded for the next gateway (Vercel AI Gateway, Requesty, a
self-hosted LiteLLM all speak the same recipe):

- **The harness is borrowed, not built** (user decision: Claude Code, one
  lane). The Claude lane's `spawn_env` injects the gateway's own cookbook —
  `ANTHROPIC_BASE_URL`, the key as `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`
  explicitly empty, every `ANTHROPIC_DEFAULT_*_MODEL` pinned to the node's
  model — and everything else (resume, `--mcp-config`, steer, journals,
  fork/compact, warm pool) comes free. MEASURED on 2.1.258: resume hits the
  cache inside the 5-minute window; Bash tool round-trips work; a
  non-Anthropic model (gpt-5.6-luna) completed a tool task, another
  (gemini-3.5-flash) ran but answered empty — "best-effort" is the honest
  label for them, and the picker says so.
- **"Installed" is a stored key, "connected" is the gateway accepting it**
  (`GET /api/v1/key`, cached 60s), both in `openrouter.py`. The key is
  written by one endpoint and never read back by any; `status()` is
  secret-free by construction and a test asserts it.
- **Tiers are minted at runtime.** The user's favorites become
  `or-<slugified model id>` tiers with the §0 seat rule applied to the
  catalog's own price (floor of $/M input, min 1), snapshot at selection.
  Two consequences drove most of the work: the ledger's add-only load hook
  merges the dynamic tables into every org doc (a deselected favorite keeps
  its row — nodes on it keep their seat price), and the MCP hire/switch
  cards grow their `tier` enum at `tools/list` time. Letter and colour are
  CANONICAL from the model id (vendor hue, price-band lightness, OKLCH) and
  ride the providers payload; the frontend injects a generated `<style>`
  block with the same selector shapes the static tiers use, so no render
  site learned a colour prop. The vendor hues are BRAND-SOURCED where a
  first-party value exists (`_VENDOR_HUE` in openrouter.py says which, and
  why the six near-identical brand blues are spread in brand order around
  Google's lane); a DARK vendor (xAI black, MiniMax navy, Z.AI grey) is
  rendered filled, and the payload's `accent` is the rim that keeps three
  dark vendors three chips. `frontend/tests/palette_probe.py --shot x.png`
  renders the whole palette beside the brand swatches — look at it before
  touching the table.
- **Cost:** the CLI prices an `anthropic/…` id at list (`costBasis:
  "list"`), which is the gateway's pass-through rate — kept. Any other
  vendor comes back `costBasis: "unknown"` and an order of magnitude wrong
  (measured), so `_after_turn` reprices those from the result's usage at the
  favorite's snapshot prices.
- **Namespaces:** `identity_in_env` answers the `openrouter` sentinel (never
  the primary login) and the cache namespace is `openrouter-key:<digest>` on
  the `api_key` lane with a measured 5-minute TTL (`SUPPORTED_LANES`).
- **Gate:** keyed ⇒ headless may hire; kiosks hold it out like codex/antigravity;
  a deselected favorite is refused at the door but survives plain rehire.
- **Not done:** a native harness for third-party models (an ACP one —
  OpenCode, Goose — needs an ACP client of its own; none is in the tree
  since D-231), the Codex lane as an
  alternative engine, account pooling, per-org key override, spend caps
  beyond the key's own.
