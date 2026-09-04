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

| operation | JSON | SQLite | |
|---|---|---|---|
| **a turn's storage cost** (4 saves) | **385 ms** | **188 ms** | **2.0× faster** |
| one `save_org`, one field changed | 96 ms | 8.9 ms | 10.8× faster |
| one `save_org`, one mail entry appended | 95 ms | 25 ms | 3.8× faster |
| `load_org`, cold process | 55 ms | 34 ms | 1.6× faster |
| read a lazy section end to end (`mail_log`) | 0.8 ms | 30 ms | **40× slower** |
| produce a portable copy of an org | 17 ms | 177 ms | **10.5× slower** |

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

   ```
   ORGTREE_MIGRATE=1 ORGTREE_STORE=sqlite ORGTREE_DATA=<root> \
       python -c "from orgtree import store; store.claim_data_root()"
   ```

   `claim_data_root` performs the migration; there is no separate step. Never
   export the flag to a surviving shell, a user or machine environment
   variable, a service definition, a scheduled task, a wrapper or a `.env`. It
   must die with the process that used it.

   **Measured downtime: 738 ms** for this install's three orgs, 20.6 MB.

   Each `<slug>.json` becomes `<slug>.json.premigration` (byte-identical to the
   source — verified by sha256) plus `<slug>.db`. The migration verifies itself:
   a document that does not round-trip aborts, leaves the `.json` untouched and
   deletes the candidate database.

3. **Verify, still offline.** `pending_migrations()` must be empty and every
   org must load.

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

1. **Stop the backend.** The rollback process must hold the owner claim.
2. **Export every org** from SQLite: `store.export_json(slug)` for each.
3. **Validate every export before moving anything** — each one must load as an
   `Org`. All of them, before step 4 touches a single authoritative file.
4. **Park the databases**: move `<slug>.db`, `-wal` and `-shm` out of `orgs/`.
   **Move, never delete.** Trash and exports live outside `orgs/`, which is why
   parking is the way out of a `BackendMismatch` refusal.
5. **Install the exports** as `<slug>.json`.
6. **Then** start the JSON build.

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
