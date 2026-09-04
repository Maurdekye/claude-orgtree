# The SQLite cutover — operator runbook

SQLite is orgtree's canonical format and `STORE_BACKEND` defaults to `sqlite`.
JSON is the legacy format: **deprecated and past LTS as of 2026-09-04 (user
ruling)**. It is not a backend you choose between — it is a format you are
migrated off, automatically, the first time you update.

**If you are just upgrading an install, read the next section and stop.** This
document is the operator runbook for the conversion itself: what the automatic
path does on your behalf, and what to do at a console when it has not.

Read the rollback section **before** you run the cutover, not after. The order
of the rollback is not the obvious one, and the obvious one loses data.

⚠ **Deprecated does not mean deleted, and three things stay.** The JSON
*reader* stays, because a migration that cannot read JSON cannot migrate
anyone — it is the on-ramp. `cutover.py rollback`, which writes JSON back out,
stays and becomes *more* load-bearing now that the migration is automatic: it
is the safety net, emergency-only rather than gone. And `MigrationRefused` /
`BackendMismatch` stay, because deprecating a format does not make a misread or
half-converted root safe.

---

## What changes, and why it was worth doing

The JSON backend keeps one document per org and rewrites the whole thing on
every save. On this install `orgs/orgtree.json` was **12.7 MB across 205 nodes
(193 archived)** when this was written and is growing by roughly half a
megabyte a day, of which **74% is append-only log sections** — `mail_log`
5.1 MB, `steered_log` 2.0 MB, `events` 1.35 MB. Changing one field re-serialised
all of it.

Measured on that real document, two roots built from the same bytes, backend
the only variable, interleaved and order-reversed — 2026-09-04, N=30, on a
machine checked quiet at both ends (433
processes against a 444 quiet baseline). **One validated snapshot of the live
root, cloned into both arms, with each org's sha256 asserted equal before any
measurement** — the source hashes and the machine conditions are both in
`logs/c3-perf.json`, so the fixture is checkable from the artifact rather than
from this sentence.

Medians **with p90 and worst sample**, because a median that hides a tail is
how a cutover feels worse than it measures:

| operation | JSON med / p90 / max | SQLite med / p90 / max | |
|---|---|---|---|
| **a turn's storage cost** (4 saves) | **436 / 468 / 1085 ms** | **219 / 248 / 293 ms** | **2.0× faster** |
| one `save_org`, one field changed | 108 / 127 / 196 ms | 10.3 / 12.7 / 19.7 ms | **10.5× faster** |
| one `save_org`, one mail entry appended | 107 / 129 / 232 ms | 28.2 / 33.0 / 45.2 ms | 3.8× faster |
| `load_org`, cold process | 62.4 / 67.1 / 67.7 ms | 40.8 / 45.8 / 68.3 ms | 1.5× faster |
| read a lazy section end to end (`mail_log`) | 0.86 / 2.2 / 2.5 ms | 34.2 / 38.3 / 47.7 ms | **39.7× slower** |
| produce a portable copy of an org | 19.9 / **330** / 391 ms | 208 / 267 / 475 ms | **10.5× slower** |

### ⚠ Read the tails, because two of these rows are misleading at the median

**JSON's `export` distribution is BIMODAL.** Its median is 19.9 ms and its p90
is **330 ms** — roughly three samples in thirty come back an order of
magnitude slow, a file copy plus `fsync` hitting a slow path. So "JSON is
10.5× cheaper at producing a copy" is true about the middle and misleading
about the experience. SQLite is genuinely slower here; JSON is not reliably
fast here.

**SQLite does not have JSON's per-turn tail, and that matters more than the
median for anything a user waits on.** JSON's worst `turn_shape` sample was
**1085 ms against a 436 ms median — 2.5×**. SQLite's worst was 293 against
219 — **1.3×**. The same pattern holds on small saves: JSON 196 ms worst
against a 108 ms median, SQLite 19.7 against 10.3.

