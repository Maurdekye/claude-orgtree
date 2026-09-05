"""Canonical references — the token format, and what it refuses.

User request 2026-09-05: agents link directly to a specific mail, presented
document or docket item from ordinary prose. `orgtree/refs.py` is the one place
those tokens are spelled; this is what holds that spelling still.

THE THREE THINGS WORTH TESTING HERE, in order of how much damage they prevent:

  1. THE ORG SEGMENT. Prose gets copied between orgs. Two orgs can hold the
     same item slug, the same agent name and the same mail id, so a token
     without an org resolves against whatever is on screen and opens something
     unrelated while looking exactly right. My first contract claimed same-org
     scope was structural; it is not, for copied text (Astra 2026-09-05).
  2. THE DELIMITERS ARE SAFE BY MEASUREMENT. Re-derived below from the REAL
     slug functions rather than from a comment claiming it.
  3. A MALFORMED TOKEN IS REFUSED, NOT GUESSED. A reference that resolves to
     something plausible is worse than one that resolves to nothing.

Run: python tests/test_refs.py
"""
import io
import os
import re
import sys
import tempfile

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys._utf8_wrapped = True

RIG = tempfile.mkdtemp(prefix="refs-")
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["ORGTREE_DATA"] = RIG
sys.path.insert(0, BACKEND)

from orgtree import refs                                          # noqa: E402
from orgtree.ledger import Org, slugify                           # noqa: E402

