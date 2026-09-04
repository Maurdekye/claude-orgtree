"""A Codex agent's OS sandbox mode follows its orgtree permission_mode.

    python backend/tests/test_codex_sandbox_mode.py   (no pytest; plain asserts)

WHAT THIS GUARDS. `supervisor._codex_sandbox` decides what a model may touch
on the operator's real machine, and one of its three answers —
`danger-full-access` — turns the OS sandbox OFF entirely. Before 2026-09-04
orgtree never passed that value and picked between the other two from the
`edit` tool switch alone; a Codex seat could therefore commit but not write a
git ref in a shared checkout on another drive, because `workspace-write`
confines writes to the turn's cwd.

The rule under test:

    edit OFF                            -> read-only        (whatever the mode)
    edit ON,  permission_mode bypass    -> danger-full-access
    edit ON,  any other permission_mode -> workspace-write

BOTH SITES. The mode is chosen twice in supervisor.py — once for a normal turn
(`codexrun.CodexTurn`) and once for a compaction fork (`codexrun.compact_fork`).
§4 drives the compaction path separately and §5 requires the two to agree for
every combination, because a fix at one site only would let one agent run at
two different OS privilege levels depending on whether it happened to be
compacting.

WHERE THE ASSERTIONS LAND. Never on a helper this file wrote: §1-§3 record the
`sandbox=` keyword as `codexrun.CodexTurn.__init__` actually receives it (and
re-read `turn.sandbox`, the attribute `codexrun` puts on the `thread/start`
wire), and §4 records the keyword `codexrun.compact_fork` actually receives.
A test that called `_codex_sandbox` directly would prove only that the helper
returns what the helper returns, and would stay green if a call site were
reverted.

ANTI-VACUITY. §0 is the positive control: it plants a deliberately impossible
sentinel and requires the recorder to SEE that the real code overwrote it, so
"no full-access leak found" can never mean "the recorder never ran". §4 also
asserts the compaction completed, because `_compact_split_codex_body` wraps
its body in a bare `except Exception` that would swallow a broken fixture and
leave a recorded-but-meaningless value behind.

Hermetic: the codex CLI resolves (via ORGTREE_CODEX) to fakecodex.py, the
scripted app-server double, and ORGTREE_DATA is a throwaway temp root
established BEFORE `orgtree.store` is imported.
"""

import json
import os
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ⚠ ORDER IS LOAD-BEARING: `store.DATA_ROOT` binds at import time. This root is
# set before the first orgtree import below, so the process cannot resolve to
# the operator's live data root.
DATA = tempfile.mkdtemp(prefix="orgtree-codexsbx-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_WARM"] = "0"        # no pre-warmed processes to reason about
# A PORT NOBODY SERVES: the codex leg's tool dispatcher POSTs /api/agent on
# ORGTREE_PORT. Left unset it defaults to 7360 and a TEST's tool calls land on
# the operator's LIVE deployment.
os.environ["ORGTREE_PORT"] = "9"
# the turn ends on its own without calling a tool — the cheapest complete turn
os.environ["FAKECODEX_SCENARIO"] = "stall"
os.environ["FAKECODEX_STALL_S"] = "0.05"

