"""Shared model for the message-visibility suite (the D-34/43/50/51/52/55 bug
family): *something was retired before its replacement existed*.

THE INVARIANT UNDER TEST
------------------------
A message the user sent is on screen CONTINUOUSLY from the moment it is sent
until the conversation ends, and NEVER appears twice.

"On screen" is the union of the three carriers an agent desk renders a user
message from, and the whole bug family lives in the hand-offs between them:

  ① transcript bubble   `chat.messages[] where role == 'user'`   (desk.tsx)
  ② pending row         `chat.pending_mail[] where from == '@user'`  (pendrow)
  ③ optimistic ghost    `convo.pending[]`                        (convo.ts)

② is itself two server-side sources fused in `api.node_chat`: the node's
mailbox (`org.d["mail"]`) and the delivery journal (`org.d["delivering"]`,
projected by `supervisor.delivering_mail`).

So the render count for one message at any instant is
    renders = ①hits + ②hits + ③hits
and the invariant is `renders == 1` at EVERY observation:
    renders == 0  →  a GAP       (the user's message vanished)
    renders >= 2  →  a DUPLICATE (D-55 found a 1.95-2.35 s one nobody reported)

WHY THE CLIENT RULES ARE PORTED HERE
------------------------------------
The graduation rule that retires ③ lives in the browser (`convo.ts`), and the
evidence it retires against is served by Python. Neither half can be judged
alone — D-51/52/55 were all failures of the *seam*. `Desk` below is a faithful
port of `convo.ts`'s `serverCopies` / `addPending` / `refreshConvo` graduation
and `desk.tsx`'s render union, so a Python test can drive real server code and
score it exactly as the browser would.

⚠ A port is a copy, and copies rot. `assert_client_model_matches_source()`
greps `convo.ts` and `desk.tsx` for the expressions this file mirrors and fails
loudly if they change — a silent drift here would turn the whole suite into a
test of a fiction.
"""

from __future__ import annotations

import dis
import inspect
import json
import os
import re
import sys
import uuid
from typing import Any

USER = "@user"

# ---------------------------------------------------------------- client port

#: convo.ts — `COPIES_WINDOW`, the newest-n transcript rows counted within
SERVER_COPIES_WINDOW = 200
#: convo.ts — `COPIES_NEEDLE`, how much of a ghost's text must be found
SERVER_COPIES_NEEDLE = 200
#: convo.ts:29 — the window the desk actually fetches
CHAT_WINDOW = 120


def server_copies(chat: dict[str, Any] | None, text: str) -> int:
    """Port of `convo.ts serverCopies()` — how many copies of `text` the server
    is currently showing. Both places count, because a message passes through
    them in order: the mailbox first, the transcript second."""
    if not chat:
        return 0
    needle = text[:SERVER_COPIES_NEEDLE]
    msgs = (chat.get("messages") or [])[-SERVER_COPIES_WINDOW:]
    return (sum(1 for m in msgs
                if m.get("role") == "user" and needle in (m.get("text") or ""))
            + sum(1 for m in (chat.get("pending_mail") or [])
                  if needle in (m.get("body") or "")))


class Desk:
    """The client, modelled: one node's desk fed a sequence of `/chat` payloads.

    `send()` mirrors desk.tsx's `send()` → `addPending` (the ghost is created
    BEFORE the POST, with the baseline taken from the payload in hand), and
    `fetch()` mirrors `refreshConvo`'s graduation, which is the ONLY thing that
    retires a ghost on the correspondence path."""

    def __init__(self) -> None:
        self.chat: dict[str, Any] | None = None
        self.pending: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []

    # --- convo.ts addPending
    def send(self, text: str) -> None:
        # + the ghosts already standing in for this same text: two sends of
        # "continue" before a refresh both used to carry seen:0, so ONE server
        # copy retired BOTH and the second went off screen (frontend suite
        # §3.2 — D-52's same-instant twin).
        self.pending.append({
            "text": text,
            "seen": server_copies(self.chat, text)
                    + sum(1 for g in self.pending if g["text"] == text)})

    # --- convo.ts dropPending (commands + failed sends only)
    def drop(self, text: str) -> None:
        # ONE call retires ONE ghost. Filtering every match meant a failed
        # second "yes" took the first send's preview down with it (§3.3).
        for i, g in enumerate(self.pending):
            if g["text"] == text:
                del self.pending[i]
                break

    # --- convo.ts ingestStream, kind === 'steered'
    def steered_event(self, text: str) -> None:
        for i, g in enumerate(self.pending):        # first match only (§3.4)
            if g["text"] in text:
                del self.pending[i]
                break

    # --- convo.ts refreshConvo
    def fetch(self, payload: dict[str, Any]) -> None:
        self.pending = [g for g in self.pending
                        if server_copies(payload, g["text"]) <= g["seen"]]
        self.chat = payload

    # --- desk.tsx render union
    def renders(self, probe: str) -> dict[str, int]:
        """How many times `probe` is ON SCREEN, counted in OCCURRENCES rather
        than rows — two identical messages drained into one envelope become one
        transcript bubble carrying both bodies, and both of them are genuinely
        visible in it. (`serverCopies` above deliberately counts rows instead:
        that is what convo.ts does, and a faithful port must not improve on it.)
        `probe` must occur exactly once per copy of the message."""
        c = self.chat or {}
        transcript = sum((m.get("text") or "").count(probe)
                         for m in (c.get("messages") or [])
                         if m.get("role") == "user")
        pendrow = sum((m.get("body") or "").count(probe)
                      for m in (c.get("pending_mail") or [])
                      if m.get("from") == USER)
        ghost = sum(g["text"].count(probe) for g in self.pending)
        return {"transcript": transcript, "pendrow": pendrow, "ghost": ghost,
                "total": transcript + pendrow + ghost}


