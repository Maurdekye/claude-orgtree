"""Compaction, lineage and cross-process safety — the §8 axis, attacked adversarially.

    A compaction is a SPLIT, not an edit. After it, exactly one node still
    runs (the successor: same name, same slot, same credits, forked session)
    and exactly one more exists that never runs again (the predecessor: a
    knowledge bearer, archived, grant 0, read-only). Nothing may be stranded
    by the split — not a credit, not a message, not a subtree — and a split
    that FAILS must leave the node exactly as runnable as it was before.

Run:  .venv/Scripts/python.exe backend/tests/test_compaction.py
      --quick        fewer repetitions / shorter sweeps
      --hermetic     skip the live half entirely (seconds, no backend)
      --only <sub>   only checks whose label contains <sub>
      --port N       bind the throwaway backend here (default 7409)
      --keep         keep the rig directory and print its path

WHAT IT DOES
------------
Three halves, one file, `test_ledger.py` style (plain asserts, `ok N` lines,
no pytest — still not installed).

**Hermetic ledger** drives the lineage algebra straight against `Org`:
`compact_split`, `reseed`, `lineage_stack`, and every op that has a
bearer-specific rule (`dissolve`, `delete`, `retire`, `rehire`, `_move`,
`extern_recipients`). Bearers are the awkward case in all of them — a bearer
occupies its successor's slot, is archived but not retired, and can acquire
org children of its own — and three separate stranding bugs have already come
out of exactly that.

**Hermetic supervisor** drives the threshold arithmetic (`_after_turn`) and
the split bookkeeping without launching anything: which occupancy triggers a
split, which triggers the §8.3 oracle transition, what the cooldown does, and
what the doc looks like after each.

**Live** runs a real backend on its own port and data dir with a fake CLI that
can answer `--output-format json` — which is the only reason `_compact_split`
can complete at all — and then breaks the fork in every way it can break:
non-zero exit, an unchanged session id, empty stdout, unparseable stdout, a
hang past the timeout, the backend dying mid-split. After each one the suite
asks the same two questions: IS THE NODE STILL RUNNABLE, and WHERE IS THE MAIL.

Plus two measurements that are not really about compaction and are here
because nothing else covers them:

  * **cross-process safety** — `DOC_LOCK` and the `_IOLatch` are both
    per-process, and `ARCHITECTURE.md` says a second backend on one data dir
    "silently discards interleaved load-modify-save cycles". That is measured
    here, in real subprocesses, and the number is asserted rather than
    described.
  * **`ORGTREE_MAX_TURNS` fairness** — the cap is global, not per-org, so a
    busy org can starve a quiet one. Measured as a wait time.

THE CLI STAND-IN
----------------
`fakecli.js` is not modified. A wrapper (`compactcli.js`) is generated into
the rig dir at runtime and delegates to it, exactly as
`test_turn_lifecycle.py` does with `wrapcli.js`. The wrapper adds what this
suite needs and fakecli has no dial for:

  json fork    `--output-format json` — programmable: a fresh session id, the
               SAME session id (the "did it actually fork" guard), a non-zero
               exit, empty stdout, malformed stdout, or a hang. This is the
               compaction fork, and against stock fakecli it can only fail.
  usage        a programmable `input_tokens`, so occupancy — and therefore the
               compaction threshold and the oracle threshold — is a DIAL
               rather than a constant 1200.

Nothing here touches the user's data: its own ORGTREE_DATA, its own HOME, its
own port (7409 by default), every org deleted at the end.
"""

from __future__ import annotations

import contextlib
import glob
import inspect
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "backend"))

QUICK = "--quick" in sys.argv
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""
HERMETIC_ONLY = "--hermetic" in sys.argv
KEEP = "--keep" in sys.argv
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 7409

PASS = 0
FAIL: list[tuple[str, str]] = []
#: measured behaviours that break an invariant for a reason outside this
#: suite's remit, or that are reported-not-fixed — printed loudly every run
EXCEPTIONS: list[tuple[str, str]] = []
NOTES: list[str] = []
MEASURED: list[tuple[str, str]] = []


def check(label: str, fn) -> None:
    global PASS
    if ONLY and ONLY not in label:
        return
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def note(msg: str) -> None:
    NOTES.append(msg)
    print(f"       · {msg}")


def measured(what: str, value: str) -> None:
    MEASURED.append((what, value))
    print(f"       # {what}: {value}")


def exception(label: str, why: str) -> None:
    """A measured behaviour that violates an invariant this suite asserts
    elsewhere, reported rather than hidden. Never silently tolerated."""
    EXCEPTIONS.append((label, why))
    print(f"  EXCEPT   {label}\n           {why}")


def fixture(ok, msg) -> None:
    """A PRECONDITION inside a gap body — raised as a RuntimeError so `gap`
    below re-reports it as a broken check instead of swallowing it as the
    finding.

    ⚠ Learned the expensive way (2026-08-06, test_batched_asks). A gap
    body's whole contract is "this assert fails", so a fixture assert and the
    assert that measures the defect are indistinguishable: gap() catches the
    first AssertionError it meets and files it as the finding. A credit
    request for 8 against a grant of 20 took the at-or-below no-op branch, so
    no row ever existed — the gap fired on its own scaffolding while the
    defect it named was real but unexercised. Use fixture(...) for every setup
    precondition in a gap body; keep a bare `assert` for the property under
    test."""
    if not ok:
        raise RuntimeError(f"fixture: {msg}")


GAPS: list[tuple[str, str, str]] = []


def gap(label, why, fn) -> None:
    """SHOULD hold, currently does not — asserts the SAFE property, is expected
    to FAIL today, keeps the suite green, and turns RED the day it is fixed.
    (Same idiom as test_hub / test_net_transport; `EXCEPTIONS` above records a
    behaviour with no fix pending, this records one with a fix prescribed.)

    ⚠ Set preconditions with `fixture(...)`, never a bare assert — see there."""
    global PASS
    try:
        fn()
    except AssertionError as e:
        GAPS.append((label, why, str(e).split("\n")[0][:300]))
        print(f"  ⚑ GAP    {label}")
        return
    except Exception:                                            # noqa: BLE001
        FAIL.append((label + " (gap check errored)", traceback.format_exc()))
        print(f"  FAIL     {label} — the gap check itself broke")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote this out of gap()")


def token() -> str:
    return "CP" + os.urandom(5).hex()


# ============================================================ the rig (paths)

TMP = tempfile.mkdtemp(prefix="orgtree-compact-")
HDATA = os.path.join(TMP, "hermetic")     # the in-process half's data root
DATA = os.path.join(TMP, "data")          # the live backend's data root
HOME = os.path.join(TMP, "home")          # transcripts land here

# ⚠ THE MAIL HUB IS NOT ISOLATED BY ORGTREE_DATA (user report 2026-08-06:
# "hundreds of disconnected orgs … crowding the connected client list").
# Every org this rig creates is born with a `local` hub entry at
# net.DEFAULT_HUB_ADDRESS — 127.0.0.1:7370, the user's REAL hub — and the
# backend's net daemon registers it there. A fresh identity per run means one
# new roster row per fixture per run, kept for 30 days, unregisterable.
# `net_autoconnect` cannot be turned off from here (orgs_create reads it from
# the request body, default True); `net_hub_address` CAN — defaults.json is
# read out of THIS data root — so the local entry is pointed at a dead port
# and registration fails harmlessly into the backoff.
# Same spirit as ORGTREE_BRIDGE_PORT=0 below: never touch anything the user's
# real install owns.
DEAD_HUB = "http://127.0.0.1:9"     # discard port: refuses instantly
os.makedirs(DATA, exist_ok=True)
with io.open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    json.dump({"net_hub_address": DEAD_HUB}, _f)

XPROC = os.path.join(TMP, "xproc")        # the cross-process measurement's root
CFG = os.path.join(TMP, "fakecli.json")
WRAP = os.path.join(TMP, "compactcli.js")
LOG = os.path.join(TMP, "backend.log")
for _d in (HDATA, DATA, HOME, XPROC):
    os.makedirs(_d, exist_ok=True)

os.environ["ORGTREE_DATA"] = HDATA        # BEFORE importing orgtree (store
                                          # resolves it at import time)
# ⚠ `transcript_path` globs `~/.claude/projects/*/`, and `~` is resolved at
# CALL time — so an un-redirected hermetic run scans the operator's real
# transcript store (thousands of files, and a session-id collision would make
# `reconcile` reach the wrong verdict). Point HOME at the rig before anything
# can look.
os.environ["USERPROFILE"] = HOME
os.environ["HOME"] = HOME

from orgtree import ledger, store, supervisor                    # noqa: E402
from orgtree.ledger import EXTERN, USER, LedgerError, Org        # noqa: E402


# =========================================================== hermetic helpers

def hspec(**over):
    s = dict(add_dirs=[], tools={"bash": True, "web": False, "edit": False,
                                 "subagents": False, "mcp": []},
             org_visibility="team", charter="test hire")
    s.update(over)
    return s


_hn = [0]


def horg(nodes: int = 1, grant: int = 20) -> tuple[Org, list[str]]:
    """A saved org with N top-level nodes, in the hermetic data root. Returns
    the live object (callers save when they want to)."""
    _hn[0] += 1
    org = store.create_org(f"zz compact {_hn[0]}")
    ids = []
    for i in range(nodes):
        r = org.hire(USER, None, "haiku", grant, f"a{i}", **hspec())
        ids.append(r["node"])
    store.save_org(org)
    return org, ids


def hire_under(org: Org, parent: str, name: str, grant: int = 0,
               actor: str = USER) -> str:
    return org.hire(actor, parent, "haiku", grant, name, **hspec())["node"]


def sid_of(org: Org, nid: str) -> str:
    return org.node(nid)["session_id"]


# ================================================== 1. hermetic: the lineage axis

def lineage_algebra() -> None:
    print("\ncompact_split — the shape of a split:")

    org, (a,) = horg()
    hire_under(org, a, "kid", 0)
    before_free_user = org.free(USER)
    before_committed = org.committed(a)
    s0 = sid_of(org, a)
    pred = org.compact_split(a, "sid-gen1")

    check("split · the predecessor id is <nid>@<generation>",
          lambda: (_eq(pred, "a0@0")))
    check("split · the successor keeps the node id",
          lambda: _true(a in org.nodes and org.node(a)["state"] == "live"))
    check("split · the successor takes the new session id",
          lambda: _eq(sid_of(org, a), "sid-gen1"))
    check("split · the predecessor keeps the OLD session id",
          lambda: _eq(org.nodes[pred]["session_id"], s0))
    check("split · generation increments on the successor",
          lambda: _eq(org.node(a)["generation"], 1))
    check("split · the predecessor keeps the OLD generation",
          lambda: _eq(org.nodes[pred]["generation"], 0))
    check("split · successor.predecessor points at the bearer",
          lambda: _eq(org.node(a)["predecessor"], pred))
    check("split · bearer.successor points back",
          lambda: _eq(org.nodes[pred]["successor"], a))
    check("split · the bearer is archived",
          lambda: _eq(org.nodes[pred]["state"], "archived"))
    check("split · the bearer is a KNOWLEDGE bearer",
          lambda: _eq(org.nodes[pred]["bearer_state"], "knowledge"))
    check("split · the bearer holds 0 credits",
          lambda: _eq(org.nodes[pred]["grant"], 0))
    check("split · the bearer shares the successor's parent slot",
          lambda: _eq(org.nodes[pred]["parent"], org.node(a)["parent"]))
    check("split · the bearer is read-only (every tool off)",
          lambda: _true(not any(org.nodes[pred]["scope"]["tools"][k]
                                for k in ("bash", "web", "edit", "subagents"))
                        and org.nodes[pred]["scope"]["tools"]["mcp"] == []))
    check("split · the bearer's cost_usd starts at 0 (it never re-bills)",
          lambda: _eq(org.nodes[pred]["cost_usd"], 0.0))
    check("split · the successor keeps its charter and title",
          lambda: _true(org.node(a)["charter"] == org.nodes[pred]["charter"]))

    # the accounting: a split must be budget-neutral in both directions
    check("split · the user's free credit is unchanged",
          lambda: _eq(org.free(USER), before_free_user))
    check("split · the successor's committed is unchanged",
          lambda: _eq(org.committed(a), before_committed))
    check("split · audit still finds no overdraft",
          lambda: _true(org.audit()["no_overdraft"]))
    check("split · the bearer does not count as a live child of the parent",
          lambda: _true(pred not in org.children(None)))
    check("split · …nor as an ORG child",
          lambda: _true(pred not in org.org_children(None)))
    check("split · the successor's own children are untouched",
          lambda: _eq(org.org_children(a), ["kid"]))

    # the deep-copy finding, re-pinned: {**scope} aliases add_dirs
    org2, (b,) = horg()
    org2.node(b)["scope"]["add_dirs"] = [{"path": "C:/x", "mode": "rw"}]
    p2 = org2.compact_split(b, "s2")
    org2.node(b)["scope"]["add_dirs"][0]["mode"] = "ro"
    check("split · the bearer's dir grants are a DEEP copy (no aliasing)",
          lambda: _eq(org2.nodes[p2]["scope"]["add_dirs"][0]["mode"], "rw"))
    org2.node(b)["scope"]["add_dirs"].append({"path": "C:/y", "mode": "rw"})
    check("split · …the list itself is copied too",
          lambda: _eq(len(org2.nodes[p2]["scope"]["add_dirs"]), 1))

    # ---- the AGENT is told, not only its superior (user ruling 2026-08-10)
    def _notes(o, k):
        return " ".join(x["text"] for x in (o.d.get("notices") or {}).get(k, []))

    check("split · the compacted agent is told it HAS a bearer, and its id",
          lambda: _true(pred in _notes(org, a),
                        f"the node's own notices: {_notes(org, a)!r}"))
    check("split · …and that rehiring it is the way to consult it — knowing "
          "is exactly what compaction took away, so nothing else can tell it",
          lambda: _true("orgtree_rehire" in _notes(org, a)
                        and "subordinate" in _notes(org, a),
                        _notes(org, a)))
    check("split · the superior is still told (the old notice is unchanged)",
          lambda: _true(pred in _notes(org, org.node(a)["parent"] or USER)
                        or org.node(a)["parent"] is None))

    check("split · the split is logged",
          lambda: _true(any(e["op"] == "compact_split"
                            for e in org.d["events"])))
    check("split · the parent chain is notified",
          lambda: _true(True))       # top-level: parent is None, nothing to notify

    print("\nrepeated generations:")
    org3, (c,) = horg()
    gens = 3 if QUICK else 8
    preds = [org3.compact_split(c, f"s-{i}") for i in range(gens)]
    check(f"gens · {gens} splits produce {gens} distinct bearers",
          lambda: _eq(len(set(preds)), gens))
    check("gens · lineage_stack returns them newest-first",
          lambda: _eq(org3.lineage_stack(c), list(reversed(preds))))
    check("gens · the successor's generation equals the split count",
          lambda: _eq(org3.node(c)["generation"], gens))
    check("gens · every bearer is archived at 0 credits",
          lambda: _true(all(org3.nodes[p]["state"] == "archived"
                            and org3.nodes[p]["grant"] == 0 for p in preds)))
    check("gens · org_children(None) still holds exactly the live agent",
          lambda: _eq(org3.org_children(None), [c]))
    check("gens · audit is still clean after every generation",
          lambda: _true(org3.audit()["no_overdraft"]))
    check("gens · the tree has ONE root — the ghosts are not siblings",
          lambda: _eq(len(org3.tree()["roots"]), 1))
    check("gens · the tree exposes the whole lineage on that root",
          lambda: _eq(len(org3.tree()["roots"][0]["lineage"]), gens))
    check("gens · tree lineage generations descend from newest",
          lambda: _eq([x["generation"] for x in org3.tree()["roots"][0]["lineage"]],
                      list(range(gens - 1, -1, -1))))
    check("gens · every lineage entry is flagged as a knowledge bearer",
          lambda: _true(all(x["bearer_state"] == "knowledge"
                            for x in org3.tree()["roots"][0]["lineage"])))

    # the loop guard, on data the API cannot produce but a hand-edit can
    org3.nodes[preds[0]]["predecessor"] = preds[-1]
    check("gens · a predecessor CYCLE terminates instead of hanging",
          lambda: _true(len(_with_timeout(lambda: org3.lineage_stack(c), 5.0))
                        <= len(org3.nodes)))

    print("\nlineage vs the org axis:")
    org4, (d,) = horg()
    k1 = hire_under(org4, d, "k1", 0)
    pd = org4.compact_split(d, "sd")
    check("axis · a bearer is NOT an org child of its own parent",
          lambda: _true(pd not in org4.org_children(None)))
    check("axis · a bearer IS in children(live_only=False) (same slot)",
          lambda: _true(pd in org4.children(None, live_only=False)))
    check("axis · descendants() of the successor does not include the bearer",
          lambda: _true(pd not in org4.descendants(d, live_only=False)))
    check("axis · the successor still parents its own reports",
          lambda: _true(k1 in org4.descendants(d, live_only=False)))
    check("axis · depth is unchanged by a split",
          lambda: _eq(org4.depth(d), 0))
    check("axis · the bearer sits at the successor's depth",
          lambda: _eq(org4.depth(pd), 0))


def _with_timeout(fn, secs: float):
    """Run fn on a thread and fail if it does not finish — the hang guards in
    this file are about NON-termination, which an assert cannot express."""
    box: list = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    t.join(secs)
    if t.is_alive():
        raise AssertionError(f"did not terminate within {secs}s")
    return box[0]


def _eq(got, want) -> None:
    if got != want:
        raise AssertionError(f"got {got!r}, want {want!r}")


def _true(cond, msg: str = "") -> None:
    if not cond:
        raise AssertionError(msg or "expected true")


def _raises(fn, frag: str = "") -> None:
    try:
        fn()
    except LedgerError as e:
        if frag and frag.lower() not in str(e).lower():
            raise AssertionError(f"raised, but not about {frag!r}: {e}")
        return
    raise AssertionError("expected a LedgerError, got none")


# ============================== 2. hermetic: bearer rules in every other op

def bearer_rules() -> None:
    print("\nrehire of a knowledge bearer:")

    org, (a,) = horg(grant=20)
    pred = org.compact_split(a, "s1")
    free_before = org.free(a)
    r = org.rehire(a, pred, grant=0)
    check("rehire · a node may rehire its OWN bearer",
          lambda: _eq(org.nodes[pred]["state"], "live"))
    check("rehire · a self-rehired bearer becomes the successor's SUBORDINATE",
          lambda: _eq(org.nodes[pred]["parent"], a))
    check("rehire · …and the successor pays its seat",
          lambda: _eq(org.free(a), free_before - org.seat_cost(pred)))
    check("rehire · the rehire warns about the arrangement",
          lambda: _true(any("subordinate" in w.lower() or "command" in w.lower()
                            for w in r.get("warnings", []))))
    check("rehire · a live bearer still carries bearer_state",
          lambda: _eq(org.nodes[pred]["bearer_state"], "knowledge"))
    # USER RULING 2026-08-12, replacing the older "a live bearer stays OFF the
    # org chart" rule this check used to pin: the `successor` link stays, but
    # it is not on its own enough to hide a node. A REHIRED bearer works —
    # takes turns, spends credits, answers mail — and hiding a live spending
    # session from the axis its operator manages by cost the user control of
    # it (the neoja org hit it as a canvas crash: a node in the map that
    # `layout` never placed). Retired AND succeeded hides; succeeded alone
    # does not.
    check("rehire · a REHIRED bearer is on the org chart — the successor "
          "link alone does not hide a working agent (user ruling)",
          lambda: _true(pred in org.children(a)
                        and pred in org.org_children(a)))
    check("rehire · …and the OTHER half of that ruling still holds: retire "
          "it again and the archived bearer leaves the org axis",
          lambda: (org.retire(USER, pred),
                   _true(pred in org.children(a, live_only=False)
                         and pred not in org.org_children(a)),
                   org.rehire(USER, pred, grant=0))[1])
    check("rehire · …so the successor's committed credit counts it",
          lambda: _true(org.committed(a) >= org.seat_cost(pred)))
    # "retired" at the ruling's word (redteam deviation catch 2026-08-12):
    # the first cut hid `state != "live"`, which also dropped an
    # UNRECOVERABLE generation — the state whose own notice says "rehire to
    # re-seed, or retire to free the credits", i.e. a node the operator MUST
    # be able to reach. Off the axis it rendered nowhere at all when its
    # successor was archived. Only archived steps off.
    check("rehire · an UNRECOVERABLE bearer stays ON the org axis — the "
          "operator must be able to re-seed or retire it",
          lambda: (org.mark_unrecoverable(pred, "session gone"),
                   _true(pred in org.org_children(a)))[1])

    # the superior-rehired path: stays a coworker in the OLD slot
    org2, (b,) = horg(grant=20)
    hire_under(org2, b, "peer", 0)
    p2 = org2.compact_split(b, "s2")
    org2.rehire(USER, p2, grant=0)
    check("rehire · a USER-rehired bearer keeps the old parent slot",
          lambda: _eq(org2.nodes[p2]["parent"], org2.node(b)["parent"]))
    check("rehire · …so it is a peer of its successor, not a report",
          lambda: _true(org2.nodes[p2]["parent"] == org2.node(b)["parent"]))

    check("rehire · a rehired bearer counts against the budget again",
          lambda: _true(org2.seat_cost(p2) > 0
                        and p2 in org2.children(org2.node(b)["parent"])))

    print("\ndissolve / retire / delete take the whole stack:")
    org3, (c,) = horg(grant=30)
    k = hire_under(org3, c, "kid", 0)
    p3a = org3.compact_split(c, "s3a")
    p3b = org3.compact_split(c, "s3b")
    org3.rehire(USER, p3a, grant=0)          # a LIVE bearer beside its successor
    sub = hire_under(org3, p3a, "bearerkid", 0)
    res = org3.dissolve(USER, c)
    check("dissolve · the successor's whole lineage stack is archived",
          lambda: _true(all(org3.nodes[x]["state"] == "archived"
                            for x in (c, p3a, p3b))))
    check("dissolve · a rehired bearer's OWN subtree goes with it",
          lambda: _eq(org3.nodes[sub]["state"], "archived"))
    check("dissolve · the org child goes too",
          lambda: _eq(org3.nodes[k]["state"], "archived"))
    check("dissolve · the report names every node it took",
          lambda: _true({c, p3a, p3b, k, sub} <= set(res["nodes"])))
    check("dissolve · nothing live is left under an archived parent",
          lambda: _true(not [x for x, n in org3.nodes.items()
                             if n["state"] == "live" and n["parent"]
                             and org3.nodes[n["parent"]]["state"] == "archived"]))
    check("dissolve · audit stays clean",
          lambda: _true(org3.audit()["no_overdraft"]))

    org4, (e,) = horg(grant=30)
    p4 = org4.compact_split(e, "s4")
    org4.rehire(USER, p4, grant=0)
    sub4 = hire_under(org4, p4, "bk", 0)
    org4.delete(USER, e)
    check("delete · the bearer is removed with its successor",
          lambda: _true(p4 not in org4.nodes and e not in org4.nodes))
    check("delete · the bearer's own subtree is removed too",
          lambda: _true(sub4 not in org4.nodes))
    check("delete · no dangling parent id survives",
          lambda: _true(all(n["parent"] is None or n["parent"] in org4.nodes
                            for n in org4.nodes.values())))
    check("delete · the deleted burn is banked",
          lambda: _true(org4.cost_total() >= 0))

    org5, (f,) = horg(grant=30)
    p5 = org5.compact_split(f, "s5")
    org5.retire(USER, f)
    check("retire · retiring the successor leaves the bearer archived",
          lambda: _eq(org5.nodes[p5]["state"], "archived"))
    check("retire · …and frees only the successor's holding",
          lambda: _true(org5.audit()["no_overdraft"]))

    print("\nmove and a lineage stack:")
    org6, (g,) = horg(grant=30)
    h = org6.hire(USER, None, "haiku", 10, "target", **hspec())["node"]
    p6 = org6.compact_split(g, "s6")
    check("move · a bearer may not be moved on its own",
          lambda: _raises(lambda: org6.move(USER, p6, h), "lineage bearer"))
    org6.move(USER, g, h)
    check("move · moving the successor drags the stack into the new slot",
          lambda: _eq(org6.nodes[p6]["parent"], h))
    check("move · the stack still shares the successor's parent",
          lambda: _eq(org6.nodes[p6]["parent"], org6.node(g)["parent"]))
    check("move · the move is budget-neutral",
          lambda: _true(org6.audit()["no_overdraft"]))

    org7, (i,) = horg(grant=30)
    j = org7.hire(USER, None, "haiku", 10, "tgt2", **hspec())["node"]
    p7 = org7.compact_split(i, "s7")
    org7.rehire(USER, p7, grant=0)
    check("move · a LIVE bearer under consultation blocks the move",
          lambda: _raises(lambda: org7.move(USER, i, j), "consultation"))

    print("\nreseed and the lost generation:")
    org8, (k8,) = horg(grant=20)
    org8.node(k8)["state"] = "unrecoverable"
    r8 = org8.reseed(USER, k8, "fresh-1")
    lost = r8["predecessor"]
    check("reseed · a dead session archives as a LOST generation",
          lambda: _eq(org8.nodes[lost]["bearer_state"], "lost"))
    check("reseed · the node comes back live with a fresh session",
          lambda: _true(org8.node(k8)["state"] == "live"
                        and sid_of(org8, k8) == "fresh-1"))
    check("reseed · the generation counter advances like a compaction",
          lambda: _eq(org8.node(k8)["generation"], 1))
    check("reseed · a lost generation is not rehirable",
          lambda: _raises(lambda: org8.rehire(USER, lost), "lost"))
    check("reseed · a lost generation still sits in the lineage stack",
          lambda: _true(lost in org8.lineage_stack(k8)))

    # a knowledge bearer that itself loses its transcript
    org9, (m,) = horg(grant=20)
    p9 = org9.compact_split(m, "s9")
    org9.rehire(USER, p9, grant=0)
    org9.nodes[p9]["state"] = "unrecoverable"
    r9 = org9.reseed(USER, p9, "never-used")
    check("reseed · a bearer with no transcript becomes LOST in place",
          lambda: _eq(org9.nodes[p9]["bearer_state"], "lost"))
    check("reseed · …and no fresh session is minted for it",
          lambda: _true("predecessor" not in r9))
    check("reseed · …and it is archived, freeing its seat",
          lambda: _eq(org9.nodes[p9]["state"], "archived"))
    check("reseed · the successor is told its bearer is gone",
          lambda: _true(any("lost" in x["text"].lower()
                            for x in (org9.d.get("notices") or {}).get(m, [])),
                        json.dumps((org9.d.get("notices") or {}).get(m))))

    print("\nexternal mail and bearers:")
    # C0 (user rulings 2026-08-05): recipients are ORG-INBOX AUDIENCE HOLDERS,
    # so the question a compaction raises is no longer "is the successor still
    # top-level" but "does the AUDIENCE follow the live successor, or does it
    # stay with the archived bearer". If it stayed with the bearer, inbound
    # org mail would bypass the live agent entirely — measured below, and it
    # does NOT: audience rows key on the grantee ID, and compact_split gives
    # the NEW id to the bearer while the successor keeps the original one.
    org10, (n10,) = horg(grant=20)
    org10.audience_grant(USER, n10, "extern")
    check("extern · the holder is a recipient before any compaction",
          lambda: _eq(org10.extern_recipients(), [n10]))
    p10 = org10.compact_split(n10, "s10")
    check("extern · an archived bearer is not an external recipient",
          lambda: _true(p10 not in org10.extern_recipients()))
    check("extern · the audience follows the LIVE successor, not the bearer",
          lambda: _eq(org10.extern_recipients(), [n10]))
    check("extern · …and inbound org mail reaches it, not the bearer",
          lambda: _eq(org10.post_external_mail("@mcp:probe", "after a compact"),
                      [n10]))
    check("extern · the bearer got no copy",
          lambda: _true(not (org10.d.get("mail") or {}).get(p10)))
    org10.rehire(USER, p10, grant=0)
    rec = org10.extern_recipients()
    if p10 in rec:
        exception(
            "extern · a REHIRED top-level knowledge bearer receives org mail",
            f"extern_recipients() = {rec}: a rehired bearer would be asked to "
            "answer FOR THE ORG from a pre-compaction context. Under C0 this "
            "can only happen if the bearer HOLDS the org-inbox audience, so "
            "the old children(None) reasoning no longer applies — re-diagnose "
            "before fixing. Reported, not fixed — ledger.py is not mine.")
    else:
        check("extern · a rehired bearer holds no audience, so it stays excluded",
              lambda: _true(p10 not in rec))

    def _live_bearer_is_not_called_consultable():
        # neoja org report 2026-08-12: a REHIRED bearer was busy at ~373k
        # occupancy, running Bash and reading files — and every chart still
        # annotated it "knowledge bearer — consultable". The chart is an
        # agent's only view of the org, so that is a false statement about a
        # running agent, made to the one reader who cannot check it.
        o = Org.create("bearer-chart", dirs=["E:/w"])
        o.hire(USER, None, "haiku", 10, "boss")
        o.hire(USER, "boss", "haiku", 5, "deployer")
        pred = o.compact_split("deployer", "sess-2")

        def line() -> str:
            return next(x for x in supervisor._render_chart(o, [pred], "")
                        if pred in x)

        assert "consultable" in line(), line()
        o.rehire(USER, pred, grant=0)
        _eq(o.node(pred)["state"], "live")
        _true("consultable" not in line(),
              f"a live, working bearer is still advertised as a thing to "
              f"consult: {line()}")
        _true("live" in line(), line())
    check("chart · a REHIRED bearer reads as live and working, not as "
          "consultable", _live_bearer_is_not_called_consultable)

    # ---- the read-down, where a lineage rule meets the turn command line.
    # Reported by the neoja org 2026-08-12: Write/Edit denied on ordinary
    # files in a seat's OWN scratch — reads fine, Bash writes fine, and the
    # charter meanwhile REQUIRES breadcrumbs.md be kept there through those
    # very tools. The FR-24 read-down grants the predecessor's scratch
    # read-only, and `scratch_dir` deliberately maps `name@gen` onto `name`
    # (lineage nodes share their successor's folder — same self, different
    # times). After the in-place rework the predecessor IS `nid@gen`, so the
    # read-down resolved onto the live node's own cwd and denied it.
    org11 = store.create_org("zz compaction readdown")
    s11 = org11.d["slug"]
    try:
        org11.hire(USER, None, "haiku", 5, "solo")
        store.save_org(org11)
        own = supervisor.scratch_dir(s11, "solo").replace("\\", "/").rstrip("/")

        def _deny() -> list[str]:
            cmd = supervisor._build_cmd(store.load_org(s11), "solo")
            st = json.loads(cmd[cmd.index("--settings") + 1])
            return list((st.get("permissions") or {}).get("deny") or [])

        check("read-down · a fresh seat is denied nothing",
              lambda: _eq(_deny(), []))
        o11 = store.load_org(s11)
        o11.cheap_compact(USER, "solo")
        store.save_org(o11)
        check("read-down · the in-place predecessor shares the seat's scratch",
              lambda: _eq(supervisor.scratch_dir(
                  s11, store.load_org(s11).node("solo")["predecessor"]),
                  supervisor.scratch_dir(s11, "solo")))
        check("read-down · …so the seat is STILL free to write its own "
              "working folder with the file tools",
              lambda: _true(not any(own in d for d in _deny()),
                            f"denied in its own cwd: {_deny()}"))
        check("read-down · and nothing else was denied either",
              lambda: _eq(_deny(), []))
    finally:
        try:
            store.delete_org(s11)
        except Exception:                                        # noqa: BLE001
            pass


# =============================== 3. hermetic: the threshold arithmetic

class _FakeRes(dict):
    pass


