"""D-194: a cost-booking point decides its lane from the provider that will
actually bill it.

    python backend/tests/test_codex_cost_lane.py   (no pytest; plain asserts)

WHAT THIS EXISTS TO CATCH, and why it is a BEHAVIOUR test rather than a count.

`api_cost_usd` means one thing: dollars billed to THE ORG'S ANTHROPIC API KEY
while the `api_fallback` window was open. `_bank_api_cost` says so, and
`App.tsx`'s `costSplitTitle` renders it to the user as
`subscription $X · api key $Y`, computing the subscription half as
`total − api`. So a dollar banked there wrongly corrupts BOTH halves of a
number the user reads.

A Codex fork cannot bill that key, and not by accident — `codexrun` strips
every `ANTHROPIC_*` and `CLAUDE_CODE_*` variable out of the Codex child's
environment on purpose (and `OPENAI_API_KEY` too, for the mirror reason), and
its dollars are priced by `providers.codex_cost` from OpenAI token rates. The
Codex TURN already knows this: `_run_one_turn` books `on_key=False` for a
codex tier and raises `_CodexTurnDone` before the Anthropic lane capture is
ever reached. The Codex COMPACTION FORK did not, and asked
`api_fallback_active(org)` — a question about a credential the process has
been deliberately deprived of.

⚠ WHY THIS FILE REPLACES A SOURCE COUNT. The drift guard in `test_headless.py`
was `src.count("on_fallback_key = api_fallback_active(org)") == 2`. It fired
when the third (Codex) capture landed — correctly, because something HAD
drifted. The reflex repair is to bump it to 3, and that is worse than leaving
it red: it silences the alarm and keeps the fault, with a reviewer's name on
it. An integer cannot tell "a legitimate new booking point" from "a booking
point asking the wrong provider's question", because both change the count by
one. This suite asks the question the integer could not: **does the money
move?** It fails on the bug and passes on the fix, and no future provider can
make it pass by arithmetic.

Hermetic. Its own ORGTREE_DATA, no backend, no network, no real Codex CLI —
`codexrun.compact_fork` and `providers.codex_status` are replaced, because
what is under test is the BOOKING DECISION, not the fork mechanics
(`test_codexrun.py` owns those).

Anti-vacuity: §0 asserts the fixture really does hold an OPEN api_fallback
window and that a CLAUDE fork in the same org banks to `api_cost_usd`. Without
that control every "stayed at zero" below would also pass on an org where the
counter could never move at all.
"""

import os
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-codexlane-")
os.environ["ORGTREE_DATA"] = DATA
# an unreachable hub, or every org this rig creates registers against the
# operator's REAL roster (test_external_mail §1 guards exactly this — and
# caught this rig missing the pin, 2026-08-29)
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
# a port nobody serves — nothing here should reach a backend, and defaulting
# to 7360 would point a TEST at the operator's live deployment
os.environ["ORGTREE_PORT"] = "9"

from orgtree import providers, store, supervisor                   # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what) -> None:
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


#: the fork's reported usage, in the app-server's own `tokenUsage` shape
#: (`total.inputTokens` INCLUDES the cached reads — see `providers.codex_cost`).
#: Chosen so the priced cost is unmistakably NON-ZERO: a fork that cost $0
#: would satisfy "api_cost_usd stayed 0" while proving nothing at all, which
#: is exactly what §1 exists to refuse. (The first cut of this fixture used
#: flat snake_case keys, priced to $0.00, and made §2 vacuously green — the
#: control caught it, which is the argument for having one.)
FORK_USAGE = {"total": {"inputTokens": 200_000, "cachedInputTokens": 0,
                        "outputTokens": 40_000},
              "last": {"inputTokens": 200_000}}
NEW_THREAD = "codex-thread-after-compact"


