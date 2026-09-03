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

§5 covers the second half of the same fix: `transcript_path` now REMEMBERS a
resolved path and re-verifies it with one `stat` instead of re-globbing. It has
no TTL by design, so its four properties (stable hit, deleted path self-heals,
misses are never cached, roots do not answer for each other) are the whole
contract and are pinned there.

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

import builtins
import glob
import os
import re
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
        # ⚠ THE PORT HERE IS DELIBERATELY NOT THE LIVE DEPLOYMENT'S.
        # Nothing in this suite opens a socket — `server` is scope
        # metadata for a hand-built ASGI call — but `tools/run_tests.py`
        # SKIPS any suite whose SOURCE mentions that port, and it cannot
        # tell a binding from a dict literal. Both of these suites were
        # silently skipped in the full run until that was noticed, so
        # keep the number off this file entirely: a guard that never
        # runs guards nothing.
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 7999),
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


# ── §5 · the `transcript_path` memo: sound because it verifies, not because
#        it is fresh ──────────────────────────────────────────────────────────
def a_remembered_transcript_is_verified_before_it_is_served() -> None:
    """The memo has no TTL, so these four properties are the whole contract.

    It is not a cache that expires; it is a remembered path that is re-checked
    with one `stat` before use. MEASURED 2026-09-03: 8.72 ms per glob vs
    0.039 ms memoised — 225x — because a glob over `projects/*` stats every
    project directory (349 on this box) and the memo stats one file.

    The soundness argument is that the session id IS the file name, so a path
    that still exists is still that session's transcript. These checks are
    what make that argument falsifiable rather than a comment.
    """
    rig = tempfile.mkdtemp(prefix="orgtree-tpath-memo-")
    try:
        proj = os.path.join(rig, "projects", "some-project")
        os.makedirs(proj)
        path = os.path.join(proj, "sess-x.jsonl")
        open(path, "w").close()

        # ① resolved, and stable across calls
        assert S.transcript_path("sess-x", rig) == path
        assert S.transcript_path("sess-x", rig) == path

        # ② a memoised path that DISAPPEARS is never served. This is the one
        #    that would rot silently: the entry is still in the dict and only
        #    the `exists` check stands between it and a caller that would then
        #    open a deleted file.
        os.remove(path)
        assert S.transcript_path("sess-x", rig) is None, (
            "a deleted transcript was served from the memo — the verify step "
            "is gone and the memo has become a plain cache")

        # ③ …and a MISS is not remembered either, so the session is found the
        #    moment it appears. Absence is the answer that flips on its own —
        #    the CLI writes the file partway through a first turn — and
        #    `_build_cmd` reads it as "this session has never run".
        open(path, "w").close()
        assert S.transcript_path("sess-x", rig) == path, (
            "a negative result was cached; a session that appeared after its "
            "first lookup stays invisible")
    finally:
        shutil.rmtree(rig, ignore_errors=True)

    # ④ the memo is per-root: two roots holding the same session id must not
    #    answer for each other (sandboxed orgs get their own transcript root).
    a = tempfile.mkdtemp(prefix="orgtree-tpath-a-")
    b = tempfile.mkdtemp(prefix="orgtree-tpath-b-")
    try:
        for r in (a, b):
            os.makedirs(os.path.join(r, "projects", "p"))
            open(os.path.join(r, "projects", "p", "same-sid.jsonl"), "w").close()
        pa = S.transcript_path("same-sid", a)
        pb = S.transcript_path("same-sid", b)
        assert pa and pb and pa != pb, (pa, pb)
        assert pa.startswith(a) and pb.startswith(b), (pa, pb)
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


