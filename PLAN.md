# orgtree — hierarchical agent org chart with a credit budget

**Status: v1.0.0 SHIPPED 2026-07-29** (`3aeafc7`). Every §13 phase landed: spikes, ledger,
supervisor, routing + envelope + audiences + user inbox, compaction lineage, spend tracking,
failure states, caps, the office-canvas UI with the five-channel encoding. Live-verified
agent-to-agent delegation cascade (agent hires agent via MCP under the no-defaults rule).
**v1.1 SHIPPED same day** (`2398ee0` + `6f1975a`): the audit gaps closed — preserving oracle
(state 3, fork-and-discard, knowledge→preserving at 92%), lineage truly off the org axis with
§8.7 stacked cards + live-bearer consult cards + cheaper-tier rehire (№16), §7.6 read-down
(transcript/scratch MCP tools + descendant scratch add-dirs), §7.3 audience requests climbing
hop-by-hop + user grant/deny/one-click rescind (№44), per-message streaming desks, inspector
(history w/ attribution, notice log, scratch browser, encoded inbox chips), №31 eager startup
reconciliation, №17 attach/release, §15 org.md editor + node charter + team charters, and the
**fable weekly-limit ruling** (leaves self-retire with the reason, managers lock, user-only
rehire = the decree; detection on limit-shaped fable turn failures). 59/59 checks.
Remaining (small): dataviz-validated palette pass over the hand-picked tier hues · Tier-1 bash
gating · №33 formal cache-economics measurement · token-level (vs per-message) streaming.
**Author:** drafted 2026-07-28 with neoja; reviewed and amended 2026-07-29 by the implementor
session with the design session (two-round chatq review) and the user's rulings.
**Supersedes:** nothing. Successor in spirit to `chatq` (`~/.claude/claude-intranet`), which
solves flat peer messaging; orgtree solves hierarchy, budget, and observability.

---

## 1. What this is

A **persistent tree of addressable Claude Code sessions** that the user can inspect and steer,
where agents spawn agents freely under a capacity budget denominated in **credits**.

Two things exist today and neither is this:

- **`chatq`** — flat peer messaging between sessions. No hierarchy, no budget, no UI.
- **Agent / Workflow tools** — real hierarchical delegation, but the children are *ephemeral
  subagents*. You cannot open one, watch it, or talk to it.

orgtree is the union: delegation hierarchy **plus** persistence, addressability, and observability.

### Goals

1. An agent can hire subagents without asking the user, within a delegated budget.
2. Escalation goes to the **parent node**, not the human, for anything the parent is authorized to
   decide.
3. Every node is a real Claude Code session the user can open, read, and message at any time.
4. The whole tree is visible in one place, with live transcripts.
5. Capacity is bounded and explicitly allocated, so the tree cannot grow without limit.

### Non-goals

- **Security between nodes.** Every session runs as the same OS user with FullControl over every
  other session's transcript, and same-user process handles are obtainable. There is no asymmetry
  to build authentication on (verified 2026-07-27 via `Get-Acl` + `Get-Process`). Budget and
  addressing rules are *structural*, not *enforced against a hostile node*. See §11.
- Capping real API dollar spend. Credits gate concurrent capacity, not tokens (§3.4).
- Multi-machine / remote nodes. Single desktop, single user.

---

## 2. Terminology

| Term | Meaning |
|---|---|
| **organization** | The whole tree. Created by the user, who is its root. |
| **node** / **agent** | One persistent Claude Code session with a place in the tree. |
| **root** | **The user.** Not a node — there is no CEO agent (§7.4). |
| **top-level agent** | A direct report of the user. There may be any number; they are peers and may talk to each other freely. |
| **name** | An agent's mandatory canonical human-readable identifier, 1–2 words (§4.7). |
| **user inbox** | The asynchronous channel agents write *to* the user, distinct from the user speaking *to* an agent (§7.5). |
| **scratch space** | A node's own working directory. Readable by itself and every ancestor; not by peers (§7.6). |
| **seat cost** | Credits a node occupies while live, set by its model tier (§3). |
| **grant** | Credits a parent hands a node, which that node may spend hiring its own reports. |
| **free** | A node's grant minus everything it has committed to live children. |
| **hire / retire / rehire / dissolve / reallocate** | The five budget operations (§4.2). |

---

## 3. Credits

### 3.1 The scale

Credits are derived from published Anthropic first-party API pricing, verified 2026-07-28 via the
`claude-api` skill.

| Model | Input $/1M | Output $/1M | Normalized | **Credits** |
|---|---|---|---|---|
| Claude Fable 5 (`claude-fable-5`) | $10.00 | $50.00 | 10.0 | **10** |
| Claude Mythos 5 (`claude-mythos-5`) | $10.00 | $50.00 | 10.0 | **10** |
| Claude Opus 5 (`claude-opus-5`) | $5.00 | $25.00 | 5.0 | **5** |
| Claude Opus 4.8 (`claude-opus-4-8`) | $5.00 | $25.00 | 5.0 | **5** |
| Claude Opus 4.7 (`claude-opus-4-7`) | $5.00 | $25.00 | 5.0 | **5** |
| Claude Opus 4.6 (`claude-opus-4-6`) | $5.00 | $25.00 | 5.0 | **5** |
| Claude Sonnet 5 (`claude-sonnet-5`) | $3.00 | $15.00 | 3.0 | **3** |
| Claude Sonnet 4.6 (`claude-sonnet-4-6`) | $3.00 | $15.00 | 3.0 | **3** |
| Claude Haiku 4.5 (`claude-haiku-4-5`) | $1.00 | $5.00 | 1.0 | **1** |

**The output:input ratio is exactly 5:1 for every current model.** Normalizing by input price and
by output price therefore produce identical weights — the scale is not a judgment call. Haiku = 1
is the natural unit.

### 3.2 Two footnotes on the numbers

- **Sonnet 5 introductory pricing.** $2.00 / $10.00 per MTok through **2026-08-31**, reverting to
  $3.00 / $15.00. The table uses the standard rate deliberately; pinning Sonnet at 2 credits would
  bake in a promotion that lapses.
- **Opus 5 fast mode** (`speed: "fast"`, beta `fast-mode-2026-02-01`) is priced at $10 / $50 —
  Fable-tier. If fast mode becomes a per-node option, it costs **10 credits**, not 5.

### 3.3 Maintenance

Model prices change and models are added. The tier table lives in one place (`tiers` in the
ledger, §5) and is the only thing to edit. Re-verify against the `claude-api` skill whenever the
lineup changes; do not answer from memory.

### 3.4 What credits actually bound

Credits are **occupancy, not expenditure** — the RAM analogy is exact. A live node *holds* its
seat cost; retiring it releases the full amount. Tokens and tool calls are free and unlimited.

⚠️ **A credit is not a dollar.** A 1-credit Haiku node can burn hundreds of dollars of real API
tokens. Credits bound *how many agents of what capability may exist at once*, weighted by tier.
That is a genuinely useful thing to bound — it caps coordination load and blast radius — but it is
not a spend cap, and the units should not be confused for money. This is why they are called
credits and not dollars.

`--max-budget-usd` (a real Claude Code CLI flag) is the *actual* spend cap and is deliberately
**not used** by this design: the stated requirement is that a node may make as many tool calls and
emit as many tokens as it wants.

---

## 4. The budget model

### 4.1 The invariant

For every node N:

```
free(N) = grant(N) − Σ over live children C of ( seat_cost(C) + grant(C) )
free(N) ≥ 0
```

A node's own seat cost is paid by its **parent**, out of the parent's grant. The root's seat is
paid by nobody — the user seats it directly.

Worked example (validated, §10):

```
ceo   [opus seat 5]  grant 50  free 0   holds 55
  fable-a  [fable seat 10]  grant 15  free 0  holds 25
     ×3 opus  [seat 5 each]
  fable-b  [fable seat 10]  grant 15  free 0  holds 25
     ×3 opus  [seat 5 each]

live seats held = 2×10 + 6×5 = 50 = the CEO's full grant. Fully occupied.
```

### 4.2 Operations

| Op | Precondition | Effect |
|---|---|---|
| `hire(parent, model, grant, name)` | **`free(actor) ≥ seat_cost(model) + grant`**, and actor is `parent` or an ancestor of it | New live child. If `actor ≠ parent`, credits cascade down the path — §4.6. `name` is **mandatory** (§4.7). |
| `retire(node)` | node is live **and has no live children** | Node → archived. `seat + grant` returns to parent's free. Session transcript preserved. |
| `rehire(node, grant?)` | node archived; `free(parent) ≥ seat + grant` | Node → live, **resumed with full prior context**. Defaults to its previous grant. |
| `dissolve(node)` | node is live | Recursive retire of node + all descendants, deepest first. Frees everything to node's parent. |
| `reallocate(node, ±Δ)` | `+Δ`: `free(parent) ≥ Δ` · `−Δ`: `free(node) ≥ Δ` | Moves grant between a parent and a child. Can only claw back genuinely unused credits. |
| `promote(node, new_parent)` | actor is an ancestor of both; `new_parent` is strictly above `node`'s current parent | Re-parents `node` upward. Budget-neutral (§4.5). |
| `demote(node, new_parent)` | actor is an ancestor of both; `new_parent ∉ {node} ∪ subtree(node)` | Re-parents `node` downward, under one of the actor's other descendants. Budget-neutral (§4.5). |

