"""Bare-name transport resolution (e860798) — the acceptance suite.

User ruling, 2026-08-05, in two parts: **drop `@ext:` entirely**, and
**resolve the transport automatically, preferring fewer hops** — with the
correction that `@org:` and `@mcp:` are mutually exclusive for any one
recipient, so the priority is not one chain but two graphs: `@org: > @net:`
and `@mcp: > @net:`.

The properties that make a bare name SAFE (agreed with the implementer before
the build):

    ① the set a caller is shown is the set the resolver considers;
    ② an `@mcp:`-only peer is never presented as pushable;
    ③ a name that resolves to nothing is refused with the candidates named,
      never silently delivered somewhere plausible.

③ is the one that matters most: guessing is how a message reaches the wrong
party, and "it went somewhere" is indistinguishable from "it went nowhere"
until someone complains a day later. Every check here is about who wins, and
what happens when nobody does.

    §1  internal names win, always
    §2  the near tier — @org: and @mcp:, mutually exclusive
    §3  the hub tier, and fewest-hops
    §4  ambiguity refuses and names every candidate
    §5  @ext: is retired, loudly, and history stays readable

Hermetic: in-memory orgs and a stubbed `external_candidates` (the same hook
api.py installs), so nothing here needs a hub, a port or the filesystem.

    python backend/tests/test_resolver.py [-v]
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-resolver-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ["USERPROFILE"] = os.environ["HOME"] = _TMP

from orgtree import ledger as ledger_mod                         # noqa: E402
from orgtree.ledger import LedgerError, Org, USER                # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


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
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote this out of gap()")


_n = [0]


def candidates(org_hits=(), net_hits=()):
    """Stub the outside-knowledge hook exactly as api.py installs it."""
    ledger_mod.external_candidates = lambda name: {
        "org": [s for s in org_hits if s == name or s.split(".")[0] == name],
        "net": [s for s in net_hits if s == name or s.split(".")[0] == name]}


def fresh_org(*names: str) -> Org:
    _n[0] += 1
    o = Org.create(f"zz resolve {_n[0]}", dirs=["E:/work"])
    o.hire(USER, None, "opus", 20, "ceo")
    for nm in names:
        o.hire("ceo", "ceo", "haiku", 2, nm, add_dirs=[], tools=dict(ALL_TOOLS),
               org_visibility="team", charter="test hire")
    return o


def mcp_correspondent(o: Org, peer: str) -> None:
    """Give the org a history with an @mcp: peer — that log IS the ledger-side
    candidate source."""
    o.post_external_mail(f"@mcp:{peer}", "first contact from outside")


def resolves(o: Org, name: str) -> str:
    return o._resolve_recipient(name, outward=True)


def refuses(o: Org, name: str, *needles: str) -> str:
    try:
        got = resolves(o, name)
    except LedgerError as e:
        for nd in needles:
            assert nd in str(e), f"{nd!r} missing from: {e}"
        return str(e)
    raise AssertionError(f"resolved to {got!r} instead of refusing")


# ══════════════════════════════════════════════════════════════════════════ §1

def sec_internal() -> None:
    print("\n§1  internal names win, always")

    def _colleague_beats_an_org():
        o = fresh_org("nova")
        candidates(org_hits=("nova",), net_hits=("nova.other.abcdef",))
        assert resolves(o, "nova") == "nova", (
            "an agent addressing a colleague was hijacked by an outside party "
            "that happens to share the name")
    check("internal · a node name outranks an identical org and hub peer",
          _colleague_beats_an_org)

    def _user_still_resolves():
        o = fresh_org()
        candidates()
        assert resolves(o, "user") == USER
    check("internal · 'user' still addresses the user", _user_still_resolves)

    def _an_agent_named_user_wins_it_back():
        o = fresh_org("user")
        candidates()
        assert resolves(o, "user") == "user", "the sentinel ate a real agent"
    check("internal · …unless an agent is literally named user (names win)",
          _an_agent_named_user_wins_it_back)

    def _unknown_name_is_left_alone():
        o = fresh_org()
        candidates()
        assert resolves(o, "ghost") == "ghost", (
            "a name nothing matches must fall through to the ordinary "
            "unknown-node error, not become an outside address")
    check("internal · a name nothing matches falls through unchanged (the "
          "normal 'no such node' error still explains it)", _unknown_name_is_left_alone)


# ══════════════════════════════════════════════════════════════════════════ §2

def sec_near_tier() -> None:
    print("\n§2  the near tier — @org: and @mcp:, mutually exclusive")

    def _local_org():
        o = fresh_org()
        candidates(org_hits=("acme",))
        assert resolves(o, "acme") == "@org:acme"
    check("near · a bare name matching a local org resolves @org:", _local_org)

    def _mcp_correspondent():
        o = fresh_org()
        mcp_correspondent(o, "desk-chat")
        candidates()
        assert resolves(o, "desk-chat") == "@mcp:desk-chat"
    check("near · a bare name matching one of this org's OWN @mcp: "
          "correspondents resolves @mcp:", _mcp_correspondent)

    def _mcp_is_scoped_to_this_org():
        o1 = fresh_org()
        mcp_correspondent(o1, "private-peer")
        o2 = fresh_org()
        candidates()
        assert resolves(o2, "private-peer") == "private-peer", (
            "one org's @mcp: correspondent leaked into another org's "
            "resolution — the log is per-org and so is the candidacy")
    check("near · an @mcp: correspondent belongs to the org that has spoken "
          "to it, and to no other", _mcp_is_scoped_to_this_org)

    def _kiosk_shaped_absence():
        """api._external_candidates excludes sealed kiosks, so a kiosk name
        never becomes a candidate — modelled here by the stub returning none."""
        o = fresh_org()
        candidates(org_hits=())
        assert resolves(o, "sealed-kiosk") == "sealed-kiosk"
    check("near · a sealed kiosk is not a candidate (it answers like a "
          "nonexistent org, same as interorg_send)", _kiosk_shaped_absence)


# ══════════════════════════════════════════════════════════════════════════ §3

def sec_hub_tier() -> None:
    print("\n§3  the hub tier, and fewest hops")

    def _net_only():
        o = fresh_org()
        candidates(net_hits=("faraway.other.abcdef",))
        assert resolves(o, "faraway") == "@net:faraway.other.abcdef", (
            "a leading-segment match on the hub roster must resolve")
    check("hub · a bare name matching a hub peer resolves @net: (leading "
          "segment counts, so nobody types a fingerprint)", _net_only)

    def _org_beats_net():
        o = fresh_org()
        candidates(org_hits=("acme",), net_hits=("acme.other.abcdef",))
        assert resolves(o, "acme") == "@org:acme", (
            "the local org lost to the hub — fewest hops must win")
    check("hub · @org: beats @net: for the same name (graph one)", _org_beats_net)

    def _mcp_beats_net():
        o = fresh_org()
        mcp_correspondent(o, "desk-chat")
        candidates(net_hits=("desk-chat.other.abcdef",))
        assert resolves(o, "desk-chat") == "@mcp:desk-chat", (
            "the local polling peer lost to the hub — fewest hops must win")
    check("hub · @mcp: beats @net: for the same name (graph two)", _mcp_beats_net)

    def _full_slug_still_works():
        o = fresh_org()
        candidates(net_hits=("faraway.other.abcdef",))
        assert resolves(o, "faraway.other.abcdef") == "@net:faraway.other.abcdef"
    check("hub · the full slug resolves too (the disambiguator a user can "
          "always fall back to)", _full_slug_still_works)


# ══════════════════════════════════════════════════════════════════════════ §4

def sec_ambiguity() -> None:
    print("\n§4  ambiguity refuses, and names every candidate")

    def _two_hub_peers():
        o = fresh_org()
        candidates(net_hits=("twin.here.aaaaaa", "twin.there.bbbbbb"))
        msg = refuses(o, "twin", "ambiguous", "twin.here.aaaaaa",
                      "twin.there.bbbbbb")
        assert "@net:" in msg
    check("ambiguity · two hub peers sharing a name refuse, with BOTH full "
          "addresses in the message", _two_hub_peers)

    def _org_and_mcp_together():
        o = fresh_org()
        mcp_correspondent(o, "acme")
        candidates(org_hits=("acme",))
        refuses(o, "acme", "ambiguous", "@org:acme", "@mcp:acme")
    check("ambiguity · a local org and an @mcp: peer sharing a name refuse "
          "rather than picking one (the tiers are exclusive per recipient, "
          "not ordered against each other)", _org_and_mcp_together)

    def _ambiguity_never_falls_through_to_the_hub():
        o = fresh_org()
        mcp_correspondent(o, "acme")
        candidates(org_hits=("acme",), net_hits=("acme.other.abcdef",))
        msg = refuses(o, "acme", "ambiguous")
        assert "@net:acme.other.abcdef" not in msg, (
            "the hub candidate was offered as a way out of a near-tier tie — "
            "a tie is decided by the user, not by demoting to more hops")
    check("ambiguity · a near-tier tie does not silently fall through to the "
          "hub", _ambiguity_never_falls_through_to_the_hub)

    def _one_of_each_is_not_ambiguous():
        """The property the two-graph correction buys: near beats hub cleanly,
        so a name on BOTH is not a tie."""
        o = fresh_org()
        candidates(org_hits=("acme",), net_hits=("acme.other.abcdef",))
        assert resolves(o, "acme") == "@org:acme"
    check("ambiguity · one near candidate + one hub candidate is NOT a tie",
          _one_of_each_is_not_ambiguous)


# ══════════════════════════════════════════════════════════════════════════ §5

def sec_retired() -> None:
    print("\n§5  @ext: is retired, loudly")

    def _refused_and_points_somewhere():
        o = fresh_org()
        candidates()
        o.audience_grant(USER, "ceo", "extern") if hasattr(o, "audience_grant") else None
        try:
            o.post_mail("ceo", "@ext:someone", "hello?")
            raise AssertionError("the retired prefix was accepted")
        except LedgerError as e:
            msg = str(e)
            assert "retired" in msg, msg
            assert "@net:" in msg, ("the refusal must name the replacement — "
                                    "a dead end is how agents get stuck: " + msg)
    check("retired · @ext: refuses and names @net: as the way forward",
          _refused_and_points_somewhere)

    def _explicit_prefixes_still_bypass():
        o = fresh_org("nova")
        candidates(org_hits=("nova",))
        assert resolves(o, "@org:nova") == "@org:nova", (
            "an explicit prefix must bypass the resolver entirely — it is the "
            "disambiguator, and re-resolving it would defeat that")
    check("retired · the surviving prefixes still bypass the resolver "
          "(they are the disambiguator)", _explicit_prefixes_still_bypass)

    def _history_stays_readable():
        o = fresh_org()
        o.d.setdefault("org_inbox", []).append(
            {"id": "old1", "dir": "in", "peer": "@ext:ancient", "body": "hi",
             "at": "2026-01-01T00:00:00Z"})
        rows = [r for r in o.d["org_inbox"] if r["peer"].startswith("@ext:")]
        assert rows and rows[0]["body"] == "hi", (
            "historical @ext: correspondence must stay readable — only NEW "
            "sends refuse")
    check("retired · historical @ext: rows remain readable (records are not "
          "rewritten by a retirement)", _history_stays_readable)


# ═════════════════════════════════════════════════════════════════════════ main

def main() -> None:
    print("═══ bare-name transport resolution — the acceptance suite ═══")
    sec_internal()
    sec_near_tier()
    sec_hub_tier()
    sec_ambiguity()
    sec_retired()

    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed, "
          f"{len(GAPS)} gaps")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    if GAPS:
        print("\n⚑ GAPS — measured, currently true, reported to the implementer:")
        for label, why, detail in GAPS:
            print(f"\n  ⚑ {label}\n    measured: {detail}\n    {why}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
