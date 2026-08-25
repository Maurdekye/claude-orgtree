"""Account-token spawn seam — the injection, and WHICH identity served a turn.

    orgtree's `clean_env()` strips every `CLAUDE_CODE_*` variable, so the
    variable the CLI itself names for a long-lived token is one orgtree
    deletes from every spawn. The org's OWN token is re-injected after the
    strip; an AMBIENT one must still reach no agent.

Run:  python backend/tests/test_spawn_identity.py
      --only <sub>

No pytest, no network, no model calls. Every token value below is an
obviously-fake placeholder; nothing here reads or writes a real credential.

WHY THE CHECKS ARE SHAPED THE WAY THEY ARE
------------------------------------------
1. **THE ABSTENTION THIS FEATURE IS MOST LIKELY TO SHIP is a success-only
   check.** "The turn succeeded" is satisfied by the AMBIENT account, so a
   success-only check passes when the injection does nothing at all. Every
   check here therefore asserts on WHICH IDENTITY, never on success.

2. **ASSERT ON THE RESOLVED ENV DICT.** `spawn_env()` is the thing that
   decides; the org record is only an intention. A check that reads
   `org.d["account_token_uuid"]` would pass while the spawn carried nothing.

3. **THE ORDERING BUG IS SILENT AND LOOKS LIKE A WORKING FEATURE.** Injecting
   before `clean_env()` strips it again; the code still "sets" the variable
   and nothing errors. §2's ambient control is what catches it.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-spawnid-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)

from orgtree import (accounts as _accounts, ledger, store,  # noqa: E402
                     supervisor as sup, tokens)

_PROBE_N = [0]


def _probe_n() -> int:
    """A fresh slug per probe org — reusing one makes create_org raise, and a
    check that dies in its own setup reports as a failure of the thing it was
    meant to measure."""
    _PROBE_N[0] += 1
    return _PROBE_N[0]

# ⚠ AMBIENT ENVIRONMENT, PINNED BEFORE ANY CHECK RUNS. `live_account_uuid()`
# answers from the CLI's real config by default, so a suite that left it alone
# would be deciding "is this account the ambient one?" against whoever happens
# to be logged into the machine running it — green on the developer's box, and
# something else in a service. Every check that cares SETS it; this default
# means the ones that don't still cannot read a real login.
_accounts.LIVE_CONFIG = os.path.join(_TMP, "fake-claude-config.json")


def set_live_account(uuid: str | None) -> None:
    """Point `live_account_uuid()` at a fixture. `None` removes the file —
    the 'nobody is logged in / config unreadable' case."""
    if uuid is None:
        if os.path.exists(_accounts.LIVE_CONFIG):
            os.remove(_accounts.LIVE_CONFIG)
        return
    with open(_accounts.LIVE_CONFIG, "w", encoding="utf-8") as f:
        json.dump({"oauthAccount": {"accountUuid": uuid}}, f)


# obviously-fake, and shaped nothing like a real credential on purpose
FAKE_TOKEN = "FAKE-TOKEN-VALUE-NOT-A-CREDENTIAL-0001"
FAKE_TOKEN_2 = "FAKE-TOKEN-VALUE-NOT-A-CREDENTIAL-0002"
UUID_A = "acct-aaaa"
UUID_B = "acct-bbbb"
VAR = "CLAUDE_CODE_OAUTH_TOKEN"

_ARGS = sys.argv[1:]
ONLY = (_ARGS[_ARGS.index("--only") + 1].lower()
        if "--only" in _ARGS and len(_ARGS) > _ARGS.index("--only") + 1 else "")
PASS = 0
FAIL: list[tuple[str, str]] = []
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


class _FakeOrg:
    """Just enough Org for the pure decision functions: `.d` and `.nodes`.
    Same shape test_limit_freeze.py uses for `spawn_env` — a real Org here
    would need a data root and a hire, and `spawn_env` / `identity_in_env`
    read nothing else."""

    def __init__(self, **d):
        self.d = d
        self.nodes: dict[str, object] = {}


def _org(**d):
    return _FakeOrg(slug="spawnid", **d)


# ── §0 isolation ───────────────────────────────────────────────────────────
def s0_isolation() -> None:
    if not section("§0 isolation — this worktree, this throwaway data root"):
        return
    root = os.path.abspath(os.path.join(_HERE, ".."))
    check("supervisor under test is THIS worktree's", lambda: (
        None if os.path.abspath(sup.__file__).startswith(root)
        else (_ for _ in ()).throw(AssertionError(
            f"imported {sup.__file__} — not this tree's backend"))))
    check("tokens module under test is THIS worktree's", lambda: (
        None if os.path.abspath(tokens.__file__).startswith(root)
        else (_ for _ in ()).throw(AssertionError(
            f"imported {tokens.__file__} — not this tree's backend"))))
    # ⚠ assert on the RESOLVED path the code uses, never os.environ, and
    # prove it FOLLOWS the root rather than merely matching it once
    check("the token store resolves under the throwaway data root", lambda: (
        None if tokens.tokens_path().startswith(store.DATA_ROOT)
        else (_ for _ in ()).throw(AssertionError(
            f"{tokens.tokens_path()} is outside {store.DATA_ROOT}"))))

    def _follows():
        was = store.DATA_ROOT
        try:
            store.DATA_ROOT = os.path.join(_TMP, "moved")
            if not tokens.tokens_path().startswith(store.DATA_ROOT):
                raise AssertionError(
                    "tokens_path() did not follow DATA_ROOT — it is cached or "
                    "read from os.environ, so a test that moves the root is "
                    "silently testing the developer's real file")
        finally:
            store.DATA_ROOT = was
    check("CONTROL: tokens_path FOLLOWS a moved data root", _follows)

    def _ambient_config_is_a_fixture():
        """⚠ `live_account_uuid()` decides "is this the ambient account?" and
        by default it reads the CLI's REAL config. A suite that left it there
        would answer from whoever is logged into the machine — green on the
        developer's box for reasons that have nothing to do with the code.
        Assert on the RESOLVED path the module uses, not on an env var."""
        if not os.path.abspath(_accounts.LIVE_CONFIG).startswith(
                os.path.abspath(_TMP)):
            raise AssertionError(
                f"live config resolves to {_accounts.LIVE_CONFIG} — outside "
                "the throwaway root, so §7 would be measuring the real login")
        set_live_account("acct-probe-only")
        eq(_accounts.live_account_uuid(), "acct-probe-only")
        set_live_account(None)
        eq(_accounts.live_account_uuid(), "",
           "a missing config must read as unknown, not as a stale answer: ")
    check("the AMBIENT identity is a fixture, and it follows the fixture",
          _ambient_config_is_a_fixture)


# ── §1 the store ───────────────────────────────────────────────────────────
def s1_store() -> None:
    if not section("§1 the token store — separate, atomic, store-first"):
        return
    check("put/get round-trips", lambda: (
        tokens.put(UUID_A, FAKE_TOKEN), eq(tokens.get(UUID_A), FAKE_TOKEN)))
    check("a second account is independent", lambda: (
        tokens.put(UUID_B, FAKE_TOKEN_2),
        eq((tokens.get(UUID_A), tokens.get(UUID_B)),
           (FAKE_TOKEN, FAKE_TOKEN_2))))
    check("get on an unknown uuid is empty, not an error", lambda: (
        eq(tokens.get("nope"), "")))
    check("has() reflects presence", lambda: (
        eq((tokens.has(UUID_A), tokens.has("nope")), (True, False))))
    check("an empty token is refused", lambda: (
        _raises(ValueError, lambda: tokens.put("x", "  "))))
    check("an empty uuid is refused", lambda: (
        _raises(ValueError, lambda: tokens.put("", FAKE_TOKEN))))
    check("forget removes it", lambda: (
        tokens.put("gone", FAKE_TOKEN), eq(tokens.forget("gone"), True),
        eq(tokens.get("gone"), "")))

    # ⚠ THE STORE IS NOT THE REGISTRY. accounts.json's guard refuses
    # credential-shaped values, and this feature must not have relaxed it.
    def _separate():
        from orgtree import accounts
        if os.path.abspath(tokens.tokens_path()) == \
                os.path.abspath(accounts.registry_path()):
            raise AssertionError("tokens are being written INTO the registry")
        if os.path.exists(accounts.registry_path()):
            with open(accounts.registry_path(), encoding="utf-8") as f:
                body = f.read()
            if FAKE_TOKEN in body or FAKE_TOKEN_2 in body:
                raise AssertionError(
                    "A TOKEN REACHED accounts.json — the registry's guard "
                    "exists precisely to keep credential material out of the "
                    "file the panel serialises")
    check("tokens never reach the identity registry file", _separate)

    def _redacted():
        r = tokens.redacted()
        blob = repr(r)
        if FAKE_TOKEN in blob or FAKE_TOKEN_2 in blob:
            raise AssertionError("redacted() leaked the token value")
        # length is a real disclosure and buys a reader nothing
        if str(len(FAKE_TOKEN)) in blob:
            raise AssertionError("redacted() leaked the token LENGTH")
        if UUID_A not in r:
            raise AssertionError("redacted() does not report presence at all")
    check("redacted() reports presence only — no value, no length", _redacted)


def _raises(exc, fn):
    try:
        fn()
    except exc:
        return
    except BaseException as e:                         # noqa: BLE001
        raise AssertionError(f"expected {exc.__name__}, got {e!r}") from None
    raise AssertionError(f"expected {exc.__name__}, nothing raised")


# ── §2 the spawn seam — the resolved env, and the ambient control ──────────
def s2_spawn() -> None:
    if not section("§2 the spawn seam — resolved env, ambient control"):
        return
    tokens.put(UUID_A, FAKE_TOKEN)

    check("an org with NO account selected carries no token", lambda: (
        eq(sup.spawn_env(_org()).get(VAR), None)))
    check("an org WITH an account carries exactly that token", lambda: (
        eq(sup.spawn_env(_org(account_token_uuid=UUID_A)).get(VAR),
           FAKE_TOKEN)))
    check("selecting an account with NO stored token carries nothing",
          lambda: (eq(sup.spawn_env(_org(account_token_uuid="ghost")).get(VAR),
                      None)))

    # ⚠⚠ THE CONTROL THAT CATCHES THE ORDERING BUG AND THE CAPTURE BUG AT
    # ONCE. `clean_env()` strips CLAUDE_CODE_*; if the injection were placed
    # BEFORE the strip it would be silently removed, and if the strip were
    # relaxed to make this feature work, an ambient token in the BACKEND's
    # environment would capture every org — the host-level-API-key failure
    # all over again.
    def _ambient_denied():
        had = os.environ.get(VAR)
        os.environ[VAR] = "AMBIENT-MUST-NEVER-REACH-AN-AGENT"
        try:
            got = sup.spawn_env(_org()).get(VAR)
            if got is not None:
                raise AssertionError(
                    "AMBIENT CAPTURE: a token in the backend's own "
                    f"environment reached an agent's spawn ({got!r}). "
                    "clean_env()'s blanket CLAUDE_CODE_* strip has been "
                    "relaxed, and every org on this machine would now "
                    "silently inherit whatever the backend was started with.")
        finally:
            if had is None:
                os.environ.pop(VAR, None)
            else:
                os.environ[VAR] = had
    check("AMBIENT CONTROL: a host-level token reaches NO agent",
          _ambient_denied)

    def _ambient_not_confused_with_own():
        """…and the org's OWN token still wins when one is ambient."""
        had = os.environ.get(VAR)
        os.environ[VAR] = "AMBIENT-MUST-NEVER-REACH-AN-AGENT"
        try:
            got = sup.spawn_env(_org(account_token_uuid=UUID_A)).get(VAR)
            eq(got, FAKE_TOKEN, "org's own token must win: ")
        finally:
            if had is None:
                os.environ.pop(VAR, None)
            else:
                os.environ[VAR] = had
    check("the org's OWN token still wins over an ambient one",
          _ambient_not_confused_with_own)


