"""Actual Chromium interactions against isolated real-Git API fixtures.

Build first with node tests/gitworkspace_browser_bundle.mjs. No live server:
all browser requests intercepted, API forwarded to fixture TestClient.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/tests"))
# Establishes/ASSERTS fresh ORGTREE_DATA + isolated HOME/config before imports.
from test_git_workspace import Fixture, git, gw, gitsettings, store, DATA
from orgtree import gitapi
from orgtree.ledger import USER
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

assert Path(store.DATA_ROOT).resolve() == DATA.resolve()


def run(out: Path, executable: str | None = None) -> None:
    bundle = ROOT / "frontend/node_modules/.orgtree-git-browser"
    if not (bundle / "probe.js").is_file():
        raise RuntimeError("INERT: build Git browser fixture first")
    out.mkdir(parents=True, exist_ok=True)
    f = Fixture()
    middle = f.commit(f.clone, "middle.txt", "middle\n")
    tip = f.commit(f.clone, "tip.txt", "tip\n")
    f.org.hire(USER, None, "haiku", 0, "agent-one")
    item = f.org.work_create(USER, "Browser graph ticket", "Fixture association for real graph navigation", owner="agent-one")
    store.save_org(f.org)
    gw.link_item(f.slug, f.rid, "refs/heads/main", item["slug"])
    large = Fixture()
    large.history()
    for i in range(52):
        git(large.clone, 'branch', f'inactive-{i:02}')
    git(large.clone, 'branch', '--set-upstream-to', 'origin/main', 'long')
    large_repo = gw.register(f.slug, str(large.clone))
    complex_repo = Fixture()
    original = git(complex_repo.clone, 'rev-parse', 'HEAD')
    git(complex_repo.clone, 'checkout', '-b', 'feature-a')
    complex_repo.commit(complex_repo.clone, 'feature.txt', 'feature\n')
    git(complex_repo.clone, 'checkout', 'main')
    git(complex_repo.clone, 'merge', '--no-ff', '-m', 'Fixture merge with two parents', 'feature-a')
    merged = git(complex_repo.clone, 'rev-parse', 'HEAD')
    git(complex_repo.clone, 'push', 'origin', 'main')
    complex_repo.commit(complex_repo.clone, 'local.txt', 'local\n')
    git(complex_repo.seed, 'fetch', 'origin')
    git(complex_repo.seed, 'merge', '--ff-only', 'origin/main')
    incoming = complex_repo.commit(complex_repo.seed, 'remote.txt', 'remote\n')
    git(complex_repo.seed, 'push', 'origin', 'main')
    gw.fetch(complex_repo.slug, complex_repo.rid)
    git(complex_repo.clone, 'branch', 'incoming', merged)
    git(complex_repo.clone, 'branch', '--set-upstream-to', 'origin/main', 'incoming')
    git(complex_repo.clone, 'worktree', 'add', '--detach', str(complex_repo.base / 'detached'), original)
    complex_registered = gw.register(f.slug, str(complex_repo.clone))
    gw.link_item(f.slug, complex_registered['id'], 'refs/heads/main', item['slug'])
    gw.link_item(f.slug, complex_registered['id'], 'refs/heads/feature-a', item['slug'])
    gw.link_item(f.slug, complex_registered['id'], 'refs/heads/incoming', item['slug'])
    gitsettings.change(lambda d: d['selected_by_org'].update({f.slug: f.rid}))
    app = FastAPI(); app.include_router(gitapi.router)
    client = TestClient(app)
    origin = "http://git-fixture.test"
    requests, blocked, errors = [], [], []
    config = json.dumps({"slug": f.slug, "agent": "agent-one"})
    html = f'<!doctype html><html><head><link rel="stylesheet" href="/probe.css"></head><body><div id="root"></div><script>window.gitFixture={config}</script><script type="module" src="/probe.js"></script></body></html>'
    def route(request):
        parsed = urlsplit(request.request.url)
        if f"{parsed.scheme}://{parsed.netloc}" != origin:
            blocked.append(request.request.url); request.abort(); return
        if parsed.path == "/":
            request.fulfill(status=200, content_type="text/html", body=html)
        elif parsed.path in ("/probe.js", "/probe.css"):
            request.fulfill(status=200, content_type="text/javascript" if parsed.path.endswith('.js') else "text/css", body=(bundle / parsed.path[1:]).read_bytes())
        elif parsed.path.startswith('/api/'):
            requests.append({"method": request.request.method, "path": parsed.path, "body": request.request.post_data})
            response = client.request(request.request.method, parsed.path + ('?' + parsed.query if parsed.query else ''),
                                      content=request.request.post_data or b'', headers={"content-type": "application/json"})
            request.fulfill(status=response.status_code, content_type="application/json", body=response.content)
        else:
            blocked.append(request.request.url); request.abort()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1)
        page.route('**/*', route)
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(origin)
        page.locator(f'[data-oid="{middle}"]').wait_for(timeout=30000)
        assert not errors, errors
        annotation = page.locator('[data-branch="refs/heads/main"]')
        assert annotation.locator('.git-ticket-row').inner_text() == item['slug']
        assert annotation.locator('.git-agent-row .tier').count() == 1
        assert 'clean' not in annotation.inner_text() and str(f.clone) not in annotation.inner_text()
        annotation.locator('.git-ticket-row button').click()
        annotation.locator('.git-agent-row button').click()
        assert page.evaluate('window.gitOpened') == [f'@item:{f.slug}/{item["slug"]}', f'@agent:{f.slug}/agent-one']
        node = page.locator(f'[data-oid="{middle}"]')
        node.hover()
        assert 'fixture middle.txt' in page.get_by_role('tooltip').inner_text()
        node.click()
        assert page.locator('.git-node-action button').count() == 1
        assert page.locator('.git-node-action').inner_text() == 'Push local changes'
        nrect = node.bounding_box(); arect = page.locator('.git-node-action').bounding_box()
        assert abs(arect['x'] - nrect['x']) < 50 and abs(arect['y'] - nrect['y']) < 50
        page.locator('.git-node-action button').click()
        page.wait_for_function('window.gitToasts.some(lines=>lines.includes("Pushed branch history"))', timeout=30000)
        assert git(f.remote, 'rev-parse', 'main') == tip and tip != middle
        page.locator('.git-node-action').wait_for(state='detached')
        vp = page.locator('.git-viewport')
        page.evaluate('window.originalGitViewport=document.querySelector(".git-viewport")')
        page.get_by_role('button', name='pin this to the window', exact=True).click()
        assert page.locator('.git-workspace.modalpin-win').count() == 1
        page.get_by_role('button', name='unpin this window', exact=True).click()
        assert page.evaluate('window.originalGitViewport===document.querySelector(".git-viewport")')
        page.evaluate("""() => { window.captureTrace=[]; const v=document.querySelector('.git-viewport');
          for(const type of ['gotpointercapture','lostpointercapture'])v.addEventListener(type,e=>window.captureTrace.push(type)); }""")
        rect = vp.bounding_box()
        x, y = rect['x'] + 130, rect['y'] + 210
        before = vp.evaluate('(v)=>({left:v.scrollLeft,top:v.scrollTop})')
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 65, y, steps=8)
        during = vp.evaluate('(v)=>({left:v.scrollLeft,top:v.scrollTop,drag:v.classList.contains("dragging")})')
        page.screenshot(path=str(out / 'git-drag-inspect.png'))
        assert during['drag'] and abs(during['left'] - before['left']) > 40, {"before": before, "during": during, "rect": rect, "target": page.evaluate('([x,y])=>document.elementFromPoint(x,y)?.outerHTML', [x,y]), "trace": page.evaluate('window.captureTrace')}
        assert abs(during['top'] - before['top']) < 1
        page.mouse.up()
        page.wait_for_function('(left)=>Math.abs(document.querySelector(".git-viewport").scrollLeft-left)<2', arg=before['left'])
        trace = page.evaluate('window.captureTrace')
        assert trace == ['gotpointercapture', 'lostpointercapture'], trace
        page.screenshot(path=str(out / 'git-workspace-wide.png'))
        page.set_viewport_size({"width": 650, "height": 850})
        page.wait_for_function('document.querySelector(".git-viewport").clientWidth<650')
        rect = vp.bounding_box(); x, y = rect['x'] + 100, rect['y'] + 180
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x - 60, y, steps=6)
        narrow = vp.evaluate('(v)=>v.scrollLeft')
        page.mouse.up()
        assert abs(vp.evaluate('(v)=>v.scrollLeft') - narrow) < 2
        # A real touch sequence produces pointer events on the stable viewport.
        session = page.context.new_cdp_session(page)
        session.send('Input.dispatchTouchEvent', {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        session.send('Input.dispatchTouchEvent', {"type": "touchMove", "touchPoints": [{"x": x - 40, "y": y}]})
        session.send('Input.dispatchTouchEvent', {"type": "touchCancel", "touchPoints": []})
        assert not vp.evaluate('(v)=>v.classList.contains("dragging")')
        page.screenshot(path=str(out / 'git-workspace-narrow.png'))
        # Real >3,000-commit history: old heads are represented immediately,
        # details page in at boundaries and mounted DOM remains bounded.
        page.set_viewport_size({"width": 1500, "height": 1000})
        page.get_by_role('combobox', name='Repository', exact=True).select_option(large_repo['id'])
        page.get_by_role('button', name='Branches and history', exact=True).click()
        picker = page.get_by_role('region', name='Branches and history')
        # section has an accessible label; use its explicit DOM selector on
        # browsers that don't map a labelled section to a region.
        picker = page.locator('.git-branch-picker')
        picker.get_by_label('long', exact=True).wait_for(timeout=30000)
        assert picker.locator('input[type=checkbox]').count() >= 55
        picker.get_by_label('long', exact=True).check()
        page.wait_for_function('document.querySelector(".git-canvas")?.offsetHeight>80000')
        page.get_by_role('button', name='Branches and history', exact=True).click()
        initial_requests = len([r for r in requests if r['path'].endswith('/history')])
        assert page.locator('.git-node').count() < 60
        assert page.locator('[data-branch="refs/heads/long"]').count() == 1
        vp.evaluate('(v)=>{v.scrollTop=3300}')
        page.wait_for_function('!document.querySelector(".git-history-end")?.textContent.includes("Loading") && document.querySelector(".git-history-end")?.offsetTop>6000', timeout=30000)
        anchor = page.locator('.git-node').first
        anchor_oid = anchor.get_attribute('data-oid')
        anchor_position = anchor.evaluate('(n)=>({x:n.offsetLeft,y:n.offsetTop})')
        scroll_before_page = vp.evaluate('(v)=>({x:v.scrollLeft,y:v.scrollTop})')
        # A real page request is triggered without changing the current view.
        page.locator('.git-history-end button').evaluate('(b)=>b.click()')
        page.wait_for_function('document.querySelector(".git-history-end")?.offsetTop>9000', timeout=30000)
        assert page.locator(f'[data-oid="{anchor_oid}"]').evaluate('(n)=>({x:n.offsetLeft,y:n.offsetTop})') == anchor_position
        assert vp.evaluate('(v)=>({x:v.scrollLeft,y:v.scrollTop})') == scroll_before_page
        # Jump to the old selected head: sequential lazy pages fill the gap.
        vp.evaluate('(v)=>{v.scrollTop=83800}')
        page.wait_for_function('document.querySelector(".git-history-end")?.textContent.includes("Beginning of history")', timeout=60000)
        assert page.locator('.git-node').count() < 60
        assert page.locator('[data-branch="refs/heads/main"]').count() == 1
        page_count = len([r for r in requests if r['path'].endswith('/history')]) - initial_requests
        assert page_count >= 25, page_count
        page.screenshot(path=str(out / 'git-workspace-large-history.png'))
        page.get_by_role('combobox', name='Repository', exact=True).select_option(complex_registered['id'])
        page.locator(f'[data-oid="{incoming}"]').wait_for()
        page.locator('[data-branch="refs/heads/main"] .git-branch-name').hover()
        assert 'diverged' in page.get_by_role('tooltip').inner_text()
        page.locator('[data-branch="refs/heads/incoming"] .git-branch-name').click()
        page.locator(f'[data-oid="{incoming}"]').click()
        assert page.locator('.git-node-action').inner_text() == 'Pull unsynced commits'
        page.keyboard.press('Escape')
        assert page.locator('.git-node-action').count() == 0
        assert page.locator('.git-node.ghost').count() >= 1
        assert page.locator('.git-node.unpushed').count() >= 1
        assert page.locator('.git-annotation').count() == 3
        page.screenshot(path=str(out / 'git-workspace-divergence-merge.png'))
        assert not errors, errors
        # The interception guard has an intentional forbidden-request control.
        page.evaluate('fetch("http://forbidden.invalid/probe").catch(()=>{})')
        page.wait_for_function('true')
        assert blocked == ['http://forbidden.invalid/probe'], blocked
        (out / 'git-browser.json').write_text(json.dumps({"data_root": str(DATA), "middle": middle, "pushed_tip": tip,
            "requests": requests, "captured_drag": trace, "horizontal_before": before, "horizontal_during": during,
            "large_history_pages": page_count, "stable_paged_anchor": anchor_position,
            "blocked_control": blocked, "errors": errors}, indent=2), encoding='utf-8')
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--browser', help='Explicit installed Chromium executable when the default Playwright revision is unavailable')
    args = parser.parse_args()
    run(args.out_dir, args.browser)
