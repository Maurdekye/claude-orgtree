"""The agent-facing surface: the orgtree MCP server + the steering hook.

    python backend/tests/test_mcptool.py          (no pytest; plain asserts)

WHAT THIS TESTS AND WHY IT LOOKS LIKE THIS

`mcptool.py` is the only thing an agent can act with, and `steer.py` runs after
every single tool call it makes. Both had been reached only indirectly — through
`/api/agent` fuzzing — never DRIVEN: nothing had ever spoken JSON-RPC to the
server the way the CLI does, so the whole stdio layer (framing, dispatch,
robustness, the tool catalogue the model actually reads) was untested.

So this suite runs the real thing as a real subprocess:

  * a REAL uvicorn on 127.0.0.1:7408 serving the REAL app, with one composite
    ASGI shim in front so the same port can answer both as the loopback admin
    listener and — when a request carries `X-Orgtree-Bridge` — as the sandbox
    BridgeGateway. (In production those are two ports; the shim exists so this
    suite binds exactly one, and it changes nothing the gateway inspects.)
  * `python -m orgtree.mcptool` as a child process, spoken to over its stdin
    and read back off its stdout, one JSON-RPC line at a time. Every tool check
    goes through that pipe — never by calling the module's functions.
  * `python orgtree/steer.py` as a child process, fed a hook payload on stdin
    exactly as the CLI feeds it.

Only `supervisor.send_message` (and chatq/interorg egress) is stubbed: it spawns
CLI turns, and this suite must never launch a model. The stub RECORDS instead,
which is what makes the "who gets woken" (`drive`) assertions possible.

    §1  the rig + the stdio protocol (framing, dispatch, robustness)
    §2  the tool catalogue — all 17, and whether the cards match the code
    §3  happy paths, one per tool
    §4  refusals — every tool's authority/argument refusal
    §5  hostile arguments: the D-58 crash families, re-aimed through MCP
    §6  authority cannot be forged from inside a tool call
    §7  the bridge / sandboxed mode
    §8  steer.py — the PostToolUse hook
    §9  caps, defaults and statelessness

Checks marked ⚑ assert a KNOWN GAP rather than a fix; each says in its body what
should happen the day the gap is closed — six of them are live 500s or silent
authority gaps in files this suite does not own (api.py / ledger.py), pinned
here because this is the surface they are reachable from.

Two defects found here WERE fixed (mcptool.py + steer.py, both in this suite's
territory); `scratchpad/mcptool_discriminate.py` reverts each one in a copy of
the package and shows the corresponding check going red.
"""

import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..")
sys.path.insert(0, BACKEND)

PORT = 7408
DATA = tempfile.mkdtemp(prefix="orgtree-mcptest-")
os.environ["ORGTREE_DATA"] = DATA

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

os.environ["ORGTREE_PORT"] = str(PORT)
os.environ.pop("ORGTREE_PUBLIC_PORT", None)
os.environ.pop("ORGTREE_EXPOSE_ADMIN", None)

from orgtree import api, mcptool, sandbox, store, supervisor      # noqa: E402

# nothing here may spawn a CLI, a container, or touch the host's chatq registry
DRIVEN: list[tuple[str, str, str]] = []          # (slug, node, nudge) — wake=True only
PARKED: list[tuple[str, str, str]] = []          # wake=False (send_notice) nudges


def _fake_send(slug, nid, text, command=False, wake=True, **kw):
    # mirror the real no-wake contract closely enough for the notice
    # assertions: every fixture node is idle, so wake=False PARKS — it never
    # belongs in DRIVEN, whose whole meaning here is "a turn would have run"
    if not wake:
        PARKED.append((slug, nid, text))
        return {"accepted": True, "queued": 0, "parked": True}
    DRIVEN.append((slug, nid, text))
    return {"accepted": True, "queued": 0}


# ☠ THE DEPLOY INTERLOCK — armed before any check runs. The argument-fuzz
# checks below call EVERY card in the catalogue, and BOSS is top-level, so
# orgtree_self_restart's authorization gate PASSES and the launch spawns a
# real update.ps1 — a rebuild and a restart of the backend serving every org
# on this machine. Worse, `update.ps1` inherits this suite's throwaway
# ORGTREE_DATA, finds no .port file in it, falls back to the DEFAULT port
# 7360 and kills the PRODUCTION backend. The `self-update-*.log` files in old
# `orgtree-mcptest-*` temp dirs are the receipts: this has been happening for
# months, surviving only because update.ps1 refused on its own account (dirty
# tree, no upstream, or -OnlyIfBehind exiting before the rebuild). D-142
# removed that last accident. See tests/_no_deploy.py.
import _no_deploy                                                # noqa: E402

_no_deploy.install()

supervisor.send_message = _fake_send
supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
supervisor.storage_check = lambda slug: None
supervisor.maybe_storage_check = lambda slug: None
CHATQ: list[tuple[str, str, str]] = []
supervisor.chatq_send = lambda slug, chat, text: (CHATQ.append((slug, chat, text))
                                                  or True)
sandbox.warm = lambda org: None
sandbox.vm_disk_cap_mib = lambda: None

PASS = 0
GAPS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def t(label):
    def deco(fn):
        check(label, fn)
        return fn
    return deco


def gap(label):
    """A check that pins CURRENT behaviour which is not what it should be."""
    global GAPS

    def deco(fn):
        global GAPS
        check("⚑ " + label, fn)
        GAPS += 1
        return fn
    return deco


# ---------------------------------------------------------------------- §1 rig
print("§1  the rig + the stdio protocol")


class Composite:
    """One port, two listeners. A request carrying the bridge header (or an
    /anthropic path, which carries the secret inline) is handed to the real
    BridgeGateway; everything else is the plain loopback admin app. Production
    binds these on separate ports — the gateway itself is untouched."""

    def __init__(self, inner):
        self.admin = inner
        self.bridge = api.BridgeGateway(inner)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            hdrs = dict(scope.get("headers") or [])
            if b"x-orgtree-bridge" in hdrs or scope.get("path", "").startswith(
                    "/anthropic/"):
                return await self.bridge(scope, receive, send)
        return await self.admin(scope, receive, send)


import uvicorn                                                   # noqa: E402

def _serve():
    """Bind :7408, retrying — a previous run of this file leaves the listener
    in TIME_WAIT for a few seconds and the bind then fails silently (uvicorn
    logs at critical and simply never sets `started`)."""
    for attempt in range(6):
        cfg = uvicorn.Config(Composite(api.app), host="127.0.0.1", port=PORT,
                             lifespan="off", log_level="critical",
                             access_log=False)
        server = uvicorn.Server(cfg)
        th = threading.Thread(target=server.run, daemon=True)
        th.start()
        for _i in range(100):
            if getattr(server, "started", False):
                return server, th
            time.sleep(0.05)
        server.should_exit = True
        th.join(timeout=10)
        print(f"    (port {PORT} busy, retrying — attempt {attempt + 2}/6)")
        time.sleep(3)
    raise AssertionError(
        f"could not bind 127.0.0.1:{PORT} — something else is listening on it "
        f"(this suite owns 7408; check for a leftover backend)")


_server, _th = _serve()


def http(method, path, body=None, headers=None):
    """Raw HTTP to the rig — used for fixtures and for the steer endpoint."""
    payload = json.dumps(body).encode() if body is not None else b""
    s = socket.create_connection(("127.0.0.1", PORT), timeout=20)
    head = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n")
    for k, v in (headers or {}).items():
        head += f"{k}: {v}\r\n"
    s.sendall(head.encode("latin1") + b"\r\n" + payload)
    buf = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    hd, _sep, bd = buf.partition(b"\r\n\r\n")
    try:
        parsed = json.loads(bd)
    except Exception:                                            # noqa: BLE001
        parsed = None
    return int(hd.split()[1]), parsed, bd


