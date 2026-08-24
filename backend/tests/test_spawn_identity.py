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


def main() -> None:
    t0 = time.perf_counter()
    print(f"data root: {store.DATA_ROOT}")
    print(f"token store: {tokens.tokens_path()}")
    for fn in (s0_isolation, s1_store, s2_spawn, s3_identity):
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
