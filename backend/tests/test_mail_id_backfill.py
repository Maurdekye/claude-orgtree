"""Mail ids: minted by the writers, repaired ONCE — never on every load.

Run directly::

    python backend/tests/test_mail_id_backfill.py

WHY THIS SUITE EXISTS
---------------------
`Org.__init__` used to walk `user_inbox`, `mail` and `mail_log` on EVERY
construction, doing `setdefault("id", uuid4().hex[:12])`. Ids arrived after the
first mail did, so pre-id entries really do need repairing — they render with
no retraction ✕ and 404 the DELETE with a false excuse — but doing it per load
cost more than it looked, in two directions.

READ. `mail_log` is 4,792,199 B, 44.4% of the live 10.8 MB document (2,256
entries, MEASURED 2026-09-03). Touching it forced the whole section on every
`load_org`.

    JSON backend, live document, the WALK ISOLATED from the parse,
    median of 25:
        user_inbox + mail + mail_log (old)   3.04 ms   2,310 entries
        user_inbox + mail            (now)   0.07 ms      50 entries
        json.loads of the same document     37.68 ms

⚠ AND THAT JSON NUMBER IS THE TRAP, TWICE OVER. ~3 ms is a rounding error
against a 38 ms parse — and it is smaller than that parse's own jitter, so an
END-TO-END `load_org` comparison on JSON shows nothing at all. The walk had to
be timed in isolation to be seen. Measuring only this backend, only end to
end, would have retired the change as unjustified. The
SQLite store loads sections lazily and never touches `mail_log` on a plain
read — there the same walk was **35.3 ms on a 12 ms load**, single-handedly
cancelling the laziness (measured by `sqlite-review`, 2026-09-03, both
backends interleaved). The same edit is worth ~9% on one backend and ~4x on
the other. MEASURE A CHANGE HERE ON BOTH.

WRITE, which is the half nobody sees. `setdefault` evaluates `uuid4()`
eagerly, so an id-less entry got a DIFFERENT id on every construction. Under
compare-on-save that makes the section differ from its snapshot every time, so
every load+save rewrote the entire 4.4 MB archive with no application change
at all. An id-less entry is therefore not a cosmetic gap; it is permanent
write amplification.

WHAT IS UNDER TEST
------------------
    §1  the door mints the id — the writer fix, without which the migration
        is a treadmill
    §2  legacy entries are repaired, and the marker records it
    §3  it is genuinely ONCE — a repaired document is not touched again
    §4  …and "once" means the walk does not happen, not that it is cheap

⚠ §1 IS THE LOAD-BEARING ONE. AUDITED 2026-09-03: of fourteen `to_user_inbox`
call sites, FOUR passed an entry with no id (two Fable-limit notices, the
forwarded audience request, the weekly-limit decision). So the old walk was
still doing real work and could not simply be deleted. Fixing those four call
sites would have left the fifth to whoever writes it next; the id is minted at
the one door instead, which cannot be forgotten.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RIG = tempfile.mkdtemp(prefix="orgtree-mail-ids-")
os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = os.path.join(RIG, "home")
os.environ["USERPROFILE"] = os.path.join(RIG, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import store, supervisor as S      # noqa: E402
from orgtree.ledger import SYSTEM, USER, Org, now   # noqa: E402

S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None

PASS = 0
FAIL = 0


def check(label: str, fn: Any) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as exc:                                    # noqa: BLE001
        FAIL += 1
        print(f"  FAIL    {label}: {exc}")
        import traceback
        traceback.print_exc()


LEGACY_N = 0


def legacy_doc() -> dict[str, Any]:
    """An org document written before mail entries carried ids."""
    global LEGACY_N
    LEGACY_N += 1
    org = store.create_org(f"zz-mail-ids-{LEGACY_N}")
    org.hire(USER, None, "haiku", 4, "boss")
    d: dict[str, Any] = dict(org.d)
    d["user_inbox"] = [{"from": SYSTEM, "kind": "request", "at": now(),
                        "body": "no id here"}]
    d["mail"] = {"boss": [{"from": USER, "kind": "message", "at": now(),
                           "body": "pending, no id"}]}
    d["mail_log"] = {"boss": [{"from": USER, "kind": "message", "at": now(),
                               "body": "archived, no id"}]}
    d.setdefault("_migrations", {}).pop("mail_log_ids", None)
    return d


def every_entry(d: dict[str, Any]) -> list[dict[str, Any]]:
    out = list(d.get("user_inbox") or [])
    for box in ("mail", "mail_log"):
        for ms in (d.get(box) or {}).values():
            out.extend(ms)
    return out


# ── §1 · the door mints the id ───────────────────────────────────────────────
def to_user_inbox_mints_an_id_the_caller_omitted() -> None:
    """Without this the migration is a treadmill: four real call sites pass no
    id, so entries written tomorrow would need repairing again."""
    org = store.create_org("zz-mail-door")
    # the UNREAD path…
    e = org.to_user_inbox({"from": SYSTEM, "kind": "request", "at": now(),
                           "body": "no id supplied"})
    assert e.get("id"), "to_user_inbox accepted an entry with no id"
    assert org.d["user_inbox"][-1]["id"] == e["id"]
    # …and the NOTICE path, which lands in a different list and would
    # otherwise be an id-less row nobody looked at
    n = org.to_user_inbox({"from": SYSTEM, "kind": "notice", "at": now(),
                           "body": "notice, no id supplied"})
    assert n.get("id"), "a notice reached user_mail_log with no id"
    assert org.d["user_mail_log"][-1]["id"] == n["id"]
    # an id the caller DID supply is never overwritten — retraction and read
    # tracking both key on it, so re-minting would orphan the reference
    keep = org.to_user_inbox({"id": "keepme00", "from": SYSTEM,
                              "kind": "request", "at": now(), "body": "mine"})
    assert keep["id"] == "keepme00", keep


# ── §2 · legacy entries are repaired, once, and it is recorded ──────────────
def a_legacy_document_is_repaired_and_the_marker_records_it() -> None:
    d = legacy_doc()
    assert all(not e.get("id") for e in every_entry(d)), "fixture already had ids"
    org = Org(d)
    got = every_entry(org.d)
    assert len(got) == 3, got
    assert all(e.get("id") for e in got), (
        "a pre-id entry was left without one — it renders with no retraction "
        "✕ and 404s the DELETE with a false excuse")
    # the marker counts only what IT repaired: `user_inbox` and `mail` are
    # walked unconditionally above it (they are small), so exactly one of the
    # three entries — the archive one — is this migration's work
    mark = org.d["_migrations"]["mail_log_ids"]
    assert mark["repaired"] == 1, mark
    assert mark.get("at"), mark


# ── §3 · genuinely once ─────────────────────────────────────────────────────
def a_repaired_document_is_not_touched_again() -> None:
    import json
    org = Org(legacy_doc())
    first = json.dumps(org.d, sort_keys=True)
    again = Org(org.d)
    assert json.dumps(again.d, sort_keys=True) == first, (
        "a second construction changed the document — the migration is not "
        "idempotent, and under compare-on-save that rewrites the archive")


# ── §4 · "once" means it does not walk, not that walking is cheap ───────────
def a_marked_document_is_not_walked_at_all() -> None:
    """The cost property, and the reason §3 is not enough on its own.

    A migration that re-walked 4.4 MB and happened to change nothing would
    satisfy §3 perfectly while costing exactly what the old code cost. So this
    plants an id-less entry in an ALREADY-MARKED document and requires that it
    come back untouched: the only way that holds is if the walk never ran.

    ⚠ That is also a real behavioural statement, not a trick: once the marker
    is set, an entry with no id stays that way. It is correct because the door
    (§1) and the `mail`/`mail_log` writers all mint ids now, so no such entry
    can be created — and it is far better than paying 44% of the document on
    every load forever to defend against one that cannot exist.
    """
    d = legacy_doc()
    d["_migrations"]["mail_log_ids"] = {"at": now(), "repaired": 0}
    org = Org(d)
    log = [e for ms in (org.d.get("mail_log") or {}).values() for e in ms]
    assert log and all(not e.get("id") for e in log), (
        "a marked document had its ARCHIVE walked anyway — the marker gates "
        "the record but not the work, which is the whole cost this removes")
    # …while the cheap sections ARE still repaired every load, which is the
    # guarantee `test_ledger.py`'s "legacy node mail gets ids on load" pins
    cheap = list(org.d.get("user_inbox") or []) + [
        e for ms in (org.d.get("mail") or {}).values() for e in ms]
    assert cheap and all(e.get("id") for e in cheap), (
        "the small boxes stopped being repaired per load; that contract is "
        "cheap to honour and something already depends on it")


try:
    print("mail id backfill")
    check("§1 to_user_inbox mints an id the caller omitted",
          to_user_inbox_mints_an_id_the_caller_omitted)
    check("§2 a legacy document is repaired and the marker records it",
          a_legacy_document_is_repaired_and_the_marker_records_it)
    check("§3 a repaired document is not touched again",
          a_repaired_document_is_not_touched_again)
    check("§4 a marked document is not walked at all",
          a_marked_document_is_not_walked_at_all)
    print(f"\n{PASS} passed, {FAIL} FAILED")
finally:
    shutil.rmtree(RIG, ignore_errors=True)

sys.exit(1 if FAIL else 0)
