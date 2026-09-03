"""The access record — the one line per request that D-239 was missing.

Run directly::

    python backend/tests/test_access_record.py

WHY THIS SUITE EXISTS
---------------------
On 2026-09-03 `GET /api/orgs/{slug}` spent two days answering in 11-38 s and
nothing noticed, because the log carried uvicorn's stock line and that line has
no duration, no size and no timestamp. Of 12,055 lines in `backend.log`, one
carried anything duration-shaped. The regression was eventually found by
attaching py-spy to the live process.

So the record is not a nicety, it is the instrument. These checks are aimed at
the four ways an instrument like this quietly stops being one.

    §1  it measures at all, and reports the fields a diagnosis needs
    §2  it cannot leak a credential — the SECURITY property, see below
    §3  the alarm fires
    §4  …and its rate limit RESETS, which is the whole difference between a
        threshold and `_tree_slow_warned`
    §5  a request that FAILS is still recorded
    §6  `inflight` really counts concurrency

⚠ §2 IS A SECURITY CHECK, NOT A TIDINESS ONE, and it is why the record logs a
route TEMPLATE rather than a path. This middleware sits on `app`, and the
BRIDGE listener wraps that same object. `main` deliberately passes
`access_log: False` for that listener in frozen mode because THE ORG CREDENTIAL
RIDES IN THE URL — the frozen CLI cannot attach a private header. A record that
logged concrete paths would re-open exactly that hole from a different file,
and it would do so silently, on a listener no one was thinking about while
writing a performance tool. Templates carry no parameter values, so the
property holds on every listener without a policy check anyone can forget.

⚠ §4 IS THE ONE THAT ENCODES THE LESSON. The system already HAD a slow-request
warning — `org_tree`'s "tree() took >1s" — and it never fired. One reason was
`_tree_slow_warned`, a set that only ever grew: the warning could fire at most
once per org per process, so it could not have described a problem getting
worse even had it been pointed at the right code. A rate limit that never
resets is not a rate limit, it is a latch. This suite proves the window
reopens AND that the requests suppressed inside it are counted rather than
lost, because "it is still slow, 47 more times" is the sentence that turns a
log line into a diagnosis.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
import shutil
import sys
import tempfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RIG = tempfile.mkdtemp(prefix="orgtree-access-record-")
os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = os.path.join(RIG, "home")
os.environ["USERPROFILE"] = os.path.join(RIG, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import api, store, supervisor as S    # noqa: E402
from orgtree.ledger import USER                    # noqa: E402

S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None

PASS = 0
FAIL = 0


def check(label: str, fn: Any) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as exc:                                    # noqa: BLE001
        FAIL += 1
        print(f"  FAIL    {label}: {exc}")
        import traceback
        traceback.print_exc()


ORG = store.create_org("zz-access-record")
ORG.hire(USER, None, "haiku", 4, "boss")
store.save_org(ORG)


def hit(path: str, query: bytes = b"") -> list[str]:
    """Drive the real app through the real middleware; return printed lines."""
    scope: dict[str, Any] = {
        "type": "http", "method": "GET", "path": path,
        "raw_path": path.encode(), "query_string": query, "headers": [],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 7360),
        "scheme": "http", "state": {}, "app": api.app,
    }

    async def recv() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_m: dict[str, Any]) -> None:
        return None

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        asyncio.run(api.app(scope, recv, send))
    return [ln for ln in buf.getvalue().splitlines() if ln.startswith("[orgtree.")]


def field(line: str, key: str) -> str:
    for tok in line.split():
        if tok.startswith(key + "="):
            return tok.split("=", 1)[1]
    raise AssertionError(f"no {key}= in {line!r}")


# ── §1 · it measures, and reports what a diagnosis needs ─────────────────────
def every_request_is_recorded_with_the_fields_a_diagnosis_needs() -> None:
    lines = [ln for ln in hit("/api/orgs/zz-access-record")
             if ln.startswith("[orgtree.access]")]
    assert len(lines) == 1, lines
    ln = lines[0]
    assert " GET /api/orgs/{slug} 200 " in ln, ln
    # the four fields that were missing on 2026-09-03 and cost an hour each
    assert float(field(ln, "handler").rstrip("ms")) >= 0
    assert float(field(ln, "total").rstrip("ms")) >= 0
    assert int(field(ln, "bytes")) > 0, "a payload size of 0 for a real payload"
    assert int(field(ln, "inflight")) >= 1
    assert "T" in ln and "Z" in ln, f"no timestamp: {ln}"


# ── §2 · SECURITY: no parameter value, no query string, ever ─────────────────
def the_record_can_never_carry_a_credential() -> None:
    """The bridge credential rides in the URL. See the header note."""
    lines = hit("/api/orgs/zz-access-record/nodes/boss/chat", b"last=120&t=SEKRIT")
    assert lines, "nothing recorded"
    for ln in lines:
        assert "/api/orgs/{slug}/nodes/{nid}/chat" in ln, ln
        # the org slug and node id are PARAMETER VALUES; on the bridge listener
        # the same position carries the org's secret
        assert "zz-access-record" not in ln, f"path parameter leaked: {ln}"
        assert "boss" not in ln, f"path parameter leaked: {ln}"
        assert "SEKRIT" not in ln and "last=120" not in ln, (
            f"query string leaked: {ln}")

    # an UNMATCHED path has no template to fall back to, and must not fall
    # back to the path either — that is the case a naive implementation gets
    # wrong, because there is no route object to ask
    for ln in hit("/api/orgs/zz-access-record/../../etc/SEKRIT"):
        assert "SEKRIT" not in ln, f"unmatched path leaked: {ln}"
        assert "<unmatched>" in ln, ln


# ── §3/§4 · the alarm, and the reset that makes it a threshold ──────────────
def _slow_lines(handler_ms: float, route: str = "/slow") -> list[str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        api._access_emit({"method": "GET", "route": type("R", (), {"path": route})()},
                         200, handler_ms, handler_ms, 10, 1)
    return [ln for ln in buf.getvalue().splitlines()
            if ln.startswith("[orgtree.slow]")]


def a_slow_request_raises_the_alarm() -> None:
    api._slow_last.pop("/slow", None)
    api._slow_held.pop("/slow", None)
    assert not _slow_lines(api._SLOW_MS - 1), "alarmed below the threshold"
    got = _slow_lines(api._SLOW_MS + 1000)
    assert len(got) == 1 and "⚠" in got[0], got
    assert "in flight" in got[0], got[0]


def the_rate_limit_resets_and_counts_what_it_suppressed() -> None:
    """`_tree_slow_warned` could fire once per process. This may not."""
    api._slow_last.pop("/slow", None)
    api._slow_held.pop("/slow", None)

    first = _slow_lines(2000)
    assert len(first) == 1, first

    # inside the window: suppressed, but COUNTED — losing them would lose the
    # trend, which is the only thing that says "getting worse"
    for _ in range(3):
        assert not _slow_lines(2000), "repeat inside the window was not held"
    assert api._slow_held["/slow"] == 3, api._slow_held

    # …and the window REOPENS. Simulated by ageing the last-warned stamp
    # rather than sleeping, so the check is deterministic and tests the rule
    # itself rather than the wall clock.
    api._slow_last["/slow"] -= api._SLOW_REPEAT_S + 1
    again = _slow_lines(2000)
    assert len(again) == 1, (
        "the alarm did not fire again after its window elapsed — it is a "
        "latch, not a rate limit, and it cannot describe a worsening problem")
    assert "3 more like it" in again[0], (
        f"the suppressed count was dropped: {again[0]!r}")
    assert "/slow" not in api._slow_held, "the held counter did not reset"


# ── §5 · a failing request is still recorded ────────────────────────────────
def a_handler_that_raises_is_still_recorded() -> None:
    """An endpoint that fails SLOWLY is exactly as interesting as one that
    succeeds slowly, and it is the case uvicorn's line describes worst."""
    async def boom(_s: Any, _r: Any, _sd: Any) -> None:
        raise RuntimeError("handler exploded")

    scope = {"type": "http", "method": "POST", "path": "/x",
             "query_string": b"", "state": {}}

    async def recv() -> dict[str, Any]:
        return {"type": "http.request", "body": b""}

    async def send(_m: Any) -> None:
        return None

    buf = io.StringIO()
    raised = False
    with contextlib.redirect_stdout(buf):
        try:
            asyncio.run(api.AccessRecord(boom)(scope, recv, send))
        except RuntimeError:
            raised = True
    assert raised, "the middleware swallowed the handler's exception"
    lines = [ln for ln in buf.getvalue().splitlines()
             if ln.startswith("[orgtree.access]")]
    assert len(lines) == 1, f"a failed request went unrecorded: {lines}"
    assert " 0 " in lines[0], lines[0]      # no response started


