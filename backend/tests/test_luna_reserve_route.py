"""Item 12 — reserve-first Luna (user ruling 2026-09-04, amended the same
evening with the per-agent "Prefer reserve" checkbox).

    python backend/tests/test_luna_reserve_route.py   (no pytest)

THE RULING. `gpt-reserve` stops being a tier anyone hires. A `luna` hire
spends OpenAI's reserve pool first and falls back to the direct Luna lane when
reserve is spent or withdrawn — or, with its "Prefer reserve" box unticked,
the other way round. The two pools stay metered apart, the turn records the
tier it asked for AND the route it actually ran on, a route change is a cache
namespace change, and the header wears a token saying which pool the agent is
actually on.

WHAT IS MEASURED AND WHAT IS NOT — read this before trusting a green run:

  · the wire is `fakecodex`, transcribed from captured bytes for the wall's
    SHAPE (the 0.150.1 specimen behind D-209) and from the codex-cli 0.153.3
    protocol schema (`codex app-server generate-json-schema`, run against
    the PINNED binary) for the camelCase `usageLimitExceeded` tag, the
    object-form connection tags, the `model` field on `turn/start` and the
    thread responses' `model` echo. A granted reserve pool's bucket shape
    (`limitName: "gpt-reserve"`) is the 2026-09-03 measurement recorded in
    `codex_limits`.
  · what is NOT measured here: that a real app-server keeps the conversation
    when `turn/start` names a different model on a resumed thread. The
    schema documents the field ("Override the model for this turn and
    subsequent turns"); continuity across a switch is UNVERIFIED until a
    disposable live control runs. Nothing below claims it.
  · `reported_model` in the receipts is what the fake ECHOES; on a real
    server it is the thread's model as the server reports it, which is a
    receipt that the field was accepted, not a measurement of which weights
    answered.

Anti-vacuity: `tests/_mutate_luna_route.py` breaks the shipped code and
requires a NAMED check here to go red for each mutant.
"""

import json
import os
import sys
import tempfile
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-lunaroute-")
os.environ["ORGTREE_DATA"] = DATA
# a PORT NOBODY SERVES — this rig runs no backend, and the default 7360 would
# send a test's tool traffic to the operator's LIVE deployment
os.environ["ORGTREE_PORT"] = "9"
os.environ["ORGTREE_WARM"] = "0"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

HERE = os.path.dirname(os.path.abspath(__file__))
FAKECODEX = os.path.join(HERE, "fakecodex.py")
CODEX_HOME = tempfile.mkdtemp(prefix="lunaroute-chome-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME
# a ChatGPT-kind login: reserve is a subscription grant, and `codex_status`
# reads the kind off auth.json's token record
with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {"account_id": "acct-luna-test"}}')

from orgtree import codex_limits, codex_route, codexrun, providers   # noqa: E402
from orgtree import store, supervisor                                 # noqa: E402
from orgtree.ledger import USER, LedgerError                          # noqa: E402
import fakecodex                                                      # noqa: E402

assert os.path.realpath(store.DATA_ROOT) == os.path.realpath(DATA), (
    "store bound to the wrong root", store.DATA_ROOT)

PASS = 0
FAIL: list[tuple[str, str]] = []

STREAMED: list[dict] = []
supervisor.stream = lambda slug, nid, payload: STREAMED.append(dict(payload))
NOTIFIED: list[tuple[str, str, str]] = []
supervisor.notify = lambda slug, nid, event: NOTIFIED.append((slug, nid, event))
supervisor.CODEX_STEER_POLL = 0.2

R = codex_route.RESERVE_MODEL
D = codex_route.DIRECT_LUNA_MODEL
RES = codex_route.RESERVE_POOL
PLAN = codex_route.PLAN_POOL


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def app_prefer(value: bool) -> None:
    """Set the app-wide default in this test's throwaway root."""
    with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
        json.dump({"prefer_reserve": value}, f)


# ── board fixtures (normalized `codex_limits.snapshot()` shape) ─────────────

def W(model, pct, resets=None, active=None, observed=None):
    """One normalized window. `observed` = when this bucket was last seen
    (epoch); None = a board normalized without the observation ledger."""
    return {"model": model, "percent": float(pct),
            "resets_at": resets, "is_active": bool(active if active is not None
                                                   else pct >= 100),
            "observed_at": observed}


def ISO(epoch):
    return codex_route._iso(epoch)   # pyright: ignore[reportPrivateUsage]


def board(limits, *, stale=False, complete=True, available=True, age=1.0,
          account=None):
    """`codex_limits.snapshot()` shape. `account` = whose board (None = a
    board that never learned; the resolver treats None as compatible and a
    DIFFERENT account as no evidence)."""
    return {"available": available, "stale": stale, "complete": complete,
            "age": age, "limits": limits, "account": account}


NOW = 1_800_000_000.0
ACCT = "codex-chatgpt:test"


def resolve(b, *, prefer=True, marks=None, login="chatgpt", tier="luna"):
    return codex_route.resolve(tier, login_kind=login, board=b, marks=marks,
                               account=ACCT, now=NOW, prefer_reserve=prefer)


# ── org fixtures ────────────────────────────────────────────────────────────

def mkorg(label: str, tier: str = "luna", *, prefer=None) -> tuple[str, str]:
    org = store.create_org(f"zz lunaroute {label}")
    r = org.hire(USER, None, tier, 2, "lx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a luna route test agent")
    nid = r["node"]
    if prefer is not None:
        org.set_scope(USER, nid, prefer_reserve=prefer)
    store.save_org(org)
    return org.d["slug"], nid


def run_turn(slug: str, nid: str, text):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(slug, nid, text)


def node_doc(slug: str, nid: str) -> dict:
    return store.load_org(slug).node(nid)


def err_rows(slug: str, nid: str) -> list:
    return store.load_org(slug).d.get("turn_error_log", {}).get(nid, [])


def model_probe(path) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def turn_models(path) -> list[str]:
    return [r["model"] for r in model_probe(path) if r["method"] == "turn/start"]


def user_rows(slug: str, nid: str) -> int:
    """How many USER records the node's journal holds — the replay guard.
    The codex lane's journal is `journals/projects/<org>/<session>.jsonl`
    (`supervisor.journal_store`), one JSON record per line."""
    n = node_doc(slug, nid)
    sid = str(n.get("session_id") or "")
    p = os.path.join(supervisor.journal_store(), "projects", slug, sid + ".jsonl")
    assert os.path.exists(p), f"no journal at {p}"
    with open(p, encoding="utf-8") as f:
        return sum(1 for x in f if x.strip()
                   and json.loads(x).get("type") == "user")


def tree_nodes(payload: dict) -> dict[str, dict]:
    """The tree endpoint nests `roots` → `children`; flatten by id."""
    out: dict[str, dict] = {}

    def walk(n):
        out[n["id"]] = n
        for c in n.get("children") or []:
            walk(c)
    for r in payload.get("roots") or []:
        walk(r)
    assert out, f"empty tree payload: {list(payload.keys())} {str(payload)[:200]}"
    return out


def scenario(name, board_name="reserve-ok"):
    os.environ["FAKECODEX_SCENARIO"] = name
    os.environ["FAKECODEX_BOARD"] = board_name
    probe = os.path.join(tempfile.mkdtemp(prefix="lunaroute-probe-"), "models.jsonl")
    os.environ["FAKECODEX_MODELPROBE"] = probe
    codex_limits.invalidate()
    providers.codex_status(force=True)
    return probe


