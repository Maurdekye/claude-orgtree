"""Long-list Chromium control: synthetic API only; no backend/storage imports."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://git-scroll-fixture.test'
OID = 'a' * 40
BRANCH = 'refs/heads/main'
SNAPSHOT = {
    'token': 'fixture', 'slug': 'fixture', 'repository_id': 'repo', 'created': 0,
    'name': 'Registered fixture', 'root': 'C:/fixture/registered', 'bare': False,
    'branches': [{'ref': BRANCH, 'oid': OID, 'local': True, 'upstream': '', 'remote': '', 'remote_ref': '',
                  'upstream_oid': None, 'tickets': [], 'sync': {'state': 'no_upstream', 'ahead': None, 'behind': None},
                  'against_trunk': {'state': 'same', 'ahead': 0, 'behind': 0},
                  'unique': {'local': [], 'remote': []}, 'classified': False}],
    'worktrees': [], 'shallow': False, 'history': {'nodes': [{'oid': OID, 'parents': [], 'at': 100,
        'subject': 'Fixture commit', 'rank': 0, 'lane': {'offset': 0, 'owner': BRANCH}}],
        'next_cursor': None, 'frontier': [], 'offset': 0},
    'inventory': [{'ref': BRANCH, 'oid': OID, 'linked': False}],
    'config': {'trunk': BRANCH, 'remote': None, 'remotes': [], 'trunk_missing': False, 'remote_missing': False},
    'omitted_active': 0, 'omitted_worktrees': 0,
    'freshness': {'state': 'not_watched', 'age_seconds': None, 'watched': False, 'busy': False},
    'ref_identity': 'fixture', 'unborn_branch': None, 'total_commits': 1,
}
CANDIDATES = [{'name': f'repository-{i:03}', 'path': 'C:/fixture/' + ('long-folder-segment/' * 8 if i == 119 else '') + f'repository-{i:03}'} for i in range(120)]


def run(args):
    bundle = ROOT / 'node_modules/.orgtree-git-browser'
    assert (bundle / 'probe.js').is_file(), 'INERT: build gitworkspace_browser_bundle.mjs first'
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=args.executable)
        for width, height in [(1400, 900), (650, 550)]:
            for registered in (True, False):
                blocked, calls, errors = [], [], []
                page = browser.new_page(viewport={'width': width, 'height': height})
                page.add_init_script("navigator.sendBeacon=()=>false;window.WebSocket=class{constructor(){throw Error('fixture blocks WebSocket')}}")
                html = '<html><head><link rel="stylesheet" href="/probe.css"></head><body><div id="root"></div><script>window.gitFixture={slug:"fixture",agent:"fixture"}</script><script type="module" src="/probe.js"></script></body></html>'
                def route(r):
                    u = urlsplit(r.request.url)
                    if f'{u.scheme}://{u.netloc}' != ORIGIN:
                        blocked.append(r.request.url); r.abort(); return
                    if u.path == '/' and r.request.method == 'GET':
                        r.fulfill(content_type='text/html', body=html); return
                    if u.path in ('/probe.js', '/probe.css') and r.request.method == 'GET':
                        r.fulfill(content_type='text/javascript' if u.path.endswith('.js') else 'text/css', body=(bundle/u.path[1:]).read_bytes()); return
                    call = (r.request.method, u.path)
                    calls.append({'method': call[0], 'path': call[1], 'body': r.request.post_data})
                    prefix = '/api/orgs/fixture/git'
                    if call == ('GET', prefix+'/repositories'):
                        value = {'repositories': [{'id': 'repo', 'name': 'Registered fixture', 'path': 'C:/fixture/registered', 'links': []}] if registered else [], 'selected': 'repo' if registered else None}
                    elif call == ('POST', prefix+'/discover'):
                        value = {'candidates': CANDIDATES, 'scanned': 200, 'truncated': False, 'errors': []}
                    elif call == ('POST', prefix+'/repositories'):
                        assert json.loads(r.request.post_data)['path'] == CANDIDATES[-1]['path']
                        value = {'id': 'repo', 'name': 'Registered fixture'}
                    elif call == ('GET', prefix+'/repo/snapshot'):
                        value = SNAPSHOT
                    elif call == ('GET', prefix+'/repo/observation'):
                        value = {'busy': False, 'ref_identity': 'fixture', 'freshness': SNAPSHOT['freshness']}
                    else:
                        blocked.append(r.request.url); r.abort(); return
                    r.fulfill(content_type='application/json', body=json.dumps(value))
                page.route('**/*', route)
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(ORIGIN)
                page.evaluate("fetch('/api/write-positive-control',{method:'POST'}).catch(()=>null)")
                assert any('write-positive-control' in url for url in blocked), 'INERT: unexpected-write guard'
                if registered:
                    page.locator('.git-node').wait_for()
                    page.get_by_role('button', name='Discovery results', exact=True).click()
                last = page.get_by_role('button', name='repository-119 · '+CANDIDATES[-1]['path'], exact=True)
                last.wait_for(state='attached')
                panel = page.locator('.git-workspace')
                listing = page.locator('.git-empty')
                before = listing.evaluate('e=>({height:e.clientHeight,scrollHeight:e.scrollHeight,top:e.scrollTop,overflow:getComputedStyle(e).overflowY})')
                result = {'window': [width,height], 'registered': registered, 'before': before}
                if args.baseline:
                    assert before['overflow'] == 'visible' and before['scrollHeight'] >= before['height']
                    result['last_outside_panel'] = last.bounding_box()['y'] > panel.bounding_box()['y']+panel.bounding_box()['height']
                    assert result['last_outside_panel'], 'INERT: baseline must reproduce unreachable final entry'
                    page.screenshot(path=str(args.out/'before.png'))
                    results.append(result); page.close(); break
                assert before['overflow'] == 'auto' and before['scrollHeight'] > before['height']+1000, before
                listing.hover(); page.mouse.wheel(0,500)
                page.wait_for_function('document.querySelector(".git-empty").scrollTop>0')
                wheel_top = listing.evaluate('e=>e.scrollTop')
                last.scroll_into_view_if_needed()
                bounds = last.bounding_box(); area = listing.bounding_box(); outer = panel.bounding_box()
                assert bounds['y'] >= area['y'] and bounds['y']+bounds['height'] <= area['y']+area['height']+1
                assert area['y'] >= outer['y'] and area['y']+area['height'] <= outer['y']+outer['height']
                assert last.evaluate('e=>{const r=e.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest("button")===e}'), 'Final entry must be hit-testable'
                assert listing.evaluate('e=>e.scrollWidth<=e.clientWidth'), 'Long path must wrap inside list'
                for label in ('Add repository', 'Scan subfolders', 'Discovery results'):
                    control=page.get_by_role('button',name=label,exact=True)
                    assert control.is_enabled()
                    assert control.evaluate('e=>{const r=e.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest("button")===e}'), label
                if registered:
                    assert page.locator('.git-viewport').bounding_box()['height'] > 30
                    footer=page.locator('.git-footer').bounding_box()
                    assert footer['y']+footer['height']<=outer['y']+outer['height']+1
                    assert page.get_by_role('button',name='Refresh',exact=True).is_enabled()
                result.update(wheel_scroll_top=wheel_top, final_scroll_top=listing.evaluate('e=>e.scrollTop'), last_bounds=bounds, list_bounds=area, panel_bounds=outer)
                page.screenshot(path=str(args.out/f'after-{width}-{registered}.png'))
                last.click()
                assert any(c['method']=='POST' and c['path'].endswith('/repositories') for c in calls), 'Final entry click must run its actual registration handler locally'
                assert not errors, errors
                result.update(final_entry_click=True, page_errors=errors, blocked=blocked)
                results.append(result); page.close()
            if args.baseline: break
        browser.close()
    (args.out/('baseline.json' if args.baseline else 'scroll-results.json')).write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--executable',required=True)
    parser.add_argument('--baseline',action='store_true')
    run(parser.parse_args())
