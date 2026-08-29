# Agent tool reference

Every agent gets a scoped MCP tool catalog. The ledger, rather than this page,
enforces authority, credit, folder, and provider rules; a tool can therefore be
present but refuse an action outside the caller's scope. Tool names below match
the MCP catalog in `backend/orgtree/mcptool.py`.

## Organization and seats

| Tool | Use |
|---|---|
| `orgtree_chart` | Show the organization visible to the caller, including scope and credit information. Pass `include_archived` to list retired nodes. |
| `orgtree_hire` | Hire a report. Supply its name, provider tier, charter, grant, folders, tools, visibility, and permission mode; a `kickoff` starts its first task. |
| `orgtree_retool` | Change a report's folders, tools, visibility, mode, charter, team charter, or effort. A caller may only change its own team charter. |
| `orgtree_switch_model` | Change a report's tier. A provider change requires confirmation and starts a fresh session; its scratch, mail, and breadcrumbs remain. |
| `orgtree_retire` | Archive a node, preserving it for rehire. Retiring a node with live reports retires that subtree. |
| `orgtree_rehire` | Restore an archived node, optionally renaming, re-scoping, granting audiences, and giving it a kickoff in one call. A recoverable bearer must stay with its original provider; use `orgtree_switch_model` after rehire to change providers. |
| `orgtree_cheap_compact` | Replace an idle report with a fresh same-tier report that can read the predecessor's folder, avoiding a costly cold compaction. |
| `orgtree_move` | Re-parent a node within the caller's reachable subtree. |
| `orgtree_dissolve` | Retire a node and all descendants. |
| `orgtree_reallocate` | Move grant credits between a report and its parent. |
| `orgtree_rename` | Rename a descendant and move its identity, mailbox, and working folder with it. |
| `orgtree_status` | Report `working`, `idle`, `done`, or `blocked`; `done` and `blocked` notify the superior. |

## Communication and audiences

| Tool | Use |
|---|---|
| `orgtree_message` | Send mail to reachable agents, the user when permitted, or an external recipient. It also sends attachments to the user or `@net:` recipients. |
| `orgtree_send_notice` | Send in-organization FYI mail without waking an idle recipient. |
| `orgtree_ask` | Put a structured question card in the user's inbox; it remains open until answered, dismissed, or withdrawn. |
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
| `orgtree_watchdog` | Create, inspect, pause, resume, or remove a persistent file, command, process, or stream monitor. |
| `orgtree_self_restart` | Rebuild and restart the current backend from its committed repository state. It refuses while an agent is mid-turn. |
| `orgtree_prime_restart` | Arm the same restart to run automatically once the machine is quiet. |

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
