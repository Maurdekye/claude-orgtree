"""HTTP-surface adversarial suite — every endpoint and all three listeners.

    python backend/tests/test_api_surface.py        (no pytest; plain asserts)

WHAT THIS TESTS AND WHY IT LOOKS LIKE THIS

api.py serves one FastAPI app behind three wrappers with three trust levels:
the ADMIN app (loopback, no auth of any kind — "you can reach 127.0.0.1" IS
the credential), the PublicGateway (`/k/<token>`, an internet-facing kiosk
visitor), and the BridgeGateway (the one door out of a sandbox container,
gated by the org's secret). The security model is that those three cannot leak
into each other, so most of this file is isolation rather than happy paths.

⚠ Requests are made by calling the ASGI app DIRECTLY with a hand-built scope,
not through an HTTP client. That is deliberate: httpx/requests normalise a URL
before it goes on the wire, so `..`, `//`, `%2f` and case games never reach the
gateway — and the gateway is what they are aimed at. A hand-built scope is
exactly what uvicorn hands the app after decoding the request target, so this
delivers what an attacker can really deliver. §11 then puts a REAL uvicorn on
port 7402 and speaks raw HTTP to it, which is what proves the assumption the
rest of the file rests on (that percent-decoding happens below us).

Nothing here spawns a turn, a container or a CLI. ORGTREE_DATA is a throwaway
temp dir (org docs are re-slugified from name, so a shared data dir makes the
fixtures collide), chatq registration and sandbox warm-up are stubbed so the
suite never touches ~/.claude/chatq or Docker, and every fixture org is created
through the API so the create path is covered too.

Two checks assert a KNOWN GAP rather than a fix — they are labelled ⚑ and each
says in its body what should happen the day the gap is closed.

    §1  fixtures + the public token gate
    §2  the public restriction matrix (frozen vs open)
    §3  path tricks against the matrix
    §4  cross-org isolation
    §5  secrets never leave loopback
    §6  error scrubbing for public visitors
    §7  the sandbox bridge
    §8  /api/agent — the MCP gateway
    §9  uploads, scratch and path traversal (+ §9b the org-disk browser)
    §10 failure modes — nothing 500s
    §11 raw HTTP against a real uvicorn on :7402
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# an isolated data root BEFORE any orgtree import: store resolves ORGTREE_DATA
# at import time. PUBLIC_PORT must be non-zero too, or _share_url() returns
# None and every "no share_url in a public payload" check passes vacuously.
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-apitest-")

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: net._default_address
# falls back to net.DEFAULT_HUB_ADDRESS — the operator's real hub — when this
# root has no defaults.json, and any rig that starts the net daemon then
# registers its fixture orgs there permanently. Measured twice (user report
# 2026-08-06; ~45 fixture orgs again on 2026-08-10). The discard port refuses
# instantly, so registration fails harmlessly into the backoff.
# Guarded over this whole directory by test_external_mail §1.
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

os.environ["ORGTREE_PORT"] = "7402"
os.environ["ORGTREE_PUBLIC_PORT"] = "7402"
os.environ.pop("ORGTREE_EXPOSE_ADMIN", None)

from orgtree import api, sandbox, store, supervisor            # noqa: E402
from orgtree import disk as dsk                                # noqa: E402

# no chatq registry writes, no Docker, no host storage walks
supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
supervisor.storage_check = lambda slug: None
sandbox.warm = lambda org: None
sandbox.vm_disk_cap_mib = lambda: None

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def t(label):
    """Define a check body and run it immediately — same contract as
    check(label, fn), but the body cannot forward-reference anything."""
    def deco(fn):
        check(label, fn)
        return fn
    return deco


GAPS = []


def gap(label, why):
    """A property that SHOULD hold and currently does not (idiom borrowed from
    test_rename.py). The body asserts the SAFE behaviour and is expected to
    FAIL: the suite stays green, the finding is printed every run instead of
    rotting in a report, and the day it is fixed the check passes unexpectedly
    and turns RED — which is the reminder to promote it to a real `t`."""
    def deco(fn):
        try:
            fn()
        except AssertionError as e:
            GAPS.append((label, why, str(e).split("\n")[0][:300]))
            print(f"  ⚑ GAP    {label}")
            return fn
        check(label + "  ← FIXED: promote out of gap()", lambda: None)
        return fn
    return deco


ADMIN = api.app
PUBLIC = api.PublicGateway(api.app)
BRIDGE = api.BridgeGateway(api.app)


class R:
    """One response: status, raw body, parsed json, and the exception that
    ESCAPED — which is what uvicorn turns into a bare 500."""

    def __init__(self, status, body, exc=None, headers=None):
        self.status, self.body, self.exc = status, body, exc
        #: lower-cased name → value, as they went on the wire
        self.headers = {k.decode().lower(): v.decode("utf-8", "replace")
                        for k, v in (headers or [])}
        try:
            self.json = json.loads(body)
        except Exception:                                      # noqa: BLE001
            self.json = None

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def __repr__(self):
        return f"<{self.status} {(self.exc or self.text)[:200]!r}>"


def call(app, method, path, body=None, headers=None, query=b"", raw=None):
    """Invoke an ASGI app with a hand-built scope: `path` goes through
    verbatim, with no client normalisation between here and the gateway."""
    payload = raw if raw is not None else (
        b"" if body is None else json.dumps(body).encode())
    hdrs = [(b"host", b"127.0.0.1:7402")]
    if payload:
        hdrs += [(b"content-type", b"application/json"),
                 (b"content-length", str(len(payload)).encode())]
    for k, v in (headers or []):
        hdrs.append((k.lower().encode(), v.encode()))
    st, chunks, exc, rhdrs = [0], [], [None], [[]]

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            st[0] = msg["status"]
            rhdrs[0] = list(msg.get("headers") or [])
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode("utf-8", "surrogateescape"),
             "query_string": query, "root_path": "", "headers": hdrs,
             "client": ("127.0.0.1", 5555), "server": ("127.0.0.1", 7402)}
    try:
        asyncio.run(app(scope, receive, send))
    except Exception as e:                                     # noqa: BLE001
        st[0] = st[0] or 500
        exc[0] = f"{type(e).__name__}: {e}"
    return R(st[0], b"".join(chunks), exc[0], rhdrs[0])


def ws_call(app, path):
    """Open a websocket through a gateway → ('accept'|'close'|'none', code)."""
    got = []

    async def receive():
        await asyncio.sleep(0)
        return {"type": "websocket.connect"}

    async def send(msg):
        got.append(msg)
        if msg["type"] == "websocket.accept":
            raise RuntimeError("stop-after-accept")

    scope = {"type": "websocket", "asgi": {"version": "3.0"}, "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"host", b"127.0.0.1:7402")], "scheme": "ws",
             "client": ("127.0.0.1", 5555), "server": ("127.0.0.1", 7402),
             "subprotocols": []}
    try:
        asyncio.run(app(scope, receive, send))
    except Exception:                                          # noqa: BLE001
        pass
    for m in got:
        if m["type"] == "websocket.accept":
            return ("accept", None)
        if m["type"] == "websocket.close":
            return ("close", m.get("code"))
    return ("none", None)


def no500(r, what):
    assert r.exc is None, f"{what} raised {r.exc}"
    assert r.status != 500, f"{what} → 500: {r.text[:200]}"
    assert r.status < 500 or r.status == 503, f"{what} → {r.status}"


def denied(r, what):
    assert r.status in (403, 404), f"{what} should be denied, got {r!r}"


def ok200(r, what):
    assert r.status == 200, f"{what} should have succeeded, got {r!r}"


# ---------------------------------------------------------------- §1 fixtures
print("§1  fixtures + the public token gate")

_r = call(ADMIN, "POST", "/api/orgs", {"name": "Admin Org"})
assert _r.status == 200, _r
ADMIN_SLUG = _r.json["slug"]
_r = call(ADMIN, "POST", "/api/orgs",
          {"name": "Kiosk One", "kiosk": {"sandbox": False, "credits": 30}})
assert _r.status == 200, _r
K = _r.json["slug"]
_r = call(ADMIN, "POST", "/api/orgs",
          {"name": "Kiosk Two", "kiosk": {"sandbox": False, "credits": 30}})
K2 = _r.json["slug"]
_r = call(ADMIN, "POST", "/api/orgs", {"name": "Sandy", "sandbox": True})
SBX = _r.json["slug"]

TOKEN = store.load_org(K).d["kiosk"]["token"]
TOKEN2 = store.load_org(K2).d["kiosk"]["token"]
SECRET = store.load_org(SBX).d["sandbox"]["secret"]
KSECRET = store.load_org(K).d["kiosk"]["sandbox_secret"]

_r = call(ADMIN, "POST", f"/api/orgs/{K}/ops",
          {"op": "hire", "tier": "opus", "name": "boss", "grant": 10})
assert _r.status == 200, _r
NID = _r.json["node"]
_r = call(ADMIN, "POST", f"/api/orgs/{K}/ops",
          {"op": "hire", "tier": "haiku", "name": "helper", "parent": NID,
           "grant": 0})
assert _r.status == 200, _r
NID2 = _r.json["node"]
_r = call(ADMIN, "POST", f"/api/orgs/{SBX}/ops",
          {"op": "hire", "tier": "opus", "name": "sbxboss", "grant": 10})
SNID = _r.json["node"]


def pub(method, path, body=None, **kw):
    return call(PUBLIC, method, f"/k/{TOKEN}{path}", body, **kw)


@t("four fixture orgs exist (2 kiosks, 1 sandboxed, 1 plain)")
def _():
    for s in (ADMIN_SLUG, K, K2, SBX):
        store.load_org(s)


@t("a kiosk carries a token AND a separate sandbox secret")
def _():
    assert len(TOKEN) == 32 and len(KSECRET) == 32
    assert TOKEN != KSECRET and TOKEN != TOKEN2 and SECRET != KSECRET


@t("hire with above= splices atomically: slot, parent and ordinal in one op")
def _():
    # FR-25 rework (2026-08-19): the anchor rides the hire op and the server
    # does hire + ordinal pin + move inside one lock/save — the old client
    # chain (hire, then a separate move) could strand a hired-but-unspliced
    # sibling on a mid-chain failure.
    r = call(ADMIN, "POST", "/api/orgs", {"name": "Splice Org"})
    s = r.json["slug"]
    kids = {}
    for nm in ("l", "anchor", "r"):
        rr = call(ADMIN, "POST", f"/api/orgs/{s}/ops",
                  {"op": "hire", "tier": "haiku", "name": nm, "grant": 0})
        assert rr.status == 200, rr
        kids[nm] = rr.json["node"]
    rr = call(ADMIN, "POST", f"/api/orgs/{s}/ops",
              {"op": "hire", "tier": "haiku", "name": "sup", "grant": 0,
               "above": kids["anchor"]})
    assert rr.status == 200, rr
    sup = rr.json["node"]
    assert rr.json.get("spliced") == kids["anchor"], rr.json
    org = store.load_org(s)
    assert org.nodes[kids["anchor"]]["parent"] == sup, "anchor not reparented"
    assert org.nodes[sup]["parent"] is None
    tops = org.children(None, live_only=False)
    assert tops == [kids["l"], sup, kids["r"]], \
        f"the hire did not take the anchor's ordinal slot: {tops}"
    # refusal is ALL-OR-NOTHING: a bad anchor refuses the whole op — no node
    # is created, nothing is saved
    before = set(store.load_org(s).nodes)
    rr = call(ADMIN, "POST", f"/api/orgs/{s}/ops",
              {"op": "hire", "tier": "haiku", "name": "ghost", "grant": 0,
               "above": "no-such-node"})
    assert rr.status == 422, rr
    assert set(store.load_org(s).nodes) == before, "a refused splice persisted"
    # anchor under a DIFFERENT parent than the hire's → refused up front
    rr = call(ADMIN, "POST", f"/api/orgs/{s}/ops",
              {"op": "hire", "tier": "haiku", "name": "ghost2", "grant": 0,
               "parent": sup, "above": kids["l"]})
    assert rr.status == 422 and "does not report to" in rr.json["detail"], rr
    assert set(store.load_org(s).nodes) == before, "a refused splice persisted"


@t("no token at all → 404, with no hint that anything exists")
def _():
    r = call(PUBLIC, "GET", "/api/orgs")
    assert r.status == 404 and r.json == {"detail": "not found"}, r


@t("a bogus token → 404")
def _():
    denied(call(PUBLIC, "GET", "/k/" + "f" * 32 + "/api/orgs"), "bogus token")


@t("a token too short for the gateway regex → 404")
def _():
    denied(call(PUBLIC, "GET", "/k/abc/api/orgs"), "short token")


@t("a case-flipped token is a different token → 404")
def _():
    denied(call(PUBLIC, "GET", f"/k/{TOKEN.upper()}/api/orgs"), "upper token")


@t("a truncated token is not a prefix match → 404")
def _():
    denied(call(PUBLIC, "GET", f"/k/{TOKEN[:-1]}/api/orgs"), "truncated")


@t("an extended token is not a match → 404")
def _():
    denied(call(PUBLIC, "GET", f"/k/{TOKEN}x/api/orgs"), "extended")


@t("a valid token reaches its own org's tree and it is marked public")
def _():
    r = pub("GET", f"/api/orgs/{K}")
    ok200(r, "own tree")
    assert r.json["slug"] == K and r.json["public"] is True, r


FALLBACK_UUID = "11111111-2222-3333-4444-555555555555"


@t("a fallback's uuid reaches the ADMIN tree and never the kiosk")
def _():
    """User ruling 2026-08-25: when an agent runs off a fallback, cite the
    fallback's number alongside its uuid. D-145 keeps account identity off the
    public side by freezing `/api/accounts` whole — but this label rides the
    NODE payload, which a kiosk visitor CAN read. So the ordinal crosses and
    the uuid does not, and that split is worth a real request rather than a
    grep over the source."""
    from orgtree import accounts, supervisor

    def find(node, nid):
        # the envelope nests under `roots`, each node under `children`
        if node.get("id") == nid:
            return node
        for kid in (node.get("children") or node.get("roots") or []):
            hit = find(kid, nid)
            if hit:
                return hit
        return None

    doc = accounts.load()
    keep = doc["keys"]
    doc["keys"] = [{"id": "kTESTROW01", "account_uuid": FALLBACK_UUID}]
    accounts.save(doc)
    try:
        supervisor.state(K, NID)["ran_as"] = "kTESTROW01"
        a = call(ADMIN, "GET", f"/api/orgs/{K}")
        ok200(a, "admin tree")
        p = pub("GET", f"/api/orgs/{K}")
        ok200(p, "kiosk tree")
        an, pn = find(a.json, NID), find(p.json, NID)
        assert an and pn, "the node is missing from one of the trees"
        assert an.get("ran_as_label") == f"fallback 1 · {FALLBACK_UUID}", an
        assert pn.get("ran_as_label") == "fallback 1", pn
        # ⚠ the strong form. Asserting the LABEL is scrubbed only proves this
        # one field; asserting the uuid is absent from the whole body catches
        # it leaking through `ran_as` or anything added later.
        assert FALLBACK_UUID not in p.text, "the uuid reached a kiosk visitor"
        # …and the control that makes that absence mean something: it really
        # is in the admin body, so the check is not passing on a fixture that
        # never carried the uuid in the first place.
        assert FALLBACK_UUID in a.text, "fixture never had the uuid at all"
    finally:
        supervisor.state(K, NID).pop("ran_as", None)
        doc = accounts.load()
        doc["keys"] = keep
        accounts.save(doc)


@t("tree · a limit freeze's reset is RE-DERIVED from the live roster, not the stamp")
def _():
    """User report 2026-08-26: "when an agent is stuck with usage limits, the
    refresh time doesn't adapt to account changes or keys being added /
    removed". `frozen.until`/`until_ts` are stamped ONCE by the supervisor and
    never rewritten, so the only honest place to correct them is where the
    payload is read.

    THREE STATES, asserted separately because each fails for its own reason,
    and the stamped label is never allowed to survive any of them. The middle
    leg is the user's literal scenario (a key is added); the FIRST is the
    control that has to fail — capacity genuinely absent, so a countdown must
    still appear and "capacity available" would be a lie.

    ⚠ `tier`, not `model`: the node DOCUMENT says `model`, `tree()` renames it
    on the way out. Reading the wrong one makes the whole feature a silent
    no-op, which is why leg ① asserts a re-derived VALUE rather than merely
    that the field exists."""
    import json as _json
    import time as _time
    from orgtree import accounts

    def find(node, nid):
        if node.get("id") == nid:
            return node
        for kid in (node.get("children") or node.get("roots") or []):
            hit = find(kid, nid)
            if hit:
                return hit
        return None

    def frozen_now():
        r = call(ADMIN, "GET", f"/api/orgs/{K}")
        ok200(r, "admin tree")
        n = find(r.json, NID)
        assert n, "the node is missing from the tree"
        return n["frozen"]

    STAMP = "STAMPED-AT-FREEZE-TIME"
    STAMP_TS = 1_000_000_000.0          # long past, and no roster value
    with store.DOC_LOCK:
        _o = store.load_org(K)
        _n = _o.nodes[NID]
        keep_fz, keep_model = _n.get("frozen"), _n["model"]
        _n["frozen"] = {"at": "2026-01-01T00:00:00Z", "until": STAMP,
                        "until_ts": STAMP_TS, "limit": True}
        _n["model"] = "opus"
        store.save_org(_o)
    keep_doc = _json.loads(_json.dumps(accounts.load()))
    keep_live = accounts.live_identity
    try:
        soon = _time.time() + 3600.0

        # ① EVERY lane marked — the control that must fail. Capacity really is
        # gone, so a countdown is the honest answer and it must be the
        # ROSTER's, not the stamp's.
        doc = _json.loads(_json.dumps(keep_doc))
        doc["keys"] = []
        doc["usage_refreshes"] = {"primary": {"opus": soon}}
        accounts.save(doc)
        fz = frozen_now()
        assert fz["until_ts"] == soon, fz
        assert "capacity resets" in (fz["until"] or ""), fz
        assert STAMP not in (fz["until"] or ""), "the stamped label survived"

        # ② a key is ADDED while the node sits parked — the user's scenario.
        doc["keys"] = [{"id": "kFREE01", "account_uuid": None}]
        accounts.save(doc)
        fz = frozen_now()
        assert fz["until"] == "capacity available — ▶ to resume", fz
        # ⚠ `until_ts` must move WITH the label: the header's red "not yet"
        # button reads the timestamp, so a countdown left standing here would
        # colour the button red beside text saying capacity is back.
        assert fz["until_ts"] is None, fz

        # ③ no lane exists AT ALL (nobody signed in, no key rows) → there is
        # no real T. It must say so rather than compute a plausible one.
        accounts.live_identity = lambda: {"uuid": "", "email": ""}
        doc["keys"] = []
        doc["usage_refreshes"] = {}
        accounts.save(doc)
        fz = frozen_now()
        assert fz["until"] == "reset time unknown", fz
        assert fz["until_ts"] is None, fz
    finally:
        accounts.live_identity = keep_live
        accounts.save(keep_doc)
        with store.DOC_LOCK:
            _o = store.load_org(K)
            _o.nodes[NID]["frozen"] = keep_fz
            _o.nodes[NID]["model"] = keep_model
            store.save_org(_o)


@t("tree · an AUTH freeze reaches the payload and keeps its own label")
def _():
    """D-156 exposes `cause` so a reader can tell a rejected credential from
    exhausted capacity. Two things are asserted, and the second is a bug the
    first one uncovered:

    · `cause` survives `ledger.tree()`'s fixed key list at all. That list
      rebuilds `frozen` field by field, so anything not named there is
      silently dropped — `spend` still is.
    · the re-derivation LEAVES AN AUTH FREEZE ALONE. An auth freeze carries
      `limit: True`, so it walked straight into `_rederive_freeze_reset` and
      had "credential rejected — replace it, then resume" overwritten with a
      capacity statement — true, and the opposite of what to fix. It was
      unreachable until `cause` reached the payload, because the projection
      dropped the only field that could tell them apart."""
    import json as _json
    from orgtree import accounts

    def find(node, nid):
        if node.get("id") == nid:
            return node
        for kid in (node.get("children") or node.get("roots") or []):
            hit = find(kid, nid)
            if hit:
                return hit
        return None

    AUTH_LABEL = "credential rejected — replace it, then resume"
    with store.DOC_LOCK:
        _o = store.load_org(K)
        _n = _o.nodes[NID]
        keep_fz, keep_model = _n.get("frozen"), _n["model"]
        _n["frozen"] = {"at": "2026-01-01T00:00:00Z", "until": AUTH_LABEL,
                        "until_ts": None, "limit": True, "cause": "auth",
                        "reset_src": "auth"}
        _n["model"] = "opus"
        store.save_org(_o)
    keep_doc = _json.loads(_json.dumps(accounts.load()))
    try:
        # a roster with capacity — so an unguarded re-derivation WOULD
        # rewrite the label to "capacity available". That is what makes the
        # assertion below mean something.
        doc = _json.loads(_json.dumps(keep_doc))
        doc["keys"] = [{"id": "kFREE01", "account_uuid": None}]
        doc["usage_refreshes"] = {}
        accounts.save(doc)
        r = call(ADMIN, "GET", f"/api/orgs/{K}")
        ok200(r, "admin tree")
        fz = find(r.json, NID)["frozen"]
        assert fz.get("cause") == "auth", (
            "`cause` did not survive tree()'s projection — the frontend "
            "cannot tell an auth freeze from a capacity one, and the banner "
            "counts it under the words 'usage limit hit'")
        assert fz["until"] == AUTH_LABEL, (
            "the re-derivation overwrote an AUTH freeze's label. The freeze "
            "cleared until_ts and said what to do; this replaced it with a "
            "capacity statement that is true and beside the point")
        assert fz["until_ts"] is None, fz
    finally:
        accounts.save(keep_doc)
        with store.DOC_LOCK:
            _o = store.load_org(K)
            _o.nodes[NID]["frozen"] = keep_fz
            _o.nodes[NID]["model"] = keep_model
            store.save_org(_o)


@t("tree · a CONNECTION freeze keeps its own stamped reset, untouched")
def _():
    """The re-derivation is scoped to the subscription pool. A connection
    backoff is OUR OWN timer measured from our own failure — `resolve` knows
    nothing about it, and rewriting it would replace a real number with an
    unrelated one. Without this leg, a re-derivation that fired on every
    freeze kind would still pass the three checks above."""
    import json as _json
    from orgtree import accounts

    def find(node, nid):
        if node.get("id") == nid:
            return node
        for kid in (node.get("children") or node.get("roots") or []):
            hit = find(kid, nid)
            if hit:
                return hit
        return None

    STAMP, STAMP_TS = "network interruption — 30s", 1_000_000_000.0
    with store.DOC_LOCK:
        _o = store.load_org(K)
        _n = _o.nodes[NID]
        keep_fz = _n.get("frozen")
        _n["frozen"] = {"at": "2026-01-01T00:00:00Z", "until": STAMP,
                        "until_ts": STAMP_TS, "connection": True}
        store.save_org(_o)
    keep_doc = _json.loads(_json.dumps(accounts.load()))
    try:
        # a roster that WOULD have produced "capacity available" for a limit
        # freeze — so this passing means the kind gate held, not that the
        # roster had nothing to say
        doc = _json.loads(_json.dumps(keep_doc))
        doc["keys"] = [{"id": "kFREE01", "account_uuid": None}]
        doc["usage_refreshes"] = {}
        accounts.save(doc)
        r = call(ADMIN, "GET", f"/api/orgs/{K}")
        ok200(r, "admin tree")
        fz = find(r.json, NID)["frozen"]
        assert fz["until"] == STAMP, fz
        assert fz["until_ts"] == STAMP_TS, fz
    finally:
        accounts.save(keep_doc)
        with store.DOC_LOCK:
            _o = store.load_org(K)
            _o.nodes[NID]["frozen"] = keep_fz
            store.save_org(_o)


@t("GET /api/orgs as a visitor lists exactly one org — its own")
def _():
    r = pub("GET", "/api/orgs")
    ok200(r, "org list")
    assert len(r.json) == 1 and r.json[0]["slug"] == K, r


@t("disabling the kiosk revokes the URL immediately (cache invalidated)")
def _():
    ok200(call(ADMIN, "POST", f"/api/orgs/{K}/kiosk", {"enabled": False}), "off")
    assert call(PUBLIC, "GET", f"/k/{TOKEN}/api/orgs").status == 404
    ok200(call(ADMIN, "POST", f"/api/orgs/{K}/kiosk", {"enabled": True}), "on")
    ok200(call(PUBLIC, "GET", f"/k/{TOKEN}/api/orgs"), "back on")


@t("rotating the token revokes the old URL and mints a new one")
def _():
    global TOKEN
    old = TOKEN
    r = call(ADMIN, "POST", f"/api/orgs/{K}/kiosk", {"rotate_token": True})
    ok200(r, "rotate")
    TOKEN = r.json["kiosk"]["token"]
    assert TOKEN != old
    assert call(PUBLIC, "GET", f"/k/{old}/api/orgs").status == 404
    ok200(call(PUBLIC, "GET", f"/k/{TOKEN}/api/orgs"), "new token")


# ------------------------------------------------- §2 the restriction matrix
print("\n§2  the public restriction matrix")

FROZEN = [
    ("POST", "/api/orgs", "create an org"),
    ("DELETE", f"/api/orgs/{K}", "delete this org"),
    ("DELETE", f"/api/orgs/{K2}", "delete another org"),
    ("POST", f"/api/orgs/{K}/settings", "org settings"),
    ("POST", f"/api/orgs/{K}/kiosk", "kiosk caps / token / ceiling"),
    ("GET", "/api/fs", "the host filesystem browser"),
    ("PUT", f"/api/orgs/{K}/orgmd", "org.md edits"),
    ("POST", "/api/agent", "the node MCP gateway"),
    ("GET", "/api/mcp-servers", "the user's MCP server list"),
    ("GET", "/api/defaults", "global org defaults"),
    ("POST", "/api/defaults", "writing global org defaults"),
    ("GET", "/api/host", "host capabilities"),
    ("GET", "/api/charters", "charter presets"),
    ("POST", "/api/extern/p1/send", "the extern peer send"),
    ("GET", "/api/extern/p1/messages", "the extern peer read"),
    ("GET", "/api/extern/p1/wait", "the extern peer wait"),
    ("POST", f"/api/orgs/{K}/nodes/{NID}/steer", "the steer-queue drain"),
    ("POST", f"/api/orgs/{K}/disk/resize", "disk resize"),
    ("POST", f"/api/orgs/{K}/disk/resize/apply", "disk resize apply"),
    ("GET", f"/api/orgs/{K}/sweep-legacy", "the legacy-sweep preview"),
    ("POST", f"/api/orgs/{K}/sweep-legacy", "the legacy sweep"),
]


def _frozen(m, p):
    def go():
        r = pub(m, p, {} if m in ("POST", "PUT") else None)
        no500(r, f"{m} {p}")
        denied(r, f"{m} {p}")
    return go


for _m, _p, _why in FROZEN:
    check(f"visitor cannot reach {_m} {_p}  ({_why})", _frozen(_m, _p))

OPEN = [
    ("GET", f"/api/orgs/{K}", None),
    ("GET", f"/api/orgs/{K}/events", None),
    ("GET", f"/api/orgs/{K}/audiences", None),
    ("GET", f"/api/orgs/{K}/inbox", None),
    ("GET", f"/api/orgs/{K}/orgmd", None),
    ("GET", f"/api/orgs/{K}/nodes/{NID}/chat", None),
    ("GET", f"/api/orgs/{K}/nodes/{NID}/inbox", None),
    ("GET", f"/api/orgs/{K}/nodes/{NID}/history", None),
    ("GET", f"/api/orgs/{K}/nodes/{NID}/scratch", None),
    ("POST", f"/api/orgs/{K}/nodes/{NID}/scope", {}),
    ("POST", f"/api/orgs/{K}/defaults", {}),
    ("POST", f"/api/orgs/{K}/inbox/read", {"ids": []}),
    ("POST", f"/api/orgs/{K}/org_inbox/read", {}),
    ("POST", f"/api/orgs/{K}/killswitch", {}),
    ("POST", f"/api/orgs/{K}/resume", {}),
]


def _open(m, p, b):
    def go():
        r = pub(m, p, b)
        ok200(r, f"{m} {p} (ruled open to visitors)")
    return go


for _m, _p, _b in OPEN:
    check(f"visitor CAN reach {_m} {_p} (ruled open)", _open(_m, _p, _b))


@t("REGRESSION: mail retraction is reachable — it was collateral of the "
   "blanket DELETE freeze, while the visitor UI renders the button")
def _():
    r = pub("DELETE", f"/api/orgs/{K}/nodes/{NID}/mail/no-such-id")
    no500(r, "retract")
    assert r.status == 404 and "pending mail" in r.text, r


@t("…and deleting the ORG is still frozen by that same clause")
def _():
    denied(pub("DELETE", f"/api/orgs/{K}"), "org delete")
    store.load_org(K)          # still there


# -------------------------------------------------------------- §3 path games
print("\n§3  path tricks against the matrix")


def reaches_fs(r):
    """Did this actually come from the host filesystem browser?"""
    return (r.status == 200 and isinstance(r.json, dict)
            and "dirs" in r.json and "path" in r.json)


def reaches_admin_config(r):
    return (r.status == 200 and isinstance(r.json, dict)
            and ("servers" in r.json
                 or ("max_top_grant" in r.json and "cascade_hire" in r.json
                     and "slug" not in r.json)
                 or "cli_version" in r.json or "charters" in r.json))


TRICKS = [
    "/api/fs/", "/api/fs//", "/API/fs", "/Api/Fs", "/api//fs", "/api/./fs",
    "/api/fs/.", "/api/fs%20", "/api/fs.", "//api/fs", "/./api/fs",
    f"/api/orgs/{K}/../../fs", f"/api/orgs/{K}/../../../api/fs",
    "/api/orgs/../fs", "/api/agent", "/api/agent/", "/api/agent//",
    "/API/agent", "//api/agent", "/api/./agent", f"/api/orgs/{K}/../../agent",
    "/api/defaults", "/api/defaults/", "/API/defaults", "//api/defaults",
    "/api/mcp-servers/", "/api/MCP-servers", "/api/host/", "/api/charters/",
    f"/api/orgs/{K}/settings/", f"/api/orgs/{K}/SETTINGS",
    f"/api/orgs/{K}/settings%20", f"/api/orgs/{K}//settings",
    f"/api/ORGS/{K}/settings", f"/api/orgs/{K}/kiosk/",
    f"/api/orgs/{K}/nodes/{NID}/steer/", f"/api/orgs/{K}/nodes/{NID}/STEER",
    # unicode that a normaliser might fold into a separator or another slug
    "/api/orgs\uff0f" + K + "/settings", "/api/or\u0261s/" + K,
    "/api/orgs/" + K + "\u0000/settings", "/api/orgs/" + K.upper(),
]


def _trick(meth, tr):
    def go():
        r = pub(meth, tr, {} if meth == "POST" else None)
        no500(r, f"{meth} {tr}")
        assert not reaches_fs(r), f"{meth} {tr!r} reached /api/fs: {r!r}"
        assert not reaches_admin_config(r), \
            f"{meth} {tr!r} reached an admin config surface: {r!r}"
    return go


for _tr in TRICKS:
    for _meth in ("GET", "POST"):
        check(f"{_meth} {_tr!r} reaches no frozen surface", _trick(_meth, _tr))


@t("a query string cannot smuggle a path past the matrix")
def _():
    denied(pub("GET", "/api/fs", query=b"path=C:\\"), "fs + query")


@t("a query string is not part of the org-scope check")
def _():
    denied(pub("GET", f"/api/orgs/{K2}", query=b"slug=" + K.encode()),
           "other org + query")


@t("an unmatched path falls through to the SPA, never to an API handler")
def _():
    r = pub("GET", "/totally/made/up")
    no500(r, "spa fallthrough")
    assert r.status in (200, 404), r
    assert r.json is None or "detail" in r.json, r


@t("☞ REGRESSION: the OpenAPI schema and the docs consoles are not public")
def _():
    """FastAPI's own routes are not under /api, so the gateway's
    "not /api ⇒ it must be the SPA" arm handed them straight to visitors: the
    schema names and describes every frozen admin endpoint, and /docs is a
    console that fires at them from the kiosk's own origin."""
    for p in ["/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect",
              "/openapi.json/", "/docs/"]:
        r = pub("GET", p)
        no500(r, p)
        denied(r, p)
        assert "/api/fs" not in r.text and "/api/agent" not in r.text, r
    # …and the admin listener still has them
    a = call(ADMIN, "GET", "/openapi.json")
    ok200(a, "admin openapi")
    assert "/api/fs" in a.text


