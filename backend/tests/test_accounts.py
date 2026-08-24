"""Account registry suite — accounts.py, adversarially.

    The registry records WHO this install may bill and never HOW. No token
    ever reaches accounts.json; passive adoption never writes the credentials
    store; a pin that cannot apply is refused rather than silently dropped.

Run:  .venv/Scripts/python.exe backend/tests/test_accounts.py
      --only <sub>   run only sections whose name contains <sub>

No pytest (it is not installed here), no network, no model calls. Everything
runs against a throwaway ORGTREE_DATA under the system temp dir, and
`subproxy.CREDS` is redirected to a temp file — §0 asserts that redirection
took, because a suite that reached the developer's real credentials store
would be a far worse bug than anything it could find.

WHY THE CHECKS ARE SHAPED THE WAY THEY ARE
------------------------------------------
Two traps this repo has been caught by before, both of which apply here with
unusual force:

1. AN ASSERTION OF ABSENCE THAT THE FAIL-LOUD PATH ERASES. "assert no token
   in accounts.json" passes trivially when the guard refused the write — and
   passes equally if the guard did nothing and the write failed for an
   unrelated reason. So the guard is tested in BOTH directions: it must RAISE
   on credential-shaped input, and a clean doc must actually reach disk. One
   leg alone is an abstention.

2. A CHECK THAT DEPENDS ON AMBIENT ENVIRONMENT. §5 asserts on the RESOLVED
   value the code uses (`store.DATA_ROOT` via `registry_path()`), never on
   os.environ, and it MOVES the root at runtime to prove the path follows it.
"""
from __future__ import annotations

import builtins
import json
import os
import sys
import tempfile
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

