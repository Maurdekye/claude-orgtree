# Frozen deployment profile

> **Status (2026-08-31).** This document covers the full frozen contract as
> merged in `main` at `2316a83b`: the install-wide policy (`8a417724`), sandbox
> network isolation and the rotatable per-org bridge credential
> (`15c72ed8`/`db8d0711`/`b1bd31ac`), and approved-install attestation
> (`bc7e456`…`2316a83b`). All source citations below were checked against that
> revision, including running the verifier and the seven policy/attestation
> test suites (99 checks total, all passing).

The frozen deployment profile is an opt-in, install-wide policy for an
operator-controlled orgtree installation. It reduces the attack surface
available to untrusted agent code while leaving ordinary (`standard`) mode
unchanged. Terms used below: an **org** is one agent organization; a
**sandboxed org** runs its agents' turns inside a dedicated Docker container
instead of natively on the host; the **bridge** is the one HTTP door out of
that container back to the backend.

**Deploying `main` does not put an install into frozen mode.** Frozen mode is
an explicit opt-in — an environment variable, sandboxing every org, and (for
an existing install) a migration. A freshly deployed `main` with no
configuration change behaves exactly as it did in `standard` mode.

## Activation contract

The only selector is:

```text
ORGTREE_DEPLOYMENT_PROFILE=frozen
```

set install-wide (not per-org, not per-org-document). Every component reads
it through `orgtree.deployment.current_policy()`; no other code parses the
environment variable itself. `current_policy()` trims whitespace, ignores
letter case, and returns an immutable `DeploymentPolicy`:

- unset, blank, or `standard` selects ordinary behavior;
- `frozen` selects the frozen policy;
- every other value raises `DeploymentConfigError` — there is no permissive
  fallback for a typo or an unrecognized profile name.

The only supported frozen backend launcher is:

```powershell
python -m orgtree.api
```

(the update scripts use this module entry point). **Direct Uvicorn
(`uvicorn orgtree.api:app`) is unsupported in frozen mode.** The reason is not
stylistic: a direct Uvicorn invocation chooses its own listener socket bind,
and the ASGI app itself has no way to observe or veto that choice before
Uvicorn is already listening — only the module entry point's preflight runs
before any socket opens. The approved-install verifier (below) rejects an
install that was not launched this way.

Frozen startup also requires `ORGTREE_PUBLIC_PORT=0` to be **explicitly set**,
not merely left unset. `update.ps1`/`update.sh` default `ORGTREE_PUBLIC_PORT`
to `7361` when the variable is absent from the environment — that default is
correct for `standard` mode and wrong for frozen mode, so an operator running
the update script for a frozen install must export `ORGTREE_PUBLIC_PORT=0`
themselves before the script runs, or it will hand the backend a public
listener port that frozen startup then refuses.

## What is enforced today

`_deployment_preflight()` (`backend/orgtree/api.py`) runs before the module
entry point starts any listener. It repeats at ASGI startup for a caller that
imports the app directly. On failure the module entry point prints
`DEPLOYMENT POLICY REFUSED STARTUP` and exits with status 2; nothing is
started.

