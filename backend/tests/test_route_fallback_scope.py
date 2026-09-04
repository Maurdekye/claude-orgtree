"""The Anthropic fallback key serves ONE route — readiness and activation must
both know which (Astra audit F1, 2026-09-04).

Two ends of one bug, both in `supervisor.py`:

  · `auto_resume_ready` asked the ORG-WIDE `api_fallback_active(org)` and
    fast-woke every limit-frozen node not marked `on_fallback`. A Luna (Codex)
    node frozen for another 24 hours became ready the moment an Anthropic
    fallback was enabled — its Codex route had gained no capacity, and the
    wake re-drove it into the same wall (audit fixture
    `cross_provider_fallback_wake`, reproduced on the old snapshot).
  · the shared Claude-CLI failure path stamped `api_fallback_until` for ANY
    tier's usage limit. An OpenRouter (`or-*`) tier runs through that same
    path — Claude Code is the harness, openrouter.ai the endpoint — so an
    OpenRouter 429 could open the org's ANTHROPIC billing window, which the OR
    spawn then never uses (`spawn_env` hands an OR tier the OR token and an
    EMPTY `ANTHROPIC_API_KEY`).

The fix scopes both to `api_fallback_active_for` / `api_fallback_tier` — the
classifier that already existed for the cost split (D-194) — rather than a
second provider table.

    §1  readiness: a provider matrix of frozen Claude, Codex, Antigravity and
        OpenRouter nodes under an open Anthropic window. Pure: `_FakeOrg`.
    §2  activation: a fake OpenRouter limit response through the REAL turn
        loop (the synth CLI stand-in from test_limit_freeze) must not open the
        Anthropic window; the same response on a haiku node MUST (control).

Every refusal is paired with the minimally different record that must still
wake or still open the window — a build where nothing ever wakes goes green on
the refusals alone, and the controls are what stop that.

Hermetic: throwaway ORGTREE_DATA + HOME (inherited from the harness), no port,
no network, no real CLI. §2 spawns `node` for the stand-in and declares itself
INERT (exit 2) if it is absent rather than passing quietly.

    python backend/tests/test_route_fallback_scope.py [-v]
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import traceback
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ⚠ the harness sets ORGTREE_DATA/HOME and writes the CLI stand-in AT IMPORT,
# before `orgtree` is imported anywhere in this process — that ordering is
# the whole reason it is imported first and `orgtree` only after it.
import test_limit_freeze as H                                    # noqa: E402

from orgtree import accounts, openrouter, store, supervisor     # noqa: E402

_LIVE_ROOT = os.path.normcase(os.path.abspath(
    os.path.join(os.path.expanduser("~"), "orgtree")))
assert os.path.normcase(os.path.abspath(store.DATA_ROOT)) != _LIVE_ROOT, \
    f"store.DATA_ROOT resolved to the LIVE root: {store.DATA_ROOT}"
assert os.path.normcase(os.path.abspath(store.DATA_ROOT)).startswith(
    os.path.normcase(os.path.abspath(H._TMP))), store.DATA_ROOT

PASS = 0
FAIL: list[tuple[str, str]] = []
VERBOSE = "-v" in sys.argv

#: one tier per provider. `or-audit-fake` is the audit's own OpenRouter fixture
#: tier — DYNAMIC (registered on the org, not in any static table), which is
#: what makes it the right probe: no tier list anywhere names it.
CLAUDE, CODEX, AGY, OR = "haiku", "luna", "flash", "or-audit-fake"
OR_MODEL = "audit/fake"

#: an OpenRouter 429 as Claude Code surfaces it — the gateway's own error
#: envelope inside the CLI's "API Error" line. `_looks_like_usage_limit`
#: admits it ("limit" + "exceeded"), which is exactly why it reached the
#: Anthropic window branch.
OR_429 = ('API Error: 429 {"error":{"message":"Rate limit exceeded: '
          'free-models-per-day. Add 10 credits to unlock 1000 free model '
          'requests per day","code":429}}')


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        if VERBOSE:
            traceback.print_exc()
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def fixture(ok: bool, msg: str) -> None:
    if not ok:
        raise RuntimeError(f"fixture: {msg}")


# ══════════════════════════════════════════════════════════════════════════ §1

def _frozen(now: float, **extra: Any) -> dict[str, Any]:
    """The audit's Luna record: a usage limit with a full day still to run."""
    fz: dict[str, Any] = {"limit": True, "until_ts": now + 86400,
                          "until": "tomorrow", "reset_src": "provider"}
    fz.update(extra)
    return fz


