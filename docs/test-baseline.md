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
exactly these 8 plus `warmpool`. Two agents, different tips, different
branches, same names.

That is the strongest this list has been. It is still one tip behind main and
will be again by the time you read it — which is the normal condition of this
document rather than a defect in it. Re-measure at your own tip; do not quote
these names at anyone as current.

> ⚠ **RE-MEASURE THIS LIST WHENEVER MAIN MOVES. Do not trust the names below
> because they are written down.** Three of the eight (`harvest`, `headless`,
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
    cd baseline && python tools/run_tests.py 2>&1 | grep -E "^✗|RUN COMPLETE"
    cd ../wt   && python tools/run_tests.py 2>&1 | grep -E "^✗|RUN COMPLETE"

Re-point the baseline when main moves: `git -C baseline checkout --detach <new-sha>`.

No flags, no subset, no env vars, no isolated ORGTREE_DATA — the runner mints
its own rig per suite (`$TEMP/orgtree-tests-*`) and redirects ORGTREE_DATA,
HOME and the port itself. The live backend can stay up; it does not collide.

  * **~6 minutes** wall (346-447s observed). Budget a 600s tool timeout.
  * The runner exits **rc=1** whenever anything fails, which on main is always.
    Do not treat rc as the signal — read the ✗ lines.
  * Run the two suites SEQUENTIALLY. Running them at once makes the timing
    suites flaky (see `warmpool` below).
  * `--list` prints the runner's own header (repo, python, tier, log dir).
  * Per-suite logs land in the `orgtree-tests-*` dir named in the output; the
    ✗ line gives you the exact path.

## The known-failing 8 (at `f2d42f5`: 130 suites, 122 passed, 8 failed)

| suite | why it fails, and why it is not yours |
|---|---|
| `account-pool-state` | hits the REAL API and gets a live `429 rate_limit_error`. Fails or passes with the account's usage, not with the code. |
| `crash-reports` | needs `frontend/node_modules/.bin/esbuild.cmd` to build a real minified bundle; `FileNotFoundError [WinError 2]` in any worktree without `node_modules`. **THE METHOD DETERMINES THE NUMBER: every worktree run sees 8, and only a run inside `E:\` itself sees 7.** This is not two agents disagreeing — it is one suite that cannot pass where we are all required to work. Confirmed independently by `storage-design`, 2026-09-03. Compare a worktree run only against a worktree baseline; comparing one against a number quoted from `E:\` shows a phantom `crash-reports` regression and sends you bisecting. Do NOT `npm install` to make it go away — `E:\frontend\node_modules` is off-limits. |
| `extern-handle-attach` | `norm_extern_handles([H2,H1,H2]) == [H2,H1]` — a real dedupe/order assertion, failing on main. |
| `external-mail` | rig hygiene: those rigs mint their own ORGTREE_DATA but never redirect `net_hub_address`, so they register against the operator's REAL hub. |
| `harvest` | structural fixture: "BEHAVIOUR CHANGED — ORDER: widened text assembled at line 11615, classifier still runs at 11739". Greps `supervisor.py` BY LINE NUMBER. |
| `headless` | structural fixture: `body.index('org.d.get("api_key")')` → `ValueError: substring not found`. Greps source text. |
| `run-completion` | the runner testing itself under a kill; "the killed run printed NO RUN COMPLETE line". |
| `turn-lifecycle` | structural fixture greps for `st["queue"][0:0] = leftover`, which someone rewrote to `st["queue"].extend(leftover)` (`supervisor.py:4884`). Zero matches on main → the fixture trips before it asserts anything. |

**Three of these (`harvest`, `headless`, `turn-lifecycle`) grep `supervisor.py`
by line number or literal text.** They are drift detectors, and anything that
shifts that file's lines can change what they say. If you touch
`supervisor.py`, re-measure the baseline at YOUR tip rather than trusting a
list someone measured two commits ago.

### ⚠ The count also depends on YOUR CHECKOUT: a symlinked node_modules

A frontend change is untestable without `frontend/node_modules`, and the team
pattern (started by `card-gallery/wt`, used by `mail-delivery/wt-ghost`) is a
**symlink to `E:\...\frontend\node_modules`** — never an install:

    ln -s /e/Libraries/Desktop/claude-orgtree/frontend/node_modules node_modules

That makes esbuild resolve, so **`crash-reports` PASSES and your baseline
reads 7**, like a run inside `E:\`. Same suite, third number, and this one is
a property of your own worktree rather than of where you ran it. Record which
you did next to any count you quote. The runner also writes a temp directory
(`node_modules/.orgtree-tests/`) *through* the link, into E:'s tree — harmless,
but know that it happens before you assume your worktree is inert.

The frontend suite itself is a separate, much cheaper run and is **actually
green** (491/491 as of `91b7573`), so for a frontend-only change you can have
a real pass rather than parity:

    cd frontend && node tests/run.mjs            # all of it, ~33 s
    cd frontend && node tests/run.mjs convo      # one file by substring
    cd frontend && npx tsc --noEmit              # app types (clean)
    cd frontend && npx tsc --noEmit -p tests/tsconfig.json   # ⚠ 4 PRE-EXISTING
                                                 # errors in cacheforecast/gallery

### Broken on main but NOT in the default run

`test_message_visibility_live` — fails **40/40 on pristine `f2d42f5`**: the
rig's hire op 422s, so nothing is ever sampled and every check fails for the
same reason. Reported by `msg-dupes`, 2026-09-03. It is not in the 8 above
because the default tier skips it (the runner reports 5-6 `skipped`), so you
will only meet it if you invoke it directly — and then it looks catastrophic
and looks like yours. It is neither.

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

## ⚠ THE RUNNER STRIPS `ORGTREE_DATA`, SO A SUITE'S DEFAULT ROOT IS PRODUCTION

*(sqlite-review, 2026-09-03. This one is not a measurement hazard. It reaches
the live install, and it did.)*

`tools/run_tests.py` strips **every** `ORGTREE_*` variable from its children,
deliberately, so no suite can inherit a pointer at the operator's real
deployment. The consequence is the part to internalise:

> **A child that does not mint its own `ORGTREE_DATA` before importing
> `store` gets the DEFAULT — `~/orgtree` — which is production.**

Under the JSON backend that is mostly harmless: the suite reads the live
documents and moves on. **Under `ORGTREE_STORE=sqlite` it is not**, because
`claim_data_root()` migrates every unmigrated `<slug>.json` it finds. So a
worktree with the `STORE_BACKEND` default flipped to `sqlite` — the documented
way to exercise that backend through this runner — **migrates the live org
documents the first time a suite forgets.**

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
