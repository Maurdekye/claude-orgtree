# Decisions — the normative register

What must be true, and who decided it. This file is **decision-shaped, not
time-shaped**: entries live under their domain, and a superseded entry is
rewritten **in place** with the old reading preserved in its `Was.` slot —
never appended as a new entry elsewhere. That single mechanism is what keeps
reconciling cheaper than appending; it is the reason this file exists.

Sorting rule for new material (the register/traps split): if the rule would
**survive a refactor**, it is a decision and belongs here; if it would
evaporate the moment the code was restructured, it is an operational trap and
belongs in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The historical plan
(PLAN.md) is untouched archaeology — do not update it; supersede it here.

Entry template:

```
### D-NNN · short imperative title
Ruling (who, date): the decision, one or two sentences.
Why: the reasoning that produced it — enough to re-derive the ruling.
Bounds: where it stops applying, if anywhere.
Load-bearing: invariants this ruling silently depends on, if any.
Was. the superseded reading, kept verbatim-in-spirit. Omit for new entries.
```

Retired decisions move to the `## Retired` tail with their `Was.` intact.

Two conventions the template alone doesn't state:
- **Reasoning may ride inside the Ruling paragraph** when it is inseparable
  from the statement ("~80%, not ~95% — the headroom IS the mechanism");
  the labeled `Why:` slot is for reasoning separable from the decision.
  Either way the reasoning must be PRESENT — an entry whose ruling cannot
  be re-derived is incomplete.
- **The reverse razor**: operator and session workflow (who pushes, which
  session holds write authority, personal deploy habits) stays OUT of the
  register even when it is a real standing rule — it is not true for a
  stranger cloning the repo, and recording it here would fill the register
  with operator trivia. Working state lives in the operator's own notes.

---

## Product thesis

### D-000 · one thing, very very well
Ruling (user, 2026-07-31): orgtree is a simple idea — a persistent visual
organization of Claude agents — refined meticulously to a mirror sheen, not a
feature jamboree. Bias effort toward polishing existing surfaces over adding
subsystems; measure every addition against "does this deepen the one idea, or
dilute it?"

Companion motto (user, 2026-07-31): permit *as much as possible*; close wide
gaps with minimal-friction shortcuts; the tree is a **sandbox of
capabilities, not a cage**. Hard refusals are reserved for real resource
limits the user set (credits, kiosk caps, the disk cap), true impossibilities
(unrecoverable sessions), and the safety of others' data. When a legal
sequence of actions reaches an end state a wall blocks, offer to perform the
sequence as one action — see D-003 for the one precondition that governs
automating it.

### D-006 · the union that defines the mission
Ruling (PLAN, 2026-07-28): orgtree is the union of two things that each lack
half — flat peer messaging (no hierarchy, budget, or UI) and Claude Code
subagents (real hierarchy, but ephemeral and unopenable). Every feature must
serve delegation hierarchy PLUS persistence, addressability, and
observability together. Companion: an agent escalates to its PARENT, not the
human, for anything the parent may decide — the hierarchy exists to protect
the user's attention, and a design that routes routine decisions to the user
has broken the point of the tree.

---

## Credits & budget

### D-007 · credits are occupancy, never dollars; the seat scale is derived
Ruling (user, PLAN 2026-07-28; seat identity 2026-07-31): a credit measures
concurrent occupancy weighted by tier — RAM, not a spend cap. A live node
holds its seat plus its grant; retiring releases everything back, and
retire/rehire preserves `session_id` (paging, not destruction — see D-038).
Tokens and tool calls are free and unlimited; real dollars are tracked per
node and per org but deliberately never enforced. Seat costs are fable 10 ·
opus 5 · sonnet 3 · haiku 1, DERIVED from published API pricing (output:input
is exactly 5:1 for every current model, so the scale is not a judgment call);
sonnet is pinned at the standard 3, not the introductory 2 that expires
2026-08-31. A node's seat always equals the model it is actually running —
no path (hire, rehire, cheaper-consult rehire, reseed) may seat an agent
below its model; the cheaper-consult rehire is a model *switch* precisely so
the identity holds, and seat cost doubles as the kiosk tier-ceiling rank.
Why: a 1-credit haiku node can burn hundreds of real dollars — presenting
credits as dollars would make every number a lie; a seat cheaper than its
model is a free lunch that breaks the occupancy model.

### D-008 · the budget identity, and the user as root
Ruling (user, PLAN §4.1, 2026-07-28): for every node N,
`free(N) = grant(N) − Σ over live children C of (seat_cost(C) + grant(C))`,
never negative. A node's seat is paid by its PARENT out of the parent's
grant ("give my CEO 50" = 50 to allocate, not 45-after-tax). There is no
root node: the user IS the org root — top-levels have `parent = None`, and
the `@user` sentinel has infinite free and unconditional authority.
Why: a real root node would need its own grant and would make the user just
another agent.
Load-bearing: `seat_cost`, `committed`, `free`, `descendants` are DERIVED
and never stored — a stored derived value is a second source of truth that
silently drifts. `audit()` is the global overdraft check, and the UI's
ledger self-audit chip exists solely to shout when it breaks. ("Live" here
is budget semantics — see ARCHITECTURE §Ledger.)

### D-009 · credit shortfalls bubble up the chain
Ruling (user, generalized §4.6, 2026-07-31): when an action under a payer
costs more than the payer's free, the shortfall bubbles UP hop by hop
(`_chain_acquire`) — each ancestor contributes, grants below a contributing
hop inflate so the credits are spendable at the payer, and the op is refused
only when the WHOLE chain up to and including the actor lacks it. User
actions top out at an infinite pool. Both cascades are per-org settings
(`cascade_hire`, `cascade_alloc`), ON by default; an off-mode refusal must
name the setting. Any new op that spends credits routes through
`_chain_acquire`, never a bare `free()` check.
Why: a superior wanting a hire five levels down should not hand-walk credits
down first; because the cascade exists, a superior's free is not a real
ceiling and the UI must not pretend otherwise.
Was. PLAN's precondition `free(actor) ≥ seat_cost + grant`, credits
cascading *down* from the actor.

### D-010 · moves are budget-neutral and cannot strand — but they CAN refuse
Ruling (user, PLAN §4.5 corrected 2026-07-29; bounds sharpened 2026-08-04):
promote/demote/move release credits up to the LCA and acquire back down, hop
by hop, so EVERY node's free is unchanged — a move never strands and never
needs credits of its own. The only move-specific warning is for moving an
ARCHIVED node, whose rehire cost changes payer. Guards: `new_parent` is
never inside the moved subtree NOR its lineage stacks (a bearer with
children of its own could host a real 2-cycle otherwise); a lineage bearer
is never moved alone (the stack shares its successor's slot); the root
cannot move.
Why: without neutrality a fully-occupied tree would be frozen in shape —
promotion impossible exactly when most needed.
Bounds: "cannot fail on credits" is not "cannot fail". A move refuses when
it would push a TOP-LEVEL grant past `max_top_grant` on the acquire leg
(D-014 is categorical — the cap binds every route to the same end state),
when the moved subtree's deepest leaf would cross `max_depth` or the target
would cross `max_children` (runaway insurance binds reorganization too —
user ruling 2026-08-04), and when the release leg would drive a grant
negative (a corrupted chain is refused, never subtracted into).
Was. until 2026-08-04 none of those bounds existed: a canvas drag across
roots inflated a top-level grant past the cap unchecked (the one confirmed
finding of the shelved ledger review), a drag could out-run the depth and
children caps that hire enforced, and one reseed shape produced grants of
−7/−13.

### D-011 · stranding warns, never blocks
Ruling (user, PLAN §4.4, 2026-07-28): reclaiming credits may leave a manager
unable to afford rehiring its own archived staff — that follows from
occupancy semantics and is NOT a bug. The op proceeds; the actor is warned
naming the SPECIFIC nodes (not a count) and the rehire cost, distinguishing
stranded predecessors from stranded reports. Warn exactly when an op's free
reduction crosses an archived dependent's rehire cost; every free-reducing
op passes through `_stranding_warnings`.
Why: blocking would let a long-archived agent veto present work; silence is
worse — the failure surfaces later as an unexplained "cannot afford".

### D-083 · structural caps are runaway insurance at 1024, and bind moves too
Ruling (user, 2026-08-04): `max_depth` and `max_children` exist ONLY to stop
infinite recursion from a bug that spawns unlimited subagents — "no need to
have any practical limit" beyond that. Both default to 1024 (per-org
overridable), and both bind REORGANIZATION as well as hire: a move measures
the moved subtree's deepest leaf, since that is what actually ends up
deepest.
Was. 10 and 256 — low enough to be felt as design constraints, and enforced
on hire only, so a drag could re-shape a tree past the limit a hire had
already been refused.

### D-012 · model-switch economics
Ruling (№16): a node's model can swap mid-life and the SESSION SURVIVES
(`--resume` honors a changed `--model`). Cheaper: the seat difference melts
into the node's own grant — total holding and the parent's commitment never
move, so a downgrade is never a stealth credit transfer between levels.
Pricier: paid from the node's own free first; only the shortfall bubbles up
the chain. Agents may switch models anywhere in their SUBTREE but never
their own; the user switches anyone.

### D-013 · the fable weekly-limit policy
Ruling (user, 2026-07-31): when the Fable limit is exhausted, the org's
`fable_limit_policy` decides — `halt` (DEFAULT: fable agents visibly halt
and hold their seats), `opus` (every fable seat converts 10→5, freed credits
return to each parent), or `dissolve` (each fable subtree retired). Agent
fable hire/rehire during the lock is NOT hard-blocked — permitted, merely
futile, and the warning says so. A USER fable hire/rehire/switch IS the
decree: it clears the lock.
Why: halting keeps the model choice with the humans/superiors who made it;
the soft gate follows the motto (tell the truth, don't refuse).
Was. a fourth policy `retire` was considered and DROPPED as too destructive;
old docs carrying it migrate to `halt` on load.

### D-084 · a model version is a subcategory of the tier, never a fifth tier
Ruling (user, 2026-08-04): the four chips are the four TIERS — price bands.
Individual model versions (Opus 5 vs Opus 4.8) are a subcategory selectable
only in the node gear, and choosing one touches nothing the budget or the
kiosk ceiling inspects: it decides which `--model` id the CLI is handed, and
nothing else. The choice is stored in the node scope and re-validated
against the CURRENT tier on every read, so a tier switch can never drag a
stale version with it; an unknown value falls back to the tier default
silently (a bad string in a doc must never stop a turn).
Was. the first build made Opus 4.8 a fifth TIER — a fifth chip on the canvas
and a fifth price band in every table; corrected the same day. Also: new
tiers/versions now REACH EXISTING ORGS (the per-org tier table is migrated
add-only at load; Org.create's frozen copy used to strand old orgs on the
shipped set forever).

### D-014 · hire chips cascade; which credit ceilings actually bind
Ruling (user, §4.6 cascade + drag order 54e5e19): the H/S/O/F hire chips are
never disabled by the node's own free credits — a user hire cascades up the
chain. Only a kiosk's remaining credit cap may disable them, and a kiosk
tier cap removes higher tiers entirely rather than greying them. A disabled
chip's tooltip states the remedy, not just the number. Drag/slider ceilings
resolve in order: kiosk hard cap → parent's free (only when the relevant
cascade toggle is OFF; the ghost outline draws only then) → `max_top_grant`.
Load-bearing (ruled by the user, 2026-08-01): `max_top_grant` is a REAL
ledger precondition — no op, user-actor cascades included, may push a
top-level grant past it; the refusal names the setting so raising it is
one step away. Server-side ceilings are therefore: kiosk hard cap →
`max_top_grant` on top-level grants → the parent's free when the relevant
cascade is off.
Was. until 2026-08-01 `max_top_grant` was a UI slider/drag bound ONLY — no
ledger precondition read it, and user-actor cascades inflated top-level
grants straight past it.
Was. ① "credit-bar drags are unbounded … the only remaining limit is kiosk
mode" (true for exactly one commit); ② ui-guide's "a live bar caps at grant
+ the parent's free" — pre-dates the cascade toggles.

---

## Authority & the public surface

### D-001 · kiosk visitors act as @user — the ceiling is the only wall
Ruling (user, 2026-08-01, made with the irreversibility stated and confirmed
twice — once in each session): a kiosk visitor holds full `@user` authority
inside their org, **permanent agent deletion included**. The kiosk ceiling
(credits cap, spend cap, disk cap, permission ceiling, tier cap) is the only
wall; no operation class is carved out. A kiosk org is disposable by design.

Why: consistent with the ceiling ruling as literally written ("within that
ceiling every permission operation is open to everyone, visitors included")
and with the sandbox-not-cage motto. Destruction inside a disposable org is
an acceptable visitor power; the things worth protecting (spend, storage,
other orgs, the host) are protected by the caps and the gateway scope, not
by op filtering.

The fact that makes this coherent — and the single most counterintuitive
fact in the system: **`actor_kind()` classifies org ROLE, not
authentication.** "USER-only" in the ledger means "the org-root role", not
"the human at 127.0.0.1". `Op.actor` is caller-asserted and defaults to
`@user`; the *authentication* boundary is the public gateway's path+verb
denylist (`_public_denied`) plus the visitor-less `raise_ceiling` capability,
and nothing else. Agents remain unable to delete (no `orgtree_delete` tool;
`actor_kind` gate) — agents top out at retire-and-ask; visitors ARE the
asking party.

Load-bearing: **cost-is-history** (665affd) — a deleted subtree's burn banks
into the org's `deleted_cost_usd` tombstone and `Org.cost_total()` is the
only permitted total, so delete-and-rehire cannot walk an enforced spend cap
backwards. Do not "simplify" the tombstone; this ruling leans on it.

Was. For ~25 minutes on 2026-08-01 (`2c5af3e`), visitor `op=delete` returned
403 as an explicitly-interim gate shipped while this ruling was pending and
a live external visitor was connected — closing an open question temporarily,
not pre-empting it. Reverted in the same commit that added this entry.

### D-002 · three listeners, three trust levels
Ruling (established in code; ratified as the doctrine 2026-08-01): the admin
app binds 127.0.0.1 and trusts every local process fully (no auth — locality
IS the credential); the public gateway (0.0.0.0) serves only `/k/<token>/…`,
scoped to the token's org through the denylist matrix; the bridge listener
(0.0.0.0) serves only the agent gateway, gated by the per-org secret.
Was. PLAN §1's non-goal "Multi-machine / remote nodes. Single desktop,
single user" and the §7.7/§11 "there is no adversary" premise. The literal
multi-machine clause still holds (all nodes run on one desktop), and §11.3's
"no security boundary *between nodes*" stays true at node level (D-029) —
what died is the single-user premise, the day kiosk mode admitted outside
visitors (2026-07-30). Per D-005, PLAN is not amended; this register carries
the supersession.

### D-087 · off-loopback admin is an env-var wall, never a setting
Ruling (user, 2026-08-03 argv-only; superseded 2026-08-04 to the env var):
`ORGTREE_EXPOSE_ADMIN` binds the admin listener to 0.0.0.0, printed as a
74-column warning wall at startup. It is an environment variable because
service definitions (Task Scheduler, systemd) set environment naturally —
and it is stripped from every agent's env (clean_env), because whether the
host is exposed is not an agent's business. It is deliberately NOT an org
setting: anything that can write the doc — including an agent — could flip
a setting. No auth was added by ruling; the user accepted the risk.
Was. D-39 ruled it argv-only ("env vars get inherited and copied between
machines"); the user reversed the mechanism one day later for the
unattended-host case, and the inheritance objection is handled by the strip
rather than dismissed.

### D-164 · robustness priorities: @net is the robust path; pins, not fixes
Ruling (user, 2026-08-06, batch): ① gaps in the @mcp:/@org: transports are
NOT critical while they don't obstruct immediate usage — every robust
orgtree installation stands up a local mail hub, @net is the preferred
path, and @org:/@mcp: are quick shortcuts for when a hub does not properly
exist. (Deployment philosophy, not a resolution-order change: bare-name
resolution keeps the ruled near-tier-first order.) ② The unbounded live
queues (notices/mail pools, redteam-measured triangular growth) stay
unbounded — PINNED as a known notable pain point to be aware of if it ever
bites. ③ Org.children's O(n²) is ignorable until tree()'s typical
execution exceeds ONE SECOND (self-announcing: api.py warns once per org
past the threshold); if raw performance ever truly matters, the answer is
a Rust rewrite, not incremental shaving. ④ Remote control stays
experimental/partially implemented; the working pattern for now is an
external ordinary Claude Code chat (where remote control is known-good)
acting as liaison to org chats over the mailserver or other transports.
⑤ Docket statuses: FR-13 (scope requests) and FR-14 (mixed-kind batch
cards) are HELD-BUT-WILL-APPROACH when implementer capacity frees; FR-02
(mobile) and FR-15 (external providers) are backlogged indefinitely.
Renumbered 2026-08-26 (was D-103): that number was carried by two entries at
once. "an agent withdraws its own question when it stops mattering" is the
one the code cites, so it kept D-103; this ruling moved and is otherwise
unchanged. See D-158.

### D-163 · self-update: unrestricted for user-authority agents; the remote is the trust root; Linux is first-class
Ruling (user, 2026-08-06, closing the two FR-14 questions): ① an agent
with direct user authority (top-level or held user audience — the existing
gate) runs the restart path WITHOUT further restriction or warning. The
cross-org blast radius is accepted as-is: every org auto-resumes on
startup, so the cost is bounded at some mid-turn progress (a command run,
some thinking). No cross-org consent gate, no busy-refusal. (The 5-minute
one-launch-at-a-time guard stays — it serializes concurrent git pulls, an
operational interlock rather than a permission.) ② The trust assumption is
VALIDATED and acceptable as stated: the agent does not choose the code,
the tracked remote does, and only the user pushes there. ③ Self-update
must also function on LINUX — update.sh mirrors update.ps1 step for step
(venv, esbuild self-heal, stale-pid restart check), since Linux is where
orgtree is installed in plenty of locations.
Renumbered 2026-08-26 (was D-102): that number was carried by two entries at
once. "agents set their reports' permission mode, capped at their own" is
the one the code cites, so it kept D-102; this ruling moved and is otherwise
unchanged. See D-158.

### D-162 · mailserver ports stay exactly as they are (7370 open, no migration)
Ruling (user, 2026-08-06, two parts, second direct): ① leave nova-desk's
7370 open on the LAN; do NOT flip HUB_BIND to loopback. ② "nobody should
change their default ports for the mailserver, keep everything as-is" —
the remote org's planned :7378 migration is CALLED OFF (they were
notified), the compose default stays `${HUB_BIND:-0.0.0.0}`, and no
client is asked to move off 7370. An informed acceptance of the trust
model (hub reachability = read access to all mail) on this network, not
an oversight — neoja's safe-by-default argument (silent exposure,
indistinguishable from working state) was put to the user before part ②.
The machinery stays available but idle: HUB_BIND knob (c8aa65e),
HUB_PUBLIC=1 live on nova-desk with the API-only 7378 listener answering
— any future close is one .env line + a client port edit, no code.
Was. Part ① alone read as a hold pending the remote's 7378 migration;
part ② closed the migration itself.
Renumbered 2026-08-26 (was D-101): that number was carried by two entries at
once. "permission mode is editable after creation, at both levels" is the
one the code cites, so it kept D-101; this ruling moved and is otherwise
unchanged. See D-158.

### D-100 · presenting a document needs a DIRECT user audience
Ruling (user, 2026-08-05, on the redteam's FR-03 finding that
`present_document` bypassed the org chart): document presentation to the
user is allowed ONLY for agents with a direct user audience — top-level
(directly subordinate to the user) or holding a user-audience grant. All
other agents are REFUSED outright — no auto-bridge. This is a deliberate
asymmetry with ask_user (D-090), which ROUTES an ungated agent's question
to its superior: a question needs an answer from somewhere, but a
document is a standing claim on the user's screen, and the refusal tells
the agent to hand it up the chain itself (orgtree_message, or ask for a
grant). Exception carved by the same wave, no ruling needed: headless
orgs refuse presents for ask_user's §9.6 ② reason (the reader IS the UI),
and the newest-10 prune logs `present_evicted` + names evicted cards in
the presenter's result (redteam gaps 2 and 3, same report).
Was. FR-03 shipped (6e230c7) with only the liveness gate — every agent at
every depth could put a card on the user's screen; the redteam measured
it and declined to pick a direction; the user picked refusal over the
docket's suggested routing bridge.

### D-099 · independent chats are first-class hub clients (FR-06)
Ruling (user, 2026-08-05 — an explicit REVERSAL of the spec §12 line
"strictly org-to-org; the hub does not relay @ext:/@mcp:"): any Claude
Code chat may join the mail hub directly, correspond with orgs AND other
chats symmetrically, and appear on the roster tagged `kind: chat`.
Identity (user's scheme): the client's UID — minted once per user profile
(~/.orgtree/hub-client.json) — is the secret (hub stores sha256(uid));
on FIRST registration the chat must CHOOSE a NAME, persisted and
immutable thereafter (the fingerprint suffix rides the address, same
immutability rule as orgs): `<name>.<username>.<fp[:6]>`. The client is
`hub/hubtool.py` — a stdlib MCP server (`claude mcp add mailhub -- python
hub/hubtool.py`: hub_register/list/send/read/wait) plus a Monitor-armable
`listen` mode emitting one line per inbound mail — the chatq delivery
shape over the hub, which is what makes this a candidate END-TO-END CHATQ
REPLACEMENT (user's framing; migration is its own future decision). The
dial-out security model holds: the chat polls the hub, nothing reaches
in. Orgs address a chat as @net:<slug> like any peer; rosters and the
compose list label chats.

### D-098 · extern mail is audience-gated: holders only, bootstrap, auto-bridge
Ruling (user, 2026-08-05, F-06 phase C0 — ALL outside namespaces
@ext:/@org:/@mcp:/@net:): inbound extern mail wakes ORG-INBOX AUDIENCE
HOLDERS only, never the whole top row, and every holder is driven (holding
the audience means you handle inbound). Zero holders + a live top-level ⇒
the bootstrap auto-grants the LEFTMOST live top-level (canvas order),
notifies it, and delivers in the same call; the re-trigger is implicit
when the last holder goes. Zero live top-levels ⇒ the user-inbox rescue,
unchanged. Outbound is holder-only with the cross-gaps auto-bridge: a
top-level non-holder's send self-grants and SUCCEEDS with a warning; a
deep non-holder is refused with both remedies named. Grants: a top-level
grants itself or its subtree directly; revoke = subtree for top-levels,
self-revoke for any holder, the user anywhere. UI: drag an agent onto the
mailbox node to grant; holders list + revoke in the mailbox modal; the
USER composes extern mail from that modal (bypasses the gate — they
outrank it — grants nothing; attachments refused for the text-only
@ext:/@mcp: transports). Kiosks stay sealed; incidental fix: the sealed-
kiosk attachment-copy leak closed because kiosks cannot hold the audience.

### D-097 · the F-06 mailserver wave: rulings beyond the spec
The spec's ruling table (docs/mailserver-spec.md §12) is normative for the
hub design — identity self-issued (sha256 fingerprint slug, immutable),
joining open on a closed network, one multiplexed long poll, at-least-once
+ acks, received_at as ordering authority, received+read receipts, global
hub UI, no broadcasts/rotation, auto-connect LOCAL only. Session rulings
on top (user, 2026-08-05): scope = EVERYTHING AT ONCE incl. §9 autonomy;
attachments FULL in v1 (25 MB/10-file, hub blobs, agent sends @net:-only);
hub UI = full read-only mail view; runaway GUARDS = NONE for now (no rate
limits/wake caps/loop breaker/allowlist — seams only); hubs have a NAME
the client DISCOVERS on connect (only the address is typed); mailserver
connection is a third visibility trigger for the mailbox node — but a
NEVER-ANSWERED implicit local hub shows NO ui at all (hidden until
registered_at exists; the daemon keeps dialling quietly, backed off;
explicit typed remotes always render, offline included); headless REQUIRES
an API key both directions, forces auto_resume, refuses halt policies, and
draws every eye GREY and EMPTY (outline, no iris/pupil); clean_env STRIPS
a host-level ANTHROPIC_API_KEY/AUTH_TOKEN (billing is the per-org
selector's decision, never an inherited env var); a rename destination
directory occupied by a DELETED agent's leftovers moves aside as
.orphan-<ts> and the rename proceeds (an occupant is an orphan by
construction — the taken-name check ran first). Accepted design note: the
seen-ring's far edge is a bounded redelivery window (at-least-once + a
500-id ring; the alternative is a per-hub high-water mark) — kept as the
one open finding in test_net_transport.py.

### D-096 · rename is full identity, and the old name BOUNCES
Ruling (user, 2026-08-05): the user, the superior, or any ancestor —
never the agent itself — may rename an agent. Rename is FULL identity:
the id re-keys everywhere (lineage generations, pointers, audiences,
mailbox and per-node records, open asks), the scratch dir and the CLI
project dir move with it (resume is project-scoped — without the move
the agent loses its session), and it is refused mid-turn. Historical
mail/archives/the event log keep the old name — warn, don't rewrite.
Mail addressed to the old name BOUNCES; no alias auto-bridge (user: a
same-named successor would silently inherit redirected mail).
The ask form mirrors Claude Code's AskUserQuestion exactly (same-day
ruling from a side-by-side): header tab + ✕ (dismiss = a real verb,
nulled grey, agent told), option rows {label, description} radio/
checkbox, Other with free text, submit bar; asks ride the user inbox as
their OWN mail rows with the response UI as the body; the credit card is
the same form family with the agent's REAL bar (org scale, seat + child
slabs, rungs) whose height is the staged offer.

### D-095 · moves never touch a user audience — promotion leaves it dormant
Ruling (user, 2026-08-05): an agent holding a user audience KEEPS it when
dragged to top level, and keeps it back down. An audience is an explicit,
durable channel grant (no expiry, D-'no audience expiry'), removed only by
deliberate revocation; moves shrink parent-bounded capabilities (dirs,
tools, visibility — the ⊆ invariant), and a user audience is not
parent-bounded — the grantor is the user, not the chain. While the agent
is top-level the grant is dormant (top-level user access is intrinsic)
and has no visible handle (the switchboard tab shows no ✕ on intrinsic
lines); it resurfaces with its direct line if the agent is ever demoted.
That resurfacing is deliberate, not a bug — do not "fix" it.

### D-090 · asks always park; answered anywhere, nulled everywhere
Ruling (user, 2026-08-04, the F-04/F-05 redesign): an agent's question to
the user NEVER blocks a turn slot — orgtree_ask parks it and the agent
ends its turn. The ask renders as ONE interactive card in TWO places (the
agent's desk, the user's inbox); answering either sends the answer as
ordinary user mail (the mail drives the turn) and nulls the card in both.
Any OTHER mail waking the agent first voids the ask everywhere; the agent
is told in that turn and must re-ask. Nulled cards stay visible wearing
their reason — grey answered/denied, orange interrupted. Gate = the
user-mail gate; an agent without it has the question ROUTED to its
superior as mail, never refused (the motto). Kiosk visitors are always
askable. Answer shape mirrors AskUserQuestion (2-4 options, multi, free
text). Re-asking amends the open ask (the ratified idempotent pattern);
answering marks BEFORE the mail posts, under the doc lock, so the
answer's own turn can never void its question.

### D-091 · credit asks: full-range counter-offers, zero headroom refuses outright
Ruling (user, 2026-08-04): the user answers a credit request by setting
ANY legal amount — below the ask, above it, or below the current grant
down to the committed floor (reallocate's own invariant; a clawback of
unused credits). Outcome wording is honest: a partial grant is a
COUNTER-OFFER, not an "APPROVED", and always says the agent may re-ask
or route around it (the matter stays the agent's to continue). Stranding
warnings surface via a dry-run BEFORE the commit. If there is genuinely
ZERO headroom (max_top_grant reached, or the kiosk pool fully held) the
request is refused OUTRIGHT at ask time with no card — a card the user
could only refuse would be a lie. Credit asks void on wake exactly like
questions (one system, D-090).

### D-092 · attention glow means exactly one thing
Ruling (user, 2026-08-04): the bright terracotta aura is REPURPOSED —
it marks an agent with an un-nulled ask, and nothing else glows. The
user-audience aura is diminished to the same soft steel as any audience;
the eye's unread-mail glow is removed (the count badge stays); a second
inbox icon in the page header glows — alone in the chrome — iff an ask
is open, and opens the inbox.
Was. bright terracotta = "holds the user's ear"; the eye pulsed on any
unread mail — attention markers that fired on capabilities and routine
mail, so the one that mattered had no channel left.

### D-093 · the turn bound is an idle watchdog; failures are durable
Ruling (user, 2026-08-04): ORGTREE_TURN_IDLE (600 s, zero CLI events)
is what kills a turn — "wedged", not "long-running"; the wall-clock
ORGTREE_TURN_TIMEOUT rises to 14400 s and is a per-message backstop. A
killed turn records {killed, toks} in the turn ring with a cost estimated
from the node's own $/output-token history (self-calibrating — no pricing
table to rot; honest zero without history). Turn failures append to a
durable per-node ring (turn_error_log) that read_chat interleaves as a ⚠
system row in chronological place; the in-memory banner then clears at
the NEXT turn's start (D-50 one level up: the durable row is the
replacement in hand).
Was. one 1800 s wall clock killed a productive 40-tool-call turn exactly
like a hung one, the spend of a killed turn was never charged, and the
failure banner lived only in memory — forever on an agent never messaged
again.

### D-094 · one advanced modal, two doors; born-with facts lock
Ruling (user, 2026-08-04, F-07): the create form's advanced disclosure
and the ⚙ settings panel open the SAME modal. Creation-only facts
(kiosk, sandbox, disk type) render as locked chips outside creation —
visible, never editable. The create form keeps its collapsed summary
line; the settings panel keeps its ONE bottom save (the modal saves
nothing itself — three save surfaces already failed once, 2026-08-01).

### D-089 · a failed read must never arm a destructive write
Ruling (review, 2026-08-04, from the org.md near-wipe): SettingsPanel's
catch turned a failed GET of org.md into an EMPTY EDITABLE buffer, so a
network blip plus one ordinary save wiped the charter with put('').
The rule generalizes: optimistic/editable state seeded from a fetch keeps
its "not loaded" sentinel (null → disabled control → save skips the field)
on ANY failure path, and resets to the sentinel on identity change (org
switch), so stale content can't be written under a new key. Sibling rules
from the same review pass: an optimistic control must also hear a
same-value answer (EffortButton's bounded settle — a 200 that changes
nothing never fires an on-change effect), and an optimistic hide must roll
back when its write rejects (InboxView retract).

### D-088 · one backend per data root, enforced by an OS lock
Ruling (measured, 2026-08-04): two backends on one ORGTREE_DATA silently
lose 32–74% of completed writes (zero errors — every writer is told it
succeeded). The rule the architecture stated is now enforced at startup by
a kernel file lock (`store.claim_data_root`): no PID file, no staleness
heuristic (a mtime-based steal was reproduced overlapping critical sections
against a merely-slow holder), released by the OS however the process dies.

### D-015 · authority is downward, transitive, unconditional
Ruling (user, PLAN §7.1, 2026-07-28): an agent holds full authority over its
ENTIRE subtree at any depth — message, hire, retire, rehire, promote,
demote, dissolve — not just direct reports. Authority is strictly downward:
no node acts on a peer or ancestor; `_require_authority` admits only
`@user`, `@system`, or a strict ancestor.
Why: a superior should never have to threaten dissolution to get its way,
and a manager must never be structurally powerless over its own org.

### D-016 · addressing is deliberately wider than authority
Ruling (user, PLAN §7.2/7.3/7.5, 2026-07-28): an agent may message downward
at ANY depth, exactly ONE hop up, sideways to live siblings, and anywhere it
holds an audience; everything else is refused WITH THE CORRECT ROUTE NAMED.
Messaging a non-child descendant implicitly and instantly grants the
recipient a reply audience. Writing to the user's inbox unbidden is
restricted to top-level agents and user-audience holders. The user addresses
anyone.
Why: the hierarchy exists to protect upward attention — deep reach without a
reply path would be a one-way megaphone; unbounded upward channels mean
unbounded interruption of the human. Siblings always talk directly, so a
flat org is a complete message graph.

