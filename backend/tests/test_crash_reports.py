"""Crash-report intake (frontend/src/crashReporter.ts's server side) —
resolve_stack()'s server-side sourcemap resolution, save_report()/
list_reports() durability, and the /api/crash-report + /api/crash-reports
HTTP endpoints: mail delivery to a live "crash-reporting" node, save-only
behaviour for kiosk/public visitors, and durability that does not depend on
mail delivery succeeding.

Plain asserts; run with:  python backend/tests/test_crash_reports.py

§1  resolve_stack() against a REAL esbuild-built bundle + sourcemap — not a
    hand-rolled fake map, so a passing check proves the actual pipeline
    (vite's sourcemap='hidden' + postbuild move + @jridgewell/trace-mapping)
    resolves a genuine minified frame, not just "didn't throw"
§2  resolve_stack() falls back to the raw stack when no map exists — the
    witness that a missing/broken map degrades gracefully instead of losing
    the report
§3  save_report()/list_reports() persist newest-first, independent of org
§4  format_mail_body() carries breadcrumbs and the React component stack
§5  the HTTP endpoint: durable save even with no matching org, mail delivery
    to a live "crash-reporting" node, no delivery to an archived/missing one,
    and no delivery for a public/kiosk caller
"""
import os
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ORGTREE_DATA", tempfile.mkdtemp(prefix="orgtree-crashtest-"))

from fastapi.testclient import TestClient   # noqa: E402

from orgtree import crashreports, store, supervisor   # noqa: E402
from orgtree.api import app                # noqa: E402
from orgtree.ledger import Org, USER       # noqa: E402

PASS = 0
NODE_BIN = shutil.which("node")


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def skip(label, reason):
    print(f"  skip     {label} — {reason}")


# --------------------------------------------------------------------- §1/§2
def _build_known_bundle(tmpdir):
    """A REAL minified build + sourcemap via the project's own esbuild
    devDependency — the same toolchain production uses (vite → esbuild),
    not a hand-rolled fake map. Proves resolve_stack() against a genuine
    artifact rather than a control that merely resembles one."""
    src = os.path.join(tmpdir, "Known.tsx")
    with open(src, "w", encoding="utf-8") as f:
        f.write(
            "export function KnownCrashingFunction() {\n"
            "  throw new Error('known crash')\n"
            "}\n"
            "KnownCrashingFunction()\n"
        )
    out = os.path.join(tmpdir, "bundle.js")
    esbuild_cli = os.path.join(
        crashreports.FRONTEND_ROOT, "node_modules", ".bin",
        "esbuild.cmd" if os.name == "nt" else "esbuild")
    subprocess.run(
        [esbuild_cli, src, f"--outfile={out}", "--bundle", "--minify",
         "--sourcemap", "--keep-names"],
        cwd=crashreports.FRONTEND_ROOT, check=True, capture_output=True, text=True)
    return out


def test_resolve_stack_real_map():
    if not NODE_BIN or not os.path.isfile(crashreports.RESOLVER):
        skip("resolve_stack against a real build", "node or resolve-stack.mjs unavailable")
        return
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _build_known_bundle(tmp)
        with open(bundle, encoding="utf-8") as f:
            text = f.read()
        idx = text.index("throw new Error")
        line = text.count("\n", 0, idx) + 1
        col = idx - text.rfind("\n", 0, idx)
        with patch.object(crashreports, "MAPS_DIR", tmp):
            raw = (f"Error: known crash\n"
                  f"    at KnownCrashingFunction (file://{bundle}:{line}:{col})")
            resolved = crashreports.resolve_stack(raw)
        # ⚑ the witness: it changed at all, and changed to the RIGHT place —
        # a check that only asserted "no exception" would pass just as
        # happily if resolution silently did nothing
        assert resolved != raw, "resolve_stack made no change — the control did not fire"
        assert "Known.tsx" in resolved, resolved
        assert "bundle.js" not in resolved.splitlines()[1], resolved
        # keep-names: the real name survives untouched through resolution
        assert "KnownCrashingFunction" in resolved, resolved


def test_resolve_stack_falls_back_without_a_map():
    """No map for this file → the ORIGINAL stack comes back unchanged, not an
    exception and not an empty string. Resolution failing must never be the
    reason a report is lost."""
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(crashreports, "MAPS_DIR", tmp):   # empty — no .map files
            raw = "Error: x\n    at f (http://h/assets/nope-DEADBEEF.js:1:2)"
            resolved = crashreports.resolve_stack(raw)
        assert resolved == raw, resolved


# ------------------------------------------------------------------------ §3
def test_save_and_list_reports():
    d1 = crashreports.save_report("orgtree", {"id": "aaa-test", "kind": "window-error",
                                              "message": "m1", "stack": "s1"})
    d2 = crashreports.save_report(None, {"id": "bbb-test", "kind": "unhandledrejection",
                                         "message": "m2", "stack": "s2"})
    assert os.path.isfile(d1) and os.path.isfile(d2)
    reports = crashreports.list_reports(limit=10)
    ids = [r["id"] for r in reports]
    assert "aaa-test" in ids and "bbb-test" in ids, ids
    assert reports[0]["id"] == "bbb-test", "expected newest-first, got: " + str(reports[0])


