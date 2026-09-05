# The docket — durable work items

The docket is the organization's record of substantive work. The user reads it
in the Work panel (a list of items with a status and an age, one pane of detail
on the right); agents write it with the `orgtree_work` tool. Nothing about an
item lives on a node, so it survives retirement, compaction, rehire and
reassignment. Specification of record: the 2026-09-05 docket workshop
(`docket-final-spec.md` in the coordinator's scratch); this page documents what
the backend actually does.

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
| `status` | `open` · `in_progress` · `blocked` · `review` · `done` · `superseded` · `dropped` |
| `owner` `{node, generation}` | who is responsible, at the generation assigned; `owner_current` / `owner_state` say whether that seat is still live and unchanged |
| `participants` | node ids with narrow collaborator rights (below) |
| `done_so_far`, `working_on_next` | **the docket status** — the latest two lists |
| `docket_at`, `last_updater` | when the latest docket update was written and by which agent |
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

Agents set `open|in_progress|blocked|review|dropped`. `done` is refused on
`update`: assert `review` and wait for `accept`. `blocked_reason` is kept while
the status is `blocked` and cleared otherwise.

## Attention

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
                OR status == done AND now − docket_at > 3600 s)      # strictly greater
               AND NOT effective_attention

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
| `accept` (→ `done`) | the user, or a strict ancestor of the owner — **never the owner** |
| `create` | any live agent (owner = itself or a subordinate) or the user |

An agent that may not read an item gets one refusal indistinguishable from a
nonexistent name.

**Pointers at an item the viewer may not read are ANONYMOUS.** A dependency
comes back `{visible: false}` with no name at all, `superseded_by` is `null`
(while `superseded_by_visible` still says a pointer exists), and `history[].by`
is redacted the same way. This got stricter when the opaque id was retired: an
id carried no title, and the name is derived from one, so serving it in those
slots would disclose the title of an item the viewer is not allowed to read.

Reassignment is to oneself or a subordinate and never changes `last_updater`.
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
| `POST …/work-items/{slug}/reply {body}` | user mail to the **last updater**, exactly, prefixed `[DOCKET REPLY · slug "title"]` (the canonical name, not the caller's spelling); 422 when no agent has updated yet, 404 when that node is gone, `deferred: true` when it is archived — never a substitute recipient |
| `POST …/work-items/{slug}/dismiss-attention {set_rev}` | above; 409 on a stale rev |
| `POST …/work-items/{slug}/accept {note}` | the user accepts |
| `POST /api/orgs/{slug}/migrate-work-identity` | the one-shot conversion to slug-only identity: exports a JSON backup of the document as it stands, then rewrites it in the same locked save. `{already: true}` and no write when there is nothing to convert; 422 (nothing written) when two items already share a name |
| `POST /api/orgs/{slug}/repair-rename {rename_at, documents[], work_items[], actor?}` | finishes a rename for records an earlier rename left under the old id: `documents[].node` and the current-identity work fields. Work items named by slug. Bounded to ONE logged rename event, an explicit allowlist, and records that still hold the old id; user or the renamed identity only; frozen against kiosk visitors |
| `GET /api/orgs/{slug}` | carries `work_items_summary: {attention, active}` for the toolbar badge |

`counts.attention` counts items (not questions) with effective attention over
the full set; `counts.active` counts non-archived items not in
`done|superseded|dropped`.

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
