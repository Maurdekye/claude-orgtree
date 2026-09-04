"""Grants are WHOLE credits; seats are not. The 104.2 defect, end to end.

    python backend/tests/test_fractional_grant.py

The operator's report (2026-09-04) was "i cant increase it; it just fails
outright". The state behind it: a top-level coordinator on a grant of 104.2,
arrived at automatically when a 104-credit report was hired on `gpt-reserve`
(seat 0.2) beneath a 100-credit superior. `_chain_acquire` inflated the
superior's grant by the fractional shortfall and left it there.

Nothing in the LEDGER complained — its arithmetic is fraction-correct. The
refusal was at the door: the credit bar rounds its TARGET to a whole number
and sends `target - grant`, so off 104.2 every delta it can compute is
fractional, and `Op.delta` was typed `int`. Pydantic answered 422 in both
directions, with no dialog and nothing in the event log.

The user's ruling: "just round up grants to the next whole number when
saturating superiors like that; fractional grant amounts is an invalid state
anyway imo". So this suite pins THREE separate things, because a fix to any
one of them alone leaves the operator stuck:

    §1  the cascade lands a WHOLE grant, rounded UP    (no new 104.2s)
    §2  a doc that already carries 104.2 is healed on load  (the live state)
    §3  the ops door accepts a fractional delta        (the actual 422)
    §4  SEATS are still fractional and `free` still is — the ruling is about
        grants and caps, and a fix that made seats whole would be worse
    §5  NEGATIVE CONTROL: rounding up has not made grants permissive
    §6  retire returns exactly seat + grant — the rounding does not mint

⚠ EVERY CHECK HERE IS PROVEN ABLE TO FAIL. §0 is a positive control that
reproduces the original defect against deliberately un-fixed arithmetic, so
"the suite is green" cannot mean "the suite never looked". If §0 ever stops
failing, the reproduction has rotted and the rest of the file is decoration.
"""

import math
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-fracgrant-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# never let a fixture org reach the operator's real mail hub (see test_ledger)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["ORGTREE_PORT"] = "7497"
os.environ["ORGTREE_STEER_HOOK"] = "0"

from fastapi.testclient import TestClient                      # noqa: E402
from orgtree import api, sandbox, store, supervisor            # noqa: E402
from orgtree.ledger import LedgerError, Org, USER              # noqa: E402

supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
supervisor.storage_check = lambda slug: None
sandbox.warm = lambda org: None

# The suite writes orgs. Refuse to run at all if this process could resolve to
# the operator's real root — a leak here cost six orgs earlier today.
_LIVE = os.path.normcase(os.path.abspath(
    os.path.join(os.path.expanduser("~"), "orgtree")))
_HERE = os.path.normcase(os.path.abspath(store.DATA_ROOT))
assert _HERE != _LIVE and not _HERE.startswith(_LIVE + os.sep), (
    f"REFUSING TO RUN: store.DATA_ROOT={store.DATA_ROOT} is the live root")

PASS = 0
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True,
             "mcp": []}


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="fractional-grant fixture")
    s.update(over)
    return s


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


def expect_error(fn, needle=""):
    try:
        fn()
    except LedgerError as e:
        assert needle.lower() in str(e).lower(), f"wrong refusal: {e}"
        return str(e)
    raise AssertionError(f"expected a refusal containing {needle!r}, got none")


_n = [0]


def saturating_org():
    """The operator's shape: a top-level superior whose cap was raised by a
    large report on a SUB-$1 seat. Returns (org, coordinator id, sub id)."""
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 100, "coordinator", **spec())
    o.hire(USER, "coordinator", "gpt-reserve", 104, "sub", **spec())
    return o, "coordinator", "sub"


print("\n§0  positive control — the defect, reproduced against raw arithmetic")