def _matrix(now: float, *, window: bool) -> H._FakeOrg:
    """One frozen node per provider, in an org that holds an Anthropic
    fallback key. `window=True` opens the key window for the next hour — the
    state the audit flipped `api_fallback` into."""
    o = H._FakeOrg(slug="zz-route", auto_resume=True, api_key="sk-test",
                   api_fallback=window, api_fallback_until=now + 3600)
    for name, tier in (("claude", CLAUDE), ("codex", CODEX),
                       ("agy", AGY), ("or", OR)):
        o.nodes[name] = {"state": "live", "model": tier, "frozen": _frozen(now)}
    return o


def sec_readiness() -> None:
    print("\n§1  readiness — the Anthropic window wakes only the route that "
          "can bill the key")
    now = time.time()
    undo = H._stub_login()
    try:
        _sec_readiness_body(now)
    finally:
        undo()


def _sec_readiness_body(now: float) -> None:
    # the fake org needs a readable roster for the pool branch; nothing here
    # is parked on a dry pool unless a check says so
    H._set_pool(CLAUDE, dry=False)

    # ── the audit fixture, ported verbatim: Luna, 24 h to go, flag flipped ──
    def _audit_luna_fixture():
        o = H._FakeOrg(slug="zz-audit", auto_resume=True,
                       api_key="FAKE-NOT-A-CREDENTIAL", api_fallback=False,
                       api_fallback_until=now + 3600)
        o.nodes["luna"] = {"state": "live", "model": CODEX,
                           "frozen": _frozen(now)}
        fixture("luna" not in supervisor.auto_resume_ready(o, now),
                "the Luna node was ready BEFORE the flag flipped — this "
                "check needs a record the timer leaves alone")
        o.d["api_fallback"] = True
        assert "luna" not in supervisor.auto_resume_ready(o, now), (
            "enabling the org's ANTHROPIC fallback made a CODEX node ready "
            "with 24 h still on its own reset. Its route gained no capacity: "
            "codexrun strips every ANTHROPIC_* variable, so the wake spends a "
            "turn re-driving into the same wall (audit F1)")
    check("audit · the Luna fixture: an Anthropic fallback flag does not "
          "wake a frozen Codex node", _audit_luna_fixture)

    # ── the matrix under an OPEN window ────────────────────────────────────
    def _codex_stays_parked():
        assert "codex" not in supervisor.auto_resume_ready(
            _matrix(now, window=True), now), \
            "an open Anthropic key window woke a frozen CODEX node"
    check("matrix · codex stays parked under an open Anthropic window",
          _codex_stays_parked)

    def _agy_stays_parked():
        assert "agy" not in supervisor.auto_resume_ready(
            _matrix(now, window=True), now), \
            "an open Anthropic key window woke a frozen ANTIGRAVITY node"
    check("matrix · antigravity stays parked under an open Anthropic window",
          _agy_stays_parked)

    def _or_stays_parked():
        assert "or" not in supervisor.auto_resume_ready(
            _matrix(now, window=True), now), (
            "an open Anthropic key window woke a frozen OPENROUTER node. The "
            "OR spawn is handed the OR token and an EMPTY ANTHROPIC_API_KEY "
            "(spawn_env) — the key cannot serve it")
    check("matrix · openrouter stays parked under an open Anthropic window",
          _or_stays_parked)

    def _control_claude_wakes():
        assert "claude" in supervisor.auto_resume_ready(
            _matrix(now, window=True), now), (
            "CONTROL FAILED: the CLAUDE node did not wake under the open "
            "window, so the three refusals above prove nothing — they would "
            "pass on a build where the fallback fast-wake was simply removed. "
            "That is the user's 2026-08-17 feature and it must keep working")
    check("control · the Claude node DOES wake under the open window "
          "(legitimate fallback wake preserved)", _control_claude_wakes)

    def _exactly_the_claude_route():
        assert supervisor.auto_resume_ready(_matrix(now, window=True), now) \
            == {"claude"}, "the ready set under an open window is not "\
            "exactly the Claude route"
    check("matrix · the ready set under an open window is exactly {claude}",
          _exactly_the_claude_route)

    # ── the SAME matrix, window shut: ordinary reset recovery, every provider
    def _window_shut_nothing_wakes():
        assert supervisor.auto_resume_ready(_matrix(now, window=False), now) \
            == set(), "with the window SHUT and 24 h to go, something woke"
    check("control · window shut, 24 h to go: nothing wakes",
          _window_shut_nothing_wakes)

    def _every_provider_wakes_at_its_own_reset():
        late = now + 86400 + 61          # past until_ts plus the limit grace
        got = supervisor.auto_resume_ready(_matrix(now, window=False), late)
        assert got == {"claude", "codex", "agy", "or"}, (
            f"ordinary reset-based recovery broke for some provider: {got}. "
            "Scoping the fallback shortcut must not stop ANY recovery — the "
            "brief names this as the wrong fix")
    check("control · every provider wakes at its own reset with the window "
          "shut", _every_provider_wakes_at_its_own_reset)

    def _every_provider_wakes_at_its_own_reset_window_open():
        late = now + 86400 + 61
        o = _matrix(now, window=True)
        o.d["api_fallback_until"] = late + 3600      # window still open then
        got = supervisor.auto_resume_ready(o, late)
        assert got == {"claude", "codex", "agy", "or"}, (
            f"with the window open AND every reset passed, some provider "
            f"stayed parked: {got} — the scoping must sit on the shortcut "
            "only, never on the timed path")
    check("control · …and with the window open too, once each reset passes",
          _every_provider_wakes_at_its_own_reset_window_open)

    # ── the guards the fast-wake already had, unchanged ────────────────────
    def _claude_frozen_on_the_key_stays_parked():
        o = _matrix(now, window=True)
        o.nodes["claude"]["frozen"]["on_fallback"] = True
        assert "claude" not in supervisor.auto_resume_ready(o, now), (
            "a Claude freeze earned ON the key lane was insta-woken into the "
            "same wall — the on_fallback guard must survive the scoping")
    check("guard · a Claude freeze earned on the key lane keeps its own reset",
          _claude_frozen_on_the_key_stays_parked)

    def _claude_auth_freeze_not_woken():
        o = _matrix(now, window=True)
        o.nodes["claude"]["frozen"]["cause"] = "auth"
        assert "claude" not in supervisor.auto_resume_ready(o, now), \
            "the open window woke an AUTH freeze (D-156)"
    check("guard · an auth freeze is never woken by the window",
          _claude_auth_freeze_not_woken)

    def _claude_untrusted_capped_not_woken():
        o = _matrix(now, window=True)
        o.nodes["claude"]["frozen"].update(untrusted=True, until_ts=None)
        assert "claude" not in supervisor.auto_resume_ready(o, now), \
            "the open window woke a CAPPED untrusted freeze"
    check("guard · a capped untrusted freeze is never woken by the window",
          _claude_untrusted_capped_not_woken)

    def _dry_pool_recovery_still_works():
        o = _matrix(now, window=False)
        o.nodes["claude"]["frozen"]["pool"] = "dry"
        H._set_pool(CLAUDE, dry=False)
        assert "claude" in supervisor.auto_resume_ready(o, now), (
            "account-pool dry recovery stopped working: a Claude node parked "
            "on a dry pool did not wake when the pool had capacity again")
        H._set_pool(CLAUDE, dry=True, now=now)
        assert "claude" not in supervisor.auto_resume_ready(o, now), \
            "…and it woke while the pool was still dry"
        H._set_pool(CLAUDE, dry=False)
    check("guard · account-pool dry recovery is untouched (wet wakes, dry "
          "stays)", _dry_pool_recovery_still_works)

    # ── fable: the Claude family, with its own policy layered on top ───────
    def _fable_wakes_under_the_window():
        o = _matrix(now, window=True)
        o.nodes["fable"] = {"state": "live", "model": "fable",
                            "frozen": _frozen(now)}
        assert "fable" in supervisor.auto_resume_ready(o, now), (
            "a fable SESSION freeze (no org-wide lock) stopped waking under "
            "the open window — fable is a Claude tier and the fable_api_"
            "fallback opt-in relies on this wake")
    check("fable · an unlocked fable freeze wakes under the window (Claude "
          "family)", _fable_wakes_under_the_window)

    def _fable_locked_never_wakes():
        o = _matrix(now, window=True)
        o.nodes["fable"] = {"state": "live", "model": "fable",
                            "limit_locked": True, "frozen": _frozen(now)}
        assert "fable" not in supervisor.auto_resume_ready(o, now), \
            "an org-wide fable lock was bypassed by the window"
    check("fable · a limit_locked fable node is never woken (fable policy "
          "preserved)", _fable_locked_never_wakes)

    def _unknown_tier_is_not_a_claude_tier():
        o = _matrix(now, window=True)
        o.nodes["mystery"] = {"state": "live", "model": "not-a-tier",
                              "frozen": _frozen(now)}
        assert "mystery" not in supervisor.auto_resume_ready(o, now), (
            "an UNRECOGNISED tier was fast-woken by the Anthropic window. "
            "The axis is positive — a KNOWN Claude tier — so a provider added "
            "tomorrow is safe the moment it is absent from claude_tiers()")
    check("axis · an unrecognised tier is not fast-woken (positive axis)",
          _unknown_tier_is_not_a_claude_tier)