@t("the SPA catch-all cannot serve a file outside frontend/dist")
def _():
    for p in ["/../../../../Windows/win.ini", "/..%2f..%2fapi",
              "/../../backend/orgtree/api.py", "/../../../../etc/passwd"]:
        r = pub("GET", p)
        no500(r, f"spa {p}")
        assert "_public_denied" not in r.text and "[fonts]" not in r.text \
            and "root:" not in r.text, f"{p} served a host file: {r!r}"


@t("unauthenticated static prefixes serve the SPA shell and nothing else")
def _():
    for p in ["/assets/", "/favicon.ico", "/favicon../../../etc", "/vite.svg"]:
        r = call(PUBLIC, "GET", p)
        no500(r, f"static {p}")
        for s in (TOKEN, TOKEN2, SECRET, KSECRET, K2):
            assert s not in r.text, f"{p} leaked {s[:8]}…"


# --------------------------------------------------------- §4 cross-org walls
print("\n§4  cross-org isolation")

CROSS_GET = [f"/api/orgs/{K2}", f"/api/orgs/{K2}/events",
             f"/api/orgs/{K2}/inbox", f"/api/orgs/{K2}/audiences",
             f"/api/orgs/{K2}/orgmd", f"/api/orgs/{ADMIN_SLUG}",
             f"/api/orgs/{ADMIN_SLUG}/events", f"/api/orgs/{SBX}",
             f"/api/orgs/{SBX}/nodes/{SNID}/chat",
             f"/api/orgs/{SBX}/nodes/{SNID}/scratch",
             f"/api/orgs/{SBX}/nodes/{SNID}/file",
             f"/api/orgs/{SBX}/nodes/{SNID}/history",
             f"/api/orgs/{K2}/disk", f"/api/orgs/{K2}/disk/file",
             f"/api/orgs/{K2}/disk/dir"]


