"""Durable operation receipts (opreceipts.py, docs/op-receipts.md, w71d69aac).

Every check drives the INSTALLED door — `POST /api/agent` — through the
FastAPI test client and reads the result back through the store, so nothing
here asserts on a helper's private shape. `supervisor.send_message` is stubbed
to RECORD (nothing may launch a model).

What this suite is FOR: the receipt log exists so that a lost response can be
resolved instead of guessed at, and the one thing it must never do is turn a
forgotten receipt into permission to do the operation twice. Sections 5 and 6
are that property; the rest keeps it honest.

Sections:
    §1  the key and the fingerprint
    §2  admission — file, replay, conflict, refuse
    §3  the receipt is in the SAME transaction as the effect
    §4  the lookup — five states, each for a real reason
    §5  the fence — a lookup that answers "not applied" makes it true
    §6  retention — the watermark, and why eviction cannot become permission
    §7  coverage — the table matches the dispatch, actions included
    §8  what is NOT stored, and what a receipt does not claim
    §9  compatibility — old documents, no key, JSON

    python backend/tests/test_op_receipts.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
_TMP = tempfile.mkdtemp(prefix="orgtree-opreceipts-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ.pop("ORGTREE_WARM", None)
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient                      # noqa: E402
from orgtree import api, mcptool, opreceipts, store, supervisor  # noqa: E402
from orgtree.ledger import USER                                # noqa: E402

assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT   # throwaway root

DRIVEN: list[tuple[str, str]] = []


def _fake_send(slug, nid, text, command=False, wake=True, **kw):
    DRIVEN.append((slug, nid))
    return {"accepted": True, "queued": 0}


supervisor.send_message = _fake_send
api.supervisor.send_message = _fake_send
# a hire through the API checks that the tier's PROVIDER is signed in on this
# machine — nothing to do with receipts, and not something a suite may depend
# on. Stubbed so the admission tests can use the one verb whose duplication is
# unmistakable: a second agent either exists or it does not.
api.provider_hire_gate = lambda *a, **kw: None

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


def fresh_org():
    """boss (top, hires) > mid > worker, plus an unrelated top-level peer."""
    _n[0] += 1
    org = store.create_org(f"receipts-{_n[0]}", [])
    org.hire(USER, None, "opus", 60, "boss")
    org.hire(USER, "boss", "haiku", 20, "mid")
    org.hire(USER, "mid", "haiku", 0, "worker")
    org.hire(USER, None, "haiku", 20, "peer")
    store.save_org(org)
    return org.d["slug"]


def call(_slug, _node, _tool, key="", **args):
    """⚠ underscored parameters on purpose: `node`, `to`, `key` and `action`
    are all ordinary tool ARGUMENTS, and a bare name here would collide with
    them.

    A keyed call goes out as `orgtree_op_call` — the wrapper is the wire
    format, not a detail of it, so every check here drives the shape a real
    client sends."""
    if key:
        body = {"org": _slug, "node": _node, "tool": opreceipts.OP_CALL,
                "args": {"tool": _tool, "args": args, "op_key": key}}
    else:
        body = {"org": _slug, "node": _node, "tool": _tool, "args": args}
    r = client.post("/api/agent", json=body)
    js = (r.json() if r.headers.get("content-type", "").startswith("application/json")
          else r.text)
    return r.status_code, js


def lookup(_slug, _node, _key, _for_tool, **for_args):
    st, js = call(_slug, _node, "orgtree_op_lookup", op_key=_key,
                  for_tool=_for_tool, for_args=for_args)
    assert st == 200, (st, js)
    return js


def rows(slug):
    return list(store.load_org(slug).d.get("op_receipts") or [])


def meta(slug):
    return dict(store.load_org(slug).d.get("op_receipts_meta") or {})


def nodes(slug):
    return sorted(store.load_org(slug).d["nodes"])


def key_at(ms):
    return opreceipts.mint_key(ms)


def k():
    return opreceipts.mint_key()


def hire_args(name="newbie"):
    """A complete agent hire — no defaults, the ledger's own rule."""
    return {"parent": "boss", "tier": "haiku", "grant": 0, "name": name,
            "charter": "do the thing", "org_visibility": "team",
            "add_dirs": [], "tools": {"bash": True, "web": False,
                                      "edit": True, "subagents": False,
                                      "mcp": []},
            "permission_mode": "acceptEdits"}


# ================================================ §1 the key and the print
print("\n§1  the key and the fingerprint")


def _key_roundtrip():
    now_ms = int(time.time() * 1000)
    key = opreceipts.mint_key(now_ms)
    assert opreceipts.parse_key(key) == now_ms, key
    # the mint time is IN the key so a never-seen key can still be judged
    for bad in ("", "nope", "123-abc", "1757070000000-XYZ",
                str(now_ms), f"{now_ms}-{'f' * 23}"):
        assert opreceipts.parse_key(bad) is None, bad


check("a key carries its own mint time; anything else parses as not-ours",
      _key_roundtrip)


def _fingerprint():
    a = {"to": "boss", "body": "hello"}
    f1 = opreceipts.fingerprint("orgtree_message", "mid", 0, a)
    assert len(f1) == 64, f1            # FULL sha256, not a prefix
    # every part of the call identity moves it
    assert f1 != opreceipts.fingerprint("orgtree_message", "mid", 1, a)
    assert f1 != opreceipts.fingerprint("orgtree_message", "worker", 0, a)
    assert f1 != opreceipts.fingerprint("orgtree_send_notice", "mid", 0, a)
    assert f1 != opreceipts.fingerprint("orgtree_message", "mid", 0,
                                        {**a, "body": "hello!"})
    # …and key order does not (canonical serialization)
    assert f1 == opreceipts.fingerprint(
        "orgtree_message", "mid", 0, {"body": "hello", "to": "boss"})


check("the fingerprint is a full sha256 over tool + node + generation + args",
      _fingerprint)


