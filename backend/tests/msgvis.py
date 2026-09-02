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

import json
import os
import re
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
    catch a formatter/marker mismatch."""
    blocks = []
    for m in mail:
        tag = " ⚠ THE USER — user instructions outrank your chain" \
            if m["from"] == USER else ""
        b = (f"FROM {m['from']} ({m.get('relationship', 'agent')}{tag}) · "
             f"{m.get('kind', 'message')} · {m['at']}\n{m['body']}")
        for a in m.get("attachments") or []:
            nb = int(a.get("bytes") or 0)
            size = f"{nb} B" if nb < 1024 else f"{nb / 1024:.0f} KB"
            b += (f"\n[ATTACHED FILE: {a.get('path')} ({size}) — in your "
                  f"working folder]")
        blocks.append(b)
    return (f"[MAIL — {len(mail)} message(s)]\n" + "\n---\n".join(blocks)
            + "\n[END MAIL]")


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
    # the D-55 identity marker moved into one function shared with read_chat's
    # hold-back (D-229, review round 2: a reply snapshot or a notice header
    # sits between the stamp and the body, so the two are matched separately)
    ("backend/orgtree/api.py",
     r"any\(supervisor\.mail_marker_in\(t, m\) for t in _seen_user\)",
     "the D-55 identity marker is what the transcript-echo tests exercise"),
    ("backend/orgtree/supervisor.py", r'stamp = f"· \{at\}"',
     "the marker's first half is the entry's own stamp"),
    ("backend/orgtree/supervisor.py",
     r"return \(not head\) or head in raw\[i \+ len\(stamp\):\]",
     "…and its second half is the head of the raw body, after the stamp"),
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