class Watch:
    """Scores ONE message across a scenario and remembers every observation, so
    a failure can say WHEN it broke and what the carriers were doing.

    The baseline is taken at construction: whatever is already on screen is not
    this send, so the invariant is `renders == baseline + 1` — which is how a
    byte-identical repeat is tested without the earlier copy masking the new
    one (D-52's mistake, and the one the probe itself must not repeat)."""

    def __init__(self, desk: Desk, probe: str, label: str) -> None:
        self.desk, self.probe, self.label = desk, probe, label
        self.base = desk.renders(probe)["total"]
        self.obs: list[tuple[str, dict[str, int]]] = []

    def look(self, at: str) -> dict[str, int]:
        r = self.desk.renders(self.probe)
        self.obs.append((at, r))
        return r

    def check(self, at: str, expect: int | None = None) -> None:
        want = self.base + 1 if expect is None else expect
        r = self.look(at)
        if r["total"] != want:
            kind = ("GAP (the message is on screen NOWHERE)" if r["total"] < want
                    else f"DUPLICATE (on screen {r['total']}x, expected {want})")
            raise AssertionError(
                f"{self.label}: {kind} after step {at!r}\n"
                f"  carriers: transcript={r['transcript']} pendrow={r['pendrow']} "
                f"ghost={r['ghost']} (baseline {self.base})\n"
                f"  timeline: " + " | ".join(
                    f"{s}={o['transcript']}/{o['pendrow']}/{o['ghost']}"
                    for s, o in self.obs))


# ------------------------------------------------------- transcript synthesis

def mail_block(mail: list[dict[str, Any]]) -> str:
    """Independent re-implementation of `supervisor._mail_block` — deliberately
    NOT imported. This is what the CLI echoes into its transcript, so the suite
    must be able to produce it from the outside; importing the private helper
    would make a formatter change invisible to the very test that exists to
    catch a formatter/marker mismatch.

    ⚠ AN INDEPENDENT COPY IS ONLY SOUND WHILE SOMETHING PINS IT. This one went
    unpinned and the suite went blind exactly as predicted, just from the other
    side: FR-05 (`reply_to`) and D-137 (`kind == "notice"`) changed the REAL
    formatter, this copy kept writing the old envelope, and the marker mismatch
    they created — a pending row that never handed over to its own transcript
    bubble — was reproduced by nothing. The user found it instead (2026-08-19,
    "messages briefly duplicated"). `assert_mail_block_matches_source()` now
    compares the two functions over every shape the real one can produce, so
    this file can be an outside copy AND be provably the same copy."""
    blocks = []
    for m in mail:
        tag = " ⚠ THE USER — user instructions outrank your chain" \
            if m["from"] == USER else ""
        if m.get("kind") == "notice":
            b = (f"NOTICE FROM {m['from']} ({m.get('relationship', 'agent')}"
                 f"{tag}) · {m['at']} — informational, delivered passively; "
                 f"no reply is expected")
        else:
            b = (f"FROM {m['from']} ({m.get('relationship', 'agent')}{tag}) · "
                 f"{m.get('kind', 'message')} · {m['at']}")
        rt = m.get("reply_to")
        if rt and str(rt.get("gist") or "").strip():
            who = str(rt.get("from") or "").strip()
            owner = f"{who}'s message" if who else "your message"
            at = str(rt.get("at") or "").strip()
            b += (f"\n↩ IN REPLY TO {owner}"
                  f"{f' of {at}' if at else ''}: “{rt.get('gist')}”")
        b += f"\n{m['body']}"
        for a in m.get("attachments") or []:
            nb = int(a.get("bytes") or 0)
            size = f"{nb} B" if nb < 1024 else f"{nb / 1024:.0f} KB"
            b += (f"\n[ATTACHED FILE: {a.get('path')} ({size}) — in your "
                  f"working folder]")
        blocks.append(b)
    return (f"[MAIL — {len(mail)} message(s)]\n" + "\n---\n".join(blocks)
            + "\n[END MAIL]")