# ==================================================== §2 admission
print("\n§2  admission — file, replay, conflict, refuse")


def _files_one():
    slug = fresh_org()
    key = k()
    st, js = call(slug, "mid", "orgtree_message", key=key, to="boss",
                  body="one")
    assert st == 200, (st, js)
    r = rows(slug)
    assert len(r) == 1, r
    row = r[0]
    assert row["outcome"] == "applied" and row["tool"] == "orgtree_message"
    assert row["node"] == "mid" and row["gen"] == 0 and row["key"] == key
    assert row["cls"] == opreceipts.TX_POST, row
    assert row["result"].get("delivered") == "boss", row["result"]
    # the post-commit effects are NAMED and NOT claimed
    assert row["post_effects"]["expected"] == ["drive"], row["post_effects"]
    assert row["post_effects"]["observed"] == "unknown", row["post_effects"]


check("an applied keyed call files exactly one receipt", _files_one)


def _refusal_files_nothing():
    slug = fresh_org()
    # positive control FIRST: this same shape DOES file when it succeeds
    st, _ = call(slug, "mid", "orgtree_message", key=k(), to="boss", body="ok")
    assert st == 200
    assert len(rows(slug)) == 1
    # …and a refusal (worker may not message a stranger it holds no line to)
    st, js = call(slug, "worker", "orgtree_message", key=k(), to="peer",
                  body="nope")
    assert st == 422, (st, js)
    assert len(rows(slug)) == 1, "a refused call left a receipt behind"


check("a REFUSED call files no receipt (with the succeeding control)",
      _refusal_files_nothing)


def _replay():
    slug = fresh_org()
    key = k()
    st, first = call(slug, "boss", "orgtree_hire", key=key, **hire_args())
    assert st == 200, (st, first)
    after = nodes(slug)
    st, again = call(slug, "boss", "orgtree_hire", key=key, **hire_args())
    assert st == 200, (st, again)
    assert again.get("replayed") is True, again
    assert again["receipt"]["id"] == rows(slug)[0]["id"]
    assert nodes(slug) == after, "the replay hired a SECOND agent"
    assert len(rows(slug)) == 1, rows(slug)
    # it is presented as a receipt, not as a fresh result
    assert "ALREADY APPLIED" in again["status"]


check("the same key twice hires ONCE and returns the receipt", _replay)


def _conflict_args():
    slug = fresh_org()
    key = k()
    st, _ = call(slug, "mid", "orgtree_message", key=key, to="boss", body="a")
    assert st == 200
    st, js = call(slug, "mid", "orgtree_message", key=key, to="boss", body="b")
    assert st == 409, (st, js)
    assert "conflict" in str(js).lower()
    assert len(rows(slug)) == 1, "the conflicting call was recorded or ran"


check("the same key with DIFFERENT arguments conflicts — no replay, no run",
      _conflict_args)


def _conflict_tool():
    slug = fresh_org()
    key = k()
    st, _ = call(slug, "mid", "orgtree_message", key=key, to="boss", body="a")
    assert st == 200
    st, js = call(slug, "mid", "orgtree_send_notice", key=key, to="boss",
                  body="a")
    assert st == 409, (st, js)
    assert len(rows(slug)) == 1


check("the same key on a different VERB conflicts", _conflict_tool)


def _scoped_to_node():
    slug = fresh_org()
    key = k()
    st, _ = call(slug, "mid", "orgtree_message", key=key, to="boss", body="a")
    assert st == 200
    # another node presenting the same key is a different scope: it RUNS
    st, js = call(slug, "worker", "orgtree_message", key=key, to="mid",
                  body="mine")
    assert st == 200, (st, js)
    assert js.get("replayed") is not True, js
    assert len(rows(slug)) == 2, rows(slug)
    # …and cannot see the other node's receipt
    ans = lookup(slug, "worker", key, "orgtree_message", to="boss", body="a")
    assert ans["state"] != "applied", ans


check("a key is scoped to its node — another node neither replays nor reads it",
      _scoped_to_node)


def _bump_generation(slug, node):
    """`generation` is the field the ledger bumps whenever a seat's session
    lineage changes (`_archive_session_in_place`, `compact_split`,
    `record_cli_compaction`). Set directly, because what is under test is what
    the RECEIPT does across the bump, not the ledger's own bump."""
    org = store.load_org(slug)
    org.d["nodes"][node]["generation"] = 1
    store.save_org(org)
    assert store.load_org(slug).d["nodes"][node].get("generation") == 1


def _same_key_in_a_new_generation_never_runs_again():
    """⚠ ASTRA, 2026-09-05T12:20Z. This check used to assert the OPPOSITE —
    that a new generation "starts clean" and the same key runs again. It was
    the duplicate the feature exists to prevent, written down as a
    requirement: a keyed call applies, the answer is lost, the seat compacts
    (its generation bumps), and the delayed original then arrives and is
    admitted a second time because the receipt is invisible at the new
    generation.

    A key belongs to the call that minted it, not to an incarnation. Finding
    it in ANY generation means it has been used."""
    slug = fresh_org()
    key = k()
    st, _ = call(slug, "worker", "orgtree_status", key=key, status="working",
                 summary="on it")
    assert st == 200
    assert rows(slug)[0]["gen"] == 0
    before = len(rows(slug))
    _bump_generation(slug, "worker")
    st, js = call(slug, "worker", "orgtree_status", key=key, status="working",
                  summary="on it")
    assert st == 422, (st, js)
    assert "generation" in str(js), js
    assert len(rows(slug)) == before, "the delayed original filed a second receipt"


check("the same key in a NEW generation is refused, never run again",
      _same_key_in_a_new_generation_never_runs_again)