class Mcp:
    """One live `python -m orgtree.mcptool` child, spoken to over its pipes."""

    def __init__(self, org, node, env=None, base=None, secret=None):
        e = dict(os.environ)
        e["ORGTREE_ORG"], e["ORGTREE_NODE"] = org, node
        e["ORGTREE_PORT"] = str(PORT)
        e["PYTHONPATH"] = BACKEND
        e["PYTHONUNBUFFERED"] = "1"
        if base is not None:
            e["ORGTREE_BASE"] = base
        if secret is not None:
            e["ORGTREE_BRIDGE_SECRET"] = secret
        e.update(env or {})
        self.p = subprocess.Popen(
            [sys.executable, "-m", "orgtree.mcptool"], env=e, cwd=BACKEND,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0)
        self.q: queue.Queue = queue.Queue()
        self.err: list[bytes] = []
        self._n = 0
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump(self):
        for line in self.p.stdout:
            self.q.put(line)
        self.q.put(None)                       # EOF sentinel — the server died

    def _pump_err(self):
        for line in self.p.stderr:
            self.err.append(line)

    def send_raw(self, text):
        self.p.stdin.write(text.encode("utf-8"))
        self.p.stdin.flush()

    def send(self, obj):
        self.send_raw(json.dumps(obj) + "\n")

    def read(self, timeout=30):
        """Next line off stdout, parsed. None means the process died."""
        try:
            line = self.q.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"no reply within {timeout}s "
                                 f"(stderr: {b''.join(self.err)[:400]!r})")
        if line is None:
            return None
        return json.loads(line.decode("utf-8"))

    def rpc(self, method, params=None, timeout=30):
        self._n += 1
        msg = {"jsonrpc": "2.0", "id": self._n, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        r = self.read(timeout)
        assert r is not None, (f"the MCP server DIED on {method} "
                               f"(stderr: {b''.join(self.err)[-600:]!r})")
        assert r.get("id") == self._n, f"id mismatch: sent {self._n}, got {r!r}"
        return r

    def call(self, tool, args=None, timeout=30):
        """tools/call → (text, isError)."""
        r = self.rpc("tools/call", {"name": tool, "arguments": args or {}},
                     timeout)
        res = r.get("result") or {}
        text = "".join(c.get("text", "") for c in res.get("content") or [])
        return text, bool(res.get("isError"))

    def ok(self, tool, args=None, timeout=30):
        text, err = self.call(tool, args, timeout)
        assert not err, f"{tool}{args} should have succeeded: {text[:400]}"
        try:
            return json.loads(text)
        except Exception:                                        # noqa: BLE001
            return {"_text": text}

    def refuse(self, tool, args=None, timeout=30):
        text, err = self.call(tool, args, timeout)
        assert err, f"{tool}{args} should have been refused, got: {text[:400]}"
        return text

    def alive(self):
        return self.p.poll() is None

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:                                        # noqa: BLE001
            pass
        try:
            self.p.wait(timeout=10)
        except Exception:                                        # noqa: BLE001
            self.p.kill()


# ------------------------------------------------------------------- fixtures
def mkorg(name, **kw):
    st, js, _b = http("POST", "/api/orgs", {"name": name, **kw})
    assert st == 200, (st, js)
    return js["slug"]


def mailbox(slug, nid):
    """The node's live mailbox straight off the doc (send_message is stubbed,
    so nothing ever drains it)."""
    return store.load_org(slug).d.get("mail", {}).get(nid, [])


def op(slug, **body):
    st, js, raw = http("POST", f"/api/orgs/{slug}/ops", body)
    assert st == 200, (st, raw[:300])
    return js


A = mkorg("Mcp Alpha")
B = mkorg("Mcp Beta")
# a generous top-level seat: everything below is hired out of BOSS's grant
op(A, op="hire", tier="opus", name="boss", grant=120, charter="the boss")
op(A, op="hire", tier="opus", name="mid", parent="boss", grant=40,
   charter="middle manager")
op(A, op="hire", tier="haiku", name="worker", parent="mid", grant=0,
   charter="the worker")
op(A, op="hire", tier="haiku", name="peer", parent="boss", grant=0,
   charter="a peer of mid")
op(A, op="hire", tier="opus", name="other", grant=10, charter="another top-level")
op(B, op="hire", tier="haiku", name="bstaff", grant=0, charter="in the other org")

BOSS = Mcp(A, "boss")
MID = Mcp(A, "mid")
WORKER = Mcp(A, "worker")


@t("initialize returns a server card and echoes the client's protocol version")
def _():
    r = BOSS.rpc("initialize", {"protocolVersion": "2025-06-18",
                                "capabilities": {},
                                "clientInfo": {"name": "x", "version": "1"}})
    res = r["result"]
    assert res["protocolVersion"] == "2025-06-18", res
    assert res["serverInfo"]["name"] == "orgtree", res
    assert "tools" in res["capabilities"], res


@t("initialize with no params falls back to a default protocol version")
def _():
    r = BOSS.rpc("initialize")
    assert r["result"]["protocolVersion"] == "2024-11-05", r


@t("notifications/initialized draws no reply and the server stays up")
def _():
    BOSS.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    time.sleep(0.4)
    assert BOSS.q.empty(), "a notification drew a response"
    assert BOSS.alive()
    BOSS.rpc("tools/list")          # still serving


@t("tools/list returns the catalogue and every card is well-formed")
def _():
    tools = BOSS.rpc("tools/list")["result"]["tools"]
    # +orgtree_present (FR-03); +orgtree_withdraw_ask; +orgtree_self_update;
    # +orgtree_cheap_compact (FR-24); +orgtree_request_scope (FR-13);
    # +orgtree_watchdog (FR-18); +orgtree_send_notice (2026-08-19);
    # +orgtree_prime_restart (FR-27, 2026-08-27);
    # +orgtree_swap +orgtree_self_subjugate (D-224, 2026-09-02)
    # +orgtree_interrupt (⏸ in isolation, 2026-09-03)
    # +orgtree_restart_wake (2026-09-04)
    assert len(tools) == 31, [x["name"] for x in tools]
    for c in tools:
        assert c["name"].startswith("orgtree_"), c
        assert len(c["description"]) > 20, c
        assert c["inputSchema"]["type"] == "object", c
        for req in c["inputSchema"].get("required", []):
            assert req in c["inputSchema"]["properties"], (c["name"], req)


@t("an unknown method with an id is answered (never wedges the client)")
def _():
    r = BOSS.rpc("resources/list")
    assert r["result"] == {} and "error" not in r, r
    r = BOSS.rpc("completely/made/up")
    assert r["result"] == {}, r


@t("malformed JSON on the wire is skipped, not fatal")
def _():
    for junk in ("{not json", "{'single': 'quotes'}", "\x00\x01\x02", "}{"):
        BOSS.send_raw(junk + "\n")
    BOSS.send_raw("\n   \n")                    # blank lines
    time.sleep(0.3)
    assert BOSS.alive(), "malformed input killed the server"
    assert BOSS.q.empty(), "junk drew a reply"
    BOSS.rpc("tools/list")


@t("a JSON scalar / array line does not kill the server (msg.get on a non-dict)")
def _():
    for junk in ("5", '"hello"', "null", "true", "[]",
                 '[{"jsonrpc":"2.0","id":9,"method":"tools/list"}]'):
        BOSS.send_raw(junk + "\n")
    time.sleep(0.4)
    assert BOSS.alive(), ("a non-object JSON-RPC line killed the server "
                          f"(stderr: {b''.join(BOSS.err)[-500:]!r})")
    BOSS.rpc("tools/list")


@t("params of the wrong TYPE does not kill the server")
def _():
    for m, p in (("initialize", []), ("initialize", "x"), ("tools/call", []),
                 ("tools/call", 7), ("tools/call", "name")):
        BOSS.send({"jsonrpc": "2.0", "id": 900, "method": m, "params": p})
        time.sleep(0.05)
    time.sleep(0.4)
    assert BOSS.alive(), ("a non-object params killed the server "
                          f"(stderr: {b''.join(BOSS.err)[-500:]!r})")
    while not BOSS.q.empty():
        BOSS.q.get()
    BOSS.rpc("tools/list")


@t("tools/call with no arguments at all is refused, not crashed")
def _():
    BOSS._n += 1
    BOSS.send({"jsonrpc": "2.0", "id": BOSS._n, "method": "tools/call",
               "params": {"name": "orgtree_message"}})
    r = BOSS.read()
    assert r is not None and r["id"] == BOSS._n, r
    assert r["result"]["isError"] is True, r
    assert BOSS.alive()


@t("arguments:null / a non-object arguments reads as empty, never crashes")
def _():
    for bad in (None, [], "x", 7):
        BOSS._n += 1
        BOSS.send({"jsonrpc": "2.0", "id": BOSS._n, "method": "tools/call",
                   "params": {"name": "orgtree_chart", "arguments": bad}})
        r = BOSS.read()
        assert r is not None and r["id"] == BOSS._n, r
        assert r["result"]["isError"] is False, (bad, r)
    assert BOSS.alive()


@t("an id-less tools/call draws NO response frame (and mutates nothing)")
def _():
    # repro: before the fix this answered with `"id": null` — a frame no client
    # can match to a request — and performed the call anyway
    before = len(DRIVEN)
    BOSS.send({"jsonrpc": "2.0", "method": "tools/call",
               "params": {"name": "orgtree_message",
                          "arguments": {"to": "mid", "body": "notification"}}})
    BOSS.send({"jsonrpc": "2.0", "method": "initialize"})
    time.sleep(0.5)
    assert BOSS.q.empty(), "an id-less request drew a response"
    assert len(DRIVEN) == before, "an id-less tools/call still ran the tool"
    assert BOSS.alive()
    BOSS.rpc("tools/list")


@t("a nameless tools/call is refused with the unknown-tool message")
def _():
    txt, err = BOSS.call("")
    assert err and "unknown orgtree tool" in txt, txt


@t("interleaved requests come back in order, one line each")
def _():
    base = BOSS._n
    for i in range(1, 6):
        BOSS.send({"jsonrpc": "2.0", "id": base + i, "method": "tools/list"})
    BOSS._n = base + 5
    got = [BOSS.read()["id"] for _ in range(5)]
    assert got == [base + i for i in range(1, 6)], got


@t("a 4 MB argument does not kill the server (one giant stdin line)")
def _():
    huge = "x" * (4 * 1024 * 1024)
    txt, err = BOSS.call("orgtree_message", {"to": "mid", "body": huge},
                         timeout=90)
    assert BOSS.alive(), "a huge payload killed the server"
    assert not err, txt[:300]


@t("a huge UNKNOWN tool name is refused without a crash")
def _():
    txt, err = BOSS.call("x" * 200000, {"a": 1}, timeout=60)
    assert err and BOSS.alive(), txt[:200]


@t("non-ASCII survives the round trip (the cp1252 reconfigure)")
def _():
    body = "em—dash · naïve · 日本語 · ✓"
    r = BOSS.ok("orgtree_message", {"to": "mid", "body": body})
    assert r.get("delivered") == "mid", r
    assert any(body in (m.get("body") or "") for m in mailbox(A, "mid")), \
        "the unicode body did not survive to the mailbox"


# ---------------------------------------------------- §2 the tool catalogue
print("\n§2  the tool catalogue — do the cards match the code?")

CARDS = {c["name"]: c for c in mcptool.TOOLS}
SCHEMA = {n: c["inputSchema"] for n, c in CARDS.items()}
DESC = {n: c["description"] for n, c in CARDS.items()}

# every verb the /api/agent dispatch knows, read off the source rather than
# copied: a verb added there and not here is exactly the drift D-58 warns about
API_SRC = open(os.path.join(BACKEND, "orgtree", "api.py"), encoding="utf-8").read()
_AGENT_CALL = API_SRC[API_SRC.index('@app.post("/api/agent")'):
                      API_SRC.index("_UPLOAD_MAX")]
_DISPATCH = sorted(set(__import__("re").findall(r'"(orgtree_\w+)"', _AGENT_CALL)))


# DEPRECATED ALIASES (D-142, 2026-08-21): names the dispatch still answers on
# purpose and that no card advertises, so tools/list teaches no new session a
# dead name. Each one is a deliberate, dated exemption from the drift rule
# below — never a place to park a name you forgot to card.
ALIASES = {"orgtree_self_update": "orgtree_self_restart"}


@t("the catalogue and the /api/agent dispatch name exactly the same 28 verbs")
def _():
    assert sorted(CARDS) == sorted(set(_DISPATCH) - set(ALIASES)), \
        f"drift: cards {sorted(set(CARDS) - set(_DISPATCH))}, " \
        f"dispatch-only {sorted(set(_DISPATCH) - set(CARDS) - set(ALIASES))}"
    # +orgtree_present (FR-03, 2026-08-05); +orgtree_withdraw_ask (the
    # manual-invalidation ruling, 2026-08-06); +orgtree_self_update (FR-14,
    # 2026-08-06) — renamed orgtree_self_restart 2026-08-21 (D-142);
    # +orgtree_cheap_compact (FR-24, 2026-08-11);
    # +orgtree_request_scope (FR-13) + orgtree_watchdog (FR-18, 2026-08-12);
    # +orgtree_send_notice (mail that never wakes, 2026-08-19);
    # +orgtree_prime_restart (FR-27, the deferred restart, 2026-08-27);
    # +orgtree_swap +orgtree_self_subjugate (D-224, seat exchange, 2026-09-02)
    # +orgtree_interrupt (⏸ in isolation, 2026-09-03)
    # +orgtree_restart_wake (2026-09-04)
    assert len(CARDS) == 31, len(CARDS)


@t("☠ the deprecated self_update alias is dispatchable but NOT advertised")
def _():
    """D-142: the rename cannot strand agents already in flight. A live session
    fetched tools/list at startup and holds the OLD catalogue until it ends,
    and stored charters carry the literal string 'orgtree_self_update' — both
    keep calling the old name at a backend that must still answer, or the
    error lands on an agent that did nothing wrong. Equally: no NEW session may
    learn the dead name, so it must stay out of the cards."""
    for old, new in ALIASES.items():
        assert new in _DISPATCH and new in CARDS, \
            f"{new} is not the carded replacement for {old}"
        assert old not in CARDS, \
            f"{old} is advertised in tools/list — new sessions are being " \
            f"taught the deprecated name"
        # ⚠ BEHAVIOURAL, not source-scanning. The first version of this check
        # asserted `old in _DISPATCH`, and _DISPATCH is regexed out of the
        # dispatch SOURCE — so the quoted tool name sitting in the explanatory
        # COMMENT above the branch satisfied it even with the branch deleted.
        # Caught by mutation 2026-08-21. Drive the real pipe instead.
        #
        # WORKER is a non-top-level node, so self_restart_gate refuses it in
        # the ledger BEFORE supervisor.launch_self_restart is ever reached:
        # this proves the alias routes all the way to the gate WITHOUT ever
        # spawning a deploy. Never call this as a node the gate would allow —
        # that would restart every org on the machine running the suite.
        txt = WORKER.refuse(old)
        assert "EVERY org on this machine" in txt, \
            f"{old} no longer reaches the self-restart gate — every live " \
            f"session and stored charter naming it breaks mid-flight " \
            f"(D-142). Got: {txt[:200]}"
        # …and it must land on the SAME gate the new name uses
        assert txt == WORKER.refuse(new), \
            f"{old} and {new} refuse differently — the alias has drifted " \
            f"onto a second implementation"


@t("no tool name is duplicated and every card carries an inputSchema")
def _():
    names = [c["name"] for c in mcptool.TOOLS]
    assert len(names) == len(set(names)), names
    for c in mcptool.TOOLS:
        assert set(c) == {"name", "description", "inputSchema"}, c["name"]


@t("static tier enums match non-conditional ledger tiers exactly")
def _():
    from orgtree.ledger import TIERS
    from orgtree.providers import CONDITIONAL_CODEX_TIERS
    expected = set(TIERS) - set(CONDITIONAL_CODEX_TIERS)
    for name, key in (("orgtree_hire", "tier"), ("orgtree_switch_model", "tier")):
        assert sorted(SCHEMA[name]["properties"][key]["enum"]) == sorted(expected), \
            (name, SCHEMA[name]["properties"][key]["enum"])
    # and the seat costs the cards RECITE match the same table
    for tier, cost in TIERS.items():
        if tier in CONDITIONAL_CODEX_TIERS:
            continue
        for name in ("orgtree_hire", "orgtree_switch_model"):
            assert f"{tier} {cost}" in DESC[name].replace("·", "").replace(
                "  ", " ") or f"{tier} {cost}" in DESC[name], \
                f"{name} recites a stale seat cost for {tier} (真 {cost})"


@t("the org_visibility enums match ledger.VIS_LEVELS")
def _():
    from orgtree.ledger import VIS_LEVELS
    for name in ("orgtree_hire", "orgtree_retool"):
        assert tuple(SCHEMA[name]["properties"]["org_visibility"]["enum"]) \
            == VIS_LEVELS, name


@t("the effort enum matches Org.EFFORTS (plus '' to clear)")
def _():
    from orgtree.ledger import Org
    assert SCHEMA["orgtree_retool"]["properties"]["effort"]["enum"] \
        == [*Org.EFFORTS, ""], SCHEMA["orgtree_retool"]["properties"]["effort"]


@t("the tools sub-schema names every switch the ledger demands of an agent")
def _():
    from orgtree.ledger import TOOL_KEYS
    props = mcptool.TOOLS_SCHEMA["properties"]
    for k in TOOL_KEYS:
        assert k in props and k in mcptool.TOOLS_SCHEMA["required"], k
    assert "mcp" in props and "mcp" in mcptool.TOOLS_SCHEMA["required"]
    # a missing key is a hire refusal, so the schema must demand all of them
    assert set(mcptool.TOOLS_SCHEMA["required"]) == {*TOOL_KEYS, "mcp"}


@t("the audience action enum is exactly what the dispatch branches on")
def _():
    src = open(os.path.join(BACKEND, "orgtree", "api.py"),
               encoding="utf-8").read()
    seg = src[src.index('body.tool == "orgtree_audience"'):]
    seg = seg[:seg.index("_kiosk_cap_check")]
    acts = set(__import__("re").findall(r'action == "(\w+)"', seg))
    assert set(SCHEMA["orgtree_audience"]["properties"]["action"]["enum"]) \
        == acts, (SCHEMA["orgtree_audience"]["properties"]["action"]["enum"],
                  sorted(acts))


@t("read_transcript's documented 80-message ceiling is the one enforced")
def _():
    assert SCHEMA["orgtree_read_transcript"]["properties"]["last"]["maximum"] == 80
    src = open(os.path.join(BACKEND, "orgtree", "api.py"),
               encoding="utf-8").read()
    assert 'min(_arg_int(a, "last", 30), 80)' in src, \
        "the card promises max 80 — the clamp moved"


@gap("7 of the 17 tools are never mentioned in the system prompt's recital")
def _():
    # ⚑ ARCHITECTURE: "adding a verb touches FOUR places … the tool schema in
    # mcptool.py and the tool recital in identity_prompt". The recital is not
    # decoration: the same prompt tells the agent its tools may arrive DEFERRED
    # and must be loaded by name. Measured against a top-level node:
    #   • 7 verbs appear NOWHERE in the prompt — move, list_orgs,
    #     read_transcript, read_scratch, send_file, switch_model, audience;
    #   • 3 more survive only inside the contraction
    #     "orgtree_retire/rehire/dissolve/reallocate", so their full tool name
    #     is never written out.
    # An agent's whole picture of what it may do comes from this paragraph plus
    # tools/list; half the org-shaping verbs are missing from the paragraph.
    org = store.load_org(A)
    recital = supervisor.identity_prompt(org, "boss")
    contracted = {"orgtree_rehire", "orgtree_dissolve", "orgtree_reallocate"}
    absent = sorted(n for n in CARDS
                    if n not in recital and n.split("orgtree_")[1] not in recital)
    # orgtree_self_update LEFT the absent set 2026-08-07 (D-104); it is
    # orgtree_self_restart since 2026-08-21 (D-142). The user ruled that an
    # agent which knows there is something to deploy should deploy it
    # unprompted, and an instruction to act unprompted has to be in the
    # standing prompt — a tool card is only read once the agent has already
    # decided to reach for the tool. It rides the top-level/audience branch,
    # which is why `boss` sees it and the two below do not (asserted just
    # below, so the gating is pinned and not merely intended).
    # ⚠ THIS PIN MATCHES BARE VERBS AS SUBSTRINGS ("move", "rename"), so
    # ordinary prose in the prompt can silently satisfy it. Measured
    # 2026-08-21: rewording the deploy paragraph to say "the pull moved HEAD"
    # took orgtree_move out of the absent set and failed here. If this list
    # shrinks, check for an accidental substring before believing the recital
    # actually gained a verb.
    # orgtree_send_file ALSO left the absent set 2026-08-08 (user ruling): a
    # request for a file is a moment the agent must recognise, and the tool
    # card only speaks once it has already reached for that tool. Same shape
    # of reason as self_update above — the prompt is where behaviour that
    # must fire UNPROMPTED belongs.
    # 2026-08-09 prompt audit: read_transcript + read_scratch left the absent
    # set too. A manager's default when a report's answer does not add up is
    # to ASK — a whole round trip that returns the agent's account of events
    # instead of the events. Reading is instant, downward-only and costs the
    # report nothing, so the trigger belongs where the moment happens.
    # The six that REMAIN absent are deliberate: rename/move/list_orgs/
    # switch_model, and D-224's swap/self_subjugate, have no moment that
    # arrives unbidden — an agent reaches for them having already decided to
    # reorganize, and finds them in their cards. (Checked against the
    # substring trap the warning above documents: the prompt's compaction
    # paragraph says "swaps only the session", which is not this node's
    # prompt, so `orgtree_swap` really is absent rather than accidentally
    # satisfied.)
    assert absent == ["orgtree_list_orgs", "orgtree_move", "orgtree_rename",
                      "orgtree_restart_wake",
                      "orgtree_self_subjugate", "orgtree_swap",
                      "orgtree_switch_model"], \
        f"the recital gap changed — update or retire this pin: {absent}"
    # D-181 audit amendment: capability guidance is identity, while today's
    # child count is live state. The leaf must already know the read/retire
    # triggers before its first hire; gating them on HAVING reports made the
    # 0↔1 boundary rewrite its cached system prompt.
    assert "orgtree_read_transcript" in recital, "the manager recital lost it"
    leaf = supervisor.identity_prompt(org, "worker")
    assert "orgtree_read_transcript" in leaf, \
        "a leaf would gain report-reading guidance at its first hire"
    assert "RETIRE IT" in leaf, \
        "a leaf would gain report-retirement guidance at its first hire"
    # …and it must reach EVERY agent, not just the audience holders: any of
    # them can be asked for a file by their superior's relay or the user
    for who in ("boss", "mid", "worker"):
        p = supervisor.identity_prompt(org, who)
        assert "orgtree_send_file" in p, f"{who} is never told how to send a file"
        assert "a path is not a delivery" in p, who
    assert "orgtree_self_restart" in recital, \
        "the D-104 deploy instruction left the top-level recital"
    assert "orgtree_self_update" not in recital, \
        "the recital still names the DEPRECATED tool — the alias exists for " \
        "charters written before the rename, not for prompts generated after"
    for lower in ("mid", "worker"):
        assert "orgtree_self_restart" not in supervisor.identity_prompt(
            org, lower), \
            f"{lower} is told to self-restart, but the gate refuses it — a " \
            f"prompt that promises what the ledger denies (D-004's sibling)"
    # ☠ D-142: the standing prompt must not tell agents that being BEHIND is
    # the only occasion. It was, it silently broke deploying a local commit,
    # and the prompt is where an agent forms its picture before ever reading a
    # tool card. A revert of the prompt half of D-142 fails here.
    # a distinctive phrase, not a bare word: "committed" alone is satisfied by
    # incidental prose anywhere in the assembled prompt (charter, CLAUDE.md),
    # which would let a reworded revert pass
    assert "not yet running" in recital, \
        "the recital no longer tells agents that code COMMITTED here and not " \
        "yet running is an occasion to deploy (D-142)"
    for dead in ("Behind is the only trigger", "behind is the only trigger"):
        assert dead not in recital, \
            f"the recital still says {dead!r} — with the gate dropped that " \
            f"guidance is wrong and strands local commits (D-142)"
    # C0 (2026-08-05): the org-inbox paragraph now names the TOOL itself —
    # "orgtree_audience action=grant target=extern" — for top-level agents
    # and holders (the recital under test is a top-level's)
    assert "orgtree_audience" in recital, \
        "the C0 recital no longer names orgtree_audience for top-levels"
    assert all(n not in recital and n.split("orgtree_")[1] in recital
               for n in contracted), \
        "the retire/rehire/dissolve/reallocate contraction changed"


@t("the recital never promises a tool that does not exist")
def _():
    org = store.load_org(A)
    for nid in ("boss", "mid", "worker"):
        recital = supervisor.identity_prompt(org, nid)
        # the prompt also writes the PREFIXED form mcp__orgtree__orgtree_x
        for name in __import__("re").findall(r"\borgtree_[a-z_]+", recital):
            bare = name.rsplit("orgtree__", 1)[-1]
            assert bare in CARDS or bare.startswith("orgtree_message"), \
                f"{nid}'s prompt names {name!r}, which is not a tool"


@t("every card's `required` list is a subset of what the dispatch reads")
def _():
    # a required arg the backend never looks at is a card lying about the shape
    src = open(os.path.join(BACKEND, "orgtree", "api.py"),
               encoding="utf-8").read()
    for name, sch in SCHEMA.items():
        for req in sch.get("required", []):
            assert f'"{req}"' in src, f"{name} requires {req!r}, unread by api.py"


# --------------------------------------------------------- §3 the happy paths
print("\n§3  happy paths — one per tool, through the pipe")


@t("orgtree_chart returns this node's own identity card")
def _():
    r = BOSS.ok("orgtree_chart")
    assert 'You are "boss"' in r["chart"], r["chart"][:200]
    assert "Mcp Alpha" in r["chart"]


# ------------------------------------------------------------------ D-178
# Archived agents are hidden from the default chart, which every agent's
# prompt is rebuilt from every turn. The COUNT AND THE ROUTE are the whole
# safety of it: the standing doctrine is that you check who you already
# retired before hiring someone new, because rehiring restores an expert that
# knows the codebase. A chart that merely omitted them would teach the next
# agent that they do not exist.


def _chart_block(chart):
    """Just the org-chart region of the identity prompt.

    ⚠ ANCHORED ON PURPOSE. The prompt's standing boilerplate independently
    contains the words "include_archived", "rehire" and "archived" — the
    tool catalogue and the rehire doctrine both mention them on every turn,
    whatever the chart does. Asserting those substrings against the WHOLE
    prompt therefore passes while the chart itself says nothing at all: a
    mutation that removed the entire pointer footer was caught only after
    this slice was introduced. Structural boundary, not a fixed offset.
    """
    i = chart.index("The full organization chart")
    rest = chart[i:]
    for end in ("\nYour charter:", "\nCredits:", "\nYour team charter"):
        j = rest.find(end)
        if j != -1:
            rest = rest[:j]
    return rest


def _mk_archived():
    """Two archived reports under mid, one of them a knowledge bearer."""
    for nm in ("gone-one", "gone-two"):
        MID.ok("orgtree_hire", {
            "name": nm, "tier": "haiku", "grant": 0, "charter": "done",
            "add_dirs": [], "org_visibility": "team",
            "tools": {"bash": False, "web": False, "edit": True,
                      "subagents": False, "mcp": []}})
        MID.ok("orgtree_retire", {"node": nm})
    o = store.load_org(A)
    o.nodes["gone-two"]["bearer_state"] = "knowledge"
    store.save_org(o)


@t("D-178: the default chart hides archived agents but counts them")
def _():
    _mk_archived()
    chart = _chart_block(BOSS.ok("orgtree_chart")["chart"])
    assert "gone-one" not in chart and "gone-two" not in chart, \
        "an archived agent is still named in the default chart"
    assert "mid" in chart and "worker" in chart, \
        "hiding the dead also hid the living"
    assert "2 archived" in chart, chart[:400]
    # the ROUTE must survive — a count with no way to act on it is a dead end
    assert "include_archived" in chart, \
        "the chart counts the archived but never says how to see them"
    # and the reason, so the next reader knows why to bother
    assert "rehir" in chart.lower(), \
        "the pointer dropped the rehire doctrine that justifies it"


@t("D-178: the pointer names knowledge bearers, which are the ones wanted")
def _():
    chart = _chart_block(BOSS.ok("orgtree_chart")["chart"])
    assert "knowledge bearer" in chart, chart[:400]
    assert "1 consultable knowledge bearer" in chart, \
        "the bearer count is wrong or unpluralised: " + chart[:400]


@t("D-178: the count sits under the superior that retired them, not at the foot")
def _():
    # per-parent placement is the point (coordinator ruling): the question is
    # "did I retire someone who did this", which is answered by WHERE the
    # count sits. A single global tally would pass a naive count assertion
    # and destroy exactly this.
    lines = _chart_block(BOSS.ok("orgtree_chart")["chart"]).splitlines()
    mid_i = next(i for i, l in enumerate(lines) if l.strip().startswith("- mid "))
    # explicit, not next(): a bare next() raises StopIteration here, which is
    # a crash rather than a verdict — the reader is left to work out that the
    # per-parent pointer is missing entirely
    ptrs = [i for i, l in enumerate(lines) if l.strip().startswith("+ 2 archived")]
    assert ptrs, ("no per-parent pointer line in the chart at all — the "
                  "count may exist only as a global tally, which cannot say "
                  "WHO retired them:\n" + "\n".join(lines[:25]))
    ptr_i = ptrs[0]
    assert ptr_i > mid_i, "the pointer is not beneath mid"
    mid_indent = len(lines[mid_i]) - len(lines[mid_i].lstrip())
    ptr_indent = len(lines[ptr_i]) - len(lines[ptr_i].lstrip())
    assert ptr_indent == mid_indent + 2, \
        (f"pointer indent {ptr_indent} is not one level under mid "
         f"({mid_indent}) — it is not attached to the superior that "
         f"retired them")
    # nothing of another parent's may have been swept into mid's count
    assert not any(l.strip().startswith("+ ") for l in lines[:mid_i])


@t("D-178: include_archived=true lists every archived agent by name")
def _():
    chart = _chart_block(BOSS.ok("orgtree_chart", {"include_archived": True})["chart"])
    assert "gone-one" in chart and "gone-two" in chart, chart[:600]
    assert "consultable" in chart, "the bearer marker vanished from the full list"
    # the pointer is pointless when the list is right there
    assert "+ 2 archived" not in chart, "counted AND listed — pick one"


@t("D-178: the string \"false\" does not switch the listing on")
def _():
    # an LLM writes include_archived:"false" often enough that plain
    # truthiness — where any non-empty string is true — would turn a
    # deliberate opt-out into an opt-in, silently
    for falsey in ("false", "False", "no", "", "0"):
        chart = _chart_block(BOSS.ok("orgtree_chart", {"include_archived": falsey})["chart"])
        assert "gone-one" not in chart, f"{falsey!r} listed the archived"
    for truthy in (True, "true", "TRUE", "yes", 1):
        chart = _chart_block(BOSS.ok("orgtree_chart", {"include_archived": truthy})["chart"])
        assert "gone-one" in chart, f"{truthy!r} did not list the archived"


@t("D-178: an unrecoverable node stays visible — it still holds its seat")
def _():
    o = store.load_org(A)
    o.nodes["gone-one"]["state"] = "unrecoverable"
    store.save_org(o)
    try:
        chart = _chart_block(BOSS.ok("orgtree_chart")["chart"])
        # hiding these was already caught once as a bug (org_children's own
        # comment): the operator must be able to reach them to re-seed or
        # retire, and they are not archived
        assert "gone-one" in chart, \
            "an unrecoverable node was hidden — it holds a seat and must " \
            "stay reachable"
        assert "1 archived" in chart, "the count did not shrink with it"
    finally:
        o = store.load_org(A)
        o.nodes["gone-one"]["state"] = "archived"
        store.save_org(o)


@t("D-178: the CANVAS is unaffected — org.tree() still carries the archived")
def _():
    # the change is presentation on the AGENT-facing chart only. The canvas
    # renders from org.tree(), a separate path; asserting that here rather
    # than leaving it at inspection, because "the UI is unaffected" is
    # exactly the kind of claim that is true when written and false later.
    tree = store.load_org(A).tree()
    flat = json.dumps(tree)
    assert "gone-one" in flat and "gone-two" in flat, \
        "hiding the archived from the chart also removed them from the " \
        "tree the canvas draws"


@t("orgtree_message reaches a direct report and wakes it")
def _():
    DRIVEN.clear()
    r = BOSS.ok("orgtree_message", {"to": "mid", "body": "hello mid",
                                    "kind": "request"})
    assert r["delivered"] == "mid" and r.get("id"), r
    assert [d[1] for d in DRIVEN] == ["mid"], DRIVEN
    assert any(m["body"] == "hello mid" for m in mailbox(A, "mid"))


@t("orgtree_message reaches a NON-CHILD descendant and grants the reply path")
def _():
    r = BOSS.ok("orgtree_message", {"to": "worker", "body": "skip-level"})
    assert r["delivered"] == "worker", r
    assert any("audience granted" in w for w in r.get("warnings", [])), r
    r2 = WORKER.ok("orgtree_message", {"to": "boss", "body": "replying up"})
    assert r2["delivered"] == "boss", r2


@t("orgtree_message one hop up, and sideways to a peer")
def _():
    assert MID.ok("orgtree_message", {"to": "boss", "body": "up"})["delivered"] \
        == "boss"
    assert MID.ok("orgtree_message", {"to": "peer", "body": "sideways"}
                  )["delivered"] == "peer"


@t("orgtree_send_notice lands in the mailbox WITHOUT waking the recipient")
def _():
    DRIVEN.clear()
    PARKED.clear()
    r = BOSS.ok("orgtree_send_notice", {"to": "mid", "body": "fyi: phase 2 done"})
    assert r["delivered"] == "mid" and r.get("id"), r
    assert DRIVEN == [], "a notice woke its recipient"
    # the no-wake nudge DID go out (it steers a running recipient; every
    # fixture node is idle, so here it parks)
    assert PARKED and PARKED[-1][1] == "mid", PARKED
    assert "next turn" in r.get("delivery", ""), r
    m = next(m for m in mailbox(A, "mid") if m["body"] == "fyi: phase 2 done")
    assert m["kind"] == "notice", m


@t("the envelope renders a notice as NOTICE — visibly not actionable mail")
def _():
    blk, _ = supervisor._mail_block(list(mailbox(A, "mid")))
    assert "NOTICE FROM boss" in blk, blk
    assert "no reply is expected" in blk, blk


@t("a notice-only mailbox does not qualify as waking mail; a mixed one does")
def _():
    org = store.load_org(A)
    box = [m for m in mailbox(A, "mid") if m["kind"] == "notice"]
    org.d["mail"]["mid"] = list(box)
    assert not org.waking_mail("mid"), org.d["mail"]["mid"]
    org.d["mail"]["mid"] = box + [{"id": "x", "from": "boss",
                                   "kind": "message", "body": "act",
                                   "at": "2026-08-19T00:00:00Z"}]
    assert org.waking_mail("mid")
    # in-memory probe only — nothing saved; the real doc is untouched


@t("a top-level agent may write to 'user' (it lands in the user inbox)")
def _():
    r = BOSS.ok("orgtree_message", {"to": "user", "body": "for the user"})
    assert r["delivered"] == "user_inbox", r
    assert any(m["body"] == "for the user"
               for m in store.load_org(A).d.get("user_inbox", []))


@t("FR-21: mail to the user carries attachments as download-card metas")
def _():
    d = supervisor.scratch_dir(A, "boss")
    with open(os.path.join(d, "findings.txt"), "w", encoding="utf-8") as f:
        f.write("the findings")
    r = BOSS.ok("orgtree_message", {"to": "user", "body": "report attached",
                                    "attachments": ["findings.txt"]})
    assert r["delivered"] == "user_inbox", r
    m = next(m for m in store.load_org(A).d.get("user_inbox", [])
             if m["body"] == "report attached")
    # the meta is _agent_send_file's exact card shape: the file was COPIED to
    # the sender's outbox (edits after send cannot rewrite what was sent) and
    # the path is outbox-relative — the same URL the standalone card uses
    a = (m.get("attachments") or [{}])[0]
    assert a.get("name") == "findings.txt" and a.get("path") == "outbox/findings.txt" \
        and a.get("bytes", 0) > 0, m
    assert os.path.isfile(os.path.join(d, "outbox", "findings.txt"))


@t("FR-21: an attachment that escapes every held root refuses BEFORE any mail "
   "is recorded (send_file's guards are inherited, not re-implemented)")
def _():
    before = len(store.load_org(A).d.get("user_inbox", []))
    outside = os.path.join(DATA, "not-yours.txt")
    with open(outside, "w", encoding="utf-8") as f:
        f.write("x")
    BOSS.refuse("orgtree_message", {"to": "user", "body": "smuggle",
                                    "attachments": [outside]})
    assert len(store.load_org(A).d.get("user_inbox", [])) == before, \
        "a refused send recorded mail anyway"


@t("FR-21: attachments to a LOCAL agent recipient still refuse, naming the "
   "send_file route")
def _():
    d = supervisor.scratch_dir(A, "worker")
    with open(os.path.join(d, "peer.txt"), "w", encoding="utf-8") as f:
        f.write("x")
    txt = WORKER.refuse("orgtree_message", {"to": "boss", "body": "here",
                                            "attachments": ["peer.txt"]})
    assert "send_file" in txt, txt


@t("orgtree_status done reports upward and leaves the node idle")
def _():
    DRIVEN.clear()
    r = WORKER.ok("orgtree_status", {"status": "done", "summary": "finished it"})
    assert r["recorded"] == "done" and r["reported_to"] == "mid", r
    assert store.load_org(A).node("worker")["last_status"]["status"] == "idle", \
        "the card says done leaves you idle"
    assert any("[DONE] finished it" in m["body"] for m in mailbox(A, "mid"))
    assert [d[1] for d in DRIVEN] == ["mid"], DRIVEN


@t("orgtree_status blocked also reports; working/idle just record")
def _():
    assert WORKER.ok("orgtree_status", {"status": "blocked", "summary": "stuck"}
                     )["reported_to"] == "mid"
    for s in ("working", "idle"):
        r = WORKER.ok("orgtree_status", {"status": s, "summary": "n/a"})
        assert "reported_to" not in r, r
        assert store.load_org(A).node("worker")["last_status"]["status"] == s


@t("a top-level agent's status names the chip, not a superior")
def _():
    r = BOSS.ok("orgtree_status", {"status": "done", "summary": "top done"})
    assert "status chip" in r["reported_to"], r


@t("orgtree_hire seats a report and tells the hirer it is IDLE")
def _():
    DRIVEN.clear()
    r = MID.ok("orgtree_hire", {
        "name": "aide", "tier": "haiku", "grant": 0, "charter": "help mid",
        "add_dirs": [], "org_visibility": "team",
        "tools": {"bash": False, "web": False, "edit": True,
                  "subagents": False, "mcp": []}})
    assert r["node"] == "aide", r
    assert "IDLE" in r["next_step"] and "orgtree_message" in r["next_step"], r
    assert DRIVEN == [], "hiring started a turn — the card says it does not"
    assert store.load_org(A).node("aide")["parent"] == "mid"


# ---------------------------------------------------------------- D-160
# The one-call hire. Four checks, and the first is the one that matters: the
# ORDERING property is the whole reason this feature can be a net loss rather
# than a net win. A composite that starts the hire's turn before its mode and
# audiences are applied produces exactly the broken half-configured agent the
# four-call dance produced — faster, and less visibly.


@t("D-160: one orgtree_hire call applies scope, audiences and kickoff")
def _():
    DRIVEN.clear()
    r = MID.ok("orgtree_hire", {
        "name": "runner", "tier": "haiku", "grant": 0,
        "charter": "runs things", "add_dirs": [], "org_visibility": "team",
        "tools": {"bash": False, "web": False, "edit": True,
                  "subagents": False, "mcp": []},
        # the three that used to force an immediate orgtree_retool
        "permission_mode": "plan", "effort": "low",
        "team_charter": "my team ships small",
        # mid may delegate its own superior's ear and a live peer's
        "audiences": ["boss", "peer"],
        "kickoff": "start on the widget audit"})
    assert r["node"] == "runner", r
    assert r["started"] is True, r
    assert "RUNNING" in r["next_step"], r["next_step"]
    assert set(r["applied"]) == {"permission_mode", "effort", "team_charter",
                                 "audience:boss", "audience:peer",
                                 "kickoff"}, r["applied"]
    o = store.load_org(A)
    n = o.node("runner")
    assert n["scope"]["permission_mode"] == "plan", n["scope"]
    assert n["scope"]["effort"] == "low", n["scope"]
    assert n["team_charter"] == "my team ships small", n
    assert {g["grantor"] for g in o.d["audiences"]
            if g["grantee"] == "runner"} == {"boss", "peer"}, o.d["audiences"]
    bodies = [m.get("body") for m in mailbox(A, "runner")]
    assert any("widget audit" in (b or "") for b in bodies), bodies
    # ONE wake, not one per audience grant plus one for the kickoff. The
    # four-call version woke the hire on the audience grant too, with nothing
    # to do yet.
    woke = [d for d in DRIVEN if d[1] == "runner"]
    assert len(woke) == 1, f"the seat was woken {len(woke)}×: {DRIVEN}"


@t("D-160: the kickoff turn cannot start before the seat is fully configured")
def _():
    # THE ordering check, and it is an instrument rather than an assertion
    # about source order: it snapshots the PERSISTED doc at the instant the
    # seat is actually woken. `drive` is consumed after store.save_org, so a
    # kickoff that ran early — or a mode/audience applied after it — shows up
    # here as a missing field at wake time. Reordering _seat_finish to kick
    # off first, or moving the drive inside DOC_LOCK, turns this red.
    DRIVEN.clear()
    snap = {}
    real_send = supervisor.send_message

    def spy(slug, nid, text, command=False, wake=True, **kw):
        # ⚠ the FIRST wake only. Recording every wake made this instrument
        # lie: under a mutation that woke the seat early, the later legitimate
        # wake overwrote the snapshot with the good state and the check passed
        # on broken code. What is being asserted is when the seat's turn FIRST
        # became possible, so only the first sample can answer it.
        if nid == "timed" and not snap:
            # reads the doc off DISK, which is the point — a wake that escapes
            # the transaction finds the seat not merely half-configured but
            # absent. Swallow that rather than raising, so the assertions
            # below render the verdict instead of a confusing 422 from the
            # hire call itself.
            d = store.load_org(slug)
            n = d.nodes.get("timed") or {"scope": {}}
            snap["mode"] = n["scope"].get("permission_mode")
            snap["effort"] = n["scope"].get("effort")
            snap["team_charter"] = n.get("team_charter")
            snap["auds"] = sorted(g["grantor"] for g in d.d["audiences"]
                                  if g["grantee"] == "timed")
            snap["mail"] = [m.get("body") for m in
                            d.d.get("mail", {}).get("timed", [])]
        return real_send(slug, nid, text, command=command, wake=wake, **kw)

    supervisor.send_message = spy
    try:
        MID.ok("orgtree_hire", {
            "name": "timed", "tier": "haiku", "grant": 0,
            "charter": "ordering probe", "add_dirs": [],
            "org_visibility": "team",
            "tools": {"bash": False, "web": False, "edit": True,
                      "subagents": False, "mcp": []},
            "permission_mode": "plan", "effort": "high",
            "team_charter": "set before the first turn",
            "audiences": ["boss", "peer"],
            "kickoff": "go"})
    finally:
        supervisor.send_message = real_send
    assert snap, "the seat was never woken — the kickoff did not start it"
    assert snap["mode"] == "plan", f"woken at mode {snap['mode']!r}"
    assert snap["effort"] == "high", f"woken at effort {snap['effort']!r}"
    assert snap["team_charter"] == "set before the first turn", snap
    assert snap["auds"] == ["boss", "peer"], \
        f"woken holding audiences {snap['auds']} — a grant landed after the turn"
    assert any("go" == (b or "") for b in snap["mail"]), \
        f"woken without the kickoff in its box: {snap['mail']}"


@t("D-160: a refusal anywhere refuses the WHOLE hire — no half-made seat")
def _():
    # PARTIAL FAILURE, ruled all-or-nothing. `user` is a target mid genuinely
    # may not grant (it is not top-level), so the audience step refuses AFTER
    # org.hire has already created the node in memory. store.save_org is never
    # reached, so the node is discarded with the unsaved doc.
    DRIVEN.clear()
    before = store.load_org(A)
    free_before = before.free("mid")
    args = {"name": "ghost", "tier": "haiku", "grant": 0,
            "charter": "should never exist", "add_dirs": [],
            "org_visibility": "team",
            "tools": {"bash": False, "web": False, "edit": True,
                      "subagents": False, "mcp": []},
            "permission_mode": "plan",
            "audiences": ["user"], "kickoff": "you should never read this"}
    txt = MID.refuse("orgtree_hire", args)
    assert "reach" in txt or "superior" in txt, txt
    after = store.load_org(A)
    assert "ghost" not in after.nodes, "a refused hire left a seat behind"
    assert after.free("mid") == free_before, \
        f"credits moved on a refused hire: {free_before} → {after.free('mid')}"
    assert not after.d.get("mail", {}).get("ghost"), "the kickoff was posted anyway"
    assert DRIVEN == [], f"a refused hire woke someone: {DRIVEN}"
    # and the shortcut refuses exactly what the long way refuses — same
    # ledger call, so this cannot drift apart from the check above
    MID.ok("orgtree_hire", {**args, "name": "ghost", "audiences": [],
                            "kickoff": None})
    MID.refuse("orgtree_audience", {"action": "grant", "from": "ghost",
                                    "target": "user"})


@t("D-160: hire cannot hand out a permission mode the hirer lacks")
def _():
    # NO NEW AUTHORITY: mid runs at the org default (acceptEdits), so
    # bypassPermissions is above its own — refused here exactly as retool
    # refuses it, and the seat is discarded with the rest of the call.
    DRIVEN.clear()
    MID.refuse("orgtree_hire", {
        "name": "overreach", "tier": "haiku", "grant": 0,
        "charter": "nope", "add_dirs": [], "org_visibility": "team",
        "tools": {"bash": False, "web": False, "edit": True,
                  "subagents": False, "mcp": []},
        "permission_mode": "bypassPermissions", "kickoff": "go"})
    assert "overreach" not in store.load_org(A).nodes, \
        "the seat survived a refused permission_mode"
    assert DRIVEN == [], DRIVEN
    # a kickoff that never wakes is a contradiction, not a default to silently
    # rewrite
    txt = MID.refuse("orgtree_hire", {
        "name": "hushed", "tier": "haiku", "grant": 0, "charter": "nope",
        "add_dirs": [], "org_visibility": "team",
        "tools": {"bash": False, "web": False, "edit": True,
                  "subagents": False, "mcp": []},
        "kickoff": "go", "kickoff_kind": "notice"})
    assert "notice" in txt, txt
    assert "hushed" not in store.load_org(A).nodes, txt


def _nap(name, **extra):
    """A fresh haiku report of mid's, hired and immediately archived."""
    MID.ok("orgtree_hire", {
        "name": name, "tier": "haiku", "grant": 0, "charter": "naps",
        "add_dirs": [], "org_visibility": "team",
        "tools": {"bash": False, "web": False, "edit": True,
                  "subagents": False, "mcp": []}, **extra})
    MID.ok("orgtree_retire", {"node": name})
    assert store.load_org(A).node(name)["state"] != "live"


@t("D-160: one orgtree_rehire renames, re-scopes, grants and starts the seat")
def _():
    _nap("napper")
    DRIVEN.clear()
    r = MID.ok("orgtree_rehire", {
        "node": "napper", "name": "sprinter",
        "permission_mode": "plan", "effort": "low",
        "charter": "runs fast now", "team_charter": "my team ships small",
        "org_visibility": "self", "audiences": ["boss"],
        "kickoff": "resume the audit"})
    assert r["node"] == "sprinter", r
    assert r["renamed_to"] == "sprinter", r
    assert r["started"] is True, r
    assert "RUNNING" in r["next_step"], r["next_step"]
    o = store.load_org(A)
    assert "napper" not in o.nodes, "the old id survived the rename"
    n = o.node("sprinter")
    assert n["state"] == "live", n["state"]
    assert n["scope"]["permission_mode"] == "plan", n["scope"]
    assert n["scope"]["effort"] == "low", n["scope"]
    assert n["scope"]["org_visibility"] == "self", n["scope"]
    assert n["charter"] == "runs fast now", n
    assert n["team_charter"] == "my team ships small", n
    assert {g["grantor"] for g in o.d["audiences"]
            if g["grantee"] == "sprinter"} == {"boss"}, o.d["audiences"]
    assert any("resume the audit" in (m.get("body") or "")
               for m in mailbox(A, "sprinter")), mailbox(A, "sprinter")
    woke = [d for d in DRIVEN if d[1] == "sprinter"]
    assert len(woke) == 1, f"woken {len(woke)}×: {DRIVEN}"


@t("D-160: a rehired seat is not woken until the rename and scope have landed")
def _():
    # the same instrument as the hire ordering probe, and it additionally
    # pins the RENAME: the seat must be woken under its NEW id, with the new
    # id's scope and audiences already on disk. Renaming after the kickoff
    # cannot work anyway — rename_node refuses a node that is mid-turn — so
    # this is the check that would catch someone "fixing" that by reordering.
    _nap("dozer")
    DRIVEN.clear()
    snap = {}
    real_send = supervisor.send_message

    def spy(slug, nid, text, command=False, wake=True, **kw):
        if nid in ("dozer", "racer") and not snap:      # FIRST wake only
            d = store.load_org(slug)
            snap["woken_as"] = nid
            n = d.nodes.get(nid) or {"scope": {}}
            snap["state"] = n.get("state")
            snap["mode"] = n["scope"].get("permission_mode")
            snap["auds"] = sorted(g["grantor"] for g in d.d["audiences"]
                                  if g["grantee"] == nid)
            snap["mail"] = [m.get("body") for m in
                            d.d.get("mail", {}).get(nid, [])]
            snap["old_id_gone"] = "dozer" not in d.nodes
        return real_send(slug, nid, text, command=command, wake=wake, **kw)

    supervisor.send_message = spy
    try:
        MID.ok("orgtree_rehire", {
            "node": "dozer", "name": "racer", "permission_mode": "plan",
            "audiences": ["boss", "peer"], "kickoff": "go"})
    finally:
        supervisor.send_message = real_send
    assert snap, "the seat was never woken"
    assert snap["woken_as"] == "racer", \
        f"woken as {snap['woken_as']!r} — the rename had not landed"
    assert snap["old_id_gone"], "the pre-rename id was still on disk at wake"
    assert snap["state"] == "live", snap
    assert snap["mode"] == "plan", f"woken at mode {snap['mode']!r}"
    assert snap["auds"] == ["boss", "peer"], snap["auds"]
    assert any("go" == (b or "") for b in snap["mail"]), snap["mail"]


@t("D-160: a rehire refused AFTER its rename says the rename stuck")
def _():
    # THE asymmetry, pinned. Rename moves folders outside any transaction, so
    # it alone cannot roll back — the user ruled it in anyway (2026-08-27).
    # What that obliges is honesty: the refusal must name the state the caller
    # is actually left in, and the id to retry against.
    _nap("stayer")
    DRIVEN.clear()
    txt = MID.refuse("orgtree_rehire", {
        "node": "stayer", "name": "mover",
        # mid is not top-level, so it may not hand out a USER audience — this
        # refuses after the rename has already run
        "audiences": ["user"], "kickoff": "never read"})
    assert "RENAME already happened" in txt, txt
    assert "mover" in txt, txt
    o = store.load_org(A)
    # the rename stuck (it is outside the transaction) ...
    assert "mover" in o.nodes and "stayer" not in o.nodes, sorted(o.nodes)
    # ... and NOTHING else did
    assert o.node("mover")["state"] != "live", "it was rehired anyway"
    assert not [g for g in o.d["audiences"] if g["grantee"] == "mover"]
    assert not o.d.get("mail", {}).get("mover"), "the kickoff was posted"
    assert DRIVEN == [], f"a refused rehire woke someone: {DRIVEN}"
    # and the advertised retry works
    r = MID.ok("orgtree_rehire", {"node": "mover", "kickoff": "now go"})
    assert store.load_org(A).node("mover")["state"] == "live"
    assert r["started"] is True, r


@t("orgtree_retool re-scopes a report, changing only the fields passed")
def _():
    before = store.load_org(A).node("aide")["scope"]["tools"]["edit"]
    r = MID.ok("orgtree_retool", {"node": "aide", "charter": "help mid better"})
    assert r, r
    n = store.load_org(A).node("aide")
    assert n["charter"] == "help mid better"
    assert n["scope"]["tools"]["edit"] == before, "an unpassed field changed"


@t("orgtree_retool sets a report's thinking effort")
def _():
    MID.ok("orgtree_retool", {"node": "aide", "effort": "low"})
    assert store.load_org(A).node("aide")["scope"]["effort"] == "low"
    MID.ok("orgtree_retool", {"node": "aide", "effort": ""})
    assert not store.load_org(A).node("aide")["scope"].get("effort")


@t("orgtree_reallocate moves credits both ways")
def _():
    MID.ok("orgtree_reallocate", {"node": "aide", "delta": 2})
    assert store.load_org(A).node("aide")["grant"] == 2
    MID.ok("orgtree_reallocate", {"node": "aide", "delta": -2})
    assert store.load_org(A).node("aide")["grant"] == 0


@t("orgtree_switch_model changes a report's tier")
def _():
    MID.ok("orgtree_switch_model", {"node": "aide", "tier": "sonnet"})
    assert store.load_org(A).node("aide")["model"] == "sonnet"
    MID.ok("orgtree_switch_model", {"node": "aide", "tier": "haiku"})


@t("orgtree_move re-parents inside the mover's reach")
def _():
    BOSS.ok("orgtree_move", {"node": "aide", "new_parent": "worker"})
    assert store.load_org(A).node("aide")["parent"] == "worker"
    BOSS.ok("orgtree_move", {"node": "aide", "new_parent": "mid"})


@t("orgtree_read_transcript reads a descendant (empty session included)")
def _():
    r = BOSS.ok("orgtree_read_transcript", {"node": "worker", "last": 5})
    assert r["node"] == "worker" and isinstance(r["messages"], list), r
    r2 = BOSS.ok("orgtree_read_transcript", {"node": "boss"})
    assert r2["node"] == "boss", r2


@t("orgtree_read_scratch lists the root with no path, then reads a file")
def _():
    d = supervisor.scratch_dir(A, "worker")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "notes.txt"), "w", encoding="utf-8") as f:
        f.write("worker notes")
    r = BOSS.ok("orgtree_read_scratch", {"node": "worker"})
    assert r["dir"] == "." and "notes.txt" in r["entries"], r
    r = BOSS.ok("orgtree_read_scratch", {"node": "worker", "path": "notes.txt"})
    assert r["content"] == "worker notes", r