# ══════════════════════════════════════════════════════════════════════════ §2

def _fallback_org(tier: str) -> tuple[str, str]:
    """A one-node org holding an Anthropic fallback key, its node on `tier`.
    An OR tier is registered on the org the way `openrouter.favorites` does
    it — seat and model id on the document, nothing in a static table."""
    slug, nid = H.probe_org()
    o = store.load_org(slug)
    o.d.update(api_key="sk-test", api_fallback=True)
    n = o.node(nid)
    n["model"] = tier
    if openrouter.is_tier(tier):
        o.d.setdefault("tiers", {})[tier] = 1
        o.d.setdefault("models", {})[tier] = OR_MODEL
    store.save_org(o)
    return slug, nid


def sec_activation() -> None:
    print("\n§2  activation — a fake OpenRouter limit through the real turn "
          "loop")
    if not shutil.which("node"):
        print("  INERT    `node` is not on PATH: the CLI stand-in cannot run, "
              "so this section proves nothing here")
        FAIL.append(("§2 inert", "node missing — activation unverified"))
        return
    openrouter.set_key("or-test-key-000000")
    try:
        _sec_activation_body()
    finally:
        openrouter.set_key("")


def _sec_activation_body() -> None:
    # ── the OpenRouter turn ────────────────────────────────────────────────
    slug, nid = _fallback_org(OR)

    def _the_or_lane_is_what_runs():
        env = supervisor.spawn_env(store.load_org(slug), tier=OR, nid=nid)
        fixture(supervisor.identity_in_env(env) == supervisor.OPENROUTER_IDENTITY,
                f"the spawn env is not the OR lane: {sorted(k for k in env if k.startswith('ANTHROPIC'))}")
        assert not env.get("ANTHROPIC_API_KEY"), (
            "the OR spawn carries the org's Anthropic key — then a window "
            "WOULD be usable and this whole section is asking the wrong "
            "question")
    check("fixture · the OR tier spawns on the OR lane with no Anthropic key",
          _the_or_lane_is_what_runs)

    H.set_mode("iserror", limit_text=OR_429)
    H.run_turn(slug, nid)
    fz_or = dict(H.node(slug, nid).get("frozen") or {})
    o_after = store.load_org(slug)

    def _the_or_limit_froze_the_node():
        fixture(bool(fz_or) and fz_or.get("limit") is True,
                f"the OR 429 did not freeze the node as a usage limit: {fz_or}")
        assert fz_or.get("cause") != "auth", fz_or
        assert fz_or.get("until_ts"), \
            "the OR freeze carries no reset — the probe floor should apply"
    check("fixture · the OR 429 freezes the node as an ordinary usage limit",
          _the_or_limit_froze_the_node)

    def _no_anthropic_window_opens_for_an_or_limit():
        assert not o_after.d.get("api_fallback_until"), (
            "an OPENROUTER limit opened the org's ANTHROPIC billing window "
            f"(api_fallback_until={o_after.d.get('api_fallback_until')!r}). "
            "The OR spawn never uses that key, so the window buys nothing "
            "for this node and puts every CLAUDE sibling onto the metered "
            "key for a wall they never hit")
        assert not o_after.d.get("api_fallback_since"), o_after.d.get(
            "api_fallback_since")
    check("activation · an OpenRouter limit cannot open the Anthropic window",
          _no_anthropic_window_opens_for_an_or_limit)

    def _the_or_freeze_carries_no_key_lane_fact():
        assert "on_fallback" not in fz_or, (
            f"the OR freeze was stamped on_fallback={fz_or.get('on_fallback')!r}"
            " — no key lane serves this provider, so the record must say "
            "nothing about one (same rule as freeze_provider_limit)")
    check("activation · the OR freeze record carries no on_fallback fact",
          _the_or_freeze_carries_no_key_lane_fact)

    def _the_or_node_recovers_on_its_own_reset():
        o = store.load_org(slug)
        now = time.time()
        fixture(nid not in supervisor.auto_resume_ready(o, now),
                "the OR node was ready immediately after freezing")
        assert nid in supervisor.auto_resume_ready(
            o, float(fz_or["until_ts"]) + 61), (
            "the OR node did not wake once its own reset passed — recovery "
            "must still be ordinary and time-based for this route")
    check("recovery · the OR node wakes on its own reset, and not before",
          _the_or_node_recovers_on_its_own_reset)

    # ── the same OR limit while a CLAUDE sibling already opened the window ─
    slug_w, nid_w = _fallback_org(OR)
    o_w = store.load_org(slug_w)
    o_w.d["api_fallback_until"] = time.time() + 3600
    store.save_org(o_w)
    H.set_mode("iserror", limit_text=OR_429)
    H.run_turn(slug_w, nid_w)
    fz_w = dict(H.node(slug_w, nid_w).get("frozen") or {})

    def _open_window_does_not_mark_the_or_freeze():
        fixture(fz_w.get("limit") is True, f"no freeze: {fz_w}")
        assert "on_fallback" not in fz_w, (
            "with a sibling's Anthropic window already open, the OR freeze "
            f"was stamped on_fallback={fz_w.get('on_fallback')!r}: the "
            "org-wide answer attributed a lane this spawn never had")
        assert nid_w not in supervisor.auto_resume_ready(
            store.load_org(slug_w), time.time()), (
            "the open window insta-woke the OR node")
    check("activation · an already-open window neither marks nor wakes the "
          "OR freeze", _open_window_does_not_mark_the_or_freeze)

    # ── control: the same response on a CLAUDE tier opens the window ──────
    slug_c, nid_c = _fallback_org(CLAUDE)
    H._set_pool(CLAUDE, dry=True)          # no other account to switch to
    H.set_mode("iserror", limit_text=OR_429)
    H.run_turn(slug_c, nid_c)
    fz_c = dict(H.node(slug_c, nid_c).get("frozen") or {})
    o_c = store.load_org(slug_c)

    def _control_the_claude_tier_opens_the_window():
        fixture(fz_c.get("limit") is True, f"the control did not freeze: {fz_c}")
        assert o_c.d.get("api_fallback_until"), (
            "CONTROL FAILED: the identical limit text on a HAIKU node opened "
            "no Anthropic window, so 'no window for OR' proves nothing — a "
            "build that never opens the window passes the OR check for free")
        assert fz_c.get("on_fallback") is False, (
            f"the Claude freeze lost its lane fact: {fz_c.get('on_fallback')!r}")
    check("control · the same limit on a Claude tier opens the window and "
          "records its lane", _control_the_claude_tier_opens_the_window)

    def _control_the_claude_node_is_woken_by_its_window():
        assert nid_c in supervisor.auto_resume_ready(o_c, time.time()), (
            "CONTROL FAILED: the Claude node whose limit opened the window "
            "is not woken by it")
    check("control · …and the window wakes that Claude node at once",
          _control_the_claude_node_is_woken_by_its_window)

    # ── codex / antigravity freezes never opened a window; keep it so ─────
    def _provider_freeze_opens_no_window():
        slug_p, nid_p = _fallback_org(CODEX)
        ok = supervisor.freeze_provider_limit(
            slug_p, nid_p, "usage limit reached", reset_ts=time.time() + 86400)
        fixture(ok, "freeze_provider_limit wrote nothing")
        o_p = store.load_org(slug_p)
        assert not o_p.d.get("api_fallback_until"), \
            "a codex freeze opened the Anthropic window"
        assert nid_p not in supervisor.auto_resume_ready(o_p, time.time())
        o_p.d["api_fallback_until"] = time.time() + 3600
        assert nid_p not in supervisor.auto_resume_ready(o_p, time.time()), \
            "an open window woke the codex freeze written by the provider path"
    check("provider · a codex freeze opens no window and is not woken by one",
          _provider_freeze_opens_no_window)


# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    print(f"data root: {store.DATA_ROOT}")
    sec_readiness()
    sec_activation()
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\nFAIL {label}\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