PASS = FAIL = 0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:3d}  {label}")
    except Exception as e:                                        # noqa: BLE001
        FAIL += 1
        import traceback
        print(f"  FAIL     {label}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=6)


def every_reference_carries_its_org():
    assert refs.item("alpha", "git-review-workspace") \
        == "@item:alpha/git-review-workspace"
    assert refs.doc("alpha", "d1a2b3c4") == "@doc:alpha/d1a2b3c4"
    assert refs.agent("alpha", "luna-reserve") == "@agent:alpha/luna-reserve"
    assert refs.mail("alpha", "user_inbox", "ab12") == "@mail:alpha/user/ab12"
    assert refs.mail("alpha", "@net:peer", "ab12") == "@mail:alpha/org/ab12"
    assert refs.mail("alpha", "luna-reserve", "ab12") \
        == "@mail:alpha/node/luna-reserve/ab12"


def the_same_name_in_two_orgs_is_two_different_references():
    """⚠ THE WHOLE REASON THE ORG SEGMENT EXISTS. Without it these two are
    the same string, and a token copied out of one org's transcript into the
    other opens an unrelated item while looking perfectly correct."""
    a = refs.item("alpha", "shared-name")
    b = refs.item("beta", "shared-name")
    assert a != b, (a, b)
    assert refs.parse(a)["org"] == "alpha"
    assert refs.parse(b)["org"] == "beta"
    # ...and the same for every other kind, since all four are copyable prose
    for make, args in ((refs.doc, ("d1",)), (refs.agent, ("worker",))):
        assert make("alpha", *args) != make("beta", *args)
    assert refs.mail("alpha", "user_inbox", "x1") != \
        refs.mail("beta", "user_inbox", "x1")


def token_shape_is_still_safe():
    """⚠ RE-DERIVED FROM THE REAL FUNCTIONS, not from the module docstring.
    The delimiters `:` and `/` are only unambiguous because no identity can
    contain one. If a slug alphabet ever widens, this fails here rather than
    silently making every token ambiguous."""
    nasty = "Weird / Name: with\\slashes, 100% & more"
    for produced in (slugify(nasty), Org._work_slugify(nasty)):
        assert re.fullmatch(refs.SEG, produced), produced
        assert "/" not in produced and ":" not in produced, produced
    # a node id is `slugify` plus a numeric suffix; an item slug is the same
    # plus `-2`. Both stay inside the segment alphabet.
    assert re.fullmatch(refs.SEG, slugify(nasty) + "-2")


def a_round_trip_survives_every_kind():
    cases = [
        (refs.item("o", "an-item"), {"kind": "item", "org": "o", "id": "an-item"}),
        (refs.doc("o", "d9"), {"kind": "doc", "org": "o", "id": "d9"}),
        (refs.agent("o", "an-agent"), {"kind": "agent", "org": "o", "id": "an-agent"}),
        (refs.mail("o", "user_inbox", "m1"),
         {"kind": "mail", "org": "o", "box": "user", "id": "m1"}),
        (refs.mail("o", "@peer", "m1"),
         {"kind": "mail", "org": "o", "box": "org", "id": "m1"}),
        (refs.mail("o", "a-node", "m1"),
         {"kind": "mail", "org": "o", "box": "node", "node": "a-node", "id": "m1"}),
    ]
    for token, want in cases:
        assert refs.parse(token) == want, (token, refs.parse(token))


def a_malformed_token_is_refused_and_never_guessed():
    bad = [
        "@item:only-one-segment",              # no org
        "@item:a/b/c",                         # too many
        "@mail:org/node/abc",                  # the 4-segment shape, one short
        "@mail:org/mailbox/abc",               # not a known box
        "@mail:org/user",                      # no id
        "@nope:org/thing",                     # not a kind
        "item:org/thing",                      # no sigil
        "@item:Org/Thing",                     # outside the segment alphabet
        "@item:org/thing/",                    # trailing delimiter
        "", None,
    ]
    for t in bad:
        assert refs.parse(t) is None, (t, refs.parse(t))
    # POSITIVE CONTROL: the near-miss of each of the first three DOES parse,
    # so the refusals above are about the malformation and not about the
    # parser refusing everything
    assert refs.parse("@item:org/thing") is not None
    assert refs.parse("@mail:org/node/a-node/abc") is not None
    assert refs.parse("@mail:org/user/abc") is not None


def a_send_with_no_local_box_gets_no_reference():
    """A reference to a mail that is not in a box we can open is worse than no
    reference. `delivered` comes from the delivery record, so an empty or
    unusable one yields nothing rather than a plausible guess."""
    assert refs.mail("o", "", "m1") is None
    assert refs.mail("o", "user_inbox", "") is None
    assert refs.mail("o", "Not A Node Id", "m1") is None


def the_matcher_finds_tokens_in_prose_and_stops_at_the_token():
    """The family regex is what a prose matcher scans with; it must end where
    the token ends rather than swallowing the punctuation after it."""
    found = refs.find_all(
        "see @item:alpha/git-review-workspace, and @mail:alpha/user/ab12. done")
    assert found == [("item", "alpha/git-review-workspace"),
                     ("mail", "alpha/user/ab12")], found
    # ⚠ A BEARER'S GENERATION IS PART OF THE NAME, never something to cut off:
    # truncating at the `@` would address the LIVE agent instead of the
    # bearer, which is the wrong-target failure this format exists to prevent.
    assert refs.find_all("ask @agent:alpha/codex-checklist@4 about it") ==         [("agent", "alpha/codex-checklist@4")]
    assert refs.parse("@agent:alpha/codex-checklist@4") ==         {"kind": "agent", "org": "alpha", "id": "codex-checklist@4"}
    assert refs.parse("@mail:alpha/node/codex-checklist@4/ab12") ==         {"kind": "mail", "org": "alpha", "box": "node",
         "node": "codex-checklist@4", "id": "ab12"}
    # ...and a token butting straight up against the next one still splits
    assert refs.find_all("@agent:a/b@2@item:a/c") ==         [("agent", "a/b@2"), ("item", "a/c")]


def the_cross_language_fixture_is_current():
    """⚠ TWO PARSERS ARE TWO CHANCES TO DISAGREE. The backend emits these
    tokens and the browser resolves them, and a disagreement is a link that
    opens the wrong thing. `frontend/tests/ref-tokens.json` is generated from
    THIS module and asserted by the TypeScript suite as well; this check is
    what stops it going stale, because a fixture nobody regenerates is a
    contract nobody is holding.

    Regenerate with `python backend/tools/gen_ref_fixture.py`."""
    import json
    sys.path.insert(0, os.path.join(BACKEND, "tools"))
    import gen_ref_fixture                                        # noqa: PLC0415
    want = gen_ref_fixture.build()
    with open(gen_ref_fixture.OUT, encoding="utf-8") as fh:
        have = json.load(fh)
    assert have == want, (
        "frontend/tests/ref-tokens.json is out of date — the token format "
        "changed and the browser's copy of the contract did not. Run "
        "`python backend/tools/gen_ref_fixture.py`")
    # and it must actually carry the half that matters
    bad = [t for t, v in want["parse"].items() if v is None]
    assert len(bad) >= 8, ("the fixture has almost no malformed cases, so "
                           f"'never guessed' is untested on both sides: {bad}")


check("the cross-language fixture is current",
      the_cross_language_fixture_is_current)
check("every reference carries its org", every_reference_carries_its_org)
check("the same name in two orgs is two different references",
      the_same_name_in_two_orgs_is_two_different_references)
check("the token alphabet is re-derived from the real slug functions",
      token_shape_is_still_safe)
check("every kind round-trips through parse", a_round_trip_survives_every_kind)
check("a malformed token is refused, never guessed",
      a_malformed_token_is_refused_and_never_guessed)
check("a send with no local box gets no reference",
      a_send_with_no_local_box_gets_no_reference)
check("the matcher finds tokens in prose and stops at the token",
      the_matcher_finds_tokens_in_prose_and_stops_at_the_token)

if FAIL:
    print(f"\n{FAIL} FAILED, {PASS} passed")
    sys.exit(1)
print(f"\nALL {PASS} CHECKS PASS")
