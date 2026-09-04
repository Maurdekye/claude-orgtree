"""A throwaway data root must never default to the operator's real mail hub.

THE HAZARD, WHICH HAS HAPPENED TWICE. A test rig mints its own ORGTREE_DATA
with `tempfile.mkdtemp`, creates fixture orgs in it, and - if that rig also
boots a backend - `net._participants` registers every one of them against
whatever `_default_address()` returns. A fresh data root has no defaults.json,
so that used to be `DEFAULT_HUB_ADDRESS`: the operator's REAL hub. The fixtures
landed in the live roster as selectable recipients that can never receive
anything. ~45 of them on 2026-08-06; again on 2026-08-10, after a fix that
covered three suites and missed a fourth which grew a live backend later.

Every repair before this one asked the RIG to remember to set
`net_hub_address`. THIS ONE DOES NOT, and that is the whole point - it is a
floor a rig author who has never heard of the hub cannot fall through.

`test_external_mail`'s hygiene assertion, which greps every rig for
`net_hub_address`, STAYS. It is the belt over this floor: it keeps naming the
rigs that rely on the default, so the day someone changes the default back the
list is already written. A red assertion that correctly detects a real hazard
is not noise (coordinator ruling 2026-09-04).

Falsifiers, all four verified to turn exactly their own group red:

M1 realpath -> abspath in `_under_os_temp`   -> group "spelling" FAILS
M2 drop the os.sep from the prefix compare   -> group "boundary" FAILS
M3 always return DEFAULT_HUB_ADDRESS         -> group "floor" FAILS
M4 ignore an explicit net_hub_address        -> group "explicit" FAILS

Hermetic: no org is created, no hub is contacted, nothing is registered. The
one check that reads the operator's real data root only asks where it is.

    python backend/tests/test_hub_default_floor.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

RIG = tempfile.mkdtemp(prefix="orgtree-hubfloor-")
os.environ["ORGTREE_DATA"] = os.path.join(RIG, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
os.environ["ORGTREE_PORT"] = "7418"             # never bound

from orgtree import net, store                              # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
NOTES: list[str] = []
TEMP_DATA = os.environ["ORGTREE_DATA"]


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                       # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def with_root(root: str, defaults: dict | None = None) -> str:
    """Resolve the default address as if DATA_ROOT were `root`."""
    saved = store.DATA_ROOT
    store.DATA_ROOT = root
    try:
        os.makedirs(root, exist_ok=True)
        p = os.path.join(root, "defaults.json")
        if defaults is None:
            try:
                os.remove(p)
            except OSError:
                pass
        else:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(defaults, f)
        return net._default_address()
    finally:
        store.DATA_ROOT = saved


def with_tempdir(fake: str, fn):
    """Run `fn` with the OS temp directory reported as `fake`."""
    saved = tempfile.gettempdir
    tempfile.gettempdir = lambda: fake
    try:
        return fn()
    finally:
        tempfile.gettempdir = saved


# ------------------------------------------------------------- group: floor
def t_temp_data_root_never_defaults_to_the_real_hub() -> None:
    """THE HEADLINE. No defaults.json, data root under the OS temp dir."""
    got = with_root(TEMP_DATA, defaults=None)
    assert got == net.UNROUTABLE_HUB_ADDRESS, (
        f"a throwaway data root under the OS temp dir resolved to {got!r}; "
        f"a rig that boots a backend would register its fixture orgs against "
        f"the operator's real hub")
    assert got != net.DEFAULT_HUB_ADDRESS, "the floor returned the real hub"


def t_a_normal_data_root_is_untouched() -> None:
    """The other half, and the one that matters for production: an install
    whose data root is NOT under temp still defaults to the real hub. If this
    fails, the fix has broken every real deployment's hub connection."""
    outside = os.path.join(RIG, "not-temp-data")
    got = with_tempdir(os.path.join(RIG, "elsewhere-temp"),
                       lambda: with_root(outside, defaults=None))
    assert got == net.DEFAULT_HUB_ADDRESS, (
        f"a data root outside the OS temp dir resolved to {got!r} instead of "
        f"the real hub - the floor is swallowing real installs")


