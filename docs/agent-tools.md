# Agent tool reference

Every agent gets a scoped MCP tool catalog. The ledger, rather than this page,
enforces authority, credit, folder, and provider rules; a tool can therefore be
present but refuse an action outside the caller's scope. Tool names below match
the MCP catalog in `backend/orgtree/mcptool.py`.

## Organization and seats

| Tool | Use |
|---|---|
| `orgtree_chart` | Show the organization visible to the caller, including scope and credit information. Pass `include_archived` to list retired nodes. |
| `orgtree_hire` | Hire a report, or insert a superior above a seat in your subtree (`hire_type='superior'`). An ordinary hire must state folders, tools and visibility explicitly (plus name, tier, charter, grant, and permission mode); a superior insertion must omit folders, tools, visibility and permission mode, because the seat takes the target's own. Either way a `kickoff` starts its first task, and `work_item` assigns an existing docket item to the new seat as part of the same call. |
| `orgtree_retool` | Change an agent's folders, tools, visibility, mode, charter, team charter, effort, or prefer_reserve in your subtree. A caller may only change its own team charter. |
| `orgtree_switch_model` | Change an agent's tier in your subtree. Across providers, the pre-switch self is archived in place as a knowledge bearer (<node>@<gen>) and the agent starts a fresh session; its scratch, mail, and breadcrumbs remain. |
| `orgtree_list_tiers` | List the tiers this machine currently offers, with provider, model, seat price, and advisory availability. Call it before `orgtree_hire` or `orgtree_switch_model` when choosing a tier; the hire gate rechecks fresh evidence, scope, and credits, so a listed tier can still be refused. |
| `orgtree_retire` | Archive a node, preserving it for rehire. Retiring a node with live reports retires that subtree. |
| `orgtree_rehire` | Restore an archived node, optionally renaming, re-scoping, granting audiences, assigning a `work_item`, and giving it a kickoff in one call. A recoverable bearer must stay with its original provider; use `orgtree_switch_model` after rehire to change providers. |
| `orgtree_cheap_compact` | Reset an idle agent's session in place (retaining seat id, parent, scope, charter, grant, and team) while archiving its prior session as a knowledge bearer (<node>@<gen>), avoiding a costly cold compaction. |
| `orgtree_move` | Re-parent a node within the caller's reachable subtree. |
| `orgtree_swap` | Two agents in your reach exchange seats: superior, reports, grant, team charter and scope stay with the seat; identity, session, charter and mailbox travel with the agent. Top-level swaps remain user-only. |
| `orgtree_self_subjugate` | Voluntarily exchange your own seat with a live descendant, including when you are top-level. Your replacement takes the seat's reports, grant and scope; your identity and session stay with you. Ordinary swaps involving top-level seats remain user-only. |
| `orgtree_dissolve` | Retire a node and all descendants. |
| `orgtree_interrupt` | Stop a node's current turn in your subtree without retiring or archiving it; the node goes idle and queued actions (such as a pending model switch or mail) apply immediately at the boundary. |
| `orgtree_reallocate` | Move grant credits between a report and its parent. |
| `orgtree_rename` | Rename a descendant and move its identity, mailbox, and working folder with it. |
| `orgtree_status` | Report `working`, `idle`, `done`, or `blocked`; `done` and `blocked` notify the superior. |
| `orgtree_work` | The docket: create, update (both lists, every time), assign, add participants, claim/verify delivery stages, record evidence, check acceptance conditions, record a reviewer's `review` decision, accept, archive, supersede durable work items. An authorized update CLAIMS the item (assignment is ownership); pass `owner` to write on somebody else's item without taking it, and `reviewer` on the update that asserts `review` — the named reviewer is told and woken at once. See [work-items.md](work-items.md). |
| `orgtree_staff` | One call: create or update a docket item, hire or rehire the seat that will do it, assign the item to that seat and start it. Refused whole — a rejected call leaves no seat, no item, no mail and no wake. |