def mail_shapes() -> list[tuple[str, dict[str, Any]]]:
    """Every structurally distinct mail entry `_mail_block` can be handed —
    one per branch it takes, crossed with the fields that move the body.

    This is the spanning set the two guards below run over. It is the answer
    to "how would we have caught it": the defect was a formatter branch nobody
    had ever rendered in a test, so the set of branches has to be enumerated
    somewhere rather than sampled by whichever scenario happened to exist."""
    at = "2026-08-04T05:00:00.000Z"
    rt_at = "2026-08-04T04:00:00.000Z"
    base = {"from": USER, "to": "agent", "at": at, "kind": "message",
            "body": "the body"}
    att = [{"name": "a.txt", "path": "uploads/a.txt", "bytes": 12}]
    return [
        ("plain user", dict(base)),
        ("plain agent", {**base, "from": "agent-2"}),
        ("relationship", {**base, "from": "agent-2", "relationship": "superior"}),
        # FR-05: the shape that broke the marker — the reply snapshot pushes
        # the body one line further from the timestamp
        ("reply, named author",
         {**base, "reply_to": {"id": "m1", "from": "agent-2", "at": rt_at,
                               "gist": "shall I ship it?"}}),
        ("reply, self-consistent (no from)",
         {**base, "reply_to": {"id": "m1", "at": rt_at, "gist": "ship it?"}}),
        ("reply, no timestamp",
         {**base, "reply_to": {"id": "m1", "from": "agent-2", "gist": "ship?"}}),
        # a blank gist is dropped by the formatter — the entry renders plain
        ("reply, blank gist",
         {**base, "reply_to": {"id": "m1", "from": "agent-2", "gist": "   "}}),
        # D-137: the other shape that broke it — the header itself continues
        # past the timestamp
        ("notice", {**base, "from": "agent-2", "kind": "notice"}),
        ("notice from the user", {**base, "kind": "notice"}),
        ("notice + reply",
         {**base, "from": "agent-2", "kind": "notice",
          "reply_to": {"id": "m1", "from": "agent-3", "at": rt_at, "gist": "?"}}),
        ("one attachment", {**base, "attachments": att}),
        ("two attachments", {**base, "attachments": att * 2}),
        # the size branch has two halves and every other shape only ever
        # exercised the bytes-side one (redteam 2026-08-19)
        ("attachment ≥ 1 KB",
         {**base, "attachments": [{"name": "big.bin", "path": "uploads/big.bin",
                                   "bytes": 4096}]}),
        ("reply + attachment",
         {**base, "attachments": att,
          "reply_to": {"id": "m1", "from": "agent-2", "at": rt_at, "gist": "g"}}),
        # ledger/mcptool allow question|request|decision|status besides
        # message; api.py:node_ask posts kind="status". They take the same
        # header branch today — the point is that the list says so out loud
        ("kind=status", {**base, "from": "agent-2", "kind": "status"}),
        ("leading newline body", {**base, "body": "\nthe body"}),
        ("whitespace-only body", {**base, "body": "   \n  "}),
        ("empty body", {**base, "body": ""}),
        ("body longer than the marker", {**base, "body": "x" * 5000}),
        ("body that looks like an envelope",
         {**base, "body": "FROM @user (agent) · message · 2026-01-01T00:00:00Z"}),
        ("unicode body", {**base, "body": "日本語 ✨ Ωπ ⚠ · — “x”"}),
    ]


#: A body at least this long forces `mail_in_transcript` down its TRUNCATING
#: branch, where the needle stops being the whole block and becomes a prefix
#: of it. Kept in step with `supervisor.MAIL_MARK_CHARS` by
#: `assert_mail_shapes_span`'s caller, which passes the real value in.
PAST_NEEDLE_BUDGET = 400