# ── §6 · inflight really counts concurrency ─────────────────────────────────
def inflight_counts_overlapping_requests() -> None:
    """The field that would have explained "chats don't load"."""
    gate = asyncio.Event()

    async def held(_s: Any, _r: Any, sd: Any) -> None:
        await gate.wait()
        await sd({"type": "http.response.start", "status": 200, "headers": []})
        await sd({"type": "http.response.body", "body": b"ok"})

    async def recv() -> dict[str, Any]:
        return {"type": "http.request", "body": b""}

    async def send(_m: Any) -> None:
        return None

    async def drive() -> str:
        mw = api.AccessRecord(held)

        def scope() -> dict[str, Any]:
            return {"type": "http", "method": "GET", "path": "/h",
                    "query_string": b"", "state": {}}

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tasks = [asyncio.create_task(mw(scope(), recv, send))
                     for _ in range(4)]
            await asyncio.sleep(0)          # let all four enter the middleware
            gate.set()
            await asyncio.gather(*tasks)
        return buf.getvalue()

    out = asyncio.run(drive())
    depths = sorted(int(field(ln, "inflight")) for ln in out.splitlines()
                    if ln.startswith("[orgtree.access]"))
    assert len(depths) == 4, out
    assert depths == [1, 2, 3, 4], (
        f"four overlapping requests reported depths {depths}; `inflight` is "
        "not counting concurrency, so queueing stays invisible")
    assert api._ACCESS_INFLIGHT == 0, (
        f"the counter leaked: {api._ACCESS_INFLIGHT} still in flight")


try:
    print("access record")
    check("§1 every request is recorded with the fields a diagnosis needs",
          every_request_is_recorded_with_the_fields_a_diagnosis_needs)
    check("§2 the record can never carry a credential",
          the_record_can_never_carry_a_credential)
    check("§3 a slow request raises the alarm",
          a_slow_request_raises_the_alarm)
    check("§4 the rate limit resets and counts what it suppressed",
          the_rate_limit_resets_and_counts_what_it_suppressed)
    check("§5 a handler that raises is still recorded",
          a_handler_that_raises_is_still_recorded)
    check("§6 inflight counts overlapping requests",
          inflight_counts_overlapping_requests)
    print(f"\n{PASS} passed, {FAIL} FAILED")
finally:
    shutil.rmtree(RIG, ignore_errors=True)

sys.exit(1 if FAIL else 0)
