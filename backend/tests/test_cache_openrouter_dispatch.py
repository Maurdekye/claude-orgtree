"""The OpenRouter lane's FULL cache snapshot, end to end.

Audit C1 (astras-entrance-exam, 2026-09-04): `_cache_snapshot` dispatched
claude / openai / EVERYTHING ELSE, and "everything else" was the Antigravity
branch — so an OpenRouter node reported `provider=openrouter` paired with an
Antigravity account namespace, `lane=provider_unsupported`, Antigravity
tool/argv digests and a managed-identity startup placeholder. The OpenRouter
namespace helper and its TTL entry both existed and were never reached: a
helper-only test cannot see this, which is why every check here goes through
the full snapshot.

The bar is not "the label says openrouter". It is that the ROUTE INPUTS are
the OpenRouter ones: the Claude Code harness pointed at openrouter.ai —
its argv, its startup files, its env pins — and the credential digested into
the account namespace and nowhere else. Each assertion below moves an input
and watches the snapshot move (or, for the credential, NOT move) with it.

Plain deterministic checks; no provider/network calls. Run with:
    python backend/tests/test_cache_openrouter_dispatch.py
"""

from __future__ import annotations

import atexit
import contextlib
import copy
import json
import os
import shutil
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DATA = tempfile.mkdtemp(prefix="orgtree-cache-openrouter-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from orgtree import (cachecontinuity as C, openrouter, providers,  # noqa: E402
                     store, supervisor as S, warmpool)
from orgtree.ledger import Org, USER                                # noqa: E402

# ⚠ store.DATA_ROOT binds at import. Prove this process cannot reach the
# operator's live root before anything below touches storage.
LIVE_ROOT = os.path.normcase(os.path.realpath(os.path.expanduser("~/orgtree")))
assert os.path.normcase(os.path.realpath(store.DATA_ROOT)) != LIVE_ROOT, \
    store.DATA_ROOT
assert os.path.normcase(os.path.realpath(store.DATA_ROOT)).startswith(
    os.path.normcase(os.path.realpath(DATA))), store.DATA_ROOT
S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None
atexit.register(lambda: shutil.rmtree(DATA, ignore_errors=True))

NOW = 1788253200.0
PASS = FAIL = 0

# Fake credentials. Shaped like the real thing so `set_key` accepts them, and
# carrying a marker no digest could reproduce so a leak is unmistakable.
KEY_A = "sk-or-v1-FAKEKEYALPHA-0000000000000000000000000000"
KEY_B = "sk-or-v1-FAKEKEYBRAVO-1111111111111111111111111111"
TIER = "or-vendor-fake-model"
MODEL_A = "vendor/fake-model"
MODEL_B = "vendor/other-fake-model"


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as exc:
        FAIL += 1
        print(f"  FAIL    {label}: {exc}")
        import traceback
        traceback.print_exc()


def eq(got: Any, want: Any, what: str = "") -> None:
    assert got == want, f"{what}: got {got!r}; want {want!r}"


class Sentinel:
    """A stand-in that RECORDS being reached. Calls are counted, never
    swallowed: the value it returns is one the assertions below would also
    reject, so a branch that reaches it fails twice over."""

    def __init__(self, name: str, value: Any) -> None:
        self.name, self.value, self.calls = name, value, 0

    def __call__(self, *_a: Any, **_k: Any) -> Any:
        self.calls += 1
        return self.value


@contextlib.contextmanager
def antigravity_sentinels():
    """Every Antigravity helper the old dispatch reached, replaced by a
    counter. The Antigravity CONTROL below proves each one is genuinely
    reached on a real Antigravity node — a sentinel nothing can trip is
    decoration."""
    ns = Sentinel("_cache_antigravity_account_namespace",
                  "antigravity-oauth:WRONG-ACCOUNT")
    grant = Sentinel("antigravity_mcp_grant", ({}, []))
    status = Sentinel("providers.antigravity_status",
                      {"installed": False, "connected": False, "path": ""})
    saved = (S._cache_antigravity_account_namespace, S.antigravity_mcp_grant,
             providers.antigravity_status)
    S._cache_antigravity_account_namespace = ns          # type: ignore[assignment]
    S.antigravity_mcp_grant = grant                      # type: ignore[assignment]
    providers.antigravity_status = status                # type: ignore[assignment]
    try:
        yield ns, grant, status
    finally:
        (S._cache_antigravity_account_namespace, S.antigravity_mcp_grant,
         providers.antigravity_status) = saved            # type: ignore[assignment]


