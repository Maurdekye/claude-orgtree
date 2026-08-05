![claude-orgtree](social-preview.png)

# claude-orgtree

A persistent, visual **organization of Claude Code agents** — a tree of real,
addressable Claude Code sessions with a credit budget, an office-room canvas,
and full agent-to-agent delegation. You sit at the root as the overseer; you
hire top-level agents, they hire their own reports, and the whole org runs on
your existing Claude Code installation and subscription.

Design document: [PLAN.md](PLAN.md) · UI manual: [docs/ui-guide.md](docs/ui-guide.md)
· How much infrastructure to run, and what each tier buys: [docs/infrastructure-tiers.md](docs/infrastructure-tiers.md)

**Design motto:** one thing, done very very well. orgtree is a simple idea —
a persistent, visual organization of Claude agents — refined meticulously and
taken to its logical conclusion, not a feature jamboree. And within that one
thing: permit as much as possible; close gaps with minimal-friction
shortcuts; step out of the way. The tree is not a rigid structure you're
confined to — it's a **sandbox of capabilities** with a *suggested*
organization that improves efficiency on the margin. Asking for what's already
true is a no-op, not an error; where a refusal would just tell you which other
command to run, orgtree runs it for you and tells you what it did. Hard "no"s
are reserved for real resource limits, true impossibilities, and protecting
the user's data. Even messaging reach is opt-in structure: siblings always
talk directly, so a flat org (or the coordinator charter's open-office
floorplan) is a complete graph — nobody is ever out of reach unless you
chose the nesting that makes them so.

## The model in one breath

**You are the root.** You hire top-level agents; agents hire their own reports
with the `orgtree_*` MCP tools every node is given. **Credits are occupancy,
not spend** — a live node *holds* its seat (haiku 1 · sonnet 3 · opus 5 ·
fable 10) plus its grant; retiring releases everything back; tokens are
unlimited (real dollars are *tracked* per node and per org, but deliberately
not capped). Messaging is **downward any depth, one hop up, sideways between
peers** — deep reach grants the recipient an audience to reply; only top-level
agents write to your inbox unbidden. Every manual action you take notifies the
agents it affects at their next turn. Reading (transcripts, scratch files) is
strictly downward. Capabilities — folders with rw/ro modes, terminal, web,
file editing, subagents, MCP servers, org-structure visibility — flow down
like credits: a parent cannot grant what it does not hold.

Nodes are ordinary Claude Code sessions. They run **resume-on-demand**: no
idle processes; each delivered message runs one `claude -p` turn and the
session sleeps again. A node near its context limit is **compacted by
splitting**: the successor carries on under the same name while the
pre-compaction self is archived in place as a consultable *knowledge bearer*.

## What you can do — a tour

**Run an organization from a living canvas.** Each org is an office-room
canvas: your **eye** at the top (the fixed anchor of the page — it never
moves), agent cards beneath, curved wires for reporting lines, dotted links
between peers, glowing bypass lines for audiences, and sparks that travel
the wires when mail moves. Pan and zoom freely; zoom into any card and it
becomes a full Claude-Code-style chat desk — transcript, live per-message
and per-tool feed, markdown rendering, and a composer whose send button
turns into a red ■ STOP while the agent is responding.

**Hire in one gesture.** Hover any card (or the eye) and pick a tier chip —
H/S/O/F. A dashed draft appears: name it, drag its credit bar to set the
grant, optionally give it a **charter** (a standing role card — pick a named
preset from `docs/charters/`, or write your own), and hire. Your hires
cascade credits automatically down the chain; agents hire their own reports
through the same ledger with explicit, no-defaults specs. Drag cards onto
other cards to re-parent whole subtrees; every hire, retire, move, or grant
change notifies the agents it affects.

**Talk to anyone — everything is mail.** Message any agent from its desk or
the switchboard. A busy agent receives your message **mid-task** (delivered
right after its next tool call, clearly attributed); idle agents wake
immediately. Every message is persistent mail: you have an inbox on the eye
(unread glow, per-mail read tracking, sent folder), and every agent has its
own webmail-style inbox tab. Agents report status with a chip on their card
and mail you results; top-level agents can always reach you, deeper ones
need an **audience**.

**The switchboard.** Click the eye and it expands to your screen, opening
side-by-side live chats with every agent that has a **direct line** to you —
top-level agents plus any audience holders. Tabs minimize/maximize each
chat; an audience-granted tab carries an ✕ that closes the line by
rescinding the grant.