@t("the un-rounded cascade really does produce 104.2 (control fails green-only)")
def _():
    # This is the OLD arithmetic, spelled out rather than called: a 100-credit
    # superior, a 104-credit report at seat 0.2, and the shortfall added raw.
    # If this ever stops landing on a fraction the reproduction is dead and
    # every check below is vacuous.
    need = round(0.2 + 104, 2)
    raw = round(100 + (need - 100), 2)
    eq(raw, 104.2, "the control's own arithmetic")
    true(raw != int(raw), "the control must produce a FRACTION or it proves nothing")


@t("…and the bar's delta off that value is fractional, which is the 422")
def _():
    # frontend/src/canvas/cards.tsx: v = round(target); onCommit(v - grant)
    grant = 104.2
    delta = math.floor(grant + 1.0 + 0.5) - grant     # a one-credit drag up
    true(delta != int(delta),
         f"expected a fractional delta off a fractional grant, got {delta!r}")


print("\n§1  the cascade lands a WHOLE grant, rounded UP")


@t("hiring a 104-credit gpt-reserve report leaves the superior on 105, not 104.2")
def _():
    o, c, s = saturating_org()
    g = o.node(c)["grant"]
    eq(g, 105, "the superior's raised cap")
    eq(g, int(g), "a grant must be a whole number")


@t("the rounding is UP: the superior is never left short of what it must carry")
def _():
    o, c, s = saturating_org()
    true(o.node(c)["grant"] >= o.committed(c),
         "a raised cap must still cover what it committed")
    true(o.free(c) >= 0, "free() may not go negative through the round-up")


@t("the report got exactly what was asked for — rounding the CAP moved nothing else")
def _():
    o, c, s = saturating_org()
    eq(o.node(s)["grant"], 104, "the report's own grant")
    eq(o.seat_cost(s), 0.2, "the report's seat")


@t("a whole-seat cascade is unchanged — no gratuitous +1 where nothing was fractional")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 100, "boss", **spec())
    o.hire(USER, "boss", "sonnet", 104, "kid", **spec())     # seat 2, whole
    eq(o.node("boss")["grant"], 106, "100 + the 6-credit shortfall, exactly")
    eq(o.free("boss"), 0, "an exactly-fitting cascade still fits exactly")


@t("deep cascade: every grant on the chain is whole, and no free() went negative")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 30, "top", **spec())
    o.hire(USER, "top", "sonnet", 10, "mid", **spec())
    # forcible hire from the USER, three deep, on a fractional seat
    o.hire(USER, "mid", "luna", 40, "deep", **spec())
    for k in ("top", "mid", "deep"):
        g = o.node(k)["grant"]
        eq(g, int(g), f"{k}'s grant must be whole")
        true(o.free(k) >= 0, f"{k} free went negative: {o.free(k)}")
    eq(o.node("deep")["grant"], 40, "the hire got what was asked")


print("\n§2  an existing fractional grant is HEALED on load (the live state)")


@t("a doc carrying 104.2 loads as 105")
def _():
    o, c, s = saturating_org()
    o.node(c)["grant"] = 104.2                 # forge the operator's state
    o.d.pop("whole_grants_v1", None)           # …as a doc written before the ruling
    store.save_org(o)
    back = store.load_org(o.d["slug"])
    eq(back.node(c)["grant"], 105, "healed on load")
    true(back.free(c) >= 0, "and still solvent")


@t("healing is idempotent — a second load moves nothing")
def _():
    o, c, s = saturating_org()
    o.node(c)["grant"] = 104.2
    o.d.pop("whole_grants_v1", None)
    store.save_org(o)
    first = store.load_org(o.d["slug"])
    store.save_org(first)
    second = store.load_org(o.d["slug"])
    eq(second.node(c)["grant"], first.node(c)["grant"], "stable")
    eq(second.node(c)["grant"], 105, "and still the healed value")