def mail_shapes_crossed(mark_chars: int = PAST_NEEDLE_BUDGET
                        ) -> list[tuple[str, dict[str, Any]]]:
    """`mail_shapes()`, crossed with the axis the guards actually turn on:
    whether the body is longer than the needle budget.

    ⚠ Below the budget the needle IS the whole block, so any change to what
    the formatter writes AFTER the body is invisible — the rendering and the
    probe are byte-identical. Above it the needle is a prefix and the layout
    starts to matter. The suite crossed long bodies with the plain, attachment
    and reply branches and never with the D-137 notice branch, so a plausible
    future tweak (one more line under a notice's body) broke the invariant for
    every notice over 400 characters while all 268 checks stayed green
    (redteam, 2026-08-19). Crossing the whole list closes that as a class
    rather than adding one more hand-picked shape.

    ⚠ Each shape also gets its OWN timestamp. They used to share one, and
    because the header carries only from/relationship/kind/at, several shapes
    rendered identical blocks — so 6 of 21 batched assertions were satisfied
    by a SIBLING entry and could not fail whatever the formatter did. Same
    vacuity, one layer along."""
    out: list[tuple[str, dict[str, Any]]] = []
    for i, (label, m) in enumerate(mail_shapes()):
        stamped = {**m, "at": f"2026-08-04T05:00:{i % 60:02d}.{i:03d}Z"}
        out.append((label, stamped))
        body = str(stamped.get("body") or "")
        out.append((f"{label} · body past the needle budget",
                    {**stamped, "body": body + "z" * (mark_chars + 100),
                     "at": f"2026-08-04T05:01:{i % 60:02d}.{i:03d}Z"}))
    return out


TURN_NUDGE = ("(orgtree) The mail above includes a message from the user, "
              "addressed to you — act on it now.")


class Transcript:
    """A CLI transcript file, written record by record — the real thing
    `read_chat` parses (`~/.claude/projects/<proj>/<session>.jsonl`)."""

    def __init__(self, home: str, session_id: str, project: str = "orgtree-suite"):
        self.dir = os.path.join(home, ".claude", "projects", project)
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, session_id + ".jsonl")
        self.n = 0

    def _write(self, rec: dict[str, Any]) -> None:
        self.n += 1
        rec.setdefault("timestamp", f"2026-08-04T05:00:{self.n % 60:02d}.{self.n:03d}Z")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def user(self, text: str, **extra: Any) -> None:
        self._write({"type": "user", "message": {"role": "user", "content": text},
                     **extra})

    def assistant(self, text: str, **extra: Any) -> None:
        self._write({"type": "assistant",
                     "message": {"role": "assistant", "model": "claude-x",
                                 "content": [{"type": "text", "text": text}],
                                 "usage": {"input_tokens": 100}}, **extra})

    def turn_echo(self, mail: list[dict[str, Any]], nudge: str = TURN_NUDGE,
                  notices: list[dict[str, Any]] | None = None) -> None:
        """What the CLI writes when orgtree feeds it a turn: the enveloped user
        event, exactly as `_run_turn` composes it."""
        prelude = []
        if notices:
            lines = "\n".join(f"- {p['at']}: {p['text']}" for p in notices)
            prelude.append(f"[ORG NOTICES — {len(notices)} change(s) since your "
                           f"last turn]\n{lines}\n[END NOTICES]")
        if mail:
            prelude.append(mail_block(mail))
        self.user(("\n\n".join(prelude) + "\n\n" + nudge) if prelude else nudge)

    def filler(self, pairs: int) -> None:
        """Prior conversation, to push things out of windows."""
        for i in range(pairs):
            self.user(f"earlier user turn {i} " + "x" * 40)
            self.assistant(f"earlier reply {i} " + "y" * 60)


# --------------------------------------------------------------- text corpus

def token() -> str:
    """A UNIQUE probe token per use. ⚠ A repeated token is indistinguishable
    from the thing being measured — it has already made a working fix look
    broken once (D-51 method note)."""
    return "zzprobe" + uuid.uuid4().hex[:12]