# ── §3 identity attribution — which account SERVED the turn ───────────────
def s3_identity() -> None:
    if not section("§3 identity — which account actually served the turn"):
        return
    tokens.put(UUID_A, FAKE_TOKEN)

    check("ambient login reports 'ambient'", lambda: (
        eq(sup.identity_in_env(sup.spawn_env(o := _org()), o), "ambient")))
    check("an injected account reports ITS UUID", lambda: (
        eq(sup.identity_in_env(
            sup.spawn_env(o := _org(account_token_uuid=UUID_A)), o), UUID_A)))
    check("an api-key org reports 'api-key'", lambda: (
        eq(sup.identity_in_env(
            sup.spawn_env(o := _org(api_key="sk-fake-not-real")), o),
           "api-key")))

    # ⚠ THE POINT OF THE WHOLE SECTION. Intent and reality must be allowed to
    # DISAGREE, and when they do the answer must follow the ENV.
    def _follows_env_not_intent():
        o = _org(account_token_uuid="ghost")      # intends an account…
        env = sup.spawn_env(o)                    # …but no token exists
        got = sup.identity_in_env(env, o)
        if got == "ghost":
            raise AssertionError(
                "IDENTITY REPORTS INTENT, NOT REALITY: the org names an "
                "account whose token is absent, the spawn carries NO "
                "credential, and attribution still claims that account "
                "served the turn. Every wrong-account diagnosis built on "
                "this would be wrong in the same direction.")
        eq(got, "ambient")
    check("attribution follows the ENV when intent and reality disagree",
          _follows_env_not_intent)

    def _no_secret_in_attribution():
        o = _org(account_token_uuid=UUID_A)
        got = sup.identity_in_env(sup.spawn_env(o), o)
        if FAKE_TOKEN in got or FAKE_TOKEN[:12] in got:
            raise AssertionError("attribution leaked the token value")
    check("attribution never contains the token", _no_secret_in_attribution)

    def _unattributed_is_named():
        """A token present with no uuid must NOT read as 'ambient' — that
        would file a token-served turn under the ambient account."""
        o = _org()
        env = sup.spawn_env(o)
        env[VAR] = FAKE_TOKEN            # resolved env carries one
        got = sup.identity_in_env(env, o)
        if got == "ambient":
            raise AssertionError(
                "a turn that CARRIED a token is being attributed to the "
                "ambient account — the one attribution error that hides a "
                "switch entirely")
        eq(got, "token:unattributed")
    check("a token with no uuid is named, not filed as ambient",
          _unattributed_is_named)