@contextlib.contextmanager
def login(uuid: str, email: str = "someone@example.invalid"):
    """Pin WHO this machine is signed in as (test_cache_continuity's rule):
    unstubbed, `accounts.live_identity` reads the developer's real
    ~/.claude.json and a primary-lane assertion would depend on whose desk
    ran it."""
    real = S.accounts.live_identity
    S.accounts.live_identity = lambda: {"uuid": uuid, "email": email}  # type: ignore[assignment]
    try:
        yield
    finally:
        S.accounts.live_identity = real                      # type: ignore[assignment]


def make_org(label: str, tier: str) -> tuple[Org, str]:
    """An org whose one agent sits on `tier`. OpenRouter tiers are dynamic
    (a favorite, not a table row), so the node is moved onto one the way the
    org doc would carry it — the same shape the audit fixture used."""
    org = store.create_org(f"zz-cache-or-{label}")
    org.hire(USER, None, "haiku", 0, "agent")
    if openrouter.is_tier(tier):
        org.node("agent")["model"] = tier
        org.d["tiers"][tier] = 1
        org.d["models"][tier] = MODEL_A
    else:
        org.node("agent")["model"] = tier
    store.save_org(org)
    return org, "agent"


def or_snapshot(org: Org, nid: str, **kw: Any) -> dict[str, Any]:
    return S._cache_snapshot(org, nid, now=NOW, include_history=False, **kw)


def openrouter_full_snapshot_takes_the_openrouter_route() -> None:
    """The audit's fixture, ported: an OpenRouter node, a key set, the
    Antigravity helpers replaced by counters. Then the route inputs, one at a
    time."""
    openrouter.set_key(KEY_A)
    org, nid = make_org("route", TIER)
    with antigravity_sentinels() as (ns, grant, status):
        snap = or_snapshot(org, nid)
        eq(ns.calls, 0, "antigravity namespace helper reached")
        eq(grant.calls, 0, "antigravity MCP grant reached")
        eq(status.calls, 0, "antigravity status probe reached")
    eq(snap["provider"], "openrouter", "provider")
    assert snap["account"].startswith("openrouter-key:"), snap["account"]
    eq(snap["lane"], "api_key", "lane")
    eq(snap["model"], MODEL_A, "model is the OpenRouter model id")
    # the lane is a SUPPORTED one with the measured 5-minute window — not an
    # arbitrary TTL, the table's own entry
    eq(C.ttl_seconds(snap["provider"], snap["lane"]), C.API_KEY_TTL_SECONDS,
       "ttl")

    # ROUTE INPUT 1 — env: the spawn env of THIS lane, credential-free. The
    # gateway base URL and the model pins are what the harness actually
    # carries to openrouter.ai; the bearer token belongs to the namespace.
    env = S.spawn_env(org, tier=TIER, nid=nid)
    assert S.openrouter_env(env), env.get("ANTHROPIC_BASE_URL")
    projection = S._cache_claude_env_projection(env)
    eq(projection.get("ANTHROPIC_BASE_URL"), openrouter.ANTHROPIC_BASE,
       "base url in env projection")
    eq(projection.get("ANTHROPIC_DEFAULT_SONNET_MODEL"), MODEL_A,
       "model pin in env projection")
    assert "ANTHROPIC_AUTH_TOKEN" not in projection, sorted(projection)
    assert KEY_A not in json.dumps(projection)
    eq(snap["components"]["env"], C.digest(projection),
       "env component is the projection of the OpenRouter spawn env")
    # …and it MOVES when the route's model pin moves
    org.d["models"][TIER] = MODEL_B
    moved = or_snapshot(org, nid)
    eq(moved["model"], MODEL_B, "model follows the tier's model id")
    assert moved["components"]["env"] != snap["components"]["env"], \
        "env digest did not move with the model pins"
    org.d["models"][TIER] = MODEL_A

    # ROUTE INPUT 2 — startup: the Claude Code harness reads this node's
    # CLAUDE.md before turn 1. The old branch hashed a managed-identity
    # placeholder that could not see the file change (the audit's C2 shape).
    scratch = S.scratch_dir(org.d["slug"], nid)
    os.makedirs(scratch, exist_ok=True)
    startup_file = os.path.join(scratch, "CLAUDE.md")
    with open(startup_file, "w", encoding="utf-8") as fh:
        fh.write("ALPHA startup rule\n")
    before = or_snapshot(org, nid)
    eq(before["components"]["startup"],
       warmpool.native_startup_context_digest(org, nid),
       "startup component is the native startup manifest")
    with open(startup_file, "w", encoding="utf-8") as fh:
        fh.write("BRAVO changed startup rule\n")
    after = or_snapshot(org, nid)
    assert after["components"]["startup"] != before["components"]["startup"], \
        "startup digest did not move with CLAUDE.md"
    os.remove(startup_file)

    # ROUTE INPUT 3 — tools/argv: the Claude harness argv projection, which
    # carries the permission mode; the Antigravity tools digest does not.
    n = org.node(nid)
    n["scope"]["permission_mode"] = "plan"
    plan = or_snapshot(org, nid)
    n["scope"]["permission_mode"] = "acceptEdits"
    accept = or_snapshot(org, nid)
    assert plan["components"]["tools"] != accept["components"]["tools"], \
        "tools digest did not move with the permission mode"
    assert plan["components"]["argv"] != accept["components"]["argv"], \
        "argv digest did not move with the permission mode"
    n["scope"].pop("permission_mode", None)

    # ORDINARY OVERRIDES still win, as on every lane.
    forced = or_snapshot(org, nid, account_override="acct-x",
                         lane_override="subscription")
    eq((forced["account"], forced["lane"]), ("acct-x", "subscription"),
       "overrides")


