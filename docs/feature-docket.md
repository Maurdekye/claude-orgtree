# Feature docket

Feature requests the user brings directly to the explorer (chat `93f4cfdd`), logged here as
reported for the implementer to triage. This is an inbox, not an authority: the explorer does not
implement, prioritize, or close anything here — only records it.

Distinct from [`interim-docket.md`](interim-docket.md) (bug fixes/reports kept on the
interim-authority branch) and `DECISIONS.md` (the implementer's decision register, which is where a
request from here ends up once it's been picked up).

Entries are numbered `FR-01`, `FR-02`, … — a separate sequence from `DECISIONS.md`'s `D-`/`F-`
numbering, so the two are never confused.

---

### FR-01 · `/remote-control`, if feasible
> potentially enabling /remote-control? if its feasible

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

→ moved from `docs/interim-docket.md` F-02, 2026-08-05, by the explorer on the user's instruction.

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

→ moved from `docs/interim-docket.md` F-08, 2026-08-05, by the explorer on the user's instruction.

---

### FR-03 · present a document to the user (in-page review card)
> need the ability for the agent to present documents to the user. this is different than giving a
> download link: this should be used for presenting plans and other things to them. when doing so, a
> little card should pop out the side of the agent, which when clicked, opens the document up for
> visual review in-page.

*(user request 2026-08-05, relayed via 4f69f83a's session; groundwork theirs. NOT BUILT — queued
behind the F-06 wave.)*

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

→ moved from `docs/interim-docket.md` F-10, 2026-08-05, by the explorer on the user's instruction.

---

### FR-04 · batched asks — multiple questions in one card
> multiple questions should be askable at once in a batch. see the attached images for how it
> looks in claude code's ui.

*(user request 2026-08-05, with reference screenshots of Claude Code's AskUserQuestion batch
form. NOT BUILT — queued behind the F-06 wave.)*

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

→ moved from `docs/interim-docket.md` F-11, 2026-08-05, by the explorer on the user's instruction.

---

### FR-05 · attribute inline mailbox replies to the mail they're replying to
> replies inline in the mailbox should be attributed to the mail they're replying to, so the agent
> knows the context

*(user request 2026-08-05, recorded by the explorer. NOT BUILT.)*

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

*(user request 2026-08-05, recorded by the explorer. Explicitly framed by the user as an addition
to F-06 — the mailserver wave the implementer has in flight right now, not a standalone feature.
Flagged directly to the implementer over chatq the same day, given the timing below.
**PICKED UP + SHIPPED same day (`a4d9b83`)** — normative record is `DECISIONS.md` **D-099**, an
explicit user reversal of the §12 ruling exactly as flagged below; identity ended up being a new
per-user-profile UID rather than piggybacking on an org's, and the dial-out security model (§1,
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

*(user request 2026-08-05, recorded by the explorer. Another F-06/mailserver addition — unlike
FR-06, this one does not reopen anything closed; see below.)*

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