# ── §4 the failover decision — pure, and the user's rules ─────────────────
def s4_failover() -> None:
    if not section("§4 failover — 401 stops, limits switch, one per turn"):
        return
    from orgtree import accounts
    tokens.put(UUID_A, FAKE_TOKEN)
    tokens.put(UUID_B, FAKE_TOKEN_2)
    accounts.save({"version": accounts.VERSION,
                   "accounts": {UUID_A: {"label": "A"}, UUID_B: {"label": "B"}},
                   "order": [UUID_A, UUID_B], "pins": {}})

    LIMIT = "Claude usage limit reached · try again in 3 hours"
    AUTH = {"is_error": True, "api_error_status": 401,
            "result": "Not logged in · Please run /login"}
    CLEAN: dict = {}

    def choice(**kw):
        kw.setdefault("res", CLEAN)
        kw.setdefault("err_blob", "")
        kw.setdefault("already_switched", False)
        return sup.failover_choice(_org(account_token_uuid=UUID_A), **kw)

    check("a usage limit switches to the alternate", lambda: (
        eq(choice(err_blob=LIMIT)[0], "switch")))
    check("the alternate is the OTHER account, not the current one", lambda: (
        eq(sup.alternate_account(_org(account_token_uuid=UUID_A)), UUID_B)))

    # ⚠ THE USER'S RULING, and the one most likely to be "simplified" later.
    def _401_stops():
        act, why = choice(res=AUTH, err_blob="the CLI exited 1")
        if act == "switch":
            raise AssertionError(
                "A 401 IS FAILING OVER. The user ruled that a rejected "
                "credential STOPS and tells them: silently switching lets a "
                "broken token sit dead for weeks, discovered only when the "
                "SECOND account fails too.")
        eq(act, "stop")
    check("a 401 STOPS — it never fails over", _401_stops)

    def _401_outranks_limit_prose():
        """A 401 whose PROSE also sounds like a limit must still stop. The
        number is evidence; the text is not."""
        res = {**AUTH, "result": "usage limit reached · try again in 3 hours"}
        act, _ = choice(res=res, err_blob=LIMIT)
        if act != "stop":
            raise AssertionError(
                "limit-sounding TEXT on a 401 turn caused a switch — the "
                "prose outranked the status code, which is the silent "
                "migration the ruling exists to prevent")
    check("a 401 outranks limit-sounding prose", _401_outranks_limit_prose)

    def _one_switch():
        act, _ = choice(err_blob=LIMIT, already_switched=True)
        if act != "none":
            raise AssertionError(
                "a turn that already switched is switching AGAIN — that is a "
                "retry loop across accounts, not one switch per turn")
    check("ONE switch per turn — a switched turn does not switch again",
          _one_switch)

    def _timeout_switches():
        """A dead token can HANG rather than fail (measured), so the timeout
        is the bound that notices."""
        act, why = choice(timed_out=True)
        eq(act, "switch")
        if "unknown" not in why:
            raise AssertionError(
                "a timeout switch must SAY the cause was unknown, not "
                "invent one — it is the case where we know least")
    check("a TIMEOUT counts as failure to serve and switches", _timeout_switches)

    def _timeout_still_bounded():
        eq(choice(timed_out=True, already_switched=True)[0], "none")
    check("a timeout does NOT escape the one-switch bound",
          _timeout_still_bounded)

    check("an ordinary failure switches nothing", lambda: (
        eq(choice(err_blob="TypeError: NoneType is not iterable")[0], "none")))

    def _no_alternate():
        """With no OTHER tokened account there is nothing to switch to, and
        saying 'switch' would strand the turn."""
        o = _org(account_token_uuid=UUID_A)
        tokens.forget(UUID_B)
        try:
            act, why = sup.failover_choice(o, res=CLEAN, err_blob=LIMIT,
                                           already_switched=False)
            eq(act, "none")
            if "no alternate" not in why:
                raise AssertionError("the reason must name the real cause")
        finally:
            tokens.put(UUID_B, FAKE_TOKEN_2)
    check("no tokened alternate → no switch, and it says why", _no_alternate)

    def _untokened_is_not_an_alternate():
        """An account we cannot authenticate as is not an alternative."""
        accounts.save({"version": accounts.VERSION,
                       "accounts": {UUID_A: {"label": "A"},
                                    "ghost": {"label": "G"}},
                       "order": [UUID_A, "ghost"], "pins": {}})
        try:
            got = sup.alternate_account(_org(account_token_uuid=UUID_A))
            if got == "ghost":
                raise AssertionError(
                    "an account with NO stored token was offered as the "
                    "failover target — the turn would be stranded on a "
                    "credential that does not exist")
            eq(got, "")
        finally:
            accounts.save({"version": accounts.VERSION,
                           "accounts": {UUID_A: {"label": "A"},
                                        UUID_B: {"label": "B"}},
                           "order": [UUID_A, UUID_B], "pins": {}})
    check("an account with no token is never the alternate",
          _untokened_is_not_an_alternate)

    def _pure():
        """PURE: deciding must not mutate the org or the store."""
        o = _org(account_token_uuid=UUID_A)
        before = dict(o.d)
        sup.failover_choice(o, res=AUTH, err_blob=LIMIT, already_switched=False)
        sup.failover_choice(o, res=CLEAN, err_blob=LIMIT, already_switched=False)
        if dict(o.d) != before:
            raise AssertionError(
                "failover_choice MUTATED the org — deciding and acting must "
                "stay separable or the decision cannot be tested at all")
    check("the decision is pure — it changes nothing", _pure)



