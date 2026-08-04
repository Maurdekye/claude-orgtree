# orgtree — the complete configuration reference

Author: session 4f69f83a · 2026-08-04 · compiled from a source sweep, not from memory. Every value
below was read at the cited line. Build-pinned to `interim-authority` at the time of writing;
re-check the cited lines if the code has moved.

**How to read this.** Configuration lives at six levels. Each one is set in a different place, at a
different time, by a different person, and — the part that actually matters — **overrides or is
clamped by** the levels around it:

```
① process     environment variables       set by whoever launches the backend
② global      defaults.json               set once from the root page, applies to FUTURE orgs
③ org         org doc settings            per organization, editable any time
④ ceiling     kiosk / sandbox             clamps everything below it, admin-only
⑤ defaults    org-level agent defaults    what a new hire is born with
⑥ agent       NodeScope (the ⚙ panel)     per seat, clamped against ④ and the parent chain
```

⚠ **The two hard rules of the hierarchy**, both live in the ledger rather than the UI:

- **A child can never exceed its parent.** `set_scope` and `hire` clamp against the parent chain,
  so a subordinate's grants are always a subset of its superior's.
- **A kiosk ceiling clamps everything, silently.** It does not 403 — it narrows the request and
  proceeds (ceiling spec §2). An agent retooling inside a kiosk gets what the ceiling allows, not
  what it asked for and not an error.

---

## ① Process level — environment variables

Set before the backend starts. Not visible in the UI, not per-org. A change requires a restart.

### Core

| variable | default | what it does |
|---|---|---|
| `ORGTREE_DATA` | `~/orgtree` | the data root: org docs, workspaces, scratch, sandboxes (`store.py:26`) |
| `ORGTREE_PORT` | `7360` | admin API + UI, bound to loopback unless exposed below (`api.py:368`) |
| `ORGTREE_PUBLIC_PORT` | `0` (off) | the PublicGateway listener for kiosk `/k/<token>` URLs (`api.py:369`) |
| `ORGTREE_PUBLIC_ORIGIN` | — | external origin advertised in kiosk links (`api.py:370`) |
| `ORGTREE_CLAUDE` / `ORGTREE_CLAUDE_CLI` | auto-detected | path to the Claude Code CLI (`supervisor.py:167,174`) |

### Turn behaviour

| variable | default | what it does |
|---|---|---|
| `ORGTREE_MAX_TURNS` | `16` | concurrent turn slots, **global not per-org**; ~306 MB per turn (`supervisor.py:243`, D-49) |
| `ORGTREE_TURN_TIMEOUT` | `14400` s | absolute per-message ceiling, re-based at each result event — a backstop, not the bound that normally fires (`supervisor.py`, reshaped 2026-08-04) |
| `ORGTREE_TURN_IDLE` | `600` s | the idle watchdog: kill only after this long with ZERO CLI stdout events — distinguishes "wedged" from "working" (`supervisor.py`) |
| `ORGTREE_COMPACT_TIMEOUT` | `600` s | the compaction fork's own bound (`supervisor.py`) — a big context can legitimately need longer |
| `ORGTREE_COMPACT_AT` | `0.80` | context fraction that triggers compaction (`supervisor.py:144`) |
| `ORGTREE_ORACLE_AT` | `0.92` | context fraction for the §8.3 state 2→3 transition (`supervisor.py:145`) |
| `ORGTREE_CONTEXT_WINDOWS` | `{}` | JSON override of per-model context sizes (`supervisor.py:153`) |
| `ORGTREE_STEER_HOOK` | on | `0` disables the PostToolUse steer hook (`supervisor.py:930,959`) |

### Sandbox

| variable | default | what it does |
|---|---|---|
| `ORGTREE_SANDBOX_IMAGE` | `orgtree-sandbox` | container image tag (`sandbox.py:52`) |
| `ORGTREE_SANDBOX_MEM` | `4g` | container memory (`sandbox.py:58`) |
| `ORGTREE_SANDBOX_CPUS` | `2` | container CPUs (`sandbox.py:59`) |
| `ORGTREE_SANDBOX_TMP` | `1g` | `/tmp` tmpfs, counts against memory (`sandbox.py:77`) |
| `ORGTREE_SANDBOX_RUN` | `64m` | `/run` tmpfs (`sandbox.py:78`) |
| `ORGTREE_SANDBOX_DISK_MB` | `20480` | virtual-disk size when the org does not specify (`sandbox.py:81`) |
| `ORGTREE_BRIDGE_PORT` | `7362` | the BridgeGateway — the one door out of a container (`sandbox.py:57`) |
| `ORGTREE_SANDBOX_API_KEY` | — | ⚠ escape hatch: a literal API key instead of the proxied subscription (`sandbox.py:306,453`) |
| `ORGTREE_SANDBOX_MCP` | off | EXPERIMENTAL — allow MCP servers inside a sandbox (`supervisor.py:479`) |

### Retired / legacy