def text_variants(tok: str) -> list[tuple[str, str, str]]:
    """(label, body, probe) — the text axis.

    `probe` must occur EXACTLY ONCE per copy of the body, since the render
    score counts its occurrences. Enforced by `check_corpus()` — a probe that
    appeared twice would score one message as a duplicate of itself."""
    zw = "a" + chr(0x200B) + "b" + chr(0x200C) + "c" + chr(0xFEFF) + "d"
    comb = "e" + chr(0x301) + "e" + chr(0x301) + " a" + chr(0x301) + chr(0x302)
    v: list[tuple[str, str, str]] = [
        ("plain", f"hello {tok}", tok),
        ("short-repeatable", f"continue {tok}", tok),
        ("very-long", f"{tok} " + ("lorem ipsum dolor sit amet " * 400), tok),
        ("just-over-400", f"{tok} " + "a" * 500, tok),
        ("unicode", f"{tok} \u65e5\u672c\u8a9e \u2728 \u03a9\u2248\u00e7\u221a \u0644\u063a\u0629", tok),
        ("combining", f"{tok} {comb}", tok),
        ("zero-width", f"{tok} {zw}", tok),
        ("newlines", f"{tok}\n\nsecond paragraph\n\nthird", tok),
        ("crlf", f"{tok}\r\nwindows line\r\nendings", tok),
        ("leading-space", f"   {tok} leading whitespace", tok),
        ("trailing-space", f"{tok} trailing whitespace   ", tok),
        ("leading-newline", f"\n{tok} leading newline", tok),
        ("tabs", f"\t{tok}\tcolumns\there", tok),
        ("markdown", f"# {tok}\n\n- a\n- b\n\n```py\nprint('x')\n```", tok),
        ("html-ish", f'{tok} <script>alert(1)</script> <b>x</b> & "quotes"', tok),
        ("mail-marker-lookalike",
         f"[MAIL \u2014 1 message(s)]\nFROM @user \u00b7 {tok}\n[END MAIL]", tok),
        ("separator-lookalike",
         f"{tok}\n---\nFROM @user (self) \u00b7 message \u00b7 2026-01-01T00:00:00Z",
         tok),
        ("attach-only-default", "(file attached)", "(file attached)"),
        ("single-token", tok, tok),
        ("json-ish", json.dumps({"tok": tok, "nested": {"a": [1, 2, 3]}}), tok),
    ]
    check_corpus(v)
    return v


def check_corpus(v: list[tuple[str, str, str]]) -> None:
    """A probe that occurred twice in its own body would score one message as a
    duplicate of itself — the suite must not be able to lie in that direction."""
    for label, body, probe in v:
        n = body.count(probe)
        if n != 1:
            raise AssertionError(
                f"corpus entry {label!r}: the probe occurs {n}x in the body — "
                f"the render score counts occurrences, so it must be exactly 1")
    check_corpus(v)
    return v


def check_corpus(v: list[tuple[str, str, str]]) -> None:
    for label, body, probe in v:
        n = body.count(probe)
        if n != 1:
            raise AssertionError(
                f"corpus entry {label!r}: the probe occurs {n}x in the body — "
                f"the render score counts occurrences, so it must be exactly 1")


# ------------------------------------------------------------- drift guard

_SOURCE_CONTRACTS = [
    # (file, regex, why this test would become a fiction if it changed)
    ("frontend/src/convo.ts", r"const COPIES_WINDOW = 200",
     "serverCopies' newest-n window is ported as SERVER_COPIES_WINDOW"),
    ("frontend/src/convo.ts", r"const COPIES_NEEDLE = 200",
     "the bounded needle is ported as SERVER_COPIES_NEEDLE"),
    ("frontend/src/convo.ts", r"c\.messages\.slice\(-COPIES_WINDOW\)",
     "serverCopies counts within the newest-n window"),
    ("frontend/src/convo.ts", r"serverCopies\(c, g\.text\) <= g\.seen",
     "the ghost graduation rule is ported in Desk.fetch"),
    ("backend/orgtree/api.py",
     r"body_cap = 2000 if n_pending <= 20 else 800 if n_pending <= 100 else 250",
     "the pending-body cap must stay above the client's needle"),
    ("backend/orgtree/api.py", r"for m in pending\]",
     "pending_mail must not be row-capped — a queued message may not fall off"),
    ("frontend/src/convo.ts",
     r"serverCopies\(e\.s\.chat, text\)\s*\+\s*e\.s\.pending\.filter",
     "the ghost baseline is server copies PLUS standing ghosts of the same "
     "text; ported in Desk.send"),
    ("frontend/src/canvas/desk.tsx", r"chat\?\.pending_mail \?\? \[\]\)\.filter\(\(m\) => m\.from === USER\)",
     "the pendrow render filter is ported in Desk.renders"),
    ("frontend/src/canvas/desk.tsx", r"addPending\(slug, node\.id, t\)",
     "the composer creates a ghost before the POST"),
    # ⚠ This pair used to be one grep for api.py's hand-rebuilt
    # `f"· {at}\n{body}"[:400]` marker. That guard was structural and it held
    # perfectly while the defect walked straight past it: the marker never
    # changed — the FORMATTER did. A one-sided grep on a two-sided contract
    # proves only that one side stands still. The rule now lives beside the
    # formatter and is checked by RUNNING both (assert_mail_marker_contract);
    # these two lines pin only that the delegation is still in place, so the
    # rule cannot quietly move back out of reach of that check.
    ("backend/orgtree/api.py",
     r"return supervisor\.mail_in_transcript\(m, _seen_user\)",
     "node_chat's pendrow evidence test is supervisor.mail_in_transcript"),
    ("backend/orgtree/supervisor.py",
     r"needle = _mail_entry_block\(probe\)",
     "the needle is built by RUNNING the formatter, never by rebuilding a "
     "copy of its output — the mistake that produced this bug family twice"),
    ("backend/orgtree/supervisor.py",
     r"return any\(needle in t for t in seen\)",
     "the evidence is ONE contiguous substring, so it cannot be assembled "
     "from two different entries of a batched envelope"),
    ("backend/orgtree/supervisor.py",
     r"return any\(needle \+ MAIL_SEP in t or needle \+ MAIL_TAIL in t\s+for t in seen\)",
     "a WHOLE block must end on a boundary the wrapper wrote, or a body that "
     "is a prefix of another's retires on that other's bubble (a GAP)"),
    ("backend/orgtree/supervisor.py",
     r"MAIL_SEP\.join\(_mail_entry_block\(m\) for m in mail\)",
     "_mail_block is the per-entry formatter plus a wrapper — which is what "
     "makes assert_mail_shapes_span's tracing of _mail_entry_block spanning, "
     "and what makes MAIL_SEP/MAIL_TAIL the real boundaries"),
    ("backend/orgtree/supervisor.py", r'b\.get\("via", "steer"\) == "turn"',
     "delivering_mail's carrier split is what the via axis exercises"),
]