⚠ An earlier version of this table was measured on a fixture that copied the
live root **twice**, once per arm, so the two arms held documents taken
seconds apart. That was a real methodology defect and the claim "the backend
selector is the only variable" was not true as measured. It was corrected and
re-run: **every ratio came back within a rounding of the preliminary numbers.**
The absolutes rose about 10% **on both arms** because the live document grew
from 12.7 MB to 13.3 MB during the day — which is the useful part of that
observation, because it is evidence the mechanism is **not fixture-dependent**
and will save the next person re-measuring this when the document is 20 MB.

**The two regressions are real and are listed on purpose.**

*Reading a lazy section* is 39.7× slower in ratio and 34 ms in absolute terms.
It is already inside the per-turn number above: a turn pays it and is still
twice as fast, because a turn writes far more than it lazily reads.

*Producing a portable copy* is **not the same operation on both sides.** Under
JSON the document already *is* the export, so that column is a file copy;
under SQLite `export_json` reconstructs the whole document from rows. Equal
purpose, different work. It runs once, offline, during a rollback — where
**208 ms** (worst measured 475 ms) does not matter.

**Sustained writes** (separate soak, 2026-09-04 12:46Z, 4000 consecutive
saves): median 8.0 ms, worst 79.8 ms.
The worst case is the WAL checkpoint, it happened once, and **it is still
faster than JSON's median save in the same soak, 94 ms**. The WAL bounds itself at ~4.1 MB
(`wal_autocheckpoint` = 1000 pages × 4096) and does not grow with write volume.
No tuning has been applied and none is recommended without its own measurement.

**Not measured:** concurrent writers. The architecture permits exactly one
claimant per root and the owner lock is enforced, so there is no supported
configuration in which two processes write one root. Every number above is
single-process, on one machine with a warm cache. Do not port the ratios to
other hardware.

---

## If you are UPGRADING AN EXISTING INSTALL, you do not follow this runbook

**User ruling, 2026-09-04 (17:00Z and 17:02Z).** SQLite is orgtree's canonical
and default format. JSON is **deprecated and past LTS**. An existing install
still on JSON is migrated **automatically the moment it updates** — no prompt,
no flag, and nothing for the operator to know or type:

```
powershell -ExecutionPolicy Bypass -File update.ps1     # Windows
./update.sh                                             # Linux / macOS
```

That is the whole procedure. Everything below this section is the record of
what those scripts do on your behalf, and what to do at a real console when
something has already gone wrong.

**The defect this closes.** `main` defaults to `ORGTREE_STORE=sqlite`. Before
2026-09-04 an install still on JSON that pulled `main` got a backend that
refused to start (`MigrationRefused`) against its own data root — and if it had
the autostart tasks registered, `orgtree-ensure` relaunched that refusing build
every five minutes forever. A routine `git pull` became a permanent outage.

**How the automatic upgrade is wired**, per platform, because they differ:

| | Windows | Linux / macOS |
|---|---|---|
| detects | `update.ps1` §1c | `update.sh` §1c |
| when | after the `git pull`, before the UI build, long before the stop | the same |
| how | hands the whole sequence to `tools/cutover_deploy.py`, detached | runs `tools/cutover.py` inline, between its own stop and start |
| mutex + prove-stopped | yes (`Global\orgtree-update`, owner-lock probe) | no — see below |
| automatic rollback | yes, the wrapper's drilled ladder | **no** — it prints the command |
| 5-minute watchdog | `-EnsureUp` brings a JSON root back up **on JSON** and writes `UPGRADE-PENDING.txt` into the data root | no watchdog exists on POSIX |

⚠ **Why the detection is after the pull and not at the top of the script.** The
question being asked is about *the code this run is about to deploy*. An install
still on the old build has a `store.py` that still defaults to `json`, so a
check placed before the pull reads "JSON code, JSON root, all fine" and does
nothing — inert on exactly the population it exists for. It is equally
important that it is **before the stop**: an install that decides here has not
been stopped and is still serving; one that discovers the problem after the stop
is down.

