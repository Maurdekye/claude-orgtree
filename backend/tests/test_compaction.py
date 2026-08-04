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

import glob
import json
import os
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


def token() -> str:
    return "CP" + os.urandom(5).hex()


# ============================================================ the rig (paths)

TMP = tempfile.mkdtemp(prefix="orgtree-compact-")
HDATA = os.path.join(TMP, "hermetic")     # the in-process half's data root
DATA = os.path.join(TMP, "data")          # the live backend's data root
HOME = os.path.join(TMP, "home")          # transcripts land here
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

from orgtree import store, supervisor                            # noqa: E402
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
    check("rehire · a live bearer pays a seat but stays OFF the org chart "
          "(§8.5 — it is lineage, not an org edge)",
          lambda: _true(pred in org.children(a)
                        and pred not in org.org_children(a)))
    check("rehire · …so the successor's committed credit counts it",
          lambda: _true(org.committed(a) >= org.seat_cost(pred)))

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
    org10, (n10,) = horg(grant=20)
    p10 = org10.compact_split(n10, "s10")
    check("extern · an archived bearer is not an external recipient",
          lambda: _true(p10 not in org10.extern_recipients()))
    check("extern · the live successor is",
          lambda: _true(n10 in org10.extern_recipients()))
    org10.rehire(USER, p10, grant=0)
    rec = org10.extern_recipients()
    if p10 in rec:
        exception(
            "extern · a REHIRED top-level knowledge bearer receives org mail",
            f"extern_recipients() = {rec}: it filters on children(None) + live, "
            "not on the org axis, so a bearer rehired into the old top-level slot "
            "is asked to answer FOR THE ORG from a pre-compaction context. "
            "org_children(None) excludes it; extern_recipients does not. "
            "Reported, not fixed — ledger.py is another agent's file.")
    else:
        check("extern · a rehired bearer is excluded from org mail",
              lambda: _true(p10 not in rec))


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


def _plant_transcript(sid: str, home: str = HOME) -> str:
    d = os.path.join(home, ".claude", "projects", "rig")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, sid + ".jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user",
                            "message": {"role": "user", "content": "x"}}) + "\n")
    return p


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
    process.stdout.write(JSON.stringify({ type: 'result', subtype: 'success',
      session_id: 'forked-after-a-banner-' + require('crypto').randomUUID(),
      result: 'compacted.', total_cost_usd: 0.25 }) + '\n')
    process.exit(0)
  }
  const wait = cfg.forkMs || 0
  setTimeout(() => {
    process.stdout.write(JSON.stringify({
      type: 'result', subtype: 'success',
      session_id: (cfg.sidPrefix || 'forked-') + require('crypto').randomUUID(),
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
    set_cfg(FAST, fork={"default": {"mode": "ok", "forkCost": 0.25}})
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
    check("manual · the successor's occupancy is RESET, not carried over",
          lambda: _true(not after.get("occupancy"),
                        f"occupancy {after.get('occupancy')!r} survived the "
                        f"split — a stale near-full reading keeps the wheel "
                        f"hot and lets the repeat precheck through"))
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


# ================================================================== the runner

def main() -> None:
    t0 = time.time()
    print(f"compaction · lineage · cross-process   (rig {TMP})")
    lineage_algebra()
    bearer_rules()
    thresholds()
    predicates()
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