# ── §6 · THE RULE ITSELF: filesystem work must not scale with node count ────
class CountFS:
    """Counts real filesystem syscalls made inside the block.

    Patches the primitives rather than one named function on purpose: this
    check must catch a per-node filesystem call that NOBODY HAS THOUGHT OF —
    including one made through a helper that does not exist yet. Naming
    `transcript_path` or `accounts.load` here would only re-guard the two
    instances already known, which is precisely the mistake `_tree_slow_warned`
    made.
    """

    SCANS = ("listdir", "scandir", "glob")     # the expensive kind: whole dirs

    def __enter__(self) -> "CountFS":
        self.n: dict[str, int] = {}
        self._real = {
            "stat": os.stat, "lstat": os.lstat, "listdir": os.listdir,
            "scandir": os.scandir, "open": builtins.open, "glob": glob.glob,
        }

        def wrap(name: str, fn: Any) -> Any:
            def counted(*a: Any, **k: Any) -> Any:
                self.n[name] = self.n.get(name, 0) + 1
                return fn(*a, **k)
            return counted

        os.stat = wrap("stat", self._real["stat"])
        os.lstat = wrap("lstat", self._real["lstat"])
        os.listdir = wrap("listdir", self._real["listdir"])
        os.scandir = wrap("scandir", self._real["scandir"])
        builtins.open = wrap("open", self._real["open"])
        glob.glob = wrap("glob", self._real["glob"])
        S.glob.glob = glob.glob                # supervisor holds its own ref
        return self

    def __exit__(self, *exc: object) -> None:
        os.stat = self._real["stat"]
        os.lstat = self._real["lstat"]
        os.listdir = self._real["listdir"]
        os.scandir = self._real["scandir"]
        builtins.open = self._real["open"]
        glob.glob = self._real["glob"]
        S.glob.glob = self._real["glob"]

    @property
    def total(self) -> int:
        return sum(self.n.values())

    @property
    def scans(self) -> int:
        return sum(self.n.get(k, 0) for k in self.SCANS)


# MEASURED 2026-09-03 with this exact harness, after both D-239 fixes:
#   5 nodes -> 18 calls (2 scans) | 25 -> 21 (2) | 125 -> 24 (2)
# i.e. 0.05 calls per added node, and directory scans FLAT.
# Before the fixes the same harness measured 23 / 46 / 149 — 1.05 per node.
# The budget is set an order of magnitude above what the code does and an
# order of magnitude below what the two known regressions cost.
_FS_PER_NODE_BUDGET = 0.25


def filesystem_work_must_not_scale_with_node_count() -> None:
    """The rule D-239 states, in the only form that can fail.

    Every other check in this file guards an INSTANCE — this one guards the
    RULE, and it is the only thing here that would have caught `6190b83` on
    the day it landed, from a reviewer who had never heard of
    `cache_forecast_public`. It is also how the second violation was found:
    `accounts.serving_label` was loading and parsing `accounts.json` once per
    seat, 1:1 with org size, and nothing in the codebase said so.
    """
    build("zz-fs-small", archived=4)
    build("zz-fs-big", archived=124)
    # warm both: the FIRST render of an org creates scratch directories and
    # populates the transcript memo, which is setup cost, not render cost
    api.org_tree("zz-fs-small", request())
    api.org_tree("zz-fs-big", request())

    with CountFS() as small:
        api.org_tree("zz-fs-small", request())
    with CountFS() as big:
        api.org_tree("zz-fs-big", request())

    grew = big.total - small.total
    per_node = grew / 120.0
    assert per_node <= _FS_PER_NODE_BUDGET, (
        f"THE TREE RENDER IS DOING FILESYSTEM WORK PER NODE. "
        f"5-node org: {small.total} syscalls {small.n}; "
        f"125-node org: {big.total} syscalls {big.n}. "
        f"That is {per_node:.2f} extra calls per added seat, over the "
        f"{_FS_PER_NODE_BUDGET} budget. `annotate` runs for EVERY node on a "
        f"6 s heartbeat and on every save_org, so anything per-node here is "
        f"multiplied by the org and by the poll rate — see D-239, where one "
        f"such call (a glob per seat) made refresh take 11-38 s. Whatever you "
        f"just added to annotate: hoist it out of the loop, cache it per "
        f"render, or skip it for archived seats.")

    # …and the hard half. A directory scan is the expensive kind — the D-239
    # glob stat-ed 349 project dirs EACH TIME — so it may not grow at all.
    assert big.scans <= small.scans, (
        f"DIRECTORY SCANS NOW SCALE WITH ORG SIZE: {small.scans} at 5 nodes, "
        f"{big.scans} at 125. A per-node listdir/scandir/glob is the exact "
        f"shape of D-239 and it is never acceptable in a render path; build "
        f"the index once per request (`supervisor.transcript_index`) or "
        f"memoise the lookup (`supervisor.transcript_path`).")


