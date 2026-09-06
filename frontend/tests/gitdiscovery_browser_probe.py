"""Held discovery request proves graph startup is independent in Chromium."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/tests'))
from test_git_workspace import Fixture, gw, DATA, store
from orgtree import gitapi
from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

assert Path(store.DATA_ROOT).resolve() == DATA.resolve()


async def run(out, executable):
    f = Fixture()
    app = FastAPI(); app.include_router(gitapi.router)
    client = TestClient(app)
    bundle = ROOT / 'frontend/node_modules/.orgtree-git-browser'
    assert (bundle / 'probe.js').is_file(), 'INERT: browser bundle missing'
    origin = 'http://git-fixture.test'
    config = json.dumps({'slug': f.slug, 'agent': 'fixture'})
    html = f'<html><head><link rel="stylesheet" href="/probe.css"></head><body><div id="root"></div><script>window.gitFixture={config}</script><script type="module" src="/probe.js"></script></body></html>'
    evidence = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=executable)
        for failure in (False, True):
            page = await browser.new_page(viewport={'width': 1400, 'height': 900})
            pending, release = asyncio.Event(), asyncio.Event()
            errors, requests = [], []
            async def route(r):
                parsed = urlsplit(r.request.url)
                assert f'{parsed.scheme}://{parsed.netloc}' == origin, 'Unexpected external request'
                if parsed.path == '/':
                    await r.fulfill(status=200, content_type='text/html', body=html)
                elif parsed.path in ('/probe.js', '/probe.css'):
                    await r.fulfill(status=200, content_type='text/javascript' if parsed.path.endswith('.js') else 'text/css', body=(bundle / parsed.path[1:]).read_bytes())
                elif parsed.path.startswith('/api/'):
                    requests.append(parsed.path)
                    if parsed.path.endswith('/discover'):
                        pending.set()
                        await release.wait()
                        if failure:
                            await r.fulfill(status=503, content_type='application/json', body='{"detail":"Fixture discovery unavailable"}')
                            return
                    response = await asyncio.to_thread(client.request, r.request.method, parsed.path,
                                                       content=r.request.post_data or b'', headers={'content-type': 'application/json'})
                    await r.fulfill(status=response.status_code, content_type='application/json', body=response.content)
                else:
                    await r.abort()
            await page.route('**/*', route)
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto(origin)
            await asyncio.wait_for(pending.wait(), 10)
            await page.locator('.git-node').first.wait_for(timeout=15000)
            assert not release.is_set(), 'INERT: discovery completed before graph assertion'
            assert await page.get_by_role('button', name='Add repository', exact=True).is_enabled()
            assert await page.get_by_role('button', name='Finding repositories…', exact=True).is_visible()
            await page.get_by_role('button', name='Repository settings', exact=True).click()
            await page.locator('.git-settings select').first.wait_for()
            await page.get_by_role('button', name='Close repository settings').click()
            await page.get_by_role('button', name='Finding repositories…', exact=True).click()
            assert await page.get_by_role('status').filter(has_text='Finding repositories in').is_visible()
            assert await page.get_by_text('No repositories found in the selected roots.', exact=True).count() == 0
            release.set()
            if failure:
                await page.get_by_role('alert').filter(has_text='Discovery failed:').wait_for()
            else:
                await page.get_by_role('button', name=f'clone · {gw.canonical(str(f.clone))}', exact=True).wait_for(timeout=20000)
            assert await page.locator('.git-node').count() > 0
            assert requests.count(f'/api/orgs/{f.slug}/git/discover') == 1
            assert not errors, errors
            evidence.append({'discovery_failure': failure, 'graph_ready_while_discovery_held': True,
                             'add_and_settings_usable': True, 'requests': requests})
            await page.screenshot(path=str(out / ('discovery-failure.png' if failure else 'discovery-complete.png')))
            await page.close()
        await browser.close()
    (out / 'git-discovery-browser.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--executable', required=True)
    args = parser.parse_args(); args.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(args.out, args.executable))
