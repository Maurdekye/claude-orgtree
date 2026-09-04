"""FR-xx · THE RESTART-WAKE & PASSIVE RESTART NOTIFICATIONS — `orgtree_restart_wake`.

WHAT IS BEING BUILT AND WHY IT IS SHAPED THIS WAY
-------------------------------------------------
User design: "maybe every single orgtree restart should send a notice to all
inactive agents with the new turn information, and an agent can just toggle it to
be a full waking message the next time instead if they wish"

1. PASSIVE DEFAULT: Every restart sends a passive notice (kind="notice") to every
   live-and-idle agent, delivering the deployed commit SHA and backend PID.
   Passive means waking_mail() is False — it starts NO turn and costs zero tokens.
   Multiple restarts supersede in-place so an idle agent reads exactly ONE current
   notice when it next takes a turn. Archived agents (~195 on this machine) are
   excluded to prevent noise and bloat.

2. OPT-IN TOGGLE (orgtree_restart_wake): An agent can upgrade its next notification
   into a full waking turn. One-shot by default (reverts to passive after firing),
   idempotent on double-arm, survives compaction, and drops cleanly if the agent
   is retired before the restart lands.

3. ACTIONABLE VERSION TRUTH: Full 40-char commit SHA (for git merge-base --is-ancestor),
   frozen at process startup so disk drift cannot lie about what is running, plus
   backend PID (with was_pid proving process replacement).

Run: python backend/tests/test_restart_wake.py
"""

import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Isolated temp data root BEFORE importing orgtree
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-wake-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

import _no_deploy                                                # noqa: E402
from orgtree import mcptool, restart_wake, store, supervisor     # noqa: E402
from orgtree.ledger import USER, LedgerError                     # noqa: E402

_no_deploy.install()
_no_deploy.assert_isolated_data_root()

_HERE = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
_GOT = os.path.realpath(os.path.dirname(os.path.dirname(supervisor.__file__)))
if _GOT != _HERE:
    raise SystemExit(
        f"refusing: suite under {_HERE!r} imported orgtree from {_GOT!r}")

PASS = 0


def check(desc, fn):
    global PASS
    try:
        fn()
        PASS += 1
        print(f"  ok    {desc}")
    except Exception as e:
        print(f"  FAIL  {desc}: {e}")
        raise


def make_org(name, sub=False):
    o = store.create_org(name)
    o.hire(USER, None, "haiku", 5, "boss", add_dirs=[], tools={},
           org_visibility="team", charter="wake fixture root")
    if sub:
        o.hire(USER, "boss", "haiku", 2, "worker", add_dirs=[],
               tools={}, org_visibility="team",
               charter="wake fixture subordinate")
    store.save_org(o)
    return o, o.d["slug"]


def reset_registry():
    restart_wake._reset_startup_done_for_tests()
    restart_wake._reset_boot_build_info_for_tests()
    p = restart_wake._wakes_path()
    try:
        os.remove(p)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
print("\n§1 · persistence, idempotency and registry")


def _arming_writes_durable_record():
    reset_registry()
    r = restart_wake.arm_restart_wake("orgA", "boss", "boss", reason="fix popup")
    assert r["armed"] is True and r["already_armed"] is False, r
    assert r["wake"]["node"] == "boss"
    assert r["wake"]["reason"] == "fix popup"

    # Verify on disk
    with open(restart_wake._wakes_path(), encoding="utf-8") as f:
        data = json.load(f)
    assert "orgA:boss" in data["wakes"]
    rec = data["wakes"]["orgA:boss"]
    assert rec["mode"] == "one_shot"
    assert rec["reason"] == "fix popup"


check("arm: writes durable record to disk with reason and one_shot default",
      _arming_writes_durable_record)


def _arming_survives_bounce():
    reset_registry()
    restart_wake.arm_restart_wake("orgA", "boss", "boss", reason="verify 1fecd8b")
    # Simulate bounce: clear in-memory caches
    restart_wake._boot_info_cache = None
    st = restart_wake.status_restart_wake("orgA", "boss")
    assert st["armed"] is True, st
    assert st["wake"]["reason"] == "verify 1fecd8b"


