"""Agent RENAME (main 57400b4) — the doc re-key and the orchestration around it.

A rename is the one operation that changes a node's IDENTITY, and identity is
the key everything else in the doc is filed under: parent/lineage pointers, the
mailbox, the delivery journal, the steer log, notices, open asks, credit
requests, audiences and their requests — plus, outside the doc, the agent's
scratch directory and the CLI's project directory (session resume is
project-scoped, so a missed move costs the agent its whole memory). Anything
this operation forgets does not raise; it strands a record under a key nobody
will look up again.

So the suite is written the way a re-key has to be checked: build a node that
is REFERENCED from every one of those places, rename it, and assert that no
reference to the old id survives anywhere it should have moved — and that every
reference the ruling says must NOT move (mail bodies, archives, the event log)
is still there, untouched.

    §1  authority — who may rename whom (never self)
    §2  the name itself — slugify, collisions, validate-all-then-mutate
    §3  the re-key, field by field, lineage generations included
    §4  what deliberately does NOT change, and what the caller is told
    §5  supervisor.rename_node — busy refusal, the two directory moves,
        in-memory turn state, and the rollback when the doc write fails

Hermetic: a throwaway ORGTREE_DATA and HOME, no port, no Docker, no CLI.

    python backend/tests/test_rename.py [-v]
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-rename-")
_HOME = os.path.join(_TMP, "home")
os.makedirs(_HOME, exist_ok=True)
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: net._default_address
# falls back to net.DEFAULT_HUB_ADDRESS — the operator's real hub — when this
# root has no defaults.json, and any rig that starts the net daemon then
# registers its fixture orgs there permanently. Measured twice (user report
# 2026-08-06; ~45 fixture orgs again on 2026-08-10). The discard port refuses
# instantly, so registration fails harmlessly into the backoff.
# Guarded over this whole directory by test_external_mail §1.
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

os.environ["USERPROFILE"] = _HOME
os.environ["HOME"] = _HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree import store, supervisor                            # noqa: E402
from orgtree.ledger import LedgerError, Org, USER                # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
VERBOSE = "-v" in sys.argv
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


def check(label, fn) -> None:
    """One check. A failure is RECORDED and the run continues — when a re-key
    misses a field it usually misses several, and stopping at the first one
    hides the shape of the bug."""
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def gap(label, why, fn) -> None:
    """A property that SHOULD hold and currently does not.

    The suite must stay green (it runs in the fast tier), but a finding that
    only lives in a report rots. So the expectation is inverted and stated: the
    check must FAIL today, and the day someone fixes the behaviour it passes
    unexpectedly and this entry turns red, which is the reminder to promote it
    to a real check. `why` is what to tell the implementer."""
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


def expect_error(fn, needle="") -> None:
    try:
        fn()
    except LedgerError as e:
        assert needle.lower() in str(e).lower(), f"wrong error: {e}"
        return
    raise AssertionError(f"expected LedgerError containing {needle!r}, got success")


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="test hire — do test things")
    s.update(over)
    return s


_n = [0]


def org3(persist: bool = False):
    """boss(20) → kid(5) → grandkid(1). `persist` also writes it to the
    throwaway data root, for the supervisor half."""
    _n[0] += 1
    name = f"zz rename {_n[0]}"
    org = Org.create(name, dirs=["E:/work"]) if not persist \
        else store.create_org(name)
    org.hire(USER, None, "opus", 20, "boss")
    org.hire("boss", "boss", "haiku", 5, "kid", **spec())
    org.hire("kid", "kid", "haiku", 1, "grandkid", **spec())
    if persist:
        store.save_org(org)
    return org


def wire_everything(org, nid: str = "kid") -> None:
    """Reference `nid` from every per-node structure a rename must re-key, so
    one rename can be checked against all of them at once."""
    org.d.setdefault("mail", {}).setdefault(nid, []).append(
        {"id": "m1", "from": USER, "body": f"hello {nid}", "at": "2026-01-01T00:00:00Z"})
    org.d.setdefault("delivering", {})[nid] = [{"tok": "t1", "at": "x", "mail": []}]
    org.d.setdefault("steered_log", {}).setdefault(nid, []).append(
        {"at": "x", "text": "mid-task delivery"})
    org.d.setdefault("turn_error_log", {}).setdefault(nid, []).append(
        {"at": "x", "error": "boom"})
    org.d.setdefault("notices", {}).setdefault(nid, []).append(
        {"at": "x", "text": "an earlier notice"})
    org.d.setdefault("audiences", []).append(
        {"grantee": nid, "grantor": "boss", "audience": "peer"})
    org.d.setdefault("audience_requests", []).append(
        {"id": "ar1", "from": nid, "target": "boss", "currently_at": nid,
         "status": "open"})
    org.d.setdefault("asks", []).append(
        {"id": "a1", "node": nid, "status": "open", "text": "may I?"})
    org.d.setdefault("credit_requests", []).append(
        {"id": "cr1", "node": nid, "status": "pending", "old": 5, "want": 9})


def refs_to(org, nid: str) -> list[str]:
    """Every place the doc still files something under `nid` as a KEY or a
    node-pointer. Historical bodies/senders are excluded on purpose — §4 checks
    those separately, because they are meant to survive."""
    hits: list[str] = []
    if nid in org.nodes:
        hits.append("nodes")
    for k, v in org.nodes.items():
        for f in ("parent", "predecessor", "successor"):
            if v.get(f) == nid:
                hits.append(f"{k}.{f}")
    for key in ("mail", "delivering", "steered_log", "turn_error_log", "notices"):
        if nid in (org.d.get(key) or {}):
            hits.append(key)
    for a in org.d.get("audiences", []):
        for f in ("grantee", "grantor"):
            if a.get(f) == nid:
                hits.append(f"audiences.{f}")
    for r in org.d.get("audience_requests", []):
        for f in ("from", "target", "currently_at"):
            if r.get(f) == nid:
                hits.append(f"audience_requests.{f}")
    for a in org.d.get("asks", []):
        if a.get("node") == nid:
            hits.append("asks.node")
    for r in org.d.get("credit_requests", []):
        if r.get("node") == nid:
            hits.append("credit_requests.node")
    return hits


# ===================================================================== §1
def sec_authority() -> None:
    print("\n§1  authority — downward only, never self")

    def _user():
        org = org3()
        r = org.rename(USER, "kid", "sprocket")
        assert r["node"] == "sprocket" and r["was"] == "kid", r
        assert "sprocket" in org.nodes and "kid" not in org.nodes
    check("the user may rename any node", _user)

    def _superior():
        org = org3()
        org.rename("boss", "kid", "sprocket")
        assert "sprocket" in org.nodes
    check("the immediate superior may rename its report", _superior)

    def _ancestor():
        org = org3()
        org.rename("boss", "grandkid", "sprocket")
        assert "sprocket" in org.nodes
    check("any ancestor may rename, not just the parent", _ancestor)

    def _self():
        org = org3()
        expect_error(lambda: org.rename("kid", "kid", "sprocket"), "authority")
        assert "kid" in org.nodes, "a refused rename must change nothing"
    check("a node may NOT rename itself (the ruling's 'never self')", _self)

    def _upward():
        org = org3()
        expect_error(lambda: org.rename("kid", "boss", "sprocket"), "authority")
        expect_error(lambda: org.rename("grandkid", "boss", "sprocket"), "authority")
    check("a subordinate may not rename its superior", _upward)

    def _sideways():
        org = org3()
        org.hire(USER, None, "haiku", 2, "outsider")
        expect_error(lambda: org.rename("outsider", "kid", "sprocket"), "authority")
    check("an unrelated top-level node has no authority either", _sideways)

    def _unknown_actor():
        org = org3()
        expect_error(lambda: org.rename("nobody", "kid", "x"), "unknown actor")
    check("an unknown actor is refused by name", _unknown_actor)

    def _unknown_target():
        org = org3()
        expect_error(lambda: org.rename(USER, "nobody", "x"), "nobody")
    check("an unknown target is refused", _unknown_target)


# ===================================================================== §2
def sec_name() -> None:
    print("\n§2  the new name — slugified, unique, all-or-nothing")

    def _slugified():
        org = org3()
        r = org.rename(USER, "kid", "  Data Wrangler!!  ")
        assert r["node"] == "data-wrangler", r
        assert "data-wrangler" in org.nodes
    check("the new name goes through slugify (case, spaces, punctuation)",
          _slugified)

    def _noop():
        org = org3()
        before = dict(org.nodes["kid"])
        r = org.rename(USER, "kid", "KID")
        assert r["node"] == "kid" and "already its name" in r["warnings"][0], r
        assert org.nodes["kid"] == before
        assert not [e for e in org.d["events"] if e["op"] == "rename"], \
            "a no-op rename must not write an event"
    check("renaming to the same slug is a no-op with a warning", _noop)

    def _empty():
        org = org3()
        expect_error(lambda: org.rename(USER, "kid", "!!!"), "mandatory")
        expect_error(lambda: org.rename(USER, "kid", "   "), "mandatory")
        assert "kid" in org.nodes
    check("a name with no letters or digits is refused", _empty)

    def _collision():
        org = org3()
        expect_error(lambda: org.rename(USER, "kid", "grandkid"), "already taken")
        assert "kid" in org.nodes and "grandkid" in org.nodes
        assert org.nodes["grandkid"]["parent"] == "kid", \
            "the refused rename must not have touched a pointer"
    check("a name already taken is refused", _collision)

    def _collision_lineage():
        # the subtle one the ruling calls out: `kid` renamed to `spare` is
        # legal on its own, but `kid@0` would collide with an existing
        # `spare@0`, so the WHOLE rename must be refused before anything moves
        org = org3()
        org.compact_split("kid", "sess-new")          # makes kid@0
        org.hire("boss", "boss", "haiku", 1, "spare", **spec())
        org.compact_split("spare", "sess-new-2")      # makes spare@0
        expect_error(lambda: org.rename(USER, "kid", "spare"), "already taken")
        assert "kid" in org.nodes and "kid@0" in org.nodes
        assert org.nodes["kid"]["predecessor"] == "kid@0"
        assert org.nodes["spare@0"]["successor"] == "spare", \
            "the other lineage must be untouched by the refusal"
    check("a collision on a LINEAGE target refuses the whole rename",
          _collision_lineage)

    def _validate_then_mutate():
        org = org3()
        org.compact_split("kid", "s2")
        wire_everything(org, "kid")
        snapshot = {k: dict(v) for k, v in org.nodes.items()}
        expect_error(lambda: org.rename(USER, "kid", "grandkid"), "already taken")
        assert {k: dict(v) for k, v in org.nodes.items()} == snapshot, \
            "validate-all-then-mutate: a refusal leaves the doc byte-identical"
        assert refs_to(org, "kid"), "…and every reference still points at kid"
    check("a refused rename mutates NOTHING (§4.7)", _validate_then_mutate)


# ===================================================================== §3
def sec_rekey() -> None:
    print("\n§3  the re-key — every structure filed under the id")

    def _nothing_left_behind():
        org = org3()
        wire_everything(org, "kid")
        org.rename(USER, "kid", "sprocket")
        left = refs_to(org, "kid")
        assert not left, f"still filed under the OLD id: {left}"
        moved = refs_to(org, "sprocket")
        for want in ("nodes", "grandkid.parent", "mail", "delivering",
                     "steered_log", "turn_error_log", "notices",
                     "audiences.grantee", "audience_requests.from",
                     "audience_requests.currently_at", "asks.node",
                     "credit_requests.node"):
            assert want in moved, f"{want} did not move to the new id ({moved})"
    check("no structure is left filed under the old id", _nothing_left_behind)

    def _payloads_survive():
        org = org3()
        wire_everything(org, "kid")
        org.rename(USER, "kid", "sprocket")
        assert org.d["mail"]["sprocket"][0]["id"] == "m1", "the mailbox moved whole"
        assert org.d["notices"]["sprocket"][0]["text"] == "an earlier notice"
        assert org.d["delivering"]["sprocket"][0]["tok"] == "t1", \
            "an in-flight delivery journal must survive the rename"
        assert org.d["turn_error_log"]["sprocket"][0]["error"] == "boom"
    check("the moved structures keep their contents", _payloads_survive)

    def _pointers():
        org = org3()
        org.rename(USER, "kid", "sprocket")
        assert org.nodes["grandkid"]["parent"] == "sprocket"
        assert org.nodes["sprocket"]["parent"] == "boss", \
            "the renamed node's own parent pointer is untouched"
        assert [c for c in org.children("sprocket")] == ["grandkid"]
        assert org.is_ancestor("boss", "sprocket")
    check("child pointers follow, and the tree still walks", _pointers)

    def _lineage():
        org = org3()
        org.compact_split("kid", "s2")          # kid@0
        org.compact_split("kid", "s3")          # kid@1
        r = org.rename(USER, "kid", "sprocket")
        assert set(r["renamed"]) == {"kid", "kid@0", "kid@1"}, r["renamed"]
        for gen in ("sprocket@0", "sprocket@1"):
            assert gen in org.nodes, f"{gen} missing — a generation was dropped"
        assert not [k for k in org.nodes if k.startswith("kid")]
        assert org.nodes["sprocket"]["predecessor"] == "sprocket@1"
        assert org.nodes["sprocket@1"]["successor"] == "sprocket"
        assert org.nodes["sprocket@1"]["predecessor"] == "sprocket@0"
        assert org.nodes["sprocket@0"]["successor"] == "sprocket"
        assert org.lineage_stack("sprocket") == ["sprocket@1", "sprocket@0"], (
            "the predecessor chain must still walk end to end — a half-moved "
            "pointer stops it at the first old id")
    check("every lineage generation re-keys with its node", _lineage)

    def _lineage_boxes():
        # a generation can hold its own mail/notices — those are keyed by the
        # GENERATION id, which is the case a `startswith(nid)` shortcut breaks
        org = org3()
        org.compact_split("kid", "s2")
        org.d.setdefault("notices", {}).setdefault("kid@0", []).append(
            {"at": "x", "text": "bearer notice"})
        org.d.setdefault("mail", {}).setdefault("kid@0", []).append(
            {"id": "m9", "from": USER, "body": "to the bearer", "at": "x"})
        org.rename(USER, "kid", "sprocket")
        assert "sprocket@0" in org.d["notices"] and "kid@0" not in org.d["notices"]
        assert org.d["mail"]["sprocket@0"][0]["id"] == "m9"
    check("per-generation mail and notices re-key too", _lineage_boxes)

    def _prefix_not_substring():
        # `kid2` and `kidney` must not be swept up by a rename of `kid` — the
        # stack is `nid` plus `nid@…`, never a bare prefix match
        org = org3()
        org.hire("boss", "boss", "haiku", 1, "kid2", **spec())
        org.hire("boss", "boss", "haiku", 1, "kidney", **spec())
        org.rename(USER, "kid", "sprocket")
        assert "kid2" in org.nodes and "kidney" in org.nodes, \
            "a node whose id merely STARTS WITH the renamed one was swept up"
    check("nodes that merely share a prefix are untouched", _prefix_not_substring)

    def _grandkid_rename():
        org = org3()
        wire_everything(org, "grandkid")
        org.rename("boss", "grandkid", "widget")
        assert not refs_to(org, "grandkid")
        assert org.nodes["widget"]["parent"] == "kid"
    check("a deep node renames the same way", _grandkid_rename)

    def _return_shape():
        org = org3()
        org.compact_split("kid", "s2")
        r = org.rename(USER, "kid", "sprocket")
        assert r["node"] == "sprocket" and r["was"] == "kid"
        assert r["renamed"] == {"kid": "sprocket", "kid@0": "sprocket@0"}
        assert isinstance(r["warnings"], list) and r["warnings"]
    check("the return names the new id, the old id and every re-key",
          _return_shape)


# ===================================================================== §4
def sec_history() -> None:
    print("\n§4  what deliberately does NOT change, and what the caller is told")

    def _history_kept():
        org = org3()
        org.post_mail(USER, "kid", "the old name is in this body: kid")
        before = len(org.d["events"])
        org.rename(USER, "kid", "sprocket")
        bodies = [m["body"] for m in org.d["mail"]["sprocket"]]
        assert any("kid" in b for b in bodies), \
            "mail BODIES are historical — the ruling says warn, don't rewrite"
        # `detail`, not `data` — the key the ledger actually writes (_log).
        # Asserted explicitly because a typo here passes vacuously: str(None)
        # never contains "kid", and the check would pass while measuring
        # nothing.
        old_events = [e for e in org.d["events"][:before]
                      if "kid" in str(e.get("detail"))]
        assert old_events, "the event log must still read as it happened"
        assert any(e.get("op") == "hire" for e in old_events), \
            "the hire that created 'kid' still names it"
    check("historical mail bodies and the event log keep the old name",
          _history_kept)

    def _warning():
        org = org3()
        r = org.rename(USER, "kid", "sprocket")
        w = " ".join(r["warnings"]).lower()
        assert "kid" in w and "sprocket" in w, w
        assert "bounce" in w or "unknown recipient" in w, \
            "the caller must be told that mail to the old name will bounce"
    check("the warning names both ids and the consequence", _warning)

    def _event_logged():
        org = org3()
        org.rename("boss", "kid", "sprocket")
        ev = [e for e in org.d["events"] if e["op"] == "rename"]
        assert len(ev) == 1, ev
        assert ev[0]["detail"]["node"] == "kid", ev[0]
        assert ev[0]["detail"]["new"] == "sprocket", ev[0]
        assert ev[0]["actor"] == "boss"
        assert ev[0]["warnings"], "the log carries the bounce warning too"
    check("the rename is logged with actor, old id and new id", _event_logged)

    def _notice_to_the_renamed():
        org = org3()
        org.rename(USER, "kid", "sprocket")
        notes = org.d.get("notices", {}).get("sprocket") or []
        assert notes, "the renamed agent is never told (notices empty)"
        text = notes[-1]["text"]
        assert "kid" in text and "sprocket" in text, text
        assert "sprocket" in text.split("refer to yourself as")[-1], \
            "the notice must tell it what to sign as now"
        assert not (org.d.get("notices", {}).get("kid")), \
            "the notice must not be filed under the id that no longer exists"
    check("the renamed agent is notified, under its NEW key", _notice_to_the_renamed)

    def _notice_names_the_actor():
        org = org3()
        org.rename("boss", "kid", "sprocket")
        assert "boss" in org.d["notices"]["sprocket"][-1]["text"]
        org2 = org3()
        org2.rename(USER, "kid", "sprocket")
        assert "the user" in org2.d["notices"]["sprocket"][-1]["text"]
    check("the notice says who did it (the user, or the ancestor)",
          _notice_names_the_actor)

    def _old_name_bounces():
        # the warning PROMISES this ("such mail will bounce with 'unknown
        # recipient'") — a promise nothing else in the codebase checks
        org = org3()
        org.rename(USER, "kid", "sprocket")
        expect_error(lambda: org.post_mail("boss", "kid", "still here?"))
        r = org.post_mail("boss", "sprocket", "found you")
        assert r["delivered"] == "sprocket", r
    check("mail to the old name bounces; the new name receives",
          _old_name_bounces)

    def _superior_not_told():
        # OBSERVATION, asserted so a change is deliberate: only the renamed
        # node is notified. Its superior — the one whose next message will
        # bounce — is not, and neither are its own reports.
        org = org3()
        org.rename(USER, "kid", "sprocket")
        assert not org.d.get("notices", {}).get("boss"), (
            "if the superior is now notified, this suite's model of who "
            "learns about a rename is out of date — update it deliberately")
        assert not org.d.get("notices", {}).get("grandkid")
    check("only the renamed agent is notified (superior and reports are not)",
          _superior_not_told)

    def _mcp_actor_pinned():
        # a drift guard, not a behaviour test: the agent-facing entry point
        # must pin the ACTOR to the calling node. If that ever becomes
        # caller-supplied, `rename`'s authority check is reading a value the
        # caller chose, and any agent can rename its own superior.
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "orgtree", "api.py"),
                   encoding="utf-8").read()
        i = src.find('if body.tool == "orgtree_rename"')
        assert i > 0, "the MCP rename branch moved — re-read this guard"
        window = src[i:i + 400]
        assert "actor=body.node" in window, (
            "the agent gateway no longer pins the rename actor to the calling "
            f"node: {window[:200]!r}")
    check("the agent gateway pins the rename actor to the caller (drift guard)",
          _mcp_actor_pinned)

    def _title_follows():
        # promoted from gap() 2026-08-05: rename now writes the display title
        # (raw new name, like hire does) onto every renamed node
        org = org3()
        org.compact_split("kid", "s2")
        org.rename(USER, "kid", "  Data Wrangler!!  ")
        assert org.nodes["data-wrangler"]["title"] == "Data Wrangler!!", \
            org.nodes["data-wrangler"]["title"]
        assert org.nodes["data-wrangler@0"]["title"] == "Data Wrangler!!", \
            "generations carry the same display title as their base"
    check("the display title follows the rename (raw name, like hire)",
          _title_follows)


# ===================================================================== §5
def sec_supervisor() -> None:
    print("\n§5  supervisor.rename_node — the two directory moves and the state")

    def scratch_of(slug, nid):
        return os.path.join(store.scratch_root(slug), nid)

    def project_of(nid_dir):
        return os.path.join(_HOME, ".claude", "projects",
                            supervisor._cli_project_dir(nid_dir))

    def setup(persist=True):
        org = org3(persist=True)
        slug = org.d["slug"]
        d = supervisor.scratch_dir(slug, "kid")       # creates it
        with open(os.path.join(d, "notes.md"), "w", encoding="utf-8") as fh:
            fh.write("agent memory")
        p = project_of(d)
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "sess.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        return org, slug, d, p

    def _encoding():
        enc = supervisor._cli_project_dir(r"C:\Users\x\orgtree\scratch\o\kid")
        assert enc == "C--Users-x-orgtree-scratch-o-kid", enc
        assert supervisor._cli_project_dir("/home/agent/orgtree/scratch/o/kid") \
            == "-home-agent-orgtree-scratch-o-kid"
        assert "@" not in supervisor._cli_project_dir("a@0"), \
            "every non-alphanumeric becomes '-', generations included"
    check("_cli_project_dir matches the CLI's own encoding", _encoding)

    def _moves():
        org, slug, d, p = setup()
        r = supervisor.rename_node(slug, "kid", "Sprocket")
        assert r["node"] == "sprocket", r
        nd = scratch_of(slug, "sprocket")
        assert os.path.isdir(nd) and not os.path.exists(d), \
            "the scratch dir must move with the identity"
        assert open(os.path.join(nd, "notes.md"), encoding="utf-8").read() \
            == "agent memory", "…with its contents"
        np = project_of(nd)
        assert os.path.isdir(np) and not os.path.exists(p), (
            "the CLI project dir must move too — resume is project-scoped, so "
            "leaving it behind costs the agent its conversation")
        assert os.path.isfile(os.path.join(np, "sess.jsonl"))
        assert "sprocket" in store.load_org(slug).nodes, "and the doc is SAVED"
    check("scratch dir and CLI project dir both move, and the doc is saved",
          _moves)

    def _state_rekeys():
        org, slug, d, p = setup()
        org.compact_split("kid", "s2")
        store.save_org(org)
        supervisor.state(slug, "kid")["turns_run"] = 7
        supervisor.state(slug, "kid@0")["turns_run"] = 3
        supervisor.rename_node(slug, "kid", "sprocket")
        assert supervisor.state(slug, "sprocket")["turns_run"] == 7, \
            "in-memory turn state must follow the identity"
        assert supervisor.state(slug, "sprocket@0")["turns_run"] == 3, \
            "…for every generation"
        assert (slug, "kid") not in supervisor._state
    check("in-memory turn state re-keys, generations included", _state_rekeys)

    def _busy_refused():
        org, slug, d, p = setup()
        supervisor.state(slug, "kid")["busy"] = True
        try:
            expect_error(lambda: supervisor.rename_node(slug, "kid", "sprocket"),
                         "mid-turn")
            assert "kid" in store.load_org(slug).nodes, "the doc was not saved"
            assert os.path.isdir(d) and not os.path.exists(
                scratch_of(slug, "sprocket")), "and nothing moved on disk"
        finally:
            supervisor.state(slug, "kid")["busy"] = False
    check("a busy node refuses the rename, changing nothing", _busy_refused)

    def _queued_refused():
        org, slug, d, p = setup()
        supervisor.state(slug, "kid")["queue"].append("a queued message")
        try:
            expect_error(lambda: supervisor.rename_node(slug, "kid", "sprocket"),
                         "mid-turn")
            assert "kid" in store.load_org(slug).nodes
        finally:
            supervisor.state(slug, "kid")["queue"].clear()
    check("a queued message refuses it too (the turn has not run yet)",
          _queued_refused)

    def _busy_generation_refused():
        org, slug, d, p = setup()
        org.compact_split("kid", "s2")
        store.save_org(org)
        supervisor.state(slug, "kid@0")["busy"] = True
        try:
            expect_error(lambda: supervisor.rename_node(slug, "kid", "sprocket"),
                         "mid-turn")
            assert "kid" in store.load_org(slug).nodes
        finally:
            supervisor.state(slug, "kid@0")["busy"] = False
    check("a busy LINEAGE generation refuses the rename as well",
          _busy_generation_refused)

    def _rollback():
        org, slug, d, p = setup()
        real = store.save_org

        def boom(o):
            raise OSError("disk gone")
        store.save_org = boom
        try:
            try:
                supervisor.rename_node(slug, "kid", "sprocket")
            except OSError:
                pass
            else:
                raise AssertionError("the failing save was swallowed")
        finally:
            store.save_org = real
        assert os.path.isdir(d) and os.path.isfile(os.path.join(d, "notes.md")), \
            "the scratch dir must be rolled BACK when the doc write fails"
        assert not os.path.exists(scratch_of(slug, "sprocket"))
        assert os.path.isdir(p) and not os.path.exists(
            project_of(scratch_of(slug, "sprocket"))), \
            "…and so must the project dir"
        assert "kid" in store.load_org(slug).nodes, "the doc on disk is unchanged"
    check("a failed doc write rolls BOTH directory moves back", _rollback)

    def _unknown_node():
        org, slug, d, p = setup()
        expect_error(lambda: supervisor.rename_node(slug, "nobody", "x"), "nobody")
    check("an unknown node is refused before anything moves", _unknown_node)

    def _same_name_noop():
        org, slug, d, p = setup()
        r = supervisor.rename_node(slug, "kid", "KID")
        assert r["node"] == "kid" and "already its name" in r["warnings"][0]
        assert os.path.isdir(d), "a no-op must not disturb the directories"
    check("renaming to the same slug does nothing, safely", _same_name_noop)

    def _occupied_scratch():
        # RE-promoted 2026-08-05 (second contract change, user bug): an
        # occupied destination is an ORPHAN BY CONSTRUCTION — the ledger's
        # taken-name check has already passed, so no existing node owns the
        # name. The rename now MOVES THE SQUATTER ASIDE (*.orphan-<ts>),
        # proceeds, and says so in the warnings; the squatter's files
        # survive inside the moved-aside dir, never adopted by the agent.
        org, slug, d, p = setup()
        squat = scratch_of(slug, "sprocket")
        os.makedirs(squat, exist_ok=True)
        with open(os.path.join(squat, "stranger.txt"), "w", encoding="utf-8") as fh:
            fh.write("someone else's files")
        r = supervisor.rename_node(slug, "kid", "sprocket")
        assert any("moved aside" in w for w in r.get("warnings", [])), r
        assert "sprocket" in store.load_org(slug).nodes
        nd = scratch_of(slug, "sprocket")
        assert open(os.path.join(nd, "notes.md"), encoding="utf-8").read() \
            == "agent memory", "the agent's OWN files landed at the new name"
        assert not os.path.exists(os.path.join(nd, "stranger.txt")), \
            "the squatter's files must NOT be adopted"
        aside = [x for x in os.listdir(os.path.dirname(nd))
                 if x.startswith("sprocket.orphan-")]
        assert aside, "the squatter dir was moved aside, not deleted"
        assert open(os.path.join(os.path.dirname(nd), aside[0],
                                 "stranger.txt"), encoding="utf-8").read() \
            == "someone else's files", "…with its contents intact"
    check("an occupied destination is moved aside and the rename proceeds",
          _occupied_scratch)

    def _occupied_project():
        # same for the CLI project dir alone — the agent must not resume a
        # dead stranger's conversations, so the orphan moves aside and a
        # FRESH project dir takes the name
        org, slug, d, p = setup()
        squat = project_of(scratch_of(slug, "sprocket"))
        os.makedirs(squat, exist_ok=True)
        with open(os.path.join(squat, "ghost.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        r = supervisor.rename_node(slug, "sprocket" if False else "kid",
                                   "sprocket")
        assert any("moved aside" in w for w in r.get("warnings", [])), r
        np = project_of(scratch_of(slug, "sprocket"))
        assert os.path.isfile(os.path.join(np, "sess.jsonl")), \
            "the agent's own sessions moved in"
        assert not os.path.exists(os.path.join(np, "ghost.jsonl")), \
            "the dead stranger's sessions must not be resumable at this name"
    check("an occupied CLI project dir is moved aside too",
          _occupied_project)

    def _generation_direct():
        # promoted from gap() 2026-08-05: a GENERATION id (`base@gen`) is now
        # refused as a rename target — the family renames from its base
        org = org3()
        org.compact_split("kid", "s2")
        expect_error(lambda: org.rename(USER, "kid@0", "spare"),
                     "lineage generation")
        assert "kid@0" in org.nodes and \
            org.nodes["kid"]["predecessor"] == "kid@0", \
            "the refusal must leave the family intact"
    check("a lineage generation cannot be renamed out of its family",
          _generation_direct)


    def _reclaim_a_deleted_name():
        # USER BUG 2026-08-05: "deleting an agent and then attempting to
        # rename another to reclaim the name does not work, im still told that
        # the name exists". Reproduced end to end through the REAL delete path.
        org, slug, d, p = setup()
        org2 = store.load_org(slug)
        org2.hire(USER, None, "opus", 10, "spare")
        store.save_org(org2)
        gone = supervisor.scratch_dir(slug, "kid")      # kid has lived
        proj = project_of(gone)
        os.makedirs(proj, exist_ok=True)
        o = store.load_org(slug)
        res = o.delete(USER, "kid")
        store.save_org(o)
        supervisor.forget(slug, res.get("deleted") or ["kid"])   # what api.py calls
        assert "kid" not in store.load_org(slug).nodes, "precondition: deleted"
        assert not os.path.exists(gone), "forget() removed the scratch dir"
        try:
            supervisor.rename_node(slug, "spare", "kid")
        except LedgerError as e:
            # the refusal IS the finding — restate it as the failed property
            raise AssertionError(
                f"the name could not be reclaimed after the agent was "
                f"deleted: {e}. The CLI project dir {proj} survives the "
                f"delete on purpose (forget's docstring: transcripts are "
                f"deliberately left alone), and rename_node refuses any "
                f"occupied destination directory") from None
        assert "kid" in store.load_org(slug).nodes
    # promoted from gap() 2026-08-05: the occupied-destination handling is now
    # move-aside (orphan by construction — the taken-name check ran first),
    # so the user's delete-then-reclaim flow works and the delete's preserved
    # transcripts survive under the .orphan name
    check("a deleted agent's name can be reclaimed by renaming another",
          _reclaim_a_deleted_name)

    def _forget_misses_an_on_disk_scratch():
        # found while reproducing the above: forget() resolves the scratch
        # root WITHOUT the on-disk branch that scratch_dir() has
        org, slug, d, p = setup()
        from orgtree import disk as dsk, sandbox as sbx
        o = store.load_org(slug)
        o.d["disk"] = {"size_mb": 4096, "migrated_at": "2026-01-01"}
        store.save_org(o)
        real_sub, real_flag = dsk.windows_sub, dict(sbx._disk_flag)
        root = os.path.join(_TMP, "diskview")
        dsk.windows_sub = lambda slug_, sub: os.path.join(root, slug_, sub)
        sbx._disk_flag.clear()
        try:
            live = supervisor.scratch_dir(slug, "kid")
            with open(os.path.join(live, "work.txt"), "w", encoding="utf-8") as fh:
                fh.write("the agent's files")
            o = store.load_org(slug)
            res = o.delete(USER, "kid")
            store.save_org(o)
            supervisor.forget(slug, res.get("deleted") or ["kid"])
            assert not os.path.isdir(live), (
                f"a disk-migrated org's scratch dir survived the delete: "
                f"{live} still holds {os.listdir(live)}. forget() removes "
                f"store.scratch_root(slug)/<nid>, but scratch_dir() puts a "
                f"disk-migrated org's scratch on the DISK — so the rmtree "
                f"targets a path that does not exist and ignore_errors=True "
                f"hides the miss")
        finally:
            dsk.windows_sub = real_sub
            sbx._disk_flag.clear()
            sbx._disk_flag.update(real_flag)
    # promoted from gap() 2026-08-05: forget() now branches on the
    # disk-migrated case exactly like scratch_dir() — the rmtree aims at the
    # org disk's scratch, not a phantom under store.scratch_root
    check("deleting a node removes its scratch dir on a DISK-MIGRATED org too",
          _forget_misses_an_on_disk_scratch)


def main() -> int:
    print("orgtree · agent rename (ledger.rename + supervisor.rename_node)")
    sec_authority()
    sec_name()
    sec_rekey()
    sec_history()
    sec_supervisor()

    print()
    if GAPS:
        print("known gaps (asserted as failing on purpose — promote when fixed):")
        for label, why, detail in GAPS:
            print(f"  ⚑ {label}\n      why: {why}\n      saw: {detail}")
        print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"rename: {PASS} passed · {len(FAIL)} FAILED · {len(GAPS)} gaps")
        return 1
    print(f"rename: all {PASS} checks passed · {len(GAPS)} known gaps")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
