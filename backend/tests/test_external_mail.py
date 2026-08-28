"""External-mail suite — the org's contact with everything outside it.

    python backend/tests/test_external_mail.py            (no pytest; plain asserts)
    python backend/tests/test_external_mail.py --quick    (skip §9 and the long polls)
    python backend/tests/test_external_mail.py --hermetic (no sockets, no subprocess)

WHAT THIS COVERS AND WHY IT LOOKS LIKE THIS

Namespaces reach an org from outside — `@mcp:` (a polling external chat on this
machine), `@org:` (another org in this instance) and `@mcp:` (an outside Claude
Code session polling us through `externtool.py`) — and every one of them
arrives through ONE funnel:

    deliver_org_inbox(slug, peer, body, attachments)   supervisor.py
      → copy attachments into every recipient's uploads/
      → Org.post_external_mail(peer, body, by_node)    ledger.py   (authorizes)
      → supervisor.send_message(slug, nid, nudge)      per recipient (delivers)

Outbound is one dispatch: the ledger's `post_mail` accepts an `@ext:/@org:/@mcp:`
address (and decides WHO may speak for the org), then `agent_call` routes what
the ledger accepted to `interorg_send` / the spool / nothing. That split —
**the ledger authorizes, the supervisor and api deliver** — is the thing §10
exists to hold down: no transport call may happen that the ledger did not
first accept.

Before this file, `deliver_org_inbox`, `interorg_send`, `extern_send` and
`extern_wait` had zero references in any test, and `post_external_mail` was
exercised only at the ledger level, never through delivery.

⚠ HYGIENE. Nothing here touches port 7360 or looks at the real orgs;
§9's uvicorn binds 7406 only. (The chatq redirect that used to live here
went with the bridge — user ruling 2026-08-05.) That claim was FALSE for the
mail hub until 2026-08-10: a throwaway ORGTREE_DATA does not isolate the hub,
and §9's backend registered every fixture org against the operator's real one.
See the DEAD_HUB block below, and §1's guard over every rig in this directory.

    §1  fixtures + the shape of the funnel
    §2  the inbound funnel — deliver_org_inbox end to end
    §3  the org-inbox model — fan-out, the recipient set, the user-inbox rescue
    §4  kiosk sealing — both directions, every enforcement point
    §5  @org: — inter-org mail
    §6  @ext: — retired (refusals + historical readability)
    §7  attachments
    §8  the extern HTTP surface — send / messages / wait, and the cursor
    §9  externtool.py driven as a real MCP server against a real uvicorn
    §10 authorization — the ledger authorizes, the transports deliver
    §11 failure paths

TWO KINDS OF FLAG.

  ⚑    an OPEN finding: the check asserts CURRENT behaviour and says in its body
       what should happen the day it is fixed, so a fix fails loudly instead of
       passing silently. Four of these live in files this suite may not edit
       (supervisor.py ×2, api.py ×2); each also calls note(), which prints at
       the end of every run — it is not possible to run this and not see them.
  ⚑→✓  a defect that WAS fixed — all of them in externtool.py, the one
       production file in this suite's territory — with the reproduction kept
       as the guard. Each was reproduced red before the fix.

The fixes, and where each is guarded:

  ① the kiosk-enumeration filter (§4) — /api/orgs falls back to the bare list
    row whenever it cannot ALSO load the org, and the bare row has no
    `kiosk_cfg`; the filter now honours the authoritative `kiosk` flag too
  ② the stored peer id is revalidated, not trusted (§9) — a BOM or junk in
    ~/.orgtree/extern-id used to 422 every verb forever
  ③ an unusable EXPLICIT ORGTREE_EXTERN_ID is reported instead of confusing
    the caller with a 404/422 from the wire (§9)
  ④ `timeout_s` coercion (§9) — a non-numeric value raised inside run_tool and
    the catch-all answered "orgtree unreachable", a wrong diagnosis
  ⑤ `attachments` coercion (§9) — a bare string reached pydantic as a 422

DISCRIMINATION. Reverting any one of ①–⑤ alone, in a temp copy of the package,
turns its own check red and leaves the rest green (5/5 measured). That is what
makes the green run mean something. ①'s checks drive `externtool.run_tool`
ITSELF with a stubbed `http()` rather than a copy of its logic, and ④'s live
check is driven with a reply already waiting so the repaired path answers on
its first poll slice instead of sitting out the 120 s it now falls back to —
without those two shapes, both reverts pass.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

QUICK = "--quick" in sys.argv
HERMETIC = "--hermetic" in sys.argv

# an isolated data root BEFORE any orgtree import — store resolves ORGTREE_DATA
# at import time, and org docs are re-slugified from name, so a shared data dir
# makes fixtures collide with the operator's real orgs
DATA = tempfile.mkdtemp(prefix="orgtree-extmail-")
os.environ["ORGTREE_DATA"] = DATA
# ⚠ THE MAIL HUB IS NOT ISOLATED BY ORGTREE_DATA. The rig fix for the user's
# 2026-08-06 report ("hundreds of disconnected orgs … crowding the connected
# client list") went into three suites and MISSED THIS ONE — measured
# 2026-08-10, when a peer flagged ~45 fixture names in the operator's live
# roster (arch, capnode, lonedead, norescue, order2 …) in two batches whose
# timestamps matched this suite's two runs that morning exactly.
# `net._default_address` reads `net_hub_address` out of defaults.json; a fresh
# data root has none, so the fallback is net.DEFAULT_HUB_ADDRESS — the REAL
# hub on 127.0.0.1:7370 — and §9's live backend registers every org it finds
# there. The roster is the compose picker's source, so each row is a
# selectable recipient that can never receive anything.
# `net_autoconnect` cannot be turned off from here (orgs_create reads it from
# the request body, default True); `net_hub_address` CAN, so the local entry
# is pointed at a dead port and registration fails harmlessly into the backoff.
DEAD_HUB = "http://127.0.0.1:9"     # discard port: refuses instantly
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    json.dump({"net_hub_address": DEAD_HUB}, _f)
os.environ["ORGTREE_PORT"] = "7406"
os.environ["ORGTREE_PUBLIC_PORT"] = "7406"
os.environ.pop("ORGTREE_EXPOSE_ADMIN", None)
# ⚠ externtool.py computes PEER at IMPORT, and peer_id() MINTS AND WRITES
# ~/.orgtree/extern-id when it is missing. Two checks import the module in
# process; pinning the id first keeps the suite out of the operator's home.
os.environ["ORGTREE_EXTERN_ID"] = "suite.inproc"

from orgtree import api, sandbox, store, supervisor           # noqa: E402
from orgtree.ledger import EXTERN, LedgerError, Org, SYSTEM, USER   # noqa: E402

sandbox.warm = lambda org: None
supervisor.storage_check = lambda slug: None

PASS = 0
NOTES: list[str] = []


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


def note(msg):
    """An open finding this suite pins but does not fix — printed at the end so
    it is impossible to run the suite and not see it."""
    NOTES.append(msg)


def expect_error(fn, needle=""):
    try:
        fn()
    except LedgerError as e:
        assert needle.lower() in str(e).lower(), f"wrong error: {e}"
        return str(e)
    raise AssertionError("expected LedgerError, got success")


# ------------------------------------------------------------------ transports
# Spies, not stubs where it matters: every check that cares about delivery
# asserts on what the transport was ASKED to do, because the split between
# "the ledger accepted it" and "a transport ran" is the invariant of §10.
DRIVEN: list[tuple] = []
INTERORG: list[tuple] = []


def _spy_send_message(slug, nid, text, command=False, wake=True):
    DRIVEN.append((slug, nid, text))
    if not wake:
        return {"accepted": True, "queued": 0, "parked": True}
    return {"accepted": True, "queued": 0}


_real_send_message = supervisor.send_message
_real_interorg_send = supervisor.interorg_send
supervisor.send_message = _spy_send_message


def _spy_interorg_send(src, dst, body):
    INTERORG.append((src, dst, body))
    return _real_interorg_send(src, dst, body)


def reset_spies():
    DRIVEN.clear()
    INTERORG.clear()


# --------------------------------------------------------------- ASGI transport
def call(method, path, body=None, query=b"", headers=None):
    """Invoke the ADMIN app with a hand-built scope — same technique (and same
    reason) as test_api_surface.py: no client normalises the target."""
    payload = json.dumps(body).encode() if body is not None else b""
    hdrs = [(b"host", b"127.0.0.1:7406"), (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode())]
    hdrs += headers or []
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": query, "root_path": "",
             "headers": hdrs, "client": ("127.0.0.1", 5555),
             "server": ("127.0.0.1", 7406)}
    return _drive(scope, payload)


def _drive(scope, payload):
    out = {"status": 0, "body": b""}
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body") or b""

    async def go():
        await api.app(scope, receive, send)

    _run(go())
    try:
        j = json.loads(out["body"])
    except Exception:                                          # noqa: BLE001
        j = None
    return out["status"], j


_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _LOOP.run_until_complete(coro)


# ------------------------------------------------------------------- fixtures
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="test hire — do test things")
    s.update(over)
    return s


def mkorg(name, tops=("ceo",), kiosk=False):
    """A saved org with `tops` live top-level agents."""
    o = Org.create(name)
    if kiosk:
        o.d["kiosk"] = {"enabled": True, "token": "tok-" + name, "credits": 100,
                        "spend_limit": 5.0, "storage_limit_mb": 256,
                        "sandbox": False, "auto_raise": False}
    for i, n in enumerate(tops):
        o.hire(USER, None, "haiku" if i else "opus", 20, n)
    store.save_org(o)
    return store.load_org(o.d["slug"])


def load(slug):
    return store.load_org(slug)


def inbox(slug, direction=None, peer=None):
    es = load(slug).d.get("org_inbox", [])
    return [e for e in es
            if (direction is None or e["dir"] == direction)
            and (peer is None or e["peer"] == peer)]


def mailbox(slug, nid):
    return load(slug).d.get("mail", {}).get(nid, [])


def uploads(slug, nid):
    p = os.path.join(supervisor.scratch_dir(slug, nid), "uploads")
    return sorted(os.listdir(p)) if os.path.isdir(p) else []


SCRATCH: list[str] = [DATA]          # everything main() removes on exit


def mktemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix)
    SCRATCH.append(d)
    return d


def tmpfile(name, content="x", root=None):
    d = root or mktemp("extmail-att-")
    p = os.path.join(d, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p



# ============================================================================ §1
def s1_fixtures():
    print("\n§1 fixtures + the shape of the funnel")

    @t("a fresh org has NO extern recipients — holders only (C0)")
    def _():
        # C0 (user rulings 2026-08-05): inbound extern mail reaches ORG-INBOX
        # audience holders, never every top-level agent. A brand-new org holds
        # nothing, so the recipient list is empty until the first contact
        # bootstraps a holder (or the user grants one).
        o = mkorg("acme", ("ceo", "cfo"))
        assert o.extern_recipients() == [], o.extern_recipients()
        assert o.extern_holders() == [], o.extern_holders()

    @t("first contact bootstraps the LEFTMOST live top-level, and only it")
    def _():
        o = load("acme")
        got = o.post_external_mail("@ext:first", "knock knock")
        assert got == ["ceo"], got
        assert o.extern_holders() == ["ceo"], o.extern_holders()
        assert "cfo" not in o.d.get("mail", {}), \
            "the second top-level received a copy — that is the retired fan-out"
        store.save_org(o)

    @t("the funnel is one function: deliver_org_inbox handles all three prefixes")
    def _():
        src = supervisor.deliver_org_inbox.__doc__ or ""
        assert "external chats" in src and "orgs" in src

    @t("post_external_mail returns exactly extern_recipients()")
    def _():
        o = load("acme")
        assert o.post_external_mail("@mcp:p", "b") == o.extern_recipients()

    @t("a fresh org doc has no org_inbox key until outside mail arrives")
    def _():
        o = mkorg("virgin")
        assert "org_inbox" not in o.d

    @t("every rig in this directory points the mail hub at a dead port")
    def _():
        """The guard for the thing this suite got wrong TWICE OVER. The
        2026-08-06 fix for "hundreds of disconnected orgs" isolated three
        rigs; nothing then checked the other twenty-odd, and this file — the
        one that boots a live backend — was among the ones missed, so the
        same pollution recurred on 2026-08-10 with ~45 fixture orgs.

        An isolated ORGTREE_DATA is not isolation from the hub: the fallback
        address is the operator's real one. So the property is checked over
        the whole directory rather than trusted per file — any rig that mints
        a throwaway data root must also write `net_hub_address`."""
        here = os.path.dirname(os.path.abspath(__file__))
        missing = []
        for fn in sorted(os.listdir(here)):
            if not fn.startswith("test_") or not fn.endswith(".py"):
                continue
            src = open(os.path.join(here, fn), encoding="utf-8").read()
            if 'ORGTREE_DATA"] =' not in src and "ORGTREE_DATA'] =" not in src:
                continue                      # no data root of its own
            if "net_hub_address" not in src:
                missing.append(fn)
        assert not missing, (
            "these rigs mint their own ORGTREE_DATA but never redirect "
            "net_hub_address, so every org they create is registered against "
            "the operator's REAL hub at net.DEFAULT_HUB_ADDRESS and stays in "
            f"the roster as an unreachable recipient: {missing}")


# ============================================================================ §2
def s2_funnel():
    print("\n§2 the inbound funnel — deliver_org_inbox end to end")
    mkorg("funnel", ("ceo", "cfo"))
    # C0 (2026-08-05): recipients are ORG-INBOX AUDIENCE HOLDERS, so the
    # two-recipient fan-out this section measures has to be SET UP rather than
    # assumed from top-level standing. Both hold it here on purpose: everything
    # below — per-copy ids, per-recipient mail_log, one drive each, the
    # comma-joined event — is about delivery to SEVERAL recipients, which is
    # still a real case, just no longer the default one.
    _f = load("funnel")
    for _who in ("ceo", "cfo"):
        _f.audience_grant(USER, _who, "extern")
    store.save_org(_f)
    reset_spies()

    @t("the fixture's two recipients are HOLDERS, not merely top-level")
    def _():
        o = load("funnel")
        assert sorted(o.extern_holders()) == ["ceo", "cfo"], o.extern_holders()
        assert o.extern_recipients() == o.extern_holders()

    @t("deliver_org_inbox returns the recipient list")
    def _():
        d = supervisor.deliver_org_inbox("funnel", "@mcp:p1", "hello org")
        assert d == ["ceo", "cfo"], d

    @t("the message is logged in the org inbox as an INBOUND entry")
    def _():
        es = inbox("funnel", "in", "@mcp:p1")
        assert len(es) == 1 and es[0]["body"] == "hello org", es

    @t("the inbound entry carries an id and an ISO-Z millisecond stamp")
    def _():
        e = inbox("funnel", "in")[0]
        assert re.fullmatch(r"[0-9a-f]{8}", e["id"]), e
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", e["at"]), e

    @t("an inbound entry carries NO `by` attribution (outside mail has no author here)")
    def _():
        assert "by" not in inbox("funnel", "in")[0]

    @t("every recipient's mailbox got its own copy")
    def _():
        for n in ("ceo", "cfo"):
            m = mailbox("funnel", n)
            assert len(m) == 1 and m[0]["body"] == "hello org", (n, m)

    @t("each copy has its own id — one retraction cannot pull them all")
    def _():
        assert mailbox("funnel", "ceo")[0]["id"] != mailbox("funnel", "cfo")[0]["id"]

    @t("the copies carry the coordinate-and-speak-for-the-org relationship text")
    def _():
        r = mailbox("funnel", "ceo")[0]["relationship"]
        assert "OUTSIDE PARTY" in r and "untrusted" in r.lower()
        assert "one reply" in r and "speaks for the org" in r

    @t("`from` is the peer address verbatim, not a node id")
    def _():
        assert mailbox("funnel", "ceo")[0]["from"] == "@mcp:p1"

    @t("each recipient's mail_log archived the copy too")
    def _():
        o = load("funnel")
        for n in ("ceo", "cfo"):
            assert len(o.d["mail_log"][n]) == 1, n

    @t("every recipient was DRIVEN exactly once")
    def _():
        assert sorted(n for s, n, _ in DRIVEN if s == "funnel") == ["ceo", "cfo"], DRIVEN

    @t("the drive nudge names all three outside address forms")
    def _():
        txt = [x[2] for x in DRIVEN if x[1] == "ceo"][0]
        for frag in ("@org:", "@mcp:", "ORG INBOX", "orgtree_message"):
            assert frag in txt, frag

    @t("the nudge says the mail is NOT user authority")
    def _():
        txt = [x[2] for x in DRIVEN if x[1] == "ceo"][0]
        assert "never user authority" in txt and "untrusted" in txt

    @t("an ext_mail event is logged naming the recipients")
    def _():
        evs = [e for e in load("funnel").d["events"] if e["op"] == "ext_mail"]
        assert evs and evs[-1]["detail"]["to"] == "ceo,cfo", evs[-1]

    @t("the event records a gist, not the body")
    def _():
        e = [e for e in load("funnel").d["events"] if e["op"] == "ext_mail"][-1]
        assert e["detail"]["gist"] == "hello org"

    @t("the mail POST precedes the drive (the doc is saved before anyone is woken)")
    def _():
        # ordering proof: the spy sees a doc that already holds the mail
        seen = {}

        def probe(slug, nid, text, command=False):
            seen[nid] = len(mailbox(slug, nid))
            return {"accepted": True}
        old = supervisor.send_message
        supervisor.send_message = probe
        try:
            supervisor.deliver_org_inbox("funnel", "@mcp:order", "ordered")
        finally:
            supervisor.send_message = old
        assert seen == {"ceo": 2, "cfo": 2}, seen

    @t("a body over 20 000 chars is truncated in the org inbox…")
    def _():
        big = "Z" * 30000
        supervisor.deliver_org_inbox("funnel", "@mcp:big", big)
        assert len(inbox("funnel", "in", "@mcp:big")[0]["body"]) == 20000

    @t("…but reaches the agents' mailboxes in FULL (the inbox is a log, not the mail)")
    def _():
        m = [x for x in mailbox("funnel", "ceo") if x["from"] == "@mcp:big"][0]
        assert len(m["body"]) == 30000

    @t("a unicode body round-trips through doc save/load unharmed")
    def _():
        body = "héllo ⚑ 世界 — ∴ ok"
        supervisor.deliver_org_inbox("funnel", "@mcp:uni", body)
        assert inbox("funnel", "in", "@mcp:uni")[0]["body"] == body
        assert [x for x in mailbox("funnel", "ceo")
                if x["from"] == "@mcp:uni"][0]["body"] == body

    @t("a whitespace-only body does not crash the gist (the D-57 ⑦ family)")
    def _():
        supervisor.deliver_org_inbox("funnel", "@mcp:ws", "   \n\t  ")
        e = [e for e in load("funnel").d["events"] if e["op"] == "ext_mail"][-1]
        assert e["detail"]["gist"] == ""

    @t("an empty body does not crash either")
    def _():
        d = supervisor.deliver_org_inbox("funnel", "@mcp:empty", "")
        assert d == ["ceo", "cfo"] and inbox("funnel", "in", "@mcp:empty")[0]["body"] == ""

    @t("a lone newline body does not crash")
    def _():
        assert supervisor.deliver_org_inbox("funnel", "@mcp:nl", "\n") == ["ceo", "cfo"]

    @t("the org inbox is capped at 200 entries, newest kept")
    def _():
        o = load("funnel")
        for i in range(260):
            o._org_inbox_log("in", "@mcp:flood", f"m{i}")
        store.save_org(o)
        es = load("funnel").d["org_inbox"]
        assert len(es) == 200 and es[-1]["body"] == "m259", (len(es), es[-1])

    @t("per-node mail_log is capped at 100")
    def _():
        o = mkorg("capnode")
        for i in range(140):
            o.post_external_mail("@mcp:x", f"m{i}")
        store.save_org(o)
        assert len(load("capnode").d["mail_log"]["ceo"]) == 100

    @t("the unread count is entries-since-mark-read, and mark-read zeroes it")
    def _():
        o = mkorg("unread")
        for i in range(3):
            o.post_external_mail("@mcp:x", f"m{i}")
        assert len(o.d["org_inbox"]) - int(o.d.get("org_inbox_read", 0)) == 3
        o.org_inbox_mark_read()
        assert len(o.d["org_inbox"]) - int(o.d.get("org_inbox_read", 0)) == 0

    @t("delivering to an org that does not exist raises, it does not silently drop")
    def _():
        try:
            supervisor.deliver_org_inbox("nosuchorg", "@mcp:p", "b")
            raise AssertionError("expected a failure")
        except LedgerError:
            pass


# ============================================================================ §3
def s3_orginbox_model():
    print("\n§3 the org-inbox model — holders, the bootstrap, the rescue")

    # C0 (user rulings 2026-08-05) RETIRED THE FAN-OUT TO TOP-LEVELS.
    # Recipients are ORG-INBOX AUDIENCE HOLDERS, at any depth; top-level
    # standing by itself delivers nothing. What survives is that delivery is
    # still one-to-MANY across holders, and every filter (state, grantor,
    # dangling rows) still applies — so those properties are re-expressed here
    # against holders rather than deleted.

    @t("delivery reaches EVERY holder, not just the first")
    def _():
        o = mkorg("fan", ("a", "b", "c", "d"))
        for who in ("a", "b", "c", "d"):
            o.audience_grant(USER, who, "extern")
        assert o.post_external_mail("@mcp:p", "x") == ["a", "b", "c", "d"]

    @t("a live top-level that holds NOTHING receives nothing")
    def _():
        o = mkorg("nonholder", ("a", "b"))
        o.audience_grant(USER, "a", "extern")
        assert o.post_external_mail("@mcp:p", "x") == ["a"]
        assert "b" not in o.d.get("mail", {}), (
            "a non-holder top-level received org mail — the retired fan-out")

    @t("a deep org-inbox audience holder is a recipient")
    def _():
        o = mkorg("holder", ("ceo",))
        o.hire("ceo", "ceo", "haiku", 5, "mid", **spec())
        o.hire("mid", "mid", "haiku", 0, "deep", **spec())
        o.d["audiences"].append({"grantee": "deep", "grantor": EXTERN,
                                 "granted_at": "x", "reason": "r"})
        assert o.extern_recipients() == ["deep"], (
            "depth is irrelevant to the org inbox — holding it is what counts")

    @t("recipients follow the AUDIENCE list order, deep or not")
    def _():
        o = mkorg("order2", ("t1", "t2"))
        o.hire("t1", "t1", "haiku", 0, "h", **spec())
        for who in ("h", "t2", "t1"):
            o.d["audiences"].append({"grantee": who, "grantor": EXTERN,
                                     "granted_at": "x", "reason": "r"})
        assert o.extern_recipients() == ["h", "t2", "t1"]

    @t("a duplicate audience ROW would deliver twice — the guards' whole job")
    def _():
        # extern_holders() reads the audience rows, so a duplicate row is a
        # duplicate recipient. Every grant path guards against that
        # (audience_grant is idempotent; both C0 auto-grants check
        # _has_audience first), and this pins the invariant those guards exist
        # to protect, by hand-writing the row they prevent.
        o = mkorg("dedup", ("ceo",))
        for _ in range(2):
            o.d["audiences"].append({"grantee": "ceo", "grantor": EXTERN,
                                     "granted_at": "x", "reason": "r"})
        assert o.extern_recipients() == ["ceo", "ceo"]
        o.post_external_mail("@mcp:p", "x")
        assert len(o.d["mail"]["ceo"]) == 2

    @t("audience_grant twice is idempotent — no second row, no double delivery")
    def _():
        o = mkorg("dedup2", ("ceo",))
        o.audience_grant(USER, "ceo", "extern")
        o.audience_grant(USER, "ceo", "extern")
        assert o.extern_recipients() == ["ceo"], o.extern_recipients()
        o.post_external_mail("@mcp:p", "x")
        assert len(o.d["mail"]["ceo"]) == 1

    @t("an audience from anyone OTHER than @extern is not an org-inbox audience")
    def _():
        o = mkorg("wrongaud", ("ceo",))
        o.hire("ceo", "ceo", "haiku", 0, "mid", **spec())
        o.audience_grant(USER, "ceo", "extern")
        o.d["audiences"].append({"grantee": "mid", "grantor": USER,
                                 "granted_at": "x", "reason": "r"})
        assert o.extern_recipients() == ["ceo"]

    @t("a dangling audience (grantee no longer a node) is skipped, not a KeyError")
    def _():
        o = mkorg("dangle", ("ceo",))
        o.audience_grant(USER, "ceo", "extern")
        o.d["audiences"].append({"grantee": "ghost", "grantor": EXTERN,
                                 "granted_at": "x", "reason": "r"})
        assert o.extern_recipients() == ["ceo"]

    @t("an ARCHIVED holder is not a recipient")
    def _():
        o = mkorg("arch", ("ceo", "cfo"))
        for who in ("ceo", "cfo"):
            o.audience_grant(USER, who, "extern")
        o.retire(USER, "cfo")
        assert o.extern_recipients() == ["ceo"]

    @t("an UNRECOVERABLE holder is not a recipient (live-for-budget ≠ live-for-delivery)")
    def _():
        o = mkorg("unrec", ("ceo", "cfo"))
        for who in ("ceo", "cfo"):
            o.audience_grant(USER, who, "extern")
        o.nodes["cfo"]["state"] = "unrecoverable"
        assert "cfo" in o.children(None), "still holds its seat"
        assert o.extern_recipients() == ["ceo"]

    @t("an UNRECOVERABLE deep holder is skipped like any other dead node")
    def _():
        o = mkorg("unrec2", ("ceo",))
        o.hire("ceo", "ceo", "haiku", 0, "h", **spec())
        o.audience_grant(USER, "ceo", "extern")
        o.d["audiences"].append({"grantee": "h", "grantor": EXTERN,
                                 "granted_at": "x", "reason": "r"})
        o.nodes["h"]["state"] = "unrecoverable"
        assert o.extern_recipients() == ["ceo"]

    @t("a FROZEN holder is still a recipient — mail waits in its mailbox")
    def _():
        o = mkorg("frozen", ("ceo",))
        o.audience_grant(USER, "ceo", "extern")
        o.nodes["ceo"]["frozen"] = {"kind": "limit", "error": "x"}
        assert o.extern_recipients() == ["ceo"]
        o.post_external_mail("@mcp:p", "held")
        assert len(o.d["mail"]["ceo"]) == 1

    @t("no live recipients ⇒ the mail is rescued into the USER inbox")
    def _():
        o = mkorg("rescue", ("ceo",))
        o.retire(USER, "ceo")
        assert o.post_external_mail("@mcp:p", "anyone home?") == []
        store.save_org(o)
        ui = o.user_mailbox()
        assert len(ui) == 1 and ui[0]["from"] == SYSTEM and ui[0]["kind"] == "notice"

    @t("the rescue notice names the peer and quotes the body")
    def _():
        b = load("rescue").user_mailbox()[0]["body"]
        assert "@mcp:p" in b and "anyone home?" in b and "no top-level agents" in b

    @t("the rescue quotes at most 2 000 chars of the body")
    def _():
        o = mkorg("rescue2", ("ceo",))
        o.retire(USER, "ceo")
        o.post_external_mail("@mcp:p", "Q" * 5000)
        assert o.user_mailbox()[0]["body"].count("Q") == 2000

    @t("⚑→✓ a LONE UNRECOVERABLE top-level still triggers the rescue (the 2026-08-01 defect)")
    def _():
        # before extern_recipients() filtered on state, children() returned the
        # unrecoverable node, so `tops` was truthy: mail was queued into a node
        # that can never drain it AND the user-inbox rescue was suppressed.
        o = mkorg("lonedead", ("ceo",))
        o.nodes["ceo"]["state"] = "unrecoverable"
        assert o.post_external_mail("@mcp:p", "help") == []
        assert len(o.user_mailbox()) == 1
        assert not o.d.get("mail", {}).get("ceo"), "must not queue into a dead node"

    @t("the rescue does NOT fire when a live top-level can be bootstrapped")
    def _():
        o = mkorg("norescue", ("ceo", "cfo"))
        o.retire(USER, "cfo")
        assert o.extern_holders() == [], "precondition: nobody holds it yet"
        assert o.post_external_mail("@mcp:p", "x") == ["ceo"]
        assert not o.user_mailbox(), (
            "the bootstrap must run BEFORE the user-inbox rescue")

    @t("the rescue path still logs the inbound entry and the event")
    def _():
        o = load("rescue")
        assert inbox("rescue", "in")
        evs = [e for e in o.d["events"] if e["op"] == "ext_mail"]
        assert evs[-1]["detail"]["to"] == "(user inbox)"

    @t("through the funnel, a rescued message drives nobody")
    def _():
        reset_spies()
        o = mkorg("rescue3", ("ceo",))
        o.retire(USER, "ceo")
        store.save_org(o)
        d = supervisor.deliver_org_inbox("rescue3", "@mcp:p", "hi")
        assert d == [] and [x for x in DRIVEN if x[0] == "rescue3"] == []

    @t("and the API reports the rescue in place of a recipient list")
    def _():
        st, j = call("POST", "/api/extern/rescuepeer/send",
                     {"org": "rescue3", "body": "hello"})
        assert st == 200 and j["delivered"] == ["(user inbox — no live agents)"], j

    @t("an org with zero nodes at all rescues rather than raising")
    def _():
        o = Org.create("emptyorg")
        store.save_org(o)
        assert o.post_external_mail("@mcp:p", "x") == []
        assert len(o.user_mailbox()) == 1

    @t("delivery is per-message: two messages give every holder two copies")
    def _():
        o = mkorg("twice", ("a", "b"))
        for who in ("a", "b"):
            o.audience_grant(USER, who, "extern")
        o.post_external_mail("@mcp:p", "one")
        o.post_external_mail("@mcp:p", "two")
        assert [len(o.d["mail"][n]) for n in ("a", "b")] == [2, 2]


# ============================================================================ §4
def s4_kiosk():
    print("\n§4 kiosk sealing — both directions, every enforcement point")
    mkorg("sealed", ("top",), kiosk=True)
    mkorg("open", ("ceo",))

    # ---- point 1: the ledger, inbound
    @t("kiosk: post_external_mail delivers to NOBODY")
    def _():
        assert load("sealed").post_external_mail("@mcp:p", "x") == []

    @t("kiosk: inbound leaves NO org-inbox entry (nothing to read back)")
    def _():
        o = load("sealed")
        o.post_external_mail("@ext:c", "x")
        assert "org_inbox" not in o.d

    @t("kiosk: inbound queues nothing into any mailbox")
    def _():
        o = load("sealed")
        o.post_external_mail("@org:open", "x")
        assert not o.d.get("mail")

    @t("kiosk: inbound does NOT reach the user inbox either (a sealed org is silent)")
    def _():
        o = load("sealed")
        before = len(o.user_mailbox())   # a kiosk is born with a notice
        o.post_external_mail("@mcp:p", "x")
        assert len(o.user_mailbox()) == before

    @t("kiosk: inbound logs no event")
    def _():
        o = load("sealed")
        before = len(o.d["events"])
        o.post_external_mail("@mcp:p", "x")
        assert len(o.d["events"]) == before

    @t("kiosk: a live top-level exists — the seal is the reason, not an empty roster")
    def _():
        # C0: top-level standing alone no longer makes a recipient, so the
        # premise is now stated as "a live top-level EXISTS, so the C0
        # bootstrap had someone to pick" — and the mail still went nowhere.
        # The audience cannot even be granted here, which is the seal itself.
        o = load("sealed")
        assert o.nodes["top"]["state"] == "live"
        assert [c for c in o.children(None)] == ["top"]
        expect_error(lambda: o.audience_grant(USER, "top", "extern"),
                     "sealed kiosk")
        assert o.extern_recipients() == [] and o.extern_holders() == []

    # ---- point 2: the ledger, outbound
    @t("kiosk: @ext: refuses as RETIRED before the seal is consulted")
    def _():
        expect_error(lambda: load("sealed").post_mail("top", "@ext:c", "x"),
                     "retired")

    @t("kiosk: an agent may not address @org:")
    def _():
        expect_error(lambda: load("sealed").post_mail("top", "@org:open", "x"),
                     "no contact with the outside world")

    @t("kiosk: an agent may not address @mcp:")
    def _():
        expect_error(lambda: load("sealed").post_mail("top", "@mcp:p", "x"),
                     "sealed kiosk")

    @t("kiosk: the outbound refusal precedes the top-level authority check")
    def _():
        # a SUBORDINATE in a kiosk gets the seal message, not the §7.5 one —
        # so the refusal never hints that a top-level could have sent it
        o = load("sealed")
        o.hire("top", "top", "haiku", 0, "sub", **spec())
        e = expect_error(lambda: o.post_mail("sub", "@org:open", "x"))
        assert "sealed kiosk" in e and "TOP-LEVEL" not in e, e

    @t("kiosk: a refused outbound leaves no org-inbox trace")
    def _():
        o = load("sealed")
        try:
            o.post_mail("top", "@org:open", "x")
        except LedgerError:
            pass
        assert "org_inbox" not in o.d

    # ---- point 3: the API — indistinguishable from "no such org"
    @t("kiosk: POST /extern/…/send is 404, exactly like an unknown org")
    def _():
        s1, j1 = call("POST", "/api/extern/pk/send", {"org": "sealed", "body": "x"})
        s2, j2 = call("POST", "/api/extern/pk/send", {"org": "nosuchorg", "body": "x"})
        assert s1 == s2 == 404, (s1, s2)
        assert j1["detail"].replace("sealed", "X") == j2["detail"].replace("nosuchorg", "X")

    @t("kiosk: the refusal text contains only the slug the caller already typed")
    def _():
        _, j = call("POST", "/api/extern/pk/send", {"org": "sealed", "body": "x"})
        assert j["detail"] == "no organization named 'sealed'"
        for leak in ("kiosk", "sealed kiosk", "token", "forbidden", "403"):
            assert leak not in j["detail"].lower().replace("'sealed'", ""), leak

    @t("kiosk: a case-variant slug is refused identically (no probing by case)")
    def _():
        _, j = call("POST", "/api/extern/pk/send", {"org": "SEALED", "body": "x"})
        assert j["detail"] == "no organization named 'SEALED'"

    @t("kiosk: nothing was delivered by the refused API call")
    def _():
        assert not load("sealed").d.get("mail")

    # ---- point 4: the scan — a kiosk's inbox is never read back
    @t("kiosk: _extern_scan skips it even when the doc HOLDS out-entries")
    def _():
        # an org that already corresponded and was later made a kiosk by hand
        o = mkorg("wasopen", ("ceo",))
        o.post_mail("ceo", "@mcp:pw", "old reply")
        store.save_org(o)
        assert len(api._extern_scan("@mcp:pw", None, None)) == 1
        o = load("wasopen")
        o.d["kiosk"] = {"enabled": True, "token": "t"}
        store.save_org(o)
        assert api._extern_scan("@mcp:pw", None, None) == []

    @t("kiosk: GET /extern/…/messages returns nothing from it")
    def _():
        st, j = call("GET", "/api/extern/pw/messages")
        assert st == 200 and j["messages"] == [] and "cursor" not in j

    @t("kiosk: an explicit org filter naming it also returns empty (no oracle)")
    def _():
        st, j = call("GET", "/api/extern/pw/messages", query=b"org=wasopen")
        st2, j2 = call("GET", "/api/extern/pw/messages", query=b"org=nosuchorg")
        assert (st, j) == (st2, j2) == (200, {"messages": []})

    # ---- point 5: the chatq registry is GONE (user ruling 2026-08-05) —
    # a kiosk can no longer leak onto any machine-wide roster via it
    @t("no chatq registration surface exists to enumerate a kiosk from")
    def _():
        for fn in ("chatq_available", "chatq_register_org",
                   "chatq_deregister_org", "chatq_send",
                   "start_chatq_bridge", "_deliver_ext", "CHATQ_ROOT"):
            assert not hasattr(supervisor, fn), fn

    # ---- point 6: inter-org
    @t("kiosk as a DESTINATION: interorg_send refuses with the unknown-org wording")
    def _():
        assert supervisor.interorg_send("open", "sealed", "x") \
            == "no organization named 'sealed'"

    @t("kiosk destination: byte-identical to an unknown slug modulo the slug itself")
    def _():
        a = supervisor.interorg_send("open", "sealed", "x")
        b = supervisor.interorg_send("open", "ghostorg", "x")
        assert a.replace("sealed", "?") == b.replace("ghostorg", "?")

    @t("kiosk destination: nothing landed in it")
    def _():
        assert "org_inbox" not in load("sealed").d

    @t("kiosk → kiosk is refused at the SENDING ledger before the destination matters")
    def _():
        mkorg("sealed2", ("top",), kiosk=True)
        expect_error(lambda: load("sealed").post_mail("top", "@org:sealed2", "x"),
                     "sealed kiosk")

    # ---- point 7: the roster
    @t("store.list_orgs marks a kiosk authoritatively")
    def _():
        rows = {o["slug"]: o for o in store.list_orgs()}
        assert rows["sealed"]["kiosk"] is True and rows["open"]["kiosk"] is False

    @t("the admin listing attaches kiosk_cfg for a loadable kiosk")
    def _():
        st, j = call("GET", "/api/orgs")
        row = [o for o in j if o["slug"] == "sealed"][0]
        assert "kiosk_cfg" in row

    @t("⚑→✓ kiosk enumeration: a kiosk whose doc will not load is listed WITHOUT kiosk_cfg")
    def _():
        # reproduction of the defect fixed in externtool.py. /api/orgs falls back
        # to the bare list row whenever store.load_org raises — a doc whose
        # internal slug disagrees with its file name (rename, restore, or a
        # concurrent delete_org rename) does exactly that. The row still carries
        # the authoritative `kiosk: True` from store.list_orgs.
        p = store.org_path("sealed")
        d = json.load(open(p, encoding="utf-8"))
        orig = d["slug"]
        d["slug"] = "sealed-renamed"
        json.dump(d, open(p, "w", encoding="utf-8"))
        try:
            _, rows = call("GET", "/api/orgs")
            row = [o for o in rows if o["slug"] == "sealed-renamed"][0]
            assert "kiosk_cfg" not in row and row["kiosk"] is True, row
            # the OLD filter (kiosk_cfg only) would have listed it…
            assert [o["slug"] for o in rows if not o.get("kiosk_cfg")].count(
                "sealed-renamed") == 1
            # …and the real externtool.py, driven over this same payload, does not
            assert "sealed-renamed" not in _extern_visible(rows)
        finally:
            d["slug"] = orig
            json.dump(d, open(p, "w", encoding="utf-8"))

    @t("the fixed filter still lists ordinary orgs")
    def _():
        _, rows = call("GET", "/api/orgs")
        vis = _extern_visible(rows)
        assert "open" in vis and "sealed" not in vis, vis

    @t("a kiosk row missing BOTH keys is the one shape that still leaks (pinned)")
    def _():
        # the residual: nothing in the payload says "kiosk". Asserted so the
        # boundary of the fix is explicit rather than assumed.
        assert _extern_visible([{"slug": "bare", "name": "bare"}]) == ["bare"]


def _extern_visible(rows):
    """externtool.orgtree_list_orgs' REAL filter, driven hermetically: its only
    dependency is http(), so stubbing that runs the shipped code path over a
    payload we choose. Not a mirror — reverting the fix in externtool.py turns
    the checks above RED."""
    from orgtree import externtool as ET
    real = ET.http
    ET.http = lambda method, path, body=None, timeout=60: rows
    try:
        out, err = ET.run_tool("orgtree_list_orgs", {})
    finally:
        ET.http = real
    assert not err, out
    return [o["slug"] for o in json.loads(out)["orgs"]]


# ============================================================================ §5
def s5_interorg():
    print("\n§5 @org: — inter-org mail")
    mkorg("alpha", ("ceo",))
    mkorg("beta", ("boss",))
    reset_spies()

    @t("alpha → beta lands in beta's inbox as @org:alpha")
    def _():
        assert supervisor.interorg_send("alpha", "beta", "hello neighbour") is None
        es = inbox("beta", "in")
        assert es[-1]["peer"] == "@org:alpha" and es[-1]["body"] == "hello neighbour"

    @t("beta's top-level was driven")
    def _():
        assert ("beta", "boss") in [(s, n) for s, n, _ in DRIVEN]

    @t("beta can reply to @org:alpha and it reaches alpha's inbox")
    def _():
        o = load("beta")
        r = o.post_mail("boss", "@org:alpha", "hello back")
        store.save_org(o)
        assert r["delivered"] == "@org:alpha"
        assert supervisor.interorg_send("beta", "alpha", "hello back") is None
        assert inbox("alpha", "in")[-1]["peer"] == "@org:beta"

    @t("the outbound is recorded in the SENDER's org inbox with `by` attribution")
    def _():
        e = [e for e in load("beta").d["org_inbox"] if e["dir"] == "out"][-1]
        assert e["peer"] == "@org:alpha" and e["by"] == "boss"

    @t("addressing your OWN org is refused")
    def _():
        expect_error(lambda: load("alpha").post_mail("ceo", "@org:alpha", "x"),
                     "this organization itself")

    @t("an unknown destination slug returns an error string, not an exception")
    def _():
        assert supervisor.interorg_send("alpha", "ghost", "x") \
            == "no organization named 'ghost'"

    @t("an unknown destination delivers nothing anywhere")
    def _():
        n = len(load("alpha").d.get("org_inbox", []))
        supervisor.interorg_send("alpha", "ghost", "x")
        assert len(load("alpha").d.get("org_inbox", [])) == n

    @t("a destination deleted mid-conversation degrades to the same error")
    def _():
        mkorg("doomed", ("ceo",))
        assert supervisor.interorg_send("alpha", "doomed", "first") is None
        store.delete_org("doomed")
        assert supervisor.interorg_send("alpha", "doomed", "second") \
            == "no organization named 'doomed'"

    @t("a destination slug with a path separator is refused, not resolved")
    def _():
        for bad in ("../alpha", r"..\alpha", "alpha/../beta"):
            r = supervisor.interorg_send("alpha", bad, "x")
            assert r and r.startswith("no organization named"), (bad, r)

    @t("the ledger accepts @org: for a slug that does not exist (delivery decides)")
    def _():
        # deliberate: the ledger authorizes WHO may speak, the bridge resolves
        # WHERE. A refusal here would mean the ledger owned the org roster.
        o = load("alpha")
        assert o.post_mail("ceo", "@org:ghost", "x")["delivered"] == "@org:ghost"

    @t("a → b → a does not recurse: each hop is one delivery")
    def _():
        reset_spies()
        supervisor.interorg_send("alpha", "beta", "ping")
        supervisor.interorg_send("beta", "alpha", "pong")
        assert len([1 for s, _, _ in DRIVEN if s == "beta"]) == 1
        assert len([1 for s, _, _ in DRIVEN if s == "alpha"]) == 1

    @t("an org may inter-org itself by slug ONLY through the bridge, and it is a no-op loop")
    def _():
        # the ledger refuses self-address, so this can only happen if a caller
        # bypasses post_mail — assert the bridge still terminates in one hop
        n = len(inbox("alpha", "in"))
        assert supervisor.interorg_send("alpha", "alpha", "self") is None
        assert len(inbox("alpha", "in")) == n + 1

    @t("a 20 KB inter-org body survives the hop (truncated only in the log)")
    def _():
        body = "L" * 20480
        supervisor.interorg_send("alpha", "beta", body)
        assert len(inbox("beta", "in")[-1]["body"]) == 20000
        assert len([m for m in mailbox("beta", "boss")
                    if len(m["body"]) == 20480]) == 1

    @t("unicode survives an inter-org hop")
    def _():
        supervisor.interorg_send("alpha", "beta", "π ≈ 3.14159 ⚑ 日本")
        assert inbox("beta", "in")[-1]["body"] == "π ≈ 3.14159 ⚑ 日本"


# ============================================================================ §6
def s6_chatq():
    print("\n§6 @ext: — RETIRED (user ruling 2026-08-05)")
    mkorg("chat", ("ceo", "cfo"))

    @t("a NEW @ext: send refuses at the ledger and names the hub route")
    def _():
        err = expect_error(lambda: load("chat").post_mail(
            "ceo", "@ext:abc", "hi"), "retired")
        assert "@net:" in err, err

    @t("the refusal leaves NO org-inbox record (nothing pretends to be sent)")
    def _():
        assert not [e for e in load("chat").d.get("org_inbox", [])
                    if e["peer"].startswith("@ext:")]

    @t("the user compose endpoint refuses @ext: the same way")
    def _():
        st, j = call("POST", "/api/orgs/chat/org_inbox/send",
                     {"to": "@ext:abc", "body": "x"})
        assert st == 422 and "retired" in j["detail"], (st, j)

    @t("HISTORICAL @ext: rows remain readable — records, not addresses")
    def _():
        o = load("chat")
        o.d.setdefault("org_inbox", []).append(
            {"id": "hist1", "dir": "in", "peer": "@ext:oldchat",
             "body": "from the chatq era", "at": "2026-08-01T00:00:00Z"})
        store.save_org(o)
        assert [e for e in load("chat").d["org_inbox"]
                if e["peer"] == "@ext:oldchat"]


# ============================================================================ §7
def s7_attachments():
    print("\n§7 attachments")
    mkorg("att", ("ceo", "cfo"))
    # C0: both hold the org-inbox audience, so this section keeps measuring
    # per-recipient attachment copies across SEVERAL recipients
    _a = load("att")
    for _who in ("ceo", "cfo"):
        _a.audience_grant(USER, _who, "extern")
    store.save_org(_a)
    reset_spies()
    root = mktemp("extmail-src-")

    @t("a file is copied into EVERY recipient's uploads/")
    def _():
        p = tmpfile("report.txt", "A" * 100, root)
        d = supervisor.deliver_org_inbox("att", "@mcp:a1", "see attached",
                                         attachments=[p])
        assert d == ["ceo", "cfo"]
        assert uploads("att", "ceo") == uploads("att", "cfo") == ["report.txt"]

    @t("the copy is byte-identical to the source")
    def _():
        p = os.path.join(supervisor.scratch_dir("att", "ceo"), "uploads", "report.txt")
        assert open(p, encoding="utf-8").read() == "A" * 100

    @t("each recipient's mail entry announces the attachment with name/path/bytes")
    def _():
        for n in ("ceo", "cfo"):
            a = mailbox("att", n)[-1]["attachments"]
            assert a == [{"name": "report.txt", "path": "uploads/report.txt",
                          "bytes": 100}], (n, a)

    @t("the path is RELATIVE to the agent's working folder, so it works sandboxed")
    def _():
        a = mailbox("att", "ceo")[-1]["attachments"][0]
        assert not os.path.isabs(a["path"]) and a["path"].startswith("uploads/")

    @t("a second send of the same name gets a collision suffix")
    def _():
        p = tmpfile("report.txt", "B" * 5, root)
        supervisor.deliver_org_inbox("att", "@mcp:a1", "again", attachments=[p])
        assert uploads("att", "ceo") == ["report-2.txt", "report.txt"]

    @t("…and a third gets -3")
    def _():
        p = tmpfile("report.txt", "C" * 5, root)
        supervisor.deliver_org_inbox("att", "@mcp:a1", "thrice", attachments=[p])
        assert "report-3.txt" in uploads("att", "ceo")

    @t("collision suffixes are PER NODE — the metadata differs per recipient")
    def _():
        o = mkorg("percol", ("n1", "n2"))
        for who in ("n1", "n2"):            # C0: recipients are holders
            o.audience_grant(USER, who, "extern")
        store.save_org(o)
        # n1 already holds the FILE name; n2 does not
        u1 = os.path.join(supervisor.scratch_dir("percol", "n1"), "uploads")
        os.makedirs(u1, exist_ok=True)
        open(os.path.join(u1, "spec.md"), "w").write("pre-existing")
        p = tmpfile("spec.md", "new", root)
        supervisor.deliver_org_inbox("percol", "@mcp:a", "x", attachments=[p])
        n1 = mailbox("percol", "n1")[-1]["attachments"][0]["name"]
        n2 = mailbox("percol", "n2")[-1]["attachments"][0]["name"]
        assert (n1, n2) == ("spec-2.md", "spec.md"), (n1, n2)

    @t("…and each node's metadata names a file that really exists in ITS uploads")
    def _():
        for n in ("n1", "n2"):
            a = mailbox("percol", n)[-1]["attachments"][0]
            assert a["name"] in uploads("percol", n), (n, a)

    @t("the pre-existing file was not overwritten")
    def _():
        u1 = os.path.join(supervisor.scratch_dir("percol", "n1"), "uploads")
        assert open(os.path.join(u1, "spec.md"), encoding="utf-8").read() \
            == "pre-existing"

    @t("hostile characters in the name are replaced, never interpreted")
    def _():
        p = tmpfile("we;ird&na$me#.txt", "x", root)
        supervisor.deliver_org_inbox("att", "@mcp:a2", "x", attachments=[p])
        got = mailbox("att", "ceo")[-1]["attachments"][0]["name"]
        assert got == "we_ird_na_me_.txt", got

    @t("a name is reduced to its BASENAME — no directory component survives")
    def _():
        sub = os.path.join(root, "deep", "deeper")
        p = tmpfile("nested.txt", "x", sub)
        supervisor.deliver_org_inbox("att", "@mcp:a3", "x", attachments=[p])
        a = mailbox("att", "ceo")[-1]["attachments"][0]
        assert a["name"] == "nested.txt" and "deep" not in a["path"]

    @t("a traversal-shaped source path cannot escape uploads/")
    def _():
        sub = os.path.join(root, "sneak")
        os.makedirs(sub, exist_ok=True)
        p = tmpfile("escape.txt", "x", sub)
        weird = os.path.join(sub, "..", "sneak", "..", "sneak", "escape.txt")
        supervisor.deliver_org_inbox("att", "@mcp:a4", "x", attachments=[weird])
        up = os.path.join(supervisor.scratch_dir("att", "ceo"), "uploads")
        parent = os.path.dirname(os.path.dirname(up))
        assert "escape.txt" in os.listdir(up)
        assert "escape.txt" not in os.listdir(parent)
        assert "escape.txt" not in os.listdir(os.path.dirname(up))

    @t("a name that sanitizes to nothing becomes file.bin")
    def _():
        # ⚠ a file literally named "..." or " " cannot be CREATED on Windows, so
        # the end-to-end path is unreachable on this host. Mirrored instead —
        # with a drift guard on the exact source line, the way msgvis.py mirrors
        # convo.ts: if the sanitizer changes, this check fails rather than
        # quietly testing a fossil.
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "orgtree", "supervisor.py"),
                   encoding="utf-8").read()
        assert 'safe = re.sub(r"[^\\w .()+\\-]", "_",' in src, "sanitizer moved"
        assert 'os.path.basename(src)).strip(" .") or "file.bin"' in src

        def sanitize(name):
            return re.sub(r"[^\w .()+\-]", "_",
                          os.path.basename(name)).strip(" .") or "file.bin"
        for name in ("...", "   ", " . . ", ".", "/x/..."):
            assert sanitize(name) == "file.bin", name
        assert sanitize("a/b/c.txt") == "c.txt"
        assert sanitize("no:pe?.txt") == "no_pe_.txt"
        if os.name != "nt":                     # the real thing where it exists
            p = tmpfile("...", "x", os.path.join(root, "dots"))
            supervisor.deliver_org_inbox("att", "@mcp:a5", "x", attachments=[p])
            assert mailbox("att", "ceo")[-1]["attachments"][0]["name"] == "file.bin"

    @t("a leading-dot name keeps its stem (strip only trims the ends)")
    def _():
        p = tmpfile(".env", "SECRET=1", os.path.join(root, "dot"))
        supervisor.deliver_org_inbox("att", "@mcp:a6", "x", attachments=[p])
        assert mailbox("att", "ceo")[-1]["attachments"][0]["name"] == "env"

    @t("a MISSING file drops silently — the mail still posts, with no attachments key")
    def _():
        gone = os.path.join(root, "never-existed.txt")
        d = supervisor.deliver_org_inbox("att", "@mcp:a7", "body survives",
                                         attachments=[gone])
        assert d == ["ceo", "cfo"]
        m = mailbox("att", "ceo")[-1]
        assert m["body"] == "body survives" and "attachments" not in m

    @t("⚑ a PARTIAL failure is invisible: the surviving files are announced, the lost one is not")
    def _():
        # Today a copy failure is swallowed (`except OSError: pass`) and neither
        # the sender's API result nor the recipient's envelope mentions it. The
        # right behaviour is arguably a warning on the send result; asserted as
        # CURRENT behaviour so a change fails loudly rather than silently.
        ok = tmpfile("kept.txt", "x", root)
        d = supervisor.deliver_org_inbox(
            "att", "@mcp:a8", "x", attachments=[ok, os.path.join(root, "gone.txt")])
        a = mailbox("att", "ceo")[-1]["attachments"]
        assert [x["name"] for x in a] == ["kept.txt"] and d == ["ceo", "cfo"]
        note("§7 ⚑ an attachment that fails to copy is dropped SILENTLY — the "
             "peer's send result and the recipient's envelope are both identical "
             "to a send that never carried it. The API validates existence and "
             "size first, so the reachable window is a TOCTOU (and any future "
             "funnel caller that does not pre-validate); a warning on the send "
             "result would close it. DESIGN QUESTION, not a decided defect.")

    @t("the ledger caps announced attachments at 10 per mail")
    def _():
        o = mkorg("cap10", ("ceo",))
        metas = [{"name": f"f{i}.txt", "path": f"uploads/f{i}.txt", "bytes": 1}
                 for i in range(15)]
        o.post_external_mail("@mcp:x", "many", attachments_by_node={"ceo": metas})
        assert len(o.d["mail"]["ceo"][-1]["attachments"]) == 10

    @t("metadata for a node that is NOT a recipient is ignored")
    def _():
        o = mkorg("stray", ("ceo",))
        o.hire("ceo", "ceo", "haiku", 0, "sub", **spec())
        o.post_external_mail("@mcp:x", "b", attachments_by_node={
            "sub": [{"name": "s.txt", "path": "uploads/s.txt", "bytes": 1}]})
        assert "attachments" not in o.d["mail"]["ceo"][-1]
        assert "sub" not in o.d["mail"]

    @t("an empty per-node list is not announced as an empty attachments array")
    def _():
        o = mkorg("emptyatt", ("ceo",))
        o.post_external_mail("@mcp:x", "b", attachments_by_node={"ceo": []})
        assert "attachments" not in o.d["mail"]["ceo"][-1]

    @t("the API refuses an attachment path that does not exist (422)")
    def _():
        st, j = call("POST", "/api/extern/pa/send",
                     {"org": "att", "body": "x", "attachments": ["Z:/nope.txt"]})
        assert st == 422 and "attachment not found" in j["detail"], (st, j)

    @t("the API refuses a directory as an attachment (isfile, not exists)")
    def _():
        st, j = call("POST", "/api/extern/pa/send",
                     {"org": "att", "body": "x", "attachments": [root]})
        assert st == 422 and "attachment not found" in j["detail"]

    @t("the API refuses a file over the 25 MB cap (413)")
    def _():
        big = os.path.join(root, "huge.bin")
        with open(big, "wb") as f:
            f.seek(25 * 1048576)
            f.write(b"\0")
        st, j = call("POST", "/api/extern/pa/send",
                     {"org": "att", "body": "x", "attachments": [big]})
        assert st == 413 and "25 MB" in j["detail"], (st, j)
        os.unlink(big)

    @t("the API silently keeps only the first 10 attachments")
    def _():
        ps = [tmpfile(f"m{i}.txt", "x", os.path.join(root, "many")) for i in range(14)]
        st, j = call("POST", "/api/extern/pa/send",
                     {"org": "att", "body": "many", "attachments": ps})
        assert st == 200
        a = mailbox("att", "ceo")[-1]["attachments"]
        assert [x["name"] for x in a] == [f"m{i}.txt" for i in range(10)], a

    @t("a rejected attachment set delivers NO message at all (validate-then-post)")
    def _():
        n = len(mailbox("att", "ceo"))
        call("POST", "/api/extern/pa/send",
             {"org": "att", "body": "x", "attachments": ["Z:/nope.txt"]})
        assert len(mailbox("att", "ceo")) == n

    @t("✓ nothing lands in a SEALED KIOSK's uploads/ (the ⚑ closed by C0)")
    def _():
        # THIS WAS AN OPEN FINDING and C0 closed it, incidentally rather than
        # deliberately — worth knowing, because an incidental fix is one
        # refactor away from returning. The funnel copies a file once PER
        # RECIPIENT; recipients used to be "every live top-level", which a
        # sealed kiosk still had, and are now org-inbox audience HOLDERS,
        # which a kiosk cannot have at all (audience_grant refuses outright).
        # So the copy loop has nothing to iterate. Delivery was always sealed;
        # what leaked was the file write that happened ahead of the seal.
        mkorg("kioskatt", ("top",), kiosk=True)
        p = tmpfile("leak.txt", "x", root)
        d = supervisor.deliver_org_inbox("kioskatt", "@mcp:z", "x", attachments=[p])
        assert d == [], "the seal must still stop delivery"
        assert uploads("kioskatt", "top") == [], (
            "a file landed in a sealed kiosk's agent workspace — the C0 "
            "holder rule is what prevents this today; if the recipient list "
            "ever stops being the loop bound, deliver_org_inbox needs its own "
            "kiosk check at the top (supervisor.py ~2560)")

    @t("no attachments argument at all leaves uploads/ untouched")
    def _():
        o = mkorg("noatt", ("ceo",))
        supervisor.deliver_org_inbox("noatt", "@mcp:x", "plain")
        assert uploads("noatt", "ceo") == []


# ============================================================================ §8
def s8_extern_http():
    print("\n§8 the extern HTTP surface — send / messages / wait, and the cursor")
    mkorg("ext", ("ceo", "cfo"))
    # C0: both hold the org-inbox audience, so §8 keeps measuring the HTTP
    # surface against a two-recipient org
    _e = load("ext")
    for _who in ("ceo", "cfo"):
        _e.audience_grant(USER, _who, "extern")
    store.save_org(_e)
    reset_spies()

    @t("POST send: 200 with the recipient list")
    def _():
        st, j = call("POST", "/api/extern/p1/send", {"org": "ext", "body": "hi"})
        assert st == 200 and j["delivered"] == ["ceo", "cfo"], (st, j)

    @t("the peer address is namespaced @mcp:<peer>")
    def _():
        assert inbox("ext", "in")[-1]["peer"] == "@mcp:p1"

    @t("send drives every recipient")
    def _():
        assert sorted(n for s, n, _ in DRIVEN if s == "ext") == ["ceo", "cfo"]

    @t("send: an empty body is 422")
    def _():
        st, j = call("POST", "/api/extern/p1/send", {"org": "ext", "body": ""})
        assert st == 422 and "empty message" in j["detail"], (st, j)

    @t("send: a whitespace-only body is 422 too")
    def _():
        st, j = call("POST", "/api/extern/p1/send", {"org": "ext", "body": "  \n\t "})
        assert st == 422

    @t("send: a missing org field is a 422 from validation, not a 500")
    def _():
        st, j = call("POST", "/api/extern/p1/send", {"body": "x"})
        assert st == 422

    @t("send: a 20 KB body is accepted whole")
    def _():
        st, j = call("POST", "/api/extern/p1/send",
                     {"org": "ext", "body": "K" * 20480})
        assert st == 200
        assert len([m for m in mailbox("ext", "ceo")
                    if len(m["body"]) == 20480]) == 1

    @t("send: a 4-byte unicode body round-trips")
    def _():
        st, _ = call("POST", "/api/extern/p1/send", {"org": "ext", "body": "𝔘𝔫𝔦 ⚑ 🜁"})
        assert st == 200 and inbox("ext", "in")[-1]["body"] == "𝔘𝔫𝔦 ⚑ 🜁"

    @t("peer id: the server charset is [A-Za-z0-9._-]{1,64}")
    def _():
        for bad in ("a b", "a#b", "a@b", "a" * 65, "a%2Fb", "péer"):
            st, _ = call("GET", f"/api/extern/{bad}/messages")
            assert st == 422, bad

    @t("peer id: dots and dashes (the real id shape) are accepted")
    def _():
        st, j = call("GET", "/api/extern/abc123def456.9f2c1a/messages")
        assert st == 200 and j == {"messages": []}

    # ---- read
    @t("read: an org's reply comes back with a cursor")
    def _():
        o = load("ext")
        o.post_mail("ceo", "@mcp:p1", "answer one")
        store.save_org(o)
        st, j = call("GET", "/api/extern/p1/messages")
        assert st == 200 and [m["body"] for m in j["messages"]] == ["answer one"]
        assert j["cursor"] == j["messages"][-1]["at"]

    @t("read: passing that cursor back returns nothing (no double delivery)")
    def _():
        _, j = call("GET", "/api/extern/p1/messages")
        st, j2 = call("GET", "/api/extern/p1/messages",
                      query=f"after={j['cursor']}".encode())
        assert st == 200 and j2 == {"messages": []}

    @t("read: no cursor key at all when the reply set is empty")
    def _():
        _, j = call("GET", "/api/extern/nobody/messages")
        assert j == {"messages": []} and "cursor" not in j

    @t("read: each message carries org, id, at and body — and nothing else")
    def _():
        _, j = call("GET", "/api/extern/p1/messages")
        assert set(j["messages"][0]) == {"org", "id", "at", "body"}

    @t("read: the internal `by` attribution is NOT exposed to the peer")
    def _():
        _, j = call("GET", "/api/extern/p1/messages")
        assert "by" not in j["messages"][0], "outbound speaks as the ORG"

    @t("read: INBOUND entries (the peer's own mail) are never echoed back")
    def _():
        _, j = call("GET", "/api/extern/p1/messages")
        assert all(m["body"] != "hi" for m in j["messages"])

    @t("read: another peer's replies are not visible")
    def _():
        o = load("ext")
        o.post_mail("ceo", "@mcp:other", "for someone else")
        store.save_org(o)
        _, j = call("GET", "/api/extern/p1/messages")
        assert all("someone else" not in m["body"] for m in j["messages"])

    @t("read: the org filter scopes to one org")
    def _():
        mkorg("ext2", ("boss",))
        o = load("ext2")
        o.post_mail("boss", "@mcp:p1", "from ext2")
        store.save_org(o)
        _, all_ = call("GET", "/api/extern/p1/messages")
        _, one = call("GET", "/api/extern/p1/messages", query=b"org=ext2")
        assert len(all_["messages"]) > len(one["messages"])
        assert {m["org"] for m in one["messages"]} == {"ext2"}

    @t("read: results are sorted by time across orgs")
    def _():
        _, j = call("GET", "/api/extern/p1/messages")
        ats = [m["at"] for m in j["messages"]]
        assert ats == sorted(ats)

    @t("read: an unknown org filter returns empty, not 404")
    def _():
        st, j = call("GET", "/api/extern/p1/messages", query=b"org=nosuchorg")
        assert st == 200 and j == {"messages": []}

    # ---- wait / fresh_only
    @t("wait: with no `after`, a reply OLDER than the peer's last question does not count")
    def _():
        o = mkorg("waiting", ("ceo",))
        o._org_inbox_log("in", "@mcp:w1", "question one")
        o.post_mail("ceo", "@mcp:w1", "answer one")
        o._org_inbox_log("in", "@mcp:w1", "question two")     # a NEW question
        store.save_org(o)
        assert api._extern_scan("@mcp:w1", None, None, True) == []

    @t("wait: the full read still shows the old answer (read is full-history)")
    def _():
        got = [m["body"] for m in api._extern_scan("@mcp:w1", None, None, False)]
        assert got == ["answer one"]

    @t("wait: the answer to question TWO does satisfy it")
    def _():
        o = load("waiting")
        o.post_mail("ceo", "@mcp:w1", "answer two")
        store.save_org(o)
        got = [m["body"] for m in api._extern_scan("@mcp:w1", None, None, True)]
        assert got == ["answer two"], got

    @t("wait: an explicit `after` overrides the fresh floor entirely")
    def _():
        got = [m["body"] for m in api._extern_scan(
            "@mcp:w1", None, "2000-01-01T00:00:00.000Z", True)]
        assert got == ["answer one", "answer two"], got

    @t("wait: a peer that never wrote to the org gets nothing (no collapsed floor)")
    def _():
        o = load("waiting")
        o.post_mail("ceo", "@mcp:silent", "unsolicited")
        store.save_org(o)
        assert api._extern_scan("@mcp:silent", None, None, True) == []
        assert len(api._extern_scan("@mcp:silent", None, None, False)) == 1

    @t("wait: the peer's own inbound trimmed by the 200-cap ⇒ nothing is provably fresh")
    def _():
        o = mkorg("trimmed", ("ceo",))
        o._org_inbox_log("in", "@mcp:t1", "my question")
        for i in range(120):
            o._org_inbox_log("in", "@mcp:filler", f"f{i}")
        o.post_mail("ceo", "@mcp:t1", "the answer")
        for i in range(120):
            o._org_inbox_log("in", "@mcp:filler", f"g{i}")
        store.save_org(o)
        es = load("trimmed").d["org_inbox"]
        assert len(es) == 200
        assert not [e for e in es if e["peer"] == "@mcp:t1" and e["dir"] == "in"]
        assert [e for e in es if e["peer"] == "@mcp:t1" and e["dir"] == "out"]
        assert api._extern_scan("@mcp:t1", None, None, True) == [], "fresh: withheld"
        assert len(api._extern_scan("@mcp:t1", None, None, False)) == 1, "read: visible"

    @t("wait endpoint: returns an empty list on timeout, never an error")
    def _():
        st, j = call("GET", "/api/extern/timeoutpeer/wait", query=b"timeout=1")
        assert st == 200 and j == {"messages": []}

    @t("wait endpoint: an already-satisfiable wait returns immediately with a cursor")
    def _():
        o = mkorg("instant", ("ceo",))
        o._org_inbox_log("in", "@mcp:i1", "q")
        store.save_org(o)
        o = load("instant")
        o.post_mail("ceo", "@mcp:i1", "a")
        store.save_org(o)
        t0 = time.monotonic()
        st, j = call("GET", "/api/extern/i1/wait", query=b"timeout=20")
        assert st == 200 and [m["body"] for m in j["messages"]] == ["a"]
        assert j["cursor"] and time.monotonic() - t0 < 2.0

    @t("wait endpoint: the timeout is clamped to [1, 55]")
    def _():
        t0 = time.monotonic()
        st, _ = call("GET", "/api/extern/nobodyatall/wait", query=b"timeout=-99")
        assert st == 200 and time.monotonic() - t0 < 4.0

    @t("wait: a reply posted while the waiter is PARKED wakes it (REVISION gating)")
    def _():
        o = mkorg("parked", ("ceo",))
        o._org_inbox_log("in", "@mcp:pk", "q")
        store.save_org(o)
        done = {}

        async def waiter():
            done["r"] = await api.extern_wait("pk", None, None, 12)

        async def replier():
            await asyncio.sleep(1.2)
            oo = load("parked")
            oo.post_mail("ceo", "@mcp:pk", "late answer")
            store.save_org(oo)

        async def both():
            await asyncio.gather(waiter(), replier())
        t0 = time.monotonic()
        _run(both())
        el = time.monotonic() - t0
        assert [m["body"] for m in done["r"]["messages"]] == ["late answer"], done
        assert 1.0 < el < 8.0, el

    @t("wait: two DIFFERENT peer ids are never woken by each other's reply")
    def _():
        # this is why peer_id() gained a per-session suffix (№5)
        o = mkorg("twowait", ("ceo",))
        o._org_inbox_log("in", "@mcp:sess.aaa", "q from A")
        o._org_inbox_log("in", "@mcp:sess.bbb", "q from B")
        store.save_org(o)
        out = {}

        async def w(name, peer):
            out[name] = await api.extern_wait(peer, None, None, 6)

        async def replyA():
            await asyncio.sleep(0.8)
            oo = load("twowait")
            oo.post_mail("ceo", "@mcp:sess.aaa", "answer for A")
            store.save_org(oo)

        async def go():
            await asyncio.gather(w("A", "sess.aaa"), w("B", "sess.bbb"), replyA())
        _run(go())
        assert [m["body"] for m in out["A"]["messages"]] == ["answer for A"]
        assert out["B"]["messages"] == [], out["B"]

    @t("wait: two waiters SHARING one peer id both get it — the pre-suffix hazard")
    def _():
        # asserted as the reason the suffix exists: with one id per machine,
        # the org's answer to session ① also satisfied session ②'s wait
        o = mkorg("sharedid", ("ceo",))
        o._org_inbox_log("in", "@mcp:shared", "q")
        store.save_org(o)
        out = {}

        async def w(name):
            out[name] = await api.extern_wait("shared", None, None, 6)

        async def rep():
            await asyncio.sleep(0.8)
            oo = load("sharedid")
            oo.post_mail("ceo", "@mcp:shared", "one answer")
            store.save_org(oo)

        async def go():
            await asyncio.gather(w("one"), w("two"), rep())
        _run(go())
        assert [m["body"] for m in out["one"]["messages"]] == ["one answer"]
        assert [m["body"] for m in out["two"]["messages"]] == ["one answer"]

    @t("cursor: N rounds of send→reply→wait(after=cursor) deliver each reply exactly once")
    def _():
        o = mkorg("rounds", ("ceo",))
        store.save_org(o)
        after = None
        seen = []
        for i in range(8):
            st, _ = call("POST", "/api/extern/rp/send",
                         {"org": "rounds", "body": f"q{i}"})
            assert st == 200
            oo = load("rounds")
            oo.post_mail("ceo", "@mcp:rp", f"a{i}")
            store.save_org(oo)
            q = (f"after={after}&" if after else "") + "timeout=3"
            st, j = call("GET", "/api/extern/rp/wait", query=q.encode())
            got = [m["body"] for m in j["messages"]]
            assert got == [f"a{i}"], (i, got, after)
            seen += got
            after = j["cursor"]
        assert seen == [f"a{i}" for i in range(8)]
        assert len(seen) == len(set(seen)), "no duplicates"

    @t("cursor: replaying the LAST cursor after every reply yields the whole set once")
    def _():
        _, j = call("GET", "/api/extern/rp/messages",
                    query=b"after=2000-01-01T00:00:00.000Z")
        assert [m["body"] for m in j["messages"]] == [f"a{i}" for i in range(8)]

    @t("⚑ cursor: a reply sharing the cursor's exact millisecond is never delivered")
    def _():
        # OPEN FINDING (api.py — not this suite's to fix). `_extern_scan` filters
        # with a STRICT `>` on a millisecond-resolution string stamp, and the
        # cursor IS the last delivered stamp. Two replies in one millisecond are
        # fine when they ride the same batch; a third that lands in that same
        # millisecond AFTER the batch was handed over can never satisfy a later
        # read or wait. The principled fix is a (at, id) cursor — the entries
        # already carry unique ids.
        o = mkorg("tie", ("ceo",))
        o.post_mail("ceo", "@mcp:tp", "first")
        store.save_org(o)
        _, j = call("GET", "/api/extern/tp/messages")
        cur = j["cursor"]
        o = load("tie")
        o.post_mail("ceo", "@mcp:tp", "same-ms sibling")
        o.d["org_inbox"][-1]["at"] = cur              # same millisecond
        store.save_org(o)
        _, j2 = call("GET", "/api/extern/tp/messages",
                     query=f"after={cur}".encode())
        assert j2["messages"] == [], "current behaviour: the sibling is lost"
        note("§8 ⚑ the extern cursor is a bare millisecond string compared with "
             "a strict `>`, so a reply landing in the SAME millisecond as the "
             "cursor is never delivered (api.py _extern_scan). Entries already "
             "carry unique ids — a (at, id) cursor closes it.")

    @t("⚑ wait: an org replying in the same millisecond its question arrived is never seen")
    def _():
        # same root cause on the fresh_only floor: floor = max(peer's own `at`)
        # and the comparison is strict, so a same-millisecond reply is withheld
        # forever unless the caller supplies an older explicit `after`.
        o = mkorg("tie2", ("ceo",))
        o._org_inbox_log("in", "@mcp:tq", "q")
        o.post_mail("ceo", "@mcp:tq", "instant")
        for e in o.d["org_inbox"]:
            if e["peer"] == "@mcp:tq":
                e["at"] = "2026-02-02T00:00:00.000Z"
        store.save_org(o)
        assert api._extern_scan("@mcp:tq", None, None, True) == []
        assert len(api._extern_scan("@mcp:tq", None, None, False)) == 1

    @t("a reply to a peer is NOT a drive of any node (outbound never wakes the org)")
    def _():
        reset_spies()
        o = load("ext")
        o.post_mail("ceo", "@mcp:p1", "no drive please")
        store.save_org(o)
        assert DRIVEN == []


# ============================================================================ §9
def s9_externtool():
    print("\n§9 externtool.py driven as a real MCP server (uvicorn on :7406)")
    from orgtree import externtool as ET       # id pinned at module setup

    # ⚠ The server process must not be able to start a REAL turn. Unstubbed,
    # deliver_org_inbox's send_message launches the host's actual Claude Code
    # CLI against the operator's subscription — and the turn's load-modify-save
    # cycles then race the parent's reads of the same doc (observed: the org
    # inbox entry vanishing under a live turn). send_message is stubbed to the
    # nudge-accepted shape, and ORGTREE_CLAUDE_CLI is pinned at fakecli.js as a
    # second net in case any other path reaches _run_turn.
    boot = (
        "import os,sys;"
        f"sys.path.insert(0, {os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')!r});"
        "from orgtree import supervisor, sandbox;"
        "sandbox.warm=lambda o: None;"
        "supervisor.storage_check=lambda s: None;"
        "supervisor.send_message=lambda s,n,t,command=False: "
        "{'accepted': True, 'queued': 0};"
        "from orgtree import api;"
        "import uvicorn;"
        "uvicorn.run(api.app, host='127.0.0.1', port=7406, log_level='error')")
    env = dict(os.environ, ORGTREE_DATA=DATA, ORGTREE_PORT="7406",
               ORGTREE_CLAUDE_CLI=os.path.join(
                   os.path.dirname(os.path.abspath(__file__)), "fakecli.js"))
    env.pop("ORGTREE_PUBLIC_PORT", None)                # admin listener only
    srv = subprocess.Popen([sys.executable, "-c", boot], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    import urllib.request
    up = False
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:7406/api/orgs", timeout=2).read()
            up = True
            break
        except Exception:                                          # noqa: BLE001
            time.sleep(0.25)
    if not up:
        srv.kill()
        raise AssertionError("uvicorn on :7406 never came up:\n"
                             + (srv.stderr.read().decode("utf-8", "replace")[-2000:]))

    class MCP:
        """One externtool.py process, spoken to over real MCP stdio."""

        def __init__(self, extern_id=None, home=None):
            e = dict(os.environ, ORGTREE_PORT="7406",
                     ORGTREE_BASE="http://127.0.0.1:7406")
            e.pop("ORGTREE_EXTERN_ID", None)
            if extern_id is not None:
                e["ORGTREE_EXTERN_ID"] = extern_id
            if home:
                e["HOME"] = home
                e["USERPROFILE"] = home
            self.p = subprocess.Popen(
                [sys.executable, os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..", "orgtree", "externtool.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=e, bufsize=0)
            self.n = 0

        def rpc(self, method, params=None, timeout=90):
            self.n += 1
            msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
            if params is not None:
                msg["params"] = params
            self.p.stdin.write((json.dumps(msg) + "\n").encode())
            self.p.stdin.flush()
            box = {}

            def rd():
                box["line"] = self.p.stdout.readline()
            th = threading.Thread(target=rd, daemon=True)
            th.start()
            th.join(timeout)
            assert "line" in box, f"{method} timed out"
            return json.loads(box["line"].decode("utf-8", "replace"))

        def tool(self, name, args=None):
            r = self.rpc("tools/call", {"name": name, "arguments": args or {}})
            c = r["result"]["content"][0]["text"]
            try:
                return json.loads(c), r["result"].get("isError", False)
            except json.JSONDecodeError:
                return c, r["result"].get("isError", False)

        def notify(self, method):
            self.p.stdin.write(
                (json.dumps({"jsonrpc": "2.0", "method": method}) + "\n").encode())
            self.p.stdin.flush()

        def close(self):
            try:
                self.p.stdin.close()
            except Exception:                                      # noqa: BLE001
                pass
            self.p.kill()

    def srv_reply(org, node, peer, body):
        """Have the org reply THROUGH the running server. ⚠ It must not be
        written straight to disk: extern_wait gates its rescans on
        store.REVISION, an IN-PROCESS counter, so a doc written by another
        process does not wake a parked waiter until its poll slice expires.
        One backend per data dir is the documented deployment, so that is
        correct — but it makes a direct-to-disk reply invisible for 25 s here."""
        req = urllib.request.Request(
            "http://127.0.0.1:7406/api/agent",
            data=json.dumps({"org": org, "node": node, "tool": "orgtree_message",
                             "args": {"to": f"@mcp:{peer}", "body": body}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    def seed_home(base):
        """A fake HOME with the machine-stable base already minted. ⚠ Without
        this, two children spawned back to back BOTH find the file missing and
        both mint a base — the id file is written without a lock, so the shared
        base is only shared once one process has finished starting."""
        h = mktemp("extmail-home-")
        os.makedirs(os.path.join(h, ".orgtree"), exist_ok=True)
        with open(os.path.join(h, ".orgtree", "extern-id"), "w",
                  encoding="utf-8") as f:
            f.write(base)
        return h

    peers = []
    try:
        mkorg("mcporg", ("ceo",))
        mkorg("mcpkiosk", ("top",), kiosk=True)
        m = MCP(extern_id="suite.alpha")
        peers.append(m)

        @t("initialize returns the protocol version and a server name")
        def _():
            r = m.rpc("initialize", {"protocolVersion": "2024-11-05"})
            assert r["result"]["serverInfo"]["name"] == "orgtree-extern"
            assert r["result"]["protocolVersion"] == "2024-11-05"

        @t("tools/list advertises exactly the four verbs")
        def _():
            r = m.rpc("tools/list")
            assert [x["name"] for x in r["result"]["tools"]] == [
                "orgtree_list_orgs", "orgtree_send", "orgtree_read", "orgtree_wait"]

        @t("every tool card has an object inputSchema")
        def _():
            r = m.rpc("tools/list")
            for x in r["result"]["tools"]:
                assert x["inputSchema"]["type"] == "object", x["name"]

        @t("orgtree_list_orgs lists the open org and hides the kiosk")
        def _():
            out, err = m.tool("orgtree_list_orgs")
            slugs = [o["slug"] for o in out["orgs"]]
            assert not err and "mcporg" in slugs and "mcpkiosk" not in slugs, slugs

        @t("list_orgs reports the caller its own peer id")
        def _():
            out, _ = m.tool("orgtree_list_orgs")
            assert out["your_peer_id"] == "@mcp:suite.alpha"

        @t("orgtree_send delivers and reports the recipients")
        def _():
            out, err = m.tool("orgtree_send", {"org": "mcporg", "body": "hello mcp"})
            assert not err and out["delivered"] == ["ceo"], out
            assert out["your_peer_id"] == "@mcp:suite.alpha"

        @t("the message really landed in the org inbox")
        def _():
            assert inbox("mcporg", "in", "@mcp:suite.alpha")[-1]["body"] == "hello mcp"

        @t("orgtree_send to the KIOSK is an error indistinguishable from a bad slug")
        def _():
            a, ea = m.tool("orgtree_send", {"org": "mcpkiosk", "body": "x"})
            b, eb = m.tool("orgtree_send", {"org": "ghostorg", "body": "x"})
            assert ea and eb
            assert str(a).replace("mcpkiosk", "?") == str(b).replace("ghostorg", "?")

        @t("orgtree_send with an empty body is an error, not a silent no-op")
        def _():
            _, err = m.tool("orgtree_send", {"org": "mcporg", "body": ""})
            assert err

        @t("orgtree_read returns the org's reply and a cursor")
        def _():
            srv_reply("mcporg", "ceo", "suite.alpha", "the org replies")
            out, err = m.tool("orgtree_read")
            assert not err and [x["body"] for x in out["messages"]] == ["the org replies"]
            assert out["cursor"]

        @t("orgtree_read with that cursor returns nothing")
        def _():
            out, _ = m.tool("orgtree_read")
            out2, _ = m.tool("orgtree_read", {"after": out["cursor"]})
            assert out2["messages"] == []

        @t("orgtree_wait blocks and returns the reply that arrives while it waits")
        def _():
            m.tool("orgtree_send", {"org": "mcporg", "body": "question two"})

            def later():
                time.sleep(1.5)
                srv_reply("mcporg", "ceo", "suite.alpha", "answer two")
            threading.Thread(target=later, daemon=True).start()
            t0 = time.monotonic()
            out, err = m.tool("orgtree_wait", {"org": "mcporg", "timeout_s": 30})
            el = time.monotonic() - t0
            assert not err and [x["body"] for x in out["messages"]] == ["answer two"]
            assert 1.0 < el < 25.0, el

        @t("a wait with NO cursor re-delivers a reply it already returned (documented)")
        def _():
            # the tool card puts cursor bookkeeping on the caller: with no
            # `after`, the floor is only the peer's own last message, so an
            # answer already handed over still satisfies the next wait
            out, err = m.tool("orgtree_wait", {"org": "mcporg", "timeout_s": 8})
            assert not err and [x["body"] for x in out["messages"]] == ["answer two"]

        @t("…and passing that cursor back makes the same wait time out empty")
        def _():
            cur = m.tool("orgtree_wait", {"org": "mcporg", "timeout_s": 8})[0]["cursor"]
            out, err = m.tool("orgtree_wait",
                              {"org": "mcporg", "after": cur, "timeout_s": 6})
            assert not err and out["messages"] == [], out

        @t("orgtree_wait returns an empty list on timeout after a fresh question")
        def _():
            m.tool("orgtree_send", {"org": "mcporg", "body": "question three"})
            out, err = m.tool("orgtree_wait", {"org": "mcporg", "timeout_s": 5})
            assert not err and out["messages"] == [], out

        @t("⚑→✓ LIVE: a non-numeric timeout_s no longer reports the server unreachable")
        def _():
            # before the fix, int("soon") raised inside run_tool and the
            # catch-all answered "orgtree unreachable at http://…" — a wrong
            # diagnosis for a server that was answering fine. Driven with a
            # reply ALREADY waiting so the repaired path returns on its first
            # slice instead of sitting out the 120 s default it falls back to.
            m.tool("orgtree_send", {"org": "mcporg", "body": "question four"})
            srv_reply("mcporg", "ceo", "suite.alpha", "answer four")
            t0 = time.monotonic()
            out, err = m.tool("orgtree_wait", {"org": "mcporg", "timeout_s": "soon"})
            assert not err, out
            assert "unreachable" not in str(out).lower(), out
            assert [x["body"] for x in out["messages"]] == ["answer four"], out
            assert time.monotonic() - t0 < 30.0

        @t("…and the coercion table behind it")
        def _():
            assert ET._int_arg({"timeout_s": "soon"}, "timeout_s", 120) == 120
            assert ET._int_arg({"timeout_s": "2 minutes"}, "timeout_s", 120) == 120
            assert ET._int_arg({}, "timeout_s", 120) == 120
            assert ET._int_arg({"timeout_s": ""}, "timeout_s", 120) == 120
            assert ET._int_arg({"timeout_s": None}, "timeout_s", 120) == 120

        @t("…while anything coercible is honoured")
        def _():
            assert ET._int_arg({"timeout_s": "5"}, "timeout_s", 120) == 5
            assert ET._int_arg({"timeout_s": 5.9}, "timeout_s", 120) == 5
            assert ET._int_arg({"timeout_s": "5.9"}, "timeout_s", 120) == 5
            assert ET._int_arg({"timeout_s": True}, "timeout_s", 120) == 1

        @t("a stringified number really shortens the live wait")
        def _():
            # a fresh question first, so the fresh_only floor withholds every
            # answer already handed over and this really does hit the timeout
            m.tool("orgtree_send", {"org": "mcporg", "body": "question five"})
            t0 = time.monotonic()
            out, err = m.tool("orgtree_wait", {"org": "mcporg", "timeout_s": "5"})
            assert not err and out["messages"] == [], out
            assert time.monotonic() - t0 < 30.0

        @t("attachments coercion accepts a bare string, a list, and nothing")
        def _():
            assert ET._attachments({"attachments": "C:/a.txt"}) == ["C:/a.txt"]
            assert ET._attachments({"attachments": ["a", "b"]}) == ["a", "b"]
            assert ET._attachments({}) == [] and ET._attachments(
                {"attachments": None}) == []
            assert ET._attachments({"attachments": [1, 2]}) == ["1", "2"]

        @t("orgtree_send coerces a bare string attachments value into a list")
        def _():
            p = tmpfile("mcpatt.txt", "hello")
            out, err = m.tool("orgtree_send", {"org": "mcporg", "body": "with file",
                                               "attachments": p})
            assert not err, out
            assert "mcpatt.txt" in uploads("mcporg", "ceo")

        @t("an unknown tool name is an isError result, not a crash")
        def _():
            out, err = m.tool("orgtree_nope")
            assert err and "unknown tool" in str(out)

        @t("a malformed stdin line is skipped and the server keeps serving")
        def _():
            m.p.stdin.write(b"not json at all\n")
            m.p.stdin.write(b"\n")
            m.p.stdin.flush()
            r = m.rpc("tools/list")
            assert len(r["result"]["tools"]) == 4

        @t("a notification (no id) draws no reply and does not desync the stream")
        def _():
            m.notify("notifications/initialized")
            r = m.rpc("tools/list")
            assert r["id"] == m.n

        @t("an unknown method WITH an id gets an empty result (never a hang)")
        def _():
            r = m.rpc("resources/list")
            assert r["result"] == {} and r["id"] == m.n

        # ---- peer identity
        @t("ORGTREE_EXTERN_ID is used verbatim, with no session suffix")
        def _():
            m2 = MCP(extern_id="pinned.identity")
            peers.append(m2)
            out, _ = m2.tool("orgtree_list_orgs")
            assert out["your_peer_id"] == "@mcp:pinned.identity"

        @t("two processes sharing a machine get DIFFERENT peer ids (the №5 suffix)")
        def _():
            home = seed_home("machinebase01")
            a, b = MCP(home=home), MCP(home=home)
            peers.extend((a, b))
            ia, _ = a.tool("orgtree_list_orgs")
            ib, _ = b.tool("orgtree_list_orgs")
            assert ia["your_peer_id"] != ib["your_peer_id"], (ia, ib)

        @t("…and they share the machine-stable BASE, differing only in the suffix")
        def _():
            home = seed_home("machinebase02")
            a, b = MCP(home=home), MCP(home=home)
            peers.extend((a, b))
            ia = a.tool("orgtree_list_orgs")[0]["your_peer_id"][5:]
            ib = b.tool("orgtree_list_orgs")[0]["your_peer_id"][5:]
            assert ia.split(".")[0] == ib.split(".")[0] == "machinebase02", (ia, ib)
            assert ia.split(".")[1] != ib.split(".")[1]
            assert len(ia.split(".")[1]) == 6

        @t("a fresh HOME mints a base and persists it for the next session")
        def _():
            home = mktemp("extmail-fresh-")
            a = MCP(home=home)
            peers.append(a)
            ia = a.tool("orgtree_list_orgs")[0]["your_peer_id"][5:]
            path = os.path.join(home, ".orgtree", "extern-id")
            assert os.path.isfile(path), "the base must survive the session"
            base = open(path, encoding="utf-8").read().strip()
            assert ia.split(".")[0] == base and len(base) == 12, (ia, base)
            b = MCP(home=home)
            peers.append(b)
            ib = b.tool("orgtree_list_orgs")[0]["your_peer_id"][5:]
            assert ib.split(".")[0] == base and ib != ia, (ia, ib)

        @t("two concurrently-waiting sessions are NOT woken by each other's reply")
        def _():
            home = seed_home("machinebase03")
            a, b = MCP(home=home), MCP(home=home)
            peers.extend((a, b))
            pa = a.tool("orgtree_list_orgs")[0]["your_peer_id"][5:]
            a.tool("orgtree_send", {"org": "mcporg", "body": "A asks"})
            b.tool("orgtree_send", {"org": "mcporg", "body": "B asks"})
            res = {}

            def w(name, cli):
                res[name] = cli.tool("orgtree_wait",
                                     {"org": "mcporg", "timeout_s": 12})[0]
            ta = threading.Thread(target=w, args=("a", a), daemon=True)
            tb = threading.Thread(target=w, args=("b", b), daemon=True)
            ta.start()
            tb.start()
            time.sleep(1.5)
            srv_reply("mcporg", "ceo", pa, "answer for A only")
            ta.join(40)
            tb.join(40)
            assert [x["body"] for x in res["a"]["messages"]] == ["answer for A only"]
            assert res["b"]["messages"] == [], res["b"]

        @t("⚑→✓ a corrupt stored extern-id self-heals instead of 422ing forever")
        def _():
            # reproduction: any editor that saves ~/.orgtree/extern-id with a BOM
            # (or any tool that writes something else there) used to produce a
            # peer id outside the server's [A-Za-z0-9._-]{1,64} charset, so
            # EVERY call 422ed with a message naming a file the user cannot see.
            home = mktemp("extmail-badid-")
            os.makedirs(os.path.join(home, ".orgtree"), exist_ok=True)
            with open(os.path.join(home, ".orgtree", "extern-id"), "wb") as f:
                f.write(b"\xef\xbb\xbfnot a valid id \xf0\x9f\x92\xa5/../")
            c = MCP(home=home)
            peers.append(c)
            out, err = c.tool("orgtree_list_orgs")
            assert not err, out
            pid = out["your_peer_id"][5:]
            assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", pid), pid
            out2, err2 = c.tool("orgtree_send", {"org": "mcporg", "body": "healed"})
            assert not err2 and out2["delivered"] == ["ceo"], out2

        @t("an over-long stored base is regenerated rather than blowing the 64-char cap")
        def _():
            home = mktemp("extmail-longid-")
            os.makedirs(os.path.join(home, ".orgtree"), exist_ok=True)
            with open(os.path.join(home, ".orgtree", "extern-id"), "w") as f:
                f.write("z" * 200)
            c = MCP(home=home)
            peers.append(c)
            out, err = c.tool("orgtree_list_orgs")
            assert not err and len(out["your_peer_id"][5:]) <= 64, out

        @t("an explicit ORGTREE_EXTERN_ID that the server would refuse fails LOUDLY")
        def _():
            c = MCP(extern_id="has spaces/and/slashes")
            peers.append(c)
            out, err = c.tool("orgtree_send", {"org": "mcporg", "body": "x"})
            assert err, out
            assert "ORGTREE_EXTERN_ID" in str(out), out

        @t("the server being DOWN is reported as unreachable, not as a crash")
        def _():
            e = dict(os.environ, ORGTREE_BASE="http://127.0.0.1:7499",
                     ORGTREE_EXTERN_ID="down.test")
            p = subprocess.Popen(
                [sys.executable, os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..", "orgtree", "externtool.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=e, bufsize=0)
            try:
                p.stdin.write((json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "orgtree_list_orgs", "arguments": {}}})
                    + "\n").encode())
                p.stdin.flush()
                r = json.loads(p.stdout.readline().decode())
                assert r["result"]["isError"] is True
                assert "unreachable" in r["result"]["content"][0]["text"]
            finally:
                p.kill()

        @t("PEER is percent-safe in the URL: a dotted session id routes correctly")
        def _():
            c = MCP(extern_id="a.b.c-d_e.123456")
            peers.append(c)
            out, err = c.tool("orgtree_send", {"org": "mcporg", "body": "dotted"})
            assert not err and out["delivered"] == ["ceo"], out
            assert inbox("mcporg", "in")[-1]["peer"] == "@mcp:a.b.c-d_e.123456"

    finally:
        for p in peers:
            p.close()
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:                                          # noqa: BLE001
            srv.kill()


# ============================================================================ §10
def s10_authorization():
    print("\n§10 authorization — the ledger authorizes, the transports deliver")
    o = mkorg("auth", ("ceo",))
    o.hire("ceo", "ceo", "haiku", 5, "mid", **spec())
    o.hire("mid", "mid", "haiku", 0, "deep", **spec())
    store.save_org(o)
    mkorg("nbr", ("boss",))

    def agent(tool, node, args, org="auth"):
        return call("POST", "/api/agent",
                    {"org": org, "node": node, "tool": tool, "args": args})

    @t("a top-level agent may address @org:")
    def _():
        reset_spies()
        supervisor.interorg_send = _spy_interorg_send
        try:
            st, j = agent("orgtree_message", "ceo",
                          {"to": "@org:nbr", "body": "from the top"})
        finally:
            supervisor.interorg_send = _real_interorg_send
        assert st == 200 and j["delivered"] == "@org:nbr", (st, j)
        assert INTERORG == [("auth", "nbr", "from the top")], INTERORG

    @t("a SUBORDINATE is refused, and no transport ran")
    def _():
        reset_spies()
        supervisor.interorg_send = _spy_interorg_send
        try:
            st, j = agent("orgtree_message", "mid",
                          {"to": "@org:nbr", "body": "sneaky"})
        finally:
            supervisor.interorg_send = _real_interorg_send
        # C0: the refusal is now about HOLDING the org-inbox audience, not
        # about depth — a deep non-holder is refused, a deep HOLDER is not
        assert st == 422 and "audience holders" in j["detail"], (st, j)
        assert INTERORG == []

    @t("the refusal names BOTH remedies, not a workaround")
    def _():
        _, j = agent("orgtree_message", "deep", {"to": "@mcp:c", "body": "x"})
        assert "escalate the message to your superior" in j["detail"]
        assert "action=grant" in j["detail"], (
            "C0: the deep agent must be told it can be GRANTED the audience, "
            "which is the route that did not exist under the old rule")

    @t("a refused outbound leaves NO org-inbox entry (nothing pretends to have gone)")
    def _():
        outs = [e for e in load("auth").d.get("org_inbox", []) if e["dir"] == "out"]
        assert all(e["by"] != "mid" and e["by"] != "deep" for e in outs)

    @t("an ORG-INBOX audience holder deep in the tree may speak for the org")
    def _():
        oo = load("auth")
        oo.d["audiences"].append({"grantee": "deep", "grantor": EXTERN,
                                  "granted_at": "x", "reason": "org inbox"})
        store.save_org(oo)
        reset_spies()
        supervisor.interorg_send = _spy_interorg_send
        try:
            st, j = agent("orgtree_message", "deep",
                          {"to": "@org:nbr", "body": "holder speaks"})
        finally:
            supervisor.interorg_send = _real_interorg_send
        assert st == 200 and INTERORG == [("auth", "nbr", "holder speaks")]

    @t("…but a holder of a DIFFERENT audience still cannot")
    def _():
        oo = load("auth")
        oo.d["audiences"] = [a for a in oo.d["audiences"] if a["grantee"] != "deep"]
        oo.d["audiences"].append({"grantee": "deep", "grantor": USER,
                                  "granted_at": "x", "reason": "user audience"})
        store.save_org(oo)
        st, j = agent("orgtree_message", "deep", {"to": "@org:nbr", "body": "x"})
        # C0: the refusal names the ORG-INBOX audience, not depth — a USER
        # audience is a different grant and confers nothing here
        assert st == 422 and "audience holders" in j["detail"], (st, j)

    @t("the USER cannot speak to an outside party through the node-message route")
    def _():
        st, j = call("POST", "/api/orgs/auth/nodes/@org:nbr/message", {"text": "x"})
        assert st in (404, 422), (st, j)

    @t("the ledger refuses the user as an outside sender in any case")
    def _():
        expect_error(lambda: load("auth").post_mail(USER, "@org:nbr", "x"),
                     "only agents message outside parties")

    @t("@system cannot speak for the org either")
    def _():
        expect_error(lambda: load("auth").post_mail(SYSTEM, "@mcp:p", "x"),
                     "only agents message outside parties")

    @t("a kiosk agent is refused before authority is even considered")
    def _():
        mkorg("authk", ("top",), kiosk=True)
        st, j = agent("orgtree_message", "top", {"to": "@org:nbr", "body": "x"},
                      org="authk")
        assert st == 422 and "sealed kiosk" in j["detail"]

    @t("no transport ran for the kiosk refusal")
    def _():
        reset_spies()
        supervisor.interorg_send = _spy_interorg_send
        try:
            agent("orgtree_message", "top", {"to": "@mcp:c", "body": "x"}, org="authk")
        finally:
            supervisor.interorg_send = _real_interorg_send
        assert INTERORG == []

    @t("@ext: refuses as RETIRED at the agent surface, hub route named")
    def _():
        st, j = agent("orgtree_message", "ceo",
                      {"to": "@ext:outsider", "body": "the reply"})
        assert st == 422 and "retired" in j["detail"], (st, j)
        assert "@net:" in j["detail"], j

    @t("@mcp: invokes NO transport — the org-inbox entry IS the delivery")
    def _():
        reset_spies()
        supervisor.interorg_send = _spy_interorg_send
        try:
            st, j = agent("orgtree_message", "ceo",
                          {"to": "@mcp:poller", "body": "pull me"})
        finally:
            supervisor.interorg_send = _real_interorg_send
        assert st == 200 and INTERORG == []
        assert api._extern_scan("@mcp:poller", "auth", None)[-1]["body"] == "pull me"

    # ---- external response handles (user feature 2026-08-20): a hire may carry
    # @mcp: addresses it answers DIRECTLY, from any depth — the in-game Prompt
    # Wizard's panel channel. The bypass is per-address, the org-inbox row keeps
    # by=sender, and the grant rides the seat across retire/rehire.

    @t("a deep hire holding a handle answers it — no audience, no transport")
    def _():
        oo = load("auth")
        oo.hire("mid", "mid", "haiku", 0, "panelist",
                **spec(external_handles=["@mcp:panel.7"]))
        store.save_org(oo)
        reset_spies()
        supervisor.interorg_send = _spy_interorg_send
        try:
            st, j = agent("orgtree_message", "panelist",
                          {"to": "@mcp:panel.7", "body": "answer for the panel"})
        finally:
            supervisor.interorg_send = _real_interorg_send
        assert st == 200 and INTERORG == [], (st, j)
        row = [e for e in load("auth").d["org_inbox"]
               if e["dir"] == "out" and e["peer"] == "@mcp:panel.7"][-1]
        assert row["by"] == "panelist"

    @t("the handle bypass is PER-ADDRESS — a different @mcp: peer still refuses")
    def _():
        st, j = agent("orgtree_message", "panelist",
                      {"to": "@mcp:someone.else", "body": "x"})
        assert st == 422 and "audience holders" in j["detail"], (st, j)

    @t("_extern_scan surfaces the by attribution for panels to render")
    def _():
        rows = api._extern_scan("@mcp:panel.7", "auth", None)
        assert rows and rows[-1]["body"] == "answer for the panel"
        assert rows[-1].get("by") == "panelist", rows[-1]

    @t("hire validates handles: only @mcp:<peer> forms are grantable")
    def _():
        expect_error(lambda: load("auth").hire(
            "mid", "mid", "haiku", 0, "badpanel",
            **spec(external_handles=["@org:nbr"])), "external_handles")

    @t("the ops surface carries external_handles into the node doc")
    def _():
        st, j = call("POST", "/api/orgs/auth/ops",
                     {"op": "hire", "parent": "ceo", "tier": "haiku",
                      "name": "opspanel", "grant": 0,
                      "external_handles": ["@mcp:panel.ops"]})
        assert st == 200, (st, j)
        assert load("auth").node(j["node"]).get("external_handles") == \
            ["@mcp:panel.ops"], j

    @t("the handle rides the seat — retire → rehire keeps it answering")
    def _():
        oo = load("auth")
        oo.retire("mid", "panelist")
        oo.rehire("mid", "panelist", grant=0)
        store.save_org(oo)
        st, j = agent("orgtree_message", "panelist",
                      {"to": "@mcp:panel.7", "body": "still here"})
        assert st == 200, (st, j)

    @t("the identity prompt names the held handle so the agent knows the channel")
    def _():
        text = supervisor.identity_prompt(load("auth"), "panelist")
        assert "EXTERNAL RESPONSE HANDLE" in text and "@mcp:panel.7" in text

    @t("an outbound to an outside party drives NO node in this org")
    def _():
        assert [d for d in DRIVEN if d[0] == "auth"] == []

    @t("the outbound is recorded with `by` for internal attribution only")
    def _():
        e = [e for e in load("auth").d["org_inbox"]
             if e["dir"] == "out" and e["peer"] == "@mcp:poller"][-1]
        assert e["by"] == "ceo"

    @t("a nonexistent node cannot send at all")
    def _():
        st, j = agent("orgtree_message", "ghostnode", {"to": "@org:nbr", "body": "x"})
        assert st == 422 and "ghostnode" in j["detail"]

    @t("an @org: send to an unknown org warns rather than 500ing")
    def _():
        st, j = agent("orgtree_message", "ceo", {"to": "@org:ghostly", "body": "x"})
        assert st == 200 and any("not delivered" in w for w in j.get("warnings", [])), j

    @t("an @org: send to a KIOSK gets the same undifferentiated warning")
    def _():
        st, j = agent("orgtree_message", "ceo", {"to": "@org:authk", "body": "x"})
        w = " ".join(j.get("warnings", []))
        assert "no organization named 'authk'" in w and "kiosk" not in w, j


# ============================================================================ §11
def s11_failures():
    print("\n§11 failure paths")
    mkorg("fail", ("ceo",))

    @t("an org deleted mid-conversation 404s the peer's next send")
    def _():
        mkorg("vanish", ("ceo",))
        st, _ = call("POST", "/api/extern/vp/send", {"org": "vanish", "body": "one"})
        assert st == 200
        store.delete_org("vanish")
        st, j = call("POST", "/api/extern/vp/send", {"org": "vanish", "body": "two"})
        assert st == 404 and j["detail"] == "no organization named 'vanish'"

    @t("a deleted org's replies disappear from the peer's reads (no ghost mail)")
    def _():
        st, j = call("GET", "/api/extern/vp/messages")
        assert st == 200 and j["messages"] == []

    @t("a wait against a deleted org just times out")
    def _():
        st, j = call("GET", "/api/extern/vp/wait", query=b"org=vanish&timeout=1")
        assert st == 200 and j == {"messages": []}

    @t("a peer that never polls accumulates replies, capped at the 200-entry log")
    def _():
        o = mkorg("hoard", ("ceo",))
        for i in range(250):
            o.post_mail("ceo", "@mcp:never", f"r{i}")
        store.save_org(o)
        got = api._extern_scan("@mcp:never", "hoard", None)
        assert len(got) == 200 and got[-1]["body"] == "r249", (len(got), got[-1])

    @t("…and the oldest replies are the ones lost, not the newest")
    def _():
        got = api._extern_scan("@mcp:never", "hoard", None)
        assert got[0]["body"] == "r50"

    @t("an org doc that will not load is skipped by the scan, not fatal")
    def _():
        o = mkorg("corrupt", ("ceo",))
        o.post_mail("ceo", "@mcp:cp", "before corruption")
        store.save_org(o)
        assert len(api._extern_scan("@mcp:cp", None, None)) == 1
        p = store.org_path("corrupt")
        keep = open(p, "rb").read()
        with open(p, "wb") as f:
            f.write(b"{not json")
        try:
            st, j = call("GET", "/api/extern/cp/messages")
            assert st == 200 and j["messages"] == [], (st, j)
        finally:
            with open(p, "wb") as f:
                f.write(keep)

    @t("a lone unpaired surrogate in a body does not take the whole scan down")
    def _():
        o = load("fail")
        o.post_mail("ceo", "@mcp:sur", "clean reply")
        store.save_org(o)
        st, j = call("GET", "/api/extern/sur/messages")
        assert st == 200 and len(j["messages"]) == 1

    @t("concurrent deliveries from several threads lose no mail")
    def _():
        o = mkorg("race", ("ceo",))
        errs = []

        def one(i):
            try:
                supervisor.deliver_org_inbox("race", f"@mcp:t{i}", f"body {i}")
            except Exception as e:                                 # noqa: BLE001
                errs.append(e)
        ths = [threading.Thread(target=one, args=(i,)) for i in range(12)]
        for th in ths:
            th.start()
        for th in ths:
            th.join(30)
        assert not errs, errs
        got = {e["body"] for e in inbox("race", "in")}
        assert got == {f"body {i}" for i in range(12)}, sorted(got)

    @t("…and every one of them reached the recipient's mailbox")
    def _():
        assert len(mailbox("race", "ceo")) == 12

    @t("a body containing NUL bytes survives the round trip")
    def _():
        b = "before\x00after"
        supervisor.deliver_org_inbox("fail", "@mcp:nul", b)
        assert inbox("fail", "in", "@mcp:nul")[0]["body"] == b

    @t("a body of pure control characters does not break the gist")
    def _():
        supervisor.deliver_org_inbox("fail", "@mcp:ctl", "\r\n\x0b\x0c")
        e = [e for e in load("fail").d["events"] if e["op"] == "ext_mail"][-1]
        assert isinstance(e["detail"]["gist"], str)

    @t("a peer address that itself looks like a node id is still treated as outside")
    def _():
        o = mkorg("shadow", ("ceo",))
        o.post_external_mail("ceo", "not really outside")
        store.save_org(o)
        # `peer` is a bare string here: it is logged, never resolved as a node
        assert inbox("shadow", "in")[0]["peer"] == "ceo"
        assert len(o.d["mail"]["ceo"]) == 1

    @t("the extern endpoints never 500 on hostile query strings")
    def _():
        for q in (b"after=%00", b"after=" + b"z" * 5000, b"org=" + b"../" * 40,
                  b"after=NOT-A-DATE", b"timeout=abc"):
            st, _ = call("GET", "/api/extern/hostile/messages", query=q)
            assert st in (200, 422), (q, st)

    @t("a hostile `after` that sorts above everything simply returns nothing")
    def _():
        st, j = call("GET", "/api/extern/p1/messages", query=b"after=9999-99-99")
        assert st == 200 and j["messages"] == []

    @t("send is idempotent-free: two identical sends produce two entries")
    def _():
        n = len(inbox("fail", "in", "@mcp:dup"))
        for _ in range(2):
            call("POST", "/api/extern/dup/send", {"org": "fail", "body": "same"})
        assert len(inbox("fail", "in", "@mcp:dup")) == n + 2

    @t("the funnel survives a recipient whose scratch dir cannot be created")
    def _():
        o = mkorg("noscratch", ("ceo",))
        real = supervisor.scratch_dir
        supervisor.scratch_dir = lambda s, n: (_ for _ in ()).throw(
            OSError("no scratch here"))
        try:
            p = tmpfile("x.txt", "x")
            try:
                supervisor.deliver_org_inbox("noscratch", "@mcp:ns", "b",
                                             attachments=[p])
                raised = False
            except OSError:
                raised = True
        finally:
            supervisor.scratch_dir = real
        # current behaviour: scratch_dir() is called OUTSIDE the per-file try,
        # so an unavailable scratch root takes the whole delivery down
        assert raised, "if this flips, the funnel now degrades instead of raising"
        assert not inbox("noscratch", "in"), "nothing was delivered"
        note("§11 ⚑ deliver_org_inbox calls scratch_dir() outside its per-file "
             "try/except, so a scratch root that cannot be created (a disk-org "
             "whose \\\\wsl.localhost mount is down) makes an inbound message "
             "with attachments raise instead of degrading to a body-only "
             "delivery (supervisor.py ~2570).")


# ================================================================== the runner
def main():
    print("external-mail suite  ·  data root:", DATA)
    if QUICK:
        print("  (--quick: §9 externtool/uvicorn skipped)")
    s1_fixtures()
    s2_funnel()
    s3_orginbox_model()
    s4_kiosk()
    s5_interorg()
    s6_chatq()
    s7_attachments()
    s8_extern_http()
    if not (QUICK or HERMETIC):
        s9_externtool()
    else:
        print("\n§9 externtool.py — SKIPPED")
    s10_authorization()
    s11_failures()

    if NOTES:
        print("\nOPEN FINDINGS (pinned, not fixed here — they live outside this "
              "suite's writable files):")
        for n in NOTES:
            print("  ⚑ " + n)
    print(f"\nALL {PASS} CHECKS PASS")


if __name__ == "__main__":
    try:
        main()
    finally:
        supervisor.send_message = _real_send_message
        for d in SCRATCH:
            shutil.rmtree(d, ignore_errors=True)