check("an OpenRouter node's full snapshot is computed from the OpenRouter "
      "route inputs, never the Antigravity branch",
      openrouter_full_snapshot_takes_the_openrouter_route)


def key_rotation_moves_the_namespace_and_only_the_namespace() -> None:
    """Rotate the OpenRouter key: the account namespace must change (another
    key is another cache namespace — INV-003) while every prefix component
    stays put (the request bytes did not change), and no raw key may appear
    anywhere in the serialized snapshot."""
    org, nid = make_org("rotate", TIER)
    openrouter.set_key(KEY_A)
    snap_a = or_snapshot(org, nid)
    openrouter.set_key(KEY_B)
    snap_b = or_snapshot(org, nid)
    assert snap_a["account"] != snap_b["account"], snap_a["account"]
    for snap in (snap_a, snap_b):
        assert snap["account"].startswith("openrouter-key:"), snap["account"]
        eq(snap["lane"], "api_key", "lane")
    eq(snap_a["components"], snap_b["components"],
       "prefix components must not move on a key rotation")
    assert snap_a["fingerprint"] != snap_b["fingerprint"], \
        "fingerprint must move with the namespace"
    # the same key again is the same namespace: the digest is deterministic
    openrouter.set_key(KEY_A)
    eq(or_snapshot(org, nid)["account"], snap_a["account"], "determinism")
    # and the namespace is read from the RESOLVED ENV the spawn carries, not
    # from the key store (the `identity_in_env` rule): with KEY_A stored, an
    # env built for KEY_B attributes to KEY_B
    env_b = S.clean_env()
    env_b["ANTHROPIC_BASE_URL"] = openrouter.ANTHROPIC_BASE
    env_b["ANTHROPIC_AUTH_TOKEN"] = KEY_B
    eq(or_snapshot(org, nid, env=env_b)["account"], snap_b["account"],
       "namespace follows the resolved env, not the key store")
    # the classifier reads the rotation as a KNOWN cold namespace, named
    prior = {"last_turn": S._cache_persistable(snap_a)}
    row = C.classify(S._cache_prepare_relations(snap_b, prior), prior, NOW)
    eq(row["state"], "known_incompatible", "rotation state")
    eq([r["component"] for r in row["reasons"]], ["account"], "reason")
    # NO RAW CREDENTIAL, in the persistable row or the raw snapshot
    for snap in (snap_a, snap_b):
        for blob in (json.dumps(S._cache_persistable(snap)),
                     json.dumps({k: v for k, v in snap.items()
                                 if k != "_history_path"}),
                     repr(snap)):
            assert KEY_A not in blob and KEY_B not in blob, blob[:200]
            assert "FAKEKEY" not in blob, blob[:200]


