"""Persistence, concurrency and durability suite — store.py and disk.py, adversarially.

    An org document survives every way the process can be interrupted, every
    interleaving of readers and writers, and every hostile slug — or it says
    so loudly. It never half-exists, never silently loses a write, and
    nothing outside orgs/ is ever touched.

Run:  .venv/Scripts/python.exe backend/tests/test_persistence.py
      --quick        shorter concurrency runs (CI-ish; the default is ~35 s)
      --soak N       seconds per concurrency configuration (default 2.5)
      --only <sub>   run only sections whose name contains <sub>

No pytest (it is not installed here), no network, no model calls, no Docker.
Everything runs against a throwaway ORGTREE_DATA under the system temp dir.

WHY THIS FILE EXISTS
--------------------
store.py is 150 lines and carries the whole durability story: one JSON doc per
org, DOC_LOCK, atomic tmp+os.replace, delete-as-rename into <data>/deleted/.
Read-only endpoints deliberately read OUTSIDE the doc lock (№22), so on
Windows every read races every write at the filesystem level — and Windows
fails `os.replace` while ANY handle on the destination is open. Both sides
carried a retry-with-backoff written on the belief that this is a brief
collision. Measurement says otherwise, and §1 is the measurement.

THE HEADLINE MEASUREMENT (2026-08-04, this machine, before the fix)
-------------------------------------------------------------------
With 8 reader threads looping on `open()` and 1 writer:

    os.replace succeeded    0 / 1,659 attempts   (0.00 %)
    at 4 readers / 4 writers:  18 / 8,467        (0.21 %)

Not a race the writer sometimes loses — a starvation it essentially always
loses. A FILE_SHARE_DELETE read handle does not help (MoveFileEx opens the
target exclusively itself; check `share-delete handle does not rescue the
replace` below). The 20-step, 1.9 s backoff therefore only delays the raise:
under a 16-reader storm `save_org` raised PermissionError on 2 of 8 writes
and leaked a stray .tmp for each. §1 asserts the post-fix behaviour and the
pre-fix numbers are recorded here so a regression is recognisable.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

# --------------------------------------------------------------- child mode
# The crash tests re-exec THIS file as a child that saves in a tight loop and
# gets killed mid-save. Handled before anything else so the child never runs
# the suite.
if len(sys.argv) > 2 and sys.argv[1] == "--crash-child":
    os.environ["ORGTREE_DATA"] = sys.argv[2]
    from orgtree import store as _cs                      # noqa: E402

    _org = _cs.load_org("victim")
    _org.d["ballast"] = ["z" * 300 for _ in range(int(sys.argv[3]))]
    _org.d["marker"] = "CHILD"
    while True:
        _org.d["n"] = _org.d.get("n", 0) + 1
        _cs.save_org(_org)

# isolated data root BEFORE any orgtree import — store resolves ORGTREE_DATA
# at import time
_TMP = tempfile.mkdtemp(prefix="orgtree-persist-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")

from orgtree import store                                  # noqa: E402
from orgtree.ledger import USER, LedgerError, Org          # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
NOTES: list[str] = []
TIMINGS: list[tuple[str, str]] = []

_ARGS = sys.argv[1:]
QUICK = "--quick" in _ARGS
ONLY = (_ARGS[_ARGS.index("--only") + 1].lower()
        if "--only" in _ARGS and len(_ARGS) > _ARGS.index("--only") + 1 else "")
SOAK = float(_ARGS[_ARGS.index("--soak") + 1]) if "--soak" in _ARGS else (
    0.8 if QUICK else 2.5)

ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}
_SECTION = [""]


def section(name: str) -> bool:
    _SECTION[0] = name
    if ONLY and ONLY not in name.lower():
        return False
    print(f"\n{name}:")
    return True


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except BaseException:                              # noqa: BLE001
        FAIL.append((f"{_SECTION[0]} / {label}", traceback.format_exc()))
        print(f"  XX     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def eq(got, want, what="") -> None:
    assert got == want, f"{what}expected {want!r}, got {got!r}"


DEFECTS: list[tuple[str, str, str]] = []


def defect(label: str, owner: str, fn) -> None:
    """A reproduction of a defect OUTSIDE this suite's territory.

    store.py and disk.py defects are FIXED in place and asserted with
    `check`. Everything found in ledger.py / supervisor.py / api.py is
    reported instead — `fn` asserts the CORRECT behaviour, so it fails
    today, and the failure text is the reproduction. ⚑ If one ever PASSES
    the suite goes red on purpose: the bug was fixed and the check must be
    promoted to a real `check()`."""
    try:
        fn()
    except AssertionError as e:
        DEFECTS.append((label, owner, str(e)[:300]))
        print(f"  ⚑      {label}  [{owner}]")
        return
    except BaseException:                                  # noqa: BLE001
        FAIL.append((f"{_SECTION[0]} / {label} (defect repro)",
                     traceback.format_exc()))
        print(f"  XX     {label}  (repro itself broke)")
        return
    FAIL.append((f"{_SECTION[0]} / {label}",
                 "This DEFECT reproduction now PASSES — the bug in "
                 f"{owner} appears fixed. Promote it to check()."))
    print(f"  !!     {label}  — now passes, promote to check()")


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="persistence test hire")
    s.update(over)
    return s


def fresh(name: str, nodes: int = 0) -> Org:
    """A real org doc on disk, built through the real ledger."""
    try:
        store.delete_org(name)
    except LedgerError:
        pass
    org = store.create_org(name)
    if nodes:
        # a real 2-level tree via the real ledger: 128 reports per manager
        # keeps clear of max_children (256) at any node count
        org.d["max_top_grant"] = 0                 # 0 = uncapped (test_ledger)
        org.hire(USER, None, "opus", 2 * nodes + 200, "ceo")
        made = 0
        mgr = 0
        while made < nodes:
            batch = min(128, nodes - made)
            mgr += 1
            name_m = f"m{mgr}"
            if nodes > 128:
                org.hire("ceo", "ceo", "sonnet", batch + 8, name_m, **spec())
                parent = name_m
            else:
                parent = "ceo"
            for i in range(batch):
                org.hire(parent, parent, "haiku", 0, f"n{made + i}", **spec())
            made += batch
    store.save_org(org)
    return org


def orgs_dir() -> str:
    return os.path.join(store.DATA_ROOT, "orgs")


def strays() -> list[str]:
    return [f for f in os.listdir(orgs_dir()) if f.endswith(".tmp")]


def stamp(d: dict, tag: int) -> None:
    """Write a self-consistent triple. A reader that sees any of these three
    disagree has observed a TORN doc — which tmp+os.replace must make
    impossible, and which no amount of retrying could paper over."""
    payload = f"{tag}:" + "q" * (tag % 97)
    d["tag"] = tag
    d["tag_echo"] = tag
    d["payload"] = payload
    d["digest"] = hashlib.sha256(payload.encode()).hexdigest()


def torn(d: dict) -> str | None:
    if d.get("tag") != d.get("tag_echo"):
        return f"tag {d.get('tag')!r} != echo {d.get('tag_echo')!r}"
    p = d.get("payload")
    if p is not None and hashlib.sha256(str(p).encode()).hexdigest() != d.get("digest"):
        return "digest does not match payload"
    return None


# ===========================================================================
def s1_collision() -> None:
    """№1 — the read/write collision, both sides, hammered."""
    if not section("1. the read/write collision (Windows os.replace vs open)"):
        return

    # -- the mechanism, stated deterministically. These two checks are the
    # -- reason the fix is a latch and not a longer backoff.
    def mechanism_open_blocks_replace() -> None:
        d = tempfile.mkdtemp(dir=_TMP)
        p = os.path.join(d, "x.json")
        open(p, "wb").write(b"{}")
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        os.write(fd, b'{"a":2}')
        os.close(fd)
        with open(p, "rb"):
            try:
                os.replace(tmp, p)
                if os.name == "nt":
                    raise AssertionError("os.replace succeeded over an OPEN "
                                         "destination on Windows — the whole "
                                         "premise of the latch changed")
            except PermissionError as e:
                eq(getattr(e, "winerror", 5), 5, "winerror: ")

    def mechanism_share_delete_no_rescue() -> None:
        """The obvious fix — open the doc with FILE_SHARE_DELETE so the
        rename can proceed — DOES NOT WORK, because MoveFileEx opens the
        target itself without sharing. Recorded so nobody spends the
        afternoon on it twice."""
        if os.name != "nt":
            NOTES.append("share-delete probe skipped: not Windows")
            return
        import ctypes
        import msvcrt
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        cf = k32.CreateFileW
        cf.restype = ctypes.c_void_p
        cf.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p]
        d = tempfile.mkdtemp(dir=_TMP)
        p = os.path.join(d, "x.json")
        open(p, "wb").write(b"{}")
        h = cf(p, 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)  # SHARE_DELETE
        assert h != ctypes.c_void_p(-1).value, "CreateFileW failed"
        f = os.fdopen(msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY), "rb")
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        os.write(fd, b'{"a":2}')
        os.close(fd)
        try:
            os.replace(tmp, p)
            f.close()
            raise AssertionError("FILE_SHARE_DELETE now rescues os.replace — "
                                 "the latch could be replaced by a share-mode "
                                 "read; re-measure before doing it")
        except PermissionError:
            pass
        finally:
            f.close()

    check("an open read handle blocks os.replace (WinError 5)",
          mechanism_open_blocks_replace)
    check("share-delete handle does not rescue the replace",
          mechanism_share_delete_no_rescue)

    # -- the hammer
    def hammer(nread: int, nwrite: int, secs: float) -> dict:
        org = fresh(f"hammer-{nread}-{nwrite}")
        slug = org.d["slug"]
        org.d["ballast"] = ["b" * 200 for _ in range(1500)]   # ~330 KB
        stamp(org.d, 0)
        store.save_org(org)
        stop = threading.Event()
        lk = threading.Lock()
        st: collections.Counter[str] = collections.Counter()
        errs: list[str] = []
        torns: list[str] = []
        committed: dict[int, int] = {}      # writer id -> last tag it committed

        def reader() -> None:
            while not stop.is_set():
                try:
                    d = store.load_org(slug).d
                    t = torn(d)
                    if t:
                        with lk:
                            torns.append(t)
                    with lk:
                        st["read"] += 1
                except BaseException as e:                 # noqa: BLE001
                    with lk:
                        errs.append(f"R {type(e).__name__}: {e}"[:160])

        def writer(wid: int) -> None:
            i = 0
            while not stop.is_set():
                i += 1
                tag = wid * 1000000 + i
                try:
                    with store.DOC_LOCK:
                        o = store.load_org(slug)
                        stamp(o.d, tag)
                        o.d.setdefault("hits", {})[str(wid)] = i
                        store.save_org(o)
                    with lk:
                        st["write"] += 1
                        committed[wid] = i
                except BaseException as e:                 # noqa: BLE001
                    with lk:
                        errs.append(f"W {type(e).__name__}: {e}"[:160])

        ts = [threading.Thread(target=reader, daemon=True) for _ in range(nread)]
        ts += [threading.Thread(target=writer, args=(k,), daemon=True)
               for k in range(nwrite)]
        t0 = time.perf_counter()
        for t in ts:
            t.start()
        time.sleep(secs)
        stop.set()
        for t in ts:
            t.join(60)
        dt = time.perf_counter() - t0
        final = store.load_org(slug).d
        return {"reads": st["read"], "writes": st["write"], "errs": errs,
                "torn": torns, "secs": dt, "final": final,
                "committed": dict(committed), "strays": strays()}

    for nr, nw in ((16, 4), (4, 16), (8, 8)) if not QUICK else ((8, 4),):
        r = hammer(nr, nw, SOAK)
        rate = f"{r['reads']/r['secs']:.0f} r/s, {r['writes']/r['secs']:.0f} w/s"
        TIMINGS.append((f"hammer R={nr} W={nw}",
                        f"{r['reads']} reads + {r['writes']} writes in "
                        f"{r['secs']:.1f}s ({rate})"))
        lbl = f"R={nr:2d} W={nw:2d}"
        check(f"{lbl}  no error escapes either side "
              f"({r['reads']} reads, {r['writes']} writes)",
              lambda r=r: eq(r["errs"][:3], [], "errors: "))
        check(f"{lbl}  no torn read", lambda r=r: eq(r["torn"][:3], [], ""))
        # ⚠ the starvation gate. Pre-fix the total came out at EXACTLY one
        # commit per writer in every configuration (16R/4W, 4R/16W, 8R/8W):
        # the first save lands before the readers spin up, and no later one
        # ever does. Post-fix: 6–14× that. The floor is on the TOTAL, not
        # per writer — DOC_LOCK is a plain RLock with no fairness, so an
        # individual thread losing the lock lottery is expected and is not
        # the property under test.
        check(f"{lbl}  writers commit ≥4× their count in total — no "
              f"starvation (pre-fix: exactly {nw}, i.e. 1 each, forever)",
              lambda r=r, nw=nw: (
                  None if r["writes"] >= 4 * nw
                  else (_ for _ in ()).throw(AssertionError(
                      f"only {r['writes']} commits from {nw} writers in "
                      f"{r['secs']:.1f}s — the replace is being starved"))))
        check(f"{lbl}  no write is lost: the final doc carries every "
              f"writer's last commit",
              lambda r=r: eq({k: v for k, v in (r["final"].get("hits") or {}).items()},
                             {str(k): v for k, v in r["committed"].items()},
                             "hits: "))
        check(f"{lbl}  the surviving doc is self-consistent",
              lambda r=r: eq(torn(r["final"]), None, ""))
        check(f"{lbl}  no stray .tmp left behind",
              lambda r=r: eq(r["strays"], [], "strays: "))

    # -- the failure mode the fix removes, made explicit
    def replace_lands_under_read_load() -> None:
        """The pre-fix number was 0/1659. Assert a floor far above the noise:
        with the latch the writer must land essentially every time."""
        org = fresh("latchproof")
        slug = org.d["slug"]
        stop = threading.Event()
        for _ in range(8):
            threading.Thread(
                target=lambda: [store.load_org(slug) for _ in iter(
                    lambda: not stop.is_set(), False)], daemon=True).start()
        time.sleep(0.15)
        ok = err = 0
        for i in range(60):
            try:
                o = store.load_org(slug)
                o.d["i"] = i
                store.save_org(o)
                ok += 1
            except PermissionError:
                err += 1
        stop.set()
        time.sleep(0.05)
        TIMINGS.append(("replace under an 8-reader storm",
                        f"{ok}/{ok+err} landed (pre-fix: 0/1659 = 0.00%)"))
        assert err == 0, f"{err} of {ok+err} saves still raised PermissionError"

    check("os.replace lands under a sustained 8-reader storm "
          "(pre-fix: 0 of 1,659)", replace_lands_under_read_load)


# ===========================================================================
def s2_read_outside_lock() -> None:
    """№22 — read-only endpoints read OUTSIDE DOC_LOCK. Prove that is safe."""
    if not section("2. read-outside-the-lock is safe (№22)"):
        return
    org = fresh("outsidelock", nodes=12)
    slug = org.d["slug"]

    def no_instance_cache() -> None:
        """The property everything else here rests on: a reader gets its OWN
        object graph, parsed from bytes, never a live reference into another
        thread's document."""
        a = store.load_org(slug)
        b = store.load_org(slug)
        assert a.d is not b.d, "load_org returned a shared dict"
        assert a.nodes is not b.nodes
        a.d["scribble"] = 1
        assert "scribble" not in store.load_org(slug).d

    check("load_org returns an independent parse, never a shared object",
          no_instance_cache)

    def reader_never_sees_a_mid_mutation_doc() -> None:
        """A reader iterating a collection while a writer REMOVES from it.
        Because the reader owns its parse this can never raise
        `dictionary changed size during iteration` — and it must also never
        observe a node the writer only half-removed (nodes[] entry gone but
        the parent's child list still naming it)."""
        stop = threading.Event()
        bad: list[str] = []

        def churner() -> None:
            i = 0
            while not stop.is_set():
                i += 1
                try:
                    with store.DOC_LOCK:
                        o = store.load_org(slug)
                        if len(o.children("ceo")) > 2:
                            o.retire(USER, o.children("ceo")[-1])
                        else:
                            o.hire("ceo", "ceo", "haiku", 0, f"r{i}", **spec())
                        store.save_org(o)
                except LedgerError:
                    pass
                except BaseException as e:                 # noqa: BLE001
                    bad.append(f"W {type(e).__name__}: {e}"[:160])

        def scanner() -> None:
            while not stop.is_set():
                try:
                    o = store.load_org(slug)
                    # walk the tree the way a read-only endpoint does
                    for nid, n in o.nodes.items():
                        par = n.get("parent")
                        if par is not None and par not in o.nodes:
                            bad.append(f"node {nid} parents missing {par}")
                        for c in o.children(nid, live_only=False):
                            if c not in o.nodes:
                                bad.append(f"child {c} of {nid} not in nodes")
                    o.tree()
                    o.audit()
                except BaseException as e:                 # noqa: BLE001
                    bad.append(f"R {type(e).__name__}: {e}"[:160])

        ts = [threading.Thread(target=churner, daemon=True) for _ in range(3)]
        ts += [threading.Thread(target=scanner, daemon=True) for _ in range(6)]
        for t in ts:
            t.start()
        time.sleep(SOAK)
        stop.set()
        for t in ts:
            t.join(60)
        eq(sorted(set(bad))[:3], [], "")

    check("a reader mid-iteration never sees a structure a writer is "
          "removing from", reader_never_sees_a_mid_mutation_doc)

    def never_a_half_written_doc() -> None:
        """Directly: 6 readers parsing while 4 writers replace docs of wildly
        varying size. Every read must yield valid JSON with a matching
        digest; a torn or truncated read shows up as either."""
        o = fresh("halfwrite")
        s = o.d["slug"]
        stop = threading.Event()
        bad: list[str] = []
        n_ok = [0]

        def wr() -> None:
            i = 0
            while not stop.is_set():
                i += 1
                with store.DOC_LOCK:
                    x = store.load_org(s)
                    # size swings by 100× between saves: a partial write is
                    # far likelier to be observable than at a constant size
                    x.d["ballast"] = ["p" * 200] * (10 if i % 2 else 4000)
                    stamp(x.d, i)
                    store.save_org(x)

        def rd() -> None:
            while not stop.is_set():
                try:
                    d = store.load_org(s).d
                    t = torn(d)
                    if t:
                        bad.append(t)
                    n_ok[0] += 1
                except BaseException as e:                 # noqa: BLE001
                    bad.append(f"{type(e).__name__}: {e}"[:160])

        ts = [threading.Thread(target=wr, daemon=True) for _ in range(4)]
        ts += [threading.Thread(target=rd, daemon=True) for _ in range(6)]
        for t in ts:
            t.start()
        time.sleep(SOAK)
        stop.set()
        for t in ts:
            t.join(60)
        TIMINGS.append(("size-swinging read storm",
                        f"{n_ok[0]} reads across 100× doc-size swings"))
        eq(sorted(set(bad))[:3], [], "")

    check("a reader never observes a half-written doc under 100× size swings",
          never_a_half_written_doc)

    def revision_is_monotonic() -> None:
        """REVISION is the pollers' change signal — it must never go
        backwards or skip a save, or the extern long-poll misses a rescan."""
        o = fresh("revcount")
        before = store.REVISION
        for i in range(25):
            o.d["i"] = i
            store.save_org(o)
        eq(store.REVISION - before, 25, "REVISION delta: ")

    check("REVISION advances exactly once per save", revision_is_monotonic)

    def revision_under_threads() -> None:
        """`REVISION += 1` is a read-modify-write on a module global. Under
        the GIL a bare += on an int is not atomic across bytecodes, so a
        concurrent save could in principle lose a bump."""
        o = fresh("revrace")
        s = o.d["slug"]
        before = store.REVISION
        n, per = 8, 30

        def w() -> None:
            for _ in range(per):
                with store.DOC_LOCK:
                    store.save_org(store.load_org(s))

        ts = [threading.Thread(target=w) for _ in range(n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(120)
        eq(store.REVISION - before, n * per, "REVISION delta: ")

    check("REVISION does not lose bumps under 8 concurrent writers",
          revision_under_threads)

    def on_save_failure_never_fails_the_write() -> None:
        """The fanout hook is best-effort by contract — the doc is already on
        disk when it runs."""
        o = fresh("fanout")
        seen: list[str] = []
        prev = store.on_save
        try:
            store.on_save = lambda slug: (seen.append(slug),
                                          (_ for _ in ()).throw(RuntimeError("boom")))[0]
            o.d["v"] = 7
            store.save_org(o)                     # must not raise
        finally:
            store.on_save = prev
        eq(seen, [o.d["slug"]], "hook saw: ")
        eq(store.load_org(o.d["slug"]).d["v"], 7, "persisted value: ")

    check("a throwing on_save hook never fails the write",
          on_save_failure_never_fails_the_write)


# ===========================================================================
def s3_crash() -> None:
    """№3 — crash durability."""
    if not section("3. crash durability"):
        return

    def kill_mid_save(trials: int, ballast: int) -> dict:
        fresh("victim")
        o = store.load_org("victim")
        o.d["marker"] = "ORIGINAL"
        store.save_org(o)
        survived = corrupt = 0
        bad: list[str] = []
        for _ in range(trials):
            p = subprocess.Popen([sys.executable, os.path.abspath(__file__),
                                  "--crash-child", store.DATA_ROOT, str(ballast)],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            time.sleep(random.uniform(0.25, 0.75))
            p.kill()
            p.wait(30)
            try:
                d = store.load_org("victim").d
                assert d.get("slug") == "victim"
                assert d.get("marker") in ("ORIGINAL", "CHILD"), d.get("marker")
                survived += 1
            except BaseException as e:                     # noqa: BLE001
                corrupt += 1
                bad.append(f"{type(e).__name__}: {e}"[:160])
        return {"survived": survived, "corrupt": corrupt, "bad": bad,
                "strays": strays()}

    n = 3 if QUICK else 8
    res = kill_mid_save(n, 4000)
    orphan_bytes = sum(os.path.getsize(os.path.join(orgs_dir(), f))
                       for f in res["strays"])
    TIMINGS.append((f"kill mid-save ×{n}",
                    f"{res['survived']} survived, {res['corrupt']} corrupt, "
                    f"{len(res['strays'])} orphaned tmp "
                    f"({orphan_bytes/1048576:.1f} MB) awaiting the sweep "
                    f"(pre-fix: never swept at all)"))
    check(f"the org survives {n} SIGKILLs landing during a save",
          lambda: (eq(res["corrupt"], 0, "corrupt: "), eq(res["bad"][:2], []))[0])
    check("no partial file ever becomes the live doc "
          "(the doc parses and keeps its identity after every kill)",
          lambda: eq(res["survived"], n, "survived: "))

    def orphans_do_not_accumulate() -> None:
        """The end-to-end statement of "stray temp files do not accumulate":
        the orphans the kills above really left, aged past the grace window,
        must all be gone after one ordinary org-list call — and the live
        docs must be untouched."""
        assert res["strays"], "the kills left no orphan to reclaim‽"
        past = time.time() - store._TMP_GRACE - 60
        for f in res["strays"]:
            p = os.path.join(orgs_dir(), f)
            if os.path.exists(p):
                os.utime(p, (past, past))
        before = {f for f in os.listdir(orgs_dir()) if f.endswith(".json")}
        store.list_orgs()
        eq(strays(), [], "orphans after the sweep: ")
        eq({f for f in os.listdir(orgs_dir()) if f.endswith(".json")}, before,
           "live docs: ")
        eq(store.load_org("victim").d["slug"], "victim", "the org: ")

    check(f"the {len(res['strays'])} orphans those kills really left are all "
          f"reclaimed, live docs untouched", orphans_do_not_accumulate)

    def sweep_reclaims_orphans() -> None:
        """SIGKILL between mkstemp and os.replace strands a temp file no
        `finally` can reach. They used to accumulate forever (12 kills left 9
        orphans holding 11.9 MB beside a 1.8 MB doc). `_sweep_tmp` reclaims
        them, age-gated so it can never touch a save in flight."""
        # a fresh orphan (as a live save's tmp would look) must SURVIVE
        fd, young = tempfile.mkstemp(dir=orgs_dir(), suffix=".tmp")
        os.write(fd, b"in flight")
        os.close(fd)
        # an old one (as a crash leaves) must be reclaimed
        fd, old = tempfile.mkstemp(dir=orgs_dir(), suffix=".tmp")
        os.write(fd, b"orphan")
        os.close(fd)
        past = time.time() - store._TMP_GRACE - 60
        os.utime(old, (past, past))
        store.list_orgs()
        assert os.path.exists(young), "the sweep ate a save in flight"
        assert not os.path.exists(old), "the sweep left a stale orphan"
        os.remove(young)

    check("the tmp sweep reclaims stale orphans and never a save in flight",
          sweep_reclaims_orphans)

    def failed_serialisation_leaves_nothing() -> None:
        """A doc carrying a non-serialisable value used to raise halfway
        through json.dump and strand a half-written .tmp in orgs/ forever."""
        o = fresh("badvalue")
        o.d["v"] = "good"
        store.save_org(o)
        before = set(os.listdir(orgs_dir()))
        o.d["bad"] = {1, 2, 3}                     # a set is not JSON
        try:
            store.save_org(o)
            raise AssertionError("a set serialised‽")
        except TypeError:
            pass
        eq(set(os.listdir(orgs_dir())) - before, set(), "files added: ")
        eq(store.load_org("badvalue").d["v"], "good", "live doc: ")

    check("a save that cannot serialise leaves no temp file and no damage",
          failed_serialisation_leaves_nothing)

    def replace_failure_leaves_nothing() -> None:
        """And when the replace itself gives up (another PROCESS holding the
        doc open — the case no in-process latch can see), the temp file must
        still be cleaned up. Simulated by pinning os.replace to failure."""
        o = fresh("replacefail")
        before = set(os.listdir(orgs_dir()))
        real = os.replace

        def boom(a, b, **kw):                              # noqa: ANN001
            raise PermissionError(13, "Access is denied")

        prev_sleep = time.sleep
        try:
            os.replace = boom                              # type: ignore[assignment]
            time.sleep = lambda _s: None                   # don't burn 1.9 s
            try:
                store.save_org(o)
                raise AssertionError("save_org swallowed a total replace failure")
            except PermissionError:
                pass
        finally:
            os.replace = real                              # type: ignore[assignment]
            time.sleep = prev_sleep
        eq(set(os.listdir(orgs_dir())) - before, set(), "files added: ")
        eq(store.load_org("replacefail").d["slug"], "replacefail", "live doc: ")

    check("a save whose replace never lands cleans up its temp file "
          "and raises", replace_failure_leaves_nothing)

    def truncated_doc_is_not_silently_accepted() -> None:
        """If a doc DID get truncated (a pre-fsync power loss, a bad restore),
        load_org must fail loudly rather than hand back a half-org — and
        list_orgs must skip it rather than crash the whole org list."""
        o = fresh("truncated")
        raw = open(store.org_path("truncated"), "rb").read()
        open(store.org_path("truncated"), "wb").write(raw[:len(raw) // 2])
        try:
            store.load_org("truncated")
            raise AssertionError("a truncated doc loaded")
        except json.JSONDecodeError:
            pass
        names = [x["slug"] for x in store.list_orgs()]
        assert "truncated" not in names, names
        assert o.d["slug"] == "truncated"

    check("a truncated doc fails loudly and does not take the org list down",
          truncated_doc_is_not_silently_accepted)

    def zero_length_doc() -> None:
        """The classic post-power-loss shape: right name, zero bytes."""
        fresh("emptied")
        open(store.org_path("emptied"), "wb").close()
        try:
            store.load_org("emptied")
            raise AssertionError("an empty doc loaded")
        except json.JSONDecodeError:
            pass

    check("a zero-length doc fails loudly", zero_length_doc)

    def save_is_fsynced() -> None:
        """os.replace is atomic against a process crash, but NTFS may make
        the rename durable before the data — so the metadata-only version of
        this recipe can leave a correctly-named doc full of zeros after a
        power loss. Measured cost of the fsync: 2.9 ms vs 0.9 ms on a 1.8 MB
        doc."""
        seen: list[int] = []
        real = os.fsync

        def spy(fd: int) -> None:
            seen.append(fd)
            real(fd)

        o = fresh("fsynced")
        try:
            os.fsync = spy                                 # type: ignore[assignment]
            store.save_org(o)
        finally:
            os.fsync = real                                # type: ignore[assignment]
        assert seen, "save_org did not fsync the temp file before replacing it"

    check("save_org fsyncs the temp file before the replace", save_is_fsynced)


# ===========================================================================
def s4_delete_restore() -> None:
    """№4 and №16 — delete is a rename; putting the file back IS the restore."""
    if not section("4. delete / restore"):
        return
    trash = os.path.join(store.DATA_ROOT, "deleted")

    def round_trip() -> None:
        o = fresh("roundtrip", nodes=3)
        o.d["payload"] = "keep me"
        store.save_org(o)
        n_before = len(o.nodes)
        store.delete_org("roundtrip")
        try:
            store.load_org("roundtrip")
            raise AssertionError("a deleted org still loads")
        except LedgerError:
            pass
        cands = sorted(f for f in os.listdir(trash) if f.startswith("roundtrip-"))
        eq(len(cands), 1, "trash copies: ")
        os.replace(os.path.join(trash, cands[0]), store.org_path("roundtrip"))
        back = store.load_org("roundtrip")
        eq(back.d["payload"], "keep me", "payload: ")
        eq(len(back.nodes), n_before, "nodes: ")

    check("delete → put the file back → the org is byte-identical", round_trip)

    def nothing_is_destroyed() -> None:
        """The whole point of №16: delete must never `os.remove`. Assert the
        doc still exists somewhere afterwards."""
        o = fresh("nodestroy")
        o.d["sig"] = "unique-marker-9137"
        store.save_org(o)
        store.delete_org("nodestroy")
        found = [f for f in os.listdir(trash)
                 if "unique-marker-9137" in open(os.path.join(trash, f),
                                                 encoding="utf-8").read()]
        eq(len(found), 1, "trash copies carrying the marker: ")

    check("delete never destroys — the doc is intact in deleted/",
          nothing_is_destroyed)

    def same_second_collision() -> None:
        """The trash name is `<slug>-<YYYYmmddTHHMMSS>.json` and `os.replace`
        OVERWRITES. Delete → recreate → delete inside one second silently
        destroyed the first backup: exactly the loss №16 exists to prevent,
        moved one step along. (Reproduced 2026-08-04: the FIRST doc was gone.)"""
        for i in range(6):
            o = fresh("samesecond")
            o.d["gen"] = i
            store.save_org(o)
            store.delete_org("samesecond")
        cands = [f for f in os.listdir(trash) if f.startswith("samesecond-")]
        eq(len(cands), 6, "trash copies after 6 rapid deletes: ")
        gens = sorted(json.load(open(os.path.join(trash, f), encoding="utf-8"))["gen"]
                      for f in cands)
        eq(gens, [0, 1, 2, 3, 4, 5], "generations preserved: ")

    check("six deletes of the same slug inside one second keep six copies "
          "(was: five silently overwritten)", same_second_collision)

    def trash_never_clobbers_a_stranger() -> None:
        """A pre-existing file in deleted/ that happens to match the name a
        delete would mint must not be overwritten."""
        os.makedirs(trash, exist_ok=True)
        stamp_s = time.strftime("%Y%m%dT%H%M%S")
        squat = os.path.join(trash, f"squatter-{stamp_s}.json")
        open(squat, "w", encoding="utf-8").write('{"i":"was here first"}')
        fresh("squatter")
        store.delete_org("squatter")
        eq(json.load(open(squat, encoding="utf-8"))["i"], "was here first",
           "squatted file: ")

    check("delete never overwrites a file already in the trash",
          trash_never_clobbers_a_stranger)

    def delete_missing_refuses() -> None:
        try:
            store.delete_org("never-existed-at-all")
            raise AssertionError("deleting a missing org succeeded")
        except LedgerError as e:
            assert "no such org" in str(e).lower(), e

    check("deleting an org that does not exist refuses", delete_missing_refuses)

    # -- hostile slugs -----------------------------------------------------
    HOSTILE = [
        "..\\defaults", "../defaults", "..", ".", "../../etc/passwd",
        "C:/Windows/Temp/pwn", "C:\\Windows\\Temp\\pwn",
        "\\\\server\\share\\doc", "sub/dir", "sub\\dir", "",
        ".hidden", "-dash", "a" * 200, "with\0null",
        "trail ", " lead", "star*", "q?", "pipe|x", "quo\"te", "lt<gt>",
    ]
    # NOT hostile, despite the folk rule: measured on this build (Windows 11
    # 26200), `orgs/con.json` / `nul.json` / `com1.json` are ordinary files —
    # the reserved DEVICE names only bite without an extension. So an org
    # legitimately named "Con" is permitted, per the motto.

    def traversal_refused() -> None:
        """`/api/orgs/{slug}` hands the raw path segment to store. Starlette's
        converter is `[^/]+`, which on Windows still admits a BACKSLASH — so
        `DELETE /api/orgs/..%5Cdefaults` returned 200 and renamed
        `<data>/defaults.json` (the global org defaults) out of the way.
        Confirmed end-to-end through the real FastAPI app on 2026-08-04."""
        bad: list[str] = []
        for s in HOSTILE:
            for fn, name in ((store.org_path, "org_path"),
                             (store.load_org, "load_org"),
                             (store.delete_org, "delete_org")):
                try:
                    fn(s)                                  # type: ignore[operator]
                    bad.append(f"{name}({s!r}) was accepted")
                except LedgerError:
                    pass
                except BaseException as e:                 # noqa: BLE001
                    bad.append(f"{name}({s!r}) raised {type(e).__name__}: {e}"[:120])
        eq(bad[:4], [], "")

    check(f"all {len(HOSTILE)} hostile slugs are refused with a LedgerError "
          f"by org_path / load_org / delete_org", traversal_refused)

    def nothing_outside_orgs_is_touched() -> None:
        """The end-to-end statement: run every hostile slug through delete_org
        and assert not one file outside orgs/ moved."""
        root = store.DATA_ROOT
        victim = os.path.join(root, "defaults.json")
        open(victim, "w", encoding="utf-8").write('{"org_defaults":"important"}')
        sibling = os.path.join(os.path.dirname(root), "outside-the-root.json")
        open(sibling, "w", encoding="utf-8").write("{}")
        before = {p: os.path.getsize(p) for p in (victim, sibling)}
        for s in HOSTILE:
            try:
                store.delete_org(s)
            except (LedgerError, OSError):
                pass
        after = {p: (os.path.getsize(p) if os.path.exists(p) else None)
                 for p in (victim, sibling)}
        eq(after, before, "files outside orgs/: ")

    check("no file outside orgs/ is moved by any hostile slug",
          nothing_outside_orgs_is_touched)

    def legal_slugs_still_work() -> None:
        """The guard must not have narrowed the real charset: slugify emits
        [a-z0-9-], and the steer route's own regex admits '@' too."""
        for s in ("acme", "a", "a-b-c", "org2", "x9", "a@b", "team-42",
                  "a" * 100):
            p = store.org_path(s)
            assert os.path.dirname(p) == orgs_dir(), p

    check("legal slugs (incl. '@' and 100 chars) still resolve into orgs/",
          legal_slugs_still_work)

    def reserved_device_names_round_trip() -> None:
        """Guard against over-tightening: an org named "Con" must still work
        (the reserved-device rule needs a bare name, not `con.json`)."""
        for s in ("con", "nul", "com1", "prn", "aux"):
            o = fresh(s)
            o.d["v"] = s
            store.save_org(o)
            eq(store.load_org(s).d["v"], s, f"{s}: ")

    check("reserved Windows device names (con/nul/com1/…) round-trip as "
          "ordinary orgs", reserved_device_names_round_trip)

    def traversal_refused_over_http() -> None:
        """The end-to-end shape of the same defect, through the real app:
        before the fix `DELETE /api/orgs/..%5Cdefaults` returned 200 and
        moved `<data>/defaults.json`. Skipped (with a note) if the web stack
        is not importable — the store-level checks above are the gate."""
        try:
            from fastapi.testclient import TestClient
            from orgtree import api, sandbox, supervisor
        except Exception as e:                             # noqa: BLE001
            NOTES.append(f"HTTP traversal check skipped: {type(e).__name__}: {e}")
            return
        sandbox.remove = lambda slug: None                 # type: ignore[assignment]
        supervisor.chatq_deregister_org = lambda slug: None  # type: ignore[assignment]
        victim = os.path.join(store.DATA_ROOT, "defaults.json")
        open(victim, "w", encoding="utf-8").write('{"org_defaults":"important"}')
        c = TestClient(api.app)                            # no lifespan: no `with`
        codes = {}
        for path in ("..%5Cdefaults", "..%5C..%5Cdefaults", "..%2Fdefaults"):
            codes[path] = c.delete(f"/api/orgs/{path}").status_code
        assert os.path.exists(victim), \
            f"the global defaults were moved by a traversal delete: {codes}"
        # 404 = the store refused the slug; 405 = `%2F` decoded to a real
        # slash and the path never matched the route at all
        assert all(v in (404, 405) for v in codes.values()), codes

    check("DELETE /api/orgs/..%5Cdefaults is a 404 and moves nothing "
          "(was: 200, and it moved the global defaults)",
          traversal_refused_over_http)

    def delete_takes_the_doc_lock() -> None:
        """Without DOC_LOCK, a load-modify-save already in flight re-creates
        the doc AFTER the rename: the org returns from the dead with no trash
        copy of its final state. The check drives the race directly."""
        o = fresh("resurrect")
        s = o.d["slug"]
        started = threading.Event()
        release = threading.Event()
        done: list[str] = []

        def slow_writer() -> None:
            with store.DOC_LOCK:
                x = store.load_org(s)
                started.set()
                release.wait(10)
                x.d["late"] = True
                try:
                    store.save_org(x)
                    done.append("saved")
                except LedgerError:
                    done.append("refused")

        t = threading.Thread(target=slow_writer, daemon=True)
        t.start()
        started.wait(10)
        d = threading.Thread(target=lambda: store.delete_org(s), daemon=True)
        d.start()
        time.sleep(0.2)
        # delete_org must still be WAITING on DOC_LOCK, not already done
        assert d.is_alive(), ("delete_org completed while another thread held "
                              "DOC_LOCK — it does not take the lock")
        release.set()
        t.join(10)
        d.join(10)
        eq(done, ["saved"], "writer: ")
        try:
            store.load_org(s)
            raise AssertionError("the org came back from the dead")
        except LedgerError:
            pass

    check("delete_org serialises against an in-flight load-modify-save "
          "(no resurrection)", delete_takes_the_doc_lock)

    def create_after_delete() -> None:
        o = fresh("recycle")
        o.d["gen"] = 1
        store.save_org(o)
        store.delete_org("recycle")
        o2 = store.create_org("recycle")
        eq(o2.d.get("gen"), None, "a recycled slug must be a FRESH org: ")
        eq(store.load_org("recycle").d["slug"], "recycle")

    check("a slug can be recreated after delete and starts fresh",
          create_after_delete)

    def create_collision_refuses() -> None:
        fresh("dup")
        try:
            store.create_org("dup")
            raise AssertionError("duplicate org created")
        except LedgerError as e:
            assert "already exists" in str(e), e

    check("creating an org whose slug already exists refuses",
          create_collision_refuses)


# ===========================================================================
def s5_caps() -> None:
    """№5 — doc growth. The org doc is one JSON file rewritten in full on
    every op, so an uncapped list is not untidiness: it is a save cost that
    grows without bound, forever, for the life of the org."""
    if not section("5. document growth and caps"):
        return

    def mail_log_cap() -> None:
        o = fresh("caps-maillog", nodes=2)
        for i in range(250):
            o.post_mail(USER, "n0", f"message {i}")
        log = o.d["mail_log"]["n0"]
        eq(len(log), 100, "mail_log[n0] length: ")
        eq([m["body"] for m in log], [f"message {i}" for i in range(150, 250)],
           "the NEWEST 100, in order: ")
        eq(len(o.d["mail_log"].get("n1", [])), 0, "the cap is per-node: ")

    check("mail_log caps at exactly 100 per node, keeping the newest in "
          "order, and is per-node not global", mail_log_cap)

    def user_outbox_cap() -> None:
        """⚠ user_outbox is ONE FLAT LIST for the whole org, so a busy org
        loses its Sent history 100 messages after the last send — regardless
        of how many recipients that was spread over."""
        o = fresh("caps-outbox", nodes=3)
        for i in range(120):
            o.post_mail(USER, f"n{i % 3}", f"out {i}")
        out = o.d["user_outbox"]
        eq(len(out), 100, "user_outbox length: ")
        eq(out[0]["body"], "out 20", "oldest kept: ")
        eq(len({e["to"] for e in out}), 3, "flat across recipients: ")

    check("user_outbox caps at 100 — flat across the whole org, not per "
          "recipient", user_outbox_cap)

    def org_inbox_cap() -> None:
        o = fresh("caps-inbox", nodes=1)
        for i in range(260):
            o._org_inbox_log("in", "@ext:peer", f"inbound {i}")
        box = o.d["org_inbox"]
        eq(len(box), 200, "org_inbox length: ")
        eq(box[0]["body"], "inbound 60", "oldest kept: ")
        eq(box[-1]["body"], "inbound 259", "newest kept: ")

    check("org_inbox caps at exactly 200, newest kept", org_inbox_cap)

    def notice_log_cap() -> None:
        o = fresh("caps-notices", nodes=2)
        for i in range(900):
            o._notify(["n0"], f"notice {i}")
        eq(len(o.d["notice_log"]), 800, "notice_log length: ")
        eq(o.d["notice_log"][0]["text"], "notice 100", "oldest kept: ")

    check("the notice_log caps at exactly 800", notice_log_cap)

    def turns_ring_cap() -> None:
        """The per-node turn ring, driven through the real supervisor
        bookkeeping (`_after_turn`) rather than by imitating its slice."""
        from orgtree import supervisor
        o = fresh("caps-turns", nodes=1)
        st: dict = {"live": []}
        for i in range(40):
            supervisor._after_turn(o.d["slug"], "n0", store.load_org(o.d["slug"]),
                                   {"duration_ms": i, "total_cost_usd": 0.001},
                                   st, occ=0)
        ring = store.load_org(o.d["slug"]).node("n0")["turns"]
        eq(len(ring), 20, "turns ring length: ")
        eq([t["ms"] for t in ring], list(range(20, 40)), "newest 20, in order: ")

    check("the per-node turns ring caps at exactly 20 (real _after_turn)",
          turns_ring_cap)

    # ---- the accumulators with NO cap -----------------------------------
    def events_uncapped() -> None:
        """`events` is appended by `_log` on every single op and trimmed by
        nothing. Measured 192.6 bytes/event with save_org's own indent=2
        formatting: 10k ops ≈ 1.9 MB, 100k ≈ 19 MB — added to EVERY save and
        EVERY load of that org, forever. (`GET …/events` already pages with
        `since`, so the read side is fine; the write side is not.)"""
        o = fresh("caps-events", nodes=1)
        base = len(o.d["events"])
        for i in range(1200):
            o.post_mail(USER, "n0", f"m{i}")
        n = len(o.d["events"]) - base
        per = len(json.dumps(o.d["events"], indent=2)) / max(1, len(o.d["events"]))
        assert n < 1200, (
            f"events is UNCAPPED: 1,200 ops appended {n} entries with no "
            f"trim ({per:.0f} bytes each ⇒ ~{per * 100000 / 1e6:.0f} MB at "
            f"100k ops, rewritten on every save)")

    defect("events grows without bound — 1 entry per op, never trimmed",
           "ledger.py:1295 (_log)", events_uncapped)

    def live_mailbox_uncapped() -> None:
        """`post_mail` caps the mail_log SHADOW copy at 100 and leaves the
        live mailbox it was copied from untouched. api.py:2784 already knows
        (`pending[-800:]`) — but that is a response-serialisation backstop;
        the stored doc keeps everything. A node that never drains (archived,
        frozen, unrecoverable) accumulates forever."""
        o = fresh("caps-livebox", nodes=1)
        for i in range(1200):
            o.post_mail(USER, "n0", f"m{i}")
        live = len(o.d["mail"]["n0"])
        assert live <= 800, (
            f"the live mailbox is UNCAPPED: {live} entries queued while "
            f"mail_log[n0] holds steady at {len(o.d['mail_log']['n0'])}")

    defect("mail[node] (the live mailbox) is uncapped while its mail_log "
           "shadow is capped at 100", "ledger.py:886 (post_mail)",
           live_mailbox_uncapped)

    def notices_uncapped() -> None:
        """`notice_log` (the org-wide audit trail) is capped at 800; the
        per-node `notices` QUEUE it is derived from is not, and it is drained
        only at a turn boundary — so a node that never takes another turn
        holds every notice it was ever sent."""
        o = fresh("caps-notices2", nodes=1)
        for i in range(900):
            o._notify(["n0"], f"n{i}")
        q = len(o.d["notices"]["n0"])
        assert q <= 800, (
            f"notices[n0] is UNCAPPED: {q} queued (notice_log correctly "
            f"holds {len(o.d['notice_log'])}); nothing drains an archived "
            f"or unrecoverable node")

    defect("notices[node] is uncapped and drains only at a turn boundary",
           "ledger.py:1285 (_notify)", notices_uncapped)

    def inbox_read_cursor() -> None:
        """`org_inbox_read` is a COUNT (`= len(org_inbox)`), and unread is
        `len(org_inbox) − org_inbox_read`. But `org_inbox` trims from the
        FRONT at 200. Once traffic crosses the cap after a mark-read, the
        already-read entries fall off, the length snaps back to 200, and the
        stored count no longer means what it meant — brand-new messages are
        counted as read. Worst case below: a completely unread inbox reports
        zero unread."""
        o = fresh("caps-cursor", nodes=1)
        for i in range(200):
            o._org_inbox_log("in", "@ext:p", f"old {i}")
        o.org_inbox_mark_read()
        for i in range(200):
            o._org_inbox_log("in", "@ext:p", f"NEW {i}")
        unread = max(0, len(o.d["org_inbox"]) - int(o.d.get("org_inbox_read", 0)))
        fresh_n = sum(1 for e in o.d["org_inbox"] if e["body"].startswith("NEW"))
        eq(fresh_n, 200, "sanity — the whole window is new mail: ")
        assert unread == 200, (
            f"the org-inbox badge shows {unread} unread while all "
            f"{fresh_n} visible entries are unseen: org_inbox_read is an "
            f"index into a list that trims from the front")

    defect("org_inbox unread count collapses to 0 once the 200-cap trims "
           "past a mark-read", "ledger.py:997 (org_inbox_mark_read)",
           inbox_read_cursor)

    def hire_fanout_quadratic() -> None:
        """Each hire notifies every existing live sibling, so K sequential
        hires under one parent write K(K−1)/2 notices — into the uncapped
        per-node queues above. This is what makes a big org's doc grow
        faster than its node count."""
        o = fresh("caps-fanout")
        for i in range(60):
            o.hire(USER, None, "haiku", 0, f"t{i}")
        n = len(o.d.get("notice_log", []))
        assert n < 60 * 4, (
            f"60 sequential top-level hires wrote {n} notices "
            f"(= 60·59/2 = 1770 — quadratic in the sibling count)")

    defect("hire()'s peer-notify fanout is O(n²) in the sibling count",
           "ledger.py:1442 (hire)", hire_fanout_quadratic)

    # ---- large docs ------------------------------------------------------
    def big_doc_timings() -> None:
        """A megabyte-scale doc must still save and load, and the cost must
        be LINEAR in bytes — the doc is rewritten in full on every op, so a
        quadratic term would compound with the uncapped lists above."""
        rows = []
        sizes = (30, 120) if QUICK else (30, 120, 400, 1000)
        for nodes in sizes:
            o = fresh(f"big-{nodes}", nodes=nodes)
            for nid in list(o.nodes)[:nodes]:
                # every node's mail_log filled to its cap
                o.d.setdefault("mail_log", {})[nid] = [
                    {"id": f"{i:04d}", "from": USER, "kind": "message",
                     "at": "2026-08-04T00:00:00Z", "body": "x" * 400}
                    for i in range(100)]
            store.save_org(o)
            p = store.org_path(o.d["slug"])
            nbytes = os.path.getsize(p)
            t = []
            for _ in range(3):
                t0 = time.perf_counter()
                store.save_org(o)
                t1 = time.perf_counter()
                store.load_org(o.d["slug"])
                t.append((t1 - t0, time.perf_counter() - t1))
            save_ms = sorted(x[0] for x in t)[1] * 1000
            load_ms = sorted(x[1] for x in t)[1] * 1000
            mb = nbytes / 1048576
            rows.append((nodes, nbytes, save_ms, load_ms,
                         save_ms / mb, load_ms / mb))
        for nodes, nbytes, s, l, spm, lpm in rows:
            TIMINGS.append((f"doc {nodes} nodes",
                            f"{nbytes/1048576:6.2f} MB  save {s:7.1f} ms  "
                            f"load {l:7.1f} ms   ({spm:5.1f} ms/MB save, "
                            f"{lpm:5.1f} ms/MB load)"))
        # the shape assertion: ms/MB must not run away with size
        worst = max(r[4] for r in rows) / max(1e-9, min(r[4] for r in rows))
        assert worst < 3.0, (
            f"save cost per MB varied {worst:.1f}× across "
            f"{[r[0] for r in rows]} nodes — that is not linear: "
            f"{[(r[0], round(r[4], 1)) for r in rows]}")
        assert rows[-1][2] < 20000, f"the largest save took {rows[-1][2]:.0f} ms"

    check("a multi-MB doc saves and loads, and the cost stays linear in "
          "bytes (no quadratic blow-up)", big_doc_timings)


_FIND_OUT = "\n".join([
    "F10@./root.txt",
    "F999@./.orgtree-disk",              # the SENTINEL: must vanish entirely
    "find: unrecognized option -- 'q'",  # busybox exits 0 on a bad flag and
    "Fabc@./garbage-size.txt",           # prints help — the parse is the gate
    "F100@./b/f1.txt",
    "F5@./b/sub/f2.txt",
    "F7@./dir with space/file one.txt",
    "F3@./weird@name/file.txt",          # '@' is the field separator
    "F1@./b/sub/deep/deeper/deepest.txt",
    "D@./a",                             # an empty directory
    "D@./b", "D@./b/sub", "D@./b/sub/deep", "D@./b/sub/deep/deeper",
    "D@./dir with space", "D@./weird@name",
])


class _CP:
    """Stands in for subprocess.CompletedProcess."""

    def __init__(self, rc: int, out: str = "") -> None:
        self.returncode, self.stdout, self.stderr = rc, out, ""


class _FakeSh:
    """Stands in for disk._sh, dispatching on the substrings disk.py's own
    comments name as the fragile bits: the SENTINEL probe, `df -k`, the two
    `find` verbs, and the `sort -rn` paging pipeline."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.mounted = True
        self.df = ""
        self.df_rc = 0
        self.find = ""
        self.find_rc = 0
        self.enum: list[str] = []

    def __call__(self, script: str, timeout: int = 60) -> _CP:
        self.calls.append(script)
        if "test -f" in script and "orgtree-disk" in script.rsplit("/", 1)[-1]:
            return _CP(0 if self.mounted else 1)
        if "df -k" in script:
            return _CP(self.df_rc, self.df)
        if "find . -type f" in script and "find . -type d" in script:
            return _CP(self.find_rc, self.find)
        if "sort -rn" in script:
            import re as _re
            m = _re.search(r"tail -n \+(\d+) \| head -(\d+)", script)
            assert m, f"unexpected paging pipeline: {script!r}"
            start, lim = int(m.group(1)), int(m.group(2))
            return _CP(0, "\n".join(self.enum[start - 1:start - 1 + lim]))
        raise AssertionError(f"unexpected script: {script!r}")


def s6_disk() -> None:
    """№6 — disk.py's accounting arithmetic, with the WSL boundary faked.
    Everything below `_sh` is pure python and fully testable; nothing that
    genuinely needs Docker is attempted.

    NOT covered here, and deliberately so — these are real dd / mkfs.ext4 /
    losetup / resize2fs / e2fsck / docker-volume invocations with no
    meaningful pure-python part: create(), mount(), unmount(), destroy(),
    shrink_image(), grow()'s resize pipeline, and _data_root()'s probe. They
    stay in the live-drill column (ARCHITECTURE.md "Testing reality")."""
    if not section("6. disk.py accounting (hermetic — no Docker, no WSL)"):
        return
    from orgtree import disk

    real_sh, real_run = disk._sh, disk._run
    fake = _FakeSh()
    disk._sh = fake                                        # type: ignore[assignment]
    disk._distro_cache = "docker-desktop"                  # skip WSL detection
    disk._dataroot_cache = "/var/lib/docker/volumes"

    def reset(slug: str | None = None) -> None:
        if slug is None:
            disk._usage_cache.clear()
            disk._tree_cache.clear()
        else:
            disk._usage_cache.pop(slug, None)
            disk._tree_cache.pop(slug, None)

    def refuses(fn, needle: str) -> None:
        try:
            fn()
        except disk.DiskError as e:
            assert needle.lower() in str(e).lower(), f"wrong message: {e}"
            return
        raise AssertionError(f"expected DiskError containing {needle!r}")

    try:
        # ---------------------------------------------------------- usage
        def usage_arithmetic() -> None:
            fake.df = ("/dev/loop0    2048000   512000   1536000  25% "
                       "/mnt/wsl/orgtree-disk/u1")
            reset("u1")
            eq(disk.usage("u1"), (512000 * 1024, 2048000 * 1024), "usage: ")
            # a mount path with spaces: the regex never captures that field
            fake.df = ("/dev/loop0    2048000   512000   1536000  25% "
                       "/mnt/wsl/orgtree-disk/my org name")
            reset("u1")
            eq(disk.usage("u1"), (512000 * 1024, 2048000 * 1024), "spaces: ")

        check("usage(): df -k columns → (used, total) bytes, ×1024 both, "
              "space-bearing mount path parsed", usage_arithmetic)

        def usage_rejects_garbage() -> None:
            """busybox `df` exits 0 on an unknown flag and prints its help
            instead — so the RETURN CODE proves nothing and only the parse
            can. Every one of these must give None, never a wrong number."""
            for out in ("BusyBox v1.37.0\nUsage: df [-Pkmhai]...", "", "   ",
                        "/dev/loop0 notanumber alsonot 5% /mnt",
                        "/dev/loop0 2048000", "\n\n"):
                fake.df = out
                reset("u2")
                eq(disk.usage("u2"), None, f"df {out[:20]!r}: ")

        check("usage(): help text / short / non-numeric df output → None, "
              "never a wrong number", usage_rejects_garbage)

        def usage_zero_and_unmounted() -> None:
            fake.df = "/dev/loop0   0   0   0   0% /mnt/wsl/orgtree-disk/u3"
            reset("u3")
            eq(disk.usage("u3"), (0, 0), "empty disk: ")
            fake.mounted = False
            reset("u3")
            eq(disk.usage("u3"), None, "unmounted: ")
            fake.mounted = True

        check("usage(): total=0 parses to (0,0); an unmounted disk is None",
              usage_zero_and_unmounted)

        def usage_cache_ttl() -> None:
            fake.df = "/dev/loop0 1000 400 600 40% /mnt/wsl/orgtree-disk/u4"
            reset("u4")
            eq(disk.usage("u4"), (400 * 1024, 1000 * 1024))
            n = len(fake.calls)
            fake.df = "/dev/loop0 1000 900 100 90% /mnt/wsl/orgtree-disk/u4"
            eq(disk.usage("u4"), (400 * 1024, 1000 * 1024), "cached: ")
            eq(len(fake.calls), n, "calls while cached: ")
            # max_age=0.0 is how the storage tiers force a fresh read
            eq(disk.usage("u4", max_age=0.0), (900 * 1024, 1000 * 1024),
               "max_age=0: ")

        check("usage(): the TTL cache serves stale values and max_age=0.0 "
              "forces a re-read", usage_cache_ttl)

        def invalidate_clears_both() -> None:
            fake.df = "/dev/loop0 1000 400 600 40% /mnt/wsl/orgtree-disk/u5"
            fake.find = _FIND_OUT
            reset()
            disk.usage("u5")
            disk._dir_children("u5")
            assert "u5" in disk._usage_cache and "u5" in disk._tree_cache
            disk.invalidate("u5")
            assert "u5" not in disk._usage_cache, "usage cache survived"
            assert "u5" not in disk._tree_cache, "tree cache survived"

        check("invalidate() drops BOTH the usage and the tree cache "
              "(the readout must move immediately after a delete)",
              invalidate_clears_both)

        # -------------------------------------------------- the tree walk
        fake.find = _FIND_OUT
        reset("t1")
        kids = disk._dir_children("t1")

        check("_dir_children(): the root level rolls up exactly — empty dir "
              "present, SENTINEL and garbage lines gone",
              lambda: eq(kids[""], {"root.txt": (False, 10, 1),
                                    "b": (True, 106, 3),
                                    "dir with space": (True, 7, 1),
                                    "weird@name": (True, 3, 1),
                                    "a": (True, 0, 0)}, "root: "))
        check("_dir_children(): sizes roll up every ancestor, four levels "
              "deep", lambda: (
                  eq(kids["b"], {"f1.txt": (False, 100, 1), "sub": (True, 6, 2)}),
                  eq(kids["b/sub"], {"f2.txt": (False, 5, 1),
                                     "deep": (True, 1, 1)}),
                  eq(kids["b/sub/deep"], {"deeper": (True, 1, 1)}),
                  eq(kids["b/sub/deep/deeper"],
                     {"deepest.txt": (False, 1, 1)}))[0])
        check("_dir_children(): spaces in a path, and '@' inside BOTH a dir "
              "name and a file name, survive the '@'-delimited parse",
              lambda: (eq(kids["dir with space"], {"file one.txt": (False, 7, 1)}),
                       eq(kids["weird@name"], {"file.txt": (False, 3, 1)}))[0])
        check("_dir_children(): the 999-byte SENTINEL contributes zero bytes "
              "and zero count anywhere (root totals sum to exactly 126)",
              lambda: eq(sum(v[1] for v in kids[""].values()), 126, "bytes: "))
        check("_dir_children(): an empty dir gets its own empty children map",
              lambda: eq(kids.get("a"), {}, "a: "))

        def tree_failure_modes() -> None:
            fake.mounted = False
            reset("t1")
            refuses(lambda: disk._dir_children("t1"), "not mounted")
            fake.mounted = True
            fake.find_rc = 1
            reset("t1")
            refuses(lambda: disk._dir_children("t1"), "tree walk failed")
            fake.find_rc = 0
            fake.find = ""
            reset("t1")
            eq(disk._dir_children("t1"), {"": {}}, "empty disk: ")
            fake.find = _FIND_OUT
            reset("t1")

        check("_dir_children(): unmounted and walk-failure both raise; an "
              "empty disk is an empty root, not a crash", tree_failure_modes)

        # ------------------------------------------------------- list_dir
        def list_dir_order() -> None:
            reset("t1")
            got = disk.list_dir("t1")
            eq([e["name"] for e in got],
               ["b", "root.txt", "dir with space", "weird@name", "a"],
               "size DESC, dirs and files intermixed: ")
            eq(got[0], {"name": "b", "path": "b", "dir": True,
                        "bytes": 106, "files": 3}, "entry shape: ")
            eq([e["path"] for e in disk.list_dir("t1", "b")],
               ["b/f1.txt", "b/sub"], "child paths are parent-prefixed: ")
            refuses(lambda: disk.list_dir("t1", "no/such/dir"),
                    "no such directory")

        check("list_dir(): ONE level, dirs and files intermixed by size DESC "
              "then name; unknown rel raises", list_dir_order)

        def subtree() -> None:
            reset("t1")
            eq(sorted(disk.subtree_files("t1", "b")),
               [("b/f1.txt", 100), ("b/sub/deep/deeper/deepest.txt", 1),
                ("b/sub/f2.txt", 5)], "under b: ")
            eq(sum(s for _p, s in disk.subtree_files("t1", "")), 126,
               "whole disk (sentinel excluded): ")
            # ⚠ asymmetry with list_dir, which RAISES for the same input.
            # Defensible for its "recursive delete classification" purpose —
            # nothing to delete under an unknown path — but it is a real
            # inconsistency in the module's error contract.
            eq(disk.subtree_files("t1", "no/such"), [], "unknown rel: ")

        check("subtree_files(): every file under a dir from the cached walk; "
              "unknown rel returns [] (list_dir raises — noted asymmetry)",
              subtree)

        # ------------------------------------------- enumerate_by_size
        fake.enum = [f"{1300 - 100 * i}@./f{i:02d}.txt" for i in range(1, 13)]

        def page(offset: int, limit: int) -> list[str]:
            return [str(e["path"])
                    for e in disk.enumerate_by_size("e1", limit=limit,
                                                    offset=offset)]

        def paging_arithmetic() -> None:
            eq(page(0, 5), [f"f{i:02d}.txt" for i in range(1, 6)], "page 1: ")
            eq(page(3, 4), [f"f{i:02d}.txt" for i in range(4, 8)], "mid: ")
            eq(page(0, 20), [f"f{i:02d}.txt" for i in range(1, 13)],
               "limit past the end: ")

        check("enumerate_by_size(): well-behaved pages are exact slices",
              paging_arithmetic)

        def paging_past_the_end() -> None:
            """The defect. `head -(offset+limit) | tail -limit` reads
            correctly and is WRONG: `head` clamps to the true line count once
            it overshoots, and `tail` then measures its window from the
            clamped end rather than from `offset`. Measured on the real
            busybox 1.37.0 in the docker-desktop distro (12 items):

                offset=8  limit=10  →  f03…f12   (10 rows, SIX duplicated
                                                  from the previous page)
                offset=12 limit=5   →  f08…f12   (a full page past the end)

            Both land exactly where a "load more" button in the recovery
            browser naturally goes — the end of a long listing."""
            eq(page(8, 10), ["f09.txt", "f10.txt", "f11.txt", "f12.txt"],
               "last partial page (was: 10 rows from f03): ")
            eq(page(12, 5), [], "offset == total (was: f08…f12): ")
            eq(page(15, 5), [], "offset past the end (was: f08…f12): ")

        check("enumerate_by_size(): paging past the end returns the right "
              "short page and then nothing — no duplicates, no stale page",
              paging_past_the_end)

        def enum_parsing() -> None:
            fake.enum = ["10@./a.txt", "999@./.orgtree-disk",
                         "notanumber@./b.txt", "no-separator-here",
                         "5@./has@at.txt", "7@./with space.txt", ""]
            eq(page(0, 20), ["a.txt", "has@at.txt", "with space.txt"],
               "sentinel + garbage dropped, '@' and spaces kept: ")
            fake.mounted = False
            refuses(lambda: disk.enumerate_by_size("e1"), "not mounted")
            fake.mounted = True

        check("enumerate_by_size(): SENTINEL and malformed lines dropped, "
              "'@' and spaces in names preserved, unmounted raises",
              enum_parsing)

        # -------------------------------------------------- path helpers
        def path_helpers() -> None:
            eq(disk.disk_volume("acme"), "orgtree-disk-acme")
            eq(disk.mount_path("acme"), "/mnt/wsl/orgtree-disk/acme")
            eq(disk._img_path("acme"),
               "/var/lib/docker/volumes/orgtree-disk-acme/_data/disk.img")
            eq(disk.windows_path("acme"),
               r"\\wsl.localhost\docker-desktop\mnt\wsl\orgtree-disk\acme")
            eq(disk.windows_sub("acme", "workspace"),
               os.path.join(disk.windows_path("acme"), "workspace"))
            # ⚠ no quoting anywhere: every helper interpolates the slug into
            # a shell line. store._safe_slug is what keeps a slug shell-safe;
            # if that guard is ever loosened, these become injection points.
            eq(disk.mount_path("a b"), "/mnt/wsl/orgtree-disk/a b")

        check("path helpers compose the volume / mount / image / UNC paths "
              "consistently", path_helpers)

        def size_guards() -> None:
            refuses(lambda: disk.create("x", 15), "at least 16 mb")
            refuses(lambda: disk.create("x", 0), "at least 16 mb")
            refuses(lambda: disk.create("x", -100), "at least 16 mb")
            prev = disk.usage
            try:
                disk.usage = lambda slug, max_age=15.0: (             # type: ignore[assignment]
                    500 * 1048576, 1024 * 1048576)
                refuses(lambda: disk.grow("x", 512), "cannot shrink")
                refuses(lambda: disk.grow("x", 1023), "cannot shrink")
            finally:
                disk.usage = prev                                     # type: ignore[assignment]

        check("create() refuses under 16 MB; grow() refuses any target below "
              "the CURRENT disk size (shrink is the offline path)",
              size_guards)

        # -------------------------------------------------------- distro
        def distro_parsing() -> None:
            calls: list[list[str]] = []

            def with_out(out: str, rc: int = 0):
                disk._distro_cache = None
                calls.clear()

                def fr(args, timeout=60):                  # noqa: ANN001
                    calls.append(args)
                    return _CP(rc, out)
                disk._run = fr                             # type: ignore[assignment]

            def u16(s: str) -> str:      # wsl.exe's UTF-16 through text mode
                return "".join(c + "\x00" for c in s)

            with_out("", rc=1)
            refuses(disk.distro, "wsl is unavailable")
            # the exact name wins even with a '-data' sibling listed
            with_out(u16("docker-desktop-data") + "\r\n"
                     + u16("docker-desktop") + "\r\n")
            eq(disk.distro(), "docker-desktop", "exact match: ")
            with_out(u16("Ubuntu") + "\r\n" + u16("*docker-desktop") + "\r\n")
            eq(disk.distro(), "docker-desktop", "'*' marker stripped: ")
            with_out(u16("Ubuntu") + "\r\n" + u16("docker-desktop-2") + "\r\n")
            eq(disk.distro(), "docker-desktop-2", "substring fallback: ")
            with_out(u16("docker-desktop-data") + "\r\n")
            refuses(disk.distro, "no docker-desktop wsl distro")
            with_out("   \r\n\r\n  \n")
            refuses(disk.distro, "have: []")
            with_out(u16("docker-desktop") + "\r\n")
            disk.distro()
            disk.distro()
            eq(len(calls), 1, "_distro_cache: calls for two lookups: ")

        check("distro(): UTF-16 NULs stripped, '*' default marker stripped, "
              "'-data' siblings excluded, failures loud, result cached",
              distro_parsing)
    finally:
        disk._sh = real_sh                                 # type: ignore[assignment]
        disk._run = real_run                               # type: ignore[assignment]
        disk._distro_cache = None
        disk._dataroot_cache = None
        disk._usage_cache.clear()
        disk._tree_cache.clear()


# ===========================================================================
def main() -> None:
    t0 = time.perf_counter()
    print(f"data root: {store.DATA_ROOT}")
    print(f"platform: {sys.platform}  soak: {SOAK}s/config"
          f"{'  (quick)' if QUICK else ''}")
    for fn in (s1_collision, s2_read_outside_lock, s3_crash, s4_delete_restore,
               s5_caps, s6_disk):
        fn()
    dt = time.perf_counter() - t0

    if TIMINGS:
        print("\nmeasurements:")
        w = max(len(a) for a, _ in TIMINGS)
        for a, b in TIMINGS:
            print(f"  {a:<{w}}  {b}")
    if DEFECTS:
        print("\n⚑ DEFECTS REPRODUCED — real bugs OUTSIDE this suite's "
              "territory (store.py and disk.py fixes are asserted as normal\n"
              "  checks above). Each line is a live reproduction: it asserts "
              "the correct behaviour and fails today. Fix one and this suite\n"
              "  goes RED until the check is promoted.")
        for label, owner, why in DEFECTS:
            print(f"\n  ⚑ {label}\n      in: {owner}\n      {why}")
    if NOTES:
        print("\nnotes:")
        for n in NOTES:
            print(f"  · {n}")
    print()
    if FAIL:
        print("=" * 72)
        for label, tb in FAIL:
            print(f"\nFAILED: {label}\n{tb}")
        print("=" * 72)
        print(f"{PASS} checks passed, {len(FAIL)} FAILED  ({dt:.1f}s)")
        print(f"\n{len(FAIL)} CHECKS FAILED")
        sys.exit(1)
    print(f"ALL {PASS} CHECKS PASS  ({dt:.1f}s)"
          + (f"  ·  {len(DEFECTS)} defect(s) reproduced, see above"
             if DEFECTS else ""))


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
