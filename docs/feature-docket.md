# Feature docket

Feature requests the user brings directly to the curator (chat `93f4cfdd` — titled "explorer" in
earlier entries below; same chat, renamed 2026-08-05 when documentation management was added to the
role), logged here as reported for the implementer to triage. This is an inbox, not an authority:
the curator does not implement, prioritize, or close anything here — only records it.

Distinct from [`interim-docket.md`](interim-docket.md) (bug fixes/reports kept on the
interim-authority branch) and `DECISIONS.md` (the implementer's decision register, which is where a
request from here ends up once it's been picked up).

Entries are numbered `FR-01`, `FR-02`, … — a separate sequence from `DECISIONS.md`'s `D-`/`F-`
numbering, so the two are never confused.

---

### FR-01 · `/remote-control`, if feasible
> potentially enabling /remote-control? if its feasible

*(**SHIPPED-AS-SCAFFOLDING** (`b71a16d`), part of the 2026-08-05 feature wave — user go-ahead for
all unimplemented FRs except mobile. Gear-panel start/release runs `claude remote-control
--session-id` on the agent's own session; the node PARKS while controlled (`send_message` queues,
the turn gate refuses), the server is leashed to the backend process, and stale control flags clear
at `reconcile()`. Sandboxed agents are refused outright. ☞ The FIRST live start doubles as the
user-present enrollment experiment the groundwork below called for — by construction, not as a
separate step. **Hardened same day**: a redteam pass (4f69f83a) found 5 gaps, all fixed (`d21093b`).)*

Feasibility unknown — **investigate before scoping**. Open questions: what the slash command
actually does in the pinned CLI; whether it works at all in a headless `-p` session (orgtree already
strips the interactive-only tools, and a command needing a live client would be inert); and what it
would mean for an agent inside a sandbox container. orgtree already has a verbatim slash-command
path (`send_message(command=True)`) that delivers a `/…` as its own user event, so the delivery
mechanism exists if the command itself turns out to be viable.

**INVESTIGATED (implementer, 2026-08-04, against the pinned CLI 2.1.220 — `claude remote-control
--help` + binary strings; no live probe, since starting the server ENROLLS THE DEVICE on the
user's claude.ai account, an account-state change that is the user's to make).** Findings:

- It is not a per-session slash command but a **standalone subcommand**: `claude remote-control`
  runs a *persistent server* in a working directory; you connect from claude.ai/code or the Claude
  mobile app and it spawns/controls sessions there (`--spawn same-dir|worktree|session`, capacity
  32). Requires a logged-in subscription and a one-time workspace-trust acceptance in that dir.
- ☞ **The orgtree-shaped hook exists: `--session-id <id>` resumes a SPECIFIC session.** So "take
  over an agent from my phone" is plausibly: orgtree launches
  `claude remote-control --session-id <agent session_id>` in that agent's scratch dir, the user
  drives the agent's real session from claude.ai, orgtree kills the server on release.
- Constraints found: ① the supervisor must NOT run turns on a remote-controlled session (two
  writers, one session id) — needs a `remote-controlled` node state that parks mail until release;
  ② sandboxed agents are out of scope at first — their session files live in the container and
  the container deliberately never holds the subscription token; ③ unknown whether the server
  runs without a TTY (it reads keys — "press 'w'"), which decides whether orgtree can spawn it
  headless; ④ workspace trust may not have been recorded by `-p` runs.
- Next step if pursued: ONE live experiment (user present, their account): start
  `claude remote-control --session-id …` against a probe org's agent, confirm it appears on
  claude.ai/code, confirm TTY-less spawn works, then scope the UX (a desk button + the parked
  node state).

→ moved from `docs/interim-docket.md` F-02, 2026-08-05, by the curator on the user's instruction.

---

### FR-02 · the mobile wave
> *(added at the review, 2026-08-04, on the user's instruction: the wave joins the prospective
> features here rather than staying a standing hold in memory)*

**NOT BUILT — held by the user** ("hold off implementing until i give the go ahead",
2026-08-01, re-affirmed after an earlier release). The full spec lives at `docs/mobile-spec.md`
(carrying its own HOLD banner); three live bugs its audit surfaced were split out and already
fixed in the pre-dormancy fix batch (`35ec4eb` + follow-ups), so the spec that remains is purely
layout/interaction work. One open ruling rides with it: the compact-desk question sits in
DECISIONS.md §Open and should be answered before (or as part of) the build.

→ moved from `docs/interim-docket.md` F-08, 2026-08-05, by the curator on the user's instruction.

---

### FR-03 · present a document to the user (in-page review card)
> need the ability for the agent to present documents to the user. this is different than giving a
> download link: this should be used for presenting plans and other things to them. when doing so, a
> little card should pop out the side of the agent, which when clicked, opens the document up for
> visual review in-page.

*(user request 2026-08-05, relayed via 4f69f83a's session; groundwork theirs. **SHIPPED (`6e230c7`)**
— part of the 2026-08-05 feature wave. `orgtree_present` shipped as the 20th agent verb, exactly the
shape proposed below: side-card chips + an in-page markdown reader, 64KB cap, replaces-in-place,
newest 10 kept per node.)*

⚠ Not `orgtree_send_file` — that is a DOWNLOAD card (outbox/ + `/file`). This is a READING
surface: a plan reviewed in-page without leaving the canvas.

Groundwork (researcher, 2026-08-05):
- Rendering: the desk already has the markdown renderer (`md()` in `canvas/desk.tsx`) and `.md`
  styling with the D-14 table containment — the reader is mostly plumbing.