⚠ **POSIX is not the same path and this document does not pretend otherwise.**
`tools/cutover_deploy.{py,ps1}` is Windows-only. `update.sh` gets the same
*outcome* by driving the same portable tool (`tools/cutover.py`) in the window
it already opens between stopping the backend and starting it, but it has no
machine-wide mutex, does not prove the stop by taking the data root's owner
lock, and **never rolls back automatically** — it stops and prints the rollback
command instead, because a rollback rewrites org authority from an export and
that is not a thing a second, undrilled implementation should decide to do.

**What it never does.** The deployed backend still never receives
`ORGTREE_MIGRATE=1`. What the 2026-09-04 ruling changed is only *who supplies
the authorisation*: the deploy now supplies it on the operator's behalf, still
scoped to the single child process that migrates, which then exits. See the
note under "The cutover" below.

**What it will not do seamlessly, on purpose.** A **mixed** root — both `.db`
and `.json` in `orgs/` — starts nothing and stops the deploy. "Seamless" does
not extend to guessing about half-migrated data. And a failed migration leaves
the install **running on its old build**, not down.

**Opting out.** `ORGTREE_NO_AUTOCUTOVER=1` skips the automatic upgrade; you
then also need `ORGTREE_STORE=json` or the backend refuses the root it is
pointed at. This is also how `tools/cutover_deploy.ps1` stops the `update.ps1`
it runs at its own step 5 from handing back to it.

## ⚠ If you are running *inside* orgtree, do not follow the steps by hand

Step 1 below is "stop the backend", and every agent on this machine runs inside
that backend. Typed into an agent's own shell, step 1 kills the shell and steps
2–5 never happen: the root is left mid-flight with nobody watching, and the
thing that would have brought it back is the process that just died.

Use the detached wrapper instead. It performs this whole runbook — including
both recoveries below — from a process with no parent to lose:

```
python tools\cutover_deploy.py C:\Users\<you>\orgtree
```

It prints a log path and returns in about two seconds; everything after that
happens without it. Read the log afterwards — it is the only record.

Two things it does that hand-running does not, and that are easy to forget:

* it holds `Global\orgtree-update` for the whole migration, so the **5-minute
  `orgtree-ensure` task cannot relaunch a backend into the middle of it**;
* it pins `ORGTREE_STORE=json` when it has to bring a backend back up on a root
  it did *not* migrate. The checkout on disk now defaults to SQLite, so "just
  start it again" no longer means "start the backend that was running".

The steps below remain the record of what actually happens, and are what to
follow when the backend is already down and you are at a real console.

## The cutover

**The deployed backend never receives `ORGTREE_MIGRATE=1`.** Migration is an
offline action performed by a process that then exits, never a startup event.
Anything that depends on removing a flag "immediately afterwards" is a step
someone eventually skips.

> ⚠ **Amended 2026-09-04 by user ruling.** This paragraph used to continue
> "…and it is an *operator* action, never automatic". That is no longer true of
> the upgrade path: `update.ps1` / `update.sh` now supply the authorisation
> themselves when they find an unmigrated JSON root, because the ruling is that
> an existing install must be migrated with no friction the moment it updates.
>
> **The rest of the rule is unchanged, and is load-bearing.** The flag still
> lives only in the environment of the one child that runs `cutover.py migrate`
> — a one-shot `.cmd` file on Windows, a command prefix on POSIX — and that
> child exits. No process that goes on to *start a backend* has ever held it.
> A backend that could convert a data root as a side effect of being pointed at
> one is the 2026-09-03 incident, and that is still forbidden. What changed is
> who types the authorisation, not where it lives or how long it lasts.
>
> `tools/cutover.py migrate` also still refuses without the flag, so the gate
> is not decoration: the authorisation still comes from outside the tool that
> does the converting.

1. **Stop the backend.** This is not politeness — it is what guarantees the
   migrating process holds the owner claim rather than racing for it.

