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

### D-103 · robustness priorities: @net is the robust path; pins, not fixes
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

### D-102 · self-update: unrestricted for user-authority agents; the remote is the trust root; Linux is first-class
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

### D-101 · mailserver ports stay exactly as they are (7370 open, no migration)
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

### D-100 · the machine's global skills are granted to unsandboxed agents
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

### D-101 · permission mode is editable after creation, at both levels
Ruling (user report, 2026-08-07): the mode was write-once at org creation and
had no control anywhere — not on the org, not on a node. Both are now
editable: the org field is the BORN-WITH default `_new_node` copies into every
hire (org ⚙, admin-only), and each node carries its own (agent ⚙). Changing
the org default is never retroactive — live agents keep the mode they were
hired with and are raised one at a time, deliberately.
Why: D-100 made the mode the difference between an agent that can maintain
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
~/.claude/projects, which no agent can be granted (D-100's segment gate) —
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
Why: D-100 made the mode the difference between an agent that can do a job
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

*(empty as of 2026-08-14 — the compact-desk sheet question resolved into
D-123)*

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
  `update.ps1` has six exit paths that never reach the restart, two of which
  (dirty tree, `git pull` failure) fire routinely — so a timeout-cleared flag
  would wedge every org on the machine on the COMMON path. Clearing it by
  watching the spawned child's pid is the promising design, but it changes
  `_detached_spawn`'s contract and lands in turn admission, which is being
  rewritten concurrently.
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

---

## Retired

*(nothing yet — retired entries keep their `Was.` and move here whole)*