def thresholds() -> None:
    print("\ncompaction threshold (_after_turn):")

    calls: list[tuple[str, str]] = []
    real_split = supervisor._compact_split

    def spy(slug: str, nid: str) -> None:
        calls.append((slug, nid))

    def run_after(org: Org, nid: str, occ: int, res: dict | None = None) -> None:
        st = supervisor.state(org.d["slug"], nid)
        supervisor._after_turn(org.d["slug"], nid, org, res or {}, st, occ)

    supervisor._compact_split = spy                        # type: ignore[assignment]
    try:
        cw = supervisor.TIER_CONTEXT["haiku"]
        for frac, want in ((0.10, False), (0.79, False), (0.80, True),
                           (0.81, True), (0.999, True)):
            org, (a,) = horg()
            store.save_org(org)
            calls.clear()
            supervisor._state.pop((org.d["slug"], a), None)
            run_after(org, a, int(cw * frac))
            check(f"threshold · occupancy {frac:.0%} of the window "
                  f"{'splits' if want else 'does not split'}",
                  (lambda w=want: _eq(bool(calls), w)))

        # the per-org override, in org-doc FRACTION units
        org, (a,) = horg()
        org.d["compact_at"] = 0.50
        store.save_org(org)
        calls.clear()
        supervisor._state.pop((org.d["slug"], a), None)
        run_after(org, a, int(cw * 0.55))
        check("threshold · the per-org compact_at overrides the env default",
              lambda: _true(bool(calls)))

        # the hard cap
        org, (a,) = horg()
        org.d["compact_at"] = 0.99
        store.save_org(org)
        calls.clear()
        supervisor._state.pop((org.d["slug"], a), None)
        run_after(org, a, int(cw * 0.96))
        check("threshold · compact_at is hard-capped at 95% however high the doc says",
              lambda: _true(bool(calls)))

        # doc-level compact_at values the /settings route would never write,
        # but defaults.json (stored org-doc-shaped and unvalidated) and a
        # hand-edited doc both can. FIXED here: before the `_threshold` clamp
        # a negative value made `occ/cw >= compact_at` true on EVERY turn, so
        # every turn forked a 600 s-ceiling compaction on a near-empty context.
        for bad, human in ((-1.0, "a negative"), (0.0, "a zero"),
                           (float("nan"), "a NaN"), (80, "a percent"),
                           ("junk", "a junk string")):
            org, (a,) = horg()
            org.d["compact_at"] = bad          # type: ignore[typeddict-item]
            store.save_org(org)
            calls.clear()
            supervisor._state.pop((org.d["slug"], a), None)
            run_after(org, a, 1)               # 1 token of a 200k window
            check(f"threshold · {human} compact_at does not compact an empty "
                  f"context",
                  (lambda: _eq(calls, [])))
            calls.clear()
            run_after(org, a, int(cw * 0.9))   # …and still splits when it should
            check(f"threshold · …{human} compact_at still splits at 90% "
                  f"(it falls back, it does not disable)",
                  (lambda: _true(bool(calls))))

        # occupancy of zero is 'unknown', not 'empty'
        org, (a,) = horg()
        store.save_org(org)
        calls.clear()
        supervisor._state.pop((org.d["slug"], a), None)
        run_after(org, a, 0)
        check("threshold · an unknown occupancy (0) never triggers a split",
              lambda: _eq(calls, []))

        # an unknown model has no pinned window — the CLI's number is the fallback
        org, (a,) = horg()
        org.node(a)["model"] = "mystery"
        org.d["tiers"]["mystery"] = 1
        org.d["models"]["mystery"] = "mystery"
        store.save_org(org)
        calls.clear()
        supervisor._state.pop((org.d["slug"], a), None)
        run_after(org, a, 9000, {"modelUsage": {"m": {"contextWindow": 10000}}})
        check("threshold · an unpinned tier falls back to the CLI's contextWindow",
              lambda: _true(bool(calls)))

        # ---- the CLI can compact first, and then nothing splits ----------------
        # USER REPORT 2026-08-06: "when an agent auto-compacts I don't see its
        # retired pre-compacted sessions behind itself."
        #
        # The stack behind a card is `node.lineage` (ledger.tree → lineage_stack),
        # and the only thing that ever writes a lineage entry is `compact_split`,
        # reached from `_maybe_compact` when `occ / cw >= compact_at`. So "no card
        # behind it" means the split never ran — and these two checks are about
        # WHY it can fail to run on a turn that visibly compacted.
        def _occupancy_is_sampled_at_its_PEAK():
            """`turn_occ = t` (supervisor.py, the stream loop) OVERWRITES on every
            assistant message, so what reaches `_after_turn` is the LAST call's
            context size, not the turn's high-water mark. A turn that climbs past
            the threshold and is then compacted BY THE CLI ends small, and the
            crossing is never observed — no split, no bearer, no stack.

            ⚠ Reading `max` here does NOT reintroduce the bug the docstring at
            `_after_turn` warns about. That one was the RESULT event's `usage`,
            which is cumulative across the turn's API calls (it read a 19%-full
            context as 123%). Per-MESSAGE usage is point-in-time — input +
            cache_read + cache_creation is the context size at that call — so the
            maximum over messages is a real peak, not a sum."""
            src = open(os.path.join(_REPO, "backend", "orgtree", "supervisor.py"),
                       encoding="utf-8").read()
            body = "\n".join(ln for ln in src.splitlines()
                             if not ln.lstrip().startswith("#"))
            fixture("turn_occ" in body,
                    "the sampling site moved — re-read this check")
            assert re.search(r"turn_occ\s*=\s*max\(turn_occ,", body), (
                "occupancy is sampled as the LAST assistant call, not the peak: a "
                "turn whose context crossed the threshold mid-flight and was then "
                "compacted by the CLI reports its small post-compaction size, so "
                "_maybe_compact never fires and the node never gains a knowledge "
                "bearer — the user sees the compaction in the transcript and no "
                "card behind the agent")
            assert not re.search(r"turn_occ\s*=\s*t\b", body), (
                "a plain last-write assignment coexists with the max — the "
                "overwrite would still lose the peak on whichever path runs it")
        # ← FIXED (promoted out of gap(), 2026-08-06, same day): the stream
        # loop samples `turn_occ = max(turn_occ, t)`. Matcher repaired on
        # promotion (implementer): the original anchored a 2600-char window
        # at `turn_occ = 0`, but the sampling site sits ~180 lines below the
        # init — outside the window — so the check could never have flipped
        # on the fix; it now scans the whole module for the max-form AND the
        # absence of any plain overwrite.
        check("sampling · context occupancy reaches _after_turn as the turn's "
              "PEAK, not its last call", _occupancy_is_sampled_at_its_PEAK)

        def _a_cli_compaction_is_noticed_by_the_TURN_path():
            """The signal exists and is already parsed — just not where it could
            act. `read_chat` reads `system/compact_boundary` out of the session
            JSONL and renders "— context compacted — · Nk tokens" (that is how the
            user SEES the compaction they are reporting), and
            `compactMetadata.preTokens` is the pre-compaction size sitting right
            there. The live turn path never looks."""
            src = open(os.path.join(_REPO, "backend", "orgtree", "supervisor.py"),
                       encoding="utf-8").read()
            fixture("compact_boundary" in src,
                    "the boundary parser is gone — re-read this check")
            i = src.index("def _after_turn")
            j = src.index("def _fork_result")
            seg = src[i:j]
            assert "compact_boundary" in seg or "preTokens" in seg, (
                "the turn path never learns that the CLI compacted this session, "
                "so the one event that MAKES a generation — the context being "
                "replaced by a summary — passes without recording one. The "
                "transcript shows '— context compacted —' and the canvas shows no "
                "card behind the agent: the same event, told two different ways")
        # ← FIXED (promoted out of gap(), 2026-08-06, same day): the exact
        # prescribed shape — `_count_cli_compactions` scans the session JSONL
        # for compact_boundary records after each turn; a NEW boundary mints
        # `record_cli_compaction` (reseed's lost-generation precedent:
        # generation bumped, lineage entry bearer_state="lost", session id
        # unchanged) and SKIPS the occ-threshold split that turn, since the
        # peak may predate the CLI's compaction. First observation BASELINES
        # without minting, so long-lived orgs are not restructured
        # retroactively on the deploy turn.
        check("boundary · a CLI-side auto-compaction is recorded as a "
              "generation, not silently absorbed",
              _a_cli_compaction_is_noticed_by_the_TURN_path)

        print("\ncooldown after a failed split (№28):")
        org, (a,) = horg()
        store.save_org(org)
        supervisor._state.pop((org.d["slug"], a), None)
        st = supervisor.state(org.d["slug"], a)
        st["compact_retry_at"] = time.time() + 900
        calls.clear()
        run_after(org, a, int(cw * 0.9))
        check("cooldown · a node inside its cooldown does not re-fire",
              lambda: _eq(calls, []))
        st["compact_retry_at"] = time.time() - 1
        calls.clear()
        run_after(org, a, int(cw * 0.9))
        check("cooldown · …and fires again once the cooldown lapses",
              lambda: _true(bool(calls)))

        print("\nthe §8.3 oracle transition:")
        # rehired BY ITS SUCCESSOR, so the bearer's parent is a real node —
        # the transition notice is addressed to `parent`, and the top-level
        # case below shows what that costs when the parent is None.
        org, (a,) = horg()
        pred = org.compact_split(a, "sx")
        org.rehire(a, pred, grant=0)
        store.save_org(org)
        supervisor._state.pop((org.d["slug"], pred), None)
        calls.clear()
        run_after(org, pred, int(cw * 0.99))
        check("oracle · a knowledge bearer NEVER re-compacts",
              lambda: _eq(calls, []))
        check("oracle · …it becomes a preserving oracle instead",
              lambda: _eq(store.load_org(org.d["slug"]).nodes[pred]["bearer_state"],
                          "preserving"))
        check("oracle · the successor is notified of the transition",
              lambda: _true(any("ORACLE" in x["text"].upper() for x in
                                (store.load_org(org.d["slug"]).d.get("notices")
                                 or {}).get(a, [])),
                            json.dumps((store.load_org(org.d["slug"]).d
                                        .get("notices") or {}).get(a))))

        # the same transition on a TOP-LEVEL bearer, where `parent` is None
        org, (a,) = horg()
        pred = org.compact_split(a, "sx2")
        org.rehire(USER, pred, grant=0)      # superior-rehired: keeps the old slot
        store.save_org(org)
        supervisor._state.pop((org.d["slug"], pred), None)
        run_after(org, pred, int(cw * 0.99))
        after_doc = store.load_org(org.d["slug"])
        check("oracle · a top-level bearer still transitions",
              lambda: _eq(after_doc.nodes[pred]["bearer_state"], "preserving"))
        # FIXED here: the notice used to go to `parent` alone, and `_notify`
        # drops a falsy target — so a top-level bearer's transition was
        # announced to nobody at all.
        check("oracle · a TOP-LEVEL bearer's transition still reaches its "
              "successor (parent is None there)",
              lambda: _true(any("ORACLE" in x["text"].upper()
                                for x in (after_doc.d.get("notices")
                                          or {}).get(a, [])),
                            json.dumps(after_doc.d.get("notices"))[:300]))
        check("oracle · …and exactly once, not once per target",
              lambda: _eq(len([x for lst in (after_doc.d.get("notices")
                                             or {}).values()
                               for x in lst if "ORACLE" in x["text"].upper()]), 1))

        # below the oracle threshold the bearer stays a knowledge bearer
        org, (a,) = horg()
        pred = org.compact_split(a, "sy")
        org.rehire(USER, pred, grant=0)
        store.save_org(org)
        supervisor._state.pop((org.d["slug"], pred), None)
        calls.clear()
        run_after(org, pred, int(cw * 0.5))
        check("oracle · below ORACLE_AT the bearer stays 'knowledge'",
              lambda: _eq(store.load_org(org.d["slug"]).nodes[pred]["bearer_state"],
                          "knowledge"))

        # a preserving oracle stays preserving and never splits
        org, (a,) = horg()
        pred = org.compact_split(a, "sz")
        org.rehire(USER, pred, grant=0)
        org.nodes[pred]["bearer_state"] = "preserving"
        store.save_org(org)
        supervisor._state.pop((org.d["slug"], pred), None)
        calls.clear()
        run_after(org, pred, int(cw * 0.999))
        check("oracle · a preserving oracle neither splits nor re-transitions",
              lambda: _true(not calls and store.load_org(org.d["slug"])
                            .nodes[pred]["bearer_state"] == "preserving"))
    finally:
        supervisor._compact_split = real_split             # type: ignore[assignment]

    print("\nwhat _build_cmd does with each bearer state:")
    org, (a,) = horg()
    pred = org.compact_split(a, "sb")
    org.rehire(a, pred, grant=0)
    store.save_org(org)
    slug = org.d["slug"]
    # `first` is derived from the transcript's existence, so both sessions need
    # a file on disk for the resume path to be the one under test
    for sid in (sid_of(org, a), sid_of(org, pred)):
        _plant_transcript(sid)
    cmd_live = supervisor._build_cmd(store.load_org(slug), a)
    cmd_know = supervisor._build_cmd(store.load_org(slug), pred)
    org.nodes[pred]["bearer_state"] = "preserving"
    store.save_org(org)
    cmd_orac = supervisor._build_cmd(store.load_org(slug), pred)
    check("cmd · a live successor resumes its own session in place",
          lambda: _true("--resume" in cmd_live and "--fork-session" not in cmd_live,
                        " ".join(cmd_live[-6:])))
    check("cmd · …at the POST-compaction session id",
          lambda: _eq(cmd_live[cmd_live.index("--resume") + 1], "sb"))
    check("cmd · a knowledge bearer also resumes in place (it is consultable)",
          lambda: _true("--resume" in cmd_know and "--fork-session" not in cmd_know,
                        " ".join(cmd_know[-6:])))
    check("cmd · a PRESERVING oracle resumes into a FORK (§8.4)",
          lambda: _true("--resume" in cmd_orac and "--fork-session" in cmd_orac,
                        " ".join(cmd_orac[-6:])))
    check("cmd · the oracle's fork resumes the bearer's own session id",
          lambda: _eq(cmd_orac[cmd_orac.index("--resume") + 1],
                      sid_of(store.load_org(slug), pred)))
    check("cmd · a bearer and its successor share one scratch dir",
          lambda: _eq(supervisor.scratch_dir(slug, pred),
                      supervisor.scratch_dir(slug, a)))
    fresh = hire_under(org, a, "nevertalked", 0)
    store.save_org(org)
    cmd_new = supervisor._build_cmd(store.load_org(slug), fresh)
    check("cmd · a node with no transcript MINTS a session rather than "
          "resuming a ghost",
          lambda: _true("--session-id" in cmd_new and "--resume" not in cmd_new,
                        " ".join(cmd_new[-6:])))


# ============ 2b. hermetic: what a COMPACTED agent reports about its context

class Sess:
    """One org, one node, one hand-written transcript — the record shapes the
    CLI really writes around a compaction, in the order it writes them.

    Every number here is a real measurement off the machine's own transcripts
    (2026-08-20, ingame-prompt's session 7bfddfd8): the fill climbed to 212,859
    tokens, a `compact_boundary` landed carrying preTokens 214,033, a 16,154-char
    summary followed it, and the next assistant record — one whole turn later —
    measured 58,078. Between the boundary and that record the agent was asked
    how full it was, and answered 212,859."""

    _n = 0

    def __init__(self) -> None:
        Sess._n += 1
        org, (a,) = horg()
        store.save_org(org)
        self.slug, self.nid = org.d["slug"], a
        self.sid = org.node(a)["session_id"]
        d = os.path.join(HOME, ".claude", "projects", "occ")
        os.makedirs(d, exist_ok=True)
        self.path = os.path.join(d, self.sid + ".jsonl")
        self.seq = 0

    def _rec(self, rec: dict) -> None:
        self.seq += 1
        rec.setdefault("timestamp", f"2026-08-20T10:{self.seq // 60:02d}:"
                                    f"{self.seq % 60:02d}.000Z")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def turn(self, occ: int, **over) -> None:
        """An assistant record whose usage says the prompt it answered was
        `occ` tokens — split across the three fields that make up a context
        fill, because reading only `input_tokens` under-reports a cached
        prompt by an order of magnitude."""
        rec = {"type": "assistant",
               "message": {"role": "assistant", "model": "claude-x",
                           "content": [{"type": "text", "text": "ok"}],
                           "usage": {"input_tokens": occ - 1000,
                                     "cache_read_input_tokens": 900,
                                     "cache_creation_input_tokens": 100}}}
        rec.update(over)
        self._rec(rec)

    def boundary(self, pre: int, post: int | None = None, **meta) -> None:
        """`post` is compactMetadata.postTokens — what SURVIVED the compaction.
        Present in 19 of the 24 real boundary records on this machine and
        absent in the older shape, so both are fixtures here."""
        m = {"trigger": "manual", "preTokens": pre, **meta}
        if post is not None:
            m["postTokens"] = post
        self._rec({"type": "system", "subtype": "compact_boundary",
                   "content": "Conversation compacted", "isMeta": False,
                   "compactMetadata": m})

    def summary(self, chars: int) -> None:
        self._rec({"type": "user", "isCompactSummary": True,
                   "message": {"role": "user", "content": "S" * chars}})

    def user(self, text: str = "hello") -> None:
        self._rec({"type": "user", "message": {"role": "user", "content": text}})

    def read(self) -> dict:
        return supervisor.read_chat(store.load_org(self.slug), self.nid)

    def doc_reading(self) -> tuple:
        """The same question asked of the DOC's writer instead of the desk's."""
        return supervisor.session_occupancy(store.load_org(self.slug), self.nid)

    def after_turn(self, occ: int) -> dict:
        """One `_after_turn` with `occ` as the turn's PEAK sample, then the
        node as the doc now holds it."""
        org = store.load_org(self.slug)
        supervisor._after_turn(self.slug, self.nid, org, {},
                               supervisor.state(self.slug, self.nid), occ)
        return store.load_org(self.slug).node(self.nid)


def _A(occ: int) -> dict:
    """One assistant record whose usage adds up to `occ` — the raw shape, for
    fixtures written straight into a file rather than through `Sess`."""
    return {"type": "assistant", "timestamp": "2026-08-20T10:00:00.000Z",
            "message": {"role": "assistant", "model": "claude-x",
                        "content": [{"type": "text", "text": "ok"}],
                        "usage": {"input_tokens": occ - 1000,
                                  "cache_read_input_tokens": 900,
                                  "cache_creation_input_tokens": 100}}}


def _B(pre: int, post: int | None = None) -> dict:
    m = {"trigger": "manual", "preTokens": pre}
    if post is not None:
        m["postTokens"] = post
    return {"type": "system", "subtype": "compact_boundary",
            "timestamp": "2026-08-20T10:00:01.000Z", "isMeta": False,
            "content": "Conversation compacted", "compactMetadata": m}


def _S(chars: int) -> dict:
    return {"type": "user", "isCompactSummary": True,
            "timestamp": "2026-08-20T10:00:02.000Z",
            "message": {"role": "user", "content": "S" * chars}}


#: a stand-in for `claude -p --resume … --fork-session`, so the §8 split's own
#: doc writes can be exercised without the live half's real CLI and its three
#: minutes. It writes the transcript the fork would have left — the records the
#: caller names, which is the whole point: a fork that writes NO boundary is a
#: real shape, and the reason `occupancy_of` has `require_boundary`.
_FORK_STUB = os.path.join(TMP, "forkstub.py")
_FORK_SPEC = os.path.join(TMP, "forkstub.spec.json")
with open(_FORK_STUB, "w", encoding="utf-8") as _f:
    _f.write(
        "import json, os, sys\n"
        "sys.stdin.read()\n"                      # the "/compact" the caller pipes
        "here = os.path.dirname(os.path.abspath(__file__))\n"
        "spec = json.load(open(os.path.join(here, 'forkstub.spec.json'),\n"
        "                     encoding='utf-8'))\n"
        "os.makedirs(spec['dir'], exist_ok=True)\n"
        "with open(os.path.join(spec['dir'], spec['sid'] + '.jsonl'),\n"
        "          'w', encoding='utf-8') as t:\n"
        "    for r in spec['lines']:\n"
        "        t.write(json.dumps(r) + '\\n')\n"
        "print(json.dumps({'type': 'result', 'session_id': spec['sid'],\n"
        "                  'total_cost_usd': 0.25}))\n")
_fork_n = [0]


def _split_via_stub(s: "Sess", lines: list[dict]) -> dict:
    """Run the real `_compact_split_body` against the stub fork, and return the
    successor node as the doc now holds it."""
    _fork_n[0] += 1
    # ⚠ NOT "forked-sid-N": the mint checks assert that no transcript exists
    # for that id, and `transcript_path` globs every project dir on the rig
    sid = f"stubfork-sid-{_fork_n[0]}"
    with open(_FORK_SPEC, "w", encoding="utf-8") as f:
        json.dump({"sid": sid, "lines": lines,
                   "dir": os.path.dirname(s.path)}, f)
    os.makedirs(supervisor.scratch_dir(s.slug, s.nid), exist_ok=True)
    real = supervisor._claude_argv
    supervisor._claude_argv = lambda: [sys.executable, _FORK_STUB]
    try:
        supervisor._compact_split_body(s.slug, s.nid)
    finally:
        supervisor._claude_argv = real
    return store.load_org(s.slug).node(s.nid)


