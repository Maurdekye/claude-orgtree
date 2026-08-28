"""Notices arrive in the user's mailbox ALREADY READ (user, 2026-08-28).

    python backend/tests/test_notice_read.py      (no pytest; plain asserts)

    "do not include system notices in the unread user mail count. in fact,
     don't even mark them as unread: they should arrive in the mailbox as
     already read."
    "in fact any notice arrives to the user mailbox as already read. but only
     system notices should be given this narrower height adjustment."

TWO PREDICATES, AND THEY ARE NOT THE SAME ONE.
  (1) READ ON ARRIVAL + out of the count: `kind == "notice"`, whatever its
      source. Owned here.
  (2) SHORTER ROW: a system notice only — `kind == "notice"` AND
      `from == "@system"`. Purely cosmetic, owned by
      frontend/tests/sysnotice.test.tsx.
Collapsing them would either shrink an agent's notice (harmless) or mark a
non-notice read (NOT harmless). §2 is the whole reason this file is careful.

⚠ THE ASYMMETRY THIS SUITE IS BUILT AROUND. Marking something read HIDES it:
it leaves the unread count, the tab title, the pip and the folder badge all
at once, and nothing ever draws the user back to it. So the interesting
direction is not "is the notice read" — it is "is EVERYTHING ELSE still
unread". §2 spends four legs on that and only one on the happy path, on
purpose. In particular the ledger sends the user `decision` mail from
@system — a Fable limit exhausted, agents halted or dissolved — which is
system-generated and must NOT be pre-read.

WHY AT THE SOURCE. `user_inbox` IS the unread set (the read endpoint's whole
job is moving an entry out of it into `user_mail_log`). Six places derive
"how much is unread" from membership of that one list, so a notice that never
enters it is excluded from all six with no filter to keep in step anywhere.
§4 is the guard that keeps it that way.
"""

import os
import re
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-noticeread-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree.ledger import Org, SYSTEM, USER                       # noqa: E402

PASS = 0
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "orgtree")


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def mkorg(name):
    org = Org.create(name)
    org.hire(USER, None, "opus", 20, "top")
    return org


def entry(**over):
    e = {"id": over.pop("id", "x"), "from": SYSTEM, "kind": "notice",
         "at": "2026-08-28T01:00:00Z", "body": "b"}
    e.update(over)
    return e


def unread(org):
    return org.d.get("user_inbox", [])


def archive(org):
    return org.d.get("user_mail_log", [])


