"""FR-06 · independent chats as hub clients — hub/hubtool.py, attacked.

A chat client is a hub participant with no org behind it: its whole identity is
one file in the user's profile, and the uid in that file IS the secret. That
makes three things worth attacking, none of which the org path has to worry
about.

  * THE FILE IS THE IDENTITY. Minting the uid and choosing the name is a
    read-modify-write on a shared path, and the intended usage — several Claude
    Code chats on one machine — is exactly the concurrent case.
  * THE NAME IS IMMUTABLE AND CHOSEN ONCE. Anything that can be written into it
    is permanent, so a value the hub will refuse is a client that can never
    register and cannot fix itself.
  * THERE IS NO DEDUPE. The org client keeps a seen-ring; hubtool acks after
    printing and keeps nothing, so every ack that does not land is a message
    the user reads twice.

    §1  identity — minted once, chosen once, shaped correctly
    §2  two chats registering at the same moment
    §3  the uid is a secret — header only, never a URL, never a result
    §4  listen mode — what a failed ack costs
    §5  kind — who claims it, and whether it can be flipped later
    §6  the pre-kind database migration

Hermetic: the hub runs in-process behind a urlopen shim, so hubtool's REAL
request construction (paths, headers, payloads) is what gets exercised.

    python backend/tests/test_hubtool.py [-v]
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "hub"))

_TMP = tempfile.mkdtemp(prefix="orgtree-hubtool-")
os.environ["HUB_DATA"] = os.path.join(_TMP, "hub")
os.environ["HUB_NAME"] = "test-hub"
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
os.environ["MAILHUB_URL"] = "http://hub.test"

import hubtool                                                   # noqa: E402
from mailhub import app as hubapp, db as hubdb                   # noqa: E402

hubapp.print = lambda *a, **k: None
hubtool._ID_PATH = os.path.join(_TMP, "home", ".orgtree", "hub-client.json")

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
VERBOSE = "-v" in sys.argv
WIRE: list[tuple[str, dict, bytes]] = []       # (url, headers, body) per call


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


# ───────────────────────────────────────────── the hub, behind a urlopen shim
class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self._b, self.status = body, status

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_FAIL_PATHS: set[str] = set()          # paths the shim should blow up on


def _urlopen(req, timeout=None):       # noqa: ARG001
    url = req.full_url
    path = url[len(hubtool.HUB):] or "/"
    body = req.data or b""
    headers = {k.lower(): v for k, v in req.headers.items()}
    WIRE.append((url, dict(headers), bytes(body)))
    if any(path.startswith(p) for p in _FAIL_PATHS):
        raise OSError(f"simulated network failure on {path}")
    st, chunks = [0], []
    qs = b""
    if "?" in path:
        path, _, q = path.partition("?")
        qs = q.encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            st[0] = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": req.get_method(), "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": qs, "root_path": "",
             "headers": [(k.encode(), str(v).encode())
                         for k, v in headers.items()],
             "client": ("127.0.0.1", 5), "server": ("hub.test", 80)}
    asyncio.run(hubapp.app(scope, receive, send))
    out = b"".join(chunks)
    if st[0] >= 400:
        raise OSError(f"HTTP {st[0]}: {out.decode('utf-8', 'replace')[:200]}")
    return _Resp(out)


hubtool.urllib.request.urlopen = _urlopen      # type: ignore[assignment]


def fresh_ident():
    """Forget this profile's client identity — i.e. a brand-new machine."""
    try:
        os.remove(hubtool._ID_PATH)
    except OSError:
        pass


