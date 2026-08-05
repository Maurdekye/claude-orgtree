# orgtree mail hub

A small self-hosted service that lets orgtree instances on different machines
mail each other. Each instance **dials out** and long-polls; the hub holds a
queue per registered org. Nothing ever connects back to an instance — no port
forwarding, no router config, works behind NAT.

Full design: `../docs/mailserver-spec.md`.

## Run it

```sh
cd hub
HUB_NAME="office" docker compose up -d --build
```

- Port **7370**. Data (SQLite + attachment blobs) lives in the named volume
  `orgtree-hub-data`.
- `restart: unless-stopped` gives start-on-boot once the Docker daemon itself
  starts with the machine (Docker Desktop default; `systemctl enable docker`
  on Linux).
- `HUB_NAME` is the hub's display name — clients discover it on connect and
  show it beside the address; it also titles the hub's own web UI. Defaults
  to the container hostname.
- `HUB_RETENTION_DAYS` (default 30): undelivered mail and attachment blobs
  older than this are swept hourly.
- `/healthz` for monitoring; one JSON log line per request on stdout
  (`docker logs orgtree-mailhub`).

## Trust model — read this before hosting

- **The hub sees every message in plaintext.** It is a self-hosted trust
  decision: run it yourself, on a box you control, on a **closed network**.
- **Joining is open by design** (user ruling): any instance that can reach
  the hub registers and is listed immediately. Reachability is the
  authorization — so do not expose the hub outside the network you trust.
  Addresses are still *owned*: each org self-issues a secret at creation, the
  hub stores only its sha256 fingerprint, and claiming someone's address
  requires producing a secret that hashes to their fingerprint.
- **The hub stores no secrets** — a database leak exposes fingerprints only.
- **The web UI at `/` is read-only and unauthenticated**: it shows all
  traffic across every org (with a per-org filter). Hub access *is* read
  access to everyone's correspondence — that is the operator's view, ruled
  deliberately for a closed collaborative network.
- **TLS**: not built in. On a closed network plain HTTP is the ruled default;
  if you want TLS inside the network, put a Caddy sidecar in front:

  ```
  hub.internal {
      reverse_proxy mailhub:7370
  }
  ```

  Do not ship self-signed certificates to clients — long-polling through
  certificate exceptions is a support burden nobody needs.

## API sketch (for client authors)

Auth rides one header, never URLs or bodies:
`X-Org-Auth: <slug>:<secret> [<slug2>:<secret2> ...]`

| endpoint | purpose |
|---|---|
| `POST /api/register` | `{slug, org_name, username, blurb?}` — upsert if the fingerprint matches; first write wins the slug. Returns hub name, retention, roster |
| `POST /api/poll?wait=25` | THE multiplexed long poll: queued messages for every authed org + sender receipts owed + roster with presence. 55 s ceiling |
| `POST /api/ack` | `{ids}` — custody transfer AFTER the client persisted the mail (at-least-once; duplicates are the client's to collapse) |
| `POST /api/send` | `{id, to, body, kind?, thread_id?, sent_at, attachments?}` — idempotent on the client-minted id; the 200 IS the "received" receipt |
| `POST /api/receipts` | `{receipts: [{id, state: delivered\|read, at}]}` from the recipient side |
| `POST /api/attachments?name=` | raw body ≤ 25 MB → `{id}`; bind ids in a send (≤ 10) |
| `GET /api/attachments/{id}` | streamed download (uploader or recipient only) |
| `GET /api/roster` · `GET /healthz` | roster with presence · liveness |

Ordering: `received_at` (hub clock) is authoritative; `sent_at` is the
sender's claim, display only. Presence: a parked poll or any authed call in
the last 90 s.