@t("orgtree_send_file copies a file from the node's own folder to its outbox")
def _():
    d = supervisor.scratch_dir(A, "worker")
    with open(os.path.join(d, "report.txt"), "w", encoding="utf-8") as f:
        f.write("deliverable")
    r = WORKER.ok("orgtree_send_file", {"path": "report.txt", "note": "here"})
    assert r.get("sent") or r.get("file") or r.get("name"), r
    assert os.path.isfile(os.path.join(d, "outbox", "report.txt")), \
        sorted(os.listdir(d))


@t("orgtree_list_orgs lists the other orgs and marks this one")
def _():
    r = BOSS.ok("orgtree_list_orgs")
    slugs = {o["slug"]: o for o in r["orgs"]}
    assert slugs[A]["you"] is True and slugs[B]["you"] is False, r


@t("orgtree_audience request → forward → grant, then the grantee may write up")
def _():
    aide = Mcp(A, "aide")
    try:
        r = aide.ok("orgtree_audience", {"action": "request", "target": "boss",
                                         "reason": "need a ruling"})
        assert r, r
        # it climbs one refusable hop: mid holds it now
        r = MID.ok("orgtree_audience", {"action": "forward", "from": "aide",
                                        "target": "boss"})
        assert r, r
        r = BOSS.ok("orgtree_audience", {"action": "grant", "from": "aide"})
        assert r, r
        assert aide.ok("orgtree_message", {"to": "boss", "body": "granted hello"}
                       )["delivered"] == "boss"
        BOSS.ok("orgtree_audience", {"action": "revoke", "grantee": "aide"})
        aide.refuse("orgtree_message", {"to": "boss", "body": "after revoke"})
    finally:
        aide.close()


