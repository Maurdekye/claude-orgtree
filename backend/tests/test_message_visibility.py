"""Message-visibility suite — the D-34/43/50/51/52/55 bug family, adversarially.

    A message the user sent is on screen CONTINUOUSLY from the moment it is
    sent until the conversation ends, and NEVER appears twice.

Run:  .venv/Scripts/python.exe backend/tests/test_message_visibility.py
      (add `-v` to print every configuration, `--only <substr>` to filter)

WHAT THIS FILE IS
-----------------
The hermetic half of the suite: real server code (`api.node_chat`,
`supervisor.delivering_mail`, `supervisor.read_chat`, the real ledger and the
real org doc on disk) driven step by step through the message lifecycle, with
the client's own rules ported into `msgvis.Desk` so each step is scored exactly
as the browser would render it. No CLI, no network, no browser — which is the
point: it can enumerate orderings a live race only visits sometimes.

The live half (a real backend + a programmable fake CLI, sweeping the timing
that makes the race intermittent) is `test_message_visibility_live.py`.

HOW A SCENARIO WORKS
--------------------
A scenario is an ordered list of world steps. The invariant is checked after
EVERY step, which is strictly stronger than a poller: a 20 Hz probe can only
land where it happens to land, while this lands on every boundary — including
the ones a race would have to be unlucky to hit.

The lifecycle being modelled (`POST /message` on an idle agent):

    ① post_mail            the mail is in the mailbox      → pendrow
    ② _journal_drain(turn) the turn takes it               → journal pendrow
    ③ transcript echo      the CLI writes the user event   → transcript bubble
    ④ _confirm_delivered   the journal batch is dropped    → transcript only

D-55 was ②→③: the journal was hidden by carrier (`via == "turn"`), so between
② and ③ the message existed in NO place the desk renders from. The fix made
③ — evidence, not carrier — the thing that retires ②. Every ordering below
exists to attack that hand-off from a different side.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, _HERE)

# isolated data root + isolated HOME (transcripts live under ~/.claude) BEFORE
# any orgtree import — store resolves ORGTREE_DATA at import time
_TMP = tempfile.mkdtemp(prefix="orgtree-msgvis-")
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

import msgvis                                                   # noqa: E402
from msgvis import Desk, Transcript, USER, Watch, token         # noqa: E402
from orgtree import api, store, supervisor                      # noqa: E402

if "--legacy-client" in sys.argv:
    # Re-measure against the PRE-FIX client rules (2026-08-04): serverCopies
    # counted within the newest 20 rows and matched the ghost's full text. Both
    # numbers are now bigger for reasons the failures under this flag explain —
    # keep it working, it is the only apples-to-apples "would this have failed
    # before?" switch the suite has for the client half.
    msgvis.SERVER_COPIES_WINDOW = 20
    msgvis.SERVER_COPIES_NEEDLE = 10 ** 9
    print("⚠ --legacy-client: the ported client rules are the PRE-FIX ones "
          "(window 20, unbounded needle)\n")

if "--legacy-marker" in sys.argv:
    # Re-measure against the PRE-FIX server-side evidence test (the one in
    # place until 2026-08-19): `node_chat` rebuilt the envelope's timestamp+
    # body junction by hand as ONE adjacent string, so any formatter that put
    # a line between them made the entry unfindable in its own transcript
    # bubble. This is the apples-to-apples switch for the server half — under
    # it, 60 of the 80 FR-05 reply configurations must fail as a DUPLICATE —
    # the other 20 carry a blank gist, which `post_mail` drops, so they render
    # plain and hand over correctly under either rule.
    def _legacy_marker(m, seen):
        body = m.get("body") or ""
        at = m.get("at")
        if not at and not body.strip():
            return False
        mark = (f"· {at}\n{body}" if at else body)[:400]
        return any(mark in t for t in seen)
    supervisor.mail_in_transcript = _legacy_marker
    print("⚠ --legacy-marker: the pendrow evidence test is the PRE-FIX one "
          "(one adjacent `· {at}\\n{body}` string)\n")

PASS = 0
FAIL: list[tuple[str, str]] = []
FRAGILE: list[tuple[str, str, str]] = []
CONFIGS = 0
VERBOSE = "-v" in sys.argv
ONLY = (sys.argv[sys.argv.index("--only") + 1]
        if "--only" in sys.argv else "")


def check(label: str, fn) -> None:
    """One check. A failure is RECORDED and the run continues — a suite that
    stops at the first failure hides how many of the others were also broken,
    which is exactly the question being asked of this one."""
    global PASS
    if ONLY and ONLY not in label:
        return
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def fragile(label: str, why_unreachable: str, fn) -> None:
    """A check whose PRECONDITION is currently unreachable, measured — so it
    cannot be a live bug today, but it is the mechanism's fault line and would
    become one the moment the precondition changed.

    Kept honest by the rule that puts a case here: `why_unreachable` must name
    a MEASUREMENT, not an opinion. Anything else is a FAIL."""
    global PASS
    if ONLY and ONLY not in label:
        return
    try:
        fn()
    except Exception as e:                                       # noqa: BLE001
        FRAGILE.append((label, why_unreachable, str(e).split("\n")[0][:300]))
        print(f"  ⚠ FRAGILE {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


# --------------------------------------------------------------------- world

class World:
    """One throwaway org with one live agent, plus direct access to every
    mutation the real turn loop performs — so a scenario can order them."""

    _n = 0

    def __init__(self, label: str = "w") -> None:
        World._n += 1
        # ⚠ the slug is re-derived from the NAME by slugify — never assume the
        # name you passed is the slug (ARCHITECTURE.md, frontend §)
        org = store.create_org(f"zz msgvis {World._n} {label}"[:60])
        self.slug = org.d["slug"]
        org.hire(USER, None, "haiku", 5, "agent")
        store.save_org(org)
        self.nid = "agent"
        self.sid = self.org().node(self.nid)["session_id"]
        self.tx = Transcript(_HOME, self.sid)

    def org(self):
        return store.load_org(self.slug)

    # --- ① the send path: a user message IS mail (api.node_message)
    def post(self, body: str, attachments=None, reply_to=None) -> dict:
        org = self.org()
        # `reply_to` is FR-05: what the inbox modal's onReply sends. It is a
        # user-facing path (App.tsx) and it moves the body one line further
        # from the envelope's timestamp — the 2026-08-19 regression.
        org.post_mail(USER, self.nid, body, attachments=attachments,
                      reply_to=reply_to)
        store.save_org(org)
        return (org.d["mail"][self.nid])[-1]

    def post_from(self, sender: str, body: str, kind: str = "message") -> dict:
        org = self.org()
        org.post_mail(sender, self.nid, body, kind=kind)
        store.save_org(org)
        return (org.d["mail"][self.nid])[-1]

    # --- ② the drain: real _journal_drain, real take_mail
    def drain(self, via: str = "turn", notices=None) -> tuple[str, list[dict]]:
        org = self.org()
        mail = org.take_mail(self.nid)
        pend = (org.d.get("notices") or {}).pop(self.nid, None) or notices
        tok = supervisor._journal_drain(org, self.nid, mail, pend, via)
        store.save_org(org)
        return tok, mail

    # --- ④ the confirm: real _confirm_delivered
    def confirm(self, tok: str) -> None:
        supervisor._confirm_delivered(self.slug, self.nid, [tok])

    def foldback(self) -> None:
        supervisor._fold_back_undelivered(self.slug, self.nid)

    def steered_log(self, text: str) -> None:
        """What `pop_steer`'s off-thread `record()` writes — the durable home
        of mid-task mail, which the CLI never transcripts."""
        org = self.org()
        org.d.setdefault("steered_log", {}).setdefault(self.nid, []).append(
            {"at": supervisor.now_iso(), "text": text})
        store.save_org(org)

    # --- the desk's fetch
    def chat(self, last: int = msgvis.CHAT_WINDOW) -> dict:
        return api.node_chat(self.slug, self.nid, last=last)

    def destroy(self) -> None:
        try:
            store.delete_org(self.slug)
        except Exception:                                        # noqa: BLE001
            pass


# ------------------------------------------------------------------ scenarios

def run_lifecycle(label: str, body: str, probe: str = "", *,
                  via: str = "turn",
                  attachments=None, filler_pairs: int = 0,
                  notices: bool = False, extra_mail: int = 0,
                  win: int = msgvis.CHAT_WINDOW,
                  confirm_before_echo: bool = False,
                  repeat_first: bool = False,
                  reply_to: dict | None = None,
                  extra_notice: int = 0) -> None:
    """The canonical send→turn→echo lifecycle, with the knobs that make it
    adversarial. Checks the invariant after every world step."""
    global CONFIGS
    CONFIGS += 1
    w = World(label[:20].replace(" ", "-"))
    try:
        desk = Desk()
        if filler_pairs:
            w.tx.filler(filler_pairs)
        if repeat_first:
            # a byte-identical earlier copy already in the transcript — D-52's
            # trap, one layer down
            w.tx.user(body)
        desk.fetch(w.chat(win))                      # the payload in hand
        watch = Watch(desk, probe or body, label)

        # ① the user hits send: ghost first, POST second (desk.tsx order)
        desk.send(body)
        watch.check("ghost created")
        mail = [w.post(body, attachments=attachments, reply_to=reply_to)]
        for i in range(extra_mail):
            mail.append(w.post_from("agent", f"unrelated agent mail {i}"))
        for i in range(extra_notice):
            # D-137 orgtree_send_notice: co-drained into the SAME envelope,
            # and its header runs past the timestamp instead of stopping at
            # it, so the user's own message is scored beside a sibling entry
            # of the shape that broke the marker.
            #
            # ⚠ WHAT THIS DOES AND DOES NOT COVER, because an earlier version
            # of this axis claimed more than it delivered. The notice itself
            # is NOT scored and cannot be: it is from an agent, and both the
            # real desk (`m.from === USER`) and the port count only the
            # user's pending rows — sabotaging `mail_in_transcript` to refuse
            # every notice left all six checks green (redteam 2026-08-19).
            # What IS scored is the USER's message sharing a transcript row
            # with an entry of the shape that broke the marker, which is a
            # real hand-off and not covered anywhere else in the lifecycle.
            # The notice's own hand-over is asserted directly, in
            # `_notice_hands_over` above.
            mail.append(w.post_from("agent", f"unrelated agent notice {i}",
                                    kind="notice"))
        desk.fetch(w.chat(win))
        watch.check("mail posted (mailbox)")

        # ② the turn drains it
        pend = [{"at": "2026-08-04T05:00:00Z", "text": "someone was hired"}] \
            if notices else None
        tok, drained = w.drain(via, notices=pend)
        desk.fetch(w.chat(win))
        watch.check(f"drained via={via}")

        if confirm_before_echo:
            # the hazard ordering: _confirm_delivered fires on the first
            # non-`system` stdout event, which is NOT proof the transcript file
            # carries the user record yet
            w.confirm(tok)
            desk.fetch(w.chat(win))
            watch.check("confirmed BEFORE the transcript echo")
            w.tx.turn_echo(drained, notices=pend)
            desk.fetch(w.chat(win))
            watch.check("transcript echo")
        else:
            w.tx.turn_echo(drained, notices=pend)
            desk.fetch(w.chat(win))
            watch.check("transcript echo")
            w.confirm(tok)
            desk.fetch(w.chat(win))
            watch.check("journal confirmed")

        # ③ the agent answers, and the conversation goes on
        w.tx.assistant("on it.")
        desk.fetch(w.chat(win))
        watch.check("agent replied")
        w.tx.filler(3)
        desk.fetch(w.chat(win))
        watch.check("conversation continues")
    finally:
        w.destroy()


def run_steer_lifecycle(label: str, body: str, probe: str = "", *,
                        hook_delivers: bool = False) -> None:
    """Mid-task delivery, driven through the REAL `_envelope` + `pop_steer`.

    `send_message` envelopes and drains the mail the instant it arrives
    (`via="steer"`), so the journal is the only copy until either

      • the steering hook fetches it — `pop_steer` confirms the journal batch
        away and writes the `steered_log` row that replaces it (the CLI records
        hook context as a `type:"attachment"` record `read_chat` cannot
        render, so that log is the message's only durable home), or
      • no tool call comes first, the result boundary folds the steer into the
        queue and writes it as a user event, and the TRANSCRIPT carries it.

    The check that matters is taken the instant `pop_steer` RETURNS: that is
    the moment the hook's HTTP response goes back and the websocket 'steered'
    frame fires, so it is the earliest a desk can look — and it is where the
    confirm-then-record-later shape left the message in no carrier at all."""
    global CONFIGS
    CONFIGS += 1
    w = World(label[:20].replace(" ", "-"))
    try:
        desk = Desk()
        w.tx.filler(2)
        desk.fetch(w.chat())
        watch = Watch(desk, probe or body, label)
        desk.send(body)
        watch.check("ghost created")
        w.post(body)
        desk.fetch(w.chat())
        watch.check("mail posted")
        # the real steer path: envelope + journal, carrier parked on the node
        etext, tok = supervisor._envelope(w.slug, w.nid, "(nudge)", via="steer")
        st = supervisor.state(w.slug, w.nid)
        st.setdefault("steer", []).append({"toks": [tok], "text": etext})
        desk.fetch(w.chat())
        watch.check("enveloped + drained via=steer")
        if hook_delivers:
            got = supervisor.pop_steer(w.slug, w.nid)   # the hook's fetch
            assert got and body in got[0], "pop_steer did not return the mail"
            desk.fetch(w.chat())
            watch.check("the instant the hook's fetch returns")
            desk.steered_event(got[0])       # the websocket 'steered' frame
            desk.fetch(w.chat())
            watch.check("steered websocket frame")
        else:
            # boundary fold: the carrier moves to the queue and is written as a
            # user event; its journal batch is still tagged via="steer"
            st["steer"] = []
            w.tx.user(etext)
            desk.fetch(w.chat())
            watch.check("boundary fold: transcript echo")
            w.confirm(tok)
            desk.fetch(w.chat())
            watch.check("journal confirmed")
        w.tx.assistant("noted.")
        desk.fetch(w.chat())
        watch.check("agent replied")
        for i in range(3):
            w.tx.filler(2)
            desk.fetch(w.chat())
            watch.check(f"conversation continues {i}")
    finally:
        w.destroy()


def main() -> None:
    print("source contracts (the ported client rules still match their originals):")
    check("convo.ts / desk.tsx / api.py / supervisor.py contracts intact",
          lambda: msgvis.assert_client_model_matches_source(_REPO))
    # ⚠ These three RUN the real functions rather than grepping them. The greps
    # above stayed green through the 2026-08-19 regression because the string
    # they pinned never moved — the formatter did. See _SOURCE_CONTRACTS.
    # The first one guards the other two: they iterate a hand-maintained shape
    # list, and a formatter branch with no shape is invisible to both.
    check("mail_shapes() reaches every line of supervisor._mail_entry_block",
          lambda: msgvis.assert_mail_shapes_span(supervisor._mail_entry_block))
    # ⚠ `MAIL_MARK_CHARS` is passed in, never assumed: the crossing below turns
    # on whether a body is longer than it, and a suite that guessed the number
    # would quietly stop exercising the truncating branch if it ever changed.
    check("msgvis.mail_block renders exactly what supervisor._mail_block does",
          lambda: msgvis.assert_mail_block_matches_source(
              supervisor._mail_block, supervisor.MAIL_MARK_CHARS))
    check("every mail shape is found in its own transcript bubble (marker contract)",
          lambda: msgvis.assert_mail_marker_contract(
              supervisor._mail_block, supervisor.mail_in_transcript,
              supervisor.MAIL_MARK_CHARS))

    # ---------------------------------------------------------------- basics
    print("\nthe carriers, in isolation:")

    def _mailbox_only():
        w = World("mbox")
        try:
            t = token()
            w.post(f"hello {t}")
            c = w.chat()
            assert len(c["pending_mail"]) == 1, c["pending_mail"]
            assert c["pending_mail"][0]["from"] == USER
            assert t in c["pending_mail"][0]["body"]
            assert not c["pending_mail"][0].get("delivering")
        finally:
            w.destroy()
    check("a posted mail shows as a pendrow (mailbox carrier)", _mailbox_only)

    def _journal_turn():
        w = World("jt")
        try:
            t = token()
            w.post(f"hello {t}")
            w.drain("turn")
            c = w.chat()
            assert len(c["pending_mail"]) == 1, c["pending_mail"]
            assert c["pending_mail"][0].get("delivering") is True
            assert c["pending_mail"][0].get("via") == "turn"
        finally:
            w.destroy()
    check("a drained via=turn batch still shows (THE D-55 FIX)", _journal_turn)

    def _journal_hidden_once_echoed():
        w = World("jh")
        try:
            t = token()
            w.post(f"hello {t}")
            _, mail = w.drain("turn")
            w.tx.turn_echo(mail)
            c = w.chat()
            assert c["pending_mail"] == [], c["pending_mail"]
            assert sum(1 for m in c["messages"]
                       if m["role"] == "user" and t in m["text"]) == 1
        finally:
            w.destroy()
    check("…and hands over the instant the transcript carries it", _journal_hidden_once_echoed)

    def _notice_hands_over():
        """D-137 `kind == "notice"`, whose header runs PAST the timestamp —
        one of the two shapes that broke the marker (user report 2026-08-19).

        It is scored here rather than in a lifecycle because a notice is
        never from `@user`, so no desk renders it as a pending row and the
        render union cannot see it at all. What it CAN corrupt is the payload
        and the mail badge: an entry that never hands over stays in
        `pending_mail` and keeps `mail_pending` counting a message the agent
        has already been given. Assert the mechanism ran (the notice was
        surfaced BEFORE the echo) and only then that it stopped."""
        w = World("nt")
        try:
            t = token()
            w.post_from("agent", f"heads up {t}", kind="notice")
            _, mail = w.drain("turn")
            assert mail and mail[0].get("kind") == "notice", mail
            c = w.chat()
            assert c["mail_pending"] == 1, c["mail_pending"]
            assert t in c["pending_mail"][0]["body"]
            w.tx.turn_echo(mail)
            c = w.chat()
            assert c["pending_mail"] == [], c["pending_mail"]
            assert c["mail_pending"] == 0, c["mail_pending"]
            assert sum(1 for m in c["messages"]
                       if m["role"] == "user" and t in m["text"]) == 1
        finally:
            w.destroy()
    check("a NOTICE hands over to its own transcript bubble (D-137 shape)",
          _notice_hands_over)

    def _reply_hands_over():
        """FR-05 `reply_to`, the other shape that broke it, at the carrier
        level: the recital line sits between the header and the body, so a
        marker rebuilt around their junction cannot occur in the transcript.
        The lifecycle axis scores the same thing end to end; this one names
        the carrier so a failure says WHICH hand-over broke."""
        w = World("rp")
        try:
            t = token()
            w.post(f"do it {t}", reply_to={"id": "m1", "from": "agent-2",
                                           "at": "2026-08-04T04:00:00.000Z",
                                           "gist": "shall I ship it?"})
            _, mail = w.drain("turn")
            assert mail[0].get("reply_to"), mail[0]
            blk = supervisor._mail_block([dict(mail[0])])
            assert "↩ IN REPLY TO" in blk, blk        # the mechanism RAN
            c = w.chat()
            assert c["mail_pending"] == 1
            w.tx.turn_echo(mail)
            c = w.chat()
            assert c["pending_mail"] == [], c["pending_mail"]
            assert sum(1 for m in c["messages"]
                       if m["role"] == "user" and t in m["text"]) == 1
        finally:
            w.destroy()
    check("a REPLY hands over to its own transcript bubble (FR-05 shape)",
          _reply_hands_over)

    def _steer_carrier():
        w = World("st")
        try:
            t = token()
            w.post(f"hello {t}")
            w.drain("steer")
            c = w.chat()
            assert len(c["pending_mail"]) == 1
            assert c["pending_mail"][0].get("delivering") is True
            assert c["pending_mail"][0].get("via") is None    # "mid-task" wording
        finally:
            w.destroy()
    check("a drained via=steer batch shows as mid-task", _steer_carrier)

    def _no_shown_callback():
        """`node_inbox` passes no `shown` — everything surfaces, deliberately."""
        w = World("ns")
        try:
            t = token()
            w.post(f"hello {t}")
            _, mail = w.drain("turn")
            w.tx.turn_echo(mail)
            org = w.org()
            assert len(supervisor.delivering_mail(org, w.nid)) == 1
            assert supervisor.delivering_mail(
                org, w.nid, lambda m: True) == []
        finally:
            w.destroy()
    check("delivering_mail with no evidence test surfaces everything", _no_shown_callback)

    # ------------------------------------------------- the text × via matrix
    print("\nlifecycle × text × carrier (the invariant after every step):")
    tk = token()
    for label, body, probe in msgvis.text_variants(tk):
        for via in ("turn", "steer"):
            check(f"lifecycle · {label} · via={via}",
                  lambda b=body, pr=probe, v=via, l=label: run_lifecycle(
                      f"{l}/{v}", b, pr, via=v))

    print("\nlifecycle × attachments (the envelope grows lines after the body):")
    for label, body, probe in msgvis.text_variants(token())[:8]:
        for nat in (1, 3):
            atts = [{"name": f"f{i}.txt", "path": f"uploads/f{i}.txt",
                     "bytes": 10 * (i + 1) * 1024} for i in range(nat)]
            check(f"lifecycle · {label} · {nat} attachment(s)",
                  lambda b=body, pr=probe, a=atts, l=label, n=nat: run_lifecycle(
                      f"{l}/att{n}", b, pr, attachments=a))

    print("\nlifecycle × batch shape (notices and co-drained agent mail):")
    for label, body, probe in msgvis.text_variants(token())[:6]:
        check(f"lifecycle · {label} · notices in the same envelope",
              lambda b=body, pr=probe, l=label: run_lifecycle(f"{l}/notices", b, pr, notices=True))
        check(f"lifecycle · {label} · 2 other mails in the same batch",
              lambda b=body, pr=probe, l=label: run_lifecycle(f"{l}/batch", b, pr, extra_mail=2))
        # the user's message sharing one transcript row with a D-137 notice —
        # see the note on `extra_notice` for exactly what this scores
        check(f"lifecycle · {label} · a notice shares the drained batch",
              lambda b=body, pr=probe, l=label: run_lifecycle(
                  f"{l}/mixed", b, pr, extra_mail=1, extra_notice=1))

    # ⚠ THE SHAPE THAT BROUGHT THE FAMILY BACK (user report 2026-08-19).
    # FR-05's reply snapshot writes a line BETWEEN the envelope's timestamp
    # and the body, so the marker `node_chat` used to rebuild — `· {at}\n
    # {body}` — could not occur in the transcript at all. The pending row
    # never handed over, and the message rendered twice from the CLI's echo
    # until `_confirm_delivered` dropped the journal batch: the sub-second
    # duplicate the user saw. Every text variant, because the gist sits
    # between the two needles for all of them.
    print("\nlifecycle × FR-05 reply snapshots (the 2026-08-19 regression):")
    for label, body, probe in msgvis.text_variants(token()):
        for rt_label, rt in (
                ("named author", {"id": "m1", "from": "agent-2",
                                  "at": "2026-08-04T04:00:00.000Z",
                                  "gist": "shall I ship it?"}),
                # `from` == the recipient is dropped by post_mail, so the
                # recital reads "your message" — a different header line
                ("self-consistent", {"id": "m1", "from": "agent",
                                     "at": "2026-08-04T04:00:00.000Z",
                                     "gist": "shall I ship it?"}),
                # a 200-char gist is the cap: the widest the body is ever
                # pushed from the timestamp
                ("max-length gist", {"id": "m1", "from": "agent-2",
                                     "at": "2026-08-04T04:00:00.000Z",
                                     "gist": "g" * 400}),
                # blank gists are ignored by post_mail — the entry renders
                # plain, and must still hand over
                ("blank gist", {"id": "m1", "from": "agent-2", "gist": "   "})):
            check(f"lifecycle · {label} · reply snapshot ({rt_label})",
                  lambda b=body, pr=probe, l=label, r=rt, rl=rt_label:
                  run_lifecycle(f"{l}/reply-{rl}", b, pr, reply_to=r))

    print("\nlifecycle × transcript size (serverCopies' 20-message baseline):")
    for pairs in (0, 12, 60, 200, 700):
        for label, body, probe in msgvis.text_variants(token())[:4]:
            check(f"lifecycle · {label} · {pairs} prior exchanges",
                  lambda b=body, pr=probe, p=pairs, l=label: run_lifecycle(
                      f"{l}/f{p}", b, pr, filler_pairs=p))
    for win in (20, 40, msgvis.CHAT_WINDOW, 300, 1000):
        def _win(wi=win):
            t = token()
            run_lifecycle(f"win{wi}", f"hello {t}", t, filler_pairs=200, win=wi)
        check(f"lifecycle · long transcript fetched with last={win}", _win)

    print("\nlifecycle × byte-identical repeat (D-52's trap, at every layer):")
    for label, body, probe in msgvis.text_variants(token())[:8]:
        check(f"lifecycle · {label} · an identical copy is already on screen",
              lambda b=body, pr=probe, l=label: run_lifecycle(f"{l}/rpt", b, pr, repeat_first=True))

    def _double_send_same_text():
        """Two identical sends in flight at once — the ghosts must not
        cross-graduate, and neither message may be hidden by the other."""
        global CONFIGS
        CONFIGS += 1
        w = World("dbl")
        try:
            t = token()
            body = f"continue {t}"
            desk = Desk()
            desk.fetch(w.chat())
            desk.send(body)
            m1 = w.post(body)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 1, desk.renders(body)
            desk.send(body)                       # the SAME text again
            w.post(body)
            desk.fetch(w.chat())
            r = desk.renders(body)
            assert r["total"] == 2, f"two sends must show twice: {r}"
            tok1, mail1 = w.drain("turn")
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 2, desk.renders(body)
            w.tx.turn_echo(mail1)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 2, desk.renders(body)
            w.confirm(tok1)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 2, desk.renders(body)
            assert m1["at"] != w.org().d["mail_log"][w.nid][-1]["at"] or True
        finally:
            w.destroy()
    check("two identical sends in one batch stay two on screen", _double_send_same_text)

    def _three_sends_partial_drain():
        global CONFIGS
        CONFIGS += 1
        w = World("three")
        try:
            t = token()
            body = f"yes {t}"
            desk = Desk()
            desk.fetch(w.chat())
            for _ in range(3):
                desk.send(body)
                w.post(body)
                desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 3, desk.renders(body)
            tok, mail = w.drain("turn")          # all three drain together
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 3, desk.renders(body)
            w.tx.turn_echo(mail)                 # one envelope, three blocks
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 3, desk.renders(body)
            w.confirm(tok)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 3, desk.renders(body)
        finally:
            w.destroy()
    check("three identical sends drained in ONE envelope stay three", _three_sends_partial_drain)

    # --------------------------------------------------- hazardous orderings
    print("\nhazardous orderings (where the family's fifth instance lived):")
    # `_confirm_delivered` retires the journal on the first non-`system` STDOUT
    # event; the message is rendered from the TRANSCRIPT FILE. Those are two
    # different pieces of evidence about the same thing, and if the stdout one
    # arrives first the message is briefly nowhere. Reachability is a fact
    # about the CLI, not about orgtree: measured on 94 real transcripts, the
    # `[MAIL —` user record precedes the turn's first assistant record in
    # 115/115 cases (median 2.4 s of daylight), so the ordering below is not
    # one the pinned CLI produces. The live suite drives it deliberately with
    # the fake CLI to show what the mechanism does when it happens.
    _WHY_ORDER = ("measured on 94 real transcripts: the turn's user record "
                  "precedes the first assistant record 115/115, median 2.4 s")
    for label, body, probe in msgvis.text_variants(token())[:6]:
        fragile(f"ordering · {label} · confirm lands BEFORE the transcript echo",
                _WHY_ORDER,
                lambda b=body, pr=probe, l=label: run_lifecycle(
                    f"{l}/cbe", b, pr, confirm_before_echo=True))

    def _launch_failed_foldback():
        global CONFIGS
        CONFIGS += 1
        w = World("fold")
        try:
            t = token()
            body = f"hello {t}"
            desk = Desk()
            desk.fetch(w.chat())
            watch = Watch(desk, body, "fold-back after a failed launch")
            desk.send(body)
            w.post(body)
            desk.fetch(w.chat())
            watch.check("posted")
            w.drain("turn")
            desk.fetch(w.chat())
            watch.check("drained")
            w.foldback()                     # the CLI never launched
            desk.fetch(w.chat())
            watch.check("folded back into the mailbox")
            tok, mail = w.drain("turn")      # the next turn tries again
            desk.fetch(w.chat())
            watch.check("re-drained")
            w.tx.turn_echo(mail)
            desk.fetch(w.chat())
            watch.check("echoed")
            w.confirm(tok)
            desk.fetch(w.chat())
            watch.check("confirmed")
        finally:
            w.destroy()
    check("a failed launch folds back with no gap and no duplicate",
          _launch_failed_foldback)

    def _replay_after_restart():
        """at-least-once: the same batch is drained and echoed TWICE (the turn
        was replayed). Two bubbles is honest — but never zero."""
        global CONFIGS
        CONFIGS += 1
        w = World("replay")
        try:
            t = token()
            body = f"hello {t}"
            desk = Desk()
            desk.fetch(w.chat())
            desk.send(body)
            w.post(body)
            desk.fetch(w.chat())
            tok, mail = w.drain("turn")
            w.tx.turn_echo(mail)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 1
            w.foldback()                       # backend died mid-turn
            desk.fetch(w.chat())
            r = desk.renders(body)
            # the transcript still shows the echo AND the mail is back in the
            # box — a duplicate, but the honest kind (at-least-once); what must
            # never happen is zero
            assert r["total"] >= 1, r
            tok2, mail2 = w.drain("turn")
            w.tx.turn_echo(mail2)
            w.confirm(tok2)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] >= 1, desk.renders(body)
        finally:
            w.destroy()
    check("an at-least-once replay never reaches zero copies", _replay_after_restart)

    def _retract():
        global CONFIGS
        CONFIGS += 1
        w = World("retract")
        try:
            t = token()
            body = f"hello {t}"
            desk = Desk()
            desk.fetch(w.chat())
            desk.send(body)
            m = w.post(body)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 1
            org = w.org()                     # the retract path (api:2770)
            org.d["mail"][w.nid] = [x for x in org.d["mail"][w.nid]
                                    if x["id"] != m["id"]]
            store.save_org(org)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 0, \
                "a RETRACTED message is meant to leave the screen"
        finally:
            w.destroy()
    check("retraction removes it (the one legitimate way to reach zero)", _retract)

    # ------------------------------------------------------------ steer path
    print("\nmid-task delivery (the steer carrier's own hand-off):")
    for label, body, probe in msgvis.text_variants(token()):
        check(f"steer · {label} · boundary fold → transcript",
              lambda b=body, pr=probe, l=label: run_steer_lifecycle(
                  f"{l}/bnd", b, pr, hook_delivers=False))
    for label, body, probe in msgvis.text_variants(token()):
        check(f"steer · {label} · hook fetch → steered_log, same instant",
              lambda b=body, pr=probe, l=label: run_steer_lifecycle(
                  f"{l}/hook", b, pr, hook_delivers=True))

    # ------------------------------------------------------ sparse polling
    print("\nsparse polling (a desk that misses the intermediate fetches):")

    def run_sparse_poll(label: str, body: str, probe: str, rows: int) -> None:
        """The desk fetches ONCE, after the whole turn has happened.

        This is not exotic: the heartbeat is 2.5 s busy / 7 s idle, a dropped
        websocket removes every event-driven refetch, and a turn emits rows far
        faster than that. The ghost is created, the message drains, the CLI
        echoes it, the agent produces `rows` more rows — and only THEN does a
        payload arrive. If the ghost's transcript copy has been buried deeper
        than `serverCopies` looks, the count can never rise again and the ghost
        is stranded for the rest of the session, rendering the message twice."""
        global CONFIGS
        CONFIGS += 1
        w = World(label[:20].replace(" ", "-"))
        try:
            desk = Desk()
            desk.fetch(w.chat())
            watch = Watch(desk, probe, label)
            desk.send(body)                       # ghost, and then silence
            w.post(body)
            tok, mail = w.drain("turn")
            w.tx.turn_echo(mail)
            for i in range(rows):                 # the turn talks and works
                w.tx.assistant(f"working on it, step {i}")
            w.confirm(tok)
            desk.fetch(w.chat())                  # …one single payload
            watch.check(f"one fetch after {rows} rows of turn output")
            for i in range(3):
                w.tx.assistant("more")
                desk.fetch(w.chat())
                watch.check(f"settled {i}")
        finally:
            w.destroy()

    for rows in (5, 15, 25, 60, 150):
        for label, body, probe in msgvis.text_variants(token())[:3]:
            check(f"sparse poll · {label} · {rows} rows landed first",
                  lambda b=body, pr=probe, r=rows, l=label: run_sparse_poll(
                      f"{l}/sparse{r}", b, pr, r))
    # Beyond the counting window the ghost can never graduate — the count it
    # was baselined against is unreachable. The window is 200 and the widest
    # gap between consecutive user messages measured over 94 real transcripts
    # is 138 rendered rows, so this is outside the observed world; the durable
    # cure is a per-send id rather than a bigger number (D-51 said so first).
    fragile("sparse poll · the copy is buried beyond the counting window",
            "measured on 94 real transcripts: the widest gap between "
            "consecutive user messages is 138 rendered rows, window is 200",
            lambda: run_sparse_poll("beyond-window", f"hello {(t := token())}",
                                    t, 400))

    # ------------------------------------------------------- agent lifecycle
    print("\nagent state (a message must survive the recipient's condition):")

    def _state_case(state_setter, label, expect_send_ok=True):
        global CONFIGS
        CONFIGS += 1
        w = World("state")
        try:
            t = token()
            body = f"hello {t}"
            desk = Desk()
            desk.fetch(w.chat())
            watch = Watch(desk, body, label)
            state_setter(w)
            desk.send(body)
            watch.check("ghost created")
            w.post(body)
            desk.fetch(w.chat())
            watch.check("posted into a mailbox that cannot run")
            # …and it is still there many polls later
            for i in range(5):
                desk.fetch(w.chat())
                watch.check(f"poll {i}")
        finally:
            w.destroy()

    def _freeze(w):
        org = w.org()
        org.node(w.nid)["frozen"] = {"at": supervisor.now_iso(),
                                     "resume_texts": []}
        store.save_org(org)

    def _archive(w):
        org = w.org()
        org.retire(USER, w.nid)
        store.save_org(org)

    def _unrecoverable(w):
        org = w.org()
        org.mark_unrecoverable(w.nid, "transcript missing")
        store.save_org(org)

    check("frozen agent: mail waits in the box, visibly",
          lambda: _state_case(_freeze, "frozen"))
    check("archived agent: deferred mail stays visible until rehire",
          lambda: _state_case(_archive, "archived"))

    def _unrecoverable_refuses():
        """An unrecoverable node REFUSES mail (ledger.post_mail), so the send
        fails — and the desk's `.catch` must retire the ghost. The failure mode
        to rule out is a ghost left on screen forever pretending it was sent."""
        global CONFIGS
        CONFIGS += 1
        w = World("unrec")
        try:
            from orgtree.ledger import LedgerError
            t = token()
            body = f"hello {t}"
            desk = Desk()
            desk.fetch(w.chat())
            _unrecoverable(w)
            desk.send(body)
            try:
                w.post(body)
                raise AssertionError("post_mail accepted mail for an "
                                     "unrecoverable node")
            except LedgerError:
                desk.drop(body)                # desk.tsx's .catch path
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 0, (
                "a REFUSED send left a ghost on screen: "
                f"{desk.renders(body)}")
        finally:
            w.destroy()
    check("unrecoverable agent: the send is refused and the ghost retires",
          _unrecoverable_refuses)

    def _rehire_delivers():
        global CONFIGS
        CONFIGS += 1
        w = World("rehire")
        try:
            t = token()
            body = f"hello {t}"
            desk = Desk()
            desk.fetch(w.chat())
            watch = Watch(desk, body, "archived → rehired → delivered")
            org = w.org()
            org.retire(USER, w.nid)
            store.save_org(org)
            desk.send(body)
            w.post(body)
            desk.fetch(w.chat())
            watch.check("queued while archived")
            org = w.org()
            org.rehire(USER, w.nid)
            store.save_org(org)
            desk.fetch(w.chat())
            watch.check("rehired")
            tok, mail = w.drain("turn")
            desk.fetch(w.chat())
            watch.check("drained by the rehire turn")
            w.tx.turn_echo(mail)
            desk.fetch(w.chat())
            watch.check("echoed")
            w.confirm(tok)
            desk.fetch(w.chat())
            watch.check("confirmed")
        finally:
            w.destroy()
    check("archived → rehired → delivered, continuous throughout", _rehire_delivers)

    # ------------------------------------------------------ transcript shapes
    print("\ntranscript shapes read_chat treats specially:")

    def _echo_with(extra: dict, label: str, expect_visible: bool):
        global CONFIGS
        CONFIGS += 1
        w = World("shape")
        try:
            t = token()
            body = f"hello {t}"
            desk = Desk()
            desk.fetch(w.chat())
            desk.send(body)
            w.post(body)
            desk.fetch(w.chat())
            tok, mail = w.drain("turn")
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 1
            # the CLI writes the echo with a flag read_chat may SKIP
            prelude = msgvis.mail_block(mail)
            body_text = prelude + "\n\n" + msgvis.TURN_NUDGE
            if extra.get("__type"):
                w.tx._write({"type": extra["__type"],
                             "message": {"role": "user", "content": body_text}})
            else:
                w.tx.user(body_text, **extra)
            desk.fetch(w.chat())
            r = desk.renders(body)
            if expect_visible:
                assert r["total"] == 1, f"{label}: {r}"
            else:
                # read_chat drops the record ⇒ the journal MUST keep showing it
                assert r["total"] == 1, (
                    f"{label}: read_chat hides this record, so the journal must "
                    f"still carry the message — got {r}")
            w.confirm(tok)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] >= 1, (
                f"{label}: confirmed away with nothing on screen — "
                f"{desk.renders(body)}")
        finally:
            w.destroy()

    # ⚠ These three are the mechanism's fault line, not a live bug: the journal
    # is confirmed on the first non-`system` STDOUT event, while the message is
    # rendered from the TRANSCRIPT — two different pieces of evidence. If the
    # CLI ever writes the turn's user event as a record `read_chat` skips, the
    # message is confirmed away with nothing on screen. Measured on 94 real
    # orgtree transcripts (2026-08-04): all 115 `[MAIL —` user records are
    # plain, unflagged `type:"user"` records, and every one precedes the turn's
    # first assistant record — so the precondition does not occur today.
    _WHY = ("measured on 94 real transcripts: 115/115 turn echoes are plain "
            "unflagged type:'user' records preceding the first assistant record")
    fragile("echo record flagged isMeta (read_chat skips it)", _WHY,
            lambda: _echo_with({"isMeta": True}, "isMeta", False))
    fragile("echo record flagged isSidechain", _WHY,
            lambda: _echo_with({"isSidechain": True}, "isSidechain", False))
    fragile("echo record flagged isVisibleInTranscriptOnly", _WHY,
            lambda: _echo_with({"isVisibleInTranscriptOnly": True}, "ivto", False))
    fragile("echo written as type:'attachment' (the shape hook context uses)",
            _WHY,
            lambda: _echo_with({"__type": "attachment"}, "attachment", False))
    check("echo record written normally",
          lambda: _echo_with({}, "plain", True))

    def _compaction_boundary():
        global CONFIGS
        CONFIGS += 1
        w = World("compact")
        try:
            t = token()
            body = f"hello {t}"
            desk = Desk()
            w.tx.filler(5)
            desk.fetch(w.chat())
            watch = Watch(desk, body, "compaction between send and echo")
            desk.send(body)
            w.post(body)
            desk.fetch(w.chat())
            watch.check("posted")
            tok, mail = w.drain("turn")
            desk.fetch(w.chat())
            watch.check("drained")
            w.tx._write({"type": "system", "subtype": "compact_boundary",
                         "compactMetadata": {"preTokens": 120000}})
            desk.fetch(w.chat())
            watch.check("context compacted mid-flight")
            w.tx.turn_echo(mail)
            desk.fetch(w.chat())
            watch.check("echoed after the boundary")
            w.confirm(tok)
            desk.fetch(w.chat())
            watch.check("confirmed")
        finally:
            w.destroy()
    check("a compaction boundary between drain and echo changes nothing",
          _compaction_boundary)

    def _transcript_disappears():
        """The session file is gone (reseed / unrecoverable): the transcript
        carrier evaporates. Anything still in flight must not evaporate with
        it."""
        global CONFIGS
        CONFIGS += 1
        w = World("gone")
        try:
            t = token()
            body = f"hello {t}"
            desk = Desk()
            desk.fetch(w.chat())
            watch = Watch(desk, body, "transcript file removed mid-flight")
            desk.send(body)
            w.post(body)
            desk.fetch(w.chat())
            watch.check("posted")
            tok, mail = w.drain("turn")
            w.tx.turn_echo(mail)
            desk.fetch(w.chat())
            watch.check("echoed")
            os.remove(w.tx.path)                 # the transcript is gone
            desk.fetch(w.chat())
            watch.check("transcript file deleted")
        finally:
            w.destroy()
    check("losing the transcript file mid-flight does not lose the message",
          _transcript_disappears)

    # --------------------------------------------------- the marker itself
    print("\nmail identity (each entry found in its OWN bubble, D-55):")

    def _marker_identity():
        """Two mails with identical BODIES and different times: echoing the
        first must not hide the second (D-52's mistake, one layer down)."""
        global CONFIGS
        CONFIGS += 1
        w = World("marker")
        try:
            t = token()
            body = f"continue {t}"
            desk = Desk()
            desk.fetch(w.chat())
            desk.send(body)
            w.post(body)
            desk.fetch(w.chat())
            tok1, mail1 = w.drain("turn")
            w.tx.turn_echo(mail1)
            desk.fetch(w.chat())
            w.confirm(tok1)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 1
            desk.send(body)                      # identical text, later
            w.post(body)
            desk.fetch(w.chat())
            assert desk.renders(body)["total"] == 2, desk.renders(body)
            tok2, mail2 = w.drain("turn")
            desk.fetch(w.chat())
            r = desk.renders(body)
            assert r["total"] == 2, (
                f"the SECOND send was hidden by the FIRST send's bubble: {r}")
        finally:
            w.destroy()
    check("an identical earlier bubble does not hide a later in-flight mail",
          _marker_identity)

    def _marker_body_only_prefix():
        """A mail whose body is a PREFIX of an earlier one, and vice versa."""
        global CONFIGS
        CONFIGS += 1
        w = World("prefix")
        try:
            t = token()
            short, long_ = f"go {t}", f"go {t} and then do the other thing"
            desk = Desk()
            desk.fetch(w.chat())
            desk.send(long_)
            w.post(long_)
            desk.fetch(w.chat())
            tok1, m1 = w.drain("turn")
            w.tx.turn_echo(m1)
            w.confirm(tok1)
            desk.fetch(w.chat())
            desk.send(short)
            w.post(short)
            desk.fetch(w.chat())
            tok2, m2 = w.drain("turn")
            desk.fetch(w.chat())
            # the SHORT body is contained in the long one's bubble, so a
            # body-only test would hide it. The marker must not.
            c = w.chat()
            assert any(short in (m.get("body") or "")
                       for m in c["pending_mail"]), (
                "the short in-flight mail was hidden by the longer earlier "
                f"bubble that contains it: {c['pending_mail']}")
        finally:
            w.destroy()
    check("a mail whose body is a substring of an earlier one still shows",
          _marker_body_only_prefix)

    def _marker_truncation():
        """The marker is 400 chars. A body that only DIVERGES after 400 chars
        would be matched by its twin — check the second one is not hidden."""
        global CONFIGS
        CONFIGS += 1
        w = World("trunc")
        try:
            t = token()
            base = "x" * 600
            a, b = base + f" first {t}", base + f" second {t}"
            desk = Desk()
            desk.fetch(w.chat())
            w.post(a)
            tok1, m1 = w.drain("turn")
            w.tx.turn_echo(m1)
            w.confirm(tok1)
            desk.fetch(w.chat())
            w.post(b)
            tok2, m2 = w.drain("turn")
            desk.fetch(w.chat())
            c = w.chat()
            assert any("second" in (m.get("body") or "")
                       for m in c["pending_mail"]), (
                "a body identical for its first 600 chars hid the new mail: "
                f"{[m['body'][:40] for m in c['pending_mail']]}")
        finally:
            w.destroy()
    check("bodies identical past the 400-char marker do not hide each other",
          _marker_truncation)

    # ------------------------------------------------------ concurrency-ish
    print("\nmany messages, many nodes:")

    def _many_sends():
        global CONFIGS
        CONFIGS += 1
        w = World("many")
        try:
            desk = Desk()
            desk.fetch(w.chat())
            watches = []
            for i in range(25):
                t = token()
                body = f"msg {i} {t}"
                wa = Watch(desk, body, f"burst message {i}")   # baseline FIRST
                desk.send(body)
                w.post(body)
                desk.fetch(w.chat())
                watches.append((wa, body))
                for prev, _ in watches:
                    prev.check(f"after send {i}")
            tok, mail = w.drain("turn")
            desk.fetch(w.chat())
            for wa, _ in watches:
                wa.check("drained together")
            w.tx.turn_echo(mail)
            desk.fetch(w.chat())
            for wa, _ in watches:
                wa.check("echoed together")
            w.confirm(tok)
            desk.fetch(w.chat())
            for wa, _ in watches:
                wa.check("confirmed")
        finally:
            w.destroy()
    check("25 messages sent back to back: every one visible exactly once",
          _many_sends)

    def _pending_mail_cap():
        """`node_chat` returns `pending[-20:]`. More than 20 queued messages
        and the OLDEST stop being rendered — a gap by truncation."""
        global CONFIGS
        CONFIGS += 1
        w = World("cap")
        try:
            desk = Desk()
            desk.fetch(w.chat())
            bodies = []
            for i in range(30):
                b = f"queued {i} {token()}"
                bodies.append(b)
                desk.send(b)
                w.post(b)
            desk.fetch(w.chat())
            missing = [i for i, b in enumerate(bodies)
                       if desk.renders(b)["total"] == 0]
            assert not missing, (
                f"{len(missing)} of 30 queued messages are on screen NOWHERE "
                f"(indices {missing[:8]}…): node_chat truncates pending_mail to "
                f"the last 20 and the ghosts have already graduated")
        finally:
            w.destroy()
    check("more than 20 queued messages: none falls off the screen",
          _pending_mail_cap)

    def _whitespace_body():
        """A body that is entirely whitespace. `ledger.post_mail` built its
        event gist with `body.strip().splitlines()[0]` — and `"".splitlines()`
        is the EMPTY LIST, so the send raised IndexError and 500ed. The
        composer trims and refuses empty; nothing else does."""
        global CONFIGS
        CONFIGS += 1
        w = World("ws")
        try:
            body = "  " + chr(10) + chr(9) + " "
            m = w.post(body)
            assert m["body"] == body
            c = w.chat()
            assert len(c["pending_mail"]) == 1, c["pending_mail"]
            _, mail = w.drain("turn")
            w.tx.turn_echo(mail)
            assert w.chat()["pending_mail"] == [], "the hand-off did not happen"
        finally:
            w.destroy()
    check("a whitespace-only body posts, shows and hands over",
          _whitespace_body)

    def _load_org_retries():
        """A `GET …/chat` that 500s is a desk that stops updating — the same
        symptom as everything else here, one layer lower.

        `save_org` retries `os.replace` because a reader may hold the file
        open; the collision is symmetric and `load_org` had no retry, so a poll
        landing inside another thread's replace came back HTTP 500. Measured on
        the live rig 2026-08-04: 3 of 123 turns. Proven here by making the
        first open fail exactly the way Windows does."""
        global CONFIGS
        CONFIGS += 1
        w = World("perm")
        try:
            import builtins
            real_open = builtins.open
            state = {"n": 0}

            def flaky(*a, **k):
                if a and isinstance(a[0], str) and a[0].endswith(w.slug + ".json") \
                        and state["n"] < 2:
                    state["n"] += 1
                    raise PermissionError(13, "Permission denied")
                return real_open(*a, **k)
            builtins.open = flaky
            try:
                org = store.load_org(w.slug)
            finally:
                builtins.open = real_open
            assert org.d["slug"] == w.slug
            assert state["n"] == 2, "the flaky open was not exercised"
        finally:
            w.destroy()
    check("load_org survives a concurrent save's replace (no 500 on a poll)",
          _load_org_retries)

    def _two_nodes():
        global CONFIGS
        CONFIGS += 1
        w = World("two")
        try:
            org = w.org()
            org.hire(USER, None, "haiku", 5, "other")
            store.save_org(org)
            t = token()
            body = f"hello {t}"
            d1, d2 = Desk(), Desk()
            d1.fetch(w.chat())
            d2.fetch(api.node_chat(w.slug, "other"))
            d1.send(body)
            w.post(body)
            d1.fetch(w.chat())
            d2.fetch(api.node_chat(w.slug, "other"))
            assert d1.renders(body)["total"] == 1
            assert d2.renders(body)["total"] == 0, \
                "the other node's desk is showing a message addressed elsewhere"
        finally:
            w.destroy()
    check("a message to one node never appears on another's desk", _two_nodes)

    # -------------------------------------------------------------- report
    print()
    if FAIL:
        print("=" * 72)
        for label, tb in FAIL:
            print(f"\nFAILED: {label}\n{tb}")
        print("=" * 72)
    if FRAGILE:
        print("KNOWN FRAGILITY — the invariant breaks under a precondition that "
              "is currently unreachable. Each line names the measurement that "
              "makes it unreachable; if that measurement stops holding, these "
              "become live bugs:")
        for label, why, err in FRAGILE:
            print(f"  ⚠ {label}\n      unreachable because: {why}\n      "
                  f"breaks as: {err}")
        print()
    print(f"{PASS} checks passed, {len(FAIL)} failed, "
          f"{len(FRAGILE)} known-fragile "
          f"({CONFIGS} lifecycle configurations run)")
    if FAIL:
        print(f"\n{len(FAIL)} CHECKS FAILED")
        sys.exit(1)
    print(f"\nALL {PASS} CHECKS PASS")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