def _cross(m, p, b=None):
    def go():
        r = pub(m, p, b)
        no500(r, f"{m} {p}")
        denied(r, f"cross-org {m} {p}")
    return go


for _p in CROSS_GET:
    check(f"visitor cannot GET another org's {_p}", _cross("GET", _p))
for _p in [f"/api/orgs/{K2}/ops", f"/api/orgs/{K2}/killswitch",
           f"/api/orgs/{K2}/dissolve-all", f"/api/orgs/{K2}/nodes/x/message",
           f"/api/orgs/{K2}/nodes/x/upload", f"/api/orgs/{K2}/inbox/clear",
           f"/api/orgs/{ADMIN_SLUG}/ops", f"/api/orgs/{SBX}/ops"]:
    check(f"visitor cannot POST another org's {_p}", _cross("POST", _p, {}))


@t("the mirror case: token 2 sees org 2 and not org 1")
def _():
    denied(call(PUBLIC, "GET", f"/k/{TOKEN2}/api/orgs/{K}"), "k2 → k1")
    ok200(call(PUBLIC, "GET", f"/k/{TOKEN2}/api/orgs/{K2}"), "k2 → k2")


@t("the org listing for token 2 does not mention org 1")
def _():
    r = call(PUBLIC, "GET", f"/k/{TOKEN2}/api/orgs")
    ok200(r, "k2 listing")
    assert len(r.json) == 1 and r.json[0]["slug"] == K2, r
    assert K not in r.text and SBX not in r.text, r


@t("websocket: the visitor's own org room is accepted")
def _():
    assert ws_call(PUBLIC, f"/k/{TOKEN}/api/orgs/{K}/ws") == ("accept", None)


@t("websocket: another org's room is closed, not joined")
def _():
    assert ws_call(PUBLIC, f"/k/{TOKEN}/api/orgs/{K2}/ws") == ("close", 4404)
    assert not api.hub.rooms.get(K2), "the refused room was still created"


@t("websocket: no token closes the socket")
def _():
    kind, _code = ws_call(PUBLIC, f"/api/orgs/{K}/ws")
    assert kind == "close", kind


# ------------------------------------------------------------- §5 the secrets
print("\n§5  secrets never leave loopback")

SECRETS = {"kiosk token": TOKEN, "other kiosk token": TOKEN2,
           "kiosk sandbox secret": KSECRET, "sandbox bridge secret": SECRET}
PUBLIC_GETS = ["/api/orgs", f"/api/orgs/{K}", f"/api/orgs/{K}/events",
               f"/api/orgs/{K}/audiences", f"/api/orgs/{K}/inbox",
               f"/api/orgs/{K}/orgmd", f"/api/orgs/{K}/nodes/{NID}/chat",
               f"/api/orgs/{K}/nodes/{NID}/inbox",
               f"/api/orgs/{K}/nodes/{NID}/history",
               f"/api/orgs/{K}/nodes/{NID}/scratch"]


def _nosecret(p):
    def go():
        r = pub("GET", p)
        no500(r, p)
        for name, s in SECRETS.items():
            assert s not in r.text, f"GET {p} leaked the {name}"
    return go


for _p in PUBLIC_GETS:
    check(f"no secret rides GET {_p}", _nosecret(_p))


@t("the public org listing carries no kiosk_cfg / share_url block")
def _():
    r = pub("GET", "/api/orgs")
    assert "kiosk_cfg" not in r.text and "share_url" not in r.text, r


@t("the public tree drops max_scope / auto_raise / share_url…")
def _():
    k = pub("GET", f"/api/orgs/{K}").json["kiosk"]
    for key in ("max_scope", "auto_raise", "share_url"):
        assert key not in k, f"{key} survived the public scrub: {k}"


@t("…and the ADMIN listener still gets them (a scrub, not a deletion)")
def _():
    a = call(ADMIN, "GET", f"/api/orgs/{K}").json["kiosk"]
    assert "max_scope" in a and "share_url" in a and "auto_raise" in a, a


@t("the public tree carries no session ids (node or lineage)")
def _():
    assert "session_id" not in pub("GET", f"/api/orgs/{K}").text


@t("the admin listing DOES carry the token (loopback-only by design)")
def _():
    assert TOKEN in call(ADMIN, "GET", "/api/orgs").text


@t("ORGTREE_EXPOSE_ADMIN unset ⇒ the admin listener binds loopback")
def _():
    assert api._admin_host() == "127.0.0.1"


def _expose(val, host):
    def go():
        os.environ["ORGTREE_EXPOSE_ADMIN"] = val
        try:
            assert api._admin_host() == host, (val, api._admin_host())
        finally:
            os.environ.pop("ORGTREE_EXPOSE_ADMIN", None)
    return go


for _val, _host in [("1", "0.0.0.0"), ("true", "0.0.0.0"), ("YES", "0.0.0.0"),
                    ("on", "0.0.0.0"), (" On ", "0.0.0.0"), ("0", "127.0.0.1"),
                    ("", "127.0.0.1"), ("no", "127.0.0.1"),
                    ("maybe", "127.0.0.1"), ("2", "127.0.0.1"),
                    ("true ", "0.0.0.0"), ("ON", "0.0.0.0")]:
    check(f"ORGTREE_EXPOSE_ADMIN={_val!r} → binds {_host}",
          _expose(_val, _host))


@t("the expose state is never reported on any public payload")
def _():
    for p in PUBLIC_GETS:
        assert "EXPOSE" not in pub("GET", p).text.upper(), p


# ---------------------------------------------------------------- §6 scrubbing
print("\n§6  error scrubbing for public visitors")

HOSTPATH = r"E:\Libraries\Desktop\claude-orgtree\secret.txt"
HOSTUSER = r"C:\Users\operator\AppData\Roaming\thing.log"

with store.DOC_LOCK:
    _o = store.load_org(K)
    _n = _o.nodes[NID]
    _n["last_denials"] = [{"tool": "Read", "arg": HOSTPATH}]
    # the codex seam's APPROVED rows (2026-09-05) carry a `cwd` that is
    # always a host path, on top of an `arg` that routinely is one
    _n["last_approvals"] = [{"tool": "commandExecution", "arg": HOSTPATH,
                             "cwd": HOSTUSER}]
    _n["frozen"] = {"at": "2026-01-01T00:00:00Z", "until": None,
                    "until_ts": None, "error": f"cli blew up at {HOSTUSER}"}
    _o.d.setdefault("events", []).append(
        {"op": "revoke_dir", "actor": "@user", "at": "2026-01-01T00:00:01Z",
         "detail": {"node": NID, "dir": HOSTPATH}, "warnings": [HOSTUSER]})
    store.save_org(_o)

_real_state = supervisor.state
supervisor.state = lambda slug, nid: {
    "busy": False, "queue": [], "last_error": f"failed reading {HOSTPATH}",
    "waiting": False, "responding": False, "live": [], "init": None}


def assert_scrubbed(r, what):
    assert r.status == 200, f"{what}: {r!r}"
    for bad in ("E:\\", "C:\\Users", "Libraries", "operator"):
        assert bad not in r.text, f"{what} leaked {bad!r}: {r.text[:400]}"


@t("tree: the workspace is a basename for a visitor")
def _():
    ws = pub("GET", f"/api/orgs/{K}").json["workspace"]
    assert "\\" not in ws and "/" not in ws, ws


@t("tree: every dir grant is a basename for a visitor")
def _():
    for d in pub("GET", f"/api/orgs/{K}").json["dirs"]:
        assert "\\" not in d["path"] and "/" not in d["path"], d


@t("tree: a node's last_error is scrubbed")
def _():
    assert_scrubbed(pub("GET", f"/api/orgs/{K}"), "tree/last_error")


@t("REGRESSION: tree — frozen.error is scrubbed (it rode out raw)")
def _():
    r = pub("GET", f"/api/orgs/{K}")
    assert_scrubbed(r, "tree/frozen.error")
    assert "cli blew up at <path>" in r.text, r.text[:400]


@t("REGRESSION: tree — last_denials[].arg is scrubbed (it rode out raw)")
def _():
    r = pub("GET", f"/api/orgs/{K}")
    assert_scrubbed(r, "tree/last_denials")
    assert '"arg": "<path>"' in r.text or '"arg":"<path>"' in r.text, \
        r.text[:400]


@t("tree: last_approvals[].arg AND .cwd are scrubbed like the denials")
def _():
    r = pub("GET", f"/api/orgs/{K}")
    assert_scrubbed(r, "tree/last_approvals")
    # both fields present and both rewritten: a scrubber that DROPPED the
    # row would pass assert_scrubbed while losing the record
    txt = r.text.replace(" ", "")
    assert '"cwd":"<path>"' in txt, r.text[:400]
    assert txt.count('"arg":"<path>"') >= 2, r.text[:400]


@t("events: details and warnings are scrubbed")
def _():
    assert_scrubbed(pub("GET", f"/api/orgs/{K}/events"), "events")


@t("history: the same event log is scrubbed there too")
def _():
    assert_scrubbed(pub("GET", f"/api/orgs/{K}/nodes/{NID}/history"), "history")


@t("orgmd: the path is a basename for a visitor")
def _():
    assert pub("GET", f"/api/orgs/{K}/orgmd").json["path"] == "CLAUDE.md"


@t("the ADMIN listener still sees the unscrubbed truth")
def _():
    # (compare against the JSON-escaped form — a Windows path is `\\` on the
    # wire, so a raw `in r.text` would be comparing two different strings)
    tree = call(ADMIN, "GET", f"/api/orgs/{K}").json
    boss = tree["roots"][0]
    assert boss["last_denials"][0]["arg"] == HOSTPATH, boss["last_denials"]
    assert HOSTUSER in boss["frozen"]["error"], boss["frozen"]
    evs = call(ADMIN, "GET", f"/api/orgs/{K}/events").json["events"]
    assert any(HOSTUSER in (e.get("warnings") or [""])[0] for e in evs), evs[-1]


