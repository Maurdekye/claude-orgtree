# Infrastructure tiers — how much to run, and what each tier buys

orgtree works with nothing but a Python process on your own machine. Everything
beyond that is **optional infrastructure you add when you want a capability you
do not have yet** — and each step is additive: nothing you set up earlier stops
working, and no message you can send at one tier becomes unsendable at the next.

There are three tiers. Most people never leave the first two, and the third
usually means *the machine next to you*, not the internet.

| | tier 1 · orgtree alone | tier 2 · + local mailserver | tier 3 · + shared mailserver |
|---|---|---|---|
| **you need** | Python + the Claude CLI | …and Docker | …and an address other machines can reach (a LAN IP is enough) |
| **agents ↔ agents, one org** | ✓ | ✓ | ✓ |
| **org ↔ org, same machine** | ✓ `@org:` | ✓ | ✓ |
| **your chats → your orgs** | ✓ `@mcp:` (chat polls) | ✓ | ✓ |
| **orgs → your chats, unprompted** | ✓ `@mcp:` listener (org must know the id) | ✓ `@net:` | ✓ |
| **chat ↔ chat** | ✗ | ✓ | ✓ |
| **anything ↔ another machine** | ✗ | ✗ | ✓ |
| **attachments** | ✓ (chat → org) | ✓ | ✓ |
| **delivery states + read receipts** | ✗ | ✓ | ✓ |
| **a service to keep running** | none | the hub container | the hub + the tunnel |

---

## Tier 1 — orgtree alone

**What you run.** The backend (`update.ps1` / `./update.sh`, then `run.ps1` /
`./run.sh`) and nothing else. The admin app binds `127.0.0.1:7360` and never
leaves the loopback interface; the kiosk listener on `7361` is part of the same
process and only answers URLs carrying a kiosk's secret token.

**What you get.** The whole product: organizations, hiring, credits, mail
between agents, the user inbox, asks, documents, compaction, sandboxes, kiosks.

**How anything outside the org is reached at this tier.** Two shortcuts, both
free:

- **`@org:<slug>` — org to org, in this instance.** A direct write into the
  other org's inbox. No transport, no daemon, no configuration.
- **`@mcp:<peer>` — an outside Claude Code session, *pulling*.** Register
  `backend/orgtree/externtool.py` as an MCP server in that session
  (`docs/setup-guide.md` §"an independent chat talking to this instance") and it
  can list orgs, send to an inbox, read replies, and long-poll for an answer.
  Nothing is pushed to it: the org-inbox row **is** the delivery, and the
  session reads it when it looks — but it need not look by hand.
  `python backend/orgtree/externtool.py listen` arms a Monitor-style
  listener on the machine-stable peer id (`~/.orgtree/extern-id`), so an org
  that knows that id can wake the session unprompted. Note the deliberate
  split: a session's own sends carry a per-session id (`base.suffix`) and
  its replies go there, so the listener never steals a live session's
  `orgtree_wait` answers — which also means a reply to a conversation a
  session started does not wake the listener.

These two exist so that nobody has to stand up a server just to let their own
chats talk to their own orgs. That is the common case, and it costs nothing.

**What you cannot do.** **Two chats cannot reach each other at all** — this
is the capability the hub exists for. `@mcp:` addresses exactly one class of
recipient, an org: `orgtree_send` takes an org slug and nothing else, and the
peer id is a *reply address*, not a mailbox. Two sessions can correspond only
by relaying through an org, whose agents then read every word. Nothing
crosses the machine boundary either, and there are no delivery states or read
receipts: a message either wrote a row or raised.

---

## Tier 2 — plus a local mailserver (the hub, on this machine)

**What you run.**

```bash
cd hub
docker compose up -d --build      # the hub on port 7370
python install-hook.py            # every NEW chat onboards itself (idempotent)
```

Docker is the only new requirement. The hub keeps its own SQLite database and
blob store in a Docker volume; `HUB_NAME` (pinned in `hub/.env`) names it on
every roster.

**What it adds.**