def assert_client_model_matches_source(repo: str) -> list[str]:
    """Fail loudly if the ported rules have drifted from their originals.
    Returns the list of contracts verified."""
    ok = []
    for rel, pat, why in _SOURCE_CONTRACTS:
        p = os.path.join(repo, rel)
        # ⚠ CRLF: .gitattributes sets `* text=auto` + core.autocrlf=true, so
        # these files come back with \r\n and any multi-line pattern silently
        # stops matching. Normalise before matching, always.
        src = open(p, encoding="utf-8").read().replace("\r\n", "\n")
        if not re.search(pat, src):
            raise AssertionError(
                f"client-model drift: {rel} no longer contains /{pat}/ — {why}. "
                f"The port in msgvis.py must be updated with the source.")
        ok.append(f"{rel}: {pat}")
    return ok


# ---------------------------------------------------- the two-sided contracts
#
# Everything above is a grep. These RUN the real functions, because the way
# this bug family came back was a grep that stayed green while the thing it
# described moved (see the note in _SOURCE_CONTRACTS).

def assert_mail_shapes_span(entry_block_real) -> list[str]:
    """`mail_shapes()` must exercise EVERY executable line of the real
    per-entry formatter.

    ⚠ This is the guard on the guards, and it exists because the two below
    iterate a HAND-MAINTAINED list. A formatter branch nobody wrote a shape
    for is rendered by neither of them — so both would have passed through
    FR-05 and D-137 exactly as the greps did, and the redteam demonstrated
    that with a synthetic third header branch (2026-08-19). Line coverage is
    the one statement of "spanning" that a future author cannot forget to
    update: add a branch without a shape and this fails on the next run,
    naming the line.

    Tracing is per-line and the traced function is tiny, so this costs
    microseconds. It deliberately traces ONLY `_mail_entry_block` — the
    batch wrapper has no branches worth pinning and would drag the whole
    call tree in."""
    code = entry_block_real.__code__
    seen_lines: set[int] = set()

    def tracer(frame, event, _arg):
        if frame.f_code is code:
            if event == "line":
                seen_lines.add(frame.f_lineno)
            return tracer
        return None

    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        for _, m in mail_shapes():
            entry_block_real(dict(m))
    finally:
        sys.settrace(old)

    # every line the interpreter considers executable in that function
    # ⚠ exclude the `def` line. From Python 3.11 the RESUME instruction at
    # offset 0 is attributed to it, so findlinestarts reports a line for
    # which no `line` trace event is ever emitted — this guard would then
    # fail on EVERY run naming a line nobody can reach, and the obvious
    # repair under time pressure is to delete the guard. Verified on 3.10,
    # 3.12 and 3.14 (redteam, 2026-08-19); the repo venv is 3.10.
    want = {ln for _, ln in dis.findlinestarts(code)
            if ln is not None and ln != code.co_firstlineno}
    missed = sorted(want - seen_lines)
    if missed:
        src, first = inspect.getsourcelines(entry_block_real)
        show = "\n".join(f"    {ln}: {src[ln - first].rstrip()}"
                         for ln in missed if 0 <= ln - first < len(src))
        raise AssertionError(
            f"mail_shapes() does not span {entry_block_real.__qualname__}: "
            f"{len(missed)} line(s) never ran.\n{show}\n"
            f"Add a shape that reaches them — an unexercised formatter branch "
            f"is invisible to BOTH contracts below, which is how this bug "
            f"family came back twice.")
    return [f"{entry_block_real.__qualname__}: all {len(want)} lines reached"]


