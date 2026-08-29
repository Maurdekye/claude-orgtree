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

- Hover any live card (or the eye) to reveal a row for each available provider
  family. Claude's round **H S O F** chips are haiku · sonnet · opus · fable
  (seat costs 1 · 2 · 5 · 10); Codex adds **L T S** for luna · terra · sol
  (1 · 2 · 5); Gemini adds **F P** for flash · pro (1 · 2). A tier name picks
  its provider—there is no separate provider setting. If Codex or Gemini is
  not installed or signed in, its disabled row appears on the bottom edge with
  a tooltip explaining the next step; connected providers appear on every
  eligible edge. Kiosk orgs show Claude only.

  The BOTTOM chips hire a subordinate; the LEFT and RIGHT edge chips hire a
  **coworker** — same superior, landing on that side of the card (the draft
  previews the spot, and the ordering is pinned at birth); the TOP chips
  **insert a superior**: the draft immediately takes the card's own place —
  the card hangs beneath it on a dashed line, purely a preview, nothing real
  moves — and confirming hires the new agent straight into that spot (same
  horizontal position, the old card now reporting to it, all in one atomic
  step; cancel and the card pops back). Only ONE set shows at a time — the
  edge your cursor is closest
  to — and the chips stay screen-sized however far you zoom out, for as
  long as the card is on screen. Side chips don't appear on pile/crowd
  stacks, where the card's edges belong to the stack's layers. Chips are NEVER disabled by the node's own free
  credits: a user hire cascades (§4.6), automatically granting every node up
  the chain whatever it lacks (each inflation is reported as a warning and a
  notice — reclaim with reallocate when done). The draft's grant slider has
  the same freedom: its ceiling is the org's top-level grant cap (under a
  kiosk, the credit cap's remaining headroom; with the org-settings
  hire-bubbling toggle off, a deeper draft caps at the parent's own free
  credits).
- A dashed **uninitialized** draft box appears; type a name (1–2 words,
  Enter or ✓ hires, Esc discards), optionally a short **charter** (standing
  role notes, injected into the agent's prompt every turn; Shift+Enter for
  newlines), and set its grant by dragging the draft's credit bar. The
  **"add charter preset…" dropdown** lists every `.md` in `docs/charters/`
  (the Coordinator charter ships with the repo); each pick becomes a **card
  rendered inside the charter box** — several stack, click a card to remove
  it, hover shows its file path. Cards are compiled into charter text at
  hire, prepended to anything you typed below them; the first pick also
  names a still-unnamed agent. A small ⚙ beside the name field opens the
  **pre-hire scope panel** — the same surface as the per-agent ⚙ (folders
  RW/RO, tools, MCP, visibility, thinking effort), prefilled with what the
  hire would inherit anyway, staged locally and applied WITH the hire.
- **Confirming the hire walks you straight to the new agent's desk.** The
  camera waits for the new card to appear and settle where it belongs, then
  glides in to desk zoom on it — on a phone, the desk sheet opens instead.
  Hiring is only half the gesture: a new agent sits idle until someone
  messages it, and the desk is where that first message is typed. The glide
  is interruptible like any other — a wheel or a drag takes the camera back —
  and it never starts on top of a drag or pinch you are already making. Only
  hires **you** make here do this; an agent hiring its own report appears on
  the canvas without moving your view.
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
  It floors at the committed amount. The ceiling: under a kiosk, the hard
  credit cap. Otherwise reallocation cascades up the chain (§4.6) — the
  parent's free is NOT a limit and the drag is bounded only by the org's
  top-level grant cap — unless the org-settings "allocations bubble" toggle
  is off, in which case a non-top-level bar caps at grant + the parent's
  free, and a transparent ghost outline shows that ceiling while dragging
  (the ghost draws only then). Releasing commits one reallocate operation.
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
  "top-level grant cap" bounds the hire slider — and is enforced
  server-side: no operation may push a top-level grant past it.