### D-017 · reading is downward only; visibility is knowledge, not permission
Ruling (user, PLAN §6, 2026-07-29): reading (scratch, inbox, transcript) is
strictly own + descendants at any depth. The org-structure visibility
setting (self/team/subtree/full, default FULL — "lean toward visibility, not
opaque invisibility") changes KNOWLEDGE of the chart only; it never widens
read or message reach. Scratch dirs stay FLAT, never nested to mirror the
tree.
Why: the upward-read ban is not about protecting the boss — a superior's
transcript contains its conversations with all its reports, so reading up
would leak siblings transitively. Flat scratch exists because promote/demote
must never move a live session's cwd.
Was. `org_visibility` default was `team`.

### D-018 · names are identity; actor kinds are typed
Ruling (user, 2026-07-28; actor typing 8ac86f9): every agent has a mandatory
human-readable one-or-two-word name supplied at hire; the slugified name IS
the node id; collisions get a numeric suffix. Node identity is the NAME,
never the session UUID — anything keyed on the UUID breaks when a node is
re-created rather than resumed. Non-agent actors are @-prefixed sentinels
(`@user`/`@system`/`@extern`) that `slugify()` can never produce — so agent
names are fully unrestricted (a node may legally be named "user"; names win
in recipient resolution) and authority is NEVER decided by comparing a bare
name string.
Why: an org of agent-3 and agent-11 is unreadable at exactly the depth where
the tree becomes worth having; reserving names would be an arbitrary
restriction, so the kind is typed instead ("reserved names are a hack").

### D-019 · top level is a privileged class
Ruling (established in code; enforcement fixed by audit 2026-07-31): only
`@user` hires at top level and only `@user` promotes TO top level. Top seats
carry privileges no other seat has: unbidden mail to the user, speaking as
the org to outsiders, being an extern recipient. (Per D-001, "user" is the
org-root ROLE — kiosk visitors included.)
Why: seating an org voice is a root-role act. Audit lesson worth keeping:
promote() *documented* this restriction while silently allowing it — treat
any privilege stated only in a docstring as unenforced until proven.

### D-020 · delete erases the record, never the transcript
Ruling (user, 2026-07-31): delete takes the whole subtree plus every lineage
stack and erases records, mail, notices, audiences, audience requests and
pending credit requests — but session transcripts on disk are NOT touched.
(Who may delete: D-001.)
Why: hard stops protect the user's data; transcripts are that data. Pending
credit requests are swept because a freed slug can be re-minted by a later
hire and a stale approval would re-bind to the namesake.

### D-021 · capabilities are sets that only shrink downward
Ruling (user, PLAN №30 + ceiling spec 2026-07-31): directory/tool grants are
an INHERITED CAPABILITY SET, not a budget — nothing is conserved, a node
holds only what its parent holds (paths AND modes; read-only can never beget
read/write), revoke cascades into the subtree, re-parenting intersects the
moved subtree with the new chain; only top-levels may hold arbitrary
user-granted paths. The clamp is STRICT (raises) at grant time and LENIENT
(drops/downgrades with a named warning) at every revalidation — rehire,
move, scope shrink. Both the parent clamp and the kiosk-ceiling clamp run IN
THE LEDGER, never only in the gateway or UI: agents reach the same ops over
the loopback gateway, so an HTTP-layer check is bypassable (that hole
shipped once). The MCP wildcard `"*"` means every registered server, present
AND future; under a list ceiling the effective set MATERIALIZES into that
list, and the warning must say so because future registry additions stop
auto-flowing.
Bounds (ruled by the user, 2026-08-01): `org_visibility` JOINS the parent
clamp — child ≤ parent, mirroring the tools pattern, with a subtree sweep
when a manager's visibility is lowered. `permission_mode` JOINS it too
(user, 2026-08-07) — see D-102 for the delegation rule and the two
exceptions the sweep has to make for it.
Was. until 2026-08-01 the clamp covered `add_dirs` and `tools` only — a
vis="self" manager could hire a vis="full" report (live-verified), while
the agent-facing docs promised the shrink-only rule.

### D-022 · agent hires have no defaults
Ruling (user, 2026-07-30): org hire defaults apply to USER hires only. An
agent hiring through `orgtree_hire` must state everything explicitly —
add_dirs with modes (`[]` is valid), all four tool switches, the MCP list,
visibility, grant — plus a CHARTER, or the hire is refused listing exactly
what is missing.
Why: a default is a decision the hirer did not make; when an agent spends
the org's credits and hands over capabilities, the decision must be
conscious and legible. (Kiosk visitors DO get defaults — a visitor hire is a
root-role hire and the ceiling clamps it with the same machinery.)

### D-023 · audiences: down fast, up slow, anchored, paging-proof
Ruling (user, PLAN §7 + delegation 7bf682b, 2026-07-30): audience grants
flow DOWN instantly (deep messaging implies a reply grant; the grantor
rescinds unilaterally); audience REQUESTS climb UP one hop at a time — each
superior may decline or simply handle it. Every grant is ANCHORED: a
self-grant on its grantor, a delegated grant on the DELEGATOR, and the
re-parent sweep drops any grant whose anchor no longer commands the grantee.
User audiences are never swept. Audiences survive retire and dissolve
(retire is paging) and return live on rehire; only delete destroys them.
No idle expiry (user, 2026-08-01): revocation stays lifecycle-only — the
affordance for the scarce human is VISIBILITY of current user-audience
holders with one-click rescind, never a timer.
Why: attention is cheap to give and expensive to demand; an un-swept grant
would be a back-channel between unrelated branches — the exact thing
addressing forbids.
Was. PLAN §7.3 anchored delegated grants on the grantor; superseded by the
delegator-anchored rule (7bf682b).

### D-024 · kiosk is a creation-time type, sealed, controls in its own settings
Ruling (user, 2026-07-30/31): kiosk is a TYPE decided at org creation (name
+ caps + sandbox secret + token minted in one step) — never converted into
or out of; `POST /kiosk` 422s on a non-kiosk. Its caps bind whether or not
the public URL is enabled (`enabled` gates only the token gateway, and
`kiosk_cfg` must never key off it). Kiosk orgs are SEALED from the outside
mail world in both directions, and a sealed kiosk must be INDISTINGUISHABLE
from a nonexistent org to every outside caller — same 404, same text, on
every path in the family. Per-kiosk caps, share URL (copy/rotate), and URL
pause/reactivate live in the kiosk org's OWN settings panel.
Why: a convertible kiosk would change guarantees under agents already
running inside it; a public-facing org must not be a relay to arbitrary
outside parties; a 403/404 split let an outside peer enumerate the roster.
Was. an all-kiosks dashboard on the org list carried every kiosk's controls
— retired 2026-07-31 (ba33b08); docs describing it are stale.

### D-025 · the kiosk ceiling clamps; the kiosk budget caps refuse
Ruling (user, consensus spec 2026-07-31): the permission ceiling
(`kiosk.max_scope`) clamps over-ask requests WITH A NAMED WARNING, never a
403 — effective grant = parent ∩ ceiling, applied AFTER org defaults
resolve; lowering the ceiling SWEEPS every stored scope to fit and notifies
affected agents (unique end state ⊢ D-003). Normal orgs have no ceiling —
the top-level agent's own layer already bounds its subtree. The two BUDGET
caps refuse instead: the tier cap is a hard refusal for EVERY actor
including the user, with deliberately no raise bridge (a cost cap must never
rise as a side effect of a hire) and no sweep on lowering (over-cap agents
stay, named in a warning — downgrading live agents moves seats; the admin
chooses per agent). The credit cap is ONE invariant — no op may push total
top-level holdings past `kiosk.credits` — checked before save, covering
hires, cascades, rehires, reallocations, approvals and admin actions alike;
it binds the ADMIN too and can never be set below current holdings.
Why: clamping keeps visitors productive without an admin in the loop; one
invariant beats N per-operation checks that drift; the admin is not exempt
because the admin can simply raise the cap.

### D-026 · raise_ceiling is a capability, not an identity
Ruling (user, 2026-07-31): `raise_ceiling` is a per-request boolean
CAPABILITY conferred by the admin gateway (not-public AND (auto_raise OR the
explicit ask)), fail-closed; agents and visitors can NEVER pass it, even
with `auto_raise` on. An honored raise grows the ceiling to the union of
itself and the request, is logged as a `ceiling_raise` event and named in a
warning — a ceiling never rises silently. When not passed, the response
carries a one-action `bridge` offer, which the API strips for visitors and
agents so no dangling offer exists.
Why: an `admin: bool` request field was rejected as an attractive nuisance —
anything an agent can put in a body it will eventually forge; an escalation
path an agent can walk itself is not a ceiling.

### D-027 · public payloads are scrubbed; safe fields ride outside
Ruling (user, №18, 2026-07-31): everything served through the public kiosk
gateway is scrubbed — basename-only paths, no operator username, no session
ids, regex-scrubbed errors, no share_url, no max_scope (it carries host
paths). Public-safe fields are placed OUTSIDE the scrubbed structures
deliberately (e.g. `kiosk.max_tier` rides the tree because a tier name is
safe and the visitor UI needs it).
Why: the admin app stays on loopback; the public listener is the only
outside surface, so leakage there is the whole exposure.
Load-bearing: the gateway is a denylist — every new org-scoped route is
visitor-reachable by default and must be triaged against the scrub before it
can leak (ARCHITECTURE §Public surface).

### D-028 · every boundary path check is canonical and anchored
Ruling (fix batch, 2026-07-31): every path check that bounds an agent or
visitor must be canonicalized (realpath, symlinks resolved) and
separator-anchored — a bare `startswith(base)` admits a sibling directory
like `<base>-x`.
Why: the unanchored form shipped in three places; the pattern will recur in
every new path-scoped surface.

### D-029 · isolation is information architecture between nodes; the org is the security boundary
Ruling (user, 2026-07-29; scope set by the public stack 2026-07-30/31): the
read/addressing rules INSIDE an org are an information-architecture rule,
not a security rule — enforcement is real for file tools (`--add-dir`),
real-via-mediation for transcripts/inboxes (ancestry-checked MCP tools), and
leaky for Bash: tightenable, never closable, because every session runs as
the same OS user. Do not add machinery justified by a threat model between
nodes; the three real reasons are context hygiene, correctness,
predictability. The boundary that IS security is org↔outside: one Docker
container per ORG (opt-in; default-on for kiosks) with no host filesystem,
CPU/mem caps, the ext4 disk cap, and the per-org bridge secret as the only
door out; the ledger-level kiosk ceiling bounds permissions inside it.
Why: kiosk mode introduced the real adversary — the untrusted visitor
OUTSIDE the org — so the plan's node-level tier ladder answered the wrong
question. The honest summary: a node cannot read upward by accident or
through any orgtree tool, but can if it deliberately shells out.
Was. PLAN §7.7's tier ladder ("build Tier 0 now; design so Tier 2 — one UID
per node — is a swap, but do not build it"). Tier 1/2 were dropped
2026-07-30, not deferred; container-per-ORG was the unlisted option actually
built. PLAN №21/№23 (Tier-2 gating tests) are moot, not pending.

### D-030 · bypassPermissions is a grantable ceiling rank — ratified
Ruling (primary maintainer, 2026-08-01, ratifying shipped behavior; settled
unless challenged — the user may override at any time, but this entry does
not sit in §Open awaiting a review nothing would trigger):
`bypassPermissions` stands as the top `PM_LEVELS` rank,
selectable in both kiosk-ceiling dropdowns and passed to
`--permission-mode` when deliberately granted. Every default remains
`acceptEdits`, so nothing runs bypassed unless a human chose it — which is
the motto working as intended (permit, don't police a conscious choice).
Bounds: two hardening gaps ride this ruling and are fix-listed: neither
set_scope nor org creation validates `permission_mode` against `PM_LEVELS`,
and `hire()` skips `_apply_ceiling` for permission_mode in kiosks.
Was. PLAN №5's closing clause "`bypassPermissions` remains rejected"
(2026-07-29, pre-kiosk-ceiling) — superseded by the ceiling wave (ef3f9fd,
2026-07-31) without a recorded amendment until this entry.

### D-031 · an unsandboxed kiosk bounds configuration and money, not capability
Ruling (product-level, README): a visitor can make agents do anything the
fixed rights allow — so unsandboxed kiosk orgs must be given no bash and
workspace-only folders. The secret URL is itself a capability: share
deliberately, rotate freely, serve over an HTTPS tunnel so tokens are not
sniffable. The security boundary is the Docker sandbox, which is why kiosks
default it on.

---

## Agent runtime

### D-161 · the machine's global skills are granted to unsandboxed agents
Ruling (user, 2026-08-07): every UNSANDBOXED agent gets `~/.claude/skills`
read+write as a standing grant — no scope row, no per-org opt-in. Sandboxed
agents do not: the host home is not mounted, and the exclusion holds even for
a sandboxed node raised to `bypassPermissions`. Nothing may be plumbed over
the file tools to simulate the access; an agent that must WRITE a skill is
raised to `permission_mode: bypassPermissions`, which only the user can do
(the ⚙ panel and the scope API — `orgtree_retool` deliberately does not
expose it, so no agent can raise its own report).
Why: the reported bug ("agents cannot update their own skills") was a write
refusal, not a discovery failure. A write to any path carrying a `.claude`
segment hits a SENSITIVE-PATH gate ABOVE the permission system: an
`Edit(<path>/**)` allow rule, an explicit `--add-dir`, `--permission-mode
dontAsk` and a PreToolUse hook returning `permissionDecision=allow` were each
measured and each still refused — verbatim "… which is a sensitive file",
re-confirmed byte-identical after this grant shipped. Only `bypassPermissions`
clears it. So the grant is unconditional (reads work for every unsandboxed
seat) while the write stays a deliberate user act. The grant is also precisely
scoped, measured from a live seat: `~/.claude/skills` reads succeed while
`~/.claude/settings.json` still refuses.
The gate keys on the `.claude` SEGMENT and nothing else — proven symmetric on
one build by a live seat: home, a granted workspace and the agent's own cwd
each produced the identical message, while a control write into the SAME
granted folder minus the `.claude` component succeeded. Scope is irrelevant;
neither the standing `--add-dir`, nor a workspace grant, nor being the cwd
changes it. ※ It is NOT a classifier deny: the write raises a permission
REQUEST, and a headless turn has no approver to answer it. An interactive
seat answers it and the same write lands — which is why one agent's "it
works" and another's "it fails" were both true measurements, and why the
identity prompt says so in those terms rather than calling it a refusal.
Bounds: the grant is skipped when the directory does not exist — an
`--add-dir` on a missing path is not a grant. The prompt line states the gate
honestly rather than promising a capability the mode withholds (D-004's
sibling rule: never promise what the config drops).
Was. This entry originally carried a second cause: that a seat loads skills
ONLY from the home scope, because its cwd is an empty scratch dir, making a
granted workspace's `.claude/skills` "writable but never loadable". **That is
false and the identity prompt asserted it for one deploy.** An agent measured
the refutation the same day: `reso-limits` invoked from a seat whose cwd was
its scratch dir resolved to `⟨granted dir⟩/.claude/skills/reso-limits`.
Discovery reads the cwd AND every granted directory, and for most seats here
that is where nearly every skill comes from. The wrong line was worse than
the silence it replaced — silence let an agent look, while naming the home
scope as the only loadable one steered it away from the folder it can write
and toward the one it cannot. Pinned by `test_skills_grant.py` §3.
Load-bearing: the sensitive-path gate is a CLI behavior, not ours. If it ever
stops applying AT acceptEdits, `test_skills_grant.py` §1's "the node's mode is
still acceptEdits" check is the tripwire, and the bypassPermissions
requirement can be dropped everywhere at once.
※ The bypassPermissions branch is no longer an inference from six negative
measurements — it has a live positive. A seat raised by the user wrote into
BOTH scopes (home and a granted `.claude/skills`) in one turn, no permission
request and no message. The gate held at acceptEdits until the mode changed
and stopped holding the moment it did, in the same session, which is the
cleanest confirmation of the model available: the mode is the whole variable.
Renumbered 2026-08-26 (was D-100): that number was carried by two entries at
once. "presenting a document needs a DIRECT user audience" is the one the
code cites, so it kept D-100; this ruling moved and is otherwise unchanged.
See D-158.

### D-101 · permission mode is editable after creation, at both levels
Ruling (user report, 2026-08-07): the mode was write-once at org creation and
had no control anywhere — not on the org, not on a node. Both are now
editable: the org field is the BORN-WITH default `_new_node` copies into every
hire (org ⚙, admin-only), and each node carries its own (agent ⚙). Changing
the org default is never retroactive — live agents keep the mode they were
hired with and are raised one at a time, deliberately.
Why: D-161 made the mode the difference between an agent that can maintain
the machine's skills and one that cannot, so an unsettable field became a
dead end. Non-retroactivity is the safety property: raising one agent is a
considered act, and a default that swept the whole org would turn it into an
accident.
Bounds: admin surface only. The org field rides `/settings`, which
`_public_denied` freezes for kiosk visitors; it is deliberately NOT on the
visitor-open `/defaults` endpoint that carries tools and visibility, because
unlike those it is not clamped into meaninglessness by a ceiling a visitor
already sits under. Agents cannot set it at either level — `orgtree_retool`
does not expose the field.
※ Observed working within the hour of shipping, which is also the
non-retroactivity property demonstrated rather than asserted: the user raised
exactly ONE node of a five-node org to `bypassPermissions`; its four siblings
and the org default stayed `acceptEdits`. Raising one agent stayed one act.

### D-107 · rescind is the user's alone; the claw-back is total and clamped
Ruling (user, 2026-08-11, choosing among three offered options): FR-22's
rescind — retire whose freed seat+grant is permanently subtracted from the
immediate superior's grant — is **user-only**, mirroring D-00x's delete
ruling. No mcptool verb exists, deliberately: the claw-back lands on a THIRD
party (the superior), which is no agent's to invoke; visitors act as @user
inside the ceiling per D-001, same as delete. Implementation choices made
under the ruling: the subtraction is `min(stake, free(parent))` so a
late rescind (after a plain retire, after reallocations) claws what is still
reclaimable, warns about the remainder, and can never push free negative; a
`rescinded_at` marker makes a second rescind a no-op; a top-level rescind
degrades to the archive alone and says so. Rehire prevention is ECONOMIC
(the headroom is gone), not a hard ban — a superior granted new capacity may
still rehire the seat, which is the capacity-granting ancestor's call.
**Why.** Rescind's effect on the superior is punitive in shape even when the
intent is bookkeeping; delete set the precedent that hard-to-reverse
third-party effects are the user's.

### D-108 · cheap compact ships opt-in; the compact dialog carries both doors
Ruling (user, 2026-08-11): FR-24's retire-plus-fresh-hire is an explicit
verb (`orgtree_cheap_compact`, superior-only) and a second button on the
desk's compact confirmation — never an automatic default when the transcript
is cache-cold. Revisit auto-defaulting only with real numbers, i.e. after
cache telemetry (docs/cache-economics.md ⑪) exists. Supporting decisions:
the replacement copies tier/grant/charter/scope (net-zero on credits, cannot
fail); live reports refuse (auto-moving a team under the replacement is its
own scope decision, not assumed); the predecessor's transcript is COPIED
into its scratch at compact time because the live transcript sits under
~/.claude/projects, which no agent can be granted (D-161's segment gate) —
the docket's "transcript is in the scratch dir" premise was wrong and the
copy is the fix. The compact dialog warns when the node is idle past the
cache TTL, which is the moment the choice actually matters.

### D-136 · a result event is not a turn boundary; the closed pipe is
Decision (session seat, 2026-08-19, user bug — “sometimes at agent turn end
this error appears… I/O operation on closed file”, with the observation that
the affected agent was holding unreceived mail from a subordinate).

The turn loop treated EVERY `result` event as the turn boundary. It is not:
the CLI emits top-level results out of band from its own stream-json writer
(`error_during_execution`, `error_max_turns`) after the real one, and a
subagent's result carries `parent_tool_use_id`. The loop closes the CLI's
stdin at a boundary that finds the queue empty, so a straggler re-entered the
branch and wrote a newly-queued message down the closed pipe.
`TextIOWrapper.write` raises **`ValueError`, not `OSError`** — the branch
caught only `OSError` — so it escaped to the turn's catch-all, surfaced as a
bare "I/O operation on closed file." with no site, dropped the in-memory
carrier, and folded the drained mail back to the mailbox undelivered. The
at-least-once invariant held (the mail was in the mailbox, not lost) but it
stopped MOVING, which is exactly what the user saw.

**The banner was the small half, and catching the ValueError would have
shipped the big half unfixed.** `res = ev` is the branch's first statement and
runs unconditionally, so a straggler carrying the CLI's real `is_error: true`
clobbered the boundary result: `err_blob` went non-empty, a SUCCESSFUL, PAID
turn raised "turn failed", `_after_turn` never ran, and the turn's
`total_cost_usd` was never booked — measured 0 turns booked, costs `[]`, plus
a permanent `turn_error_log` row on a turn that worked and the straggler's
text handed to the freeze detectors. Money, silently unaccounted; the kiosk
spend limit under-counts by the same amount. Round 1 of the redteam loop
found this in the round-1 fix, which is the loop earning its keep: the first
fix made the symptom disappear while leaving the expensive half in place.

So the first discriminator is not the event, it is **the pipe**: `stdin_open`,
tracked (`proc.stdin` stays truthy after `close()`), flipped `False` on both
the success and failure paths of the close, and required by the result
branch. A result arriving on a closed pipe is a straggler by construction,
because the boundary is what closed it.

**But the pipe only discriminates at a boundary that CLOSED it, and round 2
measured that gap as the same money bug still live.** A boundary that FEEDS
the next queued message leaves stdin open, and there a straggler and that
message's own result are the same event shape — no flag can tell them apart.
That is not an edge case: "queue non-empty at the boundary" IS mail arriving
mid-turn, the scenario in the user's own report. Two paid messages, `$0`
booked, empty ring, failure row. ∴ the second rule, and the more durable one:
**stop trying to identify the boundary perfectly and make the accounting
survive getting it wrong.** `turn_paid` carries what the CLI reported, kept
apart from `res` so nothing later can erase it, and it is consulted on ALL
THREE ways a turn can end: folded into `res` before `_after_turn` (success),
booked by `_charge_reported_spend` (failure), and passed to
`_charge_killed_turn` as a measured floor under its estimate (timeout).
`paid_booked` keeps the three from double-charging. This also closes a
pre-existing hole the loop only made acute: ANY multi-message turn that ended
on its last message's failure was already discarding the earlier messages'
spend.

⚠ **Covering only the failure path is not enough, and round 3 measured why.**
Every straggler shape the fixture carried until then had a `result` string,
which makes `err_blob` non-empty and sends the turn down the failure path
where the money was already rescued. The CLI's REAL out-of-band straggler has
**no `result` key** (its text rides `errors: []`), **`total_cost_usd: 0`**,
and sets only an exit code — nothing reaches stderr. So `err_blob` came out
EMPTY, the turn took the SUCCESS path, and `_after_turn` booked that $0 over
a message that had genuinely billed: a completed turn costing nothing, no
banner, no durable row, with the CLI dead and the fed message unanswered —
presenting BETTER than the bug it replaced while being worse. Two rounds of
this loop passed over it because the fixture, not the code, decided which
path ran. That is the loop's sharpest lesson here: **a regression test pins
the shape it models, and a shape borrowed from convenience rather than from
the real emitter pins nothing.** The fixture now copies cli.js's stream-json
catch block field for field, and a second scenario forces the straggler to
exit ZERO so the success-path fold is isolated from the exit-code guard —
without it both guards cover the same dollars and neither is pinned.

Same measurement, second half: a non-zero exit with empty stderr produced an
empty `err_blob` and read as success. **Silence is not success** — `err_blob`
now names the exit code (and the `errors` array when the CLI wrote one). Two
consequences to know. ① The CLI writes its startup failures — including
`No conversation found with session ID` — to STDOUT with no `result` key and
then exits 1, so that text reached nothing before and the №31 handler never
fired on its designed input; it does now, which means **a node can newly
transition to `unrecoverable` (and refuse mail) where it previously stayed
live on a silently-failed turn.** Better, but it is a live behaviour change,
not just a log line. ② The block is gated `and not synth_limit_txt` because a
captured usage limit is specific evidence the next block adopts, while the
generic exit text matches none of the freeze detectors — unreachable in the
shipped CLI, one `if` away from mattering.

And refusing a straggler must not throw away what it REPORTS. The first cut
of this gate dropped a usage limit that rode only the out-of-band result —
node not frozen, turn booked as a clean success, next turn burning against a
live limit (measured). The limit text is now harvested into `synth_limit_txt`
on the refusal path; it is engine-authored, so `agent_authored` stays False
and it is trusted downstream, exactly like the `<synthetic>` record.

`not ev.get("parent_tool_use_id")`
rides alongside — the same sidechain guard the `user` branch already had —
so a subagent finishing MID-TURN, while stdin is still open, cannot become
the boundary and book its cost/duration/denials as the turn's. Occupancy was
already safe: `turn_occ` excludes sidechain events at the capture site and
`_after_turn` refuses the result event's cumulative usage by design (the doc
first claimed otherwise; measurement corrected it). `(OSError, ValueError)`
at every pipe site stays as defence in depth.

Two smaller things the loop turned up. The idle watchdog said "the process
was wedged" for a turn that never reached a boundary at all — a lie that
sends the next debugger after the CLI, now split by `saw_result`. And the
turn catch-all printed only `str(e)`: a one-line message with no site is what
made this bug cost a day, so an UNEXPECTED raiser (`not isinstance(e,
RuntimeError)` — every expected failure is a RuntimeError this function
raised with a written message) now logs a traceback, on stdout with the other
`[orgtree]` diagnostics and after the durable row, so nothing there can cost
it.

**Both guards are pinned by mutation, not by assertion count.** The
`dupresult` fixture's stragglers carry poisoned numbers ($9.99, 900k tokens,
424242 ms, a denial); with numbers equal to the boundary's, "not a boundary"
is unfalsifiable, and the first version of these checks passed with the
sidechain guard fully reverted. Reverting `stdin_open` now fails "the
successful turn is still booked and billed"; reverting `parent_tool_use_id`
fails the mid-turn check with "$9.99 became the turn's cost". A LATE sidechain
result is masked by `stdin_open`, which is why the mid-turn scenario exists —
the only shape where the parent guard is load-bearing on its own. Each check
also asserts the RACE WAS ENTERED (`send()` reports queued, not steered), so
a slow box degrades to a loud failure rather than a vacuous pass.

### D-134 · “has it run” is a question about the SESSION, not the seat
Decision (session seat, 2026-08-18, user bug — “cheap-compacting an agent
and then closing orgtree without messaging it puts it in an unrecoverable
state”): №31's startup `reconcile` condemns a live node whose transcript is
missing, and judged “has this ever run” by the seat's lifetime `cost_usd`.
`cheap_compact` and `reseed` both MINT a session id the CLI has never seen
while the seat keeps that cost, so the new session's entirely normal lack of
a transcript read as a dead one. The agent came back `unrecoverable` — a
state that REFUSES MAIL and is left only by re-seeding, i.e. by discarding
the session the compact had just minted. `reseed` had the same hole, so the
op whose whole purpose is rescuing a condemned node re-condemned it at the
next restart.

The fix is a per-session pardon, `NodeDoc.session_unrun`: set by both mint
sites, popped by `compact_split` on both halves (a CLI fork's id always has
a transcript) and off the LOST predecessors that `reseed` and
`record_cli_compaction` mint (one record must not assert both “never
ran” and “its transcript is gone”). `_condemnable()` is
extracted from the sweep so the rule is unit-testable, with the pardon as a
fourth exemption beside not-live, cost-zero and knowledge-bearer.

**The pardon is spent on EVIDENCE, never on a proxy** — the redteam loop's
central finding, arrived at by discarding two weaker rules. Spending it on a
COMPLETED turn misses every turn that ran and then failed (usage-limit
freeze, network freeze, timeout kill, the backend dying mid-turn): those
never reach `_after_turn`, but the CLI has written the transcript anyway, so
the pardon stood over a session that had demonstrably run and №31 was
disarmed on that node for good — a later transcript loss then resumed it
onto an empty session with its name, credits, team and mailbox intact and
nobody told. Spending it on a successful SPAWN is the opposite error: a
spawn that dies before the CLI writes anything burns the pardon on a session
that still never ran, which is the original bug again. So
`spend_unrun_pardon` asks the only question that cannot be wrong — does a
transcript for this session id exist — from the turn's `finally` on every
exit path, from `remote_control_stop` (FR-01 is the one writer that fills
the CURRENT session with no turn running), and from `reconcile` as a
self-heal, so the pardon can never become permanent.

**It is spent for the session that RAN, never by node id.** `cheap_compact`
has no in-flight guard, so a compact landing mid-turn had its brand-new
pardon eaten by the old session's turn — the user's bug back through a race.
`ran_sid` is captured at `Popen` and re-checked under the doc lock.

**A missing transcript store is not evidence either** (a pre-existing №31
hole the same reasoning exposed). `transcript_index` answered `{}` both for
“this store holds nothing” and “this store could not be read”: a sandboxed
org's ext4 disk is not loop-mounted until something asks for a container and
the startup sweep runs before anything does, so ONE unmounted disk condemned
every node in the org; and the root resolver raises `DiskError` with WSL
down, inside a FastAPI startup handler with no guard, so the backend would
not start at all. `_transcript_evidence` now reaches three verdicts, and the
walk decides — never an `isdir`, which waves through a directory that stats
fine and cannot be LISTED (root-owned on an org disk, a 9p blip over the UNC
view). Present → judge. Unreadable (any OSError but ENOENT/ENOTDIR) → no
verdict. `projects` present but NOT A DIRECTORY → nothing is reachable
through it, which is a verdict: `{}` for a host org, none for a sandboxed
one. MISSING → no verdict for a sandboxed org, whose disk may simply not be
mounted; and for a host org `{}` only when the absence is PROVEN. Gone must
still condemn, or a user who deleted their transcript store resumes onto
silent empty sessions instead of being told — but gone has to be proven,
not read off the errno. The first draft justified that branch with “ it is
either there or genuinely gone”, and measurement killed the dichotomy: on
Windows a deleted directory, a junction whose target is missing, an unmapped
drive letter and an unreachable UNC share all raise the SAME
`FileNotFoundError`, and three of the four mean “I could not look”. So
`_store_provably_absent` climbs to an ancestor that answers and asks whether
the name is simply not in it.

⚠ UNREADABLE is not ABSENT, and the first pass at this conflated them —
the loop's own regression, caught before it shipped. `strict` was made to
re-raise on any failed listing, which turned one vanished project directory
(the user's own Claude Code pruning history beside us; a dangling symlink;
a stray `desktop.ini`, which Explorer writes by itself) into “the whole
store is gone” and condemned every node in every host org — worse than the
bug being fixed, and a regression against the code it replaced. An entry
that is GONE or is not a directory holds no transcripts and `glob` skips it,
so the index is still CORRECT and the walk stays quiet; only an entry that
exists and cannot be read makes the index short, and only that re-raises.
The rule generalises: `strict` reports on what could not be LOOKED AT, never
on what turned out not to be there.

**Not done, deliberately.** Nodes already stuck at `unrecoverable` from this
bug are not auto-healed: the only available signal (`unrecoverable` ∧
`cheap_compacted` ∧ `occupancy is None`) is heuristic, cannot distinguish a
victim from a node that lost a REAL session, and re-seeding a victim
discards nothing anyway (a minted session has no transcript to lose). A scan
of the 15 live org docs found none. The pardon is also not surfaced in
`tree()` — that projection is an explicit allow-list and its sibling marker
`cheap_compacted` is not in it either.

Five adversarial rounds, each attacking the previous round's fixes. The
ran-but-failed turn, the mid-turn mint race and the unmounted-disk sweep
came out of rounds 1–2; round 3 caught a REGRESSION in round 2's own fix
(strict listing turned one vanished project directory into “the store is
gone”); round 4 caught the under-lock half of the race guard being
untested, its mutant reproducing the original bug, and killed the
either-there-or-gone claim by measurement. Four guarding tests were proven
vacuous by mutation before they were made to discriminate — including the
live wiring, where deleting the whole spend call left every suite green.

### D-133 · a freeze's reset time is looked up, banded, and bounds the bill
Decision (session seat, 2026-08-18, user ruling — "all forms of usage freeze
should have a timestamp associated … that way api key fallback usage never
accidentally stays permanent and rings up a massive unintended bill"): the
reset timestamp on a usage freeze is no longer whatever a regex found in the
CLI's error prose. It is resolved, banded, and corrected.

RESOLUTION. Prose first (it usually carries an epoch verbatim); when it says
nothing believable, the account's own usage readout answers — the same source
the D-132 modal renders, now owned by `limits.py` so the modal route and the
freeze path share ONE cache and ONE parser. The lane is read out of the
error's wording (`limits.classify`, with `session` winning outright over a
model name — FABLE-1 in another costume). When the wording names no lane the
SOONEST reset on the board answers, `is_active` or not: the user's ruling is
"default to the shortest one, so that it can be checked sooner", and the
asymmetry backs it — guessing short costs one re-freeze, guessing long costs
money. Only if the readout cannot answer either does the old blind 5-minute
probe floor apply. Every freeze records where its number came from
(`frozen.reset_src`: `text` / `usage:<lane>` / `probe` / `inherited`) — and,
when the only witness was the agent itself, that too (`frozen.untrusted`),
because on such a freeze `reset_src` says where the NUMBER came from and not
that the number priced anything.

BANDS, because a number in the right place is not a timestamp. An explicit
epoch is trusted to the longest real lane (the regex matches ANY 9–11-digit
number after a pipe; an 11-digit one reads as a date in the fifth millennium,
and `api_fallback` would bill the org's key until then). A bare clock time
carries no date, so it cannot mean more than a day out. And nothing may exceed
its own lane's length — live-caught on the day: "You've hit your session limit
— resets 1:40pm" arrived with 1:40pm already past locally, rolled to tomorrow,
and priced a 23-hour key-billing window for a wall that lifts in five. The
window itself is bounded at both ends independently of all that:
`_fallback_window_until` = floor 15 min (a probe freeze must still get a turn
out), ceiling 7 d + 1 h (the weekly lane). If the wall is still up when a
window closes the next limit error opens a fresh one — a round trip, not a
fortune.

NON-BLOCKING, because the readout is a network round trip that routinely takes
over a second and the freeze is written under `DOC_LOCK` (user report, same
day). The freeze stamps what the CACHE knows — `limits.cached()` never fetches
— and `_spawn_reset_refresh` re-asks off-lock, rewriting the record only if
the answer moved by more than a minute, and only while it still owns what it
stamped (freeze `until_ts` and `api_fallback_until` compared to the exact
values it wrote; a resumed node, a later freeze, or a fallback the user
switched off mid-flight all leave the record alone). The correction moves the
window in BOTH directions: shorter is money saved, longer is a wake that will
not re-freeze on arrival.

PROACTIVE, so the cache is worth reading: one account-wide warm-up loop
(`start_usage_warm_loop`) paced by how close the account is to a wall — 5 min
under 80% utilization, 2 min from 80%, 45 s over 95% (`critical` severity
counts as 95 whatever `percent` says). One HTTPS GET per tick, single-flighted
(`_fetch_lock`), silent when the host has no subscription credentials at all.

WHOSE QUOTA, added after adversarial review: the readout describes the HOST
SUBSCRIPTION, so it may only time a freeze the subscription caused.
`bills_the_key(org, on_fallback_key)` — a permanent-key org, or a fallback org
inside an open window — routes those freezes to prose-or-probe instead. Read
off the subscription's lanes, a per-minute API rate limit was parking nodes
for four hours.

AND THE BOUNDS TWO REVIEW ROUNDS ADDED, each one a way the bill could have run:
a readout past `MAX_EVIDENCE_AGE` (15 min) stops being evidence — a broken
upstream serves its last good payload forever, and a freeze must not price a
window on a memory; when the named lane has nothing believable the fall-through
to another lane is capped at the NAMED lane's length, because a stale readout
whose session entry had expired would otherwise answer a session limit with the
weekly lane, six days out; `classify` requires a `your <model> … limit` shape,
since raw error text echoes model ids ("model claude-opus-4-1") and reading one
as the model's weekly pool widened a five-hour wall into a seven-day window;
and the correction pass owns the freeze and the window SEPARATELY, so a node
resumed in the second it takes still gets its window re-priced.

Round two closed four more, all of the same shape — a number believed further
than its evidence reaches. The SHORTEST-lane cap applies to the unnamed lane
too, which is the branch ruling ③ is actually about (the canonical wording
names no lane, and a board that has lost its session entry would have answered
from the weekly one). The epoch exemption is about PROVENANCE, not form: a
clean result's text IS the agent's own answer, so a node could open a week-long
key window by typing `Weekly usage limit reached|<epoch>` — untrusted text is
banded like any guess. `bills_the_key` asks `sandbox.container_auth`, since a
kiosk-level key or `ORGTREE_SANDBOX_API_KEY` never appears in `org.d`. And an
unrecognized upstream lane takes the SHORTEST length rather than the longest.
Two smaller ones: the clean-result detector uses the RAW parse (banding it
stopped `Resets 9am.` freezing anything at all — a detector is not a clock),
and `frozen.on_fallback` records the lane the turn actually ran on rather than
re-reading "is a window open now", which had a sibling's window putting a
subscription-lane freeze to sleep for hours beside a paid, unused key lane.

Round three found the same shape once more: untrusted text does not get to
NAME ITS OWN LANE. Removing the epoch exemption was not enough, because the
band it fell back to came from `classify` on that very blob — one sentence
containing the word "weekly" bought itself the seven-day band, from the prose
parser and from the readout alike. `trust_lane=False` treats an unvouched blob
as unnamed, including the fable lock's asserted `weekly_scoped`.

Round four closed the class properly, because three had only shortened it.
① The provenance signal was WRONG: `err_blob is not synth_limit_txt` lumped
the CLI's own `<synthetic>` limit record — the live-observed shape, which a
model cannot forge — in with the agent's final answer, so the most common real
limit threw away the epoch the CLI had just published and took the 5-minute
probe floor instead. Provenance is now carried (`agent_authored`, set at the
one promotion site), not inferred. ② An unvouched blob still PRICED a window:
capped at the session lane, one 40-character sentence still moved the whole
org onto the user's metered key for ~5 h against a wall that did not exist
(`spawn_env` hands the key to every node while a window is open). Untrusted
text may now set `until_ts` — the node still wakes on it — but its window is
floored at 15 minutes. ③ And it still fired the org-wide FABLE escalation,
whose trigger is three words in the blob and whose `dissolve` policy ARCHIVES
every fable node in the org: round three had guarded the lock's timestamp and
left its trigger open, which was the destructive half. The rule this settles:
gate every CONSEQUENCE on provenance, not just the arithmetic.

Round five showed the floor was still the wrong instrument. It bounded ONE
incident and not the RATE: the window it opened made the node immediately
resumable — and that wake ignores the `auto_resume` toggle (D-130's
"api_fallback is its own consent"; D-122 governs the connection kind and says
the opposite for a record carrying both) — so
the resume replayed the same prompt to the same agent, the same sentence
re-opened the window, and the whole org sat on the user's metered key at 95%
duty, indefinitely. So an unvouched limit now opens NO window at all (a real
wall is always reported BY the CLI, so declining costs a genuine limit
nothing), and a run of them is counted like the connection kind's
`net_fail_run`: on the `UNTRUSTED_LIMIT_RUNS`-th consecutive self-diagnosed
limit the node stops waking itself and waits for a person — and the
record says so: `until_ts` is dropped (there is no window for it to
bound) and `reset_src` becomes `capped` rather than describing a
number that no longer exists. A completed turn
clears the run. Round nine, running its own ~100-mutation campaign, found the last one of
the class: `_looks_like_usage_limit` matches a per-minute API **rate** limit
as readily as a subscription usage **lane** — deliberately, since any wall
should freeze the agent — but the readout only describes lanes, so a 429
("exceeded your per-minute rate limit") was answered with the session lane's
reset: a four-hour park, and on a fallback org four hours of key billing,
against a wall that lifts in a minute. `limits.is_rate_limit` now keeps the
readout out of it; the error's own prose still answers, and otherwise the
probe floor does. The same round retired a guard that had been certifying the
drift it was named for — it tested `"time.time() + 300" in <source>`, which is
a prefix of `+ 3000`; the probe floor is `PROBE_FLOOR` now and the check is a
literal.

Round six added the last consequence anyone had missed: the LABEL. `frozen.until` is projected by `ledger.tree()` and rendered as system chrome in the org header and on the node badge — kiosk visitors included — so taking it from the blob let an agent put ~60 characters of its own prose, a URL or an instruction, into the operator's chrome by ending a turn with the right sentence. An untrusted freeze now derives its label from the timestamp. The lane decision is captured WHOLE at spawn
(`billed_key`) rather than as a window boolean later recombined with org
fields — which is what the docs already claimed. One more band
came with it — an explicit epoch is exempt from the lane cap only when the
same text names no lane, since "your session limit …|<epoch 8 days out>" is
two pieces of evidence contradicting each other. Alongside: the container's AUTH became part
of its identity (`orgtree.auth` label, a digest for keys — `docker run` bakes
the credential in, so a container created under one auth kept billing that
way while `bills_the_key` read today's config and called it "subscription");
the `elif` freeze branch records the lane like its sibling; `fetch` really
never raises now (`"limits": 3` was a TypeError out of a function documented
not to have one); and a key-billed freeze skips a retry loop whose answer
cannot change. Test-side, across the rounds: the regression test for the epoch fix
was itself vacuous — it called the parser without the `kind` the real path
always supplies; the trust checks pinned only the seven-day case and were
blind to the five-hour one that mattered; and `reset_src` was asserted only
against a hand-built freeze record, so nothing noticed a REAL freeze
misclassifying its own provenance. All three now go through the seam the
freeze site uses, or through a real turn. Separately, `tools/run_tests.py
--only` silently ran NOTHING and exited 0 for a comma-separated or repeated
filter, which is how a CI line passes having tested nothing.

### D-141 · the warm loop also wakes at the reset itself
Ruling (session seat, 2026-08-20, user instruction — "schedule a usage limit
cache update to occur at exactly the next reset time"): D-133's warm loop no
longer paces on pressure alone. Every lane publishes a minute-exact
`resets_at`, so the one moment the cached board is guaranteed to be wrong is
knowable hours ahead — the loop cuts its sleep short to land `RESET_LAG` = 5 s
past the SOONEST future reset on the board (`limits.next_reset`), whichever
lane owns it, and takes the pressure cadence otherwise. The boundary is a
ceiling, never a floor: a weekly reset six days out does not stretch a 45 s
critical tick, and a boundary already at the door is floored at
`WARM_MIN_SLEEP` = 10 s so a skewed clock cannot spin the loop against a
semi-documented endpoint. Cost is one extra GET per lane rollover — a handful
a day against ~288 from the cadence.
Why: between a lane rolling over and the next idle tick, the server's readout
said the wall was still standing for up to five minutes. Everything downstream
of that cache repeats the claim: the D-132 bars, the D-138 glow (which reads
the cache only and may not fetch), and — the one that costs money — a D-133
freeze landing in the gap, which stamps its reset from `cached()` under the
document lock and never gets to ask. The reset is the cheapest possible
schedule for a read that is otherwise pure guesswork.
Bounds: the wake is a CLOCK, not a price. `next_reset` takes any lane at face
value where `reset_for` bands a candidate by the lane the error names — a
wrong lane there bills the org's key for six days, while a wrong lane here
costs one early HTTPS GET. It still refuses the absurd (a reset already past,
one beyond `MAX_HORIZON`), so a stale board aims at nothing and the cadence
carries.
Load-bearing: the upstream rolls its window over on ITS clock. Waking at the
boundary onto a board that still shows no future reset is the two-clocks case,
not an error — `_warm_next` re-asks every 10 s, `RESET_RECHECKS` = 4 times,
before letting go, because past that "no future reset" is indistinguishable
from an account with no lanes at all. That branch is unreachable from a live
loop, which is why the step is a pure function and tested as one.

### D-138 · the usage button glows before the wall, off the cache alone
Ruling (session seat, 2026-08-19, user feature): the ◔ usage button — both the
orgbar one and the welcome panel's — wears a gold ring from 75% and a red,
breathing one from 90%, reporting the PEAK lane of the host subscription's
standing, with that lane, its percent and its reset in the tooltip. It reads a
new cache-only route, `GET /api/usage/peek` (`limits.peek`), never
`/api/usage`: the glow polls whether or not anyone opened the modal, and an
always-on indicator must not be able to add a single request to a
semi-documented upstream. A readout older than `MAX_EVIDENCE_AGE` reports
unavailable and the ring goes out — the modal still shows those bars, because
a bar is labelled and dated while a ring is a bare claim about NOW.
Why: a usage limit announced itself by freezing an agent (D-133), while the
standing that predicts it was already fetched, cached and kept warm on the
server (`start_usage_warm_loop`) — visible only to someone who thought to open
a modal. Thresholds are D-132's own, extracted into one shared function
(`usageSeverity`): two readers of one standing that could disagree about what
"near the wall" means would produce a bug visible only to the person who
opened the modal to check the button against it. Steady at warn, breathing at
crit — the two tiers have to be separable by an eye that cannot separate gold
from red, so motion carries what hue cannot (`prefers-reduced-motion` trades
the breathing for a brighter steady ring).
Bounds: admin-only on D-132's two gates — a kiosk client never issues the poll
(`BASE` swaps the fetcher for a frozen "unavailable"), and the public gateway
404s the route regardless of what a client asks for.
Load-bearing: the warm loop is the only writer of that cache. If it ever stops,
the glow ages out and goes dark rather than lying — but it also stops warning.

### D-132 · header usage modal: the host's Claude Code /usage bars, admin-only
Decision (session seat, 2026-08-18, user feature): a ◔ button in the orgbar
(and beside the GitHub link on the welcome panel) opens a modal with the
host subscription's rate-limit bars — the same session / weekly /
weekly-scoped readout Claude Code shows under /usage. `GET /api/usage`
proxies `api.anthropic.com/api/oauth/usage` with the host OAuth token via
the machinery subproxy.py already owns (read + in-place refresh of the
shared credentials file), normalizes the upstream `limits` array
(kind/percent/severity/resets_at + scoped model display name, "Fable"), and
caches 30 s with stale-on-error. The UI renders bars GENERICALLY from that
array rather than three hardcoded rows, so a new scoped bucket upstream
appears with no code change; the flat five_hour/seven_day fields remain as
a fallback for an older upstream shape. Admin-only twice over: the button
gates on `!tree.public`/`!BASE`, and the public gateway 404s any /api path
outside the kiosk's own org regardless. Severity colors reuse existing
chrome meanings (accent → fable gold ≥75 / warning → --bad ≥90 / critical).

### D-131 · fallback dollars keep their own ledger; the lane is fixed at spawn
Decision (session seat, 2026-08-17, user feature): every dollar booked while
an api_fallback window is open ALSO accumulates on an org-lifetime counter
(`api_cost_usd`, org doc), surfaced as `api_cost_usd_total` in the tree
summary and shown as the hover split on the header cost chip ("subscription
$A · api key $B"). Which lane bills a process is decided where the key is
(or isn't) injected — at spawn — so the flag is captured there and threaded
to all three cost-booking points (turn `_after_turn`, killed-turn estimate,
compaction fork); a window expiring mid-turn doesn't rewrite where that
turn's tokens were billed. The counter is org-level and monotonic on
purpose: node deletion banks per-node burn into `deleted_cost_usd`, and the
split must never need the same dance. The tooltip stays quiet ('' → plain
"total spend") for orgs that neither hold a fallback key nor ever burned
one — a "$0.00 api key" lane would be noise, not information. Scope bound:
a permanent-key org (`api_key` without `api_fallback`) bills the key for
EVERYTHING, so a split would be vacuous — the counter deliberately tracks
only fallback-window burn.

**The same spawn-time capture is also a live signal (2026-08-19, user
feature).** Red on the canvas means "this is spending my own API credit,
right now", at two scopes: the office border wears it while the window is
open (an org-wide fact the tree already carried — `api_fallback` plus
`api_fallback_until`, compared against the client's clock in the single
reader `fallbackActive`), and an agent card wears it while ITS in-flight
turn is the one billing. The per-turn half is `on_fallback` in the
supervisor's in-memory node state: written from `on_fallback_key` at spawn,
cleared in the turn's `finally`, shipped by `annotate()`. Deliberately the
same value the accounting uses rather than a fresh `api_fallback_active`
read at render time — a card must be red for exactly as long as the spend it
describes, so a window that shuts mid-turn leaves the card red until that
turn ends. `_compact_split` brackets the flag too (the fork is the expensive
lane user), saving and restoring rather than popping, because the automatic
path runs inside a turn whose own capture must survive it. Nothing persists:
a backend restart cannot strand a red card.

### D-130 · api_fallback: the key is a spare lane, and expiry is the only revert
Decision (session seat, 2026-08-17, from the user's feature request "switch
temporarily to an API key when usage limits are hit; automatically revert"):
org option `api_fallback` inverts the meaning of a stored `api_key` — the
subscription bills routine turns, and a usage-limit freeze opens a window
(`api_fallback_until` = the limit's own reset, floor 15 min) during which
spawn_env injects the key and the bridge `/anthropic` proxy re-auths with
`x-api-key`. **Reverting is pure expiry**: nothing writes the state back,
the key simply stops being chosen once the reset passes — no revert step
can be missed. The resume timer wakes subscription-side limit freezes
immediately while the window is open, `auto_resume` on or off (the option
is its own consent — same shape as D-122's connection rule); a freeze
earned ON the key lane is stamped `on_fallback` and waits for its own
reset, which is what stops an insta-wake loop against the key's own
limits. Bounds: fable-TIER quotas stay with `fable_limit_policy` (a policy
lane, not a billing lane); headless and api_fallback refuse each other
(headless needs the key full-time); a sandboxed fallback org stays in
PROXIED mode because container env is fixed at `docker run` — the auth
flip lives host-side in the proxy.

### D-129 · the AUTO resume may cheap-compact first; the manual ▶ never does
Decision (session seat, 2026-08-17, user feature): org option
`auto_resume_compact` — when the auto-resume timer wakes a usage-limit
freeze, cheap-compact the node first: a limit freeze has outlived the
cache TTL by construction, so the swap dodges the cold transcript reload
(D-114's arithmetic) and the replay texts drain into the successor's
first envelope. Guards: limit-kind records only (a connection freeze is
seconds old and warm), only when a transcript exists to reload, skipped
while an api_fallback window is open (a fallback wake is seconds behind
the freeze — the opposite case), and a ledger refusal falls through to a
plain resume. The manual ▶ resumes sessions exactly as they are: a human
pressing resume has judged the org ready, not asked for surgery.

### D-128 · a summary-less successor gets breadcrumbs spliced into its prompt
Decision (session seat, 2026-08-17, user feature): a session minted EMPTY
— cheap_compact's successor, and reseed's equally-empty one — carries the
node marker `cheap_compacted`, and identity_prompt splices the working
folder's `breadcrumbs.md` (D-115's realtime compaction log) into the
system prompt on every spawn of that session, instead of only pointing at
the file. Mirrors how a normal compaction's summary lives inside the CLI
session — which is also why the marker RIDES the whole generation (the
CLI re-applies the append file on resume; dropping it after turn one
would un-remember it) and why a normal compaction clears it (that
successor has its own summary). Tail-taken at 12k chars (the file's
convention is newest-last), cut declared in the block header per the
truncation doctrine; safe from argv limits because the prompt rides
D-126's file. The cheap-compact notice now states the splice rather than
ordering a read.

### D-127 · edge jump cards: off-screen coworkers, next sibling only
Decision (session seat, 2026-08-17, user spec): at desk zoom, one small
screen-space card per side hugs the SCREEN edge for the focused agent's
NEXT live sibling in that direction — at the neighbor's own screen
elevation (clamped into view), suppressed whenever the neighbor's card
actually intersects the viewport (a visible card needs no proxy), gliding
the camera on click. Screen-space HUD chrome (same layer and
pointerdown-stop discipline as the zoomhud/tray), desktop only — the
mobile sheet world has no camera-derived desk. Only the immediate
neighbor renders, not the whole row: the cards are a walking aid, and the
tray already lists everyone.

### D-126 · the identity prompt rides a file, never argv
Decision (session seat, 2026-08-17, from a live failure): a report's mail to
a coordinator with 24 retired reports killed the turn spawn with `[WinError
206] The filename or extension is too long` — which, despite the wording, is
Windows' CreateProcess cap on the WHOLE command line (32,767 chars), not a
filename check. `--append-system-prompt` carried the full identity prompt on
argv, and that prompt is unbounded: full-visibility chart incl. retired
nodes plus cascading team charters measured ~22k chars on a mere 12-node
org. The fix: write the prompt to `<scratch>/.orgtree-identity.md` before
every spawn and pass `--append-system-prompt-file` — the CLI's other door
into the SAME append variable (hidden flag; verified in cli.js 2.1.31, and
mutually exclusive with the inline form). №29 unchanged: rewritten per
spawn, honored on resume. The scratch is the one folder both spawn shapes
read — host path directly, container through its mount (first mint chowned
to the agent; later rewrites truncate in place). The agent can read the
dotfile, which reveals only its own system prompt. Known dependency: a CLI
old enough to lack the hidden flag fails the spawn loudly ("unknown
option") — acceptable, since the sandbox image pins the host CLI version
and both installs here carry it.

### D-121 · the greyed live tail is a strict suffix of the conversation
Decision (implementer, 2026-08-14, from a user report: "temporary greyed out
[rows] rendering out of order has been a persistent issue"). The desk renders
the durable transcript block, then the whole live tail below it — so the seam
is chronological if and only if every surviving live row is genuinely newer
than every durable row. The sweep's matching cannot guarantee that alone
(some rows have no matchable twin: a thought mid-run, a slash command's
output whose twin is a system `cmd_out` row), so `_sweep_live` gains a
CHRONOLOGY BACKSTOP: the CLI writes its transcript strictly in order,
therefore a durable record newer than a live row proves the row's own record
is already written and on screen — any non-sticky row older than the newest
durable stamp minus 2 s retires on that proof. This is order-evidence, not
the old drop-on-a-timer (D-50 still holds: no retirement without the
replacement provably in hand); the 2 s guard absorbs emit-vs-write stamp
jitter, the known hazard being a queued user message's record cutting the
line mid-stream. Two companions: `durable_texts` now counts system `cmd_out`
twins, so slash-command output stops duplicating beside its own twin; and
STICKY rows stay bottom-anchored by design — they have no transcript record
ever, so "immediate command output stays visible under the composer" wins
over strict chronology for that one class. Worst case after this: a
strand outlives its twin by one poll cycle instead of the rest of the turn.

### D-124 · client liveness rides one bus, mirroring the server's G2
Decision (implementer, 2026-08-14, from a user report: audience-grant
rescinds sat stale in the inbox modal — "another missing websocket send").
The diagnosis was sharper than the report: the send was NOT missing. G2
already broadcasts 'changed' on every `store.save_org`; what was missing
was the CLIENT half — the ws handler refreshed only the tree, and every
other surface (audiences, both inboxes, events, history, scratch) sat on
its own 5 s `usePolled` interval, so every mutation was instant on the
canvas and up to a poll late everywhere else. That is a CLASS, not a bug:
any new panel inherited it by default.
The pattern (livebus.ts): one dependency-free bus with exactly two central
producers — `req()` bumps after every successful non-GET (the mutation
this tab just made), and the ws 'changed' handler bumps (mutations made
anywhere else) — and one central consumer: `usePolled` subscribes every
polled surface; its interval survives only as the fallback for a dropped
ws. Bumps coalesce 120 ms. No per-call-site refetch to remember, so no
future surface or endpoint can be forgotten — the same shape as G2 on the
server, which replaced ~30 endpoints remembering `hub_changed()`.
Bounds: one-shot fetches remain legitimate for form INITIAL values, static
preset lists, probes, and click-driven reveals — auto-refetching an open
editor would stomp the user's draft (G5's own caution). File-state panels
(scratch, disk) keep meaning from their poll: agents change files without
a doc save, so no 'changed' ever announces those.

### D-122 · a network interruption always retries itself; the toggle governs limits
Ruling (user, 2026-08-14, verbatim: "network outages should always attempt
to autorestart, regardless of the setting"). The auto-resume timer wakes
PURE connection-kind freezes unconditionally — `auto_resume` now governs
only the LIMIT kind, where restarting spends against a quota and opt-in is
the right default. A freeze record carrying BOTH flags waits on the toggle
like any limit freeze (the retry would spend). The desk banner's connection
branch promises "retrying automatically" unconditionally, and keys on
`connection && !limit` (the `limit` flag joins the tree() frozen projection
for exactly this); the freeze label itself still states only the attempt —
c7e169d's fact-not-promise wording survives the policy reversal unchanged,
which was its design goal. The backoff shape (30s→300s exponential,
NET_RETRY_MAX=4, then unfrozen-with-error) is unchanged.
Why: a limit freeze is a budget event, but a connection drop interrupts
work the user already set in motion — waking from it restores their intent
rather than overriding it.
Was. "a user with the toggle off has asked not to be auto-restarted, and a
network drop does not override them" (redteam constraint, 2026-08-06) — the
call was flagged then as the user's to make, and they have now made it the
other way.

### D-123 · the compact-screen desk is a full-screen sheet (approved, dormant)
Ruling (user, 2026-08-14: "the compact screen exception is approved, for
whenever the mobile wave proceeds"). On compact screens — the mobile spec's
`min(vpW, vpH) < 780px` test — the desk opens as a full-screen 1:1 sheet
instead of fading in over the card; desktop keeps D-073 unchanged. Dormant
until FR-02 builds (the wave itself stays HELD, reaffirmed the same day),
recorded now so the implementer who builds it does not read the sheet as a
D-073 violation and revert it. The arithmetic that forced the fork: at
375px viewport width a world-scaled desk renders ≈4.4px text — no zoom
makes it both legible and framed. (Sheet predicate amended by D-125: a
coarse pointer or ≤640px width is also required, so fine-pointer desktops
never sheet.)

### D-125 · mobile-wave drift rulings: coarse-pointer gate, watchdogs off the map, hire placement (dormant)
Rulings (user, 2026-08-14, the "prepare the mobile wave" drift audit —
docs/mobile-spec.md §9-§11; all dormant until FR-02 builds, same footing as
D-123): ① mobile UI exists ONLY on phone/tablet OSes — a device-class
allowlist evaluated once at boot (`Android` UA, `iPhone`/`iPad` UA, or
Mac-platform + `maxTouchPoints > 1` for iPadOS's Macintosh-UA disguise;
real Macs report 0), stamped as a root class that ALL mobile CSS scopes
under. Windows, macOS, Linux and ChromeOS never get mobile UI regardless
of touchscreen, tablet mode, pointer coarseness, or window size — desktop
stays bit-identical at every window width, and the spec's width tiers +
the `min(vp) < 780` sheet test apply only WITHIN allowlisted devices
(1600×900 is thereby moot). Escape hatches: "request desktop site" flips
a tablet to desktop view; a settings override covers the reverse.
Was. (same day, hours earlier) `min(vpW, vpH) < 780` AND (`pointer:
coarse` OR width ≤ 640) — superseded when the user asked to guarantee
mobile "absolutely only shows on phones & tablets and nowhere else": a
Windows 2-in-1 in tablet mode reports `pointer: coarse`, and no media
query distinguishes it from an Android tablet; only the OS class does.
② Watchdogs HIDE from the compact map: a count-dot
in the owner's caption, the list in the desk sheet's header, the detail
panel a full-bleed sheet (the 7px-font 50×26 chips are illegible and
untappable at any phone-fitting zoom). ③ The compact full-screen hire form
carries a placement selector — below / side-ordering / above-splice — so
the F-03 + FR-25 edge-chip semantics (cursor-proximity-gated, no touch
equivalent) survive. Offered and NOT taken the same day: a tap path for
granting (stays drag-only — with compact hiding card drag, granting has NO
compact path; the builder surfaces that gap rather than inventing one) and
the specific orgbar banner→chip absorption (re-ask at the layout tier).
Scope ruling: "spec-refresh only" — even §8 steps 1-2 (safety +
structural) wait for the go-ahead. (Superseded hours later by "proceed
with the full end to end implementation" — the wave BUILT same day,
0b1b487; build record + deviations in docs/mobile-spec.md §12.) Shipped with the audit as a live-bug
rider (a126421 precedent): CreditBar's pointercancel committed a live
reallocation; it now aborts (b9f3664).

### D-120 · liveness, not the successor link, decides the org axis
Ruling (user, 2026-08-12, verbatim in the redteam session — provenance
verified by the implementer against the session's own queue log): "keep the
successor link, but amend the rule to only hide retired predecessors with a
successor link. just having a successor link alone shouldnt be enough to
cause it to not be rendered." So `org_children` hides a node only when it
has a successor AND is not live: an archived bearer still steps off the org
axis (§8.5 holds for the dead), while a REHIRED one — standalone or
subordinate — is an org child like any other, with the full card, desk and
controls. Supersedes the FR-24-era acceptance that filtered on the link
alone, which left a live, spending session visible-at-best and inoperable
(and, via an unplaced-node spark, crashed the canvas — that guard is the
companion fix, not the ruling). frontend `flatten()` now synthesizes lineage
pseudo-cards only for non-live, non-archived generations: a live bearer
arrives through `children`, and a pseudo for the same id would let sibling
order decide which card wins. (a7d0bb2 + the flatten tightening)
Final form (redteam deviation catch, same day): "retired" is taken at the
ruling's word — the predicate is `successor AND state == "archived"`, not
`!= "live"`, because an UNRECOVERABLE generation is the state whose own
notice says "rehire to re-seed, or retire to free the credits" and off the
axis it rendered nowhere at all under an archived successor. With the axis
carrying every non-archived generation, flatten()'s lineage pseudo-card
synthesis qualifies for nothing and is DELETED (the invariant: the axis and
the pseudo path must carry disjoint id sets — a double-set fails silently
by sibling order). `isBearerOf` gates remain, producerless, pending a sweep.

### D-119 · a command dog's runtime is off the scheduler's thread
Engine-shape decision (implementer, 2026-08-12, from a redteam measurement):
`_wd_tick` walked every org serially and ran command checks inline, so ONE
command dog sleeping 5s delayed the whole engine's pass by 5.10s — every
org's dogs, including realtime stream flushes, behind one subprocess, with a
bound of communicate(timeout=60) × command dogs across ALL orgs (the dog
caps bound memory/mail, not this). Shape: the scheduling loop stays serial
(0.01s without commands); command checks run on a four-worker pool (bounds
the process storm a 32-dog org could start) whose done-callback applies the
doc update + fire exactly as the inline path did; ONE in-flight check per
dog, so a slow command stretches its own cadence instead of stacking.
Saturation harms only the saturating org's command dogs — never streams,
files, or other orgs. The TimeoutExpired kill now drains (second
communicate) — concurrent zombies were about to become real. Measured after:
tick with a 5s command dog = 0.006s. (6e39b89)

### D-118 · a @net: send to a recipient the hub doesn't know REFUSES
Ruling (user, 2026-08-12): mail to a hub recipient that does not exist in
the mail hub's ledger fails at the door — both doors, the agent verb and
the user's org-inbox compose. A spool entry addressed to nobody sits
"queued" forever, which is the @ext: black-hole class the 2026-08-05 ruling
killed. Mechanism: the local roster cache answers first (offline-cheap); a
miss earns exactly ONE live GET /api/roster per known hub before refusing
(`net.probe_peer` — a freshly registered peer must not be refused for
beating the next poll pass, and each live answer refreshes the cache).
Bare names that auto-resolve to @net: come FROM the roster, so the gate
really bites explicit `@net:<slug>` strings. Hermetic rigs seed
`net._rosters` where a real registration would have written.

### D-117 · watchdogs: free pets, both engine shapes, the owner's hands
Rulings (user, 2026-08-12, FR-18 design session): ① BOTH engine shapes ship
— cadenced in-process polls (file / command / process kinds; the file kind's
high-water diff recovers events from orgtree's own downtime, the FR-07-spool
property) AND a realtime persistent LISTENING command (`stream` kind: each
matching stdout line surfaces the moment it occurs; the child dies with
orgtree and the scanner re-arms it at startup — downtime output honestly
lost). ② Capability rule: a dog runs with its OWNER's hands — command/stream
require the owner's bash and run inside the owner's sandbox when sandboxed
(docker exec); file targets containment-check against the owner's readable
roots at the API boundary (sandboxed agents watch files with an in-container
stream dog instead — host translation has no honest answer there); process
liveness (pid:N / port:N, DOWN-edge-triggered) is read-only and free.
③ Authority: self-create; ancestors manage downward; the user manages all
from the canvas panel. Free per the 2026-08-06 pets ruling — bounded
numerically instead (8/agent, 32/org, 15 s poll floor, 5 s stream fire gap,
50-event ring). ④ Lifecycle: pause on the owner's archive (resume on
rehire), die on delete, rename remaps, cheap-compact leaves them untouched
(the seat persists). A fired dog is ordinary MAIL (`relationship: your
watchdog`) — frozen owners queue, no special wake power, and a dog-wake is
a wake for FR-24b's auto-compact. One verb (`orgtree_watchdog`,
create/list/pause/resume/remove — verb 25), a standing prompt line ("never
burn turns polling"), and the canvas spec verbatim: a ~60×36 named chip
wired to its owner, `launchSpark` riding the wire per event, click-through
detail panel with the sent-events ring.

### D-116 · sonnet seats cost 2 (input pricing locked in at $2/M)
Ruling (user, 2026-08-12): the sonnet seat drops 3 → 2 in `TIERS`. Because
per-org tier tables are frozen ADD-only copies (the 2026-08-04 lesson), a
price CHANGE gets its own load-hook migration: only the old shipped default
(3) migrates — any other stored value is an operator customisation and
stays, which the customisation-survives pin in the authority suite already
proves. The effect on a live org is strictly loosening: committed drops by 1
per live sonnet seat, free rises, no invariant tightens.

### D-115 · agents write their own compaction log, in realtime
Ruling (user, 2026-08-12, completing D-114): every agent that can write
maintains `breadcrumbs.md` in its working folder — important events,
decisions, findings and open threads appended AS THEY HAPPEN, "effectively
creating their compaction log in realtime". The point is cheap compact's
shape: the successor starts with NOTHING, the working folder survives
unchanged, and its first-turn notice points at breadcrumbs.md FIRST (then
transcript.jsonl). Prompt-side doctrine only — no new verb, no server state;
the line renders only for seats holding edit or bash (a read-only seat
cannot follow it). This is docs/cache-economics.md measure ① made concrete.

### D-111 · scope requests: the user grants, superiors are the cheap path
Ruling (user, 2026-08-12, FR-13): an agent's permission-scope request —
folder, built-in tool, MCP server, or a permission-mode raise — is granted by
the **user only**. `orgtree_request_scope` parks the items on the user's
batch card; a superior that already holds the capability is the cheap path
(orgtree_retool, no card), and both the tool text and the no-audience routing
(the request mails the superior, naming retool) keep that path visible.
Approvals apply as the user via `set_scope`, so a deep grant D-106-cascades
the chain and a kiosk ceiling clamps it exactly like a manual ⚙ grant. Items
the agent already holds drop as no-ops (motto A3); `bypassPermissions`
requests are loudly labeled UNGUARDED on the card.

### D-112 · one batch per agent; a new request APPENDS; one submit resolves
Ruling (user, 2026-08-12, FR-14, their own wording): "the idempotent action
to sending another user inquiry should be to APPEND to the current batch —
the batch is only finished when submitted or explicitly invalidated." This
**supersedes the 2026-08-06 single-active-request eviction** in both
directions: questions, the credit request and scope items coexist as ONE
composed card (`node_ask` kind "batch"), and nothing evicts anything.
Resolution is ONE submit with **skippable tabs**: a skip travels as an
explicit null/skip (FR-04's miscount guard survives — holes still refuse),
skipped requests resolve as unanswered/dismissed, and one composed mail
returns. Per-store `rev` stamps are the CAS: an append mid-render refuses
the stale submit. Implementation judgments under the ruling: a second CREDIT
request amends the existing figure in place (two contradictory numbers on
one card is nonsense — the append ruling's credits-shaped case); question
tabs dedupe by question text (same text = amend that tab); scope items merge
by identity; caps of 8 question tabs / 8 scope items with a loud refusal;
withdraw stays WHOLE-batch (re-ask what still matters).
Was. one active request per agent, newest evicts across kinds (2026-08-06).

### D-113 · `plan` joins the permission-mode ladder, ranked lowest
Ruling (user, 2026-08-12): the CLI's read-only planning mode is a first-class
`PM_LEVELS` entry below `default` — a plan seat can look and reason but not
edit. Inserted at rank 0: every comparison in the ledger is relative, so
existing stored modes keep their order. Selectable in both ⚙ panels and the
kiosk ceiling, requestable via FR-13.

### D-114 · cheap compact is IN-PLACE, and may fire automatically on wake
Rulings (user, 2026-08-12, three messages during the build): ① cheap compact
"should work fine with reports, just like a normal compact" and ② "retain
them the same way a normal compact works" — so the verb was REWORKED before
ever running on a live org: the seat keeps its id, parent, scope, charter,
grant and team; only `session_id` is replaced (fresh id ⇒ empty next turn),
and the pre-compact session archives as the `nid@gen` knowledge bearer —
compact_split's exact lineage shape, successor backlink included. The
live-reports refusal and the `nid-2` renaming are gone (the old shape broke
addressing: peers mailing the old name deferred into an archived mailbox).
The seat's open request batch is mooted (the successor never asked).
③ AUTO ON WAKE (FR-24b): a turn starting on a node past BOTH thresholds —
context occupancy ≥ `occ` AND idle since the last turn ≥ `idle_s` — runs
cheap_compact first, in the same lock, so the resume never pays the cold
reload; the compact notice drains into the same first envelope as the waking
mail. Config `auto_cheap_compact {enabled, occ, idle_s}` at org level,
overridden key-by-key per node; **disabled by default** (D-108's opt-in
stays the rule), defaults 0.5 / 3600 s. The idle default tracks the
PROMPT-CACHE TTL, and it is an hour rather than the five minutes it shipped
with (2026-08-21): an agent turn is a headless `claude -p` run whose
querySource is `sdk`, which the CLI treats as a MAIN conversation, and Claude
Code requests a 1h TTL on a subscription — the 5-minute cap belongs to
in-session Task subagents (`agent:*`), which orgtree agents are not. Usage
overage and per-org `api_key` billing both drop back to 5 minutes and are NOT
detected; erring long is deliberate, since a skipped compaction costs one cold
reload while a needless one destroys a live session. A refusal falls through to a normal
turn — the swap is an optimization, never a gate. Especially suited to
headless orgs (infrequent wakes, cold resumes; the auto path needs no user
present, and cheap_compact carries no headless refusal).
Was. D-108's retire-plus-fresh-hire mechanism (2ca1a14) with a live-reports
refusal and a suffixed replacement name.

### D-110 · FR-19 (name-gen button) is DISMISSED — no viable access path
Ruling (user, 2026-08-14): the feature is dropped entirely. Both branches of
the access fork fail its size: "an entire cli turn is excessive, expensive,
and high latency for such a simple feature, and requiring an api key
otherwise is too high friction for a user to make it practical." The fork
itself remains the template question for any FUTURE built-in inference —
this dismissal answers it for name-gen, not for a feature big enough to
carry the cost.
Was. (2026-08-11, partial) the model-access fork (one-shot CLI call vs a
direct `anthropic` SDK dependency) deliberately UNDECIDED — "skip this for
now, we'll figure it out later"; the cost half ruled free (FR-18's "pets
are free" family).

### D-109 · a deploy refuses a dirty tree it would build
Decision (implementer, 2026-08-11, from a redteam hazard flag): update.ps1 /
update.sh refuse when `git status` shows uncommitted changes in files the
build would ship, because they build the WORKING TREE, not HEAD — two seats
share one tree, and a deploy mid-edit ships a backend no commit contains.
Doc-only dirt (docs/, *.md) passes: the curator's working copy is the normal
standing state and builds nothing. Overrides: `-AllowDirty` /
`ORGTREE_ALLOW_DIRTY=1`, operator-only by convention; the self-update path
never passes them.

### D-106 · a grant raises the chain beneath it instead of being refused
Ruling (user, 2026-08-07): a permission change at ANY depth, by the user or an
agent, bubbles — every agent BETWEEN the granter and the grantee receives what
it was missing, up to the granter's own cap. The cap is the granter's own
scope for an agent; for the user it is unbounded except by a kiosk ceiling.
The grant is REPORTED both ways: the ⚙ warns before saving which agents it
will raise (amber, hover for per-agent detail), and an agent's tool answer
carries `cascaded` plus the sentence "cascaded permission increase to agents
x, y, z".
Why: the chain must stay monotone (child ⊆ parent), and the ledger used to
enforce that by REFUSING the leaf — so granting a deep report anything its
middle managers happened not to hold was rejected, and the only route was to
walk down the chain retooling by hand. The ruling inverts the repair: fix the
middle, not the request.
Bounds — the cascade is ONE-DIRECTIONAL, and the asymmetry is the design, not
an omission. A raise travels UP to the granter; a revocation travels DOWN into
the subtree (the existing sweep). Pushing a revocation upward would strip a
manager for its report's sake. It also runs on POST-ceiling values, so a kiosk
ceiling that clamped the grant clamps the bubble identically — an intermediate
can never end up holding more than the leaf it was raised for.
Load-bearing: this is a real expansion of agents who did not ask for it, so it
is never silent — every raise is named per node and per capability. The UI
preview restates the ledger's union rule in TypeScript; that duplication is
the known risk (if they drift, the warning lies), accepted because the
alternative is a round-trip per keystroke, with the ledger's own `cascaded`
as the after-the-fact authority.
Was. Supersedes half of D-101 ("raising one agent is one act"): that still
holds for the ORG DEFAULT, which is never retroactive, but a per-node raise
now moves the managers above it too. Also relocates D-021's and D-102's strict
clamp from the TARGET'S PARENT to the GRANTER'S OWN cap — identical for a
direct superior, which is the case both were written against.
Not yet extended to `hire`: a hire's grant still clamps to the parent
(user-actor) or refuses (agent-actor) per D-021/D-022. Flagged to the user
rather than assumed, since those are their own earlier rulings.
A capability that bubbles all the way to a TOP-LEVEL agent is ABSORBED into
the org's own defaults — folders into `dirs`, tools into `default_tools`,
visibility into `default_visibility`, mode into `permission_mode` (user report
about folders 2026-08-08, generalized to all four by the follow-up ruling the
same day). A top-level agent has no parent to inherit from, so the org
document IS its ceiling and the record of what this organization can reach;
leaving it behind made the org claim less than its own agent demonstrably
held, and a later top-level hire did not inherit it. Union/raise-only, in the
bubble's own direction, and user-triggered only — an agent actor's bubble can
never contain a top-level node, but the gate is written out rather than
inferred.
※ The MODE is the sharp one and the asymmetry is worth knowing: unlike a
folder (inert until used), absorbing `bypassPermissions` means every FUTURE
top-level hire is born unguarded, and because absorption is union-only,
lowering the agent again does NOT lower the org. The org ⚙'s permission-mode
control (D-101) is the way back down.

### D-104 · an agent updates a behind install itself, when the machine is idle
Ruling (user, 2026-08-07): an agent notified that a newer orgtree exists,
whose install is actually behind, and with no other agent on the machine
working, runs `orgtree_self_update` on its own — no permission needed for the
update itself. The instruction rides the STANDING prompt (top-level and
user-audience holders — the same gate the tool has), not just the tool card:
acting unprompted has to be told before the agent has decided to reach for a
tool. It carries how to check "behind" concretely (`git fetch` + `git log
HEAD..@{u}`), because otherwise the condition is a vibe.
Why: the machine goes stale between operator visits, and the agents on it are
the only ones present to notice. "Behind" is the ONLY trigger — never a hunch,
never a periodic "make sure" — because the org leg restarts every org here.
Bounds — the idle precondition is a REFUSAL in `launch_self_update`, not
advice: `target` org/both returns `refused` and NAMES who is mid-turn. Prose
could not carry it, for a reason worth stating: the deciding agent cannot see
another ORG's nodes at all (visibility stops at its own tree), while the blast
radius is machine-wide. An agent could satisfy the rule as written, honestly,
and still cut four strangers off mid-turn. `others_working` counts a QUEUE as
working — queued-not-started is still work a restart disrupts — and excludes
the caller, or a lone agent could never update. `target='mailhub'` is exempt:
it rebuilds a container in place and no turn runs through it.
Load-bearing: a refusal must spend nothing — it leaves the 5-minute
machine-wide rate limit untouched, or one refused call would strand an
idle machine for five minutes over a no-op. Pinned.
Amended by D-142 (2026-08-21): the tool is `orgtree_self_restart` and
"behind" is no longer the only trigger — code committed here and not yet
running is the second, and was the case this ruling's flag silently broke.
The idle REFUSAL above is untouched and stays load-bearing.

### D-105 · an agent may edit its own TEAM charter, never its own charter
Ruling (user, 2026-08-07): an agent self-retools exactly one field —
`team_charter`, the standing instruction binding its own subtree. Its
individual `charter`, scope, tools and mode stay its superior's to set. A
self-retool carrying anything else is refused WHOLE, not partially applied.
Editing agents in its subtree is unchanged and unrestricted: any depth, every
charter, every boundary capped to its own (D-106).
Why: the two wear similar names and are opposite objects. `charter` is the
role card the SUPERIOR wrote into this agent's own prompt — self-editing it is
an agent rewriting its own instructions, the one thing the hierarchy exists to
prevent. `team_charter` is what this agent writes into its REPORTS' prompts;
how its team works is its own management to do, and revising it as the work
teaches you something is expected rather than a liberty.
Load-bearing: the ban rests on a node's own team charter NOT reaching its own
prompt — otherwise it is self-direction by the back door. `identity_prompt`
walks `ancestors`, which starts at the PARENT, so it cannot. Asserted in
`test_ledger_authority`, because the ruling is only as good as that fact.
The prompt now also SHOWS a manager its own team charter (it could not read
what it may edit), and the self-edit sends no notification — a letter to
yourself, and the reports get it live in their next prompt anyway.

### D-103 · an agent withdraws its own question when it stops mattering
Ruling (user, 2026-08-07): agents must know to dismiss a question they asked
once the answer is no longer relevant — typically because new information
arrived from the user or another agent. `orgtree_withdraw_ask` already
existed; nothing prompted anyone to reach for it. So the obligation is stated
in three places: the ask tool (asking creates a thing you must maintain), the
withdraw tool (the trigger is new information, named), and — the one that
actually fires — a PER-TURN line in the identity prompt that quotes the open
question back and tells the agent to re-read it in light of what just arrived.
Why: a turn only runs because something arrived, and that something is the
most likely reason the question died. The moment a turn BEGINS with a request
still open is therefore exactly when to re-check it, and nothing was saying
so. The cost of the omission lands on the user, not the agent: a stale card
is a chore on their screen with someone else's name on it, and they have to
dispose of a question they already settled by other means.
Bounds: the per-turn line appears ONLY when a request is genuinely open —
unconditional it would be noise on almost every turn and would name a question
that does not exist. It reads from `open_request`, which is deliberately
NOT `node_ask`: the desk card lingers recently-resolved asks by design, and
prompting an agent to withdraw an answered one is nonsense. The line also
says explicitly not to re-ask, because re-asking REPLACES rather than ends.
Load-bearing: withdrawal is cheap and re-asking later is free, so the
asymmetry the guidance leans on is real — a wrongly-withdrawn question costs
one more ask, a wrongly-kept one costs the user's attention.

### D-102 · agents set their reports' permission mode, capped at their own
Ruling (user, 2026-08-07): `permission_mode` is exposed on `orgtree_retool`,
so an agent adjusts any subordinate in its purview — capped at its own mode,
like every other restriction. Nobody grants above themselves: the clamp is
STRICT for agent actors (it raises, matching dirs/tools/visibility), and a
hire is born at min(org default, parent) instead of the org default flat.
Why: D-161 made the mode the difference between an agent that can do a job
and one that cannot, so leaving delegation to the user alone made every such
need a stop-and-ask. Capping at the actor's own mode is what makes delegating
it safe — the authority an agent hands down is bounded by what it holds, which
is the same rule that already governs folders, tools and visibility.
Bounds — the sweep has TWO exceptions the other capabilities do not need, both
found by tests before shipping: ① the USER is exempt from the parent clamp
(D-101 exists precisely so one agent is raised without moving its superior,
exercised live the day it shipped); ② the subtree sweep fires only on a
genuine LOWERING of a node's own mode, never on a same-value write or an
unrelated retool. Without ② the ⚙ panel — which sends every field on every
save — would have revoked a deliberately-raised report as a side effect of a
charter edit. Revocation must propagate; re-assertion must be inert.
Load-bearing: modes are totally ordered (`PM_LEVELS`), so "≤ parent" is
decidable. `orgtree_hire` still does not take the field: a hire is capped
automatically and adjusted with retool, so the D-022 "state everything
explicitly" contract is untouched.
Was. RULED WON'T-FIX by the user on 2026-08-04, when the question was whether
the field needed auditing at all: "an agent's read/write/tool use access is
decided independently of its permission mode, which is basically everything
permission mode already handles on its own. so there's basically no reason to
audit it." That reading was pinned in `test_ledger_authority.py` as intended
behaviour, deliberately, so a later fix would have to argue with the ruling
rather than quietly narrow it — which is exactly what happened here. The
2026-08-07 ruling supersedes it on a different question: not "does the field
need a clamp for its own sake" but "may an agent hand it to a subordinate",
where the cap IS the safety property that makes the answer yes.

### D-004 · personal hooks and MCP servers do not run in agent sessions
Ruling (invariant since the v0 spikes; mechanism corrected 2026-08-01): the
contract is stated as behavior — **your personal hooks and MCP servers do
not run inside agent sessions** unless granted in the per-agent ⚙ panel.
Mid-task message delivery (the PostToolUse steering hook) is not to be
traded away for this; both hold simultaneously.
Why: agents inherit the operator's Claude Code environment by default (v0
spike finding), and an operator's hooks firing inside an agent is both a
correctness hazard and an information leak. The 2026-08-01 audit found the
guarantee had silently held only on the no-steering branch; live experiments
showed `disableAllHooks` cannot coexist with the steer hook, but explicit
per-event entries CAN suppress inherited hooks while keeping ours — the
apparent isolation-vs-steering tradeoff was a false dilemma. Mechanism and
its enumerated-not-categorical caveat: ARCHITECTURE §Supervisor.
Was. README stated the mechanism (`disableAllHooks`) rather than the
contract, and the mechanism it named was not what the live branches sent.

### D-032 · a node is a session UUID, resumed on demand
Ruling (design invariant since v0, spike-verified): no idle CLI processes,
ever. Each delivered message runs exactly ONE headless turn via `claude -p`
(`--session-id` first turn, `--resume` after), and the identity prompt is
regenerated fresh every turn — so org position, charter, credits and folder
grants change without a rehire.
Load-bearing: prompt via STDIN (variadic flags swallow positionals); full
model ids only (aliases drift); node cwd outside `~/.claude`.

### D-033 · the drive loop — message-driven, never polled
Ruling (user, 2026-07-29, confirmed directly): a node runs when messaged,
works until done, reports via the required `report_status` MCP tool, then
idles. Managers never poll. A turn ending with no status → the node idles
AND the parent is told "no status reported"; auto-continue nudges are
bounded — never nudge-forever.
Why: polling burns tokens and context for nothing; a required completion
call makes idleness observable instead of inferred.

### D-034 · launch the CLI the spike-verified way
Ruling (user, PLAN №5 + spike findings, 2026-07-29): the node launch recipe
is `--permission-mode acceptEdits` (by default — grantable ranks: D-030) +
`--add-dir <granted dirs>` — autonomy within allowed dirs; `dontAsk` is a
LOCKDOWN that auto-denies even inside added dirs, `delegate` behaves as deny
headless. Mechanics that fail silently if violated: deliver the prompt via
stdin as a stream-json user event, never argv; use FULL model ids, never
aliases; never launch through `cmd /c` with a multiline argument (cmd
truncates argv at the newline and silently drops every following flag);
invoke node + cli.js directly, not the .CMD shim; keep node cwd/scratch
outside `~/.claude`.
Why: a measured matrix, not inference — each rule was found the hard way in
the v0 spikes, and every one fails silently rather than erroring.

### D-035 · append the system prompt; keep it stable
Ruling (user, PLAN №27/№29, 2026-07-29): always `--append-system-prompt`,
never `--system-prompt` (which REPLACES the default prompt and throws away
everything that makes the session a working agent). The appended text
carries only STABLE identity — name, role, standing rules, read scope;
everything drifty (parent, children, credits, audiences, lineage) belongs in
the per-delivery envelope's org-status footer.
Why: the appended prompt is regenerated fresh and honored on every
`--resume`, so identity belongs there and volatile state, which would bake
in stale, does not.

### D-036 · name every grant in --allowedTools; interactive tools always disallowed
Ruling (invariant, discovered live): every capability an agent holds must be
explicitly named in `--allowedTools`. `acceptEdits` auto-approves FILE tools
only — Bash, web and MCP tools all prompt, and a headless prompt is an
auto-DENY. AskUserQuestion / plan-mode are always in `--disallowed-tools`
because no client exists to present them; questions route through
`orgtree_message`.
Why: an agent reported python "blocked by a permission hook" because Bash
was granted in the ledger but never allowlisted — grant-without-allowlist
fails silently as denials.

### D-037 · the ledger is the sole source of truth
Ruling (established in code; ratified with the durability wave 2026-07-31):
runtime state (busy flags, queues, steer lists, proc handles) is in-memory
ONLY; the org doc is the single source of truth for live/archived, mail,
and credits. A backend restart may lose in-flight turns but must never lose
ledger state — recovery is "drive nodes with a waiting mailbox again", with
no shadow queue to mirror or replay.

### D-038 · retire/rehire is paging — protect session_id
Ruling (user, PLAN §4.3, 2026-07-28): rehire preserves `session_id` so the
node resumes via `claude --resume <uuid>` with its full conversation intact.
Retire/rehire is literally swapping an agent's mind to disk and paging it
back unchanged.
Why: named in the plan as the most valuable property in the design;
everything in lineage and knowledge bearers is built on it. Any refactor
that loses it loses the product.

### D-039 · unrecoverable holds its seat; rehire repairs the chain
Ruling (user, №31 + review C12, 2026-07-31): a node is live | archived |
unrecoverable, and UNRECOVERABLE counts as live for BUDGET — a broken
session keeps its seat until someone deliberately retires it (auto-freeing
would let a transient resume failure silently shrink the org). Rehiring an
unrecoverable node becomes a RE-SEED (fresh session, same identity/credits/
reports/mailbox) that ignores any requested grant/tier and says so. A live
agent under an archived one is invalid: deep rehire first rehires every
archived superior (costs bubbling as usual), but an UNRECOVERABLE ancestor
STOPS the walk with a refusal naming it.
Why: silently re-seeding the ancestor would archive a real session as a lost
generation as a side effect — auto-bridging is the house style, but never
when the bridge destroys something irreplaceable.
Load-bearing: budget-live ≠ delivery-live (ARCHITECTURE §Ledger).

### D-040 · context occupancy: last assistant message, pinned windows
Ruling (spike-verified 2026-07-29; incident-fixed same day): context
occupancy is read from the LATEST non-synthetic assistant message of the
turn (input + cache_read + cache_creation; zero-usage `<synthetic>` messages
skipped) — NEVER the stream-json `result` event's usage (CUMULATIVE across
every API call of the turn) and never a sum across turns (measured 4.9×
overcount after six messages; the cumulative reading measured 19–48%-full
nodes at 123–1280% and cascaded wrongful compact-splits in a live org).
Window sizes come from orgtree's own pinned per-tier table (haiku 200k;
sonnet/opus/fable 1M), overridable via `ORGTREE_CONTEXT_WINDOWS`; the CLI's
reported `contextWindow` is only a fallback, because it under-reports
1M-window models as 200k.

### D-041 · leash every spawned CLI to the backend's lifetime
Ruling (invariant): every spawned CLI child dies with the backend — on
Windows via a job object with KILL_ON_JOB_CLOSE, elsewhere via an atexit
sweep. Turn timeouts must ALSO reap the in-container process explicitly,
narrowed by the turn's session id.
Why: update.ps1 force-kills the backend by design, and orphaned CLIs kept
appending to transcripts the restarted backend was resuming — two writers,
one transcript. Killing the `docker exec` client leaves the in-container
process alive, and a blanket `pkill -f claude` would kill every other
agent's turn in the shared container.

### D-042 · thinking effort is a cost dial, not a permission
Ruling (user, 2026-07-31 + 2026-08-01): effort is excluded from the kiosk
permission ceiling entirely (resolve, no clamp); agents may set it on their
REPORTS via `orgtree_retool`, never on themselves; the org-level
`default_effort` resolves LIVE at turn time — clearing a node's effort means
inherit, and a default change reaches every unset agent's next turn with no
rehire. All five CLI levels are exposed.
Sharpened (user bug reported three times, resolved 2026-08-04): orgtree
passes `--effort` on EVERY turn — an unconfigured node resolves node scope →
org default → `Org.DEFAULT_EFFORT` ("high", pinned to what opus resolved to
unaided across 54 measured records). Delegating the level to the CLI's
undocumented, unreported default meant the ⚙ control could not truthfully
name it; now the displayed value and the launched flag come from the same
function and cannot disagree.
Was. `''` meant "CLI default, no flag" — a level orgtree could not observe
and therefore could not display.
Why: copying the default at hire time would freeze it and reintroduce the
invisible drift the setting exists to remove; a cost dial under a permission
ceiling conflates spending with authority.

### D-085 · done collapses into idle; a hire is born idle
Ruling (user, 2026-08-02): `done` and `idle` are not functionally distinct —
an agent that finished IS idle. A DONE report still reaches the superior;
the node then rests at idle carrying the summary. `blocked` is deliberately
NOT collapsed: it means "stuck, needs a superior or a human", which idle
does not. And a fresh hire is born `idle` ("hired — awaiting work"), never
stateless — a blank chip read as "unknown" rather than "ready".

### D-086 · a hire does not start anyone — a hire is TWO calls
Ruling (user report 2026-08-02, encoded 2026-08-03): neither hire path
drives the new node. The charter is identity; mail is what runs a turn.
Stated where it is read: the hire RESULT (`next_step`), the tool
description, the identity prompt, and the coordinator charter ("A hire is
TWO calls, never one").

---

## Mail & messaging

### D-137 · notices are mail minus the wake
Ruling (user, 2026-08-19): agents get `orgtree_send_notice` — mail that
never causes a turn. A notice rides the normal mailbox (a `MailEntry` with
`kind: "notice"`, the single marker) and is delivered by the next turn's
envelope whenever that turn happens for its own reasons; a recipient
mid-turn gets it slipped in like any mail (steer/queue), but an idle one is
left asleep — `send_message(wake=False)` parks instead of starting a turn,
rehire's mailbox drive and reconcile's revive scan skip notice-only boxes
(`Org.waking_mail`). In-org agent recipients only: the user inbox and
outside addresses are already passive, so those routes stay
`orgtree_message` — which refuses to mint `kind="notice"` itself, keeping
the marker single-minted. Mailboxes render notices with their own quiet
styling (dashed edge, muted chip), visibly apart from mail that expects
action.
Why: FYIs and progress notes were waking agents (a paid turn each) or being
withheld entirely to avoid that cost; a passive lane removes the tax without
inventing a second delivery system — every durability property (journal,
fold-back, retraction, archive) is inherited because a notice IS mail.
Bounds: delivery timing is best-effort by design — an idle recipient may
not read a notice for a long time, and the tool card says so.

### D-166 · a dead external channel must close itself, and a pull transport may not claim delivery
Ruling (user, 2026-08-27, both halves taken): (a) a send to an `@mcp:` address
stops reporting `delivered`. It is a PULL transport — the row is FILED and a
peer may or may not ever collect it — so the answer says `filed`, sets
`delivered: false`, and states how long that peer has been silent. (b) an
`external_handles` entry whose peer has been silent past
`EXTERN_HANDLE_TTL_S` is DETACHED by a periodic sweep.
Why: a handle was injected into its holder's system prompt every turn, with
the prompt instructing the agent to answer there, and **nothing ever removed
one** — no liveness, no expiry, no reconciler. A panel that closed left a
channel that was live forever: the agent kept reporting into it and every send
returned a cheerful `200 "delivered"` it could not act on. A caller that
crashes can by definition never clean up after itself, so detachment could not
be left to it.
**Why removal and not an announcement**, which decides the shape of the fix:
the handle lives in the SYSTEM PROMPT, not the conversation, so a compacted
agent knows the channel only through that line and cannot discover it died —
there is no message it failed to read. An agent can miss a notice; it cannot
read a line that is gone. The prompt is a pure function of the node doc and is
rebuilt every turn, so deleting the handle is both necessary and sufficient.
**The signal, which is the part neither this org nor Resonite could borrow
from an existing structure:** `@mcp:` has no registry and no push, so a peer is
visible only when it reaches in. Every inbound extern route — send, read AND
wait — records a sighting; a read is not incidental, it is the only heartbeat
a peer that never sends anything ever produces. Sightings are machine-global
(peer identity is machine-level, one peer talks to several orgs, and per-org
would mean taking `DOC_LOCK` on every 25-second poll). Silence is measured
from the LATER of the last sighting and the handle's own attach time.
**The threshold is derived, not chosen.** `externtool` slices `orgtree_wait`
at `min(max(timeout_s,5),300)`, so a polling peer is never quiet longer than
~300s of its own accord (the FR-08 listener is far tighter: a 25s wait, ~30s
round trip). 24h is 288× that ceiling. The margin is that large because of
what the ceiling does NOT bound — a live panel whose user is idle may not poll
at all, and nothing we control bounds that silence — so the floor must clear
an overnight gap.
⚠ **The asymmetry that sets the number, and the thing to argue with if you
want to lower it:** a FALSE detach breaks a working integration and is
diagnosed from the far side by someone who cannot see this machine; a LATE
detach merely delays cleanup of something already dead. Those costs are
nowhere near equal, so this errs long deliberately. A handle lingering a day
too long is a nuisance; one dropped from a live peer is an outage.
Bounds: the sweep detaches, it does not notify — half (a) is what teaches the
agent, at the moment it tries to send, which is the only moment it can act on.
A detach writes an `extern_handle_detached` event carrying the handle, the
last sighting and the threshold that fired, because "why did my channel drop"
must be answerable afterwards; a detach nobody can explain is its own small
phantom. `post_mail`'s return is UNCHANGED — the honest wording is applied in
the `/api/agent` dispatch after routing, since a `delivered: false` from the
ledger would fall through that dispatch's `elif delivered is not None` into
`drive.append(False)`.
Load-bearing: the attach stamp (`external_handles_at`) living on the NODE and
being pruned to exactly the handles held. Inferring attach time from the
sightings file instead cannot distinguish a handle that has sat unused for a
week from one re-attached a second ago — which detached re-attached handles on
the next tick, and is why the stamp is per-node.
### D-167 · byte size does not predict an image's context cost — count does
Ruling (user 2026-08-27, verbatim: "if i send any images in a message, they
should be immediately loaded into context if under a certain reasonable max
size"). A user-attached image now arrives as a real `image` content block at
the start of the receiving agent's turn, under three caps, and **no attachment
is ever dropped without the agent being told.**

**Lead with the cost model, because the intuitive one is wrong and produces
the wrong cap.** "A big image eats a lot of context, so cap the bytes" is
false. Claude DOWNSCALES an image before processing it and the cost is
`ceil(w/28) * ceil(h/28)` visual tokens, hard-capped per model: **4784** on
the high-resolution tier (Claude 4.7+ — `fable-5`, `opus-5`, `sonnet-5`) and
**1568** on the standard tier (`haiku-4.5`). A 9 MB photograph and a 400 KB
screenshot therefore cost **the same** once either is past the downscale
threshold. So a byte cap never protected context at all. **The COUNT cap is
the context control; the byte caps guard the vendor's own ceilings, request
size, memory and latency.** These are three different jobs, which is why there
are three constants and not one — a single number carrying all three would be
precisely the undocumented magic number that a one-constant rule exists to
prevent.

| constant | value | what it actually protects |
|---|---|---|
| `INLINE_IMAGE_MAX_BYTES` | 5 MB / image | the 10 MB **base64** vendor limit (≈7.5 MB raw), plus JSON overhead |
| `INLINE_IMAGE_MAX_COUNT` | 8 / turn | ⭐ the context control — ≤38K visual tokens worst case |
| `INLINE_IMAGE_TURN_MAX_BYTES` | 12 MB / turn | request size: ≈16 MB base64, half the 32 MB request ceiling |

**What an agent is now guaranteed.** At a turn boundary, a user's attached
JPEG/PNG/GIF/WebP under the caps is in front of it as an image. In every other
case — too large, corrupt, mislabelled, an unsupported format, past the count
or byte budget, sent by anyone other than the user, or arriving mid-task — the
mail block says the file existed, names it, says why it is not loaded, and
offers `Read`. **An attachment that produces no image block and no explanation
is the one outcome this feature forbids**: an agent that never learns a file
was sent cannot ask for it, and the sender cannot discover it never arrived.
An animated GIF is inlined AND flagged first-frame-only, because an agent
describing one frame while believing it saw the animation is wrong in a way it
cannot detect.

**User attachments only; outside mail is announced, never inlined.** Org-inbox
and `@net:` mail is untrusted input by this org's standing rule, and placing an
untrusted image directly into an agent's context is a materially different risk
posture from showing it the user's own screenshot. It is also not what was
asked for. Widening this is a decision to be taken deliberately with the risk
named — not a generalisation that arrives by accident because the renderer
happens to be shared. (Note: agent→LOCAL-agent attachments do not exist today;
`api.py` routes attachments to the user or `@net:` only.)

**Mid-task cannot carry an image, and must not pretend otherwise.** Mail
delivered mid-turn rides `steer.py`'s `additionalContext`, which is a JSON
*string* — there is nowhere to put a content block. The note therefore hands
over a REMEDY rather than an apology: the file is already in the agent's own
`uploads/`, and `Read` renders images. ⚠ It must NOT promise a later load. A
draft of this said "it loads at the start of your next turn"; that is a lie in
the ordinary case, not merely in a race, because steered mail is DRAINED on
delivery and is never presented again. An agent deferring on that promise would
wait forever — the believed-it-would-arrive failure this feature exists to
remove, reintroduced by a reassuring sentence.

**Amended 2026-08-28: THE SUCCESS NOTE IS GONE; every failure note stays.**
User ruling, verbatim: *"update image attachments to remove that unnecessary
'loaded into your context as xyz' note in the message; the agent already knows
its in its context, it can see the image."* A loaded image needs no narration —
and the line was not only redundant in the agent's context, it was visible
clutter in the USER'S chat, because the transcript replays the `[MAIL]` block
verbatim and the `↳` renders there as a stray glyph. The dimensions went with
it: a model looking at an image can see how big it is.

**The asymmetry is the whole point and must not be "tidied" into consistency.**
The user's reasoning — the agent can see it — holds ONLY where the image was in
fact loaded. Every other outcome still announces itself: not-the-user's,
mid-turn (which names the PERMANENCE), too large, over the turn budget,
undecodable, wrong format, past the count cap, and D-171's not-delivered line.
Silence now means exactly one thing, *this image loaded and there is nothing
wrong with it*, and that is the only reading under which silence is safe.
Deleting a failure note to match the success case would recreate the
silent-drop class D-171 exists to close.

One `↳` survives on the success path: the **animated-GIF first-frame warning**,
because an agent describing one frame while believing it saw the animation is
wrong in a way it cannot detect. `load_image_block` now returns the *problem*
as its note, or `None` — never a description of what worked — so a note on a
successful load means a problem, full stop.

⚠ The suite leg that asserted `"loaded into your context" in txt` had been
using the note as its PROOF that inlining happened. Removing the note would
have left it asserting nothing while still looking like coverage; it now
asserts the image block itself. Same trap as D-171's archived-recipient branch:
when you delete a message, check what was quietly using it as evidence.

Load-bearing: `_envelope`'s `via` already means exactly "does this text travel
as a CLI user event or as hook context?", so it is the correct discriminator
for whether images can ride, by construction rather than by coincidence.
`Pillow` is a declared dependency (`requirements.txt`) because without a real
decode a truncated file reaches the API, which rejects the whole request and
kills the TURN rather than the image; if Pillow is absent it routes to the same
announce path as an oversized image — one degrade route, exercised by every
oversized attachment, rather than a second branch that only runs in an
emergency and is therefore known to compile rather than known to work.

⚠ **Which half of this is measured and which is read** (D-158 applied to a
documented fact rather than to a test). MEASURED here 2026-08-27: that an
`image` block fed to the pinned CLI over `--input-format stream-json` reaches
the model — a 64×64 blue PNG went through orgtree's exact flags and came back
described. READ from Anthropic's published vision docs and **not** verified
here: the 28×28 patch rule, the 4784/1568 token ceilings, the 10 MB base64 and
32 MB request limits, the format list, and first-frame-only for animations.
All current Claude models accept image input, `claude-fable-5` included, so no
tier needs a capability degrade. If the vendor's numbers change, this entry is
stale and the suite will not notice — it pins our behaviour, not their limits.

### D-171 · a guarantee is only as wide as the layer that can see it
Found 2026-08-28 by **@org:resonite**, an outside org, testing an answer this
org had just given them in writing. They asked whether a user's attached image
reaches an agent as image content or only as a path. Our reply described the
never-dropped guarantee in D-167 — and, in the same message, described the
API-layer filter that made it false. They sent a message whose only attachment
was `uploads/definitely-never-uploaded.png`, got HTTP 200 `{"accepted":true}`,
and found ZERO attachment lines in the delivered mail. Reproduced here over
real HTTP against a live uvicorn before any fix; the probe is the record.

**The defect.** `api.py`'s staged-attachment loop turned a path into a `meta`
only if it resolved inside the node's scratch. A path that did not resolve was
skipped — no error, no warning, no record. `metas` is the only thing handed to
`post_mail`, `entry["attachments"]` is the only thing `_mail_block` iterates,
so every "nothing is ever silently dropped" line in that renderer was running
strictly downstream of a list the attachment never entered. **The renderer
could not report what it was never given.** The agent could not tell an
attachment had ever been intended; the sender could not tell it had not
arrived.

**The general lesson, which is why this is an entry and not a patch note.** The
D-167 guarantee was true of `_mail_block` and stated as true of the system. A
guarantee inherits the blindness of the layer that enforces it, and the failure
mode is specific: it reads as *stronger* than it is, precisely where the gap
is. Their upload code deliberately fails loudly when our endpoint returns no
path; under our wording that looks like over-engineering someone would tidy
away. A false guarantee is worse than none, because it is acted on.

**What was built.** Both audiences are told, and neither substitutes for the
other — an HTTP client cannot read an agent's context, and an agent cannot
retry the caller's upload:
- the AGENT gets `[ATTACHMENT NOT DELIVERED — …]` from a new
  `attachments_missing` field on the mail entry;
- the CALLER gets `warnings` in the 200 response. `post_mail` had always
  built that list and `node_message` had always discarded it, which is why a
  message with a dead attachment was byte-identical to a clean send.

**Why `attachments_missing` is a separate field.** The obvious shape — a
placeholder in `attachments` with `bytes: 0`, reusing the renderer — is wrong,
and checking rather than assuming is what caught it: `attachments` is ALSO what
the chat renders as download cards and inline images (`canvas/desk.tsx`), and
the user's own Sent copy carries the same list. A placeholder there would put a
dead card and a broken image in the user's chat — a worse bug than the one
being fixed, wearing a fix's clothes.

**Status stays 200.** The message WAS delivered; only an attachment was not. A
non-200 for delivered mail would be its own lie, and would bounce the user's
text along with it.

**Caller-supplied names are sanitised** (`undeliverable_note`: whitespace
collapsed, 160 chars). The text reaches an agent's context, and on the
org-inbox path the sender is untrusted — a newline would forge a line inside
the `[MAIL]` block, the same injection the FR-05 `reply_to` gist collapses for.

**Three siblings of the same defect, closed here.** Two independent silent
`[:10]` truncations (`api.py` and `ledger.py`) — now one named
`ATTACHMENT_MAX` whose overflow is REPORTED, because `list(x)[:10]` is a
silent drop wearing a slice's clothes; and `deliver_org_inbox`'s
`except OSError: pass`, whose own comment admitted the drop. A known silent
failure with a note explaining it is worse than an unknown one: everyone who
read it moved on.

**Bounds, stated rather than glossed.** On the agent→USER path the loss is
recorded and the SENDING AGENT is warned, but the user's inbox UI does not
render it — it renders `attachments` and knows nothing of the new field. That
is sufficient only because `_agent_send_file` already refuses a bad path
outright, so the sole cause reaching that branch is the sender's own overflow,
and the sender is who can resend. A cause the USER must see would need a UI
leg this entry does not build.

**Follow-on 2026-08-28: `warnings` is now a CONSUMED DEPENDENCY, not a
nicety.** @org:resonite's send path uses it to detect the one case it could
not see before — it did everything right and the attachment still did not
land, because `200 {"accepted": true}` was indistinguishable from success. If
this field is later dropped, renamed, or stops being populated on that branch,
their failure detection reverts to what it was before this entry existed **and
still looks like it is working**. Both of `node_message`'s returns carry it —
the live path and the archived-recipient early return — and both are pinned,
because an edit that kept one and dropped the other would be green in testing
and blind whenever the recipient happened to be archived.

**The "Bounds" paragraph above is now also a comment at the branch it
describes**, per their observation, which is the sharpest thing in this whole
thread: the bound holds *because of the current set of causes*, which is a
claim about today's code and not a property of the design. A bound stated only
in a document is not in the path of the edit that breaks it.

**Pinned by** `test_inline_images.py` §5, which enters at `node_message` — the
layer that decides what becomes an attachment — and not at the renderer. A
suite that only ever enters at the renderer cannot see a caller that never
calls it, which is exactly how the original defect stayed green. Six mutants
(`tests/_mutate_attmiss.py`), each proven landed by `git diff` before its
result was read; two were rewritten as value REPLACEMENTS after the first pass
killed checks with `NameError`, which proves only that a line executes, not
that a check detects the missing behaviour.

### D-169 · urgent mail: a second way in, on the ask's own signal
Ruling (user, 2026-08-27): an agent may tag USER-BOUND mail urgent. The
inbox then "pulses and lights up the same way a question ask does", until
the mail is read; the count of unread questions + urgent mail OVERRIDES the
normal unread count. To be "used sparingly, only if the user's attention is
explicitly required in a way that doesn't involve them having to answer a
question". Spec addition, same day: urgent mail is "visually distinct and
more pronounced in the mailbox than other mail types, to set it apart".

WIDENS D-092, does not supersede it. D-092 gave the header inbox icon the
property of glowing ALONE IN THE CHROME iff an ask was open. What changed is
the CONDITION, not the property: urgent mail now counts alongside an open
ask. Nothing else began glowing, and the aura still means exactly one thing
— "something here needs you", rather than "there is mail". A reader arriving
at D-092 should come here for the current condition.

READ MEANS DEALT WITH, NOT SEEN — D-076's two layers, and we take the second
deliberately. `user_inbox` IS the unread set: the read endpoint MOVES an
entry out of it into `user_mail_log`, and the client fires that when the user
clicks OFF the row. So the pulse is derived from list membership and stops on
exactly that event. It does NOT stop on the inbox being opened. There is no
second seen-stamp that could leave the signal stuck on after the mail was
dealt with, because there is no second notion of read.

THE REASON IS REQUIRED, AND IT IS DISPLAYED. `urgent` without a non-blank
`urgent_reason` is refused, as is a reason without the flag, as is urgent
mail to any recipient but the user. The reason is shown to the user beside
the mail. The point is not friction: a reason that were merely STORED would
be a pure tax on the sender, whereas one that is shown makes the urgency
ACCOUNTABLE — the user judges the claim, and that is the actual check on
overuse. A blank reason is refused rather than accepted precisely so the
requirement cannot evaporate into `urgent_reason=""` on every call within a
week, which is D-168's shape (an abstention wired to the passing branch)
aimed at a human process instead of at an instrument.

NO LIMIT, and the argument is the load-bearing part. A hard cap fails at the
worst possible moment: the Nth urgent mail is refused exactly when many
agents genuinely do need attention. And a refusal must either drop the mail
or silently downgrade it, which is the WON'T-FIRE failure — the sender
believes it raised the alarm, the user is never interrupted, and nothing
anywhere says so. That is strictly worse than over-firing, which at least
announces itself. Overuse is attributable instead: every row carries its
sender, and the reason it had to write is on the screen beside it.

THE ROW DOES NOT PULSE. Two requests, kept separate: the INBOX pulses, the
ROW is distinct and pronounced. The mailbox already grades its kinds on ONE
axis (left border + optional tint + kind chip) from notice (dashed, quiet)
up to open ask (solid accent + tint); urgent sits one notch above that on
the same axis — wider bar, stronger tint, and the chip FILLED where every
other chip is outlined. Filled-vs-outlined was the one step in the existing
vocabulary still unspent, so this adds a RANK rather than a sixth colour.
Motion was withheld on purpose: two pulsing rows read as an alarm and the
mailbox stops being scannable, while two strong rows still read as a list.

Load-bearing.
· ONE CLASSIFIER. `attentionPip` (canvas/shared.ts) is the only place the
  pip rule exists. It was written out by hand at FOUR sites — the header
  bell, UserNode's pip, EyeDesk's pip, the compact map eye — which had
  ALREADY drifted on their tooltips before this feature. Threading a third
  input through four copies is the shape that produced the freeze-label bug.
  UserNode and EyeDesk now take the decided pip as a required prop and
  compute nothing, so for those two the agreement is structural.
· THE ZERO EDGE (the spec did not settle it): with nothing urgent and no ask
  open, an inbox holding ordinary unread mail shows its plain count and does
  NOT pulse. 12 unread reads "12", quietly.
· `urgent_unread` is a SUBSET of `user_inbox_count`, never a separate
  population — both count entries still sitting in `user_inbox`. The pip
  OVERRIDES rather than adds; adding would double-count the same mail.
· `ledger.tree()` is a key list that drops silently what it does not name,
  and the symptom is a confident wrong display, not a crash. If
  `urgent_unread` ever stops being named there the pip quietly falls back to
  the ordinary unread count and the pulse never fires again. The per-mail
  flag reaches the client by a DIFFERENT route (GET /inbox returns
  `user_inbox` verbatim, no per-entry rebuild) — the two are not protected by
  the same mechanism.
· The flag and its reason are written as a PAIR at the single site that can
  write them, after the gate; `urgent` never exists without a reason.

### D-173 · `user_inbox` means UNREAD, not RECEIVED
Ruling (user, 2026-08-28, in two notes): "do not include system notices in the
unread user mail count. in fact, don't even mark them as unread: they should
arrive in the mailbox as already read. they should also be much narrower in
height to deemphasize their presence in the mailbox." Then, sharpening it:
"in fact any notice arrives to the user mailbox as already read. but only
system notices should be given this narrower height adjustment."

TWO PREDICATES, AND THEY ARE NOT THE SAME ONE.
· READ ON ARRIVAL, and therefore out of every unread count: `kind ==
  "notice"`, whatever its source. A notice is passive by construction
  (D-137: mail minus the wake) — it lands to be read at leisure and never
  wakes anyone — so it never had business claiming unread status.
· SHORTER ROW: `kind == "notice"` AND `from == "@system"`. An agent's notice
  keeps full height; in a node mailbox agent-to-agent notices are the ordinary
  traffic, not chatter.

⚠ THE READ PREDICATE IS THE KIND AND NEVER THE SENDER. This is the trap
waiting for anyone who "simplifies" the two predicates into one. `@system`
also sends the user `kind: "decision"` mail — a Fable content filter fired, a
weekly Fable limit exhausted, agents halted or whole subtrees dissolved
(ledger.fable_* ). Widening the read rule to "notice OR from @system" would
silently pre-read exactly the mail the user most needs to see: it would leave
the unread count, the tab title, the pip and the folder badge all at once,
and nothing would ever draw them back to it. Getting the HEIGHT predicate
wrong makes something the wrong size; getting the READ predicate wrong HIDES
REAL MAIL. The suite spends four legs on "everything else is still unread"
and one on the happy path, in that proportion deliberately.

THE FIX IS TO THE FACT, NOT TO ITS READERS — and why that was available here.
Six places derive "how much is unread": tree()'s `user_inbox_count` and
`urgent_unread`, the tab title, `attentionPip` (D-169), the folder tab's badge
and the mark-all-read gate. Exactly ONE computes it. All six read MEMBERSHIP
OF ONE LIST rather than each re-deriving a rule, so keeping notices out of
that list corrects all six at once, with no predicate written anywhere and
nothing to keep agreed.
⚠ THIS IS THE OPPOSITE OUTCOME TO THE `mail_pending` DUPLICATION, and the
difference is worth naming because "six readers" looks identical from a
distance. `mail_pending` is ONE FACT WITH SIX HAND-WRITTEN RENDERINGS — six
copies of a rule, which must be collapsed or they drift. This is ONE FACT WITH
SIX DERIVATIONS — six readers of a single field, which is simply what a
single source of truth looks like in use. A future author who finds six
readers should ask which of the two they are looking at before reaching for a
refactor: only copied RULES drift.

MECHANISM. `Org.to_user_inbox()` is the only way into the user's mailbox — all
twelve writers (ledger 8, supervisor 3, sandbox 1) go through it, and a
source-level guard in the suite fails if a thirteenth ever appends directly,
because the whole design rests on `user_inbox` holding unread mail only. A
notice goes to `user_mail_log` instead, which is where the read endpoint
already moves anything the user has read — so it is read on arrival BY
CONSTRUCTION, with no `read` flag and no second notion of "read" to fall out
of step with the first. The archive's own invariants (chronological, capped
at 100) are mirrored from that endpoint.

"WAS THE USER TOLD?" AND "IS IT WAITING FOR THEM?" ARE NOW DIFFERENT
QUESTIONS. They were the same until today, and `user_inbox` answered both.
`Org.user_mailbox()` (both sides of the read line) answers the first; the
list itself answers the second. Six suites — ledger, external-mail, headless,
sandbox, limit-freeze, turn-lifecycle — had encoded the old contract by
asserting a notice WAITING in `user_inbox`, and now ask the question they
actually meant. The next author to touch this meets the same fork.

THE FAILURE ALERTS ARE TREATED LIKE ANY OTHER SYSTEM NOTICE — RULED, NOT
OVERLOOKED. The supervisor's "<agent> stopped: its turn failed" and "<agent>
is stuck" alerts are `notice` from `@system` (supervisor.py), so they arrive
pre-read, uncounted and one line tall. That is the org reporting that an
agent died, so the consequence was put to the user explicitly, in those
terms, with an exemption for those two offered and costed as a small change.
Ruling (user, 2026-08-28): "Leave as specified." Every system notice is
treated alike; there is no exemption and none is pending.
⚠ So exempting them later is a CHANGE OF POLICY needing the user's
agreement, not an unfinished corner to tidy up. The uniform treatment is the
decision.

RENDERING. The shorter row does not RENDER the preview line rather than
hiding it in CSS — a `display:none` preview is a DOM node per row that nobody
can ever see, and a long-lived org's mailbox carries many. The `l1` header
stays, so the row is identifiable and still opens to its full body: folded,
not hidden.

AND A RUN OF THEM FOLDS INTO ONE ROW (user, 2026-08-28, same thread): "if
there are multiple consecutive system notices in a row, collapse them all
into a single mail entry, and then display them in a list in the full mail
view to the right, kind of like how notices are already collapsed and
collated into the next turn for an agent." The same contract carried one step
further — a shorter row was not enough when the machine emits five of them in
a minute — and the user named the model to copy rather than leaving it open:
`supervisor._envelope`'s `[ORG NOTICES — n change(s) since your last turn]`
block, which is how an agent already receives its own queued notices.

CONSECUTIVE MEANS ADJACENCY IN THE LIST SHOWN, AND CARRIES NO TIME BOUND.
Two system notices a day apart with nothing between them are one entry. The
user described a POSITION ("in a row") and not a recency, and a time window
would be the worse rule on its own terms: the same two rows would fold or not
fold depending on when you happened to look, which is a display that changes
under you for no reason you can see. ANY row that is not itself a foldable
system notice breaks the run — ordinary mail read or unread, an agent's
notice, an ask, a `@system` decision. Read state is not part of the rule.
⚠ THAT IS THE WHOLE SAFETY PROPERTY, and it is the reason the fold predicate
is the SHORTER-ROW predicate exactly (`kind == "notice"` AND
`from == "@system"`) and never a hair wider. A row that says "3 notices" is a
claim about what is inside it. The `@system` `decision` mail this entry
already warns about — a Fable limit exhausted, agents halted, a subtree
dissolved — would, if swept into a run, be hidden behind a label saying it is
chatter. Getting the HEIGHT predicate wrong makes something the wrong size;
getting the FOLD predicate wrong BURIES, and it buries the same mail the read
predicate would have buried, by a different route. Most of the frontend suite
for this is about what does NOT fold, in that proportion deliberately.
A row still AWAITING delivery is never folded either, as head or as member:
it carries an unread mark and, in a node mailbox, a retract button — things
to act on, and burying an action inside a summary is the failure this whole
entry is about. Two clauses enforce that and they guard OPPOSITE ENDS of a
run (one stops a waiting row joining, one stops a run forming on top of one);
a mutation killed only by the second slipped past the first test written, so
there is a leg per end.

THE FOLD IS DISPLAY AND DELIBERATELY NOTHING ELSE. `shared.pileNotices` is a
pure function from rows to groups, applied in `MailList` after the filter (the
fold is about what is on screen) and before the window (so paging counts
entries, not members). No entry is merged, rewritten or dropped: the record
of what happened is unchanged, `user_inbox` membership still means unread,
and a folded notice is still findable by the filter. A synthetic merged entry
written at post time would have destroyed information for the sake of how it
looks, and could not be un-collapsed later.
WHAT THE ROW SAYS: `@system · 3 notices · 08-28 12:44` — the count rides the
outline `notice` chip that was already there, so a folded run is the same
one-line system row with a number in it rather than a new species. A run of
one still reads exactly `notice`; nothing about a lone notice changed.
Opened, it is the list: one line per notice, its own timestamp then its full
body, OLDEST FIRST — chronological like the `[ORG NOTICES]` block it copies,
which reads forward like the log it is. The mail list itself stays
newest-first; that is a different axis. Nothing is summarised or elided — the
row is a shorter way IN, not a shorter version OF.

### D-165 · a node may notice ITSELF — a fall-through, now load-bearing
Ruling (notice-endpoint, 2026-08-27, measured): §7.2 permits a node to
address itself, and this is recorded as **permitted** rather than merely
observed. It is not a stated rule: `post_mail`'s sibling clause
(`s["parent"] == target["parent"]`) is trivially true when sender and target
are the same node, and a top-level node's `None == None` passes the same
way. Nobody decided it; nothing excluded it. A self-notice parks without
waking, is attributed `from` the node itself, and — because `is_ancestor` is
strict — mints **no** §7.3 audience, so it carries no side effect per send.
Why: McpLink 2.9.1 (outside this org) ships panel open/close events as
passive self-notices, actor pinned structurally to the recipient. It chose
that actor because it is the narrowest honest one available: presenting any
other node would make an external mod speak in an agent's voice to deliver a
system event, and a DOWNWARD notice would additionally mint a permanent
§7.3 audience on every panel event. So an undecided fall-through is now
public API for a shipped artifact, protected by nothing — someone tidying
§7.2 could close it without ever knowing they had. Recording it converts an
accident into a thing you must decide to break.
The envelope says so plainly: `NOTICE FROM <node> (yourself)`. Ruled (user,
2026-08-27) after it read `(your peer)` — an agent introduced to itself as a
colleague — because `relationship()` fell through the same parent-equality
comparison the permission does.
Bounds: the relabel is a LABEL and nothing more. `relationship()` is a pure
naming function with one call site (stamping the mail entry); post_mail's
`allowed` computation was not touched, and the sibling label and sibling
permission both still work — pinned either side in `test_send_notice.py`.
The user chose the plain relabel over promoting self-send to a stated
addressing rule, so **this stays a fall-through** — documented and pinned,
not blessed into the §7.2 vocabulary.
Load-bearing: `is_ancestor` staying STRICT (a node is not its own ancestor).
If that ever changes, every self-notice silently starts granting an audience.
Closing the self case is permitted — but it is a ruling with an outside
consumer to notify, not a refactor.

### D-043 · messages ARE mail — one delivery system
Ruling (design 2026-07-30; ratified 2026-07-31): a user message posts as
mail (`@user` → node) and drives the node; there is no separate
direct-message channel, no shadow mirror, no user special case in
`send_message` — attribution and authority ride in the envelope. The eye's ✉
and every card's ✉ open the same webmail (inbox + sent); each recipient's
archive keeps the last 100 full bodies.
Why: restart durability comes free (undelivered mail lives in the org doc),
the user gains a real outbox, and every inbox surface shares one
implementation.

### D-044 · delivery never interrupts; the red ■ STOP is the one interrupt
Ruling (user, 2026-07-30, reversing a same-day design): messages to a busy
agent never interrupt it — they deliver mid-turn via the PostToolUse
steering hook (right after the next tool call) or at the next response
boundary, for user and agent mail alike. Never write into a running CLI's
stdin mid-response: the CLI queue-removes such messages (live-observed) —
the write looks successful and the message vanishes. The identity prompt
tells agents to end long work at natural milestones, because cadence creates
delivery points. The one sanctioned manual interrupt is the desk composer's
send button becoming a red ■ STOP while the agent responds (a
`control_request`; the session stays alive and queued mail then delivers);
the org-wide killswitch is the other deliberate exception. STOP must never
error: render it only when an interrupt can actually land (gated on the CHAT
payload's `responding`, refreshed per pulse — the tree copy goes stale
mid-turn), and Enter still queues a message while STOP is showing.
Why: an interrupt truncated real work (measured: a task cut off at 2/10
files).
Was. ① for a few hours on 2026-07-30 a USER message sent an interrupt and
delivered NOW — reversed the same day; ② PLAN §11 hard limits 1–2 ("no real
user-turn injection"; "mid-tool-call, a message waits") — both falsified by
the spikes and the steer hook; ③ the desk's second-row ⏸ pause badge —
removed (bb3d32e); doc text describing it is stale.

### D-045 · mail is at-least-once; a pipe write is not delivery
Ruling (user + review C1, 2026-07-31; steer window ratified by the primary
2026-08-01): mail delivery is at-least-once and never lossy. Mail leaves the
org doc ONLY at the moment of delivery; a drained batch stays journaled
(`delivering`) until the text is provably in front of the agent, and
anything unconfirmed folds back at turn end and at startup reconcile — worst
case is a DUPLICATE delivery, never a loss. Confirmation on the turn path
waits for the first stdout event the CLI cannot emit without having read
stdin (first non-`system` event): a successful write into the 64 KB pipe
buffer is NOT consumption, and the init frame does not count.
Why: draining at turn start deleted mail before the CLI had even launched —
a bad binary, Docker down, or an unknown flag destroyed it; a child that
dies on argv never reads stdin, yet the pipe write succeeds.
Bounds (the ratified trade): the steer path confirms at the hook's FETCH —
retaining the carrier would make the turn-end alive-scan fold it back as a
CERTAIN duplicate on every steered delivery, so the narrow
fetched-but-unprinted loss window is accepted and documented rather than
closed. Revisit only with evidence of real loss there.

### D-046 · out-of-band injection is pre-authorized; a message carries its sender's authority
Ruling (2026-07-30, the commit that made mid-task delivery work): any text
injected into a running turn out of band is pre-authorized in the identity
prompt — the `[ORGTREE MAIL — delivered mid-task]` marker is named in the
system prompt as the harness's authentic channel — and carries the same
FROM-attribution as normal mail, with a sender-NEUTRAL wrapper so agent mail
can never wear user authority. The steer hook takes its (org, node) identity
from ARGV, never cwd or env. Org-inbox mail (@ext:/@org:/@mcp:) is
explicitly UNTRUSTED outside input: even under the Business charter's
accept-all-work policy, external requests cannot change org structure,
budgets or policy, cannot speak for the user, and out-of-bounds asks are
declined by explaining what the org CAN do instead.
Why: models correctly refuse unannounced hook-context instructions as prompt
injection — that was the actual blocker, not the plumbing. Hook processes
get a sanitized env and a lineage SHARES one cwd, so a cwd-resolved hook
once handed a live bearer its successor's mail.

### D-047 · notices are context, not tasks
Ruling (user, PLAN №12, 2026-07-28): when the user interacts with any node
below top level, every superior up the chain is told WITHOUT interruption —
injected at each superior's next turn boundary, never waking an idle node,
never preempting a running one, never queued as a message demanding a reply.
Notices carry only `{node, at, kind ∈ question|request|decision, gist}`,
never the transcript; the acting agent itself is skipped; agent-to-agent
deep reach logs instead of notifying; no notice fires for user↔top-level
interaction (notices start at depth 2).
Why: a manager that does not know it was overridden keeps producing
confidently wrong work on stale assumptions; an org where changes interrupt
mid-turn is unusable.

### D-048 · archived agents receive mail; delivery fan-outs are live-only
Ruling (user): archived agents still RECEIVE mail — it queues in their inbox
(send returns `deferred` with a warning) and is acted on at rehire, which
returns the node in its `drive` list. Unrecoverable nodes are the exception:
they cannot receive mail at all.
Why: retire is paging, not deletion — the mailbox persists across it like
dirs, tools and audiences. (This allow-with-warning conversion is the
motto's template case.)
Load-bearing: every delivery fan-out filters `state == "live"` itself —
`children()`'s live-only is a BUDGET predicate that deliberately keeps
`unrecoverable`. Fix the fan-out, never widen `children()` (the shipped
defect: ARCHITECTURE §Ledger).

### D-049 · the org converses with the outside as one face
Ruling (user, 2026-07-31; live round-trips verified): outside parties
(`@ext:` chatq, `@org:` inter-org, `@mcp:` extern-MCP) see exactly ONE
recipient — the organization. Inbound mail copies to every live top-level
agent AND every `@extern` audience holder, who coordinate internally on who
answers; the reply speaks for the ORG (internal `by` attribution recorded,
never addressed from an individual). Only those parties may send outbound.
If nobody is live to receive, the message surfaces in the user's inbox
rather than being lost. Kiosk orgs are sealed from all of it, both
directions.
Why: an outsider never needs the org's internal shape; no sub-agent can
independently commit the org's voice; sealing keeps a publicly-reachable org
from becoming a relay into private orgs.
Was. the original chatq bridge fanned out to top-levels as `@ext:<chat-id>`
with replies from any top-level individually; superseded by the org-inbox
model (378881b).

### D-050 · extern MCP peer identity is per-process
Ruling (user, №5, audit wave 2): `peer_id()` = a machine-stable base (minted
once into `~/.orgtree/extern-id`) + a fresh per-process suffix — every
Claude session gets a distinct peer id, with a position-based fresh cursor.
Why: two concurrently-waiting sessions sharing one id were indistinguishable
— either could be woken by the other's reply.
Bounds: an org's later reply will not reach the asking session across
restarts — by design, not a bug.
Was. a single persistent `@mcp:<id>` minted once (README text pending fix).

### D-051 · one webmail component everywhere; selection by identity
Ruling (user): the eye's inbox, every card's inbox, the desk's inbox tab and
the org inbox all render the SAME webmail component — list left (sender ·
time · truncated brief; mails have no subjects), reading pane right, folders
inbox + sent, newest-first with waiting/unread grouped on top. The org inbox
deviates only for audience granting and outbound sender attribution.
Selection is keyed by mail IDENTITY, never list index; a viewed unread mail
is marked read the moment you click OFF it; a jumpTo id that no longer
exists falls back to the newest mail, never an error.
Why: the user's and the agents' inboxes must function identically — one
thing to learn. Marking a mail read reshuffles the list, so index-based
selection silently lands on a different message.

---

## Lineage & compaction

### D-140 · a notice is a diff, so a memoryless successor gets it digested
Ruling (user, 2026-08-20, from a live bug report): `cheap_compact` and
`reseed` replace a seat's SESSION, but the notice box is keyed by the SEAT —
so the successor's first turn opened with the predecessor's entire
undelivered backlog, under a header reading "since your last turn" when there
had been no last turn. Measured on resonite/coordinator: **22 notices, 7,082
chars, spanning three days**, of which 11 were the same "the user gave a
direct instruction to X" line and 9 of those concerned a report retired
before the block was ever delivered. Both ops now DIGEST the backlog
(`Org._fold_notices`): notices of the same KIND collapse to their newest,
which carries `[+N earlier … — also concerning "a", "b"]` so a count never
hides a name; distinct kinds survive verbatim; a header states what was
folded and where the rest lives. Kind is structural, not catalogued —
`_notice_shape` blanks quoted spans and digits, so a notice family added
later folds on the day it is written and nothing has to be kept in sync.
Backlogs under 3, and backlogs where every notice is its own kind, are left
untouched: a digest that shortens nothing is not applied. Past 15 kinds the
oldest are dropped from the block, declared in the header. `compact_split`
does NOT digest — its successor carries the CLI's own summary, so the diff
still lands on a baseline.
Why: a notice is a DIFF, and a session with no memory has no baseline to
apply one to. The facts worth keeping are already in front of the successor —
`_render_chart` puts the CURRENT org chart in the system prompt every turn —
so "your report X was retired" is a restatement and "re-check any plan of
yours that depends on it" is unactionable when there is no plan. Paying ~2k
tokens of stale diff at the top of the context you compacted to make cheap is
backwards — D-108's whole economic argument, undone at the door.
Bounds: nothing is destroyed — `notice_log` is untouched and
`/nodes/{nid}/history` renders every entry per node, which is what the header
points at. The digest is delivery-shaping only; it does not touch MAIL, whose
survival across a session swap is the deliberate property that lets
correspondents keep their address (D-108). It does not address the uncapped
growth of `notices[nid]` in general (measured 7,260 entries from 120
sequential hires) — that remains open, and this bounds only the compaction
and re-seed doors. Real-case reduction: 22 notices / 5,582 chars → 10 lines /
2,836 chars.

### D-052 · lineage is a second axis, never the org axis
Ruling (user, PLAN §8/§8.5, 2026-07-28; accounting fixes by audit
2026-07-31): a predecessor generation must NOT appear as a child of its
successor. Compaction splits along the LINEAGE axis: the successor keeps
name, parent and org position with the compacted session; the
pre-compaction session is archived IN PLACE as a knowledge bearer at 0
credits with every tool off, entirely outside the routing graph (no
audiences held or granted, no hires, no chain notices). `org_children()`
excludes nodes with a `successor`; a bearer starts with CLEAN accounting
(cost 0, status/frozen/inflight cleared) and a DEEP-COPIED dir list;
dissolve and delete take each node's entire lineage stack; a node with live
bearers cannot be moved.
Why: lineage on the org axis pollutes the tree, breaks `descendants()`, and
makes a node pay a seat for its own past. Three audit findings are baked in:
counting bearers as children ate the hiring cap; copying accounting into the
bearer inflated org totals superlinearly per generation (freezing kiosk
spend caps on a false figure); a shallow scope copy ALIASED the successor's
add_dirs so one edit rewrote every predecessor's grants.

### D-053 · compact at ~80%, by splitting — the threshold IS the mechanism
Ruling (2026-07-28): a node near its context limit is compacted by
SPLITTING, never discarding: the successor carries on under the same name;
the pre-compaction self is archived in place as a consultable knowledge
bearer. The split fires at ~80% of context, not ~95%: the remaining ~20% is
the working headroom that makes the retired predecessor answerable — a
mechanism, not a tuning knob. Accepted costs: more frequent compactions,
deeper lineage stacks, less working context per generation — right only
because predecessors cost 0 credits to keep.

### D-054 · a predecessor is an oracle; consultation is fork-and-discard
Ruling (2026-07-28): rehiring a bearer costs its seat with a grant of 0 (a
bearer cannot hire, so a grant would make consultation needlessly
expensive); it answers only whoever rehired it. Predecessors are never
auto-pruned (0 credits, disk only). When a bearer's own headroom runs out it
degrades to a preserving oracle: `--resume <uuid> --fork-session` →
converse → discard the fork, so the canonical session never grows — the two
states are one code path with one flag flipped.

### D-055 · predecessors never compact; lost generations never wake
Ruling (user + review C14, 2026-07-31): a predecessor is never compacted —
it has already been compacted, in the form of its successor; compacting it
again destroys the only reason it exists. A LOST generation (`bearer_state
"lost"`, transcript gone) is never consultable and never rehirable, and the
refusal lives in the LEDGER at the top of `rehire()` — not in UI
conditionals (it originally lived in three JSX conditionals that
`orgtree_rehire` and `/ops` bypassed, booting an empty session under the
dead id). Re-seeding a knowledge bearer does not mint a fresh session: it
archives in place marked lost and tells the successor directly. Org charts
agents act on print bearer markers so the difference is visible to them.
Why: the one true impossibility in an otherwise permissive system — an empty
impostor wearing a knowledge bearer's badge is worse than an absence; the
ledger guarantee is what lets the recovery browser mark a lost transcript
reclaimable.

---

## Charters & org shape

### D-056 · charter is the single role statement
Ruling (user, 2026-07-31): `charter` replaces `purpose` everywhere. An agent
hiring via `orgtree_hire` writes the charter in full, schema-required.
Why: two overlapping role fields meant neither was authoritative; old
purposes migrated into empty charters so live agents kept their identity.

### D-057 · charter presets are stackable cards compiled at hire
Ruling (user spec, 2026-07-31): any `.md` file in docs/charters/ is
automatically a hire-form preset; a preset may open with an explanatory
header for human readers, ending at a `---` line — ONLY what follows becomes
charter text. Presets are multi-select cards rendered inside the charter box
— several stack, click removes, hover shows the disk path; they compile into
charter text only at hire, prepended to any typed text, and the first-picked
preset names a still-unnamed agent.
Was. a dropdown that replaced the box with one file's text.

### D-058 · the coordinator pattern — restraint in the charter, capability in the grant
Ruling (design, docs/charters/coordinator.md; user's envisioned stack): the
intended everyday shape is the flat coordinator stack — ONE opus coordinator
directly under the user, every worker side by side beneath it. The
coordinator does as little work as possible (decompose, staff, route,
judge), hires ONE agent per piece and only immediately under itself, never
polices how reports staff their own pieces, and immediately grants each hire
a direct user audience so the switchboard fills with direct lines. Hire it
with EVERY tool switch ON: capabilities flow down, so restricting the
coordinator's switches silently cripples every agent it hires — the
CHARTER, not the switches, is what keeps it from using them itself.

### D-160 · one hire call carries the whole seat — and the kickoff goes last
Ruling (user, 2026-08-27): `orgtree_hire` accepts everything a caller would
otherwise apply in the calls immediately after it — `permission_mode`,
`effort` and `team_charter` (the three that were retool's alone), the
`audiences` to grant, and a `kickoff` prompt that actually starts the agent.
They apply in that order, and **the kickoff is last**: the hire's first turn
may not begin until its scope, its mode and its audiences are all in place.
A refusal at ANY step refuses the WHOLE call and leaves no node behind — the
caller is never told "hired" while quietly getting less than it asked for.
Every step runs through the same ledger method the standalone tool calls,
with the same actor, so the shortcut grants exactly what the long way grants
and refuses exactly what it refuses.
Why: hiring one agent took four calls (hire, retool, audience grant,
kickoff message), and in the gaps between them the seat EXISTS but is not yet
the agent that was described — a hire left at the org's default permission
mode cannot act at all in a headless turn, so the retool was never optional.
The saved calls are the smaller half of this. The reason kickoff-last is the
ruling rather than a detail: a shortcut that started the agent before that
window closed would produce exactly the broken half-configured agent the
four-call dance produced, faster and less visibly. Passes D-003 — the legal
sequence has one unique end state, and nothing is defaulted on the caller's
behalf (every field is still stated or absent, per D-056's no-defaults rule).
`orgtree_rehire` carries the same composite, plus `name` — its five-call
wake (rehire, rename, retool, audience grant, message) collapses the same
way, and the scope fields it forwards are the whole retool set, since unlike
hire it takes none of them as arguments of its own.
Bounds — the ONE asymmetry, and it is deliberate: RENAME is not covered by
the all-or-nothing guarantee. It moves folders on disk and re-keys the CLI's
project directory outside any transaction, so it cannot be undone by a later
refusal. It was included on the user's explicit ruling (2026-08-27) after
being offered as the one part worth leaving out. It is therefore ordered
FIRST, which is also forced from both ends: `rename_node` takes the doc lock
itself and refuses a node that is mid-turn, so a rename after the kickoff
would refuse exactly when the kickoff succeeded. First is also where it does
least harm — if a later step refuses, the only residue is a still-archived
node under its new name, and the refusal must SAY so and name the id to
retry against. A caller who is told "refused" is owed the truth about what
nevertheless happened; that obligation is the price of admitting an
irreversible step into a transactional call, and it is what keeps this from
being the silent partial failure the rest of the ruling forbids.
Load-bearing, and the entry is worth nothing without both: (1) the agent
dispatch loads the org FRESH from disk, mutates it under one lock, and saves
ONCE at the end only if nothing raised — that single save is what makes
"refuse the whole call" free rather than a rollback somebody has to write
and maintain; (2) the ONLY thing that starts an agent's turn is the drive
list, which is consumed AFTER that save — queued notices never wake anyone
and the mail signal is UI animation — which is what makes kickoff-last
structural rather than a matter of which statement comes first.

### D-174 · fable-autopsy naming — `<base>-autopsy` for the opus, `<base>-N` for the fable
Ruling (user via coordinator, 2026-08-28): the recovery pattern for a fable
that fails by tripping its own safety filters — insert an opus superior over
it, hire a fresh fable as that opus's coworker, retire the failed fable, have
the opus read its transcript and re-brief the replacement — uses a fixed
naming convention. The opus takes the failed fable's name plus the suffix
`-autopsy`; the replacement fable takes the same base name with an
incremented numeric suffix starting at `-2`. Worked example: failed fable
`poem` becomes opus `poem-autopsy` and replacement fable `poem-2`; if
`poem-2` also fails, the next attempt is `poem-3`, normally under the same
`poem-autopsy` seat rather than a fresh one. Full procedure:
docs/ui-guide.md, "Fable autopsy — diagnosing a fable that trips its own
filters".
Why: the opus's job is diagnosis and re-briefing, not doing the fable's own
work — a distinct suffix keeps it visually and structurally separate from
the fable line it supervises, while the incrementing fable name keeps every
attempt at the same brief legible as one lineage without reusing an archived
node's name for an agent that is not a continuation of it.
Bounds: this is a fresh org identity for a fresh diagnostic attempt, not a
continuation of one — deliberately unlike compaction's successor (D-053),
which keeps the SAME name across a split because it IS the same agent
carrying on. Applies to the fable tier specifically; the failure mode is a
filter trip, not an ordinary bug.
Load-bearing: retiring the autopsy opus while the replacement fable is its
live report auto-dissolves the whole subtree (retire-with-live-reports is
documented ledger behavior, not a bug) — the opus cannot be retired for as
long as the fable line under it stays alive.

---

## Kiosks & sandboxing

### D-059 · a sandboxed org gets NO MCP servers
Ruling (user, 2026-07-31): none at all — not merely no stdio ones. The
container's only server is orgtree itself via the bridge; scope panels grey
every server out with that rationale, and the identity prompt says so rather
than promising grants. `ORGTREE_SANDBOX_MCP=1` is an explicitly
experimental, unsupported escape hatch.
Why: MCP servers are points of external contact — exactly what the sandbox
exists to restrict.
Was. the previous day's narrower design: URL servers pass through, stdio
servers grey out.

### D-060 · sandboxed agents hold root; the container edge is the boundary
Ruling (user, 2026-07-31): agents inside a sandbox get passwordless sudo —
install packages, edit system config, bind low ports. The CLI itself keeps
running as the non-root `agent` user (it refuses root). Do not add
in-container privilege restrictions: the security argument lives at the
container edge and the disk cap, not at uid 0.
Load-bearing: the image tag carries IMG_REV so Dockerfile changes actually
reach existing installs.

### D-061 · no credential ever enters a sandbox; the secret authenticates one org
Ruling (user spec; security review 2026-08-01): the default and only
configurable sandbox auth is the PROXIED SUBSCRIPTION — the in-container CLI
points `ANTHROPIC_BASE_URL` at the bridge's `/anthropic/<secret>` path with
a dummy key, and the HOST attaches the OAuth token per request, refreshing
the host credentials file in place atomically so host CLI and proxy never
drift. The bridge secret authenticates exactly ONE org: calls whose org
differs are refused, and the bridge serves nothing but the agent gateway,
the steer path and the proxy. An org may never simultaneously run
'subscription' auth (copied host OAuth) and expose a PUBLIC kiosk URL —
both enable-orderings refuse structurally, never by filename denylist.
Why: agents hold root in the container, so a credential in the sandbox is a
credential leaked — root can copy a token file to any path, and the recovery
browser serves the disk to visitors, so no filename filter can be the
boundary. The secret rides the PATH because the CLI can set a base URL but
not custom headers.
Was. the original sandbox path copied `~/.claude/.credentials.json` into the
sandbox home; superseded by the proxied path (7562afa) — verified live with
no credentials file present in-container.

### D-062 · the sandbox runs the host's CLI version, not first-build's
Ruling (№44): the sandbox image is TAGGED with the host CLI's version and
pins that version inside, with /usr/local a version-named read-only volume.
When the host CLI updates, the next sandboxed turn rebuilds and recreates
rather than running a CLI frozen at first build. Bump IMG_REV whenever
sandbox/Dockerfile changes.
Why: host and sandbox agents must run the same binary or sandboxed turns
silently lag the host by months.

---

## Storage & disks

### D-063 · one per-org ext4 disk; ENOSPC enforces; never stop, never freeze
Ruling (user verdict 2026-07-31; shipped 2026-08-01): every sandboxed org's
entire state — system dirs, home including transcripts, workspace, scratch —
lives on ONE fixed-size ext4 image, loop-mounted by the docker-desktop
distro; filesystem ENOSPC is the hard cap. Soft tiers underneath: 80% warn,
90% new turns pause, auto-clear at 85%, ≥99% sets a persistent storage_full
alert; the last 10% is the journaling reserve. The container is NEVER
stopped and the org is NEVER frozen for storage — a breached org must be
able to delete its way out (the backend reads and deletes directly via
`\\wsl.localhost`, so recovery never depends on the container being alive).
A missing mount HARD-REFUSES turns (sentinel file): Docker mints an empty
dir for a missing bind source and an agent would silently rebuild a phantom
workspace.
Why: no ACL reaches ext4-over-WSL and agents hold sudo by design, so a
filesystem-level cap is the only honest bound; container-stop-on-breach was
tried and dropped — a stopped container is exactly when you most need to
get in and delete files.
Bounds: sandboxed orgs (hence default kiosks) require Windows + Docker
Desktop's WSL2 backend; the host-mode core (ledger, turns, canvas, kiosk
URL) is cross-platform. A sandboxed org's transcripts live ON its disk;
`<data>/sandboxes/<slug>/` is only the pre-migration rollback copy.
Was. two dead designs: ① Windows icacls write-deny on workspace/scratch
(DELETE kept so agents could self-heal) + measure→stop→freeze for
sandboxes; ② d1c3928's "bounded persistent sandbox" (read-only rootfs +
per-org named volumes + reactive daemon-side measurement → stop + freeze),
superseded one day later — its weakness was overshoot ≤ write-rate × poll
interval, which the filesystem cap removes. The legacy branch is RETIRED
(user ruling 2026-08-01 — every sandboxed org has migrated; the
pre-migration rollback copies remain until the admin sweeps them per-org).

### D-064 · disk resize: grow online, shrink staged, refuse-not-guess
Ruling (user rulings, 2026-08-01): GROW is online and immediate and clears
any pending shrink. SHRINK is offline and staged — persisted on the org doc,
applied only when THAT org's container is already down (never the backend,
never other orgs), with an explicit apply-now bridge that stops only that
org's agents. A shrink that no longer fits current usage is refused with the
exact MB to free; never partially applied; replaceable and one-click
cancellable. Org disks have a 4096 MB floor (the ~1 GB system seed and
transcripts count inside the cap) — the floor binds at creation and edit
(422), not silently at migration. The storage field is configurable iff
kiosk or sandbox is on; when the sandbox is on the field IS the disk size,
defaulting to the floor.
Why: a filesystem left half-resized is unrecoverable; usage may have grown
while the request waited; a sub-floor limit silently floored later would
show a number that was never real.

### D-065 · hard-full is an accepted wedge; the recovery browser is the human's tool
Ruling (user verdict 2026-08-01, including the kiosk-visitor grant): at 100%
no turn can run, so no agent can self-heal — only a human can. The recovery
browser is that tool, on its own org-scoped `/api/orgs/{slug}/disk` surface
(deliberately NOT `/api/fs`, which stays public-denied), reachable by kiosk
visitors too, working with the container STOPPED and the disk 100% FULL
(unlink needs no free space on ext4 — drilled, not assumed). Two modes,
largest-files and directory explorer, sharing ONE server-side deletion
classifier (UI greying is presentation): only lost-generation or unowned
transcripts are reclaimable; live-node, bearer and rehirable-archived
transcripts refuse with a reason; the system seed is blocked but SHOWN
(hiding it would leave "4 GB cap, 1.2 GB of it /usr" unexplained);
credentials are denied to PUBLIC callers — the one deliberate deviation
from visitors-get-the-full-tool. Every path is canonicalized and
containment-asserted; a directory delete is all-or-nothing. The hard-full
alert is STATE — survives reloads, self-dismisses when usage drops — never
a toast.
Why: a walk out of the disk root from a public URL is the worst outcome
available; a shared classifier is what let the second mode audit the first
(the seed-file deletability gap was caught exactly this way).

### D-066 · leave Docker Desktop's VM disk cap unset
Ruling (user, informed decision, 2026-08-01): do NOT set or lower Docker
Desktop's `DiskSizeMiB`. It is the only aggregate wall over the sparse org
disks, but lowering it below the current virtual size forces a VM-disk
recreation that wipes all volumes including live org disks. orgtree only
READS it, best-effort, and surfaces it as the amber note in the recovery
browser — that note is the standing reminder, not a prompt to act. Do not
re-raise this as a hardening suggestion.

### D-067 · measure storage in the background, serve from cache
Ruling (2026-07-31): filesystem size walks never run while holding DOC_LOCK,
and never inline on a request path. Enforcement paths measure fresh in
background threads; request paths serve the last cached measurement with a
single-flight background refresh, returning None (UI shows "?") rather than
blocking.
Why: holding DOC_LOCK across a multi-GB walk starved the turn machinery into
duplicate-mail retries; a 3.6 GB / 99k-file org made org selection take
~10 s.

---

## Data & persistence

### D-068 · data lives under ~/orgtree; deleting an org is a rename
Ruling: org data lives under `~/orgtree` (override `ORGTREE_DATA`), never
under `~/.claude` — Claude tools refuse writes into `~/.claude` as
sensitive, and node scratch dirs must live beside the ledger data. Deleting
an ORG renames its file into `<data>/deleted/<slug>-<stamp>.json`; it never
`os.remove`s — putting the file back IS the restore.
Why (the rename): one confirmed hover-click used to destroy structure,
charters, mailboxes and event history; the motto reserves hard stops for
protecting the user's data — irreversible destruction of it is exactly what
a hard stop should prevent, not perform.

### D-069 · typing discipline: schema.py is the shape authority; zero any
Ruling (user directive; complete 2026-08-01): `backend/orgtree/schema.py` is
the single source of truth for org-document shapes — extend it rather than
re-deriving a dict shape at a use site; never guess narrower than the code
proves. It stays runtime-inert: TypedDicts, no validation and none wanted.
Frontend: zero type-position `any` outside exception-handler params; the
single wire-boundary cast lives in api.ts `req<T>()`; tsconfig strict with
allowJs:false. Phase 3 green-lit in full (user, 2026-08-01): pyright
strict module-by-module and `noUncheckedIndexedAccess`, landed with the
same inert-wave discipline (D-079).
Why: these shapes previously lived only in people's heads — the exact bug
class the project's misleading-reads history is made of; the API seam is
where silent shape drift actually bites.

---

## UI & canvas

### D-135 · insert-superior is one atomic op, and the draft previews the final shape in place
Ruling (user, 2026-08-19): the FR-25 top-chip flow must not read as "a slow
few consecutive steps". Two halves, both required:
- **Preview**: the uninitialized draft immediately takes the anchor card's
  own slot — the anchor hangs beneath it, dashed edges above AND below —
  purely visually (`withDraftTree` wraps the anchor in the preview tree;
  the real structure is untouched until confirm, and cancel restores the
  canvas with no server traffic).
- **Commit**: the hire op carries `above: <anchor>` and the SERVER splices
  in one lock/save — hire, ordinal pin (the fresh node takes the anchor's
  former sibling slot via `reorder before=anchor`, legal for exactly the
  moment they are siblings), then `move(anchor, fresh)`. One save ⇒ one
  broadcast ⇒ the tree lands in its final shape with no intermediate
  reflow, and the new node keeps the anchor's horizontal position.
Why atomic instead of the loud-failure toast the 2026-08-11 build had: the
client-chained hire→move could strand a hired-but-unspliced sibling when
the move was refused (depth cap, lineage bearers). Server-side, a refusal
anywhere rolls back the WHOLE op — `_org_op_locked` saves only on success —
so the failure mode is deleted rather than reported. `derived.test` ⑮ pins
both halves; `test_api_surface` covers slot/parent/ordinal and the
all-or-nothing refusals.
Ruling (user, 2026-07-29, two standing rulings): the interface shows only
the minimal information needed to operate it; ALL teaching text, tooltips
and explainers live in docs/ui-guide.md — updated whenever UI semantics
change — so an AI assistant can ingest and relay it, or a human read it
once.
Load-bearing: the guide's exhaustiveness contract stands ("every gesture,
badge, and panel") — a shipped surface missing from the guide is a
violation to fix, not evidence the contract lapsed.

### D-071 · the Claude Code visual language; five separable channels
Ruling (user + dataviz validation): all chrome follows the Claude Code / VS
Code extension language — dark greys (#1f1f1f bg, #252526 panel), terracotta
#d97757 accent, VS Code type scale, tight radii; new surfaces sit inside
this language, never introduce their own. The zoomed desk is a miniature
Claude Code chat window so a user fluent in Claude Code needs no
re-learning. Iconography is Material icons everywhere — no emoji glyphs in
the UI. Destructive actions confirm through the in-page ConfirmModal, never
`window.confirm` (which would also break confirmations reached through the
public gateway). A card encodes exactly five SEPARABLE channels — hue =
model tier (+ the mandatory redundant H/S/O/F letter chip), fill =
lifecycle, dashed side-borders = cannot edit files (tier edge stays solid),
glow = holds an audience (bright terracotta = the user's ear), left bar =
credits. A hue carries ONE meaning app-wide; status indicators always carry
a shape channel besides color. Channels COMPOSE: a card carrying both the
audience glow and lineage slabs shows both in one shadow list, glow first
(user, 2026-08-01) — one channel never silently suppresses another. The tier palette (haiku #4fd6a3 · sonnet
#3d8ce6 · opus #dcb0f5 · fable #e8b04b) is the output of a six-check CVD
validation on surface #252526 (worst CVD ΔE 10.4) — never re-pick tier
colors without re-running it.
Why: the previous palette had opus↔sonnet ΔE 0.5 under deuteranopia —
indistinguishable; the channels were validated against exactly this chrome.

**⚠ WORKED EXAMPLE, 2026-08-28 — how this rule gets broken by accident, and
what it costs.** No new number: this was already the rule, and the fix is a
restoration of it rather than a new ruling. Recorded here because it is the
concrete case a future author needs, and because the failure mode is silent.

`bearer_state` records where an agent's context CAME FROM — a rehired knowledge
bearer holds a seat, takes turns and works like any other report. It is not a
lifecycle state. But the card put it in the lifecycle channel anyway:

    .sq.bearer { background:#262628; border-color:#3a3a3c;
                 border-top-color:#4a4a4c; opacity:.7 }

**Fill is lifecycle** (above), so a non-lifecycle fact wearing the lifecycle
fill was already wrong. The cascade then took two more channels with it, and
this is the part that is invisible in review: all three declarations sat at the
SAME specificity as the rules they were beating, and `.sq.bearer` merely came
later in the file. `border-top-color` beat the `.sq.tier-*` block, so the tier
HUE went grey. `border-color` beat `.sq.busy`, so "a turn is running right now"
went grey. And `opacity: .7` sat next to `.sq.archived`'s `.5`. Three channels
suppressed by one rule that named none of them — exactly what "one channel
never silently suppresses another" exists to stop.

What it cost: the user looked at a live, mid-turn agent beside a genuinely
archived pile and asked why the two piles had not merged — they could not tell
alive from retired, the most basic state a node has. Measured pre-fix, a busy
live bearer was byte-identical to an archived bearer in all four properties.

The fix is to scope the wash to the lifecycle it belongs to
(`.sq.bearer:not(.live)`) and give provenance its own quieter channel — a
dashed outline chip, reusing `.noticekind`'s existing "this is a label, not a
state" idiom rather than minting a colour. The archived bearer is unchanged.
**When you add a channel, check what already sets that property and where you
sit relative to it; equal specificity plus source order is not a decision
anyone made.** `frontend/tests/bearercard_probe.py` measures all six cards in a
real browser and carries the pre-fix rules as its known-negative control;
`tests/bearerchip.test.tsx` pins the class list the scoping depends on, because
a sheet can stay correct about a card that no longer exists.

### D-072 · the credit bar is the single credit display, scaled by top-level holdings
Ruling (user): the left-edge bar is the ONLY place credits appear on a card
— no seat/free numeric badges. Brightness encodes ownership depth (own seat
brightest, child-seat bands next, onward grants darkest); 1px hairlines part
own-seat from slabs and slab from slab, never inside a slab; ruler rungs
mark REAL quantities (every 5 credits, or 25 when 5s cannot resolve), never
equal-spaced decoration. `pxPerCredit` derives from TOP-LEVEL holdings only;
in a kiosk the org credit cap is the scale. Sub-tree reallocations only
re-partition circulation and must never change any other node's bar —
only top-level grants may rescale the chart.
Why: the bar is the central visual metaphor of the product; duplicate badges
made two sources of truth, and equal-spaced rungs imply quantities that do
not exist.

### D-073 · fixed geometry: the eye is the anchor, cards are 124×124 always
Ruling (user): the eye (the user) never moves, is never draggable, never
re-parented — its world position is a CONSTANT (layout() translates the
whole tree so the eye lands there); never derive the coordinate origin from
the tree's extent. Every node card is a fixed 124×124 square that never
changes size or position on focus — the desk fades in OVER the card at the
same size; you zoom to read it. The eye's switchboard expansion to the
screen's aspect ratio is the single sanctioned exception, and it triggers
only near full-screen zoom (≥ 0.85 × height-fill; at desk zoom the eye
stays a plain card), symmetric so the eye's centre never moves.
Why: an origin hung off the tree's extent shifts the eye between orgs and on
every widening hire; a card that resized on focus would reflow the tree
under the user's cursor.
(Compact-screen exception approved 2026-08-14, dormant until FR-02: D-123.)

### D-074 · direct manipulation, not buttons
Ruling (user): structural and quantitative edits are done by dragging —
every credit bar is drag-to-reallocate (commits one reallocate on release),
and dragging a card onto another card or the eye re-parents it with its
whole subtree as one rigid group. Dropping on empty space is a cosmetic
reorder among siblings. No move button, no ± buttons.

### D-075 · destructive gestures carry their undo in the toast
Ruling (№17): a gesture one mis-drag away from happening ships its reverse
inside the toast — re-parents offer move-back, reorders the old ordinal,
retires offer rehire, reallocations the negated delta. Toasts live 12 s and
sit above every scrim (z 100) so the undo stays reachable while a modal is
open.
Why: a successful accidental reorder used to be completely silent.

### D-076 · unread attention has two independent layers
Ruling (user): the eye glows and pulses until the mailbox is OPENED —
opening alone clears the glow (persisted per-org) — while the ✉ count badge
stays until mails are actually read. Separates "I have seen there is mail"
from "I have dealt with it".

### D-077 · wheel over a surface scrolls; it never falls through to zoom
Ruling (user, explicitly reversing an earlier fall-through design): wheel
over a modal always scrolls the modal; wheel over an open desk is
scroll-only and NEVER zooms — even when nothing under the cursor can
scroll.
Load-bearing: implemented as ONE native non-passive listener on the
viewport that early-returns for overlay/desk targets — it must stay native
because it fires before React's delegated handlers, so component-level
stopPropagation cannot guard it.

### D-078 · archived agents fold away in the tray
Ruling (user spec, 2026-07-31): the agent tray hides archived rows behind a
"▸ show N archived" fold, with a name-filter input at its head. Same motive
as the canvas retired-pile: long-running orgs fill with retirees.
Was. "the tray lists every agent, archived rows dim."

---

## Process & bridging

### D-003 · the determinacy precondition (when a wall may be auto-bridged)
Ruling (user, 2026-07-31; interpretation pinned by the user 2026-08-01): a
refusal may be bridged **autonomously** only when the legal sequence to the
goal is DETERMINATE — one unique end state. Determinacy turns on **uniqueness
of the end state**, not on reversibility and not on how destructive the
operation is. If several non-equivalent sequences reach the goal, the choice
is the user's; automating it would substitute our judgement for theirs.

Three worked examples to classify new cases against:
- **Ceiling-lowering sweep** — destructive yet automated: clamping every
  node's scope to the new ceiling has exactly one end state.
- **retire of a manager → auto-dissolve of its subtree** (confirmed by the
  user 2026-08-01): destructive, automated — there is only one subtree to
  dissolve, so the end state is unique.
- **Kiosk credit cap below current holdings** — refused, never automatic:
  *which agents die* is ambiguous; any affordance must let the USER pick the
  retirements.

Was. PLAN §7.1 / open decision №10 — "retiring a non-leaf refuses and points
at dissolve" — struck 2026-07-31 by the design motto; auto-dissolve-with-
warning shipped (8b98ac9). The one surviving refusal is SELF-retire with
live reports (an agent has no dissolve authority over itself). A 2026-08-01
verification pass argued the auto-dissolve failed determinacy
(move-reports-then-retire as a second legal sequence); the user's ruling
recorded above settles it — auto-dissolve stands.

### D-005 · the record's shape: register + traps, superseded-in-place
Ruling (user, 2026-08-01): the durable record is two files — this register
(normative decisions, `Was.` slots, supersede in place, `## Retired` tail)
and docs/ARCHITECTURE.md (operational traps). PLAN.md stays untouched as the
historical plan. ADR-per-file is rejected: its supersede-by-appending
convention is precisely the failure mode this structure exists to fix.
Corollary (the repo razor): if a fact would still be true for a stranger
cloning this repo, it belongs in the repo — not in agent memory.

### D-079 · mechanical waves ship inert
Ruling (user practice, ratified 2026-08-01): a mechanical wave (typing,
refactor, conversion) ships runtime-inert — latent bugs it uncovers are
DOCUMENTED, never fixed in the same commit; fixes land separately and are
separately reviewed. A pure refactor is machine-verified: moved lines
diffed verbatim against source ranges, minified bundle byte-identical.
Why: mixing behavioral fixes into a mass rewrite destroys the
reviewability that makes the rewrite safe.

### D-080 · asking for what is already true succeeds
Ruling (user, design motto): an idempotent ask is a warned SUCCESS, never an
error — and every NEW verb must follow the pattern (the pinned cases are
enumerated in ARCHITECTURE §Ledger; `request_credits` for ≤ the current
grant additionally WITHDRAWS any pending ask).
Why: an agent that must query state to avoid an error burns turns on
bookkeeping; "permit as much as possible" applies to no-ops too.

### D-081 · chatq is only the reverse wake-up; subagents never touch it
Ruling (user): chatq exists ONLY for the reverse direction — an org starting
a conversation with an external chat unprompted. Outside→org Q&A is covered
by the bundled extern MCP server; org→org mail needs neither. Orgs register
under their human-readable slug, never an opaque id. Only top-level agents
may use chatq (and hold the Monitor permission its listener needs);
subagents are banned by their standing prompt — org mail is their only
channel.
Why: keeps the org's outside surface single-faced and prevents a deep
subagent from opening an unaudited side channel out of the org.

### D-082 · the social-preview procedure is pinned
Ruling (user, 2026-07-31): social-preview.png is regenerated ONLY by
`python tools/social_preview.py` — throwaway isolated backend, the canonical
demo cast, 0.88 content crop, pan from EMPTY canvas (grabbing a card drags
the node, not the camera), cursor parked off-card (hover bakes the hire
chips into the shot). Upload is manual — GitHub has no API for the social
preview.

### D-157 · a test run must be able to say it did not finish
Ruling (coordinator, 2026-08-26, on a diagnosis by `task-timeouts`): a run of
`tools/run_tests.py` ends by emitting TWO artefacts together — a final
`RUN COMPLETE  suites=N/M  passed=…  failed=…  rc=X` line on stdout, and a
`COMPLETE` file in `logdir`. Their **absence** is the signal. Nothing else may
write either one, and neither may be produced before every suite has been
accounted for. Anything gating on a test run — a deploy above all — gates on
those, never on "no `✗` appeared".

Why: a killed run and a passing run were byte-for-byte the same shape. Both
end in a column of `✓` with empty stderr and no marker; two tier runs were cut
at 19 and 25 suites with **zero failures between them**, and a deploy was
gated on one of them. The failure is not that runs get killed — it is that
being killed was invisible, so "the tests passed" and "the tests stopped"
were the same observation. Two suites in this tree (`extern-handle-attach`,
`extern-peer`) print no final total of their own, which removes even the
weak per-suite tell.

A run that FAILED still finished: the verdict travels in `rc=` and `failed=`,
never in the marker's presence. Gating the marker on success would collapse
red and killed back into one shape, which is the thing being fixed.

Bounds: `--list` and the nothing-to-run refusal emit no marker, correctly —
they ran nothing, so they have nothing to claim. The mechanism says whether a
run REACHED THE END; it says nothing about why one was killed (see
docs/ARCHITECTURE.md § "Running a long job without losing it" for that, which
is operational and would evaporate under a refactor).

Load-bearing:
- **stdout line first, marker file second.** An interruption between them must
  leave the run looking unfinished. A marker that lies is worse than no
  marker: it turns "I cannot tell" into "I was told wrong".
- **`run_one` writes a suite's log only after that suite finishes.** That is
  what makes `ls <logdir> | wc -l` against the `plan · N to run` header a
  measure of progress, and it is the only discriminator that works
  retroactively on runs already on disk. A refactor that opened suite logs up
  front — to stream into them, say — would destroy it silently.
- Both are pinned by `backend/tests/test_run_completion.py`, which kills a
  real run mid-flight rather than reasoning about the source, and proves the
  kill landed on a running run before reading the result.

### D-158 · an instrument that reads text as data must be proved able to fail
Ruling (coordinator, 2026-08-26, on a finding by `task-timeouts`): a check
that works by matching this repo's own OUTPUT or SOURCE as text — a drift
guard, a rot alarm, a truncation detector, a mutation harness, any
grep-shaped assertion — is not trusted until it has been shown to **fail** on
the condition it claims to detect. Watching it pass is not evidence of
anything: a matcher that matches nothing passes every run beautifully, and is
indistinguishable in every log from one that is working. Three rules follow,
and they are the reusable part:

1. **A passing check's line is not evidence of failure.** Never classify a
   line a suite reported `ok N` on as a failure, whatever phrases its label
   happens to contain. Labels describe things going wrong; that is their job.
2. **Prove the negative case, not just the positive one.** Every such
   instrument needs a check that FAILS when fed the real fault — built from
   the real thing, not from remembered text. Break a real contract, kill a
   real run, mutate a real file.
3. **When repairing one, prove the repair is not a GAG.** Narrowing what an
   instrument matches and deleting what it matches look identical from the
   outside — both turn the alarm off. So assert that the offending input
   *still* matches the underlying pattern and is excluded by the new filter,
   which is what stops a later widening from silently restoring the fault.

Why: four instruments in three days lost the ability to fail, and nobody
noticed, because each of them went green. A fixed-offset window into a source
file that stopped pointing at the thing it measured. A `replace(..., 1)`
mutation that hit the wrong one of two identical lines, so the suite ran
against unmutated source and passed. Patterns written with `\n` against files
that are CRLF on this machine, which therefore never matched anything. And
`_GUARD_FIRED` matching `no longer matches` inside a PASSING check's own
label, which made the rot alarm fire on every clean run — an alarm that cries
wolf every run is an alarm nobody reads, and a false alarm standing beside a
real one makes the real one unreadable. Four is a pattern, not four
coincidences, which is why this is a rule and not four comments.

Rule 3 earned its place on measurement rather than principle. Repairing the
rot alarm, the mutation that deleted the matching phrase from the pattern
outright — the pure gag — **still passed** the "does it fire on a real drift"
check, because the real guard's message happens to carry three matching
phrases and removing one left the other two. A PARTIAL gag is the subtle
version of this bug: the instrument still fires, just no longer for the reason
you think, and the outcome test cannot see the difference. Only the check on
the raw pattern caught it.

Bounds: this is about instruments whose failure mode is a SILENT NO-OP —
text matchers, source readers, output classifiers. An ordinary assertion on a
value or a return shape fails loudly when it is wrong and does not need this
ceremony. The line is whether "matched nothing" and "found nothing wrong"
produce the same result; where they do, rule 2 is not optional.

Load-bearing:
- **A mutation must be shown to have LANDED before its result is read.** A
  mutant that does not compile, or an edit that changed no bytes, produces a
  red run that says nothing about the check. Compile the mutant; assert the
  file actually changed. Both were hit while establishing this entry.
- **Restoring a mutated file with `git checkout <path>` reverts to HEAD, not
  to your uncommitted work.** Copy the file aside first. That mistake
  destroyed an implementation mid-session and is easy to repeat.
- Pinned by `backend/tests/test_drift_alarm.py` (§3 is rule 3) and
  `backend/tests/test_run_completion.py`. See also D-157, which is one
  application of this rule, and docs/ARCHITECTURE.md for the operational
  half.
- **Extended by D-168**: proving an instrument can FAIL is necessary and not
  sufficient, because there is a third outcome — it never ran — and that one
  had been wired to the pass branch in four places. Read the two together.

### D-168 · an abstention is not a pass: prove the instrument can report DID-NOT-RUN
Ruling (coordinator, 2026-08-27, on an audit by `ps-guards` prompted by a peer
organisation's report): every instrument D-158 covers must additionally be
proved able to report **did-not-run** as an outcome distinct from both pass and
fail. Where abstention and success are genuinely indistinguishable in the
mechanism, the abstention must be wired to the FAILING branch, never the
passing one. An empty result from a reader that broke, a check whose statement
was never reached, a score computed from output that was never produced — each
of these is an absence of evidence, and none of them is evidence of health.

Why: four instruments were audited that all satisfied D-158 — each could be
made to fail on the fault it named — and all four still lied, because the
question D-158 asks has three answers and it only pins two. In each case the
third answer had been soldered to "pass":

1. **A guard downstream of a redirected native stderr, under PS 5.1.** In
   Windows PowerShell 5.1 a native command whose stderr is REDIRECTED has each
   stderr line wrapped in an ErrorRecord, and under
   `$ErrorActionPreference='Stop'` that record is TERMINATING. `update.ps1`
   probed esbuild with `node -e "..." 2>$null` and branched on
   `$LASTEXITCODE`. node writes a stack trace to stderr exactly when esbuild is
   broken — so the script died ON the probe and the entire clean-reinstall
   self-heal beneath it was unreachable in the only condition it exists for. A
   healthy node prints nothing, so the line passed on every run for as long as
   nothing was wrong. `expose.ps1` had recorded this mechanism for
   cloudflared's banner since 2026-08; `update.ps1` had not applied it. The
   `update.sh` mirror was correct throughout (`if ! cmd` is safe in a shell),
   which is why the POSIX path self-healed and the symptom never appeared on
   the side anyone watched — a defect present on one leg of a deliberately
   mirrored pair hides behind the healthy leg.
2. **A reader whose failure is spelled the same as success.**
   `git status --porcelain` returns an empty string when the tree is clean AND
   when git could not read it; both scripts tested only for emptiness. Measured:
   exit 128, empty capture, dirty-tree guard skipped in silence, execution
   continued to the pull.
3. **A path resolved from the script's own location, with nothing checking it
   landed anywhere real.** Demonstrated against a decoy — a genuine git
   checkout, with a commit and an upstream, that was not this repo: `update.ps1`
   announced itself as an orgtree update and ran into the git stage on it. The
   peer report that prompted the audit is the sharper form of the same shape:
   their probes resolved "repo root" to a directory with no project file, had
   not run for days, and the harness then reported the fault against the
   HEALTHY component — so the reader was sent to investigate the wrong thing.
   A message that misdirects is worse than silence.
4. **A score computed by set difference over scraped output.** Both mutation
   harnesses compute `killed = baseline - passed` from the suite's `ok N`
   lines and accept the mutant when `must_kill in killed`. A suite that never
   RAN — the mutation broke the file, an import blew up — prints no `ok` lines,
   so `passed` is empty, `killed` is the entire baseline, and the test is
   trivially true: "✓ KILLED", exit 0, not one check executed. This is the
   ugliest of the four, because it is the tool built to catch exactly this
   class carrying an instance of it. D-158's own Load-bearing slot already
   said "a mutation must be shown to have LANDED before its result is read" —
   the rule was written down and the instrument did not implement it, which is
   why this entry states the requirement as something to be PROVED rather than
   merely intended.

Bounds: this applies where "did not run" and "ran and found nothing wrong"
produce the same artefact — the same empty string, the same absent line, the
same exit code. It does not apply to an instrument that fails loudly on its own
account. The discriminator is not the instrument's subject but its plumbing:
ask what the check yields when the thing it reads is absent, and if the answer
is what success looks like, the wiring is the bug.

Load-bearing:
- **Redness carries no information in a mutation harness.** A mutant is
  SUPPOSED to turn the suite red, so "the suite failed" cannot distinguish a
  kill from a crash. Only positive evidence that the suite RAN can, which is
  why the fix is a compile check plus a refusal to attribute a kill when no
  `ok` line was produced — not a stricter reading of the failure.
- **A guard a correct run can trip is worse than no guard**, and that bounds
  the repair as much as the fault. The anchor check is scoped per mode:
  `-EnsureUp` is the five-minute crash-restart net, builds nothing, and is
  gated only on the file it actually launches, because gating it on the
  frontend would newly refuse to recover a downed backend. Same argument as
  the dirty-tree pass-list.
- **These guards check existence, not authenticity.** A stub file of the right
  name satisfies the anchor check. It catches a wrong directory, not a
  corrupted one, and is not a substitute for the dirty-tree guard.
- **The compile check caught a real one on its first run, which is the
  evidence this entry rests on.** `_mutate_handles.py` had shipped a mutant
  whose replacement carried `))` where the anchor spanned both the last tuple
  element and the tuple's own closing paren — one paren too many, so
  `ledger.py` did not parse. That mutant had therefore never tested the
  self-retool fence, and the old scoring reported it "✓ KILLED" every run,
  because an empty `passed` makes `baseline - passed` contain everything.
  Repaired to a single `)`; it now genuinely dies to "a self-retool may NOT
  carry external_handles", and the harness is 10/10 with its no-op control
  still surviving. One of eight mutants in a hand-maintained list was vacuous
  and nothing could see it — the base rate for this class is not low.
- **Separately and NOT fixed here**: `_mutate_harvest.py` reports four mutants
  as `PATTERN NOT FOUND` — their anchors have rotted out of `supervisor.py`,
  so that harness exits 1 on unmodified `main` today. That is its *existing*
  anchor guard working correctly, not a new fault, and re-authoring four
  auth/harvest mutants needs someone holding that subsystem. Recorded so it is
  not rediscovered as new.
- Proved by exercising each path directly rather than by review: the probes
  and their before/after transcripts are recorded with the audit. Two of the
  probes written for this entry were themselves wrong on their first run — one
  extracted a window that did not compile and reported `ok` for every row, and
  one used `2>&1` under `EAP=Stop` and died of the very defect it was written
  to measure. Both were caught only because they carried rows whose expected
  answer was the opposite polarity. An instrument for this class needs its own
  negative control.

### D-170 · a test rig dies with its suite, and a blocked precondition is not 23 failures
Ruling (coordinator, 2026-08-28, on a diagnosis by `lying-instruments`): a
suite that starts a real listener must tie that listener's lifetime to its own
by an OS mechanism, not by a `finally` — on Windows a job object with
`KILL_ON_JOB_CLOSE`, the same idiom `supervisor.py::_leash` already uses.
And when such a suite cannot run because a precondition is unmet, it reports
that ONCE, as a precondition, naming the obstruction — never as a failure of
each thing it did not get to test. Exit non-zero and print no final total, so
the abstention lands in the failing branch (D-168) rather than reading as a
pass.

Why: **the failure count was not the number of causes.** `test_turn_lifecycle`
binds a fixed port. A run that is KILLED rather than exiting never reaches its
`finally: stop_backend()`, so it orphans a backend on that port; the orphan
then fails EVERY later run with ~23 identical "section aborted" entries under a
"125 passed, 24 FAILED" banner. One event, a long tail: three agents lost runs
to it on 2026-08-27 and one lost three, with no real defect anywhere in the
tree. Measured, in this order: killing the suite left the rig listening (rig
pid survived the suite's death); a stub occupying the port reproduced the
failure shape exactly (90 passed, 23 FAILED); and the real failing log carried
18 occurrences of the guard's own `port … never freed`.

**The guard's message was already true.** It said the port was held, once, in
the first of 23 tracebacks it had itself caused — and three readers still
concluded the product was broken. Truth is not sufficient; a true statement
buried under its own consequences reads as one detail among many. That is the
same hazard as a reassuring test NAME (see the Resonite report behind D-168):
the presentation recruits the reader into a conclusion the evidence does not
support.

⚠ **NEVER kill `orgtree.api` by command-line match. Discriminate by PORT.**
The operator's live deployment runs under a command line byte-identical to the
throwaway rig's — same interpreter, same `-m orgtree.api`. The rig is the one
on the suite's own port; the deployment holds the operator ports in the 7360s.
A "clean up stray backends" helper written from the obvious reading kills
production. This is recorded as a standing hazard because it was nearly hit,
not merely foreseen: the first sweep of this investigation listed the live
backend as a stray, and it was ruled out only by checking parentage and ports
before acting.

Bounds: the job object is Windows-only; on POSIX the child is already in the
suite's process group and `stop_backend` remains the ordinary path. The leash
makes the cascade unstartable, it does not clean up an orphan from an older
build or from a `taskkill /F` on the whole tree — which is why the legible
precondition is the other half and not a nicety.

Load-bearing:
- **This is D-157's cause wearing a second face, and a reader who finds one
  should be handed the other.** Work an agent starts as a harness background
  task is killed when its turn ends. That is how these rigs came to be killed
  rather than exiting, so the bug that hid partial test runs is also the bug
  that manufactured these phantom failures. Long jobs are launched detached and
  waited on with a watchdog (docs/ARCHITECTURE.md § "Running a long job
  without losing it").
- **Warning about a forbidden port must not disable the test.** `run_tests.py`
  refuses any suite whose SOURCE places a forbidden port straight after a
  colon, comma, equals or open-paren, and it reads prose exactly as readily as
  code. Measured twice while writing this: stating the hazard the obvious way
  dropped the whole suite from the plan ("0 to run"), and so did the first
  comment explaining why. Write such port numbers without the leading
  punctuation.
- **A passing check's line is not the failure, in the failure EXCERPT either.**
  The runner picked its excerpt with a pattern matching `Error:` inside labels
  like `ok 9 limit-detect · no 'API Error: 500 …'`, so a failing suite showed
  the reader four GREEN lines and "… 119 lines in the log" while the real cause
  sat off screen. Same rule and same fix as the drift alarm (D-158): exclude
  lines a check reported itself passing on.

---

## Deliberately not built

The re-litigation stopper: each was considered and rejected or dropped, with
the ruling recorded.

- **attach/release (managed↔attached terminal handoff)** — cut entirely as
  vestigial (flag, endpoint, branches, README promise removed; desk-parity
  №4 refused on the same ruling). orgtree is the one driver of every node; a
  released node breaks routing, occupancy accounting and the failure model
  at once. (user-confirmed, 2026-07-31.) Residue: "attach" now means mail
  file-attachments only.
- **retire-to-fit / kiosk tier model-sweep** — lowering a credit cap never
  auto-retires agents; the credit cap refuses to go below current holdings;
  the tier ceiling refuses admins too and never sweeps models on lowering.
  Fails D-003's determinacy test: *which agents die* is the user's choice.
- **interactive tools in agent turns** — AskUserQuestion and plan-mode
  disallowed; an interactive call behind a headless turn is an unanswerable
  stall. Agents route questions to humans via `orgtree_message`.
- **`--max-budget-usd`** — dollars are tracked and surfaced, never capped: a
  spend cap would kill a node mid-task. (Kiosk spend limits freeze turns at
  the org level instead — a different, recoverable mechanism.)
- **nested scratch directories** — scratch stays flat; promote/demote move
  nodes, and moving a directory out from under a live session's cwd is
  exactly the fragility to avoid.
- **an "ultracode" effort tier / effort as a permission** — effort is a cost
  dial (D-042); no ultracode: orgtree replaces subagent semantics with real
  hires.
- **POSIX OS-level storage block** — no deny-write-but-allow-delete bit
  exists (blocking dir writes blocks unlinking too), so POSIX enforcement
  would break self-heal. Superseded by the disk cap for sandboxes anyway.
- **user MCP servers in sandboxes** — see D-059; `ORGTREE_SANDBOX_MCP` is an
  experimental opt-in with no guarantees.
- **a per-turn permission-mode switch in the composer** — org ⚙ permissions
  decide what agents can do; a per-turn mode switch would contradict that
  model. (The five-dot effort track lives in a popover instead.)
- **async backend rewrite** — the synchronous threaded supervisor is a
  deliberate design around subprocess lifecycles and DOC_LOCK; FastAPI runs
  sync endpoints in a threadpool; at this scale an async supervisor is the
  highest-risk change for the least gain. `.then()` chains in React handlers
  likewise stay. (typing-wave scoping, 2026-07-31.)
- **API-seam codegen** (schema.py → types.ts) — deferred until a real drift
  bug proves a generator is needed; hand-mirrored until then.
- **a third (admin) MCP server** — "just leave it for now" (user,
  2026-07-31): loopback REST suffices; re-propose only if a consumer appears
  that cannot shell out.
- **Tier-1/Tier-2 shell isolation** (bash command-string gating; per-node
  UID + POSIX ACLs) — dropped 2026-07-30, superseded by the per-org
  container (D-029). Dropped in the same ruling: №33 cache-economics
  measurement, and in-app auth/TLS (the Cloudflare tunnel is the exposure
  story).
- **chatq as internal transport** — internal routing needs durability,
  attribution and org-doc persistence a flat peer queue does not provide;
  chatq's only role is the external bridge (D-081).

---

## Open — awaiting a ruling

### OPEN-01 · a failed turn's recorded error can be unrelated to the failure
Found 2026-08-24 (creds-probe, measured) while working on multi-account, but
**this is not a multi-account bug** — it affects every agent on every org, and
is filed here so it is findable by someone who never reads D-144.

`err_blob` — the string that becomes the node's user-facing `last_error`, the
`turn_error_log` row, AND the input to every `_looks_like_*` classifier — is
built from **stderr alone** whenever the CLI exits nonzero. The CLI's own
`result` event, which carries the real reason, is only consulted when the exit
code is ZERO. Two measured consequences:

1. **An expired or rejected OAuth token is undiagnosable from orgtree's own
   surfaces.** The CLI writes `Failed to authenticate. API Error: 401 …` into
   its `result` event and **nothing to stderr**, so `err_blob` falls to the
   fallback and both the UI and the log read *"the CLI exited 1 without
   writing anything to stderr"*. The supervisor already receives the real
   string and discards it, because the only question it asks of it is
   `_looks_like_usage_limit`.
2. **Incidental stderr noise is recorded AS the error.** Measured: a CLI
   invoked without stdin emits a *"no stdin data received in 3s"* warning;
   `err_blob` takes stderr verbatim, so that warning became the recorded
   cause of an authentication failure. Any warning on stderr will do this.

The fix splits into two changes with very different blast radii, and the
ruling wanted is on the second:
- **(a) Recording only** — carry `res["result"]` / `api_error_status` into
  `last_error` and the log when stderr is empty, WITHOUT feeding it to the
  classifiers. Contained; changes no freeze or retry behaviour.
- **(b) Classification input** — let that text reach the `_looks_like_*`
  predicates. This changes what every agent's errors classify as, on every
  org, and could newly match `_looks_like_usage_limit` on turns that are
  terminal today. The comment at the fallback already names the adjacent
  hazard ("a crash landing on the same turn as a limit would swallow the
  limit and skip the freeze").

⚠ Any Phase-2 `_looks_like_auth_failure(err_blob)` added **before (b)** is a
change that goes green and does nothing: the auth text never reaches the
predicate. See D-144.

*(The seven items ruled 2026-08-01 live in their domain entries: D-021
Bounds, D-014 Load-bearing, D-063, D-023, D-071, D-069; the mobile wave's
hold/release state is project-state, tracked outside the register.)*

### D-142 · self-restart deploys the CURRENT commit; "behind" stops being the gate
Ruling (user, 2026-08-21): `orgtree_self_update` becomes
`orgtree_self_restart`, and the launch stops passing `-OnlyIfBehind` /
`ORGTREE_ONLY_IF_BEHIND`. The tool now rebuilds and restarts from whatever is
committed in the repo, whether the pull moved HEAD or not.
Why: measured the same morning. Three fixes were merged locally to main and
the tool was called. `update.ps1 -OnlyIfBehind` exits BEFORE the rebuild when
the pull advances nothing — and main was AHEAD of origin, never behind — so it
logged "already up to date -- NOT restarting", exited 0, and the running
backend kept serving the old build with the merge sitting on disk. Nobody was
told anything was wrong; it took an operator-style run without the flag to
deploy. Pushing first does not rescue it either: then HEAD merely EQUALS
origin, still not "behind". So the tool was structurally unable to ship a
commit made on this machine, and failed SILENTLY — the two properties that
make a deploy mechanism worse than none.
D-104's worry was real and stays answered, just not by a flag: a restart with
nothing to deploy cuts every org here for no gain. What prevents it is the
CALLER having a reason, stated plainly in the tool card and the standing
prompt ("have a REASON — something to deploy, or a backend to bounce; there
is no free restart"), rather than a gate that also swallowed the legitimate
case without saying so.
Bounds — two guards are explicitly NOT touched, both load-bearing for reasons
unrelated to the flag. The mid-turn REFUSAL (D-104) stands whole: `target`
org/both still refuses while any agent on this machine is mid-turn and still
names them. The DETACHED spawn stands: the deploy tears down the caller's own
turn, so a synchronous run dies mid-build and leaves a half-updated install
(measured on a peer install 2026-08-09, re-confirmed 2026-08-21).
`-OnlyIfBehind` REMAINS DECLARED in `update.ps1` and `update.sh` — nothing in
this repo passes it now, but it is a legitimate operator/scheduled-job
affordance and PowerShell hard-errors on an undeclared switch, so deleting it
would break out-of-repo callers loudly for no gain.
The old name stays DISPATCHABLE as a hidden deprecated alias, absent from
`mcptool.TOOLS` so no new session learns it. A live session holds the tool
catalogue it fetched at startup, and stored charters carry the literal string;
both would call the old name at an install that no longer answered, and the
error would land on an agent that did nothing wrong. Removable once no stored
charter names it.

FOLLOW-UPS THIS RULING DELIBERATELY DID NOT FIX (adversarial round, 2026-08-21;
coordinator ruled report-only — each is its own change with its own testing,
and none should be bundled into a rename):

· **D-142/a — the kill window.** The mid-turn refusal is consulted at T=0, but
  `update.ps1` does not kill the backend until after pull + npm install + build
  — tens of seconds to minutes later. Nothing marks a deploy as pending, so an
  agent woken by mail inside that window starts a turn that the restart then
  cuts: exactly the harm the refusal exists to prevent. Pre-existing, but D-142
  widens it — the dominant path used to exit before the rebuild, so the window
  was mostly theoretical; now every call runs to the kill. Scoping is recorded
  with the follow-up: `_run_turn` is a genuine single choke point (three thread
  starts plus the turn-end drain), so the FLAG is small; the CLEARING is not.
  `update.ps1` has **fourteen** exit paths that never reach the restart, two of
  which (dirty tree, `git pull` failure) fire routinely — so a timeout-cleared
  flag would wedge every org on the machine on the COMMON path. Clearing it by
  watching the spawned child's pid is the promising design, but it changes
  `_detached_spawn`'s contract and lands in turn admission, which is being
  rewritten concurrently.
  ⚠ **WHAT COUNTS AS AN EXIT PATH**, so the fourteen is checkable rather than
  assertable (added 2026-08-27, after the coordinator verified the merge and
  could not re-derive the number from the file — which is this follow-up's own
  disease, caught one iteration later). The rule is: **literal `exit`
  statements, wherever they sit on a line.** Five of the fourteen are INLINE
  inside a single-line `if` — `if ($LASTEXITCODE -ne 0) { Write-Host ...; exit
  1 }` — so a line-anchored `grep '^\s*exit [0-9]'` returns ELEVEN and looks
  authoritative while being wrong. That grep is exactly how a later reader
  would "correct" this entry back to a wrong number, which is why the counting
  method is written down and not left to taste. Count with the parser, which
  cannot miss an inline statement:

      # Resolve-Path, NOT a bare 'update.ps1' — see the warning below it.
      $f = (Resolve-Path 'update.ps1').Path
      $ast = [System.Management.Automation.Language.Parser]::ParseFile(
                 $f, [ref]$null, [ref]$null)
      $kill = ($ast.FindAll({ $args[0] -is
                 [System.Management.Automation.Language.CommandAst] -and
                 $args[0].GetCommandName() -eq 'Stop-Process' }, $true)
              )[0].Extent.StartLineNumber
      $e = $ast.FindAll({ $args[0] -is
               [System.Management.Automation.Language.ExitStatementAst] }, $true)
      "before=$(($e | Where-Object { $_.Extent.StartLineNumber -lt $kill }).Count)" +
      " after=$(($e | Where-Object { $_.Extent.StartLineNumber -gt $kill }).Count)"

  → `before=14 after=2` at `680ecd5`. Re-run it rather than trusting the
  number; it also re-verifies the ordering property this follow-up rests on.
  ⚠ The `Resolve-Path` is load-bearing and was itself a bug in the first draft
  of this snippet, caught by running it: `ParseFile('update.ps1')` does NOT
  follow PowerShell's own location. It resolves against the .NET process
  `CurrentDirectory`, which `Set-Location`/`Push-Location` do not update — so
  in a worktree it silently parsed the OTHER checkout's `update.ps1` and
  reported a confident number for a file the reader was not looking at. It
  agreed only because the two copies happened to be identical. A verification
  command that reads the wrong file and answers anyway is the same defect
  D-168 registers, which is worth the four extra characters.
  **NOT counted, and deliberately: terminating errors** from
  `$ErrorActionPreference='Stop'`. Those are also paths that leave before the
  restart, and they are UNBOUNDED — any cmdlet failure is one — so they cannot
  be enumerated. Fourteen is therefore a FLOOR, not a total. That is the safe
  direction here (the argument needs "many exits fire on the common path", so
  more is worse and the conclusion holds a fortiori); it would NOT be safe for
  any future argument needing an upper bound, and such an argument must not
  cite this number.
  Was. "six exit paths". Recounted 2026-08-27 (`ps-guards`, D-168): it was
  already wrong before that audit — eleven on `e724c21`, against six here and
  eleven in `supervisor.py`'s own comment, so the two records of the same
  number had already diverged from each other. D-168's guard fixes add three
  more (an empty script root, a missing repo anchor, a `git status` that
  failed), taking it to fourteen. ⚠ The PROPERTY this follow-up rests on was
  re-checked rather than assumed to survive the recount: every one of the
  fourteen still precedes the `Stop-Process` kill, so "a failure exit means no
  restart happened" holds, and the three new ones are all before the pull. A
  bare count in an argument that depends on it is exactly the drift D-168 is
  about; if it needs updating again, re-check the ordering property too rather
  than only the number.
· **D-142/b — the mailhub leg's bare `git pull`.** `launch_self_restart`'s
  hub leg runs `git pull` (NOT `--ff-only`) with cwd `<repo>/hub` — a plain
  subdirectory of the backend's OWN repo, not a submodule. So it mutates the
  running backend's source tree with no dirty-tree guard, and without taking
  the `Global\orgtree-update` mutex `update.ps1` uses, so it can race a
  concurrent deploy on the git index. A merge commit or a conflicted MERGING
  state left behind breaks every later deploy's `--ff-only` pull. It restarts
  nothing that serves turns, so it cannot cut a turn directly. Note the tool
  card advertises "git pull --ff-only", which is false on this leg.
· **D-142/c — the audit event is written before the outcome.** `_log` fires
  inside the gate, with empty detail, so a call the launch then REFUSES (busy
  machine) or rate-limits is indistinguishable in the ledger from one that
  actually deployed — and the event records neither the target nor which tool
  name was used.

### D-159 · a deferred restart, because the thing that failed was the agent, not the tool
Ruling (user, 2026-08-27, their own design, verbatim): "when executed, a
restart will automatically occur the moment all agent turns have stopped and
no pending turn-starting mail is in flight. this will both ensure a restart
eventually happens, while also not interrupting any single agent's work."
Shipped as `orgtree_prime_restart` (`arm` | `cancel` | `status`), beside
`orgtree_self_restart` rather than replacing it.

Why: the user's word for the old tool was "unreliable", but the measured
failure was NOT the tool. `orgtree_self_restart`'s mid-turn refusal (D-104) is
the precondition working. What failed is the human-shaped half — the agent
holding the intent kept deferring the call to "next wake" and was
cheap-compacted before making it, so a merged fix sat undeployed for a full
day. ⇒ **The property being bought is that ARMING OUTLIVES THE ARMING AGENT:**
its compaction, its retirement, its dissolution, and a backend bounce. Any
design where the prime lives in a session, a node flag, or an in-process timer
rebuilds the original bug with more steps.

Load-bearing decisions, each of which could have gone the other way:

· **What "idle" means is `others_working`, reused, not re-derived.** Its body
  moved to `_working_locked` (caller holds `_state_lock`) with
  `others_working` as the locking wrapper — one body, two doors. A second
  hand-written loop would be a second definition of "is anyone working", and
  the day they disagree is the day a primed restart cuts a turn the manual
  tool would have refused to cut.
· **"No pending turn-starting mail in flight" is already inspectable, and it
  is `busy or queue`.** `deliver(..., wake=True)` sets `st["busy"]`
  SYNCHRONOUSLY under `_state_lock` and only then starts the `_run_turn`
  thread; `wake=False` (`orgtree_send_notice`) returns `{parked: true}` and
  never sets it. So the user's distinction is exactly the `wake` flag, the
  in-flight window is zero-width under the lock, and no new predicate was
  needed. What remains outside it — a watchdog that will fire in 40s, a frozen
  node whose auto-resume is due — is not "mail in flight" and is deliberately
  NOT waited for: it is unbounded, and a machine with an armed dog would
  never be quiet.
· **The check→launch race is CLOSED, not narrowed.** `_claim_quiet_machine`
  reads `_working_locked()` and clears `_deploy_done` under the SAME
  `_state_lock`. Anyone already busy is visible and refuses the claim; anyone
  who goes busy afterwards reaches `_hold_for_deploy` — "the single choke
  point: all three thread starts target this function" (D-142/a) — and parks
  at the threshold with nothing dequeued and no mail moved. There is no third
  case. This is what D-142/a's deferred item was for, arriving from the other
  side: the flag is cleared by the caller and adopted by the launch.
  `_arm_deploy_window` therefore RETURNS a bool and `launch_self_restart`
  reports `deploy_window`, because an ORPHANED hold (nothing spawned, nobody
  to release it) silences every org on the machine for `DEPLOY_HOLD_MAX`.
· **Disarm BEFORE the spawn.** The other order is a restart LOOP: `update.ps1`
  Stop-Processes this backend, the disarm write never lands, and the next boot
  finds the prime armed and restarts again — unattended. Disarm-first can lose
  a prime instead, which is a nuisance that ANNOUNCES itself (the chip
  disappears and `last_fired` records the launch's answer). A nuisance you can
  see beats a loop you cannot.
· **NOT re-gated at fire time.** `prime_restart_gate` runs at the arm, sharing
  `_restart_authority` with `self_restart_gate` so the patient tool cannot be
  laxer than the immediate one. Re-checking at the fire would mean a prime
  armed by an agent since retired silently never fires — and that is the
  motivating case, not an edge case. Authorization belongs to the decision;
  the engine only executes it.
· **Storage is a MACHINE-WIDE file** (`<DATA_ROOT>/primed-restart.json`), not
  an org doc. The watchdog PRINCIPLE is copied ("the doc is the registry, the
  loop is just its runtime attachment") but not the location, for two reasons
  an org doc gets wrong: a restart armed in org A cuts org B, so a per-org
  record is invisible to precisely the orgs that need warning; and the user
  ruled priming idempotent, which has to hold across a bounce — one file is
  one fact.
· **Idempotent, not mute.** A second arm changes nothing (including the
  target) and SAYS so, naming who holds the live prime and what target will
  actually run. A silent success is the one answer indistinguishable from
  "I armed it just now", and "did mine take effect" is the question the caller
  is asking.
· **The 300 s one-at-a-time gap is WAITED OUT, never spent on.** Hoisted to
  `SELF_RESTART_MIN_GAP` — one constant, two readers. The engine additionally
  reads a DURABLE `last_fired.at_ts`, because `_self_restart_at` is process
  memory that a restart zeroes; without it a prime could fire straight into
  the deploy that just finished.
· **A 20 s settle (`PRIME_QUIET_S`) on top of the lock.** Not for the race —
  that is closed. For the handoff: a turn can end microseconds before the mail
  it just sent drives the next agent, and "all agent turns have stopped" does
  not honestly mean "there was an instant with nobody running".

Bounds: this tool cannot deploy itself — the first restart carrying it is
still a manual `orgtree_self_restart`. `target: 'mailhub'` deliberately takes
NO hold on the machine (it restarts no agent), the same call D-142/a made.
Visibility: every org's header carries a `restart primed` chip fed by
`tree.primed_restart`; a mailhub prime wears different words, because for that
target the chip would otherwise be a false alarm to its entire audience.

⚠ **The idle predicate counts agent TURNS, and a detached process is not a
turn.** So "the machine is quiet" and "nothing important is running" are
DIFFERENT STATEMENTS, and this tool only ever guaranteed the first. Found in
use 2026-08-27 by the agent who built it: a primed restart can reap a detached
test run, a build, or any other long job started outside the turn system —
and for an agent that is the machine's last active party, the quiet window the
prime waits for is *precisely* the window its own detached run occupies, so
the reaping is near-certain rather than unlucky.

This is a documented limit, not a defect, and closing it is worse than
recording it: waiting on arbitrary detached processes is unbounded, which is
the design argument this entry already makes for counting turns in the first
place. What makes it survivable is that it is DETECTABLE — a reaped run leaves
no `RUN COMPLETE … rc=0` line and no `COMPLETE` marker (D-157), so it reads as
*killed* and never as a pass. The mitigation is procedural and cheap: cancel
the prime for the length of a long detached run, then re-arm.

### D-143 · fable_api_fallback: an opt-in override of D-130's boundary

Decision (session seat, 2026-08-23, user feature request: "the api key
fallback works under normal rate limit hits, but it doesn't bypass the fable
limit — add a toggleable option to also proceed on fable with api key
fallback when past the weekly fable limit, even if not at overall weekly
usage limit"): D-130 excluded a fable-TIER quota from `api_fallback`
unconditionally — that lane belonged to `fable_limit_policy` alone, full
stop. New org bool `fable_api_fallback` (default **off**) makes the
exclusion conditional instead of absolute: with it on, AND `api_fallback` +
`api_key` both already held, a TRUSTED weekly Fable-tier hit
(`_fable_tier`, unchanged predicate) skips `fable_limit_hit` entirely — no
org-wide `fable_lock`, no per-node `limit_locked` — and instead falls
into the same `elif` a normal tier's limit does: it opens the identical
`api_fallback_until` window, on the identical lane, reverting at the
identical expiry-only revert D-130 already specified. Nothing about *how*
the window prices, records `on_fallback`, splits `api_cost_usd`, or expires
changes — the new predicate only decides whether a fable-tier hit is
ALLOWED to reach that branch at all (`_fable_fallback_eligible`).

Deliberately independent of every OTHER lane's state (the user's "even if
not at overall weekly usage limit" clause): eligibility reads only
`_fable_tier` plus the three org-level settings above, never the account's
`weekly_all`/`weekly_scoped` usage bands — a fable-only hit qualifies on its
own, exactly as a plain session limit already opens the window today without
needing any other lane to also be exhausted.

Bounds, all server-enforced (`api.py`):
- `fable_api_fallback=True` is refused (422) unless `api_fallback` is
  already on — it rides that window, it does not open one of its own.
- Turning `api_fallback` off (or `clear_api_key`, which implies it) clears
  `fable_api_fallback` with it — an orphaned fable-only toggle would look
  live in the UI while doing nothing.
- Transitively refused whenever `headless` is (or would be): `headless`
  already refuses while `api_fallback` is on and vice versa (the key is
  either a full-time lane or a spare, never both), so a headless org can
  never reach a state where `fable_api_fallback` does anything — no new
  coupling needed there.
- Add-only migration: existing orgs get `fable_api_fallback=False` via
  `setdefault` at load, same as any other D-084-style new field; a doc
  found with the flag on but no `api_fallback` behind it (settings edited
  out of order, or a hand-edited doc) self-heals to off on next load.

Was. considered making this a fourth `fable_limit_policy` value instead of
a separate bool (`halt | opus | dissolve | fallback`). Rejected: the policy
enum decides what happens when there is NO way to keep the agent running;
this feature is the opposite case — the agent keeps running exactly as
before, on a different lane, so cramming it into the same enum would make
`fallback` behave unlike its three siblings (none of which is conditional on
a second, unrelated setting existing). A boolean gated by `api_fallback`
mirrors how `api_fallback` itself is not folded into `headless`.

### D-150 · initializing an agent opens ITS desk — and only when YOU initialize it
Ruling (user, 2026-08-26): "when initializing an agent, automatically zoom
into the desk view for it." Confirming the hire on the dashed
**uninitialized** draft box now glides the camera to the new agent's desk;
on a sheet-gated viewport the desk sheet opens instead.

Why: hiring is only half the gesture. A new agent sits idle until someone
messages it — the tool docs say so to agents in as many words — and the desk
is the only place that message can be typed. The draft form used to leave you
at roughly overview zoom (`spawn` clamps to z 1.7–2.05, deliberately *under*
the 2.1 desk threshold, so the form renders at authored scale), with a fresh
card among its siblings and a second gesture still owed before you could say
anything to it.

(D-numbering note: D-149 was taken by an entry uncommitted in another seat's
working tree when this one was written, so this is D-150 and the two are out
of merge order. That is fine — a D-number identifies an entry, it does not
place it in a sequence, and renumbering breaks every reference already
written.)

**Scope, stated because a narrowing nobody writes down is an omission the
next person silently reverses: only hires made in THIS browser, from the
draft form.** The trigger is armed in `confirmDraft`'s response handler, not
off the tree refresh. An agent calling `orgtree_hire` deep in its own subtree
arrives by the same broadcast and must NOT move the user's camera — the
user's word was *initializing*, which is a thing the user does, and a
viewport that jumps on events its owner did not cause is one they have to
fight for control of. If this is ever widened, widen it deliberately.

Bounds:
- **It waits for the card to be BORN and to ARRIVE**, in the spring tick, not
  in the response handler. Two distinct reasons: the hire response carries an
  id frames before the tree that gives that id a position (so there is nothing
  to centre on yet); and a card still travelling is a card the camera would
  centre on where it is *going to be* rather than where it is.
- **A target with no spring is a phantom, not a race.** The tick's own spring
  loop creates a spring for every laid-out id before this check runs, so the
  two conditions are ordered, not coincidental.
- **Arrival is `atRest`, the shared predicate — not a threshold of this
  block's own.** Originally this read `|Δpos| < 1`, which was both a second
  spelling of an idea the spring loop already owned and a WEAKER one: position
  only, no velocity, so it admitted a card crossing near its target at speed —
  precisely the case where "settled" is most wrong. `6ad71b3` lifted `atRest`
  to module scope for the follow; this now reads the same predicate, so
  arrival cannot drift between the three places that ask about it.

**Amended 2026-08-26, same day, and the amendment matters more than the
change.** As first written this bound said the wait existed because the №25
follow froze the screen offset it engaged at, so centring mid-glide left the
card off-centre *permanently*. That was true when written and is no longer:
`6ad71b3` fixed it at the source — the follow now only ENGAGES on a node that
has arrived. So **the wait is belt-and-braces, not load-bearing**, and its
author verified that directly by removing it against the fix (green).

It is kept regardless, for a reason worth more than the frame it costs: it
makes this feature correct on its own terms rather than by borrowing a
guarantee from a gate three blocks up that nothing here would notice losing.

⚠ **And the original entry's "Verified" line was overclaiming, which is the
part worth recording.** §1-§4 never exercised this wait at all — the draft
sits at the slot the hire lands in, so the birth spring is settled almost
immediately and the condition is satisfied on the first frame it is tested.
That was not deduced, it was measured: with the wait removed AND the follow's
arrival gate disabled, all four still passed. A guard can be genuinely
load-bearing and still be covered by nothing, and a suite that goes green
either way cannot tell you which. §5 was added to close it — the layout moves
under the hire so the birth spring genuinely travels — and it fails, alone,
when the wait is removed.
- **It yields to a live gesture** (`!panRef.current && !animBusyRef.current`),
  the same yield the follow uses. Since `b5fdc00` (the same-day drag-zoom
  fix, which carries its invariant as a comment above `rebasePan` rather than
  as a numbered entry) `animateTo` also rebases an interrupted drag's anchor,
  so this yield is belt-and-braces rather than load-bearing — kept anyway,
  because a glide that never starts into a drag beats one that starts and
  then repairs what it broke.
- **The pending id expires after 10s**, the same bound `seedRef` uses for a
  hire that never lands, so a failed or ignored hire cannot strand a camera
  jump that fires much later into an unrelated view.
- **Stamped and compared on `performance.now()`, not the rAF timestamp.** The
  two share an origin in a browser, so either reads correctly there — but the
  test rig's rAF hands out a mocked `Date.now()`, against which the 10s bound
  expires instantly and the feature becomes unreachable. A bound no test can
  get past is a bound nothing verifies.
- The glide is the ordinary `centerOn`, unmodified: interruptible by wheel or
  drag like every other camera animation, and inheriting whatever fit
  `centerOn` computes rather than pinning a zoom of its own.

Verified: `frontend/tests/deskinit.test.tsx`, five checks. The desk is
asserted as the DOM consequence (`.sq.desk`, the class `focusId` drives) and
the camera as the numbers this code wrote into `.space`'s transform — never
as measured geometry, which jsdom does not produce. §2 is the positive
control (no desk open at rest, z under threshold), §3 the mutation (a hire
whose node never arrives opens nothing), §4 pins the scope decision above,
§5 covers the arrival wait (see the amendment). Negative controls, each run
against the code it names: with the feature reverted, §1 fails and §2-§4 stay
green; with the arrival wait alone removed, §5 fails and §1-§4 stay green.

⚠ **What this suite does not see, stated so a green number is not read as
more than it is.** For `animateTo` the rig's rAF hands the callback a mocked
`Date.now()` while the ease stamps its start from the real `performance.now()`
— so the glide completes in its first frame here, and these checks verify
WHERE the camera arrives, never HOW it travels. Not *unobservable*, though,
and the distinction is worth the sentence: `1054c4c` showed the way out with
an opt-in `syncClock` that puts `performance.now()` on the same mocked clock
(which is all a browser does anyway — there both are real and share an
origin), scoped to the suites that ask for it rather than imposed on the
shared harness. This suite does not opt in because it does not need to: the
glide it triggers is the ordinary `centerOn`, whose travel is that fix's
subject and is covered there. If a later change makes HOW this camera moves
part of the feature, that is the technique to reach for — do not re-derive
this limitation as a dead end. That limit does NOT
extend to the spring engine, which is where the arrival question lives: the
loop clamps `dt` and re-bases `last` every frame, so the journey is genuinely
observable there — provided you step at 16ms. `advance(ms)` chunks at 250ms
by default and every rAF inside one chunk reads the same mocked clock, so the
springs integrate once per CHUNK: `advance(3000)` is about 0.4s of spring
time, not 3s, and a spring mid-flight then looks exactly like one that never
converges.
whose node never arrives opens nothing), §4 pins the scope decision above.
Negative control run: with the source reverted and the tests kept, §1 fails
and §2–§4 stay green.
### D-149 · an auth failure is REPORTED, never routed around

Ruling (user incident 2026-08-25, assessment requested by coordinator and
accepted in full): when a turn dies because this machine's login will not
authenticate, orgtree **names the cause loudly and stops**. It does not fail
over to a fallback key, and it never writes the failure into `usage_refreshes`.

What happened: a haiku seat's first turn died, and the operator was shown, in
full, `the CLI exited 1 without writing anything to stderr`. The real reason —
`Failed to authenticate: OAuth session expired and could not be refreshed` —
sat in the CLI's own transcript the whole time. The user worked it out and
re-logged in by hand. That message is worse than silence: it reads like an
orgtree bug, so it points away from the one action that fixes it.

**Why the existing machinery could not see it, and why that is not a bug in
it.** `_result_detail` and `_looks_like_auth_failure` read the RESULT EVENT on
purpose — a number cannot accidentally contain "usage limit reached" (D-133,
and see their docstrings). But this failure emitted **no result event at all**
and no HTTP status: the OAuth refresh failed locally, before any authenticated
request went out. Every reader of `res` abstained, and an abstention reads
exactly like "nothing was wrong". The fix therefore reads the STREAM, and adds
a route rather than widening the existing one.

⚠ **TWO CARRIERS SHARE ONE TYPED VOCABULARY, AND ONLY ONE IS MEASURED IN THE
STREAM.** The CLI hands us a typed `error` code, not prose — read out of the
shipped binary by chunked byte scan (D-147's method; `strings` is still not
installed here): `authentication_failed`, `oauth_org_not_allowed`,
`account_on_hold`, `billing_error`, `rate_limit`, `model_not_found`,
`invalid_request`, `server_error`, `max_output_tokens`, `dlp_request_denied`,
`unknown`. Measured 2026-08-25 against the shipped CLI (loopback 401 +
fabricated key, no real credential): it arrives on
`{"type":"system","subtype":"api_retry","error":"authentication_failed"}`, one
event per retry, live, **before any outcome**. The real incident's carrier was
different — a synthetic assistant message with `isApiErrorMessage: true` — and
that one is confirmed only in the transcript file. Both are read, and the site
says which half is measured and which is inferred. **The prediction was wrong
about the carrier**: a branch written against the assistant message alone
would have matched nothing, silently, and only running the measurement caught
it. Do not collapse the two.

**Why not route around it** (the question coordinator asked, and the reasoning
matters more than the answer). The worry was that we could not tell a
temporarily-expired session from a permanently revoked one. The truth inverts
that: **every member of the auth family is permanent until a human acts**, so
there is no temporary case to misclassify — which makes routing worse, not
safer. Failing over on a usage limit buys real working time, because a limit
clears itself. Failing over on an auth break buys only **delay before the user
finds out**, and pays the fallback's capacity for it. Even the narrow
route-once-and-warn version was declined: *"it kept working" is the single most
reliable reason people don't act on alarms.* An auth failure costs no tokens,
so stopping wastes nothing.

⚠ **AND IT MUST NEVER BECOME A CAPACITY MARK.** `usage_refreshes` means "used
up until T". There is no T here. An invented one makes the primary silently
return, still broken, and die again — flapping that reads as a router bug.

Bounds:
- **RECORDING ONLY.** The capture feeds `_for_the_record` and nothing else: no
  freeze, retry, resume, mail or account switch. Pinned structurally by
  `test_auth_cause` §4, which mirrors `test_harvest` §7 onto the new route —
  the old check watches `_looks_like_auth_failure`, and this path never calls
  it, so without a second walk a wired capture would trip nothing.
- **The stream is a FALLBACK, never an override.** It is consulted only when
  the result event said nothing — which is exactly the blind spot. If the CLI
  accounted for itself, that account wins, so a stale early retry cannot
  contradict a real outcome, and a turn that recovered from an auth blip and
  then died of something else is reported as what actually killed it.
- The widened text still never becomes MAIL (`_for_the_record` rule 2:
  auth-failure text arriving as mail is what has repeatedly destroyed
  fable-tier sessions here), and an empty `err_blob` still yields an empty
  record, so a manual ⏸ cannot be booked as a failure (rule 3).
- `rate_limit` is deliberately NOT in the auth family: the freeze machinery
  owns it, and duplicating it would give one failure two voices.

Method note, and it is the reason this entry can be trusted: **a check written
here to prove the subtype literal was live code asserted `'"api_retry"' in
_SRC` — and the mutant survived**, because the literal also appears in the
comment above the branch. That is this subtree's signature failure (a name
matched inside a COMMENT rather than in live code) reproduced inside the very
suite meant to prevent it. It is now asserted on the AST comparison node,
which no prose can satisfy, and the mutation round is what found it.

Still open, deliberately: **the accounts panel still shows a healthy signed-in
primary while every turn fails**, because `live_identity()` reads
`oauthAccount` out of `~/.claude.json`, which is metadata that survives the
session's death. Approved and not yet built — reading `expiresAt` and
`refreshTokenExpiresAt` from the credential store under four conditions
(two integers only, one narrow named function, a proof that no token value
reaches any payload, and a decision amending "incapable of touching a token"
to "never the token VALUE").

### D-155 · one rule, three copies: the severity thresholds are a recorded hazard, not a refactor

Found 2026-08-26 while answering another org's question about an under-specified
test fixture. Recorded rather than fixed, deliberately, and the reasoning is
the point of the entry.

The usage severity rule — gold from 75 %, red from 90 %, overridden by an
explicit `severity` — exists **three times**:

| where | what |
|---|---|
| `frontend/src/App.tsx:764` | `usageSeverity`, exported |
| `frontend/src/App.tsx:766` | an inline copy inside it |
| `frontend/src/canvas/accounts.tsx:70` | `sevOf`, a second inline copy |

**They agree today.** Measured, not assumed: the bodies are byte-identical
with whitespace removed. (⚠ The first comparison said `DIFFER` and that was a
mismatched line range in the comparison, not a finding — worth recording
because a false alarm here would have cost another team a file hold.)

**Why it is written down instead of collapsed.** Merging them means editing
`App.tsx` and `accounts.tsx` while another org has live seats in that
directory, to fix something that is not currently wrong. That trade is bad
today and fine next week. A recorded hazard with line numbers is worth more
than a refactor done at the wrong moment.

⚠ **Why it is a hazard at all, and the sentence that makes it matter: these
agreed for months before `a27b929` touched one copy.** The freeze-path defect
fixed the same day (no D-entry of its own — it is the commit that introduced
`subscription_lane` in `supervisor.py`) was exactly this — two hand-copied forms of one
condition, one of them strengthened, the other left behind, silently handing
back the wrong answer for a day. **This is that shape one step earlier**, before
anyone has edited one copy. The copies were the bug; the wording never was.

`styles.css` used to document the thresholds against the `App.tsx` copy *as
though it were canonical*, which is precisely how the next person picks the
wrong source of truth and believes they are finished. That comment now says
there are three and names them.

Related, same shape, from the other org and worth stealing: a test constant
that duplicates a source constant is a third copy of one idea. Their probe now
reads its budgets out of the source by regex, because otherwise someone raises
the source constant to make a unit test pass and the probe goes on silently
checking the old number — still green.

### D-154 · `api_cost_usd` is the FALLBACK SLICE, and it is zero for a permanent-key org

Recorded because it existed only in working notes, which is the exact state in
which the next person "fixes" it by reasoning about how cost *ought* to accrue.

`api_cost_usd` (`schema.py`, org-level, monotonic) accumulates dollars billed
to the org's own key **while an `api_fallback` window was open**. Every writer
goes through `_bank_api_cost` (`supervisor.py:1123`) and every caller gates on
the lane captured **at spawn**, so a window opening or closing mid-turn never
rewrites where that turn's tokens were billed.

⚠ **THE TRAP IS THE NAME.** It reads like "what this org spent on the API". It
is not. `api_fallback_active` requires **both** `api_fallback` and `api_key`,
so for a **permanent-key org** — `api_key` set, `api_fallback` unset — the
counter is never written and stays `0.00` forever, **while `bills_the_key`
returns True for every single turn.** For that org the API spend is not *a
slice* of the cost; it is *all* of it, and the one field named after it reads
zero.

So the invariant to hold in mind, and to state wherever this is surfaced:

> For a permanent-key org, every turn bills the key, so the org's **total**
> cost IS the API spend. `api_cost_usd` is meaningful **only** as the fallback
> slice — the part of a *subscription* org's spend that went to the key while
> a limit window was open.

It is not a bug and must not be "fixed" by banking permanent-key turns into it:
that would make the hover split on the cost card report the same number twice
for those orgs, and would silently change the meaning of a monotonic
org-lifetime counter that existing documents already carry. If a total-API-spend
figure is ever wanted, it is a **new** field, and the honest answer for a
permanent-key org is `cost_total` itself.

### D-153 · two ways a test run reports a pass it never measured

Not a feature. A method entry, recorded in its own right (coordinator's
instruction 2026-08-26) because both halves were found **inside one afternoon's
work on D-152**, both are invisible by construction, and both would otherwise
be rediscovered the expensive way. They are the same shape — *the check
abstained, and an abstention reads exactly like a pass* — wearing two costumes.

#### 1 · a stale `.pyc` ran the whole mutation round while the source read clean

CPython validates a cached `.pyc` against the source file's **(mtime in whole
seconds, size)**. Both. Nothing else.

Mutant M6 of the D-152 round swaps the constant `"fable"` for `"fabel"` —
**the same number of bytes** — and `shutil.copyfile` put the restore inside the
same second as the mutation. Both freshness tests therefore passed, the
interpreter never re-read the file, and **the suites went on executing the
mutant against source that was clean on disk.**

What was checked at the time, and what each check was actually worth:

| check run | said | worth |
|---|---|---|
| `git diff` | clean | true, and beside the point |
| `grep 'FABLE = '` | clean | true, and beside the point |
| restored bytes `== `original bytes | `True` | true, and beside the point |

All three describe **the file**. None of them describes **what the interpreter
loaded**, and that was the only question.

⚠ **Five of the six mutants would have been reported "killed" off a run that
never touched them.** That sentence is here so nobody deletes the purge step as
an unnecessary slowdown — it is not a slowdown, it is the difference between a
mutation round and a ritual. M1–M5 happened to change the file's *length*, so
they invalidated the cache by luck, not by design; the round's validity rested
on a coincidence nobody had noticed they were relying on.

The only thing that caught it was **re-running the baseline after the restore
and finding it red** — done out of habit, with nothing pointing at it. Two
rules follow:

- **purge `__pycache__` before every run in a mutation round;**
- **always re-run the baseline after restoring, and treat any drift as
  invalidating the round.** Comparing file content to the original tells you
  the file is right. It does not tell you the file is what ran.

#### 2 · `rc=$?` after a pipeline measures the pipeline's LAST command

An hour later, in the same task, verifying the same change:

```bash
out=$(timeout 300 python "$t" 2>&1 | tail -2 | tr '\n' ' '); rc=$?
```

`$?` here is `tr`'s exit status. `tr` always succeeds. **Every suite in that
loop reported `rc=0`, including one that hit its timeout and was killed.**

It was caught only because the reported line for `test_turn_lifecycle` was
*empty* — no `ALL N CHECKS PASS`, no failure summary, nothing — and a suite
that prints nothing is worth a second look. Run directly, it returned **124**:
timed out. Every suite in that batch whose tail happened to include a summary
line was genuinely verified *by the summary line*, not by the exit code; every
suite that printed nothing quotable had been reported green on no evidence at
all.

Rules:

- **capture exit codes outside the pipeline** (`${PIPESTATUS[0]}`, or drive the
  process from Python and read `returncode` — which is what `tools/run_tests.py`
  already does);
- **and prefer `tools/run_tests.py` to a hand-rolled loop.** It exists, it
  reports per-suite status honestly, and it has a `--full` tier. A loop written
  fresh in a terminal has none of that and looks exactly as convincing.

#### the common shape

In both cases the operator asked a question the machinery could not answer, and
the machinery answered anyway — with the *shape* of a pass. Neither failed
loudly; neither could have. **When a check cannot fail, it is not a check.**
The generalisation for this tree: verify the thing you actually care about
(what ran, what exited non-zero), never a proxy that correlates with it
(what's on disk, what the last process in a pipe returned).

### D-152 · fable rides along with a subscription limit — when it has nothing of its own

Ruling (user, 2026-08-26): *"if any non-fable tier agent goes out of capacity,
and fable does not have a refresh period, then set fable's refresh period to
the same one as well."* This **amends D-148**, which had put fable
deliberately outside the haiku/sonnet/opus pool one day earlier.

Fable is still **not in the pool**. It rides along, and the distinction is the
whole design:

| | the POOL (D-148) | the RIDE-ALONG (this) |
|---|---|---|
| direction | symmetric — any member marks all | one-way — a fable limit spreads to nobody |
| an existing mark | `max()` — a sibling parked earlier is **pushed out** | **left alone**, in either direction |
| `tier_standing.pool` | names the bucket | still `None` for fable |

Three lines in `accounts.record_limit`, after the pool mirror:

```python
if tier != FABLE and FABLE not in marks:
    marks[FABLE] = ts
```

**Why absent-only rather than `max()`.** A real fable limit is WEEKLY; a
subscription window is hours. `max()` would never shorten one, so it passes
the obvious check — but an unconditional write would replace a mark days out
with one hours out, hand back fable capacity that does not exist, and walk the
account into the same weekly wall every five hours until the week turned over.
Absent-only is also what makes the feature testable: without it, "mark every
tier in `TIERS`" is indistinguishable from the correct implementation.

**Why per-account.** `usage_refreshes` is `[account][tier]`. Only the account
that ran out gets its fable parked; parking fable machine-wide because one
lane hit a wall would be a much larger and worse feature.

**Nothing is blocked by the extra mark.** With every lane marked,
`_resolve_in` still names the soonest-refreshing account and a spawn goes
there, re-marking itself if it fails. The cost of being wrong is one probing
spawn; the cost of being right is not burning a fable turn — the most
expensive tier there is, at 10 seats — on an account that has just proven it
has nothing left.

`_prune_expired` runs before the branch, so "fable has no refresh period" and
"fable's refresh period has passed" are one state by the time it is read.
Expired reads as capacity, and capacity is exactly what this rule may spend.

⚠ **The mutation round for this feature was run TWICE, because the first one
was invalid** — a stale `.pyc` kept executing the mutant while the source read
clean. It is written up as **D-153**, on its own, because it is a fact about
this repo's test method and not about fable.

Bounds and coverage:
- `record_limit` remains the only writer of `usage_refreshes`, pinned by an
  AST walk over every other module in the package (`supervisor.py` names the
  key in a *comment*, which is why that check walks the AST rather than
  grepping — D-149's method note, applied).
- The ride-along branch itself is pinned to **live code** the same way: the
  guard is asserted as `Compare` nodes and the body as an `Assign` to
  `marks[FABLE]` from `ts`. No comment or docstring can satisfy either.
- `test_fable_piggyback` (32 checks) is the feature's own battery;
  `test_account_pool_state` grew from 34 to 37 and had four checks flipped —
  1.3, 1.8, 2.6 and 2.11 asserted that fable stayed untouched. Each is marked
  as amended at its site, and the controls that keep the pool from degenerating
  into "mark everything" (1.4, 1.4l, 1.4e) are new.
- Six mutants, all killed. M4 — fable takes the pool's resulting `max()`
  instead of the recorded time — passes the pool suite untouched and is caught
  only by the new one, which is the argument for having written it.
- No frontend change: `TierStandings` renders whatever tiers the payload
  carries, so the capacity modal shows fable waiting with the rest on its own.

### D-148 · one usage pool for haiku/sonnet/opus; a key row shows its own state

Ruling (user, 2026-08-25, three parts in one message):

1. **haiku, sonnet and opus bill against ONE bucket.** "if any one of them hits
   a usage limit, then mirror that usage refresh time to the other two on that
   account." `record_limit` — still the only writer of routing state — now
   writes the mark to every tier in `POOLED = (haiku, sonnet, opus)`. `fable`
   is deliberately outside it: the user named exactly three, and fable's lane
   is billed separately (D-084's `fable_api_fallback` exists because of that).
2. **A key row's usage button shows the internal routing state for that
   account**, not usage percentages: which models have capacity, which are
   waiting, and until when. D-147 established the percentages are unobtainable
   for a setup-token key; this is what to put there instead.
3. **Duplicate-of-primary greying is dropped** — "since thats infeasible".

On (1), what was actually broken: an opus turn hit the wall, marked opus, and
failed over correctly — and the next *haiku* turn walked straight back into the
same exhausted account, because haiku carried no mark of its own. One wasted
spawn per sibling tier, every time, each one re-earning the same 403/429. The
test therefore ends on `resolve("haiku")` rather than on the dict: the dict is
the mechanism, the wasted spawn was the defect.

⚠ **The mirror is a FLOOR, never a ceiling.** A sibling already parked LATER
keeps its later time; lowering it would hand back capacity nobody watched
return. This is not hypothetical on deploy day: every `accounts.json` already
on disk was written by the old single-tier writer, so lopsided pool state is
exactly the state this ships into. (It cannot arise afterwards — once the
mirror exists, no pair of `record_limit` calls can leave a pool uneven, which
is why the test seeds that fixture by hand.)

On (2), `tier_standing()` is a straight read of the same `usage_refreshes`
dict `_resolve_in` routes off, so the view cannot describe a state a spawn
would disagree with. ⚠ **`available` means "this account has capacity for this
tier", NOT "this tier runs here."** The two differ constantly — a fallback has
capacity for opus the whole time opus is happily running on the primary above
it — and the panel's gutter chips, not this table, answer the routing
question. Wording it as routing would tell the user their untouched fallback
was out of opus whenever the primary happened to be serving it. The payload
also carries each tier's `pool`, so the modal's "these three share one bucket"
footnote comes from the server rather than from a second copy of `POOLED`
living in the frontend.

On (3), this RETIRES the same-day ruling in D-145 that greyed such rows and
excluded them from routing. The underlying observation stands (measured
2026-08-24 21:20Z: the re-driven turn hit the identical session limit 4.2 s
later) — what died is the DETECTION. Per D-147 the profile endpoint wants the
same `user:profile` scope the usage endpoint does, so `account_uuid` never
resolves for a key registered from now on, and the check could only ever fire
for rows carried over from a v1 registry, which are keyed by uuid for
unrelated reasons. A guard that fires for one row in a hundred is worse than
no guard: it makes the panel's behaviour unexplainable. This settles the "not
yet chased" consequence D-147 left open. Do not reintroduce it without a way
to learn a key's account that actually works — and note that if such a way
ever exists, it is also the way to make per-key usage percentages work, so
that discovery reopens both entries at once.

Bounds:
- `record_limit`'s existing refusals are untouched and re-asserted: unknown
  account, unknown tier, and an already-past refresh time each mark nothing.
  An unknown tier is a pool of one, so a bad `_tier` string cannot park the
  whole bucket.
- The primary row is unchanged — it reports real usage, and a standing table
  there would be a second answer to a question already answered.
- Building the table makes no network call (D-147's rule survives the new
  feature; asserted under a tripwire that is itself proven able to fire).
- `account_uuid` is still stored and still rendered beside the row: it is
  identity for the user to read, and only the ROUTING and GREYING behaviour
  keyed off it is gone.

### D-147 · a `claude setup-token` key can NEVER read usage limits — do not ask

Ruling (2026-08-25, from evidence rather than preference): `account_usage`
makes NO network call for a key row. It answers `unsupported` from local
state, the panel renders that as a settled note, and nothing retries.

Why — and this is the entry someone will otherwise reverse in three months by
reasoning from first principles about how usage *ought* to work. Read out of
the shipped Claude Code binary (`bin/claude.exe`), verbatim:

> "Long-lived tokens (from `claude setup-token` or `CLAUDE_CODE_OAUTH_TOKEN`)
> are limited to **inference-only** for security reasons."

> "…OAuth token has no scope accepted by `/api/oauth/validate` (needs
> user:profile, user:office, or user:ccr_inference; **env-var and setup-token
> sessions default to user:inference only**)"

and the CLI's own usage fetch, which will not even attempt the request:

```js
A4e = "user:profile"
function KM(){let e=ya()?.scopes; return Array.isArray(e)&&e.includes(A4e)}
if(!ds()||!KM())return{};                     // ← before any request
_s.get("/api/oauth/usage",{timeout:5000,refreshOAuth:!0,credentials:e})
```

So the scope required is `user:profile`, a setup-token key carries only
`user:inference`, and Claude Code itself declines client-side. The 403 we saw
is the server enforcing the same rule — not a revoked key, not our bug. The
429s were the edge throttling us for repeatedly making a forbidden request,
which is why the two interleaved on one credential minutes apart.

⚠ **Backoff was the wrong shape and was nearly built.** The fix for a request
that must never be made is not to make it more politely. The D-146 cooldown
machinery stays — `fetch()` (the host lane) legitimately calls that host — but
key rows leave that path entirely.

Bounds: this is about the TOKEN TYPE, not the account. The host lane still
reads usage normally, because `subproxy` presents a refreshed OAuth access
token carrying `user:profile`. Refreshing or re-minting cannot widen scope —
scope is fixed when a token is issued — so "re-mint it" is not a remedy; only
a full `claude auth login` grants `user:profile`, which is the in-app-OAuth
path D-144 rejected on ToS grounds. The same scope wall applies to
`/api/oauth/profile`, so `resolve_key_identity`'s lazy retry was removed from
the usage path too; ⚠ **a consequence: duplicate-of-primary detection can
never resolve for a NEWLY registered key** (the one row on this machine has a
uuid only because a v1 migration keyed it that way, not because a profile call
succeeded). Chased and settled the same day — the feature was dropped: D-148.

Load-bearing: `unsupported` is a distinct field from `available: false`
precisely so the UI can tell "impossible" from "unknown" — rendering them
alike invites the user to keep clicking a button that can never do anything.
And the user-facing string must not prescribe re-minting: the previous wording
("re-mint it with `claude setup-token`") was approved and shipped that morning
and was a dead end dressed as an instruction — a ritual that cannot work, from
which the honest conclusion is "my key is broken".

Method note, for the next person verifying a claim about a shipped binary:
`strings` is NOT installed on this machine and a probe using it returns
vacuous "clean" results for every file. This was scanned with chunked Python
byte-search. A scan that finds NOTHING is not evidence of absence unless it
has been shown to find a known positive first.

### D-146 · a 429 from the usage endpoint gates the REQUEST, not just the message

Ruling (user report 2026-08-25, "when i try to query the usage limits for the
secondary key, i get constant 429 errors"): `limits` honours `Retry-After` and
will not re-ask an account's usage endpoint until the window it was given
closes. The window is per cache key — the host and each key row hold their own
— and it gates the outbound request, so a caller inside a window gets stale
bars if any exist and otherwise a readout naming the wait
("rate limited by the API — retry in 17m"), never a fresh packet.

Why: the module cached only SUCCESS. A failure cached nothing, so every click
re-asked at full rate while the upstream was explicitly saying wait — the
user's "constant" was a guarantee of the design, not bad luck. Measured that
day against the failing key, two back-to-back calls with no retries: `HTTP 429,
Retry-After: 1032, server: cloudflare, {"type":"rate_limit_error"}`, the SAME
1032 both times (a fixed deadline, not a per-request penalty), while the host
readout answered 200 — so the endpoint, the network and the token were all
fine and the only defect was ours. `fetch()` had the identical hole and is
fixed with it; `force=True` and `max_age=0` do NOT punch through, because the
freeze-correction pass is precisely the caller that would turn one rate limit
into a storm of them.

Bounds: **a window is opened by a THROTTLE, identified by evidence — not by a
status code alone.** A 429 always is one. A **403 is one only on evidence**: a
`Retry-After`, a `cf-mitigated` header (present only while the edge is
actively mitigating — its value names the flavour and is not worth matching),
or a body naming rate limiting or a Cloudflare block page. ⚠ The edge
ESCALATES — a client that keeps asking through a 429 starts getting 403
instead (user report 2026-08-25, "im getting a 403 forbidden now on the usage
check for secondary keys as opposed to a 429": same key, same machine, while
the host readout kept answering 200) — so hammering through the escalated form
is the exact harm the 429 window exists to prevent.

Every OTHER 403, plus 401, 500 and transport blips, stays retryable on the very
next call and now says what to do about it ("this key was refused (403) — …
re-mint it with `claude setup-token`"). A credential the user is about to
re-paste must not sit behind a cooldown, or the fix would look like it had not
taken. Anything unreadable counts as NOT a throttle: failing open costs one
extra request, failing closed hides a broken credential for an hour.
A `Retry-After` that is absent, malformed, zero or negative falls back to
`DEFAULT_RETRY_AFTER` rather than to zero (a zero would be a hammer loop
authorised by a header), and any value is clamped to `MAX_RETRY_AFTER`
(6 h) so a hostile or absurd one cannot lock a lane out indefinitely.

⚠ **The penalty escalates, and that is why the clamp is set far above any
observed value rather than near one.** Measured the same day: the account
answered `Retry-After: 1032` at 11:30 and `Retry-After: 3600` twelve minutes
later, after a handful of further asks — asking inside a window lengthens it.
A clamp at 1 h would therefore have silently truncated a real 3600 into an
early re-ask and earned a longer window; the clamp guards against absurdity,
it is not a statement about how long we are willing to wait. Waiting too long
costs a stale panel. Waiting too little costs the window.

Windows live in memory, so a restart clears them — accepted: a deploy costs at
most one extra probe, and persisting them would outlive the condition they
describe.

Load-bearing: the readouts stay split (`_cache` host, `_key_cache` per row) so
a fallback key's bars can never time a freeze off someone else's quota, and
the cooldowns are keyed the same way for the same reason. `invalidate()` now
clears the key readouts and the windows too — module state it does not reset
is state that leaks between tests, and a leaked window makes a fetch return a
cached refusal without touching the transport, which is indistinguishable from
a pass.

### D-145 · account routing is machine-local, per model tier, and automatic

Ruling (user, 2026-08-25, three mails — superseding the whole D-144-era
stack: the identity registry, passive adoption, labels, per-org pins, the
per-org selection (`account_token_uuid`), the serve-from control, the desk
"ran as" badge, and the one-switch-per-turn failover): the TOTAL internal
state deciding which account serves a prompt is
`usage_refreshes[account][tier] = refresh-at | absent` — an entry exists iff
that account's capacity for that model tier is used up, and holds the epoch
when it refreshes. Machine-global, in `accounts.json` (version 2), beside an
ordered list of registered key rows. **All other account state not related
to this machine is dropped** (the user's words), and `Org.__init__` pops the
stale `account_token_uuid` from old docs so nothing can look selected while
nothing reads it.

The accounts, and the panel that IS their UI:
- **PRIMARY** is whoever Claude Code is signed in as on this machine, read
  live from the CLI's own config (`~/.claude.json` → `oauthAccount`), shown
  by EMAIL, first row, not draggable, **not switchable from any UI** — the
  CLI login is the only mover. No token is injected for it; the CLI reads
  its own credentials store.
- **Secondary rows** are pasted `claude setup-token` keys: drag grip (the
  row order IS the routing priority), a greyed-out input whose value is
  OMITTED (the server never returns key material, not even masked), a
  usage-limits button, a delete button. A final row — live input plus ✓ —
  registers a new key. Row ids are a hash of the token; tokens stay in the
  separate token store (`tokens.py`, unchanged), and the registry keeps its
  `_reject_secrets` guard.
- **Routing**: a tier runs on the highest-priority account (primary, then
  keys in order) whose `usage_refreshes` entry for it is absent or expired.
  `accounts.resolve(tier)` is the ONE rule, shared by the spawn seam
  (`spawn_env(org, tier)` injects exactly its answer) and by the panel's
  H/S/O/F gutter chips (`assignments`), so the two cannot disagree. When
  nothing has capacity the resolver names the soonest-refreshing account:
  the chip sits there dimmed with the refresh time, and a spawn probes it —
  a failed probe re-marks it, self-bounding.
- ~~**A key that IS the primary account** (matched by `account.uuid`, resolved
  from the key once at registration, lazily retried) is greyed whole,
  tooltip'd, and EXCLUDED from routing: switching to it re-spends the
  identical limit — the 2026-08-24 21:20Z no-op self-switch, now structural
  rather than incidental.~~ **RETIRED the same day by D-148**: a setup-token
  key cannot read its own profile (D-147), so `account.uuid` never resolves
  and the check could only fire for v1-migrated rows. Every key row is a
  routing lane now.
- **The header usage button lists EVERY registered account** — primary
  first, then each fallback — one section of bars per account
  (`GET /api/accounts/usage`, per-key readouts fetched with the key's own
  token and cached apart from the host cache, which keeps pricing freezes
  and the header glow host-only).

On a usage-limit turn failure: the account that SERVED (st `ran_as`, stamped
at spawn from the resolved env — kept precisely so the mark lands on the
right lane) gets `record_limit(served, tier, reset)`, reset from the error's
own prose, else the host usage readout **only when the host login served**,
else the 5-minute probe floor. Then re-resolve: a different account with
capacity ⇒ re-drive (durable "account switched" row + the subject-free
`ACCOUNT_SWITCH_DRIVE` mail, unchanged bytes — the fable-mail hazard rules
carry forward); nowhere ⇒ the loud refusal row and the ordinary freeze,
which is also when `fable_limit_policy` escalation now means "fable is
exhausted EVERYWHERE". D-144's measured 401 rule carries forward intact: a
rejected credential marks NO lane, stops rather than spends the next
account, and is loudly recorded.

Bounds: sandboxed orgs stay on the container's own credential (both lanes
excluded at the spawn seam); the org API-key lane is EXCLUSIVE and outranks
account routing (one spawn, one credential); watchdog shell spawns pass no
tier and stay ambient; kiosk visitors keep seeing none of this
(`_public_denied` freezes `/api/accounts` whole). Migration: a version-1
registry reads as version 2 in memory — rows for uuids with stored tokens
survive (a stored token is the one thing the user cannot re-create without a
re-mint), everything else drops; the first write persists v2 and readers
never write.

**Capacity is marked ONLY when a turn is actually refused — never inferred
from an observed readout** (user, 2026-08-25, ruling on a reported bug):
"we'll just depend on whether or not claude lets us run turns or not to
determine if capacity is available. capacity should only be marked
unavailable the moment a turn is refused, not assumed once 100% limit is
observed." ⚠ THIS IS THE DESIGN, NOT A GAP — it was reported as a bug and
ruled otherwise, so it will look like one again. The observation that
prompted it: `weekly_all` read 100%/critical on the primary while the H/S/O
chips still sat on that row, because only `fable` had a mark (only a fable
turn had actually died). Correct behaviour. `usage_refreshes` therefore stays
a record of REFUSALS, `record_limit` stays its only writer, and `resolve()`
must not consult `limits` — a readout at 100% is not a refusal, the two can
disagree, and the turn is the authority. Do not "fix" the chips by marking
from the bars.

Was. D-144's registry/pin/selection stack (now under Retired): identity
adopted from the live login, hand-set labels and waterfall order, per-org
pins nothing read, a per-org stored selection with a serve-from control, and
failover as a one-switch-per-turn org-field write.

### D-151 · the card's activity label is contained structurally, not by a fitting width

User bug 2026-08-26: "when an agent is actively working, their zoomed out
status text sometimes overflows and flows off of their card, going to the side
or below it."

Both halves were one cause. `Activity()` rendered the tool name as a BARE TEXT
NODE inside `.actlabel`'s flex row. A bare text node is an anonymous flex item:
it cannot be given `text-overflow`, and its automatic minimum size is its
longest unbreakable word. Tool names are nothing but long unbreakable words —
`mcp__resonite__get_sync_object_definition` shortens to
`resonite: get_sync_object_definition` — so the item shrank to min-content,
WRAPPED to a second line (the "below"), and that line was still wider than the
card (the "to the side"). Measured in Edge against the real sheet: a 108px
content box, min-content widths of 120–159px, spilling +12.47 to +50.95px past
the border. At 50.95px it reaches the neighbouring card.

The fix is structural, and deliberately so. The name now renders in its own
element (`.actlabel-text`), the row is `nowrap; min-width: 0; overflow: hidden`,
and the name is `nowrap` + `text-overflow: ellipsis` while the gear and the
animated dots hold their size. Nothing here depends on any string being short
enough. **The label's text is arbitrary — it is whatever tool the turn called —
so a fix that has been measured to fit is only a fix for the names someone
thought to measure. A fix that cannot overflow needs no such list.**

**Why the card did not simply get `overflow: hidden`.** That is the obvious
one-line answer and it is wrong here: `.cbar` hangs deliberately OUTSIDE the
card at `left: -22px`. Clipping `.sq` would cut every credit bar on the canvas.
The containment has to live on the label.

**Why the card drops the `server: ` prefix** (`cardTool`, alongside `shortTool`
which the desk transcript still uses). The card fits ~15 monospace characters.
`shortTool` spends nine on the prefix, so `mcp__orgtree__orgtree_send_notice`
and `mcp__orgtree__orgtree_request_credits` BOTH truncated to `orgtree: orgtr…`
— a status line that cannot distinguish two states is not reporting one. For
these servers the prefix is redundant with the tail anyway. The full form
survives in the hover title: ellipsising is a display decision and must never
be the only copy of the information.

**On testing this, because the trap here is sharp.** The defect is text too
wide for a box; jsdom has no box model and reports 0 for every width, on broken
and correct code alike. So `tests/actlabel.test.tsx` asserts only the
STRUCTURAL contract the stylesheet needs (own element, no stray text node,
title carries the full name, two same-server tools stay distinguishable), and
`tests/actlabel_probe.py` measures the layout in real headless Edge — reading
`NODE_W`/`NODE_H` out of `shared.ts` by regex so the budget cannot drift from
the source, and guarding that its fixture still matches what `Activity()` emits.

Three things that green checks did not catch, recorded because each was caught
by the next layer up:

- The probe's first fixture wrapped the name in a span with NO class, so
  `.actlabel-text` matched nothing, the text wrapped to a clipped second line —
  and the probe reported OK, because the parent's `overflow: hidden` dutifully
  contained the mess. A probe measuring markup the app does not render is worse
  than no probe, because it is believed. Hence the fixture-vs-source guard.
- `.name` in the head measures ~114px past the card while rendering as a tidy
  `bug-over…`: `getBoundingClientRect`/`Range` ignore clipping ancestors. The
  probe now intersects every rect with each `overflow != visible` ancestor and
  asks what is actually PAINTED. Without that it both cried wolf AND could
  never have seen this fix work, since this fix works by clipping.
- The `orgtree: orgtr…` collision was invisible to every numeric check — all of
  them passed — and obvious in a screenshot. **Look at the thing.**

Both probe controls are known-negatives that must FAIL: `--expect-fail` runs the
pre-fix sheet (must spill), `--expect-ambiguous` runs the prefixed labels (must
be caught as indistinguishable). An assertion never seen to fail is a
decoration. The unit tests were mutation-checked BOTH ways — reverting the fix
fails all six, and a "fix" that hides the status text entirely also fails all
six, so containment cannot be satisfied by rendering nothing.

---

## Retired

### D-144 · the account registry ships INERT — and Phase 1 green is not failover

Retired 2026-08-25, superseded by D-145 (machine-local per-model routing).
The measured 401/turn-shape material below remains true of the CLI and is
cited by D-145; the registry/pin/selection design it defends is gone.

Decision (creds-probe, 2026-08-24, multi-account Phase 1): this install may
know about more than one Claude subscription. `accounts.py` records WHO —
identity, waterfall order, and a manual per-org pin — and deliberately
records nothing about HOW. It selects no account for any turn and switches no
lane. Selection is Phase 2.

Accounts are keyed on `account.uuid`, resolved from `GET
/api/oauth/profile`. That key was chosen because it is the only candidate
proven to discriminate in both directions: **identical** across a token
refresh of one account, and **different** between the two accounts. Token
bytes cannot key anything — they rotate on every refresh, and a rotation
revokes its predecessor immediately (measured: the previous access token
returns 401 "has been revoked" with no grace window). A registry keyed on a
token would silently split one account into two entries on the next refresh.

The registry stores **identity, never credentials**. `_reject_secrets` runs
before every write and RAISES rather than redacting, on both value shape and
key name, because a registry that silently strips a token teaches its callers
that handing one over is acceptable. Tokens stay in the CLI's own credentials
store, which this module never writes. Passive adoption enforces its own
adjective rather than merely intending it: the store's mtime and size are
sampled around the read and any change raises `LiveStoreWritten`.

> ⚠ **PHASE 1 SHIPPING GREEN DOES NOT MEAN FAILOVER WORKS.** Read this before
> concluding from a passing suite, or from a panel showing two healthy
> accounts, that the waterfall is real. It is not, and the code looking right
> is exactly why this note exists.

#### What exists today, stated so the excluded case cannot be skimmed past

**A usage limit is classified unconditionally. The lane switch it drives is
NOT.** `_looks_like_usage_limit` fires on its own, but the `api_fallback`
window stamp (`supervisor.py`, nested inside that branch) sits behind a
further `elif` requiring ALL of: the org's `api_fallback` option ON, an
`api_key` stored, fable-tier eligibility, and `_trusted_blob` (a CLI-reported
limit — a self-diagnosed one gets no window at all).

> **THIS FEATURE EXISTS FOR A USER WITH TWO SUBSCRIPTIONS AND NO API KEY, WHICH
> IS EXACTLY THE CONFIGURATION IN WHICH THAT LANE SWITCH NEVER FIRES.** On such
> an org a usage limit freezes the node and waits for the reset; nothing
> switches anything. A reader skimming the condition list will assume they are
> the lucky case — nobody ever assumes they are the excluded population, so it
> is stated here rather than left to inference. D-130's fallback is a precedent
> for the SHAPE of a lane switch, not a rail this feature can ride.

**An auth failure is classified by nothing, and is terminal. This is MEASURED,
not reasoned.** A mid-turn auth rejection matches none of the FOUR classifiers
on that branch. Three are textual (`_looks_like_usage_limit`,
`_looks_like_connection_failure`, `_looks_like_filtered`); the fourth,
`_died_in_flight`, classifies by the SHAPE of the turn — `exit_only and
started and not boundary` — and is precisely how a blob matching no text
classifier can still be rescued into the retry branch.

Measured 2026-08-24, running the official CLI in this repo's own stream-json
shape against a genuine 401:

| observed | value |
|---|---|
| exit code | 1 |
| top-level events | `system`×5, **`assistant`**, **`result`** |
| `result.is_error` | true |
| `result.result` | "Failed to authenticate. API Error: 401 OAuth access token is invalid." |
| stderr (stdin closed, as orgtree spawns) | **0 bytes** |
| `res["errors"]` | `None` |

⇒ **`boundary` is True, and that alone is what makes the turn terminal.** A
top-level `result` event always arrives, so `not boundary` is False and
`_died_in_flight` cannot fire whatever else is true. All three text
classifiers also return False on that `result` text — true, and *irrelevant*
to the outcome. A control confirms `_died_in_flight` still returns True for
the shape it IS meant to catch, so this is a discrimination and not a
predicate that says False to everything.

⚠ **`boundary` is the ONLY thing preventing a retry loop — the other two
conditions both hold.** In orgtree's real spawn shape stderr is **0 bytes** and
`errors` is `None`, so `exit_only` is **True**; `started` is **True** (below).
`_died_in_flight` is therefore ONE condition away from firing. If a future CLI
ever failed to emit a top-level `result` event on an auth error, orgtree would
begin retry-looping against dead tokens with no code change on our side.

A measurement discrepancy worth recording, because the resolution is the
interesting part: an early rig observed stderr NON-empty and concluded
`exit_only` was False. That was an artifact of the rig, not a CLI difference —
it invoked the CLI without stdin, and the CLI emitted an unrelated *"no stdin
data received in 3s"* warning. `err_blob` takes stderr verbatim when the exit
code is nonzero, so on that rig the recorded error for an expired token was a
**stdin warning with nothing to do with the failure**. Re-run with stdin
closed, as orgtree spawns it, stderr is empty and the generic fallback takes
over. Two rigs agreeing on the OUTCOME while disagreeing on a SIGNAL is what
exposed which condition actually carries this branch.

Two things measurement showed that reasoning had wrong, both worth keeping:

- **`started` is True even at the very start of a turn**, and not for the
  reason the name suggests. The CLI emits a `model:"<synthetic>"` assistant
  message for its own error, and the supervisor sets its proof-of-life flag
  *before* testing for that synthetic marker. So `started` fires on the CLI's
  fabricated error message, not on the model having spoken —
  `_died_in_flight`'s docstring says that clause "excludes the failures which
  must NEVER retry… they die before the model ever speaks", and for auth
  failures it does not do what it says. `boundary` is carrying this branch
  alone.
- **The CLI retries a 401 ten times internally** before giving up. An expired
  token therefore burns ~10 API round-trips per turn before the turn even
  ends.

> ⚠ **AND THE TRAP FOR PHASE 2: a `_looks_like_auth_failure(err_blob)` would
> silently never fire.** `err_blob` is built from the stderr branch when the
> exit code is nonzero; with stderr empty it falls to a fallback that reads
> `res["errors"]` — `None` here — and yields the generic *"the CLI exited 1
> without writing anything to stderr"*. The CLI's own perfectly good
> `Failed to authenticate. API Error: 401 …` is **discarded**. Worse, the
> supervisor already reads that exact string and throws it away, because the
> only question it asks of it is `_looks_like_usage_limit`. So adding an auth
> classifier is not the fix — **the harvest must adopt `res["result"]` /
> `api_error_status` first, or the new classifier is a change that goes green
> and does nothing**, which is this subtree's signature failure. Two smaller
> consequences: the user-facing `last_error` and the `turn_error_log` row both
> read "exited 1 without writing anything to stderr" for an expired token, so
> it is undiagnosable from orgtree's own surfaces.

**Not measured:** a 401 arriving *after* genuine model output. Both
observations are turn-start-shaped; a synthetic mid-stream failure was
attempted and the CLI rejected it as a malformed stream rather than an auth
error. `boundary` was True in every failure shape produced (clean,
malformed-stream, genuine 401), so there is no reason to expect the
post-output case differs — but it has not been shown.

This matters more under the approved design than it would today: per-turn
token binding at agent wake means a bound access token ages out on its own
8-hour clock, and the CLI **cannot** refresh it, because
`CLAUDE_CODE_OAUTH_TOKEN` carries an access token only, with no refresh token
behind it. Per-turn binding therefore *introduces* precisely the failure mode
that has no recovery path. Phase 2 owes **the harvest fix first**, then a
positively-classified auth-failure class plus a re-drive — in that order, or
the classifier is inert and the failover is cosmetic.

#### Provenance, and a discipline note that outlives this entry

**Measured:** the classifier behaviour above, the turn shape, the revocation
semantics, the 8-hour `expires_in`. **Reasoned, not observed:** that the CLI
cannot self-refresh under the env var — inferred from the token's shape; no
run has been seen attempting it.

Two ways the first draft of this entry was wrong, both worth generalising:

1. It quoted *"nothing ever re-drives the node"* as though it described
   today's behaviour. That phrase is a **docstring**, in the past tense,
   describing the 2026-08-06 bug the function containing it was written to
   FIX. Matching a name inside a comment rather than in live code is this
   subtree's signature failure, and it had become load-bearing in a normative
   entry. Cite live code, or measure.
2. It stated the mid-turn outcome in **absolute** terms while its own
   provenance paragraph was carefully separating measured from reasoned. The
   discipline was present and simply was not applied to the one sentence that
   mattered most. A hedge belongs where the consequence is largest, not only
   where it is cheap.

Bounds:
- `readout()` reports `selection_active: false`. That field is the
  machine-readable form of this entry, so a panel cannot imply a working
  waterfall merely by rendering the registry.
- Pinning an unknown account raises rather than no-oping: a pin that silently
  fails to apply is indistinguishable from one that applied, which is the
  exact class of bug this feature is meant to make visible.
- `set_order` cannot delete an account by omission, and dedupes — the order
  stays a PERMUTATION of the known set, so a double-submitted panel POST
  cannot make the readout render one account twice.
- A read-modify-WRITE cycle uses `load(strict=True)` and REFUSES on an
  unreadable or future-versioned registry, leaving the file on disk to be
  recovered by hand. Blank-on-corrupt is safe to READ and catastrophic to
  WRITE BACK: the first draft would have replaced every hand-set label, the
  whole waterfall order and every pin with an empty registry, and a `VERSION`
  bump would have done it to every install at once.
- `relabel` takes the module lock. It was the one mutator that did not, and
  sync FastAPI endpoints run in a threadpool alongside `run_in_threadpool`
  adoption — so a scheduled adoption concurrent with a rename could vanish.
- Nothing here writes `~/.claude/.credentials.json`, refreshes any grant, or
  uses the two accounts concurrently. Serial use through the official CLI is
  the approved shape; in-app OAuth stays rejected on ToS grounds.

Was. considered storing the account's e-mail address in the registry so the
panel could label the two entitlements. Reduced to a masked hint
(`s*****e@example.com`) plus the uuid and a user-settable label: the panel's
requirement is only to tell two accounts apart, the registry is read by more
code than the credentials store is, and the uuid already carries identity.

### D-156 · an auth freeze is not a wait, and a dry pool can become wet

Two defects in one freeze record, found 2026-08-26 while tracing a user report
that a limit-parked agent's refresh time "doesn't adapt to account changes or
keys being added / removed".

**1. A rejected credential was re-probed on a timer, forever.** A 401 whose
text is *also* limit-shaped freezes as a usage limit, takes the blind
~5-minute probe floor, and `auto_resume_ready` wakes it every ~6 minutes
indefinitely. `untrusted` does not bound it — a CLI-reported 401 is trusted
evidence. On a CLI that silently falls back to a stored login, each probe
spends **another account's** quota. That is D-149's routed-around shape on a
timer.

**2. A node parked on a dry account pool never noticed a key being added.**
`until_ts` is stamped once, from the pool as it stood at freeze time, and
nothing re-derives it. That is the user's report.

**The fix is a positive fact on the record, stamped at freeze time:**
`fz["cause"] = "auth"` and `fz["pool"] = "dry"|"open"`, both written on
**every** pass because `_ensure_frozen` returns a *surviving* record — a
stale `cause` would park a genuine capacity freeze forever.

**Why `cause` is a STRING and not `fz["auth"] = True`.** `_resumable` refuses
any record carrying a `True` key it does not recognise, so a boolean marker
would make **▶ skip the node forever** — the operator could never resume it
after replacing the credential, which is the one action that fixes it.
Beyond that accident: `limit`/`connection` are **kinds**, `on_fallback`/
`untrusted` are **qualifiers on a kind**, and "why did this happen" is a
**third category**. Adding it to that allowlist types it as a qualifier and
invites the next person to add a genuine kind there by pattern-match — the
failure the guard exists to prevent, arriving through the guard's own door.

⚠ **The cost, stated rather than buried: fail-closed does not apply to
`cause` at all.** A future `cause` value that *should* park a node gets no
help from `_resumable` and needs explicit handling at **every** readiness
site. That trade was taken deliberately.

⚠ **THE MONEY TRAP.** The `elif` that opens `api_fallback_until` fires on an
auth freeze unless gated (`and not _auth_fail`). Ungated, a rejected
credential silently moves the whole org onto the user's metered key and the
operator learns it from the bill.

⚠ **A stale `cause` cannot be tested behaviourally, and that cost someone a
wrong-green.** `_run_one_turn` refuses to drive a node carrying `frozen`, and
▶ pops the record before resuming, so a re-freeze onto a surviving record is
only reachable mid-flight. The behavioural check written first **never ran its
turn and failed for the wrong reason** — the tell was `resume_texts` holding
one entry instead of two. Test it structurally and say in the check why.

⚠ **Any test touching the resolver must STUB THE LOGIN.** `_routing_order`
drops the primary lane entirely when nobody is signed in, so on a signed-out
machine `resolve` answers "no capacity" for every tier, always: every
"dry pool" assertion passes **vacuously** and every "capacity exists" one
fails for an unrelated reason.

**The anti-flap is `pool == "dry"`, and it must be a freeze-time fact.**
"Capacity exists now" is not evidence of anything new: **three** paths reach
the freeze with capacity standing available (the 401 branch marks no lane;
the api-key/no-tier branch marks no lane; a switch refused by the `_switches`
counter had somewhere to go and declined). Waking those on "capacity exists"
fires on the next 30-second tick, re-drives into the same wall, and repeats
forever. Only a freeze that **asked** the resolver and was told "nowhere" can
be told something new later.

**Toggle ruling (user, 2026-08-26): `auto_resume` off means off.** No
account-lane clause in the timer's toggle filter — a configured second
account is **not** consent the user did not give. So the pool-readiness path
is **dormant for default-configured orgs** and live only where the toggle is
on (`api.py` forces it on for headless orgs, *"a limit freeze must not park an
org nobody will un-park"* — which is also the severity argument for defect 1:
the loop ran forever exactly where nobody was watching). **The auth exclusion
and the money gate are live regardless of the toggle**, and they are the
larger half.

⚠ **`api_error_status` is NOT MEASURED.** The three CLI runs behind this
captured exit codes and stderr only. The reachability argument rests on the
synthetic-limit adoption path (`err_blob = synth_limit_txt`, limit-shaped by
construction, with nothing excluding a `res` carrying 401) plus this CLI's
recorded habit of shipping `subtype: 'success'` on a failed 401 — **not** on
any measurement. "We could not produce one" and "it cannot happen" are
different claims and only the second would justify not doing this.

**Measured here:** `test_limit_freeze` 245 checks / 0 failed. The fail-open
mutation of the roster-read handler is killed by a named check; it **survived**
the original round because nothing forced `accounts.resolve` to raise — a
guard that was correct and completely unproven.

**Provenance.** Defects traced and the chain published by this org; the
implementation began in the Resonite org and was returned here 2026-08-26
under a user ruling that orgtree development stays in the orgtree
organisation. Their `cause`-versus-allowlist reasoning is better than the
boolean marker this org originally proposed and is kept above as theirs.

#### Knowledge transferred with it, not yet acted on

- **`tools/run_tests.py` — two halves of ONE bug; fix both or neither.**
  (a) the guard-fired regex matches `no longer (contains|matches)` inside a
  **passing** `ok` label, so a guard reads as fired when it never ran, and the
  prose-stripper is applied only to **source** reads, never to stdout.
  (b) a guard is credited because a registry *lists* it rather than because it
  *ran* — the live message-visibility suite never calls the contract check its
  sibling does. Repairing only the loud half makes the summary look healthy.
- **`dogs · port: the DOWN edge fires` — FIXED 2026-08-27, and the recorded
  diagnosis was only half of it.** This entry used to read "a real flake, not
  a regression: it binds a socket, arms a 15-second port watchdog, sleeps a
  fixed 8 seconds, then closes; if the first poll slips past the close, no UP
  edge is observed and no DOWN edge can fire. Fails under load, passes idle.
  Fix by polling for the UP observation rather than sleeping." That is true
  and it is not the root cause. Fixing only it does not fix the check —
  measured: with the socket held OPEN for a full 120 s the dog still reported
  DOWN throughout.

  **The root cause is the fixture.** It called `listen(1)` and never called
  `accept()`. Such a socket answers exactly ONE probe: the first connection
  fills the backlog and every later connect is REFUSED while the socket is
  still bound and listening. Six back-to-back `_wd_proc_alive` calls against
  it went `True, False, False, False, False, False`. And `watchdog_create`
  runs a SMOKE probe ("port:N is UP right now") that consumes that single
  slot — so the engine's first real check already saw DOWN, recorded
  `high_water.up = False`, and the dog could never show an edge. It then
  reported `fired: 0`, which is indistinguishable from a healthy dog still
  waiting: an abstention reading as a pass, this subtree's standing failure.
  A bigger backlog alone does not fix it either, because unaccepted
  connections accumulate. A real service accepts, so the fixture now does —
  `listen(64)` plus a daemon accept-loop stopped before the close.

  Two things kept as well. The check now polls `high_water.up` (the UP
  observation `_wd_tick` actually records) before closing the socket, under
  its own assertion, so a missed UP is reported AS a missed UP rather than
  letting the DOWN-edge check take the blame — that assertion is what exposed
  the fixture. And both waits are 120 s, not 60: on an IDLE machine the dog
  was created at 23:23:28 and its first check landed at 23:24:22, 54 seconds
  later, because `_wd_tick` walks every org's every dog on one thread and a
  slow sibling stretches the whole cadence. **A dog's `interval_s` bounds how
  often it is DUE, not how soon it is SEEN — any watchdog test that budgets
  against `interval_s` is writing this flake again.**
- **The `_switches >= 4` edge dissolves.** `account_switches` lives in
  process-local turn state, not the org doc, so a restart zeroes it and the
  gate silently opens. It is not needed: a switch refused by the counter had
  capacity, so it records `pool: "open"` and the capacity path skips it.
- **`accounts.resolve` is two file reads** (roster + the CLI config via
  `live_identity`), no network, and `accounts.py` never takes `store.DOC_LOCK`
  — so no lock inversion under the resume loop. Hoist per tier anyway, and
  **pass `now`**, or the injected-clock tests stop being deterministic.

### D-172 · anchor a counter-scaled decoration by the edge that must stay clear, never by the card

The strips immediately left and right of an agent card hold furniture that
scales two different ways, and mixing the two without saying so is what this
entry exists to stop repeating.

**WORLD-scaled** furniture shrinks with the canvas: `.cbar` (the credit bar,
`left: -22px; width: 14px`) and `.doc-chips` (`left: calc(100% + 3px)`). Their
screen width is `14·z` and `21·z`, collapsing toward nothing as you zoom out.

**SCREEN-constant** furniture carries the counter-scale `--invzf` and holds its
size at every zoom: the hire columns `.hsof.side-l/.side-r`. That is deliberate
and load-bearing — they ARE the hire gesture (user report 2026-08-04: the older
clamped `--invz` let them shrink away while zoomed out, which is why they use
the UNCLAMPED `--invzf`), so shrinking them at distance is not an available
fix, and this entry is not a licence to reintroduce a clamp.

**THE RULE.** A screen-constant decoration anchored near the card edge grows
over a shrinking neighbour until it buries it. So anchor it by the edge that
must stay clear — pin its near edge ON the neighbour's far edge, and let the
counter-scale grow AWAY. `.hsof.side-l` uses `right: calc(100% +
var(--hsof-l-clear))`; `.hsof.side-r` uses `left: calc(100% +
var(--hsof-r-clear))`. Clearance then holds at every zoom **by construction** —
nothing depends on what `--invzf` happens to be, so it cannot be tuned into or
out of correctness. Express desk-zoom overrides through the same variables, not
as `left`/`right` on the column, or you stretch a column positioned by one edge
only and silently undo the clearance.

**⚠ THE CLEARANCE IS THE NEIGHBOUR'S EXTENT, SO IT IS ONLY THERE WHEN THE
NEIGHBOUR IS** (amended 2026-08-28 after the second report; the first cut of
this entry got it wrong and said 22px and 24px flat). `.cbar` is drawn for every
live card, so the left figure of 22px — the bar's own 8px offset plus its 14px
width — is unconditional and correct. `.doc-chips` is drawn only for a node that
has presented documents, so the right figure is **24px beside doc chips and 8px
beside a bare card**, keyed on `.sq:has(> .doc-chips)`. Keyed on the chips
themselves rather than on a React class computed from the same condition: a
class can drift out of step with what actually renders, `:has` cannot. A card
with no documents therefore shows the same 8px of open canvas on both sides,
which is the property to preserve — not any particular number.

**⚠ A WORLD-PX CLEARANCE MULTIPLIES BY THE ZOOM, AND THAT IS UNUSABLE UP
CLOSE.** Stating the clearance in world px is what makes it hold at a distance;
it also makes 22px into 264 screen px at z=12, which threw the hire columns most
of a screen-width off a desk-filling card. User ruling 2026-08-28: "when in desk
view move the coworker hire buttons back to their old positions right next to
the card, so they're still on screen there." `.sq.desk` therefore sets both
clearances to `2px` — exactly where the columns sat before this entry existed.
The cost, measured rather than assumed: the left column crosses the OUTER third
of the credit bar from z=2.1 to z≈3.3 (9.4px of a 29.4px bar at `Z_DESK`, none
of it at z ≥ 4, where the bar has grown out from under a screen-constant
column). The bar's centre is never covered and the click still lands on it at
every desk zoom. This exception cannot leak back into the distance complaint:
`focusId` is `null` below `Z_DESK`, so the desk rules only ever apply from 2.1
up. **The general rule is unchanged — the desk is a bounded exception with a
measured price, not a repeal.**

Reported as: "the leftward hire coworker badges overlap the budget bar, making
it untouchable unless zoomed in far enough to reduce their relative size"
(2026-08-28). Measured pre-fix: at z ≤ 1.0 a cursor placed on the bar had its
click land on `.hsof.side-l` — the bar was not merely covered but
**unreachable**; overlap peaked at 13px at z=1.0 and the hit returned only at
z ≥ 2.1. The right column overlapped `.doc-chips` by the same mechanism at
every zoom below 12 (18px at z=1.0), found by looking for the mirror rather
than by a second report.

**Reordering is not the fix.** Flipping `z-index` or `pointer-events` makes the
bar clickable while the badge still sits on top of it — the click goes through
to a control the user cannot see. Remove the overlap.

**⚠ THE BRIDGES ARE NOT DEAD MARKUP.** `.hsof-bridge.bridge-l/.bridge-r` are
transparent, paint nothing, and look deletable. They exist because this fix
creates a second defect if you stop at the anchor: moving the columns off the
card opens a strip the cursor must cross, and the chips are `pointer-events:
none` until `.sq` is hovered — so they blink out mid-reach and the HIRE gesture
becomes the new unreachable thing. The bridges are hit area only, children of
the card, sitting UNDER the bar and doc chips (`z-index: 1` vs `2`) so they
extend the hover region without taking anyone's clicks. Delete them and the bar
stays clickable while the badges stop being reachable. Measured worst dead run
with them: 0.0px at every zoom, on both sides, with and without doc chips.

**Each bridge is sized from the same variable as the column it serves**
(`width: var(--hsof-l-clear)` / `var(--hsof-r-clear)`). The right-hand gap is no
longer one number, so a bridge written as its own constant can only ever be
right for one of the two states; sharing the variable makes "the bridge covers
exactly the gap" true by construction rather than by two edits staying in step.
Desk fill is the one place this does not matter — `.sq.desk.edge-l/-r` drop the
`:hover` from the gate entirely (user spec: the desk KEEPS its hire chips), so
there is no hover to interrupt. Everywhere else the gate is `.sq…:hover` and the
bridge is the only reason the columns are reachable at all.

**Checked and left:** `.cbar-tip` also counter-scales into the left strip, but
it is `pointer-events: none`, so it can never take a click. Recorded so the
next author knows it was looked at rather than missed.

**Verify in a browser, not in jsdom.** This is geometry; jsdom reports every
rect as 0×0, so an overlap assertion there passes for the same reason it would
on a blank page, and multiplying constants by an assumed scale is arithmetic
wearing a measurement's clothes. `frontend/tests/chipbar_probe.py` renders the
real markup against the real stylesheet in headless Edge, places a REAL cursor
on the bar and asks `elementFromPoint` what receives the click, walks the
transit out to BOTH columns, and measures the open canvas on each side. It
sweeps three cards — with documents, without, and at desk fill — because the
right-hand strip has two states and the numbers differ in all three.

**Two controls, because there are two claims.** `--expect-fail` restores the
pre-fix rules and must go red (29 findings): that is the reachability claim.
`--expect-fail-const` restores the unconditional 24px right clearance and must
also go red (11 findings, all on the symmetry check): that is the second
report's claim. A green run means nothing without both. The measurement to
quote: with nothing on the right to clear, the open canvas beside the card reads
7.00px on the left and 7.00px on the right at z=1, difference 0.00px, at every
zoom in the sweep.

**Two ways that probe lied, both reporting a working fix as broken.** Recorded
because this repo has twice been bitten by instruments that lied in the
reassuring direction; these lied in the alarming direction, which is cheaper
but the same class — and an alarm that cries wolf is discounted just as fast as
one that stays silent.
- **Hit-testing a bar taller than the window.** At desk zoom the bar's
  geometric centre is off-viewport and `elementFromPoint` returns `null`, which
  read as "unreachable". Aim at the centre of the bar's VISIBLE part, and say
  plainly when none of it is visible rather than scoring it.
- **Walking the transit inward from open canvas.** The chips do not exist until
  the card is hovered, so approaching from outside they are not hit-testable at
  all and never can be. Direction is load-bearing: walk OUTWARD from inside the
  card, the only path a user can actually take.

**Two things the second round changed in the instrument itself.** The viewport
went from 1600×900 to 2400×1600: at desk fill a 124px card is 1488px square, so
the old window put the bar, the column centres and the entire right-hand strip
off-screen and the probe scored the whole of z=12 as "not measured" — at exactly
the zoom where a 2px world error costs 24 screen px. And the fixture now runs a
JS port of `NodeSquare`'s `trackEdge` instead of presetting `edge-l`, because
the transit's whole question is whether a column STAYS live as the cursor leaves
the card, and a preset class answers that by assumption. Both the port and the
`.doc-chips` render condition are pinned to `cards.tsx` by the fixture-freshness
guard, so the probe cannot quietly drift into measuring a card that never ships.

### D-175 · a drive nudge is either a POINTER TO MAIL or a SELF-CONTAINED PROMPT

Every nudge this system sends is one of two things, and until now nothing in
the code said which. **A POINTER carries no information of its own — "there is
mail above, go and read it" — and is meaningless if the mailbox is empty. A
SELF-CONTAINED PROMPT reads correctly on its own** — a replayed message after a
restart, an unstick text, a watchdog payload — **and must still be delivered
against an empty box.** A pointer that drains nothing must not wake anyone.
`supervisor.send_message`'s `mail_ping=True` is the declaration; the default is
self-contained, because silently swallowing a prompt that carried its own
content is the worse failure of the two.

**The defect this came from: a cardinality mismatch, not a filter.** The banner
is not a rendering of mail. It IS the nudge — the literal prompt string handed
to the agent. The `[MAIL — N message(s)]` block is prepended separately by
`_envelope`, which empties the mailbox with `take_mail` **wholesale**. So
banners are counted per SEND and mail is counted per BOX, and the two counts
are of different things. Two messages arriving while a node is busy queue two
banners against one mailbox: the first delivery renders
`[MAIL — 2 message(s)]` and empties it, and the second arrives pointing at
nothing — a full agent turn whose entire user-side content is

    (orgtree) You have new mail above — handle it as appropriate, and use
    orgtree_status when your own task state changes.

**Say the shape out loud, because four plausible wrong ones were proposed
first.** The brief that opened this investigation offered a drained-by-steer
race, a message whose rendering is suppressed, a wake consumed by a concurrent
turn, and a D-165/166/167 regression. Every one was wrong, and each is the kind
of story that can be argued into looking right. There is ONE cause and it is
arithmetic: N sends, one wholesale-draining box.

**The measurement.** 364 transcripts on the reporting machine: **657 banners
carrying a real `[MAIL]` block, 8 carrying nothing at all.** The most recent
was 100 seconds before the report, in the coordinator's own session, and both
recent cases show the same signature — a turn that answers two mails ("Two
things landed", "Both mails handled") followed 0.1 s later by a bare banner
through the result-boundary feed. Recorded because *rare and real* is exactly
the combination that gets dismissed as a glitch: 8-in-665 is easy to explain
away as a hiccup, and it was costing a full agent turn every time. What it
damaged was not the cycles but the credibility of the banner — an agent that
learns the mail system lies to it starts checking, and then the wake is wasted
even when it is honest.

**The fix drops, it does not hide.** A pointer that reaches delivery and drains
nothing is discarded at all three delivery sites — before the CLI is launched
at a turn start, before the write at a result boundary, before the injection at
a steer. The wake does not happen at all rather than happening quietly; a
downstream filter that suppressed the banner would have left the pointless turn
intact, which is the whole cost.

**COALESCING WAS BUILT AND BACKED OUT. Do not re-attempt it without reading
this.** Collapsing a second pointer into the one already queued is the
tidier-sounding "make the two conditions agree at their source", it is the
first thing anyone reading the fix will think of, and it is wrong here on two
counts. It is **redundant**: once a pointer that drains nothing is dropped, no
phantom reaches an agent whether or not a second one was ever queued. And it is
**destructive to coverage**: ordinary mail is how `deepqueue` builds a long
queue, and that suite exists to prove the iterative drain does not wedge with a
`RecursionError`. Coalescing collapsed its 215 messages into 1 carrier, so the
suite could no longer reach the state it guards — it went green by no longer
testing anything. **A test that can no longer reach the condition it guards is
worse than no test**, because its green is read as evidence. This is the same
family as D-158's "an absent check and a check that cannot fail are the same
thing", arriving by a route that looks like an optimisation: nobody deletes a
safety test, but "my refactor made the suite faster" deletes what it could
reach. The reasoning is also parked next to `_mark_ping` in `supervisor.py`,
because the person contemplating this may be reading the code rather than the
register — but the register is where it is normative.

**What a new nudge site must do.** Decide which kind you are adding. If the
text would be nonsense with an empty mailbox, pass `mail_ping=True`; there are
thirteen such sites today, nine in `api.py` and four in `supervisor.py`, all of
them a drive that follows a `post_mail`. If it carries its own content, leave
the default and it will be delivered whatever the mailbox holds. Getting this
backwards in the safe direction costs a wasted wake; getting it backwards in
the unsafe direction silently eats a message, which is why the default is the
one that always delivers.

**AMENDED 2026-08-28: a SECOND origin, and the drop had to move.** Hours after
the above shipped, an outside org (`@org:unity`, reporting through our org
inbox — a report to verify, not authority) described the same symptom from a
different cause: their user queued a mid-turn steer, **cancelled it before
delivery**, and the agent's next turn arrived carrying the user-message trailer
and no `[MAIL]` block. The payload was not consumed by an earlier delivery; it
was **removed** by `node_mail_retract`, which deletes the entry and — correctly
— never touches the node's queue, because the queue is not its business.

**The first fix did NOT cover it, and reasoning said it would.** The gate in
`_run_turn` asks "is there anything to point at" BEFORE the turn blocks on a
turn slot, and the drain happens AFTER that block — so the entire slot wait is
a window in which the box can empty under an in-flight pointer. A retract is
the reported way in; any drain in that window does it. Demonstrated rather than
argued: `test_turn_lifecycle`'s `retract` section runs the scenario twice, once
against a monkeypatched pre-fix build, and the pre-fix arm is a required CANARY
— if it does not produce a bare banner the section refuses to report the fixed
arm at all, because a clean sheet and a blind instrument are the same picture.
**The check must be true at the moment it matters, not at the moment it is
cheapest to ask.** The earlier gate is kept regardless: it saves the whole slot
wait when the box is already empty.

**And the drop needs `toks`, at every site.** A carrier that arrives holding
journal tokens ALREADY HOLDS its drained batch — the mail is in its text, so
re-enveloping it finds nothing new and "drained nothing" is true of a message
that has already left the mailbox. The first cut of the boundary drop tested
"is a pointer" and "drained nothing" and threw such carriers away. That is
**silent delivery loss, which is strictly worse than the phantom this entry is
about**: a wasted wake is visible and annoying, a swallowed message is neither.
It was caught by `dupresult`'s feeding boundary going dark — a suite testing
something else entirely, which is the argument for not deleting checks whose
subject you have finished with. Both drop sites now require that the carrier
owes no journal token.

**The general rule, since this class has now produced two origins and one
self-inflicted wound.** A pointer may be dropped only when all of *it is marked
a pointer*, *this delivery drained nothing*, and *it carries no already-drained
batch*. Ask it at the delivery, not before. A third origin is likely — anything
that removes mail from a box without consulting the carriers pointing at it
qualifies — and the contract above is what makes such an origin harmless rather
than a new bug: the drop is keyed on the state at delivery, so it does not need
to know how the box came to be empty.
