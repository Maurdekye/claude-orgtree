"""The mail hub (F-06 Phase B) — hub/mailhub/{app,db}.py, adversarially.

The hub is the one component of orgtree that is a NETWORK SERVICE: multi-writer,
reachable by anyone on the closed network, and holding everyone's mail in
plaintext. Its whole security model is two sentences — *joining is open, but
ADDRESSES are owned*, and *the direction of the connection is the boundary* —
so the tests that matter are the ones that attack the address ownership and the
custody rules, not the happy paths.

The sharpest of those is the SUFFIX ATTACK. An org's address ends in the first
6 characters of its fingerprint, and 6 hex characters is a 24-bit space that a
laptop collides in milliseconds (this suite does it in ~3,000 tries, below).
If verification ever compared that visible suffix instead of the full digest,
stealing an address would be seconds of work. §2 builds the collision and
proves the hub refuses it.

    §1  registration — first write wins, ownership, display refresh, slugs
    §2  ☞ the suffix attack, constructed for real
    §3  poll — multiplexing, partial credentials, ordering
    §4  custody — redelivery until ack, and only the recipient may ack
    §5  receipts — monotonic, one-sided, and the pushed lifecycle
    §6  send — idempotency, unknown recipient, truncation, caps
    §7  attachments — ownership, binding, download rights, size, blob paths
    §8  retention — the sweep removes row, metadata AND blob
    §9  the read-only UI and healthz

Hermetic: HUB_DATA points at a throwaway directory (set BEFORE the import, the
module reads it at import time), the app is driven in-process through
httpx.ASGITransport, and no socket is ever bound. ASGITransport does not run
the lifespan, which is deliberate: db.connect() creates the schema on every
call and the sweep is invoked directly, so nothing here depends on a background
task existing.

    python backend/tests/test_hub.py [-v]
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "hub"))

_TMP = tempfile.mkdtemp(prefix="orgtree-hub-")
os.environ["HUB_DATA"] = _TMP                    # BEFORE the import — db reads it
os.environ["HUB_NAME"] = "test-hub"
os.environ["HUB_RETENTION_DAYS"] = "30"

import httpx                                                     # noqa: E402
from mailhub import app as hubapp, db                            # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
VERBOSE = "-v" in sys.argv

# The hub logs one structured JSON line per request, which is right for a
# service and wrong for a test run — a few hundred of them bury the checks.
# Shadowing `print` in the app module's own globals silences exactly that
# module (Python resolves globals before builtins) and nothing else. `-v`
# keeps the log, which is what you want when a check is failing.
if not VERBOSE:
    hubapp.print = lambda *a, **k: None       # type: ignore[attr-defined]


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def gap(label, why, fn) -> None:
    """Inverted expectation — see test_rename.py. Keeps the suite green while
    the finding is printed every run, and turns RED when it is fixed."""
    global PASS
    try:
        fn()
    except AssertionError as e:
        GAPS.append((label, why, str(e).split("\n")[0][:300]))
        print(f"  ⚑ GAP    {label}")
        return
    except Exception:                                            # noqa: BLE001
        FAIL.append((label + " (gap check errored)", traceback.format_exc()))
        print(f"  FAIL     {label} — the gap check itself broke")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote out of gap()")


# ------------------------------------------------------------------ the wire
_transport = httpx.ASGITransport(app=hubapp.app)
# FR-10: the same app behind the PUBLIC wrapper — every request made through
# `preq` is one a remote client could make over the tunnel
from mailhub.public import PublicHub                             # noqa: E402

_public = PublicHub(hubapp.app)
_ptransport = httpx.ASGITransport(app=_public)   # type: ignore[arg-type]


def req(method: str, path: str, *, auth: str = "", json_body=None,
        content: bytes | None = None, params=None) -> httpx.Response:
    """One in-process request. `auth` is the raw X-Org-Auth header value, so a
    test can send a malformed or multi-org one deliberately."""
    async def go():
        async with httpx.AsyncClient(transport=_transport,
                                     base_url="http://hub") as c:
            return await c.request(
                method, path,
                headers={"x-org-auth": auth} if auth else None,
                json=json_body, content=content, params=params, timeout=30)
    return asyncio.run(go())


def preq(method: str, path: str, *, auth: str = "", json_body=None,
         params=None) -> httpx.Response:
    """One request through the PUBLIC listener (FR-10)."""
    async def go():
        async with httpx.AsyncClient(transport=_ptransport,
                                     base_url="http://hub") as c:
            return await c.request(
                method, path,
                headers={"x-org-auth": auth} if auth else None,
                json=json_body, params=params, timeout=30)
    return asyncio.run(go())


def pair(slug: str, secret: str) -> str:
    return f"{slug}:{secret}"


_n = [0]


def new_org(name: str = "", *, blurb: str = "") -> tuple[str, str]:
    """Register a fresh org; returns (slug, secret)."""
    _n[0] += 1
    slug = f"zz{_n[0]}.tester.{secrets.token_hex(3)}"
    secret = secrets.token_hex(16)
    r = req("POST", "/api/register", auth=pair(slug, secret),
            json_body={"slug": slug, "org_name": name or f"Org {_n[0]}",
                       "username": "tester", "blurb": blurb})
    assert r.status_code == 200, r.text
    return slug, secret


def send(frm: tuple[str, str], to: str, body: str = "hello",
         **extra) -> httpx.Response:
    payload = {"to": to, "body": body}
    payload.update(extra)
    return req("POST", "/api/send", auth=pair(*frm), json_body=payload)


def poll(*creds: tuple[str, str], wait: float = 0.0) -> dict:
    r = req("POST", "/api/poll", auth=" ".join(pair(*c) for c in creds),
            params={"wait": wait}, json_body={})
    assert r.status_code == 200, r.text
    return r.json()


def rows(sql: str, args=()) -> list:
    con = db.connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def backdate(sql: str, args=()) -> None:
    con = db.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def old_stamp(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)) \
        .isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ===================================================================== §1
def sec_register() -> None:
    print("\n§1  registration — the address is owned, not just claimed")

    def _first_write_wins():
        slug, secret = new_org("First Org")
        r = req("POST", "/api/register", auth=pair(slug, secrets.token_hex(16)),
                json_body={"slug": slug, "org_name": "Impostor"})
        assert r.status_code == 403 and "owned" in r.text, r.text
        assert rows("SELECT org_name FROM orgs WHERE slug=?",
                    (slug,))[0]["org_name"] == "First Org", \
            "the refused registration still rewrote the display fields"
    check("a second identity cannot take a registered address", _first_write_wins)

    def _reregister_refreshes():
        slug, secret = new_org("Before", blurb="old blurb")
        r = req("POST", "/api/register", auth=pair(slug, secret),
                json_body={"slug": slug, "org_name": "After",
                           "username": "renamed", "blurb": "new blurb"})
        assert r.status_code == 200, r.text
        got = rows("SELECT * FROM orgs WHERE slug=?", (slug,))[0]
        assert (got["org_name"], got["username"], got["blurb"]) \
            == ("After", "renamed", "new blurb"), got
        assert got["fingerprint"] == hashlib.sha256(secret.encode()).hexdigest()
    check("re-registering with the right secret refreshes the display fields",
          _reregister_refreshes)

    def _response_shape():
        slug, secret = new_org()
        r = req("POST", "/api/register", auth=pair(slug, secret),
                json_body={"slug": slug})
        j = r.json()
        assert j["ok"] and j["name"] == "test-hub" and j["retention_days"] == 30
        assert any(o["slug"] == slug for o in j["roster"]), j["roster"]
        me = next(o for o in j["roster"] if o["slug"] == slug)
        assert me["online"] is True, "a just-registered org is present"
    check("registration answers with the hub's name, retention and roster",
          _response_shape)

    def _no_secret():
        slug = "zz.nobody.aaaaaa"
        r = req("POST", "/api/register", json_body={"slug": slug})
        assert r.status_code == 401, r.text
        assert not rows("SELECT 1 FROM orgs WHERE slug=?", (slug,))
    check("registration without the auth header is refused", _no_secret)

    def _header_for_another_slug():
        # the header may carry several pairs; only the one matching the body's
        # slug counts, or an attacker could register X while presenting their
        # own credential for Y
        victim = "zz.victim.aaaaaa"
        mine, sec = new_org()
        r = req("POST", "/api/register", auth=pair(mine, sec),
                json_body={"slug": victim})
        assert r.status_code == 401, r.text
        assert not rows("SELECT 1 FROM orgs WHERE slug=?", (victim,))
    check("a credential for a DIFFERENT slug does not register this one",
          _header_for_another_slug)

    def _malformed():
        for bad in ("", "   ", "UPPER.case.aaaaaa", ".leading.dot",
                    "-leading-dash", "has space", "a" * 129):
            r = req("POST", "/api/register", auth=pair(bad or "x", "s" * 32),
                    json_body={"slug": bad})
            assert r.status_code in (401, 422), f"{bad!r} → {r.status_code}"
        # ⚠ a non-ASCII slug cannot even be presented: the credential rides an
        # HTTP HEADER, which is latin-1 at best, so the header carries an
        # ASCII stand-in and the body's slug is what is judged
        r = req("POST", "/api/register", auth=pair("x", "s" * 32),
                json_body={"slug": "emoji.\U0001f600.aaaaaa"})
        assert r.status_code in (401, 422), r.status_code
        assert not rows("SELECT 1 FROM orgs WHERE slug LIKE 'emoji%'")
    check("malformed slugs are refused", _malformed)

    def _long_slug_boundary():
        ok = "a" * 128
        r = req("POST", "/api/register", auth=pair(ok, secrets.token_hex(16)),
                json_body={"slug": ok})
        assert r.status_code == 200, r.text
    check("a 128-character slug is still legal (the boundary itself)",
          _long_slug_boundary)


# ===================================================================== §2
def sec_suffix_attack() -> None:
    print("\n§2  ☞ the suffix attack — constructed, not imagined")

    def _collide(prefix_len: int = 6) -> tuple[str, str]:
        """Two secrets whose fingerprints share their first `prefix_len` hex
        characters — i.e. two identities with the SAME visible address suffix.
        Birthday-bounded: ~2^12 tries for 24 bits, milliseconds in practice."""
        seen: dict[str, str] = {}
        for _ in range(400_000):
            s = secrets.token_hex(16)
            p = hashlib.sha256(s.encode()).hexdigest()[:prefix_len]
            if p in seen and seen[p] != s:
                return seen[p], s
            seen[p] = s
        raise AssertionError("no collision found — the search bound is wrong")

    def _attack():
        a, b = _collide()
        fa = hashlib.sha256(a.encode()).hexdigest()
        fb = hashlib.sha256(b.encode()).hexdigest()
        assert fa[:6] == fb[:6] and fa != fb, "the fixture did not collide"
        slug = f"zzattack.tester.{fa[:6]}"
        r = req("POST", "/api/register", auth=pair(slug, a),
                json_body={"slug": slug, "org_name": "Holder"})
        assert r.status_code == 200, r.text
        # the attacker's secret produces the SAME 6-character display suffix
        r = req("POST", "/api/register", auth=pair(slug, b),
                json_body={"slug": slug, "org_name": "Thief"})
        assert r.status_code == 403, (
            "AN ADDRESS WAS STOLEN with a 6-character fingerprint collision — "
            "verification must compare the FULL digest")
        assert rows("SELECT org_name FROM orgs WHERE slug=?",
                    (slug,))[0]["org_name"] == "Holder"
        # …and it must not authenticate anywhere else either
        for path, kw in (("/api/poll", {"json_body": {}}),
                         ("/api/ack", {"json_body": {"ids": []}}),
                         ("/api/receipts", {"json_body": {"receipts": []}}),
                         ("/api/roster", {})):
            method = "GET" if path == "/api/roster" else "POST"
            got = req(method, path, auth=pair(slug, b), **kw)
            assert got.status_code == 401, f"{path} accepted the collision: {got}"
    check("a fingerprint that shares the 6-char SUFFIX is still refused",
          _attack)

    def _full_digest_compare():
        # the same property stated at the unit: a truncated compare would pass
        slug, secret = new_org()
        fp = hashlib.sha256(secret.encode()).hexdigest()
        for wrong in (fp, fp[:6], secret[:-1], secret + "0", secret.upper()):
            r = req("GET", "/api/roster", auth=pair(slug, wrong))
            assert r.status_code == 401, \
                f"{wrong[:12]}… authenticated as {slug}"
    check("neither the fingerprint itself nor a prefix works as the secret",
          _full_digest_compare)


# ===================================================================== §3
def sec_poll() -> None:
    print("\n§3  poll — multiplexed, partial-credential tolerant, ordered")

    def _multiplex():
        a, b = new_org(), new_org()
        c = new_org()
        send(c, a[0], "for a")
        send(c, b[0], "for b")
        got = poll(a, b)
        tos = sorted(m["to"] for m in got["messages"])
        assert tos == sorted([a[0], b[0]]), got["messages"]
    check("one header, two orgs, both queues in one answer", _multiplex)

    def _partial_credentials():
        a = new_org()
        bad_slug = ("zz.nosuch.aaaaaa", secrets.token_hex(16))
        c = new_org()
        send(c, a[0], "still delivered")
        got = poll(a, bad_slug)
        assert [m["body"] for m in got["messages"]] == ["still delivered"], got
        # a KNOWN slug with the WRONG secret is the same case
        wrong = (c[0], secrets.token_hex(16))
        got2 = poll(a, wrong)
        assert got2["messages"] == [] or all(m["to"] == a[0]
                                             for m in got2["messages"])
    check("an invalid pair does not spoil the valid one", _partial_credentials)

    def _all_invalid():
        r = req("POST", "/api/poll", auth=pair("zz.nope.aaaaaa", "x" * 32),
                params={"wait": 0}, json_body={})
        assert r.status_code == 401, r.text
        r = req("POST", "/api/poll", params={"wait": 0}, json_body={})
        assert r.status_code == 401, r.text
    check("no valid credential at all is a 401", _all_invalid)

    def _ordering():
        me, sender = new_org(), new_org()
        for i in range(5):
            send(sender, me[0], f"m{i}")
        got = poll(me)
        assert [m["body"] for m in got["messages"]] == [f"m{i}" for i in range(5)], \
            "received_at (the hub clock) is the ordering authority"
    check("messages come back in hub-clock order", _ordering)

    def _envelope_shape():
        me, sender = new_org(), new_org()
        send(sender, me[0], "shaped", kind="status", sent_at="2020-01-01T00:00:00Z")
        m = poll(me)["messages"][0]
        assert set(m) == {"id", "from", "to", "body", "kind", "thread_id",
                          "sent_at", "received_at", "attachments"}, sorted(m)
        assert m["from"] == sender[0] and m["kind"] == "status"
        assert m["sent_at"] == "2020-01-01T00:00:00Z" != m["received_at"], (
            "sent_at is the sender's CLAIM and must not overwrite the hub's "
            "own received_at")
    check("the envelope carries the sender's claim and the hub's own clock",
          _envelope_shape)

    def _roster_rides_along():
        me = new_org()
        got = poll(me)
        assert any(o["slug"] == me[0] for o in got["roster"])
        assert got["name"] == "test-hub"
    check("every poll carries the roster and the hub's name", _roster_rides_along)


# ===================================================================== §4
def sec_custody() -> None:
    print("\n§4  custody — at-least-once until the recipient acks")

    def _redelivery():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0], "keep me").json()["id"]
        for attempt in range(3):
            got = poll(me)
            assert [m["id"] for m in got["messages"]] == [mid], (
                f"attempt {attempt}: an un-acked message must be redelivered "
                f"— the recipient may have died before persisting it")
        r = req("POST", "/api/ack", auth=pair(*me), json_body={"ids": [mid]})
        assert r.json()["acked"] == 1, r.text
        assert poll(me)["messages"] == [], "an acked message stops coming back"
    check("a message is redelivered until it is acked", _redelivery)

    def _ack_only_by_recipient():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0], "not yours to ack").json()["id"]
        r = req("POST", "/api/ack", auth=pair(*sender), json_body={"ids": [mid]})
        assert r.json()["acked"] == 0, "the SENDER acked its own message"
        assert [m["id"] for m in poll(me)["messages"]] == [mid], \
            "…and the recipient still has it"
        third = new_org()
        r = req("POST", "/api/ack", auth=pair(*third), json_body={"ids": [mid]})
        assert r.json()["acked"] == 0, "a third party acked someone's message"
    check("only the recipient may take custody", _ack_only_by_recipient)

    def _ack_unknown_and_double():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0]).json()["id"]
        assert req("POST", "/api/ack", auth=pair(*me),
                   json_body={"ids": ["no-such-id"]}).json()["acked"] == 0
        assert req("POST", "/api/ack", auth=pair(*me),
                   json_body={"ids": [mid]}).json()["acked"] == 1
        assert req("POST", "/api/ack", auth=pair(*me),
                   json_body={"ids": [mid]}).json()["acked"] == 0, \
            "a second ack of the same message must be a no-op"
    check("acking an unknown id, or the same id twice, changes nothing",
          _ack_unknown_and_double)


# ===================================================================== §5
def sec_receipts() -> None:
    print("\n§5  receipts — one-sided, monotonic, pushed once")

    def _lifecycle():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0], "track me").json()["id"]
        assert poll(sender)["receipts"] == [], "nothing owed before an ack"
        req("POST", "/api/ack", auth=pair(*me), json_body={"ids": [mid]})
        got = poll(sender)["receipts"]
        assert [r["id"] for r in got] == [mid] and got[0]["state"] == "fetched", got
        assert poll(sender)["receipts"] == [], \
            "a receipt already pushed must not repeat forever"
        req("POST", "/api/receipts", auth=pair(*me),
            json_body={"receipts": [{"id": mid, "state": "delivered"}]})
        got = poll(sender)["receipts"]
        assert got[0]["state"] == "delivered" and got[0]["delivered_at"], got
        req("POST", "/api/receipts", auth=pair(*me),
            json_body={"receipts": [{"id": mid, "state": "read"}]})
        got = poll(sender)["receipts"]
        assert got[0]["state"] == "read" and got[0]["read_at"], got
    check("fetched → delivered → read, each pushed exactly once", _lifecycle)

    def _monotonic():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0]).json()["id"]
        req("POST", "/api/ack", auth=pair(*me), json_body={"ids": [mid]})
        first = req("POST", "/api/receipts", auth=pair(*me),
                    json_body={"receipts": [{"id": mid, "state": "delivered",
                                             "at": "2026-01-01T00:00:00Z"}]})
        assert first.json()["recorded"] == 1
        again = req("POST", "/api/receipts", auth=pair(*me),
                    json_body={"receipts": [{"id": mid, "state": "delivered",
                                             "at": "2030-01-01T00:00:00Z"}]})
        assert again.json()["recorded"] == 0, "a receipt moved backwards"
        got = rows("SELECT delivered_at FROM messages WHERE id=?", (mid,))[0]
        assert got["delivered_at"] == "2026-01-01T00:00:00Z", got
    check("a state already recorded is never overwritten", _monotonic)

    def _wrong_side():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0]).json()["id"]
        r = req("POST", "/api/receipts", auth=pair(*sender),
                json_body={"receipts": [{"id": mid, "state": "read"}]})
        assert r.json()["recorded"] == 0, \
            "the SENDER recorded a read receipt for its own message"
        third = new_org()
        r = req("POST", "/api/receipts", auth=pair(*third),
                json_body={"receipts": [{"id": mid, "state": "read"}]})
        assert r.json()["recorded"] == 0, "a third party recorded a receipt"
        assert rows("SELECT read_at FROM messages WHERE id=?",
                    (mid,))[0]["read_at"] is None
    check("only the recipient's side may record delivery or reading",
          _wrong_side)

    def _bad_states():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0]).json()["id"]
        r = req("POST", "/api/receipts", auth=pair(*me),
                json_body={"receipts": [{"id": mid, "state": "received"},
                                        {"id": mid, "state": ""},
                                        {"id": mid, "state": "DELIVERED"},
                                        {"id": mid}]})
        assert r.json()["recorded"] == 0, \
            "only 'delivered' and 'read' are recordable states"
    check("unknown receipt states are ignored, not guessed at", _bad_states)

    def _pushed_flag():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0]).json()["id"]
        assert rows("SELECT receipts_pushed FROM messages WHERE id=?",
                    (mid,))[0]["receipts_pushed"] == 1, "nothing owed at send"
        req("POST", "/api/ack", auth=pair(*me), json_body={"ids": [mid]})
        assert rows("SELECT receipts_pushed FROM messages WHERE id=?",
                    (mid,))[0]["receipts_pushed"] == 0, "the ack owes an update"
        poll(sender)
        assert rows("SELECT receipts_pushed FROM messages WHERE id=?",
                    (mid,))[0]["receipts_pushed"] == 1, "the poll settled it"
    check("receipts_pushed is set by a state change and cleared by the poll",
          _pushed_flag)


# ===================================================================== §6
def sec_send() -> None:
    print("\n§6  send — idempotent, addressed, bounded")

    def _idempotent():
        me, sender = new_org(), new_org()
        mid = "fixed-id-" + secrets.token_hex(4)
        first = send(sender, me[0], "once", id=mid).json()
        again = send(sender, me[0], "DIFFERENT BODY", id=mid).json()
        assert first["duplicate"] is False and again["duplicate"] is True
        assert again["received_at"] == first["received_at"], (
            "a retry must report the ORIGINAL receipt time — the sender uses "
            "it to order its own outbox")
        assert len(rows("SELECT id FROM messages WHERE id=?", (mid,))) == 1
        assert rows("SELECT body FROM messages WHERE id=?",
                    (mid,))[0]["body"] == "once", "a retry rewrote the body"
    check("re-sending the same id is idempotent and keeps the first receipt",
          _idempotent)

    def _unknown_recipient():
        sender = new_org()
        r = send(sender, "zz.nobody.ffffff")
        assert r.status_code == 422 and "no org registered" in r.text, r.text
    check("mail to an unregistered address is refused at the door",
          _unknown_recipient)

    def _sender_must_own_the_from():
        me, sender = new_org(), new_org()
        victim = new_org()
        r = req("POST", "/api/send", auth=pair(*sender),
                json_body={"to": me[0], "from": victim[0], "body": "forged"})
        assert r.status_code == 401, "an org sent mail AS another org"
    check("the `from` must be one of the presented credentials",
          _sender_must_own_the_from)

    def _no_credentials():
        me = new_org()
        r = req("POST", "/api/send", json_body={"to": me[0], "body": "x"})
        assert r.status_code == 401, r.text
    check("sending with no credentials is refused", _no_credentials)

    def _body_truncated():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0], "x" * 25000).json()["id"]
        got = rows("SELECT body FROM messages WHERE id=?", (mid,))[0]["body"]
        assert len(got) == hubapp.BODY_MAX == 20000, len(got)
    check(f"the body is truncated at {hubapp.BODY_MAX}", _body_truncated)

    def _attachment_cap():
        me, sender = new_org(), new_org()
        r = req("POST", "/api/send", auth=pair(*sender),
                json_body={"to": me[0], "body": "many",
                           "attachments": [f"a{i}" for i in range(11)]})
        assert r.status_code == 422 and "at most" in r.text, r.text
    check(f"at most {hubapp.MAX_FILES_PER_MESSAGE} attachments per message",
          _attachment_cap)


# ===================================================================== §7
def sec_attachments() -> None:
    print("\n§7  attachments — owned, bound once, downloadable by two parties")

    def upload(who, data=b"payload", name="notes.txt"):
        r = req("POST", "/api/attachments", auth=pair(*who), content=data,
                params={"name": name})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def _upload_writes_a_blob():
        who = new_org()
        aid = upload(who, b"the bytes")
        p = db.blob_path(aid)
        assert os.path.isfile(p) and open(p, "rb").read() == b"the bytes"
        row = rows("SELECT * FROM attachments WHERE id=?", (aid,))[0]
        assert row["owner_slug"] == who[0] and row["bytes"] == 9
        assert row["message_id"] is None, "an upload is not yet bound"
    check("an upload lands as a real file plus an unbound row",
          _upload_writes_a_blob)

    def _name_is_basenamed():
        who = new_org()
        aid = upload(who, b"x", name="../../etc/passwd")
        assert rows("SELECT name FROM attachments WHERE id=?",
                    (aid,))[0]["name"] == "passwd"
    check("the declared filename is basenamed", _name_is_basenamed)

    def _blob_path_strips_traversal():
        root = os.path.realpath(db.BLOB_DIR)
        for evil in ("../../etc/passwd", "..\\..\\win.ini", "a/b", "..",
                     "%2e%2e", "....//....//x"):
            p = os.path.realpath(db.blob_path(evil))
            assert p == root or p.startswith(root + os.sep), \
                f"blob_path({evil!r}) escaped the blob directory: {p}"
    check("blob_path can never leave the blob directory",
          _blob_path_strips_traversal)

    def _blob_path_degenerate():
        # an id whose characters are ALL stripped leaves nothing to join, and
        # os.path.join(BLOB_DIR, "") is the blob directory itself
        for empty_ish in ("..", "///", "%%%", "-", "."):
            p = os.path.realpath(db.blob_path(empty_ish))
            assert p != os.path.realpath(db.BLOB_DIR), (
                f"blob_path({empty_ish!r}) resolved to the blob DIRECTORY "
                f"itself — a caller writing to it would target the directory, "
                f"and the sweep's os.remove() would aim at it too")
    gap("an id that sanitizes to nothing does not resolve to the directory",
        "db.blob_path() keeps only alphanumerics and joins the remainder, so "
        "an id made entirely of stripped characters yields BLOB_DIR itself "
        "rather than a file under it. Unreachable today (ids are server-minted "
        "uuid4().hex), and the docstring calls the filter belt-and-braces — "
        "but the belt has a hole exactly where the braces were supposed to "
        "matter. Raising on an empty result is one line, and it also protects "
        "the sweep's os.remove().",
        _blob_path_degenerate)

    def _binding_and_ownership():
        me, sender = new_org(), new_org()
        mine = upload(sender)
        theirs = upload(me)
        r = req("POST", "/api/send", auth=pair(*sender),
                json_body={"to": me[0], "body": "not mine",
                           "attachments": [theirs]})
        assert r.status_code == 422 and "unknown attachment" in r.text, (
            "an org attached a file it does not own")
        ok = send(sender, me[0], "mine", attachments=[mine])
        assert ok.status_code == 200, ok.text
        assert rows("SELECT message_id FROM attachments WHERE id=?",
                    (mine,))[0]["message_id"] == ok.json()["id"]
        r2 = send(sender, me[0], "again", attachments=[mine])
        assert r2.status_code == 422 and "already bound" in r2.text, (
            "the same upload was bound to a second message")
    check("an attachment is owned by its uploader and binds to ONE message",
          _binding_and_ownership)

    def _download_rights():
        me, sender = new_org(), new_org()
        third = new_org()
        aid = upload(sender, b"secret bytes")
        assert req("GET", f"/api/attachments/{aid}",
                   auth=pair(*sender)).status_code == 200, "the owner may read"
        assert req("GET", f"/api/attachments/{aid}",
                   auth=pair(*me)).status_code == 403, \
            "an unbound attachment is readable by a stranger"
        send(sender, me[0], "here", attachments=[aid])
        got = req("GET", f"/api/attachments/{aid}", auth=pair(*me))
        assert got.status_code == 200 and got.content == b"secret bytes", \
            "the recipient of the bound message may read it"
        assert req("GET", f"/api/attachments/{aid}",
                   auth=pair(*third)).status_code == 403, \
            "a third party read someone else's attachment"
        assert req("GET", f"/api/attachments/{aid}").status_code == 401
    check("owner and recipient may download; nobody else", _download_rights)

    def _unknown_and_missing_blob():
        who = new_org()
        assert req("GET", "/api/attachments/nosuchid",
                   auth=pair(*who)).status_code == 404
        aid = upload(who)
        os.remove(db.blob_path(aid))
        r = req("GET", f"/api/attachments/{aid}", auth=pair(*who))
        assert r.status_code == 410, (r.status_code, r.text)
    check("a missing row is 404; a swept blob is 410, not a crash",
          _unknown_and_missing_blob)

    def _oversize():
        who = new_org()
        big = b"\0" * (hubapp.MAX_FILE_BYTES + 1)
        r = req("POST", "/api/attachments", auth=pair(*who), content=big,
                params={"name": "big.bin"})
        assert r.status_code == 413, r.status_code
        assert not rows("SELECT 1 FROM attachments WHERE bytes>?",
                        (hubapp.MAX_FILE_BYTES,)), "an oversize row was written"
    check(f"an upload over {hubapp.MAX_FILE_BYTES // 1048576} MB is refused",
          _oversize)

    def _upload_needs_credentials():
        r = req("POST", "/api/attachments", content=b"x", params={"name": "f"})
        assert r.status_code == 401, r.text
    check("uploading without credentials is refused", _upload_needs_credentials)


# ===================================================================== §8
def sec_retention() -> None:
    print("\n§8  retention — the sweep takes row, metadata and blob together")

    def run_sweep():
        """The real `_sweep_loop` body, once: its tail sleeps for an hour, so
        the sleep is what ends the iteration here."""
        real_sleep = asyncio.sleep

        async def stop(_s):
            raise asyncio.CancelledError

        asyncio.sleep = stop                                     # type: ignore[assignment]
        try:
            asyncio.run(hubapp._sweep_loop())
        except asyncio.CancelledError:
            pass
        finally:
            asyncio.sleep = real_sleep                           # type: ignore[assignment]

    def _sweeps_old_and_keeps_fresh():
        me, sender = new_org(), new_org()
        old_id = send(sender, me[0], "ancient").json()["id"]
        aid = req("POST", "/api/attachments", auth=pair(*sender),
                  content=b"old blob", params={"name": "old.bin"}).json()["id"]
        fresh_id = send(sender, me[0], "recent").json()["id"]
        stamp = old_stamp(hubapp.RETENTION_DAYS + 1)
        backdate("UPDATE messages SET received_at=? WHERE id=?", (stamp, old_id))
        backdate("UPDATE attachments SET created_at=? WHERE id=?", (stamp, aid))
        blob = db.blob_path(aid)
        assert os.path.isfile(blob)

        run_sweep()

        assert not rows("SELECT 1 FROM messages WHERE id=?", (old_id,)), \
            "the aged message survived the sweep"
        assert not rows("SELECT 1 FROM attachments WHERE id=?", (aid,)), \
            "the aged attachment row survived"
        assert not os.path.exists(blob), (
            "the BLOB FILE survived — the row is gone but the bytes are still "
            "on disk, which is the failure mode a retention promise cannot have")
        assert rows("SELECT 1 FROM messages WHERE id=?", (fresh_id,)), \
            "the sweep took a message that was still within retention"
    check("aged rows, metadata and blobs all go; fresh ones stay",
          _sweeps_old_and_keeps_fresh)

    def _boundary():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0], "just inside").json()["id"]
        backdate("UPDATE messages SET received_at=? WHERE id=?",
                 (old_stamp(hubapp.RETENTION_DAYS - 1), mid))
        run_sweep()
        assert rows("SELECT 1 FROM messages WHERE id=?", (mid,)), \
            "a message one day INSIDE the window was swept"
    check("the cutoff is the retention window, not a day either side",
          _boundary)


# ===================================================================== §9
def sec_ui() -> None:
    print("\n§9  the read-only UI and healthz")

    def _healthz():
        j = req("GET", "/healthz").json()
        assert j["ok"] and j["name"] == "test-hub" and j["retention_days"] == 30
        before = j["queued"]
        me, sender = new_org(), new_org()
        send(sender, me[0], "counted")
        after = req("GET", "/healthz").json()
        assert after["queued"] == before + 1, (before, after)
        assert after["orgs"] >= 2
    check("healthz counts orgs and the queue, with no credentials", _healthz)

    def _ui_data():
        me, sender = new_org(), new_org()
        send(sender, me[0], "for the panel")
        j = req("GET", "/ui/data").json()
        assert j["name"] == "test-hub" and j["retention_days"] == 30
        row = next(o for o in j["orgs"] if o["slug"] == me[0])
        assert row["queued"] >= 1 and set(row) >= {
            "slug", "org_name", "username", "blurb", "online", "last_seen",
            "queued"}, row
    check("/ui/data lists every org with its queue depth", _ui_data)

    def _ui_messages():
        me, sender = new_org(), new_org()
        mid = send(sender, me[0], "visible to the operator").json()["id"]
        all_msgs = req("GET", "/ui/messages").json()["messages"]
        assert any(m["id"] == mid for m in all_msgs)
        mine = req("GET", "/ui/messages", params={"org": me[0]}).json()["messages"]
        assert all(m["to"] == me[0] or m["from"] == me[0] for m in mine)
        assert any(m["id"] == mid for m in mine)
        other = req("GET", "/ui/messages",
                    params={"org": "zz.nobody.zzzzzz"}).json()["messages"]
        assert other == []
        m = next(m for m in all_msgs if m["id"] == mid)
        assert m["state"] == "queued" and "delivered_at" in m and "read_at" in m
    check("/ui/messages shows the global view and filters by org", _ui_messages)

    def _ui_limit_clamped():
        assert len(req("GET", "/ui/messages",
                       params={"limit": 1}).json()["messages"]) == 1
        for bad in (0, -5, 10000):
            r = req("GET", "/ui/messages", params={"limit": bad})
            assert r.status_code == 200 and \
                1 <= len(r.json()["messages"]) <= 500, (bad, r.status_code)
    check("the UI limit is clamped to 1..500", _ui_limit_clamped)

    def _index_served():
        static = os.path.join(os.path.dirname(os.path.abspath(hubapp.__file__)),
                              "static", "index.html")
        if not os.path.isfile(static):
            raise AssertionError(f"the UI's index.html is missing: {static}")
        r = req("GET", "/")
        assert r.status_code == 200 and "<" in r.text
    check("the index page is served from the packaged static file",
          _index_served)

    def _ui_is_unauthenticated_by_design():
        # asserted so that making it authenticated is a DELIBERATE change: the
        # ruling is that on a closed network hub access IS read access to
        # everyone's mail (app.py docstring, spec §10.1)
        for p in ("/healthz", "/ui/data", "/ui/messages"):
            assert req("GET", p).status_code == 200, p
    check("the operator UI needs no credentials (ruled, not accidental)",
          _ui_is_unauthenticated_by_design)



# ==================================================================== §10
def sec_public_face() -> None:
    """FR-10 — the public listener is a ROUTE SPLIT, and a split is only worth
    what its blocked half proves. The hub's UI is an unauthenticated view of
    EVERYONE's mail, so the question is not 'do the api routes work through
    the wrapper' but 'is there any way at all to reach /ui or / from
    outside'."""
    print("\n§10  FR-10 the public face — what the tunnel exposes")

    def _ui_is_gone():
        for path in ("/", "/ui/data", "/ui/messages"):
            r = preq("GET", path)
            assert r.status_code == 404, f"{path} → {r.status_code}"
        # and the same paths ARE served on the private listener, so the 404s
        # above are the wrapper's doing and not a missing route
        assert req("GET", "/ui/messages").status_code == 200
    check("public · /, /ui/data and /ui/messages 404 through the public "
          "listener while the private one still serves them", _ui_is_gone)

    def _api_and_health_pass():
        me = new_org()
        r = preq("GET", "/api/roster", auth=pair(*me))
        assert r.status_code == 200 and r.json()["roster"], r.text
        assert preq("GET", "/healthz").status_code == 200
    check("public · /api/* and /healthz still work through it", _api_and_health_pass)

    def _every_api_route_still_needs_credentials():
        # the split's OTHER half: everything it lets through must gate itself
        probes = [("POST", "/api/register", {"slug": "x.y.z"}),
                  ("POST", "/api/poll", {}),
                  ("POST", "/api/ack", {"ids": []}),
                  ("POST", "/api/send", {"to": "x", "body": "y"}),
                  ("POST", "/api/receipts", {"ids": []}),
                  ("GET", "/api/roster", None),
                  ("GET", "/api/attachments/deadbeef", None)]
        for method, path, body in probes:
            r = preq(method, path, json_body=body)
            assert r.status_code in (401, 403), (
                f"{method} {path} answered {r.status_code} with NO "
                f"credentials through the public face")
    check("public · every route the wrapper admits refuses an unauthenticated "
          "caller (401/403) — enumerated, not assumed",
          _every_api_route_still_needs_credentials)

    def _path_tricks_do_not_reach_the_ui():
        tricks = ["//ui/messages", "/api/../ui/messages", "/API/roster",
                  "/UI/messages", "/healthz/../ui/messages", "/ui//messages",
                  "/./ui/messages", "/healthz/", "/api", "/apiX/roster"]
        for p in tricks:
            r = preq("GET", p)
            assert r.status_code == 404, f"{p} → {r.status_code}"
            assert b"messages" not in r.content[:200], p
    check("public · dot-segments, double slashes, case and prefix tricks all "
          "404 without reaching the UI", _path_tricks_do_not_reach_the_ui)

    def _attachment_rights_hold_through_the_public_face():
        owner, other, rcpt = new_org(), new_org(), new_org()
        up = req("POST", "/api/attachments", auth=pair(*owner),
                 content=b"secret bytes", params={"name": "plan.md"})
        aid = up.json()["id"]
        send(owner, rcpt[0], "with a file", attachments=[{"id": aid,
                                                          "name": "plan.md"}])
        assert preq("GET", f"/api/attachments/{aid}",
                    auth=pair(*owner)).status_code == 200
        assert preq("GET", f"/api/attachments/{aid}",
                    auth=pair(*other)).status_code == 403, "a stranger read it"
    check("public · attachment custody (owner or recipient only) is unchanged "
          "through the wrapper", _attachment_rights_hold_through_the_public_face)

    def _non_http_scopes_are_rejected():
        # was a ⚑ GAP — fixed 2026-08-05: PublicHub now admits ONLY http and
        # lifespan scopes to the inner app; a websocket handshake is closed
        # (1008) and any unknown future scope type is dropped. Driven
        # behaviorally against a counting stub, not read from the source: a
        # future live-feed route must be admitted here DELIBERATELY, and
        # this check names the day that conversation has to happen.
        ws = [r for r in hubapp.app.routes
              if type(r).__name__ == "WebSocketRoute"]
        assert not ws, (
            f"a websocket route now exists: {ws} — decide its public "
            f"exposure deliberately (PublicHub currently refuses it)")
        reached: list[str] = []

        async def inner(scope, receive, send):
            reached.append(str(scope.get("type")))

        pub = PublicHub(inner)
        sent: list[dict] = []

        async def _send(msg):
            sent.append(msg)

        async def _recv():
            return {"type": "websocket.connect"}

        async def drive():
            await pub({"type": "websocket", "path": "/api/poll"}, _recv, _send)
            await pub({"type": "asgi.future.type", "path": "/api/poll"},
                      _recv, _send)
            await pub({"type": "lifespan"}, _recv, _send)
        asyncio.run(drive())
        assert reached == ["lifespan"], (
            f"a non-HTTP scope reached the inner app: {reached}")
        assert any(m.get("type") == "websocket.close" for m in sent), (
            "the websocket handshake was dropped without a close frame")
    check("public · non-HTTP scopes never reach the inner app (websocket "
          "closed, unknown types dropped, lifespan admitted)",
          _non_http_scopes_are_rejected)