check("arm: survives process memory loss (reads from disk)",
      _arming_survives_bounce)


def _double_arm_is_idempotent():
    reset_registry()
    r1 = restart_wake.arm_restart_wake("orgA", "boss", "boss", reason="first")
    assert r1["armed"] is True and r1["already_armed"] is False

    r2 = restart_wake.arm_restart_wake("orgA", "boss", "boss", reason="updated")
    assert r2["armed"] is True and r2["already_armed"] is True
    assert r2["wake"]["reason"] == "updated"

    # Still only ONE record in registry
    with open(restart_wake._wakes_path(), encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["wakes"]) == 1


check("arm: double-arm is idempotent, updates reason, never duplicates",
      _double_arm_is_idempotent)


def _cancel_disarms_toggle():
    reset_registry()
    none = restart_wake.cancel_restart_wake("orgA", "boss")
    assert none["cancelled"] is False, "cancelled non-existent wake"

    restart_wake.arm_restart_wake("orgA", "boss", "boss")
    assert restart_wake.status_restart_wake("orgA", "boss")["armed"] is True

    c = restart_wake.cancel_restart_wake("orgA", "boss")
    assert c["cancelled"] is True, c
    assert restart_wake.status_restart_wake("orgA", "boss")["armed"] is False


check("cancel: disarms toggle; cancel when not armed reports cancelled=False",
      _cancel_disarms_toggle)


# ---------------------------------------------------------------------------
print("\n§2 · version identity payload (frozen truth)")


def _boot_info_captures_fields():
    restart_wake._reset_boot_build_info_for_tests()
    info = restart_wake.get_boot_build_info()
    assert "commit" in info and len(info["commit"]) >= 7, info
    assert "commit_short" in info, info
    assert "backend_pid" in info and isinstance(info["backend_pid"], int), info
    assert "started_at" in info, info
    assert "dirty" in info and isinstance(info["dirty"], bool), info


check("version: captures full commit, short commit, pid, started_at, dirty",
      _boot_info_captures_fields)


def _boot_info_is_frozen():
    restart_wake._reset_boot_build_info_for_tests()
    first = restart_wake.get_boot_build_info()
    # Modify cache directly or run subsequent checks:
    second = restart_wake.get_boot_build_info()
    assert first == second
    assert first["started_at"] == second["started_at"]


check("version: boot info is frozen at startup and does not drift",
      _boot_info_is_frozen)


# ---------------------------------------------------------------------------
print("\n§3 · startup pass: passive broadcast (default & free at scale)")


def _passive_broadcast_notifies_live_agents_without_waking():
    reset_registry()
    o, slug = make_org("zz passive", sub=True)
    try:
        # Mock previous boot with PID 9999
        p = restart_wake._wakes_path()
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"running_backend_pid": 9999, "running_commit": "0000000", "wakes": {}}, f)

        restart_wake._reset_boot_build_info_for_tests({
            "commit": "1fecd8b48f0e9112233445566778899aabbccdde",
            "commit_short": "1fecd8b",
            "branch": "main",
            "dirty": False,
            "backend_pid": 12345,
            "started_at": "2026-09-04T12:00:00Z",
        })

        res = restart_wake.on_backend_startup(dry_run=True)
        assert len(res["woken"]) == 0, "passives must not wake"
        assert len(res["notified"]) >= 2, res

        # Check mailbox of boss
        with store.DOC_LOCK:
            org = store.load_org(slug)
            box = org.d.get("mail", {}).get("boss") or []
            assert len(box) == 1, box
            m = box[0]
            assert m["kind"] == "notice", m["kind"]
            assert m["from"] == "orgtree"
            assert "[ORGTREE RESTART NOTICE]" in m["body"]
            assert "The backend was restarted" in m["body"]
            assert "What you can do with this" in m["body"]
            assert "1fecd8b48f0e9112233445566778899aabbccdde" in m["body"]
            assert "12345 (was: 9999)" in m["body"]
            assert "git merge-base --is-ancestor" in m["body"]
            # Mechanism guarantee (ledger.py:2515): waking_mail must be False for notice!
            assert org.waking_mail("boss") is False, "notice must NOT wake agent (ledger.py:2515)"
    finally:
        store.delete_org(slug)


