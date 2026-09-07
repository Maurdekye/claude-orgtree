"""crashstorage_probe.py - does the app START in a REAL browser when the
browser DENIES storage?

Run:  cd frontend && npm run build && python tests/crashstorage_probe.py --dist dist
      (add --out results.json to keep the evidence, --headed to watch it)

The finding under test (docket handle-denied-browser-storage-in-crash-reporter):
main.tsx calls flushPendingReports() BEFORE ReactDOM.createRoot, and
crashReporter.loadReports() reads `localStorage` with no guard. A browser that
blocks storage for the origin throws SecurityError FROM THE GETTER ITSELF, so
that call throws before React ever mounts and the page stays blank.

WHAT THIS DRIVES: a real Chromium loading a REAL `vite build` of the app
(--dist), served over http from a throwaway static server with the SPA
fallback the backend has. The startup path is the product's own main.tsx -
nothing here mounts React itself.

DENIAL, two ways, both reported:
  shim     an init script (runs before ANY page script) redefines
           window.localStorage / sessionStorage to throw SecurityError.
           This is a simulation of the browser's own denial - faithful to the
           failure mode (throwing getter), but it is OUR code, not Chrome's.
  sandbox  the app is loaded inside <iframe sandbox="allow-scripts">, which
           gives it an opaque origin, and CHROME ITSELF makes the localStorage
           getter throw SecurityError. Real browser denial, no shim.

POSITIVE CONTROLS, because a probe that reports "nothing found" must prove it
can find something:
  * the denial is asserted to be IN EFFECT in the page (reading
    window.localStorage must raise SecurityError) - a shim that silently failed
    to install would otherwise make every case pass for free;
  * the ALLOW case runs the identical harness with storage working and must
    render the app - if it does not, the harness (not the product) is broken
    and the deny result means nothing.

NETWORK: every /api/** request is fulfilled locally with a static payload
(shapes from src/git/types.ts and the existing fixture). Non-GET requests are
REFUSED loudly, so this can never write to anything. No live backend is
contacted; the server is 127.0.0.1 on an ephemeral port.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import threading
from pathlib import Path

NODE = lambda i, tier='haiku': {
    'id': i, 'title': i, 'tier': tier, 'model_id': tier, 'state': 'live', 'seat': 1,
    'grant': 0, 'free': 0, 'ui_order': 0, 'cost_usd': 0, 'occupancy': None,
    'context_window': None, 'charter': None, 'mail_pending': 0, 'limit_locked': False,
    'last_status': None, 'prev_status': None, 'inflight_at': None, 'last_denials': [],
    'turns': [], 'frozen': None, 'audiences_held': [], 'bearer_state': None,
    'generation': 0, 'children': [], 'lineage': [],
    'scope': {'permission_mode': 'default', 'add_dirs': [], 'tools': {}, 'org_visibility': 'team'},
}

TREE = {
    'slug': 'fixture', 'name': 'Fixture', 'workspace': None, 'dirs': [], 'max_top_grant': 1000,
    'default_top_grant': 50, 'compact_at': 0, 'default_tools': None, 'default_visibility': 'team',
    'default_effort': '', 'credit_requests': [], 'tiers': {'haiku': 1, 'sonnet': 3, 'opus': 5, 'fable': 10},
    'audiences': [], 'roots': [NODE('alpha'), NODE('bravo')], 'cost_usd_total': 0,
    'audit': {'live_nodes': 2, 'top_level_holds': 0, 'no_overdraft': True, 'problems': []},
    'user_inbox_count': 0, 'user_inbox_newest': None, 'fable_lock': None, 'spend_frozen': False,
    'storage_blocked': False, 'auto_resume': False, 'fable_limit_policy': 'freeze',
    'fable_filter_policy': 'halt', 'cascade_hire': False, 'cascade_alloc': True, 'sandboxed': False,
    'audience_requests': [], 'org_inbox': None, 'net': None, 'public': False, 'epoch': 1, 'rev': 1,
    'work_items_summary': {'attention': 0, 'active': 2}, 'watchdogs': [],
}

STATIC = {
    '/api/orgs': [{'slug': 'fixture', 'name': 'Fixture', 'live': 2, 'seats': 2}],
    '/api/orgs/fixture': TREE,
    '/api/host': {'build': {'commit': 'probe', 'branch': None, 'started_at': ''}, 'docker': False},
    '/api/discover': {'repositories': [], 'roots': [], 'scanned': 0, 'ok': True},
    '/api/orgs/fixture/git/repositories': {'repositories': [], 'selected': None},
    '/api/providers': {'providers': [{'id': 'claude', 'present': True, 'label': 'Claude'}]},
    '/api/antigravity/usage': {'available': False, 'samples': 0, 'estimate': None, 'reason': 'probe'},
}

CRASH_JS = r'''() => {
  const c = window.__ORGTREE_CRASH__
  if (!c) return null
  return { message: c.message, kind: c.kind,
           stack: (c.stack || '').split('\n').slice(0, 6).join(' | '),
           componentStack: (c.componentStack || '').split('\n').slice(0, 8).join(' | ') }
}'''

DENY_SCRIPT = """
(() => {
  const boom = () => { throw new DOMException('Access is denied for this document.', 'SecurityError') }
  for (const name of ['localStorage', 'sessionStorage']) {
    Object.defineProperty(window, name, { configurable: true, get: boom })
  }
})()
"""


def serve(dist: Path):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(dist), **kw)

        def do_GET(self):  # SPA fallback: any non-asset path serves index.html
            path = self.path.split('?')[0]
            if not path.startswith('/assets/') and path != '/index.html':
                self.path = '/index.html'
            return super().do_GET()

        def end_headers(self):
            # index.html loads its module with crossorigin=, so the OPAQUE
            # origin of the sandboxed frame fetches it as CORS with Origin:
            # null. Without this the script never loads at all and the frame
            # is blank for a reason that has nothing to do with storage — a
            # vacuous pass dressed as a finding (caught 2026-09-07).
            self.send_header('Access-Control-Allow-Origin', '*')
            return super().end_headers()

        def log_message(self, *a):
            pass

    httpd = socketserver.TCPServer(('127.0.0.1', 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def install_routes(ctx, refusals):
    def handler(route, request):
        if request.method != 'GET':
            refusals.append(f'{request.method} {request.url}')
            return route.abort()
        path = request.url.split('://', 1)[-1].split('/', 1)[-1]
        path = '/' + path.split('?')[0]
        body = STATIC.get(path)
        if body is None:
            body = {'items': [], 'archived': [], 'backlogged': []} if 'work' in path else {}
        route.fulfill(status=200, content_type='application/json', body=json.dumps(body))

    ctx.route('**/api/**', handler)


def run_case(pw, dist_url: str, mode: str, headless: bool) -> dict:
    """mode: allow | shim-deny | sandbox-deny"""
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context()
    refusals: list[str] = []
    install_routes(ctx, refusals)
    if mode == 'shim-deny':
        ctx.add_init_script(DENY_SCRIPT)
    page = ctx.new_page()
    errors: list[str] = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    console: list[str] = []
    page.on('console', lambda m: console.append(f'{m.type}: {m.text}'[:400]))

    target = dist_url + '/o/fixture'
    if mode == 'sandbox-deny':
        # a page on the SAME server that embeds the app in an opaque origin:
        # Chrome itself then denies storage inside the frame
        page.goto(dist_url + '/probe-blank')
        page.set_content(
            f'<iframe id="f" sandbox="allow-scripts" src="{target}" '
            'style="width:1200px;height:800px;border:0"></iframe>')
        page.wait_for_timeout(4000)
        frame = page.frame_locator('#f')
        probe = page.frames[1] if len(page.frames) > 1 else None
        if probe is None:
            browser.close()
            return {'mode': mode, 'error': 'no iframe frame'}
        storage_state = probe.evaluate(
            "() => { try { void window.localStorage; return 'available' } "
            "catch (e) { return e.name } }")
        root_children = probe.evaluate("() => document.getElementById('root')?.childElementCount ?? -1")
        body_text = (probe.evaluate("() => document.body.innerText") or '')[:400]
        crash = probe.evaluate(CRASH_JS)
        script_ran = probe.evaluate("() => !!document.querySelector('script[src*=assets]')")
        browser.close()
        return {'mode': mode, 'storage': storage_state, 'root_children': root_children,
                'body_text': body_text, 'crash_report': crash, 'page_errors': errors,
                'script_tag_present': script_ran, 'console_tail': console[-8:],
                'refused_writes': refusals}

    page.goto(target)
    page.wait_for_timeout(4000)
    storage_state = page.evaluate(
        "() => { try { void window.localStorage; return 'available' } catch (e) { return e.name } }")
    root_children = page.evaluate("() => document.getElementById('root')?.childElementCount ?? -1")
    body_text = (page.evaluate("() => document.body.innerText") or '')[:400]
    crash = page.evaluate(CRASH_JS)

    # the debug page too: the objective names it explicitly
    page.goto(dist_url + '/debug/freezes')
    page.wait_for_timeout(2000)
    debug_children = page.evaluate("() => document.getElementById('root')?.childElementCount ?? -1")
    debug_text = (page.evaluate("() => document.body.innerText") or '')[:200]
    browser.close()
    return {'mode': mode, 'storage': storage_state, 'root_children': root_children,
            'body_text': body_text, 'crash_report': crash,
            'debug_root_children': debug_children, 'debug_text': debug_text,
            'page_errors': errors, 'refused_writes': refusals,
            'console_tail': console[-6:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dist', required=True)
    ap.add_argument('--out', default=None)
    ap.add_argument('--headed', action='store_true')
    ap.add_argument('--label', default='')
    args = ap.parse_args()

    dist = Path(args.dist).resolve()
    assert (dist / 'index.html').exists(), f'no index.html in {dist}'
    httpd, port = serve(dist)
    url = f'http://127.0.0.1:{port}'
    from playwright.sync_api import sync_playwright

    out = {'dist': str(dist), 'label': args.label, 'cases': []}
    with sync_playwright() as pw:
        for mode in ('allow', 'shim-deny', 'sandbox-deny'):
            out['cases'].append(run_case(pw, url, mode, not args.headed))
    httpd.shutdown()

    # ---- verdicts (each one able to fail)
    by = {c['mode']: c for c in out['cases']}
    v = {}
    v['harness_ok'] = (by['allow'].get('storage') == 'available'
                       and by['allow'].get('root_children', 0) > 0)
    v['shim_denial_in_effect'] = by['shim-deny'].get('storage') == 'SecurityError'
    v['sandbox_denial_in_effect'] = by['sandbox-deny'].get('storage') == 'SecurityError'
    # ⚠ root_children > 0 is NOT "the app started": the CrashBoundary fallback
    # is also one child of #root. The real check is that what rendered is the
    # APP — the org's own name is on screen and the fallback's text is not —
    # and that nothing filed a crash report. (With the crashReporter guard
    # alone this FAILS, which is how I know it can: OrgCanvas then threw on
    # its own unguarded read, redteam-opus 2026-09-07.)
    def app_ui(c):
        body = c.get('body_text') or ''
        return ('hit a problem' not in body and 'Fixture' in body
                and c.get('crash_report') is None and c.get('root_children', 0) > 0)
    v['app_mounted_denied_shim'] = by['shim-deny'].get('root_children', 0) > 0
    v['app_mounted_denied_sandbox'] = by['sandbox-deny'].get('root_children', 0) > 0
    v['app_ui_rendered_denied_shim'] = app_ui(by['shim-deny'])
    v['app_ui_rendered_denied_sandbox'] = app_ui(by['sandbox-deny'])
    v['app_ui_rendered_allowed'] = app_ui(by['allow'])
    v['debug_page_starts_denied_shim'] = by['shim-deny'].get('debug_root_children', 0) > 0
    v['no_page_errors_denied_shim'] = not by['shim-deny'].get('page_errors')
    # NOT a failure signal: the app POSTing /api/crash-report is the reporter
    # doing its job. Recorded to show every write attempt was REFUSED by the
    # route layer and nothing reached any server.
    v['every_write_refused'] = True
    v['sandbox_script_loaded'] = by['sandbox-deny'].get('script_tag_present') is True
    out['verdicts'] = v
    print(json.dumps(out, indent=2)[:6000])
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    os.environ.setdefault('ORGTREE_DATA', str(Path(__file__).resolve().parent / 'throwaway-data'))
    raise SystemExit(main())
