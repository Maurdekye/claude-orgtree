"""Captured graph continuity. All API traffic is fulfilled locally; no storage."""
import argparse
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from gitdiscovery_scroll_probe import SNAPSHOT, BRANCH

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    bundle = ROOT / 'node_modules/.orgtree-git-browser'
    assert (bundle / 'probe.js').is_file(), 'INERT: missing browser bundle'
    args.out.mkdir(parents=True, exist_ok=True)
    snap = copy.deepcopy(SNAPSHOT)
    nodes = [{'oid': f'{i+1:040x}', 'parents': [f'{i+2:040x}'] if i < 239 else [],
              'rank': i, 'at': 100, 'subject': f'Captured commit {i}',
              'lane': {'offset': 0, 'owner': BRANCH}} for i in range(240)]
    snap.update(captured_at=1700000000, newer_available=False, total_commits=240)
    snap['branches'][0]['oid'] = nodes[0]['oid']
    snap['inventory'][0]['oid'] = nodes[0]['oid']
    snap['history'] = {'nodes': nodes[:120], 'next_cursor': 'captured-page-120', 'frontier': [], 'offset': 0}
    snap['worktrees'] = [{'id': 'wt', 'path': 'C:/fixture/checkout', 'oid': nodes[0]['oid'], 'branch': BRANCH,
                         'agents': [], 'changes': {'state': 'not_read', 'files': [], 'count': None, 'complete': False}}]
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=args.executable)
        for width, height in ((1400, 900), (650, 550)):
            page = browser.new_page(viewport={'width': width, 'height': height})
            page.set_default_timeout(5000)
            page.clock.install()
            page.add_init_script("navigator.sendBeacon=()=>false;window.WebSocket=class{constructor(){throw Error('fixture blocks WebSocket')}}")
            calls, blocked, errors, held = [], [], [], []
            snapshots = 0
            def route(r):
                nonlocal snapshots
                u = urlsplit(r.request.url); method = r.request.method
                if u.netloc != 'git-capture.test': blocked.append(r.request.url); r.abort(); return
                if method == 'GET' and u.path == '/':
                    r.fulfill(content_type='text/html', body='<link rel="stylesheet" href="/probe.css"><div id="root"></div><script>window.gitFixture={slug:"fixture",agent:"fixture"}</script><script type="module" src="/probe.js"></script>'); return
                if method == 'GET' and u.path in ('/probe.js', '/probe.css'):
                    r.fulfill(content_type='text/javascript' if u.path.endswith('js') else 'text/css', body=(bundle/u.path[1:]).read_bytes()); return
                calls.append((method, u.path, u.query))
                if method == 'POST' and u.path.endswith('/discover'):
                    value = {'candidates': [], 'scanned': 0, 'truncated': False, 'errors': []}
                elif method != 'GET': blocked.append(r.request.url); r.abort(); return
                elif u.path.endswith('/repositories'):
                    value = {'repositories': [{'id': 'repo', 'name': 'Captured fixture', 'path': 'C:/fixture', 'links': []}], 'selected': 'repo'}
                elif u.path.endswith('/snapshot'):
                    snapshots += 1
                    if snapshots > 1: held.append(r); return
                    value = snap
                elif u.path.endswith('/observation'):
                    value = {'ref_identity': 'advanced-ref', 'freshness': snap['freshness']}
                elif u.path.endswith('/history'):
                    assert u.query == 'cursor=captured-page-120', u.query
                    value = {'nodes': nodes[120:], 'next_cursor': None, 'offset': 120, 'frontier': []}
                elif u.path.endswith('/worktrees/wt/changes'):
                    value = {'state': 'dirty', 'count': 1, 'complete': True, 'read_at': 1700000300,
                             'head_oid': 'b'*40, 'branch': BRANCH, 'files': [{'path': 'after-capture.txt', 'old_path': None,
                             'xy': '??', 'conflicted': False, 'staged': None, 'unstaged': None,
                             'untracked': {'added': 1, 'removed': 0, 'reason': None}}]}
                else: blocked.append(r.request.url); r.abort(); return
                r.fulfill(json=value)
            page.route('**/*', route); page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto('http://git-capture.test'); page.locator('.git-node').first.wait_for()
            page.evaluate("fetch('/api/write-positive-control',{method:'PATCH'}).catch(()=>null)")
            assert any('write-positive-control' in x for x in blocked), 'INERT: write guard'
            assert page.locator('.git-footer').inner_text().startswith('Graph captured')
            assert not any('/changes' in c[1] for c in calls)
            name = page.locator('.git-branch-name').first
            name.evaluate('e=>e.focus({preventScroll:true})'); page.get_by_text('Changes: Not read', exact=True).wait_for()
            page.get_by_role('button', name='Read checkout changes', exact=True).click()
            page.get_by_text('after-capture.txt', exact=True).wait_for()
            assert 'bbbbbbbbbb' in page.locator('.git-hover').inner_text()
            box = page.locator('.git-hover').bounding_box()
            assert box['x'] >= 0 and box['y'] >= 0 and box['x']+box['width'] <= width and box['y']+box['height'] <= height
            page.locator('.git-viewport').evaluate('e=>{e.scrollTop=560;e.scrollLeft+=30}')
            page.wait_for_function('document.querySelector(".git-viewport").scrollTop===560')
            page.evaluate('window.retainedGraph=document.querySelector(".git-viewport")')
            before = page.locator('.git-viewport').evaluate('e=>({top:e.scrollTop,left:e.scrollLeft})')
            page.clock.run_for(5100)
            page.get_by_role('button', name='Refresh graph', exact=True).wait_for()
            assert snapshots == 1, 'Observation must not restart valid capture'
            assert page.locator('.git-node').count() > 0
            page.locator('.git-toolbar').get_by_role('button', name='Refresh', exact=True).click()
            page.get_by_role('status').filter(has_text='Reading repository').wait_for()
            assert len(held) == 1 and snapshots == 2
            assert page.evaluate('window.retainedGraph===document.querySelector(".git-viewport")')
            assert page.locator('.git-node').count() > 0
            assert page.locator('.git-viewport').evaluate('e=>({top:e.scrollTop,left:e.scrollLeft})') == before
            held.pop().fulfill(status=503, json={'detail': 'fixture refresh unavailable'})
            page.get_by_role('alert').filter(has_text='fixture refresh unavailable').wait_for()
            assert page.locator('.git-viewport').evaluate('e=>({top:e.scrollTop,left:e.scrollLeft})') == before
            assert page.evaluate('window.retainedGraph===document.querySelector(".git-viewport")')
            page.screenshot(path=str(args.out/f'captured-retained-{width}.png'))
            # Load the next page after the explicit refresh failed. Its cursor
            # still belongs to the original capture, despite newer refs.
            page.locator('.git-viewport').evaluate('e=>e.scrollTop=3200')
            page.locator(f'[data-oid="{nodes[120]["oid"]}"]').wait_for()
            assert any(c[1].endswith('/history') for c in calls)
            assert snapshots == 2 and not errors, (snapshots, errors)
            results.append({'viewport': [width,height], 'snapshot_requests': snapshots,
                            'observed_newer_without_restart': True, 'held_and_failed_refresh_retained_dom_pan': before,
                            'original_cursor_paged_after_failure': True, 'on_demand_dirty_path': 'after-capture.txt',
                            'hover_bounds': box, 'blocked': blocked, 'errors': errors})
            page.close()
        browser.close()
    (args.out/'captured-continuity.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--out', type=Path, required=True); parser.add_argument('--executable', required=True)
    run(parser.parse_args())
