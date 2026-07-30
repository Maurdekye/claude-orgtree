![claude-orgtree](social-preview.png)

# claude-orgtree

A persistent, visual **organization of Claude Code agents** — a tree of real,
addressable Claude Code sessions with a credit budget, an office-room canvas,
and full agent-to-agent delegation. You sit at the root as the overseer; you
hire top-level agents, they hire their own reports, and the whole org runs on
your existing Claude Code installation and subscription.

Design document: [PLAN.md](PLAN.md) · UI manual: [docs/ui-guide.md](docs/ui-guide.md)

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

## Requirements

- **[Claude Code](https://claude.com/claude-code)** installed and
  authenticated (`claude` must work from your terminal). Agent turns run on
  your Claude subscription or API key — **real usage costs real money**.
- **Python 3.11+**
- **Node.js 18+** (builds the frontend; also used to invoke the Claude Code
  CLI in a newline-safe way on Windows)
- Windows, macOS, or Linux. Developed and battle-tested on Windows; POSIX
  paths are handled but less traveled — issues welcome.

## Installation

```bash
git clone https://github.com/Maurdekye/claude-orgtree.git
cd claude-orgtree

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

**Recommended:** give agents their own up-to-date CLI (enables mid-task
message delivery — older CLIs never run tool hooks headless):

```bash
npm install --prefix ~/orgtree/cli @anthropic-ai/claude-code@latest
```

The supervisor auto-detects this private install and prefers it; your global
`claude` stays untouched. Without it, messages to a busy agent deliver when
its current response ends instead of after its next tool call.

**Updating:** run `update.ps1` (or double-click `update.cmd`) from the repo
root — it pulls the latest changes, rebuilds the UI, installs any new
dependencies, and restarts the backend in the background with a health check.

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
  `claude -p` with `--session-id` (first turn) / `--resume` (after), so you
  can even open a node's session yourself with `claude --resume <id>`.
- Every node loads a per-org **MCP server** (`backend/orgtree/mcptool.py`, a
  dependency-free stdio bridge back to the running backend) that provides the
  `orgtree_*` tools: message, hire, retire/rehire/dissolve, reallocate,
  status, chart, read_transcript, read_scratch, audience.
- Nodes run with `--permission-mode acceptEdits` plus `--add-dir` for exactly
  the folders you granted, `--settings '{"disableAllHooks":true}'` and
  `--strict-mcp-config` — so your personal hooks and MCP servers never leak
  into agents unless you grant them explicitly in the per-agent ⚙ panel.
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
| `ORGTREE_MAX_TURNS` | `3` | concurrent agent turns |
| `ORGTREE_TURN_TIMEOUT` | `1800` | seconds before a turn is abandoned |
| `ORGTREE_COMPACT_AT` | `0.80` | context occupancy that triggers a compaction split |
| `ORGTREE_CONTEXT_WINDOWS` | haiku 200k, others 1M | per-tier window override, JSON like `{"opus": 500000}` |
| `ORGTREE_ORACLE_AT` | `0.92` | bearer occupancy that demotes it to a preserving oracle |
| `ORGTREE_PUBLIC_PORT` | off | public kiosk listener (serves only `/k/<token>` URLs) |
| `ORGTREE_PUBLIC_ORIGIN` | LAN-IP guess | origin shown in share URLs (set to your tunnel/forwarded host) |

## Kiosk mode (preauthenticated public URLs)

Kiosk mode exposes **individual organizations** to others through secret
URLs, while the app itself stays private to your machine:

- the **admin app** (`ORGTREE_PORT`, default 7360) binds **127.0.0.1 only** —
  root access never reaches the network;
- the **public listener** (`ORGTREE_PUBLIC_PORT`) binds all interfaces but
  serves *nothing* except `/k/<token>/…` — each token maps to exactly one
  kiosk-enabled org; every other path (including `/`) is a bare 404. The
  URL is the authentication: no org list, no discovery, no admin surface.

Prepare an org normally — hire seed agents, set folder holdings and tool
rights, write charters — then open the **public kiosks** panel on the org
list, pick the org, and copy its share URL. Everything is managed live from
that dashboard: credit cap, spend limit, storage limit, enable/disable, and
**token rotation** (the old URL stops working the instant you rotate).

```bash
ORGTREE_PUBLIC_PORT=7361 python -m orgtree.api
```

For each kiosk org, enforced **server-side on the public listener** (you, on
the admin side, keep full rights in the same org — visit it like any other):

- visitors see and reach only that one org;
- configuration is refused (403): org settings, per-agent rights, hire
  defaults, org.md, kiosk caps, and the filesystem browser;
- the overseer's pool is **finite**: a fixed-size credit bar, and no
  operation (hire, cascade, rehire, reallocate, credit-request approval) may
  push total holdings past the cap;
- **spend limit** — total spend shows in the top bar; breaching it freezes
  every agent; raising the limit on the dashboard clears the freeze and ▶
  resume replays the interrupted turns;
- **storage limit** — caps the org's own workspace folder (external folder
  grants are exempt). Breaching it does *not* freeze anyone: file creation
  and writes in the workspace are blocked (on Windows, enforced at the OS
  level with delete rights kept) until enough files are deleted — the block
  lifts automatically.

⚠ Kiosk bounds *configuration and money*, not *capability*: visitors can
still make agents do anything the fixed rights allow. For anything
internet-facing, give the kiosk org **no bash**, workspace-only folders, and
deliberate web access. The secret URL is a capability: anyone holding it is
that kiosk's visitor, so share deliberately and rotate freely — and prefer
serving it through an HTTPS tunnel (set `ORGTREE_PUBLIC_ORIGIN`) so tokens
aren't sniffable in transit.

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
cd backend && python tests/test_ledger.py   # ledger invariants (65 checks)
cd frontend && npm run dev                  # vite dev server w/ API proxy
python tools/ui_probe.py sweep <org> out/   # headless UI screenshot sweep
```

The ledger (`backend/orgtree/ledger.py`) is the single source of truth for
credits, authority, addressing, and capability subsets; the supervisor
(`supervisor.py`) owns sessions and turns; `api.py` is a thin FastAPI + WS
layer; the canvas lives in `frontend/src/Canvas.jsx`.

## License

MIT — see [LICENSE](LICENSE).