def _lookup_across_a_generation_bump():
    """The other half of the same hole: the call DID apply, the seat then
    compacted, and the lookup — which reads the node's CURRENT generation —
    could not see the receipt and answered `not_applied`, i.e. "safe to
    reissue" for an operation that had already happened."""
    slug = fresh_org()
    key = k()
    st, _ = call(slug, "mid", "orgtree_message", key=key, to="boss", body="a")
    assert st == 200
    _bump_generation(slug, "mid")
    ans = lookup(slug, "mid", key, "orgtree_message", to="boss", body="a")
    assert ans["state"] == "applied", ans
    assert ans["receipt"]["gen"] == 0, ans
    # …and a DIFFERENT operation under that key still conflicts, at either
    # generation: the fingerprint is compared at the receipt's own generation,
    # not recomputed at the current one (which would never match anything).
    other = lookup(slug, "mid", key, "orgtree_message", to="boss", body="z")
    assert other["state"] == "conflict", other


check("a lookup across a generation bump still finds the receipt",
      _lookup_across_a_generation_bump)


def _malformed_and_stale():
    slug = fresh_org()
    for bad, why in ((f"nonsense", "malformed_key"),
                     (key_at(int(time.time() * 1000) + 600_000),
                      "key_from_the_future"),
                     (key_at(int(time.time() * 1000) - 1_800_000),
                      "key_stale")):
        st, js = call(slug, "mid", "orgtree_message", key=bad, to="boss",
                      body="x")
        assert st == 422, (why, st, js)
        assert why in str(js), (why, js)
        assert not rows(slug), f"{why} ran the operation anyway"


check("a malformed, future-dated or stale key refuses and runs nothing",
      _malformed_and_stale)


# ======================================= §3 one transaction with the effect
print("\n§3  the receipt rides the SAME transaction as the effect")


def _store_failure():
    """⚠ THE STORE FAILS, not a helper. An exception raised before `save_org`
    would prove nothing about atomicity — the interesting failure is one
    inside the commit itself."""
    slug = fresh_org()
    real = store.save_org
    calls = [0]

    def boom(org):
        calls[0] += 1
        raise OSError("disk went away mid-commit")

    key = k()
    store.save_org = boom
    api.store.save_org = boom
    try:
        try:
            call(slug, "boss", "orgtree_hire", key=key, **hire_args("ghost"))
        except OSError:
            pass                     # the client sees a 500/raise — as it should
    finally:
        store.save_org = real
        api.store.save_org = real
    assert calls[0] == 1, calls
    # NEITHER the effect NOR the receipt is durable
    assert "ghost" not in nodes(slug), nodes(slug)
    assert not rows(slug), rows(slug)
    # positive control: the same call, no injected failure, leaves both
    st, _ = call(slug, "boss", "orgtree_hire", key=k(), **hire_args("ghost"))
    assert st == 200
    assert "ghost" in nodes(slug) and len(rows(slug)) == 1


check("a failure INSIDE the commit leaves neither the effect nor the receipt",
      _store_failure)


def _inflight_covers_the_transaction():
    """The in-flight mark must cover the whole transaction — observed from
    inside it, at save time, rather than inferred."""
    slug = fresh_org()
    key = k()
    real = store.save_org
    seen: list[bool] = []

    def watching(org):
        with api._OP_INFLIGHT_LOCK:
            seen.append((slug, "mid", key) in api._OP_INFLIGHT)
        return real(org)

    store.save_org = watching
    api.store.save_org = watching
    try:
        st, _ = call(slug, "mid", "orgtree_message", key=key, to="boss",
                     body="hi")
    finally:
        store.save_org = real
        api.store.save_org = real
    assert st == 200
    assert seen == [True], seen
    with api._OP_INFLIGHT_LOCK:
        assert (slug, "mid", key) not in api._OP_INFLIGHT, "the mark leaked"


check("the in-flight mark covers the transaction and is cleared after it",
      _inflight_covers_the_transaction)


def _concurrent_duplicates():
    """⚠ THE OVERLAP IS FORCED, NOT HOPED FOR. Two threads racing on their own
    schedule finish one after the other on this machine, and the check passed
    for that reason rather than for the right one — the mutation harness
    caught it (M6 survived). So the first call is held INSIDE its transaction
    while the second reaches admission: with admission inside the lock the
    second waits and then replays, and with it hoisted outside the lock the
    second admits on a stale read and the org gets two of everything."""
    slug = fresh_org()
    key = k()
    real = store.save_org
    held = threading.Event()

    def slow(org):
        if not held.is_set():
            held.set()
            time.sleep(1.0)          # the window the second call arrives in
        return real(org)

    out: list[tuple[int, object]] = []
    lock = threading.Lock()

    def go():
        r = call(slug, "boss", "orgtree_hire", key=key, **hire_args("twin"))
        with lock:
            out.append(r)

    store.save_org = slow
    api.store.save_org = slow
    try:
        a = threading.Thread(target=go)
        a.start()
        held.wait(10)                # A is now inside its transaction
        b = threading.Thread(target=go)
        b.start()
        for t in (a, b):
            t.join(60)
    finally:
        store.save_org = real
        api.store.save_org = real
    assert len(out) == 2, out
    # the sharp assertion: TWO admissions would file TWO receipts, whatever
    # the ledger then does about a duplicate name
    assert len(rows(slug)) == 1, rows(slug)
    assert sorted(nodes(slug)).count("twin") == 1, nodes(slug)
    replays = [js.get("replayed") for st, js in out if isinstance(js, dict)]
    assert replays.count(True) == 1, out


check("two concurrent calls with one key hire once; the loser gets the receipt",
      _concurrent_duplicates)


# ================================================== §4 the lookup's answers
print("\n§4  the lookup — five states, each for a real reason")


def _lookup_applied():
    slug = fresh_org()
    key = k()
    st, _ = call(slug, "mid", "orgtree_message", key=key, to="boss", body="a")
    assert st == 200
    ans = lookup(slug, "mid", key, "orgtree_message", to="boss", body="a")
    assert ans["state"] == "applied", ans
    assert ans["receipt"]["key"] == key
    assert "post-commit" in ans["status"]


