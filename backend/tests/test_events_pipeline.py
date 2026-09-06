"""Step 2b — the delivery envelope and typed composition segments (design v5 §6).

    §1  _envelope drains into a journal row carrying mode / attempt / segments with
        FULL events (bare codec), in composition order: notices, mail, text
    §2  a fold-back stamps `redelivered` on the row; the next drain's attempt is 2 —
        and the event itself is untouched (delivery never inside ev)
    §3  the projection sidecar persists `segments`; _take_prompt_view hands them back;
        a v1 row (no segments) hands back None
    §4  delivering_mail rows carry a sibling `delivery` {mode, via, attempt, at}
    §5  wire_segments: operator gets full events; visitor gets PublicEvent / withheld
    §6  the generated TS carries Segment / PublicSegment / Delivery (regenerated)

Hermetic: throwaway ORGTREE_DATA, no processes (send_message is not called).

    python backend/tests/test_events_pipeline.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

_TMP = tempfile.mkdtemp(prefix="orgtree-evpipe-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ.pop("ORGTREE_WARM", None)
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from orgtree import events, store, supervisor                 # noqa: E402
from orgtree.ledger import USER, actor_of                      # noqa: E402

assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT

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


def fresh():
    _n[0] += 1
    org = store.create_org(f"pipe-{_n[0]}", [])
    org.hire(USER, None, "opus", 20, "boss")
    # no second hire: a hire queues a LEGACY notice for boss, which would sit first
    # in the box and rightly carry no `ev` — this suite wants a clean box
    store.save_org(org)
    return org.d["slug"]


def node_ref(slug, nid):
    return {"kind": "node", "org": slug, "id": nid, "name": nid, "generation": 0}


# ══════════════════════════════════════════════════════════════════════════ §1
print("\n§1  _envelope → journal row with mode / attempt / segments")


def _envelope_journal():
    slug = fresh()
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "hello boss", "message", typed=True)
    org._notify_ev(["boss"], events.mint("lifecycle.renamed", actor_of(USER),
                                         node_ref(slug, "kid"), old="kid", new="kid2", by=USER))
    store.save_org(org)
    segs_out: list = []
    text, tok, imgs = supervisor._envelope(slug, "boss", "(orgtree) nudge", via="turn",
                                           segments_out=segs_out)
    assert tok and text.startswith("[ORG NOTICES")
    org = store.load_org(slug)
    row = org.d["delivering"]["boss"][-1]
    assert row["tok"] == tok and row["via"] == "turn"
    assert row["mode"] == "turn" and row["attempt"] == 1 and row["drive"] is None
    segs = row["segments"]
    assert [s["kind"] for s in segs] == ["notices", "mail", "text"], segs
    assert segs[2]["text"] == "(orgtree) nudge"
    m = segs[1]["rows"][0]
    assert m["ev"]["variant"] == "ordinary.message" and m["ev"]["body"] == "hello boss", \
        "segment rows carry the FULL event (bare codec)"
    events.validate_event(m["ev"])
    n = segs[0]["rows"][0]
    assert n["ev"]["variant"] == "lifecycle.renamed" and n["text"].startswith("You have been renamed")
    assert segs_out and segs_out[0] == segs, "segments_out mirrors the journal"


check("journal · mode=turn, attempt=1, segments [notices, mail, text] with full events",
      _envelope_journal)


def _no_drain_no_journal():
    slug = fresh()
    segs_out: list = []
    text, tok, _ = supervisor._envelope(slug, "boss", "just text", via="steer",
                                        segments_out=segs_out)
    assert tok is None and text == "just text"
    assert segs_out == [[{"kind": "text", "text": "just text"}]]
    assert "boss" not in (store.load_org(slug).d.get("delivering") or {})


check("journal · an empty box journals nothing; segments are just the text", _no_drain_no_journal)


# ══════════════════════════════════════════════════════════════════════════ §2
print("\n§2  fold-back → attempt 2; the event is untouched")


def _attempt():
    slug = fresh()
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "retry me", "message", typed=True)
    store.save_org(org)
    _, tok1, _ = supervisor._envelope(slug, "boss", "n1", via="turn")
    supervisor._fold_back_undelivered(slug, "boss", only_toks=[tok1])
    org = store.load_org(slug)
    back = org.d["mail"]["boss"][0]
    assert back["redelivered"] == 1 and back["body"] == "retry me"
    assert "redelivered" not in json.dumps(back["ev"]), "delivery never inside ev"
    _, tok2, _ = supervisor._envelope(slug, "boss", "n2", via="turn")
    org = store.load_org(slug)
    rows = [b for b in org.d["delivering"]["boss"] if b["tok"] == tok2]
    assert rows and rows[0]["attempt"] == 2
    ev = rows[0]["segments"][0]["rows"][0]["ev"]
    assert ev["body"] == "retry me" and "redelivered" not in ev


check("attempt · fold-back stamps redelivered on the row; re-drain journals attempt=2; ev clean",
      _attempt)


# ══════════════════════════════════════════════════════════════════════════ §3
print("\n§3  projection sidecar persists segments; _take_prompt_view returns them")


def _sidecar():
    slug = fresh()
    sid = "sid-" + slug
    segs = [{"kind": "mail", "rows": [{"id": "m1", "from": USER, "kind": "message",
                                       "body": "hi", "at": "2026-09-06T00:00:00Z",
                                       "ev": events.mint("ordinary.message", actor_of(USER),
                                                         None, body="hi")}]},
            {"kind": "text", "text": "nudge"}]
    supervisor._record_prompt_view(slug, sid, "RAW", "VISIBLE", spans=[], segments=segs)
    supervisor._record_prompt_view(slug, sid, "RAW-V1", "VISIBLE-V1")          # no segments
    views = supervisor._load_prompt_views(slug, sid) if hasattr(supervisor, "_load_prompt_views") \
        else None
    if views is None:
        # read the sidecar the way the reader does
        path = supervisor._prompt_view_path(slug, sid)
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        views = {}
        for r in rows:
            views.setdefault(r["sha256"], []).append(r)
    ok, visible, got = supervisor._take_prompt_view(views, "RAW")
    assert ok and visible == "VISIBLE" and got == segs
    ok, visible, got = supervisor._take_prompt_view(views, "RAW-V1")
    assert ok and visible == "VISIBLE-V1" and got is None, "a row without segments says None"
    ok, visible, got = supervisor._take_prompt_view(views, "NEVER")
    assert not ok and got is None


check("sidecar · segments persisted with the projection and handed back; absent → None",
      _sidecar)


# ══════════════════════════════════════════════════════════════════════════ §4
print("\n§4  delivering_mail rows carry a sibling delivery envelope")


def _delivering():
    slug = fresh()
    org = store.load_org(slug)
    org.post_mail(USER, "boss", "in flight", "message", typed=True)
    store.save_org(org)
    supervisor._envelope(slug, "boss", "n", via="steer")
    org = store.load_org(slug)
    rows = supervisor.delivering_mail(org, "boss")
    assert rows and rows[0]["delivering"] is True
    d = rows[0]["delivery"]
    assert d["mode"] == "steer" and d["via"] == "steer" and d["attempt"] == 1 and d["at"]
    assert "ev" in rows[0] and "delivery" not in rows[0]["ev"]
    wire = events.wire_row(rows[0], public=False)
    assert wire["ev"]["body"] == "in flight" and wire["delivery"] == d


check("delivering · {mode, via, attempt, at} beside the row, never inside ev", _delivering)


# ══════════════════════════════════════════════════════════════════════════ §5
print("\n§5  wire_segments projection")


def _wire_segments():
    slug = fresh()
    ev_a = events.mint("access.scope_requested", actor_of("kid"),
                       {"kind": "scope_request", "org": slug, "id": "s1", "node": "kid"},
                       items=["x"], reason="r",
                       wanted={"folders": [{"path": "SENTINEL-PATH", "mode": "rw"}],
                               "tools": {"bash": None, "web": None, "edit": None,
                                         "subagents": None, "mcp": None},
                               "permission_mode": None, "org_visibility": None})
    segs = [{"kind": "mail", "rows": [{"id": "m", "from": "kid", "kind": "request",
                                       "body": "b", "at": "t", "ev": ev_a},
                                      {"id": "bad", "from": "@system", "kind": "message",
                                       "body": "b", "at": "t", "ev_raw": {"v": 9},
                                       "ev_error": {"code": "unknown_version", "path": "v",
                                                    "expected": "≤1"}}]},
            {"kind": "state", "event": events.mint("context.command", actor_of(USER),
                                                   node_ref(slug, "boss"), text="/compact"),
             "text": "/compact"},
            {"kind": "text", "text": "t"}]
    adm = events.wire_segments(segs, public=True)
    flat = json.dumps(adm)
    assert "SENTINEL-PATH" not in flat and '"ev"' not in flat and "ev_raw" not in flat
    assert adm[0]["rows"][0]["ev_public"]["projection"] == "public"
    assert adm[0]["rows"][1]["ev_error"] == {"code": "unknown_version"}
    assert adm[1]["event_public"]["variant"] == "context.command" and "event" not in adm[1]
    priv = events.wire_segments(segs, public=False)
    assert priv[0]["rows"][0]["ev"]["wanted"]["folders"][0]["path"] == "SENTINEL-PATH"
    assert priv[1]["event"]["variant"] == "context.command"
    assert events.wire_segments(None, public=True) is None


check("wire_segments · public withholds ev/ev_raw and private fields, {code} errors; "
      "operator keeps full events", _wire_segments)


# ══════════════════════════════════════════════════════════════════════════ §6
print("\n§6  generated TS carries the segment/delivery contract")


def _ts():
    ts = events.emit_typescript()
    for needle in ("export type Segment =", "export type PublicSegment =",
                   "export interface Delivery", 'kind: "drive"', "export interface WireMailRow",
                   "export interface PublicWireMailRow", "ev_public?: PublicEvent"):
        assert needle in ts, needle


check("emit · Segment / PublicSegment / Delivery / wire row types present", _ts)

print("\n" + "═" * 70)
print(f"{PASSED} checks passed, {len(FAILED)} failed")
for f in FAILED:
    print("\nFAIL:", f)
sys.exit(1 if FAILED else 0)