@t("scrubbing is non-destructive — the org doc keeps the real string")
def _():
    assert store.load_org(K).nodes[NID]["last_denials"][0]["arg"] == HOSTPATH


@t("a 404 for a missing node echoes the id, not a host path")
def _():
    r = pub("GET", f"/api/orgs/{K}/nodes/nope/chat")
    assert r.status == 404 and "\\" not in r.text, r


@t("a scratch escape refuses without naming the base directory")
def _():
    r = pub("GET", f"/api/orgs/{K}/nodes/{NID}/scratch",
            query=b"path=..%2F..%2F..%2F")
    assert r.status == 422 and "escapes" in r.text, r
    assert ":" not in r.json["detail"], r


@t("⚑ KNOWN GAP: /chat is NOT scrubbed — transcript host paths ride out")
def _():
    """Documented, not fixed (see the report). read_chat's payload passes
    through verbatim for public visitors, so an UNSANDBOXED kiosk's transcript
    hands the operator's absolute paths to the internet. Fixing it means
    regexing the visitor's own chat text, which needs a ruling — so this
    asserts the CURRENT behaviour, and fails loudly the day it changes."""
    tmp = os.path.join(tempfile.mkdtemp(prefix="orgtree-tr-"), "s.jsonl")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "assistant",
                        "content": [{"type": "text",
                                     "text": f"wrote {HOSTPATH}"}]}}) + "\n")
    old = supervisor.transcript_path
    supervisor.transcript_path = lambda sid, root=None: tmp
    try:
        r = pub("GET", f"/api/orgs/{K}/nodes/{NID}/chat")
        ok200(r, "chat")
        texts = [m.get("text") or "" for m in r.json["messages"]]
        assert any(HOSTPATH in x for x in texts), \
            "GOOD NEWS: /chat is scrubbed now — retire this check and the " \
            f"matching report entry ({texts})"
    finally:
        supervisor.transcript_path = old


supervisor.state = _real_state

# ------------------------------------------------------------------ §7 bridge
print("\n§7  the sandbox bridge")


def br(method, path, body=None, secret=SECRET, headers=None):
    hs = list(headers or [])
    if secret is not None:
        hs.append(("x-orgtree-bridge", secret))
    return call(BRIDGE, method, path, body, headers=hs)


def _boom():
    raise RuntimeError("upstream-token-not-fetched-in-tests")


api.subproxy.get_access_token = _boom


def assert_forbidden(r, what):
    assert r.status == 403 and r.json == {"detail": "forbidden"}, \
        f"{what} should be a bare bridge 403, got {r!r}"


@t("bridge: no secret → a bare 403 on the agent gateway")
def _():
    assert_forbidden(br("POST", "/api/agent",
                        {"org": SBX, "node": SNID, "tool": "orgtree_chart"},
                        secret=None), "no secret")


@t("bridge: a wrong secret → 403")
def _():
    assert_forbidden(br("POST", "/api/agent",
                        {"org": SBX, "node": SNID, "tool": "orgtree_chart"},
                        secret="0" * 32), "wrong secret")


@t("bridge: an empty secret header → 403")
def _():
    assert_forbidden(br("POST", "/api/agent", {}, secret=""), "empty secret")


@t("bridge: the right secret reaches the agent gateway for its OWN org")
def _():
    r = br("POST", "/api/agent",
           {"org": SBX, "node": SNID, "tool": "orgtree_chart"})
    ok200(r, "own org")
    assert "chart" in r.json, r


@t("☞ bridge: the secret is scoped to its own org — another org is refused")
def _():
    r = br("POST", "/api/agent",
           {"org": K, "node": NID, "tool": "orgtree_chart"})
    assert r.status == 403 and "scoped to its own org" in r.text, r


@t("bridge: …including the plain admin org")
def _():
    denied(br("POST", "/api/agent",
              {"org": ADMIN_SLUG, "node": "x", "tool": "orgtree_chart"}),
           "admin org via bridge")


@t("bridge: a KIOSK org's sandbox secret is scoped the same way")
def _():
    ok200(br("POST", "/api/agent",
             {"org": K, "node": NID, "tool": "orgtree_chart"}, secret=KSECRET),
          "kiosk secret, own org")
    r = br("POST", "/api/agent",
           {"org": SBX, "node": SNID, "tool": "orgtree_chart"}, secret=KSECRET)
    denied(r, "kiosk secret, other org")


BRIDGE_CLOSED = [
    ("GET", "/api/agent"), ("PUT", "/api/agent"), ("DELETE", "/api/agent"),
    ("GET", f"/api/orgs/{SBX}"), ("GET", "/api/orgs"), ("GET", "/api/fs"),
    ("POST", f"/api/orgs/{SBX}/settings"), ("POST", f"/api/orgs/{SBX}/ops"),
    ("GET", "/api/defaults"), ("GET", "/api/host"),
    ("GET", f"/api/orgs/{SBX}/nodes/{SNID}/chat"),
    ("GET", f"/api/orgs/{SBX}/nodes/{SNID}/scratch"),
    ("POST", f"/api/orgs/{SBX}/nodes/{SNID}/message"),
    ("POST", f"/api/orgs/{SBX}/nodes/{SNID}/upload"),
    ("GET", f"/api/orgs/{SBX}/disk/file"), ("GET", "/"), ("GET", "/index.html"),
    ("GET", "/assets/index.js"), ("POST", "/api/orgs"),
]


def _bridge_closed(m, p):
    def go():
        assert_forbidden(br(m, p, {}), f"bridge {m} {p}")
    return go


for _m, _p in BRIDGE_CLOSED:
    check(f"bridge serves nothing at {_m} {_p}", _bridge_closed(_m, _p))


@t("bridge: the steer fetch is allowed for its own org")
def _():
    ok200(br("POST", f"/api/orgs/{SBX}/nodes/{SNID}/steer"), "own steer")


@t("bridge: the steer fetch of ANOTHER org is refused")
def _():
    assert_forbidden(br("POST", f"/api/orgs/{K}/nodes/{NID}/steer"),
                     "cross-org steer")


@t("bridge: GET on the steer path is refused (POST only)")
def _():
    assert_forbidden(br("GET", f"/api/orgs/{SBX}/nodes/{SNID}/steer"),
                     "GET steer")


@t("bridge: websockets are closed outright")
def _():
    assert ws_call(BRIDGE, f"/api/orgs/{SBX}/ws") == ("close", 4403)


@t("bridge: the /anthropic proxy needs the secret IN THE PATH")
def _():
    assert_forbidden(br("POST", "/anthropic/v1/messages", {}),
                     "anthropic, header secret only")


@t("bridge: a wrong path secret is refused")
def _():
    assert_forbidden(br("POST", "/anthropic/" + "0" * 32 + "/v1/messages", {},
                        secret=None), "anthropic, bad path secret")


@t("bridge: a path secret of the wrong shape is refused")
def _():
    for bad in ["nothex", "abc", "0" * 31, "0" * 33, "DEADBEEF" * 4]:
        assert_forbidden(br("POST", f"/anthropic/{bad}/v1/messages", {},
                            secret=None), f"anthropic {bad[:10]}")


@t("bridge: the right path secret reaches the proxy (502 = no token here)")
def _():
    r = br("POST", f"/anthropic/{SECRET}/v1/messages", {}, secret=None)
    assert r.status == 502 and "upstream-token-not-fetched" in r.text, r


@t("bridge: /anthropic without the separating slash is 403, not a proxy hit")
def _():
    assert_forbidden(br("POST", f"/anthropic{SECRET}/v1/x", {}, secret=None),
                     "no slash")


@t("the /anthropic proxy refuses a NON-bridge caller (admin listener)")
def _():
    r = call(ADMIN, "POST", "/anthropic/v1/messages", {})
    assert r.status == 403 and r.json["detail"] == "bridge only", r


@t("…and a public visitor likewise")
def _():
    r = pub("POST", "/anthropic/v1/messages", {})
    assert r.status == 403 and "bridge only" in r.text, r


@t("duplicate bridge headers: the LAST one wins (documented, not exploitable)")
def _():
    good_bad = call(BRIDGE, "POST", "/api/agent",
                    {"org": SBX, "node": SNID, "tool": "orgtree_chart"},
                    headers=[("x-orgtree-bridge", SECRET),
                             ("x-orgtree-bridge", "0" * 32)])
    bad_good = call(BRIDGE, "POST", "/api/agent",
                    {"org": SBX, "node": SNID, "tool": "orgtree_chart"},
                    headers=[("x-orgtree-bridge", "0" * 32),
                             ("x-orgtree-bridge", SECRET)])
    assert good_bad.status == 403, good_bad
    assert bad_good.status == 200, bad_good


@t("⚑ KNOWN GAP: the bridge pins the ORG, not the NODE — any node id acts")
def _():
    """Documented, not fixed: one container serves every agent in the org and
    all of them can read the shared secret, so a subordinate can address the
    gateway as its own superior. Closing it needs a per-node credential, which
    does not exist. Asserts the current behaviour."""
    r = call(BRIDGE, "POST", "/api/agent",
             {"org": K, "node": NID2, "tool": "orgtree_chart"},
             headers=[("x-orgtree-bridge", KSECRET)])
    ok200(r, "impersonate a sibling node")
    r2 = call(BRIDGE, "POST", "/api/agent",
              {"org": K, "node": NID, "tool": "orgtree_chart"},
              headers=[("x-orgtree-bridge", KSECRET)])
    ok200(r2, "impersonate the superior")


# ------------------------------------------------------------- §8 /api/agent
print("\n§8  /api/agent — the MCP gateway")


def ag(node, tool, args=None, org=None):
    return call(ADMIN, "POST", "/api/agent",
                {"org": org or K, "node": node, "tool": tool,
                 "args": args or {}})


@t("list_tiers gateway validates caller/bridge and runs provider I/O outside DOC_LOCK")
def _():
    old = api._tier_discovery_payload
    calls = []

    def discovered():
        owned = store.DOC_LOCK._is_owned()  # type: ignore[attr-defined]
        calls.append(owned)
        return {"advisory": "fixture", "providers": []}

    api._tier_discovery_payload = discovered
    archived = "tier-reader-archived"
    try:
        # Positive control: the instrument really detects ownership.
        with store.DOC_LOCK:
            assert store.DOC_LOCK._is_owned()  # type: ignore[attr-defined]
        r = ag(NID, "orgtree_list_tiers")
        ok200(r, "live caller")
        assert r.json == {"advisory": "fixture", "providers": []}
        assert calls == [False], calls
        assert ag("ghost", "orgtree_list_tiers").status == 422
        with store.DOC_LOCK:
            org = store.load_org(K)
            org.hire("@user", NID, "haiku", 0, archived)
            org.retire(NID, archived)
            store.save_org(org)
        assert ag(archived, "orgtree_list_tiers").status == 422
        before = len(calls)
        cross = call(BRIDGE, "POST", "/api/agent", {
            "org": K, "node": NID, "tool": "orgtree_list_tiers"},
            headers=[("x-orgtree-bridge", SECRET)])
        assert cross.status == 403 and len(calls) == before, (cross, calls)
    finally:
        api._tier_discovery_payload = old


@t("list_tiers projection is explicit, secret-free, and keeps unknown seats unknown")
def _():
    old = api._providers_payload
    api._providers_payload = lambda: {"providers": [{
        "id": "fixture", "label": "Fixture", "hire_enabled": True,
        "reason": None, "status": {"email": "secret@example.test"},
        "path": "C:/secret", "error": {"token": "secret"},
        "tiers": [{"tier": "fixture-tier", "provider": "fixture",
                   "model": "fixture-model", "seat": None,
                   "prompt": 0.0, "price_source": "openrouter-catalog",
                   "price_unknown": ["prompt", "prompt", "cache_read"],
                   "unknown": "secret"}],
    }]}
    try:
        response = ag(NID, "orgtree_list_tiers")
    finally:
        api._providers_payload = old
    ok200(response, "finite/null discovery serializes")
    result = response.json
    assert result["providers"] == [{
        "id": "fixture", "label": "Fixture", "hire_enabled": True,
        "reason": None, "tiers": [{"tier": "fixture-tier",
                                     "provider": "fixture",
                                     "model": "fixture-model",
                                     "seat": None, "prompt": 0.0,
                                     "price_source": "openrouter-catalog",
                                     "price_unknown": ["prompt", "cache_read"]}]}], result
    assert "secret" not in json.dumps(result), result


@t("list_tiers fails closed when provider discovery is unavailable or malformed")
def _():
    old = api._providers_payload
    good = {"providers": [{
        "id": "fixture", "label": "Fixture", "hire_enabled": True,
        "reason": None, "tiers": [{"tier": "fixture", "seat": 1.5}],
    }]}
    try:
        bad_values = (
            None,
            [],
            {"providers": [{**good["providers"][0],
                            "hire_enabled": "false"}]},
            {"providers": [{**good["providers"][0],
                            "tiers": [{"tier": "nan", "seat": float("nan")}]}]},
            {"providers": [{**good["providers"][0],
                            "tiers": [{"tier": "inf", "prompt": float("inf")}]}]},
            {"providers": [{**good["providers"][0],
                            "tiers": [{"tier": "neg-inf",
                                       "context": float("-inf")}]}]},
            {"providers": [{**good["providers"][0],
                            "tiers": [{"tier": "bad-price-fields",
                                       "price_unknown": "prompt"}]}]},
            {"providers": [{**good["providers"][0],
                            "tiers": [{"tier": "bad-price-name",
                                       "price_unknown": ["api_key"]}]}]},
            {"providers": [{**good["providers"][0],
                            "tiers": [{"tier": "bad-price-source",
                                       "price_source": {"secret": True}}]}]},
            {"providers": [{**good["providers"][0],
                            "tiers": [{"tier": "invented-price-source",
                                       "price_source": "provider-invoice"}]}]},
        )
        for bad in bad_values:
            api._providers_payload = lambda bad=bad: bad
            r = ag(NID, "orgtree_list_tiers")
            assert r.status == 503 and "secret" not in r.text, r
        api._providers_payload = lambda: (_ for _ in ()).throw(
            OSError("fixture secret"))
        r = ag(NID, "orgtree_list_tiers")
        assert r.status == 503 and "secret" not in r.text, r
    finally:
        api._providers_payload = old


@t("list_tiers serves the tool declaration as true/false/null over a real "
   "agent request, and refuses any other value")