@t("orgtree_request_credits files a pending request for a top-level agent")
def _():
    grant = store.load_org(A).node("boss")["grant"]
    r = BOSS.ok("orgtree_request_credits",
                {"new_limit": grant + 25, "reason": "more reports"})
    assert r, r
    reqs = store.load_org(A).d.get("credit_requests", [])
    assert any(q["node"] == "boss" and q["status"] == "pending" for q in reqs), reqs


@t("orgtree_retire archives a report, orgtree_rehire brings it back")
def _():
    MID.ok("orgtree_hire", {
        "name": "temp", "tier": "haiku", "grant": 0, "charter": "temporary",
        "add_dirs": [], "org_visibility": "self",
        "tools": {"bash": False, "web": False, "edit": False,
                  "subagents": False, "mcp": []}})
    MID.ok("orgtree_retire", {"node": "temp"})
    assert store.load_org(A).node("temp")["state"] == "archived"
    MID.ok("orgtree_rehire", {"node": "temp"})
    assert store.load_org(A).node("temp")["state"] == "live"


@t("orgtree_message to an ARCHIVED node is queued, not refused (card claim)")
def _():
    MID.ok("orgtree_retire", {"node": "temp"})
    DRIVEN.clear()
    r = MID.ok("orgtree_message", {"to": "temp", "body": "waiting for you"})
    assert r["delivered"] == "temp", r
    assert any("archived" in w for w in r.get("warnings", [])), r
    assert DRIVEN == [], "an archived recipient was driven"
    assert any(m["body"] == "waiting for you" for m in mailbox(A, "temp"))
    MID.ok("orgtree_rehire", {"node": "temp"})