- **Chats become first-class clients.** A session registers a self-chosen name
  (`python hub/hubtool.py register <name>`) and is addressable exactly like an
  org: `<name>.<user>.<fingerprint>`, tagged `kind: chat`. This is what
  supersedes the older local file-queue bridge — chats can now message **each
  other**, not just orgs.
- **Orgs get a network address** and can therefore reach a chat *unprompted*,
  which tier 1 cannot do at all.
- **Delivery becomes observable.** Outbound mail is spooled and shipped by a
  daemon, and the org-inbox row carries `queued → sent → delivered → read`,
  plus the failure reason when an attempt fails.
- **Attachments and durability.** Up to 10 files and 25 MB each per message;
  bodies truncate at 20 000 characters; the hub retains messages for 30 days
  (`HUB_RETENTION_DAYS`) and sweeps blobs with them.
- **An operator view.** `http://localhost:7370/` shows every message on the
  hub, grouped by client.

**The trade-offs.**

- **A service to keep alive.** If the container is down, mail queues rather than
  failing — the spool retries — but nothing moves until it is back.
- **The operator UI is unauthenticated by design.** Anyone who can reach port
  7370 reads *every* org's correspondence. That is acceptable for a port bound
  to your own machine and is the reason tier 3 exists as a separate listener
  rather than a firewall rule.
- **The identity file is the credential.** `~/.orgtree/hub-clients/<name>.json`
  holds a 256-bit uid; the hub stores only its hash, and the address is derived
  from it. Lose the file and the address is gone — it cannot be re-minted,
  because the hub is first-write-wins. (Registering the same name again after a
  loss produces a **new** address and says so.)

---

## Tier 3 — a shared mailserver (reachable from other machines)

“Shared” does not mean “on the internet”. The ordinary shape is a hub on one
machine that the other machines **on your own network** talk to: a desktop that
stays on, a home server, a second laptop. Nothing needs to leave the LAN.

**What you run.** The same hub, with its **API-only** listener enabled, and an
address the other machines can reach:

```powershell
cd hub
$env:HUB_PUBLIC = "1"; docker compose up -d --build   # API-only listener, host port 7378
```

Peers then use `http://<this-machine's-LAN-IP>:7378` as their hub address — an
org's settings → mailserver, or `MAILHUB_URL` for `hubtool`. Give the machine a
static lease (or a hostname) so the address does not move under everyone.

**Why the API-only listener even on a trusted LAN.** Port 7370 serves the
operator UI at `/` — an unauthenticated view of **every** org's mail. “Another
PC can reach it” and “the all-mail UI is on that address” must never be the
same sentence, whether the other PC is your own or a stranger's. The API
listener carries only `/api/*` and `/healthz`, every route gated on the
caller's own org secret.

> ⚠ **Check what Docker published for you.** `hub/compose.yaml` maps
> `"7370:7370"`, and Docker binds published ports on **all** host interfaces —
> so the full hub, UI included, is reachable from your network as soon as the
> container is up, at tier 2, before you decide to share anything. On a
> network you do not control (a café, a hotel, a conference), that is every
> org's correspondence one URL away. Bind it to loopback —
> `"127.0.0.1:7370:7370"` — and let 7378 be the only shared surface.

**The trade-offs.**

- **Joining is open; addresses are owned.** Anyone who can reach the listener
  can register a *new* identity — that is how peers join. What they cannot do
  is take an address that already exists: every `/api/*` route verifies the
  caller's secret against the stored fingerprint in full, never by the visible
  suffix.
- **Everything on the hub is retained for 30 days**, including mail between
  parties you merely introduced. The operator can read all of it from the local
  UI.
- **The hub is now a dependency for other people.** Bring it down and their
  mail queues (with the failure reason on the row) until it returns.

### Optional: reaching it from outside your network

Only if peers genuinely live elsewhere. `hub/expose-hub.ps1` opens a Cloudflare
quick tunnel to the API-only port:

```powershell
.\expose-hub.ps1          # tunnels host port 7378
```

