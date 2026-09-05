"""Assignment 19 — user-visible timestamps in the USER'S local timezone.

Plain asserts; run with:  python backend/tests/test_localtime.py

The contract: stored instants stay UTC ISO-8601 with `Z`. Server-written prose
the user reads carries the canonical instant inside a `⟦t:…⟧` token, and the
BROWSER renders it locally at read time. The server never formats a clock
reading, so there is no recorded zone to go stale and no fallback that could
render UTC or the server's own clock.

§1  the token: minting, parsing, and what an unreadable instant does
§2  ⭐ the durable row keeps the CANONICAL instant — the property that makes a
    row written in one zone correct when reread in another, and the reason
    identity checks are unaffected by a zone change
§3  supervisor._mail_block: human copy tokenised, agent copy canonical,
    authored body text untouched
§4  ⭐ mail_marker_in across a zone change — deliver in zone A, reread with a
    different (or no) zone, match exactly once
§5  supervisor._reset_label
§6  crashreports.format_mail_body
§7  ⭐ THE POSITIVE CONTROL: a scanner over the real frontend sources that must
    FAIL when a visible-UTC render is reintroduced

⚠ NOTHING HERE NEEDS A TIMEZONE DATABASE, and that is the point rather than a
convenience. The backend no longer resolves zones, so `tzdata` cannot change
any result below. Zone-dependent behaviour — the actual local rendering, the
DST-correct abbreviation, the date rollover — is tested where it now happens,
in frontend/tests/timefmt.test.ts, against a real `Intl`.
"""
import io
import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# ⚠ BEFORE the first orgtree import: store.DATA_ROOT binds at import time, and
# a module imported first is bound to the operator's live root for the life of
# the process (seven orgs leaked into it on 2026-09-04 exactly this way).
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-tztest-")

from orgtree import crashreports, localtime, supervisor  # noqa: E402

PASS = 0
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSTANT = "2026-09-05T01:11:27.340Z"


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


# ------------------------------------------------------------------ §1
def test_token_carries_the_canonical_instant():
    tok = localtime.token(INSTANT, "full")
    assert tok == f"⟦t:{INSTANT}|full⟧", tok
    assert localtime.instants_in(tok) == [INSTANT]


def test_epoch_seconds_and_milliseconds_normalise():
    secs = 1788570687.34            # == INSTANT
    assert localtime.to_iso(secs) == "2026-09-05T01:11:27.340Z", \
        localtime.to_iso(secs)
    assert localtime.to_iso(secs * 1000) == localtime.to_iso(secs)


def test_a_naive_instant_is_read_as_utc():
    """Every producer in this codebase writes UTC; reading a naive string in
    the machine's zone is what makes a fresh sighting look hours old."""
    assert localtime.to_iso("2026-09-05T01:11:27") == "2026-09-05T01:11:27.000Z"


def test_an_unreadable_instant_yields_nothing():
    for junk in (None, "", "   ", "not a date", "2026-13-45T99:99Z", []):
        assert localtime.token(junk) == "", repr(junk)
        assert localtime.to_iso(junk) is None, repr(junk)


def test_an_unknown_style_falls_back_rather_than_emitting_junk():
    assert localtime.token(INSTANT, "wingdings").endswith("|stamp⟧")


# ------------------------------------------------------------------ §2
def test_the_durable_row_stores_no_clock_reading():
    """⭐ THE PROPERTY EVERYTHING ELSE RESTS ON.

    Earlier drafts formatted here, against a zone the browser had reported.
    Two defects killed that (coordinator review 2026-09-05): a stored numeric
    offset is invalid across a DST transition, and prose written once is read
    long afterwards. This asserts the structural fix — what goes into the
    durable row is the INSTANT, not a rendering of it — so neither defect has
    anywhere to live.
    """
    text, _ = supervisor._mail_block([_entry()], human=True)
    assert localtime.instants_in(text) == [INSTANT], text
    # no clock reading, no zone abbreviation, no offset: nothing that could be
    # wrong when this row is read in another zone or another season
    header = text.split("\n")[0]
    body_free = header.replace(INSTANT, "")
    assert not re.search(r"\d{1,2}:\d{2}\s*(AM|PM|[A-Z]{2,4})?$", body_free), header
    assert "+0" not in body_free and "-0" not in body_free, header


