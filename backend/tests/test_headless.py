"""Headless orgs and per-org API keys (F-06 §9.5/§9.6) — the autonomy rules.

A headless org is one nobody is watching. That single fact turns several
ordinarily-harmless behaviours into traps: a question parked for a user who
will never read it, a credit request that will never be approved, a limit
freeze nobody will un-park, and — the expensive one — a key that decides who
PAYS for every turn. None of those announce themselves; an unattended org that
quietly stops working looks exactly like an unattended org with nothing to do.

So the suite asks, for each surface: what happens when there is nobody there?
The rule the codebase settled on is *deny the things that wait for a human, and
never deny the audit trail* — mail to the user is always accepted, because the
inbox is the record of an unattended run. That asymmetry is what §1 and §2 pin.

    §1  the four ledger denials — and that each is ABSENT when not headless
    §2  mail to the user is never denied, only annotated
    §3  the settings couplings, all four directions, plus the forced flags
    §4  the API key — where it may appear, and where it must not
    §5  the key selectors — unsandboxed env seam and sandbox precedence
    §6  the identity prompt's headless block
    §7  the credential watcher's alarm rules
    §8  the never-registered local hub is invisible (user ruling 2026-08-05)

Hermetic: throwaway data root + HOME, no listener, no Docker, no CLI.

    python backend/tests/test_headless.py [-v]
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-headless-")
_HOME = os.path.join(_TMP, "home")
os.makedirs(_HOME, exist_ok=True)
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: net._default_address
# falls back to net.DEFAULT_HUB_ADDRESS — the operator's real hub — when this
# root has no defaults.json, and any rig that starts the net daemon then
# registers its fixture orgs there permanently. Measured twice (user report
# 2026-08-06; ~45 fixture orgs again on 2026-08-10). The discard port refuses
# instantly, so registration fails harmlessly into the backoff.
# Guarded over this whole directory by test_external_mail §1.
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

os.environ["USERPROFILE"] = _HOME
os.environ["HOME"] = _HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_PORT"] = "7412"          # never bound
os.environ["ORGTREE_PUBLIC_PORT"] = "7412"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

from orgtree import api, net, sandbox as sbx, store, supervisor    # noqa: E402
from orgtree.ledger import LedgerError, Org, USER                  # noqa: E402

supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
supervisor.storage_check = lambda slug: None
sbx.warm = lambda org: None

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
VERBOSE = "-v" in sys.argv
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}
KEY = "sk-ant-test-org-key"


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def gap(label, why, fn) -> None:
    """Inverted expectation — see test_rename.py."""
    global PASS
    try:
        fn()
    except AssertionError as e:
        GAPS.append((label, why, str(e).split("\n")[0][:300]))
        print(f"  ⚑ GAP    {label}")
        return
    except Exception:                                            # noqa: BLE001
        FAIL.append((label + " (gap check errored)", traceback.format_exc()))
        print(f"  FAIL     {label} — the gap check itself broke")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote out of gap()")


def expect_error(fn, needle=""):
    try:
        fn()
    except LedgerError as e:
        assert needle.lower() in str(e).lower(), f"wrong error: {e}"
        return str(e)
    raise AssertionError(f"expected LedgerError containing {needle!r}")


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="headless test hire")
    s.update(over)
    return s


_n = [0]


def mkorg(headless=False, key=KEY, kiosk=False, persist=False, deep=True):
    """boss(20) → kid(5); optionally headless with a key."""
    _n[0] += 1
    name = f"zz headless {_n[0]}"
    org = store.create_org(name) if persist else Org.create(name)
    if kiosk:
        org.d["kiosk"] = {"enabled": True, "token": "t" * 16, "credits": 50,
                          "spend_limit": 5.0, "storage_limit_mb": 256,
                          "sandbox": False}
    if key:
        org.d["api_key"] = key
    if headless:
        org.d["headless"] = True
        org.d["auto_resume"] = True
    org.hire(USER, None, "opus", 20, "boss")
    if deep:
        org.hire("boss", "boss", "haiku", 5, "kid", **spec())
    if persist:
        store.save_org(org)
    return org


def api_call(app, method, path, body=None):
    payload = b"" if body is None else json.dumps(body).encode()
    hdrs = [(b"host", b"127.0.0.1:7412")]
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

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": hdrs, "client": ("127.0.0.1", 5),
             "server": ("127.0.0.1", 7412)}
    try:
        asyncio.run(app(scope, receive, send))
    except Exception as e:                                       # noqa: BLE001
        st[0] = st[0] or 500
        chunks.append(f"{type(e).__name__}: {e}".encode())
    raw = b"".join(chunks)
    try:
        return st[0], json.loads(raw)
    except Exception:                                            # noqa: BLE001
        return st[0], raw.decode("utf-8", "replace")


def settings(slug, **body):
    return api_call(api.app, "POST", f"/api/orgs/{slug}/settings", body)


# ══════════════════════════════════════════════════════════════════════ §1
def sec_denials() -> None:
    print("\n§1  the four denials — nothing waits for a human who is not there")

    def _credit_request():
        org = mkorg(headless=True)
        msg = expect_error(lambda: org.request_credits("boss", 40, "more"),
                           "headless")
        assert "auto-denied" in msg and "orgtree_status" in msg, msg
        assert not org.d.get("credit_requests"), "a card was parked anyway"
    check("request_credits is denied, and names what to do instead",
          _credit_request)

    def _ask_user():
        org = mkorg(headless=True)
        msg = expect_error(lambda: org.ask_user("boss", "ship it?"), "headless")
        assert "auto-denied" in msg, msg
        assert "orgtree_message" in msg or "peer" in msg, \
            "the denial must name the route that still works"
        assert not [a for a in org.d.get("asks", []) if a["status"] == "open"]
    check("ask_user is denied, and names the peer/superior route", _ask_user)

    def _request_audience_user():
        org = mkorg(headless=True)
        msg = expect_error(
            lambda: org.request_audience("kid", USER, "I need the user"),
            "headless")
        assert "auto-denied" in msg, msg
        assert not org.d.get("audience_requests"), "a request was parked anyway"
    check("a USER-audience request is denied", _request_audience_user)

    def _audience_to_a_superior_still_works():
        # only the USER target is denied — the chain still functions, which is
        # the whole point of "coordinate through your chain instead"
        org = mkorg(headless=True)
        org.hire("kid", "kid", "haiku", 1, "grandkid", **spec())
        r = org.request_audience("grandkid", "boss", "please")
        assert r.get("currently_at") or r.get("already_reachable"), r
        assert org.d.get("audience_requests"), "the request was not parked"
    check("…but an audience request up the CHAIN is untouched",
          _audience_to_a_superior_still_works)

    def _absent_when_attended():
        org = mkorg(headless=False)
        assert org.request_credits("boss", 40, "more").get("requested")
        org2 = mkorg(headless=False)
        assert org2.ask_user("boss", "ship it?").get("asked")
        org3 = mkorg(headless=False)
        r = org3.request_audience("kid", USER, "reason")
        assert r.get("currently_at") or r.get("already_reachable"), r
    check("all three succeed when a user IS present", _absent_when_attended)

    def _denials_are_adaptive_not_silent():
        # the ruling is "deny with the reason IN the result so the agent
        # adapts": every denial must name headless AND an alternative
        org = mkorg(headless=True)
        for call in (lambda: org.request_credits("boss", 40, "x"),
                     lambda: org.ask_user("boss", "x?"),
                     lambda: org.request_audience("kid", USER, "x")):
            try:
                call()
                raise AssertionError("not denied")
            except LedgerError as e:
                t = str(e).lower()
                assert "headless" in t and "no user is present" in t, t
                assert any(w in t for w in ("instead", "or record", "or ask")), t
    check("every denial states the situation AND a way forward",
          _denials_are_adaptive_not_silent)


# ══════════════════════════════════════════════════════════════════════ §2
def sec_audit_trail() -> None:
    print("\n§2  mail to the user is never denied — it is the audit trail")

    def _accepted_with_a_warning():
        org = mkorg(headless=True)
        r = org.post_mail("boss", USER, "for the record")
        assert r["delivered"] == "user_inbox", r
        assert org.d["user_inbox"][-1]["body"] == "for the record"
        w = " ".join(r["warnings"]).lower()
        assert "headless" in w and "no reply is coming" in w, w
        assert "record" in w, "the sender must be told to treat it as a record"
    check("post_mail to the user is ACCEPTED and annotated", _accepted_with_a_warning)

    def _no_warning_when_attended():
        org = mkorg(headless=False)
        r = org.post_mail("boss", USER, "hello")
        assert not [x for x in r["warnings"] if "headless" in x.lower()], r
    check("…and carries no such warning when a user is present",
          _no_warning_when_attended)

    def _status_reports_still_flow():
        # a headless org's whole reporting channel is mail + statuses; if
        # those were denied the run would be unobservable
        org = mkorg(headless=True)
        r = org.post_mail("boss", USER, "[DONE] finished", kind="status")
        assert r["delivered"] == "user_inbox", r
        assert org.d["user_inbox"][-1]["kind"] == "status"
    check("status reports to the user survive headless too",
          _status_reports_still_flow)


# ══════════════════════════════════════════════════════════════════════ §3
def sec_couplings() -> None:
    print("\n§3  the couplings — four refusals and two forced flags")

    def _headless_needs_a_key():
        org = mkorg(headless=False, key="", persist=True)
        code, j = settings(org.d["slug"], headless=True)
        assert code == 422 and "REQUIRES an API key" in json.dumps(j), (code, j)
        assert not store.load_org(org.d["slug"]).d.get("headless")
    check("headless without an API key is refused", _headless_needs_a_key)

    def _cannot_clear_the_key_while_headless():
        org = mkorg(headless=True, persist=True)
        org.d["fable_limit_policy"] = org.d["fable_filter_policy"] = "opus"
        store.save_org(org)
        code, j = settings(org.d["slug"], clear_api_key=True)
        assert code == 422 and "headless" in json.dumps(j).lower(), (code, j)
        assert store.load_org(org.d["slug"]).d.get("api_key") == KEY
    check("clearing the key while headless is refused",
          _cannot_clear_the_key_while_headless)

    def _halt_policies_refuse():
        org = mkorg(headless=False, persist=True)
        assert org.d.get("fable_limit_policy", "halt") == "halt"
        code, j = settings(org.d["slug"], headless=True)
        assert code == 422, (code, j)
        blob = json.dumps(j)
        assert "halt" in blob and "fable_limit_policy" in blob, blob
        # …and it names BOTH when both are halt
        assert "fable_filter_policy" in blob, (
            "the refusal must name every policy that blocks it, not the first")
    check("headless refuses while a fable policy is 'halt', naming it",
          _halt_policies_refuse)

    def _kiosk_cannot_be_headless():
        org = mkorg(headless=False, kiosk=True, persist=True)
        org.d["fable_limit_policy"] = org.d["fable_filter_policy"] = "opus"
        store.save_org(org)
        code, j = settings(org.d["slug"], headless=True)
        assert code == 422 and "kiosk" in json.dumps(j).lower(), (code, j)
    check("a kiosk cannot run headless", _kiosk_cannot_be_headless)

    def _enabling_forces_auto_resume():
        org = mkorg(headless=False, persist=True)
        org.d["fable_limit_policy"] = org.d["fable_filter_policy"] = "opus"
        org.d["auto_resume"] = False
        store.save_org(org)
        code, j = settings(org.d["slug"], headless=True)
        assert code == 200, (code, j)
        d = store.load_org(org.d["slug"]).d
        assert d["headless"] is True and d["auto_resume"] is True
        assert any("auto-resume" in w.lower() for w in j.get("warnings", [])), j
    check("enabling headless forces auto-resume ON, with a warning",
          _enabling_forces_auto_resume)

    def _disabling_warns_about_the_inbox():
        org = mkorg(headless=True, persist=True)
        code, j = settings(org.d["slug"], headless=False)
        assert code == 200, (code, j)
        assert not store.load_org(org.d["slug"]).d["headless"]
        assert any("inbox" in w.lower() for w in j.get("warnings", [])), j
    check("disabling headless points the user at the inbox",
          _disabling_warns_about_the_inbox)

    def _auto_resume_not_forced_back_off():
        # turning headless OFF must not silently undo the forced flag — the
        # user may want it, and nothing says otherwise
        org = mkorg(headless=True, persist=True)
        settings(org.d["slug"], headless=False)
        assert store.load_org(org.d["slug"]).d.get("auto_resume") is True, \
            "disabling headless silently turned auto-resume back off"
    check("disabling headless leaves auto-resume where it was",
          _auto_resume_not_forced_back_off)


# ══════════════════════════════════════════════════════════════════════ §4
def sec_key_hygiene() -> None:
    print("\n§4  the API key — reported as a boolean, never as itself")

    def _tree_says_set_not_what():
        org = mkorg(headless=True, persist=True)
        code, t = api_call(api.app, "GET", f"/api/orgs/{org.d['slug']}")
        assert code == 200
        assert t["api_key_set"] is True and t["headless"] is True, t
        assert KEY not in json.dumps(t), "THE API KEY LEAKED into the tree"
    check("the tree payload carries api_key_set, never the key",
          _tree_says_set_not_what)

    def _every_admin_payload():
        org = mkorg(headless=True, persist=True)
        slug = org.d["slug"]
        for path in ("/api/orgs", f"/api/orgs/{slug}", "/api/defaults",
                     "/api/host", f"/api/orgs/{slug}/events",
                     f"/api/orgs/{slug}/inbox", f"/api/orgs/{slug}/audiences",
                     f"/api/orgs/{slug}/net"):
            _c, j = api_call(api.app, "GET", path)
            assert KEY not in json.dumps(j), f"THE API KEY LEAKED into {path}"
    check("no admin payload carries it — including …/net", _every_admin_payload)

    def _set_and_clear():
        org = mkorg(headless=False, key="", persist=True)
        slug = org.d["slug"]
        code, j = settings(slug, api_key="  sk-ant-typed-with-space  ")
        assert code == 200, (code, j)
        assert store.load_org(slug).d["api_key"] == "sk-ant-typed-with-space", \
            "the key is stored stripped"
        assert "sk-ant-typed" not in json.dumps(j), \
            "the settings RESPONSE echoed the key back"
        code, j = settings(slug, clear_api_key=True)
        assert code == 200 and "api_key" not in store.load_org(slug).d
    check("the key can be set and cleared, and is never echoed", _set_and_clear)

    def _blank_does_not_clear():
        org = mkorg(headless=False, persist=True)
        settings(org.d["slug"], api_key="   ")
        assert store.load_org(org.d["slug"]).d.get("api_key") == KEY, \
            "a blank api_key field silently wiped a configured key"
    check("a blank api_key field is ignored, not a silent clear",
          _blank_does_not_clear)


# ══════════════════════════════════════════════════════════════════════ §5
def sec_selectors() -> None:
    print("\n§5  which key a turn actually launches with")

    def _host_key_does_not_reach_a_keyless_org():
        # REDTEAM FINDING 2026-08-05, fixed: clean_env copied os.environ
        # wholesale, so an operator's host key reached every org — including
        # kiosks, whose visitors would burn it.
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-HOST"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "tok-HOST"
        try:
            env = supervisor.clean_env()
            assert "ANTHROPIC_API_KEY" not in env, (
                "a host-level ANTHROPIC_API_KEY reaches every org's turn: a "
                "keyless org silently bills the key instead of the user's "
                "subscription, and a kiosk visitor burns the operator's")
            assert "ANTHROPIC_AUTH_TOKEN" not in env, "the token twin leaks"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    check("a HOST-level key never reaches a turn (redteam finding, fixed)",
          _host_key_does_not_reach_a_keyless_org)

    def _org_key_is_injected_unsandboxed():
        org = mkorg(headless=True, persist=True)
        o = store.load_org(org.d["slug"])
        env = supervisor.clean_env()
        if o.d.get("api_key") and not sbx.is_sandboxed(o):
            env["ANTHROPIC_API_KEY"] = str(o.d["api_key"])
        assert env["ANTHROPIC_API_KEY"] == KEY
        assert "ANTHROPIC_API_KEY" not in supervisor.clean_env(), \
            "the injection must be per-turn, not a mutation of the base env"
    check("an org's own key is injected for its turns only",
          _org_key_is_injected_unsandboxed)

    def _sandbox_precedence():
        # org.d.api_key > kiosk.api_key > ORGTREE_SANDBOX_API_KEY > proxied
        src = open(os.path.join(_HERE, "..", "orgtree", "sandbox.py"),
                   encoding="utf-8").read()
        i = src.index("key = (str(org.d.get(\"api_key\")")
        window = src[i:i + 260]
        assert window.index("org.d.get(\"api_key\")") \
            < window.index("k.get(\"api_key\")") \
            < window.index("ORGTREE_SANDBOX_API_KEY"), window
        assert "proxied" in window or "proxied" in src[i:i + 600], \
            "the proxied fallback is gone"
    check("the sandbox key precedence is org → kiosk → env → proxied",
          _sandbox_precedence)


# ══════════════════════════════════════════════════════════════════════ §6
def sec_prompt() -> None:
    print("\n§6  the identity prompt says so, but only when it is true")

    def _block_present_and_absent():
        on = mkorg(headless=True)
        off = mkorg(headless=False)
        p_on = supervisor.identity_prompt(on, "boss")
        p_off = supervisor.identity_prompt(off, "boss")
        assert "HEADLESS" in p_on, "the headless block is missing"
        assert "HEADLESS" not in p_off, \
            "an attended org's agents are told nobody is watching"
        low = p_on.lower()
        assert "no user is present" in low or "nobody" in low, p_on[:400]
    check("the HEADLESS block appears only in a headless org",
          _block_present_and_absent)

    def _the_key_is_not_in_the_prompt():
        on = mkorg(headless=True)
        assert KEY not in supervisor.identity_prompt(on, "boss"), \
            "THE API KEY LEAKED into an agent's own prompt"
    check("…and the org's API key is not in it", _the_key_is_not_in_the_prompt)


# ══════════════════════════════════════════════════════════════════════ §7
def sec_cred_watcher() -> None:
    print("\n§7  the credential watcher — alarm early, never on unknown")

    def creds(payload):
        p = os.path.join(_HOME, ".claude")
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, ".credentials.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh)

    def cred_notices(slug):
        """⚠ the user inbox carries other system notices (a kiosk is BORN with
        a permission-ceiling one), so every assertion here matches the
        credential warning itself rather than the inbox being empty."""
        return [e for e in (store.load_org(slug).d.get("user_inbox") or [])
                if "log in" in str(e.get("body", "")).lower()
                or "credential" in str(e.get("body", "")).lower()
                or "api key" in str(e.get("body", "")).lower()]

    def run_watcher(expect=True):
        """One pass of the real watcher. Its loop sleeps 6 h at the END, so a
        fresh start runs the body immediately; `expect` only decides how long
        to wait before concluding nothing happened."""
        supervisor._cred_watch_started = False
        supervisor.start_cred_watcher()
        for _ in range(40 if expect else 10):
            time.sleep(0.05)
            if expect and any(cred_notices(o["slug"])
                              for o in store.list_orgs()):
                return
        time.sleep(0.2)

    def _absent_field_is_not_an_alarm():
        for o in store.list_orgs():
            store.delete_org(o["slug"])
        org = mkorg(headless=False, key="", persist=True)
        creds({"claudeAiOauth": {"accessToken": "x"}})     # no expiry field
        run_watcher(expect=False)
        assert not cred_notices(org.d["slug"]), (
            "an ABSENT refreshTokenExpiresAt raised an alarm — it is UNKNOWN, "
            "not expired (subproxy drops it on a rotation with no reported "
            "lifetime)")
    check("an absent expiry field raises NO alarm", _absent_field_is_not_an_alarm)

    def _near_expiry_mails_keyless_orgs():
        for o in store.list_orgs():
            store.delete_org(o["slug"])
        keyless = mkorg(headless=False, key="", persist=True)
        keyed = mkorg(headless=True, key=KEY, persist=True)
        kiosk = mkorg(headless=False, key="", kiosk=True, persist=True)
        creds({"claudeAiOauth": {
            "refreshTokenExpiresAt": (time.time() + 3600) * 1000}})   # ~1h
        run_watcher()
        warned = cred_notices(keyless.d["slug"])
        assert warned, "the keyless org was not warned about the expiring credential"
        assert "api key" in warned[-1]["body"].lower() \
            or "log in" in warned[-1]["body"].lower(), warned[-1]
        assert not cred_notices(keyed.d["slug"]), \
            "an org on its OWN api key has no credential ceiling to warn about"
        assert not cred_notices(kiosk.d["slug"]), \
            "a kiosk org was warned — kiosks are excluded"
    check("near expiry mails keyless non-kiosk orgs only",
          _near_expiry_mails_keyless_orgs)

    def _at_most_one_per_day():
        for o in store.list_orgs():
            store.delete_org(o["slug"])
        org = mkorg(headless=False, key="", persist=True)
        creds({"claudeAiOauth": {
            "refreshTokenExpiresAt": (time.time() + 3600) * 1000}})
        run_watcher()
        n1 = len(cred_notices(org.d["slug"]))
        assert n1 == 1, n1
        # a SECOND watcher would be a second process; within one process the
        # per-slug clock is what must hold — re-running the same loop body
        # must not stack another warning
        supervisor._cred_watch_started = False
        supervisor.start_cred_watcher()
        time.sleep(0.6)
        n2 = len(cred_notices(org.d["slug"]))
        assert n2 == n1, (
            f"the credential warning stacked: {n1} → {n2}. ⚠ the ≤1/day clock "
            f"lives in a CLOSURE (`warned`) created per watcher start, so a "
            f"restart — or any second start — re-warns immediately")
    # promoted from gap() 2026-08-05: the last-warn stamp now PERSISTS on the
    # org doc (cred_warned_at), so ≤1/day survives restarts — the closure
    # clock made it one-per-restart on exactly the host that restarts
    check("the credential warning is at most one per org per day, across starts",
          _at_most_one_per_day)


# ══════════════════════════════════════════════════════════════════════ §8
def sec_hidden_hub() -> None:
    print("\n§8  a local hub that never answered is invisible (user ruling)")

    def _hidden_until_registered():
        org = mkorg(headless=False, persist=True)
        o = store.load_org(org.d["slug"])
        o.d["net_autoconnect"] = True
        o.d["net_hubs"] = net.hub_entries(True, [])
        net.mint_identity(o)
        store.save_org(o)
        block = net.status_block(store.load_org(org.d["slug"]).d)
        assert block is not None
        local = [h for h in block["hubs"] if h["id"] == net.LOCAL_HUB_ID][0]
        assert local.get("hidden") is True, (
            "a local hub that has never registered must be hidden from every "
            f"passive surface: {local}")
    check("the implicit local entry is hidden before its first contact",
          _hidden_until_registered)

    def _visible_once_registered():
        org = mkorg(headless=False, persist=True)
        o = store.load_org(org.d["slug"])
        o.d["net_autoconnect"] = True
        o.d["net_hubs"] = net.hub_entries(True, [])
        net.mint_identity(o)
        # the registration records the ADDRESS it was earned against (second-
        # wave contract): a cell without a matching address counts as unseen
        o.d["net_state"] = {net.LOCAL_HUB_ID: {
            "registered_at": "2026-01-01",
            "address": str(o.d["net_hubs"][0]["address"])}}
        store.save_org(o)
        block = net.status_block(store.load_org(org.d["slug"]).d)
        local = [h for h in block["hubs"] if h["id"] == net.LOCAL_HUB_ID][0]
        assert not local.get("hidden"), (
            "a hub that HAS registered must stay visible — offline is "
            "meaningful once you know it exists")
    check("…and visible for ever after, offline included", _visible_once_registered)

    def _explicit_remote_is_always_visible():
        org = mkorg(headless=False, persist=True)
        o = store.load_org(org.d["slug"])
        o.d["net_hubs"] = net.hub_entries(False, ["http://typed.test:7370"])
        net.mint_identity(o)
        store.save_org(o)
        block = net.status_block(store.load_org(org.d["slug"]).d)
        assert block["hubs"], block
        assert not any(h.get("hidden") for h in block["hubs"]), (
            "an explicitly typed remote must render even when it has never "
            "answered — the user asserted it exists")
    check("a typed remote hub is never hidden", _explicit_remote_is_always_visible)


def main() -> int:
    print("orgtree · headless orgs and per-org API keys (F-06 §9.5/§9.6)")
    sec_denials()
    sec_audit_trail()
    sec_couplings()
    sec_key_hygiene()
    sec_selectors()
    sec_prompt()
    sec_cred_watcher()
    sec_hidden_hub()

    print()
    if GAPS:
        print("findings (asserted inverted — they turn RED when fixed):")
        for label, why, saw in GAPS:
            print(f"  ⚑ {label}\n      why: {why}\n      saw: {saw}")
        print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"headless: {PASS} passed · {len(FAIL)} FAILED · {len(GAPS)} findings")
        return 1
    print(f"headless: all {PASS} checks passed"
          + (f" · {len(GAPS)} findings" if GAPS else ""))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