`ORGTREE_KIOSK`, `ORGTREE_KIOSK_CREDITS`, `ORGTREE_KIOSK_SPEND_LIMIT` (`api.py:345-354`) — retired
in favour of per-org kiosk config; a legacy value is migrated once at startup and then ignored.

### Exposing the admin port

| variable | default | what it does |
|---|---|---|
| `ORGTREE_EXPOSE_ADMIN` | unset (loopback) | ☠ binds the admin API to `0.0.0.0` (`api.py:3004`). Truthy values: `1`, `true`, `yes`, `on`. |

☠ **The admin API has no password, no token and no login** — "you can reach 127.0.0.1" has always
been the whole credential. Anyone who reaches an exposed port controls every org and can make agents
run commands on the machine. VPN or SSH tunnel only; for public access use a kiosk instead.

Both deploy scripts keep a convenience switch (`-ExposeAdmin` / `--expose-admin`) that sets the
variable for that launch. A service definition sets the variable directly and needs no switch —
which is why it moved here from argv (user ruling 2026-08-04, superseding D-39).

⚠ It is **stripped from every agent's environment** by `clean_env()` (`supervisor.py:406`): env vars
are inherited by child processes, and whether the host is reachable off loopback is not an agent's
business.

### Set by orgtree, not by you

`ORGTREE_ORG`, `ORGTREE_NODE`, `ORGTREE_BASE`, `ORGTREE_BRIDGE_SECRET` are injected into each agent
process so its MCP server knows who it is (`supervisor.py:1179-1181`, `mcptool.py:22-28`).
`ORGTREE_EXTERN_ID` pins an external session's peer identity (`externtool.py:41`).

---

## ② Global defaults — `<data>/defaults.json`

Edited from the root page. **Applies to newly created orgs only** — changing it never touches an
existing org (`api.py:534-539`). Stored org-doc-shaped, so any org-level key below is legal here.

Shipped baseline (`api.py:770-774`):

| key | default |
|---|---|
| `max_top_grant` | `1000` |
| `default_top_grant` | `50` |
| `compact_at` | `0.80` |
| `fable_limit_policy` | `halt` |
| `fable_filter_policy` | `halt` |
| `cascade_hire` | `true` |
| `cascade_alloc` | `true` |
| `auto_resume` | `false` |

---

## ③ Org level — the settings panel (`POST /api/orgs/{slug}/settings`)

Editable at any time; takes effect immediately unless noted. Model at `api.py:751-764`.

| setting | type | meaning |
|---|---|---|
| `org_dirs` | `[{path, mode}]` | folder holdings. The workspace is permanent. **Additions apply to future hires; removals revoke everywhere; rw→ro downgrades propagate to every existing grant.** |
| `max_top_grant` | int | ceiling on any single top-level agent's credit grant |
| `default_top_grant` | int | pre-filled grant when hiring at top level |
| `compact_at` | int 50–95 (%) | per-org override of the compaction threshold |
| `fable_limit_policy` | `halt` \| `opus` \| `dissolve` | what happens when the weekly Fable limit is hit |
| `fable_filter_policy` | `halt` \| `opus` | what happens when a content filter flags a message |
| `clear_fable_lock` | bool (action) | clears an active Fable lock |
| `auto_resume` | bool | restart usage-limit-frozen agents 1 min after the reported reset (`supervisor.py:2527`) |
| `cascade_hire` | bool | a hire's cost bubbles up the chain (§4.6) |
| `cascade_alloc` | bool | allocations and upgrades bubble up |

Also on the org doc but not in that panel: `max_depth` (default **10**) and `max_children` (default
**256**) — runaway insurance, read with defaults at `ledger.py:1350,1358`.

### Org-wide content

| what | where | effect |
|---|---|---|
| **org.md** | `PUT /api/orgs/{slug}/orgmd` | the workspace `CLAUDE.md` — injected into every agent holding the workspace (`api.py:1748`) |
| **charter presets** | `docs/charters/*.md` | each file is a selectable preset at hire time (`api.py:1141`) |

---

## ④ Ceilings — kiosk and sandbox

Set **at creation** (`OrgCreate.kiosk`, `api.py:464-471`) and administered afterwards from the
dashboard. This is the only level that clamps rather than configures.

### Kiosk (`KioskSpec`, `api.py:448-461`)

| field | default | meaning |
|---|---|---|
| `credits` | `30` | cap on total top-level holdings |
| `spend_limit` | `50.0` | hard USD limit |
| `storage_limit_mb` | `4096` | sandboxed: the **disk size** (4096 MB floor); unsandboxed: a loose workspace+scratch cap |
| `sandbox` | `true` | run turns in a container |
| `max_scope` | permissive | the **permission ceiling** — visible and editable at creation so narrowing it is a conscious act |
| `auto_raise` | `false` | an admin's over-ceiling grant raises the ceiling instead of being clamped |

