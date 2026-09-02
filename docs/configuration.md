# orgtree — the complete configuration reference

Originally compiled from a source sweep on 2026-08-04 and revised as features
shipped. The source code is authoritative; file and line references here are
breadcrumbs, not a promise that a later refactor leaves them unchanged.

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
| `ORGTREE_DEPLOYMENT_PROFILE` | `standard` | install-wide security policy: blank/unset/`standard` preserves ordinary behavior; `frozen` selects the [frozen deployment profile](frozen-deployment.md); surrounding whitespace and case are ignored; any other value raises `DeploymentConfigError` (`deployment.py:current_policy`) |
| `ORGTREE_DATA` | `~/orgtree` | the data root: org docs, workspaces, scratch, sandboxes (`store.py:26`) |
| `ORGTREE_PORT` | `7360` | admin API + UI, bound to loopback unless exposed below (`api.py:368`) |
| `ORGTREE_PUBLIC_PORT` | `0` (off) | the PublicGateway listener for kiosk `/k/<token>` URLs (`api.py:369`) |
| `ORGTREE_PUBLIC_ORIGIN` | — | external origin advertised in kiosk links (`api.py:370`) |
| `ORGTREE_CLAUDE` / `ORGTREE_CLAUDE_CLI` | auto-detected | path to the Claude Code CLI (`supervisor.py:167,174`) |
| `ORGTREE_CODEX` | auto-detected | path to the Codex CLI; resolution is override → private install under `<data>/codex` → `PATH` |
| `CODEX_HOME` | `~/.codex` | Codex CLI home, including its own login state; orgtree passes it through and does not copy credentials |
| `ORGTREE_GEMINI` | auto-detected | path to the Gemini CLI; resolution is override → private install under `<data>/gemini` → `PATH` |
| `ORGTREE_GEMINI_HOME` | `~/.gemini` | Gemini CLI configuration and login home; useful when the CLI uses a non-default profile |

### Provider CLIs and tier availability

The installed backend supports three provider families. A tier name is global
within an org, so it already identifies its provider; do not supply a separate
provider argument to a hire or model switch.

| provider | tiers (seat credits) | available when |
|---|---|---|
| Claude Code | haiku (1), sonnet (2), opus (5), fable (10) | the Claude CLI can run turns |
| Codex | gpt-reserve (1), luna (1), terra (2), sol (5) | Codex CLI is installed and signed in, **and** the account still has usage in its current window (all four tiers share one account, so a spent account offers none of them); gpt-reserve additionally requires OpenAI's reserve grant to be live — it is withdrawn and restored per account and orgtree reads the Codex CLI's own model registry to see it |
| Gemini | flash (1), pro (2) | Gemini CLI is installed and signed in |

Provider detection is read-only. It checks the CLI installation and its own
login records, but never copies or alters credentials. The Accounts panel and
the disabled hire-chip tooltip show the next required action.

