"""One usage pool for haiku/sonnet/opus, and a key row that shows its own state.

The user's 2026-08-25 ruling, three parts:

  · haiku, sonnet and opus bill against ONE bucket, so a limit on any of them
    is a limit on all three — mirror the refresh time across the pool
  · a key row's usage button shows the internal routing state for that account
    (which models have capacity, which are waiting, until when) instead of
    percentages it can never obtain (D-147)
  · duplicate-of-primary greying is GONE — a setup-token key can never resolve
    its own account, so the check could only ever fire for v1-migrated rows

WHY THE MIRROR IS ASSERTED THROUGH `resolve`, NOT ONLY THROUGH THE DICT
----------------------------------------------------------------------
The dict is the mechanism; the DEFECT was a wasted spawn. Before this, an opus
turn failed over correctly and the next haiku turn walked straight back into
the same exhausted account, because haiku carried no mark of its own. So §1
ends on the routing question — "where does haiku go now" — which is the thing
that was actually wrong, and which a dict-shape assertion alone would not see.

⚠ EVERY SECTION STUBS `live_identity`. It reads the developer's real
`~/.claude.json`, so a suite that let it through would assert different things
on a signed-in machine than on a signed-out one — and the signed-out leg would
silently skip the primary lane entirely. The environment under test is SET
here, never inherited.

    §1  the pool: one limit marks three tiers, and routing follows
    §2  `tier_standing` — the key row's view of its own capacity
    §3  duplicate-of-primary is gone, from routing and from both payloads
    §4  controls: what would have to be true for the above to be vacuous

    python backend/tests/test_account_pool_state.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from typing import Any

_TMP = tempfile.mkdtemp(prefix="orgtree-poolstate-")
os.environ["ORGTREE_DATA"] = _TMP
# a throwaway ORGTREE_DATA does NOT isolate the mail hub (see
# test_external_mail §1, which guards this over the whole directory)
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
KID = "kPOOLKEY0001"
KID2 = "kPOOLKEY0002"
LIVE_UUID = "11111111-2222-3333-4444-555555555555"


def check(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"ok {CHECKS:3d}  {label}")
    else:
        print(f"FAIL      {label}" + (f"  — {detail}" if detail else ""))
        FAILS.append(label + (f" — {detail}" if detail else ""))


def signed_in_as(uuid: str = LIVE_UUID):
    """Install a KNOWN login. Returns the restore callable."""
    real = accounts.live_identity
    accounts.live_identity = lambda: {                      # type: ignore[assignment]
        "uuid": uuid, "email": "host@example.test" if uuid else ""}
    return lambda: setattr(accounts, "live_identity", real)


def seed(*, key_uuid: str | None = None, second: bool = False) -> None:
    """One key row (optionally two), no marks. Registered in the token store —
    `account_usage` refuses a row whose key is missing, for other reasons."""
    doc = accounts.load()
    doc["keys"] = [{"id": KID, "account_uuid": key_uuid}]
    if second:
        doc["keys"].append({"id": KID2, "account_uuid": None})
    doc["usage_refreshes"] = {}
    accounts.save(doc)
    tokens.put(KID, "sk-ant-oat01-" + "x" * 80)
    if second:
        tokens.put(KID2, "sk-ant-oat01-" + "y" * 80)


def marks(account: str) -> dict[str, float]:
    return dict(accounts.load()["usage_refreshes"].get(account) or {})


# --------------------------------------------------------------------------
def s1_pool() -> None:
    print("\n§1  one bucket: a limit on any of haiku/sonnet/opus marks all three")
    restore = signed_in_as()
    try:
        seed()
        now = time.time()

        # 1.1 the mirror itself
        ok = accounts.record_limit(accounts.PRIMARY, "sonnet", now + HOUR)
        m = marks(accounts.PRIMARY)
        check("1.1 a sonnet limit is recorded", ok and "sonnet" in m, repr(m))
        check("1.2 …and lands on haiku and opus too, at the same time",
              m.get("haiku") == m.get("sonnet") == m.get("opus"), repr(m))

        # 1.3 THE NEGATIVE CONTROL FOR 1.2. Without it, an implementation that
        #     simply marks every known tier passes 1.2 exactly as well as the
        #     pool-aware one does — and would park fable, which bills its own
        #     lane, for a wall it never hit.
        check("1.3 …but NOT on fable, which is not in the bucket",
              "fable" not in m, repr(m))

        # 1.4 …and the reverse direction: fable spreads to nobody
        seed()
        accounts.record_limit(accounts.PRIMARY, "fable", now + HOUR)
        m = marks(accounts.PRIMARY)
        check("1.4 a fable limit marks fable alone",
              set(m) == {"fable"}, repr(m))

        # 1.5/1.6 the mirror is a FLOOR, never a ceiling.
        # ⚠ THE FIXTURE HAS TO BE SEEDED BY HAND, and that is the interesting
        # part: once the mirror exists, no pair of `record_limit` calls can
        # leave the pool uneven, so a lopsided state can only ARRIVE — from an
        # accounts.json written before this change, where a single tier was
        # marked alone. Which is exactly the state every machine has on the
        # day this deploys. A first draft built the fixture with two calls and
        # failed, because the first call had already mirrored.
        def lopsided(tier: str, at: float) -> None:
            seed()
            doc = accounts.load()
            doc["usage_refreshes"] = {accounts.PRIMARY: {tier: at}}
            accounts.save(doc)

        lopsided("opus", now + 2 * HOUR)
        accounts.record_limit(accounts.PRIMARY, "sonnet", now + HOUR)
        m = marks(accounts.PRIMARY)
        check("1.5 a sibling parked later is NOT shortened by the mirror",
              abs(m.get("opus", 0) - (now + 2 * HOUR)) < 1
              and abs(m.get("sonnet", 0) - (now + HOUR)) < 1, repr(m))
        lopsided("opus", now + HOUR)
        accounts.record_limit(accounts.PRIMARY, "sonnet", now + 2 * HOUR)
        m = marks(accounts.PRIMARY)
        check("1.6 …and a sibling parked earlier is pushed out to the later one",
              abs(m.get("opus", 0) - (now + 2 * HOUR)) < 1, repr(m))

        # 1.7 THE POINT OF ALL OF IT. Before the mirror, this resolved back to
        #     `primary` — one wasted spawn per sibling tier, every time.
        seed()
        accounts.record_limit(accounts.PRIMARY, "sonnet", now + HOUR)
        r = accounts.resolve("haiku")
        check("1.7 haiku now routes OFF an account whose sonnet hit the wall",
              r["account"] == KID and r["available"] is True, repr(r))
        rf = accounts.resolve("fable")
        check("1.8 …while fable still runs on it (its own lane, untouched)",
              rf["account"] == accounts.PRIMARY, repr(rf))

        # 1.9 the refusals `record_limit` already owed must survive the change
        seed()
        check("1.9 an unknown account still marks nothing",
              accounts.record_limit("kNOSUCH", "sonnet", now + HOUR) is False
              and not marks("kNOSUCH"), repr(accounts.load()))
        check("1.10 an unknown tier still marks nothing (no pool of surprises)",
              accounts.record_limit(accounts.PRIMARY, "gpt", now + HOUR)
              is False and not marks(accounts.PRIMARY),
              repr(marks(accounts.PRIMARY)))
        check("1.11 a refresh time already past is not a mark",
              accounts.record_limit(accounts.PRIMARY, "sonnet", now - 5)
              is False and not marks(accounts.PRIMARY),
              repr(marks(accounts.PRIMARY)))
    finally:
        restore()


# --------------------------------------------------------------------------
def s2_standing() -> None:
    print("\n§2  a key row's own capacity, model by model")
    restore = signed_in_as()
    try:
        seed()
        now = time.time()

        st = {t["tier"]: t for t in accounts.tier_standing(accounts.load(), KID)}
        check("2.1 every tier is listed, in TIERS order",
              [t["tier"] for t in accounts.tier_standing(accounts.load(), KID)]
              == list(accounts.TIERS), repr(list(st)))
        check("2.2 a virgin account has capacity everywhere, no refresh times",
              all(t["available"] and t["refresh_at"] is None
                  for t in st.values()), repr(st))
        check("2.3 the pooled tiers name their bucket; fable names none",
              st["sonnet"]["pool"] == list(accounts.POOLED)
              and st["fable"]["pool"] is None, repr(st))

        accounts.record_limit(KID, "opus", now + HOUR)
        st = {t["tier"]: t for t in accounts.tier_standing(accounts.load(), KID)}
        check("2.4 an opus limit shows all three pooled tiers as waiting",
              not any(st[t]["available"] for t in accounts.POOLED), repr(st))
        check("2.5 …all with the same refresh time, and it is an ISO string",
              len({st[t]["refresh_at"] for t in accounts.POOLED}) == 1
              and str(st["opus"]["refresh_at"]).endswith("Z"), repr(st))
        check("2.6 …and fable still has capacity", st["fable"]["available"],
              repr(st))

        # 2.7 ⚠ `available` MUST mean "this account has capacity", not "this
        #     tier runs here". They differ constantly and the wrong one would
        #     tell the user their untouched fallback is out of opus whenever
        #     the primary happens to be serving it.
        r = accounts.resolve("fable")
        check("2.7 capacity is per-account, not 'where the tier routes'",
              r["account"] == accounts.PRIMARY and st["fable"]["available"],
              f"routes to {r['account']}, key fable={st['fable']}")

        # 2.8 an EXPIRED mark reads as capacity, not as a stale wall
        doc = accounts.load()
        doc["usage_refreshes"] = {KID: {"opus": now - 5}}
        accounts.save(doc)
        st = {t["tier"]: t for t in accounts.tier_standing(accounts.load(), KID)}
        check("2.8 an expired mark reads as capacity",
              st["opus"]["available"] and st["opus"]["refresh_at"] is None,
              repr(st["opus"]))

        # 2.9 …and the payload the panel actually receives carries it
        seed()
        accounts.record_limit(KID, "haiku", now + HOUR)
        got = accounts.account_usage(KID)
        tiers = got.get("tiers") or []
        check("2.9 account_usage(key) carries the standing table",
              len(tiers) == len(accounts.TIERS), repr(got)[:200])
        check("2.10 …still flagged unsupported (percentages are not coming)",
              got.get("unsupported") is True and got.get("available") is False,
              repr(got)[:200])
        check("2.11 …and it agrees with the routing state, not a second copy",
              {t["tier"] for t in tiers if not t["available"]}
              == set(accounts.POOLED), repr(tiers))

        # 2.12 the PRIMARY row is untouched: it reports real usage, so a
        #      standing table there would be a second answer to one question
        import orgtree.limits as _limits
        real = _limits.fetch
        _limits.fetch = lambda *a, **k: {"available": True, "limits": []}   # type: ignore[assignment]
        try:
            p = accounts.account_usage(accounts.PRIMARY)
        finally:
            _limits.fetch = real                            # type: ignore[assignment]
        check("2.12 the primary row carries no standing table",
              "tiers" not in p, repr(p)[:200])
    finally:
        restore()


# --------------------------------------------------------------------------
def s3_no_duplicate() -> None:
    print("\n§3  duplicate-of-primary greying is gone — routing and payloads")
    restore = signed_in_as()
    try:
        # the row is the login's OWN account: exactly the case that used to be
        # excluded from routing and greyed in the panel
        seed(key_uuid=LIVE_UUID)
        now = time.time()

        # 3.1 the control FIRST: with capacity everywhere, opus is on primary,
        #     so 3.2's answer is not just "the only row there is"
        check("3.1 (control) with capacity everywhere opus is on the primary",
              accounts.resolve("opus")["account"] == accounts.PRIMARY,
              repr(accounts.resolve("opus")))
        accounts.record_limit(accounts.PRIMARY, "opus", now + HOUR)
        r = accounts.resolve("opus")
        check("3.2 a key matching the login is a routing lane like any other",
              r["account"] == KID and r["available"] is True, repr(r))

        rd = accounts.readout()
        check("3.3 the panel payload carries no `duplicate` flag",
              all("duplicate" not in k for k in rd["keys"]), repr(rd["keys"]))
        check("3.4 …and still carries the identity the panel does render",
              rd["keys"][0].get("account_uuid") == LIVE_UUID
              and rd["keys"][0].get("ordinal") == 1, repr(rd["keys"]))
        u = accounts.account_usage(KID)
        check("3.5 the usage payload carries no `duplicate` flag either",
              "duplicate" not in u, repr(u)[:200])
    finally:
        restore()


# --------------------------------------------------------------------------
def s4_controls() -> None:
    print("\n§4  controls — what would make the above vacuous")
    # 4.1 the stub is REAL: if `signed_in_as` did not take, every section ran
    #     against the developer's own login and §3 in particular proved
    #     nothing (a signed-out machine skips the primary lane entirely).
    restore = signed_in_as()
    try:
        check("4.1 live_identity is stubbed to the fixture login",
              accounts.live_identity()["uuid"] == LIVE_UUID,
              repr(accounts.live_identity()))
        # 4.2 …and signed OUT is a different world, which is what makes 4.1
        #     worth asserting rather than assuming
        restore()
        restore = signed_in_as(uuid="")
        seed()
        r = accounts.resolve("opus")
        check("4.2 with nobody signed in, the primary is not a lane at all",
              r["account"] == KID, repr(r))
    finally:
        restore()

    # 4.3 the fixture writes where the test reads — a suite pointed at a
    #     different data root would report a virgin registry forever
    check("4.3 the registry under test is the throwaway one",
          os.path.dirname(accounts.registry_path()) == _TMP,
          accounts.registry_path())

    # 4.4 POOLED is what the ruling said, and is a SUBSET of TIERS — a typo
    #     here ("sonnet4") would make every pool assertion above trivially
    #     true of a one-member pool
    check("4.4 the pool is exactly haiku/sonnet/opus, all real tiers",
          set(accounts.POOLED) == {"haiku", "sonnet", "opus"}
          and set(accounts.POOLED) <= set(accounts.TIERS),
          repr(accounts.POOLED))

    # 4.5 the standing view must not phone home. `account_usage` on a key row
    #     is a local read (D-147) and the new table did not change that.
    class Tripwire:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, *a: Any, **k: Any) -> Any:
            self.calls += 1
            raise AssertionError("the code under test made a network request")

    import urllib.request as ur
    tw, real = Tripwire(), ur.urlopen
    ur.urlopen = tw                                         # type: ignore[assignment]
    restore = signed_in_as()
    try:
        seed()
        accounts.record_limit(KID, "sonnet", time.time() + HOUR)
        accounts.account_usage(KID)
        accounts.tier_standing(accounts.load(), KID)
        check("4.5 building the standing table makes no network call",
              tw.calls == 0, f"{tw.calls} call(s)")
        try:
            ur.urlopen("https://example.invalid/")          # the tripwire works
        except AssertionError:
            pass
        check("4.6 …and the tripwire can fire (4.5 is not vacuous)",
              tw.calls == 1, f"{tw.calls} call(s)")
    finally:
        ur.urlopen = real                                   # type: ignore[assignment]
        restore()


def main() -> int:
    for fn in (s1_pool, s2_standing, s3_no_duplicate, s4_controls):
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
