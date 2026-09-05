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
import subprocess
import sys
import tempfile
import time
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

#: THE PIN. Every (permission_mode, edit) combination the ledger can store,
#: and the OS sandbox mode it must produce. §7 asserts this whole table at
#: BOTH call sites, so changing any cell of the product's behaviour requires
#: moving a line here deliberately — §2 only asserts membership in the two
#: historical answers, which a `plan` change would have slipped past.
EXPECTED_SANDBOX = {
    # plan is the read-only planning seat and the MOST restrictive mode in
    # PM_LEVELS; before 2026-09-04 it produced workspace-write, i.e. bought
    # the operator nothing at all
    ("plan", True): "read-only",
    ("plan", False): "read-only",
    ("default", True): "workspace-write",
    ("default", False): "read-only",
    ("acceptEdits", True): "workspace-write",
    ("acceptEdits", False): "read-only",
    ("bypassPermissions", True): "danger-full-access",
    # the edit switch wins over bypass — turning a switch OFF must never make
    # an agent MORE powerful
    ("bypassPermissions", False): "read-only",
}


def mkorg_dirs(label: str, dirs: list[str]) -> tuple[str, str]:
    """One codex-tier node holding `dirs` as rw grants (org first, then node —
    the ledger clamps a node's grant against the org's)."""
    grants = [{"path": p, "mode": "rw"} for p in dirs]
    # create_org takes plain path STRINGS; the node grant takes {path, mode}
    org = store.create_org(f"zz codexsbx {label}", list(dirs))
    nid = org.hire(USER, None, "sol", 0, "cx", add_dirs=grants,
                   tools={"bash": True, "web": False, "edit": True,
                          "subagents": False, "mcp": []},
                   org_visibility="team", charter="a git-trust test agent")["node"]
    store.save_org(org)
    held = [d["path"] for d in
            store.load_org(org.d["slug"]).node(nid)["scope"]["add_dirs"]]
    eq(sorted(held), sorted(dirs), f"{label}: dirs actually granted")
    return org.d["slug"], nid


def git_trust(slug: str, nid: str) -> dict[str, str]:
    """The GIT_CONFIG_* entries in the env a real codex spawn would receive.

    Read off `_codex_process_spec`'s `env_extra` — the exact dict handed to
    `codexrun.AppServerClient` — not off the helper that builds them.
    """
    org = store.load_org(slug)
    spec = supervisor._codex_process_spec(org, nid, write_ident=False)
    return {k: v for k, v in spec["env_extra"].items()
            if k.startswith("GIT_CONFIG_")}


def child_env(slug: str, nid: str, keys: list[str]) -> dict:
    """What the codex PROCESS actually sees. fakecodex writes the named env
    keys to a file at turn start, so this proves the variables survive the
    spawn rather than merely being computed."""
    probe = os.path.join(tempfile.mkdtemp(prefix="codexsbx-env-"), "e.json")
    os.environ["FAKECODEX_ENVPROBE"] = ",".join(keys)
    os.environ["FAKECODEX_ENVPROBE_PATH"] = probe
    try:
        st = supervisor.state(slug, nid)
        with supervisor._state_lock:
            st["busy"] = True
        supervisor._run_one_turn(
            slug, nid, {"text": "env probe", "view": "env probe"})
    finally:
        os.environ.pop("FAKECODEX_ENVPROBE", None)
        os.environ.pop("FAKECODEX_ENVPROBE_PATH", None)
    if not os.path.exists(probe):
        raise AssertionError("fakecodex never wrote the env probe — the turn "
                             "did not reach the child, so this is vacuous")
    with open(probe, encoding="utf-8") as f:
        return json.load(f)


