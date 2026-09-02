"""THE MACHINE ENVELOPE NEVER RENDERS AS THE USER'S WORDS — not even for one
poll (D-229).

    python backend/tests/test_prompt_view_race.py

User report, 2026-09-02: "i saw the turn envelope associated information for
a second there before it reverted to a normal user turn message".

How: the desk strips no markers, by ruling (the browser must never guess
authorship from marker-looking strings — orgstate.test.tsx pins it). The ONLY
thing that hides `[ORG STATE]`, `[PROVIDER USAGE]` and the raw `[MAIL]`
envelope is read_chat's projection of each provider user event through its
durable sidecar row (`<sid>.views.ndjson`). read_chat loaded that sidecar ONCE,
up front, then streamed the transcript — so a turn whose journal row and view
were appended in between reached the reader with no row to project it, and
the whole prompt rendered as the user's bubble until the next poll.

Every test here drives the real `read_chat` against a real transcript and a
real sidecar, and the two that matter most are deterministic HOOKS on the
exact race rather than a sleep: §1 appends row+view from inside the sidecar
loader (the reader has already loaded; the writer lands; the reader reaches
the row), §2 hands the reader a torn sidecar row (an append in progress).

  §1  the TOCTOU: a row+view appended after the sidecar was loaded still
      renders PROJECTED — the reader reloads on a fresh miss
  §2  a torn sidecar row (no trailing newline) is skipped, and the event it
      would have projected is HELD BACK for this poll, never rendered raw
  §3  a fresh event with no row at all is held back — and the pending
      bubble is still on screen, so the message is on screen exactly once
      throughout (msgvis's union), and hands over in one payload
  §4  past the grace, an unprojected event renders raw — the fail-open
      floor is kept (a message that never appears would be a gap)
  §5  the reload does not re-spend a row already consumed by an earlier
      identical prompt
  §6  `_prompt_is_fresh` on the edges
  §7  anti-vacuity: the raw render the fix removes IS what the pre-fix
      order produces — proved by driving the projection with the sidecar
      loaded before the append and no reload, the way read_chat used to
  §2b an UNCOVERED unprojected event (batch already confirmed) renders raw
      at once — never hidden (review round 1)
  §2c events that never have a projection (command echoes, remote-control
      prompts) are never held; §2c′ pins the gate itself, `_carries_envelope`,
      with a covered record that only the gate can let through (round 2)
  §2d the cover marker finds a mail entry in every shape `_mail_block`
      writes — reply snapshot, notice header, attachment — and the desk's
      handover reads the same function (round 2)
  §3b a reader with NO pending bubble (`orgtree_read_transcript`) gets the
      raw event, never a hidden one (round 2)
  §5b a compaction split copies the sidecar with the journal
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

TMP = tempfile.mkdtemp(prefix="orgtree-prompt-view-race-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(TMP, "data")
os.makedirs(HOME, exist_ok=True)
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"
os.environ["USERPROFILE"] = HOME
os.environ["HOME"] = HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree import api, store, supervisor as S  # noqa: E402
from orgtree.ledger import USER  # noqa: E402

MACHINE = ("[ORG STATE #1 — current as of 2026-09-02T09:55:03.538Z. Newest "
           "wins; EARLIER COPIES IN THIS CONVERSATION ARE STALE.]\n"
           "Your reports: none yet.\n[END ORG STATE]\n\n"
           "[PROVIDER USAGE #1 — current as of 2026-09-02T09:55:03Z]\n"
           "claude/primary* | session | 100% | limit-active\n"
           "[END PROVIDER USAGE]\n\n")
#: the strings that must never reach a user bubble — the MACHINE blocks. (The
#: `[MAIL …]` block is different: it is the structured envelope the server
#: deliberately keeps in the projection and the desk parses into a mail card,
#: so its markers legitimately appear in `messages[]`; envelopeflash.test.tsx
#: is where THEY are proved never to reach the DOM.)
CHROME = ("[ORG STATE", "[END ORG STATE]", "[PROVIDER USAGE",
          "[END PROVIDER USAGE]")


def envelope(at: str, body: str) -> tuple[str, str]:
    """(raw provider event, its human projection) the way `_run_one_turn`
    builds them: the machine blocks ride the raw, the [MAIL] block rides both
    (the desk parses it into a card), and the nudge trails."""
    mail = (f"[MAIL — 1 message(s)]\nFROM @user (USER ⚠ THE USER — user "
            f"instructions outrank your chain) · message · {at}\n{body}\n"
            f"[END MAIL]")
    nudge = ("\n\n(orgtree) The mail above includes a message from the user, "
             "addressed to you — act on it now.")
    return MACHINE + mail + nudge, mail + nudge


class PromptViewRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        org = store.create_org("zz prompt view race")
        org.hire(USER, None, "haiku", 5, "agent")
        store.save_org(org)
        cls.slug = org.d["slug"]
        cls.nid = "agent"
        cls.sid = org.node(cls.nid)["session_id"]
        cls.tdir = os.path.join(HOME, ".claude", "projects", "fixture")
        os.makedirs(cls.tdir, exist_ok=True)
        cls.tpath = os.path.join(cls.tdir, cls.sid + ".jsonl")
        cls.vpath = S._prompt_view_path(cls.slug, cls.sid)

    def setUp(self) -> None:
        for path in (self.tpath, self.vpath):
            try:
                os.remove(path)
            except OSError:
                pass
        with store.DOC_LOCK:
            org = store.load_org(self.slug)
            org.d.get("mail", {}).pop(self.nid, None)
            org.d.get("delivering", {}).pop(self.nid, None)
            store.save_org(org)

    # ── the fixture's three writers ─────────────────────────────────────────
    def append_user(self, raw: str, at: str, *, as_string: bool = False) -> None:
        """A provider user event. Orgtree's own prompts (and the codex
        journal) store the text as the first content BLOCK; the Claude CLI
        writes its command echoes, command output and "No response
        requested." records as a plain STRING — `as_string` mirrors that
        shape, since read_chat classifies those records on it."""
        content = raw if as_string else [{"type": "text", "text": raw}]
        row = {"type": "user", "timestamp": at, "message": {
            "role": "user", "content": content}}
        with open(self.tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    #: how much of a torn row is on disk when the reader looks
    TORN_TAIL = 24

    def append_view(self, raw: str, visible: str, at: str,
                    torn: bool = False) -> None:
        """The sidecar row, complete — or TORN: the head of an append in
        progress (a row longer than the writer's buffer lands in more than
        one write), cut before its closing brace so it is not valid JSON."""
        import hashlib
        row = {"v": 1, "sha256": hashlib.sha256(raw.encode()).hexdigest(),
               "chars": len(raw), "visible": visible, "at": at}
        line = json.dumps(row, ensure_ascii=False) + "\n"
        self._torn_tail = line[-self.TORN_TAIL:]
        os.makedirs(os.path.dirname(self.vpath), exist_ok=True)
        with open(self.vpath, "a", encoding="utf-8") as f:
            f.write(line[:-self.TORN_TAIL] if torn else line)

    def older_projected_row(self) -> str:
        """A transcript that already EXISTS, with one projected row an hour
        old — so read_chat gets past its "no transcript yet" early return and
        the sidecar loader actually runs. Returns the visible text."""
        old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                            time.gmtime(time.time() - 3600))
        raw0, vis0 = envelope(old, "an earlier, settled question")
        self.append_view(raw0, vis0, old)
        self.append_user(raw0, old)
        return vis0

    def complete_torn_view(self) -> None:
        """…and the writer's second write lands."""
        with open(self.vpath, "a", encoding="utf-8") as f:
            f.write(self._torn_tail)

    def covering_batch(self, body: str) -> tuple[str, str]:
        """The user's message drained for a turn the way `_run_one_turn`
        drains it: out of the mailbox, into an UNCONFIRMED `delivering` batch
        — which is what keeps the pending bubble on screen and is the only
        state in which read_chat may hold the event back. Returns (at, tok)."""
        with store.DOC_LOCK:
            org = store.load_org(self.slug)
            org.post_mail(USER, self.nid, body, kind="message")
            mail = org.take_mail(self.nid)
            tok = S._journal_drain(org, self.nid, mail, None, "turn")
            store.save_org(org)
        return mail[0]["at"], tok

    # ── the reader's two surfaces ───────────────────────────────────────────
    def chat(self) -> dict:
        return S.read_chat(store.load_org(self.slug), self.nid)

    def user_texts(self) -> list[str]:
        return [m["text"] for m in self.chat()["messages"]
                if m["role"] == "user"]

    def assert_no_chrome(self, texts: list[str]) -> None:
        for t in texts:
            for marker in CHROME:
                self.assertNotIn(marker, t, f"machine chrome {marker!r} "
                                            f"rendered as the user's words")

    # §1 ───────────────────────────────────────────────────────────────────
    def test_row_and_view_appended_after_the_sidecar_load_still_project(self) -> None:
        vis0 = self.older_projected_row()
        at = S.now_iso()
        raw, visible = envelope(at, "please look at the fallback")
        real_load = S._load_prompt_views
        fired = {"n": 0}

        def racy_load(slug: str, sid: str):
            views = real_load(slug, sid)
            fired["n"] += 1
            if fired["n"] == 1:
                # the writer lands BETWEEN the reader's sidecar load and its
                # transcript read — view first, then the row, the order
                # `_open_journal` writes them in
                self.append_view(raw, visible, at)
                self.append_user(raw, at)
            return views

        with mock.patch.object(S, "_load_prompt_views", racy_load):
            texts = self.user_texts()
        self.assertGreaterEqual(fired["n"], 2,
                                "the reader must RELOAD the sidecar on the miss "
                                "(anti-vacuity: one load means no reload path ran)")
        self.assertEqual(texts, [vis0, visible])
        self.assert_no_chrome(texts)

    # §2 ───────────────────────────────────────────────────────────────────
    def test_torn_sidecar_row_is_skipped_and_the_event_held_back(self) -> None:
        # the message is still covered by its pending bubble (unconfirmed
        # batch) — the one state in which holding the event back is gap-free
        at, tok = self.covering_batch("torn on the way to disk")
        raw, visible = envelope(at, "torn on the way to disk")
        self.append_view(raw, visible, at, torn=True)
        self.append_user(raw, at)
        chat = self.chat()
        texts = [m["text"] for m in chat["messages"] if m["role"] == "user"]
        self.assertEqual(texts, [], "a torn row must not render the event raw")
        self.assertEqual(chat.get("prompts_withheld"), 1)
        # the append completes; the next poll draws it projected
        self.complete_torn_view()
        chat2 = self.chat()
        self.assertEqual([m["text"] for m in chat2["messages"]
                          if m["role"] == "user"], [visible])
        self.assertEqual(chat2.get("prompts_withheld"), 0)
        S._confirm_delivered(self.slug, self.nid, [tok])

    # §2b ──────────────────────────────────────────────────────────────────
    def test_uncovered_unprojected_event_renders_raw_never_hidden(self) -> None:
        """Review round 1, finding 1: the sidecar write FAILED and the batch
        is already confirmed, so the pending bubble is gone. Holding the
        event back would put the message on screen ZERO times — the state
        INV-018 forbids and D-50 calls the worse lie. It must render, raw,
        and the payload must not claim anything is held."""
        body = "revert the migration"
        at, tok = self.covering_batch(body)
        S._confirm_delivered(self.slug, self.nid, [tok])    # bubble gone
        raw, _visible = envelope(at, body)
        self.append_user(raw, at)                             # no sidecar row
        p = api.node_chat(self.slug, self.nid)
        self.assertEqual(p.get("prompts_withheld"), 0)
        self.assertEqual(p["pending_mail"], [])
        shown = [m["text"] for m in p["messages"]
                 if m["role"] == "user" and body in m["text"]]
        self.assertEqual(len(shown), 1,
                         "uncovered + unprojected must fall open to raw")
        self.assertEqual(shown[0], raw)

    # §2c ──────────────────────────────────────────────────────────────────
    def test_events_that_never_have_a_projection_are_never_held(self) -> None:
        """Review round 1, finding 2: a slash-command echo, a prompt typed
        into a remote-controlled CLI, an old-CLI command output — none ever
        has a sidecar row and none carries the envelope. Fresh or not, they
        render at once."""
        at = S.now_iso()
        # the CLI's own record shapes: string content for its command echo
        # and its "No response requested." marker; a remote-control prompt
        # arrives as the ordinary text block
        self.append_user("<command-name>/context</command-name>", at,
                         as_string=True)
        self.append_user("hello from the remote-controlled terminal", at)
        self.append_user("No response requested.", at, as_string=True)
        chat = self.chat()
        self.assertEqual([m["text"] for m in chat["messages"]
                          if m["role"] == "user"],
                         ["/context", "hello from the remote-controlled terminal"])
        self.assertEqual(chat.get("prompts_withheld"), 0,
                         "nothing was held, and a dropped machine row must "
                         "not inflate the count (finding 3)")

    # §3 ───────────────────────────────────────────────────────────────────
    def test_held_back_event_is_still_on_screen_once_via_the_pending_bubble(self) -> None:
        body = "the message that must not blink"
        with store.DOC_LOCK:
            org = store.load_org(self.slug)
            org.post_mail(USER, self.nid, body, kind="message")
            mail = org.take_mail(self.nid)
            at = mail[0]["at"]
            tok = S._journal_drain(org, self.nid, mail, None, "turn")
            store.save_org(org)
        raw, visible = envelope(at, body)
        # the provider echoed the event; the sidecar row is not there yet
        self.append_user(raw, at)

        def renders(payload: dict) -> int:
            return (sum(1 for m in payload["messages"]
                        if m["role"] == "user" and body in m["text"])
                    + sum(1 for m in payload["pending_mail"]
                          if body in m["body"]))

        p1 = api.node_chat(self.slug, self.nid)
        self.assertEqual(p1.get("prompts_withheld"), 1)
        self.assertEqual(renders(p1), 1, "held back in messages, covered by "
                                         "the pending bubble — exactly once")
        self.assert_no_chrome([m["text"] for m in p1["messages"]])
        # no turn owns the planted batch, but it is seconds old: the receipt
        # stays benign (`turn`) inside the strand hysteresis and would read
        # `stranded` only once the batch has been unowned past the grace
        # (test_midturn_mail_ingress §5 ages one and checks that)
        self.assertEqual([m.get("stage") for m in p1["pending_mail"]], ["turn"])
        # the row lands; the SAME payload retires the bubble and draws the
        # projected row (D-54's one-payload handover)
        self.append_view(raw, visible, at)
        p2 = api.node_chat(self.slug, self.nid)
        self.assertEqual(p2.get("prompts_withheld"), 0)
        self.assertEqual(renders(p2), 1)
        self.assertEqual([m["text"] for m in p2["messages"]
                          if m["role"] == "user"], [visible])
        self.assertEqual(p2["pending_mail"], [])
        S._confirm_delivered(self.slug, self.nid, [tok])

    # §4 ───────────────────────────────────────────────────────────────────
    def test_past_the_grace_an_unprojected_event_renders_raw(self) -> None:
        old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                            time.gmtime(time.time() - 3600))
        raw, _visible = envelope(old, "an hour ago, no sidecar row ever")
        self.append_user(raw, old)
        chat = self.chat()
        self.assertEqual([m["text"] for m in chat["messages"]
                          if m["role"] == "user"], [raw],
                         "fail-open: past the grace the raw event is the "
                         "honest render, not a permanent gap")
        self.assertEqual(chat.get("prompts_withheld"), 0)

    def test_the_grace_edge_is_the_constant(self) -> None:
        # just inside the grace (and covered): held; just outside: raw
        inside = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(
            time.time() - S.PROMPT_VIEW_GRACE_S + 3.0))
        outside = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(
            time.time() - S.PROMPT_VIEW_GRACE_S - 3.0))
        at_in, tok_in = self.covering_batch("inside the grace")
        raw_in, _ = envelope(at_in, "inside the grace")
        raw_out, _ = envelope(outside, "outside the grace")
        self.append_user(raw_out, outside)
        self.append_user(raw_in, inside)
        chat = self.chat()
        self.assertEqual([m["text"] for m in chat["messages"]
                          if m["role"] == "user"], [raw_out])
        self.assertEqual(chat.get("prompts_withheld"), 1)
        S._confirm_delivered(self.slug, self.nid, [tok_in])

    # §5 ───────────────────────────────────────────────────────────────────
    def test_reload_does_not_respend_a_consumed_row(self) -> None:
        at1 = S.now_iso()
        raw = MACHINE + "identical bytes twice"     # same envelope, twice
        self.append_view(raw, "first projection", at1)
        self.append_user(raw, at1)
        at2 = S.now_iso()
        self.append_user(raw, at2)             # the second has NO row (yet)
        chat = self.chat()
        texts = [m["text"] for m in chat["messages"] if m["role"] == "user"]
        # the first event keeps its projection; the second is NOT handed the
        # first's row on reload — and, being uncovered (no pending bubble),
        # it falls open to raw rather than being held
        self.assertEqual(texts, ["first projection", raw])
        self.assertEqual(chat.get("prompts_withheld"), 0)

    # §5b ──────────────────────────────────────────────────────────────────
    def test_compaction_copies_the_sidecar_with_the_journal(self) -> None:
        """A compaction split copies the predecessor's history into the
        successor's transcript, raw envelopes and original timestamps
        included. Without the sidecar the successor's read_chat has no
        projection for any of it — old rows render raw, fresh ones are held
        back (found by test_codex_dispatch §6 when the hold-back landed). The
        sidecar travels with the transcript."""
        at = S.now_iso()
        raw, visible = envelope(at, "history that must survive the split")
        self.append_view(raw, visible, at)
        self.append_user(raw, at)
        new_sid = self.sid + "-successor"
        self.assertTrue(S._copy_prompt_views(self.slug, self.sid, new_sid))
        self.assertEqual(S._load_prompt_views(self.slug, new_sid),
                         S._load_prompt_views(self.slug, self.sid))
        # the successor projects the copied row exactly as the predecessor did
        views = S._load_prompt_views(self.slug, new_sid)
        projected, text = S._take_prompt_view(views, raw, at)
        self.assertTrue(projected)
        self.assertEqual(text, visible)
        # no predecessor sidecar → nothing copied, nothing raised
        self.assertFalse(S._copy_prompt_views(self.slug, "no-such-sid", new_sid + "2"))
        self.assertFalse(S._copy_prompt_views(self.slug, self.sid, self.sid))
        os.remove(S._prompt_view_path(self.slug, new_sid))

    # §6 ───────────────────────────────────────────────────────────────────
    def test_prompt_is_fresh_edges(self) -> None:
        now = time.time()
        self.assertFalse(S._prompt_is_fresh(None, now))
        self.assertFalse(S._prompt_is_fresh("not a timestamp", now))
        self.assertTrue(S._prompt_is_fresh(S.now_iso(), now))
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60))
        self.assertFalse(S._prompt_is_fresh(old, now))

    # §7 ───────────────────────────────────────────────────────────────────
    def test_anti_vacuity_the_pre_fix_order_renders_raw(self) -> None:
        """The instrument can see the leak: project the way read_chat used
        to — sidecar loaded BEFORE the append, no reload — and the raw
        envelope is what comes out."""
        at = S.now_iso()
        raw, visible = envelope(at, "the leak the fix removes")
        stale_views = S._load_prompt_views(self.slug, self.sid)   # before
        self.append_view(raw, visible, at)
        self.append_user(raw, at)
        projected, text = S._take_prompt_view(stale_views, raw, at)
        self.assertFalse(projected)
        self.assertEqual(text, raw)
        self.assertTrue(any(m in text for m in CHROME),
                        "the pre-fix output carries the chrome — so a clean "
                        "§1 is clean, not blind")
        # …and the fixed reader, same disk state, projects it
        self.assertEqual(self.user_texts(), [visible])

    # §2c′ ─────────────────────────────────────────────────────────────────
    def test_carries_envelope_is_the_gate_and_is_pinned(self) -> None:
        """Review round 2, finding N3: §2c's records rendered for a reason
        other than the gate (nothing covered them, so `_covered_by_pending`
        alone let them through). Here the gate is pinned directly, and with a
        record that IS covered — a human typing the mail marker into a
        remote-controlled terminal — so that only the gate can let it render.
        Mutant M11 (`_carries_envelope` → True) must die here."""
        for shape in ("<command-name>/context</command-name>",
                      "hello from the remote-controlled terminal",
                      "No response requested.",
                      "a human who types [MAIL — 1 message(s)] literally"):
            self.assertFalse(S._carries_envelope(shape), shape)
        self.assertTrue(S._carries_envelope(MACHINE + "x"))
        self.assertTrue(S._carries_envelope("[PROVIDER USAGE #2 — x]\n"))
        # the notices block rides the raw event ONLY (round 2, N9): a
        # boundary-fed event whose usage block was skipped has no other header
        self.assertTrue(S._carries_envelope(
            "[ORG NOTICES — 1 change(s) since your last turn]\n- x\n[END NOTICES]"))
        body = "the covered message"
        at, tok = self.covering_batch(body)
        typed = f"typed into the remote-controlled terminal: · {at}\n{body}"
        self.append_user(typed, at)          # fresh, unprojected, COVERED
        chat = self.chat()
        self.assertEqual([m["text"] for m in chat["messages"]
                          if m["role"] == "user"], [typed],
                         "no envelope → never held, covered or not")
        self.assertEqual(chat.get("prompts_withheld"), 0)
        S._confirm_delivered(self.slug, self.nid, [tok])

    # §2d ──────────────────────────────────────────────────────────────────
    def test_mail_marker_matches_every_mail_block_shape(self) -> None:
        """Review round 2, finding N2: the cover marker — now one function
        with `node_chat._in_transcript`'s — must find a mail entry in the
        text `_mail_block` actually writes: a plain message, an FR-05 reply
        whose snapshot line sits between stamp and body, a passive notice
        whose header carries a trailing clause, an attachment, a body that
        opens with whitespace. And it must NOT match a different entry."""
        base = {"id": "m1", "from": USER, "relationship": "USER",
                "kind": "message", "at": "2026-09-02T10:00:00.000Z",
                "body": "revert the migration"}
        shapes = {
            "plain": dict(base),
            "reply": dict(base, reply_to={
                "id": "x", "at": "2026-09-02T09:00:00.000Z",
                "gist": "the earlier question", "from": "@boss"}),
            "notice": dict(base, kind="notice"),
            "attachment": dict(base, attachments=[
                {"path": "uploads/a.txt", "bytes": 12}]),
            "leading whitespace": dict(base, body="\n  starts with a newline"),
        }
        for label, entry in shapes.items():
            text, _imgs = S._mail_block([entry], self.slug, self.nid, inline=True)
            raw = MACHINE + text + "\n\n(orgtree) act on it"
            self.assertTrue(S.mail_marker_in(raw, entry), label)
            with store.DOC_LOCK:
                org = store.load_org(self.slug)
                org.d.setdefault("delivering", {})[self.nid] = [
                    {"tok": "t-" + label, "at": entry["at"], "via": "turn",
                     "mail": [entry]}]
                store.save_org(org)
            self.assertTrue(S._covered_by_pending(
                store.load_org(self.slug), self.nid, raw), label)
        # identity, not resemblance
        text, _ = S._mail_block([dict(base)], self.slug, self.nid, inline=True)
        self.assertFalse(S.mail_marker_in(
            text, dict(base, at="2026-09-02T10:00:00.001Z")), "another stamp")
        self.assertFalse(S.mail_marker_in(
            text, dict(base, body="a different body")), "another body")
        self.assertFalse(S.mail_marker_in(text, {"body": "   "}), "legacy, empty")
        self.assertTrue(S.mail_marker_in(
            text, {"body": "revert the migration"}), "legacy, bare body")
        # …and the desk's handover reads the same function: a REPLY-shaped
        # entry whose projected transcript row is on screen is handed over —
        # not shown a second time as a pending bubble (the pre-fix needle
        # missed the shape and the bubble stayed up beside the row)
        at = S.now_iso()
        entry = dict(shapes["reply"], at=at)
        text, _ = S._mail_block([entry], self.slug, self.nid, inline=True)
        nudge = "\n\n(orgtree) act on it"
        raw, visible = MACHINE + text + nudge, text + nudge
        with store.DOC_LOCK:
            org = store.load_org(self.slug)
            org.d.setdefault("delivering", {})[self.nid] = [
                {"tok": "t-reply-live", "at": at, "via": "turn", "mail": [entry]}]
            store.save_org(org)
        self.append_view(raw, visible, at)
        self.append_user(raw, at)
        p = api.node_chat(self.slug, self.nid)
        self.assertEqual(p["pending_mail"], [],
                         "the transcript row carries the reply → handed over")
        self.assertEqual(sum(1 for m in p["messages"] if m["role"] == "user"
                             and "revert the migration" in m["text"]), 1)

    # §3b ──────────────────────────────────────────────────────────────────
    def test_a_reader_with_no_bubble_gets_the_event_raw_not_hidden(self) -> None:
        """Review round 2, finding N4: `orgtree_read_transcript` returns
        `messages[]` only — nothing in that payload covers a held event, so
        holding it would show the message ZERO times to an agent reading its
        report inside the grace. That reader passes `hold_back=False` and
        gets the raw event; the desk's reader, same disk state, still holds."""
        body = "the message a superior is reading for"
        at, tok = self.covering_batch(body)
        raw, _visible = envelope(at, body)
        self.append_user(raw, at)                        # no sidecar row yet
        held = S.read_chat(store.load_org(self.slug), self.nid)
        self.assertEqual(held.get("prompts_withheld"), 1, "the desk's read holds")
        shown = S.read_chat(store.load_org(self.slug), self.nid, hold_back=False)
        self.assertEqual([m["text"] for m in shown["messages"]
                          if m["role"] == "user"], [raw])
        self.assertEqual(shown.get("prompts_withheld"), 0)
        # the transcript tool is wired to it (structural: the tool door needs
        # a driven agent to exercise end to end)
        import inspect
        self.assertIn("read_chat(org, target, hold_back=False)",
                      inspect.getsource(api))
        S._confirm_delivered(self.slug, self.nid, [tok])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