⚠ Auth is **not** configurable for a kiosk (user ruling): every sandbox uses the proxied
subscription — the host attaches the token and the container never sees a credential.

⚠ A kiosk org is **sealed from the outside world** in both directions — no chatq, no inter-org mail,
not listed to outsiders (`ledger.py:809-811,913`, `supervisor.py:2473`). The refusal is deliberately
indistinguishable from "no such org" so the kiosk roster cannot be enumerated.

### Sandbox on a non-kiosk org

`sandbox: bool` + `disk_mb` (≥ 4096) at creation (`api.py:469-471`), or enabled later. One capped
ext4 image holds everything persistent; ENOSPC is the enforcement. Soft alert at 90 %, persistent
alert at 99 %.

---

## ⑤ Agent defaults — what a new hire is born with

Org-doc keys (`schema.py:239-243`) that supply a hire's starting configuration. The user hires from
these; ⚠ **an agent hiring must state every one explicitly** — no defaults apply to an agent actor
(`ledger.py:1302-1306`).

| key | values | meaning |
|---|---|---|
| `default_tools` | `{bash, web, edit, subagents, mcp: []\|["*"]}` | the tool switches a new hire gets. `["*"]` means every registered server, present and future |
| `default_visibility` | `self` \| `team` \| `subtree` \| `full` | how much of the org chart a hire can see |
| `default_effort` | `""` (CLI default) \| `low`…`max` | thinking effort; resolved **live** at turn start, so changing it moves existing agents too |
| `permission_mode` | `acceptEdits` (default) | the CLI permission mode |
| `tiers` / `models` | `fable 10, opus 5, sonnet 3, haiku 1` | credit cost per tier and the model each maps to (`ledger.py:38-41`) |

MCP servers are discovered from the user's own `~/.claude.json` → `mcpServers`
(`supervisor.py:466-472`), so orgtree grants from that list rather than defining servers itself.

---

## ⑥ Per-agent — `NodeScope`, the ⚙ panel

Per seat, set with `set_scope`, clamped against the parent chain **and** the kiosk ceiling
(`schema.py:55-65`).

| field | values |
|---|---|
| `permission_mode` | CLI permission mode for this agent |
| `add_dirs` | `[{path, mode}]` — extra folder grants, a subset of the parent's |
| `tools` | `{bash, web, edit, subagents, mcp[]}` |
| `org_visibility` | `self` \| `team` \| `subtree` \| `full` |
| `effort` | `low` \| `medium` \| `high` \| `xhigh` \| `max`; **absent = the CLI default**, `""` clears the key |

Set at hire time instead: `tier` (model), `grant` (credits), `name`, `charter`. The **charter** is
the one role statement and is injected into every turn — editable later via retool
(`ledger.py:1295-1306`).

Resolution order for effort: node `scope.effort` → org `default_effort` → `Org.DEFAULT_EFFORT`
(`high`, `ledger.py:2185`). It is never empty at the CLI — every turn passes a flag.

Runtime state that looks like configuration but is not: `frozen`, `limit_locked`, `bearer_state`,
`cost_usd`, `occupancy`, `context_window`, `last_status`, `turns` (`schema.py:132-146`). These are
bookkeeping the supervisor writes; nothing reads them as settings.

---

## ⑦ Mailserver — PROPOSED, NOT BUILT

Full design at `docs/mailserver-spec.md`; listed here so the configuration picture is complete. None
of it exists yet.

| level | setting | ruling |
|---|---|---|
| org (creation) | mailserver addresses | remote hubs typed in explicitly |
| org (creation) | **connect to the local mailserver** | a checkbox, **checked by default**, in the `advanced` disclosure beside the address fields |
| org | identity secret | self-issued at org creation, `secrets.token_hex(16)`; never enters an agent's context |
| org | network slug | `<org>.<username>.<sha256(secret)[:6]}`, **immutable for the org's lifetime** |
| org | `net_wake` | `auto` only in v1 — pending mail drives the org at startup |
| org | accept policy | `open` by default (closed network) |
| org | `headless` | ⚠ **requires an API key**; user-bound requests auto-denied |
| hub | one hub in v1 | but stored as a list and keyed by hub id so several stay possible |

---

## Browser-local state (not configuration, but it looks like it)

Kept in `localStorage`, per browser, never synced and never in the org doc: inbox-seen watermark
and card-pile layout per org (`OrgCanvas.tsx:57,88`), disk-browser mode (`DiskBrowser.tsx:52`). A
different browser or a cleared profile starts fresh — that is intended, not a bug.

---

## Where each level is edited

| level | surface |
|---|---|
| ① process | the shell / service definition that launches the backend; restart required |
| ② global defaults | root page → defaults |
| ③ org settings | the eye → gear panel |
| ④ kiosk / sandbox | org creation form → `advanced`; afterwards the kiosk dashboard |
| ⑤ agent defaults | org settings (they are org-doc keys) |
| ⑥ per agent | the node's ⚙ panel, or `orgtree_retool` from an agent |
