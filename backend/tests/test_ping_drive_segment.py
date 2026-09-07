"""The mail-pointer nudge is a typed `drive` segment, never a `text` one.

THE DEFECT (user, 2026-09-07). "(orgtree) You have new mail above — handle it as
appropriate…" is the drive nudge that follows every mail send: machine context
that tells the agent to read the block above it. The typed composition
(`_segments_for`) filed it as a plain `text` segment because neither
composition site minted `context.drive_mail_pointer` for it, so the human
transcript rendered the sentence beneath the mail card as if somebody had
written it. The leaf is model_only in every field (HUMAN_HIDDEN_VARIANTS) and
the frontend already drops a `drive` segment by that variant — so the fix is
to MINT it, at both sites, for every `ping` carrier:

    §1  _envelope (steer + boundary feed): ping → [.., "drive"], never "text";
        the event carries the nudge verbatim and the site's stated reason
        (null when unstated); the agent text is byte-identical to the non-ping
        composition; the journal row carries `drive`
    §2  the carrier: `_mark_ping(carrier, reason)` rides `ping_reason`;
        `_carrier_is_ping` truthiness is unchanged; send_message's steer path
        composes through the same envelope
    §3  every `ping_reason="…"` literal in the producers is in the table
        (positive control: the scan finds them)
    §4  the turn-start site, through a REAL `_run_turn` against the fake CLI:
        a bare ping carrier + mail in the box → the chat row's segments end
        [.., "mail", "drive"], the provider user event still carries the
        nudge verbatim; and a folded-steer carrier (one that already OWNS its
        batch) is NOT re-minted — its [MAIL] block never disappears into a
        hidden drive segment — but REUSES the journal composition it owns
        (`_owned_segments`, root ruling 04:29Z), after anything newly drained,
        with no duplicate composition across journal rows
    §5  the same reuse at the envelope (boundary feed), and the fallbacks: a
        carrier whose token has no journal row composes its text as before
    §6  TOKEN ORDER IS TEXT ORDER (feature-astra's seam): each drain site puts
        its new token at the FRONT, because the envelope prepends the new batch
        to the text the carrier already held — so a carrier reconstructed with
        two owned tokens re-reads [new, old] against text [new][old]; pinned at
        the envelope with successive reuse and at the real turn start

Hermetic: throwaway ORGTREE_DATA/HOME set before any orgtree import; §4 runs
fakecli.js in-process exactly as test_stuck_mail_pointer_drop.py does.

    python backend/tests/test_ping_drive_segment.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import traceback

RIG = tempfile.mkdtemp(prefix="orgtree-pingdrive-")
HOME = os.path.join(RIG, "home")
os.makedirs(HOME, exist_ok=True)
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKECLI = os.path.join(BACKEND, "tests", "fakecli.js")
CFG = os.path.join(RIG, "fakecli.json")

os.environ["ORGTREE_DATA"] = os.path.join(RIG, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
os.environ["HOME"] = os.environ["USERPROFILE"] = HOME
os.environ["ORGTREE_CLAUDE_CLI"] = FAKECLI
os.environ["FAKECLI_CONFIG"] = CFG
os.environ["ORGTREE_TURN_IDLE"] = "60"
os.environ.pop("ORGTREE_WARM", None)
os.environ.pop("ORGTREE_STEER_HOOK", None)
sys.path.insert(0, BACKEND)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
with open(CFG, "w", encoding="utf-8") as _f:
    json.dump({"default": {"echoMs": 30, "firstEventMs": 50, "resultMs": 30}}, _f)

from orgtree import events, store, supervisor as S                 # noqa: E402
from orgtree import events_table as T                              # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

assert store.DATA_ROOT.startswith(RIG), store.DATA_ROOT

VARIANT = "context.drive_mail_pointer"
NUDGE = ("(orgtree) You have new mail above — handle it as appropriate, and use "
         "orgtree_status when your own task state changes.")
PASSED = 0
FAILED: list[str] = []


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception:                                        # noqa: BLE001
        FAILED.append(f"{label}\n{traceback.format_exc()}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


_n = [0]


def fresh(name: str = "boss") -> str:
    _n[0] += 1
    org = store.create_org(f"pingdrive-{_n[0]}", [])
    org.hire(USER, None, "opus", 20, name)
    store.save_org(org)
    return org.d["slug"]


def journal_row(slug: str, nid: str, tok: str) -> dict:
    rows = [b for b in (store.load_org(slug).d.get("delivering") or {}).get(nid, [])
            if b["tok"] == tok]
    assert len(rows) == 1, rows
    return rows[0]


# ══════════════════════════════════════════════════════════════════════════ §1
print("\n§1  _envelope: a ping composes a typed drive segment")


def _ping_is_drive():
    slug = fresh()
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "hello boss", "message", typed=True)
    store.save_org(org)
    segs_out: list = []
    text, tok, _ = S._envelope(slug, "boss", NUDGE, via="turn", segments_out=segs_out,
                               ping=True, ping_reason="agent_mail")
    assert tok, "the box had mail: something must have drained"
    segs = segs_out[0]
    assert [s["kind"] for s in segs] == ["mail", "drive"], segs
    assert not any(s["kind"] == "text" for s in segs), "the nudge must not be a text segment"
    drive = segs[1]
    assert drive["text"] == NUDGE, "the segment text is the nudge verbatim"
    ev = drive["event"]
    assert ev["variant"] == VARIANT and ev["text"] == NUDGE and ev["reason"] == "agent_mail"
    assert ev["actor"] == {"kind": "system", "id": S.SYSTEM}
    assert ev["object"] == {"kind": "node", "org": slug, "id": "boss", "name": "boss",
                            "generation": 0}
    events.validate_event(ev)
    # structural hide contract: the variant is human-hidden by disposition, so the
    # frontend drops the whole segment by variant — no string is ever matched
    assert VARIANT in events.human_hidden_variants()
    # the journal row carries the drive too (design: typed on the journal)
    row = journal_row(slug, "boss", tok)
    assert row["drive"] == ev and row["segments"] == segs
    # the AGENT text is untouched: mail block first, the nudge verbatim last
    assert text.startswith("[MAIL") and text.endswith("\n\n" + NUDGE), text[-200:]
    # the wire keeps the segment for both profiles (operator: full; visitor: public)
    pub = events.wire_segments(segs, public=True)
    assert pub[1]["kind"] == "drive" and pub[1]["event_public"]["variant"] == VARIANT
    assert pub[1]["text"] == NUDGE and "event" not in pub[1]
    priv = events.wire_segments(segs, public=False)
    assert priv[1]["event"] == ev


check("ping · segments [mail, drive]; event = nudge verbatim + reason; journal `drive`; "
      "human-hidden; wire both profiles", _ping_is_drive)


def _agent_text_parity():
    """B4: the ping flag changes the COMPOSITION only — the text the agent reads is
    byte-identical to what the non-ping composition produced for the same box."""
    slug = fresh()
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "same words", "message", typed=True)
    store.save_org(org)
    t_ping, tok, _ = S._envelope(slug, "boss", NUDGE, via="turn", ping=True)
    assert tok
    S._fold_back_undelivered(slug, "boss", only_toks=[tok])       # the SAME mail, back in the box
    t_plain, tok2, _ = S._envelope(slug, "boss", NUDGE, via="turn")
    assert tok2 and t_plain == t_ping, "the agent text must not depend on the ping flag"


check("ping · B4: agent text byte-identical to the non-ping composition of the same box",
      _agent_text_parity)


def _unstated_reason():
    slug = fresh()
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "x", "message", typed=True)
    store.save_org(org)
    segs_out: list = []
    _, tok, _ = S._envelope(slug, "boss", "(orgtree) mail above", via="steer",
                            segments_out=segs_out, ping=True)
    ev = segs_out[0][-1]["event"]
    assert segs_out[0][-1]["kind"] == "drive" and ev["reason"] is None
    events.validate_event(ev)
    # round trip through the bare codec keeps the null (never dropped, never guessed)
    assert events.decode_ev(json.loads(json.dumps(events.encode_ev(ev))))["reason"] is None
    assert journal_row(slug, "boss", tok)["drive"]["reason"] is None


check("ping · an unstated reason is recorded as null (never inferred)", _unstated_reason)


def _controls():
    # control 1: the non-ping composition is unchanged — the text segment stays
    slug = fresh()
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "x", "message", typed=True)
    store.save_org(org)
    segs_out: list = []
    _, tok, _ = S._envelope(slug, "boss", "authored words", via="turn", segments_out=segs_out)
    assert [s["kind"] for s in segs_out[0]] == ["mail", "text"]
    assert segs_out[0][1] == {"kind": "text", "text": "authored words"}
    assert journal_row(slug, "boss", tok)["drive"] is None
    # control 2: a ping against an EMPTY box composes [drive] and journals nothing —
    # the drop sites (tok is None) still decide its fate exactly as before
    slug2 = fresh()
    segs_out2: list = []
    text, tok2, _ = S._envelope(slug2, "boss", NUDGE, via="steer", segments_out=segs_out2,
                                ping=True, ping_reason="user_mail")
    assert tok2 is None and text == NUDGE
    assert [s["kind"] for s in segs_out2[0]] == ["drive"]
    assert "boss" not in (store.load_org(slug2).d.get("delivering") or {})
    # control 3: a bad reason is refused by the validator, not stored
    try:
        S._ping_drive(store.load_org(slug2), "boss", NUDGE, "not_a_reason")
    except events.EventInvalid:
        pass
    else:
        raise AssertionError("an out-of-table reason must be refused")


check("controls · non-ping keeps its text segment; empty box → [drive], no journal; "
      "bad reason refused", _controls)


# ══════════════════════════════════════════════════════════════════════════ §2
print("\n§2  the carrier rides the reason; the steer path composes through the envelope")


def _carrier_shapes():
    for carrier in ("bare text", {"text": "t", "view": "v"}, {"toks": ["a"], "text": "t"}):
        marked = S._mark_ping(carrier, "docket_reply")
        assert S._carrier_is_ping(marked) and marked["ping_reason"] == "docket_reply"
        assert S._carrier_ping_reason(marked) == "docket_reply"
        plain = S._mark_ping(carrier)
        assert S._carrier_is_ping(plain) and "ping_reason" not in plain, plain
        assert S._carrier_ping_reason(plain) is None
        if isinstance(carrier, dict):
            for k, v in carrier.items():
                assert marked[k] == v and plain[k] == v, "journal tokens / view preserved"
    assert S._carrier_ping_reason({"text": "t"}) is None, "not a ping → None"
    assert S._carrier_ping_reason("t") is None
    assert not S._carrier_is_ping({"text": "t", "ping_reason": "user_mail"}), \
        "a reason without the ping flag is not a ping (the flag decides, the reason describes)"


check("carrier · _mark_ping(reason) rides ping_reason; truthiness and tokens unchanged",
      _carrier_shapes)


def _steer_path():
    slug = fresh()
    st = S.state(slug, "boss")
    with S._state_lock:
        st["busy"] = True
        st["responding"] = True
    try:
        org = store.load_org(slug)
        org.post_mail(USER, "boss", "mid-task words", "message", typed=True)
        store.save_org(org)
        r = S.send_message(slug, "boss", NUDGE, mail_ping=True, ping_reason="user_mail")
        assert r.get("steering"), r
        carrier = st["steer"][-1]
        assert S._carrier_is_ping(carrier) and carrier["ping_reason"] == "user_mail"
        (tok,) = carrier["toks"]
        row = journal_row(slug, "boss", tok)
        assert [s["kind"] for s in row["segments"]] == ["mail", "drive"], row["segments"]
        assert row["segments"][1]["event"]["reason"] == "user_mail"
        assert row["drive"]["variant"] == VARIANT and row["drive"]["text"] == NUDGE
        assert carrier["text"].endswith("\n\n" + NUDGE), "the steered agent text keeps the nudge"
        # queue path (busy, not responding): the reason rides the queued carrier
        with S._state_lock:
            st["responding"] = False
        org = store.load_org(slug)
        org.post_mail(USER, "boss", "queued words", "message", typed=True)
        store.save_org(org)
        r2 = S.send_message(slug, "boss", NUDGE, mail_ping=True, ping_reason="agent_mail")
        assert r2.get("queued") == 1, r2
        q = st["queue"][-1]
        assert S._carrier_is_ping(q) and S._carrier_ping_reason(q) == "agent_mail"
    finally:
        with S._state_lock:
            st["busy"] = False
            st["responding"] = False
            st["steer"] = []
            st["queue"].clear()


check("steer/queue · send_message(mail_ping, ping_reason) → typed journal composition / "
      "reason on the queued carrier", _steer_path)


# ══════════════════════════════════════════════════════════════════════════ §3
print("\n§3  every stated reason in the producers is in the table")


def _reasons_in_table():
    lits = set(T.LEAVES[VARIANT]["fields"]["reason"]["t"][2:].rstrip("?]").split("|"))
    assert "user_mail" in lits and "reminder" in lits, lits
    found: list[tuple[str, str]] = []
    for rel in ("orgtree/api.py", "orgtree/supervisor.py"):
        src = open(os.path.join(BACKEND, rel), encoding="utf-8").read()
        for m in re.finditer(r'ping_reason="([a-z_]+)"', src):
            found.append((rel, m.group(1)))
    assert len(found) >= 14, f"positive control: the scan found only {found}"
    bad = [f for f in found if f[1] not in lits]
    assert not bad, bad
    # the conditional site (the orgtree_message target) is present and names agent_mail
    api_src = open(os.path.join(BACKEND, "orgtree/api.py"), encoding="utf-8").read()
    assert 'ping_reason="agent_mail" if target == mail_to else None' in api_src


check("reasons · every ping_reason literal in api/supervisor is a table literal "
      "(≥14 found)", _reasons_in_table)


# ══════════════════════════════════════════════════════════════════════════ §4
print("\n§4  the turn-start site, through a real _run_turn against the fake CLI")

ORG = store.create_org("pingdrive rig")
SLUG = ORG.d["slug"]
ORG.hire(USER, None, "haiku", 5, "pointerboy", add_dirs=[],
         tools={"bash": False, "web": False, "edit": False, "subagents": False, "mcp": []},
         org_visibility="team", charter="ping-drive rig agent")
store.save_org(ORG)
NID = "pointerboy"


def run_turn(carrier) -> None:
    st = S.state(SLUG, NID)
    with S._state_lock:
        st["busy"] = True
    try:
        S._run_turn(SLUG, NID, carrier)                        # blocking
    finally:
        with S._state_lock:
            st["busy"] = False


def chat_user_rows() -> list[dict]:
    org = store.load_org(SLUG)
    chat = S.read_chat(org, NID, hold_back=False)
    return [m for m in chat["messages"] if m.get("role") == "user"]


def transcript_user_texts() -> list[str]:
    import glob
    out: list[str] = []
    for p in glob.glob(os.path.join(HOME, ".claude", "projects", "*", "*.jsonl")):
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "user":
                continue
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, str):
                out.append(content)
            elif isinstance(content, list):
                out.extend(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
    return out


def _turn_start_bare_ping():
    marker = "TS-" + os.urandom(4).hex()
    with store.DOC_LOCK:
        org = store.load_org(SLUG)
        org.post_mail(USER, NID, "turn-start words " + marker, "message", typed=True)
        store.save_org(org)
    run_turn(S._mark_ping(NUDGE, "user_mail"))
    # the provider saw the mail block AND the nudge, verbatim, in one user event
    ev_texts = [t for t in transcript_user_texts() if marker in t]
    assert ev_texts, "the turn did not reach the fake CLI with the mail"
    assert ev_texts[-1].rstrip().endswith(NUDGE), ev_texts[-1][-300:]
    # the chat row's typed composition ends [.., mail, drive] — no text segment
    rows = [m for m in chat_user_rows() if isinstance(m.get("segments"), list)
            and marker in json.dumps(m["segments"])]
    assert rows, "no projected chat row with segments for this turn"
    segs = rows[-1]["segments"]
    kinds = [s["kind"] for s in segs]
    assert kinds[-2:] == ["mail", "drive"] and "text" not in kinds, kinds
    drive = segs[-1]
    assert drive["text"] == NUDGE and drive["event"]["variant"] == VARIANT
    assert drive["event"]["reason"] == "user_mail"
    for s in segs[:-2]:
        assert s["kind"] == "state", kinds        # org_state / provider_usage ride first


check("turn start · bare ping + mail → chat segments [.., mail, drive]; provider text keeps "
      "the nudge verbatim", _turn_start_bare_ping)


def _turn_start_folded_reuses_owned():
    """A steer carrier folded into the queue already OWNS its batch (journal token +
    enveloped text). The turn start must NOT mint again — that would wrap the whole
    [MAIL] block into a hidden drive segment and the message would vanish from the
    human transcript — and must not file the enveloped text as a text segment
    either (the nudge would show, twice over with the mail block). It re-reads the
    composition it owns from the journal, AFTER whatever this turn drains fresh."""
    marker = "FD-" + os.urandom(4).hex()
    with store.DOC_LOCK:
        org = store.load_org(SLUG)
        org.post_mail(USER, NID, "folded words " + marker, "message", typed=True)
        store.save_org(org)
    # the steer envelope: drains the box, journals [mail, drive], returns the enveloped text
    segs_out: list = []
    etext, tok, _ = S._envelope(SLUG, NID, NUDGE, via="steer", segments_out=segs_out,
                                ping=True, ping_reason="user_mail")
    owned = segs_out[0]
    assert tok and [s["kind"] for s in owned] == ["mail", "drive"]
    assert not S._has_deliverable(SLUG, NID), "the fixture must leave the box EMPTY"
    # …and a NEW message lands before the folded carrier starts its turn
    marker2 = "NEW-" + os.urandom(4).hex()
    with store.DOC_LOCK:
        org = store.load_org(SLUG)
        org.post_mail(USER, NID, "newer words " + marker2, "message", typed=True)
        store.save_org(org)
    carrier = S._mark_ping({"toks": [tok], "text": etext}, "user_mail")
    run_turn(carrier)
    ev_texts = [t for t in transcript_user_texts() if marker in t]
    assert ev_texts, "HELD: the folded carrier was not delivered"
    # agent bytes: the new batch first, then the owned envelope ending in the nudge
    assert marker2 in ev_texts[-1] and ev_texts[-1].index(marker2) < ev_texts[-1].index(marker)
    assert ev_texts[-1].rstrip().endswith(NUDGE) and ev_texts[-1].count(NUDGE) == 1
    rows = [m for m in chat_user_rows() if isinstance(m.get("segments"), list)
            and marker in json.dumps(m["segments"])]
    assert rows, "no projected chat row for the folded carrier"
    segs = rows[-1]["segments"]
    kinds = [s["kind"] for s in segs]
    assert "text" not in kinds, f"the enveloped text was filed as a text segment: {kinds}"
    body = [s for s in segs if s["kind"] != "state"]
    assert [s["kind"] for s in body] == ["mail", "mail", "drive"], kinds
    assert marker2 in json.dumps(body[0]) and marker in json.dumps(body[1]), \
        "order: the newly drained batch, then the owned composition"
    assert body[1:] == owned, "the owned composition is the journal's, re-read verbatim"
    assert not any("[MAIL" in d["text"] for d in segs if d["kind"] == "drive"), \
        "OVERSHOOT: the [MAIL] block was wrapped into a hidden drive segment"
    assert body[2]["text"] == NUDGE and body[2]["event"]["reason"] == "user_mail"


check("turn start · a folded carrier reuses its OWNED journal composition after the new "
      "drain — no text segment, no re-mint, order kept", _turn_start_folded_reuses_owned)


def _turn_start_no_duplicate_rows():
    """The journal row written for the NEW drain holds only its own composition —
    never a copy of the owned one — so the live per-carrier snapshot
    (commit_steer's by-token read) cannot show a message twice."""
    marker = "DUP-" + os.urandom(4).hex()
    with store.DOC_LOCK:
        org = store.load_org(SLUG)
        org.post_mail(USER, NID, "owned " + marker, "message", typed=True)
        store.save_org(org)
    etext, tok, _ = S._envelope(SLUG, NID, NUDGE, via="steer", ping=True)
    with store.DOC_LOCK:
        org = store.load_org(SLUG)
        org.post_mail(USER, NID, "fresh " + marker, "message", typed=True)
        store.save_org(org)
    seen: list = []
    real = S._journal_drain

    def spy(org, nid, mail, pending, via="steer", **kw):
        # snapshot NOW: the caller later inserts the state segments into its own
        # list in place, and an aliased reference would read those too
        seen.append(json.loads(json.dumps(kw.get("segments"))))
        return real(org, nid, mail, pending, via, **kw)
    S._journal_drain = spy
    try:
        run_turn(S._mark_ping({"toks": [tok], "text": etext}, "user_mail"))
    finally:
        S._journal_drain = real
    assert len(seen) == 1 and seen[0] is not None, seen
    assert [s["kind"] for s in seen[0]] == ["mail"], seen[0]
    assert "owned " + marker not in json.dumps(seen[0]), "the new row copied the owned batch"


check("turn start · the new drain's journal row holds its own composition only",
      _turn_start_no_duplicate_rows)


# ══════════════════════════════════════════════════════════════════════════ §5
print("\n§5  the envelope reuses owned composition too; fallbacks stay plain")


def _envelope_owned():
    slug = fresh()
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "owned one", "message", typed=True)
    store.save_org(org)
    o1: list = []
    etext, tok, _ = S._envelope(slug, "boss", NUDGE, via="steer", segments_out=o1, ping=True)
    owned = o1[0]
    assert [s["kind"] for s in owned] == ["mail", "drive"]
    # the boundary feed re-envelopes the folded carrier: nothing new → exactly the owned
    o2: list = []
    t2, tok2, _ = S._envelope(slug, "boss", etext, via="turn", segments_out=o2,
                              ping=False, owned_toks=[tok])
    assert tok2 is None and t2 == etext and o2[0] == owned
    # something new drained too → [new mail] + owned, and the new row holds only its own
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "fresh one", "message", typed=True)
    store.save_org(org)
    o3: list = []
    t3, tok3, _ = S._envelope(slug, "boss", etext, via="turn", segments_out=o3,
                              ping=False, owned_toks=[tok])
    assert tok3 and t3.endswith("\n\n" + etext)
    assert [s["kind"] for s in o3[0]] == ["mail", "mail", "drive"] and o3[0][1:] == owned
    assert o3[0][0]["rows"][0]["body"] == "fresh one"
    new_row = journal_row(slug, "boss", tok3)
    assert [s["kind"] for s in new_row["segments"]] == ["mail"] and new_row["drive"] is None
    assert journal_row(slug, "boss", tok)["segments"] == owned, "the owned row is untouched"


