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
when a manager's visibility is lowered. `permission_mode` remains org-wide
by construction ("parent clamp" is inapplicable to it; the set_scope
docstring overpromised and is fix-listed).
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

### D-070 · minimal surface; docs/ui-guide.md is the whole manual
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

- **The compact-screen desk: sheet or card?** D-073 rules the desk fades in
  OVER the card at the same size — the chat living inside the tree is the
  card metaphor's point. The mobile spec (docs/mobile-spec.md, held)
  concludes by arithmetic that no zoom makes a world-scaled desk both
  legible and framed on a phone (desk text ≈4.4 px at 375 px width), and
  proposes a full-screen 1:1 sheet on compact screens ONLY, keeping D-073
  on desktop. That contradicts a written ruling, so it needs the user's
  decision before the mobile wave builds — recorded here so an implementer
  does not read the sheet as a bug and revert it.

*(The seven items ruled 2026-08-01 live in their domain entries: D-021
Bounds, D-014 Load-bearing, D-063, D-023, D-071, D-069; the mobile wave's
hold/release state is project-state, tracked outside the register.)*

---

## Retired

*(nothing yet — retired entries keep their `Was.` and move here whole)*
