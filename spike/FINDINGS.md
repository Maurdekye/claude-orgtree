# v0 spike findings — 2026-07-29

All six load-bearing unknowns from §13 v0 resolved. **Every one PASSED or produced a working
recipe.** Raw evidence in `results/` (event logs + reports); harness in `run.py`, `e2.py`, `e3.py`.
Environment: Claude Code **2.1.31** (⚠️ corrected 2026-07-29 — "2.1.220" came from the design
chat's report and was wrong; `claude --version` says 2.1.31; every flag was still verified
against this build's own `--help`), Windows 11, `claude.CMD` via npm. Spike session:
`ef759b1a-8f75-4ee9-9e70-df941c0bcc15` (haiku, cwd `spike/`).

## Verdicts

| # | Question | Verdict |
|---|---|---|
| №18 | `/compact` programmatically? | ✅ **YES.** Sent as a plain stream-json user turn on a resumed session → real compaction: `system/compact_boundary` event emitted, `compact_boundary` record (with `compactMetadata`) written to the transcript. §8's lineage design works exactly as drafted. |
| §6.4 | Turn injection over stream-json? | ✅ **YES.** Multiple sequential user turns into ONE `-p --input-format stream-json` process; each turn yields a full `result` event. Also works when resuming (`--resume` + stream-json). |
| №16 | `--resume` with changed `--model`? | ✅ **YES.** 3 haiku turns → resumed with sonnet → transcript's next assistant message is `claude-sonnet-4-5`. Knowledge bearers can be consulted at haiku price regardless of original tier. |
| №29 | `--append-system-prompt` on `--resume`? | ✅ **YES.** A marker instruction appended at resume was obeyed in the resumed turn. The stable-identity prompt can be regenerated every resume; org-position staleness is a non-problem under resume-on-demand. |
| №5 | Headless permission semantics | ✅ **Recipe found:** `--permission-mode acceptEdits` + `--add-dir <granted dirs>`. See matrix below. |
| №24 | Usage from transcript jsonl | ✅ **Verified.** Per-assistant-message `usage` present. Last non-synthetic message: 39,934 tokens occupancy (20% of haiku's 200K); summing across turns would read 196,374 — **4.9× overcount after just 6 messages**. Never sum. |

## The permission matrix (file writes, neutral cwd)

| Mode | in-cwd | outside | outside + `--add-dir` |
|---|---|---|---|
| `default` (headless) | ⛔ denied | ⛔ | — |
| `dontAsk` | ⛔ denied | ⛔ | ⛔ **denied even in added dir** |
| `acceptEdits` | ✅ written | ⛔ denied | ✅ **written** |
| `delegate` (stream-json) | ⛔ denied, **no control_request traffic** | — | — |

- **`dontAsk` is a lockdown mode** — auto-denies everything that would prompt. The plan's original
  №5 default ("dontAsk below root") would have produced nodes unable to write a single file.
- **`delegate` emits no permission events over stream-json** — headless it behaves as deny. Not a
  permission-escalation channel. (Purpose apparently tied to in-harness delegation UIs.)
- **`acceptEdits` + `--add-dir` is exactly "autonomy within dirs"** (№5 ruling): writes allowed in
  cwd + granted dirs, denied outside. Bash writes inside scope are auto-approved (`echo > file`
  ran), so nodes can run builds; safe read commands are auto-approved everywhere (current CLI
  auto-allows recognizably-safe commands even in `default`).

## Additional findings (not in the original six)

1. **Live occupancy is free for managed processes:** every `result` event carries `usage`,
   `modelUsage`, and `contextWindow` — the watcher needs no transcript parsing while a stream-json
   process is attached. Transcript parsing is the resume-on-demand path (№24), and both agree.
2. **Cache writes here default to the 1-hour tier** (`cache_creation.ephemeral_1h_input_tokens`
   38,822 on turn 1; turn 2 read it all back). №33's cold-write worry is smaller than modeled —
   measure, but likely fine.
3. **Child sessions inherit the user's global settings and hooks.** The chatq SessionStart/End
   hooks fired inside spike sessions (deregister failed under `cmd /c` env: `$HOME` unset there).
   Nodes must be launched with `--settings` (and `--strict-mcp-config`) to suppress inherited
   hooks/servers, or every node registers itself in chatq and runs the user's hook stack.
4. **`~/.claude` is a protected path** — Write tool calls into it are refused as "sensitive file"
   in every mode tested. Node scratch/work dirs must live OUTSIDE `~/.claude` (the planned
   `~/orgtree/scratch/<name>` layout is fine). The orgtree *code* living at `~/.claude/orgtree` is
   unaffected (the supervisor writes with ordinary OS io, not Claude tools).
5. **Variadic flags (`--allowedTools`, `--add-dir`, …) swallow a following positional prompt** —
   the CLI then errors "Input must be provided through stdin". The supervisor must always deliver
   the prompt via stdin (stream-json), never as a positional arg after variadic flags.
5b. **Never launch through `cmd /c` with a multiline argument** (found 2026-07-29 the hard way):
   cmd truncates the command line at an embedded newline, silently mangling every flag after it —
   a multiline `--append-system-prompt` cost the node its `--settings` (hooks ran) and its
   `--session-id` (claude minted a fresh one). Invoke `node cli.js …` directly; CreateProcess
   passes newlines inside quoted args intact.
6. **Model aliases drift:** `--model sonnet` resolved to `claude-sonnet-4-5`, not Sonnet 5. The
   ledger's `models` map must use full ids (as §5 already specifies).
7. **Compaction inserts a `<synthetic>` assistant message with zero usage** — occupancy readers
   must take the last message with real usage, skipping synthetic records.
8. Transcript path munging on Windows: `C:\Users\ncola_k8bx\.claude\orgtree\spike` →
   `~/.claude/projects/C--Users-ncola-k8bx--claude-orgtree-spike/<sid>.jsonl` (drive colon dropped,
   separators and dots → `-`). Locate transcripts by globbing for the session uuid.
