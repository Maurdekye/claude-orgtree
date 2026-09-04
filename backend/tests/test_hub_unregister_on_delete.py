"""Deleting an org takes the polite exit from every mail hub it is on.

THE DEFECT. `POST /api/unregister` has existed on the hub since 2026-08-06 -
"the polite exit", written in the same wave that added the roster prune - and
NOTHING in this backend ever called it. `hubtool.unregister_identity` gave a
chat identity the verb; an ORG had none. So a deleted org's roster row
outlived it by up to `ORG_RETENTION_DAYS`, and the compose picker went on
offering it as a recipient that can never receive anything. Measured on the
operator's live hub 2026-09-04: 135 rows, 132 of them with no local org of
that base slug, 3 online.

It is the route built with no caller - the same shape as the org charter that
reached zero agents and the standing notes nothing read.

WHAT THIS PINS, and the boundaries matter more than the happy path:

  * the call happens, authenticated as the org's OWN identity, on every
    ENABLED hub and no other;
  * it can NEVER fail a delete. A hub that is down, slow or answering 500 is
    an ordinary condition. An org that could not be deleted because some
    unrelated machine was unreachable would be a worse defect than the stale
    row it was trying to avoid - and the row still ages out on the hub's own
    retention;
  * a 401 is SUCCESS, not an error: the hub already does not know us, which
    is the goal state;
  * the org's `net_identity` survives. Deletion here is a REVERSIBLE RENAME
    into `<data>/deleted/`, so a restored org must re-register with the same
    secret and get the IDENTICAL address back. Dropping a remote row and
    destroying a local identity are different destructive acts and this
    performs only the first.

⚠ WHAT IS DELIBERATELY NOT HERE: any sweep of rows an observer merely cannot
see. A roster row whose org is absent from THIS machine may be an org living
on another install pointed at the same hub - "I cannot see it" is a fact about
the observer, not about the org. Only the holder of the secret can prove the
org is gone, which is exactly who this is.

Falsifiers, all verified to turn exactly their own group red:

M1 snapshot the doc AFTER delete_org (nothing to auth with) -> delivery FAILS
M2 a hub error propagates out of the delete            -> resilience FAILS
M3 treat 401 as an error                                     -> semantics FAILS
M4 include disabled hubs                                     -> scope FAILS
M5 destroy net_identity on the way out                       -> restore FAILS

Hermetic: throwaway data root, a loopback stub for the hub, no real hub
contacted and no production journal.

    python backend/tests/test_hub_unregister_on_delete.py
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

RIG = tempfile.mkdtemp(prefix="orgtree-unregister-")
DATA = os.path.join(RIG, "data")
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["USERPROFILE"] = os.path.join(RIG, "home")
os.environ["HOME"] = os.path.join(RIG, "home")
os.environ["ORGTREE_PORT"] = "7420"             # never bound
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree import net, store                                  # noqa: E402
from orgtree.ledger import USER                                 # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                       # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


# --------------------------------------------------------------- a stub hub
class _Hub(http.server.BaseHTTPRequestHandler):
    status = 200
    seen: list[dict[str, object]] = []

    def do_POST(self) -> None:                              # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        type(self).seen.append({"path": self.path,
                                "auth": self.headers.get("X-Org-Auth")})
        body = json.dumps({"unregistered": []}).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a: object) -> None:        # keep the run quiet
        return


def start_hub() -> tuple[str, type[_Hub]]:
    """A fresh handler CLASS per hub, so `seen` and `status` are its own."""
    cls = type("_HubN", (_Hub,), {"seen": [], "status": 200})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    SERVERS.append(srv)
    return f"http://127.0.0.1:{srv.server_address[1]}", cls


SERVERS: list[http.server.ThreadingHTTPServer] = []
HUB_A, CLS_A = start_hub()
HUB_B, CLS_B = start_hub()
DEAD = "http://127.0.0.1:9"                    # discard port: refuses at once


def make_org(name: str, hubs: list[dict[str, object]]) -> str:
    org = store.create_org(name)
    net.mint_identity(org)
    org.d["net_hubs"] = hubs
    store.save_org(org)
    return str(org.d["slug"])


def doc_of(slug: str) -> dict:
    return dict(store.load_org(slug).d)


def reset() -> None:
    CLS_A.seen.clear()
    CLS_B.seen.clear()
    CLS_A.status = 200
    CLS_B.status = 200


def trashed_docs() -> list[str]:
    d = os.path.join(DATA, "deleted")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


# ------------------------------------------------------------ group delivery
def t_every_enabled_hub_is_told() -> None:
    reset()
    slug = make_org("exit both", [
        {"id": "a", "address": HUB_A, "enabled": True},
        {"id": "b", "address": HUB_B, "enabled": True}])
    doc = doc_of(slug)
    out = net.unregister_org(doc)
    assert sorted(out["unregistered"]) == sorted([HUB_A, HUB_B]), out
    for cls in (CLS_A, CLS_B):
        assert len(cls.seen) == 1, cls.seen
        assert cls.seen[0]["path"] == "/api/unregister", cls.seen


def t_the_call_authenticates_as_this_orgs_own_identity() -> None:
    """The hub deletes only the caller's own slugs, so the auth header IS the
    proof of ownership. If this were ever sent with someone else's pair, or
    with none, the request would remove the wrong row or nothing at all."""
    reset()
    slug = make_org("exit auth", [{"id": "a", "address": HUB_A,
                                   "enabled": True}])
    doc = doc_of(slug)
    ident = doc["net_identity"]
    net.unregister_org(doc)
    assert len(CLS_A.seen) == 1, CLS_A.seen
    assert CLS_A.seen[0]["auth"] == f"{ident['slug']}:{ident['secret']}", (
        f"the unregister did not authenticate as this org: "
        f"{CLS_A.seen[0]['auth']!r}")


def t_an_org_with_no_identity_calls_nothing() -> None:
    """A kiosk mints no identity by design. It must not produce a call, and
    must not raise on the way past."""
    reset()
    org = store.create_org("no identity")
    org.d["net_hubs"] = [{"id": "a", "address": HUB_A, "enabled": True}]
    store.save_org(org)
    out = net.unregister_org(dict(org.d))
    assert out["unregistered"] == [], out
    assert not CLS_A.seen, "a hub was contacted for an org with no identity"


# --------------------------------------------------------------- group scope
def t_a_disabled_hub_is_left_alone() -> None:
    """A disabled entry is an address the operator has switched OFF. Calling
    it would contact a machine they told us not to, and would reset the
    per-address backoff for a hub nothing else is talking to."""
    reset()
    slug = make_org("exit disabled", [
        {"id": "a", "address": HUB_A, "enabled": True},
        {"id": "b", "address": HUB_B, "enabled": False}])
    out = net.unregister_org(doc_of(slug))
    assert out["unregistered"] == [HUB_A], out
    assert len(CLS_A.seen) == 1, CLS_A.seen
    assert not CLS_B.seen, "a DISABLED hub was contacted"


# ---------------------------------------------------------- group resilience
def t_a_dead_hub_does_not_fail_the_delete() -> None:
    """THE PROPERTY THAT MATTERS MOST. An org that cannot be deleted because
    an unrelated machine is unreachable would be far worse than the stale row
    this exists to prevent."""
    reset()
    slug = make_org("exit dead hub", [
        {"id": "a", "address": DEAD, "enabled": True},
        {"id": "b", "address": HUB_A, "enabled": True}])
    doc = doc_of(slug)
    out = net.unregister_org(doc)          # must not raise
    assert out["unregistered"] == [HUB_A], out
    assert DEAD in (out.get("errors") or {}), out
    store.delete_org(slug)                 # and the delete still goes through
    assert slug not in {o["slug"] for o in store.list_orgs()}, (
        "the org survived its own deletion")


def t_a_hub_error_is_reported_not_raised() -> None:
    reset()
    CLS_A.status = 500
    slug = make_org("exit http500", [{"id": "a", "address": HUB_A,
                                      "enabled": True}])
    out = net.unregister_org(doc_of(slug))
    assert out["unregistered"] == [], out
    assert out["errors"][HUB_A] == "HTTP 500", out


# ----------------------------------------------------------- group semantics
def t_a_401_is_success_because_the_goal_state_is_reached() -> None:
    """The hub already does not know us - a prune, a rebuilt volume, or a
    second delete. Reporting that as a failure would put an error in front of
    the user for the one outcome they wanted."""
    reset()
    CLS_A.status = 401
    slug = make_org("exit 401", [{"id": "a", "address": HUB_A,
                                  "enabled": True}])
    out = net.unregister_org(doc_of(slug))
    assert out["unregistered"] == [HUB_A], out
    assert not out.get("errors"), out


# ------------------------------------------------------------- group restore
def t_the_identity_survives_so_a_restore_gets_the_same_address() -> None:
    """Deletion here is a REVERSIBLE RENAME into <data>/deleted/. Putting the
    file back IS the restore, and the restored org must come back as ITSELF:
    same secret, same fingerprint, so the hub re-mints the identical address
    on the next 401 self-heal. Dropping a remote row and destroying a local
    identity are different destructive acts; only the first is wanted."""
    reset()
    slug = make_org("exit restore", [{"id": "a", "address": HUB_A,
                                      "enabled": True}])
    before = doc_of(slug)["net_identity"]
    doc = doc_of(slug)
    net.unregister_org(doc)
    after = store.load_org(slug).d["net_identity"]
    assert after == before, (
        "unregistering changed the org's own network identity - a restore "
        "would come back as a stranger with a new address")
    assert doc["net_identity"] == before, (
        "the snapshot handed to unregister_org was mutated")


def t_the_snapshot_must_be_taken_before_the_rename() -> None:
    """THE ORDERING, pinned as behaviour rather than as a comment. Once
    delete_org has renamed the document away, `load_org` raises and there is
    no secret left to authenticate with - so a caller that snapshots
    afterwards silently sends nothing at all."""
    reset()
    slug = make_org("exit ordering", [{"id": "a", "address": HUB_A,
                                       "enabled": True}])
    doc = doc_of(slug)                     # BEFORE, as orgs_delete does
    store.delete_org(slug)
    try:
        store.load_org(slug)
        raise AssertionError(
            "load_org still resolves a deleted org - this check no longer "
            "demonstrates why the snapshot must come first")
    except Exception as e:                                   # noqa: BLE001
        assert "no such org" in str(e).lower() or isinstance(e, OSError), e
    out = net.unregister_org(doc)          # the snapshot still works
    assert out["unregistered"] == [HUB_A], out
    assert trashed_docs(), "delete_org left nothing in the trash to restore"


# ------------------------------------------------------------- group wiring
def t_the_delete_endpoint_takes_the_exit() -> None:
    """End to end through the real handler, because everything above would
    pass just as well if nothing ever called it - which was the defect."""
    reset()
    from orgtree import api
    slug = make_org("exit endpoint", [{"id": "a", "address": HUB_A,
                                       "enabled": True}])
    res = api.orgs_delete(slug)
    assert res.get("ok") is True, res
    assert len(CLS_A.seen) == 1, (
        "DELETE /api/orgs/{slug} did not unregister from the hub")
    assert CLS_A.seen[0]["path"] == "/api/unregister", CLS_A.seen
    assert res["net"]["unregistered"] == [HUB_A], res
    assert slug not in {o["slug"] for o in store.list_orgs()}


def t_the_delete_endpoint_survives_a_dead_hub() -> None:
    reset()
    from orgtree import api
    slug = make_org("exit endpoint dead", [{"id": "a", "address": DEAD,
                                            "enabled": True}])
    res = api.orgs_delete(slug)            # must not raise
    assert res.get("ok") is True, res
    assert slug not in {o["slug"] for o in store.list_orgs()}, (
        "an unreachable hub prevented an org from being deleted")


def main() -> int:
    print("group delivery: the call happens, as us")
    check("every ENABLED hub is told", t_every_enabled_hub_is_told)
    check("the call authenticates as this org's own identity",
          t_the_call_authenticates_as_this_orgs_own_identity)
    check("an org with no identity calls nothing and does not raise",
          t_an_org_with_no_identity_calls_nothing)
    print("group scope: and no hub that was not asked for")
    check("a DISABLED hub is left alone", t_a_disabled_hub_is_left_alone)
    print("group resilience: it can never fail a delete")
    check("a dead hub does not fail the delete",
          t_a_dead_hub_does_not_fail_the_delete)
    check("an HTTP error is reported, not raised",
          t_a_hub_error_is_reported_not_raised)
    print("group semantics: what the answers mean")
    check("a 401 is success - the hub already does not know us",
          t_a_401_is_success_because_the_goal_state_is_reached)
    print("group restore: delete is a reversible rename")
    check("the org's identity survives, so a restore keeps its address",
          t_the_identity_survives_so_a_restore_gets_the_same_address)
    check("the snapshot must be taken before the rename",
          t_the_snapshot_must_be_taken_before_the_rename)
    print("group wiring: something actually calls it")
    check("DELETE /api/orgs/{slug} takes the exit",
          t_the_delete_endpoint_takes_the_exit)
    check("...and still deletes when the hub is dead",
          t_the_delete_endpoint_survives_a_dead_hub)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n[X] {label}\n{tb}")
        print(f"hub-unregister: {PASS} passed - {len(FAIL)} FAILED")
        return 1
    print(f"hub-unregister: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for s in SERVERS:
            s.shutdown()
        shutil.rmtree(RIG, ignore_errors=True)
    sys.exit(rc)