def assert_mail_block_matches_source(mail_block_real,
                                     mark_chars: int = PAST_NEEDLE_BUDGET
                                     ) -> list[str]:
    """`mail_block` above must render byte-for-byte what `_mail_block` renders,
    for every shape in `mail_shapes()` — alone and batched together.

    Without this the suite's transcripts are a fiction the moment the real
    formatter grows a line, and every echo/handover check silently starts
    testing an envelope the CLI never sees. That is not hypothetical: it is
    exactly what happened between 2026-08-04 and 2026-08-19."""
    ok = []
    shapes = mail_shapes_crossed(mark_chars)
    for label, m in shapes:
        mine, theirs = mail_block([dict(m)]), mail_block_real([dict(m)])
        if mine != theirs:
            raise AssertionError(
                f"formatter drift on the {label!r} shape: msgvis.mail_block no "
                f"longer renders what supervisor._mail_block renders.\n"
                f"  msgvis    : {mine!r}\n  supervisor: {theirs!r}\n"
                f"Update msgvis.mail_block (and check whether the change also "
                f"moved the body away from mail_in_transcript's needles).")
        ok.append(label)
    # batched: the join, the count line and the per-entry independence
    batch = [dict(m) for _, m in shapes]
    if mail_block(batch) != mail_block_real(batch):
        raise AssertionError("formatter drift when the shapes are batched into "
                             "one envelope (the separator or the count line)")
    ok.append("all shapes in one batch")
    return ok


