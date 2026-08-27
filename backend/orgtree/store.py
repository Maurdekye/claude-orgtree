# pyright: strict
"""Multi-org persistence (№36). One JSON file per org under the DATA root.

Data root is ~/orgtree (NOT ~/.claude — spike finding 4, and node scratch dirs live
beside the ledger data).

⚠ SPIKE FINDING 4, RESTATED 2026-08-07 (six measurements against the pinned CLI, after
it misled a diagnosis): the file tools do not "refuse" a ~/.claude path — they raise a
PERMISSION REQUEST ("… which is a sensitive file"). An interactive seat can answer it;
a HEADLESS turn has no approver, so it surfaces as a refusal. The distinction matters
because the gate is not a classifier you can satisfy: an Edit(//path/**) allow rule, an
explicit --add-dir on the path, --permission-mode dontAsk, and a PreToolUse hook
returning permissionDecision=allow were each measured and each still refused. The gate
sits ABOVE the allow-rule, add-dir and hook layers. Only --permission-mode
bypassPermissions clears it, and that bypasses every other check too (user ruling
2026-08-07: agents that want to write the global skills run bypassPermissions; nothing
is plumbed over the file tools to fake it).

Layout:

    ~/orgtree/
      orgs/<slug>.json          the ledger documents
      scratch/<slug>/<node>/    node working dirs (flat per §7.6; made by the supervisor)

Writes are atomic (tmp + os.replace).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Generator
from typing import Any, cast

from datetime import datetime, timezone
from .ledger import LedgerError, Org, slugify
from .ledger import now as _ledger_now

DATA_ROOT: str = os.environ.get("ORGTREE_DATA", os.path.expanduser("~/orgtree"))

# Coarse per-process guard around load-modify-save cycles: API ops and the
# supervisor's notice drain both rewrite org docs; without this a stale copy
# could resurrect just-delivered notices (double delivery).
DOC_LOCK = threading.RLock()


# ---------------------------------------------------------------- the latch
# Windows: `os.replace` over the live doc fails with WinError 5 while ANY
# handle on the destination is open — and MoveFileEx opens the target
# EXCLUSIVELY itself, so no share-mode trick on the reader's side helps
# (measured: a reader holding a FILE_SHARE_DELETE handle blocks the replace
# exactly as a plain `open()` does). The retry-with-backoff below was written
# believing this was a brief collision. It is not: with 8 reader threads
# looping on `open()`, `os.replace` succeeded 0 times in 1,659 attempts
# (0.00%), and at 4R/4W 18 of 8,467 (0.2%). The writer does not lose a race,
# it never gets to run — the 1.9 s backoff budget just delays the raise.
#
# So readers and the replace are made not to OVERLAP, by the cheapest thing
# that achieves it: readers take this latch SHARED for the microseconds of
# the byte read only (they never block each other, and they still never take
# DOC_LOCK — №22's read-outside-the-lock property is untouched), and
# `save_org` takes it EXCLUSIVE across the single `os.replace`. JSON parsing
# happens after the handle is closed and the latch is dropped, so a slow
# parse costs a writer nothing.
#
# ⚠ Nothing that can call back into load_org/save_org may run while this is
# held — the held regions are one `read()` and one `os.replace()`, keep them
# that way. It is per-process only, like DOC_LOCK; the retry loops stay as
# the residual defence against another process (a backup agent, an on-access
# virus scanner) holding the doc open.
class _IOLatch:
    """Writer-preferring shared/exclusive latch. Writer preference is the
    point: without it the reader storm above is exactly what starves the
    replace."""

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False
        self._waiting = 0

    @contextlib.contextmanager
    def shared(self) -> Generator[None]:
        with self._cond:
            while self._writer or self._waiting:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if not self._readers:
                    self._cond.notify_all()

    @contextlib.contextmanager
    def exclusive(self) -> Generator[None]:
        with self._cond:
            self._waiting += 1
            while self._writer or self._readers:
                self._cond.wait()
            self._waiting -= 1
            self._writer = True
        try:
            yield
        finally:
            with self._cond:
                self._writer = False
                self._cond.notify_all()


_IO = _IOLatch()


# ------------------------------------------------- one backend per data root
# MEASURED, 2026-08-04 (test_compaction.py "xproc"): two OS processes running
# the canonical `with DOC_LOCK: load_org → mutate → save_org` cycle against one
# org doc lose **32–74 % of their completed writes**, with **zero exceptions,
# zero torn reads and zero orphaned temp files**. Both guards above are
# per-process: `DOC_LOCK` is a `threading.RLock` and `_IOLatch` a `Condition`,
# and `os.replace` is atomic — which is exactly why the failure is invisible.
# Every writer is told it succeeded; the loser's changes are simply not there.
#
# Note what this does NOT justify: a cross-process lock around `save_org`
# alone would not help at all. The race is the read-modify-write CYCLE, so a
# correct lock would have to span `load_org … save_org`, i.e. replace
# `DOC_LOCK` in every caller — regions that spawn CLI children and can be held
# for the length of a 600 s compaction fork. That is a deadlock surface, not a
# fix.
#
# So the rule the architecture already states — ONE BACKEND PER DATA ROOT — is
# enforced at the one moment it is cheap and safe to enforce: process start.
# The claim is an OS-level file lock, not a PID file with a staleness
# heuristic. That distinction is load-bearing: a stale-lock STEAL based on
# mtime is independently broken against a merely-slow (not dead) holder —
# reproduced 2026-08-04, four processes' critical sections overlapping by up
# to 2.0 s because `release()` deleted whatever file was at the path. A kernel
# lock has no stale state at all: when the holder dies, however it dies, the
# handle closes and the lock is gone.
#
# Wired at the top of `api.main()` (`store.claim_data_root()`), which refuses
# startup with a wall when another process holds the root. The mechanism is
# tested end to end in real subprocesses by `test_compaction.py` (section
# "xproc · the owner claim"). ⚠ Tests and drills that spawn their own backend
# must use an isolated ORGTREE_DATA — they already do, and now it is enforced.
_owner_fd: int | None = None


class DataRootBusy(RuntimeError):
    """Another live process already owns this ORGTREE_DATA."""


def owner_file(root: str | None = None) -> str:
    return os.path.join(root or DATA_ROOT, ".owner")


def _try_lock(fd: int) -> bool:
    """Exclusive, non-blocking, on BYTE 0. False = someone else holds it.

    ⚠ `msvcrt.locking` locks a range starting at the file's CURRENT position,
    so the seek is part of the contract, not tidiness: locking at EOF would
    give two processes two different byte ranges and mutual exclusion would
    silently not hold. Hence a raw fd (position 0 after `os.open`) rather than
    a text handle opened `"a+"` (position EOF)."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def claim_data_root(root: str | None = None) -> None:
    """Claim exclusive ownership of the data root for THIS process.

    Raises `DataRootBusy` if a live process already holds it. Idempotent
    within a process. Released by the OS when the process exits, however it
    exits — there is no cleanup to forget and no stale file to reason about.
    """
    global _owner_fd
    if _owner_fd is not None:
        return
    base = root or DATA_ROOT
    os.makedirs(base, exist_ok=True)
    fd = os.open(owner_file(base), os.O_RDWR | os.O_CREAT, 0o644)
    if not _try_lock(fd):
        held = ""
        try:
            os.lseek(fd, 1, os.SEEK_SET)
            held = os.read(fd, 200).decode("utf-8", "replace").strip()
        except OSError:
            pass
        os.close(fd)
        raise DataRootBusy(
            f"{base!r} is already in use by another orgtree process"
            + (f" ({held})" if held else "")
            + " — one backend per data root. Concurrent writers silently lose "
              "32-74% of their completed writes (measured; see the comment "
              "above this in store.py). Stop the other process, or point "
              "ORGTREE_DATA somewhere else.")
    # byte 0 is the lock byte and stays a filler; the identity lives after it
    # so a process that LOST the race can still read who holds the root
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, (f"-pid={os.getpid()} "
                      f"since={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
                 .encode("utf-8"))
        os.ftruncate(fd, 64)
    except OSError:
        pass
    _owner_fd = fd              # held for the process lifetime, deliberately


