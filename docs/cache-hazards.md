# Cache and spawn hazards — things that break the prompt cache or silently don't apply

Facts found the hard way on 2026-08-30 (D-206 work). Each is stated as the
mistake the next person would otherwise make. Companion to
`cache-economics.md`.

The current proof classes, lane-derived TTLs, migration, and pre-turn policy
are defined in [`cache-continuity.md`](cache-continuity.md). In particular,
local process restart and provider cache rejection are separate events.

## Changing spawn env does NOT respawn agents — a restart is required

The warm pool's identity hash covers the rendered identity prompt, the
normalized argv, and the resolved credential identity. **Plain environment
variables are not in it.** Change what `spawn_env` injects (a new
`CLAUDE_CODE_*` flag, say) and every parked CLI process keeps running WITHOUT
it until something else respawns it — you will conclude the flag does not
work, and you will be wrong. Ship env changes with a deploy restart, which
kills every parked process anyway.

**The one exception:** per-node overrides from `<ORGTREE_DATA>/env-overrides.json`
(`{"<slug>/<nid>": {"VAR": "value"}}`) ARE hashed into the identity, so
editing that file respawns the affected agent by itself. That file is the
right way to trial a CLI flag on one agent. Credential names
(`ANTHROPIC_*`, `CLAUDE_CODE_OAUTH_TOKEN`) are refused in overrides — the
billing lane belongs to `spawn_env` alone.

## Native CLAUDE.md is a startup input, even though orgtree did not render it

Claude Code reads instruction files from the native working-directory chain
once at session start and holds them in the process. The agent's own scratch
`CLAUDE.md` is **not** a folder grant, so `identity_prompt` does not render it.
Before the 2026-08-30 audit, editing that file left `ident_hash` byte-identical
and a parked process could serve stale self-instructions forever.

The prompt component of the warm identity now also fingerprints the startup
files the CLI loads: managed/user/project `CLAUDE.md` and `CLAUDE.local.md`,
project `.claude/CLAUDE.md`, unscoped `.claude/rules/**/*.md`, imported files
(five hops), and the documented startup prefix of auto-memory `MEMORY.md`.
Adding, editing, deleting or restoring one moves/restores the hash and the
keeper re-warms the process. Raw instruction text never enters telemetry.

Two deliberate exclusions matter:

* Global **skills** are watched live by the pinned CLI, so content edits do
  not belong in the warm identity. The skills directory's presence can still
  add the standing `--add-dir` argv entry; that is a real spawn-input change.
* Path-scoped rules load lazily when a matching file is read, not at startup.
  Hashing them would turn a harmless edit into a cold opening.

## The ORG CHARTER restarts the whole org, on every provider

`org.md` is stored as `<workspace>/CLAUDE.md`, but since 2026-09-04 it is not
delivered by any project-doc loader. `supervisor._org_charter_block` renders it
into `identity_prompt`, which orgtree writes itself on all three lanes
(`--append-system-prompt-file` for claude, the managed `AGENTS.md` for codex,
the plugin workspace for antigravity). So **one org-charter save moves every
agent's prompt hash and respawns every parked process in the org** — by design,
and disclosed in the editor's hint.

Why the old route was worse than it looked: it reached only agents holding the
workspace as a folder grant, on the claude lane. Most seats hold no grants at
all and codex reads `AGENTS.md`, never `CLAUDE.md` — so for most of the fleet
the field wrote a file nothing read, which from the outside is a setting that
does nothing.

Two properties worth keeping:

* The block is not loader-proof and does not claim to be. On codex and
  antigravity the managed prompt IS a file read back by that provider's own
  loader, so an `AGENTS.override.md` suppresses the org charter exactly as it
  suppresses the agent's identity. What is true is that the charter now has
  the same delivery reliability as the agent's own charter: if that breaks,
  the agent has already lost its identity and scope.
* A charter that is present but UNREADABLE renders a notice in the prompt
  rather than nothing. Silent absence is indistinguishable from an org that
  never wrote one, and that is the state in which nobody looks.

The workspace is deliberately excluded from `_claudemd_block`'s granted-folder
injection, so a workspace-holder receives the text once rather than twice.
`native_startup_context_digest` still fingerprints the file through the grant;
nothing about invalidation changed with that exclusion.

## MCP object-key order is not process identity

`~/.claude.json` preserves JSON insertion order, while MCP object-key order is
semantically meaningless. Serializing that mapping directly made a formatter
or reorder change the spawn argv and kill a valid warm process. The emitted
`--mcp-config` JSON is canonical (`sort_keys`, compact separators); arrays keep
their order, object keys do not. A server value change still moves the argv
hash, and restoring it restores the exact command and identity.