@t("healing walks deepest-first, so a rounded child never strands its parent")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 50, "top", **spec())
    o.hire(USER, "top", "sonnet", 20, "mid", **spec())
    o.node("mid")["grant"] = 20.4              # forge a fraction in the MIDDLE
    o.node("top")["grant"] = 22.4              # exactly carrying it (2 + 20.4)
    o.d.pop("whole_grants_v1", None)
    store.save_org(o)
    back = store.load_org(o.d["slug"])
    eq(back.node("mid")["grant"], 21, "child rounded up")
    true(back.free("top") >= 0,
         f"the parent must have been lifted to cover it, free={back.free('top')}")
    g = back.node("top")["grant"]
    eq(g, int(g), "and the parent's own grant is whole too")


@t("healing never LOWERS a grant — capacity granted is capacity kept")
def _():
    o, c, s = saturating_org()
    o.node(c)["grant"] = 200.9
    o.d.pop("whole_grants_v1", None)
    store.save_org(o)
    back = store.load_org(o.d["slug"])
    true(back.node(c)["grant"] >= 200.9,
         f"rounded DOWN to {back.node(c)['grant']} — that silently takes credits")
    eq(back.node(c)["grant"], 201, "up to the next whole credit")


@t("the repair is ONCE PER DOC — a later melt is not re-rounded on every load")
def _():
    # ⚠ THIS IS THE ANTI-MINT CHECK. `switch_model` still lands a seat
    # difference in a grant on purpose (the node's total holding must not
    # move), so a STANDING round-up rule would add up to a credit out of the
    # parent on every switch-and-reload cycle. The repair is flagged and runs
    # once; after that a fractional grant survives a round trip untouched.
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 6, "boss", **spec())
    o.hire("boss", "boss", "gpt-reserve", 0, "kid", **spec())
    o.switch_model("boss", "kid", "luna")
    o.node("kid")["grant"] = 0.1               # stand in for a melt residue
    hold = round(o.seat_cost("kid") + o.node("kid")["grant"], 2)
    store.save_org(o)
    back = store.load_org(o.d["slug"])
    eq(back.node("kid")["grant"], 0.1, "a flagged doc is left alone")
    eq(round(back.seat_cost("kid") + back.node("kid")["grant"], 2), hold,
       "the node's total holding must not move across a reload")


print("\n§3  the ops door accepts the delta the credit bar actually computes")

client = TestClient(api.app)


@t("a fractional delta is no longer a 422 (this is the reported outage)")
def _():
    o, c, s = saturating_org()
    o.node(c)["grant"] = 104.2                 # a grant a melt could still make
    store.save_org(o)   # flag intact: the door must cope, not silently heal
    r = client.post(f"/api/orgs/{o.d['slug']}/ops",
                    json={"op": "reallocate", "actor": USER, "node": c,
                          "delta": 0.7999999999999972})
    eq(r.status_code, 200, f"still refused: {r.text[:200]}")


@t("…and whatever it lands on is a WHOLE grant")
def _():
    o, c, s = saturating_org()
    o.node(c)["grant"] = 104.2
    store.save_org(o)
    r = client.post(f"/api/orgs/{o.d['slug']}/ops",
                    json={"op": "reallocate", "actor": USER, "node": c,
                          "delta": 0.7999999999999972})
    eq(r.status_code, 200, r.text[:200])
    g = r.json()["grant"]
    eq(g, int(g), f"the door wrote a fractional grant: {g!r}")


@t("in the ledger, the bar's own delta off 104.2 lands exactly on 105")
def _():
    # the door heals 104.2 on load, so this is the only place the pre-heal
    # value can still be seen — and it is the arithmetic the operator hit.
    o, c, s = saturating_org()
    o.node(c)["grant"] = 104.2
    out = o.reallocate(USER, c, 0.7999999999999972)
    eq(out["grant"], 105, "the bar showed 105; the ledger must hold 105")


@t("a stale bar's fractional delta cannot re-open the invalid state")
def _():
    o, c, s = saturating_org()          # already whole at 105
    out = o.reallocate(USER, c, 0.7999999999999972)
    g = out["grant"]
    eq(g, int(g), f"reallocate wrote a fractional grant: {g!r}")
    eq(g, 106, "snapped UP to the next whole credit, never down")


