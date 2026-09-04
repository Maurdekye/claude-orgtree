"""Codex CLI version drift, and the refusal message that used to blame it on
the account.

    python backend/tests/test_codex_cli_version.py

No real CLI, account or network is touched: every case drives
`codex_cli_version_status` through a synthetic `status` dict and a temporary
CODEX_HOME, and the inventory is replaced at its module seam.

WHAT THIS IS DEFENDING (2026-09-04, live). The pinned Codex CLI was 0.150.1,
installed 28 August and never refreshed by any script in this repo. OpenAI's
`model/list` gates rollout models on the reporting client version, so that pin
returned 9 model ids while 0.153.0 on PATH returned the same 9 plus
`gpt-6-astra` — same account, same auth, same code. The tier was invisible and
the refusal message said "the signed-in Codex account does not offer model
'gpt-6-astra'", which was FALSE and sent three investigations after the
account, the model id and the fetch, all of which were fine.
"""

import calendar
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-cliver-")
os.environ["ORGTREE_ANTIGRAVITY"] = os.path.join(
    os.environ["ORGTREE_DATA"], "missing-agy")

from orgtree import providers, turnusage  # noqa: E402

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def home(latest="0.153.3", checked="2026-09-04T19:43:25.288470200Z",
         extra=None):
    """A CODEX_HOME carrying the CLI's own update check.

    The default `checked` stamp is the REAL shape the CLI writes — nanosecond
    precision. `datetime.fromisoformat` rejects 9 fractional digits before
    3.11, so a naive parser fails on every genuine file.
    """
    d = tempfile.mkdtemp(prefix="codex-home-")
    doc = {"dismissed_version": None}
    if latest is not None:
        doc["latest_version"] = latest
    if checked is not None:
        doc["last_checked_at"] = checked
    if extra:
        doc.update(extra)
    with open(os.path.join(d, "version.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return d


def status(version="0.150.1", codex_home=None, installed=True,
           source="pin", path=r"C:\data\codex\...\codex.exe"):
    return {"installed": installed, "path": path if installed else None,
            "source": source, "version": version,
            "codex_home": codex_home if codex_home is not None else home(),
            "connected": True, "email": "x@example.com", "kind": "chatgpt"}


def utc(text):
    """Epoch for a UTC wall time. `time.mktime` would read it as LOCAL, which
    on this host is UTC+3 — enough to put `now` BEFORE the fixture's own
    `last_checked_at` and clamp every age to zero."""
    return calendar.timegm(time.strptime(text, "%Y-%m-%d %H:%M:%S"))


#: after the fixtures' 19:43:25Z update check, so ages are positive
NOW = utc("2026-09-04 20:30:00")


# ── the detector can actually detect (positive control) ────────────────────

def detects_a_real_drift():
    """THE POSITIVE CONTROL. The exact live pairing — pinned 0.150.1 against an
    available 0.153.3 — must come back True. An instrument that reports
    "nothing found" has to prove it can find something."""
    v = providers.codex_cli_version_status(status("0.150.1"), now=NOW)
    assert v["update_available"] is True, v
    assert v["evidence"] == "outdated", v
    assert v["version"] == "0.150.1" and v["latest"] == "0.153.3", v


def clears_when_current():
    """…and must come back False once the drift is gone, or it is a light that
    is always on."""
    for mine in ("0.153.3", "0.154.0"):
        v = providers.codex_cli_version_status(status(mine), now=NOW)
        assert v["update_available"] is False, (mine, v)
        assert v["evidence"] == "current", (mine, v)


def platform_suffix_is_not_a_downgrade():
    """`_codex_version` reads the PLATFORM package and answers
    `0.150.1-win32-x64`; that must order as 0.150.1, not fail to parse."""
    v = providers.codex_cli_version_status(
        status("0.150.1-win32-x64"), now=NOW)
    assert v["update_available"] is True, v
    same = providers.codex_cli_version_status(
        status("0.153.3-win32-x64"), now=NOW)
    assert same["update_available"] is False, same


# ── the nanosecond trap (red-then-green) ───────────────────────────────────

def nanosecond_stamp_parses():
    """⚠ THE TRAP. The CLI writes `…T19:43:25.288470200Z` — NINE fractional
    digits. Without clamping to six, `fromisoformat` raises on every real
    file, `checked_at` is None, and the check then reports "undated" forever
    while looking perfectly healthy. This is the assertion that fails if the
    clamp is removed."""
    v = providers.codex_cli_version_status(status("0.150.1"), now=NOW)
    assert v["checked_at"] is not None, v
    assert v["checked_at"].startswith("2026-09-04T19:43:25"), v
    assert v["check_age"] is not None and v["check_age"] > 0, v
    # and the verdict actually got past the date gate
    assert v["update_available"] is True, v


def microsecond_and_bare_stamps_also_parse():
    for stamp in ("2026-09-04T19:43:25.288470Z", "2026-09-04T19:43:25Z",
                  "2026-09-04T19:43:25+00:00", "2026-09-04T19:43:25"):
        v = providers.codex_cli_version_status(
            status("0.150.1", home(checked=stamp)), now=NOW)
        assert v["checked_at"] is not None, (stamp, v)
        assert v["update_available"] is True, (stamp, v)


# ── it declares itself inert instead of passing quietly ────────────────────

def blind_cases_are_none_not_false():
    """`update_available` is a TRISTATE. Every case where we cannot tell must
    say so — `None` — because a drift check that answers "up to date" while
    blind is worse than no check at all."""
    cases = [
        ("no-cli", providers.codex_cli_version_status(
            status(installed=False), now=NOW)),
        ("no-update-check", providers.codex_cli_version_status(
            status("0.150.1", tempfile.mkdtemp()), now=NOW)),
        ("no-update-check", providers.codex_cli_version_status(
            status("0.150.1", home(latest=None)), now=NOW)),
        ("unparsable-version", providers.codex_cli_version_status(
            status("unknown"), now=NOW)),
        ("unparsable-version", providers.codex_cli_version_status(
            status("0.150.1", home(latest="rolling")), now=NOW)),
        ("update-check-undated", providers.codex_cli_version_status(
            status("0.150.1", home(checked=None)), now=NOW)),
        ("update-check-undated", providers.codex_cli_version_status(
            status("0.150.1", home(checked="not-a-date")), now=NOW)),
    ]
    for expected, v in cases:
        assert v["update_available"] is None, (expected, v)
        assert v["evidence"] == expected, (expected, v)


def a_stale_update_check_is_not_evidence():
    """The CLI rewrites version.json during ordinary use. If it has not run in
    a week, "no update available" is silence, not a finding — and it must not
    be reported as one. The boundary is asserted from BOTH sides so the
    threshold is not free."""
    # ages run from the FIXTURE's own last_checked_at, not from NOW
    checked = utc("2026-09-04 19:43:25")
    span = providers.CODEX_VERSION_CHECK_MAX_AGE
    fresh = providers.codex_cli_version_status(status("0.153.3"), now=NOW)
    assert fresh["evidence"] == "current", fresh
    old = providers.codex_cli_version_status(
        status("0.153.3"), now=checked + span + 60)
    assert old["update_available"] is None, old
    assert old["evidence"] == "update-check-stale", old
    just_inside = providers.codex_cli_version_status(
        status("0.153.3"), now=checked + span - 60)
    assert just_inside["evidence"] == "current", just_inside
    # the threshold gates the DRIFT verdict too, not just the clean case
    stale_drift = providers.codex_cli_version_status(
        status("0.150.1"), now=checked + span + 60)
    assert stale_drift["update_available"] is None, stale_drift
    assert stale_drift["evidence"] == "update-check-stale", stale_drift


# ── it says WHICH build it measured ────────────────────────────────────────

def it_names_the_binary_and_where_it_came_from():
    """`codex_path()` resolves env > `<ORGTREE_DATA>/codex` pin > PATH, and the
    pin lives under the DATA ROOT — so two processes with different
    ORGTREE_DATA run different binaries. A version with no provenance is the
    same class of mistake as the message this file replaces."""
    for source, phrase in (("pin", "pinned under the data root"),
                           ("path", "found on PATH"),
                           ("env", "ORGTREE_CODEX override")):
        st = status("0.150.1", source=source, path=rf"C:\{source}\codex.exe")
        v = providers.codex_cli_version_status(st, now=NOW)
        assert v["source"] == source and v["path"] == st["path"], v
        note = providers.codex_cli_version_note(st, now=NOW)
        assert phrase in note, (source, note)
        assert st["path"] in note, (source, note)
        assert "0.150.1" in note, (source, note)
    assert providers.codex_cli_version_note(
        status(installed=False), now=NOW) == ""


def the_note_offers_the_upgrade_only_on_evidence():
    outdated = providers.codex_cli_version_note(status("0.150.1"), now=NOW)
    assert "0.153.3 is available" in outdated, outdated
    current = providers.codex_cli_version_note(status("0.153.3"), now=NOW)
    assert "available" not in current, current
    blind = providers.codex_cli_version_note(
        status("0.150.1", tempfile.mkdtemp()), now=NOW)
    assert "available" not in blind, blind
    assert "0.150.1" in blind, blind


# ── the refusal message no longer blames the account ───────────────────────

def refusal_names_the_cli_not_the_account():
    """THE HEADLINE. `model-missing` must not assert anything about what the
    account offers — that claim was false on 2026-09-04 and cost three
    investigations."""
    st = status("0.150.1")
    saved = providers.codex_model_inventory
    try:
        providers.codex_model_inventory = lambda **kw: {
            "available": True, "models": ["gpt-5.6-sol", "gpt-reserve"],
            "error": None}
        got = providers.conditional_codex_availability("astra", status=st)
    finally:
        providers.codex_model_inventory = saved
    assert got["enabled"] is False and got["evidence"] == "model-missing", got
    reason = got["reason"]
    # the false sentence, gone
    assert "account does not offer" not in reason, reason
    assert "does not offer model" not in reason, reason
    # the real variable, named
    assert "gpt-6-astra" in reason, reason
    assert "0.150.1" in reason, reason
    assert "codex CLI" in reason, reason
    assert "0.153.3" in reason, reason


def refusal_still_passes_when_the_model_is_there():
    """Positive control for the message path: with the id present the gate
    opens and says nothing about versions."""
    st = status("0.150.1")
    saved = providers.codex_model_inventory
    try:
        providers.codex_model_inventory = lambda **kw: {
            "available": True, "models": ["gpt-6-astra"], "error": None}
        got = providers.conditional_codex_availability("astra", status=st)
    finally:
        providers.codex_model_inventory = saved
    assert got == {"enabled": True, "evidence": "model-present",
                   "reason": None}, got


def an_unavailable_inventory_still_reports_its_own_error():
    """A fetch failure is a different fault from a missing model and must keep
    its own message rather than being absorbed into the CLI-version story."""
    st = status("0.150.1")
    saved = providers.codex_model_inventory
    try:
        providers.codex_model_inventory = lambda **kw: {
            "available": False, "models": [], "error": "boom"}
        got = providers.conditional_codex_availability("astra", status=st)
    finally:
        providers.codex_model_inventory = saved
    assert got["evidence"] == "inventory-unavailable", got
    assert got["reason"] == "boom", got


# ── the reset-time truncation ──────────────────────────────────────────────

def reset_times_round_instead_of_truncating():
    """A fixed weekly boundary rendered `08:59:59Z` on one poll and
    `09:00:00Z` on the next, because the upstream recomputes `resets_at` with
    microsecond jitter and `timespec="seconds"` FLOORS. That made a real early
    reset look like a reporting bug."""
    boundary = utc("2026-09-05 09:00:00")
    below = turnusage._iso(boundary - 0.02)
    above = turnusage._iso(boundary + 0.02)
    assert below == above, (below, above)
    assert below.endswith("09:00:00Z"), below
    # and it still floors nothing it should not: a value genuinely mid-second
    # rounds to the NEAR second, not up
    assert turnusage._iso(boundary + 0.4).endswith("09:00:00Z")
    assert turnusage._iso(boundary + 0.6).endswith("09:00:01Z")


for label, fn in [
    ("the detector finds the real 0.150.1 → 0.153.3 drift", detects_a_real_drift),
    ("…and clears once the CLI is current", clears_when_current),
    ("a -win32-x64 platform suffix still orders correctly",
     platform_suffix_is_not_a_downgrade),
    ("a NANOSECOND last_checked_at parses (the real file's shape)",
     nanosecond_stamp_parses),
    ("microsecond, bare and offset stamps parse too",
     microsecond_and_bare_stamps_also_parse),
    ("every blind case reports None, never 'up to date'",
     blind_cases_are_none_not_false),
    ("an update check older than a week stops being evidence",
     a_stale_update_check_is_not_evidence),
    ("the status names WHICH binary and how it was resolved",
     it_names_the_binary_and_where_it_came_from),
    ("the note offers an upgrade only when one is evidenced",
     the_note_offers_the_upgrade_only_on_evidence),
    ("model-missing names the CLI version, NOT the account",
     refusal_names_the_cli_not_the_account),
    ("…and still opens the gate when the model is present",
     refusal_still_passes_when_the_model_is_there),
    ("an unavailable inventory keeps its own error",
     an_unavailable_inventory_still_reports_its_own_error),
    ("reset times round to the boundary instead of flooring below it",
     reset_times_round_instead_of_truncating),
]:
    check(label, fn)

print(f"\nall {PASS} checks passed")