def _():
    """The OpenRouter tool declaration, as an AGENT actually receives it.

    ⚠ THROUGH THE GATEWAY, NOT THE HELPER. `_tier_discovery_payload` has its
    own unit checks; this one goes through `POST /api/agent` with a live
    caller so the JSON an agent reads is what is asserted — a helper test
    cannot see the response serializer, the caller validation, or the 503
    the malformed arm must produce.

    ⚠ THREE STATES, AND `null` IS ONE OF THEM. `tools` is what openrouter.ai's
    CATALOG declared: true, false, or null when the entry declared nothing
    readable. It is never an observation of a turn. The default value arm of
    the projection admits str-or-None, so this field needs its own branch and
    a bool would otherwise take the whole document down as malformed.
    """
    old = api._providers_payload
    fixture = lambda tools: {"providers": [{                      # noqa: E731
        "id": "openrouter", "label": "OpenRouter", "hire_enabled": True,
        "reason": None,
        "tiers": [{"tier": "or-fixture", "provider": "openrouter",
                   "model": "vendor/fixture", "seat": 1.0,
                   "tools": tools}]}]}
    try:
        for declared in (True, False, None):
            api._providers_payload = lambda d=declared: fixture(d)
            r = ag(NID, "orgtree_list_tiers")
            ok200(r, f"tools={declared!r} over the gateway")
            row = r.json["providers"][0]["tiers"][0]
            assert row["tools"] is declared, (declared, row)
            # present-as-null, never dropped: an absent key would make
            # "the catalog said nothing" indistinguishable from "the
            # projection forgot this tier"
            assert "tools" in row, row
        # …and every other value is a malformed tier, sanitized to a 503.
        # Identity, not membership: `1 == True`, so a `value in (True, False)`
        # test would serve an integer as a declaration.
        for bad in (1, 0, 1.0, "true", "", [], {}, "secret"):
            api._providers_payload = lambda b=bad: fixture(b)
            r = ag(NID, "orgtree_list_tiers")
            assert r.status == 503 and "secret" not in r.text, (bad, r)
    finally:
        api._providers_payload = old


@t("list_tiers serves the image-input and reasoning-parameter declarations "
   "as true/false/null over a real agent request, and refuses any other value")
def _():
    """Unit C (2026-09-05): the two declarations that joined `tools`, through
    the same gateway, one field at a time. Each is what openrouter.ai's
    CATALOG declared and never an observation: orgtree sends image blocks and
    `--effort` to every OpenRouter seat regardless, and this test says nothing
    about what a turn did with them.

    ⚠ MEASURED BEFORE THE CHANGE: naming either field in the projection's
    allowlist without its tristate branch failed the WHOLE discovery document
    — every OpenRouter tier read as malformed. So the 200s below are not
    free, and the 503s prove the arm still refuses near-misses by identity.
    """
    old = api._providers_payload

    def fixture(**caps):
        row = {"tier": "or-fixture", "provider": "openrouter",
               "model": "vendor/fixture", "seat": 1.0}
        row.update(caps)
        return {"providers": [{
            "id": "openrouter", "label": "OpenRouter", "hire_enabled": True,
            "reason": None, "tiers": [row]}]}

    try:
        for field in ("image", "reasoning"):
            for declared in (True, False, None):
                api._providers_payload = (
                    lambda f=field, d=declared: fixture(**{f: d}))
                r = ag(NID, "orgtree_list_tiers")
                ok200(r, f"{field}={declared!r} over the gateway")
                row = r.json["providers"][0]["tiers"][0]
                assert row[field] is declared, (field, declared, row)
                assert field in row, row          # present-as-null, not dropped
            for bad in (1, 0, 1.0, "true", "", [], {}, "secret"):
                api._providers_payload = lambda f=field, b=bad: fixture(**{f: b})
                r = ag(NID, "orgtree_list_tiers")
                assert r.status == 503 and "secret" not in r.text, (field, bad, r)
        # all three together, mixed states, in ONE row — the shape a real
        # favorite produces (e.g. tools true, image false, reasoning unknown)
        api._providers_payload = lambda: fixture(tools=True, image=False,
                                                 reasoning=None)
        r = ag(NID, "orgtree_list_tiers")
        ok200(r, "mixed tristate row")
        row = r.json["providers"][0]["tiers"][0]
        assert (row["tools"], row["image"], row["reasoning"]) == (True, False, None), row
    finally:
        api._providers_payload = old


@t("unknown tool → 422 with the name echoed")
def _():
    r = ag(NID, "orgtree_nope")
    assert r.status == 422 and "unknown orgtree tool" in r.text, r


@t("unknown node → 422")
def _():
    assert ag("ghost", "orgtree_chart").status == 422


@t("unknown org → 422")
def _():
    assert ag(NID, "orgtree_chart", org="no-such-org").status == 422


@t("read_transcript UPWARD is refused (§7.6 is downward-only)")
def _():
    r = ag(NID2, "orgtree_read_transcript", {"node": NID})
    assert r.status == 422 and "DOWNWARD" in r.text, r


@t("read_transcript of an unrelated id is refused")
def _():
    assert ag(NID2, "orgtree_read_transcript", {"node": "ghost"}).status == 422


@t("read_transcript of self and of a descendant are allowed")
def _():
    ok200(ag(NID, "orgtree_read_transcript", {"node": NID}), "self")
    ok200(ag(NID, "orgtree_read_transcript", {"node": NID2}), "descendant")


@t("read_scratch UPWARD is refused")
def _():
    r = ag(NID2, "orgtree_read_scratch", {"node": NID, "path": ""})
    assert r.status == 422 and "DOWNWARD" in r.text, r


@t("read_scratch cannot climb out of the scratch space")
def _():
    for bad in ["../../../../../../Users", "..\\..\\..\\..\\..\\Users",
                "uploads/../../../..", "....//....//"]:
        r = ag(NID, "orgtree_read_scratch", {"node": NID, "path": bad})
        no500(r, f"read_scratch {bad!r}")
        assert r.status == 422 or "no such path" in r.text, (bad, r)


@t("read_scratch refuses an absolute host path")
def _():
    r = ag(NID, "orgtree_read_scratch", {"node": NID, "path": "C:\\Windows"})
    no500(r, "read_scratch absolute")
    assert "Windows" not in r.text or "no such path" in r.text, r


@t("send_file cannot exfiltrate a file outside the node's holdings")
def _():
    r = ag(NID, "orgtree_send_file",
           {"path": os.path.join(store.DATA_ROOT, "orgs", K + ".json")})
    assert r.status == 422 and "only files in your" in r.text, r


@t("send_file with no path → 422, not a crash")
def _():
    assert ag(NID, "orgtree_send_file", {}).status == 422


@t("an agent cannot address a node it has no channel to")
def _():
    assert ag(NID2, "orgtree_message", {"to": "ghost", "body": "hi"}).status == 422


@t("an agent cannot hire without an explicit spec (no defaults for agents)")
def _():
    r = ag(NID, "orgtree_hire", {"tier": "haiku", "name": "sneaky"})
    no500(r, "agent hire without a spec")
    assert r.status == 422, r


@t("the ceiling-raise offer never rides an agent result")
def _():
    for tool, args in [("orgtree_chart", {}),
                       ("orgtree_status", {"status": "working"}),
                       ("orgtree_retool", {"node": NID2,
                                           "org_visibility": "self"})]:
        r = ag(NID, tool, args)
        assert "bridge" not in (r.json or {}), (tool, r)


@t("orgtree_list_orgs hides kiosk orgs from agents")
def _():
    r = ag(SNID, "orgtree_list_orgs", org=SBX)
    ok200(r, "list_orgs")
    slugs = [o["slug"] for o in r.json["orgs"]]
    assert K not in slugs and K2 not in slugs, slugs
    assert SBX in slugs and ADMIN_SLUG in slugs, slugs


@t("REGRESSION: a non-numeric `last` is 422, not a 500")
def _():
    r = ag(NID, "orgtree_read_transcript", {"node": NID, "last": "abc"})
    no500(r, "last=abc")
    assert r.status == 422 and "must be a number" in r.text, r


@t("REGRESSION: a non-numeric `delta` is 422, not a 500")
def _():
    r = ag(NID, "orgtree_reallocate", {"node": NID2, "delta": "x"})
    no500(r, "delta=x")
    assert r.status == 422, r


@t("REGRESSION: a non-numeric `grant` is 422, not a 500")
def _():
    r = ag(NID, "orgtree_hire",
           {"tier": "haiku", "name": "n", "grant": "x", "charter": "c",
            "tools": {}, "add_dirs": [], "org_visibility": "self"})
    no500(r, "grant=x")
    assert r.status == 422, r


@t("a numeric STRING argument still works (LLMs send those)")
def _():
    ok200(ag(NID, "orgtree_read_transcript", {"node": NID, "last": "5"}),
          "last='5'")


@t("a float argument is coerced, not crashed")
def _():
    ok200(ag(NID, "orgtree_read_transcript", {"node": NID, "last": 7.9}),
          "last=7.9")


AGENT_TOOLS = ["orgtree_message", "orgtree_hire", "orgtree_retool",
               "orgtree_retire", "orgtree_rehire", "orgtree_move",
               "orgtree_dissolve", "orgtree_reallocate", "orgtree_switch_model",
               "orgtree_status", "orgtree_audience", "orgtree_request_credits",
               "orgtree_read_transcript", "orgtree_read_scratch",
               "orgtree_chart", "orgtree_send_file", "orgtree_list_orgs",
               "orgtree_swap", "orgtree_self_subjugate", "orgtree_interrupt"]
JUNK_ARGS = [
    {},
    {"node": 5, "to": [], "body": {}, "tier": 1, "grant": None, "delta": [],
     "path": 3, "action": 9, "status": [], "last": {}, "name": True,
     "new_limit": "x", "tools": "nope", "add_dirs": "nope"},
    {"node": "", "to": "", "body": "", "tier": "", "name": "", "path": "",
     "action": "", "status": ""},
    {"node": NID2, "to": NID, "body": "\u0000", "delta": -10 ** 9,
     "grant": -1, "last": -5, "path": "\u0000", "tier": "\u202e"},
    {"node": NID2, "body": "x" * 100000, "name": "y" * 5000,
     "charter": "z" * 100000, "delta": 10 ** 18, "grant": 10 ** 18},
]


def _agent_fuzz(tool):
    def go():
        for a in JUNK_ARGS:
            r = ag(NID, tool, a)
            no500(r, f"{tool} args={str(a)[:70]}")
    return go


for _tool in AGENT_TOOLS:
    check(f"{_tool} survives {len(JUNK_ARGS)} hostile arg sets",
          _agent_fuzz(_tool))


# ------------------------------ §8b retire/dissolve interrupt a busy target
print("\n§8b  retire/dissolve interrupt a busy target before archiving")


class _FakeProc:
    """A stand-in for the live CLI subprocess `interrupt_turn` writes the
    control_request interrupt to — records the write without a real process.
    If `settle_after` is given, the WRITE ITSELF (not an independent timer)
    schedules `busy`/`proc`/`responding` to clear shortly after — matching
    the real system's own dependency (the turn only ends once the interrupt
    it was sent actually lands) rather than racing it. An earlier, timer-only
    version cleared on its own clock regardless of whether interrupt_turn had
    even been called yet, and would occasionally settle BEFORE a slow test
    run reached the write — a false negative in the fixture, not the code."""
    def __init__(self, slug=None, nid=None, settle_after=None):
        self.writes = []
        self._slug, self._nid, self._settle_after = slug, nid, settle_after

        class _Stdin:
            def write(_s, data):
                self.writes.append(data)
                if self._settle_after is not None:
                    def clear():
                        time.sleep(self._settle_after)
                        # ⚠ state() takes _state_lock ITSELF and the lock is
                        # NOT reentrant — resolve the dict first, mutate
                        # under the lock second. Doing it the other way round
                        # self-deadlocks this thread WHILE IT HOLDS the lock,
                        # which wedges every other thread that touches
                        # supervisor state (measured: it hung the whole
                        # suite, and the stall LOOKED like it was hundreds of
                        # checks earlier because redirected stdout is
                        # block-buffered).
                        st = supervisor.state(self._slug, self._nid)
                        with supervisor._state_lock:            # noqa: SLF001
                            st["busy"] = False
                            st["proc"] = None
                            st["responding"] = False
                    threading.Thread(target=clear, daemon=True).start()

            def flush(_s):
                pass
        self.stdin = _Stdin()


def _mark_busy(slug, nid, settle_after=None):
    """Simulate a node genuinely mid-turn: `responding`+`proc` so
    interrupt_turn has something to write to, `busy` so the caller has
    something to wait on. See _FakeProc for what `settle_after` does."""
    st = supervisor.state(slug, nid)
    proc = _FakeProc(slug, nid, settle_after)
    with supervisor._state_lock:                                # noqa: SLF001
        st["responding"] = True
        st["proc"] = proc
        st["busy"] = True
    return proc


@t("orgtree_retire (mcptool door) interrupts a busy target and waits for it "
   "to settle before archiving")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/ops",
             {"op": "hire", "tier": "haiku", "name": "busy-retire",
              "parent": NID, "grant": 0})
    ok200(r, "hire busy-retire")
    target = r.json["node"]
    proc = _mark_busy(K, target, settle_after=0.15)
    r = ag(NID, "orgtree_retire", {"node": target})
    ok200(r, "retire of a busy target")
    assert proc.writes and '"subtype": "interrupt"' in proc.writes[0], \
        f"the control_request interrupt was never sent: {proc.writes!r}"
    assert not (r.json.get("warnings") or []), \
        f"settled well inside the timeout, no warning expected: {r.json}"
    assert store.load_org(K).node(target)["state"] == "archived", \
        "the node was not actually retired"
    assert supervisor.state(K, target)["busy"] is False, \
        "retire returned before the turn boundary settled"


@t("orgtree_dissolve (mcptool door) interrupts every live descendant first")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/ops",
             {"op": "hire", "tier": "haiku", "name": "busy-parent",
              "parent": NID, "grant": 5})
    ok200(r, "hire busy-parent")
    parent = r.json["node"]
    r = call(ADMIN, "POST", f"/api/orgs/{K}/ops",
             {"op": "hire", "tier": "haiku", "name": "busy-child",
              "parent": parent, "grant": 0})
    ok200(r, "hire busy-child")
    child = r.json["node"]
    proc_p = _mark_busy(K, parent, settle_after=0.15)
    proc_c = _mark_busy(K, child, settle_after=0.15)
    r = ag(NID, "orgtree_dissolve", {"node": parent})
    ok200(r, "dissolve with a busy subtree")
    for proc in (proc_p, proc_c):
        assert proc.writes and '"subtype": "interrupt"' in proc.writes[0], \
            f"a subtree member was never interrupted: {proc.writes!r}"
    org = store.load_org(K)
    assert org.node(parent)["state"] == "archived"
    assert org.node(child)["state"] == "archived"


@t("retire (UI ops door) interrupts a busy target the same way")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/ops",
             {"op": "hire", "tier": "haiku", "name": "busy-ui",
              "parent": NID, "grant": 0})
    ok200(r, "hire busy-ui")
    target = r.json["node"]
    proc = _mark_busy(K, target, settle_after=0.15)
    r = call(ADMIN, "POST", f"/api/orgs/{K}/ops", {"op": "retire", "node": target})
    ok200(r, "retire via the ops door")
    assert proc.writes and '"subtype": "interrupt"' in proc.writes[0]
    assert store.load_org(K).node(target)["state"] == "archived"


