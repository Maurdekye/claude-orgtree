"""gpt-reserve availability: detected from what MOVES, not from the login.

    python backend/tests/test_gpt_reserve_detection.py      (no pytest)

THE REPORT (user, 2026-09-02): "i had access to gpt-reserve in my codex limit
earlier today and could use it via the codex cli. however i no longer have
access. yet the reserve token still appears. detection of reserve usability
needs a better method of detection than what's currently done."

d7b98c7 had gated the tier on the Codex login KIND — a ChatGPT subscription
rather than an API key. That is a real precondition and it is nowhere near
sufficient, because the login did not change across the outage. Measured on
the reporting machine that evening, from the CLI's own session rollouts:

  16:06-16:38Z  model=gpt-reserve, limit_id "codex", 2% -> 8%, window 7 days,
                resetting Sep 9 — a pool of its OWN, while the account's plan
                window sat spent at 100% resetting Sep 7.
  19:15Z        model=gpt-reserve, limit_id "premium", no windows at all,
                credits balance "0" -> the turn failed `usage_limit_exceeded`.

Same machine, same `auth.json`, same `kind: "chatgpt"` throughout. What moved
was OpenAI's grant, and the two places it is visible locally are the CLI's own
model registry (`gpt-reserve` flipped to `visibility: "hide"` and left the
app-server's `model/list` while sol/terra/luna stayed listed) and the usage
board orgtree already polls.

    §1  the registry read — visibility, freshness, and the fallback
    §2  the usage board — "can a turn run at all", credits included
    §3  the rule both doors ask
    §4  controls: what would make the above vacuous
    §5  the follow-up: the ACCOUNT's exhaustion is every Codex tier's
"""

import datetime as _dt
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_ROOT = tempfile.mkdtemp(prefix="orgtree-reserve-detect-")
_HOME = os.path.join(_ROOT, "codex-home")
os.makedirs(_HOME)
# the dead-hub invariant every private data root in this directory carries:
# importing the net daemon must never register against the operator's roster
with open(os.path.join(_ROOT, "defaults.json"), "w", encoding="utf-8") as _f:
    json.dump({"net_hub_address": "http://127.0.0.1:9"}, _f)
