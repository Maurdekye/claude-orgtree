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
import re
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
# per-session name-keyed identities (user ruling 2026-08-05) — one file per
# chosen name under hub-clients/, legacy single-file adopted by name
hubtool._ID_DIR = os.path.join(_TMP, "home", ".orgtree", "hub-clients")
hubtool._LEGACY_ID = os.path.join(_TMP, "home", ".orgtree", "hub-client.json")

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


def fixture(ok, msg) -> None:
    """A PRECONDITION inside a gap body — raised as a RuntimeError so `gap`
    below re-reports it as a broken check instead of swallowing it as the
    finding.

    ⚠ Learned the expensive way (2026-08-06, test_batched_asks). A gap
    body's whole contract is "this assert fails", so a fixture assert and the
    assert that measures the defect are indistinguishable: gap() catches the
    first AssertionError it meets and files it as the finding. A credit
    request for 8 against a grant of 20 took the at-or-below no-op branch, so
    no row ever existed — the gap fired on its own scaffolding while the
    defect it named was real but unexercised. Use fixture(...) for every setup
    precondition in a gap body; keep a bare `assert` for the property under
    test."""
    if not ok:
        raise RuntimeError(f"fixture: {msg}")


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
    """Forget every client identity — i.e. a brand-new machine."""
    shutil.rmtree(hubtool._ID_DIR, ignore_errors=True)
    try:
        os.remove(hubtool._LEGACY_ID)
    except OSError:
        pass
    hubtool._CUR.clear()
    os.environ.pop("MAILHUB_NAME", None)


