"""Live end-to-end drill for the F-06 mailserver (spec §11 №7).

Two REAL orgtree instances — their own ports and ORGTREE_DATA roots, run
from this repo's venv — against a THROWAWAY hub container (ephemeral, no
volume, port 7371; the live nova-desk hub is untouched). This is the
genuine two-machine test the hermetic suites cannot be: real daemon
threads, real long-polls, real Docker networking, a real process restart.

Pins: hub name discovery · auto-registration of freshly created orgs ·
roster + presence propagation · text mail A→B landing in the org inbox ·
sender-side delivery states reaching `delivered` · an attachment riding
the same path · no redelivery after a full instance restart (persisted
seen-ring) · reconnection after restart.

Operator-run (needs Docker; NOT auto-discovered by run_tests):
    python tools/net_drill.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "backend")

HUB_PORT = 7371
HUB_ADDR = f"http://127.0.0.1:{HUB_PORT}"
HUB_NAME = "drill-hub"
HUB_CONTAINER = "orgtree-mailhub-drill"
A_PORT, B_PORT = 7461, 7462

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    checks.append((name, ok))
    print(("✓" if ok else "✗"), name, flush=True)


def req(port: int, method: str, path: str, body: Any = None,
        raw: bytes | None = None, headers: dict[str, str] | None = None,
        ) -> Any:
    data = raw if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def wait_for(what: str, fn: Any, timeout: float = 90.0,
             interval: float = 1.0) -> Any:
    """Poll fn() until it returns a truthy value; check() the outcome."""
    deadline = time.monotonic() + timeout
    val: Any = None
    while time.monotonic() < deadline:
        try:
            val = fn()
        except Exception:                                     # noqa: BLE001
            val = None
        if val:
            break
        time.sleep(interval)
    check(what, bool(val))
    return val


def spawn(port: int, data_root: str) -> subprocess.Popen[bytes]:
    env = {**os.environ, "ORGTREE_DATA": data_root,
           "ORGTREE_PORT": str(port),
           # no public/bridge listeners: the bridge defaults to 7362 and
           # would collide with a live production instance on this machine
           "ORGTREE_BRIDGE_PORT": "0"}
    env.pop("ORGTREE_PUBLIC_PORT", None)
    return subprocess.Popen(
        [sys.executable, "-m", "orgtree.api"], cwd=BACKEND, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tree(port: int, slug: str) -> dict[str, Any]:
    return req(port, "GET", f"/api/orgs/{slug}")


def hub_of(t: dict[str, Any]) -> dict[str, Any]:
    hubs = (t.get("net") or {}).get("hubs") or []
    return hubs[0] if hubs else {}


def inbox_rows(t: dict[str, Any], peer: str) -> list[dict[str, Any]]:
    rows = ((t.get("org_inbox") or {}).get("entries")) or []
    return [e for e in rows if e.get("dir") == "in" and e.get("peer") == peer]


procs: list[subprocess.Popen[bytes]] = []
roots: list[str] = []
try:
    # ---- throwaway hub (ephemeral: --rm, no volume) ----
    subprocess.run(["docker", "build", "-q", "-t", "orgtree-mailhub:drill",
                    os.path.join(REPO, "hub")], check=True,
                   capture_output=True, timeout=600)
    subprocess.run(["docker", "rm", "-f", HUB_CONTAINER],
                   capture_output=True)
    subprocess.run(["docker", "run", "-d", "--rm", "--name", HUB_CONTAINER,
                    "-p", f"{HUB_PORT}:7370", "-e", f"HUB_NAME={HUB_NAME}",
                    "orgtree-mailhub:drill"], check=True,
                   capture_output=True, timeout=120)

    def hub_health() -> Any:
        with urllib.request.urlopen(f"{HUB_ADDR}/healthz",
                                    timeout=3) as resp:
            return json.loads(resp.read())
    h = wait_for("hub container up", hub_health, timeout=60)
    check("hub self-identifies by name", (h or {}).get("name") == HUB_NAME)

    # ---- two instances, isolated data roots, defaults → the drill hub ----
    for port in (A_PORT, B_PORT):
        root = tempfile.mkdtemp(prefix=f"net-drill-{port}-")
        roots.append(root)
        with open(os.path.join(root, "defaults.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"net_hub_address": HUB_ADDR}, f)
        procs.append(spawn(port, root))
    for port in (A_PORT, B_PORT):
        wait_for(f"instance :{port} up",
                 lambda p=port: req(p, "GET", "/api/orgs") is not None,
                 timeout=60)

    # ---- orgs mint identity at creation and auto-register ----
    req(A_PORT, "POST", "/api/orgs", {"name": "drill-a"})
    req(B_PORT, "POST", "/api/orgs", {"name": "drill-b"})
    net_a = req(A_PORT, "GET", "/api/orgs/drill-a/net")
    net_b = req(B_PORT, "GET", "/api/orgs/drill-b/net")
    slug_a = ((net_a or {}).get("identity") or {}).get("slug") or ""
    slug_b = ((net_b or {}).get("identity") or {}).get("slug") or ""
    check("identities minted (three-part slugs)",
          slug_a.startswith("drill-a.") and slug_b.startswith("drill-b.")
          and len(slug_a.rsplit(".", 1)[-1]) == 6)

    def a_sees_b() -> Any:
        hb = hub_of(tree(A_PORT, "drill-a"))
        return (hb.get("connected") and hb.get("name") == HUB_NAME
                and any(r.get("slug") == slug_b
                        for r in hb.get("roster") or []))
    wait_for("A connected, hub name discovered, B in roster", a_sees_b)
    hb = hub_of(tree(A_PORT, "drill-a"))
    check("B shows as present (online or fresh last_seen)",
          any(r.get("slug") == slug_b and (r.get("online")
              or r.get("last_seen")) for r in hb.get("roster") or []))
    check("local hub entry not hidden once registered",
          not hb.get("hidden"))

    # ---- text mail A → B ----
    body_txt = "net drill ping — the quick brown fox"
    sent = req(A_PORT, "POST", "/api/orgs/drill-a/org_inbox/send",
               {"to": f"@net:{slug_b}", "body": body_txt})
    oid = (sent or {}).get("id")
    check("user compose accepted", bool(oid))
    wait_for("mail landed in B's org inbox",
             lambda: any(e.get("body") == body_txt for e in inbox_rows(
                 tree(B_PORT, "drill-b"), f"@net:{slug_a}")))

    def a_out_state() -> Any:
        rows = ((tree(A_PORT, "drill-a").get("org_inbox") or {})
                .get("entries")) or []
        row = next((e for e in rows if e.get("id") == oid), {})
        return row.get("state") in ("delivered", "read")
    wait_for("sender state ladder reached delivered", a_out_state)

    # ---- attachment A → B (same path, real blob upload/download) ----
    # NB the drill orgs have NO agents, so the recipient side leaves no
    # per-agent uploads/ copy to inspect (attachment metadata rides the
    # per-node MailEntry, and the temp download is discarded by design).
    # Observable evidence instead: (1) the in-row lands WITHOUT the
    # "[attachment … could not be fetched]" note the client appends on a
    # failed blob download — so B really fetched the bytes; (2) the blob
    # round-trips the hub verbatim under B's credentials.
    payload = b"net-drill attachment payload " * 100
    up = req(A_PORT, "POST",
             "/api/orgs/drill-a/org_inbox/upload?name=drill.txt",
             raw=payload, headers={"Content-Type":
                                   "application/octet-stream"})
    check("attachment staged", bool((up or {}).get("id")))
    req(A_PORT, "POST", "/api/orgs/drill-a/org_inbox/send",
        {"to": f"@net:{slug_b}", "body": "with attachment",
         "attachments": [up["id"]]})
    wait_for("attachment mail landed (blob fetched, no failure note)",
             lambda: any(e.get("body") == "with attachment"
                         for e in inbox_rows(tree(B_PORT, "drill-b"),
                                             f"@net:{slug_a}")))

    def hub_attachment_id() -> Any:
        with urllib.request.urlopen(
                f"{HUB_ADDR}/ui/messages?org={slug_b}", timeout=5) as resp:
            msgs = json.loads(resp.read()).get("messages") or []
        for m in msgs:
            if m.get("body") == "with attachment" and m.get("attachments"):
                return m["attachments"][0].get("id")
        return None
    aid = wait_for("hub recorded the attachment", hub_attachment_id,
                   timeout=30)
    if aid:
        secret_b = ((net_b or {}).get("identity") or {}).get("secret") or ""
        r = urllib.request.Request(
            f"{HUB_ADDR}/api/attachments/{aid}",
            headers={"X-Org-Auth": f"{slug_b}:{secret_b}"})
        with urllib.request.urlopen(r, timeout=15) as resp:
            blob = resp.read()
        check("blob round-trips the hub verbatim", blob == payload)

    # ---- restart B: persisted seen-ring must prevent redelivery ----
    before = len(inbox_rows(tree(B_PORT, "drill-b"), f"@net:{slug_a}"))
    procs[1].terminate()
    procs[1].wait(timeout=30)
    procs[1] = spawn(B_PORT, roots[1])
    wait_for("B restarted", lambda: req(B_PORT, "GET", "/api/orgs")
             is not None, timeout=60)
    wait_for("B reconnected to the hub after restart",
             lambda: hub_of(tree(B_PORT, "drill-b")).get("connected"))
    time.sleep(8)          # a couple of poll cycles for any wrong redelivery
    after = len(inbox_rows(tree(B_PORT, "drill-b"), f"@net:{slug_a}"))
    check("no redelivery after restart (seen-ring held)", after == before)

finally:
    for port, slug in ((A_PORT, "drill-a"), (B_PORT, "drill-b")):
        try:
            req(port, "DELETE", f"/api/orgs/{slug}")
        except Exception:                                     # noqa: BLE001
            pass
    for p in procs:
        try:
            p.terminate()
            p.wait(timeout=15)
        except Exception:                                     # noqa: BLE001
            p.kill()
    subprocess.run(["docker", "rm", "-f", HUB_CONTAINER],
                   capture_output=True)
    time.sleep(1.0)        # let uvicorn release the data roots
    for r in roots:
        shutil.rmtree(r, ignore_errors=True)

fails = [n for n, ok in checks if not ok]
print("\nRESULT:", "PASS" if not fails else f"FAIL: {fails}")
sys.exit(1 if fails else 0)