check("applied — the receipt, and the caveat that post effects are uncovered",
      _lookup_applied)


def _lookup_conflict():
    slug = fresh_org()
    key = k()
    call(slug, "mid", "orgtree_message", key=key, to="boss", body="a")
    ans = lookup(slug, "mid", key, "orgtree_message", to="boss", body="DIFFERENT")
    assert ans["state"] == "conflict", ans


check("conflict — the key names a different operation", _lookup_conflict)


def _lookup_running():
    """A queued call is marked in flight before it reaches the lock, so the
    honest answer is `running` — and, crucially, it is NOT fenced: fencing a
    live call would refuse an operation that is about to succeed."""
    slug = fresh_org()
    key = k()
    with api._OP_INFLIGHT_LOCK:
        api._OP_INFLIGHT[(slug, "mid", key)] = time.time()
    try:
        ans = lookup(slug, "mid", key, "orgtree_message", to="boss", body="a")
    finally:
        with api._OP_INFLIGHT_LOCK:
            api._OP_INFLIGHT.pop((slug, "mid", key), None)
    assert ans["state"] == "running", ans
    assert not rows(slug), "a running call was fenced"


check("running — this process has it in flight, and it is not fenced",
      _lookup_running)


def _lookup_unsupported_operation():
    slug = fresh_org()
    ans = lookup(slug, "mid", k(), "orgtree_read_scratch", path=".")
    assert ans["state"] == "unknown", ans
    assert ans["reason"] == "unsupported_operation", ans
    assert ans["coverage"] == opreceipts.NONE, ans
    assert not rows(slug), "a verb with no transaction was fenced"


check("unknown/unsupported_operation — a verb that never reaches a transaction",
      _lookup_unsupported_operation)


def _envelope_key_is_refused():
    """⚠ THE HOLE ASTRA FOUND IN d3cd3fe, closed at its source.

    A key used to ride the request envelope, and a backend older than
    receipts DROPS an unknown envelope field and runs the operation anyway —
    reproduced against the real a0fac2f build in
    `luna-reserve/probe_old_build.py`: mail delivered, no receipt written,
    and this build's lookup then answering "not applied, safe to reissue".

    This build refuses that spelling outright, so the only way a key reaches
    the dispatch is through a verb an old build cannot execute. The check
    that matters is the SECOND assertion: a refusal that still performed the
    operation would close nothing."""
    slug = fresh_org()
    r = client.post("/api/agent", json={
        "org": slug, "node": "mid", "tool": "orgtree_message",
        "args": {"to": "boss", "body": "a"}, "op_key": k()})
    assert r.status_code == 422, (r.status_code, r.text)
    assert opreceipts.OP_CALL in r.text, r.text
    assert not (store.load_org(slug).d.get("mail") or {}).get("boss"), \
        "the envelope-keyed call was refused AND still delivered"
    assert not rows(slug)


check("an `op_key` on the envelope is refused — the old build's spelling",
      _envelope_key_is_refused)


def _wrapper_refusals_do_not_look_like_an_old_build():
    """The client falls back to an UNPROTECTED call when it sees the old
    build's `unknown orgtree tool 'orgtree_op_call'`. So this server must
    never answer a malformed wrapper with anything that reads like that, or a
    client bug quietly becomes a duplicate."""
    slug = fresh_org()
    for bad in ({"args": {"to": "boss"}, "op_key": k()},          # no tool
                {"tool": "orgtree_message", "op_key": k()},       # no args
                {"tool": "orgtree_message", "args": {"to": "boss"}},  # no key
                {"tool": opreceipts.OP_CALL, "args": {}, "op_key": k()},
                {"tool": opreceipts.OP_LOOKUP, "args": {}, "op_key": k()}):
        r = client.post("/api/agent", json={
            "org": slug, "node": "mid", "tool": opreceipts.OP_CALL,
            "args": bad})
        assert r.status_code == 422, (bad, r.status_code, r.text)
        assert not mcptool._old_build_refusal(r.text), (bad, r.text)
    # positive control: the string the client DOES act on is recognised
    assert mcptool._old_build_refusal(
        '{"detail":"unknown orgtree tool \'orgtree_op_call\'"}')
    assert not rows(slug)


check("a malformed wrapper never reads as 'this build has no receipts'",
      _wrapper_refusals_do_not_look_like_an_old_build)


def _the_first_key_on_a_document_is_admitted():
    """There is no grace window left to admit it: a key minted before this
    document had any receipts at all is ordinary, because the wrapper verb —
    not the key's age — is what proves a receipts build handled it."""
    slug = fresh_org()
    key = key_at(int(time.time() * 1000) - 5_000)
    st, js = call(slug, "mid", "orgtree_message", key=key, to="boss", body="a")
    assert st == 200, (st, js)
    assert len(rows(slug)) == 1
    assert meta(slug)["from_ms"] == 0, meta(slug)
    assert "bootstrap_ms" not in meta(slug), meta(slug)


check("the first key on a document is admitted, with no grace window",
      _the_first_key_on_a_document_is_admitted)


def _schema_ahead_is_unknown():
    """A document written by a NEWER receipts build. Its rows were admitted
    under rules this build does not have, so this build must not read a
    missing one as "never applied"."""
    slug = fresh_org()
    call(slug, "mid", "orgtree_message", key=k(), to="boss", body="a")
    for field, bump in (("schema", opreceipts.SCHEMA + 1),
                        ("coverage", opreceipts.COVERAGE + 1)):
        org = store.load_org(slug)
        org.d["op_receipts_meta"] = {**meta(slug), field: bump}
        store.save_org(org)
        ans = lookup(slug, "mid", k(), "orgtree_message", to="boss", body="z")
        assert ans["state"] == "unknown", (field, ans)
        assert ans["reason"] == "schema_ahead", (field, ans)
        org = store.load_org(slug)
        org.d["op_receipts_meta"] = {**meta(slug), field: bump - 1}
        store.save_org(org)
    # positive control: with the meta back at this build's own revisions the
    # very same lookup reaches a real answer
    ans = lookup(slug, "mid", k(), "orgtree_message", to="boss", body="z")
    assert ans["state"] == "not_applied", ans


