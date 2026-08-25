"""A 429 from the usage endpoint must STOP us asking — not just print nicer.

User report 2026-08-25:

    "when i try to query the usage limits for the secondary key, i get
     constant 429 errors."

Constant is what the code guaranteed. `limits.fetch_for_token` cached only
SUCCESS: a failure cached nothing, so the next click re-asked at full rate
while the upstream was explicitly saying wait. Measured that day against the
key that was failing, two back-to-back calls, no retries:

    HTTP 429 · Retry-After: 1032 · server: cloudflare
    {"error":{"type":"rate_limit_error","message":"Rate limited. …"}}

— the SAME 1032 both times, i.e. a fixed window with a real deadline, while
the host readout answered 200. So nothing was broken about the endpoint, the
network or the token; we were simply ignoring an instruction.

WHAT THIS SUITE ASSERTS, AND WHY IT COUNTS CALLS
------------------------------------------------
The user-visible symptom is a message, but the DEFECT is a packet. A suite
that asserted only on the error string would pass just as happily against a
version that hammered the endpoint with a prettier label on the result — the
exact shape of check this codebase has been bitten by before. So every
assertion here is on `transport.calls`, the number of times the module
actually reached for the network, and the message is checked second.

    §1  fetch_for_token — the key rows the user was clicking
    §2  fetch — the host path, which had the identical hole
    §3  the windows are independent (host vs key, key A vs key B)
    §4  the negative controls: what must NOT open a window

    python backend/tests/test_usage_cooldown.py
"""
from __future__ import annotations

import email.message
import email.utils
import io
import json
import os
import sys
import tempfile
import traceback
import urllib.error
from typing import Any

