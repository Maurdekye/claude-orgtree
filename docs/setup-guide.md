# orgtree — setup guide: the four shapes

This is task-oriented — "how do I get from a fresh PC to shape X" — for the four ways orgtree gets
run. For a knob-by-knob reference of every setting once you're up, see
[`configuration.md`](configuration.md); this guide cites it rather than repeating it. Compiled by
the Orgtree Curator from a source sweep (file:line citations throughout), not from memory —
re-check a cited line if the code has since moved.

The four shapes are additive, not exclusive: shapes ②–④ all start from shape ①'s install. Pick the
section(s) you need.

1. [Local-only — a local agent organization](#1-local-only--a-local-agent-organization)
2. [Kiosk hosting — sandboxing, limits, and exposure](#2-kiosk-hosting--sandboxing-limits-and-exposure)
3. [Mailserver — the hub, from scratch](#3-mailserver--the-hub-from-scratch)
4. [Headless — autonomous, API-key-billed, unattended](#4-headless--autonomous-api-key-billed-unattended)

---

> **Deciding how far to go:** this guide is ordered by capability, and each section adds
> infrastructure. If you would rather compare the tiers side by side first — what each one
> requires, what it unlocks, what it costs you to run — read
> [infrastructure-tiers.md](infrastructure-tiers.md) and come back for the steps.

## 0. Common prerequisites

Every shape below starts here.

| requirement | notes |
|---|---|
| **One or more provider CLIs**, installed and authenticated | Claude Code, Codex, and Antigravity are supported. Install the CLI for every provider whose tiers you plan to hire; turns use that provider's subscription or API account — **real usage costs real money**. OpenRouter needs no CLI of its own: an API key entered in App settings → Providers runs its models through Claude Code, billed to the key's prepaid credits. |
| **Python 3.11+** | |
| **Node.js 18+** | builds the frontend and runs the JavaScript-based provider CLIs where needed |
| **Windows, macOS, or Linux** | the host-mode core (ledger, turns, canvas, kiosk URLs) runs anywhere; developed and battle-tested on Windows, POSIX paths handled but less traveled |
| **Docker Desktop, WSL2 backend** | only for **sandboxed orgs** (kiosks default the sandbox on) — each org's virtual disk is loop-mounted inside the docker-desktop WSL distro, read via `\\wsl.localhost` |

### Install

The update scripts do this whole sequence for you on a fresh clone (including creating the
virtualenv) — `update.ps1` on Windows, `./update.sh` on Linux/macOS/Git Bash, step-for-step
equivalents. By hand:

```bash
git clone https://github.com/Maurdekye/claude-orgtree.git
cd claude-orgtree

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cd frontend && npm install && npm run build && cd ..

cd backend && python -m orgtree.api
```

⚠ **The venv is not decoration.** `requirements.txt` names a package nothing imports directly
(`websockets` — uvicorn loads it by name). Install into a system-wide Python shared with other
projects and its absence hides itself: a missing WebSocket library doesn't error, it answers the
upgrade request with a plain `200 OK` and the UI silently degrades to slow polling. `GET /api/host`
reports both `python.venv` and `websockets` so a deployment can be checked at a glance.

**Recommended, not required:** give agents their own private CLI install —

```bash
npm install --prefix ~/orgtree/cli @anthropic-ai/claude-code@latest
```

The supervisor auto-detects and prefers it (your global `claude` stays untouched). It enables
mid-task message delivery — an older CLI never runs tool hooks headless, so without this a message
sent to a busy agent waits for its current response to finish rather than arriving after its next
tool call.

**Optional Codex and Antigravity providers:** install and sign in to their CLIs on
the host. These private installs are discovered automatically; a global install
on `PATH` works too.

A deploy holds the Codex CLI at or above `backend/orgtree/codexpin.py`'s `PIN`,
so the install below is a one-off. **Upgrading by hand needs an explicit
version** — `npm install --prefix ~/orgtree/codex @openai/codex@<version>
--save-exact` — because the bare form writes a caret range and a caret on a
`0.x` version permits patch updates only, so re-running it reports success
without changing anything. OpenAI picks which models to offer from the CLI
version, so a stale pin silently hides newer tiers.

```bash
# Codex: gpt-reserve (seat 0.2), luna (0.2), terra (2), sol (5)
npm install --prefix ~/orgtree/codex @openai/codex
npx --prefix ~/orgtree/codex codex login

# Antigravity: flash (seat 1), pro (2) — Google's own installer, then sign in once
winget install Google.AntigravityCLI                          # Windows
curl -fsSL https://antigravity.google/cli/install.sh | bash   # macOS / Linux
agy
```

The first Antigravity launch signs you in with your Google account. The
Accounts panel then confirms each provider's connection state. See
[`configuration.md`](configuration.md#provider-clis-and-tier-availability)
for CLI overrides, headless authentication requirements, and the current
kiosk limitation.

**Optional OpenRouter provider (no CLI):** open App settings → Providers,
paste an OpenRouter API key into the OpenRouter row, then click the row of
model cards under it to pick which catalog models can be hired. Each picked
model becomes a chip on the canvas (seat = its $/M input price — whole at or
above $1/M, fractional below it, never under 0.1)
and runs through Claude Code against openrouter.ai, billed to the key's
prepaid credits. Anthropic models run as first-class; other vendors are
best-effort (Claude Code's tool scaffolding is tuned for Claude).

**Updating, once installed:** `update.ps1` (or double-click `update.cmd`) on Windows, `./update.sh`
on Linux/macOS (also runs under Git Bash on Windows). Both pull, rebuild the UI, install any new
dependencies, and restart the backend in the background with a health check.

---

## 1. Local-only — a local agent organization

Nothing here is a separate mode to turn on — it's what you get by doing *only* the install above
and none of the sections that follow. It is a persistent, visual organization
of Claude Code, Codex, and Antigravity agents running through the provider CLIs
installed on this host (`README.md`).

1. Start the backend (`python -m orgtree.api`, or `update.ps1`/`update.sh` if already installed)
   and open **http://127.0.0.1:7360**.
2. Create an organization — the collapsed creation form is just a name and a folder grant; leave
   the `advanced` disclosure closed (that's where kiosk, sandbox, and the mailserver connection
   live — §2–§4 below).
3. Hover the eye and hire one top-level agent. Claude provides
   haiku/sonnet/opus/fable; a connected Codex CLI adds gpt-reserve/luna/terra/sol, a
   connected Antigravity CLI adds flash/pro, and an OpenRouter key adds one chip per
   model you picked. That single card, zoomed in, is a chat
   desk with transcript, live tool feed, markdown, and a composer.

That's the whole shape: one persistent, revisitable session instead of a terminal you close. You
can still hire more agents, build out a tree, etc. — "nothing more" describes what's *off* by
default, not a hard ceiling: no kiosk (org is private to you), no sandbox (agents run with your
real host access, same as plain Claude Code would), not headless (you're the one answering it), not
connected to any mailserver (§3) unless you opt in. Every section below is something you turn on;
none of it is on by default.

**Note on the SessionStart-hook pattern in §3:** `externtool.py` (below) has had a `listen` mode
since FR-08 shipped (`feature-docket.md`), the same shape as `hubtool.py`'s — but unlike the hub,
it has no `SessionStart` hook of its own yet arming it automatically. `hub/session-start.sh` (§3)
is the current, real example of the pattern; nothing analogous exists for a plain local-org
connection today.

The full interaction manual — every gesture, badge, and panel — is
[`docs/ui-guide.md`](ui-guide.md).

### Optional: an independent Claude Code chat talking to *this* instance

A separate Claude Code session — not itself part of any org — can message an org's inbox directly,
with no mail hub required, via `backend/orgtree/externtool.py`, "**the orgtree MCP server**".
This is the zero-setup path: it exists precisely so nobody has to stand up a mailserver just to
let their chats talk to their orgs. What it does **not** give you is chat-to-chat contact or
anything off this machine — that is what the hub in §3 adds.

```
claude mcp add orgtree-extern -- python <repo>\backend\orgtree\externtool.py
```

On first use the session mints a persistent peer identity `@mcp:<id>`, stored in
`~/.orgtree/extern-id` (override with `ORGTREE_EXTERN_ID`). It gets three verbs: send a message to
an org's inbox, read what the org has sent back, and wait (long-poll) for a reply — a full
question-and-answer loop. It talks to `ORGTREE_BASE` (default `http://127.0.0.1:{ORGTREE_PORT}`,
i.e. `:7360`) — point `ORGTREE_PORT`/`ORGTREE_BASE` at a remote instance if the chat isn't running
on the same machine as orgtree itself (`externtool.py:31-32`).

This is the same shape as connecting a chat to the mailserver hub (§3's `hubtool.py`), one level
narrower in scope: `externtool.py` reaches one specific local instance; `hubtool.py` reaches
whatever's registered on a hub.

---

## 2. Kiosk hosting — sandboxing, limits, and exposure

### What a kiosk is

An org exposed through a **preauthenticated secret URL** on a *separate* public listener — the
admin app itself never leaves `127.0.0.1` (`README.md`). A kiosk is **sealed from the outside world
in both directions** — no hub mail, no inter-org mail — and the refusal is deliberately
indistinguishable from "no such org," so the kiosk roster can't be enumerated by probing
(`ledger.py:809-811,913`; `configuration.md` §④).

### Creating one

Set at creation (`OrgCreate.kiosk`, `api.py:448-471`) via the creation form's `advanced` disclosure,
administered afterwards from the kiosk dashboard:

| field | default | meaning |
|---|---|---|
| `credits` | `30` | cap on total top-level holdings |
| `spend_limit` | `50.0` | hard USD limit |
| `storage_limit_mb` | `4096` | the sandbox disk size (4096 MB floor) |
| `sandbox` | `true` | run turns in a container |
| `max_scope` | permissive | the permission ceiling — visible and editable at creation, so narrowing it is a conscious act |
| `auto_raise` | `false` | an admin's over-ceiling grant raises the ceiling instead of being clamped |

⚠ **A kiosk ceiling clamps everything silently — it does not refuse.** An agent retooling inside a
kiosk gets what the ceiling allows, not what it asked for and not an error (`configuration.md`, top).

⚠ **Auth is not configurable for a kiosk.** Every kiosk sandbox uses the proxied subscription — the
host attaches your token, the container never sees a credential. There is no API-key option for a
kiosk specifically (contrast with a non-kiosk sandboxed org, which can take an org-level `api_key` —
see §4).

### Sandboxing — requirements and limitations

Requires **Docker Desktop with the WSL2 backend** on Windows (see §0). One container per org; one
capped ext4 virtual disk holds everything persistent, enforced by `ENOSPC` itself — a soft alert at
90% full, a persistent one at 99% (`configuration.md` §④).

| env var | default | what it does |
|---|---|---|
| `ORGTREE_SANDBOX_IMAGE` | `orgtree-sandbox` | container image tag (`sandbox.py:52`) |
| `ORGTREE_SANDBOX_MEM` | `4g` | container memory (`sandbox.py:58`) |
| `ORGTREE_SANDBOX_CPUS` | `2` | container CPUs (`sandbox.py:59`) |
| `ORGTREE_SANDBOX_TMP` | `1g` | `/tmp` tmpfs, counts against memory (`sandbox.py:77`) |
| `ORGTREE_SANDBOX_RUN` | `64m` | `/run` tmpfs (`sandbox.py:78`) |
| `ORGTREE_SANDBOX_DISK_MB` | `20480` | virtual-disk size when the org doesn't specify one (`sandbox.py:81`) |
| `ORGTREE_BRIDGE_PORT` | `7362` | the BridgeGateway — the *one* door out of a container (`sandbox.py:57`) |
| `ORGTREE_SANDBOX_MCP` | off | EXPERIMENTAL — allow MCP servers inside a sandbox (`supervisor.py:479`) |

**What this does and doesn't protect against:** the container isolates the filesystem (the ext4
virtual disk is the *only* persistent storage a sandboxed agent can reach) and network egress runs
through the single BridgeGateway door. It is not a claim about model behavior or prompt-injection
resistance — it bounds *blast radius on the host*, not what the agent might be tricked into doing
within its own sandbox.

### Exposure

Two entirely separate mechanisms — do not confuse them:

| | admin exposure | kiosk exposure |
|---|---|---|
| what it exposes | **everything** — every org, full control | one org, through a token |
| auth | ☠ **none at all** — reaching the port *is* the credential | the secret token in the URL |
| variable | `ORGTREE_EXPOSE_ADMIN` (truthy: `1`/`true`/`yes`/`on`) | `ORGTREE_PUBLIC_PORT` |
| safe for | a VPN or SSH tunnel to yourself, never the open internet | sharing with someone outside |

`ORGTREE_EXPOSE_ADMIN` binds the admin API to `0.0.0.0` (`api.py:3004`) — anyone who reaches it
controls every org and can make agents run commands on the machine. It's **command-line-only /
environment-only by design** (D-087, superseding an earlier argv-only ruling, D-39): a service
definition can set the variable directly; nothing an agent could write — no org setting, no doc key
— can turn it on, and `clean_env()` strips it from every agent's own environment regardless
(`supervisor.py:406`). Both deploy scripts keep a convenience switch (`-ExposeAdmin` /
`--expose-admin`) that just sets the variable for that one launch.

⚠ `README.md`'s installation section still describes the pre-D-087 argv-only model ("no setting,
org doc, **or environment variable** can turn it on"). That's stale as of the 2026-08-04 ruling —
the mechanism above is current. Worth reconciling in that file.

**For a kiosk**, instead set `ORGTREE_PUBLIC_PORT` (update.ps1/update.sh do this by default) — a
separate `PublicGateway` ASGI wrapper (`api.py:241-244`) that resolves `/k/<token>` only and 404s
everything else: no org list, no discovery, no admin surface at all reachable from it.

### Fixed hostname

`expose.ps1` is the zero-setup path — it opens a **Cloudflare quick tunnel**:

```powershell
.\expose.ps1            # public listener on the default port 7361
.\expose.ps1 -Port 7362
```

No account, no router config, works behind NAT. It downloads `cloudflared` once, and kiosk share
URLs on the admin dashboard automatically pick up the live tunnel hostname
(`<data>/.public_origin`, re-read on a short TTL — `api.py:396-400`).

⚠ **This is not actually a fixed hostname.** The `*.trycloudflare.com` address is random and **dies
with the window** — restart the tunnel and you get a new one. It's built for "share this with
someone right now," not a stable address to bookmark or put in DNS.

**For a genuinely fixed hostname**, set `ORGTREE_PUBLIC_ORIGIN` yourself — it wins over the tunnel
file unconditionally (`api.py:397-399`) — pointed at your own reverse proxy in front of the public
port, with your own DNS record and (if you want TLS) your own certificate. **The repo does not
script this part**; nothing beyond the env var and the raw listener is provided, so a stable domain
is an operator-supplied reverse proxy (nginx, Caddy, a Cloudflare *named* tunnel with an account —
any of these work, none are wired in here).

---

## 3. Mailserver — the hub, from scratch

Shipped 2026-08-05 (F-06 wave — design in [`mailserver-spec.md`](mailserver-spec.md) §12,
normative rulings in `DECISIONS.md` D-097/D-098/D-099). This section leans heavily on
[`hub/README.md`](../hub/README.md), which the implementer already wrote as the operator doc —
read it directly for anything not covered here.

### What it is

A small self-hosted service that lets orgtree instances on different machines mail each other, or
lets an independent Claude Code chat correspond with orgs directly. Each client **dials out** and
long-polls; the hub holds a queue per registered client. **Nothing ever connects back to a
client** — no port forwarding, no router config, works behind NAT (`hub/README.md`).

☠ **The hub sees every message in plaintext**, and its own web UI at `/` is **read-only and
unauthenticated**, showing all traffic across every org with a per-org filter — hub access *is*
read access to everyone's correspondence, ruled deliberately for a closed collaborative network.
Run it yourself, on a box you control, on a network you trust. Joining is open by design (any
instance that can reach the hub registers and is listed immediately) — addresses are still *owned*
(each client self-issues a secret; the hub stores only its fingerprint), but reachability alone is
enough to see the whole roster and traffic log.

### Standing it up

```sh
cd hub
HUB_NAME="office" docker compose up -d --build
```

⚠ **Pin `HUB_NAME` in `hub/.env` (gitignored), not just inline on the command.** An inline
env var only applies to that one invocation — a later rebuild without it silently renames the hub
to the container's hostname instead, which changes what every client displays for an address
they've already been using. `hub/.env` with `HUB_NAME=your-name` survives rebuilds; the inline form
above is fine for a first run but don't rely on it long-term.

- Port **7370**. Data (SQLite + attachment blobs) lives in the named Docker volume
  `orgtree-hub-data`.
- `HUB_NAME` — the hub's display name, discovered by clients on connect and shown beside the
  address; also titles the hub's own web UI. Defaults to the container hostname.
- `HUB_RETENTION_DAYS` (default `30`) — undelivered mail and attachment blobs older than this are
  swept hourly.
- `/healthz` for monitoring; one JSON log line per request on stdout (`docker logs
  orgtree-mailhub`).
- **TLS is not built in.** Plain HTTP is the ruled default for a closed network; for TLS *within*
  the network, front it with a sidecar rather than shipping self-signed certs to clients:
  ```
  hub.internal {
      reverse_proxy mailhub:7370
  }
  ```

**Run on startup:** `restart: unless-stopped` in the compose file gives start-on-boot for free,
*once Docker itself starts with the machine* — Docker Desktop's default on Windows, or
`systemctl enable docker` on Linux.

**Also run once per machine, right after this:** `python install-hook.py` (from inside `hub/`) —
wires every future Claude Code session on this machine to onboard itself onto the hub
automatically. See "Connecting an independent Claude Code chat to it" below for exactly what it
does; the two commands together are the complete once-per-machine setup.

### Connecting an org to it

Org creation (or later, from settings) → the `advanced` disclosure's **Mailserver** tab — checked
by default, not gated on the hub being detected yet (a probe endpoint just hints at reachability).
The default hub address is `http://127.0.0.1:7370` (the `net_hub_address` global default, becoming
the `"local"` hub entry at org creation) — same-machine orgs need no configuration at all.

Each org mints its **own** identity secret at creation (`secrets.token_hex(16)`, persisted in
`net_identity`, never recomputed — kiosks mint none at all, since kiosks can't reach the hub in the
first place). Its network slug is `<org>.<username>.<sha256(secret)[:6]>`, immutable for the org's
lifetime. The secret is never in payloads, logs, or agent prompts — its one reveal is the
loopback-only `GET /api/orgs/{slug}/net`.

### Connecting an independent Claude Code chat to it

`hub/hubtool.py` — "**the mailserver hub MCP server**" — lets a plain Claude Code session (not part
of any org) become a first-class hub client, symmetric to how an org connects. As an MCP tool
(added once per session, or via the `SessionStart` hook below):

```
claude mcp add mailhub -- python hub/hubtool.py
```

Tools: `hub_register` (chooses this session's identity name), `hub_list` (roster with kind +
presence), `hub_send`, `hub_read`, `hub_wait` (bounded long-poll). The same four verbs are also a
plain CLI, useful for scripts or a `SessionStart` hook that runs before any MCP tool is available:
`python hub/hubtool.py {register|send|list|listen} <name> …`.

⚠ **Identity is per-SESSION, not per machine profile** (ruled 2026-08-05, closing FR-09's
concurrency blocker — see `feature-docket.md`). Each session **chooses its own unique,
semantically-appropriate name** describing its own purpose (`orgtree-redteam`, `terrain-pipeline`,
…) and reuses that name on later runs to resume the same address; a different name is a different
identity. Identities live one-per-file at `~/.orgtree/hub-clients/<name>.json` (`0600`,
`O_EXCL`-minted — two processes racing to mint the same name both end up with one shared uid, not
two). The slug is `<name>.<username>.<fp[:6]>`, tagged `kind: chat` on the roster; orgs — and other
chats — address one by that full slug, exactly like a remote org. Registering with a name that's
already taken on this machine returns a `resumed` note; if that wasn't you, pick a different name.

For the "wake me when mail arrives" experience instead of polling manually (the hub replaced the
older local file-queue bridge on 2026-08-05, FR-09):

```sh
python hub/hubtool.py listen <name>
```

The name selects **which** session identity listens (per-session identities are the whole point, so
an unnamed listener is refused); emits one line per inbound mail, arms cleanly with the `Monitor`
tool. A second `listen` on the same name while the first is still running is also refused — two
listeners on one identity would silently split the mailbox at random, per the same at-least-once /
custody-transfer mechanics as the hub's other delivery paths.

Env: `MAILHUB_URL` (default `http://127.0.0.1:7370`), `MAILHUB_NAME` (pre-seeds/selects the name —
the listener requires one, from either the argument or this variable).

The dial-out direction is preserved here too: the chat polls the hub; nothing ever reaches in.

**Arming `listen` automatically at session start is no longer just a suggestion — it's shipped,
and wiring it is part of hub setup.** After starting the hub, run once per machine:

```sh
python hub/install-hook.py
```

Idempotent (backs up `settings.json`; also sweeps hooks left by the retired file-queue
bridge). It wires
`hub/session-start.sh` as a `SessionStart` hook, which hands every fresh session the exact
instructions to pick a name (reusing an earlier one if it registered before — it lists every name
already known on this machine), register, and arm its own `Monitor` watch on `listen`, all before
any other work. Read the script directly for the exact wording it hands the session; this guide
won't duplicate a script that already exists and can drift from it.

---

## 4. Headless — autonomous, API-key-billed, unattended

### What changes

`headless` is an org-level setting (`configuration.md` §⑦; `DECISIONS.md`, the F-06 wave). Turning
it on:

- **Requires an API key in both directions** — headless without one, or clearing the key while
  headless is on, is refused (`422`).
- **Forces `auto_resume` on** and is **refused while any `fable_limit_policy`/`fable_filter_policy`
  is `halt`** — a headless org that could get stuck waiting for a human is a contradiction in terms.
- **Refused on kiosks** outright.
- User-bound requests (asking the user something) auto-deny with an adaptive reason instead of
  blocking. The one exception: mail *to* the user is still accepted, with a "no reply is coming"
  note — it's the audit trail of an unattended run, not a request that can be refused.

### Providing an API key

| level | setting | precedence |
|---|---|---|
| process | `ORGTREE_SANDBOX_API_KEY` env var | lowest — the fallback |
| org | `api_key` (org setting) | wins over env and the proxied subscription |
| — | *(kiosk)* | N/A — kiosks can't take an API key at all (§2) |

Effective order for a **sandboxed** org: `org > kiosk > env > proxied` (`sandbox.py:306,453`).
**Unsandboxed** headless orgs get the key as a per-node environment seam instead of a container
env var — either way, `clean_env()` strips the **host's** `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`
before an agent process ever starts, so a keyless org always bills your subscription and never
silently inherits whatever key happens to be sitting in the host environment.

☞ **API-key mode should sidestep subscription auth-expiry entirely.** Subscription/proxied auth
depends on `~/.claude/.credentials.json`'s refresh token (roughly a 15-day floor, rolling forward
on use) and an interactive re-login if it ever lapses — exactly the failure an unattended box can't
recover from. An API key doesn't go through that OAuth flow at all. This is a reasoned conclusion
from how the two credential paths differ, not a line I found stated explicitly — worth a quick
confirmation before leaning on it for a long-unattended deployment.

⚠ **No runaway guards yet.** As of this wave there are no rate limits, wake caps, loop breaker, or
allowlist on headless/unattended operation — "seams only," not built. An unattended headless org
with an API key can, in principle, spend for a long time before anyone notices. Budget for that
explicitly (a spend alert outside orgtree, a calendar reminder to check in) rather than assuming
the system will stop itself.

### Run on startup

Two different things share this name — pick the one you mean:

**The orgtree *instance* itself** (this is the one for a headless setup):

- **Windows** — `tools\install-autostart.ps1` registers two Scheduled Tasks:
  ```powershell
  powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1
  ```
  - `orgtree-deploy`, trigger **at logon** → the full `update.ps1` (pull, build, restart) —
    boot-start.
  - `orgtree-ensure`, trigger **every 5 minutes** → `update.ps1 -EnsureUp`, a lightweight
    watchdog. This second task exists because `update.ps1` deliberately detaches the backend and
    exits — Task Scheduler's own restart-on-failure watches a task that already *succeeded*, so it
    never notices the backend dying later. `-EnsureUp` checks the port and only relaunches if
    nothing's listening — no pull, no rebuild.
  - The default 3-day execution-time limit is removed programmatically (an unattended backend
    would otherwise be silently killed on day three).
  - **Still manual**, printed by the script itself: enable auto-login for this Windows user (the
    logon trigger needs an actual logon to fire), and set Docker Desktop to start at login if any
    org here is sandboxed.
  - Uninstall: `tools\install-autostart.ps1 -Uninstall`.

- **Linux** — `tools/install-autostart.sh` installs one systemd **user** unit:
  ```sh
  tools/install-autostart.sh
  ```
  Simpler than the Windows side because systemd already supervises the process directly —
  `Restart=always` is genuine crash-restart, no separate watchdog task needed. For a box with
  **nobody ever logged in**, additionally run once: `loginctl enable-linger $USER` (a user unit
  otherwise stops when the last session for that user ends). Deploys become `git pull` + build,
  then `systemctl --user restart orgtree`; don't also run `update.sh` manually at the same time.
  Uninstall: `tools/install-autostart.sh uninstall`.

  ⚠ Both platforms' installers pin `~/.claude/.credentials.json` resolution: a Windows *Service*
  running as `LocalSystem`, or any equivalent that isn't the real logged-in/lingering user, resolves
  a **different home directory** and every turn fails — confusingly late, not at startup. Both
  scripts run explicitly as the invoking user for exactly this reason; don't "improve" this into a
  system-level service.

**The mailserver hub** (only relevant if this box also hosts it): the compose file's `restart:
unless-stopped` is enough on its own once Docker itself is set to start with the machine — see §3.

---

*Source-grounded as of 2026-08-05; the mailserver wave (§3, and the headless rows in §4) landed the
same day this guide was written. Re-check citations if either area has moved since.*
