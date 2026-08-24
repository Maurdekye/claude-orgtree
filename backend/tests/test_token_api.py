"""The token HTTP surface — its OWN leak check, not the registry's.

    `/api/accounts` already has a check asserting no token text appears in its
    payload. **That check guards the REGISTRY object and would pass happily
    while these endpoints leaked**, because the registry genuinely contains no
    tokens and never will. An absence check that guards the wrong object is
    this repo's signature failure, and the fix is a check that guards THIS
    object.

Run:  python backend/tests/test_token_api.py

No pytest, no network. Every token value is an obvious placeholder.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-tokapi-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)

from orgtree import accounts, api, store, tokens          # noqa: E402

FAKE_TOKEN = "FAKE-TOKEN-VALUE-NOT-A-CREDENTIAL-0001"
UUID_A = "11111111-2222-3333-4444-555555555555"

PASS = 0
FAIL: list[tuple[str, str]] = []
_SECTION = [""]


def section(name: str) -> None:
    _SECTION[0] = name
    print(f"\n{name}:")


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


def profile(uuid=UUID_A, email="someone@example.com"):
    return {
        "account": {"uuid": uuid, "email": email, "full_name": "A Person",
                    "has_claude_max": True, "has_claude_pro": False,
                    "created_at": "2026-01-30T13:57:12.516557Z"},
        "organization": {"uuid": "99999999-8888-7777-6666-555555555555",
                         "name": "Org", "rate_limit_tier": "default_claude_max_20x",
                         "organization_type": "claude_max"},
        "application": {"uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "name": "Claude Code", "slug": "claude-code"},
    }


class R:
    def __init__(self, status, body):
        self.status, self.raw = status, body

    @property
    def json(self):
        try:
            return json.loads(self.raw.decode() or "null")
        except ValueError:
            return None


def call(method, path, body=None, host=b"127.0.0.1:7402"):
    """Hand-built ASGI scope — same technique as test_accounts.py §7, so
    nothing normalises the path between here and the gateway."""
    payload = b"" if body is None else json.dumps(body).encode()
    hdrs = [(b"host", host)]
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
    asyncio.run(api.app(scope, receive, send))
    return R(st[0], b"".join(chunks))


def main() -> None:
    t0 = time.perf_counter()
    print(f"data root: {store.DATA_ROOT}")
    print(f"token store: {tokens.tokens_path()}")
    accounts.upsert(accounts.identity_from_profile(profile()))

    section("§0 isolation")
    root = os.path.abspath(os.path.join(_HERE, ".."))
    check("the api under test is THIS worktree's", lambda: (
        None if os.path.abspath(api.__file__).startswith(root)
        else (_ for _ in ()).throw(AssertionError(f"imported {api.__file__}"))))
    check("the token store is under the throwaway data root", lambda: (
        None if tokens.tokens_path().startswith(store.DATA_ROOT)
        else (_ for _ in ()).throw(AssertionError("outside the temp root"))))

    section("§1 storing — store-first, and refusals are loud")
    check("PUT a token → 200 and it is reported as present", lambda: (
        eq(call("PUT", f"/api/accounts/{UUID_A}/token",
                {"token": FAKE_TOKEN}).json["tokens"], {UUID_A: "stored"})))
    check("the token really landed in the store", lambda: (
        eq(tokens.get(UUID_A), FAKE_TOKEN)))
    check("PUT for an unknown account → 404", lambda: (
        eq(call("PUT", "/api/accounts/nope/token",
                {"token": FAKE_TOKEN}).status, 404)))
    check("an empty token → 422, and the stored one survives", lambda: (
        eq(call("PUT", f"/api/accounts/{UUID_A}/token", {"token": "  "}).status,
           422),
        eq(tokens.get(UUID_A), FAKE_TOKEN,
           "a refused paste destroyed the stored token: ")))

    section("§2 THE LEAK CHECKS — guarding THIS object, not the registry")

    def _put_response_no_leak():
        raw = call("PUT", f"/api/accounts/{UUID_A}/token",
                   {"token": FAKE_TOKEN}).raw.decode()
        if FAKE_TOKEN in raw:
            raise AssertionError(
                "THE PUT RESPONSE ECHOES THE TOKEN BACK. The value must never "
                "return on the wire — success is reported as presence.")
    check("PUT response never echoes the token", _put_response_no_leak)

    def _list_no_leak():
        raw = call("GET", "/api/accounts/tokens").raw.decode()
        if FAKE_TOKEN in raw:
            raise AssertionError("GET /api/accounts/tokens leaked the value")
        if str(len(FAKE_TOKEN)) in raw:
            raise AssertionError(
                "GET /api/accounts/tokens leaked the token LENGTH — a real "
                "disclosure that buys a reader nothing they need")
    check("GET tokens leaks neither value nor length", _list_no_leak)

    def _registry_still_clean():
        raw = call("GET", "/api/accounts").raw.decode()
        if FAKE_TOKEN in raw:
            raise AssertionError(
                "a token reached the REGISTRY payload — the store was merged "
                "into accounts.json after all")
    check("the registry payload is still token-free", _registry_still_clean)

    def _control_the_leak_check_can_fail():
        """⚠ POSITIVE CONTROL. The three checks above are assertions of
        ABSENCE, and absence checks pass when the thing under test is simply
        missing — a 404 on every route would satisfy all of them. Prove the
        endpoint really returns the account whose token we are asserting is
        absent, so 'no leak' means 'present and clean'."""
        body = call("GET", "/api/accounts/tokens").json
        if not body or UUID_A not in (body.get("tokens") or {}):
            raise AssertionError(
                "the endpoint does not report this account at all, so the "
                "leak checks above are passing vacuously")
    check("CONTROL: the endpoint really reports the account",
          _control_the_leak_check_can_fail)

    section("§3 kiosk visitors are denied the token routes")
    # ⚠ MY FIRST ATTEMPT AT THIS SECTION WAS WRONG AND ITS FAILURE LOOKED LIKE
    # A VULNERABILITY. It sent requests to `api.app` with a `kiosk.local` Host
    # header and asserted a refusal; PUT and DELETE "reached" the routes and
    # one of them really deleted a stored token. But a kiosk visitor is not a
    # Host header at all — they arrive through a `/k/<token>/` PATH PREFIX
    # handled by a separate ASGI wrapper, and `api.app` is the ADMIN app. The
    # rig was simply making authenticated admin calls. Left as a warning: a
    # security check built on the wrong entry point reports a breach that
    # isn't there, and would have sent me to my superior with one.
    #
    # The matrix is therefore asked directly, as test_accounts.py §7 does.
    def _kiosk_denied():
        for meth, path in (("GET", "/api/accounts/tokens"),
                           ("PUT", f"/api/accounts/{UUID_A}/token"),
                           ("DELETE", f"/api/accounts/{UUID_A}/token")):
            d = api._public_denied(meth, path, "acme")
            if d is None:
                raise AssertionError(
                    f"kiosk visitor was NOT denied {meth} {path} — a visitor "
                    "could read, overwrite or destroy a stored credential")
            # ⚠ 403 SPECIFICALLY. `is not None` also passes via the matrix's
            # trailing catch-all 404, which fires for any /api/accounts path
            # whether or not the freeze exists — so only 403 constrains the
            # line this check is named for (the same trap review 2026-08-24
            # caught in the registry's own checks).
            eq(d[0], 403, f"{meth} {path} was denied by the catch-all 404 "
                          f"rather than by the freeze — ")
    check("kiosk visitors are denied every token route, by the FREEZE",
          _kiosk_denied)

    def _kiosk_control():
        """CONTROL: the same matrix still allows an open route, so the
        refusals above are a rule rather than a matrix that denies
        everything."""
        eq(api._public_denied("GET", "/api/orgs", "acme"), None,
           "control: /api/orgs GET should stay open to a kiosk visitor: ")
    check("CONTROL: the same matrix still allows an open route", _kiosk_control)

    section("S5 the serving indicator - resolved, never intent")
    call("POST", "/api/orgs", {"slug": "acme", "name": "Acme"})

    def _serving_ambient_by_default():
        r = call("GET", "/api/accounts/serving/acme")
        eq(r.status, 200)
        eq(r.json["serving"], "ambient",
           "an org with no account selected serves as the signed-in login: ")
    check("with nothing selected, the org serves as the ambient login",
          _serving_ambient_by_default)

    def _serving_follows_the_resolved_env():
        """⚠ THE POINT OF THE ENDPOINT. Selecting an account whose token is
        ABSENT must NOT report that account — the panel would then display a
        confident wrong answer, which is the exact confusion this surface
        exists to prevent."""
        o = store.load_org("acme")
        o.d["account_token_uuid"] = "ghost-account"
        store.save_org(o)
        r = call("GET", "/api/accounts/serving/acme")
        if r.json["serving"] == "ghost-account":
            raise AssertionError(
                "the panel reports INTENT: it names an account whose token "
                "does not exist, so it would display a confident wrong "
                "answer about who is serving turns")
        eq(r.json["serving"], "ambient")
    check("a selected account with no token does NOT show as serving",
          _serving_follows_the_resolved_env)

    def _serving_reports_the_real_one():
        tokens.put(UUID_A, FAKE_TOKEN)
        o = store.load_org("acme")
        o.d["account_token_uuid"] = UUID_A
        store.save_org(o)
        r = call("GET", "/api/accounts/serving/acme")
        eq(r.json["serving"], UUID_A,
           "a real stored token must show as the serving account: ")
        if FAKE_TOKEN in r.raw.decode():
            raise AssertionError("the serving payload leaked the token")
    check("a stored token DOES show as the serving account",
          _serving_reports_the_real_one)

    def _serving_unknown_org_is_404():
        eq(call("GET", "/api/accounts/serving/no-such-org").status, 404)
    check("an unknown org is a 404, not a confident wrong answer",
          _serving_unknown_org_is_404)

    def _serving_never_leaks():
        tokens.put(UUID_A, FAKE_TOKEN)
        r = call("GET", "/api/accounts/serving/no-such-org")
        if FAKE_TOKEN in r.raw.decode():
            raise AssertionError("the serving endpoint leaked a token value")
    check("the serving endpoint never returns token material",
          _serving_never_leaks)

    section("S6 ran_as on the node payload - resolved, never intent")
    import orgtree.supervisor as _sup

    def _ran_as_is_exposed():
        """It was recorded at spawn and read by NOTHING for a while - built and
        never surfaced. This is the check that it reaches a caller at all."""
        src = open(_sup.__file__, encoding="utf-8").read()
        if 'st["ran_as"]' not in src:
            raise AssertionError("the supervisor no longer records ran_as")
        api_src = open(api.__file__, encoding="utf-8").read()
        if '"ran_as"' not in api_src:
            raise AssertionError(
                "ran_as is recorded but NOT exposed on the node payload - it "
                "describes what actually happened and nothing outside can see "
                "it, so a turn served by the wrong account is invisible")
    check("ran_as reaches the node payload at all", _ran_as_is_exposed)

    def _ran_as_follows_resolved_env_not_intent():
        """The same assertion identity_in_env carries, one layer out: an org
        pointed at an account whose token is ABSENT must surface 'ambient',
        never that account - otherwise the field confidently reports an
        identity that never served anything."""
        class _O:
            def __init__(self, **d):
                self.d = d
                self.nodes = {}
        ghost = _O(account_token_uuid="no-token-for-this-one")
        got = _sup.identity_in_env(_sup.spawn_env(ghost), ghost)
        if got == "no-token-for-this-one":
            raise AssertionError(
                "ran_as would report the INTENDED account for a turn that ran "
                "under the ambient login - the one wrong answer that makes a "
                "silently-wrong result look identical to a correct one")
        eq(got, "ambient")
        # ...and the positive leg, so this is not passing because it always
        # says "ambient"
        tokens.put(UUID_A, FAKE_TOKEN)
        real = _O(account_token_uuid=UUID_A)
        eq(_sup.identity_in_env(_sup.spawn_env(real), real), UUID_A,
           "a genuinely stored token must surface as itself: ")
    check("ran_as follows the RESOLVED env, not the org's intent",
          _ran_as_follows_resolved_env_not_intent)

    def _ran_as_is_never_a_credential():
        tokens.put(UUID_A, FAKE_TOKEN)
        class _O:
            def __init__(self, **d):
                self.d = d
                self.nodes = {}
        o = _O(account_token_uuid=UUID_A)
        got = _sup.identity_in_env(_sup.spawn_env(o), o)
        if FAKE_TOKEN in got or FAKE_TOKEN[:12] in got:
            raise AssertionError("ran_as carries token material")
    check("ran_as never carries credential material",
          _ran_as_is_never_a_credential)

    section("§4 forgetting")
    check("DELETE forgets it", lambda: (
        eq(call("DELETE", f"/api/accounts/{UUID_A}/token").json["forgotten"],
           True),
        eq(tokens.get(UUID_A), "")))

    dt = time.perf_counter() - t0
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
