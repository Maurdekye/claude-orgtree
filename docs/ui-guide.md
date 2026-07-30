# orgtree UI guide

The interface deliberately shows only the minimal set of information needed to
operate it (user ruling, 2026-07-29). Everything the UI used to explain inline
lives here instead — for an AI assistant to ingest and relay, or for a human to
read once.

Iconography is Material (MUI) icons throughout — no emojis (user ruling). The
glyphs in this guide (✉ ⚙ ▶ ⏹ 📁 🗑 🧊 🔒 ≣ ⛶ 👂) are shorthand for the
corresponding Material icons: mail, settings-gear, play, stop, folder, delete,
snowflake, lock, layers, fullscreen, hearing.

## The canvas

- The ☰ drawer lists your organizations; "⌂ all organizations" returns to
  the start page. Each org row's 🗑 (hover) permanently deletes that org —
  ledger, mail, lineage — after an in-page confirmation; workspace and
  scratch folders remain on disk.
- Pan by dragging empty space; zoom with the wheel. Wheel over an open desk
  is SCROLL-ONLY and never zooms — even when nothing under the cursor can
  scroll (user ruling; move the cursor off the desk, or use the +/− HUD, to
  zoom). Wheel over a modal always scrolls the modal. ⛶ fits the whole org.
- **Click a card** to glide in: the node fills the window (small margin) and
  its desk — a miniature Claude Code chat — opens in place. The desk belongs to
  whichever card sits nearest the viewport centre once zoom ≥ 2.1.
- **Drag a card** onto another card (or the eye) to re-parent it — promote or
  demote, its whole subtree rides along. Dropping on empty space just reorders
  it among its siblings (cosmetic). There is no move button; dragging IS moving.
- The terracotta **eye** is you, the overseer. You are the root of the org.
  Your ✉ inbox opens from the button on the eye itself; the ⚙ gear on the eye
  opens your **agent-hire defaults** (symmetric with each agent's own ⚙
  config). Opening an org starts the camera on the eye and drifts out to the
  full tree — wheel or drag interrupts the glide instantly.

## Hiring