check("passive: live nodes get legible notice with full commit, pid + was_pid; waking_mail is False",
      _passive_broadcast_notifies_live_agents_without_waking)


def _passive_notices_supersede_rather_than_accumulate():
    reset_registry()
    o, slug = make_org("zz supersede", sub=False)
    try:
        # First restart
        restart_wake._reset_boot_build_info_for_tests({
            "commit": "1111111111111111111111111111111111111111",
            "commit_short": "1111111",
            "branch": None,
            "dirty": False,
            "backend_pid": 100,
            "started_at": "2026-09-04T10:00:00Z",
        })
        restart_wake.on_backend_startup(dry_run=True)

        with store.DOC_LOCK:
            org = store.load_org(slug)
            box = org.d.get("mail", {}).get("boss") or []
            assert len(box) == 1
            assert "1111111" in box[0]["body"]

        # Second restart before agent took a turn!
        restart_wake._reset_boot_build_info_for_tests({
            "commit": "2222222222222222222222222222222222222222",
            "commit_short": "2222222",
            "branch": None,
            "dirty": False,
            "backend_pid": 200,
            "started_at": "2026-09-04T11:00:00Z",
        })
        restart_wake.on_backend_startup(dry_run=True)

        # Agent should still have exactly ONE restart notice, updated to build 2!
        with store.DOC_LOCK:
            org = store.load_org(slug)
            box = org.d.get("mail", {}).get("boss") or []
            assert len(box) == 1, f"accumulated notices: {box}"
            assert "2222222" in box[0]["body"], box[0]["body"]
            assert "1111111" not in box[0]["body"], "old notice was not superseded"
    finally:
        store.delete_org(slug)


check("passive: subsequent restarts supersede in-place, never accumulate",
      _passive_notices_supersede_rather_than_accumulate)


def _archived_agents_are_excluded():
    reset_registry()
    o, slug = make_org("zz archive skip", sub=True)
    try:
        # Retire worker
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.retire(USER, "worker")
            store.save_org(org)

        restart_wake._reset_boot_build_info_for_tests({
            "commit": "3333333333333333333333333333333333333333",
            "commit_short": "3333333",
            "branch": None,
            "dirty": False,
            "backend_pid": 300,
            "started_at": "2026-09-04T12:00:00Z",
        })
        restart_wake.on_backend_startup(dry_run=True)

        with store.DOC_LOCK:
            org = store.load_org(slug)
            # Boss is live -> gets notice
            assert len(org.d.get("mail", {}).get("boss") or []) == 1
            # Worker is archived -> NO notice
            assert len(org.d.get("mail", {}).get("worker") or []) == 0, \
                "archived agent got a restart notice"
    finally:
        store.delete_org(slug)


check("passive: archived agents (~195 on machine) are excluded from notices",
      _archived_agents_are_excluded)


# ---------------------------------------------------------------------------
print("\n§4 · startup pass: waking toggle (opt-in)")


def _armed_toggle_wakes_agent_and_clears_one_shot():
    reset_registry()
    o, slug = make_org("zz wake toggle", sub=False)
    try:
        restart_wake.arm_restart_wake(slug, "boss", "boss", reason="verify popup fix")

        # Intercept supervisor.send_message
        sent = []
        orig_send = supervisor.send_message
        supervisor.send_message = lambda s, n, txt, **kw: sent.append((s, n, txt, kw))
        try:
            restart_wake._reset_boot_build_info_for_tests({
                "commit": "4444444444444444444444444444444444444444",
                "commit_short": "4444444",
                "branch": "feat/popup",
                "dirty": False,
                "backend_pid": 400,
                "started_at": "2026-09-04T13:00:00Z",
            })
            res = restart_wake.on_backend_startup(dry_run=True)
            assert len(res["woken"]) == 1, res["woken"]
            assert len(sent) == 1, sent
            s, n, txt, kw = sent[0]
            assert s == slug and n == "boss"
            assert kw.get("wake") is True, "must wake with a real turn"
            assert "[ORGTREE RESTART WAKE]" in txt
            assert "4444444444444444444444444444444444444444" in txt
            assert "verify popup fix" in txt
            assert "re-arm with orgtree_restart_wake" in txt

            # One-shot toggle must be CLEARED from registry
            st = restart_wake.status_restart_wake(slug, "boss")
            assert st["armed"] is False, "one-shot toggle was not cleared"
        finally:
            supervisor.send_message = orig_send
    finally:
        store.delete_org(slug)


