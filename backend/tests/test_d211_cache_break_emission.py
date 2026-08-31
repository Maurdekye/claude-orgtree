"""D-211 — the cache-break sentinel actually REACHES warm.jsonl.

D-206 set CLAUDE_CODE_IS_COWORK=1 and the org believed the diagnoser was on
for a full day. It was not. IS_COWORK gates the diagnoser's cross-process
state file and its telemetry; it does NOT gate the emission. Measured against
the shipped 2.1.241 binary, the sentinel goes through the CLI's debug FILE
logger as `E(line, {level:"warn"})`, and that logger drops everything unless
debug mode is on — and reaches STDERR, the only thing warmpool reads, only
when argv carries `--debug-to-stderr` / `-d2e`.

That is the lesson this file exists to keep: A FEATURE FLAG THAT ENABLES A
SUBSYSTEM'S STATE WITHOUT ENABLING ITS OUTPUT LOOKS EXACTLY LIKE A WORKING
FEATURE FROM THE OUTSIDE. The only thing that told us apart was a positive
control, so the positive control is now a test.

    §1  both halves of the gate are on the REAL spawn path — the argv flag
        from _build_cmd and the level cap from spawn_env
    §2  THE HONESTY GATE: a sentinel emitted on a real subprocess's real
        stderr, under the REAL argv and the REAL spawn env, lands as a
        cache-break row in warm.jsonl — and does NOT when the flag is
        removed. §2 is the check that would have caught D-206's miss.
    §3  the capture stays a filter, not a firehose: non-sentinel stderr is
        never journaled, and both stderr owners are wired

The §2 stub CLI implements the gate exactly as measured from the binary. If a
future CLI changes that contract this file goes green while production goes
silent — so the stub's docstring names the binary offsets the contract came
from, and re-deriving it is the price of a CLI bump.

Hermetic: throwaway data root + HOME, no listener, no Docker, no real CLI.

    python backend/tests/test_d211_cache_break_emission.py [-v]
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-d211-")
_HOME = os.path.join(_TMP, "home")
_DATA = os.path.join(_TMP, "data")
os.makedirs(_HOME, exist_ok=True)
os.makedirs(_DATA, exist_ok=True)
# hub guard — a bare data root must never let a stray daemon register fixture
# orgs on the real hub (same rationale as test_d206_env)
with open(os.path.join(_DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ.update({
    "ORGTREE_DATA": _DATA,
    "HOME": _HOME,
    "USERPROFILE": _HOME,
    "ORGTREE_WARM": "0",
    "ORGTREE_STEER_HOOK": "0",
    "ORGTREE_PORT": "7418",                      # never bound
})
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

from orgtree import sandbox as sbx, store          # noqa: E402
from orgtree import supervisor as S                # noqa: E402
from orgtree import warmpool as W                  # noqa: E402
from orgtree.ledger import USER                    # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
VERBOSE = "-v" in sys.argv

JOURNAL = os.path.join(_DATA, "journals", "warm.jsonl")

# The line the real CLI builds, verbatim in shape (binary offset ~310820497):
#   `[PROMPT CACHE BREAK] ${cause} [source=${qs}, call #${n}, cache read: …]`
SENTINEL_LINE = (
    "[PROMPT CACHE BREAK] likely server-side (prompt unchanged, <5min gap) "
    "[source=sdk, call #3, cache read: 43702 → 29904, creation: 16729]")
NOISE_LINE = "ordinary stderr chatter that must never be journaled"


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                        # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


class _StubOrg:
    """The minimum spawn_env touches on an org: `.d` (slug, no api_key)."""
    def __init__(self, slug="t", **d):
        self.d = {"slug": slug, **d}


def _unsandboxed(fn):
    real = sbx.is_sandboxed
    sbx.is_sandboxed = lambda _o: False
    try:
        return fn()
    finally:
        sbx.is_sandboxed = real


# ── a fixture org, so §1 can call the REAL argv builder ────────────────────
_org = store.create_org("d211 cache break")
_org.hire(USER, None, "haiku", 5, "boss", add_dirs=[],
          tools={"mcp": []}, org_visibility="full", charter="boss")
store.save_org(_org)
S.scratch_dir(_org.d["slug"], "boss")


def _real_argv() -> list[str]:
    return S._build_cmd(_org, "boss", write_ident=False)


# ── §1 both halves of the gate are on the real spawn path ──────────────────

def s1_argv_carries_debug_to_stderr():
    """Mutant that must fail here: delete "--debug-to-stderr" from the turn
    spawn's cmd list in _build_cmd. That single deletion is precisely the
    state D-206 shipped in, and it silenced the instrument completely."""
    argv = _real_argv()
    assert "--debug-to-stderr" in argv or "-d2e" in argv, (
        "the turn spawn does not route the CLI's cache-break warning to "
        "stderr — warmpool reads stderr and ONLY stderr, so the journal "
        "would stay empty no matter how many breaks occur")


def s1_spawn_env_caps_the_log_level():
    """Mutant that must fail here: delete the CLAUDE_CODE_DEBUG_LOG_LEVEL
    line in spawn_env. Not a correctness failure — a cost one: measured 187
    stderr lines/turn uncapped vs 2 capped, and err_tail (maxlen 200) is
    then pure debug noise instead of real errors."""
    env = _unsandboxed(lambda: S.spawn_env(_StubOrg()))
    assert env.get("CLAUDE_CODE_DEBUG_LOG_LEVEL") == "warn", (
        "the debug logger is uncapped; the sentinel still arrives but "
        "buried in ~187 lines a turn, which evicts err_tail's real errors")


def s1_level_is_injected_not_inherited():
    """clean_env() strips every CLAUDE_CODE_* var, so the value must come
    from the injection AFTER the strip. Mutant that must fail here: move the
    injection above clean_env()."""
    os.environ["CLAUDE_CODE_DEBUG_LOG_LEVEL"] = "inherited-garbage"
    try:
        env = _unsandboxed(lambda: S.spawn_env(_StubOrg()))
        assert env.get("CLAUDE_CODE_DEBUG_LOG_LEVEL") == "warn", (
            "the level rode the ambient environment instead of the "
            "injection — clean_env would have stripped a real value")
    finally:
        os.environ.pop("CLAUDE_CODE_DEBUG_LOG_LEVEL", None)


def s1_sandboxed_spawn_untouched():
    """Mutant that must fail here: move the injection above the sandbox
    early-return. A sandboxed org's credential and env belong to its
    container, not to the host-side spawn."""
    real = sbx.is_sandboxed
    sbx.is_sandboxed = lambda _o: True
    try:
        env = S.spawn_env(_StubOrg())
    finally:
        sbx.is_sandboxed = real
    assert "CLAUDE_CODE_DEBUG_LOG_LEVEL" not in env
    assert "CLAUDE_CODE_IS_COWORK" not in env


def s1_d206_half_still_present():
    """D-211 ADDS to D-206, it does not replace it: IS_COWORK still carries
    the cross-process state file. Losing it would degrade the diagnoser to
    in-process-only baselines without any visible symptom."""
    env = _unsandboxed(lambda: S.spawn_env(_StubOrg()))
    assert env.get("CLAUDE_CODE_IS_COWORK") == "1"


# ── §2 THE HONESTY GATE ────────────────────────────────────────────────────

_STUB = os.path.join(_TMP, "stub_cli.py")
with open(_STUB, "w", encoding="utf-8") as _f:
    _f.write('''"""A stand-in for claude.exe that reproduces the MEASURED emission gate.