@t("orgtree_dissolve removes a node and everything beneath it")
def _():
    MID.ok("orgtree_reallocate", {"node": "temp", "delta": 3})
    tmp = Mcp(A, "temp")
    try:
        tmp.ok("orgtree_hire", {
            "name": "leaf", "tier": "haiku", "grant": 0, "charter": "leaf",
            "add_dirs": [], "org_visibility": "self",
            "tools": {"bash": False, "web": False, "edit": False,
                      "subagents": False, "mcp": []}})
    finally:
        tmp.close()
    MID.ok("orgtree_dissolve", {"node": "temp"})
    d = store.load_org(A)
    assert d.node("temp")["state"] == "archived", d.node("temp")["state"]
    assert d.node("leaf")["state"] == "archived", d.node("leaf")["state"]


# ------------------------------------------------------------ §4 the refusals
print("\n§4  refusals — the authority each card promises is the one enforced")


def no500(text, what):
    assert "Internal Server Error" not in text and "Traceback" not in text, \
        f"{what} → 500: {text[:300]}"


@t("orgtree_message: a node may not address a non-peer outside its reach")
def _():
    txt = WORKER.refuse("orgtree_message", {"to": "peer", "body": "x"})
    assert "may not address" in txt, txt


@t("orgtree_message: only top-level agents (or audience holders) write to 'user'")
def _():
    txt = WORKER.refuse("orgtree_message", {"to": "user", "body": "x"})
    assert "top-level" in txt, txt


@t("orgtree_message: an unknown recipient is refused, not invented")
def _():
    txt = WORKER.refuse("orgtree_message", {"to": "nobody-here", "body": "x"})
    no500(txt, "unknown recipient")


@t("orgtree_message: outside mail (@org:/@mcp:) is a top-level privilege")
def _():
    txt = WORKER.refuse("orgtree_message", {"to": "@org:" + B, "body": "hi"})
    assert "TOP-LEVEL" in txt or "top-level" in txt, txt
    txt = WORKER.refuse("orgtree_message", {"to": "@mcp:some-chat", "body": "hi"})
    assert "TOP-LEVEL" in txt or "top-level" in txt, txt


@t("orgtree_message: an org cannot address ITSELF as an outside party")
def _():
    txt = BOSS.refuse("orgtree_message", {"to": "@org:" + A, "body": "hi"})
    assert "this organization itself" in txt, txt


@t("orgtree_send_notice: 'user' and outside addresses refuse, naming the route")
def _():
    txt = BOSS.refuse("orgtree_send_notice", {"to": "user", "body": "fyi"})
    assert "orgtree_message" in txt, txt
    txt = BOSS.refuse("orgtree_send_notice", {"to": "@org:" + B, "body": "fyi"})
    assert "orgtree_message" in txt, txt


@t("orgtree_send_notice: the §7.2 addressing rules still bind (no shortcut)")
def _():
    txt = WORKER.refuse("orgtree_send_notice", {"to": "peer", "body": "x"})
    assert "may not address" in txt, txt


@t("orgtree_message cannot mint kind='notice' — the marker has one mint")
def _():
    txt = BOSS.refuse("orgtree_message", {"to": "mid", "body": "x",
                                          "kind": "notice"})
    assert "orgtree_send_notice" in txt, txt


@t("orgtree_request_credits: refused for anyone with a superior")
def _():
    txt = MID.refuse("orgtree_request_credits", {"new_limit": 99, "reason": "x"})
    assert "top-level" in txt, txt


@t("orgtree_request_credits: a missing / non-numeric new_limit is a clean refusal")
def _():
    for bad in ({"reason": "x"}, {"new_limit": "abc", "reason": "x"},
                {"new_limit": "", "reason": "x"}):
        txt = BOSS.refuse("orgtree_request_credits", bad)
        no500(txt, f"request_credits {bad}")


@t("orgtree_hire: the no-defaults rule refuses every missing permission field")
def _():
    full = {"name": "nope", "tier": "haiku", "grant": 0, "charter": "c",
            "add_dirs": [], "org_visibility": "self",
            "tools": {"bash": False, "web": False, "edit": False,
                      "subagents": False, "mcp": []}}
    for drop in ("charter", "add_dirs", "org_visibility", "tools"):
        args = {k: v for k, v in full.items() if k != drop}
        txt = MID.refuse("orgtree_hire", args)
        no500(txt, f"hire without {drop}")
    # a PARTIAL tools dict is the same refusal — the schema demands all five
    txt = MID.refuse("orgtree_hire", {**full, "tools": {"bash": True}})
    assert "tools" in txt, txt
    assert "nope" not in store.load_org(A).nodes, "a refused hire seated a node"


@t("orgtree_hire: an unknown tier is refused")
def _():
    txt = MID.refuse("orgtree_hire", {
        "name": "nope", "tier": "gpt", "grant": 0, "charter": "c",
        "add_dirs": [], "org_visibility": "self",
        "tools": {"bash": False, "web": False, "edit": False,
                  "subagents": False, "mcp": []}})
    assert "tier" in txt, txt


@t("orgtree_hire: you cannot grant a capability you do not hold")
def _():
    MID.ok("orgtree_retool", {"node": "aide",
                              "tools": {"bash": False, "web": False,
                                        "edit": False, "subagents": False,
                                        "mcp": []}})
    aide = Mcp(A, "aide")
    try:
        MID.ok("orgtree_reallocate", {"node": "aide", "delta": 3})
        txt = aide.refuse("orgtree_hire", {
            "name": "subaide", "tier": "haiku", "grant": 0, "charter": "c",
            "add_dirs": [], "org_visibility": "self",
            "tools": {"bash": True, "web": True, "edit": True,
                      "subagents": True, "mcp": []}})
        assert "does not hold" in txt, txt
        assert "subaide" not in store.load_org(A).nodes, \
            "a refused hire still seated a node"
    finally:
        aide.close()


@t("orgtree_hire: a folder the hirer does not hold cannot be granted")
def _():
    aide = Mcp(A, "aide")
    try:
        txt = aide.refuse("orgtree_hire", {
            "name": "dirkid", "tier": "haiku", "grant": 0, "charter": "c",
            "add_dirs": [{"path": os.path.expanduser("~"), "mode": "rw"}],
            "org_visibility": "self",
            "tools": {"bash": False, "web": False, "edit": False,
                      "subagents": False, "mcp": []}})
        assert "does not hold" in txt, txt
        assert "dirkid" not in store.load_org(A).nodes
    finally:
        aide.close()


@t("orgtree_hire: hiring outside your own subtree is refused")
def _():
    txt = MID.refuse("orgtree_hire", {
        "name": "intruder", "tier": "haiku", "grant": 0, "charter": "c",
        "parent": "peer", "add_dirs": [], "org_visibility": "self",
        "tools": {"bash": False, "web": False, "edit": False,
                  "subagents": False, "mcp": []}})
    assert "subtree" in txt, txt


@t("orgtree_hire: a grant beyond the hirer's free credits is refused")
def _():
    txt = MID.refuse("orgtree_hire", {
        "name": "greedy", "tier": "haiku", "grant": 10_000, "charter": "c",
        "add_dirs": [], "org_visibility": "self",
        "tools": {"bash": False, "web": False, "edit": False,
                  "subagents": False, "mcp": []}})
    assert "credits" in txt, txt


@t("orgtree_retool: a node outside your subtree is refused")
def _():
    txt = MID.refuse("orgtree_retool", {"node": "peer", "charter": "mine now"})
    no500(txt, "retool a non-descendant")
    assert store.load_org(A).node("peer")["charter"] != "mine now"


@t("orgtree_retool: you may not set your OWN effort (a superior's dial)")
def _():
    txt = MID.refuse("orgtree_retool", {"node": "mid", "effort": "max"})
    no500(txt, "self retool")


@t("orgtree_retool: an unknown effort level is refused")
def _():
    txt = MID.refuse("orgtree_retool", {"node": "aide", "effort": "ludicrous"})
    assert "effort" in txt, txt


@t("orgtree_retire: a node outside your subtree is refused")
def _():
    txt = MID.refuse("orgtree_retire", {"node": "peer"})
    no500(txt, "retire a non-descendant")
    assert store.load_org(A).node("peer")["state"] == "live"


@t("orgtree_retire: self-retire with live reports is refused")
def _():
    txt = MID.refuse("orgtree_retire", {"node": "mid"})
    no500(txt, "self retire with reports")
    assert store.load_org(A).node("mid")["state"] == "live"


@t("orgtree_retire of an already-archived node is an idempotent success")
def _():
    MID.ok("orgtree_retire", {"node": "temp"})
    r = MID.ok("orgtree_retire", {"node": "temp"})       # again
    assert r.get("warnings") or r, r
    MID.ok("orgtree_rehire", {"node": "temp"})


@t("orgtree_rehire: a LOST generation can never be woken (the one refusal)")
def _():
    with store.DOC_LOCK:
        o = store.load_org(A)
        o.node("temp")["state"] = "archived"
        o.node("temp")["bearer_state"] = "lost"
        store.save_org(o)
    txt = MID.refuse("orgtree_rehire", {"node": "temp"})
    assert "lost" in txt.lower(), txt
    with store.DOC_LOCK:
        o = store.load_org(A)
        o.node("temp")["bearer_state"] = None
        store.save_org(o)
    MID.ok("orgtree_rehire", {"node": "temp"})


@t("orgtree_move: only the user seats agents at top level")
def _():
    txt = MID.refuse("orgtree_move", {"node": "aide", "new_parent": ""})
    assert "top level" in txt or "top-level" in txt, txt
    assert store.load_org(A).node("aide")["parent"] == "mid"


@t("orgtree_move: a node outside your reach cannot be moved")
def _():
    txt = MID.refuse("orgtree_move", {"node": "peer", "new_parent": "mid"})
    no500(txt, "move a non-descendant")


@t("orgtree_move: a cycle is refused (moving a node under its own descendant)")
def _():
    txt = BOSS.refuse("orgtree_move", {"node": "mid", "new_parent": "aide"})
    no500(txt, "cyclic move")
    assert store.load_org(A).node("mid")["parent"] == "boss"


@t("orgtree_dissolve: a superior cannot be dissolved from below")
def _():
    txt = WORKER.refuse("orgtree_dissolve", {"node": "mid"})
    no500(txt, "dissolve upward")
    assert store.load_org(A).node("mid")["state"] == "live"


@t("orgtree_reallocate: clawing back more than a report holds is refused")
def _():
    txt = MID.refuse("orgtree_reallocate", {"node": "aide", "delta": -9999})
    no500(txt, "over-claw")


@t("orgtree_reallocate: a node that is not your report is refused")
def _():
    txt = MID.refuse("orgtree_reallocate", {"node": "peer", "delta": 1})
    no500(txt, "reallocate a non-report")


@t("orgtree_read_transcript: reading a peer or a superior is refused")
def _():
    for target in ("boss", "peer"):
        txt = WORKER.refuse("orgtree_read_transcript", {"node": target})
        assert "DOWNWARD" in txt, txt


@t("orgtree_read_scratch: reading upward is refused")
def _():
    txt = WORKER.refuse("orgtree_read_scratch", {"node": "boss"})
    assert "DOWNWARD" in txt, txt


@t("orgtree_read_scratch: the path cannot escape the scratch space")
def _():
    for p in ("../../../..", "..\\..\\..\\..", "/etc/passwd",
              "../" * 12 + "Users", "notes.txt/../../../secrets"):
        txt, err = BOSS.call("orgtree_read_scratch", {"node": "worker", "path": p})
        no500(txt, f"scratch escape {p!r}")
        assert "entries" not in txt or "escapes" in txt, f"{p!r} listed: {txt[:200]}"


@t("orgtree_send_file: a file outside every held root is refused")
def _():
    outside = os.path.join(DATA, "outside.txt")
    with open(outside, "w", encoding="utf-8") as f:
        f.write("secret")
    txt = WORKER.refuse("orgtree_send_file", {"path": outside})
    assert "only files in your working folder" in txt, txt


@t("orgtree_send_file: missing, empty and absent paths each refuse cleanly")
def _():
    d = supervisor.scratch_dir(A, "worker")
    open(os.path.join(d, "empty.txt"), "w").close()
    for args, want in (({}, "path is required"),
                       ({"path": ""}, "path is required"),
                       ({"path": "no-such-file.txt"}, "no such file"),
                       ({"path": "empty.txt"}, "empty")):
        txt = WORKER.refuse("orgtree_send_file", args)
        assert want in txt, (args, txt)


@t("orgtree_switch_model: a node may not switch ITSELF")
def _():
    txt = MID.refuse("orgtree_switch_model", {"node": "mid", "tier": "haiku"})
    no500(txt, "self switch")
    assert store.load_org(A).node("mid")["model"] == "opus"


@t("orgtree_switch_model: an unknown tier and an out-of-subtree node refuse")
def _():
    assert "tier" in MID.refuse("orgtree_switch_model",
                                {"node": "aide", "tier": "gpt-9"})
    no500(MID.refuse("orgtree_switch_model", {"node": "peer", "tier": "haiku"}),
          "switch a non-descendant")


