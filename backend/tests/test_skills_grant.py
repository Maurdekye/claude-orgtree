"""The global-skills grant — user ruling 2026-08-07.

    .venv/Scripts/python.exe backend/tests/test_skills_grant.py

No pytest. Plain asserts, `ok N` lines, one final `ALL N CHECKS PASS`.

WHY THIS FILE EXISTS
--------------------
A live report said agents "cannot update their own skills". The measured
cause was TWO facts, not one:

  ① The place they were editing is not a place they load from. A headless
     turn's cwd is its own scratch dir, so PROJECT-scope discovery
     (`<cwd>/.claude/skills`) finds nothing, and the twelve rich skills in a
     granted workspace are writable-but-not-loadable. The only skills a seat
     actually loads are the machine's HOME scope, `~/.claude/skills`.
  ② A write into any path carrying a `.claude` segment hits a SENSITIVE-PATH
     gate that sits ABOVE the permission system: an `Edit(<path>/**)` allow
     rule, an explicit `--add-dir` on the path, `--permission-mode dontAsk`
     and a PreToolUse hook returning `permissionDecision=allow` were each
     measured and each still refused. Only `bypassPermissions` clears it.

The user ruled: every UNSANDBOXED agent on this machine gets the home skills
dir read+write; sandboxed agents do not; and nothing may be plumbed over the
file tools to simulate the access. So the grant is unconditional (reads work
for everyone) and raising `permission_mode` is the one remaining step — which
is why this suite also pins WHO may raise it.

    §1  the grant reaches an unsandboxed turn's argv
    §2  the sandbox never receives it
    §3  the prompt names the loadable path, and is honest about the gate
    §4  who may set bypassPermissions — the security property

⚠ ISOLATION. Every org here is named `zzskl-*`, lives under a throwaway
ORGTREE_DATA, and is removed at exit. Nothing is written to `~/.claude`.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-skltest-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "7409"

from orgtree import sandbox as sbx, store, supervisor            # noqa: E402
from orgtree.ledger import PM_LEVELS, USER, LedgerError          # noqa: E402

PFX = "zzskl-"
assert DATA != os.path.expanduser("~/orgtree"), "refusing to run on the real data root"

supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
sbx.warm = lambda org: None

PASS = 0
NOTES: list[str] = []


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def t(label):
    def deco(fn):
        check(label, fn)
        return fn
    return deco


def note(text):
    NOTES.append(text)
    print(f"       ⚑ {text}")


@atexit.register
def _cleanup():
    shutil.rmtree(DATA, ignore_errors=True)


def mkorg(name, *, sandboxed=False, grant=2):
    org = store.create_org(PFX + name)
    slug = org.d["slug"]
    assert slug.startswith(PFX), slug
    if sandboxed:
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.d["sandbox"] = {"enabled": True, "secret": "c3" * 16}
            store.save_org(o)
    with store.DOC_LOCK:
        o = store.load_org(slug)
        o.hire(USER, None, "haiku", grant, "alice")
        store.save_org(o)
    return slug


def adds(cmd):
    return [cmd[i + 1] for i, x in enumerate(cmd) if x == "--add-dir"]


# ================================================== §1 the unsandboxed grant
print("§1  the grant reaches an unsandboxed turn's argv")
PLAIN = mkorg("plain")
SKILLS = supervisor.GLOBAL_SKILLS

# the constant is the HOME scope, not the cwd scope — the whole point of the
# ruling. A project-scope path would grant a folder no seat ever loads from.
@t("the granted path is the machine's home-scope skills dir")
def _():
    assert SKILLS == os.path.join(os.path.expanduser("~"), ".claude", "skills"), SKILLS
    assert os.path.normcase(SKILLS).endswith(os.path.normcase(
        os.path.join(".claude", "skills"))), SKILLS


REAL = os.path.isdir(SKILLS)
CMD = supervisor._build_cmd(store.load_org(PLAIN), "alice")


@t("an unsandboxed turn carries --add-dir for it")
def _():
    if not REAL:
        note(f"{SKILLS} does not exist on this host — the grant is skipped by "
             f"design (an --add-dir on a missing path is not a grant); "
             f"asserting the SKIP instead")
        assert SKILLS not in adds(CMD), adds(CMD)
        return
    assert SKILLS in adds(CMD), adds(CMD)


@t("…without needing a scope entry — no add_dirs row names it")
def _():
    sc = store.load_org(PLAIN).node("alice")["scope"]
    assert not any(os.path.normcase(os.path.normpath(d["path"]))
                   == os.path.normcase(os.path.normpath(SKILLS))
                   for d in sc["add_dirs"]), sc["add_dirs"]


@t("the standing grant is rw — no deny rule is written against it")
def _():
    import json
    st = json.loads(CMD[CMD.index("--settings") + 1])
    deny = (st.get("permissions") or {}).get("deny", [])
    p = SKILLS.replace("\\", "/").rstrip("/")
    assert not any(p in d for d in deny), deny


# ⚠ this is the honest half of the ruling: the grant alone does NOT make the
# write land. If this check ever goes RED, the CLI's sensitive-path gate
# changed and the bypassPermissions requirement can be dropped everywhere.
@t("…and the node's mode is still acceptEdits — the grant is not the write")
def _():
    sc = store.load_org(PLAIN).node("alice")["scope"]
    assert sc.get("permission_mode") == "acceptEdits", sc.get("permission_mode")
    assert CMD[CMD.index("--permission-mode") + 1] == "acceptEdits"


# ==================================================== §2 the sandbox exclusion
print("\n§2  the sandbox never receives it")
SBX = mkorg("boxed", sandboxed=True)
SCMD = supervisor._build_cmd(store.load_org(SBX), "alice")


@t("☠ a sandboxed turn gets NO host skills path")
def _():
    assert SKILLS not in adds(SCMD), adds(SCMD)


@t("☠ …and no host path at all — the pre-existing container invariant holds")
def _():
    assert all(a.startswith("/home/agent/") for a in adds(SCMD)), adds(SCMD)
    joined = " ".join(SCMD)
    assert ".claude\\skills" not in joined and ".claude/skills" not in joined, \
        joined[:400]


# a sandboxed node raised to bypassPermissions must STILL not reach the host
# home: the exclusion is about the mount, not about the permission mode
@t("☠ …even when that sandboxed node is raised to bypassPermissions")
def _():
    with store.DOC_LOCK:
        o = store.load_org(SBX)
        o.node("alice")["scope"]["permission_mode"] = "bypassPermissions"
        store.save_org(o)
    cmd = supervisor._build_cmd(store.load_org(SBX), "alice")
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    assert SKILLS not in adds(cmd), adds(cmd)


# ======================================================= §3 the prompt is true
print("\n§3  the prompt names the loadable path, and is honest about the gate")


@t("an unsandboxed agent is told WHERE the skills it loads live")
def _():
    p = supervisor.identity_prompt(store.load_org(PLAIN), "alice")
    assert SKILLS in p, p[-700:]


# ⚠ THE REGRESSION THIS PINS. The first version of the line said the home
# scope "holds the skills you actually load — your own folders hold none, so
# a skill written anywhere else will never be available to you". An agent
# measured that false from a live seat: `reso-limits` resolved to
# <granted dir>/.claude/skills/reso-limits with the cwd elsewhere. Discovery
# reads the cwd and every granted directory too, so the line steered agents
# away from the folder they CAN write and toward the one they cannot.
@t("☞ …and NOT told the home scope is the only one — granted folders count")
def _():
    p = supervisor.identity_prompt(store.load_org(PLAIN), "alice")
    low = p.lower()
    for lie in ("your own folders hold none", "never be available",
                "will not load", "the only place"):
        assert lie not in low, f"the corrected prompt still claims: {lie!r}"
    assert ".claude/skills folder inside your cwd or any folder granted" in p, \
        p[-800:]


@t("☞ …and that at acceptEdits the WRITE fails, on the .claude segment")
def _():
    p = supervisor.identity_prompt(store.load_org(PLAIN), "alice")
    assert "bypassPermissions" not in p or "gated ABOVE" in p, p[-800:]
    assert ".claude segment is gated ABOVE the permission system" in p, p[-800:]
    assert "permission REQUEST" in p and "nobody present to approve" in p, \
        p[-800:]


# ⚠ the distinction that resolved the original disagreement, and the reason
# store.py's docstring was corrected in 0474a53: it is NOT a classifier deny.
# An interactive seat HAS an approver and the same write succeeds; a headless
# turn does not, so the request cannot be answered and the write fails.
# "Refused outright" reads as a hard deny and sends the next debugger looking
# for a rule to change instead of an approver to supply.
@t("☞ …and does NOT call it a hard refusal — there is no rule denying it")
def _():
    p = supervisor.identity_prompt(store.load_org(PLAIN), "alice")
    assert "refused outright" not in p, p[-800:]
    assert "not a hard deny" in p, p[-800:]


@t("a bypassPermissions agent is told it CAN write them")
def _():
    with store.DOC_LOCK:
        o = store.load_org(PLAIN)
        o.node("alice")["scope"]["permission_mode"] = "bypassPermissions"
        store.save_org(o)
    p = supervisor.identity_prompt(store.load_org(PLAIN), "alice")
    assert SKILLS in p
    # the acceptEdits branch's warning must be ABSENT, not merely the old
    # wording — this is the pair that keeps §3 from passing on both branches
    assert "permission REQUEST" not in p, p[-800:]
    assert "Writing either is fine" in p, p[-800:]
    with store.DOC_LOCK:
        o = store.load_org(PLAIN)
        o.node("alice")["scope"]["permission_mode"] = "acceptEdits"
        store.save_org(o)


@t("☠ a sandboxed agent is promised nothing about skills")
def _():
    p = supervisor.identity_prompt(store.load_org(SBX), "alice")
    assert "Skills:" not in p, p[-700:]
    assert SKILLS not in p, p[-700:]


# ============================================ §4 who may raise the mode
print("\n§4  who may set bypassPermissions — the security property")


# ⚠ SUPERSEDED PIN. This check used to assert the OPPOSITE — that
# orgtree_retool must never expose permission_mode, so no agent could raise a
# report. User ruling 2026-08-07 (D-102) reversed it: agents DO adjust their
# subordinates' mode, capped at their own. The cap, not the absence of the
# tool, is now the security property, and §5 below is where it is proven.
@t("orgtree_retool exposes permission_mode (D-102 reverses the old pin)")
def _():
    from orgtree import mcptool
    tool = next(t for t in mcptool.TOOLS if t["name"] == "orgtree_retool")
    props = tool["inputSchema"]["properties"]
    assert "permission_mode" in props, sorted(props)
    assert props["permission_mode"]["enum"] == list(PM_LEVELS), \
        props["permission_mode"]
    # the schema must SAY the cap — an agent reads this text, not the ledger
    assert "CAPPED AT YOUR OWN" in props["permission_mode"]["description"]


@t("the USER may raise a node through set_scope")
def _():
    with store.DOC_LOCK:
        o = store.load_org(PLAIN)
        o.set_scope(USER, "alice", permission_mode="bypassPermissions")
        assert o.node("alice")["scope"]["permission_mode"] == "bypassPermissions"
        o.set_scope(USER, "alice", permission_mode="acceptEdits")
        store.save_org(o)


@t("an unknown mode is refused, not silently written")
def _():
    with store.DOC_LOCK:
        o = store.load_org(PLAIN)
        try:
            o.set_scope(USER, "alice", permission_mode="dontAsk")
        except LedgerError:
            pass
        else:
            raise AssertionError("dontAsk was accepted")
        assert o.node("alice")["scope"]["permission_mode"] == "acceptEdits"


# The ORG-level half of the same gap (user report 2026-08-07: "no way of
# modifying an org or node's permission level post org creation"). The org
# field is the BORN-WITH default `_new_node` copies into every hire.
@t("the org's born-with mode is editable after creation")
def _():
    with store.DOC_LOCK:
        o = store.load_org(PLAIN)
        # ⚠ NOT acceptEdits any more, and the reason is worth stating: the
        # check above raised the TOP-LEVEL alice to bypassPermissions, and
        # since 2026-08-08 a capability reaching a top-level agent is ABSORBED
        # into the org's own defaults. Absorption is union-only, so putting
        # alice back did not put the org back — the org ⚙ is the way down.
        assert o.d["permission_mode"] == "bypassPermissions", \
            "the top-level raise did not absorb into the org default"
        o.set_hire_defaults(permission_mode="acceptEdits")
        assert o.d["permission_mode"] == "acceptEdits", "…and it can be lowered"
        o.set_hire_defaults(permission_mode="bypassPermissions")
        store.save_org(o)


@t("…and a hire made after the change is born with it")
def _():
    with store.DOC_LOCK:
        o = store.load_org(PLAIN)
        o.hire(USER, None, "haiku", 2, "bob")
        assert o.node("bob")["scope"]["permission_mode"] == "bypassPermissions"
        store.save_org(o)


@t("☞ …while agents already hired keep the mode they were hired with")
def _():
    o = store.load_org(PLAIN)
    assert o.node("alice")["scope"]["permission_mode"] == "acceptEdits", \
        "changing the org default retroactively raised a live agent"


@t("the org default is validated too — no arbitrary string reaches the argv")
def _():
    with store.DOC_LOCK:
        o = store.load_org(PLAIN)
        try:
            o.set_hire_defaults(permission_mode="plan")
        except LedgerError:
            pass
        else:
            raise AssertionError("'plan' was accepted as an org default")
        assert o.d["permission_mode"] == "bypassPermissions"
        o.set_hire_defaults(permission_mode="acceptEdits")
        store.save_org(o)


# the visitor-open defaults endpoint must never carry it: /defaults is open to
# kiosk visitors by ruling, /settings is frozen for them (api._public_denied)
@t("☠ the visitor-open hire-defaults body has no permission_mode field")
def _():
    from orgtree import api
    assert "permission_mode" not in api.HireDefaults.model_fields, \
        sorted(api.HireDefaults.model_fields)
    assert "permission_mode" in api.Settings.model_fields
    assert api._public_denied("POST", f"/api/orgs/{PLAIN}/settings", PLAIN), \
        "/settings is reachable by a kiosk visitor — the admin-only claim fails"


# ====================================== §5 the cap: nobody grants above self
print("\n§5  agents adjust their subordinates' mode, capped at their own")
CHAIN = mkorg("chain", grant=20)   # alice (top) → bob → carol
with store.DOC_LOCK:
    _o = store.load_org(CHAIN)
    _o.hire("alice", "alice", "haiku", 6, "bob", add_dirs=[],
            tools={"bash": False, "web": False, "edit": True,
                   "subagents": False, "mcp": []},
            org_visibility="team", charter="a report")
    _o.hire("bob", "bob", "haiku", 0, "carol", add_dirs=[],
            tools={"bash": False, "web": False, "edit": True,
                   "subagents": False, "mcp": []},
            org_visibility="team", charter="a grandreport")
    store.save_org(_o)


@t("everyone starts at the org default")
def _():
    o = store.load_org(CHAIN)
    for k in ("alice", "bob", "carol"):
        assert o.node(k)["scope"]["permission_mode"] == "acceptEdits", k


@t("☠ an agent CANNOT raise a report above its own mode")
def _():
    with store.DOC_LOCK:
        o = store.load_org(CHAIN)
        try:
            o.set_scope("alice", "bob", permission_mode="bypassPermissions")
        except LedgerError as e:
            assert "exceeds the parent" in str(e), str(e)
        else:
            raise AssertionError("alice granted above herself")
        assert o.node("bob")["scope"]["permission_mode"] == "acceptEdits"


@t("…nor lift itself — set_scope refuses a self-retool outright")
def _():
    with store.DOC_LOCK:
        o = store.load_org(CHAIN)
        try:
            o.set_scope("alice", "alice", permission_mode="bypassPermissions")
        except LedgerError:
            pass
        else:
            raise AssertionError("alice retooled herself")


@t("an agent CAN lower a report below its own")
def _():
    with store.DOC_LOCK:
        o = store.load_org(CHAIN)
        o.set_scope("alice", "bob", permission_mode="default")
        assert o.node("bob")["scope"]["permission_mode"] == "default"
        store.save_org(o)


@t("☞ …and lowering carries the whole subtree down with it")
def _():
    o = store.load_org(CHAIN)
    assert o.node("carol")["scope"]["permission_mode"] == "default", \
        "carol kept a mode her superior no longer holds"


@t("a hire is born capped at its PARENT, not at the org default")
def _():
    # bob sits at "default" now; the org default is still acceptEdits. Before
    # D-102 the new node copied the ORG value and outranked its own superior.
    with store.DOC_LOCK:
        o = store.load_org(CHAIN)
        assert o.d["permission_mode"] == "acceptEdits"
        o.hire("bob", "bob", "haiku", 0, "dave", add_dirs=[],
               tools={"bash": False, "web": False, "edit": True,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="born under a lowered parent")
        assert o.node("dave")["scope"]["permission_mode"] == "default", \
            o.node("dave")["scope"]["permission_mode"]
        store.save_org(o)


@t("the USER may raise a deep node — and D-106 CASCADES the chain to it")
def _():
    # the live case: the user raised `consultant` under `coordinator` on
    # 2026-08-07. Under D-101 alone that left coordinator behind and the chain
    # non-monotone; D-106 (later, same day) rules that the agents BETWEEN the
    # granter and the grantee receive what they were missing instead.
    with store.DOC_LOCK:
        o = store.load_org(CHAIN)
        r = o.set_scope(USER, "carol", permission_mode="bypassPermissions")
        assert o.node("carol")["scope"]["permission_mode"] == "bypassPermissions"
        assert o.node("bob")["scope"]["permission_mode"] == "bypassPermissions", \
            "the chain between the user and carol was left below her"
        assert o.node("alice")["scope"]["permission_mode"] == "bypassPermissions", \
            "the cascade stopped short — every node up to the granter rises"
        # and the actor is TOLD, in the words the ruling asked for
        assert r.get("cascaded") == ["alice", "bob"], r.get("cascaded")
        assert any(w.startswith("cascaded permission increase to agents")
                   for w in r["warnings"]), r["warnings"]
        store.save_org(o)


# ⚠ THE SILENT-REVOCATION GUARD. The subtree sweep runs on ANY capability
# change, so a routine folder retool anywhere up the chain must not quietly
# undo the user's deliberate above-parent grant. The sweep re-clamps
# DESCENDANTS against the edited node, never the edited node against its own
# parent (_sweep_dirs clamp_root=False); a MOVE still clamps its root.
@t("☠ an unrelated retool upstream does NOT revoke that user grant")
def _():
    with store.DOC_LOCK:
        o = store.load_org(CHAIN)
        o.set_scope(USER, "bob", org_visibility="self")   # a cap change: sweeps
        assert o.node("carol")["scope"]["permission_mode"] == "bypassPermissions", \
            "a visibility retool silently revoked a user-granted mode"
        store.save_org(o)


# …and the same-value guard must not become a hole: a REAL lowering still
# propagates DOWN. Note the asymmetry D-106 introduces and this pins: a raise
# travels UP to the granter (the cascade), a revocation travels DOWN into the
# subtree (the sweep). Neither direction is the other's inverse — pushing a
# revocation upward would strip a manager for its report's sake.
@t("☞ …but a REAL lowering still reaches the reports below it")
def _():
    with store.DOC_LOCK:
        o = store.load_org(CHAIN)
        # the cascade above left the whole chain at bypassPermissions
        assert o.node("carol")["scope"]["permission_mode"] == "bypassPermissions"
        o.set_scope(USER, "bob", permission_mode="default")         # a lowering
        assert o.node("carol")["scope"]["permission_mode"] == "default", \
            "revoking a superior's mode left the grant it covered in place"
        assert o.node("alice")["scope"]["permission_mode"] == "bypassPermissions", \
            "a lowering travelled UPWARD and stripped the manager above it"
        store.save_org(o)


# ============================ §6 the cascade carries folders into the org
print("\n§6  a bubbled folder grant becomes an ORG folder")
DIRS = mkorg("dirs", grant=30)
with store.DOC_LOCK:
    _o = store.load_org(DIRS)
    _o.hire(USER, "alice", "haiku", 4, "bob", add_dirs=[],
            tools={"bash": False, "web": False, "edit": True,
                   "subagents": False, "mcp": []},
            org_visibility="team", charter="a report")
    store.save_org(_o)
NEWDIR = os.path.join(DATA, "granted-later")
os.makedirs(NEWDIR, exist_ok=True)


@t("a folder granted DEEP bubbles to every superior")
def _():
    with store.DOC_LOCK:
        o = store.load_org(DIRS)
        r = o.set_scope(USER, "bob", add_dirs=[{"path": NEWDIR, "mode": "rw"}])
        assert any(d["path"] == NEWDIR
                   for d in o.node("alice")["scope"]["add_dirs"]), \
            o.node("alice")["scope"]["add_dirs"]
        assert r.get("cascaded") == ["alice"], r.get("cascaded")
        store.save_org(o)


# ⚠ the user report this section exists for: the bubble reached the top-level
# agent, so the ORG demonstrably holds the folder — but its own holdings list
# did not say so. The eye showed fewer folders than its own agent held, and a
# later top-level hire would not have inherited it.
@t("☞ …and the ORG's own holdings record it once it reaches a top-level")
def _():
    o = store.load_org(DIRS)
    assert any(d["path"] == NEWDIR for d in o.d["dirs"]), \
        [d["path"] for d in o.d["dirs"]]


@t("…so a NEW top-level hire inherits it by default")
def _():
    with store.DOC_LOCK:
        o = store.load_org(DIRS)
        o.hire(USER, None, "haiku", 1, "carol")
        assert any(d["path"] == NEWDIR
                   for d in o.node("carol")["scope"]["add_dirs"]), \
            o.node("carol")["scope"]["add_dirs"]
        store.save_org(o)


@t("☠ …but revoking one agent's grant does NOT strip the org's holding")
def _():
    with store.DOC_LOCK:
        o = store.load_org(DIRS)
        o.set_scope(USER, "bob", add_dirs=[])
        assert any(d["path"] == NEWDIR for d in o.d["dirs"]), \
            "the union is monotone — one node losing a grant is not the org " \
            "losing the folder"
        store.save_org(o)


# ── the same rule for EVERY capability, not just folders (user follow-up
# ruling 2026-08-08). A top-level agent has no parent to inherit from, so the
# org document IS its ceiling and the record of what this org can reach.
@t("☞ tools, visibility and MODE absorb into the org the same way")
def _():
    with store.DOC_LOCK:
        o = store.load_org(DIRS)
        o.d["default_tools"] = {"bash": False, "web": False, "edit": False,
                                "subagents": False, "mcp": []}
        o.d["default_visibility"] = "self"
        o.node("alice")["scope"]["tools"] = dict(o.d["default_tools"])
        o.node("alice")["scope"]["org_visibility"] = "self"
        r = o.set_scope(USER, "bob",
                        tools={"bash": True, "web": True, "edit": False,
                               "subagents": False, "mcp": ["alpha"]},
                        org_visibility="full",
                        permission_mode="bypassPermissions")
        dt = o.d["default_tools"]
        assert dt["bash"] and dt["web"], dt
        assert not dt["edit"] and not dt["subagents"], \
            "the org absorbed a capability that was never granted"
        assert "alpha" in dt["mcp"], dt
        assert o.d["default_visibility"] == "full", o.d["default_visibility"]
        assert o.d["permission_mode"] == "bypassPermissions", \
            o.d["permission_mode"]
        assert any("the organization now holds" in w for w in r["warnings"]), \
            r["warnings"]
        store.save_org(o)


# ⚠ the consequence of absorbing the MODE, stated as its own check because it
# is the widest blast radius in this feature: every FUTURE top-level hire is
# born at the raised mode. Existing agents are untouched (D-101).
@t("☠ …so a NEW top-level hire is born at the absorbed mode")
def _():
    with store.DOC_LOCK:
        o = store.load_org(DIRS)
        o.hire(USER, None, "haiku", 1, "dave")
        assert o.node("dave")["scope"]["permission_mode"] == "bypassPermissions", \
            o.node("dave")["scope"]["permission_mode"]
        store.save_org(o)


@t("☠ an AGENT actor never reaches the org level, whatever it grants")
def _():
    with store.DOC_LOCK:
        o = store.load_org(DIRS)
        before = json.dumps(o.d["default_tools"], sort_keys=True)
        # alice is top-level; a grant she makes to bob cannot touch the org
        # doc, because the bubble between her and bob is empty and she is
        # never on it herself
        # only what alice herself holds — the point here is the ORG doc, not
        # the cap (which §5 covers); granting above her own would refuse first
        o.set_scope("alice", "bob", tools={"bash": True, "web": True,
                                           "edit": False, "subagents": False,
                                           "mcp": []})
        assert json.dumps(o.d["default_tools"], sort_keys=True) == before, \
            "an agent's grant rewrote the ORG's defaults"


# ============================================================== the report
if NOTES:
    print(f"\n{len(NOTES)} NOTE(S):")
    for m in NOTES:
        print(f"  · {m}")
print(f"\nALL {PASS} CHECKS PASS")
