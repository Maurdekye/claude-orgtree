"""Real React/Chromium branch filtering and scrolling; fixture-only requests."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright
from gitdiscovery_scroll_probe import ROOT, ORIGIN, SNAPSHOT, BRANCH


def run(args):
    bundle = ROOT/'node_modules/.orgtree-git-browser'
    assert (bundle/'probe.js').is_file(), 'INERT: missing component bundle'
    inventory = [BRANCH]+[f'refs/heads/feature-{i:03}' for i in range(150)]
    result=[]; args.out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=args.executable)
        for width,height in ((1400,900),(650,550)):
            calls=[];blocked=[];errors=[]
            page=browser.new_page(viewport={'width':width,'height':height})
            page.add_init_script("navigator.sendBeacon=()=>false;window.WebSocket=class{constructor(){throw Error('fixture blocks WebSocket')}}")
            def route(r):
                u=urlsplit(r.request.url);method=r.request.method
                if f'{u.scheme}://{u.netloc}'!=ORIGIN or method!='GET':
                    # Discovery is fulfilled, never forwarded to any backend.
                    if method=='POST' and u.path=='/api/orgs/fixture/git/discover':
                        r.fulfill(json={'candidates':[],'scanned':0,'truncated':False,'errors':[]});return
                    blocked.append(r.request.url);r.abort();return
                if u.path=='/':
                    r.fulfill(content_type='text/html',body='<link rel="stylesheet" href="/probe.css"><div id="root"></div><script>window.gitFixture={slug:"fixture",agent:"fixture"}</script><script type="module" src="/probe.js"></script>');return
                if u.path in ('/probe.js','/probe.css'):
                    r.fulfill(content_type='text/javascript' if u.path.endswith('js') else 'text/css',body=(bundle/u.path[1:]).read_bytes());return
                if u.path.endswith('/repositories'):
                    value={'repositories':[{'id':'repo','name':'Branch fixture','path':'C:/fixture','links':[]}],'selected':'repo'}
                elif u.path.endswith('/snapshot'):
                    selected=json.loads(parse_qs(u.query)['branches'][0]) if u.query else [BRANCH,inventory[1]]
                    calls.append(selected)
                    value=deepcopy(SNAPSHOT)
                    value['inventory']=[{'ref':ref,'oid':SNAPSHOT['history']['nodes'][0]['oid'],'linked':False} for ref in inventory]
                    value['branches']=[{**deepcopy(SNAPSHOT['branches'][0]),'ref':ref} for ref in selected]
                elif u.path.endswith('/observation'):
                    value={'busy':False,'ref_identity':'fixture','freshness':SNAPSHOT['freshness']}
                else:blocked.append(r.request.url);r.abort();return
                r.fulfill(json=value)
            page.route('**/*',route);page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(ORIGIN);page.locator('.git-node').first.wait_for()
            page.evaluate("fetch('/api/blocked',{method:'POST'}).catch(()=>null)")
            assert blocked,'INERT: write guard'
            toggle=page.get_by_role('button',name='Branches and history',exact=True)
            toggle.click();search=page.get_by_role('searchbox',name='Find branch')
            listing=page.locator('.git-branch-results')
            assert listing.locator('input').count()==151
            geometry=listing.evaluate('e=>({height:e.clientHeight,total:e.scrollHeight,overflow:getComputedStyle(e).overflowY})')
            assert geometry['overflow']=='auto' and geometry['total']>geometry['height']+100,geometry
            listing.hover();page.mouse.wheel(0,400)
            page.wait_for_function('document.querySelector(".git-branch-results").scrollTop>0')
            final=listing.get_by_role('checkbox',name='feature-149',exact=True)
            final.scroll_into_view_if_needed()
            assert final.evaluate('e=>{let r=e.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===e}'),'last checkbox reachable'
            graph=page.locator('.git-viewport').bounding_box()
            assert graph['height']>80,graph
            page.screenshot(path=str(args.out/f'branches-{width}.png'))
            search.fill('FeAtUrE-14')
            assert listing.locator('input').count()==10
            assert listing.get_by_role('checkbox',name='feature-000',exact=True).count()==0
            listing.get_by_role('checkbox',name='feature-149',exact=True).check()
            page.wait_for_function('document.querySelector(".git-node")!==null')
            assert inventory[1] in calls[-1] and inventory[-1] in calls[-1],calls[-1]
            search.fill('not-a-matching-branch')
            assert listing.locator('input').count()==0
            assert page.get_by_text('No branches match this search.',exact=True).is_visible()
            page.get_by_role('button',name='Clear search',exact=True).click()
            assert listing.locator('input').count()==151
            assert listing.get_by_role('checkbox',name='feature-000',exact=True).is_checked()
            assert listing.get_by_role('checkbox',name='feature-149',exact=True).is_checked()
            toggle.click();assert not listing.count();toggle.click()
            assert listing.get_by_role('checkbox',name='feature-149',exact=True).is_checked()
            page.get_by_role('button',name='Relevant branches',exact=True).click()
            page.locator('.git-node').first.wait_for()
            assert not listing.count() and calls[-1]==[BRANCH,inventory[1]]
            toggle.click()
            for i in range(1,39):
                search.fill(f'feature-{i:03}')
                listing.get_by_role('checkbox',name=f'feature-{i:03}',exact=True).click()
                page.wait_for_function('document.querySelector(".git-node")!==null')
            assert len(calls[-1])==40,calls[-1]
            search.fill('feature-149')
            listing.get_by_role('checkbox',name='feature-149',exact=True).click()
            page.wait_for_function('document.querySelector(".git-node")!==null')
            assert len(calls[-1])==40 and inventory[-1] not in calls[-1], 'Selection cap must survive filtering'
            assert not errors,errors
            result.append({'viewport':[width,height],'geometry':geometry,'graph':graph,'matching':10,'clear':151,'hidden_selection_retained':True,'selection_cap':40,'final_reachable':True,'collapse_and_relevant':True,'blocked':blocked,'errors':errors})
            page.close()
        browser.close()
    (args.out/'branches.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--executable',required=True)
    run(parser.parse_args())
