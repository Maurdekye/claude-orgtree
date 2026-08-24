# Multi-account — handover (branch `feat/multi-account-registry`)

Written 2026-08-24 by creds-probe, on being stopped mid-feature. Assume you
cannot ask me anything. HEAD at handover: **`91ffeb9`**, 11 commits off `main`
at `9164217`, working tree **clean**, **nothing merged, nothing deployed**.

---

## ⚠ THE FOUR THINGS A SUCCESSOR MUST NOT DO

**1. Do NOT read this branch as working failover. Phase 1 green is not
failover.** The registry, the panel, the pin and the waterfall *order* all
work. Nothing selects an account for a turn. `readout()` reports
`selection_active: false` and that is the machine-readable form of the
promise. A panel showing two healthy accounts with a primary and a pin reads
exactly like a working waterfall and is not one. See **D-144**.

**2. Do NOT add a `_looks_like_auth_failure(err_blob)` before fixing the
harvest. It would go green and do nothing.** `err_blob` is built from **stderr
alone** when the CLI exits nonzero, and on an auth failure the CLI writes
**0 bytes to stderr** — the real reason (`Failed to authenticate. API Error:
401 …`) goes into its `result` event, which that path never reads. The
supervisor already receives that exact string elsewhere and discards it,
because the only question it asks of it is `_looks_like_usage_limit`. So the
classifier would never see auth text. **Harvest first, classifier second.**
See **OPEN-01** and D-144.

**3. Do NOT assume `_died_in_flight` is comfortably ruled out. It is ONE
condition away from firing.** Measured in orgtree's real spawn shape:
`exit_only` is **True** (stderr empty, `errors` None) and `started` is **True**
(the flag fires on the CLI's own `<synthetic>` error message, NOT on the model
speaking — its docstring says otherwise and is wrong for this case). Only
`boundary` — the CLI always emitting a top-level `result` event — prevents
orgtree from retry-looping against a dead token, four times on escalating
backoff, auto-resuming regardless of the org's toggle, and telling the user
"the CLI died mid-response". If a future CLI stops emitting that event, this
breaks with no code change on our side.

**4. Do NOT put a FABLE-tier agent on anything that touches accounts, and do
NOT mail one about this work.** Three fable seats were killed by the AUP
safeguard on exactly this subject; the last died on a *sanitized* report whose
only token was fabricated. **The trigger is the SUBJECT, not the secrets** —
credential material, token shapes, probe findings, identity discrimination,
auth flows, classifier errors. Mail is what kills. The standing bar for fable
work here is "briefable without naming the domain at all". Note the cost
inversion: a fable seat is **10** credits, opus **5** — fable is the expensive
tier, not the cheap one.

---

## What is done and verified

- `backend/orgtree/accounts.py` — registry, passive adoption, per-org pin,
  panel readout. **Selects nothing, switches no lane.**
- `backend/orgtree/api.py` — five routes under `/api/accounts`; whole prefix
  frozen against kiosk/public visitors **explicitly**, not via the matrix's
  trailing 404.
- `frontend/src/canvas/accounts.tsx` — the panel, with the D-144 banner.
- **62 checks** (`python backend/tests/test_accounts.py` — no pytest here,
  it's a standalone script) and **32 mutants** (`_mutate_accounts.py`):
  1 no-op survived, 31 died to their named checks.

**Never verified:** the panel has never been rendered in a browser. It
typechecks, builds, and is wired to a header button, but no check asserts the
banner is visible to a human. The test is someone opening it after deploy.

## Merge footprint, at line precision

New files (no overlap): `backend/orgtree/accounts.py`,
`backend/tests/test_accounts.py`, `backend/tests/_mutate_accounts.py`,
`frontend/src/canvas/accounts.tsx`, this file.

Shared files — what a peer hits blind (hunk starts, `git diff main..HEAD`):
- `frontend/src/App.tsx` — **4 hunks**: `@@ -22,6 +22,7` (import),
  `@@ -102,6 +103,7` (useState), `@@ -300,7 +302,13` (header button),
  `@@ -641,6 +649,10` (render block).
- `backend/orgtree/api.py` — **3 hunks**: `@@ -39,7 +39,7` (import),
  `@@ -240,6 +240,14` (`_public_denied` freeze),
  `@@ -1825,6 +1833,95` (the `/api/accounts` block).
- `frontend/src/api.ts` — 2 hunks: `@@ -10,6 +10,7`, `@@ -258,6 +259,35`.
- `frontend/src/types.ts` — 1 hunk: `@@ -792,6 +792,42`.
- `DECISIONS.md` — 2 hunks: `@@ -2820,8 +2820,44` (OPEN-01),
  `@@ -2951,6 +2987,195` (D-144). Append/append with anything else new;
  resolution is keep-both, main's first.

The panel is its own file *deliberately*: two peer worktrees
(`fix/switchboard-mutate-scroll`, `feat/extern-handle-attach`) are editing
`modals.tsx`. `App.tsx` was unavoidable.

## Left open

1. **`registry-review`'s final verdict never arrived** — it was mid-mutation
   against my fixes when work stopped. Its earlier findings are all fixed and
   committed. It had one thing outstanding worth finishing: reproducing a 401
   that arrives **after genuine model output**. Both existing measurements are
   turn-start-shaped; `boundary` was True in every failure shape produced, but
   the post-output case has **not** been shown.
2. **The harvest fix (OPEN-01) is unbuilt**, and splits into (a) recording
   only — contained, changes no freeze/retry behaviour, has a check that fails
   today; and (b) letting that text reach the classifiers — changes what every
   agent's errors classify as, on every org. **(b) is Phase 2.** No ruling was
   given on (a) before work stopped.
3. **Selection/failover (Phase 2) is entirely unbuilt.**

## Environment traps that cost real time

- A fresh worktree has **no `node_modules`**. Junction it to the main repo's
  with PowerShell `New-Item -ItemType Junction`; `cmd /c mklink /J` via Bash
  **silently no-ops**. It is gitignored.
- **PYTHONPATH carries the main tree's backend.** `sys.path.insert(0, …)`
  currently wins, and §0 now asserts `accounts.__file__` resolves inside this
  worktree — winning by import order is a coincidence, not a property.
- **Two agents in one worktree is a footgun.** `_mutate_accounts.py`'s
  `restore()` is `git checkout --`; its dirty-check now covers `backend/tests`
  as well as `backend/orgtree`. While anyone else is mutating, stage explicit
  paths — never `git add -A`.
- **Re-run the mutation round after touching code under test.** Eight mutants
  went vacuous across two rounds because the code they targeted had been
  rewritten. The harness reporting "vacuous" instead of quietly passing is the
  only reason that was caught.