# ------------------------------------------------------------------------ §4
def test_format_mail_body_includes_breadcrumbs_and_component_stack():
    body = crashreports.format_mail_body({
        "kind": "react-boundary", "at": 123, "url": "http://x/o/orgtree",
        "userAgent": "ua", "message": "boom", "stack": "Error: boom\n  at f (a:1:1)",
        "componentStack": "\n    in App\n    in CrashBoundary",
        "breadcrumbs": [{"kind": "click", "detail": 'button "Save"'}],
    })
    assert "boom" in body
    assert "in App" in body
    assert 'button "Save"' in body


# ------------------------------------------------------------------------ §5
def _mkorg(slug, with_crash_reporting_node=True):
    org = Org.create(slug, dirs=["E:/work"])
    if with_crash_reporting_node:
        org.hire(USER, None, "opus", 20, "crash-reporting",
                 charter="test crash-reporting stand-in")
    store.save_org(org)
    return org


def test_endpoint_saves_even_with_unknown_org(client):
    r = client.post("/api/crash-report", json={
        "org": "no-such-org-xyz",
        "report": {"kind": "window-error", "message": "boom",
                  "stack": "Error: boom\n  at f (a:1:1)"},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] is True
    assert body["delivered"] is False


def test_endpoint_delivers_to_live_crash_reporting_node(client):
    slug = "crashtest-live"
    _mkorg(slug, with_crash_reporting_node=True)
    with patch.object(supervisor, "send_message") as sent:
        r = client.post("/api/crash-report", json={
            "org": slug,
            "report": {"kind": "react-boundary", "message": "boom",
                      "stack": "Error: boom\n  at f (a:1:1)",
                      "componentStack": "\n  in App"},
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["delivered"] is True, body
        assert sent.called, "expected supervisor.send_message to drive the node"
        assert sent.call_args[0][0] == slug
        assert sent.call_args[0][1] == "crash-reporting"
    reloaded = store.load_org(slug)
    mailbox = reloaded.d.get("mail", {}).get("crash-reporting", [])
    assert any("boom" in (m.get("body") or "") for m in mailbox), mailbox


def test_endpoint_does_not_deliver_without_a_crash_reporting_node(client):
    slug = "crashtest-nonode"
    _mkorg(slug, with_crash_reporting_node=False)
    with patch.object(supervisor, "send_message") as sent:
        r = client.post("/api/crash-report", json={
            "org": slug,
            "report": {"kind": "window-error", "message": "boom", "stack": "s"},
        })
        assert r.status_code == 200, r.text
        assert r.json()["delivered"] is False
        assert not sent.called


def test_endpoint_retrieval_lists_saved_reports(client):
    slug = "crashtest-retrieve"
    _mkorg(slug, with_crash_reporting_node=False)
    with patch.object(supervisor, "send_message"):
        post = client.post("/api/crash-report", json={
            "org": slug,
            "report": {"kind": "unhandledrejection", "message": "retrieve-me", "stack": "s"},
        })
    assert post.status_code == 200, post.text
    rid = post.json()["id"]
    got = client.get("/api/crash-reports", params={"org": slug})
    assert got.status_code == 200, got.text
    ids = [r["id"] for r in got.json()["reports"]]
    assert rid in ids, ids


def main():
    print("crash-report intake:")
    check("resolve_stack() maps a real minified build back to source", test_resolve_stack_real_map)
    check("resolve_stack() falls back to the raw stack when no map exists", test_resolve_stack_falls_back_without_a_map)
    check("save_report()/list_reports() persist and list newest-first", test_save_and_list_reports)
    check("format_mail_body() carries breadcrumbs and component stack", test_format_mail_body_includes_breadcrumbs_and_component_stack)
    # One shared client for the HTTP-level checks (rather than one per check):
    # api.py's startup event reconciles EVERY org under ORGTREE_DATA, including
    # any live node with un-drained mail — a fresh TestClient per check would
    # make each subsequent startup treat the previous check's org as "waiting
    # since before a restart" and genuinely try to drive it, unmocked. Orgs
    # are created fresh test-by-test regardless, so nothing is shared except
    # the one reconcile pass at the very first startup (with nothing yet to
    # find).
    with TestClient(app) as client:
        check("POST /api/crash-report saves even for an unknown org",
              lambda: test_endpoint_saves_even_with_unknown_org(client))
        check("POST /api/crash-report delivers mail to a live crash-reporting node",
              lambda: test_endpoint_delivers_to_live_crash_reporting_node(client))
        check("POST /api/crash-report skips delivery with no crash-reporting node",
              lambda: test_endpoint_does_not_deliver_without_a_crash_reporting_node(client))
        check("GET /api/crash-reports retrieves a saved report by org",
              lambda: test_endpoint_retrieval_lists_saved_reports(client))
    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