check("unknown/schema_ahead — a newer build's receipts are not ours to judge",
      _schema_ahead_is_unknown)


# ========================================================== §5 the fence
print("\n§5  the fence — a lookup that says 'not applied' makes it true")


def _fence_then_original():
    """⚠ THE CASE THE `running` TABLE CANNOT COVER. The first request may
    still be on the wire — not in flight anywhere — when the lookup sees
    nothing. Telling the caller "safe to reissue" would be false unless the
    old key can no longer take effect, so the lookup FENCES it."""
    slug = fresh_org()
    key = k()
    ans = lookup(slug, "boss", key, "orgtree_hire", **hire_args("late"))
    assert ans["state"] == "not_applied", ans
    assert ans["fenced"] is True and "never apply" in ans["status"], ans
    fenced = rows(slug)
    assert len(fenced) == 1 and fenced[0]["outcome"] == "fenced", fenced
    # now the delayed original arrives — it must NOT apply
    st, js = call(slug, "boss", "orgtree_hire", key=key, **hire_args("late"))
    assert st == 422, (st, js)
    assert "fenced" in str(js), js
    assert "late" not in nodes(slug), nodes(slug)
    # and the operation is still available under a fresh key
    st, _ = call(slug, "boss", "orgtree_hire", key=k(), **hire_args("late"))
    assert st == 200
    assert "late" in nodes(slug)


check("a fenced key can never apply, and a fresh key still can",
      _fence_then_original)


def _fence_is_idempotent():
    slug = fresh_org()
    key = k()
    a1 = lookup(slug, "mid", key, "orgtree_message", to="boss", body="a")
    a2 = lookup(slug, "mid", key, "orgtree_message", to="boss", body="a")
    assert a1["state"] == a2["state"] == "not_applied", (a1, a2)
    assert len(rows(slug)) == 1, "the second lookup fenced it again"


check("asking twice fences once and answers the same", _fence_is_idempotent)


def _a_fenced_key_still_identifies_ONE_operation():
    """⚠ ASTRA, 2026-09-05T12:20Z. The APPLIED branch compares the verb and
    the fingerprint before answering; the FENCED branch did not. So a lookup
    that asked about a different operation under a key some other call had
    fenced was told "that one did not apply, safe to reissue" — an answer
    about a call the fence never covered, and with the coverage class taken
    from the ASKER's verb rather than the row's.

    A key identifies one call. Asking about a different one is a conflict,
    fenced or not."""
    slug = fresh_org()
    key = k()
    first = lookup(slug, "mid", key, "orgtree_message", to="boss", body="a")
    assert first["state"] == "not_applied", first
    for tool, args in (("orgtree_message", {"to": "boss", "body": "DIFFERENT"}),
                       ("orgtree_message", {"to": "worker", "body": "a"}),
                       ("orgtree_send_notice", {"to": "boss", "body": "a"})):
        ans = lookup(slug, "mid", key, tool, **args)
        assert ans["state"] == "conflict", (tool, args, ans)
    assert len(rows(slug)) == 1, "a conflicting lookup wrote a row"
    # positive control: the operation the fence DID cover still answers
    same = lookup(slug, "mid", key, "orgtree_message", to="boss", body="a")
    assert same["state"] == "not_applied", same


check("a fenced key still identifies ONE operation — a different one conflicts",
      _a_fenced_key_still_identifies_ONE_operation)


def _pre_transaction_never_says_not_applied():
    """`orgtree_retire` waits for the target's turn boundary BEFORE the
    transaction. A missing receipt cannot speak for that wait, so the answer
    is `unknown` even though the document effect is now fenced."""
    slug = fresh_org()
    key = k()
    ans = lookup(slug, "mid", key, "orgtree_retire", node="worker")
    assert ans["state"] == "unknown", ans
    assert ans["reason"] == "pre_transaction_step", ans
    assert ans["fenced"] is True, ans
    assert ans["coverage"] == opreceipts.PRE, ans
    # the fence still holds for the document half
    st, js = call(slug, "mid", "orgtree_retire", key=key, node="worker")
    assert st == 422 and "fenced" in str(js), (st, js)
    assert store.load_org(slug).d["nodes"]["worker"].get("archived") in (None, False)


check("a verb with work outside the transaction answers unknown, never "
      "not_applied", _pre_transaction_never_says_not_applied)


# ================================================ §6 retention / watermark
print("\n§6  retention — eviction can never become permission")


def _seed(slug, n, base_ms=None, node="mid"):
    """n applied receipts, minted one ms apart, oldest first."""
    base = base_ms if base_ms is not None else int(time.time() * 1000) - n - 10
    org = store.load_org(slug)
    log = org.d.setdefault("op_receipts", [])
    for i in range(n):
        log.append(opreceipts.row(
            op_id=f"{i:016x}", node=node, generation=0,
            key=key_at(base + i), mint_ms=base + i, tool="orgtree_message",
            args={"to": "boss", "body": str(i)},
            cls=opreceipts.TX_POST, outcome="applied",
            at="2026-09-05T00:00:00.000000+00:00", result={}))
    org.d.setdefault("op_receipts_meta", {
        "schema": opreceipts.SCHEMA, "coverage": opreceipts.COVERAGE,
        "created_at": base - 1, "from_ms": 0,
        "horizon_ms": opreceipts.HORIZON_MS, "ceiling": opreceipts.CEILING,
        "trim_to": opreceipts.TRIM_TO, "evicted": 0})
    store.save_org(org)
    return base