# ==================================================================== §11
def sec_client_filter() -> None:
    """The 2026-08-05 UI wave added /ui/messages?client=<username> — the group
    header on the operator's list pane. Both of its neighbours bound the read
    in SQL; this one does not, and it is the one whose job is NARROWING."""
    print("\n§11  the client group filter (UI wave 2026-08-05)")

    def _filters_by_client_segment():
        me, sender = new_org(), new_org()
        send(sender, me[0], "for the tester group")
        j = req("GET", "/ui/messages", params={"client": "tester"}).json()
        assert any(m["body"] == "for the tester group" for m in j["messages"])
        none = req("GET", "/ui/messages", params={"client": "nobody"}).json()
        assert none["messages"] == [], none
    check("client · the group filter matches the username segment and "
          "excludes everything else", _filters_by_client_segment)

    def _substring_does_not_match():
        # the docstring's own promise: an exact segment match, not a LIKE
        j = req("GET", "/ui/messages", params={"client": "test"}).json()
        assert j["messages"] == [], (
            "a partial username matched — the filter is a substring after "
            "all, so one client's group header can show another's mail")
    check("client · a partial username matches nothing (exact segment, not a "
          "LIKE)", _substring_does_not_match)

    def _the_narrowing_read_is_bounded():
        """MEASURED, not read: wrap the connection the endpoint gets and keep
        every statement it runs, then check the one that serves the client
        filter carries a LIMIT."""
        for i in range(30):
            me, sender = new_org(), new_org()
            send(sender, me[0], f"bulk {i}")
        stmts: list[str] = []
        real = db.connect

        class Recording:
            def __init__(self, con): self._c = con

            def execute(self, sql, args=()):
                stmts.append(" ".join(str(sql).split()))
                return self._c.execute(sql, args)

            def __getattr__(self, n):
                return getattr(self._c, n)

        db.connect = lambda: Recording(real())          # type: ignore[assignment]
        try:
            r = req("GET", "/ui/messages",
                    params={"client": "tester", "limit": 5})
        finally:
            db.connect = real                           # type: ignore[assignment]
        assert r.status_code == 200 and len(r.json()["messages"]) == 5
        selects = [x for x in stmts if x.upper().startswith("SELECT * FROM MESSAGES")]
        assert selects, stmts
        assert all("LIMIT" in x.upper() for x in selects), (
            "the client filter runs an unbounded SELECT and slices in "
            f"Python: {selects[-1]!r}")
    # promoted from gap() 2026-08-05, fixed same day: the client branch now
    # pages a parameterised LIKE prefilter (LIMIT ? OFFSET ?) and keeps the
    # exact segment match in Python — both suggested shapes at once, so the
    # read is bounded AND a name containing the client string still cannot
    # leak into another group's header
    check("client · the group filter bounds its read in SQL like its two "
          "neighbours", _the_narrowing_read_is_bounded)


