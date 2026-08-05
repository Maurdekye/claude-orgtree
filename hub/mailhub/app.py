# pyright: strict
"""The orgtree mail hub — org-to-org mail across machines.

Design (docs/mailserver-spec.md, all rulings binding):
- Instances DIAL OUT and long-poll; the hub holds a queue per registered org.
  Nothing ever connects back to an instance — the direction of the connection
  is the security model.
- Joining is open (reachability = authorization, closed network); ADDRESSES
  are owned: registration presents the org's self-issued secret, the hub
  stores only its sha256 fingerprint, and every later call re-presents the
  secret in the `X-Org-Auth` header. Compare the FULL fingerprint with
  hmac.compare_digest — the 6-char display suffix is a label, not a check.
- One multiplexed long poll per instance carries messages for all its orgs,
  the sender receipts it is owed, and the roster with presence.
- Delivery is at-least-once: a message stays `queued` until the recipient
  ACKS custody (after persisting it); duplicates are the client's to collapse.
- `received_at` (hub clock, ms) is the ordering authority; `sent_at` is the
  sender's claim, display only.
- ⚠ The hub sees every message in PLAINTEXT, and the read-only UI at `/` is
  deliberately unauthenticated: on the closed collaborative network this is
  ruled for, hub access IS read access to everyone's correspondence. Run it
  on a box you control; see README.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import socket
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse


from . import db


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    db.connect().close()                 # create schema early, fail loudly
    task = asyncio.get_event_loop().create_task(_sweep_loop())
    print(json.dumps({"ts": _now_iso(), "hub": HUB_NAME,
                      "retention_days": RETENTION_DAYS}), flush=True)
    yield
    task.cancel()


app = FastAPI(title="orgtree mail hub", lifespan=_lifespan)

HUB_NAME = (os.environ.get("HUB_NAME") or "").strip() or socket.gethostname()
RETENTION_DAYS = int(os.environ.get("HUB_RETENTION_DAYS", "30"))
MAX_FILE_BYTES = 25 * 1024 * 1024      # mirror the orgtree caps
MAX_FILES_PER_MESSAGE = 10
BODY_MAX = 20000                       # mirror the org-inbox truncation
PRESENCE_WINDOW = 90.0                 # s since last authed call = online
POLL_CEILING = 55.0                    # s, mirror extern_wait

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

# revision counter — bumped by every write that could satisfy a parked poll,
# so waiters re-check only when something actually changed (the extern_wait
# model). Plain int in a dict cell: the event loop is single-threaded.
_change = {"n": 0}
# presence: slugs with a parked poll right now (count), and the monotonic
# time of each slug's last authed call
_parked: dict[str, int] = {}
_seen_mono: dict[str, float] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds") \
        .replace("+00:00", "Z")


def _bump() -> None:
    _change["n"] += 1


def _fp(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _mark_seen(con: Any, slugs: list[str]) -> None:
    if not slugs:
        return
    now = _now_iso()
    mono = time.monotonic()
    for s in slugs:
        _seen_mono[s] = mono
        con.execute("UPDATE orgs SET last_seen=? WHERE slug=?", (now, s))
    con.commit()


def _online(slug: str) -> bool:
    if _parked.get(slug, 0) > 0:
        return True
    t = _seen_mono.get(slug)
    return t is not None and (time.monotonic() - t) < PRESENCE_WINDOW


def _auth(request: Request, con: Any) -> list[str]:
    """Verify the `X-Org-Auth: <slug>:<secret> ...` header. Returns the slugs
    whose secret hashes to the STORED fingerprint (full-digest compare).
    Unknown slugs and wrong secrets are simply not in the result — a
    multiplexed call proceeds for the valid ones."""
    out: list[str] = []
    header = request.headers.get("x-org-auth", "")
    for pair in header.split():
        slug, _, secret = pair.partition(":")
        if not slug or not secret:
            continue
        row = con.execute("SELECT fingerprint FROM orgs WHERE slug=?",
                          (slug,)).fetchone()
        if row and hmac.compare_digest(str(row["fingerprint"]), _fp(secret)):
            out.append(slug)
    return out


def _roster(con: Any) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT slug, org_name, username, blurb, last_seen, kind FROM orgs "
        "ORDER BY slug").fetchall()
    return [{"slug": r["slug"], "org_name": r["org_name"],
             "username": r["username"], "blurb": r["blurb"],
             "online": _online(r["slug"]), "last_seen": r["last_seen"],
             "kind": r["kind"] or "org"}
            for r in rows]


@app.middleware("http")
async def log_requests(request: Request, call_next: Any) -> Any:
    t0 = time.monotonic()
    resp = await call_next(request)
    # one structured line per request; slugs come from the header WITHOUT
    # secrets (never log the credential)
    slugs = [p.partition(":")[0] for p in
             request.headers.get("x-org-auth", "").split() if ":" in p]
    print(json.dumps({"ts": _now_iso(), "path": request.url.path,
                      "slugs": slugs, "status": resp.status_code,
                      "ms": round((time.monotonic() - t0) * 1000)}),
          flush=True)
    return resp


# ------------------------------------------------------------------ register

@app.post("/api/register")
async def register(request: Request) -> dict[str, Any]:
    body = await request.json()
    slug = str(body.get("slug") or "").strip()
    if not _SLUG_RE.match(slug):
        raise HTTPException(422, "malformed slug")
    # the secret arrives in the auth header (never in the body/URL)
    secret = ""
    for pair in request.headers.get("x-org-auth", "").split():
        s, _, sec = pair.partition(":")
        if s == slug:
            secret = sec
    if not secret:
        raise HTTPException(401, "registration requires X-Org-Auth: "
                                 "<slug>:<secret> for the slug being registered")
    con = db.connect()
    try:
        row = con.execute("SELECT fingerprint FROM orgs WHERE slug=?",
                          (slug,)).fetchone()
        fp = _fp(secret)
        if row is None:
            # first write wins the slug — colliding requires producing a
            # secret that hashes to the holder's fingerprint
            kind = "chat" if str(body.get("kind") or "") == "chat" else "org"
            con.execute(
                "INSERT INTO orgs (slug, fingerprint, org_name, username, "
                "blurb, registered_at, last_seen, kind) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (slug, fp, str(body.get("org_name") or ""),
                 str(body.get("username") or ""),
                 str(body.get("blurb") or ""), _now_iso(), _now_iso(), kind))
        elif not hmac.compare_digest(str(row["fingerprint"]), fp):
            raise HTTPException(403, "slug is owned by another identity")
        else:
            # re-registration refreshes the display fields
            con.execute(
                "UPDATE orgs SET org_name=?, username=?, blurb=? WHERE slug=?",
                (str(body.get("org_name") or ""),
                 str(body.get("username") or ""),
                 str(body.get("blurb") or ""), slug))
        con.commit()
        _mark_seen(con, [slug])
        _bump()
        return {"ok": True, "name": HUB_NAME,
                "retention_days": RETENTION_DAYS, "roster": _roster(con)}
    finally:
        con.close()


# ---------------------------------------------------------------------- poll

@app.post("/api/poll")
async def poll(request: Request, wait: float = 25.0) -> dict[str, Any]:
    con = db.connect()
    try:
        slugs = _auth(request, con)
    finally:
        con.close()
    if not slugs:
        raise HTTPException(401, "no valid org credentials in X-Org-Auth")
    deadline = time.monotonic() + min(max(wait, 0.0), POLL_CEILING)
    for s in slugs:
        _parked[s] = _parked.get(s, 0) + 1
    try:
        rev = -1
        while True:
            if rev != _change["n"]:
                rev = _change["n"]
                con = db.connect()
                try:
                    qmarks = ",".join("?" * len(slugs))
                    msgs = con.execute(
                        f"SELECT * FROM messages WHERE state='queued' AND "
                        f"to_slug IN ({qmarks}) ORDER BY received_at, rowid",
                        slugs).fetchall()
                    rec = con.execute(
                        f"SELECT id, state, fetched_at, delivered_at, read_at "
                        f"FROM messages WHERE receipts_pushed=0 AND "
                        f"from_slug IN ({qmarks})", slugs).fetchall()
                    if msgs or rec or time.monotonic() >= deadline:
                        receipts = [
                            {"id": r["id"],
                             "state": ("read" if r["read_at"] else
                                       "delivered" if r["delivered_at"] else
                                       "fetched" if r["fetched_at"] else
                                       "received"),
                             "fetched_at": r["fetched_at"],
                             "delivered_at": r["delivered_at"],
                             "read_at": r["read_at"]} for r in rec]
                        if rec:
                            # marked pushed on return — loss-tolerant by
                            # design: a dropped response self-heals to the
                            # next state transition
                            ids = [r["id"] for r in rec]
                            con.execute(
                                f"UPDATE messages SET receipts_pushed=1 WHERE "
                                f"id IN ({','.join('?' * len(ids))})", ids)
                            con.commit()
                        _mark_seen(con, slugs)
                        return {"name": HUB_NAME,
                                "messages": [db.row_to_envelope(m)
                                             for m in msgs],
                                "receipts": receipts,
                                "roster": _roster(con)}
                finally:
                    con.close()
            if time.monotonic() >= deadline:
                con = db.connect()
                try:
                    _mark_seen(con, slugs)
                    return {"name": HUB_NAME, "messages": [], "receipts": [],
                            "roster": _roster(con)}
                finally:
                    con.close()
            await asyncio.sleep(0.5)
    finally:
        for s in slugs:
            _parked[s] = max(0, _parked.get(s, 0) - 1)


# ----------------------------------------------------------------------- ack

@app.post("/api/ack")
async def ack(request: Request) -> dict[str, Any]:
    body = cast("dict[str, Any]", await request.json())
    ids = [str(i) for i in cast("list[Any]", body.get("ids") or [])]
    con = db.connect()
    try:
        slugs = _auth(request, con)
        if not slugs:
            raise HTTPException(401, "no valid org credentials")
        n = 0
        for mid in ids:
            cur = con.execute(
                f"UPDATE messages SET state='fetched', fetched_at=?, "
                f"receipts_pushed=0 WHERE id=? AND state='queued' AND "
                f"to_slug IN ({','.join('?' * len(slugs))})",
                (_now_iso(), mid, *slugs))
            n += cur.rowcount
        con.commit()
        _mark_seen(con, slugs)
        if n:
            _bump()
        return {"acked": n}
    finally:
        con.close()


# ---------------------------------------------------------------------- send

@app.post("/api/send")
async def send(request: Request) -> dict[str, Any]:
    body = cast("dict[str, Any]", await request.json())
    to = str(body.get("to") or "").strip()
    con = db.connect()
    try:
        slugs = _auth(request, con)
        frm = str(body.get("from") or (slugs[0] if slugs else ""))
        if frm not in slugs:
            raise HTTPException(401, "sender credentials required")
        if not con.execute("SELECT 1 FROM orgs WHERE slug=?", (to,)).fetchone():
            raise HTTPException(422, f"no org registered as {to!r}")
        mid = str(body.get("id") or uuid.uuid4().hex)
        att_ids = [str(a) for a in
                   cast("list[Any]", body.get("attachments") or [])]
        if len(att_ids) > MAX_FILES_PER_MESSAGE:
            raise HTTPException(422,
                                f"at most {MAX_FILES_PER_MESSAGE} attachments")
        metas: list[dict[str, Any]] = []
        for aid in att_ids:
            row = con.execute(
                "SELECT id, name, bytes, owner_slug, message_id FROM "
                "attachments WHERE id=?", (aid,)).fetchone()
            if not row or row["owner_slug"] != frm:
                raise HTTPException(422, f"unknown attachment {aid!r}")
            if row["message_id"] and row["message_id"] != mid:
                raise HTTPException(422, f"attachment {aid!r} already bound")
            metas.append({"id": row["id"], "name": row["name"],
                          "bytes": row["bytes"]})
        received = _now_iso()
        cur = con.execute(
            "INSERT OR IGNORE INTO messages (id, from_slug, to_slug, body, "
            "kind, thread_id, sent_at, received_at, attachments) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, frm, to, str(body.get("body") or "")[:BODY_MAX],
             body.get("kind"), body.get("thread_id"), body.get("sent_at"),
             received, json.dumps(metas)))
        fresh = cur.rowcount > 0            # idempotent retry: 0 = duplicate
        if fresh:
            for aid in att_ids:
                con.execute("UPDATE attachments SET message_id=? WHERE id=?",
                            (mid, aid))
        else:
            received = str(con.execute(
                "SELECT received_at FROM messages WHERE id=?",
                (mid,)).fetchone()["received_at"])
        con.commit()
        _mark_seen(con, [frm])
        if fresh:
            _bump()
        # the 200 IS the "received" receipt — hub custody confirmed
        return {"id": mid, "received_at": received, "duplicate": not fresh}
    finally:
        con.close()


# ------------------------------------------------------------------ receipts

@app.post("/api/receipts")
async def receipts(request: Request) -> dict[str, Any]:
    body = cast("dict[str, Any]", await request.json())
    con = db.connect()
    try:
        slugs = _auth(request, con)
        if not slugs:
            raise HTTPException(401, "no valid org credentials")
        n = 0
        for r in cast("list[dict[str, Any]]", body.get("receipts") or []):
            mid = str(r.get("id") or "")
            state = str(r.get("state") or "")
            if state not in ("delivered", "read"):
                continue
            col = "delivered_at" if state == "delivered" else "read_at"
            cur = con.execute(
                f"UPDATE messages SET {col}=?, receipts_pushed=0 WHERE id=? "
                f"AND {col} IS NULL AND "
                f"to_slug IN ({','.join('?' * len(slugs))})",
                (str(r.get("at") or _now_iso()), mid, *slugs))
            n += cur.rowcount
        con.commit()
        _mark_seen(con, slugs)
        if n:
            _bump()
        return {"recorded": n}
    finally:
        con.close()


# --------------------------------------------------------------- attachments

@app.post("/api/attachments")
async def attachment_upload(request: Request, name: str = "file") -> dict[str, Any]:
    con = db.connect()
    try:
        slugs = _auth(request, con)
        if not slugs:
            raise HTTPException(401, "no valid org credentials")
        owner = slugs[0]
        data = await request.body()
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"attachment exceeds "
                                     f"{MAX_FILE_BYTES // (1024 * 1024)} MB")
        aid = uuid.uuid4().hex
        with open(db.blob_path(aid), "wb") as f:
            f.write(data)
        con.execute(
            "INSERT INTO attachments (id, owner_slug, name, bytes, created_at)"
            " VALUES (?,?,?,?,?)",
            (aid, owner, os.path.basename(name)[:255] or "file", len(data),
             _now_iso()))
        con.commit()
        _mark_seen(con, [owner])
        return {"id": aid, "bytes": len(data)}
    finally:
        con.close()


@app.get("/api/attachments/{aid}")
async def attachment_download(aid: str, request: Request) -> Any:
    con = db.connect()
    try:
        slugs = _auth(request, con)
        if not slugs:
            raise HTTPException(401, "no valid org credentials")
        row = con.execute(
            "SELECT owner_slug, name, message_id FROM attachments WHERE id=?",
            (aid,)).fetchone()
        if not row:
            raise HTTPException(404, "no such attachment")
        allowed = row["owner_slug"] in slugs
        if not allowed and row["message_id"]:
            m = con.execute("SELECT to_slug FROM messages WHERE id=?",
                            (row["message_id"],)).fetchone()
            allowed = bool(m) and m["to_slug"] in slugs
        if not allowed:
            raise HTTPException(403, "not yours")
        path = db.blob_path(aid)
        if not os.path.isfile(path):
            raise HTTPException(410, "blob expired")
        return FileResponse(path, filename=str(row["name"]),
                            media_type="application/octet-stream")
    finally:
        con.close()


# -------------------------------------------------------------- roster/health

@app.get("/api/roster")
async def roster(request: Request) -> dict[str, Any]:
    con = db.connect()
    try:
        slugs = _auth(request, con)
        if not slugs:
            raise HTTPException(401, "no valid org credentials")
        _mark_seen(con, slugs)
        return {"name": HUB_NAME, "roster": _roster(con)}
    finally:
        con.close()


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    con = db.connect()
    try:
        orgs = con.execute("SELECT COUNT(*) c FROM orgs").fetchone()["c"]
        queued = con.execute(
            "SELECT COUNT(*) c FROM messages WHERE state='queued'"
        ).fetchone()["c"]
        return {"ok": True, "name": HUB_NAME, "orgs": orgs, "queued": queued,
                "retention_days": RETENTION_DAYS}
    finally:
        con.close()


# ------------------------------------------------------------------- hub UI
# read-only by design ("the hub is not a place to compose from"). Global view
# with a per-org filter is RULED (spec §10.1): the operator of a closed
# network is a participant, and hub access = read access to all mail.

@app.get("/")
async def ui_index() -> Any:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static", "index.html")
    return HTMLResponse(open(path, encoding="utf-8").read())


@app.get("/ui/data")
async def ui_data() -> dict[str, Any]:
    con = db.connect()
    try:
        rows = _roster(con)
        for r in rows:
            r["queued"] = con.execute(
                "SELECT COUNT(*) c FROM messages WHERE state='queued' AND "
                "to_slug=?", (r["slug"],)).fetchone()["c"]
        return {"name": HUB_NAME, "retention_days": RETENTION_DAYS,
                "orgs": rows}
    finally:
        con.close()


@app.get("/ui/messages")
async def ui_messages(org: str = "", limit: int = 100) -> dict[str, Any]:
    con = db.connect()
    try:
        limit = min(max(int(limit), 1), 500)
        if org:
            rows = con.execute(
                "SELECT * FROM messages WHERE from_slug=? OR to_slug=? "
                "ORDER BY received_at DESC, rowid DESC LIMIT ?",
                (org, org, limit)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM messages ORDER BY received_at DESC, rowid DESC "
                "LIMIT ?", (limit,)).fetchall()
        out: list[dict[str, Any]] = []
        for m in rows:
            e = db.row_to_envelope(m)
            e["state"] = m["state"]
            e["delivered_at"] = m["delivered_at"]
            e["read_at"] = m["read_at"]
            out.append(e)
        return {"messages": out}
    finally:
        con.close()


# ---------------------------------------------------------------- retention

async def _sweep_loop() -> None:
    while True:
        try:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=RETENTION_DAYS)) \
                .isoformat(timespec="milliseconds").replace("+00:00", "Z")
            con = db.connect()
            try:
                old = con.execute(
                    "SELECT id FROM attachments WHERE created_at < ?",
                    (cutoff,)).fetchall()
                for r in old:
                    try:
                        os.remove(db.blob_path(str(r["id"])))
                    except OSError:
                        pass
                con.execute("DELETE FROM attachments WHERE created_at < ?",
                            (cutoff,))
                n = con.execute("DELETE FROM messages WHERE received_at < ?",
                                (cutoff,)).rowcount
                con.commit()
                if n:
                    print(json.dumps({"ts": _now_iso(), "sweep": n}),
                          flush=True)
            finally:
                con.close()
        except Exception as e:                                   # noqa: BLE001
            print(json.dumps({"ts": _now_iso(), "sweep_error": str(e)}),
                  flush=True)
        await asyncio.sleep(3600)