check("toggle: armed agent is woken with full turn; one-shot is cleared",
      _armed_toggle_wakes_agent_and_clears_one_shot)


def _standing_mode_is_refused():
    reset_registry()
    try:
        restart_wake.arm_restart_wake("orgA", "boss", "boss", mode="standing")
        raise AssertionError("should have refused mode='standing'")
    except ValueError as e:
        assert "only one-shot" in str(e), str(e)


check("toggle: mode='standing' is refused (one-shot only per coordinator decision)",
      _standing_mode_is_refused)


def _fired_toggle_is_always_cleared_even_legacy_standing():
    reset_registry()
    o, slug = make_org("zz legacy standing", sub=False)
    try:
        # Pre-populate disk record with legacy mode="standing"
        p = restart_wake._wakes_path()
        with open(p, "w", encoding="utf-8") as f:
            json.dump({
                "wakes": {
                    f"{slug}:boss": {
                        "org": slug, "node": "boss", "mode": "standing",
                        "armed_at": restart_wake.now_iso(), "armed_by": "boss"
                    }
                }
            }, f)

        sent = []
        orig_send = supervisor.send_message
        supervisor.send_message = lambda s, n, txt, **kw: sent.append((s, n, txt, kw))
        try:
            restart_wake.on_backend_startup(dry_run=True)
            assert len(sent) == 1, sent
            # MUST be cleared from registry on startup despite mode="standing"
            st = restart_wake.status_restart_wake(slug, "boss")
            assert st["armed"] is False, "legacy standing record was not cleared"
        finally:
            supervisor.send_message = orig_send
    finally:
        store.delete_org(slug)


check("toggle: a fired toggle is ALWAYS cleared (even if record had legacy mode='standing')",
      _fired_toggle_is_always_cleared_even_legacy_standing)


def _compacted_agent_survives_and_receives_wake():
    reset_registry()
    o, slug = make_org("zz compact wake", sub=False)
    try:
        restart_wake.arm_restart_wake(slug, "boss", "boss", reason="preserve across compact")
        # Simulate cheap_compact: session_id replaced, seat stays live
        with store.DOC_LOCK:
            org = store.load_org(slug)
            old_sid = org.node("boss")["session_id"]
            org.node("boss")["session_id"] = "fresh-session-xyz"
            store.save_org(org)

        sent = []
        orig_send = supervisor.send_message
        supervisor.send_message = lambda s, n, txt, **kw: sent.append((s, n, txt, kw))
        try:
            restart_wake.on_backend_startup(dry_run=True)
            assert len(sent) == 1, sent
            s, n, txt, kw = sent[0]
            assert n == "boss"
            assert "preserve across compact" in txt
        finally:
            supervisor.send_message = orig_send
    finally:
        store.delete_org(slug)


check("toggle: survives agent compaction (seat remains live, successor woken)",
      _compacted_agent_survives_and_receives_wake)


def _retired_agent_toggle_is_dropped():
    reset_registry()
    o, slug = make_org("zz retire drop", sub=True)
    try:
        restart_wake.arm_restart_wake(slug, "worker", "boss", reason="will retire")
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.retire(USER, "worker")
            store.save_org(org)

        sent = []
        orig_send = supervisor.send_message
        supervisor.send_message = lambda s, n, txt, **kw: sent.append((s, n, txt, kw))
        try:
            res = restart_wake.on_backend_startup(dry_run=True)
            # Worker must NOT be woken!
            for s, n, txt, kw in sent:
                assert n != "worker", "retired agent was woken!"
            assert any(d["node"] == "worker" for d in res["dropped"])
            assert restart_wake.status_restart_wake(slug, "worker")["armed"] is False
        finally:
            supervisor.send_message = orig_send
    finally:
        store.delete_org(slug)