check("OpenRouter key rotation changes the account namespace only, and no "
      "raw key reaches a serialized snapshot",
      key_rotation_moves_the_namespace_and_only_the_namespace)


def compatible_is_never_a_guaranteed_hit() -> None:
    """The best OpenRouter outcome — same prefix, positive unexpired receipt
    on the supported 5-minute lane — is `compatible_observed`, and its
    wording says a hit is not guaranteed. And the receipt's window is the
    table's 5 minutes: at 4m59s the entry is live, at 5m it has expired."""
    openrouter.set_key(KEY_A)
    org, nid = make_org("hit", TIER)
    snap = or_snapshot(org, nid)
    last = S._cache_persistable(snap)
    last["history"] = {"bytes": 4, "sha256": "abcd"}
    receipt = copy.deepcopy(last)
    receipt["observed_at"] = C.iso(NOW - 60)
    receipt["cache_read_tokens"] = 50000
    book = {"last_turn": last, "receipt": receipt}
    current = dict(snap, history=last["history"],
                   last_turn_history_relation="same_or_appended",
                   receipt_history_relation="same_or_appended")
    row = C.classify(current, book, NOW)
    eq(row["state"], "compatible_observed", "state")
    eq(row["ttl_seconds"], C.API_KEY_TTL_SECONDS, "ttl")
    eq(row["lane"], "api_key", "lane")
    assert "not guaranteed" in row["reason"], row["reason"]
    assert row["readiness_cause"] != "unsupported_capability", row
    # the boundary: the same receipt, read one second before and at expiry
    live = C.classify(current, book, NOW - 60 + C.API_KEY_TTL_SECONDS - 1)
    eq(live["state"], "compatible_observed", "one second before expiry")
    gone = C.classify(current, book, NOW - 60 + C.API_KEY_TTL_SECONDS)
    eq(gone["state"], "expired_known_entry", "at expiry")


check("a matching OpenRouter prefix is `compatible_observed` on the 5-minute "
      "lane, worded as not guaranteed, and expires on the boundary",
      compatible_is_never_a_guaranteed_hit)


def missing_key_is_unobserved_not_someone_else() -> None:
    """No OpenRouter key stored: the spawn seam refuses the turn, and the
    snapshot — which the readiness poll renders WITHOUT a turn — must
    neither crash nor attribute the node to the main login or the
    Antigravity account. It is unobserved, and it says so."""
    openrouter.set_key("")
    org, nid = make_org("nokey", TIER)
    with login("uuid-main-login"), antigravity_sentinels() as (ns, grant, _s):
        snap = or_snapshot(org, nid)
        eq(ns.calls, 0, "antigravity namespace helper reached")
        eq(grant.calls, 0, "antigravity MCP grant reached")
    eq(snap["provider"], "openrouter", "provider")
    eq(snap["lane"], "unobserved", "lane")
    assert not snap["account"].startswith(S.accounts.PRIMARY), snap["account"]
    assert not snap["account"].startswith("antigravity"), snap["account"]
    assert "openrouter" in snap["account"], snap["account"]
    # the spawn seam itself still refuses, loudly — the snapshot did not
    # paper over the missing key
    try:
        S.spawn_env(org, tier=TIER, nid=nid)
    except RuntimeError as exc:
        assert "OpenRouter" in str(exc), exc
    else:
        raise AssertionError("spawn_env accepted a keyless OpenRouter tier")


check("a keyless OpenRouter node snapshots as unobserved, not as the main "
      "login or an Antigravity account",
      missing_key_is_unobserved_not_someone_else)