def release_data_root() -> None:
    """Drop the claim early (tests, a graceful shutdown). Normally unnecessary
    — process exit does it."""
    global _owner_fd
    fd, _owner_fd = _owner_fd, None
    if fd is None:
        return
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _orgs_dir() -> str:
    d = os.path.join(DATA_ROOT, "orgs")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_slug(slug: str) -> str:
    """A slug is a FILE NAME, and every public entry point takes it straight
    off the wire (`/api/orgs/{slug}`). Starlette's path converter is `[^/]+`,
    which on Windows still admits a backslash — so before this check
    `DELETE /api/orgs/..%5Cdefaults` returned 200 and renamed `<data>/
    defaults.json` (the global org defaults) into the trash. Any relative
    `.json` reachable from `<data>/orgs/` was a moveable target.
    Reject by SHAPE, then confirm the resolved path really lands in orgs/ —
    the second check is what keeps this correct if the slug charset ever
    widens."""
    if not isinstance(slug, str) or not slug or len(slug) > 128:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise LedgerError(f"invalid org slug: {slug!r}")
    if (slug in (".", "..") or slug.startswith((".", "-"))
            or any(c in slug for c in '/\\:*?"<>|\0')
            or slug != slug.strip()
            # control characters resolve INSIDE orgs/ so the containment check
            # below passes them, but they make a file nothing can address by
            # name. Caught by an independent replay of this guard 2026-08-04:
            # "a\bx" was accepted.
            # ⚠ Windows RESERVED DEVICE NAMES are deliberately NOT rejected.
            # The folklore is that `con`/`nul`/`com1` are unusable with any
            # extension; MEASURED on Windows 11 2026-08-04, `con.json`,
            # `nul.json`, `com1.json` and `aux.json` all write and read back
            # correctly — the reservation binds the BARE name. A guard here
            # would refuse a legitimate org called "con" or "aux" for nothing,
            # and test_persistence.py asserts they round-trip.
            or any(ord(c) < 0x20 or ord(c) == 0x7f for c in slug)):
        raise LedgerError(f"invalid org slug: {slug!r}")
    p = os.path.join(_orgs_dir(), slug + ".json")
    if os.path.dirname(os.path.abspath(p)) != os.path.abspath(_orgs_dir()):
        raise LedgerError(f"invalid org slug: {slug!r}")
    return slug