def mkorg(label: str, *, tier: str = "sol", window: bool = True):
    """One org holding an Anthropic api_key with the api_fallback window OPEN,
    and one node of `tier`. The window being open is the whole point: it is the
    condition under which a booking point is ALLOWED to bank to api_cost_usd,
    so it is the condition under which a wrong lane actually moves money."""
    org = store.create_org(f"zz codexlane {label}")
    org.d["api_key"] = "sk-ant-test-not-a-real-key"
    org.d["api_fallback"] = True
    org.d["api_fallback_until"] = (2 ** 31) if window else 0
    r = org.hire(USER, None, tier, 2, "cx", add_dirs=[],
                 tools={"bash": False, "web": False, "edit": False,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a cost-lane test agent")
    nid = r["node"]
    n = org.node(nid)
    old_sid = n["session_id"]
    n["codex_thread"] = old_sid          # the fork's resumable-thread gate
    n["occupancy"] = 300_000
    store.save_org(org)
    return org, nid, old_sid


def stub_codex(monkey: dict) -> None:
    """Replace the two things that would reach a real Codex install."""
    from orgtree import codexrun
    monkey["status"] = providers.codex_status
    monkey["fork"] = codexrun.compact_fork
    providers.codex_status = lambda *a, **k: {          # type: ignore[assignment]
        "installed": True, "connected": True, "path": "/fake/codex"}
    codexrun.compact_fork = lambda *a, **k: {           # type: ignore[assignment]
        "thread_id": NEW_THREAD, "token_usage": FORK_USAGE}


def unstub(monkey: dict) -> None:
    from orgtree import codexrun
    providers.codex_status = monkey["status"]           # type: ignore[assignment]
    codexrun.compact_fork = monkey["fork"]              # type: ignore[assignment]


def run_codex_fork(org, nid, old_sid):
    n = store.load_org(org.d["slug"]).node(nid)
    supervisor._compact_split_codex_body(
        org.d["slug"], nid, org, n, old_sid, "gpt-5.6")
    return store.load_org(org.d["slug"])


def main() -> int:
    monkey: dict = {}
    stub_codex(monkey)
    try:
        # ── §0 the controls. Without these, every zero below is unfalsifiable.
        print("\n§0 the fixture can move the counter (anti-vacuity)")
        org0, nid0, _ = mkorg("control")
        check("control · the fixture's api_fallback window really is OPEN — "
              "the only state in which a booking point may bank at all",
              lambda: eq(supervisor.api_fallback_active(org0), True,
                         "api_fallback_active"))
        def _claude_lane_banks():
            o = store.load_org(org0.d["slug"])
            supervisor._bank_api_cost(o, 0.25)
            eq(round(float(o.d.get("api_cost_usd") or 0), 6), 0.25,
               "api_cost_usd after a legitimate claude-lane booking")
        check("control · …and a CLAUDE-lane booking in this very org DOES "
              "reach api_cost_usd, so the counter is not merely inert here",
              _claude_lane_banks)

        # ── §1 the fork's own price is non-zero (else §2 proves nothing)
        print("\n§1 the fork under test really costs money")
        priced = providers.codex_cost("sol", FORK_USAGE)
        check("cost · the stubbed fork prices to a NON-ZERO number, so "
              "'api_cost_usd stayed 0' is a fact about the lane and not "
              "about an empty fork",
              lambda: _true(priced > 0, f"codex_cost=$={priced}"))

        # ── §2 THE BUG. Codex dollars must never reach the Anthropic counter.
        print("\n§2 a Codex compaction fork does not bank to the Anthropic key")
        org1, nid1, sid1 = mkorg("normal")
        after1 = run_codex_fork(org1, nid1, sid1)
        check("codex · the fork's dollars land on the NODE's cost_usd — the "
              "money is booked, it is only the LANE under test",
              lambda: _true(
                  float(after1.node(nid1).get("cost_usd") or 0) > 0,
                  f"cost_usd={after1.node(nid1).get('cost_usd')!r}"))
        check("codex · …and api_cost_usd stays ZERO even with the "
              "api_fallback window wide open — a Codex child is stripped of "
              "every ANTHROPIC_* variable and cannot bill that key",
              lambda: eq(float(after1.d.get("api_cost_usd") or 0), 0.0,
                         "api_cost_usd after a codex compaction fork"))

        # ── §3 the same must hold on the fork's OTHER two booking branches.
        # One capture feeds all three, so a fix that misses one is a fix that
        # only looks right on the happy path.
        print("\n§3 …on every branch, not just the one that completes")
        org2, nid2, sid2 = mkorg("deleted")
        _real_load = store.load_org

        def _drop_node(slug):
            o = _real_load(slug)
            o.nodes.pop(nid2, None)          # deleted while the fork ran
            return o
        store.load_org = _drop_node          # type: ignore[assignment]
        try:
            supervisor._compact_split_codex_body(
                org2.d["slug"], nid2, org2,
                dict(org2.node(nid2)), sid2, "gpt-5.6")
        finally:
            store.load_org = _real_load      # type: ignore[assignment]
        after2 = store.load_org(org2.d["slug"])
        check("codex · the DELETED-node branch banks to deleted_cost_usd and "
              "still leaves the Anthropic counter alone",
              lambda: eq([float(after2.d.get("deleted_cost_usd") or 0) > 0,
                          float(after2.d.get("api_cost_usd") or 0)],
                         [True, 0.0],
                         "deleted branch (deleted_cost_usd>0, api_cost_usd)"))

        org3, nid3, sid3 = mkorg("replaced")
        o3 = store.load_org(org3.d["slug"])
        o3.node(nid3)["session_id"] = "some-other-session"   # replaced mid-fork
        store.save_org(o3)
        after3 = run_codex_fork(org3, nid3, sid3)
        check("codex · the SESSION-REPLACED branch does the same",
              lambda: eq([float(after3.node(nid3).get("cost_usd") or 0) > 0,
                          float(after3.d.get("api_cost_usd") or 0)],
                         [True, 0.0],
                         "replaced branch (cost_usd>0, api_cost_usd)"))

        # ── §4 and the rule stated as a rule, not as this one instance.
        print("\n§4 the lane predicate answers per PROVIDER, not per org")
        org4, _, _ = mkorg("predicate")
        check("rule · with the window open, an Anthropic tier bills the key "
              "and every Codex tier does not — one predicate, both answers",
              lambda: eq(
                  {t: supervisor.api_fallback_active_for(org4, t)
                   for t in ("opus", "fable", "sol", "terra", "luna")},
                  {"opus": True, "fable": True,
                   "sol": False, "terra": False, "luna": False},
                  "api_fallback_active_for by tier"))
        org5, _, _ = mkorg("shut", window=False)
        check("rule · …and with the window SHUT nobody bills it, so the "
              "predicate has not merely become 'is this claude'",
              lambda: eq(
                  {t: supervisor.api_fallback_active_for(org5, t)
                   for t in ("opus", "sol")},
                  {"opus": False, "sol": False},
                  "api_fallback_active_for, window shut"))
    finally:
        unstub(monkey)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILURE(S):")
        for label, tb in FAIL:
            print(f"\n--- {label} ---\n{tb}")
        print(f"\n{PASS} passed, {len(FAIL)} FAILED")
        return 1
    print(f"ALL {PASS} CHECKS PASS")
    return 0


def _true(cond, msg="") -> None:
    if not cond:
        raise AssertionError(msg or "expected true")


if __name__ == "__main__":
    sys.exit(main())