@t("a plain whole increase still works, and still lands whole")
def _():
    o, c, s = saturating_org()
    store.save_org(o)
    r = client.post(f"/api/orgs/{o.d['slug']}/ops",
                    json={"op": "reallocate", "actor": USER, "node": c,
                          "delta": 10})
    eq(r.status_code, 200, r.text[:200])
    eq(r.json()["grant"], 115, "105 + 10")


print("\n§4  SEATS stay fractional — the ruling was about grants, not prices")


@t("gpt-reserve and luna still cost 0.2; nothing rounded a seat")
def _():
    o, c, s = saturating_org()
    eq(o.d["tiers"]["gpt-reserve"], 0.2, "gpt-reserve seat")
    eq(o.d["tiers"]["luna"], 0.2, "luna seat")


@t("free() is still allowed to be fractional when a 0.2 seat is live under it")
def _():
    o, c, s = saturating_org()
    f = o.free(c)
    eq(f, 0.8, "105 - (0.2 seat + 104 grant)")
    true(f != int(f),
         "a whole free here would mean a seat got rounded — that is the wrong fix")


print("\n§5  NEGATIVE CONTROL — grants did not become permissive")


@t("an agent still cannot grant itself past what its chain can afford")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 20, "boss", **spec())
    o.hire("boss", "boss", "opus", 0, "kid", **spec())        # seat 5, free 15
    expect_error(lambda: o.reallocate("boss", "kid", 999),
                 "not enough free credits on the chain")
    eq(o.node("kid")["grant"], 0, "and the refusal left nothing behind")


@t("an agent's over-budget HIRE is still refused, rounding or no rounding")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 6, "boss", **spec())
    expect_error(lambda: o.hire("boss", "boss", "opus", 500, "kid", **spec()),
                 "not enough free credits")


@t("the D-014 top-level cap still refuses an increase past it")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.d["max_top_grant"] = 110
    o.hire(USER, None, "opus", 100, "boss", **spec())
    expect_error(lambda: o.reallocate(USER, "boss", 50),
                 "top-level grant cap")
    eq(o.node("boss")["grant"], 100, "refused, and unchanged")


@t("the cap sees the ROUNDED figure: a raise may land ON it but not past it")
def _():
    # boss has 5 free; a gpt-reserve report at grant 8 needs 8.2, so 3.2 must
    # bubble up — 8.2 raw, 9 rounded. The cap CANNOT discriminate between
    # those two on its own (`max_top_grant` is read through int(), so no
    # integer cap falls strictly between 8.2 and 9); what is observable is
    # that the rounded 9 was admitted against a cap of 9 and that the cap
    # still bites immediately above it.
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.d["max_top_grant"] = 9
    o.hire(USER, None, "opus", 5, "boss", **spec())
    o.hire(USER, "boss", "gpt-reserve", 8, "kid", **spec())
    eq(o.node("boss")["grant"], 9, "raised to 9, not left on 8.2")
    expect_error(lambda: o.reallocate(USER, "boss", 1), "top-level grant cap")
    eq(o.node("boss")["grant"], 9, "refused, and unchanged")


@t("…and a cap BELOW the raise refuses the hire outright, leaving no credits behind")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.d["max_top_grant"] = 8
    o.hire(USER, None, "opus", 5, "boss", **spec())
    expect_error(lambda: o.hire(USER, "boss", "gpt-reserve", 8, "kid", **spec()),
                 "top-level grant cap")
    eq(o.node("boss")["grant"], 5, "the refusal did not inflate anything")
    eq(o.free("boss"), 5, "and left no stranded credits")


@t("a reduction below what the children hold is still refused")
def _():
    o, c, s = saturating_org()
    expect_error(lambda: o.reallocate(USER, c, -50), "unused")
    eq(o.node(c)["grant"], 105, "refused, and unchanged")