# ── §7 · the children index is an ACCELERATOR, never a second answer ────────
def the_children_index_agrees_with_the_scan_everywhere() -> None:
    """`tree()` asks who every node's children are, and `children()` answered
    by scanning the whole node table each time — O(n²).

    The index that replaces the scan is only allowed to be faster, never
    different, and this is the check that says so: for EVERY parent slot in a
    real-shaped org (including `None`, the root slot), in both `live_only`
    modes, and through `org_children`'s successor filter as well, the indexed
    answer must equal the scanned one — same members, same ORDER, since the
    canvas lays cards out in it.

    Verified the same way against the live 189-node document while this was
    written: 190 parent slots × 2 modes × both entry points, all agreeing.
    """
    org = build("zz-children-index", archived=30)
    # a shape the flat fixture would miss: grandchildren, mixed states, and a
    # retired predecessor wearing a `successor` link (the one case
    # `org_children` filters and `children` does not)
    org.hire(USER, None, "haiku", 4, "second")
    org.hire(USER, "boss", "haiku", 0, "mid")
    org.hire(USER, "mid", "haiku", 0, "leaf")
    org.node("old0")["successor"] = "boss"
    store.save_org(org)

    idx = org.children_index()
    slots: list[str | None] = [None, *org.nodes.keys()]
    for nid in slots:
        for live_only in (True, False):
            assert org.children(nid, live_only=live_only, index=idx) \
                == org.children(nid, live_only=live_only), (
                    f"children({nid!r}, live_only={live_only}) disagrees "
                    f"between the index and the scan")
        assert org.org_children(nid, idx) == org.org_children(nid), (
            f"org_children({nid!r}) disagrees between the index and the scan")

    # and the index must partition ALL nodes — a node missing from it would
    # silently vanish from the canvas rather than fail anything above
    assert sum(len(v) for v in idx.values()) == len(org.nodes), (
        f"{sum(len(v) for v in idx.values())} indexed vs "
        f"{len(org.nodes)} nodes — the partition drops seats")


# ── §8 · the org the handler already holds is not read a second time ────────
def local_net_slugs_does_not_re_read_the_document_it_was_given() -> None:
    """`org_tree` needed one small string per org and was parsing every
    document on disk to get them — including the one it had parsed itself
    twenty lines earlier.

    MEASURED 2026-09-03 on the live install: `list_orgs()` cost 80.3 ms and
    read 18.7 MB, against a 233 ms floor for the whole endpoint; handing the
    already-parsed document back in took it to 58.6 ms (-53%).

    Two properties, and the first is the one that keeps it honest: the ANSWER
    must not change. A faster function that returns a different set would
    silently mislabel which hub peers are local, and nothing on the desk would
    look broken.
    """
    a = store.create_org("zz-net-a")
    b = store.create_org("zz-net-b")
    for org, tag in ((a, "aaa"), (b, "bbb")):
        org.d["net_identity"] = {"slug": f"zz-{tag}", "secret": "never-read"}
        store.save_org(org)

    # ① identical to the expression it replaces, whichever org is "loaded"
    want = {str(o.get("net_slug")) for o in store.list_orgs()
            if o.get("net_slug") and not o.get("kiosk")}
    assert {"zz-aaa", "zz-bbb"} <= want, want
    for org in (a, b):
        assert store.local_net_slugs(org.d) == want, (
            f"the set changed when {org.d['slug']!r} was the loaded org")
    assert store.local_net_slugs() == want, "the no-argument form disagrees"

    # ② the loaded document's FILE is not opened again — the whole point.
    # Counted rather than timed, because a timing assertion on a 7 MB parse
    # is a flake waiting for a busy machine.
    target = os.path.basename(store.org_path("zz-net-a"))
    opened: list[str] = []
    real = builtins.open

    def spy(f: Any, *args: Any, **kw: Any) -> Any:
        opened.append(os.path.basename(str(f)))
        return real(f, *args, **kw)

    builtins.open = spy
    try:
        store.local_net_slugs(a.d)
    finally:
        builtins.open = real
    assert target not in opened, (
        f"{target} was re-read although its parsed document was handed in — "
        f"opened: {opened}")
    assert os.path.basename(store.org_path("zz-net-b")) in opened, (
        "the OTHER orgs were not read at all; the skip is too wide and the "
        f"answer would be short — opened: {opened}")


