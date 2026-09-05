"""A mail reference addresses the box the mail is ACTUALLY in.

⚠ THE DEFECT THIS PINS (Astra, 2026-09-05). The user's Sent folder is not a
box — it is a MIRROR. `post_mail` appends the delivered entry to the
recipient's archive and then appends a copy to `user_outbox`, so every row
there is a message that lives somewhere else. Stamping those rows from the box
being READ produced `@mail:<org>/user/<id>` for a mail sitting in an agent's
box: a reference that opens the wrong place, and — because the three box
families mint ids independently — one that can open a DIFFERENT REAL MESSAGE
that happens to share the id.

Everything here goes through the installed HTTP routes and reads the answer
back the same way. `supervisor.send_message` is stubbed to record, because
nothing in this file may launch a model.

Run: python backend/tests/test_mail_refs.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import traceback

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys._utf8_wrapped = True

_TMP = tempfile.mkdtemp(prefix="orgtree-mailrefs-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ.pop("ORGTREE_WARM", None)
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient                          # noqa: E402
from orgtree import api, store, supervisor                         # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

# ⚠ THE ROOT IS PROVED, not assumed: `store.DATA_ROOT` binds at import time, so
# a suite that imported it before setting the variable would write into the
# operator's live data.
assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT

supervisor.send_message = lambda *a, **k: {"accepted": True, "queued": 0}
client = TestClient(api.app)

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


def fresh_org() -> str:
    _n[0] += 1
    org = store.create_org(f"mailrefs-{_n[0]}", [])
    org.hire(USER, None, "opus", 60, "boss")
    store.save_org(org)
    return org.d["slug"]


def user_inbox(slug: str) -> dict:
    r = client.get(f"/api/orgs/{slug}/inbox")
    assert r.status_code == 200, r.text
    return r.json()


def a_user_send_is_addressed_where_it_was_DELIVERED():
    slug = fresh_org()
    org = store.load_org(slug)
    sent = org.post_mail(USER, "boss", "look at this", kind="message")
    store.save_org(org)
    mid = sent["id"]

    box = user_inbox(slug)
    row = next(m for m in box["sent"] if m["id"] == mid)
    assert row.get("ref") == f"@mail:{slug}/node/boss/{mid}", (
        "the Sent row is addressed to the box being READ rather than the box "
        f"the mail is IN: {row.get('ref')!r}")
    # …and the same message, read from the recipient's own box, agrees
    r = client.get(f"/api/orgs/{slug}/nodes/boss/inbox")
    assert r.status_code == 200, r.text
    # ⚠ PENDING, NOT DELIVERED. An agent's mail waits until its next turn, so
    # a freshly sent message is in the recipient's PENDING list — reading
    # only `delivered` found nothing and failed for a reason that had
    # nothing to do with references.
    got = r.json()
    theirs = next(m for m in got["pending"] + got["delivered"]
                  if m["id"] == mid)
    assert theirs.get("ref") == row.get("ref"), \
        "the sender and the recipient disagree about where one mail is"


check("a user send is addressed where it was DELIVERED, not where it is read",
      a_user_send_is_addressed_where_it_was_DELIVERED)


def a_colliding_id_in_an_unrelated_box_does_not_win():
    """⚠ THE REASON THE BOX MATTERS AT ALL. The three box families mint ids
    independently, so the same id can exist in two of them — and the wrong
    address does not fail, it opens the WRONG REAL MESSAGE."""
    slug = fresh_org()
    org = store.load_org(slug)
    sent = org.post_mail(USER, "boss", "the one I sent", kind="message")
    mid = sent["id"]
    # a DIFFERENT message, in the user's own inbox, wearing the same id
    org.d.setdefault("user_inbox", []).append({
        "id": mid, "from": "boss", "at": sent.get("at") or "2026-09-05T10:00:00Z",
        "kind": "message", "body": "a different message entirely",
    })
    store.save_org(org)

    box = user_inbox(slug)
    mine = next(m for m in box["sent"] if m["id"] == mid)
    theirs = next(m for m in box["pending"] if m["id"] == mid)
    assert mine["body"] != theirs["body"], \
        "positive control: these really are two different messages"
    assert mine.get("ref") == f"@mail:{slug}/node/boss/{mid}"
    assert theirs.get("ref") == f"@mail:{slug}/user/{mid}"
    assert mine["ref"] != theirs["ref"], \
        "two different messages sharing an id got the SAME reference — one of " \
        "them opens the other"


check("a colliding id in an unrelated box does not win",
      a_colliding_id_in_an_unrelated_box_does_not_win)


def a_send_with_no_local_box_carries_no_reference():
    """A reference to a mail that is not in a box we can open is worse than no
    reference — the row still renders, it simply is not a link."""
    slug = fresh_org()
    org = store.load_org(slug)
    # a row whose destination was not recorded: nothing addressable, so nothing
    # is claimed. The frontend reads the same emptiness as "unreachable" and
    # keeps plain attachment chips.
    org.d.setdefault("user_outbox", []).append({
        "id": "ab12cd34", "from": USER, "to": "",
        "at": "2026-09-05T10:00:00Z", "kind": "message", "body": "nowhere",
    })
    store.save_org(org)
    row = next(m for m in user_inbox(slug)["sent"] if m["id"] == "ab12cd34")
    assert "ref" not in row, \
        f"a box we cannot open was given a reference anyway: {row.get('ref')!r}"
    # ⚠ CONTROL, AND A CORRECTION TO MY OWN FIRST GUESS. I wrote this case
    # expecting an outbound `@net:` send to be unaddressable, and the suite
    # corrected me: `post_mail` logs an external send in the ORG INBOX, so it
    # has a local id after all and the org reference opens the real row.
    org = store.load_org(slug)
    org.d.setdefault("user_outbox", []).append({
        "id": "ef56ab78", "from": USER, "to": "@net:somewhere",
        "at": "2026-09-05T10:01:00Z", "kind": "message", "body": "out there",
    })
    store.save_org(org)
    orgrow = next(m for m in user_inbox(slug)["sent"] if m["id"] == "ef56ab78")
    assert orgrow.get("ref") == f"@mail:{slug}/org/ef56ab78", orgrow.get("ref")


check("a send with no local box carries no reference, and a local one does",
      a_send_with_no_local_box_carries_no_reference)


print(f"\n{PASSED} passed, {len(FAILED)} failed")
for f in FAILED:
    print("\nFAIL", f)
sys.exit(1 if FAILED else 0)