**The coordinator pattern.** The intended everyday shape: one opus
**coordinator** directly under you (its charter ships in `docs/charters/`),
every worker flat beneath it. The coordinator decomposes your asks, hires
per piece, and **delegates a user audience** to each hire — so the
switchboard fills with direct lines while a single authority below you does
the routine coordination. Delegated audiences are a first-class mechanic:
any agent can open any ear within its own reach (its own, a peer's, its
superior's — the user's, for top-level agents) for any agent in its subtree.

**Context is managed for you.** Each card's wheel shows context occupancy.
At the configurable threshold (80% by default) a node **splits**: a
compacted successor carries on under the same name; the predecessor stays
consultable as a knowledge bearer in its lineage stack. In the zoomed view
the wheel is also a button — click it to compact **now**.

**Limits and safety valves.** Usage-limit freezes show a 🧊 badge and a
resume button that stays **red until the reported reset time passes**, with
an inline **auto** toggle that restarts everyone a minute after the reset.
There's a per-agent interrupt (the desk composer's ■ STOP), an org-wide
**killswitch** (unlatch, then STOP ALL), per-agent rights (folders rw/ro, terminal, web, editing,
subagents, MCP servers, org visibility) enforced server-side, org-wide hire
defaults on the eye's gear, and real-dollar tracking per node and per org.

**Share an org with the world — kiosk mode.** Any org can be exposed
through a **preauthenticated secret URL** on a separate public listener,
with hard caps on credits, spend, and workspace storage; the admin app
itself never leaves 127.0.0.1. `expose.ps1` opens a Cloudflare quick tunnel
so outsiders reach it with zero setup on your router. Details below.

The full interaction manual — every gesture, badge, and panel — is
[docs/ui-guide.md](docs/ui-guide.md).

## Requirements