# ------------------------------------------------------------------ §3
def _entry(**kw):
    e = {"from": "peer-agent", "relationship": "peer", "kind": "message",
         "at": INSTANT, "body": "the body of the message"}
    e.update(kw)
    return e


def test_human_copy_is_tokenised_and_agent_copy_is_canonical():
    agent_text, _ = supervisor._mail_block([_entry()])
    human_text, _ = supervisor._mail_block([_entry()], human=True)
    # the agent compares and quotes stamps — it keeps the bare instant
    assert f"· {INSTANT}" in agent_text, agent_text
    assert localtime.OPEN not in agent_text, agent_text
    # the human row carries the same instant, for the browser to render
    assert f"⟦t:{INSTANT}|" in human_text, human_text


def test_a_notice_header_is_tokenised_too():
    text, _ = supervisor._mail_block(
        [_entry(kind="notice")], human=True)
    assert f"⟦t:{INSTANT}|" in text, text


def test_a_reply_snapshot_stamp_is_tokenised_but_its_gist_is_not():
    quoted = "2026-01-01T00:00:00Z"
    text, _ = supervisor._mail_block([_entry(reply_to={
        "from": "someone", "at": quoted, "gist": "do it by 2026-03-01T09:00Z"})],
        human=True)
    assert f"⟦t:{localtime.to_iso(quoted)}|" in text, text
    # ⚠ the gist is the SENDER'S WORDS. A timestamp inside it is theirs.
    assert "do it by 2026-03-01T09:00Z" in text, text


def test_authored_body_text_survives_verbatim():
    e = _entry(body="I ran it at 2026-09-05T01:11:27Z and it failed")
    text, _ = supervisor._mail_block([e], human=True)
    assert "I ran it at 2026-09-05T01:11:27Z and it failed" in text, \
        "authored text must survive — no regex sweep over content"


# ------------------------------------------------------------------ §4
def test_mail_marker_survives_a_zone_change():
    """⭐ THE CASE THE COORDINATOR ASKED FOR: delivered under zone A, reread
    under zone B (or with no zone at all), must still match — exactly once.

    It holds for a structural reason rather than a lucky one: the needle is
    the canonical instant, which no zone can change. There is deliberately no
    locale-dependent spelling to fall out of step.
    """
    e = _entry()
    agent_text, _ = supervisor._mail_block([e])
    human_text, _ = supervisor._mail_block([e], human=True)
    # both texts are produced without any zone being consulted, so re-deriving
    # them under a different one is byte-identical — that IS the property
    assert supervisor._mail_block([e], human=True)[0] == human_text
    assert supervisor.mail_marker_in(agent_text, e), "agent spelling lost"
    assert supervisor.mail_marker_in(human_text, e), "human spelling lost"
    # exactly one identification per row, not two
    assert human_text.count(f"⟦t:{INSTANT}|") == 1, human_text
    # and it must still be able to say NO
    assert not supervisor.mail_marker_in("an unrelated transcript row", e)
    assert not supervisor.mail_marker_in(human_text, _entry(
        at="2026-09-05T09:99:99Z", body="different message entirely"))


def test_mail_marker_still_matches_rows_written_before_this_change():
    """A projection already on disk carries the bare `· <iso>` spelling."""
    e = _entry()
    legacy = f"FROM peer-agent (peer) · message · {INSTANT}\n{e['body']}"
    assert supervisor.mail_marker_in(legacy, e)


def test_mail_marker_needs_the_body_too():
    e = _entry()
    text, _ = supervisor._mail_block([e], human=True)
    assert not supervisor.mail_marker_in(
        text, _entry(body="a completely different body")), \
        "the stamp alone must not be enough to claim identity"


