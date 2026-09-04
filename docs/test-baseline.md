# Known-failing suites on main — the baseline method

Started by `mail-delivery`; `perf-latency` added the second and third traps.
Anyone may edit it — it is only worth what its last measurement is worth.

> This lived in one agent's scratch folder until 2026-09-03, which meant the
> document every landing was being judged against could vanish with the seat
> that held it. It is in the repo now: **edit it here**, and update the
> measured tip when you re-measure.

**Backend list last measured 2026-09-03 against `f2d42f5`**, re-confirmed
identical at `6f354b4` and `9e15f3c` either side of it, and **independently
re-confirmed by `sqlite-review` at `f071dc7`** in a fresh worktree with no
`node_modules` — its raw run showed 9, which resolved on solo re-runs to
exactly the names in the table plus `warmpool`. Two agents, different
tips, different branches, same names.

⚠ THAT PARAGRAPH QUOTES A COUNT AND THE TABLE HAS GROWN SINCE. Counts in
prose are how this file came to tell two stories at once on 2026-09-04, when
two agents quoted "eight" from it in the same hour reading different parts.
Trust the table, and the per-row `last confirmed` tips in it.

That is the strongest this list has been. It is still one tip behind main and
will be again by the time you read it — which is the normal condition of this
document rather than a defect in it. Re-measure at your own tip; do not quote
these names at anyone as current.

> ⚠ **RE-MEASURE THIS LIST WHENEVER MAIN MOVES. Do not trust the names below
> because they are written down.** Three of them (`harvest`, `headless`,
> `turn-lifecycle`) are not really tests — they are drift detectors that grep
> `supervisor.py` **by line number or by literal source text**. Anything that
> shifts that file changes what they say, in either direction: a landing can
> quietly repair one, or break a fourth that was fine yesterday. A list
> measured two commits ago is evidence about two commits ago. Re-run the
> baseline at YOUR tip. It costs six minutes and it is the whole point.

`main` is NOT green and has not been for a while. "Expect green" is the wrong
bar; the right bar is **parity against a measured baseline** — you broke
nothing if your tree fails the same suites, BY NAME, as a clean tree at the
same commit.

**Before you compare your count to another agent's, read the `crash-reports`
row below.** Two correct people measured 8 and 7 on the same commit and
neither was wrong; the difference was whether their tree had
`frontend/node_modules`. Comparing raw *counts* between agents is how that
turns into an afternoon of hunting a defect that does not exist. Compare
NAMES, at the same commit, or don't compare.

## The method

Make a second worktree pinned at the commit you branched from, run the suite
in BOTH, and diff the failure lists by name:

    git -C <your-wt> worktree add ../baseline <main-sha> --detach
    cd baseline && ORGTREE_DATA=<scratch-path> python tools/run_tests.py 2>&1 | grep -E "^✗|RUN COMPLETE"
    cd ../wt   && ORGTREE_DATA=<scratch-path> python tools/run_tests.py 2>&1 | grep -E "^✗|RUN COMPLETE"

Re-point the baseline when main moves: `git -C baseline checkout --detach <new-sha>`.

⚠ **Supply an explicit `ORGTREE_DATA` in your launcher environment** (e.g.
`$env:ORGTREE_DATA = "<scratch-path>"` or `ORGTREE_DATA=<scratch-path> python tools/run_tests.py`).
Because `main` defaults to SQLite, `tools/run_tests.py` explicitly refuses to
run (exit 2) if invoked without `ORGTREE_DATA` to protect the operator's live
`~/orgtree` root from accidental migration by any suite that forgets to mint its
own root. Once past that launcher guard, the runner mints its own isolated rig
per suite (`$TEMP/orgtree-tests-*`) and redirects `ORGTREE_DATA`, `HOME` and the
port itself. The live backend can stay up; it does not collide.

  * **~6 minutes** wall (346-447s observed). Budget a 600s tool timeout.
  * The runner exits **rc=1** whenever anything fails, which on main is always.
    Do not treat rc as the signal — read the ✗ lines.
  * Run the two suites SEQUENTIALLY. Running them at once makes the timing
    suites flaky (see `warmpool` below).
  * `--list` prints the runner's own header (repo, python, tier, log dir).
  * Per-suite logs land in the `orgtree-tests-*` dir named in the output; the
    ✗ line gives you the exact path.

## Known-failing suites on main

**THE TABLE IS THE SOURCE, and it carries its own tips.** This heading used to
read "The known-failing 8 (at `f2d42f5`)" while a ninth sat in an addendum
below it, and on 2026-09-04 two agents quoted "eight" from this file in the
same hour because they stopped reading at different points. A count in a
heading goes stale silently every time the table grows — which is the exact
defect this document exists to prevent, one level up. So there is no number
here: if you need one, count the rows.

⚠ **A SUITE'S RESULT IS NOT A PROPERTY OF THE SUITE. It is a property of the
suite AND HOW IT WAS RUN, and a row that omits the second half will keep
producing the same argument.** `compaction` passes under `run_tests.py` and
fails on a direct `python tests/test_compaction.py` — the runner gives it
`--hermetic`. A row that had said only "compaction fails" was true, useless,
and cost an afternoon. **Every row names its mode.** "runner" means
`tools/run_tests.py` in the fast tier from a git worktree; "direct" means
executing the file yourself.

`last confirmed` is the tip a row was actually observed at, per row, because
they were not all measured at once. Re-measure at YOUR tip before quoting any
of it — see "The method".

`tail` is what the runner now tells you and could not before: whether the
suite RAN ITS REMAINING CHECKS. **A red row is not a measured suite.** Most
suites here use a deliberate fail-fast `check()` that raises rather than
recording, so a red one dies at its first failure and everything after it is
unmeasured — `⚐ ABORTED — tail unmeasured` in the console names exactly that
case (`stopped_early`, `1798963`).

| suite | mode | last confirmed | tail | why it fails, and why it is not yours |
|---|---|---|---|---|
| `account-pool-state` | runner | `1798963` | complete, 39 | hits the REAL API and gets a live `429 rate_limit_error`. Fails or passes with the account's usage, not with the code. |
| `codex-prewarm` | runner | `c896a00` | complete, 6 | `initialize stays once per process: got 2, wanted 1`. Verified on clean `c896a00`; it had never been in this list. |
| `compaction` | **runner: PASSES** (487 checks) · **direct: FAILS** | `1798963` | complete | `urllib.error.HTTPError: HTTP 422` out of its own rig on a direct run only. The runner passes it `--hermetic`. This row exists to hold the mode rule above, not to report a red. |
| `harvest` | runner | `1798963` | complete, 38 | structural fixture: "BEHAVIOUR CHANGED — ORDER: widened text assembled at line X, classifier still runs at line Y". Greps `supervisor.py` BY LINE NUMBER. **The integers move with any insertion; the RELATION is the assertion.** A changed pair is not evidence you caused it. |
| `headless` | runner | `1798963` | complete, 35 | structural fixture: `body.index('org.d.get("api_key")')` → `ValueError: substring not found`. Greps source text. |
| `mcp-lifecycle-finalize` | runner | `c896a00` | ⚑ DRIFT | `AssertionError: 0 is not None : vacuous: child died before EOF` across three lifecycle tests. Verified on clean `c896a00`; it had never been in this list either. |
| `run-completion` | runner | `1798963` | **⚐ ABORTED — stopped at 13** | the runner testing itself under a kill. It prints an intermediate "13 checks passed, 4 failed" and then DIES on `a killed run left a COMPLETE marker — the marker lies`, so everything after that point is unmeasured. Identical on `c896a00`. |
| `turn-lifecycle` | runner | `1798963` | complete, 96 | structural fixture greps for `st["queue"][0:0] = leftover`, rewritten to `st["queue"].extend(leftover)`. Zero matches → the fixture trips before it asserts anything. ⚠ On a DIRECT run at `02615b9` it produced no output and did not finish within 200 s — a hang, not the fixture trip. Not chased; its grep is for a LITERAL string, so a `supervisor.py` insertion cannot move it either way. |

