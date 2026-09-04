"""orgtree_hire has TWO modes, and the advertised schema must admit both.

    python backend/tests/test_hire_schema_contract.py

THE DEFECT THIS PINS (Astra audit 2026-09-04, orgtree-audit.md §11,
protocol-probes.json `hire_superior_schema_conflict`): the exported
`orgtree_hire` card listed `add_dirs`, `tools` and `org_visibility` under
`required`, while the API's `hire_type='superior'` branch refuses any
non-null value for exactly those three (the seat inherits the TARGET's
scope). A client that enforces the schema before calling — the Claude CLI
validates MCP arguments against `inputSchema` — could therefore never
construct a superior insertion at all: every schema-valid call was an
API-refused call, and every API-valid call was a schema-invalid one.

THE CONTRACT NOW (enforced at the SERVER, per mode — the schema is only the
honest surface of it):

  subordinate (default)  add_dirs, tools, org_visibility are REQUIRED —
                         an agent hire has no defaults (ledger §4.2), and
                         the API door says so naming the mode.
  superior               the same three, plus permission_mode, MUST BE
                         OMITTED — present is refused, never overwritten.

So the three are schema-OPTIONAL and server-REQUIRED-BY-MODE. This suite is
the reason "just drop them from `required`" is not the whole fix: §2 proves
the ordinary form still refuses a missing field, and that a refusal in
either mode leaves no seat, no credit movement, no mailbox and no queued
kickoff behind.

    §1  the schema contract — the served card admits the superior form
    §2  the local API — positive controls for both forms, then the
        negative controls, each checked for residue

No jsonschema package is installed here, so §1 carries a small validator
for the parts a client enforces (required / type / enum) and proves it can
fail before it is trusted to pass.
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_DATA = tempfile.mkdtemp(prefix="orgtree-hireschema-")
os.environ["ORGTREE_DATA"] = _DATA
os.makedirs(_DATA, exist_ok=True)
# never let a fixture org reach the operator's real mail hub (see test_ledger)
with open(os.path.join(_DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["ORGTREE_PORT"] = "7497"
os.environ["ORGTREE_STEER_HOOK"] = "0"

from fastapi.testclient import TestClient                      # noqa: E402
from orgtree import api, mcptool, sandbox, store, supervisor   # noqa: E402
from orgtree.ledger import USER                                # noqa: E402

# store.DATA_ROOT binds at import time — prove this process is on the
# throwaway root and can NOT have resolved to the operator's live data
assert os.path.realpath(str(store.DATA_ROOT)) == os.path.realpath(_DATA), \
    f"store bound to {store.DATA_ROOT!r}, not the throwaway {_DATA!r}"

supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
supervisor.storage_check = lambda slug: None
sandbox.warm = lambda org: None

PASS = 0
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True,
             "mcp": []}
NARROW_TOOLS = {"bash": False, "web": False, "edit": True, "subagents": False,
                "mcp": []}


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


def eq(got, want, what=""):
    assert got == want, f"{what}: {got!r} != {want!r}"


def true(cond, msg=""):
    assert cond, msg or "expected true"


# =====================================================================  §1
print("\n§1  the schema contract")

_TYPES = {"string": str, "integer": int, "number": (int, float),
          "boolean": bool, "array": list, "object": dict}


def violations(schema, args):
    """What a schema-enforcing client would refuse: a missing `required`
    key, a property of the wrong JSON type, or a value outside its `enum`.
    Recurses into object properties and array items. Nothing else — this
    is the floor every MCP client enforces, not a JSON-Schema engine."""
    out = []
    for req in schema.get("required", []):
        if req not in args:
            out.append(f"missing required {req!r}")
    props = schema.get("properties", {})
    for k, v in args.items():
        sub = props.get(k)
        if sub is None:
            continue
        ty = sub.get("type")
        if ty in _TYPES and not isinstance(v, _TYPES[ty]):
            out.append(f"{k!r} is not {ty}")
            continue
        if ty == "integer" and isinstance(v, bool):
            out.append(f"{k!r} is not integer")
        if "enum" in sub and v not in sub["enum"]:
            out.append(f"{k!r} not in enum")
        if ty == "object" and isinstance(v, dict):
            out.extend(f"{k}.{x}" for x in violations(sub, v))
        if ty == "array" and isinstance(v, list) and "items" in sub:
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out.extend(f"{k}[{i}].{x}"
                               for x in violations(sub["items"], item))
    return out


def card(catalogue, name):
    return next(c for c in catalogue if c["name"] == name)


HIRE = card(mcptool.TOOLS, "orgtree_hire")["inputSchema"]

ORDINARY = {"name": "worker", "tier": "haiku", "grant": 0,
            "charter": "an ordinary report", "add_dirs": [],
            "tools": dict(ALL_TOOLS), "org_visibility": "team",
            "kickoff": "start"}
SUPERIOR = {"name": "successor", "tier": "haiku", "grant": 0,
            "charter": "the seat above", "hire_type": "superior",
            "target": "kid", "kickoff": "you hold the seat now"}


@t("positive control: the validator passes a full ordinary hire…")
def _():
    eq(violations(HIRE, ORDINARY), [])


@t("…and CAN fail: a missing charter, a wrong type and a bad enum are caught")
def _():
    v = violations(HIRE, {k: x for k, x in ORDINARY.items() if k != "charter"})
    true(any("charter" in x for x in v), v)
    v = violations(HIRE, {**ORDINARY, "grant": "5"})
    true(any("grant" in x for x in v), v)
    v = violations(HIRE, {**ORDINARY, "hire_type": "sideways"})
    true(any("hire_type" in x for x in v), v)
    v = violations(HIRE, {**ORDINARY, "tools": {"bash": True}})
    true(any("tools." in x for x in v), v)


@t("THE CONTRACT: a superior insertion that omits the inherited scope is "
   "schema-valid on the module card")
def _():
    eq(violations(HIRE, SUPERIOR), [],
       "the served schema must admit the only form the API accepts")


@t("…and on the catalogue the CLI is actually served (available_tools)")
def _():
    served = card(mcptool.available_tools(), "orgtree_hire")["inputSchema"]
    eq(violations(served, SUPERIOR), [])
    eq(violations(served, ORDINARY), [])


@t("the fields every mode needs are still required by the schema")
def _():
    for k in ("name", "tier", "grant", "charter"):
        true(k in HIRE["required"], f"{k} must stay required")
    for k in ("add_dirs", "tools", "org_visibility", "permission_mode"):
        true(k in HIRE["properties"], f"{k} must stay an advertised property")


@t("the card says which mode needs the scope trio and which refuses it")
def _():
    # wording drift guard — the BEHAVIOUR is §2; this keeps the card from
    # promising something §2 no longer enforces, or the reverse
    for k in ("add_dirs", "tools", "org_visibility"):
        d = HIRE["properties"][k]["description"].lower()
        true("subordinate" in d and "superior" in d,
             f"{k}'s description must state both modes: {d[:120]}")
    ht = HIRE["properties"]["hire_type"]["description"]
    true("OMIT" in ht and "add_dirs" in ht and "permission_mode" in ht, ht)
    desc = card(mcptool.TOOLS, "orgtree_hire")["description"]
    true("superior" in desc.lower(), "the tool description must mention the "
                                     "second mode")


# =====================================================================  §2
print("\n§2  the local API — both forms, then the residue-free refusals")

ORG = store.create_org("zz hire schema contract")
SLUG = ORG.d["slug"]
HOST_DIR = tempfile.mkdtemp(prefix="orgtree-hireschema-dir-")
ORG.hire(USER, None, "opus", 80, "boss",
         add_dirs=[{"path": HOST_DIR, "mode": "rw"}])
ORG.hire("boss", "boss", "opus", 40, "mid", add_dirs=[{"path": HOST_DIR, "mode": "rw"}],
         tools=dict(ALL_TOOLS), org_visibility="full", charter="the middle")
# kid holds a DISTINCT, narrower scope than mid on all four sets, so an
# inserted superior taking "the target's scope" is distinguishable from one
# taking the caller's
ORG.hire("mid", "mid", "opus", 12, "kid", add_dirs=[{"path": HOST_DIR, "mode": "ro"}],
         tools=dict(NARROW_TOOLS), org_visibility="self", charter="the kid")
ORG.set_scope(USER, "mid", permission_mode="acceptEdits")
ORG.set_scope(USER, "kid", permission_mode="plan")
ORG.hire("kid", "kid", "haiku", 0, "grandkid", add_dirs=[],
         tools=dict(NARROW_TOOLS), org_visibility="self", charter="the grandkid")
store.save_org(ORG)

DRIVEN: list[str] = []
supervisor.send_message = (                       # never spawn a real turn
    lambda slug, nid, text, **kw: (DRIVEN.append(nid), {})[1])
CLIENT = TestClient(api.app)


def post(node, args):
    return CLIENT.post("/api/agent", json={"org": SLUG, "node": node,
                                           "tool": "orgtree_hire",
                                           "args": args})


def call_ok(node, args):
    r = post(node, args)
    assert r.status_code == 200, f"→ {r.status_code}: {r.text[:300]}"
    return r.json()


def fingerprint(org):
    """Everything a REFUSED hire must leave byte-identical: seats, parents,
    grants, scope, free credits at every node, and every mailbox."""
    return {
        "nodes": {k: (v["parent"], v["grant"], v["state"], v["model"],
                      repr(v["scope"]))
                  for k, v in org.nodes.items()},
        "free": {k: org.free(k) for k in org.nodes},
        "mail": {k: len(v) for k, v in (org.d.get("mail") or {}).items()},
    }


def sound(org, where):
    for k, n in org.nodes.items():
        derived = n["grant"] - sum(org.seat_cost(c) + org.nodes[c]["grant"]
                                   for c in org.children(k))
        eq(org.free(k), derived, f"{where}: free({k}) not derivable")
        if n["state"] == "live":
            true(org.free(k) >= 0, f"{where}: {k} overdrafted")
            p = n["parent"]
            if p is not None:
                ps, cs = org.nodes[p]["scope"], n["scope"]
                for tk in ("bash", "web", "edit", "subagents"):
                    true(not cs["tools"][tk] or ps["tools"][tk],
                         f"{where}: {k} holds tool {tk} its parent lacks")


def scope_of(org, nid):
    s = org.nodes[nid]["scope"]
    return (sorted((d["path"], d["mode"]) for d in s["add_dirs"]),
            {k: s["tools"][k] for k in ("bash", "web", "edit", "subagents")},
            s.get("org_visibility"), s.get("permission_mode"))


def refused(node, args, needles=()):
    """A refusal is a non-200, non-500 answer that names what was wrong AND
    leaves the org — seats, credits, mailboxes — and the drive list exactly
    as they were. Every negative control carries a `kickoff` so 'no queued
    kickoff' is a live assertion, not a vacuous one (§2's positive controls
    prove DRIVEN records one when a hire succeeds)."""
    assert "kickoff" in args, "negative controls must carry a kickoff"
    before = fingerprint(store.load_org(SLUG))
    driven = list(DRIVEN)
    r = post(node, args)
    assert r.status_code not in (200, 500), \
        f"expected a refusal, got {r.status_code}: {r.text[:300]}"
    for n in needles:
        assert n.lower() in r.text.lower(), \
            f"refusal must say {n!r}: {r.text[:300]}"
    after = store.load_org(SLUG)
    true(args["name"] not in after.nodes, f"{args['name']} was seated anyway")
    eq(fingerprint(after), before, "a refused hire moved something")
    eq(DRIVEN, driven, "a refused hire queued a kickoff")
    return r.text


@t("positive control (superior form): the schema-valid call seats the "
   "agent ABOVE the target with the TARGET's scope, and its kickoff runs")
def _():
    DRIVEN.clear()
    org = store.load_org(SLUG)
    kid_scope, mid_scope = scope_of(org, "kid"), scope_of(org, "mid")
    true(kid_scope != mid_scope, "the fixture's two scopes must differ")
    free_before = {k: org.free(k) for k in org.nodes}
    r = call_ok("mid", dict(SUPERIOR))
    eq(r["inserted_above"], "kid")
    eq(r["reports_to"], "mid")
    org = store.load_org(SLUG)
    eq(org.nodes["successor"]["parent"], "mid")
    eq(org.nodes["kid"]["parent"], "successor")
    eq(org.nodes["grandkid"]["parent"], "kid", "the target kept its own team")
    eq(scope_of(org, "successor"), kid_scope,
       "the inserted seat holds exactly the target's scope")
    true("permission_mode" not in (r.get("applied") or []), r.get("applied"))
    eq(DRIVEN, ["successor"], "the kickoff started it, and only it")
    # the hire cost the target's free (seat 1 + grant 0), the insertion is
    # budget-neutral, and nothing above the target paid
    eq(org.free("kid"), free_before["kid"] - 1)
    eq(org.free("mid"), free_before["mid"])
    eq(org.free("boss"), free_before["boss"])
    sound(org, "after the superior insertion")


@t("positive control (ordinary form): explicit scope seats a report under "
   "the target with the scope it asked for, and its kickoff runs")
def _():
    DRIVEN.clear()
    ask = {**ORDINARY, "target": "kid", "tools": dict(NARROW_TOOLS),
           "org_visibility": "self",
           "add_dirs": [{"path": HOST_DIR, "mode": "ro"}]}
    r = call_ok("mid", ask)
    org = store.load_org(SLUG)
    eq(org.nodes["worker"]["parent"], "kid")
    true(r.get("started"), r)
    eq(DRIVEN, ["worker"])
    dirs, tools, vis, _ = scope_of(org, "worker")
    eq(tools, {k: NARROW_TOOLS[k] for k in tools}, "the scope it asked for")
    eq(vis, "self")
    eq(dirs, [(os.path.normpath(HOST_DIR), "ro")])
    sound(org, "after the ordinary hire")


@t("negative control: the permission ceiling — an ordinary hire asking for "
   "more than its parent holds is refused, not clamped")
def _():
    # kid holds NARROW tools, ro on HOST_DIR and self visibility
    refused("mid", {**ORDINARY, "name": "ghost", "target": "kid",
                    "tools": dict(ALL_TOOLS), "org_visibility": "self"},
            needles=("cannot grant",))
    refused("mid", {**ORDINARY, "name": "ghost", "target": "kid",
                    "tools": dict(NARROW_TOOLS), "org_visibility": "self",
                    "add_dirs": [{"path": HOST_DIR, "mode": "rw"}]})
    refused("mid", {**ORDINARY, "name": "ghost", "target": "kid",
                    "tools": dict(NARROW_TOOLS), "org_visibility": "full"})


@t("negative control: an ordinary hire missing add_dirs / tools / "
   "org_visibility is refused, naming the field and the mode")
def _():
    for drop in ("add_dirs", "tools", "org_visibility"):
        args = {k: v for k, v in ORDINARY.items() if k != drop}
        args["name"] = "ghost"
        refused("mid", args, needles=(drop, "subordinate", "superior"))
    # the explicit mode spelling changes nothing
    args = {k: v for k, v in ORDINARY.items() if k != "tools"}
    refused("mid", {**args, "name": "ghost", "hire_type": "subordinate"},
            needles=("tools", "subordinate"))
    # a PARTIAL tools dict is still the ledger's refusal (all five switches)
    refused("mid", {**ORDINARY, "name": "ghost", "tools": {"bash": True}},
            needles=("tools",))


@t("negative control: a superior hire carrying its own scope is refused, "
   "naming every conflicting field — never silently replaced")
def _():
    for field, val in (("add_dirs", []), ("tools", dict(ALL_TOOLS)),
                       ("org_visibility", "team"),
                       ("permission_mode", "plan")):
        refused("mid", {**SUPERIOR, "name": "ghost", field: val},
                needles=(field, "omit"))
    # all four at once: the refusal names all four, and even a scope that
    # HAPPENS to equal the target's is refused (it is not the caller's to say)
    org = store.load_org(SLUG)
    ks = org.nodes["kid"]["scope"]
    txt = refused("mid", {**SUPERIOR, "name": "ghost",
                          "add_dirs": [dict(d) for d in ks["add_dirs"]],
                          "tools": dict(ks["tools"]),
                          "org_visibility": ks["org_visibility"],
                          "permission_mode": ks["permission_mode"]})
    for f in ("add_dirs", "tools", "org_visibility", "permission_mode"):
        true(f in txt, f"the refusal must name {f}: {txt[:200]}")


@t("negative control: unauthorized placement is refused in both modes — "
   "outside the subtree, above a top-level seat, beyond the budget")
def _():
    # (a) a destination outside the caller's subtree
    refused("kid", {**ORDINARY, "name": "ghost", "target": "mid"},
            needles=("outside your subtree",))
    refused("kid", {**SUPERIOR, "name": "ghost", "target": "mid"},
            needles=("outside your subtree",))
    # (b) only the user seats anyone at top level (§7.4)
    refused("boss", {**SUPERIOR, "name": "ghost", "target": "boss"},
            needles=("top-level",))
    # (c) the budget: the target pays seat + grant from its own free, and a
    # grant the whole chain cannot cover is refused — in superior mode too,
    # where the insertion itself is budget-neutral but the hire is not
    org = store.load_org(SLUG)
    too_much = int(org.free("boss") + org.free("mid") + org.free("kid")) + 100
    refused("mid", {**SUPERIOR, "name": "ghost", "grant": too_much},
            needles=("credit",))
    refused("mid", {**ORDINARY, "name": "ghost", "grant": too_much},
            needles=("credit",))
    # (d) 0f34808's whole-credit rule survives in both modes
    refused("mid", {**SUPERIOR, "name": "ghost", "grant": 0.5},
            needles=("integer",))
    refused("mid", {**ORDINARY, "name": "ghost", "grant": 0.5},
            needles=("integer",))


@t("negative control: a refusal AFTER the seat exists still rolls back "
   "everything, in the superior mode the schema now admits")
def _():
    # the hire is legal; the audience target is not — all-or-nothing must
    # discard the seat, the insertion and the queued kickoff together
    refused("mid", {**SUPERIOR, "name": "ghost", "target": "kid",
                    "audiences": ["no-such-agent"]})


@t("…and the positive controls still hold on the tree those refusals left")
def _():
    org = store.load_org(SLUG)
    eq(org.nodes["successor"]["parent"], "mid")
    eq(org.nodes["kid"]["parent"], "successor")
    eq(org.nodes["worker"]["parent"], "kid")
    sound(org, "at the end")


print(f"\nALL {PASS} CHECKS PASS")