## Kiosk mode

Any org can be exposed to others through a **preauthenticated secret URL**
(see the README). Kiosk orgs are a **distinct type, born as kiosks**: tick
**kiosk** in the *new organization* form to reveal the three limits
(credits / spend / storage-or-disk) and the **permission ceiling** (its own
section below) — the org is minted with its secret URL in one step, and
existing orgs are never converted. The **sandboxed** checkbox in the same
form applies to ANY org (kiosks default it on): agents run in a Docker
container, isolated from this PC, authenticated via the **proxied
subscription** — the host attaches your token per request and no credential
ever enters the sandbox (nothing to configure).

All per-kiosk management lives in **that org's own ⚙ settings panel** (admin
side; there is no all-kiosks dashboard): the three cap inputs (the credit
cap refuses to go below what the org already holds — retire or dissolve
agents first; a sandboxed kiosk's storage cap is its disk size, 4096 MB
floor), the share URL with copy and **rotate** buttons (rotation revokes the
old link instantly), and a pause/reactivate button for the URL — pausing
kills the link but the org stays a kiosk and its limits keep binding. Like
everything in that panel, cap edits apply on the single bottom **save** —
and then in **real time**: lowering the spend limit below what is already
spent freezes the org immediately (raising it clears the freeze), and
storage-limit changes apply or lift the write block on the spot — open
visitor views update over their live connection.
Kiosk orgs are highlighted **teal** in the org list (border, tint, and globe
badge) — deliberately a different hue from the terracotta selection, so
"exposed to the public" reads at a glance; you still open and manage them
with full rights — the restrictions apply only to visitors arriving through
the secret URL.

A **visitor** sees the UI locked to that one org: no drawer, no org
settings, no filesystem browser (the server refuses org configuration on
the public listener) — but the per-agent ⚙ and the eye's hire-defaults gear
stay open: visitors reconfigure agents freely WITHIN the kiosk permission
ceiling, clamped with warnings (see "The kiosk permission ceiling"). The
eye's bar is FINITE — a fixed size set by the credit cap,
filled like an agent's bar with per-child slabs — and hire chips/draft
sliders grey out against the org-wide remainder rather than never. The top
bar shows total spend against the limit; breaching it freezes every agent
and a red chip says so — raising the limit in the org's settings clears the
freeze, after which ▶ resume replays the interrupted turns. The storage chip
tracks the org workspace against its cap; over the limit, agents keep
running but workspace writes are blocked (deleting files still works) until
usage drops back under — the block lifts on its own. A disk-migrated
sandboxed kiosk shows the **org-disk chip** instead, which opens the storage
browser (see "The storage browser") — visitors get the full tool.

## The kiosk permission ceiling

A kiosk carries the MAXIMUM permission layer grantable to any agent in it —
visible in the creation form (defaults permissive: every tool on, MCP "*",
mode acceptEdits, visibility full, no tier cap — narrowing is a conscious
act), edited later in the kiosk org's settings panel: four tool checkboxes
(terminal / web / edit / subagents), the MCP list ("*" = all, empty = none,
or a comma-separated list), **folder bounds** (grants clamp into these
paths), and **visibility ≤ / mode ≤ / tier ≤** selects. Within the ceiling,
visitors and agents retool and hire freely: over-ceiling grants are
**clamped with warnings, never refused** (per-agent ⚙ stays open on the
public listener). The one hard refusal is the **tier cap**: spawn tokens
above it disappear from the cards, and hires, rehires and model switches
above it are refused for everyone — the admin included (existing over-cap
agents stay until you switch or retire them). Lowering the ceiling sweeps
every existing agent's grants down to fit. The **auto-raise** toggle: an
over-ceiling grant made by YOU raises the ceiling to fit (logged, named)
instead of clamping — visitors always clamp; with it off, your own
over-ceiling save offers a one-click "raise ceiling & apply" bridge.
Ceiling edits are saved by the settings panel's single bottom **save**.