**Removed 2026-09-04 — these now pass, and the reason each one was red is
worth more than the row was:**

| suite | was | now | what it turned out to be |
|---|---|---|---|
| `crash-reports` | died at check **1 of 8** since 2026-08-30 | 8/8 in any worktree | its skip guard checked `NODE_BIN` and the resolver script — a binary on PATH and a file in this repo, both always present — and **not** `esbuild`, the one thing a worktree lacks. Present, plausible, inert. |
| `extern-handle-attach` | died at check **2 of 19** since `e9ef008` | 19/19 | the CHECK was stale, not the code: it asserted insertion order while `norm_extern_handles` has sorted since the warm-identity work. **17 checks — the whole external-handle privilege surface — were unmeasured for four days behind it.** |
| `gpt-reserve-detection` | died at check **11 of 22** since `c5049fa`, hours | 24/24 | its `NoAppServer` guard WORKED and caught a real change: `astra` became a conditional tier and `provider_hire_gate` now re-queries live inventory. A working instrument reporting a genuine change, not a stale fixture. |
| `external-mail` | died at check **8 of 241** for 25 days, then 79 | 240/240 | the §1 rig-hygiene assertion, added by *"No test rig may register against the operator's real mail hub"* — a correct, valuable guard that did its own job perfectly and cost 233 checks. **§4 kiosk sealing, §8 the extern HTTP surface and §10 authorization were all inside the dark part.** A guard that aborts a suite is not free, and nobody priced it. |

⚠ **FLAGGED, NOT CHASED (2026-09-04):** `c5049fa` made rollout tiers
conditional, and `provider_hire_gate` now performs a **live model-inventory
refresh that spawns a codex app-server on the HIRE path**. That is a latency
and robustness property on a user-facing action. It needs an owner; it is
recorded here so it is not rediscovered.

**Three of these (`harvest`, `headless`, `turn-lifecycle`) grep `supervisor.py`
by line number or literal text.** They are drift detectors, and anything that
shifts that file's lines can change what they say. If you touch
`supervisor.py`, re-measure the baseline at YOUR tip rather than trusting a
list someone measured two commits ago.

### How rows got here — the method that settles "was it already red"

`git worktree add --detach <scratch>/<name> <the-tip-before-your-branch>`, run
the suite there, compare. It costs one checkout and it is the difference
between "this was already red" and "I broke it and then read a document that
told me I had not". Every `last confirmed` above was set that way.

⚠ **Fixing a long-standing red can make a suite REDDER, and that is the fix
working.** A suite that aborts on its first assertion reports one failure and
conceals every later one. `external-mail`'s hygiene assertion had been
stopping the run in §1 for weeks; with §1 passing it reached point 7 and found
a real, unrelated failure that nobody had seen. Expect that, and do not read
it as a regression you caused.

### Detail behind the 2026-09-04 rows (`cache-invalidation-audit`)

Everything a table cell could not hold. The rows themselves are above; this is
the working, kept because the reasoning is what stops the next person
re-deriving it.

**`harvest` — read this if you have just edited `supervisor.py`.** It still
fails on the ORDER fixture, but the line numbers it prints move: `11615/11739`
at `f2d42f5`, `12008/12132` at `02615b9`. That is the fixture doing its job.
It asserts a RELATION between two positions, so a uniform shift (an insertion
of ~110 lines well above both) leaves the relation intact and the failure
identical. **A changed pair of numbers in that message is not evidence you
caused it.** Read the relation, not the integers.

**`external-mail` — the hygiene assertion was correct about the hazard and
wrong about the nine rigs it named, and the difference mattered before anyone
"fixed" it.** It flagged every rig minting its own `ORGTREE_DATA` without
writing `net_hub_address` — a proxy for "could register fixture orgs against
the operator's real hub". Measured, not reasoned:

* Registration happens **only** through `net.start_net_client()`, called from
  the backend's startup path. `store.create_org` in a throwaway data root
  writes a doc and contacts nothing.
* **None of the nine flagged rigs starts a backend.** Grepped each for
  `start_net_client` / `uvicorn` / `TestClient` / lifespan; the two apparent
  hits were the word "startup" in prose.
* **None of their fixture orgs was on the real hub.** Live roster fetched
  (`GET /ui/data` on `127.0.0.1:7370`, 135 rows) and matched against every
  name those rigs create. **Zero hits.** The 2026-08-10 pollution names the
  suite's own comment cites (`arch`, `capnode`, `lonedead`, `norescue`,
  `order2`) were gone too — that was this suite, and this suite was fixed.

So the assertion was re-pointed (`02d1c18`) at what is still dangerous after
`net._under_os_temp` floors temp roots: a rig whose data root is OUTSIDE the
OS temp directory with no hub address named. ⚠ It is only sufficient BECAUSE
that floor exists — if the floor is removed or narrowed, `mkdtemp` stops being
an adequate answer and the check silently becomes worthless while still
passing. ⚠ "Every rig already uses `mkdtemp`" is a fact about today, not the
check's reason for existing.

What the roster survey also turned up, deliberately kept separate: 132 of 135
rows had a base slug that was no longer a local org — **deleting an org did
not unregister it from the hub**. Fixed in `7d8c652`; the rule for who may
say an org is gone is `docs/mailserver-spec.md` §13.

### `external-mail`, 2026-09-04: the red that was hiding a second red

⚠ **HISTORY, NOT PRESENT STATE. It is 240/240 on both backends as of**
**`c0e39b2`.** Kept because the sequence is the lesson, and because a fixed
thing described as live is the same error as a live thing described as
fixed — both were nearly said out loud today. Name the tree.

The rig-hygiene assertion has been re-pointed. It used to demand
`net_hub_address` from every rig that mints its own `ORGTREE_DATA`; it now
demands `tempfile.mkdtemp` OR `net_hub_address`, because
`net._default_address()` floors any data root that resolves under the OS temp
directory to an unroutable address (`net._under_os_temp`, landed `07f66e4`).