check("envelope · owned_toks re-reads the journal composition after the new drain; "
      "rows never duplicate", _envelope_owned)


def _owned_fallbacks():
    slug = fresh()
    org = store.load_org(slug)
    assert S._owned_segments(org, "boss", None) is None
    assert S._owned_segments(org, "boss", []) is None
    assert S._owned_segments(org, "boss", ["no-such-token"]) is None, \
        "a token with no row: None (compose the text as before), never a partial list"
    org.post_mail(USER, "boss", "x", "message", typed=True)
    store.save_org(org)
    etext, tok, _ = S._envelope(slug, "boss", NUDGE, via="steer", ping=True)
    org = store.load_org(slug)
    assert S._owned_segments(org, "boss", [tok]) == journal_row(slug, "boss", tok)["segments"]
    assert S._owned_segments(org, "boss", [tok, "missing"]) is None, "ALL tokens or none"
    # a v1 row without `segments` (pre-typed journal shape) → None as well
    with store.DOC_LOCK:
        o = store.load_org(slug)
        o.d["delivering"]["boss"].append({"tok": "v1", "at": "x", "mail": [], "notices": []})
        store.save_org(o)
    assert S._owned_segments(store.load_org(slug), "boss", ["v1"]) is None
    # and the text fallback is what the envelope then composes (unchanged behaviour)
    o4: list = []
    S._envelope(slug, "boss", etext, via="turn", segments_out=o4, owned_toks=["v1"])
    assert o4[0] == [{"kind": "text", "text": etext}]


