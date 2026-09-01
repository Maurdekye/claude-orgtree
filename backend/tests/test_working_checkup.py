"""Automatic stale-working checkups and cache-read fallback.

Run: python backend/tests/test_working_checkup.py

The clock is supplied to every scheduler pass. No sleep or provider process is
involved: the suite pins threshold admission, durable dedupe/reconciliation,
all conservative exclusions, failed-wake cooldown, and both live mode changes.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import tempfile
from typing import Any

ROOT = tempfile.mkdtemp(prefix="orgtree-working-checkup-")
os.environ["ORGTREE_DATA"] = ROOT
os.environ["HOME"] = os.path.join(ROOT, "home")
os.environ["USERPROFILE"] = os.path.join(ROOT, "home")
os.environ["ORGTREE_PORT"] = "7421"
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(ROOT, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import appsettings, store, supervisor as S  # noqa: E402
from orgtree.ledger import SYSTEM, USER                  # noqa: E402

PASS = FAIL = 0
BASE = 1_800_000_000.0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as e:  # noqa: BLE001
        FAIL += 1
        import traceback
        print(f"  FAIL   {label}: {e}")
        traceback.print_exc(limit=6)


def iso(ts: float) -> str:
    return (dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def fixture(name: str, activity: float = BASE):
    org = store.create_org(name)
    org.hire(USER, None, "haiku", 0, "agent")
    n = org.node("agent")
    n["last_status"] = {
        "status": "working", "summary": "still moving", "at": iso(activity)}
    n["working_activity_at"] = iso(activity)
    n["turns"] = [{"at": iso(activity), "cost": 0.0, "ms": 1,
                   "denials": 0}]
    store.save_org(org)
    return org.d["slug"], "agent"


def runtime_clear(slug: str, nid: str) -> None:
    with S._state_lock:
        S._state.pop((slug, nid), None)


def park(slug: str, nid: str = "agent") -> None:
    with store.DOC_LOCK:
        org = store.load_org(slug)
        if nid in org.nodes:
            org.node(nid)["last_status"] = {
                "status": "idle", "summary": "test complete", "at": iso(BASE)}
            org.node(nid).pop("working_activity_at", None)
            org.d.get("mail", {}).pop(nid, None)
            store.save_org(org)
    runtime_clear(slug, nid)


def accepted(calls: list[tuple[str, str, str]]):
    def wake(slug: str, nid: str, text: str) -> dict[str, Any]:
        calls.append((slug, nid, text))
        return {"accepted": True, "queued": 0}
    return wake


def threshold_and_internal_mail() -> None:
    slug, nid = fixture("zz-checkup-threshold")
    calls: list[tuple[str, str, str]] = []
    try:
        S._working_checkup_pass(
            accepted(calls), BASE + S.WORKING_CHECKUP_AFTER_S - 0.001,
            mode_enabled=True)
        assert calls == [], calls
        assert not store.load_org(slug).waking_mail(nid)

        S._working_checkup_pass(
            accepted(calls), BASE + S.WORKING_CHECKUP_AFTER_S,
            mode_enabled=True)
        assert [(s, n) for s, n, _ in calls] == [(slug, nid)], calls
        assert "automatic 30-minute" in calls[0][2].lower(), calls
        current = store.load_org(slug)
        mail = (current.d.get("mail") or {}).get(nid) or []
        assert len(mail) == 1, mail
        assert mail[0]["from"] == SYSTEM and mail[0]["kind"] == "message", mail
        assert "AUTOMATIC 30-MINUTE WORKING-STATUS CHECK" in mail[0]["body"]
        assert "orgtree_status" in mail[0]["body"]

        # The persisted mail + activity reservation make another scheduler
        # pass a no-op even though the injected wake did not start a thread.
        S._working_checkup_pass(
            accepted(calls), BASE + S.WORKING_CHECKUP_AFTER_S + 60,
            mode_enabled=True)
        assert len(calls) == 1, calls
    finally:
        park(slug)


check("fires exactly at 30 minutes, not early, with one @system ordinary mail",
      threshold_and_internal_mail)


def real_wake_resets_clock() -> None:
    slug, nid = fixture("zz-checkup-real-wake")
    calls: list[tuple[str, str, str]] = []
    try:
        S._note_working_activity(slug, nid, BASE + 600)
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.node(nid)["turns"][-1]["at"] = iso(BASE + 900)
            store.save_org(org)
        S._working_checkup_pass(
            accepted(calls), BASE + 600 + S.WORKING_CHECKUP_AFTER_S,
            mode_enabled=True)
        assert calls == [], calls
        S._working_checkup_pass(
            accepted(calls), BASE + 900 + S.WORKING_CHECKUP_AFTER_S,
            mode_enabled=True)
        assert [(s, n) for s, n, _ in calls] == [(slug, nid)], calls
    finally:
        park(slug)


check("real wake and later turn completion each reset the full 30-minute clock",
      real_wake_resets_clock)


def failed_turn_cools_down() -> None:
    slug, nid = fixture("zz-checkup-failed")
    calls: list[int] = []

    def failed_wake(s: str, n: str, text: str) -> dict[str, Any]:
        calls.append(len(calls) + 1)
        # Model a turn that consumed its mail but failed before reporting a
        # new status. The durable working row deliberately remains.
        with store.DOC_LOCK:
            org = store.load_org(s)
            org.take_mail(n)
            store.save_org(org)
        return {"accepted": True, "queued": 0}

    try:
        due = BASE + S.WORKING_CHECKUP_AFTER_S
        S._working_checkup_pass(failed_wake, due, mode_enabled=True)
        S._working_checkup_pass(failed_wake, due + 1, mode_enabled=True)
        assert len(calls) == 1, calls
        S._working_checkup_pass(
            failed_wake, due + S.WORKING_CHECKUP_AFTER_S,
            mode_enabled=True)
        assert len(calls) == 2, calls
    finally:
        park(slug)


check("a failed silent turn still gets a full cooldown", failed_turn_cools_down)


def nonworking_and_runtime_exclusions() -> None:
    slug, nid = fixture("zz-checkup-runtime-exclusions")
    calls: list[tuple[str, str, str]] = []
    due = BASE + S.WORKING_CHECKUP_AFTER_S
    try:
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.node(nid)["last_status"]["status"] = "idle"
            store.save_org(org)
        S._working_checkup_pass(accepted(calls), due, mode_enabled=True)
        assert calls == [], calls

        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.node(nid)["last_status"]["status"] = "working"
            store.save_org(org)
        for key, value in (
                ("busy", True), ("waiting", True),
                ("queue", ["real turn"]), ("responding", True)):
            runtime_clear(slug, nid)
            st = S.state(slug, nid)
            st[key] = value
            S._working_checkup_pass(accepted(calls), due, mode_enabled=True)
            assert calls == [], (key, calls)
        runtime_clear(slug, nid)
    finally:
        park(slug)


check("non-working, active, waiting, responding, and queued seats are excluded",
      nonworking_and_runtime_exclusions)


def durable_gate_exclusions() -> None:
    due = BASE + S.WORKING_CHECKUP_AFTER_S
    cases = (
        ("frozen", {"at": iso(BASE), "resume_texts": []}),
        ("limit_locked", True),
        ("remote_controlled", {"at": iso(BASE), "pid": 1}),
        ("inflight", {"at": iso(BASE), "text": "real turn"}),
    )
    for i, (key, value) in enumerate(cases):
        slug, nid = fixture(f"zz-checkup-durable-{i}")
        calls: list[tuple[str, str, str]] = []
        try:
            with store.DOC_LOCK:
                org = store.load_org(slug)
                org.node(nid)[key] = value
                if key == "limit_locked":
                    # A real node flag is backed by the org lock. The load
                    # migration deliberately clears orphaned flags.
                    org.d["fable_lock"] = {"no_reset": True}
                store.save_org(org)
            S._working_checkup_pass(accepted(calls), due, mode_enabled=True)
            assert calls == [], (key, calls)
        finally:
            park(slug)

    slug, nid = fixture("zz-checkup-waking-mail")
    calls = []
    try:
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.post_mail(USER, nid, "real mail is already waiting")
            store.save_org(org)
        S._working_checkup_pass(accepted(calls), due, mode_enabled=True)
        assert calls == [], calls
    finally:
        park(slug)


check("frozen/locked/remote/inflight and already-waking mail are excluded",
      durable_gate_exclusions)


def restart_persistence_and_reconciliation() -> None:
    slug, nid = fixture("zz-checkup-restart")
    calls: list[tuple[str, str, str]] = []
    real_send = S.send_message
    driven: list[tuple[str, str, str]] = []
    try:
        due = BASE + S.WORKING_CHECKUP_AFTER_S
        S._working_checkup_pass(accepted(calls), due, mode_enabled=True)
        assert len(calls) == 1
        runtime_clear(slug, nid)  # the entire process-local state was lost
        S._working_checkup_pass(
            accepted(calls), due + 1, mode_enabled=True)
        assert len(calls) == 1, calls

        S.send_message = lambda s, n, text, **kw: (                 # type: ignore[assignment]
            driven.append((s, n, text)), {"accepted": True})[1]
        S.reconcile(slug)
        assert [(s, n) for s, n, _ in driven] == [(slug, nid)], driven
        assert "waited across an orgtree restart" in driven[0][2]
    finally:
        S.send_message = real_send                                 # type: ignore[assignment]
        park(slug)

    # A legacy working row without either clock is seeded at restart/pass
    # time. Missing evidence never means "already overdue".
    slug, nid = fixture("zz-checkup-legacy")
    calls = []
    try:
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.node(nid)["last_status"].pop("at", None)
            org.node(nid).pop("working_activity_at", None)
            org.node(nid)["turns"] = []
            store.save_org(org)
        S._working_checkup_pass(accepted(calls), BASE, mode_enabled=True)
        assert calls == [], calls
        assert store.load_org(slug).node(nid).get("working_activity_at") == iso(BASE)
        S._working_checkup_pass(
            accepted(calls), BASE + S.WORKING_CHECKUP_AFTER_S,
            mode_enabled=True)
        assert len(calls) == 1, calls
    finally:
        park(slug)


check("reservation survives restart, reconcile drives it, and legacy rows seed safely",
      restart_persistence_and_reconciliation)


def idle_only_never_queues() -> None:
    slug, nid = fixture("zz-checkup-idle-only")
    try:
        st = S.state(slug, nid)
        st["busy"] = True
        before = list(st["queue"])
        result = S.send_message(
            slug, nid, S.WORKING_CHECKUP_NUDGE,
            mail_ping=True, idle_only=True)
        assert result.get("not_idle") is True, result
        assert st["queue"] == before, st["queue"]
    finally:
        park(slug)


check("idle-only ordinary delivery refuses instead of queuing behind a turn",
      idle_only_never_queues)


def mutually_exclusive_modes_and_transitions() -> None:
    appsettings.set_working_checkups_enabled(True)
    slug, nid = fixture("zz-checkup-mode-on")
    checkups: list[tuple[str, str]] = []
    caches: list[tuple[str, str]] = []
    real_due = S._working_cache_due
    try:
        S._working_cache_due = lambda org, node, now=None: (       # type: ignore[assignment]
            org.d["slug"] == slug and node == nid)

        def checkup_wake(s: str, n: str, text: str) -> dict[str, Any]:
            checkups.append((s, n))
            # Real send_message reserves busy before it returns.
            S.state(s, n)["busy"] = True
            return {"accepted": True, "queued": 0}

        S._working_lifecycle_keeper_pass(
            checkup_wake, lambda s, n: caches.append((s, n)),
            BASE + S.WORKING_CHECKUP_AFTER_S)
        assert checkups == [(slug, nid)] and caches == [], (checkups, caches)

        # enabled -> disabled while the checkup owns the seat: the fallback
        # cache read does not become a second scheduled job.
        appsettings.set_working_checkups_enabled(False)
        S._working_lifecycle_keeper_pass(
            checkup_wake, lambda s, n: caches.append((s, n)),
            BASE + S.WORKING_CHECKUP_AFTER_S + 1)
        assert caches == [], caches
    finally:
        S._working_cache_due = real_due                         # type: ignore[assignment]
        park(slug)

    # disabled -> enabled while a cache-read lease owns the seat is symmetric.
    slug, nid = fixture("zz-checkup-mode-off")
    checkups = []
    caches = []
    real_due = S._working_cache_due
    try:
        S._working_cache_due = lambda org, node, now=None: (       # type: ignore[assignment]
            org.d["slug"] == slug and node == nid)

        def cache_launch(s: str, n: str) -> None:
            caches.append((s, n))
            S.state(s, n)["cache_keepalive"] = {"lease": True}

        appsettings.set_working_checkups_enabled(False)
        S._working_lifecycle_keeper_pass(
            lambda s, n, text: {"accepted": True, "queued": 0}, cache_launch,
            BASE + S.WORKING_CHECKUP_AFTER_S)
        assert caches == [(slug, nid)], caches

        appsettings.set_working_checkups_enabled(True)
        S._working_lifecycle_keeper_pass(
            lambda s, n, text: (
                checkups.append((s, n)), {"accepted": True, "queued": 0})[1],
            cache_launch, BASE + S.WORKING_CHECKUP_AFTER_S + 1)
        assert checkups == [] and caches == [(slug, nid)], (checkups, caches)

        S.state(slug, nid).pop("cache_keepalive", None)
        S._working_lifecycle_keeper_pass(
            lambda s, n, text: (
                checkups.append((s, n)), {"accepted": True, "queued": 0})[1],
            cache_launch, BASE + S.WORKING_CHECKUP_AFTER_S + 1)
        assert checkups == [(slug, nid)] and caches == [(slug, nid)]
    finally:
        S._working_cache_due = real_due                         # type: ignore[assignment]
        appsettings.set_working_checkups_enabled(True)
        park(slug)


check("enabled chooses checkup, disabled chooses cache read, transitions never overlap",
      mutually_exclusive_modes_and_transitions)


shutil.rmtree(ROOT, ignore_errors=True)
print(f"\nALL {PASS} CHECKS PASS" if not FAIL else f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