def _trim_advances_past_max_mint():
    """⚠ PAST THE LARGEST MINT TIME EVICTED — not past the newest row's `at`.
    A key may legitimately be minted up to SKEW ahead of the server clock, so
    a watermark taken from wall-clock time can leave exactly that key
    admissible with its receipt gone. That is the one hole this design must
    not have."""
    slug = fresh_org()
    now_ms = int(time.time() * 1000)
    base = _seed(slug, opreceipts.CEILING)
    # the OLDEST row (first to be evicted) carries a future-skewed mint time
    org = store.load_org(slug)
    skewed = now_ms + opreceipts.SKEW_MS - 1_000
    org.d["op_receipts"][0]["mint_ms"] = skewed
    org.d["op_receipts"][0]["key"] = key_at(skewed)
    skewed_key = org.d["op_receipts"][0]["key"]
    store.save_org(org)
    # one more call crosses the ceiling and trims
    st, _ = call(slug, "mid", "orgtree_message", key=k(), to="boss", body="z")
    assert st == 200
    r, m = rows(slug), meta(slug)
    assert len(r) == opreceipts.TRIM_TO, len(r)
    assert m["evicted"] == opreceipts.CEILING + 1 - opreceipts.TRIM_TO, m
    assert m["from_ms"] > skewed, (m["from_ms"], skewed)
    # and the evicted, future-dated key is now REFUSED rather than admitted
    st, js = call(slug, "mid", "orgtree_message", key=skewed_key, to="boss",
                  body="0")
    assert st == 422 and "horizon_evicted" in str(js), (st, js)
    assert base is not None


check("eviction advances the watermark past the largest mint time it dropped",
      _trim_advances_past_max_mint)


def _evicted_key_cannot_double_apply():
    """applied → evicted → reloaded from disk (a restart reads exactly this)
    → the same key must NOT do it again."""
    slug = fresh_org()
    key = key_at(int(time.time() * 1000) - 1_000)
    st, _ = call(slug, "boss", "orgtree_hire", key=key, **hire_args("once"))
    assert st == 200 and "once" in nodes(slug)
    # evict it: fill the log past the ceiling with NEWER rows
    _seed(slug, opreceipts.CEILING, base_ms=int(time.time() * 1000) - 500)
    st, _ = call(slug, "mid", "orgtree_message", key=k(), to="boss", body="z")
    assert st == 200
    assert not [r for r in rows(slug) if r["key"] == key], "not evicted"
    before = nodes(slug)
    st, js = call(slug, "boss", "orgtree_hire", key=key, **hire_args("once"))
    assert st == 422, (st, js)
    assert "horizon_evicted" in str(js) or "UNKNOWN" in str(js), js
    assert nodes(slug) == before, "the evicted key hired a SECOND agent"
    # …and the lookup refuses to call it not-applied
    ans = lookup(slug, "boss", key, "orgtree_hire", **hire_args("once"))
    assert ans["state"] == "unknown", ans


check("an evicted applied key is refused, never re-executed",
      _evicted_key_cannot_double_apply)


def _watermark_is_monotonic():
    slug = fresh_org()
    d = store.load_org(slug).d
    m = opreceipts._meta(d, int(time.time() * 1000), create=True)
    m["from_ms"] = 9_000_000_000_000
    # an eviction whose rows are all OLDER must not lower it (a clock that
    # rolled back cannot re-open a window already closed)
    for i in range(opreceipts.CEILING + 1):
        opreceipts.append(d, opreceipts.row(
            op_id=f"{i:016x}", node="mid", generation=0, key=key_at(1_000 + i),
            mint_ms=1_000 + i, tool="orgtree_message", args={},
            cls=opreceipts.TX, outcome="applied", at="x"))
    assert opreceipts.watermark(d) == 9_000_000_000_000, opreceipts.watermark(d)


check("the watermark only ever increases", _watermark_is_monotonic)


# ==================================================== §7 coverage
print("\n§7  coverage — the table matches the dispatch, actions included")


def _coverage_is_complete():
    """⚠ THE ANTI-STALE CONTROL. Read the verbs off the dispatch SOURCE, the
    way test_mcptool does, so a tool added to the dispatch and forgotten here
    fails instead of silently defaulting."""
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "orgtree",
                            "api.py"), encoding="utf-8").read()
    slice_ = src[src.index('@app.post("/api/agent")'):src.index("_UPLOAD_MAX")]
    verbs = set(re.findall(r'"(orgtree_\w+)"', slice_))
    # ⚠ THE TWO RECEIPT VERBS ARE NOT IN THAT SLICE — they are constants in
    # opreceipts, spelled nowhere in the dispatch — so they would be exempt by
    # ACCIDENT. Put them in, then take them out on purpose, and prove the
    # exemption is not covering anything: neither is an operation, so neither
    # can carry a receipt.
    verbs |= {opreceipts.OP_CALL, opreceipts.OP_LOOKUP}
    for v in (opreceipts.OP_CALL, opreceipts.OP_LOOKUP):
        assert not opreceipts.receipted(v, {}), v
        verbs.discard(v)
    missing = sorted(v for v in verbs if not opreceipts.coverage(v, {}))
    assert not missing, f"dispatch verbs with no coverage class: {missing}"
    # positive control: this check CAN fail — an unclassified verb is caught
    assert not opreceipts.coverage("orgtree_not_a_real_verb", {})
    assert "orgtree_message" in verbs, "the dispatch scan found nothing"
    carded = {c["name"] for c in mcptool.TOOLS}
    unclassified = sorted(c for c in carded if not opreceipts.coverage(c, {}))
    assert not unclassified, f"carded tools with no coverage class: {unclassified}"


check("every dispatch verb and every card has a coverage class",
      _coverage_is_complete)