Codex and Gemini are not available in kiosk orgs while their sandbox support
is intentionally held back. In a headless org, their personal-login modes are
also unavailable: Codex requires an API-key login, while Gemini requires an
API-key or Vertex AI login. Provider tiers otherwise use the same credit,
scope, charter, and MCP-grant rules as Claude tiers.

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
| `ORGTREE_WORKING_CACHE_SUBSCRIPTION` | `3000` s | cache-read cadence while a Claude agent reports `working` on an OAuth/subscription lane (50 min, below its 1 h TTL) |
| `ORGTREE_WORKING_CACHE_API_KEY` | `240` s | cache-read cadence while a Claude agent reports `working` on an Anthropic API-key lane (4 min, below its 5 min TTL) |
| `ORGTREE_WORKING_CACHE_POLL` | `20` s | fleet sweep cadence for due reported-working cache reads |
| `ORGTREE_WORKING_CACHE_TIMEOUT` | `180` s | bound for one disposable keepalive fork; timeout kills the child and leaves agent state untouched |
| `ORGTREE_WORKING_CACHE_RETRY_BASE` | `60` s | first retry delay after a failed or limited keepalive request (never below the fleet poll cadence) |
| `ORGTREE_WORKING_CACHE_RETRY_MAX` | `1800` s | ceiling for exponential keepalive retry backoff; a successful read clears the backoff |

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
| `ORGTREE_SANDBOX_API_KEY` | — | ⚠ escape hatch: a literal API key instead of the proxied subscription (`sandbox.uses_subscription_auth` / `sandbox.container_auth`) |
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
| `auto_resume_compact` | `false` |

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
| `auto_resume` | bool | restart usage-limit-frozen agents 1 min after the reported reset (`supervisor.auto_resume_ready`) |
| `auto_resume_compact` | bool | cheap-compact a limit-frozen node right before the AUTO resume wakes it (the freeze outlived the cache TTL; manual ▶ never compacts) |
| `api_fallback` | bool | the org's `api_key` becomes a usage-limit SPARE: routine turns bill the subscription; a limit freeze **the CLI reported** opens a key-billed window that closes at the limit's own reset — a "limit" only the agent's own final answer claims opens none, and a run of those stops the node auto-waking (D-133). Needs `api_key`; mutually exclusive with `headless`; wakes limit-frozen agents immediately even with `auto_resume` off. Dollars burned inside a window accumulate on a separate lifetime counter (`api_cost_usd`) — hover the header cost chip for the subscription/api-key split (D-131). The window is priced off the limit's reset time, which is resolved (error prose → the account's usage readout → a 5-min probe floor), banded to its own lane, and bounded to 15 min … 7 d + 1 h so a mis-parsed timestamp cannot bill the key indefinitely (D-133) |
| `fable_api_fallback` | bool | off by default (D-130). ON, a TRUSTED weekly Fable-tier hit opens the *same* `api_fallback` window a normal usage limit does — no `fable_lock`, no `limit_locked` — instead of applying `fable_limit_policy`. Requires `api_fallback` already on (422 otherwise); turning `api_fallback` off clears this with it (D-143) |
| `cascade_hire` | bool | a hire's cost bubbles up the chain (§4.6) |
| `cascade_alloc` | bool | allocations and upgrades bubble up |

Also on the org doc but not in that panel: `max_depth` and `max_children`, both defaulting to
**1024**. They are runaway insurance; the authoritative defaults are
`MAX_DEPTH` and `MAX_CHILDREN` in `backend/orgtree/ledger.py`.

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

⚠ A kiosk org is **sealed from the outside world** in both directions — no hub mail, no inter-org mail,
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
| `tiers` / `models` | Claude: fable 10, opus 5, sonnet 2, haiku 1; Codex: sol 5, terra 2, gpt-reserve 1, luna 1; Gemini: pro 2, flash 1 | credit cost per tier and the model each maps to (`ledger.py:49-80`) |

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

## ⑦ Mailserver — BUILT (F-06 wave, 2026-08-05)

Design record: `docs/mailserver-spec.md` (§12 rulings) + DECISIONS.md D-097/D-098/D-099.

| level | setting | reality |
|---|---|---|
| global default | `net_hub_address` (`defaults.json`) | default `http://127.0.0.1:7370`; translated at org creation into the `"local"` hub entry |
| org (creation) | **connect to the mailserver on this computer** | checkbox in the tabbed advanced modal → Mailserver tab, checked by default, not gated on detection (a probe endpoint hints) |
| org | `net_hubs` | list of `{id, address, enabled, name?}` — `name` is **discovered on connect, never typed**; hub ids are client-minted (`"local"` / `uuid4[:8]`); per-hub runtime state is **stamped with the address it was earned against** and dies when the id is removed or the address changes |
| org | identity secret | minted at creation (`secrets.token_hex(16)`), persisted in `net_identity`, never recomputed; **kiosks mint no identity at all**. Never in payloads/logs/prompts — the ONE reveal is loopback-only `GET /api/orgs/{slug}/net` |
| org | network slug | `<org>.<username>.<sha256(secret)[:6]>`, immutable for the org's lifetime |
| org | `net_autoconnect` | default true |
| org | `net_wake` | `auto` only — inbound hub mail drives extern-audience holders |
| org | `headless` | requires an API key **both directions** (headless-without-key and clear-key-while-headless → 422); forces `auto_resume`; refused while a fable policy is `halt`; refused on kiosks. User-bound requests auto-denied with adaptive reasons; `post_mail`→user accepted with a "no reply is coming" note |
| org | `api_key` | org-level; sandbox precedence org > kiosk > env > proxied; unsandboxed orgs get it as a per-node env seam. `clean_env` strips the HOST's `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` — keyless orgs bill the subscription |
| hub | `HUB_NAME` env (compose) | the hub's operator-set name (default hostname), returned on register/poll and shown beside the address everywhere |
| hub | retention / caps | 30-day sweep · attachments ≤ 25 MB / 10 per message · auth `X-Org-Auth: <slug>:<secret>` pairs, full-fingerprint compare |
| chat clients | `~/.orgtree/hub-client.json` | `hub/hubtool.py` per-profile identity: 256-bit uid IS the secret (0600, O_EXCL-minted), name chosen once at first register, slug `<name>.<user>.<fp[:6]>`, `kind: chat` |

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
