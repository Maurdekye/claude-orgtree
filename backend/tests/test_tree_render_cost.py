"""The tree render's per-node cost — what `annotate` is allowed to do per seat.

Run directly::

    python backend/tests/test_tree_render_cost.py

WHY THIS SUITE EXISTS
---------------------
`GET /api/orgs/{slug}` is the single payload behind every card, meter, roster
row and badge on the desk. It is fetched on a 6 s heartbeat, on every
websocket `changed` frame (i.e. every `save_org` by any agent, the supervisor
or another tab), on every org switch and after every hire — per open tab. So
its cost is not paid once, it is paid several times a minute, and `annotate`
runs for EVERY node in the document, live and archived alike.

The failure this guards against is SILENT and it has already happened once.
Commit 6190b83 (2026-09-01, "Add deterministic cache continuity policy") added
one line to `annotate`:

    node["cache_forecast"] = supervisor.cache_forecast_public(org, node["id"])

Correct, tested, and reviewed — and it made the desk unusable within two days.
`cache_forecast_public` reaches `_cache_snapshot` → `_cache_semantic_inputs` →
`_build_cmd` → `transcript_path`, and `transcript_path` is a `glob` whose
WILDCARD COMPONENT is the project directory, so each call re-lists the user's
entire `~/.claude/projects`. MEASURED 2026-09-03 on the orgtree org (6 live
seats, 179 archived, 349 project dirs):

    one transcript_path glob            14 ms   (26 ms on a miss — it globs twice)
    cache_forecast_public, all seats   1226-4036 ms per render, ~92% archived
    cache_forecast_public, live only     76-118 ms   (11.8-16.1x cheaper)
    GET /api/orgs/orgtree              11-38 s alone, 113.7 s with two in flight

against the client's 45 s `AbortSignal.timeout` — the "signal timed out"
banner the user reported. Nothing failed; it just got slower every time an
agent was retired, because an ARCHIVED seat kept paying full price for an
answer nothing renders.

WHAT IS ACTUALLY UNDER TEST — the RULE, not the current call list
----------------------------------------------------------------
An archived seat has no next turn, so it has nothing to forecast about: the
value is `None` and it is computed for nobody. §1 pins the CONTENT (the key is
present and explicitly null, so a consumer never sees a stale persisted row)
and §2 pins the COST (the call does not happen at all).

§2 is the one that matters and it is why this file exists rather than an
assertion tacked onto an existing payload test. A future edit could compute
the forecast for archived seats and then discard it, satisfying §1 perfectly
while restoring the entire regression. Content assertions cannot see cost.

§3 is the property in its general form: the number of forecast computations
must not grow with the ARCHIVE. An org with 4 archived seats and an org with
120 archived seats must do the same amount of work, because they have the same
number of live ones. That is the invariant; §1 and §2 are its two visible
faces at one org size.

⚠ THE TEST IS `state == "archived"`, NEVER "has no warm process". A live seat
sitting idle with no parked CLI is exactly who the forecast is FOR — its next
turn is the one whose cache is at risk — so a liveness or warm-pool gate would
delete the feature's whole value while passing every check here. §4 pins that
distinction directly, because it is the plausible-looking wrong fix.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Any

from starlette.requests import Request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RIG = tempfile.mkdtemp(prefix="orgtree-tree-render-cost-")
os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = os.path.join(RIG, "home")
os.environ["USERPROFILE"] = os.path.join(RIG, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import api, store, supervisor as S   # noqa: E402
from orgtree.ledger import USER                   # noqa: E402

S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None

PASS = 0
FAIL = 0


def check(label: str, fn: Any) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as exc:                                    # noqa: BLE001
        FAIL += 1
        print(f"  FAIL    {label}: {exc}")
        import traceback
        traceback.print_exc()


def request(scope_state: dict[str, object] | None = None) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/api/test",
        "raw_path": b"/api/test", "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 7360),
        "scheme": "http", "state": scope_state or {},
    })


# A public row is what makes `cache_forecast_public` do its full job rather
# than return early — so every seat in these fixtures carries one. Without it
# the suite would pass for the wrong reason: no work to skip.
def public_row() -> dict[str, Any]:
    return {"public": {
        "generation": "g", "state": "compatible_observed",
        "source": "authoritative_receipt", "lane": "subscription",
        "ttl_seconds": 3600, "expires_at": "2099-01-01T00:00:00.000000Z",
        "last_receipt_at": "2026-01-01T00:00:00.000000Z",
        "precompact_action": "not_applicable",
        "precompact_reason": "", "changed_inputs": []}}


def build(slug: str, archived: int) -> Any:
    """One live root plus `archived` retired children, every seat carrying a
    public cache row."""
    org = store.create_org(slug)
    org.hire(USER, None, "haiku", 4, "boss")
    org.node("boss")["cache_continuity"] = public_row()
    for i in range(archived):
        nid = f"old{i}"
        # hired AS THE USER: an agent actor must state every permission
        # explicitly (ledger.hire), and none of that is under test here.
        org.hire(USER, "boss", "haiku", 0, nid)
        org.node(nid)["cache_continuity"] = public_row()
        org.retire(USER, nid)
    store.save_org(org)
    return org


def flatten(tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}

    def walk(n: dict[str, Any]) -> None:
        out[n["id"]] = n
        for c in n.get("children") or []:
            walk(c)

    for r in tree["roots"]:
        walk(r)
    return out


class Counted:
    """Wraps `cache_forecast_public` and records which seats it ran for."""

    def __init__(self) -> None:
        self.seats: list[str] = []
        self._real = S.cache_forecast_public

    def __enter__(self) -> "Counted":
        def spy(org: Any, nid: str, now: float | None = None) -> Any:
            self.seats.append(nid)
            return self._real(org, nid, now)
        S.cache_forecast_public = spy                # pyright: ignore[reportAttributeAccessIssue]
        return self

    def __exit__(self, *exc: object) -> None:
        S.cache_forecast_public = self._real         # pyright: ignore[reportAttributeAccessIssue]


# ── §1 · content: an archived seat's forecast is present and explicitly null ──
def archived_seats_carry_an_explicit_null_forecast() -> None:
    build("zz-render-content", archived=4)
    nodes = flatten(api.org_tree("zz-render-content", request()))

    live = nodes["boss"]
    assert live["state"] == "live", live["state"]
    assert live["cache_forecast"] is not None, (
        "the live seat lost its forecast — the skip caught the wrong seats, "
        "and the feature this whole subsystem exists for is now dead")

    for i in range(4):
        n = nodes[f"old{i}"]
        assert n["state"] == "archived", n["state"]
        # ⚠ the KEY MUST BE PRESENT. `Org.tree()` has already put the node's
        # persisted `cache_continuity.public` row on the payload, and that row
        # is a durable record of some past turn that never went through
        # `cachecontinuity.classify`. Omitting the key would leave that stale
        # verdict on screen; the point is to overwrite it with nothing.
        assert "cache_forecast" in n, (
            f"{n['id']}: key dropped — Org.tree()'s stale persisted row is "
            "what the reader would see")
        assert n["cache_forecast"] is None, (
            f"{n['id']}: {n['cache_forecast']!r}")


# ── §2 · cost: the computation does not happen for archived seats ─────────────
def an_archived_seat_costs_no_forecast_computation() -> None:
    build("zz-render-cost", archived=4)
    with Counted() as spy:
        api.org_tree("zz-render-cost", request())
    assert spy.seats == ["boss"], (
        f"forecast computed for {spy.seats!r}; only the live seat may pay. "
        "A `None` in the payload is not enough — computing the answer and "
        "throwing it away restores the whole regression while §1 still passes")


# ── §3 · the invariant: cost tracks LIVE seats, never the archive ─────────────
def render_cost_does_not_grow_with_the_archive() -> None:
    small = build("zz-render-small", archived=4)
    big = build("zz-render-big", archived=120)
    assert len(small.d["nodes"]) == 5 and len(big.d["nodes"]) == 121

    with Counted() as a:
        api.org_tree("zz-render-small", request())
    with Counted() as b:
        api.org_tree("zz-render-big", request())

    assert a.seats == b.seats == ["boss"], (a.seats, b.seats)
    # Stated as the property rather than as two numbers, so the message names
    # the rule when it breaks: 30x the archive, identical work.
    assert len(b.seats) == len(a.seats), (
        f"{len(a.seats)} computations at 4 archived seats but {len(b.seats)} "
        f"at 120 — the render is paying for the archive again")


# ── §4 · the plausible wrong fix: liveness is not the same question ──────────
def a_live_seat_with_no_process_still_gets_its_forecast() -> None:
    """The gate is STATE, and a warm/liveness gate would look just as green.

    An idle live seat holds no parked CLI and no in-flight turn — `busy` is
    false, `proc_warm` is false, the warm pool has no entry for it. It is also
    the seat whose next turn most needs the forecast, because that turn is the
    one about to pay a cold cache. Gating on any of those process facts instead
    of on `state` would pass §1-§3 unchanged (they retire their seats) and
    silently blank the forecast for every idle agent on the desk.
    """
    build("zz-render-idle", archived=2)
    st = S.state("zz-render-idle", "boss")
    assert not st["busy"], "fixture drifted: the seat must be idle here"
    assert not st.get("proc_warm"), "fixture drifted: no warm process expected"

    with Counted() as spy:
        nodes = flatten(api.org_tree("zz-render-idle", request()))
    assert spy.seats == ["boss"], spy.seats
    assert nodes["boss"]["cache_forecast"] is not None, (
        "an idle live seat lost its forecast — the skip is gating on liveness "
        "or warmth rather than on state")


try:
    print("tree render cost")
    check("§1 an archived seat carries an explicit null forecast",
          archived_seats_carry_an_explicit_null_forecast)
    check("§2 an archived seat costs no forecast computation",
          an_archived_seat_costs_no_forecast_computation)
    check("§3 render cost does not grow with the archive",
          render_cost_does_not_grow_with_the_archive)
    check("§4 an idle live seat still gets its forecast",
          a_live_seat_with_no_process_still_gets_its_forecast)
    print(f"\n{PASS} passed, {FAIL} FAILED")
finally:
    shutil.rmtree(RIG, ignore_errors=True)

sys.exit(1 if FAIL else 0)