# ---------------------------------------------------------- group: explicit
def t_an_explicit_address_always_wins() -> None:
    """The escape hatch, in both locations. The floor is only ever reached
    when defaults.json names no address, so an operator who deliberately
    points a temp-rooted install at a hub is never overridden."""
    inside = with_root(TEMP_DATA,
                       defaults={"net_hub_address": "http://10.0.0.9:1234"})
    assert inside == "http://10.0.0.9:1234", (
        f"an explicit net_hub_address was ignored under temp: {inside!r}")
    outside = os.path.join(RIG, "not-temp-data")
    got = with_tempdir(
        os.path.join(RIG, "elsewhere-temp"),
        lambda: with_root(outside,
                          defaults={"net_hub_address": "http://10.0.0.9:5"}))
    assert got == "http://10.0.0.9:5", (
        f"an explicit net_hub_address was ignored outside temp: {got!r}")


def t_an_empty_explicit_address_falls_through_to_the_floor() -> None:
    """`{"net_hub_address": ""}` is not a choice of hub - it is the absence of
    one, and it must not be read as an instruction to use the real hub."""
    got = with_root(TEMP_DATA, defaults={"net_hub_address": ""})
    assert got == net.UNROUTABLE_HUB_ADDRESS, (
        f"an empty address under temp resolved to {got!r}")


# ---------------------------------------------------------- group: spelling
def t_both_spellings_of_the_temp_dir_agree() -> None:
    """COORDINATOR CONDITION (2026-09-04): the resolved PATH decides, never
    the name. On Windows %TEMP% commonly arrives in 8.3 short form while a
    data root arrives spelled out; a string compare then says "not temp" for
    the same directory and fails OPEN, straight back to the real hub.

    Both spellings are derived from the running machine rather than
    hard-coded. If they coincide - no 8.3 aliasing, or not Windows - this
    check cannot detect the fault and SAYS SO instead of passing quietly."""
    raw = tempfile.gettempdir()
    resolved = os.path.realpath(raw)
    if os.path.normcase(raw) == os.path.normcase(resolved):
        NOTES.append(
            "spelling check INERT on this machine: tempfile.gettempdir() "
            f"already resolves to itself ({raw!r}), so there is no second "
            "spelling to disagree with. M1 is not falsifiable here.")
        return
    a = os.path.join(raw, "orgtree-spelling-probe")
    b = os.path.join(resolved, "orgtree-spelling-probe")
    assert net._under_os_temp(a) and net._under_os_temp(b), (
        f"the two spellings of the OS temp dir disagree: {a!r} -> "
        f"{net._under_os_temp(a)}, {b!r} -> {net._under_os_temp(b)}. "
        f"A path compare that does not resolve fails OPEN, which sends "
        f"fixture orgs to the real hub.")
    NOTES.append(f"spelling check LIVE: {raw!r} vs {resolved!r}")


# ---------------------------------------------------------- group: boundary
def t_the_temp_dir_itself_counts() -> None:
    assert net._under_os_temp(tempfile.gettempdir()), (
        "the OS temp directory itself is not recognised as being under itself")


def t_a_sibling_with_a_shared_PREFIX_does_not_count() -> None:
    """`<temp>-sibling` starts with the temp path as a STRING but is a
    different directory. Without the separator in the compare, a real install
    at a path that merely begins with the temp prefix would be silently cut
    off from its hub."""
    sib = tempfile.gettempdir() + "-sibling"
    assert not net._under_os_temp(sib), (
        f"{sib!r} was treated as being under the OS temp dir - the prefix "
        f"compare is missing its path separator")


def t_a_nonexistent_path_still_resolves() -> None:
    """The data root may not exist yet on a first run. realpath does not
    require it to, and the answer must not depend on that."""
    ghost = os.path.join(tempfile.gettempdir(), "orgtree-does-not-exist-xyz")
    assert net._under_os_temp(ghost), (
        "a not-yet-created path under temp was not recognised")