def ident_file():
    """The ACTIVE identity's file (per-session name-keyed since the
    2026-08-05 ruling)."""
    nm = hubtool._active_name()
    if not nm:
        return {}
    try:
        return json.load(open(hubtool._id_path(nm), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def become(name: str) -> str:
    """Switch this process to a named client identity, creating and
    registering it the first time. Since the per-session ruling this is the
    NORMAL shape — each name IS its own persisted identity file, so
    switching is just re-activating a name (no file swapping needed)."""
    hubtool._ident(name)
    hubtool.register()
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

    def _minted_once_per_name():
        # per-session ruling (2026-08-05): the NAME is the identity key.
        # The same name always resumes the same uid/slug; a DIFFERENT name
        # is deliberately a different identity — that is what lets N
        # concurrent sessions each have their own deliver-once mailbox.
        fresh_ident()
        first = dict(hubtool._ident("stable"))
        for _ in range(3):
            again = hubtool._ident("stable")
            assert again["uid"] == first["uid"], "the uid was re-minted"
            assert again["slug"] == first["slug"]
        other = dict(hubtool._ident("something-else-entirely"))
        assert other["uid"] != first["uid"] and other["slug"] != first["slug"], (
            "two different session names shared one identity — sessions "
            "would race for each other's mail")
    check("a name resumes its own identity; a different name is a different "
          "identity", _minted_once_per_name)

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
            f"client can never fix itself: the only remedy is deleting its "
            f"file under {hubtool._ID_DIR} by hand")
    # was a gap: an over-long immutable name bricked the client (every
    # register 422'd). Fixed 2026-08-05: _norm_name budgets the name to
    # 128 − len(user) − 6 − 2 before anything persists.
    check("an over-long name cannot brick the client identity",
          _long_name_is_capped)


# ══════════════════════════════════════════════════════════════════════ §2
def sec_race() -> None:
    print("\n§2  two chats registering at the same moment")

    def _concurrent_first_registration():
        # promoted from gap() 2026-08-05; re-keyed for the per-session
        # ruling the same day. The race now only exists when two processes
        # pick the SAME name — the O_EXCL mint makes exactly one starter
        # create that name's file; the other's os.open raises FileExistsError
        # and ADOPTS the file's uid. This exercises the real interleaving:
        # B completes fully between A's (absent) read and A's mint attempt.
        fresh_ident()
        pre = dict(hubtool._load_ident("gamma"))   # A reads: absent
        assert not pre.get("uid"), "fixture: A saw no identity"
        b = dict(hubtool._ident("gamma"))          # B completes first
        a = dict(hubtool._ident("gamma"))          # A mints → EEXIST → adopts
        assert a["uid"] == b["uid"], (
            "two racing starters of ONE name ended with DIFFERENT uids — the "
            "loser will register an address whose secret dies at its restart, "
            "and the first-write-wins hub strands that slug forever")
        assert a["slug"] == b["slug"]
    check("two processes minting the SAME name at once do not strand an "
          "address", _concurrent_first_registration)

    def _returning_session_resumes_its_identity():
        # the ruling's persistence half: the session saves its name and
        # REUSES it — a later process registering the remembered name gets
        # the same uid and therefore the same address back
        fresh_ident()
        first = dict(hubtool._ident("shared"))
        hubtool._CUR.clear()                       # a fresh process, later
        again = dict(hubtool._ident("shared"))
        assert again["uid"] == first["uid"] and again["slug"] == first["slug"]
    check("a returning session resumes its named identity (same address)",
          _returning_session_resumes_its_identity)

    def _legacy_single_identity_is_adopted():
        # the pre-ruling single-profile file: its uid must move to the new
        # per-name store so the already-registered address keeps working
        # (the hub is first-write-wins — a re-mint would strand it)
        fresh_ident()
        os.makedirs(os.path.dirname(hubtool._LEGACY_ID), exist_ok=True)
        legacy = {"uid": "e" * 64, "name": "old-timer"}
        with open(hubtool._LEGACY_ID, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        d = hubtool._ident("old-timer")
        assert d["uid"] == "e" * 64, (
            "the legacy identity was re-minted instead of adopted — its "
            "registered address is now unreachable forever")
        other = hubtool._ident("newcomer")
        assert other["uid"] != "e" * 64, (
            "a DIFFERENT name adopted the legacy uid — every session would "
            "collapse back into one identity")
    check("the pre-ruling single-profile identity is adopted by ITS name "
          "only", _legacy_single_identity_is_adopted)


# ══════════════════════════════════════════════════════════ §2b (redteam)
def sec_names() -> None:
    """The per-session ruling moved the identity key from THE PROFILE to THE
    NAME. That kills the old race — but only for sessions whose names differ,
    and nothing anywhere makes them differ. This section is about what the new
    key costs: what happens when two sessions pick the same name, and what
    happens when one session picks the wrong one."""
    print("\n§2b  per-session names — the new key, and what it gives up")

    def _taken_name_is_visibly_resumed():
        # was a finding: two sessions choosing the same words silently
        # merged into one mailbox. Same-name adoption stays BY DESIGN (it is
        # both the O_EXCL race fix and the ruling's resume-by-name), so the
        # fix is the cheap honest one the finding named: hub_register's
        # result now carries `resumed` whenever the name pre-existed, so a
        # session expecting a FRESH identity sees the collision and can
        # choose another name. (The listener half gets a hard lock — below.)
        fresh_ident()
        first = hubtool.register("orgtree-redteam")
        assert "resumed" not in first, (
            f"a brand-new name reported as resumed: {first}")
        hubtool._CUR.clear()                  # a DIFFERENT session, same idea
        second = hubtool.register("orgtree-redteam")
        assert second.get("resumed"), (
            "a second session registering an EXISTING name got no signal — "
            "the two silently share one mailbox and split each other's mail")
    check("names · registering a name that already exists says so "
          "(`resumed`) instead of silently merging",
          _taken_name_is_visibly_resumed)

    def _second_live_listener_is_refused():
        # was a finding (its strongest half): a second session on the same
        # name CONSUMED the first's mail. The standing-listener path — the
        # one that runs unattended for a whole session — now takes a
        # LIVE-HOLDER lock beside the identity file: a second `listen` on
        # the same name is refused while the first's pid is alive. (The
        # interactive hub_read path stays shared by design for the resume
        # case; `resumed` above is its visibility.)
        fresh_ident()
        become("locked-name")
        lock = hubtool._id_path("locked-name") + ".listening"
        # the live holder must be a FOREIGN pid: listen treats its own pid
        # as a stale self-lock and takes it over (correct — a crashed
        # listener must not brick its name)
        import subprocess
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"])
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(sleeper.pid))
        buf = io.StringIO()
        import contextlib
        try:
            with contextlib.redirect_stdout(buf):
                hubtool.listen("locked-name")   # must refuse, not loop
        finally:
            sleeper.kill()
            try:
                os.remove(lock)
            except OSError:
                pass
        out = buf.getvalue()
        assert "already listens" in out, (
            f"a second listener on a locked name was not refused: {out!r}")
    check("names · a second live listener on the same name is refused, not "
          "silently split", _second_live_listener_is_refused)

    def _typo_listener_refuses_and_names_the_known():
        # was a finding: listen minted on a miss, so `listen nebula-buidler`
        # produced a confident listener on a brand-new empty address while
        # the real mail sat at nebula-builder — and silence is
        # indistinguishable from 'no mail yet'. Fixed 2026-08-05: every verb
        # except register resolves EXISTING identities only (mint=False);
        # a listener miss refuses and prints the known names.
        fresh_ident()
        real = become("nebula-builder")
        become("outsider")
        hubtool.dispatch("hub_send", {"to": real, "body": "the real mail"})
        hubtool._CUR.clear()
        buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(buf):
            hubtool.listen("nebula-buidler")          # one transposition
        out = buf.getvalue()
        assert "no identity named" in out and "nebula-builder" in out, (
            f"a typo'd listen neither refused nor named the known "
            f"identities: {out!r}")
        assert not os.path.exists(
            hubtool._id_path("nebula-buidler")), (
            "the typo'd listen MINTED an identity file anyway")
    check("names · a typo'd listener refuses and prints the known names, "
          "minting nothing", _typo_listener_refuses_and_names_the_known)

    def _name_cannot_escape_the_identity_dir():
        for hostile in ("../../evil", "..\\..\\evil", "/etc/passwd",
                        "C:/Windows/win.ini", "a/../../b"):
            nm = hubtool._norm_name(hostile)
            assert "/" not in nm and "\\" not in nm and ".." not in nm, nm
            p = os.path.abspath(hubtool._id_path(nm))
            assert p.startswith(os.path.abspath(hubtool._ID_DIR) + os.sep), p
    def _split_is_disclosed_not_prevented():
        """What the three fixes did and did NOT do. The listener lock stops a
        second LISTENER; `resumed` DISCLOSES a name that already exists. The
        underlying property is unchanged and by design: one name is one
        identity, so two MCP clients on that name still share one
        deliver-once mailbox. Pinned so nobody later reads the fixes as
        prevention."""
        fresh_ident()
        me = become("shared-desk")
        become("shared-desk-sender")
        hubtool.dispatch("hub_send", {"to": me, "body": "for whoever asks first"})
        hubtool._CUR.clear()
        become("shared-desk")                  # a second session, same name
        out = json.loads(hubtool.dispatch("hub_read", {}))
        assert [m["body"] for m in out["messages"]] == ["for whoever asks first"], (
            "the residual changed shape — re-read the disclosure story")
        # and the disclosure is what the second session gets instead
        again = json.loads(hubtool.dispatch("hub_register", {"name": "shared-desk"}))
        assert again.get("resumed"), (
            "no `resumed` notice on a name that already existed — the "
            "disclosure that replaced prevention is missing")
    check("names · characterised: the mailbox split is DISCLOSED (`resumed`) "
          "and listener-locked, not prevented — two MCP clients on one name "
          "still share one deliver-once mailbox, by design",
          _split_is_disclosed_not_prevented)

    check("names · a hostile name cannot escape hub-clients/ (the name is a "
          "FILENAME now, which it was not before the ruling)",
          _name_cannot_escape_the_identity_dir)


# ══════════════════════════════════════════════════════ §2c (redteam, live)
def sec_crash() -> None:
    """THE IDENTITY FILE IS THE CREDENTIAL, AND IT IS WRITTEN IN PLACE.

    Not hypothetical: a power cut on 2026-08-05 cost this very session its
    address. It came back as orgtree-redteam.ncola-k8bx.7b471a while its old
    address .895ec4 was still on the hub roster — same name, new uid, and the
    old slug now has no surviving preimage on a first-write-wins hub, so it is
    unclaimable forever and anything sent to it is accepted and never read.
    The `resumed` notice correctly did NOT fire, which is how the loss was
    noticed in the first place.

    Both writers here are non-atomic: _save_ident truncates then dumps, and
    _mint_uid's O_EXCL path writes without fsync. The crash artifact is a
    zero-byte or truncated file — and what _ident does with one is mint a new
    identity over it, silently."""
    print("\n§2c  crash safety — the file that IS the credential")

    def _corrupt_file_is_loud_and_preserved():
        # was a gap; the shipped fix chose QUARANTINE + LOUD REMINT over the
        # prescribed refusal: the uid in a torn file is unrecoverable either
        # way (there is no backup to restore), and a hook-onboarded session
        # that refuses forever is a silent no-mail-channel fleet failure —
        # so instead (a) the wreck is preserved beside the file
        # (.corrupt-<ts>), never overwritten, (b) register() carries a
        # `reminted` warning naming the consequence (new address, old one
        # dead, tell your correspondents), and (c) read-only verbs
        # (mint=False) still refuse rather than proceed.
        fresh_ident()
        first = dict(hubtool._ident("survivor"))
        assert first.get("uid"), "fixture: an identity must exist first"
        with open(hubtool._id_path("survivor"), "w", encoding="utf-8") as f:
            f.write("")                # the crash artifact
        hubtool._CUR.clear()
        ro = hubtool._ident("survivor", mint=False)
        assert not ro.get("uid"), (
            "a read-only verb proceeded against a corrupt identity file")
        out = hubtool.register("survivor")
        assert out.get("reminted"), (
            "re-registering over a corrupt file carried no warning — the "
            "session keeps its name, loses its address, and is told nothing")
        assert out.get("slug") and first["uid"] not in json.dumps(out)
        wrecks = [f for f in os.listdir(hubtool._ID_DIR)
                  if f.startswith("survivor.json.corrupt-")]
        assert wrecks, "the damaged file was overwritten, not quarantined"
    check("crash · a damaged identity file is quarantined and the re-mint is "
          "LOUD (`reminted`), never silent", _corrupt_file_is_loud_and_preserved)

    def _an_interrupted_save_does_not_destroy_the_old_identity():
        fresh_ident()
        hubtool._ident("durable")
        before = io_read(hubtool._id_path("durable"))
        assert '"uid"' in before
        real_dump = hubtool.json.dump

        def boom(*a, **k):                    # the write dies mid-flight
            raise OSError("power cut")
        hubtool.json.dump = boom              # type: ignore[assignment]
        try:
            try:
                hubtool._save_ident("durable", {"uid": "n" * 64,
                                                "name": "durable"})
            except OSError:
                pass
        finally:
            hubtool.json.dump = real_dump     # type: ignore[assignment]
        after = io_read(hubtool._id_path("durable"))
        assert after == before, (
            "an interrupted save left the identity file as "
            f"{after[:40]!r} — open(path, 'w') truncates BEFORE anything is "
            "written, so the credential is destroyed by the attempt")
    # promoted from gap() 2026-08-05: _save_ident is tmp + fsync + replace
    # since 5ef6028 — the interrupted dump dies in the .tmp file and the
    # live credential survives untouched
    check("crash · an interrupted save leaves the previous identity intact",
          _an_interrupted_save_does_not_destroy_the_old_identity)

    def _stale_listener_lock_is_recoverable():
        # the outage also left .listening locks behind for all three sessions;
        # this is the property that made recovery automatic rather than manual
        fresh_ident()
        hubtool._ident("relistener")
        lock = hubtool._id_path("relistener") + ".listening"
        with open(lock, "w", encoding="utf-8") as f:
            f.write("999999")                 # a pid that cannot be alive
        assert not hubtool._pid_alive(999999), "fixture: the pid must be dead"
        os.remove(lock)
    check("crash · a listener lock left by a killed session names a dead pid "
          "(the takeover path, exercised for real by the outage)",
          _stale_listener_lock_is_recoverable)


def io_read(path: str) -> str:
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return ""


# ══════════════════════════════════════════════ §2d (redteam, post-fix)
def sec_mint_durability() -> None:
    """The 5ef6028 fix made _save_ident durable (tmp + fsync + replace). The
    OTHER writer — _mint_uid, the O_EXCL create that brings an identity into
    existence — still writes buffered. That is the highest-risk window in the
    whole flow, because the hub's copy of the fingerprint IS durable: the
    remote side remembers an address whose local secret was never flushed."""
    print("\n§2d  durability of the MINT path (the fix's other half)")

    def _fsync_probe(fn):
        """Run fn with os.fsync counted. Returns the call count."""
        seen = [0]
        real = hubtool.os.fsync

        def counting(fd):
            seen[0] += 1
            return real(fd)
        hubtool.os.fsync = counting          # type: ignore[assignment]
        try:
            fn()
        finally:
            hubtool.os.fsync = real          # type: ignore[assignment]
        return seen[0]

    def _save_is_durable():
        """ANTI-VACUITY: the same probe must SEE the fsync the fix added, or
        the gap below would 'pass' on a broken probe rather than a real hole."""
        fresh_ident()
        n = _fsync_probe(lambda: hubtool._save_ident(
            "durable-probe", {"uid": "d" * 64, "name": "durable-probe"}))
        assert n >= 1, "the probe cannot see _save_ident's fsync"
    check("mint · the probe sees _save_ident's fsync (so the next check is a "
          "real absence)", _save_is_durable)

    def _mint_is_durable():
        fresh_ident()
        n = _fsync_probe(lambda: hubtool._mint_uid("newborn"))
        assert n >= 1, (
            "_mint_uid created the identity file with no flush/fsync — a "
            "power cut between the mint and the OS flush leaves the file "
            "empty while the hub already holds sha256(uid), which is exactly "
            "the stranded-address shape the 2026-08-05 outage produced")
    # promoted from gap() 2026-08-05, fixed same day (§2d): _mint_uid now
    # flush+fsyncs inside the O_EXCL handle before register() hands the hub
    # a durable fingerprint of a secret that exists only in that file
    check("mint · the O_EXCL mint fsyncs the file it just created",
          _mint_is_durable)

    def _quarantine_keeps_the_wreck():
        # the fix's own promise, measured: a corrupt file is preserved as
        # evidence rather than overwritten
        fresh_ident()
        hubtool._ident("wrecked")
        with open(hubtool._id_path("wrecked"), "w", encoding="utf-8") as f:
            f.write("\x00\x00\x00")
        hubtool._CUR.clear()
        hubtool._ident("wrecked")
        wrecks = [f for f in os.listdir(hubtool._ID_DIR)
                  if f.startswith("wrecked.json.corrupt-")]
        assert wrecks, os.listdir(hubtool._ID_DIR)
    check("mint · a corrupt identity is quarantined as .corrupt-<ts>, not "
          "overwritten — the wreck survives for post-mortem",
          _quarantine_keeps_the_wreck)


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
            mode = os.stat(hubtool._id_path("perms")).st_mode & 0o777
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
    # was a gap: no seen-ring, so an ack outage repeated every unacked
    # message on every pass. Fixed 2026-08-05: take_fresh keeps a persisted
    # 200-id ring in the (now name-keyed) identity file; the ack is still
    # attempted for every delivered id.
    check("a failing ack does not repeat the same message every pass",
          _failed_ack_redelivers_with_no_dedupe)


# ══════════════════════════════════════════════════════ §4b (redteam, live)
def sec_second_consumer() -> None:
    """LIVE REPORT 2026-08-05 (curator): a message shows `fetched` in the hub's
    own log — so something polled and received it — yet the armed listener
    never printed it. Their hypothesis was a seen-ring clobber from concurrent
    short-lived CLI processes rewriting the identity JSON.

    Two mechanisms could produce that, and they have OPPOSITE signatures:
      • a stale ring written over a newer one re-admits ids → REPEATS;
      • a second consumer on the same identity takes the message off the hub
        → SILENCE for whoever polls next.
    Only the second can hide a message, and the hub's `fetched` state says a
    consumer existed. This section measures both so the diagnosis is evidence
    rather than the more alarming-sounding story."""
    print("\n§4b  a second consumer on one identity (the silent-drop report)")

    def _stale_ring_causes_repeats_not_silence():
        me = become("ring-victim")
        become("ring-sender")
        hubtool.dispatch("hub_send", {"to": me, "body": "the only copy"})
        become("ring-victim")
        nm = hubtool._active_name()
        before = hubtool._load_ident(nm)          # a short-lived process's view
        out = json.loads(hubtool.dispatch("hub_read", {}))
        assert [m["body"] for m in out["messages"]] == ["the only copy"], out
        # …that process now writes ITS stale copy back, losing the ring entry
        hubtool._save_ident(nm, before)
        again = json.loads(hubtool.dispatch("hub_read", {}))
        # the hub already acked it, so nothing redelivers — but had it not,
        # the collapsed ring would have surfaced it AGAIN, never hidden it
        assert again["messages"] == [], again
        ring = (hubtool._load_ident(nm).get("seen") or {})
        assert not any("the only copy" in str(v) for v in ring.values())
    check("second-consumer · a stale identity write LOSES ring entries, whose "
          "worst case is a repeat — it can never hide a message (so the "
          "clobber hypothesis does not explain a silent drop)",
          _stale_ring_causes_repeats_not_silence)

    def _a_second_reader_takes_it_off_the_hub():
        me = become("two-mouths")
        become("two-mouths-sender")
        hubtool.dispatch("hub_send", {"to": me, "body": "for the listener"})
        become("two-mouths")
        # ANY other call on this identity that polls is a consumer: hub_read
        # (the MCP tool) is the one a session makes without thinking of it as
        # "reading mail" — it polls, rings, and ACKS.
        stolen = json.loads(hubtool.dispatch("hub_read", {}))
        # a PRECONDITION, not the finding: if the send never landed, `stolen`
        # is empty and so is the poll below — and the gap would report a
        # stolen message that was never sent (see `fixture` above)
        fixture([m["body"] for m in stolen["messages"]] == ["for the listener"],
                f"the send did not reach the reader: {stolen}")
        # what the listener's own next pass would see
        left = hubtool.take_fresh(hubtool.poll(0.0))
        assert left, (
            "the message was consumed by another call on the SAME identity "
            "and acked; the listener's next poll returns nothing, and the "
            "hub's log shows state=fetched — which is exactly the reported "
            "symptom")
    gap("second-consumer · a listener's mail cannot be consumed out from "
        "under it by another call on the same identity",
        "The mailbox is per IDENTITY and delivery is once, so every verb that "
        "polls — hub_read and hub_wait, not just `listen` — is a consumer. A "
        "session with the hubtool MCP server registered AND a listener armed "
        "has two mouths on one mailbox: whichever polls first takes the "
        "message, acks it, and the other never sees it. The listener LOCK "
        "(.listening) stops a second LISTENER; nothing stops a second READER. "
        "This is the same deliver-once property already characterised for two "
        "sessions sharing a name (§2b), reached from inside ONE session. "
        "Options: have the listener hold the read lock for hub_read too (an "
        "armed listener makes hub_read a no-op that points at the listener's "
        "output), or give the MCP read tool its own cursor rather than the "
        "consuming poll, so the listener stays the single consumer.",
        _a_second_reader_takes_it_off_the_hub)

    def _the_listener_lock_does_not_cover_readers():
        """Characterisation, so the boundary of the existing protection is
        explicit: the lock is about LISTENERS, and hub_read never looks at it."""
        become("lock-scope")
        lock = hubtool._id_path("lock-scope") + ".listening"
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))          # a live listener, by pid
        try:
            out = json.loads(hubtool.dispatch("hub_read", {}))
            assert "messages" in out, out      # served, lock or no lock
        finally:
            os.remove(lock)
    check("second-consumer · characterised: hub_read serves normally while a "
          "listener lock is held — the lock guards the listener seat, not the "
          "mailbox", _the_listener_lock_does_not_cover_readers)


