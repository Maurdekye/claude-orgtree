"""A key row must not ASK for usage — the request is forbidden before it ships.

D-147. A `claude setup-token` key is inference-only; `/api/oauth/usage` needs
the `user:profile` scope it never carries. Claude Code itself checks the scope
and declines client-side (`if(!ds()||!KM())return{}` where `KM()` tests for
`A4e = "user:profile"`), and the server enforces the same rule with a 403. Our
old code asked anyway, on every panel open, once per key — which is how one row
earned an hour-long rate-limit window.

WHAT THIS ASSERTS, AND WHY IT COUNTS CALLS RATHER THAN READING THE MESSAGE
--------------------------------------------------------------------------
The visible change is wording; the DEFECT was a packet. A suite that checked
only the returned string would pass just as happily against a version that
still made the forbidden request and then described it nicely — the same trap
the 429 work hit. So the load-bearing assertions here are on how many times
the module reached for the network, with `urlopen` replaced by a counter that
FAILS if anything touches it.

    §1  a key row makes zero requests, and says why
    §2  the primary row still works — the fix is scoped to the token type
    §3  the controls: what would have to be true for §1 to be vacuous

    python backend/tests/test_key_usage_unsupported.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from typing import Any

_TMP = tempfile.mkdtemp(prefix="orgtree-keyusage-")
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

from orgtree import accounts, limits, tokens  # noqa: E402

FAILS: list[str] = []
CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"ok {CHECKS:3d}  {label}")
    else:
        print(f"FAIL      {label}" + (f"  — {detail}" if detail else ""))
        FAILS.append(label + (f" — {detail}" if detail else ""))


class Tripwire:
    """Stands in for `urlopen`. Any call at all is a failure — that is the
    whole point. Counts rather than raising so the count can be asserted."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *a: Any, **k: Any) -> Any:
        self.calls += 1
        raise AssertionError("the code under test made a network request")


def install(tripwire: Tripwire):
    import urllib.request as ur
    real = ur.urlopen
    ur.urlopen = tripwire                                  # type: ignore[assignment]
    return lambda: setattr(ur, "urlopen", real)


KID = "kTESTKEY0001"
UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def seed(uuid: str | None = UUID) -> None:
    doc = accounts.load()
    doc["keys"] = [{"id": KID, "account_uuid": uuid}]
    doc["key_liveness"] = {}
    accounts.save(doc)
    tokens.put(KID, "sk-ant-oat01-" + "x" * 80)