def claude_controls_are_untouched() -> None:
    """The Claude subscription and API-key lanes, through the same full
    snapshot, with the OpenRouter key SET — an OpenRouter key on the machine
    must not bleed into a Claude node's attribution."""
    openrouter.set_key(KEY_A)
    org, nid = make_org("claude", "haiku")
    with login("uuid-main-login"), antigravity_sentinels() as (ns, _g, _s):
        sub = S._cache_snapshot(org, nid, now=NOW, env={},
                                include_history=False)
        eq(ns.calls, 0, "antigravity helper on a Claude node")
    eq(sub["provider"], "claude", "provider")
    assert sub["account"].startswith(S.accounts.PRIMARY + ":"), sub["account"]
    eq(sub["lane"], "subscription", "subscription lane")
    eq(C.ttl_seconds("claude", "subscription"), C.SUBSCRIPTION_TTL_SECONDS)
    env = S.clean_env()
    env["ANTHROPIC_API_KEY"] = "FAKE-ANTHROPIC-KEY-NOT-A-CREDENTIAL"
    key = S._cache_snapshot(org, nid, now=NOW, env=env, include_history=False)
    assert key["account"].startswith("api-key:"), key["account"]
    eq(key["lane"], "api_key", "api-key lane")
    assert "FAKE-ANTHROPIC" not in json.dumps(S._cache_persistable(key))
    assert KEY_A not in json.dumps(S._cache_persistable(key))
    assert sub["account"] != key["account"]


check("Claude subscription and API-key controls: unchanged lanes, and a "
      "stored OpenRouter key does not bleed into them",
      claude_controls_are_untouched)


def codex_control_is_untouched() -> None:
    old_home = os.environ.get("CODEX_HOME")
    home = os.path.join(DATA, "codex-home")
    os.makedirs(home, exist_ok=True)
    os.environ["CODEX_HOME"] = home
    try:
        with open(os.path.join(home, "auth.json"), "w", encoding="utf-8") as fh:
            json.dump({"tokens": {"account_id": "account-a"}}, fh)
        org, nid = make_org("codex", "luna")
        eq(providers.provider_of("luna"), "openai", "luna is a Codex tier")
        with antigravity_sentinels() as (ns, grant, _s):
            snap = S._cache_snapshot(org, nid, now=NOW, include_history=False)
            eq(ns.calls, 0, "antigravity helper on a Codex node")
            eq(grant.calls, 0, "antigravity grant on a Codex node")
        eq(snap["provider"], "openai", "provider")
        assert snap["account"].startswith("codex-chatgpt:"), snap["account"]
        eq(snap["lane"], "subscription", "lane")
        assert "account-a" not in json.dumps(S._cache_persistable(snap))
    finally:
        if old_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = old_home


check("Codex control: its own account namespace and subscription lane",
      codex_control_is_untouched)


def antigravity_control_reaches_the_antigravity_branch() -> None:
    """THE POSITIVE CONTROL for every sentinel above. A real Antigravity node
    must reach all three helpers — otherwise "the sentinel was not called"
    on the OpenRouter node would be a check that cannot fail."""
    org, nid = make_org("agy", "pro")
    eq(providers.provider_of("pro"), "google", "pro is an Antigravity tier")
    with antigravity_sentinels() as (ns, grant, status):
        snap = S._cache_snapshot(org, nid, now=NOW, include_history=False)
        eq(ns.calls, 1, "antigravity namespace helper")
        assert grant.calls >= 1, "antigravity MCP grant"
        assert status.calls >= 1, "antigravity status probe"
        # ⚠ THE TOOLS/ARGV LEG ON ITS OWN. `identity_prompt` ALSO calls the
        # grant helper on an Antigravity tier, so the whole-snapshot count
        # above cannot tell whether `_cache_semantic_inputs` reached it — a
        # mutant that dropped the grant from the tools digest passed this
        # control until the leg was counted in isolation (evidence CTL-7c).
        grant_before, status_before = grant.calls, status.calls
        S._cache_semantic_inputs(org, nid, "google")
        eq(grant.calls - grant_before, 1, "grant calls from the tools leg")
        eq(status.calls - status_before, 1, "status calls from the argv leg")
    eq(snap["provider"], "google", "provider")
    eq(snap["account"], "antigravity-oauth:WRONG-ACCOUNT",
       "the sentinel's value is what the Antigravity branch reports")
    eq(snap["lane"], "provider_unsupported", "lane")
    eq(C.ttl_seconds("google", "provider_unsupported"), None, "no TTL")


check("Antigravity control: a real Antigravity node reaches every helper the "
      "OpenRouter sentinels guard",
      antigravity_control_reaches_the_antigravity_branch)


print(f"\nALL {PASS} CHECKS PASS" if not FAIL else
      f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