def assert_mail_marker_contract(mail_block_real, in_transcript,
                                mark_chars: int = PAST_NEEDLE_BUDGET
                                ) -> list[str]:
    """The writer/reader contract, RUN rather than grepped: for every shape the
    formatter can produce, the entry must be found in its own rendering — and
    must NOT be found in a rendering of a different entry.

    This is the check that was missing. The defect it now catches (user report
    2026-08-19) was three real shapes — `reply_to` with and without a named
    author, and `kind == "notice"` — whose bodies the formatter had moved away
    from the timestamp the reader rebuilt beside. Each of them left the pending
    row on screen next to its own durable transcript bubble."""
    ok = []
    shapes = mail_shapes_crossed(mark_chars)
    for label, m in shapes:
        m = dict(m)
        if not in_transcript(m, [mail_block_real([dict(m)])]):
            raise AssertionError(
                f"marker contract broken on the {label!r} shape: the entry is "
                f"NOT found in its own transcript bubble, so its pending row "
                f"will never hand over — a DUPLICATE for as long as the "
                f"journal batch lives.\n  block: "
                f"{mail_block_real([dict(m)])!r}")
        ok.append(f"{label}: found in its own bubble")
    # ⚠ AND IN A BATCHED ONE. A transcript row is a whole DRAINED BATCH — the
    # single-entry rendering above is the easy half, and testing only it is how
    # the first repair of this defect shipped a cross-entry hole (redteam,
    # 2026-08-19). Every shape must still be found when it is one entry among
    # many, wrapped in the separators and the count line.
    every = [dict(m) for _, m in shapes]
    big = mail_block_real(every)
    for label, m in shapes:
        if not in_transcript(dict(m), [big]):
            raise AssertionError(
                f"marker contract broken on the {label!r} shape when BATCHED: "
                f"found alone but not in an envelope carrying the other "
                f"{len(shapes) - 1} entries — its pending row will not hand "
                f"over on a multi-message drain, the common case for a busy "
                f"agent.")
        ok.append(f"{label}: found in a {len(shapes)}-entry batch")
    # ⚠ THE PREFIX-BODY NEGATIVE, which is what makes the whole-block branch's
    # boundary requirement mean anything. Without it, reverting
    # `mail_in_transcript`'s `needle + MAIL_SEP / MAIL_TAIL` to a bare `in`
    # left the entire suite green (redteam, 2026-08-19) — a rule with no
    # behavioural test is a rule that is not tested, however loudly the source
    # grep pins its text.
    #
    # Both entries agree on everything the header carries, INCLUDING the
    # timestamp, and one body is a strict prefix of the other. The short one
    # is on screen nowhere; retiring its pending row is a GAP.
    sat = "2026-08-04T05:09:09.909Z"
    short = {"from": USER, "to": "agent", "at": sat, "kind": "message",
             "body": "plan"}
    longer = {**short, "body": "plan and then some more words"}
    if in_transcript(dict(short), [mail_block_real([dict(longer)])]):
        raise AssertionError(
            "marker contract broken: an entry whose body is a PREFIX of "
            "another's was reported on screen by that other's bubble — the "
            "whole-block needle is being matched without the boundary the "
            "wrapper writes after a complete entry, so a message still in "
            "flight would be hidden (GAP)")
    ok.append("a prefix-body entry is NOT retired by the longer one's bubble")
    # ...and the same pair the RIGHT way round, so the negative above cannot be
    # passing merely because the rule stopped matching anything at all
    if not in_transcript(dict(short), [mail_block_real([dict(short)])]):
        raise AssertionError("the prefix-body entry is not found in its OWN "
                             "bubble — the boundary rule is over-tight")
    if not in_transcript(dict(longer), [mail_block_real([dict(longer)])]):
        raise AssertionError("the longer entry is not found in its own bubble")
    ok.append("...and both are still found in their own bubbles")
    # ...and the cross-entry direction, which is the one that has to hold for
    # the needle to mean anything. Two entries sharing a timestamp (mail is
    # stamped at millisecond resolution — a batch write can collide) must not
    # be able to lend each other evidence: an entry pairing ONE block's header
    # with ANOTHER block's body is on screen nowhere, and retiring its pending
    # row is a GAP, which this system ranks strictly worse than a duplicate.
    at = "2026-08-04T05:00:00.000Z"
    p = {"from": USER, "to": "agent", "at": at, "kind": "message",
         "body": "here is the plan"}
    q = {**p, "body": "ship it"}
    delivered_p_only = mail_block_real([dict(p)])
    if in_transcript({**p, "body": q["body"]}, [delivered_p_only]):
        raise AssertionError(
            "marker contract broken: an entry borrowing this bubble's "
            "timestamp while carrying a body the bubble does not have was "
            "reported as on screen — a message still in flight would be "
            "hidden (GAP)")
    ok.append("a same-timestamp entry cannot borrow evidence from another")
    both = mail_block_real([dict(p), dict(q)])
    if in_transcript({**p, "body": "plan"}, [both]):
        raise AssertionError(
            "marker contract broken: within ONE batched row, an entry matched "
            "using one block's header and another block's body")
    ok.append("no cross-block borrowing inside one batched row")
    if not (in_transcript(dict(p), [both]) and in_transcript(dict(q), [both])):
        raise AssertionError("both same-timestamp entries must still be found "
                             "in the batch that really carries them")
    ok.append("both same-timestamp entries ARE found when really present")
    # ...and the other direction: identity, not resemblance. A DIFFERENT entry
    # (same words, its own timestamp) must not be retired by this bubble, or a
    # message still in flight would be hidden — the gap direction.
    a = dict(shapes[0][1])
    bubble = mail_block_real([dict(a)])
    other = {**a, "at": "2026-08-04T05:00:01.000Z"}
    if in_transcript(other, [bubble]):
        raise AssertionError("marker contract broken: a re-send of the same "
                             "words matched the EARLIER bubble (D-52's trap) — "
                             "the new message would be hidden while in flight")
    ok.append("a same-text re-send does NOT match the earlier bubble")
    same_at_other_body = {**a, "body": "entirely different words"}
    if in_transcript(same_at_other_body, [bubble]):
        raise AssertionError("marker contract broken: an entry sharing only a "
                             "timestamp matched — the body is not being read")
    ok.append("a same-timestamp different-body entry does NOT match")
    if in_transcript(dict(a), ["nothing to do with this mail"]):
        raise AssertionError("marker contract broken: matched an unrelated row")
    ok.append("an unrelated transcript row does NOT match")
    # the legacy entry, which predates the timestamp field and so cannot be
    # rendered by the formatter at all: body-only identity, and never on an
    # empty needle
    legacy = {"from": USER, "to": "agent", "body": "legacy body text"}
    if not in_transcript(legacy, ["... legacy body text ..."]):
        raise AssertionError("legacy (at-less) entry: body identity broken")
    if in_transcript({**legacy, "body": "   "}, ["anything at all"]):
        raise AssertionError("legacy (at-less) entry with a blank body matched "
                             "a bubble — an empty needle matches everything")
    ok.append("legacy at-less entries fall back to the body, never to nothing")
    return ok