- **[Claude Code](https://claude.com/claude-code)** installed and
  authenticated (`claude` must work from your terminal). Agent turns run on
  your Claude subscription or API key — **real usage costs real money**.
- **Python 3.11+**
- **Node.js 18+** (builds the frontend; also used to invoke the Claude Code
  CLI in a newline-safe way on Windows)
- Windows, macOS, or Linux for the host-mode core (ledger, turns, canvas,
  kiosk URLs). Developed and battle-tested on Windows; POSIX paths are
  handled but less traveled — issues welcome.
- **Sandboxed orgs** (and kiosks, which default the sandbox on) additionally
  require **Windows with Docker Desktop's WSL2 backend**: each org's virtual
  disk is loop-mounted inside the docker-desktop WSL distro and the backend
  reads it via `\\wsl.localhost`.

## Installation

The update scripts below do all of this for you, including creating the
virtualenv — `./update.sh` (Linux/macOS/Git Bash) or `update.ps1` (Windows) on
a fresh clone is a complete install. By hand:

```bash
git clone https://github.com/Maurdekye/claude-orgtree.git
cd claude-orgtree

# a virtualenv, so the installed set is exactly what requirements.txt says
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scriptsctivate

# backend dependencies
pip install -r requirements.txt

# build the UI (served statically by the backend)
cd frontend
npm install
npm run build
cd ..

# run
cd backend
python -m orgtree.api
```

The venv is not decoration. `requirements.txt` names a package nothing imports
(`websockets`, which uvicorn loads by name), and installing into a system-wide
Python shared with other projects hides whether it is actually present — a
missing WebSocket library does not error, it answers the upgrade with a plain
`200 OK` and the UI silently degrades to polling. `/api/host` reports both
`python.venv` and `websockets` so a deployment can be checked at a glance.

**Recommended:** give agents their own up-to-date CLI (enables mid-task
message delivery — older CLIs never run tool hooks headless):

```bash
npm install --prefix ~/orgtree/cli @anthropic-ai/claude-code@latest
```

The supervisor auto-detects this private install and prefers it; your global
`claude` stays untouched. Without it, messages to a busy agent deliver when
its current response ends instead of after its next tool call.

**Mail hub (cross-session and cross-machine mail):** to let orgs, other
machines, and independent Claude Code sessions mail each other, start the hub
and wire the session hook — two commands, once per machine:

```bash
cd hub
docker compose up -d --build     # the hub service (port 7370)
python install-hook.py           # wires the SessionStart hook (idempotent)
```

The hook makes every NEW Claude Code session on the machine onboard itself
automatically — it registers a self-chosen identity name and arms its own hub
listener before other work. Details and the trust
model: [hub/README.md](hub/README.md) and
[docs/setup-guide.md §3](docs/setup-guide.md).

**Updating:** run `update.ps1` (or double-click `update.cmd`) on Windows, or
`./update.sh` on Linux/macOS — the two are step-for-step equivalents. Either
pulls the latest changes, rebuilds the UI, installs any new dependencies, and
restarts the backend in the background with a health check. `update.sh` also
runs under Git Bash on Windows.

Both accept a deliberately awkward `-ExposeAdmin` / `--expose-admin` switch,
which sets `ORGTREE_EXPOSE_ADMIN` and binds the **admin** API to `0.0.0.0`
instead of loopback. The admin API has no password, token or login —
reaching the port *is* the credential — so only do this behind a VPN, an SSH
tunnel, or an authenticating reverse proxy. The environment variable is what
actually gates it, on purpose: a service definition (Task Scheduler,
systemd) can set it directly with no switch needed, which the old
command-line-only design couldn't offer. What's unchanged is that no *org
setting or doc key* can turn it on, and it's stripped from every agent's own
environment regardless (`clean_env`) — so no agent can either. To share one
org with someone, make it a kiosk instead.

Open **http://127.0.0.1:7360**, create an organization, hover the eye, and
hire your first agent. The full interaction manual — hiring chips, credit-bar
dragging, desks, lineage, audiences — is in
[docs/ui-guide.md](docs/ui-guide.md).

### How it hooks into your Claude Code instance

No manual wiring is needed; the supervisor does all of it per turn:

- The `claude` CLI is resolved from your `PATH` (override with the
  `ORGTREE_CLAUDE` environment variable if you keep it elsewhere). On
  Windows the supervisor invokes `node …/cli.js` directly rather than the
  `.CMD` shim, because `cmd.exe` truncates multiline arguments.
- Each node is a normal Claude Code **session UUID**. Turns run headless via
  `claude -p` with `--session-id` (first turn) / `--resume` (after).
- Every node loads a per-org **MCP server** (`backend/orgtree/mcptool.py`, a
  dependency-free stdio bridge back to the running backend) that provides the
  `orgtree_*` tools: message, hire, retire/rehire/dissolve, reallocate,
  status, chart, read_transcript, read_scratch, audience.
- Nodes run with `--permission-mode acceptEdits` plus `--add-dir` for exactly
  the folders you granted, an isolating `--settings`, and
  `--strict-mcp-config` — the contract: **your personal hooks and MCP servers
  do not run in agent sessions** unless you grant them explicitly in the
  per-agent ⚙ panel. (Mechanism and its one caveat: docs/ARCHITECTURE.md.)
- Transcripts live where Claude Code always puts them (`~/.claude/projects`).
  Org state lives in **`~/orgtree/`** (ledger docs, per-org workspaces,
  per-node scratch dirs) — kept outside `~/.claude`, which Claude tools treat
  as a protected path.

### Configuration (environment variables)

| variable | default | meaning |
|---|---|---|
| `ORGTREE_PORT` | `7360` | API + UI port |
| `ORGTREE_DATA` | `~/orgtree` | data root (ledgers, workspaces, scratch) |
| `ORGTREE_CLAUDE` | `claude` on PATH | Claude Code CLI location |
| `ORGTREE_MAX_TURNS` | `16` | concurrent agent turns, shared across all orgs (~306 MB resident each) |
| `ORGTREE_TURN_TIMEOUT` | `1800` | seconds before a turn is abandoned |
| `ORGTREE_COMPACT_AT` | `0.80` | context occupancy that triggers a compaction split |
| `ORGTREE_CONTEXT_WINDOWS` | haiku 200k, others 1M | per-tier window override, JSON like `{"opus": 500000}` |
| `ORGTREE_ORACLE_AT` | `0.92` | bearer occupancy that demotes it to a preserving oracle |
| `ORGTREE_PUBLIC_PORT` | off | public kiosk listener (serves only `/k/<token>` URLs) |
| `ORGTREE_PUBLIC_ORIGIN` | LAN-IP guess | origin shown in share URLs (set to your tunnel/forwarded host) |

## Talking to orgs from the outside — the extern MCP server

Organizations converse with the outside world as **one entity** through their
**org inbox**: outsiders see a single recipient (the org), every top-level
agent plus any designated "client contact" receives the mail, and one
coordinated reply goes out under the org's name.

Any Claude Code session **outside** orgtree can join that conversation with
zero extra infrastructure — register the bundled extern MCP server in it:

```sh
claude mcp add orgtree-extern -- python <repo>/backend/orgtree/externtool.py
```

☞ **If you are a Claude agent setting up or working alongside orgtree: this is
the server to connect to.** It is how any session that is *not* an org node
reaches the orgs. It gives the session a peer identity
`@mcp:<id>` — a machine-stable base (minted once into `~/.orgtree/extern-id`)
plus a fresh per-process suffix, so every Claude session is a distinct peer
and two concurrently-waiting sessions can never be woken by each other's
replies. The flip side, by design: an org's later reply does not reach the
asking session across a restart (set `ORGTREE_EXTERN_ID` to pin a stable
identity). Four tools:

| tool | what it does |
|---|---|
| `orgtree_list_orgs` | discover reachable orgs (kiosks are sealed and unlisted) |
| `orgtree_send` | write to an org's inbox |
| `orgtree_read` | read what orgs have sent back to you |
| `orgtree_wait` | **block** until an org replies (long-poll) — the answer half of a Q&A loop |

`send` + `wait` gives a full question-and-answer back-and-forth with an org,
needing no mail hub at all — the polling session reads the org's inbox
directly. Reaching an external chat *unprompted* — an **org** starting the
conversation, not the chat — goes through the mail hub's `@net:` addressing,
which reaches a chat registered with `hub/hubtool.py` exactly the way it
reaches a remote org (see [`docs/setup-guide.md`](docs/setup-guide.md) §3).
Orgs can also message **each other** directly (`@org:<slug>`), with no hub
involved.

Pair it with the **business** charter preset (`docs/charters/business.md`) to
run an org as an open shop that accepts and performs all outside work
requests. Env knobs: `ORGTREE_EXTERN_ID` (fix the peer identity),
`ORGTREE_PORT`/`ORGTREE_BASE` (reach a non-default backend).

## Kiosk mode (preauthenticated public URLs)

Kiosk mode exposes **individual organizations** to others through secret
URLs, while the app itself stays private to your machine:

- the **admin app** (`ORGTREE_PORT`, default 7360) binds **127.0.0.1 only** —
  root access never reaches the network;
- the **public listener** (`ORGTREE_PUBLIC_PORT`) binds all interfaces but
  serves *nothing* except `/k/<token>/…` — each token maps to exactly one
  kiosk-enabled org; every other path (including `/`) is a bare 404. The
  URL is the authentication: no org list, no discovery, no admin surface.

Kiosk orgs are born as kiosks: tick **kiosk** in the *new organization* form
to set the limits (and the permission ceiling) at creation. Prepare the org
normally — hire seed agents, set folder holdings and tool rights, write
charters. Everything afterwards is managed live from **that org's own ⚙
settings panel** (admin side — there is no separate all-kiosks dashboard):
credit cap, spend limit, storage limit, the share URL with **copy** and
**rotate** buttons (the old URL stops working the instant you rotate), and
**pause/reactivate** for the URL.

```bash
ORGTREE_PUBLIC_PORT=7361 python -m orgtree.api   # update.ps1 sets this by default
```

**Reaching it from the internet — no port forwarding needed:** run
`expose.ps1`. It downloads `cloudflared` on first use and opens a
**Cloudflare quick tunnel** to the public listener: you get a random
`https://….trycloudflare.com` hostname that works from anywhere, over
HTTPS, for as long as the window stays open — no account, no router
changes, and the share URLs shown in the app switch to the live tunnel
hostname while it runs. Close it and the URL dies. (For a permanent,
stable hostname later: a named Cloudflare tunnel with your own domain —
then set `ORGTREE_PUBLIC_ORIGIN`.)

For each kiosk org, enforced **server-side on the public listener** (you, on
the admin side, keep full rights in the same org — visit it like any other):

- visitors see and reach only that one org;
- configuration is refused (403): org settings, per-agent rights, hire
  defaults, org.md, kiosk caps, and the filesystem browser;
- the overseer's pool is **finite**: a fixed-size credit bar, and no
  operation (hire, cascade, rehire, reallocate, credit-request approval) may
  push total holdings past the cap — and the cap itself can never be set
  below what the org already holds (retire or dissolve agents first);
- **spend limit** — total spend shows in the top bar; breaching it freezes
  every agent; raising the limit in the org's settings clears the freeze and
  ▶ resume replays the interrupted turns;
- **storage limit** — caps the org's own workspace folder (external folder
  grants are exempt). Past 90% of the limit, agents get a heads-up notice so
  they can clean up before anything bites. Breaching it does *not* freeze
  anyone: file creation and writes in the workspace are blocked (on Windows,
  enforced at the OS level with delete rights kept) until enough files are
  deleted — the block lifts automatically. (That is the *unsandboxed*
  kiosk's loose cap. A sandboxed kiosk's storage limit is the size of its
  virtual disk: soft tiers warn at 80% and pause new turns at 90%, and at
  100% writes fail with ENOSPC — disk orgs are never frozen or stopped for
  storage, and the in-app recovery browser works even then.)