def org_path(slug: str) -> str:
    return os.path.join(_orgs_dir(), _safe_slug(slug) + ".json")


def scratch_root(slug: str) -> str:
    return os.path.join(DATA_ROOT, "scratch", slug)


_TMP_GRACE = 300.0     # seconds; a live save's tmp lives for milliseconds


def _sweep_tmp() -> None:
    """A save that dies between mkstemp and os.replace — the process killed,
    a non-serialisable value, the replace retry giving up — leaves its temp
    file behind forever. Measured: 12 kills mid-save left 9 orphans holding
    11.9 MB beside a 1.8 MB live doc. `save_org` now cleans up its own
    failures; this catches the ones no `finally` can (SIGKILL, power loss).
    Age-gated so it can never touch a save in flight."""
    cutoff = time.time() - _TMP_GRACE
    try:
        names = os.listdir(_orgs_dir())
    except OSError:
        return
    for f in names:
        if not f.endswith(".tmp"):
            continue
        p = os.path.join(_orgs_dir(), f)
        try:
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


def _read_bytes(p: str) -> bytes:
    """The ONE read path. Under the shared latch (so a concurrent os.replace
    is not starved), slurped in one go (so the handle is not held across the
    parse), with the retry left as the residual cross-process defence."""
    for i in range(20):
        try:
            with _IO.shared():
                with open(p, "rb") as f:
                    return f.read()
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.01 * (i + 1))
    raise OSError(f"could not read {p!r}")   # unreachable