⚠ **THE ASSERTION IS ONLY SUFFICIENT BECAUSE THAT FLOOR EXISTS.** If the floor
is ever removed or narrowed, `mkdtemp` stops being an adequate answer and the
check silently becomes worthless while still passing. Both names are in its
docstring so a grep from the `net.py` end lands there first.

⚠ **"Every rig already uses `mkdtemp`" is a fact about today, not the reason
the check exists.** `grep -L mkdtemp` over the rigs that mint a data root
returned nothing on 2026-09-04. The check is what keeps that true. Do not read
a green run as permission to skip it in a new rig.

**And the part worth knowing: `external-mail` still fails, on something else
entirely.** The hygiene assertion aborted the suite in §1, so nothing after it
had run in a long time. With §1 passing, the suite reaches point 7 and fails
there instead:

> `⚑→✓ kiosk enumeration: a kiosk whose doc will not load is listed WITHOUT
> kiosk_cfg` — `assert "kiosk_cfg" not in row and row["kiosk"] is True`.

The check rewrites a kiosk org's internal slug so it disagrees with its file
name, which used to make `store.load_org` raise and force `/api/orgs` onto its
bare-row fallback. It no longer raises, so the row arrives complete with
`kiosk_cfg`. **The reproduction's precondition has stopped holding** — this
looks like fallout from the SQLite store work, not a new defect in the
listing.

Verified as pre-existing and independent of the floor: the same patched suite
run in a detached worktree at `6b83496` (floor absent) fails identically at the
same line.

**Not a disclosure bug.** `/api/orgs` is the ADMIN listing, and the check's own
neighbours assert that the real `externtool` filter still hides the org from
outside view. What is broken is the fixture's ability to reproduce the
condition it was written for — the same drift-detector shape as `harvest` and
`turn-lifecycle`.


### ⚠ The result also depends on YOUR CHECKOUT: a junctioned node_modules

A frontend change is untestable without `frontend/node_modules`, and the team
pattern is to point at the shared checkout's copy rather than install a second
one.

⚠ **DO NOT USE `ln -s` FROM GIT BASH FOR THIS.** Without the native-symlink
privilege MSYS silently falls back to COPYING, so the command that looks like
a link duplicates the entire `node_modules` tree into your worktree. Measured
2026-09-04: it produced a real directory (`Attributes: Directory`, no
`ReparsePoint`) that had to be removed with `rmdir /s`. The earlier version of
this note recommended exactly that command.