# -- section 5: the drive nudge must stay subject-free ---------------------
def s5_drive_text() -> None:
    if not section("S5 the drive nudge - subject-free, invariant, still loud"):
        return

    # The only re-drive mechanism also deposits MAIL, and the recipient may be
    # a fable-tier agent. Credential/capacity SUBJECT MATTER in mail is what
    # has repeatedly destroyed sessions here - the trigger is the subject, not
    # any secret value. These two checks are the whole defence.
    REASONS = [
        "the account is out of capacity",
        "the account did not serve the turn and the cause is unknown "
        "(timed out with no result)",
        "the credential was rejected (401)",
        "",
        "Claude usage limit reached - try again in 3 hours",
    ]

    def _invariant():
        texts = {sup.switch_drive_text(r) for r in REASONS}
        if len(texts) != 1:
            raise AssertionError(
                "THE DRIVE NUDGE VARIES BY REASON. It must be the same bytes "
                "whatever caused the switch - a per-reason variant is how the "
                "subject gets into a mailbox, one helpful improvement at a "
                f"time. Got {len(texts)} distinct strings: {sorted(texts)!r}")
    check("the nudge is INVARIANT across every switch reason", _invariant)

    def _vocabulary():
        # the subject words, and their obvious neighbours
        DENY = ("account", "credential", "token", "auth", "limit", "quota",
                "billing", "subscription", "capacity", "401", "login",
                "sign in", "signed in", "api key", "rejected", "expired")
        text = sup.switch_drive_text("the account is out of capacity").lower()
        hits = [w for w in DENY if w in text]
        if hits:
            raise AssertionError(
                f"THE DRIVE NUDGE NAMES THE SUBJECT: {hits}. This string goes "
                "into an agent mailbox and the agent may be fable-tier. Say "
                "'go again' and put the reason in the durable record.")
    check("the nudge contains none of the subject vocabulary", _vocabulary)

    def _control_the_denylist_can_fire():
        """POSITIVE CONTROL: a denylist that matched nothing would pass on an
        empty string too. Prove it fires on text that DOES name the subject."""
        DENY = ("account", "credential", "token")
        bad = "switching account because the token was rejected".lower()
        if not [w for w in DENY if w in bad]:
            raise AssertionError(
                "the denylist does not fire even on text that plainly names "
                "the subject, so the check above proves nothing")
    check("CONTROL: the denylist fires on subject-naming text",
          _control_the_denylist_can_fire)

    def _not_empty():
        """...and it must still SAY something: an empty nudge would drive a
        turn with no instruction at all."""
        if len(sup.ACCOUNT_SWITCH_DRIVE.strip()) < 20:
            raise AssertionError(
                "the nudge is empty or near-empty - subject-free must not "
                "mean contentless; the agent still needs telling to go again")
    check("the nudge still tells the agent to continue", _not_empty)



# -- section 6: the WIRING, read off the AST ------------------------------
def s6_wiring() -> None:
    if not section("S6 the wiring - only the limit path changed"):
        return
    import ast
    src = open(sup.__file__, encoding="utf-8").read()
    tree = ast.parse(src)

    def calls(name):
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == name]

    def _wired():
        cs = calls("apply_failover")
        if not cs:
            raise AssertionError(
                "apply_failover is never called - the failover is NOT wired, "
                "and every containment check below is passing vacuously "
                "because nothing happens at all")
        # ⚠ "A CALL EXISTS" IS NOT "A CALL RUNS". This check first asserted
        # only that a call node existed, and a mutant writing
        # `if False and apply_failover(...)` SURVIVED it - the feature was
        # dead-coded and the positive control still passed, which is the exact
        # abstention shape this suite exists to hunt. Reject any call whose
        # ancestry is gated on a literal False.
        parent = {}
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n):
                parent[c] = n
        live = []
        for call in cs:
            cur, dead = call, False
            while cur in parent:
                up = parent[cur]
                test = getattr(up, "test", None) if isinstance(up, ast.If) else None
                for node in ([test] if test is not None else []) + (
                        [up] if isinstance(up, ast.BoolOp) else []):
                    if any(isinstance(x, ast.Constant) and x.value is False
                           for x in ast.walk(node)):
                        dead = True
                if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    break
                cur = up
            if not dead:
                live.append(call.lineno)
        if not live:
            raise AssertionError(
                "every apply_failover call is DEAD-CODED behind a literal "
                "False - the call exists but can never run, so the failover "
                "is wired in appearance only and a limit still just freezes")
    check("POSITIVE CONTROL: apply_failover is wired AND can actually run",
          _wired)

    def _only_limit_path():
        """The containment claim, mechanically. Every apply_failover call must
        sit inside an `if` whose test mentions the usage-limit predicate - so
        a failure that is not a limit cannot reach it."""
        parent = {}
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n):
                parent[c] = n
        for call in calls("apply_failover"):
            cur, guarded = call, False
            while cur in parent:
                up = parent[cur]
                if isinstance(up, ast.If):
                    names = {x.id for x in ast.walk(up.test)
                             if isinstance(x, ast.Name)}
                    if "_looks_like_usage_limit" in names:
                        guarded = True
                        break
                if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    break
                cur = up
            if not guarded:
                raise AssertionError(
                    "BEHAVIOUR SPREAD BEYOND THE LIMIT PATH: apply_failover at "
                    f"line {call.lineno} is not inside a usage-limit branch, "
                    "so failures that are NOT limits can now switch accounts. "
                    "The containment was the whole basis for calling this "
                    "change safe.")
    check("apply_failover is reachable ONLY from the usage-limit branch",
          _only_limit_path)

    def _bound_cleared_only_on_success():
        """The one-switch bound must be cleared by a COMPLETED turn only."""
        clears = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Subscript)
                          and isinstance(t.slice, ast.Constant)
                          and t.slice.value == "switched_account"
                          for t in n.targets)
                  and isinstance(n.value, ast.Constant)
                  and n.value.value is False]
        if len(clears) != 1:
            raise AssertionError(
                f"the switch bound is cleared in {len(clears)} places; it must "
                "be cleared ONLY by a completed turn. Clearing it per-failure "
                "lets a node ping-pong between accounts forever - the "
                "retry-against-a-dead-credential shape twice believed ruled out")
    check("the one-switch bound is cleared in exactly ONE place",
          _bound_cleared_only_on_success)

    def _switch_passes_no_reason_to_mail():
        """apply_failover must be the ONLY thing driving after a switch, and
        the drive text must still come from the constant."""
        body = None
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "apply_failover":
                body = n
        if body is None:
            raise AssertionError("apply_failover is gone")
        for call in [c for c in ast.walk(body)
                     if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Name)
                     and c.func.id == "send_message"]:
            arg = call.args[2] if len(call.args) > 2 else None
            ok = (isinstance(arg, ast.Call)
                  and isinstance(arg.func, ast.Name)
                  and arg.func.id == "switch_drive_text")
            if not ok:
                raise AssertionError(
                    "the switch drive no longer sends switch_drive_text() - "
                    "something else is being put in an agent's MAILBOX, and "
                    "that is how the subject reaches a fable seat")
    check("the drive still sends only switch_drive_text()",
          _switch_passes_no_reason_to_mail)


