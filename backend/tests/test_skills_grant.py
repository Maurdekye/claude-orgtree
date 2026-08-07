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
from orgtree.ledger import USER, LedgerError                     # noqa: E402

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


def mkorg(name, *, sandboxed=False):
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
        o.hire(USER, None, "haiku", 2, "alice")
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


@t("☞ …and that at acceptEdits the WRITE is refused, on the .claude segment")
def _():
    p = supervisor.identity_prompt(store.load_org(PLAIN), "alice")
    assert "bypassPermissions" not in p or "gated ABOVE" in p, p[-800:]
    assert ".claude segment is gated ABOVE the permission system" in p, p[-800:]
    assert "refused outright" in p, p[-800:]


@t("a bypassPermissions agent is told it CAN write them")
def _():
    with store.DOC_LOCK:
        o = store.load_org(PLAIN)
        o.node("alice")["scope"]["permission_mode"] = "bypassPermissions"
        store.save_org(o)
    p = supervisor.identity_prompt(store.load_org(PLAIN), "alice")
    assert SKILLS in p
    assert "refused outright" not in p, p[-800:]
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


@t("☠ orgtree_retool does not expose permission_mode to agents")
def _():
    import json as _json
    from orgtree import mcptool
    src = open(mcptool.__file__, encoding="utf-8").read()
    # the tool schema is the whole surface an agent can reach; if the key ever
    # appears there, an agent can raise its own report to unguarded mode
    i = src.find('"orgtree_retool"')
    assert i > 0, "orgtree_retool not found — this check has gone stale"
    j = src.find('"orgtree_', i + 20)
    body = src[i:j if j > 0 else i + 4000]
    assert "permission_mode" not in body, body[:1200]
    del _json


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
        assert o.d["permission_mode"] == "acceptEdits"
        o.set_hire_defaults(permission_mode="bypassPermissions")
        assert o.d["permission_mode"] == "bypassPermissions"
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


# ============================================================== the report
if NOTES:
    print(f"\n{len(NOTES)} NOTE(S):")
    for m in NOTES:
        print(f"  · {m}")
print(f"\nALL {PASS} CHECKS PASS")