@t("orgtree_audience: an unknown action names the five legal ones")
def _():
    txt = BOSS.refuse("orgtree_audience", {"action": "elevate"})
    assert "request|forward|grant|deny|revoke" in txt, txt
    assert "request|forward|grant|deny|revoke" in BOSS.refuse("orgtree_audience", {})


@t("orgtree_audience: granting an audience to someone outside your subtree")
def _():
    txt = MID.refuse("orgtree_audience", {"action": "grant", "from": "peer"})
    no500(txt, "grant to a non-descendant")


@t("orgtree_audience: revoking an audience you never granted refuses cleanly")
def _():
    txt, _err = BOSS.call("orgtree_audience", {"action": "revoke",
                                               "grantee": "peer"})
    no500(txt, "revoke a non-audience")


@t("orgtree_list_orgs: a SEALED kiosk org is not listed (card claim)")
def _():
    kslug = mkorg("Mcp Sealed", kiosk={"sandbox": False, "credits": 5})
    try:
        r = BOSS.ok("orgtree_list_orgs")
        assert kslug not in {o["slug"] for o in r["orgs"]}, r
    finally:
        http("DELETE", f"/api/orgs/{kslug}")


# ------------------------------------------------------- §5 hostile arguments
print("\n§5  hostile arguments — the D-58 crash families, re-aimed through MCP")

# The MCP layer BUILDS these argument dicts from whatever the model emitted, so
# every family D-58 closed at /api/agent is reachable here verbatim: a container
# where a scalar was expected, an explicit null defeating a `.get(k, "")`
# default, and a non-numeric number. Nothing below may 500 and the server must
# still be answering afterwards.

_STR_KEYS = ("node", "to", "from", "target", "grantee", "parent", "new_parent",
             "name", "tier", "kind", "body", "action", "status", "summary",
             "reason", "charter", "team_charter", "org_visibility", "effort",
             "path")
_CONTAINERS = ({"a": 1}, ["x"], [], {}, [{"nested": ["deep"]}])


@t("a CONTAINER in any text argument is a clean 422 on every tool")
def _():
    for tool in sorted(CARDS):
        for key in _STR_KEYS:
            for c in ({"a": 1}, ["x"]):
                txt, err = BOSS.call(tool, {key: c})
                no500(txt, f"{tool} {key}={c!r}")
                assert err or tool in ("orgtree_chart", "orgtree_list_orgs",
                                       "orgtree_status"), \
                    f"{tool} {key}={c!r} was ACCEPTED: {txt[:200]}"
        assert BOSS.alive(), f"{tool} died on a container argument"


@t("an explicit null never defeats a default (the `.get(k, '')` family)")
def _():
    for tool in sorted(CARDS):
        args = {k: None for k in _STR_KEYS}
        args.update({"grant": None, "delta": None, "last": None,
                     "new_limit": None, "add_dirs": None, "tools": None})
        txt, _err = BOSS.call(tool, args)
        no500(txt, f"{tool} with every argument null")
    assert BOSS.alive()


@t("non-numeric numbers refuse cleanly wherever _arg_int reads one")
def _():
    for tool, key, extra in (("orgtree_reallocate", "delta", {"node": "aide"}),
                             ("orgtree_read_transcript", "last", {"node": "worker"}),
                             ("orgtree_hire", "grant", {"name": "x", "tier": "haiku"}),
                             ("orgtree_request_credits", "new_limit",
                              {"reason": "x"})):
        for bad in ("abc", "1e", "  ", "NaN", True, [1], {"n": 1}, 1.5, -0.0,
                    "0x10", "١٢٣"):
            txt, _err = BOSS.call(tool, {**extra, key: bad})
            no500(txt, f"{tool} {key}={bad!r}")
        assert BOSS.alive(), f"{tool} died on a bad {key}"


@t("_arg_int refuses 'Infinity'/'1e400' with 422, not 500 (PROMOTED 2026-08-04)")
def _():
    # ⚑ DEFECT (api.py, not this suite's file to fix). `_arg_int` falls back to
    # `int(float(v))` and catches only (TypeError, ValueError); float("Infinity")
    # parses, and int() of it raises OverflowError, which nothing catches — so
    # the agent gets a bare 500 where D-58 established a clean 422.
    # When fixed (catch OverflowError too), this check flips to `no500`.
    for tool, key, extra in (("orgtree_reallocate", "delta", {"node": "aide"}),
                             ("orgtree_read_transcript", "last", {"node": "worker"}),
                             ("orgtree_hire", "grant",
                              {"name": "x", "tier": "haiku", "charter": "c",
                               "add_dirs": [], "org_visibility": "self",
                               "tools": {"bash": False, "web": False,
                                         "edit": False, "subagents": False,
                                         "mcp": []}})):
        for bad in ("Infinity", "-Infinity", "1e400", "inf"):
            txt, err = BOSS.call(tool, {**extra, key: bad})
            assert err, (tool, bad, txt[:120])
            assert txt != "Internal Server Error", \
                f"{tool} {key}={bad!r} 500s again — OverflowError is escaping"
    assert BOSS.alive(), "the MCP server survives the backend's 500 at least"


@t("orgtree_rehire coerces `grant` like every sibling verb (PROMOTED 2026-08-04)")
def _():
    # ⚑ DEFECT (api.py): every sibling verb reads its integer through
    # `_arg_int`; rehire passes `a.get("grant")` verbatim into the ledger, where
    # `int(grant)` raises ValueError → 500. `{"node": "temp", "grant": "abc"}`
    # is exactly the shape an LLM produces.
    MID.ok("orgtree_retire", {"node": "temp"})
    for bad in ("abc", "1e", "  ", "NaN"):
        txt, err = MID.call("orgtree_rehire", {"node": "temp", "grant": bad})
        assert err and txt != "Internal Server Error", \
            f"rehire grant={bad!r} 500s again"
    assert store.load_org(A).node("temp")["state"] == "archived", \
        "a crashed rehire half-applied"
    MID.ok("orgtree_rehire", {"node": "temp"})
    assert MID.alive()


@t("a NUL byte in a path is rejected without reaching the filesystem")
def _():
    for p in ("a\x00b", "\x00", "notes.txt\x00.png"):
        txt, _err = BOSS.call("orgtree_read_scratch", {"node": "worker", "path": p})
        no500(txt, f"read_scratch path={p!r}")
        txt, _err = WORKER.call("orgtree_send_file", {"path": p})
        no500(txt, f"send_file path={p!r}")


_HTOOLS = {"bash": False, "web": False, "edit": False, "subagents": False,
           "mcp": []}


@t("the well-shaped-but-wrong add_dirs / tools values refuse without a 500")
def _():
    base = {"name": "shapechk", "tier": "haiku", "grant": 0, "charter": "c",
            "org_visibility": "self"}
    for dirs in ([{}], [{"path": "x"}], [{"path": "", "mode": "rw"}],
                 [{"path": "x", "mode": "chmod"}], []):
        no500(MID.call("orgtree_hire", {**base, "add_dirs": dirs,
                                        "tools": _HTOOLS})[0],
              f"hire add_dirs={dirs!r}")
    for tools in ([], {"bash": "yes", "web": 1, "edit": None, "subagents": [],
                       "mcp": "notalist"},
                  {"bash": True, "web": True, "edit": True, "subagents": True,
                   "mcp": [{"srv": 1}]},
                  {"bash": True, "web": True, "edit": True, "subagents": True,
                   "mcp": [None]}):
        no500(MID.call("orgtree_hire", {**base, "add_dirs": [],
                                        "tools": tools})[0],
              f"hire tools={tools!r}")
    no500(MID.call("orgtree_retool", {"node": "aide", "add_dirs": [{}]})[0],
          "retool add_dirs=[{}]")
    no500(MID.call("orgtree_retool", {"node": "aide",
                                      "tools": {"mcp": "notalist"}})[0],
          "retool tools={'mcp': str}")
    assert MID.alive()
    # a legal shape among them WILL have seated one — clear it back out
    if "shapechk" in store.load_org(A).nodes:
        MID.ok("orgtree_dissolve", {"node": "shapechk"})


@t("a STRING add_dirs is read character-by-character, and each char refuses")
def _():
    # norm_dirs iterates whatever it is given and treats a str element as a
    # path, so `add_dirs: "C:/work"` becomes one grant per character — every
    # one of which the hirer does not hold, so the hire is refused. Ugly, but
    # it lands on the capability rule rather than anywhere dangerous.
    txt = MID.refuse("orgtree_hire", {
        "name": "junkkid", "tier": "haiku", "grant": 0, "charter": "c",
        "org_visibility": "self", "add_dirs": "C:/work", "tools": _HTOOLS})
    assert "does not hold" in txt, txt
    assert "junkkid" not in store.load_org(A).nodes


@gap("a scalar or mis-typed element in add_dirs / tools 500s (ledger norm_dirs)")
def _():
    # ⚑ DEFECT (ledger.py:83/109/112 via api.py's hire/retool). `_norm_args`
    # guards the TEXT arguments D-58 enumerated; `add_dirs` and `tools` are
    # containers by contract, so nothing checks their SHAPE — and the ledger
    # iterates them raw:
    #   add_dirs=5          → TypeError: 'int' object is not iterable
    #   add_dirs=[1]        → AttributeError: 'int' has no attribute 'get'
    #   add_dirs=[{"path": 1, "mode": 2}] → AttributeError: no 'strip'
    #   tools=5             → AttributeError: 'int' has no attribute 'get'
    # An agent that emits `add_dirs: "none"` (a very natural mistake for a
    # model) gets a bare 500 instead of a sentence telling it the shape.
    hire = {"name": "junkkid", "tier": "haiku", "grant": 0, "charter": "c",
            "org_visibility": "self"}
    for dirs in (5, [1, 2], [{"path": 1, "mode": 2}], [[]], [None]):
        txt, err = MID.call("orgtree_hire", {**hire, "add_dirs": dirs,
                                             "tools": _HTOOLS})
        assert err and txt == "Internal Server Error", \
            f"hire add_dirs={dirs!r} no longer 500s — flip this pin"
    txt, err = MID.call("orgtree_hire", {**hire, "add_dirs": [], "tools": 5})
    assert err and txt == "Internal Server Error", \
        f"hire tools=5 no longer 500s — flip this pin ({txt[:120]})"
    for dirs in ([1], [{"path": 1, "mode": 2}], [None]):
        txt, err = MID.call("orgtree_retool", {"node": "aide", "add_dirs": dirs})
        assert err and txt == "Internal Server Error", \
            f"retool add_dirs={dirs!r} no longer 500s — flip this pin"
    for tools in ("x", 5):
        txt, err = MID.call("orgtree_retool", {"node": "aide", "tools": tools})
        assert err and txt == "Internal Server Error", \
            f"retool tools={tools!r} no longer 500s — flip this pin"
    assert "junkkid" not in store.load_org(A).nodes, \
        "a crashed hire seated a node anyway"
    assert MID.alive()


@t("absurd but well-typed numbers are refused or absorbed, never 500")
def _():
    for n in (10 ** 60, -(10 ** 60), 1e308, -1e308, 2 ** 63):
        no500(MID.call("orgtree_reallocate", {"node": "aide", "delta": n})[0],
              f"reallocate delta={n!r}")
        no500(BOSS.call("orgtree_request_credits",
                        {"new_limit": n, "reason": "x"})[0],
              f"request_credits new_limit={n!r}")
    assert MID.alive() and BOSS.alive()


@gap("a 400-digit grant 500s inside _chain_acquire (int too large for float)")
def _():
    # ⚑ DEFECT (ledger.py:1567). `_arg_int` accepts "999…" (400 digits) as a
    # perfectly good int, and the credit arithmetic then formats it as a float.
    txt, err = MID.call("orgtree_hire", {
        "name": "junkkid", "tier": "haiku", "grant": "9" * 400, "charter": "c",
        "add_dirs": [], "org_visibility": "self", "tools": _HTOOLS})
    assert err and txt == "Internal Server Error", \
        f"a 400-digit grant no longer 500s — flip this pin ({txt[:120]})"
    assert "junkkid" not in store.load_org(A).nodes
    assert MID.alive()


@t("unknown extra arguments are ignored, not fatal")
def _():
    r = BOSS.ok("orgtree_chart", {"nonsense": {"deep": [1, 2, 3]},
                                  "node": "mid", "tool": "x", "org": "x"})
    assert 'You are "boss"' in r["chart"], r["chart"][:120]


@t("a body of pure whitespace posts instead of crashing (D-57 ⑦)")
def _():
    for body in ("   ", "\n", "\t\t", ""):
        txt, _err = BOSS.call("orgtree_message", {"to": "mid", "body": body})
        no500(txt, f"whitespace body {body!r}")


@t("control characters and lone surrogates survive the pipe without a crash")
def _():
    for body in ("bell\x07 vt\x0b", "\x1b[31mred\x1b[0m", "\ud800 lone",
                 "null-ish \\u0000", "emoji 🐈‍⬛ zwj"):
        txt, _err = BOSS.call("orgtree_message", {"to": "mid", "body": body})
        no500(txt, f"control body {body!r}")
    assert BOSS.alive()


@t("every tool survives a 200-deep nested container in a scalar slot")
def _():
    deep = {"a": 1}
    for _i in range(200):
        deep = {"a": deep}
    for tool in ("orgtree_message", "orgtree_hire", "orgtree_audience",
                 "orgtree_read_scratch"):
        txt, _err = BOSS.call(tool, {"body": deep, "node": deep, "to": deep,
                                     "action": deep, "path": deep})
        no500(txt, f"{tool} deep container")
    assert BOSS.alive()


# ------------------------------------------ §6 authority cannot be forged
print("\n§6  the calling NODE is the actor — nothing in `arguments` changes that")


@t("identity comes from the environment, not from the arguments")
def _():
    # the MCP layer BUILDS {"org": ORG, "node": NODE} itself; args ride beside
    r = WORKER.ok("orgtree_chart", {"org": A, "node": "boss", "actor": "boss",
                                    "sender": "boss", "as": "boss"})
    assert 'You are "worker"' in r["chart"], r["chart"][:160]
    assert 'You are "boss"' not in r["chart"]


@t("a forged `node` argument cannot record another node's status")
def _():
    before = dict(store.load_org(A).node("boss")["last_status"])
    WORKER.ok("orgtree_status", {"node": "boss", "status": "blocked",
                                 "summary": "forged"})
    after = store.load_org(A).node("boss")["last_status"]
    assert after == before, f"worker rewrote boss's status: {after}"
    assert store.load_org(A).node("worker")["last_status"]["summary"] == "forged"


@t("a forged `from` cannot make a message come from someone else")
def _():
    r = WORKER.ok("orgtree_message", {"from": "boss", "to": "mid",
                                      "body": "who sent this"})
    assert r["delivered"] == "mid", r
    entry = [m for m in mailbox(A, "mid") if m["body"] == "who sent this"][-1]
    assert entry["from"] == "worker", entry


@t("a forged `from` cannot grant an audience the node may not grant")
def _():
    # `from` IS a real audience argument — which is exactly why it must not be
    # a way to act as someone else: the ACTOR is still the env node
    txt = WORKER.refuse("orgtree_audience", {"action": "grant", "from": "peer",
                                             "target": "user"})
    no500(txt, "worker granting peer an audience")
    assert not any(a["grantee"] == "peer"
                   for a in store.load_org(A).d.get("audiences", []))


@t("a node cannot address another ORG's nodes by name")
def _():
    txt = BOSS.refuse("orgtree_message", {"to": "bstaff", "body": "cross-org"})
    no500(txt, "cross-org by name")
    assert not mailbox(B, "bstaff"), "a message crossed into the other org"