def main() -> int:
    # ───────────────────────────────────────────────────────────────────────
    print("§1 the resolver — reserve first (the default box)")

    def t_granted():
        r = resolve(board([W(R, 8), W(None, 12)]))
        eq((r["route"], r["model"], r["pool"], r["reason"], r["prefer"]),
           ("reserve", R, RES, "granted", RES), "reserve with room")
        eq(r["requested"], "luna", "the tier asked for is kept")
        eq(r["selection"], "preflight", "a preflight choice")
    check("§1 reserve granted with room → reserve", t_granted)

    def t_exhausted():
        r = resolve(board([W(R, 100, ISO(NOW + 400)), W(None, 12)]))
        eq((r["route"], r["model"], r["reason"]), ("direct", D, "reserve-exhausted"),
           "reserve spent → direct")
        eq(r["reset_ts"], NOW + 400, "carries reserve's reset")
    check("§1 reserve at 100% → direct, reason reserve-exhausted, with its "
          "reset", t_exhausted)

    def t_no_grant():
        r = resolve(board([W(None, 12)], complete=True))
        eq((r["route"], r["reason"], r["evidence"]),
           ("direct", "no-grant", "board-complete"),
           "absent from a COMPLETE board = withdrawn")
    check("§1 reserve absent from a complete board → direct, no-grant",
          t_no_grant)

    def t_sparse_absent():
        # the same absence on a board that was only ever patched by sparse
        # notifications says NOTHING — unknown prefers reserve
        r = resolve(board([W(None, 12)], complete=False))
        eq((r["route"], r["reason"]), ("reserve", "board-unknown"),
           "absence on a sparse-only board is not withdrawal")
    check("§1 ⚠ reserve absent from a SPARSE board → still reserve (unknown "
          "is not withdrawn)", t_sparse_absent)

    def t_stale_unknown():
        r = resolve(board([W(R, 100, ISO(NOW + 400))], stale=True))
        eq((r["route"], r["reason"]), ("reserve", "board-stale"),
           "a stale board decides nothing")
        r2 = resolve(board([], available=False, age=None))
        eq((r2["route"], r2["reason"]), ("reserve", "board-unknown"),
           "no board at all → reserve, the turn is the probe")
    check("§1 stale or missing board → reserve (unknown never refuses)",
          t_stale_unknown)

    def t_api_key():
        r = resolve(board([W(R, 8)]), login="api-key")
        eq((r["route"], r["reason"]), ("direct", "login-kind"),
           "an API-key login cannot hold reserve")
    check("§1 API-key login → direct, reason login-kind", t_api_key)

    def t_marks():
        live = {RES: codex_route.make_mark("exhausted", ACCT, NOW + 900, NOW, "x")}
        r = resolve(board([], available=False, age=None), marks=live)
        eq((r["route"], r["reason"]), ("direct", "reserve-marked:exhausted"),
           "a live mark pins direct when the board cannot say")
        # …and a board with room OUTRANKS the mark only when that pool was
        # observed AFTER the rejection (positive recovery must be newer)
        r2 = resolve(board([W(R, 8, observed=NOW + 5)]), marks=live)
        eq(r2["route"], "reserve", "room observed after the mark clears it")
        # a mark from ANOTHER account does not bind this one
        other = {RES: codex_route.make_mark("exhausted", "codex-chatgpt:someone",
                                            NOW + 900, NOW, "x")}
        r3 = resolve(board([], available=False, age=None), marks=other)
        eq(r3["route"], "reserve", "another account's mark is ignored")
        # an EXPIRED mark does not bind either
        old = {RES: codex_route.make_mark("exhausted", ACCT, NOW - 10, NOW - 1000, "x")}
        r4 = resolve(board([], available=False, age=None), marks=old)
        eq(r4["route"], "reserve", "an expired mark is ignored")
        # a mark with no provider reset is timed to the probe floor, never open
        m = codex_route.make_mark("no-grant", ACCT, None, NOW, "x")
        eq(m["until_ts"], NOW + codex_route.MARK_PROBE_FLOOR, "probe floor")
        eq(m["reset_src"], "probe", "labelled a probe")
        assert not codex_route.mark_live(m, ACCT, NOW + codex_route.MARK_PROBE_FLOOR + 1)
    check("§1 marks: scoped to the account, expire, and lose to NEWER room",
          t_marks)

    def t_mark_vs_old_board():
        # PARENT REVIEW CASE (2026-09-05): a board read 20 s ago says reserve
        # is at 20%; the provider rejected this node 1 s ago. The old board
        # does NOT establish recovery — the mark wins
        mark = {RES: codex_route.make_mark("exhausted", ACCT, NOW + 900, NOW - 1, "x")}
        old = board([W(R, 20, observed=NOW - 20), W(None, 12, observed=NOW - 20)])
        r = resolve(old, marks=mark)
        eq((r["route"], r["reason"]), ("direct", "reserve-marked:exhausted"),
           "old board / new mark → the other route")
        # a sparse update about the PLAN bucket does not refresh reserve's
        # evidence: reserve still observed at -20, mark still wins
        plan_refreshed = board([W(R, 20, observed=NOW - 20), W(None, 12, observed=NOW + 1)])
        r2 = resolve(plan_refreshed, marks=mark)
        eq(r2["route"], "direct", "an unrelated bucket's refresh is not recovery")
        # a GENUINELY newer positive observation of reserve → preferred pool
        newer = board([W(R, 20, observed=NOW + 2), W(None, 12, observed=NOW - 20)])
        r3 = resolve(newer, marks=mark)
        eq((r3["route"], r3["reason"]), ("reserve", "granted"),
           "newer room clears the mark")
        # a board with NO per-pool observation time cannot claim to be newer
        r4 = resolve(board([W(R, 20)]), marks=mark)
        eq(r4["route"], "direct", "no observation time → the mark stands")
        # stale / unknown controls: the mark decides alone
        r5 = resolve(board([W(R, 20, observed=NOW + 2)], stale=True), marks=mark)
        eq(r5["route"], "direct", "a stale board never clears a mark")
        r6 = resolve(board([], available=False, age=None), marks=mark)
        eq(r6["route"], "direct", "no board: the mark stands")
        # and without a mark, the old board's room is simply room
        r7 = resolve(old, marks=None)
        eq(r7["route"], "reserve", "no mark: an old board with room still routes reserve")
    check("§1 ⚠ a positive observation outranks a mark ONLY when it is newer "
          "than the rejection, per pool", t_mark_vs_old_board)

    def t_board_account():
        # a board stamped with ANOTHER account's namespace is no evidence
        # for this one: the resolver falls through to marks / unknown
        other = board([W(R, 100, ISO(NOW + 400)), W(None, 12)], account="codex-chatgpt:someone")
        r = resolve(other)
        eq((r["route"], r["reason"]), ("reserve", "board-unknown"),
           "another account's exhausted reserve is not this account's")
        mine = board([W(R, 100, ISO(NOW + 400)), W(None, 12)], account=ACCT)
        eq(resolve(mine)["reason"], "reserve-exhausted", "this account's board counts")
        unstamped = board([W(R, 100, ISO(NOW + 400)), W(None, 12)], account=None)
        eq(resolve(unstamped)["reason"], "reserve-exhausted",
           "a board that never learned its account is compatible (legacy)")
    check("§1 a board from another account is treated as no evidence",
          t_board_account)

    def t_both_out():
        r = resolve(board([W(R, 100, ISO(NOW + 4000)), W(None, 100, ISO(NOW + 2000))]))
        eq(r["route"], "reserve", "both out → the preferred pool asks the provider")
        assert r["reason"].startswith("both-out:"), r["reason"]
        eq(r["reset_ts"], NOW + 2000, "the earliest known reset rides along")
    check("§1 both pools out → sent to the preferred pool, reason both-out",
          t_both_out)

    def t_other_tiers():
        r = resolve(board([W(R, 8)]), tier="gpt-reserve")
        eq((r["route"], r["model"], r["reason"]), ("reserve", R, "legacy-tier"),
           "a legacy reserve node is reserve, full stop")
        eq(codex_route.other_route(r), None, "…and has nowhere else to go")
        r2 = resolve(board([W(None, 100, ISO(NOW + 5))]), tier="sol")
        eq((r2["route"], r2["model"], r2["reason"]),
           ("direct", "gpt-5.6-sol", "tier"), "other tiers are untouched")
        eq(codex_route.other_route(r2), None, "…and never re-route")
    check("§1 legacy gpt-reserve stays reserve-only; other tiers unchanged",
          t_other_tiers)

    # ───────────────────────────────────────────────────────────────────────
    print("§2 the resolver — plan first (the box unticked)")

    def t_plan_first():
        r = resolve(board([W(R, 8), W(None, 12)]), prefer=False)
        eq((r["route"], r["model"], r["reason"], r["prefer"]),
           ("direct", D, "preferred", PLAN), "plan with room → direct")
        r2 = resolve(board([W(R, 8), W(None, 100, ISO(NOW + 2000))]), prefer=False)
        eq((r2["route"], r2["reason"], r2["reset_ts"]),
           ("reserve", "direct-exhausted", NOW + 2000),
           "plan spent → reserve (off does NOT disable the fallback)")
        r3 = resolve(board([W(None, 100, ISO(NOW + 2000))], complete=True), prefer=False)
        eq(r3["route"], "direct", "plan spent AND no grant → the provider answers")
        assert r3["reason"].startswith("both-out:direct-exhausted,reserve-no-grant"), r3["reason"]
        r4 = resolve(board([], available=False, age=None), prefer=False)
        eq((r4["route"], r4["reason"]), ("direct", "board-unknown"),
           "unknown board → the preferred pool")
    check("§2 plan-first: direct while it has room, reserve when spent, "
          "provider answers when both are out", t_plan_first)

    def t_plan_mark():
        live = {PLAN: codex_route.make_mark("exhausted", ACCT, NOW + 900, NOW, "x")}
        r = resolve(board([], available=False, age=None), marks=live, prefer=False)
        eq((r["route"], r["reason"]), ("reserve", "direct-marked:exhausted"),
           "a plan mark sends a plan-first luna to reserve")
        r2 = resolve(board([W(None, 12, observed=NOW + 3)], complete=False),
                     marks=live, prefer=False)
        eq(r2["route"], "direct", "NEWER plan room outranks the plan mark too")
        r3 = resolve(board([W(None, 12, observed=NOW - 3)], complete=False),
                     marks=live, prefer=False)
        eq(r3["route"], "reserve", "older plan room does not")
    check("§2 plan-first: a live plan mark routes to reserve; only NEWER room "
          "outranks it", t_plan_mark)

    def t_retry_routes():
        r = resolve(board([W(R, 8)]))
        o = codex_route.other_route(r)
        assert o is not None
        eq((o["route"], o["model"], o["selection"], o["reason"], o["prefer"]),
           ("direct", D, "retry", "reserve-rejected", RES), "reserve → direct retry")
        r2 = resolve(board([W(R, 8), W(None, 12)]), prefer=False)
        o2 = codex_route.other_route(r2)
        assert o2 is not None
        eq((o2["route"], o2["model"], o2["selection"], o2["prefer"]),
           ("reserve", R, "retry", PLAN), "direct → reserve retry keeps the box")
    check("§2 other_route: the retry names the other pool, is marked a retry, "
          "and keeps the preference", t_retry_routes)

    # ───────────────────────────────────────────────────────────────────────
    print("§3 pool capacity and the wake time (audit F2, per pool)")

    def t_capacity():
        # reserve spent on BOTH its session and weekly windows: the LATEST
        # reset is the pool's, because every constraint must clear
        cap = codex_route.pool_capacity(
            [W(R, 100, ISO(NOW + 3600)), W(R, 100, ISO(NOW + 86400)), W(None, 5)], RES)
        eq((cap["state"], cap["reset_ts"], cap["reset_unknown"]),
           ("exhausted", NOW + 86400, False), "latest exhausted reset")
        # one exhausted window with NO reset → unknown, not recovered
        cap2 = codex_route.pool_capacity([W(R, 100, None), W(R, 100, ISO(NOW + 5))], RES)
        eq((cap2["state"], cap2["reset_ts"], cap2["reset_unknown"]),
           ("exhausted", None, True), "an unknown reset stays unknown")
        # a session window spent and the weekly open is STILL exhausted now
        cap3 = codex_route.pool_capacity([W(R, 100, ISO(NOW + 60)), W(R, 40)], RES)
        eq(cap3["state"], "exhausted", "any spent window spends the pool")
        # a bucket named after ANOTHER model constrains neither pool
        eq(codex_route.pool_capacity([W("GPT-Spark", 100, ISO(NOW + 9))], RES)["state"],
           "absent", "a Spark grant is not reserve")
        eq(codex_route.pool_capacity([W("GPT-Spark", 100, ISO(NOW + 9))], PLAN)["state"],
           "absent", "…and not the plan")
    check("§3 pool_capacity: latest exhausted reset per pool; unknown stays "
          "unknown; other models' buckets constrain neither", t_capacity)

    def t_wake():
        lim = [W(R, 100, ISO(NOW + 4000)), W(None, 100, ISO(NOW + 2000))]
        eq(codex_route.node_wake_epoch(lim, None), NOW + 2000,
           "the earliest pool with a known reset")
        # the turn's own snapshots push a pool's reset LATER: still per pool
        snaps = {"codex": {"limitId": "codex", "limitName": None,
                           "primary": {"usedPercent": 100, "resetsAt": NOW + 6000}}}
        eq(codex_route.node_wake_epoch(lim, snaps), NOW + 4000,
           "plan now resets at +6000, so reserve's +4000 is the earliest")
        eq(codex_route.node_wake_epoch([W(R, 100, None)], None), None,
           "no known reset anywhere → None (caller probes)")
        # ⚠ NOT the soonest window on the board: a reserve session window at
        # +100 beside its weekly at +4000 does not wake the node at +100
        lim2 = [W(R, 100, ISO(NOW + 100)), W(R, 100, ISO(NOW + 4000)),
                W(None, 100, ISO(NOW + 2000))]
        eq(codex_route.node_wake_epoch(lim2, None), NOW + 2000,
           "reserve's constraints all clear at +4000; plan's at +2000")
    check("§3 node_wake_epoch: min over pools of (max over that pool's "
          "exhausted resets), never the board's soonest window", t_wake)

    def t_failure_schedule():
        board = {"available": True, "account": "acct-A", "age": 0.0,
                 "stale": False,
                 "limits": [W(None, 100, ISO(NOW + 1000)),
                            W(None, 100, ISO(NOW + 7000)),
                            W(R, 100, ISO(NOW + 3000))]}
        direct = codex_route.direct_route(
            "terra", "gpt-5.6-terra", "acct-A", reason="test",
            evidence="synthetic")
        eq(codex_route.failure_schedule(direct, board, None, PLAN, now=NOW),
           (NOW + 7000, "observed-deadline"),
           "single pool waits for every exhausted constraint")
        luna = codex_route._route_for(  # pyright: ignore[reportPrivateUsage]
            "luna", RES, "acct-A", reason="test", evidence="synthetic")
        eq(codex_route.failure_schedule(luna, board, None, RES, now=NOW),
           (NOW + 3000, "probe"),
           "earlier alternative-pool reset is only a probe")
        foreign = {**board, "account": "acct-B"}
        eq(codex_route.failure_schedule(direct, foreign, None, PLAN, now=NOW),
           (None, "probe"), "another account's board is not recovery")
        eq(codex_route.failure_schedule(direct, board, None, None, now=NOW),
           (None, "probe"), "unknown served pool is not attributed")
    check("§3 F2 failure schedule: one pool uses its latest constraint; Luna's "
          "alternative is a probe; cross-account/unknown evidence fails closed",
          t_failure_schedule)

    # ───────────────────────────────────────────────────────────────────────
    print("§4 the failure classifier — what may be re-sent")

    def cls(error, *, status="failed", items=0, usage=None, text="",
            snaps=None, pool=RES, b=None, prose=False):
        return codex_route.classify_failure(
            status=status, error=error, snapshots=snaps, items_seen=items,
            token_usage=usage, agent_text=text, pool=pool, board=b,
            usage_prose=prose)

    def t_rejected():
        for tag in ("usage_limit_exceeded", "usageLimitExceeded"):
            c = cls({"message": fakecodex.LIMIT_MESSAGE, "codexErrorInfo": tag})
            eq((c["kind"], c["rejected"]), (codex_route.KIND_USAGE_LIMIT, True),
               f"terminal usage-limit tag {tag!r} with nothing run")
        c = cls({"message": "x", "codexErrorInfo": {"type": "usage_limit_exceeded"}})
        eq(c["rejected"], True, "the tagged-object spelling too")
    check("§4 an explicit usageLimitExceeded (either spelling) with nothing "
          "run IS a rejection", t_rejected)

    def t_not_rejected():
        usage = {"message": fakecodex.LIMIT_MESSAGE, "codexErrorInfo": "usageLimitExceeded"}
        eq(cls(usage, items=1)["rejected"], False, "an item was observed")
        eq(cls(usage, usage={"last": {"outputTokens": 3}})["rejected"], False,
           "token usage was observed")
        eq(cls(usage, text="hello")["rejected"], False, "text was observed")
        eq(cls(usage, status=None)["kind"], codex_route.KIND_UNKNOWN,
           "no status at all (timeout) is UNKNOWN")
        eq(cls(usage, status=None)["rejected"], False, "…and never a rejection")
        # PARENT REVIEW CASE (2026-09-05): the usage tag on a turn whose
        # terminal status is NOT "failed" is not a terminal rejection
        for st_ in ("interrupted", "completed", "inProgress", ""):
            c = cls(usage, status=st_)
            eq(c["rejected"], False, f"status {st_!r} with a usage tag is not a rejection")
        eq(cls(usage, status="failed")["rejected"], True, "…failed is (the leg that holds)")
        for tag, kind in (("unauthorized", codex_route.KIND_AUTH),
                          ("rateLimitExceeded", codex_route.KIND_RATE_LIMIT),
                          ("contextWindowExceeded", codex_route.KIND_CONTEXT),
                          ("serverOverloaded", codex_route.KIND_OVERLOADED),
                          ({"responseStreamDisconnected": {"httpStatusCode": None}},
                           codex_route.KIND_CONNECTION),
                          ({"httpConnectionFailed": {}}, codex_route.KIND_CONNECTION),
                          ("sandbox_error", codex_route.KIND_OTHER)):
            c = cls({"message": "m", "codexErrorInfo": tag})
            eq((c["kind"], c["rejected"]), (kind, False), f"{tag!r} is {kind}, not a rejection")
        # a usage limit read from PROSE with no machine tag: freezes as
        # today, but is NOT the explicit rejection a re-drive needs
        c = cls({"message": fakecodex.LIMIT_MESSAGE}, prose=True)
        eq((c["kind"], c["rejected"]), (codex_route.KIND_USAGE_PROSE, False),
           "prose-only limit is not a re-drive")
        # no error, status failed: unknown
        c = cls(None)
        eq((c["kind"], c["rejected"]), (codex_route.KIND_UNKNOWN, False), "no error")
    check("§4 ⚠ NOT a rejection: anything observed, a timeout, auth, 429, "
          "context, overload, a connection tag, an unknown tag, prose alone",
          t_not_rejected)

    def t_pool_state():
        usage = {"message": "m", "codexErrorInfo": "usageLimitExceeded"}
        snaps = {"base_model_inference": {
            "limitId": "base_model_inference", "limitName": R,
            "primary": {"usedPercent": 100, "resetsAt": NOW + 4000},
            "secondary": {"usedPercent": 100, "resetsAt": NOW + 8000}}}
        c = cls(usage, snaps=snaps)
        eq((c["pool_state"], c["reset_ts"]), ("exhausted", NOW + 8000),
           "the turn's own snapshot: exhausted, LATEST reset")
        # absent from the turn's sparse snapshots + a complete board without
        # the bucket: no-grant
        c2 = cls(usage, snaps={"codex": {"limitId": "codex", "primary": None}},
                 b=board([W(None, 12)], complete=True))
        eq(c2["pool_state"], "no-grant", "complete board, bucket absent")
        # …but the same absence on a SPARSE board is unexplained, not no-grant
        c3 = cls(usage, snaps={}, b=board([W(None, 12)], complete=False))
        eq(c3["pool_state"], "unexplained", "sparse board cannot say withdrawn")
        # the plan pool's own wall
        c4 = cls(usage, pool=PLAN, snaps={"codex": {"limitId": "codex",
                 "primary": {"usedPercent": 100, "resetsAt": NOW + 2000}}})
        eq((c4["pool_state"], c4["reset_ts"]), ("exhausted", NOW + 2000), "plan wall")
    check("§4 pool_state: exhausted from the turn's snapshot (latest reset), "
          "no-grant only from a complete board, else unexplained", t_pool_state)

    def t_unnamed_attribution():
        # MEASURED 2026-09-05T01:20Z (live control, codex-cli 0.153.0): a
        # per-turn notification carries `limitId: "codex"` and NO limitName
        # whichever pool served the turn. An unnamed bucket in a turn's own
        # snapshots therefore describes the pool the turn was SENT to.
        unnamed = {"codex": {"limitId": "codex", "limitName": None,
                             "primary": {"usedPercent": 100, "resetsAt": NOW + 4000}}}
        eq(codex_route.snapshots_pool_reset(unnamed, RES, sent_pool=RES),
           (True, NOW + 4000), "sent to reserve: the unnamed wall is reserve's")
        eq(codex_route.snapshots_pool_reset(unnamed, PLAN, sent_pool=RES),
           (False, None), "…and not the plan's")
        eq(codex_route.snapshots_pool_reset(unnamed, PLAN, sent_pool=PLAN),
           (True, NOW + 4000), "sent to the plan: it is the plan's")
        eq(codex_route.snapshots_pool_reset(unnamed, PLAN),
           (True, NOW + 4000), "no sent pool (a full board read): unnamed = plan")
        # a NAMED bucket is filed by its name whatever the turn was sent as
        named = {"x": {"limitId": "x", "limitName": R,
                       "primary": {"usedPercent": 100, "resetsAt": NOW + 9}}}
        eq(codex_route.snapshots_pool_reset(named, RES, sent_pool=PLAN),
           (True, NOW + 9), "a named reserve bucket is reserve's even on a plan turn")
        # the classifier reads the sent pool: a reserve turn's unnamed wall
        # is `exhausted` WITH the reserve reset
        c = cls({"message": "m", "codexErrorInfo": "usageLimitExceeded"},
                snaps=unnamed, pool=RES)
        eq((c["pool_state"], c["reset_ts"]), ("exhausted", NOW + 4000), "classifier")
        # …and the shared board folds it into the RESERVE bucket, leaving
        # the plan bucket's numbers alone
        scenario("tool", "reserve-ok")
        codex_limits.fetch(force=True)
        before = codex_limits.snapshot()
        plan_before = [w["percent"] for w in before["limits"] if w.get("model") is None]
        codex_limits.observe({"limitId": "codex", "limitName": None,
                              "primary": {"usedPercent": 100,
                                          "resetsAt": time.time() + 4000}},
                             pool_hint=RES)
        after = codex_limits.snapshot()
        res_after = [w["percent"] for w in after["limits"] if w.get("model") == R]
        plan_after = [w["percent"] for w in after["limits"] if w.get("model") is None]
        eq(max(res_after), 100.0, "the reserve bucket took the wall")
        eq(plan_after, plan_before, "the plan bucket is untouched")
        eq(codex_route.pool_capacity(after["limits"], RES)["state"], "exhausted", "reserve out")
        eq(codex_route.pool_capacity(after["limits"], PLAN)["state"], "usable", "plan usable")
        # without the hint (a plan turn) the same notification is the plan's
        codex_limits.fetch(force=True)
        codex_limits.observe({"limitId": "codex", "limitName": None,
                              "primary": {"usedPercent": 100,
                                          "resetsAt": time.time() + 2000}},
                             pool_hint=PLAN)
        after2 = codex_limits.snapshot()
        eq(codex_route.pool_capacity(after2["limits"], PLAN)["state"], "exhausted", "plan out")
        eq(codex_route.pool_capacity(after2["limits"], RES)["state"], "usable", "reserve untouched")
        codex_limits.invalidate()
    check("§4 ⚠ an UNNAMED per-turn notification describes the pool the turn "
          "was SENT to (measured 2026-09-05); named buckets keep their name",
          t_unnamed_attribution)

    def t_reroute_overrides():
        # `model/rerouted` overrides the sent-pool assumption: the selected
        # pool is never called the served pool when the server said it
        # served something else
        r = resolve(board([W(R, 8)]))          # sent to reserve
        eq(codex_route.served_pool(r, None), RES, "no reroute: the sent pool")
        eq(codex_route.served_pool(r, {"fromModel": R, "toModel": D}), PLAN,
           "rerouted to the direct model: the plan pool served")
        eq(codex_route.served_pool(r, {"fromModel": R, "toModel": "gpt-9-mystery"}), None,
           "rerouted to an unknown model: attribution unknown")
        eq(codex_route.served_pool(r, {"fromModel": R, "toModel": ""}), None, "empty: unknown")
        d = resolve(board([W(R, 100, ISO(NOW + 5))]))   # sent direct
        eq(codex_route.served_pool(d, {"fromModel": D, "toModel": R}), RES,
           "a direct turn rerouted onto reserve: reserve served")
        # the classifier follows: a reserve-sent turn whose wall arrived
        # after a reroute onto the plan does NOT mark reserve exhausted
        unnamed = {"codex": {"limitId": "codex", "limitName": None,
                             "primary": {"usedPercent": 100, "resetsAt": NOW + 4000}}}
        c = cls({"message": "m", "codexErrorInfo": "usageLimitExceeded"},
                snaps=unnamed, pool=RES)
        eq(c["pool_state"], "exhausted", "sent and served reserve: reserve exhausted")
        c2 = codex_route.classify_failure(
            status="failed", error={"message": "m", "codexErrorInfo": "usageLimitExceeded"},
            snapshots=unnamed, items_seen=0, token_usage=None, agent_text="",
            pool=RES, board=None, served=PLAN)
        # the wall is the PLAN's (the pool that served), never reserve's
        eq((c2["attributed"], c2["pool_state"], c2["redrive"]), (PLAN, "exhausted", False),
           "attributed to the serving pool; not re-driven")
        c3 = codex_route.classify_failure(
            status="failed", error={"message": "m", "codexErrorInfo": "usageLimitExceeded"},
            snapshots=unnamed, items_seen=0, token_usage=None, agent_text="",
            pool=RES, board=None, served=None)
        eq((c3["pool_state"], c3["attributed"]), ("unattributed", None),
           "unknown serving pool: nothing attributed")
        assert c3["rejected"], "…but the terminal rejection itself still stands"
        assert not c3["redrive"], "…and it is not re-driven"
    check("§4 model/rerouted overrides the sent-pool attribution; an unknown "
          "destination attributes nothing", t_reroute_overrides)

    # ───────────────────────────────────────────────────────────────────────
    print("§5 the header token label")

    def t_label():
        r = resolve(board([W(R, 8)]))
        eq(codex_route.route_label(r, live=True), "reserve", "live on reserve")
        eq(codex_route.route_label(r, live=False), "last: reserve",
           "the same route, not live, SAYS last")
        d = resolve(board([W(R, 100, ISO(NOW + 5))]))
        eq(codex_route.route_label(d, live=True), "direct · reserve out",
           "direct because reserve is out")
        eq(codex_route.route_label(d, live=False), "last: direct · reserve out", "…last")
        ng = resolve(board([W(None, 5)], complete=True))
        eq(codex_route.route_label(ng, live=True), "direct · reserve out", "no grant")
        pf = resolve(board([W(R, 8), W(None, 5)]), prefer=False)
        eq(codex_route.route_label(pf, live=True), None,
           "plan-first on direct by preference: nothing to disclose")
        pr = resolve(board([W(R, 8), W(None, 100, ISO(NOW + 5))]), prefer=False)
        eq(codex_route.route_label(pr, live=True), "reserve",
           "plan-first that FELL BACK to reserve wears reserve — actual, not the box")
        ak = resolve(board([W(R, 8)]), login="api-key")
        eq(codex_route.route_label(ak, live=True), None, "api-key direct: nothing")
        eq(codex_route.route_label(resolve(board([]), tier="sol"), live=True), None,
           "other tiers: nothing")
        eq(codex_route.route_label(None, live=True), None, "no route: nothing")
    check("§5 route_label: actual route, 'last:' when not live, silent when "
          "there is nothing to disclose", t_label)

    # ───────────────────────────────────────────────────────────────────────
    print("§6 end to end — reserve first, reserve serves")
    probe = scenario("tool", "reserve-ok")
    slug, nid = mkorg("res-ok")

    def t_e2e_reserve():
        NOTIFIED.clear()
        run_turn(slug, nid, "do the thing")
        st = supervisor.state(slug, nid)
        eq(st["turns_run"], 1, "a completed turn")
        eq(st["last_error"], None, "no error")
        eq(turn_models(probe), [R], "ONE turn/start, on the reserve model")
        n = node_doc(slug, nid)
        ring = n.get("turns") or []
        eq(len(ring), 1, "one ring entry")
        rt = ring[0].get("route")
        assert isinstance(rt, dict), ring[0]
        eq((rt["route"], rt["pool"], rt["model"], rt["selection"], rt["prefer"]),
           ("reserve", RES, R, "preflight", RES), "the ring carries the route")
        eq(rt["reported_model"], R, "the provider's echo, recorded apart")
        assert rt.get("account"), "the account dimension rides the receipt"
        last = n.get("codex_route_last")
        assert isinstance(last, dict), n.keys()
        eq((last["route"], last["outcome"]), ("reserve", "completed"), "persisted last route")
        live = st.get("codex_route")
        assert isinstance(live, dict), live
        eq(live["live"], False, "not live once the turn ended")
        eq(codex_route.route_label(live, live=False), "last: reserve", "header label")
        assert "codex_routes" not in n, f"no mark on a clean turn: {n.get('codex_routes')}"
    check("§6 a luna turn with reserve granted runs on gpt-reserve; ring, "
          "node and state carry the receipt", t_e2e_reserve)

    def t_cache_ns():
        org = store.load_org(slug)
        snap = supervisor._cache_snapshot(org, nid, include_history=False)
        eq(snap["model"], R, "the cache namespace hashes the ROUTE's model")
        eq(snap.get("pool"), RES, "…and carries the pool")
        # flip the board to reserve-exhausted (cache-only read: fold a sparse
        # notification) and the forecast moves with it — a namespace change
        codex_limits.observe({"limitId": "base_model_inference", "limitName": R,
                              "primary": {"usedPercent": 100,
                                          "resetsAt": time.time() + 4000}})
        snap2 = supervisor._cache_snapshot(org, nid, include_history=False)
        eq(snap2["model"], D, "reserve out → the forecast names the direct model")
        eq(snap2.get("pool"), PLAN, "…and the plan pool")
        assert snap2["fingerprint"] != snap["fingerprint"], "a route flip is a namespace change"
        codex_limits.invalidate()
    check("§6 the cache namespace follows the route (a flip is a namespace "
          "change), from cached evidence only", t_cache_ns)

    # ───────────────────────────────────────────────────────────────────────
    print("§7 end to end — reserve rejected at the wire, re-driven on direct")
    probe = scenario("reserve_wall", "reserve-ok")
    slug2, nid2 = mkorg("res-wall")

    def t_redrive():
        NOTIFIED.clear()
        t0 = time.time()
        run_turn(slug2, nid2, "hello there")
        st = supervisor.state(slug2, nid2)
        eq(turn_models(probe), [R, D],
           "reserve was asked first, rejected, then direct — exactly two")
        eq(st["turns_run"], 1, "the re-driven turn COMPLETED")
        eq(st["last_error"], None, "…with no standing error")
        n = node_doc(slug2, nid2)
        eq(n.get("frozen"), None, "a rejection with a fallback freezes nothing")
        rows = err_rows(slug2, nid2)
        assert any("route switched" in r["text"] for r in rows), rows
        assert any("reserve route rejected" in r["text"] for r in rows), rows
        assert (slug2, nid2, "route_switched") in NOTIFIED, NOTIFIED
        # the receipt says RETRY on direct
        rt = (n.get("turns") or [])[-1].get("route")
        eq((rt["route"], rt["model"], rt["selection"], rt["reason"]),
           ("direct", D, "retry", "reserve-rejected"), "the ring names the retry")
        # the mark: scoped to the account, timed to the PROVIDER's reserve reset
        mark = (n.get("codex_routes") or {}).get(RES)
        assert isinstance(mark, dict), n.get("codex_routes")
        eq(mark["kind"], "exhausted", "the turn's own snapshot said exhausted")
        eq(mark["account"], supervisor._codex_account_namespace(), "account-scoped")
        eq(mark["reset_src"], "provider", "timed from the provider's resetsAt")
        assert abs(float(mark["until_ts"]) - (t0 + fakecodex.RESERVE_RESET_IN)) < 120, mark
        # ⚠ the user's row is journaled ONCE: the retry inherits the open
        # journal rather than writing the prompt a second time
        eq(user_rows(slug2, nid2), 1, "one user record for one message")
        eq(codex_route.route_label(n["codex_route_last"], live=False),
           "last: direct · reserve out", "the header says direct, reserve out")
    check("§7 a terminal reserve rejection with nothing run is re-driven "
          "ONCE on direct: no freeze, a durable row, a scoped mark, one user "
          "row", t_redrive)

    def t_next_turn_direct():
        # the wall's notification folded into the shared board: the NEXT
        # turn goes direct PREFLIGHT, without asking reserve again
        probe2 = os.path.join(tempfile.mkdtemp(prefix="lunaroute-probe-"), "m.jsonl")
        os.environ["FAKECODEX_MODELPROBE"] = probe2
        run_turn(slug2, nid2, "again")
        eq(turn_models(probe2), [D], "one turn/start, direct, no reserve attempt")
        n = node_doc(slug2, nid2)
        rt = (n.get("turns") or [])[-1].get("route")
        eq((rt["route"], rt["selection"]), ("direct", "preflight"), "preflight direct")
        assert rt["reason"] in ("reserve-exhausted", "reserve-marked:exhausted"), rt
        # a completed DIRECT turn does not clear reserve's mark (it says
        # nothing about reserve)
        assert RES in (n.get("codex_routes") or {}), "reserve's mark survives a direct turn"
    check("§7 the next turn resolves direct PREFLIGHT from the folded board / "
          "mark — no second rejected request", t_next_turn_direct)

    # ───────────────────────────────────────────────────────────────────────
    print("§8 end to end — plan first, plan rejected, reserve serves")
    probe = scenario("plan_wall", "reserve-ok")
    slug3, nid3 = mkorg("plan-wall", prefer=False)

    def t_plan_first_e2e():
        eq(store.load_org(slug3).prefer_reserve_for(nid3), False, "the box is off")
        run_turn(slug3, nid3, "go")
        st = supervisor.state(slug3, nid3)
        eq(turn_models(probe), [D, R], "direct first (the box), rejected, then reserve")
        eq(st["turns_run"], 1, "completed on reserve")
        n = node_doc(slug3, nid3)
        rt = (n.get("turns") or [])[-1].get("route")
        eq((rt["route"], rt["prefer"], rt["selection"]), ("reserve", PLAN, "retry"),
           "actual route reserve, preference plan, a retry")
        eq(codex_route.route_label(n["codex_route_last"], live=False), "last: reserve",
           "the header wears the ACTUAL pool, not the checkbox")
        mark = (n.get("codex_routes") or {}).get(PLAN)
        assert mark and mark["kind"] == "exhausted", n.get("codex_routes")
        eq(n.get("frozen"), None, "no freeze")
    check("§8 box unticked: direct asked first, rejected, reserve serves; the "
          "token says reserve (actual), the mark is on the plan pool",
          t_plan_first_e2e)

    def t_plan_first_clean():
        probe4 = scenario("tool", "reserve-ok")
        slug4, nid4 = mkorg("plan-ok", prefer=False)
        run_turn(slug4, nid4, "go")
        eq(turn_models(probe4), [D], "plan-first with room: direct, one attempt")
        n = node_doc(slug4, nid4)
        rt = (n.get("turns") or [])[-1].get("route")
        eq((rt["route"], rt["reason"], rt["prefer"]), ("direct", "preferred", PLAN), "preferred")
        eq(codex_route.route_label(n["codex_route_last"], live=False), None,
           "nothing to disclose")
    check("§8 box unticked with room: direct by preference, no token",
          t_plan_first_clean)

    def t_recovery_clears_mark():
        # the plan-first node above carries a PLAN mark from its wall. A
        # fresh complete board with plan room outranks the mark (positive
        # recovery), the turn runs direct, completes — and the completed
        # turn CLEARS the plan mark, so a stale rejection cannot outlive
        # the evidence that contradicts it
        assert PLAN in (node_doc(slug3, nid3).get("codex_routes") or {}), "precondition"
        probe5 = scenario("tool", "reserve-ok")
        run_turn(slug3, nid3, "once more")
        eq(turn_models(probe5), [D], "direct preflight: fresh room outranks the mark")
        n = node_doc(slug3, nid3)
        eq(supervisor.state(slug3, nid3)["turns_run"], 2, "completed")
        assert PLAN not in (n.get("codex_routes") or {}),             f"a completed plan turn must clear the plan mark: {n.get('codex_routes')}"
    check("§8 a completed turn on a pool CLEARS that pool's mark (positive "
          "recovery)", t_recovery_clears_mark)

    # ───────────────────────────────────────────────────────────────────────
    print("§9 end to end — both pools out")
    probe = scenario("both_wall", "reserve-ok")
    slug5, nid5 = mkorg("both-wall")

    def t_both_wall():
        NOTIFIED.clear()
        t0 = time.time()
        run_turn(slug5, nid5, "anything")
        st = supervisor.state(slug5, nid5)
        eq(turn_models(probe), [R, D], "reserve, then direct, then nothing more")
        eq(st["turns_run"], 0, "not a completed turn")
        assert st["last_error"] and "both Luna routes" in st["last_error"], st["last_error"]
        n = node_doc(slug5, nid5)
        fz = n.get("frozen")
        assert isinstance(fz, dict), "both out → the ordinary provider freeze"
        eq(fz.get("limit"), True, "a LIMIT freeze")
        eq(fz.get("reset_src"), "provider", "timed from the provider")
        # the node wakes at the EARLIEST pool with a known reset: the plan's
        # (2 days) before reserve's (4 days)
        want = t0 + fakecodex.PLAN_RESET_IN
        assert abs(float(fz["until_ts"]) - want) < 120, (fz["until_ts"], want, fz)
        routes = n.get("codex_routes") or {}
        assert RES in routes and PLAN in routes, routes
        assert (slug5, nid5, "frozen") in NOTIFIED
        eq(user_rows(slug5, nid5), 1, "still one user row")
        assert supervisor.resumable(n), "▶ can resume it"
    check("§9 both pools reject: freeze (limit), wake at the earliest pool's "
          "own reset, both marks written, one user row", t_both_wall)

    # ───────────────────────────────────────────────────────────────────────
    print("§10 containment — what must NOT be re-driven")
    probe = scenario("reserve_wall_after_output", "reserve-ok")
    slug6, nid6 = mkorg("partial")

    def t_partial():
        run_turn(slug6, nid6, "write me a poem")
        st = supervisor.state(slug6, nid6)
        eq(turn_models(probe), [R], "⚠ ONE attempt: output was produced, no replay")
        eq(st["turns_run"], 0, "the wall is not a completed turn")
        n = node_doc(slug6, nid6)
        fz = n.get("frozen")
        assert isinstance(fz, dict) and fz.get("limit"), "frozen through the ordinary path"
        assert not any("route switched" in r["text"] for r in err_rows(slug6, nid6))
        rt = n.get("codex_route_last")
        eq((rt["route"], rt["outcome"]), ("reserve", codex_route.KIND_USAGE_LIMIT),
           "the receipt records the reserve attempt and its verdict")
    check("§10 a usage limit AFTER output: not re-driven, frozen as before",
          t_partial)

    probe = scenario("reserve_disconnect", "reserve-ok")
    slug7, nid7 = mkorg("disconnect")

    def t_disconnect():
        run_turn(slug7, nid7, "hello")
        st = supervisor.state(slug7, nid7)
        eq(turn_models(probe), [R], "⚠ ONE attempt: an unknown outcome is never replayed")
        eq(st["turns_run"], 0, "not a completed turn")
        assert st["last_error"], "loud"
        n = node_doc(slug7, nid7)
        eq(n.get("frozen"), None, "a transport failure is not a capacity fact")
        assert "codex_routes" not in n, "…and writes no mark"
        eq(n["codex_route_last"]["outcome"], codex_route.KIND_CONNECTION, "classified")
    check("§10 a stream disconnect on reserve: not re-driven, not frozen, no "
          "mark, error kept", t_disconnect)

    # ───────────────────────────────────────────────────────────────────────
    print("§11 legacy gpt-reserve nodes and the hire door")

    def t_legacy_runs():
        probe8 = scenario("reserve_wall", "reserve-ok")
        # an EXISTING gpt-reserve node: built directly in the document, the
        # way every pre-ruling org carries one — the hire door refuses new ones
        org = store.create_org("zz lunaroute legacy")
        r = org.hire(USER, None, "luna", 2, "old", add_dirs=[],
                     tools={"bash": True, "web": False, "edit": True,
                            "subagents": False, "mcp": []},
                     org_visibility="team", charter="legacy reserve agent")
        lid = r["node"]
        org.node(lid)["model"] = "gpt-reserve"
        store.save_org(org)
        lslug = org.d["slug"]
        run_turn(lslug, lid, "hi")
        eq(turn_models(probe8), [R], "reserve only — no fallback for a legacy node")
        n = node_doc(lslug, lid)
        assert n.get("frozen") and n["frozen"].get("limit"), "it freezes like before"
        eq(n["codex_route_last"]["reason"], "legacy-tier", "the receipt says why")
    check("§11 an existing gpt-reserve node runs reserve-only and freezes on "
          "its wall (compatibility, no fold-in)", t_legacy_runs)

    def t_hire_door():
        from orgtree import api
        org = store.create_org("zz lunaroute door")
        org.d["max_top_grant"] = 200
        try:
            api.provider_hire_gate(org, "gpt-reserve")
        except LedgerError as e:
            assert "luna" in str(e) and "no longer hireable" in str(e), str(e)
        else:
            raise AssertionError("a NEW gpt-reserve hire must be refused")
        # the plain-rehire door (stored tier, user_choice_only) still opens
        api.provider_hire_gate(org, "gpt-reserve", user_choice_only=True)
        # ⚠ luna stays hireable with BOTH pools spent — hiring prepares an
        # agent; the turn answers for capacity (ruling 2026-09-02)
        os.environ["FAKECODEX_BOARD"] = "both-exhausted"
        codex_limits.invalidate()
        api.provider_hire_gate(org, "luna")
        codex_limits.invalidate()
        # the offered tier rows no longer carry the legacy token
        offered = [t["tier"] for t in providers.codex_tiers(set())]
        assert "gpt-reserve" not in offered and "luna" in offered, offered
        assert "gpt-reserve" in providers.CODEX_TIERS, "…but the AXIS still knows it"
        assert providers.is_known_tier("gpt-reserve"), "and it is a known tier"
    check("§11 the hire door refuses a NEW gpt-reserve (points at luna), keeps "
          "the plain-rehire door open, keeps an exhausted luna hireable, and "
          "the offer list drops the token while the axis keeps it", t_hire_door)

    # ───────────────────────────────────────────────────────────────────────
    print("§12 the preference: default, persistence, restart")

    def t_pref():
        app_prefer(False)
        org = store.create_org("zz lunaroute pref")
        r = org.hire(USER, None, "luna", 2, "p", add_dirs=[],
                     tools={"bash": True, "web": False, "edit": True,
                            "subagents": False, "mcp": []},
                     org_visibility="team", charter="c")
        pid = r["node"]
        eq(org.prefer_reserve_for(pid), False,
           "absent inherits the app-wide OFF default")
        ambient = tempfile.mkdtemp(prefix="lunaroute-ambient-")
        with open(os.path.join(ambient, "defaults.json"), "w", encoding="utf-8") as f:
            json.dump({"prefer_reserve": True}, f)
        bound = os.environ["ORGTREE_DATA"]
        os.environ["ORGTREE_DATA"] = ambient
        try:
            eq(org.prefer_reserve_for(pid), False,
               "ambient ORGTREE_DATA cannot redirect the bound store root")
        finally:
            os.environ["ORGTREE_DATA"] = bound
        inherited = org.hire(USER, None, "luna", 0, "inherited", add_dirs=[],
                             tools={"bash": True, "web": False, "edit": True,
                                    "subagents": False, "mcp": []},
                             org_visibility="team", charter="c")["node"]
        org.set_scope(USER, inherited, effort="low")
        assert "prefer_reserve" not in org.node(inherited)["scope"], \
            "an unrelated save must not materialize an inherited preference"
        eq(org.prefer_reserve_for(inherited), False,
           "missing preference remains app-defaulted after unrelated save")
        app_prefer(True)
        eq(org.prefer_reserve_for(inherited), True,
           "missing preference follows a later app-default flip")
        app_prefer(False)
        org.set_scope(USER, pid, prefer_reserve=True)
        eq(org.prefer_reserve_for(pid), True, "explicit per-agent ON wins")
        org.set_scope(USER, pid, prefer_reserve=False)
        eq(org.prefer_reserve_for(pid), False, "off after set_scope")
        store.save_org(org)
        again = store.load_org(org.d["slug"])
        eq(again.prefer_reserve_for(pid), False, "…and after a reload (restart)")
        eq(again.node(pid)["scope"].get("prefer_reserve"), False, "stored explicitly")
        again.set_scope(USER, pid, prefer_reserve=True)
        eq(again.prefer_reserve_for(pid), True, "back on")
        # untouched by an unrelated retool
        again.set_scope(USER, pid, effort="low")
        eq(again.prefer_reserve_for(pid), True, "an unrelated retool leaves it")
        # a self-retool may not set it (a superior's dial, like effort)
        try:
            again.set_scope(pid, pid, prefer_reserve=False)
        except LedgerError:
            pass
        else:
            raise AssertionError("self-retool must not set prefer_reserve")
        app_prefer(True)
    check("§12 prefer_reserve: absent inherits app default, explicit value "
          "persists across reload and remains superior-only", t_pref)

    def t_pref_api():
        from fastapi.testclient import TestClient
        from orgtree import api
        client = TestClient(api.app)
        app_prefer(False)
        saved = client.post("/api/defaults", json={"prefer_reserve": False})
        assert saved.status_code == 200, saved.text
        eq(saved.json()["prefer_reserve"], False,
           "app-wide default round-trips through POST /api/defaults")
        fetched = client.get("/api/defaults")
        assert fetched.status_code == 200, fetched.text
        eq(fetched.json()["prefer_reserve"], False,
           "app-wide default round-trips through GET /api/defaults")
        created = api.orgs_create(api.OrgCreate(
            name="zz lunaroute app default", net_autoconnect=False))
        created_doc = store.load_org(created["slug"]).d
        assert "prefer_reserve" not in created_doc, \
            "the app-wide default must not become an org-wide stored default"
        org = store.create_org("zz lunaroute api")
        org.d["max_top_grant"] = 200
        store.save_org(org)
        s = org.d["slug"]
        scenario("tool", "reserve-ok")
        # hire with the box off, applied WITH the hire
        r = client.post(f"/api/orgs/{s}/ops", json={
            "op": "hire", "actor": USER, "parent": None, "tier": "luna",
            "grant": 0, "name": "boxoff", "add_dirs": [],
            "tools": {"bash": True, "web": False, "edit": True,
                      "subagents": False, "mcp": []},
            "org_visibility": "team", "charter": "c", "prefer_reserve": False})
        assert r.status_code == 200, r.text
        eq(store.load_org(s).prefer_reserve_for("boxoff"), False, "hired with the box off")
        # omitted = default
        r = client.post(f"/api/orgs/{s}/ops", json={
            "op": "hire", "actor": USER, "parent": None, "tier": "luna",
            "grant": 0, "name": "boxdefault", "add_dirs": [],
            "tools": {"bash": True, "web": False, "edit": True,
                      "subagents": False, "mcp": []},
            "org_visibility": "team", "charter": "c"})
        assert r.status_code == 200, r.text
        eq(store.load_org(s).prefer_reserve_for("boxdefault"), False,
           "omitted agent inherits the app-wide OFF default")
        r = client.post(f"/api/orgs/{s}/nodes/boxdefault/scope",
                        json={"prefer_reserve": True})
        assert r.status_code == 200, r.text
        r = client.post(f"/api/orgs/{s}/nodes/boxdefault/scope",
                        json={"clear_prefer_reserve": True})
        assert r.status_code == 200, r.text
        assert "prefer_reserve" not in store.load_org(s).node("boxdefault")["scope"], \
            "clear action removes the individual override"
        eq(store.load_org(s).prefer_reserve_for("boxdefault"), False,
           "cleared preference returns to the app default")
        # editable afterwards through the gear's endpoint
        r = client.post(f"/api/orgs/{s}/nodes/boxdefault/scope",
                        json={"prefer_reserve": False})
        assert r.status_code == 200, r.text
        eq(store.load_org(s).prefer_reserve_for("boxdefault"), False, "edited later")
        # the tree payload exposes the preference on scope and the route
        # token on the node — null before any turn
        tree_payload = client.get(f"/api/orgs/{s}").json()
        eq(tree_payload["prefer_reserve_default"], False,
           "tree exposes the live app-wide OFF default")
        nodes = tree_nodes(tree_payload)
        eq(nodes["boxdefault"]["scope"].get("prefer_reserve"), False, "scope on the wire")
        eq(nodes["boxdefault"].get("codex_route"), None, "no route before a turn")
        # a NEW gpt-reserve hire through the API is refused, pointing at luna
        r = client.post(f"/api/orgs/{s}/ops", json={
            "op": "hire", "actor": USER, "parent": None, "tier": "gpt-reserve",
            "grant": 0, "name": "nope", "add_dirs": [],
            "tools": {"bash": True, "web": False, "edit": True,
                      "subagents": False, "mcp": []},
            "org_visibility": "team", "charter": "c"})
        eq(r.status_code, 422, "refused")
        assert "luna" in r.text, r.text
        # …and so is a switch onto it
        r = client.post(f"/api/orgs/{s}/ops", json={
            "op": "switch_model", "actor": USER, "node": "boxoff", "tier": "gpt-reserve"})
        eq(r.status_code, 422, "switch refused")
        # after a turn the tree carries the route token, labelled LAST
        run_turn(s, "boxdefault", "hello")
        nodes = tree_nodes(client.get(f"/api/orgs/{s}").json())
        cr = nodes["boxdefault"]["codex_route"]
        assert isinstance(cr, dict), cr
        # the box is OFF on this node and the board has plan room: direct
        # by preference — nothing to disclose
        eq((cr["route"], cr["prefer"], cr["live"], cr["label"]),
           ("direct", PLAN, False, None), "direct by preference, not live, no label")
        # the box-on sibling ran nothing yet; run it on the same board
        run_turn(s, "boxoff", "hello")
        nodes = tree_nodes(client.get(f"/api/orgs/{s}").json())
        eq(nodes["boxoff"]["codex_route"]["route"], "direct", "boxoff: direct by preference")
        # flip boxdefault's box back on and run: reserve, labelled last
        client.post(f"/api/orgs/{s}/nodes/boxdefault/scope", json={"prefer_reserve": True})
        run_turn(s, "boxdefault", "again")
        nodes = tree_nodes(client.get(f"/api/orgs/{s}").json())
        cr = nodes["boxdefault"]["codex_route"]
        eq((cr["route"], cr["live"], cr["label"]), ("reserve", False, "last: reserve"),
           "reserve, not live, labelled last")
        # the provider's echo rides the payload: on a RESUMED thread it is
        # the thread/resume response's `model`, which the fake echoes from
        # the resume's own `model` field (the route's) — a receipt that the
        # field was accepted, never proof of which weights answered
        eq(cr["reported_model"], R, "reported model rides the payload")
        # the providers document: the reserve object and the deprecated aliases
        p = client.get("/api/providers").json()
        openai = next(x for x in p["providers"] if x["id"] == "openai")
        res = openai.get("reserve")
        assert isinstance(res, dict), openai.keys()
        eq((res["granted"], res["exhausted"]), (True, False), "granted with room")
        eq(res["route"]["route"], "reserve", "a luna's next turn would be reserve")
        assert "reserve_hire_enabled" in openai and "reserve_reason" in openai, \
            "the deprecated aliases are still served"
        assert "gpt-reserve" not in [t["tier"] for t in openai["tiers"]], openai["tiers"]
        app_prefer(True)
    check("§12 through the API: app default round-trip, omitted inherits it, "
          "hire with the box, edit later, "
          "tree carries scope + route token labelled last, gpt-reserve hire/"
          "switch refused, providers doc has the reserve object + aliases",
          t_pref_api)

    def t_live_label():
        # the LIVE half of the token cannot be observed from outside a turn
        # here without a real server; the label rule is pinned in §5 and the
        # state flip is pinned by the not-live receipts above. This check
        # pins the api's composition rule: live requires BOTH the record's
        # flag and a busy node.
        from orgtree import api
        import inspect
        src = inspect.getsource(api.org_tree)
        assert 'bool(_rt.get("live")) and bool(st.get("busy"))' in src, \
            "the tree's live rule must AND the record flag with busy"
    check("§12 the tree's live rule ANDs the record's flag with the node's "
          "busy state (source pin)", t_live_label)

    # ───────────────────────────────────────────────────────────────────────
    print("§13 the board is bound to the account it was read for")

    def t_board_account_binding():
        scenario("tool", "reserve-ok")
        codex_limits.fetch(force=True)
        b1 = codex_limits.snapshot()
        acct1 = codex_limits.account_namespace()
        eq(b1["account"], acct1, "the board is stamped with the account it was read for")
        assert b1["complete"], b1
        # per-bucket observation times ride the normalized windows
        assert all(isinstance(w.get("observed_at"), float) for w in b1["limits"]), b1["limits"]
        # a sparse notification about the PLAN bucket refreshes only it
        time.sleep(0.01)
        codex_limits.observe({"limitId": "codex", "limitName": None,
                              "primary": {"usedPercent": 13}})
        b2 = codex_limits.snapshot()
        res_seen = [w["observed_at"] for w in b2["limits"] if w.get("model") == R]
        plan_seen = [w["observed_at"] for w in b2["limits"] if w.get("model") is None]
        assert res_seen and plan_seen, b2["limits"]
        assert max(plan_seen) > max(res_seen), "only the named bucket is re-observed"
        assert max(res_seen) == max(w["observed_at"] for w in b1["limits"] if w.get("model") == R)
        # the login changes under the cache: `codex login` as someone else
        with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as f:
            f.write('{"tokens": {"account_id": "acct-someone-else"}}')
        try:
            acct2 = codex_limits.account_namespace()
            assert acct2 != acct1, "the namespace must move with the account"
            # a fetch within the TTL would have served the cache — it must
            # notice the account moved and READ AGAIN for the new one
            codex_limits.fetch()
            b3 = codex_limits.snapshot()
            eq(b3["account"], acct2, "the re-read board is the new account's")
            assert b3["complete"], "…and it is a complete read, not a patched cache"
            # the resolver, asked for the OLD account, treats the new board
            # as no evidence
            r = codex_route.resolve("luna", login_kind="chatgpt", board=b3,
                                    marks=None, account=acct1)
            eq(r["reason"], "board-unknown", "another account's board is no evidence")
            # …and for the new account it counts (reserve-ok board → granted)
            r2 = codex_route.resolve("luna", login_kind="chatgpt", board=b3,
                                     marks=None, account=acct2)
            eq(r2["reason"], "granted", "the board counts for the account it names")
        finally:
            with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as f:
                f.write('{"tokens": {"account_id": "acct-luna-test"}}')
            codex_limits.invalidate()
    check("§13 the cached board is stamped with its account, re-read when the "
          "login moves, observed per bucket, and ignored for another account",
          t_board_account_binding)

    # ───────────────────────────────────────────────────────────────────────
    print("§14 evidence ages PER WINDOW, not per board (parent review, reproduced)")
    from unittest.mock import patch

    T = 1_000_000.0
    A_ACCT, B_ACCT = "codex-chatgpt:acct-A", "codex-chatgpt:acct-B"

    def win(pct, resets_in=10_000):
        return {"usedPercent": pct, "windowDurationMins": 10080,
                "resetsAt": T + resets_in}

    def at(t, acct=A_ACCT):
        """Run `codex_limits` with its clock at `t` and its login as `acct`."""
        return (patch.object(codex_limits, "account_namespace", return_value=acct),
                patch.object(codex_limits.time, "time", return_value=t))

    def observe_at(t, snap, pool, acct=A_ACCT, origin=None):
        p1, p2 = at(t, acct)
        with p1, p2:
            return codex_limits.observe(snap, pool_hint=pool, account=origin)

    def res_at(t, acct=A_ACCT, prefer=True):
        return codex_route.resolve("luna", login_kind="chatgpt",
                                   board=codex_limits.snapshot(now=t), marks={},
                                   account=acct, now=t, prefer_reserve=prefer)

    def t_per_pool_age():
        # THE PARENT'S PROBE, run against the real cache and resolver:
        # reserve exhausted at T, the plan patched at T+1000 — the board is
        # "fresh" by its last touch while reserve's numbers are 1000 s old
        codex_limits.invalidate()
        observe_at(T, {"limitId": "reserve-id", "limitName": R, "secondary": win(100)}, RES)
        observe_at(T, {"limitId": "codex", "secondary": win(10)}, PLAN)
        eq(res_at(T)["route"], "direct", "fresh exhaustion control: direct")
        # still inside the evidence age: the exhaustion binds
        observe_at(T + 100, {"limitId": "codex", "secondary": win(10)}, PLAN)
        eq(res_at(T + 100)["route"], "direct", "at T+100 reserve's reading still binds")
        # past it: a plan-only notification must NOT keep reserve excluded
        observe_at(T + 1000, {"limitId": "codex", "secondary": win(11)}, PLAN)
        b = codex_limits.snapshot(now=T + 1000)
        eq(b["stale"], False, "the board as a whole was touched a moment ago")
        lim = b["limits"]
        eq(codex_route.pool_capacity(lim, RES, now=T + 1000)["state"], "stale",
           "reserve's only window is 1000 s old → stale, not exhausted")
        eq(codex_route.pool_capacity(lim, PLAN, now=T + 1000)["state"], "usable", "plan fresh")
        r = res_at(T + 1000)
        eq((r["route"], r["reason"]), ("reserve", "board-stale"),
           "stale reserve evidence decides nothing: reserve is re-probed")
        # the same board WITHOUT aging (the historical reading) still says
        # exhausted — so the aging is what changed the answer
        eq(codex_route.pool_capacity(lim, RES)["state"], "exhausted", "un-aged control")
        # a fresh reserve reading restores the exclusion
        observe_at(T + 1001, {"limitId": "reserve-id", "limitName": R,
                              "secondary": win(100)}, RES)
        eq(res_at(T + 1001)["route"], "direct", "fresh exhaustion again → direct")
        codex_limits.invalidate()
    check("§14 ⚠ a plan-only notification cannot keep 1000-second-old reserve "
          "exhaustion binding: per-pool age, reserve re-probed", t_per_pool_age)

    def t_slot_age():
        # a notification carrying ONLY `primary` retains the bucket's old
        # `secondary` (`_merge_sparse`) — and must retain its AGE too
        codex_limits.invalidate()
        observe_at(T, {"limitId": "reserve-id", "limitName": R,
                       "primary": win(20, 3600), "secondary": win(100)}, RES)
        observe_at(T + 1000, {"limitId": "reserve-id", "limitName": R,
                              "primary": win(21, 3600)}, RES)
        b = codex_limits.snapshot(now=T + 1000)
        seen = {round(w["percent"]): w["observed_at"]
                for w in b["limits"] if w.get("model") == R}
        eq(seen.get(21), T + 1000, "the carried primary is observed now")
        eq(seen.get(100), T, "the RETAINED secondary keeps its original observation time")
        cap = codex_route.pool_capacity(b["limits"], RES, now=T + 1000)
        eq((cap["state"], cap["stale_windows"]), ("usable", 1),
           "the stale secondary is dropped; the fresh primary has room")
        eq(res_at(T + 1000)["route"], "reserve", "…so reserve is tried")
        # control: with BOTH slots carried, the 100% secondary binds
        observe_at(T + 1000, {"limitId": "reserve-id", "limitName": R,
                              "primary": win(21, 3600), "secondary": win(100)}, RES)
        eq(res_at(T + 1000)["route"], "direct", "both slots fresh → exhausted binds")
        codex_limits.invalidate()
    check("§14 a primary-only notification leaves the retained secondary's "
          "observation age alone (per slot, not per bucket)", t_slot_age)

    def t_full_read_slots():
        # a full read stamps every slot it carries — the existing §13 shape
        scenario("tool", "reserve-ok")
        codex_limits.fetch(force=True)
        b = codex_limits.snapshot()
        assert all(isinstance(w.get("observed_at"), float) for w in b["limits"]), b["limits"]
        assert len({w["observed_at"] for w in b["limits"]}) == 1, "one read, one stamp"
        codex_limits.invalidate()
    check("§14 a full read stamps every window it carries at once", t_full_read_slots)

    # ───────────────────────────────────────────────────────────────────────
    print("§15 a notification carries the account it ran as (parent review, reproduced)")

    def t_foreign_refused():
        codex_limits.invalidate()
        observe_at(T, {"limitId": "reserve-id", "limitName": R, "secondary": win(100)}, RES)
        observe_at(T, {"limitId": "codex", "secondary": win(10)}, PLAN)
        eq(codex_limits.snapshot()["account"], A_ACCT, "board stamped A")
        eq(res_at(T)["route"], "direct", "A: reserve exhausted → direct")
        # THE PARENT'S PROBE: the login moves to B and B's turn reports a
        # positive reserve window while the board is still A's
        ok = observe_at(T + 1, {"limitId": "reserve-id", "limitName": R,
                                "secondary": win(5)}, RES, acct=B_ACCT, origin=B_ACCT)
        eq(ok, False, "a B notification is REFUSED by an A board")
        eq(codex_limits.refusals()["foreign"], 1, "…and counted")
        eq(codex_limits.snapshot()["account"], A_ACCT, "the board is still A's")
        eq(res_at(T + 1)["route"], "direct", "A still routes direct (not poisoned)")
        # the origin defaults to the namespace AS OF NOW when not captured:
        # the same B notification without a captured origin is still B's
        ok2 = observe_at(T + 1, {"limitId": "reserve-id", "limitName": R,
                                 "secondary": win(5)}, RES, acct=B_ACCT)
        eq(ok2, False, "uncaptured origin falls back to the current login (B) → refused")
        # control: an A notification merges
        ok3 = observe_at(T + 2, {"limitId": "reserve-id", "limitName": R,
                                 "secondary": win(5)}, RES, origin=A_ACCT)
        eq(ok3, True, "A's own positive reading merges")
        eq(res_at(T + 2)["route"], "reserve", "…and moves A's route")
        codex_limits.invalidate()
    check("§15 ⚠ a B-account notification cannot poison a board stamped A; "
          "A's own merges", t_foreign_refused)

    def t_late_a_after_b_fetch():
        # the other order: B's FULL read replaced the board, then an A turn
        # that outlived the login change delivers its last notification
        scenario("tool", "reserve-ok")
        codex_limits.invalidate()
        with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as f:
            f.write('{"tokens": {"account_id": "acct-B-login"}}')
        try:
            codex_limits.fetch(force=True)
            b_acct = codex_limits.account_namespace()
            b = codex_limits.snapshot()
            eq(b["account"], b_acct, "B's complete board")
            assert b["complete"], b
            plan_before = sorted(w["percent"] for w in b["limits"] if w.get("model") is None)
            late = codex_limits.observe(
                {"limitId": "codex", "limitName": None,
                 "primary": {"usedPercent": 100, "resetsAt": time.time() + 4000}},
                pool_hint=RES, account="codex-chatgpt:the-old-login")
            eq(late, False, "an old A result after B's full fetch is refused")
            after = codex_limits.snapshot()
            eq(sorted(w["percent"] for w in after["limits"] if w.get("model") is None),
               plan_before, "B's plan bucket untouched")
            eq(codex_route.pool_capacity(after["limits"], RES, now=time.time())["state"],
               "usable", "B's reserve bucket untouched (reserve-ok board)")
        finally:
            with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as f:
                f.write('{"tokens": {"account_id": "acct-luna-test"}}')
            codex_limits.invalidate()
    check("§15 an A turn's late notification after B's full read is refused",
          t_late_a_after_b_fetch)

    def t_fetch_race():
        # the login moves WHILE the full read is in flight: the answer is
        # one account's numbers and the stamp would be the other's — served
        # once, cached never
        scenario("tool", "reserve-ok")
        codex_limits.invalidate()
        with patch.object(codex_limits, "account_namespace",
                          side_effect=[A_ACCT, A_ACCT, B_ACCT]):
            got = codex_limits.fetch(force=True)
        assert got.get("available"), got
        assert "changed during" in str(got.get("error") or ""), got
        eq(codex_limits.snapshot()["available"], False, "nothing cached")
        eq(codex_limits.refusals()["race"], 1, "counted as a race")
        # control: a stable login caches
        codex_limits.invalidate()
        with patch.object(codex_limits, "account_namespace",
                          side_effect=[A_ACCT, A_ACCT, A_ACCT]):
            codex_limits.fetch(force=True)
        b = codex_limits.snapshot()
        eq((b["available"], b["account"], b["complete"]), (True, A_ACCT, True),
           "stable login: cached, stamped, complete")
        codex_limits.invalidate()
    check("§15 a full read whose login moved mid-read is served uncached and "
          "stamps nothing", t_fetch_race)

    def t_handoff_carries_account():
        # THE SUPERVISOR HANDOFF: the fold after a real (fake-wire) turn
        # carries the account captured on the route at preflight, not the
        # namespace as of delivery
        seen: list[dict] = []
        real_observe = codex_limits.observe

        def rec_observe(snap, pool_hint=None, account=None):
            seen.append({"pool_hint": pool_hint, "account": account})
            return real_observe(snap, pool_hint=pool_hint, account=account)
        scenario("tool", "reserve-ok")
        s, n_ = mkorg("handoff")
        with patch.object(codex_limits, "observe", rec_observe):
            run_turn(s, n_, "hi")
        assert seen, "the turn folded nothing"
        acct = node_doc(s, n_)["codex_route_last"]["account"]
        assert acct and acct == supervisor._codex_account_namespace(), acct
        eq({x["account"] for x in seen}, {acct}, "every fold names the route's account")
        eq({x["pool_hint"] for x in seen}, {RES}, "…and the served pool")
        # the board the turn left is stamped with that account; a B-login
        # notification landing on it now is refused, the route unmoved
        eq(codex_limits.snapshot()["account"], acct, "board stamped by the turn")
        before = supervisor._codex_resolve_route(store.load_org(s), n_, "luna")
        with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as f:
            f.write('{"tokens": {"account_id": "acct-B-login"}}')
        try:
            b_acct = codex_limits.account_namespace()
            assert b_acct != acct
            ok = codex_limits.observe(
                {"limitId": "codex", "limitName": None,
                 "primary": {"usedPercent": 100, "resetsAt": time.time() + 4000}},
                pool_hint=RES, account=b_acct)
            eq(ok, False, "B's wall is refused by the A board the turn left")
            eq(codex_limits.snapshot()["account"], acct, "still A's board")
            eq(codex_route.pool_capacity(codex_limits.snapshot()["limits"], RES,
                                         now=time.time())["state"], "usable",
               "A's reserve still has room on A's board")
        finally:
            with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as f:
                f.write('{"tokens": {"account_id": "acct-luna-test"}}')
        eq(before["route"], "reserve", "A's preflight was reserve")
        codex_limits.invalidate()
    check("§15 the supervisor folds a turn's notifications under the account "
          "captured on its route; the board it leaves refuses another login's",
          t_handoff_carries_account)

    # ───────────────────────────────────────────────────────────────────────
    print("§16 a known reroute changes the token, the attribution and the retry")

    def t_label_reroute():
        r = resolve(board([W(R, 8)]))                          # sent reserve
        rr_direct = {"fromModel": R, "toModel": D, "reason": "x"}
        rr_unknown = {"fromModel": R, "toModel": "gpt-9-mystery", "reason": "x"}
        eq(codex_route.route_label(r, live=True, rerouted=rr_direct),
           "direct · rerouted off reserve", "reserve sent, direct served: says so")
        eq(codex_route.route_label(r, live=False, rerouted=rr_direct),
           "last: direct · rerouted off reserve", "…and last")
        eq(codex_route.route_label(r, live=False, rerouted=rr_unknown),
           "last: rerouted · pool unknown", "unknown destination: not inferred")
        eq(codex_route.route_label(r, live=True, rerouted=None), "reserve", "no reroute")
        d = resolve(board([W(R, 100, ISO(NOW + 5))]))          # sent direct
        eq(codex_route.route_label(d, live=True, rerouted={"fromModel": D, "toModel": R}),
           "reserve · rerouted", "direct sent, reserve served: reserve, marked rerouted")
        # the record shape the stamp writes carries `rerouted` itself
        rec = {**r, "rerouted": rr_direct}
        eq(codex_route.route_label(rec, live=False), "last: direct · rerouted off reserve",
           "read off the record")
    check("§16 route_label follows a KNOWN reroute and refuses to name an "
          "unknown one", t_label_reroute)

    def t_classify_reroute():
        usage = {"message": "m", "codexErrorInfo": "usageLimitExceeded"}
        unnamed = {"codex": {"limitId": "codex", "limitName": None,
                             "primary": {"usedPercent": 100, "resetsAt": NOW + 2000}}}
        # sent reserve, served reserve: the classic re-drive
        c = codex_route.classify_failure(
            status="failed", error=usage, snapshots=unnamed, items_seen=0,
            token_usage=None, agent_text="", pool=RES, board=None, now=NOW)
        eq((c["rejected"], c["redrive"], c["attributed"], c["pool_state"], c["reset_ts"]),
           (True, True, RES, "exhausted", NOW + 2000), "same pool: re-drive, reserve's wall")
        # sent reserve, served PLAN (known reroute): the wall is the plan's,
        # nothing is booked against reserve, and there is no re-drive —
        # "the other pool" is the one that just rejected
        c2 = codex_route.classify_failure(
            status="failed", error=usage, snapshots=unnamed, items_seen=0,
            token_usage=None, agent_text="", pool=RES, board=None, served=PLAN, now=NOW)
        eq((c2["rejected"], c2["redrive"], c2["attributed"], c2["pool_state"], c2["reset_ts"]),
           (True, False, PLAN, "exhausted", NOW + 2000), "rerouted: plan's wall, no re-drive")
        assert "rerouted" in c2["why"], c2["why"]
        # unknown destination: attributed to nothing, no re-drive
        c3 = codex_route.classify_failure(
            status="failed", error=usage, snapshots=unnamed, items_seen=0,
            token_usage=None, agent_text="", pool=RES, board=None, served=None, now=NOW)
        eq((c3["rejected"], c3["redrive"], c3["attributed"], c3["pool_state"], c3["reset_ts"]),
           (True, False, None, "unattributed", None), "unknown: nothing attributed")
    check("§16 classify_failure attributes a rejection to the pool that SERVED "
          "and re-drives only when that is the pool sent to", t_classify_reroute)

    stamps: list[dict] = []
    real_stamp = supervisor._codex_route_stamp

    def rec_stamp(st_, route_, **kw):
        rec = real_stamp(st_, route_, **kw)
        stamps.append(dict(rec))
        return rec

    def t_reroute_completes():
        probe_r = scenario("reroute_direct", "reserve-ok")
        s, n_ = mkorg("reroute-ok")
        stamps.clear()
        folds: list[dict] = []
        real_observe = codex_limits.observe

        def rec_observe(snap, pool_hint=None, account=None):
            folds.append({"pool_hint": pool_hint})
            return real_observe(snap, pool_hint=pool_hint, account=account)
        with patch.object(supervisor, "_codex_route_stamp", rec_stamp), \
                patch.object(codex_limits, "observe", rec_observe):
            run_turn(s, n_, "go")
        eq(turn_models(probe_r), [R], "ONE attempt, sent as reserve")
        eq(supervisor.state(s, n_)["turns_run"], 1, "completed")
        # the LIVE stamp moved the moment the server said rerouted — before
        # the turn ended — and its label already tells the truth
        live_rr = [x for x in stamps if x["live"] and x.get("rerouted")]
        assert live_rr, [(x["live"], x.get("rerouted")) for x in stamps]
        eq(live_rr[0]["served_pool"], PLAN, "live stamp: served pool is the plan")
        eq(codex_route.route_label(live_rr[0], live=True), "direct · rerouted off reserve",
           "row 2 WHILE the turn runs")
        eq(stamps[0].get("rerouted"), None, "the preflight stamp had no reroute yet")
        # the receipt: selected reserve, served plan, both kept apart
        last = node_doc(s, n_)["codex_route_last"]
        eq((last["route"], last["pool"], last["served_pool"], last["rerouted"]["toModel"]),
           ("reserve", RES, PLAN, D), "selected reserve, served plan")
        eq(codex_route.route_label(last, live=False), "last: direct · rerouted off reserve",
           "the not-live token says where it RAN")
        # the notification was folded into the PLAN bucket
        eq({f["pool_hint"] for f in folds}, {PLAN}, "folded as the plan's")
        # …and the tree serves the same words
        from fastapi.testclient import TestClient
        from orgtree import api
        nodes = tree_nodes(TestClient(api.app).get(f"/api/orgs/{s}").json())
        cr = nodes[n_]["codex_route"]
        eq((cr["label"], cr["served_pool"], cr["rerouted"]["toModel"], cr["route"]),
           ("last: direct · rerouted off reserve", PLAN, D, "reserve"), "tree payload")
    check("§16 a completed turn the server rerouted reserve→direct: live stamp "
          "mid-turn, receipt keeps selected apart from served, token says direct",
          t_reroute_completes)

    def t_reroute_then_wall():
        probe_w = scenario("reroute_then_wall", "reserve-ok")
        s, n_ = mkorg("reroute-wall")
        t0 = time.time()
        stamps.clear()
        with patch.object(supervisor, "_codex_route_stamp", rec_stamp):
            run_turn(s, n_, "go")
        eq(turn_models(probe_w), [R], "⚠ ONE attempt: no re-drive onto the pool that rejected")
        st = supervisor.state(s, n_)
        eq(st["turns_run"], 0, "not completed")
        n = node_doc(s, n_)
        routes = n.get("codex_routes") or {}
        assert RES not in routes, f"reserve must NOT be marked for a wall the plan raised: {routes}"
        assert not any("route switched" in r["text"] for r in err_rows(s, n_)), err_rows(s, n_)
        fz = n.get("frozen")
        assert isinstance(fz, dict) and fz.get("limit"), "the ordinary limit freeze"
        # timed from the pool that SERVED (the plan's reset), not reserve's
        assert abs(float(fz["until_ts"]) - (t0 + fakecodex.PLAN_RESET_IN)) < 120, fz
        last = n["codex_route_last"]
        eq((last["route"], last["served_pool"], last["outcome"]),
           ("reserve", PLAN, codex_route.KIND_USAGE_LIMIT), "receipt")
        eq(codex_route.route_label(last, live=False), "last: direct · rerouted off reserve",
           "token")
        assert "rerouted" in (st["last_error"] or ""), st["last_error"]
    check("§16 a wall AFTER a known reroute: no retry, no reserve mark, freeze "
          "timed from the serving pool", t_reroute_then_wall)

    def t_reroute_unknown():
        probe_u = scenario("reroute_unknown_then_wall", "reserve-ok")
        codex_limits.fetch(force=True)
        before = codex_limits.snapshot()
        plan_before = sorted(w["percent"] for w in before["limits"] if w.get("model") is None)
        s, n_ = mkorg("reroute-unknown")
        folds: list[dict] = []
        real_observe = codex_limits.observe

        def rec_observe(snap, pool_hint=None, account=None):
            folds.append({"pool_hint": pool_hint})
            return real_observe(snap, pool_hint=pool_hint, account=account)
        with patch.object(codex_limits, "observe", rec_observe):
            run_turn(s, n_, "go")
        eq(turn_models(probe_u), [R], "one attempt")
        eq(folds, [], "⚠ nothing folded: the destination names no pool")
        after = codex_limits.snapshot()
        eq(sorted(w["percent"] for w in after["limits"] if w.get("model") is None),
           plan_before, "the plan bucket did not take the wall by default")
        n = node_doc(s, n_)
        assert "codex_routes" not in n, n.get("codex_routes")
        last = n["codex_route_last"]
        eq((last["served_pool"], last["rerouted"]["toModel"]), (None, "gpt-9-mystery"), "receipt")
        eq(codex_route.route_label(last, live=False), "last: rerouted · pool unknown", "token")
        codex_limits.invalidate()
    check("§16 a wall after a reroute onto an UNKNOWN model: nothing folded, "
          "nothing marked, token says unknown", t_reroute_unknown)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"FAILED: {label}\n{tb}")
        print(f"{PASS} passed, {len(FAIL)} FAILED")
        return 1
    print(f"{PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