# ---------------------------------------------------------- group: callers
def t_a_new_org_is_born_pointing_at_the_floored_address() -> None:
    """THE CHECK THAT WAS MISSING, and its absence is the lesson.

    The first version of this suite tested `_default_address()` and stopped
    there. `orgs_create` held a SECOND implementation of the same question -
    `dflt.pop("net_hub_address") or net.DEFAULT_HUB_ADDRESS` - which never
    consulted the floor. So in a throwaway root `_default_address()` answered
    the discard port while an org created through the endpoint came out
    holding the operator's REAL hub. Measured, not reasoned: the live hub's
    own log showed a request arriving from a test run.

    A floor nothing stands on is not a floor. Test the CALLER, not only the
    helper."""
    from orgtree import api
    out = api.orgs_create(api.OrgCreate(name="floor caller probe"))
    try:
        hubs = store.load_org(out["slug"]).d.get("net_hubs") or []
        addrs = [h.get("address") for h in hubs]
        assert net.DEFAULT_HUB_ADDRESS not in addrs, (
            f"an org created in a THROWAWAY data root was born pointing at "
            f"the operator's real hub: {addrs}")
        assert addrs == [net.UNROUTABLE_HUB_ADDRESS], addrs
    finally:
        store.delete_org(out["slug"])


def t_an_explicit_default_still_reaches_a_new_org() -> None:
    """...and the operator's own choice is not swallowed by the floor."""
    from orgtree import api
    p = os.path.join(store.DATA_ROOT, "defaults.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"net_hub_address": "http://10.0.0.9:7370"}, f)
    try:
        out = api.orgs_create(api.OrgCreate(name="floor explicit probe"))
        try:
            hubs = store.load_org(out["slug"]).d.get("net_hubs") or []
            assert [h.get("address") for h in hubs] == [
                "http://10.0.0.9:7370"], hubs
        finally:
            store.delete_org(out["slug"])
    finally:
        os.remove(p)


# ----------------------------------------------------------- group: reality
def t_this_machines_real_data_root_is_not_under_temp() -> None:
    """The floor is only safe if the operator's real install is on the other
    side of it. Read-only: this asks where the data root is, nothing else."""
    real = os.environ.get("_ORGTREE_REAL_DATA") or os.path.expanduser(
        "~/orgtree")
    assert not net._under_os_temp(real), (
        f"this machine's real data root {real!r} IS under the OS temp dir, so "
        f"the floor would cut the live install off from its hub. That install "
        f"is also one temp cleanup away from losing its orgs.")


def main() -> int:
    print("group floor: a throwaway root never reaches the real hub")
    check("a temp data root with no defaults.json gets a dead address",
          t_temp_data_root_never_defaults_to_the_real_hub)
    check("a normal data root still gets the real hub (production intact)",
          t_a_normal_data_root_is_untouched)
    print("group explicit: a named address always wins")
    check("an explicit net_hub_address is honoured in both locations",
          t_an_explicit_address_always_wins)
    check("an EMPTY address is an absence, not a choice of the real hub",
          t_an_empty_explicit_address_falls_through_to_the_floor)
    print("group spelling: the resolved path decides, never the name")
    check("both spellings of the OS temp dir agree",
          t_both_spellings_of_the_temp_dir_agree)
    print("group boundary: where 'under temp' starts and stops")
    check("the temp directory itself counts", t_the_temp_dir_itself_counts)
    check("a sibling sharing the prefix does NOT count",
          t_a_sibling_with_a_shared_PREFIX_does_not_count)
    check("a path that does not exist yet still resolves",
          t_a_nonexistent_path_still_resolves)
    print("group callers: the floor is only real where it is used")
    check("a new org is born pointing at the floored address",
          t_a_new_org_is_born_pointing_at_the_floored_address)
    check("an explicit default still reaches a new org",
          t_an_explicit_default_still_reaches_a_new_org)
    print("group reality: this machine")
    check("the real data root is not under temp",
          t_this_machines_real_data_root_is_not_under_temp)

    print()
    for n in NOTES:
        print("  note: " + n)
    if FAIL:
        for label, tb in FAIL:
            print(f"\n[X] {label}\n{tb}")
        print(f"hub-default-floor: {PASS} passed - {len(FAIL)} FAILED")
        return 1
    print(f"hub-default-floor: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(RIG, ignore_errors=True)
    sys.exit(rc)
