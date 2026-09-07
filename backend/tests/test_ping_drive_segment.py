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
        nudge verbatim; and the folded-steer control (a carrier that already
        OWNS its batch) is NOT re-minted — its [MAIL] block never disappears
        into a hidden drive segment

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


def _turn_start_folded_control():
    """A steer carrier folded into the queue already OWNS its batch (journal token +
    enveloped text). Its typed composition was minted by the envelope that drained
    it; the turn start must NOT mint again — doing so would wrap the whole [MAIL]
    block into a hidden drive segment and the message would vanish from the human
    transcript while still reaching the agent."""
    marker = "FD-" + os.urandom(4).hex()
    with store.DOC_LOCK:
        org = store.load_org(SLUG)
        org.post_mail(USER, NID, "folded words " + marker, "message", typed=True)
        store.save_org(org)
    # the steer envelope: drains the box, journals [mail, drive], returns the enveloped text
    segs_out: list = []
    etext, tok, _ = S._envelope(SLUG, NID, NUDGE, via="steer", segments_out=segs_out,
                                ping=True, ping_reason="user_mail")
    assert tok and [s["kind"] for s in segs_out[0]] == ["mail", "drive"]
    assert not S._has_deliverable(SLUG, NID), "the fixture must leave the box EMPTY"
    carrier = S._mark_ping({"toks": [tok], "text": etext}, "user_mail")
    run_turn(carrier)
    ev_texts = [t for t in transcript_user_texts() if marker in t]
    assert ev_texts, "HELD: the folded carrier was not delivered"
    rows = [m for m in chat_user_rows() if isinstance(m.get("segments"), list)
            and marker in json.dumps(m["segments"])]
    assert rows, "no projected chat row for the folded carrier"
    segs = rows[-1]["segments"]
    drives = [s for s in segs if s["kind"] == "drive"]
    assert not any("[MAIL" in d["text"] for d in drives), \
        "OVERSHOOT: the [MAIL] block was wrapped into a hidden drive segment"
    assert not drives, [s["kind"] for s in segs]


check("turn start · CONTROL: a folded carrier owning its batch is not re-minted — the "
      "[MAIL] block never hides", _turn_start_folded_control)

print(f"\n{PASSED} checks passed, {len(FAILED)} failed")
for f in FAILED:
    print("\n" + f)
sys.exit(1 if FAILED else 0)