@t("a target that never settles is archived anyway, with a warning — retire "
   "never hangs forever")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/ops",
             {"op": "hire", "tier": "haiku", "name": "wedged",
              "parent": NID, "grant": 0})
    ok200(r, "hire wedged")
    target = r.json["node"]
    _mark_busy(K, target)      # no settle_after: busy stays True forever
    warnings = supervisor.interrupt_before_archive(
        K, store.load_org(K), target, timeout=0.2)
    assert warnings and target in warnings[0], warnings
    _st = supervisor.state(K, target)       # resolve OUTSIDE the lock (above)
    with supervisor._state_lock:                                 # noqa: SLF001
        _st["busy"] = False                       # tidy up for later tests


@t("orgtree_interrupt: stops a busy target WITHOUT archiving it, fires and "
   "returns (does not wait to settle)")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/ops",
             {"op": "hire", "tier": "haiku", "name": "just-interrupt",
              "parent": NID, "grant": 0})
    ok200(r, "hire just-interrupt")
    target = r.json["node"]
    proc = _mark_busy(K, target)      # never settles on its own
    t0 = time.monotonic()
    r = ag(NID, "orgtree_interrupt", {"node": target})
    elapsed = time.monotonic() - t0
    ok200(r, "orgtree_interrupt")
    assert r.json.get("interrupted") is True, r.json
    assert proc.writes and '"subtype": "interrupt"' in proc.writes[0]
    assert elapsed < 2.0, \
        f"orgtree_interrupt waited {elapsed:g}s — it must fire and return"
    assert store.load_org(K).node(target)["state"] == "live", \
        "orgtree_interrupt must never archive the node"
    _st = supervisor.state(K, target)       # resolve OUTSIDE the lock (above)
    with supervisor._state_lock:                                 # noqa: SLF001
        _st["busy"] = False                                      # tidy up


@t("orgtree_interrupt: refused on a node outside the caller's subtree")
def _():
    r = ag(NID2, "orgtree_interrupt", {"node": NID})
    assert r.status == 422, r
    r = ag(NID2, "orgtree_interrupt", {"node": NID2})   # no self-interrupt
    assert r.status == 422, r


@t("orgtree_interrupt: a no-op with a reason when the target is not mid-turn")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/ops",
             {"op": "hire", "tier": "haiku", "name": "idle-node",
              "parent": NID, "grant": 0})
    ok200(r, "hire idle-node")
    target = r.json["node"]
    r = ag(NID, "orgtree_interrupt", {"node": target})
    ok200(r, "orgtree_interrupt on an idle node")
    assert r.json.get("interrupted") is False and r.json.get("reason"), r.json


# ------------------------------------------------- §9 uploads and traversal
print("\n§9  uploads, scratch and path traversal")


@t("☞ REGRESSION: /scratch no longer accepts an unresolved node id")
def _():
    """THE hole this suite was written for. `nid` went straight into
    supervisor.scratch_dir (which os.path.joins it and mkdirs the result), and
    the containment check then anchored on the ESCAPED base — so
    nid = `..\\..\\..\\..\\Users` listed the operator's filesystem over the
    public kiosk URL, and any file under it read back 60 KB at a time."""
    for evil in ["..\\..\\..\\..", "..\\..\\..\\..\\..\\..\\Users",
                 "../../../..", "..", ".", "boss\\..\\..\\..",
                 "\u002e\u002e\\\u002e\u002e", "..\\..\\..\\..\\..\\..\\.."]:
        r = pub("GET", f"/api/orgs/{K}/nodes/{evil}/scratch")
        no500(r, f"scratch nid={evil!r}")
        # a slash-bearing id never matches the route and lands on the SPA;
        # everything else has to be refused by the node lookup
        assert r.status in (404, 422) or (r.status == 200 and r.json is None), \
            f"nid={evil!r} → {r!r}"
        assert "entries" not in r.text and '"file"' not in r.text, \
            f"nid={evil!r} LISTED: {r!r}"


@t("REGRESSION: /scratch on an unknown node is 404 (it used to mkdir one)")
def _():
    r = pub("GET", f"/api/orgs/{K}/nodes/ghost/scratch")
    assert r.status == 404 and "no such node" in r.text, r


@t("REGRESSION: a message with attachments validates the node first")
def _():
    # (targeted, not a whole-dir diff: this temp parent is shared with every
    # other process on the machine, so a set difference is not evidence)
    outside = os.path.dirname(store.DATA_ROOT)
    for stale in (os.path.join(outside, "pwned-by-test"),
                  os.path.join(store.DATA_ROOT, "pwned-by-test")):
        if os.path.isdir(stale):
            os.rmdir(stale)        # a previous run on unfixed code left it
    r = call(ADMIN, "POST",
             f"/api/orgs/{K}/nodes/..\\..\\..\\pwned-by-test/message",
             {"text": "hi", "attachments": ["a"]})
    assert r.status == 404, r
    assert not os.path.exists(os.path.join(outside, "pwned-by-test")), \
        "a directory was created outside the data root"
    assert not os.path.exists(os.path.join(store.DATA_ROOT, "pwned-by-test"))


@t("a real node's scratch lists normally")
def _():
    r = pub("GET", f"/api/orgs/{K}/nodes/{NID}/scratch")
    ok200(r, "scratch")
    assert r.json["dir"] == ".", r


def _scratch_path(bad):
    def go():
        r = call(ADMIN, "GET", f"/api/orgs/{K}/nodes/{NID}/scratch",
                 query=("path=" + bad).encode())
        no500(r, f"scratch path={bad!r}")
        assert r.status in (200, 404, 422), r
        if r.status == 200:
            assert "entries" in (r.json or {}) or "file" in (r.json or {}), r
            assert "Windows" not in r.text and "passwd" not in r.text, r
    return go


for _bad in ["../../../..", "..\\..\\..\\..", "/etc/passwd", "C:\\Windows",
             "%2e%2e%2f", "....//....//", "uploads/../../../..",
             "\\\\?\\C:\\Windows", "uploads\\..\\..\\..\\.."]:
    check(f"scratch path {_bad!r} cannot escape", _scratch_path(_bad))


@t("upload: a traversal filename is flattened into uploads/")
def _():
    for name in ["../../../pwn.txt", "..\\..\\pwn2.txt", "C:\\pwn3.txt",
                 "....", "...", "..", "/", "\\", "a/b/c.txt", "\u0000x",
                 "con.txt", " .. ", "x" * 500]:
        r = call(ADMIN, "POST", f"/api/orgs/{K}/nodes/{NID}/upload", raw=b"xy",
                 query=b"name=" + name.encode("utf-8", "replace"))
        no500(r, f"upload name={name!r}")
        ok200(r, f"upload name={name!r}")
        assert r.json["path"].startswith("uploads/"), r
        assert ".." not in r.json["path"] and ":" not in r.json["path"], r
    up = os.path.realpath(os.path.join(supervisor.scratch_dir(K, NID),
                                       "uploads"))
    for f in os.listdir(up):
        assert os.path.realpath(os.path.join(up, f)).startswith(up + os.sep), f


@t("upload: an empty body is 422")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/nodes/{NID}/upload", raw=b"")
    assert r.status == 422, r


@t("upload: over the 25 MB per-file cap is 413")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/nodes/{NID}/upload",
             raw=b"x" * (api._UPLOAD_MAX + 1))
    assert r.status == 413 and "25 MB" in r.text, r


@t("upload: a kiosk's per-node total cap is enforced")
def _():
    old = api._UPLOAD_KIOSK_TOTAL
    api._UPLOAD_KIOSK_TOTAL = 32
    try:
        r = call(ADMIN, "POST", f"/api/orgs/{K}/nodes/{NID}/upload",
                 raw=b"y" * 64, query=b"name=big.bin")
        assert r.status == 413 and "upload space is full" in r.text, r
    finally:
        api._UPLOAD_KIOSK_TOTAL = old


@t("upload: an unknown node is 404 before any filesystem touch")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/nodes/..%5C..%5Cx/upload", raw=b"z")
    assert r.status == 404, r


@t("upload: a storage-blocked org refuses with 413")
def _():
    with store.DOC_LOCK:
        o = store.load_org(K)
        o.d["storage_blocked"] = True
        store.save_org(o)
    try:
        r = call(ADMIN, "POST", f"/api/orgs/{K}/nodes/{NID}/upload", raw=b"a")
        assert r.status == 413, r
    finally:
        with store.DOC_LOCK:
            o = store.load_org(K)
            o.d["storage_blocked"] = False
            store.save_org(o)


@t("/file cannot serve outside the node's scratch")
def _():
    for bad in ["../../../../orgs/" + K + ".json", "..\\..\\..\\..\\x",
                "C:\\Windows\\win.ini", "", "..", "uploads/../../.."]:
        r = call(ADMIN, "GET", f"/api/orgs/{K}/nodes/{NID}/file",
                 query=("path=" + bad).encode())
        no500(r, f"file path={bad!r}")
        assert r.status in (404, 422), (bad, r)


@t("/file serves a real upload back")
def _():
    r = call(ADMIN, "GET", f"/api/orgs/{K}/nodes/{NID}/file",
             query=b"path=uploads/pwn.txt")
    ok200(r, "download")
    assert r.body == b"xy", r.body[:40]


# ------------------------------------------ §9b the org disk (recovery browser)
print("\n§9b the org-disk recovery browser")

FAKEDISK = tempfile.mkdtemp(prefix="orgtree-fakedisk-")
os.makedirs(os.path.join(FAKEDISK, "home", "orgtree"), exist_ok=True)
os.makedirs(os.path.join(FAKEDISK, "usr", "lib"), exist_ok=True)
DISK_SECRET = "cafebabe" * 4
with open(os.path.join(FAKEDISK, "home", "orgtree", ".bridge"), "w") as _f:
    json.dump({"url": "http://host.docker.internal:7362",
               "secret": DISK_SECRET}, _f)
with open(os.path.join(FAKEDISK, "home", ".credentials.json"), "w") as _f:
    _f.write('{"oauth":"HOST-OAUTH-TOKEN"}')
with open(os.path.join(FAKEDISK, "home", "notes.txt"), "w") as _f:
    _f.write("ordinary agent output")
with open(os.path.join(FAKEDISK, "usr", "lib", "libc.so"), "w") as _f:
    _f.write("seed")

dsk.windows_path = lambda slug: FAKEDISK
dsk.usage = lambda slug, max_age=15.0: (1024, 4096)
dsk.enumerate_by_size = lambda slug, limit=500, offset=0: [
    {"path": "home/orgtree/.bridge", "size": 90},
    {"path": "home/.credentials.json", "size": 22},
    {"path": "home/notes.txt", "size": 21},
    {"path": "usr/lib/libc.so", "size": 999}][offset:offset + limit]
dsk.list_dir = lambda slug, rel="", **kw: (
    [{"path": "home", "dir": True, "size": 133}] if not rel else [])
dsk.subtree_files = lambda slug, rel, **kw: []
dsk.invalidate = lambda slug: None
with store.DOC_LOCK:
    _o = store.load_org(K)
    _o.d["disk"] = {"size_mb": 4096}
    store.save_org(_o)


@t("☞ REGRESSION: the .bridge secret file is NOT served to a visitor")
def _():
    """`{home}/orgtree/.bridge` is written by sandbox.py and holds the org's
    bridge secret. The bridge listener binds 0.0.0.0, so downloading this gave
    a kiosk visitor the /api/agent gateway the public matrix freezes, the node
    steer fetch, and the /anthropic proxy — which attaches the HOST's
    subscription token."""
    r = pub("GET", f"/api/orgs/{K}/disk/file",
            query=b"path=home/orgtree/.bridge")
    assert r.status == 403, r
    assert DISK_SECRET not in r.text, "the secret came back anyway"


@t("the engine credential file is not served to a visitor")
def _():
    r = pub("GET", f"/api/orgs/{K}/disk/file",
            query=b"path=home/.credentials.json")
    assert r.status == 403 and "HOST-OAUTH" not in r.text, r


@t("an ordinary file on the disk still downloads for a visitor")
def _():
    r = pub("GET", f"/api/orgs/{K}/disk/file", query=b"path=home/notes.txt")
    ok200(r, "notes.txt")
    assert b"ordinary" in r.body, r.body[:60]


@t("the listing classifies both secret files as blocked for a visitor")
def _():
    r = pub("GET", f"/api/orgs/{K}/disk")
    ok200(r, "disk list")
    by = {f["path"]: f for f in r.json["files"]}
    assert by["home/orgtree/.bridge"]["class"] == "blocked", by
    assert by["home/.credentials.json"]["class"] == "blocked", by
    assert by["usr/lib/libc.so"]["class"] == "blocked", by      # system seed
    assert by["home/notes.txt"]["class"] == "content", by
    assert DISK_SECRET not in r.text


@t("the visitor's disk payload hides the admin-only host numbers")
def _():
    r = pub("GET", f"/api/orgs/{K}/disk")
    for key in ("vm_cap_mib", "size_mb", "pending_mb"):
        assert key not in r.json, f"{key} leaked to a visitor"


@t("…which the admin listener does get")
def _():
    assert "vm_cap_mib" in call(ADMIN, "GET", f"/api/orgs/{K}/disk").json


@t("a visitor cannot delete the secret or seed files")
def _():
    r = pub("POST", f"/api/orgs/{K}/disk/delete",
            {"paths": ["home/orgtree/.bridge", "home/.credentials.json",
                       "usr/lib/libc.so"]})
    ok200(r, "disk delete")
    assert all(x["ok"] is False for x in r.json["results"]), r
    assert os.path.isfile(os.path.join(FAKEDISK, "home", "orgtree", ".bridge"))
    assert os.path.isfile(os.path.join(FAKEDISK, "usr", "lib", "libc.so"))


def _disk_path(bad):
    def go():
        r = pub("GET", f"/api/orgs/{K}/disk/file",
                query=("path=" + bad).encode())
        no500(r, f"disk path={bad!r}")
        assert r.status in (403, 404, 422), (bad, r)
        assert "HOST-OAUTH" not in r.text and DISK_SECRET not in r.text, r
    return go


for _bad in ["../../../x", "..\\..\\x", "/etc/passwd", "C:\\Windows\\win.ini",
             "", ".", "..", "home/../../escape", "home/./../../escape",
             "C:home/notes.txt", "home/orgtree/../orgtree/.bridge"]:
    check(f"disk path {_bad!r} is refused", _disk_path(_bad))


@t("disk pagination clamps absurd values")
def _():
    r = pub("GET", f"/api/orgs/{K}/disk",
            query=b"offset=-999999&limit=999999999")
    ok200(r, "disk pagination")
    assert r.json["limit"] == 500 and r.json["offset"] == 0, r.json