## Set-like grants need one canonical order

Directory grants are a path→mode capability map, and external response handles
are a set of addresses. Caller list order changes neither access nor mail
routing, but both lists render into cached identity (directory grants also
become spawn argv). Reversing an unchanged list used to kill a valid warm
process. Normalization now sorts directory grants by platform-normalized
path/mode and handles by address after validation and deduplication. A real
path, mode, or handle change still invalidates identity. Production frequency
and savings were not measured.

## Forced alwaysLoad was rolled back: it made turn 1 wait for MCP handshakes

The old source comment said orgtree never waits for MCP handshakes. Orgtree
adds no explicit barrier, but that is not the whole runtime truth:
`alwaysLoad` requires the CLI to have each server's tools before it builds the
first request, so a cold or too-young parked process can wait for connection
timeouts. The post-D-206 audit measured admit-to-first-user at **5.084s** and
**7.270s** for cold multi-MCP processes and **7.001s** for a 2.4-second-young
prewarm, versus **0.039s** for a long-warm orgtree-only control. Popen itself
took only **203–375ms**.

The fleet-wide override was rolled back. It had a measured user-visible cost,
conflicted with the earlier no-first-request-handshake-wait ruling, and still
had no successful post-deploy Claude sample demonstrating a cache benefit.
Individual registry entries may opt in to `alwaysLoad`; orgtree preserves that
choice but no longer adds it to every server. A future cache experiment may
reconsider a narrower opt-in, but it must not silently restore a fleet-wide
turn barrier.

## Capability guidance must not depend on whether a report exists today

D-181 moved the live roster out of the cached system prompt, but five guidance
branches still checked child count: a manager's own set team charter; inspect,
retire, and cheap-compact guidance; and archived-agent guidance. The first-ever
hire therefore changed the parent's prompt/hash even though the fixed scratch
root kept its argv stable. Last retirement moved it again.

Render capability guidance before it becomes immediately useful. A real
0→1→2→1→0 ledger cycle must keep the parent's prompt, normalized argv, four
component digests, and combined identity byte-identical. Actual role or team
charter content changes still move the prompt component; merely adding or
removing a report does not.

## CLAUDE_CODE_IS_COWORK is ON fleet-wide (cache-break diagnoser) — and it disables skills' inline shell preprocessing

Since D-206 every unsandboxed claude spawn carries `CLAUDE_CODE_IS_COWORK=1`.
It gates the CLI's own prompt-cache-break diagnoser: per-request diffs with
named causes emitted as `[PROMPT CACHE BREAK] …` warning lines. Orgtree copies
ONLY those exact sentinel lines (never general stderr) from both warm and cold
CLI processes into `journals/warm.jsonl`, with session, pid and timestamp join
keys. The row's `at` is backend collection time, not an API request timestamp;
exact attribution uses session/order plus the raw warning's call/read/create
tuple. The warning does not carry a request ID. Enumerated side effects on the
pinned CLI 2.1.220: eager transcript flush at turn boundaries, OTel diag level
WARN, telemetry labels — and one real behaviour change:

### ⚠ D-206 ALONE EMITTED NOTHING. IT TOOK D-211 TO TURN THIS ON

`CLAUDE_CODE_IS_COWORK` gates the diagnoser's cross-process **state** and its
telemetry. **It does not gate the emission.** For a full day the fleet ran
with D-206 on, `warm.jsonl` recorded zero cache-break rows over its entire
history, and that zero was read as "no breaks" when it only ever meant "no
instrument". A flag that enables a subsystem's state without enabling its
output looks identical to a working feature from the outside.

The gate was first read from the unused PATH **2.1.241** binary, then verified
in-process against the production-spawned **2.1.220** binary. The sentinel is
written by the CLI's debug FILE logger as `E(line, {level:"warn"})`, and that
logger drops everything unless

* debug mode is on — env `DEBUG` / `DEBUG_SDK`, or argv `--debug` / `-d` /
  `--debug-to-stderr` / `-d2e` / `--debug-file`. Absent all of these,
  `shouldLog()` returns false for anyone who is not an Anthropic-internal
  `isAnt` build and the line is written **nowhere at all**; and
* for STDERR specifically, argv carries `--debug-to-stderr` / `-d2e`.
  Otherwise it goes to `~/.claude/debug/<session_id>.txt`.

