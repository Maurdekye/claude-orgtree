"""Step 3 — the producers, family by family: every migrated producer mints its typed
event and the row body is BYTE-IDENTICAL to what the pre-typed producer wrote (B4).

The oracles below are the OLD producer code, copied verbatim at the moment each site
was migrated (breadcrumbs: "capture before editing each site"). They are the parity
witness: a renderer that drifts from the old text fails here, not in the field.

    §M  family monitor — the watchdog fire (Org.watchdog_fire, typed path via
        supervisor._wd_fire) and the went-quiet alert (supervisor._wd_alert)

Hermetic: throwaway ORGTREE_DATA set BEFORE any orgtree import; `send_message` is
patched at the seam the fire path calls so no agent process is ever started.

    python backend/tests/test_events_producers.py
"""
from __future__ import annotations

import datetime as _dtm
import os
import sys
import tempfile
import traceback
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-evprod-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = _TMP

from orgtree import events, store                                # noqa: E402
from orgtree import supervisor as S                              # noqa: E402
from orgtree.ledger import USER, LedgerError, Org                # noqa: E402

assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT

PASS = 0
FAIL: list[tuple[str, str]] = []
_n = [0]


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


class Quiet:
    """Patch `S.send_message` — the seam the fire/alert paths drive through — so
    nothing starts a turn; records what would have been woken."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def __enter__(self):
        self._orig = S.send_message

        def spy(slug, nid, text, wake=False, **kw):
            self.calls.append((nid, bool(wake)))
        S.send_message = spy                                     # type: ignore
        return self

    def __exit__(self, *a):
        S.send_message = self._orig                              # type: ignore


def rig(*, once: bool = False, kind: str = "file") -> tuple[str, Org, str, dict[str, Any]]:
    _n[0] += 1
    slug = f"evprod{_n[0]}"
    o = Org.create(slug, dirs=[_TMP])
    o.hire(USER, None, "opus", 20, "boss")
    tgt = os.path.join(_TMP, f"{slug}.log")
    with open(tgt, "w", encoding="utf-8") as f:
        f.write("start\n")
    if kind == "file":
        r = o.watchdog_create("boss", "build-watch", "file", tgt, pattern="BOOM",
                              interval_s=15, once=once)
    else:
        r = o.watchdog_create("boss", "build-watch", "command", "echo R", "R", 15,
                              False, None, once)
    store.save_org(o)
    return slug, o, str(r["id"]), {"target": tgt}


def last_row(slug: str) -> dict[str, Any]:
    o = store.load_org(slug)
    return dict(o.d["mail"]["boss"][-1])


def decoded(row: dict[str, Any]) -> dict[str, Any]:
    d = events.decode(row.get("ev"), row)
    assert d["status"] == "ok", d
    return d["ev"]


# =========================================================================== oracles
# ⚠ VERBATIM copies of the pre-typed producers (supervisor.py @ f91e3ff). Do not
# "clean up": the point is that they are the old code.
_OLD_NOTE = (
    "\n\n— This was a ONE-SHOT dog: it fired once and has REMOVED ITSELF. "
    "It is gone from your list and will not fire again. Nothing is wrong "
    "and you need not remove it. If you want to watch for this again, "
    "arm a new one.")


def old_fire_body(name: str, lines: list[str], prefix: str, one_shot: bool) -> str:
    body = (f"[WATCHDOG {name}]{prefix} {len(lines)} event(s):\n"
            + "\n".join(x[:500] for x in lines[:20])
            + (f"\n… {len(lines) - 20} more" if len(lines) > 20 else ""))
    if one_shot:
        body = body[:8000 - len(_OLD_NOTE)].rstrip() + _OLD_NOTE
    return body[:8000]


def old_alert_body(w: dict[str, Any], lost: dict[str, Any]) -> str:
    tgt = str(w.get("target") or "")
    kind = str(w.get("kind") or "")
    quiet, since = S._wd_stale_ok(w)
    age = S._wd_age_s(w.get("at"))
    facts = [f"watching   : {kind} · {tgt}",
             f"armed      : {S._wd_hours(age)} ago" if age is not None
             else "armed      : (unknown)",
             f"checks run : {int(w.get('checks_run') or 0)}"
             f"  ·  fired so far: {int(w.get('fired') or 0)}",
             f"silent for : {quiet} consecutive checks"
             + (f", {S._wd_hours(since)}" if since is not None else ""),
             f"last check : {w.get('last_check') or '(never)'}"]
    if kind == "file":
        try:
            st = os.stat(tgt)
            facts.append(f"the file   : {st.st_size} bytes, last written "
                         + _dtm.datetime.fromtimestamp(
                             st.st_mtime, _dtm.timezone.utc)
                         .isoformat(timespec="seconds").replace("+00:00", "Z"))
        except OSError as e:
            facts.append(f"the file   : CANNOT BE READ — {e.strerror or e}")
    if w.get("last_output"):
        facts.append(f"it sees    : {str(w['last_output'])[:300]!r}")
    return (f"[WATCHDOG {w.get('name')}] ⚠ {lost['headline'].upper()}\n\n"
            + "\n".join(facts)
            + f"\n\n{lost['advice']}\n\n"
            + ("⚠ This is about the THING BEING WATCHED, not about orgtree. "
               "Restarts and deploys do not produce this message: the counter "
               "above only advances on checks that actually ran (D-176)."))


# ============================================================ §M family monitor
print("\n§M · monitor — watchdog fire and went-quiet alert")


def _fire_persistent():
    slug, o, wid, _ = rig()
    with Quiet() as q:
        S._wd_fire(slug, wid, "build-watch", ["BOOM one", "BOOM two"])
    assert q.calls == [("boss", True)], q.calls
    row = last_row(slug)
    assert row["body"] == old_fire_body("build-watch", ["BOOM one", "BOOM two"], "", False)
    ev = decoded(row)
    assert ev["variant"] == "monitor.watchdog_fired", ev
    assert ev["actor"] == {"kind": "watchdog", "id": wid}, ev["actor"]
    assert ev["object"] == {"kind": "watchdog", "org": slug, "id": wid,
                            "name": "build-watch", "owner": "boss"}, ev["object"]
    assert ev["lines"] == ["BOOM one", "BOOM two"] and ev["count"] == 2
    assert ev["once"] is False
    assert row["body"] == events.render_agent(ev), "body is the frozen rendering"
    assert "REMOVED ITSELF" not in row["body"]
    # the archive copy carries the same row
    log = store.load_org(slug).d["mail_log"]["boss"][-1]
    assert log["ev"] == row["ev"] and log["body"] == row["body"]


check("fire · persistent dog: body == old producer text; ev monitor.watchdog_fired "
      "with WatchdogRef, lines, count, once=False; archive copy identical",
      _fire_persistent)


def _fire_one_shot_long():
    """25 lines of 600 chars: exercises the per-line 500 cap, the 20-line window,
    the '… N more' tail AND the one-shot note surviving an over-long body."""
    slug, o, wid, _ = rig(once=True)
    lines = [f"L{i:02d} " + "x" * 600 for i in range(25)]
    with Quiet():
        S._wd_fire(slug, wid, "build-watch", lines, prefix=" STREAM EXITED —")
    row = last_row(slug)
    want = old_fire_body("build-watch", lines, " STREAM EXITED —", True)
    assert len(want) <= 8000 and "REMOVED ITSELF" in want, "oracle sanity"
    assert row["body"] == want, (row["body"][-200:], want[-200:])
    ev = decoded(row)
    assert ev["once"] is True and ev["count"] == 25 and ev["prefix"] == " STREAM EXITED —"
    assert ev["lines"] == lines, "the event keeps EVERY line untruncated; only the text is capped"
    assert row["body"] == events.render_agent(ev)
    o2 = store.load_org(slug)
    assert not any(w["id"] == wid for w in o2.d.get("watchdogs") or []), "the one-shot spent itself"


check("fire · one-shot dog, 25×600-char lines: body == old text incl. the self-removal "
      "note within 8000; ev.once True, all lines kept on the event",
      _fire_one_shot_long)


def _fire_raw_path_unchanged():
    """The positional-body path (ledger tests, pre-typed callers) is still the old
    behaviour, untyped: no ev, note appended by the shared helper."""
    slug, o, wid, _ = rig(once=True)
    o = store.load_org(slug)
    o.watchdog_fire(wid, "R", "X" * 12000)
    row = dict(o.d["mail"]["boss"][-1])
    assert "ev" not in row
    assert len(row["body"]) <= 8000 and row["body"].endswith(_OLD_NOTE)
    assert row["body"] == ("X" * 12000)[:8000 - len(_OLD_NOTE)].rstrip() + _OLD_NOTE
    assert events.decode(None, row)["status"] == "legacy"


check("fire · raw-body path: no ev (legacy row), note rule byte-identical",
      _fire_raw_path_unchanged)


def _fire_empty_refused():
    slug, o, wid, _ = rig()
    o = store.load_org(slug)
    try:
        o.watchdog_fire(wid, "R", lines=[])
    except ValueError as e:
        assert not isinstance(e, LedgerError), "must NOT be the swallowed LedgerError"
    else:
        raise AssertionError("an empty typed fire was accepted")


check("fire · typed path with no lines is a ValueError (not the LedgerError the "
      "engine swallows as 'dog changed')", _fire_empty_refused)


def _alert():
    slug, o, wid, extra = rig()
    lost = {"why": "quiet:file", "headline": "the file has gone quiet",
            "advice": "Look at the producer, not at orgtree.", "pause": False}
    o = store.load_org(slug)
    w = o._watchdog(wid)
    w["checks_run"] = 168
    w["last_check"] = "2026-09-06T21:20:00Z"
    w["last_output"] = "tail of what it saw"
    w["high_water"] = {"quiet": 7, "alive_at": "2026-09-06T20:00:00Z"}
    store.save_org(o)
    # oracle computed from the SAME persisted dog state the producer reads
    w_read = dict(store.load_org(slug)._watchdog(wid))
    want = old_alert_body(w_read, lost)
    with Quiet() as q:
        S._wd_alert(slug, wid, lost)
    assert q.calls == [("boss", True)], q.calls
    row = last_row(slug)
    assert row["body"] == want, (row["body"], want)
    ev = decoded(row)
    assert ev["variant"] == "monitor.watchdog_quiet"
    assert ev["object"]["id"] == wid and ev["object"]["owner"] == "boss"
    assert ev["headline"] == lost["headline"] and ev["advice"] == lost["advice"]
    assert any(f.startswith("checks run : 168") for f in ev["facts"]), ev["facts"]
    assert any(f.startswith("the file   : ") for f in ev["facts"]), ev["facts"]
    assert any("it sees    : " in f for f in ev["facts"]), ev["facts"]
    assert row["body"] == events.render_agent(ev)
    # the dog was NOT spent and `fired` did not move (an alert is not a fire)
    w2 = store.load_org(slug)._watchdog(wid)
    assert int(w2.get("fired") or 0) == 0 and w2["alerted_why"] == "quiet:file"


check("alert · went-quiet: body == old wd_alert_body text (file facts, it-sees, "
      "checks); ev monitor.watchdog_quiet; fired counter untouched", _alert)


def _alert_wrong_variant_refused():
    slug, o, wid, _ = rig()
    o = store.load_org(slug)
    w = o._watchdog(wid)
    fired = events.mint("monitor.watchdog_fired", {"kind": "watchdog", "id": wid},
                        o._watchdog_ref(w), prefix="", lines=["x"], count=1, once=False)
    try:
        o.watchdog_alert(wid, ev=fired)
    except LedgerError:
        pass
    else:
        raise AssertionError("watchdog_alert accepted a watchdog_fired event")
    assert not (o.d.get("mail") or {}).get("boss"), "nothing was posted"


check("alert · refuses any event but monitor.watchdog_quiet (control: nothing posted)",
      _alert_wrong_variant_refused)


def _public_projection():
    slug, o, wid, _ = rig()
    with Quiet():
        S._wd_fire(slug, wid, "build-watch", ["BOOM"])
    row = last_row(slug)
    ev = decoded(row)
    pub = events.public_event(ev)
    assert pub["projection"] == "public" and pub["variant"] == "monitor.watchdog_fired"
    assert "org" not in pub["object"], "the org slug is internal"
    assert pub["lines"] == ["BOOM"] and pub["once"] is False
    events.validate_public_event(pub)


check("public · a fired event projects to a PublicEvent (org withheld, lines kept)",
      _public_projection)


# =========================================================================== summary
print()
for label, tb in FAIL:
    print(f"── FAIL {label}\n{tb}")
print("══════════════════════════════════════════════════════════════════════")
print(f"{PASS} checks passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