# ==================================================================== §11b
def sec_client_filter_paging() -> None:
    """The §11 fix pages a parameterised LIKE prefilter. Two questions follow
    any prefilter: does it still find everything it should (paging must not
    stop early), and can the caller make the prefilter useless again."""
    print("\n§11b  the paged prefilter — completeness and the wildcard")

    def _pages_past_a_full_page_of_misses():
        """The under-delivery shape: the matches are OLDER than a whole page
        of other clients' traffic, so a single LIMIT would return nothing."""
        target = f"grp{secrets.token_hex(2)}"
        me = (f"old.{target}.aaaaaa", secrets.token_hex(16))
        req("POST", "/api/register", auth=pair(*me),
            json_body={"slug": me[0], "org_name": "Old", "username": target})
        sender = new_org()
        for i in range(3):
            send(sender, me[0], f"early {i}")           # the oldest traffic
        for i in range(150):                            # a page-plus of noise
            a, b = new_org(), new_org()
            send(a, b[0], f"noise {i}")
        j = req("GET", "/ui/messages",
                params={"client": target, "limit": 3}).json()
        bodies = [m["body"] for m in j["messages"]]
        assert len(bodies) == 3, (
            f"the filter returned {len(bodies)} of 3 existing matches — "
            "paging stopped before reaching them")
        assert set(bodies) == {"early 0", "early 1", "early 2"}, bodies
    check("paging · matches buried behind a full page of other clients' mail "
          "are still delivered (no under-delivery)", _pages_past_a_full_page_of_misses)

    def _newest_first_across_pages():
        target = f"ord{secrets.token_hex(2)}"
        me = (f"o.{target}.bbbbbb", secrets.token_hex(16))
        req("POST", "/api/register", auth=pair(*me),
            json_body={"slug": me[0], "org_name": "Ord", "username": target})
        sender = new_org()
        for i in range(5):
            send(sender, me[0], f"m{i}")
        j = req("GET", "/ui/messages",
                params={"client": target, "limit": 3}).json()
        assert [m["body"] for m in j["messages"]] == ["m4", "m3", "m2"], j
    check("paging · the newest-first order survives the paging", _newest_first_across_pages)

    def _wildcard_name_cannot_force_a_full_walk():
        """A LIKE metacharacter in the name makes the prefilter match
        everything while the exact Python check matches nothing — so the loop
        pages the entire table looking for rows that cannot exist."""
        rows_before = len(rows("SELECT id FROM messages"))
        assert rows_before > 150, rows_before      # the fixtures above
        seen: list[int] = []
        real = db.connect

        class Recording:
            def __init__(self, con): self._c = con

            def execute(self, sql, args=()):
                cur = self._c.execute(sql, args)
                if "LIKE" in str(sql).upper():
                    seen.append(1)
                return cur

            def __getattr__(self, n):
                return getattr(self._c, n)

        db.connect = lambda: Recording(real())          # type: ignore[assignment]
        try:
            r = req("GET", "/ui/messages", params={"client": "%", "limit": 5})
        finally:
            db.connect = real                           # type: ignore[assignment]
        assert r.status_code == 200 and r.json()["messages"] == []
        assert len(seen) <= 1, (
            f"a client name of '%' made the prefilter match everything and "
            f"the loop paged the whole table ({len(seen)} pages) to return "
            f"nothing — the unbounded walk is back, on a query parameter")
    # promoted from gap() 2026-08-05, fixed same day: %, _ and the escape
    # character are escaped and the clause carries ESCAPE '\' — a wildcard
    # name now prefilter-matches nothing (one page, empty result), and
    # underscore usernames stop over-matching in production
    check("wildcard · a LIKE metacharacter in the client name cannot "
          "re-create the unbounded walk",
          _wildcard_name_cannot_force_a_full_walk)


def main() -> int:
    print("orgtree · the mail hub (F-06 Phase B)")
    sec_register()
    sec_suffix_attack()
    sec_poll()
    sec_custody()
    sec_receipts()
    sec_send()
    sec_attachments()
    sec_retention()
    sec_ui()
    sec_public_face()
    sec_client_filter()
    sec_client_filter_paging()

    print()
    if GAPS:
        print("known gaps (asserted as failing on purpose — promote when fixed):")
        for label, why, saw in GAPS:
            print(f"  ⚑ {label}\n      why: {why}\n      saw: {saw}")
        print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"hub: {PASS} passed · {len(FAIL)} FAILED · {len(GAPS)} gaps")
        return 1
    print(f"hub: all {PASS} checks passed"
          + (f" · {len(GAPS)} known gaps" if GAPS else ""))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