def _coverage_by_action():
    # multipurpose verbs are classified by ACTION, not by name
    assert opreceipts.coverage("orgtree_work", {"action": "list"}) == opreceipts.NONE
    assert opreceipts.coverage("orgtree_work", {"action": "get"}) == opreceipts.NONE
    assert opreceipts.coverage("orgtree_work", {"action": "verify"}) == opreceipts.NONE
    assert opreceipts.coverage("orgtree_work", {"action": "update"}) == opreceipts.TX
    assert opreceipts.coverage("orgtree_watchdog", {"action": "create"}) == opreceipts.TX_POST
    assert opreceipts.coverage("orgtree_watchdog", {"action": "pause"}) == opreceipts.TX
    # …and a rehire that also RENAMES moves folders before the transaction
    assert opreceipts.coverage("orgtree_rehire", {"node": "x"}) == opreceipts.TX_POST
    assert opreceipts.coverage("orgtree_rehire", {"node": "x", "name": "y"}) == opreceipts.PRE
    # verbs whose side effect is a file or a process, read off their branches
    for tool in ("orgtree_restart_wake", "orgtree_cheap_compact",
                 "orgtree_interrupt", "orgtree_self_restart",
                 "orgtree_prime_restart"):
        assert opreceipts.coverage(tool, {}) == opreceipts.UNROLLED, tool
        assert not opreceipts.provable_absence(opreceipts.coverage(tool, {})), tool


check("coverage follows the arguments and the actual side effects",
      _coverage_by_action)


def _no_receipt_for_none_class():
    slug = fresh_org()
    key = k()
    st, _ = call(slug, "mid", "orgtree_work", key=key, action="list")
    assert st == 200
    assert not rows(slug), "a read action filed a receipt"


check("a NONE-class call files nothing even when a key rides it",
      _no_receipt_for_none_class)


# ============================================ §8 what is not stored/claimed
print("\n§8  what a receipt does NOT store, and does not claim")


def _no_bodies():
    slug = fresh_org()
    secret = "the-quick-brown-fox-must-not-be-stored"
    st, _ = call(slug, "mid", "orgtree_message", key=k(), to="boss",
                 body=secret)
    assert st == 200
    blob = repr(rows(slug)) + repr(meta(slug))
    assert secret not in blob, "the mail body is in the receipt log"
    st, _ = call(slug, "boss", "orgtree_hire", key=k(),
                 **{**hire_args("charterful"), "charter": secret})
    assert st == 200
    blob = repr(rows(slug))
    assert secret not in blob, "the charter is in the receipt log"
    # positive control: the fingerprint DOES follow the body it refuses to keep
    f1 = opreceipts.fingerprint("orgtree_message", "mid", 0,
                                {"to": "boss", "body": "a"})
    f2 = opreceipts.fingerprint("orgtree_message", "mid", 0,
                                {"to": "boss", "body": "b"})
    assert f1 != f2


check("bodies and charters are never stored, but the fingerprint follows them",
      _no_bodies)


def _event_bracket():
    slug = fresh_org()
    before = len(store.load_org(slug).d["events"])
    st, _ = call(slug, "mid", "orgtree_message", key=k(), to="boss", body="a")
    assert st == 200
    row = rows(slug)[0]
    ev = store.load_org(slug).d["events"]
    assert row["ev_from"] == before, (row["ev_from"], before)
    assert row["ev_to"] == len(ev), (row["ev_to"], len(ev))
    assert row["ev_to"] > row["ev_from"], row
    kinds = {e["op"] for e in ev[row["ev_from"]:row["ev_to"]]}
    assert kinds == {"mail"}, kinds


check("the receipt brackets exactly the DOCUMENT events its call produced",
      _event_bracket)


# ==================================================== §9 compatibility
print("\n§9  compatibility — old documents, no key, JSON")


def _an_export_is_stamped_as_a_snapshot():
    """⚠ ASTRA, 2026-09-05T12:20Z: do not promise retention through a restore.

    A document rolled back from an export loses the receipts written after it
    — but NOT their effects: mail already delivered to another org, a process
    already started. So a key minted after the snapshot cannot be judged from
    the restored document, and the answer must be `unknown` rather than the
    "never applied, safe to reissue" that an empty log otherwise reads as."""
    slug = fresh_org()
    call(slug, "mid", "orgtree_message", key=k(), to="boss", body="a")
    if store.STORE_BACKEND == "sqlite":
        exported = json.load(open(store.export_json(slug), encoding="utf-8"))
    else:                      # JSON backend: the document IS the export
        exported = json.loads(json.dumps(store.load_org(slug).d))
        opreceipts.stamp_export(exported)
    stamp = opreceipts.export_stamp(exported)
    assert stamp, exported.get("op_receipts_meta")

    # ⚠ THE RESTORE happens later than the export, and the key that matters
    # was minted BEFORE the snapshot (Astra's correction, 12:31Z): its
    # operation may have applied AFTER the snapshot, which leaves it missing
    # from the restored document exactly like a late-minted one. A mint time
    # cannot separate those, so neither may be answered "not applied".
    restore_ms = stamp + 60_000
    for label, mint_ms in (("minted before the snapshot", stamp - 5_000),
                           ("minted after the snapshot", stamp + 1_000)):
        d = json.loads(json.dumps(exported))          # a fresh restore each time
        decision, info = opreceipts.admit(
            d, "mid", 0, opreceipts.mint_key(mint_ms), "orgtree_message",
            {"to": "boss"}, now_ms=restore_ms)
        assert decision == opreceipts.REFUSE, (label, decision, info)
        assert info["reason"] == "restored_from_export", (label, info)

    # …and a receipt the snapshot DOES contain still reads normally: absence
    # is what a restored document cannot speak to, not the rows it has.
    old_row = exported["op_receipts"][0]
    assert opreceipts.find(exported, "mid", old_row["key"]) is old_row

    # the first touch converts the stamp into an ordinary watermark at THAT
    # moment — not at the export's — and a key minted after the restore is
    # covered normally, because its whole life is inside this document.
    assert opreceipts.resume_after_restore(exported, restore_ms)
    assert not opreceipts.export_stamp(exported)
    assert opreceipts.watermark(exported) == restore_ms
    assert not opreceipts.resume_after_restore(exported, restore_ms + 1), \
        "the conversion ran twice"
    fresh = opreceipts.mint_key(restore_ms + 1_000)
    decision, info = opreceipts.admit(exported, "mid", 0, fresh,
                                      "orgtree_message", {"to": "boss"},
                                      now_ms=restore_ms + 2_000)
    assert decision == opreceipts.ADMIT, (decision, info)