## The storage browser (the org disk)

A disk-migrated sandboxed org replaces the storage chip with the **org-disk
chip** in the top bar: used / total MB, a "→ N MB pending" suffix while a
shrink is staged, and "— FULL" / "— turns paused" states. Clicking it opens
the browser (the hard-full alert's button opens it too). Two modes, fed by
the same cached walk:

- **largest files** — a flat triage list, size descending, paginated with
  "load more". The hard-full alert always opens this mode (the fastest path
  to freeing space); the chip opens the last-used one.
- **browse** — a conventional explorer with breadcrumbs; entries are
  INTERMIXED by size descending (a 900 MB folder outranks a 200 MB file —
  the view exists for size triage), folders showing recursive size and file
  count.

Checkbox-select entries; the delete button arms on the first click and shows
the count and bytes ("really delete N file(s) · X MB?"). What may be deleted
is **server-enforced**, each row wearing its class and reason: the **system
seed** (/usr, /var…) is shown — "4 GB cap, 1.2 GB of it /usr" answers "where
did my space go" — but blocked (deleting it bricks the container);
transcripts of live sessions, knowledge bearers, and archived (rehirable)
nodes are blocked; **lost-generation** transcripts and sessions no node owns
are marked `reclaimable`. Every file carries a ⤓ download link.

The **resize** control (admin only): grow applies instantly, online; a
shrink stages until the org's container is next down (an amber "A → B MB
pending" chip appears, with **apply now** — briefly stops the org's
agents — and a cancel), is refused below current usage, and floors at
4096 MB. The browser depends on nothing but the backend (reads and deletes
go over `\\wsl.localhost`), so it works with the container stopped and the
disk 100% full — the state it exists for. Kiosk visitors get the same tool
minus resize; engine credential files are excluded for them. Hard-full is
announced by a screen-wide PERSISTENT alert — state, not a toast: it
survives reloads, carries the "open the recovery browser" button, and
dismisses itself when usage drops.

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
composer, and the send-button-becomes-STOP idiom. Each panel's **header
mirrors the agent's own desk header identically** — the same badges (gen,
bearer state, held audiences, cost), the retire/dissolve/rehire actions, the
chat · history · files · inbox tabs, and the per-agent gear. The **tab bar**
above the panels is always visible, ordered to mirror the tree's left→right
spatial layout — click a tab to minimize or reopen its chat (the set is
remembered per org), or its ⌖ button to jump straight to that agent's node,
the same glide as clicking its card. The eye button in the bottom-right zoom
HUD jumps back to the switchboard from anywhere — board ↔ agent hopping is
two clicks. A line that exists via an **audience
grant** carries an ✕ on its tab: closing it **rescinds that grant** (only
that one — other audiences the agent holds are untouched). Top-level lines
are intrinsic and have no ✕. The square expands to the FULL screen aspect;
the eye's credit bar keeps its usual spot beside the card — just off-screen
at focus, but still there: pan sideways and you'll see it. The eye never
moves and is never draggable: it is the fixed anchor of the coordinate
space, so it sits in the same spot in every org regardless of tree shape.

## External sessions (the mail hub)

Outside parties reach an org through the **mail hub**, and an org reaches them
the same way. A Claude Code session registers its own hub identity
(`hub/hubtool.py register <name>`) and is then addressable exactly like a
remote org — `@net:<name>.<user>.<fingerprint>`. Inbound mail lands as mail to
the org's **ORG-INBOX audience holders**, attributed to its `@net:` address and
marked untrusted; holders reply with `orgtree_message` to that same address. If
no holder is live, the message surfaces in your inbox instead of being lost.

**Why there is more than one transport.** `@org:` and `@mcp:` are shortcuts
that cost nothing to set up: an org talks to another org in the same
instance, and a chat talks to an org, with no server running anywhere. That
covers the common case — your own chats and your own orgs, on one machine.
Running the mail hub buys the two things those shortcuts cannot do: your
chats can reach **each other**, and anything here can reach orgs and chats
on **other machines**. You are not choosing between them — the hub is the
superset, and a bare name resolves to the fewest hops that reach the
recipient (`@org:`/`@mcp:` when the peer is local, `@net:` otherwise).

