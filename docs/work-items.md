# The docket — durable work items

The docket is the organization's record of substantive work. The user reads it
in the Work panel (a list of items with a status and an age, one pane of detail
on the right); agents write it with the `orgtree_work` tool. Nothing about an
item lives on a node, so it survives retirement, compaction, rehire and
reassignment. Specification of record: the 2026-09-05 docket workshop
(`docket-final-spec.md` in the coordinator's scratch); this page documents what
the backend actually does.

**"Dock", the verb** (the user's own term, 2026-09-05): to **dock** something is
to put a new feature on the docket — to create an item for it. It carries no
other meaning here, and in particular nothing to do with docking a panel in the
UI. It implies no extra step beyond the `create`.

## Storage

Two plain keys in the org document: `work_items` (active) and
`work_items_archive`. Under SQLite they are `doc` blobs, under JSON plain keys;
there is no DDL and no migration marker, and a document without them is an
empty docket. Reads never create the keys. The active list is capped at 200
items by **refusing** the 201st `create` (nothing is deleted for you); the
archive is unbounded.

## An item

| field | meaning |
|---|---|
| `slug` | **the only identifier.** Readable, derived from the title, unique across active+archive, fixed at creation — it does NOT follow a later title edit, so a name already written down keeps working. Every stored reference uses it: `dependencies`, `superseded_by`, ask `work_item`, the routes, the frontend's keys |
| `rev` | bumped by every mutation (verify revalidates against it) |
| `title`, `objective`, `kind` (`code` / `non-code`) | what and why |
| `status` | `backlogged` · `open` · `in_progress` · `blocked` · `waiting` · `review` · `done` · `superseded` · `dropped`. `review` means **review by agents**, `waiting` means an external event, `dropped` is the terminal **non-success** outcome (cancelled or failed unrecoverably) and is never `done` — see below |
| `blocked_reason`, `waiting_reason`, `dropped_reason` | the state's own information, required on entry to that state and cleared on the way out, so at most one is ever set |
| `owner` `{node, generation}` | **the assignment, and assignment IS ownership**: who is responsible, holds the item's management rights and receives the user's replies on it, at the generation assigned; `owner_current` / `owner_state` say whether that seat is still live and unchanged |
| `reviewer` `{node, generation}` or null | the agent named to CHECK the work when the item entered `review`. Not ownership. Absent on items that predate the field and never back-filled — absent and null both mean nobody was named |
| `participants` | node ids with narrow collaborator rights (below) |
| `done_so_far`, `working_on_next` | **the docket status** — the latest two lists |
| `docket_at`, `last_updater` | when the latest docket update was written and by which agent. `last_updater` is HISTORY: it is not who a reply reaches (that is `owner`) |
| `manual_attention` `{reason, at, by, set_rev}` | the agent-raised flag, or null |
| `dismissals` | every user dismissal of a flag, kept |
| `questions` | open asks with a tab attached to this item (derived from the ask store) |
| `effective_attention`, `attention_sources` | derived: a manual flag and/or a pending question |
| `archived`, `archived_at` | derived rule below; the physical move instant |
| `acceptance`, `evidence`, `delivery`, `accepted`, `dependencies`, `superseded_by`, `history` | delivery and acceptance metadata (below) |

### Two clocks

`updated_at` moves on any mutation. `docket_at` moves only on a **docket
update**: creation, an agent's `update`, `accept`, `reopen`, `supersede`. The
row age the user sees and the one-hour auto-archive both run on `docket_at`.
A question attachment, a delivery claim, an evidence row, a reassignment or a
user dismissal move `updated_at` and leave `docket_at` and `last_updater` alone.

## The status update — `update`

Every `update` carries **both** lists, `done_so_far` and `working_on_next`, as
lists of individual strings. Blank and whitespace-only entries are dropped; if
both lists end up empty the update is refused. A string instead of a list is
refused (a paragraph is not a list). There is no status-only or flag-only
update: a status change or an attention flag rides an update that restates the
lists. The lists are **replaced**, not merged — they are the latest complete
summary.

### An update CLAIMS the item

User ruling 2026-09-05. Assignment is ownership, and the agent writing the
status is the agent doing the work — so **an authorized update makes its author
the owner**, participants included. Being allowed to update is the claim
mechanism; an actor that may not update is refused before any of it and claims
nothing.

Authority is read from the **pre-update** state, so a participant's claim
cannot hand it, in the same call, rights the first line of `work_update`
already refused it.

The **administrative update** — a superior writing a status on somebody else's
item, a sweep, a correction — passes `owner=<the current owner>` and keeps the
item where it is. Naming the owner that is already there changes nothing at
all: no history row, no notification, no reassignment to undo. An explicit
`owner` always wins over the claim, so the history shows one assignment and the
notification names one recipient.

### Handing an item over TELLS the agent

`assign`, and a `create` or `update` that names somebody else as owner, post
item-linked mail to the new owner and **wake it** — before it has written
anything on the item. Keeping your own item notifies nobody. `orgtree_staff`
does the item, the seat and the assignment in one call; `work_item` on
`orgtree_hire`/`orgtree_rehire` does the same for a seat you are creating
anyway.

Agents set `backlogged|open|in_progress|blocked|waiting|review|dropped`. `done`
is refused on `update`: assert `review` and wait for the reviewer's `approve`
or for `accept`.

## `waiting`, `dropped`, and the information a state owes

User ruling 2026-09-05. `waiting` is **active work whose next step is not the
agent's to take**: a build, a deploy, another team's landing, an external
service. It counts as active, stays on the assigned desk and stays in the main
list. The only thing it changes is that **this item** stops producing idle
docket reminders until its event happens — it never silences any other item the
same agent holds. It is not a second backlog and it is not a closed state.
`blocked` stays reminder-eligible: being stuck is a thing an agent can usefully
be nudged about.

Nothing detects the event. There is no watcher and no automatic transition: the
ordinary wake that tells the agent (a watchdog, a message, a build
notification) is what prompts it to update the state by hand.

Each of the two states carries its own information, in its own field:

| state | field | says |
| --- | --- | --- |
| `blocked` | `blocked_reason` | what prevents progress, what would unblock it, and who can act when that is known |
| `waiting` | `waiting_reason` | the external event, **and** how the agent will learn it happened |
| `dropped` | `dropped_reason` | why the work ended without being completed: **cancelled** or **failed unrecoverably**, who decided, and what would have to change for it to be worth resuming |

The rules are the same for all three, and they are three separate rules:

* **Entering** the state requires the field — on `create` as well as `update`.
  No other status requires anything.
* **Already** in the state, field not supplied: left alone. Items written
  before this requirement stay editable rather than becoming un-updatable.
  ⚠ Reachable for `blocked` and `waiting` only. State information does not
  override the closed-item rule: `dropped` is **closed**, so a plain `update`
  is refused before this rule runs. A legacy drop stays **readable and
  reopenable**, but is **not editable while it stays dropped** — it cannot be
  given a reason after the fact, and a stored one cannot be corrected in
  place. `reopen` is the way back, and it keeps the sentence in history.
* A **blank** string is refused, never stored, **while the item is in that
  state**: blanking used to erase the field silently, and erasing required
  information without a word is the failure the requirement exists to stop.
  A blank sent for the *other* state — a `waiting_reason` on a blocked item —
  is ignored along with the field itself, which is cleared for every state it
  does not belong to anyway. The refusal guards a live reason; it is not a
  validation of the argument in the abstract.

The field is cleared whenever the item leaves the state, so a reason never
survives the state it describes. The user's own dismissal of an attention flag
still moves the item to `blocked` with its own real reason and needs no agent
input.

What the guard can and cannot do: it checks that the text is **present**. No
code can tell whether the prose names a real event or a real blocker — that is
what the wording in the tool help and the doctrine is for.

## `review` is the AGENT check — the user's review is attention

User ruling 2026-09-05. `review` says **another agent or a coordinator is
checking the work**. It is never how the user is asked for anything, and the UI
labels it **Agent review** everywhere (row, detail pane, group heading).

Asking the user to look at something is the **attention** mechanism: the manual
flag, or an attached question. Nothing in the backend requires `review` before
`accept` — an item that was only ever waiting on the user is accepted from
whatever open status it is in.

The two ways to reach the user are not interchangeable: a **question** is an
attached `orgtree_ask`, and the **manual flag** is for a concrete thing they
must see or confirm that is not a question.

The user's own review is wanted only when one of these actually holds:

* a decision was taken **beyond** the stated spec,
* a **specialized edge case** was chosen,
* a **definition gap** was filled on their behalf, or
* something is blocked on them that no attached question is already asking.

Being visible in the UI is not a reason, and neither is who owns the item. Work
that matches the stated requirements exactly — no deviations, no extra edge
cases, no definitions supplied for them — is covered by the user's standing
authorization once agents have verified it, and completes without a further
acceptance round. An exact match may not be *claimed* without comparing the
stated requirements against the delivered behaviour.

Ambiguity is a **question asked before** the implementation that depends on it,
not a choice made for the user and approved afterwards. Ordinary implementation
mechanics that do not decide product behaviour are not asked about at all.

## The reviewer — a named check that is not ownership

User ruling 2026-09-05 21:23/21:26, and the notification requirement 22:05.

The update that puts an item at `review` **must name a `reviewer`**, and that
agent is told immediately: it receives item-linked mail (`[DOCKET REVIEW
REQUEST · slug "title"]`) and is woken once, on that transition. An ordinary
update on an item already at `review` is not a new review request — it mails
nobody and wakes nobody. Re-entering review after `changes` is a new request
and does send one.

* **Not ownership.** The owner keeps the work. A reviewer gets read, `evidence`
  and exactly one decision; a status update from a reviewer-only actor is
  refused by name, because an update would claim the item.
* **`review` decision `approve`** completes the item — there is no second
  acceptance round behind it — and **`changes`** returns it to the owner as
  `in_progress`, waking them with the reviewer's note.
* **Self-review is prohibited**, checked when the reviewer is named and again
  when it decides, because ownership can move in between.
* **Who may be named:** yourself, an agent in your subtree, or your own
  superior — asking the agent above you to check your work is the ordinary
  review here.
* **Legacy items**: items already at `review` when the field shipped carry no
  reviewer. Nothing is invented for them; the owner (or the user) names one
  with an ordinary `update` carrying `reviewer`, with no status change.

## Attention

`attention_reason` carries the specifics: what was asked against what was built,
the exact decision, edge case or definition added beyond the spec, and the
confirmation wanted. `Ready for review` and `please approve` are not enough —
this field is what the user reads to know what they are approving, so the detail
lives here rather than in `evidence` or the done list. It is capped at
`WORK_ATTENTION_REASON_MAX` (500 characters) and the detail pane renders it
with newlines preserved.

**Manual flag.** `update … attention: true, attention_reason: "…"` sets
`manual_attention` and mints the next `set_rev`. A later update that does not
pass `attention: true` **clears** the flag (history records `cleared_set_rev`):
the latest update is the complete current statement, like the lists.

**User dismissal** (`POST /api/orgs/{slug}/work-items/{id}/dismiss-attention
{set_rev}`) is compare-and-swap on `set_rev` — a stale click is a 409, never a
silent clear of a newer reason. On success the flag is cleared, the status
becomes `blocked` immediately, a `dismissals` row is appended, and the lists,
`last_updater` and `docket_at` are untouched. Pending questions are **not**
touched, so the item stays orange while any remain. The flag's author (else the
owner) receives a passive notice.

**Exact-repeat protection.** Raising a reason equal, ignoring case and
whitespace, to the most recently dismissed reason is refused. A changed string
is not proof of material new information; that rule lives in the standing
instructions, the backend enforces only the exact repeat.

**Questions.** `orgtree_ask` takes `work_item` (single form) or a per-question
`work_item` (batch form). Attaching needs read right on the item and is checked
before anything records; a refused attach leaves no ask behind. The linkage is
a field on the ask store's own question tab (and `work_items` on the entry), so
an answered, dismissed, withdrawn or mooted request stops counting the moment
the ask store says so — nothing caches attention. Several agents may attach to
one item; each keeps its own asker, batch identity and answer route
(`/asks/{aid}/answer`, `/nodes/{nid}/batch`); resolving one never resolves
another. An item with several questions counts **once**. A deep agent without a
user audience gets the existing behaviour — its question is mailed to its
superior, naming the item in the text — and nothing attaches.

## Archive

`archived` is **derived on every read**:

    archived = (physically in work_items_archive
                OR status in (done, dropped) AND now − docket_at > 3600 s)
               AND NOT effective_attention                           # strictly greater

`dropped` archives itself on the same clock as `done` (user 2026-09-05): work
that was cancelled or failed unrecoverably is as finished as work that
succeeded, and when only the successful kind archived itself every dead item
stayed on the main list for good. `superseded` is deliberately not in that
set — its `superseded_by` pointer is the thing you follow, and it is unchanged.

So a done item is not archived at exactly one hour, is archived one second
later, and an item holding attention (a pending attached question or a manual
flag) is never archived — it shows in the active list so the badge always opens
onto a visible row. The physical move happens in a sweep at the head of every
docket mutation, only while no attention holds, and stamps `archived_at`; a read
never writes. Archived ids still resolve. An `update` on an archived item is
refused unless it passes `reopen: true`, which returns it to the active list
with the new (open) status. `archive` moves a closed item early; `accept` sets
`done` and starts the clock.

## Authority

Explicit, never org-wide; nothing in an item is public.

| right | who |
|---|---|
| read, `update`, `evidence`, attach a question | the owner node, the creator node, a strict ancestor of the owner (of the creator while unowned), the user, **participants** |
| `assign`, `participants`, `archive`, `supersede`, `claim`, `verify`, `check` | the same set minus participants |
| `accept` (→ `done`) | the user, a strict ancestor of the owner, or the item's **named reviewer** — never the owner |
| `review` (`approve` / `changes`) | the item's named reviewer only |
| `create` | any live agent (owner = itself or a subordinate) or the user |

An agent that may not read an item gets one refusal indistinguishable from a
nonexistent name.

**Pointers at an item the viewer may not read are ANONYMOUS.** A dependency
comes back `{visible: false}` with no name at all, `superseded_by` is `null`
(while `superseded_by_visible` still says a pointer exists), and `history[].by`
is redacted the same way. This got stricter when the opaque id was retired: an
id carried no title, and the name is derived from one, so serving it in those
slots would disclose the title of an item the viewer is not allowed to read.

Reassignment is to oneself or a subordinate, notifies and wakes the new
owner, and never changes `last_updater` or `docket_at` — those are history.
A rename carries the current-identity fields — `owner`, `last_updater`,
`participants` — onto the new id; `created_by`, `history[].by`, `evidence[].by`,
the delivery claims and `accepted.by` are authored history and keep the name
that was current when they were written.

## Delivery and acceptance metadata

`claim` records a stage (`implemented`, `committed`, `pushed`, `deployed`,
`in_build`) with `method: self-report`; the git-checkable stages need a
lowercase hex sha and start `unverified`. `verify` evaluates one of them
against **this** repository (`backend/orgtree/workitems.py`): the object
exists / is an ancestor of the local `refs/remotes/origin/main` tracking ref /
is an ancestor of the commit this backend booted from. Three-valued: `True` or
`False` only when git answered; a sha that does not resolve here, a dirty boot,
a missing or timed-out git is `None` with a `detail`. Inclusion in the running
build is **never** a functional check, and no fetch time is ever derived
(`fetched_at` is null). Git runs outside `DOC_LOCK`: capture under the lock,
evaluate, write back only if the item's `rev` is unchanged (`{stale: true}`
otherwise). Results are cached 60 s keyed by repo, stage, sha and the target's
identity.

