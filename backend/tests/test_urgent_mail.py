"""D-169 urgent mail — the ledger half: the gate, the pair, and the count.

    python backend/tests/test_urgent_mail.py      (no pytest; plain asserts)

An agent may tag user-bound mail urgent; the user's inbox then pulses the way
it does for an unanswered question, until the mail is read. This file owns the
server-side mechanics. The pip rule that consumes the count is a frontend
value and is tested in frontend/tests/urgentpip.test.tsx.

WHAT "READ" MEANS HERE, because the whole design hangs off it. `user_inbox`
IS the unread set: POST /inbox/read moves an entry out of it into
`user_mail_log`. So `urgent_unread` is derived from that one list and falls to
zero on exactly the read event — there is no second seen-stamp that could
leave the pulse stuck on after the mail was read. §4 is that property.

⚠ WHY EVERY MISUSE REFUSES RATHER THAN DEGRADING. A dropped urgent flag is the
WON'T-FIRE failure: the sender believes it raised the alarm, the user is never
interrupted, and nothing says so. That is strictly worse than an over-eager
alarm, which at least announces itself. So §2 pins that each bad call raises
instead of quietly posting ordinary mail — including the blank reason, which
is the D-168 shape (an abstention wired to the passing branch) pointed at a
human process: accept `reason=""` once and every call carries it within a
week.
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-urgent-test-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import store                                          # noqa: E402
from orgtree.ledger import LedgerError, Org, USER                  # noqa: E402
from orgtree.mcptool import TOOLS                                  # noqa: E402

PASS = 0
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def refuses(label, fn, must_say):
    """A refusal is only useful if it NAMES the fix — a bare 'invalid' leaves
    the agent to guess, and it will guess 'drop the flag'."""
    def _():
        try:
            fn()
        except LedgerError as e:
            assert must_say in str(e).lower(), (
                f"refused, but the message never mentions {must_say!r}: {e}")
            return
        raise AssertionError("accepted — this call must be refused")
    check(label, _)


def mkorg(name):
    org = Org.create(name)
    org.hire(USER, None, "opus", 20, "top")
    org.hire("top", "top", "haiku", 0, "kid", add_dirs=[],
             tools=dict(ALL_TOOLS), org_visibility="team", charter="test hire")
    return org


def urgent_rows(org):
    return [m for m in org.d.get("user_inbox", []) if m.get("urgent")]


def main():
    # ------------------------------------------------- §1 the happy path
    print("§1 an urgent mail carries the pair, and counts:")
    org = mkorg("urgent core")
    org.post_mail("top", "user", "ordinary news", "message")
    r = org.post_mail("top", "user", "the deploy is wedged", "message",
                      urgent=True, urgent_reason="  prod has been down 20m  ")
    check("the send reports the user inbox as its destination",
          lambda: (None if r["delivered"] == "user_inbox" and r.get("id")
                   else (_ for _ in ()).throw(AssertionError(r))))
    ue = urgent_rows(org)[0]
    check("the entry carries urgent=True",
          lambda: (None if ue["urgent"] is True
                   else (_ for _ in ()).throw(AssertionError(ue))))
    check("…and the reason, whitespace-stripped",
          lambda: (None if ue["urgent_reason"] == "prod has been down 20m"
                   else (_ for _ in ()).throw(AssertionError(ue))))
    # ANTI-VACUITY, the direction that matters: an ordinary mail must not
    # acquire the marker. Without this leg a writer that stamped `urgent` on
    # EVERY entry would pass every other check in this file.
    plain = [m for m in org.d["user_inbox"] if m["body"] == "ordinary news"][0]
    check("an ordinary mail carries NO urgent key at all (not urgent=False)",
          lambda: (None if "urgent" not in plain and "urgent_reason" not in plain
                   else (_ for _ in ()).throw(AssertionError(plain))))

    t = org.tree()
    check("tree(): urgent_unread counts the urgent one only",
          lambda: (None if t["urgent_unread"] == 1 and t["user_inbox_count"] == 2
                   else (_ for _ in ()).throw(AssertionError(
                       f"urgent={t['urgent_unread']} all={t['user_inbox_count']}"))))
    # THE PROJECTION LEG. tree() is built key by key and drops silently what it
    # does not name; the symptom is never a crash, it is the pip confidently
    # showing the ordinary unread count and never pulsing.
    check("tree(): the key is actually PRESENT in the projection",
          lambda: (None if "urgent_unread" in org.tree()
                   else (_ for _ in ()).throw(AssertionError(
                       "urgent_unread was dropped by the tree() key list"))))

    # ------------------------------------------------------ §2 the gate
    print("\n§2 every misuse refuses — none of them degrades quietly:")
    o2 = mkorg("urgent gate")
    refuses("a blank reason is refused, not stored as blank",
            lambda: o2.post_mail("top", "user", "b", "message",
                                 urgent=True, urgent_reason=""),
            "reason")
    refuses("…and so is a whitespace-only one (the same hole, spelled softly)",
            lambda: o2.post_mail("top", "user", "b", "message",
                                 urgent=True, urgent_reason="   \t  "),
            "reason")
    refuses("a reason WITHOUT the flag refuses, rather than silently "
            "posting ordinary mail",
            lambda: o2.post_mail("top", "user", "b", "message",
                                 urgent_reason="something broke"),
            "urgent=true")
    refuses("urgent to an AGENT refuses — urgency is about the user's inbox",
            lambda: o2.post_mail("top", "kid", "b", "message",
                                 urgent=True, urgent_reason="x"),
            "user")
    # …and the refusals wrote NOTHING. A gate that raises after appending
    # would leave a half-made row in the inbox and still look like a refusal
    # from the caller's side.
    check("every refusal above recorded no mail whatsoever",
          lambda: (None if not o2.d.get("user_inbox")
                   and not o2.d.get("mail", {}).get("kid")
                   else (_ for _ in ()).throw(AssertionError(
                       f"inbox={o2.d.get('user_inbox')} "
                       f"kid={o2.d.get('mail', {}).get('kid')}"))))

    # ------------------------------------------- §3 ordinary mail is untouched
    print("\n§3 the default is unchanged — nothing becomes urgent by accident:")
    o3 = mkorg("urgent default")
    o3.post_mail("top", "user", "plain", "message")
    o3.post_mail("top", "kid", "to an agent", "message")
    check("a send with no urgent argument leaves urgent_unread at 0",
          lambda: (None if o3.tree()["urgent_unread"] == 0
                   else (_ for _ in ()).throw(AssertionError(o3.tree()))))
    check("…while the ordinary unread count still counts it",
          lambda: (None if o3.tree()["user_inbox_count"] == 1
                   else (_ for _ in ()).throw(AssertionError(o3.tree()))))
    check("urgent=False with no reason is a normal send, not a refusal",
          lambda: o3.post_mail("top", "user", "plain2", "message",
                               urgent=False, urgent_reason=""))

    # ------------------------------------------------ §4 reading clears it
    print("\n§4 reading is what stops it — and reading means leaving user_inbox:")
    o4 = mkorg("urgent read")
    o4.post_mail("top", "user", "quiet", "message")
    o4.post_mail("top", "user", "loud", "message",
                 urgent=True, urgent_reason="the wall is on fire")
    slug = o4.d["slug"]
    store.save_org(o4)
    check("before reading: 1 urgent of 2 unread",
          lambda: (None if o4.tree()["urgent_unread"] == 1
                   else (_ for _ in ()).throw(AssertionError(o4.tree()))))

    # the read endpoint's own move, performed here on the doc: out of
    # user_inbox, into user_mail_log. (api.user_inbox_read is the caller; this
    # asserts the ledger-side property the pulse depends on.)
    org = store.load_org(slug)
    uid = [m for m in org.d["user_inbox"] if m.get("urgent")][0]["id"]
    keep = [m for m in org.d["user_inbox"] if m.get("id") != uid]
    read = [m for m in org.d["user_inbox"] if m.get("id") == uid]
    org.d["user_inbox"] = keep
    org.d.setdefault("user_mail_log", []).extend(read)
    store.save_org(org)

    after = store.load_org(slug).tree()
    check("reading the urgent one drops urgent_unread to 0",
          lambda: (None if after["urgent_unread"] == 0
                   else (_ for _ in ()).throw(AssertionError(after))))
    # ANTI-VACUITY: it must drop because THAT mail was read, not because the
    # whole inbox emptied. The quiet one is still unread and still counted.
    check("…while the ordinary unread mail beside it is still counted",
          lambda: (None if after["user_inbox_count"] == 1
                   else (_ for _ in ()).throw(AssertionError(after))))
    # and the mail is not destroyed — it keeps its marker in the read archive,
    # so the row still renders as the urgent one it was
    logged = store.load_org(slug).d["user_mail_log"]
    check("the read mail keeps its urgent marker in the archive",
          lambda: (None if logged and logged[-1].get("urgent") is True
                   and logged[-1].get("urgent_reason") == "the wall is on fire"
                   else (_ for _ in ()).throw(AssertionError(logged))))

    # ----------------------------------------------------- §5 the tool card
    print("\n§5 the tool card offers it, and says what it is for:")
    card = [c for c in TOOLS if c["name"] == "orgtree_message"][0]
    props = card["inputSchema"]["properties"]
    check("orgtree_message advertises urgent + urgent_reason",
          lambda: (None if "urgent" in props and "urgent_reason" in props
                   else (_ for _ in ()).throw(AssertionError(sorted(props)))))
    check("urgent is a boolean and urgent_reason a string",
          lambda: (None if props["urgent"]["type"] == "boolean"
                   and props["urgent_reason"]["type"] == "string"
                   else (_ for _ in ()).throw(AssertionError(props))))
    # NEITHER is `required` on the card: an ordinary message must stay a
    # two-argument call. The pairing is enforced in the ledger, where it can
    # be enforced as a RELATION between the two rather than per-field.
    check("neither is schema-required — ordinary mail stays a 2-arg call",
          lambda: (None if "urgent" not in card["inputSchema"]["required"]
                   and "urgent_reason" not in card["inputSchema"]["required"]
                   else (_ for _ in ()).throw(AssertionError(
                       card["inputSchema"]["required"]))))
    # the descriptions carry the two things that keep the signal rare and
    # accountable — sparing use, and a reason addressed to the USER
    check("the card tells the agent to use it sparingly",
          lambda: (None if "sparing" in props["urgent"]["description"].lower()
                   else (_ for _ in ()).throw(AssertionError(props["urgent"]))))
    check("…and that the reason is written for the user, and shown to them",
          lambda: (lambda d: None if "for the user" in d and "shown" in d
                   else (_ for _ in ()).throw(AssertionError(d)))(
                       props["urgent_reason"]["description"].lower()))
    # adding PARAMETERS must not have added a CARD — two suites assert the
    # catalogue size and both would go red together
    check("the tool catalogue is still 27 cards (params, not a new card)",
          lambda: (None if len(TOOLS) == 27
                   else (_ for _ in ()).throw(AssertionError(len(TOOLS)))))

    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