FAKECODEX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fakecodex.py")
CODEX_HOME = tempfile.mkdtemp(prefix="codexsbx-home-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME
with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')          # connect-state needs existence, not content

from orgtree import codexrun, providers, store, supervisor      # noqa: E402
from orgtree.ledger import PM_LEVELS, USER                      # noqa: E402

assert store.DATA_ROOT == DATA, (
    f"store bound to {store.DATA_ROOT!r}, not the throwaway root {DATA!r}")
providers._status_cache = None          # the 60s panel cache must not lie here

supervisor.stream = lambda slug, nid, payload: None

PASS = 0
FAIL: list[tuple[str, str]] = []

#: the value that must never survive a run: if a recorder still holds it, the
#: production code never reached the boundary and any "expected" assertion
#: made against it would have been vacuous
NEVER = "<<recorder-never-ran>>"


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


# ── fixtures ────────────────────────────────────────────────────────────────

def mkorg(label: str, *, edit: bool, pm: str) -> tuple[str, str]:
    """One org, one codex-tier node carrying the scope under test.

    `permission_mode` is set through the real `set_scope` door (which clamps
    against the kiosk ceiling) rather than poked into the doc, so the suite
    exercises a scope the product can actually produce.
    """
    org = store.create_org(f"zz codexsbx {label}")
    nid = org.hire(USER, None, "sol", 0, "cx", add_dirs=[],
                   tools={"bash": True, "web": False, "edit": edit,
                          "subagents": False, "mcp": []},
                   org_visibility="team",
                   charter="a codex sandbox-mode test agent")["node"]
    if pm != org.node(nid)["scope"].get("permission_mode"):
        org.set_scope(USER, nid, permission_mode=pm)
    store.save_org(org)
    reread = store.load_org(org.d["slug"]).node(nid)["scope"]
    eq(reread.get("permission_mode"), pm, f"{label}: stored permission_mode")
    eq(reread.get("tools", {}).get("edit"), edit, f"{label}: stored edit switch")
    return org.d["slug"], nid


class _Recorder:
    """Records the `sandbox=` keyword at the codexrun boundary and restores
    the real callable on exit. Seeded with NEVER so a boundary that is never
    reached is distinguishable from one that answered."""

    def __init__(self) -> None:
        self.turn_kwarg = NEVER
        self.turn_attr = NEVER
        self.fork_kwarg = NEVER
        self._real_turn = codexrun.CodexTurn
        self._real_fork = codexrun.compact_fork

    def __enter__(self) -> "_Recorder":
        rec = self

        class RecordingTurn(self._real_turn):                    # type: ignore[misc,valid-type]
            def __init__(self, *a, **kw):
                rec.turn_kwarg = kw.get("sandbox", "<<no sandbox kwarg>>")
                super().__init__(*a, **kw)
                # what codexrun will actually put on the thread/start wire
                rec.turn_attr = self.sandbox

        def recording_fork(*a, **kw):
            rec.fork_kwarg = kw.get("sandbox", "<<no sandbox kwarg>>")
            return {"thread_id": "codexsbx-forked-thread",
                    "token_usage": {"input_tokens": 10, "output_tokens": 1}}

        codexrun.CodexTurn = RecordingTurn                       # type: ignore[misc]
        codexrun.compact_fork = recording_fork                   # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> None:
        codexrun.CodexTurn = self._real_turn                     # type: ignore[misc]
        codexrun.compact_fork = self._real_fork                  # type: ignore[assignment]


def turn_sandbox(slug: str, nid: str) -> tuple[str, str]:
    """Run one real codex turn and return (kwarg, wire attribute)."""
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    with _Recorder() as rec:
        supervisor._run_one_turn(
            slug, nid, {"text": "sandbox probe", "view": "sandbox probe"})
    return rec.turn_kwarg, rec.turn_attr


def wire_sandbox(slug: str, nid: str, turns: int = 2) -> list[dict]:
    """Run `turns` REAL turns and return what fakecodex saw on the wire.

    One row per `thread/start` / `thread/resume`, in order. The first turn of
    a fresh node starts a thread; every turn after it resumes one — which is
    the whole point, because the app-server does NOT carry a resumed thread's
    sandbox forward from its birth. Measured against codex-cli 0.153.3: a
    thread born `danger-full-access` wrote outside its cwd on turn 1 and was
    refused by the OS on turn 2 when resume carried no sandbox, and could
    write again when it did.
    """
    probe = os.path.join(tempfile.mkdtemp(prefix="codexsbx-wire-"), "sbx.jsonl")
    os.environ["FAKECODEX_SANDBOXPROBE"] = probe
    try:
        for i in range(turns):
            st = supervisor.state(slug, nid)
            with supervisor._state_lock:
                st["busy"] = True
            supervisor._run_one_turn(
                slug, nid, {"text": f"wire probe {i}", "view": f"wire probe {i}"})
    finally:
        os.environ.pop("FAKECODEX_SANDBOXPROBE", None)
    if not os.path.exists(probe):
        return []
    with open(probe, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def fork_sandbox(slug: str, nid: str) -> str:
    """Drive the COMPACTION site and return the kwarg compact_fork received.

    Also proves the body ran to completion: `_compact_split_codex_body`
    swallows every exception into `state()["last_error"]`, so a fixture that
    blew up before the fork would otherwise leave NEVER behind and a bare
    "!= danger-full-access" assertion would pass for the wrong reason.
    """
    old_sid = "codexsbx-source-thread"
    with store.DOC_LOCK:
        o = store.load_org(slug)
        nd = o.node(nid)
        nd["session_id"] = old_sid
        nd["codex_thread"] = old_sid
        store.save_org(o)
    st = supervisor.state(slug, nid)
    st.pop("last_error", None)
    org = store.load_org(slug)
    n = org.node(nid)
    with _Recorder() as rec:
        supervisor._compact_split_codex_body(
            slug, nid, org, n, old_sid, providers.CODEX_MODELS["sol"])
    err = supervisor.state(slug, nid).get("last_error")
    if err:
        raise AssertionError(f"the compaction body failed instead of "
                             f"reaching the fork: {err}")
    after = store.load_org(slug).node(nid)
    eq(after.get("codex_thread"), "codexsbx-forked-thread",
       "the compaction did not actually land its successor thread")
    return rec.fork_kwarg


# ── the suite ───────────────────────────────────────────────────────────────

def main() -> int:
    print("§0 positive control: the recorder can see a value at all")
    slug, nid = mkorg("control", edit=True, pm="acceptEdits")
    kw, attr = turn_sandbox(slug, nid)
    assert kw != NEVER, (
        "the recorder never saw codexrun.CodexTurn constructed — every "
        "assertion in this file would have been vacuous")
    assert attr != NEVER, "CodexTurn was constructed but exposed no .sandbox"
    eq(kw, attr, "the kwarg and the attribute codexrun puts on the wire")
    check("the CodexTurn recorder observes a real construction", lambda: None)

    fk = fork_sandbox(slug, nid)
    assert fk != NEVER, (
        "the recorder never saw codexrun.compact_fork called — §4 would have "
        "been vacuous")
    check("the compact_fork recorder observes a real call", lambda: None)

    print("§1 a normal turn: the mode follows permission_mode")
    b_slug, b_nid = mkorg("bypass", edit=True, pm="bypassPermissions")
    b_kw, b_attr = turn_sandbox(b_slug, b_nid)
    check("bypassPermissions + edit -> danger-full-access (CodexTurn kwarg)",
          lambda: eq(b_kw, "danger-full-access", "sandbox kwarg"))
    check("…and that is what reaches the thread/start wire",
          lambda: eq(b_attr, "danger-full-access", "turn.sandbox"))

    a_slug, a_nid = mkorg("acceptedits", edit=True, pm="acceptEdits")
    a_kw, _ = turn_sandbox(a_slug, a_nid)
    check("acceptEdits + edit -> workspace-write (unchanged)",
          lambda: eq(a_kw, "workspace-write", "sandbox kwarg"))

    n_slug, n_nid = mkorg("noedit", edit=False, pm="acceptEdits")
    n_kw, _ = turn_sandbox(n_slug, n_nid)
    check("edit off -> read-only (unchanged)",
          lambda: eq(n_kw, "read-only", "sandbox kwarg"))

    print("§2 THE NEGATIVE CASE: no ordinary agent reaches full access")
    #: every mode the ledger can store except the one deliberately privileged
    ordinary = [p for p in PM_LEVELS if p != "bypassPermissions"]
    assert ordinary and len(ordinary) == len(PM_LEVELS) - 1, PM_LEVELS
    leaked: list[tuple[str, bool, str]] = []
    for pm in ordinary:
        for edit in (True, False):
            s, i = mkorg(f"neg {pm} {int(edit)}", edit=edit, pm=pm)
            got, wire = turn_sandbox(s, i)
            eq(got, wire, f"{pm}/edit={edit}: kwarg vs wire")
            # anything that is not one of the two historical answers is a
            # leak, including a value Codex does not define — a typo'd enum
            # must not pass as "well, it isn't danger-full-access"
            if got not in ("read-only", "workspace-write"):
                leaked.append((pm, edit, got))
    check(f"none of {len(ordinary) * 2} non-bypass scopes yields full access",
          lambda: eq(leaked, [], "scopes that leaked out of the two "
                                 "historical sandbox modes"))

    print("§3 the rejected variant: bypass does NOT override the edit switch")
    # Coordinator ruling 2026-09-04 ("option B"). Pinned deliberately: under
    # the rejected reading, turning a switch OFF would make an agent MORE
    # powerful. If someone "simplifies" _codex_sandbox to test permission_mode
    # first, this is the line that goes red.
    x_slug, x_nid = mkorg("bypass noedit", edit=False, pm="bypassPermissions")
    x_kw, x_attr = turn_sandbox(x_slug, x_nid)
    check("bypassPermissions + edit OFF -> read-only, not full access",
          lambda: eq((x_kw, x_attr), ("read-only", "read-only"),
                     "sandbox kwarg / wire value"))

    print("§4 the COMPACTION fork chooses by the same rule")
    check("bypassPermissions + edit -> danger-full-access (compact_fork)",
          lambda: eq(fork_sandbox(b_slug, b_nid), "danger-full-access",
                     "compact_fork sandbox kwarg"))
    check("acceptEdits + edit -> workspace-write (compact_fork)",
          lambda: eq(fork_sandbox(a_slug, a_nid), "workspace-write",
                     "compact_fork sandbox kwarg"))
    check("edit off -> read-only (compact_fork)",
          lambda: eq(fork_sandbox(n_slug, n_nid), "read-only",
                     "compact_fork sandbox kwarg"))
    check("bypass + edit OFF -> read-only (compact_fork)",
          lambda: eq(fork_sandbox(x_slug, x_nid), "read-only",
                     "compact_fork sandbox kwarg"))

    print("§5 the two sites agree for EVERY combination")
    # The split this guards is invisible in production: an agent that runs
    # workspace-write on a normal turn and danger-full-access while compacting
    # (or the reverse) looks correct from either site alone.
    disagree: list[tuple[str, bool, str, str]] = []
    for pm in PM_LEVELS:
        for edit in (True, False):
            s, i = mkorg(f"parity {pm} {int(edit)}", edit=edit, pm=pm)
            t, _ = turn_sandbox(s, i)
            f = fork_sandbox(s, i)
            if t != f:
                disagree.append((pm, edit, t, f))
    check(f"normal turn and compaction agree across all {len(PM_LEVELS) * 2} "
          f"scopes",
          lambda: eq(disagree, [], "scopes where the two sites disagree"))

    print("§6 EVERY turn is governed, not only a thread's first")
    # The app-server does not carry a resumed thread's sandbox forward from
    # its birth — it comes back at the server's own default. So a mode sent on
    # thread/start and forgotten on thread/resume is a control that applies
    # for exactly one turn and then silently stops, while the UI goes on
    # showing it. Measured live (codex-cli 0.153.3): a thread born
    # danger-full-access wrote outside its cwd on turn 1 and was refused by the
    # OS on turn 2 with resume carrying no sandbox; sending it on resume both
    # KEPT full access across a resume and RAISED a workspace-write thread into
    # it. These rows are what fakecodex saw on the wire, not a kwarg.
    w_slug, w_nid = mkorg("wire bypass", edit=True, pm="bypassPermissions")
    rows = wire_sandbox(w_slug, w_nid, turns=2)
    methods = [r["method"] for r in rows]
    check("two turns produce a thread/start then a thread/resume",
          lambda: eq(methods, ["thread/start", "thread/resume"],
                     "the wire calls fakecodex saw"))
    check("the RESUMED turn carries the sandbox key at all",
          lambda: eq([r["present"] for r in rows], [True, True],
                     "sandbox key present on each call"))
    check("both the started and the resumed turn run danger-full-access",
          lambda: eq([r["sandbox"] for r in rows],
                     ["danger-full-access", "danger-full-access"],
                     "sandbox on the wire, turn 1 then turn 2"))

    o_slug, o_nid = mkorg("wire ordinary", edit=True, pm="acceptEdits")
    o_rows = wire_sandbox(o_slug, o_nid, turns=2)
    check("an ordinary agent stays workspace-write on the resumed turn too",
          lambda: eq([r["sandbox"] for r in o_rows],
                     ["workspace-write", "workspace-write"],
                     "sandbox on the wire, turn 1 then turn 2"))

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED, {PASS} passed")
        for label, tb in FAIL:
            print(f"\n--- {label} ---\n{tb}")
        return 1
    print(f"all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        pass
    sys.exit(rc)
