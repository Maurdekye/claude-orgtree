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

from orgtree import store, supervisor as sup, tokens      # noqa: E402


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


def main() -> None:
    t0 = time.perf_counter()
    print(f"data root: {store.DATA_ROOT}")
    print(f"token store: {tokens.tokens_path()}")
    for fn in (s0_isolation, s1_store, s2_spawn, s3_identity,
               s4_failover, s5_drive_text, s6_wiring):
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
