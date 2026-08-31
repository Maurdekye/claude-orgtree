"""Limit -> wake cache attribution journal contract.

Hermetic: no CLI, API call, listener, or network.

    python backend/tests/test_limit_cache_attribution.py
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

RIG = tempfile.mkdtemp(prefix="orgtree-limit-cache-")
os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = os.path.join(RIG, "home")
os.environ["USERPROFILE"] = os.environ["HOME"]
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')

from orgtree import supervisor as S, warmpool as W  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                      # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def rows() -> list[dict]:
    path = os.path.join(RIG, "journals", "warm.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


print("\nlimit -> wake cache attribution")

W.journal_limit_cache_usage(
    "org", "node", "sid-a", 101, "acct-a",
    {
        "input_tokens": 11,
        "cache_read_input_tokens": 22000,
        "cache_creation_input_tokens": 300,
        "output_tokens": 19,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 20,
            "ephemeral_1h_input_tokens": 280,
            "secret": "must-not-land",
        },
        "result": "prompt/result text must not land",
    },
    phase="limit", limited=True)
W.journal_limit_cache_usage(
    "org", "node", "sid-a", 202, "acct-a",
    {
        "input_tokens": 7,
        "cache_read_input_tokens": 22100,
        "cache_creation_input_tokens": 0,
        "output_tokens": 23,
    },
    phase="first-after-resume", limited=False,
    prior_sid="sid-a", prior_pid=101, prior_account="acct-a",
    freeze_s=1800.1236, resume_wait_s=2.3456)
W.journal_limit_cache_usage(
    "org", "node", "sid-x", 303, "acct-x", {},
    phase="unbounded-new-vocabulary", limited=False)

limit_rows = [r for r in rows() if r.get("kind") == "limit-cache"]


def journal_contract() -> None:
    assert len(limit_rows) == 2, limit_rows
    hit, wake = limit_rows
    assert hit["phase"] == "limit" and hit["limited"] is True
    assert hit["cache_read_input_tokens"] == 22000
    assert hit["cache_creation_input_tokens"] == 300
    assert hit["ephemeral_5m_input_tokens"] == 20
    assert hit["ephemeral_1h_input_tokens"] == 280
    assert "result" not in hit and "secret" not in hit
    assert wake["phase"] == "first-after-resume"
    assert wake["process_respawned"] is True
    assert wake["prior_pid"] == 101 and wake["pid"] == 202
    assert wake["pid_changed"] is True
    assert wake["same_session"] is True
    assert wake["same_account"] is True
    assert wake["freeze_s"] == 1800.124
    assert wake["resume_wait_s"] == 2.346


check("bounded rows expose raw cache usage and cross-process identity",
      journal_contract)


def marker_lifecycle_contract() -> None:
    marker = {"session_id": "sid-a", "pid": 101, "account": "acct-a"}
    st = {"limit_cache_resume": marker}

    phase, got = S._limit_cache_result_state(
        st, {"input_tokens": 0}, limited=False)
    assert (phase, got) == (None, None)
    assert st["limit_cache_resume"] is marker

    # A second limit with no usage is still a real limit row, but it cannot
    # answer read-vs-create and therefore must not consume the marker.
    phase, got = S._limit_cache_result_state(
        st, {
            "input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "output_tokens": 0,
        }, limited=True)
    assert (phase, got) == ("limit", None)
    assert st["limit_cache_resume"] is marker

    phase, got = S._limit_cache_result_state(
        st, {"input_tokens": 1, "cache_read_input_tokens": 22000},
        limited=False)
    assert phase == "first-after-resume" and got is marker
    assert "limit_cache_resume" not in st


check("all-zero errors retain the marker; positive usage takes it",
      marker_lifecycle_contract)


def provider_gate_contract() -> None:
    foreign = {"limit_cache_resume": {}, "limit_cache_origin": {}}
    assert S._limit_cache_claude_state(foreign, "luna") is False
    assert "limit_cache_resume" not in foreign
    assert "limit_cache_origin" not in foreign

    claude = {"limit_cache_resume": {"session_id": "sid-a"}}
    assert S._limit_cache_claude_state(claude, "fable") is True
    assert "limit_cache_resume" in claude


check("cross-provider resume state is cleared while Claude state survives",
      provider_gate_contract)


def wiring_contract() -> None:
    run_src = inspect.getsource(S._run_one_turn)
    resume_src = inspect.getsource(S.resume_frozen)
    assert "_limit_cache_result_state(st, _usage, limited)" in run_src
    assert "journal_limit_cache_usage(" in run_src
    assert 'st["limit_cache_origin"]' in run_src
    assert "_limit_cache_claude_state(st, _turn_tier)" in run_src
    assert "_limit_cache_claude_state(st, tier)" in resume_src
    assert 'st["limit_cache_resume"]' in resume_src
    assert 'bool(fz.get("limit"))' in resume_src
    assert "freeze_s" in resume_src


check("the behavioral helpers are wired at freeze, resume, and result",
      wiring_contract)


def no_probe_contract() -> None:
    src = inspect.getsource(W.journal_limit_cache_usage)
    forbidden = ("subprocess", "Popen", "_build_cmd", "stdin", "prompt")
    # The docstring may explain prompt non-mutation; inspect executable lines.
    body = src[src.index("if phase not in"):]
    assert not any(word in body for word in forbidden), body


check("telemetry performs no API/process/prompt operation", no_probe_contract)

if FAIL:
    for label, tb in FAIL:
        print(f"\n--- {label} ---\n{tb}")
    raise SystemExit(1)
print(f"\nPASS: {PASS} checks")