# isolated data root BEFORE any orgtree import — store resolves ORGTREE_DATA
# at import time
_TMP = tempfile.mkdtemp(prefix="orgtree-accounts-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)

from orgtree import accounts, store, subproxy          # noqa: E402

# ⚠ redirect the credentials path BEFORE any adoption test runs. subproxy.CREDS
# points at the real ~/.claude/.credentials.json.
_REAL_CREDS = subproxy.CREDS
_FAKE_CREDS = os.path.join(_TMP, "fake-credentials.json")
subproxy.CREDS = _FAKE_CREDS

_ARGS = sys.argv[1:]
ONLY = (_ARGS[_ARGS.index("--only") + 1].lower()
        if "--only" in _ARGS and len(_ARGS) > _ARGS.index("--only") + 1 else "")
PASS = 0
FAIL: list[tuple[str, str]] = []
NOTES: list[str] = []
_SECTION = [""]


def section(name: str) -> bool:
    _SECTION[0] = name
    if ONLY and ONLY not in name.lower():
        return False
    print(f"\n{name}:")
    return True


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except BaseException:                              # noqa: BLE001
        FAIL.append((f"{_SECTION[0]} / {label}", traceback.format_exc()))
        print(f"  XX     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def eq(got, want, what="") -> None:
    assert got == want, f"{what}expected {want!r}, got {got!r}"


def raises(exc, fn, *, why=""):
    try:
        fn()
    except exc:
        return
    except BaseException as e:                         # noqa: BLE001
        raise AssertionError(
            f"expected {exc.__name__}, got {type(e).__name__}: {e}") from None
    raise AssertionError(f"expected {exc.__name__}, nothing raised. {why}")


def reset() -> None:
    try:
        os.remove(accounts.registry_path())
    except OSError:
        pass


# a realistic /api/oauth/profile response, including the credential-shaped
# fields a real one does NOT carry — so the boundary drop is actually exercised
def profile(uuid="11111111-2222-3333-4444-555555555555",
            org="99999999-8888-7777-6666-555555555555",
            email="someone@example.com"):
    return {
        "account": {"uuid": uuid, "email": email, "full_name": "A Person",
                    "has_claude_max": True, "has_claude_pro": False,
                    "created_at": "2026-01-30T13:57:12.516557Z"},
        "organization": {"uuid": org, "name": "A Person's Organization",
                         "rate_limit_tier": "default_claude_max_20x",
                         "organization_type": "claude_max"},
        "application": {"uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "name": "Claude Code", "slug": "claude-code"},
    }


FAKE_TOKEN = ("sk-ant-oat01-TESTfabricatedZZZZnotarealtokenQQQQ0000invented"
              "1111bySuite2222paddingXXXXyyyy33")


# ------------------------------------------------------------------ sections
def s0_isolation() -> None:
    if not section("§0 isolation — the suite cannot reach real state"):
        return
    check("subproxy.CREDS redirected away from the real store",
          lambda: eq(subproxy.CREDS, _FAKE_CREDS))
    check("the real credentials path is NOT the one under test", lambda: (
        None if os.path.abspath(_REAL_CREDS) != os.path.abspath(_FAKE_CREDS)
        else (_ for _ in ()).throw(AssertionError("CREDS redirect did not take"))))
    # ⚠ PYTHONPATH really does carry the MAIN tree's backend on this machine
    # (review 2026-08-24, proven by execution). The suite's sys.path.insert(0)
    # currently wins — but "currently wins by import order" is not a property,
    # it is a coincidence, and importing main's accounts.py while claiming to
    # test this branch is precisely the abstention that has shipped here
    # before. Assert on the RESOLVED module file.
    check("the accounts module under test is THIS worktree's", lambda: (
        None if os.path.abspath(accounts.__file__).startswith(
            os.path.abspath(os.path.join(_HERE, "..")))
        else (_ for _ in ()).throw(AssertionError(
            f"imported {accounts.__file__} — not this tree's backend"))))
    check("registry lives under the throwaway data root", lambda: (
        None if accounts.registry_path().startswith(store.DATA_ROOT)
        else (_ for _ in ()).throw(AssertionError(
            f"{accounts.registry_path()} escaped {store.DATA_ROOT}"))))
    # ⚠ THIS CHECK WAS A TAUTOLOGY (review 2026-08-24). It read
    #     `None if not os.path.exists(_FAKE_CREDS) or True else None`
    # and `X or True` is True for every X, so it returned None unconditionally
    # and COULD NOT FAIL — sitting in the one section whose whole job is
    # proving the suite cannot reach the user's real credentials store. It was
    # load-bearing for a safety property and asserted nothing.
    #
    # The replacement actually watches: `adopt_live` is driven with the real
    # path restored, and must open the file `subproxy.CREDS` names. Then the
    # DELIBERATE POSITIVE below proves this check can still die.
    def adoption_reads_the_configured_path_only():
        opened: list[str] = []
        real_open = builtins.open

        def watching_open(f, *a, **k):
            try:
                opened.append(os.path.abspath(os.fspath(f)))
            except TypeError:
                pass
            return real_open(f, *a, **k)
        with open(_FAKE_CREDS, "w", encoding="utf-8") as fh:
            json.dump({"claudeAiOauth": {"accessToken": FAKE_TOKEN}}, fh)
        builtins.open = watching_open
        try:
            accounts.adopt_live(resolver=lambda t: profile())
        finally:
            builtins.open = real_open
        real = os.path.abspath(_REAL_CREDS)
        assert real not in opened, f"the suite opened the REAL store: {real}"
        assert os.path.abspath(_FAKE_CREDS) in opened, \
            "adoption never opened the configured credentials path — this " \
            "check would pass for a no-op implementation"
    check("adoption opens the configured store and never the real one",
          adoption_reads_the_configured_path_only)

    # the deliberate positive: point subproxy.CREDS AT the path the check
    # forbids and confirm the check above FAILS. Without this, a rewritten
    # check that silently stopped watching would look exactly like a pass.
    def the_guard_can_still_fail():
        saved = subproxy.CREDS
        decoy = os.path.join(_TMP, "decoy-real-credentials.json")
        with open(decoy, "w", encoding="utf-8") as fh:
            json.dump({"claudeAiOauth": {"accessToken": FAKE_TOKEN}}, fh)
        global _REAL_CREDS
        saved_real = _REAL_CREDS
        subproxy.CREDS = decoy
        _REAL_CREDS = decoy                     # now "the real store" IS the target
        try:
            raises(AssertionError, adoption_reads_the_configured_path_only,
                   why="the isolation check cannot detect reading the "
                       "forbidden path — it is not watching")
        finally:
            subproxy.CREDS = saved
            _REAL_CREDS = saved_real
    check("CONTROL: that guard FAILS when the forbidden path is read",
          the_guard_can_still_fail)


def s1_secret_invariant() -> None:
    if not section("§1 the invariant — identity in, credentials never"):
        return
    reset()

    # --- leg A: the guard must FIRE. Each of these is a distinct route in.
    check("refuses a credential-shaped VALUE", lambda: raises(
        accounts.SecretInRegistry,
        lambda: accounts.save({**accounts._blank(),
                               "accounts": {"u": {"note": FAKE_TOKEN}}})))
    check("refuses a credential-shaped KEY even with a harmless value",
          lambda: raises(accounts.SecretInRegistry,
                         lambda: accounts.save({**accounts._blank(),
                                                "accounts": {"u": {"accessToken": "x"}}})))
    check("refuses refreshToken key", lambda: raises(
        accounts.SecretInRegistry,
        lambda: accounts.save({**accounts._blank(),
                               "accounts": {"u": {"refreshToken": "x"}}})))
    check("refuses a secret nested inside a LIST", lambda: raises(
        accounts.SecretInRegistry,
        lambda: accounts.save({**accounts._blank(),
                               "accounts": {"u": {"hist": ["fine", FAKE_TOKEN]}}})))
    check("refuses a long opaque run with no sk-ant prefix", lambda: raises(
        accounts.SecretInRegistry,
        lambda: accounts.save({**accounts._blank(),
                               "accounts": {"u": {"x": "Ab3" + "Zq7Kd2" * 9}}})))

    # --- leg B: the guard must ABSTAIN, and a clean doc must really land.
    # Without this leg a guard that refused EVERYTHING would pass leg A.
    def clean_writes():
        reset()
        accounts.upsert(accounts.identity_from_profile(profile()))
        on_disk = json.load(open(accounts.registry_path(), encoding="utf-8"))
        assert on_disk["accounts"], "clean doc did not reach disk"
    check("a clean identity DOES write (guard is not refusing everything)",
          clean_writes)

    # --- leg C: the refusal must not damage what was already there.
    def refusal_is_atomic():
        reset()
        accounts.upsert(accounts.identity_from_profile(profile()))
        before = open(accounts.registry_path(), encoding="utf-8").read()
        try:
            accounts.save({**accounts.load(),
                           "accounts": {"u": {"t": FAKE_TOKEN}}})
        except accounts.SecretInRegistry:
            pass
        after = open(accounts.registry_path(), encoding="utf-8").read()
        eq(after, before, "a refused write modified the registry: ")
    check("a refused write leaves the existing registry byte-identical",
          refusal_is_atomic)

    # --- leg D: the real boundary — a profile carrying a token yields none.
    def boundary_drops_tokens():
        reset()
        p = profile()
        p["account"]["accessToken"] = FAKE_TOKEN         # hostile input
        p["oauth"] = {"refresh_token": FAKE_TOKEN}
        ident = accounts.identity_from_profile(p)
        accounts.upsert(ident)                           # must not raise
        raw = open(accounts.registry_path(), encoding="utf-8").read()
        assert "sk-ant" not in raw, "token text reached the registry"
        assert FAKE_TOKEN[20:40] not in raw, "token fragment reached the registry"
    check("identity_from_profile drops token fields at the boundary",
          boundary_drops_tokens)


def s2_registry() -> None:
    if not section("§2 the record — adoption must not clobber the user"):
        return
    reset()
    a = "11111111-2222-3333-4444-555555555555"
    b = "aaaaaaaa-2222-3333-4444-555555555555"

    check("upsert records and defaults a label", lambda: (
        eq(accounts.upsert(accounts.identity_from_profile(profile()))["label"],
           "s*****e@example.com")))
    check("second account appends LAST in the waterfall order", lambda: (
        accounts.upsert(accounts.identity_from_profile(
            profile(uuid=b, email="other@example.com"))),
        eq(accounts.load()["order"], [a, b]))[-1])

    def relabel_survives_readoption():
        accounts.upsert(accounts.identity_from_profile(profile()),
                        label="Primary (work)")
        accounts.set_order([b, a])                       # user reorders
        accounts.upsert(accounts.identity_from_profile(profile()))  # re-adopt
        doc = accounts.load()
        eq(doc["accounts"][a]["label"], "Primary (work)",
           "re-adoption clobbered a hand-set label: ")
        eq(doc["order"], [b, a], "re-adoption reordered the waterfall: ")
    check("re-adoption preserves hand-set label AND order",
          relabel_survives_readoption)

    # ⚠ The check above re-adopts the account that is ALREADY LAST, so a
    # remove-then-append bug is invisible to it — [b,a] goes to [b,a] either
    # way. Mutation round 2026-08-24 caught exactly that. Re-adopt the PRIMARY
    # instead: that is also the case that matters in production, where passive
    # adoption runs on a schedule and must never demote the user's primary.
    def readoption_cannot_demote_the_primary():
        accounts.set_order([b, a])
        eq(accounts.primary(), b, "precondition: ")
        accounts.upsert(accounts.identity_from_profile(
            profile(uuid=b, email="other@example.com")))     # re-adopt PRIMARY
        eq(accounts.primary(), b,
           "re-adopting the primary demoted it out of first place: ")
        eq(accounts.load()["order"], [b, a], "order churned: ")
    check("re-adopting the PRIMARY does not demote it",
          readoption_cannot_demote_the_primary)

    def first_seen_is_stable():
        doc = accounts.load()
        first = doc["accounts"][a]["first_seen"]
        time.sleep(0.01)
        accounts.upsert(accounts.identity_from_profile(profile()))
        doc2 = accounts.load()
        eq(doc2["accounts"][a]["first_seen"], first, "first_seen moved: ")
        assert doc2["accounts"][a]["last_seen"] > first - 1, "last_seen not updated"
    check("first_seen is stable across re-adoption, last_seen advances",
          first_seen_is_stable)

    check("a corrupt registry reads as blank rather than raising", lambda: (
        open(accounts.registry_path(), "w", encoding="utf-8").write("{not json"),
        eq(accounts.load()["accounts"], {}))[-1])


def s3_passive_adoption() -> None:
    if not section("§3 passive adoption — notice, and change nothing"):
        return
    reset()

    def write_creds(tok=FAKE_TOKEN):
        with open(_FAKE_CREDS, "w", encoding="utf-8") as f:
            json.dump({"claudeAiOauth": {"accessToken": tok,
                                         "refreshToken": tok,
                                         "expiresAt": 1_787_618_187_000}}, f)

    def adopts_without_touching_the_store():
        write_creds()
        # sampled by the TEST, independently of the module's own guard —
        # asserting with the thing under test would prove nothing
        before = os.stat(_FAKE_CREDS)
        rec = accounts.adopt_live(resolver=lambda t: profile())
        after = os.stat(_FAKE_CREDS)
        assert rec is not None, "adoption returned nothing"
        eq(rec["uuid"], "11111111-2222-3333-4444-555555555555")
        eq((after.st_mtime_ns, after.st_size),
           (before.st_mtime_ns, before.st_size),
           "passive adoption modified the credentials store: ")
    check("adopts the live identity and leaves the store untouched",
          adopts_without_touching_the_store)

    # THE DISCRIMINATING LEG: prove the guard fires. Without this, the check
    # above passes whether the guard works or is absent entirely.
    def guard_fires_when_store_is_written():
        write_creds()

        def hostile(_tok):
            time.sleep(0.01)
            with open(_FAKE_CREDS, "a", encoding="utf-8") as f:
                f.write(" ")                             # someone re-logged in
            return profile()
        raises(accounts.LiveStoreWritten,
               lambda: accounts.adopt_live(resolver=hostile),
               why="the store was written during adoption and nothing noticed")
    check("RAISES if the credentials store changes mid-adoption",
          guard_fires_when_store_is_written)

    # ⚠ The hostile resolver above APPENDS, so it changes the file's SIZE — a
    # guard comparing size alone would still catch it and the check could not
    # tell the two implementations apart. Mutation round 2026-08-24 confirmed
    # the size-only mutant survived. A re-login rewrites the record IN PLACE,
    # and a rotated token is the same length as the one it replaced, so
    # same-size is the REALISTIC shape of this event, not the exotic one.
    def guard_fires_on_a_same_SIZE_rewrite():
        write_creds()
        original = open(_FAKE_CREDS, encoding="utf-8").read()

        def rewriter(_tok):
            time.sleep(0.02)
            with open(_FAKE_CREDS, "w", encoding="utf-8") as f:
                f.write(original)                        # identical bytes, new mtime
            return profile()
        size_before = os.path.getsize(_FAKE_CREDS)
        raises(accounts.LiveStoreWritten,
               lambda: accounts.adopt_live(resolver=rewriter),
               why="an in-place, same-size rewrite went unnoticed")
        eq(os.path.getsize(_FAKE_CREDS), size_before,
           "precondition: the rewrite must not change size, or this check "
           "cannot distinguish an mtime guard from a size guard: ")
    check("RAISES on a same-SIZE in-place rewrite (mtime, not just size)",
          guard_fires_on_a_same_SIZE_rewrite)

    check("no credentials file → None, not an exception", lambda: (
        os.remove(_FAKE_CREDS),
        eq(accounts.adopt_live(resolver=lambda t: profile()), None))[-1])

    def offline_is_none_not_a_false_alarm():
        write_creds()
        def boom(_tok):
            raise OSError("offline")
        eq(accounts.adopt_live(resolver=boom), None)
    check("resolver failure → None (and no spurious LiveStoreWritten)",
          offline_is_none_not_a_false_alarm)

    def no_token_is_none():
        with open(_FAKE_CREDS, "w", encoding="utf-8") as f:
            json.dump({"claudeAiOauth": {}}, f)
        eq(accounts.adopt_live(resolver=lambda t: profile()), None)
    check("credentials present but tokenless → None", no_token_is_none)

    def resolver_receives_the_live_token():
        write_creds(tok=FAKE_TOKEN)
        seen = []
        accounts.adopt_live(resolver=lambda t: (seen.append(t), profile())[1])
        eq(seen, [FAKE_TOKEN], "resolver got the wrong token: ")
    check("the live token is what gets resolved", resolver_receives_the_live_token)


def s4_pin() -> None:
    if not section("§4 the pin — refuse loudly rather than not apply"):
        return
    reset()
    a = "11111111-2222-3333-4444-555555555555"
    accounts.upsert(accounts.identity_from_profile(profile()))

    check("pin round-trips", lambda: (
        accounts.set_pin("acme", a), eq(accounts.get_pin("acme"), a))[-1])

    def unknown_is_refused_and_leaves_the_old_pin():
        raises(KeyError, lambda: accounts.set_pin("acme", "not-an-account"),
               why="pinning an unknown account silently did nothing")
        eq(accounts.get_pin("acme"), a,
           "a refused pin damaged the existing pin: ")
    check("pinning an unknown account raises AND preserves the old pin",
          unknown_is_refused_and_leaves_the_old_pin)

    check("clearing a pin", lambda: (
        accounts.set_pin("acme", None), eq(accounts.get_pin("acme"), None))[-1])
    check("unpinned org reads None", lambda: eq(accounts.get_pin("nobody"), None))

    def order_cannot_delete_an_account():
        b = "aaaaaaaa-2222-3333-4444-555555555555"
        accounts.upsert(accounts.identity_from_profile(
            profile(uuid=b, email="other@example.com")))
        got = accounts.set_order([b])                    # stale panel omits `a`
        assert a in got, "an omitted account was dropped from the registry"
        eq(got[0], b, "requested primary did not take: ")
    check("set_order cannot delete an omitted account",
          order_cannot_delete_an_account)
    check("set_order ignores unknown uuids", lambda: (
        None if "ghost" not in accounts.set_order(["ghost"]) else
        (_ for _ in ()).throw(AssertionError("unknown uuid entered the order"))))


def s5_resolution() -> None:
    if not section("§5 resolution — the path follows the RESOLVED data root"):
        return
    # asserts on store.DATA_ROOT (what the code uses), never os.environ, and
    # moves it at runtime so a path frozen at import time fails here
    original = store.DATA_ROOT
    moved = os.path.join(_TMP, "moved-root")
    os.makedirs(moved, exist_ok=True)
    try:
        store.DATA_ROOT = moved
        check("registry_path() tracks a runtime change of store.DATA_ROOT",
              lambda: eq(accounts.registry_path(),
                         os.path.join(moved, accounts.REGISTRY_NAME)))

        def writes_land_in_the_moved_root():
            accounts.upsert(accounts.identity_from_profile(profile()))
            assert os.path.exists(os.path.join(moved, accounts.REGISTRY_NAME)), \
                "write did not land in the moved root"
        check("a write lands under the moved root", writes_land_in_the_moved_root)
    finally:
        store.DATA_ROOT = original
    check("data root restored after the section",
          lambda: eq(store.DATA_ROOT, original))


def s6_readout() -> None:
    if not section("§6 the readout — Phase 1 must not imply failover works"):
        return
    reset()
    accounts.upsert(accounts.identity_from_profile(profile()))
    r = accounts.readout()
    check("readout lists accounts in waterfall order",
          lambda: eq(len(r["accounts"]), 1))
    check("readout names a primary", lambda: (
        None if r["primary"] else (_ for _ in ()).throw(
            AssertionError("no primary in readout"))))
    # D-144: the panel must not be able to imply selection exists
    check("readout declares selection_active FALSE (D-144)",
          lambda: eq(r["selection_active"], False))
    # the panel's `ago()` parses STRINGS; an epoch number renders as "NaN ago"
    # and nothing in TypeScript would catch it at runtime. Assert the wire
    # shape, and that it actually parses as a date rather than merely being
    # a string — "0" is a string too.
    def readout_timestamps_are_iso():
        import datetime as dt
        for field in ("first_seen", "last_seen"):
            v = r["accounts"][0][field]
            assert isinstance(v, str), f"{field} is {type(v).__name__}, not ISO str"
            parsed = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            assert parsed.year >= 2020, f"{field} parsed to {parsed!r}"
    check("readout emits ISO-8601 timestamps, not epoch numbers",
          readout_timestamps_are_iso)
    # …while the on-disk record keeps epoch, which is what the stability
    # checks in §2 compare
    check("the stored record still keeps epoch numbers", lambda: (
        None if isinstance(accounts.load()["accounts"][
            list(accounts.load()["accounts"])[0]]["first_seen"], (int, float))
        else (_ for _ in ()).throw(AssertionError("record lost its epoch"))))
    check("readout carries no token material", lambda: (
        None if "sk-ant" not in json.dumps(r) else (_ for _ in ()).throw(
            AssertionError("token text in readout"))))


def s8_defects() -> None:
    """Three defects found by review 2026-08-24 — each check fails on the
    pre-fix code, which is what makes it a regression test rather than a
    description."""
    if not section("§8 review findings — data loss, dedupe, and the lock"):
        return

    # ---- 1g/iii: load()-blank-then-save destroyed the registry ----
    def corrupt_registry_is_not_silently_replaced():
        reset()
        accounts.upsert(accounts.identity_from_profile(profile()),
                        label="Precious")
        accounts.set_pin("acme", "11111111-2222-3333-4444-555555555555")
        before = open(accounts.registry_path(), encoding="utf-8").read()
        with open(accounts.registry_path(), "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        # a WRITE cycle must refuse rather than blank it
        raises(accounts.RegistryUnreadable,
               lambda: accounts.upsert(accounts.identity_from_profile(profile())),
               why="a corrupt registry was silently replaced with a blank one")
        # and the unreadable file must still be on disk, recoverable by hand
        assert open(accounts.registry_path(), encoding="utf-8").read() \
            == "{ this is not json", "the unreadable registry was overwritten"
        assert "Precious" in before   # (what would have been lost)
    check("a corrupt registry is NOT blanked by the next write",
          corrupt_registry_is_not_silently_replaced)

    def version_mismatch_refuses_too():
        reset()
        accounts.upsert(accounts.identity_from_profile(profile()))
        doc = json.load(open(accounts.registry_path(), encoding="utf-8"))
        doc["version"] = accounts.VERSION + 1          # a future install wrote this
        with open(accounts.registry_path(), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        raises(accounts.RegistryUnreadable,
               lambda: accounts.set_pin("acme", "x"),
               why="a VERSION bump would blank every install's registry")
    check("a future VERSION refuses the write instead of blanking it",
          version_mismatch_refuses_too)

    def readers_still_tolerate_corruption():
        # the other half: the PANEL must not go down over it
        eq(accounts.load()["accounts"], {}, "non-strict read should be blank: ")
        eq(accounts.readout()["accounts"], [])
    check("readers still degrade to blank (the panel stays up)",
          readers_still_tolerate_corruption)

    # ---- 1g/i: set_order did not dedupe ----
    def order_stays_a_permutation():
        reset()
        a = "11111111-2222-3333-4444-555555555555"
        b = "aaaaaaaa-2222-3333-4444-555555555555"
        accounts.upsert(accounts.identity_from_profile(profile()))
        accounts.upsert(accounts.identity_from_profile(
            profile(uuid=b, email="other@example.com")))
        got = accounts.set_order([a, a, b])            # double-submitted panel
        eq(got, [a, b], "order kept a duplicate: ")
        uuids = [x["uuid"] for x in accounts.readout()["accounts"]]
        eq(len(uuids), len(set(uuids)), "readout rendered an account twice: ")
    check("set_order dedupes — the order stays a permutation",
          order_stays_a_permutation)

    # ---- reviewer 2026-08-24, gap 1: set_order's strict load had no guard.
    # Reverting `strict=True` at THIS call site survived all 62 checks: the
    # future-version file loads as blank, `known` is empty, and save() then
    # replaces every label, the whole order and every pin with an empty
    # registry — from the panel's single most-used endpoint.
    def set_order_refuses_a_future_version():
        reset()
        a = "11111111-2222-3333-4444-555555555555"
        accounts.upsert(accounts.identity_from_profile(profile()),
                        label="Precious")
        accounts.set_pin("acme", a)
        doc = json.load(open(accounts.registry_path(), encoding="utf-8"))
        doc["version"] = accounts.VERSION + 1          # a future install wrote this
        with open(accounts.registry_path(), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        before = open(accounts.registry_path(), encoding="utf-8").read()
        raises(accounts.RegistryUnreadable,
               lambda: accounts.set_order([a]),
               why="one PUT /api/accounts/order blanked a future-version registry")
        after = open(accounts.registry_path(), encoding="utf-8").read()
        eq(after, before, "set_order rewrote a registry it refused to load: ")
        assert "Precious" in after, "the hand-set label is gone"
    check("set_order refuses a future VERSION (one PUT cannot blank the store)",
          set_order_refuses_a_future_version)

    # ---- reviewer 2026-08-24, gap 2: dedupe direction was unpinned. With a
    # NON-adjacent duplicate, first-wins and last-wins disagree about who is
    # primary — a user-visible difference in a feature about who gets used
    # first. The permutation check above cannot see it: its input [a, a, b]
    # dedupes to [a, b] under either direction.
    def dedupe_keeps_the_first_occurrence():
        reset()
        a = "11111111-2222-3333-4444-555555555555"
        b = "aaaaaaaa-2222-3333-4444-555555555555"
        accounts.upsert(accounts.identity_from_profile(profile()))
        accounts.upsert(accounts.identity_from_profile(
            profile(uuid=b, email="other@example.com")))
        got = accounts.set_order([a, b, a])            # non-adjacent duplicate
        eq(got, [a, b], "dedupe is not first-wins: ")
        eq(accounts.primary(), a, "a trailing duplicate demoted the primary: ")
    check("dedupe keeps the FIRST occurrence — a duplicate cannot demote the primary",
          dedupe_keeps_the_first_occurrence)

    # ---- 1g/ii: relabel ran outside the module lock ----
    def relabel_takes_the_lock():
        import threading
        reset()
        a = "11111111-2222-3333-4444-555555555555"
        accounts.upsert(accounts.identity_from_profile(profile()))
        # hold the lock from another thread; a relabel that ignores it would
        # sail through, one that takes it must wait
        holder_in = threading.Event()
        release = threading.Event()
        done: list[bool] = []

        def holder():
            with accounts._lock:
                holder_in.set()
                release.wait(2.0)
        t = threading.Thread(target=holder, daemon=True)
        t.start()
        holder_in.wait(2.0)

        def relabeller():
            accounts.relabel(a, "Renamed")
            done.append(True)
        r = threading.Thread(target=relabeller, daemon=True)
        r.start()
        r.join(0.25)
        blocked = not done
        release.set()
        t.join(2.0)
        r.join(2.0)
        assert blocked, ("relabel completed while the registry lock was held "
                         "— it is not taking the lock")
        eq(accounts.load()["accounts"][a]["label"], "Renamed",
           "relabel did not apply after the lock was released: ")
    check("relabel serialises on the module lock", relabel_takes_the_lock)

    # ---- 1e/i: an all-lowercase secret reached disk through `label` ----
    def lowercase_secret_in_a_label_is_refused():
        reset()
        a = "11111111-2222-3333-4444-555555555555"
        accounts.upsert(accounts.identity_from_profile(profile()))
        # hex/base32-shaped: no uppercase, no separator. The old pattern
        # required BOTH an uppercase and a digit, so this was invisible.
        leak = "a" * 20 + "bcdef0123456789abcdef0123456789abcdef01"
        assert len(leak) >= 40 and leak.islower()
        raises(accounts.SecretInRegistry,
               lambda: accounts.relabel(a, leak),
               why="a 40+ char all-lowercase run reached the registry")
        raw = open(accounts.registry_path(), encoding="utf-8").read()
        assert leak[:20] not in raw, "the run reached disk anyway"
    check("a long all-lowercase run in a label is refused",
          lowercase_secret_in_a_label_is_refused)

    def ordinary_labels_still_work():
        # the other direction: the widened pattern must not refuse real labels
        a = "11111111-2222-3333-4444-555555555555"
        for good in ("Primary (work)", "personal account", "acct-2",
                     "a fairly long descriptive label with spaces in it"):
            accounts.relabel(a, good)
        eq(accounts.load()["accounts"][a]["label"],
           "a fairly long descriptive label with spaces in it")
    check("CONTROL: ordinary labels are still accepted",
          ordinary_labels_still_work)

    # ---- 1e/ii: the store DELETED mid-adoption 500'd instead of 409'ing ----
    def deleted_store_midadoption_is_LiveStoreWritten():
        with open(_FAKE_CREDS, "w", encoding="utf-8") as f:
            json.dump({"claudeAiOauth": {"accessToken": FAKE_TOKEN}}, f)

        def logout(_tok):
            os.remove(_FAKE_CREDS)             # a logout, mid-adoption
            return profile()
        raises(accounts.LiveStoreWritten,
               lambda: accounts.adopt_live(resolver=logout),
               why="deleting the store mid-adoption raised the wrong "
                   "exception, so the endpoint 500s instead of 409ing")
    check("the store DISAPPEARING mid-adoption raises LiveStoreWritten",
          deleted_store_midadoption_is_LiveStoreWritten)


def s7_http() -> None:
    if not section("§7 the HTTP surface — admin only, and refusals are loud"):
        return
    reset()
    import asyncio

    from orgtree import api

    class R:
        def __init__(self, status, body):
            self.status, self.raw = status, body

        @property
        def json(self):
            try:
                return json.loads(self.raw.decode() or "null")
            except ValueError:
                return None

    def call(app, method, path, body=None):
        """Hand-built ASGI scope — the same technique test_api_surface.py uses,
        so nothing normalises the path between here and the gateway."""
        payload = b"" if body is None else json.dumps(body).encode()
        hdrs = [(b"host", b"127.0.0.1:7402")]
        if payload:
            hdrs += [(b"content-type", b"application/json"),
                     (b"content-length", str(len(payload)).encode())]
        st, chunks = [0], []

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(msg):
            if msg["type"] == "http.response.start":
                st[0] = msg["status"]
            elif msg["type"] == "http.response.body":
                chunks.append(msg.get("body", b""))
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "http_version": "1.1", "method": method, "scheme": "http",
                 "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "headers": hdrs,
                 "client": ("127.0.0.1", 5555), "server": ("127.0.0.1", 7402)}
        asyncio.run(app(scope, receive, send))
        return R(st[0], b"".join(chunks))

    admin = api.app
    a = "11111111-2222-3333-4444-555555555555"
    accounts.upsert(accounts.identity_from_profile(profile()))

    check("GET /api/accounts returns the registry", lambda: eq(
        call(admin, "GET", "/api/accounts").json["accounts"][0]["uuid"], a))
    check("GET /api/accounts declares selection_active false (D-144)",
          lambda: eq(call(admin, "GET", "/api/accounts").json["selection_active"],
                     False))
    check("GET /api/accounts leaks no token text", lambda: (
        None if "sk-ant" not in call(admin, "GET", "/api/accounts").raw.decode()
        else (_ for _ in ()).throw(AssertionError("token text on the wire"))))

    def adopt_with_no_creds_is_200_not_500():
        try:
            os.remove(_FAKE_CREDS)
        except OSError:
            pass
        r = call(admin, "POST", "/api/accounts/adopt")
        eq(r.status, 200, "no credentials file must not be an error: ")
        eq(r.json["adopted"], None)
    check("POST adopt with no credentials → 200, adopted=null",
          adopt_with_no_creds_is_200_not_500)

    def pin_unknown_is_422():
        # ⚠ precondition (review 2026-08-24): this used to assert the pin was
        # None afterwards WITHOUT setting one first — it was None going in, so
        # the check could not tell a clean refusal from a refusal that wiped
        # the pins dict. Establish a real pin, then refuse over it.
        call(admin, "PUT", "/api/accounts/pins/acme", {"uuid": a})
        eq(accounts.get_pin("acme"), a, "precondition: ")
        r = call(admin, "PUT", "/api/accounts/pins/acme",
                 {"uuid": "not-an-account"})
        eq(r.status, 422, "an unapplied pin must be loud: ")
        eq(accounts.get_pin("acme"), a,
           "a refused pin damaged the pin that was already there: ")
        call(admin, "PUT", "/api/accounts/pins/acme", {"uuid": None})
    check("PUT pin to an unknown account → 422 and the old pin survives",
          pin_unknown_is_422)

    check("PUT pin then clear round-trips over HTTP", lambda: (
        eq(call(admin, "PUT", "/api/accounts/pins/acme", {"uuid": a}).status, 200),
        eq(accounts.get_pin("acme"), a),
        eq(call(admin, "PUT", "/api/accounts/pins/acme", {"uuid": None}).status, 200),
        eq(accounts.get_pin("acme"), None))[-1])

    check("PATCH relabels", lambda: (
        eq(call(admin, "PATCH", f"/api/accounts/{a}", {"label": "Work"}).status, 200),
        eq(accounts.load()["accounts"][a]["label"], "Work"))[-1])
    check("PATCH empty label → 422", lambda: eq(
        call(admin, "PATCH", f"/api/accounts/{a}", {"label": "  "}).status, 422))
    check("PATCH unknown account → 404", lambda: eq(
        call(admin, "PATCH", "/api/accounts/ghost", {"label": "x"}).status, 404))

    def order_over_http():
        b = "aaaaaaaa-2222-3333-4444-555555555555"
        accounts.upsert(accounts.identity_from_profile(
            profile(uuid=b, email="other@example.com")))
        r = call(admin, "PUT", "/api/accounts/order", {"order": [b]})
        eq(r.status, 200)
        eq(r.json["primary"], b, "requested primary did not take: ")
        assert any(x["uuid"] == a for x in r.json["accounts"]), \
            "an omitted account was dropped over HTTP"
    check("PUT order promotes without deleting the omitted account",
          order_over_http)

    # ---- the access boundary, with a CONTROL so the denial is not vacuous ----
    # A kiosk visitor reaching /api/accounts could choose whose subscription
    # pays for an org. Asserting only "visitor gets a non-200" would also pass
    # if the gateway were broken for EVERY path, so the control proves this
    # same visitor can still reach a route that is meant to be open.
    def kiosk_cannot_reach_the_registry():
        denied = api._public_denied("GET", "/api/accounts", "acme")
        assert denied is not None, "kiosk visitor was NOT denied /api/accounts"
        eq(denied[0], 403, "expected an explicit 403: ")
        # ⚠ these three must assert 403 SPECIFICALLY (review 2026-08-24).
        # `is not None` passed via the matrix's trailing catch-all 404 even
        # with the freeze deleted — for `/api/accounts/adopt`, `parts[2]` is
        # "accounts", never "orgs", so it 404s whether or not the rule exists.
        # 403 is reachable only through `frozen_config`, so only 403
        # constrains the line this check is named for.
        for path in ("/api/accounts/adopt", "/api/accounts/order",
                     "/api/accounts/pins/acme"):
            d = api._public_denied("POST", path, "acme")
            assert d is not None, f"kiosk visitor was not denied {path}"
            eq(d[0], 403, f"{path} was denied by the catch-all 404 rather "
                          f"than by the freeze — ")
    check("kiosk visitors are denied every /api/accounts route",
          kiosk_cannot_reach_the_registry)

    def the_denial_control():
        # same matrix, a route that must stay OPEN to that visitor — if this
        # also came back denied, the check above would prove nothing
        eq(api._public_denied("GET", "/api/orgs", "acme"), None,
           "control: /api/orgs GET should be open to a kiosk visitor: ")
        eq(api._public_denied("GET", "/api/orgs/acme/tree", "acme"), None,
           "control: the visitor's own org tree should be open: ")
    check("CONTROL: the same matrix still allows an open route",
          the_denial_control)


def main() -> None:
    t0 = time.perf_counter()
    print(f"data root: {store.DATA_ROOT}")
    print(f"credentials under test: {subproxy.CREDS}")
    for fn in (s0_isolation, s1_secret_invariant, s2_registry,
               s3_passive_adoption, s4_pin, s5_resolution, s6_readout,
               s7_http, s8_defects):
        fn()
    dt = time.perf_counter() - t0
    if NOTES:
        print("\nnotes:")
        for n in NOTES:
            print(f"  · {n}")
    print()
    if FAIL:
        print("=" * 72)
        for label, tb in FAIL:
            print(f"\nFAILED: {label}\n{tb}")
        print("=" * 72)
        print(f"{PASS} checks passed, {len(FAIL)} FAILED  ({dt:.1f}s)")
        sys.exit(1)
    print(f"ALL {PASS} CHECKS PASS  ({dt:.1f}s)")


if __name__ == "__main__":
    main()
