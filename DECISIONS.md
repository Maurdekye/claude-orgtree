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

---

## Product thesis

### D-000 · one thing, very very well
Ruling (user, 2026-07-31): orgtree is a simple idea — a persistent visual
organization of Claude agents — refined meticulously to a mirror sheen, not a
feature jamboree. Bias effort toward polishing existing surfaces over adding
subsystems; measure every addition against "does this deepen the one idea, or
dilute it?"

Companion motto (user, 2026-07-31): permit as much as possible; close wide
gaps with minimal-friction shortcuts; the tree is a **sandbox of
capabilities, not a cage**. Hard refusals are reserved for real resource
limits the user set (credits, kiosk caps, the disk cap), true impossibilities
(unrecoverable sessions), and the safety of others' data. When a legal
sequence of actions reaches an end state a wall blocks, offer to perform the
sequence as one action — see D-003 for the one precondition that governs
automating it.

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
(0.0.0.0) serves only the agent gateway, gated by the per-org secret. This
supersedes PLAN.md's "single desktop, single user … there is no adversary"
premise, which predates the public stack.

---

## Agent runtime

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
then showed `disableAllHooks` cannot coexist with the steer hook, but
explicit per-event entries CAN suppress inherited hooks while keeping ours —
so the apparent isolation-vs-steering tradeoff was a false dilemma and both
invariants are enforced. Mechanism and its enumerated-not-categorical caveat:
docs/ARCHITECTURE.md §hooks.

Was. README stated the mechanism (`disableAllHooks`) rather than the
contract, and the mechanism it named was not what the live branches sent.

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

### D-005 · the record's shape: register + traps, superseded-in-place
Ruling (user, 2026-08-01): the durable record is two files — this register
(normative decisions, `Was.` slots, supersede in place, `## Retired` tail)
and docs/ARCHITECTURE.md (operational traps). PLAN.md stays untouched as the
historical plan. ADR-per-file is rejected: its supersede-by-appending
convention is precisely the failure mode this structure exists to fix.
Corollary (the repo razor): if a fact would still be true for a stranger
cloning this repo, it belongs in the repo — not in agent memory.

---

## Retired

*(nothing yet — retired entries keep their `Was.` and move here whole)*