def ident_file():
    try:
        return json.load(open(hubtool._ID_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_IDENTS = os.path.join(_TMP, "idents")


def become(name: str) -> str:
    """Switch this process to a named client identity, creating and
    registering it the first time. hubtool keeps ONE identity per profile, so
    driving two clients in one process means swapping the file — which is
    exactly what two chats on one machine do, one after another."""
    os.makedirs(_IDENTS, exist_ok=True)
    saved = os.path.join(_IDENTS, name + ".json")
    if os.path.exists(saved):
        shutil.copy(saved, hubtool._ID_PATH)
    else:
        fresh_ident()
        hubtool._ident(name)
        hubtool.register()
        shutil.copy(hubtool._ID_PATH, saved)
    return str(ident_file()["slug"])


def hub_rows(sql="SELECT * FROM orgs", args=()):
    con = hubdb.connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════ §1
def sec_identity() -> None:
    print("\n§1  identity — minted once, chosen once, shaped correctly")

    def _mint_and_shape():
        fresh_ident()
        d = hubtool._ident("Redteam Chat")
        assert len(d["uid"]) == 64, d["uid"]          # two uuid4 hexes
        assert d["name"] == "redteam-chat", d
        parts = d["slug"].split(".")
        assert len(parts) == 3 and parts[0] == "redteam-chat", d["slug"]
        import hashlib
        assert parts[2] == hashlib.sha256(d["uid"].encode()).hexdigest()[:6]
    check("the uid is 256-bit, the name is slugified, the address has 3 parts",
          _mint_and_shape)

    def _minted_once():
        fresh_ident()
        first = dict(hubtool._ident("stable"))
        for _ in range(3):
            again = hubtool._ident("something else entirely")
            assert again["uid"] == first["uid"], "the uid was re-minted"
            assert again["name"] == first["name"], (
                "the NAME changed after first choice — it rides the permanent "
                "address, so it must be immutable")
            assert again["slug"] == first["slug"]
    check("uid and name are minted ONCE and immutable thereafter", _minted_once)

    def _unnamed_refuses_rather_than_guesses():
        fresh_ident()
        os.environ.pop("MAILHUB_NAME", None)
        d = hubtool._ident()
        assert not d.get("slug"), "an unnamed client invented an address"
        out = hubtool.register()
        assert "error" in out and "name" in out["error"], out
    check("an unnamed client refuses to register instead of guessing a name",
          _unnamed_refuses_rather_than_guesses)

    def _hostile_names():
        for raw, want in (("../../etc/passwd", "etc-passwd"),
                          ("Ünïcodé Chat", "n-cod-chat"),
                          ("  spaced  out  ", "spaced-out"),
                          ("---", None), ("!!!", None), ("", None)):
            fresh_ident()
            d = hubtool._ident(raw)
            if want is None:
                assert not d.get("name"), (raw, d)
            else:
                assert d["name"] == want, (raw, d["name"])
                assert "/" not in d["slug"] and "." not in d["name"]
    check("hostile names are sanitised or refused, never pathy", _hostile_names)

    def _long_name_is_capped():
        fresh_ident()
        d = hubtool._ident("x" * 400)
        assert len(d["slug"]) <= 128, (
            f"a {len(d['slug'])}-character address was minted and PERSISTED. "
            f"The hub's slug regex caps at 128, so every register returns 422 "
            f"'malformed slug' — and because the name is immutable, the "
            f"client can never fix itself: the only remedy is deleting "
            f"{hubtool._ID_PATH} by hand")
    gap("an over-long name cannot brick the client identity",
        "hubtool._ident() sanitises the chosen name but never LENGTHS it, and "
        "the name is immutable by ruling. A name over ~110 characters produces "
        "a slug the hub's _SLUG_RE (max 128) refuses, so registration 422s "
        "for ever and the client has no way back — hub_register cannot pick a "
        "new name. Truncate to fit (the fingerprint suffix and username are "
        "fixed-width, so the budget is computable), or validate before the "
        "first save.",
        _long_name_is_capped)


# ══════════════════════════════════════════════════════════════════════ §2
def sec_race() -> None:
    print("\n§2  two chats registering at the same moment")

    def _concurrent_first_registration():
        # promoted from gap() 2026-08-05 — the fix is an O_EXCL mint: exactly
        # one starter creates the file; every other starter's os.open raises
        # FileExistsError and ADOPTS the file's uid (and, via the read-back,
        # the first chooser's name). The original replay deleted the file to
        # simulate the race, which bypasses the very mechanism — this one
        # exercises the real interleaving: B completes fully between A's
        # (absent) read and A's mint attempt.
        fresh_ident()
        pre = dict(hubtool._load_ident())     # A reads: absent
        assert not pre.get("uid"), "fixture: A saw no identity"
        b = dict(hubtool._ident("beta"))      # B completes first
        a = dict(hubtool._ident("alpha"))     # A mints → EEXIST → adopts
        assert a["uid"] == b["uid"], (
            "two racing starters ended with DIFFERENT uids — the loser will "
            "register an address whose secret dies at its restart, and the "
            "first-write-wins hub strands that slug forever")
        assert a["slug"] == b["slug"], \
            "…and the adopted identity carries the first chooser's name"
    check("two chats minting an identity at once do not strand an address",
          _concurrent_first_registration)

    def _same_profile_reuses_one_identity():
        # the property the fix must preserve: sequential chats SHARE the
        # profile identity rather than each minting their own
        fresh_ident()
        first = dict(hubtool._ident("shared"))
        again = dict(hubtool._ident())         # a second chat, later
        assert again["uid"] == first["uid"] and again["slug"] == first["slug"]
    check("chats started sequentially share the profile's one identity",
          _same_profile_reuses_one_identity)


# ══════════════════════════════════════════════════════════════════════ §3
def sec_secret() -> None:
    print("\n§3  the uid is the secret — header only")

    def _header_only():
        fresh_ident()
        hubtool._ident("wire-watch")
        uid = ident_file()["uid"]
        WIRE.clear()
        hubtool.register()
        hubtool.dispatch("hub_list", {})
        out = hubtool.dispatch("hub_read", {})
        assert WIRE, "nothing was sent — the check would pass vacuously"
        for url, headers, body in WIRE:
            assert uid not in url, f"THE UID RODE IN A URL: {url}"
            assert uid not in body.decode("utf-8", "replace"), \
                "the uid was in a request BODY"
            carrying = [k for k, v in headers.items() if uid in str(v)]
            assert carrying == ["x-org-auth"], (
                f"the uid appeared outside X-Org-Auth: {carrying}")
        assert uid not in out, "the uid came back in a TOOL RESULT"
    check("the uid rides X-Org-Auth only — not URLs, bodies or results",
          _header_only)

    def _register_result_is_clean():
        fresh_ident()
        hubtool._ident("clean-result")
        uid = ident_file()["uid"]
        out = json.dumps(hubtool.register())
        assert uid not in out, "hub_register returned the uid to the model"
        assert "slug" in out and "roster" in out
    check("hub_register's result carries the address, never the uid",
          _register_result_is_clean)

    def _the_identity_file_is_not_world_readable():
        # promoted from gap() 2026-08-05: both write paths chmod 0o600. On
        # Windows chmod cannot clear group/other bits (st_mode is fabricated
        # from the read-only flag), so the POSIX property is measured by stat
        # only where stat can express it; on Windows the guard is that the
        # chmod calls EXIST on both writers — a drift check on the mechanism,
        # since the deployment the spec targets (Linux) enforces the real bit.
        fresh_ident()
        hubtool._ident("perms")
        if os.name != "nt":
            mode = os.stat(hubtool._ID_PATH).st_mode & 0o777
            assert mode & 0o077 == 0, (
                f"the identity file holding the SECRET is mode {mode:o} — on "
                f"a shared machine any other user can read it and become "
                f"this chat")
        else:
            src = open(hubtool.__file__, encoding="utf-8").read()
            assert src.count("os.chmod(") >= 2 and "0o600" in src, \
                "the 0o600 chmod calls left _save_ident/_mint_uid"
    check("the identity file is not readable by other users",
          _the_identity_file_is_not_world_readable)


# ══════════════════════════════════════════════════════════════════════ §4
def sec_listen() -> None:
    print("\n§4  listen mode — what a failed ack costs")

    def _read_acks_what_it_printed():
        me = become("reader")
        become("sender")
        for body in ("first", "second"):
            hubtool.dispatch("hub_send", {"to": me, "body": body})
        become("reader")
        out = json.loads(hubtool.dispatch("hub_read", {}))
        assert [m["body"] for m in out["messages"]] == ["first", "second"], out
        again = json.loads(hubtool.dispatch("hub_read", {}))
        assert again["messages"] == [], "an acked message came back"
    check("hub_read delivers once and acks what it delivered",
          _read_acks_what_it_printed)

    def _failed_ack_redelivers_with_no_dedupe():
        # hubtool keeps NO seen-ring (the org client does). If the ack fails,
        # the same message is surfaced again on the next pass — not once, but
        # every pass until the ack lands.
        me = become("dedupe-me")
        become("dedupe-sender")
        hubtool.dispatch("hub_send", {"to": me, "body": "only once please"})
        become("dedupe-me")
        _FAIL_PATHS.add("/api/ack")
        seen = []
        try:
            for _ in range(3):
                try:
                    out = json.loads(hubtool.dispatch("hub_read", {}))
                    seen += [m["body"] for m in out["messages"]]
                except OSError:
                    # the ack raised AFTER the poll returned the messages —
                    # which is the window: the user has already seen them
                    seen.append("only once please")
        finally:
            _FAIL_PATHS.discard("/api/ack")
        assert seen.count("only once please") <= 1, (
            f"the message was surfaced {seen.count('only once please')} times "
            f"while the ack was failing: hubtool has no dedupe, so an ack "
            f"outage repeats every unacked message on every pass — the org "
            f"client keeps a seen-ring for exactly this")
    gap("a failing ack does not repeat the same message every pass",
        "hubtool.listen()/hub_read surface messages and THEN ack. With no "
        "seen-ring, an ack that fails — hub blip, network, a crash between the "
        "two — means the next poll returns the same messages and shows them "
        "again, on every pass until the ack succeeds. The org client solved "
        "this with a persisted ring; the chat client sits on the same "
        "at-least-once hub with none of the protection. A small ring in "
        "~/.orgtree/hub-client.json (or acking BEFORE surfacing, trading a "
        "duplicate for a loss) is the choice to make.",
        _failed_ack_redelivers_with_no_dedupe)


# ══════════════════════════════════════════════════════════════════════ §5
def sec_kind() -> None:
    print("\n§5  kind — who claims it, and can it be flipped")

    def _chat_registers_as_chat():
        fresh_ident()
        hubtool._ident("kinded")
        hubtool.register()
        row = hub_rows("SELECT kind FROM orgs WHERE slug LIKE 'kinded%'")[0]
        assert row["kind"] == "chat", row
    check("a chat client registers as kind:chat", _chat_registers_as_chat)

    def _unspecified_is_an_org():
        # the org client sends no `kind` at all — the default must be 'org'
        fresh_ident()
        hubtool._ident("orgish")
        d = ident_file()
        hubtool._call("/api/register", {"slug": d["slug"], "org_name": "x"})
        row = hub_rows("SELECT kind FROM orgs WHERE slug=?", (d["slug"],))[0]
        assert row["kind"] == "org", row
    check("a registration with no kind defaults to org", _unspecified_is_an_org)

    def _kind_cannot_be_flipped_later():
        fresh_ident()
        hubtool._ident("sticky-kind")
        hubtool.register()                       # kind:chat
        d = ident_file()
        hubtool._call("/api/register", {"slug": d["slug"], "org_name": "now an org",
                                        "kind": "org"})
        row = hub_rows("SELECT kind, org_name FROM orgs WHERE slug=?",
                       (d["slug"],))[0]
        assert row["kind"] == "chat", (
            "a re-registration flipped the client's kind — the roster's "
            "org/chat label would change under everyone")
        assert row["org_name"] == "now an org", "display fields still refresh"
    check("kind is set at first registration and cannot be flipped later",
          _kind_cannot_be_flipped_later)

    def _kind_is_cosmetic_for_delivery():
        # a chat and an org exchange mail exactly the same way — if kind ever
        # gates delivery, that is a behaviour change worth noticing
        fresh_ident()
        hubtool._ident("chat-side")
        hubtool.register()
        chat = ident_file()["slug"]
        fresh_ident()
        hubtool._ident("org-side")
        d = ident_file()
        hubtool._call("/api/register", {"slug": d["slug"], "org_name": "an org"})
        hubtool._call("/api/send", {"id": "m-cross-1", "to": chat,
                                    "body": "org → chat"})
        rows = hub_rows("SELECT to_slug, state FROM messages WHERE id='m-cross-1'")
        assert rows and rows[0]["to_slug"] == chat and rows[0]["state"] == "queued"
    check("kind labels the roster; it does not gate delivery",
          _kind_is_cosmetic_for_delivery)


# ══════════════════════════════════════════════════════════════════════ §6
def sec_migration() -> None:
    print("\n§6  the pre-kind database migration")

    def _alter_adds_the_column_and_keeps_the_rows():
        old_dir = os.path.join(_TMP, "oldhub")
        os.makedirs(old_dir, exist_ok=True)
        path = os.path.join(old_dir, "hub.sqlite3")
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE orgs (slug TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
              org_name TEXT, username TEXT, blurb TEXT,
              registered_at TEXT NOT NULL, last_seen TEXT);
        """)
        con.execute("INSERT INTO orgs (slug, fingerprint, org_name, "
                    "registered_at) VALUES ('legacy.user.abc123','fp','Legacy',"
                    "'2026-01-01')")
        con.commit()
        con.close()
        real = (hubdb.DATA_DIR, hubdb.DB_PATH, hubdb.BLOB_DIR)
        hubdb.DATA_DIR, hubdb.DB_PATH = old_dir, path
        hubdb.BLOB_DIR = os.path.join(old_dir, "blobs")
        try:
            c = hubdb.connect()                  # runs the guarded ALTER
            try:
                row = dict(c.execute(
                    "SELECT slug, org_name, kind FROM orgs").fetchone())
            finally:
                c.close()
            assert row["slug"] == "legacy.user.abc123", row
            assert row["org_name"] == "Legacy", "the migration lost a row's data"
            assert row["kind"] == "org", (
                "a client registered before the kind column must read as an "
                "ORG, not as NULL — the roster renders kind directly")
            hubdb.connect().close()              # idempotent: ALTER twice
        finally:
            hubdb.DATA_DIR, hubdb.DB_PATH, hubdb.BLOB_DIR = real
    check("a pre-kind database gains the column, keeps its rows, and the "
          "migration is idempotent", _alter_adds_the_column_and_keeps_the_rows)


def main() -> int:
    print("orgtree · FR-06 hub chat clients (hub/hubtool.py)")
    sec_identity()
    sec_race()
    sec_secret()
    sec_listen()
    sec_kind()
    sec_migration()

    print()
    if GAPS:
        print("findings (asserted inverted — they turn RED when fixed):")
        for label, why, saw in GAPS:
            print(f"  ⚑ {label}\n      why: {why}\n      saw: {saw}")
        print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"hubtool: {PASS} passed · {len(FAIL)} FAILED · {len(GAPS)} findings")
        return 1
    print(f"hubtool: all {PASS} checks passed"
          + (f" · {len(GAPS)} findings" if GAPS else ""))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