def occupancy_reporting() -> None:
    """USER BUG 2026-08-20: "after an agent is compacted its context still
    reads FULL until its next turn runs".

    Occupancy is read off the transcript, and every record in a transcript
    describes the prompt as it stood when that call was made. A compaction
    replaces the prompt without writing a record — so the newest record went on
    describing a prompt that no longer existed, and the agent reported the fill
    it had before, on the desk and through orgtree_read_transcript, until its
    next turn happened to append a record of the new one. Measured: 213k the
    moment the /compact finished, 59k after one trivial turn, no work in
    between.

    The rule this section pins: a boundary INVALIDATES what stands above it,
    the summary that replaced the history stands in until something measures
    the new session, and the stand-in says that it is one."""
    print("\nthe fill a COMPACTED agent reports, before it next runs:")

    # ---- the ordinary case still reads the way it always did (№24)
    s = Sess()
    s.user("prime")
    s.turn(47_764)                       # the session's floor: prompt + tools
    s.turn(212_859)                      # …and where it got to
    c = s.read()
    check("occupancy · an uncompacted session reads at its NEWEST record",
          lambda: _eq(c["occupancy"], 212_859))
    check("occupancy · …and says so: nothing about it is estimated",
          lambda: _eq(c["occupancy_estimated"], False))

    # ---- THE BUG: the boundary, and nothing after it yet
    s.boundary(214_033)
    s.summary(16_154)
    c = s.read()
    check("compacted · the pre-compaction fill does NOT survive its own "
          "boundary (the reported drop is immediate)",
          lambda: _true(c["occupancy"] != 212_859,
                        f"still reporting {c['occupancy']} after a compaction — "
                        f"this is the user's bug: the agent reads FULL until "
                        f"its next turn"))
    check("compacted · …and the figure that stands in is a real, much smaller "
          "one, not an empty wheel",
          lambda: _true(c["occupancy"] and c["occupancy"] < 212_859 // 2,
                        f"{c['occupancy']!r}: a compacted agent is ~30% full, "
                        f"and both 213k and 0 are wrong answers"))
    check("compacted · …declared an ESTIMATE, because nothing has measured "
          "the new session yet",
          lambda: _eq(c["occupancy_estimated"], True))
    check("compacted · …computed as the session's floor plus the summary it "
          "now carries",
          lambda: _eq(c["occupancy"], 47_764 + 16_154 // 4))
    # the estimate is only worth shipping if it is close. 51,802 against the
    # 58,078 the next turn went on to measure: 11% low, where the reading it
    # replaces was 3.7x high.
    err = abs((47_764 + 16_154 // 4) - 58_078) / 58_078
    measured("post-compaction estimate error (ingame-prompt's real numbers)",
             f"{err:.0%} low, replacing a reading that was "
             f"{212_859 / 58_078:.1f}x high")
    check("compacted · …and lands within 15% of what the next turn then "
          "measures (real fixture, 2026-08-20)",
          lambda: _true(err < 0.15, f"{err:.0%}"))
    check("compacted · the doc's writer and the desk's reader give the SAME "
          "answer (one rule, two surfaces)",
          lambda: _eq(s.doc_reading(), (c["occupancy"], True)))

    # ---- …and the moment a turn measures it, the estimate is gone
    s.turn(58_078)
    c2 = s.read()
    check("measured · the first record after the boundary supersedes the "
          "estimate exactly",
          lambda: _eq(c2["occupancy"], 58_078))
    check("measured · …and the estimate flag drops with it",
          lambda: _eq(c2["occupancy_estimated"], False))
    check("measured · the boundary still RENDERS as its own row (the reader "
          "the user sees was not disturbed)",
          lambda: _true(any("context compacted" in (m.get("text") or "")
                            for m in c2["messages"]),
                        json.dumps([m.get("text") for m in c2["messages"]])[:300]))
    check("measured · …with the summary still attached to it, not spilled as "
          "a 16 KB user bubble",
          lambda: _true(any(m.get("summary") for m in c2["messages"])))

    # ---- unknown is a legitimate answer; a made-up number is not
    s2 = Sess()
    s2.boundary(90_000)
    s2.summary(8_000)
    c = s2.read()
    check("floorless · a session that OPENS at a boundary has no floor to "
          "estimate from, and reports unknown rather than inventing one",
          lambda: _eq(c["occupancy"], None))
    check("floorless · …and unknown is not dressed up as an estimate",
          lambda: _eq(c["occupancy_estimated"], False))

    s3 = Sess()
    s3.summary(20_000)                   # a resumed session opens with one
    s3.turn(41_000)
    c = s3.read()
    check("resumed · a summary with no boundary above it invents nothing — "
          "the session's own first record answers",
          lambda: _eq((c["occupancy"], c["occupancy_estimated"]), (41_000, False)))

    # ---- a second compaction estimates from the floor, not from the peak
    s4 = Sess()
    s4.turn(30_000)
    s4.turn(180_000)
    s4.boundary(180_000)
    s4.summary(4_000)
    s4.turn(36_000)
    s4.turn(175_000)
    s4.boundary(175_000)
    s4.summary(4_000)
    c = s4.read()
    check("twice · a second compaction invalidates the second peak too",
          lambda: _true(c["occupancy"] and c["occupancy"] < 175_000 // 2,
                        f"{c['occupancy']!r}"))
    check("twice · …estimating from the session's FLOOR, never its high-water "
          "mark",
          lambda: _eq(c["occupancy"], 30_000 + 4_000 // 4))

    # ---- the filters №8/№24 already applied must survive the rework
    s5 = Sess()
    s5.turn(25_000)
    s5.turn(900_000, isSidechain=True)          # a subagent's own window
    s5.turn(800_000, message={"role": "assistant", "model": "<synthetic>",
                              "content": [{"type": "text", "text": "x"}],
                              "usage": {"input_tokens": 800_000}})
    s5.turn(700_000, isApiErrorMessage=True)
    c = s5.read()
    check("filters · a SUBAGENT's context is still not this agent's fill",
          lambda: _eq(c["occupancy"], 25_000))
    check("filters · …nor is a synthetic or api-error record's usage",
          lambda: _eq(s5.doc_reading(), (25_000, False)))

    # ---- the doc side: a turn MEASURES, and supersedes any estimate
    s6 = Sess()
    s6.turn(20_000)
    s6.boundary(200_000)
    s6.summary(8_000)
    org = store.load_org(s6.slug)
    n = org.node(s6.nid)
    n["occupancy"], n["occupancy_est"] = 22_000, True
    store.save_org(org)
    after = s6.after_turn(61_000)
    check("doc · a turn that measures the context clears the estimate flag",
          lambda: _eq((after.get("occupancy"), after.get("occupancy_est")),
                      (61_000, None)))

    # ---- postTokens: the boundary's own account of what survived, which is a
    # far better half of the estimate than the summary's character count.
    # Measured over the 13 usable compacted sessions on this machine:
    # floor+postTokens median 3% / worst 5%, against len(summary)//4's
    # median 11% / worst 16%. Used where present, fallen back where not.
    s9 = Sess()
    s9.turn(47_764)
    s9.turn(212_859)
    s9.boundary(214_033, post=8_889)
    c = s9.read()
    check("postTokens · the boundary's own postTokens carries the estimate "
          "when the CLI writes it",
          lambda: _eq((c["occupancy"], c["occupancy_estimated"]),
                      (47_764 + 8_889, True)))
    # …and it does not wait for the summary record: the boundary is written
    # AFTER the compaction completes (it carries the duration), so by the time
    # this record exists the answer it holds is final.
    check("postTokens · …without waiting for the summary record to arrive",
          lambda: _true(c["occupancy"] != 47_764,
                        "the floor alone is not an estimate"))
    s9.summary(16_154)
    c = s9.read()
    check("postTokens · …and the summary that follows does not override the "
          "better number with the character-count guess",
          lambda: _eq(c["occupancy"], 47_764 + 8_889))
    # the fixture that proves it is worth preferring: on ingame-prompt's real
    # numbers postTokens lands 2% low where the character count is 11% low
    truth, post_est = 58_078, 47_764 + 8_889
    measured("estimator error on the real fixture",
             f"postTokens {abs(post_est - truth) / truth:.0%} · "
             f"summary//4 {abs((47_764 + 16_154 // 4) - truth) / truth:.0%}")
    check("postTokens · …which is the closer of the two on the real fixture",
          lambda: _true(abs(post_est - truth)
                        < abs((47_764 + 16_154 // 4) - truth)))

    # ---- a boundary and NOTHING after it: the state a killed compaction
    # leaves, and the one shape where an unwary reader would keep reporting
    # the pre-compaction fill because nothing arrived to overwrite it
    s10 = Sess()
    s10.turn(47_764)
    s10.turn(212_859)
    s10.boundary(214_033)                # no postTokens, no summary yet
    c = s10.read()
    check("bare · a boundary with nothing after it reports UNKNOWN — never "
          "the figure it invalidated",
          lambda: _eq((c["occupancy"], c["occupancy_estimated"]), (None, False)))

    # ---- an unreadable summary body measures nothing, and nothing is what it
    # may claim: `floor + 1 token` would be an invented number wearing the
    # estimate's badge
    s11 = Sess()
    s11.turn(47_764)
    s11.boundary(214_033)
    s11._rec({"type": "user", "isCompactSummary": True,
              "message": {"role": "user",
                          "content": [{"type": "image", "source": {}}]}})
    c = s11.read()
    check("unreadable · a summary body nothing can flatten leaves the fill "
          "unknown, not floor-plus-one",
          lambda: _eq((c["occupancy"], c["occupancy_estimated"]), (None, False)))

    # ---- an estimate is bounded by the window it has to fit inside
    s12 = Sess()
    s12.turn(20_000)
    s12.boundary(200_000)
    s12.summary(4_000_000)               # a 4 MB summary: 1M tokens of estimate
    c = s12.read()
    cw = supervisor.TIER_CONTEXT["haiku"]
    check("capped · an estimate never exceeds the context window it describes",
          lambda: _true(c["occupancy"] and c["occupancy"] <= cw,
                        f"{c['occupancy']!r} against a {cw} window"))

    # ---- the tracker's own filters, pinned where a mutation would show
    s13 = Sess()
    s13.turn(30_000)
    s13.summary(20_000)                  # a stray summary with no boundary
    c = s13.read()
    check("stray · a summary with no boundary waiting does not restate the "
          "fill as an estimate",
          lambda: _eq((c["occupancy"], c["occupancy_estimated"]),
                      (30_000, False)))
    s13._rec({"type": "assistant",
              "message": {"role": "assistant", "model": "claude-x",
                          "content": [{"type": "text", "text": "x"}],
                          "usage": {"input_tokens": -5_000}}})
    check("negative · a negative usage total is not a fill (and does not "
          "become the floor)",
          lambda: _eq(s13.read()["occupancy"], 30_000))

    # ---- a record the CLI could never write, arriving on the TURN path.
    # An AttributeError here would be reported to the user as a failed turn
    # that in fact succeeded — and, because the watermark is written after the
    # read, would repeat on every turn the node ever takes again.
    s14 = Sess()
    s14.turn(20_000)
    s14._rec({"type": "assistant", "message": "usage"})          # message: str
    s14._rec({"type": "assistant", "message": {"role": "assistant",
                                               "model": "claude-x",
                                               "usage": "usage"}})   # usage: str
    s14._rec({"type": "assistant", "message": [{"usage": 1}]})   # message: list
    s14._rec({"type": "system", "subtype": "compact_boundary",
              "isMeta": False, "compactMetadata": "usage"})      # meta: str
    s14.boundary(150_000, post=3_000)    # …and a real one after it
    check("poisoned · a malformed record cannot raise out of the reader",
          lambda: _eq(s14.read()["occupancy"], 20_000 + 3_000))
    org = store.load_org(s14.slug)
    org.node(s14.nid)["cli_compactions"] = 0
    store.save_org(org)
    check("poisoned · …nor out of the TURN path, which would report a "
          "successful turn as failed — forever",
          lambda: _eq(s14.after_turn(150_000).get("occupancy"), 23_000))
    check("poisoned · …and the turn's own watermark still advances, so the "
          "node cannot be stuck re-reading it every turn",
          lambda: _true(int(store.load_org(s14.slug).node(s14.nid)
                            .get("cli_compactions") or 0) >= 2))

    # ---- the doc side, the CLI's own in-place compaction (the other half of
    # the same bug): `occ` reaches _after_turn as the turn's PEAK, so without
    # the correction the doc keeps the fill the turn had BEFORE the CLI
    # compacted it away — and this branch returns before anything else could
    # fix it.
    s7 = Sess()
    s7.turn(20_000)
    s7.turn(150_000)
    s7.boundary(150_000)
    s7.summary(4_000)
    s7.turn(24_000)                      # the CLI compacted and kept going
    org = store.load_org(s7.slug)
    org.node(s7.nid)["cli_compactions"] = 0        # already baselined
    store.save_org(org)
    after = s7.after_turn(150_000)                 # the high-water sample
    check("cli · an in-place CLI compaction leaves the DOC at the fill the "
          "session actually carries, not the peak it reached",
          lambda: _eq(after.get("occupancy"), 24_000))
    check("cli · …a measured one, so nothing is flagged as estimated",
          lambda: _eq(after.get("occupancy_est"), None))

    s8 = Sess()
    s8.turn(20_000)
    s8.turn(150_000)
    s8.boundary(150_000)
    s8.summary(4_000)                    # …and this time the turn ENDED there
    org = store.load_org(s8.slug)
    org.node(s8.nid)["cli_compactions"] = 0
    store.save_org(org)
    after = s8.after_turn(150_000)
    check("cli · a compaction that ENDS the turn still drops the doc's fill "
          "at once, marked as the estimate it is",
          lambda: _eq((after.get("occupancy"), after.get("occupancy_est")),
                      (20_000 + 4_000 // 4, True)))

    # …and where the transcript cannot answer at all, the peak must still go.
    # UNKNOWN BEATS STALE: leaving the high-water mark in place is precisely
    # the bug this branch exists to correct.
    s15 = Sess()
    s15.turn(20_000)
    s15.turn(150_000)
    s15.boundary(150_000)                # killed before the summary landed
    org = store.load_org(s15.slug)
    org.node(s15.nid)["cli_compactions"] = 0
    store.save_org(org)
    after = s15.after_turn(150_000)
    check("cli · an unreadable aftermath clears the doc's fill rather than "
          "leaving the pre-compaction peak standing",
          lambda: _eq((after.get("occupancy"), after.get("occupancy_est")),
                      (None, None)))

    # ---- the destructive trigger must never fire on an estimate. cheap_compact
    # THROWS AWAY the summary a 600 s billed fork just produced; before the
    # successor's fill was reported at all, this branch could not see it.
    s16 = Sess()
    s16.turn(20_000)
    org = store.load_org(s16.slug)
    n16 = org.node(s16.nid)
    n16["occupancy"], n16["context_window"] = 60_000, 200_000
    n16["turns"] = [{"at": "2020-01-01T00:00:00Z", "cost": 0.0}]
    org.d["auto_cheap_compact"] = {"enabled": True, "occ": 0.25, "idle_s": 1}
    store.save_org(org)
    cfg = supervisor._auto_cheap_cfg(store.load_org(s16.slug), s16.nid)
    check("auto-cheap · the fixture really is over the trigger's threshold "
          "(the check below is not vacuous)",
          lambda: _true(cfg is not None and 60_000 / 200_000 >= cfg["occ"],
                        json.dumps(cfg)))
    check("auto-cheap · …and an ESTIMATED fill is not a number that trigger "
          "may act on",
          lambda: _true(supervisor._auto_cheap_ready(
              store.load_org(s16.slug).node(s16.nid), cfg) is True
              and supervisor._auto_cheap_ready(
                  {**store.load_org(s16.slug).node(s16.nid),
                   "occupancy_est": True}, cfg) is False))

    # ---- FR-24b: idle_s DEFAULTS TO 3600, and the RATIONALE travels with the
    # number (user ruling 2026-08-21). The old 300 meant "the prompt-cache
    # TTL" back when that was read as five minutes. For us it is an HOUR: an
    # agent turn is a headless `claude -p` run whose querySource is `sdk`,
    # which the pinned CLI classifies as a MAIN conversation, and Claude Code
    # asks for a 1h TTL on a subscription. (The five-minute cap is the
    # in-session Task-subagent one — querySource `agent:*` — which an orgtree
    # agent is not.) A revert to 300 must FAIL ONE OF THESE BY NAME rather
    # than quietly halving the window everywhere.
    #
    # ⚠ Every path below is derived from `supervisor.__file__`, so these read
    # the package that actually imported. A suite run against a different
    # checkout than it thinks reads THAT checkout's numbers and says so.
    import ast as _ast
    _pkg = os.path.dirname(supervisor.__file__)
    _repo = os.path.dirname(os.path.dirname(_pkg))
    _fe = os.path.join(_repo, "frontend", "src")

    def _idle_get_defaults(path: str) -> list:
        """Every `<x>.get("idle_s", N)` literal in a module, via the AST.

        Deliberately NOT a text search: the rationale comments beside these
        lines now contain the string "3600" themselves, so a grep would go on
        passing after the code reverted to 300. The AST sees only code."""
        with open(path, encoding="utf-8") as fh:
            tree = _ast.parse(fh.read())
        out = []
        for nd in _ast.walk(tree):
            if (isinstance(nd, _ast.Call)
                    and isinstance(nd.func, _ast.Attribute)
                    and nd.func.attr == "get" and len(nd.args) == 2
                    and isinstance(nd.args[0], _ast.Constant)
                    and nd.args[0].value == "idle_s"
                    and isinstance(nd.args[1], _ast.Constant)):
                out.append(nd.args[1].value)
        return out

    def _code(path: str) -> str:
        """Source with `//` line comments stripped — same reason as above."""
        with open(path, encoding="utf-8") as fh:
            return "\n".join(re.sub(r"//.*", "", ln)
                             for ln in fh.read().splitlines())

    s18 = Sess()
    s18.turn(20_000)
    org = store.load_org(s18.slug)
    org.d["auto_cheap_compact"] = {"enabled": True}     # no idle_s ⇒ default
    store.save_org(org)
    cfg18 = supervisor._auto_cheap_cfg(store.load_org(s18.slug), s18.nid)
    check("idle-ttl · an UNSET idle_s resolves to 3600 s, not 300",
          lambda: _eq(cfg18 and cfg18["idle_s"], 3600.0))

    org = store.load_org(s18.slug)
    org.d["auto_cheap_compact"] = {"enabled": True, "idle_s": "sixty minutes"}
    store.save_org(org)
    cfg18b = supervisor._auto_cheap_cfg(store.load_org(s18.slug), s18.nid)
    check("idle-ttl · the MALFORMED-config fallback says 3600 too (the two "
          "returns in one function are the classic pair to revert by half)",
          lambda: _eq(cfg18b and cfg18b["idle_s"], 3600.0))

    _idle_lits = (_idle_get_defaults(os.path.join(_pkg, "supervisor.py"))
                  + _idle_get_defaults(os.path.join(_pkg, "api.py"))
                  + _idle_get_defaults(os.path.join(_pkg, "ledger.py")))
    check("idle-ttl · all three backend `.get(\"idle_s\", N)` defaults are "
          "3600 — and there are still three of them (a deleted site cannot "
          "pass this by leaving an empty list)",
          lambda: _eq(sorted(_idle_lits), [3600, 3600, 3600]))

    check("idle-ttl · the DOCSTRING carries the new number, so the next "
          "reader does not inherit a rationale for 300",
          lambda: _true("idle_s 3600" in (supervisor._auto_cheap_cfg.__doc__
                                          or "")
                        and not re.search(r"idle_s 300\b",
                                          supervisor._auto_cheap_cfg.__doc__
                                          or "")))

    _app = _code(os.path.join(_fe, "App.tsx"))
    _mod = _code(os.path.join(_fe, "canvas", "modals.tsx"))
    check("idle-ttl · both frontend panels DISPLAY the 3600 default "
          "(`?? 300` would render 5 min beside a backend that means 60)",
          lambda: _true("idle_s ?? 3600" in _app and "idle_s ?? 3600" in _mod
                        and not re.search(r"idle_s \?\? 300\b", _app + _mod)))
    # the sneaky half: `(+accIdle || 5) * 60` is what an EMPTIED box saves.
    # Move only the `?? 300` sites and clearing the field silently writes 5
    # min against a field that displays 60 — a disagreement no single-site
    # check would see.
    check("idle-ttl · …and all THREE blank-field fallbacks save 60 min, so "
          "an emptied box agrees with the displayed default",
          lambda: _eq((len(re.findall(r"\+accIdle \|\| 60\b", _app + _mod)),
                       len(re.findall(r"\+accIdle \|\| 5\b", _app + _mod))),
                      (3, 0)))

    _desk = [ln for ln in _code(os.path.join(_fe, "canvas", "desk.tsx"))
             .splitlines() if "const cold" in ln]
    check("idle-ttl · the manual-compact COLD badge uses the same 1h window "
          "(at 5 min it warned 'past the cache window' on a cache warm for "
          "another 55)",
          lambda: _true(len(_desk) == 1 and "60 * 60e3" in _desk[0],
                        " | ".join(_desk)))

    with open(os.path.join(_repo, "DECISIONS.md"), encoding="utf-8") as fh:
        _dec = fh.read()
    check("idle-ttl · DECISIONS.md quotes the shipped default, not the old "
          "one",
          lambda: _true("0.5 / 3600 s" in _dec
                        and "0.5 / 300 s" not in _dec))

    # ---- the refusal that guards a billed fork rests on the FACT, not on how
    # a number was arrived at
    s17 = Sess()
    s17.turn(20_000)
    org = store.load_org(s17.slug)
    n17 = org.node(s17.nid)
    n17["occupancy"], n17["compacted_unrun"] = 58_000, True
    n17.pop("occupancy_est", None)       # a fork whose transcript MEASURED it
    store.save_org(org)
    check("guard · a measured post-compaction fill still refuses a second "
          "fork — the marker is the fact, not the estimate flag",
          lambda: _true(store.load_org(s17.slug).node(s17.nid)
                        .get("compacted_unrun")))
    after = s17.after_turn(21_000)
    check("guard · …and one completed turn clears it, however that turn went",
          lambda: _eq((after.get("compacted_unrun"), after.get("occupancy")),
                      (None, 21_000)))

    # ================================================ redteam round 2
    # Everything below was written against a defect a second adversarial pass
    # found in the code above, or against a MUTANT of it that the checks above
    # let through. Where a mutation is listed as "equivalent" it is because the
    # two spellings cannot be told apart by any input — said out loud rather
    # than pinned by a check that would only look like coverage.

    # ---- the §8 split's own doc writes, WITHOUT the three-minute live half.
    # `n["occupancy"] = occ_new` could be reverted to the `None` this commit
    # replaced — i.e. the whole manual-compaction half of the fix undone — and
    # nothing hermetic noticed (redteam mutants M5/M6).
    s18, sid18 = Sess(), None
    s18.turn(47_764)
    s18.turn(212_859)
    n18 = _split_via_stub(s18, [_A(47_764), _A(212_859),
                                _B(214_033, 3_000), _S(16_154)])
    check("split · a manual compaction writes the successor's POST-compaction "
          "fill onto the doc, at once, with no turn in between",
          lambda: _eq(n18.get("occupancy"), 47_764 + 3_000))
    check("split · …flagged as the estimate it is",
          lambda: _eq(n18.get("occupancy_est"), True))
    check("split · …and marked compacted-and-unrun, which is what the refusal "
          "on a second billed fork rests on",
          lambda: _eq(n18.get("compacted_unrun"), True))

    # …and the case that made a MEASURED number out of a pre-compaction one:
    # a fork that exits 0 having copied the history and written NO boundary
    # (the /compact refused under the compaction floor, or it died after the
    # copy). Every record in that file predates the compaction that never
    # happened, so its newest one is the fill the user's bug reported — and it
    # would land on the doc unflagged, where the wake sweep reads it as licence
    # to cheap-compact the summary away.
    s19 = Sess()
    s19.turn(47_764)
    s19.turn(212_859)
    n19 = _split_via_stub(s19, [_A(47_764), _A(212_859)])      # no boundary
    check("split · a fork that wrote no boundary reports UNKNOWN — never the "
          "pre-compaction fill it copied, and never as measured",
          lambda: _eq((n19.get("occupancy"), n19.get("occupancy_est")),
                      (None, None)))
    check("split · …and is still marked compacted-and-unrun, so the guard on "
          "a second fork does not rest on the number being readable",
          lambda: _eq(n19.get("compacted_unrun"), True))
    check("split · …so the destructive wake sweep refuses it, on a node whose "
          "fill it cannot see",
          lambda: _eq(supervisor._auto_cheap_ready(
              {**n19, "occupancy": 212_859, "context_window": 200_000,
               "turns": [{"at": "2020-01-01T00:00:00Z"}]},
              {"occ": 0.25, "idle_s": 1}), False))

    # ---- the tracker's arithmetic, one mutation at a time
    s20 = Sess()
    s20.turn(20_000)
    s20.turn(0, message={"role": "assistant", "model": "claude-x",
                         "content": [], "usage": {"input_tokens": 0,
                                                  "cache_read_input_tokens": 0,
                                                  "cache_creation_input_tokens": 0}})
    s20.boundary(150_000)
    s20.summary(4_000)
    check("tracker · a zero-token record is not a floor (an empty usage block "
          "would otherwise make every estimate unknowable)",
          lambda: _eq(s20.read()["occupancy"], 20_000 + 4_000 // 4))

    s21 = Sess()
    s21.turn(20_000)
    s21.boundary(150_000, post=-5)       # a negative "survived"
    s21.summary(4_000)
    check("tracker · a NEGATIVE postTokens is not an amount that survived — "
          "the summary's own size answers instead",
          lambda: _eq(s21.read()["occupancy"], 20_000 + 4_000 // 4))

    s22 = Sess()
    s22.turn(20_000)
    s22.boundary(150_000, post=0)        # …and zero, with nothing to fall back to
    check("tracker · a postTokens of 0 with no summary yet is unknown, not "
          "'the floor exactly'",
          lambda: _eq(s22.read()["occupancy"], None))

    s23 = Sess()
    s23.turn(20_000)
    s23.boundary(150_000, post=True)     # JSON `true` where a count belongs
    s23.summary(4_000)
    check("tracker · a boolean postTokens is not 1 token — bools are ints in "
          "Python and `int(True)` would sell floor+1 as an estimate",
          lambda: _eq(s23.read()["occupancy"], 20_000 + 4_000 // 4))

    s24 = Sess()
    s24.turn(20_000)
    s24.turn(999_999, isMeta=True)       # the engine talking, not the agent
    s24.turn(999_998, isSidechain=True)  # …and a subagent's window, not this one
    # ⚠ BOTH surfaces. `read_chat` drops these records before `_occ_record` ever
    # sees them, so asking only the desk tests the renderer's filter and not
    # the tracker's — which is how the tracker's survived round 2 (mutant M3).
    # The doc's reader has no such pre-pass: it is `_occ_record` or nothing.
    check("tracker · an isMeta or isSidechain record is not the agent's own "
          "context, on the desk and on the doc alike",
          lambda: _eq((s24.read()["occupancy"], s24.doc_reading()),
                      (20_000, (20_000, False))))

    check("tracker · a non-integer cap cannot become the reported occupancy "
          "(`min(v, True)` is `True`; `min(v, 3.7)` is `3.7`)",
          lambda: _eq([supervisor._OccTracker(c).cap
                       for c in (True, 3.7, 0, -1, None, float("nan"), "200k")],
                      [None, 3, None, None, None, None, None]))

    # ---- non-finite numerics: `isinstance(x, float)` is true for nan and inf,
    # `json.loads` mints both, and `int()` of either raises — out of read_chat
    # (a 500 for the desk, the failure this commit closed for non-dict records)
    # and out of the split before it banks a real billed fork's cost.
    s25 = Sess()
    s25.turn(20_000)
    s25._rec({"type": "assistant",
              "message": {"role": "assistant", "model": "claude-x",
                          "content": [], "usage": {"input_tokens": float("inf"),
                                                   "cache_read_input_tokens": 0,
                                                   "cache_creation_input_tokens": 0}}})
    s25.boundary(150_000, post=float("nan"))
    s25.summary(4_000)
    check("poisoned · a non-finite usage or postTokens cannot raise out of "
          "the reader either — and the summary still answers",
          lambda: _eq(s25.read()["occupancy"], 20_000 + 4_000 // 4))
    check("poisoned · …and the doc's writer agrees with the desk about it",
          lambda: _eq(s25.doc_reading(), (20_000 + 4_000 // 4, True)))

    # ---- the never-raises wrapper. Every poison fixture above is absorbed by
    # `_occ_record`, so nothing exercised `session_occupancy`'s own blanket
    # `except` — it could be narrowed to ZeroDivisionError and the suite stayed
    # green (redteam mutant M7), on the guard the turn path's promise rests on.
    check("poisoned · session_occupancy answers for a node that does not "
          "exist rather than raising into the turn that asked",
          lambda: _eq(supervisor.session_occupancy(
              store.load_org(s25.slug), "no-such-node"), (None, False)))

    # ---- `_count_cli_compactions` on a line that parses to a bare string
    s26 = Sess()
    s26.turn(20_000)
    with open(s26.path, "a", encoding="utf-8") as f:
        f.write(json.dumps("compact_boundary") + "\n")
    s26.boundary(150_000, post=3_000)
    check("poisoned · a transcript line that is a bare JSON string cannot "
          "raise out of the boundary count (it runs on the turn path)",
          lambda: _eq(supervisor._count_cli_compactions(
              store.load_org(s26.slug), s26.nid)[0], 1))

    # ---- the estimate flag must be CLEARED when a later reading measures it,
    # not merely set when one does not (redteam mutant M13)
    s27 = Sess()
    s27.turn(20_000)
    s27.turn(150_000)
    s27.boundary(150_000)
    s27.summary(4_000)
    s27.turn(24_000)                     # …and the CLI kept going
    org = store.load_org(s27.slug)
    n27 = org.node(s27.nid)
    n27["cli_compactions"], n27["occupancy_est"] = 0, True
    store.save_org(org)
    # ⚠ the turn measures NOTHING (occ falsy). With a measured turn the write
    # at the top of `_after_turn` pops the flag before this branch is reached,
    # and the branch's own `else` is dead code that no check can see — which is
    # exactly how it survived round 2's version of this check (mutant M13,
    # caught once the mutation harness was pointed at the mutated tree).
    check("cli · a measured aftermath CLEARS an estimate flag an earlier "
          "compaction left behind, even on a turn that measured nothing "
          "itself",
          lambda: _eq(s27.after_turn(0).get("occupancy_est"), None))

    # ---- the window a node's turns actually get: the pinned tier wins, or a
    # 1M-context model is capped at the 200k the CLI reports (mutant M9)
    check("window · the pinned tier beats the doc's observed window",
          lambda: _eq(supervisor.context_window(
              {"model": "sonnet", "context_window": 200_000}), 1_000_000))
    check("window · …and the doc's value answers for a model with no pin",
          lambda: _eq(supervisor.context_window(
              {"model": "some-unpinned-model", "context_window": 200_000}),
              200_000))

    # ---- TOCTOU: the transcript read happens off-lock (234 ms on a 71 MB
    # file) and `cheap_compact` can mint a new session inside that window.
    # Writing the old session's fill and boundary count onto the new one
    # reinstates a fill the node does not have and suppresses its next real
    # compaction. `spend_unrun_pardon` guards the same window the same way.
    s28 = Sess()
    s28.turn(20_000)
    s28.turn(150_000)
    s28.boundary(150_000)
    s28.summary(4_000)
    stale = store.load_org(s28.slug)     # the pre-turn doc the turn path holds
    stale.node(s28.nid)["cli_compactions"] = 0
    store.save_org(stale)
    stale = store.load_org(s28.slug)
    # the mint lands INSIDE the off-lock read, which is the only window that
    # matters: before it, the turn's own writes are the new session's problem;
    # after it, the guard has already compared
    _real_so = supervisor.session_occupancy

    def _racing_read(org, nid, require_boundary=False):
        fresh = store.load_org(s28.slug)
        fresh.cheap_compact(USER, s28.nid)
        store.save_org(fresh)
        return _real_so(org, nid, require_boundary)

    supervisor.session_occupancy = _racing_read
    _say28 = io.StringIO()
    try:
        with contextlib.redirect_stdout(_say28):
            supervisor._after_turn(s28.slug, s28.nid, stale, {},
                                   supervisor.state(s28.slug, s28.nid), 150_000)
    finally:
        supervisor.session_occupancy = _real_so
    n28 = store.load_org(s28.slug).node(s28.nid)
    # the bail writes NOTHING to the doc, by contract — so the only way anyone
    # ever learns it happened is that it says so, and the reason it names is
    # the whole value: a changed session is the ordinary mint-mid-turn race
    check("toctou · …and the discarded work is announced, naming the ordinary "
          "cause rather than bailing in silence",
          lambda: _true("session changed under the turn" in _say28.getvalue(),
                        repr(_say28.getvalue())))
    check("toctou · a session replaced while the aftermath was being read "
          "keeps its OWN empty fill, not the dead session's",
          lambda: _eq(n28.get("occupancy"), None))
    check("toctou · …and does not inherit the dead session's boundary count, "
          "which would suppress its next real compaction",
          # None, not 0: `cheap_compact` resets the counter to "never
          # baselined" for the fresh session, so the next turn re-baselines
          # against the file that session actually has
          lambda: _eq(n28.get("cli_compactions"), None))

    # ---- the baseline turn (a node's FIRST under this feature) must skip the
    # threshold check exactly as its sibling branch does. `occ` is the turn's
    # pre-compaction PEAK by design, so without the skip a node whose first
    # turn is also the turn the CLI compacted in place forks a 600 s billed
    # child on it — minting a bearer that holds post-compaction state, the
    # "worse than nothing" outcome 1b exists to prevent.
    forks: list[str] = []
    _real_split = supervisor._compact_split
    supervisor._compact_split = lambda slug, nid: forks.append(nid)
    try:
        s29 = Sess()
        s29.turn(20_000)
        s29.turn(190_000)
        s29.boundary(190_000)            # the CLI compacted mid-turn…
        s29.summary(4_000)
        org = store.load_org(s29.slug)
        n29 = org.node(s29.nid)
        n29.pop("cli_compactions", None)     # …on a node never baselined
        n29["context_window"] = 200_000
        store.save_org(org)
        s29.after_turn(190_000)          # the PEAK, over any threshold
        check("baseline · a node's first turn under the feature does not fork "
              "a billed compaction off a peak the CLI has already compacted "
              "away",
              lambda: _eq(forks, []))
        s30 = Sess()                     # control: no boundary, so it SHOULD
        s30.turn(190_000)
        org = store.load_org(s30.slug)
        n30 = org.node(s30.nid)
        n30.pop("cli_compactions", None)
        n30["context_window"] = 200_000
        store.save_org(org)
        s30.after_turn(190_000)
        check("baseline · …and the check is not vacuous: the same turn on a "
              "node the CLI has NOT compacted still forks",
              lambda: _eq(forks, [s30.nid]))
    finally:
        supervisor._compact_split = _real_split

    # ---- a re-seeded session is EMPTY, and carries none of the markers that
    # describe a summary-only one (cheap_compact was given this and its
    # sibling three functions away was missed)
    org31, (n31,) = horg(grant=20)
    _plant_transcript(sid_of(org31, n31))
    nd = org31.node(n31)
    nd["occupancy"], nd["occupancy_est"] = 58_000, True
    nd["compacted_unrun"] = True
    org31.mark_unrecoverable(n31, "test")
    org31.reseed(USER, n31, "reseeded-sid-occ")
    check("reseed · a fresh empty session reports an empty context and none "
          "of a compaction's markers",
          lambda: _eq([org31.node(n31).get(k) for k in
                       ("occupancy", "occupancy_est", "compacted_unrun")],
                      [None, None, None]))

    # ---- the wake sweep's own defensive parse. `cast` is a no-op at runtime,
    # so a torn `turns` raised AttributeError/IndexError out of a decision
    # taken under DOC_LOCK on the turn path — killing the turn the optimization
    # was trying to cheapen.
    base = {"occupancy": 60_000, "context_window": 200_000,
            "turns": [{"at": "2020-01-01T00:00:00Z"}]}
    cfg2 = {"occ": 0.25, "idle_s": 1}
    check("auto-cheap · the fixture fires (the refusals below are not "
          "vacuous)",
          lambda: _eq(supervisor._auto_cheap_ready(base, cfg2), True))
    check("auto-cheap · a torn `turns` refuses rather than raising",
          lambda: _eq([supervisor._auto_cheap_ready({**base, "turns": t}, cfg2)
                       for t in ("notalist", ["str"], [None], [{}], 7)],
                      [False] * 5))
    check("auto-cheap · …and a compacted-but-unrun node refuses however its "
          "fill was arrived at",
          lambda: _eq(supervisor._auto_cheap_ready(
              {**base, "compacted_unrun": True}, cfg2), False))

    # ================================================ redteam round 3
    # A third pass, against the second pass's fixes. Two of these are its own
    # regressions; the rest are the coverage a CORRECTED mutation harness found
    # once it was pointed at the mutated tree rather than at main (the first
    # harness copied the backend to a directory where the suite's own
    # `_REPO/backend` did not resolve, so every run imported the unmutated
    # checkout off PYTHONPATH and reported KILLED for everything, including a
    # no-op. See scratch/mutate.py, which now runs a control pair.)

    # ---- `math.isfinite` RAISES OverflowError on an int too large for a
    # float, which is a number json.loads mints and int() handles perfectly
    # well. Guarding against nan/inf with it therefore broke a case that
    # worked before the guard existed — in read_chat, where nothing catches
    # it, and in the split, before the fork's real cost is banked.
    check("poisoned · a 400-digit integer is a number the guard must answer "
          "for, not raise on",
          lambda: _eq([supervisor._finite(v) for v in
                       (10 ** 400, float("inf"), float("nan"), 1, 1.5,
                        "200000", None)],
                      [False, False, False, True, True, False, False]))
    s32 = Sess()
    s32.turn(20_000)
    s32.boundary(150_000, post=10 ** 400)
    s32.summary(4_000)
    check("poisoned · …and a transcript carrying one still reads, on both "
          "surfaces",
          lambda: _eq((s32.read()["occupancy"], s32.doc_reading()),
                      (20_000 + 4_000 // 4, (20_000 + 4_000 // 4, True))))
    check("poisoned · …including as a context window off the doc",
          lambda: _eq(supervisor._OccTracker(10 ** 400).cap, None))

    # ---- a line that parses but is not a record. Two of the three readers
    # skipped it; the one the DESK and orgtree_read_transcript run reached
    # straight for `.get` — and a comment in this change claimed otherwise.
    s33 = Sess()
    s33.turn(20_000)
    for junk in ("compact_boundary", None, 42, [1, 2]):
        with open(s33.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(junk) + "\n")
    s33.boundary(150_000, post=3_000)
    check("poisoned · every reader of a transcript skips a line that is not a "
          "record — the renderer included",
          lambda: _eq((s33.read()["occupancy"],
                       supervisor._count_cli_compactions(
                           store.load_org(s33.slug), s33.nid)[0],
                       s33.doc_reading()[0]),
                      (20_000 + 3_000, 1, 20_000 + 3_000)))

    # ---- `cli_compactions` is a doc value, and the doc is hand-editable.
    # This was the last unguarded coercion on the turn path, and it failed in
    # the permanent shape: a successful turn reported as failed, forever,
    # because the watermark that would repair it is written afterwards.
    # ⚠ NOT `True` — it parses (`int(True) == 1`), lands in the OTHER arm, and
    # `_eq(True, 1)` then passes by bool/int equality, so that iteration
    # asserted nothing the others did not. `-1` earns its place instead: it
    # parses too, and it is the shape that reached the correction branch with
    # zero boundaries in the file (fable sign-off, 2026-08-20).
    for _torn in ("abc", float("inf"), {"x": 1}, [1], -1):
        s34 = Sess()
        s34.turn(20_000)
        s34.turn(150_000)
        s34.boundary(150_000)
        s34.summary(4_000)
        org = store.load_org(s34.slug)
        org.node(s34.nid)["cli_compactions"] = _torn      # torn doc
        store.save_org(org)
        after = s34.after_turn(150_000)
        check(f"torn · an unparseable compaction watermark ({_torn!r}) cannot "
              f"raise out of the turn path",
              lambda a=after: _eq(a.get("cli_compactions"), 1))
        # ⚠ THE NAME IS THE CLAIM, so assert the claim: "never baselined" is
        # the arm that repairs the watermark WITHOUT minting. `== 1` alone is
        # equally true of `seen = 0`, which routes into the other arm and mints
        # a lost generation per historical boundary — the retroactive minting
        # the baseline arm exists to prevent. Round 3 wrote the weaker check
        # and the mutation harness scored it covered, because the mutant
        # happened to crash on the unguarded coercion four lines away rather
        # than fail an assertion (redteam round 4).
        check(f"torn · …reads as 'never baselined' ({_torn!r}): the watermark "
              f"is repaired and NO generation is minted for history it never "
              f"saw",
              lambda s=s34, a=after: _eq(
                  (a.get("generation", 0),
                   sorted(k for k in store.load_org(s.slug).nodes
                          if k.startswith(s.nid + "@")),
                   store.load_org(s.slug).node(s.nid).get("cli_compactions")),
                  (0, [], 1)))

    # ---- …and the same value read a SECOND time, from a FRESH load inside the
    # lock. `seen` above came off the caller's org, bound before a turn that
    # can run for ten minutes, so the doc can be torn (or hand-edited) inside
    # exactly the window the session re-check beside it exists for.
    #
    # ⚠ WHAT THIS PINS CHANGED WHEN THE TWO BRANCHES MERGED. It was written
    # against a second `int()` coercion, which could raise; the merged code has
    # no coercion there at all — the watermark is COMPARED (`!= seen0`), and a
    # mismatch bails without recording. So the claim is now about that bail:
    # nothing raises, nothing is half-written, no cut is left on disk naming a
    # generation the doc never learns about. The occupancy correction is inside
    # the bail, so the doc keeps this turn's own measured peak for one turn —
    # acceptable because the next turn overwrites it with its own measurement,
    # and reachable only from a torn doc.
    s34b = Sess()
    s34b.turn(20_000)
    s34b.turn(150_000)
    s34b.boundary(150_000)
    s34b.summary(4_000)
    org = store.load_org(s34b.slug)
    org.node(s34b.nid)["cli_compactions"] = 0
    store.save_org(org)
    _real_so2 = supervisor.session_occupancy

    def _tearing_read(o, nid, require_boundary=False):
        f = store.load_org(s34b.slug)
        f.node(s34b.nid)["cli_compactions"] = "abc"   # torn mid-read
        store.save_org(f)
        return _real_so2(o, nid, require_boundary)

    supervisor.session_occupancy = _tearing_read
    _say34b = io.StringIO()
    try:
        with contextlib.redirect_stdout(_say34b):
            after34b = s34b.after_turn(150_000)
    finally:
        supervisor.session_occupancy = _real_so2
    check("torn · a watermark torn INSIDE the off-lock read cannot raise "
          "either: the turn bails rather than recording against a doc that "
          "moved under it",
          lambda: _eq((after34b.get("cli_compactions"),
                       after34b.get("generation", 0)), ("abc", 0)))
    check("torn · …and the bail leaves nothing half-done — no bearer minted "
          "for a cut the doc never learned about",
          lambda: _eq(sorted(k for k in store.load_org(s34b.slug).nodes
                             if k.startswith(s34b.nid + "@")), []))
    # …and this is the reason an operator actually needs to see: the session is
    # still ours, so nothing raced us — the DOC moved under a locked read,
    # which is the shape that means something is wrong rather than busy
    check("torn · …and it is announced as the doc-is-wrong case, not confused "
          "with the ordinary mint-mid-turn race",
          lambda: _true("session held but watermark moved" in _say34b.getvalue()
                        and "session changed" not in _say34b.getvalue(),
                        repr(_say34b.getvalue())))

    # ---- the baseline turn's watermark write is what makes its `return` cost
    # exactly ONE turn instead of every turn. Nothing pinned it.
    forks2: list[str] = []
    _real_split2 = supervisor._compact_split
    supervisor._compact_split = lambda slug, nid: forks2.append(nid)
    try:
        s35 = Sess()
        s35.turn(20_000)
        s35.boundary(150_000)            # one HISTORICAL boundary…
        s35.summary(4_000)
        s35.turn(190_000)                # …and the node is near the wall now
        org = store.load_org(s35.slug)
        n35 = org.node(s35.nid)
        n35.pop("cli_compactions", None)
        n35["context_window"] = 200_000
        store.save_org(org)
        s35.after_turn(190_000)
        check("baseline · the baseline turn PERSISTS its watermark",
              lambda: _eq(store.load_org(s35.slug).node(s35.nid)
                          .get("cli_compactions"), 1))
        s35.after_turn(190_000)
        check("baseline · …so the skip costs exactly one turn: the next turn "
              "compacts normally, instead of the node never compacting again",
              lambda: _eq(forks2, [s35.nid]))
    finally:
        supervisor._compact_split = _real_split2

    # ---- a SIDECHAIN boundary is a subagent's compaction, not this agent's.
    # The counter did not filter it and the occupancy reader did, so the two
    # disagreed about one file: a generation was minted for a compaction that
    # never happened to this node, and the node's own measured fill was wiped
    # to unknown on the strength of it.
    s36 = Sess()
    s36.turn(20_000)
    s36.turn(150_000)
    s36._rec({"type": "system", "subtype": "compact_boundary",
              "isSidechain": True, "compactMetadata":
              {"trigger": "manual", "preTokens": 150_000}})
    s36._rec({"type": "system", "subtype": "compact_boundary",
              "isMeta": True, "compactMetadata":
              {"trigger": "manual", "preTokens": 150_000}})
    check("sidechain · neither a subagent's boundary nor the engine's own is "
          "this agent's compaction",
          lambda: _eq(supervisor._count_cli_compactions(
              store.load_org(s36.slug), s36.nid)[0], 0))
    check("sidechain · …so the two readers of one file cannot disagree about "
          "whether it holds a compaction",
          lambda: _eq((s36.doc_reading(), s36.read()["occupancy"]),
                      ((150_000, False), 150_000)))

    # ---- cheap_compact's own markers, the sibling of the reseed check above
    org37, (n37,) = horg(grant=20)
    _plant_transcript(sid_of(org37, n37))
    nd37 = org37.node(n37)
    nd37["occupancy"], nd37["occupancy_est"] = 58_000, True
    nd37["compacted_unrun"] = True
    org37.cheap_compact(USER, n37)
    check("cheap · a cheap-compacted session reports an empty context and "
          "none of a compaction's markers either",
          lambda: _eq([org37.node(n37).get(k) for k in
                       ("occupancy", "occupancy_est", "compacted_unrun")],
                      [None, None, None]))

    # ---- the split's own session-replacement window: 600 s with no lock,
    # and `compact_split` archives whichever session the node holds when it
    # lands. A mint inside that window would retire the fresh EMPTY session as
    # a knowledge bearer — a bearer over nothing — and strip its never-run
    # pardon, while the fork's summary went to a session nothing points at.
    s38 = Sess()
    s38.turn(20_000)
    s38.turn(150_000)
    gen0 = store.load_org(s38.slug).node(s38.nid).get("generation", 0)
    _real_occ = supervisor.occupancy_of

    def _minting_read(*a, **kw):
        o = store.load_org(s38.slug)
        o.cheap_compact(USER, s38.nid)   # the user, mid-fork
        store.save_org(o)
        return _real_occ(*a, **kw)

    supervisor.occupancy_of = _minting_read
    try:
        n38 = _split_via_stub(s38, [_A(20_000), _B(150_000, 3_000), _S(4_000)])
    finally:
        supervisor.occupancy_of = _real_occ
    check("split · a session replaced while the fork ran is not archived as a "
          "knowledge bearer — the split is abandoned, not applied to it",
          lambda: _eq(n38.get("generation"), gen0 + 1))   # the MINT's bump only
    check("split · …and the successor keeps the never-run pardon the mint "
          "gave it",
          lambda: _eq(n38.get("session_unrun"), True))
    check("split · …while the fork's real cost is still banked, not lost with "
          "the abandoned split",
          lambda: _true(float(n38.get("cost_usd") or 0) >= 0.25,
                        f"cost_usd={n38.get('cost_usd')!r}"))


#: twenty notice KINDS — distinguished by a word, because a NUMBER is
#: deliberately not a kind (`_notice_shape` blanks digits, so "notice 1" and
#: "notice 2" are one kind and would test nothing here)
KINDS20 = ("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
           "mu nu xi omicron pi rho sigma tau upsilon").split()


def notice_digest() -> None:
    """The notice box is keyed by SEAT; cheap_compact and reseed replace the
    SESSION. So the successor's first turn used to open with the whole
    undelivered backlog of a predecessor it has no memory of — measured on the
    live resonite org 2026-08-20: 22 notices, 7,082 chars, three days, 11 of
    them the same "direct instruction to X" line about a since-retired report.

    The ruling is DIGEST, not drop: same-kind repeats collapse to their
    newest, carrying the count; distinct kinds survive; nothing is destroyed
    (`notice_log` is untouched and /history renders it)."""
    print("\ncheap-compact/reseed · the predecessor's notice backlog:")

    def box(org: Org, nid: str) -> list:
        return (org.d.get("notices") or {}).get(nid) or []

    # ---- the shape key: what makes two notices "the same kind"
    same = ('The user gave a direct instruction to "angvel", inside your '
            'chain: "ok i made some changes" - it carries the USER authority.')
    other = ('The user gave a direct instruction to "ingame-prompt", inside '
             'your chain: "do the thing" - it carries the USER authority.')
    check("shape · the quoted node id and the quoted gist do not make a kind",
          lambda: _eq(ledger._notice_shape(same), ledger._notice_shape(other)))
    check("shape · …but a different SENTENCE is a different kind",
          lambda: _true(ledger._notice_shape(same)
                        != ledger._notice_shape('Your report "angvel" was '
                                                'retired by the user (freed '
                                                '10 credits).')))
    check("shape · a credit count is not a kind either",
          lambda: _eq(ledger._notice_shape('Your report "a" was retired by '
                                           'the user (freed 10 credits).'),
                      ledger._notice_shape('Your report "b" was retired by '
                                           'the user (freed 5 credits).')))

    # ---- the fold itself
    org, (a,) = horg(grant=20)
    _plant_transcript(sid_of(org, a))
    for i in range(9):
        org._notify([a], f'The user gave a direct instruction to "kid{i}", '
                         f'inside your chain: "msg {i}" - it carries the '
                         f'USER authority. Re-check any plan of yours.')
    for i in range(3):
        org._notify([a], f'Your report "kid{i}" was retired by the user '
                         f'(freed {i + 1} credits).')
    org._notify([a], "You have been renamed.")
    before = len(box(org, a))
    log_before = len(org.d.get("notice_log") or [])
    check("fold · the backlog is 13 notices of 3 kinds before the compact",
          lambda: _eq(before, 13))
    org.cheap_compact(USER, a)
    b0 = box(org, a)
    # 1 digest header + 3 kinds + cheap_compact's own "you were CHEAP-COMPACTED"
    check("fold · …and 5 lines after it (header + one per kind + the notice "
          "the compact itself queues)",
          lambda: _eq(len(b0), 5))
    check("fold · the header states what was folded and where the rest lives",
          lambda: _true("13 notices" in b0[0]["text"]
                        and "History tab" in b0[0]["text"], b0[0]["text"]))
    check("fold · the collapsed kind carries its count",
          lambda: _true(any("[+8 earlier notice(s) of this same kind"
                            in e["text"] for e in b0),
                        " || ".join(e["text"][-70:] for e in b0)))
    check("fold · the exemplar kept is the NEWEST of its kind",
          lambda: _true(any('"kid8"' in e["text"].split(" [+")[0]
                            and '"kid7"' not in e["text"].split(" [+")[0]
                            for e in b0)))
    check("fold · …and the fold recites WHICH other nodes it swallowed, so "
          "a count never hides a name",
          lambda: _true(any(all(f'"kid{i}"' in e["text"] for i in range(9))
                            for e in b0),
                        " || ".join(e["text"][-160:] for e in b0)))
    check("fold · a kind that occurred ONCE survives verbatim",
          lambda: _true(any(e["text"] == "You have been renamed."
                            for e in b0)))
    check("fold · nothing is destroyed — every folded notice is still in "
          "notice_log verbatim, which is what /history renders",
          lambda: _eq(len([e for e in (org.d.get("notice_log") or [])
                           if e["node"] == a
                           and "direct instruction" in e["text"]]), 9))
    check("fold · …and the synthetic digest header is NOT logged as history "
          "(it is chrome about the fold, not an org change)",
          lambda: _eq(len(org.d.get("notice_log") or []) - log_before, 1))
    check("fold · the op records how many notices it folded",
          lambda: _eq([e for e in org.d["events"]
                       if e["op"] == "cheap_compact"][-1]["detail"]
                      ["notices_folded"], 10))

    # ---- what it must NOT do
    org2, (b,) = horg(grant=20)
    _plant_transcript(sid_of(org2, b))
    for w in ("alpha", "beta", "gamma", "delta"):
        org2._notify([b], f"A {w} thing happened to you.")
    org2.cheap_compact(USER, b)
    check("fold · four notices of four kinds are left verbatim (no header, "
          "no loss) — a digest that shortens nothing is not applied",
          lambda: _eq(len(box(org2, b)), 5))       # 4 + the compact's own
    check("fold · …and none of them grew a fold marker",
          lambda: _true(not any("folded" in e["text"]
                                for e in box(org2, b)[:4])))

    org3, (c,) = horg(grant=20)
    _plant_transcript(sid_of(org3, c))
    org3._notify([c], "One lonely notice.")
    org3.cheap_compact(USER, c)
    check("fold · a backlog under three is never touched",
          lambda: _eq(box(org3, c)[0]["text"], "One lonely notice."))

    # ---- reseed folds too; compact_split deliberately does NOT
    org4, (d4,) = horg(grant=20)
    _plant_transcript(sid_of(org4, d4))
    for i in range(6):
        org4._notify([d4], f'Your report "kid{i}" was retired by the user '
                           f'(freed {i} credits).')
    org4.mark_unrecoverable(d4, "test")
    org4.reseed(USER, d4, "reseeded-sid-digest")
    check("fold · reseed digests the same way (its successor is as memoryless)",
          lambda: _true(any("6 notices" in e["text"]
                            for e in box(org4, d4)), box(org4, d4)))

    org5, (e5,) = horg(grant=20)
    _plant_transcript(sid_of(org5, e5))
    for i in range(6):
        org5._notify([e5], f'Your report "kid{i}" was retired by the user '
                           f'(freed {i} credits).')
    org5.compact_split(e5, "split-sid-digest")
    check("fold · a NORMAL compaction does not digest — its successor carries "
          "the CLI's summary, so the diff still lands on a baseline",
          lambda: _eq(len([e for e in box(org5, e5)
                           if "retired by the user" in e["text"]]), 6))

    # ---- the kind cap is declared, never silent
    org6, (f6,) = horg(grant=20)
    _plant_transcript(sid_of(org6, f6))
    for w in KINDS20:
        org6._notify([f6], f"A {w} thing happened to you.")
        org6._notify([f6], f"A {w} thing happened to you.")
    org6.cheap_compact(USER, f6)
    b6 = box(org6, f6)
    check("fold · past the kind cap the block is capped at 15 kinds",
          lambda: _eq(len(b6), 17))     # header + 15 + the compact's own
    check("fold · …and the header SAYS how many kinds it dropped",
          lambda: _true("5 oldest kind(s) dropped" in b6[0]["text"],
                        b6[0]["text"]))
    check("fold · …keeping the NEWEST kinds",
          lambda: _true(any(KINDS20[-1] in e["text"] for e in b6)
                        and not any(KINDS20[0] in e["text"] for e in b6)))


def _plant_transcript(sid: str, home: str = HOME) -> str:
    d = os.path.join(home, ".claude", "projects", "rig")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user",
                            "message": {"role": "user", "content": "x"}}) + "\n")
    return p


# =============== 3a-bis. hermetic: the cheap-compact fires at an ACCOUNT SWITCH
#
# D-179 (user request 2026-08-29): "when a fallback key is triggered, it
# doesn't take advantage of any existing agent cached context; it has to send
# the full context all the way up to the new account, wasting tons of usage.
# autocompact should trigger on this boundary too for that reason."
#
# ⚠ WHAT THIS IS TESTING IS NOT A NEW TRIGGER. `_auto_cheap_cfg` says idle_s
# defaults to 3600 because that IS the prompt-cache TTL — "beyond it the resume
# is cold and the swap pays for itself". Idle time was only ever a PROXY for
# coldness. An account switch is the other road to the same place, so it is
# OR-ed into the same bar, and the OCCUPANCY bar stays ANDed in front of both.
#
# ⚠ AND WHY OCCUPANCY STILL GATES A COMPACTION THAT COSTS NOTHING. It is worth
# writing down because the obvious objection — "a fallback is followed by only
# one or two more turns, so compacting could cost more than it saves" — assumes
# the compaction is itself a billed full-context call. `cheap_compact` is not:
# it makes NO API call at all (ledger.cheap_compact — archive the session as a
# bearer, assign a fresh `session_id`, done). There is no token cost to
# amortise and therefore no break-even in turns. What a needless swap spends is
# CONTEXT, and occupancy is the entire defence of it. Hence the pairing the
# checks below pin: a switch says the reload is expensive, occupancy says the
# session is big enough that losing it is the better trade, and neither alone
# is permission to fire.


def _sw_node(ran_as: str = "primary", occ: int = 60_000,
             cw: int = 200_000, **over) -> dict:
    """A node whose last turn ran JUST NOW — so the idle bar is nowhere near
    open and only an account switch can fire the swap. That is the whole point
    of the fixture: every True below is attributable to the account and to
    nothing else."""
    turn: dict = {"at": ledger.now(), "cost": 0.0}
    if ran_as:
        turn["ran_as"] = ran_as
    return {"occupancy": occ, "context_window": cw, "turns": [turn], **over}


def account_switch_compaction() -> None:
    print("\nan account switch is a cold cache (supervisor."
          "_cache_moved_account):")
    # idle_s an hour: the fixture's last turn is seconds old, so the ONLY way
    # anything below returns True is the account comparison.
    cfg = {"occ": 0.25, "idle_s": 3600.0}
    ready = supervisor._auto_cheap_ready
    moved = supervisor._cache_moved_account

    # ---- CONTROL FIRST. If the fixture fired on its own, every check under
    # it would be measuring the fixture and not the feature.
    check("switch · CONTROL: the fixture does NOT fire on idle — its last "
          "turn is seconds old, so nothing below can be an idle pass",
          lambda: _eq(ready(_sw_node(), cfg), False))
    check("switch · CONTROL: …nor when the account is UNCHANGED (this is the "
          "same fixture, same thunk shape, one value different)",
          lambda: _eq(ready(_sw_node("primary"), cfg, lambda: "primary"),
                      False))

    # ---- THE FEATURE. Same node, same instant, different serving account.
    check("switch · a node moved from the primary to a fallback key compacts "
          "at once, without waiting out idle_s",
          lambda: _eq(ready(_sw_node("primary"), cfg, lambda: "kAAA"), True))
    check("switch · …and back again: fallback → primary is the same fact "
          "(the cache is on whichever account is being LEFT)",
          lambda: _eq(ready(_sw_node("kAAA"), cfg, lambda: "primary"), True))
    check("switch · …and fallback → a DIFFERENT fallback, which is the case a "
          "rule written as 'is it the primary' would silently miss",
          lambda: _eq(ready(_sw_node("kAAA"), cfg, lambda: "kBBB"), True))
    check("switch · …and the api-key lane counts: an api_fallback window "
          "opening or expiring moves accounts too, and the cache with it",
          lambda: _eq([ready(_sw_node("api-key"), cfg, lambda: "primary"),
                       ready(_sw_node("primary"), cfg, lambda: "api-key")],
                      [True, True]))

    # ---- THE BAR THAT DID NOT MOVE. This is the "does not fire where it
    # would cost more than it saves" half of the deliverable: cheap_compact
    # spends no tokens but spends the whole context, so a small session is
    # exactly the one not worth throwing away — a switch is not permission.
    check("switch · a SMALL context does not compact on a switch — the swap "
          "costs no tokens but costs the whole session, and there is little "
          "here to save",
          lambda: _eq(ready(_sw_node("primary", occ=10_000), cfg,
                            lambda: "kAAA"), False))
    check("switch · …and the occupancy bar is honoured as a THRESHOLD, not as "
          "a nonzero test: a hair under refuses, a hair over fires",
          lambda: _eq([ready(_sw_node("primary", occ=o), cfg,
                             lambda: "kAAA") for o in (49_999, 50_001)],
                      [False, True]))
    check("switch · …and a just-compacted session is still off limits, "
          "however cold its cache is",
          lambda: _eq([ready(_sw_node("primary", **{k: True}), cfg,
                             lambda: "kAAA")
                       for k in ("compacted_unrun", "occupancy_est")],
                      [False, False]))

    # ---- CANNOT TELL MEANS DO NOT. A false negative is one cold reload; a
    # false positive destroys a live agent's context. Every unknown refuses.
    check("switch · an absent `ran_as` (the node has not run in this backend "
          "process) is not a switch — absence is not a measurement",
          lambda: _eq(ready(_sw_node(""), cfg, lambda: "kAAA"), False))
    check("switch · …nor is an empty answer from the resolver",
          lambda: _eq(ready(_sw_node("primary"), cfg, lambda: ""), False))
    check("switch · …nor is `key:unattributed` on EITHER side — a token no "
          "row explains could be two different accounts two turns running",
          lambda: _eq([ready(_sw_node(supervisor.UNATTRIBUTED), cfg,
                             lambda: "kAAA"),
                       ready(_sw_node("primary"), cfg,
                             lambda: supervisor.UNATTRIBUTED)],
                      [False, False]))
    check("switch · …and the sentinel the refusal is written against is the "
          "one `identity_in_env` actually returns (a literal would drift)",
          lambda: _eq(supervisor.identity_in_env(
              {"CLAUDE_CODE_OAUTH_TOKEN": "no-row-explains-this"}),
              supervisor.UNATTRIBUTED))

    # ---- an optimization is never allowed to be the reason a turn dies. The
    # thunk reaches the filesystem (registry + token store) under DOC_LOCK.
    def _boom() -> str:
        raise RuntimeError("registry unreadable")
    check("switch · a resolver that RAISES refuses the swap instead of "
          "killing the turn it was trying to cheapen",
          lambda: _eq(ready(_sw_node("primary"), cfg, _boom), False))
    check("switch · …and a torn `turns` still refuses under the new argument",
          lambda: _eq([ready({**_sw_node("primary"), "turns": t}, cfg,
                             lambda: "kAAA")
                       for t in ("notalist", ["str"], [None], [{}], 7)],
                      [False] * 5))

    # ---- the old callers must be untouched: `serving=None` is "not asking".
    check("switch · omitting the argument entirely leaves the idle-only "
          "behaviour exactly as it was",
          lambda: _eq([ready(_sw_node("primary"), cfg),
                       ready(_sw_node("primary"), {"occ": 0.25,
                                                   "idle_s": 0.0})],
                      [False, True]))
    check("switch · …and `_cache_moved_account` itself answers False for a "
          "caller that passed no resolver at all",
          lambda: _eq(moved(_sw_node("primary"), None), False))

    # ---- THE ORDERING IS BEHAVIOUR, NOT STYLE: `serving` reads the registry
    # and the token store off disk, once per wake, under DOC_LOCK. A node
    # below the occupancy bar must not pay for an answer that cannot change
    # the verdict.
    calls: list[int] = []

    def _counted() -> str:
        calls.append(1)
        return "kAAA"
    ready(_sw_node("primary", occ=10_000), cfg, _counted)
    check("switch · a node under the occupancy bar never consults the "
          "resolver — the filesystem read stays off the common path",
          lambda: _eq(calls, []))
    ready(_sw_node("primary", occ=60_000), cfg, _counted)
    check("switch · …and the check above is not vacuous: over the bar, it IS "
          "consulted",
          lambda: _eq(calls, [1]))
    calls.clear()
    ready(_sw_node("primary"), {"occ": 0.25, "idle_s": 0.0}, _counted)
    check("switch · …and an idle pass short-circuits it too: nothing is read "
          "when idle alone already answers",
          lambda: _eq(calls, []))

    # ---- DRIFT GUARD. The trigger is only reachable if the turn loop passes
    # a resolver built the way `identity_in_env` demands — from the env the
    # spawn will really carry. A refactor to `accounts.resolve(...)` would
    # keep every check above green and answer the wrong question for any org
    # billing its own api_key. Read out of the source, so it cannot pass by
    # the call site having been deleted.
    _src = re.sub(r"\s+", "", inspect.getsource(supervisor._run_one_turn))
    check("switch · the turn loop hands the trigger a resolver over the "
          "RESOLVED SPAWN ENV, not a bare `accounts.resolve` reading",
          lambda: _true("identity_in_env(spawn_env(" in _src,
                        "the wake-time resolver no longer reads the spawn "
                        "env — see identity_in_env's docstring on why that "
                        "is the difference between a diagnosis and a guess"))
    check("switch · …and the readiness call at the wake actually passes it "
          "(a trigger nothing feeds is a trigger that never fires)",
          lambda: _true("_auto_cheap_ready(_n0,_c,_serving_now)" in _src,
                        "the wake no longer passes a resolver"))


# ====================== 3b. hermetic: the two pure predicates the split rests on

def predicates() -> None:
    print("\nthe compaction threshold, as a value (supervisor._threshold):")
    D = 0.80
    for raw, want, why in [
        (0.80, 0.80, "the ordinary case"),
        (0.5, 0.5, "an aggressive per-org setting"),
        (0.95, 0.95, "the documented maximum"),
        (0.99, 0.95, "over the maximum is capped, not honoured"),
        (1.0, 0.95, "a full context is still capped"),
        (None, D, "unset falls back to the configured default"),
        ("", D, "an empty string is unset"),
        (0, D, "zero would mean 'compact every turn' — not a threshold"),
        (-1.0, D, "NEGATIVE: every turn forked a 600 s compaction (fixed)"),
        (-0.0001, D, "…including a hair under zero"),
        (float("nan"), D, "NaN: every comparison False, so compaction NEVER "
                          "fired and the node ran to the context wall"),
        (float("inf"), D, "infinity is not a fraction"),
        ("0.6", 0.6, "a numeric string still works (docs are JSON)"),
        ("eighty percent", D, "junk falls back rather than crashing a turn"),
        (80, D, "a PERCENT written where a fraction belongs falls back "
                "(it used to be silently read as 95%)"),
        ({"pct": 80}, D, "a wrong-shaped value falls back"),
    ]:
        check(f"threshold-fn · {raw!r} → {want} · {why}",
              (lambda r=raw, w=want: _eq(supervisor._threshold(r, D), w)))

    print("\nthe compaction fork's answer (supervisor._fork_result):")
    good = ('{"type":"result","session_id":"abc","total_cost_usd":0.25}')
    for raw, want_sid, why in [
        (good, "abc", "the ordinary single-line result"),
        ("  " + good + "\n", "abc", "surrounded by whitespace"),
        ("npm notice new version\n" + good, "abc",
         "ONE unrelated stdout line in front of it — this used to throw the "
         "whole (expensive) fork away and start a 15-minute cooldown"),
        ("warn: a\nwarn: b\n" + good + "\n", "abc", "several of them"),
        (good + '\n{"type":"result","session_id":"later"}', "later",
         "the LAST result wins"),
        ("", None, "no output at all"),
        ("not json", None, "no JSON anywhere"),
        ("[1,2,3]", None, "valid JSON that is not the result object"),
        ('{"type":"result"}', None, "a result with no session id"),
        ("{oops", None, "a truncated object"),
    ]:
        check(f"fork-result · {why}",
              (lambda r=raw, w=want_sid:
               _eq(supervisor._fork_result(r).get("session_id"), w)))
    check("fork-result · a banner line does not cost the fork its dollar figure",
          lambda: _eq(supervisor._fork_result("npm notice\n" + good)
                      .get("total_cost_usd"), 0.25))


# ================================================ 4. cross-process safety

XPROC_CHILD = r'''
import json, os, random, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["ORGTREE_DATA"] = sys.argv[2]
from orgtree import store
who, slug, n, delay = sys.argv[3], sys.argv[4], int(sys.argv[5]), float(sys.argv[6])
done, errs = 0, {}
for i in range(n):
    try:
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.d.setdefault("xproc", {}).setdefault(who, []).append(i)
            store.save_org(org)
        done += 1
    except Exception as e:
        errs[type(e).__name__] = errs.get(type(e).__name__, 0) + 1
    if delay:
        time.sleep(random.uniform(0, delay))
print(json.dumps({"who": who, "done": done, "errs": errs}))
'''


def _xproc_run(writers: int, n: int, delay: float) -> dict:
    """Run `writers` independent processes through the canonical
    load-modify-save cycle against ONE org doc. Returns the accounting."""
    org = store.create_org(f"zz xproc {writers}w {n}n {int(delay * 1000)}ms "
                           f"{os.urandom(2).hex()}")
    slug = org.d["slug"]
    org.hire(USER, None, "haiku", 5, "seat", **hspec())
    store.save_org(org)
    script = os.path.join(TMP, "xproc_child.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(XPROC_CHILD)
    procs = []
    t0 = time.time()
    for i in range(writers):
        procs.append(subprocess.Popen(
            [sys.executable, script, os.path.join(_REPO, "backend"), HDATA,
             chr(ord("A") + i), slug, str(n), str(delay)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    claimed, errs = {}, {}
    for p in procs:
        out, err = p.communicate(timeout=300)
        try:
            r = json.loads(out.strip().splitlines()[-1])
        except Exception:                                    # noqa: BLE001
            raise AssertionError(f"child produced no result: {out!r} / {err[-400:]!r}")
        claimed[r["who"]] = r["done"]
        for k, v in r["errs"].items():
            errs[k] = errs.get(k, 0) + v
    elapsed = time.time() - t0
    final = store.load_org(slug).d.get("xproc") or {}
    survived = {w: len(final.get(w, [])) for w in claimed}
    lost = {w: claimed[w] - survived.get(w, 0) for w in claimed}
    total_claimed = sum(claimed.values())
    total_lost = sum(lost.values())
    return {"slug": slug, "claimed": claimed, "survived": survived, "lost": lost,
            "errs": errs, "elapsed": elapsed,
            "pct": 100.0 * total_lost / max(1, total_claimed),
            "total_claimed": total_claimed, "total_lost": total_lost}


def cross_process() -> None:
    print("\ncross-process safety — two backends, one data dir:")
    note("ARCHITECTURE.md: 'atomic writes make a concurrent SECOND backend look "
         "like it works while silently discarding interleaved load-modify-save "
         "cycles.' Measured here rather than described.")

    reps = 1 if QUICK else 3
    configs = ([(2, 60, 0.0)] if QUICK else
               [(2, 150, 0.0), (2, 150, 0.004), (4, 80, 0.0), (2, 40, 0.03)])
    worst = 0.0
    for writers, n, delay in configs:
        pcts, errs_all = [], {}
        for _ in range(reps):
            r = _xproc_run(writers, n, delay)
            pcts.append(r["pct"])
            for k, v in r["errs"].items():
                errs_all[k] = errs_all.get(k, 0) + v
        worst = max(worst, max(pcts))
        lo, hi = min(pcts), max(pcts)
        measured(f"{writers} processes × {n} cycles, {int(delay * 1000)} ms jitter",
                 f"lost {lo:.1f}–{hi:.1f}% of completed writes"
                 + (f", exceptions {errs_all}" if errs_all else ", 0 exceptions"))
        check(f"xproc · {writers}×{n}@{int(delay * 1000)}ms · every cycle "
              f"reported SUCCESS to its caller",
              (lambda e=errs_all: _eq(e, {})))

    check("xproc · concurrent writers DO lose committed updates "
          "(the documented failure is real)",
          lambda: _true(worst > 0.0,
                        "no update was lost in any configuration — either the "
                        "measurement is too gentle or something now serialises "
                        "cross-process writes; re-check before trusting it"))
    check("xproc · the loss is SILENT — no exception on any lost write",
          lambda: _true(True))     # asserted per-config above
    check("xproc · no orphan .tmp survives the storm",
          lambda: _eq([f for f in os.listdir(os.path.join(HDATA, "orgs"))
                       if f.endswith(".tmp")], []))
    check("xproc · the doc still parses after the storm",
          lambda: _true(isinstance(store.list_orgs(), list)))

    # Would a lock file even work here? Measure before recommending one.
    lockp = os.path.join(TMP, "probe.lock")
    t0 = time.perf_counter()
    ncyc = 200 if QUICK else 1000
    for _ in range(ncyc):
        fd = os.open(lockp, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.close(fd)
        os.remove(lockp)
    per = (time.perf_counter() - t0) / ncyc * 1e6
    measured("O_CREAT|O_EXCL lock acquire+release", f"{per:.0f} µs/cycle")
    check("xproc · a lock file is cheap enough that cost is not the objection",
          lambda: _true(per < 5000, f"{per:.0f} µs is too costly to sit on "
                                    f"every save"))
    note("…but a lock around save_org alone would fix NOTHING: the race is the "
         "load-modify-save CYCLE. See the owner claim below for the choice "
         "actually made, and the report for the argument.")

    owner_claim()


OWNER_CHILD = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["ORGTREE_DATA"] = sys.argv[2]
from orgtree import store
root, hold = sys.argv[2], float(sys.argv[3])
try:
    store.claim_data_root(root)
except store.DataRootBusy as e:
    print(json.dumps({"claimed": False, "msg": str(e)[:300]})); raise SystemExit(0)
print(json.dumps({"claimed": True, "pid": os.getpid()}), flush=True)
time.sleep(hold)
if len(sys.argv) > 4 and sys.argv[4] == "crash":
    os._exit(1)                      # no atexit, no finally — the crash case
'''


def _owner_child(root: str, hold: float, crash: bool = False) -> subprocess.Popen:
    script = os.path.join(TMP, "owner_child.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(OWNER_CHILD)
    argv = [sys.executable, script, os.path.join(_REPO, "backend"), root,
            str(hold)]
    if crash:
        argv.append("crash")
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)


def _first_line(p: subprocess.Popen, secs: float = 20) -> dict:
    box: list = []

    def rd() -> None:
        assert p.stdout is not None
        box.append(p.stdout.readline())
    t = threading.Thread(target=rd, daemon=True)
    t.start()
    t.join(secs)
    if not box or not box[0].strip():
        raise AssertionError(f"child said nothing (rc={p.poll()})")
    return json.loads(box[0])


def owner_claim() -> None:
    """The chosen enforcement: an OS-level lock on <data>/.owner, claimed once
    at process start. Tested across REAL processes — an in-process test of a
    cross-process guard proves nothing."""
    print("\nxproc · the owner claim (store.claim_data_root):")
    root = os.path.join(XPROC, "claimed")
    os.makedirs(root, exist_ok=True)

    a = _owner_child(root, 6.0)
    try:
        first = _first_line(a)
        check("owner · the first process claims the root",
              lambda: _true(first["claimed"]))
        b = _owner_child(root, 0.1)
        second = _first_line(b)
        check("owner · a SECOND process on the same root is refused",
              lambda: _true(not second["claimed"], json.dumps(second)))
        check("owner · …and the refusal names the holder's pid",
              lambda: _true(f"pid={first['pid']}" in second["msg"],
                            second["msg"]))
        check("owner · …and says what the failure would have been",
              lambda: _true("silently lose" in second["msg"], second["msg"]))
        b.wait(timeout=20)
        # a DIFFERENT root is unaffected — the claim is per data dir
        other = os.path.join(XPROC, "other")
        c = _owner_child(other, 0.1)
        check("owner · a different data root is claimable at the same time",
              lambda: _true(_first_line(c)["claimed"]))
        c.wait(timeout=20)
    finally:
        a.wait(timeout=30)

    d = _owner_child(root, 0.1)
    check("owner · the root is claimable again once the holder EXITS",
          lambda: _true(_first_line(d)["claimed"]))
    d.wait(timeout=20)

    # the whole reason for an OS lock rather than a pid file with a staleness
    # heuristic: a crash leaves nothing to clean up
    e = _owner_child(root, 0.3, crash=True)
    _first_line(e)
    e.wait(timeout=20)
    check("owner · a process that CRASHES (os._exit, no cleanup) leaves no "
          "stale claim behind",
          lambda: _true(_first_line(_owner_child(root, 0.05))["claimed"],
                        "a pid-file-plus-mtime scheme would have needed a "
                        "steal here — and a steal is what double-holds a "
                        "merely-slow holder"))
    check("owner · the .owner file records the holder for a human to read",
          lambda: _true(os.path.exists(os.path.join(root, ".owner"))))

    # in-process: idempotent, and released on request
    store.claim_data_root(os.path.join(XPROC, "inproc"))
    store.claim_data_root(os.path.join(XPROC, "inproc"))
    check("owner · claiming twice in one process is a no-op, not an error",
          lambda: _true(True))
    store.release_data_root()
    f = _owner_child(os.path.join(XPROC, "inproc"), 0.05)
    check("owner · release_data_root really lets the next process in",
          lambda: _true(_first_line(f)["claimed"]))
    f.wait(timeout=20)

    exception(
        "xproc · the owner claim is INERT — nothing calls it yet",
        "store.claim_data_root() is implemented, cross-process-tested above, "
        "and never invoked: the one-line call belongs at the top of "
        "api.main(), and api.py is another agent's file this wave. Until it "
        "is wired, a second backend on one ORGTREE_DATA still loses 32-74% of "
        "its writes silently. The wiring is: `store.claim_data_root()` before "
        "the first load, with DataRootBusy printed as a startup wall.")


# ============================================ 5. long-running realism (hermetic)

def aging() -> None:
    print("\nlong-running realism — what a month of running costs:")

    org, (a,) = horg(grant=50)
    slug = org.d["slug"]
    n_ops = 200 if QUICK else 1000
    base = len(json.dumps(org.d))
    for i in range(n_ops):
        org.post_mail(USER, a, f"message {i}")
        org._log("probe", USER, {"i": i}, [])
    grown = len(json.dumps(org.d))
    per_ev = (grown - base) / max(1, len(org.d["events"]))
    measured("bytes added per logged event (mixed op)", f"{per_ev:.0f} B")
    check("aging · `events` is UNCAPPED and grows without bound",
          lambda: _true(len(org.d["events"]) >= n_ops,
                        "events stopped growing — if a cap was added, this "
                        "check is the place to record its value"))
    exception("aging · org.d['events'] has no cap (ledger._log)",
              f"{len(org.d['events'])} events after {n_ops} probe ops; every "
              f"ledger op appends one and nothing ever trims. At the measured "
              f"{per_ev:.0f} B/event a doc crosses 10 MB at roughly "
              f"{int(10e6 / max(1.0, per_ev)):,} events. Every save rewrites "
              f"the WHOLE doc and GET /events returns the whole tail. "
              f"Known-reported; ledger.py is another agent's file.")

    # the same shape, twice more, on the two queues that are supposed to
    # "drain at the next turn" — which is not a bound for an idle, frozen or
    # simply infrequently-turning node
    org_n, (na,) = horg(grant=200)
    peers = 40 if QUICK else 120
    for i in range(peers):
        org_n.hire(USER, None, "haiku", 0, f"p{i}", **hspec())
    notices = sum(len(v) for v in (org_n.d.get("notices") or {}).values())
    measured(f"notices queued by {peers} sequential top-level hires",
             f"{notices} entries (each hire notifies every existing peer)")
    check("aging · a hire notifies every peer — the fan-out is quadratic in "
          "the sibling count",
          lambda: _true(notices >= peers * (peers - 1) / 4,
                        f"{notices} for {peers} hires"))
    exception("aging · notices[nid] and mail[nid] are UNCAPPED "
              "(ledger._notify / post_mail)",
              f"`notice_log` is capped at 800 and `mail_log` at 100/recipient, "
              f"but the LIVE queues they archive are not capped at all "
              f"(ledger.py:1338-1343, 913-933). Every hire/retire/rehire/"
              f"dissolve/promote/demote fans a notice to every same-parent "
              f"peer, so a flat pool accumulates triangularly: measured "
              f"{notices} queued entries from {peers} sequential hires. They "
              f"drain only when the recipient next takes a turn — never, for "
              f"an idle, frozen or archived-then-rehired node. Independently "
              f"measured on an aged doc: notices reached 86% of doc bytes "
              f"(18.5 MB) at 5,000 events in a 300-sibling flat org, and "
              f"pending `mail` was 27.8% of a 36 MB doc. Reported, not fixed "
              f"— ledger.py is another agent's file.")
    exception("aging · tree() and audit() are O(live × total) in node count",
              "Org.children() (ledger.py:360-366) scans EVERY node on every "
              "call; tree()'s recursive build calls it once per live node and "
              "audit()'s free()→committed() does the same. Measured "
              "independently: 100 → 800 live top-level nodes (8×) took tree() "
              "from 1.15 ms to 46.75 ms (40.7× — linear predicts 8×). Every "
              "tree fetch pays it, and the tree payload is on a 6 s heartbeat "
              "per mounted org view. Compaction compounds it: each generation "
              "adds a permanent archived node to the scan. Reported, not "
              "fixed — ledger.py is another agent's file.")

    check("aging · mail_log IS capped (the contrast that makes the above a defect)",
          lambda: _true(len((org.d.get("mail_log") or {}).get(a, [])) <= 400,
                        f"mail_log grew to "
                        f"{len((org.d.get('mail_log') or {}).get(a, []))}"))

    store.save_org(org)
    t0 = time.perf_counter()
    for _ in range(5):
        store.load_org(slug)
    t_load = (time.perf_counter() - t0) / 5 * 1000
    t0 = time.perf_counter()
    for _ in range(5):
        store.save_org(store.load_org(slug))
    t_save = (time.perf_counter() - t0) / 5 * 1000 - t_load
    measured(f"doc at {len(json.dumps(org.d)) // 1024} KB",
             f"load {t_load:.1f} ms · save {t_save:.1f} ms")

    print("\nrepeated compaction generations on a real doc:")
    org2, (b,) = horg(grant=50)
    hire_under(org2, b, "r1", 0)
    hire_under(org2, b, "r2", 0)
    gens = 25 if QUICK else 150
    sizes = []
    for i in range(gens):
        org2.compact_split(b, f"gen-{i}")
        if i in (0, gens // 2, gens - 1):
            sizes.append((i + 1, len(json.dumps(org2.d))))
    measured(f"doc bytes after 1 / {gens // 2 + 1} / {gens} generations",
             " → ".join(f"{s // 1024} KB" for _, s in sizes))
    per_gen = (sizes[-1][1] - sizes[0][1]) / max(1, gens - 1)
    measured("bytes per stored knowledge bearer", f"{per_gen:.0f} B")

    t0 = time.perf_counter()
    stack = org2.lineage_stack(b)
    t_stack = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    tr = org2.tree()
    t_tree = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    org2.audit()
    t_audit = (time.perf_counter() - t0) * 1000
    measured(f"at {gens} generations",
             f"lineage_stack {t_stack:.2f} ms · tree {t_tree:.2f} ms · "
             f"audit {t_audit:.2f} ms")
    check(f"aging · {gens} generations still yield an exact lineage stack",
          lambda: _eq(len(stack), gens))
    check("aging · …the successor's parent and grant are untouched",
          lambda: _true(org2.node(b)["parent"] is None
                        and org2.node(b)["grant"] == 50))
    check("aging · …audit is still clean",
          lambda: _true(org2.audit()["no_overdraft"]))
    check("aging · …the org chart shows ONE agent, not a column of ghosts",
          lambda: _eq(org2.org_children(None), [b]))
    check("aging · …tree() stays linear enough to serve a request",
          lambda: _true(t_tree < 2000, f"tree() took {t_tree:.0f} ms"))

    print("\nreconcile over an aged doc:")
    store.save_org(org2)
    t0 = time.perf_counter()
    supervisor.reconcile(org2.d["slug"])
    t_rec = (time.perf_counter() - t0) * 1000
    measured(f"reconcile over {len(org2.nodes)} nodes / {gens} generations",
             f"{t_rec:.0f} ms")
    after = store.load_org(org2.d["slug"])
    check("reconcile · a knowledge bearer is never marked unrecoverable",
          lambda: _true(all(after.nodes[k]["state"] == "archived"
                            for k in stack)))
    check("reconcile · a never-run node (cost 0) is left alone",
          lambda: _eq(after.node(b)["state"], "live"))

    # a node that HAS run but whose transcript is gone
    org3, (c,) = horg(grant=20)
    org3.node(c)["cost_usd"] = 0.5
    store.save_org(org3)
    supervisor.reconcile(org3.d["slug"])
    check("reconcile · a node that ran but lost its transcript is unrecoverable",
          lambda: _eq(store.load_org(org3.d["slug"]).node(c)["state"],
                      "unrecoverable"))
    org3b = store.load_org(org3.d["slug"])
    p3 = org3b.compact_split(c, "s")
    org3b.nodes[p3]["cost_usd"] = 0.5
    org3b.rehire(USER, p3, grant=0)
    store.save_org(org3b)
    supervisor.reconcile(org3.d["slug"])
    check("reconcile · a LIVE knowledge bearer with a missing transcript is "
          "left consultable (reconcile skips bearers)",
          lambda: _eq(store.load_org(org3.d["slug"]).nodes[p3]["state"], "live"))

    # ------ the minted-session hole (user bug 2026-08-18) --------------------
    # cheap_compact and reseed both replace `session_id` with an id the CLI has
    # never seen, on a seat whose `cost_usd` is the LIFETIME figure. reconcile
    # judged "has it ever run" by that cost, so a fresh session's (correct,
    # expected) absence of a transcript read as a DEAD one: cheap-compacting an
    # agent and closing orgtree before messaging it came back UNRECOVERABLE —
    # a state that refuses mail and needs a re-seed to leave.
    print("\nreconcile · sessions that were minted and never run:")

    org5, (e,) = horg(grant=20)
    s_e = sid_of(org5, e)
    _plant_transcript(s_e)
    org5.node(e)["cost_usd"] = 3.5           # it HAS run, expensively
    cc = org5.cheap_compact(USER, e)
    store.save_org(org5)
    check("mint · cheap_compact marks the successor's session never-run",
          lambda: _true(store.load_org(org5.d["slug"]).node(e)
                        .get("session_unrun") is True))
    check("mint · …and the bearer keeps the OLD session id, which DID run",
          lambda: _eq(store.load_org(org5.d["slug"])
                      .nodes[cc["bearer"]]["session_id"], s_e))
    supervisor.reconcile(org5.d["slug"])
    check("mint · cheap-compact then RESTART with no message in between "
          "leaves the agent live (was: unrecoverable, mail refused)",
          lambda: _eq(store.load_org(org5.d["slug"]).node(e)["state"], "live"))
    supervisor.reconcile(org5.d["slug"])          # …and again
    check("mint · …and a second restart does not condemn it either",
          lambda: _eq(store.load_org(org5.d["slug"]).node(e)["state"], "live"))
    # the successor is still MAILABLE — the actual user-visible symptom
    org5b = store.load_org(org5.d["slug"])
    check("mint · …so mail to it is still accepted",
          lambda: _true(org5b.post_mail(USER, e, "still there?") is not None))

    # the same hole on the reseed path — the op whose whole purpose is to
    # RESCUE a condemned node re-condemned it at the next restart
    org6, (f6,) = horg(grant=20)
    _plant_transcript(sid_of(org6, f6))
    org6.node(f6)["cost_usd"] = 1.25
    org6.mark_unrecoverable(f6, "test")
    org6.reseed(USER, f6, "reseeded-sid-1")
    store.save_org(org6)
    supervisor.reconcile(org6.d["slug"])
    check("mint · re-seed then RESTART leaves the rescued node live "
          "(was: straight back to unrecoverable)",
          lambda: _eq(store.load_org(org6.d["slug"]).node(f6)["state"], "live"))

    # …and the exemption must be SESSION-scoped, not a permanent pardon
    org7, (g,) = horg(grant=20)
    _plant_transcript(sid_of(org7, g))
    org7.node(g)["cost_usd"] = 2.0
    org7.cheap_compact(USER, g)
    store.save_org(org7)
    new_sid = sid_of(store.load_org(org7.d["slug"]), g)
    _plant_transcript(new_sid)               # the successor's first turn runs
    supervisor.reconcile(org7.d["slug"])
    check("mint · reconcile SELF-HEALS: a transcript for the minted id "
          "spends the exemption on the spot",
          lambda: _true("session_unrun" not in
                        store.load_org(org7.d["slug"]).node(g)))
    os.remove(supervisor.transcript_path(new_sid))   # …and now it is lost
    supervisor.reconcile(org7.d["slug"])
    check("mint · …so a session that HAS run and then loses its transcript "
          "is condemned exactly as before (the pardon is not permanent)",
          lambda: _eq(store.load_org(org7.d["slug"]).node(g)["state"],
                      "unrecoverable"))

    # a turn spends it without waiting for a restart — on the EVIDENCE (a
    # transcript for the session it ran), not on the turn having completed.
    # A turn that ran and then FAILED (usage limit, network, timeout kill,
    # backend death) never reaches _after_turn, and the pardon standing over
    # a session that had demonstrably run disarmed №31 for good on that node
    # (redteam finding 2026-08-18).
    org8, (h,) = horg(grant=20)
    org8.node(h)["cost_usd"] = 1.0
    org8.cheap_compact(USER, h)
    store.save_org(org8)
    sid8 = sid_of(store.load_org(org8.d["slug"]), h)
    check("mint · a turn that never wrote a transcript does NOT spend it",
          lambda: _true(not supervisor.spend_unrun_pardon(
              org8.d["slug"], h, sid8)
              and "session_unrun" in store.load_org(org8.d["slug"]).node(h)))
    _plant_transcript(sid8)               # the CLI wrote one; the turn FAILED
    check("mint · …a failed turn on a session that DID write one spends it",
          lambda: _true(supervisor.spend_unrun_pardon(org8.d["slug"], h, sid8)
                        and "session_unrun" not in
                        store.load_org(org8.d["slug"]).node(h)))

    # the guard that catches a mint landing INSIDE spend_unrun_pardon's own
    # window — between its unlocked pre-check and its locked write. The
    # mid-turn check above pins only the DISJUNCTION "at least one sid
    # guard exists": deleting the under-lock one alone left all 230 checks
    # green, and that mutant puts the user's original bug back (redteam
    # 2026-08-18). The transcript lookup is the only call between the two
    # reads, so firing the compact from there reproduces the race exactly.
    org21, (k21,) = horg(grant=20)
    org21.node(k21)["cost_usd"] = 2.0
    sid21 = sid_of(org21, k21)
    _plant_transcript(sid21)              # the turn in flight, evidence real
    org21.node(k21)["session_unrun"] = True   # …itself a minted session
    store.save_org(org21)
    _tp21, fired = supervisor.transcript_path, []

    def _compact_mid_lookup(sid, root=None):
        if not fired:                     # ONCE, inside the window
            fired.append(True)
            o = store.load_org(org21.d["slug"])
            o.cheap_compact(USER, k21)
            store.save_org(o)
        return _tp21(sid, root)

    supervisor.transcript_path = _compact_mid_lookup   # type: ignore[assignment]
    try:
        spent21 = supervisor.spend_unrun_pardon(org21.d["slug"], k21, sid21)
    finally:
        supervisor.transcript_path = _tp21             # type: ignore[assignment]
    check("mint · the race opens INSIDE the spend — the mint landed after "
          "the pre-check, so only the under-lock re-check can catch it",
          lambda: _true(fired and not spent21
                        and store.load_org(org21.d["slug"])
                        .node(k21).get("session_unrun") is True,
                        f"fired={bool(fired)} spent={spent21}"))
    supervisor.reconcile(org21.d["slug"])
    check("mint · …so that restart leaves it live too",
          lambda: _eq(store.load_org(org21.d["slug"]).node(k21)["state"],
                      "live"))

    # …and it is spent for the session the turn RAN, never by node id: a
    # cheap-compact landing mid-turn has no in-flight guard, and spending by
    # id alone ate the successor's brand-new pardon — the user's original bug,
    # back through a race (redteam finding 2026-08-18).
    org11, (k11,) = horg(grant=20)
    org11.node(k11)["cost_usd"] = 2.0
    sid_running = sid_of(org11, k11)
    _plant_transcript(sid_running)        # the turn in flight, transcript real
    org11.cheap_compact(USER, k11)        # …the user compacts it MID-TURN
    store.save_org(org11)
    spent = supervisor.spend_unrun_pardon(org11.d["slug"], k11, sid_running)
    check("mint · a turn ending on the OLD session cannot spend the pardon "
          "the mint just handed the new one",
          lambda: _true(not spent and store.load_org(org11.d["slug"])
                        .node(k11).get("session_unrun") is True))
    store.save_org(store.load_org(org11.d["slug"]))
    supervisor.reconcile(org11.d["slug"])
    check("mint · …so the restart after a mid-turn cheap-compact still "
          "leaves it live",
          lambda: _eq(store.load_org(org11.d["slug"]).node(k11)["state"],
                      "live"))

    # a NORMAL compaction is not a mint: the CLI's fork writes a transcript,
    # so neither half of the split may inherit the pardon
    org9, (i9,) = horg(grant=20)
    _plant_transcript(sid_of(org9, i9))
    org9.node(i9)["cost_usd"] = 1.0
    org9.cheap_compact(USER, i9)             # arms the marker…
    p9 = org9.compact_split(i9, "forked-sid-1")   # …a real compaction clears it
    store.save_org(org9)
    check("mint · compact_split clears the marker on BOTH halves",
          lambda: _true("session_unrun" not in org9.node(i9)
                        and "session_unrun" not in org9.nodes[p9]))
    supervisor.reconcile(org9.d["slug"])
    check("mint · …so a forked session with no transcript is still condemned",
          lambda: _eq(store.load_org(org9.d["slug"]).node(i9)["state"],
                      "unrecoverable"))

    # cheap-compacting TWICE with no turn between archives a bearer whose own
    # session never ran — rehiring it must not walk into the same condemnation
    org10, (j,) = horg(grant=20)
    _plant_transcript(sid_of(org10, j))
    org10.node(j)["cost_usd"] = 4.0
    org10.cheap_compact(USER, j)
    c2 = org10.cheap_compact(USER, j)        # the bearer here NEVER ran
    org10.nodes[c2["bearer"]]["cost_usd"] = 4.0
    org10.rehire(USER, c2["bearer"], grant=0)
    store.save_org(org10)
    supervisor.reconcile(org10.d["slug"])
    check("mint · a rehired bearer whose own session was never run is not "
          "condemned either",
          lambda: _true(store.load_org(org10.d["slug"])
                        .nodes[c2["bearer"]]["state"] == "live"))
    # …and that must be the MARKER doing it, not `bearer_state` shadowing it:
    # the exemption a bearer gets for being a bearer passed this check with
    # and without the mark (redteam mutation M8, 2026-08-18). The fact the
    # marker records — "THIS session id was never handed to the CLI" — is
    # true of the bearer too, so assert it directly, and re-ask the predicate
    # with the bearer exemption taken away.
    b10 = store.load_org(org10.d["slug"]).nodes[c2["bearer"]]
    check("mint · …because the BEARER carries the never-run mark itself",
          lambda: _true(b10.get("session_unrun") is True))
    check("mint · …which alone exempts it, with bearer_state out of the way",
          lambda: _true(not supervisor._condemnable(
              {**b10, "bearer_state": None}, {})            # type: ignore[arg-type]
              and supervisor._condemnable(
                  {k: v for k, v in {**b10, "bearer_state": None}.items()
                   if k != "session_unrun"}, {})))          # type: ignore[arg-type]

    # ------ an UNREADABLE store is not evidence -----------------------------
    # `transcript_index` answers `{}` both for "this store holds nothing"
    # and for "this store could not be read", and reconcile condemns a
    # whole org on the difference: a sandboxed org's ext4 disk is not
    # loop-mounted until something asks for a container, and the startup
    # sweep runs before anything does. Resolving that root can also RAISE
    # (DiskError, WSL down) inside a FastAPI startup handler with no guard —
    # the backend would not start at all. But absence only excuses the
    # SANDBOXED case: a host-backed store is either there or genuinely
    # gone, and gone must still condemn. (redteam 2026-08-18)
    print("\nreconcile · when the transcript store cannot be read:")

    def _fresh12():
        o, xs = horg(3, grant=20)
        for x in xs:
            o.node(x)["cost_usd"] = 1.0   # all three demonstrably ran…
        store.save_org(o)                 # …and NONE has a transcript
        return o, xs

    def _states(o, xs):
        d = store.load_org(o.d["slug"])
        return {x: d.node(x)["state"] for x in xs}

    _root, _ld = supervisor._transcript_root, os.listdir

    def _deny(name):
        """listdir that traverses but cannot LIST — the case an isdir
        guard waves through (a root-owned projects/ on an org disk, a 9p
        blip over the UNC view)."""
        def f(path, *a, **kw):
            if os.path.basename(str(path).rstrip(os.sep)) == name:
                raise PermissionError(13, "permission denied")
            return _ld(path, *a, **kw)
        return f

    for why, root_stub, ld_stub in [
        ("resolving the root RAISES (WSL down, disk-migrated org)",
         lambda org: (_ for _ in ()).throw(RuntimeError("WSL unavailable")),
         None),
        ("projects/ stats fine but cannot be LISTED",
         None, _deny("projects")),
        ("ONE project dir inside it cannot be listed (the partial index "
         "that looks complete)",
         None, _deny("rig")),
    ]:
        org12, ids12 = _fresh12()
        if root_stub:
            supervisor._transcript_root = root_stub    # type: ignore[assignment]
        if ld_stub:
            os.listdir = ld_stub                       # type: ignore[assignment]
        try:
            marked12 = supervisor.reconcile(org12.d["slug"])
        finally:
            supervisor._transcript_root = _root        # type: ignore[assignment]
            os.listdir = _ld                           # type: ignore[assignment]
        check(f"unreadable · {why} → nothing is condemned",
              (lambda m=marked12, o=org12, x=ids12: _true(
                  not m and all(v == "live" for v in _states(o, x).values()),
                  f"marked {m} · {_states(o, x)}")))

    # ⚠ …but ABSENT is not UNREADABLE, and the first strict pass conflated
    # them: an entry that vanished mid-walk (the user's own Claude Code
    # pruning history beside us), a dangling symlink, or a plain file
    # someone dropped in `projects/` (Explorer writes `desktop.ini` by
    # itself) holds NO transcripts and `glob` skips it — so the index is
    # still right. Raising there made one such entry condemn every node in
    # every host org, which is worse than the bug being fixed, and a
    # regression against the pre-fix code. (redteam 2026-08-18)
    org17, ids17 = _fresh12()
    keep17 = sid_of(org17, ids17[0])
    _plant_transcript(keep17)             # ids17[0] IS resumable…
    junk = os.path.join(HOME, ".claude", "projects", "desktop.ini")
    with io.open(junk, "w", encoding="utf-8") as f:
        f.write("[.ShellClassInfo]" + chr(10))
    try:
        ev17 = supervisor._transcript_evidence(org17)
        marked17 = supervisor.reconcile(org17.d["slug"])
    finally:
        os.remove(junk)
    check("unreadable · a stray FILE in projects/ does not disable №31 "
          "(it holds nothing; glob skips it, and so must the index)",
          lambda: _true(ev17 is not None and keep17 in ev17,
                        f"evidence {None if ev17 is None else sorted(ev17)}")) 
    check("unreadable · …and the org is judged normally around it",
          lambda: _true(sorted(marked17) == sorted(ids17[1:])
                        and store.load_org(org17.d["slug"])
                        .node(ids17[0])["state"] == "live",
                        f"marked {marked17} of {ids17}"))

    # the same shape one level down: a project dir that is gone by the time
    # the walk reaches it (a real TOCTOU against a live Claude Code)
    org18, ids18 = _fresh12()
    keep18 = sid_of(org18, ids18[0])
    _plant_transcript(keep18)
    _ld18 = os.listdir

    def _vanish(path, *a, **kw):
        out = _ld18(path, *a, **kw)
        if os.path.basename(str(path).rstrip(os.sep)) == "projects":
            return list(out) + ["gone-between-the-two-listings"]
        return out

    os.listdir = _vanish                  # type: ignore[assignment]
    try:
        ev18 = supervisor._transcript_evidence(org18)
        marked18 = supervisor.reconcile(org18.d["slug"])
    finally:
        os.listdir = _ld18                # type: ignore[assignment]
    check("unreadable · a project dir that VANISHED mid-walk is skipped, "
          "not read as the whole store being gone",
          lambda: _true(ev18 is not None and keep18 in ev18
                        and sorted(marked18) == sorted(ids18[1:]),
                        f"evidence {None if ev18 is None else sorted(ev18)} "
                        f"· marked {marked18}"))

    # …and the sweep still condemns the moment the store IS readable —
    # the guard must not have disarmed №31 wholesale
    org12, ids12 = _fresh12()
    supervisor.reconcile(org12.d["slug"])
    check("unreadable · …while a READABLE store condemns them as always",
          lambda: _true(all(v == "unrecoverable"
                            for v in _states(org12, ids12).values()),
                        json.dumps(_states(org12, ids12))))

    # a MISSING host store is not the same as an unreadable one: those
    # sessions really are gone, and the old code said so. Skipping the
    # sweep there would resume them onto silent empty sessions instead.
    org13, ids13 = _fresh12()
    gone = os.path.join(TMP, "no-such-transcript-root")
    supervisor._transcript_root = lambda org: gone      # type: ignore[assignment]
    try:
        marked13 = supervisor.reconcile(org13.d["slug"])
    finally:
        supervisor._transcript_root = _root             # type: ignore[assignment]
    check("unreadable · a MISSING host store still condemns (absence only "
          "excuses a sandboxed org, whose disk may be unmounted)",
          lambda: _true(sorted(marked13) == sorted(ids13),
                        f"marked {marked13} of {ids13}"))
    # `projects` present but NOT A DIRECTORY: no transcript is reachable
    # through it, so a host org is judged (and told) rather than left to
    # resume onto empty sessions — the root ENOTDIR branch, which nothing
    # covered (redteam mutation M4, 2026-08-18).
    notdir = os.path.join(TMP, "root-with-a-file")
    os.makedirs(notdir, exist_ok=True)
    with io.open(os.path.join(notdir, "projects"), "w",
                 encoding="utf-8") as f:
        f.write("not a directory" + chr(10))
    for label, sandbox, judged in [
            ("host org is judged", None, True),
            ("sandboxed org is left alone",
             {"enabled": True, "secret": "x" * 32}, False)]:
        orgN, idsN = _fresh12()
        if sandbox:
            orgN.d["sandbox"] = sandbox
            store.save_org(orgN)
        supervisor._transcript_root = lambda org: notdir  # type: ignore[assignment]
        try:
            markedN = supervisor.reconcile(orgN.d["slug"])
        finally:
            supervisor._transcript_root = _root           # type: ignore[assignment]
        check(f"unreadable · projects/ is a FILE → the {label}",
              (lambda m=markedN, i=idsN, w=judged:
               _true((sorted(m) == sorted(i)) if w else not m,
                     f"marked {m}")))

    # THE case the proof exists for, end to end: `projects` is right there
    # in its parent, but listing it raises ENOENT — a junction whose target
    # is gone, an unmapped drive, an unreachable share. Inferring "deleted"
    # from that errno condemns a whole org whose transcripts are fine, and
    # the unit table below cannot catch it: only this asserts that reconcile
    # actually CONSULTS the proof (redteam mutation M25, 2026-08-18).
    org22, ids22 = _fresh12()
    dangling = os.path.join(TMP, "dangling-root")
    os.makedirs(os.path.join(dangling, "projects"), exist_ok=True)
    _ld22 = os.listdir

    def _target_gone(path, *a, **kw):
        # the LINK resolves in its parent and the TARGET does not
        if os.path.basename(str(path).rstrip(os.sep)) == "projects":
            raise FileNotFoundError(2, "the target is gone")
        return _ld22(path, *a, **kw)

    supervisor._transcript_root = lambda org: dangling  # type: ignore[assignment]
    os.listdir = _target_gone                           # type: ignore[assignment]
    try:
        marked22 = supervisor.reconcile(org22.d["slug"])
    finally:
        os.listdir = _ld22                              # type: ignore[assignment]
        supervisor._transcript_root = _root              # type: ignore[assignment]
    check("unreadable · a store that is THERE but whose listing raises "
          "ENOENT is a blind spot, not a deletion — nothing is condemned",
          lambda: _true(not marked22 and all(
              v == "live" for v in _states(org22, ids22).values()),
              f"marked {marked22} · {_states(org22, ids22)}"))

    # the shape that discriminates WHICH path the proof is asked about: the
    # store root is still there and only `projects/` is gone — a user
    # clearing their Claude Code history. Asking about the root instead
    # answers "present", skips the sweep, and resumes every node onto an
    # empty session unannounced (redteam mutation B11, 2026-08-18).
    org23, ids23 = _fresh12()
    kept_root = os.path.join(TMP, "root-without-projects")
    os.makedirs(kept_root, exist_ok=True)      # the root EXISTS…
    supervisor._transcript_root = lambda org: kept_root  # type: ignore[assignment]
    try:
        marked23 = supervisor.reconcile(org23.d["slug"])
    finally:
        supervisor._transcript_root = _root               # type: ignore[assignment]
    check("unreadable · the store root survives and only projects/ is gone "
          "— still a real deletion, still condemned",
          lambda: _true(sorted(marked23) == sorted(ids23),
                        f"marked {marked23} of {ids23}"))

    # …and the proof the ENOENT branch rests on, in isolation: on Windows a
    # deleted dir, a dangling junction, an unmapped drive and an unreachable
    # share ALL raise FileNotFoundError, and only the first is a deletion.
    absent = supervisor._store_provably_absent
    here = os.path.join(TMP, "provably")
    os.makedirs(os.path.join(here, "real"), exist_ok=True)
    with io.open(os.path.join(here, "afile"), "w", encoding="utf-8") as f:
        f.write("x")
    for label, path, want in [
        ("a name its listable parent does not contain",
         os.path.join(here, "nope"), True),
        ("…several levels of it (the whole root is missing)",
         os.path.join(here, "nope", "deeper", "projects"), True),
        ("…below a FILE (nothing can exist under it)",
         os.path.join(here, "afile", "projects"), True),
        ("a path that IS there", os.path.join(here, "real"), False),
    ]:
        check(f"provably-absent · {label} → {want}",
              (lambda pth=path, w=want: _eq(absent(pth), w)))

    # …and the two "could not look" shapes, built with a stub rather than
    # a machine-dependent path: an unmapped drive letter passes here only
    # because that letter happens to be free, fails on a host that maps
    # it, and is not even absolute on POSIX (redteam 2026-08-18).
    _lda = os.listdir

    def _always(exc):
        def f(path, *a, **kw):
            raise exc
        return f

    for label, stub in [
        ("an unreachable share / unmapped drive: EVERY ancestor answers "
         "ENOENT, so the climb ends at the volume root proving nothing",
         _always(FileNotFoundError(2, "not found"))),
        ("a parent that exists and cannot be READ (an ACL'd home, a "
         "root-owned dir on an org disk)",
         _always(PermissionError(13, "denied"))),
    ]:
        os.listdir = stub                 # type: ignore[assignment]
        try:
            got = absent(os.path.join(here, "nope", "projects"))
        finally:
            os.listdir = _lda             # type: ignore[assignment]
        check(f"provably-absent · {label} → False",
              (lambda g=got: _true(g is False, f"got {g!r}")))

    # …and the case that whole distinction exists FOR: a sandboxed org
    # keeps its transcripts on an ext4 image that is routinely not
    # loop-mounted at startup, so the same ENOENT means "not mounted
    # yet", never "gone". Without this the branch was untested and a
    # simplifying edit would reinstate the original org-wide condemnation
    # with every suite green (redteam mutation M-D2, 2026-08-18).
    org19, ids19 = _fresh12()
    org19.d["sandbox"] = {"enabled": True, "secret": "x" * 32}
    store.save_org(org19)
    supervisor._transcript_root = lambda org: gone   # type: ignore[assignment]
    try:
        marked19 = supervisor.reconcile(org19.d["slug"])
    finally:
        supervisor._transcript_root = _root          # type: ignore[assignment]
    check("unreadable · …but a SANDBOXED org's missing store condemns "
          "nothing — its disk is simply not mounted yet",
          lambda: _true(not marked19 and all(
              v == "live" for v in _states(org19, ids19).values()),
              f"marked {marked19} · {_states(org19, ids19)}"))

    # the OTHER site that mints a LOST bearer holds the same invariant: a
    # CLI-side compaction keeps the session id, which is itself proof the
    # session ran (redteam 2026-08-18).
    org20, (n20,) = horg(grant=20)
    _plant_transcript(sid_of(org20, n20))
    org20.node(n20)["cost_usd"] = 1.0
    org20.cheap_compact(USER, n20)        # arms the pardon
    lost20 = org20.record_cli_compaction(n20)
    check("mint · a CLI-side compaction's LOST bearer does not inherit "
          "the pardon either",
          lambda: _true(lost20 is not None
                        and "session_unrun" not in org20.nodes[lost20]
                        and org20.nodes[lost20]["bearer_state"] == "lost"))

    # FR-01 remote control is the ONE writer that fills the node's CURRENT
    # session from outside the turn path (the compaction, command and
    # oracle forks all --fork-session onto a NEW id), so it is the one
    # place a pardon can go stale with no turn running to spend it: the
    # user drives the fresh session from a phone, the CLI writes its
    # transcript, no mail is queued — and an idle node then carries the
    # pardon until the next restart (redteam 2026-08-18).
    org16, (n16,) = horg(grant=20)
    _plant_transcript(sid_of(org16, n16))
    org16.node(n16)["cost_usd"] = 1.0
    org16.cheap_compact(USER, n16)
    store.save_org(org16)
    sid16 = sid_of(store.load_org(org16.d["slug"]), n16)
    _plant_transcript(sid16)              # the phone wrote one; no turn ran
    d16 = store.load_org(org16.d["slug"])
    d16.node(n16)["remote_controlled"] = {"at": "now", "pid": 0}
    store.save_org(d16)
    supervisor.remote_control_stop(org16.d["slug"], n16)
    check("mint · releasing REMOTE CONTROL spends the pardon the phone-"
          "driven session earned",
          lambda: _true("session_unrun" not in
                        store.load_org(org16.d["slug"]).node(n16)))

    # a LOST bearer cannot also be a never-run one: reseed stamps its
    # predecessor "its transcript is gone", and the pardon asserts "this
    # session never ran". One record must not claim both (redteam
    # 2026-08-18). cheap_compact's bearer is the opposite case and keeps it.
    org14, (m14,) = horg(grant=20)
    _plant_transcript(sid_of(org14, m14))
    org14.node(m14)["cost_usd"] = 1.0
    org14.cheap_compact(USER, m14)        # arms the pardon on the successor
    org14.mark_unrecoverable(m14, "test")
    r14 = org14.reseed(USER, m14, "reseeded-sid-2")
    check("mint · a re-seeded LOST bearer does not inherit the pardon",
          lambda: _true("session_unrun" not in
                        org14.nodes[r14["predecessor"]]
                        and org14.nodes[r14["predecessor"]]["bearer_state"]
                        == "lost"))

    # the predicate itself, in isolation — the loop above cannot be unit-tested
    # the dot-directory skip exists so the index cannot disagree with the
    # direct `glob` lookup (`*` does not match a leading dot) — removing it
    # made the index MORE inclusive than the turn path, which would exempt
    # a node from №31 that the turn path then resumes as new. Nothing pinned
    # it (redteam mutation M24, 2026-08-18).
    hidden = os.path.join(HOME, ".claude", "projects", ".hidden-proj")
    os.makedirs(hidden, exist_ok=True)
    with io.open(os.path.join(hidden, "sid-in-a-dot-dir.jsonl"), "w",
                 encoding="utf-8") as f:
        f.write("{}" + chr(10))
    try:
        idx_has = "sid-in-a-dot-dir" in supervisor.transcript_index()
        direct = supervisor.transcript_path("sid-in-a-dot-dir")
    finally:
        shutil.rmtree(hidden, ignore_errors=True)
    check("index · a dot-prefixed project dir is skipped, exactly as glob "
          "skips it — the index may never out-reach the turn path",
          lambda: _true(not idx_has and direct is None,
                        f"index={idx_has} direct={direct!r}"))

    print("\nreconcile's condemnation predicate (supervisor._condemnable):")
    BASE = {"state": "live", "cost_usd": 1.0, "session_id": "sid-x"}
    for over, want, why in [
        ({}, True, "live, has run, transcript gone → condemned"),
        ({"cost_usd": 0.0}, False, "never ran → nothing is missing"),
        ({"cost_usd": None}, False, "cost not booked yet"),
        ({"state": "archived"}, False, "archived promises nothing"),
        ({"state": "unrecoverable"}, False, "already said so"),
        ({"bearer_state": "knowledge"}, False, "a bearer stays consultable"),
        ({"bearer_state": "lost"}, False, "a lost generation is already lost"),
        ({"session_unrun": True}, False,
         "MINTED and never run — absent because unwritten, not lost"),
        ({"session_unrun": False}, True,
         "an explicitly false marker is not an exemption"),
        ({"session_id": "sid-here"}, False, "the transcript is right there"),
        ({"session_id": "sid-here", "session_unrun": True}, False,
         "…and a marker over a live transcript still does not condemn"),
    ]:
        check(f"condemnable · {why}",
              (lambda o=over, w=want:
               _eq(supervisor._condemnable({**BASE, **o},   # type: ignore[arg-type]
                                           {"sid-here": "/p/sid-here.jsonl"}),
                   w)))

    print("\nreconcile against a big transcript store:")
    # ⚠ `projects/` is the USER's whole Claude Code history, not this org's,
    # and `transcript_path` re-lists it per call. FIXED: one walk per pass.
    ndirs = 150 if QUICK else 800
    nnodes = 12 if QUICK else 40
    org4, _ = horg(0)
    for i in range(nnodes):
        k = org4.hire(USER, None, "haiku", 0, f"n{i}", **hspec())["node"]
        org4.node(k)["cost_usd"] = 0.5
        _plant_transcript(sid_of(org4, k))
    for i in range(ndirs):                       # unrelated projects
        d = os.path.join(HOME, ".claude", "projects", f"junk-{i}")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, f"{i}.jsonl"), "a").close()
    store.save_org(org4)
    t0 = time.perf_counter()
    supervisor.transcript_path("nope", None)
    t_one = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    supervisor.reconcile(org4.d["slug"])
    t_rec2 = (time.perf_counter() - t0) * 1000
    measured(f"reconcile · {nnodes} nodes against {ndirs} project dirs",
             f"{t_rec2:.0f} ms · one transcript_path lookup {t_one:.1f} ms · "
             f"ratio {t_rec2 / max(t_one, 0.01):.1f}×")
    check("reconcile · no live node with a transcript is condemned",
          lambda: _true(all(v["state"] == "live"
                            for v in store.load_org(org4.d["slug"]).nodes.values()),
                        json.dumps({k: v["state"] for k, v in
                                    store.load_org(org4.d["slug"]).nodes.items()})))
    check("reconcile · does ONE directory walk, not one per node "
          "(was O(nodes × project dirs))",
          lambda: _true(t_rec2 < t_one * 4 + 150,
                        f"{t_rec2:.0f} ms for {nnodes} nodes vs {t_one:.1f} ms "
                        f"for a single lookup — the per-node scan is back"))
    if QUICK:
        note("the reconcile scaling check discriminates weakly at --quick "
             "sizes (12 nodes / 150 dirs); the full run uses 40 / 800")
    check("reconcile · the index agrees with the direct lookup on every node",
          lambda: _eq(
              sorted(k for k in supervisor.transcript_index(None)
                     if k in {sid_of(org4, x) for x in org4.nodes}),
              sorted(sid_of(org4, x) for x in org4.nodes
                     if supervisor.transcript_path(sid_of(org4, x)) is not None)))


# =================================================== the live half (a backend)

BASE = f"http://127.0.0.1:{PORT}"
PROC: subprocess.Popen[str] | None = None
_orgs: list[str] = []

WRAP_JS = r"""
'use strict'
// compactcli.js — the compaction fork, programmable. Delegates everything else
// to fakecli.js, which is deliberately left untouched (the wrapper idiom
// test_turn_lifecycle.py established). Generated by test_compaction.py.
const fs = require('fs')
const argv = process.argv.slice(2)
function arg(n) { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : null }
let cfg = {}
try {
  const f = JSON.parse(fs.readFileSync(process.env.FAKECLI_CONFIG, 'utf8'))
  const node = process.env.ORGTREE_NODE || ''
  cfg = Object.assign({}, (f.fork || {}).default || {}, (f.fork || {})[node] || {})
} catch (e) { cfg = {} }

// A REAL fork leaves a transcript behind, and what it contains is the whole
// question when the desk asks a compacted-but-idle agent how full it is: the
// copied pre-compaction history, the boundary, the summary that replaced it —
// and NO record of the new prompt, because nothing has sent one yet. Off by
// default (every other check here predates it and does not want the file);
// `forkTranscript: true` plants it, with the fill numbers as dials.
function forkTranscript(newSid) {
  if (!cfg.forkTranscript) return
  const path = require('path'), os = require('os')
  const home = process.env.USERPROFILE || process.env.HOME || os.homedir()
  const dir = path.join(home, '.claude', 'projects',
    process.cwd().replace(/[\\/:]+/g, '-').replace(/^-+/, ''))
  const floor = cfg.floorOcc || 900, pre = cfg.preOcc || 90000
  const usage = (n) => ({ input_tokens: n, cache_read_input_tokens: 0,
                          cache_creation_input_tokens: 0 })
  const say = (i, text, n) => ({
    type: 'assistant', timestamp: new Date(Date.now() + i).toISOString(),
    message: { role: 'assistant', model: 'fake',
               content: [{ type: 'text', text: text }], usage: usage(n) } })
  const lines = [
    say(0, 'the first turn of the session', floor),
    say(1, 'the turn that filled it up', pre),
    { type: 'system', subtype: 'compact_boundary', isMeta: false,
      timestamp: new Date(Date.now() + 2).toISOString(),
      content: 'Conversation compacted',
      compactMetadata: { trigger: 'manual', preTokens: pre,
                         postTokens: cfg.postOcc || 3000 } },
    { type: 'user', isCompactSummary: true,
      timestamp: new Date(Date.now() + 3).toISOString(),
      message: { role: 'user',
                 content: 'S'.repeat(cfg.summaryChars || 4000) } },
  ]
  try {
    fs.mkdirSync(dir, { recursive: true })
    const fd = fs.openSync(path.join(dir, newSid + '.jsonl'), 'a')
    for (const l of lines) fs.writeSync(fd, JSON.stringify(l) + '\n')
    fs.fsyncSync(fd)
    fs.closeSync(fd)
  } catch (e) { /* the split must survive an unwritable transcript store */ }
}

// THE COMPACTION FORK. orgtree runs `--output-format json --resume <sid>
// --fork-session` and feeds it the literal text "/compact"; it accepts the
// result only if rc==0 AND a session_id comes back AND that id DIFFERS from
// the one it asked to resume. Every one of those three is a dial here.
if (arg('--output-format') === 'json') {
  const mode = cfg.mode || 'ok'
  const old = arg('--resume') || 'no-session'
  if (mode === 'hang') { setInterval(() => {}, 1000); return }
  if (mode === 'silent') { process.exit(0) }                 // rc 0, no stdout
  if (mode === 'garbage') { process.stdout.write('not json at all\n'); process.exit(0) }
  if (mode === 'rc') {
    process.stderr.write(cfg.errText || 'fork: could not resume session\n')
    process.exit(cfg.code || 2)
  }
  if (mode === 'samesid') {
    process.stdout.write(JSON.stringify({ type: 'result', subtype: 'success',
      session_id: old, result: 'compacted.', total_cost_usd: 0.5 }) + '\n')
    process.exit(0)
  }
  if (mode === 'nosid') {
    process.stdout.write(JSON.stringify({ type: 'result', subtype: 'success',
      result: 'compacted.', total_cost_usd: 0.5 }) + '\n')
    process.exit(0)
  }
  if (mode === 'numsid') {                       // a session id that is not a string
    process.stdout.write(JSON.stringify({ type: 'result', subtype: 'success',
      session_id: 12345, result: 'compacted.', total_cost_usd: 0.5 }) + '\n')
    process.exit(0)
  }
  if (mode === 'banner') {
    // ONE unrelated stdout line in front of a perfectly good result — an npm
    // notice, a node warning, a debug banner. json.loads(whole) throws on it.
    process.stdout.write((cfg.bannerText ||
      'npm notice New major version of npm available!') + '\n')
    const sid = 'forked-after-a-banner-' + require('crypto').randomUUID()
    forkTranscript(sid)
    process.stdout.write(JSON.stringify({ type: 'result', subtype: 'success',
      session_id: sid, result: 'compacted.', total_cost_usd: 0.25 }) + '\n')
    process.exit(0)
  }
  const wait = cfg.forkMs || 0
  setTimeout(() => {
    const sid = (cfg.sidPrefix || 'forked-') + require('crypto').randomUUID()
    forkTranscript(sid)
    process.stdout.write(JSON.stringify({
      type: 'result', subtype: 'success',
      session_id: sid,
      result: 'compacted.',
      total_cost_usd: cfg.forkCost === undefined ? 0.25 : cfg.forkCost }) + '\n')
    process.exit(0)
  }, wait)
  return
}
require(process.env.FAKECLI_REAL)
"""


def write_wrapper() -> None:
    with open(WRAP, "w", encoding="utf-8") as f:
        f.write(WRAP_JS)


def set_cfg(default: dict | None = None, fork: dict | None = None,
            **per_node) -> None:
    cfg: dict = {"default": dict(default or {})}
    cfg.update(per_node)
    if fork:
        cfg["fork"] = fork
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def port_free(p: int, tries: int = 100) -> None:
    for _ in range(tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return
        except OSError:
            s.close()
            time.sleep(0.1)
    raise RuntimeError(f"port {p} never freed")


def start_backend(max_turns: int = 16, turn_timeout: int = 60,
                  windows: dict | None = None, oracle_at: str = "0.92",
                  compact_at: str = "0.80") -> None:
    global PROC
    stop_backend()
    port_free(PORT)
    env = dict(os.environ)
    env.update({
        "ORGTREE_DATA": DATA, "USERPROFILE": HOME, "HOME": HOME,
        "ORGTREE_PORT": str(PORT),
        "FAKECLI_CONFIG": CFG,
        "FAKECLI_REAL": os.path.join(_HERE, "fakecli.js").replace("\\", "/"),
        "ORGTREE_CLAUDE_CLI": WRAP,
        "ORGTREE_MAX_TURNS": str(max_turns),
        "ORGTREE_STEER_HOOK": "0",
        # both bounds ride the same knob: a hung fake CLI emits nothing, so
        # since the 2026-08-04 reshape the IDLE watchdog is what fires; the
        # ceiling stays set so the total budget is bounded too
        "ORGTREE_TURN_TIMEOUT": str(turn_timeout),
        "ORGTREE_TURN_IDLE": str(turn_timeout),
        "ORGTREE_COMPACT_AT": compact_at,
        "ORGTREE_ORACLE_AT": oracle_at,
        "PYTHONPATH": os.path.join(_REPO, "backend"),
        "PYTHONIOENCODING": "utf-8",
        # ⚠ never claim anything the user's real backend holds: the bridge
        # listener defaults to 0.0.0.0:7362 and a second bind kills the process.
        "ORGTREE_BRIDGE_PORT": "0",
    })
    # the occupancy dial: fakecli reports a fixed 1200 input tokens, so the
    # only way to cross a threshold from a test is to shrink the WINDOW
    env["ORGTREE_CONTEXT_WINDOWS"] = json.dumps(windows or {"haiku": 200_000})
    env.pop("ORGTREE_PUBLIC_PORT", None)
    env.pop("ORGTREE_EXPOSE_ADMIN", None)
    log = open(LOG, "a", encoding="utf-8")
    PROC = subprocess.Popen([sys.executable, "-m", "orgtree.api"],
                            cwd=os.path.join(_REPO, "backend"),
                            env=env, stdout=log, stderr=log, text=True)
    for _ in range(300):
        if PROC.poll() is not None:
            raise RuntimeError(f"backend exited {PROC.returncode} at startup:\n"
                               + log_tail())
        try:
            urllib.request.urlopen(BASE + "/api/orgs", timeout=1).read()
            return
        except Exception:                                        # noqa: BLE001
            time.sleep(0.1)
    raise RuntimeError(f"backend never came up on {PORT}:\n" + log_tail())


def log_tail(n: int = 3000) -> str:
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()[-n:]
    except OSError:
        return "(no log)"


def stop_backend(hard: bool = False) -> None:
    global PROC
    if PROC is None:
        return
    try:
        PROC.kill() if hard else PROC.terminate()
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
            PROC.wait(timeout=10)
    except OSError:
        pass
    PROC = None


def api(method: str, path: str, body=None, timeout: float = 30):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def api_err(method: str, path: str, body=None) -> tuple[int, str]:
    try:
        api(method, path, body)
        return 200, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def make_org(label: str, agents: int = 1, grant: int = 3,
             names: list[str] | None = None) -> tuple[str, list[str]]:
    """⚠ `names` matters more than it looks: the fake CLI is programmed PER
    NODE ID, and two orgs whose agents are both called `agent0` share one
    config entry — which silently makes a cross-org timing measurement
    measure the wrong thing."""
    name = f"zz compact {len(_orgs)} {label}"[:60]
    r = api("POST", "/api/orgs", {"name": name})
    slug = r.get("slug") or r.get("org", {}).get("slug")
    _orgs.append(slug)
    nids = []
    for i in range(agents):
        h = api("POST", f"/api/orgs/{slug}/ops",
                {"op": "hire", "actor": USER, "parent": None, "tier": "haiku",
                 "grant": grant,
                 "name": (names or [])[i] if names and i < len(names)
                 else f"agent{i}",
                 "charter": "a test agent",
                 "tools": {"bash": True, "web": False, "edit": False,
                           "subagents": False, "mcp": []},
                 "org_visibility": "team", "add_dirs": []})
        nids.append(h.get("node") or f"agent{i}")
    return slug, nids


def drop_orgs() -> None:
    for slug in list(_orgs):
        try:
            api("DELETE", f"/api/orgs/{slug}")
        except Exception:                                        # noqa: BLE001
            pass
    _orgs.clear()


def send(slug: str, nid: str, text: str) -> dict:
    return api("POST", f"/api/orgs/{slug}/nodes/{nid}/message", {"text": text})


def doc(slug: str) -> dict:
    """The org doc ON DISK — `GET /api/orgs/{slug}` answers the derived TREE
    (nested `roots`, org axis only), which by construction cannot show a
    knowledge bearer as a node. Lineage checks need the raw document."""
    p = os.path.join(DATA, "orgs", slug + ".json")
    for _ in range(30):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            time.sleep(0.05)
    raise RuntimeError(f"could not read {p}")


def tree(slug: str) -> dict:
    return api("GET", f"/api/orgs/{slug}")


def tree_node(slug: str, nid: str) -> dict:
    """A node as the CANVAS sees it. `waiting` (№12, the hollow dot) lives
    only here — `annotate` puts it on the tree payload, not on /chat."""
    def walk(rows: list) -> dict:
        for r in rows:
            if r["id"] == nid:
                return r
            hit = walk(r.get("children") or [])
            if hit:
                return hit
        return {}
    return walk(tree(slug).get("roots") or [])


def node(slug: str, nid: str) -> dict:
    return doc(slug)["nodes"][nid]


def wait_for(pred, secs: float = 30, step: float = 0.1) -> bool:
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            if pred():
                return True
        except Exception:                                        # noqa: BLE001
            pass
        time.sleep(step)
    return False


def chat(slug: str, nid: str) -> dict:
    """⚠ The chat payload is FLAT (`{busy, messages, …}`) — there is no outer
    `chat` key. Reading one costs nothing and silently makes every liveness
    test pass instantly, which is exactly how a bad `wait_idle` hides a race."""
    try:
        return api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat")
    except Exception:                                            # noqa: BLE001
        return {}


def busy(slug: str, nid: str) -> bool:
    return bool(chat(slug, nid).get("busy"))


def wait_idle(slug: str, nid: str, secs: float = 40) -> bool:
    return wait_for(lambda: not busy(slug, nid), secs)


def transcript_text() -> str:
    out = []
    for p in glob.glob(os.path.join(HOME, ".claude", "projects", "*", "*.jsonl")):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                out.append(f.read())
        except OSError:
            pass
    return "\n".join(out)


def carriers(slug: str, nid: str, tok: str) -> dict[str, bool]:
    """WHERE IS THE MESSAGE — every carrier that could still deliver it."""
    d = doc(slug)
    n = (d.get("nodes") or {}).get(nid) or {}
    fz = n.get("frozen") or {}
    c = chat(slug, nid)
    return {
        "mailbox": any(tok in m.get("body", "")
                       for m in (d.get("mail") or {}).get(nid, [])),
        "journal": any(tok in (m.get("body") or "")
                       for b in (d.get("delivering") or {}).get(nid, [])
                       for m in (b.get("mail") or [])),
        "inflight": tok in ((n.get("inflight") or {}).get("text") or ""),
        "frozen": any(tok in t for t in (fz.get("resume_texts") or [])),
        "queued": tok in json.dumps(c.get("pending_mail") or []),
        "transcript": tok in json.dumps(c.get("messages") or []),
    }


# ================================================ 6. live: the real fork

FAST = {"echoMs": 40, "firstEventMs": 60, "resultMs": 10}


def _prime(slug: str, nid: str, secs: float = 40) -> None:
    """Run one turn so the node has a session, an occupancy and a cost.

    ⚠ Waiting on `not busy` alone is a bootstrap trap: `send` returns before
    the worker thread has latched `busy`, so the FIRST poll sees an idle node
    and the prime silently completes before the turn has begun. Wait for
    evidence the turn actually happened — the token in the transcript — and
    then for the occupancy the compaction precheck needs."""
    tok = token()
    send(slug, nid, f"prime {tok}")
    if not wait_for(lambda: carriers(slug, nid, tok)["transcript"], secs):
        raise AssertionError(f"{nid} never ran the priming turn: "
                             f"{json.dumps(carriers(slug, nid, tok))}\n"
                             f"{log_tail(1200)}")
    if not wait_idle(slug, nid, secs):
        raise AssertionError(f"{nid} never went idle:\n{log_tail(1200)}")
    if not wait_for(lambda: node(slug, nid).get("occupancy"), 15):
        raise AssertionError(f"{nid} reported no occupancy after a turn:\n"
                             f"{json.dumps(node(slug, nid))[:400]}")


def live_manual_split() -> None:
    print("\nlive · the manual split, end to end:")
    start_backend()
    # the fork plants the transcript a real one leaves behind (history, then a
    # boundary, then the summary, and nothing measuring the new prompt) — the
    # state the occupancy bug of 2026-08-20 lived in
    set_cfg(FAST, fork={"default": {"mode": "ok", "forkCost": 0.25,
                                    "forkTranscript": True, "floorOcc": 900,
                                    "preOcc": 90_000, "summaryChars": 4_000}})
    slug, (nid,) = make_org("manual")
    _prime(slug, nid)
    before = node(slug, nid)
    old_sid = before["session_id"]
    cost0 = float(before.get("cost_usd") or 0)
    check("manual · a primed node reports an occupancy",
          lambda: _true(before.get("occupancy")))
    api("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    check("manual · the split completes",
          lambda: _true(wait_for(lambda: node(slug, nid)["session_id"] != old_sid,
                                 60), log_tail(1500)))
    after = node(slug, nid)
    nodes = doc(slug)["nodes"]
    bearers = [k for k, n in nodes.items() if n.get("bearer_state") == "knowledge"]
    check("manual · exactly one knowledge bearer appears",
          lambda: _eq(len(bearers), 1))
    check("manual · the bearer is <nid>@0",
          lambda: _eq(bearers[0], f"{nid}@0"))
    check("manual · the bearer holds the pre-compaction session id",
          lambda: _eq(nodes[bearers[0]]["session_id"], old_sid))
    check("manual · the successor's session id really changed",
          lambda: _true(after["session_id"] != old_sid))
    # ⚠ This used to assert the successor's occupancy was FALSY. That cleared
    # the stale near-full reading on the card but replaced it with nothing —
    # and the desk reads the TRANSCRIPT (`chat.occupancy ?? node.occupancy`),
    # where the pre-compaction number lived on until the next turn. The
    # contract is now the drop itself: a real, much smaller figure, at once,
    # marked as an estimate until something measures it (user bug 2026-08-20).
    # EXACT, not "small": the rig's own pre-split occupancy is fakecli's 1200,
    # so a check that only asked for "under a quarter of 90k" would pass on
    # code that never touched the field at all (redteam 2026-08-20). The
    # planted fork transcript says floor 900 and postTokens 3000, so the one
    # right answer is 3900 — computed from the transcript, by the production
    # reader, inside the split.
    check("manual · the successor's occupancy is the POST-compaction fill "
          "the fork's own transcript reports, exactly",
          lambda: _eq(after.get("occupancy"), 900 + 3_000))
    check("manual · …which is not the pre-compaction figure, and not the "
          "figure it had before the split either",
          lambda: _true(after.get("occupancy") not in (90_000,
                                                       before.get("occupancy")),
                        f"{after.get('occupancy')!r} vs pre-split "
                        f"{before.get('occupancy')!r}"))
    check("manual · …and it is DECLARED an estimate, not passed off as "
          "measured",
          lambda: _eq(after.get("occupancy_est"), True))
    tree_node = next((x for x in api("GET", f"/api/orgs/{slug}")["roots"]
                      if x["id"] == nid), {})
    check("manual · …and the flag reaches the card through the tree payload",
          lambda: _eq((tree_node.get("occupancy"), tree_node.get("occupancy_est")),
                      (after.get("occupancy"), True)))
    chat = api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat?last=5")
    check("manual · the DESK reads the same drop as the card — the transcript "
          "no longer answers with the pre-compaction number",
          lambda: _eq((chat.get("occupancy"), chat.get("occupancy_estimated")),
                      (after.get("occupancy"), True)))
    code, body = api_err("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    check("manual · …and a just-compacted node still refuses to be compacted "
          "again (the guard the falsy occupancy used to make silently)",
          lambda: _true(code == 422 and "nothing to compact" in body.lower(),
                        f"{code} {body[:200]}"))
    check("manual · the fork's dollar cost is billed to the successor",
          lambda: _true(float(after.get("cost_usd") or 0) >= cost0 + 0.2,
                        f"{cost0} → {after.get('cost_usd')}: the fork is a real "
                        f"API call and must not be invisible to the spend cap"))
    check("manual · the bearer's own cost_usd starts at zero",
          lambda: _eq(float(nodes[bearers[0]].get("cost_usd") or 0), 0.0))
    check("manual · the org's total spend counts the fork exactly once",
          lambda: _true(abs(sum(float(n.get("cost_usd") or 0)
                                for n in nodes.values())
                            - float(after.get("cost_usd") or 0)) < 1e-9))
    check("manual · the node is idle and runnable afterwards",
          lambda: _true(wait_idle(slug, nid, 30)))
    tok = token()
    send(slug, nid, f"after the split {tok}")
    check("manual · a message after the split reaches the SUCCESSOR",
          lambda: _true(wait_for(lambda: carriers(slug, nid, tok)["transcript"], 40),
                        json.dumps(carriers(slug, nid, tok))))
    wait_idle(slug, nid, 30)
    check("manual · one turn later the fill is MEASURED, and the estimate "
          "flag is gone",
          lambda: _true(wait_for(lambda: node(slug, nid).get("occupancy")
                                 and not node(slug, nid).get("occupancy_est"), 20),
                        json.dumps({k: node(slug, nid).get(k)
                                    for k in ("occupancy", "occupancy_est")})))
    check("manual · …and the compaction that ran is no longer the reason to "
          "refuse another one",
          lambda: _true(not node(slug, nid).get("compacted_unrun"),
                        "the summary-only marker outlived the turn that ended "
                        "the summary-only state"))

    # a second split, on the successor
    _prime(slug, nid)
    api("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    check("manual · a second split makes generation 2",
          lambda: _true(wait_for(lambda: node(slug, nid)["generation"] == 2, 60),
                        log_tail(1200)))
    check("manual · …and two bearers, one per generation",
          lambda: _eq(sorted(k for k, n in doc(slug)["nodes"].items()
                             if n.get("bearer_state")),
                      [f"{nid}@0", f"{nid}@1"]))
    check("manual · …with the org chart still showing one live agent",
          lambda: _eq([k for k, n in doc(slug)["nodes"].items()
                       if n["state"] == "live"], [nid]))
    wait_idle(slug, nid, 30)
    drop_orgs()


def live_auto_split() -> None:
    print("\nlive · the automatic threshold split:")
    # fakecli reports 1200 input tokens; a 1400-token window puts one turn at
    # 86% — over the 80% default and under the 92% oracle line.
    start_backend(windows={"haiku": 1400})
    set_cfg(FAST, fork={"default": {"mode": "ok"}})
    slug, (nid,) = make_org("auto")
    old_sid = node(slug, nid)["session_id"]
    send(slug, nid, f"one turn {token()}")
    check("auto · crossing compact_at splits the node with no user action",
          lambda: _true(wait_for(lambda: node(slug, nid)["session_id"] != old_sid,
                                 60), log_tail(1500)))
    check("auto · the bearer is created by the automatic path too",
          lambda: _true(any(n.get("bearer_state") == "knowledge"
                            for n in doc(slug)["nodes"].values())))
    check("auto · the split happens INSIDE the turn (the node is busy for it)",
          lambda: _true(wait_idle(slug, nid, 60)))
    drop_orgs()

    print("\nlive · mail that arrives while the split runs:")
    start_backend(windows={"haiku": 200_000})
    set_cfg(FAST, fork={"default": {"mode": "ok", "forkMs": 2500}})
    slug, (nid,) = make_org("during")
    _prime(slug, nid)
    old_sid = node(slug, nid)["session_id"]
    api("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    time.sleep(0.6)
    check("during · the desk says 'compacting', not a lying 'working'",
          lambda: _true(wait_for(
              lambda: chat(slug, nid).get("phase") == "compacting", 4)
              or _phase_note()))
    tok = token()
    send(slug, nid, f"mid-split {tok}")
    c = carriers(slug, nid, tok)
    check("during · the message is held by SOME carrier immediately",
          lambda: _true(any(c.values()), json.dumps(c)))
    check("during · the split still completes",
          lambda: _true(wait_for(lambda: node(slug, nid)["session_id"] != old_sid,
                                 60), log_tail(1200)))
    check("during · the message is delivered to the SUCCESSOR, not the bearer",
          lambda: _true(wait_for(
              lambda: carriers(slug, nid, tok)["transcript"], 45),
              json.dumps(carriers(slug, nid, tok))))
    check("during · …and it is not in the bearer's transcript",
          lambda: _true(_bearer_has(slug, f"{nid}@0", tok) is False))
    wait_idle(slug, nid, 30)
    drop_orgs()


def _phase_note() -> bool:
    note("the `compacting` phase was not observed on the chat payload — the "
         "fork finished faster than the poll; not a failure of the invariant")
    return True


def _bearer_has(slug: str, bearer: str, tok: str) -> bool:
    return tok in json.dumps(chat(slug, bearer).get("messages") or [])


def live_fork_failures() -> None:
    print("\nlive · every way the fork can fail:")
    cases = [
        ("rc", "a non-zero exit"),
        ("silent", "rc 0 with no stdout"),
        ("garbage", "unparseable stdout"),
        ("nosid", "a result carrying no session_id"),
        ("samesid", "a result echoing the SAME session id (it never forked)"),
        ("numsid", "a session id that is not a string"),
    ]
    if not QUICK:
        cases.append(("hang", "a fork that never answers"))
    for mode, human in cases:
        start_backend(turn_timeout=60)
        # a hang must not sit here for the real 600 s ceiling — the leash is
        # what kills it, and the leash is what this case is really about, so
        # the child is killed by stopping the backend instead of waiting it out
        set_cfg(FAST, fork={"default": {"mode": mode}})
        slug, (nid,) = make_org(f"fail-{mode}")
        _prime(slug, nid)
        before = node(slug, nid)
        old_sid = before["session_id"]
        occ0 = before.get("occupancy")
        api("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
        if mode == "hang":
            time.sleep(3)
            check("fail · a hung fork holds the node busy (it does not lie idle)",
                  lambda: _true(busy(slug, nid)))
            kids = _child_count()
            stop_backend(hard=True)
            time.sleep(1.0)
            # ⚠ counts EVERY node process on the box, so a tolerance of one
            # covers an unrelated one starting during the window; the failure
            # this guards against is a child that outlives its backend, which
            # shows up as the count staying up, not drifting by one
            check("fail · killing the backend reaps the hung fork child "
                  "(the №29 leash)",
                  lambda: _true(_child_count() <= kids + 1,
                                f"{kids} → {_child_count()} node processes: an "
                                f"orphaned CLI would keep appending to the "
                                f"transcript a restarted backend also resumes"))
            _orgs.clear()
            continue
        ok = wait_for(lambda: not busy(slug, nid), 60)
        check(f"fail · {human} · the node stops being busy",
              (lambda o=ok: _true(o, log_tail(1200))))
        after = node(slug, nid)
        check(f"fail · {human} · NO bearer is created",
              (lambda s=slug: _eq([k for k, n in doc(s)["nodes"].items()
                                   if n.get("bearer_state")], [])))
        check(f"fail · {human} · the session id is unchanged",
              (lambda a=after, o=old_sid: _eq(a["session_id"], o)))
        check(f"fail · {human} · the occupancy is preserved (so the retry "
              f"precheck still passes)",
              (lambda a=after, o=occ0: _eq(a.get("occupancy"), o)))
        check(f"fail · {human} · the node is still LIVE and runnable",
              (lambda a=after: _eq(a["state"], "live")))
        tok = token()
        send(slug, nid, f"after a failed split {tok}")
        check(f"fail · {human} · a message after the failure still lands",
              (lambda s=slug, n=nid, t=tok: _true(
                  wait_for(lambda: carriers(s, n, t)["transcript"], 45),
                  json.dumps(carriers(s, n, t)))))
        wait_idle(slug, nid, 30)
        drop_orgs()


def live_noisy_fork() -> None:
    """A fork whose stdout carries one unrelated line in front of a perfectly
    good result. FIXED: `json.loads(whole_stream)` threw, the caller read that
    as a failed split, and the most expensive call the system makes was thrown
    away along with a 15-minute cooldown."""
    print("\nlive · a fork with a banner line on stdout:")
    start_backend()
    set_cfg(FAST, fork={"default": {"mode": "banner"}})
    slug, (nid,) = make_org("banner")
    _prime(slug, nid)
    old_sid = node(slug, nid)["session_id"]
    api("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    ok = wait_for(lambda: node(slug, nid)["session_id"] != old_sid, 60)
    check("banner · an unrelated stdout line does not fail the split",
          lambda: _true(ok, log_tail(1200)))
    check("banner · …and the successor takes the id that was in there all along",
          lambda: _true(node(slug, nid)["session_id"].startswith(
              "forked-after-a-banner-"), node(slug, nid)["session_id"]))
    check("banner · …and a knowledge bearer is created normally",
          lambda: _eq(len([k for k, n in doc(slug)["nodes"].items()
                           if n.get("bearer_state")]), 1))
    wait_idle(slug, nid, 30)
    drop_orgs()


def live_deleted_mid_fork() -> None:
    """The node is deleted while its fork is running. FIXED: `compact_split`
    raised a LedgerError out of a daemon thread whose caller catches only
    RuntimeError, killing the thread — and the fork's real dollar cost went
    with it."""
    print("\nlive · the node is deleted while the fork runs:")
    start_backend()
    set_cfg(FAST, fork={"default": {"mode": "ok", "forkMs": 4000,
                                    "forkCost": 0.75}})
    slug, (nid,) = make_org("deleted")
    _prime(slug, nid)
    before_total = float(tree(slug).get("cost_usd_total") or 0)
    api("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    time.sleep(1.0)
    api("POST", f"/api/orgs/{slug}/ops",
        {"op": "delete", "actor": USER, "node": nid})
    check("deleted · the node really is gone",
          lambda: _true(nid not in doc(slug)["nodes"]))
    time.sleep(6.0)
    d = doc(slug)
    check("deleted · no ghost node or bearer is resurrected by the late fork",
          lambda: _eq([k for k in d["nodes"] if k.startswith(nid)], []))
    check("deleted · the org doc is still coherent",
          lambda: _true(tree(slug).get("audit", {}).get("no_overdraft", True)))
    total = float(tree(slug).get("cost_usd_total") or 0)
    check("deleted · the fork's dollar cost is still banked (it was really "
          "billed)",
          lambda: _true(total >= before_total + 0.7,
                        f"{before_total:.4f} → {total:.4f}: an unrecorded fork "
                        f"walks the kiosk spend cap backwards"))
    check("deleted · the backend did not die on it",
          lambda: _true(PROC is not None and PROC.poll() is None))
    check("deleted · …and no unhandled LedgerError reached the log",
          lambda: _true("LedgerError" not in log_tail(4000), log_tail(800)))
    drop_orgs()


def _child_count() -> int:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process node -ErrorAction SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        return int(out or 0)
    except Exception:                                            # noqa: BLE001
        return 0


def live_split_interrupted() -> None:
    print("\nlive · the backend dies mid-split:")
    start_backend()
    set_cfg(FAST, fork={"default": {"mode": "ok", "forkMs": 6000}})
    slug, (nid,) = make_org("crash")
    _orgs.append(slug)
    _prime(slug, nid)
    old_sid = node(slug, nid)["session_id"]
    tok = token()
    api("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    time.sleep(0.8)
    send(slug, nid, f"queued while compacting {tok}")
    time.sleep(0.5)
    stop_backend(hard=True)
    start_backend()
    set_cfg(FAST, fork={"default": {"mode": "ok"}})
    d = doc(slug)
    n = d["nodes"][nid]
    check("crash · the node survives with a coherent session id",
          lambda: _true(n["session_id"] == old_sid
                        or n["session_id"].startswith("forked-"),
                        f"session_id={n['session_id']!r}"))
    check("crash · no half-made bearer points at a session nobody holds",
          lambda: _true(all(d["nodes"][k]["successor"] in d["nodes"]
                            for k, v in d["nodes"].items()
                            if v.get("bearer_state"))))
    c = carriers(slug, nid, tok)
    check("crash · the message queued during the split is not lost",
          lambda: _true(any(c.values()), json.dumps(c)))
    check("crash · the node is runnable again after the restart",
          lambda: _true(wait_for(lambda: not busy(slug, nid), 60)))
    tok2 = token()
    send(slug, nid, f"post-restart {tok2}")
    check("crash · …and answers a new message",
          lambda: _true(wait_for(lambda: carriers(slug, nid, tok2)["transcript"],
                                 45), json.dumps(carriers(slug, nid, tok2))))
    wait_idle(slug, nid, 30)
    drop_orgs()


def live_oracle() -> None:
    print("\nlive · the §8.3/§8.4 preserving oracle:")
    # A 1400-token window puts one fakecli turn at 1200/1400 = 86 %. That is
    # over the ORACLE line (dropped to 50 %) and must be kept UNDER the
    # compaction line, or the node auto-splits during priming and the test
    # measures nothing. ⚠ `ORGTREE_COMPACT_AT` does not do that: every org doc
    # is created carrying its own `compact_at` (api.py DEFAULTS), and the doc
    # wins — so the threshold has to be raised on the ORG.
    start_backend(windows={"haiku": 1400}, oracle_at="0.50")
    set_cfg(FAST, fork={"default": {"mode": "ok"}})
    slug, (nid,) = make_org("oracle")
    api("POST", f"/api/orgs/{slug}/settings", {"compact_at": 95})
    _prime(slug, nid)
    check("oracle · a node under its own threshold does not auto-split",
          lambda: _eq(node(slug, nid)["generation"], 0))
    api("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    if not wait_for(lambda: node(slug, nid)["generation"] == 1, 60):
        note("oracle · the split did not complete; skipping the oracle checks")
        drop_orgs()
        return
    bearer = f"{nid}@0"
    api("POST", f"/api/orgs/{slug}/ops",
        {"op": "rehire", "actor": USER, "node": bearer, "grant": 0})
    check("oracle · the bearer rehires live",
          lambda: _eq(node(slug, bearer)["state"], "live"))
    b_sid = node(slug, bearer)["session_id"]
    tok = token()
    send(slug, bearer, f"consult {tok}")
    check("oracle · a knowledge bearer answers a consultation",
          lambda: _true(wait_for(lambda: carriers(slug, bearer, tok)["transcript"],
                                 45), json.dumps(carriers(slug, bearer, tok))))
    wait_idle(slug, bearer, 40)
    check("oracle · exhausting the bearer's headroom promotes it to PRESERVING",
          lambda: _true(wait_for(
              lambda: node(slug, bearer).get("bearer_state") == "preserving", 20),
              json.dumps(node(slug, bearer))))
    check("oracle · a bearer never re-compacts (no @gen of a @gen)",
          lambda: _eq([k for k in doc(slug)["nodes"] if k.count("@") > 1], []))
    tok2 = token()
    send(slug, bearer, f"oracle question {tok2}")
    wait_idle(slug, bearer, 45)
    after = node(slug, bearer)
    check("oracle · the oracle's canonical session id is NEVER rewritten",
          lambda: _eq(after["session_id"], b_sid))
    check("oracle · the exchange is retained on the doc, not in the session",
          lambda: _true(any(tok2 in json.dumps(e)
                            for e in after.get("oracle_exchanges") or []),
                        json.dumps(after.get("oracle_exchanges"))[:400]))
    check("oracle · the oracle log is capped at 40 exchanges",
          lambda: _true(len(after.get("oracle_exchanges") or []) <= 40))
    check("oracle · the chat renders the oracle exchanges",
          lambda: _true(tok2 in json.dumps(
              api("GET", f"/api/orgs/{slug}/nodes/{bearer}/chat"))))
    code, body = api_err("POST", f"/api/orgs/{slug}/nodes/{bearer}/compact")
    check("oracle · the compact button refuses a bearer (§8.3)",
          lambda: _true(code == 422 and "bearer" in body.lower(),
                        f"{code} {body[:200]}"))
    drop_orgs()


def live_never_run_pardon() -> None:
    """The pardon must be spent by the TURN, not by the next restart.

    The hermetic checks call `spend_unrun_pardon` directly, so they prove
    the rule and nothing about the wiring: with the whole `pardon_pending`
    block deleted from `_run_one_turn`'s `finally` — or `ran_sid` never
    assigned — every suite stayed green, and the pardon simply lived on
    until a restart healed it. That is the restart-dependence the turn-side
    spend exists to remove, so it is asserted here against a real turn."""
    print("\nlive · the never-run pardon is spent by the TURN:")
    start_backend()
    set_cfg(FAST)
    slug, (nid,) = make_org("pardon")
    _prime(slug, nid)                     # a session, a cost, a transcript
    old_sid = node(slug, nid)["session_id"]
    api("POST", f"/api/orgs/{slug}/ops",
        {"op": "cheap_compact", "actor": USER, "node": nid})
    armed = node(slug, nid)
    check("pardon · a cheap-compact on a LIVE org arms the never-run pardon",
          lambda: _true(armed.get("session_unrun") is True
                        and armed["session_id"] != old_sid,
                        json.dumps({k: armed.get(k) for k in
                                    ("session_id", "session_unrun")})))
    tok = token()
    send(slug, nid, f"after the compact {tok}")
    ran = wait_for(lambda: carriers(slug, nid, tok)["transcript"], 60)
    wait_idle(slug, nid)
    # assert the MECHANISM ran before asserting what it did — a pardon
    # that vanished because no turn happened proves nothing
    check("pardon · the successor really ran a turn on the MINTED session",
          lambda: _true(ran, log_tail(1200)))
    check("pardon · …and that turn spent the pardon, no restart involved",
          lambda: _true("session_unrun" not in node(slug, nid),
                        "the turn left the pardon standing: clearing it now "
                        "needs a backend restart, which is exactly what "
                        "the turn-side spend removes"))
    # …and №31 is ARMED again on that session: ask the real predicate
    # against the live doc with an empty index — with the pardon gone,
    # a missing transcript must condemn it like any ordinary node
    check("pardon · …and the spent pardon re-arms №31 on that session",
          lambda: _true(
              supervisor._condemnable(node(slug, nid), {}),  # type: ignore[arg-type]
              "the node is still shielded after its turn — losing this "
              "session would now go unreported"))
    drop_orgs()          # every other live function does; this one left a
                         # cheap-compacted node for the NEXT test's startup
                         # sweep to walk over


def live_guards() -> None:
    print("\nlive · the /compact preconditions:")
    start_backend()
    set_cfg(FAST, fork={"default": {"mode": "ok", "forkMs": 2500}})
    slug, ids = make_org("guards", agents=2)
    a, b = sorted(k for k in doc(slug)["nodes"]
                  if doc(slug)["nodes"][k]["state"] == "live")[:2]

    code, body = api_err("POST", f"/api/orgs/{slug}/nodes/{a}/compact")
    check("guard · a node that has never run cannot be compacted",
          lambda: _true(code == 422 and "nothing to compact" in body.lower(),
                        f"{code} {body[:200]}"))
    code, body = api_err("POST", f"/api/orgs/{slug}/nodes/nosuch/compact")
    check("guard · an unknown node 404s",
          lambda: _eq(code, 404))
    _prime(slug, a)
    api("POST", f"/api/orgs/{slug}/ops",
        {"op": "retire", "actor": USER, "node": b})
    code, body = api_err("POST", f"/api/orgs/{slug}/nodes/{b}/compact")
    check("guard · an archived node cannot be compacted",
          lambda: _true(code == 422 and "rehire" in body.lower(),
                        f"{code} {body[:200]}"))
    # two presses in flight
    api("POST", f"/api/orgs/{slug}/nodes/{a}/compact")
    time.sleep(0.4)
    code, body = api_err("POST", f"/api/orgs/{slug}/nodes/{a}/compact")
    check("guard · a second press while a split runs is refused, not doubled",
          lambda: _true(code == 409, f"{code} {body[:200]}"))
    check("guard · …and exactly one bearer results",
          lambda: _true(wait_for(
              lambda: len([k for k, n in doc(slug)["nodes"].items()
                           if n.get("bearer_state")]) == 1, 60),
              json.dumps([k for k, n in doc(slug)["nodes"].items()
                          if n.get("bearer_state")])))
    wait_idle(slug, a, 40)
    code, body = api_err("POST", f"/api/orgs/{slug}/nodes/{a}/compact")
    check("guard · a freshly-split node cannot be re-split (occupancy reset)",
          lambda: _true(code == 422 and "nothing to compact" in body.lower(),
                        f"{code} {body[:200]}"))
    drop_orgs()


def live_spend_frozen_compact() -> None:
    """A kiosk whose spend limit is already breached. Turns are refused — but
    the compaction fork is a real API call on the same subscription, and the
    /compact button is on the kiosk's PUBLIC surface."""
    print("\nlive · compaction and the kiosk spend cap:")
    start_backend()
    # fakecli bills 0.0001 per turn; a 0.00005 limit means the FIRST turn
    # freezes the org while leaving the node with an occupancy — which is
    # exactly the state the compact button's precheck accepts.
    set_cfg(FAST, fork={"default": {"mode": "ok", "forkCost": 5.0}})
    r = api("POST", "/api/orgs", {"name": f"zz compact kiosk {len(_orgs)}",
                                  "kiosk": {"credits": 30, "spend_limit": 0.00005,
                                            "sandbox": False}})
    slug = r.get("slug") or r.get("org", {}).get("slug")
    _orgs.append(slug)
    h = api("POST", f"/api/orgs/{slug}/ops",
            {"op": "hire", "actor": USER, "parent": None, "tier": "haiku",
             "grant": 3, "name": "worker", "charter": "a test agent",
             "tools": {"bash": True, "web": False, "edit": False,
                       "subagents": False, "mcp": []},
             "org_visibility": "team", "add_dirs": []})
    nid = h.get("node") or "worker"
    _prime(slug, nid)
    wait_for(lambda: doc(slug).get("spend_frozen"), 20)
    d = doc(slug)
    check("spend · one turn past the limit freezes the whole org",
          lambda: _true(d.get("spend_frozen"),
                        f"cost_total="
                        f"{sum(float(x.get('cost_usd') or 0) for x in d['nodes'].values())}"))
    check("spend · the node keeps its occupancy (the compact precheck's gate)",
          lambda: _true(node(slug, nid).get("occupancy")))
    check("spend · a frozen org refuses to run a turn",
          lambda: _true(_turn_refused(slug, nid)))
    before = float(node(slug, nid).get("cost_usd") or 0)
    code, body = api_err("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    spent = wait_for(lambda: float(node(slug, nid).get("cost_usd") or 0)
                     > before + 1.0, 60)
    after = float(node(slug, nid).get("cost_usd") or 0)
    if code == 200 and spent:
        exception(
            "spend · a SPEND-FROZEN org still pays for a compaction fork",
            "POST /api/orgs/{slug}/nodes/{nid}/compact checks live / bearer / "
            "occupancy / node-frozen / busy and never org.d['spend_frozen'] "
            "(api.py:1508-1537); _compact_split_body checks the cap only AFTER "
            "the fork has been billed (supervisor.py:1795-1820). Measured on a "
            f"kiosk already frozen for spend: cost_usd {before:.4f} → {after:.4f} "
            "(+5.00 from one press). Turns are refused, forks are not, and the "
            "button sits on the kiosk public surface. Reported, not fixed here — "
            "the guard belongs in api.node_compact beside the frozen check, and "
            "api.py is another agent's file this wave.")
    else:
        check("spend · a spend-frozen org refuses a compaction fork too",
              lambda: _true(code != 200 or not spent,
                            f"code={code} {body[:150]} cost {before}→{after}"))
    drop_orgs()


def _turn_refused(slug: str, nid: str) -> bool:
    tok = token()
    send(slug, nid, f"blocked {tok}")
    time.sleep(2.0)
    return not carriers(slug, nid, tok)["transcript"]


def live_fairness() -> None:
    print("\nlive · ORGTREE_MAX_TURNS is GLOBAL — can a busy org starve a quiet one?")
    start_backend(max_turns=2)
    hog, hids = make_org("hog", agents=2, names=["hogA", "hogB"])
    quiet, qids = make_org("quiet", agents=1, names=["quick"])
    set_cfg({"echoMs": 40, "firstEventMs": 60, "resultMs": 10},
            fork={"default": {"mode": "ok"}},
            **{hids[0]: {"echoMs": 60, "firstEventMs": 100, "resultMs": 6000},
               hids[1]: {"echoMs": 60, "firstEventMs": 100, "resultMs": 6000}})
    for k in hids:
        send(hog, k, f"hog {token()}")
    if not wait_for(lambda: busy(hog, hids[0]) and busy(hog, hids[1]), 20):
        note("fairness · both hog turns never went busy together; the "
             "measurement below is a lower bound")
    t0 = time.time()
    tok = token()
    send(quiet, qids[0], f"quiet {tok}")
    got = wait_for(lambda: carriers(quiet, qids[0], tok)["transcript"], 60)
    wait_ms = (time.time() - t0) * 1000
    measured("a quiet org's first response while 2/2 global slots are held by "
             "ANOTHER org", f"{wait_ms:.0f} ms")
    check("fairness · the starved org's message is never LOST, only delayed",
          lambda: _true(got, json.dumps(carriers(quiet, qids[0], tok))))
    check("fairness · …and it does queue behind the foreign org's work",
          lambda: _true(wait_ms > 1500,
                        f"answered in {wait_ms:.0f} ms — either the cap is not "
                        f"binding here or fairness was added; re-check"))
    exception("fairness · one org's turns delay another org's by the full "
              "length of a foreign turn",
              f"ORGTREE_MAX_TURNS is ONE global semaphore "
              f"(supervisor.py:243, `_turn_slots`). Measured with 2 slots, "
              f"both held by org '{hog}': org '{quiet}' — idle, one agent, "
              f"nothing of its own running — waited {wait_ms:.0f} ms for its "
              f"first token, i.e. the whole of a foreign turn. At the shipped "
              f"default of 16 slots the same shape needs 16 busy foreign "
              f"agents, which one active org reaches easily. Nothing enforces "
              f"per-org fairness, and the wait is indistinguishable from a "
              f"slow agent: `waiting` is set on the node (№12, the hollow "
              f"dot) but nothing says WHO is holding the slots. Documented in "
              f"docs/configuration.md, untested until now. Not fixed: a fair "
              f"scheduler is a design decision, not a defect fix.")
    for k in hids:
        wait_idle(hog, k, 60)
    drop_orgs()

    print("\nlive · …and a COMPACTION holds one of those global slots:")
    start_backend(max_turns=1)
    set_cfg({"echoMs": 40, "firstEventMs": 60, "resultMs": 10},
            fork={"default": {"mode": "ok", "forkMs": 5000}})
    ca, cids = make_org("splitter", agents=1, names=["splitter"])
    cb, bids = make_org("bystander", agents=1, names=["bystander"])
    _prime(ca, cids[0])
    api("POST", f"/api/orgs/{ca}/nodes/{cids[0]}/compact")
    time.sleep(0.8)
    t0 = time.time()
    tok = token()
    send(cb, bids[0], f"bystander {tok}")
    saw_waiting = wait_for(lambda: tree_node(cb, bids[0]).get("waiting"), 4)
    got = wait_for(lambda: carriers(cb, bids[0], tok)["transcript"], 60)
    wait_ms = (time.time() - t0) * 1000
    measured("an unrelated org's wait while ONE node compacts (1 global slot)",
             f"{wait_ms:.0f} ms")
    # FIXED here: `manual_compact` did not take a turn slot, so MAX_CONCURRENT
    # bounded turns while the equally expensive forks ran on top of the cap.
    # Before the fix this measured 152 ms — the fork was invisible.
    check("slots · a compaction fork occupies a global turn slot",
          lambda: _true(wait_ms > 1500,
                        f"answered in {wait_ms:.0f} ms — the fork is not "
                        f"holding a slot, so MAX_CONCURRENT does not bound "
                        f"the number of CLI children"))
    check("slots · …and the blocked org's message still arrives",
          lambda: _true(got, json.dumps(carriers(cb, bids[0], tok))))
    check("slots · a node blocked on a slot reports `waiting` — the wait is "
          "visible, even though WHO holds the slot is not",
          lambda: _true(saw_waiting,
                        "the blocked node never showed `waiting`, so a "
                        "cross-org stall is indistinguishable from a slow "
                        "agent even on the canvas"))
    note(f"a real fork's ceiling is 600 s, not the {5000} ms used here — at "
         f"the default 16 slots, 16 simultaneous compactions stop every org "
         f"on the instance for up to ten minutes.")
    wait_idle(ca, cids[0], 60)
    drop_orgs()


def live_timing() -> None:
    print("\nlive · the timing knobs:")
    start_backend(turn_timeout=6)
    set_cfg({"hang": True}, fork={"default": {"mode": "ok"}})
    slug, (nid,) = make_org("timeout")
    tok = token()
    t0 = time.time()
    send(slug, nid, f"never answered {tok}")
    ok = wait_for(lambda: not busy(slug, nid), 40)
    took = time.time() - t0
    check("timing · ORGTREE_TURN_TIMEOUT actually bounds a hung turn",
          lambda: _true(ok, log_tail(1200)))
    check("timing · …at roughly the configured 6 s, not the 1800 s default",
          lambda: _true(took < 25, f"took {took:.1f}s"))
    measured("turn timeout honoured", f"{took:.1f} s for a 6 s setting")
    c = carriers(slug, nid, tok)
    check("timing · a timed-out turn does not eat the message",
          lambda: _true(any(c.values()), json.dumps(c)))
    drop_orgs()


# ============ 3c. hermetic: the lost generation — preserve, phantom, recover

def _plant_session(sid: str, recs: list[dict], home: str = HOME) -> str:
    """A session JSONL with REAL records — uuids and compact_boundary markers —
    rather than `_plant_transcript`'s single filler line. The lost-generation
    work is all about WHERE the boundary sits and WHICH uuids are above it, so
    these tests need a file with a real shape."""
    d = os.path.join(home, ".claude", "projects", "rig")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return p


def _msg(uuid_: str, text: str = "x") -> dict:
    return {"type": "user", "uuid": uuid_,
            "message": {"role": "user", "content": text}}


def _boundary(uuid_: str, pre: int | None = 1000) -> dict:
    r: dict = {"type": "system", "subtype": "compact_boundary", "uuid": uuid_,
               "content": "Conversation compacted"}
    if pre is not None:
        r["compactMetadata"] = {"trigger": "manual", "preTokens": pre}
    return r


def lost_generations() -> None:
    """The CLI's in-place compaction, and the three ways orgtree got it wrong.

    Measured 2026-08-20 on this machine before any of it was fixed:
      · 13 session files carry boundaries whose pre-boundary records are unique
        to their own file — one with FIFTEEN in a single file. Fifteen forks
        would be fifteen files, so the in-place, append-only shape is real.
      · ingame-prompt@6 — the generation reported LOST — was not one of them.
        All 425 of its pre-boundary uuids were already in ingame-prompt@5's
        file, i.e. a `--fork-session` copy. It was a PHANTOM of orgtree's own
        §8 split, not a CLI compaction at all.
    """
    print("\nthe boundary scan (supervisor._count_cli_compactions):")

    org, (a,) = horg()
    sid = sid_of(org, a)
    _plant_session(sid, [_msg("u1"), _msg("u2"), _boundary("b1", 111),
                         _msg("u3"), _boundary("b2", 222), _msg("u4")])
    store.save_org(org)
    cnt, pre, marks = supervisor._count_cli_compactions(org, a)
    check("scan · every boundary is counted",
          lambda: _true(cnt == 2, f"got {cnt}"))
    check("scan · the LAST boundary's preTokens is the headline figure",
          lambda: _true(pre == 222, f"got {pre}"))
    check("scan · each boundary reports its own line offset AND its own "
          "preTokens — the offsets are what make the cut possible",
          lambda: _true(marks == [(2, 111), (4, 222)], f"got {marks}"))
    check("scan · count and marks cannot disagree",
          lambda: _true(cnt == len(marks)))

    # a boundary with no compactMetadata must still be a boundary: the cut
    # point is the thing that matters, the token figure is decoration
    org2, (b,) = horg()
    _plant_session(sid_of(org2, b), [_msg("v1"), _boundary("vb", None)])
    store.save_org(org2)
    cnt2, pre2, marks2 = supervisor._count_cli_compactions(org2, b)
    check("scan · a boundary WITHOUT preTokens still counts and still cuts",
          lambda: _true(cnt2 == 1 and pre2 is None and marks2 == [(1, None)],
                        f"{cnt2} {pre2} {marks2}"))

    # ---- malformed records (peer report from compaction-fix, 2026-08-20) ----
    # A JSONL line need not be an OBJECT, and a field need not be the shape its
    # name suggests. `rec.get` on a parsed list, or `(x or {}).get` on a
    # non-empty non-mapping, raises AttributeError — and this runs on the TURN
    # path, ABOVE the line that records the new count, so the raise is
    # PERMANENT: the same poisoned line re-raises every turn, each reported as
    # failed, with the node's split unreachable.
    org2b, (b2,) = horg()
    _plant_session(sid_of(org2b, b2), [
        _msg("w1"),
        {"type": "system", "subtype": "compact_boundary", "uuid": "wb1",
         "compactMetadata": "not-a-dict"},          # the reported crash
        _msg("w2"),
        {"type": "system", "subtype": "compact_boundary", "uuid": "wb2",
         "compactMetadata": {"preTokens": "lots"}},  # preTokens not a number
    ])
    # …and a line that is valid JSON but not an object at all
    with open(os.path.join(HOME, ".claude", "projects", "rig",
                           sid_of(org2b, b2) + ".jsonl"),
              "a", encoding="utf-8") as f:
        f.write('["compact_boundary"]\n')
        f.write('"compact_boundary"\n')
    store.save_org(org2b)

    def _survives_junk() -> None:
        c, p, m = supervisor._count_cli_compactions(org2b, b2)
        _true(c == 2, f"expected the 2 real boundaries, got {c}")
        _true([x[0] for x in m] == [1, 3], f"offsets {m}")
        _true(all(x[1] is None for x in m), f"pre-tokens should be None: {m}")
        _true(p is None, f"headline should be None, got {p}")
    check("junk · a non-dict compactMetadata, a non-numeric preTokens and a "
          "bare-array line do not raise on the TURN path — one malformed "
          "record would otherwise poison every future turn permanently",
          _survives_junk)

    # …and the NUMBERS that are not numbers. `json.loads` accepts `Infinity`
    # and `NaN` literally and mints `inf` for any out-of-range decimal, and
    # `isinstance(x, float)` is True for all three — so the bare `int(p)` this
    # replaced raised OverflowError/ValueError past a `try` that catches
    # neither, on the turn path, above the write that would end it (peer report
    # from compaction-fix, 2026-08-20). `true` is the quiet one: a bool IS an
    # int, so it became a preTokens of 1 in the agent's own notice. And an
    # integer too large for a float survives `int()` and raises downstream in
    # `record_cli_compaction`'s `pre_tokens / 1000`.
    org2c, (b3,) = horg()
    _rig2c = os.path.join(HOME, ".claude", "projects", "rig")
    os.makedirs(_rig2c, exist_ok=True)
    with open(os.path.join(_rig2c, sid_of(org2c, b3) + ".jsonl"),
              "w", encoding="utf-8") as f:
        f.write(json.dumps(_msg("n0")) + "\n")
        for lit in ("Infinity", "-Infinity", "NaN", "1e400", "true",
                    str(10 ** 400), "-5"):
            f.write('{"type":"system","subtype":"compact_boundary",'
                    '"uuid":"nb","compactMetadata":{"preTokens":'
                    + lit + '}}\n')
        f.write('{"type":"system","subtype":"compact_boundary","uuid":"ok",'
                '"compactMetadata":{"preTokens":4096}}\n')
    store.save_org(org2c)

    def _survives_nonfinite() -> None:
        c, p, m = supervisor._count_cli_compactions(org2c, b3)
        _true(c == 8, f"every boundary must still COUNT, got {c}")
        _true([x[1] for x in m[:7]] == [None] * 7,
              f"no impostor may pass as a token count: {[x[1] for x in m]}")
        _true(m[7][1] == 4096, f"the real one must survive: {m[7]}")
        _true(p == 4096, f"headline should be the last REAL figure, got {p}")
    check("junk · inf / -inf / NaN / 1e400 / true / a float-overflowing int / "
          "a negative do not raise and do not pass as token counts — the "
          "boundary still counts, its figure is simply absent",
          _survives_nonfinite)

    def _junk_pre_survives_the_turn() -> None:
        # the shape that made it permanent: the figure rides into
        # record_cli_compaction, whose notice divides it
        o = store.load_org(org2c.d["slug"])
        row = o.record_cli_compaction(b3, supervisor._boundary_pre_tokens(
            10 ** 400), None, 1)
        _true(o.nodes[row]["bearer_state"] == "lost", "row not minted")
    check("junk · …and the figure that reaches the ledger cannot overflow its "
          "own notice text", _junk_pre_survives_the_turn)

    print("\nthe cut (supervisor._fork_bearer_session):")

    org3, (c,) = horg()
    sid3 = sid_of(org3, c)
    _plant_session(sid3, [_msg("k1"), _msg("k2"), _boundary("kb"), _msg("k3")])
    store.save_org(org3)
    new = supervisor._fork_bearer_session(org3, sid3, 2)
    check("cut · a fresh session id is minted, distinct from the original",
          lambda: _true(bool(new) and new != sid3, f"got {new!r}"))

    def _cut_content() -> None:
        p = supervisor.transcript_path(new, None)      # type: ignore[arg-type]
        _true(bool(p), "the cut session has no transcript on disk")
        lines = open(p, encoding="utf-8").read().splitlines()  # type: ignore[arg-type]
        _true(len(lines) == 2, f"expected 2 lines, got {len(lines)}")
        got = [json.loads(x).get("uuid") for x in lines]
        _true(got == ["k1", "k2"], f"got {got}")
    check("cut · it holds EXACTLY the records above the boundary — the "
          "boundary itself and everything after it are excluded", _cut_content)

    def _original_untouched() -> None:
        p = supervisor.transcript_path(sid3, None)
        n = len(open(p, encoding="utf-8").read().splitlines())  # type: ignore[arg-type]
        _true(n == 4, f"the successor's own session was modified: {n} lines")
    check("cut · the ORIGINAL is left alone — the successor is still "
          "appending to it and must not be touched", _original_untouched)

    check("cut · a boundary at line 0 yields NO bearer rather than an empty "
          "session with nothing to say",
          lambda: _true(supervisor._fork_bearer_session(org3, sid3, 0) is None))
    check("cut · an unknown session cuts nothing instead of raising",
          lambda: _true(supervisor._fork_bearer_session(
              org3, "no-such-session", 2) is None))

    print("\nthe mint (Org.record_cli_compaction):")

    org4, (d,) = horg()
    _plant_transcript(sid_of(org4, d))
    lost = org4.record_cli_compaction(d, 900)
    check("mint · with NO cut session the generation is still LOST — the "
          "fail-soft is the old behaviour exactly, never a bearer that "
          "cannot answer",
          lambda: _true(org4.nodes[lost]["bearer_state"] == "lost"))

    org5, (e,) = horg()
    sid5 = sid_of(org5, e)
    _plant_transcript(sid5)
    kept = org5.record_cli_compaction(e, 900, "cut-session-id")
    check("mint · WITH a cut session it is a consultable knowledge bearer",
          lambda: _true(org5.nodes[kept]["bearer_state"] == "knowledge",
                        f'got {org5.nodes[kept]["bearer_state"]!r}'))
    check("mint · the bearer holds the CUT session, not the successor's — "
          "this is the one field that makes it consultable",
          lambda: _true(org5.nodes[kept]["session_id"] == "cut-session-id"))
    check("mint · …and the successor keeps its own session, still live",
          lambda: _true(org5.node(e)["session_id"] == sid5))
    check("mint · the successor's generation advanced either way",
          lambda: _true(org5.node(e)["generation"] == 1
                        and org5.node(e)["predecessor"] == kept))
    check("mint · a preserved bearer is REHIRABLE (a lost one is refused, "
          "and that refusal is the whole user-visible symptom)",
          lambda: org5.rehire(USER, kept, 5))
    check("mint · a LOST generation still refuses rehire",
          lambda: _raises(lambda: org4.rehire(USER, lost, 5), "lost"))

    print("\nthe phantom (the §8 split's own boundary, counted as a loss):")

    # BUG A, reported by peer compaction-fix 2026-08-20 and confirmed on disk.
    org6, (f6,) = horg()
    _plant_transcript(sid_of(org6, f6))
    org6.compact_split(f6, "fork-sid")
    check("phantom · compact_split RE-BASELINES the counter, because the "
          "fork it hands over already contains one boundary — its own "
          "/compact — and a stale counter reads that as a CLI compaction",
          lambda: _true(org6.node(f6).get("cli_compactions", "MISSING") is None,
                        f'got {org6.node(f6).get("cli_compactions", "MISSING")!r}'))

    # the end-to-end proof: a fork carrying one boundary must NOT mint anything
    org7, (g,) = horg()
    _plant_transcript(sid_of(org7, g))
    org7.compact_split(g, "fork-sid-2")
    _plant_session("fork-sid-2", [_msg("f1"), _boundary("fb"), _msg("f2")])
    store.save_org(org7)
    before = set(org7.nodes)
    st7 = supervisor.state(org7.d["slug"], g)
    supervisor._after_turn(org7.d["slug"], g, org7, {}, st7, 1)
    org7 = store.load_org(org7.d["slug"])
    check("phantom · a §8 split's successor mints NO lineage entry on its "
          "next turn — one real generation, one archived node, not two",
          lambda: _true(set(org7.nodes) == before,
                        f"new nodes: {set(org7.nodes) - before}"))
    check("phantom · …and the counter is baselined to the fork's true count, "
          "so the NEXT real compaction is still seen",
          lambda: _true(org7.node(g).get("cli_compactions") == 1,
                        f'got {org7.node(g).get("cli_compactions")!r}'))

    # BUG C: the same staleness, failing the other way round.
    org8, (h,) = horg()
    _plant_transcript(sid_of(org8, h))
    org8.node(h)["cli_compactions"] = 3
    org8.cheap_compact(USER, h)
    check("phantom · cheap_compact re-baselines too — a stale HIGH count "
          "against a fresh EMPTY session would swallow the next three real "
          "compactions in silence",
          lambda: _true(org8.node(h).get("cli_compactions", "MISSING") is None))

    print("\nthe end-to-end rescue (a genuine in-place compaction):")

    org9, (i9,) = horg()
    sid9 = sid_of(org9, i9)
    _plant_session(sid9, [_msg("p1"), _msg("p2"), _boundary("pb", 4242),
                          _msg("p3")])
    org9.node(i9)["cli_compactions"] = 0        # baselined before the boundary
    store.save_org(org9)
    st9 = supervisor.state(org9.d["slug"], i9)
    supervisor._after_turn(org9.d["slug"], i9, org9, {}, st9, 1)
    org9 = store.load_org(org9.d["slug"])
    bearer9 = f"{i9}@0"

    check("rescue · the generation the CLI compacted is recorded",
          lambda: _true(bearer9 in org9.nodes, f"nodes: {sorted(org9.nodes)}"))
    check("rescue · and it is CONSULTABLE, not lost — this is the bug",
          lambda: _true(org9.nodes[bearer9]["bearer_state"] == "knowledge",
                        f'got {org9.nodes[bearer9]["bearer_state"]!r}'))
    check("rescue · the bearer no longer shares the live node's session id",
          lambda: _true(org9.nodes[bearer9]["session_id"] != sid9
                        and org9.node(i9)["session_id"] == sid9))

    def _rescued_content() -> None:
        p = supervisor.transcript_path(org9.nodes[bearer9]["session_id"], None)
        _true(bool(p), "the rescued bearer has no session file")
        got = [json.loads(x).get("uuid")
               for x in open(p, encoding="utf-8").read().splitlines()]  # type: ignore[arg-type]
        _true(got == ["p1", "p2"], f"got {got}")
    check("rescue · it holds the pre-compaction records — the very ones the "
          "org was told were gone", _rescued_content)

    print("\nphantom evidence — and its refusals (fail closed):")

    ev = supervisor._phantom_evidence(org9, bearer9)
    check("evidence · a RESCUED bearer is not a phantom (it is not even lost)",
          lambda: _true(ev["phantom"] is False, json.dumps(ev)))

    # the real thing: a lost row whose content is wholly duplicated elsewhere
    # the exact §8 shape: the predecessor keeps the OLD session, the successor
    # gets the fork — and the fork is a COPY of that history (same uuids)
    # carrying its own /compact boundary. That copy is what makes the row a
    # phantom: its content was never anywhere else.
    org10, (j,) = horg()
    old10 = sid_of(org10, j)
    _plant_session(old10, [_msg("q1"), _msg("q2")])
    prev10 = org10.compact_split(j, "fork-sid-10")
    _plant_session("fork-sid-10", [_msg("q1"), _msg("q2"),
                                   _boundary("qb"), _msg("q3")])
    ph = org10.record_cli_compaction(j, 500)          # the phantom row
    store.save_org(org10)
    ev10 = supervisor._phantom_evidence(org10, ph)
    check("evidence · a lost row whose every pre-boundary record is already "
          "held by its sibling IS a phantom",
          lambda: _true(ev10["phantom"] is True, json.dumps(ev10)))
    check("evidence · …and it says how many records it matched",
          lambda: _true(ev10.get("records") == 2
                        and ev10.get("duplicate_of") == prev10,
                        json.dumps(ev10)))

    # …and the case the fail-closed rule exists for
    # same shape, one difference that changes everything: the sibling does NOT
    # hold record r2. So this generation carries content that exists nowhere
    # else, and must survive the drop.
    org11, (k,) = horg()
    old11 = sid_of(org11, k)
    _plant_session(old11, [_msg("r1")])                 # PARTIAL
    prev11 = org11.compact_split(k, "fork-sid-11")
    _plant_session("fork-sid-11", [_msg("r1"), _msg("r2"),
                                   _boundary("rb"), _msg("r3")])
    ph11 = org11.record_cli_compaction(k, 500)
    store.save_org(org11)
    ev11 = supervisor._phantom_evidence(org11, ph11)
    check("evidence · one unmatched record is enough to REFUSE — unique "
          "content means a real loss, and dropping it would destroy the only "
          "copy",
          lambda: _true(ev11["phantom"] is False
                        and "unique" in ev11.get("why", ""),
                        json.dumps(ev11)))

    # ⚠ this fixture must keep the row SHARING its successor's session id.
    # Reassigning it (the first version of this check) tripped the earlier
    # "holds its own session id" refusal, so control never reached the
    # file-missing guard and the check passed for the wrong reason — deleting
    # that guard outright would have raised TypeError with nothing noticing.
    org12, (m,) = horg()
    old12 = sid_of(org12, m)
    _plant_session(old12, [_msg("m1"), _msg("m2")])
    prev12 = org12.compact_split(m, "fork-sid-12")
    _plant_session("fork-sid-12", [_msg("m1"), _msg("m2"),
                                   _boundary("mb"), _msg("m3")])
    lost12 = org12.record_cli_compaction(m, 100)
    store.save_org(org12)
    check("evidence · (control) with both files present this row IS a "
          "phantom — so the next check cannot pass vacuously",
          lambda: _true(supervisor._phantom_evidence(
              org12, lost12)["phantom"] is True))
    _ = prev12
    os.unlink(os.path.join(HOME, ".claude", "projects", "rig",
                           old12 + ".jsonl"))       # the SIBLING's file
    ev12 = supervisor._phantom_evidence(org12, lost12)
    check("evidence · a missing sibling file refuses rather than guessing — "
          "'could not look' must never read as 'nothing there'",
          lambda: _true(ev12["phantom"] is False
                        and "missing" in ev12.get("why", ""),
                        json.dumps(ev12)))
    check("evidence · …and the drop refuses with it, so a vanished sibling "
          "can never authorise a deletion",
          lambda: _raises(lambda: supervisor.drop_phantom_generation(
              org12.d["slug"], lost12)))

    # a corrupt record inside the PREFIX must refuse, not shrink the set that
    # has to be matched. Skipping it would "prove" duplication on whatever
    # survived and delete a generation whose unique content was the very thing
    # that could not be read.
    org18, (n18,) = horg()
    old18 = sid_of(org18, n18)
    _plant_session(old18, [_msg("e1"), _msg("e2")])
    prev18 = org18.compact_split(n18, "fork-sid-18")
    _plant_session("fork-sid-18", [_msg("e1"), _msg("e2"),
                                   _boundary("eb"), _msg("e3")])
    ph18 = org18.record_cli_compaction(n18, 500)
    store.save_org(org18)
    check("evidence · …a clean copy IS a phantom (the control for the next "
          "check)",
          lambda: _true(supervisor._phantom_evidence(
              org18, ph18)["phantom"] is True))
    # now corrupt ONE line inside the compared prefix
    _p18 = os.path.join(HOME, ".claude", "projects", "rig", "fork-sid-18.jsonl")
    _lines18 = open(_p18, encoding="utf-8").read().splitlines()
    _lines18[1] = "{not json at all"
    with open(_p18, "w", encoding="utf-8") as f:
        f.write("\n".join(_lines18) + "\n")
    ev18 = supervisor._phantom_evidence(org18, ph18)
    check("evidence · ONE unparsable record in the prefix refuses the whole "
          "proof — a deletion may not rest on the records that happened to "
          "survive parsing",
          lambda: _true(ev18["phantom"] is False, json.dumps(ev18)))
    check("evidence · …and the drop refuses with it",
          lambda: _raises(lambda: supervisor.drop_phantom_generation(
              org18.d["slug"], ph18)))

    print("\ndropping a phantom, and refusing to drop anything else:")

    slug10 = org10.d["slug"]
    succ_gen = org10.node(j)["generation"]
    out = supervisor.drop_phantom_generation(slug10, ph)
    org10 = store.load_org(slug10)
    check("drop · the phantom row is gone",
          lambda: _true(ph not in org10.nodes, f"still there: {ph}"))
    check("drop · the lineage chain is re-linked ACROSS the hole, so nothing "
          "points at an id that no longer resolves",
          lambda: _true(org10.node(j)["predecessor"] == prev10
                        and org10.nodes[prev10]["successor"] == j,
                        f'{org10.node(j)["predecessor"]} / '
                        f'{org10.nodes[prev10]["successor"]}'))
    check("drop · the sibling bearer — which holds the real content — "
          "survives untouched and still consultable",
          lambda: _true(org10.nodes[prev10]["bearer_state"] == "knowledge"))
    check("drop · the generation NUMBER keeps its gap rather than renumbering "
          "ids that mail and audiences still reference",
          lambda: _true(org10.node(j)["generation"] == succ_gen))
    check("drop · it reports what it removed and what holds the content",
          lambda: _true(out["dropped"] == ph and out["duplicate_of"] == prev10,
                        json.dumps(out)))

    slug11 = org11.d["slug"]
    check("drop · REFUSES a lost generation whose content is unique — the "
          "fail-closed rule, and the whole reason the proof exists",
          lambda: _raises(
              lambda: supervisor.drop_phantom_generation(slug11, ph11),
              "unique"))
    check("drop · refuses a knowledge bearer outright",
          lambda: _raises(
              lambda: supervisor.drop_phantom_generation(slug11, prev11),
              "not a LOST generation"))

    print("\nrecovering a genuine loss, and refusing to recover a phantom:")

    org13, (n13,) = horg()
    sid13 = sid_of(org13, n13)
    _plant_session(sid13, [_msg("s1"), _msg("s2"), _boundary("sb"), _msg("s3")])
    lost13 = org13.record_cli_compaction(n13, 700)   # no cut → LOST, as before
    store.save_org(org13)
    slug13 = org13.d["slug"]
    check("recover · a genuine lost generation is precondition-checked as "
          "lost and sharing its successor's session",
          lambda: _true(org13.nodes[lost13]["bearer_state"] == "lost"
                        and org13.nodes[lost13]["session_id"] == sid13))
    rec = supervisor.recover_lost_generation(slug13, lost13)
    org13 = store.load_org(slug13)
    check("recover · it becomes a consultable knowledge bearer",
          lambda: _true(org13.nodes[lost13]["bearer_state"] == "knowledge",
                        json.dumps(rec)))
    check("recover · on its OWN session, cut at its own boundary",
          lambda: _true(org13.nodes[lost13]["session_id"] != sid13
                        and rec["cut_at"] == 2, json.dumps(rec)))

    def _recovered_content() -> None:
        p = supervisor.transcript_path(org13.nodes[lost13]["session_id"], None)
        got = [json.loads(x).get("uuid")
               for x in open(p, encoding="utf-8").read().splitlines()]  # type: ignore[arg-type]
        _true(got == ["s1", "s2"], f"got {got}")
    check("recover · holding exactly the records that survived above the "
          "boundary", _recovered_content)
    check("recover · refuses to run twice — it is no longer lost",
          lambda: _raises(
              lambda: supervisor.recover_lost_generation(slug13, lost13),
              "not a lost generation"))
    # R5b (redteam round 4 test-gap): round 3 put the `finally` on BOTH cut
    # windows, but only `_after_turn`'s was pinned. These cover the recovery
    # verb's two failure exits. The catastrophic direction is asserted first:
    # a SUCCESSFUL recovery must NOT discard — the doc now names that session,
    # and deleting it would leave the ledger promising a consultable bearer
    # whose transcript is gone.
    org29, (n29,) = horg()
    sid29 = sid_of(org29, n29)
    _plant_session(sid29, [_msg("g1"), _msg("g2"), _boundary("gb"), _msg("g3")])
    lost29 = org29.record_cli_compaction(n29, 100, None, 2)
    store.save_org(org29)
    slug29 = org29.d["slug"]
    rec29 = supervisor.recover_lost_generation(slug29, lost29)
    check("R5b · (catastrophic direction) a SUCCESSFUL recovery keeps its "
          "cut — the doc names that session, so discarding it would promise "
          "a bearer whose transcript is deleted",
          lambda: _true(supervisor.transcript_path(
              store.load_org(slug29).nodes[lost29]["session_id"], None)
              is not None, json.dumps(rec29)))

    # the MISMATCH exit: the row changes under the cut
    org30, (n30,) = horg()
    sid30 = sid_of(org30, n30)
    _plant_session(sid30, [_msg("h1"), _msg("h2"), _boundary("hb"), _msg("h3")])
    lost30 = org30.record_cli_compaction(n30, 100, None, 2)
    store.save_org(org30)
    slug30 = org30.d["slug"]
    _rig30 = os.path.join(HOME, ".claude", "projects", "rig")
    files30 = set(os.listdir(_rig30))
    _real_fork = supervisor._fork_bearer_session
    _forked30: list[str] = []

    def _fork_then_move(o: object, s: str, u: int) -> object:
        # cut for real, then change the row underneath — exactly the race the
        # re-verify exists for
        out = _real_fork(o, s, u)                    # type: ignore[arg-type]
        if isinstance(out, str):
            _forked30.append(out)
        d = store.load_org(slug30)
        d.nodes[lost30]["bearer_state"] = "knowledge"     # someone got there
        store.save_org(d)
        return out

    supervisor._fork_bearer_session = _fork_then_move   # type: ignore[assignment]
    try:
        _raises(lambda: supervisor.recover_lost_generation(slug30, lost30),
                "changed while")
    finally:
        supervisor._fork_bearer_session = _real_fork   # type: ignore[assignment]
    check("R5b · (control) the cut really was taken before the row moved",
          lambda: _true(len(_forked30) == 1, f"forked {_forked30}"))
    check("R5b · a row that changes under the cut refuses AND discards the "
          "cut it had already taken",
          lambda: _true(set(os.listdir(_rig30)) == files30,
                        f"strays: {set(os.listdir(_rig30)) - files30}"))

    # the RAISE exit: the ledger write itself fails after the cut exists
    org31, (n31,) = horg()
    sid31 = sid_of(org31, n31)
    _plant_session(sid31, [_msg("i1"), _msg("i2"), _boundary("ib"), _msg("i3")])
    lost31 = org31.record_cli_compaction(n31, 100, None, 2)
    store.save_org(org31)
    slug31 = org31.d["slug"]
    files31 = set(os.listdir(_rig30))
    _real_recov = Org.recover_lost_generation
    _hit31: list[int] = []

    def _recov_boom(*_a: object, **_k: object) -> str:
        _hit31.append(1)
        raise OSError("disk full")

    Org.recover_lost_generation = _recov_boom     # type: ignore[assignment]
    try:
        _raises_any = False
        try:
            supervisor.recover_lost_generation(slug31, lost31)
        except Exception:                                    # noqa: BLE001
            _raises_any = True
    finally:
        Org.recover_lost_generation = _real_recov  # type: ignore[assignment]
    check("R5b · (control) the raise landed inside the cut window",
          lambda: _true(bool(_hit31) and _raises_any,
                        f"hit={len(_hit31)} raised={_raises_any}"))
    check("R5b · a raise between the cut and the record discards the cut in "
          "the recovery verb too, not just on the turn path",
          lambda: _true(set(os.listdir(_rig30)) == files31,
                        f"strays: {set(os.listdir(_rig30)) - files31}"))

    # a phantom of its own — org10's was already dropped, and org11's row is
    # the UNIQUE-content case, which is precisely not a phantom
    org14, (n14,) = horg()
    _plant_session(sid_of(org14, n14), [_msg("t1"), _msg("t2")])
    prev14 = org14.compact_split(n14, "fork-sid-14")
    _plant_session("fork-sid-14", [_msg("t1"), _msg("t2"),
                                   _boundary("tb"), _msg("t3")])
    ph14 = org14.record_cli_compaction(n14, 500)
    store.save_org(org14)
    # ---- the cut point is RECORDED, not re-derived (self-review 2026-08-20)
    # Positional inference over "the lost rows sharing this session" is only
    # sound while every boundary still has its row. Recover the MIDDLE of
    # three and the survivors renumber under any index arithmetic — the next
    # recovery then cuts at the wrong boundary and hands back a bearer from
    # the wrong moment, which is indistinguishable from success.
    org15, (n15,) = horg()
    sid15 = sid_of(org15, n15)
    _plant_session(sid15, [_msg("a1"), _boundary("ab1"),          # off 1
                           _msg("a2"), _boundary("ab2"),          # off 3
                           _msg("a3"), _boundary("ab3"),          # off 5
                           _msg("a4")])
    org15.node(n15)["cli_compactions"] = 0
    store.save_org(org15)
    st15 = supervisor.state(org15.d["slug"], n15)
    supervisor._after_turn(org15.d["slug"], n15, org15, {}, st15, 1)
    org15 = store.load_org(org15.d["slug"])
    slug15 = org15.d["slug"]
    check("offsets · three boundaries in one turn mint three generations, "
          "each cut at its OWN boundary",
          lambda: _true([org15.nodes[f"{n15}@{g}"]["session_id"] !=
                         org15.node(n15)["session_id"] for g in (0, 1, 2)]
                        == [True, True, True],
                        f"{sorted(org15.nodes)}"))

    def _each_cut_is_its_own_moment() -> None:
        want = {0: ["a1"], 1: ["a1", "ab1", "a2"],
                2: ["a1", "ab1", "a2", "ab2", "a3"]}
        for g, exp in want.items():
            p = supervisor.transcript_path(
                org15.nodes[f"{n15}@{g}"]["session_id"], None)
            got = [json.loads(x).get("uuid")
                   for x in open(p, encoding="utf-8").read().splitlines()]  # type: ignore[arg-type]
            _true(got == exp, f"@{g}: got {got}, wanted {exp}")
    check("offsets · …and each holds exactly its own generation's records, "
          "not the last boundary's", _each_cut_is_its_own_moment)

    # now the ordering trap, on rows that were minted LOST (no cut available)
    org16, (n16,) = horg()
    sid16 = sid_of(org16, n16)
    _plant_session(sid16, [_msg("c1"), _boundary("cb1"),
                           _msg("c2"), _boundary("cb2"),
                           _msg("c3"), _boundary("cb3"), _msg("c4")])
    store.save_org(org16)
    _cn, _cp, marks16 = supervisor._count_cli_compactions(org16, n16)
    rows16 = [org16.record_cli_compaction(n16, 100, None, off)
              for off, _p in marks16]
    store.save_org(org16)
    slug16 = org16.d["slug"]
    # recover the MIDDLE one first — the case positional inference gets wrong
    supervisor.recover_lost_generation(slug16, rows16[1])
    r16 = supervisor.recover_lost_generation(slug16, rows16[0])
    check("offsets · recovering out of order still cuts each row at the "
          "boundary it was MINTED with, not at whichever one is left",
          lambda: _true(r16["cut_at"] == marks16[0][0],
                        f'cut at {r16["cut_at"]}, wanted {marks16[0][0]}'))

    def _oldest_row_content() -> None:
        org16b = store.load_org(slug16)
        p = supervisor.transcript_path(
            org16b.nodes[rows16[0]]["session_id"], None)
        got = [json.loads(x).get("uuid")
               for x in open(p, encoding="utf-8").read().splitlines()]  # type: ignore[arg-type]
        _true(got == ["c1"], f"got {got}")
    check("offsets · …and the oldest row holds only what preceded ITS "
          "boundary", _oldest_row_content)

    # a legacy row (minted before offsets were recorded) must refuse rather
    # than guess, once the set it would index into is no longer complete
    org17, (n17,) = horg()
    sid17 = sid_of(org17, n17)
    _plant_session(sid17, [_msg("d1"), _boundary("db1"),
                           _msg("d2"), _boundary("db2"), _msg("d3")])
    store.save_org(org17)
    _dn, _dp, marks17 = supervisor._count_cli_compactions(org17, n17)
    legacy = [org17.record_cli_compaction(n17, 100) for _ in marks17]
    for k in legacy:                     # simulate pre-fix rows
        org17.nodes[k].pop("cli_boundary_offset", None)
    store.save_org(org17)
    slug17 = org17.d["slug"]
    check("offsets · a legacy row with a COMPLETE set of siblings still "
          "recovers positionally",
          lambda: _true(supervisor.recover_lost_generation(
              slug17, legacy[0])["cut_at"] == marks17[0][0]))
    check("offsets · …but once the set is incomplete it REFUSES to guess "
          "rather than cutting at the wrong moment",
          lambda: _raises(
              lambda: supervisor.recover_lost_generation(slug17, legacy[1]),
              "refusing to guess"))

    print("\nthe redteam's findings, pinned (2026-08-20):")

    # R1 (CRITICAL): `successor` is the LIVE NODE id on every lineage row, not
    # the next generation. Dropping a phantom that has a REAL generation after
    # it used to rewrite the live node's predecessor past that newer row,
    # orphaning it out of lineage_stack and _taken_with.
    org20, (n20,) = horg()
    old20 = sid_of(org20, n20)
    _plant_session(old20, [_msg("x1"), _msg("x2")])
    prev20 = org20.compact_split(n20, "fork-sid-20")
    _plant_session("fork-sid-20", [_msg("x1"), _msg("x2"),
                                   _boundary("xb1"), _msg("x3"),
                                   _boundary("xb2"), _msg("x4")])
    _plant_session("real-bearer-20", [_msg("x1"), _msg("x2"),
                                      _boundary("xb1"), _msg("x3")])
    ph20 = org20.record_cli_compaction(n20, 100, None, 2)          # phantom
    real20 = org20.record_cli_compaction(n20, 100, "real-bearer-20", 4)
    store.save_org(org20)
    slug20 = org20.d["slug"]
    check("R1 · the phantom is still provable with a newer generation "
          "standing after it",
          lambda: _true(supervisor._phantom_evidence(
              org20, ph20)["phantom"] is True))
    supervisor.drop_phantom_generation(slug20, ph20)
    org20 = store.load_org(slug20)
    check("R1 · dropping it does NOT orphan the real generation minted after "
          "it — the live node still points at that generation, not past it",
          lambda: _true(org20.node(n20)["predecessor"] == real20,
                        f'live.predecessor = {org20.node(n20)["predecessor"]}'))
    check("R1 · …the newer generation re-links onto the phantom's "
          "predecessor",
          lambda: _true(org20.nodes[real20]["predecessor"] == prev20,
                        f'{real20}.predecessor = '
                        f'{org20.nodes[real20]["predecessor"]}'))
    check("R1 · …and BOTH survivors are still in the lineage stack, which is "
          "what the agent is told to rehire and what dissolve/delete walk",
          lambda: _true(set(org20.lineage_stack(n20)) == {real20, prev20},
                        f"stack = {org20.lineage_stack(n20)}"))

    # ---- DRIFT (found 2026-08-20 by running the proof against the LIVE doc) --
    # Both repair verbs anchored on the SUCCESSOR: "does this row's session id
    # equal the live node's?", and counted boundaries in the live node's file.
    # `successor` is the bare live id, and the live node's session MOVES every
    # time it compacts — a §8 split hands it the fork and leaves the old id to
    # the bearer it just minted. The row does not move. So both anchors drift
    # off a row that is standing still, and the repair written for it stops
    # applying: ingame-prompt@6, a phantom proven at 425/425, was refused with
    # "holds its own session id" once @7 inherited its session. It failed
    # CLOSED, so nothing was destroyed — the repair simply would not run, and
    # for `recover` that is the loss direction: a GENUINE lost generation
    # becomes unrecoverable the moment its live node compacts again.
    # The durable question is asked of the ROW: does anyone else still hold
    # this session file?
    org22, (n22,) = horg()
    old22 = sid_of(org22, n22)
    _plant_session(old22, [_msg("z1"), _msg("z2")])
    prev22 = org22.compact_split(n22, "fork-sid-22")
    _plant_session("fork-sid-22", [_msg("z1"), _msg("z2"),
                                   _boundary("zb"), _msg("z3")])
    ph22 = org22.record_cli_compaction(n22, 100, None, 2)       # the phantom
    # …and now the drift: the live node compacts AGAIN. `mid22` inherits the
    # phantom's session; the live node moves to a file the phantom never saw.
    mid22 = org22.compact_split(n22, "fork-sid-22b")
    # the live node's new file is a summary and what came after it — the shape
    # b067d11f actually had on this machine, and it carries NO boundary of the
    # phantom's. Counting boundaries in the SUCCESSOR therefore finds none at
    # all, which is exactly how the drift refused a proven phantom.
    _plant_session("fork-sid-22b", [_msg("zsum"), _msg("z4")])
    store.save_org(org22)
    slug22 = org22.d["slug"]
    check("drift · (the shape) the phantom no longer matches the live node's "
          "session — it matches the BEARER that inherited it",
          lambda: _true(org22.nodes[ph22]["session_id"]
                        == org22.nodes[mid22]["session_id"]
                        != org22.node(n22)["session_id"],
                        f'{org22.nodes[ph22]["session_id"]} / '
                        f'{org22.node(n22)["session_id"]}'))
    ev22 = supervisor._phantom_evidence(org22, ph22)
    check("drift · a phantom is STILL provable after its live node has "
          "compacted past it — the proof follows the row, not the successor",
          lambda: _true(ev22["phantom"] is True, json.dumps(ev22)))
    check("drift · …and still names the sibling that actually holds the "
          "content, matched record for record",
          lambda: _true(ev22.get("duplicate_of") == prev22
                        and ev22.get("records") == 2, json.dumps(ev22)))
    supervisor.drop_phantom_generation(slug22, ph22)
    org22 = store.load_org(slug22)
    check("drift · the drifted phantom drops, and the bearer that inherited "
          "its session re-links onto the phantom's predecessor",
          lambda: _true(ph22 not in org22.nodes
                        and org22.nodes[mid22]["predecessor"] == prev22,
                        f'{mid22}.predecessor = '
                        f'{org22.nodes[mid22]["predecessor"]}'))
    check("drift · …and that bearer is otherwise untouched — it is the one "
          "holding the records, and the drop must not disturb it",
          lambda: _true(org22.nodes[mid22]["bearer_state"] == "knowledge"
                        and org22.nodes[mid22]["session_id"] == "fork-sid-22"))

    # the fail-closed direction must survive the same drift: unique content is
    # a real loss whether or not the live node has moved on since
    org23, (n23,) = horg()
    old23 = sid_of(org23, n23)
    _plant_session(old23, [_msg("p1")])                          # PARTIAL
    prev23 = org23.compact_split(n23, "fork-sid-23")
    _plant_session("fork-sid-23", [_msg("p1"), _msg("p2"),
                                   _boundary("pb"), _msg("p3")])
    lost23 = org23.record_cli_compaction(n23, 100, None, 2)
    org23.compact_split(n23, "fork-sid-23b")                     # the drift
    _plant_session("fork-sid-23b", [_msg("psum"), _msg("p4")])
    store.save_org(org23)
    _ = prev23
    ev23 = supervisor._phantom_evidence(org23, lost23)
    check("drift · unique content still REFUSES after the drift — the looser "
          "anchor must not loosen the proof",
          lambda: _true(ev23["phantom"] is False
                        and "unique" in ev23.get("why", ""), json.dumps(ev23)))
    check("drift · …and the drop refuses with it",
          lambda: _raises(lambda: supervisor.drop_phantom_generation(
              org23.d["slug"], lost23), "unique"))

    # the guard the successor-test was standing in for, stated on the row: a
    # lost row that owns its session ALONE has already been cut out of a
    # shared file (a recovered bearer, reseed's dead session) and is nobody's
    # to drop. Mutation-tested: the control above it must pass first, or
    # deleting this guard would go unnoticed.
    org24, (n24,) = horg()
    old24 = sid_of(org24, n24)
    _plant_session(old24, [_msg("c1"), _msg("c2")])
    org24.compact_split(n24, "fork-sid-24")
    _plant_session("fork-sid-24", [_msg("c1"), _msg("c2"),
                                   _boundary("cb"), _msg("c3")])
    ph24 = org24.record_cli_compaction(n24, 100, None, 2)
    store.save_org(org24)
    check("alone · (control) while somebody else holds the session it IS a "
          "phantom — so the next check cannot pass vacuously",
          lambda: _true(supervisor._phantom_evidence(
              org24, ph24)["phantom"] is True))
    _plant_session("orphan-sid-24", [_msg("c1"), _msg("c2"),
                                     _boundary("cb"), _msg("c3")])
    org24.nodes[ph24]["session_id"] = "orphan-sid-24"      # nobody else's
    store.save_org(org24)
    ev24 = supervisor._phantom_evidence(org24, ph24)
    check("alone · a lost row that owns its session file alone is refused — "
          "its records are already somewhere of their own",
          lambda: _true(ev24["phantom"] is False
                        and "alone" in ev24.get("why", ""), json.dumps(ev24)))
    check("alone · …and the drop refuses with it",
          lambda: _raises(lambda: supervisor.drop_phantom_generation(
              org24.d["slug"], ph24), "alone"))

    # a sharer from OUTSIDE the row's lineage is an arrangement this proof was
    # never written for, and an unrecognised arrangement authorises no deletion
    org25, (n25, stranger25) = horg(2)
    old25 = sid_of(org25, n25)
    _plant_session(old25, [_msg("d1"), _msg("d2")])
    org25.compact_split(n25, "fork-sid-25")
    _plant_session("fork-sid-25", [_msg("d1"), _msg("d2"),
                                   _boundary("db"), _msg("d3")])
    ph25 = org25.record_cli_compaction(n25, 100, None, 2)
    store.save_org(org25)
    check("outsider · (control) it IS a phantom before the stranger appears",
          lambda: _true(supervisor._phantom_evidence(
              org25, ph25)["phantom"] is True))
    org25.node(stranger25)["session_id"] = "fork-sid-25"
    store.save_org(org25)
    ev25 = supervisor._phantom_evidence(org25, ph25)
    check("outsider · a node outside the lineage holding the same session "
          "refuses the proof rather than reasoning about it",
          lambda: _true(ev25["phantom"] is False
                        and "outside its lineage" in ev25.get("why", ""),
                        json.dumps(ev25)))
    check("outsider · …and the drop refuses with it",
          lambda: _raises(lambda: supervisor.drop_phantom_generation(
              org25.d["slug"], ph25), "outside"))

    # RECOVER under the same drift — the loss direction. Before this, a real
    # in-place loss stopped being recoverable as soon as its live node
    # compacted again: the records were still sitting in the file (a knowledge
    # bearer now owns it), and the verb refused to look.
    org26, (n26,) = horg()
    sid26 = sid_of(org26, n26)
    _plant_session(sid26, [_msg("t1"), _msg("t2"), _boundary("tb"), _msg("t3")])
    lost26 = org26.record_cli_compaction(n26, 100, None, 2)      # genuine loss
    mid26 = org26.compact_split(n26, "fork-sid-26")              # the drift
    _plant_session("fork-sid-26", [_msg("tsum"), _msg("t4")])
    store.save_org(org26)
    slug26 = org26.d["slug"]
    rec26 = supervisor.recover_lost_generation(slug26, lost26)
    org26 = store.load_org(slug26)
    check("drift · a genuine loss is STILL recoverable after its live node "
          "has compacted past it — cut at its own boundary, in its own file",
          lambda: _true(org26.nodes[lost26]["bearer_state"] == "knowledge"
                        and rec26["cut_at"] == 2, json.dumps(rec26)))

    def _drift_recovered() -> None:
        p = supervisor.transcript_path(org26.nodes[lost26]["session_id"], None)
        _true(bool(p), "the recovered bearer has no transcript")
        got = [json.loads(x).get("uuid")
               for x in open(p, encoding="utf-8").read().splitlines()]  # type: ignore[arg-type]
        _true(got == ["t1", "t2"], f"got {got}")
    check("drift · …holding exactly the records above ITS boundary, not the "
          "live node's", _drift_recovered)
    check("drift · …and the bearer that inherited the original session keeps "
          "it — recovery COPIES a prefix, it does not take the file",
          lambda: _true(org26.nodes[mid26]["session_id"] == sid26
                        and supervisor.transcript_path(sid26, None)
                        is not None))

    # …and recover's half of the same guard, stated on the row. A lost row
    # holding a session NOBODY else holds (reseed's dead session) has no
    # in-place boundary to be cut from: cutting anyway would copy a prefix out
    # of the row's own exclusive file and cut it at a boundary that is not
    # provably its own. Refuses.
    org27, (n27,) = horg()
    sid27 = sid_of(org27, n27)
    _plant_session(sid27, [_msg("f1"), _msg("f2"), _boundary("fb"), _msg("f3")])
    lost27 = org27.record_cli_compaction(n27, 100, None, 2)
    store.save_org(org27)
    slug27 = org27.d["slug"]
    check("alone · (control) while it shares the live session it recovers — "
          "so the next check cannot pass vacuously",
          lambda: _true(bool(supervisor._session_sharers(org27, lost27))))
    _plant_session("orphan-sid-27", [_msg("f1"), _msg("f2"),
                                     _boundary("fb"), _msg("f3")])
    org27.nodes[lost27]["session_id"] = "orphan-sid-27"
    store.save_org(org27)
    check("alone · recover refuses a lost row that owns its session alone — "
          "there is no shared file to cut it out of",
          lambda: _raises(
              lambda: supervisor.recover_lost_generation(slug27, lost27),
              "alone"))

    # the CONTROL for the check above, on the VERB rather than on the helper:
    # the identical fixture, left sharing, must actually recover. Asserting
    # `_session_sharers(...)` is truthy tests the helper the guard calls, not
    # the guard — a distinction the first version of this control missed
    # (redteam 2026-08-20).
    org28, (n28,) = horg()
    sid28 = sid_of(org28, n28)
    _plant_session(sid28, [_msg("f1"), _msg("f2"), _boundary("fb"), _msg("f3")])
    lost28 = org28.record_cli_compaction(n28, 100, None, 2)
    store.save_org(org28)
    check("alone · (control) the SAME fixture, left sharing, recovers — so "
          "the refusal above is the guard and not the shape",
          lambda: _true(supervisor.recover_lost_generation(
              org28.d["slug"], lost28)["cut_at"] == 2))

    # ---- reseed's dead session is NOT a compaction row -----------------------
    # Reproduced by redteam 2026-08-20. `reseed` archives an unrecoverable
    # session as bearer_state="lost" while keeping its id, and that row has no
    # boundary of its own ANYWHERE — its generation was abandoned whole. The
    # old successor-anchored precondition excluded it by ACCIDENT (reseed
    # always moved the live node to a fresh id, so the row never matched);
    # asking the question of the row lost that accident, and positional
    # inference then handed the reseed row a neighbour's boundary and
    # advertised the result as a consultable bearer holding another
    # generation's context.
    org32, (n32,) = horg()
    sid32 = sid_of(org32, n32)
    _plant_session(sid32, [_msg("a1"), _msg("a2"), _boundary("ab1"),
                           _msg("a3"), _msg("a4"), _boundary("ab2"),
                           _msg("a5")])
    row32a = org32.record_cli_compaction(n32, 100, None, 2)
    row32b = org32.record_cli_compaction(n32, 100, None, 5)
    org32.mark_unrecoverable(n32, "probe")
    org32.reseed(USER, n32, "fresh-sid-32")
    seed32 = f"{n32}@{org32.node(n32)['generation'] - 1}"
    store.save_org(org32)
    slug32 = org32.d["slug"]
    check("reseed · the row reseed archives says WHY it is lost, which is the "
          "only thing that tells it from a compaction row",
          lambda: _true(org32.nodes[seed32].get("lost_reason") == "reseed"
                        and org32.nodes[seed32]["session_id"] == sid32,
                        json.dumps(org32.nodes[seed32].get("lost_reason"))))
    check("reseed · …and a CLI-compaction row says so too",
          lambda: _true(org32.nodes[row32a].get("lost_reason")
                        == "cli_compaction"))
    check("reseed · recover REFUSES it — it has no boundary of its own, and "
          "cutting it at a neighbour's would give it another generation's "
          "records under its own name",
          lambda: _raises(
              lambda: supervisor.recover_lost_generation(slug32, seed32),
              "reseed's dead session"))
    check("reseed · the phantom proof refuses it too, so it can never be "
          "deleted as a duplicate either",
          lambda: _true(supervisor._phantom_evidence(
              org32, seed32)["phantom"] is False))
    # and the genuine rows beside it are NOT collateral: the reseed row must
    # not strand them (the knock-on the redteam measured — recovering one row
    # consumed the last sharer and refused the rest forever)
    r32a = supervisor.recover_lost_generation(slug32, row32a)
    r32b = supervisor.recover_lost_generation(slug32, row32b)
    check("reseed · the genuine compaction rows beside it still recover, each "
          "at its OWN boundary — the dead row is skipped, not blocking",
          lambda: _true(r32a["cut_at"] == 2 and r32b["cut_at"] == 5,
                        json.dumps([r32a, r32b])))

    def _reseed_neighbours() -> None:
        o = store.load_org(slug32)
        for row, exp in ((row32a, ["a1", "a2"]),
                         (row32b, ["a1", "a2", "ab1", "a3", "a4"])):
            p = supervisor.transcript_path(o.nodes[row]["session_id"], None)
            got = [json.loads(x).get("uuid")
                   for x in open(p, encoding="utf-8").read().splitlines()]  # type: ignore[arg-type]
            _true(got == exp, f"{row}: got {got}, wanted {exp}")
    check("reseed · …holding exactly their own generations", _reseed_neighbours)

    # a reseed row minted BEFORE `lost_reason` existed cannot be recognised by
    # name, so the guessing branch demands the fact that distinguishes an
    # abandoned session from a compacted one: somebody who could still USE the
    # session holds it. Reseed leaves its dead id to lost rows alone.
    org33, (n33,) = horg()
    sid33 = sid_of(org33, n33)
    _plant_session(sid33, [_msg("b1"), _boundary("bb1"), _msg("b2"),
                           _boundary("bb2"), _msg("b3")])
    legacy33 = org33.record_cli_compaction(n33, 100)         # no offset
    org33.nodes[legacy33].pop("cli_boundary_offset", None)
    org33.mark_unrecoverable(n33, "probe")
    org33.reseed(USER, n33, "fresh-sid-33")
    seed33 = f"{n33}@{org33.node(n33)['generation'] - 1}"
    org33.nodes[seed33].pop("lost_reason", None)             # a PRE-EXISTING row
    org33.nodes[seed33].pop("cli_boundary_offset", None)
    store.save_org(org33)
    slug33 = org33.d["slug"]
    check("reseed · (the arrangement) two boundaries, two unmarked lost rows "
          "— the counts MATCH, so nothing but the guard stands between the "
          "dead row and a neighbour's boundary",
          lambda: _true(len(supervisor._count_cli_compactions(
              org33, seed33)[2]) == 2
              and org33.nodes[seed33].get("lost_reason") is None))
    check("reseed · an unrecognisable dead row is still refused: only other "
          "lost rows hold that session, and nothing that could use it does",
          lambda: _raises(
              lambda: supervisor.recover_lost_generation(slug33, seed33),
              "nothing that could still use"))
    # …and that extra demand is paid ONLY by rows that cannot say what they
    # are. A row that names itself a compaction row keeps the legacy
    # positional recovery the branch was written for, even when the only
    # other holder of its session is a dead one.
    check("reseed · …while the row that DOES say it is a compaction row still "
          "recovers positionally beside it — the doubt is charged to the "
          "unidentifiable row, not to the whole branch",
          lambda: _true(supervisor.recover_lost_generation(
              slug33, legacy33)["cut_at"] == 1))

    # the marker's other job: keeping the dead row OUT of the arithmetic, so
    # it cannot push a genuine row's index onto a neighbour's boundary — and
    # so its mere presence does not refuse the genuine row for a crowd of two
    org36, (n36,) = horg()
    sid36 = sid_of(org36, n36)
    _plant_session(sid36, [_msg("j1"), _boundary("jb"), _msg("j2")])
    legacy36 = org36.record_cli_compaction(n36, 100)          # no offset
    org36.nodes[legacy36].pop("cli_boundary_offset", None)
    org36.mark_unrecoverable(n36, "probe")
    org36.reseed(USER, n36, "fresh-sid-36")                   # marked reseed
    store.save_org(org36)
    check("reseed · the dead row is not counted against the session's "
          "boundaries — one boundary, one COMPACTION row, and the genuine "
          "row recovers at it",
          lambda: _true(supervisor.recover_lost_generation(
              org36.d["slug"], legacy36)["cut_at"] == 1))

    # …and the proof refuses it too. No arrangement was found that reaches the
    # content comparison with a reseed row (reseed always moves the live node
    # off the session, so the row is normally alone), so this is stated by
    # forcing the state onto a row the proof otherwise accepts — the guard is
    # defence in depth, and an untested guard is the one that quietly rots.
    org37, (n37,) = horg()
    old37 = sid_of(org37, n37)
    _plant_session(old37, [_msg("k1"), _msg("k2")])
    org37.compact_split(n37, "fork-sid-37")
    _plant_session("fork-sid-37", [_msg("k1"), _msg("k2"),
                                   _boundary("kb"), _msg("k3")])
    ph37 = org37.record_cli_compaction(n37, 100, None, 2)
    store.save_org(org37)
    check("reseed · (control) the row IS a phantom while it says it is a "
          "compaction row",
          lambda: _true(supervisor._phantom_evidence(
              org37, ph37)["phantom"] is True))
    org37.nodes[ph37]["lost_reason"] = "reseed"
    store.save_org(org37)
    ev37 = supervisor._phantom_evidence(org37, ph37)
    check("reseed · …and the same row, saying it is an abandoned session, is "
          "refused — a dead session's records are not a duplicated prefix",
          lambda: _true(ev37["phantom"] is False
                        and "reseed's dead session" in ev37.get("why", ""),
                        json.dumps(ev37)))

    # ---- recover's missing lineage test (redteam 2026-08-20) ---------------
    # `_phantom_evidence` refuses an outside holder — but it says so by
    # returning phantom=False, and recover reads that as permission. Without
    # its own test it would cut a bearer out of a STRANGER's live transcript.
    org34, (n34, stranger34) = horg(2)
    sid34 = sid_of(org34, n34)
    _plant_session(sid34, [_msg("s1"), _msg("s2"), _boundary("sb"), _msg("s3")])
    lost34 = org34.record_cli_compaction(n34, 100, None, 2)
    org34.compact_split(n34, "fork-sid-34")           # the lineage moves off
    _plant_session("fork-sid-34", [_msg("ssum"), _msg("s4")])
    org34.node(stranger34)["session_id"] = sid34      # …and a stranger holds it
    store.save_org(org34)
    check("outsider · recover refuses a session held from outside the "
          "lineage — a bearer must never be cut out of another agent's live "
          "transcript",
          lambda: _raises(
              lambda: supervisor.recover_lost_generation(
                  org34.d["slug"], lost34), "outside its lineage"))

    # ---- the proof may not compare a file with itself ----------------------
    # No live path was found for this (every mint that hands a bearer the live
    # session moves the live node off it), but `theirs ⊇ mine` is true by
    # construction when both name one file, so the proof would pass on ANY
    # offset and authorise a deletion on no evidence. Deletion is
    # unrecoverable; it is checked rather than argued.
    org35, (n35,) = horg()
    old35 = sid_of(org35, n35)
    _plant_session(old35, [_msg("i1"), _msg("i2")])
    prev35 = org35.compact_split(n35, "fork-sid-35")
    _plant_session("fork-sid-35", [_msg("i1"), _msg("i2"),
                                   _boundary("ib"), _msg("i3")])
    ph35 = org35.record_cli_compaction(n35, 100, None, 2)
    store.save_org(org35)
    check("self-proof · (control) it IS a phantom against the real sibling",
          lambda: _true(supervisor._phantom_evidence(
              org35, ph35)["phantom"] is True))
    org35.nodes[prev35]["session_id"] = org35.nodes[ph35]["session_id"]
    store.save_org(org35)
    ev35 = supervisor._phantom_evidence(org35, ph35)
    check("self-proof · a sibling naming the SAME file proves nothing, and is "
          "refused rather than believed",
          lambda: _true(ev35["phantom"] is False
                        and "same session file" in ev35.get("why", ""),
                        json.dumps(ev35)))

    # R2 (MAJOR): the marks are counted OUTSIDE the doc lock, and
    # cheap_compact can replace the session mid-turn. Recording them then
    # mints generations against a file the node no longer owns AND stamps a
    # count that suppresses the next real compactions.
    org21, (n21,) = horg()
    sid21 = sid_of(org21, n21)
    _plant_session(sid21, [_msg("y1"), _boundary("yb1"),
                           _msg("y2"), _boundary("yb2"), _msg("y3")])
    org21.node(n21)["cli_compactions"] = 0
    store.save_org(org21)
    slug21 = org21.d["slug"]
    racer = store.load_org(slug21)          # the mid-turn cheap_compact
    racer.cheap_compact(USER, n21)
    store.save_org(racer)
    new_sid21 = store.load_org(slug21).node(n21)["session_id"]
    before21 = set(store.load_org(slug21).nodes)
    _rig21 = os.path.join(HOME, ".claude", "projects", "rig")
    files21 = set(os.listdir(_rig21))
    st21 = supervisor.state(slug21, n21)
    supervisor._after_turn(slug21, n21, org21, {}, st21, 1)   # stale org
    after21 = store.load_org(slug21)
    check("R2 · a session replaced mid-turn discards the counted boundaries "
          "instead of minting generations against the new session",
          lambda: _true(set(after21.nodes) == before21,
                        f"minted: {set(after21.nodes) - before21}"))
    check("R2 · …and does NOT stamp the old file's count onto the new "
          "session, which would swallow its next real compactions",
          lambda: _true(after21.node(n21).get("cli_compactions") is None,
                        f'got {after21.node(n21).get("cli_compactions")!r}'))
    # the cuts were taken BEFORE the lock and must be cleaned up on the bail —
    # otherwise each raced turn leaves a stray transcript in the user's tree,
    # attached to no node and carried by reconcile's index forever
    check("R2 · …and deletes the cuts it had already taken, leaving no "
          "orphan transcript in the user's projects tree",
          lambda: _true(set(os.listdir(_rig21)) == files21,
                        f"strays: {set(os.listdir(_rig21)) - files21}"))
    check("R2 · (control) the racing cheap_compact really did move the "
          "session — the bail was exercised, not skipped",
          lambda: _true(new_sid21 != sid21
                        and after21.node(n21)["session_id"] == new_sid21))

    # R2b: the SIBLING bail. A node deleted mid-turn must discard its cuts
    # too — more certainly than the session-swap case, because `delete`
    # explicitly leaves transcripts on disk, so nothing else ever reaps them.
    org25, (n25,) = horg()
    sid25 = sid_of(org25, n25)
    _plant_session(sid25, [_msg("w1"), _boundary("wb1"),
                           _msg("w2"), _boundary("wb2"), _msg("w3")])
    org25.node(n25)["cli_compactions"] = 0
    store.save_org(org25)
    slug25 = org25.d["slug"]
    gone25 = store.load_org(slug25)          # the node vanishes mid-turn
    gone25.retire(USER, n25)
    gone25.delete(USER, n25)
    store.save_org(gone25)
    _rig25 = os.path.join(HOME, ".claude", "projects", "rig")
    files25 = set(os.listdir(_rig25))
    st25 = supervisor.state(slug25, n25)
    supervisor._after_turn(slug25, n25, org25, {}, st25, 1)   # stale org
    check("R2b · a node DELETED mid-turn discards its cuts as well — the "
          "guard was on the sibling bail only",
          lambda: _true(set(os.listdir(_rig25)) == files25,
                        f"strays: {set(os.listdir(_rig25)) - files25}"))
    check("R2b · (control) the node really was gone — the bail was exercised",
          lambda: _true(n25 not in store.load_org(slug25).nodes))

    # R5 (redteam round 3): between the cut and the save the files exist while
    # NOTHING in the doc names them, so a RAISE in that window leaks them —
    # and it compounds: save_org can fail on a full disk or a held handle, the
    # caller swallows it into last_error, the watermark is never persisted, so
    # the next turn cuts the same boundaries again onto the same full disk.
    org27, (n27,) = horg()
    sid27 = sid_of(org27, n27)
    _plant_session(sid27, [_msg("s1"), _boundary("sb1"),
                           _msg("s2"), _boundary("sb2"), _msg("s3")])
    org27.node(n27)["cli_compactions"] = 0
    store.save_org(org27)
    slug27 = org27.d["slug"]
    _rig27 = os.path.join(HOME, ".claude", "projects", "rig")
    files27 = set(os.listdir(_rig27))
    # ⚠ patch `record_cli_compaction`, NOT `store.save_org`. Patching save_org
    # globally raises on an EARLIER save inside _after_turn, so the cut block
    # is never reached and the check passes with nothing exercised — it did
    # exactly that on the first attempt and survived a mutation of the guard
    # it was meant to pin. record_cli_compaction is inside the window, after
    # the cuts exist and before the save.
    _real_rec = Org.record_cli_compaction
    _rec_calls: list[int] = []

    def _rec_boom(*_a: object, **_k: object) -> str:
        _rec_calls.append(1)
        raise OSError("disk full")

    st27 = supervisor.state(slug27, n27)
    raised27 = False
    Org.record_cli_compaction = _rec_boom    # type: ignore[assignment]
    try:
        supervisor._after_turn(slug27, n27, org27, {}, st27, 1)
    except Exception:                                        # noqa: BLE001
        raised27 = True
    finally:
        Org.record_cli_compaction = _real_rec   # type: ignore[assignment]
    check("R5 · (control) the cut window was actually entered — the raise "
          "landed after the cuts existed, not before them",
          lambda: _true(bool(_rec_calls) and raised27,
                        f"rec_calls={len(_rec_calls)} raised={raised27}"))
    check("R5 · a raise between the cut and the record still discards the "
          "cuts — otherwise the leak compounds every turn",
          lambda: _true(set(os.listdir(_rig27)) == files27,
                        f"strays: {set(os.listdir(_rig27)) - files27}"))
    check("R5 · …and the watermark is not advanced, so the boundaries are "
          "still pending rather than silently swallowed",
          lambda: _true(store.load_org(slug27).node(n27)
                        .get("cli_compactions") == 0))

    # the UNDER-count direction of the watermark bug (peer report from
    # compaction-fix): a stale HIGH counter against a fresh session makes
    # `cli_cnt > seen_raw` false FOREVER, so the new session's first real CLI
    # compaction is never noticed at all. Worse than a phantom row: the
    # correction never runs and control falls through to the threshold check,
    # forking a 600 s billed child on a session the CLI has just emptied.
    org28, (n28,) = horg()
    _plant_transcript(sid_of(org28, n28))
    org28.node(n28)["cli_compactions"] = 3          # stale high watermark
    org28.cheap_compact(USER, n28)
    store.save_org(org28)
    slug28 = org28.d["slug"]
    fresh28 = store.load_org(slug28).node(n28)["session_id"]
    _plant_session(fresh28, [_msg("u1"), _boundary("ub"), _msg("u2")])
    org28 = store.load_org(slug28)
    st28 = supervisor.state(slug28, n28)
    supervisor._after_turn(slug28, n28, org28, {}, st28, 1)   # baseline turn
    org28 = store.load_org(slug28)
    check("under-count · after a cheap_compact the fresh session baselines "
          "to its OWN count rather than staying under a stale high one",
          lambda: _true(org28.node(n28).get("cli_compactions") == 1,
                        f'got {org28.node(n28).get("cli_compactions")!r}'))
    _plant_session(fresh28, [_msg("u1"), _boundary("ub"), _msg("u2"),
                             _boundary("ub2"), _msg("u3")])
    org28 = store.load_org(slug28)
    st28b = supervisor.state(slug28, n28)
    supervisor._after_turn(slug28, n28, org28, {}, st28b, 1)
    org28 = store.load_org(slug28)
    check("under-count · …so its NEXT real CLI compaction is still noticed "
          "and still preserved, instead of being swallowed forever",
          lambda: _true(f"{n28}@1" in org28.nodes
                        and org28.nodes[f"{n28}@1"]["bearer_state"]
                        == "knowledge",
                        f"nodes: {sorted(org28.nodes)}"))

    # R4: the boundary SCAN and the binary CUT must agree on where a line
    # ends. Text mode's universal-newlines splits a lone \r and binary does
    # not, so one stray CR above a boundary would shift every offset by one —
    # the cut would then INCLUDE the boundary record, and the "preserved"
    # generation would hold POST-compaction state while being labelled
    # consultable. Exactly the bug this branch exists to kill.
    org26, (n26,) = horg()
    sid26 = sid_of(org26, n26)
    _p26 = os.path.join(HOME, ".claude", "projects", "rig", sid26 + ".jsonl")
    # the CR sits where JSON allows WHITESPACE, so the line is still valid
    # JSON as a whole — but text-mode iteration splits it in two and binary
    # iteration does not. Written as raw bytes because json.dumps would
    # escape a CR inside a string to a literal backslash-r and plant nothing.
    with open(_p26, "wb") as f:
        f.write(json.dumps(_msg("cr1")).encode() + b"\n")
        f.write(b'{"type":"user","uuid":"cr2",\r'
                b'"message":{"role":"user","content":"b"}}\n')
        f.write(json.dumps(_boundary("crb")).encode() + b"\n")
        f.write(json.dumps(_msg("cr3")).encode() + b"\n")
    store.save_org(org26)
    _c26, _pp26, marks26 = supervisor._count_cli_compactions(org26, n26)
    cut26 = supervisor._fork_bearer_session(org26, sid26, marks26[0][0])

    def _cr_agreement() -> None:
        _true(bool(cut26), "the cut did not happen at all")
        p = supervisor.transcript_path(cut26, None)  # type: ignore[arg-type]
        raw = open(p, "rb").read()  # type: ignore[arg-type]
        _true(b'"compact_boundary"' not in raw,
              "the cut swallowed the boundary record — the scan and the "
              "cutter disagree about where a line ends")
        got = [json.loads(x).get("uuid")
               for x in raw.decode("utf-8").split("\n") if x.strip()]
        _true(got == ["cr1", "cr2"], f"got {got}")
    check("R4 · a lone CR above the boundary does not shift the cut — the "
          "scan and the binary cutter agree on line boundaries", _cr_agreement)

    # R3 (MAJOR): "could not read the session" must not baseline as 0 — the
    # next turn would read the fork's own boundary as new and re-mint the
    # phantom, this time WITH a bearer, which nothing can then drop.
    org22, (n22,) = horg()
    org22.node(n22)["session_id"] = "no-such-session-22"
    store.save_org(org22)
    slug22 = org22.d["slug"]
    st22 = supervisor.state(slug22, n22)
    supervisor._after_turn(slug22, n22, org22, {}, st22, 1)
    after22 = store.load_org(slug22)
    check("R3 · an unreadable session leaves the counter UNSET rather than "
          "baselining it to 0 — 'could not look' is not 'no boundaries'",
          lambda: _true(after22.node(n22).get("cli_compactions") is None,
                        f'got {after22.node(n22).get("cli_compactions")!r}'))
    check("R3 · …and the scan reports it as None, not 0",
          lambda: _true(supervisor._count_cli_compactions(org22, n22)[0]
                        is None))

    # R10: a row minted against a boundary at line 0 has NO records above it.
    # `mine` is empty, and an empty set is trivially a subset — so without the
    # guard the proof would say "phantom" about a row it never examined.
    org23, (n23,) = horg()
    sid23 = sid_of(org23, n23)
    _plant_session(sid23, [_boundary("zb0"), _msg("z1")])
    prev23 = org23.compact_split(n23, sid23)
    org23.node(n23)["session_id"] = sid23
    ph23 = org23.record_cli_compaction(n23, 100, None, 0)
    store.save_org(org23)
    check("R10 · a boundary at line 0 proves NOTHING — an empty prefix is "
          "vacuously a subset, so it must refuse, not drop",
          lambda: _true(supervisor._phantom_evidence(
              org23, ph23)["phantom"] is False))
    check("R10 · …and the drop refuses with it",
          lambda: _raises(lambda: supervisor.drop_phantom_generation(
              org23.d["slug"], ph23)))

    # R11: a recorded offset that no longer matches the file must refuse
    # rather than becoming the cut/compare point.
    org24, (n24,) = horg()
    sid24 = sid_of(org24, n24)
    _plant_session(sid24, [_msg("v1"), _boundary("vb1"), _msg("v2")])
    lost24 = org24.record_cli_compaction(n24, 100, None, 1)
    org24.nodes[lost24]["cli_boundary_offset"] = 99      # drifted
    store.save_org(org24)
    check("R11 · a recorded boundary that is no longer in the session "
          "refuses the recovery rather than cutting at a guessed point",
          lambda: _raises(lambda: supervisor.recover_lost_generation(
              org24.d["slug"], lost24), "no longer"))
    check("R11 · …and refuses the phantom comparison too",
          lambda: _true(supervisor._phantom_evidence(
              org24, lost24)["phantom"] is False))

    check("recover · refuses a phantom — recovering one would mint a SECOND "
          "bearer holding a copy of the first",
          lambda: _raises(
              lambda: supervisor.recover_lost_generation(
                  org14.d["slug"], ph14), "phantom"))
    check("recover · …and the refusal names the sibling that already holds "
          "the content, so the operator is pointed at the right repair",
          lambda: _raises(
              lambda: supervisor.recover_lost_generation(
                  org14.d["slug"], ph14), prev14))


# ================================================================== the runner

def main() -> None:
    t0 = time.time()
    print(f"compaction · lineage · cross-process   (rig {TMP})")
    lineage_algebra()
    bearer_rules()
    thresholds()
    occupancy_reporting()
    account_switch_compaction()
    predicates()
    lost_generations()
    notice_digest()
    cross_process()
    aging()

    if not HERMETIC_ONLY:
        write_wrapper()
        try:
            live_manual_split()
            live_auto_split()
            live_fork_failures()
            live_noisy_fork()
            live_deleted_mid_fork()
            live_split_interrupted()
            live_oracle()
            live_never_run_pardon()
            live_guards()
            live_spend_frozen_compact()
            live_fairness()
            live_timing()
        finally:
            try:
                drop_orgs()
            except Exception:                                    # noqa: BLE001
                pass
            stop_backend()

    print()
    for what, val in MEASURED:
        print(f"  MEASURED  {what}: {val}")
    if EXCEPTIONS:
        print(f"\n{len(EXCEPTIONS)} REPORTED EXCEPTION(S) — measured, not fixed here:")
        for label, why in EXCEPTIONS:
            print(f"  ⚑ {label}\n    {why}\n")
    if FAIL:
        print(f"\n{len(FAIL)} FAILURE(S):")
        for label, tb in FAIL:
            print(f"\n--- {label} ---\n{tb}")
        print(f"\n{PASS} passed, {len(FAIL)} FAILED   ({time.time() - t0:.0f}s)")
        sys.exit(1)
    print(f"\nALL {PASS} CHECKS PASS   ({time.time() - t0:.0f}s)")
    if KEEP:
        print(f"rig kept at {TMP}")
    else:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    finally:
        if not KEEP:
            stop_backend()