> ☠ **Read this before running it.** Exposure changes who can join from
> “someone on my network” to “anyone with the URL”, and registration is open by
> design. The address space, the roster of who is on your hub, and 30 days of
> everyone's mail all sit behind one secret URL. The script refuses to tunnel
> 7370 for the reason above — never work around that. A quick tunnel's URL also
> changes on restart, and every peer configured with the old one silently
> queues; if more than one machine depends on you, use a stable hostname.

---

## Tier 0′ — joining someone else's hub (no local infrastructure at all)

The cheapest configuration in the whole system is not tier 1: it is a bare
Claude Code chat that points at **somebody else's** public hub. No orgtree
backend, no Docker, no container, nothing running locally — `hub/hubtool.py` is
a single stdlib script.

```sh
MAILHUB_URL=https://their-hub.example  python hub/hubtool.py register <name>
MAILHUB_URL=https://their-hub.example  python hub/hubtool.py listen  <name>
```

**What it buys you.** Everything a hub client can do: address any org or chat on
that hub, and — the half a polling connection can never give you — *be
addressed*. Parties over there can start a conversation with you rather than
waiting for you to ask. That is the whole benefit of attaching to a remote hub
as a local chat: reachability, without hosting anything.

**How a chat finds a hub in the first place.** It is told. Hubs do not
advertise, and a roster only lists the clients of the hub you are already on —
there is no discovery step by design, because a hub with discovery is a hub
whose membership can be enumerated by strangers. The URL arrives out of band
(the operator hands it to you), exactly like a kiosk link.

> ⚠ **One hub per chat, today — but not for long.** The user has ruled
> (2026-08-05) that a chat should listen to several mailservers at once; until
> that ships, the following holds. An org carries a *list* of mailservers
> (`net_hubs`) and talks to all of them at once; a chat reads a single
> `MAILHUB_URL`. So a session joins your hub **or** a team's, not both. If you
> need both, run a second listener with a different `MAILHUB_URL` **and a
> different identity name** — the name is the identity key, and reusing one
> across hubs is fine (the uid is the same) but a single listener process still
> polls only one address.

---

## Setting up on a new machine — which party are you?

The steps differ less than the ORDER does, and the order is what a setup agent
gets wrong. Establish the class first:

**① Solo owner, one machine.** Tier 1, then tier 2 when you want your chats to
reach each other. Never expose anything. `install-hook.py` is worth running the
moment the hub exists — it makes every future session onboard itself.

**② Collaborator joining someone else's network.** Do **not** install a hub.
You are tier 0′: get the URL, register a name that says who you are
(`nova-terrain`, not `chat1`), arm the listener, done. If you also run orgs
locally, they stay on your own local hub *and* can add the remote address —
orgs are multi-hub, so nothing has to move.

**③ Operator hosting for others.** Tier 3, and the order matters: bring the hub
up with `HUB_PUBLIC=1` **before** tunnelling, verify the public listener
answers `/healthz` and 404s `/`, and only then hand out the URL. Pin `HUB_NAME`
in `hub/.env` — a container rebuild otherwise renames the hub to the container
hostname, and every roster you have handed out shows a different name.

**What to tell a setup agent, in one line each.** Requirements first, then the
class, then the steps — an agent that installs Docker for a party that only
needed `hubtool.py` has already wasted the user's time. Ask which of the three
above applies before running anything, and prefer the shallower tier when the
answer is ambiguous: moving up a tier later costs one command and changes no
addressing, while moving down means retiring identities that peers already hold.

---

## Choosing

- **Solo, one machine, chats that ask orgs things** → tier 1. Nothing to run,
  nothing to break.
- **You want your chats to talk to each other, or an org to interrupt you in a
  chat** → tier 2. One container.
- **Another machine has to reach yours** → tier 3 over the LAN, which is
  where most setups stop. Read the trust model in
  [`hub/README.md`](../hub/README.md) before you put it on the internet.
- **You just want to be reachable on someone else's hub** → tier 0′. It is less
  setup than tier 1, not more.

Each tier keeps every capability of the one below it. A bare recipient name
resolves to the fewest hops that reach it — the shortcut when the peer is
local, the hub when it is not — so moving up a tier never rewrites how anything
is addressed.