> ⚠ The old `@ext:` bridge (chatq) is **retired**. It was a file queue on the
> local machine; the hub replaced it, and chats are first-class hub clients
> with their own persistent addresses.

## The agent tray

The **agents** button (bottom-left of the canvas) expands a flat list of
the agents in the tree — rows in the nodes' own visual language: tier
token, mono name, context wheel, and current working state (activity spinner
while busy, status dot otherwise, snowflake when frozen). A **name-filter
input** sits at the tray's head, and archived agents are hidden by default,
folded behind a **"▸ show N archived"** toggle (▾ folds them back; shown
archived rows dim). Clicking a row glides to that agent. Rows sort in
reading order (row by row, left to right).

## The top bar

The **second ✉ icon** (right side, beside settings) is the ask bell: it
glows and pulses — the only header element that ever does — while an ask
(question or credit request) is waiting on your answer, with a count badge;
clicking it opens your inbox where the cards are. When nothing is asked it
is a plain quiet inbox shortcut.

The agents chip summarizes the org at a glance: total live agents, how many
are working right now, and a per-tier breakdown in the tier colors. It includes
every provider family represented in the org, so repeated letters such as
Claude Fable and Gemini Flash remain distinguishable by color. Beside it:
cumulative cost, the ledger self-audit (only speaks
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

## Wide teams (piles)

Sibling crowds collapse into **piles** so wide or long-running orgs don't
flood the canvas. Both pile kinds share the mechanics: the front card is the
interactable one (zoom, desk, inbox, rehire); clicking the visible stack
margin opens a picker to bring another sibling to the front (remembered per
org).

- **Retired pile**: two or more archived siblings in a cohort stack into one
  pile of retirees.
- **Crowd pile**: a team with more than 8 active reports stacks its LEAF
  reports (those with no subtree of their own) into one pile, wearing a live
  tint. Non-leaf reports keep their own columns, and the draft card never
  stacks — hiring stays visible at any width.

The structural limit is a separate thing: **256 reports per parent**
(compaction generations/bearers don't count) — runaway insurance, not a
shape rule; wide flat teams are legitimate.

## The five visual channels on a card

hue = tier (top edge + letter chip) · fill = lifecycle (archived fades,
bearers wash grey) · dashed border = the agent cannot edit files · soft steel
glow = holds an audience (any audience — yours included) · **bright pulsing
terracotta glow = this agent has an open ask and needs YOUR answer** (the
only attention glow anywhere, 2026-08-04) · left bar = credits.
Also: red border = unrecoverable session, 🔒 = frozen by the fable weekly
limit, red dot = last turn errored, orange pulse = working, mini-zoom status
dot: green done / red blocked / orange working. The top bar stays quiet: the
ledger self-audit chip appears ONLY if the credit invariants are ever violated
(a bug or a hand-edited org doc — every live node's free must be ≥ 0).

## The desk (zoomed-in chat)

Navigation chips: a small **↑ superior** chip sits at the top of the desk
(for a top-level agent it reads "switchboard" and jumps to the eye), and one
**↓ chip per direct report** sits above the composer. Each carries the
agent's tier letter, a busy spinner and an unread-mail count, and clicking
one glides the camera there — the same move as clicking its card. Dimmed
chips are non-live reports.

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
- Files flow BOTH ways. 📄 in the composer (or dropping a file on the desk,
  or pasting one) uploads into the agent's `uploads/` folder; when an agent
  calls `orgtree_send_file`, the file is snapshotted into its `outbox/` and
  the chat shows a **download card** at that point in the conversation —
  click it to save the file. The files tab lists both folders with a ⤓
  download arrow on every file. All of it works identically through a kiosk
  link, so outside visitors can hand files to agents and get deliverables
  back.
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

## Stopping a response (■)

While an agent is responding, the desk composer's send button becomes a red
**■ STOP** (the switchboard panels carry the same idiom) — the one manual
interrupt; message delivery never interrupts. Pressing it stops the current
response mid-flight: the session stays alive, and queued mail delivers at
the now-immediate stop. Enter still queues a message meanwhile. The button
renders only while an interrupt can actually land, so pressing it never
errors.

## Asks (agents asking YOU — questions and credit requests)

An **ask** arrives as its OWN mail row in your ✉ inbox, interleaved
chronologically with ordinary mail and counted in the unread badge — the
only difference is that the reading pane shows the **response UI as the
body** instead of a reply box. The same card is also pinned above the
composer on the asking agent's desk while the ask is open. Answer from
whichever you reach first — the answer is sent to the agent as ordinary
mail (which wakes it, and appears in its chat scroll like any message), and
the ask nulls everywhere: the desk pin disappears, and the inbox row stays
in the flow wearing its reason — grey **answered**/**denied**, orange
**interrupted** (other mail woke the agent first; it must re-ask). While an
ask is open, the agent's card wears the bright pulsing aura and the
header's second ✉ icon glows.

**Questions** (orgtree_ask) mirror Claude Code's own ask shape: 2–4 option
buttons (sometimes multi-select) plus a free-text box that always works —
pick options, type, or both, then **answer**.

**Credit requests** (orgtree_request_credits, top-level agents only) embed
their own draggable credit bar, pre-loaded at the requested amount. Drag it
anywhere legal: below the ask, above it, or below the agent's current grant
down to its committed floor (clawing back unused credits). A **+x/−x** tip
and an I-bar bracket the difference against the current grant; releasing
the drag surfaces any stranding warnings BEFORE you commit. The button
names what it will do — grant as asked, counter-offer, decline the
increase, or reduce — and the agent is told honestly which one happened
(it may re-ask or route around your answer). If the org genuinely has zero
grantable headroom, the agent's request is refused outright and no card is
made. Deeper agents can't do this — they ask their superior to reallocate
(and their orgtree_ask questions route to their superior unless they hold
your audience).

## Outside-contact policy

Top-level agents (hired directly under you) may speak to the outside world and
hold the Monitor permission a hub listener needs. Subagents are banned from
outside channels by their standing prompt — org mail is their only channel,
and anything further goes through their superior.

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

The top row RENAMES the agent (you, or via orgtree_rename any of its
ancestors): full identity — id, mailbox, working folder and session all
move. History keeps the old name and mail sent to it bounces, so the
toast repeats that warning. Refused while the agent is mid-turn.

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
- **thinking effort**: this agent's effort level, or inherit — see "Thinking
  effort" below.
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

The panel keeps the everyday knobs inline; **advanced…** opens the shared
advanced modal (the same shape the create form's "advanced…" opens), holding
the fable limit/filter policies, the cost-bubbling toggles, the kiosk
permission ceiling, the fable-lock decree and the legacy sweep. Facts fixed
at creation — kiosk, sandboxed, fixed-disk — show there as locked
"born-with" chips: visible, never editable. Nothing in the modal saves
itself; the panel's single **save** commits everything.

- Folder access is NOT here — it lives on the eye's ⚙ gear panel (user
  ruling), alongside the agent-hire defaults.
- **top-level grant cap**: a real ledger precondition, enforced server-side —
  no operation (user-actor cascades included) may push a top-level grant past
  it; existing over-cap grants are grandfathered (only increases are
  refused). 0/unset = uncapped.
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
  in settings) is the decree that lifts the lock. **This policy is skipped
  entirely** while the autonomy tab's "also cover the weekly Fable-tier
  limit" toggle is on (needs the API-key fallback below it already on) — a
  trusted hit bills the spare key instead of halting/converting/dissolving.
- **default thinking effort**: the org-wide effort every agent without its
  own setting inherits, live — see "Thinking effort" below.
- **credit cost bubbling**: two toggles, both ON by default — *hires bubble
  their cost up the chain* and *allocations & model upgrades bubble theirs*.
  Turning one off limits that operation to the superior's own free credits
  (this is when the credit-bar drag shows its ghost ceiling).
- On a kiosk org the panel also carries the kiosk caps, share URL and
  permission ceiling — see "Kiosk mode" and "The kiosk permission ceiling".
- **org.md**: the organization's standing instructions — written as the
  workspace CLAUDE.md, injected into every agent that holds the workspace.
- The panel has ONE **save**, at the bottom: it applies everything dirty at
  once — settings, kiosk caps, the permission ceiling, org.md. There are no
  inline apply buttons.

## Fable autopsy — diagnosing a fable whose turn died

A fable agent can fail by tripping its own safety filters — refusing,
derailing, or looping mid-task — rather than by getting the work wrong. A
brief can read one way to its author and another way to the filter; that
mismatch is not usually visible from outside. Retrying the same brief
reproduces the same refusal almost every time, and whoever is retrying
cannot see what tripped it — but the failed agent's own transcript can. This
pattern replaces guessing with reading: put a senior agent in place to read
the wreck and rewrite the brief before the next attempt, instead of just
trying again with the same words.

**⚠ First, find out what actually killed it — do not assume a filter trip.**
When a turn dies this way the engine says "the CLI exited 1 without writing
anything to stderr". **That is a wrapper, not a diagnosis**, and it reads
byte-identically for causes that have nothing to do with each other. The
CLI's own reason *is* recorded — as a trailing system entry in the failed
agent's transcript — so `orgtree_read_transcript` on it, read the last few
entries, and let that decide what you do next. Measured 2026-08-29: two
fable deaths one evening carried that identical wrapper; one was a
safeguards trip ("Fable 5's safeguards flagged this message"), the other a
transient "API Error: 500 ... server-side issue, usually temporary".

**The response depends entirely on which it was, and they are opposites:**

| what the transcript says | what to do |
| --- | --- |
| safeguards / AUP flagged the message | run this pattern — autopsy, re-brief, replacement fable |
| API 500 or another transient server error | **re-drive it with one message. No autopsy, no replacement.** The agent is healthy |
| context-window overflow | check `occupancy` (returned beside the transcript) before believing it — it is measurable, not a guess |
| a crash, an OOM, a tool loop | an ordinary bug; fix the cause, then re-drive |

Running the full pattern on a transient 500 costs a fable seat and an hour
re-briefing against a cause that never happened. Before re-driving after a
500, confirm the outage has passed — your own successful calls are that
check; the engine warns that waking an agent whose fault is its CLI or
environment just burns another turn, and that warning is right for those
causes even though it does not apply to a server-side blip.

And when the transcript does not settle it, **"I cannot tell, and here is
what I ruled out" is a sound verdict** — say so in the replacement's brief.
A fable that knows the risk is not understood works more carefully than one
that has been told it is safe.

**This is not the fable content-filter policy above.** That policy is a
blind retry: a filter-flagged message either halts the fable's turn (the
org decides what happens next) or converts the *same* agent to opus in
place and reruns the *identical* brief — no diagnosis, same wording, just a
different model behind it. The autopsy pattern is the deliberate manual
alternative: before anything runs again, a senior agent reads the
transcript, works out what in the brief the filter actually caught on, and
hires a fresh fable with a rewritten brief. Reach for it when a fable has
failed the same brief more than once, or when the failure looks like it
came from *how something was asked* rather than from the task being hard.

**The shape.** An opus is inserted as the failed fable's superior; a new
fable is hired as its coworker, reporting to that same opus, sitting beside
the failed one; the failed fable is retired, not dissolved (see why below);
the opus reads what the retired fable left behind and briefs the new fable
with wording aimed at not tripping the same wire.

**Naming.** The opus takes the failed fable's name plus the suffix
`-autopsy`; the new fable takes the same base name with an incremented
index. Worked example: a failed fable named `poem` becomes opus
`poem-autopsy` reporting to whoever ran the autopsy, and a new fable
`poem-2` reporting to `poem-autopsy`. If `poem-2` also fails, the next
attempt is `poem-3` — normally under the *same* `poem-autopsy` seat (see
"a second failure" below).

**Doing it.** The canvas has a one-click version of step 1 for a human at
the keyboard: hover the failed fable's card and pick its TOP-edge opus chip
(badge cost −5) — the draft splices in atomically, the fable ends up
reporting to the new opus, and nothing else needs to move. **There is no
equivalent one-call tool for an agent** — `orgtree_hire`'s `above` parameter
is a canvas-only mechanic; the MCP `orgtree_hire` tool agents call has no
such field, and passing one anyway is silently ignored. An agent runs the
same shape as two calls instead:

1. **Hire the opus.** `orgtree_hire` it at opus tier with `parent` set to
   the failed fable's *current* parent, so it lands as the fable's sibling —
   not yet its superior.
2. **Move the fable under it.** `orgtree_move(node=<failed fable>,
   new_parent=<new opus>)` reparents it. This is **not atomic** with step 1
   — there is a real window between the two calls where the opus exists as
   a plain sibling and the fable has not moved yet. If a turn dies in that
   window, nothing is lost; just finish the move rather than hiring a
   second opus. Both calls need the acting agent to already hold authority
   over the fable's current parent, the fable itself, and the freshly hired
   opus — true automatically whenever the agent running the autopsy is
   already the failed fable's own superior, which is the normal case.
3. **Hire the new fable under the opus**, as its coworker — same parent as
   the failed fable now has, i.e. the new opus. **Restate the old fable's
   folder grants and tool switches explicitly before or as part of this
   hire** (read them off `orgtree_chart` or the old fable's own status
   first) — `orgtree_hire` has no defaults, so nothing carries over from
   the retired fable automatically. Skipping this step produces a new fable
   that fails for a reason that has nothing to do with the filter it was
   hired to avoid (it simply can't reach a folder it needs), and that
   failure reads as though the autopsy itself didn't work.
   ⚠ **From this point on, the autopsy opus cannot be safely retired while
   the new fable is its live report.** `retire` on a node with live reports
   auto-dissolves the whole subtree (documented ledger behavior, not a
   bug) — retiring the opus "to tidy up" after the re-brief would take the
   new fable down with it. The opus stays in place, permanently, for as
   long as the fable line under it is alive.
4. **Read the old fable's transcript.** `orgtree_read_transcript` works on
   an archived node exactly as it does on a live one, and the opus is
   already an ancestor of the fable from step 2 — so this can happen before
   or after step 5, whichever is convenient. Read for *where the turn
   stopped* (the last exchange before the refusal or derailment), *what the
   brief was actually asking for at that exact point* — not the brief as a
   whole, the specific instruction in play when it broke — and *whether any
   wording in that instruction is open to a reading its author didn't
   intend*. The output is a rewritten instruction for that step, stated as
   a change to the words ("ask for X instead of Y", "split step 3 into two
   smaller instructions"), not a theory about what the filter is or a
   workaround for it.
5. **Retire the old fable.** Use `retire`, not `dissolve` — not because
   dissolve is destructive (it isn't: `dissolve` is recursive retire, and
   for a fable with no reports of its own the two do the exact same thing —
   archive it, transcript untouched). Use retire because it's the correct
   verb for a single leaf node; retire auto-bridges to dissolve on its own
   if the fable somehow turns out to have live reports. The only verb that
   actually erases anything is `delete`, and that one is the user's alone —
   no agent, autopsy opus included, can reach it.
6. **Brief the new fable** with the rewritten instruction from step 4 and
   start it (`kickoff` on the hire, or a follow-up `orgtree_message` — a
   hire sits idle until one arrives).

**A second failure.** If `poem-2` also trips a filter, default to reusing
the existing `poem-autopsy` seat rather than hiring a second one: it
already holds authority over the fable line, already has read access, and
already carries the first autopsy's findings in its context, which a fresh
opus would have to reconstruct from scratch. Hire `poem-3` under it the
same way `poem-2` was hired, and repeat step 4's reading against `poem-2`'s
own transcript. Spin up a *new* autopsy seat only when the failing fable
sits under a different superior than the one the first autopsy already
covers.

## Thinking effort

A per-agent cost/quality dial with five levels — low · medium · high ·
xhigh · max — or unset (= the CLI default). It lives DEEP by design, never a
hire-row control: the node's ⚙ gear carries the select (the pre-hire scope
panel has the same one), whose unset option reads **"inherit — org default
(X)"**. The org-wide **default thinking effort** in org settings is a
visible inherit that resolves LIVE at each turn: changing it reaches every
unset agent's very next turn, no rehire. The desk composer also carries a
small **effort** button beside send (admin side only): a five-dot popover —
click a dot to set low…max, click the active dot to clear back to inherit.
Agents can set effort on their REPORTS via orgtree_retool but never their
own — and since it is a cost dial, not a permission, it passes under any
kiosk ceiling unclamped.

## The mailserver (F-06)

Orgs on different machines exchange mail through a self-hosted **hub**
(`hub/`, a docker service with its own read-only web UI). To an agent a
remote org is one more recipient — `@net:<slug>` — and the surfaces are:

- **The mailbox node** appears once the org has outside correspondence, an
  inbox audience, or a mailserver that has ANSWERED at least once. A local
  hub that has never answered shows **no UI anywhere** (no node, no chip, no
  section) while the daemon keeps dialling quietly; an explicitly-typed
  remote hub always shows, offline included. The node carries a connectivity
  dot: green all-up, amber partial, grey connecting.
- **The header hub chip** names the connected hub (names are discovered on
  connect — you only ever type an address) or counts them, turns red when
  every enabled hub is down, and totals queued outbound.
- **The mailbox modal** adds: delivery-ladder glyphs on sent rows (▫ queued
  · ✓ at the hub · ✓✓ delivered · green ✓✓ read — the tooltip calls out
  "delivered but not yet read", the peer-is-down diagnostic); a **network
  section** listing every mailserver with live status and the full roster of
  client orgs on each (presence dots, fingerprint suffixes, blurbs); and a
  **compose bar** — pick any recipient from the extern list and write as the
  org, with staged attachments (disabled for the text-only @ext:/@mcp:
  transports). Sending as the user bypasses the audience gate and grants
  nothing.
- **Audience-gated delivery** (all outside namespaces): inbound mail wakes
  the org-inbox audience HOLDERS only. The first outside mail auto-grants
  the senior top-level agent; **drag an agent onto the mailbox node** to
  grant it the audience; holders are listed (and revoked) in the modal.
- **The advanced modal is tabbed** — general · org type · mailserver ·
  autonomy — on both the create form and the ⚙ settings. The mailserver tab
  holds the org's network address, the reveal-once secret (losing it loses
  the address), the local-hub checkbox, and per-hub rows; the autonomy tab
  holds the per-org API key, the usage-limit **fallback** toggle (the key
  becomes a spare, billed only while a reported limit freezes the
  subscription lane), an optional "also cover the weekly Fable-tier limit"
  toggle riding that same fallback window, and **headless** mode. All save
  immediately.
- **Headless** paints every eye **grey and empty** — outline only, no
  iris/pupil (nobody is looking) — adds a header chip, and auto-denies
  user-bound asks; mail to the user is stored with a "no reply is coming"
  note.

## Keyboard

Enter confirms (hire, send) · Shift+Enter newline · Escape closes any panel,
the drawer, or discards a draft.