# ── §7 the ambient account is not an alternate; the refusal is loud ────────
def s7_no_op_switch() -> None:
    """The 2026-08-24 21:20Z incident, pinned.

    The failover fired for real, printed a confident `account switched` row,
    re-drove the turn — and the re-driven turn hit the IDENTICAL session limit
    4.2 seconds later, because the account it switched to was the account the
    machine was already signed in as. `alternate_account` had compared against
    `account_token_uuid`, which is "" for an org running on ambient, so every
    account counted as "not the current one" — including the ambient one that
    passive adoption had itself put in the registry.

    ⚠ EVERY CHECK HERE COMES IN TWO LEGS. A one-legged version of this fix
    passes by refusing EVERYTHING, which disables failover entirely while
    looking careful — the same abstention shape as a check that never runs.
    """
    if not section("§7 no-op switch — ambient is not an alternate"):
        return
    import ast
    from orgtree import accounts

    def _registry(*uuids):
        accounts.save({"version": accounts.VERSION,
                       "accounts": {u: {"label": u} for u in uuids},
                       "order": list(uuids), "pins": {}})

    LIMIT = "Claude usage limit reached · try again in 3 hours"

    def _ambient_is_not_an_alternate():
        """THE BUG. Org on ambient, one tokened account, and it IS ambient."""
        _registry(UUID_A)
        tokens.put(UUID_A, FAKE_TOKEN)
        set_live_account(UUID_A)
        got = sup.alternate_account(_org())          # no account_token_uuid
        if got == UUID_A:
            raise AssertionError(
                "THE ORG WOULD FAIL OVER TO THE ACCOUNT IT IS ALREADY "
                "RUNNING AS. `cur == \"\"` means 'serving the AMBIENT "
                "account', not 'serving nothing' — so the ambient account is "
                "not an alternative to itself. This is the live 21:20Z "
                "failure: same subscription, same limit, a re-drive burned "
                "and a row claiming capacity was restored.")
        eq(got, "")
    check("the AMBIENT account is never the alternate", _ambient_is_not_an_alternate)

    def _but_a_real_alternate_still_wins():
        """LEG TWO. Excluding ambient must not exclude everything — a fix that
        always returns "" passes the check above and breaks failover."""
        _registry(UUID_A, UUID_B)
        tokens.put(UUID_A, FAKE_TOKEN)
        tokens.put(UUID_B, FAKE_TOKEN_2)
        set_live_account(UUID_A)
        got = sup.alternate_account(_org())          # still on ambient
        if got != UUID_B:
            raise AssertionError(
                "a GENUINELY different tokened account was not offered "
                f"(got {got!r}) — the ambient exclusion has swallowed the "
                "whole feature, which is failover disabled wearing the "
                "costume of a fix")
    check("CONTROL: a genuinely different account IS still the alternate",
          _but_a_real_alternate_still_wins)

    def _ambient_is_fine_when_it_is_not_what_we_use():
        """The exclusion is 'not the identity ALREADY SERVING', not 'never
        ambient'. An org pinned to A whose machine is signed in as B may
        absolutely fail over to B — that is a real change of account."""
        _registry(UUID_A, UUID_B)
        set_live_account(UUID_B)
        got = sup.alternate_account(_org(account_token_uuid=UUID_A))
        if got != UUID_B:
            raise AssertionError(
                "the ambient account was refused as a target even though the "
                f"org is serving a DIFFERENT one (got {got!r}) — the rule is "
                "about the identity in use, not about ambient as a category")
    check("ambient IS a valid target when the org serves something else",
          _ambient_is_fine_when_it_is_not_what_we_use)

    def _unknown_ambient_degrades_rather_than_refusing():
        """DOCUMENTED GAP, pinned so it cannot be 'tidied' into a refusal.
        With no readable config there is no ambient identity to compare
        against — and if nobody is logged in, a token beats no credential."""
        _registry(UUID_A)
        set_live_account(None)
        eq(sup.alternate_account(_org()), UUID_A)
    check("an unreadable live config degrades to the old comparison",
          _unknown_ambient_degrades_rather_than_refusing)

    def _refusal_says_the_real_cause():
        _registry(UUID_A)
        tokens.forget(UUID_B)
        set_live_account(UUID_A)
        act, why = sup.failover_choice(_org(), res={}, err_blob=LIMIT,
                                       already_switched=False)
        if act == "switch":
            raise AssertionError(
                "a limit on an org whose ONLY tokened account is the one "
                "already serving it still decides to SWITCH — the no-op the "
                "whole section exists to stop")
        eq(act, "none")
        if sup.NO_ALTERNATE not in why:
            raise AssertionError(
                "the refusal does not carry NO_ALTERNATE, so the turn loop "
                f"cannot tell it from 'not an account problem': {why!r}")
        if "already serving" not in why:
            raise AssertionError(
                "the refusal does not distinguish 'the only token belongs to "
                "the account already serving us' from 'nobody has a token' — "
                f"those need different fixes by the user: {why!r}")
        tokens.put(UUID_B, FAKE_TOKEN_2)
    check("a no-op switch is REFUSED and names the real cause",
          _refusal_says_the_real_cause)

    def _refusal_leg_two_a_real_limit_still_switches():
        """LEG TWO, at the decision level. Coordinator's explicit
        requirement: always-refuse would pass the check above."""
        _registry(UUID_A, UUID_B)
        tokens.put(UUID_A, FAKE_TOKEN)
        tokens.put(UUID_B, FAKE_TOKEN_2)
        set_live_account(UUID_A)
        act, _why = sup.failover_choice(_org(), res={}, err_blob=LIMIT,
                                        already_switched=False)
        if act != "switch":
            raise AssertionError(
                f"a limit with a real alternate available decided {act!r} — "
                "the refusal path is now swallowing genuine failovers")
    check("CONTROL: a limit with a real alternate still SWITCHES",
          _refusal_leg_two_a_real_limit_still_switches)

    # ── the refusal must leave a durable trace ────────────────────────────
    def _refusal_is_durable():
        """A refusal that writes nothing is indistinguishable from never
        having considered switching — the abstention this team keeps
        shipping."""
        org = store.create_org(f"zz noopswitch {_probe_n()}")
        r = org.hire(ledger.USER, None, "haiku", 20, "probe", add_dirs=[],
                     tools={"bash": False, "web": False, "edit": False,
                            "subagents": False, "mcp": []},
                     org_visibility="team", charter="refusal probe")
        store.save_org(org)
        slug, nid = org.d["slug"], r["node"]
        wrote = sup.log_failover_refusal(slug, nid, sup.NO_ALTERNATE + " — x")
        rows = (store.load_org(slug).d.get("turn_error_log") or {}).get(nid, [])
        if not wrote or not rows:
            raise AssertionError(
                "the refusal left NO durable row — after the fact there is "
                "then no way to tell 'we had nowhere to go' from 'this "
                "failure had nothing to do with accounts'")
        if "no account switch" not in (rows[-1].get("text") or ""):
            raise AssertionError(
                f"the durable row does not say a switch was declined: {rows[-1]!r}")
        return slug, nid
    check("the refusal writes a DURABLE row", _refusal_is_durable)

    def _refusal_sends_no_mail():
        """The refusal must not re-drive. The drive mechanism deposits mail,
        the recipient may be fable-tier, and capacity subject matter in a
        mailbox is what kills those sessions — `apply_failover` needs a
        fixed subject-free constant to stay safe, and this path simply must
        never send. (It also has nothing to say: nothing was fixed.)"""
        tree = ast.parse(open(sup.__file__, encoding="utf-8").read())
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "log_failover_refusal"), None)
        if fn is None:
            raise AssertionError("log_failover_refusal is gone")
        BAD = {"send_message", "notify", "apply_failover", "send_notice"}
        hit = [c.func.id for c in ast.walk(fn)
               if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id in BAD]
        if hit:
            raise AssertionError(
                f"the refusal path calls {hit} — it must write the record and "
                "STOP. A re-drive here burns a turn on the same wall, and "
                "the drive mechanism puts capacity subject matter in a "
                "mailbox that may belong to a fable seat.")
    check("the refusal drives nothing and mails nobody", _refusal_sends_no_mail)

    def _control_the_ast_denylist_can_fire():
        """POSITIVE CONTROL: the scan above would also pass on a function that
        does not exist, or on a denylist that matches nothing. Prove it fires
        on a function that DOES call one of them."""
        tree = ast.parse(open(sup.__file__, encoding="utf-8").read())
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "apply_failover"), None)
        BAD = {"send_message", "notify", "apply_failover", "send_notice"}
        hit = [c.func.id for c in ast.walk(fn) if fn is not None
               and isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id in BAD]
        if not hit:
            raise AssertionError(
                "the denylist finds nothing even in apply_failover, which "
                "plainly calls send_message and notify — so the check above "
                "proves nothing about the refusal path")
    check("CONTROL: the drive denylist fires on a path that DOES drive",
          _control_the_ast_denylist_can_fire)

    def _refusal_is_wired_and_can_run():
        """A call that exists is not a call that runs (b0dc223's lesson), and
        it must sit inside the usage-limit branch or the refusal row would
        appear on failures that were never about accounts."""
        src = open(sup.__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        parent = {}
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n):
                parent[c] = n
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "log_failover_refusal"]
        if not calls:
            raise AssertionError(
                "log_failover_refusal is NEVER CALLED — the loud refusal is "
                "a function nobody runs, and every check above it passes "
                "while the machine stays as silent as it was")
        live_guarded = []
        for call in calls:
            cur, dead, guarded = call, False, False
            while cur in parent:
                up = parent[cur]
                if isinstance(up, ast.If):
                    names = {x.id for x in ast.walk(up.test)
                             if isinstance(x, ast.Name)}
                    if "_looks_like_usage_limit" in names:
                        guarded = True
                    if any(isinstance(x, ast.Constant) and x.value is False
                           for x in ast.walk(up.test)):
                        dead = True
                if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    break
                cur = up
            if not dead and guarded:
                live_guarded.append(call.lineno)
        if not live_guarded:
            raise AssertionError(
                "every log_failover_refusal call is dead-coded or sits "
                "outside the usage-limit branch — so either the refusal can "
                "never run, or it can fire on failures that have nothing to "
                "do with accounts")
    check("the refusal is WIRED into the limit path and can actually run",
          _refusal_is_wired_and_can_run)


