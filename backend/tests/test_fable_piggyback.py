"""Fable rides along with a subscription limit — when it has nothing of its own.

The user's 2026-08-26 feature, in their words: *"if any non-fable tier agent
goes out of capacity, and fable does not have a refresh period, then set
fable's refresh period to the same one as well."*

It AMENDS D-148, which put fable deliberately outside the haiku/sonnet/opus
pool. Fable is still not IN the pool — the difference is the whole reason this
suite exists and is asserted throughout:

    the POOL is a max()-MIRROR   — symmetric, and it pushes a sibling out
    the RIDE-ALONG is ABSENT-ONLY — one-directional, and it never moves a mark

WHY EVERY SECTION HAS A CONTROL BESIDE IT
-----------------------------------------
`marks[FABLE] = ts` unguarded would pass a naive reading of the ruling, and
would also pass "mark every tier in TIERS", and would also pass a version that
overwrites a real weekly fable limit with a five-hour subscription window —
the last of which hands back fable capacity that does not exist. So the checks
that would still pass under those wrong implementations are named as such, and
each one has the discriminating check next to it. §5 pins the branch to LIVE
CODE through the AST rather than a substring search over source, because a
substring search over source is precisely how this subtree last shipped a
check that matched a COMMENT and asserted nothing for good (D-149's method
note).

    §1  the rule: a non-fable limit parks that account's fable, at the same time
    §2  absent-only: a live fable mark is never moved; an EXPIRED one is
    §3  one-directional, per-account, and the old refusals still refuse
    §4  what it is FOR — routing, and the three payloads agreeing
    §5  the branch is live code (AST), and `record_limit` is still the only seam
    §6  controls: what would have to be true for the above to be vacuous

    python backend/tests/test_fable_piggyback.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="orgtree-fablepiggy-")
os.environ["ORGTREE_DATA"] = _TMP
os.makedirs(_TMP, exist_ok=True)
with open(os.path.join(_TMP, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # cp1252 console
    except (AttributeError, ValueError):
        pass

from orgtree import accounts, tokens  # noqa: E402

FAILS: list[str] = []
CHECKS = 0

HOUR = 3600.0
WEEK = 7 * 24 * HOUR
KID = "kFABLEPIG001"
KID2 = "kFABLEPIG002"
LIVE_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

_PKG = os.path.join(os.path.dirname(__file__), "..", "orgtree")
_ACCOUNTS_PY = os.path.join(_PKG, "accounts.py")


def check(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"ok {CHECKS:3d}  {label}")
    else:
        print(f"FAIL      {label}" + (f"  — {detail}" if detail else ""))
        FAILS.append(label + (f" — {detail}" if detail else ""))


def signed_in_as(uuid: str = LIVE_UUID):
    """⚠ The login is SET, never inherited. `live_identity` reads the
    developer's real `~/.claude.json`, so an un-stubbed suite asserts one
    thing on a signed-in machine and another on a signed-out one — and the
    signed-out leg skips the primary lane entirely, which is half of what
    this feature is about."""
    real = accounts.live_identity
    accounts.live_identity = lambda: {                      # type: ignore[assignment]
        "uuid": uuid, "email": "host@example.test" if uuid else ""}
    return lambda: setattr(accounts, "live_identity", real)


def seed(*, second: bool = False) -> None:
    """One key row (optionally two), no marks anywhere."""
    doc = accounts.load()
    doc["keys"] = [{"id": KID, "account_uuid": None}]
    if second:
        doc["keys"].append({"id": KID2, "account_uuid": None})
    doc["usage_refreshes"] = {}
    accounts.save(doc)
    tokens.put(KID, "sk-ant-oat01-" + "x" * 80)
    if second:
        tokens.put(KID2, "sk-ant-oat01-" + "y" * 80)


def marks(account: str) -> dict[str, float]:
    return dict(accounts.load()["usage_refreshes"].get(account) or {})


def preset(account: str, tier: str, at: float) -> None:
    """A mark placed BY HAND. It has to be, for the same reason D-148's suite
    had to: once the mirror and the ride-along exist, no sequence of
    `record_limit` calls can produce a lone-tier state, so the interesting
    fixtures can only ARRIVE — from an accounts.json written before this
    change, or from a real fable limit recorded a week ago."""
    seed()
    doc = accounts.load()
    doc["usage_refreshes"] = {account: {tier: at}}
    accounts.save(doc)


# --------------------------------------------------------------------------
def s1_the_rule() -> None:
    print("\n§1  a non-fable limit parks that account's fable, at the same time")
    restore = signed_in_as()
    try:
        now = time.time()
        # ⚠ EVERY non-fable tier gets its own leg. One leg standing in for
        # three is the abstention shape this subtree keeps shipping: a rule
        # written `if tier == "sonnet"` would pass a sonnet-only check and be
        # wrong for the two tiers nobody looked at.
        for i, tier in enumerate(accounts.POOLED):
            seed()
            at = now + HOUR + i          # distinct per leg, so a stale
            ok = accounts.record_limit(accounts.PRIMARY, tier, at)
            m = marks(accounts.PRIMARY)  # registry cannot satisfy the next leg
            check(f"1.{i + 1} a {tier} limit gives fable the same refresh time",
                  ok and abs(m.get("fable", 0) - at) < 1e-6, repr(m))

        # 1.4 …and it is the RECORDED time, not "whatever the pool ended up
        #     at". The two differ exactly when a pooled sibling was already
        #     parked later — see 1.5 — and this is the leg that says which
        #     one the ruling meant ("the same one" = the limit just hit).
        preset(accounts.PRIMARY, "opus", now + 5 * HOUR)
        accounts.record_limit(accounts.PRIMARY, "sonnet", now + HOUR)
        m = marks(accounts.PRIMARY)
        check("1.4 …the time RECORDED, not the pool's max()",
              abs(m.get("fable", 0) - (now + HOUR)) < 1, repr(m))
        check("1.5 (control) the pool DID max() in that same call — so 1.4 is "
              "a real distinction, not two names for one number",
              abs(m.get("opus", 0) - (now + 5 * HOUR)) < 1
              and abs(m.get("sonnet", 0) - (now + HOUR)) < 1, repr(m))

        # 1.6 it lands on a KEY ROW too, not just the primary. The lane that
        #     actually burns fable turns when the primary is out is a key row.
        seed()
        accounts.record_limit(KID, "haiku", now + 2 * HOUR)
        check("1.6 a key row's fable rides along with its own subscription",
              abs(marks(KID).get("fable", 0) - (now + 2 * HOUR)) < 1,
              repr(marks(KID)))
    finally:
        restore()


# --------------------------------------------------------------------------
def s2_absent_only() -> None:
    print("\n§2  absent-only — a live fable mark is never moved, in either "
          "direction")
    restore = signed_in_as()
    try:
        now = time.time()

        # 2.1 THE ONE THAT MATTERS MOST. A real weekly fable limit is DAYS
        #     out; a subscription window is hours. Overwriting it would hand
        #     back fable capacity that does not exist, and the account would
        #     be walked into the same weekly wall every five hours until the
        #     week turned over.
        preset(accounts.PRIMARY, "fable", now + WEEK)
        accounts.record_limit(accounts.PRIMARY, "opus", now + HOUR)
        m = marks(accounts.PRIMARY)
        check("2.1 a weekly fable mark is NOT shortened to the pool's window",
              abs(m.get("fable", 0) - (now + WEEK)) < 1, repr(m))
        check("2.2 (control) the pooled tiers in that same call DID take the "
              "new time — the registry was written, 2.1 is not a no-op",
              abs(m.get("opus", 0) - (now + HOUR)) < 1, repr(m))

        # 2.3 …and not LENGTHENED either. `max()` would pass 2.1 and fail
        #     here, which is the difference between the pool's rule and this
        #     one stated as a check.
        preset(accounts.PRIMARY, "fable", now + 0.25 * HOUR)
        accounts.record_limit(accounts.PRIMARY, "opus", now + HOUR)
        m = marks(accounts.PRIMARY)
        check("2.3 …nor is an earlier fable mark pushed out (this is NOT max)",
              abs(m.get("fable", 0) - (now + 0.25 * HOUR)) < 1, repr(m))

        # 2.4 an EXPIRED fable mark is capacity, and capacity is what the
        #     rule is allowed to spend. (`_prune_expired` drops it first, so
        #     "absent" and "expired" are one state by the time the branch
        #     runs — asserted, not assumed.)
        preset(accounts.PRIMARY, "fable", now - 5)
        accounts.record_limit(accounts.PRIMARY, "opus", now + HOUR)
        m = marks(accounts.PRIMARY)
        check("2.4 an EXPIRED fable mark reads as absent and is replaced",
              abs(m.get("fable", 0) - (now + HOUR)) < 1, repr(m))

        # 2.5 a second limit on an account whose fable is already riding
        #     along does not extend it either — the ride-along mark is an
        #     ordinary mark the moment it lands.
        seed()
        accounts.record_limit(accounts.PRIMARY, "sonnet", now + HOUR)
        accounts.record_limit(accounts.PRIMARY, "sonnet", now + 3 * HOUR)
        m = marks(accounts.PRIMARY)
        check("2.5 a later limit does not extend an already-riding fable",
              abs(m.get("fable", 0) - (now + HOUR)) < 1
              and abs(m.get("sonnet", 0) - (now + 3 * HOUR)) < 1, repr(m))
    finally:
        restore()


# --------------------------------------------------------------------------
def s3_bounds() -> None:
    print("\n§3  one-directional, per-account, and the refusals still refuse")
    restore = signed_in_as()
    try:
        now = time.time()

        # 3.1 the reverse direction is unchanged: fable spreads to nobody.
        #     Without this, "mark every tier in TIERS" passes all of §1.
        seed()
        accounts.record_limit(accounts.PRIMARY, "fable", now + HOUR)
        check("3.1 a fable limit still marks fable alone",
              set(marks(accounts.PRIMARY)) == {"fable"},
              repr(marks(accounts.PRIMARY)))

        # 3.2 PER-ACCOUNT. One account running out says nothing about
        #     another's fable — parking fable machine-wide would be a
        #     different, and much more expensive, feature.
        seed(second=True)
        accounts.record_limit(accounts.PRIMARY, "opus", now + HOUR)
        check("3.2 the primary's limit leaves other accounts' fable alone",
              "fable" in marks(accounts.PRIMARY)
              and not marks(KID) and not marks(KID2),
              f"primary={marks(accounts.PRIMARY)} k1={marks(KID)} "
              f"k2={marks(KID2)}")

        # 3.3-3.5 the refusals `record_limit` owes must not have grown a
        #     back door: a call that marks NOTHING must not mark fable
        #     either. This is the shape that would bite hardest — an
        #     unknown-tier call parking the most expensive lane on the box.
        seed()
        check("3.3 an unknown account marks no fable",
              accounts.record_limit("kNOSUCH", "sonnet", now + HOUR) is False
              and not marks("kNOSUCH") and not marks(accounts.PRIMARY),
              repr(accounts.load()["usage_refreshes"]))
        check("3.4 an unknown tier marks no fable",
              accounts.record_limit(accounts.PRIMARY, "gpt", now + HOUR)
              is False and not marks(accounts.PRIMARY),
              repr(marks(accounts.PRIMARY)))
        check("3.5 a refresh time already past marks no fable",
              accounts.record_limit(accounts.PRIMARY, "opus", now - 5)
              is False and not marks(accounts.PRIMARY),
              repr(marks(accounts.PRIMARY)))
    finally:
        restore()


# --------------------------------------------------------------------------
def s4_what_it_is_for() -> None:
    print("\n§4  the point: a fable spawn goes elsewhere, and every surface "
          "says the same thing")
    restore = signed_in_as()
    try:
        now = time.time()
        seed(second=True)

        # 4.1 the control FIRST — with capacity everywhere, fable is on the
        #     primary, so 4.2's answer is not "the only lane there is"
        check("4.1 (control) with capacity everywhere fable is on the primary",
              accounts.resolve("fable")["account"] == accounts.PRIMARY,
              repr(accounts.resolve("fable")))

        accounts.record_limit(accounts.PRIMARY, "opus", now + HOUR)
        r = accounts.resolve("fable")
        check("4.2 THE DEFECT THIS FIXES: a fable turn no longer spawns onto "
              "an account that just proved it has nothing left",
              r["account"] == KID and r["available"] is True, repr(r))

        # 4.3 …and nothing is BLOCKED when every lane is marked: the router
        #     still names the soonest-refreshing one and a probing spawn goes
        #     there. A rule that could strand fable entirely would be a much
        #     bigger change than the user asked for.
        seed()
        accounts.record_limit(accounts.PRIMARY, "opus", now + 2 * HOUR)
        accounts.record_limit(KID, "opus", now + HOUR)
        r = accounts.resolve("fable")
        check("4.3 with every lane marked, fable still resolves — to the "
              "soonest to refresh, unavailable but nameable",
              r["account"] == KID and r["available"] is False
              and r["refresh_at"] is not None, repr(r))

        # 4.4-4.6 the three surfaces are ONE state, not three copies of it:
        #     the router, the standing table, and the payload the panel gets.
        seed()
        accounts.record_limit(KID, "sonnet", now + HOUR)
        st = {t["tier"]: t for t in accounts.tier_standing(accounts.load(), KID)}
        check("4.4 tier_standing shows the key's fable waiting…",
              st["fable"]["available"] is False
              and st["fable"]["refresh_at"] == st["sonnet"]["refresh_at"],
              repr(st["fable"]))
        check("4.5 …and still reports fable's pool as None — it rode along, "
              "it did not JOIN the bucket",
              st["fable"]["pool"] is None
              and st["sonnet"]["pool"] == list(accounts.POOLED), repr(st))
        got = accounts.account_usage(KID)
        payload = {t["tier"]: t for t in (got.get("tiers") or [])}
        check("4.6 the panel payload carries the same fable row",
              payload.get("fable", {}).get("available") is False
              and payload["fable"]["refresh_at"] == st["fable"]["refresh_at"],
              repr(got)[:220])
    finally:
        restore()


# --------------------------------------------------------------------------
def _record_limit_ast() -> ast.FunctionDef:
    with open(_ACCOUNTS_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "record_limit":
            return n
    raise AssertionError("record_limit not found in accounts.py")


def s5_live_code() -> None:
    print("\n§5  the branch is LIVE CODE, asserted on the AST")
    fn = _record_limit_ast()

    # 5.1 the GUARD: `tier != FABLE and FABLE not in marks`. Asserted as
    #     comparison nodes, which no comment or docstring can produce. The
    #     alternative — searching the source text for "FABLE not in marks" —
    #     is the exact check that survived a mutation in D-149 because the
    #     literal it looked for also sat in a comment. Never again.
    guards = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        cmps = [c for c in ast.walk(node.test) if isinstance(c, ast.Compare)]
        one_directional = any(
            isinstance(c.ops[0], ast.NotEq)
            and getattr(c.comparators[0], "id", "") == "FABLE"
            for c in cmps if c.ops)
        absent_only = any(
            isinstance(c.ops[0], ast.NotIn)
            and getattr(c.left, "id", "") == "FABLE"
            for c in cmps if c.ops)
        if one_directional and absent_only:
            guards.append(node)
    check("5.1 a live `tier != FABLE and FABLE not in marks` guard exists",
          len(guards) == 1, f"{len(guards)} matching If node(s)")

    # 5.2 …and what it guards is the assignment, to the RECORDED time. A
    #     guard over an empty body, or over `marks[FABLE] = something_else`,
    #     would satisfy 5.1 alone.
    assigned = []
    for g in guards:
        for node in ast.walk(ast.Module(body=g.body, type_ignores=[])):
            if not isinstance(node, ast.Assign):
                continue
            for t in node.targets:
                if (isinstance(t, ast.Subscript)
                        and getattr(t.value, "id", "") == "marks"
                        and getattr(t.slice, "id", "") == "FABLE"):
                    assigned.append(getattr(node.value, "id", "<expr>"))
    check("5.2 …and its body assigns marks[FABLE] = ts, the recorded time",
          assigned == ["ts"], repr(assigned))

    # 5.3 THE CONTROL THAT MAKES 5.1/5.2 WORTH HAVING: the word the naive
    #     search would have matched IS present in prose right there. A
    #     substring check over accounts.py would pass with the branch
    #     deleted; these two cannot.
    doc = ast.get_docstring(fn) or ""
    check("5.3 (control) 'fable' also appears in record_limit's PROSE — a "
          "substring search over source proves nothing here",
          "fable" in doc.lower(), repr(doc[:60]))

    # 5.4 `record_limit` is still the only seam. If another module learned to
    #     write `usage_refreshes` directly it would bypass the ride-along
    #     silently, and every check above would keep passing.
    others = []
    for name in sorted(os.listdir(_PKG)):
        if not name.endswith(".py") or name == "accounts.py":
            continue
        with open(os.path.join(_PKG, name), encoding="utf-8") as f:
            src = f.read()
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Constant)
                    and node.value == "usage_refreshes"):
                others.append(name)
                break
    check("5.4 no other module touches usage_refreshes in live code",
          others == [], repr(others))
    # …and the control: supervisor.py names it in a COMMENT, so 5.4 is an
    # AST check doing real work rather than a lucky grep.
    with open(os.path.join(_PKG, "supervisor.py"), encoding="utf-8") as f:
        check("5.5 (control) supervisor.py DOES say 'usage_refreshes' in a "
              "comment — 5.4 walks the AST for exactly this reason",
              "usage_refreshes" in f.read(), "not present at all")


# --------------------------------------------------------------------------
def s6_controls() -> None:
    print("\n§6  controls — what would make the above vacuous")
    restore = signed_in_as()
    try:
        check("6.1 live_identity is stubbed to the fixture login",
              accounts.live_identity()["uuid"] == LIVE_UUID,
              repr(accounts.live_identity()))
        restore()
        restore = signed_in_as(uuid="")
        seed()
        check("6.2 …and signed OUT is a different world (so 6.1 is not "
              "an assumption): the primary is not a lane at all",
              accounts.resolve("fable")["account"] == KID,
              repr(accounts.resolve("fable")))
    finally:
        restore()

    check("6.3 the registry under test is the throwaway one",
          os.path.dirname(accounts.registry_path()) == _TMP,
          accounts.registry_path())
    check("6.4 FABLE is a real tier and is NOT in the pool",
          accounts.FABLE in accounts.TIERS
          and accounts.FABLE not in accounts.POOLED
          and accounts.FABLE == "fable", repr(accounts.FABLE))
    # 6.5 the file the AST checks read is the module the runtime imported —
    #     otherwise §5 could be describing a copy nobody runs.
    check("6.5 §5 parses the very file `accounts` was imported from",
          os.path.realpath(_ACCOUNTS_PY)
          == os.path.realpath(accounts.__file__ or ""),
          f"{_ACCOUNTS_PY} vs {accounts.__file__}")


def main() -> int:
    for fn in (s1_the_rule, s2_absent_only, s3_bounds, s4_what_it_is_for,
               s5_live_code, s6_controls):
        try:
            fn()
        except Exception:                                  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAILS.append(f"{fn.__name__} raised")
    for f in FAILS:
        print("   ·", f)
    if FAILS:
        print(f"\n{len(FAILS)} of {CHECKS} checks FAILED")
        return 1
    print(f"\nALL {CHECKS} CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