def s1_key_rows() -> None:
    print("\n§1  a key row makes ZERO requests, and says why")
    seed()
    tw = Tripwire()
    restore = install(tw)
    try:
        limits.invalidate()
        got = accounts.account_usage(KID)
        check("1.1 the key row made no network call whatsoever", tw.calls == 0,
              f"{tw.calls} call(s)")
        check("1.2 it reports unsupported, not merely unavailable",
              got.get("unsupported") is True and got.get("available") is False,
              repr(got))
        msg = str(got.get("error") or "")
        check("1.3 the message names setup-token as the reason",
              "setup-token" in msg, repr(msg))
        # ⚠ the wording that shipped 2026-08-25 and was WRONG. Scope is fixed
        # at mint, so telling the user to re-mint is a ritual that cannot help
        # and whose failure reads as "my key is broken". Never again.
        # ⚠ and the check is on the INSTRUCTION, not the substring: a first
        # draft banned "re-mint" outright and flagged the current message for
        # saying re-minting would NOT help — which is the very thing we want
        # it to say. Mentioning the dead end to rule it out is the fix, not
        # the defect.
        low = msg.lower()
        check("1.4 it does not INSTRUCT a re-mint",
              "re-mint it with" not in low and "re-mint the key" not in low,
              repr(msg))
        check("1.5 …and rules the re-mint out explicitly",
              "would not help" in low, repr(msg))
        check("1.6 it says nothing is wrong with the key",
              "nothing is wrong" in low, repr(msg))

        # 1.7 …and it stays free no matter how often the panel asks
        for _ in range(25):
            accounts.account_usage(KID)
        check("1.7 twenty-five more asks still make zero calls", tw.calls == 0,
              f"{tw.calls} call(s)")

        # 1.8 a row whose identity never resolved must ALSO not phone home —
        #     the old lazy `resolve_key_identity` retry lived on this path
        seed(uuid=None)
        accounts.account_usage(KID)
        check("1.8 an unresolved row does not trigger a profile lookup",
              tw.calls == 0, f"{tw.calls} call(s)")

        # 1.9 the header modal lists EVERY account. It is allowed exactly one
        #     upstream read — the primary's, which is legitimate — so the
        #     primary lane is stubbed and the tripwire then measures only the
        #     per-KEY calls. (A first draft asserted zero calls outright and
        #     tripped on the primary's own fetch, which is not the defect.)
        seed()
        real_fetch = limits.fetch
        limits.fetch = lambda *a, **k: {"available": True, "limits": []}   # type: ignore[assignment]
        try:
            accounts.usage_all()
        finally:
            limits.fetch = real_fetch                      # type: ignore[assignment]
        check("1.9 usage_all() makes no PER-KEY call", tw.calls == 0,
              f"{tw.calls} call(s)")

        # A rejected inference probe is a different fact from unsupported
        # usage telemetry. Do not show the ordinary "has capacity" table or
        # tell the operator nothing is wrong with a credential we excluded.
        doc = accounts.load()
        doc["key_liveness"] = {
            KID: {"state": "dead", "checked_at": 12345.0}}
        accounts.save(doc)
        rejected = accounts.account_usage(KID)
        rejected_msg = str(rejected.get("error") or "").lower()
        check("1.10 a rejected credential is not described as usable capacity",
              not rejected.get("tiers") and not rejected.get("unsupported"),
              repr(rejected))
        check("1.11 the rejection gives the exact safe recovery order",
              "not routed" in rejected_msg
              and "claude setup-token" in rejected_msg
              and "new fallback row" in rejected_msg
              and "only after" in rejected_msg,
              repr(rejected_msg))
        check("1.12 rejected-row rendering still makes no network call",
              tw.calls == 0, f"{tw.calls} call(s)")
    finally:
        restore()


def s2_primary_still_works() -> None:
    print("\n§2  the primary row is untouched — the fix is scoped to the token")
    seed()
    asked = {"n": 0}

    def fake_fetch(*_a: Any, **_k: Any) -> dict[str, Any]:
        asked["n"] += 1
        return {"available": True, "limits": [], "plan": "max"}

    real = limits.fetch
    limits.fetch = fake_fetch                              # type: ignore[assignment]
    try:
        got = accounts.account_usage(accounts.PRIMARY)
        check("2.1 primary still reads the host subscription", asked["n"] == 1,
              f"{asked['n']} fetch(es)")
        check("2.2 primary is available and NOT flagged unsupported",
              got.get("available") is True and not got.get("unsupported"),
              repr(got))
    finally:
        limits.fetch = real                                # type: ignore[assignment]


def s3_controls() -> None:
    print("\n§3  controls — what would make §1 vacuous")
    # ⚠ 3.1 proves the TRIPWIRE CAN FIRE. Without it, "zero calls" is equally
    # explained by a tripwire that was never installed, or installed on the
    # wrong module — and a §1 that cannot fail proves nothing at all.
    tw = Tripwire()
    restore = install(tw)
    try:
        import urllib.request
        try:
            urllib.request.urlopen("https://example.invalid/")
        except AssertionError:
            pass
        check("3.1 the tripwire fires on a real urlopen (it can fail)",
              tw.calls == 1, f"{tw.calls} call(s)")
    finally:
        restore()

    # 3.2 …and that the fixture is a real key row, not a missing one — a
    #     nonexistent row also makes zero calls, for entirely other reasons
    seed()
    got = accounts.account_usage("kNOSUCHROW")
    check("3.2 an unknown row is reported as unknown, not as unsupported",
          got.get("error") == "no such key" and not got.get("unsupported"),
          repr(got))
    doc = accounts.load()
    check("3.3 the fixture row really is registered",
          any(k["id"] == KID for k in doc["keys"]), repr(doc.get("keys")))


def main() -> int:
    for fn in (s1_key_rows, s2_primary_still_works, s3_controls):
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