**Every operation is exercisable by any ancestor, not just the immediate parent** — see §7.1. The
preconditions above are unchanged by depth; only *who may invoke them* widens.

**Design decisions taken (ratify or overrule):**

1. **`retire` is leaf-only; `dissolve` is the recursive form.** Retiring a manager with live
   reports would orphan them — nodes whose parent is archived, still holding credits, unreachable.
   ~~Refusing and pointing at `dissolve` keeps the two acts distinct.~~ *Superseded 2026-07-31 by
   the design motto (permit + auto-bridge): retiring a manager now dissolves its subtree with a
   warning instead of refusing. Self-retire with live reports still refuses (no dissolve authority
   over oneself).*
2. **The root's own seat is not charged against its grant.** "Give my CEO 50" reads as 50 *to
   allocate*. A one-line change if the other reading is wanted.
3. **`rehire` defaults to the node's previous grant.** Explicit grant overrides.

### 4.3 Retire/rehire is paging

`rehire` preserves `session_id`, so the node resumes via `claude --resume <uuid>` **with its full
conversation intact**. Retire/rehire is therefore literally swapping an agent's mind to disk,
freeing its seat, and paging it back later unchanged. This is the most valuable property in the
design and should be protected in any refactor.

### 4.4 ⚠️ Emergent property: stranding

Reclaiming credits from a manager **silently revokes its ability to rehire its own archived
staff**. The archived sessions still exist on disk with full context, but their manager can no
longer afford to wake them.

```
ceo claws back fable-a's unused 10   →   free(fable-a) = 0
fable-a tries to rehire archived op2  →   refused: needs 5, has 0
```

This follows necessarily from occupancy semantics — it is not a bug.

**RESOLVED (2026-07-28): warn at reclaim.** The operation proceeds; the actor is told, before it
completes, which archived reports it is about to strand and what rehiring them would cost. Not
blocked — a manager stranding its own dormant staff is a legitimate call, and the credits may be
needed elsewhere. Not silent — it is invisible otherwise, and the failure surfaces much later as an
unexplained "cannot afford to rehire".

The warning must name the specific nodes, not just a count, since the actor's decision depends on
*which* reports it is giving up the ability to wake. The same rule fires on every hop of a
promote/demote release path (§4.5).

---

### 4.5 Promote / demote and the credit path — a derived result

Moving a node changes **who pays for it**, so re-parenting is a budget operation, not just a
pointer edit. Let `c = seat_cost(X) + grant(X)`, `P_old` and `P_new` be the old and new parents,
and `L = LCA(P_old, P_new)`.

```
release:  P_old → … → L     each hop returns c to its parent's free   (a reclaim at every step)
acquire:  L → … → P_new     each hop grants c down to its child       (a grant at every step)
```

**Result: a promote or demote along a single ancestral line is always budget-neutral and can never
fail for lack of credits.** When `P_new` is an ancestor of `P_old` (promotion), `L = P_new` — the
release delivers exactly `c` to `P_new`, which then spends exactly `c` on X. Net zero. Demotion
straight down is the mirror image, with `L = P_old`.

This matters: it means **a fully-occupied tree can still reorganize itself.** Without this
property, promotion would be impossible precisely when you most need it.

Lateral moves (different branches) route through the LCA and are net-neutral overall, but every
node on the **release path** has its grant reduced by `c`.

~~⚠️ Therefore promote/demote can strand archived reports — at every hop of the release path~~
**ERRATUM (found during v0.1 implementation, 2026-07-29): moves CANNOT strand.** The release and
acquire adjustments cancel hop by hop — every node on either path ends with its **free unchanged**
(grant and committed shift together), and rehire affordability depends only on free. Verified by
test: a lateral demote + promote back leaves every free identical. The ops that genuinely reduce a
free — and therefore carry the §4.4 warning — are `hire` (the payer), forcible hire (the actor),
`rehire` (the parent, for its *other* archived children), and `reallocate(−Δ)`. The implemented
rule: **warn exactly when an op's free reduction crosses an archived dependent's rehire cost.**
The only move-specific warning is for moving an *archived* node (its rehire cost changes payer).

**Guards:**

- `new_parent` must not be `node` itself or anywhere in `subtree(node)` — otherwise the "parent"
  chain becomes a cycle and every ancestor query diverges.
- The root cannot be moved (it has no parent). `promote(root, …)` / `demote(root, …)` are errors.
- The actor must be an ancestor of `node`, `P_old`, **and** `P_new`. Being an ancestor of both
  parents is sufficient in practice, since `node` sits under `P_old`.
- Archived nodes can be moved. Their `c` is 0 while archived (they hold nothing), so the credit
  path is a no-op — but their *rehire* cost moves with them, which changes which node can afford to
  wake them later. Worth a warning.

---

### 4.6 Forcible hire at depth

An agent may hire a subagent **anywhere in its own subtree**, even where the intermediate managers
have no free credits. The requirement is on the **actor's** pool, not the target parent's:

```
precondition:  free(actor) ≥ seat_cost(model) + grant     and  actor ⊒ target_parent
effect:        the needed credits are granted downward along actor → … → target_parent,
               one hop at a time, then the hire completes
```

This is the **acquire path** from §4.5 with no release path — the same machinery, reused. Three
operations now share it: `hire` (acquire only), `promote` (release then acquire), `demote`
(release then acquire).

The point is authority without collateral damage: a superior that wants a specialist placed deep in
its org should not have to dissolve a branch, or negotiate hop by hop, to make room.

**For the user this always succeeds** — an infinite pool means the precondition is trivially
satisfied and the cascade grants whatever is needed at every level.

⚠️ **Grant inflation.** The cascade permanently raises the grant of every intermediate node on the
path. When the new node later retires, its credits return to its *immediate* parent's free — they
do **not** flow back up to the actor. The org quietly accumulates budget in the middle. This is
correct (those managers really were given that capacity) but the actor should reclaim with
`reallocate` when done, and the UI should make an intermediate node's inflated grant visible.

### 4.7 Names are mandatory

Every agent has a **canonical, human-readable name**, supplied at hire time — including by the
user. No auto-generated identifiers, no unnamed nodes.

- **One or two words.** Long enough to mean something, short enough to fit in a tree node and a
  message chip.
- **Describes the role, the immediate task, or whatever distinguishes it** — `citations`,
  `api-migration`, `perf-triage`, `sonnet-scout`. Not `agent-7`.
- The slugified name **is** the node id (the spike already derives it this way), so it is what
  appears in the tree, in routing, in chain notices, and in every message attribution.
- Collisions get a numeric suffix (`citations-2`), but the hiring agent should be prompted to pick
  a better name rather than accept one.

The reason is not tidiness: every routing decision, notice, and inbox entry names nodes, and an org
of `agent-3` and `agent-11` is unreadable at exactly the depth where the tree becomes worth having.

---

## 5. Data model

Single JSON ledger, `orgtree.json`:

```jsonc
{
  "version": 1,
  "tiers": { "fable": 10, "opus": 5, "sonnet": 3, "haiku": 1 },
  "models": {                      // tier → concrete model id passed to --model
    "fable": "claude-fable-5",
    "opus":  "claude-opus-5",
    "sonnet":"claude-sonnet-5",
    "haiku": "claude-haiku-4-5"
  },
  "root": "ceo",
  "nodes": {
    "ceo": {
      "session_id": "…uuid…",      // stable address; survives retire/rehire
      "model": "opus",
      "parent": null,
      "grant": 50,
      "state": "live",             // live | archived
      "title": "ceo",
      "created": "2026-07-28T…Z",
      "archived_at": null,
      "pid": 12345,                // live process, if attached
      "scope": { … },              // permission mode, escalation target — §7

      // lineage axis — §8. Distinct from parent/child; NOT an org edge.
      "lineage": "ceo",            // stable id shared by every generation
      "generation": 3,             // 0 = original; increments per compaction
      "predecessor": "ceo@2",      // null on generation 0
      "successor": null,           // null on the active generation
      "bearer_state": null         // null (working) | "knowledge" | "preserving"
    }
  },
  "audiences": [                   // §7.3 — sanctioned upward channels
    {
      "grantee": "researcher-3",
      "grantor": "ceo",            // may be the reserved id "user" — §7.4
      "granted_at": "2026-07-28T…Z",
      "reason": "ceo messaged directly"   // or: "granted on request via mgr→dir→vp"
    }
  ],
  "chain_notices": [               // §7.4 — what superiors are told, and whether delivered
    {
      "node": "researcher-3",      // who was reached
      "actor": "user",             // "user", or a node id for agent deep reach
      "level": "notify",           // notify | log
      "kind": "decision",          // question | request | decision
      "gist": "user redirected the citation format to APA",
      "at": "2026-07-28T…Z",
      "delivered_to": ["vp-eng", "dir-research", "mgr-lit"]   // injected at their next turn
    }
  ],
  "audience_requests": [           // in-flight, climbing the chain one hop at a time
    {
      "from": "researcher-3",
      "target": "ceo",
      "currently_at": "vp-eng",    // whose decision it awaits
      "reason": "…",
      "opened_at": "2026-07-28T…Z"
    }
  ]
}
```