# ------------------------------------------------------------------ §5
def test_reset_label_is_a_token_not_a_clock():
    ts = 1788570687.34
    out = supervisor._reset_label(ts)
    assert out.startswith(localtime.OPEN) and out.endswith("⟧"), out
    assert localtime.instants_in(out), out
    # signature and return type unchanged — the codex freeze path calls this
    assert isinstance(out, str)
    # the stored label must not contain a rendered time in ANY zone
    assert not re.search(r"\d{1,2}:\d{2}\s?(AM|PM|am|pm)", out), out


def test_reset_label_is_stable_across_calls():
    """It is STORED. Two calls a month apart must give the same bytes, or the
    record would drift for no reason anybody could see."""
    ts = 1788570687.34
    assert supervisor._reset_label(ts) == supervisor._reset_label(ts)


# ------------------------------------------------------------------ §6
def test_crash_report_stamp_is_tokenised_and_keeps_the_raw_value():
    body = crashreports.format_mail_body(
        {"kind": "window-error", "at": 1788570687340, "message": "boom"})
    assert "⟦t:2026-09-05T01:11:27" in body, body
    # the raw value stays for server-log correlation
    assert "1788570687340" in body, body


def test_crash_report_without_a_stamp_says_unknown():
    body = crashreports.format_mail_body({"kind": "window-error"})
    assert "at: (unknown)" in body, body


# ------------------------------------------------------------------ §7
#: The frontend files that render application timestamps. Listed explicitly so
#: a NEW file with a new UTC render is a visible addition rather than a silent
#: inclusion.
SCANNED = [
    "frontend/src/App.tsx", "frontend/src/canvas/mail.tsx",
    "frontend/src/canvas/desk.tsx", "frontend/src/canvas/docs.tsx",
    "frontend/src/canvas/gallery.tsx", "frontend/src/canvas/modals.tsx",
    "frontend/src/canvas/accounts.tsx", "frontend/src/canvas/openrouter.tsx",
]

#: The two species that were really there, learned from the fourteen defects
#: rather than imagined:
#:   1. SLICING an ISO string — UTC by construction, nine of them, one of
#:      which appended the word "UTC" out loud.
#:   2. Printing the raw stamp straight into JSX (`<time>{mail.at}</time>`) —
#:      five of them, and the first version of this regex saw none of them.
#:
#: The JSX rule matches an element whose ENTIRE text child is a `.at`
#: expression. It deliberately does not match `key={…}` or a template literal
#: containing `mail.at`: those are identity, not display, and `desk.tsx` has
#: one of each.
#:
#: ⚠ ITS BLIND SPOT, measured rather than guessed: against `main` this finds
#: 13 of the 14. The miss is `docs.tsx`'s stamp rendered ALONGSIDE other
#: children, so there is no `>{…}<` to anchor on. Widening the rule fires on
#: keys and props, and a scanner that cries wolf gets suppressed. A green here
#: is not proof that no timestamp anywhere is unformatted.
UTC_RENDER = re.compile(
    r"""\.slice\(\s*\d+\s*,\s*\d+\s*\)\s*\n?\s*\.?\s*replace\(\s*['"]T['"]"""
    r"""|\bslice\(0,\s*19\)"""
    r"""|['"`][^'"`\n]*\bUTC\b[^'"`\n]*['"`]"""
    r"""|>\{\s*\w+\??\.(?:at|_at|\w+_at)\s*\}<""",
    re.X)


def _scan(text):
    return [ln.strip() for ln in text.splitlines() if UTC_RENDER.search(ln)]


def test_no_visible_utc_render_remains():
    hits = []
    for rel in SCANNED:
        path = os.path.join(REPO, rel)
        assert os.path.exists(path), f"scanner points at a missing file: {rel}"
        for line in _scan(io.open(path, encoding="utf-8").read()):
            hits.append(f"{rel}: {line}")
    assert not hits, "visible UTC render(s) reintroduced:\n" + "\n".join(hits)


