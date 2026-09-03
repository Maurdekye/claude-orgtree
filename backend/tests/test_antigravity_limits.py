"""Antigravity usage standing: observed from the wire, never fetched.

    python backend/tests/test_antigravity_limits.py   (no pytest; asserts)

The Antigravity CLI has no usage readout orgtree can open headlessly
(measured 2026-09-03: the print-mode `/quota` `/usage` `/credits` commands
went to the model and answered with the wall itself), so the lane's
standing is the last wall a turn hit — the MEASURED specimen below — with
the reset parsed out of "Resets in 165h21m54s".  This suite pins the parse,
the standing's lifecycle (wall → expiry / clear), the board row and the
header-modal shape it produces, the D-209 freeze taking the parsed reset
as a PROVIDER reset (not the blind probe floor), persistence across a
backend restart, and the two API doors.

Hermetic: a throwaway ORGTREE_DATA, the fake CLI on ORGTREE_ANTIGRAVITY so
`antigravity_status()` reports installed + connected without touching the
real binary, no network.  Anti-vacuity: the specimen's parse is checked
against the literal 595314 s, the board row against the exact rendered
line, and the freeze source against the string "provider".
"""

import io
import json
import os
import sys
import tempfile
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-agylimits-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "fakeantigravity.py")
os.environ["ORGTREE_ANTIGRAVITY"] = FAKE
os.environ["ORGTREE_CODEX"] = os.path.join(DATA, "nowhere", "codex.exe")
os.environ["CODEX_HOME"] = os.path.join(DATA, "chome")
os.environ.pop("FAKEANTIGRAVITY_SIGNED_OUT", None)

from orgtree import antigravity_limits as A            # noqa: E402
from orgtree import providers, store, supervisor as S, turnusage  # noqa: E402
from orgtree.ledger import USER                        # noqa: E402

S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None

SPECIMEN = ("Individual quota reached. Please upgrade your subscription to "
            "increase your limits. Resets in 165h21m54s.")
SPECIMEN_SECS = 165 * 3600 + 21 * 60 + 54          # 595314
LEGACY = ("Quota exceeded for quota metric 'Generate requests' and limit "
          "'Generate requests per day'")

PASS = 0
FAIL: list[tuple[str, str]] = []


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


def iso(epoch: float) -> str:
    """The BOARD's stamp (seconds precision) — the module's own `_iso` keeps
    microseconds for the modal, and the two must be compared on the board's
    terms where the board is what is asserted."""
    return turnusage._iso(epoch)