`acceptance` conditions are checked one at a time with `check` (index +
`evidence_ref`) — acceptance evidence, distinct from delivery. `evidence` rows
cap at 50 by refusal; `history` folds rows past 100 into one `{kind: "folded",
count, first_at, last_at}` row at the head — a visible, lossy omission, never a
deletion of the item.

## Routes

| route | purpose |
|---|---|
| `GET /api/orgs/{slug}/work-items[?archived=1]` | `{items, archived?, counts: {attention, active, archived}, now}` — every item, newest `docket_at` first, full set |
| `GET /api/orgs/{slug}/work-items/{slug}` | `{item}` (archived items resolve) |
| `POST …/work-items/{slug}/reply {body}` | user mail to the **owner** — the assignment — exactly, prefixed `[DOCKET REPLY · slug "title"]` (the canonical name, not the caller's spelling); 422 when the item has no owner, 404 when that node is gone, `deferred: true` when it is archived. There is **no fallback to the last updater**: never a substitute recipient |
| `POST …/work-items/{slug}/dismiss-attention {set_rev}` | above; 409 on a stale rev |
| `POST …/work-items/{slug}/accept {note}` | the user accepts |
| `POST /api/orgs/{slug}/migrate-work-identity` | the one-shot conversion to slug-only identity: exports a JSON backup of the document as it stands, then rewrites it in the same locked save. `{already: true}` and no write when there is nothing to convert; 422 (nothing written) when two items already share a name |
| `POST /api/orgs/{slug}/repair-rename {rename_at, documents[], work_items[], actor?}` | finishes a rename for records an earlier rename left under the old id: `documents[].node` and the current-identity work fields. Work items named by slug. Bounded to ONE logged rename event, an explicit allowlist, and records that still hold the old id; user or the renamed identity only; frozen against kiosk visitors |
| `GET /api/orgs/{slug}` | carries `work_items_summary: {attention, active}` for the toolbar badge |

`counts.attention` counts items (not questions) with effective attention over
the full set; `counts.active` counts non-archived items not in
`done|superseded|dropped`.

## Who an idle docket reminder goes to

The reminder (its own default-off runtime switch) wakes an idle agent with the
items **whose next action is that agent's**, and the order of the two steps is
the point:

1. Each item decides for itself whether it can be reminded about at all —
   closed and `backlogged` are out, `waiting` is out until its event, and
   effective attention (a manual flag or an open attached question) is out
   because only the user can move it. An excluded item removes **itself** and
   nothing else; it can never silence a different actionable item held by the
   same agent.
2. Only then is each surviving item assigned to one recipient
   (`Org._work_next_recipient`): an item in `review` belongs to its
   **reviewer**, everything else to its **owner**.

So the clock that gates a review item is the reviewer's own idle clock, not the
owner's, and one agent holding both its own work and somebody else's review
gets a single notification listing both, each row saying which it is.

A `review` item with **no reviewer recorded** goes to the owner, worded as a
missing review assignment — the outstanding action there is naming a reviewer,
and self-review is prohibited. A reviewer that is **no longer live** counts as
no reviewer, under its own role, because the reminder pass never wakes a
retired seat and the item would otherwise stop reaching anybody at all; the
recorded reviewer is left alone and the owner is asked to name another.
Ownership and reviewership both ignore generation: a compaction or rehire
replaces the agent, not the assignment — being retired is a different thing.

## The standing instructions

The operating doctrine (when to consult, create, update, attach, flag, reply,
close) is `supervisor.DOCKET_DOCTRINE`, rendered inside `identity_prompt` —
the one string every lane is built from (the claude identity file, the codex
`AGENTS.md`, the antigravity developer instructions). It therefore reaches every
provider through the managed prompt, not through a repository document some
lanes never read. It is stable doctrine: a changed prompt prefix once, on
deployment, then fixed bytes.

## Tests

`backend/tests/test_work_items.py` drives the installed routes (`/api/agent`
for the tool, the `work-items` routes for the user) through the test client:
storage on an old document, authority incl. two permitted askers and a refused
one, both-empty refusal, the exact 3600 s edge, attention holding an item
active, dismiss CAS and Blocked, reply routing, three-valued verify with a
fake git runner, rev revalidation, the caps, and the doctrine's presence in
every agent's identity prompt.