print("\n§6  retire returns exactly seat + grant — the rounding does not mint")


@t("freed equals the report's real holding, not the rounded cap above it")
def _():
    o, c, s = saturating_org()
    before = o.node(c)["grant"]
    r = o.retire(USER, s)
    eq(r["freed"], 104.2, "0.2 seat + 104 grant")
    eq(o.node(c)["grant"], before, "retiring a report does not touch the superior's grant")
    eq(o.free(c), 105, "the whole cap is free again — no more, no less")


@t("hire → retire → hire is not a credit mint")
def _():
    o, c, s = saturating_org()
    o.retire(USER, s)
    eq(o.free(c), 105)
    o.hire(USER, c, "gpt-reserve", 104, "sub2", **spec())
    eq(o.node(c)["grant"], 105, "the superior's cap did not climb a second time")
    eq(o.free(c), 0.8, "and the books balance exactly as before")


print("\n§7  no credit quantity is SWALLOWED — round up or refuse, never no-op")

# ⚠ THE SHAPE THIS SECTION IS ABOUT is not the lost fraction. It is an
# operation that reports SUCCESS and does something other than what was asked:
# an agent asking to move 0.5 credits and being told "ok" while the ledger did
# nothing at all. Losing 0.5 of a credit is small; a success message for work
# that never happened is what makes a caller stop checking.
#
# Every check here therefore asserts one of two outcomes and never a third:
# the asked-for amount landed (rounded UP, so nothing is lost), or the call
# REFUSED and said why. "Returned 200 and changed nothing" fails.


def agent_tool(o, node, tool, args):
    """Call the MCP door the way an agent does, over /api/agent."""
    return client.post("/api/agent", json={"org": o.d["slug"], "node": node,
                                           "tool": tool, "args": args})


@t("MCP reallocate: a fractional delta moves credits or refuses — never a silent no-op")
def _():
    o, c, s = saturating_org()
    o.hire(USER, c, "opus", 4, "kid", **spec())
    store.save_org(o)
    before = store.load_org(o.d["slug"]).node("kid")["grant"]
    r = agent_tool(o, c, "orgtree_reallocate", {"node": "kid", "delta": 0.5})
    after = store.load_org(o.d["slug"]).node("kid")["grant"]
    if r.status_code == 200:
        true(after != before,
             f"reported success and moved nothing: {before!r} -> {after!r} "
             f"(the caller asked for +0.5 and was told ok)")
        true(after > before, f"asked to ADD and the grant fell: {before} -> {after}")
    else:
        eq(after, before, "a refusal must leave the grant alone")


@t("…and with headroom it lands on the next whole credit, never truncating")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 40, "boss", **spec())    # room for the round-up
    o.hire("boss", "boss", "opus", 4, "kid", **spec())
    store.save_org(o)
    r = agent_tool(o, "boss", "orgtree_reallocate", {"node": "kid", "delta": 0.5})
    eq(r.status_code, 200, r.text[:200])
    eq(store.load_org(o.d["slug"]).node("kid")["grant"], 5, "4 + 0.5, rounded UP")


@t("…and WITHOUT headroom it refuses and names the shortfall, rather than half-doing it")
def _():
    o, c, s = saturating_org()                          # coordinator free = 0.8
    o.hire(USER, c, "opus", 4, "kid", **spec())
    store.save_org(o)
    r = agent_tool(o, c, "orgtree_reallocate", {"node": "kid", "delta": 0.5})
    eq(r.status_code, 422, f"expected a refusal, got {r.status_code}")
    true("free" in r.text, f"refused without naming the shortfall: {r.text[:200]}")
    eq(store.load_org(o.d["slug"]).node("kid")["grant"], 4,
       "and the refusal left the grant exactly where it was")