@t("disk resize stays admin-only even with a valid kiosk token")
def _():
    r = pub("POST", f"/api/orgs/{K}/disk/resize", {"size_mb": 999999})
    assert r.status == 403 and "admin side only" in r.text, r


with store.DOC_LOCK:
    _o = store.load_org(K)
    _o.d.pop("disk", None)
    store.save_org(_o)

# ------------------------------------------------------- §10 no endpoint 500s
print("\n§10 failure modes — nothing 500s")


@t("REGRESSION: a NUL in a path is 422 everywhere, not a ValueError 500")
def _():
    for p in [f"/api/orgs/{K}/nodes/{NID}/scratch",
              f"/api/orgs/{K}/nodes/{NID}/file"]:
        r = call(ADMIN, "GET", p, query=b"path=%00")
        no500(r, p)
        assert r.status == 422 and "null byte" in r.text, (p, r)
    r = call(ADMIN, "POST", f"/api/orgs/{K}/nodes/{NID}/message",
             {"text": "hi", "attachments": ["\u0000"]})
    no500(r, "message attachments NUL")
    for tool, args in [("orgtree_read_scratch", {"node": NID, "path": "\u0000"}),
                       ("orgtree_send_file", {"path": "\u0000"})]:
        no500(ag(NID, tool, args), f"agent {tool} NUL")


@t("REGRESSION: malformed org_dirs is 422, not an AttributeError 500")
def _():
    for bad in [[None], [{"path": 123, "mode": "rw"}], [5], [[]], [{}],
                [{"mode": "rw"}], [{"path": None}], [True]]:
        r = call(ADMIN, "POST", f"/api/orgs/{ADMIN_SLUG}/settings",
                 {"org_dirs": bad})
        no500(r, f"org_dirs={bad!r}")
        assert r.status == 422, (bad, r)


@t("REGRESSION: ?last=0 on /history no longer means 'the entire log'")
def _():
    for q in (b"last=0", b"last=-1", b"last=-999"):
        r = call(ADMIN, "GET", f"/api/orgs/{K}/nodes/{NID}/history", query=q)
        no500(r, str(q))
        assert len(r.json["items"]) <= 1, (q, len(r.json["items"]))


@t("an org name the host filesystem refuses is a 4xx, not a 500")
def _():
    for nm in ["", "   ", "///", "..", "\U0001f389", "-", "a" * 400, "con",
               "  lead", "trail  ", "\u0000x", ".hidden", "a:b", "x" * 130]:
        no500(call(ADMIN, "POST", "/api/orgs", {"name": nm}),
              f"create name={nm[:20]!r}")


@t("a slug that is a filename trick cannot reach outside orgs/")
def _():
    with open(os.path.join(store.DATA_ROOT, "outside.json"), "w") as f:
        json.dump({"slug": "x", "workspace": "C:/OPERATOR/SECRET",
                   "nodes": {}}, f)
    for s in ["..\\outside", "../outside", ".", "..", "con", "a:b", "x*",
              'q"', "sl/ash", "-lead", ".dot", "a" * 200, "%2e%2e"]:
        for suffix in ("", "/orgmd", "/events", "/inbox"):
            r = call(ADMIN, "GET", f"/api/orgs/{s}{suffix}")
            no500(r, f"GET slug={s!r}{suffix}")
            assert "OPERATOR" not in r.text, (s, r)
        no500(call(ADMIN, "DELETE", f"/api/orgs/{s}"), f"DELETE slug={s!r}")
    assert os.path.isfile(os.path.join(store.DATA_ROOT, "outside.json")), \
        "a slug trick moved a file that lives outside orgs/"
    assert os.path.isfile(os.path.join(store.DATA_ROOT, "defaults.json")) \
        or not os.path.exists(os.path.join(store.DATA_ROOT, "defaults.json")), \
        "defaults.json was moved"


JUNK_BODIES = [
    None, {}, [], "not-a-dict", 12345,
    {"op": None}, {"op": ["hire"]}, {"op": "hire", "grant": "many"},
    {"op": "hire", "tier": "opus", "name": "x", "add_dirs": "nope"},
    {"text": None}, {"text": ""}, {"text": " \n\t "}, {"text": "\u0000"},
    {"text": "/"}, {"text": "//"}, {"text": "/compact extra"},
    {"text": "x" * 200000}, {"text": "\U0001f4a5" * 5000},
    {"text": "hi", "attachments": ["../../../x"] * 50},
    {"ids": None}, {"ids": [None]}, {"ids": ["x"] * 5000},
    {"action": "", "node": ""}, {"action": "grant", "node": None},
    {"paths": None}, {"paths": ["\u0000"]}, {"paths": ["x"] * 2000},
    {"content": None}, {"content": "\u0000"}, {"content": "y" * 100000},
    {"size_mb": -1}, {"size_mb": 10 ** 12}, {"size_mb": "big"},
    {"before": 1, "after": []}, {"id": None, "action": None},
    {"credits": -5}, {"credits": 10 ** 15}, {"spend_limit": 1e308},
    {"max_top_grant": -1}, {"compact_at": -100}, {"compact_at": 10 ** 9},
    {"default_visibility": "\u202e"}, {"default_effort": "\u0000"},
    {"org": "", "node": "", "tool": ""}, {"org": K, "body": ""},
    {"add_dirs": "nope"}, {"tools": "nope"}, {"tools": {"bash": "yes"}},
    {"max_scope": "nope"}, {"max_scope": {"tools": 5}},
    {"rotate_token": "yes"}, {"enabled": "maybe"},
]
POST_TARGETS = [
    "/api/orgs", "/api/defaults", f"/api/orgs/{ADMIN_SLUG}/settings",
    f"/api/orgs/{K}/kiosk", f"/api/orgs/{K}/defaults",
    f"/api/orgs/{K}/nodes/{NID}/scope", f"/api/orgs/{K}/nodes/{NID}/reorder",
    f"/api/orgs/{K}/nodes/{NID}/message", f"/api/orgs/{K}/nodes/{NID}/steer",
    f"/api/orgs/{K}/nodes/{NID}/interrupt",
    f"/api/orgs/{K}/nodes/{NID}/compact",
    f"/api/orgs/{K}/killswitch", f"/api/orgs/{K}/resume",
    f"/api/orgs/{K}/credit-requests", f"/api/orgs/{K}/inbox/read",
    f"/api/orgs/{K}/inbox/clear", f"/api/orgs/{K}/org_inbox/read",
    f"/api/orgs/{K}/audiences", "/api/extern/peer1/send", "/api/agent",
    f"/api/orgs/{K}/nodes/{NID}/upload", f"/api/orgs/{K}/disk/delete",
    f"/api/orgs/{K}/disk/resize", f"/api/orgs/{K}/disk/resize/apply",
    f"/api/orgs/{K}/sweep-legacy", f"/api/orgs/{K}/ops",
]
RAW_BODIES = [b"", b"{", b"null", b"[]", b'{"a":', b"\xff\xfe\x00",
              b'{"text": "\\ud800"}', b"0" * 100000]


def _fuzz_post(tgt):
    def go():
        for b in JUNK_BODIES:
            r = call(ADMIN, "POST", tgt, b)
            no500(r, f"POST {tgt} body={str(b)[:60]}")
        for raw in RAW_BODIES:
            r = call(ADMIN, "POST", tgt, raw=raw)
            no500(r, f"POST {tgt} raw={raw[:20]!r}")
    return go


for _tgt in POST_TARGETS:
    check(f"POST {_tgt} survives {len(JUNK_BODIES) + len(RAW_BODIES)} "
          f"hostile bodies", _fuzz_post(_tgt))


@t("the fuzz legitimately rotated the kiosk token — re-sync and keep going")
def _():
    # `{"rotate_token": "yes"}` is a VALID request, so the fuzz above really
    # did mint a new URL. Anything below that speaks as a visitor needs the
    # current one; the old one must be dead.
    global TOKEN
    old, TOKEN = TOKEN, store.load_org(K).d["kiosk"]["token"]
    if TOKEN != old:
        assert call(PUBLIC, "GET", f"/k/{old}/api/orgs").status == 404
    ok200(call(PUBLIC, "GET", f"/k/{TOKEN}/api/orgs"), "re-synced token")

QUERY_TARGETS = [
    ("/api/fs", [b"", b"path=", b"path=%00", b"path=C:\\nope-xyz",
                 b"path=" + b"a" * 5000, b"path=\\\\?\\C:\\", b"path=.",
                 b"path=..", b"path=CON"]),
    ("/api/orgs/{K}/events", [b"since=-1", b"since=0", b"since=9" * 40,
                              b"since=abc", b"since="]),
    ("/api/orgs/{K}/nodes/{NID}/history",
     [b"last=0", b"last=-9", b"last=99999999", b"last=abc"]),
    ("/api/orgs/{K}/nodes/{NID}/chat",
     [b"last=0", b"last=-1", b"last=99999999", b"last=1.5"]),
    ("/api/orgs/{K}/nodes/{NID}/scratch",
     [b"path=", b"path=..", b"path=%00", b"path=" + b"x" * 5000]),
    ("/api/orgs/{K}/nodes/{NID}/file",
     [b"path=", b"path=..", b"path=%00", b"path=uploads"]),
    ("/api/orgs/{K}/nodes/{NID}/toolimg/abc",
     [b"", b"idx=-1", b"idx=99999", b"idx=abc"]),
    ("/api/orgs/{K}/disk", [b"offset=-1&limit=0", b"offset=abc",
                            b"limit=-5", b"offset=" + b"9" * 40]),
    ("/api/orgs/{K}/disk/dir", [b"", b"path=..", b"path=%00", b"path=home"]),
    ("/api/orgs/{K}/disk/file", [b"", b"path=", b"path=%00"]),
    ("/api/extern/peer1/messages", [b"", b"org=nope", b"after=", b"after=zzz"]),
    ("/api/orgs/{K}/inbox", [b""]),
    ("/api/orgs/{K}/audiences", [b""]),
    ("/api/orgs/{K}/orgmd", [b""]),
    ("/api/orgs/{K}/nodes/{NID}/inbox", [b""]),
    ("/api/charters", [b""]),
    ("/api/host", [b""]),
    ("/api/mcp-servers", [b""]),
    ("/api/defaults", [b""]),
    ("/api/orgs", [b""]),
    ("/api/orgs/{K}", [b""]),
    ("/api/orgs/{K}/sweep-legacy", [b""]),
]


def _fuzz_get(p, queries):
    def go():
        for q in queries:
            r = call(ADMIN, "GET", p, query=q)
            no500(r, f"GET {p}?{q!r}")
    return go


for _tpl, _queries in QUERY_TARGETS:
    _p = _tpl.format(K=K, NID=NID)
    check(f"GET {_p} survives {len(_queries)} hostile query strings",
          _fuzz_get(_p, _queries))


@t("unknown slugs and node ids 404 on every parameterised GET")
def _():
    for p in [f"/api/orgs/{K}/nodes/ghost/chat",
              f"/api/orgs/{K}/nodes/ghost/inbox",
              f"/api/orgs/{K}/nodes/ghost/history",
              f"/api/orgs/{K}/nodes/ghost/scratch",
              f"/api/orgs/{K}/nodes/ghost/file",
              f"/api/orgs/{K}/nodes/ghost/toolimg/x",
              "/api/orgs/no-such-org", "/api/orgs/no-such-org/events",
              "/api/orgs/no-such-org/inbox", "/api/orgs/no-such-org/orgmd",
              "/api/orgs/no-such-org/audiences"]:
        r = call(ADMIN, "GET", p)
        no500(r, p)
        assert r.status == 404, (p, r)


@t("a message to an unknown node 404s or 422s and posts nothing")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/nodes/ghost/message",
             {"text": "hi"})
    no500(r, "message to ghost")
    assert r.status in (404, 422), r


@t("an empty or whitespace-only message is 422")
def _():
    for txt in ["", " ", "\n", "\t\t", "   \n  "]:
        r = call(ADMIN, "POST", f"/api/orgs/{K}/nodes/{NID}/message",
                 {"text": txt})
        assert r.status == 422, (txt, r)


@t("no whitespace-only body ever reached the mailbox")
def _():
    box = (store.load_org(K).d.get("mail") or {}).get(NID, [])
    assert not any((m.get("body") or "").strip() == "" for m in box), box


@t("extern: a bad peer id is refused with the rule stated")
def _():
    for p in ["@@@", "a" * 65, "a b", "..", "a/b"]:
        r = call(ADMIN, "POST", f"/api/extern/{p}/send", {"org": K, "body": "x"})
        no500(r, f"extern peer={p!r}")
        # 405: a slash-bearing peer never matches the route at all
        assert r.status in (404, 405, 422), (p, r)


@t("extern: a sealed kiosk is indistinguishable from a missing org")
def _():
    a = call(ADMIN, "POST", "/api/extern/peer1/send", {"org": K, "body": "x"})
    b = call(ADMIN, "POST", "/api/extern/peer1/send",
             {"org": "definitely-not-an-org", "body": "x"})
    assert a.status == b.status == 404, (a, b)
    assert a.json["detail"].replace(K, "X") == \
        b.json["detail"].replace("definitely-not-an-org", "X"), (a, b)


@t("extern: wait honours a short timeout and returns empty")
def _():
    t0 = time.monotonic()
    r = call(ADMIN, "GET", "/api/extern/peer1/wait",
             query=b"timeout=1&org=" + ADMIN_SLUG.encode())
    ok200(r, "extern wait")
    assert r.json["messages"] == [], r
    assert time.monotonic() - t0 < 20, "wait overran its timeout"


@t("extern: a negative timeout is clamped, not looped forever")
def _():
    ok200(call(ADMIN, "GET", "/api/extern/peer1/wait", query=b"timeout=-99"),
          "negative timeout")


@t("extern: an attachment that is not a file is 422")
def _():
    r = call(ADMIN, "POST", "/api/extern/peer1/send",
             {"org": ADMIN_SLUG, "body": "x", "attachments": [store.DATA_ROOT]})
    assert r.status == 422 and "not found" in r.text, r


@t("kiosk config: a sandboxed org refuses a sub-4096 MB disk")
def _():
    for i in (0, 1, 4095):
        r = call(ADMIN, "POST", "/api/orgs",
                 {"name": f"tiny-{i}",
                  "kiosk": {"sandbox": True, "storage_limit_mb": i}})
        assert r.status == 422, (i, r)


@t("kiosk config: a non-kiosk org refuses /kiosk with an explanation")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{ADMIN_SLUG}/kiosk", {"credits": 5})
    assert r.status == 422 and "never converted" in r.text, r


@t("kiosk config: the cap cannot go below current holdings")
def _():
    r = call(ADMIN, "POST", f"/api/orgs/{K}/kiosk", {"credits": 1})
    assert r.status == 422 and "below current holdings" in r.text, r