Kiosk orgs are a **distinct type**: born as kiosks with their limits set at
creation (the *new organization* form's kiosk checkbox), never converted to
or from normal orgs. You visit them with full admin rights; URL visitors get the locked
view. The URL can be paused and reactivated; the limits always bind.

### Sandboxed orgs (Docker)

Any org — kiosk or normal — can be created **sandboxed** (kiosks default to
it): all its agents' turns run inside one dedicated **Docker container** —
real terminal use with no view of your machine: no host filesystem, no host
processes, per-container CPU/memory caps. The org workspace is the one
deliberately mounted window; session transcripts persist in the agent home
on the org's virtual disk (next paragraph) so resume, chat views, and
read-down keep working — for a migrated org, `<data>/sandboxes/<slug>/`
holds only the frozen pre-migration rollback copy, while the live home
(transcripts included) rides the disk, reachable via `\\wsl.localhost` and
the in-app storage browser.
The container reaches the backend only through a **bridge listener**
(`ORGTREE_BRIDGE_PORT`, default 7362) gated by a per-org secret that exists
nowhere but inside that container. Requires Docker Desktop running; the
image builds automatically on first use (`sandbox/Dockerfile`).

**Every sandboxed org rides ONE virtual disk with a real filesystem cap.**
The org's whole state — system dirs (`sudo apt install` and config edits work
and persist), the agent home *including session transcripts*, the workspace,
and scratch — lives on a fixed-size ext4 image (a loop mount inside Docker
Desktop's WSL distro; no admin rights involved). The rootfs is read-only,
`/tmp` is RAM (bounded by the memory cap), and `/usr/local` is a read-only
version-pinned volume so the CLI can't drift. The cap is the filesystem
itself: at 100% writes fail with ENOSPC — the container is **never stopped**.
Soft tiers run underneath: at 80% agents are warned, at 90% new turns pause
(the last 10% is the reserve that keeps session journaling alive) and resume
automatically under 85%. Disk size comes from the kiosk storage limit, the
org's `sandbox.limit_mb`, or `ORGTREE_SANDBOX_DISK_MB` (default 20 GB);
existing volume-layout orgs auto-migrate on their next turn (old volumes are
kept for rollback). The backend reads the disk directly (`\\wsl.localhost`) —
including deletes at 100% full — so recovery never depends on the container.

> ☞ **Set Docker Desktop's disk cap.** Org disks are SPARSE: a 20 GB cap costs
> the host only what's actually written, which keeps generosity free — but it
> also means N orgs can overcommit the host in aggregate. Each org's own cap
> is absolute (its ext4 size), while the **aggregate** bound is Docker
> Desktop → Settings → Resources → *Disk usage limit*. The default is a
> ~1 TB sparse disk — set it to what you can afford; the backend logs a
> warning when it's unset. (The WSL2 disk file also does not shrink on its
> own when content is deleted.)

