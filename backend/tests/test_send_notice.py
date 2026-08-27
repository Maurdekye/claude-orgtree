"""orgtree_send_notice — mail that never wakes anyone (user spec 2026-08-19).

    python backend/tests/test_send_notice.py      (no pytest; plain asserts)

The feature is one marker (MailEntry kind == "notice", minted only by the
orgtree_send_notice dispatch) plus three suppressions keyed on it:

  · supervisor.send_message(wake=False) — steer/queue into a RUNNING turn,
    but an idle node parks instead of starting one;
  · ledger rehire drive — a notice-only mailbox does not drive at rehire;
  · reconcile's revive scan — a restart is not a turn either.

The API dispatch surface (card, refusals, the kind-mint guard) is covered in
test_mcptool.py with the rest of the tool catalogue; this file owns the
ledger/supervisor mechanics, which the MCP rig's send_message stub hides.
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-notice-test-")

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: net._default_address
# falls back to the operator's real hub when this root has no defaults.json.
# Guarded over this whole directory by test_external_mail §1.
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import store, supervisor                              # noqa: E402
from orgtree.ledger import Org, USER                               # noqa: E402

PASS = 0

ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="test hire — do test things")
    s.update(over)
    return s


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def mkorg(name):
    org = Org.create(name)
    org.hire(USER, None, "opus", 20, "top")
    org.hire("top", "top", "haiku", 0, "kid", **spec())
    store.save_org(org)
    return org.d["slug"], org


def box(slug, nid):
    return store.load_org(slug).d.get("mail", {}).get(nid, [])


def main():
    print("the marker + waking_mail:")
    slug, org = mkorg("notice core")
    r = org.post_mail("top", "kid", "fyi only", "notice")
    store.save_org(org)
    check("post_mail kind='notice' boxes a normal entry wearing the marker",
          lambda: (lambda m: None if m and m[0]["kind"] == "notice"
                   and m[0]["body"] == "fyi only" and m[0].get("id")
                   else (_ for _ in ()).throw(AssertionError(m)))(box(slug, "kid")))
    check("waking_mail: False on a notice-only box",
          lambda: (None if not store.load_org(slug).waking_mail("kid")
                   else (_ for _ in ()).throw(AssertionError("notice woke"))))

    org = store.load_org(slug)
    org.post_mail("top", "kid", "now act on this", "message")
    store.save_org(org)
    check("waking_mail: True the moment ONE actionable mail joins it",
          lambda: (None if store.load_org(slug).waking_mail("kid")
                   else (_ for _ in ()).throw(AssertionError("mixed box slept"))))

    print("\nthe envelope styling:")

    def _fmt():
        blk, _ = supervisor._mail_block(box(slug, "kid"))
        assert "NOTICE FROM top" in blk, blk
        assert "no reply is expected" in blk, blk
        # the actionable sibling keeps the plain header
        assert "FROM top (your superior) · message ·" in blk, blk
    check("_mail_block: NOTICE header for notices, plain header beside it", _fmt)

    print("\nsend_message wake=False (the real function, no CLI ever spawned):")
    st = supervisor.state(slug, "kid")

    def _idle():
        r = supervisor.send_message(slug, "kid", "(orgtree) notice nudge",
                                    wake=False)
        assert r.get("parked") and r["accepted"], r
        assert not st["busy"], "wake=False set busy — a turn would have run"
        assert box(slug, "kid"), "an idle park drained the mailbox"
    check("idle → parked: no turn, mailbox untouched", _idle)

    def _busy():
        st["busy"] = True
        try:
            r = supervisor.send_message(slug, "kid", "(orgtree) notice nudge",
                                        wake=False)
            assert r.get("queued") == 1 and not r.get("parked"), r
            assert st["queue"] == ["(orgtree) notice nudge"], st["queue"]
        finally:
            st["queue"].clear()
            st["busy"] = False
    check("busy → queued into the RUNNING turn (it was already awake)", _busy)

    def _steer():
        st["busy"] = True
        st["responding"] = True
        try:
            r = supervisor.send_message(slug, "kid", "(orgtree) notice nudge",
                                        wake=False)
            assert r.get("steering"), r
            carrier = st["steer"][-1]
            assert isinstance(carrier, dict) and carrier["toks"], carrier
            assert not box(slug, "kid"), "steer did not drain the mailbox"
            tok = carrier["toks"][0]
            dl = store.load_org(slug).d.get("delivering", {}).get("kid", [])
            assert any(b.get("tok") == tok for b in dl), (tok, dl)
        finally:
            st["steer"].clear()
            st.pop("responding", None)
            st["busy"] = False
    check("responding → steered mid-task, mail journaled on the carrier", _steer)

    def _fold():
        # the steer above left a drained batch journaled with its carrier now
        # gone (we cleared it) — exactly the no-wake race. Plant a SECOND
        # batch that a live carrier still owns, then fold back only ours.
        org = store.load_org(slug)
        dl = org.d["delivering"]["kid"]
        ours = dl[0]["tok"]
        dl.append({"tok": "someone-elses", "at": "2026-08-19T00:00:00Z",
                   "mail": [{"id": "z", "from": "top", "kind": "message",
                             "body": "riding a live carrier",
                             "at": "2026-08-19T00:00:00Z"}],
                   "notices": [], "via": "steer"})
        store.save_org(org)
        supervisor._fold_back_undelivered(slug, "kid", only_toks=[ours])
        org = store.load_org(slug)
        left = org.d.get("delivering", {}).get("kid", [])
        assert [b["tok"] for b in left] == ["someone-elses"], left
        assert any(m["kind"] == "notice" for m in box(slug, "kid")), \
            "the folded notice did not return to the mailbox"
        # tidy: drop the planted batch so later checks see a clean journal
        org.d["delivering"].pop("kid", None)
        store.save_org(org)
    check("fold-back only_toks: OUR batch returns, the other stays journaled",
          _fold)

    print("\nrehire — notices wait, they do not drive:")
    slug2, org2 = mkorg("notice rehire")
    org2.retire("top", "kid")
    r = org2.post_mail("top", "kid", "fyi while you were out", "notice")
    check("deferred notice warns 'first turn after rehire', not 'acted on'",
          lambda: (None if any("first turn after rehire" in w
                               for w in r["warnings"])
                   else (_ for _ in ()).throw(AssertionError(r["warnings"]))))
    r2 = org2.rehire("top", "kid")
    check("rehire over a notice-only box: drive is EMPTY",
          lambda: (None if r2.get("drive") == []
                   else (_ for _ in ()).throw(AssertionError(r2))))
    org2.retire("top", "kid")
    org2.post_mail("top", "kid", "real work waiting", "message")
    r3 = org2.rehire("top", "kid")
    check("rehire with actionable mail beside the notice: drive fires",
          lambda: (None if "kid" in r3.get("drive", [])
                   else (_ for _ in ()).throw(AssertionError(r3))))
    store.save_org(org2)

    print("\nreconcile — a restart is not a turn:")
    slug3, org3 = mkorg("notice reconcile")
    org3.post_mail("top", "kid", "fyi across the restart", "notice")
    store.save_org(org3)
    revived = []
    real = supervisor.send_message
    supervisor.send_message = lambda s, n, t, **k: revived.append((s, n)) or \
        {"accepted": True, "queued": 0}
    try:
        supervisor.reconcile(slug3)
        check("notice-only mailbox: the revive scan leaves the node asleep",
              lambda: (None if revived == []
                       else (_ for _ in ()).throw(AssertionError(revived))))
        org3 = store.load_org(slug3)
        org3.post_mail("top", "kid", "this one matters", "message")
        store.save_org(org3)
        supervisor.reconcile(slug3)
        check("…and actionable mail beside it still revives after a restart",
              lambda: (None if (slug3, "kid") in revived
                       else (_ for _ in ()).throw(AssertionError(revived))))
    finally:
        supervisor.send_message = real

    print("\nself-notice — the §7.2 fall-through McpLink 2.9.1 depends on:")
    # ⚠ THIS SECTION PINS A FALL-THROUGH, NOT A DESIGNED FEATURE. A node may
    # address ITSELF because §7.2's sibling clause (`s["parent"] ==
    # target["parent"]`, ledger.post_mail) is trivially true when sender and
    # target are the same node. Nobody decided that; nothing excluded it.
    #
    # It became load-bearing on 2026-08-27: McpLink 2.9.1 ships panel
    # open/close events as passive SELF-notices, actor pinned structurally to
    # the recipient, because that is the only actor that borrows no authority
    # and — unlike a downward notice — mints no §7.3 audience. See D-165.
    #
    # So if you are tidying §7.2 and this section goes red, you have not
    # broken a test: you have found the decision. Closing the self case is
    # allowed, but it is a RULING (and an outside consumer to tell), never a
    # tidy-up. Investigated by notice-endpoint, 2026-08-27.
    slug4, org4 = mkorg("self notice")
    org4.hire("top", "top", "haiku", 0, "kid2", **spec())   # a REAL peer

    def _leaf_self():
        r = org4.post_mail("kid", "kid", "panel closed", "notice")
        assert r["delivered"] == "kid", r
        m = org4.d["mail"]["kid"]
        assert m[-1]["kind"] == "notice", m
        assert m[-1]["from"] == "kid", "a self-notice must be attributed to " \
                                       "the node itself, not rewritten"
    check("§7.2 PERMITS a node to notice itself (the sibling fall-through)",
          _leaf_self)

    def _top_self():
        # the OTHER trivially-equal case: a top-level node's parent is None,
        # and None == None passes the same clause
        r = org4.post_mail("top", "top", "panel closed", "notice")
        assert r["delivered"] == "top", r
        assert org4.d["mail"]["top"][-1]["from"] == "top"
    check("…and a TOP-LEVEL node too (parent None == None, same clause)",
          _top_self)

    def _no_audience():
        # §7.3 grants a reply path on a DOWNWARD send to a non-child
        # descendant. is_ancestor is strict, so self never trips it — which is
        # what keeps a per-panel-event notice free of side effects. If this
        # goes red, every panel event is silently mutating the audience list.
        r = org4.post_mail("kid", "kid", "again", "notice")
        assert r["warnings"] == [], r["warnings"]
        assert not any(a["grantee"] == "kid" and a["grantor"] == "kid"
                       for a in org4.d["audiences"]), org4.d["audiences"]
    check("a self-notice grants NO audience and warns about nothing",
          _no_audience)

    def _self_never_wakes():
        # the no-wake property, measured for the SELF case specifically
        # rather than assumed to inherit from the peer case
        store.save_org(org4)
        st4 = supervisor.state(slug4, "kid")
        assert not st4["busy"], "fixture started busy — result would be void"
        r = supervisor.send_message(slug4, "kid", "(orgtree) notice nudge",
                                    wake=False)
        assert r.get("parked") and r["accepted"], r
        assert not st4["busy"], "a self-notice set busy — a turn would run"
        assert box(slug4, "kid"), "the park drained its own mailbox"
    check("self-notice parks: no turn starts, the mailbox keeps it",
          _self_never_wakes)

    def _envelope():
        blk, _ = supervisor._mail_block(box(slug4, "kid"))
        assert "NOTICE FROM kid" in blk, blk
        # the label: ruled 2026-08-27 (user) — an agent noticing itself is
        # told so plainly. It read "your peer" until then, because
        # relationship() fell through the same parent-equality comparison the
        # permission does. Only the LABEL moved; post_mail's `allowed` was
        # not touched, and the section above is what proves that.
        assert box(slug4, "kid")[-1]["relationship"] == "yourself", \
            box(slug4, "kid")[-1]
        assert "(yourself" in blk, blk
    check("the envelope introduces it as from 'yourself', not 'your peer'",
          _envelope)

    def _label_is_not_permission():
        # the fence the relabel had to respect: a PEER is still "your peer",
        # so the new self case did not swallow the sibling label — and the
        # sibling PERMISSION still works, which is the thing that must not
        # have moved
        r = org4.post_mail("kid", "kid2", "sideways", "notice")
        assert r["delivered"] == "kid2", r
        assert org4.d["mail"]["kid2"][-1]["relationship"] == "your peer"
    check("a real peer is still 'your peer' — the relabel took only the self "
          "case", _label_is_not_permission)

    print(f"\nall {PASS} checks passed")


if __name__ == "__main__":
    main()