# ── §8 ran_as, made durable ────────────────────────────────────────────────
def s8_durable_ran_as() -> None:
    """`ran_as` was in-memory and per-node: overwritten by the next spawn,
    gone on restart. After the 21:20Z failover the ONE turn worth attributing
    — the re-driven one — was already unrecoverable when anyone looked, and a
    turn that fails on a limit writes no ring entry at all, so the error row
    is the only durable trace it leaves."""
    if not section("§8 ran_as survives the process"):
        return
    import ast

    def _org_with_node():
        org = store.create_org(f"zz ranas durable {_probe_n()}")
        r = org.hire(ledger.USER, None, "haiku", 20, "probe", add_dirs=[],
                     tools={"bash": False, "web": False, "edit": False,
                            "subagents": False, "mcp": []},
                     org_visibility="team", charter="ran_as probe")
        store.save_org(org)
        return org.d["slug"], r["node"]

    def _error_row_carries_it():
        slug, nid = _org_with_node()
        sup.state(slug, nid)["ran_as"] = UUID_B
        sup._log_turn_error(slug, nid, "turn failed: pretend limit")
        rows = (store.load_org(slug).d.get("turn_error_log") or {}).get(nid, [])
        if not rows or rows[-1].get("ran_as") != UUID_B:
            raise AssertionError(
                "the durable failure row does not record WHICH ACCOUNT ran "
                f"the turn ({rows[-1] if rows else None!r}) — which is "
                "exactly the question the 21:20Z post-mortem could not "
                "answer, because a failed turn writes no ring entry either")
    check("a durable failure row records the account that served the turn",
          _error_row_carries_it)

    def _absent_not_guessed():
        """A node that has not run in THIS process must leave the key absent.
        A default of 'ambient' would be a fabricated measurement, and
        'ambient' is precisely the answer a post-mortem must not be handed
        by accident."""
        slug, nid = _org_with_node()
        sup.state(slug, nid).pop("ran_as", None)
        sup._log_turn_error(slug, nid, "turn failed: no spawn in this process")
        rows = (store.load_org(slug).d.get("turn_error_log") or {}).get(nid, [])
        if "ran_as" in (rows[-1] or {}):
            raise AssertionError(
                f"an unrun node was attributed to {rows[-1]['ran_as']!r} — "
                "absent must stay absent; an invented 'ambient' here reads "
                "exactly like the failure mode we are hunting")
    check("a node that never spawned records NO account, not a guess",
          _absent_not_guessed)

    def _stamp_follows_state_not_intent():
        """BOTH LEGS: the stamp must track the resolved spawn identity, so it
        has to change when that changes. A constant passes a single-leg
        check."""
        slug, nid = _org_with_node()
        seen = []
        for want in (UUID_A, "ambient"):
            sup.state(slug, nid)["ran_as"] = want
            e: dict = {"at": "x", "cost": 0.0, "denials": 0}
            sup._stamp_ran_as(e, slug, nid)          # type: ignore[arg-type]
            seen.append(e.get("ran_as"))
        if seen != [UUID_A, "ambient"]:
            raise AssertionError(
                f"the ring stamp does not follow the spawn identity: {seen!r} "
                "— a stamp that always says the same thing is a constant "
                "wearing a measurement's clothes")
    check("the ring stamp FOLLOWS the resolved identity (both legs)",
          _stamp_follows_state_not_intent)

    def _never_a_credential():
        slug, nid = _org_with_node()
        sup.state(slug, nid)["ran_as"] = UUID_A
        e: dict = {"at": "x", "cost": 0.0, "denials": 0}
        sup._stamp_ran_as(e, slug, nid)              # type: ignore[arg-type]
        blob = repr(e)
        if FAKE_TOKEN in blob or FAKE_TOKEN_2 in blob:
            raise AssertionError("a token value reached the durable ring")
    check("the durable attribution is never credential material",
          _never_a_credential)

    def _every_ring_author_stamps():
        """The ring has three authors — completed, killed, and
        reported-then-failed — and the turns worth attributing are exactly
        the ones that did NOT complete. Stamping only the happy path yields a
        record that is complete precisely when nobody needs it."""
        tree = ast.parse(open(sup.__file__, encoding="utf-8").read())
        missing = []
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            writes_ring = any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "setdefault" and len(c.args) == 2
                and isinstance(c.args[0], ast.Constant)
                and c.args[0].value == "turns"
                for c in ast.walk(fn))
            if not writes_ring:
                continue
            stamps = any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                         and c.func.id == "_stamp_ran_as" for c in ast.walk(fn))
            if not stamps:
                missing.append(fn.name)
        # `_charge_killed_turn` READS the ring to estimate; it also appends.
        if missing:
            raise AssertionError(
                f"these ring authors never stamp the account: {missing} — "
                "the entries they write are the failed turns, which are the "
                "only ones a post-mortem cares about")
    check("EVERY turns-ring author stamps the account, not just the happy one",
          _every_ring_author_stamps)

    def _control_the_ring_scan_finds_authors():
        """POSITIVE CONTROL: the scan above passes trivially if it finds no
        ring authors at all — e.g. if the ring is renamed."""
        tree = ast.parse(open(sup.__file__, encoding="utf-8").read())
        authors = [n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and any(isinstance(c, ast.Call)
                           and isinstance(c.func, ast.Attribute)
                           and c.func.attr == "setdefault" and len(c.args) == 2
                           and isinstance(c.args[0], ast.Constant)
                           and c.args[0].value == "turns"
                           for c in ast.walk(n))]
        if len(authors) < 3:
            raise AssertionError(
                f"found only {authors} turns-ring authors — the check above "
                "is passing because it is scanning for something that is no "
                "longer there, not because every author stamps")
    check("CONTROL: the ring scan actually finds all three authors",
          _control_the_ring_scan_finds_authors)