# ── §9 · the ask history is capped to what the desk renders ─────────────────
def the_ask_history_is_capped_without_changing_what_the_desk_sees() -> None:
    """122,692 B of an 844 KB payload, every 6 s, for eight rendered rows.

    `App.tsx` renders `asks.filter(!askOpen).slice(-8)`; the payload shipped
    `[-60:]` of the whole history. MEASURED 2026-09-03 on the live org:
    122,692 B → 30,890 B, and what the desk renders is BYTE-IDENTICAL.

    The two properties that matter are opposites, which is why both are here:
    the history must get shorter, and the OPEN asks must not — an open ask is
    a question waiting on the user, and capping it away would lose it with
    nothing erroring.
    """
    org = store.create_org("zz-asks")
    from orgtree.ledger import ASK_HISTORY_KEEP, OPEN_ASK_STATUS
    n_open = ASK_HISTORY_KEEP + 5

    # ⚠ built through `ask_user`/`ask_answer`, never hand-rolled. A synthetic
    # entry missing `questions` makes `node_ask` raise on `tabs[0]` and the
    # whole tree 500s — which is how the first draft of this test failed, and
    # is fair warning that this row's shape is not one to guess at.
    org.hire(USER, None, "haiku", 4, "boss")
    # `_prune_asks` already caps the DOCUMENT at 30 resolved, so asking for
    # far more than that just exercises the existing prune; what matters here
    # is only that the document ends up holding MORE than the payload cap.
    for i in range(ASK_HISTORY_KEEP * 3):
        org.ask_user("boss", question=f"resolved {i}", options=["y", "n"])
        # `ask_user` returns {asked, status}, not the row — the id is the
        # entry it just appended
        org.ask_answer(str(org.d["asks"][-1]["id"]), selected=["y"])

    # ⚠ TOP-LEVEL, and that is not incidental: `ask_user` from a node holding
    # no user audience is ROUTED to its superior and creates no ask at all
    # ("you hold no user audience — the question was mailed to your
    # superior"). A fixture of child nodes yields ZERO open asks and every
    # check below passes vacuously.
    for i in range(n_open):                     # one OPEN ask per node: the
        nid = f"open{i}"                        # batch rule merges per node
        org.hire(USER, None, "haiku", 0, nid)
        org.ask_user(nid, question=f"open {i}", options=["y", "n"])
    store.save_org(org)

    doc = list(org.d.get("asks", []))
    doc_open = [a for a in doc if a["status"] in OPEN_ASK_STATUS]
    doc_res = [a for a in doc if a["status"] not in OPEN_ASK_STATUS]
    assert len(doc_open) == n_open, (len(doc_open), n_open)
    assert len(doc_res) > ASK_HISTORY_KEEP, (
        f"the fixture left only {len(doc_res)} resolved asks, at or under the "
        f"{ASK_HISTORY_KEEP} cap — nothing would be trimmed and this check "
        f"would pass without testing anything")

    got = org.tree()["asks"]
    kept_open = [a for a in got if a["status"] in OPEN_ASK_STATUS]
    kept_res = [a for a in got if a["status"] not in OPEN_ASK_STATUS]

    # ① every open ask survives, however many there are
    assert len(kept_open) == n_open, (
        f"{len(kept_open)} of {n_open} open asks survived the cap — an open "
        f"ask is a question waiting on the user and may never be dropped")

    # ② the resolved history is capped, and it is the NEWEST that are kept
    assert len(kept_res) == ASK_HISTORY_KEEP, len(kept_res)
    assert kept_res == doc_res[-ASK_HISTORY_KEEP:], (
        "the cap kept the wrong end of the history")

    # ③ the desk's own expression sees exactly what it would have seen from
    #    the uncapped list — the whole point of the change
    def desk(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [a for a in rows if a["status"] not in OPEN_ASK_STATUS][-8:]
    assert desk(got) == desk(doc), (
        "the desk's rendered asks changed; the cap is not transparent")

    # ④ ORDER is the document's, not status-grouped — a payload whose order
    #    depends on status is a trap for the next reader
    assert got == [a for a in doc if a in got], "the payload was reordered"


def the_ask_cap_cannot_drift_below_what_the_desk_slices() -> None:
    """A cross-file drift guard, because the two numbers live apart.

    If `App.tsx` ever renders more history than the backend ships, old asks
    silently stop appearing — no error, no log line, just a shorter list. The
    inequality is the contract, so it is read out of the source rather than
    restated here.
    """
    from orgtree.ledger import ASK_HISTORY_KEEP
    app = os.path.join(os.path.dirname(BACKEND), "frontend", "src", "App.tsx")
    with open(app, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"askDone\s*=\s*asks[^\n]*?\.slice\(-(\d+)\)", src)
    assert m, ("could not find the desk's ask-history slice in App.tsx — the "
               "expression moved, and this guard is now blind rather than "
               "satisfied. Re-point it before trusting a green run.")
    want = int(m.group(1))
    assert ASK_HISTORY_KEEP >= want, (
        f"App.tsx renders the last {want} resolved asks but the payload ships "
        f"only {ASK_HISTORY_KEEP} (ledger.ASK_HISTORY_KEEP). The desk's "
        f"history would silently be short. Raise ASK_HISTORY_KEEP.")


# ── §10 · the org inbox rides as a preview, and the log is fetched ──────────
def the_org_inbox_ships_a_preview_and_the_log_is_fetched() -> None:
    """105,310 B of an 844 KB payload, every 6 s, for a panel usually closed.

    The canvas renders exactly one org-inbox row — the newest — while the full
    log rode every poll. It now ships `ORG_INBOX_PREVIEW` rows and the modal
    fetches the rest from `GET /api/orgs/{slug}/org_inbox`.
    """
    from orgtree.ledger import ORG_INBOX_PREVIEW
    org = store.create_org("zz-inbox")
    n = ORG_INBOX_PREVIEW * 10
    log = [{"id": f"m{i:03d}", "at": f"2026-01-01T00:{i:02d}:00Z", "dir": "in",
            "peer": "@ext:someone", "body": f"message {i}"} for i in range(n)]
    org.d["org_inbox"] = log                      # pyright: ignore[reportGeneralTypeIssues]
    org.d["org_inbox_read"] = 0                   # pyright: ignore[reportGeneralTypeIssues]
    store.save_org(org)

    box = org.tree()["org_inbox"]
    # ① a preview, not the log
    assert len(box["entries"]) == ORG_INBOX_PREVIEW, len(box["entries"])
    # ② …and it is the NEWEST end, because the canvas renders
    #    `entries[entries.length - 1]` and would otherwise show ancient mail
    assert [e["id"] for e in box["entries"]] == \
        [f"m{i:03d}" for i in range(n - ORG_INBOX_PREVIEW, n)], box["entries"]
    # ③ `total` is the LOG's length, never the slice's. The desk's read ack is
    #    a high-water LENGTH; if this meant "rows in this payload" the ack
    #    would be captured against 3 and re-mark the rest unread.
    assert box["total"] == n, (box["total"], n)
    assert box["unread"] == n, box["unread"]

    # ④ the endpoint serves the log, with the same rule for `total`
    got = api.org_inbox_entries("zz-inbox")
    assert len(got["entries"]) == n, len(got["entries"])
    assert got["total"] == n, got["total"]
    assert [e["id"] for e in got["entries"]] == [e["id"] for e in log]

    # ⑤ the payload really got smaller — the reason any of this exists
    import json as _json
    assert len(_json.dumps(box)) < len(_json.dumps(log)) / 5, (
        "the preview is not meaningfully smaller than the log")


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
    check("§5 a remembered transcript is verified before it is served",
          a_remembered_transcript_is_verified_before_it_is_served)
    check("§6 filesystem work must not scale with node count",
          filesystem_work_must_not_scale_with_node_count)
    check("§7 the children index agrees with the scan everywhere",
          the_children_index_agrees_with_the_scan_everywhere)
    check("§8 local_net_slugs does not re-read the document it was given",
          local_net_slugs_does_not_re_read_the_document_it_was_given)
    check("§9 the ask history is capped without changing what the desk sees",
          the_ask_history_is_capped_without_changing_what_the_desk_sees)
    check("§9b the ask cap cannot drift below the desk's slice",
          the_ask_cap_cannot_drift_below_what_the_desk_slices)
    check("§10 the org inbox ships a preview and the log is fetched",
          the_org_inbox_ships_a_preview_and_the_log_is_fetched)
    print(f"\n{PASS} passed, {FAIL} FAILED")
finally:
    shutil.rmtree(RIG, ignore_errors=True)

sys.exit(1 if FAIL else 0)