def mkorg(label: str) -> tuple[str, str]:
    org = store.create_org(f"zz agylimits {label}")
    r = org.hire(USER, None, "pro", 2, "ag", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a standing test agent")
    store.save_org(org)
    return org.d["slug"], r["node"]


def main():
    print("§1 the reset parser")

    def t1():
        eq(A.reset_in_seconds(SPECIMEN), float(SPECIMEN_SECS),
           "the measured specimen, compact h/m/s run")
        eq(A.reset_in_seconds("Resets in 2m"), 120.0, "minutes only")
        eq(A.reset_in_seconds("resets in 45s"), 45.0, "seconds only")
        eq(A.reset_in_seconds("Resets in 1d2h"), 93600.0, "days+hours")
        eq(A.reset_in_seconds("Resets in 2 hours 5 minutes"), 7500.0,
           "worded units")
        eq(A.reset_in_seconds("Resets in 3 days, 2 hours and 1 minute."),
           266460.0, "worded with separators")
        eq(A.reset_in_seconds("resets in 0s"), None, "a zero is no reset")
        eq(A.reset_in_seconds("model gemini-165h resets soon"), None,
           "a unit-shaped token without the verb phrase is not a reset")
        eq(A.reset_in_seconds(LEGACY), None,
           "the metric wording names no reset")
        eq(A.reset_in_seconds(""), None, "empty")
        eq(A.reset_at(SPECIMEN, 1000.0), 1000.0 + SPECIMEN_SECS, "anchored")
    check("'Resets in 165h21m54s' parses to 595314 s; forms, zero and "
          "look-alikes handled", t1)

    print("§2 the standing: wall → board/modal/glow → expiry / clear")
    A.invalidate()
    now = time.time()

    def t2():
        eq(A.snapshot(now)["unsupported"], True, "nothing observed yet")
        ts = A.observe_wall(SPECIMEN, tier="pro", now=now)
        eq(ts, now + SPECIMEN_SECS, "observe returns the parsed reset")
        snap = A.snapshot(now + 60)
        eq((snap["available"], snap["unsupported"], snap["stale"]),
           (True, False, False), "a standing wall")
        eq(snap["observed_at"], A._iso(now), "observed stamp")
        eq(snap["age"], 60.0, "age against the caller's clock")
        lim = snap["limits"]
        eq(len(lim), 1, "one window")
        eq((lim[0]["kind"], lim[0]["percent"], lim[0]["severity"],
            lim[0]["is_active"], lim[0]["label"], lim[0]["resets_at"]),
           ("provider_window", 100.0, "critical", True, "individual quota",
            A._iso(now + SPECIMEN_SECS)), "the bar")
    check("a wall becomes one 100% critical window with the parsed reset", t2)

    def t2b():
        pk = A.peek()
        eq((pk["available"], pk["provider"], len(pk["limits"])),
           (True, "Antigravity", 1), "glow-ready")
        fx = A.fetch()
        eq((fx["account"], fx["provider"], fx["available"]),
           ("antigravity", "Antigravity", True), "modal section")
        eq(fx["label"], "fake-agy@example.test",
           "labelled by the signed-in account the connect probe named")
        eq(fx["limits"][0]["percent"], 100.0, "the same bar")
    check("peek and fetch serve the wall (fetch labelled by the account)", t2b)

    def t2c():
        eq(A.snapshot(now + SPECIMEN_SECS + 1)["available"], False,
           "past the reset the wall is presumed lifted")
        eq(A.snapshot(now + SPECIMEN_SECS - 1)["available"], True,
           "…and stands until then")
    check("a wall expires at its own reset", t2c)

    def t2d():
        A.observe_clear(now + 120)
        snap = A.snapshot(now + 121)
        eq((snap["available"], snap["unsupported"]), (False, True),
           "cleared: back to 'nothing to report'")
        fx = A.fetch()
        eq((fx["available"], fx.get("unsupported")), (False, True),
           "the modal's settled note, not an error")
        assert "last successful turn" in fx["error"], fx["error"]
        eq(A.peek()["available"], False, "no glow")
    check("a completed turn clears the wall; the modal notes the last "
          "success", t2d)

    def t2e():
        A.observe_wall(LEGACY, now=now)
        snap = A.snapshot(now + 10)
        eq((snap["available"], snap["stale"],
            snap["limits"][0]["resets_at"], snap["limits"][0]["label"]),
           (True, False, None, "generate requests per day"),
           "a wall naming no reset stands with resets_at None")
        eq(A.snapshot(now + A.MAX_EVIDENCE_AGE + 1)["stale"], True,
           "…and ages out as evidence")
        A.invalidate()
    check("a wall that names no reset stands, dated, then ages out", t2e)

    print("§3 the turn envelope's board row")
    slug, nid = mkorg("board")

    def t3():
        org = store.load_org(slug)
        A.invalidate()
        block = turnusage.render(org, nid, selected_provider="google", now=now)
        assert ("antigravity/account* | usage | unavailable(unsupported) | - "
                "| - | - | unsupported") in block, block
        A.observe_wall(SPECIMEN, now=now)
        block = turnusage.render(org, nid, selected_provider="google",
                                 now=now + 60)
        want = (f"antigravity/account* | provider_window | 100% | - | "
                f"{iso(now + SPECIMEN_SECS)} (+6d21h) | {iso(now)} "
                f"(60s,fresh) | limit-active")
        assert want in block, f"wanted {want!r} in\n{block}"
        assert "unavailable(unsupported)" not in [
            l for l in block.splitlines() if l.startswith("antigravity/")
        ][0], block
        block = turnusage.render(org, nid, now=now + SPECIMEN_SECS + 5)
        assert "antigravity/account | usage | unavailable(unsupported)" in block, \
            "past the reset the explicit unsupported row is back"
    check("the board shows the wall as 100%/limit-active with the reset, "
          "and the unsupported row otherwise", t3)

    print("§4 the D-209 freeze takes the parsed reset as a PROVIDER reset")

    def t4():
        ts = now + SPECIMEN_SECS
        eq(S._provider_limit_until(SPECIMEN, ts, now), (ts, "provider"),
           "a parsed reset inside the horizon is the provider's own")
        got_ts, src = S._provider_limit_until(SPECIMEN, None, now)
        eq((src, round(got_ts - now)), ("probe", int(S.PROBE_FLOOR)),
           "without the parse the freeze fell to the blind probe floor — "
           "the exact gap this closes")
        eq(S._provider_limit_until(SPECIMEN, now + 400 * 86400, now)[1],
           "probe", "a reset past the longest real lane is not believed")
    check("_provider_limit_until: parsed reset → 'provider'; none → probe; "
          "absurd → probe", t4)

    def t4b():
        ts = now + SPECIMEN_SECS
        ok = S.freeze_provider_limit(slug, nid, SPECIMEN, ts)
        eq(ok, True, "freeze written")
        fz = store.load_org(slug).d["nodes"][nid]["frozen"]
        eq((fz["limit"], fz["reset_src"], fz["until_ts"]),
           (True, "provider", ts), "the ordinary freeze record, provider-timed")
        assert fz["until"] and "probing" not in fz["until"], fz["until"]
        assert SPECIMEN[:40] in fz["error"], fz["error"]
        org = store.load_org(slug)
        block = turnusage.render(org, nid, selected_provider="google",
                                 now=now + 60)
        line = [l for l in block.splitlines()
                if l.startswith("antigravity/account*")][0]
        assert line.endswith("| frozen"), line
        assert iso(ts) in line, line
    check("freeze_provider_limit parks the node on the wall's own reset and "
          "the selected row reads frozen", t4b)

    print("§5 persistence across a backend restart")

    def t5():
        A.invalidate()
        A.observe_wall(SPECIMEN, tier="flash", now=now)
        path = A._path()
        assert os.path.exists(path), path
        # a restart: fresh module state, the file is all that remains
        A.forget_memory()           # a restart: only the file remains
        snap = A.snapshot(now + 5)
        eq((snap["available"], snap["limits"][0]["resets_at"]),
           (True, A._iso(now + SPECIMEN_SECS)), "the wall came back from disk")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        A.forget_memory()           # a restart: only the file remains
        eq(A.snapshot(now)["unsupported"], True,
           "a torn file loads as nothing, never raises")
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump({"wall": {"message": "x", "observed_at": "soon"}}, f)
        A.forget_memory()           # a restart: only the file remains
        eq(A.snapshot(now)["unsupported"], True,
           "a mis-shaped record is ignored")
        A.invalidate()
        assert not os.path.exists(path), "invalidate removes the file"
    check("the standing survives a restart and shrugs off a torn file", t5)

    print("§6 the modal's install / sign-in wording and the API doors")

    def t6():
        os.environ["FAKEANTIGRAVITY_SIGNED_OUT"] = "1"
        providers._antigravity_status_cache = None
        try:
            fx = A.fetch()
            eq((fx["available"], fx["error"]),
               (False, "Antigravity CLI is not signed in"), "signed out")
        finally:
            os.environ.pop("FAKEANTIGRAVITY_SIGNED_OUT", None)
            providers._antigravity_status_cache = None
        try:
            from fastapi.testclient import TestClient   # noqa: PLC0415
            from orgtree import api                     # noqa: PLC0415
        except Exception as e:                          # noqa: BLE001
            print(f"         (API doors skipped: {e.__class__.__name__})")
            return
        A.observe_wall(SPECIMEN, now=time.time())
        with TestClient(api.app) as c:
            r = c.get("/api/antigravity/usage")
            eq(r.status_code, 200, "usage door")
            body = r.json()
            eq((body["account"], body["provider"], body["available"]),
               ("antigravity", "Antigravity", True), "usage payload")
            eq(body["limits"][0]["percent"], 100.0, "the bar over the wire")
            r = c.get("/api/antigravity/usage/peek")
            eq(r.status_code, 200, "peek door")
            eq((r.json()["available"], r.json()["provider"]),
               (True, "Antigravity"), "peek payload")
        A.invalidate()
    check("a signed-out CLI reads as such; /api/antigravity/usage and /peek "
          "serve the standing", t6)

    print()
    if FAIL:
        print(f"{PASS} passed, {len(FAIL)} FAILED")
        for label, tb in FAIL:
            print(f"\n--- {label}\n{tb}")
        sys.exit(1)
    print(f"{PASS} checks passed")


if __name__ == "__main__":
    main()
