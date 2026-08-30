# Cache and spawn hazards — things that break the prompt cache or silently don't apply

Facts found the hard way on 2026-08-30 (D-206 work). Each is stated as the
mistake the next person would otherwise make. Companion to
`cache-economics.md`.

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

**Do not look under `~/.claude` for its state file.** In 2.1.220 the exact path
is `%TEMP%\claude\cache-break-state-${session_id}.json`
(`Lie()` → `Iw()` → `path.join(os.tmpdir(), "claude")`). The deployed CLI does
write those files, but almost every observed stream-json file ends a turn as
the two bytes `{}`. Useful state surviving a respawn is therefore **not
established**; the journaled warning line is the observation path. This
corrects D-206's initial, too-strong claim that the file itself made openings
across respawns attributable.

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
