# Running orgtree unattended (F-06 §9)

Boot-start is the easy half. The hard half is that the launch scripts DETACH
the backend and exit — so a naive "restart on failure" watches a launcher that
already succeeded and never notices the backend die. The installers below
encode the correct shape per platform.

## Windows — `tools\install-autostart.ps1`

Registers two scheduled tasks (uninstall with `-Uninstall`):

| task | trigger | runs |
|---|---|---|
| `orgtree-deploy` | at logon | full `update.ps1` (pull → build → relaunch) |
| `orgtree-ensure` | every 5 minutes | `update.ps1 -EnsureUp` — listener alive ⇒ silent exit; dead ⇒ relaunch only (crash-restart ≤ 5 min) |

Both are set to *run only when the user is logged on* (the CLI reads
`~/.claude/.credentials.json` from the **user profile**; a LocalSystem service
resolves a different `~` and every turn fails — the org boots, the UI serves,
and only the turns die, §9.1) and the default 3-day execution stop limit is
removed (it silently killed unattended backends on day three).

Still manual, still required for a truly unattended box:

- **auto-login** for the user (the logon trigger fires then). Configure via
  `netplwiz` or Sysinternals Autologon.
- **Docker Desktop → start at login**, if any org is sandboxed: the engine is
  a user-session application; no interactive session, no containers.

## Linux — `tools/install-autostart.sh`

One systemd **user** unit running the backend **in the foreground**
(`Type=simple`, `Restart=always`, `RestartSec=10`). Under systemd there is no
stale-backend race to guard — systemd owns the real process — so direct launch
is correct, and both boot-start and crash-restart come from the same unit.

For a box with nobody logged in, also run once: `loginctl enable-linger $USER`.

Deploys under systemd: `git pull` + build the UI, then
`systemctl --user restart orgtree`. (Manual `update.sh` runs still work, but
not while the unit is active — two owners of one port.)

A Linux box is the better host for an unattended instance; if autonomous orgs
become a real workflow, that is where they should live.

## macOS

A launchd **user agent** with `KeepAlive` — the same shape as the systemd
unit. No installer is shipped; adapt the Linux unit's ExecStart into a plist
under `~/Library/LaunchAgents`.

## The hub

`hub/compose.yaml` carries `restart: unless-stopped`, which gives the hub
start-on-boot once the Docker daemon itself starts with the machine (Docker
Desktop default on Windows; `systemctl enable docker` on Linux).

## What must be true of the ORG, not just the process (§9.4)

- **`auto_resume` on** — forced automatically when headless is enabled;
  strongly recommended for any unattended org.
- **An API key** (settings → autonomy) removes the hard ceiling on unattended
  subscription auth: the OAuth **refresh token expires** (~15-day window
  measured), renewal is interactive, and the failure mode is every turn dying
  at an unpredictable hour. The credential watcher warns each org's inbox
  when expiry is near — but a key makes the whole class impossible, and
  **headless mode requires one** (hard rule, both directions).
- **Headless** (settings → autonomy): agents are told no user is present;
  questions/credit requests/user audiences auto-deny; mail to the user is
  stored with a "no reply is coming" note (the inbox is the audit trail);
  the overseer renders grey with an empty eye; fable policies must be
  non-halt before it enables.