check("a document restored from an export answers unknown, never not_applied",
      _an_export_is_stamped_as_a_snapshot)


def _client_against_a_backend_without_receipts():
    """A client of THIS build talking to a backend that has none. The old
    build refuses the wrapper verb and applies nothing (measured against
    a0fac2f in `luna-reserve/probe_old_build.py`), so the client reissues the
    call plainly — and the operation must still happen, exactly once.

    The other half is what a LOST answer means afterwards: no receipt covers
    that call, so the honest report is `unknown`, and the client must NOT ask
    a lookup the same backend cannot answer either."""
    slug = fresh_org()
    seen: list[tuple[str, str]] = []
    real_post = mcptool._post

    def old_build(payload, timeout=30):
        tool = str(payload.get("tool") or "")
        seen.append((tool, str((payload.get("args") or {}).get("tool") or "")))
        if tool in (opreceipts.OP_CALL, opreceipts.OP_LOOKUP):
            # the pre-receipts dispatch, word for word
            return "refused", ('{"detail":"unknown orgtree tool '
                               f"'{tool}'" '"}')
        # ⚠ THROUGH THE TEST CLIENT, never `_post`: that one opens a real
        # socket to whatever backend is running on this machine.
        r = client.post("/api/agent", json=payload)
        return ("ok" if r.status_code == 200 else "refused"), r.text

    mcptool.ORG, mcptool.NODE = slug, "mid"
    mcptool._post = old_build
    try:
        out = mcptool.call_api("orgtree_message", {"to": "boss", "body": "x"})
        assert '"delivered"' in out, out
        assert len((store.load_org(slug).d.get("mail") or {}).get("boss")) == 1
        assert not rows(slug), "an old build cannot have filed a receipt"
        assert [t for t, _ in seen] == [opreceipts.OP_CALL, "orgtree_message"]

        # …and now the lost answer. `unknown`, with no lookup attempted.
        seen.clear()

        def old_build_lost(payload, timeout=30):
            tool = str(payload.get("tool") or "")
            seen.append((tool, ""))
            if tool in (opreceipts.OP_CALL, opreceipts.OP_LOOKUP):
                return "refused", ('{"detail":"unknown orgtree tool '
                                   f"'{tool}'" '"}')
            return "lost", "timed out"

        mcptool._post = old_build_lost
        lost = json.loads(mcptool.call_api("orgtree_message",
                                           {"to": "boss", "body": "y"}))
        assert lost["state"] == "unknown", lost
        assert lost["reason"] == "unsupported_build", lost
        assert opreceipts.OP_LOOKUP not in [t for t, _ in seen], seen

        # …and against a backend that DOES have receipts, an ordinary refusal
        # is reported once. Retrying it unkeyed would strip the receipt off a
        # call the server merely disliked, which is how a client-side
        # "fall back on any refusal" turns into an unprotected reissue.
        seen.clear()

        def modern(payload, timeout=30):
            seen.append((str(payload.get("tool") or ""), ""))
            r = client.post("/api/agent", json=payload)
            return ("ok" if r.status_code == 200 else "refused"), r.text

        mcptool._post = modern
        out = json.loads(mcptool.call_api("orgtree_message",
                                          {"to": "nobody", "body": "z"}))
        assert "error" in out, out
        assert [t for t, _ in seen] == [opreceipts.OP_CALL], seen
    finally:
        mcptool._post = real_post


check("a client of this build never leaves an unrecorded effect on an old one",
      _client_against_a_backend_without_receipts)


def _no_key_no_section():
    slug = fresh_org()
    st, _ = call(slug, "mid", "orgtree_message", to="boss", body="a")
    assert st == 200
    d = store.load_org(slug).d
    assert "op_receipts" not in d, "an unkeyed call created the section"
    assert "op_receipts_meta" not in d, d.get("op_receipts_meta")


check("a call with no key touches nothing — old clients pay nothing",
      _no_key_no_section)


def _old_document_round_trip():
    slug = fresh_org()
    # an old document has neither key; it must load, save and reload
    org = store.load_org(slug)
    org.d.pop("op_receipts", None)
    org.d.pop("op_receipts_meta", None)
    store.save_org(org)
    assert not rows(slug) and not meta(slug)
    st, _ = call(slug, "mid", "orgtree_message", key=k(), to="boss", body="a")
    assert st == 200
    assert len(rows(slug)) == 1
    # and it survives a second full round trip through the store
    org = store.load_org(slug)
    store.save_org(org)
    assert len(rows(slug)) == 1, rows(slug)
    assert meta(slug)["schema"] == opreceipts.SCHEMA


check("a document with no receipt section reads as empty and starts one",
      _old_document_round_trip)


def _section_is_a_list_log():
    # the classification the storage layer applies — rows, lazily, not a blob
    assert "op_receipts" in store.LIST_LOGS
    assert "op_receipts" in store.LAZY_SECTIONS
    assert "op_receipts_meta" not in store.LAZY_SECTIONS   # small + eager


check("the receipt log is a lazy row-backed section; its meta is eager",
      _section_is_a_list_log)


print(f"\n{PASSED} passed, {len(FAILED)} failed")
for f in FAILED:
    print("\n" + f)
sys.exit(1 if FAILED else 0)
