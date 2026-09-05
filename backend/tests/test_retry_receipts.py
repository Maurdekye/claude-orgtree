"""Phase 2 of w71d69aac — the operations a dying turn COMMITTED, named in the
retry banner (supervisor.resume_frozen + opreceipts.applied_since).

The net-retry banner tells the agent "whatever that turn had already done was
not undone — check your real state". Operation receipts can say WHICH org
operations that turn committed. This suite measures the claims that make
that list truthful rather than decorative:

  §1  `applied_since` — node, outcome and the inclusive bound; generation is
      not a filter; a row that does not parse drops out, never in; the
      server's filing stamp counts, not the client's mint time.
  §4  the renderer and the composer on their own: empty renders nothing, the
      payload is never inspected (markers inside it survive byte for byte),
      the row cap.
  §2  the REAL freeze branch (synthetic CLI, `died-in-flight`) records the
      bound as the ATTEMPT'S START, not the death time; attempt 2 keeps the
      run's origin; a completed turn pops it.
  §3  `resume_frozen` recomposes the banner from its STORED parts — exactly
      ONE paragraph, replay TEXT only — listing a row filed between attempt
      start and death and a row filed AFTER the freeze (the request that was
      on the wire), view byte-identical; an empty log leaves the text
      byte-identical; a payload containing the markers is untouched; a retry
      of a retry (the resumed carrier dies again, through the real loop)
      carries ONE banner, ONE paragraph, the payload once.

§2/§3 borrow test_limit_freeze's rig (throwaway ORGTREE_DATA + HOME, the
synthetic CLI, no port, no network) by importing it FIRST — it binds the
store to its own tmp root before `orgtree` is imported here. They need
`node` on PATH and declare themselves INERT (loudly) without it.

    python backend/tests/test_retry_receipts.py [-v]
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_limit_freeze as rig                                  # noqa: E402
from orgtree import opreceipts, store, supervisor               # noqa: E402

assert store.DATA_ROOT.startswith(rig._TMP), store.DATA_ROOT    # throwaway root

PASSED = 0
FAILED: list[str] = []
NOTES: list[str] = []
VERBOSE = "-v" in sys.argv

H, T = supervisor.RECEIPTS_HEAD, supervisor.RECEIPTS_TAIL
B = supervisor.RETRY_BANNER_TAIL


def check(label: str, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAILED.append(f"{label}\n{traceback.format_exc()}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


class Fixture(AssertionError):
    pass


def fixture(cond: bool, why: str) -> None:
    if not cond:
        raise Fixture(f"FIXTURE (not the property under test): {why}")


def iso(ms: int) -> str:
    """A ledger-shaped stamp (`YYYY-MM-DDTHH:MM:SS.mmmZ`) for a given ms."""
    d = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def mkrow(node: str, at_ms: int, *, outcome: str = "applied", gen: int = 1,
          tool: str = "orgtree_message", **args) -> dict:
    return opreceipts.row(op_id=opreceipts.new_id(), node=node, generation=gen,
                          key=opreceipts.mint_key(at_ms), mint_ms=at_ms,
                          tool=tool, args=args or {"to": "boss"},
                          cls=opreceipts.coverage(tool, args), outcome=outcome,
                          at=iso(at_ms), result={"delivered": True})


# ══════════════════════════════════════════════════════════════════════════ §1

def sec_applied_since() -> None:
    print("\n§1  applied_since — node, outcome, the inclusive bound")
    S = 1_800_000_000_000                      # a real epoch-ms, not a tidy int

    def _filters() -> None:
        d: dict = {}
        before = mkrow("mid", S - 1)
        on = mkrow("mid", S)
        fenced = mkrow("mid", S + 5, outcome="fenced")
        other = mkrow("worker", S + 5)
        later_gen = mkrow("mid", S + 10, gen=2)
        bad = mkrow("mid", S + 20)
        bad["at"] = "yesterday-ish"
        # a client whose clock runs far BEHIND the server: the key was minted
        # "before" the bound, the receipt was filed after it. The filing stamp
        # is the server's and is the one that counts.
        skewed = mkrow("mid", S + 25)
        skewed["mint_ms"] = S - 100_000
        for r in (before, on, fenced, other, later_gen, bad, skewed):
            opreceipts.append(d, r, now_ms=S + 30)
        got = [r["id"] for r in opreceipts.applied_since(d, "mid", S)]
        assert got == [on["id"], later_gen["id"], skewed["id"]], (
            f"expected exactly the on-bound row, the later-generation row and "
            f"the client-clock-behind row, in filing order; got {got} "
            f"(before={before['id']}, fenced={fenced['id']}, "
            f"other-node={other['id']}, unparseable={bad['id']}, "
            f"skewed={skewed['id']})")
    check("filter · at-or-after the bound, this node, applied only; the "
          "generation is NOT a filter; an unparseable `at` drops out; the "
          "server's filing stamp counts, not the client's mint time",
          _filters)

    def _nothing_created() -> None:
        d: dict = {}
        assert opreceipts.applied_since(d, "mid", S) == []
        assert opreceipts.SECTION not in d, (
            "reading receipts MATERIALISED the section on a document that "
            "never carried one — this runs on every resume")
    check("cost · a document with no receipts answers [] without creating "
          "the section", _nothing_created)

    def _at_ms_is_the_server_stamp() -> None:
        r = mkrow("mid", S + 123)
        assert opreceipts.at_ms(r) == S + 123, opreceipts.at_ms(r)
        r["mint_ms"] = S - 999_999                 # the CLIENT's clock, ignored
        assert opreceipts.at_ms(r) == S + 123, (
            "at_ms read mint_ms — the client's clock — not the server's "
            "filing stamp")
        assert opreceipts.at_ms({"at": "2026-09-05T13:52:22Z"}) == \
            int(dt.datetime(2026, 9, 5, 13, 52, 22,
                            tzinfo=dt.timezone.utc).timestamp() * 1000)
        assert opreceipts.at_ms({"at": ""}) is None
    check("clock · at_ms reads the server filing stamp (both formats), never "
          "the client's mint time", _at_ms_is_the_server_stamp)


# ══════════════════════════════════════════════════════════════════════════ §4

#: a payload that quotes every owned marker — an agent pasting a banner into
#: its own message, or a message ABOUT this feature. All of it is the user's.
HOSTILE_PAYLOAD = ("please do the thing\n" + H + "\nKEEP THIS USER INSTRUCTION\n"
                   + T + "\n" + B + "\nkeep this too")


def sec_render() -> None:
    print("\n§4  the renderer and the composer, on their own")
    S = 1_800_000_000_000
    head = "(orgtree) Your previous turn died part-way through.\n\n"

    def _empty_renders_nothing() -> None:
        assert supervisor.render_turn_receipts([], S) == "", (
            "an EMPTY list rendered a paragraph — present, plausible, inert")
        plain = supervisor.compose_retry_banner(head, "", "msg")
        assert plain == head + B + "\n\nmsg", repr(plain)
        assert H not in plain
    check("empty · no rows → no paragraph, and the banner is head + close + "
          "payload", _empty_renders_nothing)

    def _positive() -> None:
        rows = [mkrow("mid", S + 1, to="boss"), mkrow("mid", S + 2,
                                                       tool="orgtree_hire",
                                                       name="kid")]
        p = supervisor.render_turn_receipts(rows, S)
        assert p.startswith(H) and p.endswith(T), p
        for r in rows:
            assert r["id"] in p and r["tool"] in p, p
        assert "to=boss" in p and "name=kid" in p, p
        assert "unrecorded here, not absent" in p and "OBSERVED" in p, (
            "the paragraph does not state its limits: observed-since-bound, "
            "incomplete log")
        assert "unknown" in p, "post-commit effects are not declared unknown"
        out = supervisor.compose_retry_banner(head, p, "msg")
        assert out == head + p + "\n\n" + B + "\n\nmsg", repr(out)
    check("positive · rows render with tool, targets, id and the stated "
          "limits, and compose once before the closing sentence", _positive)

    def _payload_untouched() -> None:
        p = supervisor.render_turn_receipts([mkrow("mid", S + 1)], S)
        out = supervisor.compose_retry_banner(head, p, HOSTILE_PAYLOAD)
        assert out.endswith(HOSTILE_PAYLOAD), (
            "the payload was changed — the composer inspected user text")
        assert out == head + p + "\n\n" + B + "\n\n" + HOSTILE_PAYLOAD
    check("payload · a message quoting every marker survives byte for byte",
          _payload_untouched)

    def _cap() -> None:
        rows = [mkrow("mid", S + i) for i in range(supervisor.RECEIPTS_MAX_ROWS + 3)]
        p = supervisor.render_turn_receipts(rows, S)
        shown = [r for r in rows if r["id"] in p]
        assert len(shown) == supervisor.RECEIPTS_MAX_ROWS, len(shown)
        assert "and 3 more" in p, p
    check("cap · at most RECEIPTS_MAX_ROWS rows, and the remainder is counted",
          _cap)


# ══════════════════════════════════════════════════════════════════════════ §2

def _freeze_once(slug: str, nid: str, msg) -> dict:
    rig.set_mode("died-in-flight")
    rig.run_turn(slug, nid, msg)
    n = rig.node(slug, nid)
    fixture(bool(n.get("frozen")) and bool(n["frozen"].get("connection")),
            f"the synthetic death did not write a connection freeze: "
            f"{n.get('frozen')!r} (net_fail_run={n.get('net_fail_run')!r})")
    return n


def _unfreeze(slug: str, nid: str) -> None:
    """The rig's own way to un-park between attempts — never resume_frozen,
    which would replay onto the same dead CLI."""
    o = store.load_org(slug)
    o.nodes[nid].pop("frozen", None)
    store.save_org(o)


def sec_freeze_bound() -> None:
    print("\n§2  the freeze branch records the attempt's START, not its death")
    slug, nid = rig.probe_org()
    t0 = int(time.time() * 1000)
    n = _freeze_once(slug, nid, "please do the thing")
    fz = n["frozen"]
    death = opreceipts.at_ms({"at": fz.get("at", "")})
    fixture(death is not None, f"frozen.at does not parse: {fz.get('at')!r}")
    since_holder: list[int] = []

    def _bound_is_attempt_start() -> None:
        since = fz.get("receipts_since_ms")
        assert isinstance(since, int), (
            f"the freeze record carries no receipts bound: {fz!r}")
        assert t0 <= since, f"bound {since} precedes the attempt ({t0})"
        assert since <= death, f"bound {since} is after the death ({death})"
        # the mutant this exists to reject writes the DEATH time. A CLI launch
        # takes far longer than a millisecond, so equality is the mutant, not
        # noise — and if this machine ever launches that fast the check says
        # so instead of passing.
        fixture(death - since >= 2,
                f"the CLI died within {death - since} ms of the attempt start "
                f"— the bound cannot be told from the death time on this run")
        assert since < death, "the bound IS the death time"
        assert n.get("net_fail_since_ms") == since, (
            f"the node's run origin ({n.get('net_fail_since_ms')!r}) is not "
            f"what the freeze record carries ({since})")
        since_holder.append(since)
    check("bound · receipts_since_ms lies in [attempt start, death) and equals "
          "the node's run origin", _bound_is_attempt_start)

    def _structure() -> None:
        r = fz.get("retry")
        assert isinstance(r, dict) and r.get("index") == len(fz["resume_texts"]) - 1, r
        assert "please do the thing" in str(r.get("payload")), r
        assert fz["resume_texts"][r["index"]] == supervisor.compose_retry_banner(
            str(r["head"]), "", str(r["payload"])), (
            "the stored parts do not compose to the replay text written")
    check("structure · the freeze keeps the banner's parts, and they compose "
          "to exactly the replay text", _structure)

    def _second_attempt_keeps_the_origin() -> None:
        fixture(bool(since_holder), "the first check did not establish a bound")
        _unfreeze(slug, nid)
        time.sleep(0.01)
        n2 = _freeze_once(slug, nid, "please do the thing")
        assert (n2.get("net_fail_run") or 0) == 2, n2.get("net_fail_run")
        assert n2["frozen"].get("receipts_since_ms") == since_holder[0], (
            f"attempt 2 re-based the bound to its own start "
            f"({n2['frozen'].get('receipts_since_ms')}) — attempt 1's "
            f"receipts ({since_holder[0]}) fall out of the list")
    check("origin · attempt 2 keeps attempt 1's bound (the agent never read "
          "attempt 2's banner)", _second_attempt_keeps_the_origin)

    def _completed_turn_pops_it() -> None:
        _unfreeze(slug, nid)
        rig.set_mode("reply")
        rig.run_turn(slug, nid, "and now it works")
        n3 = rig.node(slug, nid)
        fixture("net_fail_run" not in n3,
                f"the completed turn did not end the run: {n3.get('net_fail_run')!r}")
        assert "net_fail_since_ms" not in n3, (
            "the run's origin outlived the run — the NEXT failure run would "
            f"list receipts from before it: {n3.get('net_fail_since_ms')!r}")
    check("reset · a completed turn pops the origin with the counter",
          _completed_turn_pops_it)


# ══════════════════════════════════════════════════════════════════════════ §3

def _capture_resume(slug: str, nid: str) -> dict:
    """▶ with the launch intercepted: a busy node queues the replay carrier
    instead of starting a thread on the dead CLI. Returns that carrier."""
    st = supervisor.state(slug, nid)
    st["busy"] = True
    try:
        supervisor.resume_frozen(slug, only=[nid])
        q = list(st["queue"])
        st["queue"].clear()
    finally:
        st["busy"] = False
    fixture(bool(q), "resume queued nothing — the record was not resumable")
    return q[0]


def sec_resume_compose() -> None:
    print("\n§3  resume_frozen — one paragraph, text only, read at resume time")

    slug, nid = rig.probe_org()
    n = _freeze_once(slug, nid, HOSTILE_PAYLOAD)
    fz = n["frozen"]
    since = int(fz["receipts_since_ms"])
    death = opreceipts.at_ms({"at": fz["at"]}) or 0
    # ⚠ THE MID-TURN ROW IS ANCHORED TO THE DEATH, NOT TO THE RECORDED BOUND.
    # A mutant that stamped the bound at freeze time (≈ the death) survived a
    # row placed at `since + 1`. The death is an independent witness — a CLI
    # launch takes far longer than the margin — so only a bound that is truly
    # the attempt's start admits a row this far before it.
    fixture(death - since >= 50,
            f"the CLI died {death - since} ms after the attempt start — too "
            f"close for a mid-turn row to sit between them on this run")
    texts0 = list(fz.get("resume_texts") or [])
    views0 = list(fz.get("resume_views") or [])
    fixture(bool(texts0) and texts0[-1].endswith(HOSTILE_PAYLOAD)
            and texts0[-1].count(H) == 1,            # the ONE inside the payload
            "the freeze wrote no banner, or wrote a paragraph at freeze time")

    mid_turn = mkrow(nid, death - 25, to="boss")             # start < at < death
    on_wire = mkrow(nid, int(time.time() * 1000), to="peer")  # AFTER the freeze
    before = mkrow(nid, since - 1, to="boss")
    other = mkrow("someone-else", since + 1, to="boss")
    fenced = mkrow(nid, since + 1, outcome="fenced")
    with store.DOC_LOCK:
        org = store.load_org(slug)
        for r in (before, mid_turn, other, fenced, on_wire):
            opreceipts.append(org.d, r)
        store.save_org(org)

    got: dict = {}

    def _compose() -> None:
        got.update(_capture_resume(slug, nid))
        text = str(got.get("text"))
        # exactly one OWNED paragraph: the payload's quoted markers are the
        # user's and are counted separately by the byte-identical check below
        assert text.count(H) == 2 and text.count(T) == 2, (
            f"{text.count(H) - 1} owned paragraph(s) in the replay text")
        assert mid_turn["id"] in text, (
            "the row filed between the attempt's start and its death is "
            "missing — the bound is the death time, or later")
        assert on_wire["id"] in text, (
            "the row filed AFTER the freeze is missing — the list was rendered "
            "at freeze time, when the on-the-wire request had not committed")
        for r, why in ((before, "before the bound"), (other, "another node"),
                       (fenced, "fenced, i.e. NOT applied")):
            assert r["id"] not in text, f"a row that is {why} was listed"
        assert text.endswith(HOSTILE_PAYLOAD), (
            "the payload is not byte-identical — user text was parsed or cut")
        assert text.index(H) < text.index(B), "not in front of the closing sentence"
        # the payload is `text[-8000:]` of the turn as launched — the message
        # with whatever envelope prefix fit — exactly what the banner always
        # replayed; the carrier brings that same string forward
        assert str(got.get("retry_payload")).endswith(HOSTILE_PAYLOAD), (
            "the carrier does not bring the original payload to the next attempt")
        assert not rig.node(slug, nid).get("frozen"), "still frozen after ▶"
    check("compose · exactly one paragraph, listing the mid-turn row and the "
          "on-the-wire row, excluding before/other-node/fenced; the payload "
          "with every marker quoted is byte-identical", _compose)

    def _view_untouched() -> None:
        fixture("text" in got, "the compose check did not capture a carrier")
        assert str(got.get("view")) == views0[-1], (
            "the human projection changed — the paragraph belongs in the "
            "replay text only")
    check("view · resume_views is byte-identical", _view_untouched)

    def _retry_of_a_retry() -> None:
        """The resumed carrier (banner + paragraph + payload) dies AGAIN,
        through the real loop; the next resume must carry ONE banner, ONE
        paragraph, the payload once — not the previous banner nested."""
        fixture("text" in got, "no carrier from the first resume")
        rig.set_mode("died-in-flight")
        rig.run_turn(slug, nid, dict(got))          # the real retry, dying
        n2 = rig.node(slug, nid)
        fixture(bool(n2.get("frozen")) and (n2.get("net_fail_run") or 0) == 2,
                f"the retried carrier did not die into attempt 2: "
                f"run={n2.get('net_fail_run')!r}")
        c2 = _capture_resume(slug, nid)
        text = str(c2["text"])
        assert text.count(B) == 2, (            # one owned + one quoted
            f"{text.count(B) - 1} banner(s) — the previous banner was nested")
        assert text.count(H) == 2, f"{text.count(H) - 1} paragraph(s)"
        assert text.count("KEEP THIS USER INSTRUCTION") == 1
        assert text.endswith(HOSTILE_PAYLOAD)
        assert mid_turn["id"] in text and on_wire["id"] in text, (
            "attempt 1's receipts fell out of attempt 2's banner")
        assert "attempt 2 of" in text
    check("once · a retry of a retry, through the real loop, carries one "
          "banner, one paragraph and the payload once", _retry_of_a_retry)

    def _empty_log_identical() -> None:
        s2, n2 = rig.probe_org()
        nn = _freeze_once(s2, n2, "nothing was committed")
        t_before = list(nn["frozen"].get("resume_texts") or [])[-1]
        c = _capture_resume(s2, n2)
        assert c["text"] == t_before, (
            "with NO receipts the replay text changed — a paragraph with only "
            "a disclaimer in it is present, plausible and inert")
        assert c.get("retry_payload") == "nothing was committed"[-8000:] or \
            str(c.get("retry_payload")).endswith("nothing was committed")
    check("empty · with no receipts the replay text is byte-identical",
          _empty_log_identical)

    def _legacy_record_untouched() -> None:
        """A freeze written by a build without `retry` parts: nothing to
        recompose, nothing parsed, text unchanged."""
        s3, n3 = rig.probe_org()
        nn = _freeze_once(s3, n3, "old shape")
        with store.DOC_LOCK:
            o = store.load_org(s3)
            o.nodes[n3]["frozen"].pop("retry", None)        # type: ignore[union-attr]
            store.save_org(o)
            opreceipts.append(o.d, mkrow(n3, int(time.time() * 1000)))
            store.save_org(o)
        t_before = list(nn["frozen"].get("resume_texts") or [])[-1]
        c = _capture_resume(s3, n3)
        assert c["text"] == t_before and "retry_payload" not in c
    check("legacy · a record without stored parts is replayed unchanged",
          _legacy_record_untouched)


def main() -> int:
    sec_applied_since()
    sec_render()
    if shutil.which("node"):
        sec_freeze_bound()
        sec_resume_compose()
    else:
        NOTES.append("INERT: `node` is not on PATH — §2 and §3 (the real "
                     "freeze branch and resume) DID NOT RUN")
    for n in NOTES:
        print(f"\n  ! {n}")
    print(f"\n{PASSED} passed, {len(FAILED)} failed"
          + (f", {len(NOTES)} inert" if NOTES else ""))
    for f in FAILED:
        print("\n" + f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
