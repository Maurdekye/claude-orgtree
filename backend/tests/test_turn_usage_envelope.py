"""Dynamic provider usage envelopes: coverage, secrecy and cache identity.

Plain deterministic checks; no provider/network calls.  Run with:
    python backend/tests/test_turn_usage_envelope.py
"""

from __future__ import annotations

import atexit
import copy
import datetime as dt
import inspect
import os
import shutil
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = tempfile.mkdtemp(prefix="orgtree-turnusage-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import (accounts, codex_limits, limits, store, supervisor as S,
                     turnusage, warmpool)                 # noqa: E402
from orgtree.ledger import USER                           # noqa: E402

assert DATA != os.path.expanduser("~/orgtree")
S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None
atexit.register(lambda: shutil.rmtree(DATA, ignore_errors=True))

NOW = 1788253200.0
PASS = FAIL = 0


def iso(epoch: float) -> str:
    return (dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as e:
        FAIL += 1
        print(f"  FAIL    {label}: {e}")
        import traceback
        traceback.print_exc()


def fixture(name: str = "zz-turnusage", tier: str = "haiku"):
    org = store.create_org(name)
    org.hire(USER, None, tier, 0, "agent")
    store.save_org(org)
    return org, "agent"


def registry() -> dict[str, Any]:
    return {
        "version": 2,
        "keys": [
            {"id": "sk-ant-secret-fallback-one", "account_uuid": "private-one"},
            {"id": "eyJ-secret-fallback-two", "account_uuid": "private-two"},
        ],
        "usage_refreshes": {
            "sk-ant-secret-fallback-one": {
                "haiku": NOW + 600, "sonnet": NOW + 600,
                "opus": NOW + 600, "fable": NOW + 3600,
            },
            "eyJ-secret-fallback-two": {},
        },
        "key_liveness": {},
    }


def claude_board(*, stale: bool = False) -> dict[str, Any]:
    return {
        "available": True,
        "observed_at": iso(NOW - (1200 if stale else 90)),
        "age": 1200.0 if stale else 90.0,
        "stale": stale,
        # Deliberately reversed: formatter owns canonical ordering.
        "limits": [
            {"kind": "weekly_scoped", "percent": 90,
             "resets_at": iso(NOW + 3600), "is_active": False,
             "model": "Fable"},
            {"kind": "weekly_all", "percent": 100,
             "resets_at": iso(NOW + 7200), "is_active": True},
            {"kind": "session", "percent": 25.5,
             "resets_at": iso(NOW + 1800), "is_active": False},
        ],
    }


def codex_board() -> dict[str, Any]:
    return {
        "available": True,
        "observed_at": iso(NOW - 30), "age": 30.0, "stale": False,
        "limits": [
            {"kind": "codex_window", "percent": 8,
             "resets_at": iso(NOW + 3600), "is_active": False},
            {"kind": "weekly_all", "percent": 39,
             "resets_at": iso(NOW + 604800),
             "is_active": False,
             # Neither raw label nor group may enter the envelope.
             "label": "sk-ant-must-not-leak",
             "group": "eyJ-must-not-leak"},
        ],
    }


REAL_CLAUDE_SNAPSHOT = limits.snapshot
REAL_CODEX_SNAPSHOT = codex_limits.snapshot
REAL_ACCOUNTS_LOAD = accounts.load


def planted_sources() -> None:
    limits.snapshot = lambda now=None: copy.deepcopy(claude_board())  # type: ignore[assignment]
    codex_limits.snapshot = lambda now=None: copy.deepcopy(codex_board())  # type: ignore[assignment]
    accounts.load = lambda strict=False: copy.deepcopy(registry())  # type: ignore[assignment]


def restore_sources() -> None:
    limits.snapshot = REAL_CLAUDE_SNAPSHOT                         # type: ignore[assignment]
    codex_limits.snapshot = REAL_CODEX_SNAPSHOT                    # type: ignore[assignment]
    accounts.load = REAL_ACCOUNTS_LOAD                             # type: ignore[assignment]


def supported_coverage_and_schema() -> None:
    org, nid = fixture("zz-turnusage-coverage")
    planted_sources()
    try:
        block = turnusage.render(
            org, nid, selected_provider="claude",
            selected_lane="fallback-2", now=NOW)
    finally:
        restore_sources()
    assert block.startswith("[PROVIDER USAGE — current as of "), block[:100]
    assert block.endswith(turnusage.CLOSE)
    header = ("provider/lane | window | used | amount | reset (countdown) | "
              "observed (age,freshness) | state")
    assert header in block
    assert "claude/primary | session | 25.5% | - |" in block
    assert "claude/primary | weekly_all | 100% | - |" in block
    assert "claude/primary | weekly_scoped:fable | 90% | - |" in block
    assert "claude/fallback-1 | haiku+sonnet+opus | unavailable(unsupported)" in block
    assert "claude/fallback-2* | haiku+sonnet+opus | unavailable(unsupported)" in block
    assert "codex/account | weekly_all | 39% | - |" in block
    assert "codex/account | codex_window | 8% | - |" in block
    assert ("gemini/account | usage | unavailable(unsupported) | - | - | - | "
            "unsupported") in block
    # No normalized source currently carries an authoritative absolute quota.
    # The stable amount column says so; it never derives one from a percent.
    for line in block.splitlines():
        if line.startswith(("claude/", "codex/", "gemini/")):
            assert " | - | " in line, line


check("stable schema covers Claude, fallbacks, Codex, and explicit Gemini unsupported",
      supported_coverage_and_schema)


def ordering_is_deterministic() -> None:
    org, nid = fixture("zz-turnusage-order")
    reg = registry()
    planted_sources()
    try:
        a = turnusage.render(org, nid, now=NOW)
        original_c = claude_board()
        original_c["limits"].reverse()
        original_x = codex_board()
        original_x["limits"] = list(reversed(original_x["limits"]))
        limits.snapshot = lambda now=None: copy.deepcopy(original_c)  # type: ignore[assignment]
        codex_limits.snapshot = lambda now=None: copy.deepcopy(original_x)  # type: ignore[assignment]
        reg["keys"] = list(reversed(reg["keys"]))
        # Reversing account priority SHOULD change fallback ordinals, so keep
        # the registry order fixed for the byte-identical window-order proof.
        accounts.load = lambda strict=False: copy.deepcopy(registry())  # type: ignore[assignment]
        b = turnusage.render(org, nid, now=NOW)
    finally:
        restore_sources()
    assert a == b, "provider response ordering changed the envelope bytes"
    positions = [a.index(x) for x in (
        "claude/primary | session", "claude/primary | weekly_all",
        "claude/primary | weekly_scoped:fable", "claude/fallback-1",
        "claude/fallback-2", "codex/account", "gemini/account")]
    assert positions == sorted(positions), positions


check("provider, account, and window ordering is deterministic",
      ordering_is_deterministic)


def stale_unknown_and_unavailable_are_explicit() -> None:
    org, nid = fixture("zz-turnusage-states")
    stale = claude_board(stale=True)
    stale["limits"][0]["percent"] = float("nan")
    stale["limits"][0]["resets_at"] = "not-a-time"
    limits.snapshot = lambda now=None: copy.deepcopy(stale)          # type: ignore[assignment]
    codex_limits.snapshot = lambda now=None: {                       # type: ignore[assignment]
        "available": False, "limits": [], "observed_at": None,
        "age": None, "stale": False}
    accounts.load = lambda strict=False: {                           # type: ignore[assignment]
        "version": 2, "keys": [], "usage_refreshes": {},
        "key_liveness": {}}
    try:
        block = turnusage.render(org, nid, now=NOW)
    finally:
        restore_sources()
    assert "unavailable | - | - |" in block, block
    assert "(1200s,stale) | stale" in block, block
    assert "codex/account | usage | unavailable(no-cache)" in block
    assert "gemini/account | usage | unavailable(unsupported)" in block


check("unknown, stale, invalid, no-cache, and unsupported states are explicit",
      stale_unknown_and_unavailable_are_explicit)


def reset_countdown_cooldown_and_freeze() -> None:
    org, nid = fixture("zz-turnusage-freeze")
    org.node(nid)["frozen"] = {
        "limit": True, "until_ts": NOW + 900,
        "at": iso(NOW - 1), "reset_src": "usage:session"}
    planted_sources()
    try:
        block = turnusage.render(
            org, nid, selected_provider="claude",
            selected_lane="primary", now=NOW)
    finally:
        restore_sources()
    assert f"{iso(NOW + 900)} (+15m)" in block
    selected = [line for line in block.splitlines()
                if line.startswith("claude/primary*")]
    assert selected and all(line.endswith("| frozen") for line in selected), selected
    fallback = next(line for line in block.splitlines()
                    if line.startswith("claude/fallback-1")
                    and "haiku+sonnet+opus" in line)
    assert "(+10m)" in fallback and fallback.endswith("| cooldown"), fallback


check("authoritative resets get countdowns and frozen/cooldown state",
      reset_countdown_cooldown_and_freeze)


def multi_account_labels_and_secrets_never_leak() -> None:
    org, nid = fixture("zz-turnusage-secrets")
    malicious = claude_board()
    malicious["limits"].append({
        "kind": "sk-ant-secret-window", "percent": 4,
        "model": "eyJ-secret-model", "group": "private-group",
        "resets_at": None, "error": "Bearer top-secret"})
    limits.snapshot = lambda now=None: copy.deepcopy(malicious)      # type: ignore[assignment]
    codex_limits.snapshot = lambda now=None: copy.deepcopy(codex_board())  # type: ignore[assignment]
    accounts.load = lambda strict=False: copy.deepcopy(registry())  # type: ignore[assignment]
    real_resolve = accounts.resolve
    real_bills = S.bills_the_key
    real_fallback_active = S.api_fallback_active
    accounts.resolve = lambda tier, now=None: {                    # type: ignore[assignment]
        "account": "eyJ-secret-fallback-two"}
    S.bills_the_key = lambda org, active: False                    # type: ignore[assignment]
    S.api_fallback_active = lambda org, now=None: False            # type: ignore[assignment]
    try:
        provider, lane = S._turn_usage_selection(org, nid, NOW)
        block = turnusage.render(
            org, nid, selected_provider="claude",
            selected_lane=lane, now=NOW)
    finally:
        accounts.resolve = real_resolve                            # type: ignore[assignment]
        S.bills_the_key = real_bills                              # type: ignore[assignment]
        S.api_fallback_active = real_fallback_active              # type: ignore[assignment]
        restore_sources()
    assert provider == "claude" and lane == "fallback-2", (provider, lane)
    assert "fallback-2*" in block
    for secret in ("sk-ant", "eyJ", "private-one", "private-two",
                   "private-group", "top-secret", "Bearer"):
        assert secret not in block, f"secret-shaped source field leaked: {secret}"


check("multi-account selection uses ordinals and redacts every raw secret-shaped field",
      multi_account_labels_and_secrets_never_leak)


def values_change_board_not_identity() -> None:
    org, nid = fixture("zz-turnusage-identity")
    accounts.load = lambda strict=False: {                           # type: ignore[assignment]
        "version": 2, "keys": [], "usage_refreshes": {},
        "key_liveness": {}}
    limits.snapshot = lambda now=None: copy.deepcopy(claude_board())  # type: ignore[assignment]
    codex_limits.snapshot = lambda now=None: copy.deepcopy(codex_board())  # type: ignore[assignment]
    try:
        before_hash, before_parts = warmpool.identity_snapshot(org, nid)
        before_prompt = S.identity_prompt(org, nid)
        before_block = turnusage.render(org, nid, now=NOW)
        moved = claude_board()
        moved["limits"][1]["percent"] = 73
        moved["observed_at"] = iso(NOW)
        moved["age"] = 0
        limits.snapshot = lambda now=None: copy.deepcopy(moved)       # type: ignore[assignment]
        after_block = turnusage.render(org, nid, now=NOW)
        after_prompt = S.identity_prompt(org, nid)
        after_hash, after_parts = warmpool.identity_snapshot(org, nid)
    finally:
        restore_sources()
    assert before_block != after_block, "calibration: telemetry mutation was invisible"
    assert before_prompt == after_prompt, "usage entered the managed system prompt"
    assert before_hash == after_hash and before_parts == after_parts, (
        "usage changed warm identity inputs")

    # Fail-capability calibration: the detector would catch the forbidden
    # design where dynamic usage is appended to the identity.
    forbidden_before = before_prompt + before_block
    forbidden_after = after_prompt + after_block
    assert forbidden_before != forbidden_after


check("moving usage changes only the user block, never system or warm identity",
      values_change_board_not_identity)


def every_source_failure_is_fail_open_and_secret_free() -> None:
    org, nid = fixture("zz-turnusage-failopen")

    def boom(*_args, **_kwargs):
        raise RuntimeError("Bearer sk-ant-secret-from-exception eyJsecret")

    limits.snapshot = boom                                      # type: ignore[assignment]
    codex_limits.snapshot = boom                                # type: ignore[assignment]
    accounts.load = boom                                        # type: ignore[assignment]
    try:
        block = turnusage.render(org, nid, now=NOW)
    finally:
        restore_sources()
    assert block.count("telemetry-error") >= 3, block
    assert "sk-ant" not in block and "eyJ" not in block and "Bearer" not in block

    # D-223 moved the formatter seam: `turn_usage_block` now calls `board`,
    # which returns (text, material_key), and `render` is the text-only wrapper
    # for callers outside a turn. The invariant is unchanged and is what is
    # being pinned — a formatter that raises must degrade to the failure block
    # rather than fail the turn — so this patches the seam the turn actually
    # travels. Patching `render` here would pass while testing nothing.
    real_board = turnusage.board
    real_select = S._turn_usage_selection
    turnusage.board = boom                                       # type: ignore[assignment]
    S._turn_usage_selection = lambda org, nid, now: ("claude", "primary")  # type: ignore[assignment]
    try:
        outer = S.turn_usage_block(org, nid, NOW)
    finally:
        turnusage.board = real_board                             # type: ignore[assignment]
        S._turn_usage_selection = real_select                    # type: ignore[assignment]
    assert turnusage.CLOSE in outer and "telemetry-error" in outer
    # …and `render` must still be the text half of `board`, or the wrapper has
    # silently become a second renderer that can drift from the real one.
    assert turnusage.render(org, nid, now=NOW) == turnusage.board(
        org, nid, now=NOW)[0]


check("source and formatter failures degrade to safe unavailable rows without blocking",
      every_source_failure_is_fail_open_and_secret_free)


def cache_snapshots_keep_timestamped_stale_evidence_without_fetch() -> None:
    with limits._lock:
        limits._cache.update(at=NOW - 1000, data={
            "available": True,
            "limits": [{"kind": "session", "percent": 12}]})
    c = REAL_CLAUDE_SNAPSHOT(NOW)
    assert c["available"] and c["stale"]
    assert c["age"] == 1000 and c["observed_at"] == iso(NOW - 1000)
    assert c["limits"][0]["percent"] == 12

    with codex_limits._lock:
        codex_limits._cache.update(at=NOW - 901, data={
            "available": True,
            "limits": [{"kind": "weekly_all", "percent": 22}]})
    x = REAL_CODEX_SNAPSHOT(NOW)
    assert x["available"] and x["stale"]
    assert x["age"] == 901 and x["observed_at"] == iso(NOW - 901)
    assert x["limits"][0]["percent"] == 22


check("cache-only snapshots preserve observation time and stale evidence",
      cache_snapshots_keep_timestamped_stale_evidence_without_fetch)


def construction_sites_and_exclusions() -> None:
    run_src = inspect.getsource(S._run_one_turn)
    ident_src = inspect.getsource(S.identity_prompt)
    cmd_src = inspect.getsource(S._build_cmd)
    hash_src = inspect.getsource(warmpool.identity_snapshot)
    assert run_src.count("turn_usage_block(") == 2, (
        "ordinary and Claude boundary-fed turns each need a fresh block")
    first_usage = run_src.index("usage_block = (turn_usage_block")
    assert first_usage < run_src.index("_codex_leg("), (
        "provider seam sees text before usage was attached")
    boundary_usage = run_src.index("nxt = (turn_usage_block")
    assert boundary_usage < run_src.index("proc.stdin.write", boundary_usage)
    assert run_src.rfind('ninf: InflightInfo', 0, boundary_usage) >= 0, (
        "dynamic usage was persisted into replay text instead of rebuilt")
    for source, name in ((ident_src, "system prompt"), (cmd_src, "launch argv"),
                         (hash_src, "warm identity hash")):
        assert "turn_usage_block" not in source, f"usage entered {name}"
    assert "if not is_cmd" in run_src[:first_usage], (
        "slash commands no longer remain verbatim")
    doc = open(os.path.join(os.path.dirname(BACKEND), "docs",
                            "turn-usage-envelope.md"), encoding="utf-8").read()
    for phrase in ("automatic working-checkup", "restart-reconciled/resumed",
                   "Slash-command turns", "Mid-response steering"):
        assert phrase in doc, f"delivery/exclusion documentation lost {phrase!r}"


check("ordinary/boundary placement and command/steer exclusions are pinned",
      construction_sites_and_exclusions)


restore_sources()
if FAIL:
    print(f"\n{FAIL} FAILED, {PASS} PASSED")
    raise SystemExit(1)
print(f"\nALL {PASS} CHECKS PASS")