## Communication and audiences

| Tool | Use |
|---|---|
| `orgtree_message` | Send mail to reachable agents, the user when permitted, or an external recipient. It also sends attachments to the user or `@net:` recipients. |
| `orgtree_send_notice` | Send in-organization FYI mail without waking an idle recipient. |
| `orgtree_ask` | Put a structured question card in the user's inbox; it remains open until answered, dismissed, or withdrawn. `work_item` (per question) attaches it to a docket item. |
| `orgtree_withdraw_ask` | Withdraw the caller's open question/credit/scope request batch when it is no longer needed. |
| `orgtree_request_credits` | Ask the user for a new total credit grant. |
| `orgtree_request_scope` | Ask the user for folder, tool, MCP, or permission-mode access that no superior can grant. |
| `orgtree_audience` | Request, forward, grant, deny, or revoke an audience. `target=extern` grants access to the organization inbox. |
| `orgtree_list_orgs` | List reachable organizations and hub peers. |

## Reading and presenting work

| Tool | Use |
|---|---|
| `orgtree_read_transcript` | Read the caller's or a descendant's recent transcript entries. |
| `orgtree_read_scratch` | List or read a descendant's scratch folder. |
| `orgtree_present` | Render a Markdown document for in-page reading by the user. |
| `orgtree_send_file` | Deliver a file to the user as a download card; images render in the conversation. |

## Operations

| Tool | Use |
|---|---|
| `orgtree_watchdog` | Create, inspect, pause, resume, or remove a file, command, process, or stream monitor. A `once: true` create makes a one-shot dog. |
| `orgtree_self_restart` | Rebuild and restart the current backend from its committed repository state. It refuses while an agent is mid-turn. `force: true` (with a `reason`) deploys anyway — it stops every working agent, waits for their turns to finish, and then deploys; they come back idle and do not resume on their own. |
| `orgtree_prime_restart` | Arm the same restart to run automatically once the machine is quiet. `deadline_minutes` (optional, needs a `reason`) bounds that wait: if the machine has not gone quiet in time the restart escalates to a forced deploy, unattended, and the agents it stops are woken again on the new build. A machine that goes quiet first never escalates. |
| `orgtree_restart_wake` | Arm, cancel, or inspect status of a one-shot waking turn on next backend restart for yourself or a subordinate report, upgrading the passive restart notice into a wake with the deployed version. |

## Common constraints

- Agent hires must state every scope field explicitly; the caller cannot grant a
  capability it does not hold.
- `orgtree_rehire` restores context only when the archived generation is
  recoverable. A LOST generation cannot be revived.
- Outside mail is sent as the organization and requires an organization-inbox
  audience holder. Coordinate before replying.
- `orgtree_send_file` is for a downloadable artifact; `orgtree_present` is for
  a document the user should read in the page.
- Keep a watchdog for a condition that may take longer than a turn instead of
  polling it manually.

### One-shot dogs

Pass `once: true` when creating a **one-shot dog**. It works with every
watchdog kind (`file`, `command`, `process`, and `stream`) and is independent
of `notice`: a one-shot dog may wake its owner or deliver a passive notice.
A real fire makes it fire exactly once and remove itself; the fire mail says
that it removed itself, and it no longer appears in `orgtree_watchdog list`.
Its per-agent watchdog slot is returned, so it no longer counts toward the
eight-dog cap.

Use a one-shot dog whenever a condition is a **deadline** or a question has
only one answer. A deadline condition stays true after it is reached, so a
persistent dog re-fires at every interval forever: `READY=yes` and
`ELAPSED>24h` are deadline conditions. By contrast, `BUILD FAILED` appearing
in a log is an edge and can suit a persistent dog. If a condition is not a
one-shot case, remove a persistent dog in the same turn that acts on its first
fire.

Only a real fire spends a one-shot dog. A went-quiet alert does not, and
neither does a fire that cannot be delivered because its dog is paused or its
owner is archived.
