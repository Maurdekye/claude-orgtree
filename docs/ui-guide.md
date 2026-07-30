# orgtree UI guide

The interface deliberately shows only the minimal set of information needed to
operate it (user ruling, 2026-07-29). Everything the UI used to explain inline
lives here instead — for an AI assistant to ingest and relay, or for a human to
read once.

## The canvas

- The ☰ drawer lists your organizations; "⌂ all organizations" returns to
  the start page. Each org row's 🗑 (hover) permanently deletes that org —
  ledger, mail, lineage — after an in-page confirmation; workspace and
  scratch folders remain on disk.
- Pan by dragging empty space; zoom with the wheel. Wheel over an open desk
  scrolls whatever is under the cursor when it can scroll that way, and falls
  back to camera zoom when it can't (empty chat, list at its end). Wheel over
  a modal always scrolls the modal. ⛶ fits the whole org.
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
  costs 1 · 3 · 5 · 10. A disabled chip means the node's free credits don't
  cover that seat (its tooltip shows the arithmetic).
- A dashed **uninitialized** draft box appears; type a name (1–2 words,
  Enter or ✓ hires, Esc discards) and set its grant by dragging the draft's
  credit bar.
- **Agent-hire defaults (⚙ on the eye).** Hires made from the chips don't ask
  about capabilities — they take the org's defaults, which start with EVERY
  capability enabled: all four tool switches plus all registered MCP servers
  ("all registered servers" tracks servers you register later, too).
  Top-level agents get the defaults exactly; deeper hires get the
  intersection of the defaults with their superior's capabilities (an agent
  can never beget a capability it doesn't hold). Agents hiring through
  orgtree_hire still state every switch explicitly — defaults never apply to
  them. Adjust any individual agent afterward via its own ⚙ config.

## Credit bars (the left-edge bar on every live card)

- A bar shows the node's **whole holding: seat + grant**. The solid block at
  the foot is the seat itself; above it, the bright fill is what's allocated
  to children, one slab per hire (each slab = that child's seat + grant, its
  seat being the darker band at the slab's foot). 1px grey hairlines part the
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

## Wires

- Curved edges = the org tree (reporting lines). Faint dotted horizontal
  links = **coworkers**: adjacent live siblings, who may message each other
  directly. A brighter curved line bulging out to the right of the tree is an
  **audience** — a direct channel bypassing the hierarchy; terracotta means
  that node holds YOUR ear, blue an agent-granted audience.
- A small **spark** runs along the wires whenever a message actually flows:
  down or up the tree, across a peer link, or along an audience line. The
  direction of travel is the direction of the message.

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

- Header: tier, name (hover for its purpose), context wheel (red ≥ 80% —
  compaction approaches), status chip, ✳ = working. Tabs: chat · history
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
  first message. Messages to a BUSY agent deliver **mid-task, right after
  its next tool call finishes** — never interrupting it (your sent bubble
  shows dimmed until delivered; the agent's system prompt authenticates this
  channel). If the agent makes no further tool calls, delivery falls back to
  the end of its current response. Requires the private agent CLI (see
  README). This applies to ALL mail — agent-to-agent messages deliver
  mid-task the same way; each message carries its sender's authority (user
  mail outranks the chain, agent mail has its normal standing).
  Queued messages survive server restarts. A `preserving` bearer answers through a discarded fork — it
  retains nothing of the exchange.

## Lineage (the ≣ stack behind a card)

Compaction splits a node: the successor continues under the same name; the
pre-compaction self is archived in place as a **knowledge bearer** (rehire at
0 grant — optionally at a cheaper tier — to consult everything the compaction
summary flattened). When its own headroom runs out it becomes a **preserving
oracle**: still answers, retains nothing. Live bearers float tethered above
their successor. A predecessor is NOT an org child — it holds no authority.

## Inboxes (✉ on the eye AND on every card)

The eye's ✉ and each card's ✉ open the SAME interface (user ruling), laid out
like a webmail client: the message list on the left — sender, send time, and a
truncated brief of the body (mails have no subjects) — and the selected
message opened full (markdown-rendered) in the reading pane on the right.
Unread / not-yet-delivered mail sorts on top with the sender highlighted; the
read / delivered archive follows, newest first. A count badge shows while
mail waits (a card's ✉ stays visible whenever it has waiting mail; otherwise
it appears on hover, next to ⚙). The desk's inbox tab is the same view inline.

- **Yours (the eye):** agents write to you asynchronously here; it enters no
  context and interrupts nobody. "Mark all read" archives (nothing is
  deleted). Top-level agents can always write; deeper agents only while
  holding your audience. **Audience requests** climb the chain hop-by-hop —
  grant or deny; **audience holders** may message you directly until
  rescinded (✕). Sender chips render the sender's CURRENT state — a message
  from a since-retired agent looks retired.
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
- **charter**: this agent's standing role card, in its prompt every turn.
  **team charter**: standing instructions cascading into every descendant.
- **🗑 delete permanently** is user-only and irreversible: takes the subtree,
  every lineage stack, records, mail and audiences (session transcripts remain
  on disk). Agents can at most retire.

## Org settings (⚙ in the top bar)

- **workspace**: minted with the org, permanent. **external folders**: added
  ones apply to future hires; removed ones are revoked everywhere immediately.
- **top-level grant cap**: bounds only the hire slider under you.
- **fable weekly-limit policy** — what happens when the shared Fable quota
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
