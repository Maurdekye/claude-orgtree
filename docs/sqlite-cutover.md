# The SQLite cutover — operator runbook

`STORE_BACKEND` now defaults to `sqlite`. This is the procedure for flipping a
live data root, and for getting back if you need to.

Read the rollback section **before** you run the cutover, not after. The order
of the rollback is not the obvious one, and the obvious one loses data.

---

## What changes, and why it was worth doing

The JSON backend keeps one document per org and rewrites the whole thing on
every save. On this install `orgs/orgtree.json` is **12.7 MB across 205 nodes
(193 archived)**, of which **74% is append-only log sections** — `mail_log`
5.1 MB, `steered_log` 2.0 MB, `events` 1.35 MB. Changing one field re-serialised
all of it.

Measured on that real document, two roots built from the same bytes, backend
the only variable, interleaved and order-reversed, N=30:

⚠ **PRELIMINARY.** These came from a fixture that copied the live root twice,
once per arm — and the live root is written continuously, so the two arms held
documents a few seconds apart rather than the same bytes. The mechanism does
not depend on that and the ratios are very unlikely to move, but the claim
"the backend selector is the only variable" was not true as measured. The
harness now takes one validated snapshot, clones it into both arms, and
records the source sha256 in the result file. This table is replaced on the
re-run.

Medians, with p90 and worst sample, because a median that hides a tail is how
a cutover feels worse than it measures:

| operation | JSON med / p90 / max | SQLite med / p90 / max | |
|---|---|---|---|
| **a turn's storage cost** (4 saves) | **385 / 404 / 483 ms** | **188 / 201 / 211 ms** | **2.0× faster** |
| one `save_org`, one field changed | 96 / 104 / **196** ms | 8.9 / 10.6 / 15.7 ms | 10.8× faster |
| one `save_org`, one mail entry appended | 95 / 101 / 114 ms | 25 / 28 / 38 ms | 3.8× faster |
| `load_org`, cold process | 55 / 60 / 64 ms | 34 / 37 / 39 ms | 1.6× faster |
| read a lazy section end to end (`mail_log`) | 0.8 / 1.7 / 2.5 ms | 30 / 32 / 40 ms | **40× slower** |
| produce a portable copy of an org | 17 / 34 / **102** ms | 177 / 228 / **280** ms | **10.5× slower** |

SQLite's tails are consistently *narrower* than JSON's — note JSON's 196 ms
worst small-save against a 96 ms median, and its 102 ms worst copy against a
17 ms median.

**The two regressions are real and are listed on purpose.**

*Reading a lazy section* is 40× slower in ratio and 30 ms in absolute terms.
It is already inside the per-turn number above: a turn pays it and is still
twice as fast, because a turn writes far more than it lazily reads.

*Producing a portable copy* is **not the same operation on both sides.** Under
JSON the document already *is* the export, so that column is a file copy;
under SQLite `export_json` reconstructs the whole document from rows. Equal
purpose, different work. It runs once, offline, during a rollback — where 177 ms
does not matter.

**Sustained writes.** 4000 consecutive saves: median 8.0 ms, worst 79.8 ms.
The worst case is the WAL checkpoint, it happened once, and **it is still
faster than JSON's median save of 94 ms**. The WAL bounds itself at ~4.1 MB
(`wal_autocheckpoint` = 1000 pages × 4096) and does not grow with write volume.
No tuning has been applied and none is recommended without its own measurement.

**Not measured:** concurrent writers. The architecture permits exactly one
claimant per root and the owner lock is enforced, so there is no supported
configuration in which two processes write one root. Every number above is
single-process, on one machine with a warm cache. Do not port the ratios to
other hardware.

---

## The cutover

**The deployed backend never receives `ORGTREE_MIGRATE=1`.** Migration is an
offline operator action, not a startup event. Anything that depends on removing
a flag "immediately afterwards" is a step someone eventually skips.

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

with the backend stopped. That does, in this order and refusing to continue if
any step fails:

1. **Claim the root** — which is what makes this process the only writer
   rather than one racing another.
2. **Export every org and validate every export** — all of them, before a
   single authoritative file moves.
3. **Park the databases**: checkpoint, close the pooled connections, then move
   `<slug>.db`, `-wal` and `-shm` out of `orgs/` into `parked-<stamp>/`.
   **Moved, never deleted.** Trash and exports live outside `orgs/`, which is
   why parking is the way out of a `BackendMismatch` refusal.
4. **Install the exports** as `<slug>.json`.

Then start the JSON build.

⚠ **Steps 3 and 4 are all-or-nothing across the whole root.** A JSON process
started part-way through silently omits every org whose database has been
parked but whose export is not yet installed — and it will *look* fine. This
is also why the invariant refuses a root holding *any* database rather than
only ones lacking a `.json`: you cannot roll back one org and leave the rest.

⚠ The pooled connection from the export step still holds each database open,
and Windows will not move a file anything holds. `cutover.py` checkpoints and
closes before moving; hand-rolling this is how you get a half-parked root.
Measured — the first version of the tool died exactly there.

⚠ **Do not start JSON part-way through.** A mid-install start silently omits
every org whose database has been parked but whose export is not yet installed.
Steps 4 and 5 are all-or-nothing across every org in the root. This is why
step 3 validates everything up front: the moment you begin moving files, you
want no reason to stop.

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