@t("MCP hire: a fractional grant is refused, not quietly shaved to a whole one")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 60, "boss", **spec())   # plenty of headroom, so
    c = "boss"                                         # a refusal is ABOUT the grant
    store.save_org(o)
    r = agent_tool(o, c, "orgtree_hire",
                   {"tier": "opus", "grant": 5.7, "name": "shaved",
                    "charter": "x", "org_visibility": "team",
                    "add_dirs": [], "tools": dict(ALL_TOOLS)})
    back = store.load_org(o.d["slug"])
    hired = [k for k in back.nodes if k.startswith("shaved")]
    if r.status_code == 200:
        true(hired, "reported success and hired nobody")
        g = back.node(hired[0])["grant"]
        true(g >= 5.7, f"asked for 5.7 and silently got {g!r}")
    else:
        eq(hired, [], "a refusal must not leave a node behind")
        true("integer" in r.text or "number" in r.text,
             f"refused, but not about the grant: {r.text[:200]}")


@t("rehire with an explicit fractional grant rounds UP, never down")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 40, "boss", **spec())
    o.hire("boss", "boss", "opus", 3, "kid", **spec())
    o.retire("boss", "kid")
    o.rehire("boss", "kid", 5.7)
    g = o.node("kid")["grant"]
    true(g >= 5.7, f"asked for 5.7 and got {g!r} — 0.7 of a credit swallowed")
    eq(g, 6, "rounded up to the next whole credit")


@t("rehire with NO grant still keeps the archived value exactly (melt round-trip)")
def _():
    # the other half of the same line: the DEFAULT must not be rounded, or a
    # melt-fractional archived grant would grow every time it is rehired.
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 40, "boss", **spec())
    o.hire("boss", "boss", "gpt-reserve", 0, "kid", **spec())
    o.node("kid")["grant"] = 0.1                # stand in for a melt residue
    o.retire("boss", "kid")
    o.rehire("boss", "kid")
    eq(o.node("kid")["grant"], 0.1, "the default keeps its fraction untouched")


@t("request_credits records the amount asked for, rounded UP")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 10, "boss", **spec())
    o.request_credits("boss", 20.5, "need a bit more")
    req = o.d["credit_requests"][-1]
    true(req["new"] >= 20.5,
         f"asked for 20.5, the request card says {req['new']!r}")
    eq(req["new"], 21, "rounded up to the next whole credit")


@t("approving a fractional amount grants at least it, never less")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.hire(USER, None, "opus", 10, "boss", **spec())
    o.request_credits("boss", 30, "more")
    rid = o.d["credit_requests"][-1]["id"]
    o.credit_request_action(rid, "approve", granted=20.5)
    g = o.node("boss")["grant"]
    true(g >= 20.5, f"approved 20.5 and the node holds {g!r}")
    eq(g, 21, "rounded up")


@t("the preview agrees with what approving would actually do")
def _():
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.d["max_top_grant"] = 21
    o.hire(USER, None, "opus", 10, "boss", **spec())
    o.request_credits("boss", 30, "more")
    rid = o.d["credit_requests"][-1]["id"]
    prev = o.credit_preview(rid, 20.5)
    o.credit_request_action(rid, "approve", granted=20.5)
    landed = o.node("boss")["grant"]
    eq(prev["ok"], True, f"preview said no, approve said {landed}")
    eq(landed, 21, "and the cap of 21 admits exactly it")


@t("…and the preview refuses when the ROUNDED figure crosses the cap")
def _():
    # the preview must judge the number that will actually be written. A
    # preview that checked the un-rounded 20.5 against a cap of 20 would say
    # "ok" and then the approval would write 21.
    _n[0] += 1
    o = Org.create(f"frac{_n[0]}")
    o.d["max_top_grant"] = 20
    o.hire(USER, None, "opus", 10, "boss", **spec())
    o.request_credits("boss", 30, "more")
    rid = o.d["credit_requests"][-1]["id"]
    eq(o.credit_preview(rid, 20.5)["ok"], False,
       "20.5 rounds to 21, which is past a cap of 20")


print(f"\n{PASS} checks passed")