def list_orgs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    _sweep_tmp()
    for f in sorted(os.listdir(_orgs_dir())):
        if not f.endswith(".json"):
            continue
        try:
            # ⚠ the `except: continue` below means a TRANSIENT read failure
            # silently DROPS an org from the listing — the org list flickers
            # rather than erroring. Hence the latched, retrying read: by the
            # time this raises, the file really is unreadable.
            doc = json.loads(_read_bytes(os.path.join(_orgs_dir(), f))
                             .decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        live = sum(1 for n in doc.get("nodes", {}).values() if n.get("state") == "live")
        out.append({"slug": doc.get("slug", f[:-5]), "name": doc.get("name", f[:-5]),
                    "nodes": len(doc.get("nodes", {})), "live": live,
                    "kiosk": doc.get("kiosk") is not None,
                    # the PUBLIC half of the org's hub identity (never the
                    # secret) — lets listings mark a local org as also
                    # hub-reachable (transport sets, user spec 2026-08-05)
                    "net_slug": cast("dict[str, Any]",
                                     doc.get("net_identity") or {}).get("slug"),
                    "created": doc.get("created")})
    return out


def load_org(slug: str) -> Org:
    p = org_path(slug)
    if not os.path.exists(p):
        raise LedgerError(f"no such org: {slug!r}")
    # The read side of the same collision. Read-only endpoints deliberately
    # read OUTSIDE DOC_LOCK (№22), so under agent load this races every save:
    # on Windows, opening the doc while a replace is in flight raises
    # PermissionError [Errno 13] (~9.5% of reads at 1 reader / 1 writer,
    # measured). Live evidence: 3 of 123 turns on the message-visibility rig
    # had a `GET …/chat` poll come back HTTP 500 — the desk's own refresh
    # failing at random while an agent worked.
    #
    # `_read_bytes` handles both halves: the shared latch (so this read does
    # not starve a concurrent replace) and the retry (for a collision from
    # outside this process). It also SLURPS and parses afterwards — the open
    # handle is what blocks os.replace, and parsing a multi-MB doc inside it
    # held it open ~100× longer than the read, while the old code's comment
    # promised a "deterministic close".
    try:
        raw = _read_bytes(p)
    except FileNotFoundError:
        # deleted (or replaced) between the exists() check above and the
        # open — delete_org renames the doc out from under readers by design
        raise LedgerError(f"no such org: {slug!r}") from None
    return Org(json.loads(raw.decode("utf-8")))


REVISION: int = 0   # bumped on every save — cheap change detection for pollers
               # (the extern long-poll gates its full-doc rescans on this)

# G2 — "the doc changed" fanout, wired to the websocket hub at startup.
#
# It lives HERE, on the write itself, rather than at the endpoints, because the
# endpoint-side version was unenforceable: an audit of every route that calls
# save_org found 14 of 30 with no broadcast, so a second viewer never learned
# about a scope edit, an audience grant, a mail retraction or an inbox read.
# That was invisible in testing because the acting client refetches in its own
# callback — only a SECOND view (another tab, the kiosk, the switchboard beside
# a desk) ever saw the stale copy.
#
# Fixing the 14 by hand would have recreated the very shape this refactor is
# about: N writers each responsible for remembering the same side effect. A
# save IS the change, so the save announces it and a new endpoint cannot forget.
on_save: Callable[[str], None] = lambda slug: None   # no-op until wired
# additional per-save listeners a MODULE registers at import time (on_save is
# a single slot the API claims at startup; these compose instead of racing
# for it). First user: the supervisor's FR-01 remote-control reaper — any doc
# mutation that removes a controlled seat must take its server with it.
save_hooks: list[Callable[[str], None]] = []


def save_org(org: Org) -> None:
    global REVISION
    p = org_path(org.d["slug"])
    # serialise BEFORE creating the temp file: a doc carrying a
    # non-serialisable value used to raise halfway through json.dump and
    # strand a half-written .tmp in orgs/ for good.
    blob = json.dumps(org.d, indent=2).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            # os.replace is atomic against a CRASH, but only against a crash
            # of the process: NTFS may make the rename durable before the
            # data, so a power loss can leave a correctly-named doc full of
            # zeros. Costs 2.9 ms vs 0.9 ms on a 1.8 MB doc — nothing beside
            # an agent turn, and this file is the org.
            f.flush()
            os.fsync(f.fileno())
        # The latch (see _IOLatch) is what actually makes this land; the
        # retry stays for collisions no in-process latch can see — another
        # process, a scanner, a backup agent holding the doc open.
        for i in range(20):
            try:
                with _IO.exclusive():
                    os.replace(tmp, p)
                tmp = ""
                break
            except PermissionError:
                if i == 19:
                    raise
                time.sleep(0.01 * (i + 1))
    finally:
        if tmp:                       # every failure path cleans up after itself
            try:
                os.remove(tmp)
            except OSError:
                pass
    REVISION += 1  # pyright: ignore[reportConstantRedefinition]  # uppercase mutable counter is the public API; renaming is forbidden this wave
    # never let a fanout failure fail the write — the doc is already on disk
    try:
        on_save(org.d["slug"])
    except Exception:
        pass
    for h in list(save_hooks):
        try:
            h(org.d["slug"])
        except Exception:
            pass


def workspace_dir(slug: str) -> str:
    return os.path.join(DATA_ROOT, "workspaces", slug)


def create_org(name: str, extra_dirs: list[str] | None = None,
               permission_mode: str = "acceptEdits") -> Org:
    """Every org gets its own fresh workspace dir, minted here. Pre-existing
    directories are an ADVANCED grant (`extra_dirs`) — appended after the workspace
    in the org's default capability set."""
    slug = slugify(name)
    if os.path.exists(org_path(slug)):
        raise LedgerError(f"org {slug!r} already exists")
    ws = os.path.normpath(workspace_dir(slug))
    os.makedirs(ws, exist_ok=True)
    dirs = [ws] + [os.path.normpath(d) for d in (extra_dirs or []) if d.strip()]
    org = Org.create(name, dirs, permission_mode, workspace=ws)
    save_org(org)
    return org


def delete_org(slug: str) -> None:
    """Gap audit №16: one confirmed hover-click used to `os.remove` the whole
    org — structure, charters, mailboxes, event history. The motto reserves
    hard stops for protecting the user's data, so delete is now a RENAME into
    <data>/deleted/; putting the file back in orgs/ IS the restore."""
    p = org_path(slug)                      # validates the slug (see _safe_slug)
    trash = os.path.join(DATA_ROOT, "deleted")
    # Under DOC_LOCK like every other write: without it a load-modify-save
    # cycle already in flight re-creates the doc AFTER the rename and the org
    # comes back from the dead, half-populated and with no trash copy of the
    # final state.
    with DOC_LOCK:
        if not os.path.exists(p):
            raise LedgerError(f"no such org: {slug!r}")
        os.makedirs(trash, exist_ok=True)
        # ⚠ The stamp is SECOND-granular, and `os.replace` overwrites. Delete
        # → recreate → delete inside one second silently destroyed the first
        # backup — the exact loss №16 made delete-as-rename to prevent, just
        # moved one step along. Never overwrite anything in the trash: find a
        # free name, and only then rename.
        stamp = time.strftime("%Y%m%dT%H%M%S")
        dest = os.path.join(trash, f"{slug}-{stamp}.json")
        n = 0
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(trash, f"{slug}-{stamp}-{n}.json")
        os.replace(p, dest)


# ---------------------------------------------------- external peer sightings
# D-166. `@mcp:` is a PULL transport: a peer is visible ONLY when it reaches
# in — a poll, a wait, or a send. Nothing else on this machine knows whether
# one is alive, which is why a response handle attached for a peer that later
# died used to sit in an agent's system prompt for good, with the prompt still
# telling the agent to answer there. This file IS that missing signal; it
# exists for no other purpose.
#
# MACHINE-GLOBAL, not per-org, for three reasons: peer identity is
# machine-level (`~/.orgtree/extern-id`), one peer talks to several orgs at
# once, and `_extern_scan` already sweeps every org for it. Per-org would also
# mean taking DOC_LOCK on every 25-second poll of every listener.
#
# This file holds EVIDENCE ONLY — one `last_seen` per peer, meaning "it
# reached in at this time". It deliberately does NOT hold when a handle was
# attached: that is a fact about a NODE, it lives on the node
# (`external_handles_at`), and keeping it here could not distinguish a handle
# that had sat unused for a week from one re-attached a second ago.
_PEERS_FILE = "extern-peers.json"
_peers_lock = threading.Lock()
# a peer nobody has bound a handle to and nobody has heard from in this long
# is just clutter; pruned on write so the file cannot grow without bound
_PEER_FORGET_S = 90 * 86400


def _peers_path(root: str | None = None) -> str:
    return os.path.join(root or DATA_ROOT, _PEERS_FILE)


def _peers_read() -> dict[str, dict[str, Any]]:
    """Never raises: a corrupt or absent file reads as 'no sightings', which
    fails SAFE — every clock restarts, so the worst case is a detach delayed
    by one TTL, never a live handle dropped early."""
    try:
        with open(_peers_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _peers_write(d: dict[str, dict[str, Any]]) -> None:
    p = _peers_path()
    cut = time.time() - _PEER_FORGET_S
    keep = {k: v for k, v in d.items()
            if max(_epoch(v.get("last_seen")), _epoch(v.get("first_observed"))) > cut}
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(keep, f, indent=1)
        os.replace(tmp, p)
        tmp = ""
    finally:
        if tmp:
            with contextlib.suppress(OSError):
                os.remove(tmp)


def _epoch(iso: Any) -> float:
    """`ledger.now()` ISO-8601 → epoch seconds. That format is ALWAYS UTC with
    a trailing Z, so it is parsed as UTC explicitly rather than through
    `strptime`'s naive-local default — which would shift every reading by the
    machine's offset and, east of UTC, make a fresh sighting look hours old.
    0.0 for anything unparseable: reads as 'infinitely long ago', which is
    safe for the forget-prune and is never on its own enough to detach."""
    if not isinstance(iso, str) or not iso:
        return 0.0
    try:
        return datetime.strptime(iso.replace("Z", ""), "%Y-%m-%dT%H:%M:%S.%f")             .replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, OverflowError):
        return 0.0


def extern_seen(addr: str) -> None:
    """The peer reached in. Called from every inbound extern route — send,
    read and wait alike, because all three prove the same thing."""
    with _peers_lock:
        d = _peers_read()
        rec = d.setdefault(addr, {})
        rec["last_seen"] = _now_iso()
        _peers_write(d)


def extern_last_seen(addr: str) -> str | None:
    """The peer's last real sighting, or None if it has never reached in."""
    return _peers_read().get(addr, {}).get("last_seen")


def _now_iso() -> str:
    """One timestamp shape across the whole system — reuse the ledger's, so a
    sighting and a mail entry can never disagree about what time it is."""
    return _ledger_now()
