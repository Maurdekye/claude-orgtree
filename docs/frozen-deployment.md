# Frozen deployment profile

> **Status (2026-08-31).** This document covers the frozen policy, sandbox
> network isolation, and rotatable bridge credential that are merged in `main`
> at `c3017347` (`8a417724` policy + `15c72ed8`/`db8d0711`/`b1bd31ac` network
> and bridge). Approved-install attestation (the manifest and the
> `verify_frozen_install.py` check) is landing separately and is **not yet on
> `main`** as of this checkpoint; see [Attestation](#attestation-and-the-approved-install-manifest)
> below for what it will add and how to tell whether it has landed.

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
   line and, once attestation lands (see below), an approved-configuration
   line; a refusal prints the `DEPLOYMENT POLICY REFUSED STARTUP` banner with
   the specific reason.
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
   and a disk of at least 4096 MB (`sandbox.py` refuses and silently raises a
   smaller request to this floor). Recreate the needed settings and agent
   structure, transfer only the intended work data, and exercise the
   replacement container before trusting it.
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

Not yet merged to `main` as of this checkpoint (`c3017347`). It is landing on
a peer branch and will add:

- `backend/orgtree/frozen_install.py` and a standalone CLI,
  `python tools/verify_frozen_install.py [--verbose] [--build-commands]`,
  that read-only inspects a live install: active profile, an approved
  manifest's own digest, the digest of every pinned lock/Dockerfile, the
  installed Python interpreter and exact package set, the frontend
  `node_modules` tree against its lockfile, each provider CLI's version,
  npm integrity, and install location, the presence and labels of the
  content-addressed sandbox/hub container images, and the launch/listener
  contract (module entry point used, admin/public/bridge listener state).
- A JSON manifest, `frozen/approved-install.json`, pinning the exact
  dependency, container-base, and provider-CLI versions an install must
  match to be considered approved.
- A call to `frozen_install.require_approved_install()` at the end of
  `_deployment_preflight()`, so an unapproved configuration refuses startup
  the same way an unsandboxed org does.

Until it lands, treat any claim about the verifier's exact check names, its
manifest schema, or its output strings as provisional. This section will be
filled in with source citations once the landing SHA is confirmed, rather
than documented from description alone.

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
- (once attestation lands) the approved-configuration line printed with a
  digest, not a refusal;
- `GET /api/host` (loopback-only) reports the expected profile.

**Exercise the policy directly** with the test suite the checkpoint shipped
with — these check source behavior, not the state of a specific deployed
host, so run them against the exact revision you intend to deploy, before
deploying it:

```powershell
python backend\tests\test_deployment_policy.py           # 4 checks
python backend\tests\test_frozen_policy_enforcement.py    # 14 checks
python backend\tests\test_frozen_gateway.py                # 6 checks
python backend\tests\test_frozen_network_policy.py         # 8 checks
python backend\tests\test_bridge_credentials.py            # 12 checks
```

All five currently pass in full (44 checks) against `main` at `c3017347`.

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
the service, install a reviewed revision, and — once attestation lands —
rerun the approved-configuration check before resuming agent work.

Returning the selector to `standard` restores ordinary behavior and is a
security downgrade: it re-enables legacy sandbox credential copying, the
broad Anthropic relay, direct host-bridge network access, and agent-triggered
restart tools. A prime-restart armed before frozen mode remains durable but
inert while frozen, and can become eligible again after a downgrade —
inspect and handle that state deliberately; frozen policy does not erase it.

For external review, retain: the exact commit and clean/dirty working-tree
state; the (once landed) approved manifest digest and verifier output;
startup logs; Docker network/container inspection output for each org; the
five test-suite results above; and, per org, the bridge-credential
attestation response. Never include provider tokens, bridge credentials,
kiosk URLs, or an unredacted environment dump in anything retained for
review.