Use a junction from PowerShell, which is a real reparse point:

    New-Item -ItemType Junction -Path frontend\node_modules `
      -Target E:\Libraries\Desktop\claude-orgtree\frontend\node_modules

Check what you got before trusting it — `(Get-Item <path> -Force).Attributes`
must say `ReparsePoint`, and `.Target` must name the shared copy.

That makes esbuild resolve, so **`crash-reports` PASSES for you**, like a run
inside `E:\`. Same suite, opposite result, and this one is
a property of your own worktree rather than of where you ran it. Record which
you did next to any count you quote. The runner also writes a temp directory
(`node_modules/.orgtree-tests/`) *through* the link, into E:'s tree — harmless,
but know that it happens before you assume your worktree is inert.

The frontend suite itself is a separate, much cheaper run and is **all but
green — 494/495 as of `3ba27db`**, so for a frontend-only change you can have
a near-real pass rather than parity. The ONE failure is pre-existing and not
yours:

> `chiptips.test.tsx` **§8 "the frontend's fallback tier table matches the
> backend's"** — verified by `msg-dupes` 2026-09-04 to fail identically on a
> pristine `3ba27db` worktree. Baseline it before reading anything into it.

⚠ The suite needs `frontend/node_modules`, which a fresh git worktree does not
have. Rather than a second `npm ci`, junction it at the shared checkout's copy
— `New-Item -ItemType Junction` in PowerShell (`cmd /c mklink` does not work
through Git Bash). Remove the junction with `rmdir` BEFORE `git worktree
remove`, or the removal fails with `Invalid argument`.

    cd frontend && node tests/run.mjs            # all of it, ~33 s
    cd frontend && node tests/run.mjs convo      # one file by substring
    cd frontend && npx tsc --noEmit              # app types (clean)
    cd frontend && npx tsc --noEmit -p tests/tsconfig.json   # ⚠ 4 PRE-EXISTING
                                                 # errors in cacheforecast/gallery

### FIXED 2026-09-04 — `test_message_visibility_live`, and how it went dark

**Status: green. 40/40, 3569 payload samples scored** (`b6e639a`). Previously
0 passed / 40 failed / **0 samples**, on pristine `f2d42f5` — reported here by
`msg-dupes` 2026-09-03, fixed by the same 2026-09-04. Left in this file because
the *diagnosis* is worth more than the status line: someone will hit a variant.

**How it went dark.** The rig runs its backend under a THROWAWAY `HOME` —
deliberately, so transcripts land there and nothing the user owns is touched.
`provider_hire_gate` refuses a Claude tier unless `accounts.live_identity()`
reports a signed-in account, and that reads `~/.claude.json`, which under that
home does not exist. So the gate answered, correctly, *"Claude is not signed
in"*, every `POST /ops` hire 422'd, no agent was ever created, no turn ever ran,
and a suite whose stated contract is *"a message ... NEVER appears twice"* was
asserting nothing at all. A real duplicate-render bug shipped underneath it
(`ebc8f9e`). **The gate was right and the rig was stale** — this is drift, not
a fault on either side, and it is the shape to look for when a rig that used to
work starts refusing: ask what the isolated environment stopped providing.

Fixed by satisfying the gate, never by relaxing it: the rig already substitutes
the CLI (`ORGTREE_CLAUDE_CLI=fakecli.js`), so it now also writes a fake
`oauthAccount` into its OWN throwaway home. `live_identity` is documented to
read CLI config metadata and never the credentials store, so no real secret is
read, copied or written, and `--real-cli` restores the real `HOME` and never
sees the file.

**Two guards added so it cannot go dark the same way:**

* **Zero samples is now its own named failure** (`_sampling_verdict`). An
  instrument that reads nothing is broken, not clean. The run now dies with
  `THE RIG SCORED NOTHING`, pointing at the FIRST traceback rather than the
  last. The general rule, which this repo keeps re-learning: **a guard that
  cannot report its own silence is not a guard.**
* **`fragile()` catches `AssertionError` only.** It used to catch every
  exception, so three of the 422s were filed as *known fragilities* reading
  `breaks as: HTTP Error 422` — an outage wearing the label of an expected CLI
  quirk, in the one category nobody re-reads. Those same three entries now read
  `after200: 3 GAP + 0 DUPLICATE samples out of 28`, which is what that bucket
  is for. **If a tolerated bucket's contents stop matching its name, the bucket
  is hiding an outage.**

⚠ **IT ONLY EVER EXERCISES CLAUDE.** It hires tier `haiku` against
`fakecli.js`. There is no Antigravity or Codex coverage in it, so it could not
have caught the Antigravity double-message bug (`3019505`) even fully working —
a suite that covers one of three lanes while claiming a provider-neutral
contract is a FALSE assurance, which is worse than a known gap. Scoping a
second lane is with the coordinator (ruled 2026-09-04: **after the cutover**);
until then, read its green with that limit in mind.

#### If you are the one adding a second lane, read this first

**Do not port the scenarios. Port the INVARIANT.** The estimate is ~3-4 days
and that is not the interesting part; this is:

The rig's central sweep is the D-55 **drain to echo** race, and that race is
about *the CLI's own transcript lagging its stdout*. For Antigravity there is
no CLI transcript to lag — the durable copy is orgtree's **own journal**,
written by the supervisor in `_antigravity_leg`. So most of the window sweep
has no meaning on that lane, and a day spent transposing it is a day lost
discovering that.

What DOES carry across is the contract: *a message is on screen continuously
and never appears twice.* Keep that, and choose the windows that lane actually
has. For Antigravity the two worth driving are the **journal-write to
`turn_done` gap** and the **draft handover** — the second being exactly the
hole that produced the double-message bug (`3019505`), so it is a window with a
proven defect in it rather than a hypothetical one.

Two practical notes, verified rather than assumed:

* **The hire path is the easy part.** `ORGTREE_ANTIGRAVITY=<fake exe>` plus
  leaving `FAKEANTIGRAVITY_SIGNED_OUT` unset satisfies the Antigravity hire
  gate entirely by env (`test_antigravity_dispatch.py:44,50-59`). It needs no
  equivalent of the `.claude.json` stanza the Claude lane required.
* **The dial is the hard part.** `fakecli.js` is config-file programmable and
  re-read on every launch — that is what makes the Claude sweep a *dial*.
  `fakeantigravity.py` is scenario-selected (`FAKEANTIGRAVITY_SCENARIO`) with
  essentially no timing surface: one `time.sleep(8.0)`. Giving it an equivalent
  config is the bulk of the work.

⚠ **IT EXHAUSTS EPHEMERAL PORTS, AND THAT LOOKS LIKE A SUBJECT FAILURE.**
Measured 2026-09-04: one `--quick` run drove machine-wide `TIME_WAIT` from
1 167 to **4 351** against a 16 384-port dynamic range (49152+, `netsh int ipv4
show dynamicport tcp`), 987 of them to the rig's own port. Every `api()` call
is a fresh `urlopen` with no keep-alive, and the 20 Hz poller makes thousands.
Started on an already-loaded machine it fails with:

    OSError: [WinError 10048] Only one usage of each socket address ... is
    normally permitted

surfacing as an ordinary red check (it hit `text · single-token` once here).
**That is the rig running out of sockets, not the subject misbehaving** —
`msg-dupes` first mis-attributed it to a backend bounce from a primed deploy
that had provably not fired. Re-run alone: 40/40. This is a specific instance of
the solo-re-run rule below, with a named mechanism and a way to check it
(`netstat -ano | grep -c TIME_WAIT`).

### Broken on main but NOT in the default run

Nothing currently. Suites here are ones the default tier skips, so you meet
them only by invoking them directly — and then they look catastrophic and look
like yours. See also the port-`7360` skip trap further down, which is how
`test_tree_render_cost` went unrun for its whole life: **a skipped suite still
leaves the run looking green.**

The general point: **`skipped` in the runner's summary is not `passed`.** If
you invoke a skipped suite by hand because it covers your change, baseline it
by hand too, at the same commit, before you read anything into the result.

### ⚠ ON A LOADED MACHINE, SOLO RE-RUNS ARE NOT A CHECK — THEY ARE THE METHOD

This began as a `warmpool` footnote. It is not one. `sqlite-review` measured
it properly on 2026-09-03 and the number is the headline of this document:

> **24 of one run's 33 failures passed when re-run alone.**

Named, so nobody re-derives the list: `sandbox`, `net-identity`,
`seat-topology`, `skills-grant`, `midturn-mail-ingress`, `steer-delivery`,
`steer-window-latency`, `inline-images`, `identity-set-order`,
`kiosk-ceiling-identity`, `working-checkup`, `working-cache-lifecycle`,
`prompt-cache-stability`, `prompt-view-race`, `provider-limit-freeze`,
`provider-switch-session`, `status-zero-vs-unknown`,
`report-guidance-identity`, `warm-native-identity`, `frozen-network-policy`,
`frozen-policy-enforcement`, `d211-cache-break-emission`, `mcp-tool-count`,
`warmpool`. Treat that as a sample of what CAN phantom-fail, not the closed
set — every one of these spawns a backend, a port or a temp tree, and this
machine routinely has several agents' rigs live at once.

**So: a raw full-run failure list is not a result.** It is a list of suites to
re-run individually. The count means nothing until you have done that, and a
number quoted before you have is not evidence of anything.

`warmpool` is merely the best-characterised one — `PermissionError
[WinError 32]` on a temp file, 26/26 and exit 0 immediately after failing in
a full run (`storage-design`), at roughly 1-in-4 on a quiet machine and
1-in-2 on a busy one. The rate tracks machine load, not code.

**`mcptool` phantom-fails too, and that one stings**, because it is the suite
that catches string-level regressions in tool results — it is what caught a
reworded delivery note pinned at `test_mcptool:1006`. Signature:
`AssertionError: the MCP server DIED on tools/call (stderr: b'')` — the same
no-output-then-nonzero shape as the empty logs. Measured by `sqlite-review`
on 2026-09-03: failed once in a full run and once solo, then passed **3/3**
on re-run at 177 checks each, plus once earlier in the session and again in
a different tree's full run. So a red `mcptool` is not automatically a real
regression — but re-run it until it is clean rather than shrugging, because
when it IS real it is telling you something a diff would not.

#### The signature: a 132-byte log

`sqlite-review` found the tell that makes this diagnosable rather than
superstitious. **A phantom failure's log contains the command line and
nothing else** — the child produced no output at all and exited non-zero.
Check the log SIZE of every failing suite before you believe a count.

Two things that sharpen it:

- It is **not** a timeout. The runner marks those `⏱ TIMEOUT` explicitly, so
  a silent 132-byte log is a different animal: the process died, it did not
  run long.
- **A fast run is a suspicious run.** The bad run finished in 222 s against a
  healthy 396 s, because suites were dying early rather than executing. If
  your full run comes back unusually quick, distrust it before you enjoy it.

#### If you automate the solo re-runs

`sqlite-review`'s `solo.sh` lives at `scratch/orgtree/sqlite-review/solo.sh`.
Two bugs it hit first are worth stealing the fixes for, because both produced
**a measurement that looked complete and was not** — strictly worse than one
that visibly fails:

1. **The child eats the loop's stdin.** A `while read` loop feeding suite
   names to a child gives that child the *name list* as its stdin, and the
   child consumes it. A 33-name list produced 14 runs and the loop then ended
   silently, looking like a finished result. Redirect the child:
   `… < /dev/null`.
2. **`--quick` is not universal.** A suite that rejects the flag exits 2 with
   a ~232-byte argparse error that reads exactly like a failure. **If a solo
   re-run reports `rc=2`, retrying bare is not optional** — `mcp-tool-count`
   and `prompt-view-race` both do this and both pass bare. Note the size tell
   above will NOT save you here: 232 bytes is not 132, so this one has to be
   caught by the exit code.

## `docker logs orgtree-mailhub` — the only instrument that watches from outside

Every other check in this repo asks the code what it thinks it does. The mail
hub runs in its own container and logs **every request it receives** as one
JSON line with the caller's slugs, so it is the one place you can see what
this fleet ACTUALLY did rather than what it should have.

    docker logs --tail 400 orgtree-mailhub | grep -i "unregister\|register\|sweep"

Worked example, and it is why this is written down. On 2026-09-04, in the
middle of a normal test run, this went past:

    {"path": "/api/unregister", "slugs": ["same-name.ncola_k8bx.005a42"],
     "status": 401, "ms": 1}

A **test rig reaching the operator's real hub.** Nothing was polluted — the
request 401'd because registration needs the net client and no rig starts one
— but a 401 is not a defence, it is a coincidence of what that rig happened
not to run. The path was open.

The cause was `orgs_create` holding a SECOND implementation of "which hub does
a new org point at" (`dflt.pop("net_hub_address") or net.DEFAULT_HUB_ADDRESS`)
that never consulted `net._default_address()` — so the temp-root floor landed
an hour earlier covered the helper and **not the caller**. No unit test saw
it; the hub's own log did.

Reach for this whenever you change anything that talks to a hub, and after
landing it. `{"sweep": N, "orgs_pruned": [...]}` lines are the retention sweep
doing its hourly pass.

## `group: callers` — a floor nothing stands on is not a floor

**Team standard (coordinator ruling 2026-09-04), from the defect above.** Any
floor, guard, clamp or default you land is UNTESTED until something proves the
real callers go through it. Testing the helper is the easy half and it is the
half that passes while production takes the other road.

Give the suite a group whose checks drive the actual entry point — the API
handler, the spawn path, the endpoint a user hits — and assert the property
there, not on the function you just wrote. The falsifier is cheap: revert your
own change and confirm that group and only that group turns red.

This sits beside two related standards already in force:

* **declare INERT rather than passing quietly** when an environment-coupled
  check cannot detect its fault on this machine (e.g. the two spellings of
  `%TEMP%` coinciding, which would make the short-name check meaningless);
* **pin against the thing that decides**, so a future change makes a check
  stop being TRUE rather than stop being NOTICED — assert against
  `CODEX_TIERS`/`ANTIGRAVITY_TIERS` themselves rather than a copied list.

## The tree you measured in may not be real

The tells above say when a *result* is not real. This one says when the
*tree* is not, which is worse, because a phantom failure is loud and this is
silent.

**A `finally` does not run when the process is killed.**

`sqlite-review`'s mutation tester
(`scratch/orgtree/sqlite-review/probes/p4_mutants.py`) plants a deliberate
defect in the real `backend/orgtree/store.py`, runs the suite, and restores
the file in a `finally`. A stray `pkill` took one run mid-mutant, and this
was left behind in `store.py`:

```python
conn.execute("PRAGMA synchronous=NORMAL")   # should be FULL
```

Of every defect that tool plants, that is precisely the one no in-process
test can catch: `synchronous=NORMAL` only loses data on a power cut, so
nothing observable changes. **The suite ran green over it.** It was caught by
diffing against the tool's own backup before sign-off — not by any test.

Generalise past mutation testing: **any tool that edits the tree it measures**
— a bisect script, a "temporarily disable X and re-run" one-liner, a probe
that swaps a config — can die holding its edit, and every later run in that
tree is then measuring something else while looking perfectly healthy.

- **After running any tool that edits the tree, diff it.** `git -C <worktree>
  diff --stat` takes a second and is usually enough.
- **Make such tools self-heal on startup.** A leftover backup file is
  unambiguous evidence that a previous run died holding an edit. ⚠ **Restore
  from it BEFORE reading the baseline** — reading first captures the planted
  defect *as* the baseline and bakes it in, which turns a one-run accident
  into a permanent one.
- **Assert the restore on the way out**, so the tool tells you rather than
  you having to remember to ask.
- **Do not `pkill -f` a broad pattern** while such a tool is running. That is
  how this happened.

### The pattern all of these share

Three of the entries in this document were found on the same day by three
different agents, and they are one defect wearing three faces: **the cleanup
that silently did not happen, leaving a result that looks complete and is
not.** A `finally` skipped by a kill. A `while read` loop whose child ate the
name list, so 33 suites became 14 and then a clean-looking finish. And, in
the app itself, an optimistic message bubble whose retirement depended on a
row that was never going to be written, so it sat there looking queued
forever.

None of the three announced itself. Each produced something that read as a
normal, finished, believable result. That is the thing to be suspicious of in
this repo — not the loud failures, which take care of themselves.

### The same defect inside your INSTRUMENTS, which is worse

By the end of 2026-09-04 the count was five, and the last two were not in the
code under test — they were in the things being used to judge it. Those are
worse, because a broken instrument is *internally consistent*: it produces a
clean, confident result every time you run it.

**① AN INSTRUMENT READING `None` IS NOT EVIDENCE OF ABSENCE.** Verifying that
the antigravity lane now emits its `{kind:"text"}` handover, `msg-dupes` read
websocket frames as `m["payload"]["kind"]` and got `None` for every frame. The
node was right, frames were arriving, the field simply looked absent. But
`api.py` spreads the payload at the TOP level (`{"type": "node_stream", "org":
…, "node": …, **payload}`), so the correct read is `m["kind"]`. Had that pass
been trusted it would have reported *"no `text` frame observed on a real
turn"* — and sent someone hunting a fix that was already working, or worse,
"fixing" correct code.

What caught it was that the reading **disagreed with a backend test that
already existed**. A second source of truth is the only thing that catches
this class, precisely because the broken instrument never contradicts itself.
If a new measurement says something surprising, reconcile it against something
you already trust *before* you believe it.

**② A TEST MAY ASSERT A REQUIREMENT YOU INVENTED.** Adding `draft_epoch`,
`msg-dupes` was asked to prove a per-node counter "cannot go backwards or
repeat across a restart". The test written for it asserted that a restart
**retires the draft immediately** — which nobody had asked for and which is not
true: a restart kills the turn, so the ordinary idle path clears the draft
anyway. The test failed, and the first instinct was to change the CODE to
satisfy it.

That is the trap. A test asserting an invented requirement is worse than no
test, because it is a confident-looking failure that drags working code toward
a wrong shape — and it survives review, because it is green afterwards. The
fix was to restate the assertion as the property actually required (*the desk
cannot end up permanently ahead of a restarted server*), which the existing
code satisfied.

**When a test fails, decide which is wrong before you decide how to fix it.**
Write down the property in words first; if the assertion does not read back as
that property, the assertion is the bug.

## ⚠ THE RUNNER STRIPS `ORGTREE_DATA`, SO A SUITE'S DEFAULT ROOT IS PRODUCTION

*(sqlite-review, 2026-09-03. This one is not a measurement hazard. It reaches
the live install, and it did.)*

`tools/run_tests.py` strips **every** `ORGTREE_*` variable from its children,
deliberately, so no suite can inherit a pointer at the operator's real
deployment. The consequence is the part to internalise:

> **A child that does not mint its own `ORGTREE_DATA` before importing
> `store` gets the DEFAULT — `~/orgtree` — which is production.**

Under the JSON backend that was mostly harmless: the suite read the live
documents and moved on. **Under `ORGTREE_STORE=sqlite` (now the default on
`main`) it is not**, because `claim_data_root()` migrates every unmigrated
`<slug>.json` it finds. Without an explicit guard, a checkout defaulting
`STORE_BACKEND` to `sqlite` **would migrate the live org documents the first
time a suite forgets to mint its own root**. (This is why `tools/run_tests.py`
now carries an explicit launcher guard refusing to run if `ORGTREE_DATA` is not
set in the invoking environment.)

That is not hypothetical. On 2026-09-03 it happened: `orgs/orgtree.json` became
`orgtree.json.premigration` + `orgtree.db`, for all three orgs, and the
deployed JSON build then answered every request `no such org: 'orgtree'` until
the files were put back. Nothing was lost — the premigration copies are
byte-exact, verified by sha256 against what the databases recorded — but the
org was down, and the trigger was a test run.

**The rule: flip BOTH literals, or neither.**

```python
_RIG_DEFAULT = os.path.join(os.path.expanduser("~"), "orgtree", "scratch", …)
DATA_ROOT:     str = os.environ.get("ORGTREE_DATA",  _RIG_DEFAULT)   # ← and this
STORE_BACKEND: str = os.environ.get("ORGTREE_STORE", "sqlite")       # ← not just this
```

**And assert it in the CHILD, not in the launcher.** "The runner probably won't
strip it" is not a check; a guard at module import is, because it runs in every
process that imports `store`, however it was spawned, and no caller can forget
it:

```python
if os.path.abspath(DATA_ROOT) == os.path.abspath(os.path.expanduser("~/orgtree")):
    raise RuntimeError("RIG WORKTREE refuses to run against the LIVE data root …")