def test_the_scanner_can_actually_fail():
    """⭐⭐ THE POSITIVE CONTROL, and the only reason the check above means
    anything. Fed the real pre-fix lines, it must catch every one."""
    for line in [
        "  const when = (at) => (at ?? '').slice(5, 16).replace('T', ' ')",
        "    ? ` — last seen ${o.lastSeen.slice(0, 16).replace('T', ' ')} UTC`",
        "  const s = at.slice(0, 19)",
        "  title={`recorded ${x} UTC`}",
        '        <span className="dim">{cur.at}</span>',
        "        <time>{mail.at}</time>",
        '          <span className="dim">{row.at}</span>',
        "        <span>{m._state_at}</span>",
    ]:
        assert _scan(line), f"the scanner MISSED a real defect: {line!r}"
    # …and must stay quiet on the fixed forms and on non-display uses, or it
    # gets suppressed and is worth nothing on the day it is right
    for line in ["  const when = fmtShort",
                 "  <span>{fmtFull(cur.at)}</span>",
                 "  <time>{fmtFull(mail.at)}</time>",
                 "  const parts = name.slice(0, 4)",
                 "  <Card key={`${mail.at}-${i}`} mail={mail} />",
                 "  const t = Date.parse(m.at)"]:
        assert not _scan(line), f"the scanner FALSE-POSITIVED on: {line!r}"


def test_the_backend_formats_no_clock_reading_anywhere():
    """⭐ The structural claim, checked against the source rather than
    asserted in a comment: `localtime` mints tokens and has no formatter."""
    src = io.open(os.path.join(REPO, "backend/orgtree/localtime.py"),
                  encoding="utf-8").read()
    for banned in ("strftime", "ZoneInfo", "astimezone(z", "%H:%M", "%I:%M"):
        assert banned not in src, \
            f"localtime.py should not format clock readings; found {banned!r}"


def main():
    print("localtime — user-visible timestamps in the user's zone\n")
    check("the token carries the canonical instant", test_token_carries_the_canonical_instant)
    check("epoch seconds and milliseconds normalise", test_epoch_seconds_and_milliseconds_normalise)
    check("a naive instant is read as UTC", test_a_naive_instant_is_read_as_utc)
    check("an unreadable instant yields nothing", test_an_unreadable_instant_yields_nothing)
    check("an unknown style falls back rather than emitting junk", test_an_unknown_style_falls_back_rather_than_emitting_junk)

    check("⭐ the durable row stores no clock reading", test_the_durable_row_stores_no_clock_reading)

    check("human copy tokenised, agent copy canonical", test_human_copy_is_tokenised_and_agent_copy_is_canonical)
    check("a notice header is tokenised too", test_a_notice_header_is_tokenised_too)
    check("a reply snapshot's stamp is tokenised, its gist is not", test_a_reply_snapshot_stamp_is_tokenised_but_its_gist_is_not)
    check("authored body text survives verbatim", test_authored_body_text_survives_verbatim)

    check("⭐ mail_marker_in survives a zone change, matching once", test_mail_marker_survives_a_zone_change)
    check("mail_marker_in still matches pre-change rows", test_mail_marker_still_matches_rows_written_before_this_change)
    check("mail_marker_in still needs the body", test_mail_marker_needs_the_body_too)

    check("_reset_label is a token, not a clock", test_reset_label_is_a_token_not_a_clock)
    check("_reset_label is stable across calls", test_reset_label_is_stable_across_calls)

    check("crash-report stamp tokenised, raw value kept", test_crash_report_stamp_is_tokenised_and_keeps_the_raw_value)
    check("a crash report with no stamp says unknown", test_crash_report_without_a_stamp_says_unknown)

    check("no visible-UTC render remains in the UI", test_no_visible_utc_render_remains)
    check("⭐ the scanner can actually fail (positive control)", test_the_scanner_can_actually_fail)
    check("⭐ the backend formats no clock reading anywhere", test_the_backend_formats_no_clock_reading_anywhere)

    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