def main():
    # ------------------------------------------------- §1 read on arrival
    print("§1 a notice lands on the READ side of the line:")
    org = mkorg("notice read")
    org.to_user_inbox(entry(id="n1", body="a turn failed"))
    check("it is NOT in the unread set",
          lambda: eq([m["id"] for m in unread(org)], [], "user_inbox"))
    check("it IS in the read archive, intact",
          lambda: eq([m["id"] for m in archive(org)], ["n1"], "user_mail_log"))
    check("…so the unread count never saw it",
          lambda: eq(org.tree()["user_inbox_count"], 0, "user_inbox_count"))
    # an AGENT's notice takes the same route — predicate (1) is the KIND, not
    # the sender. (Today api.py refuses notices addressed to the user, so this
    # is the ledger holding the rule for whenever that changes.)
    org.to_user_inbox(entry(id="n2", **{"from": "top"}))
    check("an AGENT's notice is read on arrival too — the kind decides, "
          "not the sender",
          lambda: eq([m["id"] for m in archive(org)], ["n1", "n2"], "archive"))
    check("…and it still did not touch the unread count",
          lambda: eq(org.tree()["user_inbox_count"], 0, "user_inbox_count"))

    # ------------------------- §2 EVERYTHING ELSE IS STILL UNREAD (the point)
    print("\n§2 nothing else is pre-read — the direction that HIDES mail:")
    o2 = mkorg("notice safety")
    # the dangerous near-miss: system-generated, but NOT a notice. This is the
    # mail reporting that agents were halted or dissolved.
    o2.to_user_inbox(entry(id="d1", kind="decision",
                           body="Weekly Fable usage limit exhausted"))
    check("a @system DECISION stays UNREAD — system-generated is not the test",
          lambda: eq([m["id"] for m in unread(o2)], ["d1"], "user_inbox"))
    o2.to_user_inbox(entry(id="r1", kind="request", **{"from": "top"}))
    check("an agent's audience REQUEST stays unread",
          lambda: eq([m["id"] for m in unread(o2)], ["d1", "r1"], "user_inbox"))
    o2.post_mail("top", "user", "please look at this", "message")
    check("ordinary agent mail to the user stays unread",
          lambda: eq(len(unread(o2)), 3, "user_inbox length"))
    check("…and the count agrees with the list, at 3",
          lambda: eq(o2.tree()["user_inbox_count"], 3, "user_inbox_count"))
    check("the archive stayed EMPTY — nothing was swept into it",
          lambda: eq(archive(o2), [], "user_mail_log"))
    # urgent mail (D-169) is untouched by any of this
    o2.post_mail("top", "user", "the deploy is wedged", "message",
                 urgent=True, urgent_reason="prod is down")
    check("D-169 urgent mail still counts, and still counts as urgent",
          lambda: eq((o2.tree()["user_inbox_count"], o2.tree()["urgent_unread"]),
                     (4, 1), "(unread, urgent_unread)"))

    # ------------------------------------------- §3 the archive's invariants
    print("\n§3 the archive keeps its own shape:")
    o3 = mkorg("notice archive")
    for i, at in enumerate(["2026-08-28T03:00:00Z", "2026-08-28T01:00:00Z",
                            "2026-08-28T02:00:00Z"]):
        o3.to_user_inbox(entry(id=f"a{i}", at=at))
    check("CHRONOLOGICAL, not arrival-ordered (the reader renders by position)",
          lambda: eq([m["at"] for m in archive(o3)],
                     ["2026-08-28T01:00:00Z", "2026-08-28T02:00:00Z",
                      "2026-08-28T03:00:00Z"], "archive order"))
    o4 = mkorg("notice cap")
    for i in range(140):
        o4.to_user_inbox(entry(id=f"c{i:03d}", at=f"2026-08-28T{i // 60:02d}:{i % 60:02d}:00Z"))
    check("BOUNDED at 100 — a chatty org cannot grow it without limit",
          lambda: eq(len(archive(o4)), 100, "archive length"))
    check("…and it is the OLDEST that fall off, not the newest",
          lambda: eq(archive(o4)[-1]["id"], "c139", "newest kept"))

    # --------------------------------------------------- §4 the source guard
    print("\n§4 nothing writes to the mailbox behind the helper's back:")
    # THE DRIFT GUARD. The whole design rests on `user_inbox` containing only
    # unread mail, which holds only while every writer goes through
    # to_user_inbox(). A new direct append would put a notice back in the
    # unread set and no other check in this file would notice.
    direct = []
    for fn in ("ledger.py", "supervisor.py", "sandbox.py", "api.py"):
        p = os.path.join(SRC, fn)
        src = open(p, encoding="utf-8").read().replace("\r\n", "\n")
        for i, line in enumerate(src.split("\n"), 1):
            if re.search(r'setdefault\("user_inbox", \[\]\)\.append', line):
                direct.append(f"{fn}:{i}")
    check("exactly ONE direct append exists — inside to_user_inbox itself",
          lambda: eq(len(direct), 1, f"direct appends found at {direct}"))
    check("…and it is in ledger.py, where the helper lives",
          lambda: eq(direct[0].split(":")[0], "ledger.py", "its file"))
    # ANTI-VACUITY: the scanner must actually be reading these files and
    # finding the shape it looks for — otherwise "0 found" would pass forever
    # even if the helper itself were deleted.
    check("the scanner really does match that shape (it found the one)",
          lambda: eq(bool(direct), True, "scanner found nothing at all"))

    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