```

Verify the guard by *running* it three ways before you trust it — env stripped
(must land on the scratch root), env pointed at `~/orgtree` (must **refuse**,
non-zero), env pointed at the scratch root (must work). A guard nobody has
watched fire is not yet a guard.

**Generalisation, and the reason this sits in this file:** an env var the
runner removes for your safety is also an env var your defaults have to survive
without. Any suite or tool whose behaviour depends on `ORGTREE_*` is, inside
this runner, running on its defaults — so **the default has to be the safe
answer, not the convenient one.**

## Reading the result

⚠ First, **solo-re-run every failing suite** and check its log size — see the
loaded-machine section above. What you compare is the SOLO-CONFIRMED list,
never the raw one; on a busy machine the raw list has been three times the
size of the real one.

Then: same 8 names → you broke nothing, land on parity.
A 9th name that survives a solo re-run → that one is yours. Open its log; the
✗ line has the path.

A worked example: rewording one string tripped `test_mcptool:1006`, which pins
the literal `"next turn"`. It showed up as exactly one extra name against a
baseline that was otherwise identical, which is the entire value of doing this.

## Landing (team standing rule, coordinator 2026-09-03)

**Re-check main's tip immediately before the merge, not a command earlier.**
On 2026-09-03 main moved TWICE — `9992f3c` then `9e15f3c` — in the seconds
between a `git log` and the `merge --ff-only` in the very next tool call, and
the merge was refused. Several agents land in this repo at once; the tip you
read at the top of your turn is not the tip you will merge into.

So: `git -C E:\... log --oneline -1 && git status --porcelain && git merge
--ff-only <branch>` as ONE command. If it refuses, rebase onto the new tip,
**re-run the suite** (a clean textual rebase into a file someone else just
restructured is exactly where a silent break hides), and try again.

Better still, and `storage-design`'s refinement after main moved three commits
under them: **rebase in the SAME command as the merge**, not merely before it.
The remedy is not "rebase before you merge" — everyone already does that. The
gap between the two commands IS the failure mode, so close the gap:

    git -C <wt> rebase main && git -C E:\... merge --ff-only <branch>

The suite re-run still belongs between them whenever the rebase actually
replayed your commit onto new work in a file you both touched. When it
replayed onto commits touching files you don't share, the tip check is the
part that matters and this one-liner is enough.

## The second trap: a suite the runner SILENTLY SKIPS (perf-latency, 2026-09-03)

`crash-reports` above makes two honest people report different *counts*. This
one is worse: it makes a suite **report nothing at all** while the summary
line still says everything passed.

`tools/run_tests.py` refuses to start any suite whose **source text** mentions
the live deployment's port, so a test can never talk to the operator's real
orgs. The rule is right and the implementation is deliberately loud — it
quotes the offending line "so a false positive is obvious rather than a
silently missing suite". But it greps SOURCE, and it cannot tell a socket bind
from a dict literal.

**Every suite that builds a fake ASGI scope trips it**, because the natural
thing to write is:

```python
"client": ("127.0.0.1", 1), "server": ("127.0.0.1", 7360),
```

which opens no socket at all — `server` is scope metadata for an in-process
call. Three suites were dark when this was found, in BOTH tiers:

| suite | verdict |
|---|---|
| `process-control` | **false positive.** Passed every time it was run by hand ("process control OK, 7 audit rows") and had been invisible to the runner. Fixed: the fake scope now uses a port that is not the deployment's. |
| `frozen-install` | **false positive, and NOT fixable by changing the number.** Its 7360 is inside a `launch_inventory` fixture asserting what a frozen deployment's listener table must look like — the real port is the thing under test. Grep confirms it never calls `uvicorn.run`, `.serve()`, `socket()` or `bind()`. |
| `frozen-attestation-integration` | same as above, same fixture shape, same zero binds. |

`test_tree_render_cost.py` was dark for the same reason on the day it landed,
and its results had already been quoted in a report before anyone noticed.

**What to do about it**

* **Writing a suite with a hand-built ASGI scope?** Use any port except the
  deployment's — `7999` is what the three fixed suites use — and say why in a
  comment, without writing the forbidden number.
* **After adding ANY suite, run `python tools/run_tests.py --list` and find
  your suite in the plan.** Not in the summary count — in the plan. `--list`
  prints `plan · N to run, M skipped` and names every skip with its reason.
  A suite that is skipped still leaves the run looking green.
* **Never trust `RUN COMPLETE suites=N/N` to mean your suite ran.** N counts
  what was *planned*, and a skipped suite was never planned.

### Resolved, 2026-09-03 — the opt-out marker (coordinator's ruling)

The guard is unchanged for anything that does not opt in. A suite whose port
literal is genuinely DATA declares it, in one line, and must say why:

    ORGTREE_PORT_LITERAL_IS_DATA = "the admin port appears only in
    launch_inventory fixtures asserting what a frozen deployment's listener
    table must be; this suite opens no socket (verified <date>)"

⚠ **The opt-out is louder than the skip, on purpose.** A declaring suite still
prints in the plan, carrying its stated reason:

    exclusive  frozen-install   —   ⚠ port literal declared DATA: the admin port appears only in …

The failure this area guards against is not "a suite ran" — it is "a suite
stopped running and nobody noticed". So the visible thing has to be the
opt-out. Declaring costs a sentence, which is the point: the author states
why, and a reader can check the claim.

⚠ **The marker must sit AFTER `from __future__ import annotations`** — that
import must be the first statement in the file, so putting the declaration
above it is a `SyntaxError` that surfaces as the suite failing to start.

**Both `frozen-*` suites were then run and both PASS — 20/20 and 41/41. 61
checks that had never been executing.**

Plan over the day: `132 to run, 6 skipped` → **`137 to run, 1 skipped`** (the
last is `message-visibility-live`, a deliberate `--full`-tier deferral).

⚠ **If you edit the guard's regex, test it in BOTH directions.** Editing that
line through a shell heredoc turned its `` into a literal BACKSPACE byte,
which matches nothing: the guard was silently disabled and the suites appeared
to run for the right reason while nothing was being checked. It looks exactly
like success. Prove an unmarked suite carrying the port is still skipped (a
throwaway fixture does it in seconds) as well as that a marked one runs.

A cleaner fix exists for whoever owns those suites: reference the port through
a constant so the guard never sees a literal. Not taken here — `PORT` is
env-derived (`api.py`, `ORGTREE_PORT`), so threading it into an attestation
fixture changes what that fixture asserts.

## The third trap: a `StringIO` capture cannot see a cp1252 log stream

Found 2026-09-03, by reading a production log rather than a test result.

The launcher redirects the backend's stdout to `backend.log`, and **on Windows
a redirected stream is cp1252, not UTF-8**. `print()` of a character cp1252
lacks raises `UnicodeEncodeError`. Two ways that bites:

* **Wrapped in `except`** — the line vanishes with no output, no error, no
  trace. The new access record's slow-request alarm shipped like this and
  printed **zero** times across **32** requests that crossed its threshold,
  while the ASCII access line beside it printed 236 times.
* **Not wrapped** — the process takes the raise. Proved in a subprocess:
  `python -c "print('⚠')" > file` exits **1**.

⚠ **Every suite in this repo captures stdout into `io.StringIO`, which is
unicode-native and accepts every glyph.** So a test can pass on a line that
can never reach the log. That is exactly how the alarm shipped broken with a
green run — the suite was structurally incapable of reproducing the fault.

**If you assert on printed output, encode it:**

```python
line.encode("cp1252")     # raises exactly where production raises
```

Not `assert "⚠" in line` and not a regex over the source — the failure is a
property of the BYTES, and a future author adding a glyph nobody listed would
pass any spelling check.

⚠ **`—` (U+2014) and `…` (U+2026) ARE in cp1252** (0x97, 0x85) and are fine.
Of 25 non-ASCII `print()` calls in the backend, only **four** actually broke —
`⚠` in `api.py`, `sandbox.py`, `warmpool.py` and `№` in `supervisor.py`. I
first reported all 25 as broken; that was wrong by ~6x and worth writing down,
because "it has a funny character in it" is not the test — encoding is.

`api.py` now reconfigures stdout/stderr to UTF-8 (`errors="replace"`) before
any orgtree module is imported, which fixes all four at the source; `mcptool.py`
has always done this for its own stdio. **Do not "fix" call sites by removing
glyphs** — the point is that a future author typing an em-dash need not know
any of this. `test_access_record.py` §7/§8 pin both halves.

## ⚠⚠ A PROBE CAN BE CONFIDENTLY WRONG, AND IT IS WORSE THAN PROSE

Written by `sqlite-review`, 2026-09-04, after nearly making a tool worse by
fixing it to match a check. Two instances of the same disease landed on the
same day, from opposite directions, and both are worth carrying.

**Instance 1 — an assertion that demanded ONE safe outcome when there were two.**
A rollback tool had a real defect: interrupted part-way, it could leave a data
root that a backend would start on *with an org silently missing*. The probe
written to catch that asserted **"neither backend starts"**. That sounds like
the safety property. It is not the safety property.

The invariant is **NO ORG CAN SILENTLY VANISH**, and it admits *two* safe
outcomes:

  * every moved file is put back, the root is INTACT, and the backend starts
    with EVERY org present — the best case; or
  * nothing can start at all, because the store refuses in both directions.

What must never happen is a backend starting with an org **missing**. When the
tool was fixed, its recovery path produced the *first* outcome — and the probe
went red, because it demanded the second. **Had the tool been changed to
satisfy the probe, the tool would have got worse.** The check was not merely
failing; it was arguing against correctness while looking like evidence.

**Instance 2** (`cache-invalidation-audit`, same day) is the mirror image: a
check that *looked* like it was verifying something while observing nothing at
all. One over-specifies and rejects a correct answer; the other under-specifies
and accepts anything. Both are green-or-red for reasons unrelated to the thing
they name.

### What to do about it

**When a check fails, ask which of you is wrong before you touch the code.**
The default assumption — "the check is the spec" — is exactly what makes this
dangerous, because a probe carries the authority of a measurement while being
nothing but a sentence someone wrote.

Three questions that catch it:

1. **Can you state the invariant in words that do not mention the
   implementation?** "Neither backend starts" is a description of a mechanism.
   "No org can silently vanish" is an invariant. If your assertion only
   survives in mechanism terms, it is probably pinning an accident.
2. **Does the invariant admit more than one acceptable outcome?** If it does
   and your assertion names one, the assertion is wrong, not incomplete.
3. **Has this check ever been watched to fail for the RIGHT reason?** The
   suite convention here is that a check which has never failed is not yet a
   check — the sharper version is that a check which has never failed *against
   the defect it names* is not yet a check. Mutation-test it: plant the defect
   and confirm the red comes from the defect and not from a coincidence.

And the reason this belongs in the baseline file rather than in one probe's
header: **the same failure mode applies to this document.** Every name in the
list above is an assertion about the world that was true when someone measured
it. That is why the instruction at the top is to re-measure rather than to
trust — and it is the same instruction, one level up.

## A red that was hiding behind a stale red: the kiosk-enumeration fixture

`cache-invalidation-audit` suppressed the §1 hygiene drift detector in
`test_external_mail.py`, and the suite then reached point 7 and failed:
*"kiosk enumeration: a kiosk whose doc will not load is listed WITHOUT
kiosk_cfg"*. **A stale red had hidden a live one, which is the argument for
keeping reds honest, arriving on schedule.**

### The call, and why — `sqlite-review`, 2026-09-04

**The fixture is obsolete. It is NOT a store regression, and I checked that
rather than assuming it, because it looked exactly like one.**

The check manufactured "a kiosk whose doc will not load" by rewriting an org's
internal slug to disagree with its file name. That worked because `/api/orgs`
then did a **second** `store.load_org` **by that internal slug**, which raised
`no such org` and dropped the endpoint onto its bare-row fallback — a row with
`kiosk: True` and no `kiosk_cfg`.

`d6d38cd` ("The org listing parses each document once, not twice") removed that
second lookup for performance: the endpoint keeps the document it already
parsed. Its own docstring records the consequence — *"the `LedgerError` branch
that handled that window in `orgs_list` is gone with it."*

**Measured both ways before deciding**, because "a slug/file-name disagreement
ceasing to be an error" is precisely what the SQLite work would look like:

    load_org("sealed-renamed")  -> STILL RAISES LedgerError: no such org
    list_orgs()                 -> STILL reports the internal slug
    load_org("sealed")          -> loads, as it always did

Nothing about slug/file-name disagreement stopped being an error. **The
listing simply stopped asking**, and `d6d38cd` is in `f5b4ce3` — it predates
the storage branch entirely.

**So the old behaviour was not load-bearing and is not restored.** The row now
carries *more* information, not less: both `kiosk: True` and `kiosk_cfg`. The
security property the check existed for — that `externtool.py` filters on the
authoritative `kiosk` field rather than on `kiosk_cfg` — is unaffected, and is
now asserted **directly** against the real filter with a chosen payload rather
than through a scenario that can no longer produce it. Reverting the fix in
`externtool.py` still turns it red. A second check pins the new endpoint
behaviour so the next reader is not left guessing.

⚠ The rewritten check is **backend-aware**. `store.org_path` is the `.db`
under a SQLite default, so `json.load` on it dies — the same class as the nine
format assertions in `test_persistence.py`: a test reaching past the store to
the on-disk format. **Any suite that pokes an org document as a file has this
problem the moment the default flips**, and that is a general warning, not a
note about this one fixture.

## Stating an A/B result at the precision the evidence supports

`sqlite-review`, 2026-09-04, after `phase1-audit` caught two flaws in a table I
had already sent. Both are worth keeping because neither made the conclusion
wrong — they made the *stated reason* for it wrong, which is worse in a
document somebody will reuse.

**Flaw 1: I counted failing CHECKS and presented them as run rates.** For
`dupresult` the check counts were 3 and 3, which reads as "identical". The run
rates were **JSON 2/3, SQLite 3/3**. Equal check counts across arms do not
demonstrate equal behaviour when a section can fail several checks in one run.

**Flaw 2: the log directory mixed two batches.** The rig wrote to
`logs/ab-<section>/`, so a later 3-round batch overwrote `r1..r3` of an earlier
10-round one and left `r4..r10` in place. A summary that walked the directory
silently averaged two different experiments. Fixed — one directory per batch,
stamped — because **a rig that cannot tell you which run a log came from is not
a rig.**

### What actually carries a "not backend-specific" claim

Not the counts. Three things, in this order:

1. **Identical failure SIGNATURES across arms.** Same check, same assertion,
   same captured state — for these, `{'mailbox': True, journal/transcript
   False}`, i.e. the mail is safely persisted and simply was not delivered
   inside the window.
2. **Direction reversal across batches.** A dedicated 10-round batch gave
   `freeze` at JSON 4/10 against SQLite 3/10; a later 3-round batch gave 2/3
   against 1/3. A defect that belonged to one backend does not keep changing
   which side it prefers.
3. **The failing name MOVING within a section.** `dupresult` produced three
   different check names across arms in one hour. That is a section whose
   check name is not a stable experimental unit, so comparing its names — or
   its counts of them — compares nothing.

Counts are illustration. Signatures and reversals are the evidence. **A number
that does not prove what it appears to prove is worse than no number**,
especially in a table headed for someone who will not re-derive it.

## ⚠⚠ PRESENT, PLAUSIBLE, AND INERT — the failure class of 2026-09-04

Three defects on one day shared a shape, and the shape is worth a name because
it defeats reading. In each case something **existed, looked correct, and did
nothing** — and in each case the appearance of correctness is precisely what
stopped anyone checking whether it worked.

**1. An env var set to a value the vendor never accepts.**
`AGY_CLI_DISABLE_AUTO_UPDATE="1"`, where the vendor requires the literal
string `"true"`. Present in the environment, spelled correctly, obviously
about the right thing. Inert for an unknown length of time.

**2. A floor no caller went through.** A guard that would have done its job
correctly if anything had reached it.

**3. `\b` in a non-raw Python string.** A regex written through a `'''…'''`
patch rather than an `r"…"` literal, so `\b` became a **literal backspace
byte** (0x08) instead of a word boundary. The pattern read perfectly in an
editor and matched nothing. Found with `cat -A`. **Reading it never would
have found it, at any level of care** — the character is invisible.

### Why this class is different from an ordinary bug

An ordinary bug does something wrong; you can see the wrong thing. These do
**nothing**, and nothing looks identical to "working, and the condition it
guards has not occurred." A guard that never fires and a guard that cannot
fire are the same picture from outside.

That is also why they survive review. Every one of these would pass a careful
reading, because at the level of reading they *are* correct.

### The only defence

**Prove the mechanism fired, don't verify that it exists.**

- For a setting or a flag: make it take effect and observe the effect. The
  performance harness in this work refuses to record a sample unless the child
  reports back the backend it actually resolved — and that guard was itself
  proved by asking for one backend while forcing the other, and watching it
  refuse.
- For a guard: watch it fail against the defect it names. A check that has
  never been watched to fail *for the right reason* is not yet a check.
- For a pattern, a template, or anything with escapes: **run it against known
  inputs in both directions** — does it match what it must, and leave alone
  what it must not. Both. A regex validated only on the strings it should
  match is half-tested, and the half you skipped is where the silence lives.

### And the corollary that cost the most today

**Validate against a corpus, not against your examples.** An early-abort
classifier written for this work was validated against three logs of known
outcome and pronounced good. Run against 157 real logs it called **sixty
finished suites "unfinished"** — `PASSED 21/21`, `34 checks passed`,
`10/10 checks passed`, forms its author had simply never thought of.

Three examples cannot show you the shape of a corpus. They can only confirm
that you thought of the three.

⚠ The repair for that one is instructive in its own right and it is **not**
"collect more formats". `stopped_early()` in `tools/run_tests.py` gets it
right by inverting the question: *there is no closed set of summary formats;
there IS a closed set of ways a run dies.* Find the last traceback and ask
whether anything reporting counts comes after it. Chasing formats one at a
time was the wrong shape, and a flag that cries wolf is worthless within a day.