# ── §9 the way back — a selection that can be set AND cleared ──────────────
def s9_selection_writers() -> None:
    """Until `set_account_selection` existed, `apply_failover` was the ONLY
    writer of the org's selection, and it could only ever move to another
    tokened account. A transient capacity event therefore became a permanent
    configuration change nobody chose: the 21:20Z no-op switch pinned this
    org to an account, the user later signed in as a genuinely different one,
    and the org went on serving the old token — correctly, visibly, and with
    no way back.

    ⚠ EVERY CHECK HERE IS A ROUND TRIP, NOT A SINGLE MOVE. A one-way state
    machine is exactly what we are fixing, and a check that only ever sets
    would pass on a writer that cannot clear — which is the bug wearing the
    fix's clothes. `set → clear → set` with the SERVED IDENTITY read back
    each time is the shape; asserting on `org.d` would prove only that we can
    write our own intent down.
    """
    if not section("§9 selection — set, clear, and set again"):
        return
    from orgtree import accounts

    accounts.save({"version": accounts.VERSION,
                   "accounts": {UUID_A: {"label": "A"}, UUID_B: {"label": "B"}},
                   "order": [UUID_A, UUID_B], "pins": {}})
    tokens.put(UUID_A, FAKE_TOKEN)
    tokens.put(UUID_B, FAKE_TOKEN_2)

    def _fresh_org():
        org = store.create_org(f"zz selection {_probe_n()}")
        r = org.hire(ledger.USER, None, "haiku", 20, "probe", add_dirs=[],
                     tools={"bash": False, "web": False, "edit": False,
                            "subagents": False, "mcp": []},
                     org_visibility="team", charter="selection probe")
        store.save_org(org)
        return org.d["slug"], r["node"]

    def _served(slug):
        """⚠ THE RESOLVED IDENTITY, the same way attribution and the panel
        resolve it — build the real spawn env and ask. Reading
        `org.d["account_token_uuid"]` would assert that we can write down our
        own intention, which no bug in this area has ever been about."""
        o = store.load_org(slug)
        return sup.identity_in_env(sup.spawn_env(o), o)

    def _round_trip():
        slug, _ = _fresh_org()
        set_live_account("acct-ambient-not-in-registry")
        seen = [_served(slug)]                      # fresh org: ambient
        sup.set_account_selection(slug, UUID_A)
        seen.append(_served(slug))
        sup.set_account_selection(slug, "")         # ← the way back
        seen.append(_served(slug))
        sup.set_account_selection(slug, UUID_B)
        seen.append(_served(slug))
        if seen != ["ambient", UUID_A, "ambient", UUID_B]:
            raise AssertionError(
                f"the served identity did not follow set → clear → set: "
                f"{seen!r}. If position 3 is not 'ambient' the org cannot be "
                "returned to the signed-in account, which is the one-way "
                "ratchet this whole section exists to break.")
    check("set → clear → set: the SERVED identity follows every move",
          _round_trip)

    def _cleared_means_ambient_not_nothing():
        """⚠ THE LEG THAT CANNOT BE SKIPPED. 'Cleared' must mean 'serves the
        signed-in account', not 'serves no identity at all'. A writer that
        cleared the selection AND left the spawn with no credential would
        pass a check that only asserted the selection was gone, and would
        strand every turn."""
        slug, _ = _fresh_org()
        sup.set_account_selection(slug, UUID_A)
        sup.set_account_selection(slug, "")
        o = store.load_org(slug)
        env = sup.spawn_env(o)
        if env.get(VAR):
            raise AssertionError(
                "a CLEARED org still carries an account token in its spawn "
                "env — the selection was forgotten but the credential was "
                "not, so 'back to ambient' is a lie the env contradicts")
        eq(sup.identity_in_env(env, o), "ambient",
           "a cleared org must serve the signed-in account: ")
    check("a CLEARED org falls back to ambient, not to no identity at all",
          _cleared_means_ambient_not_nothing)

    def _clear_is_idempotent_and_silent():
        """Clearing an org already on ambient succeeds and DOES NOT WRITE. A
        state machine that throws at its own resting state is the same class
        of bug as one that only moves forwards. Asserted on the bytes on
        disk, because 'did not write' is not observable from the return."""
        slug, _ = _fresh_org()
        path = store.org_path(slug)
        before = open(path, "rb").read()
        got = sup.set_account_selection(slug, "")     # already ambient
        after = open(path, "rb").read()
        eq(got, "", "clearing an ambient org must report the ambient state: ")
        if after != before:
            raise AssertionError(
                "clearing an org that was ALREADY on ambient rewrote the org "
                "document — a no-op that writes will also log an event, so "
                "the audit trail fills with changes that never happened")
    check("clearing an already-ambient org succeeds and writes NOTHING",
          _clear_is_idempotent_and_silent)

    def _reselect_is_idempotent_and_silent():
        """Same rule in the other direction — the resting state is wherever
        the org currently is, not only ambient."""
        slug, _ = _fresh_org()
        sup.set_account_selection(slug, UUID_A)
        path = store.org_path(slug)
        before = open(path, "rb").read()
        got = sup.set_account_selection(slug, UUID_A)
        if open(path, "rb").read() != before or got != UUID_A:
            raise AssertionError(
                "re-selecting the account already selected rewrote the org "
                "document — idempotence has to hold at every resting state, "
                "not just at ambient")
    check("re-selecting the CURRENT account writes NOTHING either",
          _reselect_is_idempotent_and_silent)

    def _cleared_is_one_spelling_not_two():
        """⚠ Clearing REMOVES the key. A cleared org must be indistinguishable
        from one that was never selected — two spellings of 'on ambient' is
        the `is None` ambiguity that has bitten this codebase before, and it
        is what makes a later `if "account_token_uuid" in doc` read wrong."""
        virgin, _ = _fresh_org()
        used, _ = _fresh_org()
        sup.set_account_selection(used, UUID_A)
        sup.set_account_selection(used, "")
        a = "account_token_uuid" in store.load_org(virgin).d
        b = "account_token_uuid" in store.load_org(used).d
        if (a, b) != (False, False):
            raise AssertionError(
                f"a cleared org is not shaped like a never-selected one "
                f"(virgin has key: {a}, cleared has key: {b}) — 'on ambient' "
                "now has two spellings")
    check("a cleared org is byte-shaped like one never selected",
          _cleared_is_one_spelling_not_two)

    def _unknown_account_is_refused_loudly():
        """A selection that fails to apply looks exactly like one that
        applied — the pin endpoint's own rule, and the reason this raises."""
        slug, _ = _fresh_org()
        sup.set_account_selection(slug, UUID_A)
        for bad, why in (("acct-nonexistent", "not in the registry"),
                         ("ghost-untokened", "no stored token")):
            if bad == "ghost-untokened":
                accounts.save({"version": accounts.VERSION,
                               "accounts": {UUID_A: {"label": "A"},
                                            UUID_B: {"label": "B"},
                                            bad: {"label": "G"}},
                               "order": [UUID_A, UUID_B, bad], "pins": {}})
            try:
                sup.set_account_selection(slug, bad)
            except sup.UnknownAccount:
                pass
            else:
                raise AssertionError(
                    f"selecting {bad!r} ({why}) was ACCEPTED — every turn "
                    "would then spawn on a credential that does not exist, "
                    "and the panel would confidently show the account it "
                    "cannot authenticate as")
        eq(_served(slug), UUID_A,
           "a REFUSED selection must leave the previous one intact: ")
    check("an unknown or untokened account is refused, and changes nothing",
          _unknown_account_is_refused_loudly)

    def _the_change_is_durably_recorded():
        """Who changed which identity serves this org, and when. The failover
        writes a per-NODE row because it explains one turn; a selection is
        org-level and has to be answerable after the fact from the org's own
        audit log."""
        slug, _ = _fresh_org()
        sup.set_account_selection(slug, UUID_A)
        sup.set_account_selection(slug, "")
        evs = [e for e in store.load_org(slug).d.get("events", [])
               if e.get("op") == "account_selection"]
        if len(evs) != 2:
            raise AssertionError(
                f"expected two recorded selection changes, got {len(evs)} — "
                "an identity change that leaves no trace is exactly how the "
                "21:20Z selection became a mystery six hours later")
        eq([(e["detail"]["from"], e["detail"]["to"]) for e in evs],
           [(None, UUID_A), (UUID_A, None)])
        blob = repr(evs)
        if FAKE_TOKEN in blob or FAKE_TOKEN_2 in blob:
            raise AssertionError("a token value reached the org audit log")
    check("every selection change is durably recorded, credential-free",
          _the_change_is_durably_recorded)

    def _endpoint_contract():
        """The panel drives this, so the CALL is the deliverable. Exercised
        through the real ASGI app — a check that called the supervisor
        directly would prove nothing about the route the panel will use."""
        from fastapi.testclient import TestClient
        from orgtree import api
        slug, _ = _fresh_org()
        c = TestClient(api.app)
        r = c.put(f"/api/accounts/selection/{slug}", json={"uuid": UUID_A})
        eq(r.status_code, 200)
        eq(r.json()["serving"], UUID_A)
        eq(r.json()["selection"], UUID_A)
        r = c.put(f"/api/accounts/selection/{slug}", json={"uuid": None})
        eq(r.status_code, 200)
        eq((r.json()["serving"], r.json()["selection"]), ("ambient", None),
           "clearing through the endpoint must report ambient: ")
        eq(c.put(f"/api/accounts/selection/{slug}",
                 json={"uuid": "acct-nonexistent"}).status_code, 422)
        eq(c.put("/api/accounts/selection/zz-no-such-org",
                 json={"uuid": None}).status_code, 404)
        # …and the GET agrees with the PUT, or the panel reads two truths
        eq(c.get(f"/api/accounts/serving/{slug}").json(),
           {"serving": "ambient", "label": "the signed-in account",
            "selection": None})
    check("the ENDPOINT round-trips, and GET agrees with PUT",
          _endpoint_contract)

    def _response_is_resolved_not_echoed():
        """⚠ The response must report what WOULD SERVE, not what was asked
        for. They can disagree — a selection whose token is later deleted
        resolves to ambient — and an echo would confidently show an account
        that authenticates nothing. This is the same rule `ran_as` follows."""
        from fastapi.testclient import TestClient
        from orgtree import api
        slug, _ = _fresh_org()
        c = TestClient(api.app)
        c.put(f"/api/accounts/selection/{slug}", json={"uuid": UUID_B})
        tokens.forget(UUID_B)                    # the token vanishes underneath
        try:
            body = c.get(f"/api/accounts/serving/{slug}").json()
            if body["serving"] == UUID_B:
                raise AssertionError(
                    "the response echoed the SELECTION for an account whose "
                    "token is gone — the panel would show an identity no "
                    "spawn can authenticate as")
            eq(body["serving"], "ambient")
            eq(body["selection"], UUID_B,
               "intent and resolved fact must both be visible: ")
        finally:
            tokens.put(UUID_B, FAKE_TOKEN_2)
    check("the response is the RESOLVED identity, not an echo of the request",
          _response_is_resolved_not_echoed)


def main() -> None:
    t0 = time.perf_counter()
    print(f"data root: {store.DATA_ROOT}")
    print(f"token store: {tokens.tokens_path()}")
    for fn in (s0_isolation, s1_store, s2_spawn, s3_identity,
               s4_failover, s5_drive_text, s6_wiring,
               s7_no_op_switch, s8_durable_ran_as, s9_selection_writers):
        fn()
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