# a throwaway data root BEFORE the package is imported — nothing here may see
# the operator's real ~/orgtree (the runner strips ORGTREE_* from the child
# env, so this suite must set its own rather than inherit one)
_TMP = tempfile.mkdtemp(prefix="orgtree-cooldown-")
os.environ["ORGTREE_DATA"] = _TMP

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: `net._default_address`
# falls back to `net.DEFAULT_HUB_ADDRESS` — the operator's REAL hub — when this
# root has no defaults.json, and anything that registers an org then lands there
# permanently (measured twice: user report 2026-08-06, ~45 fixture orgs again on
# 2026-08-10). The discard port refuses instantly, so registration fails
# harmlessly into the backoff. This suite only ever imports `limits` and never
# creates an org, but the property is guarded over the WHOLE directory by
# test_external_mail §1 — deliberately, because "this particular rig happens not
# to need it" is exactly the reasoning that let the pollution recur. That guard
# caught this file on its first run; the rule is per-directory, not per-file.
os.makedirs(_TMP, exist_ok=True)
with open(os.path.join(_TMP, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # cp1252 console
    except (AttributeError, ValueError):
        pass

from orgtree import limits  # noqa: E402


# --------------------------------------------------------------- the fixtures
class Clock:
    """Stands in for the `time` module inside `limits`. Only `.time()` is
    reached for, but anything else the module grows will raise loudly here
    rather than silently fall through to the real clock."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, secs: float) -> None:
        self.now += secs


class Body(io.BytesIO):
    """A urlopen() result: a context manager `json.load` can read."""

    def __enter__(self) -> "Body":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class Transport:
    """Programmable stand-in for `urlopen`, counting every call.

    `script` is a list of outcomes consumed in order; the LAST one repeats
    forever, so a test that expects "no further calls" cannot pass merely by
    exhausting the script and erroring — it has to actually not call."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script) or [self.ok()]
        self.calls = 0

    def __call__(self, req: Any, timeout: float | None = None) -> Any:
        self.calls += 1
        out = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(out, Exception):
            raise out
        return Body(json.dumps(out).encode())

    # -- outcomes ----------------------------------------------------------
    @staticmethod
    def ok(percent: int = 12) -> dict[str, Any]:
        return {"limits": [{"type": "session", "utilization": percent,
                            "resets_at": "2026-08-25T18:00:00Z"}]}

    @staticmethod
    def http(code: int, retry_after: str | None = None,
             body: bytes = b"{}", **headers: str) -> urllib.error.HTTPError:
        hdrs = email.message.Message()
        if retry_after is not None:
            hdrs["Retry-After"] = retry_after
        for k, v in headers.items():
            hdrs[k.replace("_", "-")] = v
        return urllib.error.HTTPError(
            limits.USAGE_URL, code, "Too Many Requests" if code == 429
            else "Forbidden" if code == 403 else "Server Error",
            hdrs, io.BytesIO(body))


class Rig:
    """Installs the clock + transport into `limits` and takes them out again."""

    def __init__(self, *script: Any) -> None:
        self.transport = Transport(*script)
        self.clock = Clock()

    def __enter__(self) -> "Rig":
        import urllib.request as _ur
        self._real_urlopen = _ur.urlopen
        self._real_time = limits.time
        self._real_plan = limits._plan
        self._real_subproxy = limits.subproxy
        _ur.urlopen = self.transport                       # type: ignore[assignment]
        limits.time = self.clock                           # type: ignore[assignment]
        limits._plan = lambda: "max"                       # type: ignore[assignment]

        class _Sub:
            @staticmethod
            def available() -> bool:
                return True

            @staticmethod
            def get_access_token() -> str:
                return "host-token"

        limits.subproxy = _Sub()                           # type: ignore[assignment]
        # ⚠ start from a clean module, and do it by REACHING IN rather than by
        # calling `invalidate()`. Measured during the mutation run: against
        # pre-fix code, `invalidate()` cleared only `_cache`, so `_key_cache`
        # survived from an earlier case and every later fetch was a cache HIT
        # with zero transport calls — an entire section reporting 0 for reasons
        # that had nothing to do with the behaviour under test. A rig whose
        # reset depends on the code it is testing is not a reset.
        self._reset()
        return self

    @staticmethod
    def _reset() -> None:
        limits._cache.update(at=0.0, data=None)
        limits._key_cache.clear()
        getattr(limits, "_cooldown", {}).clear()   # absent in pre-fix code

    def __exit__(self, *_: Any) -> None:
        import urllib.request as _ur
        _ur.urlopen = self._real_urlopen                   # type: ignore[assignment]
        limits.time = self._real_time                      # type: ignore[assignment]
        limits._plan = self._real_plan                     # type: ignore[assignment]
        limits.subproxy = self._real_subproxy              # type: ignore[assignment]
        self._reset()


# ----------------------------------------------------------------- the checks
FAILS: list[str] = []
CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        # ⚠ THE OUTPUT SHAPE IS LOAD-BEARING, not cosmetic. tools/run_tests.py
        # counts checks with `^\s*ok\s+(\d+)\b` and wants a closing
        # `ALL N CHECKS PASS`. The first draft printed `ok 1.1 …`, so the
        # runner read the SECTION NUMBERS as the count and reported this suite
        # as "4 checks" out of 38 — green, and measuring almost nothing.
        print(f"ok {CHECKS:3d}  {label}")
    else:
        print(f"FAIL      {label}" + (f"  — {detail}" if detail else ""))
        FAILS.append(label + (f" — {detail}" if detail else ""))


def eq(label: str, got: Any, want: Any) -> None:
    check(label, got == want, f"got {got!r}, want {want!r}")


# ============================================================== §1 key rows
def s1_key_rows() -> None:
    print("\n§1  fetch_for_token — the key rows the user was clicking")

    # 1.1 the reported case: one 429 carrying a real deadline
    with Rig(Transport.http(429, "1032")) as r:
        got = limits.fetch_for_token("tok", cache_key="k1")
        eq("1.1 one upstream call for the first ask", r.transport.calls, 1)
        check("1.1 reports unavailable", got.get("available") is False, repr(got))
        check("1.1 the message names the wait, not an HTTPError repr",
              "rate limited" in str(got.get("error"))
              and "17m" in str(got.get("error")),
              repr(got.get("error")))

        # 1.2 THE WHOLE POINT: further asks inside the window touch nothing
        for _ in range(5):
            limits.fetch_for_token("tok", cache_key="k1")
        eq("1.2 five more asks inside the window add ZERO calls",
           r.transport.calls, 1)

        # 1.3 …and the window really does end
        r.clock.advance(1031)
        limits.fetch_for_token("tok", cache_key="k1")
        eq("1.3 still gated one second before the deadline",
           r.transport.calls, 1)
        r.clock.advance(2)
        limits.fetch_for_token("tok", cache_key="k1")
        eq("1.3 asks again once the deadline passes", r.transport.calls, 2)

    # 1.4 recovery: after the window, a good answer is served normally
    with Rig(Transport.http(429, "60"), Transport.ok(41)) as r:
        limits.fetch_for_token("tok", cache_key="k1")
        r.clock.advance(61)
        got = limits.fetch_for_token("tok", cache_key="k1")
        eq("1.4 two calls total", r.transport.calls, 2)
        check("1.4 the recovered readout is served",
              got.get("available") is True and bool(got.get("limits")), repr(got))

    # 1.5 Retry-After as an HTTP-date (RFC 9110 allows either form)
    with Rig() as r:
        when = email.utils.format_datetime(
            __import__("datetime").datetime.fromtimestamp(
                r.clock.now + 600, __import__("datetime").timezone.utc))
        r.transport.script = [Transport.http(429, when)]
        limits.fetch_for_token("tok", cache_key="k1")
        limits.fetch_for_token("tok", cache_key="k1")
        eq("1.5 an HTTP-date Retry-After gates too", r.transport.calls, 1)
        r.clock.advance(601)
        limits.fetch_for_token("tok", cache_key="k1")
        eq("1.5 …and expires at the stated moment", r.transport.calls, 2)

    # 1.6 a 429 with NO Retry-After still earns a pause
    with Rig(Transport.http(429, None)) as r:
        limits.fetch_for_token("tok", cache_key="k1")
        limits.fetch_for_token("tok", cache_key="k1")
        eq("1.6 a bare 429 gates on the default", r.transport.calls, 1)
        r.clock.advance(limits.DEFAULT_RETRY_AFTER + 1)
        limits.fetch_for_token("tok", cache_key="k1")
        eq("1.6 …for exactly DEFAULT_RETRY_AFTER", r.transport.calls, 2)

    # 1.7a the MEASURED escalation must survive the clamp intact. The account
    #      answered 1032, then 3600 twelve minutes later; a clamp set near
    #      either would truncate a real window, make us ask early, and earn a
    #      longer one. This pins that 3600 is honoured to the second.
    with Rig(Transport.http(429, "3600")) as r:
        limits.fetch_for_token("tok", cache_key="k1")
        r.clock.advance(3599)
        limits.fetch_for_token("tok", cache_key="k1")
        eq("1.7a a measured 3600s window is honoured, not clamped short",
           r.transport.calls, 1)
        r.clock.advance(2)
        limits.fetch_for_token("tok", cache_key="k1")
        eq("1.7a …and ends exactly when it said", r.transport.calls, 2)

    # 1.7 a hostile / absurd Retry-After cannot lock a lane out for ever
    with Rig(Transport.http(429, "999999")) as r:
        limits.fetch_for_token("tok", cache_key="k1")
        r.clock.advance(limits.MAX_RETRY_AFTER + 1)
        limits.fetch_for_token("tok", cache_key="k1")
        eq("1.7 clamped to MAX_RETRY_AFTER", r.transport.calls, 2)

    # 1.8 …and a zero/negative/garbage one cannot authorise a hammer loop
    for raw in ("0", "-5", "soon", ""):
        with Rig(Transport.http(429, raw)) as r:
            limits.fetch_for_token("tok", cache_key="k1")
            limits.fetch_for_token("tok", cache_key="k1")
            eq(f"1.8 Retry-After {raw!r} still gates (no hammer loop)",
               r.transport.calls, 1)

    # 1.9 stale bars still win over an error box — AND the window is still set
    with Rig(Transport.ok(7), Transport.http(429, "300")) as r:
        first = limits.fetch_for_token("tok", cache_key="k1")
        check("1.9 the first ask succeeds", first.get("available") is True)
        r.clock.advance(limits.CACHE_TTL + 1)      # past the success TTL
        got = limits.fetch_for_token("tok", cache_key="k1")
        eq("1.9 the 429 was actually attempted", r.transport.calls, 2)
        check("1.9 stale bars are served, not an error",
              got.get("available") is True, repr(got))
        for _ in range(4):
            limits.fetch_for_token("tok", cache_key="k1")
        eq("1.9 …and the window still suppresses every later ask",
           r.transport.calls, 2)


# ================================================================== §2 host
def s2_host() -> None:
    print("\n§2  fetch — the host path had the identical hole")

    with Rig(Transport.http(429, "1032")) as r:
        got = limits.fetch()
        eq("2.1 one upstream call", r.transport.calls, 1)
        check("2.1 names the wait", "rate limited" in str(got.get("error")),
              repr(got.get("error")))
        for _ in range(5):
            limits.fetch()
        eq("2.1 five more asks add ZERO calls", r.transport.calls, 1)

    # 2.2 force=True must NOT punch through. The freeze-correction pass is the
    #     caller that would otherwise turn one rate limit into a storm.
    with Rig(Transport.http(429, "1032")) as r:
        limits.fetch()
        for _ in range(5):
            limits.fetch(force=True)
        eq("2.2 force=True is still gated", r.transport.calls, 1)

    # 2.3 …and max_age, the other cache-bypass, likewise
    with Rig(Transport.http(429, "1032")) as r:
        limits.fetch()
        for _ in range(5):
            limits.fetch(max_age=0.0)
        eq("2.3 max_age=0 is still gated", r.transport.calls, 1)


# ========================================================== §3 independence
def s3_independence() -> None:
    print("\n§3  one account's window must not gag another")

    with Rig(Transport.http(429, "600")) as r:
        limits.fetch_for_token("a", cache_key="rowA")
        eq("3.1 row A asked once", r.transport.calls, 1)
        limits.fetch_for_token("b", cache_key="rowB")
        eq("3.1 row B is NOT gated by row A's window", r.transport.calls, 2)

    with Rig(Transport.http(429, "600")) as r:
        limits.fetch()                                  # host takes a window
        limits.fetch_for_token("a", cache_key="rowA")   # key must still ask
        eq("3.2 a key row is not gated by the host's window",
           r.transport.calls, 2)

    with Rig(Transport.http(429, "600")) as r:
        limits.fetch_for_token("a", cache_key="rowA")   # key takes a window
        limits.fetch()                                  # host must still ask
        eq("3.3 the host is not gated by a key row's window",
           r.transport.calls, 2)


# ====================================================== §4 negative controls
def s4_controls() -> None:
    print("\n§4  negative controls — what must NOT open a window")

    # ⚠ THE CONTROL THAT MAKES §1 MEAN ANYTHING. If a plain error also gated,
    # every count above would be explained by "the module stopped calling for
    # some other reason" rather than by the 429 logic. It also proves the
    # counter can still go UP, so a passing §1 is not just a dead transport.
    with Rig(Transport.http(500, None)) as r:
        for _ in range(5):
            got = limits.fetch_for_token("tok", cache_key="k1")
        eq("4.1 a 500 opens NO window — all five asks reach the transport",
           r.transport.calls, 5)
        check("4.1 and reports the raw failure, not a cooldown",
              "rate limited" not in str(got.get("error")), repr(got.get("error")))

    # 4.2 a 401 is a credential the user can fix by re-pasting; hiding it
    #     behind a cooldown would make the fix look like it did not take
    with Rig(Transport.http(401, None)) as r:
        for _ in range(3):
            limits.fetch_for_token("tok", cache_key="k1")
        eq("4.2 a 401 opens no window either", r.transport.calls, 3)

    # 4.3 a transport-level blip is not a rate limit
    with Rig(urllib.error.URLError("connection reset")) as r:
        for _ in range(3):
            limits.fetch_for_token("tok", cache_key="k1")
        eq("4.3 a URLError opens no window", r.transport.calls, 3)

    # 4.4 invalidate() clears windows — otherwise module state leaks between
    #     tests and the leak looks exactly like the feature working
    with Rig(Transport.http(429, "1032")) as r:
        limits.fetch_for_token("tok", cache_key="k1")
        limits.invalidate()
        limits.fetch_for_token("tok", cache_key="k1")
        eq("4.4 invalidate() drops the window", r.transport.calls, 2)

    # 4.5 the rig itself must be honest: with no gating in play, N asks past
    #     the success TTL really do make N calls
    with Rig(Transport.ok(3)) as r:
        for i in range(3):
            limits.fetch_for_token("tok", cache_key="k1")
            r.clock.advance(limits.CACHE_TTL + 1)
        eq("4.5 rig sanity: three uncached successes are three calls",
           r.transport.calls, 3)


# ======================================================== §5 the 403 escalation
RATE_BODY = (b'{"error":{"type":"rate_limit_error",'
             b'"message":"Rate limited. Please try again later."}}')
WAF_BODY = b"<html><body>error code: 1010</body></html>"
AUTH_BODY = (b'{"error":{"type":"authentication_error",'
             b'"message":"invalid bearer token"}}')
PERM_BODY = (b'{"error":{"type":"permission_error",'
             b'"message":"not allowed"}}')


def s5_forbidden() -> None:
    """User report 2026-08-25: "im getting a 403 forbidden now ... as opposed
    to a 429". The edge escalates — a client that keeps asking through a 429
    starts getting 403 instead — so a THROTTLING 403 must open a window too.
    But a 403 that means "this key is no good" must NOT: that is the one the
    user fixes by pasting a new key, and a cooldown would swallow the fix."""
    print("\n§5  a 403 that is a throttle gates; a 403 that is a "
          "credential does not")

    # -- the throttling kinds: gate --------------------------------------
    for label, err in (
        ("with Retry-After", Transport.http(403, "900")),
        ("rate_limit_error body", Transport.http(403, None, RATE_BODY)),
        ("Cloudflare 1010 block page", Transport.http(403, None, WAF_BODY)),
        ("cf-mitigated header", Transport.http(403, None, b"",
                                               cf_mitigated="challenge")),
    ):
        with Rig(err) as r:
            got = limits.fetch_for_token("tok", cache_key="k1")
            for _ in range(4):
                limits.fetch_for_token("tok", cache_key="k1")
            eq(f"5.1 403 {label} opens a window (1 call, not 5)",
               r.transport.calls, 1)
            check(f"5.1 403 {label} reports the wait",
                  "rate limited" in str(got.get("error")), repr(got.get("error")))

    # 5.2 the Retry-After on a throttling 403 is honoured, not defaulted
    with Rig(Transport.http(403, "900")) as r:
        limits.fetch_for_token("tok", cache_key="k1")
        r.clock.advance(899)
        limits.fetch_for_token("tok", cache_key="k1")
        eq("5.2 still gated at 899s", r.transport.calls, 1)
        r.clock.advance(2)
        limits.fetch_for_token("tok", cache_key="k1")
        eq("5.2 open again at 901s", r.transport.calls, 2)

    # -- ⚠ THE CONTROLS THAT KEEP §5 HONEST: credential 403s must NOT gate --
    for label, err in (
        ("authentication_error", Transport.http(403, None, AUTH_BODY)),
        ("permission_error", Transport.http(403, None, PERM_BODY)),
        ("bare 403, empty body", Transport.http(403, None, b"")),
        ("401", Transport.http(401, None, AUTH_BODY)),
    ):
        with Rig(err) as r:
            got = limits.fetch_for_token("tok", cache_key="k1")
            for _ in range(2):
                limits.fetch_for_token("tok", cache_key="k1")
            eq(f"5.3 {label} stays retryable (3 calls, no window)",
               r.transport.calls, 3)
            check(f"5.3 {label} says what to DO about it",
                  "setup-token" in str(got.get("error")), repr(got.get("error")))

    # 5.4 …and the host path discriminates identically
    with Rig(Transport.http(403, None, RATE_BODY)) as r:
        limits.fetch()
        for _ in range(3):
            limits.fetch(force=True)
        eq("5.4 host: a throttling 403 gates even force=True",
           r.transport.calls, 1)
    with Rig(Transport.http(403, None, AUTH_BODY)) as r:
        for _ in range(3):
            limits.fetch(force=True)
        eq("5.4 host: a credential 403 stays retryable", r.transport.calls, 3)


def main() -> int:
    print(__doc__.strip().splitlines()[0])
    for fn in (s1_key_rows, s2_host, s3_independence, s4_controls,
               s5_forbidden):
        try:
            fn()
        except Exception:                              # noqa: BLE001
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