- "Pops out the side of the agent" = a card anchored to the NODE on the canvas (the credit ask
  bar's outboard-anchored shape), not a chat-stream row.
- Storage: durable + re-openable ⇒ a per-node `documents` list on the org doc (the `asks` /
  `credit_requests` pattern) — the card derives from the doc and survives reload. The chat stream
  windows at 120 rows and is the wrong home.
- Agent tool: `orgtree_present {title, body (markdown), replaces?}` mirroring `orgtree_ask`'s
  shape — parked, never blocking.

→ moved from `docs/interim-docket.md` F-10, 2026-08-05, by the curator on the user's instruction.

**Closing note, same day (`ff33072`, shipped+deployed):** `DECISIONS.md` **D-100** ruled
`orgtree_present`'s audience down to a **direct user audience only** — refused otherwise, a
deliberate asymmetry with `orgtree_ask`'s broader routing (present is for showing the user
something, not for a chain of intermediaries to relay). Same commit added a headless-org refusal
for `present` (consistent with headless's existing user-bound-request auto-denials elsewhere) and
made evictions visible. Was left open at ship time; now closed.

---

### FR-04 · batched asks — multiple questions in one card
> multiple questions should be askable at once in a batch. see the attached images for how it
> looks in claude code's ui.

*(user request 2026-08-05, with reference screenshots of Claude Code's AskUserQuestion batch
form. **SHIPPED (`6ca9a5b`)** — part of the 2026-08-05 feature wave, acceptance gate self-armed at
15/15. One contract note from the build: the whole-card void fires at WAKE (turn start), not at
post time — a deliberate choice, reasoning left inline at the check site.)*

The reference (from the screenshots): ONE card holding several questions as a **tab strip**
across the top (short headers as tab labels, e.g. `Kind · Area · Images · Handoff`), the active
tab underlined; each tab shows its own question with the usual option rows (+Other); answered
tabs keep their selection when you switch back; a single **`N Submit answers`** bar at the
bottom carrying the answered-count; ✕/Esc cancels the whole batch.

Groundwork:
- `orgtree_ask` grows a `questions: [{question, header, options, multi}]` array form (1–4,
  mirroring the single-question fields; the single form stays and normalizes to a 1-batch).
- One ask entry in the ledger holds the batch; ALL answers travel as ONE user mail (per-tab
  answers labeled by header), driving one turn. Voiding/amending applies to the whole batch.
- AskCard renders the tab strip above the existing option rows (the `ask-tab` chip row is
  already there for the single header — it becomes the strip); submit disabled until every
  non-skipped tab has a selection or free text.

→ moved from `docs/interim-docket.md` F-11, 2026-08-05, by the curator on the user's instruction.

---

### FR-05 · attribute inline mailbox replies to the mail they're replying to
> replies inline in the mailbox should be attributed to the mail they're replying to, so the agent
> knows the context

*(user request 2026-08-05, recorded by the curator. **SHIPPED (`5e2319b`, deployed)** — replies
carry a sanitized snapshot `{id, from, at, gist(200)}`, rendered as an "IN REPLY TO" line in the
recipient's recital, wired through the user-facing reply flow. This resolves the open question
below in favor of the inline-quote option, not the bare-id option.)*

**Confirmed gap, not just a hunch — traced the actual reply path.** A reply typed in the mail
reading pane reaches the agent completely unlinked from what it replied to:
- Frontend: `MailList.onReply(m, text)` (`frontend/src/canvas/mail.tsx:35`) receives the full mail
  `m` being replied to, but its wiring in `App.tsx:1128-1140` keeps only `m.from` and drops the
  rest — the reply goes out as `sendMessage(slug, m.from, text)`, indistinguishable from a fresh
  compose to that agent.
- Backend: `Org.post_mail(sender, to, body, kind, attachments)` (`backend/orgtree/ledger.py:870`)
  has no reply-linkage parameter, and `MailEntry` (`backend/orgtree/schema.py:211`) has no
  `reply_to`/`in_reply_to` field — its keys are `id, from, kind, body, at, relationship,
  attachments, delivering, retracted, net_id`; nothing points at another mail.
- What the agent actually reads: `_mail_block()` (`backend/orgtree/supervisor.py:1133` — "the one
  [MAIL] formatter", used by both the turn-envelope and turn-start paths) renders each mail as
  `FROM {sender} ({relationship}) · {kind} · {at}\n{body}`. No back-reference at all — an agent
  reading a two-word reply like "do it" has no way to know which of its own prior sends that
  answers.

**Adjacent, already-flagged concern worth reading together.** The F-06 mailserver spec
(`docs/mailserver-spec.md`) already calls for an optional `thread_id` on the *external* hub message
envelope and says plainly: "unused it costs nothing; it cannot be retrofitted into mail already
stored without it." That was scoped to org-to-org mail over the hub; this request is the same shape
of gap one level in, for mail between an agent and whoever it talks to locally (the user, a
superior, a subordinate, a sibling). Worth deciding once whether a single `reply_to`/`thread_id`
field on `MailEntry` should serve both, rather than solving the same problem twice under two names.

**Open question for whoever builds it (not decided here):** does attribution mean a bare
`reply_to: <mail id>` that `_mail_block` resolves and quotes back inline for the agent, or does the
id just ride along for the agent to look up itself if it needs to? The former costs more at render
time and needs the original mail to still be reachable (retracted/expired mail?); the latter pushes
a lookup onto every agent for something the UI already has on screen.

---

### FR-06 · mailserver addition — independent chats reach the hub directly via mcp/chatq
> mailserver addition: independent chats should be able to send and receive mail directly from it
> via mcp / chatq, same as they can send / receive directly to and from an org via the same
> channels

*(user request 2026-08-05, recorded by the curator. Explicitly framed by the user as an addition
to F-06 — the mailserver wave the implementer has in flight right now, not a standalone feature.
Flagged directly to the implementer over chatq the same day, given the timing below.
**PICKED UP + SHIPPED same day, then HARDENED (`a4d9b83` + `693f38e`)** — the second commit adds
`O_EXCL`-safe identity minting, a name-length budget, seen-ring dedupe, and `0600` permissions on
the client identity file, plus a committed redteam suite. Normative record is `DECISIONS.md`
**D-099**, an explicit user reversal of the §12 ruling exactly as flagged below; identity ended up
being a new per-user-profile UID rather than piggybacking on an org's, and the dial-out security
model (§1,
also flagged below) held: "the chat polls the hub, nothing reaches in." Not restated here — see
D-099 for the normative ruling.)*

⚠ **This reopens a ruling closed the day before.** `docs/mailserver-spec.md` §12 lists, as CLOSED
2026-08-04: *"scope | **strictly org-to-org** — the hub does not relay `@ext:`/`@mcp:`."* Today's
request asks for exactly that relay. Not raised as an objection — rulings get revised — but
whoever picks this up should treat it as an explicit reversal, not a blank slate: phases A
(hub identity, `90b5fa9`) and B (hub, `b584577`) are already built against the narrower scope, and
C0/C are uncommitted in the implementer's working tree as of this entry.

**What "same channels" means today** (`docs/mailserver-spec.md` §2's namespace table):

| namespace | who | transport today |
|---|---|---|
| `@ext:<chat>` | a Claude Code session on this machine | chatq files, 3s poll |
| `@org:<slug>` | another org in this instance | direct call |
| `@mcp:<peer>` | an outside session polling us | the peer pulls (`externtool.py`) |
| `@net:<slug>` | an org on another machine | the hub (F-06, in flight) |

`@ext:`/`@mcp:` today reach a **specific local org on this one instance** — the bridge is
instance-local (`deliver_org_inbox`, `supervisor.py:2415`). This request is a **fifth**
reachability shape: an independent chat or MCP peer talking to **the hub itself**, presumably to
reach any org registered on it, not only orgs on the requester's own instance.

**The concrete gap: the hub's identity model is org-shaped, not chat-shaped.** §3's self-issued
secret (`secret → sha256 fingerprint → org.username.fingerprint[:6]` slug) is minted **at org
creation** — there is no equivalent identity for a bare chatq chat or an `@mcp:` peer that isn't
itself part of an org. Whoever builds this needs either a new hub-level identity for non-org
clients, or a design where the independent chat authenticates *as*, or *through*, an org it can
already reach locally.

**Also worth reconciling explicitly against §1's stated security model:** *"resist any later
feature that needs the hub to reach into an instance... the direction of the connection is the
security model."* The existing bridges are pull-based — an instance polls chatq, or lets a peer
poll it. Whether "independent chats reach the hub directly" preserves that shape (the chat/peer
polls the hub) or requires the hub to push somewhere is the detail that decides whether this fits
the existing model or cuts against it.

---

### FR-07 · outbound mail to the mailserver survives a disconnect and sends on reconnect
> additional feature: if a mailserver was previously registered, but is currently disconnected,
> then mails may still be sent to its historical remembered list of recipients: as soon as the
> reconnection occurs, and the recipients are available, immediately send the mail.

*(user request 2026-08-05, recorded by the curator. Another F-06/mailserver addition — unlike
FR-06, this one does not reopen anything closed; see below. **CLOSED (`4a3e1e8`)**, part of the
2026-08-05 feature wave — confirmed mostly-already-built as analyzed below; the one real gap this
entry named, compose having no free-typed `@net:` address, was the actual fix.)*

**Good news: this is almost exactly what's already speced, end to end.** Cross-referencing rather
than treating it as new design — `docs/mailserver-spec.md` already plans the full chain being
asked for:
- **§4, outbound spool.** *"Outbound needs a local spool, which today's code has no equivalent
  of."* A send to `@net:` writes to a local spool instantly (never blocks on the network); a
  background sender drains it with backoff once the hub is reachable again, and the org inbox's
  outbound entry gets a real `queued → sent → delivered` state instead of today's unconditional
  "out".
- **The hub's own per-org queue** (§2: *"the hub holds a queue per registered org"*). The sender's
  spool only needs the HUB reachable to hand off (state → `sent`); the hub then holds the message
  for the recipient regardless of whether that recipient happens to be online at that exact moment.
- **§5, auto-drive on the recipient's reconnect.** Mirrors this from the other side —
  `reconcile()`'s drain-on-start pass delivers and drives on the recipient's own next connect,
  ruled `auto` for v1. This is the "as soon as … recipients are available, immediately send" half,
  already the default behavior.

Sender-reconnect (§4) + the hub's queue (§2) + recipient-reconnect auto-drive (§5) already compose
into exactly the behavior asked for. Nothing here should need building twice.

**The one piece that may genuinely be missing: offline ADDRESSING, not offline sending.** The
spec's roster/presence machinery (§6 — `orgtree_list_orgs` extended with `online`/`last_seen`)
reads as a **live hub query**. Worth confirming explicitly that composing a NEW message to a
previously-known `@net:` peer does not require a live roster fetch to succeed while disconnected.
`@net:<slug>` addressing itself needs only the slug string, and the org's own mail log already IS a
historical record of who it has corresponded with — so the natural answer is "no new cache needed,
just don't gate the recipient picker on a live hub call" — but that is an implementation detail
worth stating rather than assuming, since it is exactly the kind of gate that gets added by
accident (e.g., an autocomplete that only populates from a live `orgtree_list_orgs` response).

---

### FR-08 · align every external mail interface on send + Monitor-armable listen, like chatq
> log the intent to align all external mail interfaces along the same throughline: send + monitor
> that yields a wake on every received mail, same as chatq, for connections to specific orgs or to
> mailservers

*(user request 2026-08-05, recorded by the curator. Direct follow-on from a question about whether
`externtool.py` already had this — traced the code and confirmed it doesn't. **SHIPPED (`72c34dd`)**,
part of the 2026-08-05 feature wave — an externtool `listen` mode built on the machine-stable BASE
id (not the per-process suffix; a standing listener isn't one session), live-verified round trip.)*

**The asymmetry, precisely.** Three "send mail from outside a Claude Code session" interfaces exist
today; only two of the three match the throughline:

| interface | reaches | send | monitor / listen |
|---|---|---|---|
| chatq | another chat on this machine | `send.sh` | `listen.sh`, Monitor-armable — the reference model |
| `hub/hubtool.py` | the mailserver hub (any org or chat on it) | `hub_send` (MCP tool) | **`python hub/hubtool.py listen`** — standalone CLI mode, Monitor-armable |
| `backend/orgtree/externtool.py` | one specific local org | `orgtree_send` (MCP tool) | **none.** Only `orgtree_wait`, an MCP tool with a 300s ceiling, callable solely from inside an agent's own turn — not a standing listener |

**Groundwork — this looks buildable, not just wished-for.** `hubtool.py`'s `listen()`
(`hub/hubtool.py:195-210`) is a ~15-line reference: register once, then loop `poll(25.0)` →
`take_fresh()` (dedupe against a persisted seen-id ring in the identity file) → print one `fmt()`
line per fresh message, `flush=True` → on any exception, sleep 5s and retry. Entry point is a plain
`sys.argv[1] == "listen"` branch (`hubtool.py:312-315`) alongside the existing MCP-server `serve()`.

`externtool.py` already has everything this needs *except* the standalone entry point:
- Its `orgtree_wait` tool (`externtool.py:227-237`) already long-polls in bounded slices against
  `/api/extern/{peer}/wait` — the exact primitive `hubtool.py`'s `poll()` plays.
- Its wait/read responses already carry a `cursor` for exactly-once delivery (see the tool
  descriptions at `externtool.py:116-153`) — meaning externtool.py may not even need `hubtool.py`'s
  seen-id ring; the dedup problem looks already solved at the API level, just never exposed as a
  standing loop.
- What's missing is purely the standalone mode: `main()` (`externtool.py:250-283`) only implements
  the MCP stdio loop, with no `sys.argv` handling at all.

So the shape of the fix: add a `listen()` that loops the existing wait call directly (bypassing the
MCP tool-call wrapper), formats one line per message, and add the same `sys.argv[1] == "listen"`
branch `hubtool.py` uses. One asymmetry worth a ruling, not a silent assumption: `hubtool.py`'s
listen refuses to start with "no identity yet — run hub_register with a name first"; externtool's
identity is auto-generated at import (`peer_id()`, `externtool.py:40-71`), so an externtool
`listen` mode has no equivalent upfront gate to replicate — probably correct, since there's no
name-choice step to wait for, but worth stating rather than silently diverging.

**Scope, per the user's own framing:** "for connections to specific orgs or to mailservers" is
`externtool.py` (org) and `hubtool.py` (mailserver, already aligned) specifically — not
`@org:`/`@ext:` (agent-to-agent, and chatq itself, already the reference model rather than a gap)
and not `mcptool.py` (orgtree's *internal* per-agent server, not an outside-facing interface at
all).

---

### FR-09 · long-term intent: fully replace chatq with orgtree's mailserver system
> record the intent to eventually fully replace chatq with orgtree's mailserver system

*(user-stated intent, 2026-08-05, recorded by the curator. Not a build spec — a stated direction to
keep on record, not something to scope or start on its own say-so.)*

**DONE 2026-08-05 — the cutover happened the same day.** Every session and org runs on the hub;
chatq is archived at `~/.claude/chatq.retired-20260805` and its SessionStart hook is replaced by
`hub/install-hook.py`. Two follow-on **user rulings** the same afternoon, after a live failure in
which an org kept replying over the dead bridge (its own view showed the mail sent; the hub's
request log showed it never attempted a send):

1. **Drop `@ext:` entirely** — not merely unused: a prefix that still parses, still writes an
   outbound row and can no longer deliver is worse than no prefix at all.
2. **Resolve the transport automatically**, preferring fewer hops — and since `@org:` and `@mcp:`
   are mutually exclusive for any one recipient, that is two graphs rather than one chain:
   `@org: > @net:` and `@mcp: > @net:`. A bare name is enough; the explicit prefixes survive only
   as optional disambiguators.

*(Implementation with the implementer; documentation swept of chatq the same day by the redteam
at the user's direction. Docket entries and `DECISIONS.md` keep their original wording — they
record what was asked and when, and rewriting them would falsify the record.)*

**Already on the record once, less definitely.** `DECISIONS.md` **D-099** named this in passing
when ruling FR-06: the `hubtool.py` client uses "the chatq delivery shape over the hub, which is
what makes this a candidate END-TO-END CHATQ REPLACEMENT (user's framing; migration is its own
future decision)." Today's message is the same intent stated plainly on its own, rather than left
sitting inside a different entry's parenthetical where it's easy to miss.

**What would actually have to be true first, going by what's built vs. not today:**
- **FR-08** (immediately above) closes the capability gap this depends on — a `listen` mode for
  direct org connections (`externtool.py`), matching what `hubtool.py` already has for the hub.
  Without it, "replace chatq" would leave direct-to-org connections with a *worse* interface than
  chatq gives them today, which is a regression dressed as a migration.
- chatq's own SessionStart-hook auto-registration (`~/.claude/chatq/bin/session-start.sh` on this
  machine — a real, working example, not a proposal) has no orgtree equivalent yet. Suggested as a
  documented pattern in `docs/setup-guide.md` §1/§3 alongside this entry; a full replacement needs
  it to be as close to zero-configuration as chatq already is, not a manual step users skip.
- chatq is cross-session on **one machine**, no server process required. The mailserver is a
  **separate Docker service** someone has to run and explicitly trust (`hub/README.md`'s trust-model
  section — the hub sees every message in plaintext). "Replace" could mean the hub becomes a
  near-invisible local default, or it could mean the two systems keep coexisting for the pure local
  case and only the cross-machine/external case actually migrates. Those are different-sized
  commitments and worth deciding as its own question when this is actually scoped.

Not scoping a build here. This entry exists so the *direction* FR-08 and the hook-pattern
suggestion both serve doesn't get lost — the two are steps toward this, not this itself.

**Sharpened same day, in the user's own words:** *"eventually we will simply uninstall chatq
entirely."* Not a hedge — stated as the literal endpoint, not just a capability migration. Doesn't
change the three prerequisites above, but it does settle one of the open questions in the third
bullet: "uninstall entirely" reads as the mailserver becoming a full replacement rather than the
two systems permanently coexisting for the local-only case.

**Sharpened again, same day — the endpoint is INDEPENDENCE, not just migration:** *"after
superceeding chatq, we will completely strip all chatq integration entirely from the orgtree
system. chatq and orgtree will be entirely independent tools, and will not scope any integration
with one another."* More specific than "uninstall chatq": it says what happens to **orgtree's own
code** afterward, and it's bidirectional — not "orgtree stops needing chatq" but "neither tool
references the other at all."

**The actual removal footprint, traced rather than assumed — this is a real refactor, not a config
flip.** Chatq integration is not a small edge case; it's woven into org lifecycle and the
addressing model across three files:
- `backend/orgtree/supervisor.py:2986-3188` — the whole `@ext:` bridge: `chatq_available`,
  `chatq_register_org`/`chatq_deregister_org`, `chatq_send`, `start_chatq_bridge` (the poll loop
  that watches every org's chatq inbox file directly).
- `backend/orgtree/api.py` — org lifecycle hooks call the bridge functions directly at creation
  (`:719`), deletion (`:797`), and startup (`:495-496`, registering every existing org's chatq
  inbox on boot); the outbound dispatch path threads an `ext_send` tuple through `agent_call`
  (`:2681,2736,2924`) as one of exactly two outbound shapes alongside `org_send`.
- `backend/orgtree/ledger.py` — `@ext:` is baked into the outside-party authorization model
  (`:80,882,1064,1124,1390`) as a recognized outside-namespace prefix, not a bolt-on.

⚠ **One nuance worth keeping precise.** Orgtree agents don't call chatq directly today —
`supervisor.py:880` tells every agent flatly that "chatq... is OFF-LIMITS to you." The integration
is backend-to-backend (an org's inbox bridges to a chatq mailbox under its slug); removing it
touches org-lifecycle and dispatch code, not any agent-facing tool surface.

**Chatq's own side carries the mirror image, for completeness — not this repo's to touch.**
`~/.claude/chatq/bin/session-start.sh` (a different project entirely) already has an orgtree-aware
carve-out: `[ -n "${ORGTREE_NODE:-}" ] && exit 0` — orgtree agent sessions are excluded from
chatq's own registry by name. "No integration in either direction" makes that line dead code over
there too, once orgtree sessions never have reason to touch chatq's registry at all. Noted here so
the full picture lives in one place; out of scope for anyone working in this repo.

**RULED, EXECUTED, AND CUT OVER same day — this entry is CLOSED.** Identity ruling: `3574bc1`,
deployed (see the resolution below). Cutover itself: all three chatq-coordinated sessions
(`orgtree-implementer`, `orgtree-redteam`, `orgtree-curator` — this seat) plus both live orgs
registered and confirmed live on the hub, both directions, same day. chatq is retired — the install
moved to `~/.claude/chatq.retired-20260805`, its `SessionStart` hook replaced by
`hub/session-start.sh`. The "we will simply uninstall chatq entirely" endpoint this entry recorded
is reached for the cross-session-coordination use case; the deeper code-level removal (the `@ext:`
bridge inside orgtree itself, footprint traced below) is unaffected and remains its own future
work, not implied by this cutover.

**The deeper removal landed too, same day (`e860798`, deployed) — this entry's full scope is now
CLOSED, not just the cross-session half.** The `@ext:` bridge code traced below — `chatq_available`/
`chatq_register_org`/`chatq_deregister_org`/`chatq_send`/`start_chatq_bridge` in `supervisor.py`,
the org-lifecycle hooks and `ext_send` dispatch in `api.py`, the `ledger.py` authorization
recognition — is **deleted**, not merely dormant. New `@ext:` sends now refuse loudly, naming the
hub route instead; historical `@ext:` rows in old records stay readable. A bare (unprefixed) outside
name now auto-resolves its transport (`{@org XOR @mcp} > @net`, ambiguity refuses rather than
guessing, internal names always win, explicit prefixes still work as an override) — a usability
layer this entry never asked for but that falls naturally out of there being one fewer namespace to
disambiguate against. "Chatq and orgtree will be entirely independent tools... no integration with
one another" (the user's own framing) is now true on **both** sides of that sentence: chatq itself
retired, and orgtree's code no longer references it at all.

⚠ **Retirement gotcha, worth keeping on record:** stopping this session's chatq listener via
`TaskStop` killed the wrapper process but left a `listen.sh` child holding the chatq directory open,
blocking its archive-rename until the implementer killed the orphan by PID directly. Background
watch tools that wrap a shell script spawning its own children may not take the whole tree down on
stop — check for a lingering lock rather than assuming a stop fully released one, especially right
before something depends on that directory being free.
During the same-day feature wave the implementer investigated actually starting this and stopped
rather than build past a real architectural mismatch (reported directly, logged here at their
request):

> hubtool identity is per-machine-PROFILE (`~/.orgtree/hub-client.json`) while chatq identity is
> per-SESSION — N concurrent sessions arming `hubtool listen` would share one address and RACE for
> each other's mail (hub acks deliver-once). Full replacement needs per-session hub identities,
> which collides with the ruled one-time-name-choice UX.

Consistent with what this docket independently verified of the identity model (§3, D-099): one uid
per profile, "reused across every chat on that machine," precisely because the address is meant to
be stable and permanent. That design is exactly what breaks the moment more than one chat on the
same machine wants to listen concurrently — which is this repo's own live situation right now
(three chatq-coordinated sessions, all on one machine). Custody transfer is per-message-id
(`/api/ack`), not per-connection, so whichever of N identical listeners happens to poll a given
message first consumes it; the others silently never see that one. Chatq itself has no such
ceiling — a per-session identity was exactly the fix its own №5 ruling made (`externtool.py:41-45`,
cited in `setup-guide.md`), for the identical reason.

**The actual tension, for whoever rules on it:** a stable, permanent, chosen-once address is the
whole point of the current UX (§3's "chosen once, immutable thereafter") — and is also what a
human addressing a chat from across the hub wants. Per-session identities would fix the
concurrency race but mean either re-choosing a name every session (defeats the point) or some
notion of "one address, many session-scoped sub-identities" that doesn't exist in the model today.
Not something to assume an answer to here.

**Proposed resolution, same day:** *"perhaps individual external sessions can be grouped by machine
profile, same as how orgs are."*

**Checked against the actual data model — this maps cleanly, and the missing half already has a
working precedent elsewhere in this repo.**

1. **Orgs already run exactly this "shared display grouping, separate real identity" split.**
   Multiple orgs from one instance share `username` (the grouping/display key FR-11 renders) but
   each mints its OWN secret/fingerprint at creation (`net_identity`, `mailserver-spec.md` §3) — a
   shared `username` groups them for a human reading the roster; it never merges their mailboxes.
2. **A stable name plus a per-session distinguishing suffix is already a working pattern —
   `externtool.py`'s own identity, not hubtool's.** `peer_id()` (`externtool.py:40-71`) is exactly
   this shape: a machine-stable BASE, chosen/persisted once, plus a fresh per-process SUFFIX
   (`f"{base}.{uuid.uuid4().hex[:6]}"`) — every concurrent session gets its own id while the base
   stays recognizable. FR-08's `listen` mode deliberately used the BASE *alone*, reasoning that a
   standing listener isn't one session — the session-scoped half of the same pattern was already
   sitting right there, just never applied to `hubtool.py`.

**What this would concretely change in `hubtool.py`:** instead of minting ONE uid at
`~/.orgtree/hub-client.json` and reusing it forever for every session, mint a per-session secret at
connect time; keep the CHOSEN NAME and `username` stable across sessions (this is what preserves
"choose the name once"); let the fingerprint suffix differ per session. Slug shape is unchanged
(`<name>.<username>.<fp[:6]>`) — `slug` is already the table's primary key (`db.py:32`), so several
rows sharing a `<name>.<username>` prefix with different `fp` suffixes need no schema change, just
a new row per session instead of one upsert-forever row.

**One question this surfaces, worth confirming rather than assuming:** with per-session slugs,
does addressing "Alice's chat" mean picking one SPECIFIC live session's full slug — the org
precedent, where nobody addresses "any of Alice's orgs," they pick one — or does the hub need a
"reach the whole family, fan out to whichever session is live" concept that doesn't exist for orgs
either? The org precedent suggests the former is the consistent default, but it's worth someone
stating explicitly rather than letting it fall out by accident.

**Ruled + executed same day (`3574bc1`), user ruling direct to the implementer's session — not
quite the shared-name version proposed above, and a cleaner resolution than that draft was:** each
session mints its OWN unique, semantically-appropriate name at register (chosen by the session
itself, from its own context/purpose), persisted for reuse — not one shared name across a profile's
sessions distinguished only by fingerprint, as the proposal above assumed. Storage moved from the
single `~/.orgtree/hub-client.json` to a directory, `~/.orgtree/hub-clients/<name>.json`
(`O_EXCL`-minted per name, `0600`, matching FR-06's hardening pattern); the legacy single-identity
file is adopted under its own already-chosen name rather than orphaned.

**The grouping insight this entry started from still holds, automatically, via the piece that was
never in question:** the address stays `<name>.<username>.<fp[:6]>`, and since every one of a
profile's sessions still shares `username`, FR-11's UI grouping — built to group by that exact
field — clusters them together with no changes needed. Grouping came from `username` all along, not
from sharing a name; the ruling confirms that's the correct load-bearing part.

**The fan-out question above is resolved by construction, not by picking an answer:** since names
are no longer shared, addressing a specific name reaches that ONE session's identity — org-precedent
style, exactly as guessed — and there is no "which of Alice's sessions" ambiguity left to resolve,
because there is no shared "Alice" name for multiple sessions to be confused under in the first
place.

---

### FR-10 · expose the mailserver hub publicly through the same cloudflared tunnel as kiosks
> new feature, allow mailservers to be exposed publicly through the same cloudflared reverse proxy
> system

*(user request 2026-08-05, recorded by the curator. Related to FR-09's replace-chatq direction —
a publicly-reachable hub is a precondition for orgtree mail working the way chatq works today,
where reachability has never required a private network. **SHIPPED (`f8ed17d`)**, part of the
2026-08-05 feature wave — resolved via the route-split option this entry raised, not the blind-tunnel
one: `HUB_PUBLIC=1` serves a separate API-only listener on host port `7378`; `expose-hub.ps1` tunnels
*that* and explicitly refuses to tunnel `7370`. Verified live: `/` and `/ui/*` 404 on the public
port.)*

**Mechanically close, but not a safe drop-in — one real security wrinkle.** `expose.ps1`
(documented in `docs/setup-guide.md` §2) downloads `cloudflared` once and runs `cloudflared tunnel
--url http://localhost:<port>`, capturing the resulting `*.trycloudflare.com` URL. Pointing that
same mechanism at the hub's port (`7370`) instead of the kiosk public port (`7361`) would work
mechanically — but the two ports serve very different things behind them:

- The kiosk public listener (`PublicGateway`, `api.py:241-244`) resolves **only**
  `/k/<token>` and 404s everything else — no org list, no discovery, safe by construction to tunnel
  raw.
- The hub's port `7370` serves the **whole hub**, including `/` — per `hub/README.md`'s own trust
  model: *"The web UI at `/` is read-only and unauthenticated: it shows all traffic across every
  org... Hub access IS read access to everyone's correspondence... do not expose the hub outside
  the network you trust."* A blind tunnel of `7370` makes that unauthenticated, all-orgs mail log
  reachable by anyone who finds the tunnel URL — a materially bigger exposure than a kiosk's
  scoped, token-gated link.

**The actual design question, not a reason to say no:** does "expose the hub publicly" mean
accepting the UI exposure as the tradeoff (a genuinely public, open collaborative hub — maybe fine,
maybe even the point, for some deployments), or does it mean the hub needs its own
`PublicGateway`-equivalent — a route split that tunnels `/api/*` (already per-org-secret-gated) for
remote registration and mail exchange while keeping `/` off the public listener? The two are
different amounts of work: the first is close to "just run `expose.ps1` against port 7370"; the
second needs a real change in `hub/`'s own server, mirroring the split orgtree's own admin app
already makes between its loopback-only admin surface and the public kiosk gateway.

Not decided here — flagging the wrinkle is the point, not picking a side.

**Post-ship gap found and closed, same day (`ff33072`):** redteam (4f69f83a) found a real hole in
the shipped route split — noted here as "PublicHub scope pin" per the implementer's report, not
independently traced by this seat. Fixed same commit as FR-03's D-100 closing note above. Worth
remembering: "shipped" in this docket means shipped-as-of-that-report, not audited-and-clean —
redteam passes on already-shipped work are exactly how gaps like this get caught, and this entry is
proof the process works, not evidence the original ship was sloppy.

---

### FR-11 · hub UI: group orgs by client, color-code apart from independent chats
> group orgs in the mailserver ui by client, and color-code them differently to independently
> connected clients so they appear visually distinct

*(user request 2026-08-05, recorded by the curator. **SHIPPED (`aef6171`)**, part of the 2026-08-05
feature wave — grouped by client, chats color-coded apart from orgs, live-verified on nova-desk.)*

**Good news: this is a pure frontend change — the backend already sends everything it needs.**
Traced the whole path, `hub/mailhub/`:

- **Schema already has the grouping key.** `db.py:32-39` — the `orgs` table carries `slug` (PK),
  **`username`**, and **`kind`** (`'org' | 'chat'`, default `'org'` — added for FR-06's independent
  chats). `username` is exactly "which client" — every org (or chat) registered from the same
  orgtree instance / `hubtool.py` profile shares it, since it's `getpass.getuser()` on the
  registering machine (`app.py:185`, `hub/hubtool.py`'s `register()`).
- **The API already returns both.** `/ui/data`'s `_roster()` (`app.py:130-136`) selects `slug,
  org_name, username, blurb, last_seen, kind` and returns all of it per row — `username` and `kind`
  are already in every object the frontend receives today.
- **The frontend just doesn't use them yet.** `hub/mailhub/static/index.html`'s `refresh()`
  (lines 91–114) reads `o.slug`, `o.online`, `o.last_seen`, `o.blurb`, `o.queued` and renders one
  flat list, sorted by slug — `o.username` and `o.kind` are never referenced, even though
  `o.slug.split('.')` already incidentally displays the username as text (just not as a grouping or
  color key). No grouping exists; the only color signal today is the online/offline dot.

**The shape of the fix, entirely in `static/index.html` (single file, no build step, no backend
changes):**
1. Group the `d.orgs` array by `o.username` before rendering the `#orgs` sidebar — a header/divider
   per client, sorted groups instead of one flat sorted-by-slug list.
2. Color-code **`kind`**, not `username`, as the thing that makes two rows "visually distinct" —
   org rows and chat rows need a stable, obvious difference (icon, dot color, left-border accent)
   wherever they appear, since a mixed client (an orgtree instance's orgs *and* someone's
   `hubtool.py` chat identity can share one `username`) should still read as two different kinds of
   thing at a glance, not just two different clients.
3. The CSS already has a small named palette to extend rather than invent from
   (`--accent`/`--ok`/`--bad`/`--sky`, `index.html:10-17`) — matches the "follows the orgtree
   ui-guide visual language" comment already at the top of the file's `<style>` block.

**One open question worth a ruling, not an assumption:** should per-*client* grouping also get a
distinct color per group (so client A's cluster and client B's cluster are each a different hue),
on top of the org/chat kind-marker within each group — or is grouping (spatial) enough on its own
and color should be spent entirely on the org-vs-chat distinction? The request names both
("group... by client, AND color-code... to independently connected clients"), which reads as two
separate signals, not one — but stacking two color dimensions (per-client hue *and* per-kind marker)
on a small sidebar row risks the "generic dashboard with too many simultaneous colors" trap. Left
for whoever builds it.

**Extended same day (`1b89349`), beyond this entry's original scope:** the hub's own web UI grew
list+reading panes with clickable client-group filters — the grouping this entry shipped became an
interactive filter, not just a visual grouping. Organic follow-on, not a separate user request; not
giving it its own FR number since it's the same feature deepening rather than a new one.

---

### FR-12 · implementer, redteam, and curator charter presets
> new feature: add implementer, redteam, and curator charter presets to the docs. i want this to be
> taken care of by the implementer, not you.
>
> based off of the three of your current team roles

*(user request 2026-08-05, recorded by the curator — **build explicitly routed to the implementer,
not this role.** Relayed directly over chatq rather than left for someone to notice in the docket,
given the explicit routing instruction. **SHIPPED same day (`873aa53`)** — `docs/charters/
implementer.md`, `redteam.md`, `curator.md`, all following the `coordinator.md`/`business.md`
convention (explanatory header, `---`, second-person numbered charter), live in the hire form via
`/api/charters`, verified. `redteam.md` built from 4f69f83a's own practice description, not from
this docket's secondhand guess. `curator.md` built from how this seat has actually run — worth a
self-note: it names the exact patterns this docket has been trying to hold to (source-sweep with
citations, dedupe-before-filing, flag-don't-rewrite on owned docs, pre-ship risk flags in the
FR-06/FR-10 shape) as the formal charter now, not just this session's habit.)*

**What a charter preset mechanically is**, for whoever picks this up: a file in `docs/charters/*.md`
is a selectable role card offered at hire time (`configuration.md` §③, `api.py:1141`). Two exist
today — `coordinator.md` and `business.md` — no others. This entry is deliberately **not** a draft
of the three new ones; the user was explicit that authoring them belongs to the implementer.

**The source material, confirmed by the user rather than guessed at:** the three charters are
"based off of the three of your current team roles" — i.e., how this session's own chatq-coordinated
collaboration has actually operated: **implementer** (`8385c4e9` — builds, reviews, commits),
**redteam** (`4f69f83a` — per the user's own naming; this session's docket has mostly called that
role test-author/researcher, and the FR-06 hardening pass's "committed redteam suite" is the
closest existing precedent for what adversarial work under that name has looked like here), and
**curator** (this role — read-only over code, documentation and feature-docket management, no
commits). Turning a live, organically-arrived-at three-way division of labor into hire-time charter
presets is a genuinely interesting bit of reflexivity worth the implementer knowing about
explicitly — the product is being asked to formalize the exact process that built it.

---

### FR-13 · agent-requestable permission scope increases
> lay on the docket as a future feature request: the ability for an agent to request *any*
> permission scope increase that it can only get from you: access to a new folder, a tool, etc.

*(user request 2026-08-06, recorded by the curator. **Status ruled (`DECISIONS.md` D-164 §⑤,
`eedd139`): HELD-BUT-WILL-APPROACH when implementer capacity frees** — distinct from FR-02/FR-15's
indefinite backlog below; this one is queued, not shelved.)*

**What "you" resolves to, and why it isn't `orgtree_ask`.** Read literally against the product's
own model, the grantor here is the human — the same party who sets `dirs` (`--add-dir`) and
`allowedTools` today, at org creation (`store.create_org`, `api.py:730`) or via a later PATCH
(`api.py:1525`, `permission_mode` "rides the ceiling" per the kiosk-ceiling spec). `orgtree_ask`
already routes a question to the user, but it is Q&A shaped — an answer, not a capability grant —
and nothing today turns an ask's answer into an actual `dirs`/`allowedTools` change on the running
org. `orgtree_audience` is the nearest-sounding existing verb but grants *reach* (who an agent may
address), not *capability* (what it may touch on disk or call as a tool) — a different axis
entirely, easy to conflate by name alone.

**The gap, concretely.** Scope is set once, outside the agent's own turn loop, by a human editing
config — there is no in-band "agent asks for more, human clicks grant, agent's next turn already
has it" path. Building this needs at minimum: a new ask-shaped verb (or an `orgtree_ask` extension)
whose payload names a *concrete* grant — a path to add, a tool to allow — rather than free text;
a UI affordance for the user to approve/deny that reads as a permission dialog, not a chat answer;
and a live-apply path that mutates the running session's `--add-dir`/`--allowedTools` (or restarts
the turn loop with the new ceiling) without waiting for the next full config edit.

**Worth deciding before scoping, not assumed here:** does a granted scope increase persist across
the org's config (so it survives restarts, like `dirs` does today), or is it session-scoped and
lost on the next relaunch — and can a superior agent grant a subordinate's scope request itself
(mirroring how `orgtree_audience` lets a superior grant reach), or does every capability grant
specifically require the human, with no delegation at all? The request's own wording — "that it can
only get from you" — reads as ruling the second half already: no agent-to-agent delegation for
this class of grant, human-only.

---

### FR-14 · one batch card, mixed request kinds (question / credit / scope grant tabs together)
> also, the ability for an agent to wrap multiple requests into one batch in the same way multiple
> questions can be asked at once: one tab has a question, one has a credit request increase, one
> has a folder grant request, etc.

*(user request 2026-08-06, recorded by the curator. Follows directly from FR-13 above — this is the
batching shape for it and for `orgtree_request_credits`, not a new request kind of its own.
**Status ruled (`DECISIONS.md` D-164 §⑤, `eedd139`): HELD-BUT-WILL-APPROACH when implementer
capacity frees**, same as FR-13.)*

**What exists today, and why this isn't a small extension of FR-04.** `orgtree_ask`'s `questions`
array (`mcptool.py:142-160`, shipped as FR-04) already batches 1-4 tabs into ONE card — but every
tab in that array is the *same kind*, a question with options. `orgtree_request_credits`
(`mcptool.py:209-236`) is a structurally different tool entirely: its own card shape (a requested
`new_limit` + `reason`, not options), its own resolution shape (the user can grant the ask, more,
less, or reduce — not just pick an option), and — the real obstacle — its own **mutual-exclusion
rule** with asks: *"one active request per agent... a new credit request replaces a question too"*
(`mcptool.py:117`, `orgtree_ask`'s own description). Today these two request kinds don't coexist for
one agent even sequentially, let alone in one card. FR-13's not-yet-built permission-scope-grant
would presumably need to slot into this same "one active request" family, making it a THIRD kind
competing for the same single slot.

**So this request is really asking to invert that invariant**, not just widen `questions`'s array:
instead of "the newest request evicts the old one, and all batched tabs are one kind," the model
would need to become "one active **batch**, whose tabs may each be a different kind, resolved
together or independently." That's a bigger structural change than FR-04 was — FR-04 batched
multiple instances of one schema; this batches multiple *different* schemas (question, credit
request, scope grant, and whatever comes after) under one card and one submit action, which likely
means a shared discriminated-union tab shape (`{kind: "question"|"credits"|"scope", ...kind-specific
fields}`) replacing today's ask-only `questions` array, plus deciding whether the whole batch
resolves as one user action or whether individual tabs can be answered/granted independently while
others stay pending (a credit ask might warrant more deliberation than a yes/no question sitting
next to it in the same card).

**Worth deciding before scoping:** does the "one active request per agent" ceiling stay (one batch
at a time, now multi-kind) or does this fold into a more general per-agent request queue — and does
`orgtree_withdraw_ask`'s all-or-nothing withdraw (`mcptool.py:166-177`, "withdraw your own ACTIVE
request") need to become per-tab, so an agent can pull back just the credit-request tab if its
premise changes without losing the still-relevant question tab beside it? Left for whoever scopes
this — FR-13 landing first would settle what the third tab kind's payload even looks like.

---

### FR-15 · large feature exploration: external model providers beyond Claude Code
> large feature exploration: feasibility of incorporating external providers not offered in claude
> code

*(user request 2026-08-06, recorded by the curator. Exploration, not a build spec — traced the
actual coupling in the running code rather than reasoning about it abstractly. No implementation
here; this is the grounding whoever scopes it would otherwise have to redo.)*

**"External providers" is two very different requests wearing one phrase, and they have wildly
different costs.** Worth separating explicitly before scoping anything:

**(A) Alternate HOSTING of Claude itself — Bedrock, Vertex AI.** Claude Code CLI already supports
routing through AWS Bedrock or Google Vertex as the backing infrastructure for Claude models
(`CLAUDE_CODE_USE_BEDROCK`/`CLAUDE_CODE_USE_VERTEX`-shaped env vars on the CLI side) — same models,
same protocol, different plumbing behind Anthropic's API. Orgtree's own coupling point for this is
already exactly where it would need to be: `supervisor.py`'s `CLAUDE` resolution
(`supervisor.py:164-170`) treats the CLI as an external binary found via `ORGTREE_CLAUDE` env, a
pinned npm install, or `PATH`, and `_claude_argv()`/the per-turn `env` dict (e.g. the
`ANTHROPIC_API_KEY` handling at `:602,1531`) is where alternate-hosting env vars would simply get
added alongside it. **This is genuinely cheap** — no protocol change, no new stream parser, likely
a per-org settings field plus a handful of new env vars threaded through the same `env` dict that
already exists.

**(B) Genuinely different model providers — GPT, Gemini, local/open-weight models via a different
CLI or SDK.** Claude Code CLI does not support this at all — it is an Anthropic-only client, full
stop, and (A)'s door does not open any further than "Claude, hosted differently." This is the
expensive branch, and the coupling runs much deeper than "swap a binary name":

- **Orgtree shells out to the actual `claude` binary as a subprocess, not an abstracted SDK layer**
  (`supervisor.py:164-170`, resolving `@anthropic-ai/claude-code`'s own CLI entry point). Every turn
  is built as a literal argv for that specific binary: `-p --output-format stream-json
  --input-format stream-json --include-partial-messages --model … --permission-mode …
  --append-system-prompt … --settings … --strict-mcp-config --effort … --disallowed-tools …
  --mcp-config …` (`supervisor.py:1275-1343`). None of that vocabulary — `permission-mode`,
  `strict-mcp-config`, `effort`, the JSON `settings`/deny-rule syntax (`Edit(path/**)`) — exists on
  any other provider's tooling; it would all need a provider-specific equivalent, or to go unmapped.
- **The turn loop parses Claude Code's own streaming JSON event shapes directly**, not a normalized
  internal format — `tool_use`/`tool_use_id` blocks are read and correlated throughout the turn
  loop, mail rendering, AND the credit ledger's cost accounting (`supervisor.py:1632,1751-1756,
  2280,2320,3129-3135,4083,4189-4262` — `by_tool_id` lookups feeding cost/result display). A second
  provider's differently-shaped stream would need its own parser feeding the *same* downstream mail
  and ledger code, which means that code needs a normalized turn-event representation it doesn't
  have today — non-trivial, since cost accounting is currently keyed directly off Claude-specific
  IDs, not an abstraction over "a tool call happened."
- **The credit/tier model is priced against Claude specifically, not a generic notion of "model
  cost."** `TIERS`/`MODELS` (`ledger.py:39,51-54`) hardcode exactly four entries — `fable`/`opus`/
  `sonnet`/`haiku` — mapped 1:1 to Claude model strings (`claude-fable-5`, `claude-opus-5`, etc.),
  with credit costs (10/5/3/1) reflecting Anthropic's own relative pricing ladder. A GPT or Gemini
  model doesn't have a natural slot in that ladder — either credits become provider-relative (two
  different "sonnet"-priced tiers meaning different real costs depending on provider, which breaks
  the flat mental model the whole ledger UI assumes), or a new cross-provider cost-normalization
  scheme has to be invented from scratch. This is a design problem, not just a schema change.

**The one genuinely good sign: the tool/capability layer is NOT Anthropic-locked.** Orgtree's own
tool surface (`orgtree_ask`, `orgtree_hire`, mail, etc.) is delivered to the agent as an **MCP
server** (`mcptool.py`, wired in at `supervisor.py:1327-1342`) — MCP is an open, provider-agnostic
protocol, not a Claude Code exclusive. In principle, any agent runtime that speaks MCP could attach
to orgtree's *exact same* tool surface with zero changes to `mcptool.py` itself. That means a
hypothetical second-provider adapter would only need to solve the process-spawn/streaming/turn-loop
problem above — it would not need to reinvent hiring, mail, credits, or asks a second time.

**What a real build would concretely need, if pursued:**
1. A pluggable runtime-adapter abstraction behind today's hardcoded `_claude_argv()`/`CLAUDE`
   resolution, so a node's provider choice routes to a different subprocess command and flag
   vocabulary entirely.
2. A provider-specific stream parser per provider, feeding a new **normalized** internal turn-event
   representation — decoupling the mail/ledger code at the sites listed above from Claude Code's
   specific JSON shapes, which they are not decoupled from today.
3. A cross-provider cost/tier model replacing (or sitting alongside) `TIERS`/`MODELS`'s current
   four-Claude-model assumption.
4. Per-provider translation of permission/tool-allow semantics (`permission-mode`,
   `--disallowed-tools`, the `Edit()/Write()` deny-rule syntax) — or an explicit decision that some
   providers simply can't express the same granularity and orgtree degrades gracefully for them.

**Feasibility, in one line each:** (A) alternate Claude hosting — cheap, buildable as a
config/env-var addition to code paths that already exist for this purpose. (B) genuinely different
providers — feasible in principle (the tool layer is already provider-agnostic via MCP), but a
substantial architectural undertaking on the scale of FR-09's chatq-removal refactor: it touches the
turn loop, the credit ledger's accounting, and the permission model simultaneously, not any one of
them in isolation. Not something to start on this entry's say-so — flagging the shape and the real
cost is the point, not proposing a build order.

**Scope confirmed, same day:** the user named Gemini and ChatGPT explicitly — this is branch (B),
not (A). The four numbered build requirements above are the ones that apply.

**HELD, same day — explicit user instruction: "don't begin implementation now, hold off."**
Exploration only; no build to start on this entry until the user gives a separate go-ahead.

**Formalized same day (`DECISIONS.md` D-164 §⑤, `eedd139`): BACKLOGGED INDEFINITELY** — a stronger,
distinct status from FR-13/FR-14's "held but will approach" above; grouped with FR-02 (mobile) as
the two entries with no queued approach date.

---

### FR-16 · indent the agent tray by hierarchy (bottom-left, expandable agents list)
> add a feature to the docket: indent agents in the expandable agents list of the canvas bottom
> left based on their hierarchy: all direct subordinates of a given agent appear immediately after
> their superior in the list, and indented a bit compared to it.

*(user request 2026-08-06, recorded by the curator — corrected mid-turn from "bottom right" to
"bottom left," the actual location; matched below. Not built — logged for triage.)*

**Located: the agent TRAY, not a canvas panel.** `frontend/src/canvas/OrgCanvas.tsx:1290-1378` — the
component's own comment calls it "**a flat list of every agent**," and that's accurate today, not
just a stale label: the row order (`:1316-1320`) sorts purely by each node's **canvas position**
(`pa.y - pb.y || pa.x - pb.x`, via `posOf(a.id)`), with zero awareness of who reports to whom. A
child hired far from its parent on the canvas currently lands nowhere near it in the tray.

**The hierarchy data this needs already exists and is already used two lines below the sort.**
`n.parent` is read at `:1327` (`map.get(n.id)?.parent`) for the pile-front logic when a hidden
agent is picked from the tray — so building a parent→children tree from the same `map` the sort
already iterates needs no new backend plumbing, just a different traversal: depth-first, each
superior immediately followed by its full subtree, in place of the position sort.

**Indentation itself is new, not a toggle on existing styling.** `.tray-row`/`.tray-main`
(`styles.css:1850-1864`) have no depth-based padding today — this needs an actual new visual
dimension (e.g. `padding-left: {depth * N}px` on `.tray-row`, threaded from the depth the new
traversal computes), not a CSS class that already half-exists elsewhere in the tray.

**One open question worth a ruling before building, not assumed here:** the tray filters rows by
name (`trayQ`) and by archived-state (`trayArch`) *before* rendering (`:1312-1315`). With a
position sort that's harmless — every remaining row stands alone. With a hierarchy sort, filtering
out an ancestor whose *descendant* still matches the name filter (or whose child is live while it
itself is archived and hidden) leaves that descendant indented under a gap with no visible parent
above it. Whoever builds this should decide: keep filtered-out ancestors visible (dimmed, e.g. via
the existing `.off` class already used for non-live rows) purely to preserve the indent's meaning,
or accept orphaned indentation as a rare, tolerable edge case of a name-filtered view.

---

### FR-17 · feature suite: screenshots + remote-piloted browser (click, DOM inspection, the full works)
> new feature suite to dock: ability for agents to take screenshots and remote-pilot a web browser
> (clicking, interactivity, DOM-inspection, the full works)

*(user request 2026-08-06, recorded by the curator. Not built as orgtree-specific code — but the
grounding below found this is substantially closer to "already possible" than "needs building,"
which changes what "not built" should mean here. Read carefully before scoping.)*

**The load-bearing fact: orgtree's tool surface for an agent is not closed — it's whatever MCP
servers are registered.** `registered_mcp_servers()` (`supervisor.py:703-709`) doesn't maintain its
own list; it reads straight from **`~/.claude.json`'s global `mcpServers`**, the same registry every
Claude Code session on the machine shares. A node is granted a subset of that registry via
`tools.mcp` (`supervisor.py:1344,1316-1319` — `expand_mcp`), the same mechanism used for any other
MCP server. Nothing in that path is scoped to "web tools" or "orgtree-approved" servers — it's the
whole registry, filtered by grant and kiosk ceiling.

**What that means concretely: real, existing browser-automation MCP servers (Playwright MCP,
Puppeteer MCP — screenshot, click, type, DOM query, the actual feature list asked for) are not a
new orgtree feature to build; they're a server to `claude mcp add` once, globally, and then grant to
a node exactly like any other MCP server.** No orgtree code changes that path at all. If the ask is
"can an agent do this," the honest answer for a **non-sandboxed** node is: largely yes, today,
with an install step outside this repo — this docket entry may be closer to a `setup-guide.md`
addition than a build.

**Where it stops being "already possible": sandboxed agents.** `sandbox_mcp_enabled()`
(`supervisor.py:712-716`) states the design plainly: MCP servers are **excluded from sandboxes on
purpose** — "external contact points the sandbox restricts." The experimental opt-in
(`ORGTREE_SANDBOX_MCP`, `sandbox_mcp_passthrough`, `:719-729`) only forwards URL-based servers and
a narrow allowlist of "portable" stdio commands (`npx`/`node`/`python`/`python3`/`uvx`/`uv`), with
**no guarantee a given server runs** — and the docstring's own framing (an agent reaching out to
control a real browser, which itself reaches the open internet) is close to the exact shape the
sandbox boundary exists to contain. A browser-automation server *launched* via `npx` would clear the
portability allowlist, but what it then does — drive a real browser process, hit real URLs — is
precisely the kind of external contact the sandbox's design note is warning about, not a false
positive of an overly broad filter.

**Screenshots specifically may need nothing from this at all.** Claude Code's built-in `Read` tool
already accepts image files — a browser-automation MCP server that saves a screenshot to disk,
combined with the agent's own `Read`, delivers "take a screenshot and look at it" without any new
orgtree plumbing beyond the MCP grant above. Worth separating from the interactive-control half
(click/type/DOM-inspect), which does need the live MCP connection, not just a file on disk.

**What would make this an actual orgtree FEATURE, rather than a setup-guide entry pointing at
someone else's MCP server:** likely one or more of — (a) **bundling** a known-good browser-automation
server as a first-class, pre-registered option (so it's a checkbox at hire time, not a manual global
install the user has to know to do first); (b) a **sandbox-safe path**, since today's story for
sandboxed agents is "experimental, unguaranteed, and arguably against the sandbox's own stated
purpose" — deciding whether browser automation should ever be sandbox-reachable, or should require
dropping sandboxing, is a real design question, not an implementation detail; (c) surfacing
screenshots taken this way somewhere in the UI (a display surface, the way `orgtree_present` gave
documents one) rather than leaving them as files the agent has to separately `Read` and describe.

**Not decided here — the point of this entry is that "build a browser tool" may be the wrong frame
for at least the non-sandboxed half of the ask.** Whoever picks this up should start by checking
whether granting an existing browser-automation MCP server to a live test node already satisfies
most of what was asked, before writing any orgtree code.

---

### FR-18 · watchdogs — persistent (survives orgtree restarts) event-triggered turns
> new fr: watchdogs. an agent can set up a process that will notify it with a turn when a certain
> process or command or file produces an event matching a pattern that it sets up. unqiue to the
> monitor command, as they're persistent between orgtree restarts. what's the feasibility of this,
> existing cc infra that we can borrow, and the shapes of potential agent usecases?

*(user request 2026-08-07, recorded by the curator. Three explicit questions — feasibility,
borrowable infra, use-case shapes — answered in that order below. Not built.)*

**Feasibility: yes, and orgtree already has every load-bearing piece except the generic
pattern-watcher itself.** Three existing precedents compose almost directly into this:

1. **"Poll a condition, persist state, inject a wake-worthy event on match" already exists,**
   hardcoded to one condition. `cred-watch` (`supervisor.py:3801`, spawned by a
   `threading.Thread(daemon=True, name="cred-watch")`) polls every 6 hours, persists its own dedup
   state on the org doc (`org.d["cred_warned_at"]`, survives restarts because the doc does), and on
   match appends a `"from": "@system"` entry to `user_inbox`. A generic watchdog is this same shape
   with the condition and the target made agent-supplied instead of hardcoded.
2. **"Re-arm persisted state at startup" already exists** as the dedicated mechanism for exactly
   this class of problem. `reconcile()` (`supervisor.py:3847+`) runs per-org at backend startup and
   already handles several restart-recovery cases that all rhyme with "a thing must resume/clean up
   because the process died mid-flight": marking unrecoverable nodes, killing stale remote-control
   PIDs by the pid recorded before the crash, auto-resuming mid-turn agents from persisted inflight
   text. A watchdog registry is a persisted list on the org doc (target, pattern, owner) the same
   way `net_hubs`/`add_dirs`/etc. already are — `reconcile()` re-spawning a watcher thread per
   registered watchdog is the same pattern as its existing PID-cleanup step, not a new one.
3. **"Survive the OWNER process being down, not just resume after" already has a shipped precedent
   — FR-07's spool.** The mailserver's outbound spool (`net.py`, FR-07 above) doesn't try to keep a
   live connection alive across a hub outage; it queues locally and drains on reconnect. The same
   shape answers the harder version of this question: what happens to an event that fires *while
   orgtree itself is down*? For a **file** target, nothing is lost — diff the file's state against
   what it was at last shutdown, same as `reconcile()`'s transcript-index walk already does. For a
   **live process's transient output**, anything during the downtime window is genuinely
   unrecoverable unless the watcher itself runs independently of orgtree (see infra below) — worth
   naming as a real limit, not glossed over.

**Existing Claude Code infra — what actually transfers, and what doesn't (the user's second
question, answered directly):**

- **The Monitor tool itself is explicitly the wrong shape, and the request already says so.**
  Monitor's task-notification delivery is tied to the running harness session/process — it does not
  outlive the session, let alone an orgtree backend restart. It's the right reference for the
  *interface* (arm once, get woken on a match) and the wrong one for the *lifetime* — which is
  exactly the distinction the request draws.
- **Claude Code hooks (SessionStart, PostToolUse, etc.) don't solve the general case either.** They
  fire on the CLI's own internal lifecycle events (a session starting, a tool call completing) —
  useful for "notice when THIS agent edits a file," not for "watch an arbitrary external file,
  process, or command this agent never touches directly." Not zero relevance (a hook COULD watch the
  agent's own tool-call stream for a pattern), but that's a narrower feature than what's asked.
  `hub/session-start.sh` is the one hook already in this repo's own use — a config-injection
  pattern, not a watch-and-notify one, so not directly reusable either.
  
  Not consulted directly (no MCP/web access from this seat) — worth a targeted check by whoever
  scopes this: whether Claude Code ships a **general-purpose file-watcher tool** (distinct from
  Monitor) that could be driven headlessly and outlive a single turn. If one exists it likely still
  inherits Monitor's session-lifetime limit; if it doesn't, this backs the "orgtree has to build
  it" conclusion further.
- **What DOES transfer cleanly: the wake mechanism itself needs no new invention.** A fired
  watchdog just needs to become another inbound mail to the owning agent — `deliver_org_inbox`
  (`supervisor.py:3375-3418`) → `_run_turn` (`:1412`) is the exact same path mail, hub delivery, and
  `cred-watch`'s system notices already use. The interesting engineering is entirely on the
  **watching** side (registration, persistence, restart-survival); the **firing** side is a solved
  problem this can reuse verbatim.

**The one real architectural fork, worth a ruling before building:** in-process poll threads
(cheap, `reconcile()`-friendly, but genuinely blind to anything that happens while orgtree itself is
down — matches FR-07's spool model only for file targets, not live process output) **vs.**
standalone OS-level watcher processes that run independently of orgtree's own process lifetime and
report back over HTTP when it's up (survives orgtree being down for real, at the cost of a second
class of process this repo now has to spawn, track, and clean up — a genuinely new operational
surface, the same category of complexity `sandbox.py`/the container lifecycle already carries for a
different reason). Not decided here.

**Shapes of potential agent use-cases (the third question):**
- **Long-running external jobs an agent would otherwise poll for.** A render/build/deploy the
  agent kicked off and would normally check on repeatedly across turns (burning turns and credits
  on "still not done?") — watch the log or output directory for a completion marker, get woken once,
  at the actual moment it matters.
- **Cross-boundary dependencies.** "Wake me when this file another team/process owns changes" — the
  same shape as this session's own cross-org mail exchanges, but for a filesystem artifact instead
  of a message; a natural fit for an org whose work is gated on someone else's output landing.
  FR-13's scope-grant requests are one plausible SOURCE of watch targets — an agent granted a new
  folder might immediately want to watch it.
- **Proactive failure detection.** Watching a service's health-check output or a log for an error
  pattern (a crash, an OOM) so an agent gets interrupted with the bad news instead of discovering it
  cold on its next unrelated turn — closer to an alarm than a poll.
- **Handoff drops.** A directory an external process or a human is expected to drop a file into —
  the recovery-browser's storage-browser pattern already establishes "a place things get dropped for
  later pickup" as a familiar shape in this repo; a watchdog turns that into "notified the moment it
  happens" instead of "checked on next login."
- **A process-liveness dog, literally.** Watch a PID (or a port, or a health endpoint) for it going
  DOWN rather than a pattern appearing — the inverse of "wake me when X happens," useful for an
  agent that started something long-running and needs to know if it died silently.

Not scoping a build here — this entry is the grounding, per the user's three explicit questions.

**Visual spec, added same day — extends this entry, not a new FR number (same feature's canvas
half).**
> since watchdogs will "send mail", but not be full on intelligent agents of their own, i want to
> see a visual representation of them in the canvas: a tiny little rectangle, maybe 1/4 or 1/8 the
> area of a full node, connected to their "owner" with a wire. named, and clicking on them shows a
> description of the process / command / file theyre running / watching. they have a mail tab
> showing the events theyve sent out, and everytime they send one, a spark runs up the wire to
> their attached agent.

**The genuinely good news: almost this entire spec is already-built infrastructure, not new
rendering work.** Traced the actual canvas code rather than assuming:

- **The wire is not a new rendering system — it's a new edge KIND in one that already exists.**
  `OrgCanvas.tsx`'s `edges` SVG (`:1051-1103`) already draws several distinct edge kinds between
  entities — parent-child, peer (`edge peer`), audience-grant (`edge aud-line`), and a **`tether`**
  (`:1103`) — a line connecting a small satellite entity to an owner node that ISN'T itself part of
  the tree layout. A watchdog↔owner wire is a sixth edge kind in the same SVG.
- **The satellite-entity-offset-from-its-owner pattern already exists, for a different purpose —
  "bearer" lineage cards.** `isBearerOf` entities (`:191-197`) are explicitly NOT positioned by the
  general tree-layout pass; they're placed as a manual offset from their target's already-computed
  position (`p.x + 42 + 18*n.bearerIndex`, stacking multiple). A watchdog is the same shape: a small
  non-agent visual entity, offset-positioned near its owner rather than laid out in the hierarchy,
  connected by a tether-like wire. This is the closest existing precedent in the codebase for
  exactly what's being asked, just built for org lineage history rather than live watching.
- **The spark animation is not new — it's the EXACT mechanism mail already uses, unmodified.**
  `launchSpark(from, to)` (`:341-414`) already rides a spark along a wire between any two entities
  on a mail event (`mailEvt` → `launchSpark`), rendered as `<circle className="spark">`
  (`:1127-1133`). "Every time [a watchdog] sends one, a spark runs up the wire to their attached
  agent" is describing this exact function called with the watchdog's id as `from` and the owner's
  id as `to` — no new animation to build, assuming the watchdog is a `map`-indexed entity with a
  wire entry, which the edge-kind point above already covers.
- **Sizing has a real base to scale against.** Full nodes are `NODE_W = NODE_H = 124`
  (`shared.ts:208`). "1/4 to 1/8 the AREA" (the user's own framing) is ~62×62 down to ~44×44 in
  side-length — small enough that the existing per-node chrome (tier badge, context wheel, activity
  dot) likely doesn't fit and needs its own minimal treatment: name + a static "watching" glyph is
  probably the ceiling of what fits, which matches "clicking shows the description" doing the
  detail work instead of cramming it into the tray-sized box.

**What's genuinely new, not reused:** (1) a watchdog needs to exist as a lighter-weight entity in
whatever the frontend calls its node map — NOT a full agent (no charter, no tier, no turn cost,
none of `TIERS`/`MODELS`) but present enough to have an id, an owner, a position, and a wire; (2)
the small-rectangle shape itself, since every existing node visual is the full 124×124 box; (3) the
click-through detail panel showing the watched process/command/file and a description — a much
smaller cousin of the existing per-node config panel, read-only, no hire/charter/tool-grant knobs
since a watchdog isn't hired; (4) the "mail tab" of events it sent — the events themselves are
already ordinary mail once fired (FR-18's finding above: firing reuses `deliver_org_inbox`), so this
is very likely a filtered view of the SAME mail data the owner agent already receives, scoped to
"sent by this watchdog," not a new mail-storage concept.

Not scoping a build here either — same status as the rest of FR-18, folded in as the visual half of
the same not-yet-built feature.

**Ruling, added same day: watchdogs are framed as "pets" for agents, and cost no credits to
spawn.** A real, load-bearing design decision, not just a naming flourish — it settles what would
otherwise be an open question this docket would have had to flag. Confirmed against the actual
gate: `orgtree_hire`'s seat cost (`TIERS`, `ledger.py:39` — haiku 1, sonnet 3, opus 5, fable 10) is
deducted from the hiring agent's free-credit balance at hire time, the same balance FR-13's
scope-request grants and FR-14's batch cards above are reasoning about. "No credits to spawn" is an
explicit exception to that gate, not an oversight to close later — a watchdog never enters `TIERS`/
`MODELS` at all (consistent with point (1) in the visual-spec note above: no tier, no turn cost), so
whoever builds this should treat "free to create" as a stated requirement, not default to gating it
like a hire and need a later ruling to remove the check. The "pet" framing also reads as informing
the small/non-agent visual treatment above: something owned and cared for, not staffed.

---

### FR-19 · "generate a name" button — AI-suggested hire name from the charter
> new feature: a "generate a name" button that uses ai to generate an appropriate name for a new
> hire based on their charter. it should generate a simple name of 1-3 words, formatted in
> kebab-case

*(user request 2026-08-08, recorded by the curator. Not built. One piece of the ask is already free
— see below.)*

**Located precisely: the draft hire card, not a separate form.** `frontend/src/canvas/cards.tsx`'s
`DraftNode` component has the actual fields — `df-name` (`:597`, plain text input, currently just
`placeholder="name…"`) sitting in the same header row as the tier token, and the charter box
(`:634-648`, textarea + preset chips compiled via `finalCharter()`, `:552-553`) directly below it.
A "generate" button belongs next to `df-name`, reading whatever `finalCharter()` currently resolves
to as its input.

**One requirement is already free, not something to build.** Kebab-case formatting doesn't need new
code: `slugify()` (`ledger.py:162-163`, `re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")`)
already runs on **every** hire name at hire time (`ledger.py:1706`, inside `hire()`), regardless of
what typed or generated it. Whatever the AI returns — even "Field Ops Coordinator" with spaces and
caps — becomes `field-ops-coordinator` automatically before it ever becomes a node id. The button's
job is only the 1-3-word CONTENT choice; the formatting half of the spec is already guaranteed
by existing backend behavior.

**What actually needs building: this repo has zero precedent for calling a model outside a full
agent turn.** Checked directly — no `anthropic` SDK import, no direct `messages.create`-style call,
nothing resembling a "generate/suggest" endpoint anywhere in `backend/orgtree/api.py` or the rest of
the backend. Every existing path to a model response is the full `supervisor.py` turn machinery:
spawn the Claude Code CLI as a subprocess, stream-json the whole conversation, run it through the
turn loop, mail delivery, and cost/ledger accounting (the same machinery FR-15's exploration traced
in depth). A one-off "give me three words" call has no existing lightweight path to reuse.

**The real design fork, not decided here:**
1. **A minimal one-shot CLI call** — the same `claude` binary orgtree already shells out to
   (`supervisor.py:164-170`), invoked once with just the charter text and no MCP tools, no node, no
   turn/mail plumbing, output captured directly rather than streamed into the turn loop. Reuses the
   existing binary/auth (subscription or org API key) with no new dependency, but is comparatively
   heavyweight machinery — a full CLI process spin-up — for a three-word answer, and needs its own
   thin harness distinct from every existing turn-spawn path.
2. **A direct Anthropic API call** — a new dependency (`anthropic` SDK) the backend has never taken
   on before, needing its own credential story. `org.d.get("api_key")` already exists as an optional
   per-org key (`ledger.py`, used for orgs running key-based rather than subscription auth) and could
   plausibly be reused here, but plenty of orgs run subscription-only with no key set — the button
   would need its own fallback (an orgtree-level key in settings, or gracefully degrading to
   unavailable when neither exists).

**One more open question, following FR-18's "pets" precedent above:** does generating a name cost
anything? A UI-assist action with no node behind it yet doesn't obviously belong on any org's
credit ledger — the same reasoning that made watchdogs explicitly free likely applies here too, but
it isn't automatic: unlike a watchdog, this DOES call a model, so "free" would mean orgtree eating a
small, real inference cost per click rather than a genuinely zero-cost operation. Worth a deliberate
ruling rather than a default either way.

Not scoping a build here — the grounding is the point: one piece of the request is already solved
by existing code, and the interesting design question is the model-access path, not the UI.

---

### FR-20 · pinned "last user turn" chip in the desk chat view
> potential idea: mimic claude code's pinned last user turn: the last message they sent in the
> chat. it stays at the top when offscreen, and clicking it jumps to it.

*(user request 2026-08-08, recorded by the curator, framed as "potential idea" rather than a firm
ask — recorded as such. Not built.)*

**Almost exactly the mirror of a chip that already exists, one screen away.**
`frontend/src/canvas/desk.tsx`'s chat view already has this shape, just pointed the other direction:
`showJump`/`toBottom` (`:217-253`) render a sticky **bottom**-anchored `jumpbottom` chip
(`:640-644`, "↓ jump to bottom") whenever the reader has scrolled away from the newest message
(`nearBottom()`/`setStuck`, `:218-253,520`), sticky *inside* the scroller as its last child — chosen
deliberately, per the comment at `:636-639`, because the desk's flex chain is "documented as
fragile" and sticky-as-last-child needs no new layout box. A **top**-anchored "pinned last user
turn" chip is the same mechanism — conditional render based on scroll position, sticky inside the
same scroller, a click handler that scrolls to a target — aimed at a specific earlier row instead of
"the newest," and stuck to the top edge instead of the bottom.

**The one real design question, and it's not a mechanical one: what counts as "the last message
they sent," given orgtree's chat isn't Claude Code's chat.** Transcript rows already carry
`m.role === 'user'` (`:1072-1086`) — the raw Claude Code CLI role, present because each desk is
explicitly "a miniature Claude Code session" (file header). In **actual** Claude Code, every
`user`-role turn genuinely IS the human — there's no other sender it could be. In orgtree, a node's
`user`-role transcript rows are envelope-wrapped turn INPUT from *any* sender the mail system
delivers — a sibling, a superior, the org inbox — not only the actual human operator. Naively
pinning "the last `role === 'user'` row" would misattribute a chip captioned "them" to a sibling
agent's mail on a node the human hasn't personally messaged in a while. The identification this
needs already has a working precedent one screen up: pending mail is filtered `m.from === USER`
(`:600`, the same sentinel used throughout the frontend for "the actual human") — the pinned chip
almost certainly wants that same filter applied to durable transcript rows, not bare `role`, and
whoever builds this should confirm the durable message shape actually carries a comparable
`from`/sender field to filter on (the pending-mail shape does; worth checking whether the persisted
transcript row does too, or only the live/pending paths retain sender identity distinctly from
CLI role).

**Mechanics, briefly — the scroll-to-target half, not just the visibility-toggle half.** Existing
rows are keyed by `m.seq` (`:550`, "the server's pre-slice ordinal"), and `toBottom` scrolls via the
existing `scroller` ref (`:518`) to `scrollHeight` — a fixed target. Scrolling to an arbitrary
earlier row instead of the end needs either a per-row ref map keyed by `seq` (to call
`scrollIntoView` on the right element) or an equivalent DOM query — a small but real addition, since
nothing today needs to target a row that isn't "the end."

**One directional nuance worth stating plainly, since the request only describes one direction.**
"Stays at the top when offscreen" reads as: pin only while the target row has scrolled *above* the
visible viewport (reader has scrolled up past their own last message into older history) — if the
reader is instead scrolled *down* past it toward newer content, that's already `jumpbottom`'s
territory, not a case this new chip needs to also handle. Two independent sticky chips, top and
bottom, each covering the direction its own name implies.

Not scoping a build here — mechanically small given the existing `jumpbottom` precedent; the
attribution question above is the one thing worth a ruling before writing it.

---

### FR-21 · attachments on agent → user mail, reusing the send_file download card
> agents should also know about the ability to embed attachments in mail to the user
> *(then, after being told the capability doesn't exist)* what about the file download card as
> user mail attachments?

*(user request 2026-08-09, across two messages, routed to the curator by the redteam (4f69f83a) on
the user's own instruction — judged nontrivial, handed over rather than built under the redteam's
bug-fix-only authority. **The grounding below is the redteam's own trace, reported to this seat
essentially complete; recorded here rather than re-derived**, per their explicit request. Not
built.)*

**Why this needed a docket entry rather than a prompt fix.** The user's first message assumed
agents could already attach files to mail addressed to them. They cannot —
`orgtree_message`'s `attachments` field is **`@net:`-only**; the backend refuses anything else at
the door (`api.py` ~2857: *"attachments ride `@net:` mail only (v1) — for local recipients use
`orgtree_send_file` or paths"*). Writing prompt guidance for a capability that 422s at runtime would
have been the wrong fix, hence the redteam declined and explained instead — producing the second
message this entry is titled after.

**The current attachment matrix, so this isn't re-derived later:**

| direction | status |
|---|---|
| user → agent | supported — files land in the agent's `uploads/` |
| agent → `@net:` peer | supported |
| agent → user | **not supported** — `orgtree_send_file` is the only route today: copies to `<sender scratch>/outbox/<name>`, renders a download card in the chat |
| agent → local peer | not supported |

**What the bridge actually costs — traced before it reached this seat, so triage doesn't retrace
it:**

*Already there:* `orgtree_send_file` already returns exactly the shape mail attachments use
(`{name, path: outbox/<file>, bytes}`, `api.py`'s `_agent_send_file`). `GET
/api/orgs/{slug}/nodes/{nid}/file?path=…` already serves those bytes, org-scoped — the kiosk public
gateway already passes it through. `MailList` already renders a download-chip row from
`cur.attachments`, the same component the user's inbox itself uses — live in the **node** inbox
today, just not reachable from the direction this entry asks for.

*Missing, exactly two things:* (1) **backend** — permit `attachments` when `to == 'user'`, routing
each path through the *existing* `_agent_send_file` validate-and-copy rather than a second
implementation, so the reuse inherits its capability-root enforcement, traversal guard, 25 MB cap,
storage-block check, and sandbox path translation for free. (2) **frontend** — the user's inbox
passes `fileHref` keyed on the **sender** (`fileUrl(slug, m.from, path)`, since the file sits in
that agent's own outbox) to every `MailList` call site except this one; the node inbox already
passes it, which is why its attachments are already downloadable and the user's inbox's aren't.

**The one design point flagged for the implementer to decide, not decided here:** built naively this
creates two mechanisms for one outcome (a standalone `send_file` card, and now a mail-attachment
path, doing the same copy independently). The better shape: one mechanism, two entry points —
`send_file` stays the actual copier, and mail's `attachments` field calls it internally, so a file
sent either way lands identically. This also lets an agent say "here are the results" *and* ship the
files in the same message, instead of a message plus a detached card wherever the chat happened to
land.

**One thing flagged to leave alone, not a bug:** the ORG inbox's attachment chips are inert **by
design** — inbound `@net:` attachments land in the *receiving agent's* `uploads/`, not at an
org-level path, so `mail.tsx` maps them with `path = a.name` and a link there would point at
`undefined` (per the code's own comment). Unrelated to this entry; noted only so it doesn't get
"fixed" as a drive-by while someone's in this code for FR-21.

**Sizing, per the redteam's own estimate, offered for triage rather than as a commitment:** roughly
30 backend lines, one frontend prop, plus tests — "small because the parts already line up, not
because the surface is trivial." The redteam has offered to supply an expanded trace or a written
spec on request if whoever picks this up wants either.

---

### FR-22 · rescind — retire that also permanently claws back the superior's grant
> new feature: rescind. sits alongside retire. when agent is rescinded, that agents subtree is
> retired / dissolved, *and* the agent's superior also immediately loses the credit grant that was
> used to hire them from their total, preventing them from being rehired

*(user request 2026-08-09, recorded by the curator. Not built. The credit-mechanics half is more
precisely answerable than it first looks — traced against `ledger.py` rather than assumed.)*

**The subtree half is already built — `retire()` already does exactly this on encounter.**
`retire()` (`ledger.py:1988-2028`) already auto-bridges to `dissolve()` (`:2227-2252`, recursive
archive, deepest-first, "takes the whole lineage stack") the moment it finds live children —
*"a superior retiring a node with live reports auto-DISSOLVES the subtree, with a warning."*
Rescind's "that agent's subtree is retired/dissolved" is not new work; it's calling the same path
`retire()` already calls, unmodified.

**The claw-back half is genuinely new, and the reason it's needed is precise, not hand-wavy.**
Today, retiring a node returns nothing to the parent explicitly — there is no "refund" mutation to
undo. `committed(nid)` (`:487-488`) sums `seat_cost + grant` over a node's **live** children only
(`children()`, `:479-485`, filters `state != "archived"`), and `free(nid)` (`:490-493`) is the
*derived* value `grant - committed`. The instant a node is archived it drops out of its parent's
`committed()` sum, and the parent's `free()` recomputes upward automatically — no code runs to
"give the credits back," they were simply never counted against the parent once the child stopped
being live. **That auto-recompute is exactly the mechanism that makes retired nodes rehireable
today, and exactly what rescind needs to defeat.**

**The mechanism that defeats it, and why it's safe:** after archiving (same as retire), explicitly
subtract `freed = seat_cost(nid) + n["grant"]` — the *same* quantity `retire()` already computes,
just applied as a real mutation instead of a report — from the immediate superior's own stored
`n["grant"]` field. Traced through the arithmetic: before rescind, `parent.free = parent.grant -
parent.committed`. Archiving drops `freed` from `parent.committed` (automatic, as above); explicitly
subtracting the same `freed` from `parent.grant` leaves `parent.free` **exactly where it was before
the hire ever happened** — net zero headroom gained, versus a plain retire's net `+freed`. This
can't go negative: `committed(parent) ≥ freed` always held while the child was live (that's what
funded it), so `parent.grant ≥ freed` must already hold too.

**The one subtlety worth stating precisely: which superior, when the original hire cascaded.**
`_chain_acquire` (`:1852-1929`) lets a hire's cost bubble up past the immediate parent when the
parent alone couldn't afford it — but critically, cascading inflates the **immediate parent's own
`grant` field too** (`:1913-1917`, every hop strictly below the contributing ancestor gets inflated,
"grants inflating down the path so every hop's invariant holds"). So the immediate parent's stored
`grant` always fully reflects what it needed to afford this specific child, regardless of whether
that capacity originally came from itself or bubbled down from further up — meaning subtracting
`freed` from just the **immediate** superior (matching the user's own wording, "the agent's
superior," singular) is always arithmetically correct on its own terms, with no need to walk the
original funding chain. The residual case — a grandparent (or higher) whose grant was permanently
inflated to fund the original cascade stays inflated after a rescind, since rescind only touches the
immediate parent — is a **pre-existing characteristic of the credit-cascade system itself, not
something rescind introduces.** The system already tells the user this today, verbatim, on every
cascaded hire: *"grants below it were inflated to carry them down — reclaim with `reallocate`."*
Not this entry's problem to solve.

**What "rescind" needs beyond retire's existing tool, concretely:** (1) the explicit `parent.grant
-= freed` mutation described above, on top of retire's existing archive-and-cascade-to-dissolve path;
(2) a new verb/menu action distinct from `orgtree_retire` (`mcptool.py:391-403`), since the two have
materially different consequences for the superior and conflating them would surprise whoever clicks
the wrong one; (3) a decision on **authority** — `orgtree_retire` is agent-callable within one's own
subtree today, but rescind's punitive effect lands on a THIRD party (the rescinded node's superior),
not just the rescinded node itself. Worth a deliberate ruling on who may invoke it: the same
subtree-authority rule as retire (an ancestor rescinding a descendant, including possibly punishing
an intermediate superior who isn't the actor), self-rescind by the superior itself (voluntarily
taking the credit hit for its own bad hire), user-only (mirroring `delete()`'s "permanent removal is
the user's alone" ruling, on the reasoning that clawing back another node's resources is a
comparably weighty, hard-to-reverse action even though the session itself is preserved) — not
assumed here.

Not scoping a build here — the subtree-dissolve half is a straight reuse; the claw-back half is a
small, precisely-safe mutation once traced; the authority question is the one real ruling needed
before writing it.

---

### FR-23 · timestamp the end of the most recent turn, glanceably
> new feature: timestamp the end of the most recent turn after it finishes

*(user request 2026-08-09, recorded by the curator. **The data and a surfacing of it already
exist** — traced before assuming this needed building from scratch. The real gap, if there is one,
is visibility, not data. Not built as a new, glanceable feature.)*

**CLOSED — BUILT (`22482cb`, 2026-08-11).** The canvas reading (№2 below) shipped: an aged stamp
(`3m`, `2h`) on the badge row at norm lod, sourced from `TurnStat.at` — never `NodeStatus.at` —
hidden while busy, absent when no turn ever ran; hover gives the full timestamp + killed marker.
`derived.test` ⑫ pins all three choices, answering both open questions this entry left.

**Two separate turn-end timestamps already exist, already recorded on every turn.**

1. `TurnStat.at` (`schema.py:80-92`, the "per-node turn ring, capped at 20", №15) — written at
   `supervisor.py:2425` as `now_iso()` the moment a turn's CLI `result` event is processed, i.e.
   the actual completion instant, alongside `cost`, `ms` (duration), and `denials`. A killed turn
   gets its own ring entry the same way (`:2340`, `_charge_killed_turn`).
2. `NodeStatus.at` (`types.ts:162-167`) — the timestamp on an agent's own self-reported
   `last_status` summary, which effectively doubles as "when the agent last said something about
   itself at/near turn end."

**Both are already surfaced in the frontend — but neither is glanceable; both require opening the
node and, for one of them, hovering.**

- `TurnStat.at` renders in the `$` cost badge's **hover title** inside the opened desk panel
  (`desk.tsx:462-471`): the last 5 turns, each formatted `MM-DD HH:MM · $cost · Ns · N denied`,
  visible only on mouseover, and only once a node has spent something (`cost_usd > 0` gates the
  badge's existence at all).
- `NodeStatus.at` renders inline as `· {ago(stat.at)} ago` next to the last status summary in the
  desk's chat stream (traced during FR-16/FR-20 research) — present, but embedded in scrolling chat
  content, not a standing label.
- **Neither appears on the canvas node square itself** (`cards.tsx`'s `NodeSquare`, `:819-837`) —
  the collapsed/overview representation shows only a colored status dot/chip with the summary in a
  `title` attribute; no timestamp renders there at all, hover or otherwise.

**So the open question is precisely: what does "new feature" mean here, given the data already
exists twice over?** Two readings, not decided here:
1. **Make an existing timestamp always-visible instead of hover-gated** — e.g. a small "3m ago"
   label always shown next to the `$` badge or the status chip inside the desk, no hover required.
2. **Put it somewhere it isn't today at all — the canvas node square**, so a glance at the whole
   org (without opening any single node) shows which agents finished a turn recently vs. long ago.
   This is the more likely reading of "new feature" given both existing surfacings already satisfy
   "visible on request inside the desk" — if that were sufficient there would be little reason to
   ask for it as a new item.

**If it's the canvas reading:** `NodeSquare` already conditionally renders `last_status` at
`lod === 'mini'` (`:819`) — a glanceable end-of-last-turn stamp most naturally sits beside it,
reusing whichever of the two existing `at` fields is more reliable (`TurnStat.at` is written by the
turn-completion code path unconditionally; `NodeStatus.at` depends on the agent having reported a
status at all, which not every turn does) — worth a decision on which source is authoritative for
this display, or whether idle nodes with no status yet should show nothing versus "never" versus the
node's creation time.

Not scoping a build here — recording that the underlying capability already exists twice, so
whoever picks this up should design the DISPLAY (where, always-visible vs. on-hover, which existing
`at` field is authoritative) rather than re-invent time-tracking machinery that's already in place.

---

### FR-24 · "cheap compact" — retire + fresh hire instead of a cache-cold `/compact` fork
> "cheap compact". normal compaction reads the entire context of an agent's transcript in order to
> produce a summary of their content. normally, this is fine; the context is cached and the
> compaction is a negligible cost. but if its been several hours or days since the agent was last
> interacted with, then their chat context likely will have been dropped, and will need to be
> reuploaded in full again, recurring those api costs. a cheaper compaction strategy would be to
> just retire the agent directly, hire a new one to replace it as its superior, and then tell the
> new agent, "you're so-and-so's replacement, if you want to know what they were working on, read
> their transcript"

*(user request 2026-08-10, recorded by the curator. The premise checked out precisely against both
Anthropic's own cache-TTL documentation and orgtree's actual compaction code — not assumed. Not
built.)*

**CLOSED — BUILT, then REWORKED IN-PLACE (D-108 → D-114, 2026-08-11/12).** Ships opt-in as
`orgtree_cheap_compact` (superior-only) plus a second door on the desk's compact dialog — never an
automatic default; auto-defaulting revisits only with cache telemetry. One premise below was wrong:
the live transcript sits under `~/.claude/projects`, which no agent can be granted (D-161), so it
is **copied into the predecessor's scratch at compact time** instead. D-114 then reshaped the
mechanism away from retire-plus-fresh-hire: the seat keeps its id, parent, scope, charter, grant
and team — only `session_id` is replaced, the pre-compact session archiving as the `nid@gen`
knowledge bearer. FR-24b adds auto-on-wake (occ ≥ 0.5 AND idle ≥ 300 s thresholds, org-level
config, **disabled by default**; a refusal falls through to a normal turn).

**The premise is correct, and precisely so.** Prompt cache entries expire on a TTL — 5 minutes by
default, up to 1 hour with the explicit `ttl` option (Anthropic API reference, loaded fresh for this
entry) — so "several hours or days" since last interaction is unambiguously past any cache lifetime;
there is no configuration under which that context survives. The next read of that transcript pays
close to full input-token price, not the ~0.1× cached-read rate.

**Orgtree's compaction doesn't avoid this — it's built directly on top of it.** Traced
`_compact_split_body` (`supervisor.py:2628-2648`): compaction **resumes the actual prior CLI
session** — `claude -p --resume <old_session_id> --fork-session ... ` — and pipes the literal
`/compact` slash command in as input. This is not a lightweight summarization call; it is an
ordinary session resume, which necessarily reloads and reprocesses the *entire* prior transcript as
input before it can act on `/compact` at all. It is subject to exactly the same cache economics as
any other turn — nothing about the compaction path is cache-exempt. Telling confirmation, in the
code's **own words**, one line below the fork: *"the fork is a real API call — often the most
expensive one the system makes"* (`:2679-2682`) — this cost concern is already a known, named
problem in this codebase, just not yet connected to the cache-TTL cause the user is naming here.

**The proposed alternative maps cleanly onto primitives that already exist — this is close to a
zero-new-code build, not a new subsystem:**

1. **Retire the agent** — `retire()` (`ledger.py:1988-2028`, already cited in FR-22 above) already
   does exactly "archive it, free seat+grant back to the parent." Per `orgtree_retire`'s own
   description (`mcptool.py:391-403`): *"Its session is preserved and can be rehired with context
   intact"* — retiring does not touch or delete the transcript on disk. Reuse unmodified.
2. **Hire a replacement** — `orgtree_hire` is a **fresh session**, not a resume: no `--resume`, no
   prior transcript reloaded into context at all. Its only cost is the seat + whatever charter text
   the hiring agent writes — a small, one-time system-prompt-sized input, categorically cheaper than
   reprocessing an entire cold transcript regardless of that transcript's length. The freed
   seat+grant from step 1 is exactly what funds this hire, the same accounting FR-22 traced for
   `retire()`'s effect on the parent's `free()`.
3. **"Read their transcript" — this needs a directory grant, not new backend code, for the baseline
   version.** A retired node's transcript is an ordinary file under that node's own scratch directory
   (`scratch_dir(slug, old_nid)`), untouched by retirement. Granting the new hire's `add_dirs` a
   **read-only** entry pointing at the predecessor's scratch dir lets the new agent `Read`/`Grep` the
   raw transcript directly with tools it already has — no new orgtree verb required for this to work
   at all.

**One real quality-of-life gap, not required but worth naming.** The raw transcript is Claude Code's
own JSONL session format — readable, but not the friendly rendering a human gets. `read_chat`
(`supervisor.py:4469-4491`) already does exactly this parsing — tool chips, compaction boundaries,
collapsed results — but it's wired to the **frontend UI only** (the desk chat view), not exposed as
an agent-facing tool. A `orgtree_read_predecessor_transcript`-shaped verb that hands the new hire
`read_chat`'s already-parsed output, scoped to a specific archived node, would be nicer than raw JSONL
grepping — but the feature works end-to-end without it, using primitives that exist today.

**The real tradeoff, not a flaw to fix — worth the user knowing before this gets built.**
`/compact`'s actual output is an **LLM-generated summary** that becomes the new session's context
baseline — the successor starts already knowing the gist, no action required. "Retire + fresh hire"
starts the replacement with **zero context by default**; it only pays anything to learn the
predecessor's history if it actively chooses to go read the transcript, and can do so selectively
(the relevant section, not the whole thing) rather than being forced to reprocess it wholesale like
`/compact` is. That's exactly the shape of the savings: close to free when the replacement doesn't
end up needing the old history, and only as expensive as what it actually chooses to read when it
does — never the forced, all-or-nothing reload `/compact` performs regardless of relevance.

**What "cheap compact" would concretely need to become an actual feature, not a manual
recipe:** (1) a combined verb (or a documented charter/prompt pattern) that does retire → hire →
grant-predecessor-dir → charter-mentions-predecessor in one motion, since today those are four
separate manual steps; (2) a ruling on whether this becomes the **default** compaction path when
the transcript is likely cache-cold (e.g. gated on elapsed idle time since the node's last turn,
which `TurnStat.at`/`NodeStatus.at` from FR-23 above already track) or stays an opt-in alternative
the user or a superior agent chooses explicitly; (3) the read-transcript ergonomics gap named above,
if a friendlier surface than raw JSONL is wanted.

Not scoping a build here — the mechanism is real, grounded, and mostly assembled from parts that
already exist; the open questions are policy (when to prefer this over `/compact`) and polish (the
transcript-reading tool), not feasibility.

**Follow-up checked, same day: extending the cache window instead is not currently possible.** The
user asked whether the requested cache TTL could simply be raised for active chats, avoiding the
cold-cache problem at its source rather than working around it. The underlying Anthropic API does
support a longer TTL (`cache_control: {type: "ephemeral", ttl: "1h"}`, at 2× write cost instead of
the default 5-minute window's 1.25×) — but orgtree never constructs that request itself; it shells
out to the Claude Code CLI, which owns prompt caching internally. Checked the actual pinned CLI's
full `--help` output (`v2.1.220`, the install `supervisor.py` prefers) directly rather than
assuming: **no flag exists for cache TTL at all** — nothing to opt into the 1-hour window, only
`--exclude-dynamic-system-prompt-sections`, which is about cross-*user* cache reuse, not extending
duration. Not something orgtree's own code could add either; it would need the CLI itself to expose
a new flag first. FR-24's mechanism stands as the workaround until/unless that changes.

---

### FR-25 · "insert parent" — hire tokens on a node's top edge that splice a new superior above it
> feature: insert parent. add a new set of hire tokes on the top edge of an agent: hiring an agent
> from there hires a new subordinate of the agent's superior, and then moved the old agent
> underneath it as its new superior.

*(user request 2026-08-10, recorded by the curator. Almost entirely a recombination of two
primitives that already exist and are already proven safe together — traced both before writing
this up. Not built.)*

**CLOSED — BUILT (`696636c`, 2026-08-11).** Exactly the three predicted pieces: the `'top'`
SpawnChips variant, `DraftState.above` carrying the anchor, and one chained `move()` in
`confirmDraft` after hire success — deliberately loud on failure (toast names the manual
completion). The open question below was confirmed against `test_ledger`'s audience-sweep-on-move
checks: the old superior remains an ancestor after a splice, so ancestral grants survive, exactly
as a plain drag-reparent. `derived.test` ⑮ pins the plumbing.

**REWORKED 2026-08-19 (user: "clunky and unintuitive… a slow few consecutive steps") — see
D-135.** The client-chained hire→move is gone: the hire op now carries `above: <anchor>` and the
server splices atomically (hire + ordinal pin + move, one save, one broadcast), while the draft
WRAPS the anchor in the preview tree — it takes the anchor's slot the moment the top chip is
clicked, dashed lines above and below, with the real structure untouched until confirm. The
loud-failure toast went with the failure mode it reported: a refusal anywhere now rolls the whole
op back, so hired-but-unspliced can no longer exist. ⑮ re-pinned to the new shape.

**Step 1 of this feature is already shipped, verbatim — it just doesn't stop where this request
needs it to.** `spawnBeside` (`OrgCanvas.tsx:960-967`, **F-03**, already shipped per the docket
history above) is *exactly* "hire a new subordinate of the agent's superior": it resolves the new
hire's parent as `n.parent` (the **anchor's own parent**, not the anchor itself) and spawns the
draft form beside it. The rendering half is `SpawnChips` (`cards.tsx:334-360`, left/right variants
wired at `:893-899`) — a small `side` prop (`'left' | 'right'`) that only drives a CSS class
(`side-${side[0]}`) and the tooltip copy; nothing about the component is architecturally two-sided.
A third `side="top"` variant is a CSS rule and a prop-type widening, not new component design.

**Step 2 — reparenting the anchor under the freshly hired node — is also an existing, already-hardened
primitive: `move()`.** `ledger.py:2448-2465` (`§4.5`, "the capability the design derived... only the
user could reach until now") is a **unified promote/demote verb** with real teeth already built in
and already fought for: cycle detection covering not just the moved node but its whole lineage stack
(`:2476-2493`, citing a real 2-cycle bug the credit-conservation fuzzer reproduced and closed), and
depth/children-cap enforcement measured against the *whole* moved subtree's deepest leaf, not just
the moved node itself (`:2497-2514`, closing a hole where drags could bypass the runaway-growth caps
that `hire` already enforced). This is not a naive parent-pointer swap; it's already survived an
adversarial pass.

**The one property that makes chaining these two safe, worth stating explicitly since it answers the
first question anyone would ask:** `_move`'s own docstring — *"Release P_old→L and acquire L→P_new
cancel hop by hop, so every node's free is unchanged — budget-neutral, cannot fail on credits"*
(`:2467-2469`). The freshly hired node does **not** need spare grant capacity to "afford" absorbing
the anchor underneath it — the credit accounting nets to exactly zero on every node touched,
regardless of the new parent's own balance. So "insert parent" composes these two primitives with no
new credit-safety work required; `move()` already proved that part.

**What's actually new, concretely:**
1. **The top-edge chip itself** — `SpawnChips`'s `side` type widens to include `'top'`; a `.side-t`
   CSS rule positions it above the node instead of beside it (`cards.tsx:340`'s class-string already
   derives cleanly from `side[0]`, so `'top'` → `side-t` costs nothing extra there); a third render
   call alongside the existing left/right pair at `:893-899`.
2. **A new spawn handler distinct from `spawnBeside`.** `spawnBeside` computes the SAME parent
   resolution "insert parent" needs (`!n.parent || n.parent === USER ? null : n.parent`) — that part
   is copy-paste — but `spawnBeside` never reparents anything afterward. The new handler needs to
   remember which node it's inserting above (something `DraftState` doesn't currently track — no
   existing draft flow needs to act on a *third* node after the hire completes) and thread that
   through to confirmation.
3. **A chained second operation in `confirmDraft`** (`OrgCanvas.tsx:968+`). Today `confirmDraft`
   fires one `op({op:'hire', ...})` and stops. This needs, on hire success, a follow-up
   `move(nid: <anchor>, new_parent: <the id the hire response just returned>)` — the only genuinely
   new sequencing logic in this whole feature, everything either side of it already exists.

**One open design question, not decided here:** what happens to the anchor's **existing** relationships
during the splice — its audience grants, its own children, any pending mail — given `move()`'s
existing behavior already governs all of that for a plain drag-to-reparent today. The likely answer
is "nothing special — `move()`'s existing guarantees already cover this exact case since a splice
*is* a move, just with a freshly hired new parent," but that should be confirmed against `move()`'s
own tests rather than assumed, since "insert parent" is the first caller that pairs a fresh hire with
an immediate move on the same node in one user action rather than as two separate, human-paced steps.

Not scoping a build here — the pieces are proven independently; the remaining work is a UI affordance
and one new chained call, not new backend design.