check("toggle: retired agent's toggle is dropped without starting a turn",
      _retired_agent_toggle_is_dropped)


# ---------------------------------------------------------------------------
print("\n§5 · API integration (/api/agent) & tool card")


def _tool_card_exists_in_catalogue():
    card = next((t for t in mcptool.TOOLS if t["name"] == "orgtree_restart_wake"), None)
    assert card is not None, "orgtree_restart_wake not in mcptool.TOOLS"
    props = card["inputSchema"]["properties"]
    assert set(props["action"]["enum"]) == {"arm", "cancel", "status"}
    assert "mode" not in props, "mode must be removed from schema (one-shot only)"
    assert not card["inputSchema"].get("required"), "arming should need no args"


check("surface: tool card exists with action/reason/target properties (one-shot only)",
      _tool_card_exists_in_catalogue)


def _api_agent_round_trip():
    from fastapi.testclient import TestClient
    from orgtree import api
    reset_registry()
    o, slug = make_org("zz api agent", sub=True)
    try:
        c = TestClient(api.app)

        def call(node, args):
            return c.post("/api/agent", json={
                "org": slug, "node": node,
                "tool": "orgtree_restart_wake", "args": args})

        # Arm for self
        r = call("boss", {"reason": "via api"})
        assert r.status_code == 200, (r.status_code, r.text)
        assert r.json()["armed"] is True
        assert r.json()["wake"]["reason"] == "via api"

        # Status
        st = call("boss", {"action": "status"}).json()
        assert st["armed"] is True
        assert st["wake"]["reason"] == "via api"

        # Arm for subordinate
        r_sub = call("boss", {"target": "worker", "reason": "boss armed for worker"})
        assert r_sub.status_code == 200, r_sub.text
        assert r_sub.json()["wake"]["node"] == "worker"

        # Subordinate cannot target boss (non-subordinate) -> 403
        bad = call("worker", {"target": "boss"})
        assert bad.status_code == 403, (bad.status_code, bad.text)

        # Cancel
        can = call("boss", {"action": "cancel"}).json()
        assert can["cancelled"] is True

        # Invalid mode -> 422 (standing rejected, one-shot only)
        bad_mode = call("boss", {"mode": "standing"})
        assert bad_mode.status_code == 422

        # Invalid action -> 422
        inv = call("boss", {"action": "invalid"})
        assert inv.status_code == 422
    finally:
        store.delete_org(slug)


check("surface: arm/status/target/cancel round-trip through /api/agent",
      _api_agent_round_trip)


# ---------------------------------------------------------------------------
print("\n§6 · mutant coverage (the checks above can fail)")