`audiences` is a set of directed `(grantee → grantor)` edges checked on every upward message that
is not to the direct parent. It is derived state in the sense that it must be **swept on every
re-parenting** (§7.3) — an audience whose grantor is no longer an ancestor of its grantee is
auto-revoked.

**Node names must be stable and independent of session UUIDs.** Session IDs are minted per node
and preserved across retire/rehire, but a node's *identity in the tree* is its name. Anything
keyed on the UUID breaks the moment a node is re-created rather than resumed.

Derived, never stored: `seat_cost`, `committed`, `free`, `descendants`.

---

## 6. Architecture — what we build vs. what Claude Code provides

### 6.1 Nothing about the AI is ours

orgtree is a **supervisor over unmodified `claude` processes**. Every node is a normal Claude Code
session. There is no model integration to write, no tool implementations, no agent loop, no context
management.

| Concern | Who provides it |
|---|---|
| Agent loop, model calls, streaming | ✅ Claude Code |
| All tools (Bash/Read/Edit/Glob/Grep/Web/…) | ✅ Claude Code |
| Permission system and prompting | ✅ Claude Code |
| Context management + compaction *algorithm* | ✅ Claude Code |
| Session persistence, resume, fork | ✅ Claude Code |
| Transcripts on disk, written live | ✅ Claude Code |
| Subagents, MCP client, hooks, skills | ✅ Claude Code |
| **Credit ledger + the five ops + promote/demote** | ⬜ us |
| **Process supervisor** (spawn / resume / fork / reap) | ⬜ us |
| **Routing: addressing rules, audiences, chain notices** | ⬜ us |
| **Compaction *policy*** (when to split, lineage bookkeeping) | ⬜ us |
| **UI** (tree, stacks, transcript tailing, send box) | ⬜ us |

Roughly **0 of the ~8 days is AI work.** It is a bookkeeping service, a process manager, and a web
UI wrapped around a CLI that already does the hard part.

### 6.2 The flags that matter

Verified 2026-07-28 against `claude --help`; binary at `$CLAUDE_CODE_EXECPATH`.

| Flag | Role in orgtree |
|---|---|
| `--session-id <uuid>` | Parent mints the child's address **before** launching it. |
| `--model <model>` | Sets the node's tier. |
| **`--append-system-prompt`** | ⚠️ **Use this, not `--system-prompt`.** Appends the node's org position (parent, siblings, children, scope, escalation target) to the default prompt. `--system-prompt` *replaces* it and would throw away everything that makes the session a working agent. |
| `--permission-mode` | `default \| delegate \| dontAsk \| bypassPermissions` — the real lever for "stop escalating every risky action to me". Set per node by depth/scope. |
| **`--allowed-tools` / `--disallowed-tools`** | Makes the knowledge-bearer state (§8.3) **structurally enforced, not advisory** — a rehired predecessor is launched read-only, so it *cannot* hire, edit, or act even if it decides to try. |
| **`--mcp-config` / `--strict-mcp-config`** | Injects the orgtree MCP server (§6.3) into every node, and pins the node to exactly that server set. |
| `--settings <file-or-json>` | Per-node hook wiring without touching global settings. |
| `--resume <uuid>` / `--fork-session` | Rehire; reattach after restart; the compaction split (§6.5) and the preserving oracle (§8.4). |
| `-p --input-format stream-json --output-format stream-json` | Long-lived process you write user turns into and read events out of. `--replay-user-messages` confirms this is a continuous multi-turn channel, not one-shot. |
| `--agent` / `--agents <json>` | Per-node agent definition, if a node needs a specialized persona beyond the appended prompt. |

### 6.3 orgtree should be an MCP server