check("owned · fallbacks: no tokens / unknown token / v1 row → None, text composed as before",
      _owned_fallbacks)


# ══════════════════════════════════════════════════════════════════════════ §6
print("\n§6  token order is text order — successive reuse with two owned tokens")


def _two_owned_tokens_envelope():
    """The boundary feed's reconstruction, step by step: carrier {toks:[old], text:E1}
    → _envelope drains new → text E2 = [new] + E1, token t2 goes to the FRONT →
    carrier {toks:[t2, old], text:E2} folds/requeues → its next envelope re-reads
    [new segs, old segs], which is what E2 reads as. The wrong order ([old, t2]) is
    the seam: it would compose [old, new] against a text that says [new, old]."""
    slug = fresh()
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "OLD words", "message", typed=True)
    store.save_org(org)
    o1: list = []
    e1, t_old, _ = S._envelope(slug, "boss", NUDGE, via="steer", segments_out=o1, ping=True)
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "NEW words", "message", typed=True)
    store.save_org(org)
    o2: list = []
    e2, t_new, _ = S._envelope(slug, "boss", e1, via="turn", segments_out=o2, owned_toks=[t_old])
    assert t_new and e2.index("NEW words") < e2.index("OLD words"), "text: new first"
    toks = [t_old]
    toks.insert(0, t_new)                       # what both drain sites now do
    o3: list = []
    e3, t3, _ = S._envelope(slug, "boss", e2, via="turn", segments_out=o3, owned_toks=toks)
    assert t3 is None and e3 == e2
    bodies = [r["body"] for sg in o3[0] if sg["kind"] == "mail" for r in sg["rows"]]
    assert bodies == ["NEW words", "OLD words"], bodies
    assert o3[0] == o2[0], "successive reuse reproduces the composition the text was built from"
    assert [sg["kind"] for sg in o3[0]] == ["mail", "mail", "drive"]
    # the seam itself, as a control: the appended order composes against the text
    o4: list = []
    S._envelope(slug, "boss", e2, via="turn", segments_out=o4, owned_toks=[t_old, t_new])
    wrong = [r["body"] for sg in o4[0] if sg["kind"] == "mail" for r in sg["rows"]]
    assert wrong == ["OLD words", "NEW words"], \
        "control: _owned_segments follows carrier order, so the order at the site decides"