with open(os.path.join(_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    json.dump({"tokens": {"id_token": "eyJh.e30.sig"}}, _f)
os.environ["ORGTREE_DATA"] = _ROOT
os.environ["CODEX_HOME"] = _HOME
os.environ["ORGTREE_CODEX"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fakecodex.py")

from orgtree import codex_limits, codex_models, providers    # noqa: E402

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def write_registry(*, reserve, fetched_at=None, extra=None):
    """Author `models_cache.json` the way the CLI writes it.

    `fetched_at` is deliberately the CLI's own 7-fractional-digit RFC3339,
    which `datetime.fromisoformat` refuses on 3.10 — the parser trims it, and
    a regression there would silently make every read look ancient and send
    this suite (and a real machine's UI poll) to the app-server every time.
    """
    when = fetched_at or _dt.datetime.now(_dt.timezone.utc)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S") + f".{when.microsecond:06d}900Z"
    codex_models.invalidate()
    with open(codex_models.registry_path(_HOME), "w", encoding="utf-8") as f:
        json.dump({"fetched_at": stamp, "etag": 'W/"x"', "models": [
            {"slug": "gpt-5.6-sol", "visibility": "list"},
            {"slug": "gpt-5.6-luna", "visibility": "list"},
            {"slug": "gpt-reserve", "visibility": reserve},
        ] + list(extra or [])}, f)


class NoAppServer:
    """No live read available — so a check about the FILE cannot be satisfied
    by accidentally spawning a codex."""

    def __enter__(self):
        self._f = codex_models._from_app_server
        codex_models._from_app_server = lambda status: None
        return self

    def __exit__(self, *a):
        codex_models._from_app_server = self._f


# --------------------------------------------------------- §1 the registry
print("\n§1  the CLI's model registry — the signal that moved")


def hidden_reads_as_not_offered():
    write_registry(reserve="hide")
    with NoAppServer():
        eq(codex_models.offers("gpt-reserve"), False, "hidden reserve")
        # the leg that must hold: a reader that answered False for everything
        # would 'fix' the report by taking the whole family away
        eq(codex_models.offers("gpt-5.6-sol"), True, "sol stays offered")


def listed_reads_as_offered():
    write_registry(reserve="list")
    with NoAppServer():
        eq(codex_models.offers("gpt-reserve"), True, "granted reserve")


def unknown_visibility_is_not_a_retraction():
    """Only an explicit "hide" withholds a model. A newer CLI inventing a
    third value must not silently retract a tier the user can still hire."""
    write_registry(reserve="something-new-in-2027")
    with NoAppServer():
        eq(codex_models.offers("gpt-reserve"), True, "unknown visibility")


def a_model_the_registry_never_names_is_not_offered():
    write_registry(reserve="list")
    with NoAppServer():
        eq(codex_models.offers("gpt-9-imaginary"), False, "absent slug")


def the_cli_timestamp_parses():
    """`fetched_at` beats mtime (the CLI rewrites the file on a conditional
    GET), and it arrives with seven fractional digits."""
    write_registry(reserve="list")
    with NoAppServer():
        board = codex_models.snapshot(force=True)
    assert board["age"] is not None and board["age"] < 60, board
    eq(board["stale"], False, "a just-written registry is not stale")
    eq(board["source"], "registry file", "read from the file, not a process")


def a_stale_file_reaches_for_the_live_read():
    """An old registry is not evidence — the app-server is asked instead, and
    its answer wins."""
    write_registry(reserve="hide", fetched_at=_dt.datetime.now(
        _dt.timezone.utc) - _dt.timedelta(
            seconds=codex_models.FILE_MAX_AGE + 120))
    saved = codex_models._from_app_server
    calls = []

    def live(status):
        calls.append(status)
        return {"offered": ["gpt-5.6-sol", "gpt-reserve"], "hidden": [],
                "observed_at": __import__("time").time(),
                "source": "app-server"}

    codex_models._from_app_server = live
    try:
        board = codex_models.snapshot(force=True)
    finally:
        codex_models._from_app_server = saved
    eq(len(calls), 1, "the live read was taken exactly once")
    eq(board["source"], "app-server", "the live answer won")
    eq("gpt-reserve" in board["offered"], True, "…including its verdict")


def no_evidence_answers_unknown_not_no():
    """The rule that keeps this from becoming a new outage: a machine whose
    registry cannot be read and whose app-server will not answer knows
    NOTHING, and `None` is not `False`."""
    codex_models.invalidate()
    os.remove(codex_models.registry_path(_HOME))
    with NoAppServer():
        eq(codex_models.offers("gpt-reserve"), None, "unknown")
    write_registry(reserve="list")


def a_corrupt_registry_is_unknown_too():
    codex_models.invalidate()
    with open(codex_models.registry_path(_HOME), "w", encoding="utf-8") as f:
        f.write("{not json at all")
    with NoAppServer():
        eq(codex_models.offers("gpt-reserve"), None, "corrupt file")
    write_registry(reserve="list")


check("visibility 'hide' means the CLI is not offering the model — and sol "
      "is still offered (the leg that must hold)", hidden_reads_as_not_offered)
check("visibility 'list' means it is", listed_reads_as_offered)
check("an unrecognised visibility is NOT a retraction",
      unknown_visibility_is_not_a_retraction)
check("a slug the registry never names is not offered",
      a_model_the_registry_never_names_is_not_offered)
check("the CLI's own 7-digit fetched_at parses, and dates the evidence",
      the_cli_timestamp_parses)
check("a stale registry pays for the live app-server read",
      a_stale_file_reaches_for_the_live_read)
check("no registry and no app-server is UNKNOWN, not 'no'",
      no_evidence_answers_unknown_not_no)
check("…and so is a corrupt one", a_corrupt_registry_is_unknown_too)


# -------------------------------------------------------- §2 the usage board
print("\n§2  the usage board — can a turn run on this account at all")


def board(*, percent, reached, credits, scoped_percent=0.0):
    """The shape `account/rateLimits/read` returns, as measured."""
    return {
        "rateLimits": {
            "limitId": "codex", "limitName": None,
            "primary": {"usedPercent": percent, "windowDurationMins": 10080,
                        "resetsAt": 1788764643},
            "secondary": None, "credits": credits,
            "spendControlReached": False, "planType": "prolite",
            "rateLimitReachedType": "rate_limit_reached" if reached else None,
        },
        "rateLimitsByLimitId": {"codex_bengalfox": {
            "limitId": "codex_bengalfox", "limitName": "GPT-5.3-Codex-Spark",
            "primary": {"usedPercent": scoped_percent,
                        "windowDurationMins": 300, "resetsAt": 1788403179},
            "secondary": None, "credits": None,
            "rateLimitReachedType": None, "planType": "prolite"}},
    }


NO_CREDITS = {"hasCredits": False, "unlimited": False, "balance": "0"}
SOME_CREDITS = {"hasCredits": True, "unlimited": False, "balance": "1200"}


def spent_account_reads_as_exhausted():
    got = codex_limits._normalize(
        board(percent=100, reached=True, credits=NO_CREDITS))
    eq(got["exhausted"], True, "spent, nothing to spend past it")
    eq(got["credits"]["balance"], "0", "the balance survives normalization")


def credits_are_a_way_through():
    got = codex_limits._normalize(
        board(percent=100, reached=True, credits=SOME_CREDITS))
    eq(got["exhausted"], False, "credits keep the account runnable")


def a_healthy_account_is_not_exhausted():
    got = codex_limits._normalize(
        board(percent=8, reached=False, credits=NO_CREDITS))
    eq(got["exhausted"], False, "8% is not spent")


def a_scoped_bucket_does_not_speak_for_the_account():
    """`codex_bengalfox` (Spark) is one model's own window. It being full says
    nothing about whether a reserve turn can run, and reading it as account
    state would take every Codex tier away for the wrong reason."""
    got = codex_limits._normalize(
        board(percent=8, reached=False, credits=NO_CREDITS,
              scoped_percent=100))
    eq(got["exhausted"], False, "a scoped window is not the account")


def a_stale_board_answers_unknown():
    codex_limits.invalidate()
    eq(codex_limits.exhausted(), None, "no board at all")
    codex_limits._cache.update(at=time.time(),
                               data=codex_limits._normalize(
                                   board(percent=8, reached=False,
                                         credits=NO_CREDITS)))
    eq(codex_limits.exhausted(), False, "a FRESH healthy board says so")
    codex_limits._cache["at"] -= codex_limits.MAX_EVIDENCE_AGE + 1
    eq(codex_limits.exhausted(), None,
       "…and the same board, gone stale, stops speaking")
    codex_limits.invalidate()


def a_spent_verdict_survives_its_own_window():
    """THE ASYMMETRY, and why it is sound: usage inside a window only ever
    goes UP. "Spent, resets Sep 7" is still true an hour later without anyone
    re-asking, so a spent verdict is trusted to `exhausted_until` rather than
    to MAX_EVIDENCE_AGE. Without this the family gate goes blind fifteen
    minutes after the last Codex turn — exactly when a user who has just
    burned their week is still trying to hire."""
    data = codex_limits._normalize(
        board(percent=100, reached=True, credits=NO_CREDITS))
    eq(data["exhausted_until"], 1788764643.0, "dated by the window's reset")
    codex_limits._cache.update(at=time.time(), data=data)
    eq(codex_limits.exhausted(), True, "fresh and spent")
    codex_limits._cache["at"] -= codex_limits.MAX_EVIDENCE_AGE + 1
    eq(codex_limits.exhausted(), True, "stale, but the window has not rolled")
    codex_limits.invalidate()


def a_spent_verdict_expires_when_the_window_rolls():
    """The other half — without it the extension above would be a
    permanent refusal that no reset could ever clear."""
    raw = board(percent=100, reached=True, credits=NO_CREDITS)
    raw["rateLimits"]["primary"]["resetsAt"] = time.time() - 60
    codex_limits._cache.update(
        at=time.time() - codex_limits.MAX_EVIDENCE_AGE - 1,
        data=codex_limits._normalize(raw))
    eq(codex_limits.exhausted(), None, "a rolled window stops speaking")
    codex_limits.invalidate()


def an_undated_verdict_gets_no_extension():
    raw = board(percent=100, reached=True, credits=NO_CREDITS)
    raw["rateLimits"]["primary"].pop("resetsAt")
    data = codex_limits._normalize(raw)
    eq(data["exhausted_until"], 0.0, "no reset to trust past")
    codex_limits._cache.update(
        at=time.time() - codex_limits.MAX_EVIDENCE_AGE - 1, data=data)
    eq(codex_limits.exhausted(), None, "so it ages out the ordinary way")
    codex_limits.invalidate()


check("a spent window with no credits is an exhausted account",
      spent_account_reads_as_exhausted)
check("…but credits are a way through", credits_are_a_way_through)
check("a healthy window is not exhausted", a_healthy_account_is_not_exhausted)
check("a full MODEL-scoped window is not the account's state",
      a_scoped_bucket_does_not_speak_for_the_account)
check("evidence too old to act on answers unknown, not exhausted",
      a_stale_board_answers_unknown)
check("…but a SPENT verdict is trusted until its window resets",
      a_spent_verdict_survives_its_own_window)
check("…and stops being trusted once it has",
      a_spent_verdict_expires_when_the_window_rolls)
check("an undated spent verdict gets no extension at all",
      an_undated_verdict_gets_no_extension)


# ------------------------------------------------------------- §3 the rule
print("\n§3  the rule both the chip and the hire gate ask")


def chatgpt(kind="chatgpt"):
    return {"installed": True, "connected": True, "kind": kind}


def api_key_never_holds_reserve():
    write_registry(reserve="list")
    with NoAppServer():
        got = providers.reserve_availability(chatgpt("api-key"))
    eq(got["enabled"], False, "api-key")
    eq(got["evidence"], "login-kind", "which signal refused")


def the_grant_going_away_is_seen():
    """THE REPORT. Login unchanged; the tier goes dark anyway."""
    write_registry(reserve="hide")
    codex_limits.invalidate()
    with NoAppServer():
        got = providers.reserve_availability(chatgpt())
    eq(got["enabled"], False, "withdrawn grant")
    eq(got["evidence"], "model-registry", "which signal refused")
    assert "comes back on its own" in (got["reason"] or ""), got


def a_spent_account_is_seen():
    write_registry(reserve="list")
    saved = codex_limits.exhausted
    codex_limits.exhausted = lambda: True
    try:
        with NoAppServer():
            got = providers.reserve_availability(chatgpt())
    finally:
        codex_limits.exhausted = saved
    eq(got["enabled"], False, "spent account")
    eq(got["evidence"], "usage-limits", "which signal refused")


def a_live_grant_passes():
    """THE LEG THAT MUST HOLD."""
    write_registry(reserve="list")
    codex_limits.invalidate()
    with NoAppServer():
        got = providers.reserve_availability(chatgpt())
    eq((got["enabled"], got["reason"]), (True, None), "live grant")


check("an api-key login can never hold reserve capacity",
      api_key_never_holds_reserve)
check("a WITHDRAWN grant is seen, on an unchanged ChatGPT login",
      the_grant_going_away_is_seen)
check("a spent account is seen too", a_spent_account_is_seen)
check("…and a live grant passes (the leg that must hold)", a_live_grant_passes)


# ---------------------------------------------------------- §4 the controls
print("\n§4  controls — what would make the above vacuous")


def the_gate_and_the_chip_are_one_function():
    """d7b98c7 wrote the reserve rule twice — once in `provider_hire_gate`,
    once inline in `providers_payload` — so the chip and the refusal could
    drift. Both now read `reserve_availability`, and this is the check that
    notices if a third copy appears."""
    import inspect
    from orgtree import api
    gate = inspect.getsource(api.provider_hire_gate)
    assert "reserve_availability" in gate, gate[-900:]
    assert 'kind") != "chatgpt"' not in gate.split("headless")[-1], (
        "the reserve branch is asking the login directly again")
    payload = inspect.getsource(providers.providers_payload)
    assert "reserve_availability" in payload, payload


def the_reserve_tier_name_lives_once():
    eq(providers.RESERVE_TIER, "gpt-reserve", "the tier this is all about")
    assert providers.RESERVE_TIER in providers.CODEX_TIERS, providers.CODEX_TIERS


def the_reserve_signal_never_touches_the_other_three():
    """Anti-vacuity for the whole file: the RESERVE-specific signals may not
    take luna/terra/sol away — the user's report was explicitly that those
    three kept working while gpt-reserve did not."""
    write_registry(reserve="hide")
    saved = codex_limits.exhausted
    codex_limits.exhausted = lambda: False       # the account itself is fine
    try:
        with NoAppServer():
            pay = providers.providers_payload(
                {"installed": True, "connected": True})
    finally:
        codex_limits.exhausted = saved
    cx = next(x for x in pay["providers"] if x["id"] == "openai")
    eq(cx["hire_enabled"], True, "the family stays hireable")
    eq(cx["reason"], None, "…with nothing to apologise for")
    eq(cx["reserve_hire_enabled"], False, "only reserve goes dark")


check("one implementation, asked by both doors",
      the_gate_and_the_chip_are_one_function)
check("the tier name is a constant, not a literal in two files",
      the_reserve_tier_name_lives_once)
check("the reserve-specific signals never leak onto luna/terra/sol",
      the_reserve_signal_never_touches_the_other_three)


# ------------------------------------------------- §5 the family follow-up
print("\n§5  the ACCOUNT's exhaustion, which is every Codex tier's")


def a_spent_account_darkens_the_whole_family():
    """Coordinator decision, 2026-09-02: extend the exhaustion signal past
    gpt-reserve. The four Codex tiers share ONE account and one set of usage
    windows, so a spent account is a spent Sol exactly as much as a spent
    reserve — and a hire into it is not a polite failure, it takes the seat
    first and fails on the agent's opening turn (measured: agent `timestamp`,
    19:38Z)."""
    write_registry(reserve="list")     # the grant is live; the money is not
    saved = codex_limits.exhausted
    codex_limits.exhausted = lambda: True
    try:
        with NoAppServer():
            pay = providers.providers_payload(
                {"installed": True, "connected": True})
    finally:
        codex_limits.exhausted = saved
    cx = next(x for x in pay["providers"] if x["id"] == "openai")
    eq(cx["hire_enabled"], False, "the whole family goes dark")
    eq(cx["reserve_hire_enabled"], False, "reserve with it")
    assert "no usage left" in (cx["reason"] or ""), cx
    assert "seat" in (cx["reason"] or ""), (
        "the tooltip must say WHY it matters — the seat goes either way")
    claude = next(x for x in pay["providers"] if x["id"] == "claude")
    eq(claude["hire_enabled"], True, "and Claude is another account entirely")


def an_unread_board_leaves_the_family_alone():
    """THE LEG THAT MUST HOLD for the follow-up. `exhausted()` answers None on
    a machine nobody has polled, and None must not take a whole provider
    away — that would be a worse bug than the one being fixed."""
    write_registry(reserve="list")
    codex_limits.invalidate()
    with NoAppServer():
        pay = providers.providers_payload(
            {"installed": True, "connected": True})
    cx = next(x for x in pay["providers"] if x["id"] == "openai")
    eq((cx["hire_enabled"], cx["reason"]), (True, None), "fail open")


check("a spent account darkens sol/terra/luna too, not just reserve",
      a_spent_account_darkens_the_whole_family)
check("…and an unread board darkens nothing (the leg that must hold)",
      an_unread_board_leaves_the_family_alone)


print(f"\nPASS — gpt-reserve detection, {PASS} checks")