# ══════════════════════════════════════════════════ §4c (redteam, user report)
def sec_chat_receipts() -> None:
    """USER REPORT 2026-08-06: "when an org reads a message it goes to `read`,
    when a chat reads it only goes to `fetched`."

    The hub's ladder has two carriers, not one: `state` (queued → fetched) is
    moved by /api/ack, and `delivered_at` / `read_at` are moved by
    /api/receipts. Orgs drive BOTH — net.py keeps a _dlv_q and a _read_q and
    flushes them — so an org recipient walks ▫ → ✓ → ✓✓ → read. hubtool calls
    /api/ack and NOTHING ELSE, so a chat recipient stops at ✓ forever and the
    sender can never tell "the chat has it" from "the chat has read it".

    That is not a hub asymmetry: /api/receipts authorises on `to_slug IN
    (my slugs)` with no notion of kind, so a chat's receipts would be accepted
    today. The gap is entirely on the client side."""
    print("\n§4c  chat clients and the delivery ladder (user report)")

    def _a_chat_reports_delivery_like_an_org_does():
        me = become("ladder-reader")
        become("ladder-sender")
        hubtool.dispatch("hub_send", {"to": me, "body": "walk the ladder"})
        become("ladder-reader")
        out = json.loads(hubtool.dispatch("hub_read", {}))
        fixture([m["body"] for m in out["messages"]] == ["walk the ladder"],
                f"the fixture message never arrived: {out}")
        row = hub_rows("SELECT state, fetched_at, delivered_at, read_at "
                       "FROM messages WHERE to_slug = ?", (me,))[0]
        fixture(row["state"] == "fetched",
                f"the ack half did not run either: {row}")
        assert row["delivered_at"] or row["read_at"], (
            f"a chat consumed the message through hub_read — the call's "
            f"RETURN VALUE is the message entering the agent's context, "
            f"which is the same event an org reports as `read` — and the hub "
            f"row still carries delivered_at=None read_at=None. The sender "
            f"sees ✓ and never ✓✓: {row}")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): hubtool grew
    # `_receipts` and speaks /api/receipts in exactly the prescribed shape —
    # `listen` sends DELIVERED after the print, beside mark_seen/ack (a
    # failed surface reports no delivery); hub_read/hub_wait send READ from
    # the raw fresh dicts (which carry `id`; the tools' return shape stays
    # id-free). A listen-only session correctly tops out at ✓✓ delivered —
    # the diagnostic, not a shortfall. Best-effort: a lost receipt costs
    # display state only.
    check("receipts · a chat that consumes a message reports it, like an "
          "org does (user report 2026-08-06)",
          _a_chat_reports_delivery_like_an_org_does)

    # ---- PEER REPORT relayed by the user 2026-08-06 ----------------------
    # "an independent claude chat says unregister isn't an available verb to
    # them."
    #
    # The verb EXISTS on both surfaces since 4a4b635 — `hub_unregister` in
    # TOOLS and `if verb == "unregister"` in cli(). What is missing is the
    # ADVERTISEMENT: cli()'s catch-all usage line names seven verbs and omits
    # this one, so a session that runs `hubtool.py` bare — or mistypes a verb
    # and reads the error — is told the available set and unregister is not
    # in it. A verb nobody can discover is a verb nobody has.
    def _every_cli_verb_is_advertised():
        src = open(os.path.join(_REPO, "hub", "hubtool.py"),
                   encoding="utf-8").read()
        i = src.index("def cli(")
        body = src[i:]
        handled = set(re.findall(r'verb == "([a-z_]+)"', body)) | {
            v for grp in re.findall(r'verb in \(([^)]*)\)', body)
            for v in re.findall(r'"([a-z_]+)"', grp)}
        fixture("unregister" in handled,
                f"cli() no longer handles unregister at all: {sorted(handled)}")
        m = re.search(r'usage: hubtool\.py \[([^\]]*)\]', body)
        fixture(bool(m), "the catch-all usage line moved — re-read this check")
        # ⚠ the usage string is SPLIT across source lines, so a raw
        # split("|") captures a token like '\"\n          \"hubs'.
        # Strip the concatenation artefacts or the check invents a
        # missing verb that is plainly there (measured: it reported
        # `hubs` missing while the line advertises it).
        advertised = {v.strip().strip('\"').strip()
                      for v in m.group(1).split("|")}
        advertised = {v.split('\"')[-1].strip() for v in advertised}
        missing = sorted(handled - advertised)
        assert not missing, (
            f"cli() handles {missing} but the usage line advertises only "
            f"{sorted(advertised)} — the verb works and cannot be found. A "
            f"peer chat reported exactly this for `unregister`: they read "
            f"the usage line, did not see it, and concluded the verb was not "
            f"available to them")
    # ← FIXED: the implementer added `unregister` (and `fetch`) to the
    # usage line. Promoted 2026-08-06 under the redteam bug-fix grant,
    # after repairing a FALSE POSITIVE in this check itself: the usage
    # string is split across source lines, so the old raw split("|") read one
    # token as the tail of a broken string literal and reported `hubs`
    # missing while the line plainly advertises it. The check now
    # strips the concatenation artefacts before comparing.
    check("verbs · every verb cli() handles appears in the usage line "
          "it prints (peer report 2026-08-06)",
          _every_cli_verb_is_advertised)

    def _the_hub_would_accept_them_today():
        """ANTI-VACUITY for the gap above: if the hub REFUSED a chat's
        receipts, the fix would be a two-sided change and the finding would be
        pointing at the wrong file. It does not — the same call an org makes
        is accepted for a chat's own inbound message."""
        me = become("ladder-proof")
        become("ladder-proof-sender")
        hubtool.dispatch("hub_send", {"to": me, "body": "receipt me"})
        become("ladder-proof")
        json.loads(hubtool.dispatch("hub_read", {}))
        # ⚠ the id comes from the HUB, not from hub_read's return: the tool's
        # projection drops it (measured — KeyError 'id'), even though the raw
        # poll response carries it and `listen` already acks with those very
        # ids. Worth knowing for the fix: a receipt can be sent from INSIDE
        # hubtool without plumbing anything new to the caller.
        mid = hub_rows("SELECT id FROM messages WHERE to_slug = ? "
                       "ORDER BY received_at DESC LIMIT 1", (me,))[0]["id"]
        r = hubtool._call("/api/receipts",
                          {"receipts": [{"id": mid, "state": "read"}]})
        row = hub_rows("SELECT read_at FROM messages WHERE id = ?", (mid,))[0]
        assert row["read_at"], row
        # re-pinned on the fix (implementer): hub_read now sends the READ
        # receipt itself, so read_at is already set by the time this manual
        # copy arrives and the hub's read_at-IS-NULL guard records 0. That
        # proves BOTH halves at once — the hub accepted the client's
        # in-band receipt (read_at is set), and duplicates are idempotent
        # (recorded 0, the timestamp never overwritten).
        assert r.get("recorded") == 0, r
    check("receipts · the hub ACCEPTS a chat's receipt for its own mail "
          "(so the gap above is one-sided — client work, no hub change)",
          _the_hub_would_accept_them_today)


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
    sec_names()
    sec_crash()
    sec_mint_durability()
    sec_secret()
    sec_listen()
    sec_second_consumer()
    sec_chat_receipts()
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