| Control | Standard | Frozen |
|---|---|---|
| Existing orgs | Host-mode and sandboxed orgs are both accepted. | Startup inventories every org; an unsandboxed or unreadable org refuses startup. This proves configured sandbox status, not that Docker can actually start that org's container. |
| New orgs | Sandboxing is optional. | An unsandboxed normal or kiosk org is rejected with HTTP 422 before it is written. |
| Turn admission | Existing behavior. | Normal and immediate turn paths recheck that the org is configured as sandboxed, so a post-startup org-doc mutation cannot admit a host-mode turn. |
| Admin listener | `ORGTREE_EXPOSE_ADMIN` may opt in to `0.0.0.0`. | The launcher binds `127.0.0.1`; a truthy `ORGTREE_EXPOSE_ADMIN` refuses startup. |
| Public kiosk listener | `ORGTREE_PUBLIC_PORT` behaves as configured. | A nonzero port refuses startup — see the activation contract above. |
| Legacy sandbox credential copy | The `subscription` escape hatch (host credentials copied into the sandbox) remains available. | Startup refuses `subscription` anywhere it could be configured (process environment, org defaults, org settings, kiosk settings), and refuses an existing sandbox `.claude/.credentials.json` or an unverifiable credential path. Settings/create reject forbidden values before writing; runtime auth rechecks before container reuse/start. Existing copies are refused, not deleted — the operator removes them. |
| Sandbox network | Every org's container reaches the host bridge directly (`host.docker.internal`) and has ordinary outbound internet access. | Every org's container joins its own `--internal` Docker network with no route to the host, LAN, or internet, except through a fixed-upstream relay — see [Sandbox network boundary](#sandbox-network-boundary). |
| Anthropic relay | The `/anthropic/*` passthrough forwards any method/path. | The relay accepts only `POST v1/messages`; everything else is refused before an upstream connection opens. |
| Sandbox bridge credential | Each org's persisted sandbox secret is the sole credential, unrotatable without editing the org document by hand. | Each frozen org gets a deterministic, HMAC-signed, rotatable credential minted from a host-only install key — see [Bridge credential rotation](#bridge-credential-rotation). |
| Agent restart tools | `orgtree_self_restart`, `orgtree_self_update` (deprecated alias), and `orgtree_prime_restart` are available to authorized callers. | These tools are omitted from every provider's tool catalog and from the identity prompt; the API, ledger, and supervisor launch/arm/cancel paths independently refuse them; the prime-restart engine does not run. Operator-controlled deployment (a human running `update.ps1`/`./update.sh`) is unaffected — this only removes the *agent-triggered* path. |

## Setting up a frozen install (fresh, no existing orgs)

1. Follow the ordinary installation steps in
   [`setup-guide.md`](setup-guide.md) §0–1 (Python, Node, provider CLI,
   `update.ps1`/`./update.sh`) but do **not** start the backend yet.
2. Confirm Docker Desktop is installed with the WSL2 backend. The one-disk
   sandbox implementation shells out to `wsl.exe` and reads the mounted disk
   over `\\wsl.localhost` (`backend/orgtree/disk.py`); it is Windows-specific
   today. Frozen mode requires every org to be sandboxed, so this makes
   Docker Desktop + WSL2 a hard requirement for a frozen install, not an
   optional one as it is in standard mode.
3. Set the install-wide environment before the backend's first launch:
   ```powershell
   $env:ORGTREE_DEPLOYMENT_PROFILE = "frozen"
   $env:ORGTREE_PUBLIC_PORT = "0"
   ```
   Leave `ORGTREE_EXPOSE_ADMIN` unset. Confirm `ORGTREE_BRIDGE_PORT` is an
   enabled TCP port (default `7362`) — frozen startup refuses to run with it
   disabled, since the sandbox relay is the only permitted service path.
4. Launch with `python -m orgtree.api` (or `update.ps1`/`./update.sh`, which
   invoke it). Create every org with sandboxing enabled from the start —
   frozen mode has no path to accept an unsandboxed org, ever.
5. Read the startup console output. A clean start prints the CLI resolution
   line, then `frozen-install: approved configuration <sha256> verified (<n>
   checks)`; a refusal prints the `DEPLOYMENT POLICY REFUSED STARTUP` banner
   naming the first failed check, and nothing is started.
6. Verify a live install with the operational checks in
   [Operational guidance](#operational-guidance-running-and-verifying-a-frozen-install).

## Migrating an existing (standard-mode) install to frozen

**There is no in-place conversion of an existing unsandboxed org.** Frozen
policy has no code path that sandboxes an org that already exists — the
migration is back up / export the org's data, recreate it as a new org with
sandboxing enabled while still running `standard` mode, verify the
replacement, and only then delete the old org. Treat every step below as
required, in order:

1. Stop new agent work. Record the orgtree revision (`git rev-parse HEAD`) and
   take an offline backup of the data root (`ORGTREE_DATA`, default
   `~/orgtree`) and any operator-managed configuration. Test that the backup
   is readable before proceeding.
2. For each unsandboxed org, separately copy out the work files and records
   that must survive (workspace contents, org documents, anything you need
   from mail history). Create a **replacement** org with sandboxing enabled
   and a disk of at least 4096 MB — org creation refuses a smaller request
   with HTTP 422 (`api.py`; the one-disk system seed and transcripts do not
   fit below that floor). Recreate the needed settings and agent structure,
   transfer only the intended work data, and exercise the replacement
   container before trusting it.
3. After verifying the replacement and its recovery copy, delete the old org
   through the admin UI. Deletion is permanent. Do not copy an old org
   document into a replacement data root — that is not a supported migration
   path and can restore exactly the unsandboxed state frozen startup is
   designed to reject.
4. Remove every `subscription` selector from the process environment, org
   defaults, org settings, and kiosk settings. Remove any sandbox
   `.claude/.credentials.json` only after selecting proxied auth or an
   explicit API key for that org — treat the removed file as a live secret
   while it exists.
5. Confirm Docker Desktop with the WSL2 backend can run every replacement
   org's container.
6. Set `ORGTREE_DEPLOYMENT_PROFILE=frozen` and `ORGTREE_PUBLIC_PORT=0`
   explicitly, leave `ORGTREE_EXPOSE_ADMIN` unset, and restart with
   `python -m orgtree.api`. If any org is still unsandboxed or any forbidden
   credential selector remains, startup refuses and names exactly which orgs
   or settings are blocking it — that refusal, not a partial start, is the
   expected outcome of an incomplete migration.

## Sandbox network boundary

In frozen mode, an org's agent container is placed on its own Docker network
created with `--internal` (Docker's own flag: no route out of that network at
all) and labeled `orgtree.frozen=1`. The only other member of that network is
a small relay container (`backend/orgtree/frozen_gateway.py`), one per org,
which is the sole dual-homed process: it also sits on Docker's ordinary
`bridge` network, from which it can reach the backend's host bridge listener.
The relay:

- runs as a read-only root filesystem, all Linux capabilities dropped,
  `no-new-privileges`, capped at 128 MB / 0.25 CPU / 64 PIDs, with no
  published port — its listener is reachable only from its own org's
  internal network, at the fixed alias `orgtree-frozen-gateway`;
- accepts only `POST` requests whose *literal* request-line target (not the
  path after any proxy-style normalization) matches one of exactly three
  shapes: `/api/agent`, `/api/orgs/<slug>/nodes/<id>/steer`, or the Anthropic
  messages path carrying a bridge credential
  (`/anthropic/otb1.<payload>.<tag>/v1/messages`);
- refuses an absolute-form request target (scheme or host in the request
  line) outright — that is how a general forward proxy would let a client
  pick a different destination, and the relay is deliberately not one;
- streams the request/response bodies to one fixed configured upstream
  (`http://host.docker.internal:<ORGTREE_BRIDGE_PORT>`) rather than buffering
  them, and logs only the caller's address, never the request path (the
  Anthropic path carries the bridge credential).

The agent container itself is started with `--network <its internal
network>` and, unlike standard mode, with **no** `--add-host
host.docker.internal:host-gateway` — it has no way to resolve or reach the
host at all except by talking to its own relay at
`orgtree-frozen-gateway:8765`, which is exactly what `sandbox.bridge_url()`
configures inside the container in frozen mode.

On the host side, the bridge listener itself binds `127.0.0.1` on Windows and
macOS (where Docker Desktop forwards `host.docker.internal` to loopback
services) or the Docker bridge network's host-side gateway address on native
Linux — never `0.0.0.0`. `sandbox.bridge_bind_host()` refuses startup rather
than falling back to a wildcard bind if it cannot establish that address.

## Bridge credential rotation

Frozen mode replaces each org's persisted sandbox secret with a deterministic
credential minted from a **host-only install key**
(`<data root>/.bridge-credentials.key`, 32 random bytes, created with mode
`0600` on POSIX, never mounted into any sandbox) plus a persisted per-org
generation counter (`backend/orgtree/bridgeauth.py`). The credential is
`otb1.<base64url org slug>.<32-hex HMAC-SHA256 tag>`, where the tag binds the
slug and generation to the install key. Two admin-only, secret-free
operator endpoints exist per org:

- `GET /api/orgs/{slug}/bridge-credential` — reports scheme, org, current
  generation, a one-way fingerprint of the live credential, whether the
  previous generation is still accepted (only meaningful after a rotation),
  and `same_org_nodes_mutually_trusted: true`. It never returns the bearer
  itself.
- `POST /api/orgs/{slug}/bridge-credential/rotate` — atomically increments
  the org's generation counter and, before returning, plants the exact
  credential that was valid immediately beforehand and proves the live
  resolver now rejects it. If that verification fails, rotation raises rather
  than reporting success.

Both endpoints are frozen-mode only (`standard` mode gets HTTP 409) and
refuse a public/kiosk caller (HTTP 403) — they are operator affordances, not
agent-facing tools.

**This credential is rotatable per org. It is not per-node isolation, and
that limitation is load-bearing, not incidental.** Every sandboxed node in
one org shares one root-capable container. A sibling process inside that
container can read another live process's bearer directly from
`/proc/<pid>/environ` or `/proc/<pid>/cmdline` — nothing about HMAC binding
prevents that, because the attack is theft of a value the container already
holds in the clear, not forgery of a value it doesn't. The HMAC binding
prevents a stolen or guessed credential from being *forged* for another org,
and rotation lets an operator invalidate a specific org's credential on
demand; neither mechanism stops one node from impersonating a sibling node in
the same org. If agents in the same org must not be able to impersonate one
another, they need separate orgs, not just a rotated credential.

## Attestation and the approved-install manifest

`backend/orgtree/frozen_install.py` proves a running install matches a
pinned, committed configuration — not just that the policy selector says
`frozen`. It is exercised two ways:

- **At startup**, `require_approved_install()` runs at the very end of
  `_deployment_preflight()`, after the org-inventory and credential-selector
  checks above. It mints the host-only bridge key if this is the very first
  frozen boot, then checks everything except the running listener table
  (nothing is bound yet at this point). Any failure raises
  `DeploymentConfigError`, which `main()` turns into the same `DEPLOYMENT
  POLICY REFUSED STARTUP` banner and `SystemExit(2)` as every other frozen
  refusal — nothing is started.
- **Standalone**, `python tools/verify_frozen_install.py [--verbose]
  [--json] [--skip-containers] [--build-commands]` read-only inspects a
  *live* process. Because it runs after the backend has bound its sockets,
  it can additionally read the real kernel listener table (via `psutil`) —
  proof startup cannot produce, since startup necessarily runs before
  Uvicorn binds anything. `--skip-containers` is diagnostic only and,
  because it omits the image checks, cannot prove the complete installation.
  `--build-commands` refuses outside frozen mode and otherwise prints the
  exact `docker build` invocations for the approved images — quote that
  command in your own notes rather than hand-writing a build command, since
  it is derived from the manifest rather than fixed text.

**What it checks**, one manifest section at a time
(`frozen/approved-install.json`, schema `1`, profile `frozen`):

| manifest section | what is checked | check codes |
|---|---|---|
| the manifest itself | the manifest file's own SHA-256 matches a value pinned separately in `frozen_install.py` (`APPROVED_MANIFEST_SHA256`), so the manifest and the code that trusts it cannot drift silently | `MANIFEST_DIGEST` |
| `files` | SHA-256 of every pinned lock/definition file, **including the frozen network boundary's own source** — `backend/orgtree/bridgeauth.py` and `backend/orgtree/frozen_gateway.py` are pinned alongside the lockfiles and Dockerfiles, because the relay *is* the operation allowlist and is bind-mounted, read-only, into the one dual-homed container | `SOURCE_FILE_DIGEST` |
| `python` | interpreter (CPython, allowed minor versions, platform/machine) and the exact installed package set against a fully hash-locked `frozen/requirements.txt` (`name==version --hash=sha256:...` per line, no extras allowed) | `PYTHON_RUNTIME`, `PYTHON_PACKAGE_VERSION`, `PYTHON_PACKAGE_SET` |
| `frontend` | the installed `node_modules` tree (version + integrity) against `frontend/package-lock.json` | `FRONTEND_PACKAGE_TREE` |
| `providers` | per provider (`claude` required; `openai`/codex and `google`/gemini optional) — installed npm package version, npm `sha512-...` integrity, and that it was resolved from orgtree's own private prefix rather than bare `PATH` | `PROVIDER_VERSION`, `PROVIDER_INTEGRITY`, `PROVIDER_SOURCE` |
| `containers` | sandbox (required) and mailhub (optional) images exist under a content-addressed tag and carry the approved OCI labels, including a **digest-pinned base image** | `CONTAINER_IMAGE_PRESENT`, `CONTAINER_IMAGE_LABELS` |
| `bridge` | see below | `BRIDGE_SPEC_INVALID`, `BRIDGE_KEY_PRESENT`, `BRIDGE_KEY_HOST_ONLY`, `BRIDGE_ORGS_COVERED`, `BRIDGE_SCHEME`, `BRIDGE_SCOPE`, `BRIDGE_TRUST_BOUNDARY_DECLARED`, `BRIDGE_LEGACY_CREDENTIALS_REFUSED`, `BRIDGE_GENERATION`, `BRIDGE_FINGERPRINT`, `BRIDGE_PREVIOUS_GENERATION_REJECTED`, `BRIDGE_ATTESTATION_UNAVAILABLE` |
| launch/listener contract | the launch command actually used, the active `ORGTREE_DEPLOYMENT_PROFILE`, and — standalone only — the real listener table | `DEPLOYMENT_PROFILE`, `LAUNCH_PATH_SUPPORTED`, `LAUNCH_PROFILE_ACTIVE`, `ADMIN_LISTENER_LOOPBACK`, `PUBLIC_LISTENER_DISABLED`, `ADMIN_EXPOSURE_UNSET`, `LISTENER_TABLE_OBSERVED`, `NO_WILDCARD_LISTENER`, `LISTENER_PORT_SET`, `BRIDGE_LISTENER_HOST_ONLY` |

Image tags are content-addressed, not hand-written:
`<repository>:frozen-<first 16 hex of the manifest SHA-256>` — at this
checkpoint's manifest (digest `39f78098…`), that is
`orgtree-sandbox:frozen-39f7809836aa8da6` and
`orgtree-mailhub:frozen-39f7809836aa8da6`. Every approved image also carries
`io.orgtree.frozen.config=<full manifest SHA-256>`, so changing the manifest
changes the required tag and every previously-approved image tag stops being
approved automatically — there is no separate "re-approve the old images"
step to forget.

**The bridge manifest section is the adjudicated boundary in machine-readable
form**, and the verifier enforces it in both directions:

```json
"bridge": {
  "scheme": "hmac-sha256-org-v1",
  "scope": "org",
  "same_org_nodes_mutually_trusted": true
}
```

`_verify_bridge()` refuses a manifest that omits this, and separately refuses
one that claims anything other than `scope: "org"` and
`same_org_nodes_mutually_trusted: true` — an attestation that quietly
asserted per-node isolation would fail its own `BRIDGE_SPEC_INVALID` check.
For each sandboxed org it then checks that org's live
`bridgeauth.credential_attestation()` record matches: same scheme, `scope:
"org"`, the trust-boundary flag literally `true`, legacy credentials not
accepted, a well-formed non-negative generation counter, a
`sha256:`-prefixed fingerprint (never the bearer itself), and — the one place
a `null` is a pass rather than a warning —
`previous_generation_rejected` is `true` **or** `null` (an org that has never
rotated has no previous generation to reject; `false` means a rotation
happened and the superseded credential is still live, which is a real
finding). It also confirms the host-only signing key
(`<ORGTREE_DATA>/.bridge-credentials.key`) resolves to a path outside every
sandbox's own bind root — a key visible inside a container would not be
host-only. Running the standalone verifier never mints this key; only
frozen *startup* does, so that the very first frozen boot can attest its own
freshly-minted bridge instead of deadlocking on a key that only the check
itself would otherwise create.

**Exact output.** The header line is one of exactly `FROZEN INSTALLATION
VERIFIED` or `FROZEN INSTALLATION REFUSED`, followed by `profile: <name>`,
`approved configuration: <sha256 or 'unavailable'>`, and `checks: <n>/<m>
passed`. By default only failures are listed (`--verbose` shows every check);
each line reads `[PASS <CODE>] <subject>: expected <...>; actual <...>` or
`[FAIL <CODE>] <subject>: expected <...>; actual <...>`, occasionally
followed by an indented one-line detail. A refusal ends with, verbatim,
`Nothing was approved. Fix every FAIL above and rerun the check.` Startup
success prints `frozen-install: approved configuration <sha256> verified
(<n> checks)`.

**The listener table check is not "everything must be loopback".** A frozen
backend holds exactly two listeners — the admin app and the sandbox bridge —
and neither may bind a wildcard address, but they are not held to the same
address: the admin app must be loopback (`127.0.0.1`/`::1`); the bridge must
be host-only, which is loopback on Windows and macOS and, on native Linux,
the Docker host-side bridge gateway address that the per-org relay can reach
but the LAN cannot (`_approved_bridge_hosts()` calls
`sandbox.bridge_bind_host()` to get the policy-correct answer for the host
it is running on, rather than asserting one address everywhere). The
standalone verifier reads the whole table, so a kiosk listener that is
actually open is caught even if the environment claims
`ORGTREE_PUBLIC_PORT=0` and no code path opened it on purpose.

## Threat boundary and residual risk

Frozen policy treats agent commands, repositories, prompts, mail,
attachments, and any other agent-controlled input as untrusted relative to
the host. Its trusted computing base still includes: the host OS and
account, Docker Desktop/daemon, the WSL2 kernel, the orgtree backend process
and its service definition, the data directory, the operator, the model
provider, the credential store, and every artifact/build service used to
install any of them. None of those are inside the isolation boundary frozen
mode provides — a compromise of any of them is out of scope by design, not
an oversight.

**The isolation unit is the org container, not an individual node.** Agents
in one org share a root-capable container and must be treated as mutually
trusted at the bridge identity boundary (see
[Bridge credential rotation](#bridge-credential-rotation) above — this is
restated here deliberately, because it is the residual risk most likely to be
assumed away). Use separate orgs when agents must not be able to impersonate
one another.

Frozen policy does **not** defend against: host or Docker compromise;
container or runtime escape; a malicious approved build (attestation proves
the running configuration matches a pinned manifest, not that the manifest
itself is safe); a trusted operator acting maliciously or by mistake; or a
compromised model provider. Every allowed provider call discloses the
content sent in it — the network boundary narrows *which* operations can
reach the provider, not what is inside them. Version pins improve
repeatability, not safety, and local configuration evidence is not remote
attestation by a third party.

Host firewall and Docker/WSL network policy remain part of the boundary that
frozen mode does not manage for you. The per-org internal Docker network and
its single-purpose relay stop a sandboxed container from reaching the host,
LAN, or open internet directly; they do not stop the *relay* itself from
being a target, and they assume the host's own Docker/WSL networking is
configured sanely to begin with. Do not place sensitive host or LAN services
reachable from the same Docker bridge network the relay containers attach to.

## Operational guidance: running and verifying a frozen install

**At startup**, confirm:

- the console printed the CLI resolution line, not a `DEPLOYMENT POLICY
  REFUSED STARTUP` banner;
- the console printed `frozen-install: approved configuration <sha256>
  verified (<n> checks)`, not a refusal;
- `GET /api/host` (loopback-only) reports the expected profile.

**Run the standalone verifier against the live process** — this is the check
startup cannot do, because it reads the real kernel listener table:

```powershell
python tools\verify_frozen_install.py --verbose
```

A clean install prints `FROZEN INSTALLATION VERIFIED` and `checks: <n>/<n>
passed`; anything else names the exact failing check(s) and ends with
`Nothing was approved. Fix every FAIL above and rerun the check.`

**Exercise the policy and attestation code directly** with the test suites —
these check source behavior, not the state of a specific deployed host, so
run them against the exact revision you intend to deploy, before deploying
it:

```powershell
python backend\tests\test_deployment_policy.py               # 4 checks
python backend\tests\test_frozen_policy_enforcement.py        # 14 checks
python backend\tests\test_frozen_gateway.py                   # 6 checks
python backend\tests\test_frozen_network_policy.py            # 8 checks
python backend\tests\test_bridge_credentials.py               # 12 checks
python backend\tests\test_frozen_install.py                   # 20 checks
python backend\tests\test_frozen_attestation_integration.py   # 35 checks
```

All seven currently pass in full (99 checks) against `main` at `2316a83b`.

**For a running install**, an operator can (all admin-only, loopback):

- `GET /api/orgs/{slug}/bridge-credential` to confirm an org's bridge
  credential generation and that legacy credentials are not accepted;
- `POST /api/orgs/{slug}/bridge-credential/rotate` to rotate one org's bridge
  credential and get back proof the old bearer no longer resolves. Existing
  sandbox processes must pick up the new credential on their next request —
  the response marks `existing_processes_must_refresh: true`.

**Inspecting Docker state directly** is a useful cross-check independent of
the API: `docker network inspect orgtree-frozen-<slug>` should show
`Internal: true` and the `orgtree.frozen=1` label; `docker container inspect
orgtree-frozen-gateway-<slug>` should show it attached to both `bridge` and
that org's internal network and nothing else.

## Updates, downgrade, and evidence retention

Agent-controlled deployment is disabled in frozen policy (see the agent
restart row in the enforcement table above). A trusted operator must stop
the service, install a reviewed revision, and rerun
`python tools/verify_frozen_install.py --verbose` before resuming agent work.

Returning the selector to `standard` restores ordinary behavior and is a
security downgrade: it re-enables legacy sandbox credential copying, the
broad Anthropic relay, direct host-bridge network access, and agent-triggered
restart tools. A prime-restart armed before frozen mode remains durable but
inert while frozen, and can become eligible again after a downgrade —
inspect and handle that state deliberately; frozen policy does not erase it.

For external review, retain: the exact commit and clean/dirty working-tree
state; `python tools/verify_frozen_install.py --json` output; startup logs;
Docker network/container inspection output for each org; the seven
test-suite results above; and, per org, the bridge-credential attestation
response. Never include provider tokens, bridge credentials, kiosk URLs, or
an unredacted environment dump in anything retained for review.