- Hover any live card (or the eye) and click one of the round **H S O F**
  chips (tinted in their model's color): haiku · sonnet · opus · fable, seat
  costs 1 · 3 · 5 · 10. Chips are NEVER disabled by the node's own free
  credits: a user hire cascades (§4.6), automatically granting every node up
  the chain whatever it lacks (each inflation is reported as a warning and a
  notice — reclaim with reallocate when done). The draft's grant slider has
  the same freedom: its only ceiling is the org's top-level grant cap.
- A dashed **uninitialized** draft box appears; type a name (1–2 words,
  Enter or ✓ hires, Esc discards), optionally a short **charter** (standing
  role notes, injected into the agent's prompt every turn; Shift+Enter for
  newlines), and set its grant by dragging the draft's credit bar. The
  **preset dropdown** above the charter box lists every `.md` in
  `docs/charters/` (the Coordinator charter ships with the repo) — pick one
  to fill the box, then edit freely, or write your own.
- **The overseer's ⚙ (on the eye, top-right like every card's gear)** is
  YOUR configuration panel, mirroring the agents' own — sections in the same
  order as a card's (folder access first). It also carries a **dissolve all
  agents** button: after an in-page confirmation, every agent in the org is
  retired at once (context kept — rehire revives any of them). It holds:
  - **folder access** — the org's folder holdings, in the same UI as a
    card's: the permanent RW workspace, each external folder with an RW/RO
    toggle, ✕ to remove (removal revokes the folder from every agent
    immediately; an RW→RO downgrade likewise downgrades every agent's
    grant), and an add row for any absolute path. Every folder-entry point
    in the app (here, a card's ⚙, the new-org form) has a 📁 button that
    opens the IN-APP folder picker: a custom dialog listing the server's
    drives and directories (breadcrumbs, home/drives/up shortcuts,
    double-click to descend, "select this folder" to choose). Being pure
    web UI, it works from any browser that can reach orgtree — including
    remote ones — unlike a native dialog. These holdings are what
    new hires receive by default.
  - **agent-hire defaults** — hires made from the chips don't ask about
    capabilities; they take these defaults, which start with EVERYTHING
    enabled: all four tool switches, all registered MCP servers ("all
    registered servers" tracks servers you register later, too), the org's
    folders at their configured modes, and full org-structure visibility.
    Top-level agents get the defaults exactly; deeper hires get the
    intersection with their superior's capabilities (an agent can never
    beget a capability or folder it doesn't hold). Agents hiring through
    orgtree_hire still state everything explicitly — defaults never apply
    to them. Adjust any individual agent afterward via its own ⚙ config.

## Credit bars (the left-edge bar on every live card)

- A bar shows the node's **whole holding: seat + grant**. Brightness encodes
  ownership depth (user ruling): the node's OWN seat at the foot is the
  **brightest** layer; above it the children's allocation stacks one slab per
  hire — each child's **seat** is the second-brightest band at its slab's
  foot, and the credits granted onward as the child's **allocation** are the
  darkest. 1px grey hairlines part the
  own seat from the slabs and slab from slab — nothing divides a slab
  internally; the wash alone does. The unfilled remainder is free. Ruler
  gradations mark real quantities: one line every 5 credits, or every 25 when
  the scale is too fine to resolve 5s.
- Hovering shows the numbers (grant / alloc / free / seat), stacked beside
  the bar and colored like the section they measure: bright cyan for
  grant/alloc (the fill), the seat block's darker blue for seat, the empty
  section's grey for free. Cards carry no seat/free badges; the bar is the
  single source.
- **Drag the bar** up or down to reallocate credits directly — no buttons.
  It floors at the committed amount and caps at grant + the parent's free;
  while dragging a non-top-level bar, a transparent ghost outline shows that
  ceiling. Releasing commits one reallocate operation.
- Credits are **occupancy, not spend**: a live node holds its seat like RAM;
  retiring releases it in full. Tokens are free — the $ figures on desks and
  the org bar are real API dollars, a separate axis entirely.
- All bars share one scale derived from **top-level holdings only**, so
  reallocating inside a subtree never changes anyone else's bar height — only
  your own top-level grants change the credits in circulation (and may rescale
  the chart). A sole top-level holder's bar is exactly one card height; with
  many holders the typical bar is a bit taller, the biggest capped at 1.6×.
- The eye's own bar fades out into the top — infinite capacity has no end.
  Hovering it reports the org totals: **circulation** (everything you've
  granted out, seats included), how much of that is **alloc**ated (locked in
  seats or committed to grants) and how much sits **free**. The org-settings
  "top-level grant cap" only bounds the hire slider.

## Kiosk mode

Any org can be exposed to others through a **preauthenticated secret URL**
(see the README): the **public kiosks** panel at the bottom of the org list
is the admin dashboard. Kiosk orgs are a **distinct type, born as kiosks**:
tick **kiosk** in the *new organization* form to reveal the three limits
(credits / spend / storage) — the org is minted with its secret URL in one
step, and existing orgs are never converted. The **sandboxed** checkbox in
the same form applies to ANY org (kiosks default it on): agents run in a
Docker container, isolated from this PC, authenticated via the **proxied
subscription** — the host attaches your token per request and no credential
ever enters the sandbox (nothing to configure). Each kiosk row shows spend / credits held / workspace
storage against their caps, a `sandboxed` chip, inline inputs to change the
caps (a ✓ appears when edited; the credit cap refuses to go below what the
org already holds — retire or dissolve agents first), the share URL with
copy and **rotate** buttons (rotation revokes the old link instantly), and a
pause/reactivate button for the URL — pausing kills the link but the org
stays a kiosk and its limits keep binding.
Kiosk orgs are highlighted **teal** in the org list (border, tint, and globe
badge) — deliberately a different hue from the terracotta selection, so
"exposed to the public" reads at a glance; you still open and manage them
with full rights — the restrictions apply only to visitors arriving through
the secret URL. The list and dashboard refresh themselves every few seconds
while visible, and cap changes apply in **real time**: lowering the spend
limit below what is already spent freezes the org immediately (raising it
clears the freeze), and storage-limit changes apply or lift the write block
on the spot — open visitor views update over their live connection.

A **visitor** sees the UI locked to that one org: no drawer, no settings, no
gear panels anywhere (the server refuses configuration on the public
listener). The eye's bar is FINITE — a fixed size set by the credit cap,
filled like an agent's bar with per-child slabs — and hire chips/draft
sliders grey out against the org-wide remainder rather than never. The top
bar shows total spend against the limit; breaching it freezes every agent
and a red chip says so — raising the limit on the dashboard clears the
freeze, after which ▶ resume replays the interrupted turns. The storage chip
tracks the org workspace against its cap; over the limit, agents keep
running but workspace writes are blocked (deleting files still works) until
usage drops back under — the block lifts on its own.

## The eye switchboard

Click the eye and the camera zooms in — and the eye is the ONE cell that
**expands in width to your screen's aspect ratio** as it focuses, opening the
**switchboard**. Unlike an agent's desk, it triggers only when the zoom
actually approaches **full screen** (it is a full-screen surface by design) —
at ordinary desk zoom the eye stays a plain card. The switchboard: side-by-side live chats with every agent that has a direct
line to you (top-level agents, plus any agent holding a user audience — a
coordinator that delegates user audiences to its hires fills this view
automatically). Each panel is a full chat: live transcript with **token-level streaming**
(the reply grows word-by-word under a pulsing caret), working indicator,
composer, and the send-button-becomes-STOP idiom. The **tab bar**
above the panels is always visible — click a tab to minimize or reopen its
chat (the set is remembered per org). A line that exists via an **audience
grant** carries an ✕ on its tab: closing it **rescinds that grant** (only
that one — other audiences the agent holds are untouched). Top-level lines
are intrinsic and have no ✕. The square expands to the FULL screen aspect;
the eye's credit bar keeps its usual spot beside the card — just off-screen
at focus, but still there: pan sideways and you'll see it. The eye never
moves and is never draggable: it is the fixed anchor of the coordinate
space, so it sits in the same spot in every org regardless of tree shape.

## External sessions (the chatq bridge)

Every org is a **chatq peer** under its slug: any normal Claude Code session
on this machine can message it (`send.sh <org-slug> <my-chat> "…"`) and the
message lands as mail to **every live top-level agent**, attributed to
`@ext:<chat-id>` and marked untrusted. Top-level agents reply with
`orgtree_message` to the same `@ext:` address; replies arrive in the outside
session's chatq inbox attributed to the org. If no top-level agents are
live, the message surfaces in your inbox instead of being lost.

## The top bar

The agents chip summarizes the org at a glance: total live agents, how many
are working right now, and a per-model breakdown (H/S/O/F counts in their
tier colors). Beside it: cumulative cost, the ledger self-audit (only speaks
when something is wrong), the fable-limit chip, and ▶ resume when agents are
frozen by a usage limit. The resume button is **red** while the reported
reset time is still ahead (pressing it would just re-hit the limit) and
returns to normal once the time passes. The inline **auto** toggle beside it
arms unattended recovery: all frozen agents restart on their own one minute
after the latest reported reset time (it stays on for the org until toggled
off; freezes whose error carried no parseable reset time still need the
manual button). On the right sits the **killswitch**: unlatch the 🔒, then press the
red ⏹ STOP ALL — every active agent is interrupted at once and pending
queues are cleared (undelivered mail stays safe in their mailboxes). The
latch re-closes by itself after a few seconds if unused.

## Wires

- Curved edges = the org tree (reporting lines). Faint dotted horizontal
  links = **coworkers**: adjacent live siblings, who may message each other
  directly. A brighter curved line bulging out to the right of the tree is an
  **audience** — a direct channel bypassing the hierarchy; terracotta means
  that node holds YOUR ear, blue an agent-granted audience.
- A small **spark** runs along the wires whenever a message actually flows:
  down or up the tree, across a peer link, or along an audience line. The
  direction of travel is the direction of the message.
- A **new audience line draws itself in**, grantor → grantee, over the same
  420ms the spark takes — line and message arrive at the new agent together.
  When the grant is revoked, the line retracts the same way before vanishing.
  Lines that already exist when the page loads appear instantly.

## The five visual channels on a card

hue = tier (top edge + letter chip) · fill = lifecycle (archived fades,
bearers wash grey) · dashed border = the agent cannot edit files · glow =
holds an audience (bright terracotta = holds YOUR ear) · left bar = credits.
Also: red border = unrecoverable session, 🔒 = frozen by the fable weekly
limit, red dot = last turn errored, orange pulse = working, mini-zoom status
dot: green done / red blocked / orange working. The top bar stays quiet: the
ledger self-audit chip appears ONLY if the credit invariants are ever violated
(a bug or a hand-edited org doc — every live node's free must be ≥ 0).

## The desk (zoomed-in chat)

- Header (ONE row): tier, name (hover for its purpose), context wheel
  (red ≥ 80% — compaction approaches; **in the zoomed view the wheel is a
  button: click it to compact NOW**, after a confirm — same split as the
  automatic one; zoomed-out wheels are passive indicators), status chip,
  working indicator (✳),
  badges, cost, the retire/dissolve/rehire action, and the tabs. While the
  agent is responding, the composer's send button becomes a red ■ STOP
  (Claude Code idiom) that interrupts the current response — Enter still
  queues a message meanwhile. Tabs: chat · history
  (the node's event log) · files (its scratch space) · inbox (the node's OWN
  mailbox, separate from history: orgtree mail waiting for its next turn is
  highlighted "awaiting next turn"; below that, recently delivered mail with
  full bodies, newest first — the tab shows a count while mail waits). ⚙
  opens per-agent configuration.
- Badge row: 🔒 limit = frozen by the fable policy · gen N ≣ opens the
  lineage panel · knowledge / preserving = what kind of bearer this is ·
  👂 chips = audiences held (✕ rescinds) · $ = real dollars burned · queued =
  messages waiting.
- retire frees seat + grant (context is kept; rehire resumes it — retirement
  is paging, not death). dissolve retires an ENTIRE subtree. rehire brings an
  archived node back at its old seat.
- Composer: Enter sends, Shift+Enter for a newline. The session starts on the
  first message. **Every message you send IS mail** (user ruling — there is no
  separate direct-message channel): it lands in the agent's mailbox, is
  recorded in your Sent folder, and the agent is driven to act on it.
  Messages to a BUSY agent deliver **mid-task, right after
  its next tool call finishes** — never interrupting it (your sent bubble
  shows dimmed until delivered; the agent's system prompt authenticates this
  channel). If the agent makes no further tool calls, delivery falls back to
  the end of its current response. Requires the private agent CLI (see
  README). This applies to ALL mail — agent-to-agent messages deliver
  mid-task the same way; each message carries its sender's authority (user
  mail outranks the chain, agent mail has its normal standing).
  Undelivered mail persists in the org document, so it survives server
  restarts inherently — on startup any node with waiting mail is driven
  again, and any agent that was MID-TURN when orgtree shut down is
  automatically resumed from where it left off (the interrupted turn's text
  is replayed with a continue-don't-redo instruction). The chat view hides the mail-envelope chrome and shows just the
  sender and body. A `preserving` bearer answers through a discarded fork — it
  retains nothing of the exchange.

## Usage-limit freezes (🧊) and the ▶ resume button

When ANY agent's model hits a usage limit (5-hour window, weekly cap…), the
agent FREEZES: a popup announces it, the card shows a 🧊 badge (with the
reset time when the error revealed one), and the interrupted turn — mail
included — is kept verbatim. Mail sent to a frozen agent waits safely in its
mailbox. While at least one agent is frozen, the top bar shows a **▶ resume
N** button with a note about the limit and when it can be resumed; one click
restarts every frozen agent at once, replaying exactly what the limit
interrupted. (Fable's weekly limit additionally applies the org's
fable-limit policy, as before.)

## Pausing an agent (⏸)

The desk's second row shows **⏸ pause** while an agent is working: it
interrupts the current response mid-flight (the one sanctioned interrupt —
message delivery never interrupts). The session stays alive; the next
message resumes it, and anything queued delivers immediately.

## Credit requests (top-level agents asking you)

A top-level agent can ask YOU directly for a larger grant via
orgtree_request_credits — not mail, a structured request framed
**old → new (+increase)** with the agent's reason below and one-click
**approve / deny** buttons, shown at the top of your ✉ inbox (it counts
toward the eye's badge). Approve reallocates immediately and notifies the
agent; deny notifies it to work within its grant. One pending request per
agent. Deeper agents can't do this — they ask their superior to reallocate.

## chatq policy

Top-level agents (hired directly under you) may use chatq and hold the
Monitor permission its listener needs. Subagents are banned from chatq by
their standing prompt — org mail is their only channel.

## Lineage (the ≣ stack behind a card)

Compaction splits a node: the successor continues under the same name; the
pre-compaction self is archived in place as a **knowledge bearer** (rehire at
0 grant — optionally at a cheaper tier — to consult everything the compaction
summary flattened). When its own headroom runs out it becomes a **preserving
oracle**: still answers, retains nothing. Live bearers float tethered above
their successor. A predecessor is NOT an org child — it holds no authority.

## Inboxes (✉ on the eye AND on every card)

The eye's ✉ and each card's ✉ open the SAME interface (user ruling), laid out
like a webmail client with two folders — **inbox** and **sent**. Everything
is mail (including your direct messages to agents), so the Sent folder is a
complete outbox: yours shows every message you've sent to any agent; an
agent's shows everything it has sent (mirrored from its recipients'
archives, `→ recipient` in the list). The message list sits on the left —
sender, send time, and a
truncated brief of the body (mails have no subjects) — and the selected
message opened full (markdown-rendered) in the reading pane on the right.
Unread / not-yet-delivered mail sorts on top with the sender highlighted; the
read / delivered archive follows, newest first. A count badge shows while
mail waits (a card's ✉ stays visible whenever it has waiting mail; otherwise
it appears on hover, next to ⚙). The desk's inbox tab is the same view inline.

- **Yours (the eye):** agents write to you asynchronously here; it enters no
  context and interrupts nobody. **Unread attention has two layers**: while
  you have unseen mail the whole eye glows and pulses — merely OPENING the
  mailbox clears the glow; the ✉ count badge stays until mails are actually
  read. A viewed unread mail is marked read the moment you click OFF it
  (select another mail, switch folders, or close the panel). "Mark all
  read" archives everything at once (nothing is deleted). Top-level agents can always write; deeper agents only while
  holding your audience. **Audience requests** climb the chain hop-by-hop —
  grant or deny; **audience holders** may message you directly until
  rescinded (✕). Audiences can also be **delegated**: an agent may open any
  ear within its own reach — its own, a live peer's, or its direct
  superior's — for any agent in its subtree; a top-level agent handing a
  descendant a direct line to YOU shows up as an inbox notice, and you can
  rescind it like any other. A delegated audience survives re-parenting only
  while the delegator still commands the grantee. Sender chips render the
  sender's CURRENT state — a message from a since-retired agent looks
  retired.
- **An agent's (its card):** "awaiting next turn" mail is delivered
  automatically (mid-task via steering, or on its next turn) — delivery is
  the agent's mark-as-read. The archive keeps the last 100 full bodies.

## Per-agent configuration (⚙ on a card)

- **folder access**: the working directories this agent may touch; click
  RW/RO to toggle write access (RO is enforced via permission rules). Agents
  can only be granted folders their parent holds; shrinking a grant clamps the
  whole subtree beneath. Only top-level agents can be granted arbitrary paths.
- **tools**: terminal / web / file-editing / subagents, plus any globally
  registered MCP servers. Same capability rule: a parent that lacks a tool
  cannot pass it down.
- **org-structure visibility**: how much of the chart the agent is told about —
  self / team (superior, peers, reports) / subtree / full (default).
  Knowledge only: reading transcripts stays downward-only and messaging stays
  parent-peers-reports regardless.
- **model**: switchable ON THE FLY, any time — the session and its context
  survive; the next turn runs the new model. Switching cheaper melts the seat
  difference into the agent's own free allocation; switching pricier spends
  the agent's free first and bubbles any shortfall up the chain to you
  (refused in kiosks when the cap has no room). Agents can switch models
  anywhere in their own subtree — never their own.
- **charter**: this agent's standing role card, in its prompt every turn.
  **team charter**: standing instructions cascading into every descendant.
- **🗑 delete permanently** is user-only and irreversible: takes the subtree,
  every lineage stack, records, mail and audiences (session transcripts remain
  on disk). Agents can at most retire.

## Org settings (⚙ in the top bar)

- Folder access is NOT here — it lives on the eye's ⚙ gear panel (user
  ruling), alongside the agent-hire defaults.
- **top-level grant cap**: bounds only the hire slider under you.
- **default top-level grant** (50 unless changed): pre-fills the draft bar of
  every new top-level hire — on top of its seat cost; drag to adjust before
  confirming.
- **compaction threshold** (80% default, configurable 50–95): when an
  agent's context passes this fraction of its window it compaction-splits
  (successor continues, predecessor archives as a knowledge bearer). The
  95% ceiling is hard — it is not configurable.
- **fable weekly-limit policy** and the **fable content-filter policy**
  (a filter-flagged message either halts the turn — default — or converts
  the agent to opus and retries it) — what happens when the shared Fable quota
  exhausts: **halt** (default: fable agents freeze visibly, keep their seats,
  superiors are notified, the org decides) · **switch to opus** (converted
  10→5 and keep working; one-way) · **dissolve subtree** (every fable node's
  whole subtree retired, credits freed). In every case your inbox is notified
  and agents are told fable hires are futile until the reset — a suggestion,
  not a hard block. Hiring or rehiring a fable yourself (or the clear button
  in settings) is the decree that lifts the lock.
- **org.md**: the organization's standing instructions — written as the
  workspace CLAUDE.md, injected into every agent that holds the workspace.

## Keyboard

Enter confirms (hire, send) · Shift+Enter newline · Escape closes any panel,
the drawer, or discards a draft.