Derived from the shipped 2.1.241 binary:
  * class f9s.log()      (offset 301130509) — level filter, then shouldLog(),
                          then `if (this.toStderr) writeToStderr(...)` else a
                          FILE. So stderr requires toStderr.
  * f9s constructor      — toStderr = argv has --debug-to-stderr | -d2e
                          minLevel = env CLAUDE_CODE_DEBUG_LOG_LEVEL or debug
  * the emission site    (offset 310820497) — E(line, {level:"warn"})
Levels: verbose 0, debug 1, info 2, warn 3, error 4.
"""
import os
import sys

SENTINEL = os.environ["D211_SENTINEL"]
NOISE = os.environ["D211_NOISE"]
LEVELS = {"verbose": 0, "debug": 1, "info": 2, "warn": 3, "error": 4}

argv = sys.argv[1:]
to_stderr = "--debug-to-stderr" in argv or "-d2e" in argv
min_level = LEVELS.get((os.environ.get("CLAUDE_CODE_DEBUG_LOG_LEVEL") or
                        "debug").lower().strip(), 1)

# Noise is written unconditionally: a real CLI puts plenty on stderr that is
# not ours, and the capture must stay a filter rather than a firehose.
#
# Written as UTF-8 BYTES on purpose: the real sentinel carries a U+2192
# arrow between the two cache-read figures, and the real CLI emits UTF-8.
# warmpool's Popen decodes with encoding="utf-8", errors="replace"
# (warmpool.py:996) — _spawn_stub mirrors that, so this stub must not go
# through Python's default console encoding or the arrow is mangled before
# the code under test ever sees it.
out = sys.stderr.buffer
out.write((NOISE + "\\n").encode("utf-8"))
if to_stderr and LEVELS["warn"] >= min_level:
    out.write((SENTINEL + "\\n").encode("utf-8"))
out.flush()
''')


def _spawn_stub(argv_tail, env_extra=None):
    """Spawn the stub CLI carrying the REAL argv tail and the REAL spawn env.

    Only argv[0] (the claude binary) is swapped for the stub — every flag
    _build_cmd produced is passed through untouched, which is what makes
    deleting --debug-to-stderr from supervisor.py turn §2 red.
    """
    env = _unsandboxed(lambda: S.spawn_env(_StubOrg()))
    env = {**os.environ, **env}
    env["D211_SENTINEL"] = SENTINEL_LINE
    env["D211_NOISE"] = NOISE_LINE
    env.update(env_extra or {})
    # decoding mirrors the production spawn exactly (warmpool.py:996) — the
    # sentinel contains a U+2192, so a test that decoded with the platform
    # default would fail on a line production handles correctly
    return subprocess.Popen([sys.executable, _STUB] + argv_tail,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            env=env)


class _Shim:
    """Only what WarmProc._pump_err touches. The method under test is the
    REAL deployed one, called unbound on this shim."""
    def __init__(self, proc, nid, sid):
        self.proc = proc
        self.slug, self.nid, self.sid = "t", nid, sid
        self.err_tail: list[str] = []


def _pump(proc, nid, sid) -> _Shim:
    shim = _Shim(proc, nid, sid)
    t = threading.Thread(target=W.WarmProc._pump_err, args=(shim,))
    t.start()
    t.join(timeout=30)
    proc.wait(timeout=30)
    return shim


def _rows() -> list[dict]:
    if not os.path.exists(JOURNAL):
        return []
    with open(JOURNAL, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _breaks(nid=None) -> list[dict]:
    return [r for r in _rows()
            if r.get("kind") == "cache-break"
            and (nid is None or r.get("nid") == nid)]


def s2_sentinel_reaches_the_journal_through_the_real_argv():
    """THE gate. Real _build_cmd argv + real spawn_env + real _pump_err +
    real _journal → a cache-break row in warm.jsonl.

    Mutant that must fail here: remove "--debug-to-stderr" from _build_cmd.
    That is not hypothetical — it is exactly what production ran all of
    2026-08-30 while reporting zero breaks."""
    proc = _spawn_stub(_real_argv()[1:])
    _pump(proc, "live", "sid-live")
    got = _breaks("live")
    assert len(got) == 1, (
        f"expected exactly one cache-break row, got {len(got)} — the "
        f"sentinel did not survive the real spawn path")
    row = got[0]
    assert row["source"] == "warm-stderr"
    assert row["session_id"] == "sid-live"
    assert row["line"] == SENTINEL_LINE, "the raw evidence line was altered"
    assert row["truncated"] is False
    assert isinstance(row.get("pid"), int), (
        "no pid recorded — attribution to a process is part of the evidence")


def s2_goes_red_without_the_flag():
    """The negative half. Same everything, flag filtered out of the argv →
    the stub emits nothing and the journal stays clean. Without this check
    §2 could pass on a stub that emits unconditionally, which would make the
    whole file decorative."""
    argv = [a for a in _real_argv()[1:]
            if a not in ("--debug-to-stderr", "-d2e")]
    proc = _spawn_stub(argv)
    _pump(proc, "noflag", "sid-noflag")
    assert _breaks("noflag") == [], (
        "a row was journaled with no stderr routing flag — the stub is not "
        "reproducing the CLI's measured gate, so §2 proves nothing")


def s2_goes_red_if_the_level_cap_is_wrong():
    """Control on the OTHER half: the level var must not be set somewhere
    that suppresses the sentinel. warn is the sentinel's own level, so
    anything stricter silences it. Mutant: set the value to "error"."""
    proc = _spawn_stub(_real_argv()[1:],
                       env_extra={"CLAUDE_CODE_DEBUG_LOG_LEVEL": "error"})
    _pump(proc, "toostrict", "sid-strict")
    assert _breaks("toostrict") == [], (
        "the stub ignored the level cap — it cannot then testify that our "
        "chosen 'warn' is safe")


# ── §3 the capture is a filter, and both owners are wired ──────────────────

def s3_ordinary_stderr_is_never_journaled():
    """General stderr may carry sensitive material; only the sentinel is
    durable evidence. Mutant that must fail here: drop the marker test in
    journal_cache_break_lines."""
    assert all(NOISE_LINE not in r.get("line", "") for r in _breaks()), (
        "non-sentinel stderr leaked into the durable journal")


def s3_cold_owner_is_wired_too():
    """A cold (non-pooled) turn owns its stderr separately. Missing this
    owner removes exactly the population the instrument explains — a cold
    spawn is the likeliest thing to break cache. Mutant that must fail here:
    make read_cold_stderr call proc.stderr.read() without journaling."""
    proc = _spawn_stub(_real_argv()[1:])
    err = W.read_cold_stderr(proc, "t", "coldnode", "sid-cold")
    proc.wait(timeout=30)
    assert SENTINEL_LINE in err, "read_cold_stderr must still return the text"
    got = _breaks("coldnode")
    assert len(got) == 1 and got[0]["source"] == "cold-stderr", (
        "the cold stderr owner did not journal the sentinel")


def s3_marker_matches_the_shipped_cli():
    """The one constant that cannot drift silently. If a CLI bump changes
    the sentinel this stays green while production goes quiet — so it is
    pinned here as the thing to re-derive on every CLI bump."""
    assert W.CACHE_BREAK_MARKER == "[PROMPT CACHE BREAK]"
    assert W.CACHE_BREAK_MARKER in SENTINEL_LINE


def main() -> int:
    print("§1 both halves of the emission gate are on the real spawn path")
    check("the turn argv carries --debug-to-stderr",
          s1_argv_carries_debug_to_stderr)
    check("spawn_env caps the debug logger at warn",
          s1_spawn_env_caps_the_log_level)
    check("the level is injected after clean_env, not inherited",
          s1_level_is_injected_not_inherited)
    check("a sandboxed spawn's env stays untouched",
          s1_sandboxed_spawn_untouched)
    check("D-206's IS_COWORK half is still set", s1_d206_half_still_present)
    print("§2 the honesty gate — the sentinel reaches warm.jsonl")
    check("real argv + real spawn env → a cache-break row",
          s2_sentinel_reaches_the_journal_through_the_real_argv)
    check("no stderr flag → no row (the check that would have caught D-206)",
          s2_goes_red_without_the_flag)
    check("a stricter level cap → no row", s2_goes_red_if_the_level_cap_is_wrong)
    print("§3 the capture is a filter, and both owners are wired")
    check("ordinary stderr is never journaled",
          s3_ordinary_stderr_is_never_journaled)
    check("the cold stderr owner journals too", s3_cold_owner_is_wired_too)
    check("the marker still matches the shipped CLI",
          s3_marker_matches_the_shipped_cli)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"d211: {PASS} passed · {len(FAIL)} FAILED")
        return 1
    print(f"d211: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