Sandbox auth is the **proxied subscription** and is not configurable in the
UI: the container's CLI talks to the bridge's Anthropic passthrough, and the
HOST attaches your subscription's OAuth token (refreshing it in place) — so
sandboxed agents run on your plan while **no credential of any kind exists
inside the sandbox**. (`ORGTREE_SANDBOX_API_KEY` remains an env-level escape
hatch: a real API key, or the word `subscription` to copy credentials in.)

⚠ An *unsandboxed* kiosk bounds *configuration and money*, not *capability*:
visitors can make agents do anything the fixed rights allow, so give such
orgs no bash and workspace-only folders. The secret URL is a capability:
anyone holding it is that kiosk's visitor, so share deliberately and rotate
freely — and prefer serving it through an HTTPS tunnel (`expose.ps1`) so
tokens aren't sniffable in transit.

## A word on safety and cost

Agents run **autonomously** inside the folders you grant, with file editing
and (by default) a terminal. Folder access is enforced for Claude's file
tools, and read-only mode is enforced via permission rules — but an agent
with Bash can shell around most fences. Grant working directories the way you
would grant them to a contractor: deliberately. Keep an eye on the $ figures
the UI tracks per node and per org; the credit system bounds *concurrent
capacity*, not dollars.

## Development

```bash
python tools/run_tests.py                   # every suite, fast tier (~2 min)
cd frontend && npm run dev                  # vite dev server w/ API proxy
python tools/ui_probe.py sweep <org> out/   # headless UI screenshot sweep
```

