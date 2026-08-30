"""D-205 — fallback-key liveness is isolated, conservative, and paced.

Run: ``python backend/tests/test_fallback_key_liveness.py``.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = tempfile.mkdtemp(prefix="orgtree-fallback-liveness-")
os.environ["ORGTREE_DATA"] = ROOT
with open(os.path.join(ROOT, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import accounts, fallback_probe, limits, tokens  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
KID = "kLIVENESS0001"


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — report all checks in one run
        FAIL.append((label, str(e)))
        print(f"  FAIL  {label}: {e}")
    else:
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")


def eq(got, want, detail="") -> None:
    if got != want:
        raise AssertionError(f"got {got!r}, want {want!r}; {detail}")


def legacy(exit_code: int, text: str) -> str:
    """The one-off probe's former sentence-specific classifier."""
    if exit_code == 0:
        return fallback_probe.ALIVE
    if "You've hit your session limit" in text:
        return fallback_probe.LIMITED
    if "401 OAuth access token is invalid" in text:
        return fallback_probe.DEAD
    return fallback_probe.UNKNOWN


def reset() -> None:
    doc = accounts._blank()
    doc["keys"] = [{"id": KID, "account_uuid": None}]
    accounts.save(doc)
    tokens.put(KID, "test-token-not-a-real-credential")


def classify_cases() -> None:
    print("§1 response classes and precedence")
    combined = "HTTP 429: 401 OAuth access token is invalid while rate limit resets soon"
    only_429 = "429 too many requests"
    bare_invalid = "authenticate failed: invalid credential"
    unknown = "socket hang up"
    trap = "401 invalid token (see your organization limit policy)"
    weekly = "You've hit your weekly limit · resets Sep 1, 9pm (Asia/Jerusalem)"
    session = "You've hit your session limit · resets 4:40pm"
    per_model = "You've hit your Opus limit · resets tomorrow"
    rate_class = "RateLimitError: requests per minute exceeded"

    check("429 plus invalid is ALIVE (limit takes precedence)",
          lambda: eq(fallback_probe.classify(1, combined), fallback_probe.LIMITED))
    check("429-only response is ALIVE", lambda: eq(
        fallback_probe.classify(1, only_429), fallback_probe.LIMITED))
    check("bare invalid response is DEAD", lambda: eq(
        fallback_probe.classify(1, bare_invalid), fallback_probe.DEAD))
    check("unclassifiable response is UNKNOWN", lambda: eq(
        fallback_probe.classify(1, unknown), fallback_probe.UNKNOWN))
    check("limit-policy prose does not revive a dead key", lambda: eq(
        fallback_probe.classify(1, trap), fallback_probe.DEAD))
    check("real weekly-limit response is authenticated proof of life", lambda: eq(
        fallback_probe.classify(1, weekly), fallback_probe.LIMITED))
    check("real session-limit response is authenticated proof of life", lambda: eq(
        fallback_probe.classify(1, session), fallback_probe.LIMITED))
    check("unobserved possessive per-model limit is authenticated proof of life", lambda: eq(
        fallback_probe.classify(1, per_model), fallback_probe.LIMITED))
    check("shared rate matcher covers SDK rate-limit class spelling", lambda: eq(
        fallback_probe.classify(1, rate_class), fallback_probe.LIMITED))
    check("successful response is ALIVE with capacity", lambda: eq(
        fallback_probe.classify(0, "ok"), fallback_probe.ALIVE))

    # The first three cases are the old probe's demonstrable blind spots.
    check("control: old classifier misread combined 429 plus invalid", lambda: eq(
        legacy(1, combined), fallback_probe.DEAD))
    check("control: old classifier left 429-only unknown", lambda: eq(
        legacy(1, only_429), fallback_probe.UNKNOWN))
    check("control: old classifier missed a generic invalid rejection", lambda: eq(
        legacy(1, bare_invalid), fallback_probe.UNKNOWN))

    # Value replacement proves the probe calls the shared battle-tested limit
    # detector rather than keeping a second local response list.
    shared = limits.is_limit_message
    try:
        limits.is_limit_message = lambda _blob: False  # type: ignore[assignment]
        check("control: removing shared detector reopens the 429-plus-invalid bug", lambda: eq(
            fallback_probe.classify(1, combined), fallback_probe.DEAD))
    finally:
        limits.is_limit_message = shared  # type: ignore[assignment]

    # A value replacement, not an exception: prove the UNKNOWN check would
    # catch a classifier that started treating a transport failure as dead.
    real = fallback_probe._DEAD_RE
    try:
        fallback_probe._DEAD_RE = re.compile(r"socket hang up", re.I)
        check("control: UNKNOWN branch detects a value-mutated dead matcher", lambda: eq(
            fallback_probe.classify(1, unknown), fallback_probe.DEAD))
    finally:
        fallback_probe._DEAD_RE = real