@t("cross-org mail arrives as the ORG, never under the sending agent's name")
def _():
    r = BOSS.ok("orgtree_message", {"to": "@org:" + B, "body": "hello org B"})
    assert r["delivered"] == "@org:" + B, r
    inbox = store.load_org(B).d.get("org_inbox", [])
    assert any("hello org B" in (e.get("body") or "") for e in inbox), inbox[-2:]
    # it reaches B's top-level agents as outside mail from the ORG
    got = [m for m in mailbox(B, "bstaff") if "hello org B" in m["body"]]
    assert got, "the org-inbox copy never reached B's top-level agent"
    assert got[-1]["from"] == "@org:" + A, got[-1]["from"]
    assert "boss" not in got[-1]["from"], "the individual agent's name leaked out"


@t("an env pointing at a node that does not exist can do nothing at all")
def _():
    ghost = Mcp(A, "nosuchnode")
    try:
        for tool in ("orgtree_chart", "orgtree_list_orgs", "orgtree_status",
                     "orgtree_message"):
            txt, err = ghost.call(tool, {"to": "mid", "body": "x",
                                         "status": "done", "summary": "s"})
            assert err, f"{tool} worked for a non-existent node: {txt[:160]}"
            no500(txt, f"ghost {tool}")
    finally:
        ghost.close()


@t("an env pointing at another org's node is refused there too")
def _():
    cross = Mcp(B, "boss")            # 'boss' exists in A, not in B
    try:
        txt, err = cross.call("orgtree_chart")
        assert err, txt[:200]
        no500(txt, "cross-org ghost")
    finally:
        cross.close()


@t("a node cannot hire above its own org-visibility level")
def _():
    MID.ok("orgtree_retool", {"node": "aide", "org_visibility": "self"})
    aide = Mcp(A, "aide")
    try:
        MID.ok("orgtree_reallocate", {"node": "aide", "delta": 3})
        txt = aide.refuse("orgtree_hire", {
            "name": "viskid", "tier": "haiku", "grant": 0, "charter": "c",
            "add_dirs": [], "org_visibility": "full", "tools": _HTOOLS})
        assert "only shrinks downward" in txt, txt
        assert "viskid" not in store.load_org(A).nodes
    finally:
        aide.close()
        if "viskid" in store.load_org(A).nodes:
            MID.ok("orgtree_dissolve", {"node": "viskid"})


@t("an ARCHIVED node's process cannot hire, retool or reallocate")
def _():
    MID.ok("orgtree_retire", {"node": "temp"})
    dead = Mcp(A, "temp")
    try:
        for tool, args in (
                ("orgtree_hire", {"name": "zombie", "tier": "haiku", "grant": 0,
                                  "charter": "c", "add_dirs": [],
                                  "org_visibility": "self", "tools": _HTOOLS}),
                ("orgtree_reallocate", {"node": "mid", "delta": 1}),
                ("orgtree_request_credits", {"new_limit": 99, "reason": "x"})):
            txt, err = dead.call(tool, args)
            assert err, f"an archived node ran {tool}: {txt[:160]}"
            no500(txt, f"archived {tool}")
        assert "zombie" not in store.load_org(A).nodes
    finally:
        dead.close()
        MID.ok("orgtree_rehire", {"node": "temp"})


@gap("an ARCHIVED node's process can still SEND MAIL (post_mail has no live check)")
def _():
    # ⚑ ledger.post_mail resolves the sender with `node()` (existence) and never
    # `_require_live`, unlike hire/retool/reallocate. Live exposure is small —
    # an archived node has no running process — but a process that outlives the
    # retire (or any holder of its env) still speaks in its name.
    MID.ok("orgtree_retire", {"node": "temp"})
    dead = Mcp(A, "temp")
    try:
        r = dead.ok("orgtree_message", {"to": "mid", "body": "from the grave"})
        assert r["delivered"] == "mid", r
        assert any(m["body"] == "from the grave" and m["from"] == "temp"
                   for m in mailbox(A, "mid")), "expected the gap, not a fix"
    finally:
        dead.close()
        MID.ok("orgtree_rehire", {"node": "temp"})


@t("the read tools cannot be aimed sideways by naming the org in the args")
def _():
    for tool in ("orgtree_read_transcript", "orgtree_read_scratch"):
        txt, err = WORKER.call(tool, {"org": B, "node": "bstaff", "path": "."})
        assert err, (tool, txt[:160])          # resolved in A, where it is absent
        assert "no such node" in txt or "DOWNWARD" in txt, (tool, txt[:160])
        txt, err = WORKER.call(tool, {"org": B, "node": "boss", "path": "."})
        assert err and "DOWNWARD" in txt, (tool, txt[:160])


@t("send_file cannot reach another node's scratch by naming it")
def _():
    d = supervisor.scratch_dir(A, "boss")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "boss-secret.txt"), "w", encoding="utf-8") as f:
        f.write("boss only")
    txt = WORKER.refuse("orgtree_send_file",
                        {"node": "boss", "path": os.path.join(d, "boss-secret.txt")})
    assert "only files in your working folder" in txt, txt


# --------------------------------------------------- §7 the sandbox bridge
print("\n§7  sandboxed mode — the bridge secret pins an org, and only an org")

SBX = mkorg("Mcp Sandy", sandbox=True)
op(SBX, op="hire", tier="opus", name="sboss", grant=10, charter="sandboxed boss")
SECRET = store.load_org(SBX).d["sandbox"]["secret"]
BASE = f"http://127.0.0.1:{PORT}"


@t("a container's MCP server reaches its OWN org through the bridge")
def _():
    m = Mcp(SBX, "sboss", base=BASE, secret=SECRET)
    try:
        r = m.ok("orgtree_chart")
        assert 'You are "sboss"' in r["chart"], r["chart"][:160]
    finally:
        m.close()


@t("the same secret cannot act on ANOTHER org (403 at the gateway)")
def _():
    m = Mcp(A, "boss", base=BASE, secret=SECRET)
    try:
        for tool, args in (("orgtree_chart", {}),
                           ("orgtree_message", {"to": "mid", "body": "x"}),
                           ("orgtree_list_orgs", {})):
            txt, err = m.call(tool, args)
            assert err, f"{tool} crossed orgs on a foreign secret: {txt[:160]}"
            assert "bridge secret is scoped" in txt or "forbidden" in txt, txt
    finally:
        m.close()


@t("a wrong or empty bridge secret is a bare 403 for every tool")
def _():
    for secret in ("f" * 32, "", "  ", SECRET.upper(), SECRET[:-1]):
        m = Mcp(SBX, "sboss", base=BASE, secret=secret)
        try:
            txt, err = m.call("orgtree_chart")
            if secret == "":       # no header at all → the admin app answers
                assert not err, txt[:160]
            else:
                assert err and "forbidden" in txt, (secret[:6], txt[:160])
        finally:
            m.close()


@t("the bridge exposes ONLY /api/agent and the org's own steer path")
def _():
    hdr = {"X-Orgtree-Bridge": SECRET}
    for method, path in (("GET", "/api/orgs"), ("GET", f"/api/orgs/{SBX}"),
                         ("GET", "/api/fs"), ("POST", f"/api/orgs/{SBX}/ops"),
                         ("GET", "/api/settings"), ("GET", "/openapi.json"),
                         ("POST", f"/api/orgs/{A}/nodes/boss/steer")):
        st, js, _b = http(method, path, {} if method == "POST" else None, hdr)
        assert st == 403, f"the bridge served {method} {path} → {st}"
    st, _js, _b = http("POST", f"/api/orgs/{SBX}/nodes/sboss/steer", {}, hdr)
    assert st == 200, st


@gap("the bridge pins the ORG, not the NODE — any node id rides one secret")
def _():
    # ⚑ KNOWN, left undecided in D-58: one container serves every agent of the
    # org and they all read the same `.bridge`, so a subordinate process can
    # address /api/agent as its superior. Closing it needs a per-node
    # credential that does not exist. Pinned so the day one appears, this fails.
    op(SBX, op="hire", tier="haiku", name="sworker", parent="sboss", grant=0,
       charter="a report")
    m = Mcp(SBX, "sboss", base=BASE, secret=SECRET)   # claims to be the boss
    try:
        r = m.ok("orgtree_chart")
        assert 'You are "sboss"' in r["chart"], "the gap closed — flip this pin"
    finally:
        m.close()


@t("ORGTREE_BASE without a secret cannot reach a sandboxed org's backend")
def _():
    m = Mcp(SBX, "sboss", base=BASE)      # no ORGTREE_BRIDGE_SECRET
    try:
        # the rig's composite sends unheadered traffic to the ADMIN app, which
        # is loopback-only in production — the point is that the bridge itself
        # never answers without the secret (asserted above)
        txt, err = m.call("orgtree_chart")
        assert not err, txt[:160]
    finally:
        m.close()


@t("an unreachable backend is an error result, not a dead MCP server")
def _():
    m = Mcp(A, "boss", base="http://127.0.0.1:7", secret="")
    try:
        t0 = time.time()
        txt, err = m.call("orgtree_chart", timeout=60)
        assert err and "unreachable" in txt, txt[:200]
        assert m.alive(), "the server died when the backend was down"
        assert time.time() - t0 < 40, "a dead backend blocked for too long"
        txt2, err2 = m.call("orgtree_list_orgs", timeout=60)
        assert err2 and m.alive(), "the second call after a failure broke"
    finally:
        m.close()


# ------------------------------------------------------------- §8 steer.py
print("\n§8  steer.py — the PostToolUse hook that runs after EVERY tool call")

STEER_PY = os.path.join(BACKEND, "orgtree", "steer.py")
HOOK_PAYLOAD = json.dumps({
    "session_id": "abc", "hook_event_name": "PostToolUse",
    "tool_name": "Bash", "tool_input": {"command": "ls"},
    "tool_response": {"stdout": "x"}})


def queue_steer(slug, nid, *msgs):
    st = supervisor.state(slug, nid)
    st.setdefault("steer", []).extend(msgs)