check("order · envelope: two owned tokens re-read [new, old] against text [new][old]; "
      "appended order is the seam (control)", _two_owned_tokens_envelope)


def _turn_start_token_order():
    """The REAL turn start: a folded carrier {toks:[old]} drains a new batch; the
    carrier's token list — the one a filter replay would carry back as
    `pend_toks` — must read [new, old]. Observed through the confirm call, which
    receives exactly that list once the provider consumed the text."""
    marker = "TO-" + os.urandom(4).hex()
    with store.DOC_LOCK:
        org = store.load_org(SLUG)
        org.post_mail(USER, NID, "old " + marker, "message", typed=True)
        store.save_org(org)
    etext, t_old, _ = S._envelope(SLUG, NID, NUDGE, via="steer", ping=True)
    with store.DOC_LOCK:
        org = store.load_org(SLUG)
        org.post_mail(USER, NID, "new " + marker, "message", typed=True)
        store.save_org(org)
    confirmed: list = []
    real = S._confirm_delivered

    def spy(slug, nid, toks):
        confirmed.append(list(toks))
        return real(slug, nid, toks)
    S._confirm_delivered = spy
    try:
        run_turn(S._mark_ping({"toks": [t_old], "text": etext}, "user_mail"))
    finally:
        S._confirm_delivered = real
    two = [c for c in confirmed if len(c) == 2]
    assert two, f"no two-token confirm observed: {confirmed}"
    assert two[0][1] == t_old and two[0][0] != t_old, \
        f"carrier tokens must be [new, old] (text order), got {two[0]} with old={t_old}"


check("order · real turn start: the carrier's tokens read [new, old] after the drain",
      _turn_start_token_order)


def _both_sites_insert_front():
    """Source pin for the two drain sites (the boundary feed cannot be driven
    hermetically here): each inserts at the front, none appends. Positive control:
    the scan sees both sites."""
    src = open(os.path.join(BACKEND, "orgtree/supervisor.py"), encoding="utf-8").read()
    assert src.count("ntoks.insert(0, ntok)") == 1 and "ntoks.append(ntok)" not in src
    assert src.count("toks.insert(0, _journal_drain(") == 1 and \
        "toks.append(_journal_drain(" not in src


check("order · both drain sites insert the new token at the front (source pin)",
      _both_sites_insert_front)

print(f"\n{PASSED} checks passed, {len(FAILED)} failed")
for f in FAILED:
    print("\n" + f)
sys.exit(1 if FAILED else 0)