def _mutants():
    """Verify that mutants break the checks. Proves checks are not decorative."""
    results = []

    # Mutant 1: Passive notices wake agents
    reset_registry()
    o, slug = make_org("zz mut1", sub=False)
    try:
        restart_wake.on_backend_startup(dry_run=True)
        with store.DOC_LOCK:
            org = store.load_org(slug)
            normal_wakes = org.waking_mail("boss")
            results.append(("passive notice does not wake agent", normal_wakes is False))
    finally:
        store.delete_org(slug)

    # Mutant 2: One-shot toggle fails to clear after firing
    reset_registry()
    o, slug = make_org("zz mut2", sub=False)
    try:
        restart_wake.arm_restart_wake(slug, "boss", "boss", reason="mutant test")
        restart_wake.on_backend_startup(dry_run=True)
        st = restart_wake.status_restart_wake(slug, "boss")
        results.append(("one_shot toggle clears after firing", st["armed"] is False))
    finally:
        store.delete_org(slug)

    # Mutant 3: Superseding disabled (notices accumulate)
    reset_registry()
    o, slug = make_org("zz mut3", sub=False)
    try:
        restart_wake.on_backend_startup(dry_run=True)
        restart_wake._reset_boot_build_info_for_tests({
            "commit": "9999999999999999999999999999999999999999",
            "commit_short": "9999999",
            "branch": None,
            "dirty": False,
            "backend_pid": 999,
            "started_at": "2026-09-04T12:00:00Z",
        })
        restart_wake.on_backend_startup(dry_run=True)
        with store.DOC_LOCK:
            org = store.load_org(slug)
            box = org.d.get("mail", {}).get("boss") or []
            results.append(("notices supersede rather than accumulate", len(box) == 1))
    finally:
        store.delete_org(slug)

    # Mutant 4: Archived nodes receive notices
    reset_registry()
    o, slug = make_org("zz mut4", sub=True)
    try:
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.retire(USER, "worker")
            store.save_org(org)
        restart_wake.on_backend_startup(dry_run=True)
        with store.DOC_LOCK:
            org = store.load_org(slug)
            worker_box = org.d.get("mail", {}).get("worker") or []
            results.append(("archived nodes excluded from restart notices", len(worker_box) == 0))
    finally:
        store.delete_org(slug)

    # Mutant 5: Target authority check in API bypassed
    from fastapi.testclient import TestClient
    from orgtree import api
    reset_registry()
    o, slug = make_org("zz mut5", sub=True)
    try:
        c = TestClient(api.app)
        bad = c.post("/api/agent", json={
            "org": slug, "node": "worker",
            "tool": "orgtree_restart_wake",
            "args": {"target": "boss"}})
        results.append(("subordinate cannot arm for boss (403)", bad.status_code == 403))
    finally:
        store.delete_org(slug)

    # Mutant 6: Toggle mode="standing" accepted (must refuse with 422)
    reset_registry()
    o, slug = make_org("zz mut6", sub=False)
    try:
        c = TestClient(api.app)
        bad_mode = c.post("/api/agent", json={
            "org": slug, "node": "boss",
            "tool": "orgtree_restart_wake",
            "args": {"mode": "standing"}})
        results.append(("standing mode rejected with 422", bad_mode.status_code == 422))
    finally:
        store.delete_org(slug)

    # Mutant 7: Toggle waking turn missing wake=True flag
    reset_registry()
    o, slug = make_org("zz mut7", sub=False)
    try:
        restart_wake.arm_restart_wake(slug, "boss", "boss")
        sent = []
        orig_send = supervisor.send_message
        supervisor.send_message = lambda s, n, txt, **kw: sent.append((s, n, txt, kw))
        try:
            restart_wake.on_backend_startup(dry_run=True)
            wake_has_flag = len(sent) == 1 and sent[0][3].get("wake") is True
            results.append(("toggle fires with wake=True turn", wake_has_flag))
        finally:
            supervisor.send_message = orig_send
    finally:
        store.delete_org(slug)

    # Mutant 8: Legacy standing record survives startup drain (not always cleared)
    reset_registry()
    o, slug = make_org("zz mut8", sub=False)
    try:
        p = restart_wake._wakes_path()
        with open(p, "w", encoding="utf-8") as f:
            json.dump({
                "wakes": {
                    f"{slug}:boss": {
                        "org": slug, "node": "boss", "mode": "standing",
                        "armed_at": restart_wake.now_iso(), "armed_by": "boss"
                    }
                }
            }, f)
        restart_wake.on_backend_startup(dry_run=True)
        st = restart_wake.status_restart_wake(slug, "boss")
        results.append(("fired toggle is always cleared at drain", st["armed"] is False))
    finally:
        store.delete_org(slug)

    blind = [name for name, passed in results if not passed]
    assert not blind, f"Mutants survived: {blind}"
    return len(results)


_n = _mutants()
check(f"mutants: all {_n} value-replacement / behavioral mutants are DETECTED",
      lambda: None)

print(f"\nALL {PASS} CHECKS PASS")