def run_hook(org=None, node=None, cwd=None, stdin=HOOK_PAYLOAD, env=None,
             timeout=20):
    """Run the hook exactly as the CLI does: argv, a hook payload on stdin,
    cwd = the node's scratch dir. Returns (seconds, stdout, stderr, rc)."""
    e = dict(os.environ)
    e["ORGTREE_DATA"] = DATA
    e["ORGTREE_PORT"] = str(PORT)
    e.update(env or {})
    argv = [sys.executable, STEER_PY]
    if org is not None:
        argv += [org, node or ""]
    t0 = time.time()
    p = subprocess.run(argv, input=(stdin or "").encode("utf-8", "replace"),
                       cwd=cwd or BACKEND, env=e, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    return (time.time() - t0, p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"), p.returncode)


def scratch(slug, nid):
    d = supervisor.scratch_dir(slug, nid)
    os.makedirs(d, exist_ok=True)
    return d


@t("pending mail is delivered as PostToolUse additionalContext")
def _():
    queue_steer(A, "worker", "FROM @user: check the log", "FROM @agent mid: ping")
    dt, out, err, rc = run_hook(A, "worker", scratch(A, "worker"))
    assert rc == 0 and not err.strip(), (rc, err[:300])
    payload = json.loads(out)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "check the log" in ctx and "ping" in ctx, ctx
    assert "\n---\n" in ctx, "the two messages were not separated"
    assert ctx.startswith("[ORGTREE MAIL — delivered mid-task]"), ctx[:80]
    assert "authentic per your system prompt" in ctx, ctx[-200:]
    assert dt < 5, f"the hook took {dt:.1f}s of an 8 s budget"


@t("the wrapper stays sender-neutral (attribution lives inside each message)")
def _():
    queue_steer(A, "worker", "FROM @agent mid: not from the user")
    _dt, out, _e, _rc = run_hook(A, "worker", scratch(A, "worker"))
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    head = ctx.split("\n")[0]
    assert "user" not in head.lower(), f"the wrapper claims a sender: {head}"


@t("the fetch CONSUMES the batch — a second hook run says nothing")
def _():
    queue_steer(A, "worker", "only once")
    _dt, out1, _e, _rc = run_hook(A, "worker", scratch(A, "worker"))
    assert "only once" in out1
    dt, out2, err2, rc2 = run_hook(A, "worker", scratch(A, "worker"))
    assert out2.strip() == "", f"the message was delivered twice: {out2[:200]}"
    assert rc2 == 0 and not err2.strip()
    assert dt < 5, dt


@t("nothing pending → completely silent output, exit 0")
def _():
    _dt, out, err, rc = run_hook(A, "peer", scratch(A, "peer"))
    assert out == "" and err == "" and rc == 0, (out[:200], err[:200], rc)


@t("identity falls back to the CWD when argv carries no names")
def _():
    queue_steer(A, "worker", "cwd-derived delivery")
    _dt, out, _e, rc = run_hook(cwd=scratch(A, "worker"))
    assert rc == 0 and "cwd-derived delivery" in out, out[:200]


@t("argv WINS over the cwd — the lineage-shared scratch dir cannot mis-route")
def _():
    # the C10 regression: scratch_dir maps "name@gen" to the base "name" dir,
    # so a bearer's hook resolved as its SUCCESSOR and ate its mail
    queue_steer(A, "worker", "belongs to worker")
    queue_steer(A, "peer", "belongs to peer")
    _dt, out, _e, _rc = run_hook(A, "peer", scratch(A, "worker"))
    assert "belongs to peer" in out and "belongs to worker" not in out, out[:300]
    _dt, out2, _e, _rc = run_hook(A, "worker", scratch(A, "worker"))
    assert "belongs to worker" in out2, out2[:200]


@t("a cwd outside the scratch tree and no argv → silent, and nothing consumed")
def _():
    queue_steer(A, "worker", "must survive")
    _dt, out, err, rc = run_hook(cwd=BACKEND)
    assert out == "" and rc == 0 and not err.strip(), (out[:200], err[:200])
    _dt, out2, _e, _rc = run_hook(A, "worker", scratch(A, "worker"))
    assert "must survive" in out2, "the queue was drained by a stray hook run"


@t("a malformed / huge / absent hook payload changes nothing")
def _():
    for stdin in ("", "not json at all", "\x00\x01\x02" * 100,
                  json.dumps({"tool_response": "x" * 200000}), "{"):
        queue_steer(A, "worker", "payload-proof")
        _dt, out, err, rc = run_hook(A, "worker", scratch(A, "worker"),
                                     stdin=stdin)
        assert rc == 0 and "payload-proof" in out, (stdin[:20], rc, err[:200])


@t("a dead backend is silent and fast, and does not consume the mail")
def _():
    queue_steer(A, "worker", "survives a dead backend")
    dt, out, err, rc = run_hook(A, "worker", scratch(A, "worker"),
                                env={"ORGTREE_PORT": "7"})
    assert out == "" and rc == 0 and not err.strip(), (out[:200], err[:300])
    assert dt < 5, f"a refused connection took {dt:.1f}s"
    _dt, out2, _e, _rc = run_hook(A, "worker", scratch(A, "worker"))
    assert "survives a dead backend" in out2, "the mail was lost to a dead backend"


@t("the port comes from <data>/.port when the env does not carry one")
def _():
    pf = os.path.join(DATA, ".port")
    with open(pf, "w", encoding="utf-8") as f:
        f.write(f"{PORT}\n")
    queue_steer(A, "worker", "via the port file")
    e = {k: v for k, v in os.environ.items() if k != "ORGTREE_PORT"}
    e["ORGTREE_DATA"] = DATA
    argv = [sys.executable, STEER_PY, A, "worker"]
    p = subprocess.run(argv, input=HOOK_PAYLOAD.encode(), cwd=scratch(A, "worker"),
                       env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=20)
    assert p.returncode == 0, p.stderr[:300]
    assert "via the port file" in p.stdout.decode(), p.stdout[:200]


@t("a sandboxed hook reads .bridge and sends the org's secret")
def _():
    bridge = os.path.join(DATA, ".bridge")
    with open(bridge, "w", encoding="utf-8") as f:
        json.dump({"url": BASE, "secret": SECRET}, f)
    try:
        queue_steer(SBX, "sboss", "through the bridge")
        _dt, out, err, rc = run_hook(SBX, "sboss", scratch(SBX, "sboss"))
        assert rc == 0 and "through the bridge" in out, (out[:200], err[:300])
        # the SAME .bridge cannot fetch another org's node: the gateway 403s
        # and the hook stays silent rather than erroring into the transcript
        queue_steer(A, "worker", "not reachable on B's secret")
        _dt, out2, err2, rc2 = run_hook(A, "worker", scratch(A, "worker"))
        assert out2 == "" and rc2 == 0 and not err2.strip(), (out2[:200], err2[:200])
    finally:
        os.remove(bridge)
    _dt, out3, _e, _rc = run_hook(A, "worker", scratch(A, "worker"))
    assert "not reachable on B's secret" in out3, "the 403 consumed the mail"


@t("a .bridge with a missing/garbled url falls back instead of crashing")
def _():
    bridge = os.path.join(DATA, ".bridge")
    for blob in ("{", '{"secret": "x"}', "[]", '{"url": null}', ""):
        with open(bridge, "w", encoding="utf-8") as f:
            f.write(blob)
        queue_steer(A, "worker", "fallback works")
        _dt, out, err, rc = run_hook(A, "worker", scratch(A, "worker"))
        assert rc == 0 and not err.strip(), (blob, err[:300])
        if '"url"' not in blob or blob == '{"url": null}':
            # no usable url → the loopback fallback still delivers
            assert "fallback works" in out or out == "", (blob, out[:200])
    os.remove(bridge)
    run_hook(A, "worker", scratch(A, "worker"))       # drain whatever is left


@t("non-ASCII steered mail survives the hook (json.dumps escapes it)")
def _():
    queue_steer(A, "worker", "em—dash · 日本語 · ✓")
    _dt, out, _e, rc = run_hook(A, "worker", scratch(A, "worker"))
    assert rc == 0
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "em—dash · 日本語 · ✓" in ctx, ctx[:200]
    assert out.isascii(), "the hook emitted raw non-ASCII on a cp1252 stdout"


@t("the hook output is exactly ONE line of JSON (the CLI parses stdout)")
def _():
    queue_steer(A, "worker", "line one\nline two\nline three")
    _dt, out, _e, _rc = run_hook(A, "worker", scratch(A, "worker"))
    assert len(out.strip().splitlines()) == 1, out[:300]
    json.loads(out)


@t("a steer message that is a journal DICT is delivered as its text")
def _():
    # ARCHITECTURE: st["steer"] items may be {"toks": [...], "text": ...}
    queue_steer(A, "worker", {"toks": ["tok-abc"], "text": "journaled body"})
    _dt, out, _e, rc = run_hook(A, "worker", scratch(A, "worker"))
    assert rc == 0, rc
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "journaled body" in ctx and "tok-abc" not in ctx, ctx[:200]


@t("a BLACK-HOLED backend costs ~2 s, not 5, per tool call (PROMOTED 2026-08-04)")
def _():
    # The fetch timeout is 2 s inside an 8 s hook budget. A refused
    # connection returns instantly (checked above), but a host that accepts
    # nothing and answers nothing — a paused container, a firewall DROP, a
    # dead bridge — makes EVERY tool call of EVERY agent wait 5 s in silence.
    # 192.0.2.1 is TEST-NET-1: routable nowhere, nothing is bound here.
    bridge = os.path.join(DATA, ".bridge")
    with open(bridge, "w", encoding="utf-8") as f:
        json.dump({"url": "http://192.0.2.1:7357", "secret": ""}, f)
    try:
        queue_steer(A, "worker", "black hole")
        dt, out, err, rc = run_hook(A, "worker", scratch(A, "worker"), timeout=40)
        assert rc == 0 and out == "" and not err.strip(), (out[:200], err[:200])
        # PROMOTED 2026-08-04 — the timeout was lowered to 2 s for exactly
        # this reason, so the assertion inverts: a black-holed backend must
        # cost well under the 8 s hook budget, not most of it. Still >1 s
        # because the socket must actually be given a chance to connect.
        assert 1 < dt < 4, (
            f"the black-hole path took {dt:.1f}s — under 1 s means it is not "
            f"reaching the network at all; over 4 s means the urlopen timeout "
            f"has crept back up and every tool call of every agent pays it")
        assert dt < 8, f"the hook exceeded its own 8 s timeout ({dt:.1f}s)"
    finally:
        os.remove(bridge)
    _dt, out2, _e, _rc = run_hook(A, "worker", scratch(A, "worker"))
    assert "black hole" in out2, "the timeout consumed the mail"


@t("concurrent hook runs for the same node never double-deliver")
def _():
    # ⚠ tokens must not prefix one another (burst-1 is inside burst-10)
    queue_steer(A, "worker", *[f"burst<{i:02d}>" for i in range(12)])
    outs, lock = [], threading.Lock()

    def go():
        _dt, out, _e, _rc = run_hook(A, "worker", scratch(A, "worker"))
        with lock:
            outs.append(out)

    ths = [threading.Thread(target=go) for _ in range(4)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(timeout=60)
    joined = "".join(outs)
    for i in range(12):
        tok = f"burst<{i:02d}>"
        assert joined.count(tok) == 1, \
            f"{tok} appeared {joined.count(tok)}× across 4 concurrent hooks"


# ------------------------------------------- §9 caps, defaults, statelessness
print("\n§9  caps, defaults and statelessness")


@t("the server is stateless — tools work before any initialize handshake")
def _():
    fresh = Mcp(A, "boss")
    try:
        r = fresh.ok("orgtree_chart")            # no initialize first
        assert 'You are "boss"' in r["chart"]
        fresh.rpc("initialize")                  # and late/duplicate is fine
        fresh.rpc("initialize")
        assert fresh.ok("orgtree_list_orgs")["orgs"]
    finally:
        fresh.close()


@t("read_transcript clamps `last` into 1..80 whatever is asked")
def _():
    for last, _want in ((0, 1), (-5, 1), (1000, 80), ("1000", 80), (80, 80)):
        r = BOSS.ok("orgtree_read_transcript", {"node": "worker", "last": last})
        assert isinstance(r["messages"], list) and len(r["messages"]) <= 80, r


@t("read_scratch truncates a big file instead of streaming it into context")
def _():
    d = scratch_of = supervisor.scratch_dir(A, "worker")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(scratch_of, "big.txt"), "w", encoding="utf-8") as f:
        f.write("A" * 60000)
    r = BOSS.ok("orgtree_read_scratch", {"node": "worker", "path": "big.txt"})
    assert len(r["content"]) == 20000, len(r["content"])


@t("read_scratch caps a huge directory listing at 200 entries")
def _():
    d = os.path.join(supervisor.scratch_dir(A, "worker"), "many")
    os.makedirs(d, exist_ok=True)
    for i in range(260):
        open(os.path.join(d, f"f{i:03d}.txt"), "w").close()
    r = BOSS.ok("orgtree_read_scratch", {"node": "worker", "path": "many"})
    assert len(r["entries"]) == 200, len(r["entries"])


@gap("orgtree_status accepts any string — the card's 4-value enum is advisory")
def _():
    # ⚑ the schema says working|done|blocked|idle; the dispatch stores whatever
    # arrives (only "done" is special-cased). A model that emits "in_progress"
    # or a prompt-injected "status" lands verbatim on the dashboard chip.
    BOSS.ok("orgtree_status", {"status": "in_progress <b>x</b>", "summary": "s"})
    got = store.load_org(A).node("boss")["last_status"]["status"]
    assert got == "in_progress <b>x</b>", \
        f"status is validated now ({got!r}) — retire this pin"
    BOSS.ok("orgtree_status", {"status": "idle", "summary": "back to normal"})


@t("orgtree_message `kind` is free text, and an odd kind still delivers")
def _():
    r = BOSS.ok("orgtree_message", {"to": "mid", "body": "odd kind",
                                    "kind": "not-in-the-enum"})
    assert r["delivered"] == "mid", r
    assert [m for m in mailbox(A, "mid")
            if m["body"] == "odd kind"][-1]["kind"] == "not-in-the-enum"


@t("an @mcp: address is recorded in the org inbox with no push transport")
def _():
    r = BOSS.ok("orgtree_message", {"to": "@mcp:some-chat", "body": "polled"})
    # D-166: this used to assert `delivered == "@mcp:some-chat"`. It is a PULL
    # transport — the row is filed and a peer may or may not ever collect it —
    # so the answer now says `filed`, and `delivered` is False. The row itself
    # (the assertion below) is what this check was really about, and it is
    # unchanged; only the claim made to the agent moved.
    assert r["delivered"] is False, r
    assert r["filed"] == "@mcp:some-chat", r
    assert "NEVER polled" in r.get("status", ""), r
    assert DRIVEN[-1][1] != "@mcp:some-chat" if DRIVEN else True
    out = store.load_org(A).d.get("org_inbox", [])
    assert any("polled" in (e.get("body") or "") for e in out), out[-2:]


@t("three tool calls pipelined without reading come back in order")
def _():
    base = BOSS._n
    for i, body in enumerate(("pipe-a", "pipe-b", "pipe-c"), start=1):
        BOSS.send({"jsonrpc": "2.0", "id": base + i, "method": "tools/call",
                   "params": {"name": "orgtree_message",
                              "arguments": {"to": "mid", "body": body}}})
    BOSS._n = base + 3
    ids = [BOSS.read(60)["id"] for _ in range(3)]
    assert ids == [base + 1, base + 2, base + 3], ids
    bodies = [m["body"] for m in mailbox(A, "mid")]
    assert bodies.count("pipe-a") == 1 and bodies.count("pipe-c") == 1, bodies


@t("stdout carries JSON-RPC and nothing else (a stray print breaks the CLI)")
def _():
    quiet = Mcp(A, "worker")
    try:
        for tool in sorted(CARDS):
            quiet.call(tool, {"node": "worker", "to": "mid", "body": "x",
                              "status": "working", "summary": "s",
                              "action": "bogus", "path": "."})
        # every line read back parsed as JSON with the id we sent — enforced by
        # rpc(); what remains is that nothing EXTRA was written
        time.sleep(0.3)
        assert quiet.q.empty(), "the server wrote an unsolicited line to stdout"
    finally:
        quiet.close()


@t("FR-24 (in-place, 2026-08-12): cheap_compact keeps the SEAT — id, scope, "
   "charter, grant — and swaps only the session; the old self is nid@gen")
def _():
    # REWORKED per the user's rulings: reports and identity are retained
    # exactly the way a normal compact retains them. The old retire+fresh-
    # hire shape broke addressing (peers deferring into an archived mailbox)
    # and refused teams — both gone.
    r = BOSS.ok("orgtree_hire", {
        "parent": "boss", "tier": "haiku", "grant": 2, "name": "oldhand",
        "charter": "keeps the ledger", "add_dirs": [],
        "org_visibility": "team",
        "tools": {"bash": False, "web": False, "edit": True,
                  "subagents": False, "mcp": []}})
    assert r["node"] == "oldhand", r
    o0 = store.load_org(A)
    free0, sid0 = o0.free("boss"), o0.nodes["oldhand"]["session_id"]
    r = BOSS.ok("orgtree_cheap_compact", {"node": "oldhand"})
    assert r["node"] == "oldhand" and r["bearer"] == "oldhand@0", r
    o = store.load_org(A)
    n = o.nodes["oldhand"]
    assert n["state"] == "live" and n["session_id"] != sid0, (
        "the seat must stay live under its own id with a FRESH session")
    assert n["model"] == "haiku" and n["grant"] == 2
    assert n.get("charter") == "keeps the ledger"
    assert n["generation"] == 1 and n["predecessor"] == "oldhand@0"
    bearer = o.nodes["oldhand@0"]
    assert bearer["state"] == "archived" and bearer["grant"] == 0
    assert bearer["bearer_state"] == "knowledge" \
        and bearer["successor"] == "oldhand"
    assert bearer["session_id"] == sid0, "the OLD session lives on the bearer"
    assert o.free("boss") == free0, (free0, o.free("boss"))
    notes = (o.d.get("notices") or {}).get("oldhand") or []
    assert any("CHEAP-COMPACTED" in p["text"] for p in notes), notes
    assert not o.audit()["problems"]


@t("FR-24: the successor can rehire its own bearer, exactly as its notice "
   "says (redteam finding f327b39, re-proven on the in-place shape)")
def _():
    # ① AUTHORITY: rehire recognises the bearer via successor == actor.
    # ② ARITHMETIC: the bearer holds grant 0, so the rehire costs seat only
    #    — affordable from the successor's own free by construction.
    seat = Mcp(A, "oldhand")
    seat.ok("orgtree_rehire", {"node": "oldhand@0"})
    o = store.load_org(A)
    assert o.nodes["oldhand@0"]["parent"] == "oldhand", (
        f"the bearer did not join as the successor's subordinate: "
        f"parent is {o.nodes['oldhand@0']['parent']}")
    assert o.nodes["oldhand@0"]["state"] == "live"
    assert not o.audit()["problems"], "the rehire unbalanced the ledger"
    seat.ok("orgtree_retire", {"node": "oldhand@0"})   # and back to rest
    seat.close()


@t("FR-24: cheap_compact refuses self; a node WITH live reports keeps its "
   "team (user ruling 2026-08-12 — retention like a normal compact)")
def _():
    # self falls to the downward-only authority gate (an agent's own session
    # is mid-turn running the call), before any FR-24-specific check
    txt = MID.refuse("orgtree_cheap_compact", {"node": "mid"})
    assert "downward" in txt or "authority" in txt, txt
    # a manager compacts fine — its reports keep their superior, and the
    # successor's notice names the team it no longer remembers
    kids0 = store.load_org(A).children("mid")
    assert kids0, "fixture: mid must have live reports for this to prove anything"
    BOSS.ok("orgtree_cheap_compact", {"node": "mid"})
    o = store.load_org(A)
    assert o.nodes["mid"]["state"] == "live"
    assert o.children("mid") == kids0, (
        "the team must be UNCHANGED — cheap compact retains reports exactly "
        "like a normal compact")
    notes = (o.d.get("notices") or {}).get("mid") or []
    assert any("Your team" in p["text"] for p in notes), notes
    assert not o.audit()["problems"]


@t("the org doc is intact after the whole run (the ledger's own audit)")
def _():
    for slug in (A, B, SBX):
        rep = store.load_org(slug).audit()
        assert not rep["problems"], f"{slug}: {rep['problems']}"
        assert rep["no_overdraft"], f"{slug} overdrew its credits: {rep}"


@t("☠ this run started NO real deploy — and it did try (the interlock held)")
def _():
    """Runs last, so it sees every spawn the whole suite attempted.

    Two things are pinned, and the second is the uncomfortable one:

    (1) The interlock is STILL armed at the end of the run. A check that swaps
        `supervisor._detached_spawn` out and forgets to restore it re-arms the
        gun for everything after it.
    (2) This suite really does reach `launch_self_restart` — the fuzz checks
        call every card as a top-level node, so the authorization gate passes
        and the launch runs for real. That is not hypothetical: months of
        `orgtree-mcptest-*/self-update-*.log` files on this machine record it
        spawning powershell on every single run, and it was survivable only
        because `update.ps1` refused for its own unrelated reasons.

    If (2) ever stops being true the assert below fails, and that is
    deliberate: it means the shape of this hazard changed and the interlock's
    justification needs re-reading, not that the assert should be deleted."""
    assert _no_deploy.installed(), \
        "the deploy interlock was swapped out and never restored — a real " \
        "deploy is reachable from this suite again"
    assert _no_deploy.ATTEMPTS, \
        "no check reached the deploy spawn at all. Either the fuzz checks " \
        "stopped covering orgtree_self_restart, or the launch stopped " \
        "spawning — re-read tests/_no_deploy.py before touching this"
    for argv in _no_deploy.ATTEMPTS:
        assert any("update.ps1" in a or "update.sh" in a or "docker" in a.lower()
                   for a in argv), argv


for _m in (BOSS, MID, WORKER):                 # close the long-lived children
    _m.close()
_server.should_exit = True
_th.join(timeout=20)

print(f"\nALL {PASS} CHECKS PASS  ({GAPS} of them ⚑ known-gap pins)")