def mkorg(label: str, *, edit: bool, pm: str, bash: bool = True) -> tuple[str, str]:
    """One org, one codex-tier node carrying the scope under test.

    `permission_mode` is set through the real `set_scope` door (which clamps
    against the kiosk ceiling) rather than poked into the doc, so the suite
    exercises a scope the product can actually produce.
    """
    org = store.create_org(f"zz codexsbx {label}")
    nid = org.hire(USER, None, "sol", 0, "cx", add_dirs=[],
                   tools={"bash": bash, "web": False, "edit": edit,
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


def approval_decisions(slug: str, nid: str) -> dict[str, object]:
    """Run one real turn in which the app-server ASKS for both approvals, and
    return what `_codex_leg._approve` answered.

    This is the ⚙-rights seam and nothing exercised it before. It matters
    because it is a SECOND gate on the same permission as the OS sandbox: if
    it approves a write the sandbox forbids, the approval is decoration; if it
    declines one the sandbox allows, the agent is quietly crippled. §8 asserts
    the two agree rather than checking either alone.
    """
    probe = os.path.join(tempfile.mkdtemp(prefix="codexsbx-appr-"), "a.json")
    os.environ["FAKECODEX_SCENARIO"] = "approval"
    os.environ["FAKECODEX_APPROVALPROBE"] = probe
    try:
        st = supervisor.state(slug, nid)
        with supervisor._state_lock:
            st["busy"] = True
        supervisor._run_one_turn(
            slug, nid, {"text": "approval probe", "view": "approval probe"})
    finally:
        os.environ["FAKECODEX_SCENARIO"] = "stall"
        os.environ.pop("FAKECODEX_APPROVALPROBE", None)
    if not os.path.exists(probe):
        raise AssertionError(
            "the app-server double never recorded an approval round trip — "
            "the seam under test did not run, so any verdict here is vacuous")
    with open(probe, encoding="utf-8") as f:
        rows = json.load(f)
    return {str(r["method"]).split("/")[1]: r["decision"] for r in rows}


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

    print("§7 THE PIN: the whole (mode × edit) table, at both sites")
    wrong: list[str] = []
    for (pm, edit), want in sorted(EXPECTED_SANDBOX.items()):
        s, i = mkorg(f"pin {pm} {int(edit)}", edit=edit, pm=pm)
        got_turn, got_wire = turn_sandbox(s, i)
        got_fork = fork_sandbox(s, i)
        for where, got in (("turn", got_turn), ("wire", got_wire),
                           ("fork", got_fork)):
            if got != want:
                wrong.append(f"{pm}/edit={edit} {where}: {got} != {want}")
    check(f"all {len(EXPECTED_SANDBOX)} scopes × 3 sites match the pinned "
          f"table", lambda: eq(wrong, [], "cells that disagree with the pin"))

    print("§8 the APPROVAL seam agrees with the sandbox, cell by cell")
    # Two gates on one permission. Asserted against the sandbox mode OBSERVED
    # in the same fixture, not against a rule this file restates — so the two
    # cannot be made to agree by editing the test.
    mismatched: list[str] = []
    file_answers: set[object] = set()
    cmd_answers: set[object] = set()
    for (pm, edit), want in sorted(EXPECTED_SANDBOX.items()):
        s, i = mkorg(f"appr {pm} {int(edit)}", edit=edit, pm=pm)
        got_turn, _ = turn_sandbox(s, i)
        d = approval_decisions(s, i)
        file_answers.add(d.get("fileChange"))
        cmd_answers.add(d.get("commandExecution"))
        expect = "decline" if got_turn == "read-only" else "accept"
        if d.get("fileChange") != expect:
            mismatched.append(
                f"{pm}/edit={edit}: sandbox {got_turn} but fileChange "
                f"{d.get('fileChange')!r} (wanted {expect})")
        # ⚠ THE COMMAND BRANCH IS HELD TO THE SAME CELL (user ruling
        # 2026-09-05). This asserted `== "accept"` for every permission_mode —
        # the old carve-out — and that is precisely what let a `plan` seat
        # write: a commandExecution approval is codex asking to re-run a
        # sandbox-blocked command OUTSIDE the sandbox, so accepting one on a
        # read-only node returns the write the sandbox had just refused.
        # bash is ON in every fixture here, so anything but the sandbox's own
        # answer is the hole reopening.
        if d.get("commandExecution") != expect:
            mismatched.append(
                f"{pm}/edit={edit}: sandbox {got_turn} but commandExecution "
                f"{d.get('commandExecution')!r} with bash ON "
                f"(wanted {expect})")
    check("both approval decisions match the sandbox in every scope",
          lambda: eq(mismatched, [], "gates that disagree"))
    # ANTI-VACUITY: if `_approve` answered "accept" everywhere, the loop above
    # would still pass for the accept-side cells and quietly compare a
    # constant. Both answers must actually occur, on BOTH branches.
    check("the approval seam produced BOTH accept and decline",
          lambda: eq((sorted(str(a) for a in file_answers),
                      sorted(str(a) for a in cmd_answers)),
                     (["accept", "decline"], ["accept", "decline"]),
                     "distinct fileChange / commandExecution answers"))
    # …and the command branch must be able to say no at all, or "accept
    # everywhere" above proves nothing about it either
    nb_slug, nb_nid = mkorg("appr nobash", edit=True, pm="acceptEdits",
                            bash=False)
    nb = approval_decisions(nb_slug, nb_nid)
    check("bash OFF still declines commandExecution (control)",
          lambda: eq((nb.get("commandExecution"), nb.get("fileChange")),
                     ("decline", "accept"),
                     "command/file decisions with bash off, edit on"))

    print("§9 CodexTurn.close(): real teardown, and loud when it must not act")
    # Lives here rather than in test_codexrun.py because it is the same
    # process-boundary work: the missing close() is what made the resume
    # probe for §6 fail with "already has an active writer" — a swallowed
    # AttributeError that read as teardown and leaked the whole app-server
    # tree, still holding the thread's ~/.codex write lock.
    argv = providers.codex_argv(FAKECODEX)
    own_cwd = tempfile.mkdtemp(prefix="codexsbx-close-")
    owned = codexrun.CodexTurn(argv, cwd=own_cwd, model=None, effort=None,
                               thread_id=None)
    own_proc = owned.client.proc
    check("an app-server the turn created is running before close",
          lambda: eq(own_proc.poll(), None, "exit code while alive"))
    owned.close()
    deadline = time.time() + 10
    while own_proc.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    check("close() on an OWNED client actually ends the process",
          lambda: eq(own_proc.poll() is not None, True,
                     "process exited after close()"))

    lent = codexrun.AppServerClient(argv, cwd=own_cwd)
    borrower = codexrun.CodexTurn(argv, cwd=own_cwd, model=None, effort=None,
                                  thread_id=None, client=lent)
    raised = ""
    try:
        borrower.close()
    except RuntimeError as e:
        raised = str(e)
    check("close() on a BORROWED client raises instead of killing it",
          lambda: eq(bool(raised) and "BORROWED" in raised, True,
                     f"RuntimeError mentioning the borrow (got {raised!r})"))
    # …and the refusal must be a refusal, not a kill with a complaint
    check("the borrowed app-server is still alive after the refusal",
          lambda: eq(lent.proc.poll(), None, "exit code of the pooled client"))
    lent.close()

    print("§10 git ownership trust, scoped to the dirs already granted")
    # A codex turn's shell runs as Pendragon\CodexSandboxOffline, not the
    # operator, so git refuses an operator-owned repo outright: "detected
    # dubious ownership". That refusal is independent of the sandbox and no
    # sandbox mode fixes it. safe.directory rides the PROCESS ENV so nothing
    # is written to any config file.
    # ⚠ realpath: %TEMP% here is the 8.3 SHORT path
    # (C:\Users\NCOLA_~1\...) while git reports the long one, and
    # safe.directory is a textual match — a short-path fixture makes the
    # behaviour legs below fail for a reason that has nothing to do with
    # the code under test.
    d1 = os.path.realpath(tempfile.mkdtemp(prefix="codexsbx-g1-"))
    d2 = os.path.realpath(tempfile.mkdtemp(prefix="codexsbx-g2-"))
    g_slug, g_nid = mkorg_dirs("granted", [d1, d2])
    trust = git_trust(g_slug, g_nid)
    # …and, since safe.directory is EXACT-MATCH, the repositories INSIDE each
    # granted dir: a worktree under a granted scratch folder is the ordinary
    # case, and `<dir>` alone leaves it untrusted. Both forms are needed —
    # `<dir>/*` does not cover `<dir>` itself (measured, git 2.52).
    exact = sorted(p.replace("\\", "/") for p in (d1, d2))
    want_paths = sorted(exact + [p + "/*" for p in exact])
    got_paths = sorted(v for k, v in trust.items()
                       if k.startswith("GIT_CONFIG_VALUE_"))
    check("each granted dir becomes safe.directory for itself AND for the "
          "repos nested inside it",
          lambda: eq(got_paths, want_paths, "safe.directory values"))
    check("every key entry is safe.directory and the count matches",
          lambda: eq(([v for k, v in sorted(trust.items())
                       if k.startswith("GIT_CONFIG_KEY_")],
                      trust.get("GIT_CONFIG_COUNT")),
                     (["safe.directory"] * 4, "4"),
                     "keys and count"))
    # THE BOUNDARY. A wildcard is only safe because of where it is anchored:
    # every one must sit under a path the node already holds, and the bare
    # `*` (trust every repository on the machine) must never appear.
    check("every wildcard entry is anchored on a granted dir, none is bare",
          lambda: eq(sorted(v for v in got_paths if v.endswith("*")),
                     sorted(p + "/*" for p in exact),
                     "wildcard entries"))

    # THE NEGATIVE THAT MATTERS: no grant must mean no trust, and nothing
    # anywhere may be a blanket wildcard
    n_slug, n_nid = mkorg_dirs("nogrant", [])
    check("a node with NO granted dirs gets no GIT_CONFIG_* at all",
          lambda: eq(git_trust(n_slug, n_nid), {}, "git trust env"))
    check("no entry is ever the blanket safe.directory=*",
          lambda: eq([v for v in trust.values() if v == "*"], [],
                     "wildcard trust entries"))

    # …and it must actually reach the child process, not merely be computed
    seen = child_env(g_slug, g_nid,
                     ["GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0",
                      "GIT_CONFIG_VALUE_0"])
    check("the codex PROCESS really receives the trust env",
          lambda: eq((seen.get("GIT_CONFIG_COUNT"),
                      seen.get("GIT_CONFIG_KEY_0"),
                      seen.get("GIT_CONFIG_VALUE_0") in want_paths),
                     ("4", "safe.directory", True),
                     "env as fakecodex saw it"))

    # ⚠ AND NOW THE BEHAVIOUR, because everything above is the SHAPE of an
    # env var and would stay green if git ignored every entry in it. Real
    # `git` is run against a real repo nested inside a granted dir, with the
    # env this node's spawn would carry.
    #
    # git's ownership check only fires when the repo is owned by someone else,
    # and in this suite the operator owns everything — so without a forced
    # mismatch every leg passes and the check is worth nothing. git's own
    # GIT_TEST_ASSUME_DIFFERENT_OWNER forces it; the NO-TRUST leg below is the
    # positive control proving the mismatch is really in effect.
    def _mkrepo(path):
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith("GIT_CONFIG")}
        os.makedirs(path, exist_ok=True)
        for args in (["git", "init", "-q"],
                     ["git", "config", "user.email", "p@example.invalid"],
                     ["git", "config", "user.name", "p"]):
            subprocess.run(args, cwd=path, check=True, capture_output=True,
                           env=clean)
        with open(os.path.join(path, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("probe\n")
        subprocess.run(["git", "add", "f.txt"], cwd=path, check=True,
                       capture_output=True, env=clean)
        subprocess.run(["git", "commit", "-qm", "p"], cwd=path, check=True,
                       capture_output=True, env=clean)

    def _git_ok(repo, extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("GIT_CONFIG")}
        env["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
        env.update(extra)
        p = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%H"],
                           capture_output=True, text=True, env=env)
        return p.returncode == 0

    nested = os.path.join(d1, "wt", "deeper")     # a worktree-shaped path
    outside = os.path.join(
        os.path.realpath(tempfile.mkdtemp(prefix="codexsbx-out-")), "repo")
    _mkrepo(nested)
    _mkrepo(outside)
    check("POSITIVE CONTROL: without the trust env git REFUSES the nested "
          "repo (so the checks below are not free)",
          lambda: eq(_git_ok(nested, {}), False, "git log with no trust"))
    check("a repo nested inside a granted dir is trusted by the spawn env",
          lambda: eq(_git_ok(nested, trust), True,
                     "git log under the node's own trust env"))
    check("…and a repo OUTSIDE every granted dir is still refused",
          lambda: eq(_git_ok(outside, trust), False,
                     "git log on an ungranted repo"))
    # …and the granted dir ITSELF, which the wildcard does NOT cover (`/*`
    # matches what is under a path, not the path) — so this is the exact
    # entry's job and it is not redundant with the one above
    _mkrepo(d2)
    check("the granted dir itself is trusted too, by the exact entry",
          lambda: eq(_git_ok(d2, trust), True,
                     "git log on the granted dir as a repo"))

    # an operator who set their own GIT_CONFIG_* must not have it silently
    # dropped — the node's entries append above the inherited count
    os.environ["GIT_CONFIG_COUNT"] = "1"
    try:
        offset = git_trust(g_slug, g_nid)
    finally:
        os.environ.pop("GIT_CONFIG_COUNT", None)
    check("inherited GIT_CONFIG_* is preserved, ours appends above it",
          lambda: eq((offset.get("GIT_CONFIG_COUNT"),
                      "GIT_CONFIG_KEY_0" in offset,
                      offset.get("GIT_CONFIG_KEY_1")),
                     ("5", False, "safe.directory"),
                     "count / index 0 untouched / first appended key"))

    print("§11 the codex lane is TOLD that a sandbox denial can be retried")
    # A reserve agent hit the .git denial, correctly refused to force it, and
    # reported itself blocked when it was one approved retry away. The text
    # must also say the opposite case out loud, or it reads as licence.
    guide = supervisor.identity_prompt(store.load_org(g_slug), g_nid)
    check("the codex identity prompt explains the entitled-write retry",
          lambda: eq(("ask to retry the command with elevated permission"
                      in guide,
                      "the denial is real and stands" in guide),
                     (True, True), "both halves of the guidance"))
    check("…and it names the .git case that actually bit us",
          lambda: eq("repository's `.git` folder is blocked" in guide, True,
                     "the concrete cause is named"))
    # a CLAUDE-tier node must not be told any of this: its lane has no codex
    # ⚠ `opus`, NOT `luna` — luna is a CODEX tier here (CODEX_TIERS is astra,
    # gpt-reserve, luna, sol, terra). The first draft of this check used luna
    # and went red for the right reason against correct production code.
    # sandbox and no approval retry, so the text would be a lie there
    c_org = store.create_org("zz codexsbx claudelane")
    c_nid = c_org.hire(USER, None, "opus", 0, "cl", add_dirs=[],
                       tools={"bash": True, "web": False, "edit": True,
                              "subagents": False, "mcp": []},
                       org_visibility="team", charter="claude lane")["node"]
    store.save_org(c_org)
    c_guide = supervisor.identity_prompt(store.load_org(c_org.d["slug"]), c_nid)
    check("a claude-tier node is NOT given the codex sandbox text",
          lambda: eq("elevated permission" in c_guide, False,
                     "codex-only guidance leaking to the claude lane"))

    print("§12 a READ-ONLY codex seat is told the retry route is closed to it")
    # §11's guidance is now a promise only a write-enabled seat can keep. Told
    # to a `plan` or `edit=off` node — whose commandExecution approvals §8 now
    # declines — it produces exactly the failure §11 was written to prevent,
    # one layer up: the agent asks, is refused, and reports itself blocked
    # without ever being told why. So the text must SPLIT, and each seat hear
    # what is true for it.
    for pm, edit in (("plan", True), ("acceptEdits", False), ("plan", False)):
        s, i = mkorg(f"guide {pm} {int(edit)}", edit=edit, pm=pm)
        g = supervisor.identity_prompt(store.load_org(s), i)
        check(f"{pm}/edit={edit}: told the retry WILL BE REFUSED, and not "
              f"told it will be approved",
              lambda g=g: eq(("WILL BE REFUSED" in g,
                              "it will be approved" in g),
                             (True, False),
                             "read-only wording present / write wording gone"))
        # …and it must not leave the agent thinking every READ still works.
        # Two were measured to stop: git outside the cwd (dubious ownership)
        # and PowerShell .NET method calls (constrained language mode). An
        # agent not told these reads as "the sandbox is broken".
        check(f"{pm}/edit={edit}: names the reads that are blocked too",
              lambda g=g: eq(("dubious ownership" in g,
                              "constrained language mode" in g),
                             (True, True), "both measured read limits named"))
        # …and hands over the measured way THROUGH the git one, with the
        # caveat that makes it safe to offer. `git -c safe.directory=<exact
        # repo> -C <repo> log` read a nested repo with zero approvals asked
        # from a seat declining every escalation; the same -c aimed at the
        # PARENT still failed. Naming the form without "not OS write
        # permissions" would read as a sanctioned way to get a write.
        check(f"{pm}/edit={edit}: offers the command-scoped safe.directory "
              f"read, and says it grants no write",
              lambda g=g: eq(("safe.directory=<that repo's exact path>" in g,
                              "not OS write permissions" in g,
                              "never a parent path" in g),
                             (True, True, True),
                             "the form / the no-write caveat / no wildcards"))
    # ANTI-VACUITY, and the regression guard for the landing route: the
    # write-enabled seat must still get the ORIGINAL text. If the branch above
    # were unconditional, every check in §12 would pass and §11's agent would
    # silently lose the guidance that unblocked it.
    w_slug, w_nid = mkorg("guide writer", edit=True, pm="default")
    w_guide = supervisor.identity_prompt(store.load_org(w_slug), w_nid)
    check("a write-enabled codex seat still gets the approved-retry text",
          lambda: eq(("it will be approved" in w_guide,
                      "WILL BE REFUSED" in w_guide),
                     (True, False), "write-enabled wording, read-only absent"))

    print("§13 the seam's answers are BOOKED: denials with their command, and "
          "approvals at all")
    # Two defects, measured hermetically 2026-09-05 and user-approved to fix:
    # a codex command denial reached the desk as a chip with NO command
    # (`_approve` stored the wire's string `command` where `_tool_arg` wants a
    # dict), and an ACCEPTED escalation — a sandbox-blocked command orgtree
    # let out — left no trace anywhere. The rows below are read off the
    # persisted node after a real turn through the app-server double, whose
    # approval requests now carry the measured v2 fields.
    def rows(slug, nid, key):
        return list(store.load_org(slug).node(nid).get(key) or [])

    # POSITIVE: bash ON → both requests approved → two approval rows, no
    # denial rows, and the command row shows its command AND its cwd
    a_slug, a_nid = mkorg("book approve", edit=True, pm="default")
    approval_decisions(a_slug, a_nid)
    appr = rows(a_slug, a_nid, "last_approvals")
    dens = rows(a_slug, a_nid, "last_denials")
    check("bash ON: two approvals booked, zero denials",
          lambda: eq((sorted(r["tool"] for r in appr), dens),
                     (["commandExecution", "fileChange"], []),
                     "approval tools / denial rows"))
    cmd_row = next((r for r in appr if r["tool"] == "commandExecution"), {})
    check("…the approved command row carries the command and its cwd",
          lambda: eq((cmd_row.get("arg"), cmd_row.get("cwd")),
                     ("echo probe", "C:\\fake\\scratch\\probe-cwd"),
                     "arg / cwd off the wire's string command"))
    file_row = next((r for r in appr if r["tool"] == "fileChange"), {})
    check("…the approved fileChange row identifies the item (v2 carries no "
          "paths)", lambda: eq(file_row.get("arg"), "patch-probe",
                               "itemId as the identifier"))
    ring = store.load_org(a_slug).node(a_nid).get("turns") or []
    check("…and the turn ring counts them as approvals, not denials",
          lambda: eq((ring[-1].get("approvals"), ring[-1].get("denials")),
                     (2, 0), "last TurnStat approvals/denials"))

    # NEGATIVE: bash OFF → the command is DECLINED and its row now carries
    # the command text that used to be blank; the file change is still
    # approved and lands on the other list
    d_slug, d_nid = mkorg("book deny", edit=True, pm="default", bash=False)
    approval_decisions(d_slug, d_nid)
    dens = rows(d_slug, d_nid, "last_denials")
    appr = rows(d_slug, d_nid, "last_approvals")
    check("bash OFF: the command denial is booked WITH its command",
          lambda: eq([(r["tool"], r.get("arg"), r.get("cwd")) for r in dens],
                     [("commandExecution", "echo probe",
                       "C:\\fake\\scratch\\probe-cwd")],
                     "denial rows (was arg='' before 2026-09-05)"))
    check("…and only the file change sits on the approvals list",
          lambda: eq([r["tool"] for r in appr], ["fileChange"],
                     "approval rows with bash off"))
    ring = store.load_org(d_slug).node(d_nid).get("turns") or []
    check("…ring: 1 denial, 1 approval",
          lambda: eq((ring[-1].get("denials"), ring[-1].get("approvals")),
                     (1, 1), "last TurnStat"))

    # NOT-OBSERVED CONTROL: a result with no `permission_approvals` key at
    # all (the claude and AGY legs never send one) must leave the node
    # WITHOUT `last_approvals` and the ring entry WITHOUT `approvals` —
    # absence means "no seam ran", and `[]`/`0` would read as "the seam ran
    # and approved nothing". Driven through the same `_after_turn` the legs
    # call, on the node that just booked two approvals, so the field has to
    # be actively removed, not merely never written.
    org = store.load_org(a_slug)
    supervisor._after_turn(
        a_slug, a_nid, org,
        {"status": "completed", "total_cost_usd": 0.01, "duration_ms": 1,
         "usage": {}, "permission_denials": []},
        supervisor.state(a_slug, a_nid), 100)
    after = store.load_org(a_slug).node(a_nid)
    check("a lane that cannot report approvals leaves the field ABSENT, "
          "not empty",
          lambda: eq(("last_approvals" in after,
                      "approvals" in (after.get("turns") or [{}])[-1],
                      after.get("last_denials")),
                     (False, False, []),
                     "no last_approvals / no ring approvals / denials still "
                     "booked as []"))

    # TRUNCATION CONTROL: the detail rows are capped at 8 because they ride on
    # the node document; the ring's COUNT is not, and must not be the length
    # of the capped list. Nine approved escalations reported as eight is a
    # short count on the one number that says how much the seam let out.
    # Driven straight through `_after_turn` — nine live approval round trips
    # would measure the double's scheduling, not this arithmetic.
    # BOTH counts, because both were the length of the capped list — the
    # denials one since №7 shipped, and it is the same claim about the same
    # kind of number.
    def nine(kind):
        return [{"tool_name": kind,
                 "tool_input": {"command": f"echo {i}", "cwd": "C:\\fake\\w"}}
                for i in range(9)]

    def drive(res_extra):
        org = store.load_org(a_slug)
        supervisor._after_turn(
            a_slug, a_nid, org,
            {"status": "completed", "total_cost_usd": 0.01, "duration_ms": 1,
             "usage": {}, **res_extra},
            supervisor.state(a_slug, a_nid), 100)
        n = store.load_org(a_slug).node(a_nid)
        return n, (n.get("turns") or [{}])[-1]

    after9, ring9 = drive({"permission_denials": [],
                           "permission_approvals": nine("commandExecution")})
    check("nine approvals: 8 detail rows, but the ring counts NINE",
          lambda: eq((len(after9.get("last_approvals") or []),
                      ring9.get("approvals")),
                     (8, 9),
                     "capped rows / true count (was 8 before 2026-09-05)"))

    d9, dring9 = drive({"permission_denials": nine("Bash")})
    check("nine denials: 8 detail rows, but the ring counts NINE",
          lambda: eq((len(d9.get("last_denials") or []),
                      dring9.get("denials")),
                     (8, 9),
                     "capped rows / true count (was 8 before 2026-09-05)"))

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