warmpool reads stderr and only stderr, so D-206 alone could not have produced
a row. D-211 adds both halves: `--debug-to-stderr` on the turn spawn, plus
`CLAUDE_CODE_DEBUG_LOG_LEVEL=warn` in `spawn_env` to cap the volume (measured
on a forced, genuinely reportable break: **2 lines / 270 bytes of stderr per
turn with the sentinel present, versus 187 lines / ~20 KB uncapped**, which
would otherwise flood `WarmProc.err_tail`, a 200-entry deque, with debug noise
and evict real errors). The capture itself needed no change — it was correct
the whole time, which a positive control proved before anything was edited.

`backend/tests/test_d211_cache_break_emission.py` is the standing gate: it
drives the REAL `_build_cmd` argv and REAL `spawn_env` through the REAL
capture and asserts a row lands, and goes red if either half is removed.

### What the diagnoser will and will not tell you

These bound every number this instrument can ever produce (verified against
the production 2.1.220 binary, D-211):

* **haiku turns are excluded outright** — the reporter returns early when the
  model name contains `haiku`.
* **breaks under a 2000-token drop are invisible** — it reports only when the
  new cache read is under 95% of the previous one AND the drop is ≥ 2000.
* **the first call of a session never reports** — there is no baseline yet.
* **only these query sources are tracked**: `repl_main_thread`, `sdk`,
  `agent:custom`, `agent:default`, `agent:builtin`. Orgtree's headless turns
  report as `sdk` (confirmed live).
* the cause vocabulary distinguishes named input changes (`system prompt
  changed (+N chars)`, `tools changed`, `betas changed`, `effort changed`,
  `message history mutated at index N`, …) from `possible 5min/1h TTL expiry
  (prompt unchanged)` and `likely server-side (prompt unchanged, <5min gap)`.
  That last distinction is the point: it separates our request surface
  changing from a server-side miss on byte-stable input.

**Do not look under `~/.claude` for its state file.** In 2.1.220 the exact path
is `%TEMP%\claude\cache-break-state-${session_id}.json`
(`Lie()` → `Iw()` → `path.join(os.tmpdir(), "claude")`). The deployed CLI does
write those files, but almost every observed stream-json file ends a turn as
the two bytes `{}`. Useful state surviving a respawn is therefore **not
established**; the journaled warning line is the observation path. This
corrects D-206's initial, too-strong claim that the file itself made openings
across respawns attributable.

⚠ **Production 2.1.220 does not hydrate this state across processes.** It
writes the state file but contains none of 2.1.241's hydration path
(`baselineFromDisk`, `hydrationAttempted`, or `previousStateBySource`). A
cross-process resume therefore starts without a baseline and cannot prove the
emission path. The observed empty `{}` files are consistent with that older
implementation; validate the reporter inside one live process.

**⚠ Skill authors: inline shell preprocessing (`` !`command` `` blocks in
skill markdown) is DISABLED under this flag** — such blocks render as
"[shell command execution disabled by policy]" instead of executing. No
skill on this machine used the syntax when the flag went on (all three skill
roots checked, 2026-08-30). If you are writing a skill that needs dynamic
shell output, it will not work here; ask before designing around it.

Revert = revert the one commit that adds the line in `spawn_env`
(supervisor.py) — it is deliberately self-contained.

## A read-only folder grant that CONTAINS an agent's scratch kills its writes

Read-only grants generate `Write`/`Edit` deny rules over the whole granted
subtree, and deny beats allow. Granting the DATA ROOT read-only to an agent
denies it its own scratch (scratch lives inside the data root) — it can no
longer write its breadcrumbs or notes, silently. Grant the narrowest folder
that has what's needed (e.g. `journals\` rather than the data root). This
bit three agents on 2026-08-30, including the coordinator.

## An agent's own shell cannot reliably make direct `claude` API calls

An agent shelling out to the CLI inherits the PRIMARY account lane — no
key-row token is injected at that level. When the primary is capped (weekly
limit), every such call 429s for every model **while the fleet keeps
working**, because production turns route through `accounts.resolve` to
key-row accounts. If you need a real API call in a spike, build it against
the backend's spawn path (`spawn_env` with a tier) rather than a bare shell
spawn, and expect a confusing failure mode if you forget.

## Where to measure TTL lanes

The CLI result JSON reports cache writes split by TTL:
`usage.cache_creation.ephemeral_1h_input_tokens` and
`ephemeral_5m_input_tokens` (present even on error results). Subscription
main-conversation turns get the 1-hour TTL; API-key billing and usage-credit
overage silently drop to 5 minutes ("overage state changed (TTL flip
expected)" in the diagnoser's vocabulary). Any expiry analysis that assumes
one TTL for all turns is wrong.
