"""Canonical references — the one place a link to a thing is spelled.

User request 2026-09-05: agents must be able to link directly to a specific
mail, presented document or docket item, embedded in ordinary prose. These are
the tokens they emit, and the tokens the UI resolves.

    @item:<org>/<slug>
    @doc:<org>/<did>
    @agent:<org>/<node>
    @mail:<org>/user/<id>
    @mail:<org>/org/<id>
    @mail:<org>/node/<node>/<id>

⚠ THE ORG SEGMENT IS NOT DECORATION. Prose gets copied — between orgs, out of
a transcript, into a report. Two orgs can hold the same item slug, the same
agent name, even the same mail id, and a token without an org would resolve
against whichever org happened to be on screen and open something unrelated
while looking exactly right (Astra review 2026-09-05, correcting my first
draft, which claimed same-org scope was structural).

⚠ THE DELIMITERS ARE SAFE BY MEASUREMENT, NOT BY HOPE. Every identity that can
appear in a token is `[a-z0-9-]+`: org slugs and node ids come from
`ledger.slugify` (`[^a-z0-9]+` → `-`), item slugs from `Org._work_slugify`, and
document and mail ids are hex with at most a leading `d`. So neither `:` nor
`/` occurs inside any segment, and splitting on `/` cannot be ambiguous.
`token_shape_is_still_safe` in the tests re-derives that from the real
functions rather than trusting this paragraph.

⚠ AND A MALFORMED TOKEN IS REFUSED, NOT GUESSED. `@mail:org/node/abc123` has
three segments with a box of `node`, which is the four-segment shape missing
its node — it parses as nothing at all rather than as a mail somewhere
plausible. Guessing is how a reference opens the wrong thing.
"""
from __future__ import annotations

import re
from typing import Any

#: one segment of a reference. Deliberately the intersection of every
#: identity alphabet in the product, so a token can never carry a delimiter.
SEG = r"[a-z0-9-]+"

#: the whole family, for a matcher that has to find these inside prose
TOKEN_RE = re.compile(r"@(item|doc|agent|mail):((?:" + SEG + r")(?:/" + SEG + r")*)")

KINDS = ("item", "doc", "agent", "mail")
MAIL_BOXES = ("user", "org", "node")


def item(org: str, slug: str) -> str:
    return f"@item:{org}/{slug}"


def doc(org: str, did: str) -> str:
    return f"@doc:{org}/{did}"


def agent(org: str, nid: str) -> str:
    return f"@agent:{org}/{nid}"


def mail(org: str, delivered: str, mid: str) -> str | None:
    """The reference for a mail that was actually delivered somewhere local.

    ⚠ `delivered` IS THE DELIVERY RECORD'S OWN VALUE, and the three cases are
    exactly the three `post_mail` returns — the user's inbox, the org inbox
    (which is where an EXTERNAL send is logged, so it has a local id after
    all), or a node's box. Anything else returns None rather than inventing a
    box: a reference to a mail that is not in a box we can open is worse than
    no reference (Astra 2026-09-05).
    """
    d = str(delivered or "")
    m = str(mid or "")
    if not d or not m:
        return None
    if d == "user_inbox":
        return f"@mail:{org}/user/{m}"
    if d.startswith("@"):
        return f"@mail:{org}/org/{m}"
    if re.fullmatch(SEG, d):
        return f"@mail:{org}/node/{d}/{m}"
    return None


def parse(token: str) -> dict[str, Any] | None:
    """A token to its parts, or None when it is not one. Never a guess."""
    m = TOKEN_RE.fullmatch(str(token or ""))
    if not m:
        return None
    kind, rest = m.group(1), m.group(2).split("/")
    if kind in ("item", "doc", "agent"):
        if len(rest) != 2:
            return None
        return {"kind": kind, "org": rest[0], "id": rest[1]}
    # mail
    if len(rest) == 3 and rest[1] in ("user", "org"):
        return {"kind": "mail", "org": rest[0], "box": rest[1], "id": rest[2]}
    if len(rest) == 4 and rest[1] == "node":
        return {"kind": "mail", "org": rest[0], "box": "node",
                "node": rest[2], "id": rest[3]}
    return None