2. **Migrate, with the flag in that one command's environment only.**

   **Windows** (this install — PowerShell or cmd):

   ```
   cmd /c "set ORGTREE_MIGRATE=1&& python tools\cutover.py migrate <root>"
   ```

   **POSIX:**

   ```
   ORGTREE_MIGRATE=1 python tools/cutover.py migrate <root>
   ```

   Both forms put the variable in the **child's** environment, where it dies
   with the process. ⚠ Do **not** use `$env:ORGTREE_MIGRATE = "1"` in a shell
   you keep using and then clean it up afterwards — a variable that must be
   removed later is a step someone eventually skips, which is the whole reason
   the deployed backend never receives this flag at all.

   `tools/cutover.py migrate` deliberately does **not** set the flag for you.
   Converting a data root rewrites it, so the authorisation has to come from
   outside the tool; a tool that authorises itself makes the gate decoration.
   Run without the flag and it refuses, exit 2, printing the two forms above.

   `claim_data_root` performs the migration — it is the boot path, not a
   separate step. **Measured downtime: 738 ms** for this install's three orgs,
   20.6 MB.

   Each `<slug>.json` becomes `<slug>.json.premigration` (byte-identical to the
   source — verified by sha256) plus `<slug>.db`. The migration verifies itself:
   a document that does not round-trip aborts, leaves the `.json` untouched and
   deletes the candidate database.

3. **Export and validate every org, still offline, BEFORE the first boot.**

   ```
   python tools/cutover.py export-verify <root>
   ```

   This exports every database and proves every export loads as an `Org`. It
   is not a formality and it is not optional: **it is the step that makes a
   rollback possible at all.** From the moment the flip build accepts its
   first write, `<slug>.json.premigration` is no longer a way back — only a
   current, validated export is. Non-zero exit means do not start the flip
   build.

   Keep the files it writes under `exports/`.

4. **Start the backend.** It finds an already-migrated root, so there is no
   migration to perform and nothing to authorise.

### If you get the order wrong

Both directions refuse, loudly, before writing anything:

- SQLite pointed at an unmigrated JSON root → `MigrationRefused`, naming the
  pending orgs and the opt-in.
- JSON pointed at a root holding databases → `BackendMismatch`. This one used
  to **fail open**: the process started, claimed the root, and reported zero
  orgs while every health check passed.

The rule is **one active format per root**, and it is enforced in both
directions.

---

## The rollback

⚠ **`<slug>.json.premigration` is NOT a rollback.** It is the document as it
stood *before* the migration and contains none of the writes SQLite has
accepted since. Restoring it is a discard. On this install, one such file was
found sitting in the live root **15.1 hours stale** — it has been renamed
`.STALE-…-DO-NOT-RESTORE` for exactly this reason.

The rollback is: **take a current export, then park the database.**

```
python tools/cutover.py rollback <root>
```

with the backend stopped. That does, in this order:

1. **Claim the root** — which is what makes this process the only writer
   rather than one racing another.
2. **Export every org and validate every export** — all of them, before
   anything moves.
3. **Install every export** as `<slug>.json`, *while every database is still
   authoritative* — temp file, fsync, atomic replace, then the installed
   bytes are re-read. A `.json` beside a live `.db` is inert: SQLite reads the
   database and ignores it.
4. **Only then park the databases**: checkpoint, close the pooled
   connections, move `<slug>.db`, `-wal` and `-shm` into `parked-<stamp>/`.
   **Moved, never deleted.**

Then start the JSON build.

### ⚠ Why install-before-park, which is not the obvious order

The obvious order — park, then install — **fails open**, and it was measured
doing so. Interrupt the parking loop (an external lock, an I/O error, the
process dying) and you get some orgs parked with no `.json` installed. Start
the default SQLite build on that root and **it comes up cleanly reporting only
the orgs that survived**: the parked ones have silently vanished, because a
slug holding only a `.json.premigration` is not "pending" and the migration
wall never sees it.

Installing first makes every interruption fail **closed**:

| interrupted… | SQLite | JSON |
|---|---|---|
| after step 2 | works, all orgs | refuses (databases present) |
| after step 3 | works, all orgs | refuses (databases present) |
| **during step 4** | **refuses** (parked slugs are JSON-without-DB) | **refuses** (a database remains) |
| after step 4 | refuses until migrated | starts, all orgs |

During the parking, *neither backend can start until the last database moves*.
That is the correct behaviour for a half-finished rollback, and it is only
available because the mismatch wall refuses on **any** active database rather
than only on ones lacking a `.json`.

⚠ A rollback also cannot be done one org at a time: the rule
is one active format per root, not per org, so you cannot roll back one org
and leave the rest.

⚠ The pooled connection from the export step holds each database open, and
Windows will not move a file anything holds. `cutover.py` checkpoints and
closes first; hand-rolling this is how you get a half-parked root. Measured —
an early version of the tool died exactly there.

⚠ **This document used to promise that the move was all-or-nothing. It was
not, and the promise was the dangerous part.** A tool cannot make a sequence
of file moves atomic by asserting that it is: an external lock, an I/O error
or the process dying part-way through defeats it, and the earlier
park-then-install order then left orgs that **SQLite started on cleanly while
they were simply gone.** A correct-sounding guarantee in a runbook is how
somebody re-runs this at 3am with confidence they have not earned.

What is true is weaker and more useful: **the safety does not come from the
tool completing, it comes from the two walls.** Interrupt this procedure
anywhere and the root refuses to start under one backend or both — see the
table above. You do not need the rollback to be atomic; you need it to be
unable to leave a root that looks fine and is missing an org. That is what is
enforced, and it is enforced by the store rather than by this document.

### If a rollback fails part-way

**Which recovery you need depends on how far it got, and `cutover.py rollback`
tells you which state it is looking at rather than making you work it out.**

| state of `orgs/` | what happened | what to do |
|---|---|---|
| every org still has a `.db` | it failed before or during the install, or the put-back succeeded | fix the blocker and **re-run the same command** |
| some orgs are `.json`-only, others still have a `.db` | **killed part-way through parking** | see below — a plain re-run *cannot* work |
| no `.db` left at all | it finished | nothing to do; **start the JSON build** |

⚠ **A plain re-run cannot recover a part-way-parked root**, and this document
said it could until 2026-09-04. The already-parked slugs now look like
unmigrated JSON, so claiming the root refuses before the tool can resume. The
fix is not to make the tool push through: it is to authorise, explicitly, the
one operation that reverses the partial move — rebuilding those slugs'
databases **from their already-installed current exports**, restoring
whole-root SQLite authority, after which the rollback completes normally:

```
Windows  cmd /c "set ORGTREE_MIGRATE=1&& python tools\cutover.py rollback <root>"
POSIX    ORGTREE_MIGRATE=1 python tools/cutover.py rollback <root>
```

The tool prints exactly that command, with the list of which slugs are parked
and which are still databases, so nobody has to derive it at 3am.

⚠ **It is deliberately not automatic.** Reconstructing an org's authority from
an export is precisely the operation that must never happen because a tool
decided it was probably fine — the value of the two walls is that a confused
root *stops* instead of guessing. Read the two lists the tool prints and
confirm they are what you expect before running it.

Nothing is lost in any of these states: the exports were installed and
validated before the first database moved.

---

## What lives where afterwards

```
orgs/<slug>.db                  the org (authoritative)
orgs/<slug>.db-wal, -shm        SQLite sidecars; empty after a clean stop
orgs/<slug>.json.premigration   the pre-migration document. A BACKUP, never a
                                rollback once SQLite has taken a write.
exports/<slug>-<stamp>.json     what a rollback installs. Deliberately outside
                                orgs/, so the JSON backend never lists one as
                                an org.
deleted/<slug>-<stamp>.*        a deleted org, whole. Putting it back is the
                                restore.
```

A delete moves every artifact of an org under one trash stem, or moves none of
them: if it cannot move the document it does not move the database either.