The ledger and router want to be an **MCP server** that every node loads via `--mcp-config`, not a
set of shell scripts the agent is told to run (chatq's approach).

Nodes then get `hire`, `retire`, `promote`, `message`, `request_audience` as **real tools with
schemas and validation**, discovered automatically. That is a large ergonomic difference: an agent
reliably uses a typed tool it can see, and unreliably follows prose instructions to shell out.

The same process owns the ledger, so preconditions (§4.2) are enforced at the call site rather than
trusted to the caller, and the UI talks to it over the same interface.

### 6.4 The ownership fork — a real constraint

A session can have exactly **one driver at a time**. That produces two node kinds:

| Kind | Driver | Addressable by orchestrator? | User interacts via |
|---|---|---|---|
| **Managed** | orchestrator holds the stream-json process | ✅ writes a user turn to stdin | the UI |
| **Attached** | the user has it open in a terminal/IDE | ❌ | that terminal |

Writing to a managed node's stdin **is** a real user turn — this is the cleanest injection possible
and is how the "individually addressable" requirement is actually met. But it means **the UI is not
optional**: for managed nodes it is the only way the user talks to them.

Attaching (`claude --resume <uuid>`) is the escape hatch for deep hands-on work, and requires a
**handoff**: the orchestrator must release the node first, or two drivers write the same transcript.
Logged as decision №17.

### 6.5 Compaction has no CLI control — the split must be orchestrator-driven

`claude --help` has **zero** compaction flags. The 80% threshold (§8.2) cannot be configured, so
the orchestrator drives the split itself using primitives that do exist:

✅ **Step 1 as first drafted contradicted decision №3** (resume-on-demand has no long-lived process
to watch). Resolved 2026-07-29 (№24): after each completed turn, read the **latest assistant
message's** usage from the transcript `.jsonl` — `input_tokens + cache_read_input_tokens +
cache_creation_input_tokens` *is* the context occupancy. ⚠️ Never sum usage across turns: each
message's usage covers the entire prompt at that point, and summing overcounts so badly the split
would fire within a handful of turns.

```
1. after each completed turn, read context occupancy from the transcript (№24)
2. at ~80%:  claude --resume <uuid> --fork-session   →  successor session
3. compact the successor
4. mark the original uuid as the predecessor — it is already on disk, untouched
```

The fork is what makes this clean: the predecessor needs no special handling because it is simply
the session as it stood, never written to again.

✅ **Step 3 VERIFIED 2026-07-29 (spike B, №18).** `/compact` sent as a plain stream-json user turn
performs real compaction — `system/compact_boundary` event on the stream, `compact_boundary`
record (with `compactMetadata`) in the transcript. The lineage design needs no fallback. Note the
compaction writes a `<synthetic>` zero-usage assistant message; occupancy readers (№24) skip it.

**Two attachment strategies.** Start with **(a)**:

- **(a) Resume-on-demand** — no idle processes. A node is a session UUID plus a ledger entry;
  messages queue, and the session is resumed to consume them. Survives restarts trivially. Higher
  per-message latency.
- **(b) Live stream-json process** — one process per live node, held open. Low latency, but N idle
  processes and no restart survival without re-attach logic.

The ledger is identical either way; only the transport differs.

**Observability is free.** Every session writes `~/.claude/projects/<project>/<uuid>.jsonl` live.
The UI tails files that already exist — no instrumentation, no agent cooperation required.

---

## 7. Addressing, escalation, and scope

Each node's system prompt states, at minimum:

```
You are node <name>. Your parent is <parent>. Your reports are <children>.
Your peers are <siblings>. Your grant is <n> credits, of which <m> are free.
Escalate to <parent>, not to the user, for anything within your scope.
Ask the user directly only for: <out-of-scope list>.
```

### 7.1 Authority is transitive downward

**An agent holds full authority over its entire subtree, at any depth — not just its direct
reports.** Any ancestor may, against any descendant:

- **message and converse** with it directly, at a moment's notice, however many layers down;
- **hire, retire, rehire** it or anything beneath it;
- **promote** it — pull it up into the actor's own direct hierarchy (§4.5);
- **demote** it — push it down under one of the actor's other descendants (§4.5);
- **dissolve** it and everything below.

The design intent is that a superior should not have to threaten dissolution to get its way. Deep
reach is expected to be **rare in normal operation** — the hierarchy exists so work delegates — but
the authority is unconditional, so a manager is never structurally powerless over its own org.

Interaction with §4.4: retiring a *non-leaf* descendant still refuses and points at `dissolve`, for
the same orphaning reason. Transitive authority widens *who* may act, not *what* is coherent.

### 7.2 Addressing — asymmetric by design

Downward reach is unlimited. Upward reach is **strictly the parent chain**, one hop at a time.

| From → To | Allowed | Notes |
|---|---|---|
| node → any descendant, any depth | ✅ | direction, at will |
| node → its parent | ✅ | escalation |
| node → siblings (same parent) | ✅ | coordination |
| node → a non-parent ancestor | ⛔ **unless holding an audience** (§7.3) | |
| node → anything else (cousins, other branches) | ⛔ | route via common ancestor |
| user → any node | ✅ | always, unconditionally |

This asymmetry is the whole point: **the hierarchy exists to protect upward attention.** A superior
can always reach down; a subordinate cannot always reach up. Without it, deep reach would flood the
root the moment the tree got wide.

### 7.3 Audience grants

Deep reach creates a dead end: if the CEO messages a node five layers down, that node has no
channel to answer on. An **audience** is the reply path.

**Granting — implicit and instant.** When a node messages a descendant that is *not* its direct
child, the recipient is automatically granted audience with the sender. For the duration of the
grant it may message that superior directly, bypassing the parent chain.

**Rescinding — unilateral and instant.** The grantor may revoke at any time, for any reason, with
no notice required. The channel closes; the grantee falls back to the parent chain.

**Requesting — the proper channels.** The reverse does not exist. A subordinate **cannot request an
audience directly** with a distant ancestor. It asks its immediate superior, who may ask theirs, and
so on up the chain until the request reaches the target, who grants directly if they wish. Every
hop may decline or simply handle it, and the request stops there.

```
grant:    CEO ──────────────────────────────────▶ node        one hop, instant, implicit
request:  node ──▶ mgr ──▶ dir ──▶ VP ──▶ CEO                 n hops, each may refuse
```

Grants flow **down fast**; requests climb **up slowly**. Attention is cheap to give and expensive
to demand — which is the correct asymmetry for an org chart and the reason the CEO's inbox stays
survivable as the tree grows.

**Lifecycle rules:**

| Event | Effect on the grant |
|---|---|
| Grantor rescinds | Revoked |
| Grantee retired or dissolved | Revoked |
| Grantee **promoted to be the grantor's direct child** | Redundant — retire the grant, the parent edge supersedes it |
| Grantee promoted/demoted such that **grantor is no longer an ancestor** | ⚠️ **Auto-revoke** — see below |
| Grantor retired | Revoked (nothing to talk to) |
| Idle for a long period | Open decision (§12 №8) — default: no expiry |

⚠️ **The auto-revoke rule is the one non-obvious interaction between the two features.** An
audience is a *sanctioned* exception to upward routing, justified by the grantor being an ancestor.
If a promote or demote moves the grantee out from under the grantor, an un-revoked grant becomes a
**lateral back-channel between unrelated branches** — precisely the thing §7.2 forbids. The
re-parenting operation must sweep audience grants and revoke any whose ancestor relationship no
longer holds.

**Reaching past intermediates** is organizationally meaningful — going over someone's head. It is
handled by the **chain notice** mechanism defined in §7.4, at the lower of its two levels.

### 7.4 Direct user input

The naive case is that the user talks only to the CEO, which delegates downward. That case must
stay naive — none of the machinery below should be visible when it is all the user does.

But every node is individually addressable **and interactable** at any time, and that authority
needs a shape.

**The user is not above the org — the user IS the org's root.** There is no CEO node. The user
creates an **organization** and spawns any number of **top-level agents** directly beneath it,
exactly as any agent spawns its own reports. The user leads its org in a way completely isomorphic
to how every agent leads its suborg.

| Rule | How it lands for the user |
|---|---|
| §7.1 authority is transitive downward | Unconditional authority over the entire org, at any depth |
| §4.2 the five ops + promote/demote | All available to the user, anywhere in the tree |
| §7.3 messaging a non-child descendant grants audience | Any node the user speaks to gains an audience **with the user** |
| §7.3 grantor rescinds at will | The user closes any channel, any time |
| §7.2 upward reach is the parent chain | A node cannot address the user unbidden — it escalates to its parent |
| §7.3 requests climb one refusable hop at a time | A node wanting the user's ear asks its superior, and so on up |
| §7.2 siblings may coordinate | **Top-level agents are peers** — they talk to each other directly, like a shared chatroom |

Two properties are unique to the user and follow from being the root rather than being special:

1. **Credits are unbounded.** The user's pool is infinite — it is the source of every credit in the
   org (§4.1). Consequence: a forcible hire by the user (§4.6) can never fail.
2. **Top-level agents have a direct line.** They are the user's direct reports, so they are the
   only agents that can put a question to the user and get an answer without an audience grant.
   Everything deeper goes through the chain, or holds a grant.

### 7.5 The user's inbox — a second, asynchronous channel

Speaking **to** an agent and being written **to** by an agent are different things and need
different channels:

| Channel | Direction | Semantics |
|---|---|---|
| **Direct conversation** | user → agent | A real user turn injected into that agent's session (§6.4). Enters its context, it responds. Synchronous in feel. |
| **User inbox** | agent → user | A queued message the user reads whenever. Enters no context, blocks nobody, interrupts nothing. |

The inbox is how a report escalates without commandeering the user's attention — the counterpart to
"notify without interrupting" (§7.4) pointed the other way. Who may write to it follows the
existing rules with no additions: **top-level agents always** (direct line), **anything deeper only
while holding a user audience.** Everyone else asks their superior, who may pass it up.

**Differences from an agent-held audience**, which are worth encoding rather than discovering:

- **The user is a scarce resource.** Agent-to-agent audiences can accumulate harmlessly; user
  audiences accumulate into a crowd of nodes able to interrupt a human. The UI must show which
  nodes currently hold one so they can be pruned, and pruning should be one click.
- **User instructions outrank the chain.** If the user tells a leaf to do something its manager
  forbade, the user wins. The node should know it is acting on user authority so it can say so when
  its manager asks — otherwise it either capitulates to a stale instruction or looks insubordinate
  for no stated reason.

#### Chain notices

**When the user interacts with any node other than the root, every superior up the chain is
notified — without interruption.**

The reason is the §7 principle stated plainly: a manager that does not know the user overrode it
keeps operating on stale assumptions. It will re-derive a decision already made, or report progress
on a task that has been redirected. The notice is what stops a correct hierarchy from producing
confidently wrong work.

**"Without interruption" is a specific requirement:** inject into each superior's context at its
next turn boundary. Do not wake an idle node, do not preempt a running one, do not queue it as a
message demanding a reply. It is context, not a task.

A notice carries the minimum a superior needs to update its model — **not** the transcript, which
it can open in the UI or reach down and ask about:

| Field | Why |
|---|---|
| `node` | who the user talked to |
| `at` | when |
| `kind` | **question · request · decision** — the distinction the user named, and the one that matters. A question probably changes nothing for the manager; a decision may invalidate its current plan |
| `gist` | one line. Enough to know whether to look further |

**Two levels, one mechanism.** Chain notices also cover agent-to-agent deep reach, at a lower
level — because the underlying problem is identical, and having one mechanism means the level can
be tuned later without a redesign:

| Trigger | Level |
|---|---|
| User interacts with a non-root node | **Notify** — injected into every superior's context at its next turn |
| An agent reaches past intermediates into its own subtree | **Log** — recorded in the tree, visible on inspection, not injected |

The split reflects frequency and consequence: user intervention is rare and authoritative; agent
deep reach is routine and already within that agent's authority. Decision №9 records this and the
option to raise agent deep reach to *notify* if the log turns out to be too quiet in practice.

**The one thing that stays with the human:** destructive, irreversible, or outward-facing actions.
Not as a trust check — every node is the same model with the same memories — but because nodes
hold **different context** and can be confidently wrong about each other's in-flight work. Phrased
as adopted between the existing chats: *authority decides what I should do, it cannot decide what
is true. Check each other's facts freely; do not check each other's credentials.*

---

### 7.6 Visibility — read down, talk sideways and up

Data access and messaging follow **different shapes**, and the mismatch is the point.

| | Own | Subordinates (any depth) | Coworkers (siblings) | Superior |
|---|:---:|:---:|:---:|:---:|
| **Read scratch space** | ✅ | ✅ | ⛔ | ⛔ |
| **Read inbox** | ✅ | ✅ | ⛔ | ⛔ |
| **Read transcript** | ✅ | ✅ | ⛔ | ⛔ |
| **Send messages** | — | ✅ | ✅ | ✅ |

**Reading is strictly downward. Messaging is downward, lateral, and one hop up.** You may talk to
your peers and your boss; you may not read them.

#### Why banning *upward* reads is what makes *lateral* isolation work

The two prohibitions look like separate rules. They are one rule with a consequence.

A superior's transcript contains its conversations with **all** of its reports. If B could read its
parent A, B would learn everything A discussed with its sibling C — lateral isolation would leak
through the parent even with direct sibling reads blocked. Banning the upward read is therefore not
about protecting the boss; it is what makes the sibling ban hold transitively.

The same argument gives the user total visibility for free: everything is in the user's subtree, so
"read down" already means "read everything."

#### What this is for — and what it is not

This is an **information-architecture rule, not a security rule**, exactly like the addressing
rules in §7.2. §11 stands unchanged: every session runs as the same OS user with FullControl over
every file, so a determined node can read anything. Three real reasons it is still worth having:

1. **Context hygiene.** An agent should not be *able* to casually drown itself in its superior's
   context. Focus, not secrecy.
2. **Correctness.** Acting on information you were never given breaks the reasoning in §7 — the
   whole "different context, confidently wrong" model assumes contexts are actually different.
3. **Predictability.** You can reason about what an agent knows, which makes its behavior
   debuggable.

#### How much is actually enforced

More than convention, less than a sandbox:

| Path | Enforcement |
|---|---|
| **Scratch spaces**, file tools (Read/Write/Edit/Glob/Grep) | ✅ **Real.** `--add-dir` restricts tool access to an explicit directory set. A node is launched with its own scratch dir plus its descendants' — its file tools genuinely cannot reach a superior's or a peer's. |
| **Transcripts and inboxes** | ✅ **Real, via mediation.** Both live outside any node's allowed directories, reachable only through orgtree MCP tools that check ancestry before returning anything. |
| **Bash** | ⚠️ **Leaky.** `cat` reaches any path the OS allows. Tightenable with `--allowed-tools "Bash(...)"` patterns, never closable. |

So the honest summary: **a node cannot read upward by accident, and cannot read upward through any
tool orgtree provides. It can read upward if it deliberately shells out.** Given §1's non-goals,
that is the correct amount of enforcement to buy.

#### Scratch space layout

Nesting scratch dirs to mirror the org tree is tempting — containment would then be automatic and
`--add-dir` would need no enumeration. **Don't.** Promote and demote move nodes, and moving a
directory out from under a live session's cwd is exactly the kind of fragility to avoid.

Use a **flat layout** with an explicit access list:

```
~/orgtree/scratch/<name>/          one dir per node, flat
launch:  cd <own scratch>  --add-dir <each descendant's scratch>
```

⚠️ **`--add-dir` is a launch-time flag.** Re-parenting changes a node's descendant set, but a live
node keeps the access list it started with until it is next resumed. Either accept the lag or
bounce affected nodes after a re-parent. Logged as decision №21.

### 7.7 Can the shell be sandboxed? — the enforcement ceiling

**Not within Claude Code alone. Yes with one OS identity per node, and WSL2 is already installed
on this machine** (Ubuntu 24.04, verified 2026-07-28).

#### Why Claude Code alone cannot do it

- **There is no sandbox flag.** `claude --help` mentions "sandbox" exactly twice, both inside the
  descriptions of `--dangerously-skip-permissions` ("Recommended only for sandboxes with no
  internet access") — that is advice about running Claude *inside someone else's* sandbox, not a
  feature it provides.
- **The gating it does have operates on the command string.** `--allowed-tools "Bash(…)"`,
  `--disallowed-tools`, and PreToolUse hooks all inspect text. That is a filter over a
  Turing-complete input: `cat ../x`, `sh -c`, a Python one-liner, `$(…)`, base64. Writing a
  blocklist against something that can express the same read a thousand ways stops accidents, not
  intent.
- **Different threat model.** Claude Code's sandboxing, where it exists, protects **the host from
  the agent**. It does not isolate **agents from each other**, and it cannot produce a *tree-shaped*
  read rule — every node is the same OS principal, so there is nothing for it to discriminate on.

#### What actually closes it: one UID per node

Give each node its own Unix identity and file permissions do the work — the org tree becomes a
permission tree.

```
one Unix UID per node
scratch dir owned by the node, mode 0700
setfacl -m u:<each ancestor>:rx   →  ancestors can read, peers and superiors' peers cannot
re-parent  →  rewrite the ACL
```

💡 **This also fixes decision №21.** `--add-dir` is a launch-time flag, so a re-parented node keeps
stale access until it is next resumed. ACLs are evaluated at `open()`, so a re-parent takes effect
**immediately, on a live node.** The stronger mechanism is also the more responsive one.

#### The costs, stated honestly

| Cost | Detail |
|---|---|
| **Credentials per user** | Each Unix user gets its own `~/.claude`, so each needs auth. Either a shared read-only credentials file or per-user provisioning. ⚠️ Whether Claude Code tolerates a shared/symlinked credentials file is **unverified** — test before committing (decision №23). |
| **The org lives in WSL** | Windows-side workspaces are then reached across the 9p bridge, which is slow for file-heavy work. Relevant here, since the real projects sit on `E:\` and `C:\Program Files (x86)\…`. |
| **Privileged launcher** | Something must `sudo -u <node> claude …`, so the coordinator holds rights every node lacks. |
| **Operational drag** | Cross-user debugging, log access, and cleanup all get more annoying. |
| **Build cost** | ~1–2 days on top of the current plan. |

#### Tiers, and the recommendation

| Tier | Mechanism | Stops | Cost |
|---|---|---|---|
| **0** *(planned)* | `--add-dir` + MCP-mediated transcripts/inboxes | Every access through a provided tool; all accidents | Free — already in §7.6 |
| **1** | + Bash command gating via `--allowed-tools` / PreToolUse hook | Plausible, well-intentioned reads | Hours |
| **2** | One UID per node + ACLs, in WSL | Everything short of privilege escalation | 1–2 days + the table above |
| **3** | Container per node | Everything | Disproportionate for a desktop org |

☞ **Build Tier 0 now; add Tier 1 cheaply; design the launcher so Tier 2 is a swap, but do not build
it.** Per §1 there is no adversary — these are the user's own agents on the user's own machine.

But **Tier 1 is worth more than its position suggests**, and for a reason that is easy to miss: the
realistic failure here is not a hostile agent, it is a **helpful** one. An agent trying to be
thorough may well decide that reading its superior's transcript would help it do its job, and then
just `cat` it. That is precisely the access a command-string filter *does* stop — a helpful agent
does not base64-encode its way around a blocklist. Tier 1 defends against initiative, which is the
actual risk, at a fraction of Tier 2's cost.

---

## 8. Compaction and lineage

### 8.1 The idea

Naive compaction discards the pre-compaction chat: the summary replaces the original and the
detail is gone. In an org tree that is a waste, because the old session is the only thing that
still holds what the summary flattened.

Instead, **compaction splits a node into a successor and a predecessor.** The successor is the
compacted node and carries on the work under the same name and the same parent. The predecessor is
**retired in place** — costing nothing, per §4.2 — and can be rehired later to answer questions
about what it still remembers in full.

### 8.2 Why the threshold drops to 80%

Compacting at ~95% leaves the predecessor with no room to be useful: rehire it and it is instantly
out of context. **Compacting at ~80% is what buys the knowledge-bearer state** — it leaves roughly
20% of the window as working headroom for answering questions.

This is the mechanism, not a tuning knob. Concretely, on a 1M-token model that is ~200K of Q&A
headroom before the predecessor is exhausted; on Haiku 4.5 (200K context) it is ~40K.

The cost is real and should be stated: compacting earlier means **more frequent compactions**, a
**deeper lineage stack**, and slightly less working context per generation. The trade is detail
retention for working room, and it is the right trade only because predecessors are free to keep.

### 8.3 The three states

A node's lifecycle across a compaction boundary:

| # | State | Live? | Can it work? | Context behavior |
|---|---|---|---|---|
| 1 | **Working** | ✅ | Full agentic work, hires, delegates | Grows until the 80% threshold, then compacts |
| 2 | **Knowledge bearer** | on rehire | ❌ answers only — no hiring, no delegation | Grows within its remaining headroom |
| 3 | **Preserving oracle** | on rehire | ❌ answers only | **Does not grow** — every exchange is discarded afterward |

State 1 → 2 happens at compaction. State 2 → 3 happens when the predecessor's own headroom is
exhausted. **A predecessor never compacts** — it has already been compacted, in the form of its
successor. Compacting it again would destroy the only reason it exists.

State 3 is the fallback, to be avoided when possible: a node at 95%+ has very little room for the
question *and* the answer, so long answers may not fit and quality degrades near the ceiling. But
it is strictly better than the alternative, which is having thrown the context away.

### 8.4 Mechanism: `--fork-session`

State 3's "revert the context to before the question was asked" needs no context-editing
machinery. The CLI already has the primitive:

```
--fork-session   When resuming, create a new session ID instead of reusing the original
```

So a state-3 query is: **`--resume <predecessor-uuid> --fork-session`** → converse in the fork →
**discard the fork.** The canonical session is never written to and never grows. Follow-ups work
normally *within* one exchange; the revert is simply throwing the fork away at the end.

That also makes state 2 and state 3 the same code path with one flag flipped — state 2 resumes in
place and keeps what it learns, state 3 forks and drops it.

💡 **Prompt caching makes state 3 far cheaper than it looks.** Every fork shares the entire
predecessor prefix, so repeat questions hit cache reads (~0.1× input price) rather than paying full
freight. Caveat: cache TTL is 5 minutes by default, 1 hour opt-in — sporadic questions spaced hours
apart will miss and pay a cold write each time. If a predecessor is being consulted repeatedly,
that is an argument for the 1-hour TTL on its prefix.

### 8.5 Lineage is a second axis — not a parent/child edge

⚠️ **A predecessor must not appear as a child of its successor.** It is a *former self*, not a
subordinate. Putting it on the org axis would pollute the tree, break `descendants()`, and make
authority and credit rules incoherent (a node would be paying a seat for its own past).

There are two independent relationships:

```
org axis:      parent ─────▶ child          authority, credits, routing  (§4, §7)
lineage axis:  predecessor ──▶ successor    memory                       (§8)
```

Rules that follow:

- A predecessor keeps **the same parent** as its successor. It occupies the same org slot.
- Any ancestor may rehire a predecessor, by §7.1 — including the parent, which is the case the
  design was asked for.
- **The active node also has authority over its own lineage** (proposed — decision №14). The node
  that lost the context is the one most likely to need it, and it is the same agent talking to its
  own past. Without this it would have to ask its parent for permission to remember something.
- A predecessor is **outside the routing graph**: it holds no audiences, grants none, hires
  nobody, and receives no chain notices. It answers whoever rehired it. It is an oracle, not an
  org participant.
- `dissolve` on a node takes its **entire lineage stack** with it.

### 8.6 Credits

- A retired predecessor holds **0 credits** — §4.2 already gives this for free.
- Rehiring one costs its **seat cost, with a grant of 0**. A knowledge bearer cannot hire, so it
  has no use for a grant, and forcing one would make consultation needlessly expensive.
- Stranding (§4.4) applies: a manager whose credits were clawed back may be unable to afford to
  consult its own history. The reclaim warning should name stranded **predecessors** distinctly
  from stranded reports — losing access to a memory reads very differently from losing a worker.

### 8.7 Visual

Predecessors render **stacked behind** the active node — a card with visible depth, one layer per
generation, newest immediately behind the active face. The stack is a compactness signal at a
glance: a node with six layers behind it has been running a long time.

Interaction: the stack is inert until touched. Clicking it fans the generations out; rehiring one
lifts it out of the stack and gives it its own live card, visibly tethered to its successor. On
retire it drops back into the stack. A predecessor in **state 3** should be marked distinctly —
consulting it is lossless by construction, but the mark tells the user its answers are constrained.

### 8.8 Open questions

Logged as decisions №13–№16 in §12.

---

## 9. The interface

### 9.1 Principle

**Simple by default, exhaustive on demand.** The tree is the home view and should be readable at a
glance with no legend. Everything else is progressive disclosure — one click deeper, never a
control panel.

The failure mode to avoid is a dashboard that encodes so much simultaneously that nothing reads.
The way out is §9.3: give each quality its **own visual channel** rather than competing for
"color".

### 9.2 What must be inspectable

For any node, reachable in at most two clicks:

- **Full transcript**, live-tailed
- **Configuration** — model, tier, permission mode, allowed/disallowed tools, appended system
  prompt, credits held and free, grant, seat cost
- **Message history with attribution** — every message to and from this node, and *which node* each
  came from or went to
- **Relationships** — parent, children, siblings, full ancestor path, subtree size
- **Lineage stack** — every prior generation, its state, and when it compacted (§8.7)
- **Audiences** — held (who it may speak up to) and granted (who may speak up to it)
- **Chain notices** — what it has been told about interventions above and below it
- **Scratch space** — its working directory, browsable in place

The user sees all of this for every node, since the whole org is the user's subtree (§7.6). An
agent's own view of the org is scoped the same way: it can open any node beneath it, and sees peers
and superiors as addressable names with no readable interior.

### 9.3 Five independent channels

Colour alone cannot carry five orthogonal qualities. Each gets a **separable** channel, so they
compose without turning into mud:

| Quality | Channel | Why this one |
|---|---|---|
| **Model tier** | **Hue** | The strongest categorical channel, and only four values (haiku / sonnet / opus / fable). This is the "what am I looking at" signal, so it gets the best channel. |
| **Lifecycle state** | **Fill treatment** — saturation + lightness | Ordinal and naturally read as "vitality". Drains toward grey without changing hue, so a retired Opus node is still recognizably Opus. |
| **Permission level** | **Border** | Reads independently of fill. Thin/dashed = read-only · solid = read-write · thick + warning stroke = bypass-all. A dangerous node is visibly fenced. |
| **Audience** | **Outer glow** | An attention channel, unused by the others. Soft glow = holds an agent audience. **Distinctly brighter glow = holds an audience with the user.** |
| **Credits** | **Numeric badge** | Precision matters here; colour cannot express "7 of 15 free". |

**Depth deliberately gets no channel** — it is already encoded by position in the tree, which is
the most legible encoding available. Spending hue or lightness on it would be redundant *and*
collide with tier and lifecycle. A subtle lightness ramp may reinforce it; it must not carry it.

### 9.4 Lifecycle fill treatments

| State | Treatment |
|---|---|
| **Active / hired** | Full saturation, full opacity — the baseline |
| **Retired / fired** | Darkened and desaturated toward grey; recedes |
| **Knowledge bearer** (§8.3 state 2) | **Light grey** wash — present, consultable, not working |
| **Preserving oracle** (§8.3 state 3) | **Darker grey** wash — consultable, but answers cost it nothing and change nothing |

The two knowledge-bearer greys are deliberately adjacent: they are the same *kind* of thing, and
the darker one signals "closer to the end of its usefulness".

### 9.5 The inbox reuses the tree's encoding exactly

A message chip is styled by the **same rules as its sender's node card** — same hue for tier, same
fill for lifecycle, same glow for audience. Learn the scheme once, read it everywhere.

On top of that, each message carries:

| Signal | Encoding |
|---|---|
| Direction | Arrow + indent: **from a superior** (down-arrow, flush) · **from a subordinate** (up-arrow, indented) · **from a peer** (side-arrow) · **from the user** (distinct, always flush and prominent) |
| Sender's tier | The chip's hue — a message from a Haiku scout looks different from one from a Fable strategist |
| Sender's **current** state | The chip's fill, resolved *now*, not at send time — so a message from a **since-retired** agent shows as retired, telling you at a glance that you cannot reply to it |
| Sender is a knowledge bearer | Grey wash per §9.4 — the answer came from memory, not from live work |

That third row matters more than it looks: the most confusing thing in an async org log is
replying to someone who no longer exists.

### 9.6 Accessibility

Hue alone fails for colour-blind users, and tier is the quality most load-bearing. Every tier
carries a **redundant glyph or letter badge** (`H` `S` `O` `F`) alongside its hue. Lifecycle is
already redundant (saturation + lightness). Permission is shape, not colour, so it is safe.

### 9.7 Palette selection is deferred to build time

This section specifies **channel assignment** — an information-design decision that belongs in the
plan. It does **not** specify hex values, which belong with the implementation.

☞ **Load the `dataviz` skill when building v0.4.** It covers categorical palette construction,
contrast validation in light and dark themes, and the accessibility checks above. Inventing colours
here would mean redoing them there.

---

## 10. Validation already done

A ledger spike was written and exercised on 2026-07-28
(`…/scratchpad/orgtree.py` + `demo.py` — scratchpad is temporary; port before it is cleaned).

Confirmed working:

- The worked example reproduces exactly: 2 fable + 6 opus = 50 held, audit consistent, zero free.
- `retire` frees seat + grant; audit closes.
- `rehire` restores state and preserves `session_id`.
- `dissolve` on a subtree freed 40 (fable seat 10 + grant 15 + 3×5 live opus) in one call.
- Leaf guard refuses `retire` on a node with live reports and names them.
- Overdraft guard refuses a hire that exceeds `free(parent)`, showing the arithmetic.
- Stranding (§4.4) reproduced deliberately.

---

## 11. Hard limits — things engineering cannot remove

1. **No real user-turn injection.** Nothing in the harness lets an external process type into a
   running session. Anything the UI sends arrives through the message channel. "Individually
   addressable" therefore means *queued and surfaced promptly*, not *typed into its terminal*.
2. **Latency floor = the node's current work.** A node sees its inbox between turns. Mid-tool-call
   — a five-minute encode, a long build — a message waits. The UI must distinguish **busy** from
   **unresponsive** or it will lie.
3. **No security boundary between nodes — while every node runs as the same OS user.** Verified:
   every session holds `FullControl` on every other session's transcript, and same-user process
   handles are obtainable, so a secret cannot be hidden even in process memory. **This limit is
   escapable, unlike the other three** — one UID per node in WSL turns it from a hard limit into a
   cost. See §7.7 for the mechanism, the price, and why Tier 1 is the better buy.
   Consequence: the budget ledger is a *coordination* mechanism, honest-by-default, not a
   *sandbox*. A node that wants to overspend can edit `orgtree.json`.
4. **Session IDs shift on re-creation.** Node identity must be the name (§5).

---

## 12. Open decisions

| # | Decision | Status / default |
|---|---|---|
| 1 | Stranding policy (§4.4) | ✅ **RESOLVED 2026-07-28 — warn at reclaim**, naming the affected nodes |
| 2 | Does the root's seat come out of its own grant? | No |
| 3 | Attachment strategy (§6) | Resume-on-demand |
| 35 | Stack *(renumbered from a colliding №9)* | ✅ **RULED 2026-07-29 — Python backend + JS frontend framework.** Python keeps the validated ledger spike, MCP server, and supervisor; the UI gets a real framework for §9's encoding work. Framework ruled same day: **React + Vite** |
| 36 | Org cardinality *(renumbered from a colliding №10)* | ✅ **RULED 2026-07-29 — multiple concurrent orgs.** ⚠️ Consequence: routing, addressing, the user inbox, and the UI all need an org dimension **from the start**; `orgtree.json` becomes per-org or grows an `orgs` map. Not a late add-on |
| 37 | MVP shape *(renumbered from a colliding №12)* | ✅ **RULED 2026-07-29 — UI-first.** ⚠️ This inverted §13's terminal-first sequencing; the build plan below was re-cut accordingly. Also raises §6.4 from "the UI is not optional" to "the UI is the product" |
| 25 | Who drives a node's turns? | ✅ **RULED 2026-07-29 — message-driven + report on completion.** A node runs when messaged, works until done, then messages its parent and idles. Managers never poll. Implementor addenda (presented 2026-07-29, unobjected): completion is a required `report_status` MCP tool call; a turn ending with **no** status → node idles **and** the parent is told "no status reported" (never nudge-forever); auto-continue nudges are bounded |
| 4 | Does fast mode cost 10 credits, or is it unavailable to nodes? | Not offered in v0.1 |
| 5 | Per-node `--permission-mode` policy by depth/scope | ✅ **RULED 2026-07-29 — autonomy within allowed dirs. VERIFIED (spike E3): the recipe is `acceptEdits` + `--add-dir <granted>`** — writes allowed in cwd + granted dirs, denied outside; in-scope Bash writes auto-approved; safe reads auto-approved everywhere. `dontAsk` is a **lockdown** (auto-denies all would-prompt actions, even in added dirs — the old "dontAsk below root" default would have produced nodes unable to write a file). `delegate` emits **no** permission traffic over stream-json (behaves as deny headless). `bypassPermissions` remains rejected |
| 6 | Where does this repo live permanently? | `~/.claude/orgtree` (matches the `claude-intranet` precedent) |
| 7 | Should credits be integers only, or fractional? | Integers |
| 8 | Do audiences expire when idle? (§7.3) | No expiry — revoked only by the lifecycle-table events. ⚠️ Revisit for **user** audiences specifically (§7.4): the human is scarce, and unbounded channels mean unbounded interruption |
| 9 | Chain-notice level for agent-to-agent deep reach (§7.4) | ✅ Resolved as a two-level split — **notify** for user interventions, **log** for agent deep reach. Raise agent reach to *notify* if the log proves too quiet |
| 10 | Should retiring a non-leaf auto-promote its children to its parent, instead of refusing? | Refuse and point at `dissolve` (current). Auto-promote is the real-org behavior but can strand or exceed the grandparent's free — revisit after v0.3 |
| 11 | Does a user audience survive the node being promoted/demoted? (§7.3 auto-revoke sweep) | **Yes — always.** The user is an ancestor of every node by construction, so the sweep can never revoke a user audience. Called out because the general rule would otherwise look like it applies |
| 12 | Should chain notices be emitted for user interaction with a **top-level agent**? | No — its only superior is the user, who is the one acting. Notices start at depth 2. This is what keeps the naive case naive |
| 19 | Do reclaimed credits from a cascaded hire (§4.6) auto-return to the actor? | No — they settle in the immediate parent. The actor reclaims explicitly with `reallocate`. Auto-return would surprise a manager whose budget shrank without it acting |
| 20 | Can top-level agents dissolve *each other*? | No — they are peers, not ancestors. §7.1 authority is strictly downward; only the user can act on a sibling branch |
| 21 | `--add-dir` access lists go stale after a re-parent (§7.6) | Accept the lag at Tier 0; access refreshes on next resume. **Solved outright at Tier 2** (§7.7) — ACLs are evaluated at `open()`, so a re-parent applies immediately to a live node |
| 24 | §6.5 watched stream-json usage, but №3 picks resume-on-demand, which has no live process | ✅ **RESOLVED + VERIFIED 2026-07-29 (spike F)** — read the **latest non-synthetic assistant message's** usage from the transcript `.jsonl`: `input + cache_read + cache_creation` IS the occupancy (measured 39,934 = 20% of haiku's 200K). ⚠️ Summing across turns measured **4.9× overcount after only 6 messages**. Bonus: for a *managed* stream-json process, every `result` event carries `usage` + `modelUsage` + `contextWindow` live — no transcript parsing needed on that path |
| 26 | Task completion / reaping — is `retire(self)` legal? | ✅ **Settled 2026-07-29** (implementor proposal, presented unobjected): `report_status(done, summary)` → parent notified, node idles; parent decides retire / reassign / keep warm. `retire(self)` legal for **leaves only** (§4.2 already blocks a manager with live reports). No auto-reap timer in v0.x — the UI must surface idle-time + held credits per node so saturation stays visible |
| 27 | Message envelope format | ✅ **Settled 2026-07-29** — one injected user turn per delivery, fixed plain-text schema: (1) **MAIL** — per message: from-node (name, tier, relationship incl. USER), kind, sent-at, body, user-authority marker (§7.5); (2) **NOTICES** — queued chain notices; (3) **ORG-STATUS footer**, regenerated at every delivery: parent, children + states, free/total/per-child credits, held audiences, **lineage — predecessors + their states** (§8 is dead if a node doesn't know its past selves are consultable), and an explicit "audience just granted" line when mail from a non-parent ancestor creates one (§7.3) |
| 28 | Who authors a chain-notice gist — the actor, or an orchestrator summarization call? | ✅ **Settled 2026-07-29 — hybrid.** Agent actors author the gist at send time (required tool arg). User-via-UI interactions: first ~80 chars of the user's own message (free, no failure mode, arguably more faithful); haiku summarization only when the excerpt reads badly |
| 29 | `--append-system-prompt` bakes in parent/siblings/children/credits at launch; all drift | ✅ **Settled 2026-07-29** — the appended system prompt carries only **stable identity** (name, role, standing rules, read-access scope §7.6); everything drifty (parent, children, credits, audiences, lineage) lives in the envelope's org-status footer (№27). ✅ **VERIFIED honored on `--resume` (spike D)** — the stable prompt can be regenerated fresh at every resume |
| 31 | **No failure model anywhere in this plan** | ✅ **Accepted as build requirement 2026-07-29** — startup reconciliation pass (every ledger-live node must actually resume) + an `unrecoverable` state for "ledger says live, session dead". In scope from the first spawning milestone. Was the largest known hole |
| 32 | **Nothing watches real dollar spend** | ✅ **Accepted 2026-07-29** — surface dollars per node and per org in the UI, computed from transcript usage (nearly free — the files are already parsed for №24) |
| 33 | Resume-on-demand cache economics are unmodeled | ✅ **Accepted 2026-07-29; early data is good (spike A)** — this environment already writes the **1-hour cache tier by default** (`ephemeral_1h_input_tokens`), and a follow-up turn read the full prefix from cache. Still measure at org scale, but the cold-write worry is smaller than modeled |
| 34 | No max depth, no max children | ✅ **Accepted 2026-07-29** — configurable caps: max depth, max children per node, and max **concurrent node turns** (default 3; protects subscription rate limits) |
| 30 | **Which project directories may a node touch?** (§7.6 covers scratch only) | ✅ **RULED 2026-07-29 — inherited capability set.** Granted at hire; a node may pass on only dirs it itself holds; explicit revoke; swept on re-parent **and** on retire/dissolve of an intermediate. NOT credit-like — dirs aren't exclusive, nothing conserves, no §4.5 path machinery applies. Top-level default: **the dirs named at org creation**; deeper hires get what their parent passes down. Enforced via `--add-dir` at launch/resume |
| 23 | Does Claude Code tolerate a **shared or symlinked credentials file** across Unix users? (§7.7) | ⚠️ **Unverified — gates Tier 2.** If it does not, per-node isolation means per-node auth provisioning, which changes the cost materially. Cheap to test; do it before costing Tier 2 |
| 22 | Should a node be told *that* an ancestor read its transcript? | No. Reading down is routine and unremarkable; notifying would make ordinary supervision feel adversarial. Contrast §7.4, where the user *acting* on a node is notable enough to notify |
| 13 | Exact compaction threshold (§8.2) | 80%. Tune per model — the useful quantity is *absolute headroom left*, and 20% of 1M is very different from 20% of 200K |
| 14 | Does the active node have authority over its own lineage? (§8.5) | **Yes** — the node that lost the context is the one most likely to need it, and it is the same agent. Without it, remembering requires asking your parent |
| 15 | Are predecessors ever pruned? | No automatic pruning — they cost 0 credits and only disk. Manual prune available; `dissolve` takes the whole stack |
| 16 | Can a knowledge bearer be rehired at a **cheaper tier** than it ran at? (§8.6) | ✅ **VERIFIED 2026-07-29 (spike C)** — `--resume` honors a changed `--model`: haiku session resumed on sonnet, transcript confirms the new model served the turn. Knowledge bearers can be consulted at haiku price regardless of original tier |
| 17 | Managed↔attached handoff protocol (§6.4) | Orchestrator releases on request; node marked `attached` in the ledger and excluded from routing until released. One driver at a time is non-negotiable |
| 38 | CLAUDE.md reinterpretation by depth (§15) | ✅ **RULED + IMPLEMENTED + LIVE-VERIFIED 2026-07-29** — top-level literal · deeper verbatim-with-redirect-to-superior · user-audience exception |
| 39 | Granted-folder CLAUDE.md delivery | ✅ **Implemented** — explicit injection (native discovery doesn't reach them headless, spiked); ≤6 KB per folder |
| 40 | `org.md` | **Docket.** v0 works today via `<workspace>/CLAUDE.md`; UI editor in ⚙ settings later |
| 41 | Team charters (manager-owned, binds subtree) | **Docket** — cascade like capabilities; needs authoring surface + prompt layering |
| 42 | Node charter (long-form purpose, at hire/⚙) | **Docket** |
| 43 | Scratch self-notes | ✅ **Implemented** — native cwd load (spiked), nodes told to maintain `scratch/CLAUDE.md`; survives compaction, doubles as lineage memory |
| 44 | Audience **rescind** UI/API (user one-click revoke; §7.5 wanted it) | **Docket — gap found during §15 verification**; only re-parent sweeps revoke today |
| 18 | Can `/compact` be triggered programmatically? (§6.5) | ✅ **VERIFIED 2026-07-29 (spike B)** — `/compact` sent as a plain stream-json user turn on a resumed session performs real compaction: `system/compact_boundary` event + `compact_boundary` transcript record with `compactMetadata`. §8 works as drafted. ⚠️ Compaction inserts a `<synthetic>` zero-usage assistant message — occupancy readers skip it |

---

## 13. Build plan — re-cut 2026-07-29 for the UI-first ruling (№37)

Stack per №35: **Python backend** (ledger + supervisor + orgtree MCP server + FastAPI serving the
UI and a WebSocket event stream) · **React + Vite frontend**. Multi-org (№36) is first-class from
v0.1 — every API route, ledger file, and UI view carries the org dimension. Per the UI-first
ruling, every milestone lands as a usable increment **in the UI**; there is no terminal-only stage.

| Phase | Deliverable | Effort |
|---|---|---|
| **v0** | ✅ **DONE 2026-07-29 — all six spikes PASS** (see `spike/FINDINGS.md`): №18 `/compact` works over stream-json · №16 `--resume` honors a changed model · №5 recipe = `acceptEdits` + `--add-dir` (`dontAsk` is a lockdown; `delegate` inert headless) · №29 appended prompt honored on resume · turn injection works incl. on resume · №24 last-message occupancy verified (summing = 4.9× overcount). Plus: nodes need `--settings`/`--strict-mcp-config` isolation (they inherit user hooks/servers); node cwds must live **outside** `~/.claude` (sensitive-path protection); prompts via stdin only (variadic flags swallow positional args); full model ids only (aliases drift) | ✅ |
| **v0.1** | Ledger core ported from the spike (sonnet=3, user-as-root, multi-org) + the five ops + `promote`/`demote` with the LCA path (§4.5), cycle/root guards, consolidated stranding warnings — exposed via FastAPI. **UI shell in the same milestone:** org list, tree view, node cards with credit badges | 1½ days |
| **v0.2** | Supervisor: hire → real session, message → resume-on-demand turn, `report_status`, failure reconciliation (№31), busy/idle truthfulness (§11.2). UI: live state, per-node send box, user inbox | 2 days |
| **v0.3** | Routing: envelope (№27), addressing rules (§7.2), audience grants + requests + re-parent sweeps (audience №11 + dirs №30), chain notices (§7.4), work-dir capability set (№30). UI: audience glow, held-audience list with one-click revoke | 1½ days |
| **v0.4** | Five-channel encoding (§9.3–9.6 — **load the `dataviz` skill first**, §9.7) + live transcript tailing (incremental `.jsonl` parse) + node inspector (§9.2) + dollar spend per node/org (№32) | 1½ days |
| **v0.5** | Compaction split at 80% (№24 watcher), lineage axis, knowledge-bearer + preserving states via `--fork-session` (§8), lineage stack rendering (§8.7) | 1½ days |
| **v0.6** | Restart-resilience hardening, caps (№34), archived-roster view, cache-economics measurement (№33), docs | 1 day |

**≈ 10 days total** (up from the original 6: UI-first pulls the UI into every milestone, multi-org
and the failure model are new scope, and the spike list grew from two items to six). The authority
model and budget ledger remain the cheap part — a tree with an ancestor query and five
preconditions, one of which is already written and tested.

---

## 15. Standing instructions & CLAUDE.md layering (added 2026-07-29)

**Measured behavior (spiked live):** headless node sessions natively auto-load the **cwd
(scratch) CLAUDE.md** but do **NOT** surface CLAUDE.md files from `--add-dir` granted folders,
even after reading files there; no global `~/.claude/CLAUDE.md` exists on this machine.
∴ orgtree owns delivery for granted folders — implemented: each granted dir's `CLAUDE.md`
(≤6 KB) is injected into the identity prompt as `[STANDING INSTRUCTIONS]`.

**RULED (2026-07-29) — depth-dependent reinterpretation, implemented + live-verified:**
- **Top-level agents** work directly under the user: every CLAUDE.md applies **literally**,
  including user-communication instructions. (Verified: top-level probe read "report progress
  to the user" and took it at face value.)
- **Deeper agents** see the same files **verbatim with one reinterpretation**: they never speak
  with the user directly, so any instruction to communicate with / ask / report to "the user"
  reads as directed at their **direct superior**. (Verified: same file, depth-2 node answered
  "report to probe (my superior)".)
- **Exception:** while holding a **user audience**, user-directed instructions may be taken
  literally for its duration; the prompt says so explicitly. (Verified live — a deep node holding
  an audience reported to the user, and redirected to its superior once it lapsed.)

**The cascade model (docketed):** standing instructions flow like capabilities — downward only:
`org.md` (user-owned; **v0 exists today**: drop a `CLAUDE.md` in the org WORKSPACE folder — every
node holding the workspace gets it injected) → **team charters** (manager-owned, binds its
subtree) → **node charter** (long-form sibling of `purpose`, at hire/⚙) → **self-notes**
(`scratch/CLAUDE.md`, auto-loaded natively every turn, survives compaction — nodes are told to
maintain it; this doubles as the lineage-crossing memory system).

---

## 14. Relationship to chatq

orgtree does not replace `chatq` — it can reuse it as the transport for v0.3 addressing, since
chatq already has a registry, hooks, an archive, and a listener. What orgtree adds on top:

- a `parent` edge and ancestor queries,
- credits,
- scope/escalation in the system prompt,
- a UI.

If chatq's routing is reused, note its known property: the `from` field is `argv[2]` with no
authentication (`send.sh:32`), and the queue is a plain directory any node can append to. Under
§1's non-goals that is acceptable; the two cheap improvements worth making anyway are deriving the
sender from `$CLAUDE_CODE_SESSION_ID` instead of accepting it as an argument, and stamping each
message with the derived session id, PID, and parent-process chain so a forged message carries the
evidence of forgery in its own envelope.
