"""Frozen relay allowlist and fixed-upstream integration checks.

Run:
    python backend/tests/test_frozen_gateway.py

No Docker and no external network.  A loopback upstream records whether a
denied request opened the relay at all; this makes the negative checks capable
of catching a permissive-method/path mutation rather than merely inspecting
constants.
"""

from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import frozen_gateway as gateway  # noqa: E402


PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


SECRET = "ab" * 16
ALLOWED = [
    "/api/agent",
    "/api/orgs/example/nodes/alice@2/steer",
    f"/anthropic/{SECRET}/v1/messages",
    f"/anthropic/{SECRET}/v1/messages?beta=1",
]
DENIED = [
    "/", "/api/agent/", "//api/agent",
    "/api/orgs/example/nodes/alice/steer/",
    f"/anthropic/{SECRET}/v1/models",
    f"/anthropic/{SECRET}/v1/messages/",
    "/anthropic/short/v1/messages",
    "http://127.0.0.1:1/api/agent",
    "http://[broken/api/agent",
]
WIRE_DENIED = DENIED[:-1]  # http.client itself refuses the malformed IPv6 URL


check("the pure allowlist admits exactly the three required POST shapes",
      lambda: (
          None if all(gateway.allowed_operation("POST", p) for p in ALLOWED)
          and all(not gateway.allowed_operation("POST", p) for p in DENIED)
          and all(not gateway.allowed_operation(m, p)
                  for m in ("GET", "HEAD", "PUT", "DELETE", "PATCH", "CONNECT")
                  for p in ALLOWED)
          else (_ for _ in ()).throw(AssertionError("allowlist mismatch"))))


def bind_never_accepts_a_wildcard():
    assert gateway.bind_address("127.0.0.1", 8765) == "127.0.0.1"
    try:
        gateway.bind_address("0.0.0.0", 8765)
        raise AssertionError("wildcard relay bind was accepted")
    except ValueError:
        pass


check("the relay bind resolver refuses a wildcard interface",
      bind_never_accepts_a_wildcard)


def upstream_requires_an_explicit_usable_port():
    for origin in ("http://127.0.0.1", "http://127.0.0.1:0",
                   "http://127.0.0.1:65536"):
        try:
            gateway.configure(origin)
            raise AssertionError(f"invalid upstream was accepted: {origin}")
        except ValueError:
            pass


check("the fixed upstream requires an explicit usable TCP port",
      upstream_requires_an_explicit_usable_port)


class Upstream(BaseHTTPRequestHandler):
    hits = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(n)
        type(self).hits.append((self.path, dict(self.headers), body))
        payload = b"upstream-ok:" + body
        self.send_response(201)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload[:4])
        self.wfile.flush()
        self.wfile.write(payload[4:])

    def log_message(self, fmt, *args):
        pass


upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
upstream_thread.start()
gateway.configure(f"http://127.0.0.1:{upstream.server_port}")
relay = ThreadingHTTPServer(("127.0.0.1", 0), gateway.FrozenGatewayHandler)
relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
relay_thread.start()


def request(method, target, body=b"{}", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
    conn.request(method, target, body=body, headers=headers or {})
    res = conn.getresponse()
    out = res.status, res.read(), dict(res.getheaders())
    conn.close()
    return out


try:
    def denied_never_opens_upstream():
        Upstream.hits.clear()
        for path in WIRE_DENIED:
            status, _, _ = request("POST", path)
            assert status in (403, 405), (path, status)
        for method in ("GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS",
                       "CONNECT", "BREW"):
            status, _, _ = request(method, "/api/agent", body=b"")
            assert status in (405, 501), (method, status)
        assert Upstream.hits == [], Upstream.hits

    check("denied methods/paths open no upstream connection",
          denied_never_opens_upstream)

    def allowed_is_fixed_upstream_and_streams_back():
        Upstream.hits.clear()
        status, body, headers = request(
            "POST", "/api/agent?one=1", b'{"tool":"orgtree_chart"}',
            {"Content-Type": "application/json",
             "X-Orgtree-Bridge": SECRET,
             "Connection": "x-drop", "X-Drop": "do-not-forward"})
        assert status == 201 and body == b'upstream-ok:{"tool":"orgtree_chart"}'
        assert len(Upstream.hits) == 1, Upstream.hits
        path, got_headers, got_body = Upstream.hits[0]
        assert path == "/api/agent?one=1", path
        assert got_body == b'{"tool":"orgtree_chart"}'
        assert got_headers.get("X-Orgtree-Bridge") == SECRET
        assert "X-Drop" not in got_headers, got_headers
        assert headers.get("Connection", "").lower() == "close"

    check("an allowed request reaches only the fixed upstream and streams back",
          allowed_is_fixed_upstream_and_streams_back)

    def anthropic_operation_reaches_same_fixed_upstream():
        Upstream.hits.clear()
        status, _, _ = request(
            "POST", f"/anthropic/{SECRET}/v1/messages?beta=1", b'{"model":"x"}')
        assert status == 201
        assert Upstream.hits[0][0] == \
            f"/anthropic/{SECRET}/v1/messages?beta=1"

    check("the one Anthropic operation is relayed without widening its target",
          anthropic_operation_reaches_same_fixed_upstream)
finally:
    relay.shutdown()
    upstream.shutdown()
    relay.server_close()
    upstream.server_close()
    relay_thread.join(timeout=5)
    upstream_thread.join(timeout=5)

print(f"ALL {PASS} CHECKS PASS")