def isolated_process() -> None:
    print("\n§2 isolated child environment")
    real_run = fallback_probe.subprocess.run
    seen: dict[str, object] = {}
    original_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = "C:\\live-config-must-not-be-used"

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        env = kwargs["env"]
        seen["cfg"] = env["CLAUDE_CONFIG_DIR"]
        seen["no_api"] = "ANTHROPIC_API_KEY" not in env
        seen["no_auth"] = "ANTHROPIC_AUTH_TOKEN" not in env
        seen["token_only_in_env"] = env["CLAUDE_CODE_OAUTH_TOKEN"] == "unit-token"
        return SimpleNamespace(returncode=1, stdout="rate limit", stderr="")

    try:
        fallback_probe.subprocess.run = fake_run
        state = fallback_probe.probe("unit-token", ["resolved-cli"])
    finally:
        fallback_probe.subprocess.run = real_run
        if original_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = original_cfg
    check("probe classifies the child response without exposing it", lambda: eq(
        state, fallback_probe.LIMITED))
    check("probe uses the supervisor-resolved argv plus one cheap Haiku request", lambda: eq(
        seen["argv"], ["resolved-cli", "-p", "say ok", "--model", "haiku"]))
    check("probe strips competing credentials", lambda: eq(
        (seen["no_api"], seen["no_auth"], seen["token_only_in_env"]),
        (True, True, True)))
    check("fresh config directory is removed after the child exits", lambda: eq(
        os.path.exists(str(seen["cfg"])), False, str(seen["cfg"])))


def registry_and_schedule() -> None:
    print("\n§3 registry facts and durable hourly scheduling")
    reset()
    old_identity = accounts.resolve_key_identity
    old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    accounts.resolve_key_identity = lambda _kid: ""  # type: ignore[assignment]
    os.environ["CLAUDE_CONFIG_DIR"] = "C:\\backend-registration-config"
    try:
        rec = accounts.register_key("second-test-token", "C:\\mint-session")
    finally:
        accounts.resolve_key_identity = old_identity
        if old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_cfg
    row = next(k for k in accounts.load()["keys"] if k["id"] == rec["id"])
    check("registration stores an observed registered_at, not a fictional creation time",
          lambda: eq(bool(re.fullmatch(r".+Z", str(row.get("registered_at") or ""))), True))
    check("operator mint provenance remains its own optional field", lambda: eq(
        row.get("mint_config_dir"), "C:\\mint-session"))
    check("backend registration session is separately and honestly named", lambda: eq(
        row.get("registered_from_config_dir"), "C:\\backend-registration-config"))
    check("unknown mint provenance is absent rather than guessed", lambda: eq(
        "mint_config_dir" in next(k for k in accounts.load()["keys"] if k["id"] == KID),
        False))
    old_live = accounts.live_identity
    accounts.live_identity = lambda: {"uuid": "", "email": ""}  # type: ignore[assignment]
    try:
        readout_row = next(k for k in accounts.readout()["keys"] if k["id"] == rec["id"])
    finally:
        accounts.live_identity = old_live
    check("readout carries registration facts without relabelling them as mint facts", lambda: eq(
        (readout_row["registered_at"], readout_row["mint_config_dir"],
         readout_row["registered_from_config_dir"]),
        (row["registered_at"], "C:\\mint-session", "C:\\backend-registration-config")))

    reset()
    calls: list[str] = []

    def limited(_token: str) -> str:
        calls.append("limited")
        return fallback_probe.LIMITED

    start = 10_000.0
    first = accounts.probe_fallback_keys(start, probe=limited)
    second = accounts.probe_fallback_keys(start + 3599, probe=limited)
    third = accounts.probe_fallback_keys(start + 3600, probe=lambda _t: fallback_probe.UNKNOWN)
    live = accounts._liveness_record(accounts.load(), KID)
    check("one key is probed once per hour, including across due-loop calls", lambda: eq(
        calls, ["limited"]))
    check("due result records an authenticated-but-limited key", lambda: eq(
        first, [{"id": KID, "state": fallback_probe.LIMITED}]))
    check("not-yet-due call spends nothing", lambda: eq(second, []))
    check("UNKNOWN updates only the cadence claim, preserving prior verdict", lambda: eq(
        (third, live.get("state"), live.get("checked_at")),
        ([{"id": KID, "state": fallback_probe.UNKNOWN}], fallback_probe.LIMITED,
         start + 3600)))

    reset()
    accounts.probe_fallback_keys(start, probe=lambda _t: fallback_probe.DEAD)
    real_live = accounts.live_identity
    accounts.live_identity = lambda: {"uuid": "", "email": ""}  # type: ignore[assignment]
    try:
        routed = accounts.resolve("haiku", start + 1)
    finally:
        accounts.live_identity = real_live
    check("confirmed dead is retained for inspection but no longer routed", lambda: eq(
        routed["account"], None, repr(routed)))


if __name__ == "__main__":
    classify_cases()
    isolated_process()
    registry_and_schedule()
    if FAIL:
        print(f"\n{len(FAIL)} FAILED")
        for label, why in FAIL:
            print(f"  {label}: {why}")
        raise SystemExit(1)
    print(f"\nALL {PASS} CHECKS PASS")