### Running the tests

There is no pytest. Every backend suite is a plain script that prints `ok N`
lines and ends in `ALL N CHECKS PASS`, and the frontend suite is node's own
test runner behind an esbuild step — so each one can still be run directly
(`python backend/tests/test_ledger.py`, `npm test` in `frontend/`). One command
runs all of them and prints a single summary:

```bash
python tools/run_tests.py            # fast tier — hermetic only, ~2 min
python tools/run_tests.py --full     # everything, live rigs included, ~13 min
python tools/run_tests.py --list     # what would run, and how, without running it
```

Useful flags: `--only <substring>` · `--serial` · `--jobs N` · `--no-frontend`
· `--logdir DIR` (per-suite logs; otherwise a temp directory, path printed).
Exit status is non-zero if any suite fails.

**The two tiers.** The fast tier runs every suite in the cheapest mode that
suite advertises — `--hermetic` if it has one, else `--quick`, else plain — and
touches no real listener that matters. It is what CI runs. The full tier runs
everything at full depth, including the live rigs that spawn a real uvicorn, a
real turn loop and a fake Claude CLI, and sweep timing configurations in real
elapsed time. Those are minutes each, so they are a pre-release gate rather
than a per-change one.

**How suites are found.** By glob — `backend/tests/test_*.py` plus
`frontend/tests/run.mjs`. Adding a suite requires no edit to the runner: its
flags, whether it starts a real listener (those run one at a time, after the
parallel pool drains, so nothing races them), whether it asserts Windows-only
filesystem behaviour, and whether it carries a drift guard are all read out of
the suite's own source. The one table of literals in `run_tests.py` is `SLOW`,
which records *measured* wall times that keep a suite out of the fast tier.

**Drift guards.** Several suites mirror expressions that live in production
files and check that the original still says what the mirror assumes:
`backend/tests/msgvis.py` re-implements the client's ghost-graduation rule and
greps four sources for the nine expressions it ports, `derived.test.ts` pins
seven `convo.ts` constants, and the authority suite audits every
grant-mutating site in `ledger.py`. If one of those fires, the runner says so
under its own banner and separately from the pass/fail count,
because a drift failure does not mean the app is broken — it means a guarded
expression moved and the test's model of it did not, so every check downstream
of that model has quietly become fiction until the mirror is updated. The
summary also reports guards that ran and *held*, and flags a guard that
printed no verdict at all.

**CI** (`.github/workflows/tests.yml`) runs the fast tier on every push, on
`windows-latest` **and** `ubuntu-latest`. Windows is the authoritative job:
orgtree runs on Windows, and `test_persistence.py` asserts Windows filesystem
semantics directly (`os.replace` over an open destination raises WinError 5;
`FILE_SHARE_DELETE` does not rescue it) — the writer-preferring latch exists
*because* of them. On Linux those calls simply succeed, so the runner skips
that suite there and prints the reason in the summary rather than pretending
it passed. The Linux job is advisory until it has come back green once —
nothing in this tree has ever been observed running on Linux, and a blocking
job that has never passed is a job people turn off.

The ledger (`backend/orgtree/ledger.py`) is the single source of truth for
credits, authority, addressing, and capability subsets; the supervisor
(`supervisor.py`) owns sessions and turns; `api.py` is a thin FastAPI + WS
layer; the canvas lives in `frontend/src/canvas/` (shared · modals · mail ·
desk · cards · OrgCanvas) behind the `Canvas.tsx` barrel — the frontend is
TypeScript throughout.

## License

MIT — see [LICENSE](LICENSE).