@t("the kiosk credit cap refuses an over-cap hire on the visitor path")
def _():
    # the fuzz above legitimately rewrote the caps (a huge `credits` is a
    # valid admin request), so pin one just above current holdings first
    held = store.load_org(K).audit()["top_level_holds"]
    ok200(call(ADMIN, "POST", f"/api/orgs/{K}/kiosk",
               {"credits": int(held) + 2}), "pin the cap")
    r = pub("POST", f"/api/orgs/{K}/ops",
            {"op": "hire", "tier": "opus", "name": "greedy", "grant": 10 ** 6})
    assert r.status == 422, r
    assert "greedy" not in store.load_org(K).nodes, "the refused hire persisted"


@t("☞ the ceiling CLAMPS a visitor rather than 403ing — and cannot be raised")
def _():
    """The two open write surfaces (/scope, /defaults) plus /ops are where a
    visitor's request meets the kiosk ceiling. Ceiling spec §2 says the ledger
    narrows the request and proceeds; §1 says only an ADMIN may raise it. So:
    narrow the ceiling from the admin side, then check a visitor gets the
    narrowed thing back, that the doc really stored the narrowed thing, and
    that `raise_ceiling: true` in a VISITOR's body is inert."""
    narrow = {"tools": {"bash": False, "web": False, "edit": False,
                        "subagents": False, "mcp": []},
              "add_dirs": [], "org_visibility": "self",
              "permission_mode": "acceptEdits", "max_tier": "haiku"}
    ok200(call(ADMIN, "POST", f"/api/orgs/{K}/kiosk", {"max_scope": narrow}),
          "narrow the ceiling")
    r = pub("POST", f"/api/orgs/{K}/nodes/{NID}/scope",
            {"tools": {"bash": True, "web": True, "edit": True,
                       "subagents": True, "mcp": ["*"]},
             "org_visibility": "full", "raise_ceiling": True})
    ok200(r, "visitor retool")               # clamped, never a 403
    tools = store.load_org(K).nodes[NID]["scope"]["tools"]
    assert tools["bash"] is False and tools["mcp"] == [], tools
    assert store.load_org(K).nodes[NID]["scope"]["org_visibility"] == "self"
    r = pub("POST", f"/api/orgs/{K}/defaults",
            {"default_tools": {"bash": True, "mcp": ["*"]},
             "default_visibility": "full", "raise_ceiling": True})
    ok200(r, "visitor hire-defaults")
    d = store.load_org(K).d
    assert d["default_tools"]["bash"] is False, d["default_tools"]
    assert d["default_visibility"] == "self", d["default_visibility"]
    # the ceiling itself is untouched by everything a visitor just sent
    ms = store.load_org(K).d["kiosk"]["max_scope"]
    assert ms["tools"]["bash"] is False and ms["org_visibility"] == "self", ms
    # …and the tier cap is a HARD refusal, not a clamp
    r = pub("POST", f"/api/orgs/{K}/ops",
            {"op": "hire", "tier": "opus", "name": "toobig", "grant": 0,
             "raise_ceiling": True})
    assert r.status == 422 and "caps agent tier" in r.text, r
    assert store.load_org(K).d["kiosk"]["max_scope"]["max_tier"] == "haiku"
    # the bridge offer is an admin affordance — it must not dangle for them
    for rr in (r,):
        assert "bridge" not in (rr.json or {}), rr


@t("a failed org creation leaves nothing behind (the kiosk unwind path)")
def _():
    before = {o["slug"] for o in store.list_orgs()}
    r = call(ADMIN, "POST", "/api/orgs",
             {"name": "Bad Ceiling",
              "kiosk": {"sandbox": False, "max_scope": {"tools": "nope"}}})
    no500(r, "bad ceiling create")
    if r.status != 200:
        assert {o["slug"] for o in store.list_orgs()} == before, \
            "a half-created org survived its own failure"


# ------------------------------------------- §10b the restart stamp (D-60)
print("\n§10b X-Orgtree-Instance — the browser's restart detector")


@t("every response carries the instance stamp, on all three listeners")
def _():
    seen = set()
    for label, r in (
            ("admin", call(ADMIN, "GET", "/api/orgs")),
            ("admin tree", call(ADMIN, "GET", f"/api/orgs/{K}")),
            ("kiosk", call(PUBLIC, "GET", f"/k/{TOKEN}/api/orgs/{K}")),
            ("bridge", br("POST", "/api/agent",
                          {"org": SBX, "node": SNID, "tool": "orgtree_chart"}))):
        got = r.headers.get("x-orgtree-instance")
        assert got, f"{label} carried no stamp ({r.status})"
        seen.add(got)
    assert seen == {api.INSTANCE}, \
        f"the stamp must be THIS process's id, one value: {seen}"


@t("the stamp rides error responses too — a restart is worth noticing then")
def _():
    for app_, path, want in ((ADMIN, "/api/orgs/zznope", 404),
                             (ADMIN, f"/api/orgs/{K}/nodes/zznope/chat", 404),
                             (PUBLIC, f"/k/{TOKEN}/api/orgs/{K}/nodes/zz/chat",
                              404)):
        r = call(app_, "GET", path)
        assert r.status == want, (path, r.status)
        assert r.headers.get("x-orgtree-instance") == api.INSTANCE, \
            f"{path} ({r.status}) lost the stamp"


@t("a GATEWAY rejection is pre-app and unstamped — stated, not assumed")
def _():
    # The middleware is on the app; the two gateways answer some requests
    # themselves, before it. That is fine for the feature — a browser only ever
    # talks to the admin app or a valid /k/<token>, and both pass through — but
    # it should be a recorded property rather than a surprise to the next
    # person who greps for the header and finds a response without it.
    for label, r in (("bad token", call(PUBLIC, "GET", "/k/badtoken/api/orgs")),
                     ("frozen config surface",
                      call(PUBLIC, "GET", f"/k/{TOKEN}/api/fs")),
                     ("bridge, no secret",
                      br("POST", "/api/agent", {}, secret=None))):
        assert r.status in (403, 404), (label, r.status)
        assert "x-orgtree-instance" not in r.headers, \
            f"{label} is answered by the gateway, above the app"


@t("the stamp is a fresh value per process, and leaks nothing")
def _():
    assert re.fullmatch(r"[0-9a-f]{16}", api.INSTANCE), api.INSTANCE
    # it must not be derived from anything an outsider should not have: no
    # path, no port, no secret, no pid
    for bad in (str(os.getpid()), os.environ["ORGTREE_DATA"], SECRET, TOKEN):
        assert bad not in api.INSTANCE and api.INSTANCE not in bad


@t("index.html is never cached — the reload must not fetch the old bundle")
def _():
    if not os.path.isdir(api.FRONTEND_DIST):
        return                       # nothing built in this checkout
    r = call(ADMIN, "GET", "/")
    assert r.status == 200, r.status
    assert "no-store" in r.headers.get("cache-control", ""), (
        "index.html NAMES the content-hashed bundle files; a cached copy "
        f"reloads straight back into the old app ({r.headers})")


# ----------------------------------------- §10c the working count (F-09)
print("\n§10c the org list's working count")


@t("working_count counts RUNNING turns, per org, and nothing else")
def _():
    st = supervisor.state(K, NID)
    st2 = supervisor.state(K, NID2)
    other = supervisor.state(K2, "someone")
    try:
        assert supervisor.working_count(K) == 0
        st["busy"] = True
        assert supervisor.working_count(K) == 1
        st2["busy"] = True
        assert supervisor.working_count(K) == 2
        other["busy"] = True
        assert supervisor.working_count(K) == 2, \
            "another org's running turn must not be counted here"
        assert supervisor.working_count(K2) == 1
    finally:
        for s in (st, st2, other):
            s["busy"] = False
    assert supervisor.working_count(K) == 0


@t("a QUEUED message is not 'working' — only a running turn is")
def _():
    st = supervisor.state(K, NID)
    st["queue"].append("waiting to be sent")
    try:
        assert supervisor.working_count(K) == 0, (
            "a node with a queued message and no running turn is not working "
            "(the desk's starting… line and the queue badge cover that state)")
    finally:
        st["queue"].clear()


@t("the admin org list carries the count; a busy agent raises it")
def _():
    row = next(o for o in call(ADMIN, "GET", "/api/orgs").json if o["slug"] == K)
    assert row.get("working") == 0, row
    st = supervisor.state(K, NID)
    st["busy"] = True
    try:
        row = next(o for o in call(ADMIN, "GET", "/api/orgs").json
                   if o["slug"] == K)
        assert row["working"] == 1, row
        others = [o for o in call(ADMIN, "GET", "/api/orgs").json
                  if o["slug"] != K]
        assert all(o.get("working") == 0 for o in others), \
            "the count is per-org, not global"
    finally:
        st["busy"] = False


@t("☞ a kiosk VISITOR is not told how busy the org is")
def _():
    st = supervisor.state(K, NID)
    st["busy"] = True
    try:
        rows = call(PUBLIC, "GET", f"/k/{TOKEN}/api/orgs").json
        assert len(rows) == 1 and rows[0]["slug"] == K, rows
        assert "working" not in rows[0], (
            "the public branch returns a trimmed row on purpose — a visitor "
            f"must not read the org's live load: {rows[0]}")
    finally:
        st["busy"] = False


@t("the polled list endpoint ALLOCATES NO STATE (the reason for the helper)")
def _():
    # supervisor.state() setdefault-allocates an entry per lookup. Counting
    # through it would mint one dict per node per poll on the hottest endpoint
    # in the app, which is why working_count reads _state directly. This is the
    # regression guard for that: the fix is invisible until someone "simplifies"
    # the helper back into state().
    before = set(supervisor._state)
    for _i in range(3):
        call(ADMIN, "GET", "/api/orgs")
    assert set(supervisor._state) == before, (
        "GET /api/orgs created supervisor state entries: "
        f"{sorted(set(supervisor._state) - before)[:8]}")


@t("the count agrees with the tree the user clicks into")
def _():
    st = supervisor.state(K, NID)
    st["busy"] = True
    try:
        row = next(o for o in call(ADMIN, "GET", "/api/orgs").json
                   if o["slug"] == K)
        tree = call(ADMIN, "GET", f"/api/orgs/{K}").json

        def busy_nodes(n):
            return (1 if n.get("busy") else 0) + sum(busy_nodes(c)
                                                     for c in n["children"])
        assert row["working"] == sum(busy_nodes(r) for r in tree["roots"]), (
            "the list figure and the org's own canvas must not disagree — "
            "they are the same fact on two screens")
    finally:
        st["busy"] = False


@t("deleting an ORG forgets runtime state; scratch survives for the restore")
def _():
    # promoted from gap() 2026-08-05: orgs_delete now calls forget_state() —
    # the state-only split of forget(). Both halves pinned: the runtime state
    # dies with the org (no phantom busy agent on restore), and the scratch
    # dirs SURVIVE, because delete is a reversible rename and putting the file
    # back is documented as the restore — the agents' files must come back too.
    r = call(ADMIN, "POST", "/api/orgs", {"name": "Doomed Org"})
    doomed = r.json["slug"]
    call(ADMIN, "POST", f"/api/orgs/{doomed}/ops",
         {"op": "hire", "tier": "haiku", "name": "worker", "grant": 1})
    scratch = supervisor.scratch_dir(doomed, "worker")     # creates it
    st = supervisor.state(doomed, "worker")
    st["busy"] = True
    st["queue"].append("queued before the delete")
    assert supervisor.working_count(doomed) == 1, "precondition"
    assert call(ADMIN, "DELETE", f"/api/orgs/{doomed}").status == 200
    held = [k for k in supervisor._state if k[0] == doomed]
    assert not held, (
        f"the deleted org still holds runtime state {held} "
        f"(working_count={supervisor.working_count(doomed)})")
    assert os.path.isdir(scratch), (
        "the scratch dir must SURVIVE an org delete — the delete is a "
        "reversible rename, and the restore must bring the files back")


# ------------------------------------------------- §11 raw HTTP on port 7402
print("\n§11 raw HTTP against a real uvicorn on :7402")


@t("uvicorn decodes the target below the gateway (no split-path bypass)")
def _():
    import socket

    import uvicorn
    cfg = uvicorn.Config(PUBLIC, host="127.0.0.1", port=7402, lifespan="off",
                         log_level="critical", access_log=False)
    server = uvicorn.Server(cfg)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _i in range(300):
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    assert getattr(server, "started", False), "uvicorn did not start on 7402"

    def raw(target, method="GET"):
        s = socket.create_connection(("127.0.0.1", 7402), timeout=15)
        s.sendall(f"{method} {target} HTTP/1.1\r\nHost: 127.0.0.1:7402\r\n"
                  f"Content-Length: 0\r\nConnection: close\r\n\r\n"
                  .encode("latin1"))
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        head, _sep, body = buf.partition(b"\r\n\r\n")
        return int(head.split()[1]), body

    try:
        for target in [f"/k/{TOKEN}/api%2Ffs", f"/k/{TOKEN}/api/%66s",
                       f"/k/{TOKEN}/api/fs%00", f"/k/{TOKEN}/%61pi/fs",
                       f"/k/{TOKEN}/api/orgs/{K}/..%2F..%2Ffs",
                       f"/k/{TOKEN}/api%2fagent", f"/k/{TOKEN}/api/agent",
                       f"/k/{TOKEN}/api/orgs/{K2}",
                       f"/k/{TOKEN}/api/orgs/{K}/settings",
                       f"/k/{TOKEN}/api/orgs/{K}/%73ettings",
                       f"/k/{TOKEN}/api/orgs/{K}/nodes/..%5C..%5C..%5C../scratch",
                       f"/k/{TOKEN}/api/orgs/{K}/nodes/%2e%2e%5c%2e%2e/scratch",
                       f"/k/{TOKEN}/api/orgs/{K}/nodes/{NID}/steer"]:
            code, body = raw(target)
            assert code != 500, f"raw {target} → 500: {body[:200]!r}"
            assert b'"home"' not in body, \
                f"raw {target} reached /api/fs: {body[:200]!r}"
            assert (b'"slug":"' + K2.encode()) not in body, \
                f"raw {target} reached the other org"
            assert b'"entries"' not in body, \
                f"raw {target} listed a directory: {body[:200]!r}"
        code, _b = raw(f"/api/orgs?k={TOKEN}")
        assert code == 404, code
        code, body = raw(f"http://127.0.0.1:7402/k/{TOKEN}/api/fs")
        assert code in (400, 403, 404), (code, body[:160])
        code, body = raw(f"/k/{TOKEN}/api/orgs/{K}")
        assert code == 200 and K.encode() in body, (code, body[:160])
        assert TOKEN.encode() not in body, "the token echoed in its own payload"
    finally:
        server.should_exit = True
        th.join(timeout=20)


if GAPS:
    print("\nknown gaps (asserted as failing on purpose — promote when fixed):")
    for _label, _why, _saw in GAPS:
        print(f"  ⚑ {_label}\n      why: {_why}\n      saw: {_saw}")

print(f"\nALL {PASS} CHECKS PASS"
      + (f" · {len(GAPS)} known gaps" if GAPS else ""))
